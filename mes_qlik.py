#!/usr/bin/env python3
"""Tendline Board — Qlik 口徑（first-FAIL ＋ RTY）站級/線級 良率·產出 計算核心。

**這是什麼**：主力 Qlik app（`SALTMARSH PH Engineering Dashboard v3.0`）的 load script 活管線
`[stpTestResult-First FAIL in Production line]` ＋ RTY 連乘 KPI 的 **SQL/Python 複刻**。
**不是另一種口徑發明**——輸出＝公司語意基準本身。本模組與 `mes_yield.py`（raw 口徑、凍結）
平行存在＝雙軌制（D1）：站級/線級 yield·output 走本模組；**機台級 yield 與整個設備軸
（稼動/downtime/MTBF）維持 raw、一個位元不動**。

語意權威（分歧一律以這兩份為準、不以本檔註解為準）：
  · `_QLIK-RECON-PROBES3.sql`     — 逐段權威邏輯（[PK]/[W]/[H]/[PR]/#cls3/#endrow/#ff/
                                    #anch/#disp/#agg/[3F]/[4A]/[4B]/[4C]），2026-07-31 已對
                                    43 站 `d_total/d_pass/d_fail` **全 0**、7 個 RTY KPI
                                    |Δ|≤0.0005pp 逐位驗證。
  · `_QLIK-RECON-MAIN.md` §9.1（地雷 A–E）與 §10（定論）。
驗收對表靶：`_QLIK-RECON-SCREEN-2026-07-31.md`（固定窗 [2026-07-24, 07-31) 的 42 列＋7 KPI）。

一句話語意：**「整顆 lot 生涯只死一次」的重新標記**——per-AllPHID 取末次列 → per-lot 在末次
FAIL 中取 **`Min(stpPK)`** 標 FAIL、**該 lot 其餘所有站一律 PASS**；**在全史上算、窗只篩顯示**
（窗錨＝per-AllPHID 的 Start 側首次有效時戳）。

★ `cap` 語意（§4.2；探針與 live 的唯一刻意差，**不是 bug**）
  · 探針對表：`cap` ＝ 該次 Qlik reload 時刻（first-FAIL／末次收斂都是 **as-of** 概念，
    capping 才是對「那個螢幕切面」的忠實複刻）。
  · live 模組：`cap` 預設＝查詢當下（`SELECT GETDATE()` 一次、全程沿用）＝「as-of now」的
    公司口徑數字。Board 是活儀表板、不是凍結切面。
  · 後果：live 數字與 Qlik 螢幕可有**合法時差**（Qlik ≈13:00 日更、Board 即時）。reload 之後的
    復測只會讓 FAIL 遷移/變少（復測 PASS ⇒ fail 只減不增）；`matStatus` 是現況欄、無法凍結
    ＝唯一未消除的漂移（MAIN OPEN-4）。**對表驗收一律 `--cap <reload 時刻>`。**

★ 誠實鐵則
  · 無資料顯 `None`（消費面顯「—」），**絕不 0/100%**（[4B] outer guard 的 NULL 語意照搬）。
  · QAConfig Excel 人工改判／golden-dummy 移除／Process12 designation 過濾＝**不可複刻**，
    照 PROBES3 掛 `diag.qaconfig/golden/desig_filter='not_replicated'` 自揭、**不假裝有做**
    （其零效應證據＝MAIN §10／T-Q2 CLOSED：QAConfig 20 筆全早於 app 資料窗、來源檔 2024-01 停更）。
  · 欄位 ABSENT ⇒ **不默用 raw**，對應分支標 `SKIPPED(...)` 進 `diag.eol_branch/assy_ts_branch`。

★ perf lore（B4/M4 教訓，改本檔前先讀）
  · **兩段式母體收斂**：先 [W] 收窗內 lot 集合 → 只掃這些 lot 的全史生涯（[H] 的 `#lots`
    設計）。PROBES3 實測全流程 ~10 秒（[PK] 2.7s／[W] 1.4s／[H] 3.1s／[PR] 47ms／derive 2.9s）。
  · 每條 SQL 尾 `OPTION (RECOMPILE)`；**絕不在 5 表大 view 上做 DISTINCT/GROUP BY**
    （去重與聚合一律 Python／#temp）；**絕不對 view 塞動態 lot IN-list**（lot 清單走 `#temp` join）；
    `COLLATE` 掛 **#temp 側**（保留 view 側 seek）。
  · 本管線只用 `stpStartTime`、**不碰 `stpEndTime`**。日後若有人想加 end 過濾：先讀
    [[mes-zero-length-segments]]（end-filter 一律 `>=`，`>` 會刪掉合法零工期段）。

★ 唯讀：對 view 只有 SELECT；唯一 DDL/DML 在 `#temp`（tempdb 不算寫 MES）。
★ 凍結模組 `mes_timeline.py` / `mes_yield.py`：本檔**不 import、不執行**——`_connect()` 是
  自足複本（形狀逐字照抄 `mes_timeline._connect`，含 `SET ARITHABORT ON`）＝B1 自足交付要求；
  凍結模組零 diff、零耦合。

CLI：
    python mes_qlik.py --days 7 [--json]
    python mes_qlik.py --start 2026-07-24 --end 2026-07-31 [--cap "2026-07-31T13:00"] [--json]
    python mes_qlik.py --selftest          # 純離線合成資料、不連 MES
`--json` 輸出＝§3.4 canonical（＝snapshot 檔格式）。
"""
import argparse
import sys
import time
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# 0. 連線（自足複本；形狀＝mes_timeline._connect，含 SET ARITHABORT ON）
# =============================================================================
SERVER = "mes-sql.example.com"
DATABASE = "MES_DB"
DRIVER = "{ODBC Driver 17 for SQL Server}"
QUERY_TIMEOUT_SEC = 180          # [H] 全史拉取實測 ~3s；留寬裕上界防呆（app 端另有預算）


def _conn_str():
    return (f"Driver={DRIVER};Server={SERVER};Database={DATABASE};"
            "Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;")


def _connect(timeout=QUERY_TIMEOUT_SEC):
    """唯一 DB 入口（Kerberos 整合驗證、唯讀）。

    ★ `SET ARITHABORT ON`：ODBC Driver 預設 OFF、SSMS 預設 ON ⇒ 同一條 SQL 走不同計劃
      ＝經典「SSMS 快 app 慢」根因（2026-06-29 session B2 實證）。固定為 ON。
    ★ 本函式是 `mes_timeline._connect` 的**自足複本**（凍結模組不 import／不執行）。
      連線字串常數同步變更時兩邊都要改——凍結模組已凍結、實務上不會變。
    ★ 同一條連線必須貫穿整個 `compute_qlik_grid`（`#temp` 存活於 connection 生命週期）。
    """
    import pyodbc
    conn = pyodbc.connect(_conn_str(), autocommit=True)
    conn.timeout = timeout
    cur = conn.cursor()
    cur.execute("SET ARITHABORT ON; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
    return conn, cur


# =============================================================================
# 1. 人工審定常數（§3.1；逐字從 PROBES3 抽出。改它＝人工動作＋重跑對表）
# =============================================================================
CALIBER_QLIK = "qlik first-FAIL (company semantic baseline; SALTMARSH PH Engineering Dashboard v3.0)"
SCHEMA_ID = "board.qlik_grid.v1"

QLIK_EPOCH = "2023-12-12T00:00:00"     # loader 全史起點（照抄、不可調）

MLC_WHITELIST = ("assyModule", "assyUnit", "assyPackagedUnit",
                 "assyEndOfLine", "assyProductionReliabilityTesting")
EXCLUDED_CLASSES = ("segScrapMaterial", "segPostLaminationPeelTestVisualInspection",
                    "segUnitDieLevelVisualPQInspection")
LOT_PREFIXES = ("J0", "V0")            # Process01 母體；顯示層另篩 ptype='B'
RESULT_KEEP = ("PASS", "FAIL")         # NULL 整筆不進母體
RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"

PACK_CLASS = "segVacuumPackInspect"   # [PK] 首次包裝界
PACK_MLC = "assyPackagedUnit"
ASSY_MLC = "assyProductionReliabilityTesting"      # 站名改 segASSY、時戳改 rqsDateStarted、判定恆 FAIL
EOL_MLC = "assyEndOfLine"                         # matStatus∈(WIP,Available) → 強制 PASS
EOL_PASS_STATUSES = ("WIP", "Available")
FRESH_CLASSES = ("segShipPackaging", "segReceiptMaterialsIntoStores")   # SortingOrFresh 分支 2
FORCED_FAIL_CLASSES = ("segPostLamInspection",
                       "segPostLaminationPeelTestVisualInspection")     # 後者=死碼（Process01 已排除）
ASSY_STATION = "segASSY"

# ★ 站序權威＝OVERALL YIELD（物件 dkLKm）運算式的 43 因子序列（PROBES3 #fac 逐字拷貝）。
#   不得改用外層 '-' 守衛白名單（過時：PH 24 vs 26、PC 11 vs 12、OVERALL 41 vs 43）、
#   不得用螢幕列號回推站序、**絕不 runtime discovery**（M4 教訓：自動發現會讓站靜默消失）。
#   ord 同時＝螢幕表維度編號（螢幕缺 30＝資料現實 den=0，非抄錄遺漏）。
QLIK_STATIONS = (
    (1, "segSerialisation", "assyModule"),
    (2, "segFramePlasma", "assyModule"),
    (3, "segFrameBonding", "assyModule"),
    (4, "segBondedFrameVisualInspection", "assyModule"),
    (5, "segStraightnessCheck", "assyModule"),
    (6, "segPreLamPlasma", "assyModule"),
    (7, "segLamination", "assyModule"),
    (8, "segPostLaminationModuleVisualInspection", "assyModule"),
    (9, "segLamLeakTest", "assyModule"),
    (10, "segDiePlacement", "assyModule"),
    (11, "segPreCureVisualInspection", "assyModule"),
    (12, "segAttachCureOven", "assyModule"),
    (13, "segPostCureAlignCheck", "assyModule"),
    (14, "segFlexBonding", "assyModule"),
    (15, "segPostFlexBondingVisualInspection", "assyModule"),
    (16, "segWireBonding", "assyModule"),
    (17, "segWireBondingInspection", "assyModule"),
    (18, "segAuxElectricalTest", "assyModule"),
    (19, "segPreEncapPlasma", "assyModule"),
    (20, "segSealDispense", "assyModule"),
    (21, "segUVCure", "assyModule"),
    (22, "segSealDispenseInspection", "assyModule"),
    (23, "segPotting", "assyModule"),
    (24, "segThermalCure", "assyModule"),
    (25, "segPottingVisualInspection", "assyModule"),
    (26, "segModuleElectricalTest", "assyModule"),
    (27, "segPlasmaActivation", "assyUnit"),
    (28, "segManifoldAttach", "assyUnit"),
    (29, "segChassisAttach", "assyUnit"),
    (30, "segFinalAssembly", "assyUnit"),
    (31, "segDryUnitWeightMeasurement", "assyUnit"),
    (32, "segCalibrationTest", "assyUnit"),
    (33, "segAutomatedOQC", "assyUnit"),
    (34, "segFinalOQC", "assyUnit"),
    (35, "segCleanTest", "assyUnit"),
    (36, "segWetUnitWeightMeasurement", "assyUnit"),
    (37, "segPreDATVisualInspection", "assyUnit"),
    (38, "segDieArrayTaping", "assyUnit"),
    (39, "segQASetup", "assyPackagedUnit"),
    (40, "segFlexContactPadTapingAndPaperGuardAttachment", "assyPackagedUnit"),
    (41, "segPackagedUnitFinalVisualInspection", "assyPackagedUnit"),
    (42, "segAttachProtectiveCover", "assyPackagedUnit"),
    (43, "segVacuumPackInspect", "assyPackagedUnit"),
)

# 公司慣用顯示名（Product Flow List `Plover` 表、2026-08-01 已與 43 站逐列證實等價；ord→顯示名）。
# UI 顯示用；canonical id（segXxx）永遠並存於 title/tooltip，**不翻譯、不被取代**。
QLIK_DISPLAY = {
    1: "Serialisation", 2: "Frame Plasma",
    3: "Frame Fast Cure Bonding", 4: "Bonded Frame Post Cure Visual Inspection",
    5: "Bonded Frame Straightness Measurement", 6: "Pre Lam Plasma",
    7: "Automated Lamination", 8: "Post Lamination Visual Inspection",
    9: "Post Lam Leak Test", 10: "Die Placement", 11: "Pre Cure Die Visual Inspection",
    12: "Die Placement Cure", 13: "Post Cure X Y Axis Die Position Measurement",
    14: "Flex Bonding", 15: "Post Flex Bonding Visual Inspection", 16: "Wirebond",
    17: "Post Wirebond Visual Inspection", 18: "Post Wirebond Module Electrical Test",
    19: "Pre SealDispense Plasma Activation", 20: "Grouting and SealDispense",
    21: "Grouting and SealDispense UV Cure",
    22: "Post Grouting and SealDispense Visual Inspection", 23: "Potting",
    24: "SealDispense and Grouting Thermal Cure", 25: "Potting Visual Inspection",
    26: "Module Electrical Test", 27: "Pre CAL Module Plasma Activation",
    28: "Manifold Attach", 29: "Chassis Attach",
    30: "Unit Assembly", 31: "Dry Unit Weight Measurement (pre CAL)",
    32: "Unit Calibration Test", 33: "Automated OQC Test",
    34: "Visual OQC Test", 35: "Fluid Clean and E-Test",
    36: "Wet Unit Weight Measurement (post CLN)",
    37: "Unit Pre Die Array Taping Visual Inspection", 38: "Die Array Taping",
    39: "QA Chip Configuration", 40: "Flex Contact Pad Taping and Paper Guard Attachment",
    41: "Packaged Unit Final Visual Inspection", 42: "Attach Protective Cover",
    43: "Vacuum Pack Inspect",
}

# ★ Kestrel 站序權威＝KESTREL OVERALL YIELD（物件 ZsmHb）運算式實際相乘的 45 因子序列。
#   不得改用外層 `=0,'-'` 守衛白名單（過時：只剩 42 項）；45 才是站序、42 不是。
#   `[Product Type]={'M','H'}`（不只 M）；#101 的 Kestrel 白名單直接沿用本常數，不得再抄。
KESTREL_STATIONS = (
    (1, "segSerialisation", "assyModule"),
    (2, "segFramePlasma", "assyModule"),
    (3, "segFrameBonding", "assyModule"),
    (4, "segBondedFrameVisualInspection", "assyModule"),
    (5, "segStraightnessCheck", "assyModule"),
    (6, "segPreLamPlasma", "assyModule"),
    (7, "segLamination", "assyModule"),
    (8, "segPostLaminationModuleVisualInspection", "assyModule"),
    (9, "segLamLeakTest", "assyModule"),
    (10, "segDiePlacement", "assyModule"),
    (11, "segPreCureVisualInspection", "assyModule"),
    (12, "segAttachCureOven", "assyModule"),
    (13, "segPostCureAlignCheck", "assyModule"),
    (14, "segFlexBonding", "assyModule"),
    (15, "segPostFlexBondingVisualInspection", "assyModule"),
    (16, "segWireBonding", "assyModule"),
    (17, "segWireBondingInspection", "assyModule"),
    (18, "segGrout", "assyModule"),
    (19, "segUVCurePostGrout", "assyModule"),
    (20, "segPreEncapPlasma", "assyModule"),
    (21, "segSealDispenseDamAndFill", "assyModule"),
    (22, "segUVCurePostDamAndFill", "assyModule"),
    (23, "segSealDispenseInspection", "assyModule"),
    (24, "segPotting", "assyModule"),
    (25, "segThermalCure", "assyModule"),
    (26, "segPottingVisualInspection", "assyModule"),
    (27, "segPostThermalCureVisualInspection", "assyModule"),
    (28, "segModuleElectricalTest", "assyModule"),
    (29, "segCreateUnitAssembly", "assyUnit"),
    (30, "segFinalAssembly", "assyUnit"),
    (31, "segDryUnitWeightMeasurement", "assyUnit"),
    (32, "segPHCSoak", "assyUnit"),
    (33, "segPHCRinse", "assyUnit"),
    (34, "segPHCBurnIn", "assyUnit"),
    (35, "segAssemblyLeakTest", "assyUnit"),
    (36, "segCalibrationTest", "assyUnit"),
    (37, "segAutomatedOQC", "assyUnit"),
    (38, "segFinalOQC", "assyUnit"),
    (39, "segCleanTest", "assyUnit"),
    (40, "segWetUnitWeightMeasurement", "assyUnit"),
    (41, "segUnitInspection", "assyUnit"),
    (42, "segTopSealLube", "assyUnit"),
    (43, "segQASetup", "assyPackagedUnit"),
    (44, "segPackagedUnitFinalVisualInspection", "assyPackagedUnit"),
    (45, "segVacuumPackInspect", "assyPackagedUnit"),
)

# ★ Kestrel 12 個獨有站的**推導**顯示名（cls → 顯示名；#105，Chris 2026-09-08 裁「先用推導名」）。
#   來源：**Fable 2026-09-08 依 `QLIK_DISPLAY` 的命名風格由 canonical id 推導**，不是公司正式名。
#   正式名住在 Qlik server `04.Data\SaltmarshModuleEngineeringDashBoardV3.0\Product Flow List.xlsx`
#   工作表 `Kestrel Fresh`、欄 `Kestrel Work Flow (FRESH)`——Chris 抄回來之後**整批取代本表**
#   並把來源翻成 "qlik"（`_KESTREL_DISPLAY_SRC` 那一行的判斷），本註解一併改掉。
#   ⚠ 只放 **Kestrel 獨有**的 12 站；33 個共同站永遠沿用 `QLIK_DISPLAY`（`_DISPLAY_BY_CLS`
#     優先於本表），不得在這裡放第二份共同站名字（那會變成第二份會漂移的事實）。
KESTREL_DISPLAY_DERIVED = {
    "segGrout": "Grouting",
    "segUVCurePostGrout": "Post Grout UV Cure",
    "segSealDispenseDamAndFill": "SealDispense Dam and Fill",
    "segUVCurePostDamAndFill": "Post Dam and Fill UV Cure",
    "segPostThermalCureVisualInspection": "Post Thermal Cure Visual Inspection",
    "segCreateUnitAssembly": "Create Unit Assembly",
    "segPHCSoak": "PHC Soak",
    "segPHCRinse": "PHC Rinse",
    "segPHCBurnIn": "PHC Burn-In",
    "segAssemblyLeakTest": "Assembly Leak Test",
    "segUnitInspection": "Unit Inspection",
    "segTopSealLube": "Top O-ring Lubrication",
}

# 7 KPI ＝ ord 閉區間（PROBES3 #fx 逐字；因子數自檢 43/26/12/5/9/17/1 進 diag）
KPI_GROUPS = (("OVERALL", 1, 43), ("PH", 1, 26), ("PC", 27, 38), ("PPC", 39, 43),
              ("BEFORE_DA", 1, 9), ("FROM_DA", 10, 26), ("VOQC", 34, 34))
KPI_EXPECTED_N = {"OVERALL": 43, "PH": 26, "PC": 12, "PPC": 5,
                  "BEFORE_DA": 9, "FROM_DA": 17, "VOQC": 1}

# Kestrel 的 7 KPI 因子序（§11：ZsmHb / fbptLf / cYkNKKr / EYdCeQ / buxv / QHbJMS / JxgrAa）。
# 這是產品 profile 的資料，不碰既有 Plover 常數或共用母體。
KESTREL_KPI_GROUPS = ("OVERALL", 1, 45), ("PH", 1, 28), ("PC", 29, 42), \
                     ("PPC", 43, 45), ("BEFORE_DA", 1, 9), ("FROM_DA", 10, 28), \
                     ("AutoOQC", 37, 37)
KESTREL_KPI_EXPECTED_N = {"OVERALL": 45, "PH": 28, "PC": 14, "PPC": 3,
                          "BEFORE_DA": 9, "FROM_DA": 19, "AutoOQC": 1}

_IDENTITY_NEED = ("OVERALL", "PH", "PC", "PPC", "BEFORE_DA", "FROM_DA")
_DISPLAY_BY_CLS = {cls: QLIK_DISPLAY.get(o) for o, cls, _mcl in QLIK_STATIONS}

# #105：Kestrel 的顯示名＝**共同站沿用 Qlik 官方名 ＞ 獨有站用推導名**（兩張表的優先序
# 只寫在這裡一次）。`_KESTREL_DISPLAY_SRC` 是同一把尺算出來的來源標記（"qlik"／"derived"／
# 兩張表都查不到＝None ⇒ `build_grid()` 才會掛 display_pending；#105 之後 45 站全部有名）。
_KESTREL_DISPLAY = {o: (_DISPLAY_BY_CLS.get(cls) or KESTREL_DISPLAY_DERIVED.get(cls))
                    for o, cls, _mcl in KESTREL_STATIONS}
_KESTREL_DISPLAY_SRC = {
    o: ("qlik" if _DISPLAY_BY_CLS.get(cls)
        else ("derived" if KESTREL_DISPLAY_DERIVED.get(cls) else None))
    for o, cls, _mcl in KESTREL_STATIONS}
_QLIK_DISPLAY_SRC = {o: "qlik" for o in QLIK_DISPLAY}

# 產品設定檔是兩張平行表唯一的設定來源。`display_src` 與 `display` 同一把 ord 尺、加性欄；
# 兩張表都查不到名字時 `build_grid()` 才保留 canonical id 並明示 display_pending。
PRODUCTS = {
    "plover": {
        "ptypes": ("J",), "stations": QLIK_STATIONS, "groups": KPI_GROUPS,
        "expected": KPI_EXPECTED_N, "display": QLIK_DISPLAY,
        "display_src": _QLIK_DISPLAY_SRC,
        "chips": (("PH", "qk_kpi_ph"), ("PC", "qk_kpi_pc"), ("PPC", "qk_kpi_ppc"),
                  ("BEFORE_DA", "qk_kpi_bda"), ("FROM_DA", "qk_kpi_fda"),
                  ("VOQC", "qk_kpi_voqc")),
        "identity_need": _IDENTITY_NEED,
    },
    "kestrel": {
        "ptypes": ("V",), "stations": KESTREL_STATIONS, "groups": KESTREL_KPI_GROUPS,
        "expected": KESTREL_KPI_EXPECTED_N,
        "display": _KESTREL_DISPLAY, "display_src": _KESTREL_DISPLAY_SRC,
        "chips": (("PH", "qk_kpi_ph"), ("PC", "qk_kpi_pc"), ("PPC", "qk_kpi_ppc"),
                  ("BEFORE_DA", "qk_kpi_bda"), ("FROM_DA", "qk_kpi_fda"),
                  ("AutoOQC", "qk_kpi_autooqc")),
        "identity_need": _IDENTITY_NEED,
    },
}

# first-FAIL 落點桶（[3F] 四分類）。on_screen 不進 absorption 回傳。
BUCKET_ON_SCREEN = "on_screen"
BUCKET_SORTING = "sorting_cut"
BUCKET_NON_B = "non_b"
BUCKET_INVISIBLE = "invisible_station"

def _whitelist_of(stations):
    """站清單 → `(cls, mcl)` 白名單（profile 專用；無 I/O）。"""
    return frozenset((cls, mcl) for _o, cls, mcl in stations)


def _ord_by_key_of(stations):
    """站清單 → `(cls, mcl): ord`（profile 專用；無 I/O）。"""
    return {(cls, mcl): o for o, cls, mcl in stations}


# Plover 快取值保留，既有呼叫點與內容完全相同。
_WHITELIST_KEYS = _whitelist_of(QLIK_STATIONS)
_ORD_BY_KEY = _ord_by_key_of(QLIK_STATIONS)

# ★ [H] 全史列 9 欄 tuple 的欄位索引（pin；**純函式層與編排層一律吃這組常數**）。
#   理由（2026-08-03 LOW C-2）：原本 `r[5]`／`r[0..7]` 硬編散在兩處 ⇒ SELECT 欄序一改就
#   **靜默錯位**（不會炸、只會算錯）。`HIST_COLUMNS` 與 `build_history_sql` 的 SELECT 欄序
#   由 selftest 逐欄釘死對齊；降級路徑（mcl/matStatus/rqsPK ABSENT）以佔位常值保 arity。
#   ★ 2026-09-07（#98／`SPEC-LANDING.md` §18.18）：**8 → 9 欄**，尾巴多
#   `SegmentProductLineCode`（產品家族原碼）。探針存證＝`_probe-family-ph-split.sql` 檔頭。
#   · **加在尾巴**＝前 8 個索引一個位元不動（舊的位置常數全部照舊）。
#   · **`HIST_ROW_ARITY_MIN = 8`**：8 欄的合成／舊列**仍然合法**，家族欄視為 NULL
#     （→ `UNLABELED`）並計進 `stats["n_hist_rows_no_family"]`。短列 guard 若跟著升到 9，
#     每一份既有合成 fixture 都會變成 `n_bad_rows` ＝整盤靜默歸零。
H_PK, H_LOT, H_TS, H_RES, H_PSC, H_MLC, H_STATUS, H_PRQ, H_FAM = range(9)
HIST_ROW_ARITY = 9
HIST_ROW_ARITY_MIN = 8
HIST_COLUMNS = ("stpPK", "matMaterialLotID", "stpStartTime", "stpTestResult",
                "SegmentClassID", "mclMaterialClassID", "matStatus", "rqsPK",
                "SegmentProductLineCode")
# ★ [H] 的 Python 端 DISTINCT **只看前 8 欄**（＝loader qvs:591 那個 DISTINCT 的欄集）。
#   家族欄**不進去重鍵**：同一個 stpPK 若只在家族碼上分歧，9 欄去重會留兩列
#   ⇒ `total`／`pass` 灌水＝口徑被一個新增的顯示欄改掉。第一次出現的碼留下。
HIST_DEDUP_KEY_LEN = 8

# ── 家族（產品線）三值 ＋「未標」──────────────────────────────────────────────
# `Plover`／`Kestrel`／`Heron` 的**唯一出處**＝`mes_family.FAMILIES`（本檔不重打字串，
# 見 `_family_names()`：`mes_family` 在檔頭 import 本檔 ⇒ 反向只能延遲 import）。
# `UNLABELED` **是本檔的概念、不是 `mes_family.UNKNOWN`**：
#   · `mes_family.UNKNOWN` ＝**一個段**的碼是 NULL／不認得（段級）。
#   · `UNLABELED`          ＝**一顆 lot 的全史每一段都沒有碼**（lot 級）。
# 探針實證（09-05 segCleanTest）：17 顆「NULL 段」的 lot 全史在 assyModule／
# assyUnit 階段每段都帶 `plover` ⇒ 段級 UNKNOWN 很常見、lot 級 UNLABELED ≈ 0。
FAMILY_UNLABELED = "UNLABELED"


# =============================================================================
# 2. 小工具
# =============================================================================
def _dt(s):
    """\"YYYY-MM-DD[THH:MM[:SS]]\" | datetime → datetime（純函式；naive 台北牆鐘）。"""
    if isinstance(s, datetime):
        return s
    if s is None:
        raise ValueError("datetime required, got None")
    t = str(s).strip().replace(" ", "T")
    return datetime.fromisoformat(t)


def _win(win):
    """(start, end) → (datetime, datetime) 半開窗 [ws, we)。"""
    ws, we = win
    ws, we = _dt(ws), _dt(we)
    if not ws < we:
        raise ValueError(f"window must be half-open and non-empty: {ws} .. {we}")
    return ws, we


_SQL_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _sql_list(vals):
    """loader 自帶的常值小清單照抄＝忠實形狀（PROBES3 鐵則）。
       只允許識別字字元；任何引號/空白/非 ASCII → raise（注入面歸零）。"""
    out = []
    for v in vals:
        if not v or not set(v) <= _SQL_SAFE:
            raise ValueError(f"unsafe SQL literal (identifier chars only): {v!r}")
        out.append("N'" + v + "'")
    return ", ".join(out)


def _process01_where(has_mcl=True, alias="v", with_result=True):
    """Process01 五道過濾中的四道（時間窗由呼叫端以參數帶）。逐字照抄 loader qvs:586-606。

    ★ `with_result=False`（**僅 `mes_rework` 用**）：省略 `stpTestResult IN (PASS,FAIL)` 那道
      ＝母體多收 NULL 判定列供自揭計數（該檔在 Python 端分桶、**不進 attempts**＝口徑不變）。
      **本模組（Qlik 口徑）恆 `True`**——少了那道就不是 Process01、first-FAIL 母體即失真。
    """
    w = ""
    if with_result:
        w += f"      AND {alias}.stpTestResult IN ({_sql_list(RESULT_KEEP)})\n"
    w += (f"      AND LEFT({alias}.matMaterialLotID, 2) IN ({_sql_list(LOT_PREFIXES)})\n"
          f"      AND {alias}.SegmentClassID NOT IN ({_sql_list(EXCLUDED_CLASSES)})\n")
    if has_mcl:
        w += f"      AND {alias}.mclMaterialClassID IN ({_sql_list(MLC_WHITELIST)})\n"
    return w


def safe_pct(num, den):
    """den=0 → **None**（誠實留白，絕不 0/100）。與 mes_yield.safe_pct（回 0.0）刻意不同。"""
    return (100.0 * num / den) if den else None


# =============================================================================
# 3. I/O 層（SQL builder；參數化、尾 RECOMPILE、唯讀＝只 SELECT ＋ #temp）
# =============================================================================
LOTS_TEMP = "#board_qlik_lots"


class SchemaDivergence(RuntimeError):
    """[G0] **承重**欄位 ABSENT ＝ schema 分歧（T1 證據），具名停下。

    「停下」而不是「繼續跑出全零 grid」的理由：全零 grid 在消費面與「本窗真的沒有資料」
    長得一模一樣（都是 43 列 —），把 schema 分歧渲染成「本窗無資料」＝最壞的靜默謊。
    呼叫端（`board_data.get_qlik_grid_live` → `app.load_qlik_grid`）會把它接成
    `qlik_unavailable("error:SchemaDivergence")`＝頁面顯「—」＋原因，仍不炸站。
    """


# [G0] 承重欄位：ABSENT ⇒ raise（沒有這些欄，整條管線沒有意義）。
REQUIRED_SEG_COLUMNS = ("stpPK", "stpStartTime", "matMaterialLotID", "stpTestResult",
                        "SegmentClassID")

# [G0] guard 回傳鍵序（＝`build_column_guard_sql()` 的 SELECT 欄序；**兩者必須同步改**）。
#   `rqs_rqsPK` ＝ `production_request_v.rqsPK`（2026-08-03 MED C-1：`fetch_rqs` 的 SELECT
#   欄，原本是唯一沒進 guard 的欄 ⇒ 該欄若消失是裸 pyodbc 例外、而非既有 SKIPPED 降級）。
#   ⚠ 與 `rqsPK`（＝`process_segment_v.rqsPK`，hist 側的 join 鍵）是**兩個不同欄**。
#   `SegmentProductLineCode`（2026-09-07／#98）＝**非承重**的家族欄：ABSENT ⇒ 家族拆分
#   整片 `None`（頁面退回單一數字）、**其餘一個位元不動**（不 raise、不 degraded）。
GUARD_KEYS = ("view_seg", "view_rqs", "matStatus", "rqsPK", "mclMaterialClassID",
              "rqsDateStarted", "rqsDateRequested", "rqs_mcl",
              "rqs_rqsPK", "SegmentProductLineCode") + REQUIRED_SEG_COLUMNS


def build_column_guard_sql():
    """[G0] column guard 的 SQL 文本（純函式＝離線可斷言形狀；欄序＝`GUARD_KEYS`）。"""
    req_sql = ",\n           ".join(
        f"CASE WHEN COL_LENGTH('dbo.process_segment_v','{c}') IS NULL THEN 0 ELSE 1 END"
        for c in REQUIRED_SEG_COLUMNS)
    return f"""
    SELECT CASE WHEN OBJECT_ID('dbo.process_segment_v')   IS NULL THEN 0 ELSE 1 END,
           CASE WHEN OBJECT_ID('dbo.production_request_v') IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.process_segment_v','matStatus')            IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.process_segment_v','rqsPK')                IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.process_segment_v','mclMaterialClassID')   IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.production_request_v','rqsDateStarted')    IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.production_request_v','rqsDateRequested')  IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.production_request_v','mclMaterialClassID') IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.production_request_v','rqsPK')             IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.process_segment_v','SegmentProductLineCode') IS NULL THEN 0 ELSE 1 END,
           {req_sql}
    OPTION (RECOMPILE)
    """


def build_pack_sql(cap, has_mcl=True):
    """[PK] 首次包裝邊界 SQL（純函式）。回 `(sql, params)`。"""
    sql = ("SELECT v.matMaterialLotID, v.stpPK\n"
           "FROM dbo.process_segment_v v\n"
           "WHERE v.SegmentClassID = ?\n"
           "  AND v.stpTestResult = ?\n"
           "  AND v.stpStartTime >= ? AND v.stpStartTime < ?\n")
    params = [PACK_CLASS, RESULT_PASS, _dt(QLIK_EPOCH), _dt(cap)]
    if has_mcl:
        sql += "  AND v.mclMaterialClassID = ?\n"
        params.append(PACK_MLC)
    sql += "OPTION (RECOMPILE)"
    return sql, tuple(params)


def build_window_lots_sql(win, has_mcl=True):
    """[W] 窗內 lot 收割 SQL（純函式）。回 `(sql, params)`。"""
    ws, we = _win(win)
    sql = ("SELECT v.matMaterialLotID\n"
           "FROM dbo.process_segment_v v\n"
           "WHERE v.stpStartTime >= ? AND v.stpStartTime < ?\n"
           + _process01_where(has_mcl) +
           "OPTION (RECOMPILE)")
    return sql, (ws, we)


def build_history_sql(cap, has_mcl=True, has_status=True, has_rqs=True,
                      collate_side="temp", has_family=True):
    """[H] 全史列拉取 SQL（純函式；欄序＝`HIST_COLUMNS`）。回 `(sql, params)`。

    `has_family=False`（[G0] 說 `SegmentProductLineCode` ABSENT）⇒ 第 9 欄以
    `CAST(NULL AS nvarchar(64))` 佔位保 arity ＝家族全部 `UNLABELED`、**其餘零改動**
    （同 `matStatus`／`rqsPK` 的降級形；#98）。
    """
    if collate_side == "temp":
        join = f"JOIN {LOTS_TEMP} l ON l.lot COLLATE DATABASE_DEFAULT = v.matMaterialLotID\n"
    else:
        join = (f"JOIN {LOTS_TEMP} l "
                "ON l.lot = v.matMaterialLotID COLLATE Latin1_General_100_CI_AS_SC_UTF8\n")
    sql = ("SELECT v.stpPK, v.matMaterialLotID, v.stpStartTime, v.stpTestResult,\n"
           "       v.SegmentClassID, "
           + ("v.mclMaterialClassID" if has_mcl else "N'(absent)'") + ", "
           + ("v.matStatus" if has_status else "CAST(NULL AS nvarchar(64))") + ", "
           + ("v.rqsPK" if has_rqs else "CAST(NULL AS bigint)") + ",\n"
           "       "
           + ("v.SegmentProductLineCode" if has_family else "CAST(NULL AS nvarchar(64))") + "\n"
           "FROM dbo.process_segment_v v\n"
           + join +
           "WHERE v.stpStartTime >= ? AND v.stpStartTime < ?\n"
           + _process01_where(has_mcl) +
           "OPTION (RECOMPILE)")
    return sql, (_dt(QLIK_EPOCH), _dt(cap))


def build_rqs_sql(cap, has_mcl=True):
    """[PR] production request 掃描 SQL（純函式）。回 `(sql, params)`。"""
    sql = ("SELECT r.rqsPK, r.rqsDateStarted\n"
           "FROM dbo.production_request_v r\n"
           "WHERE r.rqsDateRequested >= ? AND r.rqsDateRequested < ?\n")
    if has_mcl:
        sql += f"  AND r.mclMaterialClassID IN ({_sql_list(MLC_WHITELIST)})\n"
    sql += "OPTION (RECOMPILE)"
    return sql, (_dt(QLIK_EPOCH), _dt(cap))


def build_lots_temp_sql():
    """`#temp` 三條 DDL/DML（唯一非 SELECT 的 SQL；tempdb 不算寫 MES）。"""
    return (f"IF OBJECT_ID('tempdb..{LOTS_TEMP}') IS NOT NULL DROP TABLE {LOTS_TEMP};",
            f"CREATE TABLE {LOTS_TEMP} ("
            "lot nvarchar(128) COLLATE Latin1_General_100_CI_AS_SC_UTF8 PRIMARY KEY);",
            f"INSERT INTO {LOTS_TEMP} (lot) VALUES (?)")


def ALL_SQL():
    """本模組會送出的**全部 SQL 文本**（selftest 形狀/負斷言的唯一清單；`mes_rework`/
    `mes_operator` 同款制度）。

    新增任何查詢**必須**登記在這裡——否則「零 `SELECT…INTO`／零 view 上 DISTINCT-GROUP BY／
    零 lot IN-list／查詢皆帶 RECOMPILE／COLLATE 掛 #temp 側／零 `stpEndTime`／零
    `stpPersonID`」這組負斷言會有漏網之魚。含全部降級分支（欄 ABSENT／collate 換側）。
    """
    cap = datetime(2026, 1, 2)
    win = (datetime(2026, 1, 1), cap)
    out = [build_column_guard_sql()]
    for hm in (True, False):
        out.append(build_pack_sql(cap, has_mcl=hm)[0])
        out.append(build_window_lots_sql(win, has_mcl=hm)[0])
        out.append(build_rqs_sql(cap, has_mcl=hm)[0])
        for side in ("temp", "view"):
            for hs in (True, False):
                for hp in (True, False):
                    for hf in (True, False):     # #98：家族欄 PRESENT／ABSENT 兩支都要登記
                        out.append(build_history_sql(cap, has_mcl=hm, has_status=hs,
                                                     has_rqs=hp, collate_side=side,
                                                     has_family=hf)[0])
    out.extend(build_lots_temp_sql())
    return tuple(out)


def fetch_column_guard(cur):
    """[G0] column guard。**兩級**：

    · **承重級**（`dbo.process_segment_v` 本身 ＋ `REQUIRED_SEG_COLUMNS` 5 欄）：任一 ABSENT
      ⇒ `raise SchemaDivergence`（原本是裸 pyodbc『Invalid column name』——一樣會倒，但
      無法被診斷為 schema 分歧、app 端 `_hint()` 也不涵蓋該型錯誤）。
    · **可降級級**（mclMaterialClassID / matStatus / rqsPK / production_request_v 四欄）：
      ABSENT **不默用 raw**——對應分支在 diag 標 SKIPPED；其中 **mcl ABSENT ⇒ 整盤 degraded**
      （Process01 白名單與 AllPHID 鍵都少一維＝與公司口徑不再等價、也是 T1 證據）。

    回 dict（全 bool）：鍵＝`GUARD_KEYS`（view_seg / view_rqs / matStatus / rqsPK /
    mclMaterialClassID / rqsDateStarted / rqsDateRequested / rqs_mcl / **rqs_rqsPK**
    ＋ 5 個承重欄同名 key）。
    """
    cur.execute(build_column_guard_sql())
    r = cur.fetchone()
    guard = {k: bool(r[i]) for i, k in enumerate(GUARD_KEYS)}
    assert_guard(guard)
    return guard


def assert_guard(guard):
    """承重欄位檢查（純函式＝離線可測）。ABSENT ⇒ `SchemaDivergence`，訊息逐欄具名。"""
    if not guard.get("view_seg"):
        raise SchemaDivergence("schema divergence: dbo.process_segment_v ABSENT")
    missing = [c for c in REQUIRED_SEG_COLUMNS if not guard.get(c)]
    if missing:
        raise SchemaDivergence("schema divergence: "
                               + ", ".join(f"{c} ABSENT" for c in missing))
    return True


def fetch_pack_boundary(cur, cap, has_mcl=True, stats=None):
    """[PK] 首次包裝邊界（Fresh/Sorting 的界）— loader `PK01`/`PK` tab（qvs:539-577）逐字複刻。

    全史 [QLIK_EPOCH, cap)、cls=PACK_CLASS、result='PASS'、mcl=PACK_MLC，per-lot MIN(stpPK)。
    ★ 本段**沒有** B0/M0 前綴過濾、**沒有** 5-mcl 白名單（照抄 loader）。
    ★ SQL 只回 (lot, stpPK) 原始列；MIN 聚合在 Python（`_pack_min`）
      ＝**不在 5 表大 view 上 GROUP BY**（B4 blocking-aggregate 斷崖教訓）。
    回 {lot: pk_first}。PROBES3 實測 2.7s／~81k lots。
    """
    sql, params = build_pack_sql(cap, has_mcl=has_mcl)
    cur.execute(sql, *params)
    rows = [(r[0], r[1]) for r in cur.fetchall()]
    if stats is not None:
        stats["n_pack_raw_rows"] = len(rows)
        stats["pack_mcl_filter"] = "applied" if has_mcl else "SKIPPED(mcl ABSENT)"
    return _pack_min(rows)


def fetch_window_lots(cur, win, has_mcl=True, stats=None):
    """[W] 窗內 lot 收割（Process01 五過濾、**全站**）→ Python `set()`（DISTINCT 不在 view 上做）。

    ★ 已知邊界（PROBES3 [W] 檔頭同款自揭）：**只靠 ASSY 列（有效時戳＝rqsDateStarted）錨進窗的
      AllPHID 刻意不收**——那類單位顯示站名一律 `segASSY`、**不在 43 站也不在任何 KPI 因子**
      ⇒ 對本模組所有數字零貢獻。
    ⚠ 但已收割 lot 的 ASSY 列**仍會**在 [H] 全史拉取時進來（lot 粒度）⇒「ASSY 列吸走 first-FAIL」
      這件事**有被算到**，D6 吸收帳不因本邊界而漏。
    """
    sql, params = build_window_lots_sql(win, has_mcl=has_mcl)
    cur.execute(sql, *params)
    raw = [r[0] for r in cur.fetchall()]
    lots = set(raw)
    if stats is not None:
        stats["n_win_rows"] = len(raw)
        stats["n_win_lots"] = len(lots)
    return lots


def _mk_lots_temp(cur, lots, batch=500):
    """把 [W] 的 lot 清單灌進 `#temp`（**絕不進 IN-list**＝B4 perf saga 鐵則）。

    #temp 字串欄釘 `COLLATE Latin1_General_100_CI_AS_SC_UTF8`（軌一 QA-S collation 事故）。
    #temp 存活於 connection 生命週期 ⇒ 必須與 `fetch_history` 同一條連線／cursor。

    ★ 灌入前先做 **casefold 去重**（`_dedup_lots_ci`）：PK 是 **CI** collation、Python `set()`
      是 case-sensitive ⇒ 兩個只差大小寫的 lot 會撞 PK violation 整輪炸掉。序號資料實務上
      不會發生，但 SQL 端 `SELECT DISTINCT`（PROBES3 [W]）在 CI 下本來就會合併＝照它。

    ★ `fast_executemany`（2026-08-03 MED C-3）：pyodbc 預設**每一列一次往返**——8 萬 lot
      ＝8 萬次 round-trip，成本原本還混進 `h_ms`（讀起來像是 [H] 很慢）。開啟後走參數陣列
      一次送出。**單一 `nvarchar(128)` 欄＝安全**（fast_executemany 的已知地雷是寬字串/
      decimal 型別推斷，這裡兩者都不涉及）。
      驅動/假 cursor 不支援該屬性 ⇒ **靜默沿用逐列路徑**（不得為了 perf 讓功能炸掉）。
      回傳＝實際灌入筆數（casefold 去重後），呼叫端收下進 `diag.n_lots_temp`。
    """
    drop_sql, create_sql, ins = build_lots_temp_sql()
    cur.execute(drop_sql)
    cur.execute(create_sql)
    seq = _dedup_lots_ci(lots)
    try:                                   # 驅動不支援就算了（功能 > perf）
        cur.fast_executemany = True
    except Exception:
        pass
    for i in range(0, len(seq), batch):
        cur.executemany(ins, [(x,) for x in seq[i:i + batch]])
    return len(seq)


def fetch_history(cur, cap, has_mcl=True, has_status=True, has_rqs=True,
                  collate_side="temp", stats=None, has_family=True):
    """[H] ★★ 全史列拉取（`#lots` 驅動）— 全檔最重的一段（PROBES3 實測 3.1s）。

    語意＝loader Process01 五道過濾逐字複刻、**不加站別限制**、時間 [QLIK_EPOCH, cap)＝全史；
    母體由 `#temp` join 縮小（**兩段式母體收斂**＝R5 核心）。
    為什麼一定要全史：first-FAIL 是 lot 的**生涯**判定（地雷 A）。

    ★ 單 view（只掃 `process_segment_v`）；lot 清單走 `#temp` join、不塞 IN-list。
    ★ `COLLATE` 掛 **#temp 側**（`l.lot COLLATE DATABASE_DEFAULT`）＝不封死 view 側 seek。
      **err 若含 "collation conflict"** ⇒ 呼叫端以 `collate_side="view"` 重試
      （回退成 `l.lot = v.matMaterialLotID COLLATE Latin1_General_100_CI_AS_SC_UTF8`
      ＝犧牲 seek、保證能跑；PROBES3 檔頭排障 ②）。
    ★ 灌完後在 Python 做一次 **8 欄 DISTINCT**（＝loader qvs:591 `Select distinct` 同形；
      #98 之後 SELECT 有 9 欄，但**去重鍵仍是前 8 欄**＝`HIST_DEDUP_KEY_LEN`，家族欄不進鍵）——
      本管線是**逐列計數**，view 若吐重複列會讓 total 灌水、FAIL 被算兩次。
      ⚠ 殘留偏差：loader 對 **14 欄**去重、本檔對 **8 欄** ⇒ 只在未取欄（mclDescription／
        matDesignation／rqsWorkOrder…）上分歧的重複 stpPK，Qlik 留兩列、本檔塌一列
        （MAIN OPEN-7；探針＝`diag.n_dup_psgpk`）。

    回 9 欄 tuple list：(stpPK, lot, ts, res, sgc, mcl, mlt_status, rqs_pk, family_raw)。
    """
    sql, params = build_history_sql(cap, has_mcl=has_mcl, has_status=has_status,
                                    has_rqs=has_rqs, collate_side=collate_side,
                                    has_family=has_family)
    cur.execute(sql, *params)
    raw = [tuple(r) for r in cur.fetchall()]
    rows = _dedup_rows(raw)
    if stats is not None:
        stats["n_hist_raw_rows"] = len(raw)
        stats["n_hist_distinct_rows"] = len(rows)
        stats["n_dup_rows_removed"] = len(raw) - len(rows)
        stats["collate_side"] = collate_side
        stats["family_col"] = "applied" if has_family else "SKIPPED(family col ABSENT)"
    return rows


def fetch_rqs(cur, cap, has_mcl=True, stats=None):
    """[PR] production request 掃描 — 只為 ASSY 列的時戳改寫（`stpStartTime-ASSY` = rqsDateStarted）。

    來源＝loader `ProductionRequest` tab（qvs:512-536）：rqsDateRequested ∈ [EPOCH, cap)、
    mcl ∈ 5 類，取 (rqsPK, rqsDateStarted)。單 view；與 hist 的 join 在 Python 上做。
    ★ 忠實形狀：loader 直取不聚合；本檔因需要 dict 而**同 rqsPK 取 MIN 塌一列**
      ⇒ `stats["n_rqs_src_pairs"] > n_rqs_rows` 就是 Qlik 會 fan-out 而本檔塌掉的自揭。
    ★ 呼叫端守衛：hist 無 ASSY 列 ⇒ **跳過本 fetch**（vacuous），
      `diag.assy_ts_branch` 把兩種 skip（無 hist／無 ASSY 列）**分開具名**。
    ★ 欄位守衛：本段碰的三個 `production_request_v` 欄（`rqsPK`／`rqsDateStarted`／
      `rqsDateRequested`）**全部在 [G0]**（`rqs_rqsPK`/`rqsDateStarted`/`rqsDateRequested`）
      ⇒ 任一 ABSENT 時呼叫端 `rqs_available=False`、根本不會進來（走既有 SKIPPED 降級，
      不是裸 pyodbc『Invalid column name』）。
    """
    sql, params = build_rqs_sql(cap, has_mcl=has_mcl)
    cur.execute(sql, *params)
    raw = [(r[0], r[1]) for r in cur.fetchall()]
    out = {}
    for pk, started in raw:
        if pk is None:
            continue
        cur_v = out.get(pk)
        if started is not None and (cur_v is None or started < cur_v):
            out[pk] = started
        elif pk not in out:
            out[pk] = started
    if stats is not None:
        stats["n_rqs_src_rows"] = len(raw)
        stats["n_rqs_src_pairs"] = len({(p, s) for p, s in raw})
        stats["n_rqs_rows"] = len(out)
    return out


# =============================================================================
# 4. 純函式層（全部離線可 selftest；SQL 只取原始列、語意全在這裡）
# =============================================================================
def _dedup_rows(rows, key_len=HIST_DEDUP_KEY_LEN):
    """8 欄 DISTINCT（loader qvs:591 `Select distinct` 同形），保序。對 dup-free 資料零行為差。

    ★ #98：SELECT 之後是 9 欄，但**去重鍵仍只取前 `key_len`（＝8）欄**——家族碼進了鍵，
      同一個 stpPK 只要碼分歧就會留兩列 ⇒ `total`／`pass` 灌水＝一個純顯示欄改掉了口徑。
      同一鍵的第一列勝（保序、確定性）。
    """
    seen = set()
    out = []
    for r in rows:
        t = tuple(r)
        k = t[:key_len]
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _dedup_lots_ci(lots):
    """lot 清單 → **case-insensitive 去重**後的排序 list（`#temp` 的 CI PRIMARY KEY 對齊）。
       同一 casefold 群留**字典序最小**的原字串（確定性；join 走 CI collation ⇒ 挑哪個都同結果）。"""
    keep = {}
    for x in lots:
        if x is None:
            continue
        k = str(x).casefold()
        if k not in keep or str(x) < keep[k]:
            keep[k] = str(x)
    return sorted(keep.values())


def _pack_min(rows):
    """(lot, stpPK) 原始列 → {lot: min stpPK}（[PK] 的聚合，放 Python）。"""
    out = {}
    for lot, pk in rows:
        if lot is None or pk is None:
            continue
        cur_v = out.get(lot)
        if cur_v is None or pk < cur_v:
            out[lot] = pk
    return out


def classify_rows(hist_rows, packmin, rqs_map, has_mtlstatus=True, stats=None):
    """＝#cls3（Process03 / ExtraASSY03）：每列派生四欄。**CASE 順序嚴格照抄 qvs:737/739/720-721**。

    輸入列＝9 欄 tuple `(stpPK, lot, ts, res, sgc, mcl, mlt_status, rqs_pk, family_raw)`
    ＝`HIST_COLUMNS`／`H_*` 索引常數（**不得再硬編位置數字**）。
    ★ arity < `HIST_ROW_ARITY_MIN`（8）的短列 ⇒ 計進 `stats["n_bad_cls_rows"]`
      （→ `diag.n_bad_rows`）並跳過——原本會裸 `IndexError` 整盤炸掉（合規輸入不可達，
      但 snapshot/合成注入可達）。
    ★ **恰好 8 欄的列合法**（#98 之前的形／合成 fixture）：`fam_raw=None`
      ⇒ 該 lot 若全史都是這種列就落 `UNLABELED`；計進 `stats["n_hist_rows_no_family"]`。

    · `station`  ：mcl==ASSY_MLC → 'segASSY'；else sgc_raw
                   （地雷 G：`sgc_raw` 與 `station` **兩欄並存**——AllPHID 鍵用 raw sgc、
                     白名單/KPI join 用 station；ASSY 改名後天然不進任何白名單）。
    · `eff_ts`   ：ASSY → rqs_map.get(rqs_pk)（**缺→None，不退回 stpStartTime**）；else ts。
    · `sf`       ：五分支順序＝未包裝→Fresh｜FRESH_CLASSES→Fresh｜line-end/ASSY mcl→Fresh｜
                   **(stpPK-1) < pk_first**→Fresh｜else Sorting（`(stpPK-1)` 怪癖照抄）。
    · `verdict`  ：res None→'Null'｜sgc∈FORCED_FAIL_CLASSES→'FAIL'｜ASSY mcl→'FAIL'｜
                   line-end ∧ has_mtlstatus ∧ mlt_status∈EOL_PASS_STATUSES→'PASS'｜
                   res∈RESULT_KEEP→res｜else None（理論不可能＝diag 自揭、整盤 degraded）。
    """
    packmin = packmin or {}
    rqs_map = rqs_map or {}
    out = []
    n_bad = 0
    n_nofam = 0
    for r in hist_rows:
        if len(r) < HIST_ROW_ARITY_MIN:             # 短列＝自揭不炸（原本裸 IndexError）
            n_bad += 1
            continue
        pk, lot, ts, res = r[H_PK], r[H_LOT], r[H_TS], r[H_RES]
        sgc, mcl, status, rqs_pk = r[H_PSC], r[H_MLC], r[H_STATUS], r[H_PRQ]
        if len(r) > H_FAM:
            fam_raw = r[H_FAM]
        else:                                       # 8 欄的舊／合成列＝家族欄視為 NULL
            fam_raw = None
            n_nofam += 1
        is_assy = (mcl == ASSY_MLC)
        station = ASSY_STATION if is_assy else sgc
        eff_ts = rqs_map.get(rqs_pk) if is_assy else ts

        pk_first = packmin.get(lot)
        if pk_first is None:
            sf = "Fresh"
        elif sgc in FRESH_CLASSES:
            sf = "Fresh"
        elif mcl in (EOL_MLC, ASSY_MLC):
            sf = "Fresh"
        elif (pk - 1) < pk_first:
            sf = "Fresh"
        else:
            sf = "Sorting"

        if res is None:
            verdict = "Null"
        elif sgc in FORCED_FAIL_CLASSES:
            verdict = RESULT_FAIL
        elif is_assy:
            verdict = RESULT_FAIL
        elif mcl == EOL_MLC and has_mtlstatus and status in EOL_PASS_STATUSES:
            verdict = RESULT_PASS
        elif res in RESULT_KEEP:
            verdict = res
        else:
            verdict = None

        out.append({"stpPK": pk, "lot": lot, "ts": ts, "res": res, "sgc_raw": sgc,
                    "mcl": mcl, "mlt_status": status, "rqs_pk": rqs_pk,
                    "station": station, "eff_ts": eff_ts, "sf": sf, "verdict": verdict,
                    # #98：家族**原碼**原樣帶著走（正規化留給 lot 級那一層＝單一出處）
                    "fam_raw": fam_raw})
    if stats is not None:
        stats["n_bad_cls_rows"] = n_bad
        stats["n_hist_rows_no_family"] = n_nofam
    return out


# ── 家族（lot 級）─────────────────────────────────────────────────────────────
def _family_names():
    """canonical 三家族名 tuple。**唯一出處＝`mes_family.FAMILIES`**（延遲 import：
       `mes_family` 在檔頭 `from mes_qlik import …` ⇒ 本檔只能反向延遲取，不得抄字串）。
       取不到（單檔執行／相依缺席）⇒ 回 `()`＝**不猜**，家族拆分整片關掉。"""
    try:
        from mes_family import FAMILIES        # noqa: PLC0415（延遲＝避環，見上）
        return tuple(FAMILIES)
    except Exception:                          # pragma: no cover（相依齊全時走不到）
        return ()


def family_keys():
    """輸出/顯示的家族鍵序：`FAMILIES` ＋ 尾巴 `UNLABELED`（確定性、落盤可重現）。"""
    return _family_names() + (FAMILY_UNLABELED,)


def lot_family_map(cls_rows, stats=None):
    """`classify_rows` 的列 → `({lot: family}, n_conflict)`（純函式、零 I/O）。

    **口徑（`SPEC-LANDING.md` §18.18／探針 `_probe-family-ph-split.sql` 檔頭）＝按 PH（lot）
    判、不按段判**：一顆 lot 在 Qlik 口徑那份**全史**裡任一段有可辨識的家族碼
    ⇒ **整顆**歸該家族；全史皆無 ⇒ `UNLABELED`。

    · 正規化＝`mes_family.normalize_family`（逐字對齊普查 CASE；**單一出處**）。
      取不到那支函式 ⇒ 回 `({}, 0)`＝**整片不拆**（絕不自己抄一份 CASE）。
    · **碼互相矛盾**（同一顆 lot 出現兩種家族）＝取**最早非 NULL 段**的碼並計進
      `n_conflict` 自揭。「最早」＝**min `stpPK`**，不是時戳、不是站序
      （地雷 C／J0SYN14：跨站復測會讓時序倒置；本檔全域排序鍵一律 stpPK）。
    · lot 為 None 的列直接丟（不入 map）。
    """
    try:
        from mes_family import normalize_family     # noqa: PLC0415（延遲＝避環）
        from mes_family import UNKNOWN as _UNK      # noqa: PLC0415
    except Exception:                               # pragma: no cover（相依齊全時走不到）
        if stats is not None:
            stats["family_split"] = "unavailable:mes_family"
        return {}, 0
    best = {}                       # lot -> (min stpPK, family)
    seen = {}                       # lot -> set(family)
    for r in cls_rows:
        lot = r.get("lot")
        if lot is None:
            continue
        fam = normalize_family(r.get("fam_raw"))
        if fam == _UNK:             # 段級 UNKNOWN（NULL／不認得）＝這一段沒有主張
            continue
        pk = r.get("stpPK")
        seen.setdefault(lot, set()).add(fam)
        cur_v = best.get(lot)
        if cur_v is None or (pk is not None and cur_v[0] is not None and pk < cur_v[0]):
            best[lot] = (pk, fam)
    out = {lot: fam for lot, (_pk, fam) in best.items()}
    n_conflict = sum(1 for s in seen.values() if len(s) > 1)
    if stats is not None:
        stats["family_split"] = "applied"
        stats["n_family_conflict_lots"] = n_conflict
    return out, n_conflict


def _allphid_key(r):
    """AllPHID ＝ lot-sf-mcl-**raw sgc**（地雷 G：鍵用 raw sgc、不是顯示站名）。"""
    return (r["lot"], r["sf"], r["mcl"], r["sgc_raw"])


def collapse_end_rows(cls_rows):
    """＝#endrow（Process04）：per-AllPHID 取 Max(eff_ts)、**RANK 平手全留**。

    ★ 先排除 `eff_ts is None`（審查 #3，**必要**）：Qlik 是 `Inner Join ... Max(...)`——
      全 NULL 的 AllPHID 的 Max=null、null 在 join 永不匹配 ⇒ **整組從 End 表消失**；
      若不排除，RANK 會讓它們全部並列 rk=1 ⇒ ASSY 降級時整批變成 verdict 恆 FAIL 的
      **幽靈 first-FAIL 候選**、搶走真正該顯 FAIL 的可見站（地雷 B 的反面）。
    """
    best = {}
    for r in cls_rows:
        if r["eff_ts"] is None:
            continue
        k = _allphid_key(r)
        b = best.get(k)
        if b is None or r["eff_ts"] > b:
            best[k] = r["eff_ts"]
    return [r for r in cls_rows
            if r["eff_ts"] is not None and best[_allphid_key(r)] == r["eff_ts"]]


def first_fail_map(end_rows):
    """＝#ff（Process05）：**per-lot** 在末次列中 verdict=='FAIL' 取 **min stpPK**。

    · D7／地雷 C：排序鍵是 **stpPK**，不是時戳、不是站序、不是螢幕列號
      （J0SYN14 型跨站復測會讓時序倒置）。
    · 地雷 I／E：**不篩 sf、不篩 mcl、不篩站**——flag 是**全線級**，KPI/表格端才切面。
      先篩再算 ⇒ flag 落錯站、誤差方向還會翻轉。
    """
    out = {}
    for r in end_rows:
        if r["verdict"] != RESULT_FAIL:
            continue
        lot, pk = r["lot"], r["stpPK"]
        if lot not in out or pk < out[lot]:
            out[lot] = pk
    return out


def window_anchors(cls_rows):
    """＝#anch（Process11）：per-AllPHID min(非 None eff_ts) ＝ **Start 側首次**（T-Q1）。

    全 None eff_ts 的 AllPHID **不入 map**（無錨＝整組掉出窗；SQL 端 NULL anchor 天然不過
    窗判定，Python 端絕不拿 None 與 datetime 比大小）。
    ⚠ Process12 的 `matDesignation-Start <> Null()` 過濾＝**不可複刻**
      （diag `desig_filter='not_replicated'`；方向：螢幕 total ≤ 本檔 total）。
    """
    out = {}
    for r in cls_rows:
        if r["eff_ts"] is None:
            continue
        k = _allphid_key(r)
        cur_v = out.get(k)
        if cur_v is None or r["eff_ts"] < cur_v:
            out[k] = r["eff_ts"]
    return out


def display_rows(end_rows, anchors, ff, win):
    """＝#disp（Process10）：末次列 ∩ anchor 落窗 [ws, we)，並貼上 first-FAIL 顯示值。

    disp ＝ 'Null'（verdict Null）｜'FAIL'（stpPK == ff[lot]）｜否則 'PASS'。
    **地雷 A 的「窗只篩顯示」在此落地**：first-FAIL 早在全史上算完，本步只決定「誰被看見」。
    """
    ws, we = _win(win)
    out = []
    for r in end_rows:
        a = anchors.get(_allphid_key(r))
        if a is None or not (ws <= a < we):
            continue
        lot = r["lot"]
        ffpk = ff.get(lot)
        if r["verdict"] == "Null":
            disp = "Null"
        elif ffpk is not None and ffpk == r["stpPK"]:
            disp = RESULT_FAIL
        else:
            disp = RESULT_PASS
        d = dict(r)
        d["anchor_ts"] = a
        d["ff_psgpk"] = ffpk
        d["disp"] = disp
        d["ptype"] = (lot[:1] if lot else "")
        d["allphid"] = f'{lot}-{r["sf"]}-{r["mcl"]}-{r["sgc_raw"]}'
        out.append(d)
    return out


def aggregate_grid(disp_rows, fam_map=None, *, ptypes=("J",)):
    """＝#agg：**ptype ∈ ptypes ∧ sf=='Fresh'** 切面，per (station, mcl) 計數。

    · `total` ＝ `COUNT(*)` **忠實 `Count(AllPHID)`**（非 distinct、含 tie fan-out＝地雷 H），
      `n_allphid` ＝ distinct 自揭（兩者差＝膨脹量，先扣 `diag.n_dup_psgpk` 才能歸因 tie）。
    · `n_endfail` ＝ verdict=='FAIL'（**raw 末次** FAIL，對 PROBES2 [2B] 口徑）
      ⇒ `n_endfail − n_fail` ＝ 被更早/看不見的站吸走的量。
    · **`fam_map`（#98）**＝`lot_family_map()` 的 `{lot: family}`。給了就多算一個
      `pass_by_family`（**只拆 `n_pass` 這一個量**、其餘一個位元不動）；map 裡沒有的 lot
      落 `UNLABELED`。⇒ 恆等式 `n_pass == Σ pass_by_family`（本函式的構造保證，
      `build_grid` 另有一道守衛）。`fam_map is None` ⇒ 整片不拆（鍵不出現）。
    """
    split = fam_map is not None
    out = {}
    for d in disp_rows:
        if d["ptype"] not in ptypes or d["sf"] != "Fresh":
            continue
        k = (d["station"], d["mcl"])
        a = out.get(k)
        if a is None:
            a = out[k] = {"total": 0, "_ids": set(), "n_pass": 0, "n_fail": 0,
                          "n_null": 0, "n_endfail": 0, "pass_by_family": ({} if split else None)}
        a["total"] += 1
        a["_ids"].add(d["allphid"])
        if d["disp"] == RESULT_PASS:
            a["n_pass"] += 1
            if split:
                f = fam_map.get(d["lot"]) or FAMILY_UNLABELED
                a["pass_by_family"][f] = a["pass_by_family"].get(f, 0) + 1
        elif d["disp"] == RESULT_FAIL:
            a["n_fail"] += 1
        else:
            a["n_null"] += 1
        if d["verdict"] == RESULT_FAIL:
            a["n_endfail"] += 1
    for a in out.values():
        a["n_allphid"] = len(a.pop("_ids"))
    return out


def order_family_counts(counts, keys=None):
    """`{family: n}` → **鍵序固定、零值不寫鍵**的 dict（R→A→M→UNLABELED；#98）。

    · 不在鍵序裡的家族（理論不可達）排在最後、依字典序 ⇒ 永遠**不靜默丟資料**。
    · `None` ⇒ `None`（「沒拆」與「拆了但全零」不是同一件事）。
    """
    if counts is None:
        return None
    keys = family_keys() if keys is None else tuple(keys)
    out = {}
    for k in keys:
        n = int(counts.get(k) or 0)
        if n > 0:
            out[k] = n
    for k in sorted(x for x in counts if x not in keys):
        n = int(counts.get(k) or 0)
        if n > 0:
            out[k] = n
    return out


def fpy_grid(cls_rows, anchors, win, *, ptypes=("J",)):
    """FIRST PASS YIELD 忠實複刻（SCREEN §2 運算式）：

        Count({B, [stpTestResult-Start]={'PASS'}} [stpTestResult-Start])
      / Count({B}                                 [stpTestResult-Start])

    · 單位＝per-AllPHID **首次列**（min eff_ts；**tie 全留＝fan-out 忠實**——Start 表是
      Qlik `Inner Join Min(...)`，與末次收斂的 RANK 平手全留**同形**。
      ⚠ **絕不 tie→min stpPK 塌一列**＝那是另一種口徑發明）。
    · 判定用 **raw `res`**（不是 first-FAIL、也不是強制 verdict）。
    · 母體＝anchor 落窗 ∧ ptype=='B'、**不篩 Fresh**（T-Q3 ＝照抄公司報表現況；
      含 Sorting 的窗會與 Fresh 切面分歧＝運算式原文行為，照抄不修）。
    · **`res is None` 不進分母**：Qlik 的 `Count([stpTestResult-Start])` 只計非空值。
      經 SQL 路徑不可達（Process01 已濾 `stpTestResult IN (PASS,FAIL)`）＝母體保證；
      合成/snapshot 注入時才可能出現，不排除的話 FPY 會被靜默壓低。

    回 {(station, mcl): (num, den)}。
    """
    ws, we = _win(win)
    out = {}
    for r in cls_rows:
        eff = r["eff_ts"]
        if eff is None:
            continue
        a = anchors.get(_allphid_key(r))
        if a is None or eff != a:                       # 只留首次列（tie 全留）
            continue
        if not (ws <= a < we):
            continue
        if (r["lot"] or "")[:1] not in ptypes:
            continue
        if r["res"] is None:                            # Count(field) 只計非空值
            continue
        k = (r["station"], r["mcl"])
        num, den = out.get(k, (0, 0))
        out[k] = (num + (1 if r["res"] == RESULT_PASS else 0), den + 1)
    return out


def _rty_core(rows_by_ord, stations=QLIK_STATIONS, groups=KPI_GROUPS):
    """[4A] 因子連乘核心 → {kpi: (prod, n_stations, n_empty, n_allfail)}。

    factor = IIF(num==0, 1, num) / IIF(den==0, 1, den)（地雷 J：**兩個守衛各自獨立**——
    num=0 **不是跳站**〔有量但全 FAIL ⇒ 1/den〕，只有 den=0 才等於跳站〔1/1〕）。
    `prod` 永遠有定義（外層 '-' 守衛只在 `rty()` 的顯示層壓成 None）＝[4C] 恆等自檢用的是它。
    """
    ords = [s[0] for s in stations]
    out = {}
    for name, lo, hi in groups:
        prod, n_st, n_empty, n_allfail = 1.0, 0, 0, 0
        for o in ords:
            if not (lo <= o <= hi):
                continue
            num, den = rows_by_ord.get(o, (0, 0))
            n_st += 1
            if den == 0:
                n_empty += 1
            elif num == 0:
                n_allfail += 1
            prod *= (1.0 if num == 0 else float(num)) / (1.0 if den == 0 else float(den))
        out[name] = (prod, n_st, n_empty, n_allfail)
    return out


def rty(rows_by_ord, stations=QLIK_STATIONS, groups=KPI_GROUPS, expected=None):
    """＝[4B]：7 個 RTY KPI。回 {kpi: {"pct", "n_stations", "n_empty", "n_allfail"}}。

    **外層守衛（審查 #14）**：全部因子站 den==0 ⇒ `pct=None`——**絕不 100.0**
    （每個因子 1/1 連乘 = 1 會假裝成「有值」）。Qlik 在無資料時顯 `-`；本檔以 NULL 近似
    （MAIN OPEN-6：Qlik 守衛條件是舊白名單計數、本檔用 OVERALL 因子表 den 全空 ⇒ 近似非逐字）。
    **因子數自檢**：n_stations != KPI_EXPECTED_N ⇒ pct=None（站清單接錯＝全部作廢）。
    """
    expected = KPI_EXPECTED_N if expected is None else expected
    core = _rty_core(rows_by_ord, stations, groups)
    out = {}
    for name, (prod, n_st, n_e, n_af) in core.items():
        exp = expected.get(name)
        bad_count = (exp is not None and n_st != exp)
        pct = None if (n_st == 0 or n_e == n_st or bad_count) else 100.0 * prod
        out[name] = {"pct": pct, "n_stations": n_st, "n_empty": n_e, "n_allfail": n_af}
    return out


def _station_count_check(core, expected=None):
    expected = KPI_EXPECTED_N if expected is None else expected
    bad = [f"{k}={core[k][1]}(exp {expected[k]})" for k in expected if k in core
           and core[k][1] != expected[k]]
    missing = [k for k in expected if k not in core]
    if missing:
        bad.append("missing " + ",".join(sorted(missing)))
    return "ok" if not bad else "!! factor-count mismatch : " + "; ".join(bad)


def _identity_check(core, all_empty, *, need=_IDENTITY_NEED):
    """＝[4C]：OVERALL ≟ PH×PC×PPC、PH ≟ BeforeDA×FromDA（<1e-9）。

    **不是**對 Qlik 的檢查（同一份因子表本來就恆等），是「因子表與聚合沒接錯」的內部自檢。
    用 `_rty_core` 的**原始乘積**（不是被外層守衛壓成 None 的顯示值）＝與 [4C] 讀 `#kpi.val` 同形。
    全空窗（所有 KPI 的因子站 den 全 0）→ 'n/a(all empty)'＝**不算 degraded、不算 fail**。
    """
    if all_empty:
        return "n/a(all empty)"
    miss = [k for k in need if k not in core]
    if miss:
        return "!! KPI missing -> " + ",".join(miss)
    ov, ph, pc, pp, bd, fd = (core[k][0] for k in need)
    d1, d2 = abs(ov - ph * pc * pp), abs(ph - bd * fd)
    if d1 < 1e-9 and d2 < 1e-9:
        return "ok"
    return f"!! identity broken -> d_overall={d1:.3e} d_ph={d2:.3e}"


def _ff_bucket(station, mcl, sf, ptype, *, whitelist=_WHITELIST_KEYS, ptypes=("J",)):
    """[3F] first-FAIL 落點四分類。**CASE 順序承重、不可調**（審查 #4/#8/#13）：

      ①白名單 ∧ Fresh ∧ 本產品 → on_screen
      ②白名單 ∧ 非 Fresh   → sorting_cut   ← 必須排在 ③ 之前：Sorting 的 M0 lot 落白名單站
                                              ＝sorting_cut、**不是** non_b
      ③白名單（非本產品）   → non_b
      ④否則                → invisible_station

    ⚠ `non_b` 分支在 `absorption()` 的母體下**結構性不可達**（母體要求該 lot 有本產品+Fresh+
      profile 白名單的窗內顯示列 ⇒ lot 首碼已在 `ptypes`）——與 loader 的 PeelTest 強制 FAIL 同類的死分支，
      **照抄不刪**（忠實形狀＋日後母體若放寬即活）。selftest 直接對本函式驗該分支。
    """
    in_wl = (station, mcl) in whitelist
    if in_wl and sf == "Fresh" and ptype in ptypes:
        return BUCKET_ON_SCREEN
    if in_wl and sf != "Fresh":
        return BUCKET_SORTING
    if in_wl:
        return BUCKET_NON_B
    return BUCKET_INVISIBLE


def absorption(ff, end_rows, anchors, win, disp_rows=None, *, ptypes=("J",),
               whitelist=_WHITELIST_KEYS):
    """＝[3F]（D6 的資料源）：first-FAIL 落在**螢幕看不到的地方**的量。

    `disp_rows` ＝ 已算好的 `display_rows(...)`（`build_grid` 會傳，省一次全量重算；[H] 之後
    最重的派生步驟＝PROBES3 derive 2.9s 級）。不給則自己算＝同輸入同結果、零語意差。

    母體＝「窗內有顯示列（本產品 ∧ Fresh ∧ profile 白名單）」的 lot（＝與主 grid 同母體，
    避免把窗外 lot 也算進來；審查 #16）。對每個這種 lot 的 first-FAIL 落點做 `_ff_bucket`
    判定，回**非 on_screen** 的各桶 `[{station, mcl, bucket, n_lots}]`。

    **空清單＝本窗無吸收，也是有效資訊**（不是「沒算」）。
    這就是「**可見 FAIL 加總 ≠ 實際死亡顆數**」的當場證據（地雷 D）——一顆 lot 可以死在
    `segPostLamInspection`／`segASSY` 這種不在 43 站的地方，可見各站全顯 PASS。

    ⚠ **已知邊界（照抄探針、不自行「補強」）**：`on_screen` 判定只看
      (白名單站, Fresh, B) 三條件，**不看該 first-FAIL 單位本身的 anchor 是否落窗**
      （落窗與否在 [3C] 是 `ff_unit_in_window` 另一欄）。⇒ 「死在白名單站、但那個單位的
      首次時戳在窗外」的 lot 歸 `on_screen`、**不計入吸收帳**。改掉它會讓對表多出無法歸因的差
      ——要改是探針/口徑 owner 的事，不是本模組的事。
    """
    disp = display_rows(end_rows, anchors, ff, win) if disp_rows is None else disp_rows
    qualifying = {d["lot"] for d in disp
                  if d["ptype"] in ptypes and d["sf"] == "Fresh"
                  and (d["station"], d["mcl"]) in whitelist}
    by_pk = {}
    for r in end_rows:                                   # 忠實 JOIN（dup stpPK 會 fan-out）
        by_pk.setdefault(r["stpPK"], []).append(r)
    buckets = {}
    for lot, ffpk in ff.items():
        if lot not in qualifying:
            continue
        for e in by_pk.get(ffpk, ()):
            b = _ff_bucket(e["station"], e["mcl"], e["sf"], (e["lot"] or "")[:1],
                           whitelist=whitelist, ptypes=ptypes)
            if b == BUCKET_ON_SCREEN:
                continue
            k = (e["station"], e["mcl"], b)
            buckets[k] = buckets.get(k, 0) + 1
    out = [{"station": s, "mcl": m, "bucket": b, "n_lots": n}
           for (s, m, b), n in buckets.items()]
    out.sort(key=lambda x: (-x["n_lots"], x["station"], x["mcl"], x["bucket"]))
    return out


# =============================================================================
# 5. 組裝（§3.4 canonical pin）
# =============================================================================
def assy_branch_of(n_hist, n_assy, rqs_available=True, rqs_map=None):
    """`diag.assy_ts_branch` 的**唯一**判定（純函式）。

    原本同一組判定字串**寫了兩份**（`build_grid` 一份、`compute_qlik_grid` 一份），
    改一邊忘另一邊＝degraded 判準（`assy_lossy` 靠 `startswith("SKIPPED")`）靜默失準。
    順序承重：無 hist ＞ 無 ASSY 列 ＞ 來源不可用 ＞ 有/無 production-request 列。
    """
    if not n_hist:
        return "UNKNOWN(no history rows -> upstream [H] not run/failed)"
    if not n_assy:
        return "vacuous(0 ASSY rows)"
    if not rqs_available:
        return ("SKIPPED(production_request_v/rqsDateStarted/rqsDateRequested/rqsPK ABSENT)"
                " -> ASSY eff_ts = NULL")
    if rqs_map:
        return "implemented"
    return "SKIPPED(no production-request rows) -> ASSY eff_ts = NULL"


def build_grid(cls_rows, win, cap, packmin=None, rqs_map=None, has_mtlstatus=True,
               has_mcl=True, n_win_lots=None, n_dup_rows_removed=0, n_rqs_src_pairs=None,
               n_rqs_rows=None, eol_branch=None, assy_ts_branch=None, timing=None,
               stations=QLIK_STATIONS, expected=None, n_bad_rows=0, n_lots_temp=None,
               has_family=True, fam_map=None, *, groups=KPI_GROUPS, ptypes=("J",),
               product="plover", display=None, display_src=None):
    """純函式鏈編排＋§3.4 canonical 組裝（**離線可測、零 I/O**）。

    不變式（selftest 釘死）：`stations` 恆 43 列、ord 恆 1..43；per (station,mcl)
    `fail ≤ n_endfail` ⇒ `absorbed_elsewhere ≥ 0`；`pass+fail ≤ total`（差＝n_null）；
    `identity_check ∈ {'ok','n/a(all empty)'}` 否則 kpis 全 None；
    `absorbed_total == Σ absorption`；全空輸入 → 43 列 total=0、KPI 全 None、**不 raise**。

    ★ `win_end > cap` 守衛（2026-08-03 MED C-2）：cap 是全史上界（as-of），**早於窗尾就是
      「窗的後半段沒有資料可看」**——CLI 對表時 `--cap` 打錯（打成前一天 reload 時刻）會
      靜默少算、頁面卻一切正常。此處自 `win`/`cap` 現推 `diag.cap_before_window_end` 並
      **併入 degraded**（誠實降級、不 raise ⇒ API 不變）。
      ⚠ live 路徑不會誤觸：`board_data.resolve_window` 把 `win_end` clamp 到 now(=cap)，
      `run(days=…)` 亦 `we == cap`。
    """
    ws, we = _win(win)
    cap = _dt(cap)
    expected = KPI_EXPECTED_N if expected is None else expected
    display = QLIK_DISPLAY if display is None else display
    # #105：顯示名來源標記（加性）。沒給 map ⇒ 有名字就算 "qlik"（Plover 的既有語意）。
    display_src = {} if display_src is None else display_src
    whitelist = _whitelist_of(stations)

    end_rows = collapse_end_rows(cls_rows)
    ff = first_fail_map(end_rows)
    anchors = window_anchors(cls_rows)
    disp = display_rows(end_rows, anchors, ff, win)
    # ── #98 家族拆分（**只拆 pass、總和不變**）─────────────────────────────────
    #   欄 ABSENT（`has_family=False`）⇒ 整片不拆（`None`），**不是**「全部 UNLABELED」：
    #   「沒有那一欄」與「有那一欄但沒有碼」是兩件事，頁面該說的話也不同（C-LD2 同精神）。
    fam_stats = {}
    n_fam_conflict = 0
    if not has_family:
        fam_map, fam_split = None, "SKIPPED(SegmentProductLineCode ABSENT)"
    elif fam_map is None:
        fam_map, n_fam_conflict = lot_family_map(cls_rows, stats=fam_stats)
        fam_split = fam_stats.get("family_split") or "applied"
        if fam_split != "applied":
            fam_map = None
    else:
        fam_split = "applied(injected)"
    agg = aggregate_grid(disp, fam_map=fam_map, ptypes=ptypes)
    fpy = fpy_grid(cls_rows, anchors, win, ptypes=ptypes)
    absorb = absorption(ff, end_rows, anchors, win, disp_rows=disp, ptypes=ptypes,
                         whitelist=whitelist)

    rows_by_ord, out_stations, n_neg = {}, [], 0
    n_fam_broken = 0
    for o, cls, mcl in stations:
        a = agg.get((cls, mcl))
        f_num, f_den = fpy.get((cls, mcl), (0, 0))
        n_pass = a["n_pass"] if a else 0
        n_fail = a["n_fail"] if a else 0
        n_endfail = a["n_endfail"] if a else 0
        absorbed = n_endfail - n_fail
        if absorbed < 0:
            n_neg += 1
        # ★ 家族拆分：**恆等式是硬前提**——`pass == Σ pass_by_family`。
        #   構造上必然成立（同一個 `if disp == PASS` 分支各加一次）；不成立就是這一段被
        #   改壞了 ⇒ **整片吐 None ＋ caveat**，絕不印一組加不回總數的拆分（C-LD2）。
        pbf = None
        if fam_map is not None:
            pbf = order_family_counts((a or {}).get("pass_by_family") or {})
            if sum(pbf.values()) != n_pass:
                pbf, n_fam_broken = None, n_fam_broken + 1
        rows_by_ord[o] = (n_pass, n_pass + n_fail)
        display_name = display.get(o) if display is not None else None
        # ★ #105：`display_pending` ＝**兩張表都沒有名字**（不再是「非 Plover 就 pending」）。
        #   `display_source` 加性：有名字時 "qlik"／"derived"，沒名字時 None（＝pending）。
        display_source = display_src.get(o) or ("qlik" if display_name else None)
        display_pending = display_name is None
        out_stations.append({
            "ord": o, "cls": cls, "mcl": mcl, "display": display_name or cls,
            "display_pending": display_pending, "display_source": display_source,
            "total": a["total"] if a else 0,
            "pass": n_pass, "fail": n_fail,
            "pass_by_family": pbf,
            "final_yield_pct": safe_pct(n_pass, n_pass + n_fail),
            "fpy_pct": safe_pct(f_num, f_den), "fpy_num": f_num, "fpy_den": f_den,
            "n_allphid": a["n_allphid"] if a else 0,
            "n_endfail": n_endfail, "n_null": a["n_null"] if a else 0,
            "absorbed_elsewhere": absorbed,
        })

    core = _rty_core(rows_by_ord, stations, groups)
    kpis = rty(rows_by_ord, stations, groups, expected=expected)
    all_empty = all(v[2] == v[1] for v in core.values()) if core else True
    scc = _station_count_check(core, expected)
    identity_need = PRODUCTS.get(product, {}).get("identity_need", _IDENTITY_NEED)
    ident = _identity_check(core, all_empty, need=identity_need)

    # ---- diag -------------------------------------------------------------
    n_hist = len(cls_rows)
    n_assy = sum(1 for r in cls_rows if r["mcl"] == ASSY_MLC)
    if eol_branch is None:
        eol_branch = "implemented" if has_mtlstatus else "SKIPPED(matStatus ABSENT)"
    if assy_ts_branch is None:
        assy_ts_branch = assy_branch_of(n_hist, n_assy, True, rqs_map)

    seen_keys = {}
    for r in end_rows:
        k = _allphid_key(r)
        seen_keys[k] = seen_keys.get(k, 0) + 1
    tie = sum(1 for v in seen_keys.values() if v > 1)
    n_dup_psgpk = len(end_rows) - len({r["stpPK"] for r in end_rows})
    n_verdict_unexpected = sum(1 for r in cls_rows if r["verdict"] is None)
    n_pack_lots = len(packmin) if packmin is not None else 0

    # ---- degraded 三類（**單一信任旗**：消費面只看這一個，不必去讀 diag 細節）----
    #  ① 內部自檢壞掉（verdict ELSE 分支／因子數／恆等／負吸收／packmin 全空但有史料）；
    #  ② **schema 分歧**：mcl 欄 ABSENT ⇒ Process01 白名單與 AllPHID 鍵各少一維
    #     ＝與公司口徑不再等價（PROBES3 [G0] 把它列為承重欄／T1 證據）。**這條不可省**：
    #     少了它，「(station,mcl) 永不匹配白名單 ⇒ 43 列全零」會被渲染成「本窗無資料的 —」。
    #  ③ **ASSY 時戳改寫被跳過而窗內真有 ASSY 列**：那批列 eff_ts=NULL ⇒ 整批掉出窗
    #     （[PR] 讀法：該窗若有 ASSY 量則主 grid 要打折）。本窗實證 ASSY=0 ⇒ 現實風險低，
    #     但不能只自揭在 diag.assy_ts_branch。
    #  ④ **cap 早於窗尾**：as-of 上界把窗的後半段整段砍掉 ⇒ 數字系統性偏少而頁面看不出來
    #     （CLI 對表 `--cap` 打錯就是這型）。誠實降級、不 raise。
    mcl_filter = "applied" if has_mcl else "SKIPPED(mcl ABSENT)"
    assy_lossy = bool(str(assy_ts_branch or "").startswith("SKIPPED") and n_assy > 0)
    cap_before_we = bool(we > cap)
    degraded = bool(
        n_verdict_unexpected
        or scc != "ok"
        or ident not in ("ok", "n/a(all empty)")
        or n_neg
        or (packmin is not None and n_pack_lots == 0 and n_hist > 0)
        or not has_mcl                                   # ②
        or assy_lossy                                     # ③
        or cap_before_we                                 # ④
    )
    caveats = []
    if not has_mcl:
        caveats.append("mclMaterialClassID ABSENT -> Process01 whitelist + AllPHID key "
                       "lost a dimension; grid is NOT company-caliber equivalent")
    if assy_lossy:
        caveats.append(f"ASSY timestamp rewrite skipped with {n_assy} ASSY rows in scope "
                       f"-> those rows dropped out of the window ({assy_ts_branch})")
    if cap_before_we:
        caveats.append(f"cap ({cap}) is earlier than window end ({we}) -> the tail of the "
                       "window has no as-of data; figures are systematically low "
                       "(check --cap)")
    # #98：家族拆分**永遠不進 `degraded`**——它是加法欄，壞了就整片 `None`（頁面退回
    #   單一數字），不得因為一個顯示欄把整份 grid 的報廢腿判成不可信。
    if n_fam_broken:
        fam_split = "broken:identity"
        caveats.append(f"family split identity broken on {n_fam_broken} station rows "
                       "(pass != sum of pass_by_family) -> split dropped, totals untouched")
    if n_fam_conflict:
        caveats.append(f"{n_fam_conflict} lots carry more than one product-family code "
                       "across their history (earliest stpPK wins; totals unaffected)")
    if n_lots_temp is not None and n_win_lots is not None and n_lots_temp != n_win_lots:
        caveats.append(f"#temp lot count {n_lots_temp} != window lot count {n_win_lots} "
                       "(casefold-dedup collapsed case-variant lot ids; [H] join is CI)")

    diag = {
        "mcl_filter": mcl_filter,
        # #98：家族拆分的三個自揭欄（`family_split` ＝ applied｜applied(injected)｜
        #   SKIPPED(...)｜unavailable:…｜broken:identity）
        "family_split": fam_split,
        "n_family_conflict_lots": n_fam_conflict,
        "n_family_labeled_lots": (None if fam_map is None else len(fam_map)),
        "caveats": caveats,
        "n_win_lots": (n_win_lots if n_win_lots is not None
                       else len({r["lot"] for r in cls_rows})),
        "n_hist_rows": n_hist,
        "n_dup_rows_removed": n_dup_rows_removed,
        "n_dup_psgpk": n_dup_psgpk,
        "n_bad_rows": n_bad_rows,               # [H] 短列（arity < 8）＝自揭不炸
        "n_lots_temp": n_lots_temp,             # 實際灌進 #temp 的筆數（≠ n_win_lots ⇒ caveat）
        "cap_before_window_end": cap_before_we,
        "n_pack_lots": n_pack_lots,
        "n_tie_allphid": tie,
        "n_eff_null": sum(1 for r in cls_rows if r["eff_ts"] is None),
        "n_rqs_src_pairs": n_rqs_src_pairs,
        "n_rqs_rows": (n_rqs_rows if n_rqs_rows is not None
                       else (len(rqs_map) if rqs_map is not None else None)),
        "n_verdict_null_unexpected": n_verdict_unexpected,
        "n_postlamfilm_rows": sum(1 for r in cls_rows
                                  if r["sgc_raw"] == "segPostLamInspection"),
        "n_peeltest_rows_deadbranch": sum(
            1 for r in cls_rows
            if r["sgc_raw"] == "segPostLaminationPeelTestVisualInspection"),
        "n_eol_rows": sum(1 for r in cls_rows if r["mcl"] == EOL_MLC),
        "n_eol_forced_pass": sum(1 for r in cls_rows if r["mcl"] == EOL_MLC
                                 and r["verdict"] == RESULT_PASS and r["res"] == RESULT_FAIL),
        "n_assy_rows": n_assy,
        "n_assy_null_prqdate": sum(1 for r in cls_rows
                                  if r["mcl"] == ASSY_MLC and r["eff_ts"] is None),
        "eol_branch": eol_branch,
        "assy_ts_branch": assy_ts_branch,
        "station_count_check": scc,
        "identity_check": ident,
        "qaconfig": "not_replicated", "golden": "not_replicated",
        "desig_filter": "not_replicated",
        "timing": dict(timing or {}),
        "degraded": degraded,
        # --- additive 自揭（非 pin，B2 不必讀）---
        "n_end_rows": len(end_rows),
        "n_allphid_total": len(anchors),
        "n_disp_rows": len(disp),
        "n_verdict_null_branch": sum(1 for r in cls_rows if r["verdict"] == "Null"),
        "n_lots_with_fail": len(ff),
        "n_absorbed_negative": n_neg,
    }

    return {
        "schema": SCHEMA_ID,
        "caliber": CALIBER_QLIK,
        "product": product,
        "win_start": ws, "win_end": we, "cap": cap,
        "stations": out_stations,
        "kpis": kpis,
        "absorption": absorb,
        "absorbed_total": sum(x["n_lots"] for x in absorb),
        "diag": diag,
    }


def compute_qlik_grid(cur, win, cap, *, product="plover"):
    """唯一 I/O 編排點：column_guard → [PK] → [W] → #temp → [H] →（有 ASSY 列才）[PR]
       → 純函式鏈 → `build_grid`。每階段計時進 `diag.timing`（比照 [G5]）。

    ⚠ `cur` 必須是同一條連線的 cursor（`#temp` 存活於 connection 生命週期）。
    """
    try:
        profile = PRODUCTS[product]
    except KeyError:
        raise ValueError(f"unknown Qlik product profile: {product!r}") from None
    ws, we = _win(win)
    cap = _dt(cap)
    timing, st = {}, {}

    t0 = time.perf_counter()
    guard = fetch_column_guard(cur)
    timing["guard_ms"] = int((time.perf_counter() - t0) * 1000)
    has_mcl = guard["mclMaterialClassID"]
    has_status = guard["matStatus"]
    has_rqs_col = guard["rqsPK"]                 # process_segment_v 側（hist 的 join 鍵）
    has_family = bool(guard.get("SegmentProductLineCode"))   # #98：非承重，缺 ⇒ 不拆
    rqs_available = bool(guard["view_rqs"] and guard["rqsDateStarted"]
                         and guard["rqsDateRequested"]
                         and guard.get("rqs_rqsPK"))   # ← production_request_v 側 SELECT 欄

    t0 = time.perf_counter()
    packmin = fetch_pack_boundary(cur, cap, has_mcl=has_mcl, stats=st)
    timing["pk_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    lots = fetch_window_lots(cur, (ws, we), has_mcl=has_mcl, stats=st)
    timing["w_ms"] = int((time.perf_counter() - t0) * 1000)

    # `#temp` 灌檔與 [H] 拉取**分開計時**（原本混在 h_ms ⇒ 逐列 round-trip 的成本被讀成
    # 「[H] 很慢」；MED C-3）。total_ms 仍是全部 *_ms 之和 ⇒ 合計不變。
    t0 = time.perf_counter()
    n_temp = None
    if lots:
        n_temp = _mk_lots_temp(cur, lots)
    timing["temp_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    if lots:
        try:
            hist = fetch_history(cur, cap, has_mcl=has_mcl, has_status=has_status,
                                 has_rqs=has_rqs_col, collate_side="temp", stats=st,
                                 has_family=has_family)
        except Exception as e:                      # PROBES3 檔頭排障 ②：COLLATE 換側回退
            if "collation conflict" not in str(e).lower():
                raise
            hist = fetch_history(cur, cap, has_mcl=has_mcl, has_status=has_status,
                                 has_rqs=has_rqs_col, collate_side="view", stats=st,
                                 has_family=has_family)
    else:
        hist = []
    timing["h_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    n_assy_raw = sum(1 for r in hist if len(r) > H_MLC and r[H_MLC] == ASSY_MLC)
    rqs_map = {}
    if hist and n_assy_raw and rqs_available:
        rqs_map = fetch_rqs(cur, cap, has_mcl=guard["rqs_mcl"], stats=st)
    assy_branch = assy_branch_of(len(hist), n_assy_raw, rqs_available, rqs_map)
    timing["pr_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    cls_rows = classify_rows(hist, packmin, rqs_map, has_mtlstatus=has_status, stats=st)
    grid = build_grid(
        cls_rows, (ws, we), cap,
        packmin=packmin, rqs_map=rqs_map, has_mtlstatus=has_status, has_mcl=has_mcl,
        n_win_lots=len(lots), n_dup_rows_removed=st.get("n_dup_rows_removed", 0),
        n_rqs_src_pairs=st.get("n_rqs_src_pairs"), n_rqs_rows=st.get("n_rqs_rows"),
        eol_branch=("implemented" if has_status else "SKIPPED(matStatus ABSENT)"),
        assy_ts_branch=assy_branch, timing=timing,
        n_bad_rows=st.get("n_bad_cls_rows", 0), n_lots_temp=n_temp,
        has_family=has_family, stations=profile["stations"], expected=profile["expected"],
        groups=profile["groups"], ptypes=profile["ptypes"], product=product,
        display=profile["display"], display_src=profile.get("display_src"))
    timing["derive_ms"] = int((time.perf_counter() - t0) * 1000)
    timing["total_ms"] = sum(v for k, v in timing.items() if k.endswith("_ms")
                             and k != "total_ms")
    grid["diag"]["timing"] = timing
    grid["diag"]["column_guard"] = guard
    return grid


def run(win=None, days=None, cap=None, timeout=QUERY_TIMEOUT_SEC, *, product="plover"):
    """CLI/snapshot 用的 live 編排（開一條連線、跑完關掉）。回 canonical grid。

    · `days` 給值 ⇒ 滾動窗 [now-days, now)、cap=now（live 語意）。
    · `win` 給值 ⇒ 固定窗；`cap` 未給則取 MES `GETDATE()`（見 §4.2 cap 語意）。
    """
    conn, cur = _connect(timeout=timeout)
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        if days is not None:
            win = (now - timedelta(days=days), now)
        if win is None:
            raise ValueError("either --days or --start/--end is required")
        grid = compute_qlik_grid(cur, win, cap if cap is not None else now, product=product)
        grid["mes_now"] = now
        return grid
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =============================================================================
# 6. 呈現
# =============================================================================
def _p(x, w=8, nd=3):
    return (f"{x:>{w}.{nd}f}%" if isinstance(x, (int, float)) else f"{'—':>{w}}")


def render(grid):
    d = grid["diag"]
    profile = PRODUCTS.get(grid.get("product"), PRODUCTS["plover"])
    print(f"=== Qlik 口徑（first-FAIL ＋ RTY）　窗 [{grid['win_start']}, {grid['win_end']})　"
          f"cap={grid['cap']} ===")
    print(f"  口徑＝{grid['caliber']}")
    if d["degraded"]:
        print("  ⚠ DEGRADED：資料品質門檻未過，以下數字標記為不可信（見 diag）。")
    print(f"\n  ── 7 個 RTY KPI ──")
    for name, _lo, _hi in profile["groups"]:
        k = grid["kpis"][name]
        print(f"    {name:<10}{_p(k['pct'])}   （站 {k['n_stations']}／空 {k['n_empty']}"
              f"／全FAIL {k['n_allfail']}）")
    print(f"\n  ── {len(profile['stations'])} 站（ord 序；—＝窗內無單位，非 0%）──")
    print(f"    {'#':>3} {'站':<46}{'TOTAL':>7}{'FPY':>9}{'PASS':>7}{'FAIL':>6}{'FINAL':>9}"
          f"{'吸收':>6}")
    print("    " + "-" * 96)
    for s in grid["stations"]:
        print(f"    {s['ord']:>3} {s['display'][:45]:<46}{s['total']:>7}"
              f"{_p(s['fpy_pct'], 8)}{s['pass']:>7}{s['fail']:>6}"
              f"{_p(s['final_yield_pct'], 8)}{s['absorbed_elsewhere']:>6}")
    print(f"\n  ── 隱形吸收（D6）：first-FAIL 落在本表以外 ＝ {grid['absorbed_total']} 顆 ──")
    if grid["absorption"]:
        for a in grid["absorption"]:
            print(f"    {a['n_lots']:>5}  {a['bucket']:<18}{a['station']}  ({a['mcl']})")
        print("    ⚠ 可見 FAIL 加總 ≠ 實際死亡顆數——上列即差額來源。")
    else:
        print("    （本窗無吸收；空清單也是有效資訊）")
    print(f"\n  ── diag ──")
    for k in ("n_win_lots", "n_lots_temp", "n_hist_rows", "n_dup_rows_removed", "n_dup_psgpk",
              "n_bad_rows", "cap_before_window_end", "n_pack_lots",
              "n_tie_allphid", "n_eff_null", "n_verdict_null_unexpected", "n_postlamfilm_rows",
              "n_peeltest_rows_deadbranch", "n_eol_rows", "n_eol_forced_pass", "n_assy_rows",
              "n_assy_null_prqdate", "eol_branch", "assy_ts_branch", "mcl_filter",
              "station_count_check", "identity_check", "qaconfig", "golden", "desig_filter",
              "timing", "degraded", "caveats"):
        print(f"    {k:<28}{d.get(k)}")


# =============================================================================
# 7. CLI
# =============================================================================
def _hint(e):
    low = str(e).lower()
    if isinstance(e, SchemaDivergence) or "schema divergence" in low or "invalid column" in low:
        return (" → **schema 分歧**（報表 view 少了承重欄）。不是連線問題、也不是本窗無資料；"
                "先對 `_QLIK-RECON-MAIN.md` §9.1 重新校 [G0] 欄位守衛，勿改數字。")
    if any(k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")):
        return " → 連不到 MES。確認已連 VPN/內網。"
    if "login failed" in low or "28000" in str(e):
        return " → 登入被拒。確認 AD 帳號對報表庫有讀權。"
    return ""


def main():
    ap = argparse.ArgumentParser(
        description="Tendline Board Qlik 口徑（first-FAIL ＋ RTY）站級/線級良率·產出（唯讀）")
    ap.add_argument("--days", type=int, default=None, help="滾動窗天數（cap=now）")
    ap.add_argument("--start", default=None, help="固定窗起（含），YYYY-MM-DD[THH:MM]")
    ap.add_argument("--end", default=None, help="固定窗迄（不含）")
    ap.add_argument("--cap", default=None,
                    help="全史上界（as-of）。對表驗收＝該次 Qlik reload 時刻；未給＝GETDATE()")
    ap.add_argument("--product", default="plover", choices=tuple(PRODUCTS),
                    help="產品平行表：plover 或 kestrel")
    ap.add_argument("--json", action="store_true", help="輸出 canonical JSON（＝snapshot 檔格式）")
    ap.add_argument("--selftest", action="store_true", help="離線確定性單測（不連 MES）")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    win = None
    if args.start or args.end:
        if not (args.start and args.end):
            print("[mes_qlik] --start 與 --end 必須成對給")
            sys.exit(2)
        if args.days is not None:
            # 互斥：`run()` 內是 days 優先，靜默覆蓋顯式固定窗 ⇒ 對表驗收會拿滾動窗數字
            # 去對固定窗螢幕常數（最貴的一種「看起來對不上」）。這裡直接擋下、不猜意圖。
            print("[mes_qlik] --days 與 --start/--end 互斥（固定窗對表請勿帶 --days）")
            sys.exit(2)
        win = (_dt(args.start), _dt(args.end))
    elif args.days is None:
        args.days = 7

    try:
        grid = run(win=win, days=args.days, cap=(_dt(args.cap) if args.cap else None),
                   product=args.product)
    except Exception as e:
        print(f"[mes_qlik] 查詢失敗: {type(e).__name__}{_hint(e)}\n  {str(e)[:300]}")
        sys.exit(2)

    if args.json:
        import json
        print(json.dumps(grid, ensure_ascii=False, default=str))
        return
    render(grid)


# =============================================================================
# 8. 離線單測（純合成資料、零 MES、零網路；§5 的 12 組案例）
# =============================================================================
class _T:
    def __init__(self):
        self.n = 0
        self.bad = []

    def ok(self, cond, label):
        self.n += 1
        if not cond:
            self.bad.append(label)
        return bool(cond)

    def eq(self, got, exp, label):
        return self.ok(got == exp, f"{label}: got={got!r} exp={exp!r}")

    def close(self, got, exp, tol, label):
        return self.ok(got is not None and abs(got - exp) < tol,
                       f"{label}: got={got!r} exp≈{exp!r}")


class _FakeCur:
    """離線假 cursor：讓 `compute_qlik_grid` 整條 I/O 編排在**零網路**下可測。

    只認得本模組真正會送出的 SQL（[G0] guard／[PK]／[W]／`#temp` DDL／[H]／[PR]）；
    其餘 ⇒ 空結果（防呆）。`fast_executemany` 當一般屬性收下供斷言。
    """

    def __init__(self, guard_row, pack_rows=(), win_rows=(), hist_rows=(), rqs_rows=()):
        self.guard_row = tuple(guard_row)
        self.pack_rows = [tuple(r) for r in pack_rows]
        self.win_rows = list(win_rows)
        self.hist_rows = [tuple(r) for r in hist_rows]
        self.rqs_rows = [tuple(r) for r in rqs_rows]
        self.sql_log = []
        self.executemany_calls = []
        self._next = []
        self.fast_executemany = None      # 末行：`_FakeCurNoFEM` 覆寫本行即 raise

    def execute(self, sql, *params):
        self.sql_log.append((sql, params))
        s = sql.strip()
        if s.startswith("SELECT CASE WHEN OBJECT_ID"):
            self._next = [self.guard_row]
        elif s.startswith("SELECT v.matMaterialLotID, v.stpPK"):      # [PK]（先於 [W] 比對）
            self._next = list(self.pack_rows)
        elif s.startswith("SELECT v.matMaterialLotID"):               # [W]
            self._next = [(x,) for x in self.win_rows]
        elif s.startswith("SELECT v.stpPK"):                          # [H]
            self._next = list(self.hist_rows)
        elif s.startswith("SELECT r.rqsPK"):                          # [PR]
            self._next = list(self.rqs_rows)
        else:                                                         # #temp DDL
            self._next = []
        return self

    def executemany(self, sql, seq):
        self.executemany_calls.append((sql, len(list(seq))))

    def fetchone(self):
        return self._next[0] if self._next else None

    def fetchall(self):
        return list(self._next)


class _FakeCurNoFEM(_FakeCur):
    """驅動不支援 `fast_executemany` 的假 cursor（設屬性即 raise）＝防禦路徑釘子。"""

    def __init__(self, *a, **kw):
        object.__setattr__(self, "_armed", False)
        super().__init__(*a, **kw)
        object.__setattr__(self, "_armed", True)

    def __setattr__(self, k, v):
        if k == "fast_executemany" and getattr(self, "_armed", False):
            raise AttributeError("this driver does not support fast_executemany")
        object.__setattr__(self, k, v)


def _guard_row_map(**over):
    """[G0] guard 的 {key: 0/1} dict（預設全 PRESENT）；欄序一律走 `GUARD_KEYS`。"""
    d = {k: 1 for k in GUARD_KEYS}
    d.update(over)
    return d


def _guard_row(**over):
    """[G0] guard row tuple（**鍵序＝`GUARD_KEYS` 現算**、絕不硬編欄數）。"""
    d = _guard_row_map(**over)
    return tuple(d[k] for k in GUARD_KEYS)


def _selftest():
    print("=== mes_qlik --selftest（Qlik 口徑確定性語意；純離線合成資料）===")
    t = _T()

    PH, PC, PPC = "assyModule", "assyUnit", "assyPackagedUnit"
    DA = "segDiePlacement"                                   # ord 10
    CAL = "segCalibrationTest"                       # ord 32
    AutoOQC = "segAutomatedOQC"       # ord 33
    VOQC = "segFinalOQC"                # ord 34
    ASSY = "segFinalAssembly"                         # ord 30（螢幕缺列）
    FILM = "segPostLamInspection"              # 隱形吸收站（強制 FAIL）
    PEEL = "segPostLaminationPeelTestVisualInspection"    # 死分支

    def D(day, h=0, mi=0):
        return datetime(2026, 7, day, h, mi)

    WIN = (D(24), D(31))
    CAP = datetime(2026, 7, 31, 13)

    def R(pk, lot, ts, res, sgc, mcl, status=None, rqs=None, fam=None):
        return (pk, lot, ts, res, sgc, mcl, status, rqs, fam)

    def grid_of(rows, packmin=None, rqs=None, **kw):
        cls = classify_rows(rows, packmin or {}, rqs or {},
                            has_mtlstatus=kw.pop("has_mtlstatus", True))
        return build_grid(cls, WIN, CAP, packmin=packmin, rqs_map=rqs, **kw)

    def row(g, ordn):
        return g["stations"][ordn - 1]

    # ---- 0. 常數表健全性（人工審定清單的自檢；接錯＝全部作廢）------------------
    t.eq(len(QLIK_STATIONS), 43, "0.1 43 站")
    t.eq([s[0] for s in QLIK_STATIONS], list(range(1, 44)), "0.2 ord 1..43")
    t.eq(len({(s[1], s[2]) for s in QLIK_STATIONS}), 43, "0.3 (站,mcl) 唯一")
    t.eq(sorted(QLIK_DISPLAY), list(range(1, 44)), "0.4 顯示名涵蓋 1..43")
    for name, lo, hi in KPI_GROUPS:
        n = sum(1 for o, _c, _m in QLIK_STATIONS if lo <= o <= hi)
        t.eq(n, KPI_EXPECTED_N[name], f"0.5 KPI 因子數 {name}")
    t.eq(_ORD_BY_KEY[(CAL, PC)], 32, "0.6 CAL=ord32")
    t.eq(_ORD_BY_KEY[(DA, PH)], 10, "0.7 DA=ord10")
    t.ok((FILM, PH) not in _WHITELIST_KEYS, "0.8 PostLamFilm 不在 43 站白名單")
    t.ok((ASSY_STATION, ASSY_MLC) not in _WHITELIST_KEYS, "0.9 segASSY 不在白名單")
    t.ok(safe_pct(1, 0) is None and safe_pct(1, 2) == 50.0, "0.10 safe_pct den=0→None")
    try:
        _sql_list(("seg'; DROP TABLE x --",))
        t.ok(False, "0.11 _sql_list 應拒絕非識別字")
    except ValueError:
        t.ok(True, "0.11 _sql_list 拒絕非識別字")

    # ---- 0A. Kestrel 45 站（ZsmHb 實際因子；#101 直接沿用這一份）--------------
    t.eq(len(KESTREL_STATIONS), 45, "0A.1 Kestrel 45 站")
    t.eq([s[0] for s in KESTREL_STATIONS], list(range(1, 46)), "0A.2 Kestrel ord 1..45")
    t.eq(len({s[1] for s in KESTREL_STATIONS}), 45, "0A.3 Kestrel cls 無重複")
    t.ok(all(s[2] in ("assyModule", "assyUnit", "assyPackagedUnit")
             for s in KESTREL_STATIONS), "0A.4 Kestrel mcl 只准三種組裝類")
    t.eq([s[1] for s in KESTREL_STATIONS[:17]], [s[1] for s in QLIK_STATIONS[:17]],
         "0A.5 Kestrel 前 17 站逐字等於 Plover")
    t.eq(KESTREL_STATIONS[44][1], "segVacuumPackInspect",
         "0A.6 Kestrel 第 45 站是 Vacuum Packing")
    t.eq([s[1] for s in KESTREL_STATIONS[36:38]], [AutoOQC, VOQC],
         "0A.7 Kestrel 37/38 是 AutoOQC/VOQC")

    # ---- 1. 跨站復測時序倒置（J0SYN14 型；地雷 C／D7）------------------------
    # CAL 末次 FAIL stpPK=900 @07-28；AutoOQC FAIL stpPK=800 @07-29（stpPK 較小、時戳較晚）
    # ⇒ first-FAIL 必須記 AutoOQC（stpPK 裁決）；用時戳或站序都會錯記 CAL。
    rows1 = [R(700, "J0SYN14", D(25, 8), "PASS", CAL, PC),
             R(900, "J0SYN14", D(28, 9), "FAIL", CAL, PC),
             R(800, "J0SYN14", D(29, 10), "FAIL", AutoOQC, PC)]
    c1 = classify_rows(rows1, {}, {})
    e1 = collapse_end_rows(c1)
    f1 = first_fail_map(e1)
    t.eq(f1, {"J0SYN14": 800}, "1.1 first-FAIL 取 min stpPK（非時戳/站序）")
    g1 = grid_of(rows1)
    t.eq((row(g1, 32)["total"], row(g1, 32)["pass"], row(g1, 32)["fail"]), (1, 1, 0),
         "1.2 CAL 顯 PASS（被吸收）")
    t.eq(row(g1, 32)["n_endfail"], 1, "1.3 CAL raw 末次仍是 FAIL")
    t.eq(row(g1, 32)["absorbed_elsewhere"], 1, "1.4 CAL absorbed_elsewhere=1")
    t.eq((row(g1, 33)["pass"], row(g1, 33)["fail"]), (0, 1), "1.5 AutoOQC 記 FAIL")
    t.eq(row(g1, 32)["final_yield_pct"], 100.0, "1.6 CAL final=100%")
    t.eq(row(g1, 33)["final_yield_pct"], 0.0, "1.7 AutoOQC final=0%")
    t.eq(g1["absorbed_total"], 0, "1.8 落點在螢幕上 ⇒ absorbed_total=0")
    t.eq((row(g1, 32)["fpy_num"], row(g1, 32)["fpy_den"]), (1, 1), "1.9 CAL FPY 首次=PASS")
    t.eq(row(g1, 33)["fpy_pct"], 0.0, "1.10 AutoOQC FPY=0%")

    # ---- 2. 強制判定三分支（＋sf 五分支）------------------------------------
    c2a = classify_rows([R(1, "J0001", D(25), "PASS", FILM, PH)], {}, {})
    t.eq(c2a[0]["verdict"], "FAIL", "2.1 PostLamFilm raw PASS → 強制 FAIL")
    c2b = classify_rows([R(2, "J0001", D(25), "PASS", DA, ASSY_MLC, None, 55)], {},
                        {55: D(26)})
    t.eq(c2b[0]["station"], ASSY_STATION, "2.2 ASSY 站名改寫 segASSY")
    t.eq(c2b[0]["sgc_raw"], DA, "2.3 sgc_raw 與 station 兩欄並存（地雷 G）")
    t.eq(c2b[0]["verdict"], "FAIL", "2.4 ASSY 判定恆 FAIL")
    t.eq(c2b[0]["eff_ts"], D(26), "2.5 ASSY 時戳改寫 rqsDateStarted")
    c2b2 = classify_rows([R(2, "J0001", D(25), "PASS", DA, ASSY_MLC, None, 55)], {}, {})
    t.ok(c2b2[0]["eff_ts"] is None, "2.6 rqs 缺 → eff_ts None（不退回 stpStartTime）")
    c2c = classify_rows([R(3, "J0001", D(25), "FAIL", DA, EOL_MLC, "WIP")], {}, {})
    t.eq(c2c[0]["verdict"], "PASS", "2.7 line-end+WIP → 強制 PASS")
    c2c2 = classify_rows([R(3, "J0001", D(25), "FAIL", DA, EOL_MLC, "WIP")], {}, {},
                         has_mtlstatus=False)
    t.eq(c2c2[0]["verdict"], "FAIL", "2.8 matStatus ABSENT → 不默用強制 PASS、退 raw")
    g2 = build_grid(c2c2, WIN, CAP, has_mtlstatus=False)
    t.eq(g2["diag"]["eol_branch"], "SKIPPED(matStatus ABSENT)", "2.9 eol_branch SKIPPED")
    c2c3 = classify_rows([R(3, "J0001", D(25), "FAIL", DA, EOL_MLC, "Scrapped")], {}, {})
    t.eq(c2c3[0]["verdict"], "FAIL", "2.10 line-end 非 WIP/Available → raw")
    # sf 五分支
    t.eq(classify_rows([R(9, "J0X", D(25), "PASS", DA, PH)], {}, {})[0]["sf"], "Fresh",
         "2.11 sf①未包裝→Fresh")
    t.eq(classify_rows([R(300, "J0X", D(25), "PASS", "segShipPackaging", PH)],
                       {"J0X": 100}, {})[0]["sf"], "Fresh", "2.12 sf②FRESH_CLASSES")
    t.eq(classify_rows([R(300, "J0X", D(25), "PASS", DA, EOL_MLC, "WIP")],
                       {"J0X": 100}, {})[0]["sf"], "Fresh", "2.13 sf③line-end/ASSY mcl")
    t.eq(classify_rows([R(100, "J0X", D(25), "PASS", DA, PH)],
                       {"J0X": 100}, {})[0]["sf"], "Fresh", "2.14 sf④(pk-1)<pk_first 邊界")
    t.eq(classify_rows([R(101, "J0X", D(25), "PASS", DA, PH)],
                       {"J0X": 100}, {})[0]["sf"], "Sorting",
         "2.15 sf⑤(pk-1)==pk_first → Sorting（(stpPK-1) 怪癖）")
    t.eq(classify_rows([R(1, "J0X", D(25), "PASS", PEEL, PH)], {}, {})[0]["verdict"],
         "FAIL", "2.16 PeelTest 強制 FAIL 分支照抄（活碼上不可達）")

    # ---- 3. 隱形吸收（D6）---------------------------------------------------
    rows3 = [R(5, "J0INV", D(24, 1), "PASS", FILM, PH),      # 強制 FAIL、隱形站
             R(10, "J0INV", D(25), "PASS", DA, PH)]          # 可見站顯示列
    g3 = grid_of(rows3)
    t.eq((row(g3, 10)["total"], row(g3, 10)["pass"], row(g3, 10)["fail"]), (1, 1, 0),
         "3.1 隱形吸收後可見站全 PASS")
    t.eq(g3["absorbed_total"], 1, "3.2 absorbed_total=1")
    t.eq(g3["absorption"], [{"station": FILM, "mcl": PH,
                             "bucket": BUCKET_INVISIBLE, "n_lots": 1}],
         "3.3 absorption 桶＝invisible_station")
    t.eq(g3["diag"]["n_postlamfilm_rows"], 1, "3.4 n_postlamfilm_rows 自揭")
    t.ok(all(s["cls"] != FILM for s in g3["stations"]), "3.5 隱形站不在 43 列")
    # Sorting-cut：同 lot 的 Fresh 顯示列 + Sorting 側 FAIL 末次列
    rows3b = [R(50, "J0SORT", D(24, 2), "PASS", DA, PH),     # (50-1)<100 → Fresh
              R(200, "J0SORT", D(25), "FAIL", DA, PH)]       # (200-1)>=100 → Sorting
    g3b = grid_of(rows3b, packmin={"J0SORT": 100})
    t.eq(row(g3b, 10)["fail"], 0, "3.6 Fresh 側顯 PASS（FAIL 在 Sorting 側）")
    t.eq(g3b["absorption"], [{"station": DA, "mcl": PH,
                              "bucket": BUCKET_SORTING, "n_lots": 1}],
         "3.7 absorption 桶＝sorting_cut")
    t.eq(g3b["absorbed_total"], 1, "3.8 sorting_cut 計入 absorbed_total")
    # 四桶分類器（含結構性不可達的 non_b 分支）
    t.eq(_ff_bucket(DA, PH, "Fresh", "J"), BUCKET_ON_SCREEN, "3.9 桶①on_screen")
    t.eq(_ff_bucket(DA, PH, "Sorting", "J"), BUCKET_SORTING, "3.10 桶②sorting_cut")
    t.eq(_ff_bucket(DA, PH, "Fresh", "V"), BUCKET_NON_B, "3.11 桶③non_b（M0 lot）")
    t.eq(_ff_bucket(FILM, PH, "Fresh", "J"), BUCKET_INVISIBLE, "3.12 桶④invisible")
    t.eq(_ff_bucket(DA, PH, "Sorting", "V"), BUCKET_SORTING,
         "3.13 桶序承重：Sorting 的 M0 → sorting_cut 而非 non_b")
    t.eq(_ff_bucket(DA, PPC, "Fresh", "J"), BUCKET_INVISIBLE,
         "3.14 白名單是 (站,mcl) 複合鍵")

    # ---- 4. tie（末次側／首次側都全留＝fan-out 忠實）------------------------
    rows4 = [R(1, "J0TIE", D(25), "PASS", DA, PH),
             R(2, "J0TIE", D(26), "PASS", DA, PH),
             R(3, "J0TIE", D(26), "PASS", DA, PH),           # 末次 tie
             R(4, "J0TIE2", D(25), "PASS", DA, PH),          # 首次 tie
             R(5, "J0TIE2", D(25), "FAIL", DA, PH),
             R(6, "J0TIE2", D(27), "PASS", DA, PH)]
    g4 = grid_of(rows4)
    t.eq(row(g4, 10)["total"], 3, "4.1 末次 tie 全留（COUNT(*) 忠實）")
    t.eq(row(g4, 10)["n_allphid"], 2, "4.2 n_allphid distinct 自揭")
    t.eq(g4["diag"]["n_tie_allphid"], 1, "4.3 n_tie_allphid=1")
    t.eq(g4["diag"]["n_dup_psgpk"], 0, "4.4 無重複 stpPK")
    t.eq((row(g4, 10)["fpy_num"], row(g4, 10)["fpy_den"]), (2, 3),
         "4.5 首次 tie 全留（絕不塌 min stpPK）")
    t.close(row(g4, 10)["fpy_pct"], 200.0 / 3, 1e-9, "4.6 FPY=2/3")

    # ---- 5. RTY 恆等（[4C]）＋ VOQC 免費錨點 --------------------------------
    rby = {}
    for i, (o, _c, _m) in enumerate(QLIK_STATIONS):
        rby[o] = (90 + (i % 7), 100 + (i % 7))              # 每站不同因子
    k5 = rty(rby)
    core5 = _rty_core(rby)
    t.eq({n: k5[n]["n_stations"] for n in KPI_EXPECTED_N}, dict(KPI_EXPECTED_N),
         "5.1 七 KPI 因子數")
    t.close(core5["OVERALL"][0], core5["PH"][0] * core5["PC"][0] * core5["PPC"][0], 1e-12,
            "5.2 OVERALL ≡ PH×PC×PPC")
    t.close(core5["PH"][0], core5["BEFORE_DA"][0] * core5["FROM_DA"][0], 1e-12,
            "5.3 PH ≡ BeforeDA×FromDA")
    t.close(k5["VOQC"]["pct"], 100.0 * rby[34][0] / rby[34][1], 1e-9, "5.4 VOQC=單站 final")
    # 在真 grid 上驗 identity 與 VOQC≡ord34 final
    rows5 = [R(1, "J0V1", D(25), "PASS", VOQC, PC),
             R(2, "J0V2", D(25), "FAIL", VOQC, PC),
             R(3, "J0V3", D(25), "PASS", DA, PH)]
    g5 = grid_of(rows5)
    t.eq(g5["diag"]["identity_check"], "ok", "5.5 identity_check ok")
    t.eq(g5["diag"]["station_count_check"], "ok", "5.6 station_count_check ok")
    t.eq(g5["kpis"]["VOQC"]["pct"], row(g5, 34)["final_yield_pct"], "5.7 VOQC≡ord34 final")
    t.eq(row(g5, 34)["final_yield_pct"], 50.0, "5.8 ord34 final=50%")
    t.close(g5["kpis"]["OVERALL"]["pct"], 50.0, 1e-9,
            "5.9 OVERALL=50%（其餘站 1/1 或 1/den）")
    t.ok(g5["diag"]["degraded"] is False, "5.10 正常盤不 degraded")

    # ---- 6. 三個守衛（den=0 / num=0 / 外層）---------------------------------
    t.ok(row(g5, 30)["final_yield_pct"] is None, "6.1 ord30 無資料 → final None（不是 0）")
    t.eq(row(g5, 30)["ord"], 30, "6.2 ord30 列仍在（不從表上消失）")
    t.eq(row(g5, 30)["cls"], ASSY, "6.3 ord30=segFinalAssembly")
    c6 = _rty_core({34: (0, 5)})
    t.close(c6["VOQC"][0], 0.2, 1e-12, "6.4 num=0 守衛 → 1/den（非 0、非跳站）")
    t.eq(c6["VOQC"][3], 1, "6.5 n_allfail 自揭")
    c6b = _rty_core({})
    t.eq(c6b["VOQC"][0], 1.0, "6.6 den=0 → 1/1（跳站）")
    k6 = rty({})
    t.ok(all(k6[n]["pct"] is None for n in KPI_EXPECTED_N),
         "6.7 外層守衛：全空 → pct 全 None（絕不 100.0）")
    k6b = rty({o: (1, 1) for o in range(1, 44)}, stations=QLIK_STATIONS[:5])
    t.ok(all(v["pct"] is None for v in k6b.values()), "6.8 因子數不符 → pct 全 None")
    g6 = build_grid(classify_rows(rows5, {}, {}), WIN, CAP, stations=QLIK_STATIONS[:5])
    t.ok(g6["diag"]["station_count_check"].startswith("!!"), "6.9 站清單接錯 → 自檢報錯")
    t.ok(g6["diag"]["degraded"] is True, "6.10 站清單接錯 → degraded")

    # ---- 7. FPY 未篩 Fresh（T-Q3；刻意與 Fresh 切面分歧）--------------------
    rows7 = [R(1, "J0FR", D(25), "PASS", DA, PH),                      # Fresh
             R(200, "J0SO", D(25), "FAIL", DA, PH)]                    # Sorting
    g7 = grid_of(rows7, packmin={"J0SO": 100})
    t.eq(row(g7, 10)["total"], 1, "7.1 TOTAL＝Fresh 切面（1）")
    t.eq(row(g7, 10)["fpy_den"], 2, "7.2 FPY 分母未篩 Fresh（2）")
    t.eq(row(g7, 10)["fpy_pct"], 50.0, "7.3 FPY=50%")
    t.eq(row(g7, 10)["final_yield_pct"], 100.0, "7.4 FINAL（Fresh 切面）=100%")

    # ---- 8. 窗錨 Start 側（地雷 A）------------------------------------------
    rows8a = [R(1, "J0PRE", D(20), "PASS", DA, PH),        # anchor 在窗前
              R(2, "J0PRE", D(26), "PASS", DA, PH)]
    g8a = grid_of(rows8a)
    t.eq(row(g8a, 10)["total"], 0, "8.1 anchor 在窗前 → 不顯示（末次在窗內也不算）")
    rows8b = [R(1, "J0EXT", D(30), "PASS", DA, PH),        # anchor 在窗內
              R(2, "J0EXT", datetime(2026, 7, 31, 10), "FAIL", DA, PH)]  # 生涯延伸到窗後 ≤cap
    g8b = grid_of(rows8b)
    t.eq(row(g8b, 10)["total"], 1, "8.2 anchor 落窗 → 顯示")
    t.eq(row(g8b, 10)["fail"], 1, "8.3 first-FAIL 用全史（窗後的末次 FAIL 照算）")
    rows8c = [R(1, "J0OUT", D(20), "FAIL", DA, PH),        # 窗外死亡（DA 單位 anchor 在窗前）
              R(2, "J0OUT", D(25), "PASS", CAL, PC)]       # 窗內另一站
    g8c = grid_of(rows8c)
    t.eq(row(g8c, 32)["fail"], 0, "8.4 窗外死亡 → 窗內站照樣 PASS（地雷 A）")
    t.eq(row(g8c, 10)["total"], 0, "8.5 窗外死亡的那個單位本身不顯示")
    # [3F] 的 on_screen 判定＝(白名單站, Fresh, B) 三條件，**不看該 first-FAIL 單位是否落窗**
    # （落窗與否是 [3C] 的 `ff_unit_in_window` 另欄自揭）⇒ 此例歸 on_screen、不進吸收帳。
    # 照抄不改：absorption 的桶語意必須與探針逐字一致，否則對表會多出無法歸因的差。
    t.eq(g8c["absorbed_total"], 0, "8.6 落白名單站的 ff → on_screen（不看是否落窗；[3F] 逐字）")

    # ---- 9. eff_ts NULL 排除（審查 #3/#5）----------------------------------
    rows9 = [R(1, "J0ASSY", D(25), "PASS", DA, ASSY_MLC, None, 77)]
    g9 = grid_of(rows9, rqs={})
    t.eq(g9["diag"]["n_eff_null"], 1, "9.1 n_eff_null 自揭")
    t.eq(g9["diag"]["n_assy_null_prqdate"], 1, "9.2 n_assy_null_prqdate 自揭")
    t.eq(g9["diag"]["n_end_rows"], 0, "9.3 全 NULL AllPHID 整組不進末次收斂")
    t.eq(g9["diag"]["n_lots_with_fail"], 0, "9.4 無幽靈 first-FAIL 候選")
    t.eq(sum(s["total"] for s in g9["stations"]), 0, "9.5 grid 全零")
    t.ok(g9["diag"]["assy_ts_branch"].startswith("SKIPPED"), "9.6 assy_ts_branch SKIPPED")
    t.eq(build_grid([], WIN, CAP)["diag"]["assy_ts_branch"],
         "UNKNOWN(no history rows -> upstream [H] not run/failed)",
         "9.7 兩種 skip 分開具名（空 hist ≠ 無 ASSY 列）")
    t.eq(build_grid(classify_rows([R(1, "J0A", D(25), "PASS", DA, PH)], {}, {}),
                    WIN, CAP)["diag"]["assy_ts_branch"], "vacuous(0 ASSY rows)",
         "9.8 有料但無 ASSY 列 → vacuous")

    # ---- 10. verdict Null / 不可能分支 --------------------------------------
    rows10 = [R(1, "J0N", D(25), None, DA, PH)]
    g10 = grid_of(rows10)
    t.eq((row(g10, 10)["total"], row(g10, 10)["pass"], row(g10, 10)["fail"],
          row(g10, 10)["n_null"]), (1, 0, 0, 1), "10.1 res None → disp Null、total 照計")
    t.ok(row(g10, 10)["final_yield_pct"] is None, "10.2 全 Null → final None")
    t.eq(g10["diag"]["n_verdict_null_branch"], 1, "10.3 n_verdict_null_branch")
    t.eq(g10["diag"]["n_verdict_null_unexpected"], 0, "10.4 'Null' 分支不算 unexpected")
    t.ok(g10["diag"]["degraded"] is False, "10.5 Null 分支不 degraded")
    rows10b = [R(1, "J0U", D(25), "UNKNOWN", DA, PH)]
    g10b = grid_of(rows10b)
    t.eq(g10b["diag"]["n_verdict_null_unexpected"], 1, "10.6 ELSE None → unexpected>0")
    t.ok(g10b["diag"]["degraded"] is True, "10.7 unexpected>0 → degraded")

    # ---- 11. 形狀不變式 ------------------------------------------------------
    g11 = build_grid([], WIN, CAP)
    t.eq(g11["schema"], SCHEMA_ID, "11.1 schema pin")
    t.eq(g11["caliber"], CALIBER_QLIK, "11.2 caliber pin")
    t.eq((g11["win_start"], g11["win_end"], g11["cap"]), (D(24), D(31), CAP),
         "11.3 窗/cap 回傳")
    t.eq(len(g11["stations"]), 43, "11.4 空輸入仍 43 列")
    t.eq([s["ord"] for s in g11["stations"]], list(range(1, 44)), "11.5 ord 1..43")
    t.ok(all(s["total"] == 0 and s["pass"] == 0 and s["fail"] == 0
             for s in g11["stations"]), "11.6 空輸入全零")
    t.ok(all(s["final_yield_pct"] is None and s["fpy_pct"] is None
             for s in g11["stations"]), "11.7 空輸入 → 全 None（不是 0/100）")
    t.ok(all(g11["kpis"][n]["pct"] is None for n in KPI_EXPECTED_N), "11.8 空窗 KPI 全 None")
    t.eq(sorted(g11["kpis"]), sorted(KPI_EXPECTED_N), "11.9 7 個 KPI key 固定")
    t.eq(g11["diag"]["identity_check"], "n/a(all empty)", "11.10 全空 → n/a(all empty)")
    t.ok(g11["diag"]["degraded"] is False, "11.11 全空不算 degraded")
    t.eq((g11["absorption"], g11["absorbed_total"]), ([], 0), "11.12 空吸收")
    t.eq(g11["diag"]["qaconfig"], "not_replicated", "11.13 qaconfig 自揭")
    t.eq((g11["diag"]["golden"], g11["diag"]["desig_filter"]),
         ("not_replicated", "not_replicated"), "11.14 golden/desig 自揭")
    for g, lbl in ((g1, "case1"), (g3, "case3"), (g3b, "case3b"), (g4, "case4"),
                   (g5, "case5"), (g7, "case7"), (g8b, "case8b"), (g10, "case10")):
        t.ok(all(s["fail"] <= s["n_endfail"] for s in g["stations"]),
             f"11.15 fail ≤ n_endfail（{lbl}）")
        t.ok(all(s["absorbed_elsewhere"] >= 0 for s in g["stations"]),
             f"11.16 absorbed_elsewhere ≥ 0（{lbl}）")
        t.ok(all(s["pass"] + s["fail"] <= s["total"] for s in g["stations"]),
             f"11.17 pass+fail ≤ total（{lbl}）")
        t.ok(all(s["total"] - s["pass"] - s["fail"] == s["n_null"]
                 for s in g["stations"]), f"11.18 差＝n_null（{lbl}）")
        t.eq(g["absorbed_total"], sum(x["n_lots"] for x in g["absorption"]),
             f"11.19 absorbed_total ≡ Σ absorption（{lbl}）")
        t.eq(len(g["stations"]), 43, f"11.20 恆 43 列（{lbl}）")

    # ---- 12. 8 欄 DISTINCT ---------------------------------------------------
    dup = [R(1, "J0D", D(25), "PASS", DA, PH), R(1, "J0D", D(25), "PASS", DA, PH),
           R(2, "J0D", D(26), "PASS", DA, PH)]
    ded = _dedup_rows(dup)
    t.eq(len(ded), 2, "12.1 完全重複列去重")
    t.eq(ded[0][0], 1, "12.2 去重保序")
    g12 = build_grid(classify_rows(ded, {}, {}), WIN, CAP,
                     n_dup_rows_removed=len(dup) - len(ded))
    t.eq(g12["diag"]["n_dup_rows_removed"], 1, "12.3 n_dup_rows_removed 自揭")
    g12b = build_grid(classify_rows(dup, {}, {}), WIN, CAP)   # 未去重＝雙計現形
    t.eq(g12b["diag"]["n_dup_psgpk"], 0, "12.4 dup 未落末次列時 n_dup_psgpk=0")
    dup2 = [R(9, "J0D2", D(26), "PASS", DA, PH), R(9, "J0D2", D(26), "PASS", DA, PH)]
    g12c = build_grid(classify_rows(dup2, {}, {}), WIN, CAP)
    t.eq(g12c["diag"]["n_dup_psgpk"], 1, "12.5 末次層殘差探針（8 欄 DISTINCT 沒吃掉的重複）")
    t.eq(row(g12c, 10)["total"], 2, "12.6 未去重會讓 total 灌水（＝為何必須去重）")

    # ---- 13. 輔助：_dt / _win / _pack_min ------------------------------------
    t.eq(_dt("2026-07-24"), datetime(2026, 7, 24), "13.1 _dt 日期")
    t.eq(_dt("2026-07-31T13:00"), datetime(2026, 7, 31, 13), "13.2 _dt 時分")
    t.eq(_dt("2026-07-31 13:00:00"), datetime(2026, 7, 31, 13), "13.3 _dt 空白分隔")
    t.eq(_dt(CAP), CAP, "13.4 _dt datetime 直通")
    try:
        _win((D(31), D(24)))
        t.ok(False, "13.5 _win 應拒絕反向窗")
    except ValueError:
        t.ok(True, "13.5 _win 拒絕反向窗")
    t.eq(_pack_min([("A", 5), ("A", 3), ("B", 9), (None, 1), ("C", None)]),
         {"A": 3, "B": 9}, "13.6 _pack_min 取 MIN、丟 None")
    t.eq(_dedup_lots_ci(["J0A", "j0a", "J0B", None, "J0A"]), ["J0A", "J0B"],
         "13.7 _dedup_lots_ci 對齊 #temp 的 CI PRIMARY KEY（大小寫不同 → 不撞 PK）")

    # ---- 14. 承重欄位守衛（[G0]）＝schema 分歧具名停下，不產全零 grid -----------
    good_guard = {"view_seg": True, "view_rqs": True, "matStatus": True, "rqsPK": True,
                  "mclMaterialClassID": True, "rqsDateStarted": True,
                  "rqsDateRequested": True, "rqs_mcl": True,
                  **{c: True for c in REQUIRED_SEG_COLUMNS}}
    t.ok(assert_guard(good_guard) is True, "14.1 全欄在 → 通過")
    for col in REQUIRED_SEG_COLUMNS:
        g = dict(good_guard, **{col: False})
        try:
            assert_guard(g)
            t.ok(False, f"14.2 承重欄 {col} ABSENT 應 raise")
        except SchemaDivergence as e:
            t.ok(col in str(e), f"14.2 承重欄 {col} ABSENT → SchemaDivergence（訊息具名）")
    try:
        assert_guard(dict(good_guard, view_seg=False))
        t.ok(False, "14.3 view ABSENT 應 raise")
    except SchemaDivergence:
        t.ok(True, "14.3 process_segment_v ABSENT → SchemaDivergence")
    t.ok("schema divergence" in _hint(SchemaDivergence("x")).lower()
         or "schema" in _hint(SchemaDivergence("schema divergence: stpPK ABSENT")),
         "14.4 _hint 涵蓋 schema 分歧（不誤導成連線問題）")
    # mcl ABSENT ＝可降級但**必須 degraded**（否則全零 grid 被讀成「本窗無資料」）
    g14 = build_grid(classify_rows(rows5, {}, {}), WIN, CAP, has_mcl=False)
    t.ok(g14["diag"]["degraded"] is True, "14.5 mcl ABSENT → degraded=True（單一信任旗）")
    t.eq(g14["diag"]["mcl_filter"], "SKIPPED(mcl ABSENT)", "14.6 mcl_filter 頂層自揭")
    t.ok(any("mclMaterialClassID ABSENT" in c for c in g14["diag"]["caveats"]),
         "14.7 caveats 具名")
    t.ok(build_grid(classify_rows(rows5, {}, {}), WIN, CAP)["diag"]["degraded"] is False,
         "14.8 mcl 在 → 不 degraded（逐位元同舊行為）")
    t.eq(build_grid(classify_rows(rows5, {}, {}), WIN, CAP)["diag"]["mcl_filter"],
         "applied", "14.9 mcl_filter=applied")
    # ASSY 時戳改寫被跳過、且窗內真有 ASSY 列 ⇒ 那批列整批掉出窗 ⇒ degraded
    t.ok(g9["diag"]["degraded"] is True,
         "14.10 assy_ts_branch SKIPPED ∧ n_assy_rows>0 → degraded（[PR] 讀法）")
    t.ok(any("ASSY timestamp rewrite skipped" in c for c in g9["diag"]["caveats"]),
         "14.11 ASSY caveat 具名")
    t.eq(g5["diag"]["caveats"], [], "14.12 正常盤 caveats 空")
    t.ok(build_grid([], WIN, CAP)["diag"]["degraded"] is False,
         "14.13 空 hist（vacuous ASSY 分支）不因 ASSY 而 degraded")

    # ---- 15. FPY 分母只計非空 res（Count(field) 語意）--------------------------
    rows15 = [R(1, "J0F1", D(25), "PASS", DA, PH),
              R(2, "J0F2", D(25), None, DA, PH)]         # res NULL：不進分母也不進分子
    g15 = grid_of(rows15)
    t.eq((row(g15, 10)["fpy_num"], row(g15, 10)["fpy_den"]), (1, 1),
         "15.1 res None 不進 FPY 分母（否則 FPY 被靜默壓低）")
    t.eq(row(g15, 10)["fpy_pct"], 100.0, "15.2 FPY=100%（非 50%）")
    t.ok(row(g10, 10)["fpy_pct"] is None,
         "15.3 全 res None → FPY None（誠實留白、非 0%）")

    # ---- 16. absorption(disp_rows=) ≡ 自行重算（純效率、零語意差）--------------
    c16 = classify_rows(rows3, {}, {})
    e16 = collapse_end_rows(c16)
    a16 = window_anchors(c16)
    f16 = first_fail_map(e16)
    d16 = display_rows(e16, a16, f16, WIN)
    t.eq(absorption(f16, e16, a16, WIN, disp_rows=d16), absorption(f16, e16, a16, WIN),
         "16.1 傳入已算好的 disp ≡ 內部重算")

    # ---- 17. SQL 面形狀／負斷言（ALL_SQL；2026-08-03 LOW C-1）-------------------
    #   本模組原本零 SQL 面斷言＝perf 鐵則（RECOMPILE／view 上不 DISTINCT／不塞 IN-list／
    #   COLLATE 掛 #temp 側）全靠註解守。移植 `mes_rework`/`mes_operator` 的 ALL_SQL 制度。
    sqls = ALL_SQL()
    all_text = "\n".join(sqls)
    sel = [s for s in sqls if s.lstrip().upper().startswith("SELECT")]
    ddl = [s for s in sqls if not s.lstrip().upper().startswith("SELECT")]
    # #98：[H] 的降級組合由 2×2×2 變成 2×2×2×2（多一個 has_family）⇒ 26 → 42。
    t.eq(len(sqls), 42, "17.1 ALL_SQL 登記 42 條（1 guard＋2×(3＋16)＋3 DDL；新查詢必須登記）")
    t.eq(len(sel), 39, "17.2 其中 39 條 SELECT")
    t.eq(len(ddl), 3, "17.3 三條 #temp DDL/DML（唯一非 SELECT）")
    t.ok(all(s.rstrip().endswith("OPTION (RECOMPILE)") for s in sel),
         "17.4 ★ 每條查詢尾 RECOMPILE（B4 計劃快取鐵則）")
    t.ok(all(" INTO " not in s.upper() for s in sel),
         "17.5 ★ 零 `SELECT … INTO`（2026-08-02 live 事故：帶參數 prepare ＝巢狀 scope）")
    t.ok(all("DISTINCT" not in s.upper() for s in sel),
         "17.6 ★ 5 表大 view 上零 DISTINCT（B4 blocking-aggregate 斷崖）")
    t.ok(all("GROUP BY" not in s.upper() for s in sel),
         "17.7 ★ view 上零 GROUP BY（聚合全 Python）")
    t.ok("matMaterialLotID IN" not in all_text and "lot IN (" not in all_text,
         "17.8 ★ 零動態 lot IN-list（lot 清單一律走 #temp join）")
    t.eq(all_text.count("stpEndTime"), 0,
         "17.9 ★ stpEndTime 全 SQL 文本零出現（[[mes-zero-length-segments]]）")
    t.eq(all_text.count("stpPersonID"), 0, "17.10 ★ stpPersonID 零出現（§24 負斷言）")
    t.ok("OperatorId" not in all_text and "PersonID" not in all_text,
         "17.11 ★ 任何人員識別欄零出現（§24）")
    h_temp = build_history_sql(CAP)[0]
    h_view = build_history_sql(CAP, collate_side="view")[0]
    t.ok("l.lot COLLATE DATABASE_DEFAULT = v.matMaterialLotID" in h_temp,
         "17.12 ★ COLLATE 掛 #temp 側（保留 view 側 seek）")
    t.ok("COLLATE" not in h_temp.split("JOIN")[0],
         "17.13 SELECT 清單零 COLLATE（只在 join 條件）")
    t.ok("v.matMaterialLotID COLLATE Latin1_General_100_CI_AS_SC_UTF8" in h_view,
         "17.14 collation conflict 回退分支＝掛 view 側（犧牲 seek、保證能跑）")
    t.ok(all(LOTS_TEMP in s for s in ddl) and all("dbo." not in s for s in ddl),
         "17.15 DDL 只碰 #temp（唯讀鐵則：對 view 只有 SELECT）")
    t.ok(build_window_lots_sql(WIN)[0].count("stpTestResult IN") == 1
         and h_temp.count("stpTestResult IN") == 1,
         "17.16 ★ Qlik 側恆帶 Process01 判定過濾（with_result=True；少了就不是 Process01）")
    t.ok("stpTestResult IN" in _process01_where(True)
         and "stpTestResult IN" not in _process01_where(True, with_result=False),
         "17.17 with_result 旗只影響判定那道（mes_rework 專用、本模組永不用 False）")
    t.eq(_process01_where(True).count("AND"), 4, "17.18 Process01 四道過濾（逐字照抄）")
    t.eq(build_pack_sql(CAP, has_mcl=True)[1],
         (PACK_CLASS, RESULT_PASS, _dt(QLIK_EPOCH), CAP, PACK_MLC), "17.19 [PK] 參數序")
    t.eq(len(build_pack_sql(CAP, has_mcl=False)[1]), 4, "17.20 [PK] mcl ABSENT 少一參數")
    t.eq(build_window_lots_sql(WIN)[1], WIN, "17.21 [W] 參數＝半開窗")
    t.eq(build_history_sql(CAP)[1], (_dt(QLIK_EPOCH), CAP), "17.22 [H] 參數＝全史 [EPOCH, cap)")
    t.ok("mclMaterialClassID" not in build_history_sql(CAP, has_mcl=False)[0],
         "17.23 mcl ABSENT ⇒ [H] 全文零引用該欄（引用即炸）")
    t.ok("matStatus" not in build_history_sql(CAP, has_status=False)[0]
         and "v.rqsPK" not in build_history_sql(CAP, has_rqs=False)[0],
         "17.24 matStatus/rqsPK ABSENT ⇒ 佔位常值、零引用")

    # ---- 18. [H] 欄序常數對齊（LOW C-2；原本 r[5] 硬編散在兩處）-----------------
    head = build_history_sql(CAP)[0].split("FROM dbo.process_segment_v")[0]
    cols = [c.strip() for c in head.replace("SELECT", "", 1).split(",")]
    t.eq(len(cols), HIST_ROW_ARITY, "18.1 SELECT 恰 9 欄＝HIST_ROW_ARITY")
    t.eq(len(HIST_COLUMNS), HIST_ROW_ARITY, "18.2 HIST_COLUMNS 9 欄")
    for i, name in enumerate(HIST_COLUMNS):
        t.ok(name in cols[i], f"18.3 欄序對齊：位置 {i} ＝ {name}")
    t.eq((H_PK, H_LOT, H_TS, H_RES, H_PSC, H_MLC, H_STATUS, H_PRQ, H_FAM), tuple(range(9)),
         "18.4 H_* 索引常數 0..8")
    t.eq(HIST_COLUMNS[H_MLC], "mclMaterialClassID",
         "18.5 ★ H_MLC 指到 mcl（compute 的 ASSY 列計數靠它；原本硬編 r[5]）")
    t.eq(HIST_COLUMNS[H_PRQ], "rqsPK", "18.6 H_PRQ 指到 rqsPK")
    # ★ #98：家族欄加在**尾巴**、前 8 個索引一個位元不動；去重鍵仍是前 8 欄。
    t.eq(HIST_COLUMNS[H_FAM], "SegmentProductLineCode", "18.7 H_FAM 指到家族原碼欄")
    t.eq(HIST_COLUMNS[:8],
         ("stpPK", "matMaterialLotID", "stpStartTime", "stpTestResult",
          "SegmentClassID", "mclMaterialClassID", "matStatus", "rqsPK"),
         "18.8 ★ 前 8 欄逐位元同 #98 之前（加欄不得動既有欄序）")
    t.eq(HIST_DEDUP_KEY_LEN, 8, "18.9 ★ DISTINCT 鍵仍是 8 欄（家族碼不進去重鍵）")
    t.eq(len(_dedup_rows([R(1, "J0S", D(25), "PASS", DA, PH, fam="plover"),
                          R(1, "J0S", D(25), "PASS", DA, PH, fam="kestrel")])), 1,
         "18.10 ★ 同 stpPK 只差家族碼 ⇒ 仍塌一列（否則 total／pass 灌水）")
    t.ok("v.SegmentProductLineCode" not in build_history_sql(CAP, has_family=False)[0],
         "18.11 家族欄 ABSENT ⇒ [H] 全文零引用（佔位常值）")

    # ---- 19. classify_rows 短列（LOW C-4；原本裸 IndexError）--------------------
    st19 = {}
    c19 = classify_rows([R(1, "J0S", D(25), "PASS", DA, PH), (1, "J0S"), ("x",)],
                        {}, {}, stats=st19)
    t.eq(len(c19), 1, "19.1 短列跳過、好列照常")
    t.eq(st19["n_bad_cls_rows"], 2, "19.2 短列計數進 stats")
    g19 = build_grid(c19, WIN, CAP, n_bad_rows=st19["n_bad_cls_rows"])
    t.eq(g19["diag"]["n_bad_rows"], 2, "19.3 n_bad_rows 進 diag 自揭")
    t.eq(build_grid([], WIN, CAP)["diag"]["n_bad_rows"], 0, "19.4 預設 0（不是 None）")

    # ---- 20. cap 早於窗尾（MED C-2）------------------------------------------
    g20 = build_grid(classify_rows(rows5, {}, {}), WIN, D(30))       # cap < we=D(31)
    t.ok(g20["diag"]["cap_before_window_end"] is True, "20.1 cap<窗尾 → 旗立起")
    t.ok(g20["diag"]["degraded"] is True, "20.2 ★ 併入 degraded（靜默少算 → 誠實降級）")
    t.ok(any("earlier than window end" in c for c in g20["diag"]["caveats"]),
         "20.3 caveat 具名（含 check --cap 指引）")
    t.ok(g5["diag"]["cap_before_window_end"] is False,
         "20.4 ★ 反向釘：cap>窗尾照常、旗不誤觸")
    g20b = build_grid(classify_rows(rows5, {}, {}), WIN, D(31))      # cap == we（live 常態）
    t.ok(g20b["diag"]["cap_before_window_end"] is False and g20b["diag"]["degraded"] is False,
         "20.5 ★ cap==窗尾（live/run(days) 常態）不 degraded")

    # ---- 21. guard 補 rqs_rqsPK（MED C-1）------------------------------------
    gsql = build_column_guard_sql()
    t.eq(gsql.count("CASE WHEN"), len(GUARD_KEYS), "21.1 guard SQL 欄數 ≡ GUARD_KEYS")
    t.ok("'dbo.production_request_v','rqsPK'" in gsql,
         "21.2 ★ fetch_rqs 的 SELECT 欄 rqsPK 進 guard（原本唯一漏網欄）")
    t.eq(GUARD_KEYS[8], "rqs_rqsPK", "21.3 rqs_rqsPK 在 rqs_mcl 之後（欄序 pin）")
    t.eq(GUARD_KEYS[9], "SegmentProductLineCode", "21.3b #98 家族欄在 rqs_rqsPK 之後")
    t.ok("'dbo.process_segment_v','SegmentProductLineCode'" in gsql,
         "21.3c ★ 家族欄進 guard（缺欄走降級、不是裸 Invalid column name）")
    t.ok(assert_guard(dict(good_guard, SegmentProductLineCode=False)) is True,
         "21.3d 家族欄非承重級（ABSENT 不 raise）")
    t.eq(GUARD_KEYS[-len(REQUIRED_SEG_COLUMNS):], REQUIRED_SEG_COLUMNS,
         "21.4 承重欄仍在鍵序尾端")
    t.ok(assert_guard(dict(good_guard, rqs_rqsPK=False)) is True,
         "21.5 rqs_rqsPK 非承重級（ABSENT 不 raise、走 SKIPPED 降級）")
    t.ok(assy_branch_of(5, 1, False, {}).startswith("SKIPPED")
         and "rqsPK" in assy_branch_of(5, 1, False, {}),
         "21.6 來源不可用的 SKIPPED 訊息具名 rqsPK")

    # ---- 22. assy_branch_of 共用（LOW C-3；原本判定字串寫兩份）-------------------
    t.eq(assy_branch_of(0, 0), "UNKNOWN(no history rows -> upstream [H] not run/failed)",
         "22.1 無 hist")
    t.eq(assy_branch_of(5, 0), "vacuous(0 ASSY rows)", "22.2 無 ASSY 列")
    t.eq(assy_branch_of(5, 1, True, {77: D(26)}), "implemented", "22.3 有 rqs map")
    t.eq(assy_branch_of(5, 1, True, {}),
         "SKIPPED(no production-request rows) -> ASSY eff_ts = NULL", "22.4 rqs 無列")
    t.eq(build_grid([], WIN, CAP)["diag"]["assy_ts_branch"], assy_branch_of(0, 0),
         "22.5 ★ build_grid 與編排層共用同一個判定（不再各寫一份）")

    # ---- 23. compute_qlik_grid 端到端（_FakeCur；零網路）------------------------
    hist23 = [R(150, "J0AAA", D(25), "PASS", DA, PH),
              R(160, "J0AAA", D(26), "FAIL", CAL, PC)]
    cur23 = _FakeCur(_guard_row(), pack_rows=[("J0AAA", 999999)],
                     win_rows=["J0AAA"], hist_rows=hist23)
    g23 = compute_qlik_grid(cur23, WIN, CAP)
    t.eq(g23["schema"], SCHEMA_ID, "23.1 canonical schema")
    t.eq((row(g23, 10)["total"], row(g23, 32)["fail"]), (1, 1), "23.2 端到端 grid 正確")
    t.ok(g23["diag"]["degraded"] is False, "23.3 正常路徑不 degraded")
    t.eq(g23["diag"]["n_win_lots"], 1, "23.4 n_win_lots 自揭")
    t.eq(g23["diag"]["n_lots_temp"], 1, "23.5 #temp 實灌筆數自揭（LOW C-5）")
    t.eq(g23["diag"]["column_guard"]["rqs_rqsPK"], True, "23.6 guard 進 diag")
    t.ok(all(k in g23["diag"]["timing"] for k in
             ("guard_ms", "pk_ms", "w_ms", "temp_ms", "h_ms", "pr_ms", "derive_ms", "total_ms")),
         "23.7 ★ temp_ms 與 h_ms 分開計時（MED C-3：灌檔成本不再混進 [H]）")
    t.eq(g23["diag"]["assy_ts_branch"], "vacuous(0 ASSY rows)", "23.8 無 ASSY 列 → vacuous")
    sent23 = "\n".join(s for s, _p in cur23.sql_log)
    t.ok("SELECT r.rqsPK" not in sent23, "23.9 無 ASSY 列 ⇒ [PR] 根本不送（vacuous 守衛）")
    t.ok("stpEndTime" not in sent23 and "stpPersonID" not in sent23,
         "23.10 ★ 端到端送出的 SQL 零 stpEndTime／零人員欄")
    t.eq(cur23.fast_executemany, True, "23.11 #temp 灌檔開 fast_executemany")
    t.eq([n for _s, n in cur23.executemany_calls], [1], "23.12 executemany 一批一列")
    # 降級路徑：rqs_rqsPK ABSENT ＋ 窗內真有 ASSY 列 ⇒ [PR] 不送、SKIPPED、degraded
    hist24 = [R(150, "J0AAA", D(25), "PASS", DA, PH),
              R(170, "J0AAA", D(25), "PASS", DA, ASSY_MLC, None, 55)]
    guard24 = dict(_guard_row_map(), rqs_rqsPK=0)
    cur24 = _FakeCur(tuple(guard24[k] for k in GUARD_KEYS), pack_rows=[("J0AAA", 999999)],
                     win_rows=["J0AAA"], hist_rows=hist24, rqs_rows=[(55, D(26))])
    g24 = compute_qlik_grid(cur24, WIN, CAP)
    t.ok(g24["diag"]["assy_ts_branch"].startswith("SKIPPED"),
         "23.13 ★ rqs_rqsPK ABSENT ⇒ 走既有 SKIPPED 降級（不是裸 pyodbc 例外）")
    t.ok(g24["diag"]["degraded"] is True, "23.14 ASSY 列掉窗 ⇒ degraded")
    t.ok("SELECT r.rqsPK" not in "\n".join(s for s, _p in cur24.sql_log),
         "23.15 缺欄時 [PR] 查詢根本不送（guard 真的擋住）")
    # 同資料、guard 完整 ⇒ [PR] 有送、branch=implemented（對照組）
    cur25 = _FakeCur(_guard_row(), pack_rows=[("J0AAA", 999999)], win_rows=["J0AAA"],
                     hist_rows=hist24, rqs_rows=[(55, D(26))])
    g25 = compute_qlik_grid(cur25, WIN, CAP)
    t.eq(g25["diag"]["assy_ts_branch"], "implemented", "23.16 對照組：guard 全在 → implemented")
    t.ok("SELECT r.rqsPK" in "\n".join(s for s, _p in cur25.sql_log), "23.17 [PR] 有送")
    # 驅動不支援 fast_executemany ⇒ 靜默沿用逐列路徑、功能不炸
    cur26 = _FakeCurNoFEM(_guard_row(), pack_rows=[("J0AAA", 999999)],
                          win_rows=["J0AAA"], hist_rows=hist23)
    g26 = compute_qlik_grid(cur26, WIN, CAP)
    t.eq(row(g26, 32)["fail"], 1, "23.18 ★ 驅動不支援 fast_executemany ⇒ 照跑不炸")
    # #temp 實灌筆數 != 窗內 lot 數（大小寫變體塌掉）⇒ caveat
    cur27 = _FakeCur(_guard_row(), pack_rows=[], win_rows=["J0AAA", "j0aaa"],
                     hist_rows=[R(150, "J0AAA", D(25), "PASS", DA, PH)])
    g27 = compute_qlik_grid(cur27, WIN, CAP)
    t.eq((g27["diag"]["n_win_lots"], g27["diag"]["n_lots_temp"]), (2, 1),
         "23.19 casefold 去重讓 #temp 少一筆")
    t.ok(any("#temp lot count" in c for c in g27["diag"]["caveats"]),
         "23.20 兩者不等 ⇒ caveat 自揭（LOW C-5）")

    # ---- 24. render 回歸保護（LOW C-7；審查輪跑過但沒釘）-----------------------
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        render(g5)          # 正常盤（含吸收空清單）
        render(g3)          # 有吸收列
        render(build_grid([], WIN, CAP))        # 全空盤（KPI 全 None → 「—」格式）
        render(g14)         # degraded 盤
    out24 = buf.getvalue()
    t.ok("Qlik 口徑" in out24 and CALIBER_QLIK in out24, "24.1 render 不炸、標頭有口徑")
    t.ok("DEGRADED" in out24, "24.2 degraded 大聲印出")
    t.ok("本窗無吸收" in out24 and "invisible_station" in out24,
         "24.3 吸收段兩態（空清單／有列）都渲染得出來")
    t.ok("stpPersonID" not in out24 and "EMP00" not in out24, "24.4 render 零人員鍵（§24）")

    # ---- 25. verdict CASE 第一道順序（LOW C-8）--------------------------------
    #   `res is None` **必須先於** FORCED_FAIL：交換順序時，隱形站的 NULL 判定列會從
    #   'Null' 變成 'FAIL' ⇒ 憑空生出 first-FAIL 候選、把可見站的 FAIL 吸走。
    c25 = classify_rows([R(1, "J0NF", D(25), None, FILM, PH)], {}, {})
    t.eq(c25[0]["verdict"], "Null",
         "25.1 ★ res None 先於 FORCED_FAIL（交換順序此列會變 FAIL）")
    t.eq(classify_rows([R(1, "J0NP", D(25), None, DA, ASSY_MLC, None, 9)], {},
                       {9: D(26)})[0]["verdict"], "Null",
         "25.2 res None 亦先於 ASSY 恆 FAIL")
    t.eq(classify_rows([R(1, "J0NE", D(25), None, DA, EOL_MLC, "WIP")], {}, {})[0]["verdict"],
         "Null", "25.3 res None 亦先於 line-end 強制 PASS")
    g25b = grid_of([R(1, "J0NF2", D(25), None, FILM, PH), R(2, "J0NF2", D(25), "PASS", DA, PH)])
    t.eq((row(g25b, 10)["pass"], row(g25b, 10)["fail"]), (1, 0),
         "25.4 ★ NULL 判定的隱形站不吸走可見站（順序若錯這裡會變 fail=1）")
    t.eq(g25b["absorbed_total"], 0, "25.5 無幽靈吸收")

    # ---- 26. #98 家族按 PH（lot）拆 pass（`SPEC-LANDING.md` §18.18）--------------
    #   口徑：**lot 全史任一段有碼 ⇒ 整顆歸該家族**；全史皆無 ⇒ UNLABELED。
    #   總和恆等：`pass == Σ pass_by_family`（拆的是同一個量，不是第二次計數）。
    c26 = classify_rows([R(150, "J0R1", D(25), "PASS", DA, PH, fam="plover"),
                         R(151, "J0R1", D(26), "PASS", CAL, PC),          # 同 lot 的 NULL 段
                         R(160, "J0A1", D(25), "PASS", DA, PH, fam="KESTREL"),
                         R(170, "J0U1", D(25), "PASS", DA, PH)], {}, {})
    m26, nc26 = lot_family_map(c26)
    t.eq(m26.get("J0R1"), "Plover",
         "26.1 ★ lot 全史任一段有碼 ⇒ 整顆歸該家族（NULL 段不推翻）")
    t.eq(m26.get("J0A1"), "Kestrel", "26.2 大小寫走 normalize_family（普查 CASE 逐字）")
    t.ok("J0U1" not in m26, "26.3 ★ 全史皆 NULL ⇒ 不入 map（消費面落 UNLABELED）")
    t.eq(nc26, 0, "26.4 無矛盾 ⇒ n_conflict=0")
    # 矛盾：同一顆 lot 兩種碼 ⇒ 取**最早 stpPK**（不是時戳）並計 n_conflict
    c26b = classify_rows([R(200, "J0X", D(28), "PASS", DA, PH, fam="heron"),
                          R(100, "J0X", D(29), "PASS", CAL, PC, fam="plover")], {}, {})
    m26b, nc26b = lot_family_map(c26b)
    t.eq((m26b.get("J0X"), nc26b), ("Plover", 1),
         "26.5 ★ 碼矛盾取最早 stpPK（100 的 plover 勝、時戳較晚不影響）＋計 n_conflict")
    # grid 層：拆分 ＋ 恆等式 ＋ 鍵序 ＋ 零值不寫鍵
    g26 = grid_of([R(150, "J0R1", D(25), "PASS", DA, PH, fam="plover"),
                   R(151, "J0R2", D(25), "PASS", DA, PH, fam="plover"),
                   R(160, "J0A1", D(25), "PASS", DA, PH, fam="kestrel"),
                   R(170, "J0U1", D(25), "PASS", DA, PH)])
    r26 = row(g26, 10)
    t.eq(r26["pass_by_family"], {"Plover": 2, "Kestrel": 1, FAMILY_UNLABELED: 1},
         "26.6 ★ pass 按家族拆（未標的 lot 落 UNLABELED）")
    t.eq(list(r26["pass_by_family"]), ["Plover", "Kestrel", FAMILY_UNLABELED],
         "26.7 鍵序固定 R→A→M→未標（Heron 為零 ⇒ 不寫鍵）")
    t.eq(sum(r26["pass_by_family"].values()), r26["pass"],
         "26.8 ★★ 恆等式：pass ≡ Σ pass_by_family（總和不變＝口徑不變）")
    t.eq(row(g26, 32)["pass_by_family"], {},
         "26.9 零產出站＝空 dict（『拆了但全零』≠『沒拆』）")
    t.eq(g26["diag"]["family_split"], "applied", "26.10 family_split 自揭")
    t.eq(g26["diag"]["n_family_conflict_lots"], 0, "26.11 矛盾數進 diag")
    # 欄 ABSENT ⇒ 整片 None（**不是**全部 UNLABELED），其餘數字一個位元不動
    g26c = grid_of([R(150, "J0R1", D(25), "PASS", DA, PH, fam="plover")], has_family=False)
    g26d = grid_of([R(150, "J0R1", D(25), "PASS", DA, PH, fam="plover")])
    t.ok(all(s["pass_by_family"] is None for s in g26c["stations"]),
         "26.12 ★ 家族欄 ABSENT ⇒ 整片 None（頁面退回單一數字）")
    t.ok(g26c["diag"]["family_split"].startswith("SKIPPED")
         and g26c["diag"]["degraded"] is False,
         "26.13 ★ 家族欄缺席**不** degraded（加法欄壞了不得毒化報廢腿）")
    t.eq([(s["total"], s["pass"], s["fail"]) for s in g26c["stations"]],
         [(s["total"], s["pass"], s["fail"]) for s in g26d["stations"]],
         "26.14 ★★ 有無家族欄，total／pass／fail 逐位元相同")
    # 8 欄舊列（#98 之前的形／既有合成 fixture）仍合法
    st26 = {}
    c26e = classify_rows([(150, "J0R1", D(25), "PASS", DA, PH, None, None)], {}, {}, stats=st26)
    t.eq((len(c26e), st26["n_bad_cls_rows"], st26["n_hist_rows_no_family"]), (1, 0, 1),
         "26.15 ★ 8 欄列不算壞列（家族視為 NULL、計數自揭）")
    t.eq(order_family_counts(None), None, "26.16 None ⇒ None（沒拆 ≠ 拆了全零）")

    # ---- 27. Kestrel 平行表（#101；共用母體、lot 首碼只在 grid 顯示層切分）--------
    t.eq(PRODUCTS["plover"]["stations"], QLIK_STATIONS, "27.1 Plover profile 沿用 43 站")
    t.eq(PRODUCTS["kestrel"]["stations"], KESTREL_STATIONS, "27.2 Kestrel profile 用 45 站")
    for name, lo, hi in KESTREL_KPI_GROUPS:
        n = sum(1 for o, _c, _m in KESTREL_STATIONS if lo <= o <= hi)
        t.eq(n, KESTREL_KPI_EXPECTED_N[name], f"27.3 Kestrel KPI 因子數 {name}")
    t.ok("Y0" not in LOT_PREFIXES, "27.4 LOT_PREFIXES 不含 H0（母體未放寬）")

    # 同一批 B0/M0 史料下，Plover profile 與只餵 B0 的結果必須逐 dict 相同；家族碼不能偷當切分鍵。
    #   ★ §11.1-1：母體一個位元不動、切分只在顯示層 ⇒ `diag` 的母體計數**該**照實含 M0 列，
    #   所以逐位元比對排除 `diag`（27.5），另釘一根 diag 誠實自揭（27.5b）。
    mix27 = [R(1, "J0P", D(25), "PASS", DA, PH, fam="kestrel"),
             R(2, "V0P", D(25), "PASS", "segGrout", PH, fam="plover"),
             R(3, "V0Q", D(25), "PASS", AutoOQC, PC, fam="kestrel"),
             R(4, "V0R", D(25), "FAIL", AutoOQC, PC, fam="kestrel")]
    g27r = grid_of(mix27)
    g27b = grid_of([mix27[0]])
    t.eq({k: v for k, v in g27r.items() if k != "diag"},
         {k: v for k, v in g27b.items() if k != "diag"},
         "27.5 ★ B0+M0 mixed Plover grid ≡ B0-only grid（diag 以外逐位元相同）")
    t.eq(g27r["diag"]["n_hist_rows"], len(mix27),
         "27.5b ★ 母體共用：diag 母體計數照實含 M0 列（顯示層切分不得回頭改母體）")

    ap = PRODUCTS["kestrel"]
    g27a = grid_of(mix27, stations=ap["stations"], expected=ap["expected"],
                    groups=ap["groups"], ptypes=ap["ptypes"], product="kestrel",
                    display=ap["display"], display_src=ap["display_src"])
    t.ok(g27a["product"] == "kestrel" and len(g27a["stations"]) == 45
         and any(s["total"] > 0 for s in g27a["stations"]),
         "27.6 Kestrel M0 rows enter 45-station grid")
    t.ok(all(s["display_pending"] is False for s in g27r["stations"]),
         "27.7 Plover rows never set display_pending")
    t.ok(all(s["display_source"] == "qlik" for s in g27r["stations"]),
         "27.7b ★ #105 Plover 43 站顯示名來源恆 qlik（加性欄不得改動既有頁面語意）")
    # ---- 27.8 系列（#105，Chris 2026-09-08 裁「先用推導名」）：Kestrel 45 站全部有名 ----
    a_grout = row(g27a, 18)
    t.eq((a_grout["display"], a_grout["display_pending"], a_grout["display_source"]),
         ("Grouting", False, "derived"),
         "27.8 ★ #105 Kestrel 獨有站印推導顯示名（不再 display_pending）")
    t.eq(len(KESTREL_DISPLAY_DERIVED), 12,
         "27.8b ★ 推導名恰 12 個（Kestrel 獨有站數）")
    _ars_only = {c for _o, c, _m in KESTREL_STATIONS} - {c for _o, c, _m in QLIK_STATIONS}
    t.eq(sorted(KESTREL_DISPLAY_DERIVED), sorted(_ars_only),
         "27.8c ★ 推導名的 key 逐字＝Kestrel 獨有站集合（多一個少一個都紅）")
    t.ok(all(s["display_pending"] is False for s in g27a["stations"]),
         "27.8d ★ Kestrel 45 站零 display_pending（12 站已有推導名）")
    t.eq(sorted({s["display_source"] for s in g27a["stations"]}), ["derived", "qlik"],
         "27.8e ★ 來源標記只有兩種值、且兩種都出現（33 共同站 qlik／12 獨有站 derived）")
    t.eq(sum(1 for s in g27a["stations"] if s["display_source"] == "derived"), 12,
         "27.8f ★ derived 恰 12 站")
    t.ok(all(s["display"] != s["cls"] for s in g27a["stations"]),
         "27.8g ★ 45 站都印得出名字、沒有一站退回 canonical id")
    t.eq([row(g27a, o)["display"] for o in (19, 29, 42)],
         ["Post Grout UV Cure", "Create Unit Assembly", "Top O-ring Lubrication"],
         "27.8h ★ 推導名逐字（抄回正式名時這根會紅＝提醒同步換釘）")
    t.eq(row(g27a, 10)["display"], QLIK_DISPLAY[10],
         "27.8i ★ 共同站沿用 QLIK_DISPLAY（ord 10 segDiePlacement 兩表同站）")
    t.eq(_ff_bucket(DA, PH, "Fresh", "J", whitelist=_whitelist_of(KESTREL_STATIONS),
                    ptypes=("V",)), BUCKET_NON_B,
         "27.9 Kestrel non_b means non-product (B0)")
    core27 = _rty_core({s["ord"]: (s["pass"], s["pass"] + s["fail"])
                         for s in g27a["stations"]}, ap["stations"], ap["groups"])
    t.eq(_identity_check(core27, False, need=ap["identity_need"]), "ok",
         "27.10 Kestrel OVERALL/PH identities")
    t.close(g27a["kpis"]["AutoOQC"]["pct"], row(g27a, 37)["final_yield_pct"], 1e-9,
            "27.11 Kestrel AutoOQC ≡ ord37 final（num>0 才成立，見 27.13）")
    try:
        compute_qlik_grid(None, WIN, CAP, product="unknown")
        t.ok(False, "27.12 unknown product 應 raise")
    except ValueError:
        t.ok(True, "27.12 unknown product raise ValueError")
    # ★ Qlik 的 IIF(num==0,1,num)（地雷 J／`_rty_core` docstring）：ord37 有量但全 FAIL
    #   ⇒ 因子被守衛成 1/1 ⇒ AutoOQC 100%，而站列 final_yield_pct=0% —— 兩者**本來就會分岔**，
    #   27.11 那條恆等式只在 num>0 時成立。照抄 Qlik、不「修正」。
    t.close(rty({37: (0, 1)}, ap["stations"], ap["groups"],
                expected=ap["expected"])["AutoOQC"]["pct"], 100.0, 1e-9,
            "27.13 ★ IIF(num==0,1,num)：ord37 全 FAIL ⇒ AutoOQC 100%（Qlik 忠實、非 bug）")

    # ---- 27.14–27.16 Kestrel FPY「一致式」結構釘（2026-09-08 Chris 裁；§11.1-5）--------
    #   Qlik 的 `aNXTHZU` FPY 分子取 `[stpTestResult-First FAIL in Production line]`
    #   （被 Start=PASS 夾住）、分母取 `[stpTestResult-Start]`，與 Plover `HpLuZFN` 不一致。
    #   Chris：「Qlik owner 已經離職，有誤的我們能導正就導正，無法導正就忽略」
    #   ⇒ **Board 兩個產品共用同一支 `fpy_grid`（分子分母同取 Start 欄）＝導正**；Qlik 端不改。
    #   這三根釘的是**結構**，不是某個數字：哪天有人為 Kestrel 另寫一支 FPY，這裡當場紅。
    t.eq(sorted(k for k in globals() if "fpy" in k.lower()), ["fpy_grid"],
         "27.14 ★ 全模組只有 `fpy_grid` 一個 FPY 入口（不得為 Kestrel 另寫一支）")
    t.ok(all("fpy" not in k.lower() for p in PRODUCTS.values() for k in p),
         "27.14b ★ 產品 profile 不帶 per-product FPY 覆寫鍵（切分只有 ptypes）")
    #   同一批史料、同一個站（ord 10 ＝ segDiePlacement，兩張表逐字相同）：B 與 M 各一 PASS 一 FAIL。
    #   ⇒ 兩邊都必須是 (num, den) == (1, 2)：**首次判定 FAIL 的單位照樣進分母**
    #     ＝分母是 Start 欄、不是被 PASS 夾過的子集（Qlik Kestrel 式若被複製，這根會紅）。
    fpy27 = [R(10, "J0F", D(25), "FAIL", DA, PH),
             R(11, "J0G", D(25), "PASS", DA, PH),
             R(12, "V0F", D(25), "FAIL", DA, PH),
             R(13, "V0G", D(25), "PASS", DA, PH)]
    g27fr = grid_of(fpy27)
    g27fa = grid_of(fpy27, stations=ap["stations"], expected=ap["expected"],
                     groups=ap["groups"], ptypes=ap["ptypes"], product="kestrel",
                     display=ap["display"], display_src=ap["display_src"])
    t.eq((row(g27fa, 10)["fpy_num"], row(g27fa, 10)["fpy_den"]), (1, 2),
         "27.15 ★ Kestrel FPY 分母含首次 FAIL 的單位（分子分母同一欄＝一致式）")
    t.eq([(s["fpy_num"], s["fpy_den"]) for s in g27fa["stations"][:17]],
         [(s["fpy_num"], s["fpy_den"]) for s in g27fr["stations"][:17]],
         "27.16 ★ 兩表共同的前 17 站 FPY 逐位元同形（同一支算式、只換 lot 首碼）")

    print(f"\n  斷言 {t.n} 條、失敗 {len(t.bad)} 條")
    for b in t.bad:
        print(f"    FAIL  {b}")
    print(f"\n  → {'PASS ✅' if not t.bad else 'FAIL ❌'}")
    if t.bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
