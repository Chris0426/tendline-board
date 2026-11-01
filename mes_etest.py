#!/usr/bin/env python3
"""Tendline Board — Hermes 的**失效碼排行／die 級電測**記錄級 MES 查詢核心（MES 工具包②·包 3；ROADMAP #176）。

**這是什麼**：兩支唯讀工具的資料腿（`board_mcp` 消費；runtime **沒有 LLM 參與**——LLM 只叫工具，
工具跑**固定審過的參數化 SQL**，`SPEC-HERMES-TRUST.md` §7.6.1）：
  · `station_failure_pareto(cls, days)` ——某站近 N 天 FAIL 段的失效碼排行
                                          （`board.station_failure_pareto.v1`；探針 F0／F0s／F1／F3／F4）。
  · `module_die_etest(serial)`       ——一顆 module 的打線後電測（AET）：PH 級 2 變數＋
                                          11 個 die 級各 17 變數（`board.module_die_etest.v1`；D0–D5）。

**不是什麼**：不是良率（`mes_qlik`／`mes_yield`）、不是站別變數字典（`mes_hermes` 的 `station_variables`
只列「有哪些變數」、不數它們）、不是序號履歷（`mes_material.serial_genealogy`）。規格上下限**不在**本檔。

**唯一的 SQL 來源＝`_PROBE-ASSISTANT-MES-FAMILY-3-20260924.sql`**（2026-09-24 12:33 Chris SSMS 實跑、
19 結果集零錯誤；判讀＝`_PROBE-ASSISTANT-MES-FAMILY-3-RESULT-DIGEST-20260924.md` 含第四支追記）。
每條語句**參數化後沿用**該步的欄位／join；偏離處逐條寫在下表與該常數上方，**零新增欄出處**
（所有欄都在探針某一步 SELECT 過）。

═══ 對照探針的刻意偏離（理由寫在各常數上方）═══════════════════════════════════════════
  E1 **臨時表一律在非參數化批次建空殼、帶參數的語句只准 `INSERT INTO #x`**（`mes_hermes`
     2026-09-21 live 首跑事故：pyodbc 帶參數＝`sp_executesql`，裡面 `SELECT … INTO #t` 建的表隨那次
     呼叫消失；`mes_material` D1 同一條）。空殼一律 `WHERE 1=0`、欄位與來源逐字同對應步驟。
  E2 **四站 → 單站**：探針 `#fseg` 以 `IN (@f1..@f4)` 一次抓四站、`cls` 欄分組；工具一次只查一站
     （`= ?`）⇒ `#ep_fseg` 刪 `cls`／`matPK` 兩欄（工具用不到＝收窄），F3／F4 的 `cls` 分組與
     `PARTITION BY cls` 隨之拿掉（單站時等價）。
  E3 **F0 字典拿掉 `TOP (@topn)`**（digest 異常 1：`TOP 200` 把 AET 截掉 6 個、OQCT 截掉全部 37 個）；
     只取 `vdfProductionDataID`／`FailureDescription` 兩欄（收窄）。失效碼相關查詢**全部零 `TOP`**（selftest 釘）。
  E4 **F4 排序鍵改「FAIL 段數」優先**（Chris／DIGEST 規格：排行以帶該碼的 FAIL 段數計；探針以全部段數
     排——F3 證 PASS 段 0% 帶碼，兩者在探針窗內等價），並補 `vdfProductionDataID` 當同分決勝
     （探針 CLN Channel0–4 同分 52＝順序不定）；`rk <= 25` 改參數 `rk <= ?`（仍 25）。
  E5 **F1＋F3 併一條**（`SQL_FP_COVER`）：F3 已含 F1 的每結果段數（`n_seg`），單站時 F1 是 F3 的子集。
  E6 **族 D 的窗換成序號**（同 `mes_material` D8 的形）：`#dph` 的 `stpStartTime` 窗換成
     `MTL.matMaterialLotID = ?`（等號、不走 LIKE），`TOP (?)` 上限 `PH_CAP`；`#ddie` 保留探針的
     「前後各放寬 1 天」，錨點由整體窗換成**最新一次 PH 電測段的開始時間**，並以
     `stpRSID IN (SELECT … FROM #ep_dph WHERE stpPK = ?)` 半連接收斂到那一次（D1 的 RSID 相連形）。
  E7 **substrate 不在 SQL 算**：探針 D2 的 `LEFT(die_lot, 16)` **不採**（digest 異常 3：`…_R26_D11` 截成 `…_D1`）；
     Python 端 `substrate_of()` 從 `_R<nn>_D<n>` 前切，並自揭「推論待證」。selftest 負斷言全文零 `LEFT(`。
  E8 **D2 位置子查詢補 `ORDER BY PSM.stpPK DESC`**（探針 `TOP 1` 無排序＝重工時不定；取最近一次 Die Placement）。
  E9 **D3／D3b 由彙總改逐列**（工具要每顆 die 的值、不是全域 min/max）：欄＝D3 的
     `vdfProductionDataID`／`FailureDescription`／`vdfUOM`＋D4 的 `sgrReal`，外加內部鍵 `stpPK`（不出輸出）；
     D4 的四變數 `IN (...)` 過濾拿掉＝17 變數全取。
  E10 **D5 由彙總改逐列、且 PH 段與 die 段都查**（`UNION ALL`＝F5 的形；`lvl` 標層級）。
  E11 **不查族 V**（`oqc_bins` 放棄：第四支探針 `segUnitPostOQCBinning` 30 天零段、
      `varOQCT_BinName` 只「Bin 3A」）⇒ OQC FAIL 內容由本檔的失效碼排行回答。

═══ 欄位出處（[[ssms-sql-column-provenance]]；探針檔頭 [s07]／[s15d]／[s01450]／[app]）════════════
  | 物件 | 本檔取的欄 | 探針步驟 |
  |---|---|---|
  | `dbo.process_segments_t` | `stpPK`／`matPK`／`stpStartTime`／`stpTestResult`／`stpRSID` | `#fseg`／`#dph`／`#ddie` |
  | `dbo.process_segment_definition_t`×`process_segment_class_t` | `sgdPK`／`sgcPK`／`SegmentClassID` | 同上 |
  | `dbo.materials_t`×`material_definition_t`×`material_class_t` | `matPK`／`matMaterialLotID`／`mdfPK`／`mclPK`／`mclMaterialClassID` | `#dph`／`#ddie` |
  | `dbo.segment_integer_data_t` | `stpPK`／`vdfPK`／`sgiInteger` | `#fmfc`／D5 |
  | `dbo.segment_real_data_t` | `stpPK`／`vdfPK`／`sgrReal` | D3／D3b／D4 |
  | `dbo.segment_materials_t` | `stpPK`／`matPK`／`sgmAssemblyPosition` | D2 |
  | `dbo.production_data_definition_t` | `vdfPK`／`vdfProductionDataID`／`FailureDescription`／`vdfClassID`／`vdfUOM` | F0／`#fmfc`／D3 |
  | `dbo.segment_class_variable` | `SegmentClassId`／`VariableId` | F0／F0s |

  **絕對不 SELECT 的欄**（`ALL_SQL()` 全文負斷言）：人名（`stpPersonID`／`OperatorId`／`OperatorName`／
  `aslPersonID`／`rqsRequestor`）、任何 `Comment`、EAV 大 view（`all_segment_data_value_v`）、`LEFT(`。

★ perf 鐵則（沿用 `mes_hermes`／`mes_material`）：打 `dbo.*` 的每條 SELECT／INSERT 以 `OPTION (RECOMPILE)`
  收尾；碰 `process_segments_t` 的每條都有 `stpStartTime >= ?` 窗或序號等號收斂；**刻意不濾 `stpEndTime`**
  （[[mes-zero-length-segments]]：日後要濾一律 `>=`、絕不 `>`）。
★ §24：零人名欄。★ 唯讀：唯一 DDL/DML 在 `#ep_*`（tempdb）。★ 凍結模組（`mes_timeline`／`mes_yield`）零 import。

CLI:
    python mes_etest.py --selftest         # 純離線（讀 _fixtures/ 兩份 golden ＋ 合成列），零 MES
    python mes_etest.py --sql              # 只**印**本模組會送出的全部 SQL（不執行）
    python mes_etest.py --build-fixtures   # 由探針 TSV 重建兩份 fixture（純離線讀檔；fixture 不可手改）
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import mes_material as _mm                                 # 純常數／純函式重用（連線、_s/_num/_ts、normalize_code、_dump）
from mes_material import SchemaDivergence                  # noqa: F401  （連線單一出處＝mes_qlik，經 mes_material）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # pragma: no cover
    pass

BASE = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# 1. 人工審定常數
# =============================================================================
SCHEMA_PARETO = "board.station_failure_pareto.v1"
SCHEMA_ETEST = "board.module_die_etest.v1"

TOOLS = ("station_failure_pareto", "module_die_etest")

QUERY_TIMEOUT_SEC = 180

#: ★ 探針證過的四站（F3：FAIL 段 100% 帶 >0 失效碼、PASS 0%）。其餘站 ⇒ `station_unsupported`（不查 MES）。
SUPPORTED_STATIONS = {
    "segCalibrationTest": "CAL",
    "segCleanTest": "CLN",
    "segAuxElectricalTest": "AET",
    "segFinalOQC": "OQCT",
}
#: 短名別名（大小寫不拘）→ 全名。
STATION_ALIASES = {"CAL": "segCalibrationTest", "CLN": "segCleanTest",
                   "AET": "segAuxElectricalTest", "OQCT": "segFinalOQC",
                   "OQC": "segFinalOQC"}
#: 碼值型態（F2）：CAL／OQCT 純 0/1 旗標；CLN／AET 混計數（max 11）。
COUNT_TYPE_STATIONS = ("segCleanTest", "segAuxElectricalTest")
#: 站類別 id 形（`SegmentClassID varchar(75)`）。
CLASS_RE = re.compile(r"^seg[A-Za-z0-9_]{1,72}$")

#: 排行上限（探針 F4 `rk <= 25`；回傳標 `truncated`）。
RANK_CAP = 25
#: 窗（天）：預設＝探針族 F 的 7 天；上限 31（超界拒絕、不 clamp）。
PARETO_DAYS_DEFAULT = 7
PARETO_DAYS_MAX = 31

#: 族 D。
AET_CLASS = "segAuxElectricalTest"
PH_MLC = "assyModule"
DIE_MLC = "sbsDie"
#: 一顆序號的 AET PH 段上限（重測次數；D0 證 RSID 在 PH 段上唯一）。
PH_CAP = 50
#: 一次電測的 die 段上限（D1 證恆為 11；留餘裕抓異常）。
DIE_CAP = 50
DIES_EXPECTED = 11
#: die 段相對 PH 段的時間放寬（探針 `#ddie` 前後各 1 天）。
DIE_WINDOW_DAYS = 1
#: 同名跨層變數（D3 vs D3b：量值尺度不同、**不得混比**）。
CROSS_LEVEL_SAME_NAME = ("varAET_Iddq", "varAET_Iposq")
#: D3：覆蓋率極低的選用變數（多數 die 沒有這一列＝沒寫、不是 0）。
SPARSE_DIE_VARS = ("varAET_bad_actuators",)
#: die 批號形 `<substrate>_R<nn>_D<n>`（digest 異常 3；substrate 取 `_R` 前＝推論待證）。
DIE_LOT_RE = re.compile(r"^(?P<substrate>.+?)_R(?P<r>\d+)_D(?P<d>\d+)$")

#: 回傳鍵集合（**查不到也長同一個形**；validator 與 smoke 以此為準）。
PARETO_KEYS = ("station", "station_short", "found", "window", "n_segments", "n_fail", "n_pass",
               "n_other", "fail_coverage", "ranking", "n_codes_ranked", "n_codes_registered",
               "not_measurable", "truncated", "caveats", "reason", "unavailable_reason", "diag")
ETEST_KEYS = ("serial", "found", "station", "module_level", "n_attempts", "die_level",
              "not_measurable", "truncated", "caveats", "reason", "unavailable_reason", "diag")
KEYS_BY_TOOL = {"station_failure_pareto": PARETO_KEYS, "module_die_etest": ETEST_KEYS}
RANK_ROW_KEYS = ("rank", "code", "label", "n_fail_seg", "share_of_fail", "n_seg", "sum_value",
                 "value_kind")
DIE_LEVEL_KEYS = ("for_attempt_started_at", "n_dies", "n_dies_expected", "n_pass", "n_fail",
                  "variables", "dies")
DIE_ROW_KEYS = ("position", "die_lot", "substrate", "started_at", "result", "values",
                "failure_codes")

#: 「MES 裡真的沒有」的閉集（≠ `unavailable_reason`＝我們取不到）。
REASONS = ("no_segments_in_window",)
#: `not_measurable` 理由閉集。
NOT_MEASURABLE_REASONS = ("station_unsupported", "serial_not_found", "die_count_mismatch")

_TS_FMT = "%Y-%m-%d %H:%M:%S"

# =============================================================================
# 2. I/O 層：SQL 文本（全部模組常數＝離線可斷言；唯讀）
# =============================================================================
#: G0 物件／欄守衛。缺 ⇒ `SchemaDivergence`（停下、不回全零板）。每支工具只看自己要的那幾格。
SQL_GUARD = (
    "SELECT CASE WHEN OBJECT_ID('dbo.process_segments_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.process_segment_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.process_segment_class_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_integer_data_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_real_data_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_materials_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.production_data_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_class_variable') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.materials_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_class_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.production_data_definition_t','vdfClassID')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.process_segments_t','stpRSID')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.segment_materials_t','sgmAssemblyPosition')"
    " IS NULL THEN 0 ELSE 1 END\n"
    "OPTION (RECOMPILE)")
GUARD_KEYS = ("tbx_stp", "tbx_sgd", "tbx_sgc", "tbx_int", "tbx_real", "tbx_sgm", "tbx_vdf",
              "tbx_scv", "tbx_mlt", "tbx_mdf", "tbx_mcl", "vdfClassID", "stpRSID",
              "sgmAssemblyPosition")
_SEG3 = ("tbx_stp", "tbx_sgd", "tbx_sgc")
GUARD_NEEDS = {
    "station_failure_pareto": _SEG3 + ("tbx_int", "tbx_vdf", "tbx_scv", "vdfClassID"),
    "module_die_etest": _SEG3 + ("tbx_int", "tbx_real", "tbx_sgm", "tbx_vdf", "tbx_mlt",
                                    "tbx_mdf", "tbx_mcl", "vdfClassID", "stpRSID",
                                    "sgmAssemblyPosition"),
}

# ── station_failure_pareto（#fseg → #fmfc → #fflag → F3 → F4；字典 F0）──────────────────
#: 空殼批次（E1）。`#ep_fseg`＝探針 `#fseg`（:101-109）的欄位子集（E2 刪 `cls`／`matPK`）；
#: `#ep_fmfc`＝`#fmfc`（:117-123）刪 `cls`；`#ep_fflag`＝`#fflag`（:137-138）逐字。
SQL_FP_TEMPS = (
    "IF OBJECT_ID('tempdb..#ep_fflag') IS NOT NULL DROP TABLE #ep_fflag;\n"
    "IF OBJECT_ID('tempdb..#ep_fmfc')  IS NOT NULL DROP TABLE #ep_fmfc;\n"
    "IF OBJECT_ID('tempdb..#ep_fseg')  IS NOT NULL DROP TABLE #ep_fseg;\n"
    "SELECT PSG.stpPK, PSG.stpStartTime, PSG.stpTestResult\n"
    "INTO #ep_fseg\n"
    "FROM dbo.process_segments_t AS PSG\n"
    "JOIN dbo.process_segment_definition_t AS PSD ON PSD.sgdPK = PSG.sgdPK\n"
    "JOIN dbo.process_segment_class_t      AS PSC ON PSC.sgcPK = PSD.sgcPK\n"
    "WHERE 1=0;\n"
    "SELECT s.stpPK, s.stpTestResult, p.vdfProductionDataID, p.FailureDescription, si.sgiInteger\n"
    "INTO #ep_fmfc\n"
    "FROM #ep_fseg s\n"
    "JOIN dbo.segment_integer_data_t       si ON si.stpPK = s.stpPK\n"
    "JOIN dbo.production_data_definition_t p  ON p.vdfPK  = si.vdfPK\n"
    "WHERE 1=0;\n"
    "SELECT stpPK, any_pos = MAX(CASE WHEN sgiInteger > 0 THEN 1 ELSE 0 END)\n"
    "INTO #ep_fflag FROM #ep_fmfc WHERE 1=0 GROUP BY stpPK;")

#: F0 字典（探針 :80-88；E3 拿掉 `TOP`、單站 `= ?`、欄收窄成兩欄）。
SQL_FP_DICT = (
    "SELECT p.vdfProductionDataID, p.FailureDescription\n"
    "FROM dbo.segment_class_variable v\n"
    "JOIN dbo.process_segment_class_t      c ON c.sgcPK = v.SegmentClassId\n"
    "JOIN dbo.production_data_definition_t p ON p.vdfPK = v.VariableId\n"
    "WHERE c.SegmentClassID = ?\n"
    "  AND p.vdfClassID = 'fcdMaterialFailureCode'\n"
    "ORDER BY p.vdfProductionDataID\n"
    "OPTION (RECOMPILE)")

#: `#fseg`（探針 :101-109；E2 單站；起點過濾 `>=`／`<`、**不碰 stpEndTime**）。
SQL_FP_SEG = (
    "INSERT INTO #ep_fseg (stpPK, stpStartTime, stpTestResult)\n"
    "SELECT PSG.stpPK, PSG.stpStartTime, PSG.stpTestResult\n"
    "FROM dbo.process_segments_t AS PSG\n"
    "JOIN dbo.process_segment_definition_t AS PSD ON PSD.sgdPK = PSG.sgdPK\n"
    "JOIN dbo.process_segment_class_t      AS PSC ON PSC.sgcPK = PSD.sgcPK\n"
    "WHERE PSC.SegmentClassID = ?\n"
    "  AND PSG.stpStartTime >= ? AND PSG.stpStartTime < ?\n"
    "OPTION (RECOMPILE)")
#: 探針 :110 逐字（表名換 `#ep_`）。
SQL_FP_SEG_INDEX = "CREATE CLUSTERED INDEX ix_ep_fseg ON #ep_fseg(stpPK)"

#: `#fmfc`（探針 :117-123；`vdfClassID` 過濾、不靠名字前綴；非參數化）。
SQL_FP_MFL = (
    "INSERT INTO #ep_fmfc (stpPK, stpTestResult, vdfProductionDataID, FailureDescription, sgiInteger)\n"
    "SELECT s.stpPK, s.stpTestResult, p.vdfProductionDataID, p.FailureDescription, si.sgiInteger\n"
    "FROM #ep_fseg s\n"
    "JOIN dbo.segment_integer_data_t       si ON si.stpPK = s.stpPK\n"
    "JOIN dbo.production_data_definition_t p  ON p.vdfPK  = si.vdfPK\n"
    "WHERE p.vdfClassID = 'fcdMaterialFailureCode'\n"
    "OPTION (RECOMPILE)")

#: `#fflag`（探針 :137-138：**先逐段算旗標再 join**＝聚合函式內不放子查詢，Msg 130）。
SQL_FP_FLAG = (
    "INSERT INTO #ep_fflag (stpPK, any_pos)\n"
    "SELECT stpPK, any_pos = MAX(CASE WHEN sgiInteger > 0 THEN 1 ELSE 0 END)\n"
    "FROM #ep_fmfc GROUP BY stpPK")

#: F1＋F3（探針 :139-144；E5 併一條、E2 拿掉 `cls` 分組）。
SQL_FP_COVER = (
    "SELECT s.stpTestResult,\n"
    "       n_seg             = COUNT(*),\n"
    "       n_seg_any_mfl_row = SUM(CASE WHEN f.stpPK IS NOT NULL THEN 1 ELSE 0 END),\n"
    "       n_seg_any_mfl_pos = SUM(CASE WHEN f.any_pos = 1 THEN 1 ELSE 0 END)\n"
    "FROM #ep_fseg s LEFT JOIN #ep_fflag f ON f.stpPK = s.stpPK\n"
    "GROUP BY s.stpTestResult ORDER BY n_seg DESC")

#: F4 排行（探針 :147-156；E2 拿掉 `PARTITION BY cls`、E4 排序鍵＝FAIL 段數＋碼名決勝、`rk <= ?`）。
SQL_FP_RANK = (
    "SELECT vdfProductionDataID, FailureDescription, n_seg_pos, n_fail_seg_pos, sum_value, rk\n"
    "FROM (\n"
    "  SELECT vdfProductionDataID, FailureDescription,\n"
    "         n_seg_pos      = COUNT(DISTINCT stpPK),\n"
    "         n_fail_seg_pos = COUNT(DISTINCT CASE WHEN stpTestResult = 'FAIL' THEN stpPK END),\n"
    "         sum_value      = SUM(CAST(sgiInteger AS bigint)),\n"
    "         rk = ROW_NUMBER() OVER (ORDER BY\n"
    "                COUNT(DISTINCT CASE WHEN stpTestResult = 'FAIL' THEN stpPK END) DESC,\n"
    "                COUNT(DISTINCT stpPK) DESC, vdfProductionDataID)\n"
    "  FROM #ep_fmfc WHERE sgiInteger > 0\n"
    "  GROUP BY vdfProductionDataID, FailureDescription\n"
    ") x WHERE rk <= ? ORDER BY rk")

# ── module_die_etest（#dph → #ddie → D2 位置 → D3b／D3 值 → D5 失效碼）────────────────
_D_JOIN = (
    "FROM dbo.materials_t AS MTL\n"
    "JOIN dbo.process_segments_t          AS PSG ON PSG.matPK = MTL.matPK\n"
    "JOIN dbo.process_segment_definition_t AS PSD ON PSG.sgdPK = PSD.sgdPK\n"
    "JOIN dbo.process_segment_class_t      AS PSC ON PSC.sgcPK = PSD.sgcPK\n"
    "JOIN dbo.material_definition_t       AS MLD ON MLD.mdfPK = MTL.mdfPK\n"
    "JOIN dbo.material_class_t            AS MLC ON MLC.mclPK = MLD.mclPK\n")
_D_COLS = ("SELECT PSG.stpPK, PSG.matPK, MTL.matMaterialLotID, PSG.stpRSID, PSG.stpStartTime,"
           " PSG.stpTestResult\n")
#: 空殼批次（E1）。`#ep_dph`／`#ep_ddie`＝探針 `#dph`（:174-185）／`#ddie`（:188-199）欄位與 join 逐字。
SQL_DE_TEMPS = (
    "IF OBJECT_ID('tempdb..#ep_ddie') IS NOT NULL DROP TABLE #ep_ddie;\n"
    "IF OBJECT_ID('tempdb..#ep_dph')  IS NOT NULL DROP TABLE #ep_dph;\n"
    + _D_COLS + "INTO #ep_dph\n" + _D_JOIN + "WHERE 1=0;\n"
    + _D_COLS + "INTO #ep_ddie\n" + _D_JOIN + "WHERE 1=0;")

_D_INS = "(stpPK, matPK, matMaterialLotID, stpRSID, stpStartTime, stpTestResult)\n"
#: `#dph`（探針 :174-185；E6 窗換成序號等號、`TOP (?)`＝`PH_CAP`、最新在前）。
SQL_DE_PH = (
    "INSERT INTO #ep_dph " + _D_INS +
    "SELECT TOP (?) PSG.stpPK, PSG.matPK, MTL.matMaterialLotID, PSG.stpRSID, PSG.stpStartTime,"
    " PSG.stpTestResult\n" + _D_JOIN +
    "WHERE PSC.SegmentClassID = 'segAuxElectricalTest'\n"
    "  AND MLC.mclMaterialClassID = 'assyModule'\n"
    "  AND MTL.matMaterialLotID = ?\n"
    "ORDER BY PSG.stpStartTime DESC, PSG.stpPK DESC\n"
    "OPTION (RECOMPILE)")
SQL_DE_PH_READ = (
    "SELECT stpPK, matMaterialLotID, stpStartTime, stpTestResult\n"
    "FROM #ep_dph ORDER BY stpStartTime DESC, stpPK DESC")

#: `#ddie`（探針 :188-199；E6 窗錨在最新 PH 段 ±1 天、RSID 半連接收斂到那一次；`TOP (?)`＝`DIE_CAP`）。
SQL_DE_DIE = (
    "INSERT INTO #ep_ddie " + _D_INS +
    "SELECT TOP (?) PSG.stpPK, PSG.matPK, MTL.matMaterialLotID, PSG.stpRSID, PSG.stpStartTime,"
    " PSG.stpTestResult\n" + _D_JOIN +
    "WHERE PSC.SegmentClassID = 'segAuxElectricalTest'\n"
    "  AND MLC.mclMaterialClassID = 'sbsDie'\n"
    "  AND PSG.stpStartTime >= ? AND PSG.stpStartTime < ?\n"
    "  AND PSG.stpRSID IN (SELECT p.stpRSID FROM #ep_dph p WHERE p.stpPK = ?)\n"
    "OPTION (RECOMPILE)")

#: D2（探針 :223-234）：die 段＋Die Placement 位置（相關子查詢逐字；E8 補 `ORDER BY`；E7 不算 substrate）。
SQL_DE_DIE_READ = (
    "SELECT d.stpPK, die_lot = d.matMaterialLotID, d.stpStartTime, d.stpTestResult,\n"
    "       assy_position = (SELECT TOP 1 PSM.sgmAssemblyPosition\n"
    "                        FROM dbo.segment_materials_t PSM\n"
    "                        JOIN dbo.process_segments_t          G2 ON G2.stpPK = PSM.stpPK\n"
    "                        JOIN dbo.process_segment_definition_t D2 ON D2.sgdPK = G2.sgdPK\n"
    "                        JOIN dbo.process_segment_class_t      C2 ON C2.sgcPK = D2.sgcPK\n"
    "                        WHERE PSM.matPK = d.matPK AND C2.SegmentClassID = 'segDiePlacement'\n"
    "                        ORDER BY PSM.stpPK DESC)\n"
    "FROM #ep_ddie d\n"
    "ORDER BY assy_position, d.stpPK\n"
    "OPTION (RECOMPILE)")

_REAL_COLS = "SELECT {a}.stpPK, vdf.vdfProductionDataID, vdf.FailureDescription, vdf.vdfUOM, r.sgrReal\n"
#: D3b 逐列（E9；PH 級＝全部 attempt）。
SQL_DE_PH_REAL = (
    _REAL_COLS.format(a="p") +
    "FROM #ep_dph p\n"
    "JOIN dbo.segment_real_data_t          r   ON r.stpPK = p.stpPK\n"
    "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = r.vdfPK\n"
    "ORDER BY p.stpPK, vdf.vdfProductionDataID\n"
    "OPTION (RECOMPILE)")
#: D3／D4 逐列（E9；die 級＝最新一次電測的 die 段、17 變數全取）。
SQL_DE_DIE_REAL = (
    _REAL_COLS.format(a="d") +
    "FROM #ep_ddie d\n"
    "JOIN dbo.segment_real_data_t          r   ON r.stpPK = d.stpPK\n"
    "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = r.vdfPK\n"
    "ORDER BY d.stpPK, vdf.vdfProductionDataID\n"
    "OPTION (RECOMPILE)")

#: D5 逐列（E10；PH 段＋die 段，`UNION ALL`＝F5 的形；die 碼與 AET 站碼表同源）。
SQL_DE_MFL = (
    "SELECT lvl = 'ph', p.stpPK, vdf.vdfProductionDataID, vdf.FailureDescription, si.sgiInteger\n"
    "FROM #ep_dph p\n"
    "JOIN dbo.segment_integer_data_t       si  ON si.stpPK = p.stpPK\n"
    "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = si.vdfPK\n"
    "WHERE vdf.vdfClassID = 'fcdMaterialFailureCode'\n"
    "UNION ALL\n"
    "SELECT lvl = 'die', d.stpPK, vdf.vdfProductionDataID, vdf.FailureDescription, si.sgiInteger\n"
    "FROM #ep_ddie d\n"
    "JOIN dbo.segment_integer_data_t       si  ON si.stpPK = d.stpPK\n"
    "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = si.vdfPK\n"
    "WHERE vdf.vdfClassID = 'fcdMaterialFailureCode'\n"
    "OPTION (RECOMPILE)")

#: 失效碼相關查詢（selftest：全文零 `TOP`＝digest 異常 1 的教訓）。
FAILURE_CODE_SQL_NAMES = ("SQL_FP_DICT", "SQL_FP_MFL", "SQL_FP_FLAG", "SQL_FP_COVER",
                          "SQL_FP_RANK", "SQL_DE_MFL")


def ALL_SQL():
    """本模組會送出的**全部 SQL**（selftest 負斷言的唯一清單；新增查詢必須登記在這裡）。"""
    return (SQL_GUARD,
            SQL_FP_TEMPS, SQL_FP_DICT, SQL_FP_SEG, SQL_FP_SEG_INDEX, SQL_FP_MFL, SQL_FP_FLAG,
            SQL_FP_COVER, SQL_FP_RANK,
            SQL_DE_TEMPS, SQL_DE_PH, SQL_DE_PH_READ, SQL_DE_DIE, SQL_DE_DIE_READ,
            SQL_DE_PH_REAL, SQL_DE_DIE_REAL, SQL_DE_MFL)


#: `board_mcp._TOOL_QUERIES` 用的真名（grep 得到）。
TOOL_QUERY_NAMES = {
    "station_failure_pareto": ("mes_etest.SQL_FP_DICT", "mes_etest.SQL_FP_SEG",
                               "mes_etest.SQL_FP_MFL", "mes_etest.SQL_FP_COVER",
                               "mes_etest.SQL_FP_RANK"),
    "module_die_etest": ("mes_etest.SQL_DE_PH", "mes_etest.SQL_DE_DIE",
                            "mes_etest.SQL_DE_DIE_READ", "mes_etest.SQL_DE_PH_REAL",
                            "mes_etest.SQL_DE_DIE_REAL", "mes_etest.SQL_DE_MFL"),
}


def open_connection(timeout=QUERY_TIMEOUT_SEC):
    """唯讀連線（`mes_material.open_connection`＝`mes_qlik._connect` 重用）。"""
    return _mm.open_connection(timeout=timeout)


error_detail = _mm.error_detail          # 降級形 `diag.error_detail` 的全 repo 單一出處

# =============================================================================
# 3. 純函式層（零 I/O；selftest 主戰場）
# =============================================================================
_s, _int, _num, _ts = _mm._s, _mm._int, _mm._num, _mm._ts


def _dt(v):
    return _mm._mh._dt(v)


def _fmt(v):
    return v.strftime(_TS_FMT) if isinstance(v, datetime) else v


def normalize_station(cls):
    """站 → `(全名, err)`。短名（CAL/CLN/AET/OQCT/OQC，大小寫不拘）或全名；形不對 ⇒ `bad_station`。
    **形對但不在四站**＝回全名、無 err（由 `precheck` 回 `station_unsupported`，不查 MES）。"""
    hint = "station: CAL / CLN / AET / OQCT (or segCalibrationTest …)"
    if not isinstance(cls, str) or not cls.strip():
        return None, {"error": "missing_station", "hint": hint}
    s = cls.strip()
    if s.upper() in STATION_ALIASES:
        return STATION_ALIASES[s.upper()], None
    if not CLASS_RE.match(s):
        return None, {"error": "bad_station", "given": cls, "hint": hint}
    for full in SUPPORTED_STATIONS:
        if full.lower() == s.lower():
            return full, None
    return s, None


def normalize_pareto_args(cls=None, days=None):
    """`station_failure_pareto` 參數 → `(key, err)`；`key={"cls", "days"}`。days 1..31（超界拒絕、不 clamp）。"""
    norm, err = normalize_station(cls)
    if err:
        return None, err
    d = PARETO_DAYS_DEFAULT if days in (None, "") else _int(days)
    if d is None or not (1 <= d <= PARETO_DAYS_MAX):
        return None, {"error": "bad_days", "given": days,
                      "hint": f"days must be an integer 1..{PARETO_DAYS_MAX} (refused, not clamped)"}
    return {"cls": norm, "days": d}, None


def normalize_serial(serial):
    """序號 → `(normalized, err)`（`mes_material.normalize_code` 單一出處：等號比對、白名單）。"""
    return _mm.normalize_code(serial, kind="serial")


def substrate_of(die_lot):
    """die 批號 `<substrate>_R<nn>_D<n>` → substrate（`_R` 前）。認不得回 None（不猜、**不用固定長度截**）。"""
    s = _s(die_lot)
    if not s:
        return None
    m = DIE_LOT_RE.match(s)
    return m.group("substrate") if m else None


# ---- 骨架（鍵集合固定；查不到也長同一個形）--------------------------------------
def blank(tool, key, *, found=False, reason=None, unavailable_reason=None, diag=None):
    d = dict(diag or {})
    if tool == "station_failure_pareto":
        k = key if isinstance(key, dict) else {"cls": key, "days": None}
        cls = k.get("cls")
        return {"station": cls, "station_short": SUPPORTED_STATIONS.get(cls), "found": found,
                "window": {"from": None, "to": None, "days": k.get("days")},
                "n_segments": None, "n_fail": None, "n_pass": None, "n_other": None,
                "fail_coverage": None, "ranking": [], "n_codes_ranked": None,
                "n_codes_registered": None, "not_measurable": [], "truncated": False,
                "caveats": [], "reason": reason, "unavailable_reason": unavailable_reason,
                "diag": d}
    if tool == "module_die_etest":
        return {"serial": key, "found": found, "station": AET_CLASS, "module_level": [],
                "n_attempts": None, "die_level": None, "not_measurable": [],
                "truncated": False, "caveats": [], "reason": reason,
                "unavailable_reason": unavailable_reason, "diag": d}
    raise KeyError(tool)


_NM_UNSUPPORTED = ("本工具只開探針證過的四站（CAL／CLN／AET／OQCT：FAIL 段 100% 帶失效碼、PASS 0%）；"
                   "其他站的失效碼覆蓋率沒驗過，不回排行以免把「沒登記」說成「沒失效」。")


def precheck(tool, key):
    """不需 MES 就能下結論的情況 → 回完整 payload；否則 None。live／demo 共用（`board_data` 先叫它）。"""
    if tool == "station_failure_pareto" and key["cls"] not in SUPPORTED_STATIONS:
        out = blank(tool, key, found=False)
        out["not_measurable"] = [{"field": "station", "reason": "station_unsupported",
                                  "note": _NM_UNSUPPORTED}]
        out["caveats"] = ["支援的站：" + "／".join(f"{v}（{k}）" for k, v in SUPPORTED_STATIONS.items())]
        return out
    return None


# ---- station_failure_pareto -------------------------------------------------------
def _caveats_pareto(cls, *, truncated, coverage_clean, n_fail):
    short = SUPPORTED_STATIONS.get(cls)
    c = [
        "排行以「帶該失效碼的 FAIL 段數」排序；一段 FAIL 可同時帶多個碼 ⇒ `share_of_fail` 加總可超過 100%。",
        "段數是**測試段**（同一顆重測會算多段），不是產品顆數（PH）；窗以段開始時間計（含起點、不含終點）。",
        "失效碼只活在 MES 整數表、類別＝fcdMaterialFailureCode（探針證：字串／實數表零列）；"
        "`label` 是 MES 碼表的英文描述原文。",
    ]
    if cls in COUNT_TYPE_STATIONS:
        c.append(f"{short} 的部分碼是**計數**（例：Iddq 失效顆數，探針見過最大 11）⇒ `sum_value` 是次數總和，"
                 "不要跟段數 `n_fail_seg` 混比或相加；`value_kind`＝count 的列才有這個差別。")
    else:
        c.append(f"{short} 的碼是 0/1 旗標（探針證）⇒ `sum_value` 與段數相同。")
    if cls == AET_CLASS:
        c.append("AET 的段同時包含 module 段與它的 die 段（一次電測＝1 個 PH 段＋11 個 die 段，"
                 "die 段也各自帶碼）⇒ 段數以 die 為主、**不等於失效 PH 顆數**；要看單顆請用 module_die_etest。")
    if cls == "segFinalOQC":
        c.append("OQC 的分箱欄（Bin name）現行產線不可用（30 天只見一種值、FAIL 全空；獨立分箱段 30 天零段）"
                 "⇒ OQC FAIL 的內容以本排行回答。")
    if not coverage_clean:
        c.append("⚠ 本窗 FAIL／PASS 與失效碼的對應**不像探針那樣乾淨**（探針：FAIL 100% 帶碼、PASS 0%）"
                 "⇒ 看 `fail_coverage`；排行只描述帶碼的段。")
    if n_fail == 0:
        c.append("本窗沒有 FAIL 段 ⇒ 排行為空（不是查不到）。")
    if truncated:
        c.append(f"⚠ 只列前 {RANK_CAP} 個碼（`truncated=true`）＝長尾未列；`n_codes_ranked` 是**下界**。")
    return c


def derive_failure_pareto(cls, dict_rows, cover_rows, rank_rows, *, window=None,
                          n_codes_registered=None, fetched_at=None, caps=None, elapsed_s=None,
                          days_requested=None):
    """F0／F3／F4 的列 → `board.station_failure_pareto.v1`。列一律照 SQL 欄位順序（fixture 同形）。"""
    caps = dict(caps or {})
    window = dict(window or {})
    diag = {"fetched_at": _fmt(fetched_at), "cap_hit": caps, "elapsed_s": elapsed_s,
            "days_requested": days_requested, "n_rank_rows_raw": len(rank_rows)}
    base = blank("station_failure_pareto", {"cls": cls, "days": window.get("days")}, diag=diag)
    base["window"] = {"from": _fmt(window.get("from")), "to": _fmt(window.get("to")),
                      "days": window.get("days")}
    by_res = {}
    for r in cover_rows:
        res = _s(r[0]) or "(null)"
        a = by_res.setdefault(res, [0, 0, 0])
        a[0] += _int(r[1]) or 0
        a[1] += _int(r[2]) or 0
        a[2] += _int(r[3]) or 0
    n_seg = sum(v[0] for v in by_res.values())
    if n_seg == 0:
        base["reason"] = "no_segments_in_window"
        base["n_segments"] = 0
        base["caveats"] = ["本窗此站沒有任何測試段（以段開始時間計）；請加大 days 或確認站別。"]
        return base
    fail = by_res.get("FAIL", [0, 0, 0])
    pas = by_res.get("PASS", [0, 0, 0])
    n_other = n_seg - fail[0] - pas[0]
    cov = {"n_fail_with_code": fail[2], "n_fail_without_code": fail[0] - fail[2],
           "n_pass_with_code": pas[2]}
    coverage_clean = (fail[2] == fail[0]) and pas[2] == 0
    ranking = []
    for r in rank_rows:
        n_fail_seg = _int(r[3]) or 0
        n_s = _int(r[2]) or 0
        sv = _int(r[4])
        ranking.append({"rank": _int(r[5]), "code": _s(r[0]), "label": _s(r[1]),
                        "n_fail_seg": n_fail_seg,
                        "share_of_fail": (round(n_fail_seg / fail[0], 4) if fail[0] else None),
                        "n_seg": n_s, "sum_value": sv,
                        "value_kind": ("count" if (sv or 0) > n_s else "flag")})
    ranking.sort(key=lambda x: (x["rank"] if x["rank"] is not None else 10 ** 9))
    truncated = any(bool(v) for v in caps.values())
    n_reg = n_codes_registered if n_codes_registered is not None else (
        len(dict_rows) if dict_rows is not None else None)
    base.update({"found": True, "n_segments": n_seg, "n_fail": fail[0], "n_pass": pas[0],
                 "n_other": n_other, "fail_coverage": cov, "ranking": ranking,
                 "n_codes_ranked": len(ranking), "n_codes_registered": n_reg,
                 "truncated": truncated,
                 "caveats": _caveats_pareto(cls, truncated=truncated,
                                            coverage_clean=coverage_clean, n_fail=fail[0])})
    return base


# ---- module_die_etest ----------------------------------------------------------
def _caveats_etest(*, truncated, has_sparse):
    c = [
        "module 級（`module_level`，每次電測一列）與 die 級（`die_level`）**分開回**："
        "`varAET_Iddq`／`varAET_Iposq` 兩層同名但**尺度不同**（探針實測：die 級量值範圍遠大於 PH 級，"
        "不是 11 顆的加總或平均）⇒ 不得跨層相加、平均或互比。",
        "die 段以「同一次測試序號」與 PH 段相連；位置（`position` 0–10）取自 Die Placement 的組裝位置。"
        "探針 7 天窗 640/640 顆恰配 11 個 die。",
        "`substrate` 由 die 批號 `<substrate>_R<nn>_D<n>` 取 `_R` 前一段＝**推論待證**（未對照基板主檔）；"
        "不要用固定長度截字（`…_D11` 會被截成 `…_D1`）。",
        "die 的失效碼與 AET 站碼表同源（mflAET_*）；PASS 段不帶碼。",
        "只展開**最新一次**電測的 die；較早的電測只列 PH 級值與結果。",
        "本工具只回 MES 原值、不判規格上下限。",
    ]
    if has_sparse:
        c.append(f"`{SPARSE_DIE_VARS[0]}` 覆蓋率很低（探針：約 1.2% 的 die 段有值）⇒ 多數 die 沒有這一欄＝"
                 "**沒寫**，不是 0。")
    if truncated:
        c.append("⚠ 列數撞到上限（`truncated=true`）⇒ 清單是**下界**。")
    return c


def derive_die_etest(serial, ph_rows, die_rows, ph_real_rows, die_real_rows, mfl_rows, *,
                     fetched_at=None, caps=None, elapsed_s=None, die_window=None):
    """`#dph` 讀回／D2／D3b／D3／D5 的列 → `board.module_die_etest.v1`。

    列欄序（＝SQL SELECT 順序）：ph `(stpPK, lot, start, result)`；die `(stpPK, die_lot, start, result,
    assy_position)`；real `(stpPK, var, desc, uom, value)`；mfl `(lvl, stpPK, code, desc, value)`。"""
    caps = dict(caps or {})
    diag = {"fetched_at": _fmt(fetched_at), "cap_hit": caps, "elapsed_s": elapsed_s,
            "die_window": dict(die_window) if die_window else None,
            "n_ph_rows_raw": len(ph_rows), "n_die_rows_raw": len(die_rows),
            "n_real_rows_raw": len(ph_real_rows) + len(die_real_rows),
            "n_mfl_rows_raw": len(mfl_rows)}
    ph = [tuple(r) for r in ph_rows]
    if not ph:
        out = blank("module_die_etest", serial, found=False, diag=diag)
        out["not_measurable"] = [{
            "field": "serial", "reason": "serial_not_found",
            "note": "MES 查無這個碼的 AET module 電測段：可能不是 module 序號（單元／包裝序號請先用 "
                    "serial_genealogy 找它的 module）、或還沒做到打線後電測。"}]
        return out
    # attempts：最新在前（開始時間、stpPK 決勝；fixture 無 stpPK 時以時間）
    def _k(r):
        d = _dt(r[2])
        return (d or datetime.min, _int(r[0]) or 0)
    ph.sort(key=_k, reverse=True)
    mfl_by = {}
    for r in mfl_rows:
        v = _int(r[4])
        mfl_by.setdefault((_s(r[0]), _int(r[1])), []).append(
            {"code": _s(r[2]), "label": _s(r[3]), "value": v})
    for lst in mfl_by.values():
        lst.sort(key=lambda x: (-(x["value"] or 0), str(x["code"])))
    ph_vals = {}
    for r in ph_real_rows:
        ph_vals.setdefault(_int(r[0]), []).append(
            {"variable": _s(r[1]), "uom": _s(r[3]), "value": _num(r[4])})
    attempts = []
    n_att = len(ph)
    for i, r in enumerate(ph):
        pk = _int(r[0])
        vals = sorted(ph_vals.get(pk, []), key=lambda x: str(x["variable"]))
        attempts.append({"attempt": n_att - i, "is_latest": i == 0, "started_at": _ts(r[2]),
                         "result": _s(r[3]), "values": vals,
                         "failure_codes": list(mfl_by.get(("ph", pk), []))})
    # die 級
    var_meta, die_vals = {}, {}
    for r in die_real_rows:
        pk, var = _int(r[0]), _s(r[1])
        m = var_meta.setdefault(var, {"variable": var, "label": _s(r[2]), "uom": _s(r[3]),
                                      "n_dies_with_value": 0})
        dv = die_vals.setdefault(pk, {})
        if var not in dv:
            m["n_dies_with_value"] += 1
        dv[var] = _num(r[4])
    dies = []
    for r in die_rows:
        pk = _int(r[0])
        lot = _s(r[1])
        dies.append({"position": _int(r[4]), "die_lot": lot, "substrate": substrate_of(lot),
                     "started_at": _ts(r[2]), "result": _s(r[3]),
                     "values": dict(sorted(die_vals.get(pk, {}).items())),
                     "failure_codes": list(mfl_by.get(("die", pk), []))})
    dies.sort(key=lambda d: (d["position"] if d["position"] is not None else 10 ** 6,
                             str(d["die_lot"])))
    variables = sorted(var_meta.values(), key=lambda m: str(m["variable"]))
    die_level = {"for_attempt_started_at": attempts[0]["started_at"], "n_dies": len(dies),
                 "n_dies_expected": DIES_EXPECTED,
                 "n_pass": sum(1 for d in dies if d["result"] == "PASS"),
                 "n_fail": sum(1 for d in dies if d["result"] == "FAIL"),
                 "variables": variables, "dies": dies}
    nm = []
    if len(dies) != DIES_EXPECTED:
        nm.append({"field": "die_level.dies", "reason": "die_count_mismatch",
                   "note": f"最新一次電測配到 {len(dies)} 個 die 段（預期 {DIES_EXPECTED}；探針 640/640 皆 11）"
                           "⇒ die 清單可能不完整或多出，勿據此下結論。"})
    truncated = any(bool(v) for v in caps.values())
    has_sparse = bool(die_rows)             # 選用變數多數 die 沒寫：有 die 就提醒（不論這次有沒有值）
    return {"serial": serial, "found": True, "station": AET_CLASS, "module_level": attempts,
            "n_attempts": n_att, "die_level": die_level, "not_measurable": nm,
            "truncated": truncated, "caveats": _caveats_etest(truncated=truncated,
                                                              has_sparse=has_sparse),
            "reason": None, "unavailable_reason": None, "diag": diag}


# ---- validator（形狀檢查單一出處；smoke／selftest 委派它）--------------------------
_FORBIDDEN_OUT = ("stpPersonID", "OperatorName", "OperatorId", "matPK", "stpPK", "stpRSID")


def ok_payload(tool, rec):
    try:
        keys = KEYS_BY_TOOL[tool]
        body = {k: v for k, v in rec.items() if k not in ("source", "provenance")}
        if set(body) != set(keys):
            return False
        if not isinstance(rec["caveats"], list) or not isinstance(rec["diag"], dict):
            return False
        if rec["reason"] is not None and rec["reason"] not in REASONS:
            return False
        if rec["found"] and (rec["reason"] or rec["unavailable_reason"]):
            return False
        for nm in rec["not_measurable"]:
            if nm["reason"] not in NOT_MEASURABLE_REASONS:
                return False
        blob = json.dumps(body, ensure_ascii=False, default=str)
        if any(f in blob for f in _FORBIDDEN_OUT):
            return False
        if "\n" in "".join(_mm._walk_strings(body)):
            return False
        if tool == "station_failure_pareto":
            for r in rec["ranking"]:
                if set(r) != set(RANK_ROW_KEYS) or r["value_kind"] not in ("flag", "count"):
                    return False
        if tool == "module_die_etest" and rec["die_level"] is not None:
            dl = rec["die_level"]
            if set(dl) != set(DIE_LEVEL_KEYS):
                return False
            for d in dl["dies"]:
                if set(d) != set(DIE_ROW_KEYS):
                    return False
                # 跨層不混：die 列只准 die 值（dict）、PH 值只住 module_level
                if not isinstance(d["values"], dict):
                    return False
        return True
    except Exception:                                   # noqa: BLE001
        return False


# =============================================================================
# 4. I/O 編排（每支工具唯一的編排點；失敗一律往上拋，由 `board_data` 接成誠實降級）
# =============================================================================
def fetch_guard(cur, tool):
    cur.execute(SQL_GUARD)
    r = cur.fetchone()
    g = {k: bool(r[i]) for i, k in enumerate(GUARD_KEYS)}
    missing = [k for k in GUARD_NEEDS[tool] if not g.get(k)]
    if missing:
        raise SchemaDivergence("schema divergence: " + ", ".join(f"{k} ABSENT" for k in missing))
    return g


def _rows(cur):
    return [tuple(r) for r in cur.fetchall()]


def compute_failure_pareto(cur, cls, days, *, now=None):
    t0 = time.perf_counter()
    fetch_guard(cur, "station_failure_pareto")
    now = now or datetime.now()
    frm = (now - timedelta(days=int(days))).strftime(_TS_FMT)
    to = now.strftime(_TS_FMT)                  # ★ 上界永不超過現在（CONCEPT §21.1 髒日期）
    cur.execute(SQL_FP_DICT, cls)
    dict_rows = _rows(cur)
    cur.execute(SQL_FP_TEMPS)
    cur.execute(SQL_FP_SEG, cls, frm, to)
    cur.execute(SQL_FP_SEG_INDEX)
    cur.execute(SQL_FP_MFL)
    cur.execute(SQL_FP_FLAG)
    cur.execute(SQL_FP_COVER)
    cover = _rows(cur)
    rank = []
    if cover:
        cur.execute(SQL_FP_RANK, int(RANK_CAP))
        rank = _rows(cur)
    return derive_failure_pareto(cls, dict_rows, cover, rank,
                                 window={"from": frm, "to": to, "days": int(days)},
                                 fetched_at=now, caps={"ranking": len(rank) >= RANK_CAP},
                                 elapsed_s=round(time.perf_counter() - t0, 3),
                                 days_requested=int(days))


def compute_die_etest(cur, serial):
    t0 = time.perf_counter()
    fetch_guard(cur, "module_die_etest")
    cur.execute(SQL_DE_TEMPS)
    cur.execute(SQL_DE_PH, int(PH_CAP), serial)
    cur.execute(SQL_DE_PH_READ)
    ph = _rows(cur)
    dies = ph_real = die_real = mfl = []
    window = None
    if ph:
        latest = ph[0]                          # SQL 已最新在前
        t = _dt(latest[2])
        if t is None:
            raise ValueError("module segment start time unreadable")
        frm = (t - timedelta(days=DIE_WINDOW_DAYS)).strftime(_TS_FMT)
        to = (t + timedelta(days=DIE_WINDOW_DAYS)).strftime(_TS_FMT)
        window = {"from": frm, "to": to}
        cur.execute(SQL_DE_DIE, int(DIE_CAP), frm, to, int(latest[0]))
        cur.execute(SQL_DE_DIE_READ)
        dies = _rows(cur)
        cur.execute(SQL_DE_PH_REAL)
        ph_real = _rows(cur)
        cur.execute(SQL_DE_DIE_REAL)
        die_real = _rows(cur)
        cur.execute(SQL_DE_MFL)
        mfl = _rows(cur)
    caps = {"attempts": len(ph) >= PH_CAP, "dies": len(dies) >= DIE_CAP}
    return derive_die_etest(serial, ph, dies, ph_real, die_real, mfl, fetched_at=datetime.now(),
                            caps=caps, elapsed_s=round(time.perf_counter() - t0, 3),
                            die_window=window)


def compute(tool, cur, key):
    """`board_data.get_mes_etest` 的 live 分派點（`key` 已經過 normalize；呼叫端先跑 `precheck`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    if tool == "station_failure_pareto":
        return compute_failure_pareto(cur, key["cls"], key["days"])
    if tool == "module_die_etest":
        return compute_die_etest(cur, key)
    raise KeyError(tool)


# =============================================================================
# 5. demo：fixture（`_fixtures/mes_etest_<tool>.json`）→ 同一支 derive_*
# =============================================================================
FIXTURE_FILES = {t: f"mes_etest_{t}.json" for t in TOOLS}
DEMO_SERIAL = "J0SYN09"
DEMO_STATION = "segCalibrationTest"

_DEMO_CAVEAT_PARETO = ("（demo）這是探針 {frm} → {to} 的 {d} 天樣本；demo 不依 days 重算"
                       "（問的是 {req} 天）。")
_DEMO_CAVEAT_ETEST = ("（demo）探針樣本只抽了 die 級四個變數（Iddq／Iposq／grossi_Idd／grossi_Ivpos），"
                      "PH 級值與失效碼明細未抽 ⇒ demo 的 `values` 不完整；live 會回 PH 級 2 變數＋die 級 17 變數。")


def demo_lookup(tool, doc, key):
    """fixture 裡找樣本 → 跑**同一支** `derive_*`（demo 與 live 走同一份純函式＝形狀單一出處）。
    查不到回 `None`（呼叫端回 `not_in_demo_fixture`，**不是** `serial_not_found`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    gs = (doc or {}).get("golden") or []
    if tool == "station_failure_pareto":
        for g in gs:
            if g["key"] == key["cls"]:
                s = g["steps"]
                w = g["window"]
                out = derive_failure_pareto(
                    key["cls"], s["F0"]["rows"], s["F3"]["rows"], s["F4"]["rows"], window=w,
                    n_codes_registered=g["n_codes_registered_F0s"], fetched_at=g["captured_at"],
                    caps=g["caps"], days_requested=key["days"])
                if key["days"] != w["days"]:
                    out["caveats"].append(_DEMO_CAVEAT_PARETO.format(
                        frm=w["from"], to=w["to"], d=w["days"], req=key["days"]))
                return out
    elif tool == "module_die_etest":
        for g in gs:
            if g["key"].upper() == key.upper():
                s = g["steps"]
                out = derive_die_etest(key, s["PH"]["rows"], s["DIE"]["rows"],
                                       s["PH_REAL"]["rows"], s["DIE_REAL"]["rows"],
                                       s["MFL"]["rows"], fetched_at=g["captured_at"],
                                       caps=g["caps"])
                out["caveats"].append(_DEMO_CAVEAT_ETEST)
                return out
    return None


# =============================================================================
# 6. fixture builder（讀探針 TSV；純離線、零 MES）
# =============================================================================
PROBE_TSV = "_PROBE-ASSISTANT-MES-FAMILY-3-RESULT.tsv"
PROBE_SQL = "_PROBE-ASSISTANT-MES-FAMILY-3-20260924.sql"
CAPTURED_AT = "2026-08-14 10:00:00"
_TAG_RE = re.compile(r"^(0a|F[0-9]s?|D[0-9]b?|V[0-9]|END)$")


def parse_probe_tsv(path):
    """探針 TSV（SSMS 多結果集貼成一檔；本支的 `step` 值無空白）→ `{tag: {"header", "rows"}}`。
    零列結果集（F5）無 `step` 值可認 ⇒ 略過。"""
    sets, cur = [], None
    with io.open(path, encoding="utf-8-sig") as fh:
        for ln in fh.read().split("\n"):
            ln = ln.rstrip("\r")
            if ln.startswith("step\t"):
                cur = {"header": ln.split("\t"), "rows": []}
                sets.append(cur)
                continue
            if cur is None or not ln.strip():
                continue
            cells = ln.split("\t")
            if _TAG_RE.match(cells[0]):
                if len(cells) != len(cur["header"]):
                    raise ValueError(f"{cells[0]}: ragged row {cells[:3]}")
                cur["rows"].append(cells)
    out = {}
    for s in sets:
        if s["rows"]:
            out.setdefault(s["rows"][0][0], s)
    return out


def _col(step_set, name):
    return step_set["header"].index(name)


def _pick(step_set, cols, where=None):
    idx = [_col(step_set, c) for c in cols]
    rows = []
    for r in step_set["rows"]:
        d = dict(zip(step_set["header"], r))
        if where is None or where(d):
            rows.append([None if r[i] == "NULL" else r[i] for i in idx])
    return rows


def build_fixtures(base=BASE):
    t = parse_probe_tsv(os.path.join(base, PROBE_TSV))
    a0 = dict(zip(t["0a"]["header"], t["0a"]["rows"][0]))
    win = {"from": a0["win_from"][:19], "to": a0["win_to"][:19], "days": 7}
    common = {"captured_at": CAPTURED_AT, "source_probe": PROBE_SQL,
              "_provenance": "domain-side read-only query 2026-08-14; " + PROBE_SQL +
                             "; builder = mes_etest.py --build-fixtures (do not hand-edit)"}
    # ── pareto：每站一份（F0 字典／F0s 計數／F3 覆蓋／F4 排行）──
    fp = []
    for cls in SUPPORTED_STATIONS:
        f0 = _pick(t["F0"], ("vdfProductionDataID", "FailureDescription"),
                   where=lambda d, c=cls: d["cls"] == c)
        f0s = _pick(t["F0s"], ("n_var",), where=lambda d, c=cls: d["cls"] == c
                    and d["vdfClassID"] == "fcdMaterialFailureCode")
        n_reg = int(f0s[0][0]) if f0s else None
        f3 = _pick(t["F3"], ("stpTestResult", "n_seg", "n_seg_any_mfl_row", "n_seg_any_mfl_pos"),
                   where=lambda d, c=cls: d["cls"] == c)
        f4 = _pick(t["F4"], ("vdfProductionDataID", "FailureDescription", "n_seg_pos",
                             "n_fail_seg_pos", "sum_value", "rk"),
                   where=lambda d, c=cls: d["cls"] == c)
        fp.append({"key": cls, **common, "window": dict(win),
                   "n_codes_registered_F0s": n_reg,
                   "dict_complete_in_probe": len(f0) == n_reg,
                   "caps": {"ranking": len(f4) >= RANK_CAP},
                   "steps": {
                       "F0": {"source_step": "F0 (probe TOP 200 — may be truncated; "
                                             "n_codes_registered_F0s is authoritative)",
                              "columns": ["vdfProductionDataID", "FailureDescription"], "rows": f0},
                       "F3": {"source_step": "F3",
                              "columns": ["stpTestResult", "n_seg", "n_seg_any_mfl_row",
                                          "n_seg_any_mfl_pos"], "rows": f3},
                       "F4": {"source_step": "F4 (rk by all-segment count; F3 proves == FAIL count)",
                              "columns": ["vdfProductionDataID", "FailureDescription", "n_seg_pos",
                                          "n_fail_seg_pos", "sum_value", "rk"], "rows": f4}}})
    out = {"station_failure_pareto": {
        "schema_id": SCHEMA_PARETO, "golden": fp,
        "_note": "window = probe family F 7-day window; demo does not recompute for other days."}}
    # ── die etest：D2 抽到的 5 顆（D4 只抽四變數、TOP 200 截斷最後一顆）──
    d2, d3, d4 = t["D2"], t["D3"], t["D4"]
    meta = {r[_col(d3, "vdfProductionDataID")]: (r[_col(d3, "FailureDescription")],
                                                 r[_col(d3, "vdfUOM")]) for r in d3["rows"]}
    serials = []
    for r in d2["rows"]:
        s = r[_col(d2, "ph_lot")]
        if s not in serials:
            serials.append(s)
    de = []
    for ser in serials:
        rows2 = [dict(zip(d2["header"], r)) for r in d2["rows"] if r[_col(d2, "ph_lot")] == ser]
        ph = [[None, ser, rows2[0]["ph_time"], rows2[0]["ph_result"]]]
        die = [[r["die_stpPK"], r["die_lot"], r["die_time"], r["die_result"], r["assy_position"]]
               for r in rows2]
        pk_of = {r["die_lot"]: r["die_stpPK"] for r in rows2}
        real = []
        for r in d4["rows"]:
            d = dict(zip(d4["header"], r))
            if d["ph_lot"] == ser:
                var = d["vdfProductionDataID"]
                real.append([pk_of[d["die_lot"]], var, meta[var][0], meta[var][1], d["sgrReal"]])
        n_die_with_vals = len({x[0] for x in real})
        de.append({"key": ser, **common,
                   "die_values_complete_in_probe": n_die_with_vals == len(die),
                   "caps": {"attempts": False, "dies": False},
                   "steps": {
                       "PH": {"source_step": "D2 (ph_lot/ph_time/ph_result; stpPK not in probe)",
                              "columns": ["stpPK", "matMaterialLotID", "stpStartTime",
                                          "stpTestResult"], "rows": ph},
                       "DIE": {"source_step": "D2",
                               "columns": ["stpPK", "die_lot", "stpStartTime", "stpTestResult",
                                           "assy_position"], "rows": die},
                       "PH_REAL": {"source_step": "(not sampled per serial in probe; D3b is aggregate)",
                                   "columns": ["stpPK", "vdfProductionDataID", "FailureDescription",
                                               "vdfUOM", "sgrReal"], "rows": []},
                       "DIE_REAL": {"source_step": "D4 (4 vars; desc/uom from D3)",
                                    "columns": ["stpPK", "vdfProductionDataID", "FailureDescription",
                                                "vdfUOM", "sgrReal"], "rows": real},
                       "MFL": {"source_step": "(D5 is aggregate; sampled dies are all PASS)",
                               "columns": ["lvl", "stpPK", "vdfProductionDataID",
                                           "FailureDescription", "sgiInteger"], "rows": []}}})
    ref = {"D0": dict(zip(t["D0"]["header"][1:], t["D0"]["rows"][0][1:])),
           "D1": _pick(t["D1"], ("n_die_per_ph", "n_ph_seg")),
           "D3": _pick(d3, ("vdfProductionDataID", "vdfUOM", "n_rows", "min_v", "max_v")),
           "D3b": _pick(t["D3b"], ("vdfProductionDataID", "vdfUOM", "n_rows", "min_v", "max_v")),
           "D5": _pick(t["D5"], ("vdfProductionDataID", "stpTestResult", "n_rows", "n_pos"))}
    out["module_die_etest"] = {
        "schema_id": SCHEMA_ETEST, "golden": de, "demo_serial": DEMO_SERIAL,
        "_reference": ref,
        "_note": "per-serial rows from D2/D4 (D4 TOP 200 truncates the last serial's values); "
                 "_reference = probe aggregates for selftest pins, not used by the tool."}
    return out


def write_fixtures(base=BASE):
    docs = build_fixtures(base)
    paths = []
    for tool, doc in docs.items():
        p = os.path.join(base, "_fixtures", FIXTURE_FILES[tool])
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_mm._dump(doc) + "\n")
        paths.append(p)
    return paths


def _fixture(tool, base=BASE):
    with open(os.path.join(base, "_fixtures", FIXTURE_FILES[tool]), encoding="utf-8") as fh:
        return json.load(fh)


# =============================================================================
# 7. --selftest（純離線）
# =============================================================================
class _FakeCursor:
    """記錄送出的 SQL 與參數；依 SQL 身分回預置列。"""

    def __init__(self, answers, guard=None):
        self.log, self.answers = [], answers
        self._last = None
        self.guard = guard or tuple(1 for _ in GUARD_KEYS)

    def execute(self, sql, *params):
        self.log.append((sql, params))
        self._last = sql

    def fetchone(self):
        if self._last is SQL_GUARD:
            return self.guard
        return self.answers[self._last]

    def fetchall(self):
        return self.answers.get(self._last, [])


def _selftest():
    n = 0
    mod = sys.modules[__name__]

    def t(label, cond, detail=""):
        nonlocal n
        assert cond, f"selftest FAIL: {label}{(' — ' + str(detail)) if detail else ''}"
        n += 1

    # ---- ① SQL 文本紀律（`ALL_SQL()` 是唯一清單）--------------------------------------
    sqls = ALL_SQL()
    t("all-sql-17", len(sqls) == 17 and len(set(sqls)) == 17)
    for s in sqls:
        head = s.lstrip()
        if head.startswith(("SELECT", "INSERT")) and "dbo." in s and "WHERE 1=0" not in s:
            t("recompile", s.rstrip().endswith("OPTION (RECOMPILE)"), s[:50])
        if "?" in s:
            # ★ 參數化語句（sp_executesql）裡建的 #temp 會隨呼叫消失（mes_hermes 09-21 事故）
            t("param-no-select-into", not re.search(r"(?<!INSERT )INTO #", s), s[:50])
            t("param-no-create", "CREATE " not in s and "DROP " not in s, s[:50])
        if "WHERE 1=0" in s:
            t("temps-unparameterized", "?" not in s, s[:50])
    t("temps-only-in-shell-batches",
      [s for s in sqls if re.search(r"(?<!INSERT )INTO #", s)] == [SQL_FP_TEMPS, SQL_DE_TEMPS])
    blob = "\n".join(sqls)
    for bad in ("stpPersonID", "OperatorId", "OperatorName", "aslPersonID", "rqsRequestor",
                "Comment", "all_segment_data_value_v", "rqsChangeLog", "sgrString"):
        t(f"never-{bad}", bad not in blob)
    t("★ no-LEFT(-on-lot（digest 異常 3）", "LEFT(" not in blob.upper())
    t("no-end-filter", "stpEndTime" not in blob)
    t("no-varOQCT-bin（oqc_bins 放棄）", "BinName" not in blob and "OQCBin" not in blob)
    for name in FAILURE_CODE_SQL_NAMES:
        t(f"★ mfl-no-TOP-{name}（F0 截斷教訓）", "TOP" not in getattr(mod, name))
    t("mfl-by-classid", all("vdfClassID = 'fcdMaterialFailureCode'" in getattr(mod, nm)
                            for nm in ("SQL_FP_DICT", "SQL_FP_MFL", "SQL_DE_MFL")))
    t("mfl-no-name-prefix", "LIKE 'mfl" not in blob and " LIKE " not in blob)
    t("fp-window-halfopen", "PSG.stpStartTime >= ? AND PSG.stpStartTime < ?" in SQL_FP_SEG)
    t("fp-single-station-eq", "PSC.SegmentClassID = ?" in SQL_FP_SEG
      and "IN (@" not in blob)
    t("fp-flag-first（Msg 130）", "MAX(CASE WHEN sgiInteger > 0" in SQL_FP_FLAG
      and "LEFT JOIN #ep_fflag" in SQL_FP_COVER)
    for s in sqls:
        # ★ 聚合函式內／GROUP BY 內不放子查詢（Msg 130／144）
        t("no-subquery-in-aggregate", not re.search(r"(COUNT|SUM|MAX|MIN|AVG)\s*\(\s*\(\s*SELECT", s),
          s[:40])
        m = re.search(r"GROUP BY([^\n]*)", s)
        if m:
            t("no-subquery-in-group-by", "SELECT" not in m.group(1), s[:40])
    t("fp-rank-fail-first（E4）", "ORDER BY\n                COUNT(DISTINCT CASE WHEN stpTestResult = 'FAIL'"
      in SQL_FP_RANK and "rk <= ?" in SQL_FP_RANK and "vdfProductionDataID)" in SQL_FP_RANK)
    t("fp-rank-no-partition（E2）", "PARTITION BY" not in SQL_FP_RANK)
    t("fp-int-table-only（F5）", "segment_string_data_t" not in blob
      and "segment_real_data_t" not in SQL_FP_MFL)
    t("de-ph-eq-serial", "MTL.matMaterialLotID = ?" in SQL_DE_PH and "'assyModule'" in SQL_DE_PH)
    t("de-die-wfrdie", "'sbsDie'" in SQL_DE_DIE and "'segAuxElectricalTest'" in SQL_DE_DIE)
    t("de-die-rsid-semijoin（D1）",
      "PSG.stpRSID IN (SELECT p.stpRSID FROM #ep_dph p WHERE p.stpPK = ?)" in SQL_DE_DIE)
    t("de-die-window-param", "PSG.stpStartTime >= ? AND PSG.stpStartTime < ?" in SQL_DE_DIE)
    t("de-position-surfaceprep（D2）", "'segDiePlacement'" in SQL_DE_DIE_READ
      and "sgmAssemblyPosition" in SQL_DE_DIE_READ and "ORDER BY PSM.stpPK DESC" in SQL_DE_DIE_READ)
    t("de-real-base-tables（D3）", "segment_real_data_t" in SQL_DE_PH_REAL
      and "segment_real_data_t" in SQL_DE_DIE_REAL and "#ep_dph" in SQL_DE_PH_REAL
      and "#ep_ddie" in SQL_DE_DIE_REAL)
    t("de-no-4var-filter（E9）", "IN ('varAET" not in blob)
    t("de-mfl-two-levels（E10）", "lvl = 'ph'" in SQL_DE_MFL and "lvl = 'die'" in SQL_DE_MFL)
    t("guard-keys", len(GUARD_KEYS) == SQL_GUARD.count("CASE WHEN")
      and all(set(v) <= set(GUARD_KEYS) for v in GUARD_NEEDS.values()))
    t("query-names-real", all(hasattr(mod, q.split(".", 1)[1])
                              for qs in TOOL_QUERY_NAMES.values() for q in qs)
      and set(TOOL_QUERY_NAMES) == set(TOOLS))
    t("query-names-in-all-sql", all(getattr(mod, q.split(".", 1)[1]) in sqls
                                    for qs in TOOL_QUERY_NAMES.values() for q in qs))
    t("caps", RANK_CAP == 25 and DIES_EXPECTED == 11 and PARETO_DAYS_MAX == 31)

    # ---- ② 參數正規化 -----------------------------------------------------------------
    t("st-alias-cal", normalize_station("cal") == ("segCalibrationTest", None))
    t("st-alias-oqc", normalize_station("OQC")[0] == "segFinalOQC")
    t("st-full-case", normalize_station("segcleantest")[0] == "segCleanTest")
    t("st-unsupported-passes-norm", normalize_station("segDiePlacement") == ("segDiePlacement", None))
    for bad in ("x'; --", "seg%", "DiePlacement", "seg A"):
        t(f"st-bad-{bad}", normalize_station(bad)[1]["error"] == "bad_station")
    t("st-missing", normalize_station("")[1]["error"] == "missing_station")
    t("pa-default-days", normalize_pareto_args("CAL")[0] == {"cls": "segCalibrationTest",
                                                             "days": PARETO_DAYS_DEFAULT})
    t("pa-30", normalize_pareto_args("CAL", 30)[0]["days"] == 30)
    for bad in (0, 32, "x", -1):
        t(f"pa-days-refused-{bad}", normalize_pareto_args("CAL", bad)[1]["error"] == "bad_days")
    t("serial-ok", normalize_serial(" J0SYN09 ") == ("J0SYN09", None))
    t("serial-bad", normalize_serial("B0%")[1]["error"] == "bad_serial")
    t("★ substrate-two-digit-die", substrate_of("C9SY02-22_R26_D11") == "C9SY02-22")
    t("substrate-one-digit-die", substrate_of("C9SY02-22_R36_D4") == "C9SY02-22")
    t("substrate-d10-d1-same-substrate-not-same-string",
      substrate_of("C9SY01-02_R35_D10") == substrate_of("C9SY01-02_R35_D1") == "C9SY01-02")
    t("substrate-unparseable-none", substrate_of("WEIRDLOT") is None and substrate_of(None) is None)

    # ---- ③ 純函式：pareto -------------------------------------------------------------
    cov = [("PASS", "100", "0", "0"), ("FAIL", "10", "10", "10"), ("ABORT", "2", "0", "0")]
    rk = [("mflA", "A fail", "6", "6", "6", "1"), ("mflB", "B fail", "5", "5", "14", "2")]
    p = derive_failure_pareto("segAuxElectricalTest", [("mflA", "A"), ("mflB", "B"), ("mflC", "C")],
                              cov, rk, window={"from": "2026-09-17 00:00:00",
                                               "to": "2026-09-24 00:00:00", "days": 7},
                              caps={"ranking": False})
    t("pa-validator", ok_payload("station_failure_pareto", p), p)
    t("pa-counts", (p["n_segments"], p["n_fail"], p["n_pass"], p["n_other"]) == (112, 10, 100, 2))
    t("pa-share", p["ranking"][0]["share_of_fail"] == 0.6 and p["ranking"][1]["share_of_fail"] == 0.5)
    t("pa-value-kind", [r["value_kind"] for r in p["ranking"]] == ["flag", "count"])
    t("pa-registered", p["n_codes_registered"] == 3 and p["n_codes_ranked"] == 2)
    t("pa-aet-die-caveat", any("die 段" in c for c in p["caveats"]))
    t("pa-count-caveat", any("sum_value" in c and "計數" in c for c in p["caveats"]))
    t("pa-coverage-clean-no-warning", not any("不像探針" in c for c in p["caveats"]))
    p2 = derive_failure_pareto("segCalibrationTest", [], [("PASS", "5", "1", "1"),
                                                              ("FAIL", "4", "3", "3")], [],
                               caps={"ranking": False})
    t("pa-coverage-dirty-warning", any("不像探針" in c for c in p2["caveats"])
      and p2["fail_coverage"] == {"n_fail_with_code": 3, "n_fail_without_code": 1,
                                  "n_pass_with_code": 1})
    t("pa-flag-caveat-cal", any("0/1 旗標" in c for c in p2["caveats"]))
    p0 = derive_failure_pareto("segCalibrationTest", [], [], [], window={"days": 7})
    t("pa-empty-reason", p0["found"] is False and p0["reason"] == "no_segments_in_window"
      and ok_payload("station_failure_pareto", p0))
    pnf = derive_failure_pareto("segCalibrationTest", [], [("PASS", "5", "0", "0")], [])
    t("pa-no-fail", pnf["found"] and pnf["ranking"] == [] and pnf["n_fail"] == 0
      and any("沒有 FAIL" in c for c in pnf["caveats"]))
    ptr = derive_failure_pareto("segCleanTest", [], cov, rk, caps={"ranking": True})
    t("pa-trunc-caveat", ptr["truncated"] and any("下界" in c for c in ptr["caveats"]))
    pu = precheck("station_failure_pareto", {"cls": "segDiePlacement", "days": 7})
    t("pa-unsupported", pu["found"] is False and pu["not_measurable"][0]["reason"]
      == "station_unsupported" and ok_payload("station_failure_pareto", pu))
    t("pa-supported-no-precheck", precheck("station_failure_pareto",
                                           {"cls": "segCleanTest", "days": 7}) is None)
    t("pa-oqc-bin-caveat", any("分箱" in c for c in derive_failure_pareto(
        "segFinalOQC", [], cov, rk)["caveats"]))

    # ---- ④ 純函式：die etest ----------------------------------------------------------
    ph = [(101, "B1", "2026-09-20 10:00:00", "FAIL"), (202, "B1", "2026-09-21 10:00:00", "PASS")]
    dies = [(1000 + i, f"W1-01_R1{i // 10}_D{i + 1}", "2026-09-21 10:00:05", "PASS", str(10 - i))
            for i in range(11)]
    ph_real = [(202, "varAET_Iddq", "Leak", "µA", "2"), (202, "varAET_Iposq", "Leak", "µA", "9"),
               (101, "varAET_Iddq", "Leak", "µA", "1993.636")]
    die_real = [(1000, "varAET_Iddq", "Leak", "µA", "2500"), (1000, "varAET_grossi_Idd", "G", "mA",
                                                             "4.022"),
                (1001, "varAET_Iddq", "Leak", "µA", "3"), (1001, "varAET_bad_actuators", "B",
                                                            "actuators", "0")]
    mfl = [("ph", 101, "mflAET_Iddq", "Number of Iddq leakage failures", "2"),
           ("die", 1000, "mflAET_Contact", "contact", "1")]
    e = derive_die_etest("B1", ph, dies, ph_real, die_real, mfl, caps={"attempts": False})
    t("de-validator", ok_payload("module_die_etest", e), e)
    t("de-latest-first", e["module_level"][0]["is_latest"]
      and e["module_level"][0]["started_at"] == "2026-09-21 10:00:00"
      and [a["attempt"] for a in e["module_level"]] == [2, 1])
    t("de-ph-values-per-attempt", [v["variable"] for v in e["module_level"][0]["values"]]
      == ["varAET_Iddq", "varAET_Iposq"] and e["module_level"][1]["values"][0]["value"] == 1993.636)
    t("de-ph-mfl", e["module_level"][1]["failure_codes"][0]["code"] == "mflAET_Iddq"
      and e["module_level"][1]["failure_codes"][0]["value"] == 2)
    dl = e["die_level"]
    t("de-11-dies", dl["n_dies"] == 11 and e["not_measurable"] == [])
    t("de-sorted-by-position", [d["position"] for d in dl["dies"]] == list(range(11)))
    t("★ de-cross-level-not-mixed", all("varAET_Iposq" not in d["values"] for d in dl["dies"])
      and dl["dies"][-1]["values"]["varAET_Iddq"] == 2500)
    t("de-die-mfl", dl["dies"][-1]["failure_codes"] == [{"code": "mflAET_Contact",
                                                         "label": "contact", "value": 1}])
    t("de-variables-meta", [v["variable"] for v in dl["variables"]]
      == ["varAET_Iddq", "varAET_bad_actuators", "varAET_grossi_Idd"]
      and dl["variables"][0]["n_dies_with_value"] == 2)
    t("de-sparse-caveat", any("bad_actuators" in c for c in e["caveats"]))
    t("de-cross-scale-caveat", any("尺度不同" in c for c in e["caveats"]))
    t("de-substrate-caveat", any("推論待證" in c for c in e["caveats"]))
    t("de-substrate-derived", dl["dies"][0]["substrate"] == "W1-01")
    t("de-no-internal-keys-out", all(k not in json.dumps(e) for k in ("stpPK", "stpRSID", "matPK")))
    e10 = derive_die_etest("B1", ph, dies[:10], [], [], [])
    t("★ de-die-count-mismatch", e10["not_measurable"][0]["reason"] == "die_count_mismatch"
      and ok_payload("module_die_etest", e10))
    e0 = derive_die_etest("B9", [], [], [], [], [])
    t("★ de-serial-not-found", e0["found"] is False
      and e0["not_measurable"][0]["reason"] == "serial_not_found"
      and e0["die_level"] is None and ok_payload("module_die_etest", e0))
    t("de-trunc", derive_die_etest("B1", ph, dies, [], [], [], caps={"dies": True})["truncated"])
    bad = dict(e)
    bad["diag"] = dict(e["diag"], stpPK=1)
    t("validator-rejects-internal-key-in-diag", not ok_payload("module_die_etest", bad))
    bad2 = dict(e)
    bad2["not_measurable"] = [{"field": "x", "reason": "whatever"}]
    t("validator-rejects-open-reason", not ok_payload("module_die_etest", bad2))
    bad3 = dict(p)
    bad3["extra"] = 1
    t("validator-rejects-extra-key", not ok_payload("station_failure_pareto", bad3))
    t("blank-keys", all(set(blank(tl, None)) == set(KEYS_BY_TOOL[tl]) for tl in TOOLS))

    for tl in TOOLS:
        cur = _FakeCursor({}, guard=tuple(0 for _ in GUARD_KEYS))
        try:
            key = {"cls": "segCalibrationTest", "days": 7} if tl == TOOLS[0] else "B1"
            compute(tl, cur, key)
            t(f"guard-raises-{tl}", False)
        except SchemaDivergence as ex:
            t(f"guard-raises-{tl}", "ABSENT" in str(ex))

    # golden fixture 不存在（例：脫敏公開樹不帶原始撈取 golden）⇒ 依賴 golden 的段落標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    _fx_absent = [f for f in FIXTURE_FILES.values()
                  if not os.path.exists(os.path.join(BASE, "_fixtures", f))]
    if _fx_absent:
        print("mes_etest selftest: skipped: %s absent" % ", ".join(sorted(_fx_absent)))
        print(f"mes_etest selftest: {n} assertions PASS")
        return 0

    # ---- ⑤ fixture：兩份 golden 走同一支 derive_*（demo 與 live 同形）-------------------
    fx = {tl: _fixture(tl) for tl in TOOLS}
    t("fx-schema-ids", [fx[tl]["schema_id"] for tl in TOOLS] == [SCHEMA_PARETO, SCHEMA_ETEST])
    t("fx-builder-reproducible", build_fixtures() == fx)
    for tl in TOOLS:
        for g in fx[tl]["golden"]:
            for st in g["steps"].values():
                t(f"fx-cols-{tl}", all(len(row) == len(st["columns"]) for row in st["rows"]))
            t(f"fx-provenance-{tl}", g["source_probe"] == PROBE_SQL and g["captured_at"])
    fpg = {g["key"]: g for g in fx["station_failure_pareto"]["golden"]}
    t("fx-fp-four-stations", sorted(fpg) == sorted(SUPPORTED_STATIONS))
    t("★ fx-fp-F0s-authoritative", {SUPPORTED_STATIONS[k]: g["n_codes_registered_F0s"]
                                    for k, g in fpg.items()}
      == {"CAL": 31, "CLN": 159, "AET": 16, "OQCT": 37})
    t("★ fx-fp-F0-truncated-disclosed", {SUPPORTED_STATIONS[k]: g["dict_complete_in_probe"]
                                         for k, g in fpg.items()}
      == {"CAL": True, "CLN": True, "AET": False, "OQCT": False})
    demo = {k: demo_lookup("station_failure_pareto", fx["station_failure_pareto"],
                           {"cls": k, "days": 7}) for k in fpg}
    for k, v in demo.items():
        t(f"fx-fp-{SUPPORTED_STATIONS[k]}-valid", v["found"] and ok_payload("station_failure_pareto", v))
        t(f"★ fx-fp-{SUPPORTED_STATIONS[k]}-coverage-100-0",
          v["fail_coverage"]["n_fail_without_code"] == 0 and v["fail_coverage"]["n_pass_with_code"] == 0)
    t("★ fx-fp-n-fail", {SUPPORTED_STATIONS[k]: (v["n_fail"], v["n_pass"]) for k, v in demo.items()}
      == {"CAL": (133, 1186), "CLN": (388, 9769), "AET": (1129, 12119), "OQCT": (562, 557)})
    cal = demo["segCalibrationTest"]
    t("★ fx-fp-cal-top2", [(r["code"], r["n_fail_seg"]) for r in cal["ranking"][:2]]
      == [("mflCAL_FailedUnitDeadChannelCount", 58), ("mflCAL_FailedModuleConnectionTest", 54)])
    t("fx-fp-cal-flags", all(r["value_kind"] == "flag" for r in cal["ranking"]))
    aet = demo["segAuxElectricalTest"]
    t("★ fx-fp-aet-iddq-count", aet["ranking"][0]["code"] == "mflAET_Iddq"
      and aet["ranking"][0]["n_fail_seg"] == 910 and aet["ranking"][0]["sum_value"] == 1588
      and aet["ranking"][0]["value_kind"] == "count")
    t("fx-fp-aet-share", aet["ranking"][0]["share_of_fail"] == round(910 / 1129, 4))
    t("fx-fp-oqc-top", demo["segFinalOQC"]["ranking"][0]["code"]
      == "mflOQCT_LightStreaks")
    t("fx-fp-cln-22", demo["segCleanTest"]["n_codes_ranked"] == 22
      and not demo["segCleanTest"]["truncated"])
    d30 = demo_lookup("station_failure_pareto", fx["station_failure_pareto"],
                      {"cls": DEMO_STATION, "days": 30})
    t("★ fx-fp-demo-30d-honest", d30["window"]["days"] == 7 and d30["diag"]["days_requested"] == 30
      and any("demo 不依 days 重算" in c for c in d30["caveats"]))
    t("fx-fp-demo-unsupported", demo_lookup("station_failure_pareto", fx["station_failure_pareto"],
                                            {"cls": "segDiePlacement", "days": 7})["not_measurable"][0]
      ["reason"] == "station_unsupported")
    deg = {g["key"]: g for g in fx["module_die_etest"]["golden"]}
    t("fx-de-five-serials", sorted(deg) == ["J0SYN09", "J0SYN16", "J0SYN10", "J0SYN11", "J0SYN12"])
    t("fx-de-demo-serial-complete", fx["module_die_etest"]["demo_serial"] == DEMO_SERIAL
      and deg[DEMO_SERIAL]["die_values_complete_in_probe"] is True)
    t("fx-de-zkzg-partial-disclosed", deg["J0SYN12"]["die_values_complete_in_probe"] is False)
    ref = fx["module_die_etest"]["_reference"]
    t("★ fx-de-D1-all-11", ref["D1"] == [["11", "640"]])
    t("★ fx-de-D3-17-vars-D3b-2", len(ref["D3"]) == 17 and [r[0] for r in ref["D3b"]]
      == ["varAET_Iddq", "varAET_Iposq"])
    for k in deg:
        v = demo_lookup("module_die_etest", fx["module_die_etest"], k)
        t(f"fx-de-{k}-valid", v["found"] and ok_payload("module_die_etest", v)
          and v["die_level"]["n_dies"] == 11 and v["not_measurable"] == [])
    dz = demo_lookup("module_die_etest", fx["module_die_etest"], DEMO_SERIAL)
    t("fx-de-positions-0-10", [d["position"] for d in dz["die_level"]["dies"]] == list(range(11)))
    t("★ fx-de-substrate-from-_R", {d["substrate"] for d in dz["die_level"]["dies"]} == {"C9SY01-02"}
      and any(d["die_lot"] == "C9SY01-02_R35_D10" for d in dz["die_level"]["dies"]))
    t("fx-de-4-vars-in-demo", sorted(v["variable"] for v in dz["die_level"]["variables"])
      == ["varAET_Iddq", "varAET_Iposq", "varAET_grossi_Idd", "varAET_grossi_Ivpos"])
    t("fx-de-demo-caveat", any("demo" in c for c in dz["caveats"]))
    t("fx-de-value", [d["values"]["varAET_Iposq"] for d in dz["die_level"]["dies"]
                      if d["die_lot"] == "C9SY01-02_R45_D1"] == [17])
    t("fx-de-miss-none", demo_lookup("module_die_etest", fx["module_die_etest"], "NOPE") is None)

    # ---- ⑥ 編排（假游標）：順序、臨時表只在非參數化批次建、參数 ----------------------
    g = fpg["segCalibrationTest"]["steps"]
    cur = _FakeCursor({SQL_FP_DICT: g["F0"]["rows"], SQL_FP_COVER: g["F3"]["rows"],
                       SQL_FP_RANK: g["F4"]["rows"]})
    now = datetime(2026, 8, 14, 12, 0, 0)
    rp = compute_failure_pareto(cur, "segCalibrationTest", 30, now=now)
    t("cmp-fp-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_FP_DICT, SQL_FP_TEMPS, SQL_FP_SEG,
                                                  SQL_FP_SEG_INDEX, SQL_FP_MFL, SQL_FP_FLAG,
                                                  SQL_FP_COVER, SQL_FP_RANK])
    t("cmp-fp-params", cur.log[3][1] == ("segCalibrationTest", "2026-08-25 12:00:00",
                                         "2026-08-14 12:00:00")
      and cur.log[-1][1] == (RANK_CAP,) and cur.log[1][1] == ("segCalibrationTest",))
    t("cmp-fp-valid", ok_payload("station_failure_pareto", rp) and rp["window"]["days"] == 30
      and rp["n_codes_registered"] == 31)
    t("cmp-fp-temps-no-params", all(p_ == () for s_, p_ in cur.log
                                    if s_ in (SQL_FP_TEMPS, SQL_FP_MFL, SQL_FP_FLAG)))
    cur = _FakeCursor({SQL_FP_COVER: []})
    t("cmp-fp-empty-skips-rank", compute_failure_pareto(cur, "segCleanTest", 7)["reason"]
      == "no_segments_in_window" and SQL_FP_RANK not in [s for s, _ in cur.log])
    cur = _FakeCursor({})
    r_un = compute("station_failure_pareto", cur, {"cls": "segDiePlacement", "days": 7})
    t("cmp-fp-unsupported-zero-sql", cur.log == [] and r_un["not_measurable"][0]["reason"]
      == "station_unsupported")
    gd = deg[DEMO_SERIAL]["steps"]
    ph_live = [(555, DEMO_SERIAL, datetime(2026, 8, 14, 0, 8, 2), "PASS")]
    cur = _FakeCursor({SQL_DE_PH_READ: ph_live, SQL_DE_DIE_READ: gd["DIE"]["rows"],
                       SQL_DE_PH_REAL: [(555, "varAET_Iddq", "Leak", "µA", 2.0)],
                       SQL_DE_DIE_REAL: gd["DIE_REAL"]["rows"], SQL_DE_MFL: []})
    re_ = compute_die_etest(cur, DEMO_SERIAL)
    t("cmp-de-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_DE_TEMPS, SQL_DE_PH, SQL_DE_PH_READ,
                                                  SQL_DE_DIE, SQL_DE_DIE_READ, SQL_DE_PH_REAL,
                                                  SQL_DE_DIE_REAL, SQL_DE_MFL])
    t("cmp-de-ph-params", cur.log[2][1] == (PH_CAP, DEMO_SERIAL))
    t("★ cmp-de-die-window-±1d", cur.log[4][1] == (DIE_CAP, "2026-08-13 00:08:02",
                                                   "2026-08-15 00:08:02", 555))
    t("cmp-de-valid", ok_payload("module_die_etest", re_) and re_["die_level"]["n_dies"] == 11
      and re_["module_level"][0]["values"][0]["value"] == 2)
    cur = _FakeCursor({SQL_DE_PH_READ: []})
    rmiss = compute_die_etest(cur, "NOPE")
    t("cmp-de-miss-stops", [s for s, _ in cur.log][-1] is SQL_DE_PH_READ
      and rmiss["not_measurable"][0]["reason"] == "serial_not_found")
    print(f"mes_etest selftest: {n} assertions PASS")
    return 0


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    if "--sql" in argv:
        for s in ALL_SQL():                  # ⚠ 只印、不執行（開發期不連 MES_DB）
            print(s + "\n;\n")
        return 0
    if "--build-fixtures" in argv:
        for p in write_fixtures():
            print("wrote", p)
        return 0
    print("usage: python mes_etest.py --selftest | --sql | --build-fixtures")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
