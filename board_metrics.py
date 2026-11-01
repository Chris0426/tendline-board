#!/usr/bin/env python3
"""Tendline Board — Hermes 指標登記表（`SPEC-HERMES-TRUST.md` §2；Phase A / A1a）。

身分
----
本模組是 Hermes 「現在多少／怎麼算／對不對」那條路的**語意面**：把五頁（landing `/`、
`/utilization`、`/station/{cls}`、`/equipment`、`/daily`）上每一個會被念出口的數字，
登記成「一句公式 ＋ 一串引用」。值不在這裡；值走 `board_mcp` 的既有唯讀工具。

三條鐵則（違反即作廢）
----------------------
1. **C-HT2 存引用、不抄 SPEC**：本檔只放一句公式與 `module.name`／卡號／i18n 鍵的引用。
   SPEC 永遠是裁決正本；抄過來的那一份會自己長大，然後沒有人知道哪一份是真的。
2. **零 I/O、零 `app` 匯入**：登記表是常數＋引用，不是資料層。本檔不連 MES、不讀網路、
   不匯入任何目標模組——`--selftest` 對目標模組一律走 `ast` 靜態解析（§2.5-2）。
3. **`id` 發布後不改名**：改口徑＝換一個新 `id`（同 C-MR8 慣例），不得就地改語意。

轉錄規則（`ALL_METRICS` 怎麼從 SPEC §2.6 五張表來的）
--------------------------------------------------
- `id`／`formula["zh"]`／`formula_ref`／`inputs`／`caliber`／`unit`／`contracts`／`status`
  逐字照表；表裡的「同上」**逐列展開成上一列的實值**；`formula_ref`／`inputs` 只留
  `module.name`，去掉括號裡的 `檔:行`。
  `formula["zh"]` 只去掉 markdown 粗體標記（兩顆星號），其餘一字不動。
- `formula["en"]` 是本檔把 zh 那句翻成的一句英文（一句、無換行、≤160 字元）。
- `freshness` 由 `_freshness_by_rule()` 機械判定（規則見該函式），**不逐筆手填**。
  **規則是頁面優先**：`pages` 含 `/daily` 的一律 `ledger_daily`（2026-09-19 修正輪裁；
  `/daily` 整頁的材料都由日帳供，caliber 不是 `daily-ledger` 的那兩筆也一樣）。
- `encoding` 只在視覺桶／顏色類指標上填，值＝**既有**圖例鍵 → 意義；找不到既有鍵就 `None`。
- `i18n_keys` 只放**六個** I18N 家族裡真的存在、且今天真的在解釋這個數字的鍵：
  `app.py` 的 `I18N`／`_EQ_I18N`／`_AV_I18N`／`_LD_I18N`／`_MR_I18N`，加上
  `board_attrib.py` 的 `I18N_ATTRIB`（`/daily` 歸因桶的標籤住那裡）。
  找不到留空，**不編鍵名**。
  ★ A4（#138）：這些鍵裡「解釋數字的 tooltip／圖例／註記」那一批，**文字本身**已搬進本檔 `TIPS`
    （第 2a 節；頁面讀的仍是原鍵、只是值由 `merge_tips()` 在 import 期灌進家族字典）——所以 `i18n_keys`
    指到的鍵現在可能住在六個字典**或** `TIPS`；`--selftest` 第 4 條兩處都認、第 10 條釘「只住一處」。

`VALUE_PATHS`（A2 加；**純常數表、零 I/O**）
------------------------------------------
`get_metric(id, scope, window)` 要知道「這一筆的值從哪個既有 loader 的哪個鍵拿」。
**那張對照表放這裡（常數），取值的碼住 `board_mcp.get_metric()`**——本檔的鐵則②
（零 I/O、零 `app` 匯入）不因為 A2 而放寬：值解析層若住這裡，第 8 條負斷言會當場紅。
表裡全部是字串與 tuple；`loaders` 只是**名字**，本檔不 import、不呼叫它們。
`--selftest` 第 9 條驗：每個 key ∈ `ALL_METRICS`、每個 `loaders` 名以 AST 靜態解析得到、
`scope_kind`／`transform` 在 enum 內。

`inputs` 有三種參照（`--selftest` 第 2 條依序認）
------------------------------------------------
1. **登記表自身的 metric id**（SPEC §2.6 就是這樣寫的，如 `util.planned_h`、
   `station.rate_good`、`station.oee` 的「上三者」）⇒ 在 `ALL_METRICS` 裡解析。
2. **裸模組名**（如 `mes_wip`、`board_attrib`）⇒ 只驗 `<repo 根>/<模組>.py` 存在。
3. **`module.name`** ⇒ 驗模組檔存在，且 `name` 是該檔 `ast` 頂層的 `def`／`class`／賦值名。
其餘（SPEC 表裡的散文，如「A11 拉取」）＝**追不到**；該筆 `status` 轉 `draft`，
理由逐條記在 `REVIEW_NOTES`。
⚠ **裸模組名只准出現在 `status="draft"` 的指標上**（2026-09-19 修正輪裁）：`verified` 的
指標若留裸模組名，`--selftest` 第 2 條直接 FAIL——`verified` 的意思是「追到 `檔:行`」，
指到一整個檔不算追到。

CLI
---
    python board_metrics.py --selftest        # §2.5 九條（A2 加第 9 條 VALUE_PATHS；A4 加第 10 條 TIPS）
    python board_metrics.py --json
    python board_metrics.py --list [page]
    python board_metrics.py --explain <id> [en|zh]
"""
from __future__ import annotations

import ast
import glob
import io
import json
import os
import re
import sys
from dataclasses import dataclass, fields

try:                                        # Windows console 中文（與 mes_* 同形）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                           # pragma: no cover - 老 runtime 沒有這支
    pass

BASE = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# 0. enum 常數（§2.2／§2.3；**沿用既有值、不新造**）
# =============================================================================
CALIBERS = frozenset({
    "qlik-first-fail", "raw-machine-level", "raw-equipment-axis",
    "dual", "rw-planned", "daily-ledger",
    # ★ 2026-09-22（助理準確性包①；`SPEC-HERMES-TRUST.md` §12.13）：`updown_now` 換源到
    #   `/equipment` 那張板 ⇒ **名冊與視角都換了**（近 N 天 MES 出現過的機台／母機視角
    #   → `EQMT_CATALOG` 固定名冊／子工位視角／長停另桶）。**換名冊就是換口徑**——不可沿用
    #   `raw-equipment-axis`（那把尺講的是「MES 段的 interval-union」，與這張板的工單視角
    #   不是同一件事）⇒ 立一個新值，讀端一看 `caliber` 就知道這個 DOWN 數字是哪張板的。
    "eqmt-board-now",
    # ★ 2026-09-23 #167 P4：`/downtime` 的尺＝工單日帳月視圖（每月一把尺、逐月標 `axis`）。
    "wo-ledger",
    # ★ 2026-09-26 #175：`knowledge_card`（知識卡）的尺＝**文件值**（官方製程規範／設備手冊某版次
    #   的摘要），不是任何量測軸。沿用 `raw-equipment-axis`（零數字工具的預設填法）會讓讀端以為
    #   卡上的規格／參數是 MES 量到的——卡上**有數字**（參數、限值、PM 週期），而且與 MES 不同時
    #   以 MES 為準 ⇒ 立一個新值，一看 `caliber` 就知道「這是文件、不是資料」。
    "doc-reference",
})
# ⚠ `static` ＝**登記表自己的常數、不是量測**（A1b 修正輪加）：`list_metrics`／`explain_metric`
#   的出處信封用它。原本填 `fixture` 會讓讀者以為「這個定義是假資料」——定義是版本化的常數，
#   假的是 demo 的**數字**，兩件事不可共用一個值。**本模組的指標資料不用它**（指標描述的是
#   頁面上的數字、其新鮮度仍由 `_freshness_by_rule()` 判 live／ledger_daily／…）。
FRESHNESS = frozenset({"live", "ledger_daily", "frozen_month", "fixture", "static"})
UNITS = frozenset({"PH", "%", "h", "count", "ratio"})
STATUS = frozenset({"verified", "draft"})
LANGS = ("en", "zh")

# `caliber` 的一句話語意（§2.3 表的「語意」欄）——`persona_rules_summary()` 的唯一出處。
CALIBER_NOTE = {
    "en": {
        "qlik-first-fail": ("company station/line caliber: each unit counts as one failure, "
                            "booked at the station where it first failed"),
        "raw-machine-level": ("machine-level raw: retests count as failures, so it never "
                              "reconciles with the company caliber"),
        "raw-equipment-axis": ("equipment time axis: interval-union over every segment, "
                               "NO product-view de-duplication"),
        "dual": "one payload deliberately mixes two axes; each block carries its own axis label",
        "rw-planned": "expected run window axis: the denominator is the sum of |RW(m)|",
        "daily-ledger": "daily time ledger (equipment axis, raw; work orders split by S12)",
        "eqmt-board-now": ("the /equipment board's \"right now\" view: fixed "
                           "EQMT_CATALOG roster, sub-position rows, DOWN = an open countable "
                           "work order, registered not-scheduled machines in their own bucket"),
        "wo-ledger": ("work-order ledger month view (/equipment/downtime/monthly): per-machine union of cmms work-order "
                      "downtime per production day; each month is EITHER clipped to the expected "
                      "production time OR on the 24h clock (field `axis`), never mixed, never comparable"),
        "doc-reference": ("document reference (knowledge cards): values summarised from official process "
                          "specs / equipment manuals at the listed revision — not measurements; MES wins "
                          "when they differ"),
    },
    "zh": {
        "qlik-first-fail": "公司站／線口徑：每個 PH 只算一次不良、記在第一次壞掉那站",
        "raw-machine-level": "機台級 RAW：重測算不良，與公司口徑天生對不起來",
        "raw-equipment-axis": "設備時間軸：interval-union 計入所有 segment、NO 產品視角去重",
        "dual": "同一 payload 刻意混兩軸，逐區塊自帶軸標籤",
        "rw-planned": "應生產時段（RW）軸：分母＝Σ_m |RW(m)|",
        "daily-ledger": "日帳時間帳本（設備軸／raw、工單按 S12）",
        "eqmt-board-now": ("設備會議板 `/equipment` 的「此刻」視角：固定名冊 `EQMT_CATALOG`、"
                           "子工位列、DOWN＝有可計停機的開啟工單、登記長停另成一桶"),
        "wo-ledger": ("工單日帳月視圖（/equipment/downtime/monthly）：每生產日機台工單停機 union；每個月**只用一把尺**"
                      "（應生產時段剪裁或 24h 軸，欄位 `axis`），不混、兩尺不可比"),
        "doc-reference": "文件參考（知識卡）：官方製程規範／設備手冊某版次的摘要值、不是量測；與 MES 不同時以 MES 為準",
    },
}

# 路由字串（`pages`／`verify.page_url_tpl` 的唯一出處）
P_LANDING = "/"
P_UTIL = "/utilization"
P_STATION = "/station/{cls}"
P_EQUIP = "/equipment"
P_DAILY = "/daily"
P_DOWNTIME = "/equipment/downtime/monthly"   # #167 P4；09-24 改名（舊 `/downtime` 301）
P_MONTHLY = "/monthly"                       # 2026-10-02 助理工具第二批 ③（`yield_summary.monthly_loss`）

EXPECT_DEFAULT = "頁面同一格的數字與工具回傳相等"


# =============================================================================
# 1. `Metric`（§2.2 欄位定死；加欄要走 SPEC §13 裁決）
# =============================================================================
@dataclass(frozen=True)
class Metric:
    """一個會被念出口的數字。欄位順序＝`SPEC-HERMES-TRUST.md` §2.2 表的順序。"""

    id: str
    pages: tuple[str, ...]
    title: dict[str, str]
    formula: dict[str, str]
    formula_ref: str
    inputs: tuple[str, ...]
    caliber: str
    unit: str
    encoding: dict[str, str] | None
    pitfalls: tuple[str, ...]
    freshness: str
    verify: dict
    contracts: tuple[str, ...]
    i18n_keys: tuple[str, ...]
    status: str


def _freshness_by_rule(mid: str, caliber: str, pages: tuple[str, ...]) -> str:
    """freshness 判定規則（**機械判定、不逐筆手填**；2026-09-19 修正輪改成頁面優先）。

    ① **頁面優先**：`pages` 含 `/daily` ⇒ `ledger_daily`。整頁的材料都由日帳供，
       所以 caliber 是 `qlik-first-fail`／`raw-equipment-axis` 的那兩筆
       （`daily.good_rate`／`daily.families`）**也是** `ledger_daily`。
    ② caliber `daily-ledger` ⇒ `ledger_daily`。`station.cap_equiv_ph` 走的是這條：
       它長在 live 的 `/station` 瀑布上，但速率腿綁在日帳，是**綁定腿**。
    ③ landing Q2／Q3 的月數字（`landing.q2_*`／`landing.q3_*`）⇒ `ledger_daily`。
    ④ 其餘（機隊狀態、座、`/utilization`、`/station`、`/equipment`）⇒ `live`。
    """
    if P_DAILY in pages:
        return "ledger_daily"
    if caliber == "daily-ledger":
        return "ledger_daily"
    if caliber == "wo-ledger":                 # #167：工單日帳每日 08:40 落帳
        return "ledger_daily"
    if mid.startswith("landing.q2_") or mid.startswith("landing.q3_"):
        return "ledger_daily"
    return "live"


def _m(mid, page, title_en, title_zh, f_en, f_zh, formula_ref, inputs, caliber, unit,
       contracts=(), i18n_keys=(), pitfalls=(), encoding=None, status="verified",
       expect=None, pages=None) -> Metric:
    """逐筆建構（`verify` 三鍵與 `freshness` 由規則填，避免逐筆手抄出錯）。"""
    pg = tuple(pages) if pages else (page,)
    ins = tuple(inputs)
    return Metric(
        id=mid,
        pages=pg,
        title={"en": title_en, "zh": title_zh},
        formula={"en": f_en, "zh": f_zh},
        formula_ref=formula_ref,
        inputs=ins,
        caliber=caliber,
        unit=unit,
        encoding=encoding,
        pitfalls=tuple(pitfalls),
        freshness=_freshness_by_rule(mid, caliber, pg),
        verify={"page_url_tpl": page,
                "ssms_ref": ins[0] if ins else "",
                "expect": expect or EXPECT_DEFAULT},
        contracts=tuple(contracts),
        i18n_keys=tuple(i18n_keys),
        status=status,
    )


# =============================================================================
# 2. 登記表（`SPEC-HERMES-TRUST.md` §2.6 五張表，共 54 筆；2026-09-23 `#169` 加 `equip.cell_equiv_ph`；2026-09-23 `#167` P4 加 `/downtime` 八筆＝62）
# =============================================================================
_METRICS: tuple[Metric, ...] = (

    # ---------------------------------------------------------------- (A) landing `/`
    _m("landing.fleet_producing_n", P_LANDING, "Producing machines", "生產台數",
       "Number of roster machines whose visual bucket is Producing right now.",
       "名冊機台中此刻落在「生產」視覺桶的台數",
       "app._ld_state_from_board",
       ("app._ld_state", "board_eqmeeting.build_board"),
       "raw-equipment-axis", "count", ("C-LD18",),
       ("ld_q1_run", "ld_lg_run"),
       ("SPEC-LANDING.md §11 C-LD18 — 狀態層只讀快取、永不重建。",
        "SPEC-LANDING.md §12 A4 — MES 是分鐘級，秒級刷新只刷出假的即時感。"),
       {"ld_lg_run": "生產桶（Producing）"}),

    _m("landing.fleet_down_n", P_LANDING, "Down machines", "停機台數",
       "Number of machines in the Down bucket (the RW_DOWN_STATES whitelist; Unknown is excluded).",
       "落在「停機」桶的台數（＝`RW_DOWN_STATES` 白名單，不含「不明」）",
       "app._ld_state_from_board",
       ("app._ld_state", "board_eqmeeting.build_board"),
       "rw-planned", "count", ("C-LD18",),
       ("ld_q1_down", "ld_lg_down"),
       ("SPEC-LANDING.md §11 C-LD2 — 不知道≠壞了；停機桶只收 RW_DOWN_STATES 白名單。",
        "SPEC-AVAILABILITY.md §6-1 — 把「不明」畫成停機，妥善率會變成訊號完整度的倒數。"),
       {"ld_lg_down": "停機桶（Down）"}),

    _m("landing.fleet_wait_n", P_LANDING, "Standby machines", "待機台數",
       "Number of machines in the standby family of buckets (standby, process wait, not scheduled).",
       "落在「待機類」桶的台數（待機／流程等待／非排定）",
       "app._ld_state_from_board",
       ("app._ld_state", "board_eqmeeting.build_board"),
       "raw-equipment-axis", "count", (),
       ("ld_q1_wait", "ld_lg_wait"),
       ("SPEC-LANDING.md §11 C-LD2 — 待機類與停機是不同的桶，不得合併。",),
       {"ld_lg_wait": "待機（Standby）", "ld_lg_idle": "該跑沒跑（Should-run idle）"}),

    _m("landing.fleet_nodata_n", P_LANDING, "No-data machines", "無資料台數",
       "Hollow block: the state layer has no data for this machine at this moment.",
       "空心：這一刻狀態層還沒有這台的資料",
       "app._ld_state_from_board",
       ("app._ld_state", "board_eqmeeting.build_board"),
       "raw-equipment-axis", "count", ("C-LD41",),
       ("ld_q1_nodata", "ld_lg_nodata"),
       ("SPEC-LANDING.md §11 C-LD2 — 無資料一律空心線框；不知道≠壞了。",),
       {"ld_lg_nodata": "無資料（No data）＝空心線框"}),

    _m("landing.fleet_nomes_n", P_LANDING, "Not-measurable machines", "量不到台數",
       "Dashed hollow: this machine structurally never has a MES signal (eid not in MES_MEASURABLE_EIDS).",
       "虛線空心：結構上永遠沒有 MES 訊號（eid ∉ MES_MEASURABLE_EIDS）",
       "board_eqmeeting.MES_MEASURABLE_EIDS",
       ("app._ld_state", "board_eqmeeting.build_board"),
       "raw-equipment-axis", "count", ("C-LD41",),
       ("ld_q1_nomes", "ld_lg_nomes", "ld_lg_nomes_tip"),
       ("SPEC-LANDING.md §11 C-LD41 — 量不到自成一桶，永遠不落「待機」／「該跑沒跑」。",
        "SPEC-LANDING.md §12 A6 — 空心不是能力標籤，別拿它當 R/A/M 讀。"),
       {"ld_lg_nomes": "量不到（Not measurable）＝虛線空心",
        "ld_lg_nomes_tip": "結構上永遠沒有 MES 訊號的說明句"}),

    _m("landing.fleet_dormant_n", P_LANDING, "Parked machines", "長停台數",
       "Number of machines or seats sitting in the dorm (long-stop) bucket.",
       "落在長停桶 `dorm` 的台數／座數",
       "board_eqmeeting.build_row",
       # ★ A2 出處補填（SPEC §13 ☐6-1「台數／座數是否分開數」）：**機台與座是兩條 lane**——
       #   機台桶由 `app._ld_state_from_board` 數（該函式明寫 `is_seat` 的列 `continue`＝
       #   C-RW26「座不是方塊」），座級長停另走 `app._ld_seat_dormant` → `ld_subunit_cells`。
       ("board_dormant.dormant_info", "app._ld_state_from_board", "app._ld_seat_dormant"),
       "raw-equipment-axis", "count", ("C-LD48", "C-RW26"),
       ("ld_lg_dorm", "ld_lg_dorm_tip"),
       ("SPEC-DORMANT-CAPACITY.md §2 C-D1 — 長停是登記出來的事實，不是偵測推導出來的。",
        "SPEC-DORMANT-CAPACITY.md §2 C-D3 — 分母因 dormant 縮小時必須看得見，靜默排除＝獎勵不修機器。",
        "SPEC-RUNWINDOW.md §7 C-RW26 — 座列不進任何 Σ；機台桶的台數**不含**座。"),
       {"ld_lg_dorm": "長停（非排定）／Parked (long stop)",
        "ld_lg_dorm_tip": "長停桶的說明句"}),

    _m("landing.q2_output_ph", P_LANDING, "Yesterday output", "昨日產出",
       "Yesterday's Vacuum Pack station output in PH, on the Qlik caliber.",
       "昨日 Vacuum Pack 站產出（PH，Qlik 口徑）",
       "app._ld_period_pick",
       ("board_monthly.last_settled_day", "app._LD_VACUUM_CLS"),
       "qlik-first-fail", "PH", ("C-LD11", "C-LD12"),
       ("ld_q2_out", "ld_q2_out_tip"),
       ("SPEC-LANDING.md §11 C-LD11 — 期別層不得自己聚合，只准讀 board_monthly 已 Σ 好的線級欄位。",
        "SPEC-LANDING.md §11 C-LD12 — 缺材料印理由、不印 0。")),

    _m("landing.q2_oee_pct", P_LANDING, "Month OEE", "本月 OEE",
       "This month's line-level OEE, taken as a field from /monthly; landing never recomputes it.",
       "本月線級 OEE＝`/monthly` 線級 `oee_pct`，只挑欄位不算",
       "app._mr_model", ("app._mr_docs",),
       "rw-planned", "%", ("C-LD11", "C-LD13"),
       ("ld_q2_oee", "ld_q2_oee_tip"),
       ("SPEC-LANDING.md §11 C-LD3 — 一把尺：Q2 只用應生產時段軸，不與 24h 軸並列。",
        "SPEC-LANDING.md §11 C-LD13 — landing 不得冷載期別層（TTL 快取）。"),
       expect="與 /monthly 線級 oee_pct 同值（landing 這一頁永不自己算一次）"),

    _m("landing.q2_rty_pct", P_LANDING, "Month RTY", "本月 RTY",
       "This month's RTY, taken as the line-level rty_pct field from /monthly.",
       "本月 RTY＝`/monthly` 線級 `rty_pct`",
       "app._mr_model", ("app._mr_docs",),
       "qlik-first-fail", "%", ("C-LD11",),
       ("ld_q2_rty", "ld_q2_rty_tip"),
       ("SPEC-LANDING.md §11 C-LD11 — 期別層不得自己聚合。",),
       expect="與 /monthly 線級 rty_pct 同值"),

    _m("landing.q3_loss_total_ph", P_LANDING, "Month total loss", "本月三損失合計",
       "This month's three losses added up in PH, read from line['loss_total_ph'] and rounded.",
       "本月三損失合計（PH）＝`line[\"loss_total_ph\"]`，取整",
       "app._mr_model", ("app._mr_docs",),
       "daily-ledger", "PH", ("C-LD11", "C-LD12"),
       ("ld_q3_head", "ld_q3_tip"),
       ("SPEC-LANDING.md §11 C-LD11 — 只讀線級已 Σ 好的欄位。",
        "SPEC-LANDING.md §11 C-LD12 — 缺材料印理由、不印 0。"),
       expect="三損失合計 ＝ 停機當量 ＋ 良率損失 ＋ 重工（三拆分同頁並列）"),

    _m("landing.q3_down_equiv_ph", P_LANDING, "Downtime equivalent", "停機當量",
       "Downtime equivalent in PH = downtime hours multiplied by the good-unit rate.",
       "停機當量（PH）＝停機時數 × 良品速率",
       "app._mr_model", ("board_monthly.month_rate",),
       "daily-ledger", "PH", ("C-LD12",),
       ("ld_q3_down",),
       ("SPEC-RUNWINDOW.md §7-7 — 當量是註解不是乘數：恆帶 ≈、不做 Σ、不當主數字。",)),

    _m("landing.q3_qlik_fail", P_LANDING, "Yield loss", "良率損失",
       "Yield loss in PH, read from line['qlik_fail'].",
       "良率損失（PH）＝`line[\"qlik_fail\"]`",
       "app._mr_model", ("app._mr_docs",),
       "qlik-first-fail", "PH", ("C-LD11",),
       ("ld_q3_yield",),
       ("SPEC-CAPACITY.md §8-1 — `qlik_fail` 已經是 PH，再乘一次速率就是把顆數當小時。",)),

    _m("landing.q3_rework_n", P_LANDING, "Rework", "重工",
       "Rework in PH, read from line['rework_n'].",
       "重工（PH）＝`line[\"rework_n\"]`",
       "app._mr_model", ("app._mr_docs",),
       "daily-ledger", "PH", ("C-LD11",),
       ("ld_q3_rework",),
       ("SPEC-CAPACITY.md §8-3 — 重工明裁不扣；扣了就是雙計。",)),

    _m("landing.state_freshness", P_LANDING, "State as-of", "狀態時點",
       "State as-of chip = the timestamp of the slowest leg itself, with every leg listed in the tooltip.",
       "狀態時點 chip＝拖最久那條腿自己的時刻（逐腿進 tooltip）",
       "app._ld_state",
       ("app._ld_state", "board_eqmeeting.build_board"),
       "raw-equipment-axis", "h", ("C-LD37",),
       ("ld_st_fresh", "ld_st_tip", "ld_st_tip_oldest"),
       ("SPEC-LANDING.md §11 C-LD4 — 鮮度取 min、必顯 as-of，stale 不藏。",
        "SPEC-LANDING.md §11 C-LD6 — 缺快取不打 MES（顯暖機中）。")),

    _m("landing.seat_state", P_LANDING, "Seat state", "座級狀態",
       "Seat-level visual bucket for seat cells and seat cards; long-stop seats reuse the existing dorm bucket.",
       "座級視覺桶（座格／座卡），長停座用既有 `dorm` 桶",
       "app._ld_sub_view",
       ("app.load_nest_seats", "board_eqmeeting.seat_rows_for_board"),
       "raw-equipment-axis", "count", ("C-LD48", "C-LD51", "C-LD54"),
       ("ld_su_nest", "ld_su_tip_nodata"),
       ("SPEC-RUNWINDOW.md §7-14 — EID 級工單停機不得平均分攤給各座；誠實替代＝交叉揭露。",),
       {"ld_lg_dorm": "長停座沿用既有 dorm 桶，不另發明座專用桶"}),

    # ------------------------------------------------------------ (B) `/utilization`
    _m("util.planned_h", P_UTIL, "Expected run window", "應生產時段",
       "Total expected run window = the sum of |RW(m)| over machines; the one and only source in Tendline Board.",
       "應生產時段總長＝`Σ_m |RW(m)|`（全 Board 唯一一支）",
       "board_eqmeeting.erw_machine_windows",
       ("app.rw_station_payload",),
       "rw-planned", "h", ("C-AV1", "C-RW10"),
       ("u_erw_hdr", "av_rw_head"),
       ("SPEC-RUNWINDOW.md §7-4 — 不得用機台自身訊號擴窗或砍窗（停越久越好看，且沒有測試會紅）。",
        "SPEC-RUNWINDOW.md §7-5 — |RW|=0 一律顯「—」，不得顯 0% 或 100%。",
        "SPEC-RUNWINDOW.md §7-3 — 該跑窗不是「班表」「排班」「schedule」，叫錯名字會被拿去回答計畫類問題。"),
       expect="同一站的 Σ|RW| 在 /utilization、/station、/equipment 三頁必須相等（只有一支）"),

    _m("util.utilization", P_UTIL, "Utilization", "稼動率",
       "Utilization = production time divided by the expected run window.",
       "稼動率＝生產時間 ÷ 應生產時段",
       "board_oee.rw_split",
       ("app.load_scoreboard", "util.planned_h"),
       "rw-planned", "%", ("C-CP8",),
       ("kpi_effi_rw_t", "u_erw_prod_lbl"),
       ("SPEC-AVAILABILITY.md §6-11 — 同一站同一秒有兩把尺是本頁最難發現的病；要分歧先改 `_util_erw_ctx` 本身。",
        "SPEC-LANDING.md §11 C-LD3 — 不與 24h 軸數字並列。"),
       {"u_erw_prod_lbl": "應生產時段內的生產（設備視角 · interval-union · 不去重）",
        "u_erw_unexp_lbl": "該跑而沒跑：既無生產訊號也無工單紀錄",
        "u_rw_off": "未排生產（不進分母）",
        "u_rw_uncov": "日曆未涵蓋"},
       expect="稼動率 ＝ 生產時間 ÷ 應生產時段（分子分母同一把尺）"),

    _m("util.availability", P_UTIL, "Availability", "妥善率",
       "Availability chip = (expected run window minus countable downtime inside RW) divided by that window.",
       "妥善率 chip＝（應生產時段 − 計停機∩RW）÷ 應生產時段",
       "board_oee.rw_avail_down_sec",
       ("board_eqmeeting.wo_segments_scoped", "board_eqmeeting.parent_seat_rollup"),
       "rw-planned", "%", ("C-AV2", "C-AV3", "C-CP6"),
       ("kpi_avail_rw_t", "q_avail_rw", "av_center"),
       ("SPEC-AVAILABILITY.md §6-1 — 把「不明」畫成停機，妥善率會變成訊號完整度的倒數。",
        "SPEC-AVAILABILITY.md §6-3 — 用 `RW_HEALTH_STATES` 算 A，等料／等外包會憑空變「可用」。",
        "SPEC-RUNWINDOW.md §7-15 — 顯示字串不得寫回「可用率」，已正名為「妥善率」。"),
       expect="妥善率 ＝（應生產時段 − 計停機∩RW）÷ 應生產時段；停 4h 與停 20h 的分母恆同"),

    _m("util.down_by_state", P_UTIL, "Downtime by state", "逐態停機",
       "Downtime split by state, cut in priority order so overlapping states are deducted only once.",
       "逐態停機拆分（優先序切分，跨態重疊只扣一次）",
       "board_eqmeeting.erw_state_split",
       ("board_eqmeeting.wo_segments_scoped",),
       "rw-planned", "h", ("C-AV5", "C-AV9"),
       ("av_down_head", "av_down_lbl", "av_legend_hdr"),
       ("SPEC-AVAILABILITY.md §6-2 — 逐態各自加總當圓餅：兩張重疊的工單會讓總和 > 分母。",
        "SPEC-AVAILABILITY.md §6-10 — 本頁的 by_state 不得拿去對 /equipment 的逐態時數（事件視角 vs 時間切分視角）。")),

    _m("util.fleet_util_24h", P_UTIL, "24h fallback utilization", "24h 退路稼動",
       "Fallback axis: utilization on the 24h all-day axis, used only when there is no RW axis and always with a caveat.",
       "退路軸：24h 全日軸稼動（無 RW 軸時；必掛退路警語）",
       "board_oee.station_model",
       ("app.load_scoreboard",),
       "raw-equipment-axis", "%", ("C-AV8", "C-CP11"),
       ("av_fallback_note",),
       ("SPEC-AVAILABILITY.md §6-6 — 不得在退路上印「應生產時段」；那是 24h 軸換個名字裝回來。",
        "SPEC-LANDING.md §12 A2 — 兩把尺並列（24h 軸 vs RW 軸）是反模式。")),

    _m("util.zero_len_unmeasurable", P_UTIL, "Zero-length unmeasurable", "零工期不可測",
       "Zero-length stations: the segment-length method is structurally unmeasurable, so A/U/OEE show a dash and 0 never enters the product.",
       "零工期站：段長法結構性不可測 ⇒ A／U／OEE 顯「—」、0 不進乘積",
       "board_oee.station_model",
       ("app.load_scoreboard_one",),
       "raw-equipment-axis", "ratio", (),
       ("util_unmeasurable_note", "ut_unmeasurable_note", "util_unmeasurable_tip", "ut_unmeasurable_tip"),
       ("SPEC-DAILY-ATTRIB.md §1 T6 — 量不到顯「—」，絕不填 0。",)),

    # -------------------------------------------------------- (C) `/station/{cls}`
    _m("station.rate_good", P_STATION, "Good-unit rate", "良品速率",
       "Good-unit rate in PH per hour: how many good units this station makes each hour; one ruler for the whole station.",
       "良品速率（PH/h）＝這一站每小時做出幾 PH 良品；全站唯一一把尺",
       "board_monthly.month_rate", ("app._mr_docs",),
       "qlik-first-fail", "PH", ("C-CP1", "C-CP2", "C-CP14"),
       ("cp_rate_def", "cp_rate_tip"),
       ("SPEC-CAPACITY.md §8-9 — 站頁的顆數腿必須零 MES，不得在站頁現場讀 MES 算速率。",
        "SPEC-CAPACITY.md §8-4 — tag 有效時不得硬乘站級速率。")),

    _m("station.cap_theory_ph", P_STATION, "Theoretical capacity", "理論產能",
       "Theoretical capacity = expected run window multiplied by the good-unit rate.",
       "理論產能＝應生產時段 × 良品速率",
       "board_oee.capacity_waterfall",
       ("util.planned_h", "station.rate_good"),
       "rw-planned", "PH", ("C-CP3",),
       ("cp_l_theory", "cp_tip_theory"),
       ("SPEC-RUNWINDOW.md §7-7 — 拿該跑窗去乘產能算損失顆數是反模式；當量當註解、不當乘數。",),
       expect="理論產能 ＝ 應生產時段 × 良品速率；五層加起來要回到它（C-CP3 恆等式）"),

    _m("station.cap_down_ph", P_STATION, "Downtime layer", "停機層",
       "Downtime layer = downtime hours multiplied by the rate, rounded before it enters the identity.",
       "停機層＝停機時數 × 速率（取整後入恆等式）",
       "board_oee.capacity_waterfall",
       ("util.down_by_state",),
       "rw-planned", "PH", ("C-CP3", "C-CP6"),
       ("cp_l_down", "cp_tip_down"),
       ("SPEC-CAPACITY.md §8-12 — 瀑布層數不得加到五層以上；細分在 /equipment 展示。",)),

    _m("station.cap_idle_ph", P_STATION, "Idle layer", "該跑沒跑層",
       "Should-have-run layer = (expected run window minus downtime minus production) multiplied by the rate.",
       "該跑沒跑層＝（應生產 − 停機 − 生產）× 速率",
       "board_oee.capacity_waterfall",
       ("util.down_by_state",),
       "rw-planned", "PH", ("C-CP3",),
       ("cp_l_idle", "cp_tip_idle"),
       ("SPEC-CAPACITY.md §8-12 — 不得把等料另立成一層（Chris 要的是四層）。",)),

    _m("station.cap_fail_ph", P_STATION, "Failure layer", "不良層",
       "Failure layer = qlik_fail, which is already in PH and is never multiplied by the rate again.",
       "不良層＝`qlik_fail`（已經是 PH，不再乘速率）",
       "board_oee.capacity_waterfall",
       ("app.load_qlik_grid",),
       "qlik-first-fail", "PH", ("C-CP3",),
       ("cp_l_fail", "cp_tip_fail"),
       ("SPEC-CAPACITY.md §8-1 — 把 `qlik_fail` 乘速率再放進瀑布＝把顆數當小時。",),
       expect="不良層 ＝ qlik_fail 原值（PH），不得再乘速率"),

    _m("station.cap_good_ph", P_STATION, "Good units", "良品",
       "Good-unit headline = the measured qlik_pass, not the residual.",
       "良品主字＝量到的 `qlik_pass`（不是殘差）",
       "board_oee.capacity_waterfall",
       ("app.load_qlik_grid",),
       "qlik-first-fail", "PH", ("C-CP4",),
       ("cp_l_good", "cp_tip_good"),
       ("SPEC-CAPACITY.md §8-2 — 把殘差當良品主字是 2026-09-04 自糾過的病；印實測就必須同時顯式列出重工當量。",),
       expect="良品主字 ＝ 量到的 qlik_pass，且與 /output、/monthly 對得上"),

    _m("station.cap_equiv_ph", P_STATION, "Rework equivalent", "重工當量",
       "Rework equivalent = residual minus good units; drawn as hatching only, never a layer, never multiplied into OEE.",
       "重工當量＝殘差 − 良品；只畫斜紋、永不成為一層、永不乘進 OEE",
       "board_oee.capacity_waterfall",
       ("app.load_qlik_grid",),
       "daily-ledger", "PH", ("C-CP4", "C-CP5"),
       ("cp_rework_in", "cp_tip_rework", "cp_equiv_ledger"),
       ("SPEC-CAPACITY.md §8-3 — 為了讓瀑布加得起來而把重工也扣掉＝雙計，Chris 明裁不扣。",
        "SPEC-CAPACITY.md §8-5 — 不得拿 `mes_yield.fpy_metrics.rework_pct` 當斜紋（與不良層語意重疊）。")),

    _m("station.availability", P_STATION, "Station availability", "站級妥善率",
       "Station-level availability on the RW axis, using the availability state whitelist.",
       "站級妥善率（RW 軸、妥善率白名單）",
       "board_oee.station_model",
       ("app.rw_station_payload", "board_eqmeeting.parent_seat_rollup"),
       "rw-planned", "%", ("C-CP6", "C-CP8"),
       ("kpi_avail_rw_t", "q_avail_rw", "st_q_avail_rw"),
       ("SPEC-CAPACITY.md §8-6 — 口徑統一後仍保留 `q_avail_rw` 的「刻意不同口徑」消歧義句＝假話。",)),

    _m("station.utilization", P_STATION, "Station utilization", "站級稼動率",
       "Station-level utilization on the RW axis.",
       "站級稼動率（RW 軸）",
       "board_oee.station_model",
       ("app.rw_station_payload",),
       "rw-planned", "%", ("C-CP8",),
       ("kpi_effi_rw_t",),
       ("SPEC-CAPACITY.md §8-7 — 不得只改 zh 的 `kpi_avail_t` 值；那顆鍵被 /daily 與 /landing/v1 共用，必須走新鍵。",)),

    _m("station.quality", P_STATION, "Station yield", "站級良率",
       "Station-level yield on the Qlik final-yield caliber; stations outside the 43-station whitelist are honestly left blank.",
       "站級良率（Qlik 口徑 final yield；43 站白名單外誠實留白）",
       "board_oee._pick_quality",
       ("app.load_qlik_grid",),
       "qlik-first-fail", "%", (),
       ("kpi_qual_t", "q_qual"),
       ("SPEC-DAILY-ATTRIB.md §1 T6 — 量不到顯「—」，絕不填 0。",)),

    _m("station.oee", P_STATION, "Station OEE", "站級 OEE",
       "OEE = availability times utilization times yield; rework is never multiplied in.",
       "OEE＝妥善 × 稼動 × 良率（重工不乘進去）",
       "board_oee.station_model",
       ("station.availability", "station.utilization", "station.quality"),
       "rw-planned", "%", ("C-CP5", "C-CP7"),
       ("c_oee", "q_oee_rw", "st_q_oee_rw"),
       ("SPEC-CAPACITY.md §8-3 — 重工不扣、也不進乘積。",
        "SPEC-AVAILABILITY.md §6-11 — 三個因子必須同軸；混軸就是同一秒兩把尺。"),
       expect="OEE ＝ 妥善率 × 稼動率 × 良率；重工當量不進乘積"),

    _m("station.unit_bar", P_STATION, "Machine bar", "機台橫 bar",
       "One horizontal bar per machine on the same axis: the machine-level slice of the station waterfall.",
       "每台機一條同軸橫 bar（＝站瀑布的機台級切片）",
       "board_oee.station_detail",
       ("app.load_yield_station",),
       "raw-machine-level", "PH", ("C-CP13",),
       ("cp_col_bar", "cp_tip_card"),
       ("SPEC-AVAILABILITY.md §6-11 — 機台級切片與站級主圖必須同軸，不得各自一把尺。",)),

    _m("station.seat_rows", P_STATION, "Seat rows", "逐座列",
       "Seat rows are roster-driven: every seat on the roster becomes a row, and missing facts print a dash plus the reason.",
       "逐座列由名冊驅動：名冊上有的座一定成列，缺事實印「—」＋原因",
       "board_capacity.NEST_EAV_UNITS",
       ("app.load_nest_seats",),
       "raw-equipment-axis", "count", ("C-CP16",),
       ("cp_dorm_seats", "eq_seat_row_nest"),
       ("SPEC-RUNWINDOW.md §7-14 — EID 級工單停機不得平均分攤給各座（三個座各背 1/3 ＝三個數字全錯）。",
        "SPEC-RUNWINDOW.md §7-8 — 不適用就是「—」，不得拿鄰綑／同區／全廠的窗代打。")),

    _m("station.tag_filter", P_STATION, "Tag filter threshold", "標籤篩選門檻",
       "Threshold rule under ?tag= filtering: the full five-layer PH is kept when excluded machines hold at most 5% of the station's producing machine-hours.",
       "`?tag=` 能力篩選下的門檻規則（被濾掉機台佔本站生產機時 ≤ 5% ⇒ 完整五層 PH）",
       "board_tagui.tag_filter",
       # ★ A2 出處補填（SPEC §13 ☐6-2「門檻常數與『生產機時』的確切分母」）：
       #   門檻＝`board_tagui.CP_TAG_PH_MAX_EXCL_DEFAULT`（`0.05`，env `BOARD_CP_TAG_PH_MAX_EXCL`
       #   可覆寫）；判定＝`board_tagui.tag_ph_mode(producing_full_sec, producing_kept_sec)`，
       #   **分母＝全站生產機時（秒）、不是台數**（該函式 docstring 逐字）。
       ("board_tags.parse_tag_param", "board_tags.filter_eids", "board_tags.PRODUCT_TAGS",
        "board_tagui.tag_ph_mode", "board_tagui.CP_TAG_PH_MAX_EXCL_DEFAULT"),
       "rw-planned", "ratio", ("C-CP10", "C-CP15", "C-AV12"),
       ("cp_tag_ph_full", "cp_tag_ph_na", "cp_rate_tile_tag"),
       ("SPEC-CAPACITY.md §8-4 — tag 有效時硬乘站級速率＝「按產品切」換了個入口。",
        "SPEC-AVAILABILITY.md §6-4 — 重算 tag 時餵全量 `mwin` 給趨勢，濾掉的機台會偷偷回到分母。",
        "SPEC-CAPACITY.md §3.7 — share 的分母是**生產機時**不是台數；用台數會讓整窗閒著的機把卡踢退場。")),

    # ------------------------------------------------------------- (D) `/equipment`
    _m("equip.stop_h", P_EQUIP, "Machine downtime hours", "逐機停機時數",
       "Per-machine downtime hours in this window = the interval-union of work-order segments intersected with the run window.",
       "逐機本窗停機時數＝工單段 ∩ 應生產時段的 interval-union",
       "board_eqmeeting.build_row",
       ("board_eqmeeting.wo_segments_scoped",),
       "rw-planned", "h", ("C-AV2",),
       ("eq_col_stop_tip", "eq_wo_downtime"),
       ("SPEC-RUNWINDOW.md §7-10 — 分子取全段、分母取該跑窗會算出 >100% 的停機占比。",)),

    _m("equip.avail_pct", P_EQUIP, "Machine availability", "逐機妥善率",
       "Per-machine availability = (expected run window minus countable downtime) divided by the expected run window.",
       "逐機妥善率＝（應生產 − 計停機）÷ 應生產",
       "board_eqmeeting.build_row",
       ("board_eqmeeting.erw_machine_windows",),
       "rw-planned", "%", ("C-AV1", "C-PC11"),
       ("eq_col_avail",),
       ("SPEC-RUNWINDOW.md §7-4 — 不得用機台自身訊號擴窗或砍窗（C-PC11：停 4h 與停 20h 分母恆同）。",
        "SPEC-RUNWINDOW.md §7-5 — |RW|=0 一律「—」，週末不得顯 0% 或 100%。")),

    _m("equip.avail_delta_pp", P_EQUIP, "Availability delta", "妥善率變化",
       "Availability difference against the previous window in percentage points; suppressed in the one-click close-out case.",
       "妥善率與上一窗的差（百分點）；一鍵結單情境下抑制 Δ",
       "board_eqmeeting.build_row",
       # ★ A2 出處補填（SPEC §13 ☐6-3「Δ 抑制沒有契約卡」）：Δ 本體＝`erw_row` 的 `delta`
       #   （最新一個**完整生產日**的妥善率 − 整窗妥善率）；`build_row` 只在**長停登記**或
       #   **非名冊**時把它設成 None。契約卡＝C-RW24／C-CF8（Δ 的分母永不按座、座列 Δ 恆「—」）。
       ("board_eqmeeting.erw_machine_windows", "board_eqmeeting.erw_row"),
       "rw-planned", "%", ("C-RW24", "C-CF8"),
       ("eq_delta_basis", "eq_delta_basis_tip"),
       ("SPEC-RUNWINDOW.md §7-1 — 不得並列兩種真相（同屏放兩種軸的 A%）。",
        "SPEC-RUNWINDOW.md §10.8 ④ — Δ 的兩端是不同尺度（最新完整生產日**單日** vs **整窗**），"
        "基準必須恆顯，否則會被讀成「昨天 vs 前天」。",
        "GUIDE-DOWNTIME-RESIDUAL.md §3 — ⚠ 公式句裡「一鍵結單情境下抑制 Δ」講的是 **`/maint` 的 "
        "MTTR Δ**（`board_maint.wo_lifecycle` 的 `oneclick` 旗）；`/equipment` 這一格的 Δ 只在"
        "**長停登記／非名冊**時「—」，與一鍵結單無關（見 REVIEW_NOTES 10）。")),

    _m("equip.now_bucket", P_EQUIP, "Now state bucket", "Now 狀態桶",
       "Now column state bucket, synthesised from multiple evidence sources; silence is not a fault.",
       "Now 欄狀態桶（多源證據合成；沉默≠故障）",
       "board_eqmeeting.build_row",
       ("app.load_updown",),
       "raw-equipment-axis", "count", ("C-RW27",),
       ("eq_col_now", "eq_now_producing", "eq_now_idle", "eq_now_down", "eq_now_nosig"),
       ("SPEC-LANDING.md §11 C-LD2 — 不知道≠壞了；沉默不得升格成故障。",),
       {"eq_now_producing": "UP · 生產中", "eq_now_idle": "UP · 閒置",
        "eq_now_down": "DOWN · 有工單證據", "eq_now_nosig": "無訊號（不是故障）",
        "eq_now_dormant": "非排定（長停登記）"}),

    _m("equip.mes_last_h", P_EQUIP, "Last production", "最後生產",
       "Last production = hours since the most recent MES production signal.",
       "「最後生產」＝距最近一段 MES 生產訊號的小時數",
       "board_eqmeeting.build_row",
       ("app.load_updown",),
       "raw-equipment-axis", "h", ("C-RW27",),
       ("eq_mes_asof", "eq_mes_asof_tip"),
       ("SPEC-DORMANT-CAPACITY.md §2 C-D1 — 偵測永不裁決；`MAX(stpStartTime)` 不是停產日。",)),

    _m("equip.dormant", P_EQUIP, "Long-stop registration", "長停登記",
       "Long-stop registration (A11): registration is a decision, not a detection, and MAX(stpStartTime) is not a stop date.",
       "長停登記（A11）：登記是決策、不是偵測——`MAX(stpStartTime)` 不是停產日",
       "board_dormant.dormant_info",
       # ★ A2 出處補填（SPEC §13 ☐6-4「A11 拉取是散文、沒有 SPEC 節號」）：拉取端是**本 repo 的
       #   具名函式**＝`board_data.get_dormant_events`（內部 `_a11_fetch_all` →
       #   `board_w8_transport.fetch_schedule_state_events`，整批失敗即退回過渡橋常數
       #   `board_dormant.DORMANT_BRIDGE`）；節號＝`SPEC-DORMANT-CAPACITY.md` §6／§6.1。
       # ★ 2026-10-02（R41 §12.1-7 MCP 缺口補洞）：加 `board_dormant.bridge_rows`＝橋常數的**唯一讀口**
       #   （WP-4b R-C3；合成進程恆空）——單機工具的退路投影經它讀橋，不直接讀 `DORMANT_BRIDGE`。
       ("board_data.get_dormant_events", "board_w8_transport.fetch_schedule_state_events",
        "board_dormant.DORMANT_BRIDGE", "board_dormant.bridge_rows"),
       "raw-equipment-axis", "count", ("C-D1", "C-D2", "C-D3"),
       ("eq_now_dormant", "eq_now_dormant_tip", "eq_strip_dormant"),
       ("SPEC-DORMANT-CAPACITY.md §2 C-D1 — 狀態是登記的事實，不是推導。",
        "SPEC-DORMANT-CAPACITY.md §2 C-D2 — MES `astStatus` 只當哨兵佐證、永不當源。",
        "SPEC-DORMANT-CAPACITY.md §2 C-D3 — 排除必須看得見；靜默排除＝獎勵不修機器。",
        "SPEC-DORMANT-CAPACITY.md §6.1 — A11 整批降級（部分換源＝同頁混兩份真相）；"
        "`events=None` ⇒ 呼叫端一律走過渡橋常數。",
        # ★ 2026-10-02：助理單機回應的 since／原因怎麼讀（`dormant_info` 的語意，照實寫給 explain_metric）
        "SPEC-DORMANT-CAPACITY.md §12.1-7 — 「自 X 日」＝**本段**長停的起點（往回併連續的長停登記；"
        "復機後再長停＝新的一段），原因＝最後一筆登記的自由文字、逐字引用不分類；"
        "單機工具（machine_lookup／equipment_downtime／updown_now）的 `dormant` 欄＝頁面那一列，"
        "`dormant_registry` 說登記是誰說的（`unavailable`＝讀不到登記，**不是**在役）。"
        "長停機的妥善率／稼動刻意不算（印「—」），不是缺資料。",
        # ★ 2026-10-02 助理工具第二批 ①：座級長停（某一座被登記不排生產）
        "SPEC-DORMANT-CAPACITY.md §12.1-7（座級，2026-10-02）— 座級長停在工具的 `dormant_seats`"
        "（updown_now 另有 `n_dormant_seats`）、**不算進** `n_dormant`（那是台數）；"
        "`dormant` 空但 `dormant_seats` 不空＝**有**長停登記（在座級），不得答「沒有長停」。"
        "座列原因欄空白＝登記沒填原因 ⇒ `reason` null，照實說「原因未填」、不編。")),

    _m("equip.seat_scope", P_EQUIP, "Seat scope union", "座集合聯集",
       "Seat set union: seat ids inside scope that belong to this parent machine also count as named seats.",
       "座集合聯集：`scope` 裡屬於本母機的座 id 也算指名座",
       "board_capacity.event_seat_units",
       ("board_w8.effective_seat_units", "board_w8.effective_sub_units"),
       "raw-equipment-axis", "count", ("C-RW28",),
       ("eq_seat_whole", "eq_seat_suffix", "eq_seat_wo"),
       ("SPEC-RUNWINDOW.md §7-14 — 不得把 EID 級工單停機平均分攤給各 nest。",)),

    _m("equip.wo_mes_conflict", P_EQUIP, "Work-order MES conflict", "工單 MES 衝突",
       "Cross-disclosure (c): the work order says stopped while MES says running; disclose only, never change the number.",
       "(c) 交叉揭露：工單說停、MES 說在跑（只揭露、不改數字）",
       "board_eqmeeting.build_row",
       ("board_eqmeeting.wo_segments", "app.load_updown"),
       "raw-equipment-axis", "count", (),
       ("eq_dx_conflict", "eq_coll_mesprod"),
       ("SPEC-RUNWINDOW.md §7-14 — 誠實替代＝交叉揭露，不是把數字改成看起來合理的樣子。",
        "SPEC-RUNWINDOW.md §7-1 — 只揭露、不改任何數字；不得把矛盾折成第三種真相。")),

    # ★ 2026-09-23 `#169`（Chris 親測：問「≈85PH 的正式算法在哪一頁」，助理只找到 Yield 的 PH
    #   良率、答「工具沒有回傳公式」）＝`/equipment` 每格／停機欄的 `≈NNPH` **從來沒登記**。
    #   ⚠ 同一個公式出現在兩個粒度：逐日格（`erw.days[ds].equiv_ph`）與停機欄的整窗值
    #     （`row.equiv_ph`）——兩者都是**各自的停機小時 × 同一速率**獨立算，**整窗值不是逐日格
    #     的和**（C-RW13 禁 Σ；四捨五入與 <1PH 門檻也逐格各自套）⇒ 一筆涵蓋、不另登列尾。
    #     頁上**沒有**區塊／整頁合計當量（SPEC-RUNWINDOW §4.1「不做區塊／整頁 Σ」）⇒ 無可登。
    #   ⚠ 不是 `landing.q3_down_equiv_ph`（月帳線級）、也不是 `station.cap_equiv_ph`（重工當量）。
    _m("equip.cell_equiv_ph", P_EQUIP, "Cell downtime equivalent (≈PH, output equivalent)",
       "格內停機當量（≈PH、產出當量）",
       "≈PH = in-window downtime × rate; rate = median of last 6 qualified production days; seat-hour ruler falls back to machine-hour; no rate means hours only.",
       "停機當量 ≈PH＝窗內停機時數 × 速率；速率＝該站近 6 個合格生產日（producing_h ≥ 2h）速率中位數；座‧小時尺法定人數不足時整站退回機台小時舊尺；兩腿都給不出站速率 ⇒ 該格只有小時、無 ≈PH。",
       "board_eqmeeting.build_row",
       ("app._eqm_rates_by_eid", "app._eqm_pick_rates", "app._eqm_roster_cls_candidates",
        "board_eqmeeting.seat_factors_for", "board_eqmeeting.inherit_child_rates",
        "board_eqmeeting.erw_row"),
       "rw-planned", "PH", ("C-RW13", "C-RW24", "C-RW25", "C-RW26"),
       ("eq_equiv_tip", "eq_rate_seat_tip", "eq_rate_mh_tip", "eq_seat_row_equiv_tip"),
       ("SPEC-RUNWINDOW.md §2 C-RW13 — ≈ 恆帶＝上限估算（未計同站他機補位；真損失以綑級實際 vs 目標"
        "缺口為準）；只逐格顯示、**永不跨格／跨列／區塊 Σ**。停機欄的整窗值是整窗停機 × 同一速率"
        "獨立算，**不是**逐日格的和。",
        "SPEC-RUNWINDOW.md §4.1-c — 速率＝該站近 6 個合格生產日（producing_h ≥ 2h）速率中位數；"
        "分母是**實際生產**機台小時、不是該跑窗；7 天內無合格日 ⇒ 只印小時、不印 ≈PH（不猜）。",
        "SPEC-RUNWINDOW.md §6.3-b C-RW25 — 尺別：座‧小時尺（每座速率 × 安裝座數；Gen2 槽位算一台 "
        "Gen1）法定人數不足時整站退回機台小時舊尺（tooltip 註「舊尺」、混座站為下限）；同站同板只一種尺。",
        "SPEC-RUNWINDOW.md §4.1-d（ROADMAP #145）— MES 90 天對映找不到時退回名冊／730 天普查站別"
        "（live-only、只補缺不覆蓋、多候選不猜）；兩腿都給不出站速率 ⇒ 該格只有小時、無 ≈PH。",
        "SPEC-RUNWINDOW.md §6.3-b C-RW24／§6.3-c C-RW26 — 座係數只乘在當量上（A%／小時永不按座）；"
        "座列的 ≈PH 是母機列的**子集**、不得相加。長停／非名冊／框 inert ⇒ 恆無當量。",
        "鮮度＝`/equipment` 板快取（`app.EQM_TTL`，預設 600s）；速率樣本逐日滾動 ⇒ 同一台今天與"
        "昨天的當量不同屬正常。⚠ 別與 `landing.q3_down_equiv_ph`（月帳線級）或 "
        "`station.cap_equiv_ph`（重工當量）混為一談。"),
       expect=("點該格下鑽：窗內停機小時 × 本站速率（座尺＝每座速率 × 座數）× 座係數，"
               "四捨五入後 ＝ 格內 ≈NNPH；<1PH 或無速率 ⇒ 不顯")),

    # ----------------------------------------------------------------- (E) `/daily`
    _m("daily.gap_wo_h", P_DAILY, "Work-order gap", "工單缺口",
       "Yesterday's gap in the work-order bucket = countable downtime work-order segments intersected with the should-run window.",
       "昨日缺口歸因「工單」桶＝可計停機工單段 ∩ 該跑窗",
       "board_attrib.BUCKETS",
       ("board_attrib.COUNTABLE_WO_STATES",),
       "daily-ledger", "h", (), ("at_b_wo", "at_overlap"),
       ("SPEC-DAILY-ATTRIB.md §1 T1 — 恆等式的工單項＝`wo_union_h`；S12 分態時數只做顯示，絕不進恆等式。",
        "SPEC-DAILY-ATTRIB.md §1 T2 — 不雙計：同一段機台時間只入一桶，優先序固定。"),
       expect="T1 恆等式：每站 fleet_n × 24h ＝ 生產 ＋ 工單 ＋ 全廠停線 ＋ 斷料 ＋ 下游堵 ＋ 未解釋（≤1e-6h）"),

    _m("daily.gap_starved_h", P_DAILY, "Starved gap", "缺料缺口",
       "Starved bucket: nothing is coming in from upstream.",
       "「缺料」桶：上游沒東西進來",
       "board_attrib.BUCKETS",
       ("mes_wip",),
       "daily-ledger", "h", (), ("at_b_starved", "at_stale"),
       ("SPEC-DAILY-ATTRIB.md §9 — off-shift 閒置判成斷料（C10 活動鄰近 gate）是反模式。",
        "SPEC-DAILY-ATTRIB.md §9 — `cover` 是 None 時當 0 會誤判斷料。"),
       status="draft"),

    _m("daily.gap_blocked_h", P_DAILY, "Blocked gap", "阻塞缺口",
       "Blocked bucket: the downstream is jammed.",
       "「阻塞」桶：下游塞住",
       "board_attrib.BUCKETS",
       ("board_attrib.downstream_of",),
       "daily-ledger", "h", (), ("at_b_blocked", "at_cross"),
       ("SPEC-DAILY-ATTRIB.md §9 — `cover` 是 None 時當 ∞ 會誤判下游堵。",),
       status="draft"),

    _m("daily.gap_unexplained_h", P_DAILY, "Unexplained gap", "未歸因缺口",
       "Unexplained bucket = the residual; the residual must never be promoted automatically into manpower or scheduling.",
       "「未歸因」桶＝殘差；殘差不得自動升格成人力／排班",
       "board_attrib.BUCKETS",
       ("board_attrib.downstream_of",),
       "daily-ledger", "h", (), ("at_b_unexplained", "at_gate"),
       ("SPEC-DAILY-ATTRIB.md §1 T3 — 殘差誠實：缺證據≠人力；升格條件見 §7。",
        "SPEC-DAILY-ATTRIB.md §9 — 殘差自動標「人力不足」是反模式。")),

    _m("daily.pareto_30d", P_DAILY, "30-day Pareto", "30 天 Pareto",
       "Attribution Pareto over the last 30 days (PARETO_DAYS defaults to 30).",
       "近 30 天歸因 Pareto（`PARETO_DAYS` 預設 30）",
       "board_attrib.PARETO_DAYS",
       ("board_data.read_ledger_docs",),
       "daily-ledger", "h", (), ("at_pareto_cover",),
       ("SPEC-DAILY-ATTRIB.md §9 — 不得為 30d Pareto 每次重算 30 天（用日帳落地）。",
        "board_data.read_ledger_docs docstring — 餵 `board_attrib.pareto30` 必須帶 "
        "`v1_view=True`，否則 v2 檔會被當壞檔、時間榜靜默丟光所有新日子。")),

    _m("daily.good_rate", P_DAILY, "Daily good rate", "日級良品速率",
       "Daily good-unit rate = qlik_pass divided by good machine-hours; below MIN_PROD_MH it returns None.",
       "日級良品速率＝`qlik_pass ÷ 良品機時`（低於 `MIN_PROD_MH` ⇒ None）",
       "board_unit.good_rate",
       ("app.load_qlik_grid",),
       "qlik-first-fail", "PH", (), ("at_est",),
       ("SPEC-DAILY-ATTRIB.md §9 — 站級 rate × 機台小時＝把 est 放大 fleet_n 倍（C5 的病）。",)),

    _m("daily.ranked_losses", P_DAILY, "Top losses", "最大損失排名",
       "Yesterday's top N losses, used for the diagnosis sentence.",
       "昨日最大損失前 N 名（診斷句用）",
       "board_unit.ranked_losses",
       ("board_unit.build_board",),
       "daily-ledger", "PH", (), (),
       ("SPEC-DAILY-ATTRIB.md §1 T4 — 帳本權威單位是小時；est 顆數是溝通用估算、顯示恆帶 ≈。",)),

    _m("daily.families", P_DAILY, "Family activity", "family 活動",
       "Product family activity counts per station and per machine; this is the activity layer, not the capability tag.",
       "逐站逐機的產品 family 活動計數（活動層、不是能力標籤）",
       "mes_family.run",
       ("mes_family.ALL_SQL",),
       "raw-equipment-axis", "count", ("C-PT3",), (),
       ("SPEC-PRODUCT-TAGS.md — 活動層計數不是能力標籤；標籤全由 MES 的 730 天窗門檻決定。",)),

    _m("daily.unmeasurable_reason", P_DAILY, "Unmeasurable reason", "不可測原因",
       "Why that day was unmeasurable (zero_len, nosig, no_fleet, no_window, src_err, not_in_kun).",
       "那一天為什麼量不到（`zero_len`／`nosig`／`no_fleet`／`no_window`／`src_err`／`not_in_kun`）",
       "board_attrib.NOTE_I18N_KEY",
       ("board_data.read_ledger_doc",),
       "daily-ledger", "count", (),
       ("at_note_zero_len", "at_note_nosig", "at_note_no_fleet",
        "at_note_no_window", "at_note_src_err", "at_note_not_in_kun"),
       ("SPEC-DAILY-ATTRIB.md §1 T6 — 量不到顯「—」＋一句說明，絕不填 0。",
        "board_attrib.py:89-91 — `src_err`（我沒查到）與 `no_fleet`（那站沒機台）"
        "渲染成同一句＝把「我沒查到」說成「那站沒開線」。")),

    _m("daily.ledger_schema_ver", P_DAILY, "Ledger schema version", "日帳 schema 版",
       "Daily ledger schema version, which separates the v4 and v5 calibers; read the data, not the version number.",
       "日帳 schema 版（口徑換代靠它分 v4／v5，看資料不看版號）",
       "board_attrib.LEDGER_SCHEMA_VER",
       ("board_data.read_ledger_doc",),
       "daily-ledger", "count", ("C-CP9", "C-MR8"), ("at_caliber",),
       ("SPEC-DAILY-ATTRIB.md §1 T5 — 帳本口徑軸＝設備軸／raw；口徑說明只在 footer 出現一次。",
        "board_data.read_ledger_doc docstring — schema 不符回 None，**絕不猜**；"
        "口徑換代看資料、不看版號。")),
    # ----------------------------------------------------------------- (F) `/downtime`
    # ★ 2026-09-23 #167 P4（Chris：「助理也要有能力回答 /downtime 頁的內容」）。八筆全由
    #   `board_downtime.downtime_model` 一支供（頁面與 MCP 工具 `downtime_monthly` 同一支讀端）；
    #   事實表＝`_wo_ledger`（每日 08:40 排程落帳）。caliber＝`wo-ledger`（兩把尺逐月標在 `axis`）。
    _m("downtime.area_month_h", P_DOWNTIME, "Area-month downtime (bar segment)", "區月停機（柱段）",
       "Σ over the area's roster machines and the month's production days of the machine's work-order downtime, overlapping orders counted once.",
       "該區名冊內機台、該月各生產日的機台工單停機加總（同機重疊工單只算一次）",
       "board_downtime.downtime_model",
       ("_wo_ledger.compute_day_rows", "board_eqmeeting.downtime_by_eid_scoped",
        "board_eqmeeting.erw_row"),
       "wo-ledger", "h", ("C-DT1", "C-DT2", "C-DT5", "C-DT13", "C-DT14"),
       ("dt_axis_24h", "dt_axis_rw", "dt_seg_tip", "dt_sep_note"),
       ("SPEC-DOWNTIME.md §6.1 C-DT1 — 月尺整月 × 全三區：該月任一天不是應生產時段剪裁 ⇒ 整月用 24h 軸；"
        "**兩把尺之間不可比、不可相減或算趨勢**（頁上有虛線分隔）。",
        "SPEC-DOWNTIME.md §6.4 C-DT5 — 柱高＝機台 union；不是工單時數加總。",
        "SPEC-DOWNTIME.md §6.6-a C-DT14 — eMaint 時代 PM 單（無狀態紀錄）整張排除；名冊外／Zone D／目錄外不進柱。"),
       expect="表格孿生該月該區那一格 ＝ 柱段 tooltip 的 h"),

    _m("downtime.total_h", P_DOWNTIME, "Total downtime (KPI)", "總停機（KPI）",
       "Sum of the selected months' bars (three areas, roster machines, per-machine union).",
       "選取月份三區柱高加總（名冊內機台、機台 union）",
       "board_downtime.downtime_model", ("downtime.area_month_h",),
       "wo-ledger", "h", ("C-DT1", "C-DT5"), ("dt_kpi_total", "dt_kpi_total_tip", "dt_mixed"),
       ("SPEC-DOWNTIME.md §7.4 — 選取同時含 24h 月與應生產時段月時，總數是兩把尺的加總（頁上 dt_mixed 一句）；"
        "要比較請只選同一把尺的月份。",)),

    _m("downtime.avg_per_machine_h", P_DOWNTIME, "Average downtime per machine", "每台平均停機",
       "Total downtime ÷ number of Zone A+B+C machines on the current cluster roster.",
       "總停機 ÷ Zone A＋B＋C 在綑名冊內的台數（當下名冊）",
       "board_downtime.downtime_model", ("downtime.total_h", "board_downtime.roster_eids"),
       "wo-ledger", "h", ("C-DT9",), ("dt_kpi_avg", "dt_kpi_avg_tip"),
       ("SPEC-DOWNTIME.md §14 ⑯ — 分母是名冊內三區台數，**不是**有停機的台數（後者會讓壞的台少的月平均反而變高）。",
        "SPEC-DOWNTIME.md §6.3 ⑱ — 名冊沒有歷史版本：過去月份也用當下名冊。")),

    _m("downtime.n_wo", P_DOWNTIME, "Work orders with downtime", "有停機的工單數",
       "Distinct work orders with downtime hours > 0 in the selected months (roster machines, three areas).",
       "選取月份內停機時數 > 0 的相異工單數（名冊內三區）",
       "board_downtime.downtime_model", ("_wo_ledger.compute_day_rows",),
       "wo-ledger", "count", ("C-DT14",), ("dt_kpi_wo", "dt_kpi_wo_tip"),
       ("SPEC-DOWNTIME.md §6.6-a — 被排除的 eMaint PM 單、取消／作廢單不計。",)),

    _m("downtime.n_eids", P_DOWNTIME, "Machines with downtime", "停機台數",
       "Distinct roster machines of the three areas with downtime hours > 0 in the selected months.",
       "選取月份內停機時數 > 0 的名冊內三區相異機台數",
       "board_downtime.downtime_model", ("_wo_ledger.compute_day_rows",),
       "wo-ledger", "count", ("C-DT13",), ("dt_kpi_eids", "dt_kpi_eids_tip"),
       ("SPEC-DOWNTIME.md §6.3 C-DT13 — 名冊外／Zone D／目錄外機台不計。",)),

    _m("downtime.top5_share", P_DOWNTIME, "Top-5 share", "前五佔比",
       "Σ of each area-month's top-5 work-order hours ÷ total downtime.",
       "各區各月前五張工單時數加總 ÷ 總停機",
       "board_downtime.downtime_model", ("downtime.wo_h", "downtime.total_h"),
       "wo-ledger", "%", ("C-DT5",), ("dt_kpi_top5", "dt_kpi_top5_tip"),
       ("SPEC-DOWNTIME.md §6.4 — 單張工單時數把同機重疊時段各自計入 ⇒ 佔比可能偏高、甚至超過 100%。",)),

    _m("downtime.delta_h", P_DOWNTIME, "Monthly change", "月均變化",
       "Last selected month's total − average of the other selected months (h and %); shown only when all selected months share one ruler.",
       "最末選取月總停機 − 其餘選取月平均（h 與 %）；選取月份同一把尺才顯示",
       "board_downtime.downtime_model", ("downtime.total_h",),
       "wo-ledger", "h", ("C-DT1",), ("dt_kpi_delta", "dt_kpi_delta_tip", "dt_delta_cross"),
       ("SPEC-DOWNTIME.md §14 ③ — 只選一個月不顯；跨尺（24h 月＋應生產時段月）不顯、印「跨尺不比」。",
        "SPEC-DOWNTIME.md §7.4 — Δ 不上色：只給符號與數，不做好壞判讀。")),

    _m("downtime.wo_h", P_DOWNTIME, "Work-order downtime (top-5 row)", "單張工單停機（前五列）",
       "Σ over the month's production days of the work order's downtime hours on its machine (roster, area); share = ÷ the area-month bar.",
       "該單在該月各生產日的停機時數加總（名冊內、該區）；佔比＝÷ 該區該月柱高",
       "board_downtime.downtime_model", ("_wo_ledger.compute_day_rows", "board_eqmeeting.wo_segments_scoped"),
       "wo-ledger", "h", ("C-DT5", "C-DT6", "C-DT7", "C-DT12"),
       ("dt_col_hpct", "dt_col_stage", "dt_flag_migrated", "dt_flag_long", "dt_overlap_note"),
       ("SPEC-DOWNTIME.md §6.4 — 同機重疊時段在各單分別計入、在柱高只計一次。",
        "SPEC-DOWNTIME.md §6.5 C-DT6 — 長掛單（活躍 > 90 天）預設納入並標旗，可 chip 排除。",
        "SPEC-DOWNTIME.md §6.6 C-DT7 — 遷移單＝開單→結單整段估算（旗只揭露、不改數）；修／等料／等外包拆分對遷移單不存在。",
        "SPEC-DOWNTIME.md §10 C-DT12 — 只歸因機台、不歸因人。")),
    # ----------------------------------------------------------------- (G) `/monthly`
    # ★ 2026-10-02 助理工具第二批 ③（QG-7 ②「能解釋」）：MCP `yield_summary.monthly_loss` 讀的是
    #   `/monthly` 頁面同一支（`app._mr_model`＝`board_monthly.month_model`；已凍月讀凍結快照）。
    #   登記的是**站列**那一格（頁面損失分頁的排序貨幣）；線級三筆已由 landing Q3 登記、不重登。
    _m("monthly.station_loss_total_ph", P_MONTHLY, "Station three-loss total (month)",
       "站別三損失合計（月）",
       "Per station, one calendar month: downtime equivalent + yield loss (first-FAIL PH) + rework PH; null when the station has no month rate.",
       "每站、一個日曆月：停機當量＋良率損失（first-FAIL PH）＋重工 PH；該站無月尺 ⇒ 合計 null",
       "board_monthly.month_model",
       ("app._mr_model", "app._mr_docs", "board_monthly.month_rate"),
       "daily-ledger", "PH", ("C-MR2", "C-MR3"),
       ("mr_head", "mr_col_equiv", "mr_col_yield", "mr_col_rework"),
       ("SPEC-MONTHLY.md C-MR3 — 無月尺站（合格生產日 < 5）算不出停機當量 ⇒ 合計 null、絕不 0；"
        "排在有合計的站之後、以停機小時排序（月模型的 sort_key＝損失序；頁面站表本身是 Qlik 流序、"
        "每列印三個拆分，不印合計）。",
        "SPEC-MONTHLY.md C-MR2 — 只有損失可跨站相加；良率不是（線級只有 RTY）。",
        "Chris 2026-09-02 裁 — 「損失」頭數字＝三損失合計並附拆分；使用者指名某一種時才只答那一種。",
        "停機當量＝停機小時 × 該站當月良品速率中位數 × 座數係數；未計同站他機補位 ⇒ 上限估算。",
        "月＝生產月（生產日 08:00–08:00）；當月＝部分月（partial）、不是完整月可比的數。"),
       expect="＝ `/monthly` 損失分頁該站那一列三格（當量＋良率損失＋重工）相加（同一份月模型、同一個取整）"),
)

ALL_METRICS: dict[str, Metric] = {m.id: m for m in _METRICS}


# =============================================================================
# 2a. `TIPS`（A4，`ROADMAP.md` #138；`SPEC-HERMES-TRUST.md` §12.4）—— 頁面 tooltip／圖例文案的**唯一出處**
# =============================================================================
#: 五頁（`/`、`/utilization`、`/station/{cls}`、`/equipment`、`/daily`）上「解釋一個數字」的 tooltip、
#:   圖例與註記文字。**文字住這裡、不住 `app.py` 的 I18N 字典**——頁面（模板與取值的碼）讀的仍是原本
#:   那個 i18n 鍵，只是該鍵的值在 import 期由 `merge_tips()` 從本表灌進它的家族字典。
#:   ⇒ 改本表一個字，頁面（v1／v2、en／zh）跟著變；兩處不可能漂開。
#: 規則（違反即作廢；`--selftest` 第 10 條釘）：
#:   ① 一個 i18n 鍵只住一處：登記到本表就**必須**從原家族字典字面量刪掉（否則兩份會漂）；
#:   ② `family`＝它被灌進的家族字典名（窄字典渲染的輪也找得到鍵：`_equipment_body.html` 在冒煙裡
#:      吃的是 `_EQ_I18N[lang]`）；`metric`＝解釋它的登記表指標 id（該指標的 `i18n_keys` 必須含此鍵）；
#:   ③ **共用鍵走新鍵**（`SPEC-CAPACITY.md` §7 反模式 7）：同一個鍵若另有別頁（或別的模板）在讀，
#:      本頁改用本表的**新鍵**，舊鍵與舊頁一個字不動——否則改本表會把沒換軸的頁也改掉；
#:   ④ 兩語都要有，且 `{placeholder}` 集合 en／zh 相同；zh 不得出現「顆」（數量單位 PH）。
#: ⚠ 本表只放**文字**。公式與口徑仍住 `Metric.formula`；二者互不抄（C-HT2）。
@dataclass(frozen=True)
class Tip:
    family: str        # 灌進哪個家族字典（`I18N`／`_EQ_I18N`／`_AV_I18N`／`_LD_I18N`／`_MR_I18N`／`I18N_ATTRIB`）
    page: str          # 五頁之一（`P_*` 常數）
    metric: str        # 解釋它的指標 id
    en: str
    zh: str


TIP_FAMILIES = ("I18N", "_EQ_I18N", "_AV_I18N", "_LD_I18N", "_MR_I18N", "I18N_ATTRIB")

TIPS: dict[str, Tip] = {
    # ---- TIPS-ROWS-BEGIN ----
    # ---- landing `/`（A4 第 1 頁；全是 ld_* 專屬鍵，同鍵搬家） ----
    "ld_lg_run": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_producing_n",
        "Producing",
        "生產"),
    "ld_lg_down": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_down_n",
        "Down",
        "停機"),
    "ld_lg_wait": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_wait_n",
        "Standby",
        "待機"),
    "ld_lg_nodata": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_nodata_n",
        "No data",
        "無資料"),
    "ld_lg_nomes": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_nomes_n",
        "Not measurable",
        "量不到"),
    "ld_lg_nomes_tip": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_nomes_n",
        ("No MES signal source for these machines (jigs, ovens, "
         "microscopes). Unknown ≠ idle."),
        ("這些機台沒有 MES 訊號源（治具／烤箱／顯微鏡）。"
         "不知道≠沒在做。")),
    "ld_lg_dorm": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_dormant_n",
        "Parked (long stop)",
        "長停（非排定）"),
    "ld_lg_dorm_tip": Tip(
        "_LD_I18N", P_LANDING, "landing.fleet_dormant_n",
        "Registered long stop (A11); not scheduled to run",
        "登記的長停（A11），非排定生產"),
    "ld_q2_out_tip": Tip(
        "_LD_I18N", P_LANDING, "landing.q2_output_ph",
        ("Modules that passed {st} yesterday. It is the last station on "
         "the line, so this is the day's finished-goods count."),
        ("昨日通過 {st} 的 PH 數。這是產線最後一站，"
         "所以等於當日成品數。")),
    "ld_q2_oee_tip": Tip(
        "_LD_I18N", P_LANDING, "landing.q2_oee_pct",
        ("OEE for the whole line this calendar month = availability x "
         "utilisation x yield."),
        "整條線本月的 OEE＝可用率 × 稼動率 × 良率。"),
    "ld_q2_rty_tip": Tip(
        "_LD_I18N", P_LANDING, "landing.q2_rty_pct",
        ("RTY, rolled throughput yield: the share of modules that pass "
         "every station first time, this month. It is every station's "
         "first-pass yield multiplied together, so one weak station pulls "
         "the whole number down."),
        ("RTY（直通良率）＝本月一次就通過所有站的 PH 比例。"
         "各站首次良率連乘，任何一站弱就會拉低整個數字。")),
    "ld_q3_tip": Tip(
        "_LD_I18N", P_LANDING, "landing.q3_loss_total_ph",
        ("Whole line, this calendar month, every station added up. Downtime "
         "equivalent = downtime hours x that station's usual PH per hour; "
         "yield loss = modules that failed first time; rework = extra "
         "passes through a station. Each figure is lost capacity at its own "
         "station, so the total is not the number of modules that went "
         "unshipped."),
        ("整條線、本月、所有站加總。停機當量＝停機小時 × 該站平常每小時"
         "做幾 PH；良率損失＝首次不良的 PH 數；"
         "重工＝多走一次站的 PH 數。每個數字都是各站自己的產能損失，"
         "所以合計不等於少出貨那麼多 PH。")),
    "ld_st_tip": Tip(
        "_LD_I18N", P_LANDING, "landing.state_freshness",
        ("Read from the cached equipment board only - this page never "
         "queries MES or cmms on its own. Freshness is the oldest of the "
         "sources, never the newest."),
        ("只讀設備板那份快取——這一頁不會自己去問 MES 或 cmms。"
         "鮮度取各來源裡最舊的那一個，不取最新。")),
    "ld_st_tip_oldest": Tip(
        "_LD_I18N", P_LANDING, "landing.state_freshness",
        "Oldest: {s} {t} · {m} min",
        "最舊：{s} {t}・{m} 分鐘"),
    "ld_su_tip_nodata": Tip(
        "_LD_I18N", P_LANDING, "landing.seat_state",
        "{u} · no data",
        "{u} · 無資料"),
    # ---- /utilization（A4 第 2 頁）：u_erw_prod_lbl 專屬、同鍵搬家；util_unmeasurable_* 與 /station、舊 landing 共用 ⇒ 新鍵 ut_* ----
    "u_erw_prod_lbl": Tip(
        "I18N", P_UTIL, "util.utilization",
        ("Production inside the expected run window (equipment view, "
         "interval-union)"),
        ("應生產時段內的生產（設備視角 · interval-union "
         "· 不去重 §14.4）")),
    "ut_unmeasurable_note": Tip(
        "I18N", P_UTIL, "util.zero_len_unmeasurable",
        ("Utilization is not measurable for this station: its MES segments "
         "are instantaneous event markers with no duration, so "
         "segment-length utilization does not apply. This is NOT the same "
         "as 'no signal' — there ARE MES events, they just carry no run "
         "time. Output &amp; yield below remain real."),
        ("此站稼動不可測：其 MES 段是瞬時事件標記、無時長，"
         "段長法稼動不適用。這與『沒訊號』不同——確有 MES 事件、"
         "只是不帶運轉時長。下方產出與良率仍為真實值。")),
    "ut_unmeasurable_tip": Tip(
        "I18N", P_UTIL, "util.zero_len_unmeasurable",
        ("Util not measurable — this station's MES records instantaneous "
         "events only (segments carry no run duration)."),
        "稼動不可測——此站 MES 只記瞬時事件（段無時長訊號）。"),
    # ---- /station/{cls}（A4 第 3 頁）：cp_* 專屬、同鍵搬家；q_avail_rw／q_oee_rw 與舊 landing 共用 ⇒ 新鍵 st_* ----
    "cp_rate_def": Tip(
        "_EQ_I18N", P_STATION, "station.rate_good",
        ("good units / clean producing machine-hours (rework and scrap "
         "taken out); median of qualifying days ({month}, {n})"),
        ("本月良品 ÷ 乾淨生產機時（扣重工、報廢）；"
         "合格日中位數（{month}、{n} 日）")),
    "cp_rate_tip": Tip(
        "_EQ_I18N", P_STATION, "station.rate_good",
        ("Upper-bound estimate: it does not model another machine on the "
         "same station picking up the slack."),
        "上限估算——未計同站他機補位。"),
    "cp_l_theory": Tip(
        "_EQ_I18N", P_STATION, "station.cap_theory_ph",
        "Theoretical",
        "理論產能"),
    "cp_tip_theory": Tip(
        "_EQ_I18N", P_STATION, "station.cap_theory_ph",
        ("Everything the station could have made if the whole expected run "
         "window ran at the good-unit rate. Upper bound."),
        "整段應生產時段都用良品速率跑，本來做得出來的量。上限估算。"),
    "cp_l_down": Tip(
        "_EQ_I18N", P_STATION, "station.cap_down_ph",
        "Downtime",
        "停機"),
    "cp_tip_down": Tip(
        "_EQ_I18N", P_STATION, "station.cap_down_ph",
        ("Work-order downtime intersected with the expected run window, in "
         "PH."),
        "cmms 工單停機 ∩ 應生產時段，換成 PH。"),
    "cp_l_idle": Tip(
        "_EQ_I18N", P_STATION, "station.cap_idle_ph",
        "Should-run idle",
        "該跑沒跑"),
    "cp_tip_idle": Tip(
        "_EQ_I18N", P_STATION, "station.cap_idle_ph",
        ("Time inside the window when the equipment was fine but nothing "
         "was being produced."),
        "機器好著、時段內卻沒在生產的時間。"),
    "cp_l_fail": Tip(
        "_EQ_I18N", P_STATION, "station.cap_fail_ph",
        "Fails",
        "不良"),
    "cp_tip_fail": Tip(
        "_EQ_I18N", P_STATION, "station.cap_fail_ph",
        "PH that failed first time (Qlik first-FAIL caliber).",
        "Qlik 首次失敗的頭。"),
    "cp_l_good": Tip(
        "_EQ_I18N", P_STATION, "station.cap_good_ph",
        "Good output",
        "良品產出"),
    "cp_tip_good": Tip(
        "_EQ_I18N", P_STATION, "station.cap_good_ph",
        ("Measured good output for this window, Qlik caliber - the same "
         "figure the Output page and the Monthly report print."),
        ("本期間量到的良品產出，Qlik 口徑——與產出頁、"
         "月報印的是同一個數字。")),
    "cp_rework_in": Tip(
        "_EQ_I18N", P_STATION, "station.cap_equiv_ph",
        "includes rework ~{p}% (took production time, not deducted again)",
        "內含重工 ≈{p}%（佔用生產時間，已在速率裡，不另扣）"),
    "cp_tip_rework": Tip(
        "_EQ_I18N", P_STATION, "station.cap_equiv_ph",
        ("That machine time WAS spent producing, it just did not become new "
         "good units - second and later attempts. It stands next to good "
         "output as an addition, and is never deducted a second time. "
         "Counted in attempts, so read it as ~."),
        ("那段機時**確實被生產用掉了、只是沒有變成新的良品**——第二次"
         "以後的嘗試。它站在良品旁邊當**加項**，"
         "永遠不會再從良品裡扣一次。單位是「次」不是 PH，所以恆帶 ≈。")),
    "cp_equiv_ledger": Tip(
        "_EQ_I18N", P_STATION, "station.cap_equiv_ph",
        "the daily ledger's own rework estimate is {n} PH",
        "日帳自己的重工估計是 {n} PH"),
    "cp_col_bar": Tip(
        "_EQ_I18N", P_STATION, "station.unit_bar",
        "Timeline (produced / down / should-run idle)",
        "時間軸（生產／停機／該跑沒跑）"),
    "cp_tip_card": Tip(
        "_EQ_I18N", P_STATION, "station.unit_bar",
        ("Expected run window x good-unit rate = theoretical capacity; each "
         "row below is one kind of loss, and what is left is good output. "
         "The multiplication above is a score, this is a ledger - the good "
         "row includes the machine time spent on retries, so it reads "
         "slightly higher than the score alone would give."),
        ("應生產時段 × 良品速率 ＝ 理論產能，往下每一層是一種損失，"
         "最後剩的就是良品。上面乘出來的是分數、"
         "下面畫的是帳——帳裡的良品段包含重工佔用的機時，"
         "所以會比分數乘出來的略高。")),
    "cp_dorm_seats": Tip(
        "_EQ_I18N", P_STATION, "station.seat_rows",
        "{k} seat(s) excluded - capacity counted on {n}/{N}",
        "{k} 座不計 · 產能按 {n}/{N} 座計"),
    "cp_tag_ph_full": Tip(
        "_EQ_I18N", P_STATION, "station.tag_filter",
        ("Time rows recomputed over the machines shown; the fails / good "
         "rows are station-wide (product view, never split by capability). "
         "The machines filtered out are {pct}% of this station's producing "
         "machine-hours."),
        ("時間三層已按顯示的機台重算；不良／良品層＝全站產品視角（不按能力"
         "拆）。被濾掉的機台佔本站生產機時 {pct}%。")),
    "cp_tag_ph_na": Tip(
        "_EQ_I18N", P_STATION, "station.tag_filter",
        ("Time rows recomputed over the machines shown; output and quality "
         "are not split by product, so this card shows hours only. The "
         "machines filtered out are {pct}% of this station's producing "
         "machine-hours - too much to price the remaining hours at a "
         "station-level rate."),
        ("時間三層已按顯示的機台重算；產出與良率不按產品拆分，"
         "故本卡只顯示小時。被濾掉的機台佔本站生產機時 {pct}%——太"
         "多了，剩下的小時不能再用站級速率換算。")),
    "cp_rate_tile_tag": Tip(
        "_EQ_I18N", P_STATION, "station.tag_filter",
        ("station-level rate - the machines shown are {pct}% of this "
         "station's producing machine-hours"),
        "站級尺 · 顯示的機台佔本站生產機時 {pct}%"),
    "st_q_avail_rw": Tip(
        "I18N", P_STATION, "station.availability",
        ("Of the expected run window, the share the equipment was "
         "available. Every work order that stops the machine pulls it down "
         "— repair, planned PM, waiting parts, waiting vendor, legacy "
         "eMaint orders. = 1 − work-order downtime ÷ expected run window. "
         "Same caliber as the Availability on the equipment meeting and "
         "utilization pages."),
        ("應生產時段裡，設備可用的比例。所有會讓機台停下來的工單都會拉低它"
         "——維修、計畫保養、等料、等外包、eMaint 舊制單。"
         "＝1 − 應生產時段內的工單停機 ÷ 應生產時段。與設備會議頁、"
         "稼動頁的妥善率**同一把尺**。")),
    "st_q_oee_rw": Tip(
        "I18N", P_STATION, "station.oee",
        ("One overall number on the expected run window: available × busy × "
         "good. = Availability × Utilization × Quality."),
        ("應生產時段上的一個總分：可用 × 有在跑 × 做得好。"
         "＝妥善率 × 稼動率 × 良率。")),
    # ---- /equipment（A4 第 4 頁）：eq_* 專屬鍵，同鍵搬家（欄標題／chip 字面不動） ----
    "eq_col_stop_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.stop_h",
        "work-order downtime inside the expected run window, last {d} days",
        "近 {d} 天應生產時段內的工單停機合計"),
    "eq_delta_basis_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.avail_delta_pp",
        ("Δ = availability of the latest full production day − average "
         "availability over the last {d} days (percentage points)"),
        "Δ＝最新一個完整生產日的妥善率 − 近 {d} 天平均妥善率（百分點）"),
    "eq_mes_asof_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.mes_last_h",
        ("The MES last-production evidence was last refreshed at {hm} (its "
         "10-minute push is delayed — usually MES connectivity). Ages are "
         "still measured against now; only machines that resumed production "
         "after {hm} may look staler than they are."),
        ("MES 最後生產證據上次更新於 {hm}（每 10 分鐘的推送延遲了——通常是 MES "
         "連線問題）。距今時數仍以現在為基準計算；只有 {hm} 之後才復跑的機台會顯得比實際久一點。")),
    "eq_now_dormant_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.dormant",
        ("Registered as not scheduled for production since {d} — {r}. This "
         "is a recorded decision, not a fault and not a guess. Availability "
         "is not computed (showing 100% would vouch for a machine that is "
         "deliberately not running)."),
        ("自 {d} 起登記為不排生產——{r}。這是被記錄下來的決策，不是故障、也不是猜的。"
         "不計妥善率（顯 100% 等於替一台刻意不開的機器背書）。")),
    "eq_equiv_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.cell_equiv_ph",
        ("Output equivalent ≈ in-window downtime × this station’s actual "
         "production rate (PH = modules). Upper-bound estimate: sister "
         "machines covering is not accounted for; the authority for real "
         "loss is the cluster target-vs-actual gap."),
        ("產出當量 ≈ 應生產時段內的停機 × 本站實際生產速率（PH＝module）。"
         "上限估算：未計同站他機補位；真損失以綑級「實際 vs 目標」缺口為準。")),
    "eq_rate_seat_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.cell_equiv_ph",
        (" Rate: this station’s output per seat-hour × this row’s seats (a "
         "Gen2 bay counts as one Gen1 machine)."),
        "\u3000速率＝站級每座速率 × 本列座數（座‧小時尺；Gen2 一個槽位算一台 Gen1）。"),
    "eq_rate_mh_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.cell_equiv_ph",
        (" Rate: this station’s output per machine-hour (old ruler; on a "
         "station with unequal seat counts this is a lower bound)."),
        "\u3000速率＝站級每機台小時（舊尺；混座站為下限）。"),
    "eq_seat_row_equiv_tip": Tip(
        "_EQ_I18N", P_EQUIP, "equip.cell_equiv_ph",
        (" Subset: the parent machine’s equivalent above already includes "
         "this row (folded by k/n). Never add the two together."),
        "\u3000其中：母機列的當量已含本列（按 k/n 折過），本列不得再加。"),
    "eq_now_producing": Tip(
        "_EQ_I18N", P_EQUIP, "equip.now_bucket",
        "UP · producing",
        "UP·生產中"),
    "eq_now_idle": Tip(
        "_EQ_I18N", P_EQUIP, "equip.now_bucket",
        "UP · idle",
        "UP·待命"),
    "eq_now_down": Tip(
        "_EQ_I18N", P_EQUIP, "equip.now_bucket",
        "DOWN",
        "DOWN"),
    "eq_now_nosig": Tip(
        "_EQ_I18N", P_EQUIP, "equip.now_bucket",
        "no run in {n}d+",
        "近 {n} 天無生產"),
    "eq_now_dormant": Tip(
        "_EQ_I18N", P_EQUIP, "equip.dormant",
        "not scheduled",
        "長停"),
    "eq_strip_dormant": Tip(
        "_EQ_I18N", P_EQUIP, "equip.dormant",
        "{n} not scheduled (excluded from availability)",
        "{n} 個工位長停不計"),
    "eq_dx_conflict": Tip(
        "_EQ_I18N", P_EQUIP, "equip.wo_mes_conflict",
        "seed=noise, human-confirmed real",
        "種子判雜訊、人工確認為真"),
    "eq_coll_mesprod": Tip(
        "_EQ_I18N", P_EQUIP, "equip.wo_mes_conflict",
        "producing in MES {n}",
        "MES 有生產 {n}"),
    "eq_wo_downtime": Tip(
        "_EQ_I18N", P_EQUIP, "equip.stop_h",
        "cmms reported downtime {m} min",
        "cmms 申報停機 {m} min"),
    # ---- /daily（A4 第 5 頁）：at_* 歸因桶標籤與口徑句，同鍵搬家（family I18N_ATTRIB） ----
    "at_b_wo": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_wo_h",
        "Work-order downtime",
        "工單停機"),
    "at_overlap": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_wo_h",
        ("Hours where a work order was open while the machine was still "
         "producing — counted as producing, shown here for reconciliation."),
        "工單開著、機台仍在生產的時數——歸生產，這裡列出來供對帳。"),
    "at_b_starved": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_starved_h",
        "Starved (no material)",
        "斷料"),
    "at_stale": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_starved_h",
        ("\"Starved\" means the queue is empty — over-aged material still at "
         "the door counts as material on hand."),
        "「斷料」＝門口沒料；超齡老料還在門口就不算斷料（物理上料在）。"),
    "at_b_blocked": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_blocked_h",
        "Blocked (downstream backed up)",
        "下游堵"),
    "at_cross": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_blocked_h",
        ("On /utilization, \"unknown\" is the un-split superset; starved and "
         "blocked here are split by positive WIP evidence, not by absence "
         "of a work order."),
        ("/utilization 的「不明」是尚未裂解的上位集；本頁的斷料/下游堵是靠 WIP "
         "正證據裂解出來的，不是「沒有工單」反推。")),
    "at_b_unexplained": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_unexplained_h",
        "Unexplained (possibly manpower / scheduling / other)",
        "未解釋（可能人力/排班/其他）"),
    "at_gate": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.gap_unexplained_h",
        ("Idle time with no production signal within {h}h is left "
         "unexplained (it looks exactly like an unstaffed shift)."),
        ("前後 {h} 小時內沒有任何生產訊號的閒置時間一律留在未解釋（它跟「這班本來就沒排人」"
         "長得一模一樣）。")),
    "at_pareto_cover": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.pareto_30d",
        ("Covers {n} of {d} days ({miss} missing, {deg} with a degraded "
         "work-order source)."),
        "涵蓋 {d} 天中的 {n} 天（缺 {miss} 天、{deg} 天工單源降級）。"),
    "at_est": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.good_rate",
        ("≈ estimated PH, converted at this station's own PH per "
         "machine-hour."),
        "≈ 估算 PH 數，用本站自己的「每機台小時產能」換算。"),
    "at_note_zero_len": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.unmeasurable_reason",
        ("This station's segments have no duration, so a time ledger cannot "
         "be computed — output view only."),
        "本站 segment 無時長、時間帳本結構性算不出來——只給產出視角。"),
    "at_note_nosig": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.unmeasurable_reason",
        ("No MES signal and no work orders in the window — nothing to "
         "attribute (not the same as zero downtime)."),
        "這一天沒有 MES 訊號、也沒有工單——無可歸因（≠沒有停機）。"),
    "at_note_no_fleet": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.unmeasurable_reason",
        "No machines on the roster for this station in the window.",
        "這一天本站名冊上沒有機台。"),
    "at_note_no_window": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.unmeasurable_reason",
        ("The observation window could not be derived from the inputs — "
         "nothing to attribute."),
        "從輸入推不出觀測期間——無可歸因。"),
    "at_note_src_err": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.unmeasurable_reason",
        ("This station's timeline could not be read just now (query failed "
         "or skipped by the time budget) — this is not the same as \"no "
         "machines\"."),
        "本站 timeline 當時讀不到（查詢失敗或預算跳站）——不是「沒有機台」。"),
    "at_note_not_in_kun": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.unmeasurable_reason",
        ("not applicable — every machine on this station is outside the kun "
         "catalog, so the expected run window does not apply (A8: excluded "
         "from the ledger and from the actionable total)"),
        "不適用——本站機台全數不在綑名冊，應生產時段對它們不成立（A8：不進帳本、不進可行動合計）"),
    "at_caliber": Tip(
        "I18N_ATTRIB", P_DAILY, "daily.ledger_schema_ver",
        ("Equipment axis, raw MES segments; work-order hours per the S12 "
         "phase rules (same function as the Equipment page)."),
        "設備軸、MES raw 段；工單時數走 S12 分階段口徑（與設備頁同一支函式）。"),
    # ---- TIPS-ROWS-END ----
}


# =============================================================================
# 2b. `VALUE_PATHS`（A2；**純常數表**——`get_metric` 的取值對照，取值的碼不在這裡）
# =============================================================================
#: `scope_kind` enum：`fleet`＝全機隊那一格；`cls`＝某一站（精確 cls，由 `list_stations` 解析）。
SCOPE_KINDS = ("fleet", "cls")
#: `transform` enum＝**名字**（對應的純函式住 `board_mcp._VALUE_TRANSFORMS`，本檔不呼叫）。
#:   `as_is`＝原值直送；`round1`＝四捨五入 1 位小數；`pct100_round1`＝×100 再取 1 位；
#:   `as_bool`＝`bool(...)`；`sec_to_h_round1`＝秒 → 小時再取 1 位（RW 軸的 `|RW|` 是秒，
#:   登記表那一筆的 `unit` 是 `h` ⇒ **換算住 transform、不住取值的碼、更不住登記表**）。
TRANSFORMS = ("as_is", "round1", "pct100_round1", "as_bool", "sec_to_h_round1")


@dataclass(frozen=True)
class ValuePath:
    """一筆指標在某一個 `scope_kind` 下的取值路徑（**全部是字串與 tuple、零 I/O**）。"""

    loaders: tuple[str, ...]      # 這次會走到的既有 loader 名（＝信封 `queries`；零新增查詢路徑）
    scope_kind: str               # SCOPE_KINDS 之一
    key_path: tuple[str, ...]     # loader 回傳物件裡的鍵路徑
    transform: str                # TRANSFORMS 之一
    note: str                     # 一句話：這條路徑答的是哪一格


#: **A2 的可達面＝17 個 `(id, scope)` 組合**（`SPEC-HERMES-TRUST.md` §4.1「只先接 `/utilization`
#: 與 `/station` 已有 fetcher 可達的值」的字面）。**不在這張表裡的 id 一律回 `no_value_path`**
#: ＋一個閉集裡的 `reason`（那張表住 `board_mcp._NO_VALUE_REASON`，資料驅動、不是 if/elif）。
#: ⚠ 加一筆＝**接一條新的取值路徑**，必須是既有 loader 的既有鍵；新增 MES 查詢路徑是 §4.2-4 禁的。
#:
#: ★ 2026-09-21：`app.station_axis_payload` 抽出來之後，RW（應生產時段）軸的 11 筆進表
#:   （原本 12 筆的 `rw_axis_not_exposed`）。**那一支就是 `/station` 頭卡在走的同一支**
#:   ⇒ 工具答的數字與頁上印的是同一個（QG-13）。
#:   ⚠ **`util.down_by_state` 刻意不進表**：它的值是逐態 dict、不是純量，而 `get_metric`
#:     的 `value` 是純量欄（smoke 釘「不是數就是 bool 就是 null」）。硬塞＝在一個純量欄裡
#:     回一個 dict，下游沒有任何人知道怎麼讀。
#:   ⚠ RW 軸 **live only**（三層擋）⇒ demo／snapshot 下這 11 筆是「有路徑、但這一次沒有值」
#:     ＝`value=null` ＋ `unavailable_reason`，**不是** `no_value_path`（§12.2）。
VALUE_PATHS: dict[str, tuple[ValuePath, ...]] = {
    "util.fleet_util_24h": (
        ValuePath(("app.load_scoreboard",), "fleet", ("fleet", "util"), "pct100_round1",
                  "機隊 24h 退路軸稼動＝`fleet_scoreboard` 頂層那一格（ratio ⇒ ×100）"),
        ValuePath(("app.load_scoreboard_one",), "cls", ("util_strict_pct",), "round1",
                  "單站 24h 退路軸稼動；`zero_len_only` 為真 ⇒ 值 null＋不可測旗，絕不回 0"),
    ),
    "util.zero_len_unmeasurable": (
        ValuePath(("app.load_scoreboard_one",), "cls", ("zero_len_only",), "as_bool",
                  "這一站的段長法稼動是不是結構性不可測（bool）"),
    ),
    "station.quality": (
        ValuePath(("app.load_qlik_grid", "board_data.qlik_rows_by_cls"), "cls",
                  ("final_yield_pct",), "as_is",
                  "站級良率（公司 Qlik 口徑）；43 站白名單外誠實留白、絕不回退 raw"),
    ),
    "station.cap_fail_ph": (
        ValuePath(("app.load_qlik_grid", "board_data.qlik_rows_by_cls"), "cls",
                  ("fail",), "as_is",
                  "不良層＝`qlik_fail` 原值（**已經是 PH，不再乘速率**）"),
    ),
    "station.cap_good_ph": (
        ValuePath(("app.load_qlik_grid", "board_data.qlik_rows_by_cls"), "cls",
                  ("pass",), "as_is",
                  "良品主字＝量到的 `qlik_pass`（不是殘差）"),
    ),

    # ---- RW（應生產時段）軸 11 筆（2026-09-21；全部走 `app.station_axis_payload` 一支）----
    #   `key_path` 的第一段＝那支回傳 dict 的鍵：`st`＝`board_oee.station_model` 的回傳、
    #   `cap`＝`capacity_waterfall` 的回傳、`cap_rate`＝`_station_rate` 的第一個回傳。
    #   ⚠ 三軸（A／U／OEE）在 `st` 裡是 **ratio**（0..1）⇒ `pct100_round1`；`rw_planned_sec`
    #     是**秒** ⇒ `sec_to_h_round1`；`cap` 的 PH 六欄已經是 PH 整數 ⇒ `as_is`。
    "util.planned_h": (
        ValuePath(("app.station_axis_payload",), "cls", ("st", "rw_planned_sec"),
                  "sec_to_h_round1",
                  "這一站這段窗的 Σ|RW|（全 Board 唯一一支；live only）"),
    ),
    "util.utilization": (
        ValuePath(("app.station_axis_payload",), "cls", ("st", "utilization"), "pct100_round1",
                  "RW 軸稼動率＝生產 ÷ 應生產時段（與 `station.utilization` 同一格）"),
    ),
    "util.availability": (
        ValuePath(("app.station_axis_payload",), "cls", ("st", "availability"), "pct100_round1",
                  "RW 軸妥善率＝（應生產 − 計停機∩RW）÷ 應生產（與 `station.availability` 同一格）"),
    ),
    "station.availability": (
        ValuePath(("app.station_axis_payload",), "cls", ("st", "availability"), "pct100_round1",
                  "站級妥善率（RW 軸、妥善率白名單）"),
    ),
    "station.utilization": (
        ValuePath(("app.station_axis_payload",), "cls", ("st", "utilization"), "pct100_round1",
                  "站級稼動率（RW 軸）"),
    ),
    "station.oee": (
        ValuePath(("app.station_axis_payload",), "cls", ("st", "oee"), "pct100_round1",
                  "站級 OEE＝妥善 × 稼動 × 良率（重工不乘進去）"),
    ),
    "station.rate_good": (
        ValuePath(("app.station_axis_payload",), "cls", ("cap_rate",), "round1",
                  "良品速率 PH/h＝`board_monthly.month_rate`（全站唯一一把尺；live only）"),
    ),
    "station.cap_theory_ph": (
        ValuePath(("app.station_axis_payload",), "cls", ("cap", "theory_ph"), "as_is",
                  "理論產能＝應生產時段 × 良品速率（無尺／無計數 ⇒ null）"),
    ),
    "station.cap_down_ph": (
        ValuePath(("app.station_axis_payload",), "cls", ("cap", "down_ph"), "as_is",
                  "停機層 PH（取整後入 C-CP3 恆等式）"),
    ),
    "station.cap_idle_ph": (
        ValuePath(("app.station_axis_payload",), "cls", ("cap", "idle_ph"), "as_is",
                  "該跑沒跑層 PH"),
    ),
    "station.cap_equiv_ph": (
        ValuePath(("app.station_axis_payload",), "cls", ("cap", "equiv_ph"), "as_is",
                  "重工當量＝殘差 − 良品（只畫斜紋、永不成為一層）"),
    ),
}


# =============================================================================
# 3. `REVIEW_NOTES`（拿不準的地方**一行一句寫清楚**，不靜默猜；SPEC §13 待裁的延伸）
# =============================================================================
REVIEW_NOTES: tuple[str, ...] = (
    "參照種類：`inputs` 在 SPEC §2.6 就混了兩種東西——`module.name` 與**登記表自身的 metric id**"
    "（`util.planned_h`／`station.rate_good`／`util.down_by_state`／`station.oee` 的「上三者」）。"
    "selftest 第 2 條先認 metric 交叉參照、再走 AST；這是解析方式，不是放寬斷言。",
    "裸模組名：修正輪後只剩 `daily.gap_starved_h` 的 `inputs`＝`mes_wip` 一處，且它是 draft。"
    "第 2 條對 `verified` 指標的裸模組名一律 FAIL（不是 warn）——`verified` 的意思是追到 `檔:行`。",
    "`daily.unmeasurable_reason` 的 `formula_ref`：SPEC 原指 `board_attrib.py:85-94`，但那一段是"
    "**六個分開的 `NOTE_*` 常數**、沒有單一彙總名。本檔取 `board_attrib.NOTE_I18N_KEY`"
    "（`board_attrib.py:1592`，值恰好就是那六個 key 的對映）當 `formula_ref`；"
    "六個 `NOTE_*` 常數名本身未進 `inputs`（`inputs` 放的是日帳 reader）。",
    "`daily.pareto_30d` 用 `read_ledger_docs`（複數、近 N 天）、`daily.ledger_schema_ver` 與 "
    "`daily.unmeasurable_reason` 用 `read_ledger_doc`（單日）——兩支是不同函式，**別互換**。",
    "`equip.wo_mes_conflict` 的兩個來源是從 `board_eqmeeting.py:2278-2281` 的實作追出來的："
    "工單側＝該列已算好的 segments（源頭函式 `wo_segments`，`:435`；`build_row` 內經 "
    "`dormant_split_segments` 包一層），MES 側＝Now 欄那份 `mes_last_h`（loader `app.load_updown`，"
    "`app.py:9693`）。**零新查詢**是這條揭露的前提；A2 若改成自己查 MES 就違約。",
    "`daily.ranked_losses`／`daily.families` 的 `i18n_keys` 仍為空：`/daily` 版面另外那批鍵"
    "（`dl_*`／`un_*`）不住 `I18N_ATTRIB`，也不住 app.py 五家族 ⇒ 要填得先裁「第七個家族算不算」。"
    "本輪不擴、不編鍵名。",
    "`daily.good_rate` 掛 `at_est`、`daily.ledger_schema_ver` 掛 `at_caliber` 兩處是**借用**："
    "前者講的是 est 換算用的「每機時幾顆」、後者講的是帳本口徑句，都不是為這個數字寫的專屬鍵。"
    "A4（五頁 tooltip 改讀登記表）動到這兩格時要重看一次。",
    "SPEC §13 ☐6 的六筆原生 draft：**A2（2026-09-21）補齊其中四筆的出處並轉 verified**"
    "（`landing.fleet_dormant_n`／`station.tag_filter`／`equip.avail_delta_pp`／`equip.dormant`，"
    "逐筆理由寫在該筆上方的 ★ 註解）；`daily.gap_starved_h`／`daily.gap_blocked_h` **維持 draft**"
    "——它們卡的不是出處而是**現場事實**（缺料／下游堵的判準門檻，`ROADMAP.md:376` 標「Chris 未答、"
    "別自己猜門檻」）⇒ 本輪不猜。⇒ verified 51／draft 2。",
    "`equip.dormant` 的 `inputs` 原寫「A11 拉取」（散文、AST 追不到）。A2 改成本 repo 的具名函式"
    "`board_data.get_dormant_events`（整批拉取＋整批降級）＋`board_w8_transport."
    "fetch_schedule_state_events`（HTTP 那一支）＋`board_dormant.DORMANT_BRIDGE`（拉不到時的過渡橋）"
    "⇒ 「跨 repo 來源怎麼登記」這一題**不必裁**：登記的是我方的消費端，cmms 是它的資料來源。",
    "⚠ `equip.avail_delta_pp` 的一句公式（逐字照 SPEC §2.6 (D)）寫「一鍵結單情境下**抑制** Δ」，"
    "但 A2 逐行追碼的結果是：**那是 `/maint` 的 MTTR Δ**（`board_maint.wo_lifecycle` 的 `oneclick` "
    "旗、`GUIDE-DOWNTIME-RESIDUAL.md` §3「唯一行為變更＝Δ 箭頭抑制」），`/equipment` 這一格的 Δ "
    "只在**長停登記**或**非名冊**時變 None（`board_eqmeeting.build_row`）。**本檔不擅自改公式句**"
    "（C-HT2：SPEC 是正本），改成把更正寫進 `pitfalls`＝`explain_metric` 會把它與公式一起吐出來。"
    "要治本得由主 session 改 SPEC §2.6 (D) 那一列。",
    "`VALUE_PATHS`（A2）只登記**已有 fetcher 可達**的 17 個 `(id, scope)` 組合（2026-09-21 從 6 個"
    "擴到 17：RW 軸 11 筆改走新抽出的 `app.station_axis_payload`）；其餘 37 個 id（2026-09-23 `#169` 加 `equip.cell_equiv_ph` 後＝38）的"
    "「為什麼沒有值」寫在 `board_mcp._NO_VALUE_REASON`（資料驅動）。**兩張表刻意分家**："
    "本檔是登記表（零 I/O），reason 閉集是工具面的答法，會隨 A3／A4 的可達面改變。",
)

# 合成產線的退階語意（`SPEC-SYNTHLINE.md` §7.1；WP-4b R-B4，2026-09-30 Fable 代裁、Chris 可否決）。
#   **只是文字**：不改任何指標的定義、不加 `Metric` 欄位、`explain()` 輸出不變。`board_mcp.explain_metric`
#   只在合成進程把這一句附在回答上（非合成進程一個位元都不印）⇒ 回答「這個數字從哪來」時不會把合成
#   世界講成工廠資料。C-SY11：不含 LIVE／REAL／real／「真值」。第一期腿的範圍＝SPEC §1.2 N6／§6。
SYNTH_NOTE = {
    "en": ("On the synthetic line every figure comes from the synthetic world (a generated production line "
           "on a live clock), not from the plant. Legs not provided in phase 1: yield / quality / Qlik "
           "(machine-level and station-level), WIP and dispatch, manpower, rework, the daily attribution "
           "page, cost and budget — metrics that need them read \"—\"; `freshness` describes the page "
           "cadence, not the data origin."),
    "zh": ("在合成產線上，每個數字都出自合成世界（依即時時鐘產生的生成產線），不是工廠資料。第一期未提供的腿："
           "良率／品質／Qlik（機台級與站級）、WIP 與調度、人力、重工、每日歸因頁、成本與預算——依賴它們的指標一律"
           "印「—」；`freshness` 描述的是頁面節奏，不是資料出處。"),
}


# =============================================================================
# 4. 公開函式
# =============================================================================
def get(mid: str) -> Metric:
    """取一筆；不存在丟 `ValueError`（不回 None——靜默的 None 會變成靜默的謊）。"""
    try:
        return ALL_METRICS[mid]
    except KeyError:
        raise ValueError(f"unknown metric id: {mid}") from None


def _page_match(registered: str, want: str) -> bool:
    """`/station/{cls}` 這類樣板路由：樣板本身、或樣板 `{` 之前的字首，都算命中。"""
    if registered == want:
        return True
    if "{" in registered:
        prefix = registered.split("{", 1)[0].rstrip("/")
        return bool(prefix) and (want == prefix or want.startswith(prefix + "/"))
    return False


def list_for_page(page: str | None = None) -> list[dict]:
    """該頁（或全部）的輕量清單；依 id 排序。**不含公式全文**（§4.1 `list_metrics`）。"""
    out = []
    for mid in sorted(ALL_METRICS):
        m = ALL_METRICS[mid]
        if page is not None and not any(_page_match(p, page) for p in m.pages):
            continue
        out.append({"id": m.id, "title": dict(m.title), "unit": m.unit,
                    "caliber": m.caliber, "status": m.status, "pages": list(m.pages)})
    return out


def explain(mid: str, lang: str = "en") -> dict:
    """單一指標的「怎麼算」；`title`／`formula` 取該語言字串（§4.1 `explain_metric`）。"""
    if lang not in LANGS:
        raise ValueError(f"unknown lang: {lang}")
    m = get(mid)
    return {
        "id": m.id,
        "title": m.title[lang],
        "formula": m.formula[lang],
        "caliber": m.caliber,
        "unit": m.unit,
        "freshness": m.freshness,
        "pitfalls": list(m.pitfalls),
        "verify": dict(m.verify),
        "contracts": list(m.contracts),
        "pages": list(m.pages),
        "status": m.status,
    }


def persona_rules_summary(lang: str = "en") -> str:
    """給 A3 注入 persona 的口徑摘要（≤12 行純文字）。

    **口徑住登記表、persona 只准放風格與邊界**（C-HT11）——這支就是那條界線的實作。
    """
    if lang not in LANGS:
        raise ValueError(f"unknown lang: {lang}")
    note = CALIBER_NOTE[lang]
    order = ("qlik-first-fail", "raw-machine-level", "raw-equipment-axis",
             "dual", "rw-planned", "daily-ledger", "eqmt-board-now")
    if lang == "en":
        head = "Caliber rules (generated from the metric registry; never hand-write a caliber):"
        tail = ["Unit rule: quantities are always reported in PH (modules), never in pieces.",
                "Confidence is positive-only {high, medium, low}: no signal means low, and low "
                "means ask a follow-up, not \"nothing is wrong\"."]
    else:
        head = "口徑規則（由指標登記表產生；**不得**在 persona 手寫口徑）："
        tail = ["單位鐵則：數量單位一律 PH（modules），不用「顆」。",
                "信心正面 only {high, medium, low}：無訊號⇒low，而 low 的下游是補問，"
                "不是「這台沒問題」。"]
    lines = [head] + [f"- {c}: {note[c]}" for c in order] + tail
    return "\n".join(lines)


def persona_rules_hash(lang: str = "en") -> str:
    """`persona_rules_summary(lang)` 的 sha256 前 16 碼（A3；`SPEC-HERMES-TRUST.md` §4.3「部署 hash 釘」）。

    **repo 產生值**：gateway（住 WSL、零 Board 匯入）把同一段文字以產生區塊嵌在 `hermes/gateway.py`
    `_PERSONA_RULES`，自算 `PERSONA_RULES_SHA` 並由 `/health` 加性鍵 `persona_rules_sha` 對外報；
    smoke `[metrics]` 釘「gateway 那一份 ＝ 本函式」，部署後 `/health` 那一格 ＝ 本函式＝線上真的推了。
    配方定死：UTF-8、不含尾端換行（＝`persona_rules_summary` 的原樣輸出）。
    """
    import hashlib
    return hashlib.sha256(persona_rules_summary(lang).encode("utf-8")).hexdigest()[:16]


def to_json() -> str:
    """整份登記表序列化（tuple→list、`ensure_ascii=False`、`indent=2`、鍵排序）。"""
    doc = {}
    for mid, m in ALL_METRICS.items():
        row = {}
        for f in fields(m):
            v = getattr(m, f.name)
            row[f.name] = list(v) if isinstance(v, tuple) else v
        doc[mid] = row
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)


def tip_keys(page: str | None = None, family: str | None = None) -> list[str]:
    """`TIPS` 的鍵（依登記順序）；可按頁（樣板路由規則同 `list_for_page`）或家族篩。"""
    return [k for k, t in TIPS.items()
            if (page is None or _page_match(t.page, page))
            and (family is None or t.family == family)]


def tip_texts(lang: str, family: str | None = None) -> dict[str, str]:
    """`{i18n 鍵: 該語言文字}`（`family` 給了就只取該家族）。未知 lang 丟 `ValueError`。"""
    if lang not in LANGS:
        raise ValueError(f"unknown lang: {lang}")
    return {k: getattr(t, lang) for k, t in TIPS.items() if family is None or t.family == family}


def merge_tips(target: dict, family: str | None = None) -> None:
    """把 `TIPS` 的文字灌進 `target`（形狀 `{lang: {key: text}}`，**就地**）。

    app 端每個家族字典定義完就呼叫一次（`family`＝該字典名）；`family=None` ＝全部灌進去
    （給合併後的總字典，或冒煙的正向釘用）。**只加／覆寫 `TIPS` 自己的鍵**，別的鍵一個位元不碰。
    """
    for lang in LANGS:
        target.setdefault(lang, {}).update(tip_texts(lang, family))


# =============================================================================
# 5. `--selftest`（SPEC §2.5 八條；**全部 AST／文字靜態，零目標模組匯入**）
# =============================================================================
_ID_RE = re.compile(r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")
# 六個 I18N 家族＝`{模組檔: (家族名…)}`。六個都是 `{lang: {key: text}}` 形（實測）。
# ★ 2026-09-19 修正輪加入第六個：`/daily` 歸因桶的標籤住 `board_attrib.I18N_ATTRIB`，
#   不在 app.py 裡——少掃它就等於逼 `/daily` 十筆指標的 `i18n_keys` 永遠留空。
_I18N_SOURCES = (
    ("app.py", ("I18N", "_EQ_I18N", "_AV_I18N", "_LD_I18N", "_MR_I18N")),
    ("board_attrib.py", ("I18N_ATTRIB",)),
    # ★ 2026-09-23 #167 P4：第七個家族＝`/downtime` 的 `dt_*`（唯一一份住 `board_downtime`）。
    ("board_downtime.py", ("I18N_DOWNTIME",)),
)
_FORMULA_MAX = 160


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8", errors="replace").read()


_TOPLEVEL_CACHE: dict[str, set | None] = {}


def _toplevel_names(mod: str) -> set | None:
    """該模組檔頂層的 `def`／`class`／賦值名集合；檔案不存在回 None。**零匯入**。"""
    if mod in _TOPLEVEL_CACHE:
        return _TOPLEVEL_CACHE[mod]
    path = os.path.join(BASE, mod + ".py")
    if not os.path.exists(path):
        _TOPLEVEL_CACHE[mod] = None
        return None
    names: set = set()
    try:
        tree = ast.parse(_read(path))
    except SyntaxError:
        _TOPLEVEL_CACHE[mod] = names
        return names
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
                elif isinstance(tgt, (ast.Tuple, ast.List)):
                    for e in tgt.elts:
                        if isinstance(e, ast.Name):
                            names.add(e.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    _TOPLEVEL_CACHE[mod] = names
    return names


def _resolve_ref(ref: str) -> tuple[str, str]:
    """回 `(kind, why)`：`kind` ∈ {metric, bare_module, module_name, unresolved}。

    `why` 只在 `unresolved` 有值。**`bare_module` 是合法解析但不夠強**——
    第 2 條對 `status="verified"` 的指標會把它當 FAIL（見該處註解）。
    """
    if ref in ALL_METRICS:
        return "metric", ""                           # ① 登記表自身的 metric id
    if "." not in ref:                                # ② 裸模組名
        if os.path.exists(os.path.join(BASE, ref + ".py")):
            return "bare_module", ""
        return "unresolved", f"裸模組 {ref}.py 不存在"
    mod, _, name = ref.partition(".")                 # ③ module.name
    names = _toplevel_names(mod)
    if names is None:
        return "unresolved", f"模組檔 {mod}.py 不存在"
    if name not in names:
        return "unresolved", f"{name} 不在 {mod}.py 的 AST 頂層"
    return "module_name", ""


_I18N_CACHE: dict | None = None


def _i18n_keys_by_family() -> dict:
    """六個 I18N 家族 → `{家族名: {lang: set(keys)}}`。**每檔只 `ast.parse` 一次**、零匯入。"""
    global _I18N_CACHE
    if _I18N_CACHE is not None:
        return _I18N_CACHE
    out: dict = {}
    for fname, families in _I18N_SOURCES:
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            continue
        try:
            tree = ast.parse(_read(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if not (isinstance(tgt, ast.Name) and tgt.id in families):
                    continue
                if not isinstance(node.value, ast.Dict):
                    continue
                langs: dict = {}
                for k, v in zip(node.value.keys, node.value.values):
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Dict):
                        keys = set()
                        for kk in v.keys:
                            if isinstance(kk, ast.Constant) and isinstance(kk.value, str):
                                keys.add(kk.value)
                        langs[k.value] = keys
                out[tgt.id] = langs
    # ★ A4（#138）：第八個「家族」＝本檔自己的 `TIPS`（文字已搬進登記表、不再住字典字面量）。
    #   `i18n_keys` 指到的鍵只要在六個字典或這裡其中之一、且兩語齊，就算存在。
    out["TIPS"] = {lang: {k for k, t in TIPS.items() if getattr(t, lang)} for lang in LANGS}
    _I18N_CACHE = out
    return out


_SPEC_CACHE: list | None = None


def _spec_blobs() -> list:
    """repo 根的 `SPEC-*.md`／`_SPEC-*.md` 全文（utf-8、`errors="replace"`）。"""
    global _SPEC_CACHE
    if _SPEC_CACHE is not None:
        return _SPEC_CACHE
    paths = sorted(set(glob.glob(os.path.join(BASE, "SPEC-*.md"))
                       + glob.glob(os.path.join(BASE, "_SPEC-*.md"))))
    _SPEC_CACHE = [(os.path.basename(p), _read(p)) for p in paths]
    return _SPEC_CACHE


def _selftest() -> int:
    fails: dict = {}
    warns: list = []

    def fail(n: int, detail: str) -> None:
        fails.setdefault(n, []).append(detail)

    def warn(n: int, detail: str) -> None:
        warns.append(f"warn {n}. {detail}")

    # --- 1. id 唯一＋格式 -----------------------------------------------------
    seen: set = set()
    for m in _METRICS:
        if m.id in seen:
            fail(1, f"{m.id} 重複")
        seen.add(m.id)
        if not _ID_RE.match(m.id):
            fail(1, f"{m.id} 不符 ^[a-z][a-z0-9]*\\.[a-z][a-z0-9_]*$")
    if len(ALL_METRICS) != len(_METRICS):
        fail(1, f"ALL_METRICS {len(ALL_METRICS)} != _METRICS {len(_METRICS)}")

    # --- 2. formula_ref／inputs 以 AST 靜態解析（零匯入） ---------------------
    #     ★ 2026-09-19 修正輪：`verified` 的指標**不准**留裸模組名——`verified` 的意思是
    #       「追到 `檔:行`」，指到一整個檔不算追到（draft 才可以，且會印 warn）。
    for m in _METRICS:
        for ref in (m.formula_ref,) + m.inputs:
            kind, why = _resolve_ref(ref)
            if kind == "unresolved":
                msg = f"{m.id}: `{ref}` — {why}"
                if m.status == "draft":
                    warn(2, msg + "（status=draft ⇒ 不算 FAIL）")
                else:
                    fail(2, msg)
            elif kind == "bare_module":
                msg = f"{m.id}: `{ref}` 是裸模組名（沒有 .name）"
                if m.status == "draft":
                    warn(2, msg + "（status=draft ⇒ 不算 FAIL）")
                else:
                    fail(2, msg + "，verified 指標不得留裸模組名")

    # --- 3. contracts 卡號在某份 SPEC 內 grep 得到 ---------------------------
    #     公開樹只帶 DESIGN.md／SPEC-UI-V2.md、不帶內部 SPEC 全文 ⇒ 本條標 skipped、不算失敗；私有樹照舊全查。
    import core_paths as _wp_pub
    _pub = _wp_pub.is_public_tree()
    blobs = _spec_blobs()
    if _pub:
        print("board_metrics selftest: skipped: internal SPEC-*.md absent (public tree), check 3 not run")
    elif not blobs:
        fail(3, "repo 根找不到任何 SPEC-*.md／_SPEC-*.md")
    for m in ([] if _pub else _METRICS):
        for card in m.contracts:
            if any(card in b for _n, b in blobs):
                continue
            msg = f"{m.id}: 卡號 {card} 在 SPEC-*.md／_SPEC-*.md 內 grep 不到"
            if m.status == "draft":
                warn(3, msg + "（status=draft ⇒ 不算 FAIL）")
            else:
                fail(3, msg)

    # --- 4. i18n_keys 存在於 app.py 五家族之一、且 en／zh 兩語都有 -----------
    fams = _i18n_keys_by_family()
    want_fams = {f for _src, families in _I18N_SOURCES for f in families}
    missing_fams = sorted(want_fams - set(fams))
    if missing_fams:
        fail(4, f"I18N 家族解析不到：{missing_fams}")
    for m in _METRICS:
        for key in m.i18n_keys:
            hit = [fam for fam, langs in fams.items()
                   if all(key in langs.get(lang, set()) for lang in LANGS)]
            if not hit:
                where = [fam for fam, langs in fams.items()
                         if any(key in langs.get(lang, set()) for lang in LANGS)]
                fail(4, f"{m.id}: i18n 鍵 `{key}` "
                        + (f"只在 {where} 的單語出現" if where else "不在六個家族內"))

    # --- 5. verify 三鍵齊且 page_url_tpl 非空 -------------------------------
    for m in _METRICS:
        if not isinstance(m.verify, dict):
            fail(5, f"{m.id}: verify 不是 dict")
            continue
        missing = [k for k in ("page_url_tpl", "ssms_ref", "expect") if k not in m.verify]
        if missing:
            fail(5, f"{m.id}: verify 缺鍵 {missing}")
        elif not str(m.verify["page_url_tpl"]).strip():
            fail(5, f"{m.id}: verify.page_url_tpl 是空的")

    # --- 6. enum ＋ pages ----------------------------------------------------
    for m in _METRICS:
        if m.caliber not in CALIBERS:
            fail(6, f"{m.id}: caliber `{m.caliber}` 不在 enum")
        if m.freshness not in FRESHNESS:
            fail(6, f"{m.id}: freshness `{m.freshness}` 不在 enum")
        if m.unit not in UNITS:
            fail(6, f"{m.id}: unit `{m.unit}` 不在 enum")
        if m.status not in STATUS:
            fail(6, f"{m.id}: status `{m.status}` 不在 enum")
        if not m.pages:
            fail(6, f"{m.id}: pages 是空的")
        for p in m.pages:
            if not p.startswith("/"):
                fail(6, f"{m.id}: page `{p}` 不以 / 開頭")

    # --- 7. formula／title 兩語齊、一句、≤160 -------------------------------
    for m in _METRICS:
        for lang in LANGS:
            s = m.formula.get(lang)
            if not s:
                fail(7, f"{m.id}: formula[{lang}] 缺")
                continue
            if "\n" in s:
                fail(7, f"{m.id}: formula[{lang}] 含換行（一句就是一句）")
            if len(s) > _FORMULA_MAX:
                fail(7, f"{m.id}: formula[{lang}] 長度 {len(s)} > {_FORMULA_MAX}")
            if not m.title.get(lang):
                fail(7, f"{m.id}: title[{lang}] 缺")

    # --- 8. 負斷言：本檔全檔不得出現三個字串（用拼接組出，免得自己踩自己） ---
    banned = ("im" + "port app", "py" + "odbc", "SEL" + "ECT ")
    try:
        src = _read(os.path.abspath(__file__))
    except Exception as e:                            # pragma: no cover
        src = ""
        fail(8, f"讀不到本檔原始碼：{e}")
    for b in banned:
        if b in src:
            fail(8, f"本檔出現禁字串 `{b}`")

    # --- 9. `VALUE_PATHS`（A2）：key ∈ 登記表、loader 名 AST 追得到、enum 合規 -----
    #     ★ 這一條就是「A2 的可達面不准偷偷長大」的釘子：新增一筆就必須是**既有 loader 的
    #       既有鍵**（新增 MES 查詢路徑是 SPEC §4.2-4 明禁的）。
    for mid, paths in VALUE_PATHS.items():
        if mid not in ALL_METRICS:
            fail(9, f"VALUE_PATHS 的 key `{mid}` 不在 ALL_METRICS（編出來的 id）")
        if not paths:
            fail(9, f"{mid}: VALUE_PATHS 條目是空的（要嘛有路徑、要嘛整筆別登記）")
        kinds = [p.scope_kind for p in paths]
        if len(set(kinds)) != len(kinds):
            fail(9, f"{mid}: 同一個 scope_kind 登記了兩條路徑 {kinds}")
        for p in paths:
            if p.scope_kind not in SCOPE_KINDS:
                fail(9, f"{mid}: scope_kind `{p.scope_kind}` 不在 enum")
            if p.transform not in TRANSFORMS:
                fail(9, f"{mid}: transform `{p.transform}` 不在 enum")
            if not p.key_path or not all(isinstance(k, str) and k for k in p.key_path):
                fail(9, f"{mid}: key_path 空或含非字串")
            if not p.note.strip():
                fail(9, f"{mid}: note 是空的（每條路徑要說清楚答的是哪一格）")
            if not p.loaders:
                fail(9, f"{mid}: loaders 是空的")
            for ref in p.loaders:
                kind, why = _resolve_ref(ref)
                if kind != "module_name":
                    fail(9, f"{mid}: loader `{ref}` 不是 module.name 形（{kind}{'：' + why if why else ''}）")

    # --- 10. `TIPS`（A4，#138）：文字只住一處、指標對得上、兩語齊 -----------------
    #     ★ 「一個鍵只住一處」是這一條的核心：搬進 `TIPS` 的鍵若還留在 app.py／board_attrib 的
    #       字典字面量裡，兩份文字會各自漂——而且漂了沒有任何頁面會紅（後灌的那份贏）。
    fam_literal = {f: langs for f, langs in _i18n_keys_by_family().items() if f != "TIPS"}
    for key, t in TIPS.items():
        if t.family not in TIP_FAMILIES:
            fail(10, f"{key}: family `{t.family}` 不在 {TIP_FAMILIES}")
        if not t.page.startswith("/"):
            fail(10, f"{key}: page `{t.page}` 不以 / 開頭")
        met = ALL_METRICS.get(t.metric)
        if met is None:
            fail(10, f"{key}: metric `{t.metric}` 不在 ALL_METRICS（編出來的 id）")
        else:
            if key not in met.i18n_keys:
                fail(10, f"{key}: 指標 {met.id} 的 i18n_keys 沒有它（解釋關係要雙向可查）")
            if not any(_page_match(p, t.page) or _page_match(t.page, p) for p in met.pages):
                fail(10, f"{key}: page `{t.page}` 不在指標 {met.id} 的 pages {met.pages}")
        for lang in LANGS:
            if not getattr(t, lang).strip():
                fail(10, f"{key}: {lang} 文字是空的")
        if set(re.findall(r"\{(\w*)\}", t.en)) != set(re.findall(r"\{(\w*)\}", t.zh)):
            fail(10, f"{key}: en／zh 的 {{placeholder}} 集合不同（format 會 KeyError 或漏填）")
        if "顆" in t.zh:
            fail(10, f"{key}: zh 出現「顆」（數量單位一律 PH）")
        dup = [f for f, langs in fam_literal.items()
               if any(key in langs.get(lang, set()) for lang in LANGS)]
        if dup:
            fail(10, f"{key}: 同一個鍵還留在 {dup} 的字典字面量裡（兩份文字會漂；搬家就是刪掉舊的）")

    # --- 輸出 ---------------------------------------------------------------
    titles = {
        1: "id 全表唯一、格式合規",
        2: "formula_ref／inputs 以 AST 靜態解析（零目標模組匯入）",
        3: "contracts 卡號在 SPEC-*.md／_SPEC-*.md grep 得到",
        4: "i18n_keys 存在於六個 I18N 家族（app.py 五個＋board_attrib.I18N_ATTRIB）且兩語齊",
        5: "每個指標的 verify 三鍵齊、page_url_tpl 非空",
        6: "caliber／freshness／unit／status 在 enum，pages 非空且以 / 開頭",
        7: "formula 兩語齊、無換行、≤160；title 兩語齊",
        8: "負斷言：本檔無 app 匯入／DB driver／查詢關鍵字",
        9: "VALUE_PATHS 的 key ∈ ALL_METRICS、loader 名 AST 追得到、scope_kind／transform 在 enum",
        10: "TIPS（A4）：文字只住一處（字典字面量已刪）、指標與 i18n_keys 雙向對得上、兩語齊、placeholder 同",
    }
    for n in range(1, 11):
        if n in fails:
            for d in fails[n]:
                print(f"FAIL {n}. {d}")
        else:
            print(f"ok  {n}. {titles[n]}")
    for w in warns:
        print(w)

    n_verified = sum(1 for m in _METRICS if m.status == "verified")
    n_draft = sum(1 for m in _METRICS if m.status == "draft")
    print(f"verified={n_verified} draft={n_draft} total={len(_METRICS)}")
    by_page: dict = {}
    for m in _METRICS:
        by_page[m.pages[0]] = by_page.get(m.pages[0], 0) + 1
    print("pages: " + " ".join(f"{p}={n}" for p, n in sorted(by_page.items())))
    n_paths = sum(len(v) for v in VALUE_PATHS.values())
    print(f"value_paths: {len(VALUE_PATHS)} id／{n_paths} 個 (id, scope) 組合可達")
    print(f"warn={len(warns)}（只對 status=draft 的第 2／3 條放寬，逐筆列在上面）")
    print("REVIEW_NOTES:")
    for i, note in enumerate(REVIEW_NOTES, 1):
        print(f"  {i}. {note}")

    if fails:
        print(f"FAIL {sum(len(v) for v in fails.values())} assertion(s) in "
              f"{len(fails)}/10 group(s)")
        return 1
    print("PASS 10/10")
    return 0


# =============================================================================
# 6. CLI
# =============================================================================
_USAGE = """board_metrics.py — Hermes 指標登記表（SPEC-HERMES-TRUST §2）

  python board_metrics.py --selftest            §2.5 十條靜態斷言（A2 加 VALUE_PATHS、A4 加 TIPS）
  python board_metrics.py --json                整份登記表（JSON）
  python board_metrics.py --list [page]         輕量清單（page 例：/ /utilization /station /equipment /daily）
  python board_metrics.py --explain <id> [en|zh]  單一指標的「怎麼算」
  python board_metrics.py --persona [en|zh]     A3 persona 口徑摘要（gateway `_PERSONA_RULES` 的產生來源）
  python board_metrics.py --persona-hash [en|zh]  上一行的 sha256 前 16 碼（＝gateway `/health` 的 persona_rules_sha）
"""


def main(argv: list) -> int:
    if not argv:
        print(_USAGE.rstrip())
        return 0
    cmd = argv[0]
    if cmd == "--selftest":
        return _selftest()
    if cmd == "--json":
        print(to_json())
        return 0
    if cmd == "--list":
        page = argv[1] if len(argv) > 1 else None
        rows = list_for_page(page)
        for r in rows:
            print(f"{r['id']:<28} {r['unit']:<6} {r['caliber']:<20} "
                  f"{r['status']:<9} {','.join(r['pages'])}")
        print(f"-- {len(rows)} metric(s)" + (f" on {page}" if page else ""))
        return 0
    if cmd == "--explain":
        if len(argv) < 2:
            print("--explain 需要一個 metric id")
            return 2
        lang = argv[2] if len(argv) > 2 else "en"
        try:
            doc = explain(argv[1], lang)
        except ValueError as e:
            print(str(e))
            return 2
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0
    if cmd == "--persona":
        lang = argv[1] if len(argv) > 1 else "en"
        try:
            print(persona_rules_summary(lang))
        except ValueError as e:
            print(str(e))
            return 2
        return 0
    if cmd == "--persona-hash":
        lang = argv[1] if len(argv) > 1 else "en"
        try:
            print(persona_rules_hash(lang))
        except ValueError as e:
            print(str(e))
            return 2
        return 0
    print(_USAGE.rstrip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
