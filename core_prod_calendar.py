#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生產日曆 v0.1 產出端 `board.prod_calendar.v1`（SPEC-PROD-CALENDAR.md v0.1／ROADMAP #34）。

**這是什麼**：SALTMARSH 全廠「實際生產過的時段」——cmms NOTE-59（A12）反向需求的產出端。
兩個產出、一套事實（SPEC §3），外加一個強制自檢：
  1. **日級布林序列**：生產日 D＝[D 08:00, D+1 08:00) 台北（C-PC1 日界，永不用午夜界）；
     quorum＝綑名冊 EID 當日有 `designation='Production'` 段（C-PC2/C-PC3，字面精確比對）。
  2. **closure 清單**：全廠 Production 訊號軸（僅名冊綑 EID、僅 Production 段）上連續沉默
     ≥ 240 分（C-PC5；[240,600) 分實測零觀測死區＝FINDINGS §9）為一個 closure；
     邊界＝真實訊號時刻（前段 stpEndTime → 後段 stpStartTime），不貼齊班表、不貼齊日界。
  3. **休日盤點自檢（SPEC §3、強制）**：每個被宣告的休息生產日附當日**全 MES 段**盤點
     （哪些 EID、什麼 designation、幾格）——designation 值域漂移或 quorum bug 的雙保險哨兵
     （FINDINGS §8-2 的形：4/10 休日曾被整日型 Engineering 段蓋滿，不分 designation 就誤判）。

**不是什麼**（C-PC10 命名鐵則）：本檔產出「實跑窗／生產時段」，**不是班表**——排了班但沒跑
（達標提早關、缺料）在 MES 上與沒排班不可分；任何欄位/變數/文案不得叫 schedule/班表。
G1（SALTMARSH 真排班檔）仍不存在，本日曆不是它的替代品。
**範圍（#34）**：只做產出端。交付/傳遞機制（SPEC §4 wire 形＝草案）凍結在與 cmms QG-3
對版時，本檔不建端點、不推播；`board.last_production.v1` 一個位元不動。

**coverage＝[首訊號起點, 觀測終點]（C-PC8／QG-3 ⑤；★ 2026-08-23 晚 Chris 拍板修訂尾端語意，
存證＝SPEC-PROD-CALENDAR.md §10）**：
  · **前緣 from＝首段訊號起點∩窗（不變、保守）**：窗前緣到首訊號之間我方沒觀測過 ⇒ 不主張。
  · **尾緣 to＝觀測終點＝min(查詢窗尾, generated_at)**，**不再是「末筆訊號時刻」**。
    why：probe 的查詢窗一路涵蓋到執行當下 ⇒「末訊號之後沒有訊號」是**觀測到的沉默**、
    不是未觀測。舊語意（訊號跨度）有結構性後果：夜班末訊號 ~06:07、生產日界 08:00、
    日曆每日 08:20 產生 ⇒ **最新一個生產日永遠差最後兩小時而不被主張**（C-PC8 要求日級
    主張須完整落在 coverage 內），例會最想看的「昨天」在 /equipment 換軸（#39）後整欄
    結構性顯「—」。觀測時刻與 `generated_at` **同源**（run_live 兩者都由 win_end 來）⇒
    selftest 確定性不靠 wall clock。
  · **尾端未閉合的沉默**：closure 需要右邊界＝下一個真實訊號。若某生產日 quorum 為假、
    卻沒有任何 closure 蓋得住（沉默還在進行中），該日**暫不主張**——否則吐出「休息日沒有
    closure」的不自洽產出（`ok_calendar` 會擋 ⇒ live 整輪不落盤）。下一輪有訊號封口即補上。
  · 零訊號（MES 降級/空窗）⇒ coverage 收成零寬（from==to==窗尾）、三清單全空＝什麼都不
    主張、絕不編造（**不變**）。

**紀律**：
  · 名冊唯一出處＝`_probe_kun_activity.KUN_ROSTER`（C-PC2；import 重用、不另抄）。
    附屬件/Zone D/31335 不在名冊 ⇒ 天然不進 quorum（C-PC2），但**照進休日盤點**（盤點是全段）。
  · 時鐘一條路（QG-3 ⑥）：MES 台北牆鐘 naive → wire UTC Z 一律走
    `core_lastprod_push.taipei_to_utc_dt`/`to_utc_z`，不另寫轉換。
    （附帶而承重的巧合：台北 08:00 ＝ UTC 00:00 ⇒ 生產日 D 的 UTC 跨度恰為
    [D 00:00Z, D+1 00:00Z)；selftest 有釘，`PROD_DAY_START_H` 改了會先紅。）
  · 零工期段是合法訊號點（M4／[[mes-zero-length-segments]]）：start==end 照收、可切開沉默。
  · `tw_calendar.json`（政府行事曆）**永不作為本檔資料源**（C-PC4）；不硬編星期規則（C-PC9）。
  · **live 由 Chris 在域內機器跑**（agent 不連 MES_DB）：
        python core_prod_calendar.py --days 30      # 30d＝唯一有 perf 實證的窗寬；加寬先量時間
    離線驗證：python core_prod_calendar.py --selftest
    fixture 重建（golden 由本檔 builder 實跑產生、**不得手改**）：
        python core_prod_calendar.py --make-fixture
"""
import argparse
import copy
import datetime as _dt
import io
import json
import os
import sys
import time

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import _probe_kun_activity as ka                    # 名冊/生產日界/slot 位圖唯一出處
from core_lastprod_push import (TAIPEI_OFFSET_H, parse_utc_z,   # 時鐘一條路（QG-3 ⑥）
                              taipei_to_utc_dt, to_utc_z)

REPO = os.path.dirname(os.path.abspath(__file__))
FIXTURE_PATH = os.path.join(REPO, "_fixtures", "board.prod_calendar.v1.json")
OUT_LATEST = os.path.join(REPO, "_PROD-CALENDAR-LATEST.json")

SCHEMA_ID = "board.prod_calendar.v1"
DAY_BOUNDARY_LABEL = "08:00 Asia/Taipei"            # C-PC1（§4 草案的宣告欄、字面凍結）
CLOSURE_MIN_MIN = 240                               # C-PC5（A1 已拍板；改值先讀 FINDINGS §9）
WALL_FMT = "%Y-%m-%dT%H:%M:%S"                      # MES 台北牆鐘 naive（fixture _build_inputs 同形）
UTC_Z_FMT = "%Y-%m-%dT%H:%M:%SZ"
TOP_KEYS = ("schema_id", "generated_at", "day_boundary", "coverage",
            "days", "closures", "rest_day_audit")
# ★ #182-P2（2026-09-25；`SPEC-PROD-CALENDAR.md` §11）＝**加法、選配**頂層鍵：
#   `open_silence_from`＝尾端**尚未閉合**的全廠沉默起點（＝末筆訊號時刻、UTC Z）。
#   只在「末訊號之後、觀測終點之前確實有沉默」時才出現；沒有 ⇒ **整個鍵缺席**
#   （舊 doc／零訊號降級形都不帶它 ⇒ 既有消費者逐位元不變）。closure 仍只由真實訊號封口
#   （C-PC5 不動）——這個鍵只是把「還在進行中的那段沉默」**講出來**，讓消費端（日帳）知道
#   建帳當下這一天的尾巴還沒定形（`cal_open_tail`），而不是把它當成「有涵蓋、零 closure」。
OPTIONAL_TOP_KEYS = ("open_silence_from",)
SEG_KEYS = ("eid", "designation", "n_segments", "slots")

# 名冊 EID 展平（唯一出處＝probe 的 KUN_ROSTER；共用 EID 重複展平無妨——是集合）。
ROSTER_EIDS = frozenset(e for _k, _a, _n, _es in ka.KUN_ROSTER for e in _es)


# ── 純函式核心（selftest 蓋這層；I/O 不進來）────────────────────────────────────
def _valid_row(eid, end, start, desig):
    """rows 元組健檢（欄序＝`build_lastprod_sql`：eid, end, start, designation）。
    end < start 由 SQL `stpEndTime >= stpStartTime` 結構性排除，防的是合成輸入。"""
    return bool(eid) and start is not None and end is not None and end >= start


def signal_timeline(rows):
    """rows → 依 start 升冪的 [(start, end)]＝**全廠 Production 訊號軸**。
    僅名冊綑 EID、僅 `designation == 'Production'`（C-PC2/C-PC3 同一過濾、字面精確）。
    零工期段（start == end）是合法訊號點、照收（M4 鐵則）。"""
    sigs = [(s, e) for eid, e, s, desig in rows
            if desig == "Production" and eid in ROSTER_EIDS and _valid_row(eid, e, s, desig)]
    sigs.sort()
    return sigs


def closures_between(sigs):
    """訊號軸 → [(silence_start, silence_end)]：連續全廠沉默 ≥ 240 分（C-PC5）。
    邊界＝真實訊號時刻（前段 end 的滾動最大值 → 後段 start）；段可重疊 ⇒ 取滾動 max end，
    否則長段內嵌一個短段會製造假沉默。首訊號之前／末訊號之後不產生 closure
    （沒有兩側真實邊界 ⇒ 屬 coverage 外＝未涵蓋，見檔頭 coverage 節）。"""
    out, cur_end = [], None
    for s, e in sigs:
        if cur_end is not None and (s - cur_end) >= _dt.timedelta(minutes=CLOSURE_MIN_MIN):
            out.append((cur_end, s))
        cur_end = e if cur_end is None else max(cur_end, e)
    return out


def quorum_days(rows):
    """rows → set(生產日 date)：該日有任一名冊 EID 的 Production 段觸及（C-PC2 quorum）。
    「觸及」與 slot 邏輯同界（08:00 生產日界、含端點）；跨日段點亮所有跨到的生產日。"""
    days = set()
    for eid, e, s, desig in rows:
        if desig == "Production" and eid in ROSTER_EIDS and _valid_row(eid, e, s, desig):
            d, dl, g = ka.production_day(s), ka.production_day(e), 0
            while d <= dl and g < 400:
                days.add(d)
                d += _dt.timedelta(days=1)
                g += 1
    return days


def rest_day_audit(rows, rest_dates):
    """休日盤點（SPEC §3、強制）：每個被宣告的休息生產日 → 當日**全 MES 段**
    （不分 designation、不分名冊內外）依 (eid, designation) 聚合的
    {eid, designation, n_segments, slots}。slots＝當日觸及的 10 分鐘格數
    （走 probe 的 slot 機制＝同一套日界）。整日零段 ⇒ segments=[]（誠實；
    FINDINGS §8-2：6/10 休日全空是常態）。"""
    want = {_dt.date.fromisoformat(x): x for x in rest_dates}
    acc = {iso: {} for iso in rest_dates}
    for eid, e, s, desig in rows:
        if not _valid_row(eid, e, s, desig):
            continue
        for dobj, slots in ka.slot_spans(s, e).items():
            iso = want.get(dobj)
            if iso is None:
                continue
            rec = acc[iso].setdefault((eid, desig), [0, set()])
            rec[0] += 1
            rec[1] |= slots
    out = []
    for iso in rest_dates:
        segs = [{"eid": k[0], "designation": k[1], "n_segments": v[0], "slots": len(v[1])}
                for k, v in acc[iso].items()]
        segs.sort(key=lambda r: (-r["slots"], r["eid"], r["designation"] or ""))
        out.append({"date": iso, "segments": segs})
    return out


def observation_end(generated_at_z, fallback):
    """觀測終點（台北牆鐘 naive）＝`generated_at` 換回台北軸（★ 2026-08-23 語意修訂的軸）。

    · **可注入、確定性**：時刻取自傳進來的 `generated_at_z`（run_live＝win_end；fixture＝
      `_build_inputs` 的字面值），**本函式永不讀 wall clock**——讀了 golden 每跑一次就變、
      逐位元比對當場失效。
    · 時鐘一條路（QG-3 ⑥）：解析與偏移都用 `core_lastprod_push` 的 `parse_utc_z`／
      `TAIPEI_OFFSET_H`，不另寫轉換、不裸呼 `astimezone()`（那會吃執行機器時區）。
    · 時戳壞掉 ⇒ 回 `fallback`（呼叫端給末訊號時刻＝**退回舊語意的保守側**）；
      不炸整輪，而該 doc 反正會被 `ok_calendar` 的 `_z_ok(generated_at)` 擋下。
    """
    u = parse_utc_z(generated_at_z)
    return fallback if u is None else u + _dt.timedelta(hours=TAIPEI_OFFSET_H)


def compute_calendar(rows, win_start, win_end, generated_at_z):
    """核心產出（純函式）：rows（台北牆鐘 naive、欄序 eid,end,start,desig）＋查詢窗 → doc。

    · coverage＝[首訊號起點, **觀測終點**]∩窗（檔頭 coverage 節；尾端語意 2026-08-23 修訂
      ＝SPEC §10：末訊號之後的沉默是**觀測到的**、不是未觀測）。
    · days＝**完整落在 coverage 內**的生產日才主張（部分覆蓋的邊緣日＝未涵蓋）；
      **且**休息日必須有 closure 蓋得住才主張（尾端未閉合的沉默＝暫不主張，見下）。
    · 零訊號 ⇒ coverage 零寬（from==to==窗尾）、三清單全空＝什麼都不主張（絕不編造）。
    """
    sigs = signal_timeline(rows)
    if not sigs:
        z = to_utc_z(win_end)
        return {"schema_id": SCHEMA_ID, "generated_at": generated_at_z,
                "day_boundary": DAY_BOUNDARY_LABEL,
                "coverage": {"from": z, "to": z},
                "days": [], "closures": [], "rest_day_audit": []}
    cov_from = max(win_start, sigs[0][0])
    sig_end = max(e for _s, e in sigs)
    # 尾緣＝觀測終點∩窗；`max(sig_end, …)` 是防呆：收到的訊號永遠算已觀測，
    # 不會因為時戳落在訊號之前而把真實證據截掉（截了 closure 會掉出 coverage、validator 紅）。
    cov_to = min(win_end, max(sig_end, observation_end(generated_at_z, sig_end)))
    if cov_to < cov_from:                       # 防呆（窗與訊號不相交的退化形）
        cov_to = cov_from
    cls = closures_between(sigs)
    days_out = []
    d, dl, g = ka.production_day(cov_from), ka.production_day(cov_to), 0
    qd = quorum_days(rows)
    while d <= dl and g < 400:
        ds = _dt.datetime.combine(d, _dt.time(ka.PROD_DAY_START_H))
        de = ds + _dt.timedelta(days=1)
        prod = d in qd
        # ★ 尾端未閉合沉默的守門（隨 2026-08-23 尾緣語意一起進來）：coverage 現在會伸到
        #   觀測終點 ⇒ 一個「沉默還在進行中」的休息日可能整日落進 coverage，但它的 closure
        #   還沒有右邊界（closure 邊界＝真實訊號時刻，C-PC5，永不用觀測時刻假造）。
        #   主張了就是吐出「休息日沒有 closure」的不自洽產出 ⇒ 暫不主張，下一輪封口再補。
        if ds >= cov_from and de <= cov_to \
                and (prod or any(a <= ds and de <= b for a, b in cls)):
            days_out.append({"date": d.isoformat(), "production": prod})
        d += _dt.timedelta(days=1)
        g += 1
    closures = [{"start": to_utc_z(a), "end": to_utc_z(b)} for a, b in cls]
    rest = [x["date"] for x in days_out if not x["production"]]
    out = {"schema_id": SCHEMA_ID, "generated_at": generated_at_z,
           "day_boundary": DAY_BOUNDARY_LABEL,
           "coverage": {"from": to_utc_z(cov_from), "to": to_utc_z(cov_to)},
           "days": days_out, "closures": closures,
           "rest_day_audit": rest_day_audit(rows, rest)}
    # ★ #182-P2（§11）：尾端未閉合沉默的起點（加法、選配鍵；見 `OPTIONAL_TOP_KEYS`）。
    #   末訊號之後到觀測終點之間有沉默 ⇒ 講出它從哪裡開始；**不設門檻**：一段 2 小時的
    #   沉默在產生當下與「會變成 closure 的長停」不可分（09-13 建帳時才 2h11m），判斷交給
    #   消費端的「重疊 ∧ 事後封口狀態」，產出端只陳述事實、不猜它會多長。
    if sig_end < cov_to:
        out["open_silence_from"] = to_utc_z(sig_end)
    return out


# ── validator（provider governance；live 寫檔前的閘、fixture invalid 的判官）────────
def _z_ok(ts):
    if not isinstance(ts, str) or not ts.endswith("Z"):
        return False
    try:
        _dt.datetime.strptime(ts, UTC_Z_FMT)
        return True
    except ValueError:
        return False


def _parse_z(ts):
    return _dt.datetime.strptime(ts, UTC_Z_FMT)


def ok_calendar(doc):
    """True＝合法。字面值比對（不拿常數比對自己＝lastprod invalid[3] 那課）。
    蓋 SPEC §8 ② 的產出端不變式：closure 恆 ≥240 分且升冪不重疊、與日級布林一致
    （休日必被 closure 全蓋、訊號日不得整日落在 closure 內）、休日盤點逐日在場欄位齊、
    盤點內不得出現「名冊 EID ∧ Production」（quorum 矛盾哨兵）、
    零寬 coverage ⇒ 三清單必空（降級形什麼都不主張）。"""
    if not isinstance(doc, dict):
        return False
    r = {k: v for k, v in doc.items() if not k.startswith("_")}
    # ★ #182-P2：必備鍵一個不能少、選配鍵（`OPTIONAL_TOP_KEYS`）可有可無，其餘一律拒收
    #   （未知頂層鍵仍然紅＝反靜默：多一個沒人認得的欄位不該悄悄過閘）。
    if not set(TOP_KEYS) <= set(r) or set(r) - set(TOP_KEYS) - set(OPTIONAL_TOP_KEYS):
        return False
    if r["schema_id"] != "board.prod_calendar.v1":
        return False
    if r["day_boundary"] != "08:00 Asia/Taipei":
        return False
    if not _z_ok(r["generated_at"]):
        return False
    cov = r["coverage"]
    if not isinstance(cov, dict) or set(cov) != {"from", "to"}:
        return False
    if not (_z_ok(cov["from"]) and _z_ok(cov["to"])):
        return False
    cf, ct = _parse_z(cov["from"]), _parse_z(cov["to"])
    if cf > ct:
        return False
    days, closures, audit = r["days"], r["closures"], r["rest_day_audit"]
    if not (isinstance(days, list) and isinstance(closures, list) and isinstance(audit, list)):
        return False
    if cf == ct and (days or closures or audit):
        return False
    prev_d, spans, false_dates = None, [], []
    for it in days:
        if not isinstance(it, dict) or set(it) != {"date", "production"}:
            return False
        if not isinstance(it["production"], bool):
            return False
        try:
            dobj = _dt.date.fromisoformat(it["date"])
        except (TypeError, ValueError):
            return False
        if prev_d is not None and dobj <= prev_d:
            return False
        prev_d = dobj
        ds = taipei_to_utc_dt(_dt.datetime.combine(dobj, _dt.time(ka.PROD_DAY_START_H)))
        de = ds + _dt.timedelta(days=1)
        if ds < cf or de > ct:                  # 日級主張必完整落在 coverage 內（C-PC8）
            return False
        spans.append((ds, de, it["production"]))
        if not it["production"]:
            false_dates.append(it["date"])
    prev_end, cls = None, []
    for it in closures:
        if not isinstance(it, dict) or set(it) != {"start", "end"}:
            return False
        if not (_z_ok(it["start"]) and _z_ok(it["end"])):
            return False
        a, b = _parse_z(it["start"]), _parse_z(it["end"])
        if not a < b:
            return False
        if b - a < _dt.timedelta(minutes=CLOSURE_MIN_MIN):
            return False
        if a < cf or b > ct:
            return False
        if prev_end is not None and a <= prev_end:      # 升冪且不重疊
            return False
        prev_end = b
        cls.append((a, b))
    # ★ #182-P2（§11）：尾端未閉合沉默的起點——在場就必須自洽：
    #   ① UTC Z 形；② 落在 coverage 內且**嚴格早於**觀測終點（沉默長度 > 0；零寬 coverage
    #   的降級形因此天然不可能帶它）；③ 不早於任何已封口 closure 的結束（沉默不可能開始
    #   在一段已經被下一筆訊號封口的沉默之前）。
    if "open_silence_from" in r:
        osf = r["open_silence_from"]
        if not _z_ok(osf):
            return False
        o = _parse_z(osf)
        if not (cf <= o < ct):
            return False
        if cls and cls[-1][1] > o:
            return False
    for ds, de, prod in spans:
        covered = any(a <= ds and de <= b for a, b in cls)
        if prod and covered:            # 有訊號的日整日被 closure 蓋＝計畫否定訊號（C-PC4 鏡像）
            return False
        if not prod and not covered:    # 休日沒被蓋＝停機憑空消失側（構造保證被打破）
            return False
    if [a.get("date") for a in audit if isinstance(a, dict)] != false_dates:
        return False                    # 盤點逐休日在場、同序（§3 強制自檢）
    for a in audit:
        if not isinstance(a, dict) or set(a) != {"date", "segments"}:
            return False
        if not isinstance(a["segments"], list):
            return False
        for sgt in a["segments"]:
            if not isinstance(sgt, dict) or set(sgt) != set(SEG_KEYS):
                return False
            if not (isinstance(sgt["eid"], str) and sgt["eid"]):
                return False
            if not (sgt["designation"] is None or isinstance(sgt["designation"], str)):
                return False
            if not (isinstance(sgt["n_segments"], int) and sgt["n_segments"] >= 1):
                return False
            if not (isinstance(sgt["slots"], int) and 1 <= sgt["slots"] <= ka.SLOTS_PER_DAY):
                return False
            if sgt["eid"] in ROSTER_EIDS and sgt["designation"] == "Production":
                return False            # quorum 矛盾哨兵：休日盤點裡不可能有名冊 Production 段
    return True


# ── fixture（golden 由 builder 實跑產生、不得手改；invalid＝合法基底只動一處）──────
def build_from_inputs(bi):
    """golden `_build_inputs` → doc（與 `run_live` 的計算路徑逐字同一條＝`compute_calendar`）。
    `rows` 存讀者友善欄序 [eid, start, end, designation]（台北牆鐘），此處轉回內部欄序。"""
    rows = [(eid, _dt.datetime.strptime(end, WALL_FMT),
             _dt.datetime.strptime(start, WALL_FMT), desig)
            for eid, start, end, desig in bi["rows"]]
    return compute_calendar(rows,
                            _dt.datetime.strptime(bi["win_start"], WALL_FMT),
                            _dt.datetime.strptime(bi["win_end"], WALL_FMT),
                            bi["generated_at"])


# golden 1：主劇本（真名冊 EID、手擬時刻）。涵蓋的值域形＝
#   跨午夜段留在同一生產日（C-PC1）／夜間 3h50m 空窗不成 closure（A5 保守、實形）／
#   用餐 50 分空窗不成 closure／零工期名冊訊號點／Baseline 段不進 quorum（C-PC3）／
#   附屬件段不進 quorum（C-PC2）／整休日＋34h closure／盤點含「roster Engineering 污染」
#   （FINDINGS §8-2 形）與「Zone D 機休日跑段」（C-PC2 排除可見）／coverage 兩側邊緣日不主張。
_G1_INPUTS = {
    "win_start": "2026-08-09T12:00:00", "win_end": "2026-08-14T06:00:00",
    "generated_at": "2026-08-13T22:30:00Z",
    "rows": [
        ["EID-25872", "2026-08-10T08:30:00", "2026-08-10T16:00:00", "Production"],
        ["EID-17431", "2026-08-10T16:10:00", "2026-08-11T04:30:00", "Production"],
        ["EID-25872", "2026-08-11T08:20:00", "2026-08-11T12:00:00", "Production"],
        ["EID-25590", "2026-08-11T12:50:00", "2026-08-11T22:00:00", "Production"],
        ["EID-27854", "2026-08-11T13:00:00", "2026-08-11T13:00:00", "Production"],
        ["EID-25872", "2026-08-11T14:00:00", "2026-08-11T15:00:00", "Engineering (Baseline)"],
        ["EID-01450", "2026-08-11T10:00:00", "2026-08-11T10:30:00", "Production"],
        ["EID-17431", "2026-08-12T10:00:00", "2026-08-12T11:00:00", "Engineering (Baseline)"],
        ["EID-07905", "2026-08-12T09:00:00", "2026-08-12T09:20:00", "Production"],
        ["EID-17431", "2026-08-13T08:15:00", "2026-08-13T16:00:00", "Production"],
    ],
}
# golden 2：降級形（零名冊 Production 訊號——只有 Baseline 與附屬件段）⇒ coverage 零寬、
# 三清單全空＝「MES 降級 ⇒ 該期間標未涵蓋、絕不編造」（SPEC §8 ④ 指名的 case）。
_G2_INPUTS = {
    "win_start": "2026-08-10T08:00:00", "win_end": "2026-08-12T08:00:00",
    "generated_at": "2026-08-12T01:00:00Z",
    "rows": [
        ["EID-25872", "2026-08-10T09:00:00", "2026-08-10T10:00:00", "Engineering (Baseline)"],
        ["EID-01450", "2026-08-10T11:00:00", "2026-08-10T11:30:00", "Production"],
    ],
}
# golden 3（★ 2026-08-23 尾緣語意修訂的存證形）：排程實形——每日 08:20 產生、窗尾＝產生時刻。
# 夜班末訊號 06:07 落在生產日界 08:00 之前 ⇒ **舊語意下最新生產日（08-18）永遠不被主張**；
# 新語意下觀測終點＝08-19 08:20 ≥ 08-19 08:00 ⇒ 08-18 完整落在 coverage 內、被正常主張。
# 同一份還釘住首端不變（08-16 首訊號在 20:00 ⇒ 該日仍誠實不主張）。
_G3_INPUTS = {
    "win_start": "2026-07-20T08:20:00", "win_end": "2026-08-19T08:20:00",
    "generated_at": "2026-08-19T00:20:00Z",          # ＝台北 08-19 08:20（與窗尾同源）
    "rows": [
        ["EID-25872", "2026-08-16T20:00:00", "2026-08-16T23:00:00", "Production"],
        ["EID-25872", "2026-08-17T08:30:00", "2026-08-17T16:00:00", "Production"],
        ["EID-17431", "2026-08-17T16:10:00", "2026-08-18T06:07:00", "Production"],
        ["EID-25872", "2026-08-18T08:30:00", "2026-08-18T16:00:00", "Production"],
        ["EID-17431", "2026-08-18T16:10:00", "2026-08-19T06:07:00", "Production"],
    ],
}


def _fixture_doc():
    """組整份 fixture（`--make-fixture` 用）。invalid 變體＝golden1 合法基底 deepcopy 後
    **只動一處**宣稱的缺陷（selftest 驗 record 被拒 ∧ _repaired 被收＝壞對地方）。"""
    g1, g2 = build_from_inputs(_G1_INPUTS), build_from_inputs(_G2_INPUTS)
    g3 = build_from_inputs(_G3_INPUTS)
    base = copy.deepcopy(g1)

    def mut(case, reject_by, fn):
        rec = copy.deepcopy(base)
        fn(rec)
        return {"case": case, "reject_by": reject_by,
                "record": rec, "_repaired": copy.deepcopy(base)}

    invalid = [
        mut("★ coverage 缺 `to` 欄（QG-3 ① 指名變體）。coverage 是承重欄（C-PC8）——缺了它"
            "消費端無從分辨「日曆還沒算到」與「那天沒生產」，正是 cmms 停機錨那個病的重演。",
            "provider_governance", lambda r: r["coverage"].pop("to")),
        mut("★ closure 邊界順序反轉（start/end 對調；QG-3 ① 指名變體）。時間倒轉的 closure"
            "＝配對錯段的 provider bug 形（同 lastprod invalid[10] 那課），吐出即違治理。",
            "provider_governance",
            lambda r: r["closures"].__setitem__(0, {"start": r["closures"][0]["end"],
                                                    "end": r["closures"][0]["start"]})),
        mut("★ 用餐級空窗（50 分）被宣告成 closure（C-PC5/A5 違反）。<240 分的沉默是運營型暫停"
            "（用餐/換線/夜間空窗），判非生產＝天天砍掉真生產側的錯——A5 拍板寧可保守。",
            "provider_governance",
            lambda r: r["closures"].insert(0, {"start": "2026-08-11T04:00:00Z",
                                               "end": "2026-08-11T04:50:00Z"})),
        mut("★ closures 清空但休息日仍宣告——休日沒被任何 closure 蓋＝一致性由構造保證這條"
            "被打破（quorum 為假的日其沉默必 ≥240 分 ⇒ 必有 closure；沒有＝停機憑空消失側）。",
            "provider_governance", lambda r: r.__setitem__("closures", [])),
        mut("★ closure 往前吞掉整個有訊號的生產日（start 提前一整天）——訊號日整日落在"
            " closure 內＝「計畫/推導否定訊號」（C-PC4 單向規則的鏡像；MES 是事實）。",
            "provider_governance",
            lambda r: r["closures"][0].__setitem__("start", "2026-08-10T14:00:00Z")),
        mut("★ 休日盤點清空（SPEC §3 強制自檢缺席）。盤點是 designation 漂移與 quorum bug 的"
            "雙保險哨兵——少了它，A2（值域閉集）的兜底就不存在。",
            "provider_governance", lambda r: r.__setitem__("rest_day_audit", [])),
        mut("★ 休日盤點裡出現「名冊 EID ∧ designation='Production'」段——與該日 quorum=false"
            "自相矛盾（有這段那天就不是休日）＝quorum 實作出 bug 的長相，哨兵必須攔。",
            "provider_governance",
            lambda r: r["rest_day_audit"][0]["segments"][0].__setitem__("designation",
                                                                        "Production")),
        mut("`schema_id` 錯字（`board.prod_cal.v1`）⇒ 拒讀不猜。validator 用字面字串比對、"
            "不拿模組常數比對自己（lastprod invalid 同型存證：常數怎麼改都綠＝沒有守衛）。",
            "consumer", lambda r: r.__setitem__("schema_id", "board.prod_cal.v1")),
        mut("★ #182-P2：`open_silence_from`（尾端未閉合沉默起點）落在觀測終點之後——"
            "沉默不可能從還沒觀測到的時刻開始；吐出＝時鐘或 coverage 算錯的 provider bug 形"
            "（消費端會拿它判日帳的 `cal_open_tail`，錯了就是整天的分母判錯）。",
            "provider_governance",
            lambda r: r.__setitem__("open_silence_from", "2026-08-14T00:00:00Z")),
    ]
    return {
        "schema_id": SCHEMA_ID,
        "_status": "Board＝producer（SPEC-PROD-CALENDAR v0.1／ROADMAP #34）。★ 範圍＝產出端："
                   "wire 交付形（SPEC §4）仍是**草案**、凍結在與 cmms QG-3 對版時——本 fixture"
                   "是 Board 對**算得出來、算得對**的承諾存證，不是對 cmms 的 wire 承諾。"
                   "★ 合成樣本：EID 用真名冊（_probe_kun_activity.KUN_ROSTER），時刻手擬。",
        "_generated_by": "★★ golden 的每一個位元＝`core_prod_calendar.build_from_inputs`（→"
                         "`compute_calendar`）對 `_build_inputs` 實跑產生。**不得手改**——"
                         "selftest 會重跑並逐位（含鍵序）比對，手改立刻紅。invalid＝合法基底"
                         "deepcopy 只動一處宣稱缺陷（record 被拒 ∧ _repaired 被收）。"
                         "重生指令：python core_prod_calendar.py --make-fixture",
        "provenance": [
            {"field": "day_boundary / days[].date",
             "source": "C-PC1 生產日界＝台北 08:00（`SALTMARSH-KUN-STRUCTURE.md` §5.3；午夜界的實測"
                       "後果＝FINDINGS §8-1 週日假結論）。date＝生產日日期（台北軸日期欄），"
                       "跨度恰為 [D 00:00Z, D+1 00:00Z)（台北 08:00≡UTC 00:00）"},
            {"field": "days[].production",
             "source": "C-PC2 quorum（綑名冊任一 EID 當日有 Production 段；名冊唯一出處＝"
                       "`_probe_kun_activity.KUN_ROSTER`）＋C-PC3 designation 字面精確比對"
                       "（FINDINGS §8-2：不分 designation 誤判 4/10 休日）"},
            {"field": "closures[]",
             "source": "C-PC5：連續全廠靜默 ≥240 分；閾值實證＝FINDINGS §9（[240,600) 零觀測"
                       "死區、59 天逐位元同）。邊界＝真實訊號時刻（前段 stpEndTime→後段"
                       " stpStartTime），不貼齊班表/日界"},
            {"field": "coverage",
             "source": "C-PC8 未涵蓋誠實＋QG-3 ⑤；**尾端語意 2026-08-23 晚 Chris 拍板修訂**"
                       "（存證＝SPEC-PROD-CALENDAR §10）：from＝首訊號起點∩窗（首端保守不變）、"
                       "to＝**觀測終點** min(窗尾, generated_at)——查詢窗涵蓋到執行當下 ⇒ 末訊號"
                       "之後的沉默是觀測到的、不是未觀測（舊語意＝訊號跨度，結構性後果是最新一個"
                       "生產日永遠不被主張）。零訊號 ⇒ 零寬（from==to）＝什麼都不主張；前緣邊緣日"
                       "與「尾端沉默未閉合的休息日」不進 days"},
            {"field": "rest_day_audit[]",
             "source": "SPEC §3 強制自檢：每個休息生產日附當日全 MES 段盤點（EID/designation/"
                       "格數；格＝probe 的 10 分鐘 slot）＝designation 漂移與 quorum bug 哨兵"},
            {"field": "時刻格式",
             "source": "QG-3 ⑥：MES 台北牆鐘 naive → wire UTC Z，一律走"
                       " `core_lastprod_push.taipei_to_utc_dt`/`to_utc_z` 一條路、不另寫"},
        ],
        "golden": [
            dict({"_scenario": "★ 主劇本（5 日窗）：週型實形縮影。值域形涵蓋＝跨午夜段留在"
                               "同一生產日（C-PC1）／夜間 3h50m 空窗**不成** closure（A5 保守"
                               "＝ZC 實形）／用餐 50 分空窗不成 closure／零工期名冊訊號點"
                               "（EID-27854、M4 鐵則）／Baseline 段不進 quorum（C-PC3）／"
                               "附屬件（EID-01450）不進 quorum（C-PC2）／整休日 08-12 由 34h15m"
                               " closure 全蓋／盤點含 roster Engineering 污染（FINDINGS §8-2 形）"
                               "＋Zone D 機（EID-07905）休日跑段（C-PC2 排除的可見存證）／"
                               "coverage 兩側邊緣日（08-10 首訊號之前；08-13 **觀測時刻（台北"
                               " 08-14 06:30）早於其日界 08-14 08:00＝當日還沒過完**）誠實不主張。",
                  "_build_inputs": _G1_INPUTS}, **g1),
            dict({"_scenario": "★ 降級形（SPEC §8 ④ 指名 case）：窗內零名冊 Production 訊號"
                               "（只有 Baseline 與附屬件段）⇒ coverage 收成零寬（from==to）、"
                               "days/closures/盤點全空＝該期間**未涵蓋**、絕不編造成生產或"
                               "非生產（C-PC8）。",
                  "_build_inputs": _G2_INPUTS}, **g2),
            dict({"_scenario": "★ 排程實形（**2026-08-23 尾緣語意修訂的存證**；SPEC §10）："
                               "每日 08:20 產生、窗尾＝產生時刻，夜班末訊號 06:07（生產日界"
                               " 08:00 之前）。**舊語意（coverage.to＝末訊號）下最新生產日"
                               " 08-18 永遠差最後 1h53m 而不被主張**——例會最想看的「昨天」"
                               "結構性缺席；新語意 to＝觀測終點（台北 08-19 08:20）⇒ 08-18 完整"
                               "落在 coverage 內、正常主張。同份釘住首端不變（08-16 首訊號 20:00"
                               "＝該日仍誠實不主張）與 9h30m 夜間 closure。",
                  "_build_inputs": _G3_INPUTS}, **g3),
        ],
        "invalid": invalid,
        "_note": [
            "wire 交付形＝SPEC §4 草案（本 fixture 的 golden 形＝其超集：多 rest_day_audit 一欄"
            "＝§3 強制自檢隨產出）。傳遞機制（端點/推播/檔）未定＝與 cmms 對版時凍結；"
            "屆時盤點欄要不要上 wire、尾端進行中沉默（closure 尚無右邊界）怎麼表達，一併裁。",
            "★ 邊緣日語意（**2026-08-23 晚修訂尾端**；SPEC §10）：coverage.to＝**觀測終點**"
            "＝min(窗尾, generated_at)，不再是末訊號時刻 ⇒ 觀測時刻已越過日界的那個生產日"
            "**照常主張**（golden3＝08:20 排程實形），這正是修掉「最新一個生產日永遠缺席」。"
            "仍不主張的只有三種：①首訊號之前的前緣日（首端保守不變）②觀測時刻還沒越過日界"
            "的「今天」（golden1 尾端形）③尾端沉默未閉合的休息日（closure 缺右邊界）。",
            "本日曆產出的是「實跑窗／生產時段」——**不是班表**（C-PC10）。G1（SALTMARSH 真排班檔）"
            "仍不存在；拿本檔回答「該排幾個人／明天開不開」是它答不了的問題。",
            "quorum 名冊＝`_probe_kun_activity.KUN_ROSTER` 硬編（A4：Chris 人工維護；漂移哨兵＝"
            "probe 的 unknown EID 分流）。附屬件/Zone D 不進 quorum（A8 已拍板；翻案形＝立偽綑）。",
        ],
    }


def make_fixture():
    doc = _fixture_doc()
    with io.open(FIXTURE_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"fixture → {FIXTURE_PATH}")
    return 0


def check_fixture(eid_map=None):
    """fixture 承重驗證（QG-3 ①③④；`--selftest` 與 `_fixtures/selftest_fixtures.py` 都呼叫
    這一份——**不在兩處各寫一份**）。回 (n_golden, n_invalid)。
    `eid_map`（選配；預設 None＝逐位元照讀、私有樹唯一用法）＝{fixture 內 EID: 換成的 EID}：
    載入時對整份 fixture 文字做確定性 EID 換名（公開樹自測把名冊角色換成合成名冊的對應機台；
    fixture 檔本身零寫入、計算碼零改動）。"""
    with open(FIXTURE_PATH, "rb") as f:
        raw = f.read()
    assert raw.count(b"\r") == 0, "prod_calendar fixture 含 CR（行尾鐵則＝LF、md5 平台無關）"
    text = raw.decode("utf-8")
    if eid_map:
        import re as _re
        text = _re.sub(r"EID-\d{5}", lambda m: eid_map.get(m.group(0), m.group(0)), text)
    fx = json.loads(text)
    for k in ("schema_id", "provenance", "golden", "invalid"):
        assert k in fx, f"prod_calendar fixture 缺統一結構欄 {k}"
    assert fx["schema_id"] == "board.prod_calendar.v1", "fixture 頂層 schema_id 非契約字面值"
    assert SCHEMA_ID == "board.prod_calendar.v1", "實作 SCHEMA_ID 與契約字面不同版"
    for p in fx["provenance"]:
        assert p.get("field") and p.get("source"), "provenance 條目缺 field/source"
    n_golden = 0
    for gi, g in enumerate(fx["golden"]):
        built = build_from_inputs(g["_build_inputs"])
        want = {k: v for k, v in g.items() if not k.startswith("_")}
        assert json.dumps(built, ensure_ascii=False) == json.dumps(want, ensure_ascii=False), \
            (f"golden[{gi}] 與 compute_calendar 實跑輸出不一致（fixture 被手改，或產出形變了"
             f"——兩者都必須有人看一眼）")
        assert ok_calendar(g), f"golden[{gi}] 未過自家 validator"
        n_golden += 1
    # 值域形都要活著（只有一形＝另一形是死的，改壞了沒測試會紅）
    g1, g2 = fx["golden"][0], fx["golden"][1]
    prods = {d["production"] for d in g1["days"]}
    assert prods == {True, False}, "golden1 應同時有生產日與休息日"
    assert g1["closures"], "golden1 應有 closure"
    audit = g1["rest_day_audit"]
    assert audit and audit[0]["segments"], "golden1 盤點應非空（污染形的存證）"
    eids = {s["eid"] for s in audit[0]["segments"]}
    assert any(e in ROSTER_EIDS for e in eids), "盤點應含名冊機的非 Production 污染段（§8-2 形）"
    assert any(e not in ROSTER_EIDS for e in eids), "盤點應含名冊外段（C-PC2 排除的可見存證）"
    assert any(s == e for _eid, s, e, _d in g1["_build_inputs"]["rows"]), \
        "golden1 缺零工期訊號樣本（M4 鐵則的正確長相）"
    assert g2["coverage"]["from"] == g2["coverage"]["to"] and not g2["days"] \
        and not g2["closures"] and not g2["rest_day_audit"], \
        "golden2 降級形應為零寬 coverage＋三清單全空"
    # golden3＝2026-08-23 尾緣語意（SPEC §10）的存證：最新一個生產日必須在場，
    # 且 coverage.to 必須是觀測終點（＝generated_at）而非末訊號（06:07）。
    g3 = fx["golden"][2]
    assert g3["coverage"]["to"] == g3["generated_at"], \
        "golden3 的 coverage.to 應＝觀測終點（generated_at）——尾緣語意退回訊號跨度了"
    assert g3["days"] and g3["days"][-1] == {"date": "2026-08-18", "production": True}, \
        "golden3 應主張最新一個完整生產日 08-18（舊語意下它永遠缺席＝本次修訂的目標）"
    # invalid 雙向：record 被拒 ∧ _repaired 被收 ⇒ 壞在宣稱那一點
    n_invalid = 0
    for iv in fx["invalid"]:
        tag = iv["case"][:28]
        assert iv.get("case") and "record" in iv and "_repaired" in iv, \
            f"invalid[{tag}] 缺 case/record/_repaired"
        assert not ok_calendar(iv["record"]), f"invalid[{tag}] 竟通過 validator＝變體沒壞對地方"
        assert ok_calendar(iv["_repaired"]), f"invalid[{tag}] _repaired 竟也被拒＝壞在別處"
        assert iv["record"] != iv["_repaired"], f"invalid[{tag}] record==_repaired＝沒動到任何東西"
        n_invalid += 1
    assert n_invalid >= 2, "非法變體須 ≥2（QG-3 ①）"
    assert json.loads(text) == fx, "JSON round-trip 不一致"
    return n_golden, n_invalid


# ── I/O（live；Chris 跑）────────────────────────────────────────────────────────
def run_live(days):
    """live 產出（Chris 在域內機器跑；agent 不連 MES）。SQL 形＝`build_lastprod_sql`
    （C-LP1 實證形：逐 class 點查、零聚合下推、`OPTION (RECOMPILE)`、end-filter `>=`）。
    寫檔前過 `ok_calendar` 閘——驗不過就不落盤（絕不寫出壞產出）。"""
    from mes_qlik import _connect
    from mes_timeline import EQP_STATIONS
    from core_lastprod_push import build_lastprod_sql
    sql, _ = build_lastprod_sql(with_designation=True, with_result=False)
    win_end = _dt.datetime.now().replace(microsecond=0)     # 台北牆鐘（與 MES 同軸）
    win_start = win_end - _dt.timedelta(days=days)
    conn, cur = _connect(180)
    rows, t_all = [], time.monotonic()
    try:
        for cls in EQP_STATIONS:
            t0 = time.monotonic()
            cur.execute(sql, cls, days)
            got = cur.fetchall()
            rows.extend((r[0], r[1], r[2], r[3]) for r in got)
            print(f"  scan {cls:<44} rows={len(got):<6} {time.monotonic() - t0:.1f}s")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    print(f"total rows={len(rows)} in {time.monotonic() - t_all:.0f}s")
    doc = compute_calendar(rows, win_start, win_end, to_utc_z(win_end))
    if not ok_calendar(doc):
        print("✗ 產出未過 ok_calendar 閘——不落盤（請回報，這是 provider bug）")
        return 2
    with io.open(OUT_LATEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"calendar → {OUT_LATEST}")
    _print_report(doc)
    return 0


def _taipei(z):
    """wire UTC Z → 台北牆鐘字串（**僅供 stdout 摘要**；檔內一律 UTC Z）。"""
    return (_parse_z(z) + _dt.timedelta(hours=8)).strftime("%m-%d %H:%M")


def _print_report(doc):
    days = doc["days"]
    n_prod = sum(1 for d in days if d["production"])
    print(f"\n══ 生產日曆（日界 08:00 台北；coverage {doc['coverage']['from']} → "
          f"{doc['coverage']['to']}）══")
    print(f"  生產日 {n_prod}／休息日 {len(days) - n_prod}（共主張 {len(days)} 個完整生產日；"
          f"coverage.to＝觀測終點＝產生時刻——首訊號之前的前緣日、還沒過完的今天、"
          f"沉默尚未閉合的休息日，三者不主張）")
    for c in doc["closures"]:
        mins = int((_parse_z(c["end"]) - _parse_z(c["start"])).total_seconds() // 60)
        print(f"  closure {c['start']} → {c['end']}  （台北 {_taipei(c['start'])} → "
              f"{_taipei(c['end'])}；{mins/60:.1f}h）")
    if not doc["closures"]:
        print("  （無 closure）")
    print("  ── 休日盤點自檢（SPEC §3 強制；designation 漂移/quorum bug 哨兵）──")
    for a in doc["rest_day_audit"]:
        if not a["segments"]:
            print(f"  {a['date']}: 全 MES 零段（乾淨休日）")
            continue
        print(f"  {a['date']}: {len(a['segments'])} 組 (EID×designation)——逐組檢視：")
        for s in a["segments"]:
            tag = "名冊" if s["eid"] in ROSTER_EIDS else "名冊外"
            print(f"    {s['eid']}({tag}) {s['designation']!r} "
                  f"segs={s['n_segments']} slots={s['slots']}/144")
    if not doc["rest_day_audit"]:
        print("  （本 coverage 內無休息日）")


# ── selftest（離線、零 MES）─────────────────────────────────────────────────────
def _selftest():
    n = [0]

    def ck(cond, msg):
        n[0] += 1
        assert cond, f"FAIL [{n[0]}] {msg}"

    D = _dt.datetime
    MIN = _dt.timedelta(minutes=1)
    # 1) 承重巧合的釘：台北 08:00 ≡ UTC 00:00 ⇒ 生產日 D 的 UTC 跨度＝[D 00:00Z, D+1 00:00Z)。
    #    `PROD_DAY_START_H` 或時鐘路徑改了，這裡先紅（validator 的日跨度檢查倚賴它）。
    ck(ka.PROD_DAY_START_H == 8, "生產日界＝08:00（C-PC1）")
    ck(taipei_to_utc_dt(D(2026, 8, 12, 8, 0)) == D(2026, 8, 12, 0, 0),
       "台北 08:00 ≡ UTC 00:00（一條路轉換＝core_lastprod_push）")
    # 2) 名冊展平：唯一出處＝probe 名冊；附屬件/Zone D 天然不在
    # WP-10d（R-P11）：具名機台改由名冊推導（私有樹＝名冊內 25872／27854／17431、名冊外 01450〔別名〕／07905〔Zone D〕；公開樹＝合成名冊內三台＋名冊外兩個不在名冊的編號）。名冊外＝「附屬件／Zone D 不在名冊」的結構形，不是某個編號。
    import core_paths as _wp
    if _wp.is_public_tree():
        _rs = sorted(ROSTER_EIDS)
        _RA, _RB, _RC, _RD = _rs[0], _rs[1], _rs[2], _rs[3]
        _top = int(_rs[-1][4:])
        _NA, _NB = "EID-%05d" % (_top + 1), "EID-%05d" % (_top + 2)
    else:
        _RA, _RB, _RC, _RD, _NA, _NB = "EID-25872", "EID-27854", "EID-17431", "EID-25590", "EID-01450", "EID-07905"
    ck(ROSTER_EIDS == frozenset(e for _k, _a, _n, es in ka.KUN_ROSTER for e in es),
       "ROSTER_EIDS 恰為 KUN_ROSTER 展平")
    ck(_RA in ROSTER_EIDS and _NA not in ROSTER_EIDS
       and _NB not in ROSTER_EIDS, "附屬件/Zone D 不在名冊（C-PC2 排除的結構形）")
    # 3) signal_timeline：三重過濾＋排序＋零工期照收
    rows = [(_RA, D(2026, 8, 10, 16), D(2026, 8, 10, 8, 30), "Production"),
            (_NA, D(2026, 8, 10, 11), D(2026, 8, 10, 10), "Production"),
            (_RA, D(2026, 8, 10, 7), D(2026, 8, 10, 6), "Engineering (Baseline)"),
            (_RB, D(2026, 8, 10, 7, 0), D(2026, 8, 10, 7, 0), "Production")]
    sigs = signal_timeline(rows)
    ck(sigs == [(D(2026, 8, 10, 7, 0), D(2026, 8, 10, 7, 0)),
                (D(2026, 8, 10, 8, 30), D(2026, 8, 10, 16))],
       "非名冊/非 Production 濾除、零工期名冊訊號照收、依 start 升冪")
    ck(signal_timeline([(_RA, None, D(2026, 8, 10, 8), "Production")]) == [],
       "缺時刻列不進訊號軸")
    # 4) closures_between：240 分邊界、重疊段滾動 max end、邊界＝真實訊號時刻
    a, b = D(2026, 8, 10, 8), D(2026, 8, 10, 12)
    ck(closures_between([(a, b), (b + 239 * MIN, b + 300 * MIN)]) == [],
       "239 分空窗不成 closure（<240）")
    ck(closures_between([(a, b), (b + 240 * MIN, b + 300 * MIN)])
       == [(b, b + 240 * MIN)], "恰 240 分空窗成 closure、邊界＝前段 end／後段 start")
    long_seg = (D(2026, 8, 10, 8), D(2026, 8, 10, 20))
    inner = (D(2026, 8, 10, 9), D(2026, 8, 10, 10))
    nxt = (D(2026, 8, 11, 2), D(2026, 8, 11, 4))
    ck(closures_between(sorted([long_seg, inner, nxt]))
       == [(D(2026, 8, 10, 20), D(2026, 8, 11, 2))],
       "重疊段取滾動 max end——長段內嵌短段不得製造假沉默")
    ck(closures_between([(a, b)]) == [], "單段無 closure（首末訊號之外不屬 closure）")
    # 5) quorum_days：跨午夜同生產日、跨 08:00 兩日、Baseline/附屬件不點亮
    qd = quorum_days([(_RC, D(2026, 8, 11, 4, 30), D(2026, 8, 10, 16, 10), "Production")])
    ck(qd == {_dt.date(2026, 8, 10)}, "跨午夜段留在同一生產日（C-PC1）")
    qd = quorum_days([(_RC, D(2026, 8, 11, 8, 5), D(2026, 8, 11, 7, 55), "Production")])
    ck(qd == {_dt.date(2026, 8, 10), _dt.date(2026, 8, 11)}, "跨 08:00 才點亮兩個生產日")
    ck(quorum_days([(_RA, D(2026, 8, 10, 10), D(2026, 8, 10, 9), "Engineering (Baseline)"),
                    (_NA, D(2026, 8, 10, 10), D(2026, 8, 10, 9), "Production")]) == set(),
       "Baseline 段與附屬件段都不進 quorum（C-PC2/C-PC3）")
    # 6) rest_day_audit：全段盤點（不分名冊/designation）、同鍵聚合、slot 計數、排序
    arows = [(_RC, D(2026, 8, 12, 11), D(2026, 8, 12, 10), "Engineering (Baseline)"),
             (_RC, D(2026, 8, 12, 13), D(2026, 8, 12, 12, 30), "Engineering (Baseline)"),
             (_NB, D(2026, 8, 12, 9, 20), D(2026, 8, 12, 9), "Production"),
             (_RA, D(2026, 8, 11, 10), D(2026, 8, 11, 9), "Production")]
    aud = rest_day_audit(arows, ["2026-08-12"])
    ck(len(aud) == 1 and aud[0]["date"] == "2026-08-12", "盤點逐休日一筆")
    ck(aud[0]["segments"][0] == {"eid": _RC, "designation": "Engineering (Baseline)",
                                 "n_segments": 2, "slots": 11},
       "同 (EID,designation) 聚合：2 段、slot 聯集 7+4=11 格、slots 多者在前")
    ck(aud[0]["segments"][1] == {"eid": _NB, "designation": "Production",
                                 "n_segments": 1, "slots": 3},
       "名冊外段照進盤點（盤點是全段、與 quorum 過濾無關）")
    ck(all(s["eid"] != _RA for s in aud[0]["segments"]),
       "非休日的段不進盤點")
    ck(rest_day_audit(arows, ["2026-08-14"]) == [{"date": "2026-08-14", "segments": []}],
       "整日零段 ⇒ segments=[]（誠實空、不是缺欄）")
    # 具名 golden 輸入（rows 裡的真名冊 EID）：私有樹原樣；公開樹把六個角色換成合成名冊的對應機台（名冊內 4 台＋名冊外 2 個編號）
    _EMAP = {"EID-25872": _RA, "EID-17431": _RC, "EID-25590": _RD, "EID-27854": _RB, "EID-01450": _NA, "EID-07905": _NB}

    def _inp(bi):
        return dict(bi, rows=[[_EMAP.get(row[0], row[0])] + list(row[1:]) for row in bi["rows"]])

    # 7) compute_calendar：golden1 劇本的承重形（fixture 逐位比對之外的語意釘）
    g1 = build_from_inputs(_inp(_G1_INPUTS))
    ck(g1["coverage"] == {"from": "2026-08-10T00:30:00Z", "to": "2026-08-13T22:00:00Z"},
       "coverage＝[首訊號起點, 觀測終點∩窗尾]（尾緣語意 2026-08-23 修訂＝SPEC §10；"
       "此劇本窗尾 08-14 06:00 早於觀測 06:30 ⇒ 取窗尾）")
    ck(g1["days"] == [{"date": "2026-08-11", "production": True},
                      {"date": "2026-08-12", "production": False}],
       "前緣日 08-10（首訊號之前）不主張；08-13 因**觀測時刻未越過其日界**（當日還沒過完）"
       "不主張；quorum 真/假各一")
    ck(g1["closures"] == [{"start": "2026-08-11T14:00:00Z", "end": "2026-08-13T00:15:00Z"}],
       "34h15m 沉默成 closure；3h50m 夜窗與 50 分用餐窗都不成（A5/C-PC5）")
    ck(g1["rest_day_audit"][0]["date"] == "2026-08-12"
       and len(g1["rest_day_audit"][0]["segments"]) == 2, "休日盤點在場、兩組段")
    ck(ok_calendar(g1), "golden1 過 validator")
    # 7b) ★ 尾緣語意＝觀測終點（2026-08-23 晚 Chris 拍板；存證＝SPEC-PROD-CALENDAR §10）。
    #     舊語意（coverage.to＝末訊號時刻）的結構性後果：夜班末訊號 06:07 < 日界 08:00 ⇒
    #     08:20 產生的日曆**永遠差最後兩小時**、最新一個生產日恆缺席（例會要看的「昨天」）。
    g3 = build_from_inputs(_inp(_G3_INPUTS))
    ck(g3["coverage"]["to"] == "2026-08-19T00:20:00Z" == g3["generated_at"],
       "① 尾緣＝觀測終點（＝generated_at 台北 08-19 08:20），不是末訊號 06:07")
    ck(g3["days"][-1] == {"date": "2026-08-18", "production": True},
       "① 觀測時刻已越過日界 ⇒ **最新一個生產日被主張**（舊語意下它永遠缺席＝修訂的目標）")
    ck([x["date"] for x in g3["days"]] == ["2026-08-17", "2026-08-18"],
       "① 首端不變：08-16 首訊號在 20:00 ⇒ 該日仍誠實不主張（前緣保守側一格沒鬆）")
    ck(ok_calendar(g3), "① 新形過 validator")
    #     ② 觀測時刻早於日界（當日未過完）⇒ 末欄仍不主張——同一份 rows 只把時鐘往回撥。
    early = dict(_inp(_G3_INPUTS), win_end="2026-08-19T07:00:00",
                 generated_at="2026-08-18T23:00:00Z")     # ＝台北 08-19 07:00，日界前一小時
    ge = build_from_inputs(early)
    ck(ge["coverage"]["to"] == "2026-08-18T23:00:00Z",
       "② 觀測終點就是那個時刻（訊號還是到 06:07，尾緣不再跟著訊號走）")
    ck([x["date"] for x in ge["days"]] == ["2026-08-17"],
       "② 觀測時刻未越過 08-19 08:00 ⇒ 08-18 仍不主張（不是漏算，是還沒過完）")
    #     觀測時刻可注入且確定性（永不讀 wall clock）；時戳壞掉退回舊語意的保守側。
    ck(observation_end("2026-08-19T00:20:00Z", D(2000, 1, 1)) == D(2026, 8, 19, 8, 20),
       "observation_end＝UTC Z→台北牆鐘（固定 +8、走 core_lastprod_push 一條路）")
    ck(observation_end("2026-08-19 00:20:00", D(2026, 8, 19, 6, 7)) == D(2026, 8, 19, 6, 7),
       "時戳壞掉 ⇒ 退回 fallback（呼叫端給末訊號＝舊語意保守側），不炸整輪")
    ck(build_from_inputs(dict(_inp(_G3_INPUTS), generated_at="not-a-timestamp"))["coverage"]["to"]
       == "2026-08-18T22:07:00Z",
       "壞時戳的整份產出＝退回末訊號跨度（06:07 台北）；該 doc 另由 _z_ok 擋下")
    #     ③ 尾端**未閉合**的沉默：沉默是觀測到的，但 closure 需要右邊界＝下一個真實訊號 ⇒
    #        沒 closure 蓋得住的休息日暫不主張（否則吐出「休息日沒有 closure」的不自洽產出）。
    _open = {"win_start": "2026-08-16T08:00:00", "win_end": "2026-08-18T08:20:00",
             "generated_at": "2026-08-18T00:20:00Z",
             "rows": [[_RA, "2026-08-16T20:00:00", "2026-08-16T23:00:00", "Production"]]}
    go = build_from_inputs(_open)
    ck(go["coverage"]["to"] == "2026-08-18T00:20:00Z" and go["days"] == []
       and go["closures"] == [], "③ 沉默還在進行中 ⇒ 08-17 休息日暫不主張（closure 缺右邊界）")
    ck(ok_calendar(go), "③ 誠實不主張仍是合法產出")
    _closed = copy.deepcopy(_open)
    _closed["rows"].append([_RA, "2026-08-18T08:05:00", "2026-08-18T08:10:00",
                            "Production"])
    gc = build_from_inputs(_closed)
    ck(gc["days"] == [{"date": "2026-08-17", "production": False}] and len(gc["closures"]) == 1
       and gc["rest_day_audit"][0]["date"] == "2026-08-17",
       "③ 下一輪有訊號封口 ⇒ 同一個休息日補上（配對釘：差別只在有沒有右邊界）")
    ck(ok_calendar(gc), "③ 封口後仍過 validator（休日必被 closure 全蓋的構造保證還在）")
    # 7c) ★ #182-P2（§11）：尾端未閉合沉默的起點＝加法選配鍵 `open_silence_from`。
    ck(go.get("open_silence_from") == "2026-08-16T15:00:00Z",
       "④ 沉默進行中 ⇒ 講出起點＝末訊號時刻（台北 08-16 23:00＝15:00Z），不設長度門檻")
    ck(gc.get("open_silence_from") == "2026-08-18T00:10:00Z" and ok_calendar(gc),
       "④ 封口後尾端只剩 08:10→08:20 的新沉默：起點前移到最新末訊號、舊的那段已成 closure")
    ck(g3.get("open_silence_from") == "2026-08-18T22:07:00Z",
       "④ 排程實形：08:20 產生時夜班末訊號 06:07 之後的沉默照實陳述（生產日 08-18 尾巴未定形）")
    _live = dict(_inp(_G3_INPUTS), rows=_inp(_G3_INPUTS)["rows"] + [
        [_RA, "2026-08-19T08:15:00", "2026-08-19T08:20:00", "Production"]])
    ck("open_silence_from" not in build_from_inputs(_live),
       "④ 末訊號恰在觀測終點 ⇒ 沒有沉默 ⇒ **整個鍵缺席**（不是 null）")
    ck("open_silence_from" not in build_from_inputs(_inp(_G2_INPUTS)),
       "④ 零訊號降級形不帶它（零寬 coverage 不可能有沉默）")
    bad = copy.deepcopy(go); bad["open_silence_from"] = "2026-08-18T00:20:00Z"
    ck(not ok_calendar(bad), "④ 起點＝觀測終點（零長沉默）拒收")
    bad = copy.deepcopy(gc); bad["open_silence_from"] = "2026-08-17T00:00:00Z"
    ck(not ok_calendar(bad), "④ 起點早於已封口 closure 的結束 ⇒ 自相矛盾、拒收")
    bad = copy.deepcopy(go); bad["open_silence_from"] = "2026-08-16 15:00:00"
    ck(not ok_calendar(bad), "④ 非 UTC Z 形拒收")
    bad = copy.deepcopy(go); bad["mystery"] = 1
    ck(not ok_calendar(bad), "④ 選配鍵之外的未知頂層鍵照樣拒收（反靜默）")
    _old = {k: v for k, v in go.items() if k != "open_silence_from"}
    ck(ok_calendar(_old), "④ 舊 doc（沒有這個鍵）照收＝加法、不破舊消費者")
    # 8) 降級形：零名冊 Production 訊號 ⇒ 零寬 coverage、三清單全空、仍過 validator
    g2 = build_from_inputs(_inp(_G2_INPUTS))
    ck(g2["coverage"]["from"] == g2["coverage"]["to"] == "2026-08-12T00:00:00Z",
       "降級形 coverage 零寬＝什麼都不主張（絕不編造）")
    ck(g2["days"] == [] and g2["closures"] == [] and g2["rest_day_audit"] == [],
       "降級形三清單全空")
    ck(ok_calendar(g2), "降級形仍是合法產出（誠實空 ≠ 壞產出）")
    ck(compute_calendar([], D(2026, 8, 10), D(2026, 8, 12), "2026-08-12T01:00:00Z")["days"] == [],
       "零列輸入同降級形")
    # 9) validator 直接拒收路徑（fixture invalid 之外的快速煙霧）
    bad = copy.deepcopy(g1); bad["schema_id"] = "board.prod_cal.v1"
    ck(not ok_calendar(bad), "schema_id 錯字拒收")
    bad = copy.deepcopy(g1); bad["coverage"]["from"] = "2026-08-14T00:00:00Z"
    ck(not ok_calendar(bad), "coverage from > to 拒收")
    bad = copy.deepcopy(g1); bad["generated_at"] = "2026-08-13 22:30:00"
    ck(not ok_calendar(bad), "裸 naive 牆鐘（無 Z）拒收")
    bad = copy.deepcopy(g1); bad["days"][0]["production"] = 1
    ck(not ok_calendar(bad), "production 非 bool 拒收（型別漂移不指望對面 coerce）")
    bad = copy.deepcopy(g1); bad["days"].append({"date": "2026-08-12", "production": True})
    ck(not ok_calendar(bad), "日期不升冪/重複拒收")
    bad = copy.deepcopy(g1); bad["closures"][0]["end"] = "2026-08-11T17:00:00Z"
    ck(not ok_calendar(bad), "closure 縮短到 3h（<240 分）拒收")
    bad = copy.deepcopy(g1); bad["rest_day_audit"][0]["segments"][0]["slots"] = 0
    ck(not ok_calendar(bad), "slots=0 拒收（觸及即 ≥1 格）")
    bad = copy.deepcopy(g1)
    bad["rest_day_audit"][0]["segments"][0]["extra"] = 1
    ck(not ok_calendar(bad), "盤點段多欄拒收（欄集精確）")
    ck(not ok_calendar({}), "空 doc 拒收")
    ck(not ok_calendar(None), "None 拒收不炸")
    # 10) fixture 檔（golden 重建逐位比對＋invalid 雙向）——與 selftest_fixtures 共用同一份
    #     公開樹：fixture 的 EID 是私有名冊經對照表換過的編號（合成名冊對不到）⇒ 載入時以同一張
    #     角色表 `_EMAP`（鍵在公開樹與 fixture 經同一張對照表換名、逐字一致）換成合成名冊的對應機台，
    #     golden 逐位比對與 invalid 雙向照跑（合成 fixture 變體；私有樹逐位元照讀、斷言數不變）。
    n_g, n_iv = check_fixture(eid_map=_EMAP if _wp.is_public_tree() else None)
    ck((n_g, n_iv) == (3, 9), f"fixture 應為 3 golden／9 invalid, got {(n_g, n_iv)}")
    print(f"selftest PASS ({n[0]} checks; fixture golden {n_g}／invalid {n_iv})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None,
                    help="live 窗寬（Chris 在域內機器跑；30＝唯一有 perf 實證的窗寬）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--make-fixture", action="store_true",
                    help="由 builder 重生 fixture（golden 不得手改）")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    if a.make_fixture:
        raise SystemExit(make_fixture())
    if a.days is None:
        ap.error("離線環境用 --selftest／--make-fixture；live 用 --days 30（Chris 跑）")
    raise SystemExit(run_live(a.days))


if __name__ == "__main__":
    main()
