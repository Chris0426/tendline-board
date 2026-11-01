# -*- coding: utf-8 -*-
"""wsynth_tick.py — 合成產線的 tick（SPEC-SYNTHLINE WP-6；裁決 Q11／Q-F／Q-G／Q-L）
======================================================================================

一句話：以**同一個合成世界**（`wsynth_io`，seed＝`wsynth_params`、時鐘＝`wsynth_clock`）產出私機上由
排程探針產的兩個產物，寫進**合成狀態目錄**，再做合成進程的月帳凍結與輪替、最後打第一期頁面暖機：

  步驟（依序；`--steps` 可選子集）
  ------------------------------
  calendar  P1 生產日曆 `board.prod_calendar.v1`：合成原子段（逐 class 點查形）→ **原碼**
            `core_prod_calendar.compute_calendar` → `ok_calendar` 閘 → `core_paths.prod_calendar_path()`
            （＝`board_data.PROD_CALENDAR_PATH` 讀的那一個；`core_prod_calendar.py` 凍結、零改動，
            清冊 §7.1）。**只在事實變了或跨生產日才重寫**（見 `calendar_due`）。
  lastprod  P4 last_production：**原碼** `core_lastprod_push.plan_round` → 合成原子列 → `compose_round`
            （Q-G 等價抽取，`run()` 與本檔共用同一份編排；本檔不另寫第二份）→ 狀態檔
            `core_lastprod_push.state_file_path()`（＝`app.EQM_LP_STATE` 預設讀的那一個）；
            wire payload 寫成 `LASTPROD_PAYLOAD_FILE`（「寫檔」形；**不 POST**，見下）。
  freeze    Q11：合成進程月帳自動凍＋保留 13 個月輪替——**判定與寫入一律走 app 的單一出處**
            `app._mr_freeze_due(SOURCE_SYNTH, today=…)`／`app._mr_synth_rotate()`（WP-4a 已落地的合成分支；
            本檔只負責「每輪問一次」，不另寫凍結判定）。
  warm      Q-L：每輪結束打一次第一期頁面的暖機端點（`WARM_PATHS`；只准 loopback；失敗不算 tick 失敗）。

鐵則（違反即作廢）
------------------
* **只在合成進程跑**（`BOARD_SOURCE=synth`）；啟動走 `wsynth_launch.boot()`（＝絆線：驅動替身先於任何
  `mes_*`、UTC+8、cmms URL 白名單、狀態目錄必須改向到 repo／git 工作樹之外且有 `SYNTH_WORLD.json` 標記，
  空目錄則建標記）。非合成進程、未改向、或 `BOARD_EQM_LP_STATE` 另指他處 ⇒ 拒絕（退出碼 2），不寫任何檔。
* **來源隔離**：每一個寫入點先 `core_source.assert_state_marker()`，再確認目標落在 `BOARD_STATE_DIR` 之下
  （`_guard_write`）；路徑一律取自 `core_paths`／既有模組的路徑函式（不拼 repo 根字面）。
* **所有寫檔走 `board_atomic`**（唯一暫存名＋fsync＋os.replace）；內容沒變就不寫（mtime 不動＝
  `board_data` 以檔案 (mtime,size) 掛在快取鍵上的那些快取不會被白白打掉）。
* **確定性**：同 seed、同凍結時鐘 ⇒ 三個產物逐位元相同；同一時刻重跑＝冪等（不重寫）；時鐘往前只追加
  （前綴穩定由 `wsynth_world` 保證，本檔自測再交叉釘一次）。產生器與本檔都不自己取現在時間——唯一的
  「現在」＝`wsynth_io.now()`（＝`wsynth_clock.now()`，測試以 `BOARD_SYNTH_NOW` 凍結）。
* **零真資料**（C-SY1）：輸入只有合成世界；不讀 `_fixtures`、snapshot、repo 的狀態目錄、綑級 raw、平面圖、
  知識卡。輸出面不帶真實統計（C-SY2a／R-C1～R-C6）：日曆與 payload 的每個數字都出自合成段。
* **不註冊排程、不重啟服務、不 POST**：tick 只是一支模組＋CLI；排程（主機層 timer）由主持人另裁（WP-H）。
  POST 到 sandbox Work 的票證來源未裁（私機的 ingest 票證檔是真 cmms 的票）⇒ 本包只寫檔。

第一期刻意不做（誠實降級，見回報 needs_ruling）
------------------------------------------------
* 站別 lot（v6 `station_lot`，`STATION_LOT_PROXY` 五站）：那五站是 lot 級製程段、合成世界第一期不產
  （WP-11）⇒ 以「查詢未提供」（`STATION_LOT_NOT_PROVIDED`）走 `compose_round` 既有的失敗隔離路徑，
  狀態檔記一句、不主張「掃過且零生產」。
* P1 日曆在 g2 世界上**驗不過**（2026-10-01 實測）：產生器把班段截在 08:00:00（生產日界）上，
  `quorum_days`（含端點）把該刻點亮成「生產日」，而沉默由同一刻起算成整日 closure ⇒ `ok_calendar` 拒收
  「有訊號的日整日被 closure 蓋」。**g3（2026-10-02，WP-6b，主持人明示重置）已在產生器修掉**
  （`wsynth_world.off_day_boundary`：段永不恰好結束在界上）；凍結的 `core_prod_calendar` 未改。
  閘照舊：日曆萬一驗不過仍不寫、步驟回 invalid、退出碼 3；自測在 g3 世界上把「驗不過」直接判 FAIL。
* P6 fleet_map、P7 日帳：仍由 app 進程自己的路徑產（第一期沒有任何第一期頁面會建日帳 ⇒ freeze 步驟在
  第一期通常回「不凍」——那是誠實結果，不是失敗）。

CLI：`python wsynth_tick.py [--steps calendar,lastprod,freeze,warm] [--warm-base URL] [--json] [--selftest]`
退出碼：0＝通過（warm 失敗不算）；2＝拒絕啟動；3＝某個必要步驟失敗（日曆驗不過、last_production 無事實）。
"""
import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import core_source as _wrs
import core_paths as _wp
import board_atomic as _wat
import wsynth_launch as _launch     # 只 import core_source（零 MES）；boot／import_app＝合成進程啟動的唯一順序

REPO = os.path.dirname(os.path.abspath(__file__))

STEP_CALENDAR, STEP_LASTPROD, STEP_FREEZE, STEP_WARM = "calendar", "lastprod", "freeze", "warm"
STEPS = (STEP_CALENDAR, STEP_LASTPROD, STEP_FREEZE, STEP_WARM)
REQUIRED_STEPS = (STEP_CALENDAR, STEP_LASTPROD, STEP_FREEZE)   # 失敗 ⇒ 退出碼 3；warm 永不算失敗

#: 日曆查詢窗（天）＝`core_prod_calendar` live 唯一有實證的窗寬（其檔頭「`--days 30`」）；同窗＝不另立口徑。
CALENDAR_DAYS = 30
#: P4 wire payload 的落點（合成狀態目錄下；「寫檔」形，供主機層日後 POST 或檢視）。
LASTPROD_PAYLOAD_FILE = "synth_last_production.payload.json"
#: 站別 lot 的「未提供」理由（走 `compose_round` 的 `station_lot_err` 既有路徑；不主張零生產）。
STATION_LOT_NOT_PROVIDED = "synth_not_provided: lot-level process segments are phase 2 (WP-11)"

#: 暖機目標＝合成啟動器的預設綁定（loopback）。
DEFAULT_WARM_BASE = "http://%s:%d" % (_launch.DEFAULT_HOST, _launch.DEFAULT_PORT)
WARM_HEALTH_PATH = "/health"
#: 第一期頁面的資料端點（Q-L）。字串＝頁面自己請求的正規形（`warm-board.ps1` 同一條紀律：不帶 source／lang，
#: 雜參數會鑄出新快取鍵）；順序＝私機變更鏈的精神（機況先、稼動頁殿後）。`wsynth_wiring_selftest` (T) 釘
#: 每一條都在合成進程的第一期白名單內且回 200。
WARM_PATHS = (
    "/equipment/body?days=7",
    "/landing2/body",
    "/utilization",
    "/availability",
    "/station",
    "/equipment/health/body",
)
WARM_TIMEOUT_S = 300          # 單一端點（＝`warm-board.ps1` 預設 TimeoutSec）
WARM_HEALTH_TIMEOUT_S = 30
WARM_ROUND_BUDGET_S = 20 * 60

EXIT_OK, EXIT_REFUSED, EXIT_STEP_FAILED = 0, 2, 3
ENV_EQM_LP_STATE = "BOARD_EQM_LP_STATE"     # app 的專屬覆寫（優先於 core_paths）；合成 tick 下必須不設


class TickRefused(RuntimeError):
    """tick 拒絕啟動或拒絕寫入（非合成進程、未改向、寫入點不在合成狀態目錄下）。"""


# =============================================================================
# 啟動與寫入閘
# =============================================================================
def boot(out=print):
    """合成進程檢查＋絆線。→ 絆線摘要；不過 ⇒ `TickRefused`／`wsynth_launch.LaunchError`／`SynthStartupError`。"""
    if not _wrs.is_synth_process():
        raise TickRefused("%s must be %r: the tick only runs as a synthetic process"
                          % (_wrs.ENV_SOURCE, _wrs.SOURCE_SYNTH))
    if not _wp.is_redirected():
        raise TickRefused("%s is not set: synthetic artifacts must not land in the repo state dir"
                          % _wp.ENV_STATE_DIR)
    if str(os.environ.get(ENV_EQM_LP_STATE) or "").strip():
        raise TickRefused("%s is set: the synthetic process must read last_production from the "
                          "redirected state dir only" % ENV_EQM_LP_STATE)
    summary = _launch.boot(out=out)            # check_env → A11 進程內開 → startup_tripwire（含標記）
    _wrs.assert_state_marker()
    return summary


def _state_root():
    root = _wp.env_state_dir()
    if root is None:
        raise TickRefused("%s is not set" % _wp.ENV_STATE_DIR)
    return os.path.normcase(os.path.realpath(root))


def _guard_write(path):
    """寫入點閘：標記檔在場、目標在合成狀態目錄之下、不在 repo 內。→ 正規化後的絕對路徑。"""
    _wrs.assert_state_marker()
    p = os.path.realpath(os.fspath(path))
    root = _state_root()
    pc = os.path.normcase(p)
    if not (pc == root or pc.startswith(root.rstrip("\\/") + os.sep)):
        raise TickRefused("refusing to write outside the synthetic state dir: %s" % p)
    repo = os.path.normcase(os.path.realpath(REPO))
    if pc == repo or pc.startswith(repo.rstrip("\\/") + os.sep):
        raise TickRefused("refusing to write inside the repo: %s" % p)
    return p


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json_if_changed(path, doc):
    """`doc` 與檔內 JSON 相同 ⇒ 不寫（回 "unchanged"）；否則原子寫（`board_atomic` 預設形：indent=1、
    ensure_ascii=False＝`core_lastprod_push._write_state` 同形）→ "written"。"""
    p = _guard_write(path)
    if _read_json(p) == json.loads(json.dumps(doc, ensure_ascii=False)):
        return "unchanged"
    _wat.atomic_write_json(p, doc)
    return "written"


# =============================================================================
# P1 生產日曆
# =============================================================================
def calendar_rows(t, days):
    """合成世界 → `compute_calendar` 吃的列：`(eid, end, start, designation)`（＝`build_lastprod_sql` 去掉
    result 欄的欄序），逐 `EQP_STATIONS` 點查（＝`core_prod_calendar.run_live` 的迴圈）。"""
    import mes_timeline as _mt          # 凍結模組：只讀常數
    import wsynth_io as _io
    return [(s.eid, s.end, s.start, s.designation)
            for cls in _mt.EQP_STATIONS for s in _io.closed_rows(cls, days, t)]


def calendar_facts(doc):
    """日曆裡「消費端會據以改判」的主張：日級布林、closure、休日盤點、尾端沉默在不在。

    刻意不含隨時鐘滑動的三欄：`generated_at`／`coverage.to`（觀測終點＝現在）、`coverage.from`（＝窗起點與
    首訊號取大，窗每一刻都在滑）、`open_silence_from` 的值（＝最新一段的結束時刻）。舊檔的這三欄停在較早的
    時刻＝主張得較少，不是錯（過去的段不會被改寫，C-SY5）；每生產日至少換一版（`calendar_due`）。"""
    d = doc if isinstance(doc, dict) else {}
    return {"days": d.get("days"), "closures": d.get("closures"), "rest_day_audit": d.get("rest_day_audit"),
            "open_tail": "open_silence_from" in d}


def calendar_due(old, new, ok_fn, prod_day_of):
    """純函式：要不要重寫日曆 → `(due, why)`。

    私機的日曆一天只產兩次（08:05／08:20），而 `board_data` 把日曆檔的 (mtime,size) 掛在多個快取鍵上——
    每個 tick 都重寫＝每個 tick 打掉整片快取。所以只在：舊檔缺或驗不過、事實（`calendar_facts`）變了、或
    產生時刻跨進新的生產日（＝私機「每天至少換一版」的節奏；讓 `coverage.to` 至少每天前進）時才寫。"""
    if not isinstance(old, dict):
        return True, "absent"
    try:
        if not ok_fn(old):
            return True, "invalid"
    except Exception:                                   # noqa: BLE001（壞檔＝重寫）
        return True, "invalid"
    if calendar_facts(old) != calendar_facts(new):
        return True, "facts_changed"
    try:
        if prod_day_of(old.get("generated_at")) != prod_day_of(new.get("generated_at")):
            return True, "new_production_day"
    except Exception:                                   # noqa: BLE001
        return True, "invalid"
    return False, "unchanged"


def step_calendar(t, out=print, days=CALENDAR_DAYS):
    import _probe_kun_activity as _ka    # 生產日界唯一出處（core_prod_calendar 同一支）
    import core_prod_calendar as _wpc
    import wsynth_clock as _clk
    win_end = t
    win_start = t - _dt.timedelta(days=days)
    rows = calendar_rows(t, days)
    doc = _wpc.compute_calendar(rows, win_start, win_end, _wpc.to_utc_z(win_end))
    res = {"step": STEP_CALENDAR, "rows": len(rows), "days_claimed": len(doc.get("days") or ()),
           "closures": len(doc.get("closures") or ())}
    if not _wpc.ok_calendar(doc):
        out("[tick/calendar] rows=%d: produced doc failed ok_calendar -> NOT written (the app stays on its "
            "calendar-absent path); on the g2 world the cause was segments ending exactly on the 08:00 "
            "production-day boundary (fixed in the generator from g3)" % res["rows"])
        return dict(res, ok=False, status="invalid")
    path = _wp.prod_calendar_path(repo=REPO)
    due, why = calendar_due(_read_json(path), doc, _wpc.ok_calendar,
                            lambda z: _ka.production_day(_clk.from_utc_z(z)))
    if due:
        p = _guard_write(path)
        _wat.atomic_write_text(p, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")   # ＝run_live 的序列化形
        status = "written"
    else:
        status = "unchanged"
    out("[tick/calendar] rows=%d days=%d closures=%d -> %s (%s)"
        % (res["rows"], res["days_claimed"], res["closures"], status, why))
    return dict(res, ok=True, status=status, why=why)


# =============================================================================
# P4 last_production（Q-G：編排一律走 core_lastprod_push 的純函式）
# =============================================================================
def lastprod_rows(t, days):
    """`[(class, rows), …]`，逐 `lastprod_scan_classes()` 點查；列形＝`build_lastprod_sql` 全欄
    `(eid, end, start, designation, result)`（台北牆鐘 naive）。"""
    import core_lastprod_push as _lp
    import wsynth_io as _io
    return [(cls, [(s.eid, s.end, s.start, s.designation, s.result) for s in _io.closed_rows(cls, days, t)])
            for cls in _lp.lastprod_scan_classes()]


def nest_rows(t, days):
    """形① `[((class, var), rows), …]`，逐 `nest_query_pairs()`；列形＝`build_nest_sql`
    `(eid, end, start, designation, nest)`。持有 `var` 的機台＝名冊常數 `NEST_EAV_UNITS`（同 `wsynth_io`）。"""
    import board_capacity as _wcap
    import core_lastprod_push as _lp
    import wsynth_io as _io
    out = []
    for cls, var in _lp.nest_query_pairs():
        holders = {e for e, rec in _wcap.NEST_EAV_UNITS.items() if rec.get("var") == var}
        rows = []
        for s in _io.closed_rows(cls, days, t):
            n = _io.nest_number(s.unit)
            if s.eid in holders and n is not None:
                rows.append((s.eid, s.end, s.start, s.designation, n))
        out.append(((cls, var), rows))
    return out


def step_lastprod(t, out=print):
    import core_lastprod_push as _lp
    import wsynth_io as _io
    now_utc = _lp.taipei_to_utc_dt(t)
    state_path = _guard_write(_lp.state_file_path())
    lookback, refresh = _lp.DEFAULT_LOOKBACK_D, _lp.DEFAULT_REFRESH_D
    plan = _lp.plan_round(_lp._read_raw_state(state_path), now_utc, lookback, refresh,
                          scope=_lp.DEFAULT_SCOPE)
    for line in plan["logs"]:
        out(line)
    days = plan["days"]
    known = {a["asset_id"] for a in _io.world(t).ctx.fleet}     # 合成世界的設備主檔＝機隊定義
    rnd = _lp.compose_round(plan, now_utc, lookback, _lp.DEFAULT_SCOPE, lastprod_rows(t, days),
                            nest_rows_by_pair=nest_rows(t, days),
                            station_lot_rows_by_cls=None, station_lot_err=STATION_LOT_NOT_PROVIDED,
                            known=known, with_designation=True, with_result=True)
    for line in rnd["logs"]:
        out(line)
    res = {"step": STEP_LASTPROD, "window_days": days, "incremental": bool(plan["incremental"]),
           "scanned": rnd["scanned"], "rows": rnd["n_rows"]}
    if rnd["exit"] is not None:
        return dict(res, ok=False, status="no_facts", exit=rnd["exit"])
    st = "partial_not_written" if rnd["new_state"] is None else _write_json_if_changed(state_path, rnd["new_state"])
    pl = _write_json_if_changed(_wp.state_path(LASTPROD_PAYLOAD_FILE, repo=REPO), rnd["payload"])
    out("[tick/lastprod] window=%dd scanned=%d rows=%d items=%d partial=%s -> state %s, payload %s"
        % (days, rnd["scanned"], rnd["n_rows"], rnd["diag"]["n_items"], rnd["partial"], st, pl))
    return dict(res, ok=True, status=st, payload=pl, partial=bool(rnd["partial"]),
                items=rnd["diag"]["n_items"])


# =============================================================================
# Q11 月帳自動凍＋輪替（判定與寫入＝app 的單一出處）
# =============================================================================
def step_freeze(t, out=print):
    app = _launch.import_app(out=out)
    _wrs.assert_state_marker()
    month, written, why = app._mr_freeze_due(_wrs.SOURCE_SYNTH, today=t.date().isoformat())
    gone = app._mr_synth_rotate()
    out("[tick/freeze] month=%s written=%s why=%s rotated=%s" % (month, written, why, gone))
    return {"step": STEP_FREEZE, "ok": not str(why).startswith("error"), "month": month,
            "written": bool(written), "why": why, "rotated": list(gone)}


# =============================================================================
# Q-L 暖機（只准 loopback；best-effort）
# =============================================================================
def _no_proxy_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(opener, url, timeout):
    """→ "OK <status>"／"ERR <類別>"。整個回應讀完（伺服器那邊照樣算完、填快取）。"""
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            r.read()
            return "OK %d" % r.status
    except urllib.error.HTTPError as e:
        return "OK %d" % e.code
    except Exception as e:                              # noqa: BLE001（暖機永不往外丟）
        return "ERR %s" % type(e).__name__


def step_warm(base=DEFAULT_WARM_BASE, out=print, paths=WARM_PATHS, timeout_s=WARM_TIMEOUT_S,
              budget_s=WARM_ROUND_BUDGET_S, opener=None):
    res = {"step": STEP_WARM, "ok": True, "base": base, "results": []}
    parts = urllib.parse.urlsplit(str(base or ""))
    if parts.scheme != "http" or not _wrs.is_loopback_host(parts.hostname or ""):
        out("[tick/warm] refused: warm base must be http://<loopback> (got %r)" % (base,))
        return dict(res, status="refused_non_loopback")
    op = opener or _no_proxy_opener()
    root = "%s://%s" % (parts.scheme, parts.netloc)
    h = _get(op, root + WARM_HEALTH_PATH, WARM_HEALTH_TIMEOUT_S)
    if h != "OK 200":
        out("[tick/warm] health -> %s; skipping this round" % h)
        return dict(res, status="service_down", health=h)
    t0 = time.monotonic()
    for p in paths:
        left = budget_s - (time.monotonic() - t0)
        if left <= 0:
            out("[tick/warm] round budget exhausted; skipping the remaining endpoint(s)")
            break
        r = _get(op, root + p, max(1.0, min(float(timeout_s), left)))
        res["results"].append([p, r])
        out("[tick/warm] GET %s -> %s" % (p, r))
    return dict(res, status="done", health=h)


# =============================================================================
# 編排
# =============================================================================
def parse_steps(spec):
    if spec is None:
        return STEPS
    got = [s.strip() for s in str(spec).split(",") if s.strip()]
    bad = [s for s in got if s not in STEPS]
    if bad or not got:
        raise ValueError("unknown or empty --steps: %s (choose from %s)" % (bad or spec, ",".join(STEPS)))
    return tuple(s for s in STEPS if s in got)             # 永遠照固定順序跑（warm 殿後）


def run_tick(steps=STEPS, warm_base=DEFAULT_WARM_BASE, out=print):
    """已通過 `boot()` 的合成進程內跑一輪。→ 摘要 dict（`rc`、`now`、`results`）。"""
    import wsynth_io as _io                 # 合成進程限定（非合成進程 import 會被拒）
    t = _io.now()
    results = []
    for s in steps:
        if s == STEP_CALENDAR:
            results.append(step_calendar(t, out))
        elif s == STEP_LASTPROD:
            results.append(step_lastprod(t, out))
        elif s == STEP_FREEZE:
            results.append(step_freeze(t, out))
        elif s == STEP_WARM:
            results.append(step_warm(warm_base, out))
    failed = [r["step"] for r in results if r["step"] in REQUIRED_STEPS and not r.get("ok")]
    return {"rc": EXIT_STEP_FAILED if failed else EXIT_OK, "now": t.isoformat(sep=" "),
            "world": _io.cache_info().get("world_id"), "failed": failed, "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Synthetic line tick (BOARD_SOURCE=synth): production calendar, "
                                             "last_production, monthly freeze/rotation, page warm-up")
    ap.add_argument("--steps", help="comma list from %s (default: all, in that order)" % ",".join(STEPS))
    ap.add_argument("--warm-base", default=DEFAULT_WARM_BASE, help="loopback base URL of the synthetic server")
    ap.add_argument("--json", action="store_true", help="print the round summary as one JSON line")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    try:
        steps = parse_steps(a.steps)
    except ValueError as e:
        print("[wsynth_tick] REFUSED: %s" % e)
        return EXIT_REFUSED
    t0 = time.perf_counter()
    try:
        boot()
    except (TickRefused, _launch.LaunchError, _wrs.SynthStartupError) as e:
        if getattr(e, "summary", None):
            for line in _wrs.format_summary(e.summary):
                print(line)
        print("[wsynth_tick] REFUSED: %s" % e)
        return EXIT_REFUSED
    try:
        summ = run_tick(steps, a.warm_base)
    except TickRefused as e:
        print("[wsynth_tick] REFUSED: %s" % e)
        return EXIT_REFUSED
    print("[wsynth_tick] now=%s world=%s steps=%s failed=%s %.1fs"
          % (summ["now"], summ["world"], ",".join(steps), summ["failed"] or "-", time.perf_counter() - t0))
    if a.json:
        print(json.dumps(summ, ensure_ascii=False, sort_keys=True, default=str))
    return summ["rc"]


# ============================================================================ selftest ===
#   以下只給 `--selftest`。上半部不依賴本節。父進程（非合成）只排程子進程與比對；凡是會 import
#   `wsynth_io`／`core_lastprod_push`（→ MES 模組）的事，一律在合成子進程裡做（驅動替身先於 MES 模組）。
# =========================================================================================
_SELFTEST_MARK = "# ============================================================================ selftest ==="
_CHILD_MARK = "@@WSYNTH_TICK@@"
_TAIL = 4000

_CHILD = r'''
import sys, os, io, json, builtins, traceback, datetime as _d
TREE, SD, ARG = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
sys.path.insert(0, TREE)
R = {"opened": [], "writes": [], "fsops": []}
_bopen = builtins.open
def _rec(file, mode="r", *a, **k):
    try:
        p = None if isinstance(file, int) else os.path.abspath(os.fspath(file))
    except Exception:
        p = None
    if p:
        R["opened"].append(p)
        if any(ch in str(mode) for ch in "wax+"):
            R["writes"].append(p)
    return _bopen(file, mode, *a, **k)
builtins.open = _rec
io.open = _rec
for _nm in ("replace", "rename", "remove", "unlink", "mkdir", "makedirs", "rmdir"):
    _orig = getattr(os, _nm)
    def _w(*a, _o=_orig, _n=_nm, **k):
        try:
            R["fsops"].append([_n, os.path.abspath(os.fspath(a[-1] if _n in ("replace", "rename") else a[0]))])
        except Exception:
            pass
        return _o(*a, **k)
    setattr(os, _nm, _w)
import wsynth_tick as K
lines = []
try:
    R["rc"] = K.main(ARG.get("argv", []) + ["--json"]) if ARG.get("cli") else None
    if not ARG.get("cli"):
        K.boot(out=lines.append)
        R["summary"] = K.run_tick(K.parse_steps(ARG.get("steps")), ARG.get("warm_base") or K.DEFAULT_WARM_BASE,
                                  out=lines.append)
except SystemExit as e:
    R["rc"] = e.code
except Exception as e:
    R["exc"] = "%s: %s" % (type(e).__name__, e)
    R["tb"] = traceback.format_exc()[-3000:]
R["lines"] = lines[-80:]
R["app_loaded"] = "app" in sys.modules
R["mes_loaded"] = sorted(m for m in sys.modules if m.startswith("mes_"))
R["wsynth_loaded"] = sorted(m for m in sys.modules if m.startswith("wsynth_"))
drv = sys.modules.get("pyodbc")
R["driver_stub"] = bool(drv is None or drv.__dict__.get("__core_source_stub__"))
X = ARG.get("extra") or []
if R.get("summary") and not R.get("exc"):
    try:
        import core_paths as P
        import core_lastprod_push as LP
        import core_prod_calendar as WPC
        import wsynth_io as IO
        import wsynth_world as W
        import mes_timeline as MT
        T = IO.now()
        files = {"calendar": P.prod_calendar_path(repo=TREE), "state": LP.state_file_path(),
                 "payload": P.state_path(K.LASTPROD_PAYLOAD_FILE, repo=TREE)}
        import hashlib
        R["files"] = {}
        for k, p in files.items():
            if os.path.exists(p):
                with _bopen(p, "rb") as f:
                    b = f.read()
                R["files"][k] = [hashlib.sha256(b).hexdigest(), os.stat(p).st_mtime_ns, p]
                if k == "payload":
                    _pl = json.loads(b.decode("utf-8"))
                    R["payload_rebuild"] = _pl.pop("full_window_rebuild", None)
                    R["payload_core_sha"] = hashlib.sha256(json.dumps(_pl, sort_keys=True).encode()).hexdigest()
        if "xcheck" in X:
            # 獨立來源：產生器自己的曝光（build_world），不經 wsynth_io 的查詢碼
            world = W.build_world(T)
            segs = [(_d.datetime.fromisoformat(s["start"]), _d.datetime.fromisoformat(s["end"]), s["eid"],
                     s["cls"], s["designation"]) for s in world["segments"]]
            fleet = {a["asset_id"] for a in world["ctx"].fleet}
            # P1：同一個純函式吃「tick 的列」與「獨立列」⇒ 逐欄相等（日曆 quorum 由合成段算出）
            lo = T - _d.timedelta(days=K.CALENDAR_DAYS)
            st_set = set(MT.EQP_STATIONS)
            rows = [(e, en, s, dg) for s, en, e, c, dg in segs if c in st_set and lo <= s <= T and s <= en <= T]
            ref = WPC.compute_calendar(rows, lo, T, WPC.to_utc_z(T))
            mine = WPC.compute_calendar(K.calendar_rows(T, K.CALENDAR_DAYS), lo, T, WPC.to_utc_z(T))
            R["x_cal_io_equal_ref"] = (mine == ref)
            R["x_cal_valid"] = WPC.ok_calendar(ref)
            R["x_cal_file_exists"] = os.path.exists(files["calendar"])
            if R["x_cal_file_exists"]:
                R["x_cal_file_equal"] = json.loads(_bopen(files["calendar"], encoding="utf-8").read()) == ref
            R["x_cal_audit_eids_in_fleet"] = all(s["eid"] in fleet for a in ref["rest_day_audit"] for s in a["segments"])
            R["x_cal_n_days"] = len(ref["days"])
            R["x_cal_days"] = ref["days"]
            if not R["x_cal_valid"]:
                # 診斷（只在自測、不進任何產物）：把「恰好結束在生產日界 08:00:00」的段往前推 1 秒後重算——
                # 過閘 ⇒ 唯一的不變式破口＝生產日界端點（quorum 含端點 vs closure 由該刻起算）。g2 的已知破口；
                # g3（WP-6b）已在產生器避開 ⇒ 走到這裡＝回歸，診斷值隨 (cal3) FAIL 印出。
                H = WPC.ka.PROD_DAY_START_H
                def _at_bd(x):
                    return x.hour == H and x.minute == 0 and x.second == 0
                one = _d.timedelta(seconds=1)
                nud = [(e, en - one if _at_bd(en) else en, (s - one) if (_at_bd(en) and s == en) else s, dg)
                       for e, en, s, dg in rows]
                R["x_cal_n_boundary_rows"] = sum(1 for e, en, s, dg in rows if _at_bd(en))
                R["x_cal_nudged_ok"] = WPC.ok_calendar(WPC.compute_calendar(nud, lo, T, WPC.to_utc_z(T)))
            # P4：payload 每台末段 ≡ 同一批段（獨立列、fold_eid）的 max end
            stt = json.loads(_bopen(files["state"], encoding="utf-8").read())
            pl = json.loads(_bopen(files["payload"], encoding="utf-8").read())
            cov = LP.parse_utc_z(stt["covered_from"]) + _d.timedelta(hours=8)
            scan = set(LP.lastprod_scan_classes())
            mx = {}
            for s, en, e, c, dg in segs:
                if c in scan and cov <= s <= T and s <= en <= T:
                    fe = MT.fold_eid(e)
                    if fe is not None and (fe not in mx or en > mx[fe]):
                        mx[fe] = en
            cut = T - _d.timedelta(days=LP.DEFAULT_LOOKBACK_D)
            bad = []
            for it in pl["items"]:
                exp = mx.get(it["asset_id"])
                exp_z = None if exp is None or exp < cut else LP.to_utc_z(exp)
                if it["last_segment_end"] != exp_z:
                    bad.append([it["asset_id"], it["last_segment_end"], exp_z])
            roster = set(LP.roster_eids())
            R["x_lp_bad"] = bad[:10]
            R["x_lp_n_items"] = len(pl["items"])
            R["x_lp_n_with_end"] = sum(1 for it in pl["items"] if it["last_segment_end"])
            R["x_lp_items_known"] = all(it["asset_id"] in roster or it["asset_id"] in fleet for it in pl["items"])
            R["x_lp_observed_at"] = pl["observed_at"] == LP.to_utc_z(T)
            R["x_lp_station_lot_err"] = stt.get("station_lot_err")
            R["x_lp_state_eids"] = {k: (v.get("end") if isinstance(v, dict) else v) for k, v in stt["eids"].items()}
    except Exception as e:
        R["xexc"] = "%s: %s" % (type(e).__name__, e)
        R["xtb"] = traceback.format_exc()[-3000:]
if "rotation" in X and R.get("summary") and not R.get("exc"):
    try:
        import app as A
        import wsynth_io as IO
        snap = A._MR_SNAP_DIR
        os.makedirs(snap, exist_ok=True)
        import board_monthly as WMN
        m0 = WMN.month_of(IO.now().date().isoformat())
        ms = [WMN.month_add(m0, -k) for k in range(2, 15)]          # 13 份合成快照（不含上個月＝凍結判定不受影響）
        for m in ms:
            with _bopen(os.path.join(snap, m + ".json"), "w", encoding="utf-8") as f:
                f.write('{"source": "synth"}')
        old_ns = WMN.month_add(m0, -40)
        with _bopen(os.path.join(snap, old_ns + ".json"), "w", encoding="utf-8") as f:
            f.write("{}")                                         # 非合成快照：不列、不輪替
        r13 = K.step_freeze(IO.now(), out=lines.append)
        left13 = sorted(n[:-5] for n in os.listdir(snap) if n.endswith(".json"))
        extra = WMN.month_add(m0, -15)
        with _bopen(os.path.join(snap, extra + ".json"), "w", encoding="utf-8") as f:
            f.write('{"source": "synth"}')
        r14 = K.step_freeze(IO.now(), out=lines.append)
        left14 = sorted(n[:-5] for n in os.listdir(snap) if n.endswith(".json"))
        R["rot"] = {"m0": m0, "seed": ms, "ns": old_ns, "extra": extra, "r13": r13, "left13": left13,
                    "r14": r14, "left14": left14}
    except Exception as e:
        R["rexc"] = "%s: %s" % (type(e).__name__, e)
        R["rtb"] = traceback.format_exc()[-3000:]
if "warmstub" in X:
    try:
        import http.server, threading
        got = []
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                got.append(self.path)
                code = 500 if (ARG.get("health_down") and self.path == "/health") else 200
                self.send_response(code); self.send_header("Content-Length", "2"); self.end_headers()
                self.wfile.write(b"ok")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        R["warm_up"] = K.step_warm(base, out=lines.append)
        R["warm_got"] = list(got)
        ARG["health_down"] = True
        del got[:]
        R["warm_down"] = K.step_warm(base, out=lines.append)
        R["warm_got_down"] = list(got)
        R["warm_nonloop"] = K.step_warm("http://example.invalid:8050", out=lines.append)
        srv.shutdown(); srv.server_close()
    except Exception as e:
        R["wexc"] = "%s: %s" % (type(e).__name__, e)
print("@@WSYNTH_TICK@@" + json.dumps(R, default=str))
'''


def _child_env(over):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("BOARD_") and not k.startswith("LLM_") and not k.startswith("IMONNIT_")
           and k not in ("CMMS_Board_INGEST_TOKEN",)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k, v in over.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def _run_child(sd, arg, env_over, timeout=1500):
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="wsynth_tick_cwd_") as cwd:
        r = subprocess.run([sys.executable, "-c", _CHILD, REPO, sd or "", json.dumps(arg)],
                           env=_child_env(env_over), cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    for line in reversed((r.stdout or "").splitlines()):
        if line.startswith(_CHILD_MARK):
            res = json.loads(line[len(_CHILD_MARK):])
            res["_stdout"] = (r.stdout or "")[-_TAIL:]
            return res
    raise AssertionError("child failed rc=%s\nstdout=%s\nstderr=%s"
                         % (r.returncode, (r.stdout or "")[-_TAIL:], (r.stderr or "")[-_TAIL:]))


def _repo_fingerprint():
    out = {}
    for dp, dns, fns in os.walk(REPO):
        dns[:] = [d for d in dns if d not in (".git", ".claude", "__pycache__")]
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, REPO)] = (st.st_size, st.st_mtime_ns)
    return out


def _under(p, root):
    pc, rc = os.path.normcase(os.path.realpath(p)), os.path.normcase(os.path.realpath(root))
    return pc == rc or pc.startswith(rc.rstrip("\\/") + os.sep)


def _selftest():
    import re
    import shutil
    import tempfile
    import wsynth_params as _P          # 純常數模組（零 I/O、零 MES）
    t_all = time.time()
    n, fails = [0], []

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            fails.append(msg)
            print("FAIL [%d] %s" % (n[0], msg), flush=True)
        else:
            print("ok   [%d] %s" % (n[0], msg), flush=True)

    # ── (0) 原始碼潔淨（上半部）：無機台編號、站名、工單號字面；無狀態目錄／日曆檔名字面 ────────────
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
        src = f.read().split(_SELFTEST_MARK)[0]
    ck(not re.search(r"EID-\d{5}", src) and not re.search(r"\bseg[A-Z]\w+", src)
       and not re.search(r"\b7[1-9]\d{4}\b", src),
       "(0a) module source has no EID / station-class / work-order literals")
    ck(('"%s"' % _wp.STATE_DIRNAME) not in src and _wp.PROD_CALENDAR_FILE not in src,
       "(0b) module source builds no state-dir / calendar path literal (paths come from core_paths)")
    ck(parse_steps(None) == STEPS and parse_steps("warm,calendar") == (STEP_CALENDAR, STEP_WARM),
       "(0c) steps run in the fixed order (warm last) whatever order is given")
    try:
        parse_steps("calendar,bogus")
        ck(False, "(0d) unknown step refused")
    except ValueError:
        ck(True, "(0d) unknown step refused")
    ck(all(p.startswith("/") and "source=" not in p and "lang=" not in p for p in WARM_PATHS)
       and WARM_PATHS == tuple(dict.fromkeys(WARM_PATHS)),
       "(0e) warm paths are canonical (no source/lang, no duplicates)")

    # ── (0f) `calendar_due` 純函式（何時重寫日曆）─────────────────────────────────────────
    import wsynth_clock as _clk
    pd = (lambda z: (_clk.from_utc_z(z) - _dt.timedelta(hours=_P.PROD_DAY_START_H)).date())
    okf = (lambda doc: isinstance(doc, dict) and doc.get("schema_id") == "x")
    base = {"schema_id": "x", "generated_at": "2026-01-05T02:00:00Z", "coverage": {"from": "a", "to": "b"},
            "days": [{"date": "2026-01-04", "production": True}], "closures": [], "rest_day_audit": [],
            "open_silence_from": "2026-01-05T01:00:00Z"}
    later = dict(base, generated_at="2026-01-05T09:00:00Z", coverage={"from": "a2", "to": "b2"},
                 open_silence_from="2026-01-05T08:59:00Z")
    ck(calendar_due(None, base, okf, pd) == (True, "absent")
       and calendar_due({"schema_id": "bad"}, base, okf, pd) == (True, "invalid"),
       "(0f) calendar_due: absent / invalid on disk -> write")
    ck(calendar_due(base, later, okf, pd) == (False, "unchanged"),
       "(0g) calendar_due: only the clock-sliding fields moved (generated_at, coverage, open_silence_from value) "
       "within the same production day -> no rewrite (cache stamps keep)")
    ck(calendar_due(base, dict(later, closures=[{"start": "s", "end": "e"}]), okf, pd)[1] == "facts_changed"
       and calendar_due(base, {k: v for k, v in later.items() if k != "open_silence_from"}, okf, pd)[1]
       == "facts_changed",
       "(0h) calendar_due: a new closure / the open tail sealing -> rewrite")
    ck(calendar_due(base, dict(later, generated_at="2026-01-06T00:30:00Z"), okf, pd) == (True, "new_production_day"),
       "(0i) calendar_due: first tick of a new production day -> rewrite even with identical facts")

    # 凍結時刻（由參數推得、不寫日期字面）
    T1 = _dt.datetime.combine(_P.HORIZON_START + _dt.timedelta(days=60), _dt.time(14, 37))
    T2 = T1 + _dt.timedelta(hours=26)                           # 隔一個生產日（同目錄＝增量輪）
    d = _P.HORIZON_START + _dt.timedelta(days=40)
    first = _dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    TM = _dt.datetime.combine(first + _dt.timedelta(days=1), _dt.time(9, 10))   # 跨月：月初第 2 天 09:10
    root = tempfile.mkdtemp(prefix="wsynth_tick_st_")
    fp0 = _repo_fingerprint()
    live_paths = (os.path.join(REPO, _wp.STATE_DIRNAME, "last_production.json"),
                  os.path.join(REPO, _wp.PROD_CALENDAR_FILE),
                  os.path.join(REPO, _wp.STATE_DIRNAME, LASTPROD_PAYLOAD_FILE))
    live0 = {p: (os.stat(p).st_mtime_ns if os.path.exists(p) else None) for p in live_paths}
    forbidden = ("_fixtures", "_kb_a", "_kb_b", "_KUN-", "layout_eid_xy.json", "snapshot",
                 _wp.PROD_CALENDAR_FILE, os.sep + _wp.STATE_DIRNAME + os.sep)

    def synth_env(sd, now):
        return {"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": sd, "BOARD_SYNTH_NOW": now.isoformat()}

    def isolation(tag, res, sd):
        w = [p for p in res.get("writes", []) if not _under(p, sd)]
        fs = [x for x in res.get("fsops", []) if not _under(x[1], sd)]
        ck(not w and not fs, "(%s-iso) every write/replace/mkdir lands under the synthetic state dir: %s %s"
           % (tag, w[:3], fs[:3]))
        bad = [p for p in res.get("opened", []) if _under(p, REPO)
               and any(part in os.sep + os.path.relpath(p, REPO) for part in forbidden)]
        ck(not bad, "(%s-c-sy1) no fixture / real-data / repo-state file opened: %s" % (tag, bad[:3]))
        ck(res.get("driver_stub") is True, "(%s-stub) database driver is the tripwire stub" % tag)

    # ── (A) 確定性＋交叉釘（T1、全新目錄）────────────────────────────────────────────────
    sdA, sdB = os.path.join(root, "A"), os.path.join(root, "B")
    def cal_case(tag, res, written_status=("written",)):
        """日曆：驗得過 ⇒ 落檔且＝獨立重算；驗不過 ⇒ 不落檔、步驟回 invalid、rc 3（閘的行為照釘），但 g3 世界上
        驗不過本身就是 FAIL（g2 的唯一破口＝段恰好結束在生產日界 08:00:00，g3 產生器已避開）。→ 期望的 rc。"""
        cs = {r["step"]: r for r in (res.get("summary") or {}).get("results", [])}.get(STEP_CALENDAR, {})
        ck(res.get("x_cal_io_equal_ref") is True and (res.get("x_cal_n_days") or 0) >= 20,
           "(%s-cal1) P1: compute_calendar over the tick's rows == over independently exposed segments (generator, "
           "not wsynth_io) — quorum comes from the synthetic segments; %s days claimed" % (tag, res.get("x_cal_n_days")))
        if res.get("x_cal_valid"):
            ck(cs.get("status") in written_status and res.get("x_cal_file_exists") is True
               and res.get("x_cal_file_equal") is True,
               "(%s-cal2) calendar validates -> written to the redirected path and == the recomputation: %s"
               % (tag, cs.get("status")))
            return EXIT_OK
        ck(cs.get("status") == "invalid" and cs.get("ok") is False and res.get("x_cal_file_exists") is False,
           "(%s-cal2) calendar fails ok_calendar -> NOT written, step reports invalid: %s" % (tag, cs))
        # g3（WP-6b）：產生器已保證段不結束在生產日界 ⇒ 日曆必須驗得過；走到這裡＝回歸，直接紅（診斷照印）
        ck(False, "(%s-cal3) g3 world: the synthetic calendar must validate (generator keeps every segment off the "
           "08:00 production-day boundary); boundary rows=%s, nudged-1s validates=%s"
           % (tag, res.get("x_cal_n_boundary_rows"), res.get("x_cal_nudged_ok")))
        return EXIT_STEP_FAILED

    a1 = _run_child(sdA, {"steps": "calendar,lastprod", "extra": ["xcheck"]}, synth_env(sdA, T1))
    ck(not a1.get("exc") and not a1.get("xexc") and a1.get("summary"),
       "(A1) tick at T1 in a fresh state dir ran (%s %s %s)" % (a1.get("exc"), a1.get("xexc"), a1.get("tb", "")[-800:]))
    isolation("A1", a1, sdA)
    ck(os.path.isfile(os.path.join(sdA, _wrs.MARKER_FILE)), "(A2) empty state dir adopted: SYNTH_WORLD.json marker created")
    exp_rc = cal_case("A", a1)
    ck((a1.get("summary") or {}).get("rc") == exp_rc, "(A3) round exit code %s == expected %s"
       % ((a1.get("summary") or {}).get("rc"), exp_rc))
    ck({"state", "payload"} <= set(a1.get("files", {})) and all(_under(v[2], sdA) for v in a1["files"].values()),
       "(A3b) last_production state + payload (and the calendar when valid) under the state dir: %s"
       % sorted(a1.get("files", {})))
    st = {r["step"]: r for r in (a1.get("summary") or {}).get("results", [])}
    ck(st.get("lastprod", {}).get("status") == "written" and st.get("lastprod", {}).get("payload") == "written"
       and st.get("lastprod", {}).get("incremental") is False,
       "(A4) first round writes state + payload; last_production is a full-window round: %s" % st.get("lastprod"))
    days = a1.get("x_cal_days") or []
    ck(any(x["production"] for x in days) and any(not x["production"] for x in days),
       "(A6) the synthetic calendar has both production and rest days (quorum from synthetic segments)")
    ck(a1.get("x_cal_audit_eids_in_fleet") is True, "(A7) rest-day audit lists only synthetic-fleet EIDs")
    ck(a1.get("x_lp_bad") == [] and (a1.get("x_lp_n_with_end") or 0) > 0 and a1.get("x_lp_items_known") is True
       and a1.get("x_lp_observed_at") is True,
       "(A8) P4 last_production: every item == max end of the same segments (fold_eid, window) — %s items, %s with "
       "a last segment; mismatches=%s" % (a1.get("x_lp_n_items"), a1.get("x_lp_n_with_end"), a1.get("x_lp_bad")))
    ck(a1.get("x_lp_station_lot_err") == STATION_LOT_NOT_PROVIDED,
       "(A9) station lot honestly 'not provided' (no zero-production claim): %r" % a1.get("x_lp_station_lot_err"))
    ck(a1.get("wsynth_loaded") and "wsynth_io" in a1["wsynth_loaded"], "(A10) tick ran on the wsynth_io world")

    b1 = _run_child(sdB, {"steps": "calendar,lastprod"}, synth_env(sdB, T1))
    ck(not b1.get("exc") and {k: v[0] for k, v in b1.get("files", {}).items()}
       == {k: v[0] for k, v in a1.get("files", {}).items()},
       "(B1) determinism: same seed + same frozen clock in another state dir -> byte-identical calendar, state, "
       "payload")

    # ── (I) 冪等：同一時刻重跑 ────────────────────────────────────────────────────────────
    a1b = _run_child(sdA, {"steps": "calendar,lastprod"}, synth_env(sdA, T1))
    st = {r["step"]: r for r in a1b["summary"]["results"]} if a1b.get("summary") else {}
    cal_same = "unchanged" if exp_rc == EXIT_OK else "invalid"
    ck(not a1b.get("exc") and st.get("calendar", {}).get("status") == cal_same
       and st.get("lastprod", {}).get("status") == "unchanged" and (a1b.get("summary") or {}).get("rc") == exp_rc,
       "(I1) rerun at the same frozen time: calendar and last_production state not rewritten, same exit code: %s" % st)
    ck(all(a1b.get("files", {}).get(k) == a1.get("files", {}).get(k) for k in ("calendar", "state"))
       and a1b.get("payload_core_sha") == a1.get("payload_core_sha")
       and a1.get("payload_rebuild") is True and a1b.get("payload_rebuild") is False,
       "(I2) rerun: state (and calendar) byte- and mtime-identical; the wire payload keeps every fact and only "
       "drops this round's full-window-rebuild claim (incremental round, as in the live pusher)")
    a1c = _run_child(sdA, {"steps": "calendar,lastprod"}, synth_env(sdA, T1))
    st = {r["step"]: r for r in a1c["summary"]["results"]} if a1c.get("summary") else {}
    ck(not a1c.get("exc") and a1c.get("files") == a1b.get("files")
       and st.get("lastprod", {}).get("status") == "unchanged" and st.get("lastprod", {}).get("payload") == "unchanged",
       "(I3) third round at the same time: every file byte- and mtime-identical (idempotent): %s" % st.get("lastprod"))
    isolation("I", a1b, sdA)

    # ── (F) 時鐘往前（T2＝T1+26h、同一目錄＝增量輪）：前綴穩定、單調 ─────────────────────────
    a2 = _run_child(sdA, {"steps": "calendar,lastprod", "extra": ["xcheck"]}, synth_env(sdA, T2))
    st = {r["step"]: r for r in a2["summary"]["results"]} if a2.get("summary") else {}
    ck(not a2.get("exc") and not a2.get("xexc") and st.get("lastprod", {}).get("incremental") is True,
       "(F1) T2 round on the same dir is incremental: %s %s" % (a2.get("exc"), a2.get("xexc")))
    ck(a2.get("x_lp_bad") == [] and a2.get("x_lp_observed_at") is True,
       "(F2) after the incremental round every item still == max end of the same segments: %s" % a2.get("x_lp_bad"))
    e1, e2 = a1.get("x_lp_state_eids") or {}, a2.get("x_lp_state_eids") or {}
    ck(e1 and all(k in e2 and str(e2[k]) >= str(e1[k]) for k in e1),
       "(F3) last_production is monotone: no EID lost, no end moved backwards (%d EIDs)" % len(e1))
    d1 = {x["date"]: x["production"] for x in (a1.get("x_cal_days") or [])}
    d2 = {x["date"]: x["production"] for x in (a2.get("x_cal_days") or [])}
    lost = sorted(set(d1) - set(d2))
    ck(d1 and all(d2.get(k) == v for k, v in d1.items() if k in d2) and len(lost) <= 2
       and all(k < min(d2) for k in lost) and len(d2) >= len(d1) - 1,
       "(F4) prefix stability: days claimed at T1 keep their production flag at T2; only the window's left edge "
       "drops out (%s)" % lost)
    rc2 = cal_case("F", a2)
    ck(rc2 != EXIT_OK or st.get("calendar", {}).get("why") in ("facts_changed", "new_production_day"),
       "(F5) next production day: a valid calendar is rewritten (%s)" % st.get("calendar", {}).get("why"))
    ck((a2.get("summary") or {}).get("rc") == rc2, "(F6) round exit code == expected %s" % rc2)
    isolation("F", a2, sdA)

    # ── (M) 跨月＋月帳凍結＋13 個月輪替（freeze 走 app 的單一出處）────────────────────────────
    sdM = os.path.join(root, "M")
    m = _run_child(sdM, {"steps": "calendar,lastprod,freeze", "extra": ["xcheck", "rotation"]}, synth_env(sdM, TM))
    st = {r["step"]: r for r in m["summary"]["results"]} if m.get("summary") else {}
    prev_month = "%04d-%02d" % ((first.year, first.month - 1) if first.month > 1 else (first.year - 1, 12))
    fz = st.get("freeze", {})
    rcm = cal_case("M", m)
    ck(not m.get("exc") and (m.get("summary") or {}).get("rc") == rcm and fz.get("ok") is True
       and fz.get("month") == prev_month,
       "(M1) cross-month tick (%s): freeze asks about the previous month by the synthetic clock (%s): %s %s"
       % (TM, prev_month, fz, m.get("tb", "")[-800:]))
    ck(fz.get("written") is False and fz.get("rotated") == [],
       "(M2) phase 1 has no daily ledgers -> nothing frozen, nothing rotated (honest no-op): %s" % fz.get("why"))
    ck(m.get("x_lp_bad") == [] and m.get("x_lp_observed_at") is True,
       "(M3) cross-month last_production still pinned to the same segments")
    cal_months = sorted({x["date"][:7] for x in (m.get("x_cal_days") or [])})
    ck(len(cal_months) == 2, "(M4) the calendar window spans the month boundary: %s" % cal_months)
    rot = m.get("rot") or {}
    ck(not m.get("rexc") and rot.get("r13", {}).get("rotated") == [] and len(rot.get("left13", [])) == 14
       and rot.get("ns") in rot.get("left13", []),
       "(M5) rotation boundary: exactly 13 synthetic snapshots -> none removed (non-synth snapshot untouched): "
       "%s %s" % (m.get("rexc"), rot.get("r13")))
    ck(rot.get("r14", {}).get("rotated") == [rot.get("extra")] and len(rot.get("left14", [])) == 14
       and rot.get("ns") in rot.get("left14", []) and rot.get("extra") not in rot.get("left14", []),
       "(M6) 14 synthetic snapshots -> the oldest one removed, 13 kept; non-synth snapshot never rotated: %s"
       % rot.get("r14"))
    isolation("M", m, sdM)
    ck(m.get("app_loaded") is True and m.get("driver_stub") is True,
       "(M7) freeze imported app inside the synthetic process (driver stub in place)")
    ck(a1.get("app_loaded") is False, "(M8) without the freeze step the tick never imports app")

    # ── (W) 暖機：loopback 假伺服器（health 正常／掛掉）、非 loopback 拒絕 ──────────────────────
    sdW = os.path.join(root, "W")
    w = _run_child(sdW, {"steps": "warm", "warm_base": "http://127.0.0.1:9", "extra": ["warmstub"]},
                   synth_env(sdW, T1))
    ck(not w.get("exc") and not w.get("wexc"), "(W0) warm child ran: %s %s" % (w.get("exc"), w.get("wexc")))
    ck(w.get("warm_got") == ["/health"] + list(WARM_PATHS)
       and [r[1] for r in (w.get("warm_up") or {}).get("results", [])] == ["OK 200"] * len(WARM_PATHS),
       "(W1) warm: health gate then every phase-1 endpoint once, in order: %s" % w.get("warm_got"))
    ck(w.get("warm_got_down") == ["/health"] and (w.get("warm_down") or {}).get("status") == "service_down",
       "(W2) health not 200 -> round skipped after the gate: %s" % w.get("warm_got_down"))
    ck((w.get("warm_nonloop") or {}).get("status") == "refused_non_loopback",
       "(W3) non-loopback warm base refused (no request sent)")
    ck(w.get("summary", {}).get("rc") == 0, "(W4) a failed warm never fails the tick (server down: rc 0)")
    isolation("W", w, sdW)

    # ── (R) 拒絕啟動：非合成、未改向、repo 內、非空無標記、覆寫 LP 狀態檔 ─────────────────────
    sdN = os.path.join(root, "N")
    os.makedirs(sdN)
    r = _run_child(sdN, {"cli": True, "argv": ["--steps", "calendar,lastprod"]},
                   {"BOARD_STATE_DIR": sdN, "BOARD_SYNTH_NOW": T1.isoformat()})
    ck(r.get("rc") == EXIT_REFUSED and os.listdir(sdN) == [] and "wsynth_io" not in (r.get("wsynth_loaded") or [])
       and not r.get("mes_loaded"),
       "(R1) non-synthetic process refused (rc 2): nothing written, no wsynth_io / MES module loaded")
    r = _run_child(None, {"cli": True}, {"BOARD_SOURCE": "synth", "BOARD_SYNTH_NOW": T1.isoformat()})
    ck(r.get("rc") == EXIT_REFUSED and not r.get("writes"), "(R2) synth without BOARD_STATE_DIR refused (rc 2)")
    inrepo = os.path.join(REPO, "_wsynth_tick_selftest_must_not_exist")
    r = _run_child(inrepo, {"cli": True}, synth_env(inrepo, T1))
    ck(r.get("rc") == EXIT_REFUSED and not os.path.exists(inrepo), "(R3) state dir inside the repo refused, not created")
    sdU = os.path.join(root, "U")
    os.makedirs(sdU)
    with open(os.path.join(sdU, "someone_elses.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    r = _run_child(sdU, {"cli": True}, synth_env(sdU, T1))
    ck(r.get("rc") == EXIT_REFUSED and sorted(os.listdir(sdU)) == ["someone_elses.json"],
       "(R4) non-empty unmarked state dir refused, nothing added")
    sdL = os.path.join(root, "L")
    r = _run_child(sdL, {"cli": True}, dict(synth_env(sdL, T1), BOARD_EQM_LP_STATE=os.path.join(root, "x.json")))
    ck(r.get("rc") == EXIT_REFUSED and not os.path.exists(sdL), "(R5) %s override refused" % ENV_EQM_LP_STATE)
    r = _run_child(None, {"cli": True, "argv": ["--steps", "nope"]}, {})
    ck(r.get("rc") == EXIT_REFUSED, "(R6) bad --steps refused before anything runs")

    # ── (L) live／demo 狀態檔路徑與 repo 全程未被碰 ──────────────────────────────────────────
    live1 = {p: (os.stat(p).st_mtime_ns if os.path.exists(p) else None) for p in live_paths}
    ck(live1 == live0, "(L1) repo-root calendar and repo state-dir last_production / payload untouched")
    fp1 = _repo_fingerprint()
    changed = sorted(k for k in set(fp0) | set(fp1) if fp0.get(k) != fp1.get(k))
    ck(not changed, "(L2) repo files unchanged by the whole selftest: %s" % changed[:8])

    shutil.rmtree(root, ignore_errors=True)
    print("wsynth_tick selftest: %d checks, %d failed, %.0fs -> %s"
          % (n[0], len(fails), time.time() - t_all, "ALL PASS" if not fails else "FAIL"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
