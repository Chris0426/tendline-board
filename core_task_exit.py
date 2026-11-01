"""core_task_exit - scheduled-task exit-code status file (WP-X1b, 2026-10-01).

Why this exists
---------------
Every Board scheduled task runs inside ``conhost.exe --headless`` (windowless rule). Measured
2026-10-01 (WP-X1): conhost does NOT pass the child's exit code back, so schtasks
``Last Result`` is always 0 for a round that finished -- a round that died on
``EXIT_MES`` (8) looks exactly like a clean one. The wrapper itself must stay (no
windows), so the python entry records its own exit code instead:

    _state/task_exit/<task>.json  =  {task, exit_code, exit_name,
                                      started_at, finished_at, host_pid}

written atomically through ``board_atomic`` at the end of EVERY round, and the
installer's ``-Status`` prints it (exit code, name, finish time, age, STALE when older
than two schedule periods).

Mechanism: try/finally (not atexit)
-----------------------------------
``run_recorded(task, fn)`` calls ``fn`` inside try/except/finally. atexit was rejected:
an atexit hook never sees the exit code (``sys.exit(8)`` is gone by the time it runs).
The finally block covers: a normal return (``return rc``), ``sys.exit(n)`` /
``raise SystemExit(n)``, and any uncaught exception (recorded as exit 1, which is what
the interpreter then exits with). The original exception / SystemExit is re-raised
unchanged, so the process exit code is EXACTLY what it was without this module.

Hard rules
----------
* Writing the file must never change the exit code and must never raise
  (``write_status`` swallows every ``Exception`` and returns False).
* No status file yet (first round after deploy) is a normal state, not an error.
* Diagnostic invocations do not write: ``--selftest``, ``--dry-run``, ``--status``,
  ``--verify``, ``-h``/``--help`` (``SKIP_FLAGS``). The scheduled actions pass no flags.
* A round KILLED from outside (ExecutionTimeLimit, ``schtasks /End``) is
  TerminateProcess: no finally runs, the previous record stays and ages -- that is
  exactly what the ``-Status`` STALE line (> 2 periods) is for.
* Zero repo dependencies beyond ``core_paths`` and ``board_atomic`` (both zero-dep).

Not applied to: service-type tasks (``TendlineBoardDashboard`` = uvicorn :8000,
``TendlineBoardSaltmarshTunnel`` = cloudflared). They are long-running daemons: a "round" never
finishes, so there is no exit code to record; their health check is ``/health`` and the
process list, which their ``-Status`` already shows.

Run ``python core_task_exit.py --selftest`` (offline; writes only to a temp dir).
"""
import os
import re
import sys
from datetime import datetime

import core_paths as _wp
import board_atomic as _wat

REPO = os.path.dirname(os.path.abspath(__file__))
SUBDIR = "task_exit"
SKIP_FLAGS = ("--selftest", "--dry-run", "--status", "--verify", "-h", "--help")
_TASK_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# Uncaught KeyboardInterrupt: Windows reports STATUS_CONTROL_C_EXIT (0xC000013A, shown
# signed by PowerShell/schtasks); POSIX shells report 130. Measured 2026-10-01 (py 3.14).
EXIT_INTERRUPTED = -1073741510 if os.name == "nt" else 130


def status_path(task, repo=None):
    """``<repo>/_state/task_exit/<task>.json`` (``BOARD_STATE_DIR`` honoured via core_paths)."""
    if not _TASK_RE.match(str(task or "")):
        raise ValueError(f"bad task name: {task!r}")
    return _wp.state_path(SUBDIR, f"{task}.json", repo=REPO if repo is None else repo)


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def should_record(argv):
    """False for diagnostic invocations (any SKIP_FLAGS flag, also ``--flag=value``)."""
    for a in argv or ():
        a = str(a)
        if a in SKIP_FLAGS or a.split("=", 1)[0] in SKIP_FLAGS:
            return False
    return True


def exit_code_of(exc):
    """The process exit code the interpreter will produce for ``exc`` escaping main."""
    if exc is None:
        return 0
    if isinstance(exc, SystemExit):
        c = exc.code
        if c is None:
            return 0
        if isinstance(c, int):
            return int(c)
        return 1                      # sys.exit("message") prints it and exits 1
    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    return 1                          # uncaught exception -> traceback, exit 1


def exit_name_of(code, exc, names=None):
    if exc is not None and not isinstance(exc, SystemExit):
        if isinstance(exc, KeyboardInterrupt):
            return "INTERRUPTED"
        return "UNCAUGHT_EXCEPTION:" + type(exc).__name__
    if isinstance(exc, SystemExit) and exc.code is not None and not isinstance(exc.code, int):
        return "SYSTEM_EXIT_MESSAGE"
    n = (names or {}).get(code)
    return str(n) if n else f"EXIT_{code}"


def write_status(task, exit_code, exit_name, started_at, finished_at=None, repo=None,
                 _writer=None):
    """Atomically write the status file. NEVER raises; returns True on success."""
    try:
        doc = {"task": str(task), "exit_code": int(exit_code), "exit_name": str(exit_name),
               "started_at": str(started_at),
               "finished_at": str(finished_at or _now_iso()),
               "host_pid": os.getpid()}
        (_writer or _wat.atomic_write_json)(status_path(task, repo), doc, indent=1)
        return True
    except Exception as e:                                  # noqa: BLE001
        try:
            sys.stderr.write(f"[task_exit] status file not written ({type(e).__name__}: {e})\n")
        except Exception:                                   # noqa: BLE001
            pass
        return False


def run_recorded(task, fn, *, names=None, repo=None, argv=None, _writer=None):
    """Call ``fn()``; record its exit code in the finally block; return / re-raise unchanged.

    ``fn`` may return an int (or None == 0), call ``sys.exit``, or raise. Whatever
    happens, the caller sees exactly what it would have seen without this wrapper.
    """
    record = should_record(sys.argv[1:] if argv is None else argv)
    started = _now_iso()
    exc = None
    rc = None
    try:
        rc = fn()
        return rc
    except BaseException as e:                              # noqa: BLE001
        exc = e
        raise
    finally:
        if record:
            try:
                if exc is None:
                    code = 0 if rc is None else (int(rc) if isinstance(rc, int) else 1)
                else:
                    code = exit_code_of(exc)
                name = exit_name_of(code, exc, names)
            except Exception:                               # noqa: BLE001
                code, name = -1, "UNKNOWN"
            write_status(task, code, name, started, repo=repo, _writer=_writer)


def read_status(task, repo=None):
    """(doc, None) or (None, reason). Missing file -> (None, "no record yet")."""
    try:
        p = status_path(task, repo)
    except ValueError as e:
        return None, str(e)
    if not os.path.exists(p):
        return None, "no record yet"
    try:
        import json
        with open(p, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:                                  # noqa: BLE001
        return None, f"unreadable: {type(e).__name__}"


# =============================================================================
def _selftest():
    import shutil
    import subprocess
    import tempfile
    ok = bad = 0

    def ck(cond, msg):
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            print("FAIL:", msg)

    names = {0: "EXIT_OK", 1: "EXIT_UNEXPECTED", 8: "EXIT_MES"}
    d = tempfile.mkdtemp(prefix="coretaskexit-")
    keys = {"task", "exit_code", "exit_name", "started_at", "finished_at", "host_pid"}
    try:
        # path / naming
        p = status_path("TendlineBoardX", repo=d)
        ck(os.path.normcase(p) == os.path.normcase(os.path.join(d, _wp.STATE_DIRNAME, SUBDIR, "TendlineBoardX.json")),
           "1.1 path = <repo>/_state/task_exit/<task>.json")
        for badname in ("../x", "a/b", "", "a b", None):
            try:
                status_path(badname, repo=d)
                ck(False, f"1.2 bad task name rejected: {badname!r}")
            except ValueError:
                ck(True, "1.2")
        ck(read_status("TendlineBoardX", repo=d) == (None, "no record yet"),
           "1.3 first round: no file is a normal state (no error)")
        ck(should_record([]) and should_record(["--lookback-days", "30"])
           and not should_record(["--selftest"]) and not should_record(["--dry-run"])
           and not should_record(["--status"]) and not should_record(["--verify=2026"]),
           "1.4 diagnostic flags skip, scheduled (no-arg) run records")

        # 2. success path (returns 0)
        r = run_recorded("TendlineBoardX", lambda: 0, names=names, repo=d, argv=[])
        doc, why = read_status("TendlineBoardX", repo=d)
        ck(r == 0 and why is None and doc["exit_code"] == 0 and doc["exit_name"] == "EXIT_OK",
           "2.1 success: returns 0, records 0/EXIT_OK")
        ck(set(doc) == keys and doc["task"] == "TendlineBoardX" and doc["host_pid"] == os.getpid(),
           "2.2 exactly the six spec keys")
        ck(doc["started_at"] <= doc["finished_at"] and "T" in doc["finished_at"]
           and (doc["finished_at"][-6] in "+-"), "2.3 ISO timestamps with UTC offset")
        ck(sorted(os.listdir(os.path.dirname(p))) == ["TendlineBoardX.json"], "2.4 no temp leftover")

        # 3. EXIT_MES via SystemExit(8)
        def _mes():
            raise SystemExit(8)
        try:
            run_recorded("TendlineBoardX", _mes, names=names, repo=d, argv=[])
            ck(False, "3.1 SystemExit must propagate")
        except SystemExit as e:
            ck(e.code == 8, "3.1 SystemExit(8) re-raised unchanged")
        doc, _ = read_status("TendlineBoardX", repo=d)
        ck(doc["exit_code"] == 8 and doc["exit_name"] == "EXIT_MES", "3.2 records 8/EXIT_MES")

        # 4. uncaught exception
        class _Boom(RuntimeError):
            pass

        def _boom():
            raise _Boom("x")
        try:
            run_recorded("TendlineBoardX", _boom, names=names, repo=d, argv=[])
            ck(False, "4.1 exception must propagate")
        except _Boom:
            ck(True, "4.1 original exception re-raised")
        doc, _ = read_status("TendlineBoardX", repo=d)
        ck(doc["exit_code"] == 1 and doc["exit_name"] == "UNCAUGHT_EXCEPTION:_Boom",
           "4.2 uncaught -> exit 1, UNCAUGHT_EXCEPTION:<type>")

        # 5. write failure never changes the exit code and never raises
        def _bad_writer(*_a, **_k):
            raise PermissionError(13, "denied")
        try:
            run_recorded("TendlineBoardX", _mes, names=names, repo=d, argv=[], _writer=_bad_writer)
            ck(False, "5.1")
        except SystemExit as e:
            ck(e.code == 8, "5.1 write failure: SystemExit(8) still 8")
        ck(run_recorded("TendlineBoardX", lambda: 3, names=names, repo=d, argv=[],
                        _writer=_bad_writer) == 3, "5.2 write failure: return value unchanged")
        doc, _ = read_status("TendlineBoardX", repo=d)
        ck(doc["exit_name"] == "UNCAUGHT_EXCEPTION:_Boom", "5.3 failed write left previous record intact")
        ck(write_status("../bad", 0, "x", "s", repo=d) is False, "5.4 write_status never raises")

        # 6. misc codes
        ck(exit_code_of(SystemExit(None)) == 0 and exit_code_of(SystemExit("msg")) == 1
           and exit_code_of(KeyboardInterrupt()) == EXIT_INTERRUPTED, "6.1 exit_code_of")
        ck(exit_name_of(5, None, names) == "EXIT_5" and exit_name_of(1, SystemExit("m")) ==
           "SYSTEM_EXIT_MESSAGE", "6.2 exit_name_of fallbacks")
        run_recorded("TendlineBoardX", lambda: 0, names=names, repo=d, argv=["--dry-run"])
        doc, _ = read_status("TendlineBoardX", repo=d)
        ck(doc["exit_name"] == "UNCAUGHT_EXCEPTION:_Boom", "6.3 --dry-run run does not write")

        # 7. real process: actual exit codes equal recorded ones (try/finally under sys.exit)
        here = os.path.dirname(os.path.abspath(__file__))
        for tag, body, want_rc, want_name in (
                ("ok", "return 0", 0, "EXIT_OK"),
                ("mes", "sys.exit(8)", 8, "EXIT_MES"),
                ("boom", "raise ValueError('boom')", 1, "UNCAUGHT_EXCEPTION:ValueError")):
            code = ("import sys; sys.path.insert(0, %r)\n"
                    "import core_task_exit as w\n"
                    "def main():\n    %s\n"
                    "sys.exit(w.run_recorded('TendlineBoardProc', main, names=%r, repo=%r))\n"
                    % (here, body, names, d))
            pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                env={**os.environ, "BOARD_STATE_DIR": ""})
            doc, why = read_status("TendlineBoardProc", repo=d)
            ck(pr.returncode == want_rc and doc and doc["exit_code"] == want_rc
               and doc["exit_name"] == want_name and doc["host_pid"] != os.getpid(),
               f"7.{tag} subprocess rc={pr.returncode} recorded={doc} why={why}")
        # 7.w unwritable status dir in a real process: exit code still 8
        blk = os.path.join(d, "blocked")
        os.makedirs(blk)
        with open(os.path.join(blk, _wp.STATE_DIRNAME), "w") as f:      # a FILE where the dir must go
            f.write("x")
        code = ("import sys; sys.path.insert(0, %r)\nimport core_task_exit as w\n"
                "def main():\n    sys.exit(8)\n"
                "w.run_recorded('TendlineBoardProc', main, repo=%r)\n" % (here, blk))
        pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "BOARD_STATE_DIR": ""})
        ck(pr.returncode == 8 and "status file not written" in pr.stderr,
           f"7.w write failure in a real process: rc={pr.returncode} stderr={pr.stderr[-200:]!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"core_task_exit selftest: {ok} passed, {bad} failed")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
