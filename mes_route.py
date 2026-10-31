#!/usr/bin/env python3
"""Tendline Board — Hermes 的**工單路線／版本解析**記錄級 MES 查詢核心（MES 工具包②·包 4）。

**這是什麼**：一支唯讀工具 `wo_route(wo_id)` 的資料腿（`board_mcp` 消費；runtime **沒有 LLM 參與**——
LLM 只叫工具，工具跑**固定審過的參數化 SQL**，`SPEC-HERMES-TRUST.md` §7.6.1）：
一張工單的路線（站序）＋每個站序掛了哪幾個段定義版本（recipe／PR 版本）＋**三層 fallback 收斂**到哪一版
（`board.wo_route.v1`；探針 A9-0／A9／A9a–e）。

**不是什麼**：不是序號在哪一站（`mes_hermes.wo_serials_status`）、不是用料（`mes_material.wo_materials`）、
不是規格上下限（`station_spec_limits` 另一包）。recipe 本文**不取**（只回 PR 版本號＋描述）。

**裁決唯一來源＝`_PROBE-ASSISTANT-MES-FAMILY-2-RESULT-DIGEST-20260923.md` §7.1**（2026-09-23 Fable 代裁、Chris 明示授權）：
  | 層 | 條件 | `resolution` | 指定版本？ |
  |---|---|---|---|
  | 1  | 該站序只有一個雙 Active 版本（無扇出） | `unique` | 是 |
  | 2  | 扇出 ∧ 本單序號**恰只**在一個候選版本上有段 | `ran_wo` | 是 |
  | 2b | 扇出 ∧ 本單序號在**多個**候選版本上都有段（事實、非模糊） | `ran_wo_multi` | 否（依本單段數降冪全列） |
  | 3  | 扇出 ∧ 本單序號在候選版本上**零段** | `ambiguous` | 否（依全廠近 90 天跑量降冪全列） |
  ★ **永不用 `sgdPublishedDate` 代裁**：套回原資料，WO910002 八個扇出站有 5 個「發佈日最新」的版本全廠
    90 天**零段**（order 16 最新 `/99` 0 段 vs 實跑 `/83` 段數充足）＋批量發佈（多版擠在同一分鐘）
    ⇒ 發佈日只當 `published_at` **揭露欄**，SQL 不以它排序、Python 不以它排序或選版（selftest 雙向釘）。
  ★ `is_wo_flo`（workflow 版本）作為收斂維度**放棄**（零鑑別力）＝不進碼、不進 caveat。

**唯一的 SQL 來源＝`_PROBE-ASSISTANT-MES-FAMILY-2-20260923.sql` §A9（:692-874）**（2026-09-23 12:41 Chris SSMS 實跑、
零錯誤）。每條語句**參數化後沿用**該步的欄位／join；偏離處逐條寫在下表與該常數上方，**零新增欄出處**
（唯一探針 A9 外的欄＝`production_request_v.rqsQuantityRequested`：`mes_hermes.SQL_WO_HEAD`（W1）live 已跑、
第一支探針 [0b] `production_request_v` 第 13 欄）。

═══ 對照探針的刻意偏離（理由寫在各常數上方）═══════════════════════════════════════════
  R1 **臨時表一律在非參數化批次建空殼、帶參數的語句只准 `INSERT INTO #x`**（`mes_hermes` 2026-09-21 live 首跑事故：
     pyodbc 帶參數＝`sp_executesql`，裡面 `SELECT … INTO #t` 建的表隨那次呼叫消失）。空殼一律 `WHERE 1=0`、
     **只從 `dbo.*` 取欄、彼此不 join**（同 `mes_hermes.SQL_WO_DROP_TEMPS`）。
  R2 **兩張單 → 一張單**：`#pr` 的 `@wo1 OR @wo2` → `= ?`（逐字等號、不走 LIKE、不收 `/n` 改版）；
     `rqs_id` 分組鍵隨之拿掉（單張時等價）。
  R3 **`#mpr`／`#ser`／`#ser_m` 三表併一張 `#rt_ser`（matPK 唯一）**：單張工單時 `#ser` 以 `mpr_rqs` 分組＝以 matPK
     分組、`#ser_m` 是它的去重複本 ⇒ 直接 `GROUP BY m.matPK`＋唯一叢集索引；`lot` 欄工具用不到（收窄）。
     `TOP (?)`＝`SER_CAP` 20,000（探針 `TOP (20000)` 逐字、參數化）。
  R4 **`#seg` 欄收窄**：拿掉 `cls`／`fam`（工具用不到）；`TOP (?)`＝`SEG_CAP` 500,000（探針逐字）；
     下界 `stpStartTime >= ?`＝`@wo_since`（'2000-01-01'＝真全歷史，同 `mes_hermes.SINCE_DEFAULT`）；**刻意不濾 stpEndTime**。
  R5 **`#route` 欄收窄**：拿掉 `is_wo_flo`／`flo_desc`／`floScope`／`floPublishedDate`／`flo_mcl`（連帶拿掉
     `material_class_t` 的 LEFT JOIN）／`sgdReworkTimes`／recipe 頭與長度／`prd*`／`mdfPK`／`bilPK`／`matDesignation`；
     加回 `sgdDesignation`（探針 WHERE 用的欄；Active 旗的出處）。條件（prdPK 收斂＋雙 Active）逐字。
  R6 **`#run_wo` 改直接回列**（不建表）：分組鍵 `mpr_rqs, sgdPK` → `sgdPK`（R2）；`n_seg_tagged_this_wo` 的比較對象
     由 `#pr.rqs_id` 欄改為參數 `?`（＝同一個工單號；免 CROSS JOIN、免 COLLATE 兩欄衝突）；**加** `n_ser = COUNT(DISTINCT g.matPK)`
     （既有欄的聚合、非新欄；臨時表上的 DISTINCT）；拿掉 `fam_min/fam_max`；加 `WHERE g.sgdPK IN (路線上的 sgd)` 收斂輸出。
  R7 **新增站序級計數 `SQL_RT_RUN_ORDER`**（臨時表上 `#rt_seg`×`#rt_route`；零新欄）：同站序多版時序號數不能由逐版相加。
  R8 **`#fan` 拿掉 `rqs_id`／`n_flo`**（R2；`n_flo` 工具用不到）。
  R9 **A9d 的 `#run_nd` 改直接回列**（不建表）；拿掉 `fam_min/fam_max`；逐站 OUTER APPLY＋`COLLATE DATABASE_DEFAULT`
     （temp 側）逐字；窗參數化 `>= ?`／`< ?`（上界＝現在，CONCEPT §21.1 髒日期）。**只在有 ambiguous 站序時才跑**
     （它是本工具最貴的一步、只有第 3 層需要它當排序鍵；第 2／2b 層不需要）⇒ 沒跑時 `n_seg_90d` 為 null、`diag.nd_scanned=false`。
  R10 **A9a／A9b2／A9c／A9e 四個「給人看」的結果集不送**：它們是彙總／攤開視圖，工具在 Python 由同一批列算出
     （`n_orders`／`n_fanned`＝A9a／A9b2；逐 sgd 維度＝A9c；收斂＝§7.1 裁決取代 A9e 的「只數不裁」）。
  R11 **新增 `SQL_RT_COUNTS`**（兩張臨時表的列數；cap 自揭用）。

═══ 欄位出處（[[ssms-sql-column-provenance]]）═══════════════════════════════════════════
  | 物件 | 本檔取的欄 | 探針步驟 |
  |---|---|---|
  | `dbo.production_request_v` | `rqsPK`／`rqsProductionRequestID`／`mclMaterialClassID`／`rqsDateStarted`／`rqsDateCompleted`／`floPK`／`floWorkflowID`／`prdPK`／`prdProductDefinitionID`／`ProductLineCode` | A9 `#pr` |
  |  | `rqsQuantityRequested` | `mes_hermes.SQL_WO_HEAD`（W1 live）；第一支 [0b] |
  | `dbo.material_production_request` | `ProductionRequestId`／`MaterialId` | A9 `#mpr` |
  | `dbo.materials_t` | `matPK` | A9 `#ser` |
  | `dbo.process_segment_v` | `stpPK`／`matPK`／`sgdPK`／`stpStartTime`／`SegmentProductionRequestCode`／`SegmentClassID` | A9 `#seg`／A9d |
  | `dbo.product_workflow_segment_v` | `floPK`／`floWorkflowID`／`flsOrder`／`flsRequirement`／`sgdPK`／`sgdProcessSegmentID`／`sgdDescription`／`SegmentClassID`／`sgdDesignation`／`sgdPublishedDate`／`prdPK`／`floDesignation` | A9 `#route` |

  **絕對不 SELECT 的欄**（`ALL_SQL()` 全文負斷言）：人名（`sgdPublishingAuthority`／`floPublishingAuthority`／
  `prdPublishingAuthority`／`sgcPublishingAuthority`／`stpPersonID`／`OperatorId`／`rqsRequestor`）、`*ChangeLog`、任何 `Comment`、
  recipe 本文（`sgdSegmentRecipe`）、`sgdWorkInstructionURI`。

★ perf 鐵則（沿用 `mes_hermes`／`mes_material`）：打 `dbo.*` 的每條 SELECT／INSERT 以 `OPTION (RECOMPILE)` 收尾；
  碰段表的每條都以序號（`#rt_ser` matPK 等號 join）或「單站＋時間窗」收斂；**刻意不濾 `stpEndTime`**
  （[[mes-zero-length-segments]]）。大 view 上零 `DISTINCT`（A9d 的 `GROUP BY sgdPK` 在 APPLY 內＝探針原形、單站＋窗）。
★ §24：零人名欄。★ 唯讀：唯一 DDL/DML 在 `#rt_*`（tempdb）。★ 凍結模組（`mes_timeline`／`mes_yield`）零 import。

CLI:
    python mes_route.py --selftest           # 純離線（讀 _fixtures/ golden ＋ 合成列），零 MES
    python mes_route.py --sql                # 只**印**本模組會送出的全部 SQL（不執行）
    python mes_route.py --build-fixtures     # 由探針 TSV 重建 fixture（純離線讀檔；fixture 不可手改）
    python mes_route.py --demo <wo_id>       # demo 路徑印 JSON
"""
from __future__ import annotations

import inspect
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import mes_material as _mm                                 # 純常數／純函式重用（連線、_s/_int/_ts、normalize_wo_id、_dump）
from mes_material import SchemaDivergence                  # noqa: F401  （連線單一出處＝mes_qlik，經 mes_material）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # pragma: no cover
    pass

BASE = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# 1. 人工審定常數
# =============================================================================
SCHEMA_ROUTE = "board.wo_route.v1"
TOOLS = ("wo_route",)
QUERY_TIMEOUT_SEC = 180

#: 上限（探針 `#ser TOP (20000)`／`#seg TOP (500000)` 逐字；同 `mes_hermes.SER_CAP／SEG_CAP`）。
SER_CAP = 20000
SEG_CAP = 500000
#: `#seg` 的時間下界＝真全歷史（探針 `@wo_since`；同 `mes_hermes.SINCE_DEFAULT`）。
SINCE_DEFAULT = "2000-01-01 00:00:00"
#: 全廠跑量窗（探針 `@route_days`；§7.1 第 3 層排序鍵）。
ROUTE_DAYS = 90

#: 三層 fallback（§7.1）＝閉集。
RESOLUTIONS = ("unique", "ran_wo", "ran_wo_multi", "ambiguous")
UNRESOLVED = ("ran_wo_multi", "ambiguous")

#: 回傳鍵集合（**查不到也長同一個形**；validator 與 smoke 以此為準）。
ROUTE_KEYS = ("wo_id", "found", "work_order", "n_orders", "route", "n_serials", "n_seg",
              "not_measurable", "truncated", "caveats", "reason", "unavailable_reason", "diag")
WORK_ORDER_KEYS = ("rqs_id", "product_class", "product_definition", "product_line", "workflow",
                   "qty_requested", "started_at", "completed_at")
ROUTE_ITEM_KEYS = ("order", "workflow", "station", "stations", "resolution", "chosen",
                   "n_candidates", "candidates", "n_serials", "n_seg")
CHOSEN_KEYS = ("sgd_pk", "seg_def", "description", "station", "active")
CANDIDATE_KEYS = ("sgd_pk", "seg_def", "description", "station", "requirement", "active",
                  "published_at", "n_seg_wo", "n_seg_tagged_this_wo", "n_serials_wo",
                  "last_run_wo", "n_seg_90d", "last_run_90d")
KEYS_BY_TOOL = {"wo_route": ROUTE_KEYS}

#: 「MES 裡真的沒有」的閉集（≠ `unavailable_reason`＝我們取不到）。
REASONS = ("wo_not_found", "no_route")
#: `not_measurable` 理由閉集（§7.1：未收斂的站序逐站列入）。
NOT_MEASURABLE_REASONS = ("route_version_unresolved",)

_TS_FMT = "%Y-%m-%d %H:%M:%S"

# =============================================================================
# 2. I/O 層：SQL 文本（全部模組常數＝離線可斷言；唯讀）
# =============================================================================
#: G0 物件／欄守衛。缺 ⇒ `SchemaDivergence`（停下、不回全零板）。欄＝本檔用到、且不在 Board 其他 live 查詢裡的那幾個。
SQL_GUARD = (
    "SELECT CASE WHEN OBJECT_ID('dbo.production_request_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.material_production_request') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.materials_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.process_segment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.product_workflow_segment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.production_request_v','prdPK')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.process_segment_v','sgdPK')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.process_segment_v','SegmentProductionRequestCode')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.product_workflow_segment_v','flsOrder')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.product_workflow_segment_v','sgdDesignation')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.product_workflow_segment_v','floDesignation')"
    " IS NULL THEN 0 ELSE 1 END\n"
    "OPTION (RECOMPILE)")
GUARD_KEYS = ("view_rqs", "tbx_mpr", "tbx_mlt", "view_seg", "view_pws", "rqs_prdPK",
              "seg_sgdPK", "SegmentProductionRequestCode", "pws_flsOrder", "pws_sgdDesignation",
              "pws_floDesignation")
GUARD_NEEDS = {"wo_route": GUARD_KEYS}

#: 空殼批次（R1）。五張表的欄與來源逐字同對應的 INSERT（`#rt_pr`＝A9 `#pr` :696-704＋`rqsQuantityRequested`；
#: `#rt_ser`＝`#ser`/`#ser_m` :723-733 併表（R3）；`#rt_seg`＝`#seg` :736-742 收窄（R4）；`#rt_route`＝`#route`
#: :755-768 收窄（R5）；`#rt_fan`＝`#fan` :772-776 收窄（R8；空殼從 view 取欄＝型別同源、不 join 臨時表）。
#: ⚠ `#rt_fan` 空殼的 `GROUP BY` **不可拿掉**：少了它 `COUNT(DISTINCT …)` 會回一列、空殼多一列垃圾。
SQL_RT_TEMPS = (
    "IF OBJECT_ID('tempdb..#rt_fan')   IS NOT NULL DROP TABLE #rt_fan;\n"
    "IF OBJECT_ID('tempdb..#rt_route') IS NOT NULL DROP TABLE #rt_route;\n"
    "IF OBJECT_ID('tempdb..#rt_seg')   IS NOT NULL DROP TABLE #rt_seg;\n"
    "IF OBJECT_ID('tempdb..#rt_ser')   IS NOT NULL DROP TABLE #rt_ser;\n"
    "IF OBJECT_ID('tempdb..#rt_pr')    IS NOT NULL DROP TABLE #rt_pr;\n"
    "SELECT r.rqsPK, rqs_id = r.rqsProductionRequestID, rqs_mcl = r.mclMaterialClassID,\n"
    "       started = r.rqsDateStarted, completed = r.rqsDateCompleted,\n"
    "       flo = r.floPK, flo_id = r.floWorkflowID, prd = r.prdPK, prd_id = r.prdProductDefinitionID,\n"
    "       prod_line = r.ProductLineCode, qty = r.rqsQuantityRequested\n"
    "INTO #rt_pr\n"
    "FROM dbo.production_request_v r\n"
    "WHERE 1=0;\n"
    "SELECT m.matPK\n"
    "INTO #rt_ser\n"
    "FROM dbo.material_production_request mpr\n"
    "JOIN dbo.materials_t m ON m.matPK = mpr.MaterialId\n"
    "WHERE 1=0\n"
    "GROUP BY m.matPK;\n"
    "SELECT v.stpPK, v.matPK, v.sgdPK, seg_start = v.stpStartTime,\n"
    "       seg_board = v.SegmentProductionRequestCode\n"
    "INTO #rt_seg\n"
    "FROM dbo.process_segment_v v\n"
    "WHERE 1=0;\n"
    "SELECT w.floPK, w.floWorkflowID, w.flsOrder, w.flsRequirement,\n"
    "       w.sgdPK, seg_def = w.sgdProcessSegmentID, seg_desc = w.sgdDescription,\n"
    "       station = w.SegmentClassID, w.sgdDesignation, w.sgdPublishedDate\n"
    "INTO #rt_route\n"
    "FROM dbo.product_workflow_segment_v w\n"
    "WHERE 1=0;\n"
    "SELECT w.floWorkflowID, w.flsOrder, n_sgd = COUNT(DISTINCT w.sgdPK)\n"
    "INTO #rt_fan\n"
    "FROM dbo.product_workflow_segment_v w\n"
    "WHERE 1=0\n"
    "GROUP BY w.floWorkflowID, w.flsOrder;")

#: A9 `#pr`（:696-704；R2 單張逐字等號）＋`rqsQuantityRequested`（W1 出處）。
SQL_RT_HEAD = (
    "INSERT INTO #rt_pr (rqsPK, rqs_id, rqs_mcl, started, completed, flo, flo_id, prd, prd_id,\n"
    "                    prod_line, qty)\n"
    "SELECT r.rqsPK, rqs_id = r.rqsProductionRequestID, rqs_mcl = r.mclMaterialClassID,\n"
    "       started = r.rqsDateStarted, completed = r.rqsDateCompleted,\n"
    "       flo = r.floPK, flo_id = r.floWorkflowID, prd = r.prdPK, prd_id = r.prdProductDefinitionID,\n"
    "       prod_line = r.ProductLineCode, qty = r.rqsQuantityRequested\n"
    "FROM dbo.production_request_v r\n"
    "WHERE r.rqsProductionRequestID = ?\n"
    "OPTION (RECOMPILE)")
SQL_RT_HEAD_READ = (
    "SELECT rqsPK, rqs_id, rqs_mcl, started, completed, flo, flo_id, prd, prd_id, prod_line, qty\n"
    "FROM #rt_pr ORDER BY rqsPK")

#: A9 `#route`（:755-768；R5 收窄；prdPK 收斂＋雙 Active 逐字；非參數化）。
SQL_RT_ROUTE = (
    "INSERT INTO #rt_route (floPK, floWorkflowID, flsOrder, flsRequirement, sgdPK, seg_def,\n"
    "                       seg_desc, station, sgdDesignation, sgdPublishedDate)\n"
    "SELECT w.floPK, w.floWorkflowID, w.flsOrder, w.flsRequirement,\n"
    "       w.sgdPK, seg_def = w.sgdProcessSegmentID, seg_desc = w.sgdDescription,\n"
    "       station = w.SegmentClassID, w.sgdDesignation, w.sgdPublishedDate\n"
    "FROM dbo.product_workflow_segment_v w\n"
    "JOIN #rt_pr p ON p.prd = w.prdPK\n"
    "WHERE w.sgdDesignation = 'Active'\n"
    "  AND w.floDesignation = 'Active'\n"
    "OPTION (RECOMPILE)")
#: ★ 排序鍵＝站序＋sgdPK（**不含**發佈日；§7.1 永不用 sgdPublishedDate 代裁）。
SQL_RT_ROUTE_READ = (
    "SELECT floPK, floWorkflowID, flsOrder, flsRequirement, sgdPK, seg_def, seg_desc, station,\n"
    "       sgdDesignation, sgdPublishedDate\n"
    "FROM #rt_route ORDER BY floWorkflowID, flsOrder, sgdPK")

#: A9 `#fan`（:772-776；R8；同定義：同一 floWorkflowID × flsOrder 下 distinct sgdPK > 1）。
SQL_RT_FAN = (
    "INSERT INTO #rt_fan (floWorkflowID, flsOrder, n_sgd)\n"
    "SELECT r.floWorkflowID, r.flsOrder, n_sgd = COUNT(DISTINCT r.sgdPK)\n"
    "FROM #rt_route r\n"
    "GROUP BY r.floWorkflowID, r.flsOrder\n"
    "HAVING COUNT(DISTINCT r.sgdPK) > 1")

#: A9 `#mpr`→`#ser`→`#ser_m`（:716-733；R3 併表；`TOP (?)`＝SER_CAP）。
SQL_RT_SER = (
    "INSERT INTO #rt_ser (matPK)\n"
    "SELECT TOP (?) m.matPK\n"
    "FROM dbo.material_production_request mpr\n"
    "JOIN #rt_pr p ON p.rqsPK = mpr.ProductionRequestId\n"
    "JOIN dbo.materials_t m ON m.matPK = mpr.MaterialId\n"
    "GROUP BY m.matPK\n"
    "OPTION (RECOMPILE)")
#: 探針 :733 `ix_ser_m` 逐字（表名換 `#rt_`）。
SQL_RT_SER_INDEX = "CREATE UNIQUE CLUSTERED INDEX ix_rt_ser ON #rt_ser (matPK)"

#: A9 `#seg`（:736-742；R4 收窄；`TOP (?)`＝SEG_CAP；下界＝`@wo_since`；**不濾 stpEndTime**）。
SQL_RT_SEG = (
    "INSERT INTO #rt_seg (stpPK, matPK, sgdPK, seg_start, seg_board)\n"
    "SELECT TOP (?) v.stpPK, v.matPK, v.sgdPK, seg_start = v.stpStartTime,\n"
    "       seg_board = v.SegmentProductionRequestCode\n"
    "FROM dbo.process_segment_v v\n"
    "JOIN #rt_ser s ON s.matPK = v.matPK\n"
    "WHERE v.stpStartTime >= ?\n"
    "OPTION (RECOMPILE)")
#: 探針 :743 逐字（表名換 `#rt_`）。
SQL_RT_SEG_INDEX = "CREATE INDEX ix_rt_seg ON #rt_seg (sgdPK, matPK)"

#: R11：兩張臨時表的列數（cap 自揭）。
SQL_RT_COUNTS = (
    "SELECT n_ser = (SELECT COUNT(*) FROM #rt_ser),\n"
    "       n_seg = (SELECT COUNT(*) FROM #rt_seg)")

#: A9 `#run_wo`（:779-786；R6：直接回列、單張、`seg_board = ?`、加 n_ser、只回路線上的 sgd）。
SQL_RT_RUN_WO = (
    "SELECT g.sgdPK, n_seg = COUNT(*),\n"
    "       n_seg_tagged_this_wo = SUM(CASE WHEN g.seg_board = ? THEN 1 ELSE 0 END),\n"
    "       n_ser = COUNT(DISTINCT g.matPK),\n"
    "       last_start = MAX(g.seg_start)\n"
    "FROM #rt_seg g\n"
    "WHERE g.sgdPK IN (SELECT r.sgdPK FROM #rt_route r)\n"
    "GROUP BY g.sgdPK\n"
    "ORDER BY g.sgdPK")

#: R7：站序級序號數／段數（同站序多版時序號數不能逐版相加）。臨時表上的 DISTINCT。
SQL_RT_RUN_ORDER = (
    "SELECT r.floWorkflowID, r.flsOrder, n_ser = COUNT(DISTINCT g.matPK),\n"
    "       n_seg = COUNT(DISTINCT g.stpPK)\n"
    "FROM #rt_seg g\n"
    "JOIN (SELECT x.floWorkflowID, x.flsOrder, x.sgdPK FROM #rt_route x\n"
    "      GROUP BY x.floWorkflowID, x.flsOrder, x.sgdPK) r ON r.sgdPK = g.sgdPK\n"
    "GROUP BY r.floWorkflowID, r.flsOrder\n"
    "ORDER BY r.floWorkflowID, r.flsOrder")

#: A9d `#run_nd`（:845-857；R9 直接回列、拿掉 fam、窗參數化；逐站 OUTER APPLY＝單 class＋窗；
#: temp 側 `COLLATE DATABASE_DEFAULT`（Msg 468 防線）、view 欄側不加＝保住 SegmentClassID 上的 seek）。
#: ★ 只在有 ambiguous 站序時才送（R9）；只掃「有扇出的站序用到的站」（探針原設計＝控制代價）。
SQL_RT_RUN_ND = (
    "SELECT c.station, a.sgdPK, a.n_seg_nd, a.last_start_nd\n"
    "FROM (SELECT DISTINCT r.station FROM #rt_route r\n"
    "      WHERE EXISTS (SELECT 1 FROM #rt_fan f\n"
    "                    WHERE f.floWorkflowID = r.floWorkflowID AND f.flsOrder = r.flsOrder)) c\n"
    "OUTER APPLY (\n"
    "    SELECT s.sgdPK, n_seg_nd = COUNT(*), last_start_nd = MAX(s.stpStartTime)\n"
    "    FROM dbo.process_segment_v s\n"
    "    WHERE s.SegmentClassID = c.station COLLATE DATABASE_DEFAULT\n"
    "      AND s.stpStartTime >= ?\n"
    "      AND s.stpStartTime <  ?\n"
    "    GROUP BY s.sgdPK\n"
    ") a\n"
    "OPTION (RECOMPILE)")


def ALL_SQL():
    """本模組會送出的**全部 SQL**（selftest 負斷言的唯一清單；新增查詢必須登記在這裡）。"""
    return (SQL_GUARD, SQL_RT_TEMPS, SQL_RT_HEAD, SQL_RT_HEAD_READ, SQL_RT_ROUTE,
            SQL_RT_ROUTE_READ, SQL_RT_FAN, SQL_RT_SER, SQL_RT_SER_INDEX, SQL_RT_SEG,
            SQL_RT_SEG_INDEX, SQL_RT_COUNTS, SQL_RT_RUN_WO, SQL_RT_RUN_ORDER, SQL_RT_RUN_ND)


#: `board_mcp._TOOL_QUERIES` 用的真名（grep 得到）。
TOOL_QUERY_NAMES = {
    "wo_route": ("mes_route.SQL_RT_HEAD", "mes_route.SQL_RT_ROUTE", "mes_route.SQL_RT_SER",
                 "mes_route.SQL_RT_SEG", "mes_route.SQL_RT_RUN_WO", "mes_route.SQL_RT_RUN_ORDER",
                 "mes_route.SQL_RT_RUN_ND"),
}


def open_connection(timeout=QUERY_TIMEOUT_SEC):
    """唯讀連線（`mes_material.open_connection`＝`mes_qlik._connect` 重用）。"""
    return _mm.open_connection(timeout=timeout)


error_detail = _mm.error_detail          # 降級形 `diag.error_detail` 的全 repo 單一出處

# =============================================================================
# 3. 純函式層（零 I/O；selftest 主戰場）
# =============================================================================
_s, _int, _ts = _mm._s, _mm._int, _mm._ts


def _fmt(v):
    return v.strftime(_TS_FMT) if isinstance(v, datetime) else v


def normalize_wo(wo_id):
    """工單號 → `(normalized, err)`（`mes_hermes.normalize_wo_id` 單一出處，經 `mes_material`）。"""
    return _mm.normalize_wo_id(wo_id)


def normalize_args(tool, wo_id):
    """`board_mcp` 用的統一入口（同 `mes_equipment.normalize_args` 的形）。"""
    if tool != "wo_route":
        return None, {"error": "unknown_tool", "given": tool}
    return normalize_wo(wo_id)


def blank(tool, key, *, found=False, reason=None, unavailable_reason=None, diag=None):
    if tool != "wo_route":
        raise KeyError(tool)
    return {"wo_id": key, "found": found, "work_order": None, "n_orders": None, "route": [],
            "n_serials": None, "n_seg": None, "not_measurable": [], "truncated": False,
            "caveats": [], "reason": reason, "unavailable_reason": unavailable_reason,
            "diag": dict(diag or {})}


def precheck(tool, key):
    """不需 MES 就能下結論的情況 → 回完整 payload；否則 None（目前恆 None；保留與 `mes_etest` 同介面）。"""
    if tool not in TOOLS:
        raise KeyError(tool)
    return None


# ---- 固定句（§7.1 原句與工具自揭；caveat 全文住這裡、不散在呼叫端）----------------
CAVEAT_ORDER = "站序來自 MES 路線定義（workflow 的 flsOrder）；實跑順序可能不同，Optional 站也可能被跳過。"
CAVEAT_CANDIDATES = ("候選版本＝路線定義裡「段定義與 workflow 都是 Active」的版本；同一站序掛多個版本＝扇出，"
                     "扇出時依三層規則收斂：本單序號只跑過一版＝ran_wo（指定）；跑過多版＝ran_wo_multi（全列、"
                     "依本單段數排序、不指定）；零紀錄＝ambiguous（全列、依全廠近 90 天跑量排序、不指定）。")
CAVEAT_PUBLISHED = ("`published_at`（發佈日）只揭露、**不拿來選版**：MES 常批量發佈（多版擠在同一分鐘），"
                    "且發佈日最新的版本常常全廠沒人在跑。")
CAVEAT_RAN_WO = ("`n_seg_wo` 計本單序號在該版本上的全部段（含以其他工單號記錄的段）；記在本單號下的段數見 "
                 "`n_seg_tagged_this_wo`。")
CAVEAT_ND_SCANNED = ("`n_seg_90d`＝全廠近 {d} 天在該版本上的段數（以段開始時間計），只對有扇出的站序掃描；"
                     "無扇出的站序此欄為 null。")
CAVEAT_ND_SKIPPED = ("本單沒有 ambiguous 站序 ⇒ 未掃全廠跑量（`n_seg_90d` 全為 null；它只是第 3 層的排序鍵，"
                     "省下本工具最貴的一步）。")
#: §7.1 第 3 層固定句（「探針上限」→「查詢上限」）。
CAVEAT_AMBIGUOUS = ("第 {order} 站序（{station}）：該站候選 {n} 版、本單序號在 MES 零段紀錄（序號可能曾以別的工單號跑過，"
                    "或已超出查詢上限）；依全廠近 {d} 天跑量排序，**不指定本單走哪一版**。")
CAVEAT_MULTI = ("第 {order} 站序（{station}）：本單序號確實跑過 {k} 個版本（這是事實、不是模糊），"
                "依本單段數由多到少全列、不指定單一版本。")
#: §7.1：整條路線若有未收斂站序，頂層加一句。
CAVEAT_TOP_UNRESOLVED = "路線 {n} 站中 {m} 站版本未收斂（本單走過多版 {a} 站、本單零紀錄 {b} 站）。"
CAVEAT_MULTI_STATION = "第 {order} 站序同時掛了不同站類別（{stations}）＝並列或擇一的站，各版本分屬不同站。"
CAVEAT_TRUNCATED = ("⚠ 序號或段數撞到查詢上限（序號 {sc:,}／段 {gc:,}）⇒ 本單段數是**下界**，"
                    "部分站序可能因此被判成 ambiguous。")
CAVEAT_NO_SERIALS = "本單在 MES 沒有序號名冊（material_production_request 零列）⇒ 扇出的站序一律無法以本單紀錄收斂。"
NOTE_NOT_FOUND = ("MES 查無此工單號（以 rqsProductionRequestID 逐字等號比對；MES 工單號形如 WO910001/1，"
                  "要含 /n 改版後綴）。")
NOTE_NO_ROUTE = ("工單存在，但它的產品定義在 MES 找不到「段定義與 workflow 都是 Active」的路線"
                 "（路線可能已改版停用）⇒ 無法列站序。")
NM_NOTE = {"ran_wo_multi": "本單序號跑過多個版本；全列、不指定單一版本。",
           "ambiguous": "本單序號在候選版本上零段紀錄；依全廠跑量排序、不指定版本。"}


def _sort_desc(v):
    """降冪排序鍵：None 排最後。"""
    return -(v if v is not None else -1)


def derive_wo_route(wo_id, head_rows, route_rows, run_wo_rows, run_order_rows, nd_rows,
                    counts_row=None, *, run_wo_scope="all", nd_scanned=False, nd_window=None,
                    route_days=ROUTE_DAYS, since_used=SINCE_DEFAULT, fetched_at=None, caps=None,
                    elapsed_s=None):
    """探針 A9 形的列 → `board.wo_route.v1`（**純函式、零 I/O**；demo 與 live 同一支）。

    列欄序（＝SQL SELECT 順序）：
      head   `(rqsPK, rqs_id, rqs_mcl, started, completed, flo, flo_id, prd, prd_id, prod_line, qty)`
      route  `(floPK, floWorkflowID, flsOrder, flsRequirement, sgdPK, seg_def, seg_desc, station,
               sgdDesignation, sgdPublishedDate)`
      run_wo `(sgdPK, n_seg, n_seg_tagged_this_wo, n_ser, last_start)`
      order  `(floWorkflowID, flsOrder, n_ser, n_seg)`
      nd     `(station, sgdPK, n_seg_nd, last_start_nd)`
      counts `(n_ser, n_seg)`
    `run_wo_scope`："all"（live：不在 run_wo 的 sgd＝本單零段）／"fanned_only"（demo：探針只量了扇出站，
    非扇出站的本單計數＝null、不是 0）。
    """
    caps = dict(caps or {})
    diag = {"fetched_at": _fmt(fetched_at), "elapsed_s": elapsed_s, "since_used": since_used,
            "route_days": route_days, "nd_scanned": bool(nd_scanned),
            "nd_window": dict(nd_window) if nd_window else None,
            "run_wo_scope": run_wo_scope, "cap_hit": caps,
            "n_head_rows": len(head_rows), "n_route_rows_raw": len(route_rows),
            "n_orders": None, "n_fanned": None, "n_unresolved": None,
            "n_ran_wo_multi": None, "n_ambiguous": None}
    if not head_rows:
        out = blank("wo_route", wo_id, found=False, reason="wo_not_found", diag=diag)
        out["caveats"] = [NOTE_NOT_FOUND]
        return out
    h = tuple(head_rows[0])
    work_order = {"rqs_id": _s(h[1]), "product_class": _s(h[2]), "product_definition": _s(h[8]),
                  "product_line": _s(h[9]), "workflow": _s(h[6]),
                  "qty_requested": _mm._num(h[10]), "started_at": _ts(h[3]),
                  "completed_at": _ts(h[4])}
    n_ser_all = n_seg_all = None
    if counts_row is not None:
        n_ser_all, n_seg_all = _int(counts_row[0]), _int(counts_row[1])
    truncated = any(bool(v) for v in caps.values())
    base = blank("wo_route", wo_id, found=True, diag=diag)
    diag = base["diag"]                      # blank() 複製一份；之後的 update 要寫在回傳物件上
    base.update({"work_order": work_order, "n_serials": n_ser_all, "n_seg": n_seg_all,
                 "truncated": truncated})

    # ── 路線：(workflow, 站序) → 候選（以 sgdPK 去重；view 可能因 mdf／bil 重複成多列）──
    orders = {}
    for r in route_rows:
        r = tuple(r)
        wf, order, sgd, seg_def = _s(r[1]), _int(r[2]), _int(r[4]), _s(r[5])
        ckey = sgd if sgd is not None else ("def", seg_def)
        cands = orders.setdefault((wf, order), {})
        if ckey in cands:
            continue
        desig = _s(r[8])
        cands[ckey] = {"sgd_pk": sgd, "seg_def": seg_def, "description": _s(r[6]),
                       "station": _s(r[7]), "requirement": _s(r[3]),
                       "active": (desig == "Active") if desig is not None else None,
                       "published_at": _ts(r[9]),
                       "n_seg_wo": None, "n_seg_tagged_this_wo": None, "n_serials_wo": None,
                       "last_run_wo": None, "n_seg_90d": None, "last_run_90d": None}
    if not orders:
        base["reason"] = "no_route"
        base["n_orders"] = 0
        base["caveats"] = [NOTE_NO_ROUTE]
        diag.update({"n_orders": 0, "n_fanned": 0, "n_unresolved": 0, "n_ran_wo_multi": 0,
                     "n_ambiguous": 0})
        return base

    run_wo = {}
    for r in run_wo_rows:
        r = tuple(r)
        run_wo[_int(r[0])] = (_int(r[1]) or 0, _int(r[2]) or 0, _int(r[3]), _ts(r[4]))
    run_order = {(_s(r[0]), _int(r[1])): (_int(r[2]), _int(r[3])) for r in map(tuple, run_order_rows)}
    nd = {}
    for r in nd_rows:
        r = tuple(r)
        pk = _int(r[1])
        if pk is not None:
            nd[pk] = (_int(r[2]) or 0, _ts(r[3]))
    fls = {k[0] for k in orders}

    route, nm, cav_tail = [], [], []
    n_multi = n_amb = 0
    for key in sorted(orders, key=lambda k: (k[1] if k[1] is not None else 10 ** 9, str(k[0]))):
        wf, order = key
        cands = list(orders[key].values())
        fanned = len(cands) > 1
        for c in cands:
            rw = run_wo.get(c["sgd_pk"]) if c["sgd_pk"] is not None else None
            if rw is not None:
                c["n_seg_wo"], c["n_seg_tagged_this_wo"], c["n_serials_wo"], c["last_run_wo"] = rw
            elif run_wo_scope == "all" or fanned:
                c["n_seg_wo"], c["n_seg_tagged_this_wo"], c["n_serials_wo"] = 0, 0, 0
            if nd_scanned and fanned and c["sgd_pk"] is not None:
                c["n_seg_90d"], c["last_run_90d"] = nd.get(c["sgd_pk"], (0, None))
        # ★ 排序／選版鍵只有：本單段數、全廠 90 天段數、版本號字串、sgdPK——**永不含發佈日**
        if not fanned:
            res, chosen_c = "unique", cands[0]
        else:
            ran = [c for c in cands if (c["n_seg_wo"] or 0) > 0]
            if len(ran) == 1:
                res, chosen_c = "ran_wo", ran[0]
                cands.sort(key=lambda c: (c is not chosen_c, _sort_desc(c["n_seg_wo"]),
                                          _sort_desc(c["n_seg_90d"]), str(c["seg_def"]),
                                          c["sgd_pk"] or 0))
            elif len(ran) > 1:
                res, chosen_c = "ran_wo_multi", None
                cands.sort(key=lambda c: (_sort_desc(c["n_seg_wo"]), str(c["seg_def"]),
                                          c["sgd_pk"] or 0))
            else:
                res, chosen_c = "ambiguous", None
                cands.sort(key=lambda c: (_sort_desc(c["n_seg_90d"]), str(c["seg_def"]),
                                          c["sgd_pk"] or 0))
        stations = sorted({c["station"] for c in cands if c["station"]})
        st_label = stations[0] if len(stations) == 1 else "／".join(stations)
        if key in run_order:
            o_ser, o_seg = run_order[key]
        elif run_wo_scope == "all":
            o_ser, o_seg = 0, 0
        else:
            segs = [c["n_seg_wo"] for c in cands]
            o_ser = None
            o_seg = sum(segs) if segs and all(v is not None for v in segs) else None
        chosen = ({k: chosen_c[k] for k in CHOSEN_KEYS} if chosen_c is not None else None)
        route.append({"order": order, "workflow": wf,
                      "station": stations[0] if len(stations) == 1 else None,
                      "stations": stations, "resolution": res, "chosen": chosen,
                      "n_candidates": len(cands), "candidates": cands,
                      "n_serials": o_ser, "n_seg": o_seg})
        field = f"route[{order}].sgd" if len(fls) == 1 else f"route[{wf}:{order}].sgd"
        if res in UNRESOLVED:
            nm.append({"field": field, "reason": "route_version_unresolved", "note": NM_NOTE[res]})
        if res == "ran_wo_multi":
            n_multi += 1
            cav_tail.append(CAVEAT_MULTI.format(order=order, station=st_label,
                                                k=sum(1 for c in cands if (c["n_seg_wo"] or 0) > 0)))
        elif res == "ambiguous":
            n_amb += 1
            cav_tail.append(CAVEAT_AMBIGUOUS.format(order=order, station=st_label, n=len(cands),
                                                    d=route_days))
        if len(stations) > 1:
            cav_tail.append(CAVEAT_MULTI_STATION.format(order=order, stations="／".join(stations)))

    n_fanned = sum(1 for x in route if x["n_candidates"] > 1)
    caveats = [CAVEAT_ORDER, CAVEAT_CANDIDATES, CAVEAT_PUBLISHED, CAVEAT_RAN_WO,
               CAVEAT_ND_SCANNED.format(d=route_days) if nd_scanned else CAVEAT_ND_SKIPPED]
    if n_multi + n_amb:
        caveats.append(CAVEAT_TOP_UNRESOLVED.format(n=len(route), m=n_multi + n_amb, a=n_multi,
                                                    b=n_amb))
    caveats.extend(cav_tail)
    if n_ser_all == 0:
        caveats.append(CAVEAT_NO_SERIALS)
    if truncated:
        caveats.append(CAVEAT_TRUNCATED.format(sc=SER_CAP, gc=SEG_CAP))
    diag.update({"n_orders": len(route), "n_fanned": n_fanned, "n_unresolved": n_multi + n_amb,
                 "n_ran_wo_multi": n_multi, "n_ambiguous": n_amb})
    base.update({"n_orders": len(route), "route": route, "not_measurable": nm,
                 "caveats": caveats})
    return base


# ---- validator（形狀檢查單一出處；smoke／selftest 委派它）--------------------------
_FORBIDDEN_OUT = ("stpPersonID", "OperatorName", "OperatorId", "matPK", "stpPK", "rqsPK",
                  "PublishingAuthority", "ChangeLog", "SegmentRecipe")


def ok_payload(tool, rec):
    try:
        if tool != "wo_route":
            return False
        body = {k: v for k, v in rec.items() if k not in ("source", "provenance")}
        if set(body) != set(ROUTE_KEYS):
            return False
        if not isinstance(rec["caveats"], list) or not isinstance(rec["diag"], dict):
            return False
        if rec["reason"] is not None and rec["reason"] not in REASONS:
            return False
        if rec["found"]:
            if rec["unavailable_reason"] or rec["reason"] not in (None, "no_route"):
                return False
            if set(rec["work_order"]) != set(WORK_ORDER_KEYS):
                return False
        elif rec["route"]:
            return False
        for nm in rec["not_measurable"]:
            if nm["reason"] not in NOT_MEASURABLE_REASONS:
                return False
        n_unres = 0
        for it in rec["route"]:
            if set(it) != set(ROUTE_ITEM_KEYS) or it["resolution"] not in RESOLUTIONS:
                return False
            if it["n_candidates"] != len(it["candidates"]):
                return False
            if (it["chosen"] is None) != (it["resolution"] in UNRESOLVED):
                return False
            if it["chosen"] is not None and set(it["chosen"]) != set(CHOSEN_KEYS):
                return False
            if (it["resolution"] == "unique") != (it["n_candidates"] == 1):
                return False
            for c in it["candidates"]:
                if set(c) != set(CANDIDATE_KEYS):
                    return False
            n_unres += it["resolution"] in UNRESOLVED
        if n_unres != len(rec["not_measurable"]):
            return False
        blob = json.dumps(body, ensure_ascii=False, default=str)
        if any(f in blob for f in _FORBIDDEN_OUT):
            return False
        if "\n" in "".join(_mm._walk_strings(body)):
            return False
        return True
    except Exception:                                   # noqa: BLE001
        return False


# =============================================================================
# 4. I/O 編排（唯一的編排點；失敗一律往上拋，由 `board_data` 接成誠實降級）
# =============================================================================
def fetch_guard(cur, tool="wo_route"):
    cur.execute(SQL_GUARD)
    r = cur.fetchone()
    g = {k: bool(r[i]) for i, k in enumerate(GUARD_KEYS)}
    missing = [k for k in GUARD_NEEDS[tool] if not g.get(k)]
    if missing:
        raise SchemaDivergence("schema divergence: " + ", ".join(f"{k} ABSENT" for k in missing))
    return g


def _rows(cur):
    return [tuple(r) for r in cur.fetchall()]


def _fanned_orders(route_rows):
    """路線列 → 扇出站序集合（= `#rt_fan` 的 Python 對等；用來決定要不要送 A9d）。"""
    by = {}
    for r in route_rows:
        by.setdefault((_s(r[1]), _int(r[2])), set()).add(_int(r[4]))
    return {k for k, v in by.items() if len(v) > 1}


def compute_wo_route(cur, wo_id, *, now=None, since=SINCE_DEFAULT, ser_cap=SER_CAP,
                     seg_cap=SEG_CAP, route_days=ROUTE_DAYS):
    """`wo_route(wo_id)` 的唯一 I/O 編排點（`wo_id` 必須已經過 `normalize_wo`）。

    順序＝guard → 空殼 → 工單頭 →〔查無停〕→ 路線 →〔無路線停〕→ 扇出 → 序號 → 段 → 計數 →
    逐版本段數 → 逐站序計數 →〔**只在有 ambiguous 站序時**〕全廠 90 天跑量（A9d）。"""
    t0 = time.perf_counter()
    fetch_guard(cur)
    now = now or datetime.now()
    cur.execute(SQL_RT_TEMPS)
    cur.execute(SQL_RT_HEAD, wo_id)
    cur.execute(SQL_RT_HEAD_READ)
    head = _rows(cur)
    route = run_wo = run_order = nd = []
    counts = None
    nd_scanned, nd_window, caps = False, None, {"serials": False, "segments": False}
    if head:
        cur.execute(SQL_RT_ROUTE)
        cur.execute(SQL_RT_ROUTE_READ)
        route = _rows(cur)
    if route:
        cur.execute(SQL_RT_FAN)
        cur.execute(SQL_RT_SER, int(ser_cap))
        cur.execute(SQL_RT_SER_INDEX)
        cur.execute(SQL_RT_SEG, int(seg_cap), since)
        cur.execute(SQL_RT_SEG_INDEX)
        cur.execute(SQL_RT_COUNTS)
        counts = tuple(cur.fetchone())
        caps = {"serials": (_int(counts[0]) or 0) >= int(ser_cap),
                "segments": (_int(counts[1]) or 0) >= int(seg_cap)}
        cur.execute(SQL_RT_RUN_WO, wo_id)
        run_wo = _rows(cur)
        cur.execute(SQL_RT_RUN_ORDER)
        run_order = _rows(cur)
        ran = {_int(r[0]) for r in run_wo if (_int(r[1]) or 0) > 0}
        by = {}
        for r in route:
            by.setdefault((_s(r[1]), _int(r[2])), set()).add(_int(r[4]))
        has_ambiguous = any(len(v) > 1 and not (v & ran) for v in by.values())
        if has_ambiguous:
            frm = (now - timedelta(days=int(route_days))).strftime(_TS_FMT)
            to = now.strftime(_TS_FMT)            # ★ 上界永不超過現在（CONCEPT §21.1 髒日期）
            nd_window = {"from": frm, "to": to, "days": int(route_days)}
            cur.execute(SQL_RT_RUN_ND, frm, to)
            nd = _rows(cur)
            nd_scanned = True
    return derive_wo_route(wo_id, head, route, run_wo, run_order, nd, counts,
                           run_wo_scope="all", nd_scanned=nd_scanned, nd_window=nd_window,
                           route_days=int(route_days), since_used=since, fetched_at=now,
                           caps=caps, elapsed_s=round(time.perf_counter() - t0, 3))


def compute(tool, cur, key):
    """`board_data.get_mes_route` 的 live 分派點（`key` 已經過 normalize；呼叫端先跑 `precheck`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    if tool == "wo_route":
        return compute_wo_route(cur, key)
    raise KeyError(tool)


# =============================================================================
# 5. demo：fixture（`_fixtures/mes_route_wo_route.json`）→ 同一支 derive_*
# =============================================================================
FIXTURE_FILES = {t: f"mes_route_{t}.json" for t in TOOLS}
DEMO_WO = "WO910002/1"

_DEMO_CAVEAT = ("（demo）探針樣本：逐站路線取自第一份 fixture 樣本、扇出站序的版本／本單段數／全廠 90 天跑量取自"
                "第二份 fixture A9c／A9d；無扇出站序的 sgdPK、本單段數、序號數與工單數量探針沒取 ⇒ "
                "demo 為 null（不是 0）；live 會全部回值。")


def demo_lookup(tool, doc, key):
    """fixture 裡找樣本 → 跑**同一支** `derive_wo_route`（demo 與 live 形狀單一出處）。
    查不到回 `None`（呼叫端回 `not_in_demo_fixture`，**不是** `wo_not_found`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    for g in (doc or {}).get("golden") or []:
        if str(g["key"]).upper() == str(key).upper():
            s = g["steps"]
            out = derive_wo_route(
                g["key"], s["HEAD"]["rows"], s["ROUTE"]["rows"], s["RUN_WO"]["rows"],
                s["RUN_ORDER"]["rows"], s["ND"]["rows"], s["COUNTS"]["rows"][0],
                run_wo_scope=g["run_wo_scope"], nd_scanned=g["nd_scanned"],
                nd_window=g["nd_window"], route_days=g["nd_window"]["days"],
                fetched_at=g["captured_at"], caps=g["caps"])
            out["caveats"].append(_DEMO_CAVEAT)
            return out
    return None


# =============================================================================
# 6. fixture builder（讀探針 TSV；純離線、零 MES）
# =============================================================================
PROBE_TSV = "_PROBE-ASSISTANT-MES-FAMILY-2-RESULT.tsv"
PROBE_SQL = "_PROBE-ASSISTANT-MES-FAMILY-2-20260923.sql"
ROUTE_TSVS = {"WO910002/1": "_PROBE-ASSISTANT-MES-FAMILY-RESULT-WO910002.tsv",
              "WO910001/1": "_PROBE-ASSISTANT-MES-FAMILY-RESULT-WO910001.tsv"}
ROUTE_PROBE_SQL = "_PROBE-ASSISTANT-MES-FAMILY-20260922.sql"
CAPTURED_AT = "2026-08-13 11:00:00"
#: 各張示範哪幾層（selftest 反向驗證：跑 derive 後的實際 resolution 集合必須相等）。
DEMONSTRATES = {"WO910002/1": ["unique", "ran_wo", "ambiguous"],
                "WO910001/1": ["unique", "ran_wo", "ran_wo_multi"]}
_SKIP_PREFIX = ("Warning:", "Completion time", "(")


def parse_probe_tsv(path):
    """SSMS 多結果集 TSV → `{step 值: {"header", "rows", "ragged_fixed"}}`。

    ⚠ 描述欄內嵌 tab（例 `QR910008/7` 的描述以 tab 開頭）會多出一格：列長＝表頭＋1 且有空格時，
    刪掉 index>0 的第一個空格（＝那個前導 tab；`_s()` 本來也會把它清掉）。"""
    sets, cur = [], None
    with io.open(path, encoding="utf-8-sig") as fh:
        for ln in fh.read().split("\n"):
            ln = ln.rstrip("\r")
            if ln.startswith("step\t"):
                cur = {"header": ln.split("\t"), "rows": [], "ragged_fixed": 0, "ragged_skipped": 0}
                sets.append(cur)
                continue
            if cur is None or not ln.strip() or ln.startswith(_SKIP_PREFIX):
                continue
            cells = ln.split("\t")
            if len(cells) == len(cur["header"]) + 1 and "" in cells[1:]:
                del cells[cells.index("", 1)]
                cur["ragged_fixed"] += 1
            if len(cells) != len(cur["header"]):
                cur["ragged_skipped"] += 1          # 本工具用不到的結果集可能有；用到的由 builder 檢查為 0
                continue
            cur["rows"].append(cells)
    out = {}
    for s in sets:
        if s["rows"]:
            out.setdefault(s["rows"][0][0], s)
    return out


def _dicts(step_set, where=None):
    rows = []
    for r in step_set["rows"]:
        d = {k: (None if v == "NULL" else v) for k, v in zip(step_set["header"], r)}
        if where is None or where(d):
            rows.append(d)
    return rows


def build_fixtures(base=BASE):
    t = parse_probe_tsv(os.path.join(base, PROBE_TSV))
    ctx = _dicts(t["0e context"])[0]
    nd_window = {"from": ctx["route_from"][:19], "to": ctx["win_to"][:19], "days": ROUTE_DAYS}
    heads = {d["rqs_id"]: d for d in _dicts(t["A9-0 WO header (own floPK / prdPK / product line)"])}
    a9a = {d["rqs_id"]: d for d in _dicts(t["A9a route size overview"])}
    a9c = _dicts(t["A9c fanned order x sgd dimensions"])
    for step in ("A9-0 WO header (own floPK / prdPK / product line)", "A9a route size overview",
                 "A9c fanned order x sgd dimensions",
                 "A9d fanned sgd - plant-wide runs in route window"):
        if t[step]["ragged_skipped"]:
            raise ValueError(f"{step}: {t[step]['ragged_skipped']} unparseable rows")
    a9d = _dicts(t["A9d fanned sgd - plant-wide runs in route window"])
    golden = []
    for wo in sorted(ROUTE_TSVS):
        h = heads[wo]
        r1 = parse_probe_tsv(os.path.join(base, ROUTE_TSVS[wo]))["A9 WO route (Active-filtered)"]
        if r1["ragged_skipped"]:
            raise ValueError(f"{wo}: route list has {r1['ragged_skipped']} unparseable rows")
        c_wo = [d for d in a9c if d["rqs_id"] == wo]
        by_def = {(int(d["flsOrder"]), d["seg_def"]): d for d in c_wo}
        route = []
        for d in _dicts(r1):
            c = by_def.get((int(d["flsOrder"]), d["seg_def"]))
            # A9a：n_flo_pk=1 且 n_rows_on_wo_flo=n_view_rows ⇒ 全部路線列的 floPK＝工單自己的 flo。
            route.append([h["flo"], d["floWorkflowID"], d["flsOrder"], d["flsRequirement"],
                          c["sgdPK"] if c else None, d["seg_def"], d["seg_desc"], d["station"],
                          "Active", c["sgdPublishedDate"] if c else None])
        missing = [d for d in c_wo
                   if not any(r[5] == d["seg_def"] and int(r[2]) == int(d["flsOrder"]) for r in route)]
        if missing:
            raise ValueError(f"{wo}: A9c sgd not in first-probe route: {[d['seg_def'] for d in missing]}")
        if len(route) != int(a9a[wo]["n_sgd"]):
            raise ValueError(f"{wo}: route rows {len(route)} != A9a n_sgd {a9a[wo]['n_sgd']}")
        run_wo = [[d["sgdPK"], d["n_seg_this_wo_serials"], d["n_seg_tagged_this_wo"], None,
                   d["last_run_this_wo_serials"]] for d in c_wo]
        nd = [[d["station"], d["sgdPK"], d["n_seg_nd"], d["last_start_nd"]]
              for d in a9d if d["rqs_id"] == wo]
        head = [[h["rqsPK"], h["rqs_id"], h["rqs_mcl"], h["started"], h["completed"], h["flo"],
                 h["flo_id"], h["prd"], h["prd_id"], h["prod_line"], None]]
        golden.append({
            "key": wo, "demonstrates": DEMONSTRATES[wo],
            "captured_at": CAPTURED_AT, "source_probe": PROBE_SQL,
            "route_source_probe": ROUTE_PROBE_SQL,
            "_provenance": ("domain-side read-only query 2026-08-13 (" + PROBE_SQL + " A9-0/A9a/A9c/A9d) + "
                            "2026-08-12 (" + ROUTE_PROBE_SQL + " A9 route list, " + ROUTE_TSVS[wo] +
                            "); builder = mes_route.py --build-fixtures (do not hand-edit)"),
            "a9a_check": {"n_order": int(a9a[wo]["n_order"]), "n_sgd": int(a9a[wo]["n_sgd"]),
                          "n_fanned_order": int(a9a[wo]["n_fanned_order"])},
            "run_wo_scope": "fanned_only", "nd_scanned": True, "nd_window": dict(nd_window),
            "caps": {"serials": False, "segments": False},
            "steps": {
                "HEAD": {"source_step": "A9-0 (qty not in probe)",
                         "columns": ["rqsPK", "rqs_id", "rqs_mcl", "started", "completed", "flo",
                                     "flo_id", "prd", "prd_id", "prod_line", "qty"], "rows": head},
                "ROUTE": {"source_step": "first-probe A9 route list; sgdPK/sgdPublishedDate from A9c "
                                         "(fanned orders only); floPK from A9-0 (A9a: single floPK)",
                          "ragged_rows_fixed": r1["ragged_fixed"],
                          "columns": ["floPK", "floWorkflowID", "flsOrder", "flsRequirement", "sgdPK",
                                      "seg_def", "seg_desc", "station", "sgdDesignation",
                                      "sgdPublishedDate"], "rows": route},
                "RUN_WO": {"source_step": "A9c (fanned orders only; n_ser not in probe)",
                           "columns": ["sgdPK", "n_seg", "n_seg_tagged_this_wo", "n_ser",
                                       "last_start"], "rows": run_wo},
                "RUN_ORDER": {"source_step": "(not in probe)",
                              "columns": ["floWorkflowID", "flsOrder", "n_ser", "n_seg"], "rows": []},
                "ND": {"source_step": "A9d (route_days=90)",
                       "columns": ["station", "sgdPK", "n_seg_nd", "last_start_nd"], "rows": nd},
                "COUNTS": {"source_step": "(not in probe)", "columns": ["n_ser", "n_seg"],
                           "rows": [[None, None]]}}})
    return {"wo_route": {
        "schema_id": SCHEMA_ROUTE, "golden": golden, "demo_wo": DEMO_WO,
        "_note": ("two work orders together show all four layers: WO910002/1 = unique + ran_wo (order 11) + "
                  "ambiguous (orders 16/17/21/24/32/33/38); WO910001/1 = unique + ran_wo (orders 4/8) + "
                  "ran_wo_multi (orders 5/6/7). Non-fanned orders have no sgdPK/run counts in the probe "
                  "(null, not 0).")}}


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

    def __init__(self, answers, guard=None, counts=(10, 100)):
        self.log, self.answers = [], answers
        self._last = None
        self.guard = guard or tuple(1 for _ in GUARD_KEYS)
        self.counts = counts

    def execute(self, sql, *params):
        self.log.append((sql, params))
        self._last = sql

    def fetchone(self):
        if self._last is SQL_GUARD:
            return self.guard
        if self._last is SQL_RT_COUNTS:
            return self.counts
        return self.answers[self._last]

    def fetchall(self):
        return self.answers.get(self._last, [])


def _R(order, sgd, seg_def, station="segX", req="Mandatory", pub="2025-09-04 13:36:00",
       wf="WF1/1", desc=None):
    """合成路線列（ROUTE_READ 欄序）。"""
    return (1, wf, order, req, sgd, seg_def, desc or f"desc {seg_def}", station, "Active", pub)


def _selftest():
    n = 0
    mod = sys.modules[__name__]

    def t(label, cond, detail=""):
        nonlocal n
        assert cond, f"selftest FAIL: {label}{(' — ' + str(detail)) if detail else ''}"
        n += 1

    # ---- ① SQL 文本紀律（`ALL_SQL()` 是唯一清單）--------------------------------------
    sqls = ALL_SQL()
    t("all-sql-15", len(sqls) == 15 and len(set(sqls)) == 15)
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
        # ★ 聚合函式內／GROUP BY 內不放子查詢（Msg 130／144）
        t("no-subquery-in-aggregate",
          not re.search(r"(COUNT|SUM|MAX|MIN|AVG)\s*\(\s*\(\s*SELECT", s), s[:40])
        for m in re.finditer(r"GROUP BY([^\n]*)", s):
            t("no-subquery-in-group-by", "SELECT" not in m.group(1), s[:40])
    t("temps-only-in-shell-batch",
      [s for s in sqls if re.search(r"(?<!INSERT )INTO #", s)] == [SQL_RT_TEMPS])
    t("shell-five-tables", all(f"INTO #rt_{x}\n" in SQL_RT_TEMPS
                               for x in ("pr", "ser", "seg", "route", "fan")))
    t("shell-no-temp-join", "JOIN #" not in SQL_RT_TEMPS and "FROM #" not in SQL_RT_TEMPS)
    t("shell-group-by-kept", SQL_RT_TEMPS.count("GROUP BY") == 2)
    blob = "\n".join(sqls)
    for bad in ("stpPersonID", "OperatorId", "OperatorName", "aslPersonID", "rqsRequestor",
                "PublishingAuthority", "ChangeLog", "Comment", "sgdSegmentRecipe",
                "WorkInstructionURI", "all_segment_data_value_v"):
        t(f"never-{bad}", bad not in blob)
    t("no-end-filter", "stpEndTime" not in blob)
    t("no-like", " LIKE " not in blob)
    # ★★ §7.1：永不用 sgdPublishedDate 代裁——SQL 側：只出現在欄清單，不在任何排序／比較／聚合裡
    for s in sqls:
        for m in re.finditer(r"ORDER BY[^\n]*", s):
            t("★ no-published-in-order-by", "Published" not in m.group(0), m.group(0))
        for m in re.finditer(r"(WHERE|AND|HAVING|OVER|MAX\(|MIN\()[^\n]*", s):
            t("★ no-published-in-predicate", "PublishedDate" not in m.group(0), m.group(0))
    t("★ published-only-as-column", blob.count("sgdPublishedDate") == 4
      and "floPublishedDate" not in blob and "prdPublishedDate" not in blob)
    t("head-eq-no-revision", "r.rqsProductionRequestID = ?" in SQL_RT_HEAD
      and "OR " not in SQL_RT_HEAD)
    t("head-qty-w1-provenance", "r.rqsQuantityRequested" in SQL_RT_HEAD
      and "rqsQuantityRequested" in _mm._mh.SQL_WO_HEAD)
    t("route-double-active", "w.sgdDesignation = 'Active'" in SQL_RT_ROUTE
      and "w.floDesignation = 'Active'" in SQL_RT_ROUTE and "p.prd = w.prdPK" in SQL_RT_ROUTE)
    t("route-unparameterized", "?" not in SQL_RT_ROUTE and "?" not in SQL_RT_FAN)
    t("fan-definition", "HAVING COUNT(DISTINCT r.sgdPK) > 1" in SQL_RT_FAN)
    t("ser-top-param", "SELECT TOP (?) m.matPK" in SQL_RT_SER and "GROUP BY m.matPK" in SQL_RT_SER)
    t("seg-top-param-since", "SELECT TOP (?)" in SQL_RT_SEG and "v.stpStartTime >= ?" in SQL_RT_SEG
      and "JOIN #rt_ser s ON s.matPK = v.matPK" in SQL_RT_SEG)
    t("seg-no-distinct-on-view", "DISTINCT" not in SQL_RT_SEG)
    t("run-wo-tagged-param", "CASE WHEN g.seg_board = ? THEN 1" in SQL_RT_RUN_WO
      and "WHERE g.sgdPK IN (SELECT r.sgdPK FROM #rt_route r)" in SQL_RT_RUN_WO)
    t("nd-single-class-window", "s.SegmentClassID = c.station COLLATE DATABASE_DEFAULT"
      in SQL_RT_RUN_ND and "s.stpStartTime >= ?" in SQL_RT_RUN_ND
      and "s.stpStartTime <  ?" in SQL_RT_RUN_ND and "OUTER APPLY" in SQL_RT_RUN_ND)
    t("nd-fanned-stations-only", "EXISTS (SELECT 1 FROM #rt_fan f" in SQL_RT_RUN_ND)
    t("caps", SER_CAP == 20000 and SEG_CAP == 500000 and ROUTE_DAYS == 90
      and SER_CAP == _mm._mh.SER_CAP and SEG_CAP == _mm._mh.SEG_CAP
      and SINCE_DEFAULT == _mm._mh.SINCE_DEFAULT)
    t("guard-keys", len(GUARD_KEYS) == SQL_GUARD.count("CASE WHEN")
      and all(set(v) <= set(GUARD_KEYS) for v in GUARD_NEEDS.values()))
    t("query-names-real", all(hasattr(mod, q.split(".", 1)[1])
                              for qs in TOOL_QUERY_NAMES.values() for q in qs)
      and set(TOOL_QUERY_NAMES) == set(TOOLS))
    t("query-names-in-all-sql", all(getattr(mod, q.split(".", 1)[1]) in sqls
                                    for qs in TOOL_QUERY_NAMES.values() for q in qs))
    # ★★ Python 側：derive 的排序鍵不含 published_at（原始碼檢查＋下方置換測試雙向釘）
    src = inspect.getsource(derive_wo_route)
    sort_lines = [ln for ln in src.split("\n") if "sort(" in ln or "key=lambda" in ln
                  or "_sort_desc(" in ln]
    t("★ derive-sort-keys-no-published", sort_lines and all("publish" not in ln for ln in sort_lines))
    t("★ derive-published-only-copied", src.count("published_at") == 1
      and '"published_at": _ts(r[9])' in src)

    # ---- ② 參數正規化 -----------------------------------------------------------------
    t("wo-ok", normalize_wo(" WO910001/1 ") == ("WO910001/1", None))
    t("wo-sap-ok", normalize_wo("9000100001")[1] is None)
    for bad in ("Board%", "WO910001/1'; --", "Board_1", "Board 1", "A/B/C"):
        t(f"wo-bad-{bad}", normalize_wo(bad)[1]["error"] == "bad_wo_id")
    t("wo-missing", normalize_wo("")[1]["error"] == "missing_wo_id"
      and normalize_wo(None)[1]["error"] == "missing_wo_id")
    t("norm-args-unknown-tool", normalize_args("x", "WO1")[1]["error"] == "unknown_tool")
    t("precheck-none", precheck("wo_route", "WO1") is None)
    t("blank-keys", set(blank("wo_route", "WO1")) == set(ROUTE_KEYS))

    # ---- ③ 純函式：四層各一案例（合成列）-----------------------------------------------
    head = [(1, "WO9/1", "assyModule", "2026-09-01 08:00:00", None, 1, "WF1/1", 2, "PD1/1",
             "plover", 240.0)]
    route = [
        _R(1, 10, "PR1/1", "segA"),                                            # unique
        _R(2, 20, "PR2/1", "segB", pub="2025-01-01 00:00:00"),                 # ran_wo（跑的是舊的）
        _R(2, 21, "PR2/2", "segB", pub="2026-09-01 00:00:00"),
        _R(3, 30, "PR3/1", "segC"), _R(3, 31, "PR3/2", "segC"),                # ran_wo_multi
        _R(4, 40, "PR4/1", "segD", pub="2025-01-01 00:00:00"),                 # ambiguous
        _R(4, 41, "PR4/2", "segD", pub="2026-09-01 00:00:00"),
        _R(4, 42, "PR4/3", "segD", pub="2026-01-01 00:00:00"),
        _R(1, 10, "PR1/1", "segA"),                                            # view 重複列（mdf／bil）
    ]
    run_wo = [(10, 300, 300, 300, "2026-09-02 00:00:00"), (20, 50, 40, 50, "2026-09-03 00:00:00"),
              (30, 20, 20, 20, "2026-09-04 00:00:00"), (31, 90, 90, 80, "2026-09-05 00:00:00")]
    run_order = [("WF1/1", 1, 300, 300), ("WF1/1", 2, 50, 50), ("WF1/1", 3, 95, 110)]
    nd = [("segD", 40, 900, "2026-09-20 00:00:00"), ("segD", 42, 5000, "2026-09-21 00:00:00"),
          ("segB", 20, 700, "2026-09-20 00:00:00"), ("segD", None, None, None)]
    rec = derive_wo_route("WO9/1", head, route, run_wo, run_order, nd, (240, 999),
                          nd_scanned=True, nd_window={"from": "a", "to": "b", "days": 90},
                          caps={"serials": False, "segments": False})
    t("syn-validator", ok_payload("wo_route", rec), rec)
    by = {x["order"]: x for x in rec["route"]}
    t("syn-four-orders-dedup", [x["order"] for x in rec["route"]] == [1, 2, 3, 4]
      and by[1]["n_candidates"] == 1)
    t("★ L1-unique", by[1]["resolution"] == "unique" and by[1]["chosen"]["sgd_pk"] == 10
      and by[1]["chosen"]["seg_def"] == "PR1/1" and by[1]["chosen"]["active"] is True)
    t("★ L2-ran_wo", by[2]["resolution"] == "ran_wo" and by[2]["chosen"]["sgd_pk"] == 20
      and by[2]["candidates"][0]["sgd_pk"] == 20)
    t("★ L2-ran_wo-picks-older-published", by[2]["candidates"][0]["published_at"]
      < by[2]["candidates"][1]["published_at"])
    t("★ L2b-ran_wo_multi", by[3]["resolution"] == "ran_wo_multi" and by[3]["chosen"] is None
      and [c["sgd_pk"] for c in by[3]["candidates"]] == [31, 30])
    t("★ L3-ambiguous", by[4]["resolution"] == "ambiguous" and by[4]["chosen"] is None
      and [c["sgd_pk"] for c in by[4]["candidates"]] == [42, 40, 41]
      and [c["n_seg_90d"] for c in by[4]["candidates"]] == [5000, 900, 0])
    t("L3-zero-wo", all(c["n_seg_wo"] == 0 for c in by[4]["candidates"]))
    t("L3-not-newest-published", by[4]["candidates"][0]["published_at"] != "2026-09-01 00:00:00")
    t("nm-two-unresolved", [(x["field"], x["reason"]) for x in rec["not_measurable"]]
      == [("route[3].sgd", "route_version_unresolved"), ("route[4].sgd", "route_version_unresolved")])
    t("diag-counts", {k: rec["diag"][k] for k in ("n_orders", "n_fanned", "n_unresolved",
                                                  "n_ran_wo_multi", "n_ambiguous")}
      == {"n_orders": 4, "n_fanned": 3, "n_unresolved": 2, "n_ran_wo_multi": 1, "n_ambiguous": 1})
    t("diag-cap-hit", rec["diag"]["cap_hit"] == {"serials": False, "segments": False}
      and rec["truncated"] is False)
    t("order-counts-from-run-order", (by[3]["n_serials"], by[3]["n_seg"]) == (95, 110)
      and (by[4]["n_serials"], by[4]["n_seg"]) == (0, 0))
    t("tagged-this-wo", by[2]["candidates"][0]["n_seg_tagged_this_wo"] == 40)
    t("nd-only-fanned", by[1]["candidates"][0]["n_seg_90d"] is None
      and by[3]["candidates"][0]["n_seg_90d"] == 0)
    t("wo-header", rec["work_order"] == {"rqs_id": "WO9/1", "product_class": "assyModule",
                                         "product_definition": "PD1/1", "product_line": "plover",
                                         "workflow": "WF1/1", "qty_requested": 240,
                                         "started_at": "2026-09-01 08:00:00", "completed_at": None})
    t("top-counts", (rec["n_serials"], rec["n_seg"], rec["n_orders"]) == (240, 999, 4))
    cv = rec["caveats"]
    t("cav-order-sentence", CAVEAT_ORDER in cv and "實跑順序可能不同" in CAVEAT_ORDER)
    t("cav-published", CAVEAT_PUBLISHED in cv)
    t("★ cav-top-unresolved", "路線 4 站中 2 站版本未收斂（本單走過多版 1 站、本單零紀錄 1 站）。" in cv)
    t("★ cav-ambiguous-7.1-sentence", any("第 4 站序（segD）：該站候選 3 版、本單序號在 MES 零段紀錄" in c
                                          and "不指定本單走哪一版" in c for c in cv))
    t("cav-multi", any(c.startswith("第 3 站序（segC）：本單序號確實跑過 2 個版本") for c in cv))
    t("cav-nd-scanned", any("只對有扇出的站序掃描" in c for c in cv))
    t("no-internal-keys-out", all(k not in json.dumps(rec) for k in ("rqsPK", "matPK", "stpPK")))
    # ★★ 置換測試：把發佈日全部打亂／倒過來，resolution／chosen／候選順序一個都不能變
    for variant in ("reverse", "same", "none"):
        r2 = []
        for i, r in enumerate(route):
            pub = {"reverse": f"20{99 - i:02d}-01-01 00:00:00", "same": "2025-09-04 13:36:00",
                   "none": None}[variant]
            r2.append(r[:9] + (pub,))
        rec2 = derive_wo_route("WO9/1", head, r2, run_wo, run_order, nd, (240, 999),
                               nd_scanned=True, caps={})
        t(f"★ published-permutation-invariant-{variant}",
          [(x["resolution"], (x["chosen"] or {}).get("sgd_pk"),
            [c["sgd_pk"] for c in x["candidates"]]) for x in rec2["route"]]
          == [(x["resolution"], (x["chosen"] or {}).get("sgd_pk"),
               [c["sgd_pk"] for c in x["candidates"]]) for x in rec["route"]])
    # 同分決勝＝版本號字串（非發佈日）
    tie = derive_wo_route("WO9/1", head, [_R(5, 51, "PR5/2", "segE", pub="2020-01-01 00:00:00"),
                                          _R(5, 50, "PR5/1", "segE", pub="2026-01-01 00:00:00")],
                          [], [], [("segE", 50, 10, None), ("segE", 51, 10, None)], (1, 1),
                          nd_scanned=True)
    t("tie-break-by-seg-def", [c["seg_def"] for c in tie["route"][0]["candidates"]]
      == ["PR5/1", "PR5/2"] and tie["route"][0]["resolution"] == "ambiguous")
    # ND 沒掃：ambiguous 仍成立，但 n_seg_90d null、caveat 說明
    rec_ns = derive_wo_route("WO9/1", head, route, run_wo, run_order, [], (240, 999),
                             nd_scanned=False)
    t("nd-skipped-null", all(c["n_seg_90d"] is None for x in rec_ns["route"]
                             for c in x["candidates"]) and CAVEAT_ND_SKIPPED in rec_ns["caveats"]
      and rec_ns["diag"]["nd_scanned"] is False)
    # 多站類別同站序
    ms = derive_wo_route("WO9/1", head, [_R(8, 80, "PR8/1", "segP"), _R(8, 81, "PR8/2", "segQ",
                                                                        req="Optional")],
                         [(80, 5, 5, 5, None), (81, 3, 3, 3, None)], [], [], (5, 8))
    t("multi-station-order", ms["route"][0]["station"] is None
      and ms["route"][0]["stations"] == ["segP", "segQ"]
      and ms["route"][0]["resolution"] == "ran_wo_multi"
      and any("不同站類別" in c for c in ms["caveats"]) and ok_payload("wo_route", ms))
    t("requirement-per-candidate", sorted(c["requirement"] for c in ms["route"][0]["candidates"])
      == ["Mandatory", "Optional"])
    # 多 workflow ⇒ field 帶 workflow
    mw = derive_wo_route("WO9/1", head, [_R(1, 1, "A/1", wf="WFa/1"), _R(1, 2, "A/2", wf="WFa/1"),
                                         _R(1, 3, "B/1", wf="WFb/1")], [], [], [], (0, 0))
    t("multi-workflow-field", [x["field"] for x in mw["not_measurable"]] == ["route[WFa/1:1].sgd"]
      and mw["n_orders"] == 2 and ok_payload("wo_route", mw))
    t("no-serials-caveat", CAVEAT_NO_SERIALS in mw["caveats"])
    # 截斷
    tr = derive_wo_route("WO9/1", head, route, run_wo, run_order, nd, (20000, 500000),
                         nd_scanned=True, caps={"serials": True, "segments": True})
    t("★ truncated", tr["truncated"] and any("下界" in c and "20,000" in c for c in tr["caveats"])
      and tr["diag"]["cap_hit"] == {"serials": True, "segments": True} and ok_payload("wo_route", tr))
    # wo_not_found／no_route
    nf = derive_wo_route("NOPE/1", [], [], [], [], [], None)
    t("★ wo_not_found", nf["found"] is False and nf["reason"] == "wo_not_found"
      and nf["route"] == [] and nf["not_measurable"] == [] and ok_payload("wo_route", nf)
      and NOTE_NOT_FOUND in nf["caveats"])
    nr = derive_wo_route("WO9/1", head, [], [], [], [], None)
    t("★ no_route", nr["found"] is True and nr["reason"] == "no_route" and nr["n_orders"] == 0
      and ok_payload("wo_route", nr) and NOTE_NO_ROUTE in nr["caveats"])
    # validator 反例
    bad = json.loads(json.dumps(rec))
    bad["route"][0]["chosen"] = None
    t("validator-rejects-unique-without-chosen", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(rec))
    bad["route"][3]["chosen"] = dict(bad["route"][3]["candidates"][0])
    t("validator-rejects-ambiguous-with-chosen", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(rec))
    bad["not_measurable"][0]["reason"] = "whatever"
    t("validator-rejects-open-reason", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(rec))
    bad["not_measurable"] = bad["not_measurable"][:1]
    t("validator-rejects-nm-count-mismatch", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(rec))
    bad["route"][0]["resolution"] = "latest_published"
    t("validator-rejects-open-resolution", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(rec))
    bad["diag"]["rqsPK"] = 1
    t("validator-rejects-internal-key", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(rec))
    bad["extra"] = 1
    t("validator-rejects-extra-key", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(rec))
    bad["route"][0]["candidates"][0]["description"] = "a\nb"
    t("validator-rejects-newline", not ok_payload("wo_route", bad))
    bad = json.loads(json.dumps(nf))
    bad["reason"] = "no_route"
    bad["found"] = True
    t("validator-rejects-found-without-header", not ok_payload("wo_route", bad))

    cur = _FakeCursor({}, guard=tuple(0 for _ in GUARD_KEYS))
    try:
        compute("wo_route", cur, "WO9/1")
        t("guard-raises", False)
    except SchemaDivergence as ex:
        t("guard-raises", "ABSENT" in str(ex) and cur.log == [(SQL_GUARD, ())])
    part = list(1 for _ in GUARD_KEYS)
    part[GUARD_KEYS.index("pws_sgdDesignation")] = 0
    try:
        compute("wo_route", _FakeCursor({}, guard=tuple(part)), "WO9/1")
        t("guard-raises-one-col", False)
    except SchemaDivergence as ex:
        t("guard-raises-one-col", "pws_sgdDesignation ABSENT" in str(ex))

    # golden fixture 不存在（例：脫敏公開樹不帶原始撈取 golden）⇒ 依賴 golden 的段落標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    _fx_absent = [f for f in FIXTURE_FILES.values()
                  if not os.path.exists(os.path.join(BASE, "_fixtures", f))]
    if _fx_absent:
        print("mes_route selftest: skipped: %s absent" % ", ".join(sorted(_fx_absent)))
        print(f"mes_route selftest: {n} assertions PASS")
        return 0

    # ---- ④ fixture：兩張工單走同一支 derive_*（demo 與 live 同形）----------------------
    fx = _fixture("wo_route")
    t("fx-schema-id", fx["schema_id"] == SCHEMA_ROUTE and fx["demo_wo"] == DEMO_WO)
    t("fx-builder-reproducible", build_fixtures() == {"wo_route": fx})
    gs = {g["key"]: g for g in fx["golden"]}
    t("fx-two-wos", sorted(gs) == ["WO910002/1", "WO910001/1"])
    for k, g in gs.items():
        for st in g["steps"].values():
            t(f"fx-cols-{k}", all(len(row) == len(st["columns"]) for row in st["rows"]))
        t(f"fx-provenance-{k}", g["source_probe"] == PROBE_SQL and g["captured_at"] == CAPTURED_AT)
    t("fx-ragged-desc-tab-fixed", gs["WO910001/1"]["steps"]["ROUTE"]["ragged_rows_fixed"] == 1)
    demo = {k: demo_lookup("wo_route", fx, k) for k in gs}
    all_res = set()
    for k, v in demo.items():
        t(f"fx-{k}-valid", v["found"] and ok_payload("wo_route", v), k)
        res = sorted({x["resolution"] for x in v["route"]})
        t(f"★ fx-{k}-demonstrates", res == sorted(gs[k]["demonstrates"]), res)
        a = gs[k]["a9a_check"]
        t(f"★ fx-{k}-A9a-orders", v["n_orders"] == a["n_order"]
          and v["diag"]["n_fanned"] == a["n_fanned_order"]
          and sum(x["n_candidates"] for x in v["route"]) == a["n_sgd"])
        t(f"fx-{k}-demo-caveat", _DEMO_CAVEAT in v["caveats"])
        all_res |= set(res)
    t("★ fx-four-layers-covered", all_res == set(RESOLUTIONS))
    d63 = {x["order"]: x for x in demo["WO910002/1"]["route"]}
    d90 = {x["order"]: x for x in demo["WO910001/1"]["route"]}
    t("★ fx-63-ambiguous-orders", sorted(o for o, x in d63.items() if x["resolution"] == "ambiguous")
      == [16, 17, 21, 24, 32, 33, 38])
    t("★ fx-63-o11-ran_wo", d63[11]["resolution"] == "ran_wo"
      and d63[11]["chosen"]["seg_def"] == "QR910003/4" and d63[11]["candidates"][0]["n_seg_wo"] == 214)
    t("★ fx-63-o16-83-first-not-99", [c["seg_def"] for c in d63[16]["candidates"]][:3]
      == ["QR910005/83", "QR910005/82", "QR910005/81"]
      and d63[16]["candidates"][0]["n_seg_90d"] == int(next(
          r[2] for r in gs["WO910002/1"]["steps"]["ND"]["rows"]
          if r[1] == str(d63[16]["candidates"][0]["sgd_pk"])))
      and d63[16]["candidates"][-1]["n_seg_90d"] == 0)
    newest16 = max((c for c in d63[16]["candidates"]), key=lambda c: c["published_at"])
    t("★ fx-63-o16-newest-published-has-zero-runs", newest16["seg_def"] == "QR910005/99"
      and newest16["n_seg_90d"] == 0)
    t("★ fx-63-o21-14-first（最舊的在跑）", d63[21]["candidates"][0]["seg_def"] == "QR910004/14"
      and d63[21]["candidates"][0]["n_seg_90d"] == 9120)
    # §7.1 論點：扇出站若以發佈日最新代裁，有 5 個會指到零跑量版本
    zero_newest = 0
    for x in d63.values():
        if x["n_candidates"] > 1:
            nw = max(x["candidates"], key=lambda c: c["published_at"])
            zero_newest += (nw["n_seg_90d"] == 0)
    t("★ fx-63-newest-published-zero-run-5-of-8", zero_newest == 5
      and sum(1 for x in d63.values() if x["n_candidates"] > 1) == 8)
    t("fx-63-o17-two-stations", d63[17]["stations"] == ["segPreCureAlignCheck",
                                                         "segPreCureHeightCheck"])
    t("★ fx-90-multi-orders", sorted(o for o, x in d90.items() if x["resolution"] == "ran_wo_multi")
      == [5, 6, 7] and sorted(o for o, x in d90.items() if x["resolution"] == "ran_wo") == [4, 8])
    t("★ fx-90-o5-by-wo-seg", [(c["seg_def"], c["n_seg_wo"]) for c in d90[5]["candidates"][:2]]
      == [("QR910006/29", 88), ("QR910007/29", 41)])
    t("fx-90-o6-by-wo-seg", [c["n_seg_wo"] for c in d90[6]["candidates"]] == [64, 47, 0])
    t("fx-90-o4-chosen", d90[4]["chosen"]["seg_def"] == "QR910001/12")
    t("fx-90-o8-desc-tab-cleaned", any(c["description"] == "Fluid Clean and E-Test (Gen B)-Heron"
                                       for c in d90[8]["candidates"]))
    t("fx-90-unique-null-counts", d90[1]["resolution"] == "unique"
      and d90[1]["chosen"]["sgd_pk"] is None and d90[1]["n_seg"] is None
      and d90[1]["candidates"][0]["n_seg_wo"] is None)
    t("fx-90-fanned-order-seg-sum", d90[5]["n_seg"] == 129 and d90[5]["n_serials"] is None)
    t("fx-90-station-sequence", [d90[o]["station"] for o in (1, 3, 11)]
      == ["segPlasmaActivation", "segFinalAssembly", "segDieArrayTaping"])
    t("fx-90-nm", [x["field"] for x in demo["WO910001/1"]["not_measurable"]]
      == ["route[5].sgd", "route[6].sgd", "route[7].sgd"])
    t("fx-63-top-caveat", "路線 34 站中 7 站版本未收斂（本單走過多版 0 站、本單零紀錄 7 站）。"
      in demo["WO910002/1"]["caveats"])
    t("fx-header", demo["WO910001/1"]["work_order"]["product_line"] == "heron"
      and demo["WO910001/1"]["work_order"]["workflow"] == "FW910001/49"
      and demo["WO910001/1"]["work_order"]["qty_requested"] is None)
    t("fx-miss-none", demo_lookup("wo_route", fx, "NOPE/1") is None)
    t("fx-case-insensitive", demo_lookup("wo_route", fx, "wo910001/1") is not None)

    # ---- ⑤ 編排（假游標）：順序、臨時表只在非參數化批次建、參數 ----------------------
    now = datetime(2026, 8, 14, 12, 0, 0)
    ans = {SQL_RT_HEAD_READ: head, SQL_RT_ROUTE_READ: route, SQL_RT_RUN_WO: run_wo,
           SQL_RT_RUN_ORDER: run_order, SQL_RT_RUN_ND: nd}
    cur = _FakeCursor(ans, counts=(240, 999))
    rc = compute_wo_route(cur, "WO9/1", now=now)
    t("★ cmp-order", [s for s, _ in cur.log] == [
        SQL_GUARD, SQL_RT_TEMPS, SQL_RT_HEAD, SQL_RT_HEAD_READ, SQL_RT_ROUTE, SQL_RT_ROUTE_READ,
        SQL_RT_FAN, SQL_RT_SER, SQL_RT_SER_INDEX, SQL_RT_SEG, SQL_RT_SEG_INDEX, SQL_RT_COUNTS,
        SQL_RT_RUN_WO, SQL_RT_RUN_ORDER, SQL_RT_RUN_ND])
    lg = dict((s, p) for s, p in cur.log)
    t("cmp-params-head", lg[SQL_RT_HEAD] == ("WO9/1",))
    t("cmp-params-caps", lg[SQL_RT_SER] == (SER_CAP,) and lg[SQL_RT_SEG] == (SEG_CAP, SINCE_DEFAULT))
    t("cmp-params-run-wo", lg[SQL_RT_RUN_WO] == ("WO9/1",))
    t("★ cmp-params-nd-90d", lg[SQL_RT_RUN_ND] == ("2026-06-26 12:00:00", "2026-08-14 12:00:00"))
    t("cmp-unparam-batches", all(p == () for s, p in cur.log
                                 if s in (SQL_RT_TEMPS, SQL_RT_ROUTE, SQL_RT_FAN, SQL_RT_SER_INDEX,
                                          SQL_RT_SEG_INDEX, SQL_RT_COUNTS, SQL_RT_RUN_ORDER)))
    t("cmp-valid", ok_payload("wo_route", rc) and rc["diag"]["nd_scanned"]
      and rc["diag"]["nd_window"]["days"] == 90 and rc["n_serials"] == 240)
    t("cmp-same-as-derive", [x["resolution"] for x in rc["route"]]
      == ["unique", "ran_wo", "ran_wo_multi", "ambiguous"])
    # 沒有 ambiguous ⇒ 不送 A9d
    cur = _FakeCursor(dict(ans, **{SQL_RT_ROUTE_READ: route[:5]}))
    rn = compute_wo_route(cur, "WO9/1", now=now)
    t("★ cmp-no-ambiguous-skips-nd", SQL_RT_RUN_ND not in [s for s, _ in cur.log]
      and rn["diag"]["nd_scanned"] is False and ok_payload("wo_route", rn))
    # 查無工單 ⇒ 停在讀頭
    cur = _FakeCursor({SQL_RT_HEAD_READ: []})
    rm = compute_wo_route(cur, "NOPE/1", now=now)
    t("cmp-not-found-stops", [s for s, _ in cur.log][-1] is SQL_RT_HEAD_READ
      and rm["reason"] == "wo_not_found")
    # 無路線 ⇒ 不掃序號／段
    cur = _FakeCursor({SQL_RT_HEAD_READ: head, SQL_RT_ROUTE_READ: []})
    rr = compute_wo_route(cur, "WO9/1", now=now)
    t("★ cmp-no-route-skips-seg", [s for s, _ in cur.log][-1] is SQL_RT_ROUTE_READ
      and SQL_RT_SEG not in [s for s, _ in cur.log] and rr["reason"] == "no_route")
    # 上限撞到
    cur = _FakeCursor(ans, counts=(SER_CAP, 12))
    rt = compute_wo_route(cur, "WO9/1", now=now)
    t("cmp-cap-hit", rt["truncated"] and rt["diag"]["cap_hit"] == {"serials": True, "segments": False})
    cur = _FakeCursor(ans, counts=(5, SEG_CAP))
    t("cmp-cap-hit-seg", compute_wo_route(cur, "WO9/1", now=now)["diag"]["cap_hit"]["segments"])
    t("compute-dispatch", compute("wo_route", _FakeCursor(ans), "WO9/1")["found"])
    print(f"mes_route selftest: {n} assertions PASS")
    return 0


def _demo_cli(argv):
    key, err = normalize_wo(argv[0])
    if err:
        print(json.dumps(err, ensure_ascii=False))
        return 2
    out = demo_lookup("wo_route", _fixture("wo_route"), key)
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
    print("usage: python mes_route.py --selftest | --sql | --build-fixtures | --demo <wo_id>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
