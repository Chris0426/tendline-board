"""board_atomic - shared atomic file write helper (zero external dependencies).

Semantics (see _ATOMIC-WRITE-SURVEY-20260930.md):
  * temp file in the SAME directory, unique per call: ``.<basename>.<pid>-<tid>-<seq>.tmp``
    (two concurrent writers never share a temp file);
  * flush + os.fsync, then os.replace (readers never see a half-written file);
  * PermissionError on replace is retried briefly (3 attempts, <= ~50ms total);
  * any exception unlinks OUR temp file and then re-raises;
  * missing parent directory is created;
  * NO cross-process lock: who wins between writers stays with the callers' own gates.

Run ``python board_atomic.py --selftest`` for the self-test.
"""
import itertools
import json
import os
import sys
import threading
import time

_SEQ = itertools.count(1)


def _tmp_name(path):
    d, base = os.path.split(os.path.abspath(path))
    return os.path.join(d, f".{base}.{os.getpid()}-{threading.get_ident()}-{next(_SEQ)}.tmp")


def _replace_retry(tmp, path, retries):
    n = max(1, int(retries))
    for attempt in range(n):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == n - 1:
                raise
            time.sleep(0.01 + 0.015 * attempt)


def atomic_write_text(path, text, *, encoding="utf-8", replace_retries=3):
    """Write ``text`` to ``path`` atomically. Raises on failure (own temp file removed)."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = _tmp_name(path)
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _replace_retry(tmp, path, replace_retries)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, doc, *, ensure_ascii=False, indent=1, sort_keys=False, separators=None):
    """Serialise first (a dumps error leaves no temp file), then atomic_write_text."""
    text = json.dumps(doc, ensure_ascii=ensure_ascii, indent=indent,
                      sort_keys=sort_keys, separators=separators)
    atomic_write_text(path, text)


def _selftest():
    import tempfile
    import shutil
    from unittest import mock
    ok = bad = 0

    def ck(cond, msg):
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            print("FAIL:", msg)

    d = tempfile.mkdtemp(prefix="wratomic-")
    try:
        names = {_tmp_name(os.path.join(d, "x.json")) for _ in range(200)}
        ck(len(names) == 200, "tmp names unique")
        n0 = next(iter(names))
        ck(os.path.dirname(n0) == os.path.abspath(d) and os.path.basename(n0).startswith(".x.json.")
           and n0.endswith(".tmp"), "tmp name same dir, .<base>.<pid>-<tid>-<seq>.tmp")
        p = os.path.join(d, "sub", "a.json")
        sub = os.path.dirname(p)
        atomic_write_json(p, {"k": "中文", "a": [1, 2]})
        with open(p, encoding="utf-8") as f:
            raw = f.read()
        ck(json.loads(raw) == {"k": "中文", "a": [1, 2]} and "中文" in raw, "json roundtrip, ensure_ascii False")
        ck("\n " in raw, "default indent=1")
        atomic_write_json(p, {"b": 1, "a": 2}, ensure_ascii=True, indent=None, sort_keys=True,
                          separators=(",", ":"))
        with open(p, encoding="utf-8") as f:
            ck(f.read() == '{"a":2,"b":1}', "json params passthrough")
        ck(os.listdir(sub) == ["a.json"], "no leftover after success")
        try:
            atomic_write_json(p, {"x": object()})
            ck(False, "unserialisable should raise")
        except TypeError:
            ck(True, "raises")
        ck(os.listdir(sub) == ["a.json"], "no leftover after dumps error")
        with open(p, encoding="utf-8") as f:
            ck(f.read() == '{"a":2,"b":1}', "target untouched after failure")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            try:
                atomic_write_text(p, "zzz")
                ck(False, "should raise")
            except OSError:
                ck(True, "OSError propagates")
        ck(os.listdir(sub) == ["a.json"], "no leftover after replace error")
        real = os.replace
        calls = {"n": 0}

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError(13, "busy")
            return real(a, b)
        with mock.patch("os.replace", side_effect=flaky):
            atomic_write_text(p, "ok-retry")
        with open(p, encoding="utf-8") as f:
            ck(calls["n"] == 3 and f.read() == "ok-retry", "replace retried twice then ok")
        with mock.patch("os.replace", side_effect=PermissionError(13, "busy")) as m:
            try:
                atomic_write_text(p, "never", replace_retries=3)
                ck(False, "should raise")
            except PermissionError:
                ck(m.call_count == 3, "gives up after 3 attempts")
        ck(os.listdir(sub) == ["a.json"], "no leftover after retry exhaustion")
        errs = []
        big = {"v": "x" * 200000}

        def worker(tag):
            try:
                for i in range(30):
                    atomic_write_json(p, {"tag": tag, "i": i, **big})
            except Exception as e:  # noqa: BLE001
                errs.append(repr(e))
        ts = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        with open(p, encoding="utf-8") as f:
            final = json.load(f)
        ck(final["tag"] in ("A", "B") and final["i"] == 29 and len(final["v"]) == 200000,
           "final file complete after concurrent writers")
        ck(not errs, f"no writer errors: {errs[:1]}")
        ck(os.listdir(sub) == ["a.json"], "no leftover after concurrent writes")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"board_atomic selftest: {ok} passed, {bad} failed")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
