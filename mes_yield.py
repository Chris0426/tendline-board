#!/usr/bin/env python3
"""Tendline Board §26 B/C — 產出(OUTPUT) + 良率(YIELD) 軸（凍結 v0, §26.10 設備單位模型）。

`mes_timeline.py`（稼動軸）的並列姊妹模組：同樣『參數化審過 SQL 拉 raw + Python 確定性彙總
+ _selftest』凍結紀律，runtime 無 LLM（§15）、唯讀。**不改 mes_timeline**，只 import 重用
（fetch_fleet / EQP_STATIONS / _connect / **fold_eid**＝形④ 別名折疊，2026-09-02 §10.8-8）。

★ 口徑誠實（§26.5，CRITICAL）：真實不良口徑＝**未定（待製程 SME）**。raw fail% 因含重測/多試
  **高估真實不良**（實證：AutoOQC raw 不良率遠高於真實不良率、倍率因產品別而異；CAL FPY
  首試 vs right-first-time 差距很大）。故本模組一律輸出 **RAW v0、明標 "raw, pending §26.5"**，
  **不發明倍率、不為重測暗自校正**。可運作的 raw 儀表板本身＝招募 SME 的文件。

設計（探索已驗，見 MES-MULTISTATION-FINDINGS §10 + _explore_output*.py / _explore_yield*.py）：
  - 主單位＝實體機台（MES parent EID）；模組＝下鑽（§26.10）。重用 mes_timeline.fetch_fleet
    取機台級 fleet（已含自參照 dependent-equipment export 防護 + 模組排除）。
  - ★ OUTPUT = COUNT(DISTINCT matMaterialLotID)，**每層各自獨立 DISTINCT 計**——
    **絕不 Σ stpQuantityProcessed（≡1/段＝把重測雙計，測站膨脹 1.6–1.8×）、絕不相加子層計數**
    （= 稼動 interval-union 的『勿相加子層』同則，只是運算子從聯集換成 COUNT DISTINCT）。
    Σ機台 > 站（同件跨機台重測）、Σ模組 > 機台（同件跨模組）——故站/機台各自 DISTINCT。
  - YIELD（FPY/末試/重工）主源＝**rep.fact_segment_rv**（預算 AttemptNumber/IsLastAttempt/
    SuccessCounter，1:1 對 segment，免手刻 ROW_NUMBER）；以 sgcPK 過濾（= SegmentClassId）。
    fact_segment 不覆蓋全部站（部分加工站結構性無 fact 列）→ 該站 FPY 留 None（誠實）。
  - raw 單次良率（全站可得）＝process_segment_v 的 qFail/qProc + ResultSuccess（含 product split）。
  - 產品別（§26.5 O4）= GROUP BY SegmentProductLineCode（plover/kestrel/heron）；NULL/其它
    （substrate/非產品）入獨立 "(unattributed)" 桶、不混入產品總數。
  - **每筆參數化 fetch 結尾 OPTION(RECOMPILE)**（根因＝SQL Server 參數嗅探計劃不穩定，同 query
    0.7s↔90s HYT00；多站迴圈必需）。**逐站 per-class 點查（SegmentClassID=?）**，
    **絕不全線掃描**（無 class filter 對 process_segment_v 全掃即逾時）。

CLI：--station <cls>（預設 CAL，含機台級下鑽）/ --all-stations（curated，短窗友善、給儀表板計分板）
     / --days N / --json / --selftest（離線確定性數學，不連 MES）。
"""
import argparse
import sys

import mes_timeline as mt  # noqa: F401 — 重用 fetch_fleet / EQP_STATIONS / _connect / fold_eid / hms（凍結，不改）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CAL_CLASS = mt.CAL_CLASS
# §26.5 良率口徑＝OPEN（待製程 SME）。本模組所有 yield 數字皆 RAW v0、未為重測/多試校正。
CALIBER = "raw, pending §26.5 caliber"
PRODUCT_LINES = ("plover", "kestrel", "heron")   # §26.5 O4：heron 納/濾＝SME 待決，先全列
UNATTRIBUTED = "(unattributed)"                   # NULL SegmentProductLineCode（substrate/非產品）獨立桶


# ---- 參數化 SQL（凍結；每筆 OPTION(RECOMPILE)、per-class 點查、唯讀） -----------------
def fetch_station_raw(cur, days, cls=CAL_CLASS, win=None):
    """站級 raw 聚合（無 equipment join＝最輕）。回 dict。
       OUTPUT=COUNT(DISTINCT lot)（**非 Σqty**）；good=任一段 ResultSuccess=1 的 distinct lot。
       pscpk（=fact_segment 的 SegmentClassId）由本查一併取回，省一次往返。
       日曆窗 additive path（/daily 昨日戰報 2026-07-07）；win=None＝原滾動窗 byte-identical。"""
    if win is None:
        cur.execute("""
    SELECT
      MIN(s.sgcPK)                                                          AS pscpk,
      COUNT(DISTINCT s.matMaterialLotID)                                    AS units,
      COUNT(DISTINCT CASE WHEN s.ResultSuccess = 1 THEN s.matMaterialLotID END) AS good_units,
      COUNT(*)                                                              AS segs,
      SUM(CAST(ISNULL(s.stpQuantityProcessed, 0) AS float))                 AS qproc,
      MAX(s.stpQuantityProcessed)                                           AS qmax,
      SUM(CAST(ISNULL(s.stpQuantityFailed, 0) AS float))                    AS qfail,
      SUM(CASE WHEN s.ResultSuccess = 1 THEN 1 ELSE 0 END)                  AS rspass,
      SUM(CASE WHEN s.ResultSuccess = 0 THEN 1 ELSE 0 END)                  AS rsfail
    FROM dbo.process_segment_v s
    WHERE s.SegmentClassID = ?
      AND s.stpStartTime >= DATEADD(day, -?, GETDATE())
      AND s.stpStartTime <= GETDATE()
    OPTION (RECOMPILE)
    """, cls, days)
    else:
        cur.execute("""
    SELECT
      MIN(s.sgcPK)                                                          AS pscpk,
      COUNT(DISTINCT s.matMaterialLotID)                                    AS units,
      COUNT(DISTINCT CASE WHEN s.ResultSuccess = 1 THEN s.matMaterialLotID END) AS good_units,
      COUNT(*)                                                              AS segs,
      SUM(CAST(ISNULL(s.stpQuantityProcessed, 0) AS float))                 AS qproc,
      MAX(s.stpQuantityProcessed)                                           AS qmax,
      SUM(CAST(ISNULL(s.stpQuantityFailed, 0) AS float))                    AS qfail,
      SUM(CASE WHEN s.ResultSuccess = 1 THEN 1 ELSE 0 END)                  AS rspass,
      SUM(CASE WHEN s.ResultSuccess = 0 THEN 1 ELSE 0 END)                  AS rsfail
    FROM dbo.process_segment_v s
    WHERE s.SegmentClassID = ?
      AND s.stpStartTime >= ?
      AND s.stpStartTime < ?
    OPTION (RECOMPILE)
    """, cls, win[0], win[1])
    r = cur.fetchone()
    return {
        "pscpk": r[0], "units": r[1] or 0, "good_units": r[2] or 0, "n_seg": r[3] or 0,
        "qty_proc": float(r[4] or 0), "qty_max": (float(r[5]) if r[5] is not None else None),
        "qty_fail": float(r[6] or 0), "rs_pass": r[7] or 0, "rs_fail": r[8] or 0,
    }


def fetch_product_split(cur, days, cls=CAL_CLASS, win=None):
    """產品別 OUTPUT（§26.5 O4）：每 SegmentProductLineCode 的 distinct lot。NULL→ unattributed 桶。
       日曆窗 additive path（/daily 昨日戰報 2026-07-07）；win=None＝原滾動窗 byte-identical。"""
    if win is None:
        cur.execute("""
    SELECT s.SegmentProductLineCode AS pl, COUNT(DISTINCT s.matMaterialLotID) AS units
    FROM dbo.process_segment_v s
    WHERE s.SegmentClassID = ?
      AND s.stpStartTime >= DATEADD(day, -?, GETDATE())
      AND s.stpStartTime <= GETDATE()
    GROUP BY s.SegmentProductLineCode
    OPTION (RECOMPILE)
    """, cls, days)
    else:
        cur.execute("""
    SELECT s.SegmentProductLineCode AS pl, COUNT(DISTINCT s.matMaterialLotID) AS units
    FROM dbo.process_segment_v s
    WHERE s.SegmentClassID = ?
      AND s.stpStartTime >= ?
      AND s.stpStartTime < ?
    GROUP BY s.SegmentProductLineCode
    OPTION (RECOMPILE)
    """, cls, win[0], win[1])
    out = {}
    for pl, units in cur.fetchall():
        out[pl if pl else UNATTRIBUTED] = units or 0
    return out


def fetch_fpy_fact_segment(cur, days, pscpk, win=None):
    """FPY/末試/重工 raw（rep.fact_segment_rv，單位＝MaterialId）。pscpk=SegmentClassId。
       att1≠不良（已 pass 仍複測＝benign retest）；故同時回 retest（良性）與 rework（真重工：att1 fail→retry）。
       回 None 表 fact_segment 窗內無此 class 列（部分加工站結構性無 fact 覆蓋＝誠實留白）。
       日曆窗 additive path（/daily 昨日戰報 2026-07-07）；win=None＝原滾動窗 byte-identical。"""
    if pscpk is None:
        return None
    if win is None:
        cur.execute("""
    WITH fs AS (
      SELECT MaterialId,
        MAX(CASE WHEN AttemptNumberBySegmentClass = 1 THEN CAST(SuccessCounter AS int) END) AS first_ok,
        MAX(CASE WHEN IsLastAttemptBySegmentClass = 1 THEN CAST(SuccessCounter AS int) END) AS last_ok,
        MAX(AttemptNumberBySegmentClass) AS attempts
      FROM rep.fact_segment_rv
      WHERE SegmentClassId = ?
        AND StartTime >= DATEADD(day, -?, GETDATE())
        AND StartTime <= GETDATE()
      GROUP BY MaterialId
    )
    SELECT
      COUNT(*)                                                          AS units,
      SUM(CASE WHEN first_ok = 1 THEN 1 ELSE 0 END)                     AS att1_pass,
      SUM(CASE WHEN last_ok = 1 THEN 1 ELSE 0 END)                      AS last_pass,
      SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END)                     AS retested,
      SUM(CASE WHEN first_ok = 1 AND attempts > 1 THEN 1 ELSE 0 END)    AS passed_but_retested,
      SUM(CASE WHEN first_ok = 0 AND attempts > 1 THEN 1 ELSE 0 END)    AS failed_then_retried
    FROM fs
    OPTION (RECOMPILE)
    """, pscpk, days)
    else:
        cur.execute("""
    WITH fs AS (
      SELECT MaterialId,
        MAX(CASE WHEN AttemptNumberBySegmentClass = 1 THEN CAST(SuccessCounter AS int) END) AS first_ok,
        MAX(CASE WHEN IsLastAttemptBySegmentClass = 1 THEN CAST(SuccessCounter AS int) END) AS last_ok,
        MAX(AttemptNumberBySegmentClass) AS attempts
      FROM rep.fact_segment_rv
      WHERE SegmentClassId = ?
        AND StartTime >= ?
        AND StartTime < ?
      GROUP BY MaterialId
    )
    SELECT
      COUNT(*)                                                          AS units,
      SUM(CASE WHEN first_ok = 1 THEN 1 ELSE 0 END)                     AS att1_pass,
      SUM(CASE WHEN last_ok = 1 THEN 1 ELSE 0 END)                      AS last_pass,
      SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END)                     AS retested,
      SUM(CASE WHEN first_ok = 1 AND attempts > 1 THEN 1 ELSE 0 END)    AS passed_but_retested,
      SUM(CASE WHEN first_ok = 0 AND attempts > 1 THEN 1 ELSE 0 END)    AS failed_then_retried
    FROM fs
    OPTION (RECOMPILE)
    """, pscpk, win[0], win[1])
    r = cur.fetchone()
    if not r or not r[0]:
        return None
    return {"units": r[0] or 0, "att1_pass": r[1] or 0, "last_pass": r[2] or 0,
            "retested": r[3] or 0, "passed_but_retested": r[4] or 0,
            "failed_then_retried": r[5] or 0}


def _agg_machine_yield(pk_eids, seg_rows):
    """純函式（離線可單測）：stpPK→[eid] 對映 + 每段 (stpPK,lot,rs,qproc,qfail) → per-eid 聚合。
       逐 (segment,equipment) 對累計＝等價原 SQL 的 `process_segment_v JOIN segment_equipment_v`
       一列（含同段跨多 eid 的 fan-out）。units=COUNT(DISTINCT lot)、good=DISTINCT lot@rs=1、
       n_seg=COUNT(*)、qproc/qfail=Σ、rs_pass/rs_fail=Σ(rs=1/0)。lot=None 不計入 DISTINCT（同 SQL）。"""
    agg = {}
    for pk, lot, rs, qproc, qfail in seg_rows:
        for eid in pk_eids.get(pk, ()):              # 每 (段,設備) 對＝原 JOIN 一列
            d = agg.get(eid)
            if d is None:
                d = agg[eid] = {"lots": set(), "good": set(), "n_seg": 0,
                                "qproc": 0.0, "qfail": 0.0, "rs_pass": 0, "rs_fail": 0}
            d["n_seg"] += 1
            d["qproc"] += qproc
            d["qfail"] += qfail
            if lot is not None:
                d["lots"].add(lot)
                if rs == 1:
                    d["good"].add(lot)
            if rs == 1:
                d["rs_pass"] += 1
            elif rs == 0:
                d["rs_fail"] += 1
    return {eid: {"units": len(d["lots"]), "good_units": len(d["good"]), "n_seg": d["n_seg"],
                  "qty_proc": d["qproc"], "qty_fail": d["qfail"],
                  "rs_pass": d["rs_pass"], "rs_fail": d["rs_fail"]}
            for eid, d in agg.items()}


def fetch_machine_yield(cur, eids, days, cls=CAL_CLASS, win=None):
    """機台級（parent EID）OUTPUT+raw yield 下鑽。filter 至 fleet EIDs→Gen2 槽位重複列自動丟。
       每機台各自 COUNT(DISTINCT lot)（**勿相加得站**）。回 {eid: {...}}。

       ★ 拆查詢（2026-06-29 session B2，§perf）：**不**用 `process_segment_v s JOIN
       segment_equipment_v se` 單句——segment_equipment_v 內部已 inner-join process_segment_v
       （見 mes_timeline.fetch_fleet docstring），外接即雙展開＋自我 join，大窗計劃爆炸→HYT00
       （與站頁 timeline 同款雙 view 病；timeline 修好後 units 區塊就會踩到這條，故一起修）。
       改兩段單源查詢 + Python join（ResultSuccess 只 process_segment_v 有→無法照搬單表法）：
         Y1: stpPK→eid（segment_equipment_v 單表，= P2 形＝大窗快）；只留 fleet EID。
         Y2: stpPK→lot/ResultSuccess/qty（process_segment_v 單表，最輕）。
       純聚合在 _agg_machine_yield（離線單測涵蓋）＝等價原 SQL JOIN-GROUP BY、含 fan-out。
       日曆窗 additive path（/daily 昨日戰報 2026-07-07）；win=None＝原滾動窗 byte-identical
       （Y1 保留 stpEndTime 過濾、Y2 不變；win 只替換時間 predicate）。

       ⚠⚠ **#123 站別折疊（`mes_timeline.CLASS_ALIASES`）刻意不套在本函式**（2026-09-10、
       `SPEC-RUNWINDOW.md` §10.8-10、鐵則 C-RW21）：Y1／Y2 是**站級良率＝產品視角**
       （按 `ResultSuccess`／lot 算），而站別折疊是**稼動＝設備視角**的口徑（把機台跑
       Kestrel 的時間算回同一台機）。Kestrel 的良率有**自己的一本帳**（45 站 grid、
       `mes_qlik`／`board_kestrel`）——把 Kestrel 的 lot 折進 Plover 站的良率分母，
       等於把兩本帳混在一起，兩邊都會錯。**形④ EID 折疊（`mt.fold_eid`）照舊要套**，
       那是「同一台機的兩個編號」、與站別無關。
       ⇒ 連帶（**不是 bug**）：`fetch_fleet` 折疊後，窗內只跑 Kestrel 的機台（如 Seal#7
       `EID-25846`）會進 fleet 但在這裡 `units=0`＝「有機台、無 Plover 產出」，正確。"""
    if not eids:
        return {}
    eidset = set(eids)
    # Y1：stpPK → fleet eid（單表 segment_equipment_v，P2 形）。同段跨多 eid → list（保 fan-out 語意）。
    # ★ d=30 斷崖修（2026-06-30 B4，P12 實證）：補 `stpEndTime` 過濾——無此過濾時此掃描在 30 天斷崖
    # （machine_yield 30d=43.7s 幾乎全在 Y1；Y2 僅 0.7s）。加 end 過濾＝P12a 的 3.5s。口徑：機台級
    # yield 因此只計封閉段（排開放/在製段）；30 天歷史窗開放段≈0、且開放段無完成結果＝可忽略，並與
    # 稼動時間軸（fetch_segments 亦 end 過濾）對齊。Y2 不改（0.7s）；其未配對 stpPK 在 _agg 自然丟。
    # ★ M4 修正（2026-07-10，04-mes-text2sql 帶話＋Chris Q14 實證）：`>` → `>=`——嚴格大於把零工期段
    # （stpEndTime=stpStartTime、合法瞬時事件＝進料檢/序號寫入）的 stpPK 從 Y1 刪掉 → Y2 的
    # ResultSuccess 在 _agg 配不到對＝machine_yield 系統性偏差（Q14d @30d：segDiePlacement fail%
    # 遠低於真實值——sbsSubstrate 進料檢零工期段 ~100% fail 被整批稀釋掉）。Q14c perf：
    # 500→703ms 無斷崖（防護＝IS NOT NULL）。⚠ 此改動後 machine_yield 讀值全面重 baseline（M4）。
    if win is None:
        cur.execute("""
    SELECT se.stpPK, se.astEquipmentID
    FROM dbo.segment_equipment_v se
    WHERE se.SegmentClassID = ?
      AND se.stpStartTime >= DATEADD(day, -?, GETDATE())
      AND se.stpStartTime <= GETDATE()
      AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime
    OPTION (RECOMPILE)
    """, cls, days)
    else:
        cur.execute("""
    SELECT se.stpPK, se.astEquipmentID
    FROM dbo.segment_equipment_v se
    WHERE se.SegmentClassID = ?
      AND se.stpStartTime >= ?
      AND se.stpStartTime < ?
      AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime
    OPTION (RECOMPILE)
    """, cls, win[0], win[1])
    # ★ 形④ 別名折疊（2026-09-02、`SPEC-RUNWINDOW.md` §10.8-8）：**逐列先折疊再比對** eidset
    # ——與 `mes_timeline.fetch_segments` 逐字同形（同一支 `mt.fold_eid`，本檔不另抄表）。
    # `eids` 來自已折疊的 `fetch_fleet`（裡面是 `EID-38106`），Y1 的 MES 列卻寫著 `EID-38041`
    # ⇒ 不折的話兩邊永遠對不上：第 39 站那台機的 units/good/fail% 全部讀 0（不是「沒生產」，
    # 是配不到對）。`EID-38072` 的列直接丟（其段⊂主 EID；留著會讓同一個 stpPK 在
    # `pk_eids[pk]` 裡出現兩次 ⇒ `_agg_machine_yield` 把同一段的 n_seg/qty 各算一次＝雙算）。
    # ⚠ SQL **一個位元未動**；對折疊表以外的每一顆 EID `fold_eid` 恆等 ⇒ 逐位元原行為。
    pk_eids = {}
    for pk, eid in cur.fetchall():
        eid = mt.fold_eid(eid)
        if eid is not None and eid in eidset:
            pk_eids.setdefault(pk, []).append(eid)
    if not pk_eids:
        return {}
    # Y2：stpPK → lot/ResultSuccess/qty（單表 process_segment_v，最輕；ResultSuccess 只此有）。
    if win is None:
        cur.execute("""
    SELECT s.stpPK, s.matMaterialLotID, s.ResultSuccess,
           CAST(ISNULL(s.stpQuantityProcessed, 0) AS float),
           CAST(ISNULL(s.stpQuantityFailed, 0) AS float)
    FROM dbo.process_segment_v s
    WHERE s.SegmentClassID = ?
      AND s.stpStartTime >= DATEADD(day, -?, GETDATE())
      AND s.stpStartTime <= GETDATE()
    OPTION (RECOMPILE)
    """, cls, days)
    else:
        cur.execute("""
    SELECT s.stpPK, s.matMaterialLotID, s.ResultSuccess,
           CAST(ISNULL(s.stpQuantityProcessed, 0) AS float),
           CAST(ISNULL(s.stpQuantityFailed, 0) AS float)
    FROM dbo.process_segment_v s
    WHERE s.SegmentClassID = ?
      AND s.stpStartTime >= ?
      AND s.stpStartTime < ?
    OPTION (RECOMPILE)
    """, cls, win[0], win[1])
    rows = [(r[0], r[1], (int(r[2]) if r[2] is not None else None), r[3], r[4])
            for r in cur.fetchall()]
    return _agg_machine_yield(pk_eids, rows)


# ---- 確定性核心（凍結、純函式、可離線單測） -----------------------------------
def safe_pct(num, den):
    return (100.0 * num / den) if den else 0.0


def fail_metrics(qty_proc, qty_fail, rs_pass=0, rs_fail=0):
    """raw 單次失敗率（**attempt/segment grain；高估真實不良**，§26.5 待校正）。
       fail_pct_raw = ΣqFail / ΣqProc（qProc≡1/段→≈段級失敗比）；rs_fail_pct = 判退段比。"""
    return {
        "qty_proc": qty_proc, "qty_fail": qty_fail,
        "fail_pct_raw": safe_pct(qty_fail, qty_proc),
        "rs_judged": rs_pass + rs_fail,
        "rs_fail_pct": safe_pct(rs_fail, rs_pass + rs_fail),
    }


def fpy_metrics(fpy):
    """由 fact_segment 計數算 raw FPY 框架（單位＝MaterialId）。fpy=None→全 None（誠實留白）。
       兩框架並陳（§26.5 招募槓桿，實證差 ~15pt）：
         fpy_raw_pct   = 首試 success / units（att#1 pass，**含『已 pass 仍複測』**）
         rft_pct       = right-first-time = 首試 pass 且未再測 / units（較保守）
       另：final_pass_pct（末試 success）、rework_pct（att1 fail→retry＝真重工）、
           retest_pct（att1 pass 卻仍 att>1＝良性重測，非不良）。"""
    if not fpy:
        return {"fpy_units": None, "fpy_raw_pct": None, "final_pass_pct": None,
                "rft_pct": None, "rework_pct": None, "retest_pct": None}
    u = fpy["units"]
    rft = fpy["att1_pass"] - fpy["passed_but_retested"]
    return {
        "fpy_units": u,
        "fpy_raw_pct": safe_pct(fpy["att1_pass"], u),
        "final_pass_pct": safe_pct(fpy["last_pass"], u),
        "rft_pct": safe_pct(rft, u),
        "rework_pct": safe_pct(fpy["failed_then_retried"], u),
        "retest_pct": safe_pct(fpy["passed_but_retested"], u),
    }


def output_overlap(station_units, machine_units):
    """診斷：Σ機台 − 站（同件跨機台重測量）。**僅供誠實揭露『為何不相加』**——
       站/機台 OUTPUT 各自獨立 DISTINCT，本函式絕不被當成站值（防 §26.10 相加陷阱）。"""
    sm = sum(machine_units)
    return {"station": station_units, "sum_machines": sm, "overlap": sm - station_units}


def build_station_record(cls, raw, products, fpy, fleet_size=None, machines=None):
    """組裝站級 canonical record（站級各量獨立 DISTINCT；機台為下鑽，不回填站值）。"""
    fm = fail_metrics(raw["qty_proc"], raw["qty_fail"], raw["rs_pass"], raw["rs_fail"])
    fp = fpy_metrics(fpy)
    prod_total = sum(v for k, v in (products or {}).items() if k in PRODUCT_LINES)
    rec = {
        "cls": cls,
        "caliber": CALIBER,
        # ---- OUTPUT 軸（COUNT DISTINCT lot；非 Σqty） ----
        "output_units": raw["units"],
        "good_units": raw["good_units"],
        "n_seg": raw["n_seg"],
        "qty_max": raw["qty_max"],                 # >1 ⇒ batch 站（如 BendTest），COUNT(DISTINCT) 會低估，§26.5 SME
        "is_batch": (raw["qty_max"] is not None and raw["qty_max"] > 1),
        "products": products or {},
        "product_units": prod_total,               # 三線 distinct lot 合（不含 unattributed）
        "unattributed_units": (products or {}).get(UNATTRIBUTED, 0),
        # ---- YIELD 軸（RAW v0；§26.5 待校正） ----
        "has_yield_signal": (raw["qty_fail"] > 0 or raw["rs_fail"] > 0),
        "fail_pct_raw": fm["fail_pct_raw"],        # ΣqFail/ΣqProc（attempt grain，高估）
        "qty_fail": raw["qty_fail"],
        "rs_fail": raw["rs_fail"], "rs_pass": raw["rs_pass"],
        "rs_fail_pct": fm["rs_fail_pct"],
        "fpy_source": ("rep.fact_segment_rv" if fpy else None),
        **fp,                                      # fpy_units/fpy_raw_pct/final_pass_pct/rft_pct/rework_pct/retest_pct
        "fleet_size": fleet_size,
    }
    if machines is not None:
        rec["machines"] = machines
    return rec


def build_machine_records(fleet, myield):
    """機台級下鑽 records（每機台獨立 DISTINCT；§26.10 主單位＝機台）。"""
    out = []
    for eid, desc in fleet:
        m = myield.get(eid)
        if not m:
            out.append({"eid": eid, "desc": desc, "output_units": 0, "good_units": 0,
                        "n_seg": 0, "fail_pct_raw": None, "qty_fail": 0.0,
                        "rs_fail": 0, "rs_pass": 0, "caliber": CALIBER})
            continue
        fm = fail_metrics(m["qty_proc"], m["qty_fail"], m["rs_pass"], m["rs_fail"])
        out.append({"eid": eid, "desc": desc,
                    "output_units": m["units"], "good_units": m["good_units"],
                    "n_seg": m["n_seg"],
                    "fail_pct_raw": (fm["fail_pct_raw"] if m["qty_proc"] else None),
                    "qty_fail": m["qty_fail"],
                    "rs_fail": m["rs_fail"], "rs_pass": m["rs_pass"], "caliber": CALIBER})
    return out


# ---- 單站編排（機台主單位 + 站級各自 DISTINCT；§26.10） --------------------------
def station_yield(cur, cls, days, with_machines=True, win=None):
    """單站 OUTPUT+YIELD。with_machines=True 加機台級下鑽（呼 fetch_fleet＝req1 機台主單位路徑）。
       站級各量永遠獨立 COUNT(DISTINCT)（非 Σ機台）。
       日曆窗 additive path（/daily 昨日戰報 2026-07-07）；win=None＝原滾動窗 byte-identical。
       ★ fetch_fleet 是 mes_timeline 凍結函式、無 win 參數；win 模式下仍以 days 抓 fleet 目錄
       （fleet＝機台清單、非時間敏感量測；yield 量測本身已由 fetch_machine_yield 的 win 裁窗）。"""
    raw = fetch_station_raw(cur, days, cls, win=win)
    products = fetch_product_split(cur, days, cls, win=win)
    fpy = fetch_fpy_fact_segment(cur, days, raw["pscpk"], win=win)
    fleet_size, machines = None, None
    if with_machines:
        fleet = mt.fetch_fleet(cur, days, cls)          # 機台級 EID（模組排除 + 自參照防護）
        fleet_size = len(fleet)
        myield = fetch_machine_yield(cur, [e for e, _ in fleet], days, cls, win=win)
        machines = build_machine_records(fleet, myield)
    return build_station_record(cls, raw, products, fpy, fleet_size, machines)


def scoreboard_all_stations(days=3, stations=None, win=None):
    """全線 OUTPUT+YIELD 計分板（§26.2 T1；給儀表板）。逐站 per-class 點查（站級、不下鑽機台
       ＝短窗快又穩，避開 fetch_fleet 3-way join 的間歇 HYT00）。逐站 try/except + 重連續跑。
       回 (mes_now, board, skipped)。
       日曆窗 additive path（/daily 昨日戰報 2026-07-07）；win=None＝原滾動窗 byte-identical。"""
    stations = mt.EQP_STATIONS if stations is None else stations
    conn, cur = mt._connect()
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
    except Exception:
        conn.close()
        raise
    board, skipped = [], []
    for cls in stations:
        try:
            board.append(station_yield(cur, cls, days, with_machines=False, win=win))
        except Exception as e:
            skipped.append({"cls": cls, "reason": f"{type(e).__name__}: {str(e)[:200]}"})
            try:
                conn.close()
            except Exception:
                pass
            try:                                       # 重連：單站逾時恐毒化連線→避免其餘全跳
                conn, cur = mt._connect()
            except Exception:
                pass
    try:
        conn.close()
    except Exception:
        pass
    return now, board, skipped


# ---- 呈現 -------------------------------------------------------------------
def _p(x, w=6):
    return (f"{x:>{w}.1f}%" if isinstance(x, (int, float)) else f"{'  n/a':>{w+1}}")


def render_station(rec, now, days):
    print(f"=== §26 產出/良率（RAW v0）　{rec['cls']}　MES now={now}　近{days}天　"
          f"機台級 {rec.get('fleet_size')} 台 ===")
    print(f"  ★ 口徑＝{CALIBER}（未為重測/多試校正；真實不良待製程 SME 定 §26.5 O1–O5）")
    bt = "　[batch 站 qtyMax>1：COUNT(DISTINCT) 會低估，§26.5]" if rec["is_batch"] else ""
    print(f"\n  ── OUTPUT 軸（COUNT DISTINCT lot；非 Σqty）──{bt}")
    print(f"    產出單位 {rec['output_units']}　good(任一 pass) {rec['good_units']}　段 {rec['n_seg']}")
    if rec["products"]:
        ps = "　".join(f"{k}={v}" for k, v in sorted(rec["products"].items(), key=lambda x: -x[1]))
        print(f"    產品別(distinct lot)：{ps}　（三線合 {rec['product_units']}；"
              f"unattributed {rec['unattributed_units']} 不計入產品）")
    print(f"\n  ── YIELD 軸（RAW v0）──　fpy_source={rec['fpy_source']}　yield_signal={rec['has_yield_signal']}")
    print(f"    fail%(raw, qFail/qProc) {_p(rec['fail_pct_raw'])}　rs_fail%(判退段) {_p(rec['rs_fail_pct'])}"
          f"　(qFail={rec['qty_fail']:.0f} rsFail={rec['rs_fail']})")
    print(f"    FPY(首試) {_p(rec['fpy_raw_pct'])}　right-first-time {_p(rec['rft_pct'])}"
          f"　final(末試) {_p(rec['final_pass_pct'])}　rework(真) {_p(rec['rework_pct'])}"
          f"　retest(良性) {_p(rec['retest_pct'])}　(fpy_units={rec['fpy_units']})")
    if rec.get("machines") is not None:
        print(f"\n  ── 機台級下鑽（主單位；各自 DISTINCT，**勿相加得站**）──")
        print(f"    {'機台':<12}{'產出':>6}{'good':>6}{'段':>6}{'fail%raw':>10}")
        print("    " + "-" * 44)
        for m in rec["machines"]:
            print(f"    {m['eid']:<12}{m['output_units']:>6}{m['good_units']:>6}{m['n_seg']:>6}"
                  f"{_p(m['fail_pct_raw'], 9)}")
        ov = output_overlap(rec["output_units"], [m["output_units"] for m in rec["machines"]])
        print(f"    （站 OUTPUT={ov['station']}　Σ機台={ov['sum_machines']}　"
              f"重疊(同件跨機台)={ov['overlap']}　→ 站為獨立 DISTINCT、非 Σ機台）")


def render_scoreboard(now, board, skipped, days):
    print(f"=== §26 全線產出/良率計分板（RAW v0）　MES now={now}　近{days}天　"
          f"站 {len(board)}（跳過 {len(skipped)}）===")
    print(f"  ★ 口徑＝{CALIBER}")
    print(f"\n  {'站(segmentClass)':<42}{'產出':>7}{'good':>7}{'fail%raw':>10}{'FPY首試':>9}"
          f"{'final':>8}{'rework':>8}")
    print("  " + "-" * 91)
    for s in sorted(board, key=lambda x: -x["output_units"]):
        heron = "＋heron" if s["products"].get("heron") else ""
        print(f"  {s['cls']:<42}{s['output_units']:>7}{s['good_units']:>7}{_p(s['fail_pct_raw'], 9)}"
              f"{_p(s['fpy_raw_pct'], 8)}{_p(s['final_pass_pct'], 7)}{_p(s['rework_pct'], 7)}{heron}")
    if skipped:
        print(f"\n  跳過 {len(skipped)} 站：")
        for sk in skipped:
            print(f"    {sk['cls']}: {sk['reason']}")
    print(f"\n  OUTPUT=COUNT(DISTINCT lot)（非 Σqty、非 Σ子層）；YIELD 全 RAW（§26.5 未校正）。"
          f"FPY 空＝該站 fact_segment 無覆蓋（誠實留白）。")


# ---- CLI -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Tendline Board §26 產出/良率軸（RAW v0，唯讀，凍結）")
    ap.add_argument("--station", default=CAL_CLASS, help="單站 segmentClass（預設 CAL；含機台下鑽）")
    ap.add_argument("--all-stations", action="store_true",
                    help="跑 curated EQP_STATIONS 全線計分板（站級、短窗友善，給儀表板）")
    ap.add_argument("--days", type=int, default=4, help="回看天數（低頻站建議拉大；--all 用短窗）")
    ap.add_argument("--json", action="store_true", help="輸出 canonical JSON")
    ap.add_argument("--selftest", action="store_true", help="離線確定性數學單測（不連 MES）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return

    if args.all_stations:
        try:
            now, board, skipped = scoreboard_all_stations(days=args.days)
        except Exception as e:
            print(f"[mes_yield] 全線計分板失敗: {type(e).__name__}{_hint(e)}\n  {str(e)[:300]}")
            sys.exit(2)
        if args.json:
            import json
            print(json.dumps({"mes_now": str(now), "days": args.days, "caliber": CALIBER,
                              "stations": board, "skipped": skipped},
                             ensure_ascii=False, default=str))
        else:
            render_scoreboard(now, board, skipped, args.days)
        return

    conn, cur = mt._connect()
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        rec = station_yield(cur, args.station, args.days, with_machines=True)
    except Exception as e:
        print(f"[mes_yield] 查詢失敗: {type(e).__name__}{_hint(e)}\n  {str(e)[:300]}")
        conn.close()
        sys.exit(2)
    conn.close()
    if args.json:
        import json
        print(json.dumps({"mes_now": str(now), "days": args.days, "caliber": CALIBER,
                          "station": rec}, ensure_ascii=False, default=str))
        return
    render_station(rec, now, args.days)


def _hint(e):
    low = str(e).lower()
    if any(k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")):
        return " → 連不到 MES。確認已連 VPN/內網。"
    if "login failed" in low or "28000" in str(e):
        return " → 登入被拒。確認 AD 帳號對 MES_DB 有讀權。"
    return ""


# ---- 離線單測（確定性數學；不連 MES，§15 harness 把關） --------------------------
def _selftest():
    """驗 fpy/fail 百分比數學 + OUTPUT『勿相加子層』不變式（合成計數，不連 MES）。"""
    print("=== mes_yield --selftest（RAW v0 確定性數學）===")
    ok = True

    # 1. fail_metrics：rawFail=18.5%（仿 AutoOQC raw 18.5% 例）。
    fm = fail_metrics(qty_proc=1000.0, qty_fail=185.0, rs_pass=760, rs_fail=240)
    exp_fm = {"fail_pct_raw": 18.5, "rs_fail_pct": 24.0}
    got_fm = {"fail_pct_raw": round(fm["fail_pct_raw"], 4), "rs_fail_pct": round(fm["rs_fail_pct"], 4)}
    ok &= got_fm == exp_fm
    print(f"  fail_metrics  期望 {exp_fm}\n                實得 {got_fm}  {'OK' if got_fm==exp_fm else 'FAIL'}")

    # 2. fpy_metrics：units=100, att1_pass=83, last=97, retested=40(=12 benign+28 rework)。
    #    fpy 首試 83%；rft=(83-12)=71%；final 97%；rework 28%；retest 12%。
    fp = fpy_metrics({"units": 100, "att1_pass": 83, "last_pass": 97, "retested": 40,
                      "passed_but_retested": 12, "failed_then_retried": 28})
    exp_fp = {"fpy_raw_pct": 83.0, "rft_pct": 71.0, "final_pass_pct": 97.0,
              "rework_pct": 28.0, "retest_pct": 12.0}
    got_fp = {k: round(fp[k], 4) for k in exp_fp}
    ok &= got_fp == exp_fp
    print(f"  fpy_metrics   期望 {exp_fp}\n                實得 {got_fp}  {'OK' if got_fp==exp_fp else 'FAIL'}")

    # 3. fpy=None → 全 None（誠實留白，fact_segment 無覆蓋站）。
    none_ok = all(v is None for v in fpy_metrics(None).values())
    ok &= none_ok
    print(f"  fpy None→全 None  {'OK' if none_ok else 'FAIL'}")

    # 4. OUTPUT『勿相加子層』不變式：站 distinct=1750，Σ機台=2395（同件跨機台重測 +645/37%）。
    #    模組**永遠回報站級獨立 DISTINCT（1750）**，絕不用 Σ機台。
    ov = output_overlap(1750, [800, 700, 500, 395])
    exp_ov = {"station": 1750, "sum_machines": 2395, "overlap": 645}
    ok &= ov == exp_ov
    print(f"  output_overlap 期望 {exp_ov}\n                 實得 {ov}  {'OK' if ov==exp_ov else 'FAIL'}")

    # 5. safe_pct 邊界：分母 0 → 0.0（不除零）。
    z_ok = (safe_pct(5, 0) == 0.0 and safe_pct(0, 0) == 0.0 and safe_pct(3, 4) == 75.0)
    ok &= z_ok
    print(f"  safe_pct 邊界(分母0→0)  {'OK' if z_ok else 'FAIL'}")

    # 6. build_station_record：batch 旗標 + 產品總和（不含 unattributed）+ raw label。
    raw = {"pscpk": 2, "units": 1750, "good_units": 1600, "n_seg": 7483, "qty_proc": 7483.0,
           "qty_max": 1.0, "qty_fail": 586.0, "rs_pass": 6897, "rs_fail": 586}
    rec = build_station_record("segCalibrationTest", raw,
                               {"plover": 1500, "heron": 200, "kestrel": 50, UNATTRIBUTED: 30},
                               None)
    rec_ok = (rec["output_units"] == 1750 and rec["product_units"] == 1750
              and rec["unattributed_units"] == 30 and rec["is_batch"] is False
              and rec["fpy_raw_pct"] is None and rec["caliber"] == CALIBER)
    ok &= rec_ok
    print(f"  build_station_record（產品合={rec['product_units']} batch={rec['is_batch']} "
          f"fpy=None caliber 標記）  {'OK' if rec_ok else 'FAIL'}")

    # 7. _agg_machine_yield（拆查詢 Python 聚合＝等價原 SQL JOIN-GROUP BY；含 fan-out + DISTINCT + 漏對映丟棄）。
    #    stpPK 5 同段 fan-out 到 A,B（模組/父機共記）；stpPK 6 無對映(不在 fleet)→丟。
    pk_eids = {1: ["A"], 2: ["A"], 3: ["A"], 4: ["B"], 5: ["A", "B"]}
    seg_rows = [(1, "L1", 1, 1.0, 0.0), (2, "L1", 0, 1.0, 1.0), (3, "L2", 1, 1.0, 0.0),
                (4, "L3", 1, 1.0, 0.0), (5, "L4", 1, 1.0, 0.0), (6, "LX", 1, 1.0, 0.0)]
    my_agg = _agg_machine_yield(pk_eids, seg_rows)
    exp_my = {  # A: 段{1,2,3,5}=4、lot{L1,L2,L4}=3、good{L1,L2,L4}=3、qproc4 qfail1 rs(3pass/1fail)
        "A": {"units": 3, "good_units": 3, "n_seg": 4, "qty_proc": 4.0, "qty_fail": 1.0,
              "rs_pass": 3, "rs_fail": 1},
        "B": {"units": 2, "good_units": 2, "n_seg": 2, "qty_proc": 2.0, "qty_fail": 0.0,
              "rs_pass": 2, "rs_fail": 0}}
    my_ok = (my_agg == exp_my)
    ok &= my_ok
    print(f"  _agg_machine_yield（fan-out+DISTINCT+漏對映丟）期望 {exp_my}\n"
          f"                     實得 {my_agg}  {'OK' if my_ok else 'FAIL'}")

    print(f"\n  → {'PASS ✅' if ok else 'FAIL ❌'}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
