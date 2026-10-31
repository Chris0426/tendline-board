# -*- coding: utf-8 -*-
"""
wsynth_emit.py — 合成世界的輸出器（SPEC-SYNTHLINE WP-1；C-SY12）
=================================================================

把 `wsynth_world.build_world()` 的結果寫成兩份帳＋各自的 manifest：

  <out>/seed/seed-YYYY-MM-DD.jsonl      tendline.seed.v1（Board＋cmms 共用；台北日界 00:00）
  <out>/seed/seed-manifest.json         tendline.seed.manifest.v1
  <out>/mes/mes-segments-YYYY-MM-DD.jsonl   tendline.mes_segments.v1（Board 私用；按段 start 的台北日）
  <out>/mes/mes-manifest.json           tendline.mes_segments.manifest.v1

鐵則
  · **帳不進版控**（C-SY12）：輸出目錄由參數指定，預設＝系統暫存目錄下 `tendline-synth/<world_id>`；
    指向本 repo 內任何位置一律拒絕（`EmitRefused`）。
  · 位元穩定：JSON 以 sort_keys／緊湊分隔／UTF-8 序列化，行尾一律 `\\n`（不隨平台換 CRLF）。
  · 前綴穩定：同一世界延長 `until` 後，每個舊檔的舊內容是新檔內容的前綴（事件依 seq、段依
    (end, stp_seq, eid, unit) 排序；新曝光者的時刻恆晚於舊 until）。
  · 寫出前先過驗證器：先寫同層暫存目錄、用 `wsynth_seed` 驗過才換進輸出目錄；失敗不留半成品。

CLI：`python wsynth_emit.py [--out DIR] [--seed S] [--until T] [--seed-only] [--json] [--selftest]`
（T＝台北 naive ISO 或 UTC Z；省略＝`wsynth_clock.now()`；啟動先做 UTC+8 檢查。
退出碼 0 成功／2 參數或環境拒絕／3 產生或驗證失敗；SPEC-SYNTHLINE §5.6。）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import sys
import tempfile

import wsynth_params as P

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

EXIT_OK = 0
EXIT_REFUSED = 2      # 參數或環境拒絕：參數錯、--until 解析失敗、輸出目錄在 repo 內、主機時區不符
EXIT_FAILED = 3       # 產生或驗證失敗：寫出的種子過不了 wsynth_seed 驗證器、或產生器內部錯誤
SUMMARY_SCHEMA = "tendline.emit.summary.v1"


class EmitRefused(RuntimeError):
    pass


class EmitInvalid(RuntimeError):
    """寫出的種子過不了 `wsynth_seed` 驗證器（CLI 退出碼 3）。"""


def _line(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def default_out_dir(world_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), "tendline-synth", world_id)


def _inside(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.realpath(path))
    r = os.path.normcase(os.path.realpath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def render(world) -> dict:
    """純函式：世界 → {相對路徑: bytes}。emit 與 selftest 共用（雜湊比對用）。"""
    from wsynth_clock import from_utc_z
    files = {}
    seed_days = {}
    for e in world["events"]:
        day = from_utc_z(e["at"]).date().isoformat()
        seed_days.setdefault(day, []).append(_line(e))
    mes_days = {}
    for s in world["segments"]:
        day = s["start"][:10]
        mes_days.setdefault(day, []).append(_line(s))
    seed_index = []
    for day in sorted(seed_days):
        name = "seed-%s.jsonl" % day
        data = "".join(seed_days[day]).encode("utf-8")
        files["seed/" + name] = data
        seed_index.append({"day": day, "file": name, "n_events": len(seed_days[day]),
                           "sha256": hashlib.sha256(data).hexdigest()})
    mes_index = []
    for day in sorted(mes_days):
        name = "mes-segments-%s.jsonl" % day
        data = "".join(mes_days[day]).encode("utf-8")
        files["mes/" + name] = data
        mes_index.append({"day": day, "file": name, "n_segments": len(mes_days[day]),
                          "sha256": hashlib.sha256(data).hexdigest()})
    common = {"world_id": world["world_id"], "generator_version": world["generator_version"],
              "fleet_digest": world["fleet_digest"], "horizon_start": world["horizon_start"],
              "exposed_until": world["exposed_until"], "flags": world["flags"]}
    # SPEC §5.1／§5.7（WP-2 修正輪 F6）：非預設旗標的世界只供測試 ⇒ manifest 帶明確測試標記，
    # 消費方預設拒讀。預設世界不帶此鍵（位元組不變）。
    if dict(world["flags"]) != P.DEFAULT_FLAGS:
        common["test_only"] = True
    files["seed/seed-manifest.json"] = json.dumps(
        dict(common, schema=P.SEED_MANIFEST_SCHEMA, days=seed_index),
        sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
    files["mes/mes-manifest.json"] = json.dumps(
        dict(common, schema=P.MES_MANIFEST_SCHEMA, days=mes_index),
        sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
    return files


def validate_seed_dir(seed_dir: str) -> list:
    """用 `wsynth_seed` 驗證器驗一個 seed/ 目錄（manifest、日檔 sha256／行數、整份帳跨行不變式）。
    回傳錯誤字串清單；空＝通過。永不拋例外。"""
    import wsynth_seed as S
    errs = []
    try:
        with open(os.path.join(seed_dir, "seed-manifest.json"), "rb") as fh:
            man, m_errs = S.parse_manifest_bytes(fh.read())
        errs += ["%s: %s" % e for e in m_errs]
        if man is None:
            return errs or ["manifest unreadable"]
        names = sorted(n for n in os.listdir(seed_dir) if n.startswith("seed-") and n.endswith(".jsonl"))
        raws = {}
        for n in names:
            with open(os.path.join(seed_dir, n), "rb") as fh:
                raws[n] = fh.read()
        errs += ["%s: %s" % e for e in S.check_day_files(man, raws)]
        evs = []
        for n in names:
            evs.extend(S.split_day_bytes(raws[n]))
        rep = S.validate_ledger(evs, manifest=man, complete=True, strict=True, allow_test_world=True)
        if rep["rejected"] or rep["errors"]:
            errs += ["%s: %s" % (e["code"], e["detail"]) for e in rep["errors"][:5]] or [str(rep["rejected"])]
    except Exception as exc:
        errs.append("validator raised %s: %s" % (type(exc).__name__, exc))
    return errs


def emit(world, out_dir=None, *, seed_only=False) -> str:
    """寫檔。回傳實際輸出目錄。拒絕寫進 repo。只清理自己管的子目錄內的帳檔。
    先寫進輸出目錄內的暫存子目錄並用驗證器驗過，才換進輸出目錄；驗證失敗 ⇒ `EmitInvalid`、輸出目錄不動。
    `seed_only=True` ⇒ 只處理 `seed/`（不寫、也不清 `mes/`）。"""
    out = out_dir or default_out_dir(world["world_id"])
    if _inside(out, REPO_ROOT):
        raise EmitRefused("refusing to write synthetic ledgers inside the repo: %s" % out)
    subs = (("seed", "seed-"),) if seed_only else (("seed", "seed-"), ("mes", "mes-"))
    keep = dict(subs)
    files = {k: v for k, v in render(world).items() if k.split("/")[0] in keep}
    out = os.path.abspath(out)
    created = not os.path.exists(out)
    os.makedirs(out, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".emit-stage-", dir=out)
    ok = False
    try:
        for rel in sorted(files):
            path = os.path.join(stage, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(files[rel])
        errs = validate_seed_dir(os.path.join(stage, "seed"))
        if errs:
            raise EmitInvalid("emitted seed failed validation: " + "; ".join(errs[:3]))
        for sub, prefix in subs:
            d = os.path.join(out, sub)
            os.makedirs(d, exist_ok=True)
            for name in sorted(os.listdir(d)):
                if name.startswith(prefix) and (name.endswith(".jsonl") or name.endswith(".json")):
                    os.remove(os.path.join(d, name))
            for name in sorted(os.listdir(os.path.join(stage, sub))):
                os.replace(os.path.join(stage, sub, name), os.path.join(d, name))
        ok = True
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if created and not ok:                 # 失敗不留半成品：連本次新建的輸出目錄一起清掉
            shutil.rmtree(out, ignore_errors=True)
    return out


def parse_until(raw):
    """`--until` 兩種形：台北 naive（`2026-07-03T14:45:00`）或 UTC Z（`2026-07-03T06:45:00Z`）。
    其他（含帶 +hh:mm 偏移）一律 ValueError。兩種形同一時刻 ⇒ 回傳同一個台北 naive datetime。"""
    import wsynth_clock
    s = raw.strip()
    if s.endswith(("Z", "z")):
        return wsynth_clock.from_utc_z(s[:-1] + "Z")
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is not None:
        raise ValueError("offset forms are not accepted (use Taipei naive or UTC Z): %r" % raw)
    return d


def summary(world, out, seed_only) -> dict:
    from wsynth_clock import from_utc_z
    n_days = len({from_utc_z(e["at"]).date() for e in world["events"]})
    return {"schema": SUMMARY_SCHEMA, "world_id": world["world_id"],
            "generator_version": world["generator_version"], "fleet_digest": world["fleet_digest"],
            "horizon_start": world["horizon_start"], "exposed_until": world["exposed_until"],
            "n_day_files": n_days, "n_events": len(world["events"]), "out": out,
            "seed_only": bool(seed_only)}


EPILOG = """\
exit codes:
  0  success
  2  argument or environment refused (bad arguments, unparseable --until, output dir inside the
     repo, host UTC offset is not +08:00)
  3  generation or validation failed (the emitted seed did not pass the wsynth_seed validator,
     or the generator raised); nothing is left in the output directory
--json prints one machine-readable summary line on stdout; all other messages go to stderr.
"""


def _err(msg):
    print(msg, file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Emit the synthetic line world (tendline.seed.v1 + mes segments).",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="output directory (default: <tmp>/tendline-synth/<world_id>); "
                                  "must not be inside the repo")
    ap.add_argument("--seed", default=P.DEFAULT_SEED)
    ap.add_argument("--until", help="Taipei naive ISO (2026-07-03T14:45:00) or UTC Z "
                                    "(2026-07-03T06:45:00Z); default = wsynth_clock.now()")
    ap.add_argument("--seed-only", action="store_true", help="write only <out>/seed/ (no <out>/mes/)")
    ap.add_argument("--json", action="store_true", help="print a one-line JSON summary on stdout")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        import wsynth_selftest
        return wsynth_selftest.main([])
    import wsynth_clock
    import wsynth_world
    try:
        wsynth_clock.check_utc_offset()
        until = parse_until(a.until) if a.until else wsynth_clock.now()
    except (wsynth_clock.ClockOffsetError, wsynth_clock.ClockError, ValueError) as exc:
        _err("wsynth_emit: refused: %s" % exc)
        return EXIT_REFUSED
    if a.out and _inside(a.out, REPO_ROOT):
        _err("wsynth_emit: refused: output dir is inside the repo: %s" % a.out)
        return EXIT_REFUSED
    try:
        world = wsynth_world.build_world(until, a.seed)
        out = emit(world, a.out, seed_only=a.seed_only)
    except EmitRefused as exc:
        _err("wsynth_emit: refused: %s" % exc)
        return EXIT_REFUSED
    except EmitInvalid as exc:
        _err("wsynth_emit: FAILED: %s" % exc)
        return EXIT_FAILED
    except Exception as exc:
        _err("wsynth_emit: FAILED: %s: %s" % (type(exc).__name__, exc))
        return EXIT_FAILED
    line = ("world_id=%s events=%d segments=%d out=%s"
            % (world["world_id"], len(world["events"]), len(world["segments"]), out))
    if a.json:
        _err(line)
        print(json.dumps(summary(world, out, a.seed_only), sort_keys=True, separators=(",", ":")))
    else:
        print(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
