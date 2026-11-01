# -*- coding: utf-8 -*-
"""board_runhours.py — 維修體質頁逐站生產時數快取（方案 C，WP-C1a；規格＝`SPEC-RUNHOURS-CACHE.md` v1）
======================================================================================================

這支只做兩件事：
1. **純函式**：檔頭／站紀錄的形狀、拒讀、窗閘、逐站換段 merge、新鮮度分級、刷新順序、讀取端合併
   （同 eid 多站＝較新錨勝、同錨照候選序後者勝；Δ 只在同一筆紀錄內配對）。
2. **兩支 I/O**：`read`（任何失敗＝`{}`、絕不 raise）與 `persist_station`（逐站 read-merge-write、
   模組級進程內鎖、原子寫一律 `board_atomic`、恆不 raise）。

**不做的事**（app 端負責）：查 MES（`app.load`）、預算、退避計時、候選站排序（`app._maint_cand_stations`）。

鐵則（SPEC §3／§4／§6／§7）
--------------------------
* **粒度＝站**；一站一筆紀錄，本期 `cur` 與前期 `prev` 綁在同一筆、同一個錨點 `anchor`（案 A）。
* **逐站換段**：本輪量到的站整筆換掉；沒量到的站原封保留。**站內不做機台聯集、不做欄位級 merge**
  （絕不出現「本輪 cur＋上一輪 prev」）。
* **檔頭參數**（`schema_ver`／`source`／`mtbf_days`／`prev_days`／`stop_min`）任一不符 ⇒ 整份拒讀；
  寫入端從空檔重建。
* **窗閘**：`cur.win` 長 == `mtbf_days` 天、`prev.win` 長 == `prev_days − mtbf_days` 天（容差 60 秒）、
  `prev.win[1] == cur.win[0]`、`cur.win[1] == anchor`；任一不符 ⇒ 不落盤（回 reason）。
* **新鮮度**：`age = now − computed_at`（本機牆鐘）。`age < soft`＝fresh；`soft ≤ age < max`＝usable
  （採用但排進刷新）；`age ≥ max`＝expired（不採）。`computed_at` 缺／壞形／純日期＝expired。
* **來源隔離**：只有 live 類來源（`core_source.is_live_like`：live／synth）讀寫；demo／snapshot 讀回 `{}`、
  寫入拒絕。檔頭 `source` 必須等於呼叫端給的 source（雙保險）。路徑走 `core_paths.state_path`
  （合成進程設 `BOARD_STATE_DIR` ⇒ 落合成狀態目錄）。

零相依：只 import 標準函式庫與 `board_atomic`／`core_paths`／`core_source`（三支皆零 repo 相依）。

自測：`python board_runhours.py --selftest`（寫檔只寫系統暫存目錄；env 只在子進程內設）。
"""
import datetime as _dt
import json
import math
import os
import sys
import threading

import board_atomic as _wat
import core_paths as _wp
import core_source as _wrs

SCHEMA_VER = 1
FILE_NAME = "maint_runhours.json"

#: 新鮮度門檻（小時；SPEC §6、§11 Q4）。env 於呼叫時讀（`soft_h()`／`max_age_h()`）。
DEFAULT_SOFT_H = 6.0
DEFAULT_MAX_AGE_H = 72.0
ENV_SOFT_H = "BOARD_RUNHOURS_SOFT_H"
ENV_MAX_AGE_H = "BOARD_RUNHOURS_MAX_AGE_H"

#: 窗長容差（秒；SPEC §4「容差 1 分鐘」）。
WIN_TOL_SEC = 60.0

#: computed_at 在未來超過此小時數＝時鐘不可信 ⇒ 視同 expired（「不知道多舊」＝「太舊」）。
FUTURE_SKEW_H = 0.1

#: MES 連線類失敗的退避秒數（§11 Q8：10 分鐘不重試刷新）。計時由 app 端持有。
CONN_BACKOFF_SEC = 600.0
#: 連線類失敗的判讀字樣（pyodbc SQLSTATE 與 driver 訊息；09-30 log 實見 `08001`／`Named Pipes Provider … 53`）。
#: ⚠ 刻意**不含** `HYT00`（查詢逾時）：那是「MES 慢」不是「連不上」，退避會把慢的一站誤當全斷。
CONN_ERROR_MARKERS = ("08001", "08S01", "08004", "HYT01", "Named Pipes Provider",
                      "TCP Provider", "Login timeout expired")

FRESH, USABLE, EXPIRED, MISSING = "fresh", "usable", "expired", "missing"

_LOCK = threading.Lock()          # read-merge-write 進程內鎖（C2 會有背景執行緒＋請求執行緒並存）；單一寫入進程假設見 SPEC §13

_HDR_KEYS = ("schema_ver", "source", "mtbf_days", "prev_days", "stop_min")


# =============================================================================
# 路徑與參數
# =============================================================================
def runhours_path(repo=None, environ=None):
    """`<repo>/_state/maint_runhours.json`；合成（`BOARD_STATE_DIR`）⇒ `<root>/maint_runhours.json`。"""
    base = repo if repo is not None else os.path.dirname(os.path.abspath(__file__))
    return _wp.state_path(FILE_NAME, repo=base, environ=environ)


def _env_hours(name, default, environ=None):
    env = os.environ if environ is None else environ
    try:
        v = float(str(env.get(name, "") or "").strip())
    except ValueError:
        return default
    return v if (math.isfinite(v) and v > 0) else default


def soft_h(environ=None):
    return _env_hours(ENV_SOFT_H, DEFAULT_SOFT_H, environ)


def max_age_h(environ=None):
    """`max` 必須 > `soft`；env 給出矛盾值 ⇒ 兩者都退回預設（不猜哪一個才是本意）。"""
    s, m = soft_h(environ), _env_hours(ENV_MAX_AGE_H, DEFAULT_MAX_AGE_H, environ)
    return m if m > s else (DEFAULT_MAX_AGE_H if DEFAULT_MAX_AGE_H > s else s * 12)


def header(source, mtbf_days, prev_days, stop_min):
    return {"schema_ver": SCHEMA_VER, "source": source, "mtbf_days": int(mtbf_days),
            "prev_days": int(prev_days), "stop_min": int(stop_min)}


# =============================================================================
# 時間
# =============================================================================
def _parse_ts(s):
    """完整 datetime 字串 → naive datetime；缺／壞形／**純日期（長度 ≤10）**／帶時區 ⇒ None。"""
    if isinstance(s, _dt.datetime):
        return s if s.tzinfo is None else None
    if not isinstance(s, str):
        return None
    s = s.strip()
    if len(s) <= 10:
        return None
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo is None else None


def _iso(d):
    return d.isoformat()


# =============================================================================
# 紀錄：建構、驗證、窗閘
# =============================================================================
def make_record(anchor, computed_at, cur_by_eid, prev_by_eid, prev_ok, mtbf_days, prev_days):
    """組一筆站紀錄（SPEC §3.2）。`anchor`＝本期查詢回來的 `mes_now`（DB 時鐘）；兩窗**都由它導出**。
       `prev_ok=False` ⇒ `prev.by_eid` 一律清空（不得拿別輪的 prev 補）。"""
    T = _parse_ts(anchor) if not isinstance(anchor, _dt.datetime) else anchor
    if T is None:
        raise ValueError("anchor")
    c0 = T - _dt.timedelta(days=int(mtbf_days))
    p0 = T - _dt.timedelta(days=int(prev_days))
    ca = computed_at if isinstance(computed_at, str) else _iso(computed_at)
    cur = {str(e): {"h": float(v["h"]), "zlo": bool(v.get("zlo"))}
           for e, v in (cur_by_eid or {}).items()}
    prev = ({str(e): {"h": float(v["h"])} for e, v in (prev_by_eid or {}).items()}
            if prev_ok else {})
    return {"anchor": _iso(T), "computed_at": ca,
            "cur": {"win": [_iso(c0), _iso(T)], "by_eid": cur},
            "prev": {"win": [_iso(p0), _iso(c0)], "by_eid": prev, "ok": bool(prev_ok)}}


def _num_ok(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(float(x)) and float(x) >= 0.0)


def norm_record(rec):
    """壞形 ⇒ None（整站丟棄）。合法 ⇒ 正規化副本（computed_at 原樣保留，由 `classify` 判新鮮度）。"""
    try:
        if not isinstance(rec, dict):
            return None
        T = _parse_ts(rec.get("anchor"))
        cur, prev = rec.get("cur"), rec.get("prev")
        if T is None or not isinstance(cur, dict) or not isinstance(prev, dict):
            return None
        cw, pw = cur.get("win"), prev.get("win")
        if not (isinstance(cw, list) and len(cw) == 2 and isinstance(pw, list) and len(pw) == 2):
            return None
        if any(_parse_ts(x) is None for x in (cw + pw)):
            return None
        cb, pb = cur.get("by_eid"), prev.get("by_eid")
        if not isinstance(cb, dict) or not isinstance(pb, dict) or not isinstance(prev.get("ok"), bool):
            return None
        ncur = {}
        for e, v in cb.items():
            if not isinstance(e, str) or not isinstance(v, dict) or not _num_ok(v.get("h")):
                return None
            ncur[e] = {"h": float(v["h"]), "zlo": bool(v.get("zlo"))}
        nprev = {}
        if prev["ok"]:
            for e, v in pb.items():
                if not isinstance(e, str) or not isinstance(v, dict) or not _num_ok(v.get("h")):
                    return None
                nprev[e] = {"h": float(v["h"])}
        ca = rec.get("computed_at")
        return {"anchor": rec["anchor"], "computed_at": ca if isinstance(ca, str) else None,
                "cur": {"win": list(cw), "by_eid": ncur},
                "prev": {"win": list(pw), "by_eid": nprev, "ok": prev["ok"]}}
    except Exception:
        return None


def window_ok(rec, mtbf_days, prev_days, tol_sec=WIN_TOL_SEC):
    """落盤窗閘（SPEC §4）→ `(ok, reason)`；reason ∈ {"", "shape", "cur_len", "prev_len", "seam", "anchor"}。"""
    r = norm_record(rec)
    if r is None:
        return (False, "shape")
    c0, c1 = (_parse_ts(x) for x in r["cur"]["win"])
    p0, p1 = (_parse_ts(x) for x in r["prev"]["win"])
    want_c = _dt.timedelta(days=int(mtbf_days)).total_seconds()
    want_p = _dt.timedelta(days=int(prev_days) - int(mtbf_days)).total_seconds()
    if abs((c1 - c0).total_seconds() - want_c) > tol_sec:
        return (False, "cur_len")
    if abs((p1 - p0).total_seconds() - want_p) > tol_sec:
        return (False, "prev_len")
    if p1 != c0:
        return (False, "seam")
    if c1 != _parse_ts(r["anchor"]):
        return (False, "anchor")
    return (True, "")


# =============================================================================
# 檔：拒讀、merge、doc
# =============================================================================
def parse(doc, hdr, mtbf_days=None, prev_days=None):
    """檔 → `{cls: record}`。檔頭任一不符 ⇒ `{}`；壞站條目（形狀或窗閘不過）丟棄、不炸。"""
    try:
        if not isinstance(doc, dict) or not isinstance(hdr, dict):
            return {}
        for k in _HDR_KEYS:
            if doc.get(k) != hdr.get(k) or type(doc.get(k)) is not type(hdr.get(k)):
                return {}
        cls_map = doc.get("cls")
        if not isinstance(cls_map, dict):
            return {}
        md = hdr["mtbf_days"] if mtbf_days is None else mtbf_days
        pd = hdr["prev_days"] if prev_days is None else prev_days
        out = {}
        for c, rec in cls_map.items():
            if not isinstance(c, str):
                continue
            r = norm_record(rec)
            if r is None or not window_ok(r, md, pd)[0]:
                continue
            out[c] = r
        return out
    except Exception:
        return {}


def merge(old, new_records):
    """**逐站換段**：`new_records` 的站整筆取代；其餘站原封保留。站內不聯集、不拼欄位。回新 dict。"""
    out = dict(old or {})
    for c, rec in (new_records or {}).items():
        out[c] = rec
    return out


def make_doc(cls_map, hdr, updated_at):
    d = dict(hdr)
    d["updated_at"] = updated_at if isinstance(updated_at, str) else _iso(updated_at)
    d["cls"] = {c: cls_map[c] for c in sorted(cls_map)}
    return d


# =============================================================================
# 新鮮度、刷新順序、讀取端合併
# =============================================================================
def classify(rec, now, soft=DEFAULT_SOFT_H, max_age=DEFAULT_MAX_AGE_H):
    """→ `(state, age_h)`；state ∈ {fresh, usable, expired, missing}。age_h 在 computed_at 不可用時為 None。"""
    if rec is None:
        return (MISSING, None)
    ca = _parse_ts((rec or {}).get("computed_at"))
    if ca is None:
        return (EXPIRED, None)
    age_h = (now - ca).total_seconds() / 3600.0
    if age_h < -FUTURE_SKEW_H:
        return (EXPIRED, None)
    if age_h < soft:
        return (FRESH, age_h)
    if age_h < max_age:
        return (USABLE, age_h)
    return (EXPIRED, age_h)


def refresh_order(cand_cls, records, now, soft=DEFAULT_SOFT_H, max_age=DEFAULT_MAX_AGE_H):
    """待量清單（SPEC §6）：沒紀錄的（照候選序）→ 過期／可用的（age 大的先；age 不明＝最舊；同齡照候選序）。
       **新鮮的站不出現**＝G1 的全部機制（同 `fleet_fetch_order` 的 stale-first 理由）。"""
    records = records or {}
    missing, stale = [], []
    for i, c in enumerate(cand_cls or ()):
        st, age = classify(records.get(c), now, soft, max_age)
        if st == MISSING:
            missing.append(c)
        elif st != FRESH:
            stale.append((-(age if age is not None else math.inf), i, c))
    stale.sort()
    return missing + [c for _, _, c in stale]


def assemble(cand_cls, records, now, soft=DEFAULT_SOFT_H, max_age=DEFAULT_MAX_AGE_H, measured=()):
    """讀取端合併 → dict：
         cur      {eid: {"h", "zlo", "cls"}}（形狀與舊 `_compute_maint_prod` 逐鍵相同）
         prev     {eid: {"h"}}（只來自 cur 那台**同一筆紀錄**、且 `prev.ok`）
         served   [cls…]（候選序；有被採用的站）
         prev_ok  [cls…]（served 中 `prev.ok` 的站）
         states   {cls: fresh|usable|expired|missing}
         n_disk   {"fresh","usable","expired","missing"}（**不含**本輪量到的 `measured` 站）
         age_max_h  served 中、非本輪量到的站的最大年齡（無＝None）
       同 eid 出現在兩站 ⇒ 較新錨勝；同錨 ⇒ 候選序**後者**勝（與舊路徑 last-wins 等價的確定性規則）。"""
    records = records or {}
    measured = set(measured or ())
    served, prev_ok, states = [], [], {}
    n_disk = {FRESH: 0, USABLE: 0, EXPIRED: 0, MISSING: 0}
    age_max = None
    chosen = {}                       # eid → (anchor_dt, cls, rec)
    for c in cand_cls or ():
        rec = records.get(c)
        st, age = classify(rec, now, soft, max_age)
        states[c] = st
        if c not in measured:
            n_disk[st] += 1
        if st not in (FRESH, USABLE):
            continue
        served.append(c)
        if rec["prev"]["ok"]:
            prev_ok.append(c)
        if c not in measured and age is not None:
            age_max = age if age_max is None else max(age_max, age)
        T = _parse_ts(rec["anchor"])
        for e in rec["cur"]["by_eid"]:
            got = chosen.get(e)
            if got is None or T >= got[0]:
                chosen[e] = (T, c, rec)
    cur, prev = {}, {}
    for e, (_T, c, rec) in chosen.items():
        v = rec["cur"]["by_eid"][e]
        cur[e] = {"h": v["h"], "zlo": bool(v.get("zlo")), "cls": c}
        if rec["prev"]["ok"] and e in rec["prev"]["by_eid"]:
            prev[e] = {"h": rec["prev"]["by_eid"][e]["h"]}
    return {"cur": cur, "prev": prev, "served": served, "prev_ok": prev_ok, "states": states,
            "n_disk": n_disk, "age_max_h": age_max}


def is_conn_error(exc):
    """MES 連線類失敗（Q8 退避的觸發條件）。只看例外文字；判讀字樣＝`CONN_ERROR_MARKERS`。"""
    try:
        txt = f"{exc!r} {exc!s} {getattr(exc, 'args', ())!r}"
    except Exception:
        return False
    return any(m in txt for m in CONN_ERROR_MARKERS)


# =============================================================================
# I/O
# =============================================================================
def _load_raw(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read(path, source, mtbf_days, prev_days, stop_min):
    """讀檔 → `{cls: record}`；檔缺／壞／拒讀／非 live 類來源 ⇒ `{}`。**絕不 raise**。"""
    try:
        if not _wrs.is_live_like(source):
            return {}
        raw = _load_raw(path)
        if raw is None:
            return {}
        return parse(raw, header(source, mtbf_days, prev_days, stop_min))
    except Exception:
        return {}


def persist_station(path, cls, rec, source, mtbf_days, prev_days, stop_min, now_iso=None):
    """逐站 read-merge-write（進程內鎖內）→ `(written, reason)`；**恆不 raise**。
       reason：`written:<站數>`／`source`／`win:<窗閘 reason>`／`error:<例外類名>`。
       檔頭不符（或檔壞）⇒ 從空檔重建（不 merge 舊內容）。原子寫一律 `board_atomic.atomic_write_json`。
       ⚠ **單一寫入進程假設**（SPEC §13）：`_LOCK` 只是進程內鎖；同一檔若有兩個進程（如 uvicorn `--workers`>1）
       同時寫，後寫者會丟掉另一進程剛寫的站。改多 worker 之前必須先在這裡加跨進程檔鎖。"""
    try:
        if not _wrs.is_live_like(source):
            return (False, "source")
        ok, why = window_ok(rec, mtbf_days, prev_days)
        if not ok:
            return (False, f"win:{why}")
        hdr = header(source, mtbf_days, prev_days, stop_min)
        with _LOCK:
            _wrs.assert_state_marker()          # 合成進程：狀態目錄必須有標記（非合成：no-op）
            raw = _load_raw(path)
            old = parse(raw, hdr) if raw is not None else {}
            new = merge(old, {cls: norm_record(rec)})
            ts = now_iso or _dt.datetime.now().replace(microsecond=0).isoformat()
            _wat.atomic_write_json(path, make_doc(new, hdr, ts))
            return (True, f"written:{len(new)}")
    except Exception as e:
        return (False, f"error:{type(e).__name__}")


# =============================================================================
# 自測
# =============================================================================
def _selftest():
    import shutil
    import subprocess
    import tempfile
    from unittest import mock

    res = {"pass": 0, "fail": 0}

    def ck(cond, msg):
        if cond:
            res["pass"] += 1
        else:
            res["fail"] += 1
            print("FAIL:", msg)

    D = _dt.datetime
    TD = _dt.timedelta
    T = D(2026, 10, 2, 8, 57, 12)
    NOW = D(2026, 10, 2, 9, 0, 0)
    MD, PD, SM = 90, 180, 15
    HDR = header("live", MD, PD, SM)

    def rec(anchor=T, comp=None, cur=None, prev=None, ok=True):
        return make_record(anchor, comp or (NOW - TD(minutes=3)),
                           cur if cur is not None else {"E1": {"h": 812.4, "zlo": False}},
                           prev if prev is not None else {"E1": {"h": 790.1}}, ok, MD, PD)

    # ── 1. 形狀與 parse 拒讀 ──
    r0 = rec()
    ck(r0["cur"]["win"] == [_iso(T - TD(days=90)), _iso(T)], "1a cur.win = [T-90d, T]")
    ck(r0["prev"]["win"] == [_iso(T - TD(days=180)), _iso(T - TD(days=90))], "1b prev.win = [T-180d, T-90d]")
    ck(r0["prev"]["win"][1] == r0["cur"]["win"][0], "1c seam prev.win[1] == cur.win[0]")
    ck(set(r0) == {"anchor", "computed_at", "cur", "prev"} and r0["prev"]["ok"] is True, "1d record keys")
    ck(make_record(T, NOW, {"E1": {"h": 1.0}}, {"E1": {"h": 2.0}}, False, MD, PD)["prev"]["by_eid"] == {},
       "1e prev_ok=False ⇒ prev.by_eid emptied")
    doc = make_doc({"segA": r0}, HDR, NOW)
    ck(parse(json.loads(json.dumps(doc)), HDR) == {"segA": norm_record(r0)}, "1f doc→json→parse round-trip")
    for k, bad in (("schema_ver", 2), ("source", "synth"), ("mtbf_days", 60), ("prev_days", 120),
                   ("stop_min", 30)):
        d2 = dict(doc)
        d2[k] = bad
        ck(parse(d2, HDR) == {}, f"1g header mismatch {k} ⇒ whole file rejected")
    d2 = dict(doc)
    d2["mtbf_days"] = 90.0
    ck(parse(d2, HDR) == {}, "1h header type mismatch (90.0 vs 90) ⇒ rejected")
    ck(parse({"schema_ver": 1}, HDR) == {} and parse(None, HDR) == {} and parse([], HDR) == {},
       "1i garbage docs ⇒ {}")
    bad_doc = make_doc({"segA": r0, "segB": {"anchor": "x"}, "segC": dict(r0, cur={"win": [], "by_eid": {}}),
                        "segD": dict(r0, cur={"win": r0["cur"]["win"], "by_eid": {"E9": {"h": "nan"}}}),
                        "segE": dict(r0, cur={"win": r0["cur"]["win"], "by_eid": {"E9": {"h": -1.0}}})},
                       HDR, NOW)
    p = parse(bad_doc, HDR)
    ck(set(p) == {"segA"}, f"1j bad station entries dropped, good kept (got {sorted(p)})")
    ck(norm_record(dict(r0, prev=dict(r0["prev"], ok="yes"))) is None, "1k prev.ok must be bool")
    ck(parse(dict(doc, cls=[1, 2]), HDR) == {}, "1l cls not a dict ⇒ {}")

    # ── 3. 窗閘 ──
    ck(window_ok(r0, MD, PD) == (True, ""), "3a good record passes")
    r_bad = json.loads(json.dumps(r0))
    r_bad["cur"]["win"][0] = _iso(T - TD(days=89))
    ck(window_ok(r_bad, MD, PD) == (False, "cur_len"), "3b cur window length mismatch")
    r_bad = json.loads(json.dumps(r0))
    r_bad["prev"]["win"][0] = _iso(T - TD(days=170))
    ck(window_ok(r_bad, MD, PD) == (False, "prev_len"), "3c prev window length mismatch")
    r_bad = json.loads(json.dumps(r0))
    r_bad["prev"]["win"] = [_iso(T - TD(days=180, seconds=30)), _iso(T - TD(days=90, seconds=30))]
    ck(window_ok(r_bad, MD, PD) == (False, "seam"), "3d seam broken (prev.win[1] != cur.win[0])")
    r_tol = json.loads(json.dumps(r0))
    r_tol["cur"]["win"][0] = _iso(T - TD(days=90, seconds=-59))
    r_tol["prev"]["win"] = [_iso(T - TD(days=180, seconds=-59)), r_tol["cur"]["win"][0]]
    ck(window_ok(r_tol, MD, PD)[0] is True, "3e within 60s tolerance passes")
    r_bad = json.loads(json.dumps(r0))
    r_bad["anchor"] = _iso(T + TD(minutes=5))
    ck(window_ok(r_bad, MD, PD) == (False, "anchor"), "3f cur.win[1] must equal anchor")
    ck(window_ok({"anchor": 1}, MD, PD) == (False, "shape"), "3g malformed ⇒ shape")
    ck(window_ok(make_record(T, NOW, {}, {}, True, 60, 120), MD, PD)[0] is False,
       "3h record built for other MTBF days fails the gate")
    ck(parse(make_doc({"segA": r0, "segX": r_bad}, HDR, NOW), HDR).keys() == {"segA"},
       "3i parse drops a gate-failing record")

    # ── 2. merge 逐站換段（含負斷言）──
    old = {"segA": rec(cur={"E1": {"h": 1.0, "zlo": False}, "E2": {"h": 2.0, "zlo": False}},
                       prev={"E1": {"h": 9.0}, "E2": {"h": 8.0}}),
           "segB": rec(cur={"E3": {"h": 3.0, "zlo": False}})}
    newA = rec(anchor=T + TD(hours=7), cur={"E1": {"h": 5.0, "zlo": False}}, prev={}, ok=False)
    m = merge(old, {"segA": newA})
    ck(m["segA"] is newA and m["segB"] is old["segB"], "2a measured station replaced whole; others untouched")
    ck("E2" not in m["segA"]["cur"]["by_eid"], "2b NEGATIVE: no machine union inside a station (E2 gone)")
    ck(m["segA"]["prev"]["ok"] is False and m["segA"]["prev"]["by_eid"] == {},
       "2c NEGATIVE: never this round's cur + last round's prev")
    ck(set(old["segA"]["cur"]["by_eid"]) == {"E1", "E2"}, "2d merge does not mutate its input")

    # ── 4. fresh 邊界 ──
    S, M = DEFAULT_SOFT_H, DEFAULT_MAX_AGE_H
    def at(h):
        return {"computed_at": _iso(NOW - TD(hours=h))}
    ck(classify(at(0), NOW)[0] == FRESH, "4a age 0 fresh")
    ck(classify(at(S - 0.001), NOW)[0] == FRESH, "4b age just < 6h fresh")
    ck(classify(at(S), NOW)[0] == USABLE, "4c age == 6h usable (boundary)")
    ck(classify(at(M - 0.001), NOW)[0] == USABLE, "4d age just < 72h usable")
    ck(classify(at(M), NOW)[0] == EXPIRED, "4e age == 72h expired (boundary)")
    ck(classify({"computed_at": None}, NOW) == (EXPIRED, None), "4f computed_at missing ⇒ expired")
    ck(classify({"computed_at": "2026-10-02"}, NOW)[0] == EXPIRED, "4g date-only (len 10) ⇒ expired")
    ck(classify({"computed_at": "2026-13-45T00:00:00"}, NOW)[0] == EXPIRED, "4h malformed ⇒ expired")
    ck(classify({"computed_at": "2026-10-02T09:00:00+08:00"}, NOW)[0] == EXPIRED, "4i tz-aware ⇒ expired")
    ck(classify({"computed_at": _iso(NOW + TD(hours=2))}, NOW)[0] == EXPIRED, "4j future skew ⇒ expired")
    ck(classify(None, NOW) == (MISSING, None), "4k no record ⇒ missing")
    ck(soft_h({}) == 6.0 and max_age_h({}) == 72.0, "4l defaults 6h / 72h")
    ck(soft_h({ENV_SOFT_H: "3"}) == 3.0 and max_age_h({ENV_MAX_AGE_H: "48"}) == 48.0, "4m env honoured")
    ck(soft_h({ENV_SOFT_H: "junk"}) == 6.0 and max_age_h({ENV_MAX_AGE_H: "-1"}) == 72.0, "4n bad env ⇒ default")
    ck(max_age_h({ENV_SOFT_H: "10", ENV_MAX_AGE_H: "5"}) > soft_h({ENV_SOFT_H: "10"}),
       "4o contradictory env keeps max > soft")

    # ── 5. 刷新順序 ──
    recs5 = {"sF": dict(rec(), computed_at=_iso(NOW - TD(hours=1))),
             "sU10": dict(rec(), computed_at=_iso(NOW - TD(hours=10))),
             "sU30": dict(rec(), computed_at=_iso(NOW - TD(hours=30))),
             "sX": dict(rec(), computed_at=_iso(NOW - TD(hours=100))),
             "sBad": dict(rec(), computed_at="??")}
    order = refresh_order(["sF", "sU10", "sM1", "sU30", "sX", "sM2", "sBad"], recs5, NOW)
    ck(order == ["sM1", "sM2", "sBad", "sX", "sU30", "sU10"], f"5a missing > stale(oldest first) (got {order})")
    ck("sF" not in order, "5b fresh station never in the to-measure list")
    ck(refresh_order(["b", "a"], {}, NOW) == ["b", "a"], "5c all missing ⇒ candidate (R46) order kept")
    tie = {"x": dict(rec(), computed_at=_iso(NOW - TD(hours=10))),
           "y": dict(rec(), computed_at=_iso(NOW - TD(hours=10)))}
    ck(refresh_order(["y", "x"], tie, NOW) == ["y", "x"], "5d same age ⇒ candidate order")

    # ── 6. Δ 配對 ──
    rA = rec(cur={"E1": {"h": 10.0, "zlo": False}, "E2": {"h": 20.0, "zlo": True}},
             prev={"E1": {"h": 7.0}, "E2": {"h": 6.0}})
    rB = rec(cur={"E3": {"h": 30.0, "zlo": False}}, prev={}, ok=False)
    a = assemble(["segA", "segB"], {"segA": rA, "segB": rB}, NOW)
    ck(a["cur"] == {"E1": {"h": 10.0, "zlo": False, "cls": "segA"},
                    "E2": {"h": 20.0, "zlo": True, "cls": "segA"},
                    "E3": {"h": 30.0, "zlo": False, "cls": "segB"}}, "6a cur map shape = old {h,zlo,cls}")
    ck(a["prev"] == {"E1": {"h": 7.0}, "E2": {"h": 6.0}}, "6b prev only from same record")
    ck("E3" not in a["prev"] and a["prev_ok"] == ["segA"], "6c prev.ok=false ⇒ every Δ of that station missing")
    rC = rec(cur={"E4": {"h": 1.0, "zlo": False}}, prev={"E5": {"h": 2.0}})
    ck("E4" not in assemble(["segC"], {"segC": rC}, NOW)["prev"], "6d eid without prev entry ⇒ no Δ")
    stale_rec = dict(rA, computed_at=_iso(NOW - TD(hours=80)))
    a2 = assemble(["segA"], {"segA": stale_rec}, NOW)
    ck(a2["cur"] == {} and a2["served"] == [] and a2["n_disk"][EXPIRED] == 1, "6e expired station not served")
    us_rec = dict(rA, computed_at=_iso(NOW - TD(hours=20)))
    a3 = assemble(["segA", "segZ"], {"segA": us_rec}, NOW)
    ck(a3["served"] == ["segA"] and a3["n_disk"] == {FRESH: 0, USABLE: 1, EXPIRED: 0, MISSING: 1}
       and abs(a3["age_max_h"] - 20.0) < 1e-9, "6f usable served; disk counts; age_max_h")
    a4 = assemble(["segA"], {"segA": rA}, NOW, measured={"segA"})
    ck(a4["n_disk"] == {FRESH: 0, USABLE: 0, EXPIRED: 0, MISSING: 0} and a4["age_max_h"] is None,
       "6g measured station not counted as disk")
    ck(assemble(["segQ"], {"segA": rA}, NOW)["cur"] == {}, "6h non-candidate stations ignored")

    # ── 7. 同 eid 兩站 ──
    r_old = rec(anchor=T - TD(hours=3), cur={"E1": {"h": 1.0, "zlo": False}}, prev={"E1": {"h": 11.0}})
    r_new = rec(anchor=T, cur={"E1": {"h": 2.0, "zlo": False}}, prev={}, ok=False)
    a = assemble(["s1", "s2"], {"s1": r_new, "s2": r_old}, NOW)
    ck(a["cur"]["E1"]["cls"] == "s1" and a["cur"]["E1"]["h"] == 2.0, "7a newer anchor wins (even if earlier)")
    ck("E1" not in a["prev"], "7b Δ follows the winning record (its prev.ok=false ⇒ no Δ, not the loser's prev)")
    r_s = rec(anchor=T, cur={"E1": {"h": 5.0, "zlo": False}})
    a = assemble(["s1", "s2"], {"s1": r_new, "s2": r_s}, NOW)
    ck(a["cur"]["E1"]["cls"] == "s2" and "E1" in a["prev"], "7c same anchor ⇒ later candidate wins (last-wins)")

    # ── 連線類失敗判讀 ──
    ck(is_conn_error(Exception("('08001', '[08001] [Microsoft][ODBC Driver 17 for SQL Server]"
                               "Named Pipes Provider: Could not open a connection to SQL Server [53]. ')")),
       "Q8a 08001 / Named Pipes ⇒ connection error")
    ck(not is_conn_error(Exception("('HYT00', '[HYT00] Query timeout expired')")),
       "Q8b HYT00 query timeout is NOT a connection error")
    ck(not is_conn_error(ValueError("boom")), "Q8c ordinary error ⇒ not connection")

    # ── 8. I/O ──
    tmp = tempfile.mkdtemp(prefix="board-runhours-st-")
    try:
        pth = os.path.join(tmp, "sub", "dir", FILE_NAME)
        ck(read(pth, "live", MD, PD, SM) == {}, "8a missing file ⇒ {}")
        calls = []
        real_w = _wat.atomic_write_json

        def _count(path, doc, **kw):
            calls.append(path)
            return real_w(path, doc, **kw)
        with mock.patch.object(_wat, "atomic_write_json", _count):
            ok1 = persist_station(pth, "segA", rA, "live", MD, PD, SM, now_iso="2026-10-02T09:00:00")
            ok2 = persist_station(pth, "segB", rB, "live", MD, PD, SM)
        ck(ok1 == (True, "written:1") and ok2 == (True, "written:2"), f"8b persist ok ({ok1}, {ok2})")
        ck(calls == [pth, pth], "8c writes go through board_atomic.atomic_write_json")
        ck(os.path.isfile(pth), "8d missing parent dirs created")
        got = read(pth, "live", MD, PD, SM)
        ck(set(got) == {"segA", "segB"} and got["segA"] == norm_record(rA), "8e read back per station")
        raw = json.load(open(pth, encoding="utf-8"))
        ck(all(raw.get(k) == HDR[k] for k in _HDR_KEYS) and "updated_at" in raw, "8f header stamped")
        ck(not [f for f in os.listdir(os.path.dirname(pth)) if f.endswith(".tmp")], "8g no temp leftovers")
        ck(read(pth, "synth", MD, PD, SM) == {}, "8h source mismatch on read ⇒ {}")
        ck(read(pth, "live", 60, 120, SM) == {}, "8i MTBF change ⇒ whole file rejected")
        ck(persist_station(pth, "segC", rC, "live", 60, 120, SM)[0] is False, "8j gate refuses record of other days")
        r60 = make_record(T, NOW, {"E1": {"h": 1.0}}, {"E1": {"h": 1.0}}, True, 60, 120)
        ck(persist_station(pth, "segC", r60, "live", 60, 120, SM) == (True, "written:1"),
           "8k header mismatch ⇒ writer rebuilds from an empty file (old stations dropped)")
        ck(set(read(pth, "live", 60, 120, SM)) == {"segC"}, "8l rebuilt file holds only the new station")
        ck(persist_station(pth, "segX", r_bad, "live", MD, PD, SM) == (False, "win:anchor"),
           "8m gate failure ⇒ not persisted + reason")
        for src in ("demo", "snapshot", "", None):
            ck(persist_station(pth, "segA", rA, src, MD, PD, SM) == (False, "source"),
               f"8n source {src!r} never writes")
            ck(read(pth, src, 60, 120, SM) == {}, f"8o source {src!r} never reads")

        def _boom(*a, **k):
            raise PermissionError("locked")
        with mock.patch.object(_wat, "atomic_write_json", _boom):
            r = persist_station(pth, "segA", rA, "live", 60, 120, SM)
        ck(r == (False, "win:cur_len"), "8p (gate first)")
        with mock.patch.object(_wat, "atomic_write_json", _boom):
            r = persist_station(pth, "segZ", r60, "live", 60, 120, SM)
        ck(r == (False, "error:PermissionError"), f"8q write exception never raises ({r})")
        ck(set(read(pth, "live", 60, 120, SM)) == {"segC"}, "8r failed write leaves the old file intact")
        with open(pth, "w", encoding="utf-8") as f:
            f.write("{not json")
        ck(read(pth, "live", 60, 120, SM) == {}, "8s corrupt file ⇒ {}")
        ck(persist_station(pth, "segC", r60, "live", 60, 120, SM)[0] is True, "8t corrupt file ⇒ rebuilt")
        ck(isinstance(_LOCK, type(threading.Lock())), "8u module-level process lock exists")
        # 兩執行緒交錯寫不丟站
        p2 = os.path.join(tmp, "race", FILE_NAME)
        errs = []

        def _w(prefix):
            try:
                for i in range(12):
                    persist_station(p2, f"{prefix}{i}", rA, "live", MD, PD, SM)
            except Exception as e:     # pragma: no cover
                errs.append(e)
        ths = [threading.Thread(target=_w, args=(p,)) for p in ("a", "b", "c")]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        ck(not errs and len(read(p2, "live", MD, PD, SM)) == 36, "8v three interleaved writers lose no station")
    finally:
        shutil.rmtree(tmp, True)

    # ── 9. 合成路徑（子進程內設 BOARD_STATE_DIR）──
    sd = tempfile.mkdtemp(prefix="board-runhours-sd-")
    try:
        env = dict(os.environ)
        env[_wp.ENV_STATE_DIR] = sd
        here = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run([sys.executable, "-c",
                              "import board_runhours as m; print(m.runhours_path())"],
                             cwd=here, env=env, capture_output=True, text=True, timeout=60)
        got_p = (out.stdout or "").strip()
        ck(os.path.normcase(os.path.dirname(got_p)) == os.path.normcase(os.path.abspath(sd)),
           f"9a synth: path lands in BOARD_STATE_DIR ({got_p!r} {out.stderr[-200:]!r})")
        repo_state = _wp.state_dir(repo=here, environ={})
        ck(not os.path.normcase(got_p).startswith(os.path.normcase(str(repo_state))),
           "9b synth: path not under the repo state dir")
        ck(os.path.normcase(runhours_path(environ={})) ==
           os.path.normcase(os.path.join(str(repo_state), FILE_NAME)), "9c live: repo state dir")
    finally:
        shutil.rmtree(sd, True)

    print(f"board_runhours selftest: {res['pass']} PASS / {res['fail']} FAIL")
    return 0 if res["fail"] == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    print(__doc__)
