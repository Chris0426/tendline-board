#!/usr/bin/env python3
"""Tendline Board 前門儀表板的資料供應層（live / snapshot / demo）。

把 mes_timeline.py（凍結 v1）的確定性核心包成儀表板可消費的 canonical 結構。
三種資料源（同一份輸出結構，UI 不分源）：
  - live    : 連 MES 走凍結的參數化 query（需 VPN + Kerberos 整合驗證、唯讀）。
  - snapshot: 載已存的 `mes_timeline --json` 輸出檔（離線也能看真實快照）。
  - demo    : 合成 raw intervals → 走『同一套』analyze/timeline_records 分類
              （離線開發/demo 用；分類邏輯與 live 完全相同，只有 interval 來源不同）。

鐵則（沿用，未違反）：
  - 不改 mes_timeline（凍結）；只 import 重用其函式。
  - 狀態 = §23；信心 {high, medium, low} 正面 only。融合是規則非 agent、runtime 無 LLM（§15）。
  - 稼動 = 設備視角 interval-union、NO 去重（§14.4）——直接重用 mes_timeline.analyze。
  - 所有 aggregate 由 canonical records 推導（單一真相源）；
    不明% = 不明秒 / 觀測秒（§25 健康訊號，應隨 cmms/SALTMARSH 排程/補問上線單調下降）。

CLI（離線可跑）：
  python board_data.py --demo --json      # 印 demo canonical JSON
  python board_data.py --source snapshot --file out.json
"""
import argparse
import html
import json
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import mes_timeline as mt
import board_atomic as _wat  # 原子寫共用 helper（零相依；日帳三條路徑用）
import core_paths as _wp  # 執行期產物路徑唯一出處（Q-F；零相依）
import core_source as _wsrc  # 來源值／合成進程旗標唯一出處（零相依；import 它不會載入任何 wsynth_ 模組）
import mes_yield as my              # §26 B/C 產出/良率（凍結，唯讀；重用其 scoreboard/station_yield/builders）
import board_eqmeeting as wem     # W8 工單態 overlay（§14.4.1）：重用 S12 分階段 wo_segments/downtime_by_eid、不重寫
import board_w8 as w8             # 同上：work_type→§23 態對映（BUCKET_STATE/wo_state）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---- 合成產線（synth）接縫：SPEC-SYNTHLINE WP-3；裁決 Q3／Q-A／Q-B／Q-E ------------------------
#   ★ I/O 注入點（MES fetcher 入口）一律看**進程旗標** `_synth_proc()`，不看請求的 `source`
#     （Q-A：多處呼叫端把 "live" 字面寫死，看參數會漏網）。合成進程下改由 `wsynth_io` 回同形原子資料，
#     其後的 `_clip`／`analyze`／`_build`……全部照原碼走（C-SY8）。
#   ★ 路由判斷（§5 的 S 類）改用 `_live_route(source)`（＝`core_source.is_live_like` 的 LIVE_LIKE 判斷）。
#   ★ `wsynth_io` 一律在函式內延遲 import ⇒ **live／demo 進程永遠不載入任何 wsynth_ 模組**。
#   ★ 非合成進程：以下三個 helper 讓每一處判斷與改動前對所有輸入逐位元等價（見各 docstring）。
def _synth_proc():
    """本進程是不是合成進程（`core_source` 鎖定的進程旗標；非合成進程恆 False）。"""
    return _wsrc.is_synth_process()


def _wsio():
    """延遲 import 合成讀取面。**只在 `_synth_proc()` 為真時呼叫。**"""
    import wsynth_io
    return wsynth_io


def _live_route(source):
    """§5 S 類的路由判斷（原 `source == "live"`）。

       · 非合成進程：`normalize_source` 把 `synth` 拒成 None、其餘值照 app 現行規則 ⇒
         `is_live_like(...)` 為真**當且僅當** `source == "live"`（對所有輸入與原判斷等價）。
       · 合成進程：任何 `source` 都塌成 `synth`（Q-B）⇒ 恆走 live 資料路徑，
         其 I/O 在 fetcher 入口依進程旗標轉接到 `wsynth_io`。"""
    return _wsrc.is_live_like(_wsrc.normalize_source(source))


def _synth_src():
    return _wsrc.SOURCE_SYNTH

# §23 十態 canonical 排序 + 工單態（cmms 工單疊到不明區間；donut/legend/fleet timeline 依此序著色，
#   **不在序內的態會被 donut_segments 靜默跳過**——工單可產的每個態都必須在這裡）。
#   ★ 2026-08-16 起工單 machine-time 分類＝/equipment 那套（Chris 裁決全站統一）：
#     維修／計畫保養／等料(停機)／等外包(停機)。調機與 w8 暫桶（其他維護(子類待定)）＝
#     eMaint legacy——**仍須留在序內**（歷史單真出現時以 legacy 灰具名顯示、不藏資料），
#     但不再是主 legend 語言。
STATE_ORDER = [
    "生產", "計畫保養", "調機", "流程等待", "待機",
    "該跑沒跑", "非排定", "維修",
    wem.HOLD_PARTS_STATE, wem.HOLD_VENDOR_STATE,     # 等料(停機)/等外包(停機)＝/equipment 同名同色
    "故障停機", w8.BUCKET_STATE, "不明",
]

# live MES query 逾時上限（秒）：避免大觀測窗(如 30 天)單一 query 無限掛住 → 逾時則該段 unavailable、頁面仍回。
LIVE_QUERY_TIMEOUT_SEC = 180

# ---- Up/Down 板（/updown）live/snapshot 參數 ------------------------------------
# 目錄窗（fetch_fleet）：列「近 N 天在役」的真機台＝板的機台清單（比 evidence 窗寬，故容得下
#   「在役但近 24h 無段」的 UNKNOWN 機台）。日後換 diag.mes_activity.v1 常時目錄即無此窗限。
UD_FLEET_DAYS = int(os.environ.get("BOARD_UD_FLEET_DAYS", "3"))
# evidence 窗（fetch_segments）：只看「最近一段生產」距今 age＝近況訊號（建議 24h）。
UD_LOOKBACK_DAYS = int(os.environ.get("BOARD_UD_LOOKBACK_DAYS", "1"))
# updown 專用單一 query 逾時（比 180s 短）：全站逐站掃、單站不該掛太久；配合整體 budget（app 層）。
UD_QUERY_TIMEOUT = int(os.environ.get("BOARD_UD_QUERY_TIMEOUT", "30"))
# 連線（login）逾時（ROADMAP #23 (b)，2026-08-18）：live 探測的失敗實測**全是連線期失敗**
#   （08001：Named Pipes 53/64、SSL 10060、login timeout——不是查詢逾時），而 ODBC 預設的
#   多協定 fallback 會把「連不上」燒成 15–105s 白等。健康網路下連線 <1s ⇒ 上限收到 15s
#   ＝失敗快退（→ /equipment 落 lastprod 狀態檔或 caveat、/updown 落 caveat），不改任何窗行為。
UD_LOGIN_TIMEOUT = int(os.environ.get("BOARD_UD_LOGIN_TIMEOUT", "15"))

# demo CAL fleet＝父機級（與 live overview 對齊）：3 Gen1 獨立 + Gen2 父機 EID-24742。
# 稼動(timeline)/良率(yield)demo 共用同一組 EID；單站頁再走 GEN2_BAY_POSITIONS 把 Gen2 拆工位（與 live 對齊）。
DEMO_CAL_FLEET = [('EID-55040', 'Calibration Tester #1', 0.84),
                  ('EID-54306', 'Calibration Tester #2', 0.47),
                  ('EID-55232', 'Calibration Tester Gen 2', 0.83)]

# CAL Gen2 4-up 真實槽位工位（MES dependent-equipment export；2026-06-29 census U4/U5 驗證：近 30 天僅 Bay 1/4 有產出）。
# 呈現層 detail-only：凍結 fetch_fleet 仍 rollup 到父機（站級總數不動），單站頁才把父機拆成工位。
# 元組＝(工位 EID, 顯示名, demo 生產比)；live 走真查詢、demo 按比例切。機台帶＝配置工位一律顯示
# （窗內無訊號者空軌+nosig 旗）；yield/units 表＝只留有產出者（output>0，產出語意）。
CLN_CLASS = "segCleanTest"                    # CLN（Fluid Clean E-Test）＝/daily 對映的 CLN 站；EQP_STATIONS 之一。
GEN2_BAY_POSITIONS = {'EID-55232': [('EID-54912', 'Calibration Bay #1', 0.61),
                                       ('EID-54681', 'Calibration Bay #2', 0.0),
                                       ('EID-54775', 'Calibration Bay #3', 0.0),
                                       ('EID-54457', 'Calibration Bay #4', 0.52)],
                         'EID-55546': [('EID-54358', 'Rinse Bay #1', 0.57),
                                       ('EID-54395', 'Rinse Bay #2', 0.49),
                                       ('EID-55074', 'Rinse Bay #3', 0.44),
                                       ('EID-55513', 'Rinse Bay #4', 0.38),
                                       ('EID-55177', 'Rinse Bay #5', 0.33)],
                         'EID-54346': [('EID-55196', 'Fill Bay #1', 0.42),
                                       ('EID-54760', 'Fill Bay #2', 0.36),
                                       ('EID-54380', 'Fill Bay #3', 0.29)]}
# 父機 EID → True＝模組級打標覆蓋率低（子工位＝細分視圖、Σ子≠父，站頁 yield 展開保留父機列、UI 掛覆蓋率 caveat）。
MODULE_COVERAGE_NOTES = {'EID-55546': True, 'EID-54346': True}
# 覆蓋率低的父機所屬 segment class（站頁 /station 掛 CLN 覆蓋率 caveat 用；class 粒度＝robust 於 demo/live）。
MODULE_COVERAGE_NOTE_CLASSES = {CLN_CLASS}
# 子工位 EID → 父機 EID（反查；供 demo 站頁 yield 列補標 parent_eid＝縮排/群組，與 live 對齊）。
_BAY_PARENT = {m: p for p, _mods in GEN2_BAY_POSITIONS.items() for m, _n, _r in _mods}
# demo ratio<=0 的工位（census 無產出，如 CAL 26841/26887）：全列制下仍成列、但 demo 不替它合成產出（誠實零列）。
_ZERO_RATIO_MODULES = {m for _p, _mods in GEN2_BAY_POSITIONS.items() for m, _n, r in _mods if r <= 0}
# 稼動時間軸(/utilization、/station)的 curated 父機級 demo fleet（拆子工位由 band/units 展開；EID 全真實、不捏造）。
# CAL 走既有 DEMO_CAL_FLEET(get_demo else 分支)；此處只補 CLN＝與 live 機隊同形（二母機）。
DEMO_TL_FLEETS = {'segCleanTest': [('EID-55546', 'Clean Tester Gen 2 (rinse)', 0.77),
                                       ('EID-54346', 'Clean Tester Gen 2 (fill)', 0.69)]}


# ---- 日期範圍（觀測窗）：在資料層裁切，凍結核心不動 -----------------------------
def _parse_dt(s):
    """'YYYY-MM-DD' 或完整 datetime 字串 → datetime；空/壞 → None。"""
    if not s or not str(s).strip():
        return None
    try:
        return datetime.fromisoformat(str(s).strip())
    except ValueError:
        return None


def _is_date_only(s):
    return bool(s) and len(str(s).strip()) <= 10


def resolve_window(now, days, start=None, end=None):
    """決定觀測窗 [win_start, win_end]。預設 = [now-days, now]（與凍結行為一致）。
       end 為純日期時含當日（到隔日 0 點）；end 上限為 now（不看未來）；
       壞範圍（start>=end）→ 退回以 win_end 為錨的 days 視窗。"""
    we = _parse_dt(end)
    if we is not None and _is_date_only(end):
        we = we + timedelta(days=1)               # 純日期 end → 含當日
    win_end = we or now
    if win_end > now:
        win_end = now
    win_start = _parse_dt(start) or (win_end - timedelta(days=days))
    if win_start >= win_end:
        win_start = win_end - timedelta(days=days)
    return win_start, win_end


def _clip(intervals, lo, hi):
    """把 raw 區間裁到 [lo, hi]；無重疊者丟棄（裁切後仍交給凍結 analyze）。"""
    out = []
    for a, b in intervals:
        a2, b2 = (a if a > lo else lo), (b if b < hi else hi)
        if b2 > a2:
            out.append((a2, b2))
    return out


# ---- 零工期段偵測（M4 後遺：end-filter `>=` 後零工期段回歸）--------------------------
#   八個站的 MES segment 100% 零工期（stpEndTime==stpStartTime＝瞬時事件標記：進料檢/序號寫入/
#   烘烤打點）。段長法稼動＝生產秒/觀測秒，對它們**結構性不可測**（分子恆 0）→ 若照算會顯
#   util 0.0%＝誤導「從不運轉」。誠實做法＝偵測後顯「—」（不可測）而非 0%。
#   ★ 語意邊界（呈現層文案必守）：「—」≠壞掉、≠沒訊號；＝「有 MES 事件、但段無時長訊號、
#     段長法不適用」。與 nosig（整窗無訊號＝n_seg==0）是**兩個不同語意**，不共用同一句文案。
def _win_seg_count(intervals, lo, hi):
    """窗內 segment 數（含零工期段；與 _clip 不同——_clip 丟零工期段〔b2>a2 才留〕）。
       用於機台級 zero_len 偵測：clip 掏空後仍要知道『窗內原本有段』。零工期段 (t,t) 落窗＝計入。"""
    return sum(1 for a, b in intervals if b >= lo and a <= hi)


def _zero_len_only_station(rec):
    """站級零工期偵測（純函式、加性旗）：fleet 非空 且 Σsegments>0 且 Σ生產秒==0
       ⟺ 全機隊在窗內只有零工期段（任一正時長段都會讓 interval-union 生產秒>0）。
       用於稼動計分板站 rec（mt.fleet_scoreboard／snapshot／demo 同形欄位）。
       混合站（有真時長段）＝生產秒>0＝旗不立（誠實 util 真值）。整站無訊號（n_seg==0）＝旗不立。"""
    return bool((rec.get("fleet_size") or 0) > 0
                and (rec.get("n_seg") or 0) > 0
                and (rec.get("producing_sec") or 0) == 0)


def _range_station_zlo(fleet, segs, start, end):
    """日曆窗 live 計分板：站級零工期旗（純函式、供離線測）。
       ★ 必在 _clip 掏空零工期段**之前**判定——range 路徑 analyze(_clip(...)) 後 n_seg=0，
         _zero_len_only_station 會誤判 False（旗永不立、真產出被藏）。故此處直接看未 clip 的原始段。
       語意與 _zero_len_only_station 一致（Σ 口徑）：fleet 非空 且 窗內有段(含零工期) 且
       無任一機台有正時長段 ⟺ 全機隊窗內只有零工期段。任一機有正時長段→旗不立。
       fleet=[(eid,desc)]、segs={eid:[(a,b)]}（未 clip）。"""
    if not fleet:
        return False
    any_seg = any_pos = False
    for eid, _ in fleet:
        ivs = segs.get(eid, [])
        if _win_seg_count(ivs, start, end) > 0:
            any_seg = True
        if _clip(ivs, start, end):            # clip 非空 ⟺ 有正時長段落窗
            any_pos = True
    return bool(any_seg and not any_pos)


def aggregate(records):
    """由 canonical 狀態 records 推導 aggregate（單一真相源）。"""
    by_state = defaultdict(float)
    for r in records:
        by_state[r["state"]] += r["sec"]
    total = sum(by_state.values())
    prod = by_state.get("生產", 0.0)
    unknown = by_state.get("不明", 0.0)
    return {
        "by_state": dict(by_state),
        "total_sec": total,                              # = 觀測窗（首段→now）
        "producing_sec": prod,
        "downtime_sec": total - prod,                    # 非生產（v1 即不明）
        "unknown_sec": unknown,
        "util": (prod / total if total else 0.0),        # 稼動 = 設備視角生產/觀測
        "unknown_pct": (unknown / total if total else 0.0),  # §25 健康訊號
    }


def _build(fleet, segs, now, days, stop_min, source, win_start=None, zero_len=None):
    """fleet=[(eid,desc)], segs={eid:[(start,end)]} → canonical（走凍結 analyze/records）。
       now=觀測窗末端（=win_end，亦即尾段停機的錨）；win_start=窗起（共同軸左緣）。
       zero_len={eid:bool}（可選）＝呼叫端（get_live）在 _clip 掏空零工期段**之前**算好的旗；
       _build 拿到的 segs 已 clip（零工期段消失、analyze→None），故機台級 zero_len 靠此傳入。
       未傳時退回從 analyze 推（segs 未經 clip 的路徑：a.n_seg>0 且生產秒==0＝零工期 only）。"""
    thr = stop_min * 60
    zl = zero_len or {}
    machines, all_records = [], []
    for eid, desc in fleet:
        # 源資料有雙重轉義（如 EID-11860 的 MES desc 內含字面 "&amp;" → "Dispenser …"）→ 在資料層一次
        # html.unescape 還原（對正常名稱冪等：無實體者原樣返回）。凍結 fetch_fleet 不動、只在此融合層修。
        desc = html.unescape(desc) if isinstance(desc, str) else desc
        a = mt.analyze(segs.get(eid, []), now, thr)
        # 零工期 only：傳入旗（live，clip 掏空前算）OR analyze 推（未 clip 路徑：有段但生產秒 0）。
        zlo = bool(zl.get(eid)) or bool(a and (a.get("n_seg") or 0) > 0
                                        and (a.get("producing_sec") or 0) == 0)
        if not a:
            machines.append({"eid": eid, "desc": desc, "records": [], "n_seg": 0,
                             "micro_idle_sec": 0.0, "agg": aggregate([]), "_a": None,
                             "zero_len_only": zlo})
            continue
        recs = mt.timeline_records(eid, a, now)
        all_records.extend(recs)
        machines.append({"eid": eid, "desc": desc, "records": recs,
                         "n_seg": a["n_seg"], "micro_idle_sec": a["micro_idle_sec"],
                         "agg": aggregate(recs), "_a": a, "zero_len_only": zlo})   # _a=凍結 analyze 結果，供站頁直接彙總 fleet_scoreboard(免重抓)
    return {"source": source, "mes_now": now, "days": days, "stop_min": stop_min,
            "win_start": win_start, "win_end": now,
            "machines": machines, "fleet": aggregate(all_records),
            "micro_idle_sec": sum(m["micro_idle_sec"] or 0 for m in machines)}


# ---- live：連 MES（重用凍結的參數化 query；需 VPN+Kerberos） --------------------
def get_live(days=4, stop_min=15, start=None, end=None, cls=mt.CAL_CLASS):
    if _synth_proc():                       # 清冊 §1 #1–#2（M1＋M2）：進程旗標分派
        return _get_live_synth(days, stop_min, start, end, cls)
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC   # 逾時上限：大窗 query 不致無限掛住（逾時→該段 unavailable、頁仍回）
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        win_start, win_end = resolve_window(now, days, start, end)
        # 凍結 fetcher 以 now 為錨抓最近 N 天；取足以覆蓋 win_start 的天數，再於本層裁切。
        fetch_days = max(days, (now - win_start).days + 1)
        fleet = mt.fetch_fleet(cur, fetch_days, cls)
        segs = mt.fetch_segments(cur, [e for e, _ in fleet], fetch_days, cls)
    finally:
        conn.close()
    return _live_build(fleet, segs, win_start, win_end, days, stop_min, "live")


def _live_build(fleet, segs, win_start, win_end, days, stop_min, source):
    """get_live 的後半（抓回原始段之後）：live 與 synth 共用**同一段**（C-SY8：聚合只有一份）。"""
    # 零工期 only 偵測**在 clip 之前**（_clip 丟零工期段→analyze None→無從辨別 nosig vs zero_len）：
    #   窗內原有段(含零工期) 但 clip 後空 ⟺ 窗內全零工期段 ⟺ 生產秒 0（clip 非空⇒有正時長段⇒生產>0）。
    clipped = {e: _clip(ivs, win_start, win_end) for e, ivs in segs.items()}
    zlo = {e: (_win_seg_count(segs.get(e, []), win_start, win_end) > 0 and not clipped.get(e))
           for e, _ in fleet}
    return _build(fleet, clipped, win_end, days, stop_min, source, win_start, zero_len=zlo)


def _get_live_synth(days, stop_min, start, end, cls):
    """合成進程的 get_live：窗錨＝`wsynth_io.now()`（取代 MES `SELECT GETDATE()`，C-SY4），
       fleet／segments 由轉接層同形供應，其後與 live 走同一個 `_live_build`。"""
    io = _wsio()
    now = io.now()
    win_start, win_end = resolve_window(now, days, start, end)
    fetch_days = max(days, (now - win_start).days + 1)
    fleet = io.fetch_fleet(fetch_days, cls, now=now)
    segs = io.fetch_segments([e for e, _ in fleet], fetch_days, cls, now=now)
    return _live_build(fleet, segs, win_start, win_end, days, stop_min, _synth_src())


# ---- snapshot：載已存的 mes_timeline --json（records 已分類，直接分組） ----------
def get_snapshot(path, stop_min=None, days=4, start=None, end=None):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    snap_now = _parse_dt(data.get("mes_now")) or datetime.now().replace(microsecond=0)
    win_start, win_end = resolve_window(snap_now, days, start, end)
    ranged = bool(start or end)
    by_eid = defaultdict(list)
    for r in data["records"]:
        if ranged:                                # 把已分類 record 裁到窗（重算 sec、丟無重疊）
            rs, re = _parse_dt(r["start"]), _parse_dt(r["end"])
            if not rs or not re:
                continue
            a2, b2 = (rs if rs > win_start else win_start), (re if re < win_end else win_end)
            if b2 <= a2:
                continue
            r = {**r, "start": str(a2), "end": str(b2), "sec": (b2 - a2).total_seconds()}
        by_eid[r["eid"]].append(r)
    machines, all_recs = [], []
    for eid in sorted(by_eid):
        recs = sorted(by_eid[eid], key=lambda r: r["start"])
        all_recs.extend(recs)
        machines.append({"eid": eid, "desc": "", "records": recs, "n_seg": None,
                         "micro_idle_sec": None, "agg": aggregate(recs),
                         # snapshot 已分類 records（無原始段計數）→ 機台級 zero_len 保守不判（False）；
                         # 站級零工期仍由計分板快照 n_seg/producing 偵測（見 _wrap_scoreboard）。
                         "zero_len_only": False})
    return {"source": "snapshot", "mes_now": (win_end if ranged else data.get("mes_now")),
            "days": data.get("days", days), "stop_min": data.get("stop_min", stop_min),
            "win_start": (win_start if ranged else None),
            "win_end": (win_end if ranged else data.get("mes_now")),
            "machines": machines, "fleet": aggregate(all_recs), "micro_idle_sec": None}


# ---- demo：合成 raw intervals → 走同一套分類（離線開發/demo） -------------------
def _demo_intervals(start, end, duty, seed):
    """合成設備視角段（窗 [start,end]）：含重疊（重測/Gen2 子槽位）+ micro-idle + 停機。
       seed 偶數者延後起跑，讓共同軸 demo 也呈現各台觀測窗起點不齊。"""
    import random
    rnd = random.Random(seed * 97 + 13)
    span_min = max(1.0, (end - start).total_seconds() / 60)
    t = start + timedelta(minutes=rnd.randint(0, int(span_min * 0.12)) if seed % 2 else 0)
    ivs = []
    while t < end:
        block_min = rnd.randint(20, 120)
        blk_end = min(t + timedelta(minutes=block_min), end)
        for _ in range(rnd.randint(1, 4)):                       # 1–4 重疊子段
            a = t + timedelta(minutes=rnd.randint(0, max(1, block_min // 2)))
            b = min(a + timedelta(minutes=rnd.randint(10, 90)), blk_end)
            if b > a:
                ivs.append((a, b))
        t = blk_end
        if rnd.random() > duty:
            t += timedelta(minutes=rnd.randint(15, 180))         # 停機（>=門檻）
        else:
            t += timedelta(minutes=rnd.randint(1, 12))           # micro-idle（併入 run）
    return ivs


def get_demo(days=4, stop_min=15, start=None, end=None, cls=mt.CAL_CLASS):
    now = datetime.now().replace(microsecond=0)
    win_start, win_end = resolve_window(now, days, start, end)
    fleet, segs = [], {}
    tl_fleet = DEMO_TL_FLEETS.get(cls)
    if tl_fleet is not None:
        # curated 父機級 fleet（CLN：父機 01066，band 展開子工位）＝真 EID、確定性 seed。
        for i, (eid, desc, duty) in enumerate(tl_fleet):
            fleet.append((eid, desc))
            segs[eid] = _demo_intervals(win_start, win_end, duty, seed=i)
    elif cls and cls != mt.CAL_CLASS:
        # 非 CAL 站（/utilization 站別切換）：EID 取自該站 demo 良率機台列（F1 鐵則：不捏造 EID）；
        # 段用同一 _demo_intervals 合成器、per-EID 確定性 seed。無機台站＝誠實空 fleet。
        ys = _demo_yield_station(cls, days)
        for m in ((ys.get("station") or {}).get("machines") or []):
            eid = m.get("eid")
            if not eid:
                continue
            seed = sum(ord(c) for c in eid)
            fleet.append((eid, m.get("desc", "")))
            segs[eid] = _demo_intervals(win_start, win_end, 0.35 + (seed % 45) / 100.0, seed=seed)
    else:
        for i, (eid, desc, duty) in enumerate(DEMO_CAL_FLEET):
            fleet.append((eid, desc))
            segs[eid] = _demo_intervals(win_start, win_end, duty, seed=i)
    return _build(fleet, segs, win_end, days, stop_min, "demo", win_start)


def get_data(source="demo", days=4, stop_min=15, snapshot_path=None, start=None, end=None, cls=mt.CAL_CLASS):
    if _live_route(source):                 # §5 wd:363（S）
        return get_live(days, stop_min, start, end, cls)
    if source == "snapshot":
        if not snapshot_path:
            raise ValueError("source=snapshot 需提供 snapshot_path")
        return get_snapshot(snapshot_path, stop_min, days, start, end)
    return get_demo(days, stop_min, start, end, cls)


# ======================================================================================
#  名冊補列（2026-08-24 修 bug；裁決存證＝`SPEC-RUNWINDOW.md` §6.5、鐵則＝**C-RW18**）
# --------------------------------------------------------------------------------------
#  ★ bug 的形（Chris 2026-08-23 在 live 發現 Seal 站看不到 `EID-25846` Seal#7）：
#    `/utilization`／`/station`／`/machine` 的機台清單是**從 MES 活動段反推**的
#    （`get_live` → 凍結 `mt.fetch_fleet`：那支只回「窗內有 segment」的 EID）
#    ⇒ **整窗都在 down 的機台沒有任何段 ⇒ 整台從清單消失**
#    ⇒ 它的停機既不進分母也不進分子 ⇒ **站稼動被高估、停越久越好看**。
#    `/equipment` 不受影響是因為那頁走 curated `EQMT_CATALOG`（清單不是反推的）。
#  ★ 修法（**推導，不是第二份事實**）：清單 ＝「較寬窗 fleet ∩ 綑名冊」。
#    · 較寬窗＝`max(days, ROSTER_DAYS)`，**只取清單、不取 segments**（B4 之後 30d 實測 ≈3.5s）。
#    · 綑名冊唯一出處＝`_probe_kun_activity.KUN_ROSTER`（經 `wem.kun_roster_eids()`，
#      **不抄第二份**）＝`SPEC-RUNWINDOW.md` §4.2「只算 本站機隊 ∩ 綑名冊」的同一份判定。
#  ★ 補進來的機台**不是靜默 0**：整窗一段 `不明`（conf=low）＋ `roster_nosig` 旗，
#    呈現層必須明說「本頁顯示期間內無生產訊號」。有了那段不明，工單 overlay 才有東西可以
#    著色 ⇒ cmms 四類（維修／計畫保養／等料／等外包）在那一列**看得見**（原本無處可疊）。
#  ⚠ **降級絕不靜默**：較寬窗查詢失敗 ⇒ 回 `(None, "error:…")`，呼叫端沿用現行清單並**標示**。
#  ⚠ 本節**不在 `get_live` 裡**：`app.load()` 的快取被 `/dispatch`／維修體質 Δ 共用，
#    在資料層直接補會把它們一起換掉（`/dispatch` 在 §4.4 零接觸清單上）。
#    ⇒ 補列在**呼叫端**（route 層）做，逐頁 opt-in。
# ======================================================================================
ROSTER_DAYS = int(os.environ.get("BOARD_ROSTER_DAYS", "30"))
ROSTER_FLEET_TTL = float(os.environ.get("BOARD_ROSTER_FLEET_TTL", "1800"))
ROSTER_NOSIG_NOTE = "名冊補列(本頁期間無生產訊號)"
_ROSTER_FLEET_CACHE = {}


ROSTER_ANCHOR_MAX_DAYS = int(os.environ.get("BOARD_ROSTER_ANCHOR_MAX_DAYS",
                                            str(2 * ROSTER_DAYS + 2)))


def roster_wide_days_anchored(win_start, now=None):
    """#182-P2 F3：寬窗名冊**錨到窗起**的天數＝`(now − win_start).days + 1 + ROSTER_DAYS`。

       `fetch_fleet(N)` 的 SQL 錨在 `GETDATE()`（凍結模組、不動）⇒ 回補越舊的日子，固定 30 天
       的名冊越看不到「那一天前 30 天有段」的機台。只改**參數**、SQL 一個位元不動；結果仍是
       超集（窗後才出現的機也會被補 ⇒ 分母方向保守）。上限 `ROSTER_ANCHOR_MAX_DAYS`
       （預設 62＝日曆 30 天涵蓋 ×2＋2；rw 軸本來就只能回補日曆涵蓋內的日子）。
       ⚠ 30 天以上的 `fetch_fleet` 窗**沒有 perf 實證**（B4 P12a 只量到 30d≈3.5s）；失敗＝
       `roster_fleet` 回 `error:`、`roster_pad=False` 誠實降級（F1 的空軌→stub 不靠它）。"""
    now = now or datetime.now()
    try:
        lag = max(0, (now - win_start).days) + 1
    except Exception:
        return ROSTER_DAYS
    return min(ROSTER_DAYS + lag, max(ROSTER_DAYS, ROSTER_ANCHOR_MAX_DAYS))


def roster_fleet(cls, days, source="live", *, wide_days=None):
    """較寬窗的機台目錄（**只取清單、不取 segments**）→ `([(eid, desc)…] 或 None, status)`。

       status：`"ok"`＝查到了／`"same"`＝本窗已 ≥ ROSTER_DAYS（現行清單就是寬窗清單、無須第二查）／
       `"off"`＝非 live（demo/snapshot 無 MES 目錄可查）／`"error:…"`＝失敗（呼叫端誠實標示）。
       ⚠ 只呼叫凍結 `mt.fetch_fleet`（零新 SQL、query 本體一個位元不動）。
       模組級 TTL 快取：避免每次請求都多打一次 30d 查詢。
       ★ `wide_days`（#182-P2 F3、kw-only）＝呼叫端**明示**寬窗天數（日帳用
         `roster_wide_days_anchored` 錨到窗起）；給了就不走 `"same"` 短路。預設 None＝逐位元舊行為。"""
    if not _live_route(source):             # §5 wd:429（S；清冊 §1 #3 M14）
        return (None, "off")
    if wide_days is not None:
        try:
            wide = max(int(wide_days), ROSTER_DAYS)
        except (TypeError, ValueError):
            wide = ROSTER_DAYS
    else:
        try:
            wide = max(int(days or 0), ROSTER_DAYS)
        except (TypeError, ValueError):
            wide = ROSTER_DAYS
        if wide <= (days or 0):
            return (None, "same")
    key = (cls, wide)
    hit = _ROSTER_FLEET_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ROSTER_FLEET_TTL:
        return hit[1]
    try:
        if _synth_proc():                   # 清冊 §1 #3：進程旗標分派（寬窗目錄，錨＝合成時鐘）
            fleet = _wsio().fetch_fleet(wide, cls)
        else:
            conn, cur = mt._connect()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                fleet = mt.fetch_fleet(cur, wide, cls)
            finally:
                conn.close()
        res = ([(e, html.unescape(d) if isinstance(d, str) else d) for e, d in fleet], "ok")
    except Exception as e:
        return (None, f"error:{type(e).__name__}")
    _ROSTER_FLEET_CACHE[key] = (time.monotonic(), res)
    return res


def roster_stub_machines(data, wide_fleet, roster_eids=None):
    """「名冊內、但本窗機隊裡沒有」的機台 → stub machine dict 清單（純函式、零 I/O）。

       ★ C-RW18：**整窗都在 down 的機台不得從清單消失**。stub 的形＝與 `_build` 產出的
       machine dict 同構（`records`／`agg`／`n_seg`…全在），差別只有：
         · `records`＝整窗一段 `不明`（conf=low、note＝`ROSTER_NOSIG_NOTE`）
           ——工單 overlay 靠這段才有東西可切、cmms 四類才著得上色；
         · `roster_nosig=True`＝呈現層據此明說「本頁顯示期間內無生產訊號」（**不是靜默 0**）。
       `wide_fleet=None`（查不到／非 live）⇒ 回 `[]`（呼叫端另行標示降級、絕不假裝完整）。"""
    if not wide_fleet or not data:
        return []
    def _dt(x):
        return x if isinstance(x, datetime) else _parse_dt(x)

    ws, we = _dt(data.get("win_start")), _dt(data.get("win_end") or data.get("mes_now"))
    if ws is None or we is None or we <= ws:
        return []
    if roster_eids is None:
        roster_eids = wem.kun_roster_eids()
    have = {m.get("eid") for m in (data.get("machines") or ())}
    out = []
    for eid, desc in wide_fleet:
        if eid in have or eid not in roster_eids:
            continue                      # 名冊外不補（C-RW9：這個框對它們不適用）
        rec = {"eid": eid, "state": "不明", "conf": "low", "start": ws, "end": we,
               "sec": (we - ws).total_seconds(), "note": ROSTER_NOSIG_NOTE}
        out.append({"eid": eid, "desc": html.unescape(desc) if isinstance(desc, str) else desc,
                    "records": [rec], "n_seg": 0, "micro_idle_sec": 0.0,
                    "agg": aggregate([rec]), "_a": None, "zero_len_only": False,
                    "roster_nosig": True})
    return sorted(out, key=lambda m: m["eid"])


def normalize_empty_tracks(data, roster_eids=None):
    """#182-P2 F1（**只給日帳建帳路徑用**）：名冊機「窗內零段」一律換成 stub 形 → `(data', eids)`。

       病理（`_INVEST-NOSIG-DRIFT-20260925.md` §0–§4）：時間軸機台清單是 `fetch_fleet(fetch_days)`
       ＝**錨在建帳當下**的滾動集合。名冊機「當天零段、但窗起到建帳當下之間有段」會以**空軌**
       （`records=[]`、非 `zero_len_only`）進場 ⇒ `roster_stub_machines` 看它已在 `have` 裡、不補
       stub ⇒ 引擎 nosig 閘只數 record ⇒ 同一天同一份 MES 歷史，會因**建帳時點不同**在 nosig 與
       「可測、0 生產」之間翻（全月 44 翻案、314 筆反證 0 反例）。
       修法＝窗內沒有證據的名冊機（`records` 空 ∧ 非 `zero_len_only`）**不論從哪條路進場**
       一律用 stub 形（整窗 `不明`、`roster_nosig`）＝C-RW18「照樣進分母」的同一個表示法。
       · 名冊外的空軌不動（A8 本來就把它移出 rw 分母；不是這條規則的對象）。
       · 窗內只有零工期段的機台（`zero_len_only`）不動＝T6 另有處置（F2 站級常數）。
       · 回**新 dict／新 list**（`data["machines"]` 是 `app.load()` 快取裡那份，絕不 mutate）。
       `eids`＝被換成 stub 的機台（排序；落盤證據 `rw.empty_track_eids`）。"""
    if not data:
        return data, []
    if roster_eids is None:
        roster_eids = wem.kun_roster_eids()
    keep, conv = [], []
    for m in data.get("machines") or ():
        eid = m.get("eid") if isinstance(m, dict) else None
        if (eid and eid in roster_eids and not (m.get("records") or ())
                and not m.get("zero_len_only")):
            conv.append((eid, m.get("desc") or ""))
            continue
        keep.append(m)
    if not conv:
        return data, []
    base = {**data, "machines": keep}
    stubs = roster_stub_machines(base, conv, roster_eids)
    got = {s["eid"] for s in stubs}
    if got != {e for e, _d in conv}:                 # 窗壞形 ⇒ stub 造不出來 ⇒ 原樣退回（不丟機台）
        return data, []
    return with_roster_fill(base, stubs), sorted(got)


def with_roster_fill(data, stubs):
    """把 stub 機台併進 data（**回新 dict、絕不 mutate 快取裡那份**）。

       `fleet` 聚合跟著重算＝那段不明時間本來就該進機隊帳（原本整台不見＝分母少一台）。
       stubs 空 ⇒ 原樣回傳（逐位元不變）。"""
    if not stubs or not data:
        return data
    machines = list(data.get("machines") or ()) + list(stubs)
    machines.sort(key=lambda m: m.get("eid") or "")
    all_recs = [r for m in machines for r in (m.get("records") or ())]
    return {**data, "machines": machines, "fleet": aggregate(all_recs)}


# ======================================================================================
# W8 工單態 overlay（§14.4.1 DEMO→真值；2026-07-07 cmms W8/A3 live 後落地）
# ------------------------------------------------------------------------------------
#   把 cmms 工單的『可計停機』時間疊到每台機的『不明』區間上，裂解成 /equipment 那套
#   machine-time 分類——**生產永不被覆蓋（MES 正面訊號優先）**、
#   剩餘不明仍留不明（idle vs 非排定＝SALTMARSH 排程源 G1 未接，誠實不猜）。
#
#   ★ 消費紀律（同 /equipment，數字**與語言**跨頁一致；2026-08-16 Chris 裁決＝
#     「Board 展示 machine time 的分類與 legend，全站 100% 統一到 /equipment 那套」）：
#     - S12 分階段停機口徑＝**重用 board_eqmeeting.wo_segments**（不重寫 _phase_timeline/countable）：
#       uptime-hold 子類（等機台空檔 WAITING_MACHINE_TIME / 試跑 TEST_RUN）＝機台可生產、**不上色**（留不明）。
#     - **S12 態直通、不收斂**：等料(停機)/等外包(停機) 以自己的態上色（紫/琥珀＝/equipment 同色）。
#       ⚠ 2026-08-16 前這裡曾把 hold 態收斂回母 work_type 態（「此頁只用 4 態 palette」）——
#       **已由 Chris 裁決廢除**：那個 4 態 palette（維修/計畫保養/調機/其他維護）是 eMaint 歷史單
#       的分類，新 cmms 已無 machine-time 的 調機/其他維護 類。**勿把收斂加回來。**
#     - legacy（eMaint 調機/其他維護(子類待定)）：態名直通、**顯示端一律 legacy 灰**
#       （app.STATE_COLORS＝/equipment --st-legacy 同值）＝「真出現→灰＋具名」防呆網，不進主 legend 語言。
#     - 重疊工單同機＝interval-union、每一刻恰一態（deterministic：活躍窗較早者優先、平手取較小 wo_no）。
#   純函式、零 I/O、不改凍結核心；operate on copies（不 mutate 傳入 data／cache）。
# ======================================================================================
def _wo_down_colored(wos, win_start, win_end):
    """單一 EID 的 normalize_wo 列表 → 可計停機的『上色區間』（可能互相重疊，_split_records 再解析）。
       重用 wem.wo_segments（S12 分階段）取 countable 段；**態直通（含 等料/等外包）、零收斂**。
       回 [{start,end,state,conf,wno,prio}]（prio＝(start, wno)＝classify_gaps 同序）。"""
    segs = wem.wo_segments(wos, win_start, win_end)
    out = []
    for s in segs:
        if not s.get("countable", True):          # uptime-hold（機台可生產）→ 不上色、留不明
            continue
        a, b = wem._coerce_dt(s.get("start")), wem._coerce_dt(s.get("end"))
        if a is None or b is None or b <= a:
            continue
        wno = s.get("work_order_no")
        out.append({"start": a, "end": b, "state": s.get("state"),
                    "conf": s.get("confidence") or "high",
                    "wno": wno, "prio": (a, wno if wno is not None else 0)})
    return out


def _split_records(recs, colored, eid):
    """把一台機的 records 裡的『不明』段依 colored 上色區間切分（生產等其他態原樣保留、不覆蓋）。
       每個不明 record → 依 colored 端點切子段：完整落在某上色區間者著該態（deterministic prio），
       否則留不明。相鄰同態子段合併。純函式；子段為新 dict（不 mutate 原 record）。"""
    if not colored:
        return list(recs)
    out = []
    for r in recs:
        if r.get("state") != "不明":
            out.append(r)                          # 生產/其他態＝MES 正面訊號優先、原樣保留
            continue
        rs, re = wem._coerce_dt(r.get("start")), wem._coerce_dt(r.get("end"))
        if rs is None or re is None or re <= rs:
            out.append(r)
            continue
        pts = {rs, re}
        for c in colored:
            s = c["start"] if c["start"] > rs else rs
            e = c["end"] if c["end"] < re else re
            if s < e:
                pts.add(s)
                pts.add(e)
        spts = sorted(pts)
        note = r.get("note")
        conf0 = r.get("conf", "low")
        pieces = []
        for a, b in zip(spts, spts[1:]):
            best = None
            for c in colored:
                if c["start"] <= a and c["end"] >= b and (best is None or c["prio"] < best["prio"]):
                    best = c
            if best is None:
                seg = {"eid": eid, "state": "不明", "conf": conf0,
                       "start": a, "end": b, "sec": (b - a).total_seconds()}
                if note:
                    seg["note"] = note
            else:
                seg = {"eid": eid, "state": best["state"], "conf": best["conf"],
                       "start": a, "end": b, "sec": (b - a).total_seconds(),
                       "source": "cmms_workorder", "work_order_no": best["wno"]}
            if pieces and pieces[-1]["end"] == seg["start"] \
                    and pieces[-1]["state"] == seg["state"] \
                    and pieces[-1].get("work_order_no") == seg.get("work_order_no"):
                pieces[-1]["end"] = seg["end"]
                pieces[-1]["sec"] += seg["sec"]
            else:
                pieces.append(seg)
        out.extend(pieces)
    return out


def _clip_colored_to_windows(colored, windows_by_wno):
    """把 `_wo_down_colored` 的上色區間裁到**該單的機器視角窗**（A10 擴散 R1、2026-08-14）。

       `windows_by_wno`＝`{work_order_no: [(s, e), …]}`（＝`wscope.machine_scope_windows`）。
       某單**沒有** key ⇒ 該單不受裁（＝非母機單、或讀不到範圍事件的降級路徑＝高估側）；
       有 key 但空 list ⇒ 該單整段不上色（範圍全程不含母機＝機器沒有整台停）。
       純函式、不 mutate 輸入。"""
    out = []
    for c in colored or []:
        wins = windows_by_wno.get(c.get("wno"), None)
        if wins is None:
            out.append(c)
            continue
        for w_s, w_e in wins:
            a = c["start"] if c["start"] > w_s else w_s
            b = c["end"] if c["end"] < w_e else w_e
            if b > a:
                cut = dict(c)
                cut["start"], cut["end"] = a, b
                out.append(cut)
    out.sort(key=lambda c: c["prio"])
    return out


def overlay_wo_states(data, wos_by_eid, *, now=None, scope_windows=None):
    """把 cmms 工單可計停機疊到 canonical data 的『不明』區間 → 新 data（不 mutate 傳入/cache）。
       wos_by_eid＝{eid: [normalize_wo 輸出]}（app 層由 wem.group_wos_by_eid 分好；demo 由 demo_window_wos 合成）。
       每台機重算 agg、fleet 重算 aggregate（不明秒↓＝§25 訊號真下降；生產秒不變＝util 不變）。

       ★ `scope_windows`＝`{eid: {wo_no: [(s,e), …]}}`（A10 擴散 R1、2026-08-14）：
         母機那幾台的著色先裁到**機器視角**窗——範圍縮小到某幾個工位時，那台機器不是
         整台停（其餘工位還在生產），整條帶塗滿＝說謊。
         **`None`／缺 eid ⇒ 逐位元同加此參數前**（其餘機台一個位元不動）。"""
    if not data or not wos_by_eid:
        return data
    win_start = wem._coerce_dt(data.get("win_start"))
    win_end = wem._coerce_dt(data.get("win_end")) or wem._coerce_dt(data.get("mes_now"))
    if win_start is None or win_end is None or win_end <= win_start:
        return data
    scope_windows = scope_windows or {}
    new_machines, all_recs = [], []
    for m in (data.get("machines") or []):
        recs = m.get("records") or []
        wos = wos_by_eid.get(m.get("eid"))
        colored = _wo_down_colored(wos, win_start, win_end) if wos else []
        if colored and m.get("eid") in scope_windows:
            colored = _clip_colored_to_windows(colored, scope_windows[m["eid"]])
        if not colored:
            new_machines.append(m)
            all_recs.extend(recs)
            continue
        new_recs = _split_records(recs, colored, m.get("eid"))
        new_recs.sort(key=lambda r: r["start"])
        nm = dict(m)
        nm["records"] = new_recs
        nm["agg"] = aggregate(new_recs)
        new_machines.append(nm)
        all_recs.extend(new_recs)
    nd = dict(data)
    nd["machines"] = new_machines
    nd["fleet"] = aggregate(all_recs)
    nd["wo_overlay"] = True
    return nd


def overlay_children_wo_states(children, inherited_by_eid, win_start, win_end):
    """子工位附掛列的工單著色（A10 擴散 R1、2026-08-14）→ 新的 `{parent: [child…]}`。

       `children`＝`expand_machines_band` 輸出；`inherited_by_eid`＝
       `board_scope.expand_for_children` 輸出（**已含 2026-08-14 的 dormant 翻面**
       ⇒ 長停工位的 clip 全空 ⇒ 該列自然一格都不上色，不必在這裡再判一次）。

       ★ 這修的是 `expand_machines_band` docstring 那句「子工位列無工單著色（誠實、無單可疊）」
         ——那句在 A10 之前是對的（工單開在母機、子工位確實沒有自己的單）；A10 把母單展開到
         子工位之後，**有單可疊了**，繼續留白就是把已知的事實藏起來。
       ★ 段的來源＝`wem._inherited_segments`（母單 countable 段 ∩ clip）＝**與 `/equipment`
         子工位列逐段同一份**；跨頁不會出現「設備頁紅、稼動頁白」。
       ★ 只碰 `records`／`agg`（形同 `overlay_wo_states` 對父機列做的事）；
         `nosig`／`parent_eid`／`cov_note` 等旗標原樣保留。純函式、**不 mutate 輸入**
         （`load_band_children` 的結果在快取裡，mutate 會毒化整個 TTL）。"""
    if not children or not inherited_by_eid:
        return children
    ws, we = wem._coerce_dt(win_start), wem._coerce_dt(win_end)
    if ws is None or we is None or we <= ws:
        return children
    out = {}
    for parent, kids in children.items():
        new_kids = []
        for k in kids or []:
            ents = inherited_by_eid.get(k.get("eid"))
            segs = wem._inherited_segments(ents, ws, we) if ents else []
            # S12 態直通（同 `_wo_down_colored`；2026-08-16 起等料/等外包不再收斂回母態）
            colored = []
            for s in segs:
                if not s.get("countable", True):
                    continue
                a, b = wem._coerce_dt(s.get("start")), wem._coerce_dt(s.get("end"))
                if a is None or b is None or b <= a:
                    continue
                state, conf = s.get("state"), s.get("confidence")
                wno = s.get("work_order_no")
                colored.append({"start": a, "end": b, "state": state, "conf": conf or "high",
                                "wno": wno, "prio": (a, wno if wno is not None else 0)})
            if not colored:
                new_kids.append(k)
                continue
            colored.sort(key=lambda c: c["prio"])
            recs = _split_records(k.get("records") or [], colored, k.get("eid"))
            recs.sort(key=lambda r: r["start"])
            nk = dict(k)
            nk["records"] = recs
            nk["agg"] = aggregate(recs)
            new_kids.append(nk)
        out[parent] = new_kids
    return out


def demo_window_wos(data):
    """demo：由 canonical data 的『不明』段合成確定性契約形工單（放進既有 gap 內、per-EID 種子；
       跨 fleet 涵蓋 /equipment 那套四個計停機態（維修/計畫保養/等料(停機)/等外包(停機)）
       ＋每 5 台 1 台 uptime-hold 示範『機台可生產不上色』）。
       ★ 2026-08-16 起 demo **不再合成 legacy work_type**（CONVERSION/PART REQUEST）——
         與 /equipment demo 同紀律（「demo 亦不合成」legacy）；等料/等外包改由 REACTIVE 單的
         ON_HOLD(WAITING_PARTS/WAITING_VENDOR) 相位產生＝與真單同一條 S12 路徑。
       回 {eid: [wo]}（normalize_wo 形，wo_segments 直接消費）。"""
    machines = (data or {}).get("machines") or []
    HOLD = [None, None, "WAITING_PARTS", "WAITING_VENDOR"]   # 0=REACTIVE 全修 1=PM 2/3=hold 拆色
    out, wno, pcount = {}, 90000, 0
    for i, m in enumerate(machines):
        eid = m.get("eid")
        if not eid:
            continue
        unk = [r for r in (m.get("records") or []) if r.get("state") == "不明"]
        if not unk:
            continue
        r = max(unk, key=lambda r: r.get("sec") or 0.0)
        s, e = wem._coerce_dt(r.get("start")), wem._coerce_dt(r.get("end"))
        if s is None or e is None:
            continue
        span = (e - s).total_seconds()
        if span < 1800:                            # 太短的不明段不放單（避免碎屑）
            continue
        a = s + timedelta(seconds=span * 0.2)
        b = s + timedelta(seconds=span * 0.8)
        wno += 1
        if i % 5 == 4:                             # uptime-hold 示範：等機台空檔＝不上色、留不明
            out[eid] = [{"work_order_no": wno, "asset_id": eid, "work_type": "PM",
                         "opened_at": a, "closed_at": None,
                         "status_history": [
                             {"status": "OPEN", "at": a, "hold_reason": None},
                             {"status": "ON_HOLD", "at": a + timedelta(seconds=span * 0.1),
                              "hold_reason": "WAITING_MACHINE_TIME"}]}]
        else:
            k = pcount % 4
            pcount += 1
            if k == 1:                             # 計畫保養
                out[eid] = [{"work_order_no": wno, "asset_id": eid, "work_type": "PM",
                             "opened_at": a, "closed_at": b, "status_history": []}]
            elif HOLD[k]:                          # 維修單中途進 等料/等外包（前半紅、後半紫/琥珀）
                mid = a + timedelta(seconds=(b - a).total_seconds() * 0.5)
                out[eid] = [{"work_order_no": wno, "asset_id": eid, "work_type": "REACTIVE",
                             "opened_at": a, "closed_at": b,
                             "status_history": [
                                 {"status": "OPEN", "at": a, "hold_reason": None},
                                 {"status": "ON_HOLD", "at": mid, "hold_reason": HOLD[k]}]}]
            else:                                  # 維修（全程）
                out[eid] = [{"work_order_no": wno, "asset_id": eid, "work_type": "REACTIVE",
                             "opened_at": a, "closed_at": b, "status_history": []}]
    return out


# ======================================================================================
# Up/Down 板（/updown）— MES 證據供給層（"管道逐一打通、逐源換真"的第一源）
#   輸出＝ station_evidence：[(cls, [(eid, desc, {"mes_last_h": float|None}), …]), …]
#   消費者兩個：board_updown.build_board_live（套五階裁決階梯）＋ /equipment 多源 Now（app._eqm_mes_evidence）。
#   ★ 鐵則：只重用凍結 fetch_fleet/fetch_segments（絕不自寫新 SQL）；EID 全來自真 fleet（不捏造）；
#     MES 沉默＝mes_last_h None＝**證據缺席**，兩個消費者都不得據此推 DOWN（gap≠fault）。
#     （/updown 把缺席歸 UNKNOWN；/equipment 歸 UP·待命＝沒工單就預設可用，2026-07-10 拍板。）
# ======================================================================================
# ★ 2026-09-07 Chris 裁決：這是「生產」的閉集，不是 `designation != R&D` 的猜測。
#   Engineering (R&D) 是維修／製程試跑訊號，永遠不得抬高 production-grade recency。
PRODUCTION_DESIGNATIONS = ("Production", "Engineering (Baseline)")

# `mes_timeline.fetch_segments` 是 frozen。這裡保留其 SQL 的逐字副本，只在 SELECT 末尾
# 加 `, se.matDesignation`；沒有該欄的舊 MES 以 COL_LENGTH guard 明確退回**同一條** frozen SQL。
# 不可把這段併回 mes_timeline，也不可「順手」改任何 WHERE / OPTION / 參數順序。
FROZEN_FETCH_SEGMENTS_SQL = """
    SELECT se.astEquipmentID, se.stpStartTime, se.stpEndTime
    FROM dbo.segment_equipment_v se
    WHERE se.SegmentClassID = ?
      AND se.stpStartTime >= DATEADD(day, -?, GETDATE())
      AND se.stpStartTime <= GETDATE()
      AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime
    OPTION (RECOMPILE)
    """
FETCH_SEGMENTS_DESIG_SQL = FROZEN_FETCH_SEGMENTS_SQL.replace(
    "se.stpStartTime, se.stpEndTime\n", "se.stpStartTime, se.stpEndTime, se.matDesignation\n", 1)
COL_LENGTH_DESIGNATION_SQL = (
    "SELECT CASE WHEN COL_LENGTH('dbo.segment_equipment_v', 'matDesignation') IS NULL "
    "THEN 0 ELSE 1 END"
)


def fetch_segments_desig_guard(cur):
    """欄位存在性守門。False 時 caller 必須跑完全 frozen 的 fallback query。"""
    cur.execute(COL_LENGTH_DESIGNATION_SQL)
    row = cur.fetchone()
    return bool(row and row[0])


def fetch_segments_desig(cur, eids, days, cls=mt.CAL_CLASS, desig_available=True):
    """fetch_segments 的 designation-aware sibling（不碰 frozen 模組）。

    回 `{eid: [(start, end, designation|None)]}`；EID folding、無 IN-list、時間窗與
    frozen fetch_segments 完全同義。`desig_available=False` 時 SQL 文本就是
    :data:`FROZEN_FETCH_SEGMENTS_SQL`，只把第三欄補 None。

    ★ #123 站別折疊（2026-09-10、`SPEC-RUNWINDOW.md` §10.8-10）：站別展開走
      **`mt.classes_for(cls)`（唯一出處，本檔不另抄表）**——逐別名各送一次同一條 SQL、
      回列串接。**SQL 常數一個位元不動**；無別名的 37 站迴圈只跑一次＝逐位元原行為。
      不在這裡折的後果與 `fetch_segments` 同型：跑 Kestrel 的那五台在 /updown、
      /equipment 的 recency 上會被讀成「窗內無段」。
    """
    if not eids:
        return {}
    eidset = set(eids)
    sql = FETCH_SEGMENTS_DESIG_SQL if desig_available else FROZEN_FETCH_SEGMENTS_SQL
    rows = []
    for c in mt.classes_for(cls):
        cur.execute(sql, c, days)
        rows.extend(cur.fetchall())
    out = {}
    for row in rows:
        eid, st, en = row[:3]
        feid = mt.fold_eid(eid)
        if feid is not None and feid in eidset:
            designation = row[3] if desig_available and len(row) > 3 else None
            out.setdefault(feid, []).append((st, en, designation))
    return out


def _mes_recency_evidence(ivs, now, desig_available):
    """同一批段 → production-grade 與 any-segment 的原子 evidence（純函式）。"""
    newest_any = max(ivs or [], key=lambda x: (x[1], x[0]), default=None)
    newest_prod = max((x for x in (ivs or []) if x[2] in PRODUCTION_DESIGNATIONS),
                      key=lambda x: (x[1], x[0]), default=None) if desig_available else newest_any
    def _age(rec):
        return max(0.0, (now - rec[1]).total_seconds() / 3600.0) if rec else None
    return {
        "mes_last_h": _age(newest_prod),
        "mes_any_last_h": _age(newest_any),
        "mes_last_designation": newest_any[2] if newest_any and desig_available else None,
        "mes_meta": {"desig_available": bool(desig_available)},
    }


def _updown_evidence_live(budget_sec=None, fleet_days=None, lookback_days=None):
    """live：逐站 fetch_fleet（目錄窗）取真機台 + fetch_segments（evidence 窗）取最近生產 age。
       budget_sec＝整體時間預算：逾時則 raise TimeoutError（→ app 層該卡誠實 unavailable、**不重打第二輪**）。
       fleet_days／lookback_days＝覆寫兩窗（None＝用 UD_* 預設）。/equipment 的 ⚠ 久靜判斷需要看得比
       24h 遠，故它傳自己的寬窗（EQM_MES_DAYS）；**/updown 不傳＝行為與過去逐位元相同**。
       單機 age＝(now − 最近一段 end)/3600h；窗內無段→None。now＝MES server clock（穩定錨）。
       ★ 兩窗相等時（/equipment 用法）fleet 與 segments 過濾條件同為 stpStartTime ≥ now−N 天
         → fleet 內每台必有段 → age 必非 None；「缺 eid」單一語意＝窗內無任何生產段。
       ⚠ 已知盲點（Q13 2026-07-10 實測定案：真實但罕見）：凍結 fetch_segments 只回封閉段
       （stpEndTime IS NOT NULL）→ 正在跑「開放中長段」的機台，age 取自前一段收尾。30 天窗內段數極大，其中
       僅極少數單段 >2h（最長者為 Wirebond 跨夜 run／EOLRunner）。
       後果在現行口徑下已無害：這些機台顯 UP·待命 而非 UP·生產中（**顏色狀態不變、只少一個 badge**），
       且 18.3h ≪ 久靜門檻（當時 72h；R45 起 `wem.QUIET_H`＝168h）＝不會誤閃 ⚠。故**不補開放段判定、不動凍結模組**（Chris 拍板）。"""
    deadline = (time.monotonic() + budget_sec) if budget_sec else None
    fleet_days = UD_FLEET_DAYS if fleet_days is None else fleet_days
    lookback_days = UD_LOOKBACK_DAYS if lookback_days is None else lookback_days
    if _synth_proc():                       # 清冊 §1 #4–#6（M1＋M11）：進程旗標分派
        return _updown_evidence_synth(deadline, budget_sec, fleet_days, lookback_days)
    conn, cur = mt._connect(login_timeout=UD_LOGIN_TIMEOUT)   # #23(b) 連不上快退、不改窗行為
    conn.timeout = UD_QUERY_TIMEOUT
    out = []
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        desig_available = fetch_segments_desig_guard(cur)
        for cls in mt.EQP_STATIONS:
            if deadline and time.monotonic() > deadline:
                raise TimeoutError(f"updown live fetch exceeded {budget_sec}s budget (at {cls})")
            fleet = mt.fetch_fleet(cur, fleet_days, cls)         # 凍結：真機台 EID（排模組）
            if not fleet:
                continue
            segs = fetch_segments_desig(cur, [e for e, _ in fleet], lookback_days, cls,
                                        desig_available=desig_available)
            machines = []
            for eid, desc in fleet:
                ivs = segs.get(eid) or []
                machines.append((eid, desc, _mes_recency_evidence(ivs, now, desig_available)))
            out.append((cls, machines))
    finally:
        conn.close()
    return out


def _updown_evidence_synth(deadline, budget_sec, fleet_days, lookback_days):
    """合成進程的 `_updown_evidence_live`：同一個逐站迴圈、同一支 `_mes_recency_evidence`；
       窗錨＝`wsynth_io.now()`、desig 欄恆可用（`fetch_segments_desig_guard` 恆 True）。"""
    io = _wsio()
    now = io.now()
    desig_available = io.fetch_segments_desig_guard()
    out = []
    for cls in mt.EQP_STATIONS:
        if deadline and time.monotonic() > deadline:
            raise TimeoutError(f"updown synth fetch exceeded {budget_sec}s budget (at {cls})")
        fleet = io.fetch_fleet(fleet_days, cls, now=now)
        if not fleet:
            continue
        segs = io.fetch_segments_desig([e for e, _ in fleet], lookback_days, cls, now=now,
                                       desig_available=desig_available)
        machines = []
        for eid, desc in fleet:
            ivs = segs.get(eid) or []
            machines.append((eid, desc, _mes_recency_evidence(ivs, now, desig_available)))
        out.append((cls, machines))
    return out


def _updown_evidence_snapshot(path):
    """snapshot：走與 live 同一程式路徑（有什麼站算什麼站）——由已存 timeline 快照的分類 records
       推每台『最近一段生產(state=生產) end』距 mes_now 的 age。缺檔/壞檔/無 records → []（優雅降級、不炸）。
       快照通常僅 CAL 站 → cls 一律 CAL_CLASS；有 record 但無生產段的機台 → age None → UNKNOWN。"""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    snap_now = _parse_dt(data.get("mes_now")) or datetime.now().replace(microsecond=0)
    last_prod, descs = {}, {}
    for r in (data.get("records") or []):
        eid = r.get("eid")
        if not eid:
            continue
        descs.setdefault(eid, r.get("desc", "") or "")
        if r.get("state") == "生產":
            e = _parse_dt(r.get("end"))
            if e and (eid not in last_prod or e > last_prod[eid]):
                last_prod[eid] = e
    machines = []
    for eid in sorted(descs):
        le = last_prod.get(eid)
        mes_h = max(0.0, (snap_now - le).total_seconds() / 3600.0) if le else None
        # 既有 timeline snapshot 沒有 matDesignation：保持舊的 age，並明示不可把它當 grade 判斷。
        machines.append((eid, descs[eid], {"mes_last_h": mes_h, "mes_any_last_h": mes_h,
                                           "mes_last_designation": None,
                                           "mes_meta": {"desig_available": False}}))
    return [(mt.CAL_CLASS, machines)] if machines else []


def get_updown_evidence(source, snapshot_path=None, budget_sec=None,
                        fleet_days=None, lookback_days=None):
    """MES 近期生產證據（live/snapshot）。demo＝全模擬、由 board_updown 自產（不經此）。
       消費者：/updown（用 UD_* 預設窗）與 /equipment 多源 Now（傳自己的寬窗、支撐 ⚠ 久靜）。
       fleet_days/lookback_days 只作用於 live；snapshot 讀已存檔、無窗可調（照檔內容給）。"""
    if _live_route(source):                 # §5 wd:968（S）
        return _updown_evidence_live(budget_sec, fleet_days=fleet_days, lookback_days=lookback_days)
    if source == "snapshot":
        return _updown_evidence_snapshot(snapshot_path)
    raise ValueError("get_updown_evidence: source 只支援 live|snapshot（demo 全模擬由 board_updown 自產）")


# ---- lastprod 狀態檔 → /equipment 多源 Now 的 MES 證據（2026-08-17 換源） -------------------
#   `core_lastprod_push` 每 10 分鐘把全 roster（⊇ EQMT_CATALOG）30 天窗的「最後生產段結束時刻」
#   寫進 `_state/last_production.json`——本 reader 把它折成 {eid: age_h}，讓 Now 欄：
#     · 零新 MES 查詢（cache miss 時省掉 76–110s 探測）；
#     · MES 斷線免疫（檔在本地；08001 連線錯誤那型失敗態幾乎絕跡）；
#     · 「近 N 天無生產」有依據（整窗掃描＝C-LP6 級主張，不是「探測窗看不到」）。
#   ★ 消費紀律（SPEC-LASTPROD-PUSH v1.2 §狀態檔第二消費者）：
#     · **唯讀**——絕不寫回；狀態檔的寫入權只在 core_lastprod_push。
#     · **schema 明列閉集 {v4}**——不認識就整檔拒收（回 None ⇒ app 退回 live 探測＝
#       安全側）；push 端每次升版**必須**同輪更新這裡（該 SPEC 已登記；2026-08-22 v3 照辦）。
#     · **陳舊分級（#23(a)，2026-08-18 Chris 拍板）**——updated_at 超過 max_age_s（呼叫端傳
#       **硬上限**、預設 6h）才整檔拒收；其間的「稍舊」檔照收、meta 帶 age_s/updated_at 讓
#       呼叫端標 as-of。理由＝08-17 實測 push 與頁面探測打同一台 SQL Server、MES 一死兩腿齊斷：
#       整份拒收去打一個很可能也死的探測，嚴格劣於誠實顯示稍舊的值（末段時刻是絕對時戳、
#       檔舊 40min 只影響「updated_at 之後才復跑的機台」那一小塊，age 本身照 now 算仍精確）。
#       超過硬上限仍拒收＝不拿殭屍檔宣稱「30 天無生產」。
#     · **覆蓋守門**——covered_from 沒蓋滿 lookback 窗（partial 尚未自癒）⇒ 拒收；
#       「缺 eid ⇒ 無生產」這句話只有整窗覆蓋時才為真。
# ★ 明列閉集（2026-09-07）：v4 的 prod_* 是 reader 消費的語意欄，因此 v3 必須整檔拒收，
#   迫使 push 全窗重建；不得為了部署方便把 generic end 誤當 production-grade end。
# ★ 2026-09-08 v5（`SPEC-LANDING.md` §18.23）：**v4 與 v5 同時收**。理由與 v3→v4 那次相反、
#   而且是對稱的：v5 只是在信封層**加**一個 `sub_units` 鍵，`eids` 的每一個欄與語意
#   **一個位元都沒動** ⇒ 拒收 v4 換不到任何正確性，只換來「新碼上線、push 還沒跑過」
#   那段空窗期 `/equipment` 白白退回 MES 探測。缺 `sub_units`（v4）＝子工位一律「不知道」
#   ＝空心格，那正是 C-LD2 要的答案。
# ★ 2026-09-08 v6（#104(b)、`SPEC-LANDING.md` §18.26）：**v4／v5／v6 三版同時收**——
#   同一條理由再套一次：v6 只是在信封層**加**一個 `station_lot` 鍵（站別末筆生產級 lot 段），
#   `eids` 與 `sub_units` 的每一個欄與語意**一個位元都沒動**。缺 `station_lot`（v4/v5 檔）
#   ＝`{}`＝「一站都不知道」⇒ landing 一台都不推斷（C-LD2「不知道≠沒在做」），
#   那正是「新碼上線、push 還沒跑過」那段空窗期應該有的答案。
LASTPROD_STATE_SCHEMAS = ("board.lastprod.state.v4", "board.lastprod.state.v5",
                          "board.lastprod.state.v6")


def parse_lastprod_state(doc, now, max_age_s):
    """狀態檔 dict → ({eid: age_h}, meta) ／ 拒收 → (None, reason)。純函式。

    meta＝{"lookback_days": int, "age_s": float, "updated_at": datetime,
           "designation_by_eid": {eid: str|None}, "prod_by_eid": {...},
           "any_age_by_eid": {...}, "prod_designation_available": bool}（#23(a) 陳舊分級：max_age_s＝**硬上限**；
    呼叫端拿 age_s 自行分「新鮮／稍舊標 as-of」兩級，這裡不做政策）。

    designation_by_eid（#22，2026-08-18）＝各機**任何類別末段**的 MES `matDesignation` 原值
    （'Production'／'Engineering (R&D)'…；狀態檔 v2 每筆本就配對存 end+start+designation、
    push 端 2026-08-17 起帶）。★ **配對原子性**：值必屬同一段——ages 的 age 由該筆 end 算、
    designation 取同一筆 ⇒ (c) 交叉揭露拿它標「MES 那段是什麼類別」不會張冠李戴。
    非字串／缺欄 ⇒ None（降級形；呼叫端顯示面省略類別、不猜）。v4 的 `prod_*` 則是
    Production／Engineering (Baseline) 閉集的 argmax；`ages` 優先回這個 production-grade
    age。若 writer 明示 designation 欄不可用，才安全退回 generic end 的舊口徑。

    age_h＝(now − 該機末段結束時刻)/3600、clamp ≥0（末段時刻是 MES server clock、now 是
    本機牆鐘，容許秒級 skew；負值＝時鐘倒轉，壓 0 不外洩）。
    壞條目（時刻解析不了）＝跳過該機（＝Now 欄退回「未涵蓋」形，不猜、不炸整檔）。
    """
    if not isinstance(doc, dict) or doc.get("schema") not in LASTPROD_STATE_SCHEMAS:
        return None, "bad_schema"
    try:
        upd = datetime.fromisoformat(str(doc.get("updated_at")).replace("Z", "+00:00"))
        cov = datetime.fromisoformat(str(doc.get("covered_from")).replace("Z", "+00:00"))
        lookback = int(doc.get("lookback_days"))
    except (ValueError, TypeError):
        return None, "bad_meta"
    if lookback <= 0:
        return None, "bad_meta"
    age_s = (now - upd).total_seconds()
    if age_s < 0 or age_s > max_age_s:
        return None, "stale_state"
    if cov > now - timedelta(days=lookback) + timedelta(days=1):   # +1d 邊界餘裕（同 plan_window）
        return None, "coverage_short"
    ages, any_ages, desig, prod_by_eid, prod_desig = {}, {}, {}, {}, {}
    prod_designation_available = doc.get("prod_designation_available") is True
    for eid, v in (doc.get("eids") or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            end = datetime.fromisoformat(str(v.get("end")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        key = str(eid)
        any_age = max(0.0, (now - end).total_seconds() / 3600.0)
        any_ages[key] = any_age
        # #22：同一筆的 designation 一起帶（配對原子性——絕不跨筆湊）。非字串 ⇒ None。
        d = v.get("designation")
        desig[key] = d if isinstance(d, str) and d.strip() else None
        prod = {"prod_end": None, "prod_start": None, "prod_designation": None,
                "prod_result": None}
        p_end = v.get("prod_end")
        p_desig = v.get("prod_designation")
        if p_end is not None and p_desig in PRODUCTION_DESIGNATIONS:
            try:
                p_dt = datetime.fromisoformat(str(p_end).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                p_dt = None
            if p_dt is not None:
                prod.update({k: v.get(k) for k in prod})
                prod["prod_age_h"] = max(0.0, (now - p_dt).total_seconds() / 3600.0)
                prod_desig[key] = p_desig
        prod_by_eid[key] = prod
        ages[key] = prod.get("prod_age_h") if prod_designation_available else any_age
    # ★ v5 `sub_units`（`SPEC-LANDING.md` §18.23）：`{eid: {unit_id: age_h}}`。
    #   · v4 檔沒有這個鍵 ⇒ `{}`＝「一座都不知道」＝landing 全畫空心（C-LD2），
    #     **不是**「都沒在跑」。
    #   · age 與 `ages` 同一條算法（now − end、clamp ≥0）＝零第二把尺。
    #   · 壞值逐筆跳過（同 `eids` 的紀律）；`prod_designation_available` 為 False 時
    #     writer 那側本來就寫空表，這裡不必再判一次。
    sub_ages = {}
    for eid, units in (doc.get("sub_units") or {}).items():
        if not isinstance(units, dict):
            continue
        got = {}
        for unit, z in units.items():
            try:
                u_end = datetime.fromisoformat(str(z).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            got[str(unit)] = max(0.0, (now - u_end).total_seconds() / 3600.0)
        if got:
            sub_ages[str(eid)] = got
    # ★ v6 `station_lot`（`SPEC-LANDING.md` §18.26）：`{站: {last_prod_end, asof, age_h}}`。
    #   · **這是站級事實、不是機台事實**——把它攤成 per-EID 會製造「每台各自量到」的錯覺；
    #     哪幾台治具吃這一站的動靜，是 `board_eqmeeting.STATION_LOT_PROXY` 的職掌（唯一出處），
    #     本 reader **不認識任何 EID**，只把檔裡的時刻折成 datetime ＋ 年齡。
    #   · `last_prod_end` 為 None＝**writer 掃過、窗內零生產級段**（缺席才是「不知道」）
    #     ⇒ `age_h` 也是 None，讀端據此**不推斷**。
    #   · age 與 `ages` 同一條算法（now − end、clamp ≥0）＝零第二把尺。
    #   · v4/v5 檔沒有這個鍵 ⇒ `{}`；壞值逐筆跳過（同 `eids`／`sub_units` 的紀律）。
    station_lot = {}
    for cls, v in (doc.get("station_lot") or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            asof = datetime.fromisoformat(str(v.get("asof")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue                 # 沒有時點的末筆講不出年齡（同 writer 那側的判準）
        end = None
        if v.get("last_prod_end") is not None:
            try:
                end = datetime.fromisoformat(
                    str(v.get("last_prod_end")).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                end = None
        station_lot[str(cls)] = {
            "last_prod_end": end, "asof": asof,
            "age_h": (None if end is None
                      else max(0.0, (now - end).total_seconds() / 3600.0)),
            "asof_age_h": max(0.0, (now - asof).total_seconds() / 3600.0)}
    return ages, {"lookback_days": lookback, "age_s": age_s, "updated_at": upd,
                  "designation_by_eid": desig, "any_age_by_eid": any_ages,
                  "prod_by_eid": prod_by_eid, "production_designation_by_eid": prod_desig,
                  "prod_designation_available": prod_designation_available,
                  "sub_unit_ages_by_eid": sub_ages,
                  "station_lot": station_lot,
                  # writer 在那一輪查失敗時留的一句話（None＝沒事）；只給診斷／log 用。
                  "station_lot_err": (doc.get("station_lot_err")
                                      if isinstance(doc.get("station_lot_err"), str) else None)}


def lastprod_state_ages(path, now=None, max_age_s=21600.0):
    """I/O 殼：讀檔＋parse。任何失敗（缺檔/壞 JSON/拒收）→ (None, reason)——呼叫端退探測。
       max_age_s 預設＝6h 硬上限（#23(a)；「稍舊」的分級政策在呼叫端，見 parse docstring）。"""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"unreadable:{type(e).__name__}"
    from datetime import timezone
    return parse_lastprod_state(doc, now or datetime.now(timezone.utc), max_age_s)


# ---- 全線設備稼動計分板（§26.2 T1 / §26.9(b)）：landing 稼動區塊的『線級 headline』 -------
# 注意：mes_timeline.scoreboard_all_stations 對全站逐站查詢、長窗很慢（days7 >120s）。
# 故 app 層以 modest 窗 + 長 TTL 快取消費（見 app.load_scoreboard），絕不每個 web 請求同步重跑。
def scoreboard_rollup(stations):
    """線級彙總（設備視角 interval-union rollup，§14.4 無產品去重）：
       util = Σ生產 / Σ活躍窗，跨站相加（不同站＝不同設備，相加不雙計）。"""
    prod = sum((s.get("producing_sec") or 0) for s in stations)
    active = sum((s.get("active_span_sec") or 0) for s in stations)
    return {
        "n_stations": len(stations),
        "n_active_stations": sum(1 for s in stations if (s.get("active_span_sec") or 0) > 0),
        "n_machines": sum((s.get("fleet_size") or 0) for s in stations),
        "n_seg": sum((s.get("n_seg") or 0) for s in stations),
        "producing_sec": prod,
        "active_span_sec": active,
        "stop_sec": sum((s.get("stop_sec") or 0) for s in stations),
        "util": (prod / active) if active else 0.0,   # 全線設備稼動 headline
    }


def _wrap_scoreboard(source, now, days, stop_min, board, skipped):
    """把 mes_timeline 計分板（board=各站 fleet_scoreboard）包成 canonical（呈現層消費）。"""
    stations = sorted(board, key=lambda s: -((s.get("producing_sec") or 0)))  # T1：產出時間大→小
    for s in stations:                        # 加性標零工期站（M4 後遺；呈現層顯「—」不顯 util 0%）
        s["zero_len_only"] = _zero_len_only_station(s)
    return {"source": source, "mes_now": now, "days": days, "stop_min": stop_min,
            "stations": stations, "skipped": skipped or [],
            "fleet": scoreboard_rollup(stations)}


def get_scoreboard_live(days=3, stop_min=15):
    """連 MES 全線（重用凍結 mes_timeline.scoreboard_all_stations；需 VPN+Kerberos、唯讀）。"""
    if _synth_proc():                       # 清冊 §1 #10（M3）：同形迴圈、凍結純函式照算
        now, board, skipped = _wsio().scoreboard_all_stations(days=days, stop_min=stop_min)
        return _wrap_scoreboard(_synth_src(), now, days, stop_min, board, skipped)
    now, board, skipped = mt.scoreboard_all_stations(days=days, stop_min=stop_min)
    return _wrap_scoreboard("live", now, days, stop_min, board, skipped)


def get_fleet_by_cls_live(days=3, stop_min=15, budget_sec=None, order=None):
    """live：逐站 fetch_fleet 取真機台 EID → {cls: [(eid,desc),…]}（供 landing OEE A↔U 真拆分 rollup）。
       ★ 只重用凍結 mt.fetch_fleet（＝scoreboard_all_stations 內部所用的同一 fetcher、無新 SQL、EID 全來自真 fleet）。
       per-站 try/except + 重連（單站逾時不毒化其餘、比照 scoreboard_all_stations 韌性）；
       budget_sec 用盡→提早收（回已成功站＝誠實部分覆蓋，未抓到的站 landing 端退回 demo 拆分）。
       stop_min 僅簽章對稱（fetch_fleet 不需）。回 {cls: fleet}（跳過失敗/空站）。
       ⚠ 額外 MES 成本：本函式對全站再各跑一次 fetch_fleet（凍結 B4 後已去 DISTINCT、輕量）——
       與稼動 scoreboard 並行、長 TTL 快取（app 層），首暖付一次；任何失敗→landing 端 graceful 退 demo。
       ★ `order`（2026-08-27 加、選用）＝`{cls: asof}` ⇒ **只把迴圈順序換成 stale-first**
         （`fleet_fetch_order`；沒抓過的站最優先）。**查詢文本、參數、per-站 try/except、
         budget 語意一字不動**——實測 45 秒只跑得完全站中的兩站，固定從第 1 站開始 ⇒ 後段站
         永遠抓不到、落盤累積永遠收斂不了。`order=None`／空 ⇒ 逐位元照舊 `EQP_STATIONS` 原序。"""
    deadline = (time.monotonic() + budget_sec) if budget_sec else None
    if _synth_proc():                       # 清冊 §1 #7（M1）：同一個逐站迴圈、同一個預算語意
        return _fleet_by_cls_synth(days, deadline, order)
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC
    out = {}
    stations = fleet_fetch_order(mt.EQP_STATIONS, order) if order else list(mt.EQP_STATIONS)
    try:
        for cls in stations:
            if deadline and time.monotonic() > deadline:
                break                        # 誠實部分覆蓋：回已抓到的站
            try:
                fleet = mt.fetch_fleet(cur, days, cls)
                if fleet:
                    out[cls] = fleet
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                try:                          # 重連：單站逾時恐毒化連線 → 避免其餘站連帶失敗
                    conn, cur = mt._connect()
                    conn.timeout = LIVE_QUERY_TIMEOUT_SEC
                except Exception:
                    pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _fleet_by_cls_synth(days, deadline, order):
    """合成進程的 `get_fleet_by_cls_live`：迴圈序（含 stale-first `order`）、預算提早收、跳過空站，
       皆與 live 同義；錨＝一次 `wsynth_io.now()`。"""
    io = _wsio()
    now = io.now()
    out = {}
    stations = fleet_fetch_order(mt.EQP_STATIONS, order) if order else list(mt.EQP_STATIONS)
    for cls in stations:
        if deadline and time.monotonic() > deadline:
            break                            # 誠實部分覆蓋：回已抓到的站
        try:
            fleet = io.fetch_fleet(days, cls, now=now)
            if fleet:
                out[cls] = fleet
        except Exception:
            pass
    return out


def get_scoreboard_one_live(cls, days=3, stop_min=15):
    """連 MES 單站計分板（scoreboard_all_stations stations=[cls] → 只查該站，免掃全站）。
       站頁用：避免 days 大時全站順序查逾時（站頁只需自身 util rec）。canonical 同形、stations 僅 1 站。"""
    if _synth_proc():                       # 清冊 §1 #11（M3）
        now, board, skipped = _wsio().scoreboard_all_stations(days=days, stop_min=stop_min,
                                                              stations=[cls])
        return _wrap_scoreboard(_synth_src(), now, days, stop_min, board, skipped)
    now, board, skipped = mt.scoreboard_all_stations(days=days, stop_min=stop_min, stations=[cls])
    return _wrap_scoreboard("live", now, days, stop_min, board, skipped)


def station_rollup_from_data(data, cls):
    """由 get_live/get_demo 的 per-machine timeline **直接彙總**站級計分板 rec（免再抓一次 segments）。
       重用凍結 mt.fleet_scoreboard ＝口徑與 get_scoreboard_one_live 完全一致(同 fleet/同 analyze)。
       machines 缺 _a（如 snapshot 無 analyze）→ 回 None，由呼叫端 fallback 走單站計分板。"""
    machines = (data or {}).get("machines") or []
    results = {m["eid"]: m["_a"] for m in machines if m.get("_a")}
    fleet = [(m["eid"], m.get("desc", "")) for m in machines]
    if not results:
        # 無 analyze 結果：可能整站無訊號，或（live）零工期段被 _clip 掏空 → analyze None。
        # 若任一機台帶零工期旗 → 回一個零工期站 rec（供站頁 overview 顯「—」zero_len 語意、非誤導 0%）。
        if machines and any(m.get("zero_len_only") for m in machines):
            n_zl = sum(1 for m in machines if m.get("zero_len_only"))
            return {"cls": cls, "fleet_size": len(fleet), "n_seg": n_zl,
                    "producing_sec": 0.0, "active_span_sec": 0.0, "run_span_sec": 0.0,
                    "stop_sec": 0.0, "util_strict_pct": 0.0, "trailing_sec": 0.0,
                    "zero_len_only": True}
        return None
    rec = mt.fleet_scoreboard(cls, fleet, results)
    rec["zero_len_only"] = _zero_len_only_station(rec)
    return rec


def get_scoreboard_snapshot(path, days=3, stop_min=15):
    """載已存的 `mes_timeline --all-stations --json` 輸出（離線即時、可重現真資料）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return _wrap_scoreboard("snapshot", data.get("mes_now"),
                            data.get("days", days), data.get("stop_min", stop_min),
                            data.get("stations", []), data.get("skipped", []))


def _demo_scoreboard(days=3, stop_min=15):
    """合成全線計分板（離線開發/demo）：用真 EQP_STATIONS class 名 + 確定性 util。
       部分站近窗無活動（誠實留白）；分類/口徑與 live 一致，僅數字來源不同。"""
    import random
    now = datetime.now().replace(microsecond=0)
    win = max(1, days) * 86400.0
    board = []
    for i, cls in enumerate(mt.EQP_STATIONS):
        rnd = random.Random(i * 131 + 7)
        if i % 7 == 5:                       # 約每 7 站 1 站近窗無活動（留白＝誠實）
            board.append({"cls": cls, "fleet_size": rnd.randint(0, 1), "n_seg": 0,
                          "producing_sec": 0.0, "active_span_sec": 0.0, "run_span_sec": 0.0,
                          "stop_sec": 0.0, "util_strict_pct": 0.0, "trailing_sec": 0.0})
            continue
        fleet_size = rnd.randint(1, 4)
        duty = rnd.uniform(0.35, 0.92)
        active = win * fleet_size * rnd.uniform(0.5, 0.95)
        prod = active * duty
        stop = (active - prod) * rnd.uniform(0.6, 0.95)
        micro = max(0.0, active - prod - stop)
        board.append({"cls": cls, "fleet_size": fleet_size,
                      "n_seg": int(prod / 1800) + rnd.randint(2, 40),
                      "producing_sec": prod, "active_span_sec": active,
                      "run_span_sec": prod + micro, "stop_sec": stop,
                      "util_strict_pct": (100 * prod / active) if active else 0.0,
                      "trailing_sec": rnd.choice([0.0, 0.0, 0.0, rnd.uniform(600, 9000)])})
    return _wrap_scoreboard("demo", now, days, stop_min, board, [])


def get_scoreboard(source="demo", days=3, stop_min=15, snapshot_path=None):
    """全線稼動計分板（§26.2 T1）。同 get_data 的三源語意（demo/snapshot/live）。"""
    if _live_route(source):                 # §5 wd:1292（S）
        return get_scoreboard_live(days, stop_min)
    if source == "snapshot":
        if not snapshot_path:
            raise ValueError("source=snapshot 需提供 scoreboard snapshot_path")
        return get_scoreboard_snapshot(snapshot_path, days, stop_min)
    return _demo_scoreboard(days, stop_min)


# ---- 日曆窗稼動計分板（/daily 昨日戰報：窗＝[start, end) naive 台北牆鐘、非滾動窗）------------
def _range_filter_scoreboard(sb, stations, start, end, unsupported):
    """demo/snapshot：把整盤過濾到指定 stations（+ 重算 rollup）並蓋窗鍵。unsupported=聚合快照無法重切。"""
    if stations is not None:
        want = set(stations)
        stns = [s for s in (sb.get("stations") or []) if s.get("cls") in want]
        sb = {**sb, "stations": stns, "fleet": scoreboard_rollup(stns)}
    else:
        sb = {**sb}
    sb["win_start"], sb["win_end"] = start, end
    if unsupported:
        sb["range_unsupported"] = True
    return sb


def _scoreboard_range_live(start, end, stop_min, stations):
    """live 日曆窗：逐站 fetch_fleet + fetch_segments（凍結，以 fetch_days 覆蓋窗）→ _clip 到 [start,end)
       → analyze（now 錨＝end，尾段停機錨在窗末）→ fleet_scoreboard。per-站 try/except + 重連
       （比照 mt.scoreboard_all_stations 韌性：單站逾時不毒化其餘）。"""
    stations = mt.EQP_STATIONS if stations is None else stations
    thr = stop_min * 60
    if _synth_proc():                       # 清冊 §1 #8–#9（M1＋M2）：進程旗標分派
        return _scoreboard_range_synth(start, end, stop_min, stations, thr)
    fetch_days = max(1, (datetime.now() - start).days + 1)   # 凍結 fetcher 以 now 為錨→取足以覆蓋 start 的天數
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC
    board, skipped, zlo_by_cls = [], [], {}
    for cls in stations:
        try:
            fleet = mt.fetch_fleet(cur, fetch_days, cls)
            segs = mt.fetch_segments(cur, [e for e, _ in fleet], fetch_days, cls)
            results = {eid: mt.analyze(_clip(segs.get(eid, []), start, end), end, thr)
                       for eid, _ in fleet}
            board.append(mt.fleet_scoreboard(cls, fleet, results))
            zlo_by_cls[cls] = _range_station_zlo(fleet, segs, start, end)   # ★ clip 前算（零工期段偵測）
        except Exception as e:
            skipped.append({"cls": cls, "reason": f"{type(e).__name__}: {str(e)[:200]}"})
            try:
                conn.close()
            except Exception:
                pass
            try:                                   # 重連：單站逾時恐毒化連線
                conn, cur = mt._connect()
                conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            except Exception:
                pass
    try:
        conn.close()
    except Exception:
        pass
    return _range_wrap("live", start, end, stop_min, board, skipped, zlo_by_cls)


def _range_wrap(source, start, end, stop_min, board, skipped, zlo_by_cls):
    """日曆窗計分板的收尾（live 與 synth 共用同一段）。"""
    days = (end - start).days or 1
    sb = _wrap_scoreboard(source, end, days, stop_min, board, skipped)
    sb["win_start"], sb["win_end"] = start, end
    # 覆蓋 _wrap_scoreboard 由 n_seg=0（零工期段被 _clip 掏空）推出的 False：range 路徑靠 clip 前算的旗。
    for s in (sb.get("stations") or []):
        if zlo_by_cls.get(s.get("cls")):
            s["zero_len_only"] = True
    return sb


def _scoreboard_range_synth(start, end, stop_min, stations, thr):
    """合成進程的 `_scoreboard_range_live`：同一個逐站迴圈（`_clip` 到 [start,end)、`analyze` 錨＝end、
       `fleet_scoreboard`、clip 前算零工期旗）；`fetch_days` 的 now＝`wsynth_io.now()`（live 用
       `datetime.now()`，正式運行兩者同源）。"""
    io = _wsio()
    now = io.now()
    fetch_days = max(1, (now - start).days + 1)
    board, skipped, zlo_by_cls = [], [], {}
    for cls in stations:
        try:
            fleet = io.fetch_fleet(fetch_days, cls, now=now)
            segs = io.fetch_segments([e for e, _ in fleet], fetch_days, cls, now=now)
            results = {eid: mt.analyze(_clip(segs.get(eid, []), start, end), end, thr)
                       for eid, _ in fleet}
            board.append(mt.fleet_scoreboard(cls, fleet, results))
            zlo_by_cls[cls] = _range_station_zlo(fleet, segs, start, end)
        except Exception as e:
            skipped.append({"cls": cls, "reason": f"{type(e).__name__}: {str(e)[:200]}"})
    return _range_wrap(_synth_src(), start, end, stop_min, board, skipped, zlo_by_cls)


def get_scoreboard_range(source, start, end, stop_min=15, stations=None, snapshot_path=None):
    """日曆窗版稼動計分板（/daily 昨日戰報）；窗＝[start, end) naive 台北牆鐘（半開）。
       stations＝明確 cls 清單（呼叫端傳 20 boss 站）。回 canonical + win_start/win_end 鍵。
       live＝逐站裁窗真算；demo＝確定性合成（數字不依賴窗、標 DEMO）；
       snapshot＝聚合快照無法重切→誠實 fallback（range_unsupported=True，頁面顯註記）。"""
    if _live_route(source):                 # §5 wd:1364（S）
        return _scoreboard_range_live(start, end, stop_min, stations)
    if source == "snapshot":
        if not snapshot_path:
            raise ValueError("source=snapshot 需提供 scoreboard snapshot_path")
        sb = get_scoreboard_snapshot(snapshot_path, max(1, (end - start).days), stop_min)
        return _range_filter_scoreboard(sb, stations, start, end, unsupported=True)
    sb = _demo_scoreboard(days=1, stop_min=stop_min)   # demo 數字不依賴窗（標 DEMO）
    return _range_filter_scoreboard(sb, stations, start, end, unsupported=False)


# ======================================================================================
# §26 B/C — 產出(OUTPUT) + 良率(YIELD) 軸（RAW v0；§26.5 真實不良口徑待製程 SME）
# 與稼動 scoreboard 對稱的三源（demo/snapshot/live），消費凍結 mes_yield（不改）。
#
# ★ 誠實鐵則（§26.5 / §26.10）——前端必貫徹：
#   - 所有 yield 數字皆 **RAW v0**、未為重測/多試校正（raw fail% 高估真實不良；
#     實證 AutoOQC raw 18.5%→真 4.6%、CAL FPY 86.6%首試 vs 52.3%right-first-time）。
#   - OUTPUT = COUNT(DISTINCT lot)，每層各自獨立 DISTINCT、**勿相加子層**；
#     **跨站亦不可 Σ output_units**（同一 lot 跨多站＝重複計）→ 線級 headline 走 **anchor 站**
#     （CAL＝已驗 ground-truth 參考站），絕不做跨站加總、不偽造線級總數。
#   - YIELD 線級 headline 同走 anchor 站 raw（fail%/FPY），不偽造跨站池化或校正。
# ======================================================================================
YIELD_CALIBER = my.CALIBER             # "raw, pending §26.5 caliber"
YIELD_ANCHOR_CLS = my.CAL_CLASS        # 線級 headline 參考站（CAL＝已驗 ground-truth baseline）

# §26 O9 — 線出口站(line-exit) 候選（待製程 SME 指定；CONCEPT §26.5/O9）。
# 全部在凍結 mes_timeline.EQP_STATIONS 內（不新增凍結站、不跨站相加、不偽造線級總數）。
# 單位不一：EndOfLineChart*＝module（同 CAL B00 序號）；Unit*/Packing＝unit（可售件，另一單位）。
# ⚠ 真線出口若在 EQP_STATIONS 外（如真 Shipping 站，現有站清單內無 segShipping），curate 進去＝改凍結 EQP_STATIONS + 重驗＝待 SME 確認、勿預加。
EOL_CANDIDATES = [
    {"cls": "segEOLChecker", "unit": "module"},
    {"cls": "segEOLRunner", "unit": "module"},
    {"cls": "segAutomatedOQC", "unit": "unit"},
    {"cls": "segFinalOQC", "unit": "unit"},
    {"cls": "segAssemblyLeakTest", "unit": "unit"},
    {"cls": "segVacuumPackInspect", "unit": "unit"},
]


def line_exit_candidates(yb):
    """O9 線級產出『候選』：各線出口候選站的真實 per-station output（不跨站 Σ、無線級總數、不偽造）。
       純呈現層（讀 yb['stations']）。誠實：哪站才是線出口＋計數單位＝§26.5 待 SME，故只列候選、不定線級數字。"""
    stations = (yb.get("stations") if yb else None) or []
    out = []
    for c in EOL_CANDIDATES:
        s = next((x for x in stations if x.get("cls") == c["cls"]), None)
        out.append({"cls": c["cls"], "unit": c["unit"],
                    "output_units": (s.get("output_units") if s else None),
                    "present": s is not None})
    return out


def yield_anchor(stations, anchor_cls=YIELD_ANCHOR_CLS):
    """線級 headline 的 anchor 站：預設 CAL；無則退回 output_units 最大站；皆無→None。
       OUTPUT/YIELD 線級 **不跨站相加 distinct lot**（§26.10）——以單一參考站誠實代表。"""
    a = next((s for s in stations if s.get("cls") == anchor_cls), None)
    if a is None:
        producing = [s for s in stations if (s.get("output_units") or 0) > 0]
        a = max(producing, key=lambda s: (s.get("output_units") or 0), default=None)
    return a


def yield_rollup(stations, anchor_cls=YIELD_ANCHOR_CLS):
    """線級彙總（誠實、不跨站加總 distinct lot；計數類僅給『活躍站數』而非偽線級總數）。"""
    return {
        "n_stations": len(stations),
        "n_output_stations": sum(1 for s in stations if (s.get("output_units") or 0) > 0),
        "n_yield_stations": sum(1 for s in stations if s.get("has_yield_signal")),
        "anchor": yield_anchor(stations, anchor_cls),
        "anchor_cls": anchor_cls,
    }


def _wrap_yield_scoreboard(source, now, days, caliber, board, skipped):
    """把 mes_yield 計分板（board=各站站級 rec）包成 canonical。排序：output_units 大→小。"""
    stations = sorted(board, key=lambda s: -((s.get("output_units") or 0)))
    return {"source": source, "mes_now": now, "days": days,
            "caliber": caliber or YIELD_CALIBER,
            "stations": stations, "skipped": skipped or [],
            "rollup": yield_rollup(stations)}


def get_yield_scoreboard_live(days=3):
    """連 MES 全線產出/良率（重用凍結 mes_yield.scoreboard_all_stations；站級、唯讀）。"""
    now, board, skipped = my.scoreboard_all_stations(days=days)
    return _wrap_yield_scoreboard("live", now, days, my.CALIBER, board, skipped)


def get_yield_scoreboard_snapshot(path, days=3):
    """載已存的 `mes_yield --all-stations --json`（離線即時、可重現真資料）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return _wrap_yield_scoreboard("snapshot", data.get("mes_now"),
                                  data.get("days", days), data.get("caliber"),
                                  data.get("stations", []), data.get("skipped", []))


def _demo_station_rec(cls, i, with_machines=False, blank=False):
    """合成單站 OUTPUT+YIELD rec（離線開發/demo）。**走凍結 build_station_record/_machine_records**
       ＝rec 形與 live byte-shape 一致（欄位/口徑全同，僅數字來源不同）。
       約 1/3 站判退（測站，有 yield signal + fact_segment FPY）；其餘製程/烘烤站 0 fail（誠實留白）。"""
    import random
    rnd = random.Random(i * 911 + 17)
    if blank:
        raw = {"pscpk": None, "units": 0, "good_units": 0, "n_seg": 0, "qty_max": None,
               "qty_proc": 0.0, "qty_fail": 0.0, "rs_pass": 0, "rs_fail": 0}
        return my.build_station_record(cls, raw, {}, None,
                                       (rnd.randint(0, 2) if with_machines else None),
                                       ([] if with_machines else None))
    units = rnd.randint(150, 1300)
    is_test = (i % 3 == 0)
    seg_mult = rnd.uniform(1.05, 1.8) if is_test else 1.0     # 測站重測 → 段 > units
    n_seg = max(units, int(units * seg_mult))
    qfail = int(units * rnd.uniform(0.05, 0.22)) if is_test else 0
    rs_fail = int(n_seg * rnd.uniform(0.04, 0.18)) if is_test else 0
    raw = {"pscpk": (i + 2 if is_test else None), "units": units,
           "good_units": units - int(qfail * rnd.uniform(0.3, 0.7)),
           "n_seg": n_seg, "qty_max": 1.0, "qty_proc": float(n_seg),
           "qty_fail": float(qfail), "rs_pass": n_seg - rs_fail, "rs_fail": rs_fail}
    plover = int(units * rnd.uniform(0.7, 0.9))
    kestrel = int(units * rnd.uniform(0.03, 0.12))
    heron = max(0, units - plover - kestrel - rnd.randint(0, 30))
    products = {"plover": plover, "kestrel": kestrel, "heron": heron,
                my.UNATTRIBUTED: rnd.randint(0, 40)}
    fpy = None
    if is_test:
        u = units
        att1 = int(u * rnd.uniform(0.78, 0.93))
        retested = int(u * rnd.uniform(0.2, 0.5))
        passed_but = int(retested * rnd.uniform(0.2, 0.4))
        fpy = {"units": u, "att1_pass": att1, "last_pass": int(u * rnd.uniform(0.95, 0.99)),
               "retested": retested, "passed_but_retested": passed_but,
               "failed_then_retried": retested - passed_but}
    fleet_size, machines = None, None
    if with_machines:
        if cls == mt.CAL_CLASS:                       # CAL：父機級 fleet → detail 拆 Gen2 工位（與 live 對齊）
            fleet = _demo_expand_fleet([(eid, d) for eid, d, _du in DEMO_CAL_FLEET])
        elif cls in DEMO_TL_FLEETS:                   # CLN：父機級 fleet → 拆子工位（保留父機列，見 _demo_expand_fleet）
            fleet = _demo_expand_fleet([(eid, d) for eid, d, _du in DEMO_TL_FLEETS[cls]])
        else:
            k = rnd.randint(1, 4)
            fleet = [(f"EID-{(i * 7 + j) % 9000 + 1000:05d}", f"{cls[3:]} #{j + 1}") for j in range(k)]
        k = len(fleet)
        myield = {}
        for eid, _d in fleet:
            if eid in _ZERO_RATIO_MODULES:            # census 無產出工位：不合成假產出（build_machine_records 給誠實零列）
                continue
            mu = max(1, int(units / k * rnd.uniform(0.6, 1.3)))   # Σ機台 > 站（跨機台重測重疊）＝誠實
            mfail = int(mu * rnd.uniform(0.05, 0.2)) if is_test else 0
            mseg = max(mu, int(mu * seg_mult))
            myield[eid] = {"units": mu, "good_units": mu - int(mfail * 0.5), "n_seg": mseg,
                           "qty_proc": float(mseg), "qty_fail": float(mfail),
                           "rs_pass": mseg - mfail, "rs_fail": mfail}
        fleet_size, machines = k, my.build_machine_records(fleet, myield)
        for mr in machines:                           # demo 子工位列補標 parent_eid（與 live _expand_gen2_yield_live 對齊）
            pe = _BAY_PARENT.get(mr.get("eid"))
            if pe:
                mr["parent_eid"] = pe
    return my.build_station_record(cls, raw, products, fpy, fleet_size, machines)


# ---- CAL Gen2 工位展開（detail-only；凍結核心不動、站級總數不變）------------------------------
def _demo_expand_fleet(fleet):
    """demo：父機級 fleet 裡的 Gen2/CLN 父機 → **配置工位全列**（與機台帶同規則；2026-07-08 Chris 拍板）。
       無產出工位（ratio<=0）仍成列、demo 由 _ZERO_RATIO_MODULES 給誠實零 yield（不合成假產出）。
       覆蓋率低的父機（MODULE_COVERAGE_NOTES，CLN 二母機）＝**保留父機列 + 附子工位**
       （父機＝機台全部活動 superset；子工位＝其中模組級標記的那部分子集、非相加；與 _expand_gen2_yield_live 一致）；
       CAL（U4 census 乾淨、父=工位聯集）＝取代父機、只留工位。pure。"""
    return [(e, d) for e, d, _p in expand_unit_roster(fleet)]


def gen2_unit_split(eid):
    """**Gen2／CLN 父機 → 工位的唯一規則**（合成產線 WP-4b R-B1 抽出；QG-13 單一出處）。pure、零 I/O。

       回 None＝不是配置了工位的父機（照原列）；否則回 `(keep_parent, [(工位 EID, 顯示名), …])`：
       · 工位＝`GEN2_BAY_POSITIONS[eid]` **全列**（配置工位一律成列；2026-07-08 Chris 拍板，無產出／停用
         工位也要看得見＝不做 ratio>0 過濾）、順序＝配置順序；
       · `keep_parent`＝覆蓋率低的父機（`MODULE_COVERAGE_NOTES`，CLN 二母機）**保留父機列、排在自己的工位之前**；
         CAL（census 乾淨、父＝工位聯集）＝取代父機、只留工位。
       呼叫端：`expand_unit_roster`（→ `_demo_expand_fleet`、合成進程站頁名冊骨架）與 `_expand_gen2_yield_live`。
       ⚠ `expand_units_timeline`（稼動 agg 展開）**不走這支**：那邊只換「有活動的工位」的 agg、規則不同
         （agg 缺席＝稼動「—」），列清單永遠由本支決定。"""
    pos = GEN2_BAY_POSITIONS.get(eid)
    if not pos:
        return None
    return bool(MODULE_COVERAGE_NOTES.get(eid)), [(e, d) for e, d, _r in pos]


def expand_unit_roster(fleet):
    """父機級 `[(eid, desc), …]` → 站頁單位列 `[(eid, desc, parent_eid), …]`（`gen2_unit_split` 規則）。pure。
       父機列與一般機台的 `parent_eid`＝None；工位列＝其父機 EID。"""
    out = []
    for eid, desc in fleet:
        sp = gen2_unit_split(eid)
        if sp is None:
            out.append((eid, desc, None))
            continue
        keep_parent, kids = sp
        if keep_parent:
            out.append((eid, desc, None))             # 保留父機列
        out.extend([(e, d, eid) for e, d in kids])    # 全列（不再 ratio>0 過濾）
    return out


def synth_unit_skeleton(cls, machines):
    """**合成進程專用**（WP-4b R-B1，Fable 2026-09-30 代裁「不算 AP-2」）：站頁機台表的「名冊骨架」。

       良率腿在合成第一期未提供（M4＝第二期），而 `board_oee.station_detail` 的列清單取自良率記錄的
       `machines` ⇒ 由**時間軸的機台清單**（`data["machines"]` 的 eid／desc）經 `expand_unit_roster`
       （與 live／demo 同一條 Gen2 展開規則）排出列，產出／良品一律 0 ⇒ `station_detail` 的品質與 OEE
       自然是 None（模板另依 `yield_na` 把產出／良品兩欄印「—」）。**不產任何數字**；稼動與妥善率仍由
       既有 run-window 腿真算。回 station_detail 吃的 y_rec 形 `{"cls", "machines": [...]}`。"""
    fleet = [(m.get("eid"), m.get("desc", "")) for m in (machines or []) if m.get("eid")]
    rows = []
    for eid, desc, parent in expand_unit_roster(fleet):
        rows.append({"eid": eid, "desc": desc, "output_units": 0, "good_units": 0, "parent_eid": parent})
    return {"cls": cls, "machines": rows}


def _expand_gen2_yield_live(cur, cls, days, rec):
    """live：rec['machines'] 的 Gen2 父機列 → 真實工位列（重用凍結 fetch_machine_yield/build_machine_records）。
       配置工位全列（無產出＝誠實零列）；站級欄位完全不動；展開失敗 → 保留父機列（graceful）。"""
    machines = rec.get("machines") if rec else None
    if not machines:
        return
    out = []
    for m in machines:
        sp = gen2_unit_split(m.get("eid"))           # 唯一規則（WP-4b R-B1 抽出；輸出逐位元同前）
        if sp is None:
            out.append(m)
            continue
        keep_parent, fleet = sp
        try:
            myield = my.fetch_machine_yield(cur, [e for e, _ in fleet], days, cls)
            # 配置工位**全列**（2026-07-08 Chris 拍板：無產出/停用工位也要看得見）＝不再 output>0 過濾；
            # build_machine_records 對無 yield 資料工位天然給誠實零列（output 0、fail_pct None）。
            rows = my.build_machine_records(fleet, myield)
            for r in rows:
                r["parent_eid"] = m.get("eid")
            # 覆蓋率低的父機（CLN）＝保留父機列 + 子工位。父機＝該機台全部活動（superset、含模組級標記的那部分）、
            # 子工位＝該子集細分（∴ 非相加、站頁本就標 sx_units_nosum）；不丟父機＝不漏那 100% 真值。
            # CAL（U4 census：父=工位聯集、乾淨）＝取代父機、只留工位。
            if keep_parent:
                out.append(m)
            out.extend(rows or [m])
        except Exception:
            out.append(m)
    rec["machines"] = out


def _gen2_module_aggs_live(cls, days, stop_min, eids):
    """live：工位級 timeline agg（重用凍結 fetch_segments/analyze/timeline_records + aggregate）。回 {eid: agg}（只留有活動者）。"""
    if _synth_proc():                       # 清冊 §1 #13（M2／M10）：窗錨＝合成時鐘
        io = _wsio()
        now = io.now()
        return _module_aggs(eids, io.fetch_segments(eids, days, cls, now=now), now, stop_min)
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC   # 逾時上限：大窗 query 不致無限掛住（逾時→該段 unavailable、頁仍回）
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        segs = mt.fetch_segments(cur, eids, days, cls)
    finally:
        conn.close()
    return _module_aggs(eids, segs, now, stop_min)


def _module_aggs(eids, segs, now, stop_min):
    """`_gen2_module_aggs_live` 的後半（live 與 synth 共用同一段）。"""
    thr = stop_min * 60
    out = {}
    for e in eids:
        a = mt.analyze(segs.get(e, []), now, thr)
        recs = mt.timeline_records(e, a, now) if a else []
        agg = aggregate(recs)
        if (agg.get("total_sec") or 0) > 0:
            out[e] = agg
    return out


def expand_units_timeline(source, cls, days, stop_min, tl_machines):
    """detail-only：把 tl_machines 的 Gen2 父機 agg 換成工位 agg（單站三軸 util 用；keys 與 yield 展開對齊）。
       live＝查工位 segments；demo＝按 demo 比例切父機 producing（total 同窗）；snapshot＝不動。
       任何失敗 → 保留父機 agg（graceful；單站頁退回父機顯示、不崩）。"""
    if source == "snapshot" or not tl_machines:
        return tl_machines
    parents = [e for e in tl_machines if e in GEN2_BAY_POSITIONS]
    if not parents:
        return tl_machines
    out = dict(tl_machines)
    for p in parents:
        pos = GEN2_BAY_POSITIONS[p]
        try:
            if _live_route(source):             # §5 wd:1610（S）
                aggs = _gen2_module_aggs_live(cls, days, stop_min, [e for e, _d, _r in pos])
            else:                                     # demo：按 ratio 切父機 producing（total 同窗），留 ratio>0
                base = out[p]
                T = base.get("total_sec") or 0.0
                P0 = base.get("producing_sec") or 0.0
                aggs = {}
                for e, _d, r in pos:
                    if r <= 0:
                        continue
                    P = P0 * r
                    aggs[e] = {"by_state": {"生產": P, "不明": max(0.0, T - P)},
                               "total_sec": T, "producing_sec": P,
                               "downtime_sec": max(0.0, T - P), "unknown_sec": max(0.0, T - P),
                               "util": (P / T if T else 0.0), "unknown_pct": ((T - P) / T if T else 0.0)}
            if aggs:
                if not MODULE_COVERAGE_NOTES.get(p):
                    del out[p]                        # CAL（census 乾淨、工位=父機）取代父機；
                out.update(aggs)                      # 覆蓋率低（CLN）保留父機 agg（承載未打標多數活動），與 yield 側一致。
        except Exception:
            pass                                      # 保留父機 agg
    return out


def _child_machine(eid, desc, recs, a, parent_eid):
    """子工位 machine dict（形同 _build 的機台列）＋ parent_eid/cov_note，供 /utilization 機台帶附掛列 + /machine 下鑽。
       a=None（窗內無 segment）→ 空列 + nosig 旗（呈現層顯空軌+「—」；不隱藏＝『整窗無訊號』本身是會議資訊）。"""
    return {"eid": eid, "desc": desc, "records": recs,
            "n_seg": a["n_seg"] if a else 0, "micro_idle_sec": a["micro_idle_sec"] if a else 0.0,
            "agg": aggregate(recs), "_a": a, "nosig": not recs,
            "parent_eid": parent_eid, "cov_note": bool(MODULE_COVERAGE_NOTES.get(parent_eid))}


def expand_machines_band(source, cls, days, stop_min, data):
    """/utilization 機台帶：Gen2/CLN 父機 → 子工位『附掛列』(nest；**不取代父機、不進 data、不計入合計**)。
       回 {parent_eid: [child machine dict]}（child dict 形同 _build 機台列，多 parent_eid/cov_note）。
       ★ 設計鐵則（Fable plan）：donut/趨勢/A%U% 全走原 data(父機聯集)＝完全不受影響；子工位列純細分視圖。
         WO overlay 疊在父機列(工單掛父機 EID)＝保留。
       ⚠ **「子工位列無工單著色（誠實、無單可疊）」那句已於 2026-08-14 作廢**：A10 把母單展開到
         子工位之後就有單可疊了，著色改由 `overlay_children_wo_states` 在呼叫端疊上去
         （父機列走機器視角、子工位列走 A10 clips＝同一張單的兩個投影、永不相加）。
       live：查子工位 segments，**視窗與 data 對齊**（mirror get_live：fetch_days 覆蓋 win_start、_clip 到窗）；
       demo：_demo_intervals per 工位 seed（ratio>0 才顯）；snapshot：{}（子工位資料無、退父機列，graceful）。"""
    if source == "snapshot" or not data:
        return {}
    machines = data.get("machines") or []
    present = [m["eid"] for m in machines if m["eid"] in GEN2_BAY_POSITIONS]
    if not present:
        return {}
    win_start = data.get("win_start")
    win_end = data.get("win_end") or data.get("mes_now")
    thr = stop_min * 60
    out = {}

    def _kids_from_segs(parent_eid, pos, get_ivs):
        """配置的工位**一律成列**：窗內無 segment（含 demo ratio-0）→ 空列 nosig（空軌+「—」），不隱藏。
           2026-07-08 Chris：SF M1/M3 整窗無訊號被藏掉＝誤導『少了兩個工位』；無訊號＝會議級資訊、必須可見。"""
        kids = []
        for e, d, r in pos:
            ivs = get_ivs(e, r) or []
            a = mt.analyze(ivs, win_end, thr)
            recs = mt.timeline_records(e, a, win_end) if a else []
            kids.append(_child_machine(e, d, recs, a, parent_eid))
        return kids

    def _live_kids(db_now, fetch_segs):
        """live／synth 共用的逐父機展開（視窗與 data 對齊；`fetch_segs(eids, fetch_days)`＝I/O 來源）。"""
        fetch_days = max(days, (db_now - win_start).days + 1) if win_start else days
        for p in present:
            pos = GEN2_BAY_POSITIONS[p]
            try:                                   # per-parent 隔離：該父機查/算失敗→無子工位列、父機列仍在（graceful）
                segs = fetch_segs([e for e, _d, _r in pos], fetch_days)
                kids = _kids_from_segs(
                    p, pos, lambda e, r: (_clip(segs.get(e, []), win_start, win_end)
                                          if win_start else segs.get(e, [])))
            except Exception:
                continue
            if kids:
                out[p] = kids

    if _live_route(source):                   # §5 wd:1675（S）
        if _synth_proc():                     # 清冊 §1 #14（M2／M10）：窗錨＝合成時鐘
            io = _wsio()
            db_now = io.now()
            _live_kids(db_now, lambda eids, fd: io.fetch_segments(eids, fd, cls, now=db_now))
            return out
        conn, cur = mt._connect()
        conn.timeout = LIVE_QUERY_TIMEOUT_SEC
        try:
            cur.execute("SELECT GETDATE()")
            db_now = cur.fetchone()[0]
            _live_kids(db_now, lambda eids, fd: mt.fetch_segments(cur, eids, fd, cls))
        finally:
            conn.close()
    else:                                             # demo
        for p in present:
            pos = GEN2_BAY_POSITIONS[p]
            kids = _kids_from_segs(
                p, pos, lambda e, r: (_demo_intervals(win_start, win_end, r, seed=sum(ord(c) for c in e))
                                      if r > 0 else None))
            if kids:
                out[p] = kids
    return out


def demo_machine_util(cls, machines, days, stop_min):
    """§26.9 demo（非 CAL 站）：由 yield demo 機台列的 EID 合成 per-machine 稼動 agg（離線、確定性、無 MES）。
       走『同一套』凍結 analyze/timeline_records + 本地 _demo_intervals/aggregate；keys 與 yield 機台對齊
       （util-EID == yield-EID by construction → station_detail join 得起來）。只留窗內有活動者（total_sec>0）。
       回 {eid: agg}。EID 全來自 yield 機台列（非手寫；杜絕 fake-EID）。cls 僅保留簽章對稱、本層未用。"""
    import random
    now = datetime.now().replace(microsecond=0)
    win_start, win_end = resolve_window(now, days)
    thr = stop_min * 60
    out = {}
    for m in (machines or []):
        eid = m.get("eid")
        if not eid:
            continue
        seed = sum(ord(c) for c in eid)
        duty = random.Random(seed).uniform(0.40, 0.89)
        ivs = _demo_intervals(win_start, win_end, duty, seed)
        a = mt.analyze(ivs, win_end, thr)
        recs = mt.timeline_records(eid, a, win_end) if a else []
        agg = aggregate(recs)
        if (agg.get("total_sec") or 0) > 0:
            out[eid] = agg
    return out


def _demo_yield_scoreboard(days=3):
    """合成全線產出/良率計分板（離線開發/demo）；真 EQP_STATIONS class 名、口徑與 live 一致。"""
    now = datetime.now().replace(microsecond=0)
    board = [_demo_station_rec(cls, i, with_machines=False, blank=(i % 9 == 7))
             for i, cls in enumerate(mt.EQP_STATIONS)]
    return _wrap_yield_scoreboard("demo", now, days, my.CALIBER, board, [])


def get_yield_scoreboard(source="demo", days=3, snapshot_path=None):
    """全線產出/良率計分板（§26.2 T1）。同 get_scoreboard 三源語意（demo/snapshot/live）。"""
    if _synth_proc():                       # §5 wd:1741（N；M4 第二期）：明確未提供、不落 demo
        raise _wsio().not_provided("yield_scoreboard")
    if source == "live":
        return get_yield_scoreboard_live(days)
    if source == "snapshot":
        if not snapshot_path:
            raise ValueError("source=snapshot 需提供 yield scoreboard snapshot_path")
        return get_yield_scoreboard_snapshot(snapshot_path, days)
    return _demo_yield_scoreboard(days)


# ---- 日曆窗產出/良率計分板（/daily 昨日戰報；窗＝[start, end) naive 台北牆鐘）------------------
def _range_filter_yield(yb, stations, start, end, unsupported):
    """demo/snapshot：把整盤過濾到指定 stations（+ 重算 rollup）並蓋窗鍵。"""
    if stations is not None:
        want = set(stations)
        stns = [s for s in (yb.get("stations") or []) if s.get("cls") in want]
        yb = {**yb, "stations": stns, "rollup": yield_rollup(stns)}
    else:
        yb = {**yb}
    yb["win_start"], yb["win_end"] = start, end
    if unsupported:
        yb["range_unsupported"] = True
    return yb


def get_yield_scoreboard_range(source, start, end, stations=None, snapshot_path=None):
    """日曆窗版產出/良率計分板（/daily 昨日戰報）；窗＝[start, end) naive 台北牆鐘（半開）。
       live＝凍結 my.scoreboard_all_stations(win=(start,end)) 真裁窗（站級、不下鑽機台，daily 不需）；
       demo＝確定性合成（數字不依賴窗、標 RAW v0）；snapshot＝聚合快照無法重切→誠實 fallback。
       回 canonical + win_start/win_end 鍵。"""
    if _synth_proc():                       # §5 wd:1770（N；M4 第二期）
        raise _wsio().not_provided("yield_scoreboard_range")
    if source == "live":
        now, board, skipped = my.scoreboard_all_stations(
            days=max(1, (datetime.now() - start).days + 1), stations=stations, win=(start, end))
        yb = _wrap_yield_scoreboard("live", end, 1, my.CALIBER, board, skipped)
        yb["win_start"], yb["win_end"] = start, end
        return yb
    if source == "snapshot":
        if not snapshot_path:
            raise ValueError("source=snapshot 需提供 yield scoreboard snapshot_path")
        yb = get_yield_scoreboard_snapshot(snapshot_path, days=1)
        return _range_filter_yield(yb, stations, start, end, unsupported=True)
    yb = _demo_yield_scoreboard(days=1)                 # demo 數字不依賴窗（標 RAW v0）
    return _range_filter_yield(yb, stations, start, end, unsupported=False)


# ======================================================================================
# ---- Qlik 口徑站級/線級 grid（D1 雙軌制；語意核心＝新凍結模組 `mes_qlik`）--------------
#   本節是 **唯一 import `mes_qlik` 的地方**（消費面一律經 app.load_qlik_grid → 這裡）。
#   語意權威＝`_QLIK-RECON-PROBES3.sql` + `_QLIK-RECON-MAIN.md` §9.1/§10；本檔只做三源 canonical
#   （live 呼叫 mes_qlik、demo 確定性合成、snapshot 誠實降級），**不重述任何 first-FAIL 語意**。
#   機台級 yield 與整個設備軸維持 raw（get_yield_* 全部原樣不動）＝D1 雙軌制的另一軌。
# ======================================================================================
QLIK_SCHEMA = "board.qlik_grid.v1"
QLIK_CALIBER = ("qlik first-FAIL (company semantic baseline; "
                "SALTMARSH PH Engineering Dashboard v3.0)")
QLIK_KPI_KEYS = ("OVERALL", "PH", "PC", "PPC", "BEFORE_DA", "FROM_DA", "VOQC")

_MQ = None
_MQ_TRIED = False


def qlik_module():
    """lazy import `mes_qlik`（B1 平行交付；未就緒 → None ＝ 全面誠實降級、絕不炸整站）。
       lazy 的第二個理由：demo/snapshot 路徑不需要 pyodbc/MES，import 失敗不該讓前門掛掉。"""
    global _MQ, _MQ_TRIED
    if not _MQ_TRIED:
        _MQ_TRIED = True
        try:
            import mes_qlik as _m
            _MQ = _m
        except Exception as e:                    # 模組未就緒/相依缺 → 記一行、走降級
            print(f"[board_data] mes_qlik unavailable ({type(e).__name__}: {str(e)[:120]}) "
                  f"→ Qlik-caliber views degrade honestly", flush=True)
            _MQ = None
    return _MQ


def qlik_unavailable(reason, ws=None, we=None, source=None, product="plover"):
    """Qlik grid 誠實降級形（D8）：canonical 骨架 + `unavailable` 原因；**絕不拿 raw 頂替、絕不合成**。
       消費面一律以 `qlik_ok()` 判定，unavailable 時站級/線級 yield·output 一律顯「—」。"""
    return {"schema": QLIK_SCHEMA, "caliber": QLIK_CALIBER, "source": source, "product": product,
            "unavailable": reason, "win_start": ws, "win_end": we, "cap": None,
            "stations": [], "kpis": {}, "absorption": [], "absorbed_total": None,
            "diag": {"degraded": True}}


def qlik_ok(grid):
    """grid 可用＝非 None、無 `unavailable`、且有站列（空盤＝不可用，寧顯「—」不顯 0）。"""
    return bool(grid) and not grid.get("unavailable") and bool(grid.get("stations"))


def qlik_rows_by_cls(grid):
    """{cls: station row}（不可用 → 空 dict ＝ 消費端自然全部落「—」）。"""
    if not qlik_ok(grid):
        return {}
    return {r.get("cls"): r for r in grid["stations"] if r.get("cls")}


def qlik_kpi_pct(grid, key="OVERALL"):
    """KPI 百分比（0–100 float）或 None（無資料／不可用；**絕不回 100.0 當預設**）。"""
    if not qlik_ok(grid):
        return None
    k = (grid.get("kpis") or {}).get(key) or {}
    v = k.get("pct")
    return v if isinstance(v, (int, float)) else None


def _qlik_window(days=None, start=None, end=None, now=None):
    """觀測窗 [ws, we)（台北 naive 牆鐘）。start/end 走與其他頁同一個 resolve_window 語意。"""
    now = now or datetime.now().replace(microsecond=0)
    return resolve_window(now, days or 3, start, end)


def _demo_qlik_grid(ws, we, product="plover"):
    """確定性合成 Qlik grid（D8）：以 `mes_qlik` 的 43 站人工審定常數為骨架（**絕不自己另存一份
       站清單**——mes_qlik 未就緒即誠實降級），seeded rnd 造單調遞減 flow + 少量 fail，
       **必含隱形吸收站示範**（segPostLamInspection）；KPI 由同一組列現算（恆等 by
       construction、絕不手填）。全合成＝頁面上仍掛 demo 大牌。"""
    mq = qlik_module()
    if mq is None:
        return qlik_unavailable("mes_qlik_module_missing", ws, we, "demo", product)
    if product not in mq.PRODUCTS:
        product = "plover"
    profile = mq.PRODUCTS[product]
    import random

    # Plover 一路保持原有的 station loop 和 seed，避免 Kestrel 的加入改變任何 demo 數字。
    rows, cohort = [], 1180
    for ordn, cls, mcl in profile["stations"]:
        rnd = (random.Random(ordn * 977 + 13) if product == "plover"
               else random.Random(f"{product}:{ordn * 977 + 13}"))
        raw_display = (profile["display"] or {}).get(ordn)
        display = raw_display or cls
        # ★ #105：判準與 `mes_qlik.build_grid` **逐字同一條**（demo 與 live 不得兩種說法）：
        #   `display_pending` ＝兩張表都沒有名字；`display_source` ＝那張表的來源標記
        #   （加性欄；profile 沒帶 `display_src` ⇒ 有名字就算 "qlik"）。
        display_pending = raw_display is None
        display_source = ((profile.get("display_src") or {}).get(ordn)
                          or ("qlik" if raw_display else None))
        if product == "plover" and ordn == 30:  # Plover 螢幕缺列：維持既有 deterministic fixture
            rows.append({"ord": ordn, "cls": cls, "mcl": mcl, "display": display,
                         "display_pending": False, "display_source": display_source,
                         "total": 0, "pass": 0, "fail": 0, "final_yield_pct": None,
                         "fpy_pct": None, "fpy_num": 0, "fpy_den": 0,
                         "n_allphid": 0, "n_endfail": 0, "n_null": 0,
                         "absorbed_elsewhere": 0})
            continue
        total = cohort
        fail = rnd.randint(0, 3) if ordn % 4 else rnd.randint(2, 9)
        fail = min(fail, max(0, total - 1))
        passed = total - fail
        absorbed = 1 if ordn in (8, 26) else 0          # raw 末次死、first-FAIL 記在別站（吸收）
        fpy_den = total + rnd.randint(0, 40)            # 未篩 Fresh（T-Q3）⇒ 母體可略大於 Fresh 切面
        fpy_num = max(0, fpy_den - fail - rnd.randint(0, 12))
        rows.append({
            "ord": ordn, "cls": cls, "mcl": mcl, "display": display,
            "display_pending": display_pending, "display_source": display_source,
            "total": total, "pass": passed, "fail": fail,
            "final_yield_pct": (100.0 * passed / (passed + fail)) if (passed + fail) else None,
            "fpy_pct": (100.0 * fpy_num / fpy_den) if fpy_den else None,
            "fpy_num": fpy_num, "fpy_den": fpy_den,
            "n_allphid": total, "n_endfail": fail + absorbed, "n_null": 0,
            "absorbed_elsewhere": absorbed,
        })
        cohort = max(60, passed)
    absorption = [
        {"station": "segPostLamInspection", "mcl": "assyModule",
         "bucket": "invisible_station", "n_lots": 7},
        {"station": "segASSY", "mcl": "assyProductionReliabilityTesting",
         "bucket": "invisible_station", "n_lots": 1},
        {"station": "segDiePlacement", "mcl": "assyModule",
         "bucket": "sorting_cut", "n_lots": 2},
        {"station": "segWireBonding", "mcl": "assyModule",
         "bucket": "non_b", "n_lots": 1},
    ]
    # KPI 用**同一個 `mes_qlik.rty()` 純函式**現算（恆等 by construction、絕不手填）；
    # rows_by_ord ＝ {ord: (num, den)}＝(first-FAIL PASS, PASS+FAIL)（[4A] 因子定義）。
    kpis = mq.rty({r["ord"]: (r["pass"], r["pass"] + r["fail"]) for r in rows},
                  profile["stations"], profile["groups"], profile["expected"])
    return {
        "schema": QLIK_SCHEMA, "caliber": QLIK_CALIBER, "source": "demo", "product": product,
        "win_start": ws, "win_end": we, "cap": we,
        "stations": rows, "kpis": kpis,
        "absorption": absorption,
        "absorbed_total": sum(a["n_lots"] for a in absorption),
        "diag": {"degraded": False, "demo": True, "n_win_lots": rows[0]["total"] if rows else 0,
                 "qaconfig": "not_replicated", "golden": "not_replicated",
                 "desig_filter": "not_replicated", "mcl_filter": "applied", "caveats": []},
    }


def get_qlik_grid_live(days=None, start=None, end=None, product="plover"):
    """live：一條連線跑 `mes_qlik.compute_qlik_grid`（兩段式母體收斂、#temp join；R5 perf lore
       全在 mes_qlik 內）。cap＝查詢當下 `GETDATE()`＝『as-of now』的公司口徑（§4.2 產品語意；
       對表驗收才走 CLI 的 --cap <reload 時刻>）。逾時上限同其他 live 路徑。"""
    mq = qlik_module()
    if mq is None:
        ws, we = _qlik_window(days, start, end)
        return qlik_unavailable("mes_qlik_module_missing", ws, we, "live", product)
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC
    try:
        cur.execute("SELECT GETDATE()")
        cap = cur.fetchone()[0]
        ws, we = _qlik_window(days, start, end, now=cap)
        grid = mq.compute_qlik_grid(cur, (ws, we), cap, product=product)
    finally:
        conn.close()
    out = dict(grid or {})
    out["source"] = "live"
    return out


def get_qlik_grid_snapshot(path, ws, we, require_window=True, product="plover"):
    """snapshot：`python mes_qlik.py --start … --end … --json > snapshot_qlik.json` 的存檔。

    · `require_window=True`（**使用者顯式給了 start/end**）：窗/schema 任一不符即誠實降級
      （D8：聚合切面無法重切成別的窗；絕不拿別窗數字冒充）。
    · `require_window=False`（**沒給窗**＝頁面預設滾動窗）：改**供應快照自己的窗**並標記
      `window_from_snapshot=True`（頁面照它渲染窗標籤）。理由：預設滾動窗錨在「此刻」，
      逐秒比對永遠不可能相等 ⇒ 舊行為讓 `?source=snapshot` 結構性永遠全「—」＝死路，
      而「照快照自身的窗供應且標示出來」正是姊妹 `get_yield_scoreboard_snapshot` 的既有慣例，
      同樣誠實（標的就是快照自己的窗、不是使用者要的窗）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return qlik_unavailable("snapshot_missing", ws, we, "snapshot", product)
    except Exception as e:
        return qlik_unavailable(f"snapshot_unreadable:{type(e).__name__}", ws, we, "snapshot", product)
    if (data or {}).get("schema") != QLIK_SCHEMA:
        return qlik_unavailable("snapshot_schema_mismatch", ws, we, "snapshot", product)
    s0, e0 = _parse_dt(data.get("win_start")), _parse_dt(data.get("win_end"))
    if require_window and (s0 != ws or e0 != we):
        return qlik_unavailable("snapshot_window_mismatch", ws, we, "snapshot", product)
    if s0 is None or e0 is None:
        return qlik_unavailable("snapshot_window_unreadable", ws, we, "snapshot", product)
    out = dict(data)
    out["source"] = "snapshot"
    out.setdefault("product", product)
    out["win_start"], out["win_end"] = s0, e0
    out["window_from_snapshot"] = bool(not require_window)
    return out


def get_qlik_grid(source="demo", days=None, start=None, end=None, snapshot_path=None,
                  snap_kestrel_path=None, product="plover"):
    """Qlik 口徑站級/線級 grid（canonical §3.4 形）。三源語意同其他計分板；**任何失敗都回
       `qlik_unavailable(...)` 而不是 raise**（消費面 43 列／KPI／吸收行一律落「—」）。

       snapshot 的窗語意見 `get_qlik_grid_snapshot`：**只有使用者顯式帶 start/end 時才要求
       逐秒相等**；沒帶就照快照自身的窗供應並標記（否則預設滾動窗永遠對不上＝結構性死路）。"""
    mq = qlik_module()
    if mq is not None and product not in mq.PRODUCTS:
        product = "plover"
    elif mq is None and product not in ("plover", "kestrel"):
        product = "plover"
    ws, we = _qlik_window(days, start, end)
    if _synth_proc():                       # §5 wd:1999（N；M5 第二期）：既有降級形、不落 demo
        return qlik_unavailable(_wsio().NOT_PROVIDED, ws, we, _synth_src(), product)
    if source == "live":
        return get_qlik_grid_live(days, start, end, product=product)
    if source == "snapshot":
        path = (snap_kestrel_path or os.environ.get("BOARD_QLIK_SNAP_KESTREL",
                                                     "snapshot_qlik_kestrel.json")
                if product == "kestrel" else snapshot_path or "snapshot_qlik.json")
        return get_qlik_grid_snapshot(path, ws, we, require_window=bool(start and end),
                                      product=product)
    return _demo_qlik_grid(ws, we, product=product)


# ======================================================================================
# ---- MES-WIP 入站佇列（`board.wip.v1`；/dispatch 調度板的 cover 軸；計算核心＝新模組 `mes_wip`）--
#   規格＝`SPEC-DISPATCH.md` §4（API pin）/§5（口徑裁決）。本節與上面的 Qlik 節同構：
#   **只做三源 canonical**（live 呼叫 mes_wip、demo 確定性合成、snapshot 誠實降級），
#   **不重述任何佇列語意**（站序/stpPK 位置/排除桶全在 mes_wip；本檔一個位元都不複製）。
#   demo 情境參數表住 `board_dispatch.DEMO_STATIONS`（與 util 軸同一張表 ⇒ 兩軸情境對得上）。
# ======================================================================================
WIP_SCHEMA = "board.wip.v1"
WIP_CALIBER = "mes-wip live-lot queue (plover B-prefix, 43-route, stpPK-position)"
# demo 專用：pass-through 站（灰列示範）。**live 權威永遠是 `mes_wip.WIP_PASSTHROUGH_ORDS`**——
# 模組在時一律以它為準，此值只在 mes_wip 尚未就緒的離線 demo 下當情境骨架（SPEC §11）。
_DEMO_PASSTHROUGH_ORDS = (30,)

_MW = None
_MW_TRIED = False


def wip_module():
    """lazy import `mes_wip`（B1 平行交付；未就緒 → None ＝ 誠實降級、絕不炸整站；同 qlik_module）。"""
    global _MW, _MW_TRIED
    if not _MW_TRIED:
        _MW_TRIED = True
        try:
            import mes_wip as _m
            _MW = _m
        except Exception as e:
            print(f"[board_data] mes_wip unavailable ({type(e).__name__}: {str(e)[:120]}) "
                  f"→ dispatch WIP degrades honestly", flush=True)
            _MW = None
    return _MW


def wip_unavailable(reason, source=None):
    """WIP 誠實降級形：canonical 骨架 + `unavailable` 原因；**絕不拿別的數字頂替、絕不合成**。
       消費面（/dispatch）一律以 `wip_ok()` 判定；不可用時整板顯降級說明，不出任何燈。"""
    return {"schema": WIP_SCHEMA, "caliber": WIP_CALIBER, "source": source, "ok": False,
            "unavailable": reason, "cap": None, "params": {}, "stations": [],
            "line": {}, "diag": {"degraded": True}, "elapsed_s": None}


def wip_ok(wip):
    """可用＝非 None、無 `unavailable`、且有站列（空盤＝不可用，寧顯降級不顯 0）。"""
    return bool(wip) and not wip.get("unavailable") and bool(wip.get("stations"))


def _wip_passthrough_ords():
    mw = wip_module()
    v = getattr(mw, "WIP_PASSTHROUGH_ORDS", None) if mw is not None else None
    return tuple(v) if v else _DEMO_PASSTHROUGH_ORDS


def _demo_wip(params=None):
    """確定性合成 `board.wip.v1`：骨架站序取自 **`mes_qlik.QLIK_STATIONS`**（人工審定 43 站；
       **絕不自己另存一份站清單**——mes_qlik 未就緒即誠實降級），每站的佇列/速率由
       `board_dispatch.demo_station_wip` 產（seeded、與 util 軸共用同一張情境表）。
       全合成＝頁面仍掛「全合成假資料」大牌。"""
    mq = qlik_module()
    if mq is None:
        return wip_unavailable("mes_qlik_module_missing", "demo")
    import board_dispatch as wdisp
    p = dict(params or {})
    rwh = p.get("rate_win_h") or wdisp.DEMO_RATE_WIN_H
    rfd = p.get("rate_fallback_d") or wdisp.DEMO_RATE_FALLBACK_D
    pt = set(_wip_passthrough_ords())
    rows = []
    for ordn, cls, mcl in mq.QLIK_STATIONS:
        row = {"ord": ordn, "cls": cls, "mcl": mcl,
               "display": (getattr(mq, "QLIK_DISPLAY", {}) or {}).get(ordn) or cls}
        row.update(wdisp.demo_station_wip(ordn, cls, rate_win_h=rwh, rate_fallback_d=rfd,
                                          passthrough=(ordn in pt)))
        rows.append(row)
    cap = datetime.now().replace(microsecond=0)
    return {
        "schema": WIP_SCHEMA, "ok": True, "caliber": WIP_CALIBER, "source": "demo",
        "cap": cap.isoformat(),
        "params": {"age_cap_d": p.get("age_cap_d", 14), "rate_win_h": rwh,
                   "rate_fallback_d": rfd, "lookback_d": p.get("lookback_d", 21),
                   "h_lookback_d": p.get("h_lookback_d", 60)},
        "stations": rows,
        # ★ `live_total` 的 pin 不變式（`mes_wip.derive_wip`、其 selftest 11.19 釘死）＝
        #   Σqueue ＋ Σstale ＋ Σfail_hold（＋判定分歧，demo 恆 0）——**不是只有 Σqueue**。
        #   demo 少算 stale/fail_hold 的話，footer 的「在製 N 顆」在 demo 與 live 兩模式語意
        #   不同、使用者照 live 語意對帳會對不平（調度板的賣點就是每個數字都能自己驗）。
        "line": {"live_total": sum(r["queue_n"] + r["stale_n"] + r["fail_hold_n"]
                                   for r in rows),
                 "queue_total": sum(r["queue_n"] for r in rows),
                 "stale_total": sum(r["stale_n"] for r in rows),
                 "fail_hold_total": sum(r["fail_hold_n"] for r in rows),
                 "excluded_dead": 0, "excluded_done": 0, "excluded_assy": 0,
                 "excluded_off_route": 0, "excluded_non_b": 0},
        "diag": {"degraded": False, "demo": True, "dead_filter": "applied",
                 "status_census": {}, "zero_tput_with_queue": [],
                 "zero_tput_win_d": 30},
        "elapsed_s": 0.0,
    }


def get_wip_live(params=None):
    """live：一條連線跑 `mes_wip.compute_wip`（兩段式母體收斂、#temp join；perf lore 全在 mes_wip 內）。
       cap 預設＝查詢當下（mes_wip 自己一次 `GETDATE()`）＝『as-of now』；逾時上限同其他 live 路徑。
       **恆不 raise**：模組缺/schema 分歧/連線失敗 → `wip_unavailable(...)`。"""
    mw = wip_module()
    if mw is None:
        return wip_unavailable("mes_wip_module_missing", "live")
    p = dict(params or {})
    kw = {k: p[k] for k in ("age_cap_d", "rate_win_h", "rate_fallback_d",
                            "lookback_d", "h_lookback_d") if p.get(k) is not None}
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC
    try:
        wip = mw.compute_wip(cur, **kw)
    finally:
        conn.close()
    out = dict(wip or {})
    out["source"] = "live"
    return out


def get_wip(source="demo", params=None):
    """MES-WIP canonical（§4.4 pin）。三源語意同其他計分板；**任何失敗都回 `wip_unavailable(...)`
       而不是 raise**（/dispatch 整板落誠實降級、絕不假造佇列）。

       snapshot v1＝**不支撐**（D6：聚合切面無法重建「此刻」的在製位置——WIP 是 as-of now 的
       物理事實，拿舊切面冒充等於謊報現場料況）→ 誠實降級，不硬造。"""
    if _synth_proc():                       # §5 wd:2134（N；M6 第二期）
        return wip_unavailable(_wsio().NOT_PROVIDED, _synth_src())
    if source == "live":
        try:
            return get_wip_live(params)
        except Exception as e:
            return wip_unavailable(f"error:{type(e).__name__}", "live")
    if source == "snapshot":
        return wip_unavailable("snapshot_unsupported", "snapshot")
    try:
        return _demo_wip(params)
    except Exception as e:
        return wip_unavailable(f"error:{type(e).__name__}", "demo")


# ======================================================================================
# ---- Hermes 記錄級查詢（`board.station_vars.v1`／`board.wo_serials.v1`；核心＝`mes_hermes`）---
#   規格＝`SPEC-HERMES-TRUST.md` §7.5（九條限制）／§7.6.6／§7.6.7。本節與 WIP 節同構：
#   **只做取數與誠實降級**，一個口徑字都不在這裡重述（口徑住 `mes_hermes` 的 docstring）。
#
#   ★ 三源語意（形逐條照 `get_wip`）：
#     · `live`     → 一條連線跑 `mes_hermes.compute_*`（`conn.timeout` ＋ `finally: close()`）。
#     · `snapshot` → **工單序號現況不支撐**（同 D6 理由：那是 as-of now 的物理事實，拿舊切面
#                    冒充＝謊報現場）；**站級變數字典支撐**（它是字典不是量測、沒有 as-of 問題）。
#     · `demo`     → `_fixtures/` 的 golden。
#   ⚠ **「fixture 沒有這一筆」與「MES 裡真的沒有」必須分得開**（本節最容易寫錯的一件事）：
#     前者 `unavailable_reason="not_in_demo_fixture"`、後者 `reason="class_not_in_mes"`／
#     `"no_production_request_matched"`。混用＝把「我沒這份樣本」說成「現場沒這東西」。
# ======================================================================================
_MH = None
_MH_TRIED = False
_MH_FIXTURES = {}
HERMES_FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures")


def hermes_mes_module():
    """lazy import `mes_hermes`（未就緒 → None ＝ 誠實降級、絕不炸整站；同 `wip_module`）。"""
    global _MH, _MH_TRIED
    if not _MH_TRIED:
        _MH_TRIED = True
        try:
            import mes_hermes as _m
            _MH = _m
        except Exception as e:                        # noqa: BLE001
            print(f"[board_data] mes_hermes unavailable ({type(e).__name__}: {str(e)[:120]}) "
                  f"→ Hermes record-level tools degrade honestly", flush=True)
            _MH = None
    return _MH


def _hermes_fixture(name):
    """讀 `_fixtures/<name>`（進程內快取一份）。讀不到 → None（呼叫端誠實降級）。"""
    if name in _MH_FIXTURES:
        return _MH_FIXTURES[name]
    doc = None
    try:
        with open(os.path.join(HERMES_FIXTURE_DIR, name), encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as e:                            # noqa: BLE001
        print(f"[board_data] fixture {name} unavailable ({type(e).__name__})", flush=True)
    _MH_FIXTURES[name] = doc
    return doc


def _hermes_unavailable(kind, key, reason, source, error_detail=None):
    """記錄級工具的誠實降級形（骨架鍵集合與成功形相同＝消費端不必分兩種形狀）。

    ★ `error_detail`（2026-09-21 live 首跑教訓）：**live 的例外是在這裡被接住的**
      （`get_*` 恆不 raise ⇒ `board_mcp` 的 except 永遠輪不到），所以「錯誤要可診斷」
      這件事只能落在這一層。給了就寫進 `diag.error_detail`（格式＝`mes_hermes.error_detail`
      單一出處）；不給就一個位元都不多——**非 live 的三條降級路徑形狀零改動**。
    """
    mh = hermes_mes_module()
    if mh is not None:
        blank = (mh._vars_blank(key, found=False, unavailable_reason=reason)
                 if kind == "station_vars"
                 else mh._wo_blank(key, found=False, unavailable_reason=reason))
    elif kind == "station_vars":                      # 模組都沒了 ⇒ 最小誠實骨架
        blank = {"cls": key, "found": False, "class_description": None, "n_vars": None,
                 "groups": [], "uom_census": {}, "notes": [], "reason": None,
                 "unavailable_reason": reason, "diag": {}}
    else:
        blank = {"wo_id": key, "found": False, "work_order": None, "serial_count": None,
                 "qty_requested": None, "serial_count_note": None, "n_serial_lot_dst": None,
                 "n_rework_instances": None, "last_station_distribution": [], "serials": [],
                 "caveats": [], "reason": None, "unavailable_reason": reason, "diag": {}}
    if error_detail:
        blank["diag"]["error_detail"] = error_detail
    blank["source"] = source
    return blank


def get_station_vars(source="demo", cls=None):
    """`board.station_vars.v1` canonical（三源分派）。**恆不 raise。**

    `snapshot` 走 demo 的 golden：站級變數**字典**不是量測，沒有 as-of 問題（與
    `get_wo_serials` 的 `snapshot_unsupported` 刻意不同——那一支答的是現場物理狀態）。
    """
    if _synth_proc():                       # §5 wd:2233（N；M12）：不落 demo fixture（AP-8）
        return _hermes_unavailable("station_vars", cls, _wsio().NOT_PROVIDED, _synth_src())
    mh = hermes_mes_module()
    if mh is None:
        return _hermes_unavailable("station_vars", cls, "mes_hermes_module_missing", source)
    if source == "live":
        try:
            conn, cur = mh.open_connection()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                out = mh.compute_station_vars(cur, cls)
            finally:
                conn.close()
            out["source"] = "live"
            return out
        except Exception as e:                        # noqa: BLE001
            return _hermes_unavailable("station_vars", cls,
                                       f"error:{type(e).__name__}", "live",
                                       error_detail=mh.error_detail(e))
    doc = _hermes_fixture("board.station_vars.v1.json")
    if not doc:
        return _hermes_unavailable("station_vars", cls, "fixture_unavailable", source)
    for g in doc.get("golden") or []:
        if g.get("cls") == cls:
            out = {k: v for k, v in g.items() if not k.startswith("_")}
            out["source"] = source
            return out
    # ★ **不是** `class_not_in_mes`：demo 只是沒有這一筆樣本，不代表 MES 沒有這一站。
    return _hermes_unavailable("station_vars", cls, "not_in_demo_fixture", source)


def get_wo_serials(source="demo", wo_id=None):
    """`board.wo_serials.v1` canonical（三源分派）。**恆不 raise。**

    `snapshot` ⇒ **不支撐**（逐字同 `get_wip` 的 D6 理由）：工單序號現況是 **as-of now** 的
    物理事實，拿舊切面冒充等於謊報現場——寧可誠實降級，不硬造。
    """
    if _synth_proc():                       # §5 wd:2268（N；M12）
        return _hermes_unavailable("wo_serials", wo_id, _wsio().NOT_PROVIDED, _synth_src())
    mh = hermes_mes_module()
    if mh is None:
        return _hermes_unavailable("wo_serials", wo_id, "mes_hermes_module_missing", source)
    if source == "live":
        try:
            conn, cur = mh.open_connection()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                out = mh.compute_wo_serials(cur, wo_id)
            finally:
                conn.close()
            out["source"] = "live"
            return out
        except Exception as e:                        # noqa: BLE001
            return _hermes_unavailable("wo_serials", wo_id, f"error:{type(e).__name__}", "live",
                                       error_detail=mh.error_detail(e))
    if source == "snapshot":
        return _hermes_unavailable("wo_serials", wo_id, "snapshot_unsupported", "snapshot")
    doc = _hermes_fixture("board.wo_serials.v1.json")
    if not doc:
        return _hermes_unavailable("wo_serials", wo_id, "fixture_unavailable", source)
    for g in doc.get("golden") or []:
        if g.get("wo_id") == wo_id or (g.get("work_order") or {}).get("sap_wo") == wo_id:
            out = {k: v for k, v in g.items() if not k.startswith("_")}
            out["source"] = source
            return out
    # ★ **不是** `no_production_request_matched`：demo 只是沒有這一張單的樣本。
    return _hermes_unavailable("wo_serials", wo_id, "not_in_demo_fixture", source)


# ---- 助理 MES 查詢第三批·包①：材料／批號／出貨（核心＝`mes_material`）------------------
#   形逐條照 `get_wo_serials`（**恆不 raise**、只做取數與誠實降級、口徑住 `mes_material`）：
#     · `live`     → 一條連線跑 `mes_material.compute(tool, cur, key)`。
#     · `snapshot` → **不支撐**（同 `get_wo_serials` 的 D6 理由：拿舊切面冒充＝謊報現場）。
#     · `demo`     → `_fixtures/mes_material_<tool>.json`，跑**同一支** `derive_*`；
#                    查不到 ⇒ `not_in_demo_fixture`（**不是** `no_material_matched`）。
_MM = None
_MM_TRIED = False


def mes_material_module():
    """lazy import `mes_material`（未就緒 → None ＝ 誠實降級；同 `hermes_mes_module`）。"""
    global _MM, _MM_TRIED
    if not _MM_TRIED:
        _MM_TRIED = True
        try:
            import mes_material as _m
            _MM = _m
        except Exception as e:                        # noqa: BLE001
            print(f"[board_data] mes_material unavailable ({type(e).__name__}: "
                  f"{str(e)[:120]}) → material tools degrade honestly", flush=True)
            _MM = None
    return _MM


def _synth_tool_unavailable():
    """合成進程下 M12 工具族（material／etest／equipment／route／segment）的「未提供」形＝各工具
       「模組缺」分支的既有最小骨架（`found=False`＋`unavailable_reason`）；不 import 任何 mes_ 模組、
       不讀 fixture（AP-8；清冊 §5 part5 §7-6）。"""
    return {"found": False, "unavailable_reason": _wsio().NOT_PROVIDED,
            "source": _synth_src(), "diag": {}}


def _mm_unavailable(mm, tool, key, reason, source, error_detail=None):
    out = mm.blank(tool, key, found=False, unavailable_reason=reason)
    if error_detail:
        out["diag"]["error_detail"] = error_detail
    out["source"] = source
    return out


def get_mes_material(tool, source="demo", key=None):
    """`serial_genealogy`／`wo_materials`／`lot_where_used`／`shipment_lookup` 的三源分派。
    **恆不 raise**（`mes_material` 整支載不到時回最小骨架 `{found: False, unavailable_reason}`）。
    `key` 必須已經過 `mes_material.normalize_*`。"""
    if _synth_proc():                       # §5 wd:2336（N；M12）
        return _synth_tool_unavailable()
    mm = mes_material_module()
    if mm is None:
        return {"found": False, "unavailable_reason": "mes_material_module_missing",
                "source": source, "diag": {}}
    if source == "live":
        try:
            conn, cur = mm.open_connection()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                out = mm.compute(tool, cur, key)
            finally:
                conn.close()
            out["source"] = "live"
            return out
        except Exception as e:                        # noqa: BLE001
            return _mm_unavailable(mm, tool, key, f"error:{type(e).__name__}", "live",
                                   error_detail=mm.error_detail(e))
    if source == "snapshot":
        return _mm_unavailable(mm, tool, key, "snapshot_unsupported", "snapshot")
    doc = _hermes_fixture(mm.FIXTURE_FILES[tool])
    if not doc:
        return _mm_unavailable(mm, tool, key, "fixture_unavailable", source)
    out = mm.demo_lookup(tool, doc, key)
    if out is None:
        return _mm_unavailable(mm, tool, key, "not_in_demo_fixture", source)
    out["source"] = source
    return out


# ---- MES 工具包②·包 3：失效碼排行／die 級電測（核心＝`mes_etest`；ROADMAP #176）------------
#   形逐條照 `get_mes_material`（**恆不 raise**、只做取數與誠實降級、口徑住 `mes_etest`）：
#     · 先跑 `mes_etest.precheck`（不支援的站 ⇒ `station_unsupported`，**不開 MES 連線**）。
#     · `live`     → 一條連線跑 `mes_etest.compute(tool, cur, key)`。
#     · `snapshot` → **不支撐**（同 `get_mes_material`：拿舊切面冒充＝謊報現場）。
#     · `demo`     → `_fixtures/mes_etest_<tool>.json`，跑**同一支** `derive_*`；
#                    查不到 ⇒ `not_in_demo_fixture`（**不是** `serial_not_found`）。
_ME = None
_ME_TRIED = False


def mes_etest_module():
    """lazy import `mes_etest`（未就緒 → None ＝ 誠實降級；同 `mes_material_module`）。"""
    global _ME, _ME_TRIED
    if not _ME_TRIED:
        _ME_TRIED = True
        try:
            import mes_etest as _m
            _ME = _m
        except Exception as e:                        # noqa: BLE001
            print(f"[board_data] mes_etest unavailable ({type(e).__name__}: "
                  f"{str(e)[:120]}) → etest tools degrade honestly", flush=True)
            _ME = None
    return _ME


def get_mes_etest(tool, source="demo", key=None):
    """`station_failure_pareto`／`module_die_etest` 的三源分派。**恆不 raise**。
    `key` 必須已經過 `mes_etest.normalize_pareto_args`（dict `{cls, days}`）／`normalize_serial`（str）。"""
    if _synth_proc():                       # §5 wd:2402（N；M12）
        return _synth_tool_unavailable()
    me = mes_etest_module()
    if me is None:
        return {"found": False, "unavailable_reason": "mes_etest_module_missing",
                "source": source, "diag": {}}
    try:
        pre = me.precheck(tool, key)
    except Exception as e:                            # noqa: BLE001
        return _mm_unavailable(me, tool, key, f"error:{type(e).__name__}", source,
                               error_detail=me.error_detail(e))
    if pre is not None:
        pre["source"] = source
        return pre
    if source == "live":
        try:
            conn, cur = me.open_connection()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                out = me.compute(tool, cur, key)
            finally:
                conn.close()
            out["source"] = "live"
            return out
        except Exception as e:                        # noqa: BLE001
            return _mm_unavailable(me, tool, key, f"error:{type(e).__name__}", "live",
                                   error_detail=me.error_detail(e))
    if source == "snapshot":
        return _mm_unavailable(me, tool, key, "snapshot_unsupported", "snapshot")
    doc = _hermes_fixture(me.FIXTURE_FILES[tool])
    if not doc:
        return _mm_unavailable(me, tool, key, "fixture_unavailable", source)
    try:
        out = me.demo_lookup(tool, doc, key)
    except Exception as e:                            # noqa: BLE001
        return _mm_unavailable(me, tool, key, f"error:{type(e).__name__}", source,
                               error_detail=me.error_detail(e))
    if out is None:
        return _mm_unavailable(me, tool, key, "not_in_demo_fixture", source)
    out["source"] = source
    return out


# ---- MES 工具包②·包 1：設備事件／父子槽位／校正歷史（核心＝`mes_equipment`）-------------
#   形逐條照 `get_mes_etest`（**恆不 raise**、只做取數與誠實降級、口徑住 `mes_equipment`）：
#     · 先跑 `mes_equipment.precheck`（目前恆 None；保留介面）。
#     · `live`     → 一條連線跑 `mes_equipment.compute(tool, cur, key)`（EID 查無 ⇒ `eid_not_found`）。
#     · `snapshot` → **不支撐**（同 `get_mes_material`：拿舊切面冒充＝謊報現場）。
#     · `demo`     → `_fixtures/mes_equipment_<tool>.json`（07905／08158／35535），跑**同一支** `derive_*`；
#                    查不到 ⇒ `not_in_demo_fixture`（**不是** `eid_not_found`）。
#: ⚠ 名字刻意不是 `_MQ`：那個全域是 `qlik_module()`（mes_qlik）的快取——整合包 2026-09-24 抓到撞名
#:   （先叫到的一方贏、另一方拿到錯的模組 ⇒ Qlik 口徑整站或設備三支工具靜默壞掉）。
_MEQ = None
_MEQ_TRIED = False


def mes_equipment_module():
    """lazy import `mes_equipment`（未就緒 → None ＝ 誠實降級；同 `mes_etest_module`）。"""
    global _MEQ, _MEQ_TRIED
    if not _MEQ_TRIED:
        _MEQ_TRIED = True
        try:
            import mes_equipment as _m
            _MEQ = _m
        except Exception as e:                        # noqa: BLE001
            print(f"[board_data] mes_equipment unavailable ({type(e).__name__}: "
                  f"{str(e)[:120]}) → equipment tools degrade honestly", flush=True)
            _MEQ = None
    return _MEQ


def get_mes_equipment(tool, source="demo", key=None):
    """`equipment_status_events`／`equipment_modules`／`equipment_calibration` 的三源分派。**恆不 raise**。
    `key` 必須已經過 `mes_equipment.normalize_args`（events／calibration＝dict `{eid, days}`；modules＝str）。"""
    if _synth_proc():                       # §5 wd:2474（N；M12）
        return _synth_tool_unavailable()
    mq = mes_equipment_module()
    if mq is None:
        return {"found": False, "unavailable_reason": "mes_equipment_module_missing",
                "source": source, "diag": {}}
    try:
        pre = mq.precheck(tool, key)
    except Exception as e:                            # noqa: BLE001
        return {"found": False, "unavailable_reason": f"error:{type(e).__name__}",
                "source": source, "diag": {"error_detail": mq.error_detail(e)}}
    if pre is not None:
        pre["source"] = source
        return pre
    if source == "live":
        try:
            conn, cur = mq.open_connection()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                out = mq.compute(tool, cur, key)
            finally:
                conn.close()
            out["source"] = "live"
            return out
        except Exception as e:                        # noqa: BLE001
            return _mm_unavailable(mq, tool, key, f"error:{type(e).__name__}", "live",
                                   error_detail=mq.error_detail(e))
    if source == "snapshot":
        return _mm_unavailable(mq, tool, key, "snapshot_unsupported", "snapshot")
    doc = _hermes_fixture(mq.FIXTURE_FILES[tool])
    if not doc:
        return _mm_unavailable(mq, tool, key, "fixture_unavailable", source)
    try:
        out = mq.demo_lookup(tool, doc, key)
    except Exception as e:                            # noqa: BLE001
        return _mm_unavailable(mq, tool, key, f"error:{type(e).__name__}", source,
                               error_detail=mq.error_detail(e))
    if out is None:
        return _mm_unavailable(mq, tool, key, "not_in_demo_fixture", source)
    out["source"] = source
    return out


# ---- MES 工具包②·包 4：工單路線／版本解析（核心＝`mes_route`；§7.1 三層 fallback）-------------
#   形逐條照 `get_mes_etest`（**恆不 raise**、只做取數與誠實降級、口徑住 `mes_route`）：
#     · 先跑 `mes_route.precheck`（目前恆 None；保留介面）。
#     · `live`     → 一條連線跑 `mes_route.compute(tool, cur, key)`（查無工單 ⇒ `reason=wo_not_found`）。
#     · `snapshot` → **不支撐**（同 `get_mes_material`：拿舊切面冒充＝謊報現場）。
#     · `demo`     → `_fixtures/mes_route_wo_route.json`（WO910002/1、WO910001/1），跑**同一支** `derive_*`；
#                    查不到 ⇒ `not_in_demo_fixture`（**不是** `wo_not_found`）。
#: ⚠ 名字刻意不是 `_MR`：那個全域是 `rework_module()`（mes_rework）的快取（同上撞名，整合包 2026-09-24）。
_MRT = None
_MRT_TRIED = False


def mes_route_module():
    """lazy import `mes_route`（未就緒 → None ＝ 誠實降級；同 `mes_etest_module`）。"""
    global _MRT, _MRT_TRIED
    if not _MRT_TRIED:
        _MRT_TRIED = True
        try:
            import mes_route as _m
            _MRT = _m
        except Exception as e:                        # noqa: BLE001
            print(f"[board_data] mes_route unavailable ({type(e).__name__}: "
                  f"{str(e)[:120]}) → route tool degrades honestly", flush=True)
            _MRT = None
    return _MRT


def get_mes_route(tool, source="demo", key=None):
    """`wo_route` 的三源分派。**恆不 raise**。`key` 必須已經過 `mes_route.normalize_wo`（str）。"""
    if _synth_proc():                       # §5 wd:2544（N；M12）
        return _synth_tool_unavailable()
    mr = mes_route_module()
    if mr is None:
        return {"found": False, "unavailable_reason": "mes_route_module_missing",
                "source": source, "diag": {}}
    try:
        pre = mr.precheck(tool, key)
    except Exception as e:                            # noqa: BLE001
        return {"found": False, "unavailable_reason": f"error:{type(e).__name__}",
                "source": source, "diag": {"error_detail": mr.error_detail(e)}}
    if pre is not None:
        pre["source"] = source
        return pre
    if source == "live":
        try:
            conn, cur = mr.open_connection()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                out = mr.compute(tool, cur, key)
            finally:
                conn.close()
            out["source"] = "live"
            return out
        except Exception as e:                        # noqa: BLE001
            return _mm_unavailable(mr, tool, key, f"error:{type(e).__name__}", "live",
                                   error_detail=mr.error_detail(e))
    if source == "snapshot":
        return _mm_unavailable(mr, tool, key, "snapshot_unsupported", "snapshot")
    doc = _hermes_fixture(mr.FIXTURE_FILES[tool])
    if not doc:
        return _mm_unavailable(mr, tool, key, "fixture_unavailable", source)
    try:
        out = mr.demo_lookup(tool, doc, key)
    except Exception as e:                            # noqa: BLE001
        return _mm_unavailable(mr, tool, key, f"error:{type(e).__name__}", source,
                               error_detail=mr.error_detail(e))
    if out is None:
        return _mm_unavailable(mr, tool, key, "not_in_demo_fixture", source)
    out["source"] = source
    return out


# ---- MES 工具包②·包 2：段量測／規格限／重測（核心＝`mes_segment`；ROADMAP #173）-------------
#   形逐條照 `get_mes_etest`（**恆不 raise**、只做取數與誠實降級、口徑住 `mes_segment`）：
#     · 先跑 `mes_segment.precheck`（目前恆 None；保留介面——參數錯誤由 `normalize_*` 擋在工具層）。
#     · `live`     → 一條連線跑 `mes_segment.compute(tool, cur, key)`。
#     · `snapshot` → **不支撐**（同 `get_mes_material`：拿舊切面冒充＝謊報現場）。
#     · `demo`     → `_fixtures/mes_segment_<tool>.json`（CLN 一段 FAIL 段／三站限值／三站重測），跑**同一支**
#                    `derive_*`；查不到 ⇒ `not_in_demo_fixture`（**不是** `serial_not_found_at_station`）。
_MSG = None
_MSG_TRIED = False


def mes_segment_module():
    """lazy import `mes_segment`（未就緒 → None ＝ 誠實降級；同 `mes_etest_module`）。"""
    global _MSG, _MSG_TRIED
    if not _MSG_TRIED:
        _MSG_TRIED = True
        try:
            import mes_segment as _m
            _MSG = _m
        except Exception as e:                        # noqa: BLE001
            print(f"[board_data] mes_segment unavailable ({type(e).__name__}: "
                  f"{str(e)[:120]}) → segment tools degrade honestly", flush=True)
            _MSG = None
    return _MSG


def get_mes_segment(tool, source="demo", key=None):
    """`segment_data`／`station_spec_limits`／`station_retest` 的三源分派。**恆不 raise**。
    `key` 必須已經過 `mes_segment.normalize_segment_args`（dict `{serial, cls, which}`）／
    `normalize_limits_args`（`{cls, recipe}`）／`normalize_retest_args`（`{cls, days}`）。"""
    if _synth_proc():                       # §5 wd:2615（N；M12）
        return _synth_tool_unavailable()
    ms = mes_segment_module()
    if ms is None:
        return {"found": False, "unavailable_reason": "mes_segment_module_missing",
                "source": source, "diag": {}}
    try:
        pre = ms.precheck(tool, key)
    except Exception as e:                            # noqa: BLE001
        return {"found": False, "unavailable_reason": f"error:{type(e).__name__}",
                "source": source, "diag": {"error_detail": ms.error_detail(e)}}
    if pre is not None:
        pre["source"] = source
        return pre
    if source == "live":
        try:
            conn, cur = ms.open_connection()
            conn.timeout = LIVE_QUERY_TIMEOUT_SEC
            try:
                out = ms.compute(tool, cur, key)
            finally:
                conn.close()
            out["source"] = "live"
            return out
        except Exception as e:                        # noqa: BLE001
            return _mm_unavailable(ms, tool, key, f"error:{type(e).__name__}", "live",
                                   error_detail=ms.error_detail(e))
    if source == "snapshot":
        return _mm_unavailable(ms, tool, key, "snapshot_unsupported", "snapshot")
    doc = _hermes_fixture(ms.FIXTURE_FILES[tool])
    if not doc:
        return _mm_unavailable(ms, tool, key, "fixture_unavailable", source)
    try:
        out = ms.demo_lookup(tool, doc, key)
    except Exception as e:                            # noqa: BLE001
        return _mm_unavailable(ms, tool, key, f"error:{type(e).__name__}", source,
                               error_detail=ms.error_detail(e))
    if out is None:
        return _mm_unavailable(ms, tool, key, "not_in_demo_fixture", source)
    out["source"] = source
    return out


# ======================================================================================
# ---- 人力欄（`board.operator.v1`；/dispatch 人力欄；計算核心＝`mes_operator`）------------
#   規格＝`SPEC-DISPATCH.md` §9.1（人名紅線）/§9.2（R4 判讀）/§9.3（D-OP1–D-OP4＋API pin）。
#   本節與 WIP 節同構：**只做取數與誠實降級**，一個口徑字都不在這裡重述。
#
#   ★ 兩層窗、兩層 TTL（SPEC §9.3；快取本體在 app）：
#       counts 層 [O1]＝分鐘級窄窗掃描（便宜、短 TTL）；
#       分類層  [O2]＝`class_d` 天掃描（貴、小時級 TTL）→ 由呼叫端快取後**以參數餵回來**。
#   ★ **分類層缺 ⇒ 整欄降級**（不是「先顯數字之後再說」）：沒有分類就分不出機器帳號站，
#     那些站會以「1 人」之姿混進畫面＝D-OP2 明令禁止的假在場。誠實「—」勝過半套數字。
#   ★ 三源：**live 才有**。demo 由 `board_dispatch.demo_operator` 合成（與 util/cover 情境
#     同一批骨架）；snapshot 不支撐（人力是 as-of now 的現場事實，舊切面重播＝謊報現場）。
#   ★ §24：本節搬運的**只有整數計數**——`COUNT(DISTINCT)` 全在 SQL 內完成（`mes_operator`），
#     識別值連 Python 都不進，快取裡自然也不可能有。
# ======================================================================================
OPR_SCHEMA = "board.operator.v1"
OPR_CALIBER = "mes-operator presence proxy (distinct OperatorId in window; counts only)"

_MO = None
_MO_TRIED = False


def operator_module():
    """lazy import `mes_operator`（未就緒 → None ＝ 誠實降級、絕不炸整站；同 wip_module）。"""
    global _MO, _MO_TRIED
    if not _MO_TRIED:
        _MO_TRIED = True
        try:
            import mes_operator as _m
            _MO = _m
        except Exception as e:
            print(f"[board_data] mes_operator unavailable ({type(e).__name__}: {str(e)[:120]}) "
                  f"→ dispatch headcount degrades honestly", flush=True)
            _MO = None
    return _MO


def operator_unavailable(reason, source=None):
    """人力誠實降級形：canonical 骨架 + `unavailable`；**絕不拿別的數字頂替、絕不合成**。
       消費面（`board_dispatch.operator_cell`）看到這個形 → 整欄「—」＋ footer `dp_mp_off`。"""
    return {"schema": OPR_SCHEMA, "caliber": OPR_CALIBER, "source": source, "ok": False,
            "unavailable": reason, "cap": None, "params": {}, "stations": {},
            "line": {}, "diag": {"degraded": True}, "elapsed_s": None}


def operator_ok(opr):
    """可用＝非 None、無 `unavailable`、`stations` 是 dict、且分類層沒降級（同消費面判準）。"""
    return (bool(opr) and not opr.get("unavailable") and bool(opr.get("ok"))
            and isinstance(opr.get("stations"), dict)
            and not (opr.get("diag") or {}).get("degraded"))


def _opr_kw(params, keys):
    p = dict(params or {})
    return {k: p[k] for k in keys if p.get(k) is not None}


def get_operator_class(source="live", params=None):
    """分類層 [O2]（`class_d` 天慢查詢）→ `{"ok":True,"rows":[(cls,p,b,n)…],"cap":iso,"class_d":d}`
       ／失敗 `{"ok":False,"unavailable":reason,"rows":[],"cap":None}`。**恆不 raise。**

       ★ 這一層由呼叫端以**小時級 TTL** 快取後餵給 `get_operator`（SPEC §9.3：絕不跟 counts
         層共用一個 TTL——`class_d` 天掃描比分鐘級窄窗貴一個量級）。
       ★ rows ＝ `(cls, d7_persons, d7_blocks, d7_rows)` 純整數元組＝可安全長期快取（零識別值）。"""
    def bad(reason):
        return {"ok": False, "unavailable": reason, "rows": [], "cap": None, "class_d": None}
    if _synth_proc():                       # §5 wd:2710（N；M7 第二期）
        return bad(_wsio().NOT_PROVIDED)
    if source != "live":
        return bad("live_only")
    mo = operator_module()
    if mo is None:
        return bad("mes_operator_module_missing")
    class_d = _opr_kw(params, ("class_d",)).get("class_d") or mo.CLASS_D
    try:
        conn, cur = mo._connect()
        conn.timeout = LIVE_QUERY_TIMEOUT_SEC
        try:
            mo.fetch_operator_guard(cur)                 # [G0] 承重欄缺 → raise → 誠實降級
            cur.execute("SELECT GETDATE()")
            cap = cur.fetchone()[0]
            rows = mo.fetch_auto_stats(cur, cap, class_d=class_d)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        # 錯誤原文落 log（reason 仍只帶類名＝機器可讀；2026-08-01 scope 事故時只有類名＝得用猜的）
        print(f"[board_data operator/class ERR] {type(e).__name__}: {str(e)[:300]}", flush=True)
        return bad(f"error:{type(e).__name__}")
    return {"ok": True, "unavailable": None, "rows": rows,
            "cap": mo._dt(cap).isoformat(), "class_d": int(class_d)}


def get_operator(source="live", params=None, class_layer=None):
    """`board.operator.v1` canonical（§9.3 pin）。**恆不 raise**：任何失敗 → `operator_unavailable`。

       · live       ：counts 層 [O1] 現查（一條連線）＋ `class_layer`（呼叫端長 TTL 快取）
                      → `mes_operator.build_operator` 純函式組裝。
       · demo       ：**不在這裡合成**——demo 的人力與 cover/util 情境同一批骨架，住
                      `board_dispatch.demo_operator`（`demo_dispatch` 內部呼叫）。
       · snapshot   ：不支撐（人力是 as-of now 的現場事實，舊切面重播＝謊報現場，同 `get_wip`）。
       · `class_layer` 缺/失敗 → **整欄降級**（見本節抬頭：沒有分類就會把機器帳號當人）。"""
    if _synth_proc():                       # §5 wd:2748（N；M7 第二期）
        return operator_unavailable(_wsio().NOT_PROVIDED, _synth_src())
    if source == "snapshot":
        return operator_unavailable("snapshot_unsupported", "snapshot")
    if source != "live":
        return operator_unavailable("demo_synthesised_in_board_dispatch", source)
    mo = operator_module()
    if mo is None:
        return operator_unavailable("mes_operator_module_missing", "live")
    if not (class_layer or {}).get("ok"):
        return operator_unavailable(
            f"class_layer:{(class_layer or {}).get('unavailable') or 'missing'}", "live")
    kw = _opr_kw(params, ("now_min", "ref_h", "max_persons", "min_blocks", "route"))
    class_d = (class_layer or {}).get("class_d") or mo.CLASS_D
    el = None
    try:
        conn, cur = mo._connect()
        conn.timeout = LIVE_QUERY_TIMEOUT_SEC
        try:
            guard = mo.fetch_operator_guard(cur)
            cur.execute("SELECT GETDATE()")
            cap = cur.fetchone()[0]
            stats = {}
            t0 = time.perf_counter()
            count_rows = mo.fetch_operator_counts(
                cur, cap, now_min=kw.get("now_min", mo.NOW_MIN),
                ref_h=kw.get("ref_h", mo.REF_H), stats=stats)
            el = time.perf_counter() - t0
        finally:
            try:
                conn.close()
            except Exception:
                pass
        out = mo.build_operator(count_rows, (class_layer or {}).get("rows") or [], cap,
                                class_d=class_d, stats=stats, guard=guard,
                                timing={"o1_ms": int(el * 1000)}, **kw)
    except Exception as e:
        # 錯誤原文落 log（同 get_operator_class；reason 仍只帶類名）
        print(f"[board_data operator ERR] {type(e).__name__}: {str(e)[:300]}", flush=True)
        return operator_unavailable(f"error:{type(e).__name__}", "live")
    out = dict(out or {})
    out["source"] = "live"
    out["caliber"] = OPR_CALIBER
    out["elapsed_s"] = round(el, 3) if el is not None else None
    # 分類層是**另一個時刻**取的（長 TTL）——自揭，別讓人以為兩層同一個錨。
    out["diag"] = dict(out.get("diag") or {}, class_cap=(class_layer or {}).get("cap"))
    return out


# ======================================================================================
# /daily 昨日缺口「時間帳本歸因」canonical（`board.attrib.v1`；規格＝`SPEC-DAILY-ATTRIB.md`）
#   本節與上面兩節（Qlik / WIP）同構：**只做三源 canonical**，
#   **一個口徑字都不在這裡重述**——歸因語意全在 `board_attrib`（純函式引擎、B1）、
#   佇列語意全在 `mes_wip`、工單語意全在 `board_eqmeeting`（S12）。本檔只負責
#   「把料端到引擎面前」＋「三源分派」＋「日帳落地 I/O」（SPEC §8.1 的 B2 檔集）。
#
#   ★ 三源（SPEC §5）
#     live     ：20 站昨日 timeline（呼叫端給、走 app 的日期鍵 immutable 快取）
#                ＋ cmms 工單段（wem.wo_segments、S12）＋ **一次** WIP 拉料 → 48 點純函式重放。
#     demo     ：`board_attrib.demo_board()`＝全合成、走**同一個真引擎**（口徑不可能分岔）。
#     snapshot ：**誠實降級**——聚合切面沒有 per-machine 昨日區間、也沒有當時的 WIP 位置，
#                硬算等於編故事（同 `get_wip` 對 snapshot 的裁決）。
# ======================================================================================
ATTRIB_SCHEMA = "board.attrib.v1"
ATTRIB_STATE_DIR = _wp.STATE_DIRNAME          # "_state"（名稱常數；路徑一律經 core_paths 組）
ATTRIB_LEDGER_SUBDIR = _wp.LEDGER_SUBDIR      # "daily_ledger"

_WA = None
_WA_TRIED = False


def attrib_module():
    """lazy import `board_attrib`（B1；未就緒 → None ＝ 誠實降級、絕不炸整站；同 wip_module）。"""
    global _WA, _WA_TRIED
    if not _WA_TRIED:
        _WA_TRIED = True
        try:
            import board_attrib as _m
            _WA = _m
        except Exception as e:
            print(f"[board_data] board_attrib unavailable "
                  f"({type(e).__name__}: {str(e)[:120]}) → /daily 歸因誠實降級", flush=True)
            _WA = None
    return _WA


def attrib_unavailable(reason, source=None, day=None):
    """歸因誠實降級形：canonical 骨架 + `unavailable` 原因；**絕不拿別的數字頂替、絕不合成**。
       消費面（/daily）一律以 `attrib_ok()` 判定；不可用時整段顯降級說明，不出任何桶。"""
    return {"schema": ATTRIB_SCHEMA, "source": source, "day": day,
            "unavailable": reason, "rows": [], "hero": None, "pareto30": None,
            "wo_source_status": None, "computed_at": None, "diag": {"degraded": True}}


def attrib_ok(board):
    """可用＝非 None、無 `unavailable`、且有站列（空盤＝不可用，寧顯降級不顯 0）。"""
    return bool(board) and not board.get("unavailable") and bool(board.get("rows"))


def attrib_rows_by_cls(board):
    """`{cls: ledger}`（消費面 join 用；不可用→{}）。"""
    if not attrib_ok(board):
        return {}
    return {r.get("cls"): r for r in (board.get("rows") or []) if r.get("cls")}


# ---- ① 一次拉料（SPEC §2 編排 pin：guard → [W1] → `#temp` → [H1]，同一 cursor）----------
def fetch_wip_rows_live(cap, *, lookback_d=None, h_lookback_d=None, timeout=None):
    """as-of `cap` 的 WIP 原始列（7 欄 tuple）→ `(rows, meta)`。**一次 SQL 拉料、零 per-point 查詢**。

       ★ 編排逐段照抄 `mes_wip.compute_wip`（同一條連線／cursor＝`#temp` 綁連線生命週期、
         COLLATE 換側回退、`_clamp_lookback`/`_clamp_h_lookback` **在 fetch 之前**）——
         唯一差別＝**不呼叫 `derive_wip`**（那要 48 次、且每次 cap 不同），改由呼叫端用
         `board_attrib.replay_series` 在 Python 內重放。`cap` 由呼叫端明給
         （/daily＝昨日 24:00），**絕不在此重取 now**（母體窗與速率窗會對不齊）。
       ★ `meta` 帶回重放時必須逐位元沿用的參數（has_mcl/has_status/兩個窗/速率窗）——
         重放若用預設值而 live 拉料用了 clamp 後的值，兩者的母體語意會靜默分岔。
       失敗一律往上拋（呼叫端接成 `attrib_unavailable`）。"""
    if _synth_proc():                       # M6 WIP 拉料（第二期）：依本函式契約往上拋
        raise _wsio().not_provided("wip_rows")
    mw = wip_module()
    if mw is None:
        raise RuntimeError("mes_wip_module_missing")
    cap = mw._dt(cap)
    # 連線走 **`mes_wip._connect`（＝`mes_qlik._connect` 的 re-export）**＝與 `compute_wip`
    # 同一個連線語意來源（autocommit／`SET ARITHABORT ON`／READ UNCOMMITTED）。
    # 舊版走 `mt._connect`（凍結模組的 helper）——今日兩者等價，但同一條 WIP 拉料管線靠兩個
    # 來源維持等價＝日後其中一邊調 timeout/SET 選項就會靜默分岔。
    conn, cur = mw._connect(timeout=timeout or LIVE_QUERY_TIMEOUT_SEC)
    t0 = time.perf_counter()
    st = {}
    try:
        guard = mw.fetch_wip_guard(cur)                     # [G0] 承重欄缺→SchemaDivergence
        has_mcl, has_status = guard["mclMaterialClassID"], guard["matStatus"]
        lb = mw.LOOKBACK_D if lookback_d is None else int(lookback_d)
        hb = mw.H_LOOKBACK_D if h_lookback_d is None else int(h_lookback_d)
        lookback_eff, _n1 = mw._clamp_lookback(lb, mw.AGE_CAP_D)
        h_lookback_eff, _n2 = mw._clamp_h_lookback(hb, lookback_eff)
        lots = mw.fetch_recent_lots(cur, cap, lookback_eff, has_mcl=has_mcl, stats=st)
        rows = []
        if lots:
            mw._mk_lots_temp(cur, lots)
            try:
                rows = mw.fetch_lot_rows(cur, cap, h_lookback_eff, has_mcl=has_mcl,
                                         has_status=has_status, collate_side="temp", stats=st)
            except Exception as e:                          # COLLATE 換側回退（軌一 QA-S 事故）
                if "collation conflict" not in str(e).lower():
                    raise
                rows = mw.fetch_lot_rows(cur, cap, h_lookback_eff, has_mcl=has_mcl,
                                         has_status=has_status, collate_side="view", stats=st)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    meta = {"cap": cap, "has_mcl": has_mcl, "has_status": has_status,
            "age_cap_d": mw.AGE_CAP_D, "rate_win_h": mw.RATE_WIN_H,
            "rate_fallback_d": mw.RATE_FALLBACK_D,
            "lookback_d": lookback_eff, "h_lookback_d": h_lookback_eff,
            "n_rows": len(rows), "n_win_lots": st.get("n_win_lots"),
            "collate_side": st.get("collate_side"),
            "elapsed_s": round(time.perf_counter() - t0, 3)}
    return rows, meta


def attrib_wip_series(rows, meta, win_start, win_end, cls_list, grid_min=None):
    """WIP 原始列 → `{cls: [(t_i, cover_h, queue_n, stale_n)…]}`（**零額外 SQL**）。

       重放與 as-of 裁切全在 `board_attrib.replay_series`（SPEC §2 的 as-of 陷阱在那裡收口：
       `latest_positions` 不吃 cap ⇒ 每點先 `ts < t_i` 過濾再重放）。本函式只做兩件事：
       把網格點算出來、把 `meta` 的參數原樣轉給 `derive_wip`（不自作主張填預設值）。
       `cls_list` 必須是 **20 站 ∪ 它們的下游站**（下游堵桶要讀下游序列）。"""
    wa, mw = attrib_module(), wip_module()
    if wa is None or mw is None:
        return {}
    pts = wa.grid_points(win_start, win_end, grid_min or wa.GRID_MIN)
    return wa.replay_series(rows, pts, cls_list=cls_list,
                            age_cap_d=meta.get("age_cap_d", mw.AGE_CAP_D),
                            rate_win_h=meta.get("rate_win_h", mw.RATE_WIN_H),
                            rate_fallback_d=meta.get("rate_fallback_d", mw.RATE_FALLBACK_D),
                            has_status=meta.get("has_status", True),
                            has_mcl=meta.get("has_mcl", True),
                            lookback_d=meta.get("lookback_d", mw.LOOKBACK_D),
                            h_lookback_d=meta.get("h_lookback_d", mw.H_LOOKBACK_D))


def attrib_rate_ctx(yb_row):
    """C5 est 換算脈絡＝**per-machine-hour**（站級 rate × 機台小時會放大 fleet_n 倍＝§9 反模式）。

       這裡只給分子（`lots_day`＝該站**昨日 distinct lot**＝設備軸 raw 產出，與帳本同軸同窗），
       **分母由 `build_ledger` 用它自己的 `producing_h`**（＝昨日該站生產機台小時）補上
       ⇒ 分子分母同站同窗、單位＝顆/機台小時（＝`oee.actual_rate_uph` 語意）。
       ★ 刻意用 raw `output_units`（T5：帳本是設備軸 raw）——**不是** /daily 產出列的 Qlik 口徑數字
         （那是良率/產出軸的公司口徑、first-FAIL 語意，拿來當設備軸速率分子＝混軸）。
       量不到（無列/無產出欄）→ `None` ⇒ est 全 None、帳本小時照給（T4）。"""
    if not yb_row:
        return None
    lots = yb_row.get("output_units")
    if lots is None:
        return None
    # min_prod_mh＝換算率分母下限（2026-08-02 事故：瞬時段站 0.0017h → est 顯 ≈340 萬顆）
    return {"lots_day": float(lots), "rate_src": "y24_raw",
            "min_prod_mh": float(os.environ.get("BOARD_ATTRIB_MIN_PROD_H", "0.5"))}


# ---- ①b 生產日曆（G2 全廠停線；`SPEC-PROD-CALENDAR.md` §9 消費者鐵則）-----------------
#   ★ 本節**只做讀檔＋窗裁切＋C-PC12 兜底**，一個口徑字都不在這裡重述：
#     日曆的產出語意（quorum／240 分閾值／日界 08:00／coverage 收窄）全在 `core_prod_calendar`，
#     validator 也**委派它的 `ok_calendar`**——絕不另寫第二份（兩份驗證＝兩份會漂移的事實）。
#   ★ 檔不存在＝**常態不是錯誤**（`/calendar` 產出目前由 Chris 在域內機器手動跑）⇒
#     缺檔/壞檔/schema 不符一律回 None，呼叫端把 closure 桶留 0＝舊行為。
# （import 時定值＝原時機；BOARD_STATE_DIR 須在 import 前設，見 core_paths.IMPORT_TIME_SITES）
PROD_CALENDAR_PATH = _wp.prod_calendar_path(repo=os.path.dirname(os.path.abspath(__file__)))
# wire 是 UTC Z、MES/帳本是**台北牆鐘 naive**。台灣沒有日光節約（1980 年後永久停用）⇒
# 位移恆為 +8h，不引入 tz 資料庫（與 `core_lastprod_push.taipei_to_utc_dt` 反向、同一個常數）。
TAIPEI_UTC_OFFSET_H = 8
_CAL_Z_FMT = "%Y-%m-%dT%H:%M:%SZ"


def prod_calendar_stamp():
    """日曆檔的版本印記 `(mtime, size)`；**檔不在 → None**（絕不 raise）。

       用途＝呼叫端（app `load_attrib`）的快取鍵：日曆檔內容會改變帳本 ⇒ 日期鍵 immutable
       快取必須看得到它換版，否則新日曆要等到隔天才生效。
    """
    try:
        st = os.stat(PROD_CALENDAR_PATH)
        return (int(st.st_mtime), int(st.st_size))
    except Exception:
        return None


# ---- 綑級名義配置（SPEC-DISPATCH-V2 §14；檔案源、只讀）------------------------------
# Chris 人工維護的資料不屬 MES，也不可由實測 operator 數反推。快取鍵必含 (mtime,size)，
# 否則同一個 app process 會靜默看不到人工換檔。
KUN_NOMINAL_STAFF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "_KUN-NOMINAL-STAFF.json")
_KUN_NOMINAL_CACHE = {}

# ---- 出貨目標（SPEC-PACKOUT-TARGET C-PT1–6；SALTMARSH 晨會表 Target Pack Out 的人工抄本、只讀）----
# C-PT2：只吃 repo 根這份抄好的小檔、永不讀 Downloads 的活檔；C-PT6：零 MES、零新 SQL。
# 印記快取照 `kun_nominal_stamp` 的形（`(mtime,size)` 進快取鍵 ⇒ 人工換檔不必重啟）。
PACKOUT_TARGET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "_SALTMARSH-PACKOUT-TARGET.json")
PACKOUT_TARGET_SCHEMA = "board.packout_target.v1"
PACKOUT_TARGET_MAX_D = 45            # C-PT5：as_of 超過 45 天 ⇒ stale（數字照印、chip 加註）
_PACKOUT_TARGET_CACHE = {}

# ---- 平面座標（SPEC-DISPATCH-V2 C-KD6/C-KD11；檔案源、只讀）-----------------------
# 供綑級調度重用的 layout loader。這裡只負責 `(mtime,size)` 快取與最小形狀防護；距離口徑
# 仍唯一住在 `board_kundispatch.layout_dist_matrix()`，不可在資料層重算第二份。
LAYOUT_DOC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "layout_eid_xy.json")
_LAYOUT_DOC_CACHE = {}


def layout_doc_stamp(path=None):
    """layout 檔版本印記 `(mtime,size)`；缺檔或不可讀 → None（不炸呈現面）。

       ★ SL2-A（SPEC-SYNTHLINE-PHASE2 D-LY3）：合成進程**不 stat 真座標檔**（C-SY1／C-SY15），回合成版面的
         `("synth-layout", LAYOUT_VERSION, fleet_digest)`（與 `load_layout_doc` 同一個記憶體文件）。"""
    if _synth_proc():
        try:
            import wsynth_layout as _wsl
            return _wsl.doc_stamp()
        except Exception:
            return None
    try:
        st = os.stat(path or LAYOUT_DOC_PATH)
        return (int(st.st_mtime), int(st.st_size))
    except Exception:
        return None


def load_layout_doc(path=None):
    """讀 `board.layout.v1` 文件 → `(doc|None, status)`，只讀本機檔案、零 MES。

       ★ SL2-A（D-LY3）：合成進程一律回合成版面（`wsynth_layout.cached_doc()`，首頁同一個物件）的**副本**，
         不論 `path`——合成進程裡沒有任何路徑能讀到真座標檔（fail-closed；C-SY1／C-SY15）。"""
    if _synth_proc():
        try:
            import wsynth_layout as _wsl
            doc = _wsl.cached_doc()
        except Exception:
            return None, "invalid"
        return json.loads(json.dumps(doc)), "ok"
    actual =os.path.abspath(path or LAYOUT_DOC_PATH)
    stamp = layout_doc_stamp(actual)
    if stamp is None:
        return None, "absent"
    cache_key = (actual, stamp)
    cached = _LAYOUT_DOC_CACHE.get(cache_key)
    if cached is None:
        try:
            with open(actual, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict) or doc.get("schema_id") != "board.layout.v1":
                raise ValueError("schema")
            cached = (doc, "ok")
        except Exception:
            cached = (None, "invalid")
        _LAYOUT_DOC_CACHE.clear()
        _LAYOUT_DOC_CACHE[cache_key] = cached
    doc, status = cached
    # consumer 不得污染快取物件（layout 父子關係後續還會被純函式展開）。
    return (json.loads(json.dumps(doc)) if doc is not None else None), status


def kun_nominal_stamp(path=None):
    """§14 名義配置檔的 ``(mtime,size)`` 印記；不存在或不可讀即 None。"""
    try:
        st = os.stat(path or KUN_NOMINAL_STAFF_PATH)
        return (int(st.st_mtime), int(st.st_size))
    except Exception:
        return None


def _normalise_kun_nominal_doc(raw):
    """驗 §14 並回讀取用副本；small_night 僅在讀取端 alias，絕不回寫原檔。"""
    from datetime import date as _date
    from decimal import Decimal, ROUND_HALF_UP

    if not isinstance(raw, dict):
        raise ValueError("root_not_object")
    if raw.get("schema") != "board.kun_nominal_staff.v1":
        raise ValueError("schema")
    try:
        _date.fromisoformat(str(raw.get("as_of")))
    except Exception as exc:
        raise ValueError("as_of") from exc
    by_kun = raw.get("by_kun")
    if not isinstance(by_kun, dict):
        raise ValueError("by_kun")
    out, diag = {}, []
    for kun, shifts in by_kun.items():
        if not isinstance(kun, str) or not isinstance(shifts, dict):
            raise ValueError("by_kun_entry")
        dst = {}
        for shift, value in shifts.items():
            # C-MP6：歷史 small_night 只在 reader 對映，落地檔與新碼不再寫它。
            canonical = "night" if shift == "small_night" else shift
            if canonical not in ("day", "swing", "night"):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                raise ValueError("staff_value")
            rounded = int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if rounded < 0:
                raise ValueError("staff_negative")
            if value != rounded:
                diag.append(f"rounded:{kun}:{canonical}")
            dst[canonical] = rounded
        out[kun] = dst
    doc = {key: raw.get(key) for key in ("schema", "source", "as_of", "note") if key in raw}
    doc["by_kun"] = out
    if diag:
        doc["_diag"] = diag
    return doc


def load_kun_nominal_staff(path=None, *, today=None):
    """讀 §14 `_KUN-NOMINAL-STAFF.json` → ``(doc|None, status)``。

    status 恆為 ``ok``／``stale``／``absent``／``error:<reason>``。過期資料仍回傳，讓
    consumer 能揭露 as_of，但 C-KD8 會把它的 staff_n 降成 None。
    """
    from datetime import date as _date

    actual = os.path.abspath(path or KUN_NOMINAL_STAFF_PATH)
    stamp = kun_nominal_stamp(actual)
    if stamp is None:
        return None, "absent"
    cache_key = (actual, stamp)
    cached = _KUN_NOMINAL_CACHE.get(cache_key)
    if cached is None:
        try:
            with open(actual, "r", encoding="utf-8") as fh:
                normal = _normalise_kun_nominal_doc(json.load(fh))
            cached = (normal, None)
        except Exception as exc:  # 不把壞檔炸到頁面；錯因只回穩定型別/短鍵。
            cached = (None, "error:" + (str(exc) or type(exc).__name__).replace(" ", "_")[:80])
        _KUN_NOMINAL_CACHE.clear()
        _KUN_NOMINAL_CACHE[cache_key] = cached
    doc, err = cached
    if err:
        return None, err
    try:
        as_of = _date.fromisoformat(str(doc.get("as_of")))
        reference = today or datetime.now().date()
        max_days = int(os.environ.get("BOARD_KD_NOMINAL_MAX_D", "60"))
        status = "stale" if (reference - as_of).days > max_days else "ok"
    except Exception as exc:
        return None, "error:" + (str(exc) or type(exc).__name__).replace(" ", "_")[:80]
    # 呼叫端不可污染印記快取。
    return json.loads(json.dumps(doc)), status


def packout_target_stamp(path=None):
    """`_SALTMARSH-PACKOUT-TARGET.json` 的 ``(mtime,size)`` 印記；不存在或不可讀即 None（§2）。"""
    try:
        st = os.stat(path or PACKOUT_TARGET_PATH)
        return (int(st.st_mtime), int(st.st_size))
    except Exception:
        return None


def _normalise_packout_target_doc(raw):
    """驗 §2 格式 pin 並回讀取用副本（壞形一律 raise ValueError(短鍵)）。

       ★ 日期鍵＝曆日、只能落在自己那個月裡；**表上空白的日子沒有鍵**（C-PT3：週日空白＝
         無目標、不是 0）⇒ 這裡不補任何鍵。值必須是非負整數（PH）；bool 不算數。"""
    from datetime import date as _date

    if not isinstance(raw, dict):
        raise ValueError("root_not_object")
    if raw.get("schema") != PACKOUT_TARGET_SCHEMA:
        raise ValueError("schema")
    try:
        _date.fromisoformat(str(raw.get("as_of")))
    except Exception as exc:
        raise ValueError("as_of") from exc
    months = raw.get("months")
    if not isinstance(months, dict):
        raise ValueError("months")
    out = {}
    for month, days in months.items():
        if not (isinstance(month, str) and len(month) == 7 and isinstance(days, dict)):
            raise ValueError("month_entry")
        try:
            _date.fromisoformat(month + "-01")
        except Exception as exc:
            raise ValueError("month_key") from exc
        dst = {}
        for day, value in days.items():
            try:
                ok_day = (isinstance(day, str) and day[:7] == month
                          and _date.fromisoformat(day).isoformat() == day)
            except Exception:
                ok_day = False
            if not ok_day:
                raise ValueError("day_key")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("target_value")
            dst[day] = int(value)
        out[month] = dst
    doc = {key: raw.get(key) for key in ("schema", "source", "as_of", "unit", "note") if key in raw}
    doc["months"] = out
    return doc


def load_packout_target(path=None, *, today=None):
    """讀 `_SALTMARSH-PACKOUT-TARGET.json` → ``(doc|None, status)``（SPEC-PACKOUT-TARGET §2）。

       status 恆為 ``ok``／``stale``／``absent``／``error:<reason>``。``stale``（C-PT5：
       `as_of` 距今超過 `PACKOUT_TARGET_MAX_D` 天）**仍回資料**——數字照印，只是出處 chip
       要加註檔齡；``doc["age_d"]``＝距今天數（呼叫端印「N 天未更新」用，不必自己再算）。"""
    from datetime import date as _date

    actual = os.path.abspath(path or PACKOUT_TARGET_PATH)
    stamp = packout_target_stamp(actual)
    if stamp is None:
        return None, "absent"
    cache_key = (actual, stamp)
    cached = _PACKOUT_TARGET_CACHE.get(cache_key)
    if cached is None:
        try:
            with open(actual, "r", encoding="utf-8") as fh:
                cached = (_normalise_packout_target_doc(json.load(fh)), None)
        except Exception as exc:  # 壞檔不炸頁面；錯因只回短鍵（同 kun nominal）。
            cached = (None, "error:" + (str(exc) or type(exc).__name__).replace(" ", "_")[:80])
        _PACKOUT_TARGET_CACHE.clear()
        _PACKOUT_TARGET_CACHE[cache_key] = cached
    doc, err = cached
    if err:
        return None, err
    doc = json.loads(json.dumps(doc))                  # 呼叫端不可污染印記快取
    reference = today or datetime.now().date()
    doc["age_d"] = (reference - _date.fromisoformat(str(doc.get("as_of")))).days
    return doc, ("stale" if doc["age_d"] > PACKOUT_TARGET_MAX_D else "ok")


def packout_target_view(doc, day):
    """純函式：目標檔 × 某個曆日（`YYYY-MM-DD`）→ ``{month_has, day_t, mtd_t}``。

       · ``month_has``＝那個月有沒有抄本（False ⇒ 呼叫端**整個目標格不渲染**，C-PT3）。
       · ``day_t``    ＝那一天的日目標；表上空白（週日／未排）⇒ None（印「—」＋理由，**絕不 0**）。
       · ``mtd_t``    ＝該月 1 日到 `day`（含）的日目標**加總**（§3：表上 `MTD Target` 也是這樣
         算，不抄它）；一天目標都還沒有 ⇒ None。"""
    blank = {"month_has": False, "day_t": None, "mtd_t": None}
    day = str(day or "")[:10]
    if not isinstance(doc, dict) or len(day) != 10:
        return blank
    days = (doc.get("months") or {}).get(day[:7])
    if not isinstance(days, dict):
        return blank
    upto = [int(v) for k, v in days.items() if str(k) <= day]
    return {"month_has": True, "day_t": days.get(day),
            "mtd_t": (sum(upto) if upto else None)}


def _cal_module():
    """lazy import `core_prod_calendar`（未就緒 → None＝誠實降級；同 attrib_module）。"""
    try:
        import core_prod_calendar as _m
        return _m
    except Exception:
        return None


def load_prod_calendar(path=None):
    """`_PROD-CALENDAR-LATEST.json` → 驗過的 doc，或 **None**（缺檔/壞檔/驗不過）。

       驗證**一律委派 `core_prod_calendar.ok_calendar`**（provider 自己的閘＝唯一一份）。
       回 `(doc|None, status)`；status ∈ {"ok","absent","invalid","no_module"}——
       `absent` 是常態、不該產生任何警語；`invalid` 才值得在 diag/caveat 記一句。
    """
    p = path or PROD_CALENDAR_PATH
    m = _cal_module()
    if m is None:
        return (None, "no_module")
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return (None, "absent")
    except Exception:
        return (None, "invalid")
    try:
        if not m.ok_calendar(doc):
            return (None, "invalid")
    except Exception:
        return (None, "invalid")
    return (doc, "ok")


def _cal_z_to_taipei(ts):
    """wire UTC Z → 台北牆鐘 naive（固定 +8h；見 `TAIPEI_UTC_OFFSET_H`）。壞值 → None。"""
    try:
        return (datetime.strptime(str(ts), _CAL_Z_FMT)
                + timedelta(hours=TAIPEI_UTC_OFFSET_H))
    except Exception:
        return None


def calendar_closures(doc, win_start, win_end):
    """日曆 doc → `[{"start","end"}…]`（台北牆鐘、clip 到 `[win_start, win_end)`）。

       C-PC8：**窗與 coverage 的交集之外一律不主張**——日曆還沒算到的期間不是「有生產」
       也不是「沒生產」，所以窗若整段落在 coverage 外，這裡回空清單（closure 桶＝0）。
    """
    if not doc:
        return []
    cov = doc.get("coverage") or {}
    cf, ct = _cal_z_to_taipei(cov.get("from")), _cal_z_to_taipei(cov.get("to"))
    lo = win_start if (cf is None or win_start > cf) else cf
    hi = win_end if (ct is None or win_end < ct) else ct
    out = []
    for c in doc.get("closures") or ():
        a, b = _cal_z_to_taipei((c or {}).get("start")), _cal_z_to_taipei((c or {}).get("end"))
        if a is None or b is None:
            continue
        a = a if a > lo else lo
        b = b if b < hi else hi
        if b > a:
            out.append({"start": a, "end": b})
    out.sort(key=lambda c: (c["start"], c["end"]))
    return out


def calendar_coverage(doc):
    """日曆 coverage → `(from, to)` 台北牆鐘 naive；任一端壞值/缺席 → `(None, None)`。

       C-PC8 的承重欄：消費端要能區分「這段日曆算過了」與「日曆還沒算到」。
       `calendar_covers` 答的是布林（本窗有沒有被完整涵蓋），這支給**邊界本身**——
       `/equipment` 要逐日欄判斷「這一欄完整落在 coverage 內嗎」，布林不夠用。"""
    cov = (doc or {}).get("coverage") or {}
    cf, ct = _cal_z_to_taipei(cov.get("from")), _cal_z_to_taipei(cov.get("to"))
    if cf is None or ct is None:
        return (None, None)
    return (cf, ct)


def calendar_day_production(doc, day_iso):
    """日曆 doc ＋ 生產日 `YYYY-MM-DD` → `True`／`False`／**`None`＝日曆沒算到那一天**。

       ★ **三態是承重的**（C-PC8 的同一條線）：「日曆還沒算到」既不是「有生產」也不是
       「沒生產」⇒ 消費端必須能**不主張**。把缺席折成 `False` 會讓 `/daily` 在日曆過期時
       對著一整排正常的生產日喊「昨天是休息日」——比不顯示糟得多。
       `days[]` 的結構（逐日唯一、升冪、`production` 是 bool、完整落在 coverage 內）已由
       `core_prod_calendar.ok_calendar` 在 `load_prod_calendar` 裡驗過 ⇒ 這裡**只查表、不重驗**
       （消費端不得另寫第二份 validator——smoke 有負斷言釘住這條紀律）。"""
    if not doc or not day_iso:
        return None
    for it in doc.get("days") or ():
        if isinstance(it, dict) and it.get("date") == str(day_iso):
            p = it.get("production")
            return p if isinstance(p, bool) else None
    return None


def calendar_covers(doc, win_start, win_end):
    """日曆的 coverage 是否**完整涵蓋**本窗（C-PC8 承重欄；不涵蓋 ≠ 沒生產）。"""
    cov = (doc or {}).get("coverage") or {}
    cf, ct = _cal_z_to_taipei(cov.get("from")), _cal_z_to_taipei(cov.get("to"))
    if cf is None or ct is None:
        return False
    return cf <= win_start and ct >= win_end


def calendar_open_tail(doc, win_start, win_end):
    """#182-P2 B：日曆尾端**未閉合沉默**與本窗重疊 ⇒ `{"from", "overlap_sec"}`，否則 None。

       消費端讀 `board.prod_calendar.v1` 的加法選配鍵 `open_silence_from`（SPEC-PROD-CALENDAR
       §11）。沉默 `[from, coverage.to)` 還在進行中——它可能事後封口成 closure（本日分母該扣）、
       也可能只是個短空窗；**建帳當下不可分**（09-13 那天建帳時才 2h11m）⇒ 只落旗、不猜。
       `from` 以台北牆鐘 ISO 落盤（與日帳其他時刻同軸）。沒有鍵／壞值／不重疊 ⇒ None。"""
    osf = _cal_z_to_taipei((doc or {}).get("open_silence_from"))
    if osf is None:
        return None
    _cf, ct = calendar_coverage(doc)
    if ct is None:
        return None
    a = osf if osf > win_start else win_start
    b = ct if ct < win_end else win_end
    if b <= a:
        return None
    return {"from": osf.isoformat(), "overlap_sec": int((b - a).total_seconds())}


CAL_TAIL_OPEN = "open"               # 同一段沉默還在進行（沒封口）
CAL_TAIL_CLOSURE = "sealed_closure"  # 封口了、而且成了 closure ⇒ 本日分母該扣 ⇒ 要重建
CAL_TAIL_NONE = "sealed_none"        # 封口了、沒成 closure（<240 分）⇒ 原帳就是對的 ⇒ 就地標 resolved
CAL_TAIL_UNKNOWN = "unknown"         # 日曆缺席／已滾出涵蓋 ⇒ 判不了


def cal_open_tail_state(tail, doc):
    """日帳的 `cal_open_tail` 旗 ＋ **現在的**日曆 → 四態之一（純函式、零 I/O）。

       · 日曆缺席、或 `coverage` 已不含旗上的起點 ⇒ `unknown`（滾出 30 天窗；判不了就不主張）。
       · 現在的 `open_silence_from` ≤ 旗上起點 ⇒ 同一段沉默還開著 ⇒ `open`。
       · 否則已封口：有 closure 蓋住旗上起點 ⇒ `sealed_closure`；沒有 ⇒ `sealed_none`。
         （closure 的左邊界＝前段末訊號＝那時的 `open_silence_from`，兩者同一個時刻。）"""
    if not isinstance(tail, dict) or not doc:
        return CAL_TAIL_UNKNOWN
    try:
        frm = datetime.fromisoformat(str(tail.get("from")))
    except Exception:
        return CAL_TAIL_UNKNOWN
    cf, ct = calendar_coverage(doc)
    if cf is None or ct is None or frm < cf or frm >= ct:
        return CAL_TAIL_UNKNOWN
    osf = _cal_z_to_taipei(doc.get("open_silence_from"))
    if osf is not None and osf <= frm:
        return CAL_TAIL_OPEN
    for c in doc.get("closures") or ():
        a, b = _cal_z_to_taipei((c or {}).get("start")), _cal_z_to_taipei((c or {}).get("end"))
        if a is not None and b is not None and a <= frm < b:
            return CAL_TAIL_CLOSURE
    return CAL_TAIL_NONE


def closures_with_uncertainty(closures, segs_all):
    """**C-PC12 兜底**（零新查詢）：closure ⊕ 該時段有 countable 工單活躍的綑 → `uncertain_eids`。

       為什麼必須做：整綑同一天全部機台都在停機時，全廠訊號軸上長得跟「休息」一模一樣
       ⇒ 會被判成 closure ＝**停機憑空消失**。工單資料本頁本來就在手（`segs_all`），
       交叉檢查零成本；發生率低但方向危險，別因為「很少發生」跳過。

       · 對每個 closure：找出「有 countable 工單段與它重疊」的 EID → 經
         `_probe_kun_activity._EID2KUN` 映到綑 → **該綑全體名冊 EID** 都標不確定
         （綑是排班決策單位＝C-PC2；只標那一台等於用單機空窗推論，那是 C-PC6 禁止的）。
       · EID 查無綑映射 ⇒ **不擴散**（closure 對其他機台照常適用）——不猜。
       · 共用 EID 同時點亮兩綑＝保守放寬（多標不確定 ⇒ 少主張停線），可接受。
       輸入的 `closures` 不被 mutate（回新 list）。
    """
    out = []
    e2k = ka_kun_map()
    for c in closures or ():
        a, b = c.get("start"), c.get("end")
        kuns = set()
        for eid, segs in (segs_all or {}).items():
            if not any(isinstance(s, dict) and s.get("countable", True)
                       and s.get("start") is not None and s.get("end") is not None
                       and s["start"] < b and s["end"] > a for s in segs or ()):
                continue
            for k in e2k.get(eid) or ():
                kuns.add(k)
        unc = sorted({e for k in kuns for e in kun_members(k)})
        out.append({"start": a, "end": b, "uncertain_eids": tuple(unc)})
    return out


_KUN_MAPS = None


def _kun_maps():
    """`(_EID2KUN, {綑: (成員 EID…)})`；probe 未就緒 → 兩個空 dict（誠實降級、不炸）。

       ★ 名冊唯一出處＝`_probe_kun_activity.KUN_ROSTER`（C-PC2／A4：Chris 人工維護）
         ——本檔**不另抄一份綑結構**。
    """
    global _KUN_MAPS
    if _KUN_MAPS is None:
        try:
            import _probe_kun_activity as _ka
            _KUN_MAPS = (dict(_ka._EID2KUN),
                         {k: tuple(es) for k, _a, _n, es in _ka.KUN_ROSTER})
        except Exception:
            _KUN_MAPS = ({}, {})
    return _KUN_MAPS


def ka_kun_map():
    return _kun_maps()[0]


def kun_members(kun):
    return _kun_maps()[1].get(kun) or ()


# ---- ①c 該跑窗的兩個綑級 raw（SPEC-RUNWINDOW §5；loader 只有這一份＝C-MP3）-----------
#   `_KUN-MANPOWER-RAW.json`（擴窗階梯第三層的門檻）＋`_KUN-ACTIVITY-RAW.json`
#   （階梯第一/二層的 slot 位圖）。**缺檔＝常態**（probe 由 Chris/排程跑）⇒ absent
#   不產生任何警語；invalid 才值得記一句。讀法（coverage／逐日切片）在
#   `board_runwindow`（純函式）——本檔只管 I/O 與版本印記。
KUN_MANPOWER_PATH = _wp.kun_manpower_path(repo=os.path.dirname(os.path.abspath(__file__)))
KUN_SLOTS_PATH = _wp.kun_activity_path(repo=os.path.dirname(os.path.abspath(__file__)))


def _raw_stamp(path):
    """`(mtime, size)`；檔不在 → None（絕不 raise）。＝`prod_calendar_stamp` 的同型。"""
    try:
        st = os.stat(path)
        return (int(st.st_mtime), int(st.st_size))
    except Exception:
        return None


def kun_manpower_stamp():
    """人力 raw 版本印記（app 快取鍵尾巴用；SPEC-RUNWINDOW §5.2）。"""
    return _raw_stamp(KUN_MANPOWER_PATH)


def kun_slots_stamp():
    """slot raw 版本印記（同上）。"""
    return _raw_stamp(KUN_SLOTS_PATH)


def _load_kun_raw(path, top_key):
    """共用載入形：JSON dict ∧ 有 `top_key` dict ⇒ ok；否則 absent/invalid。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return (None, "absent")
    except Exception:
        return (None, "invalid")
    if not (isinstance(doc, dict) and isinstance(doc.get(top_key), dict)
            and doc.get(top_key)):
        return (None, "invalid")
    return (doc, "ok")


def load_kun_manpower(path=None):
    """`_KUN-MANPOWER-RAW.json` → `(doc|None, status)`；status ∈ {ok, absent, invalid}。"""
    return _load_kun_raw(path or KUN_MANPOWER_PATH, "by_prod_day_shift")


def load_kun_slots(path=None):
    """`_KUN-ACTIVITY-RAW.json`（需含 slot 軌）→ `(doc|None, status)`。
       ⚠ 舊格式 raw（無 `by_prod_day_slots`）＝invalid——擴窗階梯 ①② 需要 slot 位圖，
       沒有就走 §5.3 降級形（呼叫端拿到 None 自然降級）。"""
    return _load_kun_raw(path or KUN_SLOTS_PATH, "by_prod_day_slots")


# ---- ② 三源分派 ---------------------------------------------------------------------
def _attrib_wo_segs_by_eid(wos_by_eid, win_start, win_end, dormant_events=None):
    """`{eid: [normalize_wo]}` → `{eid: [S12 段]}`（**重用 `wem.wo_segments_scoped`、零重寫**）。

       ★ A10 擴散 R2（2026-08-16；SPEC-DORMANT §5.3）：`/daily` 的機隊成員是**父機那一台**
         （timeline `fetch_fleet` 排模組）⇒ 母單範圍縮小到某幾個工位時，「這台機器」不算
         整台停（維修桶不再高估）；且**登記長停的工位不計任何 downtime**。兩者都收口在
         `wem.wo_segments_scoped`（＝`/utilization`／landing 的 union 版同一支口徑）。
       ⚠ 母單須由呼叫端（app `_build_attrib_day`）先帶好 `scope_events`；沒帶＝整機
         ＝高估側降級（同 `/equipment` 紀律）。`dormant_events=None`＝過渡橋常數。"""
    out = {}
    for eid, wos in (wos_by_eid or {}).items():
        segs = wem.wo_segments_scoped(eid, wos, win_start, win_end,
                                      dormant_events=dormant_events)
        if segs:
            out[eid] = segs
    return out


def attrib_eid_cls_map(fbc):
    """站→機台目錄（`{cls: [(eid, desc)…]}`）→ **EID→站候選** `{eid: (cls, …)}`（純函式）。

       ★ 用途＝T1 名冊補集在 live 路徑的**權威對映**：長窗 `fetch_fleet` 目錄（app 端
         `load_fleet_by_cls`，凍結 fetcher、人工可審）認得「整天在修、昨日零生產段」的機台
         屬於哪一站——而昨日 timeline 名冊天生看不到它們。
       ★ 候選是 **tuple 不是單值**：同一台機器可能在多站出現（跨站機台）⇒ 歧義由呼叫端
         決定「不硬塞」，這裡不做 last-wins 覆寫（那會靜默選錯站）。
    """
    out = {}
    for cls, lst in (fbc or {}).items():
        for item in (lst or ()):
            eid = item[0] if isinstance(item, (tuple, list)) else item
            if not eid:
                continue
            if cls not in out.setdefault(eid, []):
                out[eid].append(cls)
    return {e: tuple(sorted(cs)) for e, cs in out.items()}


def _attrib_assign_wo_eids(segs_all, on_roster, order, eid_cls):
    """WO-only EID（有 countable 工單、但不在任何一站的昨日 timeline 名冊上）→ 歸站。

       回 `(extra_by_cls, stats)`：
         · `extra_by_cls`＝`{cls: [eid…]}` ⇒ 呼叫端把它併進該站的 `wo_segs`，
           引擎的名冊補集（`diag.n_roster_added`）就會把整台 24h 補進帳本（T1）。
         · `stats`＝逐類計數（UI/verify 用）：`added` / `other_station`（對映到已知的
           **非 BOSS 站** ＝有解釋、不是帳本破洞）／`ambiguous`（跨站機台）／`unknown`
           （長窗目錄裡查無此 EID）。
       **只認權威對映**（`eid_cls`＝長窗 fetch_fleet 目錄）——不用機台名字/描述猜（M4
       runtime discovery 紅線）；歧義與未知一律留在外面並自揭，絕不硬塞任何站。
    """
    order_set = set(order or ())
    extra, other, ambig, unknown = {}, [], [], []
    for eid in sorted(segs_all or ()):
        if eid in on_roster:
            continue
        cands = (eid_cls or {}).get(eid) or ()
        if isinstance(cands, str):
            cands = (cands,)
        in_order = sorted({c for c in cands if c in order_set})
        if len(in_order) == 1:
            extra.setdefault(in_order[0], []).append(eid)
        elif len(in_order) > 1:
            ambig.append(eid)                    # 跨站機台：選哪一站都是猜 ⇒ 不硬塞
        elif cands:
            other.append(eid)                    # 對映到非 BOSS 站＝本頁本就不管它
        else:
            unknown.append(eid)                  # 目錄裡查無此 EID（或目錄不可用）
    stats = {"added": sum(len(v) for v in extra.values()), "other_station": len(other),
             "ambiguous": len(ambig), "unknown": len(unknown),
             "added_by_cls": {c: len(v) for c, v in sorted(extra.items())}}
    return extra, stats


def build_attrib_board(source, win_start, win_end, machines_by_cls, *, order=None,
                       wos_by_eid=None, wo_status=None, yb=None, wip_rows=None,
                       wip_meta=None, grid_min=None, pareto_docs=None, computed_at=None,
                       eid_cls=None, skipped_cls=None, diag=None, dormant_events=None,
                       calendar=None, cal_status=None, rw_by_eid=None,
                       seats=None, equiv_factor_by_cls=None):
    """20 站昨日時間帳本 → `daily_board` canonical（live/snapshot 真值路徑；純組裝、零 I/O）。

       · `machines_by_cls`＝`{cls: data["machines"]}`（呼叫端的 timeline 快取；名冊即 `fetch_fleet`
         機隊＝含「昨日零生產段但仍在名冊上」的機台）。
       · `wos_by_eid`＝cmms A3 LIST 分組結果；**`wo_status != 'live'` ⇒ 一律以 `wo_segs=None`
         呼叫引擎**（＝degraded：整片閒置灌未解釋、絕不拿「沒有工單」當「沒有維修」）。
       · **`eid_cls`＝T1 名冊補集的權威對映**（`attrib_eid_cls_map(load_fleet_by_cls(...))`）：
         「整天在修、昨日零生產段」的機台不在昨日 timeline 名冊上——只靠 `machines_by_cls`
         過濾 `wo_segs` 會把它整台 24h 濾掉，**維修桶在最嚴重的長修日系統性低估**（T1 紅線）。
         有了長窗目錄就能把它歸回原站，引擎自己會補名冊（`diag.n_roster_added`）。
         對映不可用／歧義／目錄查無 ⇒ **不硬塞**，逐類計數自揭（`n_wo_eids_*`）。
       · `skipped_cls`＝呼叫端 timeline **查詢失敗/預算跳站**的站：這些站掛 `src_err`
         不可測帳本，**不得**與「名冊上沒有機台」（`no_fleet`）共用同一句（R5）。
       · **`calendar`／`cal_status`＝G2 生產日曆**（`SPEC-PROD-CALENDAR.md`；additive）：
         closure 是**全廠級**事實 ⇒ 每一站餵**同一份**清單（C-PC6：綑/單機級的靜默日曆
         一律沉默）。C-PC12 兜底在這裡做（`closures_with_uncertainty`）——`segs_all`
         本來就在手，**零新查詢**。`calendar=None`／驗不過 ⇒ 每站 `closures=None`
         ＝與 G2 之前逐位相同。
    """
    wa = attrib_module()
    if wa is None:
        return attrib_unavailable("board_attrib_module_missing", source,
                                  win_start.strftime("%Y-%m-%d"))
    order = list(order or machines_by_cls.keys())
    wo_ok = str(wo_status or "").lower() == "live"
    segs_all = (_attrib_wo_segs_by_eid(wos_by_eid, win_start, win_end,
                                       dormant_events=dormant_events) if wo_ok else {})
    series = attrib_wip_series(wip_rows, wip_meta or {}, win_start, win_end,
                               _attrib_series_classes(order), grid_min) if wip_rows else {}
    ymap = {s.get("cls"): s for s in ((yb or {}).get("stations") or [])}
    skipped = {c for c in (skipped_cls or ()) if c not in machines_by_cls}

    # ── pass 1：昨日 timeline 名冊 ────────────────────────────────────────────
    roster, on_roster = {}, set()
    for cls in order:
        eids = {m.get("eid") for m in (machines_by_cls.get(cls) or ()) if m.get("eid")}
        roster[cls] = eids
        on_roster |= eids
    # ── pass 2：WO-only EID 歸站（T1 名冊補集；不硬塞、逐類自揭）───────────────
    extra, wo_stats = _attrib_assign_wo_eids(segs_all, on_roster, order, eid_cls)

    # ── pass 2b：closure 清單＋ C-PC12 兜底（零新查詢）────────────────────────
    #   ★ A5（2026-08-23）：closure 桶已退場——closures 在引擎裡**只剩 uncertain 標記**
    #     一個用途（rw 軸下的誠實殘差）。沒有 rw_by_eid ⇒ 不餵（引擎會忽略＋caveat，
    #     這裡直接不傳＝乾淨）。
    cal_status = cal_status or ("ok" if calendar else "absent")
    cal_raw = calendar_closures(calendar, win_start, win_end) if calendar else []
    closures = (closures_with_uncertainty(cal_raw, segs_all)
                if (cal_raw and rw_by_eid is not None) else None)
    n_unc_eids = len({e for c in (closures or ()) for e in c.get("uncertain_eids") or ()})

    # ── pass 2c：★ R44（C-AV16，Fable 2026-09-29）形③ 父機的妥善率分子＝在役座推導 ───────
    #   只在 rw 軸 ∧ 工單源 live；同一支 `wem.parent_seat_rollup`（＝`/availability` 列尾、
    #   `/utilization` A chip、站級三軸、下鑽同尺）。只替換引擎 `rw.avail_down_sec`／
    #   `rw.down_by_state` 那幾台父機的加項（引擎 kw `avail_rollup`），其餘一個位元不動。
    avail_rollup = None
    if wo_ok and rw_by_eid is not None:
        try:
            _rw_off = {e: [((a - win_start).total_seconds(), (b - win_start).total_seconds())
                           for a, b in (ivs or ())]
                       for e, ivs in rw_by_eid.items()}
            _rolls = wem.parent_seat_rollups(
                list(rw_by_eid), wos_by_eid, win_start, win_end, rw_by_eid=_rw_off,
                dormant_events=dormant_events, down_states=wa.woee.RW_AVAIL_DOWN_STATES)
            avail_rollup = {p: {"avail_down_sec": r["down_sec"],
                                "down_by_state": dict(r["by_state_union"]),
                                "excl_sec": r["excluded_rw_sec"]}
                            for p, r in _rolls.items()} or None
        except Exception as e:                 # noqa: BLE001（算不出＝逐位元舊行為）
            print(f"[attrib] R44 parent seat rollup failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            avail_rollup = None

    leds = {}
    for cls in order:
        if cls in skipped:                       # 來源側失敗 ≠ 沒有機台（MED）
            leds[cls] = wa.unavailable_ledger(cls, win=(win_start, win_end),
                                              diag={"downstream": wa.downstream_of(cls)})
            continue
        machines = list(machines_by_cls.get(cls) or [])
        eids = set(roster.get(cls) or ()) | set(extra.get(cls) or ())
        wo_segs = ({e: segs_all[e] for e in eids if e in segs_all}) if wo_ok else None
        dn_cls = wa.downstream_of(cls)
        leds[cls] = wa.build_ledger(cls, machines, wo_segs, series.get(cls),
                                    series.get(dn_cls), attrib_rate_ctx(ymap.get(cls)),
                                    grid_min=grid_min or wa.GRID_MIN,
                                    win=(win_start, win_end), dn_cls=dn_cls,
                                    wo_source=("ok" if wo_ok else "degraded"),
                                    closures=closures, rw_by_eid=rw_by_eid,
                                    # ★ W3／W1（`SPEC-CAPACITY-FORECAST.md` §2）：座帳與
                                    #   停機當量係數**由呼叫端（app）用 `board_capacity`
                                    #   算好傳進來**（C-CF3 座名冊唯一住處）；本層只轉手。
                                    seats=seats,
                                    equiv_factor=(equiv_factor_by_cls or {}).get(cls),
                                    # ★ #182-P2 F2：零工期八站＝站級常數（T6 一致化；
                                    #   與 F1 同批——F1 讓 stub 變多，沒有這條零工期站就會
                                    #   以「可測、0 生產」把整窗分母灌進 U）。
                                    zero_len_classes=wa.ZERO_LEN_STATIONS,
                                    avail_rollup=avail_rollup)
    board = wa.daily_board(leds, order=order, day=win_start.strftime("%Y-%m-%d"),
                           source=source, wo_source_status=("ok" if wo_ok else "degraded"),
                           pareto_docs=pareto_docs, end_day=win_start.strftime("%Y-%m-%d"),
                           computed_at=computed_at)
    board["diag"] = dict(diag or {}, wo_status=wo_status,
                         n_wo_eids=len(segs_all),
                         # 真正的帳本破洞＝歧義＋目錄查無（**不含**對映到非 BOSS 站者：
                         # 那些機台本來就不歸這 20 站管、混進來只會讓警語永遠亮著）
                         n_wo_eids_unassigned=wo_stats["ambiguous"] + wo_stats["unknown"],
                         n_wo_eids_added=wo_stats["added"],
                         n_wo_eids_added_by_cls=wo_stats["added_by_cls"],
                         n_wo_eids_other_station=wo_stats["other_station"],
                         n_wo_eids_ambiguous=wo_stats["ambiguous"],
                         wo_map_src=("fleet_by_cls" if eid_cls else "unavailable"),
                         wo_map_n=len(eid_cls or {}),
                         wip_rows=(wip_meta or {}).get("n_rows"),
                         wip_grid_points=len(next(iter(series.values()), ())) if series else 0,
                         wip_series_classes=len(series))
    # G2 日曆源狀態（additive 揭露欄；**缺檔＝常態、不是警語**＝UI 靠 closure 時數決定要不要
    # 出字，不靠這個欄位出噪音）。`covers_day`＝C-PC8：coverage 沒蓋滿本窗 ≠「那天沒生產」。
    board["cal_status"] = cal_status
    board["cal"] = {"status": cal_status,
                    "generated_at": (calendar or {}).get("generated_at"),
                    "covers_day": (calendar_covers(calendar, win_start, win_end)
                                   if calendar else None),
                    "n_closures": len(cal_raw),
                    "n_uncertain_eids": n_unc_eids,
                    # A5：rw 軸未接（無日曆/未涵蓋）⇒ 分母退回舊 24h 軸（頁面標示）
                    "inert": bool(cal_raw) and not wo_ok,
                    "rw_axis": rw_by_eid is not None}
    return board


def _attrib_series_classes(order):
    """重放需要的站集＝**這些站 ∪ 它們的下游站**（下游堵桶要讀下游 cover）。"""
    wa = attrib_module()
    out = set(order or ())
    if wa is not None:
        for c in list(out):
            d = wa.downstream_of(c)
            if d:
                out.add(d)
    return out


def get_attrib_day(source, win_start, win_end, machines_by_cls=None, **kw):
    """昨日歸因 canonical（SPEC §3/§5）。三源語意同其他計分板；**任何失敗都回
       `attrib_unavailable(...)` 而不是 raise**（/daily 該段落誠實降級、絕不假造桶）。"""
    wa = attrib_module()
    day = win_start.strftime("%Y-%m-%d") if win_start else None
    if wa is None:
        return attrib_unavailable("board_attrib_module_missing", source, day)
    if source == "snapshot":
        # 聚合切面沒有 per-machine 昨日區間、也沒有當時的 WIP 位置（同 get_wip 的 D6 裁決）。
        return attrib_unavailable("snapshot_unsupported", "snapshot", day)
    if source == "demo":
        try:
            return wa.demo_board(day=day or wa.DEMO_DAY)
        except Exception as e:
            return attrib_unavailable(f"error:{type(e).__name__}", "demo", day)
    try:
        return build_attrib_board(source, win_start, win_end, machines_by_cls or {}, **kw)
    except Exception as e:
        return attrib_unavailable(f"error:{type(e).__name__}", source, day)


# ---- ③ 日帳落地（C6；`_state/daily_ledger/YYYY-MM-DD.json`、gitignored）---------------
#   ★ 落地條件＝`source=='live'` only（`board_attrib.should_persist`）；
#     `degraded` 日允許重算覆寫（`should_overwrite`）＝非無條件 immutable。
#   ★ 2026-08-02 起日帳升 **v2**（SPEC-DAILY-UNIT §6）：**寫恆 v2**（`board_unit.ledger_doc`
#     ＝v1 station doc ＋ `units` 子塊；unit 板缺＝units=None 照寫 v2）、**讀雙收 v1/v2**
#     （`board_unit.parse_ledger_doc`；v1 舊檔照讀、units 補 None）。文件形狀與 scrub
#     （§24/T7 零人員欄）全在引擎檔；本檔只做檔案 I/O。`board_unit` 模組缺 → 退回 v1
#     讀寫（誠實降級、不炸）。
def ledger_dir():
    # 唯一出處＝`core_paths.ledger_dir`（`_backfill_ledger` 讀端同一支，R-18）；呼叫時才讀 env。
    return _wp.ledger_dir(repo=os.path.dirname(os.path.abspath(__file__)))


def ledger_path(day):
    return os.path.join(ledger_dir(), f"{day}.json")


def _parse_ledger_any(doc):
    """v1/v2 雙收 parse（單一收口）：unit 模組在＝`board_unit.parse_ledger_doc`（雙收）；
       不在＝退 `board_attrib.parse_ledger_doc`（v1 only；v2 檔讀不出＝計入缺日、誠實）。"""
    wu = unit_module()
    if wu is not None:
        return wu.parse_ledger_doc(doc)
    wa = attrib_module()
    return wa.parse_ledger_doc(doc) if wa is not None else None


#   ★ 合成產線 Q-C（來源隔離收口在**本層**，2026-09-29 異家族審查 finding）：日帳 doc 的
#     `source` 欄是承重資料欄。**所有**日帳讀取（頁面、顆數帳基準、柏拉圖、產品族活動、月報、
#     回補腳本、落地端讀既有檔）一律經 `read_ledger_doc`（`read_ledger_docs` 逐日呼叫它），
#     判準唯一出處＝`core_source.ledger_doc_admitted`：非合成進程只拒合成帳（缺欄舊帳照收）、
#     合成進程只收合成帳。被拒＝**視同缺檔**（回 None）＋計數（`ledger_source_rejects()`
#     進程累計；呼叫端要逐次數字就傳 `diag={}`）。目錄裡沒有合成帳時＝逐位元同改動前。
_LEDGER_SRC_LOCK = threading.Lock()
_LEDGER_SRC_REJECTS = {"n": 0, "last": []}       # last＝最近 32 筆被拒的 (day, source)
_LEDGER_SRC_LAST_MAX = 32


def ledger_source_rejects(reset=False):
    """本進程累計被來源隔離拒掉的日帳數 → `{"n": int, "last": [(day, source)…]}`（診斷用）。"""
    with _LEDGER_SRC_LOCK:
        out = {"n": _LEDGER_SRC_REJECTS["n"], "last": list(_LEDGER_SRC_REJECTS["last"])}
        if reset:
            _LEDGER_SRC_REJECTS["n"] = 0
            _LEDGER_SRC_REJECTS["last"] = []
    return out


def _diag_add(diag, key, item):
    if isinstance(diag, dict):
        diag.setdefault(key, []).append(item)


def _ledger_src_reject(day, doc, diag):
    src = doc.get("source") if isinstance(doc, dict) else None
    with _LEDGER_SRC_LOCK:
        _LEDGER_SRC_REJECTS["n"] += 1
        last = _LEDGER_SRC_REJECTS["last"]
        last.append((str(day), src))
        del last[:-_LEDGER_SRC_LAST_MAX]
    if isinstance(diag, dict):
        diag["n_source_rejected"] = int(diag.get("n_source_rejected") or 0) + 1
    _diag_add(diag, "source_rejected", (str(day), src))


def read_ledger_doc(day, *, raw=False, diag=None, base=None):
    """單日日帳 → 驗過的 dict（缺檔/壞檔/schema 不符 → None；**絕不猜**）。v1/v2 雙收。

       · **來源隔離**（Q-C）：`core_source.ledger_doc_admitted` 不收 ⇒ None（視同缺檔）＋計數。
       · `raw=True` ⇒ 回 json 原物件（不 parse；給落地端讀既有檔、回補腳本、探針用），
         來源隔離照樣先過。
       · `diag`（dict，選填）⇒ 累計 `n_source_rejected`／`source_rejected`［(day, source)…］／
         `read_errors`［(day, 例外名)…；含缺檔］。
       · `base`（選填）＝日帳目錄覆寫（探針對指定目錄讀）；預設 `ledger_path(day)`。"""
    p = os.path.join(str(base), f"{day}.json") if base is not None else ledger_path(day)
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        _diag_add(diag, "read_errors", (str(day), type(e).__name__))
        return None
    if not _wsrc.ledger_doc_admitted(doc):
        _ledger_src_reject(day, doc, diag)
        return None
    if raw:
        return doc
    try:
        return _parse_ledger_any(doc)
    except Exception as e:
        _diag_add(diag, "read_errors", (str(day), type(e).__name__))
        return None


def read_ledger_docs(days=None, end_day=None, v1_view=False, *, diag=None):
    """近 `days` 天日帳 → `[doc…]`（缺日/壞檔不炸、只是不在清單裡＝Pareto 自己會揭露涵蓋不足）。

       `v1_view=True`＝把 v2 檔降成 v1 視圖（`board_unit.to_v1_doc` 剝 units/units_line）
       ——**餵 `board_attrib.pareto30`（時間榜聚合權威）必須用這個**：wattr 的 parse 只認
       v1 schema_ver，直接餵 v2 會被當壞檔＝v2 上線後時間榜靜默丟光所有新日子。

       來源隔離（Q-C）逐日由 `read_ledger_doc` 做：被拒＝不在清單裡（同缺日）；`diag={}`
       取本次被拒數（`n_source_rejected`）與日期。"""
    wa = attrib_module()
    if wa is None:
        return []
    n = int(days or wa.PARETO_DAYS)
    end = wa._dt(str(end_day)) if end_day else datetime.now()
    out = []
    wu = unit_module() if v1_view else None
    for k in range(n):
        d = (end - timedelta(days=k)).strftime("%Y-%m-%d")
        doc = read_ledger_doc(d, diag=diag)
        if doc is not None and v1_view and wu is not None:
            doc = wu.to_v1_doc(doc)
        if doc is not None:
            out.append(doc)
    return out


#   ★ M3（2026-09-04）：v4 的**doc 級三鍵**（`rw_axis`／`rw_reason`／`families_status`）
#     一律**由呼叫端傳** `rw_meta=`（`board_unit.ledger_doc` 的裁決：本層看得到站列有沒有
#     `rw`，但看不到「為什麼沒有」⇒ 絕不反推）。**不傳＝三個 None＝逐位元原行為**。
_DOC_V4_META_KEYS = ("rw_axis", "rw_reason", "families_status",
                     # ★ #182-P2（v8）：建帳證據五鍵同一條路（呼叫端主張、顆數腿逐鍵沿用）。
                     #   鍵名唯一出處＝`board_unit._DOC_V4_KEYS + _DOC_V8_KEYS`（selftest 釘）。
                     "rw_anchor", "built_at", "wo_open_at_build", "cal_open_tail",
                     "roster_pad_unmeasurable")


def _doc_v4_meta(rw_meta, existing=None):
    """`rw_meta`（＋既有磁碟 doc）→ v4 doc 級三鍵（白名單、未知鍵忽略；純函式）。

    鍵名的唯一出處＝`board_unit._DOC_V4_KEYS`（selftest cross-pin 釘死）。未知鍵**忽略
    而不是炸**：呼叫端多帶診斷欄（如 `n_stations`）是常態，不該讓日帳落盤失敗。

    ★ **`rw_meta is None` ⇒ 逐鍵沿用 `existing`**（2026-09-04 HIGH；同 `carry_forward_units`
      的精神＝**保留前一位呼叫端的主張，本層不反推**）。為什麼非有不可：同一天的日帳有
      **兩個**寫者——`app._build_attrib_day`（帶 `rw_meta`）與 `board_data.get_unit_day`
      （**不帶**、它本來就不該主張 rw），而顆數腿通常是**最後一個寫者**（attrib 腿先寫出
      `units` 全 None 的檔 ⇒ `should_overwrite` 因「units 缺」放行 ⇒ 顆數腿覆寫）。舊行為
      把三鍵一律寫成 None ⇒ 落地檔恆 `rw_axis: null`，而 `should_overwrite` 的自癒只認
      `rw_axis is False`（`None`＝「沒人主張過」、刻意不放行）⇒ 那一天**永久停在 null**
      ⇒ `board_monthly` 的 `coverage.n_days_rw` 恆 0 ⇒ 月報凍結永遠 `rw_incomplete`。
      站列的 `rw` 還在（A／U／OEE 照算得出來）＝這個病**不會自己現形**，只會讓自動凍結
      永不發生。
    ★ 反過來，`rw_meta` **給了就整份以呼叫端為準**（連呼叫端寫 `None` 的鍵也是主張，不回頭
      看 existing）——那是 C3 的語意：「本次重算對這一天的判定」。要沿用只有一種寫法＝
      **整個 `rw_meta` 不傳**。沒有 existing（新檔／壞檔）⇒ 維持三鍵 None。
    """
    if rw_meta is None:
        e = existing if isinstance(existing, dict) else {}
        return {k: e.get(k) for k in _DOC_V4_META_KEYS}
    m = rw_meta if isinstance(rw_meta, dict) else {}
    return {k: m.get(k) for k in _DOC_V4_META_KEYS}


def persist_ledger(board, *, unit_board=None, grid_min=None, rw_meta=None,
                   products=None, products_status=None):
    """C6 落地：`live` 且（無檔 或 既有檔可覆寫）才寫；**原子寫**（temp + os.replace）。

       回 `(written: bool, reason: str)`——reason 供 diag/log 判讀（不落地也要講得出為什麼）。
       順手輪替過期日帳（`expired_days`，預設保留 90 天）。
       ★ 版本升級（SPEC-DAILY-UNIT §6＋Part C §4）：**寫恆 `board_unit.LEDGER_SCHEMA_VER`
         現行值（2026-08-03 起＝v3）**——`unit_board` 給了就帶 units 子塊；沒給（attrib 先
         落地、unit 板還沒算）＝units=None 檔（`should_overwrite` 判「units 缺」＝可被
         unit 路徑同日覆寫升級）。覆寫政策＝`board_unit.should_overwrite`
         （wo!=ok ∨ 非現行版 ∨ units 缺/degraded）；unit 模組缺＝退 v1 行為。
       ★ **第二道覆寫閘（2026-09-04）＝`board_unit.refuse_overwrite`**：既有檔可以被動，
         不代表**要寫進去的那份**配得上蓋掉它。整天時間源讀不到的板（站列全 `src_err`／
         `unmeasurable`、時間欄全 `None`）**不得覆寫一份有可測站的既有檔** ⇒ 回
         `(False, "refused:src_err_all_over_measurable")`，既有檔一個位元不動
         （事故＝2026-08-11；判準與反例全文在 `refuse_overwrite` 的 docstring）。
         **R48（2026-09-30）**：同一道閘另擋「工單源降級的新帳蓋 `wo_source_status == ok` 的
         舊帳」⇒ `(False, "refused:wo_degraded_over_ok")`（判準＝`board_unit.degraded_would_clobber_ok`）。
       ★ `rw_meta`（M3、additive）＝v4 doc 級三鍵 `{"rw_axis", "rw_reason", "families_status"}`
         的**呼叫端主張**（`_doc_v4_meta` 白名單過濾）。**不傳＝逐鍵沿用既有檔**
         （2026-09-04 HIGH；理由與反例全文在 `_doc_v4_meta` docstring）——顆數腿
         （`get_unit_day`）不主張 rw，卻常是同一天的最後一個寫者，一律寫 None 會把 attrib
         腿的主張抹掉、且因為 `should_overwrite` 只認 `rw_axis is False` 而**永久**停在 null。
         既有檔也沒有（新檔）⇒ 仍是三個 None。
         ⚠ 這三鍵**不得由本層反推**：本層看得到站列有沒有 `rw`／`families`，看不到
         「為什麼沒有」（日曆缺席？涵蓋不到？family 抓爆預算？）——編一個理由出來，
         那一天就永遠沒人回頭回補了（`board_unit.ledger_doc` 的裁決）。"""
    wa = attrib_module()
    if wa is None or not attrib_ok(board):
        return (False, "no_board")
    if not wa.should_persist(board.get("source")):
        return (False, "not_live")
    day = board.get("day")
    if not day:
        return (False, "no_day")
    wu = unit_module()
    try:
        # 缺檔／壞檔＝視同無檔（should_overwrite 回 True）。來源隔離（Q-C）：另一來源的帳
        #   （非合成進程碰到合成帳、合成進程碰到非合成帳）同樣視同無檔——**不沿用它的任何一腿**
        #   （carry_forward 不得把另一來源的顆數／子樹帶進本進程的帳）。
        existing = read_ledger_doc(day, raw=True)
        ow = (wu.should_overwrite(existing) if wu is not None
              else wa.should_overwrite(existing))
        if not ow:
            return (False, "already_ok")
        if wu is not None:
            # ★ v7（#103）：`products`／`products_status` 是 **additive keyword**——不傳
            #   ⇒ `ledger_doc` 寫 `products: null`，再由下面的 `carry_forward_products`
            #   從既有檔補回來（顆數腿是唯一抓得到 Kestrel grid 的寫者，時間腿不是）。
            doc = wu.ledger_doc(board, unit_board, grid_min=grid_min or wa.GRID_MIN,
                                products=products, products_status=products_status,
                                **_doc_v4_meta(rw_meta, existing))
            # ★ HIGH-1（2026-08-03）：**顆數腿只補不毀**。`_build_attrib_day` 的落地呼叫
            #   不帶 `unit_board` ⇒ 這份 doc 的 units 全 None；degraded 日 attrib 每 15
            #   分鐘重建一次、`should_overwrite` 又因為 `wo != ok` 放行 ⇒ 已經算好落地的
            #   顆數帳被自己輾掉，而 unit 側的日期鍵快取當天不會再回寫＝那天的顆數史永久
            #   消失、也永遠進不了 7d 基準樣本。`carry_forward_units` 只填新 doc 的洞、
            #   絕不覆蓋新值 ⇒ 無條件套用是安全且冪等的（同一天、同一份檔）。
            doc = wu.carry_forward_units(doc, existing)
            # ★ #103 同型（`carry_forward_products` 的 docstring 有反例）：Kestrel 子樹
            #   **只補不毀**——時間腿在 degraded 日每 15 分鐘重建一次且不帶 products，
            #   沒有這一句就會把顆數腿寫好的那棵樹輾掉，而顆數腿當天不會再回寫。
            doc = wu.carry_forward_products(doc, existing)
            # ★ 覆寫政策的**第二道**（2026-09-04）：**壞帳不得蓋好帳**。`should_overwrite`
            #   問的是「既有檔可不可以被動」，這一支問「要寫進去的這份配不配蓋掉它」
            #   ——2026-08-11 的事故（28 站 timeline 全部連線失敗）就活在中間那道縫裡：
            #   一份「整天零可測」的板蓋掉了 21 站可測的好帳，磁碟上只剩 `.bak-`。
            #   判準與理由全文＝`board_unit.refuse_overwrite`；拒絕時**既有檔與它的
            #   `.bak-` 一個位元不動**（這裡只是 return）。
            refuse = wu.refuse_overwrite(doc, existing)
            if refuse:
                return (False, refuse)
        else:                                     # unit 模組缺＝退 v1（誠實降級、不硬造 v2）
            doc = wa.ledger_doc(board, grid_min=grid_min or wa.GRID_MIN)
        d = ledger_dir()
        os.makedirs(d, exist_ok=True)
        p = ledger_path(day)
        # 同目錄唯一暫存名＋fsync＋os.replace（原子；讀者永不見半寫檔；兩寫者不共用暫存檔）
        _wat.atomic_write_json(p, doc)
        _prune_ledgers(day)
        return (True, "written")
    except Exception as e:
        return (False, f"error:{type(e).__name__}")


def persist_product_subtree(day, product, subtree, *, status=None):
    """**只改 `products.<product>` 這一棵**、Plover 的每一個位元原地不動（#103 D2）。

       回 `(written: bool, reason: str)`。用途＝`_backfill_ledger.py --product kestrel`
       的落地腿：既有那一天的日帳已經是事實（時間腿、顆數腿都算過了），要補的只有
       Kestrel 那一棵樹 ⇒ **絕不走整份重算**（重算要連 MES 兩分鐘、還會動到 `r_y`）。

       · 檔不存在／parse 不出來 ⇒ `(False, "no_doc")`——呼叫端該走**整份重算**那條路
         （`_backfill_ledger` 的 `build`），本函式**不生檔**（憑空造一份只有 Kestrel 的
         日帳＝一份沒有 Plover 帳的假日帳）。
       · `subtree is None` ⇒ `(False, "no_subtree")`（Kestrel grid 抓不到＝子樹缺席、
         **不寫一棵空樹**；只把 `products_status` 更新上去讓人看得見理由）。
       · 冪等：同一份子樹寫兩次 ⇒ 第二次 `(False, "already_same")`、檔一個位元不動。
       · **`schema_ver` 一律升到現行版**（子樹是 v7 的腿；不升版讀端分不出「這份檔有沒有
         那一腿」——同 `units_by_family` 的升版理由）。
       · 原子寫（temp + `os.replace`），與 `persist_ledger` 同一條紀律。
    """
    wu = unit_module()
    if wu is None:
        return (False, "no_unit_module")
    p = ledger_path(str(day))
    raw = read_ledger_doc(str(day), raw=True)      # 缺檔／壞檔／另一來源的帳（Q-C）＝no_doc
    if raw is None:
        return (False, "no_doc")
    if wu.parse_ledger_doc(raw) is None:
        return (False, "no_doc")
    blk = wu.parse_product_block(subtree) if subtree is not None else None
    if blk is None:
        if status is not None and raw.get("products_status") != status:
            raw["products_status"] = status
            _atomic_write_json(p, raw)
            return (False, "no_subtree:status_noted")
        return (False, "no_subtree")
    old = (raw.get(wu.PRODUCTS_KEY) or {}) if isinstance(raw.get(wu.PRODUCTS_KEY), dict) else {}
    if old.get(str(product)) == blk and raw.get("products_status") == (status or "ok"):
        return (False, "already_same")
    new = dict(old)
    new[str(product)] = blk
    raw[wu.PRODUCTS_KEY] = new
    raw["products_status"] = status or "ok"
    raw["schema_ver"] = wu.LEDGER_SCHEMA_VER
    try:
        _atomic_write_json(p, raw)
    except Exception as e:
        return (False, f"error:{type(e).__name__}")
    return (True, "written")


def wo_ledger_path():
    """工單日帳 sqlite 路徑（唯一出處＝`_wo_ledger.DEFAULT_LEDGER`／env `BOARD_WO_LEDGER`）。"""
    p = os.environ.get("BOARD_WO_LEDGER")
    if p:
        return p
    return _wp.state_path("wo_ledger.sqlite", repo=os.path.dirname(os.path.abspath(__file__)))


def wo_meta_status(wo_nos, path=None):
    """#182-P2 A②：旗上工單現在的狀態 → `{wo_no: {"closed": bool, "long": bool}}`（**唯讀**）。

       讀 `_state/wo_ledger.sqlite` 的 `wo_meta`（排程 `TendlineBoardWoLedger` 每日重拉 cmms 維護；
       `closed_at`／`active_end` 任一有值＝已結、`flag_long=1`＝>90 天長掛＝C-DT6 同一旗）。
       sqlite 一律 `mode=ro`（本函式永不寫它＝C-DT10 事實列唯一寫者）。檔不在／壞 ⇒ `{}`
       ＝全部「查無」⇒ `board_unit.wo_open_resolution` 判未解（不知道就不主張）。"""
    wos = sorted({str(x) for x in wo_nos or ()})
    if not wos:
        return {}
    p = path or wo_ledger_path()
    if not os.path.exists(p):
        return {}
    import sqlite3
    out = {}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            for i in range(0, len(wos), 400):
                chunk = wos[i:i + 400]
                q = ("SELECT wo_no, closed_at, active_end, flag_long FROM wo_meta "
                     f"WHERE wo_no IN ({','.join('?' * len(chunk))})")
                for no, closed, a_end, flong in con.execute(q, chunk):
                    out[str(no)] = {"closed": bool(closed) or bool(a_end),
                                    "long": bool(flong)}
        finally:
            con.close()
    except Exception:
        return {}
    return out


def wo_meta_eids(wo_nos, path=None):
    """工單號 → 機台 `{wo_no: eid}`（**唯讀**；`wo_meta.eid`；sqlite `mode=ro`）。

       2026-10-02 `wo_open_at_build` 讀端收窄的材料（`board_unit.narrow_wo_open_flags`）。
       檔不在／壞／查無那張 ⇒ 不出現＝「不知道機台」⇒ 讀端**照留旗**（不知道就不主張）。"""
    wos = sorted({str(x) for x in wo_nos or ()})
    if not wos:
        return {}
    p = path or wo_ledger_path()
    if not os.path.exists(p):
        return {}
    import sqlite3
    out = {}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            for i in range(0, len(wos), 400):
                chunk = wos[i:i + 400]
                q = ("SELECT wo_no, eid FROM wo_meta "
                     f"WHERE wo_no IN ({','.join('?' * len(chunk))})")
                for no, eid in con.execute(q, chunk):
                    if eid:
                        out[str(no)] = str(eid)
        finally:
            con.close()
    except Exception:
        return {}
    return out


def narrow_wo_open_docs(docs, path=None):
    """日帳 list → 同序 list，每份的 `wo_open_at_build` 收窄到「機台在該日站 `rw.eids` 內」的單。

       ★ 2026-10-02（`_FOLLOWUPS-20261002.md` ②；WO 35456 型誤旗）：2026-10-02 以前落地的日帳，
         旗的範圍是 `rw_by_eid`（＝`KUN_ROSTER` ∪ 本日 fleet，比進得了分母的站機台寬）。
         **不重寫日帳**——讀的時候收窄，凍結閘（`app._mr_unsettled_days`）、自動重建計畫、
         `/monthly` coverage 都吃收窄後的旗。判準＝`board_unit.ledger_rw_eids`＋
         `narrow_wo_open_flags`（與寫端同一條範圍定義）。
       · **零寫入**：沒變的那份回**同一個物件**；有變的回淺拷貝（`load()`／快取那份不 mutate）。
       · sqlite 讀不到／查無機台 ⇒ 旗照留（保守）；任何例外 ⇒ 原 list 原樣回（恆不 raise）。"""
    docs = list(docs or ())
    try:
        import board_unit as _wu
        allw = {w for d in docs for w in _wu.wo_open_flags(d)}
        if not allw:
            return docs
        we = wo_meta_eids(allw, path=path)
        if not we:
            return docs
        out = []
        for d in docs:
            fl = _wu.wo_open_flags(d)
            if not fl:
                out.append(d)
                continue
            kept = _wu.narrow_wo_open_flags(
                fl, _wu.ledger_rw_eids((d or {}).get("stations")), we)
            out.append(d if kept == fl else dict(d, wo_open_at_build=kept))
        return out
    except Exception:
        return docs


def mark_cal_open_tail_resolved(day, how, *, at=None):
    """#182-P2：日曆尾巴事後封口**且沒成 closure**（`sealed_none`）⇒ 就地在 `cal_open_tail`
       記 `resolved`（**只動這一個鍵**、其餘每個位元不動）→ `(written, reason)`。

       為什麼不重建：沒有 closure 形成 ⇒ 建帳當下算的分母就是對的，重建只會花 2–4 分鐘 MES
       得到同一份帳。為什麼要記：不記的話那一天永遠帶旗、30 天後日曆滾出涵蓋就再也判不了
       （凍結閘會一直點名它）。同 `persist_product_subtree` 的「只改一腿、原子寫」紀律；
       呼叫端負責「已凍月不寫」（C-DT10）。旗不在／已 resolved ⇒ 不寫。"""
    p = ledger_path(str(day))
    raw = read_ledger_doc(str(day), raw=True)      # 缺檔／壞檔／另一來源的帳（Q-C）＝no_doc
    if raw is None:
        return (False, "no_doc")
    tail = raw.get("cal_open_tail") if isinstance(raw, dict) else None
    if not isinstance(tail, dict):
        return (False, "no_flag")
    if tail.get("resolved"):
        return (False, "already_resolved")
    raw["cal_open_tail"] = dict(tail, resolved=str(how),
                                resolved_at=(at or datetime.now()).replace(microsecond=0)
                                .isoformat())
    try:
        _atomic_write_json(p, raw)
    except Exception as e:
        return (False, f"error:{type(e).__name__}")
    return (True, "written")


def _atomic_write_json(path, doc):
    """temp + `os.replace`（同目錄改名＝原子；讀者永不見半寫檔）。"""
    _wat.atomic_write_json(path, doc)       # 唯一暫存名＋fsync＋os.replace（見 board_atomic）


LEDGER_TMP_STALE_SEC = 3600.0     # `.tmp` 殘骸＝寫到一半就死掉的行程留下的（正常存活 <1s）


def _prune_ledgers(today):
    """保留 `KEEP_DAYS` 天輪替（純函式判定在 `board_attrib.expired_days`；此處只刪檔）。

       ★ 2026-08-03 兩個修（LOW E-⑤）：
         ① **輪替窗差一天**：`expired_days` 的地板＝`today 00:00 − keep_days`、判準是
            `dd < floor` ⇒ 恰好落在地板那天的檔**留著** ⇒ 實際保留 `keep_days + 1` 天。
            這裡把 `today` 往後推一天再問，才真的保留 `KEEP_DAYS` 天。
         ② **殘骸也要收**：`persist_ledger` 的 `.tmp`（行程被砍在 `os.replace` 之前）與
            `_backfill_ledger.py --force` 的 `.bak-*`（刻意保留的舊檔）**兩種都不叫
            `YYYY-MM-DD.json`** ⇒ 舊版輪替永遠看不到它們＝在 `_state/` 裡無限累積。
            `.bak-*` 照日期走同一條輪替線；`.tmp` 只清放超過一小時的（現役的別碰）。
    """
    wa = attrib_module()
    if wa is None:
        return 0
    try:
        d = ledger_dir()
        names = os.listdir(d)
    except Exception:
        return 0
    days = [n[:-5] for n in names if n.endswith(".json")]
    # ★ `today + 1d`＝把 expired_days 的「`< floor` 保留地板當天」語意校正回 KEEP_DAYS 天
    t_dt = wa._dt(str(today))
    today_q = ((t_dt + timedelta(days=1)).strftime("%Y-%m-%d") if t_dt is not None
               else today)
    dead = set(wa.expired_days(days, today_q))
    n = 0
    for dead_day in dead:
        try:
            os.remove(ledger_path(dead_day))
            n += 1
        except Exception:
            pass
    now = time.time()
    for name in names:
        p = os.path.join(d, name)
        try:
            if name.endswith(".tmp"):
                if (now - os.path.getmtime(p)) > LEDGER_TMP_STALE_SEC:
                    os.remove(p)
                    n += 1
            elif ".json.bak-" in name and name[:10] in dead:
                os.remove(p)
                n += 1
        except Exception:
            pass
    return n


# ======================================================================================
# ④ 站→機台對映的**落盤累積**（`_state/fleet_map.json`；Chris 2026-08-27 晚拍板）
#   規格存證＝`SPEC-RUNWINDOW.md` §4.1-b；warm-only 界線＝`SPEC-WARM-CACHE.md` §9⑭。
#
#   ★ 病因（2026-08-27 域內實測，`_probe_eid_cls_rates.py`）：`get_fleet_by_cls_live` 對
#     **全站逐站** `fetch_fleet`，`budget_sec`（＝`app.LAND_WO_BUDGET` 45 秒）用盡就提早收
#     ⇒ 實測一輪只完成 **全站中的兩站**，而且**每次抓到的是不同的子集**（16:16 那輪有 Seal 三台、
#     20:39 那輪只剩 CAL/CLN 三台）。`app._FBC_CACHE` 又是**整份覆蓋**（key 一樣就換掉整個
#     dict）⇒ `/equipment` 的產出當量集合**逐小時漂移**：同一台機台這小時有 ≈PH、下小時消失。
#     probe 的判定桶說得更白：開單當下那 12 台**全部 NO-MAPPING**，不是速率沒有、是**對映腿
#     根本沒抓到那些站**。
#   ★ 修法的立論＝**機台↔站別是慢變事實**（機台不天天搬家），所以它不該是 volatile 快取的
#     一次抓取結果，而該像日帳一樣**落盤累積**：本次抓到的站更新該站條目，**沒抓到的站保留
#     舊條目**（逐站 merge、**永不整份覆蓋**——整份覆蓋正是上面那個漂移的機制本身）。
#   ★ 只有**當量對映腿**讀它（`app._eqm_rates_by_eid`）：維修體質／MTBF 頁、著陸 A↔U 拆分
#     照舊吃 `load_fleet_by_cls` 的 volatile 結果，**回傳語意一個位元不變**（run_smoke 有負斷言）。
#   ★ 站齡上限＝`app.FLEET_MAP_MAX_AGE_D`（env `BOARD_FLEET_MAP_MAX_AGE_D`、預設 90 天）：
#     超齡的站條目**不採**、誠實掉回 NO-MAPPING——一個 90 天沒被證實過的對映拿去掛當量，
#     比誠實顯「—」傷害大（同 `_eqm_pick_rates` 新鮮度上限的那條理由）。
#   ★ 本節**零 MES、零 cmms**：寫入點掛在**既有**抓取成功之後（旁路 write-through），
#     讀取點只讀一個本地小 JSON ⇒ warm-only 語意一個位元沒動。
# ======================================================================================
FLEET_MAP_FILE = "fleet_map.json"
FLEET_MAP_SCHEMA_VER = 1
FLEET_MAP_MAX_AGE_D = 90          # 預設站齡上限（天）；真正生效的值由 app 端 env 讀出後傳入


def fleet_map_path():
    return _wp.state_path(FLEET_MAP_FILE, repo=os.path.dirname(os.path.abspath(__file__)))


def _fm_machines(lst):
    """`[(eid, desc)…]`／`[[eid, desc]…]`／`[eid…]` → `[(eid, desc)…]`（去重保序、壞列丟棄）。

       ⚠ 一律回 **tuple 的 list**：`attrib_eid_cls_map` 只吃 `item[0]`，但磁碟往返會把
       tuple 變 list ⇒ 若不在這裡收口，「記憶體那份」與「磁碟那份」的元素型別會不同，
       合併後的 dict 會出現兩種形狀，等著在下一個消費者那裡炸。"""
    out, seen = [], set()
    for item in (lst or ()):
        if isinstance(item, (tuple, list)):
            eid = item[0] if item else None
            desc = item[1] if len(item) > 1 else ""
        else:
            eid, desc = item, ""
        eid = str(eid).strip() if eid else ""
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append((eid, str(desc or "")))
    return out


def parse_fleet_map(doc):
    """`fleet_map.json` 的 doc → `{cls: {"asof": iso, "machines": [(eid, desc)…]}}`（純函式）。

       壞 doc／壞站條目**一律丟棄不炸**（同 `read_ledger_doc` 的「絕不猜」紀律）；
       schema_ver 不符 ⇒ 整份不採（**拒讀，不冒險解讀**＝QG-3.3 的本地版）。"""
    if not isinstance(doc, dict):
        return {}
    try:
        if int(doc.get("schema_ver") or 0) != FLEET_MAP_SCHEMA_VER:
            return {}
    except (TypeError, ValueError):
        return {}
    out = {}
    for cls, ent in (doc.get("cls") or {}).items():
        if not (cls and isinstance(ent, dict)):
            continue
        machines = _fm_machines(ent.get("machines"))
        if not machines:
            continue                       # 空站條目＝沒有資訊，不佔位（也不會被誤判成「這站沒機台」）
        asof = str(ent.get("asof") or "").strip()
        out[str(cls)] = {"asof": asof, "machines": machines}
    return out


def merge_fleet_map(old, fresh, *, now_iso=None):
    """**逐站 merge、永不整份覆蓋**（純函式；`now_iso` 可注入）。

       `old`＝`parse_fleet_map` 的產物；`fresh`＝本次 `get_fleet_by_cls_live` 的
       `{cls: [(eid, desc)…]}`。本次抓到的站 ⇒ 換新機台清單＋`asof` 蓋成 `now_iso`；
       **本次沒抓到的站 ⇒ 原條目原封保留**（`asof` 也不動——它記的是「上次證實這站長這樣
       是什麼時候」，不是「上次寫檔是什麼時候」）。
       ⚠ **這支就是本輪的全部**：把它寫成 `dict(old, **fresh)` 之外的任何「整份換掉」形，
       就退回 2026-08-27 那個「當量集合逐小時漂移」的病灶。"""
    now_iso = now_iso or datetime.now().replace(microsecond=0).isoformat()
    out = {c: dict(e) for c, e in (old or {}).items()}
    for cls, lst in (fresh or {}).items():
        machines = _fm_machines(lst)
        if not (cls and machines):
            continue                       # 空站＝這輪沒抓到（get_fleet_by_cls_live 跳過空站）
        out[str(cls)] = {"asof": now_iso, "machines": machines}
    return out


def fleet_map_doc(parsed, *, now_iso=None):
    """`{cls: {...}}` → 落盤 doc（純函式）。`updated_at`＝寫檔時刻（與各站 `asof` 無關）。"""
    now_iso = now_iso or datetime.now().replace(microsecond=0).isoformat()
    return {"schema_ver": FLEET_MAP_SCHEMA_VER, "updated_at": now_iso,
            "cls": {c: {"asof": e.get("asof") or "",
                        "machines": [list(m) for m in (e.get("machines") or ())]}
                    for c, e in sorted((parsed or {}).items())}}


def fleet_map_fresh(parsed, *, today=None, max_age_d=None):
    """站齡過濾（純函式、`today` 可注入）→ `({cls: [(eid, desc)…]}, n_stale)`。

       `asof` 早於 `today − max_age_d`（地板 **inclusive**＝地板那天仍收）⇒ **不採**；
       `asof` 缺／壞形 ⇒ 也**不採**（「不知道多舊」與「太舊」在誠實面上同一級，
       安全側＝寧可掉回 NO-MAPPING，也不要用一個來歷不明的對映去掛當量）。
       回傳第二元素＝被丟掉的站數，供對映腿的揭露 log（沒有它，「這站沒對映」與
       「這站的對映過期了」在外面長得一模一樣）。"""
    if max_age_d is None:
        max_age_d = FLEET_MAP_MAX_AGE_D
    t = today or datetime.now().strftime("%Y-%m-%d")
    floor = None
    try:
        floor = (datetime.strptime(str(t)[:10], "%Y-%m-%d")
                 - timedelta(days=int(max_age_d))).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        floor = None                       # today 壞形＝不套地板（同 `_eqm_pick_rates` 的安全側）
    out, n_stale = {}, 0
    for cls, ent in (parsed or {}).items():
        asof = str((ent or {}).get("asof") or "")[:10]
        try:                               # ⚠ 必須真的 parse：`"not-a-date"` 長度也是 10，
            datetime.strptime(asof, "%Y-%m-%d")   # 而且字串比大小時 'n' > '2' ⇒ 光比字典序會**收下**它
        except (TypeError, ValueError):
            n_stale += 1                   # 缺／壞形一律不採（**與 floor 無關**，連 today 壞形時也不採）
            continue
        if floor is not None and asof < floor:
            n_stale += 1
            continue
        machines = _fm_machines((ent or {}).get("machines"))
        if machines:
            out[cls] = machines
    return (out, n_stale)


def merge_fleet_fbc(disk, mem):
    """磁碟累積 ∪ 本次記憶體 fleet（純函式）——**逐站合併、記憶體較新者勝**。

       記憶體那份是這一輪剛從 MES 抓回來的事實 ⇒ 同一站兩邊都有時取記憶體；
       記憶體沒抓到的站由磁碟補位（**這就是「累積」在讀取端的全部意思**）。
       ⚠ 記憶體側的**空站不覆蓋**磁碟：`get_fleet_by_cls_live` 跳過空站，所以
       「記憶體沒有這站」永遠只代表「這輪沒抓到」，不代表「這站沒機台了」。"""
    out = {c: list(v) for c, v in (disk or {}).items()}
    for cls, lst in (mem or {}).items():
        machines = _fm_machines(lst)
        if cls and machines:
            out[str(cls)] = machines
    return out


def fleet_fetch_order(stations, asof_by_cls):
    """**stale-first 抓取順序**（純函式）：沒抓過的站最優先，其次 `asof` 最舊的。

       `stations`＝`mes_timeline.EQP_STATIONS`（**原序即 tie-break**：Python 的 sort 穩定
       ⇒ 同 `asof`／同樣沒抓過的站維持原順序）。**只換迴圈順序、查詢文本一字不動。**
       ★ 為什麼需要它：預算截斷下若每輪都從第 1 站開始，第 3 站以後**永遠**抓不到
       ⇒ 落盤累積永遠收斂不了。stale-first 讓每輪從上次沒抓到的地方接下去。
       ★ 空磁碟那一輪＝**與舊行為逐位元相同**（全部沒抓過 ⇒ key 全同 ⇒ 原序）。
       ⚠ 已知取捨（不是 bug）：`load_fleet_by_cls` 的 volatile 結果**同一份被維修體質／
       著陸 A↔U 共用** ⇒ 預算截斷下它們看到的站別會**逐輪輪替**（舊行為是永遠只看到
       前兩站）。覆蓋率沒有變差、只是換成輪流；當量腿因為讀累積檔而完全不受影響。"""
    seq = [c for c in (stations or ())]
    amap = asof_by_cls or {}
    return sorted(seq, key=lambda c: str((amap.get(c) or "")))


def read_fleet_map():
    """`_state/fleet_map.json` → `parse_fleet_map` 的產物（缺檔／壞檔 → `{}`；**絕不炸**）。"""
    try:
        with open(fleet_map_path(), "r", encoding="utf-8") as f:
            return parse_fleet_map(json.load(f))
    except Exception:
        return {}


def persist_fleet_map(fbc, *, now_iso=None):
    """write-through：把本次抓到的站 merge 進 `_state/fleet_map.json`（**原子寫**）。

       回 `(written: bool, reason: str)`——**恆不 raise**：落盤是旁路，失敗只影響「下次
       開機少幾站」，**絕不能影響 `load_fleet_by_cls` 原本的回傳**（呼叫端不看回傳值也對）。
       ⚠ 呼叫端負責 `source == "live"` 守門（合成／快照的目錄不得污染累積檔＝QG-4.2 混源）。"""
    if not fbc:
        return (False, "empty")
    try:
        doc = fleet_map_doc(merge_fleet_map(read_fleet_map(), fbc, now_iso=now_iso),
                            now_iso=now_iso)
        p = fleet_map_path()
        _wat.atomic_write_json(p, doc)     # 唯一暫存名＋fsync＋os.replace（讀者永不見半寫檔；同 persist_ledger）
        return (True, f"written:{len(doc.get('cls') or {})}")
    except Exception as e:
        return (False, f"error:{type(e).__name__}")


# ======================================================================================
# /daily 顆數帳本 canonical（`board.unit.v1`；規格＝`SPEC-DAILY-UNIT.md`）
#   本節與 Qlik / WIP / attrib 節同構：**只做三源 canonical**、一個口徑字都不在這裡重述——
#   恆等式/基準/橋接語意全在 `board_unit`（B1d 純函式引擎）、重工計數全在 `mes_rework`
#   （B1c；**本節＝唯一 import `mes_rework` 的地方**，同 mes_wip/mes_qlik 慣例）。
#   ★ 報廢桶＝**消費呼叫端傳入的既有 qlik grid**（app.load_qlik_grid 昨日窗快取）——
#     本層絕不重拉 Qlik（R7：絕不 per-consumer 各查一次）。
#   ★ 三源：live＝真路徑（rework SQL ＋ 日帳基準 ＋ attrib/qlik 板 join）；demo＝
#     `board_unit.demo_board`（確定性、走真引擎）；snapshot＝誠實降級（聚合切面無
#     窗內嘗試列可算＝同 attrib 的裁決）。
# ======================================================================================
UNIT_SCHEMA = "board.unit.v1"

_WU = None
_WU_TRIED = False
_MR = None
_MR_TRIED = False


def unit_module():
    """lazy import `board_unit`（未就緒 → None ＝ 誠實降級、絕不炸整站；同 attrib_module）。"""
    global _WU, _WU_TRIED
    if not _WU_TRIED:
        _WU_TRIED = True
        try:
            import board_unit as _m
            _WU = _m
        except Exception as e:
            print(f"[board_data] board_unit unavailable "
                  f"({type(e).__name__}: {str(e)[:120]}) → /daily 顆數帳誠實降級", flush=True)
            _WU = None
    return _WU


def rework_module():
    """lazy import `mes_rework`（**唯一 import 它的檔＝本檔**；未就緒 → None ＝ 計數腿降級）。"""
    global _MR, _MR_TRIED
    if not _MR_TRIED:
        _MR_TRIED = True
        try:
            import mes_rework as _m
            _MR = _m
        except Exception as e:
            print(f"[board_data] mes_rework unavailable "
                  f"({type(e).__name__}: {str(e)[:120]}) → 重工計數腿誠實降級", flush=True)
            _MR = None
    return _MR


def unit_unavailable(reason, source=None, day=None):
    """顆數帳誠實降級形：canonical 骨架 + `unavailable` 原因；**絕不拿別的數字頂替、絕不合成**。
       消費面（/daily）一律以 `unit_ok()` 判定；不可用時顆數欄整批「—」＋一句說明。"""
    return {"schema": UNIT_SCHEMA, "source": source, "day": day,
            "unavailable": reason, "rows": [], "hero": None, "units_line": None,
            "absorption": [], "absorbed_total": None, "pareto30": None,
            "computed_at": None, "diag": {"degraded": True}}


def unit_ok(board):
    """可用＝非 None、無 `unavailable`、且有站列（空盤＝不可用，寧顯降級不顯 0）。"""
    return bool(board) and not board.get("unavailable") and bool(board.get("rows"))


def unit_rows_by_cls(board):
    """`{cls: unit row}`（消費面 join 用；不可用→{}）。"""
    if not unit_ok(board):
        return {}
    return {r.get("cls"): r for r in (board.get("rows") or []) if r.get("cls")}


def get_unit_day(source, win_start, win_end, *, attrib_board=None, qlik_grid=None,
                 qlik_grid_kestrel=None, labels=None, persist=True):
    """昨日顆數帳 canonical（SPEC-DAILY-UNIT §7）。三源語意同其他計分板；**任何失敗都回
       `unit_unavailable(...)` 而不是 raise**（/daily 顆數欄誠實降級、絕不假造桶）。

       · `attrib_board`／`qlik_grid` 由呼叫端傳入（app 的日期鍵/窗鍵快取）——本層零重拉。
       · 重工腿＝`mes_rework.run(day)`（唯一新 SQL；失敗＝rework=None ⇒ 板自揭
         `no_rework_src`、其餘腿照算＝partial 不整段陣亡）。
       · 基準腿＝**只從日帳讀**（`board_unit.base_rates`；絕不即時重算 7 天＝§9 反模式）。
       · `labels`＝BOSS 友善名 `{cls: label}`（呼叫端從 `board_daily` 傳入——本檔不
         import board_daily、避免循環）。
       · 落地（C6）：live 且 attrib 板可用時 `persist_ledger(attrib, unit_board=板)`＝v2；
         **重工源整批缺席的板不落地**（wu.should_overwrite 不看 attempts，一旦落地就把
         這一天鎖成「attempts 全 None」、rework 恢復後也升不了級——寧可留檔可覆寫）。"""
    wu = unit_module()
    day = win_start.strftime("%Y-%m-%d") if win_start else None
    if wu is None:
        return unit_unavailable("board_unit_module_missing", source, day)
    if source == "snapshot":
        # 聚合切面沒有窗內嘗試列、也沒有逐日日帳基準（同 get_attrib_day 的裁決）。
        return unit_unavailable("snapshot_unsupported", "snapshot", day)
    if source == "demo":
        try:
            return wu.demo_board(day=day or wu.DEMO_DAY)
        except Exception as e:
            return unit_unavailable(f"error:{type(e).__name__}", "demo", day)
    try:
        att_rows = attrib_rows_by_cls(attrib_board)
        rework = None
        mr = rework_module() if not _synth_proc() else None   # 重工腿（第二期）：合成進程不打 MES
        if mr is not None:
            try:
                rework = mr.run(day=day, timeout=LIVE_QUERY_TIMEOUT_SEC)
            except Exception as e:                # SchemaDivergence／連線失敗＝計數腿降級
                print(f"[board_data unit/rework ERR] {type(e).__name__}: "
                      f"{str(e)[:300]}", flush=True)
                rework = None
        base = wu.base_rates(
            read_ledger_docs(days=wu.BASE_DAYS + 1, end_day=day), day)
        board = wu.build_board(
            day, att_rows, qlik_grid, rework, base, source=source, labels=labels,
            pareto_docs=read_ledger_docs(end_day=day),
            end_day=day,
            computed_at=datetime.now().replace(microsecond=0).isoformat())
        # ★ 時間腿整板缺席（attrib 暫時失敗）＝**可恢復降級**，必須自揭：否則 app 端會把
        #   「43 站全 no_time_src」的板當成事實黏進日期鍵 immutable 快取一整天，而 attrib
        #   自己 15 分鐘就自癒 ⇒ 同一頁兩本帳互相矛盾（verify V2-MED）。
        # ★ `wo_source_status` 一併透傳（2026-08-03 HIGH-1b）：cmms 當機那天時間帳四桶
        #   會把維修時數灌進「未解釋」＝顆數帳的換算分解也跟著失真，而板本身**看不出**
        #   （grid 好好的、rework 也在）⇒ app 端的 immutable 日期鍵會把它黏一整天。
        #   attrib 自己 15 分鐘自癒，unit 也要能跟著自癒 ⇒ 判準必須看得到這個欄位。
        board["diag"] = dict(board.get("diag") or {},
                             att_missing=(not attrib_ok(attrib_board)),
                             wo_source_status=(attrib_board or {}).get("wo_source_status"))
        # ★ #103（D2）：Kestrel 那一腿**與 Plover 完全獨立**——`qlik_grid_kestrel` 抓不到
        #   （None／unavailable）⇒ 子樹缺席 ＋ `products_status` 講得出理由，而 Plover 的
        #   落地**一個位元不受影響**（「Kestrel failure never blocks the Plover write」）。
        a_blk, a_status = None, None
        if qlik_grid_kestrel is not None:
            try:
                a_blk = wu.product_block(qlik_grid_kestrel, product=wu.PRODUCT_KESTREL)
            except Exception as e:                # 子樹是加分腿：炸了也不得拖垮顆數帳
                print(f"[board_data unit/kestrel ERR] {type(e).__name__}: "
                      f"{str(e)[:200]}", flush=True)
                a_blk = None
            a_status = ("ok" if a_blk is not None else
                        f"skipped:{(qlik_grid_kestrel or {}).get('unavailable') or 'empty'}")
        if (persist and attrib_ok(attrib_board)
                and not all(r.get("attempts") is None for r in board["rows"])):
            persist_ledger(attrib_board, unit_board=board,
                           products=({wu.PRODUCT_KESTREL: a_blk} if a_blk else None),
                           products_status=a_status)
        board["diag"] = dict(board.get("diag") or {}, products_status=a_status)
        return board
    except Exception as e:
        print(f"[board_data unit ERR] {type(e).__name__}: {str(e)[:300]}", flush=True)
        return unit_unavailable(f"error:{type(e).__name__}", source, day)


# ======================================================================================
# 產品 family 活動層（M3 C2；`SPEC-PRODUCT-TAGS.md` §2.6 ＋ `SPEC-MONTHLY.md` §3.4）
#   兩支：**寫腿**（`fetch_day_families_live`＝日帳 v4 站列 `families` 的來源）與
#   **讀腿**（`read_family_activity`＝近 N 天聯集，給標籤 chip 的「實心＝近期在跑」）。
#   口徑與 SQL 全在 `mes_family`（**本檔＝唯一 import 它的地方**，同 mes_rework 慣例）；
#   本節只做編排、預算、與檔案 I/O。
#
#   ★★ `None ≠ {}`（這一節唯一一條會把事情做錯的線）
#      · 寫腿失敗／逾時 ⇒ 回 **None** ⇒ doc 級 `families_status="skipped:…"／"error:…"`，
#        站列 `families` 留 None＝「**沒查**」。
#      · 查成功但那站那天零 family 段 ⇒ 該站 `{}`＝「**查過了、沒在跑**」。
#      把前者寫成 `{}` 就是把「不知道」畫成「閒著」＝ C-PT3 明令要防的事（chip 會從
#      「活動未知（維持實心）」變成「空心」＝憑空宣稱這台機沒在跑那個產品）。
#   ★ **絕不 raise 進日帳建構**：family 是加分欄，抓不到就是抓不到，不得拖垮 /daily。
# ======================================================================================
FAMILY_BUDGET_ENV = "BOARD_FAMILY_BUDGET"     # 秒；整批（全部站）共用一個預算
FAMILY_BUDGET_SEC = 30.0
# 讀腿窗的 fallback。⚠ **窗的唯一出處是呈現層**（C6 `board_tagui.RECENT_DAYS`，
#   env `BOARD_TAG_RECENT_DAYS`）——呼叫端一律明傳 `days=`；這裡的 7 只是 CLI/測試
#   沒傳時的保守值，**刻意不讀那個 env**（第二個 env 讀者＝第二份會漂移的事實）。
FAMILY_DAYS_FALLBACK = 7

_MF = None
_MF_TRIED = False


def family_module():
    """lazy import `mes_family`（**唯一 import 它的檔＝本檔**；未就緒 → None ＝ family 腿降級）。

    ⚠ **必須是 lazy**：`import board_data` 不得把 `mes_family` 帶進 `sys.modules`
    （run_smoke 有釘子）。理由同 `mes_rework`——那是一條會連 MES 的路徑，import 圖上
    不該有人「順手」把它拉起來。
    """
    global _MF, _MF_TRIED
    if not _MF_TRIED:
        _MF_TRIED = True
        try:
            import mes_family as _m
            _MF = _m
        except Exception as e:
            print(f"[board_data] mes_family unavailable "
                  f"({type(e).__name__}: {str(e)[:120]}) → family 活動層誠實降級", flush=True)
            _MF = None
    return _MF


def family_budget_sec():
    """整批 family 抓取的秒預算（env `BOARD_FAMILY_BUDGET`；壞值 → 預設 30s）。"""
    try:
        v = float(os.environ.get(FAMILY_BUDGET_ENV, FAMILY_BUDGET_SEC))
        return v if v > 0 else FAMILY_BUDGET_SEC
    except Exception:
        return FAMILY_BUDGET_SEC


def fetch_day_families_live(win_start, win_end, stations=None, budget_sec=None, stats=None):
    """單日 per-站 per-EID family 段數（live 寫腿）。回 `{cls: {eid: {family: n}}}`｜`None`。

       · **一條連線跑完全部站**（`mes_family.open_connection`；43 站各開一條＝43 次
         Kerberos 握手）。逐站 `mes_family.run(cls, win, cur)`。
       · **整批共用一個秒預算**（`budget_sec`／env `BOARD_FAMILY_BUDGET`，預設 30s）：
         每站開跑前檢查；用完 ⇒ **回 None**（不回半盤）。半盤在這個介面上**無法自我描述**
         ——呼叫端拿到 dict 只會當「查完了」，沒查的站就會被寫成 `{}`＝「沒在跑」。
         寧可整批誠實作廢（`families_status="skipped:budget"`），也不吐一盤半真半假。
       · 連線逾時＝`ceil(預算)` 秒（不是 `LIVE_QUERY_TIMEOUT_SEC` 的 180）：單站掛住
         也不可能超出整批預算。
       · **任何失敗恆回 None、絕不 raise**（family 是加分欄；SPEC-MONTHLY §3.4）。
       · `stats`＝呼叫端可傳入的 dict；本函式寫入 `status`（`"ok"`／`"skipped:<why>"`／
         `"error:<T>"`＝**doc 級 `families_status` 直接用它**）＋ 計時與逐站自揭欄。
       · 站清單：`stations` 未給 ⇒ `mes_timeline.EQP_STATIONS`（**永不 runtime discovery**）；
         給了就照給的（順序保留、去重、只收字串）。
    """
    st_out = stats if isinstance(stats, dict) else {}
    if _synth_proc():                       # M8 產品族（第二期；R-8）：比照 demo 的 `skipped:source`
        st_out["status"] = "skipped:source"
        return None
    mf = family_module()
    if mf is None:
        st_out["status"] = "skipped:module_missing"
        return None
    if win_start is None or win_end is None:
        st_out["status"] = "skipped:no_window"
        return None
    seen = set()
    sts = []
    for c in (stations if stations is not None else mt.EQP_STATIONS):
        if isinstance(c, str) and c and c not in seen:
            seen.add(c)
            sts.append(c)
    st_out.update({"n_stations": len(sts), "n_queried": 0})
    if not sts:
        st_out["status"] = "skipped:no_stations"
        return None
    budget = family_budget_sec() if budget_sec is None else float(budget_sec or 0)
    if budget <= 0:
        st_out["status"] = "skipped:budget"
        return None
    st_out["budget_sec"] = round(budget, 1)

    conn = None
    out = {}
    per = []
    t_all = time.perf_counter()
    try:
        conn, cur = mf.open_connection(timeout=max(1, -int(-budget // 1)))   # ceil，零 import
        for cls in sts:
            if time.perf_counter() - t_all >= budget:
                st_out.update({"status": "skipped:budget",
                               "elapsed_s": round(time.perf_counter() - t_all, 2),
                               "slowest": sorted(per, key=lambda r: -r[1])[:5]})
                print(f"[board_data families] 預算 {budget:.0f}s 用盡於 "
                      f"{st_out['n_queried']}/{len(sts)} 站 ⇒ 整批作廢（skipped:budget）",
                      flush=True)
                return None
            one = {}
            fams = mf.run(cls, (win_start, win_end), cur, stats=one)
            out[cls] = fams          # ★ 恆寫（含 `{}`）＝「查過了」；沒查的站不會出現在鍵裡
            st_out["n_queried"] += 1
            per.append((cls, one.get("timing", {}).get("total_ms", 0)))
    except Exception as e:
        print(f"[board_data families ERR] {type(e).__name__}: {str(e)[:300]}", flush=True)
        st_out.update({"status": f"error:{type(e).__name__}",
                       "elapsed_s": round(time.perf_counter() - t_all, 2)})
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    st_out.update({
        "status": "ok",
        "elapsed_s": round(time.perf_counter() - t_all, 2),
        "n_eid_rows": sum(len(v) for v in out.values()),
        "n_segments": sum(sum(f.values()) for v in out.values() for f in v.values()),
        "slowest": sorted(per, key=lambda r: -r[1])[:5],
    })
    return out


def family_activity_from_docs(docs, days=None, end_day=None):
    """**純函式**：日帳 docs → `({eid: {...}}, meta)`（近窗 family 活動聯集）。

       回：
         · `{eid: {"families": {family: n}, "days": k, "as_of": "YYYY-MM-DD"}}`
           ——`families`＝窗內段數逐 family 相加；`days`＝這顆 EID 出現過 family 資料的
           **天數**；`as_of`＝它**最後一次**有資料的那一天。
         · `meta = {"stations_seen", "as_of", "days_seen", "n_docs", "n_docs_with_families",
                    "statuses", "days_requested", "end_day"}`
           ——`stations_seen`＝窗內**被查過**的站（該站在某一天的 `families` 非 None）。
             消費端判「活動未知」靠它：某 EID 的普查站**不在** `stations_seen` ⇒ 那顆
             chip 是 **`recent=None`（維持實心＋『活動未知』tooltip）**，不是空心。
       · **v3 檔貢獻零**（沒有 `families` 欄）——但它們仍計入 `n_docs`，所以
         `n_docs_with_families == 0` 就是「窗內還沒有 v4 資料」＝全體 unknown。
       · `families` 為 `None` ⇒ 該站那天**沒查**（不進 `stations_seen`）；
         `{}` ⇒ 查過零段（**進** `stations_seen`）。這條線就是 C-PT3。
       · 壞 doc／壞站列／壞值一律跳過、不炸（同 `read_ledger_docs` 的紀律）。
    """
    acc = {}
    days_by_eid = {}
    last_by_eid = {}
    stations_seen = set()
    days_seen = set()
    statuses = {}
    n_with = 0
    for doc in docs or ():
        if not isinstance(doc, dict):
            continue
        day = doc.get("day")
        day = day if isinstance(day, str) else None
        s = doc.get("families_status")
        statuses[s if isinstance(s, str) else "none"] = \
            statuses.get(s if isinstance(s, str) else "none", 0) + 1
        hit = False
        for srow in doc.get("stations") or ():
            if not isinstance(srow, dict):
                continue
            fams = srow.get("families")
            if not isinstance(fams, dict):        # None（沒查）／壞值 ⇒ 不進 stations_seen
                continue
            cls = srow.get("cls")
            if isinstance(cls, str) and cls:
                stations_seen.add(cls)
            hit = True
            for eid, per in fams.items():
                if not isinstance(eid, str) or not isinstance(per, dict):
                    continue
                d = acc.setdefault(eid, {})
                for fam, n in per.items():
                    if not isinstance(n, (int, float)) or isinstance(n, bool):
                        continue
                    d[fam] = d.get(fam, 0) + int(n)
                if day:
                    days_by_eid.setdefault(eid, set()).add(day)
                    if last_by_eid.get(eid) is None or day > last_by_eid[eid]:
                        last_by_eid[eid] = day
        if hit:
            n_with += 1
            if day:
                days_seen.add(day)
    # 鍵序＝**段數多的在前、同數以名字**（確定性、可重現）。刻意**不**在這裡引用
    # `mes_family.FAMILIES`：讀腿是零 MES 的純函式，不該為了排序把那支模組拉進 import 圖；
    # 而顯示順序本來就由呈現層（`board_tagui` 依 `FAMILIES` 逐 chip 走）決定。
    act = {}
    for eid in sorted(acc):
        d = acc[eid]
        ordered = {f: n for f, n in sorted(d.items(), key=lambda kv: (-kv[1], str(kv[0])))}
        act[eid] = {"families": ordered,
                    "days": len(days_by_eid.get(eid) or ()),
                    "as_of": last_by_eid.get(eid)}
    meta = {"stations_seen": sorted(stations_seen),
            "as_of": (max(days_seen) if days_seen else None),
            "days_seen": sorted(days_seen),
            "n_docs": sum(1 for d in (docs or ()) if isinstance(d, dict)),
            "n_docs_with_families": n_with,
            "statuses": statuses,
            "days_requested": days,
            "end_day": end_day}
    return act, meta


def read_family_activity(days=None, end_day=None):
    """近 `days` 天日帳 → `(activity, meta)`（讀腿；**薄 I/O 包**，口徑全在純函式）。

       `days` 未給 ⇒ `FAMILY_DAYS_FALLBACK`（7；窗的唯一出處是 C6 的呈現層常數，
       呼叫端請明傳）。缺日/壞檔＝不在清單裡（`read_ledger_docs` 的既有行為）⇒
       `meta.n_docs` 會自己揭露涵蓋不足。**零 MES、只讀落地日帳。**
    """
    n = int(days or FAMILY_DAYS_FALLBACK)
    docs = read_ledger_docs(days=n, end_day=end_day)
    return family_activity_from_docs(docs, days=n, end_day=end_day)


# ======================================================================================
# ---- PM 保養進度板（`board.pm.v1`；`/equipment/pm`）------------------------------------
#   規格＝`SPEC-PM-BOARD.md`（§1 鐵則／§2 C-P1–C-P8 口徑／§4 三路源／§5 誠實降級／§9 反模式）。
#   本節與上面各節同構：**只做三源 canonical 與編排**，一個口徑字都不在這裡重述
#   （四態判定/backlog/owner 聚合全在純函式 `board_pm`；HTTP 全在 `board_w8_transport`）。
#
#   ★ 三路源（§8.3）：`/pm-schedules`＝**主源**（backlog／owner／預警／標工／寬限／suppress）
#     ｜WO LIST（`work_type='PM'`）＝完成度母體與趨勢｜WO detail＋`/tasks/{no}/steps`＝展開卡。
#     ＋**第四條「名稱腿」`/tasks`（S-PM4／§8.4）＝task_no→人話名字典，fail-open**：
#     掛掉只讓清單顯代號（`_3TN905K21` 之類），板的任何數字零影響——**絕不因字典失敗降級整板**。
#   ★ **主源亡＝整板亡；WO 腿亡＝partial**（score/trend 降級，backlog／owner／預警照建）——
#     沒理由因為次要腿掛了就把「誰欠 40 件保養」這種主源事實一起藏起來。
#   ★ **live 雙閘**（P0-1）：`BOARD_CMMS_*`（cmms 設定）＋ **`BOARD_PM_LIVE`（本頁專屬）**。
#     後者是硬閘：cmms 2026-08-09 依 eMaint 全量重灌，**8/9 前的 live＝開發期混合態、不得讀**
#     ⇒ 未設 ⇒ `pm_live_gated` 誠實降級卡。/equipment 早就設好 cmms env、只靠它會提早打真 API。
#   ★ snapshot **不支撐**（同 `get_wip` D6 裁決）：到期與 backlog 是 as-of-now 的事實，
#     拿舊切面重播＝謊報現場欠帳。誠實降級，不硬造。
# ======================================================================================
PM_SCHEMA = "board.pm.v1"
PM_CALIBER = ("PM progress (due-based four-state; /pm-schedules backlog; "
              "standard_hours ruler)")
PM_SOURCES = ("live", "snapshot", "demo")
PM_LIVE_ENV = "BOARD_PM_LIVE"                  # P0-1 硬閘（8/10 之後 Chris 自己開）
PM_GRACE_ENV = "BOARD_PM_GRACE_D"              # per-PM 寬限欄 null 時的 fallback（C-P2）
# WO LIST 只能按**開單日**窗查（CM1：無到期日窗）⇒ 超抓後由 `board_pm.score` 按 due 過濾。
# 150 天＝涵蓋「本季」分數窗 ＋ 生成提前量（保養單常在到期前數週生成）。
PM_WO_LOOKBACK_D = int(os.environ.get("BOARD_PM_WO_LOOKBACK_D", "150"))
# ★ 2026-08-05 SALTMARSH 反饋輪 R1：趨勢由 12 **週**改 12 **個月**、窗多了「本年 YTD」
#   ⇒ 150 天的固定超抓**再也不夠**（12 個月要 ~365 天、年窗到 12 月底要 ~365 天）。
#   固定 lookback 不夠時的失敗型最惡劣：**早期月份會是結構性空白**，而頁面看起來只是
#   「那幾個月沒有保養到期」＝把抓不到講成沒發生。故改成**按窗與趨勢現算**（下界仍是 env 值）。
#   `PM_WO_GEN_LEAD_D`＝生成提前量：一張 X 月到期的單可能在數週前就開單，開單日窗要多退這麼多。
PM_WO_GEN_LEAD_D = int(os.environ.get("BOARD_PM_WO_GEN_LEAD_D", "45"))

_WPM = None
_WPM_TRIED = False
_W8T = None
_W8T_TRIED = False


def pm_module():
    """lazy import `board_pm`（未就緒 → None ＝ 誠實降級、絕不炸整站；同 unit_module）。"""
    global _WPM, _WPM_TRIED
    if not _WPM_TRIED:
        _WPM_TRIED = True
        try:
            import board_pm as _m
            _WPM = _m
        except Exception as e:
            print(f"[board_data] board_pm unavailable "
                  f"({type(e).__name__}: {str(e)[:120]}) → /equipment/pm 誠實降級", flush=True)
            _WPM = None
    return _WPM


def pm_transport_module():
    """lazy import `board_w8_transport`（PM 板的唯一 HTTP 腿；未就緒 → None ＝ 整板降級）。"""
    global _W8T, _W8T_TRIED
    if not _W8T_TRIED:
        _W8T_TRIED = True
        try:
            import board_w8_transport as _m
            _W8T = _m
        except Exception as e:
            print(f"[board_data] board_w8_transport unavailable "
                  f"({type(e).__name__}: {str(e)[:120]}) → PM live 誠實降級", flush=True)
            _W8T = None
    return _W8T


def pm_live_enabled():
    """P0-1 第二道閘：`BOARD_PM_LIVE` truthy 才放行 live（**預設關**）。
       ⚠ 這不是「功能開關」而是**資料可信度閘**——cmms 8/9 重灌前的 live 是開發期混合態。"""
    return os.environ.get(PM_LIVE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _pm_env_grace():
    """per-PM `completion_window_days` 為 null 時的全域 fallback（C-P2；壞值→None＝用模組預設）。"""
    v = os.environ.get(PM_GRACE_ENV, "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def pm_wo_lookback_d(d1, win="mo", wpm=None):
    """WO LIST 的**開單日**回看天數＝max(env 下界, 分數窗所需, 12 個月趨勢所需) ＋ 生成提前量。

    為什麼要現算（2026-08-05 §11 (a)/(b2)）：LIST 只有開單日窗（CM1），而頁面要的是**到期日**
    落在窗／趨勢內的單。抓得不夠遠 ⇒ 早期月份與年窗前段**結構性空白**，頁面卻只會顯示
    「那時候沒有保養到期」＝把「抓不到」講成「沒發生」。抓多了只是慢一點、且有 truncated 自揭。
    """
    d = d1.date() if hasattr(d1, "date") else d1
    need = [PM_WO_LOOKBACK_D]
    if wpm is not None:
        try:
            need.append((d - wpm.score_window(d, win)[0]).days + PM_WO_GEN_LEAD_D)
            need.append((d - wpm._month_starts(d)[0]).days + PM_WO_GEN_LEAD_D)
        except Exception:                      # noqa: BLE001（引擎形變異 ⇒ 退 env 下界、不炸）
            pass
    return max(need)


def pm_unavailable(reason, source=None):
    """PM 板誠實降級形：canonical 骨架 + `unavailable` 原因；**絕不拿別的數字頂替、絕不合成**。
       消費面（/equipment/pm）一律以 `board.get("unavailable")` 判定 → 全頁降級卡；
       **絕不因為拉不到就顯示「零逾期」**（§5：那是最糟的一種假話——正好把欠帳藏起來）。"""
    return {"schema": PM_SCHEMA, "caliber": PM_CALIBER, "mode": source, "source": source,
            "today": None, "win": None, "sched_status": None, "wo_status": None,
            "wo_ok": False, "truncated": False,
            "score": None, "backlog": None, "owners": None, "persons": None,
            "forecast": None, "trend": None, "items_index": None,
            "diag": {"degraded": True}, "unavailable": reason}


def pm_ok(board):
    """可用＝非 None 且無 `unavailable`（**空 backlog 是合法事實**——保養全做完了就是零列，
       與 WIP 的「空盤＝不可用」相反：那裡空盤代表查詢沒撈到，這裡空清單代表真的沒欠帳）。"""
    return bool(board) and not board.get("unavailable")


def pm_item_unavailable(reason, pm=None):
    """展開卡降級形（形同 `board_pm.demo_item`，兩腿標 off）。"""
    return {"pm": pm, "steps": {"status": "off", "rows": []},
            "wo": {"status": "off", "detail": None}, "unavailable": reason}


def _pm_ts(v, fmt="%Y-%m-%d %H:%M:%S"):
    """cmms 時戳（Z-aware ∨ 台北 naive）→ 台北牆鐘字串；壞值→None。
       **重用 `board_w8._parse_dt`＝全 repo 唯一一份 UTC→台北轉換**（絕不另寫第二份）。"""
    if v is None:
        return None
    try:
        return w8._parse_dt(v).strftime(fmt)
    except (ValueError, TypeError):
        return None


def _pm_wo_detail_view(raw):
    """WO detail raw → 展開卡的單況腿（形＝`board_pm.demo_item` 的 `wo.detail`）。

    · 完成時戳走 `board_pm._done_at`（C-P8 判準**唯一落點**，本檔不另判）。
    · `status_history` 空 ⇒ **遷移單 caveat**（W8 已知現實：大量舊 eMaint 單無轉移紀錄）
      → 回退成 `opened→closed` 一行 `fallback_span`，**不假裝有階段軌跡**。
    """
    wpm = pm_module()
    hist = []
    raw_hist = raw.get("status_history")
    for e in (raw_hist if isinstance(raw_hist, list) else []):
        if not isinstance(e, dict):
            continue
        st = e.get("to_status")
        at = _pm_ts(e.get("changed_at"))
        if isinstance(st, str) and at:
            hist.append({"to_status": st.strip().upper(), "changed_at_s": at})
    hist.sort(key=lambda r: r["changed_at_s"])
    span = None
    if not hist:
        s = _pm_ts(raw.get("opened_at"), "%Y-%m-%d %H:%M")
        e = _pm_ts(raw.get("closed_at"), "%Y-%m-%d %H:%M")
        if s:
            span = f"{s} → {e}" if e else f"{s} →"
    done = wpm._done_at(raw) if wpm is not None else None
    return {"wno": raw.get("work_order_no"),
            "status": (str(raw.get("status") or "").strip().upper() or None),
            "brief": raw.get("brief_description"),
            "closed_s": done.strftime("%Y-%m-%d %H:%M:%S") if done else None,
            "hist": hist or None, "fallback_span": span}


def get_pm_board(source="demo", *, win="mo", today=None):
    """PM 板 canonical（SPEC-PM-BOARD §4）。三源語意同其他計分板；**任何失敗都回
       `pm_unavailable(...)` 而不是 raise**（整頁誠實降級、絕不假造完成度或零逾期）。

    live 編排順序（每一步失敗的落點都寫在 SPEC §5）：
      ① `BOARD_PM_LIVE` 未設 → `pm_live_gated`（P0-1 硬閘、8/10 前一律走這裡）
      ② cmms env 未設 → `not_configured`
      ③ `/pm-schedules` 失敗 → 整板 `unavailable=<reason>`（**主源亡＝整板亡**）
      ④ WO LIST 失敗 → **partial**：`wo_status=<reason>`、score/trend 全 None，
         backlog／owner／預警照建（主源還在）
      ⑤ `/tasks` 名稱字典失敗 → **fail-open**：`diag.task_name_status=<reason>`、清單顯代號，
         **板其餘一切零影響**（S-PM4；名字是可讀性、不是承重件）
      ⑥ `board_pm.build_pm_board` 組板

    `today`＝可注入（測試/回放用）；live 預設＝`datetime.now()`（backlog 是 as-of-now 事實）。
    """
    src = source if source in PM_SOURCES else "demo"   # 分派點正規化（防直呼；同 load_dispatch 教訓）
    wpm = pm_module()
    # 窗鍵正規化走引擎那一份（`yr`／別名 `year` 都在那裡定義）＝**一份規則一個落點**
    wkey = wpm.win_key(win) if wpm is not None else "mo"
    if wpm is None:
        return pm_unavailable("board_pm_module_missing", src)
    if src == "snapshot":
        return pm_unavailable("snapshot_unsupported", "snapshot")
    if src == "demo":
        try:
            return wpm.demo_pm(win=wkey) if today is None \
                else wpm.demo_pm(today=today, win=wkey)
        except Exception as e:
            print(f"[board_data pm ERR] demo {type(e).__name__}: {str(e)[:300]}", flush=True)
            return pm_unavailable(f"error:{type(e).__name__}", "demo")
    # ---- live ----
    try:
        if not pm_live_enabled():
            return pm_unavailable("pm_live_gated", "live")      # ① 8/10 硬閘
        w8t = pm_transport_module()
        if w8t is None:
            return pm_unavailable("w8_transport_module_missing", "live")
        if not w8t.is_configured():
            return pm_unavailable("not_configured", "live")     # ②
        t0 = time.time()
        sched, reason = w8t.fetch_pm_schedules()                # ③ 主源
        if reason is not None:
            print(f"[board_data pm] /pm-schedules 失敗 → 整板降級 reason={reason}",
                  flush=True)
            return pm_unavailable(reason, "live")
        now = today or datetime.now()
        d1 = now.date() if hasattr(now, "date") else now
        wos, wo_status = [], "live"                             # ④ WO 腿（失敗＝partial）
        lookback_d = pm_wo_lookback_d(d1, wkey, wpm)            # 窗／12 個月趨勢現算（見上）
        wo_res, wreason = w8t.fetch_pm_work_orders(
            d1 - timedelta(days=lookback_d), d1)
        if wreason is not None:
            wo_status = wreason
            print(f"[board_data pm] WO 腿失敗 → score/trend 降級（backlog/owner 照建）"
                  f" reason={wreason}", flush=True)
        else:
            wos = wo_res["rows"]
        # ⑤ 名稱腿（S-PM4）＝`/tasks` 人話名字典。**fail-open**：任何失敗只讓頁面退回顯代號，
        #   板的四態／backlog／owner／預警一個位元都不受影響——名字是可讀性、不是承重件。
        #   ⚠ 只取 `task_no → description`：`/tasks` 也有 `is_active`，但那是**模板停用旗**，
        #     與殭屍判準（`/pm-schedules` 側的資產退役）不同義；不放進 map ＝結構上不可能混用。
        task_names, tn_status, tk_res = None, None, None
        # ★ 連「transport 根本沒有這支」都要 fail-open：直接呼叫 ⇒ AttributeError 會被外層
        #   catch-all 收成 `error:AttributeError` ＝**整板黑掉**，那正好違反 fail-open 本身。
        _fetch_tasks = getattr(w8t, "fetch_tasks", None)
        if _fetch_tasks is None:
            tn_status = "not_wired"
        else:
            tk_res, treason = _fetch_tasks()
            if treason is not None:
                tn_status = treason
                tk_res = None
                print(f"[board_data pm] 名稱腿失敗 → 顯示代號（板不降級）reason={treason}",
                      flush=True)
            else:
                task_names = {r["task_no"]: r["description"] for r in tk_res["rows"]
                              if r.get("description")}
                tn_status = "truncated" if tk_res.get("truncated") else "ok"
        # 任一路少讀就掛 truncated（覆蓋不足要看得見；WO 腿整個失敗時它的旗標無意義、不併）
        # ⚠ 名稱腿的 truncated **不併進板級 truncated**：那只代表少幾個名字，不是資料覆蓋
        #   不足；併進去會讓整板掛上「少讀」警示＝把裝飾品的問題講成承重件的問題。
        trunc = bool(sched.get("truncated")) or (
            wreason is None and bool((wo_res or {}).get("truncated")))
        board = wpm.build_pm_board(                             # ⑥
            sched["rows"], wos, today=now, win=wkey, mode="live", source="live",
            sched_status="live", wo_status=wo_status, truncated=trunc,
            assets_active=None,                 # v1 無 asset 端點 ⇒ 殭屍只剩 >90d 腿（頁上揭露）
            env_grace_d=_pm_env_grace(),
            task_names=task_names, task_name_status=tn_status)
        board["diag"] = dict(board.get("diag") or {},
                             wire_sched_dropped_n=sched.get("dropped_n"),
                             wire_wo_dropped_n=(wo_res or {}).get("dropped_n"),
                             wire_task_dropped_n=(tk_res or {}).get("dropped_n"),
                             wo_lookback_d=lookback_d)
        print(f"[board_data pm live] {time.time() - t0:.1f}s sched={sched['count']} "
              f"wo={len(wos)} wo_status={wo_status} "
              f"tasks={len(task_names or {})} task_name_status={tn_status}", flush=True)
        return board
    except Exception as e:
        print(f"[board_data pm ERR] {type(e).__name__}: {str(e)[:300]}", flush=True)
        return pm_unavailable(f"error:{type(e).__name__}", "live")


PM_ITEM_NAMES_TTL = float(os.environ.get("BOARD_PM_INV_TTL", "1800"))
PM_ITEM_NAMES_TTL_FAIL = 120.0
_PM_INAMES = None            # (mono_ts, {item_code: 顯示名}, status)


def pm_item_names(force=False):
    """零件人話名字典（S-PM6）→ `({item_code: 顯示名}, status)`；**fail-open、恆不 raise**。

    · 來源＝`/inventory-items` 兩頁 → `board_pm.item_names_map`
      （顯示鏈 name→description、雙空不入表＝規則住引擎、這裡只做 I/O 與快取）。
    · 半小時 TTL（零件主檔幾乎不動；失敗只黏 2 分鐘＝來源恢復即恢復）。**live 專用**：
      env 閘未開／未配置一律回 `({}, 'off')`＝**零網路**、展開卡顯 item_code。
    · **任何失敗都只讓用料欄顯代號**（呼叫端不得因此降級整張卡，更不得降級板）——
      這一層是可讀性，不是承重件（與 `/tasks` 名稱腿同族紀律）。
    """
    global _PM_INAMES
    now = time.monotonic()
    hit = _PM_INAMES
    if hit and not force:
        ttl = PM_ITEM_NAMES_TTL if hit[2] in ("ok", "truncated") else PM_ITEM_NAMES_TTL_FAIL
        if (now - hit[0]) < ttl:
            return (hit[1], hit[2])
    names, status = {}, "off"
    try:
        wpm, w8t = pm_module(), pm_transport_module()
        if wpm is None or w8t is None or not pm_live_enabled() or not w8t.is_configured():
            status = "off"
        else:
            _fetch = getattr(w8t, "fetch_inventory_items", None)
            if _fetch is None:
                status = "not_wired"           # transport 沒有這支＝fail-open，不是例外
            else:
                res, reason = _fetch()
                if reason is not None:
                    status = reason
                    print(f"[board_data pm/inv] 零件名字典失敗 → 用料顯代號"
                          f"（卡不降級）reason={reason}", flush=True)
                else:
                    names = wpm.item_names_map(res["rows"])
                    status = "truncated" if res.get("truncated") else "ok"
                    print(f"[board_data pm/inv] items={res['count']} names={len(names)} "
                          f"pages={res.get('pages')} status={status} "
                          f"page_error={res.get('page_error')}", flush=True)
    except Exception as e:                      # noqa: BLE001（字典腿永不外拋到卡）
        names, status = {}, f"error:{type(e).__name__}"
        print(f"[board_data pm/inv ERR] {type(e).__name__}: {str(e)[:200]}", flush=True)
    _PM_INAMES = (now, names, status)
    return (names, status)


COST_PARTS_TTL = float(os.environ.get("BOARD_COST_PARTS_TTL", "1800"))
COST_PARTS_TTL_FAIL = 120.0
_COST_PARTS = None           # (mono_ts, rows, status)


def cost_parts_rows(force=False):
    """cmms 零件主檔**原始列** → `(rows, status)`；**fail-open、恆不 raise**。

    ★ 唯一消費者＝`/cost` 的**第三層料號級比對**（`board_cost` ← `board_partsmatch`）。
      比對用的是每一列的 `vendor_part_no`（cmms NOTE-63 盤點：覆蓋率高但非全滿）。

    · 與 `pm_item_names()` **刻意分開兩份快取**：那一支回的是「item_code → 顯示名」的**字典**
      （PM 展開卡用），本支要的是**整列**。共用一份快取＝把兩個不同壽命、不同失敗後果的東西
      綁在一起（字典掛掉只讓用料顯代號；主檔掛掉是第三層整層不作用）。
    · 半小時 TTL（零件主檔幾乎不動；失敗只黏 2 分鐘）。**live 專用**：env 閘未開／未配置
      一律回 `([], 'off')`＝**零網路**，`/cost` 那一頁誠實顯示「第三層這次沒作用」。
    · **列數不是契約**（NOTE-63【五】1：不同時點回報的列數都不是錯的）
      ⇒ 本函式**零列數斷言**、零筆數告警。
    """
    global _COST_PARTS
    now = time.monotonic()
    hit = _COST_PARTS
    if hit and not force:
        ttl = COST_PARTS_TTL if hit[2] in ("ok", "truncated") else COST_PARTS_TTL_FAIL
        if (now - hit[0]) < ttl:
            return (hit[1], hit[2])
    rows, status = [], "off"
    try:
        w8t = pm_transport_module()
        if w8t is None or not pm_live_enabled() or not w8t.is_configured():
            status = "off"
        else:
            _fetch = getattr(w8t, "fetch_inventory_items", None)
            if _fetch is None:
                status = "not_wired"
            else:
                res, reason = _fetch()
                if reason is not None:
                    status = reason
                    print(f"[board_data cost/parts] 零件主檔失敗 → /cost 第三層這次不作用"
                          f"（頁不降級）reason={reason}", flush=True)
                else:
                    rows = list(res["rows"])
                    status = "truncated" if res.get("truncated") else "ok"
                    _vpn = sum(1 for r in rows if r.get("vendor_part_no"))
                    print(f"[board_data cost/parts] items={len(rows)} "
                          f"vendor_part_no={_vpn} status={status}", flush=True)
    except Exception as e:                      # noqa: BLE001（加分腿永不外拋）
        rows, status = [], f"error:{type(e).__name__}"
        print(f"[board_data cost/parts ERR] {type(e).__name__}: {str(e)[:200]}", flush=True)
    _COST_PARTS = (now, rows, status)
    return (rows, status)


# ---------------------------------------------------------------------------
# cmms 零件主檔的**本地快取**（2026-09-01 Chris 裁決 3；SPEC-RUN-COST.md §8.12-3）
#
#   ⚠⚠⚠ **為什麼要有它**（實測，不是推論）：cmms（Fly）睡著時 `cost/parts` 回
#     `reason=unreachable` ⇒ `/cost` 的第三層失效 ⇒ 剔除桶靜靜地縮水、零件直購
#     靜靜地膨脹（暖機前後票數與金額落差明顯），而**頁面零揭露**。
#     快取讓「主檔暫時連不上」變成一件**不影響數字、但講得出來**的事。
#   ⚠ 料號主檔幾乎不動（NOTE-63：不同時點回報的列數都不是錯的）
#     ⇒ 用一份幾天前的副本比用「沒有主檔」誠實得多。
#   ⚠ **live 優先**：拿得到 live 就用 live 並順手更新快取；拿不到才讀快取。
#     兩個都沒有 ⇒ `source="none"` ⇒ 表 2 整段降級（`board_runcost.build_oneoff`）。
# ---------------------------------------------------------------------------
COST_PARTS_CACHE = "cmms_parts_reference.json"
COST_PARTS_CACHE_SCHEMA = "board.cmms_parts_reference.v1"


def cost_parts_cache_path():
    return _wp.state_path(COST_PARTS_CACHE, repo=os.path.dirname(os.path.abspath(__file__)))


def _read_parts_cache(path=None):
    """快取檔 → `(rows, as_of)`；**恆不 raise**，缺檔／壞檔回 `([], None)`。"""
    p = path or cost_parts_cache_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:                           # noqa: BLE001
        return [], None
    if not isinstance(obj, dict) or obj.get("schema") != COST_PARTS_CACHE_SCHEMA:
        return [], None                         # 版本不對 ⇒ 整份拒讀（不半信半疑）
    rows = obj.get("rows")
    if not isinstance(rows, list) or not rows:
        return [], None
    return [r for r in rows if isinstance(r, dict)], obj.get("as_of")


def _write_parts_cache(rows, path=None):
    """把 live 拿到的主檔存成快取（**恆不 raise**：寫不進去不該讓頁面壞掉）。"""
    p = path or cost_parts_cache_path()
    try:
        doc = {"schema": COST_PARTS_CACHE_SCHEMA,
               "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
               "n": len(rows), "rows": rows}
        _wat.atomic_write_json(p, doc, indent=None)   # 原子換檔（唯一暫存名）：讀到半份比沒有更糟
        return True
    except Exception:                           # noqa: BLE001
        return False


def cost_parts_reference(force=False, path=None):
    """`/cost` 第三層要用的 cmms 備品主檔 ＋ **這一次的來源狀態** → `(rows, meta)`。

    `meta = {"source", "as_of", "status", "n"}`；`source`：
      · `live`  ——這一次真的從 cmms 拿到（順手更新快取）
      · `cache` ——連不上，用本地快取（頁首揭露「備品主檔＝快取 as of …」）
      · `off`   ——**這台主機結構上就沒接 cmms**（live 閘未開／未配置）⇒ 照舊行為
      · `none`  ——**該連得上卻連不上、而且連快取都沒有** ⇒ 表 2 整段降級

    ⚠⚠ `off` 與 `none` 必須分開：`off` 是「我方從來沒有聲稱這一層在跑」（開發機、smoke），
      `none` 是「它本來在跑、這次不見了」——後者才是會讓數字**靜默膨脹**的那一種。
      把兩者混成同一個狀態 ⇒ 要嘛開發機整頁黑掉，要嘛正式機繼續無聲膨脹。"""
    rows, status = cost_parts_rows(force=force)
    if rows:
        _write_parts_cache(rows, path)
        return rows, {"source": "live", "as_of": None, "status": status, "n": len(rows)}
    if status in ("off", "not_wired"):
        return [], {"source": "off", "as_of": None, "status": status, "n": 0}
    crows, as_of = _read_parts_cache(path)
    if crows:
        return crows, {"source": "cache", "as_of": as_of, "status": status,
                       "n": len(crows)}
    return [], {"source": "none", "as_of": None, "status": status, "n": 0}


COST_AGG_TTL = float(os.environ.get("BOARD_COST_AGG_TTL", "1800"))
COST_AGG_TTL_FAIL = 120.0
#: 請求窗固定覆蓋 SPEC-RUN-COST.md §8.1-1 最大檔位（近 12 個月）＋ 1 個月餘裕。
COST_AGG_WINDOW_MONTHS = 13
_COST_AGG = None             # (mono_ts, payload, status)


def _months_ago_first_day(n, *, anchor=None):
    """`anchor`（date，預設今天）往回推 `n` 個整月的第一天（純函式；不碰時區——
       cmms 的 `from_date`/`to_date` 是台北日曆日，這裡只需要月的粒度）。"""
    d = anchor or datetime.utcnow().date()
    idx = d.year * 12 + (d.month - 1) - n
    return d.replace(year=idx // 12, month=idx % 12 + 1, day=1)


def cost_aggregate_payload(force=False):
    """cmms 領料成本彙總的**原始 payload**（`GET /cost/aggregate`）→ `(payload, status)`；
       **fail-open、恆不 raise**。

    ★ 唯一消費者＝`/cost/runs`（`board_runcost` 的每 run 成本／每良品成本那條線）。
    · **深層驗證不在本函式**：13 鍵齊全、`schema_id` 字面、金額十進位字串——全部住
      `board_runcost.parse_aggregate`（SPEC-RUN-COST.md §8.8 的閘門就是為了不要在這裡
      猜形狀）。本函式只管「打不打得到、拿不拿得到」，回傳的是**原始 payload**，未經任何
      加工（同 `board_w8_transport.fetch_cost_aggregate` 的分工：那支負責網路 ＋ 最外層
      dict pin，本函式負責快取 ＋ live 閘）。
    · **請求窗固定覆蓋近 `COST_AGG_WINDOW_MONTHS`（13）個月，不隨頁面的 `months=` 改變**
      ——切檔位（3／6／12）是 `board_runcost.load_board(months=)` 的呈現層工作，不是重打
      一次 cmms（同 `_state/cost_rollup.json` 一次落地、頁面自己切窗的紀律）。
    · 半小時 TTL（cmms 允許回頭補改已結案工單的帳，過短追不上；`as_of` 由 payload 自帶，
      TTL 只決定多久重打一次），失敗只黏 2 分鐘（來源恢復即恢復）——**與 `cost_parts_rows`
      刻意分開一份快取**：那支是零件主檔（`/cost` 採購側第三層），這支是領料成本彙總
      （消耗側），兩者壽命與失敗後果都不同，綁在一起會互相拖累。
    · **live 專用**：`pm_live_enabled()` 這道閘未開，或未配置 `BOARD_CMMS_BASE_URL`／
      `BOARD_CMMS_TOKEN` ⇒ 一律回 `(None, 'off')`＝**零網路**，`/cost/runs` 誠實顯示
      「cmms 未連線」（SPEC-RUN-COST.md §7.2 降級態）。**沒有 `?source=` demo/snapshot 形**
      （SPEC §9.6 同一條紀律：這頁沒有一份合成副本，讀不到就誠實降級）。
    """
    global _COST_AGG
    now = time.monotonic()
    hit = _COST_AGG
    if hit and not force:
        ttl = COST_AGG_TTL if hit[2] == "ok" else COST_AGG_TTL_FAIL
        if (now - hit[0]) < ttl:
            return (hit[1], hit[2])
    payload, status = None, "off"
    try:
        w8t = pm_transport_module()
        if w8t is None or not pm_live_enabled() or not w8t.is_configured():
            status = "off"
        else:
            _fetch = getattr(w8t, "fetch_cost_aggregate", None)
            if _fetch is None:
                status = "not_wired"
            else:
                today = datetime.utcnow().date()
                from_date = _months_ago_first_day(COST_AGG_WINDOW_MONTHS - 1, anchor=today)
                res, reason = _fetch(from_date.isoformat(), today.isoformat())
                if reason is not None:
                    status = reason
                    print(f"[board_data cost/aggregate] 讀取失敗 → /cost/runs 這次降級"
                          f"（分母那半邊不受影響）reason={reason}", flush=True)
                else:
                    payload, status = res, "ok"
                    print(f"[board_data cost/aggregate] from={from_date.isoformat()} "
                          f"to={today.isoformat()} rows={len((res or {}).get('rows') or [])} "
                          f"status=ok", flush=True)
    except Exception as e:                       # noqa: BLE001（加分腿永不外拋）
        payload, status = None, f"error:{type(e).__name__}"
        print(f"[board_data cost/aggregate ERR] {type(e).__name__}: {str(e)[:200]}",
              flush=True)
    _COST_AGG = (now, payload, status)
    return (payload, status)


def get_pm_item(source, pm_id, *, idx_row=None):
    """backlog 列內展開卡（形＝`board_pm.demo_item`：`{pm, steps, wo, unavailable}`）。

    · `idx_row`＝呼叫端從已快取的 `board["items_index"]` 撈出的該列——**本函式不回頭重拉 LIST**
      （展開一列不該再掃整批排程）。
    · **兩腿獨立容忍**（§5）：細項腿與單況腿各自降級，一腿掛不影響另一腿、更不影響清單。
    · `steps.status`：`ok`（有料）｜`empty`（200 空陣列＝cmms 無細項）｜`gone`（task 404）｜
      `off`（S2 未上線／未配置／gated／**該排程根本沒有 task 參照**）｜`error`（其他失敗）。
    · `wo.status`：`ok`｜`none`（排程無 `last_work_order_no`＝沒單可拉，說 gone/off 都是謊）｜
      `gone`（404 soft-ref、~7% 已知現實、**不是錯誤**）｜`off`｜`error`。
    """
    src = source if source in PM_SOURCES else "demo"
    wpm = pm_module()
    if wpm is None:
        return pm_item_unavailable("board_pm_module_missing", idx_row)
    if src == "snapshot":
        return pm_item_unavailable("snapshot_unsupported", idx_row)
    if src == "demo":
        try:
            return wpm.demo_item(pm_id) or pm_item_unavailable("pm_unknown_id")
        except Exception as e:
            return pm_item_unavailable(f"error:{type(e).__name__}")
    # ---- live ----
    if not idx_row:
        return pm_item_unavailable("pm_unknown_id")
    try:
        if not pm_live_enabled():
            return {"pm": idx_row, "steps": {"status": "off", "rows": []},
                    "wo": {"status": "off", "detail": None}, "unavailable": None}
        w8t = pm_transport_module()
        if w8t is None or not w8t.is_configured():
            return {"pm": idx_row, "steps": {"status": "off", "rows": []},
                    "wo": {"status": "off", "detail": None}, "unavailable": None}
        # 細項腿（S2；CM10：`task_id` 的值就是 `task_no`＝一跳直達，無需先查 task）
        task_id = idx_row.get("task_id")
        if not task_id:
            steps = {"status": "off", "rows": []}     # 排程沒給 task 參照＝無從查（不謊稱無細項）
        else:
            rows, reason = w8t.fetch_task_steps(task_id)
            if reason is None:
                # 用料人話名（S-PM6）＝**fail-open 裝飾層**：字典拿不到就顯 item_code、
                # 步驟一列不少（`decorate_steps` 是純函式、值域一個位元不動）。
                inames, istatus = pm_item_names()
                steps = {"status": "ok" if rows else "empty",
                         "rows": wpm.decorate_steps(rows, inames),
                         "item_name_status": istatus}
            elif reason == "http_404":
                steps = {"status": "gone", "rows": []}
            else:
                print(f"[board_data pm/item] steps 腿失敗 task={task_id} "
                      f"reason={reason}", flush=True)
                # ★ reason 必須上頁（模板 `st.reason` 已在等這個鍵）：只進 server log ⇒
                #   工程師看到的是「細項載入失敗：」冒號後一片空白，分不出逾時/5xx/形狀分歧
                #   ——「看不到原因的降級卡等於沒有降級卡」（本頁自己的紀律）。
                steps = {"status": "error", "rows": [], "reason": reason}
        # 單況腿（soft-ref：404 是已知現實、不是錯誤）
        last_wo = idx_row.get("last_wo")
        if last_wo is None:
            wo = {"status": "none", "detail": None}
        else:
            raw, reason = w8t.fetch_pm_wo_detail(last_wo)
            if reason is None and isinstance(raw, dict):
                wo = {"status": "ok", "detail": _pm_wo_detail_view(raw)}
            elif reason is None:
                # 回了非 dict＝形不符、不硬解讀（reason 上頁＝同上，別讓冒號後空白）
                wo = {"status": "error", "detail": None, "reason": "bad_shape"}
            elif reason == "http_404":
                wo = {"status": "gone", "detail": None}
            else:
                print(f"[board_data pm/item] detail 腿失敗 wno={last_wo} "
                      f"reason={reason}", flush=True)
                wo = {"status": "error", "detail": None, "reason": reason}
        return {"pm": idx_row, "steps": steps, "wo": wo, "unavailable": None}
    except Exception as e:
        print(f"[board_data pm/item ERR] {type(e).__name__}: {str(e)[:300]}", flush=True)
        return pm_item_unavailable(f"error:{type(e).__name__}", idx_row)


# ======================================================================================
# A11 dormant（長停）事實換源：`board_dormant.DORMANT_BRIDGE` 常數 → cmms 事件流
#   規格＝`SPEC-DORMANT-CAPACITY.md` §6.1（消費紀律）＋§8（換源存證）。
#
#   ★ 本節是這條線的**唯一 I/O 收口**：投影（as-of、capacity_split、dormant_info…）全部
#     住 `board_dormant`（零 I/O 純函式），本節只做「取事件 → 換成同形 dict → 快取」。
#     ⇒ 換源那天 `board_dormant.py` 一個位元都不必動（這就是換源的存在理由）。
#
#   ★ **閘門預設關**（`BOARD_A11_LIVE`）：未設 ⇒ `(None, "gated", None)`、**零 HTTP**、
#     連 transport 都不碰 ⇒ 全站照走過渡橋常數＝今天的行為逐位不變。啟用＝Chris 在 cmms
#     部署完＋逐筆登記存量後自己設 env 並重啟（比照 `BOARD_PM_LIVE` 的資料可信度閘紀律：
#     這不是「功能開關」，是「這批事實現在可不可信」）。
#
#   ★ **任一 EID 失敗 ⇒ 整批不用**（退回橋常數＋頁面揭露）。理由＝**部分換源＝同一頁上混兩份
#     真相**：M2 讀到 cmms 的復機事件（已排產）、M3 讀不到而沿用橋常數（長停）⇒ 產能分母是
#     1 還是 2 取決於哪一次 HTTP 剛好失敗，而畫面完全正常。整批降級雖然粗，但**永遠自洽**。
# ======================================================================================
def _a11_env_float(name, default):
    """env 讀 float；缺或壞值 → default（**壞值不炸整站**，同 `_pm_env_grace` 的容忍紀律）。"""
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


A11_LIVE_ENV = "BOARD_A11_LIVE"                 # 換源硬閘（**預設關**；Chris 部署後自己開）
A11_SCHEMA_ID = "contract_asset_schedule_state.v1"
A11_TTL = _a11_env_float("BOARD_A11_TTL", 1800.0)          # 30 分（登記事件一天幾次、不必更頻）
A11_TTL_FAIL = _a11_env_float("BOARD_A11_TTL_FAIL", 120.0)  # 冷失敗只黏 2 分鐘（源恢復即恢復）
A11_STALE_MAX = _a11_env_float("BOARD_A11_STALE_MAX", 48 * 3600.0)  # stale-serve 上限 48h
_A11_EVENTS = None            # (mono_ts, [event dict…])：**成功值專用**（失敗不寫）
_A11_FAIL_TS = None           # 上次冷失敗時刻（避免每個 request 都重打一輪 26 次 HTTP）


def a11_live_enabled():
    """換源閘：`BOARD_A11_LIVE` truthy 才放行（**預設關**＝走過渡橋常數）。"""
    return os.environ.get(A11_LIVE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def a11_eids():
    """換源覆蓋面＝**cmms 可能登得出長停事件的全部 EID**（2026-09-03 擴面，見下）。

    ★ **唯一出處原則：一顆 EID 都不在本檔手抄**——四段全部從既有名冊常數組出來，
      抄一份就等於埋一顆「兩邊哪天不一致、而且沒有任何測試會發現」的雷：
        ① 形① nest-EAV **母機**＝`board_capacity.NEST_EAV_UNITS` 的鍵。
           （2026-09-08 深夜 AOI 數台退出子工位名冊＝`SPEC-LANDING.md` §18.30.4／
             **C-LD46**。⚠ 這幾台因此也退出 A11 覆蓋面——覆蓋面本來就是「有座的機台
             ＋基線名單」，沒有座了就與其餘機台一樣不在面上；這是名冊唯一出處的
             **推論結果**，不是另一個裁決。）
           （nest 座位沒有自己的 EID——那正是形①的定義 ⇒ 只能問母機，工位軸由回來的
             事件列上的 `sub_unit` 表達。）
        ② 形② co-tag **母機＋子機**＝`board_capacity.COTAG_CHILD_UNITS`
           的鍵與值（子機是獨立 EID、各自能被登記；母機自己也能被登記）。
        ③ CAL Gen2 子工位＝`board_scope.PARENT_EXPANSION["EID-24742"]`（原有覆蓋面）。
        ④ §11.4.1 基線播種的 EID 級名單＝`board_dormant.A11_BASELINE_EIDS`
           （碼側鏡射常數，正本＝`SPEC-DORMANT-CAPACITY.md` §11.4.1 的 ✅ 表）。
      ⇒ 四段聯集（四段互不重疊；`EID-25270` 退役出名冊、AOI 退座後隨名冊減少；
        selftest 以 `len(a11_eids())` 實測、勿手抄）。**排序穩定**（依上列順序、段內依名冊順序），
        方便 selftest 與日誌比對。

    ★ **為什麼 2026-09-03 擴面**（Chris 裁決：「cmms 側能提供的正確機器狀態資訊，Board 也要
      能接到」）：舊覆蓋面只有 ③ 四顆 ⇒ cmms 已登的 11 筆裡有 **7 筆 EID 級 + 4 筆工位級**
      完全問不到，「登了卻進不了投影」＝Board 這側把對的事實擋在門外。分母因此改變是**正確
      結果、不是副作用**。

    ★ **母機（`EID-24742`／`EID-16077`／`EID-27600`）刻意仍不入列**：A10 的展開母機沒有
      自身稼動數據、也不會被登記長停（§6.1 判定語意 ③「不做母子傳遞」＋`board_scope` ⑦
      「不加母機列」）。`EID-27617`（SERIAL_UNITS）同樣永不入列＝不在 `EQMT_CATALOG`。
      ⚠ 形② 的母機（`EID-08158`／`EID-25402`）是**另一回事**：它們在名冊裡、有自身段，
      入列。

    ⚠ **代價（接線時已知、寫在這裡免得日後被當成新病）**：`_a11_fetch_all` 是 per-EID
      迴圈 ⇒ 一次冷取數＝**26 次 HTTP**（舊 4 次），而且**任一失敗即整批 None**。
      TTL 30 分鐘吸收頻率、stale-serve 48h 吸收抖動；但 cmms 端若不穩，整批退橋的機率
      會比 4 顆時高。真的擋到再談批次化（cmms 的批次讀只答「現在」、答不了 as-of 歷史
      ⇒ 不能拿來頂替這條腿，見 §6.1「兩條讀取路徑分工」）。
    """
    import board_capacity as _wc
    import board_dormant as _wdorm
    import board_scope as _ws
    out = []
    for eid in list(_wc.NEST_EAV_UNITS):                       # ① 形① 母機 6（原 9）
        out.append(eid)
    for parent, children in _wc.COTAG_CHILD_UNITS.items():     # ② 形② 母機 2 ＋ 子機 4
        out.append(parent)
        out.extend(children)
    out.extend(_ws.PARENT_EXPANSION["EID-55232"])              # ③ CAL Gen2 四工位
    out.extend(_wdorm.A11_BASELINE_EIDS)                       # ④ §11.4.1 EID 級 7
    seen, uniq = set(), []
    for eid in out:                                            # 去重但保序（重疊＝名冊改了，不炸）
        if eid not in seen:
            seen.add(eid)
            uniq.append(eid)
    return uniq


def _a11_adapt(eid, row):
    """cmms 事件列 → `board_dormant` 事件形 dict（`events_for` 收 dict 形）。

    ★ `ts_effective` 餵的是 **`effective_date`**（台北日曆日字串），不是 wire 的
      `ts_effective`（UTC Z）。`board_dormant` 的欄名沿用歷史（橋常數時期它本來就是
      台北日曆日）——**全鏈零時區換算**：cmms 已經把台北日曆日算好放在 `effective_date`，
      我們只搬運。拿 UTC 字串前 10 碼頂替＝整段長停區間左移一日（§6.1 明令禁止）。
    ★ `reason` 是**自由文字、不鑄 vocab**（SPEC §6）⇒ 中英兩欄塞同一份原文、逐字顯示、
      不做語意分類、不據以分色。

    ★★ **2026-09-03 Chris 裁：接線。** 原話＝
      **「cmms 側能提供的正確機器狀態資訊，Board 也要能接到」**
      ⇒ 本函式搬 `sub_unit`（缺席／null ＝ `None` ＝**整台機**，即 A13 契約 ■二① 的缺席語意）。
      **分母改變是正確結果、不是副作用**（`EID-26596`、`26718`、`12724` 的產能分母
      各少了被登記長停的座；存證＝`SPEC-DORMANT-CAPACITY.md` §11.6 的 ✅ 裁決段）。
      同輪一併把 `a11_eids()` 的覆蓋面從 CAL 四工位擴到整份 A11 名冊——**兩半要一起翻才有效**：
      只搬欄位而不擴覆蓋面，那 4 筆工位級事件仍在 `26596`／`26718`／`12724` 上、根本問不到。

    ⚠ **兩條 lane 的分工（接線後最重要的一條，別自己再造第三個過濾點）**：
      · `sub_unit is None` ＝整機 lane ⇒ 餵 `board_dormant`（`is_dormant`／`dormant_info`／
        `dormant_set`／`capacity_split`）。
      · `sub_unit` 非 None ＝工位 lane ⇒ 只給 `board_capacity._nest_effective_states`。
      **`board_dormant.events_for` 已在匯流口把工位 lane 整列濾掉**（2026-09-03 加的
      結構性防呆）⇒ 呼叫端（`app.py` `/equipment`、`board_scope` clip、`board_eqmeeting`、
      `board_export`）**照舊把整份事件流原樣傳下去即可，一個位元都不必改**。
      ⚠ 別在呼叫端各自加 `sub_unit is None` 過濾——那就是五份會漂移的規則。
    """
    return {"eid": eid,
            "ts_effective": row["effective_date"],
            "state": row.get("state"),
            "sub_unit": row.get("sub_unit"),      # None＝整台機（缺席語意，A13 契約 ■二①）
            "reason_zh": row.get("reason"), "reason_en": row.get("reason"),
            "source": "cmms"}


def _a11_fetch_all():
    """`a11_eids()` 逐顆各打一次 → (events, None) | (None, reason)。**任一失敗即整批 None。**

    ⚠ 2026-09-03 擴面後＝**26 次 HTTP**（舊 4 次），TTL 30 分鐘吸收頻率；理由與代價
    見 `a11_eids()` docstring 末段。整批降級的紀律不變（部分換源＝同頁混兩份真相）。"""
    w8t = pm_transport_module()          # 同一支 lazy import（不另開第二條 transport 路徑）
    if w8t is None:
        return (None, "not_wired")
    fn = getattr(w8t, "fetch_schedule_state_events", None)
    if fn is None:
        return (None, "not_wired")       # transport 沒有這支＝誠實降級，不是例外
    if not w8t.is_configured():
        return (None, "not_configured")
    try:
        eids = a11_eids()
    except Exception as e:               # noqa: BLE001（名冊拿不到＝不知道要問誰 ⇒ 退橋）
        print(f"[board_data a11] 名冊不可得（{type(e).__name__}）→ 退回過渡橋", flush=True)
        return (None, "no_roster")
    out = []
    for eid in eids:
        res, reason = fn(eid)
        if reason is not None:
            return (None, reason)        # ★ 整批不用（部分換源＝同頁混兩份真相）
        for row in (res.get("rows") or []):
            out.append(_a11_adapt(eid, row))
    return (out, None)


def get_dormant_events(force=False):
    """A11 長停事實 → `(events | None, status, age_s)`。**恆不 raise。**

    · `events=None` ⇒ 呼叫端一律走 `board_dormant` 的預設（過渡橋常數）＝今天的行為。
    · status ∈ `gated`（閘未開、**零 HTTP**）｜`live`｜`live_stale`（來源暫掛、供舊值＋age）｜
      `error:<reason>`（冷失敗、無舊值可供）。頁面據此顯示來源短註（C-D3「絕不靜默」）。
    · `age_s`＝這批事件距離取回當下的秒數（`live` 也可能有幾百秒＝TTL 內；`None`＝無資料）。
    """
    global _A11_EVENTS, _A11_FAIL_TS
    if not a11_live_enabled():
        return (None, "gated", None)                  # ★ 短路：連 transport 都不碰
    now = time.monotonic()
    hit = _A11_EVENTS
    if hit and not force and (now - hit[0]) < A11_TTL:
        return (hit[1], "live", now - hit[0])
    # 冷失敗抑制：剛失敗過就別讓每個 request 都重打 26 次 HTTP（有舊值時照樣先試重取）
    if (not force and hit is None and _A11_FAIL_TS is not None
            and (now - _A11_FAIL_TS) < A11_TTL_FAIL):
        return (None, "error:recent_fail", None)
    try:
        events, reason = _a11_fetch_all()
    except Exception as e:                            # noqa: BLE001（換源腿永不炸頁面）
        events, reason = None, f"error_{type(e).__name__}"
        print(f"[board_data a11 ERR] {type(e).__name__}: {str(e)[:200]}", flush=True)
    if reason is None:
        _A11_EVENTS, _A11_FAIL_TS = (now, events), None
        print(f"[board_data a11] events={len(events)} eids={len(a11_eids())} "
              f"status=live", flush=True)
        return (events, "live", 0.0)
    if hit is not None and (now - hit[0]) < A11_STALE_MAX:
        print(f"[board_data a11] 取回失敗 reason={reason} → **供舊值** "
              f"age={(now - hit[0]) / 60:.0f}min（stale-serve 上限 {A11_STALE_MAX / 3600:.0f}h）",
              flush=True)
        return (hit[1], "live_stale", now - hit[0])
    _A11_FAIL_TS = now
    print(f"[board_data a11] 取回失敗 reason={reason}、無可用舊值 → **退回過渡橋常數**"
          f"（頁面揭露來源）", flush=True)
    return (None, f"error:{reason}", None)


def peek_dormant_events():
    """**只窺既有快取、絕不觸發取數** → `(events, status, age_s)`｜未命中 `(None, None, None)`。

    ★ 這支是給 **Hermes 頁面上下文組裝**（`app._assist_ctx_block`）用的，紀律與既有五個
      `_peek_*` 完全相同：**零 HTTP、零 transport 觸碰、零副作用、非阻塞**。
      ctx 組裝掛在使用者送出訊息的關鍵路徑上——若它會觸發一輪 26 次 cmms 取數，
      使用者按下送出就得等網路（而且是為了一段「可有可無的摘要」）。
      **拿不到就誠實省略那一層**，這是本 repo 對 ctx 組裝的承重紀律，不是最佳化。
    · 閘未開 ⇒ `(None, None, None)`（與板面路徑的 `gated` 同義：走橋常數）。
    · 快取超過 `A11_STALE_MAX` ⇒ 視同未命中（陳到某個程度就不再是事實，同 stale-serve 上限）。
    · **絕不寫快取、絕不動 `_A11_FAIL_TS`**——peek 不留痕跡，板面路徑的取數節奏不受它影響。
    """
    if not a11_live_enabled():
        return (None, None, None)
    hit = _A11_EVENTS
    if hit is None:
        return (None, None, None)
    age = time.monotonic() - hit[0]
    if age >= A11_STALE_MAX:
        return (None, None, None)
    return (hit[1], "live" if age < A11_TTL else "live_stale", age)


# ======================================================================================
# A11 批次現況讀（板面著色的第二條腿；規格＝`SPEC-DORMANT-CAPACITY.md` §6.2）
#
#   ★ **兩條讀取路徑分工（§6.1／§6.2，別混用）**：
#       ① **批次答「誰」**＝本節（`GET /assets/schedule-state`）：一次問完整份名冊的
#          **現況**，供 `/equipment` 板面著色（整份名冊）。
#       ② **per-EID 答「何時」**＝上一節的事件流：as-of 歷史（產能分母逐日切換、
#          A10 clip 構造期扣 dormant 日）。覆蓋面＝`a11_eids()`（**2026-09-03 起**：
#          形① 母機＋形② 母機/子機＋CAL 四工位＋§11.4.1 基線；舊值只有 CAL 工位）。
#     ⇒ **絕不得拿本節的「現況」去塗歷史窗**（heatmap 是逐日 as-of；拿今天的結論回頭套
#       歷史正是 C-D4 明文禁止的事）。本節的輸出只餵**顯示態**（Now 欄 ◌／A% 「—」／窄橫列）。
#
#   ★ 紀律沿用上一節逐條（同一批事實、同一種降級語意）：閘＝同一個 `BOARD_A11_LIVE`
#     （閘關＝零 HTTP）／TTL 30 分／來源暫掛 stale-serve 至 48h／冷失敗 2 分鐘抑制窗／
#     **恆不 raise**。差別只有一個：本節**一次呼叫問完整份名冊**（transport 內部切塊），
#     不是 per-EID 迴圈。
#
#   ★ `unknown[]` 是錯誤不是雜訊（cmms NOTE-34【三】硬紀律）：非空＝我方送的 EID 在 cmms
#     主檔查無 ⇒ **兩邊名冊漂了**。本層**原樣往上傳**，由 app／模板在頁上紅字報出來；
#     **不吞、不轉成整批失敗**（`items` 對已知那些台仍然有效）。
# ======================================================================================
A11_BATCH_SCHEMA_ID = "contract_asset_schedule_state_batch.v1"
_A11_BATCH = None         # (mono_ts, key, (states_by_eid, unknown))：**成功值專用**（失敗不寫）
_A11_BATCH_FAIL_TS = None  # 上次冷失敗時刻（冷失敗抑制窗，同事件流那條）


def _a11_batch_fetch(eids, as_of):
    """打一次批次讀 → `((states_by_eid, unknown), None)` | `(None, reason)`。

    `states_by_eid`＝**dict 查表**（`{eid: {"state", "since", "reason"}}`）——
      ★ **刻意不依賴 cmms 的 EID 升冪保證**（§6.2 Board 側自定第二條）：把便利當契約＝
        日後對方一個良性的排序改動就讓板面死掉。
    """
    w8t = pm_transport_module()          # 同一支 lazy import（不另開第二條 transport 路徑）
    if w8t is None:
        return (None, "not_wired")
    fn = getattr(w8t, "fetch_schedule_state_batch", None)
    if fn is None:
        return (None, "not_wired")       # transport 沒有這支＝誠實降級，不是例外
    if not w8t.is_configured():
        return (None, "not_configured")
    res, reason = fn(list(eids), as_of=as_of)
    if reason is not None:
        return (None, reason)
    states = {}
    for row in (res.get("items") or []):
        aid = row.get("asset_id")
        if not aid:                      # transport 已 pin 過（空鍵＝bad_schema），此處純防呆
            continue
        states[str(aid)] = {"state": row.get("state"), "since": row.get("since"),
                            "reason": row.get("reason")}
    return ((states, list(res.get("unknown") or [])), None)


def get_schedule_state(eids, as_of=None, force=False):
    """A11 批次現況 → `(states_by_eid | None, unknown | None, status, age_s)`。**恆不 raise。**

    · `states_by_eid=None` ⇒ 呼叫端**退回既有覆蓋面**（＝事件流那條腿）＝今天的行為逐位不變。
    · status ∈ `gated`（閘未開、**零 HTTP**）｜`live`｜`live_stale`（來源暫掛、供舊值＋age）｜
      `error:<reason>`（含 `error:empty_eids`）。
    · ★ **空名冊 ⇒ `error:empty_eids`、零網路、連 transport 都不碰**——
      呼叫端的名冊若因故載不出來就會遞空 list 進來，而「安靜地改問 cmms 全部資產」與呼叫端想要的
      東西毫無關係、卻長得完全正常。transport 那層也有同名短路（雙保險）；在這裡再擋一次是
      為了**連 lazy import 都不觸發**，且日誌能指認是誰遞了空名冊。
    · 快取鍵含 `(名冊, as_of)`：名冊或 as-of 一變即重取（否則切窗會拿到別人的答案）。
    """
    global _A11_BATCH, _A11_BATCH_FAIL_TS
    if not a11_live_enabled():
        return (None, None, "gated", None)            # ★ 短路：連 transport 都不碰
    want = [str(e) for e in (eids or []) if e]
    if not want:
        print("[board_data a11-batch] 名冊為空 → empty_eids（零網路；**不退化成問全機隊**）",
              flush=True)
        return (None, None, "error:empty_eids", None)
    key = (tuple(want), as_of)
    now = time.monotonic()
    hit = _A11_BATCH
    if hit and not force and hit[1] == key and (now - hit[0]) < A11_TTL:
        return (hit[2][0], hit[2][1], "live", now - hit[0])
    stale = hit if (hit and hit[1] == key) else None   # 名冊變了 ⇒ 舊值不可用於 stale-serve
    if (not force and stale is None and _A11_BATCH_FAIL_TS is not None
            and (now - _A11_BATCH_FAIL_TS) < A11_TTL_FAIL):
        return (None, None, "error:recent_fail", None)
    try:
        got, reason = _a11_batch_fetch(want, as_of)
    except Exception as e:                            # noqa: BLE001（著色腿永不炸頁面）
        got, reason = None, f"error_{type(e).__name__}"
        print(f"[board_data a11-batch ERR] {type(e).__name__}: {str(e)[:200]}", flush=True)
    if reason is None:
        _A11_BATCH, _A11_BATCH_FAIL_TS = (now, key, got), None
        print(f"[board_data a11-batch] items={len(got[0])} unknown={len(got[1])} "
              f"eids={len(want)} status=live", flush=True)
        return (got[0], got[1], "live", 0.0)
    if stale is not None and (now - stale[0]) < A11_STALE_MAX:
        print(f"[board_data a11-batch] 取回失敗 reason={reason} → **供舊值** "
              f"age={(now - stale[0]) / 60:.0f}min", flush=True)
        return (stale[2][0], stale[2][1], "live_stale", now - stale[0])
    _A11_BATCH_FAIL_TS = now
    print(f"[board_data a11-batch] 取回失敗 reason={reason}、無可用舊值 → "
          f"**退回既有覆蓋面**（頁面照舊）", flush=True)
    return (None, None, f"error:{reason}", None)


def schedule_state_dormant(states_by_eid):
    """批次現況 → `{eid: {"since", "reason_zh", "reason_en"}}`（**只收 `not_scheduled` 那些**）。

    形狀刻意對齊 `board_dormant.dormant_info` 的輸出＝呼叫端（`build_board(dormant_by_eid=)`）
    收哪一條腿來的都長一樣、模板零分叉。
    ★ **先讀 `state`，再讀 `since`**（§6.2 消費紀律③）：`since` 是「現況自哪天起」，
      復機那筆的 `since` 是**復機日**——把它一律讀成「長停自」會讓一台在產的機器顯成長停。
    ★ `reason` 是**不可信任的自由文字**（v1 不鑄 vocab）：中英兩欄塞同一份原文、逐字顯示、
      不做語意分類、不據以分色（同事件流那條 `_a11_adapt`）。
    """
    import board_dormant as _wdorm     # 值域從投影層 import，**不在本檔抄第二份字面值**
    out = {}
    for eid, st in (states_by_eid or {}).items():
        if not isinstance(st, dict) or st.get("state") != _wdorm.STATE_NOT_SCHEDULED:
            continue
        out[eid] = {"since": st.get("since"),
                    "reason_zh": st.get("reason"), "reason_en": st.get("reason")}
    return out


# ---- SALTMARSH 週報復刻頁（`board.saltmarshweekly.v1`；`/saltmarsh/weekly`；internal-file）-------------
#   **本節是這條線唯一的檔案 I/O 收口**：讀凍結 fixture → `board_saltmarshweekly` 純函式組頁。
#   · v1 的每一個顯示值都來自 fixture（`page["values_from"] == "fixture"`、`live_wired False`）
#     ——SPEC §4 的 🟢* 規則：對表通過前不得把 Board 值印上去。tier 講「將來會不會自己更新」、
#     state 講「今天顯示的是什麼」，兩者分開才不會把承諾講成現況。
#   · 因此 `source` 目前**不影響任何數字**（demo/live/snapshot 同一份凍結頁），只透傳給頁面
#     標籤與導覽。§12 step 5 接 live 那天，分歧點在這個函式裡、不在 app、更不在模板。
#   · fixture 換版＝**換檔名不覆蓋**（SPEC §10）；`board_saltmarshweekly.DATA_DATE` 與檔名前八碼
#     不一致時 `parse_fixture` 會 raise ⇒ 這裡轉成誠實降級卡，不讓整頁 500。
CW_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures",
                          "saltmarsh_weekly_20260803.v1.json")
_CW = None
_CW_TRIED = False
_CW_DOC = None                     # 模組級 lazy cache：解析過的 fixture（凍結檔＝讀一次就夠）


def saltmarshweekly_module():
    """lazy import `board_saltmarshweekly`（未就緒 → None ＝ 誠實降級，同 pm_module 慣例）。"""
    global _CW, _CW_TRIED
    if not _CW_TRIED:
        _CW_TRIED = True
        try:
            import board_saltmarshweekly as _m
            _CW = _m
        except Exception as e:      # noqa: BLE001（引擎缺席不得炸整站）
            print(f"[board_data] board_saltmarshweekly unavailable "
                  f"({type(e).__name__}: {str(e)[:120]}) → /saltmarsh/weekly 誠實降級", flush=True)
            _CW = None
    return _CW


def saltmarsh_weekly_unavailable(reason, source=None):
    """誠實降級形：骨架 ＋ `unavailable` 原因。**絕不合成、絕不拿別週的 fixture 頂替**
       （SPEC §9：把「讀不到」畫成「那天沒有資料」是這一頁最糟的一種假話）。"""
    return {"schema": "board.saltmarshweekly.v1", "source": source, "unavailable": reason,
            "data_date": None, "deck_title": None,
            # 彙總與記號一律 None／空：**沒有數字就不畫數字**（頁頂細條與圖例面板整條不出現）。
            "tier_counts": None, "mark_counts": None,
            "tier_badges": [], "mark_badges": [], "n_pending": 0,
            "n_sections": 0, "n_elements": 0, "sections": [], "nav": [],
            "slide_order": [], "excluded_slides": {},
            "source_pptx": None, "source_md5": None,
            "values_from": "fixture", "live_wired": False}


def read_saltmarsh_weekly_fixture(force=False):
    """讀 ＋ `parse_fixture` 凍結週報 fixture（模組級 cache）。**唯一開這個檔的地方。**
       失敗 ⇒ raise（呼叫端 `get_saltmarsh_weekly` 轉成降級卡）——這裡不吞例外，
       否則「fixture 壞了」與「fixture 是空的」在上層就分不出來。"""
    global _CW_DOC
    if _CW_DOC is not None and not force:
        return _CW_DOC
    cw = saltmarshweekly_module()
    if cw is None:
        raise RuntimeError("board_saltmarshweekly not importable")
    with open(CW_FIXTURE, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    _CW_DOC = cw.parse_fixture(doc)
    return _CW_DOC


def get_saltmarsh_weekly(source="demo"):
    """`/saltmarsh/weekly` 的 canonical 頁模型。**恆不 raise**：任何失敗 → `saltmarsh_weekly_unavailable`。

    回傳＝`board_saltmarshweekly.build_page()` 的輸出（形狀見該模組 docstring）。
    """
    try:
        cw = saltmarshweekly_module()
        if cw is None:
            return saltmarsh_weekly_unavailable("engine_missing", source)
        page = cw.build_page(read_saltmarsh_weekly_fixture(), source=source)
        if not isinstance(page, dict) or not page.get("sections"):
            return saltmarsh_weekly_unavailable("bad_shape", source)
        page.setdefault("unavailable", None)
        return page
    except FileNotFoundError:
        print(f"[board_data saltmarsh/weekly] fixture 不存在：{CW_FIXTURE}", flush=True)
        return saltmarsh_weekly_unavailable("fixture_missing", source)
    except Exception as e:          # noqa: BLE001（頁面永遠要能回，降級卡帶原因）
        print(f"[board_data saltmarsh/weekly ERR] {type(e).__name__}: {str(e)[:300]}", flush=True)
        return saltmarsh_weekly_unavailable(f"error:{type(e).__name__}", source)


# ---- 單站下鑽（station→machine；§26.2 T2/T3、§26.10 機台主單位 + 缺陷歸模組） ----
def get_yield_station_live(cls, days=4, with_machines=True):
    """連 MES 單站（含機台級下鑽；重用凍結 station_yield(with_machines=True)）。
       with_machines=False＝只站級（raw+product+fact_segment，**不抓 fleet/機台**）＝站頁
       overview 頭卡輕量路徑（省一次 fetch_fleet + machine yield，僅需 output/quality headline）。"""
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC   # 逾時上限：大窗 query 不致無限掛住（逾時→該段 unavailable、頁仍回）
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        rec = my.station_yield(cur, cls, days, with_machines=with_machines)
        if with_machines:
            _expand_gen2_yield_live(cur, cls, days, rec)   # detail-only：Gen2 父機 → 真實工位（站級總數不動）
    finally:
        conn.close()
    return {"source": "live", "mes_now": now, "days": days, "caliber": my.CALIBER, "station": rec}


def get_yield_machines_from_fleet(source, cls, days, fleet):
    """單站頁 units 區塊：由【已知 fleet】(timeline 已抓回＝免再 fetch_fleet，去重重複的三方 join)
       直接算機台級 yield。重用凍結 leaf：fetch_machine_yield + build_machine_records + Gen2 工位展開。
       回 [machine records]；非 live 或無 fleet → None（呼叫端 fallback 既有 get_yield_station 完整路徑）。"""
    if _synth_proc():                       # §5 wd:5642（N；M4 第二期）
        raise _wsio().not_provided("yield_machines")
    if source != "live" or not fleet:
        return None
    conn, cur = mt._connect()
    conn.timeout = LIVE_QUERY_TIMEOUT_SEC
    try:
        myield = my.fetch_machine_yield(cur, [e for e, _ in fleet], days, cls)
        rec = {"machines": my.build_machine_records(fleet, myield)}
        _expand_gen2_yield_live(cur, cls, days, rec)       # detail-only：Gen2 父機 → 真實工位
    finally:
        conn.close()
    return rec["machines"]


def get_yield_station_snapshot(path, cls, days=4):
    """載 `mes_yield --station <cls> --json`（單站含機台）。cls 不符時標記、由呈現層誠實提示。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rec = data.get("station")
    return {"source": "snapshot", "mes_now": data.get("mes_now"),
            "days": data.get("days", days), "caliber": data.get("caliber") or YIELD_CALIBER,
            "station": rec, "cls_mismatch": bool(rec and rec.get("cls") != cls)}


def _demo_yield_station(cls, days=4):
    now = datetime.now().replace(microsecond=0)
    i = mt.EQP_STATIONS.index(cls) if cls in mt.EQP_STATIONS else 0
    return {"source": "demo", "mes_now": now, "days": days, "caliber": my.CALIBER,
            "station": _demo_station_rec(cls, i, with_machines=True)}


def get_yield_station(source="demo", cls=None, days=4, snapshot_path=None, with_machines=True):
    """單站 OUTPUT+YIELD（含機台下鑽）。同三源語意。
       with_machines=False（live）＝只站級 headline、不抓機台（站頁 overview 頭卡輕量路徑）。
       demo/snapshot 一律回完整（合成/讀檔本就便宜，machines 未用即無妨）。"""
    cls = cls or YIELD_ANCHOR_CLS
    if _synth_proc():                       # §5 wd:5677（N；M4 第二期）
        raise _wsio().not_provided("yield_station")
    if source == "live":
        return get_yield_station_live(cls, days, with_machines=with_machines)
    if source == "snapshot":
        if not snapshot_path:
            raise ValueError("source=snapshot 需提供 yield station snapshot_path")
        return get_yield_station_snapshot(snapshot_path, cls, days)
    return _demo_yield_station(cls, days)


# ======================================================================================
# --selftest：W8 工單態 overlay 純函式離線自驗（零 MES、deterministic；不 import app）
# ======================================================================================
def _selftest_overlay():
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # Production-caliber seam: the sibling query may append exactly one SELECT column;
    # fallback is byte-for-byte the frozen query and parser never accepts v3 as v4.
    ck(FETCH_SEGMENTS_DESIG_SQL.replace(", se.matDesignation", "") == FROZEN_FETCH_SEGMENTS_SQL,
       "MES designation SQL diff：only `, se.matDesignation` is appended to frozen SELECT")
    # ⚠ 這一根**必須**去讀 `mes_timeline.fetch_segments` 的**真原始碼**。
    #   codex 初稿是拿本檔裡另一份手打的字面量比對＝拿自己的副本比自己的副本（循環釘）：
    #   frozen 模組哪天真被改了，這根仍然全綠。改成 `inspect.getsource` 抽出來比 ⇒
    #   frozen 那條一動，本檔的 sibling 立刻紅。
    import inspect as _inspect
    _frozen_src = _inspect.getsource(mt.fetch_segments)
    ck(FROZEN_FETCH_SEGMENTS_SQL in _frozen_src,
       "fallback SQL 逐字取自 mes_timeline.fetch_segments 的真原始碼（非本檔的第二份手打副本）")
    ck(_frozen_src.count("cur.execute(\"\"\"") == 1
       and _frozen_src.split("cur.execute(\"\"\"")[1].split("\"\"\"")[0] == FROZEN_FETCH_SEGMENTS_SQL,
       "frozen fetch_segments 只送一條 query，且就是這一條（多一條＝這根抓不到、必須有人來改）")

    # ---- #123 站別折疊（§10.8-10）：sibling 也要逐別名點查，否則 /updown・/equipment 的
    #      recency 對那五台仍是「窗內無段」。**站別表不抄第二份**＝直接用 mt.classes_for。
    class _ClsCur:
        """假 cursor：依 execute 的第一個位置參數（站別）回列；`scans` ＝執行證據。"""

        def __init__(self, by_cls):
            self.by_cls, self.scans, self._last = dict(by_cls), [], []

        def execute(self, sql, *args):
            self.scans.append(args[0] if args else None)
            self._last = list(self.by_cls.get(args[0] if args else None, []))

        def fetchall(self):
            return self._last

    _t0 = datetime(2026, 9, 1, 8, 0, 0)
    _t1 = datetime(2026, 9, 1, 9, 0, 0)
    _t2 = datetime(2026, 9, 1, 10, 0, 0)
    _t3 = datetime(2026, 9, 1, 11, 0, 0)
    _cc = _ClsCur({"segSealDispense": [("EID-25749", _t0, _t1, "Production")],
                   "segSealDispenseDamAndFill": [("EID-25749", _t2, _t3, "Production"),
                                                  ("EID-99999", _t0, _t1, "Production")],
                   "segGrout": [("EID-26190", _t0, _t1, "Production")]})
    _cs = fetch_segments_desig(_cc, ["EID-25749", "EID-26190"], 30, "segSealDispense")
    ck(_cc.scans == ["segSealDispense", "segSealDispenseDamAndFill", "segGrout"],
       "★ #123: sibling 對 Seal 逐別名各送一次（順序＝mt.classes_for，主站在前）")
    ck(_cs["EID-25749"] == [(_t0, _t1, "Production"), (_t2, _t3, "Production")]
       and _cs["EID-26190"] == [(_t0, _t1, "Production")] and "EID-99999" not in _cs,
       "★★ #123: Kestrel 段掛回同一台機、fleet 外的 EID 照舊丟棄")
    _cc1 = _ClsCur({"segDiePlacement": [("EID-00425", _t0, _t1, "Production")]})
    _cs1 = fetch_segments_desig(_cc1, ["EID-00425"], 30, "segDiePlacement")
    ck(_cc1.scans == ["segDiePlacement"]
       and _cs1 == {"EID-00425": [(_t0, _t1, "Production")]},
       "★ #123: 無別名的 37 站只送一次、結果逐位元原行為")
    ck("mt.classes_for(cls)" in _inspect.getsource(fetch_segments_desig)
       and "CLASS_ALIASES" not in _inspect.getsource(fetch_segments_desig),
       "★★ 站別展開走 mt.classes_for（本檔零第二份折疊表；抄一份＝兩邊各改一半的靜默錯）")

    _qnow = datetime(2026, 9, 7, 12, 0, 0)
    _qivs = [(_qnow - timedelta(hours=4), _qnow - timedelta(hours=3), "Production"),
             (_qnow - timedelta(hours=2), _qnow - timedelta(hours=1), "Engineering (R&D)")]
    _qe = _mes_recency_evidence(_qivs, _qnow, True)
    ck(_qe["mes_last_h"] == 3.0 and _qe["mes_any_last_h"] == 1.0
       and _qe["mes_last_designation"] == "Engineering (R&D)",
       "newer R&D keeps older Production as mes_last_h and exposes any-segment evidence")
    ck(_mes_recency_evidence(_qivs, _qnow, False)["mes_last_h"] == 1.0,
       "designation unavailable ⇒ production-grade field safely falls back to prior generic behavior")
    from datetime import timezone
    _pnow = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    _pdoc = {"schema": "board.lastprod.state.v4", "updated_at": "2026-09-07T11:00:00Z",
             "covered_from": "2026-08-01T00:00:00Z", "lookback_days": 30,
             "prod_designation_available": True,
             "eids": {"EID-00950": {"end": "2026-09-07T11:00:00Z", "start": "2026-09-07T10:50:00Z",
                                        "designation": "Engineering (R&D)", "result": "PASS",
                                        "prod_end": "2026-09-07T09:00:00Z", "prod_start": "2026-09-07T08:50:00Z",
                                        "prod_designation": "Production", "prod_result": "PASS"}}}
    _pa, _pm = parse_lastprod_state(_pdoc, _pnow, 7200)
    ck(_pa["EID-00950"] == 3.0 and _pm["any_age_by_eid"]["EID-00950"] == 1.0
       and _pm["prod_by_eid"]["EID-00950"]["prod_designation"] == "Production",
       "v4 parser exposes prod_* while generic latest segment remains visible")
    ck(parse_lastprod_state(dict(_pdoc, schema="board.lastprod.state.v3"), _pnow, 7200)[0] is None,
       "v3 state is rejected whole; deploy requires full v4 rebuild")
    # ★ v5 `sub_units`（`SPEC-LANDING.md` §18.23）：加性鍵、v4 仍收（空窗期不退回探測）。
    ck(_pm["sub_unit_ages_by_eid"] == {},
       "v4 state carries no sub_units ⇒ empty map, which the landing renders hollow (C-LD2)")
    _v5 = dict(_pdoc, schema="board.lastprod.state.v5",
               sub_units={"EID-12724": {"nest:1": "2026-09-07T11:00:00Z",
                                        "nest:2": "2026-09-07T06:00:00Z",
                                        "nest:3": "not-a-time"},
                          "EID-08158": {"EID-05707": "2026-09-07T11:30:00Z"},
                          "EID-BAD": "not-a-dict"})
    _v5a, _v5m = parse_lastprod_state(_v5, _pnow, 7200)
    ck(_v5a["EID-00950"] == 3.0 and _v5m["prod_designation_available"] is True,
       "v5 state is accepted and its eids semantics are bit-for-bit the v4 ones")
    ck(_v5m["sub_unit_ages_by_eid"] == {"EID-12724": {"nest:1": 1.0, "nest:2": 6.0},
                                        "EID-08158": {"EID-05707": 0.5}},
       "v5 sub_unit ages use the same now-minus-end ruler; bad rows are skipped, never guessed")
    # ★ v6 `station_lot`（#104(b)、`SPEC-LANDING.md` §18.26）：同樣是加性鍵，v4/v5 仍收。
    ck(_pm["station_lot"] == {} and _v5m["station_lot"] == {}
       and _pm["station_lot_err"] is None,
       "v4/v5 state carries no station_lot ⇒ empty map ⇒ the landing infers nothing (C-LD2)")
    _v6 = dict(_v5, schema="board.lastprod.state.v6",
               station_lot={"segDieArrayTaping": {"last_prod_end": "2026-09-07T11:30:00Z",
                                                  "asof": "2026-09-07T11:55:00Z"},
                            "segWireBondingPullTest": {"last_prod_end": None,
                                                    "asof": "2026-09-07T11:55:00Z"},
                            "segPreCureVisualInspection": {"last_prod_end": "bad",
                                                           "asof": "2026-09-07T11:55:00Z"},
                            "segNoAsof": {"last_prod_end": "2026-09-07T11:30:00Z"},
                            "segBad": "not-a-dict"},
               station_lot_err=None)
    _v6a, _v6m = parse_lastprod_state(_v6, _pnow, 7200)
    ck(_v6a["EID-00950"] == 3.0
       and _v6m["sub_unit_ages_by_eid"] == _v5m["sub_unit_ages_by_eid"],
       "v6 state is accepted and its eids / sub_units semantics stay bit-for-bit the v5 ones")
    ck(sorted(_v6m["station_lot"]) == ["segDieArrayTaping", "segPreCureVisualInspection",
                                       "segWireBondingPullTest"]
       and _v6m["station_lot"]["segDieArrayTaping"]["age_h"] == 0.5
       and _v6m["station_lot"]["segWireBondingPullTest"]["age_h"] is None
       and _v6m["station_lot"]["segPreCureVisualInspection"]["age_h"] is None
       and _v6m["station_lot"]["segDieArrayTaping"]["asof_age_h"] < 0.1,
       "v6 station_lot: same now-minus-end ruler as ages; a scanned-but-empty station keeps its "
       "key with age None (absence is the only way to say 'never asked'); a row without asof is "
       "dropped whole, a bad last_prod_end only collapses to None; non-dict rows are skipped")

    D = datetime
    ws, we = D(2026, 6, 10, 0, 0), D(2026, 6, 10, 12, 0)

    def rec(state, s, e, conf="high", note=None):
        r = {"eid": "X", "state": state, "conf": conf, "start": s, "end": e,
             "sec": (e - s).total_seconds()}
        if note:
            r["note"] = note
        return r

    def mk(eid, recs):
        return {"eid": eid, "desc": "d", "records": recs, "n_seg": len(recs),
                "micro_idle_sec": 0.0, "agg": aggregate(recs), "_a": None}

    def mig(wno, eid, wt, a, b):
        return {"work_order_no": wno, "asset_id": eid, "work_type": wt,
                "opened_at": a, "closed_at": b, "status_history": []}

    # 各機台一致底盤：生產 00–03 · 不明 03–09 · 生產 09–12
    def base_recs():
        return [rec("生產", D(2026, 6, 10, 0), D(2026, 6, 10, 3)),
                rec("不明", D(2026, 6, 10, 3), D(2026, 6, 10, 9), conf="low"),
                rec("生產", D(2026, 6, 10, 9), D(2026, 6, 10, 12))]

    machines = [
        mk("M1", base_recs()),   # case1：單工單落不明中段 → 3 段
        mk("M2", base_recs()),   # case2：工單跨生產＋不明 → 生產不覆蓋、只上色不明部分
        mk("M3", base_recs()),   # case3：uptime-hold → 不上色
        mk("M4", base_recs()),   # case4：同機重疊兩工單 → union 不雙計
        mk("M5", base_recs()),   # case5：工單落窗外 → 無效
        mk("M6", base_recs()),   # case6：無工單 → 不變
    ]
    data = {"source": "demo", "mes_now": we, "win_start": ws, "win_end": we,
            "days": 1, "stop_min": 15, "machines": machines,
            "fleet": aggregate([r for m in machines for r in m["records"]]),
            "micro_idle_sec": 0.0}
    import copy
    snapshot = copy.deepcopy(data)

    wos = {
        "M1": [mig(1, "M1", "REACTIVE", D(2026, 6, 10, 5), D(2026, 6, 10, 7))],
        "M2": [mig(2, "M2", "PM", D(2026, 6, 10, 2), D(2026, 6, 10, 4))],   # 02–03 落生產、03–04 落不明
        "M3": [{"work_order_no": 3, "asset_id": "M3", "work_type": "PM",
                "opened_at": D(2026, 6, 10, 4), "closed_at": None,
                "status_history": [
                    {"status": "OPEN", "at": D(2026, 6, 10, 4), "hold_reason": None},
                    {"status": "ON_HOLD", "at": D(2026, 6, 10, 4, 30),
                     "hold_reason": "WAITING_MACHINE_TIME"}]}],
        "M4": [mig(41, "M4", "REACTIVE", D(2026, 6, 10, 4), D(2026, 6, 10, 7)),
               mig(42, "M4", "PM", D(2026, 6, 10, 5), D(2026, 6, 10, 8))],
        "M5": [mig(5, "M5", "REACTIVE", D(2026, 6, 10, 20), D(2026, 6, 10, 22))],
    }
    nd = overlay_wo_states(data, wos, now=we)

    def recs_of(eid):
        return next(m["records"] for m in nd["machines"] if m["eid"] == eid)

    # case1：不明 03–09 → 不明03–05 · 維修05–07 · 不明07–09（外加兩生產）
    m1 = recs_of("M1")
    ck([r["state"] for r in m1] == ["生產", "不明", "維修", "不明", "生產"], f"case1 序列: {[r['state'] for r in m1]}")
    rep = next(r for r in m1 if r["state"] == "維修")
    ck(rep["start"] == D(2026, 6, 10, 5) and rep["end"] == D(2026, 6, 10, 7)
       and rep["source"] == "cmms_workorder" and rep["work_order_no"] == 1 and rep["conf"] == "high",
       f"case1 維修段欄位: {rep}")

    # case2：生產 00–03 完整保留（只有不明 03–04 上色 PM）
    m2 = recs_of("M2")
    prod0 = m2[0]
    ck(prod0["state"] == "生產" and prod0["start"] == D(2026, 6, 10, 0)
       and prod0["end"] == D(2026, 6, 10, 3), f"case2 生產不被覆蓋: {prod0}")
    ck([r["state"] for r in m2] == ["生產", "計畫保養", "不明", "生產"], f"case2 序列: {[r['state'] for r in m2]}")
    pm = next(r for r in m2 if r["state"] == "計畫保養")
    ck(pm["start"] == D(2026, 6, 10, 3) and pm["end"] == D(2026, 6, 10, 4), f"case2 PM 只上色不明部分: {pm}")

    # case3：uptime-hold → 完全不上色（序列同底盤）
    m3 = recs_of("M3")
    ck([r["state"] for r in m3] == ["生產", "不明", "生產"], f"case3 uptime-hold 不上色: {[r['state'] for r in m3]}")

    # case4：重疊兩單 → 維修04–07（較早）· 計畫保養07–08 · union 停機=4h 非 5h
    m4 = recs_of("M4")
    ck([r["state"] for r in m4] == ["生產", "不明", "維修", "計畫保養", "不明", "生產"],
       f"case4 序列: {[r['state'] for r in m4]}")
    down4 = sum(r["sec"] for r in m4 if r["state"] in ("維修", "計畫保養"))
    ck(abs(down4 - 4 * 3600) < 1e-6, f"case4 union 停機應=4h 不雙計，got {down4/3600}h")

    # case5：工單落窗外 → 不變
    m5 = recs_of("M5")
    ck([r["state"] for r in m5] == ["生產", "不明", "生產"], f"case5 窗外工單無效: {[r['state'] for r in m5]}")

    # case7（2026-08-16 統一語言）：REACTIVE 中途進 等料 → **hold 態直通、不收斂回母態**。
    #   ⚠ 這條是「收斂廢除」的守衛：若有人把「等料/等外包收斂回 work_type 母態」加回
    #   `_wo_down_colored`，序列會變成 [...維修, 維修...]（合併成一段）⇒ 此釘紅。
    data7 = {"source": "demo", "mes_now": we, "win_start": ws, "win_end": we,
             "days": 1, "stop_min": 15, "machines": [mk("M7", base_recs())],
             "fleet": aggregate(base_recs()), "micro_idle_sec": 0.0}
    wos7 = {"M7": [{"work_order_no": 7, "asset_id": "M7", "work_type": "REACTIVE",
                    "opened_at": D(2026, 6, 10, 4), "closed_at": D(2026, 6, 10, 8),
                    "status_history": [
                        {"status": "OPEN", "at": D(2026, 6, 10, 4), "hold_reason": None},
                        {"status": "ON_HOLD", "at": D(2026, 6, 10, 6),
                         "hold_reason": "WAITING_PARTS"}]}]}
    m7 = next(m["records"] for m in overlay_wo_states(data7, wos7)["machines"]
              if m["eid"] == "M7")
    ck([r["state"] for r in m7] == ["生產", "不明", "維修", wem.HOLD_PARTS_STATE, "生產"],
       f"case7 等料態直通（維修04–06·等料06起，不收斂；懸尾 hold 依 S12 延伸）: "
       f"{[r['state'] for r in m7]}")
    _h7 = next(r for r in m7 if r["state"] == wem.HOLD_PARTS_STATE)
    ck(_h7["start"] == D(2026, 6, 10, 6), f"case7 等料段自 ON_HOLD 時點起算: {_h7}")
    ck(wem.HOLD_PARTS_STATE in STATE_ORDER and wem.HOLD_VENDOR_STATE in STATE_ORDER,
       "case7b hold 態必須在 STATE_ORDER（否則 donut/fleet timeline 靜默跳過＝畫不出來）")

    # case6：無工單 → 物件原樣（identity）
    ck(recs_of("M6") is machines[5]["records"], "case6 無工單機台 records 應原樣參照")

    # aggregate 守恆：每台 Σ record 秒 == 12h 窗（覆蓋不變）；不明秒下降；生產秒不變
    for m in nd["machines"]:
        tot = sum(r["sec"] for r in m["records"])
        ck(abs(tot - 12 * 3600) < 1e-6, f"{m['eid']} Σ秒應=12h，got {tot/3600}h")
    ck(nd["fleet"]["producing_sec"] == snapshot["fleet"]["producing_sec"],
       "生產秒總量 overlay 前後不變")
    ck(nd["fleet"]["unknown_sec"] < snapshot["fleet"]["unknown_sec"], "不明秒 overlay 後應下降（§25）")
    ck(w8.BUCKET_STATE in STATE_ORDER, "暫桶須在 STATE_ORDER（否則 donut 跳過）")

    # ---- A10 擴散 R1（2026-08-14）：機器視角裁切 + 子工位附掛列著色 --------------
    # ★ 負對照先行：`scope_windows=None` ⇒ 與加此參數前**逐位元相同**。
    ck(overlay_wo_states(snapshot, wos, scope_windows=None) == nd,
       "R1-1 scope_windows=None ⇒ overlay 逐位元同加此參數前（其餘機台不受影響）")
    ck(overlay_wo_states(snapshot, wos, scope_windows={}) == nd,
       "R1-2 空 dict 亦然")
    # 機器視角：把 M1 那張維修單裁到 05:00–06:00（模擬「範圍縮小後只有這一小時是整機停」）
    nd_c = overlay_wo_states(snapshot, wos,
                             scope_windows={"M1": {1: [(D(2026, 6, 10, 5), D(2026, 6, 10, 6))]}})
    m1c = next(m for m in nd_c["machines"] if m["eid"] == "M1")["records"]
    _rep = [r for r in m1c if r["state"] == "維修"]
    ck(len(_rep) == 1 and _rep[0]["start"] == D(2026, 6, 10, 5)
       and _rep[0]["end"] == D(2026, 6, 10, 6),
       f"R1-3 母機著色裁到機器視角窗（原 05–07 → 05–06）, got {_rep}")
    ck(next(m for m in nd_c["machines"] if m["eid"] == "M2")["records"]
       == next(m for m in nd["machines"] if m["eid"] == "M2")["records"],
       "R1-4 沒有出現在 scope_windows 裡的機台逐位元不變")
    # 空 list ⇒ 該單整段不上色（範圍全程不含母機＝機器沒有整台停）
    nd_z = overlay_wo_states(snapshot, wos, scope_windows={"M1": {1: []}})
    ck(all(r["state"] != "維修"
           for r in next(m for m in nd_z["machines"] if m["eid"] == "M1")["records"]),
       "R1-5 機器視角窗為空 ⇒ 該單一格都不上色")
    # 子工位附掛列著色（`overlay_children_wo_states`）：純函式、不 mutate 快取
    _kid = {"eid": "K1", "desc": "kid", "records": [
        {"eid": "K1", "state": "不明", "conf": "low",
         "start": D(2026, 6, 10, 4), "end": D(2026, 6, 10, 8), "sec": 4 * 3600}],
        "n_seg": 0, "micro_idle_sec": 0.0, "agg": None, "_a": None, "nosig": False,
        "parent_eid": "P1", "cov_note": False}
    _kid["agg"] = aggregate(_kid["records"])
    _kids = {"P1": [_kid]}
    _ent_wo = w8.normalize_wo({"work_order_no": 9001, "asset_id": "P1", "work_type": "REACTIVE",
                               "opened_at": D(2026, 6, 10, 5), "closed_at": D(2026, 6, 10, 6),
                               "status_history": []})
    _inh = {"K1": [{"wo": _ent_wo, "parent_eid": "P1", "dropped_dormant": [],
                    "clips": [(D(2026, 6, 10, 5), D(2026, 6, 10, 6), "inherited")]}]}
    _nk = overlay_children_wo_states(_kids, _inh, D(2026, 6, 10, 0), D(2026, 6, 10, 12))
    ck([r["state"] for r in _nk["P1"][0]["records"]] == ["不明", "維修", "不明"],
       f"R1-6 子工位列吃到母單著色, got={[r['state'] for r in _nk['P1'][0]['records']]}")
    ck([r["state"] for r in _kid["records"]] == ["不明"],
       "R1-7 **不得 mutate 輸入**（load_band_children 的結果在快取裡，毒化＝整個 TTL 都錯）")
    ck(_nk["P1"][0]["parent_eid"] == "P1" and _nk["P1"][0]["nosig"] is False,
       "R1-8 其餘旗標原樣保留")
    ck(overlay_children_wo_states(_kids, {}, D(2026, 6, 10, 0), D(2026, 6, 10, 12)) is _kids
       and overlay_children_wo_states(_kids, None, D(2026, 6, 10, 0), D(2026, 6, 10, 12)) is _kids,
       "R1-9 無繼承 entries ⇒ 原物件回傳（零成本、零改變）")
    # clips 全空（＝長停工位被扣光）⇒ 該列一格都不上色
    _inh_d = {"K1": [{"wo": _ent_wo, "parent_eid": "P1", "clips": [],
                      "dropped_dormant": [(D(2026, 6, 10, 5), D(2026, 6, 10, 6), "inherited")]}]}
    ck(overlay_children_wo_states(_kids, _inh_d, D(2026, 6, 10, 0),
                                  D(2026, 6, 10, 12))["P1"][0]["records"] == _kid["records"],
       "R1-10 clips 全空（長停被扣光）⇒ 子工位列逐位元不變（絕不拿 dropped 去上色）")

    # 無 mutation：傳入 data 與其 records 應與 overlay 前 deepcopy 相等
    ck(data == snapshot, "overlay 不得 mutate 傳入 data")

    # empty WOs → data 原樣返回（同物件）
    ck(overlay_wo_states(data, {}) is data, "空 wos → 原 data")

    # html.unescape 雙重轉義修復（Part 3）
    ck(html.unescape("Dispenser X") == "Dispenser X", "desc &amp; 應還原")
    ck(html.unescape("Dispenser X") == "Dispenser X", "正常名稱 unescape 冪等")

    # demo_window_wos（2026-08-16 統一語言）：跨 fleet 覆蓋 /equipment 那套四個計停機態、
    #   且**不合成 legacy work_type**（與 /equipment demo 同紀律「demo 亦不合成」）。
    dwd = get_demo(days=4, stop_min=15)
    dwos = demo_window_wos(dwd)
    wts = {w["work_type"] for lst in dwos.values() for w in lst}
    ck(not (wts - {"REACTIVE", "PM"}),
       f"demo WOs 不得合成 legacy work_type（CONVERSION/PART REQUEST）: {wts}")
    nd2 = overlay_wo_states(dwd, dwos)
    ck(nd2.get("wo_overlay") is True, "demo overlay 應標 wo_overlay")
    dstates = {r["state"] for m in nd2["machines"] for r in (m["records"] or [])}
    ck({"維修", "計畫保養", wem.HOLD_PARTS_STATE, wem.HOLD_VENDOR_STATE} <= dstates
       or len(dwd["machines"]) < 6,
       f"demo 上色態應覆蓋四個計停機態（fleet 夠大時）: {sorted(dstates)}")

    # ---- 子工位機台帶展開（expand_machines_band；nest、不進 data、不動父機聯集）----
    import datetime as _dt2
    # WP-10d（R-P11）：Gen2 母機／工位／無產出工位由名冊常數推導；
    #   私有與公開樹的名冊形狀不同。具名事實釘（_BAY_PARENT 反查）只在私有樹照釘。
    import core_paths as _wp
    _pub = _wp.is_public_tree()
    _P1, _P2, _P3 = list(GEN2_BAY_POSITIONS)[:3]
    _K1, _VX5, _K3 = ([e for e, _n, _r in GEN2_BAY_POSITIONS[p]] for p in (_P1, _P2, _P3))
    _K1, _VX5, _K3 = list(_K1), list(_VX5), list(_K3)
    _Z1 = [e for e, _n, _r in GEN2_BAY_POSITIONS[_P1] if _r <= 0]     # 無產出（ratio-0）工位
    dcal = get_data("demo", days=4, stop_min=15, cls=mt.CAL_CLASS)
    fleet0 = dict(dcal["fleet"]["by_state"])                # 展開前父機聯集
    n_mach0 = len(dcal["machines"])
    chc = expand_machines_band("demo", mt.CAL_CLASS, 4, 15, dcal)
    ck(len(dcal["machines"]) == n_mach0, "band 展開不得改動 data['machines']（子工位不進 data）")
    ck(dcal["fleet"]["by_state"] == fleet0, "band 展開不得改動父機聯集（donut 不變）")
    ck(list(chc.keys()) == [_P1], f"CAL 子工位只掛 Gen2 父機: {list(chc.keys())}")
    ck([k["eid"] for k in chc[_P1]] == _K1,
       "CAL 子工位＝配置的工位一律成列（不隱藏）")
    ck([k["eid"] for k in chc[_P1] if k["nosig"]] == _Z1,
       "無活動工位（ratio-0）＝nosig 空列（空軌+『—』、非隱藏）")
    ck(all((k["agg"]["total_sec"] or 0) == 0 and k["records"] == [] for k in chc[_P1] if k["nosig"]),
       "nosig 列＝零 records/零 agg（誠實空、非捏造）")
    parents = {m["eid"] for m in dcal["machines"]}
    kids = {k["eid"] for ks in chc.values() for k in ks}
    ck(parents.isdisjoint(kids), "子工位 EID 與父機 EID 不相交")
    ck(all(k.get("cov_note") is False for k in chc[_P1]), "CAL 子工位無覆蓋率 caveat")
    # 每個子工位 record 落在窗內
    ws, we = dcal["win_start"], dcal["win_end"]
    for k in chc[_P1]:
        for r in k["records"]:
            rs = r["start"] if isinstance(r["start"], _dt2.datetime) else _dt2.datetime.fromisoformat(str(r["start"]))
            re = r["end"] if isinstance(r["end"], _dt2.datetime) else _dt2.datetime.fromisoformat(str(r["end"]))
            ck(ws <= rs and re <= we, "子工位 record 應落在觀測窗內")
    ck(expand_machines_band("snapshot", mt.CAL_CLASS, 4, 15, dcal) == {}, "snapshot band 展開＝{}")
    # 確定性：同輸入兩次相同
    chc2 = expand_machines_band("demo", mt.CAL_CLASS, 4, 15, dcal)
    ck([k["eid"] for k in chc2[_P1]] == [k["eid"] for k in chc[_P1]], "band 展開確定性")
    # CLN：二父八子（DI 母機 16077 + SF 母機 27600、各帶 M1-M4）、全帶覆蓋率 caveat
    dcln = get_data("demo", days=4, stop_min=15, cls=CLN_CLASS)
    ck([m["eid"] for m in dcln["machines"]] == [_P2, _P3], "CLN demo fleet＝二母機 16077/27600")
    chi = expand_machines_band("demo", CLN_CLASS, 4, 15, dcln)
    ck(len(chi.get(_P2, [])) == len(_VX5) and len(chi.get(_P3, [])) == len(_K3),
       "CLN 子工位＝DI 4 + SF 4（各掛自己母機）")
    ck(all(k.get("cov_note") is True for ks in chi.values() for k in ks), "CLN 子工位帶覆蓋率 caveat")
    # CLN 站頁 yield 展開保留父機列（_demo_expand_fleet keep-parent）：2 父 + 8 子 = 10 列、父機先於各自子工位
    cln_fleet = _demo_expand_fleet([(e, d) for e, d, _du in DEMO_TL_FLEETS[CLN_CLASS]])
    ck(len(cln_fleet) == 2 + len(_VX5) + len(_K3) and cln_fleet[0][0] == _P2 and cln_fleet[1 + len(_VX5)][0] == _P3,
       "CLN demo yield fleet＝二父機各領 4 工位（保留父機）")
    cal_fleet = _demo_expand_fleet([(e, d) for e, d, _du in DEMO_CAL_FLEET])
    ck(_P1 not in {e for e, _ in cal_fleet}, "CAL demo yield fleet＝取代父機（不留 Gen2 父機）")
    ck(len(cal_fleet) == len(DEMO_CAL_FLEET) - 1 + len(_K1) and _Z1[0] in {e for e, _ in cal_fleet},
       "yield fleet 全列制：CAL＝3 Gen1 + 4 工位（無產出工位 26841/26887 也成列）")
    # WP-4b R-B1：Gen2 展開唯一規則 `gen2_unit_split`／`expand_unit_roster`／合成名冊骨架
    ck(gen2_unit_split("EID-00884") is None, "gen2_unit_split：一般機台 → None")
    ck(gen2_unit_split(_P1) == (False, [(e, d) for e, d, _r in GEN2_BAY_POSITIONS[_P1]]),
       "gen2_unit_split：CAL Gen2 取代父機、四工位全列")
    ck(gen2_unit_split(_P2)[0] is True, "gen2_unit_split：CLN 母機保留父機列")
    _diag = expand_unit_roster([(e, d) for e, d, _du in DEMO_TL_FLEETS[CLN_CLASS]])
    ck([(e, d) for e, d, _p in _diag] == cln_fleet and [p for _e, _d, p in _diag][:2] == [None, _P2],
       "expand_unit_roster ≡ _demo_expand_fleet（CLN）且工位帶 parent_eid")
    _sk = synth_unit_skeleton(mt.CAL_CLASS, [{"eid": e, "desc": d} for e, d, _du in DEMO_CAL_FLEET])
    ck([r["eid"] for r in _sk["machines"]] == [e for e, _ in cal_fleet]
       and all(r["output_units"] == 0 and r["good_units"] == 0 for r in _sk["machines"])
       and {r["parent_eid"] for r in _sk["machines"] if r["eid"] in _BAY_PARENT} == {_P1},
       "synth_unit_skeleton：列＝同一條展開規則、產出／良品 0（不產數字）、工位帶 parent_eid")
    # 全列制下 demo 不替 census 無產出工位合成假產出（誠實零列）
    rec_cal = _demo_station_rec(mt.CAL_CLASS, 0, with_machines=True)
    z = {mr["eid"]: mr for mr in (rec_cal.get("machines") or [])}
    ck(_Z1[0] in z and (z[_Z1[0]].get("output_units") or 0) == 0,
       "demo 零產出工位＝output 0（不捏造）")
    ck((z.get(_K1[0]) or {}).get("output_units", 0) > 0, "demo 有產出工位仍有合成產出")

    # expand_units_timeline（站頁稼動展開）：CLN 保留父機 agg、CAL 取代（MED-1 keep-parent guard）
    base_agg = {"by_state": {"生產": 100.0, "不明": 20.0}, "total_sec": 120.0, "producing_sec": 100.0,
                "downtime_sec": 20.0, "unknown_sec": 20.0, "util": 100.0 / 120, "unknown_pct": 20.0 / 120}
    cln_units = expand_units_timeline("demo", CLN_CLASS, 4, 15,
                                      {_P2: dict(base_agg), _P3: dict(base_agg)})
    ck(_P2 in cln_units and _P3 in cln_units and len(cln_units) == 2 + len(_VX5) + len(_K3),
       "CLN 站頁稼動展開保留二父機 + 8 工位")
    cal_units = expand_units_timeline("demo", mt.CAL_CLASS, 4, 15, {_P1: dict(base_agg)})
    ck(_P1 not in cal_units, "CAL 站頁稼動展開取代父機（不留 Gen2 父機）")
    ck(all(_BAY_PARENT.get(m) == p for p, mods in GEN2_BAY_POSITIONS.items() for m, _n, _r in mods)
       and (_pub or (_BAY_PARENT.get("EID-14732") == "EID-16077" and _BAY_PARENT.get("EID-27639") == "EID-27600"
                     and _BAY_PARENT.get("EID-24790") == "EID-24742")), "_BAY_PARENT 反查對映正確（二父）")

    # ---- 零工期段偵測（M4 後遺）：機台級旗 + 站級旗 + station_rollup 空結果分支 ----
    zws, zwe = D(2026, 6, 10, 0, 0), D(2026, 6, 10, 12, 0)   # 專用窗（ws/we 上方已被 demo 窗覆寫）
    Z0, Z1 = D(2026, 6, 10, 4, 0), D(2026, 6, 10, 8, 0)      # 兩個瞬時事件時刻（同窗、不同時間）
    # _win_seg_count：零工期段落窗計入
    ck(_win_seg_count([(Z0, Z0), (Z1, Z1)], zws, zwe) == 2, "零工期段落窗計入 _win_seg_count")
    ck(_win_seg_count([(D(2020, 1, 1), D(2020, 1, 1))], zws, zwe) == 0, "窗外零工期段不計入")
    # _zero_len_only_station：fleet>0 且 n_seg>0 且 producing==0 → True
    ck(_zero_len_only_station({"fleet_size": 2, "n_seg": 5, "producing_sec": 0.0}) is True,
       "零工期站：n_seg>0 且生產 0 → 旗立")
    ck(_zero_len_only_station({"fleet_size": 2, "n_seg": 5, "producing_sec": 100.0}) is False,
       "混合站（生產>0）→ 旗不立")
    ck(_zero_len_only_station({"fleet_size": 2, "n_seg": 0, "producing_sec": 0.0}) is False,
       "整站無訊號（n_seg 0）→ 旗不立（≠零工期）")
    ck(_zero_len_only_station({"fleet_size": 0, "n_seg": 0, "producing_sec": 0.0}) is False,
       "空 fleet → 旗不立")
    # _build（未 clip 路徑 analyze 推）：全零工期段機台 → zero_len_only；真時長機台 → 不立
    zb = _build([("Z", "zero"), ("R", "real")],
                {"Z": [(Z0, Z0), (Z1, Z1)], "R": [(Z0, Z1)]}, zwe, 1, 15, "demo", zws)
    zmap = {m["eid"]: m for m in zb["machines"]}
    ck(zmap["Z"]["zero_len_only"] is True and (zmap["Z"]["agg"]["producing_sec"] == 0.0),
       "_build 全零工期機台→旗立、生產 0")
    ck(zmap["R"]["zero_len_only"] is False and zmap["R"]["agg"]["producing_sec"] > 0,
       "_build 真時長機台→旗不立")
    # _build zero_len 傳入（模擬 live clip 掏空：segs 空但傳旗）
    zb2 = _build([("Z", "zero")], {"Z": []}, zwe, 1, 15, "live", zws, zero_len={"Z": True})
    ck(zb2["machines"][0]["zero_len_only"] is True and zb2["machines"][0]["_a"] is None,
       "_build clip 掏空(segs 空)+傳旗→旗立、_a None")
    # station_rollup_from_data 空結果分支：機台帶零工期旗 → 回零工期站 rec
    zr = station_rollup_from_data({"machines": [{"eid": "Z", "desc": "z", "_a": None, "zero_len_only": True}]},
                                  "segZeroLen")
    ck(zr is not None and zr.get("zero_len_only") is True and zr["producing_sec"] == 0.0,
       "station_rollup 空結果+零工期旗→零工期站 rec")
    ck(station_rollup_from_data({"machines": [{"eid": "N", "_a": None}]}, "segNo") is None,
       "station_rollup 空結果+無零工期旗（整站無訊號）→ None")
    # _range_station_zlo（日曆窗 live 站級旗；clip 前算）：全零工期機隊→True、混合→False、無段→False
    ck(_range_station_zlo([("A", "a"), ("B", "b")],
                          {"A": [(Z0, Z0), (Z1, Z1)], "B": [(Z0, Z0)]}, zws, zwe) is True,
       "_range_station_zlo 全零工期機隊→站旗 True")
    ck(_range_station_zlo([("A", "a"), ("B", "b")],
                          {"A": [(Z0, Z0)], "B": [(Z0, Z1)]}, zws, zwe) is False,
       "_range_station_zlo 混合（B 有正時長段）→站旗 False")
    ck(_range_station_zlo([("A", "a"), ("B", "b")],
                          {"A": [(Z0, Z0)], "B": []}, zws, zwe) is True,
       "_range_station_zlo 零工期 + 無段機台（無段不計）→站旗 True")
    ck(_range_station_zlo([("A", "a")], {"A": []}, zws, zwe) is False,
       "_range_station_zlo 整站無段→站旗 False（≠零工期）")
    ck(_range_station_zlo([], {}, zws, zwe) is False,
       "_range_station_zlo 空 fleet→站旗 False")
    # demo/live 計分板：demo 永不觸發零工期旗（合成段皆正時長）
    dsb = get_scoreboard("demo", days=3)
    ck(all(s.get("zero_len_only") is False for s in dsb["stations"]),
       "demo 計分板全站 zero_len_only False（合成不觸發）")

    # ── A10 擴散 R2（2026-08-16）：`_attrib_wo_segs_by_eid` 的**接線**釘──────────
    #    口徑本體釘在 `board_eqmeeting` N3；這裡只釘「/daily 的供給層真的走 scoped 版
    #    且 dormant_events 有傳到底」（wiring，不重驗口徑）。
    import board_w8 as _w8r2
    import board_scope as _wsr2
    _r2d = datetime(2026, 7, 4)
    # ⚠ 時刻一律餵 naive 台北 datetime（normalize_wo 契約行為）；不得在本檔出現 +8h 換算
    #   （檔尾 A11 負斷言掃全檔源碼、UTC-Z 造樣會誤觸）。
    _r2wo = _w8r2.normalize_wo({
        "work_order_no": 72001, "asset_id": _P1, "work_type": "REACTIVE",
        "opened_at": _r2d + timedelta(hours=8, minutes=50),
        "closed_at": _r2d + timedelta(hours=15, minutes=40),
        "status_history": [
            {"to_status": "OPEN", "changed_at": _r2d + timedelta(hours=8, minutes=50)},
            {"to_status": "IN_PROGRESS",
             "changed_at": _r2d + timedelta(hours=10, minutes=25)},
            {"to_status": "CLOSED",
             "changed_at": _r2d + timedelta(hours=15, minutes=40)}]})
    _r2wo["scope_events"] = _w8r2.normalize_scope_events(
        [{"ts": _r2d + timedelta(hours=10, minutes=30),
          "scope": [_wsr2.PARENT_EXPANSION[_P1][0]]}])[0]
    _r2segs = _attrib_wo_segs_by_eid({_P1: [_r2wo]},
                                     _r2d, _r2d + timedelta(days=1), dormant_events=[])
    _r2sec = sum((s["end"] - s["start"]).total_seconds()
                 for s in _r2segs.get(_P1) or [] if s.get("countable"))
    ck(abs(_r2sec - (1 * 3600 + 40 * 60)) < 1e-6,
       "R2 wiring：/daily 母機 wo_segs 走機器視角（1h40m，非整窗 6h50m）")
    _r2dwo = _w8r2.normalize_wo({
        "work_order_no": 72002, "asset_id": _Z1[0], "work_type": "REACTIVE",
        "opened_at": _r2d + timedelta(hours=2),
        "closed_at": _r2d + timedelta(hours=6), "status_history": []})
    ck(_Z1[0] not in _attrib_wo_segs_by_eid({_Z1[0]: [_r2dwo]},
                                                 _r2d, _r2d + timedelta(days=1)),
       "R2 wiring：長停工位（橋常數）直接單在 /daily 的 wo_segs 整台消失（不計任何 downtime）")
    ck(_Z1[0] in _attrib_wo_segs_by_eid({_Z1[0]: [_r2dwo]},
                                             _r2d, _r2d + timedelta(days=1),
                                             dormant_events=[]),
       "R2 wiring：dormant_events=[] 負對照 ⇒ 照常入帳（參數真的有傳到底）")

    # ── 日帳輪替（2026-08-03 LOW E-⑤）：**改寫 `ledger_dir` 指到 tmp 目錄**＝零觸碰
    #    真的 `_state/daily_ledger/`（selftest 絕不刪使用者的日帳）。
    import shutil
    import tempfile
    _real_dir = globals()["ledger_dir"]
    _tmpd = tempfile.mkdtemp(prefix="board_ledger_selftest_")
    try:
        globals()["ledger_dir"] = lambda: _tmpd
        today = "2026-08-03"
        keep = attrib_module().KEEP_DAYS
        d_keep = (datetime(2026, 8, 3) - timedelta(days=keep - 1)).strftime("%Y-%m-%d")
        d_edge = (datetime(2026, 8, 3) - timedelta(days=keep)).strftime("%Y-%m-%d")
        for nm in (f"{today}.json", f"{d_keep}.json", f"{d_edge}.json",
                   f"{d_edge}.json.bak-20260801120000", f"{today}.json.tmp",
                   f"{d_keep}.json.bak-20260801120000"):
            with open(os.path.join(_tmpd, nm), "w", encoding="utf-8") as fh:
                fh.write("{}")
        # `.tmp` 造成「一小時前寫的」＝殘骸；剛寫的 .tmp（同一支正在寫）不得被清
        old = time.time() - LEDGER_TMP_STALE_SEC - 60
        os.utime(os.path.join(_tmpd, f"{today}.json.tmp"), (old, old))
        with open(os.path.join(_tmpd, f"{d_keep}.json.tmp"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        _prune_ledgers(today)
        left = set(os.listdir(_tmpd))
        ck(f"{d_edge}.json" not in left,
           f"輪替窗＝恰 KEEP_DAYS 天：第 {keep} 天前的檔要被刪（舊版差一天＝多留 1 天）")
        ck(f"{d_keep}.json" in left and f"{today}.json" in left,
           "窗內的日帳一個都不能少")
        ck(f"{d_edge}.json.bak-20260801120000" not in left
           and f"{d_keep}.json.bak-20260801120000" in left,
           ".bak-* 殘骸照同一條輪替線（過期才刪、窗內保留）")
        ck(f"{today}.json.tmp" not in left and f"{d_keep}.json.tmp" in left,
           "陳舊 .tmp 殘骸清掉、現役 .tmp（剛寫的）不碰")
        # 新命名（board_atomic：.<day>.json.<pid>-<tid>-<seq>.tmp）同走那條線；舊固定名上面已釘
        _nn_old = f".{d_keep}.json.111-222-3.tmp"
        _nn_new = f".{d_keep}.json.111-222-4.tmp"
        for nm in (_nn_old, _nn_new):
            with open(os.path.join(_tmpd, nm), "w", encoding="utf-8") as fh:
                fh.write("{}")
        os.utime(os.path.join(_tmpd, _nn_old), (old, old))
        _prune_ledgers(today)
        left = set(os.listdir(_tmpd))
        ck(_nn_old not in left and _nn_new in left,
           "新命名 .tmp：陳舊清掉、現役不碰")
        # 原子寫釘（新語意）：日帳走 board_atomic（同目錄唯一暫存＋fsync＋os.replace），不再固定 .tmp
        _wat_src = open(_wat.__file__, encoding="utf-8").read()
        import inspect
        _pl_src = inspect.getsource(persist_ledger) + inspect.getsource(_atomic_write_json)
        ck("os.replace(" in _wat_src and "os.fsync(" in _wat_src and "_wat.atomic_write_json" in _pl_src
           and '+ ".tmp"' not in _pl_src and "os.replace(" not in _pl_src,
           "日帳原子寫＝board_atomic（os.replace＋fsync），persist_ledger／_atomic_write_json 不再自帶固定 .tmp")
        _atomic_write_json(os.path.join(_tmpd, "z.json"), {"k": "中"})
        ck(sorted(n for n in os.listdir(_tmpd) if "z.json" in n) == ["z.json"],
           "_atomic_write_json 寫完不留暫存檔")
    finally:
        globals()["ledger_dir"] = _real_dir
        shutil.rmtree(_tmpd, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════════
    # PM 板三源 canonical（`board.pm.v1`；SPEC-PM-BOARD §4/§5）
    #   **全離線零 HTTP**：live 路徑注入 fake transport 模組（`_W8T` 全域直接換掉），
    #   fake 只認得四個方法、任何真連線都不可能發生。
    # ══════════════════════════════════════════════════════════════════════
    if pm_module() is not None:
        _fx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures")
        with open(os.path.join(_fx, "pm_board_fixture_pm_schedules.v1.json"),
                  encoding="utf-8") as fh:
            _sched_rows = json.load(fh)["rows"]
        with open(os.path.join(_fx, "pm_board_fixture_list_rows.v1.json"),
                  encoding="utf-8") as fh:
            _wo_rows = json.load(fh)["rows"]

        _TASK_ROWS = [{"task_no": "TSK-01", "description": "Manifold Wash Rig Weekly "
                                                           "Maintenance", "is_active": True},
                      {"task_no": "_3TN905K21",
                       "description": "AttachRig Probe Card Replacement", "is_active": True}]
        # 零件名字典（S-PM6）＝prod fixture 真列（含 name null／雙 null 兩種值域）
        with open(os.path.join(_fx, "pm_board_fixture_inventory_items.v1.json"),
                  encoding="utf-8") as fh:
            _INV_ROWS = json.load(fh)["response_rows"]

        class _FakeT:
            """假 transport 模組（形同 `board_w8_transport` 的 PM 五支）。"""
            def __init__(self, sched=None, sched_reason=None, wo=None, wo_reason=None,
                         steps=None, steps_reason=None, detail=None, detail_reason=None,
                         tasks=None, tasks_reason=None, tasks_truncated=False,
                         inv=None, inv_reason=None, inv_truncated=False,
                         configured=True, boom=False):
                self.calls = []
                self._a = dict(sched=sched, sched_reason=sched_reason, wo=wo,
                               wo_reason=wo_reason, steps=steps, steps_reason=steps_reason,
                               detail=detail, detail_reason=detail_reason,
                               tasks=tasks, tasks_reason=tasks_reason,
                               tasks_truncated=tasks_truncated,
                               inv=inv, inv_reason=inv_reason, inv_truncated=inv_truncated)
                self._cfg, self._boom = configured, boom

            def is_configured(self):
                return self._cfg

            def fetch_inventory_items(self, **kw):
                self.calls.append("inv")
                if self._a["inv_reason"]:
                    return (None, self._a["inv_reason"])
                rows = self._a["inv"] if self._a["inv"] is not None else _INV_ROWS
                return ({"schema_id": "pm_inventory_items.v1", "rows": rows,
                         "truncated": bool(self._a["inv_truncated"]), "page_error": None,
                         "pages": 1, "dropped_n": 0, "count": len(rows)}, None)

            def fetch_tasks(self, **kw):
                self.calls.append("tasks")
                if self._a["tasks_reason"]:
                    return (None, self._a["tasks_reason"])
                rows = self._a["tasks"] if self._a["tasks"] is not None else _TASK_ROWS
                return ({"schema_id": "pm_tasks.v1", "rows": rows,
                         "truncated": bool(self._a["tasks_truncated"]),
                         "limit_hit": bool(self._a["tasks_truncated"]),
                         "dropped_n": 0, "count": len(rows)}, None)

            def fetch_pm_schedules(self, **kw):
                self.calls.append("sched")
                if self._boom:
                    raise RuntimeError("boom")
                if self._a["sched_reason"]:
                    return (None, self._a["sched_reason"])
                rows = self._a["sched"] if self._a["sched"] is not None else _sched_rows
                return ({"schema_id": "pm_schedules.v1", "rows": rows, "truncated": False,
                         "dropped_n": 0, "count": len(rows)}, None)

            def fetch_pm_work_orders(self, f, t, **kw):
                self.calls.append(("wo", str(f), str(t)))
                if self._a["wo_reason"]:
                    return (None, self._a["wo_reason"])
                rows = self._a["wo"] if self._a["wo"] is not None else _wo_rows
                return ({"schema_id": "pm_work_orders.v1", "rows": rows,
                         "truncated": False, "dropped_n": 0, "count": len(rows)}, None)

            def fetch_task_steps(self, task_no, **kw):
                self.calls.append(("steps", task_no))
                if self._a["steps_reason"]:
                    return (None, self._a["steps_reason"])
                return (list(self._a["steps"] or []), None)

            def fetch_pm_wo_detail(self, wno, **kw):
                self.calls.append(("detail", wno))
                if self._a["detail_reason"]:
                    return (None, self._a["detail_reason"])
                return (self._a["detail"], None)

        def _with_pm(fake, live=True, fn=None):
            """暫時換掉 `_W8T` 與 `BOARD_PM_LIVE`（用完還原；零真 HTTP）。"""
            g = globals()
            old_t, old_tried = g["_W8T"], g["_W8T_TRIED"]
            old_env = os.environ.get(PM_LIVE_ENV)
            g["_W8T"], g["_W8T_TRIED"] = fake, True
            if live:
                os.environ[PM_LIVE_ENV] = "1"
            else:
                os.environ.pop(PM_LIVE_ENV, None)
            try:
                return fn()
            finally:
                g["_W8T"], g["_W8T_TRIED"] = old_t, old_tried
                if old_env is None:
                    os.environ.pop(PM_LIVE_ENV, None)
                else:
                    os.environ[PM_LIVE_ENV] = old_env

        # ---- demo 三態 ----
        pb = get_pm_board("demo")
        ck(pb["schema"] == "board.pm.v1" and pb["source"] == "demo"
           and pb.get("unavailable") is None, "PM demo：板可用、schema pin")
        ck(pm_ok(pb) and pb["backlog"]["rows"] and pb["items_index"],
           "PM demo：backlog 有列、items_index 有料（展開卡的唯一資料源）")
        ck(get_pm_board("demo", win="qr")["win"]["key"] == "qr"
           and get_pm_board("demo", win="junk")["win"]["key"] == "mo",
           "PM demo：win 傳遞 + 壞值落回 mo")
        ck(get_pm_board("garbage")["source"] == "demo",
           "PM：source 白名單在分派點正規化（垃圾 source → demo，絕不誤走 live）")
        snap = get_pm_board("snapshot")
        ck(snap["unavailable"] == "snapshot_unsupported" and snap["score"] is None
           and snap["backlog"] is None,
           "PM snapshot：誠實降級（as-of-now 事實不可用舊切面重播）")
        ck(pm_ok(snap) is False and pm_unavailable("x")["diag"]["degraded"] is True,
           "PM：降級形的 pm_ok 判 False、diag.degraded 掛旗")
        ck(set(pm_unavailable("x", "live")) == set(pb),
           "PM：降級形與正常板**鍵集相同**（模板不必分兩套形狀取值）")
        ck("Lost" not in json.dumps(pb, default=str)
           and "損失" not in json.dumps(pb, ensure_ascii=False, default=str),
           "PM：板 payload 零「Lost／損失」（本頁語彙負斷言）")

        # ---- live 雙閘 ----
        f0 = _FakeT()
        g0 = _with_pm(f0, live=False, fn=lambda: get_pm_board("live"))
        ck(g0["unavailable"] == "pm_live_gated" and f0.calls == [],
           "PM live 閘①：BOARD_PM_LIVE 未設 → pm_live_gated，且**一次 HTTP 都沒打**")
        f1 = _FakeT(configured=False)
        g1 = _with_pm(f1, fn=lambda: get_pm_board("live"))
        ck(g1["unavailable"] == "not_configured" and f1.calls == [],
           "PM live 閘②：cmms env 未設 → not_configured、零呼叫")

        # ---- live 正常路徑（fixture 兩張 WO＋四條排程）----
        _T0 = datetime(2026, 8, 4, 9, 0, 0)
        f2 = _FakeT()
        b2 = _with_pm(f2, fn=lambda: get_pm_board("live", today=_T0))
        ck(b2.get("unavailable") is None and b2["source"] == "live"
           and b2["sched_status"] == "live" and b2["wo_status"] == "live",
           f"PM live：正常路徑組板成功，got {b2.get('unavailable')}")
        ck(b2["diag"]["n_sched"] == 4 and b2["diag"]["n_wo"] == 2,
           "PM live：主源四條排程 + WO 兩張全進板")
        ck(b2["today"] == "2026-08-04" and b2["win"]["key"] == "mo",
           "PM live：today 可注入（測試確定性）")
        wo_call = [c for c in f2.calls if isinstance(c, tuple) and c[0] == "wo"][0]
        _lb = pm_wo_lookback_d(_T0, "mo", pm_module())
        ck(wo_call[2] == "2026-08-04" and wo_call[1] == str(_T0.date() - timedelta(days=_lb)),
           f"PM live：WO 窗＝開單日超抓 {_lb} 天（CM1 無到期日窗 ⇒ 之後按 due 過濾），got {wo_call}")
        ck(b2["diag"]["wo_lookback_d"] == _lb and b2["diag"]["wire_sched_dropped_n"] == 0,
           "PM live：wire 層丟棄數與超抓窗進 diag（覆蓋誠實）")
        # ★ 2026-08-05 (b2)：趨勢改 12 **個月** ⇒ 超抓必須跟著長；沿用舊的固定 150 天，
        #   最舊的那幾個月會**結構性空白**，而頁面只會顯示「那時候沒有保養到期」＝謊。
        ck(_lb >= 365 + PM_WO_GEN_LEAD_D - 31 and _lb > PM_WO_LOOKBACK_D,
           f"PM live：超抓窗涵蓋 12 個月趨勢（含生成提前量 {PM_WO_GEN_LEAD_D} 天），got {_lb}")
        _lb_yr = pm_wo_lookback_d(datetime(2026, 12, 20), "yr", pm_module())
        ck(_lb_yr >= (datetime(2026, 12, 20) - datetime(2026, 1, 1)).days + PM_WO_GEN_LEAD_D,
           f"PM live：年窗（YTD）也把超抓拉到 1/1 之前（12 月時 YTD 比 12 個月趨勢還長一點），"
           f"got {_lb_yr}")
        ck(pm_wo_lookback_d(_T0, "mo", None) == PM_WO_LOOKBACK_D,
           "PM live：引擎不可用時退回 env 下界（不炸、也不假裝抓得比實際遠）")
        b2y = _with_pm(_FakeT(), fn=lambda: get_pm_board("live", today=_T0, win="year"))
        ck(b2y["win"]["key"] == "yr"
           and b2y["win"]["start"] == datetime(2026, 1, 1).date()
           and b2y["win"]["end"] == datetime(2026, 8, 4).date(),
           f"PM live：`?win=year` 別名收斂成 yr、且窗＝**本年至今**（SALTMARSH 反饋輪 R1 (a)），"
           f"got {b2y['win']}")

        # ---- 名稱腿（S-PM4 `/tasks`；**fail-open**）----
        #   `_sched_rows` 的 task_id 是真 prod 值（含 `_3TN905K21`）⇒ 字典命中即人話名。
        ck("tasks" in f2.calls, "PM live：名稱腿有被呼叫（/tasks 一次抓全量字典）")
        ck(b2["diag"]["task_name_status"] == "ok" and b2["diag"]["task_names_n"] == 2,
           f"PM live：名稱源狀態上板（頁面要有一行解釋為何顯代號），got {b2['diag']}")
        ck(all(("task_name" in r) for r in b2["backlog"]["rows"])
           and all(("task_name" in v) for v in b2["items_index"].values()),
           "PM live：清單與展開索引都帶 task_name 欄（兩處顯示同一個名字）")
        f2b = _FakeT(tasks_reason="timeout")
        b2b = _with_pm(f2b, fn=lambda: get_pm_board("live", today=_T0))
        ck(b2b.get("unavailable") is None and b2b["sched_status"] == "live"
           and b2b["wo_status"] == "live"
           and b2b["diag"]["task_name_status"] == "timeout"
           and all(r["task_name"] is None for r in b2b["backlog"]["rows"]),
           "PM live：★ 名稱腿掛掉＝**fail-open**——板照建、四態/backlog/owner 零影響，"
           "只是清單退回顯代號（拿字典當承重牆才是反模式）")
        ck(b2b["backlog"]["n_total"] == b2["backlog"]["n_total"]
           and b2b["truncated"] == b2["truncated"],
           "PM live：名稱腿死活不改變任何數字，也不把板級 truncated 掛起來")
        f2c = _FakeT(tasks_truncated=True)
        b2c = _with_pm(f2c, fn=lambda: get_pm_board("live", today=_T0))
        ck(b2c["diag"]["task_name_status"] == "truncated" and b2c["truncated"] is False,
           "PM live：字典被截斷 → 自揭 `truncated` 名稱狀態，但**不併進板級 truncated**"
           "（少幾個名字 ≠ 資料覆蓋不足）")

        class _FakeNoTasks(_FakeT):
            """舊版 transport（根本沒有 fetch_tasks）＝fail-open 的最後一道。"""
            fetch_tasks = None
        f2d = _FakeNoTasks()
        b2d = _with_pm(f2d, fn=lambda: get_pm_board("live", today=_T0))
        ck(b2d.get("unavailable") is None
           and b2d["diag"]["task_name_status"] == "not_wired",
           "PM live：transport 沒有 fetch_tasks（舊版模組）也只降級名稱——直接呼叫會炸成 "
           "error:AttributeError＝整板黑掉，那正好違反 fail-open 本身")

        # ---- 主源亡＝整板亡；WO 腿亡＝partial ----
        f3 = _FakeT(sched_reason="timeout")
        b3 = _with_pm(f3, fn=lambda: get_pm_board("live", today=_T0))
        ck(b3["unavailable"] == "timeout" and b3["backlog"] is None,
           "PM live：主源 /pm-schedules 失敗 → 整板降級（reason 原樣透傳）")
        f4 = _FakeT(wo_reason="http_500")
        b4 = _with_pm(f4, fn=lambda: get_pm_board("live", today=_T0))
        ck(b4.get("unavailable") is None and b4["wo_status"] == "http_500"
           and b4["wo_ok"] is False,
           "PM live：WO 腿失敗＝partial（整板不亡、wo_status 帶原因）")
        ck(b4["score"]["completeness"] is None and b4["trend"]["months"] is not None
           and b4["backlog"] is not None and b4["owners"] is not None
           and b4["forecast"] is not None,
           "PM live partial：分數/趨勢降級，**backlog／owner／預警照建**（主源還在）")
        f5 = _FakeT(boom=True)
        b5 = _with_pm(f5, fn=lambda: get_pm_board("live", today=_T0))
        ck(b5["unavailable"] == "error:RuntimeError",
           "PM live：任何例外都收斂成 error:<Type>（**恆不 raise**、整站不炸）")

        # ---- 展開卡（demo 三型）----
        it1 = get_pm_item("demo", "PMD-01")
        ck(it1["steps"]["status"] == "ok" and it1["steps"]["rows"],
           "PM item demo：PMD-01 細項有料")
        ck(get_pm_item("demo", "PMD-02")["steps"]["status"] == "empty",
           "PM item demo：PMD-02＝cmms 無細項（200 空陣列、不是錯誤）")
        ck(get_pm_item("demo", "PMD-03")["wo"]["status"] == "gone",
           "PM item demo：PMD-03＝soft-ref 404（~7% 已知現實）")
        ck(get_pm_item("demo", "NOPE")["unavailable"] == "pm_unknown_id",
           "PM item demo：未知 pm_id → 誠實降級")
        ck(get_pm_item("snapshot", "PMD-01")["unavailable"] == "snapshot_unsupported",
           "PM item snapshot：不支撐")

        # ---- 展開卡（live 兩腿獨立容忍）----
        idx = pb["items_index"]["PMD-01"]
        det_raw = {"work_order_no": 34081, "status": "CLOSED",
                   "brief_description": "Manifold Wash Rig Weekly Maintenance",
                   "closed_date": "2026-05-20", "closed_time": "20:59:47",
                   "opened_at": "2026-05-17T23:56:13Z", "closed_at": "2026-05-20T12:59:47Z",
                   "status_history": []}
        f6 = _FakeT(steps=[{"proc_seq": 10, "description": "Lock out", "parts": []}],
                    detail=det_raw)
        i6 = _with_pm(f6, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i6["steps"]["status"] == "ok" and i6["wo"]["status"] == "ok",
           "PM item live：兩腿皆 ok")
        ck(i6["wo"]["detail"]["closed_s"] == "2026-05-20 20:59:47",
           f"PM item live：完成時戳走 closed_date+time 主路徑（C-P8 唯一落點），"
           f"got {i6['wo']['detail']['closed_s']}")
        ck(i6["wo"]["detail"]["hist"] is None
           and i6["wo"]["detail"]["fallback_span"] == "2026-05-18 07:56 → 2026-05-20 20:59",
           f"PM item live：history 空＝遷移單 ⇒ 回退 opened→closed 一行（Z→台北牆鐘），"
           f"got {i6['wo']['detail']['fallback_span']}")
        f7 = _FakeT(steps_reason="http_404", detail_reason="http_404")
        i7 = _with_pm(f7, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i7["steps"]["status"] == "gone" and i7["wo"]["status"] == "gone"
           and i7["unavailable"] is None,
           "PM item live：兩腿 404 各自翻 gone、整卡不算失敗")
        f8 = _FakeT(steps_reason="timeout", detail=det_raw)
        i8 = _with_pm(f8, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i8["steps"]["status"] == "error" and i8["wo"]["status"] == "ok",
           "PM item live：**兩腿獨立容忍**（細項掛了單況照顯）")
        ck(i8["steps"].get("reason") == "timeout",
           "PM item live：細項腿降級**原因上頁**（模板印 st.reason；只進 log ⇒ 使用者看到"
           "『載入失敗：』冒號後空白＝等於沒有降級卡）")
        f8b = _FakeT(steps=[], detail_reason="http_500")
        i8b = _with_pm(f8b, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i8b["wo"]["status"] == "error" and i8b["wo"].get("reason") == "http_500",
           "PM item live：單況腿降級原因同樣上頁（逾時 vs 5xx vs 形狀分歧要分得出來）")
        idx_nowo = dict(idx, last_wo=None)
        f9 = _FakeT(steps=[])
        i9 = _with_pm(f9, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx_nowo))
        ck(i9["wo"]["status"] == "none" and i9["steps"]["status"] == "empty",
           "PM item live：排程無 last_wo → `none`（沒單可拉，說 gone/off 都是謊）")
        idx_notask = dict(idx, task_id=None)
        f10 = _FakeT(detail=det_raw)
        i10 = _with_pm(f10, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx_notask))
        ck(i10["steps"]["status"] == "off" and i10["wo"]["status"] == "ok"
           and i10["unavailable"] is None
           and not any(isinstance(c, tuple) and c[0] == "steps" for c in f10.calls),
           "PM item live：無 task 參照 → off 且不打 S2（不謊稱 cmms 無細項）；單況腿照走")
        f12 = _FakeT(detail=None)                # transport 回 (None, None)＝形不符
        i12 = _with_pm(f12, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i12["wo"]["status"] == "error" and i12["unavailable"] is None
           and i12["wo"].get("reason") == "bad_shape",
           "PM item live：detail 回非 dict → 該腿 error＋reason=bad_shape（不硬解讀、不炸整卡）")
        f11 = _FakeT()
        i11 = _with_pm(f11, live=False,
                       fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i11["steps"]["status"] == "off" and i11["wo"]["status"] == "off"
           and f11.calls == [],
           "PM item live 閘：gated ⇒ 兩腿 off、零 HTTP")
        ck(get_pm_item("live", "PMD-01")["unavailable"] == "pm_unknown_id",
           "PM item live：無 idx_row ⇒ 誠實降級（**絕不回頭重拉整批 LIST**）")

        # ---- 展開卡：S2 真 fixture round-trip（prod v87；§8.2 兩條值域鐵則用真資料釘）----
        with open(os.path.join(_fx, "pm_board_fixture_task_steps_YPHYM.v1.json"),
                  encoding="utf-8") as fh:
            _st_y = json.load(fh)["response"]         # 交檔包裝 → wire 形（裸 list）
        with open(os.path.join(_fx, "pm_board_fixture_task_steps_CAL1DM.v1.json"),
                  encoding="utf-8") as fh:
            _st_a = json.load(fh)["response"]
        f13 = _FakeT(steps=_st_y, detail=det_raw)
        i13 = _with_pm(f13, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i13["steps"]["status"] == "ok" and len(i13["steps"]["rows"]) == 2,
           "PM item fixture YPHYM：兩步真回應完整過管線")
        ck(all(p["quantity"] is None
               for r in i13["steps"]["rows"] for p in r["parts"]),
           "PM item fixture YPHYM：`parts[].quantity` 一路 null 到模板（模板顯「—」）——"
           "**任何一層補 0 都是把「造冊未清點」謊報成「用量為零」**")
        f14 = _FakeT(steps=_st_a, detail=det_raw)
        i14 = _with_pm(f14, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(len(i14["steps"]["rows"]) == 8
           and len({r["proc_seq"] for r in i14["steps"]["rows"]}) == 1,
           "PM item fixture CAL1DM：八步 proc_seq 全標 10 仍是八列——**proc_seq 勿當唯一鍵**"
           "（拿它去重／當 dict 鍵會把整張 checklist 塌成一步）")
        ck([r["description"] for r in i14["steps"]["rows"]]
           == [r["description"] for r in _st_a],
           "PM item fixture CAL1DM：依回傳順序枚舉（server 已排序、client 不重排）")

        # ---- 用料人話名（S-PM6／§11 (c)；**fail-open 裝飾層**）------------------------
        #   YPHYM 的兩顆用料就是 fixture 裡的 XC910001/XC910002（cmms 特意選的同一組）。
        def _fresh_inames():
            globals()["_PM_INAMES"] = None       # 清掉半小時 TTL，讓每個情境各自打一次
        _fresh_inames()
        f15 = _FakeT(steps=_st_y, detail=det_raw)
        i15 = _with_pm(f15, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        _p15 = [p for r in i15["steps"]["rows"] for p in r["parts"]]
        ck(i15["steps"]["item_name_status"] == "ok" and "inv" in f15.calls
           and sorted(p["name"] for p in _p15) == ["Pump filter", "Pump oil"],
           f"PM item live：用料欄補上人話名（XC910001/XC910002 → Pump oil/Pump filter），"
           f"got {[p['name'] for p in _p15]}")
        ck(all(p["quantity"] is None for p in _p15),
           "PM item live：加了名字**不動值域**——quantity 仍是 null（模板顯「—」、不補 0）")
        _fresh_inames()
        f16 = _FakeT(steps=_st_y, detail=det_raw, inv_reason="timeout")
        i16 = _with_pm(f16, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        _p16 = [p for r in i16["steps"]["rows"] for p in r["parts"]]
        ck(i16["steps"]["status"] == "ok" and i16["unavailable"] is None
           and len(i16["steps"]["rows"]) == len(i15["steps"]["rows"])
           and all(p["name"] is None for p in _p16)
           and [p["item_code"] for p in _p16] == [p["item_code"] for p in _p15]
           and i16["steps"]["item_name_status"] == "timeout",
           "PM item live：★ 零件字典掛掉＝**fail-open**——步驟一列不少、用料退回顯 item_code、"
           "狀態自揭；**絕不因為字典失敗讓整張卡或整板降級**")
        _fresh_inames()

        class _FakeNoInv(_FakeT):
            """舊版 transport（沒有 fetch_inventory_items）＝同 /tasks 的最後一道 fail-open。"""
            fetch_inventory_items = None
        f17 = _FakeNoInv(steps=_st_y, detail=det_raw)
        i17 = _with_pm(f17, fn=lambda: get_pm_item("live", "PMD-01", idx_row=idx))
        ck(i17["steps"]["status"] == "ok"
           and i17["steps"]["item_name_status"] == "not_wired"
           and i17["unavailable"] is None,
           "PM item live：transport 沒有 fetch_inventory_items 也只降級名字（直接呼叫會炸成 "
           "error:AttributeError＝整張卡黑掉）")
        _fresh_inames()
        f18 = _FakeT(steps=_st_y, detail=det_raw)
        _with_pm(f18, live=False, fn=lambda: pm_item_names())
        ck(pm_item_names()[1] == "off" and "inv" not in f18.calls,
           "PM item：live 閘未開 ⇒ 字典腿 `off`、**零網路**（8/10 硬閘同一條線）")
        _fresh_inames()
        f19 = _FakeT()
        _n19 = _with_pm(f19, fn=lambda: pm_item_names())
        ck(_n19[0]["XC910003"] == "TIP ASSY, 3.2MM, TYPE B"
           and "XC910004" not in _n19[0],
           "PM item：字典走引擎的顯示鏈（name 空→description；雙空不入表 ⇒ 顯代號、不給連結）")
        _cached = _with_pm(_FakeT(inv_reason="timeout"), fn=lambda: pm_item_names())
        ck(_cached[1] == "ok" and _cached[0] == _n19[0],
           "PM item：字典有 TTL（半小時）——展開第二列不會再打一次 cmms")
        _fresh_inames()

    # ══════════════════════════════════════════════════════════════════════
    # A11 換源（`get_dormant_events`；SPEC-DORMANT-CAPACITY.md §6.1／§8）
    #   **全離線零 HTTP**：同樣直接換掉 `_W8T` 全域（fake 只認得那一支方法）。
    # ══════════════════════════════════════════════════════════════════════
    import datetime as _a11dt
    import board_dormant as _wdorm
    import board_scope as _wscope
    import board_capacity as _wcap
    # WP-10d（R-P11）：橋上兩台長停工位／有座機台由名冊常數推導（私有樹＝EID-26841、EID-26887／EID-26596）；
    #   名冊的具名計數與逐顆 EID 基線只在私有樹照釘。
    _D2, _D3 = (r[0] for r in _wdorm.DORMANT_BRIDGE)
    _sp = list(_wcap.NEST_EAV_UNITS)[1]
    _P1 = list(_wscope.PARENT_EXPANSION)[0]

    _A11_FX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_fixtures", "contract_asset_schedule_state.v1.json")
    with open(_A11_FX, encoding="utf-8") as fh:
        _a11_golden = json.load(fh)          # 裸 list 位元拷貝＝wire 形本身

    class _FakeA11:
        """假 transport 模組（只認得 A11 那一支；任何真連線都不可能發生）。"""
        def __init__(self, per_eid=None, reason=None, configured=True, missing=False):
            self.per_eid, self.reason = per_eid or {}, reason
            self._cfg, self._missing = configured, missing
            self.calls = []

        def is_configured(self):
            return self._cfg

        def fetch_schedule_state_events(self, asset_id, **kw):
            self.calls.append(asset_id)
            if self.reason is not None:
                return (None, self.reason)
            rows = self.per_eid.get(asset_id, [])
            if self._missing:
                rows = [{k: v for k, v in r.items() if k != "effective_date"} for r in rows]
                return (None, "missing_effective_date")
            return ({"schema_id": A11_SCHEMA_ID, "rows": list(rows), "count": len(rows)}, None)

    def _with_a11(fake, live=True, fn=None):
        """暫換 `_W8T` 與 `BOARD_A11_LIVE`（用完還原；並清 A11 快取＝情境彼此不污染）。
           ★ 兩條腿（事件流／批次讀）共用同一個閘與同一支 transport ⇒ 兩份快取一起清、一起還原。"""
        g = globals()
        old_t, old_tried = g["_W8T"], g["_W8T_TRIED"]
        old_env = os.environ.get(A11_LIVE_ENV)
        old_cache, old_fail = g["_A11_EVENTS"], g["_A11_FAIL_TS"]
        old_bcache, old_bfail = g["_A11_BATCH"], g["_A11_BATCH_FAIL_TS"]
        g["_W8T"], g["_W8T_TRIED"] = fake, True
        g["_A11_EVENTS"], g["_A11_FAIL_TS"] = None, None
        g["_A11_BATCH"], g["_A11_BATCH_FAIL_TS"] = None, None
        if live:
            os.environ[A11_LIVE_ENV] = "1"
        else:
            os.environ.pop(A11_LIVE_ENV, None)
        try:
            return fn()
        finally:
            g["_W8T"], g["_W8T_TRIED"] = old_t, old_tried
            g["_A11_EVENTS"], g["_A11_FAIL_TS"] = old_cache, old_fail
            g["_A11_BATCH"], g["_A11_BATCH_FAIL_TS"] = old_bcache, old_bfail
            if old_env is None:
                os.environ.pop(A11_LIVE_ENV, None)
            else:
                os.environ[A11_LIVE_ENV] = old_env

    # ---- 閘門（預設關）----
    fa0 = _FakeA11()
    ck(_with_a11(fa0, live=False, fn=get_dormant_events) == (None, "gated", None)
       and fa0.calls == [],
       "A11 閘：`BOARD_A11_LIVE` 未設 → (None,'gated',None)，且**一次 HTTP 都沒打**"
       "（連 transport 都不碰）")
    ck(_wdorm.is_dormant(_D2, _a11dt.date(2026, 8, 10), None) is True,
       "A11 閘 off 的意義：events=None ⇒ 投影走過渡橋常數＝**今天的行為逐位不變**")
    fa0b = _FakeA11(configured=False)
    _g0b = _with_a11(fa0b, fn=get_dormant_events)
    ck(_g0b[0] is None and _g0b[1] == "error:not_configured" and fa0b.calls == [],
       "A11：閘開但 cmms env 未設 → error:not_configured、零呼叫（頁面退橋＋揭露）")

    # ---- 名冊＝四段全部從既有常數組出（絕不手抄第二份）----
    import board_capacity as _wcap
    _a11r = a11_eids()
    # ⚠ `EID-25270` 退役（cmms 側已退役、實體移出產線）
    #   ⇒ 同時移出 `EQMT_CATALOG` 與 `A11_BASELINE_EIDS`。**退役 ≠ 長停**——長停講的是
    #   「還在線上、只是不排產」；把退役機留在覆蓋面等於每天向 cmms 問一台不存在的機器。
    # ⚠ AOI 機台退出 `NEST_EAV_UNITS`（C-LD46／
    #   `SPEC-LANDING.md` §18.30.4）⇒ 它們隨之退出覆蓋面。**退出座 ≠ 退役**：機台仍在
    #   `EQMT_CATALOG`，只是不再是「有座的機台」⇒ 與其餘無座機台同待遇。
    if not _pub:
        import board_private_pins as _pp
    ck((_pub or len(_a11r) == _pp.PINS["data.a11_rows_n"]) and len(set(_a11r)) == len(_a11r),
       f"A11 名冊：25270 退役＋AOI 退座後筆數符私有樹釘值、無重複，got {len(_a11r)}")
    ck(set(_wcap.NEST_EAV_UNITS) <= set(_a11r) and (_pub or len(_wcap.NEST_EAV_UNITS) == _pp.PINS["data.nest_eav_machines_n"]),
       "A11 名冊 ①：形① nest-EAV 母機全在覆蓋面內（**就是** board_capacity 那一份）")
    ck(set(_a11r).isdisjoint({"EID-09763", "EID-26161", "EID-38316"}),
       "A11 名冊 ①-b ★ C-LD46 負釘：AOI 機台退出子工位名冊後也不再在 A11 覆蓋面上")
    ck(set(_wcap.COTAG_CHILD_UNITS) <= set(_a11r)
       and {c for kids in _wcap.COTAG_CHILD_UNITS.values() for c in kids} <= set(_a11r),
       "A11 名冊 ②：形② 兩組的母機與子機全在覆蓋面內")
    ck(set(_wscope.PARENT_EXPANSION[_P1]) <= set(_a11r),
       "A11 名冊 ③：CAL Gen2 工位（原有覆蓋面）保留")
    # ⚠ 基線機清單因 `EID-25270` 退役而調整。負斷言同時釘住「退役機不得回鍋」。
    ck((_pub or tuple(_wdorm.A11_BASELINE_EIDS) == ("EID-26554", "EID-27163", "EID-27208",
                                                    "EID-25421", "EID-27639", "EID-27706"))
       and set(_wdorm.A11_BASELINE_EIDS) <= set(_a11r),
       "A11 名冊 ④：§11.4.1 基線機清單（碼側鏡射＝`board_dormant.A11_BASELINE_EIDS`）")
    ck("EID-25270" not in _a11r and "EID-25304" not in _a11r,
       "A11 名冊 ★ 負斷言：已退役的 AutoOQC#4／#5 不得回到覆蓋面")
    ck("EID-39940" not in _a11r, "退役機 Burn-in 不得進 A11 覆蓋面")
    ck(all(e not in _a11r for e in ("EID-27854", "EID-26379", "EID-34624",
                                    "EID-35641", "EID-37457", "EID-37549")),
       "A11 名冊 ★ 負斷言：§11.4.1 裁決**不登**的六顆永不入列（`27854`＝待命非長停）")
    ck(_D2 in _a11r and _D3 in _a11r
       and all(p not in _a11r for p in _wscope.PARENT_EXPANSION) and "EID-27617" not in _a11r,
       "A11 名冊：橋的兩筆都在覆蓋面內；A10 三個展開母機與 27617 永不入列")
    ck(all(m in _a11r for m in _wcap.NEST_EAV_UNITS)
       and (_pub or ("EID-26596" in _a11r and "EID-26718" in _a11r and "EID-12724" in _a11r)),
       "A11 名冊 ★ 承重：cmms 已登的四筆工位級長停掛在這三台母機上 —— 問不到就等於沒接線")

    # ---- 正常路徑：逐顆各打一次、adapter 對映 ----
    _per = {e: [] for e in a11_eids()}
    _per[_D2] = _a11_golden
    fa1 = _FakeA11(_per)
    ev1, st1, age1 = _with_a11(fa1, fn=get_dormant_events)
    # ⚠ 次數釘一律**動態**（覆蓋面會隨名冊變；上面那根 `len(_a11r) == 25` 才是量的閘）。
    ck(st1 == "live" and sorted(fa1.calls) == sorted(a11_eids())
       and len(fa1.calls) == len(a11_eids()),
       f"A11 live：覆蓋面逐顆各打一次（不多不少），got {len(fa1.calls)}")
    ck(len(ev1) == 2 and all(e["source"] == "cmms" for e in ev1)
       and {e["eid"] for e in ev1} == {_D2} and age1 == 0.0,
       f"A11 live：golden 兩筆事件掛在正確 EID 上、標記 source=cmms，got {ev1}")
    ck(ev1[0]["ts_effective"] == "2025-10-24" and ev1[0]["state"] == "not_scheduled"
       and ev1[0]["reason_zh"] == ev1[0]["reason_en"] == "線外查修,暫不排產",
       f"A11 adapter：ts_effective 欄餵的是 **effective_date**、reason 自由文字雙欄同值，"
       f"got {ev1[0]}")
    ck(ev1[1]["state"] == "in_production" and ev1[1]["reason_zh"] is None,
       "A11 adapter：復機事件與 null reason 原樣帶過（不補字、不分類）")
    # 空 [] ＝該資產全程排產中（不是查無資料）
    ck(_wdorm.is_dormant("EID-27250", _a11dt.date(2026, 8, 10), ev1) is False,
       "A11：零事件的工位 ⇒ 全程排產中（空 [] 不分兩路）")

    # ---- ★ G6 座級投影 golden（cmms NOTE-72 ①，2026-09-10 第四輪對版）----
    #   cmms 把登在**座資產**（母機 EID 加 `-N<n>` 後綴）上的 effective 事件，投影進母機
    #   `GET /assets/{母機}/schedule-state-events` 的串流，每筆 `sub_unit = "nest:<n>"`，
    #   其餘欄位原樣、與母機自己的事件合併後依 `ts_effective` 升冪。**零新欄、零 schema
    #   bump、三份既有 golden 逐位不變** ⇒ **Board 側零改碼**：本函式（`_a11_adapt`）早就搬
    #   `sub_unit`、兩條 lane 早就分好，本組只是把「這份 wire 真的吃得下」釘住。
    #   ⚠ `board_capacity.fold_seat_id` **不接這條路**：A11 的 row 沒有 asset id 欄。
    _A11_G6_FX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "_fixtures", "contract_asset_schedule_state.v1.seat_projection.json")
    with open(_A11_G6_FX, encoding="utf-8") as fh:
        _a11_g6 = json.load(fh)          # 裸 list 位元拷貝＝wire 形本身
    import board_w8_transport as _a11t
    _g6_res, _g6_rsn = _a11t.fetch_schedule_state_events(
        _sp,
        transport=lambda url: (_a11_g6, None) if url.endswith(
            "/assets/" + _sp + "/schedule-state-events") else (None, "http_404"))
    ck(_g6_rsn is None and _g6_res["schema_id"] == A11_SCHEMA_ID and _g6_res["count"] == 2
       and _g6_res["rows"] == _a11_g6
       and all(set(r) == {"ts_effective", "effective_date", "sub_unit", "state", "reason",
                          "recorded_at"} for r in _a11_g6),
       f"A11 G6：座級投影 golden 過**既有**形狀驗證（裸 list、六欄恆在、state 值域、"
       f"effective_date 在），got {_g6_rsn}")
    _g6_ev = [_a11_adapt(_sp, row) for row in _a11_g6]
    ck([e["sub_unit"] for e in _g6_ev] == ["nest:2", None],
       f"A11 G6 ★ 兩條 lane：第一筆＝座 lane `nest:2`（投影筆）、第二筆＝整機 lane `None`，"
       f"got {[e['sub_unit'] for e in _g6_ev]}")
    ck([e["eid"] for e in _g6_ev] == [_sp, _sp]
       and [e["ts_effective"] for e in _g6_ev] == ["2026-01-06", "2026-04-02"]
       and [e["state"] for e in _g6_ev] == ["not_scheduled", "not_scheduled"]
       and _g6_ev[1]["reason_zh"] == _g6_ev[1]["reason_en"] == "整機移機改裝,暫不排產"
       and all(e["source"] == "cmms" for e in _g6_ev),
       f"A11 G6 adapter：投影筆一樣掛在**母機** EID 上、ts_effective 仍餵 effective_date、"
       f"state／reason 原樣搬運，got {_g6_ev}")
    _g6_per = {e: [] for e in a11_eids()}
    _g6_per[_sp] = _a11_g6
    _ev6, _st6, _ = _with_a11(_FakeA11(_g6_per), fn=get_dormant_events)
    ck(_st6 == "live" and _ev6 == _g6_ev
       and _wdorm.is_dormant(_sp, _a11dt.date(2026, 5, 1), _ev6) is True
       and _wdorm.is_dormant(_sp, _a11dt.date(2026, 2, 1), _ev6) is False,
       f"A11 G6 全鏈：投影筆隨整機查詢一起回來，整機 lane 定調整機謂詞、座 lane 被 events_for "
       f"結構性濾掉（2/1 只有座停 ⇒ 整機仍在產），got {_st6}")

    # ---- ★ 結構釘：投影讀的是 effective_date，不是裸截 ts_effective（off-by-one 真實例）----
    _off = {e: [] for e in a11_eids()}
    _off[_D3] = [{"ts_effective": "2023-05-22T16:00:00Z",   # UTC 前 10 碼＝05-22
                          "effective_date": "2023-05-23",           # 台北生效日＝05-23
                          "state": "not_scheduled", "reason": "夜間登記",
                          "recorded_at": "2023-05-22T16:02:00Z"}]
    ev2, st2, _ = _with_a11(_FakeA11(_off), fn=get_dormant_events)
    ck(st2 == "live"
       and _wdorm.is_dormant(_D3, _a11dt.date(2023, 5, 23), ev2) is True
       and _wdorm.is_dormant(_D3, _a11dt.date(2023, 5, 22), ev2) is False,
       "A11 ★ off-by-one 結構釘：UTC 16:00 那筆的生效日是**台北 7/12**——7/12 長停、7/11 不是。"
       "若哪天有人改成截 ts_effective 前 10 碼（或補 +8h 換算），這條立刻紅")

    # ---- 任一 EID 失敗 ⇒ 整批不用（部分換源＝同頁混兩份真相）----
    class _FakeA11Partial(_FakeA11):
        def fetch_schedule_state_events(self, asset_id, **kw):
            self.calls.append(asset_id)
            if asset_id == _D3:
                return (None, "timeout")
            return ({"schema_id": A11_SCHEMA_ID, "rows": list(self.per_eid.get(asset_id, [])),
                     "count": 0}, None)
    fa3 = _FakeA11Partial(_per)
    ev3, st3, age3 = _with_a11(fa3, fn=get_dormant_events)
    ck(ev3 is None and st3 == "error:timeout" and age3 is None,
       f"A11 ★ 任一 EID 失敗 ⇒ **整批 None**（退橋＋揭露）。部分換源＝M2 讀到復機、M3 沿用橋，"
       f"分母是 1 還是 2 取決於哪次 HTTP 剛好失敗、而畫面完全正常，got {st3}")
    fa4 = _FakeA11(_per, missing=True)
    ck(_with_a11(fa4, fn=get_dormant_events)[1] == "error:missing_effective_date",
       "A11：缺 effective_date＝cmms 尚未部署 NOTE-31 補欄 ⇒ 整批退橋（絕不截 ts_effective）")

    # ---- TTL 命中 ＋ stale-serve ----
    def _ttl_case():
        fa = _FakeA11(_per)
        globals()["_W8T"], globals()["_W8T_TRIED"] = fa, True
        a = get_dormant_events()
        b = get_dormant_events()
        return (fa, a, b)
    fa5, a5, b5 = _with_a11(_FakeA11(_per), fn=_ttl_case)
    ck(len(fa5.calls) == len(a11_eids()) and b5[1] == "live" and b5[0] is a5[0],
       f"A11 TTL：30 分內第二次呼叫命中快取（不再打整輪 HTTP），got calls={len(fa5.calls)}")

    def _stale_case():
        good = _FakeA11(_per)
        globals()["_W8T"], globals()["_W8T_TRIED"] = good, True
        first = get_dormant_events()
        globals()["_A11_EVENTS"] = (time.monotonic() - A11_TTL - 1.0, first[0])  # 人工過期
        globals()["_W8T"] = _FakeA11(_per, reason="unreachable")
        return (first, get_dormant_events())
    f_first, f_stale = _with_a11(_FakeA11(_per), fn=_stale_case)
    ck(f_stale[1] == "live_stale" and f_stale[0] == f_first[0] and f_stale[2] > A11_TTL,
       f"A11 stale-serve：來源暫掛 ⇒ **供舊值**＋標 live_stale＋帶 age（≤48h），got {f_stale[1]}")

    def _stale_expired():
        globals()["_W8T"], globals()["_W8T_TRIED"] = _FakeA11(_per), True
        first = get_dormant_events()
        globals()["_A11_EVENTS"] = (time.monotonic() - A11_STALE_MAX - 1.0, first[0])
        globals()["_W8T"] = _FakeA11(_per, reason="unreachable")
        return get_dormant_events()
    ck(_with_a11(_FakeA11(_per), fn=_stale_expired) == (None, "error:unreachable", None),
       "A11 stale 上限：超過 48h 的舊值不再供應 ⇒ 退橋（陳到某個程度就不再是事實）")

    # ---- peek（warm-only；Hermes ctx 專用）：**零 HTTP、絕不觸發取數** ----
    def _peek_miss():
        fa = _FakeA11(_per)
        globals()["_W8T"], globals()["_W8T_TRIED"] = fa, True
        return (fa, peek_dormant_events())
    fa6, pk6 = _with_a11(_FakeA11(_per), fn=_peek_miss)
    ck(pk6 == (None, None, None) and fa6.calls == [],
       f"A11 peek：快取未命中 ⇒ (None,None,None) 且**一次呼叫都沒有**——ctx 組裝掛在使用者"
       f"送出訊息的關鍵路徑上，絕不為一段摘要去打 26 次 cmms，got {pk6} calls={fa6.calls}")

    def _peek_hit():
        fa = _FakeA11(_per)
        globals()["_W8T"], globals()["_W8T_TRIED"] = fa, True
        got = get_dormant_events()                     # 板面路徑：真取數（26 次）
        n_after_fetch = len(fa.calls)
        pk = peek_dormant_events()                     # peek：不得再打
        return (fa, got, n_after_fetch, pk, globals()["_A11_EVENTS"])
    fa7, got7, n7, pk7, cache7 = _with_a11(_FakeA11(_per), fn=_peek_hit)
    ck(n7 == len(a11_eids()) and len(fa7.calls) == n7
       and pk7[1] == "live" and pk7[0] is got7[0],
       f"A11 peek：命中 ⇒ 回**同一份**事件、狀態 live，且**不觸發重抓**"
       f"（呼叫數仍是取數那一輪的次數），got calls={len(fa7.calls)} status={pk7[1]}")
    ck(cache7 is not None and cache7[1] is got7[0],
       "A11 peek：**不寫快取、不留痕跡**（板面路徑的取數節奏不受 peek 影響）")

    def _peek_stale():
        fa = _FakeA11(_per)
        globals()["_W8T"], globals()["_W8T_TRIED"] = fa, True
        first = get_dormant_events()
        globals()["_A11_EVENTS"] = (time.monotonic() - A11_TTL - 1.0, first[0])   # TTL 過期
        pk_stale = peek_dormant_events()
        globals()["_A11_EVENTS"] = (time.monotonic() - A11_STALE_MAX - 1.0, first[0])
        pk_dead = peek_dormant_events()
        return (fa, pk_stale, pk_dead)
    fa8, pk_stale, pk_dead = _with_a11(_FakeA11(_per), fn=_peek_stale)
    ck(pk_stale[1] == "live_stale" and pk_stale[0] is not None
       and pk_dead == (None, None, None) and len(fa8.calls) == len(a11_eids()),
       f"A11 peek：TTL 過期仍供舊值（live_stale）、超過 48h 視同未命中；"
       f"**全程零重抓**，got calls={len(fa8.calls)}")

    def _peek_gated():
        fa = _FakeA11(_per)
        globals()["_W8T"], globals()["_W8T_TRIED"] = fa, True
        get_dormant_events()                            # 先暖起來（閘開）
        os.environ.pop(A11_LIVE_ENV, None)              # 再關閘
        return (fa, peek_dormant_events())
    fa9, pk9 = _with_a11(_FakeA11(_per), fn=_peek_gated)
    ck(pk9 == (None, None, None),
       "A11 peek：閘關 ⇒ 即使快取是熱的也回未命中（與板面 `gated` 同義＝走橋常數）")

    # ---- transport 舊版（沒有這支）＝fail-open 退橋，不是 AttributeError ----
    class _FakeNoA11(_FakeA11):
        fetch_schedule_state_events = None
    ck(_with_a11(_FakeNoA11(), fn=get_dormant_events)[1] == "error:not_wired",
       "A11：transport 沒有這支（舊版模組）⇒ error:not_wired 退橋，**不炸成 AttributeError**")

    class _FakeBoom(_FakeA11):
        def fetch_schedule_state_events(self, asset_id, **kw):
            raise RuntimeError("boom")
    ck(_with_a11(_FakeBoom(), fn=get_dormant_events)[1].startswith("error:"),
       "A11：任何例外都收斂成 error:*（**恆不 raise**——換源腿永遠不得炸掉 /equipment）")

    # ══════════════════════════════════════════════════════════════════════
    # A11 批次現況讀（`get_schedule_state`／`schedule_state_dormant`；SPEC §6.2）
    #   板面著色的第二條腿：**批次答「誰」**（現況、整份名冊），歷史仍走上面那條事件流。
    #   ⚠ golden 第一筆的 `since:"2025-10-24"` 是**被 cmms 自己否定過的合成值**
    #     （真值＝2023-05-23／2023-05-11，§9b）——本段一律**當結構不當事實**用。
    # ══════════════════════════════════════════════════════════════════════
    _A11B_FX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_fixtures", "contract_asset_schedule_state_batch.v1.json")
    with open(_A11B_FX, encoding="utf-8") as fh:
        _a11b_golden = json.load(fh)         # 信封位元拷貝＝wire 形本身
    _B_EIDS = [i["asset_id"] for i in _a11b_golden["items"]] + _a11b_golden["unknown"]

    class _FakeA11B:
        """假 transport（只認得批次讀那一支）。`payload=None` ⇒ 依 `reason` 回失敗。"""
        def __init__(self, payload=None, reason=None, configured=True, missing=False):
            self.payload, self.reason = payload, reason
            self._cfg, self._missing = configured, missing
            self.calls = []

        def is_configured(self):
            return self._cfg

        def fetch_schedule_state_batch(self, eids=None, **kw):
            self.calls.append((tuple(eids or []), kw.get("as_of")))
            if self.reason is not None:
                return (None, self.reason)
            items = list((self.payload or {}).get("items") or [])
            unk = list((self.payload or {}).get("unknown") or [])
            return ({"schema_id": A11_BATCH_SCHEMA_ID, "items": items, "unknown": unk,
                     "count": len(items), "unknown_n": len(unk), "pages": 1,
                     "explicit": True}, None)

    # ---- 閘門（與事件流同一個閘；關＝零 HTTP）----
    fb0 = _FakeA11B(_a11b_golden)
    ck(_with_a11(fb0, live=False, fn=lambda: get_schedule_state(_B_EIDS))
       == (None, None, "gated", None) and fb0.calls == [],
       "A11 batch 閘：`BOARD_A11_LIVE` 未設 → gated 且**一次呼叫都沒有**（板面退回既有覆蓋面）")
    fb0b = _FakeA11B(_a11b_golden, configured=False)
    _b0b = _with_a11(fb0b, fn=lambda: get_schedule_state(_B_EIDS))
    ck(_b0b == (None, None, "error:not_configured", None) and fb0b.calls == [],
       f"A11 batch：閘開但 cmms env 未設 → error:not_configured、零呼叫，got {_b0b[2]}")

    # ---- ★ 空名冊：零網路短路，**絕不退化成「問全機隊」** ----
    fb_e = _FakeA11B(_a11b_golden)
    ck(_with_a11(fb_e, fn=lambda: get_schedule_state([])) == (None, None, "error:empty_eids", None)
       and fb_e.calls == [],
       "A11 batch ★：名冊空 ⇒ error:empty_eids、**連 transport 都不碰**——呼叫端名冊載不出來時"
       "安靜地改問 cmms 全部資產長得完全正常（兩種模式必須由呼叫端明示，不得由資料推導）")

    # ---- 正常路徑：golden round-trip＋unknown 原樣透傳 ----
    fb1 = _FakeA11B(_a11b_golden)
    st_b, unk_b, stat_b, age_b = _with_a11(fb1, fn=lambda: get_schedule_state(_B_EIDS))
    ck(stat_b == "live" and age_b == 0.0 and len(fb1.calls) == 1
       and fb1.calls[0][0] == tuple(_B_EIDS),
       f"A11 batch live：**一次呼叫問完整份名冊**（不是 per-EID 迴圈），got {fb1.calls}")
    ck(set(st_b) == {"EID-09670", "EID-27639", "EID-27706"}
       and st_b["EID-09670"]["state"] == "not_scheduled"
       and st_b["EID-27639"]["state"] == "in_production",
       f"A11 batch：items → dict 查表（**不依賴 cmms 的 EID 升冪**），got {sorted(st_b)}")
    ck(unk_b == ["EID-99999"],
       f"A11 batch ★：`unknown` **原樣透傳**（是錯誤不是雜訊：兩邊名冊漂了要在頁上紅字報），"
       f"got {unk_b}")

    # ---- ★ 消費紀律③：先讀 state 再讀 since（復機那筆的 since 是**復機日**）----
    _dm = schedule_state_dormant(st_b)
    ck(set(_dm) == {"EID-09670"} and _dm["EID-09670"]["since"] == "2025-10-24"
       and _dm["EID-09670"]["reason_zh"] == _dm["EID-09670"]["reason_en"] == "線外查修,暫不排產",
       f"A11 batch ★：`schedule_state_dormant` 只收 not_scheduled——`EID-27639` 帶著 since "
       f"2026-03-02 但那是**復機日**，把 since 一律讀成「長停自」會讓在產的機器顯成長停，got {_dm}")
    ck(set(_dm["EID-09670"]) == {"since", "reason_zh", "reason_en"},
       "A11 batch：形狀對齊 `wdorm.dormant_info`（呼叫端收哪條腿來的都長一樣、模板零分叉）")
    ck(schedule_state_dormant(None) == {} and schedule_state_dormant({"E": "x"}) == {},
       "A11 batch：None／髒值 ⇒ 空 dict（不炸、不猜）")

    # ---- 亂序不影響（結構釘：我方既不排序、也不要求排序）----
    _shuf = {"items": list(reversed(_a11b_golden["items"])), "unknown": ["EID-99999"]}
    _st_s, _unk_s, _, _ = _with_a11(_FakeA11B(_shuf), fn=lambda: get_schedule_state(_B_EIDS))
    ck(_st_s == st_b and _unk_s == unk_b,
       "A11 batch：餵亂序 items 照樣得到**逐位相同**的查表結果（把升冪當契約＝日後對方一個"
       "良性排序改動就讓板面死掉）")

    # ---- 失敗 ⇒ 整批不用（含 incomplete_scope：cmms 退回省流版時傳輸層就紅）----
    for _rs in ("incomplete_scope", "timeout", "http_404", "bad_schema"):
        _bf = _with_a11(_FakeA11B(reason=_rs), fn=lambda: get_schedule_state(_B_EIDS))
        ck(_bf == (None, None, f"error:{_rs}", None),
           f"A11 batch：{_rs} ⇒ 整批 None（板面退回既有覆蓋面、頁面不炸），got {_bf[2]}")

    class _FakeA11BBoom(_FakeA11B):
        def fetch_schedule_state_batch(self, eids=None, **kw):
            raise RuntimeError("boom")
    ck(_with_a11(_FakeA11BBoom(), fn=lambda: get_schedule_state(_B_EIDS))[2].startswith("error:"),
       "A11 batch：任何例外都收斂成 error:*（**恆不 raise**——著色腿永遠不得炸掉 /equipment）")

    class _FakeNoA11B(_FakeA11B):
        fetch_schedule_state_batch = None
    ck(_with_a11(_FakeNoA11B(), fn=lambda: get_schedule_state(_B_EIDS))[2] == "error:not_wired",
       "A11 batch：transport 沒有這支（舊版模組）⇒ error:not_wired，**不炸成 AttributeError**")

    # ---- TTL 命中 ＋ 名冊變動即重取 ＋ stale-serve ----
    def _b_ttl():
        fa = _FakeA11B(_a11b_golden)
        globals()["_W8T"], globals()["_W8T_TRIED"] = fa, True
        a = get_schedule_state(_B_EIDS)
        b = get_schedule_state(_B_EIDS)
        c = get_schedule_state(_B_EIDS[:2])          # 名冊變了 ⇒ 不得吃上一份答案
        return (fa, a, b, c)
    fb5, a5b, b5b, c5b = _with_a11(_FakeA11B(_a11b_golden), fn=_b_ttl)
    ck(len(fb5.calls) == 2 and b5b[2] == "live" and b5b[0] is a5b[0] and c5b[2] == "live",
       f"A11 batch TTL：同名冊第二次命中快取；**名冊一變即重取**（否則切名冊會拿到別人的答案），"
       f"got calls={len(fb5.calls)}")

    def _b_stale():
        good = _FakeA11B(_a11b_golden)
        globals()["_W8T"], globals()["_W8T_TRIED"] = good, True
        first = get_schedule_state(_B_EIDS)
        globals()["_A11_BATCH"] = (time.monotonic() - A11_TTL - 1.0,
                                   globals()["_A11_BATCH"][1], globals()["_A11_BATCH"][2])
        globals()["_W8T"] = _FakeA11B(reason="unreachable")
        return (first, get_schedule_state(_B_EIDS))
    _bf1, _bst = _with_a11(_FakeA11B(_a11b_golden), fn=_b_stale)
    ck(_bst[2] == "live_stale" and _bst[0] == _bf1[0] and _bst[1] == _bf1[1]
       and _bst[3] > A11_TTL,
       f"A11 batch stale-serve：來源暫掛 ⇒ 供舊值＋標 live_stale＋帶 age（≤48h），got {_bst[2]}")

    def _b_stale_dead():
        globals()["_W8T"], globals()["_W8T_TRIED"] = _FakeA11B(_a11b_golden), True
        get_schedule_state(_B_EIDS)
        globals()["_A11_BATCH"] = (time.monotonic() - A11_STALE_MAX - 1.0,
                                   globals()["_A11_BATCH"][1], globals()["_A11_BATCH"][2])
        globals()["_W8T"] = _FakeA11B(reason="unreachable")
        return get_schedule_state(_B_EIDS)
    ck(_with_a11(_FakeA11B(_a11b_golden), fn=_b_stale_dead)
       == (None, None, "error:unreachable", None),
       "A11 batch stale 上限：超過 48h 的舊值不再供應 ⇒ 退回既有覆蓋面")

    # ---- 冷失敗抑制窗（不讓每個 request 都重打）----
    def _b_supp():
        fa = _FakeA11B(reason="unreachable")
        globals()["_W8T"], globals()["_W8T_TRIED"] = fa, True
        a = get_schedule_state(_B_EIDS)
        b = get_schedule_state(_B_EIDS)
        return (fa, a, b)
    fb7, _a7, _b7 = _with_a11(_FakeA11B(reason="unreachable"), fn=_b_supp)
    ck(len(fb7.calls) == 1 and _b7[2] == "error:recent_fail",
       f"A11 batch：冷失敗 2 分鐘抑制窗（每個 request 重打一次 HTTP＝把慢的來源變成更慢的頁面），"
       f"got calls={len(fb7.calls)}")

    # ---- 負斷言（結構釘）：本檔 A11 段零 +8h 換算 ----
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        _wd_src = fh.read()
    _nh8 = "hours=" + str(8) + ")"
    ck(_nh8 not in _wd_src and ("timedelta(" + _nh8) not in _wd_src,
       "A11 負斷言：資料層零 +8h 換算（cmms 已把台北日曆日算好放 effective_date、我們只搬運）")

    # ---- 該跑窗兩個綑級 raw 的 loader（SPEC-RUNWINDOW §5；absent＝常態）----
    _rw_tmp = tempfile.mkdtemp(prefix="board_kunraw_selftest_")
    _p_absent = os.path.join(_rw_tmp, "nope.json")
    ck(load_kun_manpower(_p_absent) == (None, "absent")
       and load_kun_slots(_p_absent) == (None, "absent"),
       "kun raw loader：缺檔 ⇒ (None,'absent')＝常態、零警語")
    _p_bad = os.path.join(_rw_tmp, "bad.json")
    with open(_p_bad, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    ck(load_kun_manpower(_p_bad)[1] == "invalid", "kun raw loader：壞 JSON ⇒ invalid")
    _p_old = os.path.join(_rw_tmp, "old.json")
    with open(_p_old, "w", encoding="utf-8") as fh:
        json.dump({"by_prod_day": {"2026-08-01": {}}}, fh)   # 舊格式：無 slot 軌
    ck(load_kun_slots(_p_old)[1] == "invalid",
       "kun slots loader：舊格式 raw（無 by_prod_day_slots）⇒ invalid（擴窗 ①② 需要位圖）")
    _p_ok = os.path.join(_rw_tmp, "ok.json")
    with open(_p_ok, "w", encoding="utf-8") as fh:
        json.dump({"by_prod_day_shift": {"2026-08-01": {"ZB-2": {"day": 1}}}}, fh)
    _d_ok, _st_ok = load_kun_manpower(_p_ok)
    ck(_st_ok == "ok" and "2026-08-01" in _d_ok["by_prod_day_shift"],
       "kun manpower loader：合法檔 ⇒ (doc,'ok')")
    ck(_raw_stamp(_p_ok) is not None and _raw_stamp(_p_absent) is None,
       "kun raw stamp：(mtime,size)；缺檔 None 絕不 raise")

    # ══════════════════════════════════════════════════════════════════════
    # 產品 family 活動層（M3 C2）——**全離線零 MES**：寫腿注入假 `mes_family` 模組
    #   （`_MF` 全域直接換掉，fake 只認得 `open_connection`／`run`／`FAMILIES`）、
    #   讀腿餵合成 v3/v4 假 doc。
    # ══════════════════════════════════════════════════════════════════════
    # ---- 寫腿（fetch_day_families_live）：狀態機四型 ＋ None≠{} ----------------
    _fws, _fwe = datetime(2026, 9, 1), datetime(2026, 9, 2)

    class _FakeConn:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class _FakeMF:
        """假 `mes_family`（形同真模組的 `open_connection`／`run`）。"""
        FAMILIES = ("Plover", "Kestrel", "Heron")

        def __init__(self, by_cls=None, boom=None, slow=0.0):
            self.by_cls = dict(by_cls or {})
            self.boom = boom
            self.slow = slow
            self.calls = []
            self.conns = []
            self.timeouts = []

        def open_connection(self, timeout=None):
            self.timeouts.append(timeout)
            c = _FakeConn()
            self.conns.append(c)
            return c, object()

        def run(self, cls, win, cur, stats=None):
            self.calls.append((cls, win))
            if self.boom is not None and cls == self.boom:
                raise RuntimeError("boom")
            if self.slow:
                time.sleep(self.slow)
            if isinstance(stats, dict):
                stats["timing"] = {"total_ms": 1}
            return dict(self.by_cls.get(cls) or {})

    def _with_mf(fake, fn):
        _o, _ot = globals()["_MF"], globals()["_MF_TRIED"]
        globals()["_MF"], globals()["_MF_TRIED"] = fake, True
        try:
            return fn()
        finally:
            globals()["_MF"], globals()["_MF_TRIED"] = _o, _ot

    _fam_a = {"segDiePlacement": {"EID-1": {"Plover": 3}},
              "segWireBonding": {}}
    _mf1 = _FakeMF(_fam_a)
    _st1 = {}
    _r1 = _with_mf(_mf1, lambda: fetch_day_families_live(
        _fws, _fwe, stations=["segDiePlacement", "segWireBonding"], stats=_st1))
    ck(_r1 == {"segDiePlacement": {"EID-1": {"Plover": 3}}, "segWireBonding": {}},
       f"families 寫腿：回 {{cls: {{eid: {{family: n}}}}}}；★ 查過零段的站**恆帶 `{{}}`**"
       f"（沒查＝鍵不存在），got {_r1}")
    ck(_st1["status"] == "ok" and _st1["n_queried"] == 2 and _st1["n_stations"] == 2,
       f"families 寫腿：stats.status='ok'＋逐站計數（doc 級 families_status 直接用它），got {_st1}")
    ck(_st1["n_eid_rows"] == 1 and _st1["n_segments"] == 3,
       "families 寫腿：段數/機台列自揭")
    ck(len(_mf1.conns) == 1 and _mf1.conns[0].closed,
       "families 寫腿：**一條連線跑完全部站**且必定關掉（43 站各開一條＝43 次握手）")
    ck(_mf1.timeouts == [30],
       f"families 寫腿：連線逾時＝ceil(預算)=30s（不是 LIVE_QUERY_TIMEOUT_SEC 的 180），"
       f"got {_mf1.timeouts}")

    _st2 = {}
    _r2f = _with_mf(_FakeMF(_fam_a, boom="segWireBonding"), lambda: fetch_day_families_live(
        _fws, _fwe, stations=["segDiePlacement", "segWireBonding"], stats=_st2))
    ck(_r2f is None and _st2["status"] == "error:RuntimeError",
       f"families 寫腿：任一站爆炸 ⇒ **整批 None**＋error:<T>（半盤在這個介面無法自我描述），"
       f"got {_r2f!r}/{_st2.get('status')}")

    _st3 = {}
    _r3f = _with_mf(_FakeMF(_fam_a, slow=0.02), lambda: fetch_day_families_live(
        _fws, _fwe, stations=["segDiePlacement", "segWireBonding", "segGrout"],
        budget_sec=0.03, stats=_st3))
    ck(_r3f is None and _st3["status"] == "skipped:budget" and _st3["n_queried"] >= 1,
       f"families 寫腿：預算用盡 ⇒ **None**＋skipped:budget（★ 不回半盤——半盤會讓沒查的站"
       f"被寫成 `{{}}`＝把「不知道」畫成「閒著」，C-PT3），got {_r3f!r}/{_st3.get('status')}")

    _st4 = {}
    ck(_with_mf(None, lambda: fetch_day_families_live(_fws, _fwe, stats=_st4)) is None
       and _st4["status"] == "skipped:module_missing",
       "families 寫腿：mes_family 模組缺 ⇒ None＋skipped:module_missing（誠實降級、不炸）")
    _st5 = {}
    ck(_with_mf(_FakeMF(_fam_a), lambda: fetch_day_families_live(
        None, _fwe, stats=_st5)) is None and _st5["status"] == "skipped:no_window",
       "families 寫腿：無窗 ⇒ None＋skipped:no_window")
    _st6 = {}
    ck(_with_mf(_FakeMF(_fam_a), lambda: fetch_day_families_live(
        _fws, _fwe, stations=[], stats=_st6)) is None
       and _st6["status"] == "skipped:no_stations",
       "families 寫腿：空站清單 ⇒ None（不靜默回 {}）")
    _mf7 = _FakeMF(_fam_a)
    _with_mf(_mf7, lambda: fetch_day_families_live(_fws, _fwe, stations=None))
    ck([c[0] for c in _mf7.calls] == list(mt.EQP_STATIONS),
       "families 寫腿：stations 未給 ⇒ mes_timeline.EQP_STATIONS（永不 runtime discovery）")
    _mf8 = _FakeMF(_fam_a)
    _with_mf(_mf8, lambda: fetch_day_families_live(
        _fws, _fwe, stations=["segDiePlacement", "segDiePlacement", None, "", 7]))
    ck([c[0] for c in _mf8.calls] == ["segDiePlacement"],
       f"families 寫腿：站清單去重＋只收字串（順序保留），got {[c[0] for c in _mf8.calls]}")
    ck(family_budget_sec() == FAMILY_BUDGET_SEC == 30.0,
       "families 預算：env 未設 ⇒ 30s")
    _oe = os.environ.get(FAMILY_BUDGET_ENV)
    try:
        os.environ[FAMILY_BUDGET_ENV] = "not-a-number"
        ck(family_budget_sec() == 30.0, "families 預算：壞 env 值 ⇒ 退回預設（不炸）")
        os.environ[FAMILY_BUDGET_ENV] = "12.5"
        ck(family_budget_sec() == 12.5, "families 預算：env 覆寫生效")
    finally:
        if _oe is None:
            os.environ.pop(FAMILY_BUDGET_ENV, None)
        else:
            os.environ[FAMILY_BUDGET_ENV] = _oe

    # ---- 讀腿（family_activity_from_docs）：v3/v4 混餵、None≠{}、stations_seen ----
    def _doc(day, ver, stations_rows, status=None):
        d = {"schema_ver": f"board.daily_ledger.{ver}", "day": day, "stations": stations_rows}
        if ver == "v4":
            d["families_status"] = status
        return d

    def _srow(cls, fams):
        return {"cls": cls, "families": fams}

    _docs = [
        _doc("2026-09-03", "v4", [_srow("segDiePlacement", {"EID-1": {"Plover": 2},
                                                         "EID-2": {"Kestrel": 5}}),
                                  _srow("segWireBonding", {})], status="ok"),
        _doc("2026-09-02", "v4", [_srow("segDiePlacement", {"EID-1": {"Plover": 1,
                                                                   "Heron": 4}}),
                                  {"cls": "segGrout", "families": None}], status="ok"),
        _doc("2026-09-01", "v3", [{"cls": "segDiePlacement"}]),          # v3：貢獻零
        "not-a-dict",
    ]
    _act, _meta = family_activity_from_docs(_docs, days=7, end_day="2026-09-03")
    ck(_act["EID-1"]["families"] == {"Heron": 4, "Plover": 3},
       f"families 讀腿：近窗逐 family 相加（鍵序＝段數多的在前），got {_act['EID-1']['families']}")
    ck(_act["EID-1"]["days"] == 2 and _act["EID-1"]["as_of"] == "2026-09-03",
       "families 讀腿：days＝有資料的天數；as_of＝最後一次有資料那天")
    ck(_act["EID-2"] == {"families": {"Kestrel": 5}, "days": 1, "as_of": "2026-09-03"},
       "families 讀腿：逐 EID 獨立（不互相污染）")
    ck(list(_act) == ["EID-1", "EID-2"], "families 讀腿：EID 鍵序升冪（確定性）")
    ck(_meta["stations_seen"] == ["segDiePlacement", "segWireBonding"],
       f"families 讀腿：★ `families=={{}}` 的站**進** stations_seen（查過零段）、"
       f"`None` 的站**不進**（沒查）——chip 的 None≠False 全靠這條，got {_meta['stations_seen']}")
    ck(_meta["as_of"] == "2026-09-03" and _meta["days_seen"] == ["2026-09-02", "2026-09-03"],
       "families 讀腿：meta.as_of／days_seen＝窗內真的有 family 資料的日子")
    ck(_meta["n_docs"] == 3 and _meta["n_docs_with_families"] == 2,
       f"families 讀腿：v3 檔計入 n_docs 但不貢獻 families（n_with=2）；壞 doc 直接跳過，"
       f"got {_meta['n_docs']}/{_meta['n_docs_with_families']}")
    ck(_meta["statuses"].get("ok") == 2 and _meta["statuses"].get("none") == 1,
       f"families 讀腿：doc 級 families_status 分桶自揭（v3＝none），got {_meta['statuses']}")
    _e_act, _e_meta = family_activity_from_docs([])
    ck(_e_act == {} and _e_meta["n_docs_with_families"] == 0
       and _e_meta["as_of"] is None and _e_meta["stations_seen"] == [],
       "families 讀腿：零 doc ⇒ 空活動＋stations_seen 空（＝全體『活動未知』，不是全體閒置）")
    _v3_act, _v3_meta = family_activity_from_docs(
        [_doc("2026-09-01", "v3", [{"cls": "segDiePlacement"}])])
    ck(_v3_act == {} and _v3_meta["stations_seen"] == [],
       "families 讀腿：★ 全 v3 窗 ⇒ stations_seen 空（v4 還沒上線的日子不得被讀成『都沒在跑』）")
    _junk = family_activity_from_docs([
        {"day": "2026-09-03", "stations": ["x", None, 7,
                                           {"cls": None, "families": {"EID-9": "bad"}},
                                           {"cls": "segGrout",
                                            "families": {"EID-9": {"Plover": True,
                                                                   "Heron": "3",
                                                                   "Kestrel": 2}}}]},
        {"stations": None}, {"day": 7}, None])
    ck(_junk[0] == {"EID-9": {"families": {"Kestrel": 2}, "days": 1,
                              "as_of": "2026-09-03"}}
       and _junk[1]["stations_seen"] == ["segGrout"],
       f"families 讀腿：壞列/壞值（bool、字串、None cls）全跳過、好值照收、**不炸**，got {_junk[0]}")
    _noday = family_activity_from_docs(
        [{"stations": [_srow("segGrout", {"EID-9": {"Plover": 1}})]}])
    ck(_noday[0]["EID-9"]["as_of"] is None and _noday[0]["EID-9"]["days"] == 0
       and _noday[1]["as_of"] is None,
       "families 讀腿：doc 沒有 day ⇒ as_of None／days 0（不編一個日期出來）")
    ck(FAMILY_DAYS_FALLBACK == 7,
       "families 讀腿：窗 fallback＝7 天（唯一出處在呈現層 board_tagui.RECENT_DAYS）")
    import inspect as _insp
    ck("\nimport mes_family" not in _wd_src and "\nfrom mes_family" not in _wd_src,
       "families：★ 全檔零**頂層** import mes_family（只在 `family_module()` 內縮排 import）"
       "＝`import board_data` 不得把它帶進 sys.modules（run_smoke 有同款釘）")
    ck("rw_meta" in _insp.signature(persist_ledger).parameters,
       "persist_ledger：v4 doc 級三鍵走 `rw_meta=`（呼叫端主張、本層不反推）")
    ck(_doc_v4_meta(None) == {k: None for k in _DOC_V4_META_KEYS},
       "persist_ledger：rw_meta 未給 ＋ 無既有檔 ⇒ 三鍵全 None＝逐位元原行為")
    ck(_doc_v4_meta({"rw_axis": True, "families_status": "ok", "n_stations": 43})
       == dict({k: None for k in _DOC_V4_META_KEYS}, rw_axis=True, families_status="ok"),
       "persist_ledger：rw_meta 白名單過濾（未知鍵忽略而不是炸掉落盤）")
    _wu_pin = unit_module()
    ck(_wu_pin is None
       or _DOC_V4_META_KEYS == _wu_pin._DOC_V4_KEYS + _wu_pin._DOC_V8_KEYS,
       "persist_ledger：★ doc 級鍵名 cross-pin board_unit._DOC_V4_KEYS＋_DOC_V8_KEYS"
       "（不抄第二份鍵名；#182-P2 v8 五鍵走同一條 rw_meta／carry-forward 路）")

    # ── doc 級三鍵 carry-forward（2026-09-04 HIGH；顆數腿不得抹掉 attrib 腿的主張）──────
    #   病理：`get_unit_day` 走 `persist_ledger(attrib, unit_board=…)`＝**不帶 rw_meta**，
    #   且它通常是同一天的最後一個寫者 ⇒ 舊行為把三鍵寫回 None ⇒ 落地檔恆 `rw_axis: null`
    #   ⇒ `should_overwrite` 的自癒只認 `False`（None 刻意不放行）⇒ 永久停在 null
    #   ⇒ `board_monthly.coverage.n_days_rw` 恆 0 ⇒ 月報自動凍結永遠 `rw_incomplete`。
    _ex_v4 = {"rw_axis": True, "rw_reason": "ok", "families_status": "skipped:budget"}
    _N8 = {k: None for k in _DOC_V4_META_KEYS}          # v8 五鍵在舊檔上＝None（未知）
    ck(_doc_v4_meta(None, dict(_ex_v4, schema_ver="board.daily_ledger.v4", stations=[]))
       == dict(_N8, **_ex_v4),
       "(a) rw_meta 未給 ⇒ 三鍵**逐鍵沿用 existing**（保留前一位呼叫端的主張、不反推）")
    ck(_doc_v4_meta({"rw_axis": False, "rw_reason": "calendar_absent"}, _ex_v4)
       == dict(_N8, rw_axis=False, rw_reason="calendar_absent"),
       "(b) rw_meta 給了 ⇒ **整份**以呼叫端為準；呼叫端沒寫的鍵＝主張 None，不回頭撿 existing")
    ck(_doc_v4_meta(None, None) == _N8
       and _doc_v4_meta(None, "not-a-dict") == _N8
       and _doc_v4_meta(None, {"schema_ver": "board.daily_ledger.v3"}) == _N8,
       "(c) 無 existing／壞 existing／v3 舊檔（沒有這三鍵）⇒ 三鍵 None＝現況不變")

    if unit_module() is not None and attrib_module() is not None:
        import shutil as _shp
        import tempfile as _tfp
        _pl_tmp = _tfp.mkdtemp(prefix="board_v4meta_selftest_")
        _pl_real = globals()["ledger_dir"]
        try:
            globals()["ledger_dir"] = lambda: _pl_tmp
            _pl_day = "2026-08-11"
            _pl_board = dict(attrib_module().demo_board(day=_pl_day), source="live")
            _pl_unit = {"rows": [{"cls": "segDiePlacement", "attempts": 7}],
                        "units_line": {"attempts": 7}}

            def _pl_read():
                with open(ledger_path(_pl_day), "r", encoding="utf-8") as fh:
                    return json.load(fh)

            _w0 = persist_ledger(_pl_board)
            _d0 = _pl_read()
            ck(_w0[0] is True and all(_d0.get(k) is None for k in _DOC_V4_META_KEYS),
               f"(c) 端到端：無既有檔 ＋ rw_meta 未給 ⇒ 落地三鍵 None（現況），got {_w0}")

            os.remove(ledger_path(_pl_day))
            persist_ledger(_pl_board, rw_meta={"rw_axis": True, "rw_reason": "ok",
                                               "families_status": "ok"})
            _d1 = _pl_read()
            _w2 = persist_ledger(_pl_board, unit_board=_pl_unit)
            _d2 = _pl_read()
            ck(_w2[0] is True
               and all(_d2.get(k) == _d1.get(k) for k in _DOC_V4_META_KEYS)
               and _d2.get("rw_axis") is True and _d2.get("rw_reason") == "ok"
               and _d2.get("families_status") == "ok",
               f"(d) 端到端：attrib 腿（帶 rw_meta）→ 顆數腿（不帶）⇒ 三鍵**逐位元同前一寫者**"
               f"（舊行為＝全被抹成 None ⇒ 月報 n_days_rw 恆 0），got "
               f"{[_d2.get(k) for k in _DOC_V4_META_KEYS]}")
            ck(any((s.get("units") or {}).get("attempts") == 7
                   for s in _d2.get("stations") or ()),
               "(d) 端到端：顆數腿真的有寫進 units（沿用三鍵不是靠『沒覆寫成功』蒙到的）")

            os.remove(ledger_path(_pl_day))
            persist_ledger(_pl_board, rw_meta={"rw_axis": True, "rw_reason": "ok",
                                               "families_status": "ok"})
            persist_ledger(_pl_board, rw_meta={"rw_axis": False,
                                               "rw_reason": "calendar_absent"})
            _d3 = _pl_read()
            ck(_d3.get("rw_axis") is False and _d3.get("rw_reason") == "calendar_absent"
               and _d3.get("families_status") is None,
               f"(b) 端到端：後一位**帶 rw_meta** 的呼叫端整份說了算（沒寫的鍵落 None、"
               f"不沿用），got {[_d3.get(k) for k in _DOC_V4_META_KEYS]}")

            # ── ★ 2026-09-04 第二道覆寫閘：**壞帳不得蓋好帳**（事故＝2026-08-11）────────
            #   那天 28 站 timeline 全部 SQL 連線失敗 ⇒ 一份「整天零可測」的板蓋掉了
            #   21 站可測的好帳（磁碟上只剩 `.bak-`，`read_ledger_docs` 不認那個檔名）。
            import copy as _cp
            os.remove(ledger_path(_pl_day))
            _rwm_ok = {"rw_axis": True, "rw_reason": "ok", "families_status": "ok"}
            persist_ledger(_pl_board, unit_board=_pl_unit, rw_meta=_rwm_ok)
            _good = _pl_read()
            _bad_board = _cp.deepcopy(_pl_board)
            for _r in _bad_board.get("rows") or ():
                _r["note"] = unit_module().wattr.NOTE_SRC_ERR   # 整批「當時讀不到」
                _r["unmeasurable"] = True
            _wref = persist_ledger(_bad_board, rw_meta=_rwm_ok)
            ck(_wref == (False, unit_module().REFUSE_SRC_ERR_ALL) and _pl_read() == _good,
               f"★ 端到端：整天零可測的板**寫不進去**（回明確理由、既有好帳一個位元不動），"
               f"got {_wref}")
            _one_bad = _cp.deepcopy(_pl_board)
            for _r in (_one_bad.get("rows") or ())[:1]:
                _r["note"] = unit_module().wattr.NOTE_SRC_ERR
                _r["unmeasurable"] = True
            ck(persist_ledger(_one_bad, rw_meta=_rwm_ok)[0] is True,
               "★ 端到端：**部分**站 src_err 照樣落地（這道閘刻意只認整天全滅那一種形）")
        finally:
            globals()["ledger_dir"] = _pl_real
            _shp.rmtree(_pl_tmp, ignore_errors=True)

    # ── 綑級名義配置（SPEC-DISPATCH-V2 §14；暫存檔，不建立 Chris 的正式檔）───────────
    import tempfile as _kd_tmp
    with _kd_tmp.TemporaryDirectory(prefix="board_kd_nominal_selftest_") as _kd_dir:
        _kd_absent = os.path.join(_kd_dir, "absent.json")
        ck(kun_nominal_stamp(_kd_absent) is None
           and load_kun_nominal_staff(_kd_absent)[1] == "absent",
           "kun nominal：缺檔→(None, absent)，不建正式 _KUN-NOMINAL-STAFF.json")
        _kd_good = os.path.join(_kd_dir, "good.json")
        with open(_kd_good, "w", encoding="utf-8") as _fh:
            json.dump({"schema": "board.kun_nominal_staff.v1", "source": "test", "as_of": "2026-09-05",
                       "by_kun": {"ZA-1": {"day": 2.5, "small_night": 1.5}}}, _fh)
        _kd_doc, _kd_status = load_kun_nominal_staff(_kd_good, today=datetime(2026, 9, 6).date())
        ck(_kd_status == "ok" and _kd_doc["by_kun"]["ZA-1"] == {"day": 3, "night": 2}
           and _kd_doc.get("_diag"),
           "kun nominal：small_night 讀取 alias→night；小數四捨五入並留 diag（不回寫）")
        _kd_stale_doc, _kd_stale = load_kun_nominal_staff(_kd_good, today=datetime(2027, 1, 1).date())
        ck(_kd_stale == "stale" and _kd_stale_doc is not None,
           "kun nominal：as_of 超過60日仍回資料但 status=stale")
        _kd_bad = os.path.join(_kd_dir, "bad.json")
        with open(_kd_bad, "w", encoding="utf-8") as _fh:
            _fh.write("{bad json")
        ck(load_kun_nominal_staff(_kd_bad)[0] is None
           and load_kun_nominal_staff(_kd_bad)[1].startswith("error:"),
           "kun nominal：壞檔→(None, error:…)")

    # ── 出貨目標抄本（SPEC-PACKOUT-TARGET §2／§4；暫存檔，不碰 repo 根那份）──────────
    import tempfile as _pt_tmp
    with _pt_tmp.TemporaryDirectory(prefix="board_packout_selftest_") as _pt_dir:
        _pt_absent = os.path.join(_pt_dir, "absent.json")
        ck(packout_target_stamp(_pt_absent) is None
           and load_packout_target(_pt_absent) == (None, "absent"),
           "packout target：缺檔→(None, absent)")
        _pt_good = os.path.join(_pt_dir, "good.json")
        with open(_pt_good, "w", encoding="utf-8") as _fh:
            json.dump({"schema": PACKOUT_TARGET_SCHEMA, "source": "test `Sep 26`",
                       "as_of": "2026-09-05", "unit": "PH",
                       "months": {"2026-09": {"2026-09-01": 120, "2026-09-02": 96,
                                              "2026-09-04": 40}}}, _fh)
        _pt_doc, _pt_st = load_packout_target(_pt_good, today=datetime(2026, 9, 6).date())
        ck(_pt_st == "ok" and _pt_doc["age_d"] == 1
           and _pt_doc["months"]["2026-09"]["2026-09-02"] == 96,
           f"packout target：好檔→ok、age_d=1，got {_pt_st}")
        _pt_stale_doc, _pt_stale = load_packout_target(_pt_good,
                                                       today=datetime(2026, 10, 21).date())
        ck(_pt_stale == "stale" and _pt_stale_doc is not None and _pt_stale_doc["age_d"] == 46
           and load_packout_target(_pt_good, today=datetime(2026, 10, 20).date())[1] == "ok",
           "packout target：as_of 超過 45 天→stale（仍回資料）；恰 45 天仍 ok")
        _pt_v = packout_target_view(_pt_doc, "2026-09-02")
        _pt_sun = packout_target_view(_pt_doc, "2026-09-03")
        ck(_pt_v == {"month_has": True, "day_t": 96, "mtd_t": 216}
           and _pt_sun == {"month_has": True, "day_t": None, "mtd_t": 216}
           and packout_target_view(_pt_doc, "2026-10-01")["month_has"] is False
           and packout_target_view(_pt_doc, "2026-10-01")["mtd_t"] is None
           and packout_target_view(None, "2026-09-02")["month_has"] is False,
           f"packout target：日目標／MTD Σ／空白日 None 不 0／缺月整格不渲染，got {_pt_v} {_pt_sun}")
        for _pt_name, _pt_body in (("bad_json", "{bad json"),
                                   ("bad_schema", json.dumps({"schema": "x", "as_of": "2026-09-05",
                                                              "months": {}})),
                                   ("bad_day", json.dumps({"schema": PACKOUT_TARGET_SCHEMA,
                                                           "as_of": "2026-09-05",
                                                           "months": {"2026-09": {"2026-08-31": 1}}})),
                                   ("bad_val", json.dumps({"schema": PACKOUT_TARGET_SCHEMA,
                                                           "as_of": "2026-09-05",
                                                           "months": {"2026-09": {"2026-09-01": True}}}))):
            _pt_bad = os.path.join(_pt_dir, _pt_name + ".json")
            with open(_pt_bad, "w", encoding="utf-8") as _fh:
                _fh.write(_pt_body)
            _pt_r = load_packout_target(_pt_bad)
            ck(_pt_r[0] is None and str(_pt_r[1]).startswith("error:"),
               f"packout target：壞檔 {_pt_name}→(None, error:…)，got {_pt_r[1]}")
    # repo 抄本（ROADMAP #81）：只驗「讀得到、形狀對、每月加總 > 0」——**不寫任何真值**（加總、
    #   日目標、檔案雜湊都不寫：抄本是真實的 SALTMARSH 目標，斷言與訊息會跟著原始碼進公開樹）。
    #   公開樹沒有抄本（範圍排除）⇒ skipped（R-P11）；私有樹缺檔照 FAIL。
    _pt_repo, _pt_repo_st = load_packout_target(today=datetime(2026, 9, 6).date())
    import core_paths as _pt_wp
    if _pt_repo_st == "absent" and _pt_wp.is_public_tree():
        print("board_data selftest: skipped: "
              f"{os.path.basename(PACKOUT_TARGET_PATH)} absent (public tree)")
    else:
        _pt_months = (_pt_repo or {}).get("months") or {}
        _pt_shape = bool(_pt_months) and all(
            isinstance(_m_days, dict) and _m_days
            and all(str(_d)[:7] == _m and type(_v) is int and _v >= 0
                    for _d, _v in _m_days.items())
            and sum(_m_days.values()) > 0
            for _m, _m_days in _pt_months.items())
        ck(_pt_repo_st == "ok" and set(_pt_months) >= {"2026-08", "2026-09"} and _pt_shape,
           f"packout target：repo 抄本可讀、八月／九月皆在、每日目標為非負整數且落在該月、"
           f"每月加總 > 0（ROADMAP #81；不釘真值），got {_pt_repo_st}")

    # ── #103（`_SPEC-QLIK-YIELD.md` §12）：產品子樹的落地腿（**只寫子樹**、冪等）────────
    #   ⚠ 全程在 tempdir 裡跑（`ledger_path` 暫時改指向），**絕不碰 Chris 真的 `_state/`**。
    import tempfile as _pf_tmp
    _wu_p = unit_module()
    if _wu_p is not None:
        with _pf_tmp.TemporaryDirectory(prefix="board_products_selftest_") as _pf_dir:
            _pf_day = "2026-09-01"
            _pf_doc = {
                "schema_ver": _wu_p.LEDGER_SCHEMA_V6, "day": _pf_day, "source": "live",
                "wo_source_status": "ok", "computed_at": f"{_pf_day}T23:59:00",
                "grid_min": 30, "thresholds": {}, "line": {},
                "stations": [{"cls": "segX", "units": {"attempts": 3, "qlik_pass": 9}}],
            }
            _sv_lp = globals()["ledger_path"]
            globals()["ledger_path"] = (
                lambda day, _d=_pf_dir: os.path.join(_d, f"{day}.json"))
            try:
                with open(ledger_path(_pf_day), "w", encoding="utf-8") as _fh:
                    json.dump(_pf_doc, _fh, ensure_ascii=False)
                _pf_blk = _wu_p.product_block(
                    get_qlik_grid("demo", days=1, product="kestrel"))
                ck(_pf_blk is not None and _pf_blk["n_stations"] == 45,
                   "#103：demo Kestrel grid → 45 站子樹（`product_block` 只挑欄、不算式子）")
                _ok1, _w1 = persist_product_subtree(_pf_day, "kestrel", _pf_blk)
                with open(ledger_path(_pf_day), encoding="utf-8") as _fh:
                    _pf_after = json.load(_fh)
                ck(_ok1 and _w1 == "written"
                   and _pf_after["stations"] == _pf_doc["stations"]
                   and _pf_after["schema_ver"] == _wu_p.LEDGER_SCHEMA_VER
                   and _wu_p.product_stations_of(_pf_after) is not None,
                   "#103 ★ 只寫子樹：Plover 站列一個位元不動、版號升 v7、子樹讀得回來")
                ck(persist_product_subtree(_pf_day, "kestrel", _pf_blk)
                   == (False, "already_same"),
                   "#103 冪等：同一份子樹寫兩次 ⇒ already_same、檔不動")
                ck(persist_product_subtree("2099-01-01", "kestrel", _pf_blk)
                   == (False, "no_doc"),
                   "#103 檔不在 ⇒ no_doc（**不憑空造一份只有 Kestrel 的假日帳**）")
                ck(persist_product_subtree(_pf_day, "kestrel", None)[1]
                   .startswith("no_subtree"),
                   "#103 子樹缺席 ⇒ 不寫一棵空樹（『沒量到』≠『量到 0』）")
            finally:
                globals()["ledger_path"] = _sv_lp

    # ── #182-P2：日曆尾巴／空軌 stub／寬窗名冊錨／wo_meta 旗解／就地標 resolved ─────────
    #   ⚠ 日期一律**相對 today**（記憶 smoke-fixture-date-anchors）。
    _td = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    _py0 = _td - timedelta(days=2) + timedelta(hours=wem.PROD_DAY_START_H)
    _pt0 = _py0 + timedelta(days=1)

    def _zz(v):                                   # 台北 naive → wire UTC Z（固定 +8）
        return (v - timedelta(hours=TAIPEI_UTC_OFFSET_H)).strftime(_CAL_Z_FMT)
    _cal = {"coverage": {"from": _zz(_py0 - timedelta(days=5)), "to": _zz(_pt0 + timedelta(minutes=5))},
            "closures": [], "open_silence_from": _zz(_pt0 - timedelta(hours=2))}
    _tl = calendar_open_tail(_cal, _py0, _pt0)
    ck(_tl == {"from": (_pt0 - timedelta(hours=2)).isoformat(), "overlap_sec": 7200},
       "#182-P2 B：日曆尾端未閉合沉默與生產日重疊 ⇒ {from（台北）, overlap_sec}")
    ck(calendar_open_tail(dict(_cal, open_silence_from=_zz(_pt0 + timedelta(minutes=1))),
                          _py0, _pt0) is None
       and calendar_open_tail({k: v for k, v in _cal.items() if k != "open_silence_from"},
                              _py0, _pt0) is None,
       "#182-P2 B：沉默起點在窗後／舊日曆沒有這個鍵 ⇒ None（不落旗）")
    ck(cal_open_tail_state(_tl, _cal) == CAL_TAIL_OPEN,
       "#182-P2：同一份日曆＝同一段沉默還開著 ⇒ open")
    _sealed_c = dict(_cal, coverage=dict(_cal["coverage"], to=_zz(_pt0 + timedelta(days=1))),
                     closures=[{"start": _zz(_pt0 - timedelta(hours=2)),
                                "end": _zz(_pt0 + timedelta(hours=5))}],
                     open_silence_from=_zz(_pt0 + timedelta(hours=20)))
    ck(cal_open_tail_state(_tl, _sealed_c) == CAL_TAIL_CLOSURE,
       "#182-P2：事後封口且成了 closure ⇒ sealed_closure（本日分母該扣 ⇒ 要重建）")
    _sealed_n = dict(_sealed_c, closures=[])
    ck(cal_open_tail_state(_tl, _sealed_n) == CAL_TAIL_NONE,
       "#182-P2：事後封口、沒成 closure ⇒ sealed_none（原帳就對 ⇒ 就地標 resolved、不重建）")
    ck(cal_open_tail_state(_tl, dict(_cal, coverage={"from": _zz(_pt0), "to": _zz(_pt0 + timedelta(days=1))}))
       == CAL_TAIL_UNKNOWN and cal_open_tail_state(_tl, None) == CAL_TAIL_UNKNOWN,
       "#182-P2：日曆缺席／起點已滾出涵蓋 ⇒ unknown（判不了就不主張）")
    # F1：空軌名冊機 → stub；名冊外空軌、零工期機、有段的機台原樣；不 mutate 快取那份
    _rost = {"EID-R1", "EID-R2", "EID-R3"}
    _mach = [{"eid": "EID-R1", "desc": "r1", "records": []},                       # 空軌名冊機
             {"eid": "EID-R2", "desc": "r2", "records": [], "zero_len_only": True},  # 零工期
             {"eid": "EID-R3", "desc": "r3",
              "records": [{"state": "生產", "start": _py0, "end": _pt0, "sec": 86400.0}]},
             {"eid": "EID-X9", "desc": "x9", "records": []}]                         # 名冊外空軌
    _dat = {"machines": _mach, "win_start": _py0, "win_end": _pt0}
    _snap = json.dumps(_dat, default=str)
    _nd, _conv = normalize_empty_tracks(_dat, _rost)
    _by = {m["eid"]: m for m in _nd["machines"]}
    ck(_conv == ["EID-R1"] and _by["EID-R1"].get("roster_nosig") is True
       and len(_by["EID-R1"]["records"]) == 1 and _by["EID-R1"]["records"][0]["state"] == "不明"
       and _by["EID-R2"]["records"] == [] and _by["EID-X9"]["records"] == []
       and len(_by["EID-R3"]["records"]) == 1 and len(_nd["machines"]) == 4,
       "#182-P2 F1：窗內零段的**名冊機**一律換 stub（整窗不明）；零工期機／名冊外空軌／有段機原樣")
    ck(json.dumps(_dat, default=str) == _snap and normalize_empty_tracks(
        {"machines": [_mach[2]], "win_start": _py0, "win_end": _pt0}, _rost)[1] == [],
       "#182-P2 F1：不 mutate 輸入（`load()` 快取那份）；沒有空軌 ⇒ 原樣回、零轉換")
    # F3：寬窗名冊錨到窗起（參數化；SQL 不動）
    ck(roster_wide_days_anchored(_py0, now=_pt0 + timedelta(minutes=12)) == ROSTER_DAYS + 2
       and roster_wide_days_anchored(_py0 - timedelta(days=400), now=_pt0)
       == max(ROSTER_DAYS, ROSTER_ANCHOR_MAX_DAYS),
       "#182-P2 F3：寬窗天數＝(now−窗起).days＋1＋30，上限 ROSTER_ANCHOR_MAX_DAYS")
    ck(roster_fleet("segX", 1, "demo", wide_days=45) == (None, "off"),
       "#182-P2 F3：wide_days 不改非 live 的短路（demo 仍 off、不連 MES）")
    # wo_meta 旗解（sqlite ro）＋就地標 resolved
    import sqlite3 as _sq
    import tempfile as _tf
    _tdir = _tf.mkdtemp(prefix="board-p2-")
    _db = os.path.join(_tdir, "wo.sqlite")
    _c = _sq.connect(_db)
    _c.execute("CREATE TABLE wo_meta (wo_no TEXT PRIMARY KEY, closed_at TEXT, active_end TEXT, "
               "flag_long INTEGER)")
    _c.executemany("INSERT INTO wo_meta VALUES (?,?,?,?)",
                   [("101", "2026-01-01 09:00:00", None, 0), ("201", None, None, 1),
                    ("301", None, None, 0)])
    _c.commit()
    _c.close()
    _ms = wo_meta_status(["101", "201", "301", "999"], path=_db)
    ck(_ms == {"101": {"closed": True, "long": False}, "201": {"closed": False, "long": True},
               "301": {"closed": False, "long": False}}
       and wo_meta_status(["1"], path=os.path.join(_tdir, "nope.sqlite")) == {},
       "#182-P2 A②：wo_meta（ro）→ closed／long；查無的單不出現（＝未解）、檔不在 ⇒ {}")
    # ★ 2026-10-02 讀端收窄（WO 35456 型誤旗；`_FOLLOWUPS-20261002.md` ②）
    _db2 = os.path.join(_tdir, "wo2.sqlite")
    _c = _sq.connect(_db2)
    _c.execute("CREATE TABLE wo_meta (wo_no TEXT PRIMARY KEY, eid TEXT)")
    _c.executemany("INSERT INTO wo_meta VALUES (?,?)",
                   [("35456", "EID-38790"), ("35473", "EID-00950"), ("37100", "EID-25526"),
                    ("888", None)])
    _c.commit()
    _c.close()
    ck(wo_meta_eids(["35456", "35473", "888", "999"], path=_db2)
       == {"35456": "EID-38790", "35473": "EID-00950"}
       and wo_meta_eids(["35456"], path=_db) == {}
       and wo_meta_eids(["35456"], path=os.path.join(_tdir, "nope.sqlite")) == {},
       "2026-10-02 wo_meta_eids（唯讀）→ {wo: eid}；eid 空／查無不出現；缺欄或檔不在 ⇒ {}")
    _st_in = [{"cls": "segCalibrationTest", "rw": {"eids": ["EID-00950", "EID-17431"]}},
              {"cls": "segX", "unmeasurable": True}]
    _dA = {"day": "2026-09-01", "stations": _st_in, "wo_open_at_build": ["35456", "35473"]}
    _dB = {"day": "2026-09-22", "stations": _st_in,
           "wo_open_at_build": ["35473", "37100", "777"]}
    _dC = {"day": "2026-09-06", "stations": _st_in, "wo_open_at_build": ["35473"]}
    _dD = {"day": "2026-08-01", "stations": [{"cls": "segX"}], "wo_open_at_build": ["35456"]}
    _dE = {"day": "2026-09-02", "stations": _st_in, "wo_open_at_build": ["35456"]}
    _snap_n = json.dumps([_dA, _dB, _dC, _dD, _dE], sort_keys=True)
    _nar = narrow_wo_open_docs([_dA, _dB, _dC, _dD, _dE], path=_db2)
    ck([d.get("wo_open_at_build") for d in _nar]
       == [["35473"], ["35473", "777"], ["35473"], ["35456"], []]
       and _nar[2] is _dC and _nar[3] is _dD
       and json.dumps([_dA, _dB, _dC, _dD, _dE], sort_keys=True) == _snap_n,
       "2026-10-02 narrow_wo_open_docs：不在任何站 rw.eids 的機台（35456／37100）其舊旗讀端拿掉；"
       "查無機台（777）留、無 rw 範圍的帳（24h 軸）留；沒變的回同一物件；輸入不 mutate（不重寫日帳）")
    ck(narrow_wo_open_docs([_dA], path=os.path.join(_tdir, "nope.sqlite"))[0] is _dA
       and narrow_wo_open_docs(None) == [],
       "2026-10-02 narrow_wo_open_docs：wo_ledger 缺 ⇒ 旗原樣（不知道就不收窄）")
    _sv_lp2 = globals()["ledger_path"]
    try:
        globals()["ledger_path"] = lambda d: os.path.join(_tdir, f"{d}.json")
        with open(os.path.join(_tdir, "2026-01-02.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema_ver": "board.daily_ledger.v8", "day": "2026-01-02",
                       "stations": [{"cls": "segA"}], "cal_open_tail": dict(_tl)}, fh)
        _r1 = mark_cal_open_tail_resolved("2026-01-02", CAL_TAIL_NONE, at=_pt0)
        with open(os.path.join(_tdir, "2026-01-02.json"), encoding="utf-8") as fh:
            _after = json.load(fh)
        ck(_r1 == (True, "written") and _after["cal_open_tail"]["resolved"] == CAL_TAIL_NONE
           and _after["stations"] == [{"cls": "segA"}]
           and mark_cal_open_tail_resolved("2026-01-02", CAL_TAIL_NONE) == (False, "already_resolved")
           and mark_cal_open_tail_resolved("2099-01-01", CAL_TAIL_NONE) == (False, "no_doc"),
           "#182-P2：sealed_none 就地標 resolved（只動那一個鍵、冪等、檔不在不造檔）")
    finally:
        globals()["ledger_path"] = _sv_lp2

    n += _selftest_ledger_source()
    print(f"board_data overlay selftest: {n} 斷言 PASS")
    return 0


# ======================================================================================
# 合成產線 Q-C：日帳／月快照來源隔離自測（2026-09-29 異家族審查 finding 的回歸釘）
#   · 資料全在系統暫存目錄；`BOARD_STATE_DIR`／`BOARD_SOURCE` 只在子進程內設；零 MES（驅動替身）。
#   · 非合成子進程：每個讀取入口只回 live 與缺欄的帳、合成帳被拒並計數。
#   · 合成子進程：每個讀取入口只回合成帳。
#   · 原始碼掃描：非凍結模組裡，除了資料層讀取函式，沒有地方直接開日帳／月快照目錄下的檔。
# ======================================================================================
_LSI_FROZEN = ("mes_timeline.py", "mes_yield.py", "mes_operator.py", "board_w8.py",
               "core_prod_calendar.py")
#: 資料層讀取函式（開檔＋判準在這幾支；其餘一律經它們）。
_LSI_READERS = {("board_data.py", "read_ledger_doc"),
                ("board_monthly.py", "read_snapshot"),
                ("board_monthly.py", "_snapshot_admitted")}
#: 白名單（附理由）：(檔, 開檔函式, 呼叫端)。
_LSI_ALLOW = {
    ("_backfill_ledger.py", "_read_json", "main"):
        "讀 `--force` 改名移走的 `.bak-` 備份（不是 `<day>.json` 日帳），只為判斷要不要把它放回原處；"
        "判斷前以 core_source.ledger_doc_admitted 過濾（另一來源的備份不拿來蓋回去）",
}
#: 目錄記號。**屬性存取**（`wd.ledger_path`、`_wp.ledger_dir`、`_wmn.snapshot_path`…）到處都算；
#: 通用字眼的**裸名**只在定義它的模組算（別的模組有同名的自家帳本／參數，如 llmclass 的
#: `ledger_path`、board_data 的 MES 切面參數 `snapshot_path`）；專名裸名到處都算。
_LSI_ATTRS = {"ledger_path", "ledger_dir", "LEDGER_DIR", "monthly_ledger_dir", "_MR_SNAP_DIR",
              "snapshot_path", "LEDGER_SUBDIR", "MONTHLY_SUBDIR"}
_LSI_BARE = {"LEDGER_DIR", "_ledger_dir", "LEDGER_SUBDIR", "MONTHLY_SUBDIR", "_MR_SNAP_DIR",
             "monthly_ledger_dir"}
_LSI_BARE_LOCAL = {"board_data.py": {"ledger_path", "ledger_dir"},
                   "board_monthly.py": {"snapshot_path"},
                   "core_paths.py": {"ledger_dir", "state_path"}}
_LSI_LITERALS = {"daily_ledger", "monthly_ledger"}


def _lsi_scan(repo):
    """→ `(findings, n_files)`；finding＝(檔, 開檔函式, 呼叫端 or None)。純讀原始碼（ast）。"""
    import ast

    def _is_read_open(call):
        f = call.func
        if isinstance(f, ast.Name) and f.id == "open":
            mode_pos = 1
        elif isinstance(f, ast.Attribute) and f.attr == "open":
            mode_pos = 1 if (isinstance(f.value, ast.Name) and f.value.id in ("io", "codecs")) else 0
        elif isinstance(f, ast.Attribute) and f.attr in ("read_text", "read_bytes"):
            return True
        else:
            return False
        mode = None
        if len(call.args) > mode_pos:
            mode = call.args[mode_pos]
        for kw in call.keywords:
            if kw.arg == "mode":
                mode = kw.value
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
            return not any(c in mode.value for c in "wax")
        return mode is None

    def _facts(nodes, bare):
        tok, opens, calls = False, 0, set()
        stack = list(nodes)
        while stack:
            nd = stack.pop()
            if isinstance(nd, ast.Expr) and isinstance(getattr(nd, "value", None), ast.Constant) \
                    and isinstance(nd.value.value, str):
                continue                                  # docstring／裸字串不算
            if isinstance(nd, ast.Name) and nd.id in bare:
                tok = True
            elif isinstance(nd, ast.Attribute) and nd.attr in _LSI_ATTRS:
                tok = True
            elif isinstance(nd, ast.Constant) and nd.value in _LSI_LITERALS:
                tok = True
            if isinstance(nd, ast.Call):
                if _is_read_open(nd):
                    opens += 1
                f = nd.func
                if isinstance(f, ast.Name):
                    calls.add(f.id)
                elif isinstance(f, ast.Attribute):
                    calls.add(f.attr)
            stack.extend(ast.iter_child_nodes(nd))
        return tok, opens, calls

    findings, n_files = [], 0
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in (".git", ".claude", "_publish", "__pycache__")]
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, fn), repo).replace(os.sep, "/")
            if not fn.endswith(".py") or fn in _LSI_FROZEN or fn == "run_smoke.py" or "selftest" in fn:
                continue
            try:
                with open(os.path.join(dp, fn), "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
            except Exception:
                findings.append((rel, "<unparsable>", None))
                continue
            n_files += 1
            units = []
            top = []
            for nd in tree.body:
                if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units.append((nd.name, [nd]))
                elif isinstance(nd, ast.ClassDef):
                    for m in nd.body:
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            units.append((m.name, [m]))
                else:
                    top.append(nd)
            units.append(("<module>", top))
            bare = _LSI_BARE | _LSI_BARE_LOCAL.get(rel, set())
            facts = {name: _facts(nodes, bare) for name, nodes in units}
            for name, (tok, opens, calls) in facts.items():
                if "selftest" in name:
                    continue                              # 自測碼（暫存目錄）不算
                if opens and tok and (rel, name) not in _LSI_READERS:
                    findings.append((rel, name, None))
                if not tok or name == "<module>":
                    continue                              # 模組頂層只看直接開檔（呼叫太雜）
                for c in calls:
                    h = facts.get(c)
                    if c != name and h and h[1] and not h[0] and (rel, c) not in _LSI_READERS \
                            and "selftest" not in c:
                        findings.append((rel, c, name))
    return sorted(set(findings)), n_files


_LSI_CHILD = r'''
import json, os, sys, types
from datetime import datetime
sys.path.insert(0, os.getcwd())
import core_source as S
SYN = S.is_synth_process()
if SYN:
    S.startup_tripwire()          # 合成進程：絆線自己裝驅動替身（必須在任何 mes_* import 之前）
else:
    class _NoDB(Exception): pass
    def _no(*a, **k): raise _NoDB("ledger-source selftest: DB driver stubbed")
    _m = types.ModuleType("pyodbc"); _m.connect = _no; _m.Error = _NoDB
    sys.modules["pyodbc"] = _m
P = json.loads(sys.argv[1])
R = {"synth": SYN}
import board_data as wd
days, end = P["days"], P["end"]
R["doc"] = {d: ((wd.read_ledger_doc(d) or {}).get("day") is not None) for d in days}
R["doc_raw"] = {d: (wd.read_ledger_doc(d, raw=True) or {}).get("source", "<absent>") if wd.read_ledger_doc(d, raw=True) is not None else None for d in days}
dg = {}
R["docs"] = sorted(x.get("day") for x in wd.read_ledger_docs(days=P["n"], end_day=end, diag=dg))
R["docs_rej"] = dg.get("n_source_rejected", 0)
R["docs_v1"] = sorted(x.get("day") for x in wd.read_ledger_docs(days=P["n"], end_day=end, v1_view=True))
act, meta = wd.read_family_activity(days=P["n"], end_day=end)
R["fam_n_docs"] = meta.get("n_docs")
wu = wd.unit_module(); cap = {}
_br, _bb = wu.base_rates, wu.build_board
def _sbr(docs, day): cap["base"] = sorted(x.get("day") for x in docs); return _br(docs, day)
def _sbb(*a, **k): cap["pareto"] = sorted(x.get("day") for x in (k.get("pareto_docs") or ())); return _bb(*a, **k)
wu.base_rates, wu.build_board = _sbr, _sbb
wd._MR_TRIED, wd._MR = True, None
_ud = datetime.strptime(end, "%Y-%m-%d").replace(hour=8)
wd.get_unit_day("synth" if SYN else "live", _ud, _ud.replace(hour=20), persist=False)
wu.base_rates, wu.build_board = _br, _bb
R["unit_base"], R["unit_pareto"] = cap.get("base"), cap.get("pareto")
import core_paths as _wpx
_pub = _wpx.is_public_tree()
try:
    import _backfill_ledger as bf
except ImportError:
    if not _pub:
        raise
    bf = None        # 公開樹不帶回補腳本 ⇒ 該旁路標 skipped（私有樹缺即拋）
if bf is not None:
    R["bf_doc"] = {d: bf.read_doc(d) is not None for d in days}
    R["bf_plan"] = {d: a for d, a, _w in bf.build_plan(days)}
try:
    import _probe_rw_denominator as prd
except ImportError:
    if not _pub:
        raise
    prd = None       # 公開樹不帶探針
if prd is not None:
    pdg = {}
    _pd, _pb = prd.read_docs(wd.ledger_dir(), diag=pdg)
    R["probe"] = sorted(x.get("day") for x in _pd); R["probe_bad"] = _pb; R["probe_rej"] = pdg.get("n_source_rejected", 0)
import board_monthly as wm
import core_paths as wp
md = wp.monthly_ledger_dir(); mdg = {}
R["snap_list"] = wm.list_snapshots(md, diag=mdg)
R["snap_read"] = {m: wm.read_snapshot(md, m) is not None for m in P["months"]}
R["snap_rej_list"] = mdg.get("n_source_rejected", 0)
R["snap_counter"] = wm.snapshot_source_rejects()["n"]
R["month_model_days"] = (wm.month_model(wd.read_ledger_docs(days=P["n"], end_day=end), "2026-08", today="2026-09-15").get("coverage") or {}).get("n_days_present")
if not SYN:
    import app as A
    R["app_docs"] = sorted(x.get("day") for x in A._ledger_docs(days=P["n"], end_day=end))
    R["app_doc"] = {d: A._ledger_doc(d) is not None for d in days}
    _today = datetime.now()
    A.wtagui.recent_days = lambda: (_today - datetime.strptime(days[0], "%Y-%m-%d")).days + 2
    R["app_tag_n_docs"] = (A._tag_activity()[1] or {}).get("n_docs")
# 寫入端（最後做；改檔）：另一來源的既有檔＝視同無檔 ⇒ 本進程整份覆寫、不沿用任何一腿
R["subtree_other"] = wd.persist_product_subtree(P["other"], "kestrel", None)
R["caltail_other"] = wd.mark_cal_open_tail_resolved(P["other"], "x")
wa = wd.attrib_module()
b = dict(wa.demo_board(day=P["other"]), source=("synth" if SYN else "live"))
R["persist_other"] = list(wd.persist_ledger(b))
with open(wd.ledger_path(P["other"]), encoding="utf-8") as fh:
    R["persist_other_source"] = json.load(fh).get("source")
R["counter"] = wd.ledger_source_rejects()["n"]
sys.stdout.write("@@LSI@@" + json.dumps(R, ensure_ascii=False, default=repr))
'''


def _selftest_ledger_source():
    import subprocess
    import tempfile
    import board_monthly as _wm
    repo = os.path.dirname(os.path.abspath(__file__))
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # (0) 判準本體＝core_source（非合成／合成兩態）
    ck(_wsrc.ledger_doc_admitted({"source": "live"}, synth_process=False)
       and not _wsrc.ledger_doc_admitted({"source": "synth"}, synth_process=False)
       and _wsrc.ledger_doc_admitted({}, synth_process=False)
       and _wsrc.ledger_doc_admitted({"source": "synth"}, synth_process=True)
       and not _wsrc.ledger_doc_admitted({}, synth_process=True)
       and not _wsrc.ledger_doc_admitted({"source": "live"}, synth_process=True),
       "Q-C 判準：非合成只拒合成帳（缺欄照收）；合成只收合成帳")

    base_docs = _wm.load_sample_docs()[:12]          # 仿真日帳 fixture（數字全假）
    kinds = {}
    for i, d in enumerate(base_docs):
        kinds[d["day"]] = ("live", "synth", "absent")[i % 3]
    days = sorted(kinds)
    snap_fx = os.path.join(repo, "_fixtures", "board.monthly_ledger.v1.json")
    with open(snap_fx, "r", encoding="utf-8") as fh:
        snap0 = [s for s in json.load(fh)["golden"] if isinstance(s, dict) and s.get("month")][0]
    months = {"2026-05": "live", "2026-06": "synth", "2026-07": "absent"}

    def _populate(sd):
        led = os.path.join(sd, "daily_ledger")
        mon = os.path.join(sd, "monthly_ledger")
        os.makedirs(led)
        os.makedirs(mon)
        for d in base_docs:
            doc = dict(d)
            k = kinds[d["day"]]
            if k == "absent":
                doc.pop("source", None)
            else:
                doc["source"] = k
            with open(os.path.join(led, d["day"] + ".json"), "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False)
        for m, k in months.items():
            s = dict(snap0, month=m)
            if k == "absent":
                s.pop("source", None)
            else:
                s["source"] = k
            with open(os.path.join(mon, m + ".json"), "w", encoding="utf-8") as fh:
                json.dump(s, fh, ensure_ascii=False)

    def _run(synth):
        sd = tempfile.mkdtemp(prefix="board_lsi_")
        try:
            _populate(sd)
            if synth:                                  # 合成進程的狀態目錄必須帶標記（C-SY3）
                with open(os.path.join(sd, _wsrc.MARKER_FILE), "w", encoding="utf-8") as fh:
                    json.dump({"kind": _wsrc.MARKER_KIND, "version": _wsrc.MARKER_VERSION}, fh)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("BOARD_SOURCE", "BOARD_STATE_DIR", "BOARD_CMMS_BASE_URL",
                                "BOARD_EQM_LP_STATE", "BOARD_WO_LEDGER", "LLM_CLASS_LEDGER",
                                "LLM_SERVICE_LEDGER", "BOARD_ASSISTANT_DB")}
            env.update(BOARD_STATE_DIR=sd, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                       BOARD_NO_AUTOFREEZE="1")
            if synth:
                env["BOARD_SOURCE"] = "synth"
            other = next(d for d in days if kinds[d] == ("live" if synth else "synth"))
            p = {"days": days, "end": days[-1], "n": 40, "months": sorted(months), "other": other}
            r = subprocess.run([sys.executable, "-c", _LSI_CHILD, json.dumps(p)], cwd=repo, env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=900)
            out = r.stdout or ""
            assert "@@LSI@@" in out, "ledger-source child (%s) failed: %s" % (
                "synth" if synth else "non-synth", (out + (r.stderr or ""))[-1500:])
            res = json.loads(out.split("@@LSI@@", 1)[1])
            res["_other"] = other
            return res
        finally:
            import shutil
            shutil.rmtree(sd, ignore_errors=True)

    want_ns = sorted(d for d in days if kinds[d] != "synth")
    want_sy = sorted(d for d in days if kinds[d] == "synth")
    n_syn, n_ns = len(want_sy), len(want_ns)
    for synth, want, n_rej in ((False, want_ns, n_syn), (True, want_sy, n_ns)):
        tag = "合成" if synth else "非合成"
        r = _run(synth)
        ck(r["synth"] is synth, f"({tag}) 子進程旗標")
        ck(sorted(d for d, ok in r["doc"].items() if ok) == want,
           f"({tag}) read_ledger_doc 只回本來源的帳：{r['doc']}")
        ck(sorted(d for d, v in r["doc_raw"].items() if v is not None) == want,
           f"({tag}) read_ledger_doc(raw=True) 同一判準")
        ck(r["docs"] == want and r["docs_v1"] == want and r["docs_rej"] == n_rej,
           f"({tag}) read_ledger_docs（含 v1_view）只回本來源、diag 計被拒 {n_rej}：{r['docs']} rej={r['docs_rej']}")
        ck(r["unit_base"] is not None and set(r["unit_base"]) <= set(want)
           and r["unit_pareto"] == want,
           f"({tag}) 旁路①顆數帳 get_unit_day：基準與柏拉圖只吃本來源的帳：{r['unit_base']} / {r['unit_pareto']}")
        ck(r["fam_n_docs"] == len(want),
           f"({tag}) 旁路②產品族活動 read_family_activity：n_docs={r['fam_n_docs']}")
        if "bf_doc" not in r:
            print("board_data selftest: skipped: _backfill_ledger absent (public tree)")
        else:
            ck(sorted(d for d, ok in r["bf_doc"].items() if ok) == want
               and all((r["bf_plan"][d] == "build") == (d not in want) for d in days),
               f"({tag}) 回補腳本 read_doc／build_plan：另一來源的日子＝無檔（build）：{r['bf_plan']}")
        if "probe" not in r:
            print("board_data selftest: skipped: _probe_rw_denominator absent (public tree)")
        else:
            ck(r["probe"] == want and r["probe_bad"] == [] and r["probe_rej"] == n_rej,
               f"({tag}) 探針 _probe_rw_denominator.read_docs：{r['probe']} rej={r['probe_rej']}")
        want_m = sorted(m for m, k in months.items() if (k == "synth") == synth)
        ck(r["snap_list"] == want_m
           and sorted(m for m, ok in r["snap_read"].items() if ok) == want_m
           and r["snap_rej_list"] == len(months) - len(want_m) and r["snap_counter"] >= 1,
           f"({tag}) 月快照 list/read 只認本來源：{r['snap_list']} {r['snap_read']}")
        ck(r["month_model_days"] == len([d for d in want if d.startswith("2026-08")]),
           f"({tag}) 月報 month_model 只吃本來源的日帳：{r['month_model_days']}")
        if not synth:
            ck(r["app_docs"] == want and sorted(d for d, ok in r["app_doc"].items() if ok) == want
               and r["app_tag_n_docs"] == len(want),
               f"({tag}) app 讀口（_ledger_docs／_ledger_doc／_tag_activity）委派資料層：{r['app_docs']}")
        ck(r["subtree_other"] == ["False", "no_doc"] or r["subtree_other"] == [False, "no_doc"],
           f"({tag}) persist_product_subtree 對另一來源的帳＝no_doc：{r['subtree_other']}")
        ck(r["caltail_other"] in ([False, "no_doc"],),
           f"({tag}) mark_cal_open_tail_resolved 對另一來源的帳＝no_doc：{r['caltail_other']}")
        ck(r["persist_other"] == [True, "written"]
           and r["persist_other_source"] == ("synth" if synth else "live"),
           f"({tag}) persist_ledger：另一來源的既有檔視同無檔、整份以本來源覆寫：{r['persist_other']} "
           f"src={r['persist_other_source']}")
        ck(r["counter"] >= n_rej, f"({tag}) 進程累計計數 ledger_source_rejects={r['counter']}")

    # 原始碼掃描：先驗掃描器抓得到（負對照），再掃整個 repo
    _neg = tempfile.mkdtemp(prefix="board_lsi_scan_")
    try:
        with open(os.path.join(_neg, "bad_reader.py"), "w", encoding="utf-8") as fh:
            fh.write("import board_data as wd\nimport core_paths as _wp\n"
                     "def f(day):\n    with open(wd.ledger_path(day)) as fh:\n        return fh.read()\n"
                     "def g():\n    return h(_wp.ledger_dir())\n"
                     "def h(p):\n    with open(p, encoding='utf-8') as fh:\n        return fh.read()\n"
                     "def w(day):\n    with open(wd.ledger_path(day), 'w') as fh:\n        fh.write('x')\n"
                     "def ok(day):\n    return wd.read_ledger_doc(day)\n")
        _nf, _nn = _lsi_scan(_neg)
    finally:
        import shutil
        shutil.rmtree(_neg, ignore_errors=True)
    ck(_nn == 1 and _nf == [("bad_reader.py", "f", None), ("bad_reader.py", "h", "g")],
       f"原始碼掃描負對照：直接開檔與經 helper 開檔都抓得到、寫檔與走讀取函式不算：{_nf}")
    # 非凍結模組裡沒有第二個直接開日帳／月快照的點（白名單附理由）
    found, n_files = _lsi_scan(repo)
    extra = [f for f in found if f not in _LSI_ALLOW]
    ck(n_files > 50 and not extra,
       f"原始碼掃描（{n_files} 檔）：資料層讀取函式之外沒有直接開日帳目錄的點；未列白名單：{extra}")
    # 白名單條目所屬檔案在本樹不存在（例：脫敏公開樹不帶回補腳本）⇒ 該條無從過期，不要求存在；檔案在 ⇒ 照舊必須掃得到。
    ck(all(f in found or not os.path.exists(os.path.join(repo, f[0])) for f in _LSI_ALLOW),
       f"白名單每一條都還真的存在（過期條目要刪）：{sorted(set(_LSI_ALLOW) - set(found))}")
    return n


def main():
    ap = argparse.ArgumentParser(description="Tendline Board 資料供應層（canonical 結構）")
    ap.add_argument("--selftest", action="store_true", help="W8 overlay 純函式離線自驗（零 MES）")
    ap.add_argument("--source", choices=["live", "snapshot", "demo"], default="demo")
    ap.add_argument("--demo", action="store_true", help="= --source demo")
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--stop-min", type=int, default=15)
    ap.add_argument("--start", help="觀測窗起 YYYY-MM-DD（不給=now-days）")
    ap.add_argument("--end", help="觀測窗訖 YYYY-MM-DD（不給=now；純日期含當日）")
    ap.add_argument("--file", help="snapshot 檔（source=snapshot 用）")
    ap.add_argument("--scoreboard", action="store_true",
                    help="印全線設備稼動計分板（§26.2 T1）而非單站 per-machine")
    ap.add_argument("--yield-scoreboard", dest="yield_sb", action="store_true",
                    help="印全線產出/良率計分板（§26 B/C，RAW v0）")
    ap.add_argument("--yield-station", dest="yield_st", metavar="CLS",
                    help="印單站產出/良率 + 機台下鑽（§26 B/C，RAW v0）")
    ap.add_argument("--json", action="store_true", help="印 canonical JSON")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest_overlay())
    source = "demo" if args.demo else args.source
    if args.yield_sb:
        yb = get_yield_scoreboard(source, days=args.days, snapshot_path=args.file)
        if args.json:
            print(json.dumps(yb, ensure_ascii=False, default=str))
            return
        rb = yb["rollup"]
        an = rb["anchor"] or {}
        print(f"source={yb['source']}  mes_now={yb['mes_now']}  口徑＝{yb['caliber']}（RAW v0）")
        print(f"  anchor={an.get('cls')}  產出(distinct lot) {an.get('output_units')}  "
              f"fail%raw {an.get('fail_pct_raw')}  FPY {an.get('fpy_raw_pct')}  "
              f"｜ 產出站 {rb['n_output_stations']}/{rb['n_stations']}  良率訊號站 {rb['n_yield_stations']}")
        for s in yb["stations"][:12]:
            print(f"    {s['cls']:<42} 產出 {s['output_units']:>6}  fail%raw {s['fail_pct_raw']:>6.1f}  "
                  f"FPY {('%.1f' % s['fpy_raw_pct']) if s['fpy_raw_pct'] is not None else '  n/a':>6}")
        return
    if args.yield_st:
        ys = get_yield_station(source, cls=args.yield_st, days=args.days, snapshot_path=args.file)
        if args.json:
            print(json.dumps(ys, ensure_ascii=False, default=str))
            return
        s = ys["station"]
        print(f"source={ys['source']}  {s['cls']}  口徑＝{ys['caliber']}（RAW v0）  fleet {s.get('fleet_size')} 台")
        print(f"  產出 {s['output_units']} (distinct lot)  good {s['good_units']}  fail%raw {s['fail_pct_raw']:.1f}")
        for m in (s.get("machines") or []):
            print(f"    {m['eid']:<12} 產出 {m['output_units']:>6}  good {m['good_units']:>6}  "
                  f"fail%raw {('%.1f' % m['fail_pct_raw']) if m['fail_pct_raw'] is not None else 'n/a'}")
        return
    if args.scoreboard:
        sb = get_scoreboard(source, days=args.days, stop_min=args.stop_min, snapshot_path=args.file)
        if args.json:
            print(json.dumps(sb, ensure_ascii=False, default=str))
            return
        fl = sb["fleet"]
        print(f"source={sb['source']}  mes_now={sb['mes_now']}  "
              f"全線稼動 {fl['util']*100:.0f}%  站 {fl['n_active_stations']}/{fl['n_stations']} 活躍  "
              f"機台 {fl['n_machines']}  跳過 {len(sb['skipped'])}")
        for s in sb["stations"]:
            print(f"  {s['cls']:<42}{s['fleet_size']:>3}台  稼動 {s['util_strict_pct']:>3.0f}%  "
                  f"生產 {mt.hms(s['producing_sec'])}")
        return
    data = get_data(source, days=args.days, stop_min=args.stop_min, snapshot_path=args.file,
                    start=args.start, end=args.end)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return
    f = data["fleet"]
    print(f"source={data['source']}  mes_now={data['mes_now']}  機台 {len(data['machines'])} 台")
    print(f"機隊稼動 {f['util']*100:.0f}%  ｜  不明 {f['unknown_pct']*100:.0f}%（§25 健康訊號）")
    for m in data["machines"]:
        ag = m["agg"]
        print(f"  {m['eid']:<12} {m['desc']:<18} 稼動 {ag['util']*100:>3.0f}%  "
              f"段 {str(m['n_seg']):>4}  觀測 {mt.hms(ag['total_sec'])}  "
              f"不明 {mt.hms(ag['unknown_sec'])}  ({len(m['records'])} 段時間軸)")


if __name__ == "__main__":
    main()
