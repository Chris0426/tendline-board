# -*- coding: utf-8 -*-
"""
wsynth_params.py — 合成產線的參數（SPEC-SYNTHLINE C-SY6）
==========================================================

★★ **手寫，非由任何真實資料推得。** ★★
本檔每一個數字都是人手寫的「看起來像一條產線」的假設值：故障率、修復時長、PM 週期、
稼動水準、開線機率、段長……**沒有一個是從 MES／cmms／eMaint 真資料統計、擬合或抽樣而來**
（N1；聚合統計本身也是內部資訊）。code review 時由主持人抽查。

★ 本檔**只放行為參數**（故障率、修復時長、節拍、開線機率……），**不放台數**：每站台數、
  父機與座數、區域一律以程式名冊為準（第二批裁決第 5 條；`wsynth_world.fleet_from_roster`）。

★ 版號兩個、各管一件事（2026-10-02 v1.1 審查 Finding 1 起解耦）：
  · `GENERATOR_VERSION`＝**世界識別與帳形的版號**：進 `world_id` 的輸入與 manifest。**任何讓已發佈的
    帳位元組改變的變更**（事件內容、多／少事件、seq 位移、schema 字串、MES 段內容）都必須升它，
    使 `world_id` 跟著變——以 `world_id` 當快取鍵或續讀游標的消費者才不會拿舊鍵讀到新形。
  · `WORLD_RNG_SALT`＝**亂數鍵的鹽**：只進 `wsynth_world.Rng`。改它＝世界重生（所有段、工單、事件
    全部重抽）＝裁決 Q15 的「世界重置」，由主持人明示才可改。
  ⇒ 「換帳形但世界不重抽」（例：v1.1 加產料號主檔）＝只升 `GENERATOR_VERSION`、鹽不動；
    「世界重置」＝鹽與版號都動。只改註解兩者都不必動。

編號區段（裁決 Q14／Q19）
  · 工單號：**六位數**（起點見下方 Q-N 定案）；寬度做成參數 `WO_NO_WIDTH`（cmms 確認其工單號無五位數
    假設前照六位數做）。
  · 料號／lot／serial：WP-0 以私有對照表實算空洞後提案、主持人於第二批 Q-N 定案（見下方），
    改了就升版。
  · 第一期 EID 不是合成的（沿用程式名冊，Q2），故本檔不宣告 EID 區段。

要重現的「病」（裁決 Q5）——每條一個旗標，註明對應口徑；關掉旗標該病即從世界消失：
  · `zero_length_stations`：零工期段站（段 start==end）。口徑＝記憶 mes-zero-length-segments；
    `board_attrib.ZERO_LEN_STATIONS`（八站結構性不可測，稼動印「—」）。
  · `on_hold_reasons`：ON_HOLD 各 hold_reason（WAITING_PARTS／WAITING_VENDOR／OTHER＝計停機；
    WAITING_MACHINE_TIME／TEST_RUN＝機台可生產不計）。口徑＝`board_eqmeeting._phase_kind`、
    `UPTIME_HOLD_REASONS`；W8 `HOLD_REASONS_KNOWN`。
  · `one_click_close`：一鍵結單（IN_PROGRESS／COMPLETED／CLOSED 三轉移同一時戳 ⇒ MTTR＝0、
    PM 停機在 /equipment 隱形）。口徑＝記憶 cmms-oneclick-close-mttr（選項 b 誠實標記）。
  · `long_stop`：長停（A11 `not_scheduled`）。口徑＝SPEC-DORMANT-CAPACITY、`board_dormant`、
    `board_capacity` as-of 規則（C-D1：狀態只來自登記，永不由 MES 沉默推導）。
  · `seat_level_wo`：父機座級工單（形③ 父機 scope／座自己的單；形① nest 座 `-N` 工單；
    座級 A11 sub_unit）。口徑＝SPEC-AVAILABILITY C-AV16／§9（R44 `wem.parent_seat_rollup`）。
  不重現（Q5）：ETL 落後、遷移單無 history、重複開單——本檔**沒有**這些旗標，產生器也不產。
"""
from __future__ import annotations

import datetime as _dt

# ---------------------------------------------------------------------------
# 版號與 seed
# ---------------------------------------------------------------------------
GENERATOR_VERSION = "tendline-synth-g5"   # g5（2026-10-25，區域鍵詞彙）：asset.define.area 改中性鍵 za/zb/zc/zd、綑 id 改 ZA-/ZB-/ZC- ⇒ world_id 與 manifest 變；世界內容不重抽（鹽仍 g3）
#   g4（2026-10-02，v1.1 審查 Finding 1）：帳形變（+60 行 inventory.item、seq +60、schema v1.1）⇒ world_id 變；世界內容不重抽（鹽仍 g3）
#   g3（2026-10-02，主持人明示重置；WP-6b）：段永不恰好結束在生產日界 08:00:00（`wsynth_world.off_day_boundary`）
#   g2（2026-09-29，主持人明示准許重置）：wo.scope 時刻不得早於開單；形③ 追加範圍情境調高
#   g1..g3 時代鹽＝版號本身（Rng 以版號入鍵）；g4 起兩者解耦，見檔頭「版號兩個」。

# 亂數鍵的鹽（`wsynth_world.Rng`）。固定為 g3 世界的值 ⇒ g4 的段、工單、事件內容與 g3 逐位元相同
# （影片草稿與已錄鏡頭依賴 g3 世界的事件；`wsynth_selftest` 釘「解耦前後同種子世界內容不變」）。
# **改它＝世界重置**（裁決 Q15，主持人明示）；改時 `GENERATOR_VERSION` 必須同步升。
WORLD_RNG_SALT = "tendline-synth-g3"
DEFAULT_SEED = "tendline-sandbox-s1"

SEED_SCHEMA = "tendline.seed.v1.1"                  # v1.1（2026-10-02）：+inventory.item、三條帳級規則
SEED_MANIFEST_SCHEMA = "tendline.seed.manifest.v1.1"
MES_SCHEMA = "tendline.mes_segments.v1"
MES_MANIFEST_SCHEMA = "tendline.mes_segments.manifest.v1"

# ---------------------------------------------------------------------------
# 時間軸（Q20：上線日往前 90 天；horizon 逐日延長）
#   ⚠ 上線日是**固定常數**、不是「現在」：若從 now 推起點，重啟就會平移整個世界＝破前綴穩定。
#   暫定值；sandbox 真正上線日由主持人定，改它＝升版。
# ---------------------------------------------------------------------------
GO_LIVE_DATE = _dt.date(2026, 10, 1)
HISTORY_DAYS = 90
HORIZON_START = GO_LIVE_DATE - _dt.timedelta(days=HISTORY_DAYS)

# PM 週期相位的錨（與 horizon 無關，避免 horizon 改變時 PM 相位跟著動）
PM_EPOCH = _dt.date(2026, 1, 5)

# ---------------------------------------------------------------------------
# 編號（Q14＋第二批 Q-N 定案；寬度與起點仍是參數）
#   · 工單號 710001–799999（避開舊公開版 700xx 的視覺混淆）。
#   · 料號 TL900001–TL999999。
#   · 序號與「等同序號的 lot」S000001–S0ZZZZZ（首字 S、其後 6 位 base-36、第二位恆 0）。
#   · 非序號 lot L000001–L0ZZZZZ 屬第二期，本期不產。
#   · ⚠ 本產生器**不產 MES 工單號**；若第二期要產，**不得**用「Board 前綴緊接六位數」的形
#     （脫敏規則 `mes_wo` 左側無邊界，R-20）。selftest 釘輸出無此形。
# ---------------------------------------------------------------------------
WO_NO_WIDTH = 6
WO_NO_FIRST = 71 * 10 ** (WO_NO_WIDTH - 2) + 1         # 710001
WO_NO_LAST = 8 * 10 ** (WO_NO_WIDTH - 1) - 1           # 799999

ITEM_CODE_PREFIX = "TL"
ITEM_CODE_WIDTH = 6
ITEM_CODE_FIRST = 9 * 10 ** (ITEM_CODE_WIDTH - 1) + 1   # TL900001
ITEM_CODE_LAST = 10 ** ITEM_CODE_WIDTH - 1              # TL999999
ITEM_CODE_POOL = 60                 # 合成零件主檔大小（手寫）
# seed v1.1 料號主檔（`inventory.item`）：名稱＝模板句，類別依料號索引確定性輪替（不碰 RNG；手寫通用詞）
ITEM_CATEGORIES = ("bearing", "belt", "sensor", "channel", "gasket", "fuse", "cable", "valve",
                   "filter", "seal")
ITEM_NAME_TEMPLATE = "Spare part: {category} #{index}"
# `inventory.item` 的共同欄位 asset_id＝哨兵（料號主檔沒有資產；不是資產、永不由 asset.define 定義）。
# 以運算式組出而非字面：本檔受「無 EID 字面值」掃描（wsynth_selftest [8]，防真 EID 混入）；
# 契約字面值住 `wsynth_seed.ITEM_ASSET_ID`，selftest 釘兩者相等。
ITEM_ASSET_ID = "EID-%0*d" % (5, 0)

SERIAL_PREFIX = "S"                 # 序號與等同序號的 lot（段的 lot＝以 stp_seq 派生的序號）
SERIAL_WIDTH = 6                    # base-36 位數
SERIAL_FIRST = 1                    # S000001
SERIAL_LAST = 36 ** (SERIAL_WIDTH - 1) - 1              # S0ZZZZZ（第二位恆 0）

# ---------------------------------------------------------------------------
# 「病」旗標（Q5）
# ---------------------------------------------------------------------------
DISEASES = ("zero_length_stations", "on_hold_reasons", "one_click_close",
            "long_stop", "seat_level_wo")
DEFAULT_FLAGS = {name: True for name in DISEASES}

# ---------------------------------------------------------------------------
# 角色閉集（Q7：人名欄一律角色字串，永不放人名或帳號）
# ---------------------------------------------------------------------------
ROLES = ("role:operator", "role:technician", "role:engineer", "role:planner", "role:system")

# ---------------------------------------------------------------------------
# 開線（台北牆鐘；生產日界 08:00，C-PC1）
#   每個生產日全廠一個「開不開線」決定；夜班全廠一個「開不開夜班」決定。
# ---------------------------------------------------------------------------
PROD_DAY_START_H = 8
SHIFT_HOURS = 12
# 週一..週日 全廠停線機率（手寫）
P_PLANT_CLOSED_BY_WEEKDAY = (0.02, 0.02, 0.02, 0.02, 0.03, 0.35, 0.85)
P_NIGHT_SHIFT = 0.65

# 每台機的稼動水準（開線時段內「在跑」的機率），以雜湊選一檔（手寫）
UTIL_TIERS = (0.30, 0.50, 0.70, 0.85)
SEG_MINUTES = (10, 90)              # 一段的長度（分）；Q-H：生產 run 切成 10–90 分短段、無段超過 90 分
SEG_GAP_MINUTES = (0, 4)            # 兩段之間的換批空檔（分）
IDLE_MINUTES = (5, 90)              # 閒置塊（分）
P_ENGINEERING_BASELINE = 0.03       # 生產段中標成工程基準批的比例
P_SEG_FAIL = 0.02                   # 段結果 FAIL 的比例
TRIAL_EVERY_MINUTES = (20, 60)      # TEST_RUN 期間試跑段的間隔
TRIAL_SEG_MINUTES = (10, 20)

# ---------------------------------------------------------------------------
# 工單（手寫）
# ---------------------------------------------------------------------------
P_FAIL_TIERS = (0.015, 0.03, 0.05)  # 每台每日 REACTIVE 故障機率，以雜湊選一檔
P_SEAT_WO_NEST = 0.5                # 形① 機台故障落在單一座（`-N` 工單）的比例
P_FAIL_PARENT = 0.15                # 形③ 父機（整組多座）每日 REACTIVE 故障機率（手寫；g2 起）
P_PARENT_SCOPED = 0.8               # 形③ 父機工單帶 scope（只停部分座）的比例（g2：0.6→0.8）
P_SCOPE_CHANGE = 0.8                # 形③ scoped 單中途以 wo.scope 追加一座的比例（g2：0.5→0.8）

# REACTIVE 生命週期原型權重（旗標關掉的原型權重併回 plain）
REACTIVE_ARCHETYPES = (
    ("plain", 0.45),
    ("hold_down", 0.18),            # WAITING_PARTS／WAITING_VENDOR／OTHER
    ("hold_test", 0.10),            # TEST_RUN
    ("hold_wmt", 0.07),             # WAITING_MACHINE_TIME
    ("one_click", 0.15),
    ("cancelled", 0.05),
)
HOLD_DOWN_REASONS = ("WAITING_PARTS", "WAITING_VENDOR", "OTHER")

P_FSA = 0.6                          # 開單時帶 failure_started_at 的比例
FSA_LEAD_MIN = (5, 45)               # 故障到開單的延遲（分）
RESPONSE_MIN = (5, 90)               # 開單到 IN_PROGRESS（分）
REPAIR_MIN = (30, 360)               # 維修時長（分）
HOLD_MIN = {                          # 各 hold_reason 的暫停時長（分）
    "WAITING_PARTS": (12 * 60, 4 * 24 * 60),
    "WAITING_VENDOR": (24 * 60, 3 * 24 * 60),
    "OTHER": (2 * 60, 24 * 60),
    "TEST_RUN": (60, 6 * 60),
    "WAITING_MACHINE_TIME": (4 * 60, 24 * 60),
}
CANCEL_AFTER_MIN = (5, 30)
CLOSE_AFTER_MIN = (10, 48 * 60)      # COMPLETED 到 CLOSED（行政結案）
P_STAY_COMPLETED = 0.1               # 停在 COMPLETED、未行政結案的比例
P_PRA = 0.5                          # plain／hold_down 單有人按「修正停機終點」的比例
PRA_WRITE_LAG_MIN = (0, 9)           # pra 欄寫入時刻落後其值的分鐘數（< CLOSE_AFTER_MIN 下限，保證終態後無事件）

PM_INTERVAL_DAYS = (14, 21, 28, 30)  # 每台 PM 週期，以雜湊選一檔
PM_OPEN_MIN = (0, 60)                # PM 開單時刻＝08:00 + 此分鐘
PM_START_AFTER_MIN = (30, 240)
PM_WORK_MIN = (60, 240)
P_PM_ONE_CLICK = 0.2                 # 旗標開時 PM 一鍵結單比例

# ---------------------------------------------------------------------------
# 長停（A11；手寫）
# ---------------------------------------------------------------------------
P_DORMANT_START_PER_DAY = 0.001
DORMANT_DAYS = (14, 60)
P_DORMANT_OPEN_ENDED = 0.3
A11_RECORD_HOUR = 9

# ---------------------------------------------------------------------------
# 文字模板（Q7：notes／brief 一律模板句＋參數，禁自由文字）
# ---------------------------------------------------------------------------
SYMPTOMS = ("feeder jam", "vacuum pressure alarm", "alignment drift", "sensor fault",
            "motion axis alarm", "temperature out of range", "vision reject spike",
            "dispense volume drift")
ACTIONS = ("replaced worn part", "recalibrated axis", "cleaned and re-seated sensor",
           "adjusted process parameter", "reset controller and verified")
BRIEF_REACTIVE = "Breakdown: {symptom}"
BRIEF_PM = "Preventive maintenance ({interval}-day)"
NOTE_TEMPLATES = {
    "report": "Operator reported {symptom}.",
    "progress": "Technician started work on {symptom}.",
    "hold": "Work paused: {reason}.",
    "resume": "Work resumed after {reason}.",
    "part": "Requested part {item_code} x{quantity}.",
    "note": "Repair complete: {action}.",
}
DIAGNOSIS_TEMPLATE = "Root cause: {symptom}."
ACTION_TEMPLATE = "{action}."
A11_REASON_TEMPLATE = {
    "not_scheduled": "Not scheduled: line plan excludes this unit.",
    "in_production": "Back in production plan.",
}

# 事件 kind 閉集（§5.3；v1.1 起 inventory.item 入閉集並產出；pm.schedule 仍屬第二期保留、不產）
KINDS = ("asset.define", "wo.open", "wo.status", "wo.scope", "wo.note", "wo.part",
         "wo.field", "asset.schedule_state", "inventory.item", "pm.schedule")
KINDS_EMITTED_PHASE1 = ("asset.define", "wo.open", "wo.status", "wo.scope", "wo.note",
                        "wo.part", "wo.field", "asset.schedule_state", "inventory.item")
WO_FIELDS = ("diagnosis", "action_taken", "confirmed_reason_code", "production_resumed_at",
             "downtime_estimated", "scheduled_date", "assigned_vendor")
ASSET_ROLES = ("contains_module", "cotag", "alias_of")
DESIGNATION_PRODUCTION = "Production"
DESIGNATION_BASELINE = "Engineering (Baseline)"
DESIGNATION_TRIAL = "Engineering (R&D)"
DESIGNATIONS = (DESIGNATION_PRODUCTION, DESIGNATION_BASELINE, DESIGNATION_TRIAL)
PRODUCTION_GRADE = (DESIGNATION_PRODUCTION, DESIGNATION_BASELINE)
RESULTS = ("PASS", "FAIL")
