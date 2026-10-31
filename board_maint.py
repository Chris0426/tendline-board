# -*- coding: utf-8 -*-
"""
board_maint.py — 維修體質頁（Maintenance Health）『呈現模型』（純函式 + demo 合成 + selftest）
=====================================================================================

定位（/equipment 的子頁；同受眾＝設備會議：工程師＋一線主管）：
  /equipment＝**日曆視角**（每台每日停機格盤）；本頁＝**統計視角**，只回答一個問題——
  「維修這件事做得順不順」。三節：
    S1 單卡在哪（30d 結案 REACTIVE 工單的生命週期＋階段組成＋最慢 5 張）
    S2 機台體質（90d MTBF × MTTR 象限散點＋壞蛋榜）
    S3 站別 Pareto（30d 各機群 S12 停機時數＋維修單數＋MTTR）

═══ 口徑與鐵則（規格拍板；違反＝verify 打回）═══════════════════════════════════
  1. 遷移單（status_history 空→migration fallback）：**排除**於階段分解與 MTTR 之外；MTBF 的
     故障**計數**照算（一張 REACTIVE 單＝一次故障、與有無 history 無關）；lead time 只在
     opened/completed 都有時計入、不分階段。
  2. §24 D1/D2：任何 per-person 維度（誰修的／個人排行）**永不出現**——本模組只消費
     w8.normalize_wo 輸出（per-person 已在 w8 邊界白名單剝除），輸出 dict 不得含人名欄。
  3. 凍結模組 mes_timeline / mes_yield **完全不 import**（本頁不連 MES、不碰 SQL）；per-EID 生產
     小時由 app 層以既有 accessor（app.load / app.load_fleet_by_cls）取好後傳入 prod_by_eid。
  4. MTBF 分子＝MES 生產小時（非日曆天）÷ REACTIVE 單數。「週末不開機」不該灌水 MTBF＝
     單位標籤「N 生產小時/次」直接烙進數字（顯示層防誤讀）。
  5. 沒開工單的故障看不到＝MTBF 偏樂觀（footer 誠實段講明）。
  7. 一鍵結單誠實標記（2026-07-12 選項 b，計算一位元不改、只加旗標/計數/顯示標記）：cmms 結單瞬間
     自動補 OPEN→IN_PROGRESS→COMPLETED 三轉移同一時戳→IN_PROGRESS 階段長度=0→repair_h=0→MTTR
     恆 0，讀起來像「秒修」＝誤導。此處以 oneclick 旗（repair_h≈0）標出「0 是量不到、非秒修」，
     並抑制該類 Δ 趨勢主張（量測壞掉≠趨勢）；MTTR/中位數計算本身完全不變。
  6. 統計量選擇（§1(f) 裁決）：
     · 生命週期大數字＝**中位**（med）+ p90（穩健、規格指定）。
     · 階段組成帶＝**平均**（mean）——可加：Σ(各階段 mean) ≡ mean(lead)，佔比嚴格 100%
       （中位不可加、且多數階段為 0→中位多為 0＝帶子誤導）。
     · p90＝nearest-rank：sorted(xs)[ceil(0.9n)-1]（deterministic）。

═══ 消費紀律（本檔只 import board_w8 / board_eqmeeting；絕不改姊妹）═══════════
  - board_w8：normalize_wo / wo_active_window / wo_state / migration_fallback_rate（純消費）。
  - board_eqmeeting：_phase_timeline（活躍窗階段時間線重建）/ downtime_by_eid（S12 countable
    interval-union、per-EID 先 union 再跨機相加）/ UPTIME_HOLD_REASONS（不計停機集，脫鉤鐵則：
    絕不自建兩值集、絕不引用 w8.HOLD_REASONS_KNOWN 當停機判準）/ EQMT_CATALOG（固定機台目錄）/
    demo 合成積木（_demo_phase_wo / _demo_migration_wo / _demo_open_wo / _seed / _DEMO_ANCHOR）。

用法：
  python board_maint.py --selftest   # 離線自驗（inline 契約形 fixture 過 normalize_wo；零 MES）
"""
import sys
import math
import argparse
import datetime as _dt
import random

import board_w8 as w8
import board_eqmeeting as wem

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------
MAINT_DAYS_DEFAULT = 30      # S1/S3 窗（env BOARD_MAINT_DAYS、app 層 clamp 7..60）
MTBF_DAYS_DEFAULT = 90       # S2 窗（env BOARD_MTBF_DAYS、app 層 clamp 30..90＝load() 上限）

# 頁面局部呈現 key（**非** §23 canonical；顯示層才翻譯）
STAGE_WAIT_START = "等動工"      # OPEN 階段（opened→首個 IN_PROGRESS）
STAGE_REPAIR = "修理"            # IN_PROGRESS 階段 Σ
STAGE_PARTS = "等料"             # ON_HOLD WAITING_PARTS
STAGE_VENDOR = "等外包"          # ON_HOLD WAITING_VENDOR
STAGE_OTHER = "其他暫停"         # ON_HOLD OTHER／未知（w8.HOLD_REASON_UNKNOWN「不知道」）
STAGE_UPTIME = "暫停·可生產"     # ON_HOLD ∈ wem.UPTIME_HOLD_REASONS（tooltip：不計停機）
STAGE_ORDER = (STAGE_WAIT_START, STAGE_REPAIR, STAGE_PARTS, STAGE_VENDOR, STAGE_OTHER, STAGE_UPTIME)

SMALL_SAMPLE_N = 5           # S3 小樣本門檻（<5 張結案單→不做分佈主張）
SCATTER_MIN_REACTIVE = 2     # S2 入圖門檻

# 散點圖幾何（viewBox 0 0 640 340；plot 區留軸標籤空間）
_PX0, _PX1, _PY0, _PY1 = 52.0, 624.0, 16.0, 296.0


# ---------------------------------------------------------------------------
# 統計基元（純）
# ---------------------------------------------------------------------------

def median(xs):
    """中位數。空→None；偶數取中間兩值均值。"""
    s = sorted(v for v in (xs or []) if v is not None)
    n = len(s)
    if n == 0:
        return None
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0


def p90(xs):
    """第 90 百分位（nearest-rank：sorted[ceil(0.9n)-1]）。空→None。"""
    s = sorted(v for v in (xs or []) if v is not None)
    n = len(s)
    if n == 0:
        return None
    return s[max(0, math.ceil(0.9 * n) - 1)]


def station_group(name):
    """目錄顯示名去尾碼 `\\s*#\\d+$` → 機群名（"Wirebond#7"→"Wirebond"）。
       非 str→None；無尾碼原樣（"AutoOQC"→"AutoOQC"）。這就是本頁『站別』定義（機群、涵蓋整份目錄；
       多數目錄機台不在 MES 站 fleet，若用 MES cls 分組會漏掉治具/顯微鏡類）。"""
    if not isinstance(name, str):
        return None
    import re
    return re.sub(r"\s*#\d+$", "", name).strip()


# ---------------------------------------------------------------------------
# 單張工單生命週期（消費 normalize_wo 輸出）
# ---------------------------------------------------------------------------

def _stage_of(status, hold_reason):
    """單一階段狀態 → 呈現階段 key（保 Σ=lead：所有落在活躍窗內的階段皆映到某 key）。"""
    if status == "OPEN":
        return STAGE_WAIT_START
    if status == "IN_PROGRESS":
        return STAGE_REPAIR
    if status == "ON_HOLD":
        if hold_reason in wem.UPTIME_HOLD_REASONS:     # 等機台空檔/試跑＝機台可生產、不計停機
            return STAGE_UPTIME
        if hold_reason == "WAITING_PARTS":
            return STAGE_PARTS
        if hold_reason == "WAITING_VENDOR":
            return STAGE_VENDOR
        return STAGE_OTHER                              # OTHER/未知「不知道」→其他暫停
    return STAGE_OTHER                                  # 其他狀態落窗內（罕見）→其他暫停（保 Σ=lead）


def wo_lifecycle(w, win_end):
    """單張 normalize_wo 輸出 → 生命週期 dict；None＝無有效窗（wo_active_window None）。
       回 {wno, eid, work_type, reactive, migration, ongoing, opened, completed, lead_h,
           stages, phase_segs, repair_h}。
       · 窗＝w8.wo_active_window(w, now=win_end)（**不裁頁窗**：lead/MTTR 是單的屬性、非窗的）。
       · migration（history 空）→ stages/phase_segs/repair_h = None（鐵則1；lead_h 照算）。
       · ongoing（end None）→ stages/phase_segs/repair_h/lead_h 全 None（不假設結單時間）。
       · 結案·非 migration → 走 wem._phase_timeline 逐階段對映（OPEN→等動工／IN_PROGRESS→修理／
         ON_HOLD→依 hold_reason；status None 防禦＝視同 migration→無階段分解）。
       · **取消／作廢單 → None**（2026-08-11 拍板①，判定＝`w8.wo_is_cancelled`）＝對齊 PM 板
         既有 `C-P7`「不進任何分母」：這張單不進 lifecycle 統計、不進 MTTR／lead time 中位、
         不進 MTBF 的故障次數、不進 station Pareto。取消掉的單不是一次故障、也不是一次修理，
         把它算進去會同時灌大分子與分母。⚠ 判定**絕不讀 `closed_at`**（理由見 `board_w8.wo_is_cancelled`；
         ⚠ 舊理由「CANCELLED 恆 NULL」2026-08-16 已失效，規則不變）。
       不變式：stages 非 None 時 Σstages ≡ lead_h（1e-6 內；_phase_timeline 鋪滿窗）。純函式。"""
    if not isinstance(w, dict):
        return None
    if w8.wo_is_cancelled(w):          # 取消／作廢＝不進任何分母（拍板①；對齊 C-P7）
        return None
    win = w8.wo_active_window(w, now=win_end)
    if win is None:
        return None
    a_start, a_end = win
    ongoing = a_end is None
    hist = w.get("status_history") or []
    migration = not hist
    wt = (w.get("work_type") or "").strip().upper() or None
    reactive = (wt == "REACTIVE")
    lead_h = None if a_end is None else (a_end - a_start).total_seconds() / 3600.0

    stages = phase_segs = repair_h = None
    if not ongoing and not migration:
        phs = wem._phase_timeline(w, a_start, a_end, False)
        if any(p.get("status") is None for p in phs):   # 防禦：無階段資料→視同 migration
            stages = phase_segs = repair_h = None
        else:
            stages = {k: 0.0 for k in STAGE_ORDER}
            phase_segs = []
            repair_h = 0.0
            for p in phs:
                dur_h = (p["end"] - p["start"]).total_seconds() / 3600.0
                if dur_h <= 0:
                    continue
                key = _stage_of(p["status"], p.get("hold_reason"))
                stages[key] += dur_h
                phase_segs.append((key, p["start"], p["end"]))
                if p["status"] == "IN_PROGRESS":
                    repair_h += dur_h

    # 一鍵結單旗（選項 b）：結案·非遷移·非防禦分支（repair_h 非 None）→ repair_h≈0 即一鍵結單
    #   （IN_PROGRESS 階段零長＝結單時三轉移同時戳）；migration/ongoing/防禦（repair_h None）→ None（不可知）。
    oneclick = None if repair_h is None else (repair_h <= 1e-9)

    return {"wno": w.get("work_order_no"), "eid": w.get("asset_id"), "work_type": wt,
            "reactive": reactive, "migration": migration, "ongoing": ongoing,
            "opened": a_start, "completed": a_end, "lead_h": lead_h,
            "stages": stages, "phase_segs": phase_segs, "repair_h": repair_h,
            "oneclick": oneclick}


def _iter_lifecycles(wos_by_eid, win_end):
    """{eid:[normalize_wo…]} → yield lifecycle dict（skip None/非 dict）。"""
    for _eid, wos in (wos_by_eid or {}).items():
        for w in wos or []:
            lc = wo_lifecycle(w, win_end)
            if lc is not None:
                yield lc


# ---------------------------------------------------------------------------
# S1 — 生命週期統計（對象＝completed ∈ 窗 的 REACTIVE 結案單）
# ---------------------------------------------------------------------------

def lifecycle_stats(wos_by_eid, win_start, win_end):
    """S1 模型。回 {n_closed, n_ongoing, n_migration, med_lead_d, p90_lead_d,
       stage_mean_h:{key:h}, stage_total_share:[(key, share, mean_h)], slowest:[≤5 lifecycle]}。"""
    closed, n_ongoing, n_migration = [], 0, 0
    for lc in _iter_lifecycles(wos_by_eid, win_end):
        if not lc["reactive"]:
            continue
        if lc["ongoing"]:
            if lc["opened"] is not None and lc["opened"] <= win_end:   # 活躍窗交窗
                n_ongoing += 1
            continue
        comp = lc["completed"]
        if comp is None or not (win_start <= comp <= win_end):
            continue                                    # 只計 completed ∈ 窗（窗外結案不入）
        closed.append(lc)
        if lc["migration"]:
            n_migration += 1

    leads = [lc["lead_h"] for lc in closed if lc["lead_h"] is not None]
    med = median(leads)
    p9 = p90(leads)
    med_lead_d = (med / 24.0) if med is not None else None
    p90_lead_d = (p9 / 24.0) if p9 is not None else None

    # 階段 mean 只含非 migration 結案單（鐵則1）
    non_mig = [lc for lc in closed if lc["stages"] is not None]
    stage_mean = {k: 0.0 for k in STAGE_ORDER}
    if non_mig:
        for k in STAGE_ORDER:
            stage_mean[k] = sum(lc["stages"].get(k, 0.0) for lc in non_mig) / len(non_mig)
    total = sum(stage_mean.values())
    stage_share = ([(k, stage_mean[k] / total, stage_mean[k])
                    for k in STAGE_ORDER if stage_mean[k] > 0] if total > 0 else [])

    slowest = sorted((lc for lc in closed
                      if lc["stages"] is not None and lc["lead_h"] is not None),
                     key=lambda lc: -lc["lead_h"])[:5]
    # 一鍵結單標記（選項 b）：n_decomposed＝可分解階段結案單、n_oneclick＝其中一鍵結單張數。
    n_oneclick = sum(1 for lc in non_mig if lc["oneclick"])
    return {"n_closed": len(closed), "n_ongoing": n_ongoing, "n_migration": n_migration,
            "med_lead_d": med_lead_d, "p90_lead_d": p90_lead_d,
            "stage_mean_h": stage_mean, "stage_total_share": stage_share, "slowest": slowest,
            "n_decomposed": len(non_mig), "n_oneclick": n_oneclick}


# ---------------------------------------------------------------------------
# S2 — 機台體質（MTBF × MTTR）
# ---------------------------------------------------------------------------

def _catalog_maps(catalog):
    """catalog → (order[eid…], name_by_eid, area_by_eid)（保序、去重）。"""
    order, name_by, area_by = [], {}, {}
    for area, name, eid in catalog:
        if eid not in name_by:
            name_by[eid] = name
            area_by[eid] = area
            order.append(eid)
    return order, name_by, area_by


def machine_health(wos90_by_eid, prod_by_eid, win_start, win_end, catalog=None):
    """S2 模型。roster＝catalog（整份目錄）＋有 WO 的非目錄 EID。per-EID：
       n_reactive＝REACTIVE 且活躍窗 start ∈ [win_start,win_end]（**migration 也計**＝鐵則1）；
       mttr_h＝結案·非 migration REACTIVE 的 repair_h 中位（無合格單→None）；
       prod_h/zlo＝prod_by_eid.get(eid)（{"h","zlo"} 或 None）；
       mtbf_h＝prod_h/n_reactive（prod_h>0 且 n_reactive>0；zlo→None+旗）；
       burden_h＝n_reactive*mttr_h（mttr None→None）。"""
    catalog = wem.EQMT_CATALOG if catalog is None else catalog
    prod_by_eid = prod_by_eid or {}
    order, name_by, area_by = _catalog_maps(catalog)
    for eid in (wos90_by_eid or {}):                    # 非目錄 EID（有 WO）也成列（不丟）
        if eid not in name_by:
            name_by[eid] = eid
            area_by[eid] = None
            order.append(eid)

    rows = []
    for eid in order:
        name = name_by[eid]
        wos = (wos90_by_eid or {}).get(eid) or []
        n_reactive, repairs, n_oneclick = 0, [], 0
        for w in wos:
            lc = wo_lifecycle(w, win_end)
            if lc is None or not lc["reactive"]:
                continue
            if lc["opened"] is not None and win_start <= lc["opened"] <= win_end:
                n_reactive += 1                          # 故障計數（migration 也算）
            if (not lc["ongoing"] and not lc["migration"] and lc["repair_h"] is not None
                    and lc["completed"] is not None and win_start <= lc["completed"] <= win_end):
                repairs.append(lc["repair_h"])           # MTTR 只採結案·非 migration
                if lc["oneclick"]:
                    n_oneclick += 1                      # 一鍵結單張數（進 MTTR 樣本的子集）
        mttr_h = median(repairs) if repairs else None
        # 顯示旗（選項 b）：mttr_h≈0 且有一鍵結單→這個 0 是「量不到」不是「秒修」（計算不變）。
        mttr_oneclick = (mttr_h is not None and mttr_h <= 1e-9 and n_oneclick > 0)

        pinfo = prod_by_eid.get(eid)
        prod_h = pinfo.get("h") if isinstance(pinfo, dict) else None
        zlo = bool(pinfo.get("zlo")) if isinstance(pinfo, dict) else False

        mtbf_h = None
        insufficient_reason = None
        if zlo:
            insufficient_reason = "zero_len"             # 段長法不可測（≠無訊號）
        elif prod_h is None:
            insufficient_reason = "no_prod"
        elif n_reactive > 0 and prod_h > 0:
            mtbf_h = prod_h / n_reactive
        burden_h = (n_reactive * mttr_h) if mttr_h is not None else None

        rows.append({"eid": eid, "name": name, "area": area_by.get(eid),
                     "grp": station_group(name), "n_reactive": n_reactive,
                     "mttr_h": mttr_h, "mtbf_h": mtbf_h, "burden_h": burden_h,
                     "prod_h": prod_h, "zlo": zlo, "insufficient_reason": insufficient_reason,
                     "n_mttr": len(repairs), "n_oneclick": n_oneclick,
                     "mttr_oneclick": mttr_oneclick})

    plot = [r for r in rows if r["n_reactive"] >= SCATTER_MIN_REACTIVE
            and r["mtbf_h"] is not None and r["mttr_h"] is not None]
    plot_eids = {r["eid"] for r in plot}
    n_insufficient = sum(1 for r in rows if r["n_reactive"] >= 1 and r["eid"] not in plot_eids)
    n_no_prod = sum(1 for r in rows if r["n_reactive"] >= 1 and (r["prod_h"] is None or r["zlo"]))
    top5 = sorted((r for r in rows if r["burden_h"] is not None),
                  key=lambda r: -r["burden_h"])[:5]
    return {"rows": rows, "plot": plot, "n_insufficient": n_insufficient,
            "n_no_prod": n_no_prod, "top5": top5,
            "n_oneclick_total": sum(r["n_oneclick"] for r in rows),
            "n_mttr_total": sum(r["n_mttr"] for r in rows)}


def scatter_model(plot_rows, focus_eid=None, top_eids=()):
    """散點座標（純數學；模板只鋪 <svg>）。x=MTBF、y=MTTR、線性軸。
       → None（空）或 {points, x_max, y_max, ticks_x, ticks_y, med_x_px, med_y_px, quad}。
       壞象限（常壞又難修＝MTBF 低 & MTTR 高＝左上）：入圖點的 x/y 中位十字線、<3 點不畫。"""
    rows = [r for r in (plot_rows or [])
            if r.get("mtbf_h") is not None and r.get("mttr_h") is not None]
    if not rows:
        return None
    top_set = set(top_eids or ())
    xs = [r["mtbf_h"] for r in rows]
    ys = [r["mttr_h"] for r in rows]
    x_max = max(max(xs) * 1.08, 1.0)
    y_max = max(max(ys) * 1.15, 0.1)

    def _cx(x):
        return _PX0 + (x / x_max) * (_PX1 - _PX0)

    def _cy(y):
        return _PY1 - (y / y_max) * (_PY1 - _PY0)

    def _fmt(v):
        return ("%.0f" % v) if v >= 100 else ("%.1f" % v)

    points = [{"eid": r["eid"], "name": r["name"], "x": r["mtbf_h"], "y": r["mttr_h"],
               "cx": round(_cx(r["mtbf_h"]), 2), "cy": round(_cy(r["mttr_h"]), 2),
               "focus": (focus_eid is not None and r["eid"] == focus_eid),
               "hot": r["eid"] in top_set,
               "oc": bool(r.get("mttr_oneclick"))} for r in rows]
    ticks_x = [(round(_cx(x_max * f), 2), _fmt(x_max * f)) for f in (0, .25, .5, .75, 1)]
    ticks_y = [(round(_cy(y_max * f), 2), _fmt(y_max * f)) for f in (0, .25, .5, .75, 1)]

    med_x_px = med_y_px = quad = None
    if len(rows) >= 3:
        mx, my = median(xs), median(ys)
        med_x_px, med_y_px = round(_cx(mx), 2), round(_cy(my), 2)
        quad = {"x0": round(_PX0, 2), "y0": round(_PY0, 2),
                "w": round(med_x_px - _PX0, 2), "h": round(med_y_px - _PY0, 2)}
    return {"points": points, "x_max": x_max, "y_max": y_max,
            "ticks_x": ticks_x, "ticks_y": ticks_y,
            "med_x_px": med_x_px, "med_y_px": med_y_px, "quad": quad}


# ---------------------------------------------------------------------------
# S2b — Δ 自我趨勢（本期 90d vs 前期 90d；「跟自己比」，v1.1）
# ---------------------------------------------------------------------------
# 三層好壞基準政策（GUIDE §3「2026-07-12 裁決」）：①相對同儕＝散點中位十字線（v1）；
# ②跟自己比＝本節 Δ；③絕對門檻＝永不捏造（SME/OEM 校準輸入，§26.5）。
#   前期窗＝[now-180d, now-90d]（與本期同長、同錨 now）。前期生產小時＝producing(180d)−
#   producing(90d)（app 層差集算好後傳入 prod_prev_by_eid；demo 自帶）。
#   顯示門檻：前期 REACTIVE <2 張 或 前期值不可得 → Δ「—」（不做趨勢主張）。

def _prev_machine_stats(wos_prev_by_eid, prev_prod_by_eid, win_start, win_end, ref_now):
    """前期窗 per-EID {n_reactive, mttr_h, prod_h, mtbf_h}（口徑與 machine_health 完全一致，
       只是窗＝前期）。ref_now＝真實現在（供 wo_lifecycle 解析活躍窗、與本期同基準）；
       n_reactive／mttr 的窗歸屬用 (win_start, win_end)＝前期窗界。遷移單：計入 n_reactive、
       排除於 mttr（鐵則1，與本期一致）。"""
    prev_prod_by_eid = prev_prod_by_eid or {}
    out = {}
    for eid, wos in (wos_prev_by_eid or {}).items():
        n_reactive, repairs, n_oneclick = 0, [], 0
        for w in wos or []:
            lc = wo_lifecycle(w, ref_now)
            if lc is None or not lc["reactive"]:
                continue
            if lc["opened"] is not None and win_start <= lc["opened"] <= win_end:
                n_reactive += 1                              # 故障計數（migration 也算）
            if (not lc["ongoing"] and not lc["migration"] and lc["repair_h"] is not None
                    and lc["completed"] is not None and win_start <= lc["completed"] <= win_end):
                repairs.append(lc["repair_h"])               # MTTR 只採結案·非 migration
                if lc["oneclick"]:
                    n_oneclick += 1                          # 一鍵結單張數（前期）
        mttr_h = median(repairs) if repairs else None
        pinfo = prev_prod_by_eid.get(eid)
        prod_h = pinfo.get("h") if isinstance(pinfo, dict) else None
        mtbf_h = (prod_h / n_reactive) if (prod_h is not None and prod_h > 0 and n_reactive > 0) else None
        out[eid] = {"n_reactive": n_reactive, "mttr_h": mttr_h, "prod_h": prod_h,
                    "mtbf_h": mtbf_h, "n_oneclick": n_oneclick}
    return out


def _delta(cur, prev, better_when_higher):
    """cur/prev（float）→ Δ dict 或 None（任一 None＝不可比）。
       better_when_higher＝True（MTBF：越大越好）／False（MTTR：越小越好）。
       回 {"dir":"up"|"down"|"flat", "good":bool|None, "prev":prev}；good＝語意色旗
       （True→改善綠、False→惡化紅、None→持平）。"""
    if cur is None or prev is None:
        return None
    if cur > prev:
        d = "up"
    elif cur < prev:
        d = "down"
    else:
        return {"dir": "flat", "good": None, "prev": prev}
    good = (d == "up") if better_when_higher else (d == "down")
    return {"dir": d, "good": good, "prev": prev}


def _attach_deltas(rows, prev_by_eid):
    """把 mtbf_delta / mttr_delta 掛到每列（鍵恆存在＝模板可安全讀）。
       前期 <2 張 REACTIVE 或前期資料不可得 → 兩 Δ 皆 None（不做趨勢主張＝顯「—」）。"""
    prev_by_eid = prev_by_eid or {}
    for r in rows:
        pv = prev_by_eid.get(r["eid"])
        if not pv or pv.get("n_reactive", 0) < SCATTER_MIN_REACTIVE:
            r["mtbf_delta"] = None
            r["mttr_delta"] = None
            continue
        r["mtbf_delta"] = _delta(r.get("mtbf_h"), pv.get("mtbf_h"), better_when_higher=True)
        r["mttr_delta"] = _delta(r.get("mttr_h"), pv.get("mttr_h"), better_when_higher=False)
        # Δ 抑制（選項 b，誠實：量測壞掉≠趨勢）：本期或前期 MTTR 是一鍵結單的 0→MTTR Δ「—」；
        #   MTBF Δ 完全不受影響（MTBF 分子＝生產小時、與一鍵結單無關）。
        if r.get("mttr_oneclick") or (pv.get("mttr_h") is not None
                                      and pv.get("mttr_h") <= 1e-9 and pv.get("n_oneclick", 0) > 0):
            r["mttr_delta"] = None


# ---------------------------------------------------------------------------
# S3 — 站別 Pareto（機群為單位）
# ---------------------------------------------------------------------------

def station_pareto(wos30_by_eid, win_start, win_end, catalog=None, dormant_events=None):
    """S3 模型：eid→grp（station_group(目錄名)；非目錄 EID→grp=eid 原樣）。
       per-grp：stop_h＝Σ wem.downtime_by_eid_scoped[eid].down_sec/3600（重用 S12 countable
       interval-union、per-EID 先 union 再跨機相加，不重寫）；n_reactive；n_closed；mttr_h（中位）；
       small_sample＝n_closed<5。回 stop_h 降冪 list；全 0 grp 不列。
       ★ A10 擴散 R2（2026-08-16；SPEC-DORMANT §5.3）：停機時數改走 **scoped 版**——
         母機 EID（母單開在 EID-24742 等三台、非目錄 ⇒ grp=eid 原樣列）走機器視角
         （範圍縮到某幾個工位＝機器不算整台停）；登記長停的工位不計任何 downtime。
         `dormant_events=None`＝過渡橋常數；母單的 `scope_events` 由 app 層帶好，
         沒帶＝整機＝高估側降級（同 `/equipment`）。**MTTR／n_reactive／n_closed 不變**
         （lifecycle 答「這張單修多久」、不是「機器停多久」——兩軸分開）。"""
    catalog = wem.EQMT_CATALOG if catalog is None else catalog
    name_by = {eid: name for _a, name, eid in catalog}
    dbe = wem.downtime_by_eid_scoped(wos30_by_eid or {}, win_start, win_end,
                                     dormant_events=dormant_events)   # per-EID union（S12+R2）

    groups = {}
    for eid, wos in (wos30_by_eid or {}).items():
        grp = station_group(name_by.get(eid, eid)) or eid   # 非目錄/無群→eid 原樣
        g = groups.setdefault(grp, {"grp": grp, "stop_h": 0.0, "n_reactive": 0,
                                    "n_closed": 0, "n_oneclick": 0, "_repairs": []})
        g["stop_h"] += (dbe.get(eid, {}).get("down_sec") or 0.0) / 3600.0
        for w in wos or []:
            lc = wo_lifecycle(w, win_end)
            if lc is None or not lc["reactive"]:
                continue
            if lc["opened"] is not None and win_start <= lc["opened"] <= win_end:
                g["n_reactive"] += 1
            if (not lc["ongoing"] and not lc["migration"] and lc["repair_h"] is not None
                    and lc["completed"] is not None and win_start <= lc["completed"] <= win_end):
                g["n_closed"] += 1
                g["_repairs"].append(lc["repair_h"])
                if lc["oneclick"]:
                    g["n_oneclick"] += 1                 # 一鍵結單張數（該 grp）

    out = []
    for g in groups.values():
        if g["stop_h"] <= 0 and g["n_reactive"] == 0 and g["n_closed"] == 0:
            continue                                    # 全 0 grp 不列
        g["mttr_h"] = median(g["_repairs"]) if g["_repairs"] else None
        g["small_sample"] = g["n_closed"] < SMALL_SAMPLE_N
        # 顯示旗（選項 b）：grp MTTR≈0 且有一鍵結單→標「量不到」。n_mttr＝n_closed（不另存）。
        g["mttr_oneclick"] = (g["mttr_h"] is not None and g["mttr_h"] <= 1e-9
                              and g["n_oneclick"] > 0)
        g.pop("_repairs")
        out.append(g)
    out.sort(key=lambda g: -g["stop_h"])
    # 真 Pareto（S3 升級，v1.1）：佔比%（該站停機h÷總停機h）+ 累計%（80/20 集中度）。
    #   排序不變（已按 stop_h 降冪）；佔比/累計只作呈現用（列內橫條 width=share）。
    total_stop = sum(g["stop_h"] for g in out)
    cum = 0.0
    for g in out:
        g["share"] = (g["stop_h"] / total_stop) if total_stop > 0 else 0.0
        cum += g["share"]
        g["cum"] = cum
    return out


# ---------------------------------------------------------------------------
# 頁面總模型
# ---------------------------------------------------------------------------

def _days_between(win):
    try:
        return int(round((win[1] - win[0]).total_seconds() / 86400.0))
    except (TypeError, ValueError, AttributeError):
        return None


def build_maint(wos30_by_eid, wos90_by_eid, prod_by_eid, win30, win90,
                wos_prev_by_eid=None, prod_prev_by_eid=None, win_prev=None, mode=None,
                wo_status="not_configured", truncated30=False, truncated90=False,
                prod_meta=None, wo_status30=None, wo_status90=None,
                truncated_prev=False, wo_status_prev=None, dormant_events=None):
    """頁面總模型（純組裝、零 I/O）。wo_status != 'live' 且 mode != 'demo' → 三節仍建（空 wos＝
       空統計），模板據 wo_status 顯降級卡。scatter 以 focus_eid=None baked（focus 高亮環由模板
       比對 pt.eid==focus 繪製＝focus 不入快取鍵）。
       ★ 兩窗獨立狀態（verify MED：30d/90d 各一次 LIST，一窗失敗另一窗成功時＝不對稱失敗）：
         wo_status30／wo_status90＝各窗自己的狀態（未給→回退 wo_status＝兩窗同源時的舊行為）。
         模板據此「分節降級」——某窗失敗只降級吃該窗的節（S1/S3 吃 30d、S2 吃 90d），
         不讓成功窗的真統計被假裝成「零」，也不讓失敗窗渲染空統計假裝正常。
       ★ Δ 自我趨勢（v1.1，可選）：wos_prev_by_eid＝前期窗（180d LIST 超集，A3 active-in）、
         prod_prev_by_eid＝前期段生產小時（producing(180d)−producing(90d)、app 差集或 demo 自帶）、
         win_prev＝(前期起, 前期迄)＝[now-180d, now-90d]。三者齊備才算 Δ 掛到 s2 rows；缺任一
         （snapshot/未接通/budget 用盡）→ 每列 Δ 皆 None（顯「—」＝誠實不主張）。ref_now＝w90e
         （＝現在；wo_lifecycle 活躍窗解析與本期同基準）。"""
    wo_status30 = wo_status if wo_status30 is None else wo_status30
    wo_status90 = wo_status if wo_status90 is None else wo_status90
    w30s, w30e = win30
    w90s, w90e = win90
    s1 = lifecycle_stats(wos30_by_eid, w30s, w30e)
    s2 = machine_health(wos90_by_eid, prod_by_eid, w90s, w90e)
    # Δ：前期窗 per-EID 統計 → 掛 mtbf_delta/mttr_delta 到每列（top5 為 rows 子集、同 dict＝一併帶）。
    prev_stats = None
    if wos_prev_by_eid is not None and win_prev is not None:
        prev_stats = _prev_machine_stats(wos_prev_by_eid, prod_prev_by_eid or {},
                                         win_prev[0], win_prev[1], w90e)
    _attach_deltas(s2["rows"], prev_stats or {})           # prev_stats None→每列 Δ None（鍵恆存在）
    s2["scatter"] = scatter_model(s2["plot"], focus_eid=None,
                                  top_eids=tuple(r["eid"] for r in s2["top5"]))
    s3 = station_pareto(wos30_by_eid, w30s, w30e, dormant_events=dormant_events)
    all90 = [w for wos in (wos90_by_eid or {}).values() for w in (wos or []) if w is not None]
    fb, tot = w8.migration_fallback_rate(all90)
    has_oneclick = bool(s1.get("n_oneclick") or s2.get("n_oneclick_total")
                        or any(g.get("n_oneclick") for g in s3))
    return {"mode": mode, "wo_status": wo_status, "has_oneclick": has_oneclick,
            "wo_status30": wo_status30, "wo_status90": wo_status90, "wo_status_prev": wo_status_prev,
            "s1": s1, "s2": s2, "s3": s3,
            "win30": win30, "win90": win90, "win_prev": win_prev,
            "days30": _days_between(win30), "days90": _days_between(win90),
            "days_prev": _days_between(win_prev) if win_prev else None,
            "has_delta": prev_stats is not None,
            "truncated30": bool(truncated30), "truncated90": bool(truncated90),
            "truncated_prev": bool(truncated_prev),
            "prod_meta": prod_meta, "fallback_rate": (fb, tot)}


# ---------------------------------------------------------------------------
# demo 合成（走真管線 normalize_wo；deterministic；錨＝wem._DEMO_ANCHOR）
# ---------------------------------------------------------------------------

# demo 生產小時「無訊號」台（驗『資料不足』列；catalog 索引）＝刻意回 None。
_DEMO_NO_PROD_IDX = (2, 5, 9)


def demo_prod_hours(eid, days):
    """demo 生產小時（純 seed、無 MES）。"""
    return days * 24.0 * (0.25 + (wem._seed(eid) % 55) / 100.0)


def _demo_reactive_steps(opened, closed, rnd):
    """已結案 REACTIVE 分階段 steps（OPEN→IN_PROGRESS→[ON_HOLD(hr)→IN_PROGRESS]→COMPLETED）；
       hr 依 rnd 輪轉六類（含 uptime 兩類）＝階段組成帶全數有值。"""
    span = (closed - opened).total_seconds()
    steps = [("OPEN", None, opened),
             ("IN_PROGRESS", None, opened + _dt.timedelta(seconds=span * 0.15))]
    hrs = ("WAITING_PARTS", "WAITING_VENDOR", "OTHER", "WAITING_MACHINE_TIME", "TEST_RUN")
    if rnd.random() < 0.6:
        hr = hrs[rnd.randrange(len(hrs))]
        steps.append(("ON_HOLD", hr, opened + _dt.timedelta(seconds=span * 0.45)))
        steps.append(("IN_PROGRESS", None, opened + _dt.timedelta(seconds=span * 0.72)))
    steps.append(("COMPLETED", None, closed))
    return steps


def demo_maint(days30=MAINT_DAYS_DEFAULT, days90=MTBF_DAYS_DEFAULT):
    """全合成 → (wos30, wos90, prod_by_eid, win30, win90, wos_prev, prod_prev, win_prev)；
       deterministic（同參數兩呼結構相等）。走真管線 normalize_wo（與 live 完全同路徑）。
       ★ wos30 = wos90（同一 dict）——30d/90d 差異由 stat 函式的窗過濾承擔（completed∈窗／
         活躍窗 start∈窗）＝兩窗數字自洽、不另合成。末三元素＝Δ 自我趨勢前期窗合成
         （build_maint 以 *demo_maint() 位置展開即接上）。"""
    win_end = wem._DEMO_ANCHOR
    w90s = win_end - _dt.timedelta(days=days90)
    w30s = win_end - _dt.timedelta(days=days30)
    span90_h = days90 * 24.0
    wos90 = {}

    for idx, (_area, _name, eid) in enumerate(wem.EQMT_CATALOG):
        rnd = random.Random(wem._seed(eid) * 173 + 11)
        base = 50000 + wem._seed(eid) * 5
        # ── 一鍵結單 showcase（選項 b；idx 13＝Vacuum Oven for film dry / EID-32176；不在
        #    _DEMO_NO_PROD_IDX(2,5,9)、非 EQM_SHOWCASE_EID(idx0)、idx≥12＝非散點保底台）──
        #    整台改成只有 3 張一鍵結單 REACTIVE：三轉移全蓋 closed 時戳→repair_h=0→MTTR 0（有標記）、
        #    入散點 y=0、completed 落 30d 窗內（S1 吃到 3 張）；不掛 ongoing 單。每台獨立 seed→
        #    只影響本台，determinism 斷言不破。
        if idx == 13:
            oc_raw = []
            for j, dago in enumerate((20, 12, 6)):
                op = win_end - _dt.timedelta(days=dago)
                cl = op + _dt.timedelta(hours=8)
                steps = [("OPEN", None, cl), ("IN_PROGRESS", None, cl), ("COMPLETED", None, cl)]
                oc_raw.append(wem._demo_phase_wo(base + 70 + j, eid, op, cl, "REACTIVE", steps))
            wos90[eid] = [w8.normalize_wo(r) for r in oc_raw]
            continue
        raw = []
        # 前 12 台保底 ≥2 張已結案 REACTIVE（散點 plot 不空＝deterministic 保證），其餘 0–6 張。
        n = max(2, rnd.randint(0, 6)) if idx < 12 else rnd.randint(0, 6)
        for i in range(n):
            forced_reactive = (idx < 12 and i < 2)      # 保底兩張為 REACTIVE·已結案·非遷移
            wt = "REACTIVE" if (forced_reactive or rnd.random() < 0.70) else "PM"
            op = w90s + _dt.timedelta(hours=rnd.uniform(0, span90_h - 60))
            if (not forced_reactive) and rnd.random() < 0.15:    # ~15% 遷移單（空 history）
                cl = op + _dt.timedelta(hours=rnd.uniform(2, 40))
                raw.append(wem._demo_migration_wo(base + i, eid, op, cl, wt))
            else:                                        # 分階段已結案單
                cl = op + _dt.timedelta(hours=rnd.uniform(2, 30))
                raw.append(wem._demo_phase_wo(base + i, eid, op, cl, wt,
                                              _demo_reactive_steps(op, cl, rnd)))
        if wem._seed(eid) % 11 in (0, 1):               # 部分機台掛 ongoing 單（驗「進行中 N 張」）
            op = win_end - _dt.timedelta(hours=rnd.uniform(4, 40))
            raw.append(wem._demo_open_wo(base + 90, eid, op, win_end, "reactive_ip"))
        wos90[eid] = [w8.normalize_wo(r) for r in raw]

    # showcase：EQM_SHOWCASE_EID（EID-12724）注入一張 21 天超長單（等料 15 天）＝最慢榜首、
    #   階段帶「等料」明顯佔比；另兩張確保入散點。皆落 30d 窗內（completed 近錨）。
    sc = wos90.get(wem.EQM_SHOWCASE_EID)
    if sc is not None:
        op = win_end - _dt.timedelta(days=22)
        cl = win_end - _dt.timedelta(days=1)            # completed 落 30d 窗內＝S1 最慢榜首
        long_steps = [("OPEN", None, op),
                      ("IN_PROGRESS", None, op + _dt.timedelta(hours=6)),
                      ("ON_HOLD", "WAITING_PARTS", op + _dt.timedelta(days=1)),
                      ("IN_PROGRESS", None, op + _dt.timedelta(days=16)),
                      ("COMPLETED", None, cl)]           # 等料 ~15 天
        sc.append(w8.normalize_wo(
            wem._demo_phase_wo(49700, wem.EQM_SHOWCASE_EID, op, cl, "REACTIVE", long_steps)))
        for k, dago in ((49701, 8), (49702, 5)):         # 兩張確保 ≥3 REACTIVE 入散點
            o2 = win_end - _dt.timedelta(days=dago)
            c2 = o2 + _dt.timedelta(hours=6)
            sc.append(w8.normalize_wo(
                wem._demo_phase_wo(k, wem.EQM_SHOWCASE_EID, o2, c2, "REACTIVE",
                                   _demo_reactive_steps(o2, c2, random.Random(k)))))

    prod_by_eid = {}
    for idx, (_area, _name, eid) in enumerate(wem.EQMT_CATALOG):
        if idx in _DEMO_NO_PROD_IDX:
            prod_by_eid[eid] = None                     # 無 MES 生產訊號→驗「資料不足」列
        else:
            prod_by_eid[eid] = {"h": demo_prod_hours(eid, days90), "zlo": False, "cls": None}

    # ── Δ 自我趨勢 demo（前期窗 [anchor-2·90d, anchor-90d]；三態都現形供目視驗收）──
    #   scenario = idx%3：0 改善（前期 MTBF 極低/MTTR 極高→本期一定較好）／1 惡化（前期 MTBF
    #   極高/MTTR 極低→本期一定較差）／2 資料不足（前期 REACTIVE 1 張<2→Δ「—」）。前期生產小時用
    #   極端值強制方向（不受本期隨機值影響＝三態穩定可驗）。wos_prev＝90d 超集＋前期單（測窗歸屬）。
    prev_end = win_end - _dt.timedelta(days=days90)             # = w90s
    prev_start = win_end - _dt.timedelta(days=2 * days90)       # 前期起（同長、同錨）
    wos_prev, prod_prev = {}, {}
    for idx, (_area, _name, eid) in enumerate(wem.EQMT_CATALOG):
        scenario = idx % 3
        plist = list(wos90.get(eid, []))                        # 90d 超集（前期窗過濾會排除本期單）
        n_prev = 1 if scenario == 2 else 2
        repair_h = 20.0 if scenario == 0 else 0.5               # 改善＝前期 MTTR 高／惡化＝前期 MTTR 低
        for i in range(n_prev):
            op = prev_start + _dt.timedelta(days=10 + i * 6)    # 落前期窗內（repair≤20h→cl 亦在窗內）
            cl = op + _dt.timedelta(hours=0.2 + repair_h)
            steps = [("OPEN", None, op),
                     ("IN_PROGRESS", None, op + _dt.timedelta(hours=0.2)),
                     ("COMPLETED", None, cl)]
            plist.append(w8.normalize_wo(
                wem._demo_phase_wo(48000 + idx * 4 + i, eid, op, cl, "REACTIVE", steps)))
        wos_prev[eid] = plist
        # 前期段生產小時（極端值定向）：改善→極低（前期 MTBF 低）／惡化→極高（前期 MTBF 高）。
        prod_prev[eid] = {"h": 1.0 if scenario == 0 else (200000.0 if scenario == 1 else 100.0)}

    return (wos90, wos90, prod_by_eid, (w30s, win_end), (w90s, win_end),
            wos_prev, prod_prev, (prev_start, prev_end))


# ---------------------------------------------------------------------------
# selftest（inline 契約形 fixture 過 normalize_wo；固定 NOW 錨；零 I/O、零 MES）
# ---------------------------------------------------------------------------

def _selftest():
    NOW = _dt.datetime(2026, 7, 1, 12, 0, 0)
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    def _h(to, hr, at):
        return {"from_status": None, "to_status": to, "hold_reason": hr,
                "changed_at": at, "source_actor": "human:demo"}

    def _iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # ---- statistics 基元（6）----
    ck(median([3, 1, 2]) == 2, "median 奇數")
    ck(median([1, 2, 3, 4]) == 2.5, "median 偶數取均")
    ck(median([]) is None, "median 空→None")
    ck(p90(list(range(1, 11))) == 9, "p90 nearest-rank(n=10 取第9)")   # ceil(9)=9→idx8→值9
    ck(p90([42]) == 42, "p90 單元素")
    ck(station_group("Wirebond#7") == "Wirebond"
       and station_group("CAL Gen2 Bay#1") == "CAL Gen2 Bay"
       and station_group("AutoOQC") == "AutoOQC" and station_group(None) is None,
       "station_group 去尾碼/原樣/None")

    # ---- wo_lifecycle（10）----
    D = _dt.datetime
    op = D(2026, 6, 11, 9, 0)
    raw_rx = {"work_order_no": 2001, "asset_id": "EID-A", "work_type": "REACTIVE",
              "opened_at": _iso(op), "closed_at": _iso(D(2026, 6, 11, 15, 0)),
              "status_history": [
                  _h("OPEN", None, _iso(op)),
                  _h("IN_PROGRESS", None, _iso(D(2026, 6, 11, 9, 10))),
                  _h("ON_HOLD", "WAITING_PARTS", _iso(D(2026, 6, 11, 11, 0))),
                  _h("IN_PROGRESS", None, _iso(D(2026, 6, 11, 13, 0))),
                  _h("COMPLETED", None, _iso(D(2026, 6, 11, 15, 0)))]}
    lc = wo_lifecycle(w8.normalize_wo(raw_rx), NOW)
    ck(abs(lc["lead_h"] - 6.0) < 1e-9, f"正常結案 lead_h=6, got {lc['lead_h']}")
    ck(set(lc["stages"].keys()) == set(STAGE_ORDER), "stages 六鍵齊")
    ck(abs(lc["stages"][STAGE_PARTS] - 2.0) < 1e-9, f"等料 2.0h, got {lc['stages'][STAGE_PARTS]}")
    ck(abs(sum(lc["stages"].values()) - lc["lead_h"]) < 1e-6, "Σstages ≡ lead_h")
    ck(abs(lc["repair_h"] - (110 + 120) / 60.0) < 1e-9, f"repair_h 兩段 IN_PROGRESS 和, got {lc['repair_h']}")

    def _mk_hold(hr):
        r = {"work_order_no": 2010, "asset_id": "EID-A", "work_type": "REACTIVE",
             "opened_at": _iso(op), "closed_at": _iso(D(2026, 6, 11, 12, 0)),
             "status_history": [_h("OPEN", None, _iso(op)),
                                _h("IN_PROGRESS", None, _iso(D(2026, 6, 11, 10, 0))),
                                _h("ON_HOLD", hr, _iso(D(2026, 6, 11, 11, 0))),
                                _h("COMPLETED", None, _iso(D(2026, 6, 11, 12, 0)))]}
        return wo_lifecycle(w8.normalize_wo(r), NOW)
    ck(_mk_hold("WAITING_MACHINE_TIME")["stages"][STAGE_UPTIME] > 0, "WAITING_MACHINE_TIME→暫停·可生產")
    ck(_mk_hold("BRAND_NEW_VOCAB")["stages"][STAGE_OTHER] > 0, "未知 hold（不知道）→其他暫停")

    raw_mig = {"work_order_no": 2002, "asset_id": "EID-A", "work_type": "REACTIVE",
               "opened_at": _iso(D(2026, 6, 12, 0, 0)), "closed_at": _iso(D(2026, 6, 12, 6, 0)),
               "status_history": []}
    lcm = wo_lifecycle(w8.normalize_wo(raw_mig), NOW)
    ck(lcm["migration"] and lcm["stages"] is None and lcm["repair_h"] is None
       and abs(lcm["lead_h"] - 6.0) < 1e-9, "遷移單→stages/repair None、lead 照算")
    raw_mig_open = {"work_order_no": 2003, "asset_id": "EID-A", "work_type": "REACTIVE",
                    "opened_at": _iso(D(2026, 6, 14, 6, 0)), "status_history": []}
    ck(wo_lifecycle(w8.normalize_wo(raw_mig_open), NOW)["lead_h"] is None,
       "遷移單缺 closed_at→ongoing→lead None")
    raw_ong = {"work_order_no": 2004, "asset_id": "EID-A", "work_type": "REACTIVE",
               "opened_at": _iso(D(2026, 6, 14, 6, 0)),
               "status_history": [_h("OPEN", None, _iso(D(2026, 6, 14, 6, 0))),
                                  _h("IN_PROGRESS", None, _iso(D(2026, 6, 14, 6, 30)))]}
    lco = wo_lifecycle(w8.normalize_wo(raw_ong), NOW)
    ck(lco["ongoing"] and lco["lead_h"] is None and lco["stages"] is None
       and lco["repair_h"] is None, "ongoing→lead/stages/repair 全 None")
    # ---- NOTE-60（2026-08-22）：pra 經 wo_active_window 穿透到 lifecycle（Chris 拍板
    #      「MTTR 一起裁到 pra」——復機才是修好）。WO-36103 型：OPEN 未結單但已對齊 ⇒
    #      不再 ongoing、lead 收口在 pra、Σstages 不變式照守。
    raw_pra = dict(raw_ong, work_order_no=2005,
                   production_resumed_at=_iso(D(2026, 6, 14, 9, 30)))
    frame = wo_lifecycle(w8.normalize_wo(raw_pra), NOW)
    ck(frame is not None and not frame["ongoing"] and abs(frame["lead_h"] - 3.5) < 1e-9,
       f"pra 生效：OPEN＋已對齊 ⇒ 非 ongoing、lead 收口在 pra（3.5h）, got {frame}")
    ck(abs(sum(frame["stages"].values()) - frame["lead_h"]) < 1e-6,
       "pra 裁短窗後 Σstages ≡ lead_h 不變式照守（_phase_timeline 裁到新終點）")
    ck(abs(frame["repair_h"] - 3.0) < 1e-9,
       f"repair_h＝IN_PROGRESS 06:30→pra 09:30＝3h（MTTR 裁到復機、不吃到 now）, "
       f"got {frame['repair_h']}")
    segs = lc["phase_segs"]
    ck(segs[0][1] == op and segs[-1][2] == D(2026, 6, 11, 15, 0)
       and all(segs[i][2] <= segs[i + 1][2] for i in range(len(segs) - 1)),
       "phase_segs 時序遞增且首尾＝活躍窗")

    # ---- 取消／作廢 → None（2026-08-11 拍板①；對齊 PM 板 C-P7「不進任何分母」）----
    #      劇本＝prod 真案 WO 35185：REACTIVE、開單 47 天後取消、closed_at NULL（CANCELLED 恆 NULL）。
    raw_cx = {"work_order_no": 35185, "asset_id": "EID-A", "work_type": "REACTIVE",
              "opened_at": _iso(D(2026, 6, 1, 10, 39)), "closed_at": None,
              "status": "CANCELLED",
              "status_history": [_h("CANCELLED", None, _iso(D(2026, 6, 14, 8, 15)))]}
    ck(wo_lifecycle(w8.normalize_wo(raw_cx), NOW) is None,
       "取消單 → wo_lifecycle None（不進 lifecycle／MTTR／MTBF／pareto 任何分母）")
    ck(wo_lifecycle(w8.normalize_wo(dict(raw_cx, work_order_no=35209, status="VOIDED",
                                         status_history=[])), NOW) is None,
       "VOIDED + 空 history（eMaint 匯出型）→ None（只讀 history 末筆會漏掉整批）")
    # ★ 負對照：同一張單 CLOSED ⇒ 照樣進統計、lead 照算（結案單不得因這條修法消失）
    lc_cl = wo_lifecycle(w8.normalize_wo(dict(
        raw_cx, work_order_no=35267, status="CLOSED",
        status_history=[_h("CLOSED", None, _iso(D(2026, 6, 14, 8, 15)))])), NOW)
    ck(lc_cl is not None and lc_cl["reactive"] and lc_cl["lead_h"] > 300,
       "負對照：同單 CLOSED → 照進統計、lead 照算（WO-30006 那型 306 天 lead 是真的）")
    # 統計層自動跟上（不必在 _iter_lifecycles 再寫一次判定）
    ck(list(_iter_lifecycles({"EID-A": [w8.normalize_wo(raw_cx)]}, NOW)) == [],
       "取消單 → _iter_lifecycles 零產出（統計層結構性跟上、非第二份 predicate）")

    # ---- lifecycle_stats（8）----
    W0, W1 = D(2026, 6, 1, 0, 0), NOW
    box = {"EID-A": [w8.normalize_wo(raw_rx), w8.normalize_wo(raw_mig),
                     w8.normalize_wo(raw_ong)]}
    # PM 單（不入 S1）
    raw_pm = {"work_order_no": 2005, "asset_id": "EID-A", "work_type": "PM",
              "opened_at": _iso(op), "closed_at": _iso(D(2026, 6, 11, 10, 0)),
              "status_history": [_h("OPEN", None, _iso(op)),
                                 _h("IN_PROGRESS", None, _iso(D(2026, 6, 11, 9, 30))),
                                 _h("COMPLETED", None, _iso(D(2026, 6, 11, 10, 0)))]}
    box["EID-A"].append(w8.normalize_wo(raw_pm))
    st = lifecycle_stats(box, W0, W1)
    ck(st["n_closed"] == 2, f"n_closed 只數 completed∈窗 REACTIVE（含遷移）, got {st['n_closed']}")
    ck(st["n_ongoing"] == 1, f"n_ongoing 計數, got {st['n_ongoing']}")
    ck(st["n_migration"] == 1, "n_migration（結案·遷移）計數")
    # 窗外結案不入
    late = {"work_order_no": 2006, "asset_id": "EID-A", "work_type": "REACTIVE",
            "opened_at": _iso(D(2026, 5, 1, 0, 0)), "closed_at": _iso(D(2026, 5, 1, 3, 0)),
            "status_history": []}
    st2 = lifecycle_stats({"EID-A": [w8.normalize_wo(late)]}, W0, W1)
    ck(st2["n_closed"] == 0, "窗外結案不入 S1")
    # med/p90 手算：兩張結案 lead＝6h(rx)、6h(mig) → med=6h→0.25d
    ck(abs(st["med_lead_d"] - 0.25) < 1e-9, f"med_lead_d 手算, got {st['med_lead_d']}")
    # 階段 mean 只含非遷移（rx）：Σ=lead(rx)=6h
    ck(abs(sum(st["stage_mean_h"].values()) - 6.0) < 1e-6, "Σ(stage_mean) ≡ mean(非遷移 lead)")
    ck(abs(sum(s for _k, s, _m in st["stage_total_share"]) - 1.0) < 1e-6, "stage_total_share Σ=1.0")
    ck(len(st["slowest"]) <= 5 and all(s["stages"] is not None for s in st["slowest"]),
       "slowest ≤5 且全 stages 非 None")

    # ---- machine_health / scatter（10）----
    cat = [("za", "M-One#1", "EID-A"), ("za", "M-Two#1", "EID-B"), ("za", "M-Three#1", "EID-C")]
    two_rx = {"EID-A": [w8.normalize_wo(raw_rx),
                        w8.normalize_wo({**raw_rx, "work_order_no": 2020,
                                         "opened_at": _iso(D(2026, 6, 20, 9, 0)),
                                         "closed_at": _iso(D(2026, 6, 20, 13, 0)),
                                         "status_history": [
                                             _h("OPEN", None, _iso(D(2026, 6, 20, 9, 0))),
                                             _h("IN_PROGRESS", None, _iso(D(2026, 6, 20, 9, 0))),
                                             _h("COMPLETED", None, _iso(D(2026, 6, 20, 13, 0)))]}),
                        w8.normalize_wo(raw_mig)]}   # +遷移單（計數但不污染 mttr）
    prod = {"EID-A": {"h": 100.0, "zlo": False}, "EID-B": None,
            "EID-C": {"h": 50.0, "zlo": True}}
    mh = machine_health(two_rx, prod, W0, W1, catalog=cat)
    rA = next(r for r in mh["rows"] if r["eid"] == "EID-A")
    ck(rA["n_reactive"] == 3, f"n_reactive 含遷移單, got {rA['n_reactive']}")
    early = {"work_order_no": 2030, "asset_id": "EID-A", "work_type": "REACTIVE",
             "opened_at": _iso(D(2026, 5, 1, 0, 0)), "status_history": []}
    mh_e = machine_health({"EID-A": [w8.normalize_wo(early)]}, {}, W0, W1, catalog=cat)
    ck(next(r for r in mh_e["rows"] if r["eid"] == "EID-A")["n_reactive"] == 0,
       "n_reactive 窗界：活躍窗 start 窗外不計")
    # mttr＝結案非遷移 repair 中位（rx repair=3.833h、2020 repair=4h）→ median=3.9167h
    ck(rA["mttr_h"] is not None and abs(rA["mttr_h"] - median([230 / 60.0, 4.0])) < 1e-9,
       f"mttr 結案非遷移 repair 中位、遷移不污染, got {rA['mttr_h']}")
    ck(abs(rA["mtbf_h"] - 100.0 / 3) < 1e-9, f"mtbf=prod/n_reactive 手算, got {rA['mtbf_h']}")
    # 單位語意釘子：不做日曆天換算（prod=100,n=2→50.0，非 50/24）
    mh_u = machine_health({"EID-A": [w8.normalize_wo(raw_rx),
                                     w8.normalize_wo({**raw_rx, "work_order_no": 2021,
                                                      "opened_at": _iso(D(2026, 6, 20, 9, 0)),
                                                      "closed_at": _iso(D(2026, 6, 20, 10, 0)),
                                                      "status_history": []})]},
                          {"EID-A": {"h": 100.0, "zlo": False}}, W0, W1, catalog=cat)
    ck(abs(next(r for r in mh_u["rows"] if r["eid"] == "EID-A")["mtbf_h"] - 50.0) < 1e-9,
       "MTBF 單位＝生產小時、不除 24")
    rB = next(r for r in mh["rows"] if r["eid"] == "EID-B")
    ck(rB["mtbf_h"] is None and rB["insufficient_reason"] == "no_prod", "prod None→mtbf None/no_prod")
    rC = next(r for r in mh["rows"] if r["eid"] == "EID-C")
    ck(rC["mtbf_h"] is None and rC["insufficient_reason"] == "zero_len", "zlo→mtbf None/zero_len")
    ck(all(r["n_reactive"] >= 2 and r["mtbf_h"] is not None for r in mh["plot"])
       and "EID-B" not in {r["eid"] for r in mh["plot"]}, "plot 只含 n_reactive≥2 且雙值齊")
    ck(all(r["burden_h"] is not None for r in mh["top5"])
       and mh["top5"] == sorted(mh["top5"], key=lambda r: -r["burden_h"]),
       "top5 burden 降冪、mttr None 不入")
    sc = scatter_model(mh["plot"], focus_eid="EID-A", top_eids=("EID-A",))
    ck((sc is None) == (len(mh["plot"]) == 0), "scatter 空→None 對應")
    if sc:
        p0 = sc["points"][0]
        ck(_PX0 <= p0["cx"] <= _PX1 and _PY0 <= p0["cy"] <= _PY1, "散點座標落 plot 區內")
        ck(any(p["focus"] for p in sc["points"]), "focus 旗正確")
    ck(scatter_model([]) is None, "scatter 空 plot→None")
    sc1 = scatter_model([{"eid": "X", "name": "x", "mtbf_h": 10.0, "mttr_h": 2.0}])
    ck(sc1["med_x_px"] is None and sc1["quad"] is None, "<3 點無 med/quad")

    # ---- station_pareto（4）----
    # 同 grp 兩台（AutoOQC#1/#2）stop_h 相加、per-EID union（同機重疊兩單不雙計）
    ov1 = {"work_order_no": 3001, "asset_id": "EID-P", "work_type": "REACTIVE",
           "opened_at": _iso(D(2026, 6, 20, 0, 0)), "closed_at": _iso(D(2026, 6, 20, 4, 0)),
           "status_history": []}
    ov2 = {"work_order_no": 3002, "asset_id": "EID-P", "work_type": "REACTIVE",
           "opened_at": _iso(D(2026, 6, 20, 2, 0)), "closed_at": _iso(D(2026, 6, 20, 6, 0)),
           "status_history": []}   # 與 ov1 重疊 2h → union=6h（非 8h）
    pq = {"work_order_no": 3003, "asset_id": "EID-Q", "work_type": "REACTIVE",
          "opened_at": _iso(D(2026, 6, 21, 0, 0)), "closed_at": _iso(D(2026, 6, 21, 3, 0)),
          "status_history": []}
    pcat = [("w", "AutoOQC#1", "EID-P"), ("w", "AutoOQC#2", "EID-Q")]
    par = station_pareto({"EID-P": [w8.normalize_wo(ov1), w8.normalize_wo(ov2)],
                          "EID-Q": [w8.normalize_wo(pq)]}, W0, NOW, catalog=pcat)
    autooqc = next(g for g in par if g["grp"] == "AutoOQC")
    ck(abs(autooqc["stop_h"] - (6.0 + 3.0)) < 1e-6, f"同 grp stop_h 相加+per-EID union, got {autooqc['stop_h']}")
    ck(par == sorted(par, key=lambda g: -g["stop_h"]), "Pareto stop_h 降冪")
    ck(station_pareto({"EID-P": [w8.normalize_wo({**ov1, "status_history": [
        _h("OPEN", None, _iso(D(2026, 6, 20, 0, 0))),
        _h("IN_PROGRESS", None, _iso(D(2026, 6, 20, 0, 0))),
        _h("COMPLETED", None, _iso(D(2026, 6, 20, 4, 0)))]})] * 4},
        W0, NOW, catalog=pcat)[0]["small_sample"] is True, "n_closed=4→small_sample True")
    # 非目錄 EID → grp=eid 原樣列出（不丟）
    par_nd = station_pareto({"EID-ZZZ": [w8.normalize_wo(
        {"work_order_no": 3009, "asset_id": "EID-ZZZ", "work_type": "REACTIVE",
         "opened_at": _iso(D(2026, 6, 20, 0, 0)), "closed_at": _iso(D(2026, 6, 20, 1, 0)),
         "status_history": []})]}, W0, NOW, catalog=pcat)
    ck(any(g["grp"] == "EID-ZZZ" for g in par_nd), "非目錄 EID→grp=eid 原樣列出")
    # 真 Pareto（S3 升級，v1.1）：佔比/累計 — 單群 share=1、cum=1
    ck(abs(autooqc["share"] - 1.0) < 1e-9 and abs(autooqc["cum"] - 1.0) < 1e-9,
       "Pareto 單群 share/cum＝1.0")

    # ---- station_pareto × A10 擴散 R2（2026-08-16；SPEC-DORMANT §5.3）----
    #    ① 母機 EID（非目錄 ⇒ grp=eid 原樣）走機器視角：範圍縮到一個工位 ⇒ 只計初始整機段。
    import board_scope as _wsc
    # WP-10d（R-P11）：母機／橋上長停工位／demo showcase 機由名冊常數推導（私有樹＝EID-24742／EID-26841／EID-32176）
    import board_dormant as _wdm
    _pa = list(_wsc.PARENT_EXPANSION)[0]
    _dm = _wdm.DORMANT_BRIDGE[0][0]
    _pwo = w8.normalize_wo({
        "work_order_no": 3101, "asset_id": _pa, "work_type": "REACTIVE",
        "opened_at": _iso(D(2026, 6, 20, 8, 50)), "closed_at": _iso(D(2026, 6, 20, 15, 40)),
        "status_history": [
            _h("OPEN", None, _iso(D(2026, 6, 20, 8, 50))),
            _h("IN_PROGRESS", None, _iso(D(2026, 6, 20, 10, 25))),
            _h("CLOSED", None, _iso(D(2026, 6, 20, 15, 40)))]})
    _pwo["scope_events"] = w8.normalize_scope_events(
        [{"ts": D(2026, 6, 20, 10, 30), "scope": [_wsc.PARENT_EXPANSION[_pa][0]]}])[0]
    _par_p = station_pareto({_pa: [_pwo]}, W0, NOW, catalog=pcat, dormant_events=[])
    _gp = next(g for g in _par_p if g["grp"] == _pa)
    ck(abs(_gp["stop_h"] - (1 + 40 / 60.0)) < 1e-6,
       f"R2：母機列 stop_h＝機器視角 1h40m（非整窗 6h50m）, got {_gp['stop_h']}")
    ck(_gp["n_reactive"] == 1, "R2：n_reactive 不受機器視角影響（軸分開：單數照算）")
    #    ② 登記長停的工位（橋常數）：stop_h 歸零、但單數/MTTR 母體照算（lifecycle 軸不動）。
    _dwo = w8.normalize_wo({
        "work_order_no": 3102, "asset_id": _dm, "work_type": "REACTIVE",
        "opened_at": _iso(D(2026, 6, 20, 2, 0)), "closed_at": _iso(D(2026, 6, 20, 6, 0)),
        "status_history": [
            _h("OPEN", None, _iso(D(2026, 6, 20, 2, 0))),
            _h("IN_PROGRESS", None, _iso(D(2026, 6, 20, 2, 30))),
            _h("COMPLETED", None, _iso(D(2026, 6, 20, 6, 0)))]})
    _cat_d = pcat + [("za", "CAL Gen2 Bay#2", _dm)]
    _par_d = station_pareto({_dm: [_dwo]}, W0, NOW, catalog=_cat_d)
    _gd = next(g for g in _par_d if g["grp"] == "CAL Gen2 Bay")
    ck(_gd["stop_h"] == 0.0 and _gd["n_reactive"] == 1 and _gd["n_closed"] == 1,
       f"R2：長停工位 stop_h=0（不計任何 downtime）、單數/結案母體照算, got {_gd['stop_h']}")
    #    ③ 負對照：dormant_events=[] ⇒ 照計（參數真的有傳到底）。
    ck(abs(next(g for g in station_pareto({_dm: [_dwo]}, W0, NOW, catalog=_cat_d,
                                          dormant_events=[])
                if g["grp"] == "CAL Gen2 Bay")["stop_h"] - 4.0) < 1e-6,
       "R2：dormant_events=[] 負對照 ⇒ 照計 4h")

    # ---- demo / build（5）----
    a = demo_maint()
    b = demo_maint()
    m_a = build_maint(*a, mode="demo", wo_status="demo")
    m_b = build_maint(*b, mode="demo", wo_status="demo")
    ck(m_a["s1"]["n_closed"] == m_b["s1"]["n_closed"]
       and [x["wno"] for x in m_a["s1"]["slowest"]] == [x["wno"] for x in m_b["s1"]["slowest"]],
       "demo_maint deterministic（n_closed/榜首 wno 相等）")
    ck(len(m_a["s2"]["plot"]) >= 8 and len(m_a["s1"]["slowest"]) == 5,
       f"demo ≥8 台入 plot、slowest 5 張齊, plot={len(m_a['s2']['plot'])}")
    ck(set(m_a.keys()) >= {"s1", "s2", "s3", "win30", "win90", "truncated30", "truncated90",
                           "fallback_rate", "mode"}, "build_maint 頂層鍵齊")
    m_empty = build_maint({}, {}, {}, a[3], a[4], mode="live", wo_status="not_configured")
    ck(m_empty["s1"]["n_closed"] == 0 and m_empty["s2"]["scatter"] is None,
       "wo_status=not_configured→模型仍建、n_closed=0")
    # 兩窗狀態：未給→回退頂層 wo_status（舊行為）
    ck(m_empty["wo_status30"] == "not_configured" and m_empty["wo_status90"] == "not_configured",
       "wo_status30/90 未給→回退 wo_status")
    ck(m_a["wo_status30"] == "demo" and m_a["wo_status90"] == "demo", "demo→兩窗皆 demo")
    # verify MED：不對稱失敗（30d 失敗、90d 成功）＝兩窗狀態獨立帶出，模板據此分節降級
    m_asym = build_maint({}, b[1], b[2], a[3], a[4], mode="live",
                         wo_status="error", wo_status30="error", wo_status90="live")
    ck(m_asym["wo_status30"] == "error" and m_asym["wo_status90"] == "live"
       and m_asym["s1"]["n_closed"] == 0 and len(m_asym["s2"]["rows"]) > 0,
       "不對稱：30d error(S1 空) / 90d live(S2 有列) 各自帶出（成功窗不被假裝成零）")

    # ---- Δ 自我趨勢（S2b, v1.1）（12）----
    PS, PE = D(2026, 4, 1, 0, 0), D(2026, 5, 1, 0, 0)          # 前期窗 [4/1, 5/1]

    def _prx(no, eid, dom, rep_h):                             # 前期結案 REACTIVE（repair=rep_h）
        op = D(2026, 4, dom, 8, 0)
        cl = op + _dt.timedelta(hours=0.2 + rep_h)
        return w8.normalize_wo(
            {"work_order_no": no, "asset_id": eid, "work_type": "REACTIVE",
             "opened_at": _iso(op), "closed_at": _iso(cl),
             "status_history": [_h("OPEN", None, _iso(op)),
                                _h("IN_PROGRESS", None, _iso(op + _dt.timedelta(hours=0.2))),
                                _h("COMPLETED", None, _iso(cl))]})
    # 2 張結案（repair 4h/6h→mttr med 5h）+ prod 100 → n=2, mtbf=50, mttr=5
    ps = _prev_machine_stats({"EID-A": [_prx(6001, "EID-A", 5, 4.0), _prx(6002, "EID-A", 9, 6.0)]},
                             {"EID-A": {"h": 100.0}}, PS, PE, NOW)
    ck(ps["EID-A"]["n_reactive"] == 2 and abs(ps["EID-A"]["mtbf_h"] - 50.0) < 1e-9
       and abs(ps["EID-A"]["mttr_h"] - 5.0) < 1e-9, "prev stats n/mtbf/mttr 手算")
    # 窗歸屬：窗前單(3/20)不計；跨窗結案單(opened 4/25∈窗、completed 5/10∉窗)計 n、排除 mttr
    before = w8.normalize_wo({"work_order_no": 6010, "asset_id": "EID-W", "work_type": "REACTIVE",
                              "opened_at": _iso(D(2026, 3, 20, 8, 0)),
                              "closed_at": _iso(D(2026, 3, 20, 12, 0)), "status_history": []})
    straddle = w8.normalize_wo({"work_order_no": 6011, "asset_id": "EID-W", "work_type": "REACTIVE",
                                "opened_at": _iso(D(2026, 4, 25, 8, 0)),
                                "closed_at": _iso(D(2026, 5, 10, 8, 0)),
                                "status_history": [
                                    _h("OPEN", None, _iso(D(2026, 4, 25, 8, 0))),
                                    _h("IN_PROGRESS", None, _iso(D(2026, 4, 25, 10, 0))),
                                    _h("COMPLETED", None, _iso(D(2026, 5, 10, 8, 0)))]})
    psw = _prev_machine_stats({"EID-W": [before, straddle, _prx(6012, "EID-W", 10, 4.0)]},
                              {"EID-W": {"h": 30.0}}, PS, PE, NOW)
    ck(psw["EID-W"]["n_reactive"] == 2, "窗歸屬：窗前單不計、跨窗(opened∈窗)計 → n=2")
    ck(abs(psw["EID-W"]["mttr_h"] - 4.0) < 1e-9, "MTTR 只含 completed∈窗（跨窗結案排除）")
    # 遷移單：計入 n_reactive、排除於 MTTR（與本期一致）
    mig = w8.normalize_wo({"work_order_no": 6020, "asset_id": "EID-M", "work_type": "REACTIVE",
                           "opened_at": _iso(D(2026, 4, 12, 8, 0)),
                           "closed_at": _iso(D(2026, 4, 12, 14, 0)), "status_history": []})
    sgm = _prev_machine_stats({"EID-M": [mig, _prx(6021, "EID-M", 14, 2.0)]},
                              {"EID-M": {"h": 40.0}}, PS, PE, NOW)
    ck(sgm["EID-M"]["n_reactive"] == 2 and abs(sgm["EID-M"]["mttr_h"] - 2.0) < 1e-9,
       "遷移單：計入 n_reactive、排除於 MTTR")
    # 前期段生產小時差集（app 已算好差集傳入；此處驗 mtbf=prod/n 直用）
    ck(_prev_machine_stats({"EID-D": [_prx(6030, "EID-D", 6, 3.0), _prx(6031, "EID-D", 8, 3.0)]},
                           {"EID-D": {"h": 24.0}}, PS, PE, NOW)["EID-D"]["mtbf_h"] == 12.0,
       "前期 MTBF＝前期段生產小時 / 前期 REACTIVE 數")
    # _delta 語意色（MTBF 越大越好／MTTR 越小越好）
    ck(_delta(50.0, 30.0, True)["good"] is True and _delta(50.0, 30.0, True)["dir"] == "up",
       "MTBF ↑＝改善（good True）")
    ck(_delta(20.0, 30.0, True)["good"] is False and _delta(20.0, 30.0, True)["dir"] == "down",
       "MTBF ↓＝惡化（good False）")
    ck(_delta(8.0, 5.0, False)["good"] is False and _delta(8.0, 5.0, False)["dir"] == "up",
       "MTTR ↑＝惡化（good False）")
    ck(_delta(3.0, 5.0, False)["good"] is True and _delta(3.0, 5.0, False)["dir"] == "down",
       "MTTR ↓＝改善（good True）")
    ck(_delta(5.0, 5.0, True)["dir"] == "flat" and _delta(5.0, 5.0, True)["good"] is None
       and _delta(None, 5.0, True) is None and _delta(5.0, None, True) is None,
       "持平 flat/good None；任一 None→不可比")
    # _attach_deltas：前期 <2 REACTIVE → Δ None（資料不足）；≥2 → Δ 有值
    rows_x = [{"eid": "EID-A", "mtbf_h": 80.0, "mttr_h": 3.0},
              {"eid": "EID-Z", "mtbf_h": 10.0, "mttr_h": 2.0}]
    _attach_deltas(rows_x, {"EID-A": ps["EID-A"],
                            "EID-Z": {"n_reactive": 1, "mttr_h": 9.0, "mtbf_h": 99.0}})
    ck(rows_x[0]["mtbf_delta"] is not None and rows_x[0]["mtbf_delta"]["good"] is True
       and rows_x[1]["mtbf_delta"] is None and rows_x[1]["mttr_delta"] is None,
       "前期<2 REACTIVE→Δ None；≥2 且 MTBF↑→改善")
    # demo：Δ 三態都現形（改善/惡化/資料不足）
    ck(m_a["has_delta"] is True, "demo build has_delta True")
    _dm = [r for r in m_a["s2"]["rows"] if r.get("mtbf_delta")]
    _imp = [r for r in _dm if r["mtbf_delta"]["good"] is True]
    _wor = [r for r in _dm if r["mtbf_delta"]["good"] is False]
    _insuf = [r for r in m_a["s2"]["rows"] if r["mtbf_h"] is not None and r.get("mtbf_delta") is None]
    ck(_imp and _wor and _insuf,
       f"demo Δ 三態現形：改善={len(_imp)} 惡化={len(_wor)} 不足={len(_insuf)}")
    # 無前期資料 → has_delta False、每列 Δ None（snapshot/未接通/budget 用盡的誠實態）
    m_np = build_maint({}, two_rx, prod, (W0, W1), (W0, W1), mode="live", wo_status="live")
    ck(m_np["has_delta"] is False and all(r.get("mtbf_delta") is None for r in m_np["s2"]["rows"]),
       "無前期資料→has_delta False、每列 Δ None")

    # ---- 一鍵結單誠實標記（選項 b；計算不變、只旗標/計數/顯示）（12）----
    def _mk_oc(no, opd, cld, eid="EID-OC"):              # 一鍵結單：三轉移全蓋 closed 時戳
        return w8.normalize_wo(
            {"work_order_no": no, "asset_id": eid, "work_type": "REACTIVE",
             "opened_at": _iso(opd), "closed_at": _iso(cld),
             "status_history": [_h("OPEN", None, _iso(cld)),
                                _h("IN_PROGRESS", None, _iso(cld)),
                                _h("COMPLETED", None, _iso(cld))]})

    def _mk_rx(no, opd, rep_h, eid="EID-MX"):            # 正常結案（IN_PROGRESS 走 rep_h）
        cl = opd + _dt.timedelta(hours=rep_h)
        return w8.normalize_wo(
            {"work_order_no": no, "asset_id": eid, "work_type": "REACTIVE",
             "opened_at": _iso(opd), "closed_at": _iso(cl),
             "status_history": [_h("OPEN", None, _iso(opd)),
                                _h("IN_PROGRESS", None, _iso(opd)),
                                _h("COMPLETED", None, _iso(cl))]})
    # (1) 一鍵結單 fixture：opened 09:00、三 history 全蓋 15:00、closed 15:00
    oclc = wo_lifecycle(_mk_oc(7001, D(2026, 6, 11, 9, 0), D(2026, 6, 11, 15, 0)), NOW)
    ck(abs(oclc["repair_h"]) < 1e-9 and oclc["oneclick"] is True
       and abs(oclc["stages"][STAGE_WAIT_START] - 6.0) < 1e-9
       and abs(sum(oclc["stages"].values()) - oclc["lead_h"]) < 1e-6,
       "一鍵結單 fixture：repair 0/oneclick True/等動工 6h/Σ≡lead")
    # (2) 正常 → oneclick False；遷移 → None；ongoing → None
    ck(lc["oneclick"] is False, "正常結案（repair>0）→oneclick False")
    ck(lcm["oneclick"] is None and lco["oneclick"] is None, "遷移單/ongoing→oneclick None（不可知）")
    # (3) 兩張一鍵結單機 → mttr0/n_mttr2/n_oneclick2/旗True/burden0/入 plot/散點 oc True
    mh_oc = machine_health(
        {"EID-OC": [_mk_oc(7001, D(2026, 6, 11, 9, 0), D(2026, 6, 11, 15, 0)),
                    _mk_oc(7002, D(2026, 6, 20, 9, 0), D(2026, 6, 20, 15, 0))]},
        {"EID-OC": {"h": 200.0, "zlo": False}}, W0, W1, catalog=[("za", "OneClick#1", "EID-OC")])
    rOC = next(r for r in mh_oc["rows"] if r["eid"] == "EID-OC")
    ck(abs(rOC["mttr_h"]) < 1e-9 and rOC["n_mttr"] == 2 and rOC["n_oneclick"] == 2
       and rOC["mttr_oneclick"] is True and abs(rOC["burden_h"]) < 1e-9,
       "一鍵結單機：mttr0/n_mttr2/n_oneclick2/旗True/burden0")
    ck(rOC["eid"] in {r["eid"] for r in mh_oc["plot"]}
       and mh_oc["n_oneclick_total"] == 2 and mh_oc["n_mttr_total"] == 2,
       "一鍵結單機仍入 plot + machine_health 總計")
    sc_oc = scatter_model(mh_oc["plot"])
    ck(sc_oc and any(p["oc"] for p in sc_oc["points"]), "散點點 oc pass-through True")
    # (4) 混合機（1 一鍵 + 2 正常 4h/6h）→ mttr=median[0,4,6]=4、n_oneclick1、旗 False
    mh_mx = machine_health(
        {"EID-MX": [_mk_oc(7010, D(2026, 6, 15, 9, 0), D(2026, 6, 15, 15, 0), "EID-MX"),
                    _mk_rx(7011, D(2026, 6, 16, 9, 0), 4.0),
                    _mk_rx(7012, D(2026, 6, 17, 9, 0), 6.0)]},
        {"EID-MX": {"h": 300.0, "zlo": False}}, W0, W1, catalog=[("za", "Mixed#1", "EID-MX")])
    rMX = next(r for r in mh_mx["rows"] if r["eid"] == "EID-MX")
    ck(abs(rMX["mttr_h"] - 4.0) < 1e-9 and rMX["n_oneclick"] == 1 and rMX["mttr_oneclick"] is False,
       "混合機：mttr=median[0,4,6]=4、n_oneclick1、旗 False")
    # (5) lifecycle_stats n_decomposed / n_oneclick 手算（1 一鍵 + 1 正常 + 1 遷移）
    st_oc = lifecycle_stats(
        {"EID-OC": [_mk_oc(7005, D(2026, 6, 11, 9, 0), D(2026, 6, 11, 15, 0)),
                    w8.normalize_wo(raw_rx), w8.normalize_wo(raw_mig)]}, D(2026, 6, 1, 0, 0), NOW)
    ck(st_oc["n_decomposed"] == 2 and st_oc["n_oneclick"] == 1,
       "S1：n_decomposed(非遷移)=2、n_oneclick=1")
    # (6) station_pareto：全一鍵 grp → mttr0/旗True/n_oneclick2
    par_oc = station_pareto(
        {"EID-P": [_mk_oc(7020, D(2026, 6, 21, 9, 0), D(2026, 6, 21, 15, 0), "EID-P"),
                   _mk_oc(7021, D(2026, 6, 22, 9, 0), D(2026, 6, 22, 15, 0), "EID-P")]},
        W0, NOW, catalog=[("w", "OCsta#1", "EID-P")])
    g_oc = next(g for g in par_oc if g["grp"] == "OCsta")
    ck(abs(g_oc["mttr_h"]) < 1e-9 and g_oc["mttr_oneclick"] is True and g_oc["n_oneclick"] == 2,
       "S3 全一鍵 grp：mttr0/旗True/n_oneclick2")
    # (7) Δ 抑制：本期一鍵列→mttr_delta None、mtbf_delta 照算；前期一鍵0→mttr_delta None
    rows_oc = [{"eid": "EID-OC", "mtbf_h": 80.0, "mttr_h": 0.0, "mttr_oneclick": True}]
    _attach_deltas(rows_oc, {"EID-OC": {"n_reactive": 3, "mtbf_h": 40.0, "mttr_h": 5.0,
                                        "n_oneclick": 0}})
    ck(rows_oc[0]["mttr_delta"] is None and rows_oc[0]["mtbf_delta"] is not None
       and rows_oc[0]["mtbf_delta"]["good"] is True,
       "Δ 抑制：本期一鍵→mttr_delta None、mtbf_delta 照算(改善)")
    rows_poc = [{"eid": "EID-Z", "mtbf_h": 50.0, "mttr_h": 4.0, "mttr_oneclick": False}]
    _attach_deltas(rows_poc, {"EID-Z": {"n_reactive": 2, "mtbf_h": 40.0, "mttr_h": 0.0,
                                        "n_oneclick": 2}})
    ck(rows_poc[0]["mttr_delta"] is None and rows_poc[0]["mtbf_delta"] is not None,
       "Δ 抑制：前期一鍵0→mttr_delta None、mtbf_delta 照算")
    # (8) demo：s1/s2 一鍵結單=3、showcase 機（idx13=EID-32176）旗 True、has_oneclick True；空→False
    ck(m_a["s1"]["n_oneclick"] == 3 and m_a["s2"]["n_oneclick_total"] == 3
       and m_a["has_oneclick"] is True, "demo：s1/s2 一鍵結單=3、has_oneclick True")
    _r13 = next((r for r in m_a["s2"]["rows"] if r["eid"] == wem.EQMT_CATALOG[13][2]), None)
    ck(_r13 is not None and _r13["mttr_oneclick"] is True and _r13["n_oneclick"] == 3,
       "demo showcase 機（EQMT_CATALOG idx13）mttr_oneclick True/n_oneclick 3")
    ck(build_maint({}, {}, {}, a[3], a[4], mode="live",
                   wo_status="not_configured")["has_oneclick"] is False,
       "空 build → has_oneclick False")

    # 治理釘子：s2 rows 與 s1 slowest 的鍵集合不含任何 per-person 欄
    banned = ("actor", "assignee", "technician", "operator", "confirmed_by", "person", "who")
    keys = set()
    for r in m_a["s2"]["rows"]:
        keys |= set(r.keys())
    for r in m_a["s1"]["slowest"]:
        keys |= set(r.keys())
    ck(not any(any(b in k.lower() for b in banned) for k in keys),
       f"§24：輸出無 per-person 欄, keys={sorted(keys)}")
    # showcase 最慢榜首＝21 天超長單（等料佔比明顯）
    ck(m_a["s1"]["slowest"] and m_a["s1"]["slowest"][0]["lead_h"] > 15 * 24,
       "showcase 21 天超長單＝最慢榜首")

    print(f"[board_maint] selftest PASS — {n} 項斷言全過："
          f"median/p90(nearest-rank)/station_group；wo_lifecycle 階段分解 Σ≡lead + 遷移/ongoing "
          f"排除 + **取消/作廢→None（不進任何分母；空 history 的 VOIDED 也擋；CLOSED 負對照照算）**；"
          f"lifecycle_stats completed∈窗 + 階段 mean 可加 + slowest；machine_health "
          f"MTBF=生產小時/次(不除24) + zlo/no_prod 資料不足 + plot≥2 REACTIVE；scatter 座標/壞象限/"
          f"focus；Δ 自我趨勢 前期窗歸屬+差集生產+遷移排除+語意色+三態；station_pareto per-EID union "
          f"+ 小樣本 + 非目錄 EID + 佔比/累計；一鍵結單誠實標記（oneclick 旗/n_oneclick 計數/"
          f"mttr_oneclick 顯示旗/Δ 抑制；MTTR 計算一位元不變）；demo deterministic + build 鍵齊 + "
          f"§24 無 per-person。凍結核心未 import（不連 MES）。")
    return n


def main():
    ap = argparse.ArgumentParser(
        description="維修體質頁（Maintenance Health）呈現模型：純函式 + demo 合成。"
                    "/equipment 子頁＝統計視角（S1 單卡在哪 / S2 機台體質 / S3 站別 Pareto）。")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（inline fixture、零 MES）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    print("[board_maint] 純函式呈現模型（S1/S2/S3）；離線驗證：--selftest。"
          "app 層以 load_maint 消費，per-EID 生產小時由 app.load/load_fleet_by_cls 取好傳入。")


if __name__ == "__main__":
    main()
