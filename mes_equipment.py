#!/usr/bin/env python3
"""Tendline Board — Hermes 的**設備事件／父子槽位／校正歷史**記錄級 MES 查詢核心（MES 工具包②·包 1）。

**這是什麼**：三支唯讀工具的資料腿（`board_mcp` 消費；runtime **沒有 LLM 參與**——LLM 只叫工具，
工具跑**固定審過的參數化 SQL**，`SPEC-HERMES-TRUST.md` §7.6.1）：
  · `equipment_status_events(eid, days)` ——某台機近 N 天的 MES 錯誤／狀態事件
                                          （`board.equipment_status_events.v1`；探針 D4a／D4b／D0d／Dx）。
  · `equipment_modules(eid)`             ——父子槽位邊（`board.equipment_modules.v1`；探針 D6）。
  · `equipment_calibration(eid, days)`   ——校正歷史（`board.equipment_calibration.v1`；探針 I4／Dx）。

**不是什麼**：不是停機時間（`/downtime`、`equipment_downtime`）、不是稼動（`mes_timeline`，凍結、零 import）、
不是 cmms 工單（維修／PM 住 cmms）。MES 事件是**時點**、沒有持續時間；事件 ≠ 停機。

**唯一的 SQL 來源＝`_PROBE-ASSISTANT-MES-FAMILY-2-20260923.sql`**（2026-09-23 Chris SSMS 實跑、38 結果集零錯誤；
判讀＝`_PROBE-ASSISTANT-MES-FAMILY-2-RESULT-DIGEST-20260923.md` §3：兩個 join 假設
`equipment_error_log_t.vdfPK→production_data_definition_t.vdfPK`、`mes.EquipmentFailureEvent.EquipmentId = astPK`
都已自揭證實）。每條語句**參數化後沿用**該步的欄位／join；偏離逐條寫在下表與該常數上方。
唯一不在第二支探針 SELECT 過的欄＝`production_data_definition_t.FailureDescription`（出處＝第三支探針 F0，
`mes_etest.SQL_FP_DICT` 已 live 用）。

═══ 對照探針的刻意偏離（理由寫在各常數上方）═══════════════════════════════════════════
  Q1 **三台 → 單台、EID 只解析一次**：探針以 `VALUES (@eid1..@eid3)`＋逐台 `OUTER APPLY` 跑三台，且每步都
     `JOIN equipment_t e ON e.astEquipmentID = v.eid`；工具先以 `SQL_EQ_MASTER`（Dx＋D1 的形）把 EID 解成
     `astPK`，其後各步改 `astPK = ?`（Dx 證 astEquipmentID→astPK 1:1；少一次 join、語意等價）。
  Q2 **自由文字欄一律不取**：D4a 的 `comment_head`（`aslComment`）、D4b 的 `comment_head`（`Comment`）刪除；
     事件標籤改取**字典表**定義名 `vdf.FailureDescription`＋類別 `vdf.vdfClassID`（D0c 的 `code_class`）。
  Q3 **D4a 逐列**：`TOP (@topn_each)`＝60 → `TOP (?)`＝`EV_CAP` 200；排序補 `aslPK DESC` 決勝（D0c 取過）。
  Q4 **事件彙總新增一條**（`SQL_EV_SUMMARY`）＝D0d 的 `COUNT/MIN/MAX … GROUP BY` 形改單機、分組鍵換成
     碼×類別×定義名×狀態、**零 TOP**（全窗計數，逐列清單才有上限）。
  Q5 **rep 源只取筆數**（Dx `n_rep_fail_ev` 的形）當兩源對帳：D4b 與 D4a 逐列 1:1 同時同碼（探針兩源對帳全數一致）
     ⇒ 兩源逐列都取＝雙算。rep 的 `OperatorName`／`Comment` 當然也不取。
  Q6 **D6**：刪 `rel` CASE（Python 以 EID 判角色）、刪 `VALUES`／`JOIN me`（Q1）、加兩個
     `LEFT JOIN dbo.equipment_v` 取父／子類別 `ascEquipmentClassID`（欄與 `astPK` 出處＝D1）；
     `TOP (@topn)`＝200 保留成參數 `MOD_CAP`。
  Q7 **I4**：`TOP (5)` → `TOP (?)`＝`CAL_CAP` 20，加窗 `ashDateUpdated >= ? AND < ?`、補 `ashPK DESC` 決勝；
     `cal_head`（`LEFT(…,1200)`＋`REPLACE`）改取**整欄** `ashCalibration`——**只進 Python 解析、原文永不輸出**
     （原文含 SVN `$Id$` 帳號、`Created by` 主機名＝§24 風險）；輸出只有白名單欄（項目／結果／到期／上次校正／參考件 EID）。
  Q8 **校正計數**（`SQL_CAL_COUNT`）＝Dx `n_cal_rows` 的形＋窗內計數（`SUM(CASE…)`）＋`MAX(ashDateUpdated)`。
  Q9 **窗外最近一筆**（`SQL_CAL_LAST`）＝I4 形 `TOP (1)`、條件 `< 窗起點`；只在窗內零筆時送。
  Q10 **零臨時表**：本檔每步都是單表／小 join 的直接 SELECT ⇒ 不需要 `#temp`（selftest 釘：全文零 `INTO #`、
     零 `CREATE`／`DROP`；日後若要加，照 `mes_etest` E1：非參數化批次建 `WHERE 1=0` 空殼、帶參數只准 `INSERT INTO #x`）。

═══ 欄位出處（[[ssms-sql-column-provenance]]）═════════════════════════════════════════════
  | 物件 | 本檔取的欄 | 探針步驟 |
  |---|---|---|
  | `dbo.equipment_t` | `astPK`／`astEquipmentID`／`astDescription` | Dx／D6 |
  | `dbo.equipment_v` | `astPK`／`astStatus`／`astDesignation`／`ascEquipmentClassID`／`SegmentClassID`／`astLastCalibrationDate`／`astCalibrationDueDate` | D1 |
  | `dbo.equipment_error_log_t` | `aslPK`／`astPK`／`vdfPK`／`aslDate`／`astStatus` | D0c／D0d／D4a |
  | `dbo.production_data_definition_t` | `vdfPK`／`vdfProductionDataID`／`vdfClassID`／`FailureDescription` | D0c／D4a（desc＝第三支 F0） |
  | `mes.EquipmentFailureEvent` | `EquipmentId`／`EventTime` | Dx／D4b |
  | `dbo.dependent-equipment export` | `astPK_parent`／`astPK_dependent`／`eqeDateUpdated` | D6／Dx |
  | `dbo.equipment_history_t` | `ashPK`／`astPK`／`ashDateUpdated`／`ashCalibration` | I4／Dx |

  **絕對不 SELECT 的欄**（`ALL_SQL()` 全文負斷言）：人名（`aslPersonID`／`OperatorName`／`OperatorId`／
  `ashCalibrationAuthority`／`stpPersonID`／`rqsRequestor`）、任何 `Comment`（`aslComment`／`Comment`／`ashComments`）、
  `ashSoftware`／`ashConfiguration`、`stpEndTime`、`LEFT(`。

★ perf：每條以 `OPTION (RECOMPILE)` 收尾；事件／校正都帶 `astPK = ?`＋時間窗（`>= ?` 且 `< ?`，上界＝現在）。
★ §24：零人名欄。★ 唯讀：零 DDL/DML（連 tempdb 都不寫）。★ 凍結模組（`mes_timeline`／`mes_yield`）零 import。

CLI:
    python mes_equipment.py --selftest         # 純離線（讀 _fixtures/ 三份 golden ＋ 合成列），零 MES
    python mes_equipment.py --sql              # 只**印**本模組會送出的全部 SQL（不執行）
    python mes_equipment.py --build-fixtures   # 由探針 TSV 重建三份 fixture（純離線讀檔；fixture 不可手改）
    python mes_equipment.py --demo <tool> <eid> [days]   # 用 fixture 跑 demo（純離線）
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import mes_material as _mm                                 # 純常數／純函式重用（連線、_s/_int/_ts、_dump）
from mes_material import SchemaDivergence                  # noqa: F401  （連線單一出處＝mes_qlik，經 mes_material）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # pragma: no cover
    pass

BASE = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# 1. 人工審定常數
# =============================================================================
SCHEMA_EVENTS = "board.equipment_status_events.v1"
SCHEMA_MODULES = "board.equipment_modules.v1"
SCHEMA_CAL = "board.equipment_calibration.v1"
SCHEMA_BY_TOOL = {"equipment_status_events": SCHEMA_EVENTS,
                  "equipment_modules": SCHEMA_MODULES,
                  "equipment_calibration": SCHEMA_CAL}

TOOLS = ("equipment_status_events", "equipment_modules", "equipment_calibration")

QUERY_TIMEOUT_SEC = 180

#: 窗（天）：1..365、預設 30（超界拒絕、不 clamp）。探針事件窗＝365 天（`@ev_days`）。
DAYS_DEFAULT = 30
DAYS_MAX = 365

#: 列上限（回傳標 `truncated`）。
EV_CAP = 200          # 事件逐列（探針 D4a 每台 60；彙總不受限）
MOD_CAP = 200         # 父子邊（探針 D6 `@topn`＝200；Dx：07905 共 38 列）
CAL_CAP = 20          # 校正記錄（探針 I4 每台 5；Leak Test Nest 一天一筆 ⇒ 365 天約 365 筆）
CAL_ITEMS_CAP = 60    # 單筆校正記錄解析出的項目上限（07905 的 scripter 報告數十列量測）

#: EID 正規化（同 `board_mcp._norm_eid` 的語意：數字補零 5 碼、>5 碼保留原長、垃圾 → 拒絕）。
EID_CANON_RE = re.compile(r"^EID-\d{5,}$")

#: 回傳鍵集合（**查不到也長同一個形**；validator 與 smoke 以此為準）。
EQUIP_KEYS = ("eid", "name", "status", "designation", "class", "home_station")
EV_KEYS = ("eid", "found", "equipment", "window", "n_events", "n_events_rep", "events", "by_code",
           "by_status", "not_measurable", "truncated", "caveats", "reason", "unavailable_reason",
           "diag")
MOD_KEYS = ("eid", "found", "equipment", "parents", "children", "n_edges", "n_parents",
            "n_children", "latest_config_at", "not_measurable", "truncated", "caveats", "reason",
            "unavailable_reason", "diag")
CAL_KEYS = ("eid", "found", "equipment", "window", "n_records", "n_records_in_window",
            "last_calibrated_at", "registered", "records", "last_before_window", "not_measurable",
            "truncated", "caveats", "reason", "unavailable_reason", "diag")
KEYS_BY_TOOL = {"equipment_status_events": EV_KEYS, "equipment_modules": MOD_KEYS,
                "equipment_calibration": CAL_KEYS}
EVENT_ROW_KEYS = ("at", "source_table", "code", "category", "label", "status_after", "duration_s")
BY_CODE_KEYS = ("code", "category", "label", "n", "first_at", "last_at")
LINK_KEYS = ("eid", "name", "class", "n_edge_rows", "first_linked_at", "last_linked_at",
             "in_latest_config")
CAL_RECORD_KEYS = ("recorded_at", "format", "overall_result", "next_due", "n_items", "items",
                   "items_truncated", "payload_len", "payload_empty")
CAL_ITEM_KEYS = ("item", "result", "value", "min", "max", "last_calibrated", "due", "ref_eid")
CAL_FORMATS = ("ini_sections", "scripter_report", "empty", "unrecognized")

#: 「MES 裡真的沒有」的閉集（≠ `unavailable_reason`＝我們取不到）。EID 存在（`found=true`）也可帶。
REASONS = ("no_events_in_window", "no_module_edges", "no_calibration_records",
           "no_calibration_in_window")
#: `not_measurable` 理由閉集。
NOT_MEASURABLE_REASONS = ("eid_not_found", "calibration_payload_empty")

_TS_FMT = "%Y-%m-%d %H:%M:%S"

# =============================================================================
# 2. I/O 層：SQL 文本（全部模組常數＝離線可斷言；唯讀）
# =============================================================================
#: G0 物件／欄守衛。缺 ⇒ `SchemaDivergence`（停下、不回全零板）。每支工具只看自己要的那幾格。
SQL_GUARD = (
    "SELECT CASE WHEN OBJECT_ID('dbo.equipment_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.equipment_v') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.equipment_error_log_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.production_data_definition_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('mes.EquipmentFailureEvent') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.dependent-equipment export') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN OBJECT_ID('dbo.equipment_history_t') IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.equipment_error_log_t','vdfPK')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('mes.EquipmentFailureEvent','EquipmentId')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.dependent-equipment export','eqeDateUpdated')"
    " IS NULL THEN 0 ELSE 1 END,\n"
    "       CASE WHEN COL_LENGTH('dbo.equipment_history_t','ashCalibration')"
    " IS NULL THEN 0 ELSE 1 END\n"
    "OPTION (RECOMPILE)")
GUARD_KEYS = ("tbx_ast", "vw_ast", "tbx_asl", "tbx_vdf", "rep_fail", "tbx_dep", "tbx_ash",
              "asl_vdfPK", "rep_EquipmentId", "dep_eqeDateUpdated", "ash_ashCalibration")
_EQ2 = ("tbx_ast", "vw_ast")
GUARD_NEEDS = {
    "equipment_status_events": _EQ2 + ("tbx_asl", "tbx_vdf", "rep_fail", "asl_vdfPK",
                                       "rep_EquipmentId"),
    "equipment_modules": _EQ2 + ("tbx_dep", "dep_eqeDateUpdated"),
    "equipment_calibration": _EQ2 + ("tbx_ash", "ash_ashCalibration"),
}

#: 設備主檔（Q1／Q10）：Dx 的 `equipment_t` 查 EID（探針 :494-507）＋ D1 的 `equipment_v` 欄（探針 :513-521），
#: 以 `astPK` LEFT JOIN（D1 取過 `e.astPK`）。登記校正欄＝§7.6.6 實質棄用、只當登記值回。
SQL_EQ_MASTER = (
    "SELECT e.astPK, e.astEquipmentID, e.astDescription, v.astStatus, v.astDesignation,\n"
    "       ast_class = v.ascEquipmentClassID, home_station = v.SegmentClassID,\n"
    "       reg_last_cal = v.astLastCalibrationDate, reg_cal_due = v.astCalibrationDueDate\n"
    "FROM dbo.equipment_t AS e\n"
    "LEFT JOIN dbo.equipment_v AS v ON v.astPK = e.astPK\n"
    "WHERE e.astEquipmentID = ?\n"
    "OPTION (RECOMPILE)")

# ── equipment_status_events（D0d 彙總形 → D4a 逐列 → Dx rep 筆數）────────────────────────
#: Q4：D0d（探針 :447-457）改單機、分組鍵換成碼×類別×定義名×狀態、零 TOP；vdf LEFT JOIN＝D0c／D4a 同形。
SQL_EV_SUMMARY = (
    "SELECT vdf.vdfProductionDataID, vdf.vdfClassID, vdf.FailureDescription,\n"
    "       status_after = eel.astStatus,\n"
    "       n_event = COUNT(*), first_event = MIN(eel.aslDate), last_event = MAX(eel.aslDate)\n"
    "FROM dbo.equipment_error_log_t AS eel\n"
    "LEFT JOIN dbo.production_data_definition_t AS vdf ON vdf.vdfPK = eel.vdfPK\n"
    "WHERE eel.astPK = ?\n"
    "  AND eel.aslDate >= ? AND eel.aslDate < ?\n"
    "GROUP BY vdf.vdfProductionDataID, vdf.vdfClassID, vdf.FailureDescription, eel.astStatus\n"
    "ORDER BY n_event DESC\n"
    "OPTION (RECOMPILE)")

#: D4a（探針 :578-594）：Q1 單機 `astPK = ?`、Q2 刪 `comment_head`＋加字典定義名／類別、Q3 `TOP (?)`＋`aslPK` 決勝。
SQL_EV_ROWS = (
    "SELECT TOP (?) eel.aslDate, status_after = eel.astStatus,\n"
    "       vdf.vdfProductionDataID, vdf.vdfClassID, vdf.FailureDescription\n"
    "FROM dbo.equipment_error_log_t AS eel\n"
    "LEFT JOIN dbo.production_data_definition_t AS vdf ON vdf.vdfPK = eel.vdfPK\n"
    "WHERE eel.astPK = ?\n"
    "  AND eel.aslDate >= ? AND eel.aslDate < ?\n"
    "ORDER BY eel.aslDate DESC, eel.aslPK DESC\n"
    "OPTION (RECOMPILE)")

#: Q5：Dx 的 `n_rep_fail_ev` 子查詢（探針 :503-504）提成獨立一條；rep 源只取筆數當兩源對帳（D4b :598-614 的窗與 join 鍵）。
SQL_EV_REP_COUNT = (
    "SELECT n_rep_fail_ev = COUNT(*)\n"
    "FROM mes.EquipmentFailureEvent AS f\n"
    "WHERE f.EquipmentId = ?\n"
    "  AND f.EventTime >= ? AND f.EventTime < ?\n"
    "OPTION (RECOMPILE)")

# ── equipment_modules（D6）────────────────────────────────────────────────────────────
#: D6（探針 :646-658）：Q1 `astPK = ?`（兩端各一個參數）、Q6 刪 `rel` CASE、加父／子類別（equipment_v，D1 欄）。
SQL_MOD_EDGES = (
    "SELECT TOP (?) parent_eid = pa.astEquipmentID, parent_desc = pa.astDescription,\n"
    "       child_eid = ch.astEquipmentID, child_desc = ch.astDescription,\n"
    "       parent_class = pv.ascEquipmentClassID, child_class = cv.ascEquipmentClassID,\n"
    "       d.eqeDateUpdated\n"
    "FROM dbo.dependent-equipment export AS d\n"
    "LEFT JOIN dbo.equipment_t AS pa ON pa.astPK = d.astPK_parent\n"
    "LEFT JOIN dbo.equipment_t AS ch ON ch.astPK = d.astPK_dependent\n"
    "LEFT JOIN dbo.equipment_v AS pv ON pv.astPK = d.astPK_parent\n"
    "LEFT JOIN dbo.equipment_v AS cv ON cv.astPK = d.astPK_dependent\n"
    "WHERE d.astPK_parent = ? OR d.astPK_dependent = ?\n"
    "ORDER BY d.eqeDateUpdated DESC\n"
    "OPTION (RECOMPILE)")

# ── equipment_calibration（Dx 計數 → I4 逐列 → I4 窗外最近一筆）──────────────────────────
_CAL_HAS = ("  AND eh.ashCalibration IS NOT NULL AND DATALENGTH(eh.ashCalibration) > 0\n")
#: Q8：Dx `n_cal_rows`（探針 :501-502）＋窗內計數＋最近一筆時間；上界＝現在（§21.1 髒日期）。
SQL_CAL_COUNT = (
    "SELECT n_all = COUNT(*),\n"
    "       n_win = SUM(CASE WHEN eh.ashDateUpdated >= ? THEN 1 ELSE 0 END),\n"
    "       last_at = MAX(eh.ashDateUpdated)\n"
    "FROM dbo.equipment_history_t AS eh\n"
    "WHERE eh.astPK = ?\n" + _CAL_HAS +
    "  AND eh.ashDateUpdated < ?\n"
    "OPTION (RECOMPILE)")
_CAL_COLS = ("SELECT TOP (?) eh.ashDateUpdated, cal_len = DATALENGTH(eh.ashCalibration),"
             " eh.ashCalibration\n"
             "FROM dbo.equipment_history_t AS eh\n"
             "WHERE eh.astPK = ?\n" + _CAL_HAS)
#: Q7：I4（探針 :664-676）單機、`TOP (?)`、加窗、整欄 `ashCalibration`（只進 Python 解析、原文不輸出）。
SQL_CAL_ROWS = (
    _CAL_COLS +
    "  AND eh.ashDateUpdated >= ? AND eh.ashDateUpdated < ?\n"
    "ORDER BY eh.ashDateUpdated DESC, eh.ashPK DESC\n"
    "OPTION (RECOMPILE)")
#: Q9：I4 形、窗內零筆時才送：窗起點之前最近一筆（`TOP (?)`＝1）。
SQL_CAL_LAST = (
    _CAL_COLS +
    "  AND eh.ashDateUpdated < ?\n"
    "ORDER BY eh.ashDateUpdated DESC, eh.ashPK DESC\n"
    "OPTION (RECOMPILE)")


def ALL_SQL():
    """本模組會送出的**全部 SQL**（selftest 負斷言的唯一清單；新增查詢必須登記在這裡）。"""
    return (SQL_GUARD, SQL_EQ_MASTER,
            SQL_EV_SUMMARY, SQL_EV_ROWS, SQL_EV_REP_COUNT,
            SQL_MOD_EDGES,
            SQL_CAL_COUNT, SQL_CAL_ROWS, SQL_CAL_LAST)


#: `board_mcp._TOOL_QUERIES` 用的真名（grep 得到）。
TOOL_QUERY_NAMES = {
    "equipment_status_events": ("mes_equipment.SQL_EQ_MASTER", "mes_equipment.SQL_EV_SUMMARY",
                                "mes_equipment.SQL_EV_ROWS", "mes_equipment.SQL_EV_REP_COUNT"),
    "equipment_modules": ("mes_equipment.SQL_EQ_MASTER", "mes_equipment.SQL_MOD_EDGES"),
    "equipment_calibration": ("mes_equipment.SQL_EQ_MASTER", "mes_equipment.SQL_CAL_COUNT",
                              "mes_equipment.SQL_CAL_ROWS", "mes_equipment.SQL_CAL_LAST"),
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
    if isinstance(v, str) and len(v) > 26 and v[19:20] == ".":
        v = v[:26]                                   # SSMS 7 位小數秒 → %f 只收 6 位
    return _mm._mh._dt(v)


def _fmt(v):
    return v.strftime(_TS_FMT) if isinstance(v, datetime) else v


def normalize_eid(raw):
    """任意輸入 → `('EID-#####', None)` 或 `(None, err)`。語意同 `board_mcp._norm_eid`：
    接受 `07905`／`7905`／`eid-07905`／`EID07905`（大小寫、空白容忍）；數字部位非純數字 ⇒ `bad_eid`（絕不猜）。"""
    hint = "eid looks like EID-07905 (or 07905)"
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, {"error": "missing_eid", "hint": hint}
    if not isinstance(raw, (str, int)) or isinstance(raw, bool):
        return None, {"error": "bad_eid", "given": raw, "hint": hint}
    s = str(raw).strip().upper().replace(" ", "")
    if s.startswith("EID-"):
        s = s[4:]
    elif s.startswith("EID"):
        s = s[3:]
    if not s.isdigit() or len(s) > 8:
        return None, {"error": "bad_eid", "given": raw, "hint": hint}
    return "EID-" + s.zfill(5), None


def normalize_days(days):
    d = DAYS_DEFAULT if days in (None, "") else _int(days)
    if d is None or not (1 <= d <= DAYS_MAX):
        return None, {"error": "bad_days", "given": days,
                      "hint": f"days must be an integer 1..{DAYS_MAX} (refused, not clamped)"}
    return d, None


def normalize_args(tool, eid=None, days=None):
    """工具參數 → `(key, err)`。events／calibration：`{"eid", "days"}`；modules：`"EID-#####"`。"""
    e, err = normalize_eid(eid)
    if err:
        return None, err
    if tool == "equipment_modules":
        return e, None
    if tool not in TOOLS:
        return None, {"error": "unknown_tool", "given": tool}
    d, err = normalize_days(days)
    if err:
        return None, err
    return {"eid": e, "days": d}, None


def _key_eid(key):
    return key.get("eid") if isinstance(key, dict) else key


def _key_days(key):
    return key.get("days") if isinstance(key, dict) else None


# ---- 骨架（鍵集合固定；查不到也長同一個形）--------------------------------------
def blank(tool, key, *, found=False, reason=None, unavailable_reason=None, diag=None):
    d = dict(diag or {})
    eid = _key_eid(key)
    common = {"eid": eid, "found": found, "equipment": None, "not_measurable": [],
              "truncated": False, "caveats": [], "reason": reason,
              "unavailable_reason": unavailable_reason, "diag": d}
    if tool == "equipment_status_events":
        return {**common, "window": {"from": None, "to": None, "days": _key_days(key)},
                "n_events": None, "n_events_rep": None, "events": [], "by_code": [],
                "by_status": {}}
    if tool == "equipment_modules":
        return {**common, "parents": [], "children": [], "n_edges": None, "n_parents": None,
                "n_children": None, "latest_config_at": None}
    if tool == "equipment_calibration":
        return {**common, "window": {"from": None, "to": None, "days": _key_days(key)},
                "n_records": None, "n_records_in_window": None, "last_calibrated_at": None,
                "registered": None, "records": [], "last_before_window": None}
    raise KeyError(tool)


def precheck(tool, key):
    """不需 MES 就能下結論的情況 → 完整 payload；否則 None。三支設備工具目前**沒有**這種情況
    （EID 存不存在要問 MES）；保留介面＝`board_data.get_mes_equipment` 與 `mes_etest` 同形。"""
    if tool not in TOOLS:
        raise KeyError(tool)
    return None


def equipment_block(m):
    """`SQL_EQ_MASTER` 一列（astPK, eid, desc, status, designation, class, home, reg_last, reg_due）→ 輸出區塊。
    `astPK` 是內部鍵、**不出輸出**。"""
    return {"eid": _s(m[1]), "name": _s(m[2]), "status": _s(m[3]), "designation": _s(m[4]),
            "class": _s(m[5]), "home_station": _s(m[6])}


_NM_EID = ("MES 設備主檔（equipment_t）查無這個 EID：可能打錯、或它不是 MES 登記的設備"
           "（工具只收 MES 的 EID，不收 cmms 資產號）。")


def eid_not_found(tool, key, diag=None):
    out = blank(tool, key, found=False, diag=diag)
    out["not_measurable"] = [{"field": "eid", "reason": "eid_not_found", "note": _NM_EID}]
    return out


_CAV_MES_ONLY = "只看 MES（事件表）；機台本身的 EventLog／通訊紀錄未採集 ⇒ MES 沒記到的事，這裡看不到。"


# ---- equipment_status_events -----------------------------------------------------
def _caveats_events(*, n_events, n_rep, truncated, has_uncoded):
    c = [
        "MES 事件是**時點**（錯誤碼寫入時刻）、沒有持續時間 ⇒ `duration_s` 恆為 null；事件數 ≠ 停機時數"
        "（停機請看 equipment_downtime／/downtime）。",
        "`status_after` 是寫事件當下的設備狀態欄；多數錯誤事件不改變狀態（探針 07905／08158 全為 In Service）"
        " ⇒ 有事件不代表停機。",
        "`label` 是 MES 錯誤碼字典的英文定義名原文；事件的自由文字註記刻意不取。",
        _CAV_MES_ONLY,
    ]
    if n_events == 0:
        c.append("本窗 MES 對這台**沒有任何事件記錄**。不少機台（例如量測機）MES 本來就不寫事件"
                 " ⇒ 零筆＝「MES 沒有記錄」，不等於「沒有故障」。")
    if n_rep is not None and n_events is not None and n_rep != n_events:
        c.append(f"⚠ 兩個 MES 事件源筆數不一致（錯誤事件表 {n_events}／報表事實表 {n_rep}）"
                 "⇒ 以錯誤事件表為準；差異可能是報表 ETL 延遲。")
    if has_uncoded:
        c.append("部分事件沒有對應錯誤碼（`code`＝null）＝MES 寫入時沒掛碼、不是查漏。")
    if truncated:
        c.append(f"⚠ 逐列清單只列最近 {EV_CAP} 筆（`truncated=true`）；`n_events`／`by_code`／`by_status`"
                 " 是全窗計數、不受此限。")
    return c


def derive_status_events(eid, master, summary_rows, event_rows, n_rep, *, window=None,
                         n_events=None, caps=None, fetched_at=None, elapsed_s=None,
                         days_requested=None):
    """`SQL_EV_SUMMARY`／`SQL_EV_ROWS`／`SQL_EV_REP_COUNT` 的列 → `board.equipment_status_events.v1`。

    列欄序（＝SQL SELECT 順序）：summary `(code, class, label, status, n, first, last)`；
    event `(at, status, code, class, label)`。`n_events` 省略＝summary 加總（live）；demo 可傳探針 Dx 總數。"""
    caps = dict(caps or {})
    window = dict(window or {})
    key = {"eid": eid, "days": window.get("days")}
    diag = {"fetched_at": _fmt(fetched_at), "cap_hit": caps, "elapsed_s": elapsed_s,
            "days_requested": days_requested, "n_event_rows_raw": len(event_rows),
            "n_summary_rows_raw": len(summary_rows)}
    if master is None:
        return eid_not_found("equipment_status_events", key, diag)
    out = blank("equipment_status_events", key, found=True, diag=diag)
    out["equipment"] = equipment_block(master)
    out["window"] = {"from": _fmt(window.get("from")), "to": _fmt(window.get("to")),
                     "days": window.get("days")}
    by_code, by_status = {}, {}
    n_sum = 0
    for r in summary_rows:
        code, n = _s(r[0]), _int(r[4]) or 0
        n_sum += n
        b = by_code.setdefault(code, {"code": code, "category": _s(r[1]), "label": _s(r[2]),
                                      "n": 0, "first_at": None, "last_at": None})
        b["n"] += n
        f, l_ = _ts(r[5]), _ts(r[6])
        if f and (b["first_at"] is None or f < b["first_at"]):
            b["first_at"] = f
        if l_ and (b["last_at"] is None or l_ > b["last_at"]):
            b["last_at"] = l_
        st = _s(r[3]) or "(null)"
        by_status[st] = by_status.get(st, 0) + n
    n_ev = n_sum if n_events is None else int(n_events)
    events = [{"at": _ts(r[0]), "source_table": "equipment_error_log_t", "code": _s(r[2]),
               "category": _s(r[3]), "label": _s(r[4]), "status_after": _s(r[1]),
               "duration_s": None} for r in event_rows]
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    truncated = bool(caps.get("rows")) or len(events) < n_ev and len(events) >= EV_CAP
    out.update({
        "n_events": n_ev, "n_events_rep": (None if n_rep is None else _int(n_rep)),
        "events": events,
        "by_code": sorted(by_code.values(), key=lambda b: (-b["n"], str(b["code"]))),
        "by_status": dict(sorted(by_status.items(), key=lambda kv: (-kv[1], kv[0]))),
        "truncated": truncated,
        "reason": "no_events_in_window" if n_ev == 0 else None,
        "caveats": _caveats_events(n_events=n_ev, n_rep=_int(n_rep), truncated=truncated,
                                   has_uncoded=any(b["code"] is None for b in by_code.values())),
    })
    return out


# ---- equipment_modules ------------------------------------------------------------
def derive_modules(eid, master, edge_rows, *, caps=None, fetched_at=None, elapsed_s=None):
    """`SQL_MOD_EDGES` 的列 `(parent_eid, parent_desc, child_eid, child_desc, parent_class, child_class,
    updated)` → `board.equipment_modules.v1`。角色以 EID 判（父端＝本機 ⇒ 對方是子槽位）。"""
    caps = dict(caps or {})
    diag = {"fetched_at": _fmt(fetched_at), "cap_hit": caps, "elapsed_s": elapsed_s,
            "n_edge_rows_raw": len(edge_rows)}
    if master is None:
        return eid_not_found("equipment_modules", eid, diag)
    out = blank("equipment_modules", eid, found=True, diag=diag)
    out["equipment"] = equipment_block(master)
    me = _s(master[1]) or eid
    links = {"children": {}, "parents": {}}
    n_self, n_unresolved = 0, 0
    latest = None
    for r in edge_rows:
        pe, ce, at = _s(r[0]), _s(r[2]), _ts(r[6])
        if pe == me and ce == me:
            n_self += 1                                  # 自參照（mes_timeline 同樣忽略）
            continue
        if pe == me:
            role, oe, on, oc = "children", ce, _s(r[3]), _s(r[5])
        else:
            role, oe, on, oc = "parents", pe, _s(r[1]), _s(r[4])
        if oe is None:
            n_unresolved += 1
            continue
        if at and (latest is None or at > latest):
            latest = at
        L = links[role].setdefault(oe, {"eid": oe, "name": on, "class": oc, "n_edge_rows": 0,
                                        "first_linked_at": None, "last_linked_at": None,
                                        "in_latest_config": False})
        L["n_edge_rows"] += 1
        if at and (L["first_linked_at"] is None or at < L["first_linked_at"]):
            L["first_linked_at"] = at
        if at and (L["last_linked_at"] is None or at > L["last_linked_at"]):
            L["last_linked_at"] = at
    for role in links:
        role_latest = max((x["last_linked_at"] for x in links[role].values()
                           if x["last_linked_at"]), default=None)
        for x in links[role].values():
            x["in_latest_config"] = bool(role_latest) and x["last_linked_at"] == role_latest
    ch = sorted(links["children"].values(), key=lambda x: (not x["in_latest_config"],
                                                           str(x["eid"])))
    pa = sorted(links["parents"].values(), key=lambda x: (not x["in_latest_config"],
                                                          str(x["eid"])))
    truncated = bool(caps.get("edges"))
    c = [
        "MES 的模組關係表是**歷次設定紀錄**、不是現況快照：同一子槽位多列＝多次設定；"
        "`in_latest_config`＝該子槽位出現在最近一次設定時間點（**推論**：MES 無「目前掛載」欄）。",
        "`n_edges`＝原始邊列數（含重複設定、無法解析的邊）；`n_children`／`n_parents` 是去重後的模組數。",
    ]
    if not edge_rows:
        c.append("MES 沒有這台的父子槽位邊＝它在 MES 裡是獨立機台（或模組關係沒登記在 MES）。")
    if n_unresolved:
        c.append(f"有 {n_unresolved} 列邊的另一端在設備主檔查不到（MES 端空值或已刪除的機台）⇒ 不列出。")
    if n_self:
        c.append(f"有 {n_self} 列自己連到自己的邊（MES 資料雜訊）⇒ 忽略。")
    if pa:
        c.append("本機是別台的子槽位（`parents` 非空）⇒ 稼動／停機通常記在父機上。")
    if truncated:
        c.append(f"⚠ 邊列撞到上限 {MOD_CAP}（`truncated=true`）⇒ 清單是**下界**。")
    out["diag"].update({"n_self_loop": n_self, "n_unresolved": n_unresolved})
    out.update({"parents": pa, "children": ch, "n_edges": len(edge_rows), "n_parents": len(pa),
                "n_children": len(ch), "latest_config_at": latest, "truncated": truncated,
                "reason": "no_module_edges" if not edge_rows else None, "caveats": c})
    return out


# ---- equipment_calibration：payload 解析（原文只在這裡、永不輸出）--------------------
_SECTION_RE = re.compile(r"\[([^\]\r\n]{1,80})\]")
_KV_RE = re.compile(r"([A-Za-z][^=\"\[\]]{0,80}?)\s*=\s*\"([^\"]*)\"")
_SCRIPTER_HEAD_RE = re.compile(
    r"Calibration Equipment:\s*(EID-\d{5,})\s+(PASS|FAIL)\s+(\d{2})/(\d{2})/(\d{4})\s+"
    r"(\d{2}:\d{2}:\d{2})")
_NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_MEAS_RE = re.compile(r"(?<![\w.])([A-Za-z0-9_]{1,40})\s+(" + _NUM + r")\s+(" + _NUM + r")\s+("
                      + _NUM + r")\s+(" + _NUM + r")\s+(PASS|FAIL)\b")
#: INI 鍵白名單（**只有這些會出輸出**；其餘鍵值一律丟掉）。
_KEY_RESULT = ("result",)
_KEY_DUE = ("calibration due date", "calibration check due date")
_KEY_LAST = ("last calibration date",)
_KEY_REF = ("calibration part id",)
_EID_VALUE_RE = re.compile(r"^EID-\d{5,}$")
_EMPTY_PAYLOADS = ("", "[]", "{}")


def _iso(v):
    """`2026-08-14T08:13:16` → `2026-08-14 08:13:16`；認不得回 None（不猜）。"""
    return _ts(v) if _dt(v) is not None else None


def _res(v):
    s = (_s(v) or "").upper()
    return s if s in ("PASS", "FAIL") else None


def _item(name, **kw):
    it = {k: None for k in CAL_ITEM_KEYS}
    it["item"] = name
    it.update(kw)
    return it


def parse_calibration(payload, payload_len=None):
    """`ashCalibration` 原文 → 一筆輸出記錄（**不含原文**）。兩種已知格式：
      · `ini_sections`：`[Section] Key = "value" …`（Leak Test Nest／CLN／Press／Plasma…）；
      · `scripter_report`：`Calibration Equipment: EID-xxxxx PASS|FAIL dd/mm/yyyy …`＋量測表（07905 E-Tester）。
    `[]`／空 ⇒ `empty`（**有記錄但無內容**）；兩種都認不得 ⇒ `unrecognized`（不猜、不回原文）。"""
    raw = payload if isinstance(payload, str) else ("" if payload is None else str(payload))
    stripped = raw.strip()
    rec = {"recorded_at": None, "format": None, "overall_result": None, "next_due": None,
           "n_items": 0, "items": [], "items_truncated": False,
           "payload_len": _int(payload_len) if payload_len is not None else len(raw),
           "payload_empty": False}
    if stripped in _EMPTY_PAYLOADS:
        rec.update({"format": "empty", "payload_empty": True})
        return rec
    items = []
    m = _SCRIPTER_HEAD_RE.search(raw)
    if m:
        fmt = "scripter_report"
        ref, res, dd, mm_, yy, hms = m.groups()
        when = f"{yy}-{mm_}-{dd} {hms}"
        rec["overall_result"] = res
        items.append(_item("Calibration Equipment", result=res, last_calibrated=_iso(when),
                           ref_eid=ref))
        for mm in _MEAS_RE.finditer(raw[m.end():]):
            name, meas, _std, lo, hi, r = mm.groups()
            items.append(_item(name, result=r, value=_num(meas), min=_num(lo), max=_num(hi)))
    else:
        heads = list(_SECTION_RE.finditer(raw))
        if not heads:
            rec["format"] = "unrecognized"
            return rec
        fmt = "ini_sections"
        for i, h in enumerate(heads):
            body = raw[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(raw)]
            kv = {}
            for k in _KV_RE.finditer(body):
                kv.setdefault(re.sub(r"\s+", " ", k.group(1)).strip().lower(), k.group(2))
            if not kv:
                continue
            ref = next((kv[k] for k in _KEY_REF if k in kv), None)
            items.append(_item(
                _s(h.group(1)),
                result=next((_res(kv[k]) for k in _KEY_RESULT if k in kv), None),
                last_calibrated=next((_iso(kv[k]) for k in _KEY_LAST if k in kv), None),
                due=next((_iso(kv[k]) for k in _KEY_DUE if k in kv), None),
                ref_eid=(ref if ref and _EID_VALUE_RE.match(ref.strip()) else None)))
        results = [i["result"] for i in items if i["result"]]
        rec["overall_result"] = ("FAIL" if "FAIL" in results else "PASS" if results else None)
    dues = sorted(i["due"] for i in items if i["due"])
    rec.update({"format": fmt if items else "unrecognized", "next_due": dues[0] if dues else None,
                "n_items": len(items), "items": items[:CAL_ITEMS_CAP],
                "items_truncated": len(items) > CAL_ITEMS_CAP})
    if not items:
        rec["overall_result"] = None
    return rec


def _cal_record(row):
    """`SQL_CAL_ROWS`／`SQL_CAL_LAST` 一列 `(ashDateUpdated, cal_len, ashCalibration)` → 記錄。"""
    rec = parse_calibration(row[2], row[1])
    rec["recorded_at"] = _ts(row[0])
    return rec


def derive_calibration(eid, master, count_row, cal_rows, last_rows, *, window=None, caps=None,
                       fetched_at=None, elapsed_s=None, days_requested=None):
    """`SQL_CAL_COUNT`／`SQL_CAL_ROWS`／`SQL_CAL_LAST` → `board.equipment_calibration.v1`。
    `count_row`＝`(n_all, n_win, last_at)`；`last_rows` 只在窗內零筆時有值。"""
    caps = dict(caps or {})
    window = dict(window or {})
    key = {"eid": eid, "days": window.get("days")}
    diag = {"fetched_at": _fmt(fetched_at), "cap_hit": caps, "elapsed_s": elapsed_s,
            "days_requested": days_requested, "n_cal_rows_raw": len(cal_rows)}
    if master is None:
        return eid_not_found("equipment_calibration", key, diag)
    out = blank("equipment_calibration", key, found=True, diag=diag)
    out["equipment"] = equipment_block(master)
    out["window"] = {"from": _fmt(window.get("from")), "to": _fmt(window.get("to")),
                     "days": window.get("days")}
    out["registered"] = {"last_calibration": _ts(master[7]), "due": _ts(master[8])}
    cr = tuple(count_row) if count_row else (0, 0, None)
    n_all, n_win = _int(cr[0]) or 0, _int(cr[1]) or 0
    records = [_cal_record(r) for r in cal_rows]
    records.sort(key=lambda r: r["recorded_at"] or "", reverse=True)
    last_before = _cal_record(last_rows[0]) if (last_rows and not records) else None
    truncated = bool(caps.get("records")) or n_win > len(records)
    seen = records + ([last_before] if last_before else [])
    n_empty = sum(1 for r in seen if r["payload_empty"])
    n_unrec = sum(1 for r in seen if r["format"] == "unrecognized")
    nm, c = [], [
        "校正真源＝MES 設備歷史表的校正欄（每筆＝一次校正寫入）；只回解析出的白名單欄"
        "（項目、PASS/FAIL、量測值與上下限、上次校正、到期、參考件 EID），**原文不回**。",
        "`registered`＝設備主檔上登記的校正日／到期日：這組欄位不一定隨每次校正更新（有的機台停在多年前）"
        " ⇒ 只當登記值參考，"
        "以 `records` 為準。",
        "`next_due` 取該筆記錄內最早的到期日；有些格式（例如 E-Tester 報告）沒有到期欄 ⇒ null。",
    ]
    if n_all == 0:
        c.append("MES 沒有這台的任何校正記錄（設備歷史表的校正欄全空）；不代表沒校正——可能在 MES 以外管理。")
    elif n_win == 0 and last_before:
        c.append(f"本窗（{window.get('days')} 天）沒有校正記錄 ⇒ 附上窗外最近一筆（`last_before_window`，"
                 f"{last_before['recorded_at']}）。")
    if seen and n_empty == len(seen):
        nm.append({"field": "records[].items", "reason": "calibration_payload_empty",
                   "note": "有校正記錄、但內容是空殼（例如 `[]`）⇒ 量不到任何校正項目與結果；"
                           "不是零筆、也不是 PASS。"})
        c.append("這台的校正記錄是**空殼**（有寫入、沒內容）；有些機台的校正記在子槽位上"
                 " ⇒ 可用 equipment_modules 找子槽位 EID 再查校正。")
    elif n_empty:
        c.append(f"{n_empty} 筆記錄是空殼（有寫入、沒內容）⇒ `payload_empty=true`、不計結果。")
    if n_unrec:
        c.append(f"{n_unrec} 筆記錄的格式認不得 ⇒ 只回時間與長度、不猜內容。")
    if any(r["items_truncated"] for r in seen):
        c.append(f"部分記錄項目超過 {CAL_ITEMS_CAP} 個 ⇒ `items` 只列前 {CAL_ITEMS_CAP} 個（`items_truncated`）。")
    if truncated:
        c.append(f"⚠ 窗內共 {n_win} 筆、只列最近 {len(records)} 筆（`truncated=true`）。")
    reason = ("no_calibration_records" if n_all == 0
              else "no_calibration_in_window" if n_win == 0 else None)
    out.update({"n_records": n_all, "n_records_in_window": n_win,
                "last_calibrated_at": _ts(cr[2]), "records": records,
                "last_before_window": last_before, "not_measurable": nm, "truncated": truncated,
                "caveats": c, "reason": reason})
    return out


# ---- validator（形狀檢查單一出處；smoke／selftest 委派它）--------------------------
_FORBIDDEN_OUT = ("astPK", "aslPK", "ashPK", "aslPersonID", "OperatorName", "OperatorId",
                  "ashCalibrationAuthority", "stpPersonID", "$Id", "Created by")


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
        if rec["found"] and rec["unavailable_reason"]:
            return False
        if rec["found"] and set(rec["equipment"] or {}) != set(EQUIP_KEYS):
            return False
        for nm in rec["not_measurable"]:
            if nm["reason"] not in NOT_MEASURABLE_REASONS:
                return False
        blob = json.dumps(body, ensure_ascii=False, default=str)
        if any(f in blob for f in _FORBIDDEN_OUT):
            return False
        if "\n" in "".join(_mm._walk_strings(body)):
            return False
        if tool == "equipment_status_events":
            if not isinstance(rec["by_status"], dict):
                return False
            for e in rec["events"]:
                if set(e) != set(EVENT_ROW_KEYS) or e["duration_s"] is not None:
                    return False
            for b in rec["by_code"]:
                if set(b) != set(BY_CODE_KEYS):
                    return False
        if tool == "equipment_modules":
            for x in rec["parents"] + rec["children"]:
                if set(x) != set(LINK_KEYS):
                    return False
        if tool == "equipment_calibration":
            recs = rec["records"] + ([rec["last_before_window"]] if rec["last_before_window"]
                                     else [])
            for r in recs:
                if set(r) != set(CAL_RECORD_KEYS) or r["format"] not in CAL_FORMATS:
                    return False
                for it in r["items"]:
                    if set(it) != set(CAL_ITEM_KEYS):
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


def fetch_master(cur, eid):
    cur.execute(SQL_EQ_MASTER, eid)
    r = cur.fetchone()
    return tuple(r) if r else None


def _window(days, now):
    now = now or datetime.now()
    return ((now - timedelta(days=int(days))).strftime(_TS_FMT), now.strftime(_TS_FMT), now)


def compute_status_events(cur, eid, days, *, now=None):
    t0 = time.perf_counter()
    fetch_guard(cur, "equipment_status_events")
    frm, to, now = _window(days, now)             # ★ 上界永不超過現在（CONCEPT §21.1 髒日期）
    win = {"from": frm, "to": to, "days": int(days)}
    m = fetch_master(cur, eid)
    if m is None:
        return derive_status_events(eid, None, [], [], None, window=win, fetched_at=now)
    pk = int(m[0])
    cur.execute(SQL_EV_SUMMARY, pk, frm, to)
    summary = _rows(cur)
    rows = []
    if summary:
        cur.execute(SQL_EV_ROWS, int(EV_CAP), pk, frm, to)
        rows = _rows(cur)
    cur.execute(SQL_EV_REP_COUNT, pk, frm, to)
    rr = cur.fetchone()
    n_rep = rr[0] if rr else None
    n_total = sum(_int(r[4]) or 0 for r in summary)
    return derive_status_events(eid, m, summary, rows, n_rep, window=win, fetched_at=now,
                                caps={"rows": len(rows) >= EV_CAP and n_total > len(rows)},
                                elapsed_s=round(time.perf_counter() - t0, 3),
                                days_requested=int(days))


def compute_modules(cur, eid):
    t0 = time.perf_counter()
    fetch_guard(cur, "equipment_modules")
    m = fetch_master(cur, eid)
    if m is None:
        return derive_modules(eid, None, [], fetched_at=datetime.now())
    pk = int(m[0])
    cur.execute(SQL_MOD_EDGES, int(MOD_CAP), pk, pk)
    edges = _rows(cur)
    return derive_modules(eid, m, edges, caps={"edges": len(edges) >= MOD_CAP},
                          fetched_at=datetime.now(),
                          elapsed_s=round(time.perf_counter() - t0, 3))


def compute_calibration(cur, eid, days, *, now=None):
    t0 = time.perf_counter()
    fetch_guard(cur, "equipment_calibration")
    frm, to, now = _window(days, now)
    win = {"from": frm, "to": to, "days": int(days)}
    m = fetch_master(cur, eid)
    if m is None:
        return derive_calibration(eid, None, None, [], [], window=win, fetched_at=now)
    pk = int(m[0])
    cur.execute(SQL_CAL_COUNT, frm, pk, to)
    cnt = cur.fetchone()
    cnt = tuple(cnt) if cnt else (0, 0, None)
    n_win = _int(cnt[1]) or 0
    rows, last = [], []
    if n_win:
        cur.execute(SQL_CAL_ROWS, int(CAL_CAP), pk, frm, to)
        rows = _rows(cur)
    elif _int(cnt[0]):
        cur.execute(SQL_CAL_LAST, 1, pk, frm)
        last = _rows(cur)
    return derive_calibration(eid, m, cnt, rows, last, window=win, fetched_at=now,
                              caps={"records": n_win > len(rows)},
                              elapsed_s=round(time.perf_counter() - t0, 3),
                              days_requested=int(days))


def compute(tool, cur, key):
    """`board_data.get_mes_equipment` 的 live 分派點（`key` 已經過 `normalize_args`；呼叫端先跑 `precheck`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    if tool == "equipment_status_events":
        return compute_status_events(cur, key["eid"], key["days"])
    if tool == "equipment_modules":
        return compute_modules(cur, key)
    if tool == "equipment_calibration":
        return compute_calibration(cur, key["eid"], key["days"])
    raise KeyError(tool)


# =============================================================================
# 5. demo：fixture（`_fixtures/mes_equipment_<tool>.json`）→ 同一支 derive_*
# =============================================================================
FIXTURE_FILES = {t: f"mes_equipment_{t}.json" for t in TOOLS}
DEMO_EIDS = ('EID-55518', 'EID-54519')           # 示範機（DIGEST §3）；EID-35535＝零訊號邊界案例
ZERO_SIGNAL_EID = "EID-55172"

_DEMO_CAV_EV = ("（demo）探針樣本＝每台最近 {n_s} 筆事件（MES 該 365 天窗共 {n_t} 筆）⇒ 窗 {d} 天的逐列與彙總"
                "{tail}；錨點＝探針時刻 {to}。")
_DEMO_CAV_LABEL = ("（demo）`label` 取自報表事實表的事件描述（探針未抽字典定義名）；`category` 探針未抽 ⇒ null。")
_DEMO_CAV_MOD = "（demo）探針未抽模組類別 ⇒ `class` 為 null；live 會帶 MES 設備類別。"
_DEMO_CAV_CAL = ("（demo）探針只抽每台最近 5 筆校正、每筆前 1200 字 ⇒ 項目清單不完整、窗內筆數可能是下界；"
                 "錨點＝探針時刻 {to}。")


def _demo_window(g, days):
    to = _dt(g["anchor_to"])
    frm = to - timedelta(days=int(days))
    return frm, to


def demo_lookup(tool, doc, key):
    """fixture 裡找樣本 → 跑**同一支** `derive_*`（demo 與 live 走同一份純函式＝形狀單一出處）。
    demo 依 `days` 以探針時刻為錨過濾樣本（樣本不足以證明完整時標 `truncated`＝下界）。
    查不到回 `None`（呼叫端回 `not_in_demo_fixture`，**不是** `eid_not_found`）。"""
    pre = precheck(tool, key)
    if pre is not None:
        return pre
    eid = _key_eid(key)
    g = next((x for x in (doc or {}).get("golden") or [] if x["key"] == eid), None)
    if g is None:
        return None
    s = g["steps"]
    master = tuple(s["MASTER"]["rows"][0]) if s["MASTER"]["rows"] else None
    if tool == "equipment_modules":
        out = derive_modules(eid, master, s["EDGES"]["rows"], caps=g["caps"],
                             fetched_at=g["captured_at"])
        if out["found"]:
            out["caveats"].append(_DEMO_CAV_MOD)
        return out
    days = key["days"]
    frm, to = _demo_window(g, days)
    win = {"from": frm.strftime(_TS_FMT), "to": to.strftime(_TS_FMT), "days": days}
    if tool == "equipment_status_events":
        sample = s["EVENTS"]["rows"]
        n_total = g["n_events_probe_Dx"]
        inwin = [r for r in sample if _dt(r[0]) and _dt(r[0]) >= frm]
        complete = len(sample) >= n_total
        exact = complete or any(_dt(r[0]) < frm for r in sample if _dt(r[0]))
        full_window = days == g["probe_window_days"]
        n_ev = n_total if full_window else len(inwin)
        summ = {}
        for r in inwin:                                # demo 以樣本模擬 SQL_EV_SUMMARY 的分組
            k = (r[2], r[3], r[4], r[1])
            a = summ.setdefault(k, [0, r[0], r[0]])
            a[0] += 1
            a[1], a[2] = min(a[1], r[0]), max(a[2], r[0])
        summary = [(k[0], k[1], k[2], k[3], v[0], v[1], v[2]) for k, v in summ.items()]
        n_rep = g["n_rep_probe_Dx"] if full_window else None
        # 逐列少於總數（全窗：樣本 60 < Dx 363）或計數本身只是下界（子窗但樣本沒涵蓋到窗起點）⇒ truncated
        partial = len(inwin) < n_ev or (not full_window and not exact)
        out = derive_status_events(eid, master, summary, inwin, n_rep, window=win, n_events=n_ev,
                                   caps={"rows": partial},
                                   fetched_at=g["captured_at"], days_requested=days)
        if out["found"] and n_total:
            tail = ("完整" if not partial
                    else "只涵蓋樣本（`by_code`／`by_status` 是樣本彙總、`n_events` "
                         + ("取探針全窗計數" if full_window else "是下界") + "）")
            out["caveats"].append(_DEMO_CAV_EV.format(n_s=len(sample), n_t=n_total, d=days,
                                                      tail=tail, to=g["anchor_to"]))
            out["caveats"].append(_DEMO_CAV_LABEL)
        return out
    if tool == "equipment_calibration":
        sample = s["CAL"]["rows"]
        n_all = g["n_cal_probe_Dx"]
        inwin = [r for r in sample if _dt(r[0]) and _dt(r[0]) >= frm]
        exact = len(sample) >= n_all or any(_dt(r[0]) < frm for r in sample if _dt(r[0]))
        before = [r for r in sample if _dt(r[0]) and _dt(r[0]) < frm]
        cnt = (n_all, len(inwin), sample[0][0] if sample else None)
        out = derive_calibration(eid, master, cnt, inwin, before[:1] if not inwin else [],
                                 window=win, caps={"records": not exact},
                                 fetched_at=g["captured_at"], days_requested=days)
        if out["found"] and n_all:
            out["caveats"].append(_DEMO_CAV_CAL.format(to=g["anchor_to"]))
        return out
    raise KeyError(tool)


# =============================================================================
# 6. fixture builder（讀探針 TSV；純離線、零 MES）
# =============================================================================
PROBE_TSV = "_PROBE-ASSISTANT-MES-FAMILY-2-RESULT.tsv"
PROBE_SQL = "_PROBE-ASSISTANT-MES-FAMILY-2-20260923.sql"
PROBE_EIDS = ('EID-55518', 'EID-54519', 'EID-55172')
#: 各結果集以 `step` 欄的前綴辨認（本支的 `step` 值帶空白說明）。
_STEP_PREFIX = {"0e": "0e context", "Dx": "Dx candidate", "D1": "D1 equipment master",
                "D4a": "D4a equipment status", "D4b": "D4b equipment failures",
                "D6": "D6 dependent", "I4": "I4 calibration"}
#: 原文去識別（fixture 存的是 I4 前 1200 字；SVN `$Id$` 帶提交者帳號、`Created by` 帶主機名）。
_SCRUB = ((re.compile(r"\$Id:[^$]*\$"), "$Id-scrubbed"),
          (re.compile(r"Created by\s*:\s*\S+"), "Created-by-scrubbed"))


def scrub_payload(s):
    if s is None:
        return None
    for rx, rep in _SCRUB:
        s = rx.sub(rep, s)
    return s


def parse_probe_tsv(path):
    """探針 TSV（SSMS 多結果集貼成一檔）→ `{tag: {"header", "rows"}}`（tag＝`_STEP_PREFIX` 的鍵）。"""
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


def _rows_of(t, tag, eid, cols, eid_col="eid"):
    h = t[tag]["header"]
    idx = [h.index(c) for c in cols]
    e = h.index(eid_col)
    return [[r[i] for i in idx] for r in t[tag]["rows"] if r[e] == eid]


def build_fixtures(base=BASE):
    t = parse_probe_tsv(os.path.join(base, PROBE_TSV))
    ctx = dict(zip(t["0e"]["header"], t["0e"]["rows"][0]))
    anchor = ctx["win_to"][:19]
    common = {"captured_at": anchor, "source_probe": PROBE_SQL, "anchor_to": anchor,
              "_provenance": "domain-side read-only query 2026-08-13; " + PROBE_SQL +
                             "; builder = mes_equipment.py --build-fixtures (do not hand-edit)"}
    dx = {r[1]: dict(zip(t["Dx"]["header"], r)) for r in t["Dx"]["rows"]}
    master_cols = ["astPK", "astEquipmentID", "astDescription", "astStatus", "astDesignation",
                   "ast_class", "home_station", "reg_last_cal", "reg_cal_due"]
    out = {}
    ev, mod, cal = [], [], []
    for eid in PROBE_EIDS:
        master = {"source_step": "D1 (equipment_v; astPK/astEquipmentID/astDescription = Dx equipment_t)",
                  "columns": master_cols,
                  "rows": _rows_of(t, "D1", eid, master_cols, eid_col="eid_param")}
        # ── events：D4a 逐列 ＋ D4b 同時同碼列的 EventDescription 當標籤（探針未抽字典定義名）──
        d4b = {(r[0], r[1]): r[2] for r in _rows_of(t, "D4b", eid, ["EventTime", "FailureCode",
                                                                     "EventDescription"])}
        evrows = [[r[0], r[1], r[2], None, d4b.get((r[0], r[2]))]
                  for r in _rows_of(t, "D4a", eid, ["aslDate", "status_after", "code_from_vdf"])
                  if r[0] is not None]
        ev.append({"key": eid, **common, "probe_window_days": 365,
                   "n_events_probe_Dx": int(dx[eid]["n_err_ev"]),
                   "n_rep_probe_Dx": int(dx[eid]["n_rep_fail_ev"]),
                   "steps": {"MASTER": master,
                             "EVENTS": {"source_step": "D4a (TOP 60 per EID; label from D4b "
                                                       "EventDescription matched on time+code; "
                                                       "category not sampled)",
                                        "columns": ["aslDate", "status_after",
                                                    "vdfProductionDataID", "vdfClassID",
                                                    "FailureDescription"],
                                        "rows": evrows}}})
        # ── modules：D6（探針未抽類別）──
        edges = [[r[0], r[1], r[2], r[3], None, None, r[4]]
                 for r in _rows_of(t, "D6", eid, ["parent_eid", "parent_desc", "child_eid",
                                                  "child_desc", "eqeDateUpdated"])]
        mod.append({"key": eid, **common, "n_edges_probe_Dx": int(dx[eid]["n_dep_edges"]),
                    "caps": {"edges": False},
                    "steps": {"MASTER": master,
                              "EDGES": {"source_step": "D6 (class columns not sampled)",
                                        "columns": ["parent_eid", "parent_desc", "child_eid",
                                                    "child_desc", "parent_class", "child_class",
                                                    "eqeDateUpdated"],
                                        "rows": edges}}})
        # ── calibration：I4（每台最近 5 筆、每筆前 1200 字、去識別）──
        calrows = [[r[0], r[1], scrub_payload(r[2])]
                   for r in _rows_of(t, "I4", eid, ["ashDateUpdated", "cal_len", "cal_head"])
                   if r[0] is not None]
        cal.append({"key": eid, **common, "n_cal_probe_Dx": int(dx[eid]["n_cal_rows"]),
                    "steps": {"MASTER": master,
                              "CAL": {"source_step": "I4 (latest 5 per EID; payload = first 1200 "
                                                     "chars, newlines->spaces, $Id/Created-by "
                                                     "scrubbed; cal_len = true DATALENGTH)",
                                      "columns": ["ashDateUpdated", "cal_len", "ashCalibration"],
                                      "rows": calrows}}})
    note = ("EID-07905 / EID-08158 = demo machines (DIGEST §3); EID-35535 = zero-signal boundary "
            "case (MES writes no events / edges / calibration for it). demo filters the probe "
            "sample by days anchored at anchor_to.")
    out["equipment_status_events"] = {"schema_id": SCHEMA_EVENTS, "golden": ev, "_note": note}
    out["equipment_modules"] = {"schema_id": SCHEMA_MODULES, "golden": mod, "_note": note}
    out["equipment_calibration"] = {"schema_id": SCHEMA_CAL, "golden": cal, "_note": note}
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
    """記錄送出的 SQL 與參數；依 SQL 身分回預置列（fetchone 取 answers[sql]，可為 None）。"""

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
        return self.answers.get(self._last)

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
    t("all-sql-9", len(sqls) == 9 and len(set(sqls)) == 9)
    for s in sqls:
        t("recompile", s.rstrip().endswith("OPTION (RECOMPILE)"), s[:50])
        t("select-only", s.lstrip().startswith("SELECT"), s[:50])
        t("★ no-temp-table（Q10）", "INTO #" not in s and "#" not in s, s[:50])
        t("no-ddl-dml", not re.search(r"\b(CREATE|DROP|INSERT|UPDATE|DELETE|MERGE|EXEC)\b", s),
          s[:50])
        t("no-subquery-in-aggregate",
          not re.search(r"(COUNT|SUM|MAX|MIN|AVG)\s*\(\s*\(\s*SELECT", s), s[:40])
        m = re.search(r"GROUP BY([^\n]*)", s)
        if m:
            t("no-subquery-in-group-by", "SELECT" not in m.group(1), s[:40])
    blob = "\n".join(sqls)
    for bad in ("aslPersonID", "OperatorName", "OperatorId", "ashCalibrationAuthority",
                "stpPersonID", "rqsRequestor", "Comment", "aslComment", "ashComments",
                "ashSoftware", "ashConfiguration", "all_segment_data_value_v",
                "PublishingAuthority", "ChangeLog"):
        t(f"★ never-{bad}", bad not in blob)
    t("★ no-stpEndTime", "stpEndTime" not in blob)
    t("no-LEFT(-truncation（Q7：原文整欄只進 Python）", "LEFT(" not in blob.upper().replace("LEFT JOIN", ""))
    t("no-REPLACE", "REPLACE(" not in blob)
    t("no-LIKE", " LIKE " not in blob)
    for nm_ in ("SQL_EV_SUMMARY", "SQL_EV_ROWS", "SQL_EV_REP_COUNT"):
        s = getattr(mod, nm_)
        t(f"{nm_}-halfopen-window", (">= ? AND" in s and "< ?" in s))
    t("ev-by-eqppk（Q1）", all("eel.astPK = ?" in getattr(mod, x) for x in ("SQL_EV_SUMMARY",
                                                                          "SQL_EV_ROWS")))
    t("ev-vdf-left-join（D0c 形：未掛碼事件不丟）",
      "LEFT JOIN dbo.production_data_definition_t AS vdf ON vdf.vdfPK = eel.vdfPK" in SQL_EV_ROWS
      and "LEFT JOIN dbo.production_data_definition_t AS vdf ON vdf.vdfPK = eel.vdfPK"
      in SQL_EV_SUMMARY)
    t("ev-dictionary-label（Q2）", "vdf.FailureDescription" in SQL_EV_ROWS
      and "vdf.vdfClassID" in SQL_EV_ROWS)
    t("ev-summary-no-TOP（Q4）", "TOP" not in SQL_EV_SUMMARY and "COUNT(*)" in SQL_EV_SUMMARY)
    t("ev-rows-top-param（Q3）", SQL_EV_ROWS.startswith("SELECT TOP (?)")
      and "eel.aslPK DESC" in SQL_EV_ROWS)
    t("★ rep-count-only（Q5：不逐列＝不雙算）", "COUNT(*)" in SQL_EV_REP_COUNT
      and "EventDescription" not in blob and "f.EquipmentId = ?" in SQL_EV_REP_COUNT)
    t("mod-both-ends", "d.astPK_parent = ? OR d.astPK_dependent = ?" in SQL_MOD_EDGES
      and SQL_MOD_EDGES.startswith("SELECT TOP (?)"))
    t("mod-no-rel-case（Q6）", "CASE" not in SQL_MOD_EDGES)
    t("cal-has-content-filter", all("DATALENGTH(eh.ashCalibration) > 0" in s
                                    for s in (SQL_CAL_COUNT, SQL_CAL_ROWS, SQL_CAL_LAST)))
    t("cal-window", "eh.ashDateUpdated >= ? AND eh.ashDateUpdated < ?" in SQL_CAL_ROWS
      and "eh.ashDateUpdated < ?" in SQL_CAL_LAST and "ashPK DESC" in SQL_CAL_ROWS)
    t("cal-count-upper-bound", "eh.ashDateUpdated < ?" in SQL_CAL_COUNT)
    t("master-tbl-and-view", "FROM dbo.equipment_t AS e" in SQL_EQ_MASTER
      and "LEFT JOIN dbo.equipment_v AS v ON v.astPK = e.astPK" in SQL_EQ_MASTER
      and "e.astEquipmentID = ?" in SQL_EQ_MASTER)
    t("guard-keys", len(GUARD_KEYS) == SQL_GUARD.count("CASE WHEN")
      and all(set(v) <= set(GUARD_KEYS) for v in GUARD_NEEDS.values())
      and set(GUARD_NEEDS) == set(TOOLS))
    t("query-names-real", all(hasattr(mod, q.split(".", 1)[1])
                              for qs in TOOL_QUERY_NAMES.values() for q in qs)
      and set(TOOL_QUERY_NAMES) == set(TOOLS))
    t("query-names-in-all-sql", all(getattr(mod, q.split(".", 1)[1]) in sqls
                                    for qs in TOOL_QUERY_NAMES.values() for q in qs))
    t("all-sql-covered-by-tools", {s for s in sqls if s is not SQL_GUARD}
      == {getattr(mod, q.split(".", 1)[1]) for qs in TOOL_QUERY_NAMES.values() for q in qs})
    t("caps", (EV_CAP, MOD_CAP, CAL_CAP, DAYS_DEFAULT, DAYS_MAX) == (200, 200, 20, 30, 365))

    # ---- ② 參數正規化 -----------------------------------------------------------------
    for raw in ("EID-07905", "eid-07905", "07905", "7905", " EID07905 ", 7905):
        t(f"eid-ok-{raw!r}", normalize_eid(raw) == ("EID-07905", None))
    for bad in ("EID-7905x", "x'; --", "EID-%", "EID-", "123456789", True, 3.5):
        t(f"eid-bad-{bad!r}", normalize_eid(bad)[1]["error"] == "bad_eid")
    t("eid-missing", normalize_eid("")[1]["error"] == "missing_eid"
      and normalize_eid(None)[1]["error"] == "missing_eid")
    t("args-default-days", normalize_args("equipment_status_events", "7905")
      == ({"eid": "EID-07905", "days": 30}, None))
    t("args-cal-365", normalize_args("equipment_calibration", "08158", 365)[0]["days"] == 365)
    t("args-modules-str", normalize_args("equipment_modules", "08158") == ("EID-08158", None))
    for bad in (0, 366, "x", -1):
        t(f"days-refused-{bad}", normalize_args("equipment_status_events", "07905", bad)[1]
          ["error"] == "bad_days")
    t("args-unknown-tool", normalize_args("nope", "07905")[1]["error"] == "unknown_tool")
    t("precheck-none", all(precheck(tl, "EID-07905") is None for tl in TOOLS))
    t("blank-keys", all(set(blank(tl, None)) == set(KEYS_BY_TOOL[tl]) for tl in TOOLS))

    # ---- ③ 純函式：events ------------------------------------------------------------
    M = (172, "EID-07905", "FA Module E-test Machine (FA)", "In Service", "Pilot Line Module",
         "equipModuleElectricalTest", "segAuxElectricalTest", "2025-11-25 08:14:18",
         "2026-11-25 08:14:17")
    summ = [("eqfA", "dimEquipmentFailureCode", "A fail", "In Service", "3",
             "2026-09-01 00:00:00", "2026-09-10 00:00:00"),
            ("eqfB", "dimEquipmentFailureCode", "B fail", "Out Of Service", "2",
             "2026-09-02 00:00:00", "2026-09-03 00:00:00"),
            ("eqfA", "dimEquipmentFailureCode", "A fail", "Out Of Service", "1",
             "2026-08-30 00:00:00", "2026-08-30 00:00:00"),
            (None, None, None, "In Service", "1", "2026-09-05 00:00:00", "2026-09-05 00:00:00")]
    evr = [("2026-09-10 00:00:00.1234", "In Service", "eqfA", "dimEquipmentFailureCode", "A fail"),
           ("2026-09-03 00:00:00", "Out Of Service", "eqfB", "dimEquipmentFailureCode", "B fail")]
    e = derive_status_events("EID-07905", M, summ, evr, 7,
                             window={"from": "2026-08-25 00:00:00", "to": "2026-09-24 00:00:00",
                                     "days": 30})
    t("ev-validator", ok_payload("equipment_status_events", e), e)
    t("ev-n", e["n_events"] == 7 and e["n_events_rep"] == 7 and e["found"] and e["reason"] is None)
    t("ev-by-code-merged", [(b["code"], b["n"]) for b in e["by_code"]]
      == [("eqfA", 4), ("eqfB", 2), (None, 1)]
      and e["by_code"][0]["first_at"] == "2026-08-30 00:00:00"
      and e["by_code"][0]["last_at"] == "2026-09-10 00:00:00")
    t("ev-by-status", e["by_status"] == {"In Service": 4, "Out Of Service": 3})
    t("ev-rows", e["events"][0]["at"] == "2026-09-10 00:00:00.1234"
      and e["events"][0]["source_table"] == "equipment_error_log_t"
      and e["events"][0]["duration_s"] is None)
    t("ev-equipment-block", e["equipment"]["class"] == "equipModuleElectricalTest"
      and "astPK" not in json.dumps(e) and e["equipment"]["eid"] == "EID-07905")
    t("ev-uncoded-caveat", any("沒有對應錯誤碼" in c for c in e["caveats"]))
    t("ev-no-downtime-caveat", any("≠ 停機" in c for c in e["caveats"]))
    t("ev-mes-only-caveat", any("EventLog" in c for c in e["caveats"]))
    t("ev-rep-agree-no-warning", not any("不一致" in c for c in e["caveats"]))
    e2 = derive_status_events("EID-07905", M, summ, evr, 5)
    t("ev-rep-mismatch-warning", any("不一致" in c for c in e2["caveats"]))
    e0 = derive_status_events("EID-35535", M, [], [], 0, window={"days": 30})
    t("★ ev-zero-signal", e0["found"] and e0["n_events"] == 0 and e0["reason"]
      == "no_events_in_window" and e0["not_measurable"] == []
      and any("不等於「沒有故障」" in c for c in e0["caveats"])
      and ok_payload("equipment_status_events", e0))
    enf = derive_status_events("EID-99999", None, [], [], None, window={"days": 30})
    t("★ ev-eid-not-found", enf["found"] is False and enf["not_measurable"][0]["reason"]
      == "eid_not_found" and ok_payload("equipment_status_events", enf))
    et = derive_status_events("EID-07905", M, summ, evr, 7, caps={"rows": True})
    t("ev-trunc", et["truncated"] and any("全窗計數" in c for c in et["caveats"]))

    # ---- ④ 純函式：modules -----------------------------------------------------------
    edges = [("EID-07905", "FA", "EID-27124", "Aligner52", "equipA", "equipAligner",
              "2021-10-06 17:08:18"),
             ("EID-07905", "FA", "EID-24821", "Aligner46", "equipA", "equipAligner",
              "2021-10-06 17:08:18"),
             ("EID-07905", "FA", "EID-24821", "Aligner46", "equipA", "equipAligner",
              "2018-03-08 11:12:59"),
             ("EID-07905", "FA", None, None, "equipA", None, "2018-03-08 11:12:15"),
             ("EID-07905", "FA", "EID-07905", "FA", "equipA", "equipA", "2010-01-01 00:00:00"),
             ("EID-00001", "Big", "EID-07905", "FA", "equipBig", "equipA",
              "2019-01-01 00:00:00")]
    mo = derive_modules("EID-07905", M, edges)
    t("mod-validator", ok_payload("equipment_modules", mo), mo)
    t("mod-children", [c["eid"] for c in mo["children"]] == ["EID-24821", "EID-27124"]
      and mo["n_children"] == 2 and mo["n_edges"] == 6)
    t("mod-dedup-count", {c["eid"]: c["n_edge_rows"] for c in mo["children"]}
      == {"EID-24821": 2, "EID-27124": 1})
    t("mod-latest-config", all(c["in_latest_config"] for c in mo["children"])
      and mo["latest_config_at"] == "2021-10-06 17:08:18")
    t("mod-first-last", [c for c in mo["children"] if c["eid"] == "EID-24821"][0]
      ["first_linked_at"] == "2018-03-08 11:12:59")
    t("mod-parent", [p["eid"] for p in mo["parents"]] == ["EID-00001"]
      and any("子槽位" in c and "parents" in c for c in mo["caveats"]))
    t("mod-unresolved-and-self", mo["diag"]["n_unresolved"] == 1 and mo["diag"]["n_self_loop"]
      == 1 and any("查不到" in c for c in mo["caveats"]) and any("自己連到自己" in c
                                                                  for c in mo["caveats"]))
    t("mod-class", mo["children"][0]["class"] == "equipAligner")
    m0 = derive_modules("EID-35535", M, [])
    t("★ mod-zero-signal", m0["found"] and m0["n_edges"] == 0 and m0["reason"]
      == "no_module_edges" and ok_payload("equipment_modules", m0)
      and any("獨立機台" in c for c in m0["caveats"]))
    mnf = derive_modules("EID-99999", None, [])
    t("mod-eid-not-found", mnf["not_measurable"][0]["reason"] == "eid_not_found"
      and ok_payload("equipment_modules", mnf))
    t("mod-trunc", derive_modules("EID-07905", M, edges, caps={"edges": True})["truncated"])

    # ---- ⑤ 純函式：calibration 解析 ---------------------------------------------------
    ini = ('[ZeroLeak]\r\nCalibration Check Due Date = "2026-08-14T08:13:16"\r\n'
           'Last Calibration Date = "2026-08-13T08:13:16"\r\nCalibration Part ID = "EID-05743"\r\n'
           'Operator = "someone"\r\nResult = "PASS"\r\n\r\n[Pressures]\r\n'
           'Calibration Due Date = "2026-12-30T10:00:00"\r\nResult = "FAIL"\r\n')
    pr = parse_calibration(ini)
    t("cal-ini-format", pr["format"] == "ini_sections" and pr["n_items"] == 2)
    t("cal-ini-items", [(i["item"], i["result"], i["due"]) for i in pr["items"]]
      == [("ZeroLeak", "PASS", "2026-08-14 08:13:16"), ("Pressures", "FAIL", "2026-12-30 10:00:00")])
    t("cal-ini-last-ref", pr["items"][0]["last_calibrated"] == "2026-08-13 08:13:16"
      and pr["items"][0]["ref_eid"] == "EID-05743")
    t("cal-ini-overall-fail", pr["overall_result"] == "FAIL" and pr["next_due"]
      == "2026-08-14 08:13:16")
    t("★ cal-ini-whitelist（Operator 鍵不出）", "someone" not in json.dumps(pr)
      and "Operator" not in json.dumps(pr))
    t("cal-ini-flat-spaces", parse_calibration(ini.replace("\r\n", " "))["items"] == pr["items"])
    scr = ("Calibration Equipment: EID-05781 FAIL 25/11/2025 08:14:09.017  # Scripter v3 "
           "# $Id: x.pas 1 2009-05-21 someuser $ # Created by  : etest-002 "
           "# Value Measured StdVal Min Max Pass/Fail VRef 2.00711031 2 1.96 2.04 PASS "
           "3V3 3.30457321 3.3 2.97 3.63 PASS Reset 274.683855 274 268.52 279.48 FAIL ")
    ps = parse_calibration(scr, 25732)
    t("cal-scripter-format", ps["format"] == "scripter_report" and ps["overall_result"] == "FAIL")
    t("cal-scripter-head", ps["items"][0] == _item("Calibration Equipment", result="FAIL",
                                                   last_calibrated="2025-11-25 08:14:09",
                                                   ref_eid="EID-05781"))
    t("cal-scripter-meas", [(i["item"], i["value"], i["min"], i["max"], i["result"])
                            for i in ps["items"][1:]]
      == [("VRef", 2.00711031, 1.96, 2.04, "PASS"), ("3V3", 3.30457321, 2.97, 3.63, "PASS"),
          ("Reset", 274.683855, 268.52, 279.48, "FAIL")])
    t("cal-scripter-len", ps["payload_len"] == 25732 and ps["next_due"] is None)
    t("★ cal-no-raw-text-out", all(x not in json.dumps(ps) for x in ("someuser", "etest-002",
                                                                      "$Id", "Scripter")))
    for emp in ("[]", " [] ", "", None, "{}"):
        pe = parse_calibration(emp)
        t(f"★ cal-empty-{emp!r}", pe["format"] == "empty" and pe["payload_empty"]
          and pe["items"] == [] and pe["overall_result"] is None)
    pu = parse_calibration("just some free text without structure")
    t("cal-unrecognized", pu["format"] == "unrecognized" and pu["items"] == []
      and "free text" not in json.dumps(pu))
    many = "".join(f'[S{i}] Result = "PASS" ' for i in range(CAL_ITEMS_CAP + 5))
    pm = parse_calibration(many)
    t("cal-items-cap", pm["items_truncated"] and len(pm["items"]) == CAL_ITEMS_CAP
      and pm["n_items"] == CAL_ITEMS_CAP + 5)
    t("cal-ini-bad-date-none", parse_calibration('[X] Calibration Due Date = "soon"')["items"][0]
      ["due"] is None)

    # ---- ⑥ 純函式：calibration derive -------------------------------------------------
    cw = {"from": "2026-08-25 00:00:00", "to": "2026-09-24 00:00:00", "days": 30}
    c1 = derive_calibration("EID-07905", M, (100, 2, "2026-09-20 00:00:00"),
                            [("2026-09-01 00:00:00", 300, ini), ("2026-09-20 00:00:00", 25732, scr)],
                            [], window=cw)
    t("cal-validator", ok_payload("equipment_calibration", c1), c1)
    t("cal-counts", (c1["n_records"], c1["n_records_in_window"], len(c1["records"]))
      == (100, 2, 2) and c1["last_calibrated_at"] == "2026-09-20 00:00:00")
    t("cal-sorted-desc", [r["recorded_at"] for r in c1["records"]]
      == ["2026-09-20 00:00:00", "2026-09-01 00:00:00"])
    t("cal-registered", c1["registered"] == {"last_calibration": "2025-11-25 08:14:18",
                                             "due": "2026-11-25 08:14:17"})
    t("cal-no-nm", c1["not_measurable"] == [] and c1["reason"] is None and not c1["truncated"])
    c2 = derive_calibration("EID-08158", M, (33, 0, "2015-10-27 09:28:36"), [],
                            [("2015-10-27 09:28:36", 2, "[]")], window=cw)
    t("★ cal-08158-empty-shell", c2["found"] and c2["n_records"] == 33
      and c2["reason"] == "no_calibration_in_window"
      and c2["last_before_window"]["payload_empty"]
      and c2["not_measurable"][0]["reason"] == "calibration_payload_empty"
      and ok_payload("equipment_calibration", c2))
    t("cal-empty-shell-hint-modules", any("equipment_modules" in c for c in c2["caveats"]))
    c3 = derive_calibration("EID-08158", M, (33, 5, "2015-10-27 09:28:36"),
                            [("2015-10-27 09:28:36", 2, "[]")] * 5, [], window=cw)
    t("★ cal-in-window-all-empty", c3["not_measurable"][0]["reason"]
      == "calibration_payload_empty" and c3["n_records_in_window"] == 5
      and c3["reason"] is None)
    c4 = derive_calibration("EID-07905", M, (3, 2, None),
                            [("2026-09-01 00:00:00", 2, "[]"), ("2026-09-02 00:00:00", 300, ini)],
                            [], window=cw)
    t("cal-partial-empty-caveat-only", c4["not_measurable"] == []
      and any("1 筆記錄是空殼" in c for c in c4["caveats"]))
    c0 = derive_calibration("EID-35535", M, (0, 0, None), [], [], window=cw)
    t("★ cal-zero-signal", c0["found"] and c0["n_records"] == 0 and c0["reason"]
      == "no_calibration_records" and c0["not_measurable"] == []
      and ok_payload("equipment_calibration", c0))
    ct = derive_calibration("EID-07905", M, (400, 300, None), [("2026-09-01 00:00:00", 300, ini)],
                            [], window=cw)
    t("cal-trunc", ct["truncated"] and any("只列最近" in c for c in ct["caveats"]))
    cnf = derive_calibration("EID-99999", None, None, [], [], window=cw)
    t("cal-eid-not-found", cnf["not_measurable"][0]["reason"] == "eid_not_found"
      and ok_payload("equipment_calibration", cnf))

    # ---- ⑦ validator 負例 --------------------------------------------------------------
    bad = dict(e)
    bad["diag"] = dict(e["diag"], astPK=1)
    t("validator-rejects-internal-key", not ok_payload("equipment_status_events", bad))
    bad2 = dict(c2)
    bad2["not_measurable"] = [{"field": "x", "reason": "whatever"}]
    t("validator-rejects-open-reason", not ok_payload("equipment_calibration", bad2))
    bad3 = dict(mo)
    bad3["extra"] = 1
    t("validator-rejects-extra-key", not ok_payload("equipment_modules", bad3))
    bad4 = dict(e)
    bad4["events"] = [dict(e["events"][0], duration_s=5)]
    t("validator-rejects-fake-duration", not ok_payload("equipment_status_events", bad4))
    bad5 = dict(c1)
    bad5["records"] = [dict(c1["records"][0], raw="Created by : etest-002")]
    t("validator-rejects-raw-payload", not ok_payload("equipment_calibration", bad5))
    bad6 = dict(e)
    bad6["unavailable_reason"] = "x"
    t("validator-rejects-found-and-unavailable", not ok_payload("equipment_status_events", bad6))

    for tl in TOOLS:
        cur = _FakeCursor({}, guard=tuple(0 for _ in GUARD_KEYS))
        try:
            key = "EID-07905" if tl == "equipment_modules" else {"eid": "EID-07905", "days": 30}
            compute(tl, cur, key)
            t(f"guard-raises-{tl}", False)
        except SchemaDivergence as ex:
            t(f"guard-raises-{tl}", "ABSENT" in str(ex) and len(cur.log) == 1)

    # golden fixture 不存在（例：脫敏公開樹不帶原始撈取 golden）⇒ ⑧⑨ 標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    _fx_absent = [f for f in FIXTURE_FILES.values()
                  if not os.path.exists(os.path.join(BASE, "_fixtures", f))]
    if _fx_absent:
        print("mes_equipment selftest: skipped: %s absent" % ", ".join(sorted(_fx_absent)))
        print(f"mes_equipment selftest: {n} assertions PASS")
        return 0

    # ---- ⑧ fixture：三份 golden 走同一支 derive_*（demo 與 live 同形）------------------
    fx = {tl: _fixture(tl) for tl in TOOLS}
    t("fx-schema-ids", [fx[tl]["schema_id"] for tl in TOOLS]
      == [SCHEMA_EVENTS, SCHEMA_MODULES, SCHEMA_CAL])
    t("fx-builder-reproducible", build_fixtures() == fx)
    for tl in TOOLS:
        t(f"fx-three-eids-{tl}", [g["key"] for g in fx[tl]["golden"]] == list(PROBE_EIDS))
        for g in fx[tl]["golden"]:
            for st in g["steps"].values():
                t(f"fx-cols-{tl}", all(len(row) == len(st["columns"]) for row in st["rows"]))
            t(f"fx-provenance-{tl}", g["source_probe"] == PROBE_SQL and g["captured_at"])
    fxblob = json.dumps(fx, ensure_ascii=False)
    t("★ fx-scrubbed（$Id／Created by）", "$Id:" not in fxblob and "Created by" not in fxblob
      and "jkol" not in fxblob)
    t("★ fx-no-comment-column", "comment_head" not in fxblob and "Could not scan" not in fxblob)
    evg = {g["key"]: g for g in fx["equipment_status_events"]["golden"]}
    t("★ fx-ev-Dx", {k: (g["n_events_probe_Dx"], g["n_rep_probe_Dx"]) for k, g in evg.items()}
      == {"EID-07905": (23, 23), "EID-08158": (363, 363), "EID-35535": (0, 0)})
    t("fx-ev-sample-sizes", {k: len(g["steps"]["EVENTS"]["rows"]) for k, g in evg.items()}
      == {"EID-07905": 23, "EID-08158": 60, "EID-35535": 0})
    t("fx-ev-labels-matched", all(r[4] for g in evg.values() for r in g["steps"]["EVENTS"]["rows"]))
    modg = {g["key"]: g for g in fx["equipment_modules"]["golden"]}
    t("★ fx-mod-Dx-equals-D6", all(len(g["steps"]["EDGES"]["rows"]) == g["n_edges_probe_Dx"]
                                   for g in modg.values())
      and {k: g["n_edges_probe_Dx"] for k, g in modg.items()}
      == {"EID-07905": 38, "EID-08158": 6, "EID-35535": 0})
    calg = {g["key"]: g for g in fx["equipment_calibration"]["golden"]}
    t("★ fx-cal-Dx", {k: g["n_cal_probe_Dx"] for k, g in calg.items()}
      == {"EID-07905": 100, "EID-08158": 33, "EID-35535": 0})
    t("fx-cal-08158-shell", all(r[1] == "2" and r[2] == "[]"
                                for r in calg["EID-08158"]["steps"]["CAL"]["rows"]))

    # demo：events
    def dl(tl, eid, days=None):
        k = eid if tl == "equipment_modules" else {"eid": eid, "days": days or DAYS_DEFAULT}
        return demo_lookup(tl, fx[tl], k)

    d365 = dl("equipment_status_events", "EID-07905", 365)
    t("fx-ev-07905-365", d365["n_events"] == 23 and len(d365["events"]) == 23
      and d365["n_events_rep"] == 23 and not d365["truncated"]
      and ok_payload("equipment_status_events", d365))
    t("★ fx-ev-07905-codes", [(b["code"], b["n"]) for b in d365["by_code"]]
      == [("eqfAttachRig_CalibrationFailure", 13), ("eqfAttachRig_QIDConnectionError", 10)])
    t("fx-ev-07905-label", d365["by_code"][1]["label"] == "Could not connect to QID")
    d30 = dl("equipment_status_events", "EID-07905", 30)
    t("fx-ev-07905-30d", d30["n_events"] == 2 and d30["window"]["days"] == 30
      and d30["window"]["to"] == "2026-08-13 11:31:05" and not d30["truncated"]
      and d30["n_events_rep"] is None and ok_payload("equipment_status_events", d30))
    e619 = dl("equipment_status_events", "EID-08158", 365)
    t("★ fx-ev-08158-365-truncated-honest", e619["n_events"] == 363 and len(e619["events"]) == 60
      and e619["truncated"] and any("樣本彙總" in c for c in e619["caveats"])
      and ok_payload("equipment_status_events", e619))
    t("fx-ev-08158-top", e619["by_code"][0]["code"] == "eqfLT_clampingFailure"
      and e619["by_code"][0]["n"] == 29)
    e619s = dl("equipment_status_events", "EID-08158", 30)
    t("★ fx-ev-08158-30d-exact（樣本涵蓋到窗起點⇒計數精確）", e619s["n_events"] == 22
      and len(e619s["events"]) == 22 and not e619s["truncated"] and e619s["n_events_rep"] is None
      and ok_payload("equipment_status_events", e619s))
    e619y = dl("equipment_status_events", "EID-08158", 120)
    t("★ fx-ev-08158-120d-lower-bound", e619y["truncated"] and e619y["n_events"] == 60
      and any("是下界" in c for c in e619y["caveats"]))
    z = dl("equipment_status_events", ZERO_SIGNAL_EID, 365)
    t("★ fx-ev-35535-zero-signal", z["found"] and z["n_events"] == 0
      and z["reason"] == "no_events_in_window" and z["n_events_rep"] == 0
      and z["equipment"]["name"] == "Profiler CT300 (PLANT1) #2"
      and any("不等於「沒有故障」" in c for c in z["caveats"])
      and ok_payload("equipment_status_events", z))
    t("fx-ev-miss-none", dl("equipment_status_events", "EID-00000") is None)

    # demo：modules
    m402 = dl("equipment_modules", "EID-07905")
    t("fx-mod-07905", ok_payload("equipment_modules", m402) and m402["n_edges"] == 38
      and m402["n_parents"] == 0)
    t("★ fx-mod-07905-children", sorted(c["eid"] for c in m402["children"])
      == ["EID-01450", "EID-01529", "EID-08961", "EID-24821", "EID-26920", "EID-27004",
          "EID-27124", "EID-38173"])
    t("fx-mod-07905-latest", sorted(c["eid"] for c in m402["children"] if c["in_latest_config"])
      == ["EID-24821", "EID-27124"] and m402["latest_config_at"] == "2021-10-06 17:08:18")
    t("fx-mod-07905-unresolved", m402["diag"]["n_unresolved"] == 10
      and sum(c["n_edge_rows"] for c in m402["children"]) + 10 == 38)
    m619 = dl("equipment_modules", "EID-08158")
    t("★ fx-mod-08158-nests", sorted(c["eid"] for c in m619["children"])
      == ["EID-05707", "EID-07757"] and all(c["name"] == "Leak Test Nest" for c in m619["children"])
      and all(c["n_edge_rows"] == 3 for c in m619["children"]))
    mz = dl("equipment_modules", ZERO_SIGNAL_EID)
    t("★ fx-mod-35535-zero-signal", mz["found"] and mz["n_edges"] == 0
      and mz["reason"] == "no_module_edges" and ok_payload("equipment_modules", mz))
    t("fx-mod-demo-caveat", any("demo" in c for c in m402["caveats"]))

    # demo：calibration
    k365 = dl("equipment_calibration", "EID-07905", 365)
    t("fx-cal-07905-365", ok_payload("equipment_calibration", k365)
      and k365["n_records"] == 100 and k365["n_records_in_window"] == 4
      and len(k365["records"]) == 4 and not k365["truncated"])
    t("★ fx-cal-07905-scripter", all(r["format"] == "scripter_report" and r["overall_result"]
                                     == "FAIL" for r in k365["records"])
      and k365["records"][0]["items"][0]["ref_eid"] == "EID-05781"
      and k365["records"][0]["payload_len"] == 25732)
    t("fx-cal-07905-meas", [i["item"] for i in k365["records"][0]["items"][1:4]]
      == ["VRef", "3V3", "Reset"])
    t("fx-cal-07905-registered", k365["registered"]["due"] == "2026-11-25 08:14:17")
    k30 = dl("equipment_calibration", "EID-07905", 30)
    t("fx-cal-07905-30d-last-before", k30["n_records_in_window"] == 0
      and k30["reason"] == "no_calibration_in_window"
      and k30["last_before_window"]["recorded_at"] == "2025-11-25 08:14:18"
      and k30["not_measurable"] == [] and ok_payload("equipment_calibration", k30))
    k619 = dl("equipment_calibration", "EID-08158", 365)
    t("★ fx-cal-08158-empty-shell", k619["found"] and k619["n_records"] == 33
      and k619["last_before_window"]["payload_empty"]
      and k619["last_before_window"]["format"] == "empty"
      and k619["not_measurable"][0]["reason"] == "calibration_payload_empty"
      and ok_payload("equipment_calibration", k619))
    kz = dl("equipment_calibration", ZERO_SIGNAL_EID, 365)
    t("★ fx-cal-35535-zero-signal", kz["found"] and kz["n_records"] == 0
      and kz["reason"] == "no_calibration_records" and kz["records"] == []
      and kz["not_measurable"] == [] and ok_payload("equipment_calibration", kz))
    t("fx-cal-demo-caveat", any("1200" in c for c in k365["caveats"]))
    t("fx-cal-no-raw", all(x not in json.dumps(k365) for x in ("Scripter", "scrubbed", "aligner46")))

    # ---- ⑨ 編排（假游標）：順序、參數、早停 --------------------------------------------
    now = datetime(2026, 8, 14, 12, 0, 0)
    g = evg["EID-07905"]["steps"]
    cur = _FakeCursor({SQL_EQ_MASTER: M, SQL_EV_SUMMARY: summ, SQL_EV_ROWS: evr,
                       SQL_EV_REP_COUNT: (7,)})
    r = compute_status_events(cur, "EID-07905", 30, now=now)
    t("cmp-ev-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_EQ_MASTER, SQL_EV_SUMMARY,
                                                  SQL_EV_ROWS, SQL_EV_REP_COUNT])
    t("cmp-ev-params", cur.log[1][1] == ("EID-07905",)
      and cur.log[2][1] == (172, "2026-08-25 12:00:00", "2026-08-14 12:00:00")
      and cur.log[3][1] == (EV_CAP, 172, "2026-08-25 12:00:00", "2026-08-14 12:00:00")
      and cur.log[4][1] == (172, "2026-08-25 12:00:00", "2026-08-14 12:00:00"))
    t("cmp-ev-valid", ok_payload("equipment_status_events", r) and r["n_events"] == 7
      and r["n_events_rep"] == 7 and not r["truncated"])
    cur = _FakeCursor({SQL_EQ_MASTER: M, SQL_EV_SUMMARY: [], SQL_EV_REP_COUNT: (0,)})
    r0 = compute_status_events(cur, "EID-35535", 30, now=now)
    t("cmp-ev-empty-skips-rows", SQL_EV_ROWS not in [s for s, _ in cur.log]
      and r0["reason"] == "no_events_in_window")
    cur = _FakeCursor({SQL_EQ_MASTER: None})
    rn = compute_status_events(cur, "EID-99999", 30, now=now)
    t("★ cmp-ev-not-found-stops", [s for s, _ in cur.log] == [SQL_GUARD, SQL_EQ_MASTER]
      and rn["not_measurable"][0]["reason"] == "eid_not_found")
    big = [("2026-09-10 00:00:00", "In Service", "eqfA", None, "A")] * EV_CAP
    cur = _FakeCursor({SQL_EQ_MASTER: M, SQL_EV_SUMMARY: [("eqfA", None, "A", "In Service",
                                                           "500", "2026-09-01 00:00:00",
                                                           "2026-09-10 00:00:00")],
                       SQL_EV_ROWS: big, SQL_EV_REP_COUNT: (500,)})
    rb = compute_status_events(cur, "EID-07905", 30, now=now)
    t("cmp-ev-cap-truncated", rb["truncated"] and rb["n_events"] == 500
      and len(rb["events"]) == EV_CAP)

    cur = _FakeCursor({SQL_EQ_MASTER: M, SQL_MOD_EDGES: edges})
    rm = compute_modules(cur, "EID-07905")
    t("cmp-mod-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_EQ_MASTER, SQL_MOD_EDGES]
      and cur.log[2][1] == (MOD_CAP, 172, 172))
    t("cmp-mod-valid", ok_payload("equipment_modules", rm) and rm["n_children"] == 2)
    cur = _FakeCursor({SQL_EQ_MASTER: None})
    t("cmp-mod-not-found", compute_modules(cur, "EID-99999")["not_measurable"][0]["reason"]
      == "eid_not_found" and len(cur.log) == 2)

    gcal = calg["EID-07905"]["steps"]["CAL"]["rows"]
    cur = _FakeCursor({SQL_EQ_MASTER: M, SQL_CAL_COUNT: (100, 2, "2025-11-25 08:14:18"),
                       SQL_CAL_ROWS: gcal[:2]})
    rc = compute_calibration(cur, "EID-07905", 365, now=now)
    t("cmp-cal-order", [s for s, _ in cur.log] == [SQL_GUARD, SQL_EQ_MASTER, SQL_CAL_COUNT,
                                                   SQL_CAL_ROWS])
    t("cmp-cal-params", cur.log[2][1] == ("2025-09-24 12:00:00", 172, "2026-08-14 12:00:00")
      and cur.log[3][1] == (CAL_CAP, 172, "2025-09-24 12:00:00", "2026-08-14 12:00:00"))
    t("cmp-cal-valid", ok_payload("equipment_calibration", rc) and len(rc["records"]) == 2)
    shell = calg["EID-08158"]["steps"]["CAL"]["rows"]
    M619 = (45, "EID-08158", "PLOVER: Lam Leak Test Machine v2 #1", "In Service",
            "Volume Production", "equipLamLeakTest", "segLamLeakTest",
            "2010-07-07 15:09:58", "2026-12-31 00:00:00")
    cur = _FakeCursor({SQL_EQ_MASTER: M619, SQL_CAL_COUNT: (33, 0, shell[0][0]),
                       SQL_CAL_LAST: shell[:1]})
    rs = compute_calibration(cur, "EID-08158", 30, now=now)
    t("★ cmp-cal-08158-last-only", [s for s, _ in cur.log][-1] is SQL_CAL_LAST
      and SQL_CAL_ROWS not in [s for s, _ in cur.log]
      and cur.log[-1][1] == (1, 45, "2026-08-25 12:00:00")
      and rs["not_measurable"][0]["reason"] == "calibration_payload_empty")
    cur = _FakeCursor({SQL_EQ_MASTER: M, SQL_CAL_COUNT: (0, None, None)})
    rz = compute_calibration(cur, "EID-35535", 30, now=now)
    t("cmp-cal-zero-no-row-queries", [s for s, _ in cur.log][-1] is SQL_CAL_COUNT
      and rz["reason"] == "no_calibration_records")
    print(f"mes_equipment selftest: {n} assertions PASS")
    return 0


def _demo_cli(argv):
    tool, eid = argv[0], argv[1]
    days = argv[2] if len(argv) > 2 else None
    key, err = normalize_args(tool, eid, days)
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
    if argv[:1] == ["--demo"] and len(argv) >= 3:
        return _demo_cli(argv[1:])
    print("usage: python mes_equipment.py --selftest | --sql | --build-fixtures | "
          "--demo <tool> <eid> [days]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
