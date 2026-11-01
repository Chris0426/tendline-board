#!/usr/bin/env python3
"""Tendline Board — Hermes 的**材料／批號／出貨**記錄級 MES 查詢核心（助理 MES 查詢第三批·包①）。

**這是什麼**：四支唯讀工具的資料腿（`board_mcp` 消費；runtime **沒有 LLM 參與**——LLM 只叫
工具，工具跑**固定審過的參數化 SQL**，`SPEC-HERMES-TRUST.md` §7.6.1）：
  · `serial_genealogy(code)`  ——一顆序號的生命週期＋**用了哪些生產材料批號**
                                （`board.serial_genealogy.v1`；探針 B0／B1／B6／A6）。
  · `wo_materials(wo_id)`     ——一張 MES 生產工單用了哪些材料批號（`board.wo_materials.v1`；A1／0d／A6）。
  · `lot_where_used(lot)`     ——一個材料批號用在哪些序號／工單／時段＋供應商入庫
                                （`board.lot_where_used.v1`；C0／C1／C3／C4a）。
  · `shipment_lookup(lot|wo_id|window)` ——出貨記錄（`board.shipment.v1`；F2／F2b）。

**不是什麼**：不是序號最後一站（`mes_hermes.wo_serials_status`）、不是 family 活動
（`mes_family`＝日帳 v4 站列的 `families` 欄，**名字相近、用途完全不同**，所以本包另開新檔
而不擴它）、不是良率（`mes_qlik`／`mes_yield`）。

**唯一的 SQL 來源＝`_PROBE-ASSISTANT-MES-FAMILY-20260922.sql`**（2026-09-23 11:42 Chris SSMS 三張單
實跑、70 結果集零錯誤；判讀＝`_PROBE-ASSISTANT-MES-FAMILY-RESULT-DIGEST-20260923.md`）。每條語句
**參數化後逐字沿用**該步的欄位／join／`TOP`；偏離處逐條寫在該常數上方（全部是「收窄」：
刪欄、`JOIN #temp` 改半連接去重、`SELECT … INTO #t` 改空殼＋`INSERT`），**零新增欄、零新增 join**。

═══ 對照探針的刻意偏離（**全部是收窄**；理由寫在各常數上方）═══════════════════════════════
  D1 **臨時表一律在非參數化批次建空殼、帶參數的語句只准 `INSERT INTO #x`**（`mes_hermes`
     2026-09-21 live 首跑事故：pyodbc 帶參數＝`sp_executesql`，裡面 `SELECT … INTO #t` 建的表
     隨那次呼叫消失）。空殼一律 `WHERE 1=0`、欄位與來源逐字同對應步驟＝**零新增出處**。
  D2 **`segment_equipment_v` 一段多台會複製列**（digest 異常 1：WO910001 B1 47 列／37 個
     stpPK）⇒ B1 照抄進 `#mm_bseg`，但 A6 對它改用 **`stpPK IN (SELECT … FROM #mm_bseg)` 半連接**
     （JOIN 會把材料列一起複製）；Python 端以 `stpPK` 折成一段、設備攤成清單。
  D3 **大 view 零 `GROUP BY`／`DISTINCT`**（C-HT4）⇒ 探針的 A6b／C1b（`GROUP BY` ＋
     `COUNT(DISTINCT)` 打在 `segment_material_v` 上）**不採**；批號彙總全在 Python 以 A6／C1
     明細做。代價＝明細有上限（★ Fable 2026-09-23 裁 (a)：A6／C1 由探針 `@topn` 200 拉到
     `DETAIL_CAP`＝1000，其餘查詢仍 200）⇒ 彙總**只涵蓋最新 1000 筆**，
     回傳 `truncated=true` 時批號清單是**下界**（見 caveats）。
  D4 **不取自由文字欄**：C3 的 `comment_head`（`LEFT(r.Comment,200)`）刪掉——README §D-1：
     自由文字可能內嵌人名、「工具化前要先裁能不能回」＝本包不回。
  D5 **不採 C4b**（`all_segment_data_value_v` 的 `varRM_*`）：它走 EAV view（§7.6.4 陷阱 3），
     兩張活單的四個值與 C3／C4a 逐字相符、2019 舊料 C3 空時它也空 ⇒ 零增益、只增風險。
  D6 **不採 C2／A7**（`rep.material_consumption_rv`）：digest §F-3 裁「dbo 源為主、rep 源選配」；
     三張單兩源 class 種類數一致（14／2／12）只寫進 caveat。⇒ `lot_where_used` **不給機台**
     （機台維度只在 C2；要看機台請對序號問 `serial_genealogy`，它的 B1 有設備）。
  D7 **B5（`rep.product_material`）不採**：狀態以 B0 的 `materials_t.StatusId` 為準（live 真源）；
     `rep.Module`（B6）只拿來**串下游實體**、不拿它的 `*StatusId`——★ 探針 WO910001 樣本
     B6 三個狀態欄（1／1／1）與 B0 同一 matPK 的 `StatusId`（3／3／1）**對不上**＝報表層落後
     （2019 舊單則全對得上）。型號走 B1 沒有、B5 有——本包**不回型號**（要型號問 `wo_serials_status`
     以外的既有工具；2019 舊單 `SegmentProductLineCode` 本來就 NULL，digest §B-H）。
  D8 `shipment_lookup(lot)` ＝ F2 的欄位與 view、過濾由「窗」換成 `matMaterialLotID = ?`
     （F2 的預期段自己寫「以 lot 回溯」）；`wo_id` ＝ F2b 的過濾欄直接綁參數（F2b 是 JOIN `#pr`）；
     三者皆無 ⇒ F2 原樣（窗）。

═══ 欄位出處（[[ssms-sql-column-provenance]]；README §C 表＝逐欄 inventory 行號）══════════════
  | 物件 | 本檔取的欄 | 探針步驟（結果集） |
  |---|---|---|
  | `dbo.materials_t`×`material_definition_t`×`material_class_t` | `matPK`／`matMaterialLotID`／`StatusId`／`mclMaterialClassID`／`mdfMaterialDefinitionID`／`matPK_parent`／`matConcession`／`matInitialQuantity`／`matQuantityConsumed`／`matQuantityScrapped`／`matExpiryDate` | B0／C0 |
  | `dbo.segment_equipment_v` | `stpPK`／`SegmentClassID`／`sgdProcessSegmentID`／`astEquipmentID`／`astDescription`／`stpStartTime`／`stpEndTime`／`stpTestResult`／`rqsProductionRequestID`／`matPK` | B1 |
  | `rep.Module` | `Code`／`ModulestatusId`／`UnitId`／`UnitStatusId`／`PackagedUnitId`／`PackagedUnitstatusId`／`EolId`／`EolStatusId`／`ASSYId`／`ASSYStatusId`／`FirstSegmentStartTime`／`LastSegmentStartTime`／三個 `*ProductionRequestId`／`LastMaterialId`／`LastMaterialClassId`／`LastMaterialStatusId`／`Id` | B6 |
  | `dbo.segment_material_v` | `matMaterialLotID`／`mclMaterialClassID`／`SegmentClassID`／`stpStartTime`／`ConsumedMtlMaterialLotID`／`ConsumedMlcMaterialClassID`／`ConsumedMldMaterialDefinitionID`／`ConsumedMldUOM`／`sgmQuantity`／`sgmAssemblyPosition`／`ConsumedMtlStatus`／`ConsumedMtlConcession`／`ConsumedMtlExpiryDate`／`ConsumedMtlPK_parent`／`SegmentProductionRequestCode`／`stpPK`／`ConsumedMtlPK`／`rqsProductionRequestID` | A6／C1 |
  | `dbo.production_request_v` | `rqsPK`／`rqsProductionRequestID`／`rqsWorkOrder`／`rqsDescription`／`mclMaterialClassID`／`rqsDateStarted`／`rqsDateCompleted`／`rqsQuantityRequested`／`prdDescription`／`ProductLineCode` | 0d `#pr`／A1 |
  | `dbo.material_production_request` | `MaterialId`／`ProductionRequestId` | 0d `#mpr` |
  | `dbo.process_segment_v` | `stpPK`／`matPK`／`stpStartTime` | 0d `#seg` |
  | `rep.material_received_into_stores_rv` | `MaterialId`／`TimeReceived`／`SupplierCode`／`SupplierLotCode`／`SupplierPartNumber`／`PurchaseOrderCode`／`DeliveryDocket`／`ManufacturingDate`／`WashingDate` | C3 |
  | `dbo.material_supplier_v` | `matPK`／`matMaterialLotID`／`Supplier` | C4a |
  | `dbo.material_shipment_v` | `matMaterialLotID`／`stpStartTime`／`QATime`／`rqsProductionRequestID`／`rqsWorkOrder`／`PurchaseOrder`／`CustomerName`／`CartonCode`／`PalletCode`／`PacklistCode` | F2／F2b |

  ⚠ `rep.Module.Id` 的 SELECT **不取**（探針 B6 也沒取）；模組那一階以 B0 的
    `mclMaterialClassID='assyModule'` 認，其餘四階以 B6 的四個下游 id 對 B0 的 `matPK` 認。
  **絕對不 SELECT 的欄**（`ALL_SQL()` 全文負斷言）：人名（`stpPersonID`／`OperatorId`／
  `OperatorName`／`aslPersonID`／`rqsRequestor`）、`rqsChangeLog`、任何 `Comment`、三張 EAV 大表。

★ perf 鐵則（沿用 `mes_hermes` 的一套，引用不重述）：打 `dbo.*`／`rep.*` 的每條 SELECT／INSERT
  以 `OPTION (RECOMPILE)` 收尾；碰 `process_segment_v`／`segment_equipment_v`／`segment_material_v`
  的每條都有 `stpStartTime >= ?` 窗或 `#temp` 整數鍵收斂；大 view 零 `DISTINCT`／`GROUP BY`／動態
  `IN (...)`；**刻意不濾 `stpEndTime`**（日後要濾一律 `>=`、絕不 `>`）；排序用 `stpPK`。
★ §24：零人名欄。★ 唯讀：唯一 DDL/DML 在 `#mm_*`（tempdb）。★ 凍結模組零 import。
★ 客戶名（`CustomerName`）是**商業資訊**不是廠內人員（§24 管的是人）——照回，但 caveat 提醒
  「對外展示前要脫敏」（F2 探針註解原話）。

CLI:
    python mes_material.py --selftest         # 純離線（讀 _fixtures/ 四份 golden ＋ 合成列），零 MES
    python mes_material.py --sql              # 只**印**本模組會送出的全部 SQL（不執行）
    python mes_material.py --build-fixtures   # 由三份探針 TSV 重建四份 fixture（純離線讀檔）
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

from mes_qlik import SchemaDivergence, _connect            # noqa: F401  連線單一出處
import mes_hermes as _mh                                   # 純常數／純函式重用（status enum、_ts、error_detail、wo 驗證）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # pragma: no cover
    pass

BASE = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# 1. 人工審定常數
# =============================================================================
SCHEMA_SERIAL = "board.serial_genealogy.v1"
SCHEMA_WO_MAT = "board.wo_materials.v1"
SCHEMA_LOT = "board.lot_where_used.v1"
SCHEMA_SHIP = "board.shipment.v1"

TOOLS = ("serial_genealogy", "wo_materials", "lot_where_used", "shipment_lookup")

QUERY_TIMEOUT_SEC = 180
#: 探針 `@topn`（每支取數查詢的列上限；**保留**，回傳標 `truncated`）。
TOPN = 200
#: ★ 明細上限（Fable 2026-09-23 裁 (a)）：**A6（`wo_materials`／`serial_genealogy`）與 C1
#: （`lot_where_used`）** 由 200 拉到 1000；A6b／C1b 仍不採（C-HT4 不開例外）。`truncated` 語意不變＝下界。
#: ⚠ fixture 的 `caps` 仍以探針的 `TOPN`（200）判——那份資料是在探針那一層被截的。
DETAIL_CAP = 1000
#: 探針 `#b_mlt`／`#c_mlt` 的 `TOP (50)`。
MTL_CAP = 50
#: 探針 `#ser`／`#seg` 的兩層上限（`wo_materials` 的母體）。
WSER_CAP = 20000
WSEG_CAP = 500000
#: 探針 `@wo_since`＝真全歷史（召回／履歷要全歷史）。
SINCE_DEFAULT = "2000-01-01 00:00:00"
#: `shipment_lookup` 窗模式：探針 F2 的預設 7 天；上限 31（工具面 clamp 拒絕、不猜）。
SHIP_DAYS_DEFAULT = 7
SHIP_DAYS_MAX = 31

MES_RESERVED_CLASS = _mh.MES_RESERVED_CLASS
MODULE_CLASS = "assyModule"

#: 序號／批號格式白名單（`matMaterialLotID varchar(75)`；走等號比對、不走 LIKE）。
#: 實例：`J0SYN01`／`LA-9100001-9001`／`LT910001/1`／`LG-9100001-9001`。排除空白與 `%'[;`。
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/#\-]{0,74}$")

#: `rep.Module` 的四個下游 id 欄 → 階段名（B6 欄位順序見 `SQL_SG_B6`）。
STAGES = ("module", "unit", "packaged_unit", "eol", "assy")

#: 回傳鍵集合（**查不到也長同一個形**；validator 與 smoke 以此為準）。
SERIAL_KEYS = ("serial", "found", "is_module", "entities", "downstream_unresolved",
               "last_segment", "work_orders", "n_segments", "materials", "material_lots",
               "n_material_lots", "upstream_stage_links", "truncated", "caveats",
               "reason", "unavailable_reason", "diag")
WO_MAT_KEYS = ("wo_id", "found", "work_order", "material_lots", "n_material_rows",
               "n_material_lots", "n_material_classes", "upstream_stage_links",
               "truncated", "caveats", "reason", "unavailable_reason", "diag")
LOT_KEYS = ("lot", "found", "materials", "used_in", "by_work_order", "n_products",
            "supplier", "not_measurable", "truncated", "caveats", "reason",
            "unavailable_reason", "diag")
SHIP_KEYS = ("mode", "key", "found", "shipments", "n_shipments", "truncated", "caveats",
             "reason", "unavailable_reason", "diag")
KEYS_BY_TOOL = {"serial_genealogy": SERIAL_KEYS, "wo_materials": WO_MAT_KEYS,
                "lot_where_used": LOT_KEYS, "shipment_lookup": SHIP_KEYS}

#: 「MES 裡真的沒有」的閉集（≠ `unavailable_reason`＝我們取不到）。
REASONS = ("no_material_matched", "no_production_request_matched", "no_shipment_record")
#: `not_measurable` 理由閉集（SPEC §7.6 包① 理由表）。
NOT_MEASURABLE_REASONS = ("receipt_record_not_retained",)

_TS_FMT = "%Y-%m-%d %H:%M:%S"

# =============================================================================
# 2. I/O 層：SQL 文本（全部模組常數＝離線可斷言；唯讀）
# =============================================================================
#: G0 欄位守衛：本包碰到的 12 個物件 ＋ Board 主線從沒跑過的三欄。缺 ⇒ `SchemaDivergence`（停下、
#: 不回全零板——全零板在消費面與「這顆沒用料」長得一樣＝靜默謊）。每支工具只看自己要的那幾格。
SQL_GUARD = (
    "SELECT CASE WHEN OBJECT_ID('dbo.materials_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_class_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_equipment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.segment_material_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('rep.Module') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.production_request_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_production_request') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.process_segment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('rep.material_received_into_stores_rv') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_supplier_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_shipment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.segment_material_v','ConsumedMtlPK')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('rep.Module','UnitId')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.material_shipment_v','matMaterialLotID')"
    " IS NULL THEN 0 ELSE 1 END\n"
    "OPTION (RECOMPILE)")
GUARD_KEYS = ("tbx_mlt", "tbx_mdf", "tbx_mcl", "view_seg_ast", "view_seg_mat", "rep_module",
              "view_rqs", "tbx_mpr", "view_seg", "rep_received", "view_supplier",
              "view_shipment", "ConsumedMtlPK", "UnitId", "shipment_lot")
_MTL3 = ("tbx_mlt", "tbx_mdf", "tbx_mcl")
GUARD_NEEDS = {
    "serial_genealogy": _MTL3 + ("view_seg_ast", "view_seg_mat", "rep_module",
                                 "UnitId"),
    "wo_materials": ("view_rqs", "tbx_mpr", "view_seg", "view_seg_mat"),
    "lot_where_used": _MTL3 + ("view_seg_mat", "ConsumedMtlPK", "rep_received",
                               "view_supplier"),
    "shipment_lookup": ("view_shipment", "shipment_lot"),
}

# ── serial_genealogy（B0 → B1 → B6 → A6）──────────────────────────────────────
#: 空殼批次（D1）。`#mm_b`＝B0 的 `#b_mlt` 欄位逐字；`#mm_bseg`＝B1 的欄位子集（刪 `duration_s`
#: ／`q_proc`／`q_fail`＝工具用不到的欄）。
SQL_SG_TEMPS = (
    "IF OBJECT_ID('tempdb..#mm_bseg') IS NOT NULL DROP TABLE #mm_bseg;\n"
    "IF OBJECT_ID('tempdb..#mm_b')    IS NOT NULL DROP TABLE #mm_b;\n"
    "SELECT m.matPK, lot = m.matMaterialLotID, status_id = m.StatusId,\n"
    "       mcl = mcl.mclMaterialClassID, mdf = mdf.mdfMaterialDefinitionID,\n"
    "       parent = m.matPK_parent, concession = m.matConcession\n"
    "INTO #mm_b\n"
    "FROM dbo.materials_t m\n"
    "JOIN dbo.material_definition_t mdf ON mdf.mdfPK = m.mdfPK\n"
    "JOIN dbo.material_class_t      mcl ON mcl.mclPK = mdf.mclPK\n"
    "WHERE 1=0;\n"
    "SELECT se.stpPK, station = se.SegmentClassID, seg_def = se.sgdProcessSegmentID,\n"
    "       eid = se.astEquipmentID, ast_desc = se.astDescription,\n"
    "       seg_start = se.stpStartTime, seg_end = se.stpEndTime,\n"
    "       result = se.stpTestResult, seg_wo = se.rqsProductionRequestID\n"
    "INTO #mm_bseg\n"
    "FROM dbo.segment_equipment_v se\n"
    "WHERE 1=0;")

#: B0 序號 → 物料實體（一顆序號可有多個 matPK＝各階段／重工實體）。探針 `:494-503` 逐字。
SQL_SG_B0 = (
    "INSERT INTO #mm_b (matPK, lot, status_id, mcl, mdf, parent, concession)\n"
    "SELECT TOP (?) m.matPK, lot = m.matMaterialLotID, status_id = m.StatusId,\n"
    "       mcl = mcl.mclMaterialClassID, mdf = mdf.mdfMaterialDefinitionID,\n"
    "       parent = m.matPK_parent, concession = m.matConcession\n"
    "FROM dbo.materials_t m\n"
    "JOIN dbo.material_definition_t mdf ON mdf.mdfPK = m.mdfPK\n"
    "JOIN dbo.material_class_t      mcl ON mcl.mclPK = mdf.mclPK\n"
    "WHERE m.matMaterialLotID = ?\n"
    "OPTION (RECOMPILE)")
SQL_SG_B0_READ = (
    "SELECT matPK, lot, status_id, mcl, mdf, parent, concession\n"
    "FROM #mm_b ORDER BY matPK")

#: B1 序號走過的段（`segment_equipment_v`；探針 `:517-532`，欄位子集、`TOP`/窗/排序逐字）。
#: ⚠ 一段多台會複製列（D2）——這裡照收，去重在 Python。
SQL_SG_B1 = (
    "INSERT INTO #mm_bseg (stpPK, station, seg_def, eid, ast_desc,\n"
    "                      seg_start, seg_end, result, seg_wo)\n"
    "SELECT TOP (?) se.stpPK, station = se.SegmentClassID,\n"
    "       seg_def = se.sgdProcessSegmentID,\n"
    "       eid = se.astEquipmentID, ast_desc = se.astDescription,\n"
    "       seg_start = se.stpStartTime, seg_end = se.stpEndTime,\n"
    "       result = se.stpTestResult, seg_wo = se.rqsProductionRequestID\n"
    "FROM dbo.segment_equipment_v se\n"
    "JOIN #mm_b b ON b.matPK = se.matPK\n"
    "WHERE se.stpStartTime >= ?\n"
    "ORDER BY se.stpPK\n"
    "OPTION (RECOMPILE)")
SQL_SG_B1_READ = (
    "SELECT stpPK, station, seg_def, eid, ast_desc, seg_start, seg_end, result, seg_wo\n"
    "FROM #mm_bseg ORDER BY stpPK")

#: B6 模組一列到底（`rep.Module`；探針 `:622-630` 逐字、`step` 欄除外）。
SQL_SG_B6 = (
    "SELECT TOP (?)\n"
    "       ph.Code, ph.ModulestatusId, ph.UnitId, ph.UnitStatusId,\n"
    "       ph.PackagedUnitId, ph.PackagedUnitstatusId, ph.EolId, ph.EolStatusId,\n"
    "       ph.ASSYId, ph.ASSYStatusId, ph.FirstSegmentStartTime, ph.LastSegmentStartTime,\n"
    "       ph.ModuleProductionRequestId, ph.UnitProductionRequestId,\n"
    "       ph.PackagedUnitProductionRequestId,\n"
    "       ph.LastMaterialId, ph.LastMaterialClassId, ph.LastMaterialStatusId\n"
    "FROM rep.Module ph\n"
    "JOIN #mm_b b ON b.matPK = ph.Id\n"
    "OPTION (RECOMPILE)")

#: A6 以序號為鍵（探針 A6 `:337-356` 的欄位逐字；母體由「工單的段」換成「這顆序號的段」）。
#: ⚠ D2：`JOIN #seg` 改 **`stpPK IN (SELECT … FROM #mm_bseg)`**（半連接不複製列）。
A6_COLS = (
    "       product_lot   = vm.matMaterialLotID,\n"
    "       product_class = vm.mclMaterialClassID,\n"
    "       station       = vm.SegmentClassID,\n"
    "       seg_start     = vm.stpStartTime,\n"
    "       consumed_lot  = vm.ConsumedMtlMaterialLotID,\n"
    "       consumed_class= vm.ConsumedMlcMaterialClassID,\n"
    "       consumed_def  = vm.ConsumedMldMaterialDefinitionID,\n"
    "       consumed_uom  = vm.ConsumedMldUOM,\n"
    "       qty           = vm.sgmQuantity,\n"
    "       assy_position = vm.sgmAssemblyPosition,\n"
    "       consumed_status = vm.ConsumedMtlStatus,\n"
    "       consumed_concession = vm.ConsumedMtlConcession,\n"
    "       consumed_expiry = vm.ConsumedMtlExpiryDate,\n"
    "       consumed_parent = vm.ConsumedMtlPK_parent,\n"
    "       seg_board        = vm.SegmentProductionRequestCode\n")
SQL_SG_A6 = (
    "SELECT TOP (?)\n" + A6_COLS +
    "FROM dbo.segment_material_v vm\n"
    "WHERE vm.stpPK IN (SELECT g.stpPK FROM #mm_bseg g)\n"
    "ORDER BY vm.stpPK DESC\n"
    "OPTION (RECOMPILE)")

# ── wo_materials（0d #pr → #mpr/#ser → #seg → A6）────────────────────────────
#: 空殼批次（D1）。`#mm_wpr`＝0d `#pr` 的欄位子集；`#mm_wser`＝0d `#mpr` 的 `MaterialId`
#: （以表上 `GROUP BY` 去重＝`mes_hermes` W2 同形，**表**上聚合合法）；`#mm_wseg`＝0d `#seg`
#: 的 `stpPK`／`matPK`（A6 只需 `stpPK`）。
SQL_WM_TEMPS = (
    "IF OBJECT_ID('tempdb..#mm_wseg') IS NOT NULL DROP TABLE #mm_wseg;\n"
    "IF OBJECT_ID('tempdb..#mm_wser') IS NOT NULL DROP TABLE #mm_wser;\n"
    "IF OBJECT_ID('tempdb..#mm_wpr')  IS NOT NULL DROP TABLE #mm_wpr;\n"
    "SELECT r.rqsPK, rqs_id = r.rqsProductionRequestID, sap_wo = r.rqsWorkOrder,\n"
    "       rqs_desc = r.rqsDescription, rqs_mcl = r.mclMaterialClassID,\n"
    "       started = r.rqsDateStarted, completed = r.rqsDateCompleted,\n"
    "       qty_req = r.rqsQuantityRequested, prd_desc = r.prdDescription,\n"
    "       prod_line = r.ProductLineCode\n"
    "INTO #mm_wpr\n"
    "FROM dbo.production_request_v r\n"
    "WHERE 1=0;\n"
    "SELECT matPK = mpr.MaterialId\n"
    "INTO #mm_wser\n"
    "FROM dbo.material_production_request mpr\n"
    "WHERE 1=0\n"
    "GROUP BY mpr.MaterialId;\n"
    "SELECT v.stpPK, v.matPK\n"
    "INTO #mm_wseg\n"
    "FROM dbo.process_segment_v v\n"
    "WHERE 1=0;")

#: W1 工單頭（0d `#pr` `:148-160`；`/n` 改版：`LIKE` 參數＝去後綴 base＋`/%`，同 `mes_hermes`）。
SQL_WM_HEAD = (
    "INSERT INTO #mm_wpr (rqsPK, rqs_id, sap_wo, rqs_desc, rqs_mcl, started, completed,\n"
    "                     qty_req, prd_desc, prod_line)\n"
    "SELECT r.rqsPK, rqs_id = r.rqsProductionRequestID, sap_wo = r.rqsWorkOrder,\n"
    "       rqs_desc = r.rqsDescription, rqs_mcl = r.mclMaterialClassID,\n"
    "       started = r.rqsDateStarted, completed = r.rqsDateCompleted,\n"
    "       qty_req = r.rqsQuantityRequested, prd_desc = r.prdDescription,\n"
    "       prod_line = r.ProductLineCode\n"
    "FROM dbo.production_request_v r\n"
    "WHERE r.rqsProductionRequestID = ?\n"
    "   OR r.rqsProductionRequestID LIKE ?\n"
    "   OR r.rqsWorkOrder           = ?\n"
    "OPTION (RECOMPILE)")
SQL_WM_HEAD_READ = (
    "SELECT rqsPK, rqs_id, sap_wo, rqs_desc, rqs_mcl, started, completed, qty_req,\n"
    "       prd_desc, prod_line\n"
    "FROM #mm_wpr ORDER BY rqs_id")

#: W2 名冊（0d `#mpr` `:163-168` ＋ `#ser` 的 `TOP (20000)`）。
SQL_WM_SER = (
    "INSERT INTO #mm_wser (matPK)\n"
    "SELECT TOP (?) mpr.MaterialId\n"
    "FROM dbo.material_production_request mpr\n"
    "JOIN #mm_wpr p ON p.rqsPK = mpr.ProductionRequestId\n"
    "GROUP BY mpr.MaterialId\n"
    "OPTION (RECOMPILE)")
SQL_WM_SER_INDEX = "CREATE INDEX ix_mm_wser ON #mm_wser (matPK)"

#: W3 段（0d `#seg` `:184-195`：`TOP (500000)`、`stpStartTime >= @wo_since`、刻意不濾 `stpEndTime`）。
SQL_WM_SEG = (
    "INSERT INTO #mm_wseg (stpPK, matPK)\n"
    "SELECT TOP (?) v.stpPK, v.matPK\n"
    "FROM dbo.process_segment_v v\n"
    "JOIN #mm_wser s ON s.matPK = v.matPK\n"
    "WHERE v.stpStartTime >= ?\n"
    "OPTION (RECOMPILE)")
SQL_WM_SEG_INDEX = "CREATE INDEX ix_mm_wseg ON #mm_wseg (stpPK)"
SQL_WM_COUNTS = "SELECT (SELECT COUNT(*) FROM #mm_wser), (SELECT COUNT(*) FROM #mm_wseg)"

#: A6（探針 `:337-356` 逐字：`JOIN #seg g ON g.stpPK = vm.stpPK`、`ORDER BY vm.stpPK DESC`）。
SQL_WM_A6 = (
    "SELECT TOP (?)\n" + A6_COLS +
    "FROM dbo.segment_material_v vm\n"
    "JOIN #mm_wseg g ON g.stpPK = vm.stpPK\n"
    "ORDER BY vm.stpPK DESC\n"
    "OPTION (RECOMPILE)")

# ── lot_where_used（C0 → C1 → C3 → C4a）───────────────────────────────────────
SQL_LW_TEMPS = (
    "IF OBJECT_ID('tempdb..#mm_c') IS NOT NULL DROP TABLE #mm_c;\n"
    "SELECT m.matPK, lot = m.matMaterialLotID, status_id = m.StatusId,\n"
    "       mcl = mcl.mclMaterialClassID, mdf = mdf.mdfMaterialDefinitionID,\n"
    "       parent = m.matPK_parent, init_qty = m.matInitialQuantity,\n"
    "       qty_consumed = m.matQuantityConsumed, qty_scrapped = m.matQuantityScrapped,\n"
    "       expiry = m.matExpiryDate\n"
    "INTO #mm_c\n"
    "FROM dbo.materials_t m\n"
    "JOIN dbo.material_definition_t mdf ON mdf.mdfPK = m.mdfPK\n"
    "JOIN dbo.material_class_t      mcl ON mcl.mclPK = mdf.mclPK\n"
    "WHERE 1=0;")

#: C0 批號身分（探針 `:661-672` 逐字）。
SQL_LW_C0 = (
    "INSERT INTO #mm_c (matPK, lot, status_id, mcl, mdf, parent, init_qty,\n"
    "                   qty_consumed, qty_scrapped, expiry)\n"
    "SELECT TOP (?) m.matPK, lot = m.matMaterialLotID, status_id = m.StatusId,\n"
    "       mcl = mcl.mclMaterialClassID, mdf = mdf.mdfMaterialDefinitionID,\n"
    "       parent = m.matPK_parent, init_qty = m.matInitialQuantity,\n"
    "       qty_consumed = m.matQuantityConsumed, qty_scrapped = m.matQuantityScrapped,\n"
    "       expiry = m.matExpiryDate\n"
    "FROM dbo.materials_t m\n"
    "JOIN dbo.material_definition_t mdf ON mdf.mdfPK = m.mdfPK\n"
    "JOIN dbo.material_class_t      mcl ON mcl.mclPK = mdf.mclPK\n"
    "WHERE m.matMaterialLotID = ?\n"
    "OPTION (RECOMPILE)")
SQL_LW_C0_READ = (
    "SELECT matPK, lot, status_id, mcl, mdf, parent, init_qty, qty_consumed,\n"
    "       qty_scrapped, expiry\n"
    "FROM #mm_c ORDER BY matPK")

#: C1 反向（探針 `:684-698` 逐字：整數 `ConsumedMtlPK` 過濾、全歷史窗、`stpPK DESC`）。
SQL_LW_C1 = (
    "SELECT TOP (?)\n"
    "       consumed_lot  = vm.ConsumedMtlMaterialLotID,\n"
    "       product_lot   = vm.matMaterialLotID,\n"
    "       product_class = vm.mclMaterialClassID,\n"
    "       station       = vm.SegmentClassID,\n"
    "       seg_start     = vm.stpStartTime,\n"
    "       qty           = vm.sgmQuantity,\n"
    "       assy_position = vm.sgmAssemblyPosition,\n"
    "       wo            = vm.rqsProductionRequestID,\n"
    "       seg_wo        = vm.SegmentProductionRequestCode\n"
    "FROM dbo.segment_material_v vm\n"
    "JOIN #mm_c c ON c.matPK = vm.ConsumedMtlPK\n"
    "WHERE vm.stpStartTime >= ?\n"
    "ORDER BY vm.stpPK DESC\n"
    "OPTION (RECOMPILE)")

#: C3 供應商入庫（探針 `:740-750`；D4：刪 `comment_head`）。自己或母批（KB Frame Cover 陷阱）。
SQL_LW_C3 = (
    "SELECT TOP (?)\n"
    "       asked_lot = c.lot,\n"
    "       matched_on = CASE WHEN r.MaterialId = c.matPK THEN 'self' ELSE 'parent' END,\n"
    "       r.TimeReceived, r.SupplierCode, r.SupplierLotCode, r.SupplierPartNumber,\n"
    "       r.PurchaseOrderCode, r.DeliveryDocket, r.ManufacturingDate, r.WashingDate\n"
    "FROM #mm_c c\n"
    "JOIN rep.material_received_into_stores_rv r\n"
    "  ON r.MaterialId = c.matPK OR r.MaterialId = c.parent\n"
    "ORDER BY r.TimeReceived DESC\n"
    "OPTION (RECOMPILE)")

#: C4a 供應商名（探針 `:758-762` 逐字）。
SQL_LW_C4A = (
    "SELECT TOP (?) vs.matMaterialLotID, vs.Supplier\n"
    "FROM dbo.material_supplier_v vs\n"
    "JOIN #mm_c c ON c.matPK = vs.matPK OR c.parent = vs.matPK\n"
    "OPTION (RECOMPILE)")

# ── shipment_lookup（F2／F2b）──────────────────────────────────────────────────
SHIP_COLS = (
    "       ms.matMaterialLotID, ms.stpStartTime, ms.QATime,\n"
    "       ms.rqsProductionRequestID, ms.rqsWorkOrder,\n"
    "       ms.PurchaseOrder, ms.CustomerName, ms.CartonCode, ms.PalletCode, ms.PacklistCode\n")
#: D8 主鍵＝lot（F2 欄位逐字、過濾換成 lot 等號）。
SQL_SH_LOT = (
    "SELECT TOP (?)\n" + SHIP_COLS +
    "FROM dbo.material_shipment_v ms\n"
    "WHERE ms.matMaterialLotID = ?\n"
    "ORDER BY ms.stpStartTime DESC\n"
    "OPTION (RECOMPILE)")
#: D8 次要＝wo_id（F2b 的過濾欄直接綁參數；**三張探針單 F2b 皆 0 列＝未驗證**）。
SQL_SH_WO = (
    "SELECT TOP (?)\n" + SHIP_COLS +
    "FROM dbo.material_shipment_v ms\n"
    "WHERE ms.rqsProductionRequestID = ?\n"
    "ORDER BY ms.stpStartTime DESC\n"
    "OPTION (RECOMPILE)")
#: F2 原樣（窗）。
SQL_SH_WINDOW = (
    "SELECT TOP (?)\n" + SHIP_COLS +
    "FROM dbo.material_shipment_v ms\n"
    "WHERE ms.stpStartTime >= ?\n"
    "  AND ms.stpStartTime <  ?\n"
    "ORDER BY ms.stpStartTime DESC\n"
    "OPTION (RECOMPILE)")


def ALL_SQL():
    """本模組會送出的**全部 SQL**（selftest 負斷言的唯一清單；新增查詢必須登記在這裡）。"""
    return (SQL_GUARD,
            SQL_SG_TEMPS, SQL_SG_B0, SQL_SG_B0_READ, SQL_SG_B1, SQL_SG_B1_READ,
            SQL_SG_B6, SQL_SG_A6,
            SQL_WM_TEMPS, SQL_WM_HEAD, SQL_WM_HEAD_READ, SQL_WM_SER, SQL_WM_SER_INDEX,
            SQL_WM_SEG, SQL_WM_SEG_INDEX, SQL_WM_COUNTS, SQL_WM_A6,
            SQL_LW_TEMPS, SQL_LW_C0, SQL_LW_C0_READ, SQL_LW_C1, SQL_LW_C3, SQL_LW_C4A,
            SQL_SH_LOT, SQL_SH_WO, SQL_SH_WINDOW)


#: `board_mcp._TOOL_QUERIES` 用的真名（grep 得到）。
TOOL_QUERY_NAMES = {
    "serial_genealogy": ("mes_material.SQL_SG_B0", "mes_material.SQL_SG_B1",
                         "mes_material.SQL_SG_B6", "mes_material.SQL_SG_A6"),
    "wo_materials": ("mes_material.SQL_WM_HEAD", "mes_material.SQL_WM_SER",
                     "mes_material.SQL_WM_SEG", "mes_material.SQL_WM_A6"),
    "lot_where_used": ("mes_material.SQL_LW_C0", "mes_material.SQL_LW_C1",
                       "mes_material.SQL_LW_C3", "mes_material.SQL_LW_C4A"),
    "shipment_lookup": ("mes_material.SQL_SH_LOT", "mes_material.SQL_SH_WO",
                        "mes_material.SQL_SH_WINDOW"),
}


def open_connection(timeout=QUERY_TIMEOUT_SEC):
    """唯讀連線（`mes_qlik._connect` 重用；呼叫端負責 timeout 與 `finally: close()`）。"""
    return _connect(timeout=timeout)


error_detail = _mh.error_detail          # 降級形 `diag.error_detail` 的全 repo 單一出處


# =============================================================================
# 3. 純函式層（零 I/O；selftest 主戰場）
# =============================================================================
def _s(v):
    """字串欄 → 清掉換行／tab（digest 異常 2：自由文字內嵌換行會撐壞下游）；空／NULL → None。"""
    if v is None:
        return None
    s = str(v)
    if s == "NULL":
        return None
    s = re.sub(r"[\r\n\t]+", " ", s).strip()
    return s or None


def _int(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        try:
            f = float(str(v).strip())
            return int(f) if f.is_integer() else None
        except (TypeError, ValueError):
            return None


def _num(v):
    """數量欄 → float／int（整數值回 int）；認不得回 None（不猜）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _ts(v):
    return _mh._ts(v)


def _status(sid):
    code, _note = _mh.status_code(_int(sid))
    return code


def normalize_code(code, *, kind):
    """序號／批號 → `(normalized, err)`。等號比對、不走 LIKE；白名單擋 `%'[;` 與空白。"""
    hint = ("serial looks like J0SYN01" if kind == "serial"
            else "material lot looks like LA-9100001-9001 or LT910001/1")
    if not isinstance(code, str) or not code.strip():
        return None, {"error": f"missing_{kind}", "hint": hint}
    s = code.strip()
    if not CODE_RE.match(s):
        return None, {"error": f"bad_{kind}", "given": code, "hint": hint}
    return s, None


normalize_wo_id = _mh.normalize_wo_id
wo_base = _mh.wo_base


def normalize_ship_args(lot=None, wo_id=None, days=None):
    """`shipment_lookup` 參數 → `(mode, key, err)`。lot 優先；兩者都無 ⇒ 窗模式（days 1..31）。"""
    if lot not in (None, ""):
        norm, err = normalize_code(lot, kind="lot")
        return ("lot", {"lot": norm, "wo_id": None, "days": None}, err)
    if wo_id not in (None, ""):
        norm, err = normalize_wo_id(wo_id)
        return ("wo_id", {"lot": None, "wo_id": norm, "days": None}, err)
    d = SHIP_DAYS_DEFAULT if days in (None, "") else _int(days)
    if d is None or not (1 <= d <= SHIP_DAYS_MAX):
        return ("window", None, {"error": "bad_days", "given": days,
                                 "hint": f"days must be an integer 1..{SHIP_DAYS_MAX} "
                                         "(refused, not clamped)"})
    return ("window", {"lot": None, "wo_id": None, "days": d}, None)


# ---- 骨架（鍵集合固定；查不到也長同一個形）--------------------------------------
def blank(tool, key, *, found=False, reason=None, unavailable_reason=None, diag=None):
    d = dict(diag or {})
    if tool == "serial_genealogy":
        return {"serial": key, "found": found, "is_module": None, "entities": [],
                "downstream_unresolved": [], "last_segment": None, "work_orders": [],
                "n_segments": None, "materials": [], "material_lots": [],
                "n_material_lots": None, "upstream_stage_links": [], "truncated": False,
                "caveats": [], "reason": reason, "unavailable_reason": unavailable_reason,
                "diag": d}
    if tool == "wo_materials":
        return {"wo_id": key, "found": found, "work_order": None, "material_lots": [],
                "n_material_rows": None, "n_material_lots": None, "n_material_classes": None,
                "upstream_stage_links": [], "truncated": False, "caveats": [],
                "reason": reason, "unavailable_reason": unavailable_reason, "diag": d}
    if tool == "lot_where_used":
        return {"lot": key, "found": found, "materials": [], "used_in": [],
                "by_work_order": [], "n_products": None,
                "supplier": {"received": [], "supplier_names": []}, "not_measurable": [],
                "truncated": False, "caveats": [], "reason": reason,
                "unavailable_reason": unavailable_reason, "diag": d}
    if tool == "shipment_lookup":
        k = key if isinstance(key, dict) else {"lot": None, "wo_id": None, "days": None}
        mode = ("lot" if k.get("lot") else "wo_id" if k.get("wo_id") else "window")
        return {"mode": mode, "key": dict(k), "found": found, "shipments": [],
                "n_shipments": None, "truncated": False, "caveats": [], "reason": reason,
                "unavailable_reason": unavailable_reason, "diag": d}
    raise KeyError(tool)


# ---- A6 明細 → 材料列／批號彙總（serial_genealogy 與 wo_materials 共用）-----------------
def _a6_material(r):
    """A6 一列（`A6_COLS` 順序）→ 回傳用的材料列。"""
    return {
        "consumed_lot": _s(r[4]), "material_class": _s(r[5]), "definition": _s(r[6]),
        "uom": _s(r[7]), "qty": _num(r[8]), "station": _s(r[2]), "used_at": _ts(r[3]),
        "for_product": _s(r[0]), "for_product_class": _s(r[1]),
        "consumed_status": _s(r[10]), "concession": _s(r[11]), "expiry": _ts(r[12]),
        "work_order": _s(r[14]),
    }


def _split_self_links(mats):
    """被消耗的「材料」其實是**同一顆序號的上一階實體**（例：packaged unit 消耗同碼的
    unit；2019 WO910003 A6b 的 `J0SYN01` 就是這一型）⇒ 不算生產材料批號、另列。"""
    real, links = [], {}
    for m in mats:
        if m["consumed_lot"] and m["consumed_lot"] == m["for_product"]:
            k = (m["material_class"], m["station"])
            links[k] = links.get(k, 0) + 1
        else:
            real.append(m)
    link_rows = [{"material_class": c, "station": st, "n": n}
                 for (c, st), n in sorted(links.items(), key=lambda kv: (-kv[1], str(kv[0])))]
    return real, link_rows


def rollup_lots(mats, *, count_products=True):
    """材料列 → 批號彙總（**Python 端聚合**＝D3；大 view 上零 GROUP BY）。"""
    agg = {}
    for m in mats:
        k = (m["material_class"], m["definition"], m["consumed_lot"])
        a = agg.get(k)
        if a is None:
            a = agg[k] = {"consumed_lot": m["consumed_lot"], "material_class": m["material_class"],
                          "definition": m["definition"], "uom": m["uom"], "n_uses": 0,
                          "_products": set(), "qty_total": 0, "_qty_ok": True,
                          "first_used": None, "last_used": None, "_stations": set()}
        a["n_uses"] += 1
        a["_products"].add(m["for_product"])
        if m["qty"] is None:
            a["_qty_ok"] = False
        else:
            a["qty_total"] += m["qty"]
        if m["station"]:
            a["_stations"].add(m["station"])
        t = m["used_at"]
        if t:
            a["first_used"] = t if a["first_used"] is None or t < a["first_used"] else a["first_used"]
            a["last_used"] = t if a["last_used"] is None or t > a["last_used"] else a["last_used"]
    out = []
    for a in agg.values():
        q = a["qty_total"] if a["_qty_ok"] else None
        if isinstance(q, float):
            q = round(q, 6)
        row = {"consumed_lot": a["consumed_lot"], "material_class": a["material_class"],
               "definition": a["definition"], "uom": a["uom"], "n_uses": a["n_uses"]}
        if count_products:
            row["n_products"] = len(a["_products"])
        row.update({"qty_total": q, "first_used": a["first_used"], "last_used": a["last_used"],
                    "stations": sorted(a["_stations"])})
        out.append(row)
    out.sort(key=lambda r: (str(r["material_class"]), -r["n_uses"], str(r["consumed_lot"])))
    return out


# ---- serial_genealogy ------------------------------------------------------------
def _caveats_serial(truncated):
    c = [
        "材料批號來自 MES 的「段 × 被消耗物料」記錄；**一顆序號走過的每一階（模組、單元、"
        "包裝…）各自消耗的料都在裡面**，`for_product_class` 說明是哪一階用掉的。",
        "`upstream_stage_links` ＝這顆序號自己上一階的實體被下一階「消耗」（同一個碼），"
        "那是組裝關聯、**不是生產材料**，已從批號清單裡拿掉。",
        "狀態（`status_code`）以 MES 物料主檔為準；報表層的模組生命週期表只用來串出下游實體，"
        "它的狀態欄會落後（探針實證），不引用。",
        "`last_segment` ＝最後一筆製程段（排除 MES 自身事件流 segMESReserved、依段序）；"
        "它**不是**「現在正在那一站跑」，也不是停產日。",
        "一段若掛多台設備，段只算一次、設備攤在 `machines` 清單。",
    ]
    if truncated:
        c.append("⚠ 結果被列上限截斷（`truncated=true`，明細最多 1000 列、其餘查詢 200 列）：清單只是**下界**，"
                 "不得說「就只有這些」。")
    return c


def derive_serial_genealogy(serial, mlt_rows, seg_rows, ph_rows, mat_rows, *,
                            since_used=SINCE_DEFAULT, fetched_at=None, caps=None,
                            elapsed_s=None):
    """B0／B1／B6／A6 的列 → `board.serial_genealogy.v1`。列一律照 SQL 欄位順序（fixture 同形）。"""
    caps = dict(caps or {})
    mlt = [tuple(r) for r in mlt_rows]
    diag = {"since_used": since_used,
            "fetched_at": (fetched_at.strftime(_TS_FMT) if isinstance(fetched_at, datetime)
                           else fetched_at),
            "n_entities_raw": len(mlt), "n_segment_rows_raw": len(seg_rows),
            "n_material_rows_raw": len(mat_rows), "cap_hit": caps, "elapsed_s": elapsed_s}
    if not mlt:
        out = blank("serial_genealogy", serial, found=False, reason="no_material_matched",
                    diag=diag)
        out["caveats"] = ["MES 物料主檔查無這個碼：請確認是產品序號（如 J0SYN01），"
                          "材料批號請改問 lot_where_used。"]
        return out
    ph = [tuple(r) for r in ph_rows]
    stage_by_pk = {}
    downstream_unresolved = []
    is_ph = bool(ph)
    if ph:
        p = ph[0]
        down = {"unit": _int(p[2]), "packaged_unit": _int(p[4]),
                "eol": _int(p[6]), "assy": _int(p[8])}
        pks = {_int(r[0]) for r in mlt}
        for stg, pk in down.items():
            if pk is None:
                continue
            if pk in pks:
                stage_by_pk[pk] = stg
            else:
                downstream_unresolved.append(stg)
    entities = []
    for r in mlt:
        pk, mcl = _int(r[0]), _s(r[3])
        stage = stage_by_pk.get(pk)
        if stage is None and is_ph and mcl == MODULE_CLASS:
            stage = "module"
        entities.append({"stage": stage, "material_class": mcl, "definition": _s(r[4]),
                         "status_id": _int(r[2]), "status_code": _status(r[2]),
                         "concession": _s(r[6])})
    # 段：以 stpPK 折一列（D2），設備攤成清單；排除 segMESReserved 後取 max(stpPK) 當最後一段。
    segs = {}
    for r in seg_rows:
        pk = _int(r[0])
        g = segs.get(pk)
        if g is None:
            g = segs[pk] = {"stpPK": pk, "station": _s(r[1]), "seg_def": _s(r[2]),
                            "machines": [], "started_at": _ts(r[5]), "ended_at": _ts(r[6]),
                            "result": _s(r[7]), "work_order": _s(r[8])}
        eid = _s(r[3])
        if eid and eid not in [m["eid"] for m in g["machines"]]:
            g["machines"].append({"eid": eid, "name": _s(r[4])})
    prod = [g for g in segs.values() if g["station"] != MES_RESERVED_CLASS]
    last = max(prod, key=lambda g: g["stpPK"]) if prod else None
    last_segment = ({k: v for k, v in last.items() if k != "stpPK"} if last else None)
    wos = {}
    for g in sorted(prod, key=lambda g: g["stpPK"]):
        w = g["work_order"]
        if not w:
            continue
        a = wos.setdefault(w, {"work_order": w, "first_seen": g["started_at"],
                               "last_seen": g["started_at"], "n_segments": 0})
        a["n_segments"] += 1
        a["last_seen"] = g["started_at"]
    mats_all = [_a6_material(r) for r in mat_rows]
    mats, links = _split_self_links(mats_all)
    lots = rollup_lots(mats, count_products=False)
    for m in mats:
        m.pop("for_product", None)          # 單顆序號＝恆為自己，不回冗餘欄
    truncated = any(bool(v) for v in caps.values())
    diag["n_segments_dedup"] = len(segs)
    diag["n_dup_equipment_rows"] = len(seg_rows) - len(segs)
    return {"serial": serial, "found": True, "is_module": is_ph, "entities": entities,
            "downstream_unresolved": downstream_unresolved, "last_segment": last_segment,
            "work_orders": list(wos.values()), "n_segments": len(prod),
            "materials": mats, "material_lots": lots, "n_material_lots": len(lots),
            "upstream_stage_links": links, "truncated": truncated,
            "caveats": _caveats_serial(truncated), "reason": None,
            "unavailable_reason": None, "diag": diag}


# ---- wo_materials ----------------------------------------------------------------
def _caveats_wo(truncated, product_line_null):
    c = [
        "批號清單來自 MES「這張工單的每一段 × 該段消耗的物料」（主源）；報表層第二源不另查——"
        "探針三張單兩源的料種數逐一相符（14／2／12）。",
        "`n_uses` 是消耗**記錄筆數**、`n_products` 是用到它的產品序號數；數量 `qty_total` 的單位看 `uom`，"
        "不同批號之間**不要相加**。",
        "`upstream_stage_links` ＝本單把上一階的同碼實體（例：模組）組進來，屬組裝關聯、"
        "已從材料批號清單拿掉。",
    ]
    if product_line_null:
        c.append("這張單的型號欄是空的：舊單（2019 年那一代）的產品分類方式跟現在不同，"
                 "**不是資料壞掉**。")
    if truncated:
        c.append("⚠ 消耗記錄超過上限、只看得到**最新的 1000 筆** ⇒ 批號清單是**下界**，"
                 "較早投入的批號可能沒列出；要逐顆完整清單請對序號問 serial_genealogy。")
    return c


def derive_wo_materials(wo_id, head_rows, mat_rows, *, n_serials=None, n_segments=None,
                        since_used=SINCE_DEFAULT, fetched_at=None, caps=None, elapsed_s=None):
    caps = dict(caps or {})
    heads = [tuple(r) for r in head_rows]
    diag = {"since_used": since_used,
            "fetched_at": (fetched_at.strftime(_TS_FMT) if isinstance(fetched_at, datetime)
                           else fetched_at),
            "n_serials": n_serials, "n_segments": n_segments,
            "n_material_rows_raw": len(mat_rows), "cap_hit": caps, "elapsed_s": elapsed_s}
    if not heads:
        out = blank("wo_materials", wo_id, found=False,
                    reason="no_production_request_matched", diag=diag)
        return out
    exact = [h for h in heads if h[1] == wo_id]
    by_sap = [h for h in heads if h[2] == wo_id]
    h = (exact or by_sap or heads)[0]
    work_order = {"rqs_id": _s(h[1]), "sap_wo": _s(h[2]), "description": _s(h[3]),
                  "product_class": _s(h[4]), "started_at": _ts(h[5]),
                  "completed_at": _ts(h[6]), "qty_requested": _num(h[7]),
                  "product_definition": _s(h[8]), "product_line": _s(h[9]),
                  "revisions": sorted({_s(x[1]) for x in heads if _s(x[1])})}
    mats, links = _split_self_links([_a6_material(r) for r in mat_rows])
    lots = rollup_lots(mats)
    truncated = any(bool(v) for v in caps.values())
    return {"wo_id": wo_id, "found": True, "work_order": work_order, "material_lots": lots,
            "n_material_rows": len(mats), "n_material_lots": len(lots),
            "n_material_classes": len({l["material_class"] for l in lots}),
            "upstream_stage_links": links, "truncated": truncated,
            "caveats": _caveats_wo(truncated, work_order["product_line"] is None),
            "reason": None, "unavailable_reason": None, "diag": diag}


# ---- lot_where_used --------------------------------------------------------------
_NM_RECEIPT = ("MES 的入庫記錄（供應商、供應商批號、入庫時間）有保留期限；"
               "舊料（探針實證：2019 年的批號）查無＝記錄沒保留，不是這批料沒有供應商。")


def _caveats_lot(truncated):
    c = [
        "`used_in` 是逐筆消耗記錄（產品序號、站、時間、工單）；`by_work_order` 是同一份明細"
        "在 Python 端按工單×站彙總。",
        "本工具**不給機台**：機台維度在報表層第二源，本包未納入；要看哪台機請對產品序號問 "
        "serial_genealogy。",
        "供應商有兩個來源（入庫記錄、物料供應商欄）；兩者在探針兩張活單逐字一致。",
        "供應商名／供應商批號是 MES 入庫原值，不是已驗證的主檔；例如內部調撥收到的料，供應商可顯示為「Aldergrove Precision」。",
    ]
    if truncated:
        c.append("⚠ 消耗記錄超過上限、只看得到**最新的 1000 筆** ⇒ 受影響序號／工單是**下界**，"
                 "召回範圍不得據此說「就只有這些」。")
    return c


def derive_lot_where_used(lot, c0_rows, c1_rows, c3_rows, c4a_rows, *,
                          since_used=SINCE_DEFAULT, fetched_at=None, caps=None, elapsed_s=None):
    caps = dict(caps or {})
    c0 = [tuple(r) for r in c0_rows]
    diag = {"since_used": since_used,
            "fetched_at": (fetched_at.strftime(_TS_FMT) if isinstance(fetched_at, datetime)
                           else fetched_at),
            "n_used_rows_raw": len(c1_rows), "cap_hit": caps, "elapsed_s": elapsed_s}
    if not c0:
        out = blank("lot_where_used", lot, found=False, reason="no_material_matched", diag=diag)
        out["caveats"] = ["MES 物料主檔查無這個批號（請確認拼寫；產品序號請改問 serial_genealogy）。"]
        return out
    materials = [{"material_class": _s(r[3]), "definition": _s(r[4]),
                  "status_id": _int(r[2]), "status_code": _status(r[2]),
                  "has_parent_lot": _int(r[5]) is not None,
                  "initial_qty": _num(r[6]), "qty_consumed": _num(r[7]),
                  "qty_scrapped": _num(r[8]), "expiry": _ts(r[9])} for r in c0]
    used = [{"product_serial": _s(r[1]), "product_class": _s(r[2]), "station": _s(r[3]),
             "used_at": _ts(r[4]), "qty": _num(r[5]), "work_order": _s(r[7]),
             "segment_work_order": _s(r[8])} for r in c1_rows]
    agg = {}
    for u in used:
        k = (u["work_order"], u["station"])
        a = agg.setdefault(k, {"work_order": u["work_order"], "station": u["station"],
                               "n_uses": 0, "_p": set(), "qty_total": 0, "_ok": True,
                               "first_used": None, "last_used": None})
        a["n_uses"] += 1
        a["_p"].add(u["product_serial"])
        if u["qty"] is None:
            a["_ok"] = False
        else:
            a["qty_total"] += u["qty"]
        t = u["used_at"]
        if t:
            a["first_used"] = t if a["first_used"] is None or t < a["first_used"] else a["first_used"]
            a["last_used"] = t if a["last_used"] is None or t > a["last_used"] else a["last_used"]
    by_wo = []
    for a in agg.values():
        q = a["qty_total"] if a["_ok"] else None
        if isinstance(q, float):
            q = round(q, 6)
        by_wo.append({"work_order": a["work_order"], "station": a["station"],
                      "n_uses": a["n_uses"], "n_products": len(a["_p"]), "qty_total": q,
                      "first_used": a["first_used"], "last_used": a["last_used"]})
    by_wo.sort(key=lambda r: (-r["n_uses"], str(r["work_order"]), str(r["station"])))
    received = [{"matched_on": _s(r[1]), "received_at": _ts(r[2]), "supplier": _s(r[3]),
                 "supplier_lot": _s(r[4]), "supplier_part": _s(r[5]),
                 "purchase_order": _s(r[6]), "delivery_docket": _s(r[7]),
                 "manufactured_at": _ts(r[8]), "washed_at": _ts(r[9])} for r in c3_rows]
    names = sorted({_s(r[1]) for r in c4a_rows if _s(r[1])})
    nm = []
    if not received:
        nm.append({"field": "supplier.received", "reason": "receipt_record_not_retained",
                   "note": _NM_RECEIPT})
    truncated = any(bool(v) for v in caps.values())
    return {"lot": lot, "found": True, "materials": materials, "used_in": used,
            "by_work_order": by_wo, "n_products": len({u["product_serial"] for u in used}),
            "supplier": {"received": received, "supplier_names": names},
            "not_measurable": nm, "truncated": truncated, "caveats": _caveats_lot(truncated),
            "reason": None, "unavailable_reason": None, "diag": diag}


# ---- shipment_lookup -------------------------------------------------------------
def _caveats_ship(mode, truncated):
    c = ["每一列是一顆出貨的產品（單位 PH）；`shipment_segment_at` 是出貨段的時間。",
         "客戶名是商業資訊：對外展示或轉貼前要先脫敏。"]
    if mode == "wo_id":
        c.append("⚠ 用工單號直查出貨**尚未驗證**：探針三張單（含 2019 年應早已出貨的舊單）"
                 "用工單號都查到 0 筆 ⇒ 查無**不代表沒出貨**，請改用產品序號查。")
    if truncated:
        c.append("⚠ 超過 200 筆、只列最新的 200 筆 ⇒ 是**下界**。")
    return c


def derive_shipments(mode, key, rows, *, fetched_at=None, caps=None, elapsed_s=None,
                     window=None):
    caps = dict(caps or {})
    diag = {"fetched_at": (fetched_at.strftime(_TS_FMT) if isinstance(fetched_at, datetime)
                           else fetched_at),
            "window": window, "cap_hit": caps, "elapsed_s": elapsed_s}
    ships = [{"serial": _s(r[0]), "shipment_segment_at": _ts(r[1]), "qa_at": _ts(r[2]),
              "work_order": _s(r[3]), "sap_wo": _s(r[4]), "purchase_order": _s(r[5]),
              "customer": _s(r[6]), "carton": _s(r[7]), "pallet": _s(r[8]),
              "packlist": _s(r[9])} for r in rows]
    truncated = any(bool(v) for v in caps.values())
    out = {"mode": mode, "key": dict(key), "found": bool(ships), "shipments": ships,
           "n_shipments": len(ships), "truncated": truncated,
           "caveats": _caveats_ship(mode, truncated),
           "reason": (None if ships else "no_shipment_record"),
           "unavailable_reason": None, "diag": diag}
    return out


# ---- validator（形狀檢查單一出處；smoke／selftest 委派它）--------------------------
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
        blob = json.dumps(rec, ensure_ascii=False, default=str)
        if "stpPersonID" in blob or "OperatorName" in blob or "matPK" in blob:
            return False
        if "\n" in "".join(_walk_strings(body)):
            return False
        if tool == "lot_where_used":
            for nm in rec["not_measurable"]:
                if nm["reason"] not in NOT_MEASURABLE_REASONS:
                    return False
        return True
    except Exception:                                   # noqa: BLE001
        return False


def _walk_strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            if k == "caveats" or k == "diag":
                continue
            yield from _walk_strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _walk_strings(v)


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


def compute_serial_genealogy(cur, serial, since=SINCE_DEFAULT, topn=TOPN):
    t0 = time.perf_counter()
    fetch_guard(cur, "serial_genealogy")
    cur.execute(SQL_SG_TEMPS)
    cur.execute(SQL_SG_B0, int(MTL_CAP), serial)
    cur.execute(SQL_SG_B0_READ)
    mlt = _rows(cur)
    seg = ph = mats = []
    if mlt:
        cur.execute(SQL_SG_B1, int(topn), since)
        cur.execute(SQL_SG_B1_READ)
        seg = _rows(cur)
        cur.execute(SQL_SG_B6, int(topn))
        ph = _rows(cur)
        if seg:
            cur.execute(SQL_SG_A6, int(DETAIL_CAP))
            mats = _rows(cur)
    caps = {"entities": len(mlt) >= MTL_CAP, "segments": len(seg) >= int(topn),
            "materials": len(mats) >= int(DETAIL_CAP)}
    return derive_serial_genealogy(serial, mlt, seg, ph, mats, since_used=since,
                                   fetched_at=datetime.now(), caps=caps,
                                   elapsed_s=round(time.perf_counter() - t0, 3))


def compute_wo_materials(cur, wo_id, since=SINCE_DEFAULT, topn=TOPN):
    t0 = time.perf_counter()
    fetch_guard(cur, "wo_materials")
    cur.execute(SQL_WM_TEMPS)
    cur.execute(SQL_WM_HEAD, wo_id, wo_base(wo_id) + "/%", wo_id)
    cur.execute(SQL_WM_HEAD_READ)
    heads = _rows(cur)
    mats, n_ser, n_seg = [], None, None
    if heads:
        cur.execute(SQL_WM_SER, int(WSER_CAP))
        cur.execute(SQL_WM_SER_INDEX)
        cur.execute(SQL_WM_SEG, int(WSEG_CAP), since)
        cur.execute(SQL_WM_SEG_INDEX)
        cur.execute(SQL_WM_COUNTS)
        c = cur.fetchone()
        n_ser, n_seg = int(c[0]), int(c[1])
        if n_seg:
            cur.execute(SQL_WM_A6, int(DETAIL_CAP))
            mats = _rows(cur)
    caps = {"serials": (n_ser or 0) >= WSER_CAP, "segments": (n_seg or 0) >= WSEG_CAP,
            "materials": len(mats) >= int(DETAIL_CAP)}
    return derive_wo_materials(wo_id, heads, mats, n_serials=n_ser, n_segments=n_seg,
                               since_used=since, fetched_at=datetime.now(), caps=caps,
                               elapsed_s=round(time.perf_counter() - t0, 3))


def compute_lot_where_used(cur, lot, since=SINCE_DEFAULT, topn=TOPN):
    t0 = time.perf_counter()
    fetch_guard(cur, "lot_where_used")
    cur.execute(SQL_LW_TEMPS)
    cur.execute(SQL_LW_C0, int(MTL_CAP), lot)
    cur.execute(SQL_LW_C0_READ)
    c0 = _rows(cur)
    c1 = c3 = c4 = []
    if c0:
        cur.execute(SQL_LW_C1, int(DETAIL_CAP), since)
        c1 = _rows(cur)
        cur.execute(SQL_LW_C3, int(topn))
        c3 = _rows(cur)
        cur.execute(SQL_LW_C4A, int(topn))
        c4 = _rows(cur)
    caps = {"materials": len(c0) >= MTL_CAP, "used_in": len(c1) >= int(DETAIL_CAP)}
    return derive_lot_where_used(lot, c0, c1, c3, c4, since_used=since,
                                 fetched_at=datetime.now(), caps=caps,
                                 elapsed_s=round(time.perf_counter() - t0, 3))


def compute_shipments(cur, mode, key, topn=TOPN):
    t0 = time.perf_counter()
    fetch_guard(cur, "shipment_lookup")
    now = datetime.now()
    window = None
    if mode == "lot":
        cur.execute(SQL_SH_LOT, int(topn), key["lot"])
    elif mode == "wo_id":
        cur.execute(SQL_SH_WO, int(topn), key["wo_id"])
    else:
        frm = (now - timedelta(days=int(key["days"]))).strftime(_TS_FMT)
        to = now.strftime(_TS_FMT)             # ★ 上界永不超過現在（CONCEPT §21.1 髒日期）
        window = {"from": frm, "to": to}
        cur.execute(SQL_SH_WINDOW, int(topn), frm, to)
    rows = _rows(cur)
    return derive_shipments(mode, key, rows, fetched_at=now,
                            caps={"shipments": len(rows) >= int(topn)},
                            elapsed_s=round(time.perf_counter() - t0, 3), window=window)


def compute(tool, cur, key):
    """`board_data.get_mes_material` 的 live 分派點（`key` 已經過 normalize）。"""
    if tool == "serial_genealogy":
        return compute_serial_genealogy(cur, key)
    if tool == "wo_materials":
        return compute_wo_materials(cur, key)
    if tool == "lot_where_used":
        return compute_lot_where_used(cur, key)
    if tool == "shipment_lookup":
        mode = "lot" if key.get("lot") else "wo_id" if key.get("wo_id") else "window"
        return compute_shipments(cur, mode, key)
    raise KeyError(tool)


# =============================================================================
# 5. demo：fixture（`_fixtures/mes_material_<tool>.json`）→ 同一支 derive_*
# =============================================================================
FIXTURE_FILES = {t: f"mes_material_{t}.json" for t in TOOLS}


def demo_lookup(tool, doc, key):
    """fixture 裡找樣本 → 跑**同一支** `derive_*`（demo 與 live 走同一份純函式＝形狀單一出處）。
    查不到回 `None`（呼叫端回 `not_in_demo_fixture`，**不是** `no_material_matched`）。"""
    gs = (doc or {}).get("golden") or []
    if tool == "serial_genealogy":
        for g in gs:
            if g["key"].upper() == key.upper():
                s = g["steps"]
                return derive_serial_genealogy(key, s["B0"]["rows"], s["B1"]["rows"],
                                               s["B6"]["rows"], s["A6"]["rows"],
                                               fetched_at=g["captured_at"], caps=g["caps"])
    elif tool == "wo_materials":
        for g in gs:
            h = g["steps"]["A1"]["rows"]
            if g["key"] == key or any(r[2] == key for r in h):
                s = g["steps"]
                return derive_wo_materials(key, h, s["A6"]["rows"],
                                           n_serials=g["counts"]["n_serials"],
                                           n_segments=g["counts"]["n_segments"],
                                           fetched_at=g["captured_at"], caps=g["caps"])
    elif tool == "lot_where_used":
        for g in gs:
            if g["key"].upper() == key.upper():
                s = g["steps"]
                return derive_lot_where_used(key, s["C0"]["rows"], s["C1"]["rows"],
                                             s["C3"]["rows"], s["C4a"]["rows"],
                                             fetched_at=g["captured_at"], caps=g["caps"])
    elif tool == "shipment_lookup":
        mode = "lot" if key.get("lot") else "wo_id" if key.get("wo_id") else "window"
        for g in gs:
            if mode == "window" and g["mode"] == "window":
                return derive_shipments("window", key, g["steps"]["F2"]["rows"],
                                        fetched_at=g["captured_at"], caps=g["caps"],
                                        window=g.get("window"))
            if mode == "wo_id" and g["mode"] == "wo_id" and g["key"] == key["wo_id"]:
                return derive_shipments("wo_id", key, g["steps"]["F2b"]["rows"],
                                        fetched_at=g["captured_at"], caps=g["caps"])
            if mode == "lot" and g["mode"] == "window":
                rows = [r for r in g["steps"]["F2"]["rows"]
                        if (r[0] or "").upper() == key["lot"].upper()]
                if rows:
                    return derive_shipments("lot", key, rows, fetched_at=g["captured_at"],
                                            caps={"shipments": False})
    return None


# =============================================================================
# 6. fixture builder（讀三份探針 TSV；純離線、零 MES）
# =============================================================================
PROBE_TSV = {
    "WO910001/1": "_PROBE-ASSISTANT-MES-FAMILY-RESULT-WO910001.tsv",
    "WO910002/1": "_PROBE-ASSISTANT-MES-FAMILY-RESULT-WO910002.tsv",
    "WO910003/5": "_PROBE-ASSISTANT-MES-FAMILY-RESULT-WO910003.tsv",
}
CAPTURED_AT = "2026-08-13 10:00:00"
_STEP_RE = re.compile(r"^[0A-I][0-9a-z]* ")

#: fixture 欄位順序＝各 SQL 的 SELECT 順序；值＝TSV 表頭名（探針別名）。
FIX_COLS = {
    "B0": ("matPK", "lot", "status_id", "mcl", "mdf", "parent", "concession"),
    "B1": ("stpPK", "station", "seg_def", "eid", "ast_desc", "seg_start", "seg_end",
           "result", "seg_wo"),
    "B6": ("Code", "ModulestatusId", "UnitId", "UnitStatusId",
           "PackagedUnitId", "PackagedUnitstatusId", "EolId", "EolStatusId",
           "ASSYId", "ASSYStatusId", "FirstSegmentStartTime", "LastSegmentStartTime",
           "ModuleProductionRequestId", "UnitProductionRequestId",
           "PackagedUnitProductionRequestId", "LastMaterialId", "LastMaterialClassId",
           "LastMaterialStatusId"),
    "A6": ("product_lot", "product_class", "station", "seg_start", "consumed_lot",
           "consumed_class", "consumed_def", "consumed_uom", "qty", "assy_position",
           "consumed_status", "consumed_concession", "consumed_expiry", "consumed_parent",
           "seg_board"),
    "A1": ("rqsPK", "rqs_id", "sap_wo", "rqs_desc", "rqs_mcl", "started", "completed",
           "qty_req", "prd_desc", "prod_line"),
    "A6b": ("consumed_class", "consumed_def", "consumed_lot", "n_seg", "n_product", "qty_sum",
            "first_used", "last_used"),
    "C0": ("matPK", "lot", "status_id", "mcl", "mdf", "parent", "init_qty", "qty_consumed",
           "qty_scrapped", "expiry"),
    "C1": ("consumed_lot", "product_lot", "product_class", "station", "seg_start", "qty",
           "assy_position", "wo", "seg_wo"),
    "C3": ("asked_lot", "matched_on", "TimeReceived", "SupplierCode", "SupplierLotCode",
           "SupplierPartNumber", "PurchaseOrderCode", "DeliveryDocket", "ManufacturingDate",
           "WashingDate"),
    "C4a": ("matMaterialLotID", "Supplier"),
    "F2": ("matMaterialLotID", "stpStartTime", "QATime", "rqsProductionRequestID",
           "rqsWorkOrder", "PurchaseOrder", "CustomerName", "CartonCode", "PalletCode",
           "PacklistCode"),
    "F2b": ("matMaterialLotID", "stpStartTime", "QATime", "rqsProductionRequestID",
            "rqsWorkOrder", "PurchaseOrder", "CustomerName", "CartonCode", "PalletCode",
            "PacklistCode"),
}
#: 零列步驟要靠表頭認（TSV 零列時沒有 `step` 值）。
_EMPTY_HDR_TAG = {"asked_lot|matched_on|TimeReceived": "C3",
                  "matMaterialLotID|Supplier": "C4a",
                  "matMaterialLotID|stpStartTime|QATime|CustomerName": "F2b",
                  "rqs_id|seg_def|seg_def_desc": "A8"}


def parse_probe_tsv(path):
    """探針 TSV（SSMS 多結果集貼成一檔）→ `{step_tag: {"header": [...], "rows": [[...]]}}`。

    ⚠ 自由文字欄的內嵌換行會把一列拆成兩行（digest 異常 2）＝續行併回上一列最後一格。"""
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
            if _STEP_RE.match(cells[0]):
                cur["rows"].append(cells)
            elif cur["rows"]:
                prev = cur["rows"][-1]
                prev[-1] = prev[-1] + "\n" + cells[0]
                prev.extend(cells[1:])
    out = {}
    for s in sets:
        if s["rows"]:
            tag = s["rows"][0][0].split(" ", 1)[0]
        else:
            hdr = "|".join(s["header"][1:])
            tag = next((v for k, v in _EMPTY_HDR_TAG.items() if hdr.startswith(k)), None)
            if tag is None:
                continue
        out.setdefault(tag, s)
    return out


#: F2b 探針只取了 F2 的 6 欄子集（`:1177-1179`）；fixture 一律照 F2 的 10 欄順序、缺的欄留 None
#: （F2b 三張單皆 0 列＝實際上沒有值要補）。
_SUBSET_OK = {"F2b"}


def _fix_rows(step_set, tag, *, where=None):
    hdr = step_set["header"]
    idx = [(hdr.index(c) if c in hdr else None) for c in FIX_COLS[tag]]
    if None in idx and tag not in _SUBSET_OK:
        raise ValueError(f"{tag}: header lacks {FIX_COLS[tag][idx.index(None)]}")
    rows = []
    for r in step_set["rows"]:
        if len(r) != len(hdr):
            raise ValueError(f"{tag}: ragged row {r[:3]}")
        vals = [None if (i is None or r[i] == "NULL") else r[i] for i in idx]
        if where is None or where(dict(zip(FIX_COLS[tag], vals))):
            rows.append(vals)
    return rows


def _step(tsv, tag, src_wo, *, where=None, note=None):
    s = tsv[tag]
    d = {"source_step": (s["rows"][0][0] if s["rows"] else f"{tag} (0 rows)"),
         "source_wo": src_wo, "columns": list(FIX_COLS[tag]),
         "rows": _fix_rows(s, tag, where=where)}
    if note:
        d["_note"] = note
    return d


def build_fixtures(base=BASE):
    tsvs = {wo: parse_probe_tsv(os.path.join(base, f)) for wo, f in PROBE_TSV.items()}
    common = {"captured_at": CAPTURED_AT,
              "_provenance": "domain-side read-only query 2026-08-13; _PROBE-ASSISTANT-MES-FAMILY-20260922.sql; "
                             "builder = mes_material.py --build-fixtures (do not hand-edit)"}
    out = {}
    # serial_genealogy：每張單 0e 自動解析的那顆序號。
    sg = []
    for wo, t in tsvs.items():
        ser = t["0e"]["rows"][0][t["0e"]["header"].index("serial_resolved")]
        steps = {"B0": _step(t, "B0", wo), "B1": _step(t, "B1", wo), "B6": _step(t, "B6", wo),
                 "A6": _step(t, "A6", wo, where=lambda d, s=ser: d["product_lot"] == s,
                             note="PARTIAL: probe A6 = latest 200 rows of this WO's segments, "
                                  "filtered to this serial; the live tool reads ALL of the "
                                  "serial's segments.")}
        sg.append({"key": ser, "source_wo": wo, **common,
                   "caps": {"entities": False, "segments": len(steps["B1"]["rows"]) >= TOPN,
                            "materials": True},
                   "steps": steps})
    out["serial_genealogy"] = {"schema_id": SCHEMA_SERIAL, "golden": sg,
                               "_note": "caps.materials=true on purpose: the A6 subset is partial."}
    wm = []
    for wo, t in tsvs.items():
        h = t["0e"]["header"]
        e = t["0e"]["rows"][0]
        a6 = _step(t, "A6", wo)
        wm.append({"key": wo, "source_wo": wo, **common,
                   "counts": {"n_serials": int(e[h.index("n_serial_mtlpk")]),
                              "n_segments": int(e[h.index("n_seg_rows")])},
                   "caps": {"serials": False, "segments": False,
                            "materials": len(a6["rows"]) >= TOPN},
                   "steps": {"A1": _step(t, "A1", wo), "A6": a6},
                   "_crosscheck_A6b": _step(t, "A6b", wo,
                                            note="probe GROUP BY rollup (NOT used by the tool; "
                                                 "C-HT4) — selftest checks tool lots ⊆ these")})
    out["wo_materials"] = {"schema_id": SCHEMA_WO_MAT, "golden": wm}
    lw = []
    for wo, t in tsvs.items():
        lot = t["0e"]["rows"][0][t["0e"]["header"].index("lot_resolved")]
        c1 = _step(t, "C1", wo)
        lw.append({"key": lot, "source_wo": wo, **common,
                   "caps": {"materials": False, "used_in": len(c1["rows"]) >= TOPN},
                   "steps": {"C0": _step(t, "C0", wo), "C1": c1, "C3": _step(t, "C3", wo),
                             "C4a": _step(t, "C4a", wo)}})
    out["lot_where_used"] = {"schema_id": SCHEMA_LOT, "golden": lw}
    t = tsvs["WO910001/1"]
    e = dict(zip(t["0e"]["header"], t["0e"]["rows"][0]))
    f2 = _step(t, "F2", "WO910001/1")
    sh = [{"mode": "window", "key": "7d", "source_wo": "WO910001/1", **common,
           "window": {"from": e["win_from"][:19], "to": e["win_to"][:19]},
           "caps": {"shipments": len(f2["rows"]) >= TOPN}, "steps": {"F2": f2}}]
    for wo, tt in tsvs.items():
        sh.append({"mode": "wo_id", "key": wo, "source_wo": wo, **common,
                   "caps": {"shipments": False}, "steps": {"F2b": _step(tt, "F2b", wo)}})
    out["shipment_lookup"] = {"schema_id": SCHEMA_SHIP, "golden": sh,
                              "_note": "lot mode in demo = filter of the 7-day F2 window sample."}
    return out


def write_fixtures(base=BASE):
    docs = build_fixtures(base)
    paths = []
    for tool, doc in docs.items():
        p = os.path.join(base, "_fixtures", FIXTURE_FILES[tool])
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_dump(doc) + "\n")
        paths.append(p)
    return paths


def _dump(o, ind=0):
    """JSON（LF、UTF-8 原字）；**純量 list 一行一列**＝fixture 逐列可 diff、檔小一半。"""
    pad, pad1 = " " * ind, " " * (ind + 1)
    if isinstance(o, dict):
        if not o:
            return "{}"
        items = [f"{pad1}{json.dumps(k, ensure_ascii=False)}: {_dump(v, ind + 1)}"
                 for k, v in o.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(o, list):
        if all(not isinstance(v, (dict, list)) for v in o):
            return json.dumps(o, ensure_ascii=False)
        return "[\n" + ",\n".join(pad1 + _dump(v, ind + 1) for v in o) + "\n" + pad + "]"
    return json.dumps(o, ensure_ascii=False)


def _fixture(tool, base=BASE):
    with open(os.path.join(base, "_fixtures", FIXTURE_FILES[tool]), encoding="utf-8") as fh:
        return json.load(fh)


# =============================================================================
# 7. --selftest（純離線）
# =============================================================================
class _FakeCursor:
    """記錄送出的 SQL 與參數；依 SQL 身分回預置列（驗編排順序與「參數化語句不建臨時表」）。"""

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

    def t(label, cond, detail=""):
        nonlocal n
        assert cond, f"selftest FAIL: {label}{(' — ' + str(detail)) if detail else ''}"
        n += 1

    # ---- ① SQL 文本紀律（`ALL_SQL()` 是唯一清單）--------------------------------------
    sqls = ALL_SQL()
    t("all-sql-26", len(sqls) == 26 and len(set(sqls)) == 26)
    for s in sqls:
        if s.lstrip().startswith(("SELECT", "INSERT")) and ("dbo." in s or "rep." in s):
            t("recompile", s.rstrip().endswith("OPTION (RECOMPILE)"), s[:50])
        if "?" in s:
            # ★ 參數化語句（sp_executesql）裡建的 #temp 會隨呼叫消失（mes_hermes 09-21 事故）
            t("param-no-select-into", not re.search(r"(?<!INSERT )INTO #", s), s[:50])
        if "WHERE 1=0" in s:
            t("temps-unparameterized", "?" not in s, s[:50])
    blob = "\n".join(sqls)
    for bad in ("stpPersonID", "OperatorId", "OperatorName", "aslPersonID", "rqsRequestor",
                "rqsChangeLog", "Comment", "segment_real_data_t", "segment_integer_data_t",
                "segment_string_data_t", "all_segment_data_value_v", "rqsCurrentEquipment",
                "rqsSegmentState", "material_consumption_rv", "rep.product_material"):
        t(f"never-{bad}", bad not in blob)
    t("no-end-filter", "stpEndTime >" not in blob and "stpEndTime <" not in blob
      and "stpEndTime IS" not in blob)
    big = ("segment_material_v", "process_segment_v", "segment_equipment_v",
           "material_shipment_v")
    for s in sqls:
        if "WHERE 1=0" in s:
            continue
        if any(v in s for v in big):
            t("big-view-no-distinct-groupby", "DISTINCT" not in s and "GROUP BY" not in s, s[:60])
            t("big-view-no-dynamic-in", not re.search(r"IN \((?!SELECT)", s), s[:60])
    t("group-by-only-on-mpr-table", [s for s in sqls if "GROUP BY" in s and "WHERE 1=0" not in s]
      == [SQL_WM_SER])
    for s in (SQL_SG_B1, SQL_WM_SEG, SQL_LW_C1):
        t("window-param", "stpStartTime >= ?" in s, s[:40])
    t("a6-semi-join-dedup", "stpPK IN (SELECT g.stpPK FROM #mm_bseg g)" in SQL_SG_A6
      and "JOIN #mm_bseg" not in SQL_SG_A6)
    t("a6-wo-join-verbatim", "JOIN #mm_wseg g ON g.stpPK = vm.stpPK" in SQL_WM_A6)
    t("c1-int-key", "c.matPK = vm.ConsumedMtlPK" in SQL_LW_C1)
    t("like-only-wo-head", [s for s in sqls if " LIKE " in s] == [SQL_WM_HEAD])
    t("ship-lot-eq", "ms.matMaterialLotID = ?" in SQL_SH_LOT)
    t("ship-wo-eq", "ms.rqsProductionRequestID = ?" in SQL_SH_WO)
    t("ship-window-halfopen", "stpStartTime >= ?" in SQL_SH_WINDOW
      and "stpStartTime <  ?" in SQL_SH_WINDOW)
    t("topn-200", TOPN == 200 and MTL_CAP == 50)
    t("★ detail-cap-1000（Fable 09-23 裁 (a)）", DETAIL_CAP == 1000)
    t("guard-keys", len(GUARD_KEYS) == SQL_GUARD.count("CASE WHEN")
      and all(set(v) <= set(GUARD_KEYS) for v in GUARD_NEEDS.values()))
    t("query-names-real", all(hasattr(sys.modules[__name__], q.split(".", 1)[1])
                              for qs in TOOL_QUERY_NAMES.values() for q in qs))

    # ---- ② 參數正規化 -----------------------------------------------------------------
    t("code-ok", normalize_code(" J0SYN01 ", kind="serial") == ("J0SYN01", None))
    t("code-lot-slash", normalize_code("LT910001/1", kind="lot")[0] == "LT910001/1")
    for bad in ("B0%", "B0 2", "x'--", "[a]", "a;b", "é"):
        t(f"code-bad-{bad}", normalize_code(bad, kind="lot")[1]["error"] == "bad_lot")
    t("code-missing", normalize_code("", kind="serial")[1]["error"] == "missing_serial")
    t("ship-mode-lot-first", normalize_ship_args(lot="V0SYN01", wo_id="WO1/1")[0] == "lot")
    t("ship-mode-wo", normalize_ship_args(wo_id="WO910004/1")[:2]
      == ("wo_id", {"lot": None, "wo_id": "WO910004/1", "days": None}))
    t("ship-window-default", normalize_ship_args()[1]["days"] == SHIP_DAYS_DEFAULT)
    for bad in (0, 32, "x", -1):
        t(f"ship-days-refused-{bad}", normalize_ship_args(days=bad)[2]["error"] == "bad_days")

    # ---- ③ 純函式：去重、換行、自連結、NULL 型號 -------------------------------------
    seg = [(10, "segA", "PR1", "EID-00001", "M1", "2026-09-01 10:00:00", "2026-09-01 10:00:00",
            "PASS", "WO1/1"),
           (10, "segA", "PR1", "EID-00002", "M2", "2026-09-01 10:00:00", "2026-09-01 10:00:00",
            "PASS", "WO1/1"),
           (11, MES_RESERVED_CLASS, "QR910002/1", None, None, "2026-09-02 00:00:00", None, None,
            "WO1/1"),
           (9, "segZ", "PR2", "EID-00003", "M3", "2026-08-31 09:00:00", None, "FAIL", "WO0/1")]
    mlt = [(1, "B1", 3, "assyModule", "PN1", None, "MPCR1\n2"),
           (2, "B1", 1, "assyUnit", "PN2", None, None)]
    ph = [("B1", 3, 2, 1, 999, 1, None, None, None, None, None, None, 1, 2, 3, 2, 12, 1)]
    mats = [("B1", "assyUnit", "segA", "2026-09-01 10:00:00", "B1", "assyModule",
             "PN1", "each", "1", None, "Consumed", None, None, None, "WO1/1"),
            ("B1", "assyUnit", "segA", "2026-09-01 10:00:00", "TA-1", "fluidBlack",
             "PN9", "L", "0.015", "", "Available", None, "2027-01-01 00:00:00", None, "WO1/1"),
            ("B1", "assyUnit", "segA", "2026-09-01 10:00:00", "TA-1", "fluidBlack",
             "PN9", "L", "0.015", "", "Available", None, "2027-01-01 00:00:00", None, "WO1/1")]
    r = derive_serial_genealogy("B1", mlt, seg, ph, mats, fetched_at="2026-09-23 12:00:00",
                                caps={"entities": False})
    t("sg-validator", ok_payload("serial_genealogy", r), r)
    t("sg-dedup-psgpk", r["n_segments"] == 2 and r["diag"]["n_dup_equipment_rows"] == 1)
    t("sg-machines-listed", r["last_segment"]["machines"] == [
        {"eid": "EID-00001", "name": "M1"}, {"eid": "EID-00002", "name": "M2"}])
    t("sg-last-excludes-reserved", r["last_segment"]["station"] == "segA")
    t("sg-stages", [e["stage"] for e in r["entities"]] == ["module", "unit"])
    t("sg-downstream-unresolved", r["downstream_unresolved"] == ["packaged_unit"])
    t("sg-newline-cleaned", r["entities"][0]["concession"] == "MPCR1 2")
    t("sg-self-link-split", r["upstream_stage_links"] == [
        {"material_class": "assyModule", "station": "segA", "n": 1}]
      and all(m["consumed_lot"] != "B1" for m in r["materials"]))
    t("sg-rollup", r["material_lots"] == [{"consumed_lot": "TA-1", "material_class": "fluidBlack",
                                           "definition": "PN9", "uom": "L", "n_uses": 2,
                                           "qty_total": 0.03, "first_used": "2026-09-01 10:00:00",
                                           "last_used": "2026-09-01 10:00:00",
                                           "stations": ["segA"]}], r["material_lots"])
    t("sg-status-from-b0", r["entities"][0]["status_code"] == "Consumed")
    t("sg-no-mtlpk-out", "matPK" not in json.dumps(r))
    t("sg-wo-order", [w["work_order"] for w in r["work_orders"]] == ["WO0/1", "WO1/1"])
    r0 = derive_serial_genealogy("B9", [], [], [], [])
    t("sg-miss", r0["found"] is False and r0["reason"] == "no_material_matched"
      and ok_payload("serial_genealogy", r0))
    rnp = derive_serial_genealogy("C1", [(5, "C1", 1, "conPaper", "PN0", None, None)], [], [], [])
    t("sg-not-module", rnp["is_module"] is False and rnp["entities"][0]["stage"] is None
      and rnp["last_segment"] is None)
    t("sg-trunc-caveat", derive_serial_genealogy("B1", mlt, seg, ph, mats,
                                                 caps={"materials": True})["truncated"] is True)

    head = [(1, "WO910003/5", "9000100003", "9000100002 to 9000100003 Heron\nB2", "assyPackagedUnit",
             "2019-03-12 08:41:05", "2019-04-09 18:12:30", "260", "asset ref2 ...", None)]
    w = derive_wo_materials("WO910003/5", head, mats, n_serials=224, n_segments=1960,
                            caps={"materials": True})
    t("wm-validator", ok_payload("wo_materials", w), w)
    t("wm-desc-newline", w["work_order"]["description"] == "9000100002 to 9000100003 Heron B2")
    t("wm-product-line-null-caveat", w["work_order"]["product_line"] is None
      and any("2019" in c for c in w["caveats"]))
    t("wm-trunc-caveat", w["truncated"] and any("下界" in c for c in w["caveats"]))
    t("wm-by-sap", derive_wo_materials("9000100003", head, [])["work_order"]["rqs_id"] == "WO910003/5")
    t("wm-miss", derive_wo_materials("WO0/1", [], [])["reason"] == "no_production_request_matched")

    c0 = [(7, "L1", 1, "conPaper", "PN0", None, "3750", "478", "0", "2039-02-14 09:05:11")]
    c1 = [("L1", "B2", "assyUnit", "segCal", "2026-09-23 11:00:00", "4", "", "WO2/1",
           "WO2/1"),
          ("L1", "B3", "assyUnit", "segCal", "2026-09-23 10:00:00", "4", "", "WO2/1",
           "WO2/1")]
    lw = derive_lot_where_used("L1", c0, c1, [], [("L1", "寬侑")])
    t("lw-validator", ok_payload("lot_where_used", lw), lw)
    t("lw-not-measurable", lw["not_measurable"][0]["reason"] == "receipt_record_not_retained")
    t("lw-by-wo", lw["by_work_order"] == [{"work_order": "WO2/1", "station": "segCal",
                                           "n_uses": 2, "n_products": 2, "qty_total": 8,
                                           "first_used": "2026-09-23 10:00:00",
                                           "last_used": "2026-09-23 11:00:00"}])
    t("lw-supplier-names", lw["supplier"]["supplier_names"] == ["寬侑"])
    t("lw-supplier-receipt-raw-caveat", any("MES 入庫原值" in c for c in lw["caveats"])
      and any("Aldergrove Precision" in c for c in lw["caveats"]))
    t("lw-miss", derive_lot_where_used("Lx", [], [], [], [])["reason"] == "no_material_matched")

    sh = derive_shipments("wo_id", {"lot": None, "wo_id": "WO1/1", "days": None}, [])
    t("sh-empty-honest", sh["found"] is False and sh["reason"] == "no_shipment_record"
      and any("尚未驗證" in c for c in sh["caveats"]) and ok_payload("shipment_lookup", sh))
    t("sh-blank-keys", all(set(blank(tl, "x")) == set(KEYS_BY_TOOL[tl]) for tl in TOOLS[:3])
      and set(blank("shipment_lookup", None)) == set(SHIP_KEYS))

    for tl in TOOLS:
        cur = _FakeCursor({}, guard=tuple(0 for _ in GUARD_KEYS))
        try:
            key = {"lot": "X", "wo_id": None, "days": None} if tl == "shipment_lookup" else "X"
            compute(tl, cur, key)
            t(f"guard-raises-{tl}", False)
        except SchemaDivergence as e:
            t(f"guard-raises-{tl}", "ABSENT" in str(e))

    # golden fixture 不存在（例：脫敏公開樹不帶原始撈取 golden）⇒ 依賴 golden 的段落標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    _fx_absent = [f for f in FIXTURE_FILES.values()
                  if not os.path.exists(os.path.join(BASE, "_fixtures", f))]
    if _fx_absent:
        print("mes_material selftest: skipped: %s absent" % ", ".join(sorted(_fx_absent)))
        print(f"mes_material selftest: {n} assertions PASS")
        return 0

    # ---- ④ fixture：四份 golden 走同一支 derive_*（demo 與 live 同形）-------------------
    fx = {tl: _fixture(tl) for tl in TOOLS}
    t("fx-schema-ids", [fx[tl]["schema_id"] for tl in TOOLS]
      == [SCHEMA_SERIAL, SCHEMA_WO_MAT, SCHEMA_LOT, SCHEMA_SHIP])
    for tl in TOOLS:
        for g in fx[tl]["golden"]:
            for st in g["steps"].values():
                t(f"fx-cols-{tl}", all(len(row) == len(st["columns"]) for row in st["rows"]))
                t(f"fx-provenance-{tl}", st["source_wo"] in PROBE_TSV and g["captured_at"])
    sgs = {g["key"]: demo_lookup("serial_genealogy", fx["serial_genealogy"], g["key"])
           for g in fx["serial_genealogy"]["golden"]}
    t("fx-sg-three", sorted(sgs) == ["J0SYN07", "J0SYN02", "J0SYN18"])
    for k, v in sgs.items():
        t(f"fx-sg-{k}-valid", v["found"] and ok_payload("serial_genealogy", v), k)
    d = sgs["J0SYN02"]
    t("fx-sg-live-3-entities", [e["stage"] for e in d["entities"]]
      == ["module", "unit", "packaged_unit"])
    # digest 異常 1 的實證：WO910001 B1 41 列／32 個 stpPK ⇒ 折疊後恰 32 段（含 MES 事件段前）。
    t("★ fx-sg-dedup-real", d["diag"]["n_segment_rows_raw"] == 41
      and d["diag"]["n_segments_dedup"] == 32 and d["diag"]["n_dup_equipment_rows"] == 9)
    t("fx-sg-j0syn02-lots", d["n_material_lots"] == 8 and d["truncated"] is True)
    old = sgs["J0SYN07"]
    t("fx-sg-2019-chain", [e["stage"] for e in old["entities"]]
      == ["module", "unit", "packaged_unit", "assy"]
      and old["downstream_unresolved"] == [])
    t("fx-sg-2019-dedup", old["diag"]["n_segment_rows_raw"] == 26
      and old["diag"]["n_dup_equipment_rows"] == 2)
    up = sgs["J0SYN18"]
    t("fx-sg-upstream-only-ph", [e["stage"] for e in up["entities"]] == ["module"]
      and up["entities"][0]["status_code"] == "WIP")
    wms = {g["key"]: (g, demo_lookup("wo_materials", fx["wo_materials"], g["key"]))
           for g in fx["wo_materials"]["golden"]}
    for k, (g, v) in wms.items():
        t(f"fx-wm-{k}-valid", v["found"] and ok_payload("wo_materials", v), k)
        a6b = {(r[0], r[1], r[2]) for r in g["_crosscheck_A6b"]["rows"]}
        mine = {(l["material_class"], l["definition"], l["consumed_lot"]) for l in v["material_lots"]}
        links = {(r[5], r[6], r[4]) for r in g["steps"]["A6"]["rows"] if r[4] == r[0]}
        if len(a6b) < TOPN:            # A6b 本身沒被截斷才拿來當全集
            t(f"★ fx-wm-{k}-lots-subset-of-probe-rollup", (mine | links) <= a6b,
              sorted((mine | links) - a6b)[:3])
        t(f"fx-wm-{k}-truncated-honest", v["truncated"] is True)
    t("fx-wm-2019-null-line", wms["WO910003/5"][1]["work_order"]["product_line"] is None)
    t("fx-wm-desc-clean", "\n" not in (wms["WO910003/5"][1]["work_order"]["description"] or ""))
    t("fx-wm-sap", demo_lookup("wo_materials", fx["wo_materials"], "9000100001")["wo_id"]
      == "9000100001")
    lws = {g["key"]: demo_lookup("lot_where_used", fx["lot_where_used"], g["key"])
           for g in fx["lot_where_used"]["golden"]}
    for k, v in lws.items():
        t(f"fx-lw-{k}-valid", v["found"] and ok_payload("lot_where_used", v), k)
    t("fx-lw-recent-supplier", lws["LA-9100001-9001"]["supplier"]["received"][0]["supplier_lot"]
      == "20260811" and lws["LA-9100001-9001"]["not_measurable"] == [])
    t("fx-lw-2019-not-measurable", lws["LT910001/1"]["not_measurable"][0]["reason"]
      == "receipt_record_not_retained" and lws["LT910001/1"]["truncated"] is True)
    t("fx-lw-wos", {r["work_order"] for r in lws["LA-9100001-9001"]["by_work_order"]}
      == {"WO910005/1", "WO910006/1", "WO910007/1", "WO910008/1", "WO910001/1"})
    shw = demo_lookup("shipment_lookup", fx["shipment_lookup"],
                      {"lot": None, "wo_id": None, "days": 7})
    t("fx-sh-window", shw["found"] and shw["n_shipments"] == 200 and shw["truncated"]
      and ok_payload("shipment_lookup", shw))
    shl = demo_lookup("shipment_lookup", fx["shipment_lookup"],
                      {"lot": "V0SYN01", "wo_id": None, "days": None})
    t("fx-sh-lot", shl["n_shipments"] == 1 and shl["shipments"][0]["work_order"] == "WO910004/1")
    shwo = demo_lookup("shipment_lookup", fx["shipment_lookup"],
                       {"lot": None, "wo_id": "WO910003/5", "days": None})
    t("fx-sh-wo-zero-honest", shwo["found"] is False and shwo["reason"] == "no_shipment_record")
    t("fx-miss-none", demo_lookup("lot_where_used", fx["lot_where_used"], "NOPE") is None)
    t("fx-builder-reproducible", build_fixtures() == fx)

    # ---- ⑤ 編排（假游標）：順序、臨時表只在非參數化批次建、參數型別 ----------------------
    g0 = fx["serial_genealogy"]["golden"][0]["steps"]
    cur = _FakeCursor({SQL_SG_B0_READ: g0["B0"]["rows"], SQL_SG_B1_READ: g0["B1"]["rows"],
                       SQL_SG_B6: g0["B6"]["rows"], SQL_SG_A6: g0["A6"]["rows"]})
    rs = compute_serial_genealogy(cur, "J0SYN02")
    t("cmp-sg-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_SG_TEMPS, SQL_SG_B0,
                                                  SQL_SG_B0_READ, SQL_SG_B1, SQL_SG_B1_READ,
                                                  SQL_SG_B6, SQL_SG_A6])
    t("cmp-sg-params", cur.log[2][1] == (MTL_CAP, "J0SYN02") and cur.log[4][1]
      == (TOPN, SINCE_DEFAULT))
    t("cmp-sg-valid", ok_payload("serial_genealogy", rs) and rs["found"])
    cur = _FakeCursor({SQL_SG_B0_READ: []})
    compute_serial_genealogy(cur, "NOPE")
    t("cmp-sg-miss-stops", [s for s, _ in cur.log][-1] is SQL_SG_B0_READ)
    gw = fx["wo_materials"]["golden"][0]["steps"]
    cur = _FakeCursor({SQL_WM_HEAD_READ: gw["A1"]["rows"], SQL_WM_COUNTS: (60, 880),
                       SQL_WM_A6: gw["A6"]["rows"]})
    rw = compute_wo_materials(cur, "WO910001/1")
    t("cmp-wm-like-base", cur.log[2][1] == ("WO910001/1", "WO910001/%", "WO910001/1"))
    t("cmp-wm-valid", ok_payload("wo_materials", rw) and rw["diag"]["n_serials"] == 60)
    t("★ cmp-wm-a6-cap-1000", [p for s_, p in cur.log if s_ is SQL_WM_A6] == [(DETAIL_CAP,)]
      and rw["truncated"] is False)            # fixture 200 列 < 1000 ⇒ live 語意下不算截斷
    cur = _FakeCursor({SQL_SG_B0_READ: g0["B0"]["rows"], SQL_SG_B1_READ: g0["B1"]["rows"],
                       SQL_SG_B6: g0["B6"]["rows"], SQL_SG_A6: g0["A6"]["rows"]})
    compute_serial_genealogy(cur, "J0SYN02")
    t("★ cmp-sg-a6-cap-1000", [p for s_, p in cur.log if s_ is SQL_SG_A6] == [(DETAIL_CAP,)]
      and [p for s_, p in cur.log if s_ is SQL_SG_B1] == [(TOPN, SINCE_DEFAULT)])
    gl = fx["lot_where_used"]["golden"][0]["steps"]
    cur = _FakeCursor({SQL_LW_C0_READ: gl["C0"]["rows"], SQL_LW_C1: gl["C1"]["rows"],
                       SQL_LW_C3: gl["C3"]["rows"], SQL_LW_C4A: gl["C4a"]["rows"]})
    compute_lot_where_used(cur, gl["C0"]["rows"][0][1])
    t("★ cmp-lw-c1-cap-1000", [p for s_, p in cur.log if s_ is SQL_LW_C1]
      == [(DETAIL_CAP, SINCE_DEFAULT)]
      and [p for s_, p in cur.log if s_ is SQL_LW_C3] == [(TOPN,)])
    cur = _FakeCursor({SQL_LW_C0_READ: [], })
    t("cmp-lw-miss", compute_lot_where_used(cur, "X")["reason"] == "no_material_matched")
    cur = _FakeCursor({SQL_SH_WINDOW: []})
    rsh = compute_shipments(cur, "window", {"lot": None, "wo_id": None, "days": 7})
    t("cmp-sh-window-bounds", rsh["diag"]["window"]["to"] >= rsh["diag"]["window"]["from"]
      and cur.log[-1][1][0] == TOPN)
    print(f"mes_material selftest: {n} assertions PASS")
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
    print("usage: python mes_material.py --selftest | --sql | --build-fixtures")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
