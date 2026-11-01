"""_wo_ledger.py -- 工單日帳（`/downtime` 的事實表；SPEC-DOWNTIME.md §5；ROADMAP #167 P1）。

一句話：每個 08:00 生產日、每台母機、每張工單、每個階段態落一列停機秒數，兩把尺並存
（`raw_sec`＝24h 軸、永遠有；`clipped_sec`＝該跑窗交集、三源涵蓋才有、缺＝NULL）。

★ 鐵則（SPEC §10；違反即作廢）
  · C-DT2  停機計算**只呼叫 §4.2 積木**：`w8.wo_active_window`／`w8.wo_is_cancelled`／
           `wem.wo_segments_scoped`（內含 `wo_segments`＋`dormant_split_segments`）／
           `wem.downtime_by_eid_scoped`／`wem.bucket_segments_by_day`／`wem.erw_row`／
           `wcap.fold_seat_id`／`wem._union_seconds`。本檔**零自寫分段**；
           **禁用** `wem._inherited_segments`（A2：一張單在母機與各模組各算一次）。
  · C-DT3  `clipped_sec` 缺＝NULL，永不 0、永不以 raw 代打。
  · C-DT4  區只從 `EQMT_CATALOG.area_key` 來（`area_of_eid`）；禁用 `_AREA2REGION`／`KUN_REGION`。
  · C-DT5  機台 union（`eid_day`，bar 的分子）與單張（`wo_day`，排名）兩張表、不互推。
  · C-DT10 事實列唯一寫者＝本檔；凍結人明示（`--freeze-month`）；已凍月零寫入。
  · C-DT13 母體不隨尺換：名冊外／目錄外列照樣落帳（`in_roster=0`／`area='__offcat__'`），
           讀端一律不進 bar。
  · C-DT14 eMaint 時代 PM 單＝`is_emaint_pm(rec)` **在輸入端濾掉**（積木零改動）；只留
           `wo_meta.excluded_reason='emaint_pm'` 供頁尾計數。
  · C-DT15 遷移單不推算等機台時間（本檔沒有任何這樣的分支）。
  · A12    已滾出三源涵蓋的日子，`clipped_sec` **保留原值**（不改寫成 NULL、不用 raw 覆蓋）。

純函式核心＝`compute_day_rows`（零 I/O）；I/O（cmms、日曆檔、A11、sqlite、log）只在 CLI 那一層。
CLI：
    python _wo_ledger.py --day YYYY-MM-DD        # 寫 D 這一天
    python _wo_ledger.py --refresh               # 排程用：未凍月全部日子重算（見 run-wo-ledger.ps1）
    python _wo_ledger.py --backfill 2024-07 2026-08
    python _wo_ledger.py --freeze-month YYYY-MM  # 人明示才凍
    python _wo_ledger.py --dump --month YYYY-MM  # sqlite 不能 git diff 的緩解（§5.2）
    python _wo_ledger.py --selftest
    （任一寫入指令加 --dry-run＝算、印摘要、零寫入；加 --fixture PATH＝以 fixture 取代 cmms）
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import board_w8 as w8
import core_paths as _wp   # 執行期產物路徑唯一出處（Q-F；零相依）
import board_eqmeeting as wem
import board_capacity as _wcap
import board_dormant as wdorm

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
SCHEMA_VERSION = 1
ENV_LEDGER = "BOARD_WO_LEDGER"
DEFAULT_LEDGER = _wp.state_path("wo_ledger.sqlite", repo=BASE)   # import 時定值＝原時機
LOCK_NAME = "wo_ledger.lock"
LOCK_STALE_SEC = 2 * 3600
LOG_DIR = BASE / "_logs"
SETTLED_AFTER_MIN = 25     # 排程 settled 規則（#166 同形）：D+1 08:25 之後算過且 clipped ⇒ 當天不再跑

BAR_AREAS = ("za", "zb", "zc")          # 【需求】只顯示 Zone A／B／C；Zone D 不顯示
OFFCAT = "__offcat__"                      # 目錄外 asset（§6.3；⑪）
LONG_WO_DAYS = 90                          # §6.5 長掛單門檻（§14 ① 已裁）
LIST_CAP = 2000                            # cmms active-in 上限（§4.4）；回滿＝截斷＝中止
MAX_WINDOW_DAYS = 31                       # §5.6：每窗 ≤31 天
PROD_DAY_START_H = wem.PROD_DAY_START_H    # 08:00 生產日界（唯一出處在 eqmeeting）

# wo_segments 的 canonical state → 面板「修／等料／等外包」三欄（顯示層分組；**不改任何數**）
STAGE_OF_KIND = {wem.HOLD_PARTS_STATE: "parts", wem.HOLD_VENDOR_STATE: "vendor"}

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS wo_day (
  day TEXT NOT NULL, eid TEXT NOT NULL, area TEXT NOT NULL, wo_no TEXT NOT NULL,
  kind TEXT NOT NULL, raw_sec INTEGER NOT NULL, clipped_sec INTEGER,
  coverage_flag TEXT NOT NULL, in_roster INTEGER NOT NULL,
  ivs TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (day, eid, wo_no, kind));
CREATE TABLE IF NOT EXISTS eid_day (
  day TEXT NOT NULL, eid TEXT NOT NULL, area TEXT NOT NULL, raw_sec INTEGER NOT NULL,
  clipped_sec INTEGER, coverage_flag TEXT NOT NULL, in_roster INTEGER NOT NULL,
  rw_iv TEXT,
  PRIMARY KEY (day, eid));
CREATE TABLE IF NOT EXISTS wo_meta (
  wo_no TEXT PRIMARY KEY, eid TEXT, area TEXT, work_type TEXT, opened_at TEXT,
  closed_at TEXT, status TEXT, fault_code TEXT, flag_migrated INTEGER,
  flag_oneclick_pm INTEGER, flag_long INTEGER, span_days REAL, active_start TEXT,
  active_end TEXT, detail_hash TEXT, fetched_at TEXT, excluded_reason TEXT);
CREATE TABLE IF NOT EXISTS wo_summary (
  wo_no TEXT NOT NULL, lang TEXT NOT NULL, prompt_version TEXT NOT NULL,
  detail_hash TEXT, body_json TEXT, created_at TEXT,
  PRIMARY KEY (wo_no, lang, prompt_version));
CREATE TABLE IF NOT EXISTS month_freeze (
  month TEXT PRIMARY KEY, frozen_at TEXT, by_cli TEXT);
CREATE TABLE IF NOT EXISTS ledger_day (
  day TEXT PRIMARY KEY, coverage_flag TEXT NOT NULL, computed_at TEXT,
  n_rows INTEGER, schema_version INTEGER);
CREATE TABLE IF NOT EXISTS ledger_meta (
  key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_wo_day_wo ON wo_day(wo_no);
"""
# ★ 兩張加性表（§5.3 之外；實作輪自裁、報告 §7 有記）：
#   · `ledger_day`＝「哪一天用哪把尺」的日級真相。月份退 24h 軸的判定（§6.1）必須看
#     **每一天**，而一天若零工單就一列 `wo_day` 都沒有 ⇒ 只看 `wo_day.coverage_flag`
#     會把「沒涵蓋但剛好沒停機」的日子漏看成涵蓋。
#   · `ledger_meta`＝A11 起算月（⑬）、schema_version 之類的單值事實（頁尾揭露用）。
# ★ `wo_day.ivs`／`eid_day.rw_iv`＝⑰「chip 下讀端重做 union」的原料（秒、相對生產日 08:00）：
#   `wo_day` 只存秒數就做不了 union（union 要區間）。`rw_iv` 只在 clipped 列才有。


# ---------------------------------------------------------------------------
# 純函式：謂詞、區、meta
# ---------------------------------------------------------------------------

def is_emaint_pm(rec):
    """C-DT14（Chris 裁 ⑥）：`status_history` 空（遷移單、無狀態轉移）**且** work_type＝PM。
       **不看結單與否**（SPEC §6.6-a）。單一出處：日後若 ⑲ 同步 `/equipment`，就搬這一支。"""
    if not isinstance(rec, dict):
        return False
    return (not (rec.get("status_history") or [])
            and str(rec.get("work_type") or "").strip().upper() == "PM")


def area_of_eid(eid, catalog=None):
    """C-DT4：區**只**從 `EQMT_CATALOG.area_key` 來。

       · 目錄內 ⇒ 該列 area_key。
       · `wscope.PARENT_EXPANSION` 的母機（`EID-24742`／`16077`／`27600`，**不在目錄**）⇒
         其子槽位在目錄裡的 area_key **全體一致**時取該值（仍是目錄那一欄，不是推論）；
         不一致或子槽位都不在目錄 ⇒ 目錄外。
       · 其餘 ⇒ `'__offcat__'`（⑪ 已裁：落目錄外桶、頁尾揭露）。"""
    import board_scope as _wscope
    cat = wem.EQMT_CATALOG if catalog is None else catalog
    amap = {e: a for a, _n, e in cat}
    if eid in amap:
        return amap[eid]
    kids = _wscope.PARENT_EXPANSION.get(eid) or ()
    areas = {amap[k] for k in kids if k in amap}
    if len(areas) == 1 and all(k in amap for k in kids):
        return next(iter(areas))
    return OFFCAT


def _fmt(dt):
    return None if dt is None else dt.strftime("%Y-%m-%d %H:%M:%S")


def _hash_rec(rec):
    """normalize 後工單的內容雜湊（`wo_meta.detail_hash`）：狀態／結單／history 任一變動就變。"""
    blob = json.dumps(rec, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _wo_meta(rec, eid, area, now, excluded_reason=None):
    """一張單的 meta 列（排名欄位＋來源旗；**旗只揭露、不改數**＝C-DT7）。"""
    win = w8.wo_active_window(rec, now=now)
    a_start = a_end = None
    span = None
    if win is not None:
        a_start = wem._coerce_dt(win[0])
        a_end = wem._coerce_dt(win[1]) if win[1] is not None else None
        span = ((a_end or now) - a_start).total_seconds() / 86400.0
    hist = rec.get("status_history") or []
    wt = str(rec.get("work_type") or "").strip().upper()
    migrated = (not hist) and rec.get("closed_at") is not None
    oneclick = False
    if wt == "PM" and hist and a_start is not None and a_end is not None:
        # 一鍵結單 PM（記憶 cmms-oneclick-close-mttr）：整個活躍窗內 **countable 段總長 0**
        # ——判定走同一支 `wo_segments`（積木），不另寫「IN_PROGRESS 長度」的第二份解讀。
        segs = wem.wo_segments([rec], a_start, a_end)
        oneclick = not any(s.get("countable", True) and s["end"] > s["start"] for s in segs)
    return {
        "wo_no": str(rec.get("work_order_no")), "eid": eid, "area": area, "work_type": wt or None,
        "opened_at": _fmt(wem._coerce_dt(rec.get("opened_at"))),
        "closed_at": _fmt(wem._coerce_dt(rec.get("closed_at"))),
        "status": rec.get("status"), "fault_code": rec.get("confirmed_reason_code"),
        "flag_migrated": int(bool(migrated)), "flag_oneclick_pm": int(bool(oneclick)),
        "flag_long": int(bool(span is not None and span > LONG_WO_DAYS)),
        "span_days": None if span is None else round(span, 3),
        "active_start": _fmt(a_start), "active_end": _fmt(a_end),
        "detail_hash": _hash_rec(rec), "fetched_at": _fmt(now),
        "excluded_reason": excluded_reason,
    }


def day_window(day, now):
    """生產日 D → `(ds, d_start, d_end)`；d_end 裁到 now（當天＝partial）。未開始 ⇒ None。"""
    d = _dt.date.fromisoformat(day)
    ws = _dt.datetime(d.year, d.month, d.day, PROD_DAY_START_H)
    we = ws + _dt.timedelta(days=1)
    if now is not None and now < we:
        we = now
    if we <= ws:
        return None
    return (day, ws, we)


def _erw_ctx(day_wins, calendar, segs_all):
    """該跑窗 ctx（`wem.erw_prepare`＝`/equipment` 同一支建構器；C-PC8 住在它裡面）。

       `calendar` 兩形：
         · `{"doc": 日曆 doc, "slots": …, "mp": …}`（live／CLI）⇒ closures 經
           `wd.calendar_closures`＋`wd.closures_with_uncertainty`（C-PC12 兜底）＝`/equipment` 同路。
         · `{"closures": […], "coverage": (from, to), "slots": …, "mp": …}`（fixture／demo 預解形）。
       None／doc 缺 ⇒ None（整天 raw_only）。"""
    if not calendar:
        return None
    ws, we = day_wins[0][1], day_wins[-1][2]
    if "doc" in calendar:
        doc = calendar.get("doc")
        if not doc:
            return None
        import board_data as wd
        closures = wd.closures_with_uncertainty(wd.calendar_closures(doc, ws, we), segs_all)
        cov = wd.calendar_coverage(doc)
    else:
        closures = wem.closures_in_window(list(calendar.get("closures") or ()), ws, we)
        cov = calendar.get("coverage")
        if cov:
            cov = (wem._coerce_dt(cov[0]), wem._coerce_dt(cov[1]))
    return wem.erw_prepare(day_wins, closures, cov, calendar.get("slots"), calendar.get("mp"))


def _rel_ivs(ivs, d_start):
    """datetime 區間 → 相對生產日起點的整數秒 `[[s, e], …]`（已 union、排序）。"""
    out = []
    for s, e in wem._union_intervals(ivs):
        out.append([int(round((s - d_start).total_seconds())),
                    int(round((e - d_start).total_seconds()))])
    return out


def compute_day_rows(day, work_orders, catalog, calendar, *, now, dormant_events=None):
    """一個生產日 → `(wo_day_rows, eid_day_rows, wo_meta_rows, footer_counts)`。**零 I/O。**

       `work_orders`＝`normalize_wo` 輸出（可含 None／不相交的單；本函式自己篩）。
       `catalog`＝`EQMT_CATALOG` 形。`calendar`＝見 `_erw_ctx`（None＝整天 raw_only）。
       `now`＝寫者起跑時刻（同一次讀取、同一個 now＝§5.4）。
       `dormant_events`＝A11 事件（None＝過渡橋常數；`[]`＝關）＝`wo_segments_scoped` 的同一個參數。"""
    dw = day_window(day, now)
    empty_footer = {"rows": 0, "clipped": 0, "raw_only": 0, "excluded_emaint_pm": 0,
                    "offcat": 0, "covered": False}
    if dw is None:
        return ([], [], [], empty_footer)
    ds, ws, we = dw
    day_wins = [dw]
    metas, by_eid = {}, {}
    n_emaint = 0
    for rec in work_orders or ():
        if not isinstance(rec, dict) or rec.get("work_order_no") is None:
            continue
        if w8.wo_is_cancelled(rec):               # 取消／作廢不落帳（§5.3）
            continue
        win = w8.wo_active_window(rec, now=now)
        if win is None:
            continue
        a_s = wem._coerce_dt(win[0])
        a_e = wem._coerce_dt(win[1]) if win[1] is not None else now
        if a_s is None or a_e is None or a_e <= ws or a_s >= we:
            continue                               # 與本生產日不相交
        eid = _wcap.fold_seat_id(rec.get("asset_id"))[0]
        area = area_of_eid(eid, catalog)
        wno = str(rec.get("work_order_no"))
        if is_emaint_pm(rec):                      # C-DT14：輸入端濾掉、只留 meta 列
            metas[wno] = _wo_meta(rec, eid, area, now, excluded_reason="emaint_pm")
            n_emaint += 1
            continue
        metas[wno] = _wo_meta(rec, eid, area, now)
        by_eid.setdefault(eid, []).append(rec)

    segs_all = {e: wem.wo_segments(ws_, ws, we) for e, ws_ in by_eid.items()}
    ctx = _erw_ctx(day_wins, calendar, segs_all)
    covered = ctx is not None and ds in (ctx.get("days") or {})
    probe_ctx = ctx if ctx is not None else {"days": {}, "closures": []}

    wo_rows, eid_rows = [], []
    union_all = wem.downtime_by_eid_scoped(by_eid, ws, we, dormant_events=dormant_events)
    for eid in sorted(by_eid):
        wos = by_eid[eid]
        area = area_of_eid(eid, catalog)
        segs = [s for s in wem.wo_segments_scoped(eid, wos, ws, we, dormant_events=dormant_events)
                if s.get("countable", True)]
        bucket = wem.bucket_segments_by_day(segs, day_wins).get(ds) or []
        in_roster = wem.erw_row(eid, {}, day_wins, probe_ctx) is not None
        clip_ok = covered and in_roster
        raw_eid = int(round(union_all.get(eid, {}).get("down_sec", 0.0)))
        er = wem.erw_row(eid, {ds: bucket}, day_wins, ctx) if clip_ok else None
        eid_clip = int(round(er["days"][ds]["stop_h"] * 3600.0)) if er else None
        rw_iv = ([[int(round(lo * 60.0)), int(round(hi * 60.0))] for lo, hi in er["days"][ds]["iv"]]
                 if er else None)
        groups = {}
        for s in bucket:
            groups.setdefault((str(s.get("work_order_no")), s.get("state")), []).append(s)
        n_wo_here = 0
        for (wno, kind), gsegs in sorted(groups.items()):
            ivs = [(s["start"], s["end"]) for s in gsegs]
            raw = int(round(wem._union_seconds(ivs)))
            if raw <= 0:
                continue
            clip = None
            if clip_ok:
                g = wem.erw_row(eid, {ds: gsegs}, day_wins, ctx)
                clip = int(round(g["days"][ds]["stop_h"] * 3600.0))
            wo_rows.append({"day": ds, "eid": eid, "area": area, "wo_no": wno, "kind": kind,
                            "raw_sec": raw, "clipped_sec": clip,
                            "coverage_flag": "clipped" if clip_ok else "raw_only",
                            "in_roster": int(in_roster), "ivs": _rel_ivs(ivs, ws)})
            n_wo_here += 1
        if raw_eid > 0 or n_wo_here:
            eid_rows.append({"day": ds, "eid": eid, "area": area, "raw_sec": raw_eid,
                             "clipped_sec": eid_clip,
                             "coverage_flag": "clipped" if clip_ok else "raw_only",
                             "in_roster": int(in_roster), "rw_iv": rw_iv})
    footer = {"rows": len(wo_rows),
              "clipped": sum(1 for r in wo_rows if r["coverage_flag"] == "clipped"),
              "raw_only": sum(1 for r in wo_rows if r["coverage_flag"] == "raw_only"),
              "excluded_emaint_pm": n_emaint,
              "offcat": len({r["wo_no"] for r in wo_rows if r["area"] == OFFCAT}),
              "covered": bool(covered)}
    return (wo_rows, eid_rows, list(metas.values()), footer)


def a11_since(dormant_events):
    """⑬：A11 長停事件的起算日（最早 effective 日）＋來源。→ `(YYYY-MM-DD|None, 'live'|'bridge')`。
       走 `wdorm.events_for`（唯一匯流口；工位 lane 自動排除），不另解讀事件列。"""
    src = "bridge" if dormant_events is None else "live"
    eids = set()
    for row in wdorm._rows(dormant_events):
        e = row.get("eid") if isinstance(row, dict) else (row[0] if row else None)
        if e:
            eids.add(e)
    first = None
    for e in eids:
        for ev in wdorm.events_for(e, dormant_events):
            d = ev["ts_effective"]
            if first is None or d < first:
                first = d
    return ((first.isoformat() if first else None), src)


# ---------------------------------------------------------------------------
# sqlite
# ---------------------------------------------------------------------------

def ledger_path():
    p = os.environ.get(ENV_LEDGER, "").strip()
    return Path(p) if p else DEFAULT_LEDGER


def connect(path=None, readonly=False):
    p = Path(path) if path else ledger_path()
    if readonly:
        if not p.exists():
            return None
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=30)
        conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def frozen_months(conn):
    return {r["month"] for r in conn.execute("SELECT month FROM month_freeze")}


def set_meta(conn, key, value):
    conn.execute("INSERT INTO ledger_meta(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def write_day(conn, day, wo_rows, eid_rows, meta_rows, footer, computed_at):
    """一天的事實列落帳（交易內）。回 `'written'|'frozen'`。

       · 已凍月（C-DT10）⇒ 零寫入（wo_meta 也不寫；摘要表不在這裡）。
       · 三源涵蓋（footer.covered）⇒ 該日整批替換（兩把尺都重算）。
       · 不涵蓋 ⇒ raw 重算；**A12**：之前落過 clipped 的列 `clipped_sec` 帶原值、flag 仍 clipped；
         之前沒有的列（事後回溯出現的單）＝raw_only＋NULL（C-DT3）⇒ 該日降 raw_only。"""
    if day[:7] in frozen_months(conn):
        return "frozen"
    covered = bool(footer.get("covered"))
    prev_wo, prev_eid = {}, {}
    if not covered:
        for r in conn.execute("SELECT eid, wo_no, kind, clipped_sec, coverage_flag FROM wo_day "
                              "WHERE day=?", (day,)):
            if r["coverage_flag"] == "clipped" and r["clipped_sec"] is not None:
                prev_wo[(r["eid"], r["wo_no"], r["kind"])] = r["clipped_sec"]
        for r in conn.execute("SELECT eid, clipped_sec, coverage_flag, rw_iv FROM eid_day "
                              "WHERE day=?", (day,)):
            if r["coverage_flag"] == "clipped" and r["clipped_sec"] is not None:
                prev_eid[r["eid"]] = (r["clipped_sec"], r["rw_iv"])
    prev_day = conn.execute("SELECT coverage_flag FROM ledger_day WHERE day=?", (day,)).fetchone()
    was_clipped = bool(prev_day and prev_day["coverage_flag"] == "clipped")
    day_flag = "clipped" if covered else "raw_only"
    wo_out, eid_out = [], []
    carried_all = True
    for r in wo_rows:
        r = dict(r)
        if not covered and was_clipped:
            k = (r["eid"], r["wo_no"], r["kind"])
            if k in prev_wo:
                r["clipped_sec"], r["coverage_flag"] = prev_wo[k], "clipped"
            elif r["in_roster"] and r["area"] in BAR_AREAS:
                carried_all = False
        wo_out.append(r)
    for r in eid_rows:
        r = dict(r)
        if not covered and was_clipped and r["eid"] in prev_eid:
            r["clipped_sec"], r["rw_iv"] = prev_eid[r["eid"]]
            r["coverage_flag"] = "clipped"
        eid_out.append(r)
    if not covered and was_clipped and carried_all:
        day_flag = "clipped"                         # A12：算不回來的尺原樣保留
    conn.execute("DELETE FROM wo_day WHERE day=?", (day,))
    conn.execute("DELETE FROM eid_day WHERE day=?", (day,))
    conn.executemany(
        "INSERT INTO wo_day(day,eid,area,wo_no,kind,raw_sec,clipped_sec,coverage_flag,in_roster,ivs)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(r["day"], r["eid"], r["area"], r["wo_no"], r["kind"], r["raw_sec"], r["clipped_sec"],
          r["coverage_flag"], r["in_roster"], json.dumps(r.get("ivs") or [])) for r in wo_out])
    conn.executemany(
        "INSERT INTO eid_day(day,eid,area,raw_sec,clipped_sec,coverage_flag,in_roster,rw_iv)"
        " VALUES(?,?,?,?,?,?,?,?)",
        [(r["day"], r["eid"], r["area"], r["raw_sec"], r["clipped_sec"], r["coverage_flag"],
          r["in_roster"], None if r.get("rw_iv") is None else
          (r["rw_iv"] if isinstance(r["rw_iv"], str) else json.dumps(r["rw_iv"])))
         for r in eid_out])
    cols = ("wo_no", "eid", "area", "work_type", "opened_at", "closed_at", "status", "fault_code",
            "flag_migrated", "flag_oneclick_pm", "flag_long", "span_days", "active_start",
            "active_end", "detail_hash", "fetched_at", "excluded_reason")
    conn.executemany(
        f"INSERT INTO wo_meta({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
        "ON CONFLICT(wo_no) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in cols[1:]),
        [tuple(m.get(c) for c in cols) for m in meta_rows])
    conn.execute("INSERT INTO ledger_day(day, coverage_flag, computed_at, n_rows, schema_version) "
                 "VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET coverage_flag=excluded.coverage_flag,"
                 " computed_at=excluded.computed_at, n_rows=excluded.n_rows,"
                 " schema_version=excluded.schema_version",
                 (day, day_flag, computed_at, len(wo_out), SCHEMA_VERSION))
    return "written"


def freeze_month(conn, month, by_cli="--freeze-month"):
    """凍結＝人明示（C-DT10；記憶 backfill-plan-vs-write-gate）。當月（部分月）不可凍。"""
    today = _dt.datetime.now()
    cur = production_month(today)
    if month >= cur:
        raise ValueError(f"{month} 尚未收完（當月／未來月不可凍；C-MR4 同族）")
    conn.execute("INSERT OR IGNORE INTO month_freeze(month, frozen_at, by_cli) VALUES(?,?,?)",
                 (month, _fmt(today), by_cli))
    conn.commit()


def production_month(now):
    d = now.date() if now.hour >= PROD_DAY_START_H else (now - _dt.timedelta(days=1)).date()
    return f"{d.year:04d}-{d.month:02d}"


def production_day(now):
    d = now.date() if now.hour >= PROD_DAY_START_H else (now - _dt.timedelta(days=1)).date()
    return d.isoformat()


# ---------------------------------------------------------------------------
# I/O 層（CLI）：取數、日曆、鎖、log
# ---------------------------------------------------------------------------

class TruncatedWindow(RuntimeError):
    """active-in 回滿上限＝截斷＝靜默少單 ⇒ 中止、不寫部分月（§5.6）。"""


def load_env_file(path=None):
    """cmms 端點與 token 住 `_hermes.env`（app.py `_load_hermes_env` 同一份、同一個 setdefault 紀律）；
       排程任務的進程沒有 app 幫它載 ⇒ 寫者自己載。token 絕不入 log。"""
    p = Path(path) if path else BASE / "_hermes.env"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def cmms_fetcher(start_dt, end_dt):
    """[start, end) 的活躍工單（normalize 後）。截斷／失敗一律 raise（回填不寫部分月）。"""
    import board_w8_transport as w8t
    load_env_file()
    res, reason = w8t.fetch_active_work_orders(_fmt(start_dt), _fmt(end_dt))
    if res is None:
        raise RuntimeError(f"cmms active-in failed: {reason}")
    if res.get("truncated") or res.get("count", 0) >= LIST_CAP:
        raise TruncatedWindow(f"window {start_dt}..{end_dt} truncated "
                              f"(count={res.get('count')}, list_truncated={res.get('list_truncated')}, "
                              f"detail_truncated={res.get('detail_truncated')})")
    return res.get("work_orders") or []


def fixture_fetcher(path, eid_map=None):
    """fixture（`_fixtures/wo_ledger.v1.json` 形）⇒ fetcher：回與窗相交的單（normalize 過）。
       `eid_map`（選配；預設 None＝照讀）＝{fixture 內 EID: 換成的 EID}：載入時對整份文字做確定性換名
       （公開樹自測的合成 fixture 變體；座號形 `<EID>-N<n>` 的 EID 段一併換、檔案零寫入）。"""
    text = Path(path).read_text(encoding="utf-8")
    if eid_map:
        import re as _re
        text = _re.sub(r"EID-\d{5}", lambda m: eid_map.get(m.group(0), m.group(0)), text)
    doc = json.loads(text)
    wos = [w for w in (w8.normalize_wo(r) for r in doc.get("work_orders") or ()) if w]

    def _f(start_dt, end_dt):
        out = []
        for w in wos:
            win = w8.wo_active_window(w, now=end_dt)
            if win is None:
                continue
            a_e = wem._coerce_dt(win[1]) if win[1] is not None else end_dt
            if wem._coerce_dt(win[0]) < end_dt and a_e > start_dt:
                out.append(w)
        return out
    return _f, doc


def fixture_calendar(doc):
    """fixture 的 `calendar` 節（預解形；slots/mp 走 `wem.demo_erw_inputs`＝既有合成器）。"""
    cal = doc.get("calendar")
    if not cal:
        return None
    cf, ct = wem._coerce_dt(cal["coverage"][0]), wem._coerce_dt(cal["coverage"][1])
    slots, mp = wem.demo_erw_inputs(cf, ct)
    closures = [{"start": wem._coerce_dt(c["start"]), "end": wem._coerce_dt(c["end"]),
                 "uncertain_eids": tuple(c.get("uncertain_eids") or ())}
                for c in cal.get("closures") or ()]
    return {"closures": closures, "coverage": (cf, ct), "slots": slots, "mp": mp}


def live_calendar():
    """live 日曆三源（`_PROD-CALENDAR-LATEST.json`＋兩個綑 raw）；缺＝None（整批 raw_only）。"""
    import board_data as wd
    doc, _st = wd.load_prod_calendar()
    if doc is None:
        return None
    return {"doc": doc, "slots": wd.load_kun_slots()[0], "mp": wd.load_kun_manpower()[0]}


def live_dormant():
    try:
        import board_data as wd
        return wd.get_dormant_events()[0]
    except Exception:
        return None


def acquire_lock(path):
    lp = Path(path).parent / LOCK_NAME
    lp.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lp.exists() and time.time() - lp.stat().st_mtime > LOCK_STALE_SEC:
            lp.unlink()
    except OSError:
        pass
    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, f"{os.getpid()} {_fmt(_dt.datetime.now())}".encode())
    os.close(fd)
    return lp


def log_line(text, log_dir=None):
    if os.environ.get("WO_LEDGER_LOG_ONLY") != "1":      # 排程 wrapper 設 1：只落檔、不重印
        print(text, flush=True)
    d = Path(log_dir) if log_dir else LOG_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        fn = d / f"wo_ledger-{_dt.datetime.now().strftime('%Y%m%d')}.log"
        with open(fn, "a", encoding="utf-8") as f:
            f.write(_dt.datetime.now().strftime("%H:%M:%S") + " " + text + "\n")
    except OSError:
        pass


def month_days(month, now):
    y, m = int(month[:4]), int(month[5:7])
    d = _dt.date(y, m, 1)
    last = production_day(now)
    out = []
    while d.month == m and d.isoformat() <= last:
        out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def months_between(a, b):
    y, m = int(a[:4]), int(a[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def run_days(days, fetcher, calendar, dormant, now, conn, dry_run=False, log_dir=None):
    """`days`（同一窗 ≤31 天）一次取數、逐日計算＋落帳。回摘要 dict。"""
    if not days:
        return {"days": 0}
    if len(days) > MAX_WINDOW_DAYS:
        raise ValueError("window > 31 days (§5.6)")
    t0 = time.monotonic()
    first = day_window(days[0], None)[1]
    last = day_window(days[-1], None)[2]
    if last > now:
        last = now
    wos = fetcher(first, last)
    tot = {"rows": 0, "clipped": 0, "raw_only": 0, "excluded_emaint_pm": 0, "offcat": 0,
           "written": 0, "frozen": 0}
    excl, offc = set(), set()
    for day in days:
        wo_rows, eid_rows, metas, ft = compute_day_rows(day, wos, wem.EQMT_CATALOG, calendar,
                                                        now=now, dormant_events=dormant)
        tot["rows"] += ft["rows"]
        tot["clipped"] += ft["clipped"]
        tot["raw_only"] += ft["raw_only"]
        excl.update(m["wo_no"] for m in metas if m.get("excluded_reason"))
        offc.update(r["wo_no"] for r in wo_rows if r["area"] == OFFCAT)
        if not dry_run:
            st = write_day(conn, day, wo_rows, eid_rows, metas, ft, _fmt(now))
            tot[st] += 1
    if not dry_run:
        conn.commit()
    tot["excluded_emaint_pm"] = len(excl)
    tot["offcat"] = len(offc)
    tot["elapsed"] = round(time.monotonic() - t0, 2)
    tot["n_wo"] = len(wos)
    log_line(f"day={days[0]}..{days[-1]} rows={tot['rows']} clipped={tot['clipped']} "
             f"raw_only={tot['raw_only']} excluded_emaint_pm={tot['excluded_emaint_pm']} "
             f"offcat={tot['offcat']} n_wo={tot['n_wo']} written={tot['written']} "
             f"frozen={tot['frozen']} dry_run={int(dry_run)} elapsed={tot['elapsed']}s", log_dir)
    return tot


def record_a11(conn, dormant):
    since, src = a11_since(dormant)
    set_meta(conn, "a11_since", since or "")
    set_meta(conn, "a11_source", src)
    set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()


def settled_state(conn, now):
    """#166 同形的 settled 規則：最後一個已收盤生產日 D 已經在 D+1 08:25 之後算過、且那天是
       clipped（日曆當天已就緒）⇒ 今天剩下的重試槽不必再打 cmms。"""
    d = (_dt.date.fromisoformat(production_day(now)) - _dt.timedelta(days=1)).isoformat()
    sp = day_window(d, None)[2] + _dt.timedelta(minutes=SETTLED_AFTER_MIN)
    r = conn.execute("SELECT coverage_flag, computed_at FROM ledger_day WHERE day=?", (d,)).fetchone()
    flag, at = (r[0], r[1]) if r else (None, None)
    ok = bool(r and flag == "clipped" and at and wem._coerce_dt(at) >= sp)
    return {"day": d, "flag": flag, "computed_at": at, "settle_point": _fmt(sp), "settled": ok}


def dump_month(conn, month):
    out = {"month": month, "ledger_day": [], "eid_day": [], "wo_day": []}
    for tbl in ("ledger_day", "eid_day", "wo_day"):
        for r in conn.execute(f"SELECT * FROM {tbl} WHERE substr(day,1,7)=? ORDER BY day", (month,)):
            out[tbl].append(dict(r))
    return out


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="工單日帳寫者（SPEC-DOWNTIME §5）")
    ap.add_argument("--day")
    ap.add_argument("--refresh", action="store_true",
                    help="未凍月（ledger 內＋當月）全部日子重算（排程用）")
    ap.add_argument("--backfill", nargs=2, metavar=("FROM_MONTH", "TO_MONTH"))
    ap.add_argument("--freeze-month")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--month")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", help="以 fixture 取代 cmms（離線驗證用；日曆也取 fixture）")
    ap.add_argument("--now", help="覆寫寫者時刻（'YYYY-MM-DD HH:MM:SS'；fixture 驗證用）")
    ap.add_argument("--if-unsettled", action="store_true",
                    help="排程用：最後一個已收盤生產日已在 settle 點後以 clipped 落帳 ⇒ 記一行 skip、零取數")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    now = wem._coerce_dt(a.now) if a.now else _dt.datetime.now().replace(microsecond=0)
    if a.dump:
        conn = connect(readonly=True)
        if conn is None:
            print("no ledger at", ledger_path())
            return 1
        print(json.dumps(dump_month(conn, a.month or production_month(now)),
                         ensure_ascii=False, indent=1))
        return 0
    if a.freeze_month:
        conn = connect()
        freeze_month(conn, a.freeze_month)
        log_line(f"freeze month={a.freeze_month} (human-explicit)")
        return 0
    if a.if_unsettled:
        conn0 = connect(readonly=True)
        if conn0 is not None:
            st = settled_state(conn0, now)
            conn0.close()
            if st["settled"]:
                log_line(f"skip: settled -- {st['day']} already written {st['computed_at']} with "
                         f"coverage={st['flag']} (>= settle point {st['settle_point']}); cmms untouched")
                return 0
    if not (a.day or a.refresh or a.backfill):
        ap.print_help()
        return 2
    if a.fixture:
        fetcher, fdoc = fixture_fetcher(a.fixture)
        calendar, dormant = fixture_calendar(fdoc), fdoc.get("dormant_events", [])
    else:
        fetcher, calendar, dormant = cmms_fetcher, live_calendar(), live_dormant()
    conn = connect()
    lock = None if a.dry_run else acquire_lock(ledger_path())
    if not a.dry_run and lock is None:
        log_line("abort: another ledger writer holds the lock (backfill vs schedule are exclusive)")
        return 3
    try:
        if not a.dry_run:
            record_a11(conn, dormant)
        if a.day:
            try:
                run_days([a.day], fetcher, calendar, dormant, now, conn, a.dry_run)
            except RuntimeError as e:
                conn.rollback()
                log_line(f"ABORT day={a.day}: {e} -- nothing written")
                return 4 if isinstance(e, TruncatedWindow) else 1
        months = []
        if a.backfill:
            months = months_between(a.backfill[0], a.backfill[1])
        elif a.refresh:
            have = {r[0] for r in conn.execute("SELECT DISTINCT substr(day,1,7) FROM ledger_day")}
            have.add(production_month(now))
            months = sorted(have - frozen_months(conn))
        for m in months:
            days = month_days(m, now)
            try:
                run_days(days, fetcher, calendar, dormant, now, conn, a.dry_run)
            except TruncatedWindow as e:
                conn.rollback()
                log_line(f"ABORT month={m}: {e} -- nothing written for this month")
                return 4
            except RuntimeError as e:
                conn.rollback()
                log_line(f"ABORT month={m}: {e} -- nothing written for this month")
                return 1
    finally:
        if lock is not None:
            try:
                lock.unlink()
            except OSError:
                pass
    return 0


# ---------------------------------------------------------------------------
# selftest（fixture；零網路、零 MES、零 _state）
# ---------------------------------------------------------------------------

FIXTURE = BASE / "_fixtures" / "wo_ledger.v1.json"

# 公開樹自測的合成 fixture 變體（R-P11）：fixture 的工單 asset 是私有目錄機台（公開樹裡是對照表換過的編號、合成目錄對不到）
#   ⇒ 依「角色」（目錄 area_key＋是否在該跑窗名冊）從合成目錄選同形機台、載入時換名，逐日計算／落帳／凍結照跑同一組斷言。
#   鍵＝fixture 的 EID 字面（公開樹裡與 fixture 經同一張對照表換名、逐字一致）；私有樹以不計數的 assert 核對本表＝私有目錄實況（防漂移）。
_FIXTURE_ROLES = (
    ("EID-12724", "za", True), ("EID-00425", "za", True), ("EID-13219", "za", True),
    ("EID-25402", "za", True), ("EID-11428", "za", True),
    ("EID-25590", "zb", True), ("EID-26718", "zb", True), ("EID-07349", "zb", False),
    ("EID-36255", "zc", True), ("EID-37932", "zc", True),
)
_FIXTURE_PARENT = "EID-24742"           # 母機（不在目錄、area 由子槽位推）


def _fixture_role_ctx(doc):
    """角色判定的共用素材：(在名冊判定函式, 母機集合, 子機集合)。在名冊＝`wem.erw_row` 對涵蓋日不回 None（＝compute_day_rows 的 in_roster）。"""
    import board_scope as _wsc
    cal = fixture_calendar(doc)
    now = wem._coerce_dt(doc["now"])
    dwin = [day_window(doc["expect"]["parity_day"], now)]
    ctx = _erw_ctx(dwin, cal, {})
    kids = {k for ks in _wsc.PARENT_EXPANSION.values() for k in ks}
    return (lambda e: wem.erw_row(e, {}, dwin, ctx) is not None), set(_wsc.PARENT_EXPANSION), kids


def _pub_fixture_map(doc):
    """合成目錄上為每個角色選一台（目錄序、決定性；排除母機、子機、座號登記機）→ {fixture EID: 合成 EID}；選不到 ⇒ None。"""
    in_roster, parents, kids = _fixture_role_ctx(doc)
    avoid = parents | kids | set(_wcap.NEST_EAV_UNITS) | {ev.get("eid") for ev in doc.get("dormant_events") or ()}
    used, out = set(), {}
    for fx_eid, area, rost in _FIXTURE_ROLES:
        pick = next((e for a, _n, e in wem.EQMT_CATALOG
                     if a == area and e not in used and e not in avoid and in_roster(e) == rost), None)
        if pick is None:
            return None
        used.add(pick)
        out[fx_eid] = pick
    par = next((p for p in sorted(parents) if area_of_eid(p) in BAR_AREAS and in_roster(p)), None)
    if par is None:
        return None
    out[_FIXTURE_PARENT] = par
    return out


def _selftest():
    import tempfile
    n = [0]

    def ck(cond, msg):
        if not cond:
            raise AssertionError(msg)
        n[0] += 1

    fetch, doc = fixture_fetcher(FIXTURE)
    _fx_map = None
    import core_paths as _wp_fx      # 本函式後段另有區域 import _wp（同名在此尚未綁定）⇒ 此處用別名
    if _wp_fx.is_public_tree():
        _fx_map = _pub_fixture_map(doc)
        if _fx_map is not None:
            fetch, doc = fixture_fetcher(FIXTURE, eid_map=_fx_map)
    else:   # 角色表＝私有目錄實況（不計入斷言數；漂移即紅）
        _rin, _pars, _kids = _fixture_role_ctx(doc)
        assert all(area_of_eid(e) == a and _rin(e) == r for e, a, r in _FIXTURE_ROLES) \
            and _FIXTURE_PARENT in _pars and _rin(_FIXTURE_PARENT), "_FIXTURE_ROLES 與私有目錄／名冊不符"
    cal = fixture_calendar(doc)
    dorm = doc.get("dormant_events", [])
    now = wem._coerce_dt(doc["now"])
    exp = doc["expect"]

    # ---- 謂詞與區（C-DT4／C-DT14）----
    ck(is_emaint_pm({"work_type": "PM", "status_history": []}), "emaint pm: PM ∧ history 空")
    ck(is_emaint_pm({"work_type": " pm ", "status_history": None, "closed_at": None}),
       "emaint pm：未結也算（不看結單）")
    ck(not is_emaint_pm({"work_type": "REACTIVE", "status_history": []}), "遷移 REACTIVE 不排除")
    ck(not is_emaint_pm({"work_type": "PM", "status_history": [{"status": "OPEN"}]}),
       "有 history 的 PM 不排除")
    ck(not is_emaint_pm(None), "None 不炸")
    # WP-10d（R-P11）：具名機台由名冊常數推導（私有樹＝首列 EID-12724／za、母機 EID-24742／zc；公開樹＝合成名冊）
    import board_scope as _wsc
    import core_paths as _wp
    _cat = {e: a for a, _n, e in wem.EQMT_CATALOG}
    _e0 = wem.EQMT_CATALOG[0][2]
    _pp = list(_wsc.PARENT_EXPANSION)[0]
    ck(area_of_eid(_e0) == wem.EQMT_CATALOG[0][0], "目錄內＝area_key")
    ck(area_of_eid("EID-99999") == OFFCAT, "目錄外＝__offcat__")
    ck(area_of_eid(_pp) == _cat[_wsc.PARENT_EXPANSION[_pp][0]], "母機（不在目錄）＝子槽位一致的 area_key")
    ck(area_of_eid(_e0, [("zb", "x", _e0)]) == "zb", "catalog 參數生效（不硬編）")
    import ast
    import inspect
    _tree = ast.parse(inspect.getsource(sys.modules[__name__]))
    _names = {getattr(nd, "attr", None) or getattr(nd, "id", None)
              for nd in ast.walk(_tree) if isinstance(nd, (ast.Name, ast.Attribute))}
    ck("_inherited_segments" not in _names, "A2：本檔零 _inherited_segments 呼叫（AST）")
    ck(not ({"_AREA2REGION", "KUN_REGION"} & _names), "A1：本檔零班別區引用（AST）")

    # 逐日計算／落帳／凍結：golden fixture 的 EID 是私有名冊；公開樹用上面的合成 fixture 變體（_fx_map），選不到角色才 skipped（R-P11）
    if not _wp_fx.is_public_tree() or _fx_map is not None:
        # ---- 逐日計算（fixture 全窗）----
        days_all = []
        for m in doc["months"]:
            days_all += month_days(m, now)
        per_day = {}
        for day in days_all:
            wos = fetch(*day_window(day, None)[1:])
            per_day[day] = compute_day_rows(day, wos, wem.EQMT_CATALOG, cal, now=now,
                                            dormant_events=dorm)
        all_wo = [r for d in per_day.values() for r in d[0]]
        all_eid = [r for d in per_day.values() for r in d[1]]
        all_meta = {m["wo_no"]: m for d in per_day.values() for m in d[2]}
        ck(len(doc["months"]) >= 3, "fixture ≥3 個月")
        ck({r["area"] for r in all_wo} >= set(BAR_AREAS), "三區都有列")

        # C-DT14：eMaint PM 零 wo_day 列、有 meta 計數
        epm = str(exp["emaint_pm_wo"])
        ck(all(r["wo_no"] != epm for r in all_wo), "C-DT14：eMaint PM 單零 wo_day 列")
        ck(all_meta.get(epm, {}).get("excluded_reason") == "emaint_pm", "C-DT14：wo_meta 有排除列")
        ck(sum(d[3]["excluded_emaint_pm"] for d in per_day.values()) >= 1, "C-DT14：footer 計數 ≥1")
        # 遷移 REACTIVE：整段算＋旗
        mig = str(exp["migrated_reactive_wo"])
        mig_rows = [r for r in all_wo if r["wo_no"] == mig]
        ck(mig_rows and all_meta[mig]["flag_migrated"] == 1, "遷移 REACTIVE：落列＋flag_migrated")
        ck(abs(sum(r["raw_sec"] for r in mig_rows) - exp["migrated_reactive_raw_sec"]) <= 1,
           f"遷移 REACTIVE 整段算（opened→closed）{sum(r['raw_sec'] for r in mig_rows)}")
        # 長掛單
        lw = str(exp["long_wo"])
        ck(all_meta[lw]["flag_long"] == 1 and all_meta[lw]["span_days"] > LONG_WO_DAYS, "長掛單 >90 天標旗")
        ck(any(r["wo_no"] == lw for r in all_wo), "長掛單預設納入（有列）")
        # 目錄外（C-DT13）
        oc = str(exp["offcat_wo"])
        ck(all(r["area"] == OFFCAT for r in all_wo if r["wo_no"] == oc) and
           any(r["wo_no"] == oc for r in all_wo), "目錄外單落 __offcat__")
        # 名冊外（C-DT13）
        nr = exp["out_of_roster_eid"]
        ck(any(r["eid"] == nr for r in all_eid) and all(r["in_roster"] == 0 and r["clipped_sec"] is None
                                                         for r in all_eid if r["eid"] == nr),
           "名冊外：in_roster=0、clipped NULL（24h 月與 clipped 月一樣不進 bar）")
        # 座單折疊
        seat = exp["seat_parent_eid"]
        ck(any(r["eid"] == seat and r["wo_no"] == str(exp["seat_wo"]) for r in all_wo),
           "座 `-N` 單折回母機（fold_seat_id）")
        # 取消單
        ck(all(r["wo_no"] != str(exp["cancelled_wo"]) for r in all_wo) and
           str(exp["cancelled_wo"]) not in all_meta, "取消單不落帳（連 meta 都沒有）")
        # 跨月單
        xm = str(exp["cross_month_wo"])
        ck(len({r["day"][:7] for r in all_wo if r["wo_no"] == xm}) >= 2, "跨月單落在兩個月")
        # 一鍵結單 PM
        oc_pm = str(exp["oneclick_pm_wo"])
        ck(all_meta[oc_pm]["flag_oneclick_pm"] == 1 and not any(r["wo_no"] == oc_pm for r in all_wo),
           "一鍵結單 PM：0h（零列）＋旗")

        # C-DT3：raw_only 月 clipped 全 NULL；clipped 日有值
        rm, cm = exp["raw_only_month"], exp["clipped_month"]
        ck(all(r["clipped_sec"] is None and r["coverage_flag"] == "raw_only"
               for r in all_wo if r["day"][:7] == rm), "C-DT3：raw_only 月 clipped 全 NULL")
        ck(not any(r["clipped_sec"] == 0 and r["coverage_flag"] == "raw_only" for r in all_wo),
           "C-DT3：raw_only 列永不 0")
        cov_rows = [r for r in all_wo if r["coverage_flag"] == "clipped"]
        ck(cov_rows and all(r["day"][:7] == cm for r in cov_rows), "clipped 列只在涵蓋月")
        ck(all(r["clipped_sec"] is not None and r["clipped_sec"] <= r["raw_sec"] for r in cov_rows),
           "clipped ≤ raw（窗交集）")
        ck(any(r["clipped_sec"] < r["raw_sec"] for r in cov_rows), "有列真的被該跑窗剪短")

        # C-DT5：同機重疊兩單——機台 union < Σ 單張
        ov = exp["overlap_eid"]
        ov_day = exp["overlap_day"]
        ed = next(r for r in per_day[ov_day][1] if r["eid"] == ov)
        sw = sum(r["raw_sec"] for r in per_day[ov_day][0] if r["eid"] == ov)
        ck(ed["raw_sec"] < sw, f"C-DT5：同機重疊 union {ed['raw_sec']} < Σ單 {sw}")
        ck(ed["raw_sec"] == exp["overlap_union_sec"], f"C-DT5：union 秒數 {ed['raw_sec']}")
        # union ＝ 讀端依 ivs 重做 union（⑰ 的原料正確）
        ivs = [(a, b) for r in per_day[ov_day][0] if r["eid"] == ov for a, b in r["ivs"]]
        base = _dt.datetime(2000, 1, 1)
        ck(int(wem._union_seconds([(base + _dt.timedelta(seconds=a), base + _dt.timedelta(seconds=b))
                                   for a, b in ivs])) == ed["raw_sec"], "⑰：ivs 重做 union＝eid_day")
        # 每一台每一天：eid raw ≤ Σ wo raw 且 ≥ max
        ok = True
        for d, (board, er, _m, _f) in per_day.items():
            for e in er:
                parts = [r["raw_sec"] for r in board if r["eid"] == e["eid"]]
                if parts and not (max(parts) <= e["raw_sec"] <= sum(parts)):
                    ok = False
        ck(ok, "每台每日：max(單) ≤ union ≤ Σ單")
        # clipped 日：eid clipped ＝ union(ivs) ∩ rw_iv
        ok = True
        for d, (board, er, _m, _f) in per_day.items():
            for e in er:
                if e["coverage_flag"] != "clipped":
                    continue
                ivs = wem._union_intervals([(base + _dt.timedelta(seconds=a), base + _dt.timedelta(seconds=b))
                                            for r in board if r["eid"] == e["eid"] for a, b in r["ivs"]])
                rw = wem._union_intervals([(base + _dt.timedelta(seconds=a), base + _dt.timedelta(seconds=b))
                                           for a, b in e["rw_iv"]])
                if abs(wem._ivs_seconds(wem._intersect_intervals(ivs, rw)) - e["clipped_sec"]) > 2:
                    ok = False
        ck(ok, "⑰：clipped union＝ivs ∩ rw_iv（讀端重做可還原寫端）")

        # 同源釘（SPEC §13 P1、§9）：名冊內機台的 eid clipped ＝ `/equipment` build_row 格子 stop_h
        peid = exp["parity_eid"]
        pday = exp["parity_day"]
        dwin = [day_window(pday, now)]
        wos_p = [w for w in fetch(dwin[0][1], dwin[0][2])
                 if _wcap.fold_seat_id(w.get("asset_id"))[0] == peid and not is_emaint_pm(w)]
        ctx = _erw_ctx(dwin, cal, {peid: wem.wo_segments(wos_p, dwin[0][1], dwin[0][2])})
        entry = next(c for c in wem.EQMT_CATALOG if c[2] == peid)
        row = wem.build_row(entry, wos_p, dwin, dwin[0][2], "demo", dormant_events=dorm, erw_ctx=ctx)
        mine = next(r for r in per_day[pday][1] if r["eid"] == peid)
        ck(abs(row["erw"]["days"][pday]["stop_h"] * 3600.0 - mine["clipped_sec"]) <= 1,
           f"同源釘：/equipment 格子 {row['erw']['days'][pday]['stop_h']*3600:.0f}s ＝ 日帳 {mine['clipped_sec']}s")

        # 取消單＝積木過濾（不是本檔另寫）：wo_segments 對它回空
        cw = [w for w in fetch(now - _dt.timedelta(days=400), now) if str(w["work_order_no"]) == str(exp["cancelled_wo"])]
        ck(cw and w8.wo_is_cancelled(cw[0]), "取消單經 w8.wo_is_cancelled 判定")

        # ---- sqlite 寫者：凍結／A12／冪等 ----
        tmpd = tempfile.mkdtemp(prefix="wo-ledger-st-")
        dbp = os.path.join(tmpd, "l.sqlite")
        conn = connect(dbp)
        ck(conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal", "WAL")
        for day, (board, er, ms, ft) in per_day.items():
            ck(write_day(conn, day, board, er, ms, ft, _fmt(now)) == "written", f"write {day}") if day == days_all[0] else \
                write_day(conn, day, board, er, ms, ft, _fmt(now))
        conn.commit()
        n_rows = conn.execute("SELECT COUNT(*) FROM wo_day").fetchone()[0]
        ck(n_rows == len(all_wo), "落帳列數＝計算列數")
        # 冪等：同一天重寫不變
        d0 = cm + "-05"
        snap = [tuple(r) for r in conn.execute("SELECT * FROM wo_day WHERE day=? ORDER BY eid,wo_no,kind", (d0,))]
        write_day(conn, d0, *per_day[d0], _fmt(now))
        ck(snap == [tuple(r) for r in conn.execute("SELECT * FROM wo_day WHERE day=? ORDER BY eid,wo_no,kind", (d0,))],
           "同一天重寫＝逐位元相同（冪等）")
        # A12：同一天改以「不涵蓋」重算 ⇒ clipped 原值保留、不變 NULL、不被 raw 覆蓋
        board, er, ms, ft = compute_day_rows(d0, fetch(*day_window(d0, None)[1:]), wem.EQMT_CATALOG,
                                          None, now=now, dormant_events=dorm)
        ck(ft["covered"] is False and all(r["clipped_sec"] is None for r in board), "calendar=None ⇒ 全 raw_only")
        write_day(conn, d0, board, er, ms, ft, _fmt(now))
        after = {(r["eid"], r["wo_no"], r["kind"]): r["clipped_sec"]
                 for r in conn.execute("SELECT * FROM wo_day WHERE day=?", (d0,))}
        before = {(r[1], r[3], r[4]): r[6] for r in snap}
        ck(after == before, "A12：已滾出涵蓋的日子 clipped_sec 保留原值（未改 NULL、未用 raw 覆蓋）")
        ck(conn.execute("SELECT coverage_flag FROM ledger_day WHERE day=?", (d0,)).fetchone()[0] == "clipped",
           "A12：該日尺仍是 clipped")
        ck(all(r["clipped_sec"] is not None for r in conn.execute(
            "SELECT clipped_sec FROM eid_day WHERE day=? AND coverage_flag='clipped'", (d0,))),
           "A12：eid_day 也保留")
        # 凍結：已凍月零寫入
        conn.execute("INSERT INTO month_freeze VALUES(?,?,?)", (rm, "x", "selftest"))
        rm_day = next(d for d in days_all if d[:7] == rm)
        before_n = conn.execute("SELECT COUNT(*) FROM wo_day WHERE substr(day,1,7)=?", (rm,)).fetchone()[0]
        ck(write_day(conn, rm_day, [], [], [], {"covered": False}, "x") == "frozen", "C-DT10：已凍月回 frozen")
        ck(conn.execute("SELECT COUNT(*) FROM wo_day WHERE substr(day,1,7)=?", (rm,)).fetchone()[0] == before_n,
           "C-DT10：已凍月零寫入")
        try:
            freeze_month(conn, production_month(_dt.datetime.now()))
            ck(False, "當月不可凍")
        except ValueError:
            ck(True, "當月不可凍（C-MR4 同族）")
        # run_days 與截斷中止
        tot = run_days([days_all[-1]], fetch, cal, dorm, now, conn, dry_run=True, log_dir=tmpd)
        ck(tot["written"] == 0, "--dry-run 零寫入")

        def _trunc(a, b):
            raise TruncatedWindow("x")
        try:
            run_days([days_all[-1]], _trunc, cal, dorm, now, conn, log_dir=tmpd)
            ck(False, "截斷要 raise")
        except TruncatedWindow:
            ck(True, "截斷 ⇒ TruncatedWindow（回填中止、不寫部分月）")
        try:
            run_days([f"2026-01-{i:02d}" for i in range(1, 32)] + ["2026-02-01"], fetch, cal, dorm, now,
                     conn, dry_run=True, log_dir=tmpd)
            ck(False, ">31 天要 raise")
        except ValueError:
            ck(True, "§5.6：每窗 ≤31 天")
        # 鎖互斥
        lk = acquire_lock(dbp)
        ck(lk is not None and acquire_lock(dbp) is None, "鎖：第二個寫者拿不到（回填與排程互斥）")
        lk.unlink()
        # A11 起算月（⑬）
        s, srcn = a11_since(dorm)
        ck(srcn == "live" and s == exp["a11_since"], f"⑬：A11 起算日 {s}")
        ck(a11_since(None)[1] == "bridge", "⑬：None＝過渡橋")
        record_a11(conn, dorm)
        ck(conn.execute("SELECT value FROM ledger_meta WHERE key='a11_since'").fetchone()[0] == exp["a11_since"],
           "⑬：ledger_meta 落 a11_since")
        # log 行格式
        lf = [p for p in os.listdir(tmpd) if p.startswith("wo_ledger-")]
        txt = open(os.path.join(tmpd, lf[0]), encoding="utf-8").read() if lf else ""
        ck(all(k in txt for k in ("day=", "rows=", "clipped=", "raw_only=", "excluded_emaint_pm=",
                                  "offcat=", "elapsed=")), "log 一行結構化摘要")
        st = settled_state(conn, now)
        ck(st["day"] == "2026-09-09" and st["settled"], f"settled：D=09-09 已於 settle 點後 clipped 落帳 {st}")
        conn.execute("UPDATE ledger_day SET computed_at='2026-09-10 08:10:00' WHERE day='2026-09-09'")
        ck(not settled_state(conn, now)["settled"], "settled：settle 點（08:25）前算的不算")
        ck(not settled_state(conn, now + _dt.timedelta(days=3))["settled"], "settled：D 沒落帳 ⇒ 不 skip")
        conn.close()
    else:
        print("_wo_ledger selftest: skipped: golden fixture EIDs are the private catalog and the synthetic catalog lacks one of the fixture roles; per-day ledger / write_day / freeze / settled sections not run")

    ck(month_days("2026-02", _dt.datetime(2026, 9, 1)) [-1] == "2026-02-28", "month_days 月尾")
    ck(months_between("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"], "months_between")
    ck(production_day(_dt.datetime(2026, 9, 2, 7, 59)) == "2026-09-01", "08:00 生產日界")
    print(f"_wo_ledger selftest: {n[0]} checks PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
