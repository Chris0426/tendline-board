#!/usr/bin/env python3
"""Tendline Board — Hermes 的**記錄級** MES 查詢核心（`board.station_vars.v1`／`board.wo_serials.v1`）。

**這是什麼**：`SPEC-HERMES-TRUST.md` §7.5／§7.6 兩個固定工具的資料腿——
  · `station_variables(cls)`：**這一站登記了哪些量測／配方變數**（兩張小表、秒回）。
  · `wo_serials_status(wo_id)`：**這張工單有幾顆序號、各自最後一段在哪一站**（A2 第二包）。
兩者都由 `board_mcp` 的唯讀工具消費，**runtime 沒有 LLM 參與**：LLM 只叫工具，
工具跑**固定審過的參數化 SQL**（§7.6.1）。

**不是什麼**：不是 WIP 佇列（`mes_wip`＝`/dispatch` 的 `board.wip.v1`）、不是良率（`mes_qlik`／
`mes_yield`）、不是稼動（`mes_timeline`）、不是重工（`mes_rework`）、不是 family 活動（`mes_family`）。
**本檔刻意不擴 `mes_wip`**：那支的身分是 WIP 軸資料源、檔頭逐字寫「不是良率、不是稼動」，
它的 perf lore（兩段式母體收斂、`#temp` lot-字串 join、`h_lookback ≥ lookback` clamp）與
這裡兩條查詢**完全無關**，共存會讓下一個人以為新查詢也吃那套 clamp；而且 `mes_wip.ALL_SQL()`
的負斷言涵蓋面會被糊掉。新模組自帶自己的 `ALL_SQL()` 與自己的負斷言。

語意權威（分歧一律以 SPEC 為準、不以本檔註解為準）：
  · `SPEC-HERMES-TRUST.md` §7.5（`wo_serials_status` 的**九條限制**）／§7.6.6／§7.6.7
    （`segMESReserved` 硬排除、`MaterialStatus` 13 值、`station_variables` 可動筆）。
  · 探針存證＝`_PROBE-WO-SERIALS.sql`＋`_PROBE-WO-SERIALS-README.md`（§B 出處表／§C 地雷）、
    `_PROBE-HERMES-CATALOG.sql` STEP8a／8b＋`_PROBE-HERMES-CATALOG-README.md`。

═══ `wo_serials_status` 的九條限制（§7.5 `:542` 原文；**逐條落在碼上，不是散文**）═══════
  ① **不做未結段語意**：回傳裡**沒有** `open`／`last_open`／`in_progress`／`is_running` 任何欄；
     措辭一律「最後**已完成**的製程段」。依據＝三張探針單全部序號**零未結段**。
  ② **`AssignmentTime` 只在活單可信**：`diag.assignment_time_usable` ＋ `assignment_time_reason`
     （`latest_segment_recent`／`batch_stamp`／`no_segment`／`no_recent_segment`）。判準＝最新段
     `stpStartTime` 距 `fetched_at` ≤ `LIVE_RECENT_H`；否決線＝`first_assign == rqsDateCompleted`
     的序號佔比 ≥ `BATCH_STAMP_SHARE`（2019 那種批次蓋章形）⇒ 逐序號 `assigned_at=null`。
     ⚠ **`rqsSegmentState` 本模組一個位元都不讀**（兩輪矛盾 9/15）；
     ⚠ **絕不用 `rqsDateCompleted IS NULL` 當活單閘**（`WO910001/1` 完工日已填仍在跑段）。
  ③ **扇出自揭**：逐序號 `last_segment_wo`／`last_wo_is_this_wo`；頂層
     `n_rework_instances = serial_count − n_serial_lot_dst`。**不做第四條扇出查詢**：
     以 `matPK` 分組的 `n_serial_multi_board` 結構上永遠 0（重工＝同 lot 新 `matPK`）。
  ④ **序號數與 `qty_requested` 並列** ＋ `serial_count_note`（活單序號數低於 qty 是常態）。
  ⑤ **`StatusId` 轉 enum 名**（硬編 `MATERIAL_STATUS` 13 值）、**`OutgoingStatusId` 只印數字**
     （MES 無 `OutgoingStatus` 對照表；值域對齊 ≠ 依 `MaterialStatus` 對照）。值域外 ⇒
     `status_code = null` ＋ `status_code_note`，**絕不猜**。
  ⑥ **`last_result` 字面轉述**（`PASS`／`FAIL`／`null` 原值直送，不翻譯、不映射）。
  ⑦ **`n_seg` 只當已完成段數的下界**，不是「第幾站」（同 class 可在一條路線出現兩次、
     `flsOrder` 非唯一鍵）⇒ 要答「在第幾站」得等 `wo_route`。
  ⑧ **工期欄帶 `not_measurable`**：`start == end` ⇒ `last_segment_duration_s = null`（**不是 0**）；
     零工期是**站屬性**（秤重／組裝／OQC 站 100% start==end），不是 `segMESReserved` 專屬。
  ⑨ **`diag.since_used` ＋ 三個 `*_cap_hit` 必附**。

★ 欄位出處（[[ssms-sql-column-provenance]]：每個 物件.欄位 都追得到跑過的查詢）
  probe ＝ `_PROBE-WO-SERIALS.sql`（2026-09-21 三輪 Chris SSMS 實跑）／
  `_PROBE-HERMES-CATALOG.sql`（同日兩跑）；inventory ＝
  `..\\MES knowledge base\\workspace\\08_schema_query_hardening\\artifacts\\table_column_inventory.csv`。

  | 欄 | 物件 | 跑過的查詢（結果集） | inventory |
  |---|---|---|---|
  | `rqsPK`                    | `dbo.production_request_v`     | WO-SERIALS STEP1／STEP2a（1／3） | `:4678` |
  | `rqsProductionRequestID`   | `dbo.production_request_v`     | 同上 | `:4679`（varchar 75） |
  | `rqsWorkOrder`             | `dbo.production_request_v`     | 同上 | `:4680`（varchar 300） |
  | `rqsDescription`           | `dbo.production_request_v`     | 同上 | `:4683` |
  | `mclMaterialClassID`       | `dbo.production_request_v`／`dbo.material_class_t` | 同上 | `:4713`／`:3554` |
  | `rqsDateStarted`           | `dbo.production_request_v`     | 同上 | `:4688`（nullable） |
  | `rqsDateCompleted`         | `dbo.production_request_v`     | 同上 | `:4689`（nullable） |
  | `rqsQuantityRequested`     | `dbo.production_request_v`     | 同上 | `:4690`（real） |
  | `Id`                       | `dbo.material_production_request` | STEP1／STEP1b（1／2） | `:3301` |
  | `MaterialId`               | `dbo.material_production_request` | 同上 | `:3302` |
  | `ProductionRequestId`      | `dbo.material_production_request` | 同上 | `:3303` |
  | `AssignmentTime`           | `dbo.material_production_request` | 同上 | `:3304` |
  | `LastWorkTime`             | `dbo.material_production_request` | 同上 | `:3305`（nullable） |
  | `OutgoingStatusId`         | `dbo.material_production_request` | 同上 | `:3308`（tinyint） |
  | `matPK`                    | `dbo.materials_t`／`dbo.process_segment_v` | STEP1／STEP2b／STEP2c | `:3576`／`:4368` |
  | `matMaterialLotID`         | `dbo.materials_t`              | STEP1／STEP2b | `:3577` |
  | `mdfPK`                    | `dbo.materials_t`／`dbo.material_definition_t` | 同上 | `:3580`／`:3558` |
  | `StatusId`                 | `dbo.materials_t`              | 同上 | `:3588`（tinyint） |
  | `mdfMaterialDefinitionID`  | `dbo.material_definition_t`     | STEP1 | `:3559` |
  | `mclPK`                    | `dbo.material_definition_t`／`dbo.material_class_t` | STEP1 | `:3561`／`:3553` |
  | `stpPK`                    | `dbo.process_segment_v`        | STEP2c／STEP2d／STEP3a（4） | `:4367` |
  | `stpStartTime`             | `dbo.process_segment_v`        | 同上 | `:4372` |
  | `stpEndTime`               | `dbo.process_segment_v`        | 同上 | `:4373`（nullable） |
  | `SegmentClassID` | `dbo.process_segment_v`／`dbo.process_segment_class_t` | 同上／CATALOG STEP8a-8b（23／24） | `:4388`／`:3646` |
  | `stpTestResult`            | `dbo.process_segment_v`        | 同上 | `:4371`（varchar 4） |
  | `SegmentProductionRequestCode` | `dbo.process_segment_v`    | STEP2c／STEP3a | `:4443`（varchar 75） |
  | `sgcPK`                    | `dbo.process_segment_class_t`    | CATALOG STEP8a／8b | `:3645` |
  | `sgcDescription`           | `dbo.process_segment_class_t`    | CATALOG STEP3a／8b | `:3647` |
  | `SegmentClassId`           | `dbo.segment_class_variable`      | CATALOG STEP8a／8b | `:3443` |
  | `VariableId`               | `dbo.segment_class_variable`      | 同上 | `:3444` |
  | `vdfPK`                    | `dbo.production_data_definition_t` | CATALOG STEP8b | `:3695` |
  | `vdfProductionDataID`      | `dbo.production_data_definition_t` | 同上 | `:3696` |
  | `FailureDescription`           | `dbo.production_data_definition_t` | 同上 | `:3697` |
  | `vdfClassID`               | `dbo.production_data_definition_t` | 同上 | `:3698` |
  | `vdfType`                  | `dbo.production_data_definition_t` | 同上 | `:3699` |
  | `vdfUOM`                   | `dbo.production_data_definition_t` | 同上 | `:3700` |

  **絕對不 SELECT 的欄**（負斷言釘在 `ALL_SQL()` 的文本掃描）：
  `rqsCurrentEquipment`（15 張裡 8 張同指一台、含已結單）、`rqsSegmentState`（兩輪矛盾
  5/15→9/15）、`rqsChangeLog`（內嵌人名）、`stpPersonID`／`OperatorId`／`rqsRequestor`
  （§24 人名紅線）。`vdfPK` **查得到但不外送**（內部主鍵、對答題無用）。

★ perf 鐵則（B2/B4/M4 的血；改本檔前先讀 `GOVERNANCE.md` QG-2）
  · 每條打 `dbo.*` 的 SELECT 以 `OPTION (RECOMPILE)` 收尾。
  · 碰 `dbo.process_segment_v`（5 表大 view）的每條**都有 `stpStartTime >= ?` 窗**；
    view 上**零 `DISTINCT`／零 `GROUP BY`／零動態 `IN (...)` 清單**（聚合全在 Python，
    母體收斂靠 `#temp` join＝逐字同探針 STEP2c）。
  · **刻意不濾 `stpEndTime`**（沒收尾的段就是「它現在在那一站」）。⚠ **日後若真要濾，一律
    `stpEndTime >= stpStartTime`、絕不 `>`**——`>` 會刪掉合法零工期段、八站整站隱形
    （[[mes-zero-length-segments]]、`mes_wip.py:36-37`、`mes_family.py:47`）。
  · 「每顆序號的最後一段」用 **`max(stpPK)`**、**不是** `stpStartTime`（零工期／同批寫入會並列，
    取「最後」不決定；`stpPK` 是 bigint 單調插入序，README §C-4）。
  · `station_variables` 的三張表都是**小表**（非大 view）⇒ join 與 `ORDER BY` 合法；
    **零 EAV 大表**（`segment_real_data_t`／`segment_integer_data_t`／`segment_string_data_t`
    三張合計約 22 億列＝§7.6.4 陷阱 3，本模組一個位元都不碰）。
  · 兩層 `TOP` 上限＋`*_cap_hit` 自揭；`diag.since_used` 必附（限制⑨）。

★ §24 人名紅線：本模組不取、不回、不記任何姓名／工號欄位（`ALL_SQL()` 全文本負斷言）。
★ 唯讀：對 view／表只有 SELECT；唯一 DDL/DML 在 `#temp`（tempdb 不算寫 MES）。
★ 凍結模組 `mes_timeline.py`／`mes_yield.py`：**不 import、不執行、零 diff**。
  `_connect`／`SchemaDivergence` 走 `mes_qlik`（與 `mes_wip.py:63` 同一條路，**不抄第四份連線碼**）。

CLI:
    python mes_hermes.py --selftest      # 純離線（合成列＋讀 _fixtures/ 兩份 golden），零 MES、零網路
    python mes_hermes.py --sql           # 只**印**本模組會送出的全部 SQL 文本（不執行）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

# ── 連線／schema 分歧例外的單一出處（同 `mes_wip`／`mes_family` 慣例；不抄第二份）───────
from mes_qlik import SchemaDivergence, _connect            # noqa: F401

try:                                        # Windows console 中文（與其他 mes_* 同形）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                           # pragma: no cover - 老 runtime 沒有這支
    pass

BASE = os.path.dirname(os.path.abspath(__file__))


# =============================================================================
# 1. 人工審定常數（改它＝人工動作）
# =============================================================================
SCHEMA_WO_SERIALS = "board.wo_serials.v1"
SCHEMA_STATION_VARS = "board.station_vars.v1"

CALIBER_RECORD_LEVEL = ("raw-equipment-axis: record-level MES facts (serial / segment / "
                        "variable dictionary); NOT the company first-FAIL yield caliber")

QUERY_TIMEOUT_SEC = 180

#: `MaterialStatus` 13 值（§7.6.6 洞①；catalog 探針 STEP2a 實測）。**值域外一律 null、絕不猜。**
MATERIAL_STATUS = {
    1: "Available", 2: "Closed", 3: "Consumed", 4: "Goods Inwards Inspection",
    5: "Hold", 6: "In Process", 7: "In Stores", 8: "In Transit", 9: "Reserved",
    10: "Scrapped", 11: "Shipped", 12: "WIP", 13: "Quarantined",
}

#: MES 自身的事件稽核流水（§7.6.6 洞②）——**硬排除，寫進凍結 SQL、不讓 agent 事後濾**。
#: 證據：`sgcDescription`「MES Reserved Process Segments」、34 條 definition 33 條 Obsolete、
#: 近 365 天全部段皆**零工期**、0 未結。
MES_RESERVED_CLASS = "segMESReserved"
#: 排除後無製程段的序號進這個桶（§7.6.6：**不留白、不退回印它**）。
NO_SEGMENT_BUCKET = "(僅 MES 事件)"
#: 連 MES 事件都沒有的序號（已投料、尚未開工）。
NO_ACTIVITY_BUCKET = "(無製程段／未開工)"

#: ⚠ `segScrapMaterial` **不排除**（Chris／Fable 2026-09-21 裁）：它是合法的 Optional 路線步驟
#: （catalog STEP4c order 2），三張活單 0 顆停在它。要排就是改一行 WHERE ＋一顆釘。

#: 上限（前兩個逐字同探針 `:24-25`）＋ `diag` 逐一自揭 `*_cap_hit`。
SER_CAP = 20000
SEG_CAP = 500000
EVT_CAP = 200000
VARS_CAP = 2000

#: `@since` 預設＝**真全歷史**（探針第二輪之二用的值）。`NULL` 自動窗會切掉上游舊段、
#: 讓 `n_serial_no_seg` 虛高（第二輪之一實證）。
SINCE_DEFAULT = "2000-01-01 00:00:00"

#: 限制② 的兩條判準。
LIVE_RECENT_H = 24            # 最新段距 `fetched_at` 幾小時內算「活單」
BATCH_STAMP_SHARE = 0.5       # `first_assign == rqsDateCompleted` 的序號佔比 ≥ 此值＝批次蓋章形
ASSIGNMENT_REASONS = ("latest_segment_recent", "batch_stamp", "no_segment", "no_recent_segment")

#: `wo_id` 格式白名單（同時解掉 LIKE 萬用字元問題：排除 `%` `_` `[`）。
WO_ID_RE = re.compile(r"^[A-Za-z0-9]{1,40}(/\d{1,3})?$")
#: `cls` 格式白名單（對齊 `SegmentClassID varchar(75)`）。
CLS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,74}$")

#: `vdfClassID` 4 值／`vdfType` 4 值／`vdfUOM` 12 值（2026-09-21 catalog 探針實測閉集）。
PDD_CLASS_IDS = ("fcdProcessParameter", "fcdSegmentData",
                 "fcdMaterialFailureCode", "dimEquipmentFailureCode")
PDD_TYPES = ("float", "integer", "string", "uriReference")
#: ⚠ **編碼往返**：`µm`（MICRO SIGN）與 `°`／`°C`（DEGREE SIGN）必須逐位元原樣往返；
#: 釘的是「JSON 序列化→反序列化逐位元不變」與「census 鍵 ⊆ 本閉集」，**不釘死某一個 codepoint**。
PDD_UOMS = ("µm", "none", "mm", "°", "s", "N", "°C",
            "L/min", "mm/s", "Yes/No", "kPa", "%")

#: `station_variables` 的散文（§7.6.6／§7.6.7 新事實③；**值的語意住這裡、不散在呼叫端**）。
STATION_VARS_NOTES = (
    "vdfClassID 四值：fcdProcessParameter（配方參數）／fcdSegmentData（量測值）／"
    "fcdMaterialFailureCode（材料失敗碼，mfl*）／dimEquipmentFailureCode（設備失敗碼，eqf*）"
    "——前綴與 vdfClassID 一對一。",
    "vdfType 的 string ＋ vdfUOM='Yes/No' 不是布林，原樣轉述、不要譯成 true/false。",
    "uom='none' 不代表無單位語意，多半是計數或代碼。",
    "本清單是「這一站登記了哪些變數」，不是「這一站這次量到多少」——值在 "
    "segment_t{Real,Integer,String}Data，本工具不碰。",
)

#: 回傳的鍵集合（**固定**；smoke 與 fixture validator 共用同一份）。
STATION_VARS_KEYS = ("cls", "found", "class_description", "n_vars", "groups", "uom_census",
                     "notes", "reason", "unavailable_reason", "diag")
WO_SERIALS_KEYS = ("wo_id", "found", "work_order", "serial_count", "qty_requested",
                   "serial_count_note", "n_serial_lot_dst", "n_rework_instances",
                   "last_station_distribution", "serials", "caveats",
                   "reason", "unavailable_reason", "diag")
SERIAL_KEYS = ("lot", "material_class", "status_id", "status_code", "status_code_note",
               "outgoing_status_id", "last_station", "last_segment_start", "last_segment_end",
               "last_segment_duration_s", "duration_not_measurable", "last_result",
               "last_segment_wo", "last_wo_is_this_wo", "n_seg", "assigned_at",
               "no_process_segment", "last_event_at")

#: 限制① 的負斷言字樣（**回傳裡永遠不得出現**；smoke 逐鍵掃）。
FORBIDDEN_OPEN_KEYS = ("open", "last_open", "in_progress", "is_running", "is_open")

_TS_FMT = "%Y-%m-%d %H:%M:%S"


# =============================================================================
# 2. I/O 層：SQL 文本（**全部是模組常數＝離線可斷言**；唯讀）
# =============================================================================
# ── `station_variables`（S1／S2；兩張小表＋一張定義表，秒回）────────────────────────────
#: S0 欄位守衛（形同 `mes_wip._sql_guard`）：物件或承重欄缺 ⇒ raise `SchemaDivergence`，
#: **停下、不跑出全零板**（全零板在消費面與「這站沒有變數」長得一樣＝靜默謊）。
SQL_VARS_GUARD = (
    "SELECT CASE WHEN OBJECT_ID('dbo.process_segment_class_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_class_variable') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.production_data_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.process_segment_class_t','SegmentClassID')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.segment_class_variable','SegmentClassId')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.production_data_definition_t','vdfProductionDataID')"
    " IS NULL THEN 0 ELSE 1 END\n"
    "OPTION (RECOMPILE)")
VARS_GUARD_KEYS = ("tbx_class", "tbx_scv", "tbx_vdf", "SegmentClassID",
                   "SegmentClassId", "vdfProductionDataID")

#: S1 class 存在性——**分辨「不存在」vs「存在但零變數」，兩者絕不可混**。
SQL_CLASS_EXISTS = (
    "SELECT c.sgcPK, c.SegmentClassID, c.sgcDescription\n"
    "FROM dbo.process_segment_class_t c\n"
    "WHERE c.SegmentClassID = ?\n"
    "OPTION (RECOMPILE)")

#: S2 變數清單（逐字照 `_PROBE-HERMES-CATALOG.sql:252-258` 的三表 join，加 `TOP` 與 `RECOMPILE`）。
SQL_CLASS_VARS = (
    "SELECT TOP (?) p.vdfPK, p.vdfProductionDataID, p.FailureDescription,\n"
    "       p.vdfClassID, p.vdfType, p.vdfUOM\n"
    "FROM dbo.segment_class_variable v\n"
    "JOIN dbo.process_segment_class_t      c ON c.sgcPK = v.SegmentClassId\n"
    "JOIN dbo.production_data_definition_t p ON p.vdfPK = v.VariableId\n"
    "WHERE c.SegmentClassID = ?\n"
    "ORDER BY p.vdfProductionDataID\n"
    "OPTION (RECOMPILE)")


# ── `wo_serials_status`（W0–W3b；**形狀逐條重現探針 `_PROBE-WO-SERIALS.sql`**）──────────
#: W0 欄位守衛：六個物件 ＋ **Board 從沒跑過的三欄**（`process_segment_v.matPK`／
#: `SegmentProductionRequestCode`／`material_production_request.AssignmentTime`）。
#: 缺 ⇒ raise `SchemaDivergence`＝**停下、不跑出全零板**（理由逐字同 `mes_wip.py:233-237`：
#: 全零板在消費面與「這張工單沒有序號」長得一樣＝靜默謊）。
SQL_WO_GUARD = (
    "SELECT CASE WHEN OBJECT_ID('dbo.production_request_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_production_request') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.materials_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_class_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.process_segment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.process_segment_v','matPK')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.process_segment_v','SegmentProductionRequestCode')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.material_production_request','AssignmentTime')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.materials_t','StatusId')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.material_production_request','OutgoingStatusId')"
    " IS NULL THEN 0 ELSE 1 END\n"
    "OPTION (RECOMPILE)")
WO_GUARD_KEYS = ("view_rqs", "tbx_mpr", "tbx_mlt", "tbx_mdf", "tbx_mcl", "view_seg",
                 "stp_matPK", "SegmentProductionRequestCode", "AssignmentTime",
                 "StatusId", "OutgoingStatusId")

#: 每次呼叫先清 `#temp`、**再把三張建成空殼**（同一條連線可能被重用；探針 `:27-31` 同款）。
#: ★★ **臨時表一律在這個「非參數化」批次裡建，帶參數的語句只准 `INSERT INTO #x`**
#:   （2026-09-21 live 首跑事故＝`error:ProgrammingError`、0.8 秒就回）：pyodbc 帶參數的
#:   `execute` 走 **`sp_executesql`**，在其中 `SELECT … INTO #t` 建的臨時表**隨那一次呼叫
#:   一起消失** ⇒ 下一句 `SELECT … FROM #t` 必 `Invalid object name '#t'`（SQLSTATE 42S02
#:   ⇒ pyodbc `ProgrammingError`）。**非參數化批次建的 `#temp` 是連線層的**，`sp_executesql`
#:   內部看得到、可以 `INSERT INTO`——這是唯一不改口徑就能修的路。
#:   ⚠ 三句空殼一律 `WHERE 1=0`，**欄位清單／來源表逐字同 W1／W2／W3a**：型別由來源自動
#:   推導＝**零新增出處**（檔頭 36 欄出處表不因此多一欄），也不必手寫任何 T-SQL 型別。
#:   ⚠ `#board_ser` 那句的 `GROUP BY` **不可拿掉**：少了它 `COUNT(*)` 會回一列 `0`，空殼裡就
#:   多一列垃圾（`WHERE 1=0` 只保證來源零列，不保證聚合零列）。
#:   ⚠ 三句空殼**彼此不互相 join**（`#board_seg` 只從 `process_segment_v` 取欄、不 join
#:   `#board_ser`）＝同一批次內零臨時表相依，少一種編譯期意外。
#: ⚠ 常數名留 `SQL_WO_DROP_TEMPS`（呼叫點與 `ALL_SQL()` 順序不動）；它現在「清完順手建空殼」。
SQL_WO_DROP_TEMPS = (
    "IF OBJECT_ID('tempdb..#board_seg') IS NOT NULL DROP TABLE #board_seg;\n"
    "IF OBJECT_ID('tempdb..#board_ser') IS NOT NULL DROP TABLE #board_ser;\n"
    "IF OBJECT_ID('tempdb..#board_pr')  IS NOT NULL DROP TABLE #board_pr;\n"
    "SELECT r.rqsPK, r.rqsProductionRequestID, r.rqsWorkOrder, r.rqsDescription,\n"
    "       r.mclMaterialClassID, r.rqsDateStarted, r.rqsDateCompleted,\n"
    "       r.rqsQuantityRequested\n"
    "INTO #board_pr\n"
    "FROM dbo.production_request_v r\n"
    "WHERE 1=0;\n"
    "SELECT m.matPK, lot = m.matMaterialLotID, mcl = mcl.mclMaterialClassID,\n"
    "       mdf = mdf.mdfMaterialDefinitionID, status_id = m.StatusId,\n"
    "       legacy_rqs = m.rqsPK, n_link = COUNT(*),\n"
    "       first_assign = MIN(x.AssignmentTime), last_assign = MAX(x.AssignmentTime),\n"
    "       out_status = MAX(x.OutgoingStatusId)\n"
    "INTO #board_ser\n"
    "FROM dbo.material_production_request x\n"
    "JOIN dbo.materials_t          m   ON m.matPK   = x.MaterialId\n"
    "JOIN dbo.material_definition_t mdf ON mdf.mdfPK = m.mdfPK\n"
    "JOIN dbo.material_class_t      mcl ON mcl.mclPK = mdf.mclPK\n"
    "WHERE 1=0\n"
    "GROUP BY m.matPK, m.matMaterialLotID, mcl.mclMaterialClassID,\n"
    "         mdf.mdfMaterialDefinitionID, m.StatusId, m.rqsPK;\n"
    "SELECT v.stpPK, v.matPK, v.SegmentClassID,\n"
    "       v.stpStartTime, v.stpEndTime, v.stpTestResult,\n"
    "       v.SegmentProductionRequestCode\n"
    "INTO #board_seg\n"
    "FROM dbo.process_segment_v v\n"
    "WHERE 1=0;")

#: W1 工單頭（含 `/n` 改版收斂）。
#: ⚠ **`base` 必須是去掉 `/n` 後綴的字串**：原本 `LIKE @wo + '/%'` 在 `@wo` 已含 `/1` 時展開成
#:   `'WO910001/1/%'`、永遠配不到 `/2` ⇒ 前三輪的 `n_rqs=1` **不構成「無改版」證據**
#:   （探針 `:62` 的修正註解）。格式白名單已排除 `%` `_` `[` ⇒ `LIKE` 安全；仍走參數化。
#: ⚠ **`INSERT INTO #board_pr` 不是 `SELECT … INTO`**（空殼在 `SQL_WO_DROP_TEMPS` 建好）——
#:   理由＝那個常數上方那段 `sp_executesql` 作用域說明；欄位清單／來源／過濾一字未改。
SQL_WO_HEAD = (
    "INSERT INTO #board_pr (rqsPK, rqsProductionRequestID, rqsWorkOrder, rqsDescription,\n"
    "                    mclMaterialClassID, rqsDateStarted, rqsDateCompleted,\n"
    "                    rqsQuantityRequested)\n"
    "SELECT r.rqsPK, r.rqsProductionRequestID, r.rqsWorkOrder, r.rqsDescription,\n"
    "       r.mclMaterialClassID, r.rqsDateStarted, r.rqsDateCompleted,\n"
    "       r.rqsQuantityRequested\n"
    "FROM dbo.production_request_v r\n"
    "WHERE r.rqsProductionRequestID = ?\n"
    "   OR r.rqsProductionRequestID LIKE ?\n"
    "   OR r.rqsWorkOrder           = ?\n"
    "OPTION (RECOMPILE)")
SQL_WO_HEAD_READ = (
    "SELECT rqsPK, rqsProductionRequestID, rqsWorkOrder, rqsDescription,\n"
    "       mclMaterialClassID, rqsDateStarted, rqsDateCompleted, rqsQuantityRequested\n"
    "FROM #board_pr ORDER BY rqsProductionRequestID")

#: W2 名冊（**MPR canonical**；序號去重以 `matPK`）。
#: ⚠ 走 `material_production_request`、**不走** `materials_t.rqsPK`（Gate A §5 canonical；
#:   後者只當對照＝`diag.n_legacy_rqs_mismatch`，README §C-1）。
#: ⚠ `GROUP BY` 在這裡**合法**：三張都是**表**不是大 view（C-HT4 禁的是大 view 上的聚合）。
#: ⚠ `INSERT INTO #board_ser`（同 W1 的理由；空殼在 `SQL_WO_DROP_TEMPS`）。
SQL_WO_SERIALS = (
    "INSERT INTO #board_ser (matPK, lot, mcl, mdf, status_id, legacy_rqs, n_link,\n"
    "                     first_assign, last_assign, out_status)\n"
    "SELECT TOP (?) m.matPK, lot = m.matMaterialLotID, mcl = mcl.mclMaterialClassID,\n"
    "       mdf = mdf.mdfMaterialDefinitionID, status_id = m.StatusId,\n"
    "       legacy_rqs = m.rqsPK, n_link = COUNT(*),\n"
    "       first_assign = MIN(x.AssignmentTime), last_assign = MAX(x.AssignmentTime),\n"
    "       out_status = MAX(x.OutgoingStatusId)\n"
    "FROM dbo.material_production_request x\n"
    "JOIN dbo.materials_t          m   ON m.matPK   = x.MaterialId\n"
    "JOIN dbo.material_definition_t mdf ON mdf.mdfPK = m.mdfPK\n"
    "JOIN dbo.material_class_t      mcl ON mcl.mclPK = mdf.mclPK\n"
    "WHERE x.ProductionRequestId IN (SELECT p.rqsPK FROM #board_pr p)\n"
    "GROUP BY m.matPK, m.matMaterialLotID, mcl.mclMaterialClassID,\n"
    "         mdf.mdfMaterialDefinitionID, m.StatusId, m.rqsPK\n"
    "OPTION (RECOMPILE)")
SQL_WO_SERIALS_INDEX = "CREATE INDEX ix_board_ser ON #board_ser (matPK)"
SQL_WO_SERIALS_READ = (
    "SELECT matPK, lot, mcl, mdf, status_id, legacy_rqs, n_link,\n"
    "       first_assign, last_assign, out_status\n"
    "FROM #board_ser ORDER BY lot")

#: W3a 製程段（**`segMESReserved` 硬排除寫進凍結 SQL、不讓 agent 事後濾**）。
#: ⚠ **刻意不濾 `stpEndTime`**（沒收尾的段就是「它現在在那一站」）。日後若真要濾，一律
#:   `stpEndTime >= stpStartTime`、**絕不 `>`**——`>` 會刪掉合法零工期段、八站整站隱形。
#: ⚠ 「最後一段」的聚合**全在 Python** 以 `max(stpPK)` 做（`stpStartTime` 會並列、不決定）。
#: ⚠ `INSERT INTO #board_seg`（同 W1 的理由；空殼在 `SQL_WO_DROP_TEMPS`）。
SQL_WO_SEGMENTS = (
    "INSERT INTO #board_seg (stpPK, matPK, SegmentClassID,\n"
    "                     stpStartTime, stpEndTime, stpTestResult,\n"
    "                     SegmentProductionRequestCode)\n"
    "SELECT TOP (?) v.stpPK, v.matPK, v.SegmentClassID,\n"
    "       v.stpStartTime, v.stpEndTime, v.stpTestResult,\n"
    "       v.SegmentProductionRequestCode\n"
    "FROM dbo.process_segment_v v\n"
    "JOIN #board_ser s ON s.matPK = v.matPK\n"
    "WHERE v.stpStartTime >= ?\n"
    "  AND v.SegmentClassID <> '" + MES_RESERVED_CLASS + "'\n"
    "OPTION (RECOMPILE)")
SQL_WO_SEGMENTS_INDEX = "CREATE INDEX ix_board_seg ON #board_seg (matPK, stpPK)"
SQL_WO_SEGMENTS_READ = (
    "SELECT stpPK, matPK, SegmentClassID, stpStartTime, stpEndTime,\n"
    "       stpTestResult, SegmentProductionRequestCode\n"
    "FROM #board_seg")

#: W3b MES 事件段（**只在「排除後還有序號一段都沒有」時才跑**；2019 舊單 212/224 就是這種）。
#: `NOT EXISTS (… #board_seg …)` 把母體收到「真的沒有製程段」那幾顆上＝不對大 view 多掃一趟全量。
SQL_WO_EVENTS = (
    "SELECT TOP (?) v.matPK, v.stpStartTime\n"
    "FROM dbo.process_segment_v v\n"
    "JOIN #board_ser s ON s.matPK = v.matPK\n"
    "WHERE v.stpStartTime >= ?\n"
    "  AND v.SegmentClassID = '" + MES_RESERVED_CLASS + "'\n"
    "  AND NOT EXISTS (SELECT 1 FROM #board_seg g WHERE g.matPK = v.matPK)\n"
    "OPTION (RECOMPILE)")

#: ⚠ **W4（扇出）＝不做**：§7.5 之二已證以 `matPK` 分組的 `n_serial_multi_board` **結構上永遠 0**
#:   （重工＝同 lot 新 `matPK`），可見面＝`n_serial_mtlpk > n_serial_lot_dst` ＋逐序號
#:   `last_segment_wo`／`last_wo_is_this_wo` 自揭欄 ⇒ 省一條查詢、不為它停筆。


def ALL_SQL():
    """本模組會送出的**全部 SQL 文本**（selftest 形狀／負斷言的唯一清單）。

    新增任何查詢**必須**登記在這裡——否則人名零出現、`rqsCurrentEquipment`／`rqsSegmentState`
    零出現、大 view 零 `DISTINCT`／`GROUP BY`、每條 `OPTION (RECOMPILE)` 的負斷言會有漏網之魚
    （`mes_family.ALL_SQL`／`mes_rework.ALL_SQL` 同款制度）。
    """
    return (SQL_VARS_GUARD, SQL_CLASS_EXISTS, SQL_CLASS_VARS,
            SQL_WO_GUARD, SQL_WO_DROP_TEMPS, SQL_WO_HEAD, SQL_WO_HEAD_READ,
            SQL_WO_SERIALS, SQL_WO_SERIALS_INDEX, SQL_WO_SERIALS_READ,
            SQL_WO_SEGMENTS, SQL_WO_SEGMENTS_INDEX, SQL_WO_SEGMENTS_READ,
            SQL_WO_EVENTS)


def open_connection(timeout=QUERY_TIMEOUT_SEC):
    """開一條唯讀連線（`mes_qlik._connect` 重用）。回 `(conn, cur)`。

    ⚠ 呼叫端（`board_data`）負責 `conn.timeout = LIVE_QUERY_TIMEOUT_SEC` 與 `finally: close()`。
    """
    return _connect(timeout=timeout)


#: `diag.error_detail` 的上限（字元）——夠放 SQLSTATE ＋ SQL Server 訊息，不放整段 traceback。
ERROR_DETAIL_MAX = 300


def error_detail(e, limit=ERROR_DETAIL_MAX):
    """例外 → `diag.error_detail` 的**一行**（記錄級兩支工具的降級形共用，**單一出處**）。

    ★ 為什麼要有這一欄（2026-09-21 live 首跑教訓）：當時 `wo_serials_status` 只回
      `unavailable_reason="error:ProgrammingError"` ＋ **`diag:{}`** ⇒ 型別名之外一個字都沒有、
      **無法診斷**（真因是臨時表作用域，卻要靠人猜）。分類碼回答「哪一類壞了」，
      `error_detail` 回答「MES 到底說了什麼」——兩者都要，**不互相取代**。
    ⚠ 這是**降級路徑專用**：成功路徑的 `diag` 一個位元都不多。
    ⚠ 內容是 SQL Server／ODBC 的原話（含 SQLSTATE 與物件名）；連線層失敗時原話可能帶主機名，
      **不另外加工**（加工＝製造第二份「什麼算安全」的事實），要收斂請在消費端裁。
    """
    return f"{type(e).__name__}: {str(e)[:int(limit)]}"


# =============================================================================
# 3. 純函式層（selftest 主戰場；全部離線可測、零 I/O）
# =============================================================================
def normalize_cls(cls):
    """`cls` → `(ok, err)`；`err` 是要回給 agent 的錯誤物件（`None`＝通過）。

    · 空／非字串 ⇒ `missing_cls`（逐位元同 `app.py` 的 400 body 形）。
    · 不合格式 ⇒ `bad_cls`（白名單 `^[A-Za-z][A-Za-z0-9_]{0,74}$`，對齊 varchar(75)）。
    · `segMESReserved` ⇒ **硬拒** `excluded_class`（§7.6.7 新事實②：它自己登記了 34 個變數，
      但它是 MES 事件稽核流水、不是生產站）。
    """
    if not isinstance(cls, str) or not cls.strip():
        return None, {"error": "missing_cls",
                      "hint": "call list_stations and pass an exact cls"}
    s = cls.strip()
    if not CLS_RE.match(s):
        return None, {"error": "bad_cls", "given": cls,
                      "hint": "cls looks like segDiePlacement (letters, digits and _ only, <=75 chars)"}
    if s == MES_RESERVED_CLASS:
        return None, {"error": "excluded_class", "given": s,
                      "hint": ("segMESReserved is the MES internal event audit stream, "
                               "not a production station")}
    return s, None


def _vars_blank(cls, *, found, reason=None, unavailable_reason=None, diag=None):
    """`station_variables` 的骨架（**鍵集合固定**；查不到也長同一個形）。"""
    return {"cls": cls, "found": found, "class_description": None, "n_vars": None,
            "groups": [], "uom_census": {}, "notes": list(STATION_VARS_NOTES),
            "reason": reason, "unavailable_reason": unavailable_reason,
            "diag": dict(diag or {})}


def derive_station_vars(cls, cls_row, var_rows, *, cap_hit=False, elapsed_s=None):
    """S1 的 class 列 ＋ S2 的變數列 → `board.station_vars.v1` 的 payload（純函式、零 I/O）。

    · `cls_row is None` ⇒ `found=False` ＋ `reason="class_not_in_mes"`（**MES 裡真的沒有**）。
      ⚠ 這與「demo fixture 沒有這一筆」是**兩件事**，後者由 `board_data` 填
      `unavailable_reason="not_in_demo_fixture"`——混用就是把「我沒這份樣本」說成「MES 沒這一站」。
    · `cls_row` 在而 `var_rows` 空 ⇒ `found=True`、`n_vars=0`、`groups=[]`（**存在但沒有**）。
    · 分群按 `vdfClassID`（§7.6.6：「none 按 vdfClassID 分群講」），群序＝`PDD_CLASS_IDS`，
      群內序＝`vdfProductionDataID`（＝S2 的 `ORDER BY`，確定性）。
    · **`vdfPK` 不外送**（內部主鍵、對答題無用）。
    · 值域外的 `vdfClassID`／`vdfType`／`vdfUOM` **原樣轉述**（不丟、不猜）——閉集是 2026-09-21
      的實測，不是契約；真的出現新值時，誠實印出來比靜默歸類有用。
    """
    if cls_row is None:
        return _vars_blank(cls, found=False, reason="class_not_in_mes",
                           diag={"vars_cap_hit": bool(cap_hit), "elapsed_s": elapsed_s})
    out = _vars_blank(cls, found=True,
                      diag={"vars_cap_hit": bool(cap_hit), "elapsed_s": elapsed_s})
    desc = cls_row[2] if len(cls_row) > 2 else None
    out["class_description"] = desc if isinstance(desc, str) and desc.strip() else None

    by_class, census, n = {}, {}, 0
    for r in var_rows:
        r = tuple(r)
        if len(r) < 6 or r[1] is None:
            continue                      # 壞列丟棄不炸（QG-3.2）
        _pk, name, vdesc, class_id, vtype, uom = r[:6]
        rec = {"name": str(name),
               "description": (vdesc if isinstance(vdesc, str) and vdesc.strip() else None),
               "type": vtype, "uom": uom}
        by_class.setdefault(class_id, []).append(rec)
        census[uom] = census.get(uom, 0) + 1
        n += 1

    order = list(PDD_CLASS_IDS) + [k for k in sorted(by_class, key=str)
                                   if k not in PDD_CLASS_IDS]
    out["groups"] = [{"class_id": k, "n": len(by_class[k]), "variables": by_class[k]}
                     for k in order if k in by_class]
    # census 依計數降冪、同數依鍵名（確定性；agent 一眼看主單位）
    out["uom_census"] = {k: v for k, v in sorted(census.items(),
                                                 key=lambda kv: (-kv[1], str(kv[0])))}
    out["n_vars"] = n
    return out


def ok_station_vars(rec):
    """`board.station_vars.v1` 的形狀檢查（**fixture validator 與 smoke 共用同一份**）。

    白名單欄 ＋ enum 健檢 ＋ 內部自洽（`sum(group.n) == n_vars`、census 總數 ＝ `n_vars`）。
    壞資料回 `False`、**不丟例外**（QG-3.2）。
    """
    try:
        if not isinstance(rec, dict):
            return False
        # `_` 前綴＝fixture 自帶的期望值註記（`_fixtures/README.md`），非契約欄。
        keys = {k for k in rec if not k.startswith("_")} - {"source", "provenance"}
        if not keys <= set(STATION_VARS_KEYS) or "cls" not in rec or "found" not in rec:
            return False
        if not isinstance(rec["cls"], str) or not CLS_RE.match(rec["cls"]):
            return False
        if rec["cls"] == MES_RESERVED_CLASS:          # 硬排除的 class 不得出現在 golden
            return False
        if not isinstance(rec["found"], bool):
            return False
        if not isinstance(rec.get("groups"), list) or not isinstance(rec.get("uom_census"), dict):
            return False
        if not rec["found"]:
            return rec.get("n_vars") is None and rec["groups"] == []
        n = rec.get("n_vars")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return False
        total = 0
        for g in rec["groups"]:
            if set(g) != {"class_id", "n", "variables"}:
                return False
            if not isinstance(g["variables"], list) or g["n"] != len(g["variables"]):
                return False
            for v in g["variables"]:
                if set(v) != {"name", "description", "type", "uom"}:
                    return False
                if not isinstance(v["name"], str) or not v["name"]:
                    return False
                if v["type"] not in PDD_TYPES:        # enum 健檢（值域外的 golden 不收）
                    return False
                if not isinstance(v["uom"], str) or not v["uom"]:
                    return False
                # ★ 編碼往返：`µ` 必須是 **U+00B5 MICRO SIGN**（探針實測值），
                #   不是 U+03BC 希臘小寫 μ——換了就代表某一層做過 normalize，往返已破。
                if "μ" in v["uom"]:
                    return False
            total += g["n"]
        if total != n:
            return False
        if any("μ" in str(k) for k in rec["uom_census"]):
            return False
        if sum(rec["uom_census"].values()) != n:
            return False
        return True
    except Exception:                                  # noqa: BLE001 - validator 恆不炸
        return False


# ---- `wo_serials_status` 的純函式層 -------------------------------------------------
_SERIAL_COUNT_NOTE = ("序號隨生產逐批掛上；活單的序號數低於 qty_requested 是常態，不是漏料"
                      "（探針第二輪之一實證：240 的單當下只有 180 顆，分三天掛上）。")
_COMPLETED_NOTE = ("MES 會在工單**還在跑**時就填這一欄（`WO910001/1` 11:15:21 已填、"
                   "段仍跑到 11:25:40）⇒ **不可當「已完工」判準**。")
_NOT_RECORDED = "未記錄"

#: 九條限制的一句話版（**逐條**；`caveats` 直接送給 agent 轉述）。
def _caveats(assignment_reason):
    return [
        "①「最後一站」指的是**最後一筆已完成的製程段**；本工具不做未結段語意"
        "（三張探針單合計 464 顆序號零未結段）。",
        "② `assigned_at`（序號投料進本工單的時刻）只在活單可信；本次判定＝"
        f"`{assignment_reason}`，`diag.assignment_time_usable` 為準。"
        "⚠ `rqsDateCompleted IS NULL` **不是**活單閘（完工日會在還在跑時就填）。",
        "③ 重工＝同一個 lot 新開一個物料實體 ⇒ 扇出以 `matPK` 分組時結構上恆為 0；"
        "可見面＝`serial_count − n_serial_lot_dst`（`n_rework_instances`）與逐序號的"
        "`last_segment_wo`／`last_wo_is_this_wo` 自揭欄。",
        "④ `serial_count` 與 `qty_requested` 是兩個數字，**並列看**、不要相減當缺料。",
        "⑤ `status_code` 由 `StatusId` 對 13 值 enum 轉出；`outgoing_status_id` **只印數字**"
        "——MES 沒有 `OutgoingStatus` 對照表，150 萬列的值域只是與 `MaterialStatus.Id`"
        "**對齊**（缺 9／13），本批未查 `sys.foreign_keys` ⇒ 不得寫「依 MaterialStatus 對照」。",
        "⑥ `last_result` 是 MES 原值直送（PASS／FAIL／null），不翻譯、不映射。"
        "⚠ 探針樣本裡 FAIL 極少（464 顆中只有 2019 舊單的 4 顆）⇒ FAIL 路徑幾乎未經實測。",
        "⑦ `n_seg` 是**已完成段數的下界**，不是「第幾站」：同一個 class 可在一條路線出現兩次"
        "（`segCleanTest` 在第 4 與第 8 序）、`flsOrder` 非唯一鍵 ⇒ 要答「在第幾站」"
        "得等 `wo_route`（未上線）。",
        "⑧ `last_segment_duration_s` 為 null ＋ `duration_not_measurable=true` ＝**量不到**、"
        "不是 0 秒。零工期是**站屬性**（秤重／組裝／OQC 站 100% start==end），"
        "不是 `segMESReserved` 專屬。",
        "⑨ `diag.since_used` 是段的時間下界（此前的段不在本結果內）；三個 `*_cap_hit` 為真"
        "代表結果被上限截斷、數字只是下界。",
    ]


def normalize_wo_id(wo_id):
    """`wo_id` → `(normalized, err)`；`err` 是要回給 agent 的錯誤物件（`None`＝通過）。

    格式白名單 `^[A-Za-z0-9]{1,40}(/\\d{1,3})?$`——同時解掉 LIKE 萬用字元問題
    （排除 `%` `_` `[`）。MES 工單號形如 `WO910001/1`、SAP 單號形如 `9000100001`。
    """
    if not isinstance(wo_id, str) or not wo_id.strip():
        return None, {"error": "missing_wo_id",
                      "hint": "work order looks like WO910001/1 (MES) or 9000100001 (SAP)"}
    s = wo_id.strip()
    if not WO_ID_RE.match(s):
        return None, {"error": "bad_wo_id", "given": wo_id,
                      "hint": "work order looks like WO910001/1 (MES) or 9000100001 (SAP)"}
    return s, None


def wo_base(wo_id):
    """去掉 `/n` 改版後綴（`WO910001/1` → `WO910001`）。**W1 的 LIKE 參數靠它**。"""
    return str(wo_id).split("/", 1)[0]


def status_code(status_id):
    """`StatusId` → enum 名；**值域外回 `(None, note)`，絕不猜**（限制⑤）。"""
    if isinstance(status_id, bool) or not isinstance(status_id, int):
        return None, f"StatusId {status_id!r} is not an integer; no enum name is claimed"
    name = MATERIAL_STATUS.get(status_id)
    if name is None:
        return None, (f"StatusId {status_id} is outside the 13 known MaterialStatus values "
                      "(1-13); no enum name is claimed")
    return name, None


def _dt(v):
    """任意時刻值 → `datetime`；認不得回 None（**不猜、不補**）。"""
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        s = v.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _ts(v):
    """`datetime`／字串 → `'YYYY-MM-DD HH:MM:SS[.ffff]'`（有小數秒才帶）；認不得回 None。"""
    d = _dt(v)
    if d is None:
        return None
    if d.microsecond:
        return d.strftime("%Y-%m-%d %H:%M:%S.") + f"{d.microsecond:06d}"[:4]
    return d.strftime(_TS_FMT)


def _wo_blank(wo_id, *, found, reason=None, unavailable_reason=None, diag=None):
    """`wo_serials_status` 的骨架（**鍵集合固定**；查不到也長同一個形）。"""
    return {"wo_id": wo_id, "found": found, "work_order": None,
            "serial_count": None, "qty_requested": None, "serial_count_note": None,
            "n_serial_lot_dst": None, "n_rework_instances": None,
            "last_station_distribution": [], "serials": [], "caveats": [],
            "reason": reason, "unavailable_reason": unavailable_reason,
            "diag": dict(diag or {})}


def _pick_head(wo_id, head_rows):
    """多列工單頭（`/n` 改版）→ `(本單那一列, 全部 rqs_id, 全部 rqsPK)`。

    優先取 `rqsProductionRequestID` 逐字等於輸入的那一列；再取 `rqsWorkOrder` 相等的；
    都沒有才取第一列（＝只有改版命中時）。**不猜哪一版才是「正確的」**，改版全列進
    `work_order.revisions`。
    """
    rows = [tuple(r) for r in head_rows]
    exact = [r for r in rows if r[1] == wo_id]
    by_sap = [r for r in rows if r[2] == wo_id]
    pick = (exact or by_sap or rows)[0]
    return pick, sorted({r[1] for r in rows if r[1]}), {r[0] for r in rows}


def derive_wo_serials(wo_id, head_rows, ser_rows, seg_rows, evt_rows, *,
                      since_used=SINCE_DEFAULT, fetched_at=None,
                      ser_cap_hit=False, seg_cap_hit=False, evt_cap_hit=False,
                      n_mpr_rows=None, elapsed_s=None, recent_h=LIVE_RECENT_H):
    """W1–W3b 的列 → `board.wo_serials.v1` 的 payload（**純函式、零 I/O**）。

    列形（＝各條 SQL 的 SELECT 欄序）：
      · `head_rows`：`(rqsPK, rqs_id, sap_wo, desc, mcl, started, completed, qty_req)`
      · `ser_rows` ：`(matPK, lot, mcl, mdf, status_id, legacy_rqs, n_link,
                       first_assign, last_assign, out_status)`
      · `seg_rows` ：`(stpPK, matPK, cls, seg_start, seg_end, res, seg_board)`
      · `evt_rows` ：`(matPK, stpStartTime)`（只含「排除 segMESReserved 後無製程段」那幾顆）

    九條限制逐條落在這裡（詳見模組 docstring）；**回傳裡永遠沒有未結段語意的任何一鍵**。
    """
    if not head_rows:
        return _wo_blank(wo_id, found=False, reason="no_production_request_matched",
                         diag={"since_used": since_used, "serial_cap_hit": bool(ser_cap_hit),
                               "segment_cap_hit": bool(seg_cap_hit),
                               "event_cap_hit": bool(evt_cap_hit),
                               "n_rqs": 0, "elapsed_s": elapsed_s})
    head, revisions, rqs_pks = _pick_head(wo_id, head_rows)
    rqs_ids = set(revisions)
    completed_dt = _dt(head[6])
    fetched_dt = _dt(fetched_at) or datetime.now()

    # 最後一段＝`max(stpPK)`（**不是** `stpStartTime`：零工期／同批寫入會並列、不決定）。
    last_seg, n_seg_by_mlt, latest_start = {}, {}, None
    for r in seg_rows:
        r = tuple(r)
        if len(r) < 7 or r[1] is None:
            continue                                   # 壞列丟棄不炸
        pk, mlt = r[0], r[1]
        n_seg_by_mlt[mlt] = n_seg_by_mlt.get(mlt, 0) + 1
        prev = last_seg.get(mlt)
        if prev is None or (pk is not None and prev[0] is not None and pk > prev[0]):
            last_seg[mlt] = r
        st = _dt(r[3])
        if st is not None and (latest_start is None or st > latest_start):
            latest_start = st
    last_evt = {}
    for r in evt_rows:
        r = tuple(r)
        if len(r) < 2 or r[0] is None:
            continue
        d = _dt(r[1])
        if d is not None and (last_evt.get(r[0]) is None or d > last_evt[r[0]]):
            last_evt[r[0]] = d

    # ② 活單閘：先看有沒有段 → 再看批次蓋章形 → 最後看最新段距今。
    norm_ser = []
    for r in ser_rows:
        r = tuple(r)
        if len(r) < 10 or r[0] is None:
            continue
        norm_ser.append(r)
    n_stamp = sum(1 for r in norm_ser
                  if completed_dt is not None and _dt(r[7]) == completed_dt)
    if latest_start is None:
        usable, reason_a = False, "no_segment"
    elif norm_ser and (n_stamp / len(norm_ser)) >= BATCH_STAMP_SHARE:
        usable, reason_a = False, "batch_stamp"
    elif (fetched_dt - latest_start) <= timedelta(hours=recent_h):
        usable, reason_a = True, "latest_segment_recent"
    else:
        usable, reason_a = False, "no_recent_segment"

    serials, n_legacy_mismatch, n_no_seg = [], 0, 0
    for r in norm_ser:
        mlt, lot, mcl, _mdf, sid, legacy_rqs, _n_link, first_assign, _last_assign, out_st = r[:10]
        if legacy_rqs is None or legacy_rqs not in rqs_pks:
            n_legacy_mismatch += 1
        code, code_note = status_code(sid)
        seg = last_seg.get(mlt)
        rec = {
            "lot": lot, "material_class": mcl,
            "status_id": sid, "status_code": code, "status_code_note": code_note,
            "outgoing_status_id": out_st,
            "last_station": None, "last_segment_start": None, "last_segment_end": None,
            "last_segment_duration_s": None, "duration_not_measurable": False,
            "last_result": None, "last_segment_wo": None, "last_wo_is_this_wo": None,
            "n_seg": n_seg_by_mlt.get(mlt, 0),
            "assigned_at": (_ts(first_assign) if usable else None),
            "no_process_segment": seg is None,
            "last_event_at": None,
        }
        if seg is None:
            n_no_seg += 1
            rec["last_event_at"] = _ts(last_evt.get(mlt))
            rec["duration_not_measurable"] = True     # 沒有段＝工期量不到（**不是 0**）
        else:
            _pk, _mlt, cls, s_start, s_end, res, seg_board = seg[:7]
            d0, d1 = _dt(s_start), _dt(s_end)
            rec["last_station"] = cls
            rec["last_segment_start"] = _ts(s_start)
            rec["last_segment_end"] = _ts(s_end)
            rec["last_result"] = res                   # ⑥ 原值直送
            rec["last_segment_wo"] = seg_board
            rec["last_wo_is_this_wo"] = (seg_board in rqs_ids) if seg_board is not None else None
            if d0 is not None and d1 is not None and d1 > d0:
                rec["last_segment_duration_s"] = round((d1 - d0).total_seconds(), 1)
            else:
                rec["duration_not_measurable"] = True  # ⑧ start==end／end 缺 ⇒ null、**不是 0**
        serials.append(rec)

    # 站別分布（三種桶：真站／(僅 MES 事件)／(無製程段／未開工)）
    buckets = {}
    for rec in serials:
        if rec["last_station"]:
            key, when = rec["last_station"], rec["last_segment_start"]
        elif rec["last_event_at"]:
            key, when = NO_SEGMENT_BUCKET, rec["last_event_at"]
        else:
            key, when = NO_ACTIVITY_BUCKET, None
        b = buckets.setdefault(key, {"station": key, "n_serial": 0,
                                     "earliest": None, "latest": None})
        b["n_serial"] += 1
        if when:
            b["earliest"] = when if b["earliest"] is None else min(b["earliest"], when)
            b["latest"] = when if b["latest"] is None else max(b["latest"], when)
    dist = sorted(buckets.values(), key=lambda b: (-b["n_serial"], b["station"]))

    n_lot = len({r["lot"] for r in serials if r["lot"] is not None})
    qty = head[7]
    if isinstance(qty, float) and qty.is_integer():
        qty = int(qty)                                 # `rqsQuantityRequested` 是 real
    out = _wo_blank(wo_id, found=True)
    out["work_order"] = {
        "rqs_id": head[1], "sap_wo": head[2], "description": head[3],
        "material_class": head[4],
        "started_at": _ts(head[5]),
        "started_at_note": None if _ts(head[5]) else _NOT_RECORDED,
        "completed_at": _ts(head[6]),
        "completed_at_note": _COMPLETED_NOTE if _ts(head[6]) else None,
        "qty_requested": qty,
        "revisions": revisions,
    }
    out["serial_count"] = len(serials)
    out["qty_requested"] = qty
    out["serial_count_note"] = _SERIAL_COUNT_NOTE
    out["n_serial_lot_dst"] = n_lot
    out["n_rework_instances"] = len(serials) - n_lot
    out["last_station_distribution"] = dist
    out["serials"] = serials
    out["caveats"] = _caveats(reason_a)
    out["diag"] = {
        "since_used": since_used,
        "serial_cap_hit": bool(ser_cap_hit), "segment_cap_hit": bool(seg_cap_hit),
        "event_cap_hit": bool(evt_cap_hit),
        "n_rqs": len(head_rows),
        "n_mpr_rows": n_mpr_rows,
        "n_serial_no_segment": n_no_seg,
        "n_legacy_rqs_mismatch": n_legacy_mismatch,
        "assignment_time_usable": usable,
        "assignment_time_reason": reason_a,
        "excluded_mes_reserved": True,
        "fetched_at": _ts(fetched_dt),
        "elapsed_s": elapsed_s,
    }
    return out


def ok_wo_serials(rec):
    """`board.wo_serials.v1` 的形狀檢查（**fixture validator 與 smoke 共用同一份**）。

    白名單欄 ＋ 九條限制的可執行面 ＋ 內部自洽。壞資料回 `False`、**不丟例外**（QG-3.2）。
    """
    try:
        if not isinstance(rec, dict):
            return False
        keys = {k for k in rec if not k.startswith("_")} - {"source", "provenance"}
        if not keys <= set(WO_SERIALS_KEYS) or "wo_id" not in rec or "found" not in rec:
            return False
        if not isinstance(rec["found"], bool):
            return False
        blob = json.dumps(rec, ensure_ascii=False, default=str)
        for bad in FORBIDDEN_OPEN_KEYS:               # ① 未結段語意零出現
            if f'"{bad}"' in blob:
                return False
        for bad in ("rqs_current_equipment", "rqs_segment_state",
                    "rqsCurrentEquipment", "rqsSegmentState"):
            if bad in blob:                            # §7.6.6：兩欄不可當現況
                return False
        if not rec["found"]:
            return rec.get("serial_count") is None and rec.get("serials") == []
        wo = rec.get("work_order")
        if not isinstance(wo, dict) or not wo.get("rqs_id"):
            return False
        if set(wo) != {"rqs_id", "sap_wo", "description", "material_class", "started_at",
                       "started_at_note", "completed_at", "completed_at_note",
                       "qty_requested", "revisions"}:
            return False
        if not isinstance(wo["revisions"], list) or wo["rqs_id"] not in wo["revisions"]:
            return False
        if wo["started_at"] is None and wo["started_at_note"] is None:
            return False                               # 未記錄必須說出來
        serials = rec.get("serials")
        if not isinstance(serials, list) or rec.get("serial_count") != len(serials):
            return False
        n_lot = len({s.get("lot") for s in serials if s.get("lot") is not None})
        if rec.get("n_serial_lot_dst") != n_lot:
            return False
        if rec.get("n_rework_instances") != len(serials) - n_lot:
            return False
        for s in serials:
            if set(s) != set(SERIAL_KEYS):
                return False
            sid, code = s.get("status_id"), s.get("status_code")
            if code is not None and MATERIAL_STATUS.get(sid) != code:
                return False                           # ⑤ 猜出來的 enum 名不收
            if code is None and sid is not None and s.get("status_code_note") is None:
                return False                           # 值域外必須說出來
            if s.get("last_result") not in (None, "PASS", "FAIL"):
                return False                           # ⑥ 原值直送；其餘值要先回探針
            d = s.get("last_segment_duration_s")
            if d is None and not s.get("duration_not_measurable"):
                return False                           # ⑧ 量不到必須掛旗
            if d is not None and (s.get("duration_not_measurable") or d <= 0):
                return False                           # ⑧ **絕不填 0**
            if s.get("no_process_segment"):
                # 無製程段 ⇒ 站必為 null。`last_event_at` **可以是 null**：那是第三種形
                # 「已投料、還沒開工」（連 MES 事件都沒有）＝`(無製程段／未開工)` 桶。
                if s.get("last_station") is not None:
                    return False
            elif not s.get("last_station"):
                return False
            if not isinstance(s.get("n_seg"), int) or isinstance(s.get("n_seg"), bool):
                return False
        # 站別分布與序號自洽
        dist = rec.get("last_station_distribution")
        if not isinstance(dist, list) or sum(b["n_serial"] for b in dist) != len(serials):
            return False
        for b in dist:
            if set(b) != {"station", "n_serial", "earliest", "latest"}:
                return False
        diag = rec.get("diag") or {}
        for k in ("since_used", "serial_cap_hit", "segment_cap_hit", "event_cap_hit",
                  "assignment_time_usable", "assignment_time_reason"):
            if k not in diag:
                return False                           # ⑨ 四欄 ＋ ② 兩欄必附
        if diag["assignment_time_reason"] not in ASSIGNMENT_REASONS:
            return False
        if not diag["assignment_time_usable"] and any(s["assigned_at"] is not None
                                                      for s in serials):
            return False                               # ② 不可信就一顆都不印
        if len(rec.get("caveats") or []) < 9:
            return False                               # 九條限制逐條
        return True
    except Exception:                                  # noqa: BLE001 - validator 恆不炸
        return False


# =============================================================================
# 4. I/O 編排（唯一的 I/O 點；cursor 由呼叫端傳入或由 `open_connection` 開）
# =============================================================================
def assert_vars_guard(guard):
    """承重物件／欄檢查（純函式＝離線可測）。ABSENT ⇒ `SchemaDivergence`、逐項具名。"""
    missing = [k for k in VARS_GUARD_KEYS if not guard.get(k)]
    if missing:
        raise SchemaDivergence("schema divergence: " + ", ".join(f"{k} ABSENT" for k in missing))
    return True


def fetch_vars_guard(cur):
    """S0 欄位守衛。回 dict（全 bool）：`VARS_GUARD_KEYS` 逐鍵。"""
    cur.execute(SQL_VARS_GUARD)
    r = cur.fetchone()
    guard = {k: bool(r[i]) for i, k in enumerate(VARS_GUARD_KEYS)}
    assert_vars_guard(guard)
    return guard


def compute_station_vars(cur, cls, cap=VARS_CAP):
    """`station_variables(cls)` 的唯一 I/O 編排點。回 `board.station_vars.v1` 的 payload。

    `cls` **必須是已經過 `normalize_cls` 的值**（呼叫端在工具層擋掉格式與 `segMESReserved`）。
    失敗一律往上拋（`board_data` 接成誠實降級形，**絕不回空清單**——空清單的意思是
    「查過了、這站零變數」，拿它頂替失敗＝把「不知道」畫成「沒有」）。
    """
    t0 = time.perf_counter()
    fetch_vars_guard(cur)
    cur.execute(SQL_CLASS_EXISTS, cls)
    row = cur.fetchone()
    cls_row = tuple(row) if row is not None else None
    var_rows = []
    if cls_row is not None:
        cur.execute(SQL_CLASS_VARS, int(cap), cls)
        var_rows = [tuple(r) for r in cur.fetchall()]
    return derive_station_vars(cls, cls_row, var_rows,
                               cap_hit=len(var_rows) >= int(cap),
                               elapsed_s=round(time.perf_counter() - t0, 3))


def assert_wo_guard(guard):
    """W0 承重物件／欄檢查（純函式＝離線可測）。ABSENT ⇒ `SchemaDivergence`、逐項具名。"""
    missing = [k for k in WO_GUARD_KEYS if not guard.get(k)]
    if missing:
        raise SchemaDivergence("schema divergence: " + ", ".join(f"{k} ABSENT" for k in missing))
    return True


def fetch_wo_guard(cur):
    """W0 欄位守衛。回 dict（全 bool）：`WO_GUARD_KEYS` 逐鍵。"""
    cur.execute(SQL_WO_GUARD)
    r = cur.fetchone()
    guard = {k: bool(r[i]) for i, k in enumerate(WO_GUARD_KEYS)}
    assert_wo_guard(guard)
    return guard


def compute_wo_serials(cur, wo_id, since=SINCE_DEFAULT,
                       ser_cap=SER_CAP, seg_cap=SEG_CAP, evt_cap=EVT_CAP):
    """`wo_serials_status(wo_id)` 的唯一 I/O 編排點。回 `board.wo_serials.v1` 的 payload。

    順序＝W0 guard → 清 `#temp` **並建三張空殼** → W1（`INSERT INTO #board_pr`）→ 讀頭
    → W2（`INSERT INTO #board_ser`）→ 讀名冊 → W3a（`INSERT INTO #board_seg`）→ 讀段
    →〔**只有還有序號一段都沒有時**〕W3b 事件 → `derive_*`。
    ⚠ **三張 `#temp` 只能在那個非參數化批次裡建**（`SQL_WO_DROP_TEMPS` 上方的作用域說明）；
      這裡任何一句改回 `SELECT … INTO #x` 就會在 live 炸 `ProgrammingError`，離線測不出來。

    · `wo_id` **必須是已經過 `normalize_wo_id` 的值**（格式白名單擋掉 LIKE 萬用字元）。
    · 失敗一律往上拋（`board_data` 接成誠實降級形，**絕不回空名冊**）。
    · `fetched_at` 取**本機時刻**（台北牆鐘，全 repo 同一把尺）＝限制② 的「距今」基準。
    """
    t0 = time.perf_counter()
    fetch_wo_guard(cur)
    cur.execute(SQL_WO_DROP_TEMPS)
    cur.execute(SQL_WO_HEAD, wo_id, wo_base(wo_id) + "/%", wo_id)
    cur.execute(SQL_WO_HEAD_READ)
    head_rows = [tuple(r) for r in cur.fetchall()]
    ser_rows = seg_rows = evt_rows = []
    if head_rows:
        cur.execute(SQL_WO_SERIALS, int(ser_cap))
        cur.execute(SQL_WO_SERIALS_INDEX)
        cur.execute(SQL_WO_SERIALS_READ)
        ser_rows = [tuple(r) for r in cur.fetchall()]
        cur.execute(SQL_WO_SEGMENTS, int(seg_cap), since)
        cur.execute(SQL_WO_SEGMENTS_INDEX)
        cur.execute(SQL_WO_SEGMENTS_READ)
        seg_rows = [tuple(r) for r in cur.fetchall()]
        # W3b **只在需要時跑**：排除 `segMESReserved` 後仍有序號一段都沒有（2019 舊單那型）。
        if len({r[1] for r in seg_rows}) < len(ser_rows):
            cur.execute(SQL_WO_EVENTS, int(evt_cap), since)
            evt_rows = [tuple(r) for r in cur.fetchall()]
    return derive_wo_serials(
        wo_id, head_rows, ser_rows, seg_rows, evt_rows,
        since_used=since, fetched_at=datetime.now(),
        ser_cap_hit=len(ser_rows) >= int(ser_cap),
        seg_cap_hit=len(seg_rows) >= int(seg_cap),
        evt_cap_hit=len(evt_rows) >= int(evt_cap),
        n_mpr_rows=len(ser_rows),
        elapsed_s=round(time.perf_counter() - t0, 3))


# =============================================================================
# 5. `--selftest`（純離線：合成列 ＋ 讀 `_fixtures/` golden；零 MES、零網路）
# =============================================================================
def _fixture(name):
    with open(os.path.join(BASE, "_fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def _selftest():
    n = 0

    def t(label, cond, detail=""):
        nonlocal n
        assert cond, f"selftest FAIL: {label}{(' — ' + str(detail)) if detail else ''}"
        n += 1

    # ---- SQL 文本：perf 紀律與負斷言（`ALL_SQL()` 是唯一清單）-----------------
    sqls = ALL_SQL()
    t("all-sql-nonempty", len(sqls) >= 14 and all(isinstance(s, str) and s for s in sqls))
    for s in sqls:
        # 只對「打 `dbo.*` 的 SELECT／INSERT…SELECT」要求 RECOMPILE；`DROP TABLE`／
        # `CREATE INDEX`／讀 `#temp` 的 SELECT 不適用（它們不吃計畫快取的那個病）。
        # `SQL_WO_DROP_TEMPS` 以 `IF` 起頭 ⇒ 不在此列：它三句都 `WHERE 1=0`、零列可掃。
        if s.lstrip().startswith(("SELECT", "INSERT")) and "dbo." in s:
            t("recompile", s.rstrip().endswith("OPTION (RECOMPILE)"), s[:60])
    blob = "\n".join(sqls)
    # ★ 空殼批次（`WHERE 1=0`）不算「會掃大 view 的那一條」：它只是拿來讓 SQL Server 推型別。
    seg_sql = [s for s in sqls if "FROM dbo.process_segment_v" in s and "WHERE 1=0" not in s]
    t("seg-sql-count", len(seg_sql) == 2)
    for s in seg_sql:
        t("seg-window", "v.stpStartTime >= ?" in s, s[:60])
        t("seg-no-distinct", "DISTINCT" not in s and "GROUP BY" not in s)
    t("seg-reserved-excluded", f"<> '{MES_RESERVED_CLASS}'" in SQL_WO_SEGMENTS)
    t("evt-reserved-only", f"= '{MES_RESERVED_CLASS}'" in SQL_WO_EVENTS)
    t("no-end-filter", "stpEndTime >" not in blob and "stpEndTime <" not in blob)
    t("wo-head-like-param", "rqsProductionRequestID LIKE ?" in SQL_WO_HEAD)
    # 母體走 MPR canonical（Gate A §5）；`materials_t.rqsPK` **只當對照欄**（legacy_rqs），
    # 不是 join 鍵——它在 WHERE 裡出現就是走回舊路。
    t("wo-mpr-canonical",
      "WHERE x.ProductionRequestId IN (SELECT p.rqsPK FROM #board_pr p)" in SQL_WO_SERIALS
      and "legacy_rqs = m.rqsPK" in SQL_WO_SERIALS
      and "m.rqsPK =" not in SQL_WO_SERIALS)
    for banned in ("stpPersonID", "OperatorId", "rqsRequestor", "rqsChangeLog",
                   "rqsCurrentEquipment", "rqsSegmentState"):
        t(f"no-{banned}", banned not in blob)
    for eav in ("segment_real_data_t", "segment_integer_data_t", "segment_string_data_t"):
        t(f"no-eav-{eav}", eav not in blob)
    t("no-dynamic-in-list", "IN (?" not in blob and "IN (@" not in blob)
    t("vars-sql-no-vdf-pk-leak", "p.vdfPK" in SQL_CLASS_VARS)   # 查得到、但 derive 不外送

    # ---- ★ 臨時表作用域（2026-09-21 live 首跑事故的回歸形；fixture 抓不到這一類）-------
    # pyodbc 帶參數的 `execute` 走 `sp_executesql`，其中 `SELECT … INTO #t` 建的表隨那次呼叫
    # 消失 ⇒ 下一句讀它必 `Invalid object name`。兩顆釘讓這一類 bug 以後開不出來：
    #   ① 任何**含 `?`** 的語句裡，`INTO #` 只准前面接 `INSERT`；
    #   ② 三張 `#temp` 一律在**非參數化**的 `SQL_WO_DROP_TEMPS` 批次裡建好。
    for s in sqls:
        if "?" not in s:
            continue
        for m in re.finditer(r"INTO\s+#", s):
            t("★ no-select-into-temp-in-parameterized",
              s[:m.start()].rstrip().upper().endswith("INSERT"), s[:70])
    t("★ temps-created-unparameterized", "?" not in SQL_WO_DROP_TEMPS)
    for _tmp in ("#board_pr", "#board_ser", "#board_seg"):
        t(f"★ temp-skeleton-{_tmp}",
          f"INTO {_tmp}\n" in SQL_WO_DROP_TEMPS
          and f"DROP TABLE {_tmp};" in SQL_WO_DROP_TEMPS
          and f"INSERT INTO {_tmp} (" in blob)
    t("★ temp-skeletons-are-empty", SQL_WO_DROP_TEMPS.count("WHERE 1=0") == 3)
    # 空殼的 `GROUP BY` 不可拿掉（沒有它 `COUNT(*)` 會塞一列 0 進 `#board_ser`）
    t("★ temp-skeleton-ser-keeps-group-by",
      "WHERE 1=0\nGROUP BY m.matPK" in SQL_WO_DROP_TEMPS)

    # ---- `error_detail`（降級路徑的可診斷性；純函式）--------------------------
    class _FakeProgrammingError(Exception):
        pass

    _e = _FakeProgrammingError("('42S02', \"[42S02] [Microsoft][ODBC Driver 17 for SQL Server]"
                               "[SQL Server]Invalid object name '#board_pr'. (208)\")")
    t("err-detail-has-type", error_detail(_e).startswith("_FakeProgrammingError: "))
    t("err-detail-has-sqlstate", "42S02" in error_detail(_e) and "#board_pr" in error_detail(_e))
    t("err-detail-truncated", len(error_detail(_FakeProgrammingError("x" * 5000)))
      <= ERROR_DETAIL_MAX + len("_FakeProgrammingError: "))

    # ---- 欄位出處：SQL 裡每個 MES 欄名都在 docstring 的出處表裡（釘 ③ 的回歸形）----
    doc = __doc__ or ""
    ident = re.compile(r"\b(?:rqs|mlt|mdf|mcl|stp|sgc|vdf|ash|ast)[A-Z][A-Za-z0-9_]*\b")
    extra = ("Id", "MaterialId", "ProductionRequestId", "AssignmentTime", "LastWorkTime",
             "OutgoingStatusId", "StatusId", "SegmentClassId", "VariableId",
             "SegmentProductionRequestCode")
    cols = set(ident.findall(blob)) | {c for c in extra if re.search(rf"\b{c}\b", blob)}
    missing = sorted(c for c in cols if f"`{c}`" not in doc)
    t("every-column-has-provenance", not missing, missing)

    # ---- 九條限制逐條出現在 docstring（釘 ④ 的回歸形）------------------------
    for mark in "①②③④⑤⑥⑦⑧⑨":
        t(f"limit-{mark}-documented", mark in doc)
    t("limits-header", "九條限制" in doc)
    t("ge-not-gt-documented", "stpEndTime >= stpStartTime" in doc)

    # ---- `normalize_cls` -----------------------------------------------------
    t("cls-ok", normalize_cls("segDiePlacement") == ("segDiePlacement", None))
    t("cls-trim", normalize_cls("  segDiePlacement ")[0] == "segDiePlacement")
    t("cls-missing", normalize_cls("")[1]["error"] == "missing_cls")
    t("cls-missing-none", normalize_cls(None)[1]["error"] == "missing_cls")
    for bad in ("seg DiePlacement", "seg%", "1segX", "seg-x", "seg'x", "x" * 80):
        t(f"cls-bad-{bad[:6]}", normalize_cls(bad)[1]["error"] == "bad_cls")
    t("cls-reserved", normalize_cls(MES_RESERVED_CLASS)[1]["error"] == "excluded_class")
    t("cls-err-shape", all({"error", "hint"} <= set(normalize_cls(x)[1])
                           for x in ("", "seg%", MES_RESERVED_CLASS)))

    # ---- `derive_station_vars`（合成列；四群 × 四型 × UOM 往返）--------------
    synth = [
        (1, "varDiePlacement_DeltaX1", "X Positional Error", "fcdSegmentData", "float", "µm"),
        (2, "varDiePlacement_DeltaTheta1", "Angular Error", "fcdSegmentData", "float", "°"),
        (3, "varDiePlacement_PickFinalForce", "Final Pick Force", "fcdProcessParameter", "float", "N"),
        (4, "varDiePlacement_PlasmaScanPasses", "Plasma Scan Passes", "fcdProcessParameter",
         "integer", "none"),
        (5, "varDiePlacement_PlasmaCleanedShuttleMode", "Shuttle cleaned", "fcdProcessParameter",
         "string", "Yes/No"),
        (6, "mflDiePlacement_AttachGeneralError", "general error", "fcdMaterialFailureCode",
         "integer", "none"),
        (7, "eqfDiePlacement_EStop", "Emergency Stop condition", "dimEquipmentFailureCode",
         "string", "none"),
        (8, "varDiePlacement_ShimFiducials0", "Shim Fiducials", "fcdSegmentData",
         "uriReference", "none"),
    ]
    sv = derive_station_vars("segDiePlacement", (11, "segDiePlacement", "Die Placement"), synth,
                             elapsed_s=0.01)
    t("sv-keys", set(sv) == set(STATION_VARS_KEYS))
    t("sv-found", sv["found"] is True and sv["n_vars"] == 8)
    t("sv-desc", sv["class_description"] == "Die Placement")
    t("sv-group-order", [g["class_id"] for g in sv["groups"]] == list(PDD_CLASS_IDS))
    t("sv-group-sum", sum(g["n"] for g in sv["groups"]) == sv["n_vars"])
    t("sv-census-sum", sum(sv["uom_census"].values()) == sv["n_vars"])
    t("sv-census-subset", set(sv["uom_census"]) <= set(PDD_UOMS))
    t("sv-no-vdf-pk", "vdfPK" not in json.dumps(sv, ensure_ascii=False))
    t("sv-var-keys", set(sv["groups"][0]["variables"][0]) == {"name", "description", "type", "uom"})
    t("sv-types-closed", {v["type"] for g in sv["groups"] for v in g["variables"]} <= set(PDD_TYPES))
    t("sv-validator", ok_station_vars(sv))
    # UOM 逐位元往返（JSON 序列化→反序列化；**不釘死 codepoint**，釘不變）
    rt = json.loads(json.dumps(sv, ensure_ascii=False))
    t("sv-uom-roundtrip", rt["uom_census"] == sv["uom_census"]
      and "µm" in sv["uom_census"] and "°" in sv["uom_census"])
    t("sv-micro-sign-codepoint",
      [hex(ord(c)) for c in "µm"] == ["0xb5", "0x6d"])     # MICRO SIGN、不是希臘 μ(0x3bc)
    # 存在但零變數 vs 不存在（**兩者絕不可混**）
    sv0 = derive_station_vars("segChassisAttach",
                              (12, "segChassisAttach", None), [])
    t("sv-zero-vars", sv0["found"] is True and sv0["n_vars"] == 0 and sv0["groups"] == []
      and sv0["reason"] is None)
    t("sv-zero-desc-null", sv0["class_description"] is None)
    t("sv-zero-validator", ok_station_vars(sv0))
    svx = derive_station_vars("segNotInMes", None, [])
    t("sv-not-in-mes", svx["found"] is False and svx["reason"] == "class_not_in_mes"
      and svx["n_vars"] is None)
    t("sv-not-in-mes-keys", set(svx) == set(STATION_VARS_KEYS))
    t("sv-not-in-mes-validator", ok_station_vars(svx))
    t("sv-demo-miss-differs", svx["unavailable_reason"] is None)   # ≠ not_in_demo_fixture
    # 壞列丟棄不炸
    svb = derive_station_vars("segDiePlacement", (11, "segDiePlacement", "Die Placement"),
                              synth + [(9, None, None, "fcdSegmentData", "float", "mm"), (10,)])
    t("sv-bad-rows-dropped", svb["n_vars"] == 8)
    # 未知 enum 值原樣轉述（不丟、不猜）
    svu = derive_station_vars("segX", (1, "segX", None),
                              [(1, "varX_A", "a", "fcdBrandNew", "double", "furlong")])
    t("sv-unknown-enum-kept", svu["n_vars"] == 1
      and svu["groups"][0]["class_id"] == "fcdBrandNew"
      and svu["groups"][0]["variables"][0]["type"] == "double")
    t("sv-validator-rejects-unknown-type", not ok_station_vars(svu))
    t("sv-cap-hit", derive_station_vars("segX", (1, "segX", None), synth,
                                        cap_hit=True)["diag"]["vars_cap_hit"] is True)

    # ---- guard（純函式層）----------------------------------------------------
    good = {k: True for k in VARS_GUARD_KEYS}
    t("guard-ok", assert_vars_guard(good) is True)
    for k in VARS_GUARD_KEYS:
        bad = dict(good, **{k: False})
        try:
            assert_vars_guard(bad)
            raise AssertionError("guard should have raised")
        except SchemaDivergence as e:
            t(f"guard-raises-{k}", k in str(e))

    # ---- fixture golden（契約面；零 MES）-------------------------------------
    # golden fixture 不存在（例：脫敏公開樹不帶原始撈取 golden）⇒ 該段標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    if not os.path.exists(os.path.join(BASE, "_fixtures", "board.station_vars.v1.json")):
        print("mes_hermes selftest: skipped: board.station_vars.v1.json absent")
    else:
        fx = _fixture("board.station_vars.v1.json")
        t("fx-schema", fx["schema_id"] == SCHEMA_STATION_VARS)
        t("fx-golden-n", len(fx["golden"]) >= 2)
        t("fx-golden-valid", all(ok_station_vars(g) for g in fx["golden"]))
        t("fx-invalid-n", len(fx["invalid"]) >= 3)
        t("fx-invalid-rejected", all(not ok_station_vars(iv["record"]) for iv in fx["invalid"]))
        t("fx-has-zero-var-golden", any(g["found"] and g["n_vars"] == 0 for g in fx["golden"]))
        t("fx-no-reserved", MES_RESERVED_CLASS not in json.dumps(
            [g for g in fx["golden"]], ensure_ascii=False))
        _big = next(g for g in fx["golden"] if (g.get("n_vars") or 0) > 0)
        t("fx-uom-roundtrip",
          json.loads(json.dumps(_big, ensure_ascii=False))["uom_census"] == _big["uom_census"])
        t("fx-uom-closed", set(_big["uom_census"]) <= set(PDD_UOMS))
        t("fx-class-closed", {g["class_id"] for g in _big["groups"]} <= set(PDD_CLASS_IDS))

    # ---- `normalize_wo_id`／`wo_base`／`status_code` --------------------------
    t("wo-ok", normalize_wo_id("WO910001/1") == ("WO910001/1", None))
    t("wo-ok-sap", normalize_wo_id("9000100001")[0] == "9000100001")
    t("wo-trim", normalize_wo_id("  WO910001/1 ")[0] == "WO910001/1")
    t("wo-missing", normalize_wo_id("")[1]["error"] == "missing_wo_id")
    t("wo-missing-none", normalize_wo_id(None)[1]["error"] == "missing_wo_id")
    for bad in ("WO910001/1%", "WO_910001", "WO910001 /1", "WO910001/1'", "WO[910001]",
                "WO910001/1234", "x" * 41):
        t(f"wo-bad-{bad[:8]}", normalize_wo_id(bad)[1]["error"] == "bad_wo_id")
    t("wo-err-shape", all({"error", "hint"} <= set(normalize_wo_id(x)[1])
                          for x in ("", "Board%")))
    # ★ 探針第二輪踩過的坑：`@wo` 已含 `/1` 時 LIKE 必須先去後綴，否則永遠配不到 `/2`
    t("wo-base-strips-rev", wo_base("WO910001/1") == "WO910001"
      and wo_base("WO910001") == "WO910001")
    t("status-code-known", status_code(12) == ("WIP", None) and status_code(3)[0] == "Consumed")
    t("status-code-outside", status_code(99)[0] is None and "13 known" in status_code(99)[1])
    t("status-code-nonint", status_code(None)[0] is None and status_code(True)[0] is None)
    t("status-13-values", len(MATERIAL_STATUS) == 13 and MATERIAL_STATUS[10] == "Scrapped")

    # ---- `derive_wo_serials`：活單形（合成列；九條限制逐條）-------------------
    now = datetime(2026, 8, 11, 12, 0, 0)
    head_live = [(91002, "WO910001/1", "9000100001", "Heron (batch A)", "assyUnit",
                  datetime(2026, 8, 11, 9, 20, 17), datetime(2026, 8, 11, 11, 15, 21), 60.0)]
    ser_live = [
        (1, "J0SYN03", "assyUnit", "mdfX", 12, 91002, 1,
         datetime(2026, 8, 11, 9, 14, 3), datetime(2026, 8, 11, 9, 14, 3), 12),
        (2, "J0SYN04", "assyUnit", "mdfX", 12, 91002, 1,
         datetime(2026, 8, 11, 10, 12, 9), datetime(2026, 8, 11, 10, 12, 9), 12),
        (3, "J0SYN05", "assyUnit", "mdfX", 99, 91002, 1,
         datetime(2026, 8, 11, 11, 8, 2), datetime(2026, 8, 11, 11, 8, 2), 12),
    ]
    seg_live = [
        (100, 1, "segCleanTest", datetime(2026, 8, 11, 10, 0),
         datetime(2026, 8, 11, 10, 5), "PASS", "WO910001/1"),
        (101, 1, "segCalibrationTest", datetime(2026, 8, 11, 11, 21, 4),
         datetime(2026, 8, 11, 11, 25, 40), "PASS", "WO910001/1"),
        # 零工期段（秤重站；start==end 帶 4 位小數）⇒ 工期 null ＋ not_measurable
        (102, 2, "segDryUnitWeightMeasurement",
         datetime(2026, 8, 11, 10, 41, 7, 125000), datetime(2026, 8, 11, 10, 41, 7, 125000),
         "PASS", "WO910001/1"),
        # 最後一段掛在**別張工單**下（扇出自揭欄）
        (103, 3, "segFinalAssembly", datetime(2026, 8, 11, 11, 20, 6),
         datetime(2026, 8, 11, 11, 23, 9), "FAIL", "WO910009/1"),
    ]
    live = derive_wo_serials("WO910001/1", head_live, ser_live, seg_live, [],
                             fetched_at=now, n_mpr_rows=3, elapsed_s=0.42)
    t("wo-keys", set(live) == set(WO_SERIALS_KEYS))
    t("wo-found", live["found"] is True and live["serial_count"] == 3)
    t("wo-qty-parallel", live["qty_requested"] == 60 and live["serial_count"] == 3
      and "常態" in live["serial_count_note"])                       # ④ 並列
    t("wo-qty-int", isinstance(live["qty_requested"], int))          # real → 整數化
    t("wo-revisions", live["work_order"]["revisions"] == ["WO910001/1"])
    t("wo-completed-note", live["work_order"]["completed_at"] is not None
      and "還在跑" in live["work_order"]["completed_at_note"])        # ② 完工日不是活單閘
    t("wo-serial-keys", all(set(s) == set(SERIAL_KEYS) for s in live["serials"]))
    _by_lot = {s["lot"]: s for s in live["serials"]}
    t("wo-last-seg-by-pk", _by_lot["J0SYN03"]["last_station"] == "segCalibrationTest")
    t("wo-duration", _by_lot["J0SYN03"]["last_segment_duration_s"] == 276.0
      and _by_lot["J0SYN03"]["duration_not_measurable"] is False)
    t("wo-nseg-lower-bound", _by_lot["J0SYN03"]["n_seg"] == 2)        # ⑦ 下界、不是站序
    t("★ wo-zero-length-null", _by_lot["J0SYN04"]["last_segment_duration_s"] is None
      and _by_lot["J0SYN04"]["duration_not_measurable"] is True)      # ⑧ **絕不填 0**
    t("wo-zero-length-ts-keeps-fraction",
      _by_lot["J0SYN04"]["last_segment_start"].endswith(".1250"))
    t("wo-status-enum", _by_lot["J0SYN03"]["status_code"] == "WIP")   # ⑤
    t("★ wo-status-outside-enum", _by_lot["J0SYN05"]["status_code"] is None
      and _by_lot["J0SYN05"]["status_code_note"] is not None)         # ⑤ 值域外不猜
    t("wo-outgoing-number-only", _by_lot["J0SYN05"]["outgoing_status_id"] == 12)
    t("wo-result-verbatim", _by_lot["J0SYN05"]["last_result"] == "FAIL")   # ⑥
    t("★ wo-fanout-self-disclose", _by_lot["J0SYN05"]["last_segment_wo"] == "WO910009/1"
      and _by_lot["J0SYN05"]["last_wo_is_this_wo"] is False)          # ③
    t("wo-rework-instances", live["n_rework_instances"] == 0 and live["n_serial_lot_dst"] == 3)
    t("wo-assignment-usable", live["diag"]["assignment_time_usable"] is True
      and live["diag"]["assignment_time_reason"] == "latest_segment_recent")   # ②
    t("wo-assigned-at-printed", all(s["assigned_at"] for s in live["serials"]))
    t("wo-diag-nine", all(k in live["diag"] for k in
                          ("since_used", "serial_cap_hit", "segment_cap_hit",
                           "event_cap_hit")))                          # ⑨
    t("wo-since-default", live["diag"]["since_used"] == SINCE_DEFAULT)
    t("wo-caveats-nine", len(live["caveats"]) == 9)
    t("wo-dist-sum", sum(b["n_serial"] for b in live["last_station_distribution"]) == 3)
    t("wo-excluded-flag", live["diag"]["excluded_mes_reserved"] is True)
    t("wo-validator", ok_wo_serials(live))
    _blob = json.dumps(live, ensure_ascii=False, default=str)
    for bad in FORBIDDEN_OPEN_KEYS:                                    # ① 未結段語意零出現
        t(f"wo-no-{bad}", f'"{bad}"' not in _blob)
    for bad in ("rqsCurrentEquipment", "rqsSegmentState"):
        t(f"wo-no-{bad}", bad not in _blob)

    # ---- 舊單形：批次蓋章 ＋ 排除 segMESReserved 後無製程段 -------------------
    completed_old = datetime(2019, 4, 9, 18, 12, 30)
    head_old = [(91001, "WO910003/5", "9000100003", "9000100002 to 9000100003 Heron",
                 "assyPackagedUnit", datetime(2019, 3, 12, 8, 41, 5),
                 completed_old, 260.0)]
    ser_old = [(10 + i, f"B0000{i}K", "assyPackagedUnit", "mdfY", 10, 91001, 1,
                completed_old, completed_old, 1) for i in range(4)]
    seg_old = [(200, 10, "segDieArrayTaping", datetime(2019, 3, 13, 15, 27, 41, 50000),
                datetime(2019, 3, 13, 15, 27, 41, 50000), "FAIL", "WO910003/5")]
    evt_old = [(11, datetime(2020, 3, 12)), (12, datetime(2020, 3, 13)),
               (13, datetime(2020, 3, 12))]
    old = derive_wo_serials("WO910003/5", head_old, ser_old, seg_old, evt_old,
                            fetched_at=now, n_mpr_rows=4, elapsed_s=1.1)
    t("wo-old-keys", set(old) == set(WO_SERIALS_KEYS))
    t("★ wo-old-batch-stamp", old["diag"]["assignment_time_usable"] is False
      and old["diag"]["assignment_time_reason"] == "batch_stamp")      # ②
    t("★ wo-old-assigned-at-null", all(s["assigned_at"] is None for s in old["serials"]))
    _old_no_seg = [s for s in old["serials"] if s["no_process_segment"]]
    t("★ wo-old-no-process-segment", len(_old_no_seg) == 3
      and all(s["last_station"] is None and s["last_event_at"] for s in _old_no_seg))
    t("★ wo-old-event-bucket",
      any(b["station"] == NO_SEGMENT_BUCKET and b["n_serial"] == 3
          for b in old["last_station_distribution"]))
    t("wo-old-fail-verbatim",
      [s["last_result"] for s in old["serials"] if not s["no_process_segment"]] == ["FAIL"])
    t("wo-old-validator", ok_wo_serials(old))
    # 無段也無事件 ⇒ 第三個桶（已投料、未開工）
    none_yet = derive_wo_serials("WO900000/1", head_old, ser_old[:1], [], [], fetched_at=now)
    t("wo-no-activity-bucket",
      none_yet["last_station_distribution"][0]["station"] == NO_ACTIVITY_BUCKET)
    t("wo-no-segment-reason", none_yet["diag"]["assignment_time_reason"] == "no_segment")
    # 舊單但非批次蓋章 ⇒ `no_recent_segment`（⚠ 本單新增的第四個值，見報告）
    ser_stale = [(20, "J0SYN15", "assyPackagedUnit", "mdfY", 10, 91001, 1,
                  datetime(2019, 3, 12, 9, 30), datetime(2019, 3, 12, 9, 30), 1)]
    stale = derive_wo_serials("WO910003/5", head_old, ser_stale,
                              [(300, 20, "segDieArrayTaping", datetime(2019, 3, 13, 10, 0),
                                datetime(2019, 3, 13, 10, 5), "PASS", "WO910003/5")],
                              [], fetched_at=now)
    t("wo-stale-reason", stale["diag"]["assignment_time_reason"] == "no_recent_segment"
      and stale["diag"]["assignment_time_usable"] is False
      and stale["serials"][0]["assigned_at"] is None)
    t("wo-reasons-closed", all(x["diag"]["assignment_time_reason"] in ASSIGNMENT_REASONS
                               for x in (live, old, none_yet, stale)))
    # 查不到
    miss = derive_wo_serials("WO999999/9", [], [], [], [], elapsed_s=0.1)
    t("wo-not-found", miss["found"] is False
      and miss["reason"] == "no_production_request_matched"
      and miss["unavailable_reason"] is None)
    t("wo-not-found-keys", set(miss) == set(WO_SERIALS_KEYS))
    t("wo-not-found-validator", ok_wo_serials(miss))
    # 改版收斂：同 base 的 /1 /2 全收，`revisions` 兩筆
    head_rev = head_live + [(91003, "WO910001/2", "9000100001", "Heron (batch A) rev2",
                             "assyUnit", None, None, 60.0)]
    rev = derive_wo_serials("WO910001/1", head_rev, ser_live, seg_live, [], fetched_at=now)
    t("wo-revisions-both", rev["work_order"]["revisions"] == ["WO910001/1", "WO910001/2"]
      and rev["work_order"]["rqs_id"] == "WO910001/1" and rev["diag"]["n_rqs"] == 2)
    # `rqsDateStarted` 可 NULL 而完工日已填（§7.6.7 新事實①）⇒ 「未記錄」
    head_null = [(1, "WO910010/1", None, "conPaper", "conPaper", None,
                  datetime(2026, 8, 10, 8, 0), 1.0)]
    nul = derive_wo_serials("WO910010/1", head_null, ser_live[:1], [], [], fetched_at=now)
    t("wo-started-not-recorded", nul["work_order"]["started_at"] is None
      and nul["work_order"]["started_at_note"] == _NOT_RECORDED)
    t("wo-started-validator", ok_wo_serials(nul))
    # validator 的反面：改壞就要紅（壞對地方）
    bad = json.loads(json.dumps(live, ensure_ascii=False, default=str))
    bad["serials"][1]["last_segment_duration_s"] = 0
    bad["serials"][1]["duration_not_measurable"] = False
    t("wo-validator-rejects-zero-duration", not ok_wo_serials(bad))
    bad2 = json.loads(json.dumps(live, ensure_ascii=False, default=str))
    bad2["serials"][0]["last_open"] = 0
    t("wo-validator-rejects-last-open", not ok_wo_serials(bad2))
    bad3 = json.loads(json.dumps(live, ensure_ascii=False, default=str))
    bad3["serials"][2]["status_code"] = "WIP"          # 值域外卻猜了一個名字
    t("wo-validator-rejects-guessed-status", not ok_wo_serials(bad3))

    # ---- W0 guard（純函式層）------------------------------------------------
    wg = {k: True for k in WO_GUARD_KEYS}
    t("wo-guard-ok", assert_wo_guard(wg) is True)
    for k in ("view_rqs", "stp_matPK", "SegmentProductionRequestCode"):
        try:
            assert_wo_guard(dict(wg, **{k: False}))
            raise AssertionError("guard should have raised")
        except SchemaDivergence as e:
            t(f"wo-guard-raises-{k}", k in str(e))

    # ---- fixture golden（契約面；零 MES）-------------------------------------
    if not os.path.exists(os.path.join(BASE, "_fixtures", "board.wo_serials.v1.json")):
        print("mes_hermes selftest: skipped: board.wo_serials.v1.json absent")
        print(f"mes_hermes selftest: {n} assertions PASS")
        return 0
    fw = _fixture("board.wo_serials.v1.json")
    t("fw-schema", fw["schema_id"] == SCHEMA_WO_SERIALS)
    t("fw-golden-n", len(fw["golden"]) >= 2)
    t("fw-golden-valid", all(ok_wo_serials(g) for g in fw["golden"]))
    t("fw-invalid-n", len(fw["invalid"]) >= 4)
    t("fw-invalid-rejected", all(not ok_wo_serials(iv["record"]) for iv in fw["invalid"]))
    _fwb = json.dumps(fw["golden"], ensure_ascii=False)
    for bad in FORBIDDEN_OPEN_KEYS:
        t(f"fw-no-{bad}", f'"{bad}"' not in _fwb)
    t("fw-has-batch-stamp-golden",
      any(g["diag"]["assignment_time_reason"] == "batch_stamp" for g in fw["golden"]))
    t("fw-has-no-process-segment",
      any(s["no_process_segment"] for g in fw["golden"] for s in g["serials"]))
    t("fw-has-zero-length",
      any(s["duration_not_measurable"] and s["last_segment_start"] == s["last_segment_end"]
          for g in fw["golden"] for s in g["serials"]))

    print(f"mes_hermes selftest: {n} assertions PASS")
    return 0


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    if "--sql" in argv:
        # ⚠ **只印、不執行**（開發期 Claude 不跑任何連 MES_DB 的程式）。
        for s in ALL_SQL():
            print(s + "\n;\n")
        return 0
    print(__doc__.strip().splitlines()[-3].strip() if __doc__ else "")
    print("usage: python mes_hermes.py --selftest | --sql")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
