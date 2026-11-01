#!/usr/bin/env python3
"""Tendline Board — Hermes 的**段量測族**記錄級 MES 查詢核心（MES 工具包②·包 2；ROADMAP #173）。

**這是什麼**：三支唯讀工具的資料腿（`board_mcp` 消費；runtime **沒有 LLM 參與**——LLM 只叫工具，
工具跑**固定審過的參數化 SQL**，`SPEC-HERMES-TRUST.md` §7.6.1）：
  · `segment_data(code, station, which='last')` ——一顆序號在某站的當次量測值（`board.segment_data.v1`）：
        段（which=last／first／all，all 上限 20）×（開始時間、EID、判定、配方版本）× 數值變數 × 三組限值
        （spec／control／valid，逐 sgdPK＋vdfPK 對）× 逐變數旗（`out_of_spec`／`control_exceeded`／
        `invalid_measurement`／`limit_ambiguous`）＋失效碼。**`segment_out_of_spec` 不另立工具**＝本工具
        回傳內的旗子視圖（每段 `summary.out_of_spec_variables`）。
  · `station_spec_limits(cls, recipe=None)` ——某站 recipe 規格限（`board.station_spec_limits.v1`；G3 形）。
  · `station_retest(cls, days)` ——某站近 N 天同一序號多段（`board.station_retest.v1`；G5a／G5b 形）。

**不是什麼**：不是失效碼排行（`mes_etest.station_failure_pareto`）、不是 die 級電測（`mes_etest.module_die_etest`）、
不是站別變數字典（`mes_hermes.station_variables`）、不是良率。**不答「為何 FAIL」**（超規 ≠ 段 FAIL）。

**SQL 來源**＝第二支探針 `_PROBE-ASSISTANT-MES-FAMILY-2-20260923.sql`（G1b／G3a–d／G4b／G4c／G5a／G5b，
2026-09-23 Chris SSMS 實跑、38 集零錯誤）＋第三支探針 `_PROBE-ASSISTANT-MES-FAMILY-3-20260924.sql`
（`#dph`／D3／D3b／D4／D5 的**基表逐段取值形**，2026-09-24 12:33 實跑零錯誤）＋第一支探針 B1（`segment_equipment_v`
取 EID）。判讀＝兩份 DIGEST（第二支 §2、§7.2 裁決；第三支 §3）。**零新增欄出處**（每欄都在某一步 SELECT 過，見下表）。

═══ 對照探針的刻意偏離（理由寫在各常數上方）═══════════════════════════════════════════
  S1 **臨時表一律在非參數化批次建空殼、帶參數的語句只准 `INSERT INTO #x`**（`mes_hermes` 2026-09-21 live
     首跑事故；`mes_etest` E1 同條）。空殼一律 `WHERE 1=0`。
  S2 **量測值走基表、不走 EAV 大 view**：探針 G2／G4b／G4c 以 `dbo.all_segment_data_value_v`（約 22 億列）取值；
     本檔改走第三支探針 D3／D4 已跑過的基表形——`segment_real_data_t.sgrReal`／`segment_integer_data_t.sgiInteger`
     以 `stpPK` join `production_data_definition_t`（D5 也避開 EAV）。selftest 負斷言全文零 `all_segment_data_value_v`。
  S3 **段母體換形**：探針 `#g_seg` 以 `process_segment_v`（站＋窗＋`TOP 500`）取樣；本檔以 `mes_etest` 族 D 的
     基表 join（`materials_t`×`process_segments_t`×sgd×sgc×mdf×mcl，`#dph` 形）收斂——
     `segment_data`＝**序號等號＋站**（`MTL.matMaterialLotID = ?`，同 `mes_etest` E6）、
     `station_retest`＝**站＋窗**（探針的 `TOP 500` 取樣改成全窗、上限 `RT_SEG_CAP`＝下界揭露）。
  S4 **FAIL 定義收窄成 `stpTestResult = 'FAIL'`**：探針 G5 用 `res = 'FAIL' OR ok = 0`（`ResultSuccess` 是
     `process_segment_v` 的衍生欄、基表沒有）；與 `mes_etest` 同口徑。demo（取自探針）的 FAIL 仍是探針口徑＝caveat 自揭。
  S5 **限值逐段對**：探針 G4b 以 `lf.sgdPK = g.sgdPK AND lf.vdfPK = d.vdfPK` join；本檔把限值讀回 Python 再以
     **(sgdPK, vdfPK)** 對（SQL 端＝`lf.sgdPK IN (SELECT … FROM #sg_sd)` 半連接，等價收窄）。**永不取站級限值**（§7.2 ②）。
  S6 **G3c 拿掉 `OUTER APPLY … TOP (@topn_each)` 與 `LEFT(VariableCodeExpression,120)`**：工具一次一站（`= ?`）、
     `TOP (?)`＝`LIMIT_ROWS_CAP`（探針 CLN 全站限值列數）；`VariableCodeExpression`／`IniRecordNumber`／
     `IniLineNumber` 不取（工具用不到＝收窄）。`ORDER BY` 逐字（Active 先、發佈日新到舊）＋`sgdPK` 決勝。
  S7 **G5a／G5b 由 `#g_seg` 改 `#sg_rt`→`#sg_rtl` 兩段**（先逐序號彙總進表、再對表彙總＝聚合內不放子查詢，
     Msg 130／144 教訓）；分組鍵加 `mclMaterialClassID`（`#dph` 已取過的欄：CLN／AET 段含 die 段，
     G5b 實例 `C9SY03-19_R67_D1`）；`n_pass`／`n_fail_in_retest` 兩欄為同表 `CASE` 衍生（零新欄出處）。
  S8 **EID 取自 `segment_equipment_v`**（第一支探針 B1 的欄 `stpPK`／`astEquipmentID`／`astDescription`）；
     母體以 `se.stpPK IN (SELECT … FROM #sg_sd)` 半連接收斂（`mes_material` A6 的形）；一段多台＝清單。
  S9 **文字型與陣列型變數不回值**（Fable 2026-09-24 裁：第三支探針沒驗值域、不補探針）：只取
     `vdfType IN ('float','integer')` 的值；其餘型別（string／uriReference／dateTime／1DRealArray…）以 G1b 字典形
     （`segment_class_variable`×sgc×vdf，`vdfType NOT IN (…)`）**只列名**、標 `not_measurable: variable_type_unsupported`。
     基表讀回若出現非數值型或同段同變數多列（陣列跡象）⇒ Python 端同樣丟值、列名。

═══ 欄位出處（[[ssms-sql-column-provenance]]）════════════════════════════════════════════
  | 物件 | 本檔取的欄 | 探針步驟 |
  |---|---|---|
  | `dbo.process_segments_t` | `stpPK`／`sgdPK`／`matPK`／`stpStartTime`／`stpTestResult` | ③`#dph`；②`#g_seg` |
  | `dbo.process_segment_definition_t` | `sgdPK`／`sgcPK`／`sgdProcessSegmentID`／`sgdDesignation`／`sgdPublishedDate` | ③`#dph`；② G3c／G3d |
  | `dbo.process_segment_class_t` | `sgcPK`／`SegmentClassID` | ③`#dph`；② G1b |
  | `dbo.materials_t`×`material_definition_t`×`material_class_t` | `matPK`／`matMaterialLotID`／`mdfPK`／`mclPK`／`mclMaterialClassID` | ③`#dph` |
  | `dbo.segment_real_data_t` | `stpPK`／`vdfPK`／`sgrReal` | ③ D3／D3b／D4 |
  | `dbo.segment_integer_data_t` | `stpPK`／`vdfPK`／`sgiInteger` | ③ `#fmfc`／D5 |
  | `dbo.production_data_definition_t` | `vdfPK`／`vdfProductionDataID`／`FailureDescription`／`vdfUOM`／`vdfType`／`vdfClassID` | ③ D3／F0；② G1b |
  | `dbo.segment_class_variable` | `SegmentClassId`／`VariableId` | ② G1b；③ F0 |
  | `rep.numeric_variable_pass_criteria_limits_flat_rv` | `sgdPK`／`vdfPK`／`vdfProductionDataID`／`vdfUOM`／`RangeCode`／`LimitCode`／`LimitValue`／`MatchingIniRecordCount`／`sgdProcessSegmentID`／`SegmentClassID` | ② G3a／G3c／G4b |
  | `dbo.segment_equipment_v` | `stpPK`／`astEquipmentID`／`astDescription` | ① B1 |

  **絕對不 SELECT 的欄**（`ALL_SQL()` 全文負斷言）：人名（`stpPersonID`／`OperatorId`／`OperatorName`／
  `aslPersonID`／`rqsRequestor`／`sgdPublishingAuthority`）、任何 `Comment`、EAV 大 view（`all_segment_data_value_v`）、
  `stpEndTime`、`segment_string_data_t`（文字型不回值）。

★ perf 鐵則（沿用 `mes_etest`）：打 `dbo.*`／`rep.*` 的每條 SELECT／INSERT 以 `OPTION (RECOMPILE)` 收尾；碰
  `process_segments_t` 的每條都有序號等號或 `stpStartTime >= ? AND < ?` 窗；**刻意不濾 `stpEndTime`**
  （[[mes-zero-length-segments]]）。★ §24：零人名欄。★ 唯讀：唯一 DDL/DML 在 `#sg_*`（tempdb）。
★ `mes_timeline` 只 import **常數** `EQP_STATIONS`（站白名單單一出處；同 `board_export`／`core_lastprod_push`），零函式呼叫。

CLI:
    python mes_segment.py --selftest         # 純離線（讀 _fixtures/ 三份 golden ＋ 合成列），零 MES
    python mes_segment.py --sql              # 只**印**本模組會送出的全部 SQL（不執行）
    python mes_segment.py --build-fixtures   # 由探針 TSV 重建三份 fixture（純離線讀檔；fixture 不可手改）
    python mes_segment.py --demo <tool> <arg…>
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import mes_material as _mm                                 # 純常數／純函式重用（連線、_s/_int/_num/_ts、normalize_code、_dump）
import mes_etest as _me                                    # 站短名別名（CAL/CLN/AET/OQCT/OQC）單一出處
from mes_material import SchemaDivergence                  # noqa: F401  （連線單一出處＝mes_qlik，經 mes_material）
from mes_timeline import EQP_STATIONS as _EQP_STATIONS     # ⚠ 只取常數（凍結模組；零函式呼叫）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # pragma: no cover
    pass

BASE = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# 1. 人工審定常數
# =============================================================================
SCHEMA_SEGMENT = "board.segment_data.v1"
SCHEMA_LIMITS = "board.station_spec_limits.v1"
SCHEMA_RETEST = "board.station_retest.v1"

TOOLS = ("segment_data", "station_spec_limits", "station_retest")

QUERY_TIMEOUT_SEC = 180

#: 站白名單＝`mes_timeline.EQP_STATIONS`（39 站；人工審定常數）。
STATIONS = tuple(_EQP_STATIONS)
#: 短名別名（大小寫不拘）→ 全名（`mes_etest.STATION_ALIASES` 單一出處）。
STATION_ALIASES = dict(_me.STATION_ALIASES)
_SHORT_OF = {}
for _k, _v in STATION_ALIASES.items():
    _SHORT_OF.setdefault(_v, _k)
CLASS_RE = _me.CLASS_RE

#: `which` 閉集；`all` 上限 20 段（最新在前）。
WHICH = ("last", "first", "all")
ALL_CAP = 20
#: 數值型別（只有這兩種回值；其餘只列名＝Fable 09-24 裁）。
NUMERIC_TYPES = ("float", "integer")
#: 失效碼類別（整數表；第三支探針 F5 證字串／實數表零列）。
MFL_CLASS = "fcdMaterialFailureCode"
#: 探針 G3a 證**只有這一站**在 pass-criteria 限值 view 有列（另 `segStraightnessCheck`／
#: `segLamination` 0 列；其餘站沒查過 ⇒ live 照查、不預設）。
LIMIT_STATIONS_VERIFIED = ("segCleanTest",)
#: MES `fn_SplitRecipeLimitKey`：RangeCode ∈ spec/control/valid、LimitCode ∈ min/max（DIGEST §7.2）。
RANGE_CODES = ("spec", "control", "valid")
LIMIT_CODES = ("min", "max")

#: station_spec_limits：限值列上限（探針 CLN 全站限值列數）、輸出 recipe 上限。
LIMIT_ROWS_CAP = 20000
RECIPE_CAP = 50
#: station_retest：窗（天）預設 7、上限 31（超界拒絕、不 clamp）；段上限（下界揭露）；明細上限。
RETEST_DAYS_DEFAULT = 7
RETEST_DAYS_MAX = 31
RT_SEG_CAP = 300000
RETEST_TOP = 50
FAIL = "FAIL"

#: 回傳鍵集合（**查不到也長同一個形**；validator 與 smoke 以此為準）。
SEGMENT_KEYS = ("serial", "station", "station_short", "which", "found", "n_segments_at_station",
                "first_seen", "last_seen", "segments", "unsupported_variables", "not_measurable",
                "truncated", "caveats", "reason", "unavailable_reason", "diag")
SEG_ROW_KEYS = ("started_at", "result", "recipe", "material_class", "equipment",
                "limits_available", "summary", "values", "failure_codes")
SEG_SUMMARY_KEYS = ("n_values", "n_with_limits", "n_out_of_spec", "out_of_spec_variables",
                    "n_control_exceeded", "n_invalid_measurement", "n_limit_ambiguous")
VALUE_ROW_KEYS = ("variable", "label", "uom", "var_class", "value", "spec", "control", "valid",
                  "out_of_spec", "control_exceeded", "invalid_measurement", "limit_ambiguous")
LIMITS_KEYS = ("station", "station_short", "found", "recipe_filter", "n_limit_rows",
               "n_recipes_with_limits", "n_variables", "by_designation", "recipes",
               "not_measurable", "truncated", "caveats", "reason", "unavailable_reason", "diag")
RECIPE_ROW_KEYS = ("recipe", "designation", "published_at", "n_variables", "variables")
LIMIT_VAR_KEYS = ("variable", "uom", "spec", "control", "valid", "limit_ambiguous")
RETEST_KEYS = ("station", "station_short", "found", "window", "n_segments", "n_fail", "n_pass",
               "n_other", "n_serials", "n_serials_retested", "max_segments_per_serial",
               "n_fail_in_retested", "by_material_class", "top", "not_measurable", "truncated",
               "caveats", "reason", "unavailable_reason", "diag",
               # ★ 2026-09-25 QG-7 回答層（Q10：「756 PH」其實 693 die＋49 PH＋14 其他）：加性自揭拆分。
               #   `n_serials_retested` 一位元不動＝全部材料類別合計；PH 只指 module、die 另計。
               "n_ph_retested", "n_die_retested", "n_other_retested")
#: 重測拆分用的材料類別（值同 `mes_etest.PH_MLC`／`DIE_MLC`；本模組不為兩個字串多 import 一支）。
RETEST_PH_CLASS = "assyModule"
RETEST_DIE_CLASS = "sbsDie"
RETEST_TOP_KEYS = ("serial", "material_class", "n_segments", "n_fail", "first_start", "last_start")
RETEST_CLASS_KEYS = ("material_class", "n_segments", "n_serials", "n_serials_retested", "n_fail")
KEYS_BY_TOOL = {"segment_data": SEGMENT_KEYS, "station_spec_limits": LIMITS_KEYS,
                "station_retest": RETEST_KEYS}

#: 「MES 裡真的沒有」的閉集（≠ `unavailable_reason`＝我們取不到）。
REASONS = ("no_segments_in_window",)
#: `not_measurable` 理由閉集。
NOT_MEASURABLE_REASONS = ("serial_not_found_at_station", "variable_type_unsupported",
                          "no_pass_criteria_limits", "recipe_not_found")
#: `found=true` 仍可帶的 `unavailable_reason`（DIGEST §7.2 ④：限值 view 零列＝存在但沒有）。
FOUND_UNAVAILABLE_OK = ("no_pass_criteria_limits",)

#: ★ 四條硬 caveat——①–③ **逐字**抄 `_PROBE-ASSISTANT-MES-FAMILY-2-RESULT-DIGEST-20260923.md` §7.2（每支限值類回傳必帶）。
#:   ④ 2026-09-25 QG-7 回答層改寫（eval 低點 7）：DIGEST 原句「另兩站回…」是 fixture 時代措辭（探針只抽三站），
#:   live 讀起來沒頭沒尾 ⇒ 改成 live 行為的講法；語意不變（只 CLN 有限值、其餘站 found=true＋no_pass_criteria_limits）。
HARD_CAVEATS = (
    "① **超規 ≠ 段 FAIL**：`[G4c]` `stpPK=184391812` FAIL 但 Iposq 13.27 在三界之內 ⇒ 工具答「哪些變數超出 spec」、"
    "**不答「為何 FAIL」**",
    "② 限值**逐 sgdPK 對**、不取站級（`[G4b]` 窗內同變數 `n_limit_value_dst=2`）",
    "③ `MatchingIniRecordCount>1`（view header 註明 potential misidentification）的變數標 `limit_ambiguous`",
    "④ MES 數值規格限目前只有 `segCleanTest`（CLN）有登記（`[G3a]`）；其他站回 `found=true`＋"
    "`unavailable_reason=\"no_pass_criteria_limits\"`＝MES 沒登這站的數值上下限（是答案、不是查不到，"
    "也不代表全部 PASS）；要規格得剖 recipe（另一張探針單、本輪不做）",
)
#: `station_retest` 的硬 caveat（同節逐字）。
RETEST_CAVEAT = "重測原因 MES 無欄位、不推"
#: `station_retest` 的單位 caveat（2026-09-25 QG-7 回答層 Q10：「756 PH」其實 92% 是 die）。
RETEST_UNIT_CAVEAT = ("`n_serials_retested` 是**全部材料類別合計的序號數、不是 PH**：PH 只指 module"
                      "（`n_ph_retested`＝assyModule）；die 另計（`n_die_retested`＝sbsDie，die 不是 PH）；"
                      "其他類別（`n_other_retested`，如 assyEndOfLine）不自動歸 PH。三者相加＝`n_serials_retested`。")
#: 三組界的角色（DIGEST §7.2 裁；spec＝PASS 主界是 MES 原始碼，control／valid 語意為推論）。
ROLE_CAVEAT = ("只有 spec 界算超規（`out_of_spec`）；`control_exceeded` 只揭露、不計入超規數；值落在 valid 界外標 "
               "`invalid_measurement`、該變數不判超規（量測異常不是製程異常）。spec＝PASS 主界（MES 原始碼 "
               "fn_SplitRecipeLimitKey：省略 range 時預設 spec）；control／valid 的語意是推論。")

_TS_FMT = "%Y-%m-%d %H:%M:%S"

# =============================================================================
# 2. I/O 層：SQL 文本（全部模組常數＝離線可斷言；唯讀）
# =============================================================================
#: G0 物件／欄守衛。缺 ⇒ `SchemaDivergence`（停下、不回全零板）。每支工具只看自己要的那幾格。
SQL_GUARD = (
    "SELECT CASE WHEN OBJECT_ID('dbo.process_segments_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.process_segment_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.process_segment_class_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.materials_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_class_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_real_data_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_integer_data_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.production_data_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_class_variable') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_equipment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('rep.numeric_variable_pass_criteria_limits_flat_rv') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.production_data_definition_t','vdfType')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('rep.numeric_variable_pass_criteria_limits_flat_rv','MatchingIniRecordCount')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('rep.numeric_variable_pass_criteria_limits_flat_rv','RangeCode')"
    " IS NULL THEN 0 ELSE 1 END\n"
    "OPTION (RECOMPILE)")
GUARD_KEYS = ("tbx_stp", "tbx_sgd", "tbx_sgc", "tbx_mlt", "tbx_mdf", "tbx_mcl", "tbx_real",
              "tbx_int", "tbx_vdf", "tbx_scv", "view_seg_ast", "view_limits", "vdfType",
              "lim_matching", "lim_range")
_SEG3 = ("tbx_stp", "tbx_sgd", "tbx_sgc")
_MTL3 = ("tbx_mlt", "tbx_mdf", "tbx_mcl")
GUARD_NEEDS = {
    "segment_data": GUARD_KEYS,
    "station_spec_limits": ("tbx_sgd", "view_limits", "lim_matching", "lim_range"),
    "station_retest": _SEG3 + _MTL3,
}

#: 段母體 join（＝第三支探針 `#dph`／`#ddie` 的 FROM/JOIN 逐字；`mes_etest._D_JOIN` 同文）。
_S_JOIN = (
    "FROM dbo.materials_t AS MTL\n"
    "JOIN dbo.process_segments_t          AS PSG ON PSG.matPK = MTL.matPK\n"
    "JOIN dbo.process_segment_definition_t AS PSD ON PSG.sgdPK = PSD.sgdPK\n"
    "JOIN dbo.process_segment_class_t      AS PSC ON PSC.sgcPK = PSD.sgcPK\n"
    "JOIN dbo.material_definition_t       AS MLD ON MLD.mdfPK = MTL.mdfPK\n"
    "JOIN dbo.material_class_t            AS MLC ON MLC.mclPK = MLD.mclPK\n")

# ── segment_data（COUNT → #sg_sd → 讀回 → EID／實數／整數／限值／字典）──────────────────────
_SD_COLS = ("SELECT PSG.stpPK, PSG.sgdPK, PSD.sgdProcessSegmentID, PSG.stpStartTime, PSG.stpTestResult,"
            " MLC.mclMaterialClassID\n")
_SD_INS = "(stpPK, sgdPK, sgdProcessSegmentID, stpStartTime, stpTestResult, mclMaterialClassID)\n"
#: 空殼批次（S1）。`#sg_sd`＝`#dph`（③:174-185）欄位子集＋`sgdPK`／`sgdProcessSegmentID`（② G3d 已取）。
SQL_SD_TEMPS = (
    "IF OBJECT_ID('tempdb..#sg_sd') IS NOT NULL DROP TABLE #sg_sd;\n"
    + _SD_COLS + "INTO #sg_sd\n" + _S_JOIN + "WHERE 1=0;")

#: 這顆序號在此站共幾段（③ `#dph` 的 FROM／JOIN；S3 窗換成序號等號＋站；只回三個彙總值）。
SQL_SD_COUNT = (
    "SELECT n_seg = COUNT(*), first_start = MIN(PSG.stpStartTime), last_start = MAX(PSG.stpStartTime)\n"
    + _S_JOIN +
    "WHERE PSC.SegmentClassID = ?\n"
    "  AND MTL.matMaterialLotID = ?\n"
    "OPTION (RECOMPILE)")

_SD_WHERE = ("WHERE PSC.SegmentClassID = ?\n"
             "  AND MTL.matMaterialLotID = ?\n")
#: `#sg_sd`（③:174-185；S3 窗→序號等號＋站；`TOP (?)`＝1（last／first）或 `ALL_CAP+1`（all）；最新在前）。
SQL_SD_SEG_LAST = (
    "INSERT INTO #sg_sd " + _SD_INS +
    "SELECT TOP (?) PSG.stpPK, PSG.sgdPK, PSD.sgdProcessSegmentID, PSG.stpStartTime, PSG.stpTestResult,"
    " MLC.mclMaterialClassID\n" + _S_JOIN + _SD_WHERE +
    "ORDER BY PSG.stpStartTime DESC, PSG.stpPK DESC\n"
    "OPTION (RECOMPILE)")
#: 同上、最早在前（which=first）。
SQL_SD_SEG_FIRST = (
    "INSERT INTO #sg_sd " + _SD_INS +
    "SELECT TOP (?) PSG.stpPK, PSG.sgdPK, PSD.sgdProcessSegmentID, PSG.stpStartTime, PSG.stpTestResult,"
    " MLC.mclMaterialClassID\n" + _S_JOIN + _SD_WHERE +
    "ORDER BY PSG.stpStartTime ASC, PSG.stpPK ASC\n"
    "OPTION (RECOMPILE)")
SQL_SD_SEG_READ = (
    "SELECT stpPK, sgdPK, sgdProcessSegmentID, stpStartTime, stpTestResult, mclMaterialClassID\n"
    "FROM #sg_sd ORDER BY stpStartTime DESC, stpPK DESC")

#: 段的設備（① B1 的欄；S8 母體＝`#sg_sd` 半連接；一段多台會多列＝Python 攤清單）。
SQL_SD_EQP = (
    "SELECT se.stpPK, se.astEquipmentID, se.astDescription\n"
    "FROM dbo.segment_equipment_v se\n"
    "WHERE se.stpPK IN (SELECT s.stpPK FROM #sg_sd s)\n"
    "ORDER BY se.stpPK, se.astEquipmentID\n"
    "OPTION (RECOMPILE)")

_VAL_COLS = ("SELECT s.stpPK, vdf.vdfPK, vdf.vdfProductionDataID, vdf.FailureDescription, vdf.vdfUOM,"
             " vdf.vdfType, vdf.vdfClassID, {v}\n")
#: 實數值（③ D3／D4 逐列形：`segment_real_data_t` × vdf；S2 基表、零 EAV；`vdfType`／`vdfClassID`＝② G1b／③ F0）。
SQL_SD_REAL = (
    _VAL_COLS.format(v="r.sgrReal") +
    "FROM #sg_sd s\n"
    "JOIN dbo.segment_real_data_t          r   ON r.stpPK = s.stpPK\n"
    "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = r.vdfPK\n"
    "ORDER BY s.stpPK, vdf.vdfProductionDataID\n"
    "OPTION (RECOMPILE)")
#: 整數值（③ D5／`#fmfc` 形：`segment_integer_data_t` × vdf；含失效碼——Python 以 `vdfClassID` 分流）。
SQL_SD_INT = (
    _VAL_COLS.format(v="si.sgiInteger") +
    "FROM #sg_sd s\n"
    "JOIN dbo.segment_integer_data_t       si  ON si.stpPK = s.stpPK\n"
    "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = si.vdfPK\n"
    "ORDER BY s.stpPK, vdf.vdfProductionDataID\n"
    "OPTION (RECOMPILE)")
#: 限值（② G4b 的 join 鍵 `sgdPK`＋`vdfPK`；S5 改半連接讀回、Python 逐 (sgdPK, vdfPK) 對）。
SQL_SD_LIMITS = (
    "SELECT lf.sgdPK, lf.vdfPK, lf.vdfProductionDataID, lf.RangeCode, lf.LimitCode, lf.LimitValue,"
    " lf.MatchingIniRecordCount\n"
    "FROM rep.numeric_variable_pass_criteria_limits_flat_rv lf\n"
    "WHERE lf.sgdPK IN (SELECT s.sgdPK FROM #sg_sd s)\n"
    "OPTION (RECOMPILE)")
#: 站字典中**不回值**的型別（② G1b 形：`segment_class_variable`×sgc×vdf；S9 只列名）。
SQL_SD_DICT_UNSUPPORTED = (
    "SELECT p.vdfProductionDataID, p.vdfType, p.vdfClassID\n"
    "FROM dbo.segment_class_variable v\n"
    "JOIN dbo.process_segment_class_t      c ON c.sgcPK = v.SegmentClassId\n"
    "JOIN dbo.production_data_definition_t p ON p.vdfPK = v.VariableId\n"
    "WHERE c.SegmentClassID = ?\n"
    "  AND p.vdfType NOT IN ('float', 'integer')\n"
    "ORDER BY p.vdfType, p.vdfProductionDataID\n"
    "OPTION (RECOMPILE)")

# ── station_spec_limits（G3c 形）────────────────────────────────────────────────────────
#: ② G3c（:215-235）；S6 單站 `= ?`、拿掉 APPLY／`LEFT(expr)`／INI 行號、`TOP (?)`＝`LIMIT_ROWS_CAP`、
#: 排序逐字＋`sgdPK`／`RangeCode` 決勝。
SQL_SL_LIMITS = (
    "SELECT TOP (?) lf.sgdPK, lf.sgdProcessSegmentID, sgd.sgdDesignation, sgd.sgdPublishedDate,\n"
    "       lf.vdfProductionDataID, lf.vdfUOM, lf.RangeCode, lf.LimitCode, lf.LimitValue,\n"
    "       lf.MatchingIniRecordCount\n"
    "FROM rep.numeric_variable_pass_criteria_limits_flat_rv lf\n"
    "LEFT JOIN dbo.process_segment_definition_t sgd ON sgd.sgdPK = lf.sgdPK\n"
    "WHERE lf.SegmentClassID = ?\n"
    "ORDER BY CASE WHEN sgd.sgdDesignation = 'Active' THEN 0 ELSE 1 END, sgd.sgdPublishedDate DESC,\n"
    "         lf.sgdPK, lf.vdfProductionDataID, lf.RangeCode, lf.LimitCode\n"
    "OPTION (RECOMPILE)")

# ── station_retest（#sg_rt → #sg_rtl → G5a／G5b）───────────────────────────────────────────
_RT_COLS = ("SELECT PSG.stpPK, MTL.matMaterialLotID, MLC.mclMaterialClassID, PSG.stpStartTime,"
            " PSG.stpTestResult\n")
_RTL_AGG = (
    "SELECT s.matMaterialLotID, s.mclMaterialClassID,\n"
    "       n_seg       = COUNT(*),\n"
    "       n_fail      = SUM(CASE WHEN s.stpTestResult = 'FAIL' THEN 1 ELSE 0 END),\n"
    "       n_pass      = SUM(CASE WHEN s.stpTestResult = 'PASS' THEN 1 ELSE 0 END),\n"
    "       first_start = MIN(s.stpStartTime),\n"
    "       last_start  = MAX(s.stpStartTime)\n")
#: 空殼批次（S1／S7）。`#sg_rt`＝`#dph` 欄位子集；`#sg_rtl`＝G5b 的逐序號彙總形（＋mcl／n_pass）。
SQL_RT_TEMPS = (
    "IF OBJECT_ID('tempdb..#sg_rtl') IS NOT NULL DROP TABLE #sg_rtl;\n"
    "IF OBJECT_ID('tempdb..#sg_rt')  IS NOT NULL DROP TABLE #sg_rt;\n"
    + _RT_COLS + "INTO #sg_rt\n" + _S_JOIN + "WHERE 1=0;\n"
    + _RTL_AGG + "INTO #sg_rtl\nFROM #sg_rt s WHERE 1=0\n"
    "GROUP BY s.matMaterialLotID, s.mclMaterialClassID;")

#: 窗內段（③ `#dph` 形；S3 站＋窗、全窗取、`TOP (?)`＝`RT_SEG_CAP`；起點過濾 `>=`／`<`、**不碰 stpEndTime**）。
SQL_RT_SEG = (
    "INSERT INTO #sg_rt (stpPK, matMaterialLotID, mclMaterialClassID, stpStartTime, stpTestResult)\n"
    "SELECT TOP (?) PSG.stpPK, MTL.matMaterialLotID, MLC.mclMaterialClassID, PSG.stpStartTime,"
    " PSG.stpTestResult\n" + _S_JOIN +
    "WHERE PSC.SegmentClassID = ?\n"
    "  AND PSG.stpStartTime >= ? AND PSG.stpStartTime < ?\n"
    "ORDER BY PSG.stpPK DESC\n"
    "OPTION (RECOMPILE)")
#: 逐序號彙總（② G5b 的內層 `GROUP BY g.cls, g.lot`；S7 先落表＝聚合內不放子查詢；非參數化）。
SQL_RT_LOT = (
    "INSERT INTO #sg_rtl (matMaterialLotID, mclMaterialClassID, n_seg, n_fail, n_pass,"
    " first_start, last_start)\n"
    + _RTL_AGG +
    "FROM #sg_rt s\n"
    "GROUP BY s.matMaterialLotID, s.mclMaterialClassID")
#: G5a（② :366-374）對 `#sg_rtl` 彙總；S7 分組鍵換 mcl（單站時 `cls` 分組拿掉）。
SQL_RT_SUMMARY = (
    "SELECT x.mclMaterialClassID, n_seg = SUM(x.n_seg), n_lot = COUNT(*),\n"
    "       n_lot_retest = SUM(CASE WHEN x.n_seg > 1 THEN 1 ELSE 0 END),\n"
    "       max_seg_per_lot = MAX(x.n_seg), n_seg_fail = SUM(x.n_fail), n_seg_pass = SUM(x.n_pass),\n"
    "       n_fail_in_retest = SUM(CASE WHEN x.n_seg > 1 THEN x.n_fail ELSE 0 END)\n"
    "FROM #sg_rtl x\n"
    "GROUP BY x.mclMaterialClassID\n"
    "ORDER BY n_seg DESC")
#: G5b（② :377-386）：同一顆同站 >1 段；`TOP (?)`＝`RETEST_TOP`；排序段數多→FAIL 多→序號。
SQL_RT_TOP = (
    "SELECT TOP (?) x.matMaterialLotID, x.mclMaterialClassID, x.n_seg, x.n_fail, x.first_start,"
    " x.last_start\n"
    "FROM #sg_rtl x\n"
    "WHERE x.n_seg > 1\n"
    "ORDER BY x.n_seg DESC, x.n_fail DESC, x.matMaterialLotID")


def ALL_SQL():
    """本模組會送出的**全部 SQL**（selftest 負斷言的唯一清單；新增查詢必須登記在這裡）。"""
    return (SQL_GUARD,
            SQL_SD_TEMPS, SQL_SD_COUNT, SQL_SD_SEG_LAST, SQL_SD_SEG_FIRST, SQL_SD_SEG_READ,
            SQL_SD_EQP, SQL_SD_REAL, SQL_SD_INT, SQL_SD_LIMITS, SQL_SD_DICT_UNSUPPORTED,
            SQL_SL_LIMITS,
            SQL_RT_TEMPS, SQL_RT_SEG, SQL_RT_LOT, SQL_RT_SUMMARY, SQL_RT_TOP)


#: `board_mcp._TOOL_QUERIES` 用的真名（grep 得到）。
TOOL_QUERY_NAMES = {
    "segment_data": ("mes_segment.SQL_SD_COUNT", "mes_segment.SQL_SD_SEG_LAST",
                     "mes_segment.SQL_SD_SEG_FIRST", "mes_segment.SQL_SD_EQP",
                     "mes_segment.SQL_SD_REAL", "mes_segment.SQL_SD_INT",
                     "mes_segment.SQL_SD_LIMITS", "mes_segment.SQL_SD_DICT_UNSUPPORTED"),
    "station_spec_limits": ("mes_segment.SQL_SL_LIMITS",),
    "station_retest": ("mes_segment.SQL_RT_SEG", "mes_segment.SQL_RT_LOT",
                       "mes_segment.SQL_RT_SUMMARY", "mes_segment.SQL_RT_TOP"),
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


def _real(v):
    """實數值 → 7 位有效數字（MES `real` 是 float32：`27.6100006103516` → `27.61`）；整數值回 int。"""
    f = _num(v)
    if isinstance(f, float):
        f = float(f"{f:.7g}")
        return int(f) if f.is_integer() else f
    return f


def normalize_station(cls):
    """站 → `(全名, err)`。短名（CAL/CLN/AET/OQCT/OQC）或全名（大小寫不拘）；白名單＝`EQP_STATIONS`。"""
    hint = "station: a Tendline Board station such as CAL / CLN / segCleanTest (see list_stations)"
    if not isinstance(cls, str) or not cls.strip():
        return None, {"error": "missing_station", "hint": hint}
    s = cls.strip()
    if s.upper() in STATION_ALIASES:
        return STATION_ALIASES[s.upper()], None
    if not CLASS_RE.match(s):
        return None, {"error": "bad_station", "given": cls, "hint": hint}
    for full in STATIONS:
        if full.lower() == s.lower():
            return full, None
    return None, {"error": "station_not_in_whitelist", "given": cls, "hint": hint}


def normalize_serial(code):
    """序號 → `(normalized, err)`（`mes_material.normalize_code(kind="serial")` 單一出處：等號比對、白名單）。"""
    return _mm.normalize_code(code, kind="serial")


def normalize_segment_args(code=None, station=None, which=None):
    """`segment_data` 參數 → `(key, err)`；`key={"serial", "cls", "which"}`。"""
    ser, err = normalize_serial(code)
    if err:
        return None, err
    cls, err = normalize_station(station)
    if err:
        return None, err
    w = "last" if which in (None, "") else (which.strip().lower() if isinstance(which, str) else which)
    if w not in WHICH:
        return None, {"error": "bad_which", "given": which, "hint": "which: last / first / all"}
    return {"serial": ser, "cls": cls, "which": w}, None


def normalize_limits_args(cls=None, recipe=None):
    """`station_spec_limits` 參數 → `(key, err)`；`key={"cls", "recipe"}`（recipe 形如 QR910001/12，選填）。"""
    norm, err = normalize_station(cls)
    if err:
        return None, err
    r = None
    if recipe not in (None, ""):
        if not isinstance(recipe, str) or not re.match(r"^[A-Za-z0-9_./-]{1,75}$", recipe.strip()):
            return None, {"error": "bad_recipe", "given": recipe,
                          "hint": "recipe looks like QR910001/12"}
        r = recipe.strip()
    return {"cls": norm, "recipe": r}, None


def normalize_retest_args(cls=None, days=None):
    """`station_retest` 參數 → `(key, err)`；days 1..31（超界拒絕、不 clamp）。"""
    norm, err = normalize_station(cls)
    if err:
        return None, err
    d = RETEST_DAYS_DEFAULT if days in (None, "") else _int(days)
    if d is None or not (1 <= d <= RETEST_DAYS_MAX):
        return None, {"error": "bad_days", "given": days,
                      "hint": f"days must be an integer 1..{RETEST_DAYS_MAX} (refused, not clamped)"}
    return {"cls": norm, "days": d}, None


def _outside(v, lo, hi):
    """值是否落在 [lo, hi] 之外；兩界皆無 ⇒ None（無從判）。"""
    if v is None or (lo is None and hi is None):
        return None
    return bool((lo is not None and v < lo) or (hi is not None and v > hi))


def build_limit_index(limit_rows):
    """限值列 `(sgdPK, vdfPK, var, RangeCode, LimitCode, LimitValue, MatchingIniRecordCount)` →
    `{(sgdPK, varkey): {"spec": {"min","max"}, "control": …, "valid": …, "ambiguous": bool}}`。

    varkey＝`vdfPK`（live）；vdfPK 缺（demo 取自探針無此欄）⇒ 退回變數名。同一 (sgd, var, range, limit) 出現
    **多個不同值** ⇒ 該界置 None 且 `ambiguous`；`MatchingIniRecordCount>1` ⇒ `ambiguous`（界照給）。"""
    raw, unknown = {}, set()
    for r in limit_rows:
        sgd, vdf, var = _int(r[0]), _int(r[1]), _s(r[2])
        rng, lim = (_s(r[3]) or "").lower(), (_s(r[4]) or "").lower()
        if rng not in RANGE_CODES or lim not in LIMIT_CODES:
            unknown.add(f"{rng}/{lim}")
            continue
        key = (sgd, vdf if vdf is not None else var)
        e = raw.setdefault(key, {"vals": {}, "ambiguous": False})
        e["vals"].setdefault((rng, lim), set()).add(_real(r[5]))
        if (_int(r[6]) or 0) > 1:
            e["ambiguous"] = True
    idx = {}
    for key, e in raw.items():
        out = {rc: {"min": None, "max": None} for rc in RANGE_CODES}
        amb = e["ambiguous"]
        for (rng, lim), vs in e["vals"].items():
            vs = {v for v in vs if v is not None}
            if len(vs) == 1:
                out[rng][lim] = next(iter(vs))
            elif len(vs) > 1:
                amb = True
        out["ambiguous"] = amb
        idx[key] = out
    return idx, sorted(unknown)


def judge(value, lim):
    """值 × 限 → (out_of_spec, control_exceeded, invalid_measurement)。無限 ⇒ 三個 None。
    落在 valid 外 ⇒ `invalid_measurement=True`、其餘兩旗 None（量測異常不判製程）。"""
    if lim is None or value is None:
        return None, None, None
    inv = _outside(value, lim["valid"]["min"], lim["valid"]["max"])
    if inv:
        return None, None, True
    return (_outside(value, lim["spec"]["min"], lim["spec"]["max"]),
            _outside(value, lim["control"]["min"], lim["control"]["max"]), inv)


# ---- 骨架（鍵集合固定；查不到也長同一個形）--------------------------------------
def blank(tool, key, *, found=False, reason=None, unavailable_reason=None, diag=None):
    d = dict(diag or {})
    k = key if isinstance(key, dict) else {}
    cls = k.get("cls")
    if tool == "segment_data":
        return {"serial": k.get("serial"), "station": cls, "station_short": _SHORT_OF.get(cls),
                "which": k.get("which"), "found": found, "n_segments_at_station": None,
                "first_seen": None, "last_seen": None, "segments": [],
                "unsupported_variables": {"n": 0, "by_type": {}}, "not_measurable": [],
                "truncated": False, "caveats": [], "reason": reason,
                "unavailable_reason": unavailable_reason, "diag": d}
    if tool == "station_spec_limits":
        return {"station": cls, "station_short": _SHORT_OF.get(cls), "found": found,
                "recipe_filter": k.get("recipe"), "n_limit_rows": None,
                "n_recipes_with_limits": None, "n_variables": None, "by_designation": {},
                "recipes": [], "not_measurable": [], "truncated": False, "caveats": [],
                "reason": reason, "unavailable_reason": unavailable_reason, "diag": d}
    if tool == "station_retest":
        return {"station": cls, "station_short": _SHORT_OF.get(cls), "found": found,
                "window": {"from": None, "to": None, "days": k.get("days")},
                "n_segments": None, "n_fail": None, "n_pass": None, "n_other": None,
                "n_serials": None, "n_serials_retested": None, "max_segments_per_serial": None,
                "n_fail_in_retested": None, "by_material_class": [], "top": [],
                "not_measurable": [], "truncated": False, "caveats": [], "reason": reason,
                "unavailable_reason": unavailable_reason, "diag": d,
                "n_ph_retested": None, "n_die_retested": None, "n_other_retested": None}
    raise KeyError(tool)


def precheck(tool, key):
    """不需 MES 就能下結論的情況 → 回完整 payload；否則 None（目前恆 None：參數錯誤由 normalize 擋）。"""
    if tool not in TOOLS:
        raise KeyError(tool)
    return None


# ---- segment_data -----------------------------------------------------------------
def _caveats_segment(*, which, n_total, truncated, any_limits, n_unsupported, cls):
    c = list(HARD_CAVEATS)
    c.append(ROLE_CAVEAT)
    c.append("數值只回 float／integer 型變數；文字型（string／uriReference／dateTime，含字串型設備失效碼）與陣列型"
             "（1DRealArray）只列名（`unsupported_variables`）、不回值（第三支探針沒驗值域）。")
    c.append("MES 只寫有量到的變數：登記了但這段沒有列＝**沒寫**，不是 0。失效碼只取整數表類別 "
             "fcdMaterialFailureCode（PASS 段通常不帶碼）。")
    c.append("`equipment` 是 MES 在該段登記的設備（一段可掛主機＋模組多台）。")
    if which != "all" and n_total and n_total > 1:
        c.append(f"本序號在此站共 {n_total} 段（含重測）；只展開最{'新' if which == 'last' else '早'}一段"
                 f"（which=all 可看全部，上限 {ALL_CAP} 段）。")
    if not any_limits:
        if cls in LIMIT_STATIONS_VERIFIED:
            c.append("這段的配方版本在 MES pass-criteria 限值 view 沒有列（同站其他版本有）⇒ 只回值、不判旗。")
        else:
            c.append("此站（這段的配方版本）在 MES pass-criteria 限值 view 沒有列 ⇒ 只回值、不判旗"
                     "（要規格得剖 recipe，另一張探針單）。")
    if n_unsupported:
        c.append(f"此站另有 {n_unsupported} 個非數值型變數只列名、不回值。")
    if truncated:
        c.append(f"⚠ 段數超過 {ALL_CAP}（`truncated=true`）＝只列最新 {ALL_CAP} 段。")
    return c


def derive_segment_data(key, count_row, seg_rows, ast_rows, real_rows, int_rows, limit_rows,
                        dict_rows, *, fetched_at=None, elapsed_s=None, caps=None):
    """列 → `board.segment_data.v1`。列一律照 SQL 欄位順序（fixture 同形）：
    count `(n_seg, first_start, last_start)`；seg `(stpPK, sgdPK, recipe, start, result, mcl)`；
    ast `(stpPK, eid, desc)`；real／int `(stpPK, vdfPK, var, desc, uom, type, class, value)`；
    limit `(sgdPK, vdfPK, var, RangeCode, LimitCode, LimitValue, MatchingIniRecordCount)`；dict `(var, type, class)`。"""
    caps = dict(caps or {})
    which = key["which"]
    diag = {"fetched_at": _fmt(fetched_at), "elapsed_s": elapsed_s, "cap_hit": caps,
            "n_seg_rows_raw": len(seg_rows), "n_value_rows_raw": len(real_rows) + len(int_rows),
            "n_limit_rows_raw": len(limit_rows)}
    out = blank("segment_data", key, diag=diag)
    n_total = _int(count_row[0]) if count_row else len(seg_rows)
    if not seg_rows:
        out["n_segments_at_station"] = n_total or 0
        out["not_measurable"] = [{
            "field": "serial", "reason": "serial_not_found_at_station",
            "note": "MES 查無這個序號在此站的段：序號可能不對、還沒做到這站，或這站記在它的上／下游實體"
                    "（單元序號 vs module 序號——可先用 serial_genealogy 看它走過哪些站）。"}]
        out["caveats"] = ["序號以等號比對（不做模糊搜尋）。"]
        return out
    lim_idx, unknown_codes = build_limit_index(limit_rows)
    if unknown_codes:
        diag["unknown_limit_codes"] = unknown_codes
    ast = {}
    for r in ast_rows:
        lst = ast.setdefault(_int(r[0]), [])
        item = {"eid": _s(r[1]), "description": _s(r[2])}
        if item not in lst:
            lst.append(item)
    # 值：同段同變數多列 ⇒ 陣列跡象、不回值；非數值型 ⇒ 不回值
    unsupported = {}
    grouped = {}
    for kind, rows in (("real", real_rows), ("int", int_rows)):
        for r in rows:
            pk, vdf, var = _int(r[0]), _int(r[1]), _s(r[2])
            g = grouped.setdefault((pk, var), {"vdf": vdf, "label": _s(r[3]), "uom": _s(r[4]),
                                               "type": _s(r[5]), "class": _s(r[6]), "vals": []})
            g["vals"].append(_real(r[7]) if kind == "real" else _int(r[7]))
    for r in dict_rows:
        unsupported.setdefault(_s(r[1]) or "(null)", set()).add(_s(r[0]))
    per_seg, mfl = {}, {}
    for (pk, var), g in grouped.items():
        if g["class"] == MFL_CLASS:
            for v in g["vals"]:
                mfl.setdefault(pk, []).append({"code": var, "label": g["label"], "value": v})
            continue
        if (g["type"] or "").lower() not in NUMERIC_TYPES:
            unsupported.setdefault(g["type"] or "(null)", set()).add(var)
            continue
        if len(g["vals"]) > 1:
            unsupported.setdefault("multi_valued", set()).add(var)
            continue
        per_seg.setdefault(pk, []).append((var, g))
    segs = []
    any_limits = False
    for r in seg_rows:
        pk, sgd = _int(r[0]), _int(r[1])
        rows_out = []
        summ = {k: 0 for k in SEG_SUMMARY_KEYS}
        summ["out_of_spec_variables"] = []
        seg_has_lim = False
        for var, g in sorted(per_seg.get(pk, []), key=lambda x: str(x[0])):
            lim = lim_idx.get((sgd, g["vdf"] if g["vdf"] is not None else var))
            if lim is None and g["vdf"] is not None:
                lim = lim_idx.get((sgd, var))
            v = g["vals"][0]
            oos, ctl, inv = judge(v, lim)
            amb = (lim["ambiguous"] if lim else None)
            rows_out.append({"variable": var, "label": g["label"], "uom": g["uom"],
                             "var_class": g["class"], "value": v,
                             "spec": dict(lim["spec"]) if lim else None,
                             "control": dict(lim["control"]) if lim else None,
                             "valid": dict(lim["valid"]) if lim else None,
                             "out_of_spec": oos, "control_exceeded": ctl,
                             "invalid_measurement": inv, "limit_ambiguous": amb})
            summ["n_values"] += 1
            if lim:
                seg_has_lim = True
                summ["n_with_limits"] += 1
            if oos:
                summ["n_out_of_spec"] += 1
                summ["out_of_spec_variables"].append(var)
            if ctl:
                summ["n_control_exceeded"] += 1
            if inv:
                summ["n_invalid_measurement"] += 1
            if amb:
                summ["n_limit_ambiguous"] += 1
        any_limits = any_limits or seg_has_lim
        codes = sorted(mfl.get(pk, []), key=lambda x: (-(x["value"] or 0), str(x["code"])))
        segs.append({"started_at": _ts(r[3]), "result": _s(r[4]), "recipe": _s(r[2]),
                     "material_class": _s(r[5]), "equipment": list(ast.get(pk, [])),
                     "limits_available": seg_has_lim, "summary": summ, "values": rows_out,
                     "failure_codes": codes})
    order = sorted(range(len(segs)), key=lambda i: (_dt(segs[i]["started_at"]) or datetime.min,
                                                     _int(seg_rows[i][0]) or 0), reverse=True)
    segs = [segs[i] for i in order]
    truncated = which == "all" and ((n_total or 0) > ALL_CAP or len(segs) > ALL_CAP)
    segs = segs[:ALL_CAP] if which == "all" else segs[:1]
    unsup = {t: sorted(x for x in vs if x) for t, vs in sorted(unsupported.items())}
    n_unsup = sum(len(v) for v in unsup.values())
    nm = []
    if n_unsup:
        nm.append({"field": "unsupported_variables", "reason": "variable_type_unsupported",
                   "note": "非 float／integer 型（文字／陣列等）或同段多值的變數只列名、不回值（Fable 2026-09-24 裁）。"})
    if not any_limits:
        nm.append({"field": "segments[].values[].out_of_spec", "reason": "no_pass_criteria_limits",
                   "note": "這段的配方版本在 MES pass-criteria 限值 view 沒有列 ⇒ 旗子一律 null（不是「沒超規」）。"})
    count_first = _ts(count_row[1]) if count_row else None
    count_last = _ts(count_row[2]) if count_row else None
    out.update({"found": True, "n_segments_at_station": n_total, "first_seen": count_first,
                "last_seen": count_last, "segments": segs,
                "unsupported_variables": {"n": n_unsup, "by_type": unsup},
                "not_measurable": nm, "truncated": truncated,
                "caveats": _caveats_segment(which=which, n_total=n_total, truncated=truncated,
                                            any_limits=any_limits, n_unsupported=n_unsup,
                                            cls=key["cls"])})
    return out


# ---- station_spec_limits ----------------------------------------------------------
def derive_spec_limits(key, rows, *, fetched_at=None, elapsed_s=None, caps=None):
    """G3c 形的列 `(sgdPK, recipe, designation, published, var, uom, RangeCode, LimitCode, LimitValue,
    MatchingIniRecordCount)` → `board.station_spec_limits.v1`。"""
    caps = dict(caps or {})
    diag = {"fetched_at": _fmt(fetched_at), "elapsed_s": elapsed_s, "cap_hit": caps,
            "n_rows_raw": len(rows)}
    out = blank("station_spec_limits", key, diag=diag)
    cls = key["cls"]
    if not rows:
        out.update({"found": True, "n_limit_rows": 0, "n_recipes_with_limits": 0, "n_variables": 0,
                    "unavailable_reason": "no_pass_criteria_limits",
                    "caveats": list(HARD_CAVEATS) + [
                        f"{cls} 在 MES pass-criteria 限值 view（rep.numeric_variable_pass_criteria_limits_flat_rv）零列＝"
                        "這站的配方沒有登記數值規格限（探針 G3d：逐版本含 Inactive／Obsolete 全部 0）；"
                        "要規格得剖 recipe（另一張探針單、本輪不做）。"]})
        return out
    rec = {}
    for r in rows:
        sgd, name = _int(r[0]), _s(r[1])
        rk = sgd if sgd is not None else name
        e = rec.setdefault(rk, {"recipe": name, "designation": _s(r[2]),
                                "published_at": _ts(r[3]), "lim_rows": [], "uom": {}})
        e["lim_rows"].append((sgd, None, _s(r[4]), r[6], r[7], r[8], r[9]))
        e["uom"].setdefault(_s(r[4]), _s(r[5]))
    by_des = {}
    for e in rec.values():
        d = e["designation"] or "(null)"
        by_des[d] = by_des.get(d, 0) + 1
    all_vars = set()
    recipes = []
    for e in rec.values():
        idx, _unk = build_limit_index(e["lim_rows"])
        vs = []
        for (_sgd, var), lim in sorted(idx.items(), key=lambda kv: str(kv[0][1])):
            all_vars.add(var)
            vs.append({"variable": var, "uom": e["uom"].get(var), "spec": dict(lim["spec"]),
                       "control": dict(lim["control"]), "valid": dict(lim["valid"]),
                       "limit_ambiguous": lim["ambiguous"]})
        recipes.append({"recipe": e["recipe"], "designation": e["designation"],
                        "published_at": e["published_at"], "n_variables": len(vs), "variables": vs})
    recipes.sort(key=lambda x: (0 if x["designation"] == "Active" else 1,
                                -(_dt(x["published_at"]) or datetime.min).timestamp()
                                if _dt(x["published_at"]) else 0, str(x["recipe"])))
    nm = []
    caveats = list(HARD_CAVEATS) + [ROLE_CAVEAT]
    if key.get("recipe"):
        pick = [x for x in recipes if (x["recipe"] or "").lower() == key["recipe"].lower()]
        if not pick:
            nm.append({"field": "recipe", "reason": "recipe_not_found",
                       "note": f"此站的限值 view 裡沒有配方版本 {key['recipe']}（可能拼錯、或那一版沒有數值規格）。"})
    else:
        pick = [x for x in recipes if x["designation"] == "Active"] or recipes
        if len(pick) < len(recipes):
            caveats.append(f"預設只列 Active 版本（{len(pick)} 版）；非 Active 版本只計數（`by_designation`）。"
                           "指定 recipe 可看任一版本。")
    truncated = len(pick) > RECIPE_CAP or any(bool(v) for v in caps.values())
    if truncated:
        caveats.append(f"⚠ 列數撞上限（`truncated=true`）⇒ 清單是下界（recipe 上限 {RECIPE_CAP}）。")
    caveats.append("同一變數在不同配方版本的限值可以不同（探針 G4b：同窗同變數 2 種值）⇒ 判某一段請用 segment_data"
                   "（逐段依它自己的配方版本對限）。")
    out.update({"found": True, "n_limit_rows": len(rows), "n_recipes_with_limits": len(recipes),
                "n_variables": len(all_vars), "by_designation": dict(sorted(by_des.items())),
                "recipes": pick[:RECIPE_CAP], "not_measurable": nm, "truncated": truncated,
                "caveats": caveats})
    return out


# ---- station_retest ---------------------------------------------------------------
def derive_retest(key, summary_rows, top_rows, *, window=None, fetched_at=None, elapsed_s=None,
                  caps=None):
    """G5a 形 `(mcl, n_seg, n_lot, n_lot_retest, max_seg, n_fail, n_pass, n_fail_in_retest)`＋
    G5b 形 `(lot, mcl, n_seg, n_fail, first, last)` → `board.station_retest.v1`。"""
    caps = dict(caps or {})
    window = dict(window or {})
    diag = {"fetched_at": _fmt(fetched_at), "elapsed_s": elapsed_s, "cap_hit": caps,
            "n_top_rows_raw": len(top_rows)}
    out = blank("station_retest", key, diag=diag)
    out["window"] = {"from": _fmt(window.get("from")), "to": _fmt(window.get("to")),
                     "days": window.get("days", key.get("days"))}

    def _sum(i):
        vals = [_int(r[i]) for r in summary_rows]
        return None if any(v is None for v in vals) else sum(vals)

    n_seg = _sum(1) or 0
    if n_seg == 0:
        out.update({"reason": "no_segments_in_window", "n_segments": 0,
                    "caveats": ["本窗此站沒有任何段（以段開始時間計）；請加大 days 或確認站別。"]})
        return out
    n_fail, n_pass = _sum(5), _sum(6)
    by_class = [{"material_class": _s(r[0]), "n_segments": _int(r[1]), "n_serials": _int(r[2]),
                 "n_serials_retested": _int(r[3]), "n_fail": _int(r[5])} for r in summary_rows]
    maxes = [_int(r[4]) for r in summary_rows if _int(r[4]) is not None]
    top = [{"serial": _s(r[0]), "material_class": _s(r[1]), "n_segments": _int(r[2]),
            "n_fail": _int(r[3]), "first_start": _ts(r[4]), "last_start": _ts(r[5])}
           for r in top_rows]
    n_ret = _sum(3)
    # ★ 加性拆分（QG-7 回答層 Q10）：PH＝assyModule、die＝sbsDie、其餘類別（含類別為空）＝other；
    #   三者相加恆＝`n_serials_retested`（全窗總數不知道 ⇒ 三欄一起 None，不猜）。
    if n_ret is None:
        n_ph_ret = n_die_ret = n_oth_ret = None
    else:
        n_ph_ret = sum(c["n_serials_retested"] or 0 for c in by_class
                       if c["material_class"] == RETEST_PH_CLASS)
        n_die_ret = sum(c["n_serials_retested"] or 0 for c in by_class
                        if c["material_class"] == RETEST_DIE_CLASS)
        n_oth_ret = n_ret - n_ph_ret - n_die_ret
    top_trunc = n_ret is not None and n_ret > len(top)
    seg_trunc = any(bool(v) for v in caps.values())
    truncated = top_trunc or seg_trunc
    caveats = [
        "重測 ≠ 先 FAIL：" + RETEST_CAVEAT + "（探針 G5b：有序號重測 4 段但 0 FAIL）⇒ `n_segments` 與 `n_fail` 分兩欄，"
        "不推「因為 FAIL 才重測」。",
        "段數是測試段（同一顆重測會算多段），不是產品顆數（PH）；窗以段開始時間計（含起點、不含終點）。"
        "`n_fail`＝MES 段判定為 FAIL 的段數。",
        "`by_material_class` 分材料類別：部分站（如 CLN／AET）的段同時包含 module 段與 die 段，"
        "die 批號形如 `<substrate>_R<nn>_D<n>`。",
        RETEST_UNIT_CAVEAT,
    ]
    if top_trunc:
        caveats.append(f"⚠ 只列段數最多的前 {RETEST_TOP} 顆（`truncated=true`）⇒ `top` 是下界；"
                       "`n_serials_retested` 是全窗總數。")
    if seg_trunc:
        caveats.append(f"⚠ 窗內段數撞上限（`truncated=true`）⇒ 所有計數是**下界**（只涵蓋最新的段）。")
    out.update({"found": True, "n_segments": n_seg, "n_fail": n_fail, "n_pass": n_pass,
                "n_other": (n_seg - n_fail - n_pass) if (n_fail is not None and n_pass is not None)
                else None,
                "n_serials": _sum(2), "n_serials_retested": n_ret,
                "max_segments_per_serial": max(maxes) if maxes else None,
                "n_fail_in_retested": _sum(7), "by_material_class": by_class, "top": top,
                "truncated": truncated, "caveats": caveats,
                "n_ph_retested": n_ph_ret, "n_die_retested": n_die_ret,
                "n_other_retested": n_oth_ret})
    return out


# ---- validator（形狀檢查單一出處；smoke／selftest 委派它）--------------------------
_FORBIDDEN_OUT = ("stpPersonID", "OperatorName", "OperatorId", "matPK", "stpPK", "sgdPK", "vdfPK",
                  "stpRSID")


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
        if rec["found"] and rec["reason"]:
            return False
        if rec["found"] and rec["unavailable_reason"] and (
                rec["unavailable_reason"] not in FOUND_UNAVAILABLE_OK
                or tool != "station_spec_limits" or rec["n_limit_rows"] != 0):
            return False
        for nm in rec["not_measurable"]:
            if nm["reason"] not in NOT_MEASURABLE_REASONS:
                return False
        # caveats 例外：硬 caveat 逐字引 DIGEST（含 `stpPK=184391812`／「逐 sgdPK 對」字樣＝文字、不是內部鍵）
        blob = json.dumps({k: v for k, v in body.items() if k != "caveats"}, ensure_ascii=False,
                          default=str)
        if any(f in blob for f in _FORBIDDEN_OUT):
            return False
        if "\n" in "".join(_mm._walk_strings(body)):
            return False
        if tool == "segment_data":
            if rec["which"] is not None and rec["which"] not in WHICH:
                return False
            if len(rec["segments"]) > ALL_CAP:
                return False
            if rec["which"] in ("last", "first") and len(rec["segments"]) > 1:
                return False
            for sg in rec["segments"]:
                if set(sg) != set(SEG_ROW_KEYS) or set(sg["summary"]) != set(SEG_SUMMARY_KEYS):
                    return False
                for v in sg["values"]:
                    if set(v) != set(VALUE_ROW_KEYS):
                        return False
                    # ★ 只有 spec 算超規；invalid 時不判超規
                    if v["invalid_measurement"] and v["out_of_spec"] is not None:
                        return False
                    if v["spec"] is None and v["out_of_spec"] is not None:
                        return False
                if sg["summary"]["n_out_of_spec"] != sum(1 for v in sg["values"] if v["out_of_spec"]):
                    return False
        if tool == "station_spec_limits":
            for r in rec["recipes"]:
                if set(r) != set(RECIPE_ROW_KEYS):
                    return False
                for v in r["variables"]:
                    if set(v) != set(LIMIT_VAR_KEYS):
                        return False
        if tool == "station_retest":
            for r in rec["top"]:
                if set(r) != set(RETEST_TOP_KEYS):
                    return False
            for r in rec["by_material_class"]:
                if set(r) != set(RETEST_CLASS_KEYS):
                    return False
            if len(rec["top"]) > RETEST_TOP:
                return False
            # ★ QG-7 Q10：拆分三欄要嘛全 None、要嘛相加＝全窗重測序號數（不得各說各話）
            _split = (rec["n_ph_retested"], rec["n_die_retested"], rec["n_other_retested"])
            if any(v is not None for v in _split):
                if any(v is None for v in _split) or rec["n_serials_retested"] is None \
                        or sum(_split) != rec["n_serials_retested"]:
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


def compute_segment_data(cur, key):
    t0 = time.perf_counter()
    fetch_guard(cur, "segment_data")
    cls, ser, which = key["cls"], key["serial"], key["which"]
    cur.execute(SQL_SD_COUNT, cls, ser)
    count_row = cur.fetchone()
    n_total = _int(count_row[0]) if count_row else 0
    seg = ast = real = ints = lims = dct = []
    if n_total:
        cur.execute(SQL_SD_TEMPS)
        top = (ALL_CAP + 1) if which == "all" else 1
        cur.execute(SQL_SD_SEG_FIRST if which == "first" else SQL_SD_SEG_LAST, int(top), cls, ser)
        cur.execute(SQL_SD_SEG_READ)
        seg = _rows(cur)
        if seg:
            cur.execute(SQL_SD_EQP)
            ast = _rows(cur)
            cur.execute(SQL_SD_REAL)
            real = _rows(cur)
            cur.execute(SQL_SD_INT)
            ints = _rows(cur)
            cur.execute(SQL_SD_LIMITS)
            lims = _rows(cur)
            cur.execute(SQL_SD_DICT_UNSUPPORTED, cls)
            dct = _rows(cur)
    return derive_segment_data(key, count_row, seg, ast, real, ints, lims, dct,
                               fetched_at=datetime.now(),
                               elapsed_s=round(time.perf_counter() - t0, 3),
                               caps={"segments": which == "all" and n_total > ALL_CAP})


def compute_spec_limits(cur, key):
    t0 = time.perf_counter()
    fetch_guard(cur, "station_spec_limits")
    cur.execute(SQL_SL_LIMITS, int(LIMIT_ROWS_CAP), key["cls"])
    rows = _rows(cur)
    return derive_spec_limits(key, rows, fetched_at=datetime.now(),
                              elapsed_s=round(time.perf_counter() - t0, 3),
                              caps={"limit_rows": len(rows) >= LIMIT_ROWS_CAP})


def compute_retest(cur, key, *, now=None):
    t0 = time.perf_counter()
    fetch_guard(cur, "station_retest")
    now = now or datetime.now()
    frm = (now - timedelta(days=int(key["days"]))).strftime(_TS_FMT)
    to = now.strftime(_TS_FMT)                  # ★ 上界永不超過現在（CONCEPT §21.1 髒日期）
    cur.execute(SQL_RT_TEMPS)
    cur.execute(SQL_RT_SEG, int(RT_SEG_CAP), key["cls"], frm, to)
    n_ins = getattr(cur, "rowcount", -1)
    cur.execute(SQL_RT_LOT)
    cur.execute(SQL_RT_SUMMARY)
    summ = _rows(cur)
    top = []
    if summ:
        cur.execute(SQL_RT_TOP, int(RETEST_TOP))
        top = _rows(cur)
    n_seg = sum(_int(r[1]) or 0 for r in summ)
    return derive_retest(key, summ, top, window={"from": frm, "to": to, "days": int(key["days"])},
                         fetched_at=now, elapsed_s=round(time.perf_counter() - t0, 3),
                         caps={"segments": n_seg >= RT_SEG_CAP or (isinstance(n_ins, int)
                                                                   and n_ins >= RT_SEG_CAP)})


def compute(tool, cur, key):
    """`board_data.get_mes_segment` 的 live 分派點（`key` 已經過 normalize；呼叫端先跑 `precheck`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    if tool == "segment_data":
        return compute_segment_data(cur, key)
    if tool == "station_spec_limits":
        return compute_spec_limits(cur, key)
    if tool == "station_retest":
        return compute_retest(cur, key)
    raise KeyError(tool)


# =============================================================================
# 5. demo：fixture（`_fixtures/mes_segment_<tool>.json`）→ 同一支 derive_*
# =============================================================================
FIXTURE_FILES = {t: f"mes_segment_{t}.json" for t in TOOLS}
DEMO_SERIAL = "J0SYN17"
DEMO_STATION = "segCleanTest"

_DEMO_CAVEAT_SEG = ("（demo）樣本＝探針 G4c 的一段 FAIL 段（CLN、2026-08-13 09:10:04）；序號 {ser} 是以 G5b 同站同秒首段"
                    "推得（推論、探針未直接 join）。G4c 只抽前 20 列 ⇒ 只有 5 個變數、Channel4 只有 spec 上限；"
                    "設備、配方版本、失效碼、非數值型字典探針沒抽（空）。demo 不依 which 重算（問的是 {which}）。")
_DEMO_CAVEAT_LIM = ("（demo）探針 G3c 每站只抽前 60 列（Active 先、新版先）⇒ 只涵蓋少數版本；"
                    "live 全站 {n_rows} 列／{n_sgd} 版本／{n_var} 變數（G3a）。")
_DEMO_CAVEAT_RT = ("（demo）探針 G5 每站只取窗內最近 500 段樣本（非全窗；live 取全窗），FAIL 口徑＝`stpTestResult='FAIL' "
                   "OR ResultSuccess=0`（live 只看 stpTestResult）、材料類別未抽；demo 不依 days 重算（問的是 {req} 天）。")


def demo_lookup(tool, doc, key):
    """fixture 裡找樣本 → 跑**同一支** `derive_*`。查不到回 `None`（呼叫端回 `not_in_demo_fixture`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    gs = (doc or {}).get("golden") or []
    for g in gs:
        s = g["steps"]
        if tool == "segment_data":
            if g["key"]["serial"].upper() == key["serial"].upper() and g["key"]["cls"] == key["cls"]:
                out = derive_segment_data(
                    key, s["COUNT"]["rows"][0], s["SEG"]["rows"], s["EQP"]["rows"], s["REAL"]["rows"],
                    s["INT"]["rows"], s["LIMITS"]["rows"], s["DICT"]["rows"],
                    fetched_at=g["captured_at"], caps={"segments": False})
                out["caveats"].append(_DEMO_CAVEAT_SEG.format(ser=key["serial"], which=key["which"]))
                return out
        elif tool == "station_spec_limits":
            if g["key"] == key["cls"]:
                out = derive_spec_limits(key, s["LIMITS"]["rows"], fetched_at=g["captured_at"],
                                         caps=g["caps"])
                ref = g["reference_G3a"]
                if out["n_limit_rows"]:
                    out["caveats"].append(_DEMO_CAVEAT_LIM.format(
                        n_rows=ref["n_rows"], n_sgd=ref["n_sgd"], n_var=ref["n_var"]))
                return out
        elif tool == "station_retest":
            if g["key"] == key["cls"]:
                out = derive_retest(key, s["SUMMARY"]["rows"], s["TOP"]["rows"], window=g["window"],
                                    fetched_at=g["captured_at"], caps=g["caps"])
                out["diag"]["days_requested"] = key["days"]
                if out["found"]:
                    out["caveats"].append(_DEMO_CAVEAT_RT.format(req=key["days"]))
                return out
    return None


# =============================================================================
# 6. fixture builder（讀探針 TSV；純離線、零 MES）
# =============================================================================
PROBE_TSV = "_PROBE-ASSISTANT-MES-FAMILY-2-RESULT.tsv"
PROBE_SQL = "_PROBE-ASSISTANT-MES-FAMILY-2-20260923.sql"
_STEP_PREFIX = {"0e": "0e context", "G1a": "G1a variable dictionary summary",
                "G3a": "G3a spec-limit coverage", "G3b": "G3b LimitCode",
                "G3c": "G3c spec-limit detail", "G3d": "G3d spec-limit rows per PR",
                "G4b": "G4b value vs limit", "G4c": "G4c raw value-limit",
                "G5a": "G5a retest summary", "G5b": "G5b retest detail"}
DEMO_PSGPK = "900000001"


def parse_probe_tsv(path):
    """探針 TSV（SSMS 多結果集貼成一檔）→ `{tag: {"header", "rows"}}`（`mes_equipment` 同形；NULL→None）。"""
    sets, cur = {}, None
    with io.open(path, encoding="utf-8-sig") as fh:
        lines = fh.read().split("\n")
    for ln in lines:
        ln = ln.rstrip("\r")
        if ln.startswith("step\t"):
            cur = {"header": ln.split("\t"), "rows": []}
            continue
        if cur is None or not ln.strip():
            continue
        cells = ln.split("\t")
        tag = next((t for t, p in _STEP_PREFIX.items() if cells[0].startswith(p)), None)
        if tag is None:
            continue
        if len(cells) != len(cur["header"]):
            raise ValueError(f"{tag}: ragged row {cells[:3]}")
        s = sets.setdefault(tag, {"header": cur["header"], "rows": []})
        s["rows"].append([None if c == "NULL" else c for c in cells])
    return sets


def _dicts(t, tag):
    return [dict(zip(t[tag]["header"], r)) for r in t[tag]["rows"]]


def build_fixtures(base=BASE):
    t = parse_probe_tsv(os.path.join(base, PROBE_TSV))
    ctx = dict(zip(t["0e"]["header"], t["0e"]["rows"][0]))
    anchor = ctx["win_to"][:19]
    common = {"captured_at": anchor, "source_probe": PROBE_SQL,
              "_provenance": "domain-side read-only query 2026-08-13; " + PROBE_SQL +
                             "; builder = mes_segment.py --build-fixtures (do not hand-edit)"}
    stations = (ctx["cls1"], ctx["cls2"], ctx["cls3"])
    g3a = {d["station"]: d for d in _dicts(t, "G3a")}
    out = {}
    # ── segment_data：G4c 的 FAIL 段 900000001（CLN）；序號以 G5b 同站同秒首段推得 ──
    g4c = [d for d in _dicts(t, "G4c") if d["stpPK"] == DEMO_PSGPK]
    seg_start = g4c[0]["seg_start"]
    g5b = [d for d in _dicts(t, "G5b") if d["station"] == DEMO_STATION and d["first_start"] == seg_start]
    if len(g5b) != 1:
        raise ValueError(f"serial inference for stpPK {DEMO_PSGPK} is not unique: {len(g5b)}")
    ser = g5b[0]["lot"]
    g3c = _dicts(t, "G3c")
    meta = {}
    for d in g3c:
        if d["vdfProductionDataID"]:
            meta.setdefault(d["vdfProductionDataID"], (d["vdfUOM"], d["vdfType"], d["vdfClassID"]))
    real, lims, seen = [], [], set()
    for d in g4c:
        var = d["var_id"]
        if var not in seen:
            seen.add(var)
            uom, typ, klass = meta[var]
            real.append([DEMO_PSGPK, None, var, None, uom, typ, klass, d["value_num"]])
        # MatchingIniRecordCount：G4c 沒取；G3a 證本站全站 max＝1
        lims.append([None, None, var, d["RangeCode"], d["LimitCode"], d["LimitValue"],
                     g3a[DEMO_STATION]["max_matching_ini"]])
    out["segment_data"] = {
        "schema_id": SCHEMA_SEGMENT, "demo_serial": ser, "demo_station": DEMO_STATION,
        "golden": [{"key": {"serial": ser, "cls": DEMO_STATION}, **common,
                    "serial_inference": f"G5b {DEMO_STATION} lot whose first_start == G4c seg_start "
                                        f"{seg_start} (stpPK {DEMO_PSGPK}); unique match, not a probe join",
                    "steps": {
                        "COUNT": {"source_step": "G5b (n_seg/first_start/last_start of that lot; "
                                                 "window- and 500-segment-capped)",
                                  "columns": ["n_seg", "first_start", "last_start"],
                                  "rows": [[g5b[0]["n_seg"], g5b[0]["first_start"],
                                            g5b[0]["last_start"]]]},
                        "SEG": {"source_step": "G4c (stpPK/seg_start/seg_result; sgdPK/recipe/mcl not "
                                               "in probe)",
                                "columns": ["stpPK", "sgdPK", "sgdProcessSegmentID", "stpStartTime",
                                            "stpTestResult", "mclMaterialClassID"],
                                "rows": [[DEMO_PSGPK, None, None, seg_start, g4c[0]["seg_result"],
                                          None]]},
                        "EQP": {"source_step": "(not sampled)",
                                "columns": ["stpPK", "astEquipmentID", "astDescription"], "rows": []},
                        "REAL": {"source_step": "G4c value_num (TOP 20 rows ⇒ 5 variables); "
                                                "uom/type/class from G3c",
                                 "columns": ["stpPK", "vdfPK", "vdfProductionDataID", "FailureDescription",
                                             "vdfUOM", "vdfType", "vdfClassID", "sgrReal"],
                                 "rows": real},
                        "INT": {"source_step": "(not sampled)",
                                "columns": ["stpPK", "vdfPK", "vdfProductionDataID", "FailureDescription",
                                            "vdfUOM", "vdfType", "vdfClassID", "sgiInteger"],
                                "rows": []},
                        "LIMITS": {"source_step": "G4c RangeCode/LimitCode/LimitValue of that segment; "
                                                  "MatchingIniRecordCount = G3a max_matching_ini",
                                   "columns": ["sgdPK", "vdfPK", "vdfProductionDataID", "RangeCode",
                                               "LimitCode", "LimitValue", "MatchingIniRecordCount"],
                                   "rows": lims},
                        "DICT": {"source_step": "(not sampled; G1a counts in _reference)",
                                 "columns": ["vdfProductionDataID", "vdfType", "vdfClassID"],
                                 "rows": []}}}],
        "_reference": {"G1a": [[d["station"], d["var_class"], d["var_type"], d["n_var"]]
                               for d in _dicts(t, "G1a")]},
        "_note": "one CLN FAIL segment from probe G4c (TOP 20 ⇒ partial); demo ignores `which`."}
    # ── station_spec_limits：三站（CLN＝G3c 前 60 列；另兩站 0 列）──
    sl = []
    for cls in stations:
        rows = [[None, d["seg_def"], d["sgdDesignation"], d["sgdPublishedDate"],
                 d["vdfProductionDataID"], d["vdfUOM"], d["RangeCode"], d["LimitCode"],
                 d["LimitValue"], d["MatchingIniRecordCount"]]
                for d in g3c if d["station"] == cls and d["seg_def"] is not None]
        a = g3a[cls]
        sl.append({"key": cls, **common,
                   "reference_G3a": {"n_rows": int(a["n_rows"]), "n_sgd": int(a["n_sgd"]),
                                     "n_var": int(a["n_var"]), "max_matching_ini": a["max_matching_ini"]},
                   "caps": {"limit_rows": int(a["n_rows"]) > len(rows)},
                   "steps": {"LIMITS": {
                       "source_step": "G3c (TOP 60 per station; sgdPK not in probe ⇒ grouped by recipe)",
                       "columns": ["sgdPK", "sgdProcessSegmentID", "sgdDesignation", "sgdPublishedDate",
                                   "vdfProductionDataID", "vdfUOM", "RangeCode", "LimitCode",
                                   "LimitValue", "MatchingIniRecordCount"], "rows": rows}}})
    out["station_spec_limits"] = {
        "schema_id": SCHEMA_LIMITS, "golden": sl,
        "_reference": {"G3b": [[d["station"], d["RangeCode"], d["LimitCode"], d["n_rows"], d["n_var"],
                                d["n_sgd"], d["limit_value_min"], d["limit_value_max"],
                                d["sample_full_code"]] for d in _dicts(t, "G3b")],
                       "G3d_cln": [[d["seg_def"], d["sgdDesignation"], d["n_limit_rows"]]
                                   for d in _dicts(t, "G3d") if d["station"] == DEMO_STATION]},
        "_note": "CLN rows = probe G3c TOP 60; the other two probed stations have 0 rows (G3a/G3d)."}
    # ── station_retest：三站（G5a 彙總＋G5b 明細；500 段樣本）──
    rt = []
    for cls in stations:
        a = next(d for d in _dicts(t, "G5a") if d["station"] == cls)
        summ = [[None, a["n_seg"], a["n_lot"], a["n_lot_retest"], a["max_seg_per_lot"],
                 a["n_seg_fail"], None, None]]
        det = [[d["lot"], None, d["n_seg"], d["n_fail"], d["first_start"], d["last_start"]]
               for d in _dicts(t, "G5b") if d["station"] == cls]
        det.sort(key=lambda r: (-int(r[2]), -int(r[3]), r[0]))
        rt.append({"key": cls, **common,
                   "window": {"from": ctx["win_from"][:19], "to": ctx["win_to"][:19], "days": 7},
                   "caps": {"segments": True},
                   "steps": {
                       "SUMMARY": {"source_step": "G5a (500-segment sample; mcl/n_pass/n_fail_in_retest "
                                                  "not in probe)",
                                   "columns": ["mclMaterialClassID", "n_seg", "n_lot", "n_lot_retest",
                                               "max_seg_per_lot", "n_seg_fail", "n_seg_pass",
                                               "n_fail_in_retest"], "rows": summ},
                       "TOP": {"source_step": "G5b (same sample; sorted n_seg desc, n_fail desc, lot)",
                               "columns": ["matMaterialLotID", "mclMaterialClassID", "n_seg", "n_fail",
                                           "first_start", "last_start"], "rows": det[:RETEST_TOP]}}})
    out["station_retest"] = {
        "schema_id": SCHEMA_RETEST, "golden": rt,
        "_note": "probe G5 = most recent 500 segments per station in the 7-day window (sample, not full)."}
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

    def __init__(self, answers, guard=None, one=None):
        self.log, self.answers = [], answers
        self._last = None
        self.guard = guard or tuple(1 for _ in GUARD_KEYS)
        self.one = one or {}
        self.rowcount = -1

    def execute(self, sql, *params):
        self.log.append((sql, params))
        self._last = sql

    def fetchone(self):
        if self._last is SQL_GUARD:
            return self.guard
        return self.one.get(self._last)

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
        if head.startswith(("SELECT", "INSERT")) and ("dbo." in s or "rep." in s) and "WHERE 1=0" not in s:
            t("recompile", s.rstrip().endswith("OPTION (RECOMPILE)"), s[:50])
        if "?" in s:
            t("param-no-select-into", not re.search(r"(?<!INSERT )INTO #", s), s[:50])
            t("param-no-create", "CREATE " not in s and "DROP " not in s, s[:50])
        if "WHERE 1=0" in s:
            t("temps-unparameterized", "?" not in s, s[:50])
    t("temps-only-in-shell-batches",
      [s for s in sqls if re.search(r"(?<!INSERT )INTO #", s)] == [SQL_SD_TEMPS, SQL_RT_TEMPS])
    blob = "\n".join(sqls)
    for bad in ("stpPersonID", "OperatorId", "OperatorName", "aslPersonID", "rqsRequestor",
                "PublishingAuthority", "Comment", "rqsChangeLog", "sgrString",
                "segment_string_data_t"):
        t(f"never-{bad}", bad not in blob)
    t("★ never-EAV-all_segment_data_value_v（S2）", "all_segment_data_value_v" not in blob)
    t("never-process_segment_v（S3 基表）", "process_segment_v" not in blob)
    t("no-end-filter", "stpEndTime" not in blob)
    t("no-LEFT(", "LEFT(" not in blob.upper())
    t("no-LIKE", " LIKE " not in blob)
    for s in sqls:
        t("no-subquery-in-aggregate",
          not re.search(r"(COUNT|SUM|MAX|MIN|AVG)\s*\(\s*\(\s*SELECT", s), s[:40])
        m = re.search(r"GROUP BY([^\n]*)", s)
        if m:
            t("no-subquery-in-group-by", "SELECT" not in m.group(1), s[:40])
    t("sd-serial-eq", all("MTL.matMaterialLotID = ?" in x
                          for x in (SQL_SD_COUNT, SQL_SD_SEG_LAST, SQL_SD_SEG_FIRST)))
    t("sd-station-eq", all("PSC.SegmentClassID = ?" in x
                           for x in (SQL_SD_COUNT, SQL_SD_SEG_LAST, SQL_SD_SEG_FIRST, SQL_RT_SEG)))
    t("sd-last-desc-first-asc", "stpStartTime DESC" in SQL_SD_SEG_LAST
      and "stpStartTime ASC" in SQL_SD_SEG_FIRST)
    t("sd-real-base-table（S2）", "dbo.segment_real_data_t" in SQL_SD_REAL and "r.sgrReal" in SQL_SD_REAL)
    t("sd-int-base-table（S2）", "dbo.segment_integer_data_t" in SQL_SD_INT
      and "si.sgiInteger" in SQL_SD_INT)
    t("sd-values-psgpk-join", "ON r.stpPK = s.stpPK" in SQL_SD_REAL and "ON si.stpPK = s.stpPK" in SQL_SD_INT)
    t("sd-limits-per-sgd（S5）", "lf.sgdPK IN (SELECT s.sgdPK FROM #sg_sd s)" in SQL_SD_LIMITS
      and "lf.vdfPK" in SQL_SD_LIMITS and "SegmentClassID" not in SQL_SD_LIMITS)
    t("sd-limits-matching-ini", "MatchingIniRecordCount" in SQL_SD_LIMITS)
    t("sd-ast-semijoin（S8）", "se.stpPK IN (SELECT s.stpPK FROM #sg_sd s)" in SQL_SD_EQP)
    t("★ sd-dict-unsupported-only（S9）", "p.vdfType NOT IN ('float', 'integer')" in SQL_SD_DICT_UNSUPPORTED)
    t("sl-single-station-eq", "lf.SegmentClassID = ?" in SQL_SL_LIMITS
      and "TOP (?)" in SQL_SL_LIMITS)
    t("sl-active-first（G3c 排序）", "CASE WHEN sgd.sgdDesignation = 'Active' THEN 0 ELSE 1 END" in SQL_SL_LIMITS)
    t("rt-window-halfopen", "PSG.stpStartTime >= ? AND PSG.stpStartTime < ?" in SQL_RT_SEG)
    t("rt-lot-table-first（S7）", "FROM #sg_rt s" in SQL_RT_LOT and "?" not in SQL_RT_LOT
      and "FROM #sg_rtl x" in SQL_RT_SUMMARY and "FROM #sg_rtl x" in SQL_RT_TOP)
    t("rt-fail-psgtestresult（S4）", "s.stpTestResult = 'FAIL'" in SQL_RT_LOT and "ResultSuccess" not in blob)
    t("rt-retest-having", "WHERE x.n_seg > 1" in SQL_RT_TOP)
    t("guard-keys", len(GUARD_KEYS) == SQL_GUARD.count("CASE WHEN")
      and all(set(v) <= set(GUARD_KEYS) for v in GUARD_NEEDS.values()))
    t("query-names-real", all(hasattr(mod, q.split(".", 1)[1])
                              for qs in TOOL_QUERY_NAMES.values() for q in qs)
      and set(TOOL_QUERY_NAMES) == set(TOOLS))
    t("query-names-in-all-sql", all(getattr(mod, q.split(".", 1)[1]) in sqls
                                    for qs in TOOL_QUERY_NAMES.values() for q in qs))
    t("caps", ALL_CAP == 20 and RETEST_TOP == 50 and RETEST_DAYS_MAX == 31)
    t("hard-caveats-4-verbatim", len(HARD_CAVEATS) == 4
      and "超規 ≠ 段 FAIL" in HARD_CAVEATS[0] and "逐 sgdPK 對" in HARD_CAVEATS[1]
      and "MatchingIniRecordCount>1" in HARD_CAVEATS[2]
      and "no_pass_criteria_limits" in HARD_CAVEATS[3])

    # ---- ② 參數正規化 -----------------------------------------------------------------
    t("stations-whitelist-39", len(STATIONS) == 39 and "segCleanTest" in STATIONS)
    t("st-alias-cln", normalize_station("cln") == ("segCleanTest", None))
    t("st-alias-oqc", normalize_station("OQC")[0] == "segFinalOQC")
    t("st-full-case", normalize_station("segcalibrationtest")[0] == "segCalibrationTest")
    t("st-not-in-whitelist", normalize_station("segGrout")[1]["error"] == "station_not_in_whitelist")
    for bad in ("x'; --", "seg%", "FluidClean", "seg A"):
        t(f"st-bad-{bad}", normalize_station(bad)[1]["error"] == "bad_station")
    t("st-missing", normalize_station(None)[1]["error"] == "missing_station")
    k, e = normalize_segment_args(" J0SYN03 ", "CAL")
    t("sd-args-default-last", e is None and k == {"serial": "J0SYN03", "cls": "segCalibrationTest",
                                                   "which": "last"})
    t("sd-args-which-all", normalize_segment_args("B1", "CLN", "ALL")[0]["which"] == "all")
    t("sd-args-bad-which", normalize_segment_args("B1", "CLN", "latest")[1]["error"] == "bad_which")
    t("sd-args-bad-serial", normalize_segment_args("B0%", "CLN")[1]["error"] == "bad_serial")
    t("sd-args-missing-station", normalize_segment_args("B1", "")[1]["error"] == "missing_station")
    t("sl-args", normalize_limits_args("CLN", "QR910001/12")[0] == {"cls": "segCleanTest",
                                                                     "recipe": "QR910001/12"})
    t("sl-args-bad-recipe", normalize_limits_args("CLN", "PR'; --")[1]["error"] == "bad_recipe")
    t("rt-args-default", normalize_retest_args("CLN")[0] == {"cls": "segCleanTest", "days": 7})
    for bad in (0, 32, "x"):
        t(f"rt-days-refused-{bad}", normalize_retest_args("CLN", bad)[1]["error"] == "bad_days")
    t("real-7sig", _real("27.6100006103516") == 27.61 and _real("5") == 5 and _real(None) is None)

    # ---- ③ 純函式：限值與判旗 --------------------------------------------------------
    L = [(1, 10, "vA", "spec", "min", "7", "1"), (1, 10, "vA", "spec", "max", "25", "1"),
         (1, 10, "vA", "control", "max", "20", "1"), (1, 10, "vA", "valid", "min", "1", "1"),
         (1, 10, "vA", "valid", "max", "100", "1"),
         (1, 11, "vB", "spec", "max", "75", "2"),                    # MatchingIniRecordCount>1
         (1, 12, "vC", "spec", "max", "5", "1"), (1, 12, "vC", "spec", "max", "6", "1"),  # 兩個不同值
         (2, 10, "vA", "spec", "max", "30", "1"),                    # 另一版本
         (1, 10, "vA", "weird", "max", "1", "1")]
    idx, unk = build_limit_index(L)
    t("lim-index-per-sgd", idx[(1, 10)]["spec"] == {"min": 7, "max": 25}
      and idx[(2, 10)]["spec"] == {"min": None, "max": 30})
    t("★ lim-ambiguous-matching-ini", idx[(1, 11)]["ambiguous"] is True
      and idx[(1, 11)]["spec"]["max"] == 75)
    t("★ lim-ambiguous-distinct-values", idx[(1, 12)]["ambiguous"] is True
      and idx[(1, 12)]["spec"]["max"] is None)
    t("lim-unknown-codes-diag", unk == ["weird/max"] and idx[(1, 10)]["ambiguous"] is False)
    la = idx[(1, 10)]
    t("judge-in", judge(10, la) == (False, False, False))
    t("★ judge-oos-spec-only", judge(27.61, la) == (True, True, False))
    t("★ judge-control-not-oos", judge(22, la) == (False, True, False))
    t("★ judge-invalid-not-oos", judge(150, la) == (None, None, True))
    t("judge-below-min", judge(3, la)[0] is True)
    t("judge-no-lim", judge(3, None) == (None, None, None))
    t("judge-one-sided", judge(80, idx[(1, 11)])[0] is True and judge(80, idx[(1, 11)])[2] is None)

    # ---- ④ 純函式：segment_data -------------------------------------------------------
    key = {"serial": "B1", "cls": "segCleanTest", "which": "last"}
    cnt = (3, "2026-09-20 08:00:00", "2026-09-23 09:00:00")
    seg = [(900, 1, "PR1/1", "2026-09-23 09:00:00", "FAIL", "assyModule")]
    ast = [(900, "EID-14732", "CLN#1"), (900, "EID-14732", "CLN#1"), (900, "EID-99999", "module")]
    real = [(900, 10, "vA", "A pres", "kPa", "float", "fcdSegmentData", "27.6100006103516"),
            (900, 11, "vB", "B cur", "µA", "float", "fcdSegmentData", "13.27"),
            (900, 13, "vArr", "arr", "mm", "1DRealArray", "fcdSegmentData", "1.0"),
            (900, 14, "vDup", "dup", "mm", "float", "fcdSegmentData", "1.0"),
            (900, 14, "vDup", "dup", "mm", "float", "fcdSegmentData", "2.0"),
            (900, 15, "vNoLim", "n", "s", "float", "fcdSegmentData", "4")]
    ints = [(900, 20, "pP", "param", "none", "integer", "fcdProcessParameter", "3"),
            (900, 30, "mflX", "X fail", "none", "integer", MFL_CLASS, "1"),
            (900, 31, "mflY", "Y fail", "none", "integer", MFL_CLASS, "4")]
    dct = [("varS1", "string", "fcdSegmentData"), ("eqfE1", "string", "dimEquipmentFailureCode"),
           ("uri1", "uriReference", "fcdSegmentData")]
    sd = derive_segment_data(key, cnt, seg, ast, real, ints, L, dct)
    t("sd-validator", ok_payload("segment_data", sd), sd)
    s0 = sd["segments"][0]
    vals = {v["variable"]: v for v in s0["values"]}
    t("sd-found", sd["found"] and sd["n_segments_at_station"] == 3 and len(sd["segments"]) == 1)
    t("sd-eid-dedup-list", s0["equipment"] == [{"eid": "EID-14732", "description": "CLN#1"},
                                               {"eid": "EID-99999", "description": "module"}])
    t("★ sd-out-of-spec-flag", vals["vA"]["out_of_spec"] is True and vals["vA"]["value"] == 27.61
      and vals["vA"]["spec"] == {"min": 7, "max": 25})
    t("★ sd-limit-ambiguous", vals["vB"]["limit_ambiguous"] is True and vals["vB"]["out_of_spec"] is False)
    t("sd-no-lim-null-flags", vals["vNoLim"]["spec"] is None and vals["vNoLim"]["out_of_spec"] is None
      and vals["vNoLim"]["limit_ambiguous"] is None)
    t("sd-int-param-value", vals["pP"]["value"] == 3 and vals["pP"]["var_class"] == "fcdProcessParameter")
    t("★ sd-array-not-returned", "vArr" not in vals and "vArr" in sd["unsupported_variables"]["by_type"]
      ["1DRealArray"])
    t("★ sd-multi-valued-not-returned", "vDup" not in vals
      and sd["unsupported_variables"]["by_type"]["multi_valued"] == ["vDup"])
    t("★ sd-string-listed-not-valued", sd["unsupported_variables"]["by_type"]["string"]
      == ["eqfE1", "varS1"] and "varS1" not in vals and sd["unsupported_variables"]["n"] == 5)
    t("★ sd-not-measurable-type", any(x["reason"] == "variable_type_unsupported"
                                      for x in sd["not_measurable"]))
    t("sd-mfl-split", [c["code"] for c in s0["failure_codes"]] == ["mflY", "mflX"]
      and "mflX" not in vals)
    t("sd-summary", s0["summary"]["n_out_of_spec"] == 1 and s0["summary"]["out_of_spec_variables"] == ["vA"]
      and s0["summary"]["n_control_exceeded"] == 1 and s0["summary"]["n_limit_ambiguous"] == 1
      and s0["summary"]["n_with_limits"] == 2 and s0["summary"]["n_values"] == 4)
    t("sd-hard-caveats", all(h in sd["caveats"] for h in HARD_CAVEATS) and ROLE_CAVEAT in sd["caveats"])
    t("sd-retest-caveat-last", any("共 3 段" in c and "最新" in c for c in sd["caveats"]))
    t("sd-no-internal-keys", all(k_ not in json.dumps({k: v for k, v in sd.items() if k != "caveats"})
                                 for k_ in ("stpPK", "sgdPK", "vdfPK")))
    t("sd-limits-available", s0["limits_available"] is True and not any(
        x["reason"] == "no_pass_criteria_limits" for x in sd["not_measurable"]))
    # 限值換 sgd（段的 sgdPK=2）⇒ 只對到 sgd 2 的限
    sd2 = derive_segment_data(key, cnt, [(900, 2, "PR1/2", "2026-09-23 09:00:00", "FAIL", None)],
                              [], real[:1], [], L, [])
    t("★ sd-limits-by-segment-sgd", sd2["segments"][0]["values"][0]["spec"] == {"min": None, "max": 30}
      and sd2["segments"][0]["values"][0]["out_of_spec"] is False)
    # invalid
    sd3 = derive_segment_data(key, cnt, seg, [], [(900, 10, "vA", "A", "kPa", "float",
                                                   "fcdSegmentData", "150")], [], L, [])
    v3 = sd3["segments"][0]["values"][0]
    t("★ sd-invalid-not-oos", v3["invalid_measurement"] is True and v3["out_of_spec"] is None
      and sd3["segments"][0]["summary"]["n_out_of_spec"] == 0
      and sd3["segments"][0]["summary"]["n_invalid_measurement"] == 1 and ok_payload("segment_data", sd3))
    # 非 CLN：無限值
    kc = {"serial": "J0SYN03", "cls": "segCalibrationTest", "which": "last"}
    sd4 = derive_segment_data(kc, (1, "2026-09-22 10:00:00", "2026-09-22 10:00:00"),
                              [(5, 7, "PR9/1", "2026-09-22 10:00:00", "FAIL", "assyUnit")],
                              [], [(5, 40, "varCAL_X", "x", "mm", "float", "fcdSegmentData", "1.5")],
                              [], [], [])
    t("★ sd-non-cln-no-limits", sd4["found"] and sd4["segments"][0]["limits_available"] is False
      and any(x["reason"] == "no_pass_criteria_limits" for x in sd4["not_measurable"])
      and sd4["segments"][0]["values"][0]["out_of_spec"] is None and ok_payload("segment_data", sd4))
    t("sd-non-cln-caveat", any("剖 recipe" in c for c in sd4["caveats"]))
    # which=all 上限
    ka = dict(key, which="all")
    many = [(1000 + i, 1, "PR1/1", f"2026-09-{(i % 20) + 1:02d} 10:{i % 60:02d}:00", "PASS", None)
            for i in range(21)]
    sda = derive_segment_data(ka, (25, "2026-09-01 10:00:00", "2026-09-21 10:00:00"), many,
                              [], [], [], [], [], caps={"segments": True})
    t("★ sd-all-cap-20", len(sda["segments"]) == ALL_CAP and sda["truncated"]
      and any("只列最新 20 段" in c for c in sda["caveats"]) and ok_payload("segment_data", sda))
    t("sd-all-newest-first", _dt(sda["segments"][0]["started_at"]) >= _dt(sda["segments"][-1]["started_at"]))
    sdf = derive_segment_data(dict(key, which="first"), cnt, seg, [], [], [], [], [])
    t("sd-first-caveat", any("最早" in c for c in sdf["caveats"]) and len(sdf["segments"]) == 1)
    sd0 = derive_segment_data(key, (0, None, None), [], [], [], [], [], [])
    t("★ sd-not-found", sd0["found"] is False and sd0["not_measurable"][0]["reason"]
      == "serial_not_found_at_station" and ok_payload("segment_data", sd0))
    bad = json.loads(json.dumps(sd))
    bad["segments"][0]["values"][0]["out_of_spec"] = True
    bad["segments"][0]["values"][0]["invalid_measurement"] = True
    t("validator-rejects-invalid-and-oos", not ok_payload("segment_data", bad))
    bad2 = json.loads(json.dumps(sd))
    bad2["segments"] = bad2["segments"] * 2
    t("validator-rejects-two-segs-for-last", not ok_payload("segment_data", bad2))
    bad3 = json.loads(json.dumps(sd))
    bad3["diag"]["stpPK"] = 1
    t("validator-rejects-internal-key", not ok_payload("segment_data", bad3))
    bad4 = json.loads(json.dumps(sd))
    bad4["not_measurable"] = [{"field": "x", "reason": "whatever"}]
    t("validator-rejects-open-reason", not ok_payload("segment_data", bad4))
    bad5 = json.loads(json.dumps(sd))
    bad5["segments"][0]["summary"]["n_out_of_spec"] = 7
    t("validator-rejects-summary-mismatch", not ok_payload("segment_data", bad5))
    bad6 = json.loads(json.dumps(sd))
    bad6["unavailable_reason"] = "no_pass_criteria_limits"
    t("validator-rejects-found-unavailable-on-sd", not ok_payload("segment_data", bad6))

    # ---- ⑤ 純函式：spec limits ---------------------------------------------------------
    kl = {"cls": "segCleanTest", "recipe": None}
    SL = [(1, "PR1/2", "Active", "2026-09-02 00:00:00", "vA", "kPa", "spec", "max", "25", "1"),
          (1, "PR1/2", "Active", "2026-09-02 00:00:00", "vA", "kPa", "spec", "min", "7", "1"),
          (1, "PR1/2", "Active", "2026-09-02 00:00:00", "vA", "kPa", "valid", "max", "100", "1"),
          (1, "PR1/2", "Active", "2026-09-02 00:00:00", "vB", "µA", "control", "max", "40", "2"),
          (2, "PR1/1", "Obsolete", "2020-01-01 00:00:00", "vA", "kPa", "spec", "max", "30", "1"),
          (3, "PR1/3", "Active", "2026-03-25 00:00:00", "vA", "kPa", "spec", "max", "26", "1")]
    ls = derive_spec_limits(kl, SL)
    t("sl-validator", ok_payload("station_spec_limits", ls), ls)
    t("sl-active-only-default", [r["recipe"] for r in ls["recipes"]] == ["PR1/2", "PR1/3"]
      and ls["by_designation"] == {"Active": 2, "Obsolete": 1} and ls["n_recipes_with_limits"] == 3)
    r0 = ls["recipes"][0]
    t("sl-min-max-by-range", r0["variables"][0]["spec"] == {"min": 7, "max": 25}
      and r0["variables"][0]["valid"] == {"min": None, "max": 100}
      and r0["variables"][0]["uom"] == "kPa")
    t("★ sl-limit-ambiguous", r0["variables"][1]["limit_ambiguous"] is True
      and r0["variables"][1]["control"]["max"] == 40)
    t("sl-counts", ls["n_limit_rows"] == 6 and ls["n_variables"] == 2)
    t("sl-hard-caveats", all(h in ls["caveats"] for h in HARD_CAVEATS))
    t("sl-active-caveat", any("只列 Active" in c for c in ls["caveats"]))
    lr = derive_spec_limits(dict(kl, recipe="pr1/1"), SL)
    t("sl-recipe-filter-any-designation", [r["recipe"] for r in lr["recipes"]] == ["PR1/1"]
      and lr["recipes"][0]["variables"][0]["spec"]["max"] == 30)
    lnf = derive_spec_limits(dict(kl, recipe="PR9/9"), SL)
    t("sl-recipe-not-found", lnf["recipes"] == [] and lnf["not_measurable"][0]["reason"]
      == "recipe_not_found" and ok_payload("station_spec_limits", lnf))
    l0 = derive_spec_limits({"cls": "segLamination", "recipe": None}, [])
    t("★ sl-non-cln-no-pass-criteria", l0["found"] is True and l0["n_limit_rows"] == 0
      and l0["unavailable_reason"] == "no_pass_criteria_limits" and l0["recipes"] == []
      and ok_payload("station_spec_limits", l0))
    t("sl-non-cln-caveat", any("剖 recipe" in c for c in l0["caveats"])
      and all(h in l0["caveats"] for h in HARD_CAVEATS))
    many_r = [(i, f"PR{i}/1", "Active", "2026-01-01 00:00:00", "vA", "kPa", "spec", "max", "1", "1")
              for i in range(RECIPE_CAP + 3)]
    lt = derive_spec_limits(kl, many_r)
    t("sl-recipe-cap", len(lt["recipes"]) == RECIPE_CAP and lt["truncated"]
      and ok_payload("station_spec_limits", lt))
    bad = json.loads(json.dumps(ls))
    bad["unavailable_reason"] = "no_pass_criteria_limits"
    t("validator-rejects-unavailable-with-rows", not ok_payload("station_spec_limits", bad))

    # ---- ⑥ 純函式：retest ---------------------------------------------------------------
    kr = {"cls": "segCleanTest", "days": 7}
    summ = [("assyModule", "400", "350", "40", "4", "6", "390", "3"),
            ("sbsDie", "100", "71", "23", "2", "1", "99", "1")]
    top = [("J0SYN17", "assyModule", "4", "1", "2026-08-13 09:10:04", "2026-08-13 10:41:19"),
           ("J0SYN08", "assyModule", "3", "0", "2026-08-13 08:17:06", "2026-08-13 09:32:11")]
    rr = derive_retest(kr, summ, top, window={"from": "2026-08-07 00:00:00",
                                              "to": "2026-08-14 00:00:00", "days": 7})
    t("rt-validator", ok_payload("station_retest", rr), rr)
    t("★ rt-segments-fail-two-cols", (rr["n_segments"], rr["n_fail"], rr["n_pass"], rr["n_other"])
      == (500, 7, 489, 4))
    t("rt-serials", (rr["n_serials"], rr["n_serials_retested"], rr["max_segments_per_serial"],
                     rr["n_fail_in_retested"]) == (421, 63, 4, 4))
    t("rt-by-class", [c["material_class"] for c in rr["by_material_class"]] == ["assyModule", "sbsDie"])
    t("★ rt-no-fail-inference-caveat", any(RETEST_CAVEAT in c for c in rr["caveats"])
      and any("不推「因為 FAIL 才重測」" in c for c in rr["caveats"]))
    t("rt-truncated-top-lower-bound", rr["truncated"] and any("前 50 顆" in c for c in rr["caveats"])
      and not any("計數是**下界**" in c for c in rr["caveats"]))
    r0_ = derive_retest(kr, [], [])
    t("rt-empty-reason", r0_["found"] is False and r0_["reason"] == "no_segments_in_window"
      and ok_payload("station_retest", r0_))
    rn = derive_retest(kr, [(None, "10", "9", "1", "2", "1", None, None)], [top[0]])
    t("rt-unknown-pass-null", rn["n_pass"] is None and rn["n_other"] is None
      and rn["n_fail_in_retested"] is None and not rn["truncated"] and ok_payload("station_retest", rn))
    # ★ QG-7 回答層 Q10（2026-09-25 live：「756 PH」其實 sbsDie 693／assyModule 49／其他 14）
    t("★ rt-split-ph-die", (rr["n_ph_retested"], rr["n_die_retested"], rr["n_other_retested"])
      == (40, 23, 0) and rr["n_serials_retested"] == 63)
    t("★ rt-unit-caveat", RETEST_UNIT_CAVEAT in rr["caveats"] and "不是 PH" in RETEST_UNIT_CAVEAT
      and "die 另計" in RETEST_UNIT_CAVEAT)
    _q10 = derive_retest({"cls": "segAuxElectricalTest", "days": 7},
                         [("sbsDie", "9000", "8500", "693", "4", "1000", "8000", "990"),
                          ("assyModule", "1500", "1200", "49", "3", "80", "1400", "60"),
                          ("assyEndOfLine", "600", "100", "12", "2", "25", "575", "15"),
                          ("assyProductionReliabilityTesting", "84", "28", "2", "2", "4", "80", "2")],
                         [top[0]])
    t("★ rt-split-q10-live-shape", (_q10["n_serials_retested"], _q10["n_ph_retested"],
                                    _q10["n_die_retested"], _q10["n_other_retested"]) == (756, 49, 693, 14)
      and ok_payload("station_retest", _q10), _q10)
    t("rt-split-null-when-total-unknown", (rn["n_ph_retested"], rn["n_die_retested"],
                                           rn["n_other_retested"]) == (0, 0, 1)
      and (r0_["n_ph_retested"], r0_["n_die_retested"], r0_["n_other_retested"]) == (None, None, None))
    _rnu = derive_retest(kr, [("assyModule", "10", "9", None, "2", "1", "9", None)], [])
    t("rt-split-all-none-if-n_ret-none", _rnu["n_serials_retested"] is None
      and (_rnu["n_ph_retested"], _rnu["n_die_retested"], _rnu["n_other_retested"]) == (None, None, None)
      and ok_payload("station_retest", _rnu))
    bad = json.loads(json.dumps(rr))
    bad["n_ph_retested"] = bad["n_serials_retested"]          # 把合計冒充成 PH ⇒ 三欄相加不等於總數
    t("★ validator-rejects-split-not-summing", not ok_payload("station_retest", bad))
    bad = json.loads(json.dumps(rr))
    bad["n_die_retested"] = None
    t("validator-rejects-partial-split", not ok_payload("station_retest", bad))
    t("★ caveat4-live-wording", "另兩站" not in HARD_CAVEATS[3] and "其他站" in HARD_CAVEATS[3]
      and "found=true" in HARD_CAVEATS[3] and "不代表全部 PASS" in HARD_CAVEATS[3])
    bad = json.loads(json.dumps(rr))
    bad["top"] = bad["top"] * 30
    t("validator-rejects-top-over-cap", not ok_payload("station_retest", bad))
    t("blank-keys", all(set(blank(tl, {})) == set(KEYS_BY_TOOL[tl]) for tl in TOOLS))

    for tl, k_ in (("segment_data", {"serial": "B1", "cls": "segCleanTest", "which": "last"}),
                   ("station_spec_limits", {"cls": "segCleanTest", "recipe": None}),
                   ("station_retest", {"cls": "segCleanTest", "days": 7})):
        cur = _FakeCursor({}, guard=tuple(0 for _ in GUARD_KEYS))
        try:
            compute(tl, cur, k_)
            t(f"guard-raises-{tl}", False)
        except SchemaDivergence as ex:
            t(f"guard-raises-{tl}", "ABSENT" in str(ex))

    # golden fixture 不存在（例：脫敏公開樹不帶原始撈取 golden）⇒ 依賴 golden 的段落標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    _fx_absent = [f for f in FIXTURE_FILES.values()
                  if not os.path.exists(os.path.join(BASE, "_fixtures", f))]
    if _fx_absent:
        print("mes_segment selftest: skipped: %s absent" % ", ".join(sorted(_fx_absent)))
        print(f"mes_segment selftest: {n} assertions PASS")
        return 0

    # ---- ⑦ fixture：三份 golden 走同一支 derive_*（demo 與 live 同形）-------------------
    fx = {tl: _fixture(tl) for tl in TOOLS}
    t("fx-schema-ids", [fx[tl]["schema_id"] for tl in TOOLS] == [SCHEMA_SEGMENT, SCHEMA_LIMITS,
                                                                 SCHEMA_RETEST])
    t("fx-builder-reproducible", build_fixtures() == fx)
    for tl in TOOLS:
        for g in fx[tl]["golden"]:
            for st in g["steps"].values():
                t(f"fx-cols-{tl}", all(len(row) == len(st["columns"]) for row in st["rows"]))
            t(f"fx-provenance-{tl}", g["source_probe"] == PROBE_SQL and g["captured_at"])
    t("★ fx-sd-demo-serial-inferred", fx["segment_data"]["demo_serial"] == DEMO_SERIAL
      and "unique match" in fx["segment_data"]["golden"][0]["serial_inference"])
    dsd = demo_lookup("segment_data", fx["segment_data"],
                      {"serial": DEMO_SERIAL, "cls": DEMO_STATION, "which": "last"})
    t("fx-sd-valid", dsd["found"] and ok_payload("segment_data", dsd), dsd)
    ds0 = dsd["segments"][0]
    dv = {v["variable"]: v for v in ds0["values"]}
    t("fx-sd-fail-seg", ds0["result"] == "FAIL" and ds0["started_at"] == "2026-08-13 09:10:04")
    t("★ fx-sd-ch0-oos-27.61-vs-25", dv["varCLN_BackChannelWash_Channel0Pres"]["value"] == 27.61
      and dv["varCLN_BackChannelWash_Channel0Pres"]["spec"] == {"min": 7, "max": 25}
      and dv["varCLN_BackChannelWash_Channel0Pres"]["out_of_spec"] is True
      and dv["varCLN_BackChannelWash_Channel0Pres"]["invalid_measurement"] is False)
    t("fx-sd-5-vars-all-oos", len(dv) == 5 and ds0["summary"]["n_out_of_spec"] == 5)
    t("fx-sd-ch4-spec-max-only", dv["varCLN_BackChannelWash_Channel4Pres"]["spec"] == {"min": None, "max": 25}
      and dv["varCLN_BackChannelWash_Channel4Pres"]["valid"] == {"min": None, "max": None})
    t("fx-sd-n-at-station", dsd["n_segments_at_station"] == 4)
    t("fx-sd-demo-caveat", any("（demo）" in c and "推論" in c for c in dsd["caveats"]))
    t("fx-sd-miss-none", demo_lookup("segment_data", fx["segment_data"],
                                     {"serial": "NOPE", "cls": DEMO_STATION, "which": "last"}) is None)
    t("fx-sd-other-station-none", demo_lookup("segment_data", fx["segment_data"],
                                              {"serial": DEMO_SERIAL, "cls": "segCalibrationTest",
                                               "which": "last"}) is None)
    slg = {g["key"]: g for g in fx["station_spec_limits"]["golden"]}
    t("fx-sl-three-stations", sorted(slg) == sorted(["segStraightnessCheck",
                                                     "segLamination", "segCleanTest"]))
    t("★ fx-sl-G3a", slg["segCleanTest"]["reference_G3a"] == {"n_rows": 5055, "n_sgd": 91,
                                                                  "n_var": 20, "max_matching_ini": "1"})
    dli = demo_lookup("station_spec_limits", fx["station_spec_limits"],
                      {"cls": "segCleanTest", "recipe": None})
    t("fx-sl-cln-valid", dli["found"] and ok_payload("station_spec_limits", dli) and dli["truncated"])
    t("fx-sl-cln-qr062630-12", dli["recipes"][0]["recipe"] == "QR910001/12"
      and dli["recipes"][0]["variables"][0]["variable"] == "varCLN_BackChannelWash_Channel0Pres"
      and dli["recipes"][0]["variables"][0]["spec"] == {"min": 7, "max": 25}
      and dli["recipes"][0]["variables"][0]["valid"] == {"min": 1, "max": 100})
    t("fx-sl-demo-caveat", any("5055" in c for c in dli["caveats"]))
    for cls in ("segStraightnessCheck", "segLamination"):
        d0 = demo_lookup("station_spec_limits", fx["station_spec_limits"], {"cls": cls, "recipe": None})
        t(f"★ fx-sl-{cls}-no-limits", d0["found"] and d0["unavailable_reason"] == "no_pass_criteria_limits"
          and d0["n_limit_rows"] == 0 and ok_payload("station_spec_limits", d0))
    t("fx-sl-unprobed-station-none", demo_lookup("station_spec_limits", fx["station_spec_limits"],
                                                 {"cls": "segCalibrationTest", "recipe": None}) is None)
    ref = fx["station_spec_limits"]["_reference"]
    t("★ fx-sl-G3b-six-combos", sorted((r[1], r[2]) for r in ref["G3b"])
      == sorted((a, b) for a in RANGE_CODES for b in LIMIT_CODES))
    rtg = {g["key"]: g for g in fx["station_retest"]["golden"]}
    dri = demo_lookup("station_retest", fx["station_retest"], {"cls": "segCleanTest", "days": 7})
    t("fx-rt-cln-valid", dri["found"] and ok_payload("station_retest", dri))
    t("★ fx-rt-cln-G5a", (dri["n_segments"], dri["n_serials"], dri["n_serials_retested"],
                          dri["max_segments_per_serial"], dri["n_fail"]) == (500, 421, 63, 4, 6))
    t("fx-rt-cln-top-truncated", len(dri["top"]) == RETEST_TOP and dri["truncated"]
      and dri["top"][0]["n_segments"] == 4)
    t("★ fx-rt-retest-without-fail", any(r["n_segments"] >= 3 and r["n_fail"] == 0 for r in dri["top"]))
    dfl = demo_lookup("station_retest", fx["station_retest"], {"cls": "segLamination", "days": 30})
    t("fx-rt-film-4-seg-0-fail", dfl["top"][0]["serial"] == "J0SYN13" and dfl["top"][0]["n_segments"] == 4
      and dfl["top"][0]["n_fail"] == 0 and len(dfl["top"]) == dfl["n_serials_retested"] == 6
      and dfl["truncated"] and not any("前 50 顆" in c for c in dfl["caveats"])
      and any("計數是**下界**" in c for c in dfl["caveats"]))
    t("fx-rt-demo-caveat", any("demo 不依 days 重算（問的是 30 天）" in c for c in dfl["caveats"])
      and dfl["diag"]["days_requested"] == 30)
    t("fx-rt-sets", sorted(rtg) == sorted(slg))

    # ---- ⑧ 編排（假游標）：順序、臨時表只在非參數化批次建、參數 ----------------------
    g = fx["segment_data"]["golden"][0]["steps"]
    live_seg = [(900000001, 4512, "QR910001/12", datetime(2026, 8, 13, 9, 10, 4), "FAIL",
                 "assyModule")]
    cur = _FakeCursor({SQL_SD_SEG_READ: live_seg, SQL_SD_EQP: [(900000001, "EID-14732", "CLN")],
                       SQL_SD_REAL: [(900000001, 10, "varCLN_BackChannelWash_Channel0Pres", "p", "kPa",
                                      "float", "fcdSegmentData", 27.6100006103516)],
                       SQL_SD_INT: [], SQL_SD_LIMITS: [(4512, 10, "varCLN_BackChannelWash_Channel0Pres",
                                                        "spec", "max", 25.0, 1)],
                       SQL_SD_DICT_UNSUPPORTED: [("varCLN_Barcode", "string", "fcdSegmentData")]},
                      one={SQL_SD_COUNT: (4, datetime(2026, 8, 13, 9, 10, 4),
                                          datetime(2026, 8, 13, 10, 41, 19))})
    rsd = compute("segment_data", cur, {"serial": "J0SYN17", "cls": "segCleanTest", "which": "last"})
    t("cmp-sd-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_SD_COUNT, SQL_SD_TEMPS, SQL_SD_SEG_LAST,
                                                  SQL_SD_SEG_READ, SQL_SD_EQP, SQL_SD_REAL, SQL_SD_INT,
                                                  SQL_SD_LIMITS, SQL_SD_DICT_UNSUPPORTED])
    t("cmp-sd-params", cur.log[1][1] == ("segCleanTest", "J0SYN17")
      and cur.log[3][1] == (1, "segCleanTest", "J0SYN17")
      and cur.log[-1][1] == ("segCleanTest",))
    t("cmp-sd-temps-no-params", all(p_ == () for s_, p_ in cur.log
                                    if s_ in (SQL_SD_TEMPS, SQL_SD_SEG_READ, SQL_SD_EQP, SQL_SD_REAL,
                                              SQL_SD_INT, SQL_SD_LIMITS)))
    t("★ cmp-sd-pairs-sgd-vdf", ok_payload("segment_data", rsd)
      and rsd["segments"][0]["values"][0]["out_of_spec"] is True
      and rsd["segments"][0]["recipe"] == "QR910001/12"
      and rsd["segments"][0]["equipment"][0]["eid"] == "EID-14732"
      and rsd["unsupported_variables"]["by_type"] == {"string": ["varCLN_Barcode"]})
    cur = _FakeCursor({SQL_SD_SEG_READ: live_seg * 1}, one={SQL_SD_COUNT: (30, None, None)})
    compute("segment_data", cur, {"serial": "B1", "cls": "segCleanTest", "which": "all"})
    t("cmp-sd-all-top-21", [p_ for s_, p_ in cur.log if s_ is SQL_SD_SEG_LAST] == [(21, "segCleanTest",
                                                                                    "B1")])
    cur = _FakeCursor({}, one={SQL_SD_COUNT: (2, None, None)})
    compute("segment_data", cur, {"serial": "B1", "cls": "segCleanTest", "which": "first"})
    t("cmp-sd-first-asc", SQL_SD_SEG_FIRST in [s for s, _ in cur.log]
      and SQL_SD_SEG_LAST not in [s for s, _ in cur.log])
    cur = _FakeCursor({}, one={SQL_SD_COUNT: (0, None, None)})
    r0c = compute("segment_data", cur, {"serial": "NOPE", "cls": "segCleanTest", "which": "last"})
    t("cmp-sd-miss-stops-after-count", [s for s, _ in cur.log] == [SQL_GUARD, SQL_SD_COUNT]
      and r0c["not_measurable"][0]["reason"] == "serial_not_found_at_station")
    cur = _FakeCursor({SQL_SL_LIMITS: []})
    rl = compute("station_spec_limits", cur, {"cls": "segLamination", "recipe": None})
    t("cmp-sl", [s for s, _ in cur.log] == [SQL_GUARD, SQL_SL_LIMITS]
      and cur.log[1][1] == (LIMIT_ROWS_CAP, "segLamination")
      and rl["unavailable_reason"] == "no_pass_criteria_limits")
    cur = _FakeCursor({SQL_RT_SUMMARY: summ, SQL_RT_TOP: top})
    now = datetime(2026, 8, 14, 12, 0, 0)
    rrt = compute_retest(cur, {"cls": "segCleanTest", "days": 30}, now=now)
    t("cmp-rt-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_RT_TEMPS, SQL_RT_SEG, SQL_RT_LOT,
                                                  SQL_RT_SUMMARY, SQL_RT_TOP])
    t("cmp-rt-params", cur.log[2][1] == (RT_SEG_CAP, "segCleanTest", "2026-08-25 12:00:00",
                                         "2026-08-14 12:00:00") and cur.log[-1][1] == (RETEST_TOP,))
    t("cmp-rt-valid", ok_payload("station_retest", rrt) and rrt["window"]["days"] == 30)
    cur = _FakeCursor({SQL_RT_SUMMARY: []})
    t("cmp-rt-empty-skips-top", compute_retest(cur, {"cls": "segCleanTest", "days": 7})["reason"]
      == "no_segments_in_window" and SQL_RT_TOP not in [s for s, _ in cur.log])
    print(f"mes_segment selftest: {n} assertions PASS")
    return 0


def _demo_cli(argv):
    tool = argv[0]
    if tool == "segment_data":
        key, err = normalize_segment_args(argv[1] if len(argv) > 1 else None,
                                          argv[2] if len(argv) > 2 else None,
                                          argv[3] if len(argv) > 3 else None)
    elif tool == "station_spec_limits":
        key, err = normalize_limits_args(argv[1] if len(argv) > 1 else None,
                                         argv[2] if len(argv) > 2 else None)
    elif tool == "station_retest":
        key, err = normalize_retest_args(argv[1] if len(argv) > 1 else None,
                                         argv[2] if len(argv) > 2 else None)
    else:
        print(json.dumps({"error": "unknown_tool", "given": tool}))
        return 2
    if err:
        print(json.dumps(err, ensure_ascii=False))
        return 2
    out = demo_lookup(tool, _fixture(tool), key)
    print(json.dumps(out, ensure_ascii=False, indent=1) if out is not None
          else '{"unavailable_reason": "not_in_demo_fixture"}')
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
    if argv[:1] == ["--demo"] and len(argv) >= 2:
        return _demo_cli(argv[1:])
    print("usage: python mes_segment.py --selftest | --sql | --build-fixtures | "
          "--demo segment_data <serial> <station> [which] | --demo station_spec_limits <station> [recipe] | "
          "--demo station_retest <station> [days]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
