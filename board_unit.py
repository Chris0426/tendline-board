#!/usr/bin/env python3
"""Tendline Board — /daily「顆數帳本」引擎 `board.unit.v1`。規格＝`SPEC-DAILY-UNIT.md`（Part 0 裁決優先）。

**回答的問題**：昨天這站「顆數去哪了」——①重工/復測 ②報廢（first-FAIL）③速率劣化（含微停，
未拆分）三桶＋橋接欄，與時間帳（`board_attrib`＝「小時去哪了」）**兩本帳各自恆等、
永不相加、永無「gap≡Σ桶」句**（Part 0 鐵則 5）。
**不是什麼**：不是 Qlik 口徑的再發明（報廢＝直接消費 `mes_qlik.build_grid` 的 per-station
`fail`）、不是 WIP/佇列口徑（那是 `mes_wip`）、不是考勤工具（§24 零人員欄）。

本檔＝**B1d 純函式引擎**（零 I/O、零 MES、零 HTTP、零檔案；`--selftest` 全離線）。
輸入形只「讀檔對版」不 import：`mes_rework` 聚合形（SPEC Part A §2.4；唯一 import 它的檔
＝`board_data`）；`board_attrib` ledger row 形與 `mes_qlik.build_grid` station row 形
則直接 import 模組讀常數/重用工具（不改它們、零 diff）。

★ 恆等式（SPEC Part A §5／Part C §2；誤差界**寫死**、非 env）
  [U-A] 嘗試帳（硬恆等 ≤1e-6）：`r_base × producing_h ＝ attempts ＋ speed_net`
        —— `speed_net` 就是定義式殘差 ⇒ 構造性成立、無捨入破口。
        （**嘗試軸的內部帳、不再是頁面的尺**＝2026-08-03 提案 E；保留供對帳/日帳續存。）
  [U-B] 顆帳（整數精確）：`attempts ＝ first_attempts ＋ rework_n`
        —— rework 源給的 `rework_n` 與 `attempts − first_attempts` 不合＝**輸入不一致**
        ⇒ `identity_ok=False`＋caveat（不是修數字、是揭露）。
  [U-D] ★ **頂層溝通式＝提案 E 單一尺**（≤1e-6；2026-08-03 Chris 拍板，取代舊 [U-C]）：
        `P − good ＝ 工單停機 ＋ 斷料 ＋ 下游堵 ＋ 未解釋 ＋ 重工 ＋ 報廢 ＋ 慢速`
        全部單位＝**顆**、全部乘**同一把尺** `r_good_base`；
          · `P = r_good_base × total_mh`、`good = qlik_pass`
          · 前四項＝`r_good_base × 時間帳四桶`（T1 保證四桶和＝total−producing ⇒ 精確）
          · 重工 `= r_good_base × rework_h`、報廢 `= r_good_base × scrap_h`
          · 慢速 `= r_good_base × good_h − qlik_pass`（可負＝比基準快）
        代數上恆真：RHS ＝ r×(total−producing) + r×(rework_h+scrap_h+good_h) − good
                        ＝ r×total − good ＝ LHS。**構造性成立、無捨入破口。**
        ★ **容差隨尺縮放**（`identity_error_ud` 本身不動、只動判定端）：時間帳四桶是
          `board_attrib` 負殘差 clamp 後的量，可能帶 k×1e-6 機時的殘渣；乘上尺
          `r_good_base`（顆／機時）之後，殘差就變成 k×1e-6×r 顆 ⇒ 固定 1e-6 顆的門檻在
          尺一大就會把構造性平的帳判成 False。判定門檻＝`max(UD_TOL, r_good_base×1e-5+UD_TOL)`
          ——真正的來源不一致（[U-B] 破、桶少一項）是**整數顆級**，遠大於此、照樣抓得到。
  ★ 舊 [U-C]（含 `unit_bridge` 項、以 `r_base` 為尺）**已退役**——`unit_bridge` 續存為
    揭露欄，但不再是恆等式的一項（口徑差改用「比例」吸收，見 `split_producing`）。

★ 提案 A/E 承重裁決（2026-08-03 Chris 拍板；設計存證＝`ROADMAP.md` §2.1 ＋ SPEC Part C）
  · **A 綠色切三段**：`producing_h` 依**當日比例**切成 `rework_h / scrap_h / good_h`
    （`split_producing`），三段**精確加總＝producing_h**。假設「每次嘗試耗時相同」＋
    「首次嘗試的報廢率＝當日 Qlik first-FAIL 率」——兩句都必須印在頁上。
    ★ 報廢用**率**（`q = qlik_fail/(qlik_pass+qlik_fail)`）不用**絕對數**：絕對數會把
      `unit_bridge`（Qlik 母體 ≠ 窗內活動）帶進切分 ⇒ 第四段可正可負、堆疊條會溢出。
      用率則三段恆非負、恆平——口徑差由比例自然吸收（bridge 續存為獨立揭露欄）。
  · **E 良品速率＝唯一換算尺** `r_good = qlik_pass / good_h`（good_h＝扣掉重工與報廢時數
    的「乾淨機台小時」）。**為何分母要同時扣重工與報廢**：只扣重工（＝Chris 草稿的 5.39）
    會讓報廢被吸進速率裡 ⇒ 報廢桶再列一次就是雙計；兩者都扣 ⇒ `r_good` 對報廢率中性
    （分子分母同步縮），四個桶彼此正交。
    ★ 一頁一把尺：`board_attrib` 的 est（distinct lot/機時）與本檔 `r_y`（attempts/機時）
      **一律不得出現在 /daily 的損失語句裡**（§2.1「三個速率並存」＝這條要解的問題）。
  · 基準 `r_good_base`＝近 7 日日帳 `r_good` 的**中位數**（同 `r_base` 的樣本判準）；
    v2 舊日帳沒有 `r_good` 欄 ⇒ `r_good_of_units` **從 v2 的存量欄現場推導**
    （`producing_h = attempts / r_y` 可還原）⇒ **換尺不需要回補、21 站基準不歸零**。
  · 速率劣化分子＝**attempts（含重工列）**（§4）：分子若排除重工，重工造成的吞吐下降會
    同時進速率桶＝雙計。速率桶只量「每次嘗試變慢＋微停」的殘差、與重工桶正交。
  · 基準 `r_base`＝近 7 日日帳 `r_y` 的**中位數**（同站；**只從日帳讀、絕不即時重算 7 天**）。
    樣本 < `BOARD_UNIT_BASE_MIN_DAYS`（預設 4）→ `None`＋note `base_warming`（首週自然
    如此、無回補）。樣本納入判準＝`doc wo_source_status=='ok' ∧ r_y 可測 ∧ identity_ok
    is not False`——**刻意不是** SPEC §9-3 字面的 `identity_ok≠None`：首暖期 identity_ok
    恆 None，字面規則會讓基準永遠暖不起來（死鎖）；`is not False` 保留其本意
    （identity 破的日子＝資料不一致、不可信）且可收斂。
  · 橋接欄＝**揭露不硬扣**（§5；比照 `wo_overlap_prod_h`）：`unit_bridge` 可正可負、
    不是 bug——正＝昨日活動單位尚未（或不）進 Qlik 顯示母體；負＝錨落昨日但活動在他日。
    `rework_time_est_h ≈ rework_n / r_base`＝揭露欄、**不從時間帳扣**。
  · partial 模式（§5）：`r_base=None` ⇒ [U-A]/[U-C]「—」（`identity_ok=None`），但 [U-B]
    與報廢**照算照顯**（純計數不需 rate）——顆數帳可用性不被基準首暖綁架。
  · 報廢源 degraded/unavailable ⇒ scrap 欄 None＋`scrap_src_degraded`、[U-C] 降 partial；
    `absorption` N=0 也出行（「本窗零吸收」是有效資訊）；degraded 時 `absorbed_total=None`
    （**不可信的吸收數不顯 0**）。
  · 全停日（producing=0 ∧ attempts=0）：`speed_net=0.0`（量到了就是零）；
    producing≤0 ∧ attempts>0＝**輸入不一致** → `speed_net=None`＋caveat（不硬給負值）。
  · **跨窗零嘗試日（producing>0 ∧ attempts=0）＝同一族的來源不一致**（嘗試錨在窗外＝
    跨午夜 lot），note `counts_window_mismatch`：`r_y=None`（**若落 0.0 會進 7d 中位樣本
    把基準拉低＝毒化**）、`speed_net=None`＋caveat。`split_producing` 本來就回 None
    ⇒ `loss_by=None`、[U-D]「—」。**真全停日不受影響**（producing==0 那條分支照舊）。
  · T6 量不到「—」絕不 0；估算恆帶 ≈（`slow_est_n`／`rework_time_est_h`／est 全家）。
  · T7/§24：本檔輸出零人員欄；diag passthrough 一律過 `wattr._scrub` 剝人員鍵。
  · 凍結紀律：`mes_timeline`/`mes_yield` 不 import；`board_attrib` 引擎/`mes_qlik` 只
    import 重用、零改動；**不 import `mes_rework`**（唯一 import 它的檔＝`board_data`）。

★ 日帳 v3（SPEC §6 ＋ Part C §4；讀寫 I/O 在 `board_data`，本檔只管形狀）
  `LEDGER_SCHEMA_VER='board.daily_ledger.v3'`；station doc `units` 子塊加提案 A/E 欄
  （`good_h`/`rework_h`/`scrap_h`/`r_good`/`r_good_base`/`base_good_n_days`/`slow_good`）。
  `parse_ledger_doc` **三收 v1/v2/v3**（v1 照讀、`units` 補 None；v2 照讀、新欄補 None
  ——**基準取樣時再由 `r_good_of_units` 現場推導**＝換尺不必回補）；
  `should_overwrite` 擴充＝`wo!=ok ∨ v1 ∨ v2 ∨ units 缺/degraded`。
  時間榜聚合權威仍在 `wattr.pareto30`——v2 檔經 `to_v1_doc` 降視圖後餵它
  （`pareto30_time`），**不另寫一份時間聚合**；顆數榜＝`pareto30_units`（本檔）。
  附帶：`ledger_doc` 對「unit board 有、attrib board 沒有」的站（如 43−交集的無時間源站）
  **補一列時間欄全 None、note='no_time_src' 的 station doc**——否則這些站的顆數史永遠
  進不了日帳、顆數 Pareto 對它們恆盲（§6 精神：兩榜各自誠實）。

CLI：`python board_unit.py --selftest`（純離線）｜`--demo`（印 demo 板 JSON）
"""
import argparse
import json
import math                              # `isfinite` ＝ ranked_losses 的 NaN/inf 守衛
import os
import sys
from datetime import datetime, timedelta   # `datetime` ＝ pareto30_units 的壞 day 守衛用

import mes_qlik as mq                  # 43 站序/顯示名唯一權威（不另抄）
import board_attrib as wattr         # 時間帳形狀/工具/日帳 v1（唯讀重用、零 diff）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# =============================================================================
# 1. 常數 / env
# =============================================================================
SCHEMA_ID = "board.unit.v1"
CALIBER_UNIT = ("daily unit ledger (PH): scrap/rework per the Qlik first-FAIL caliber "
                "in the same window; slow vs the station's own 7d baseline rate "
                "(approx; incl. micro-stops, not split); never added to the time ledger")

# 三個顆數桶（顯示/堆疊固定序＝Part B §7：scrap→rework→slow；色盤驗證依此鄰接序）
UNIT_BUCKETS = ("scrap", "rework", "slow")

# ── 提案 E 單一尺的八個損失桶（[U-D]；全部單位＝顆、全部乘 r_good_base）────────────
# 前五＝時間帳五桶（`board_attrib.BUCKETS` 同名同序、不另抄語意）、後三＝顆數帳三桶。
LOSS_BUCKETS = tuple(wattr.BUCKETS) + UNIT_BUCKETS          # 8 個；顯示序＝本序
# ★ 三分（提案 C ＋ 2026-08-22 G2）：
#   · **可行動**＝真的有人可以去修的產能損失。
#   · **推定**（`unexplained`）**永不進損失合計**——它是「沒有排班源時夜班/週末閒置的去處」
#     （SPEC-DAILY-ATTRIB T3），算進損失會得到「損失 359 顆 vs 目標 90 顆」那種一問就破的
#     句子。它照樣**現形**（獨立子句 + 時間條照畫），只是不加總。
#   · **非生產**：★ 2026-08-23 A5 之後**空集合**——closure 桶已退場（全廠停線改在
#     時間帳的**分母端**被扣掉＝該跑窗；`SPEC-RUNWINDOW.md` §4.3、存證＝
#     `SPEC-DAILY-ATTRIB.md` §8.4）。常數保留＝三分結構與其斷言不塌；哪天再有
#     「有證據的非生產桶」就放這裡。
ACTIONABLE_BUCKETS = ("wo", "starved", "blocked", "rework", "scrap", "slow")
PRESUMED_BUCKETS = ("unexplained",)
NONPRODUCTION_BUCKETS = ()
assert (set(ACTIONABLE_BUCKETS) | set(PRESUMED_BUCKETS) | set(NONPRODUCTION_BUCKETS)
        == set(LOSS_BUCKETS)), \
    "可行動 ∪ 推定 ∪ 非生產 必須恰好覆蓋 LOSS_BUCKETS（漏一個桶＝損失憑空消失）"

# 恆等式誤差界（SPEC §5：**寫死**、非 env——這是規格，不是校準參數）
UA_TOL = 1e-6
UD_TOL = 1e-6
UC_TOL = UD_TOL                      # 舊名 alias（外部若有引用不炸；[U-C] 本身已退役）

# 基準（§4）：7d 窗 pin；最少樣本走 env（live 校準＝SPEC §10 Q2）
BASE_DAYS = 7
BASE_MIN_DAYS = wattr._env_int("BOARD_UNIT_BASE_MIN_DAYS", 4)
# r_y 分母下限（與 wattr._rate_per_mh 同一把尺、同一個 env）：瞬時段站 producing_h≈0.0017h
# 會算出 14 萬顆/機台小時的 r_y **並落進日帳**——四天後就變成基準中位數＝垃圾永久化。
MIN_PROD_MH = float(os.environ.get("BOARD_ATTRIB_MIN_PROD_H", str(wattr.MIN_PROD_MH)))

# note key（穩定 key 非文案；時間側四種直接沿用 attrib 的 note 值＝同一套字彙）
NOTE_NO_TIME_SRC = "no_time_src"       # 43 站中無稼動源／attrib 未給該站（≠ src_err ≠ zero_len）
NOTE_NO_REWORK_SRC = "no_rework_src"   # 重工計數源不可用（mes_rework 失敗）
NOTE_BASE_WARMING = "base_warming"     # 7d 基準樣本不足（首週自然如此、無回補）
NOTE_NO_QLIK = "no_qlik"               # 報廢源（Qlik grid）不可用/degraded
# 機台在生產、但本窗零嘗試列（嘗試錨在窗外＝跨午夜 lot）＝**兩源窗口不一致**、不是速率 0。
NOTE_COUNTS_WINDOW = "counts_window_mismatch"
# ★ 上一條的**鏡像**：窗內有嘗試列（機器真的在跑）、Qlik 母體卻整站掛零（`pass+fail==0`
#   且 grid 列存在＝不是 degraded）＝兩源窗口不一致的另一個方向（Qlik 錨在他日／該站本窗
#   還沒有單位完成生涯）。放行的話 `r_good = 0/good_h = 0.0` 會落進日帳、進 7d 中位樣本
#   ⇒ `r_good_base=0.0` ⇒ 整板「未使用 ≈0、還比基準快 N 顆」而且**一句 caveat 都沒有**。
NOTE_QLIK_WINDOW = "qlik_window_mismatch"
UNIT_NOTE_KEYS = (NOTE_NO_TIME_SRC, NOTE_NO_REWORK_SRC, NOTE_BASE_WARMING, NOTE_NO_QLIK,
                  NOTE_COUNTS_WINDOW, NOTE_QLIK_WINDOW,
                  wattr.NOTE_ZERO_LEN, wattr.NOTE_NOSIG, wattr.NOTE_NO_FLEET,
                  wattr.NOTE_NO_WINDOW, wattr.NOTE_SRC_ERR)

# 日帳 v4（v1 常數唯一權威＝wattr；不重打字串）
#   ★ 2026-09-03 M3：v3 → **v4**＝站列多 `rw`／`families` 兩個 additive 尾欄
#     ＋doc 級三個新鍵 `rw_axis`／`rw_reason`／`families_status`。
#     舊版號常數一律**保留**（`should_overwrite` 要分辨「該升級」與「別 clobber」）。
#   ★ 2026-09-04（`SPEC-CAPACITY.md` §3.1）：v4 → **v5**＝站列 `rw` 區塊多三個 additive 欄
#     （`avail_down_sec`／`down_by_state`／`eids`）。**沒有任何欄位被改名或改值**——
#     `health_down_sec` 原值照寫（舊尺、相容欄）。升版號的理由是**口徑分界**：月級的
#     A／U 從此只吃 `avail_down_sec`，v4 檔沒有那一欄 ⇒ 必須能被一眼分辨、不得混算
#     （C-MR8「改口徑＝換 id、不原地改」）。`should_overwrite` 見下：v4 檔自動可覆寫。
LEDGER_SCHEMA_V1 = wattr.LEDGER_SCHEMA_VER            # "board.daily_ledger.v1"
LEDGER_SCHEMA_V2 = "board.daily_ledger.v2"
LEDGER_SCHEMA_V3 = "board.daily_ledger.v3"
#   ★ 2026-09-07（#98／`SPEC-LANDING.md` §18.18）：v5 → **v6**＝站列 `units` 多一個
#     additive 尾欄 `units_by_family`（`{"Plover": n, …}`；零值不寫鍵、鍵序 R→A→M→未標）。
#     **`qlik_pass` 一個位元不動**——這是同一個量的拆分，恆等式 `qlik_pass ≡ Σ` 由
#     `mes_qlik.build_grid` 保證、本檔再驗一次。升版號的理由**不是換口徑**（總和不變），
#     而是「這份檔有沒有那一腿」必須一眼分辨得出來：回補判準（`family_split_unknown`）
#     與 `should_overwrite` 都靠它，v5 檔照讀 ⇒ 兩欄退回單一數字（誠實）。
LEDGER_SCHEMA_V4 = "board.daily_ledger.v4"
LEDGER_SCHEMA_V5 = "board.daily_ledger.v5"
#   ★ 2026-09-08（#103／`_SPEC-QLIK-YIELD.md` §12）：v6 → **v7**＝doc 級多一個加性鍵
#     `products`（現只有 `products.kestrel`）＝**同一份帳的第二棵產品子樹**。
#     · **既有每一個 Plover 鍵一個位元不動**（#101 那條「母體一個位元不動」的帳版本）：
#       站列、`units`、`line`、doc 級舊鍵全部原樣；Kestrel 只住在新的子樹裡。
#     · **兩棵樹永不相加**（D1）：Qlik 沒有「全線」那張表，Board 也不發明。子樹的讀取
#       只有 `product_stations_of` 一個入口，Plover 的聚合函式一個都不看 `products`
#       （selftest 逐位元負釘：加了子樹之後 `to_v1_doc`／`pareto30_units`／`base_rates`
#       ／`family_split_unknown` 的輸出完全相同）。
#     · **v6 檔照收**：沒有 `products` ⇒ `product_stations_of` 回 `None`＝「Kestrel 無帳」
#       ⇒ 顯示「—」，**絕不是 0**（同 `units_by_family` 的 None／{} 紀律）。
LEDGER_SCHEMA_V6 = "board.daily_ledger.v6"
LEDGER_SCHEMA_V7 = "board.daily_ledger.v7"
#   ★ 2026-09-25（#182-P2；`_INVEST-RW-DENOMINATOR-20260925.md` §3.2／`_INVEST-NOSIG-DRIFT` §6 F4；
#     SPEC-DOWNTIME §14 ㉘）：v7 → **v8**＝doc 級五個**加法**鍵（`_DOC_V8_KEYS`），全部是
#     「這份帳**什麼時候、用哪個錨、在什麼狀態下**建的」的證據——沒有它們，錯錨的回補檔、
#     日曆尾巴還沒封口的日子、建帳時工單還開著的日子，在檔面上和好帳長得一模一樣。
#     · 既有每一個鍵一個位元不動；v7 檔照讀（新鍵 ⇒ None＝**未知**）。
#     · 升版號的理由＝「這份檔有沒有那些證據」必須一眼分辨（缺 `rw_anchor` ⇒ `rw_anchor_legacy`）。
LEDGER_SCHEMA_VER = "board.daily_ledger.v8"

# doc 級 v4 新鍵（**呼叫端傳入、本檔不猜**）：
#   · `rw_axis`        bool｜None——這一天的日帳是不是站在該跑窗軸上（False＝退回 24h 軸）。
#                      None ＝「不知道」（舊檔／呼叫端沒說）——**不得當成 False**：
#                      False 是「我知道它降級了」，會觸發覆寫升級；None 只是沒有主張。
#   · `rw_reason`      "ok"｜"calendar_absent"｜"calendar_uncovered"｜"error:<T>"｜None
#   · `families_status` "ok"｜"skipped:<why>"｜"error:<T>"｜None（None ≠ {}：沒抓≠抓到零筆）
_DOC_V4_KEYS = ("rw_axis", "rw_reason", "families_status")

# station doc `units` 子塊鍵集（SPEC §6 ＋ Part C §4；派生顯示欄不落檔＝可重算）
# ★ v3 新增七欄在**尾端**（v2 檔逐鍵讀取時前段位置不變＝parse 對舊檔零風險）。
_DOC_UNIT_KEYS_V2 = ("attempts", "first_attempts", "rework_n", "qlik_pass", "qlik_fail",
                     "r_y", "r_base", "base_n_days", "speed_net", "unit_bridge",
                     "identity_ok", "scrap_src_degraded", "note")
_DOC_UNIT_KEYS_V3 = _DOC_UNIT_KEYS_V2 + ("good_h", "rework_h", "scrap_h", "r_good",
                                         "r_good_base", "base_good_n_days", "slow_good")
# ★ v6（#98）：`units_by_family` 也加在**尾端**（同一條紀律＝舊檔逐鍵讀取位置不變）。
#   `None`＝**沒拆**（家族欄缺席／grid degraded／v5 以前的檔）；`{}`＝**拆了、這站零產出**。
#   兩者永不互換（同 `families` 的 null／{} 紀律，`SPEC-MONTHLY.md` §3.4-a）。
_DOC_UNIT_KEYS = _DOC_UNIT_KEYS_V3 + ("units_by_family",)

# ── v7（#103）：產品子樹 -------------------------------------------------------------
#   形（doc 級）：`{"products": {"kestrel": {product, n_stations, degraded, note, stations[]}}}`
#   站列鍵集＝**Plover `units` 之中「純粹由 Qlik grid 得出」的那幾個**（`qlik_pass`／
#   `qlik_fail`／`units_by_family`）＋ grid 自己的三個誠實欄（`qlik_total`／`n_null`／顯示名）。
#   ⚠ **刻意沒有** `attempts`／`rework_n`／`r_y`／`r_good`／`speed_net`：那些的分母是**時間腿**
#     （`producing_h`）與**重工腿**（`mes_rework`），而那兩腿是**設備視角、不分產品**的
#     （`CONCEPT.md` §14.4）。硬把 Kestrel 顆數除以整台機的生產機時＝憑空發明第三把尺。
#     Kestrel 那些欄一律缺席 ⇒ 頁面顯「—」（C-MR3：量不到就是量不到，不是 0）。
PRODUCTS_KEY = "products"
PRODUCT_PLOVER = "plover"           # 既有那棵樹的名字（**不落檔**：它就是 doc 本體）
PRODUCT_KESTREL = "kestrel"
_DOC_PRODUCT_STATION_KEYS = ("cls", "ord", "display", "display_pending",
                             "qlik_pass", "qlik_fail", "qlik_total", "n_null",
                             "units_by_family")
_DOC_PRODUCT_KEYS = ("product", "n_stations", "degraded", "note", "stations")
# doc 級 additive 鍵：Kestrel 這一腿當天的狀態（`None` ≠ `"ok"`；同 `families_status`
#   的紀律——「沒查」與「查過了、沒有」永不互換）。
_DOC_V7_KEYS = ("products_status",)

# ── v8（#182-P2）：建帳證據五鍵（**呼叫端主張**＝`app._build_attrib_day`；本檔不反推）────────
#   · `rw_anchor`        "HH:MM"＝這份帳的窗從幾點起算（生產日＝"08:00"）。缺／≠"08:00"
#                        ⇒ `rw_anchor_legacy`（09-01..11 與八月＝日曆日窗 00:00 錨的回補檔全中）。
#   · `built_at`         時間腿建帳時刻（ISO、台北牆鐘）。`computed_at` 會被顆數腿改寫、這個不會。
#   · `wo_open_at_build` 建帳當下**還開著**、且在本窗有 countable 段的工單號（排序 list）。
#                        `[]`＝查過、沒有；`None`＝不知道（工單源降級／舊檔）。
#                        ⚠ 建帳時已是 >90 天長掛單者**不列**（C-DT6 永遠納入＝口徑、不是時點問題）。
#   · `cal_open_tail`    建帳時日曆尾端**未閉合沉默**與本生產日重疊 ⇒
#                        `{"from": 台北 ISO, "overlap_sec": n}`；事後查明封口且沒成 closure 可就地
#                        標 `resolved`（`board_data.mark_cal_open_tail_resolved`）。`None`＝沒有。
#   · `roster_pad_unmeasurable` 不可測站列的 C-RW18 名冊證據 `{cls: {roster_pad, roster_pad_eids,
#                        empty_track_eids}}`（不可測列 `rw=None`，證據原本整筆丟掉＝F4）。
_DOC_V8_KEYS = ("rw_anchor", "built_at", "wo_open_at_build", "cal_open_tail",
                "roster_pad_unmeasurable")
RW_ANCHOR = "08:00"            # 生產日窗錨（唯一出處＝`board_eqmeeting.PROD_DAY_START_H`；selftest 交叉釘）

# retry reason（`rw_retry_reason` 的值域；`_backfill_ledger` 與凍結閘都讀這三個字面值）
RETRY_RW_ANCHOR_LEGACY = "rw_anchor_legacy"
RETRY_RW_CAL_OPEN_TAIL = "rw_cal_open_tail"
RETRY_RW_WO_OPEN_AT_BUILD = "rw_wo_open_at_build"

DEMO_DAY = wattr.DEMO_DAY

_r6 = wattr._r6


def _median(xs):
    """中位數（奇＝中點、偶＝中間兩點均值）；空 → None。"""
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    m = n // 2
    return float(xs[m]) if n % 2 else (float(xs[m - 1]) + float(xs[m])) / 2.0


# =============================================================================
# 1b. 提案 A：綠色切三段 ／ 提案 E：良品速率（純函式；本節是新尺的全部算術）
# =============================================================================
def split_producing(producing_h, attempts, rework_n, qlik_pass, qlik_fail):
    """**提案 A**：生產機台小時 → `(good_h, rework_h, scrap_h)`；量不到 → `(None,None,None)`。

    口徑（頁上必須印出這兩句假設）
      ① 每次嘗試耗時相同 ⇒ `rework_h = producing_h × rework_n / attempts`。
      ② 首次嘗試的報廢率＝當日 Qlik first-FAIL 率 `q = qlik_fail / (qlik_pass+qlik_fail)`
         ⇒ `scrap_h = (producing_h − rework_h) × q`、`good_h = 其餘`。
    ★ 三段**精確加總＝producing_h**、且恆非負（q∈[0,1]、rework_n≤attempts）。
      用**率**而不是絕對數是承重裁決：絕對數會把 `unit_bridge` 帶進切分 ⇒ 出現第四段
      且可正可負、堆疊條溢出。口徑差改由比例吸收，bridge 續存為獨立揭露欄。
    可測條件：`producing_h>0` ∧ `attempts>0` ∧ `rework_n` 可得 ∧ `0 ≤ rework_n ≤ attempts`。
      報廢腿缺（qlik_pass/qlik_fail 皆 None）⇒ `q=0` **不可**（會把報廢時數灌進 good_h ＝
      虛報良品速率）⇒ 整組回 None（顯「—」）。分母 `qlik_pass+qlik_fail == 0`（本窗 Qlik
      零母體）⇒ `q=0.0`＝量到了就是零（比照 `oee.actual_rate_uph` 的 0 vs None 分野）。
    ★ **全停日例外**（`producing_h==0 ∧ attempts==0 ∧ rework_n==0`）⇒ `(0.0, 0.0, 0.0)`：
      整天沒跑就是三段皆零、**量到了不是量不到**。若在這裡回 None，[U-D] 在全停日就整列
      算不出來（`good_h is None` ⇒ `loss_by=None`）——而全停日正是損失最大、最該把時數
      全部落進時間四桶的那一天。`producing>0 ∧ attempts==0` 或反之仍是**來源不一致** → None。
    """
    if producing_h is None or attempts is None or rework_n is None:
        return (None, None, None)
    if qlik_pass is None or qlik_fail is None:
        return (None, None, None)
    try:
        ph = float(producing_h)
        at = int(attempts)
        rw = int(rework_n)
        qp = int(qlik_pass)
        qf = int(qlik_fail)
    except Exception:
        return (None, None, None)
    if qp < 0 or qf < 0:
        # 負的 pass/fail＝來源壞值（Qlik 口徑下不可能）。放行的話 q 會落到 [0,1] 之外
        # ⇒ 三段其中一段變負、堆疊條倒著畫、[U-D] 照樣「平」（構造性）＝錯得很安靜。
        return (None, None, None)
    if ph == 0.0 and at == 0 and rw == 0:
        return (0.0, 0.0, 0.0)                 # 全停日：量到了就是零（見上方 ★）
    if ph <= 0 or at <= 0 or rw < 0 or rw > at:
        return (None, None, None)
    rework_h = ph * (rw / at)
    first_h = ph - rework_h
    denom = qp + qf
    q = (qf / denom) if denom > 0 else 0.0
    scrap_h = first_h * q
    good_h = first_h - scrap_h
    return (good_h, rework_h, scrap_h)


def good_rate(qlik_pass, good_h, *, min_prod_mh=MIN_PROD_MH):
    """**提案 E** 良品速率 `r_good = qlik_pass / good_h`（顆／乾淨機台小時）；量不到 → None。

    · `good_h < min_prod_mh`（預設 0.5 機時）→ None＝**分母下限**（2026-08-02 事故：
      瞬時段站 0.0017h 分母算出 14 萬顆/機時 **並落進日帳** ⇒ 四天後變基準中位數＝
      垃圾永久化）。與 `r_y`／`wattr._rate_per_mh` 共用同一個 env。
    · `qlik_pass == 0` 且分母足夠 → **0.0**（量到了就是零、不是不可測）。
    """
    if qlik_pass is None or good_h is None:
        return None
    try:
        gh = float(good_h)
        qp = float(qlik_pass)
    except Exception:
        return None
    if gh < float(min_prod_mh):
        return None
    return qp / gh


def family_counts_of(pass_by_family, qlik_pass):
    """grid 站列的 `pass_by_family` → 日帳 `units.units_by_family`（純函式；#98）。

    · `None` 進 ⇒ `None` 出（**沒拆**；家族欄缺席／grid degraded／舊檔）。
    · 值全部轉 int、**零值不寫鍵**、鍵序照 `mes_qlik.order_family_counts`（R→A→M→未標）。
    · **恆等式守衛**：`Σ != qlik_pass` ⇒ 回 `None`（加不回總數的拆分寧可不落檔）。
      `qlik_pass is None`（報廢腿量不到）⇒ 也回 `None`（沒有可對的總數＝不主張）。
    """
    if pass_by_family is None or qlik_pass is None:
        return None
    if not isinstance(pass_by_family, dict):
        return None
    try:
        out = mq.order_family_counts({k: int(v or 0) for k, v in pass_by_family.items()})
        return out if sum(out.values()) == int(qlik_pass) else None
    except (TypeError, ValueError):
        return None


def family_split_unknown(doc):
    """這份日帳**有沒有家族拆分那一腿**（`SPEC-LANDING.md` §18.18）；True＝沒有 ⇒ 該回補。

    ★ **唯一出處**：`should_overwrite`（落地閘）與 `_backfill_ledger.plan_day`（計畫）
      問的是**同一支**——2026-09-05 那 34 天 `already_ok` 就是「兩份會漂移的同義判準」
      造成的（[[backfill-plan-vs-write-gate]]）。新判準一律只寫一次。
    · 判準＝「有 `units`、報廢腿**量得到**（`qlik_pass is not None`）、且**沒有**
      `scrap_src_degraded`，卻缺 `units_by_family`」的站存在。
    · **收斂性**：回補之後這種站一律拿到 dict（零產出站＝`{}`）⇒ 判準轉假、不會每天重寫。
      報廢腿降級的站**排除在外**——那已經被 `should_overwrite` 既有的降級條款管著，
      再算一次只會讓結構性缺列的日子永久可覆寫。
    · 壞形／讀不懂 ⇒ **False**（不主張；讓既有的版號／壞檔判準去處理）。
    """
    p = parse_ledger_doc(doc)
    if p is None:
        return False
    for s in p.get("stations") or ():
        u = s.get("units") if isinstance(s, dict) else None
        if not isinstance(u, dict):
            continue
        if u.get("scrap_src_degraded") or u.get("qlik_pass") is None:
            continue
        if u.get("units_by_family") is None:
            return True
    return False


# =============================================================================
# 1c. 產品子樹（#103；v7 加性腿。**純函式、零 I/O**；讀寫都只有這一節）
# =============================================================================
def product_block(qlik_grid, *, product=PRODUCT_KESTREL):
    """Kestrel Qlik grid → 日帳 `products.<product>` 子樹；**不可用 ⇒ `None`**（＝子樹缺席）。

    · 材料只有一個：`mes_qlik.build_grid(product=…)` 的 grid（`board_data.get_qlik_grid`）。
      **一個式子都不算**——`qlik_pass`／`qlik_fail`／`units_by_family` 全是 grid 站列既有的
      量，本函式只挑欄位、過恆等式守衛（同 `_units_block` 的紀律）。
    · grid `unavailable`／空站列 ⇒ `None`：**Kestrel 那一腿抓不到就是缺席**，不得寫成
      一棵全 0 的子樹（0 是量到的 0、缺席是沒量到，兩者永不互換＝C-MR3／C-LD32）。
    · `diag.degraded` ⇒ 子樹照寫但 `degraded=True`、每站 `units_by_family` 一律 `None`
      （grid 自己都說降級了，家族拆分不可信）。
    · 站列鍵集＝`_DOC_PRODUCT_STATION_KEYS`（白名單；grid 多出來的欄不落檔＝可重算）。
    ⚠ **與 Plover 站列零交集**：本函式回的東西只會被放進 `doc["products"][product]`，
      永遠不會回寫 `doc["stations"]`（`ledger_doc` 那一段有負斷言）。
    """
    if not isinstance(qlik_grid, dict) or qlik_grid.get("unavailable"):
        return None
    rows = qlik_grid.get("stations")
    if not rows:
        return None
    degraded = bool((qlik_grid.get("diag") or {}).get("degraded"))
    out_rows = []
    for r in rows:
        if not (isinstance(r, dict) and r.get("cls")):
            continue
        qp, qf = r.get("pass"), r.get("fail")
        try:
            qp = None if qp is None else int(qp)
            qf = None if qf is None else int(qf)
        except (TypeError, ValueError):
            qp = qf = None
        tot = r.get("total")
        try:
            tot = None if tot is None else int(tot)
        except (TypeError, ValueError):
            tot = None
        ubf = (None if degraded
               else family_counts_of(r.get("pass_by_family"), qp))
        out_rows.append({
            "cls": r.get("cls"), "ord": r.get("ord"),
            "display": r.get("display"),
            "display_pending": bool(r.get("display_pending")),
            "qlik_pass": qp, "qlik_fail": qf, "qlik_total": tot,
            "n_null": r.get("n_null"), "units_by_family": ubf,
        })
    if not out_rows:
        return None
    return {"product": str(product), "n_stations": len(out_rows),
            "degraded": degraded, "note": None, "stations": out_rows}


def parse_product_block(blk):
    """`products.<p>` 子樹 → 驗過的 dict（白名單；壞形 ⇒ `None`，**絕不猜**）。"""
    if not isinstance(blk, dict):
        return None
    rows = []
    for s in blk.get("stations") or ():
        if not (isinstance(s, dict) and s.get("cls")):
            continue
        rows.append({k: s.get(k) for k in _DOC_PRODUCT_STATION_KEYS})
    if not rows:
        return None
    out = {k: blk.get(k) for k in _DOC_PRODUCT_KEYS}
    out["stations"] = rows
    out["n_stations"] = len(rows)
    return out


def parse_products(doc_products):
    """doc 級 `products` → `{product: 子樹}`；一棵都讀不出來 ⇒ `None`（＝這份檔沒有那一腿）。"""
    if not isinstance(doc_products, dict):
        return None
    out = {}
    for k, v in doc_products.items():
        blk = parse_product_block(v)
        if blk is not None:
            out[str(k)] = blk
    return out or None


def product_stations_of(doc, product=PRODUCT_KESTREL):
    """**子樹的唯一讀取入口**：日帳 doc → `{cls: 站列 dict}`；沒有那一腿 ⇒ `None`。

    ★ 為什麼要有唯一入口（D1「兩樹永不相加」的結構性保證）：只要每一個消費端都經過
      這一支，就不可能有人「順手」把 `products.kestrel` 的 `qlik_pass` 加進 Plover 的
      站列 Σ——那是 `#101` 已經裁過的事（Qlik 沒有「全線」那張表）。`None` 與 `{}`
      一樣**永不互換**：`None`＝這一天沒有 Kestrel 帳（顯「—」）、`{}` 不會出現
      （`product_block` 空站列就回 None）。
    """
    p = doc if isinstance(doc, dict) else None
    if p is None:
        return None
    blocks = parse_products(p.get(PRODUCTS_KEY))
    if not blocks:
        return None
    blk = blocks.get(str(product))
    if blk is None:
        return None
    return {str(s["cls"]): s for s in blk["stations"] if s.get("cls")}


def product_subtree_unknown(doc, product=PRODUCT_KESTREL):
    """這份日帳**缺 `products.<p>` 子樹**嗎（＝該跑一次子樹回補）？壞形／None ⇒ True。

    ⚠ **這一條刻意不進 `should_overwrite`**：Kestrel 那一腿抓不到是**誠實降級**
      （`products_status="error:…"`），不代表 Plover 的任何數字不是事實。把它接進覆寫閘
      ＝每一天都因為「Kestrel 沒抓到」被整份重算一次（那是 2026-09-05 那型事故的鏡像）。
      回補由 `_backfill_ledger.py --product kestrel` **明示**觸發（D2）。
    """
    return product_stations_of(doc, product) is None


def qlik_window_mismatch(attempts, qlik_pass, qlik_fail):
    """**Qlik 空母體日**判定（`NOTE_QLIK_WINDOW` 的唯一判準；純謂詞）。

    True ＝「窗內有嘗試列（`attempts>0`）、報廢腿也**量得到**（pass/fail 皆非 None＝
    grid 列在、不是 degraded），但 `pass+fail == 0`」＝Qlik 母體與窗內活動不同窗。
    此時 `q=0` ⇒ `good_h` 吃下整段 first_h、`r_good = 0/good_h = 0.0`——**那個 0.0 才是
    真正的災難**（落日帳 → 4 天後成為 `r_good_base` 中位數 → 整頁尺歸零）。
    ⇒ 呼叫端一律把 `r_good`／`loss_by` 判成量不到（None＋note），**絕不落 0.0**。
    """
    if qlik_pass is None or qlik_fail is None or attempts is None:
        return False
    try:
        return int(attempts) > 0 and (int(qlik_pass) + int(qlik_fail)) == 0
    except Exception:
        return False


def r_good_of_units(u, *, min_prod_mh=MIN_PROD_MH):
    """日帳 `units` 子塊 → `r_good`（v3 直接讀；**v2 現場推導**；不可得 → None）。

    ★ 這支就是「換尺不必回補」的關鍵：v2 檔沒有 `r_good` 欄，但
      `producing_h = attempts / r_y` 可從存量欄還原 ⇒ `split_producing` + `good_rate`
      重跑一次即得。**沒有它，提案 E 上線當天全 43 站基準歸零、要等 4 天**
      （或跑 16 分鐘回補；兩者都不必了）。
    ★ Qlik 空母體日（`qlik_window_mismatch`）**在讀 `r_good` 欄之前就先擋掉**：v3 直讀路徑
      也要擋，否則本修上線前已落地的毒值（`r_good=0.0`）會繼續從舊檔流進基準樣本。
    """
    if not isinstance(u, dict):
        return None
    if qlik_window_mismatch(u.get("attempts"), u.get("qlik_pass"), u.get("qlik_fail")):
        return None
    rg = u.get("r_good")
    if rg is not None:
        try:
            return float(rg)
        except Exception:
            return None
    r_y, at = u.get("r_y"), u.get("attempts")
    if r_y is None or at is None:
        return None
    try:
        r_y = float(r_y)
        at = int(at)
    except Exception:
        return None
    if r_y <= 0 or at <= 0:
        return None
    producing_h = at / r_y
    good_h, _rw, _sc = split_producing(producing_h, at, u.get("rework_n"),
                                       u.get("qlik_pass"), u.get("qlik_fail"))
    return good_rate(u.get("qlik_pass"), good_h, min_prod_mh=min_prod_mh)


# =============================================================================
# 2. 基準速率（§4）——只從日帳讀、絕不即時重算 7 天
# =============================================================================
def base_rates(docs, day, *, days=BASE_DAYS, min_days=None):
    """近 `days` 日日帳 → `{cls: {"r_base","n_days","r_good_base","n_days_good"}}`。

    · 窗＝`day` 的**前** `days` 天（不含 `day` 自己——重算日不得自我引用）。
    · 同日多檔＝後到勝（dict 覆寫；正常單機單服務不會發生、防呆而已）。
    · 樣本判準（docstring 頂部有裁決存證）：`wo_source_status=='ok'` ∧ units 存在 ∧
      該速率可測 ∧ `identity_ok is not False`。degraded 日/不可測日不入樣本。
    · 樣本數 < `min_days`（預設 `BASE_MIN_DAYS`）→ 該基準 `None`（呼叫端標 `base_warming`）。
    ★ **兩把尺各自獨立取樣**：`r_base`（嘗試軸、[U-A] 用）與 `r_good_base`（提案 E 的
      **頁面唯一尺**、[U-D] 用）天數可以不同（v2 舊檔的 `r_good` 靠 `r_good_of_units`
      推導，推不出來的日子只掉 `r_good_base` 那一側、不連坐 `r_base`）。
    """
    min_days = BASE_MIN_DAYS if min_days is None else int(min_days)
    d0 = wattr._dt(str(day))
    if d0 is None:
        return {}
    want = {(d0 - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(1, int(days) + 1)}
    by_day = {}
    for doc in docs or ():
        p = parse_ledger_doc(doc)
        if p is not None and p["day"] in want:
            by_day[p["day"]] = p
    samples, samples_good = {}, {}
    for p in by_day.values():
        if str(p.get("wo_source_status") or "").lower() != "ok":
            continue
        # ★ 一天一站最多一個樣本：檔內若同 cls 重複出現（手改/合併過的檔），逐列 append 會
        #   讓「天數」虛胖 ⇒ 基準提早生效、且該日在中位裡被加權兩次（verify V1-LOW）。
        day_rates, day_good = {}, {}
        for s in p["stations"]:
            u = s.get("units")
            if not u or u.get("identity_ok") is False:
                continue
            if u.get("r_y") is not None:
                day_rates[s["cls"]] = float(u["r_y"])  # 同日同站重複＝後到勝（同 by_day 慣例）
            rg = r_good_of_units(u)
            # ★ 第三道防線（前兩道＝`_build_row` 與 `r_good_of_units` 的空母體守衛）：
            #   `r_good <= 0` 一律**不入樣本**。合法的零到不了這裡——真全敗日（pass=0 ∧
            #   fail>0）的 q=1 ⇒ `good_h=0` ⇒ 早被 `MIN_PROD_MH` 判成 None；能算出 0.0 的
            #   路徑只有空母體日。留這道是為了舊檔（本修之前落地的毒值）。
            if rg is not None and float(rg) > 0:
                day_good[s["cls"]] = float(rg)
        for cls, r in day_rates.items():
            samples.setdefault(cls, []).append(r)
        for cls, r in day_good.items():
            samples_good.setdefault(cls, []).append(r)
    out = {}
    for cls in set(samples) | set(samples_good):
        xs = samples.get(cls) or []
        gs = samples_good.get(cls) or []
        rgb = _r6(_median(gs)) if len(gs) >= min_days else None
        if rgb is not None and rgb <= 0:
            rgb = None                 # 尺 ≤0 ＝**沒有尺**（顯「—」），絕不拿它去乘七個桶
        out[cls] = {
            "r_base": (_r6(_median(xs)) if len(xs) >= min_days else None),
            "n_days": len(xs),
            "r_good_base": rgb,
            "n_days_good": len(gs),
        }
    return out


# =============================================================================
# 3. 單站 row（內部）＋恆等式檢核
# =============================================================================
def _grid_ctx(qlik_grid):
    """Qlik grid → `(rows_by_(cls,mcl)|None, degraded_global, absorption, absorbed_total)`。

    grid None／`unavailable`／空 stations／`diag.degraded` ⇒ 全板報廢欄降級
    （scrap 全 None；`absorbed_total=None`＝**不可信的吸收數不顯 0**）。
    """
    if not isinstance(qlik_grid, dict) or qlik_grid.get("unavailable"):
        return None, True, [], None
    st = qlik_grid.get("stations")
    if not st:
        return None, True, [], None
    rows = {}
    for r in st:
        if isinstance(r, dict) and r.get("cls"):
            rows[(r.get("cls"), r.get("mcl"))] = r
    if (qlik_grid.get("diag") or {}).get("degraded"):
        return rows, True, [], None
    absorb = [dict(a) for a in (qlik_grid.get("absorption") or ()) if isinstance(a, dict)]
    at = qlik_grid.get("absorbed_total")
    at = int(at) if at is not None else sum(int(a.get("n_lots") or 0) for a in absorb)
    return rows, False, absorb, at


def _rework_ctx(rework):
    """rework 聚合形（SPEC Part A §2.4）→ `(stations|None, degraded_cls_only, diag)`。"""
    if not isinstance(rework, dict) or not isinstance(rework.get("stations"), dict):
        return None, False, {}
    diag = dict(rework.get("diag") or {})
    return rework["stations"], bool(diag.get("degraded")), diag


def _counts_of(rw_stations, cls, mcl):
    """站計數查找：先 `(cls, mcl)` 全鍵、mcl 降級時退 `cls` 單鍵；查無 → None。"""
    if rw_stations is None:
        return None
    st = rw_stations.get((cls, mcl))
    if st is None:
        st = rw_stations.get(cls)
    return st if isinstance(st, dict) else None


def _build_row(o, cls, mcl, att, counts, qrow, scrap_deg_global, b, caveats):
    """單站顆數帳 row（純函式；欄位語意見模組 docstring）。"""
    # ---- 時間腿（producing_h；量不到＝None、note 沿用 attrib 的 key）---------
    time_note = None
    producing = total_mh = None
    att_buckets = None
    if att is None:
        time_note = NOTE_NO_TIME_SRC
    elif att.get("unmeasurable"):
        time_note = att.get("note") or NOTE_NO_TIME_SRC
    else:
        producing = float(att.get("producing_h") or 0.0)
        total_mh = float(att.get("total_mh") or 0.0)
        # A5（2026-08-23）：closure 桶退場——舊板殘留的 closure_h 不進顆數帳
        #   （那段時間在新框裡「本來就不該跑」，不是未使用產能）。
        att_buckets = {k: float(att.get(f"{k}_h") or 0.0)
                       for k in ("wo_union", "starved", "blocked", "unexplained")}

    # ---- 計數腿（attempts / first / rework；[U-B]）--------------------------
    counts_note = None
    attempts = first = rework_n = None
    ub_ok = None
    if counts is None:
        counts_note = NOTE_NO_REWORK_SRC
    else:
        try:
            attempts = int(counts.get("attempts") or 0)
            first = int(counts.get("first_attempts") or 0)
            claim = counts.get("rework_n")
            rework_n = int(claim) if claim is not None else attempts - first
            ub_ok = (attempts == first + rework_n)
            if not ub_ok:
                caveats.append(f"{cls}: rework source claims rework_n={rework_n} but "
                               f"attempts-first={attempts - first} (input inconsistent)")
        except Exception:
            attempts = first = rework_n = None
            counts_note = NOTE_NO_REWORK_SRC
            caveats.append(f"{cls}: bad rework counts row (treated as unavailable)")

    # ---- 報廢腿（Qlik pass/fail；D4：絕不拿 raw 頂替）------------------------
    scrap_deg = bool(scrap_deg_global)
    qpass = qfail = None
    fam_split = None                  # #98：None＝**沒拆**（≠ `{}`＝拆了但零產出）
    if not scrap_deg:
        if qrow is None:
            scrap_deg = True          # build_grid 恆 43 列 ⇒ 缺列＝異常、按 degraded 處理
            caveats.append(f"{cls}: qlik grid row missing (per-row scrap degraded)")
        else:
            qpass = int(qrow.get("pass") or 0)
            qfail = int(qrow.get("fail") or 0)
            # ★ #98 家族拆分：**只照抄 grid 已經算好的那一份**（零第二次計數）。
            #   恆等式 `qlik_pass ≡ Σ units_by_family` 在這裡**再驗一次**——grid 端已有一道，
            #   但日帳是會被凍進月帳的落盤事實，加不回總數的拆分寧可不寫（C-LD2）。
            fam_split = family_counts_of(qrow.get("pass_by_family"), qpass)
            if fam_split is None and qrow.get("pass_by_family") is not None:
                caveats.append(f"{cls}: family split does not add up to qlik_pass "
                               f"({qpass}) - split dropped, totals untouched")

    # ---- 速率腿（r_y / r_base / speed_net；[U-A]）---------------------------
    r_base = b.get("r_base") if b else None
    base_n = int((b or {}).get("n_days") or 0)
    # ★ 跨窗零嘗試日：機台在生產、卻一列嘗試都沒有（嘗試錨在窗外＝跨午夜 lot）＝**兩源
    #   窗口不一致**，不是「速率 0」。放行的話 `r_y=0.0` 會**落進日帳、進 7d 中位樣本**＝
    #   把該站基準永久拉低（同 MIN_PROD_MH 那顆雷的反向版本）。
    window_mismatch = (attempts == 0 and producing is not None and producing > 0)
    r_y = None
    # producing < MIN_PROD_MH（瞬時段/幾乎零生產日）＝速率不可測、不落假天價（同 wattr C5 下限）
    if (attempts is not None and producing is not None and producing >= MIN_PROD_MH
            and not window_mismatch):
        r_y = attempts / producing
    speed_net = None
    if window_mismatch:
        caveats.append(f"{cls}: attempts=0 while producing_h>0 (attempts anchored outside "
                       f"this window; cross-midnight lot) -> rates left unmeasured")
    elif r_base is not None and producing is not None and attempts is not None:
        if producing > 0 or attempts == 0:
            speed_net = r_base * producing - attempts     # 全停日＝0.0（量到了就是零）
        else:
            caveats.append(f"{cls}: attempts>0 with producing_h<=0 (inconsistent sources) "
                           f"-> speed bucket left unmeasured")
    speed_loss = max(0.0, speed_net) if speed_net is not None else None
    speed_gain = max(0.0, -speed_net) if speed_net is not None else None

    # ---- 橋接（定義式殘差；揭露不硬扣）--------------------------------------
    unit_bridge = None
    if first is not None and qpass is not None and qfail is not None:
        unit_bridge = first - (qpass + qfail)
    rework_est = None
    if rework_n is not None and r_base is not None and r_base > 0:
        rework_est = rework_n / r_base

    # ---- 提案 A：綠色切三段（good_h / rework_h / scrap_h；精確和＝producing）------
    good_h, rework_h, scrap_h = split_producing(producing, attempts, rework_n,
                                                qpass, qfail)
    # ★ Qlik 空母體日：三段切分仍是有效的**機時**分解（q=0 只是說「本窗沒有單位完成生涯」），
    #   但它算出來的 `r_good` 恆為 0.0＝毒尺 ⇒ 尺與換算分解一律判成量不到（見
    #   `qlik_window_mismatch` docstring；`loss_by` 由下方的 `and not qlik_mismatch` 擋）。
    qlik_mismatch = qlik_window_mismatch(attempts, qpass, qfail)
    if qlik_mismatch:
        caveats.append(f"{cls}: attempts>0 while the Qlik population is empty "
                       f"(pass+fail=0) -> good rate and the ruler breakdown left "
                       f"unmeasured (a 0.0 rate here would poison the 7d baseline)")
    r_good = None if qlik_mismatch else good_rate(qpass, good_h)

    # ---- 提案 E：[U-D] 單一尺損失分解（全部單位＝顆、全部乘 r_good_base）---------
    r_good_base = b.get("r_good_base") if b else None
    base_good_n = int((b or {}).get("n_days_good") or 0)
    p_potential_good = good_conv = slow_good = None
    loss_by = None                       # {桶: 顆}；七桶齊、精確平（[U-D]）
    if (r_good_base is not None and total_mh is not None and good_h is not None
            and qpass is not None and att_buckets is not None and not qlik_mismatch):
        p_potential_good = r_good_base * total_mh
        good_conv = r_good_base * good_h
        slow_good = good_conv - qpass                      # 可負＝比基準快
        # ★ 鍵名必須是 **canonical 桶名**（`LOSS_BUCKETS`）——時間帳那邊叫 `wo_union`、
        #   這裡叫 `wo`。曾經寫成 `{k: r×v for k,v in att_buckets.items()}` 直接把
        #   `wo_union` 帶進來 ⇒ `loss_actionable` 對 `ACTIONABLE_BUCKETS` 取值 KeyError、
        #   `identity_error_ud`/`ranked_losses` 對 `wo` 拿到 None 而**靜默失真**（殘差回 0.0
        #   ＝「帳看起來是平的」）。故此處一律**顯式逐鍵**建 dict，不做鍵名轉譯魔術。
        loss_by = {
            "wo": r_good_base * att_buckets["wo_union"],
            "starved": r_good_base * att_buckets["starved"],
            "blocked": r_good_base * att_buckets["blocked"],
            "unexplained": r_good_base * att_buckets["unexplained"],
            "rework": r_good_base * rework_h,
            "scrap": r_good_base * scrap_h,
            "slow": slow_good,
        }
    # 可行動損失合計（提案 C：**未解釋不進合計**；負值桶〔比基準快〕不抵銷別的桶）
    loss_actionable = None
    if loss_by is not None:
        loss_actionable = sum(max(0.0, loss_by[b_]) for b_ in ACTIONABLE_BUCKETS)

    # ---- [U-C] 舊溝通式材料（已退役於恆等式，但 downtime_by 仍供對帳/明細帶）-----
    p_potential = downtime_conv = downtime_by = None
    if r_base is not None and total_mh is not None:
        p_potential = r_base * total_mh
        downtime_conv = r_base * (total_mh - (producing or 0.0))
        downtime_by = {k: r_base * v for k, v in (att_buckets or {}).items()}

    # ---- identity_ok（partial=None；量得到才判 True/False）-------------------
    #      ★ 判準＝[U-A]（嘗試軸）＋[U-B]（整數）＋[U-D]（頁面尺）；[U-C] 已退役。
    #        [U-D] 量不到（r_good_base 首暖/報廢腿缺）**不算 False**＝partial → None，
    #        否則換尺當天全板 identity_ok=False、日帳全部被判不可信（連鎖死鎖）。
    partial = (r_base is None or producing is None or attempts is None
               or qpass is None or speed_net is None or loss_by is None)
    if ub_ok is False:
        # ★ [U-B] 是**整數帳**：它破了就是來源自相矛盾，與別的腿量不量得到無關。
        #   以前先判 partial ⇒ 首暖/報廢腿缺的日子會把「rework 源自稱值對不上」吞成 None
        #   ⇒ 該日照樣進基準樣本（`base_rates` 只排除 False）＝不可信的資料毒化中位數。
        identity_ok = False
    elif partial:
        identity_ok = None
    else:
        row_probe = {"r_base": r_base, "producing_h": producing, "attempts": attempts,
                     "speed_net": speed_net,
                     "p_potential_good": p_potential_good, "qlik_pass": qpass,
                     "loss_by": loss_by}
        # 容差隨尺縮放（模組 docstring [U-D] ★）：attrib 負殘差 clamp 留下的 k×1e-6 機時
        # 乘上尺就是 k×1e-6×r 顆；真正的來源不一致是整數顆級、照樣抓得到。
        ud_tol = max(UD_TOL, float(r_good_base) * 1e-5 + UD_TOL)
        identity_ok = bool(identity_error_ua(row_probe) <= UA_TOL
                           and identity_error_ud(row_probe) <= ud_tol)

    # note 優先序：時間 > 計數 > **跨窗零嘗試** > 基準首暖 > 報廢（scrap 另有獨立旗）
    #   ★ 首暖判準改看 **r_good_base**＝頁面實際在用的那把尺（只看 r_base 會出現
    #     「藥丸全空、卻沒有任何一句話說為什麼」＝2026-08-02 verify V3-MED 同型）。
    note = time_note or counts_note
    if note is None and window_mismatch:
        note = NOTE_COUNTS_WINDOW
    if note is None and qlik_mismatch:
        note = NOTE_QLIK_WINDOW          # 比 base_warming 具體＝先講「兩源不同窗」那件事
    if note is None and (r_good_base is None or r_base is None):
        note = NOTE_BASE_WARMING
    if note is None and scrap_deg:
        note = NOTE_NO_QLIK

    return {
        # ---- canonical（Part 0 §2 甲 §6 units 形）----
        "ord": o, "cls": cls, "mcl": mcl, "display": mq.QLIK_DISPLAY.get(o, cls),
        "attempts": attempts, "first_attempts": first, "rework_n": rework_n,
        "qlik_pass": qpass, "qlik_fail": qfail, "units_by_family": fam_split,
        "r_y": r_y, "r_base": r_base, "base_n_days": base_n,
        "speed_net": speed_net, "unit_bridge": unit_bridge,
        "identity_ok": identity_ok, "note": note,
        # ---- 提案 A/E canonical（日帳 v3 `units` 子塊；派生欄不落檔）----
        "good_h": good_h, "rework_h": rework_h, "scrap_h": scrap_h,
        "r_good": r_good, "r_good_base": r_good_base, "base_good_n_days": base_good_n,
        "slow_good": slow_good,
        # ★ 顯示層專用（**不落日帳、不進恆等式**）：「比基準快」只有在乾淨機時夠厚時才
        #   講得出口——薄生產日（good_h≈0）的 slow_good ＝ −qlik_pass，那是「本窗幾乎沒在
        #   跑、產出卻掛在這裡」的口徑差，講成「▲ 比基準快 N 顆」是假好消息。
        #   `slow_good`/`loss_by` 原值不動＝[U-D] 照舊精確平。
        "speed_gain_good": ((-slow_good) if (slow_good is not None and slow_good < 0
                                             and good_h is not None
                                             and good_h >= MIN_PROD_MH) else None),
        # ---- 顯示導出欄（Part 0 §2：scrap_n=qlik_fail、slow_est_n=round(max(0,net))）----
        "scrap_n": qfail, "slow_est_n": (int(round(speed_loss)) if speed_loss is not None
                                         else None),
        "speed_loss": speed_loss, "speed_gain": speed_gain,
        "scrap_src_degraded": scrap_deg,
        # ---- 提案 E 顯示欄（**頁面唯一尺**；量不到全 None＝顯「—」絕不 0）----
        "p_potential_good": p_potential_good, "good_conv": good_conv,
        "loss_by": loss_by, "loss_actionable": loss_actionable,
        "loss_unexplained": (loss_by or {}).get("unexplained"),
        "unexplained_h": (att_buckets or {}).get("unexplained"),
        "ranked": ranked_losses({"loss_by": loss_by}),      # 提案 H：單一排序 top-N
        # ---- 揭露/對帳欄（additive）----
        "rework_time_est_h": rework_est, "producing_h": producing, "total_mh": total_mh,
        "p_potential": p_potential, "downtime_conv": downtime_conv,
        "downtime_conv_by": downtime_by,
        "label": None,                      # build_board 依 labels 參數回填
    }


def identity_error_ua(row):
    """[U-A] 殘差 `|r_base×producing_h − (attempts + speed_net)|`；量不到 → 0.0（無帳可驗）。"""
    if not row:
        return 0.0
    rb, ph = row.get("r_base"), row.get("producing_h")
    at, sn = row.get("attempts"), row.get("speed_net")
    if rb is None or ph is None or at is None or sn is None:
        return 0.0
    return abs(float(rb) * float(ph) - (float(at) + float(sn)))


def identity_error_ud(row):
    """[U-D] 殘差 `|(P_good − good) − Σ 七桶|`（顆）；量不到 → 0.0（無帳可驗）。

    ★ 這是**提案 E 上線後唯一的頂層恆等式**——`selftest` 與 live 對帳都看它。
    """
    if not row:
        return 0.0
    lb = row.get("loss_by")
    if not isinstance(lb, dict) or row.get("p_potential_good") is None \
            or row.get("qlik_pass") is None:
        return 0.0
    if any(lb.get(b) is None for b in LOSS_BUCKETS):
        # ★ 這裡**不能回 0.0**：`loss_by` 是 dict、另兩腿也齊，卻少了一個桶＝結構破洞
        #   （鍵名寫錯／新增桶漏接線）。回 0.0 ＝「帳是平的」＝假平——恰恰是 `_build_row`
        #   註解裡那顆「靜默失真」的雷。回 inf ⇒ identity_ok 判 False、頁面標不可信。
        return float("inf")
    lhs = float(row["p_potential_good"]) - float(row["qlik_pass"])
    rhs = sum(float(lb[b]) for b in LOSS_BUCKETS)
    return abs(lhs - rhs)


def identity_error_uc(row):
    """[U-C]（**已退役**、留給舊 v2 檔對帳）殘差；量不到 → 0.0。

    現行 row 不再帶 `p_potential`/`downtime_conv` 以外的舊材料齊全組 ⇒ 多半回 0.0。
    新碼一律用 `identity_error_ud`。
    """
    if not row:
        return 0.0
    need = ("p_potential", "downtime_conv", "qlik_pass", "qlik_fail",
            "rework_n", "speed_net", "unit_bridge")
    if any(row.get(k) is None for k in need):
        return 0.0
    lhs = float(row["p_potential"]) - float(row["qlik_pass"])
    rhs = (float(row["downtime_conv"]) + float(row["rework_n"]) + float(row["speed_net"])
           + float(row["qlik_fail"]) + float(row["unit_bridge"]))
    return abs(lhs - rhs)


# display floor（提案 H）：換算後 < floor 顆的桶不進排序（live 校準＝改 env 不改碼）。
LOSS_CHIP_MIN = wattr._env_int("BOARD_LOSS_CHIP_MIN", 1)


def ranked_losses(row, *, max_n=3, min_pcs=None):
    """**提案 H**：七桶換算成顆**一起排序**取前 `max_n`（保障名額制已退役）。

    · 只收 `ACTIONABLE_BUCKETS`——`unexplained` 有自己的揭露子句（提案 C），
      混進排行就會變成「最大損失＝未解釋」這種不可行動、又一問就破的頭條。
    · 只收 **正值**：`slow` 為負＝比基準快（是好消息、不是損失）⇒ 不進排行，
      由 `row["slow_good"]` 自己在明細帶現形。
    · `loss_by` 缺（首暖/報廢腿缺/時間腿缺）→ `[]`（顯「—」，**絕不填 0**＝T6）。
    · 回 `[{"bucket","pcs","acct"("time"|"pcs"),"approx"}…]`；`approx` 恆 True
      ——**換算過的東西一律帶 ≈**（含 scrap/rework：它們的原始計數雖精確，
      乘上基準速率之後就是估算了）。原始精確計數仍在明細帶逐桶列出。
    """
    lb = (row or {}).get("loss_by")
    if not isinstance(lb, dict):
        return []
    floor = max(1, LOSS_CHIP_MIN if min_pcs is None else int(min_pcs))
    cand = []
    for b in ACTIONABLE_BUCKETS:
        v = lb.get(b)
        if v is None:
            continue
        # ★ NaN/inf 守衛：`NaN < floor` 恆為 False ⇒ 沒有這一行的話 NaN **通過 floor**、
        #   排序時又與任何數比較皆 False ⇒ 它會**停在原位登上榜首**、頁面顯「≈nan 顆」。
        #   來源＝上游 0/0 或壞值；量不到就該顯「—」，不是排第一。
        try:
            v = float(v)
        except Exception:
            continue
        if not math.isfinite(v) or v < floor:
            continue
        cand.append({"bucket": b, "pcs": float(v), "approx": True,
                     "acct": ("pcs" if b in UNIT_BUCKETS else "time")})
    cand.sort(key=lambda c: (-c["pcs"], LOSS_BUCKETS.index(c["bucket"])))
    return cand[:max_n]


# =============================================================================
# 4. build_board — 43 站顆數帳板（API pin）
# =============================================================================
def build_board(day, att_rows, qlik_grid, rework, base, *, source=None, labels=None,
                stations=mq.QLIK_STATIONS, pareto_docs=None, end_day=None,
                pareto_days=wattr.PARETO_DAYS, computed_at=None):
    """43 站顆數帳板（純函式）。rows **恆 43、ord 升冪、不藏站**（與 /dispatch 同紀律）。

    輸入（全部是「形」——本檔不做任何 I/O）
      · `day`        "YYYY-MM-DD"（昨日窗；與時間帳同窗）。
      · `att_rows`   `{cls: attrib ledger row}` 或 row list（`wattr.build_ledger` 形）。
                     缺站＝該站無時間源 → note `no_time_src`；`unmeasurable` 站沿用其 note
                     （zero_len/nosig/src_err/…＝四個 key 四句話、R5 降級講對原因）。
      · `qlik_grid`  `mes_qlik.build_grid` 回傳（或 canonical 包裝）；None/`unavailable`/
                     `diag.degraded` ⇒ 報廢欄整板降級（scrap None＋`scrap_src_degraded`）。
      · `rework`     `mes_rework.aggregate_attempts` 形（Part A §2.4）；None ⇒ 計數腿降級。
                     `diag.degraded`（mcl ABSENT）⇒ 站鍵退 cls 單鍵 join＋caveat。
      · `base`       `base_rates()` 回傳 `{cls: {"r_base","n_days"}}`；None ⇒ 全站首暖。
      · `labels`     `{cls: 友善名}`（BOSS 20 短名；B2c 由 `board_daily` 傳入）。
    回傳
      `{"schema","caliber","day","source","unavailable":None,"computed_at",
        "rows"(43), "hero", "units_line", "absorption", "absorbed_total",
        "pareto30"(顆數榜|None), "diag"}`
      · `hero`＝Part B §1.2 pin：`by_bucket{scrap,rework,slow}` / `top` / `worst`。
        slow 的線級值＝Σ `speed_loss`（**只加損失、不拿別站的 gain 抵銷**——這是損失排行，
        淨額會把 A 站的慢藏在 B 站的快裡）；gain 只活在各自的 row。
      · `units_line`＝日帳 v2 line 塊（Σ 可算站＋`absorbed_total` 獨立行＋`n_units_partial`）。
    """
    if isinstance(att_rows, dict):
        att_map = dict(att_rows)
    else:
        att_map = {r.get("cls"): r for r in (att_rows or ()) if isinstance(r, dict)}
    grid_rows, scrap_deg_global, absorb, absorbed_total = _grid_ctx(qlik_grid)
    rw_stations, rw_cls_only, rw_diag = _rework_ctx(rework)

    caveats = []
    if scrap_deg_global:
        caveats.append("qlik grid unavailable/degraded -> scrap column blanked for the "
                       "whole board (never substituted with raw mes_yield)")
    if rw_stations is None:
        caveats.append("rework source unavailable -> attempts/rework columns blanked")
    elif rw_cls_only:
        caveats.append("rework mcl degraded -> station join fell back to cls-only key")

    rows = []
    lab = labels or {}
    for o, cls, mcl in stations:
        att = att_map.get(cls)
        counts = _counts_of(rw_stations, cls, mcl)
        if rw_stations is not None and counts is None:
            caveats.append(f"{cls}: missing from rework aggregate (contract promises all "
                           f"43 keys) -> counts blanked for this row")
        qrow = (grid_rows or {}).get((cls, mcl))
        r = _build_row(o, cls, mcl, att, counts, qrow, scrap_deg_global,
                       (base or {}).get(cls), caveats)
        r["label"] = lab.get(cls)
        rows.append(r)

    # ---- hero / units_line（Σ 只吃量得到的站；全空 → None 絕不 0）-----------
    def _sum(key, cast=int):
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return None
        s = sum(vals)
        return cast(round(s)) if cast is int else s

    by_bucket = {"scrap": _sum("qlik_fail"), "rework": _sum("rework_n"),
                 "slow": _sum("speed_loss", cast=float)}
    top = sorted(((b, v) for b, v in by_bucket.items() if v),
                 key=lambda kv: (-kv[1], UNIT_BUCKETS.index(kv[0])))
    worst = None
    for r in rows:
        for b, v in (("scrap", r["qlik_fail"]), ("rework", r["rework_n"]),
                     ("slow", r["speed_loss"])):
            if v and (worst is None or v > worst["n"]):
                worst = {"cls": r["cls"], "bucket": b, "n": v}
    n_partial = sum(1 for r in rows if r["identity_ok"] is None)

    # ── 提案 E/H 線級：七桶顆數 Σ（只加可算站）＋單一排行＋每桶最痛的站 ──────────
    #    ★ **絕不用淨額**：slow 為負的站不得抵銷別站的慢（那會把 A 站的慢藏進 B 站的快）
    #      ⇒ 線級 slow ＝ Σ max(0, slow)；負值另計 `slow_gain_pcs` 獨立揭露。
    loss_line = {b: 0.0 for b in LOSS_BUCKETS}
    loss_worst = {b: None for b in LOSS_BUCKETS}
    slow_gain = 0.0
    n_ruled = 0
    # ★ 推定子句的**機時腿**必須與顆腿同一組列（2026-08-03 群 B fix ③）：
    #   `loss_unexplained` 只加得到「有尺」的站，若機時改用時間帳線級（＝全部**可測**站的 Σ）
    #   兩腿範圍不同 ⇒ 讀者把 n ÷ h 反算速率會對不上任何一站。時間帳全站的機時另有
    #   `attrib.by_bucket['unexplained']` 在時間帳區塊照顯示、資訊不丟。
    unexp_ruled_h = 0.0
    for r in rows:
        lb = r.get("loss_by")
        if not isinstance(lb, dict):
            continue
        n_ruled += 1
        _uh = r.get("unexplained_h")
        if _uh is not None:
            unexp_ruled_h += float(_uh)
        for b in LOSS_BUCKETS:
            v = lb.get(b)
            if v is None:
                continue
            if v < 0:
                # ★ 線級 gain 走與逐列同一道閘（`speed_gain_good`）：薄生產日的假 gain
                #   不得混進 hero 的「▲ 比基準快」大字（顯示層一致、恆等式不受影響）。
                if b == "slow" and r.get("speed_gain_good") is not None:
                    slow_gain += float(r["speed_gain_good"])
                continue
            loss_line[b] += float(v)
            w = loss_worst[b]
            if w is None or float(v) > w["pcs"]:
                loss_worst[b] = {"cls": r["cls"], "pcs": float(v)}
    if n_ruled:
        loss_line = {b: _r6(v) for b, v in loss_line.items()}
        loss_ranked = ranked_losses({"loss_by": loss_line}, max_n=len(ACTIONABLE_BUCKETS))
        loss_actionable = _r6(sum(loss_line[b] for b in ACTIONABLE_BUCKETS))
    else:
        loss_line = loss_ranked = loss_actionable = None
        loss_worst = {}
    hero = {"by_bucket": by_bucket, "top": top, "worst": worst,
            "n_rows": len(rows), "n_partial": n_partial,
            "n_identity_fail": sum(1 for r in rows if r["identity_ok"] is False),
            "approx_buckets": ("slow",),          # 顯示層對這些桶恆帶 ≈（舊嘗試軸）
            # ── 提案 E/H（頁面 hero 實際消費的就是這一組；單位全部＝顆、同一把尺）──
            "loss_by": loss_line, "loss_ranked": loss_ranked,
            "loss_actionable": loss_actionable,
            "loss_unexplained": (loss_line or {}).get("unexplained"),
            # additive（群 B fix ③）：與 `loss_unexplained` **同一組列**的未解釋機時。
            # 消費端＝`board_daily.line_sentence_ctx` / hero 推定行（勿再取 attrib 全站值）。
            "unexplained_ruled_h": (_r6(unexp_ruled_h) if n_ruled else None),
            "loss_worst": loss_worst,
            "slow_gain_pcs": (_r6(slow_gain) if slow_gain > 1e-9 else None),
            "n_ruled": n_ruled, "n_no_ruler": len(rows) - n_ruled}
    units_line = {"attempts": _sum("attempts"), "rework_n": _sum("rework_n"),
                  "scrap_n": _sum("qlik_fail"), "slow_est_n": _sum("speed_loss"),
                  "absorbed_total": absorbed_total, "n_units_partial": n_partial,
                  "n_rows": len(rows),
                  # 提案 E 線級（落日帳 v3 的 line 塊；顆數、單一尺）
                  "loss_actionable": loss_actionable,
                  "loss_unexplained": (loss_line or {}).get("unexplained"),
                  "n_ruled": n_ruled}

    diag = wattr._scrub({                          # §24：diag passthrough 一律剝人員鍵
        "n_no_time": sum(1 for r in rows if r["producing_h"] is None),
        "n_scrap_degraded": sum(1 for r in rows if r["scrap_src_degraded"]),
        "n_counts_missing": sum(1 for r in rows if r["attempts"] is None),
        "n_base_warming": sum(1 for r in rows if r["r_base"] is None),
        # 提案 E：頁面實際用的那把尺缺了幾站（首暖／報廢腿缺／時間腿缺都會落這裡）
        "n_good_base_warming": sum(1 for r in rows if r["r_good_base"] is None),
        "n_no_split": sum(1 for r in rows if r["good_h"] is None),
        "n_no_ruler": sum(1 for r in rows if r["loss_by"] is None),
        "grid_degraded": bool(scrap_deg_global),
        "rework_diag": rw_diag,
        "caveats": list(dict.fromkeys(caveats)),
    })

    return {
        "schema": SCHEMA_ID, "caliber": CALIBER_UNIT,
        "day": str(day) if day is not None else None, "source": source,
        "unavailable": None, "computed_at": computed_at,
        "rows": rows, "hero": hero, "units_line": units_line,
        "absorption": wattr._scrub(absorb), "absorbed_total": absorbed_total,
        "pareto30": (pareto30_units(pareto_docs, days=pareto_days, end_day=end_day)
                     if pareto_docs else None),
        "diag": diag,
    }


# =============================================================================
# 5. 日帳 v3（形狀＋parse **三收 v1/v2/v3**；檔案 I/O 在 board_data）
# =============================================================================
def _units_block(row):
    """unit row → station doc `units` 子塊（`_DOC_UNIT_KEYS` 逐鍵；浮點 _r6）。"""
    out = {}
    for k in _DOC_UNIT_KEYS:
        v = row.get(k)
        out[k] = _r6(v) if isinstance(v, float) else v
    return out


def ledger_doc(att_board, unit_board, *, day=None, source=None, wo_source_status=None,
               computed_at=None, grid_min=wattr.GRID_MIN, th=wattr.TH,
               rw_axis=None, rw_reason=None, families_status=None,
               products=None, products_status=None,
               rw_anchor=None, built_at=None, wo_open_at_build=None, cal_open_tail=None,
               roster_pad_unmeasurable=None):
    """attrib 板＋unit 板 → **v4 日帳文件**（v1 組裝權威仍是 `wattr.ledger_doc`、不重寫）。

    · station doc ＝ v1 形 ＋ `units` 子塊（依 cls join；unit 板缺該站 → `units=None`）。
      v4 的站列尾欄 `rw`／`families` 由 `wattr._DOC_STATION_KEYS` 白名單自動透傳。
    · unit 板有、attrib 板沒有的站 → **補一列時間欄全 None 的 station doc**（note
      `no_time_src`）——顆數史（r_y/scrap/rework）才進得了日帳（模組 docstring 有裁決）。
    · line 加 `units_line`；全文件過 `wattr._scrub`（§24 最後一道）。
    · **doc 級三個 v4 鍵一律由呼叫端傳入**（`rw_axis`／`rw_reason`／`families_status`）：
      本檔看得到站列有沒有 `rw`，但**看不到「為什麼沒有」**（日曆缺席？涵蓋不到？抓爆了？）
      ⇒ 預設 `None`＝「未知」，**絕不從站列反推**（反推出來的理由是編的，而 `rw_reason`
      的用途正是讓人知道該不該回補；編一個「ok」會讓那一天永遠沒人回頭看）。
    """
    doc = wattr.ledger_doc(att_board, day=day, source=source,
                           wo_source_status=wo_source_status, computed_at=computed_at,
                           grid_min=grid_min, th=th)
    doc["schema_ver"] = LEDGER_SCHEMA_VER
    doc["rw_axis"] = rw_axis
    doc["rw_reason"] = rw_reason
    doc["families_status"] = families_status
    ublocks = {r["cls"]: _units_block(r) for r in (unit_board or {}).get("rows") or ()
               if isinstance(r, dict) and r.get("cls")}
    have = set()
    for s in doc["stations"]:
        s["units"] = ublocks.get(s["cls"])
        have.add(s["cls"])
    for cls, blk in ublocks.items():
        if cls in have:
            continue
        extra = {k: None for k in wattr._DOC_STATION_KEYS}
        extra.update({"cls": cls, "unmeasurable": True, "note": NOTE_NO_TIME_SRC,
                      "units": blk})
        doc["stations"].append(extra)
    ul = (unit_board or {}).get("units_line")
    doc["line"]["units_line"] = ({k: (_r6(v) if isinstance(v, float) else v)
                                  for k, v in ul.items()} if isinstance(ul, dict) else None)
    # ★ v7（#103）：產品子樹**加在最外層**、與 `stations` 平行。呼叫端傳什麼就寫什麼
    #   （本檔不去抓 grid）；`products` 為 None／空 ⇒ **整個鍵不寫**＝v6 檔的逐位元形。
    #   ⚠ 子樹的內容**永不回寫 `doc["stations"]`**——那是 D1 的「兩樹永不相加」在寫端的
    #     樣子：Plover 站列在上面那個迴圈就寫完了，這裡只是掛上第二棵樹。
    blocks = {}
    for k, v in (products or {}).items():
        blk = parse_product_block(v)
        if blk is not None:
            blocks[str(k)] = blk
    doc[PRODUCTS_KEY] = blocks or None
    doc["products_status"] = products_status
    # ★ v8（#182-P2）：建帳證據五鍵＝呼叫端主張、原樣寫（None＝未知，不猜）。
    doc["rw_anchor"] = rw_anchor
    doc["built_at"] = built_at
    doc["wo_open_at_build"] = (sorted(str(x) for x in wo_open_at_build)
                               if isinstance(wo_open_at_build, (list, tuple, set)) else None)
    doc["cal_open_tail"] = dict(cal_open_tail) if isinstance(cal_open_tail, dict) else None
    doc["roster_pad_unmeasurable"] = (dict(roster_pad_unmeasurable)
                                      if isinstance(roster_pad_unmeasurable, dict) else None)
    return wattr._scrub(doc)


def carry_forward_products(doc, existing_doc):
    """新 doc ＋ 同一天既有檔 → 保留既有 `products` 腿的新 doc（純函式、不 mutate 輸入）。

    ★ 為什麼非有不可（＝`carry_forward_units` 的同型病）：同一天的日帳有**兩個寫者**
      （`app._build_attrib_day` 的時間腿、`board_data.get_unit_day` 的顆數腿），而
      **只有顆數腿抓得到 Kestrel grid**。時間腿每 15 分鐘就會在 degraded 日重建一次並
      落地 ⇒ 沒有這一支，已經算好的 Kestrel 子樹會被自己的時間腿抹掉。
    · 規則同 `carry_forward_units`：**只補洞、絕不覆蓋**——新 doc 有那個 product ⇒ 以新為準；
      沒有而既有檔有 ⇒ carry。`products_status` 同規則（新的是 None 才沿用舊的）。
    """
    if not isinstance(doc, dict):
        return doc
    old = parse_ledger_doc(existing_doc)
    if old is None:
        return doc
    old_blocks = parse_products(old.get(PRODUCTS_KEY)) or {}
    if not old_blocks:
        return doc
    out = dict(doc)
    new_blocks = dict(parse_products(doc.get(PRODUCTS_KEY)) or {})
    for k, blk in old_blocks.items():
        new_blocks.setdefault(k, blk)
    out[PRODUCTS_KEY] = new_blocks
    if out.get("products_status") is None:
        out["products_status"] = old.get("products_status")
    return out


def carry_forward_units(doc, existing_doc):
    """新 doc ＋**同一天**的既有檔 → 保留既有 `units` 腿的新 doc（純函式、不 mutate 輸入）。

    ★ 為什麼非有不可（2026-08-03 HIGH-1）：cmms degraded 的日子 attrib 每 15 分鐘就重建
      一次、並走 `persist_ledger(board)`（**不帶 unit_board**）⇒ `ledger_doc` 把整份檔的
      `units` 寫成 None，而 `should_overwrite` 因為 `wo != ok` 照樣放行 ⇒ **已經算好落地的
      顆數帳被自己毀掉**；而 unit 側的日期鍵快取此時是 immutable（板本身沒 degraded）、
      當天不會再回寫 ⇒ 這一天的顆數史永久消失、也永遠進不了 7d 基準樣本。
    · 規則＝**只補洞、絕不覆蓋**：新 doc 該站 `units` 為 None 且既有檔有值 → carry；
      新 doc 有值 → 一律以新值為準（今天算得更完整）。
    · 既有檔有、新 doc 整站缺（unit-only 站＝43−交集的無時間源站）→ **整列 carry**
      （那一列本來就是時間欄全 None 的形，照抄不會憑空生出時間帳）。
    · `line.units_line` 同規則。既有檔 parse 不出來（壞檔/未知版）→ 原樣返還（不猜）。
    """
    if not isinstance(doc, dict):
        return doc
    old = parse_ledger_doc(existing_doc)
    if old is None:
        return doc
    old_st = {s.get("cls"): s for s in old.get("stations") or ()
              if isinstance(s, dict) and s.get("cls") and s.get("units")}
    old_line = (old.get("line") or {}).get("units_line")
    if not old_st and old_line is None:
        return doc
    out = dict(doc)
    out["line"] = dict(doc.get("line") or {})
    out["stations"] = [dict(s) for s in doc.get("stations") or ()
                       if isinstance(s, dict)]
    seen = set()
    for s in out["stations"]:
        seen.add(s.get("cls"))
        if s.get("units") is None:
            src = old_st.get(s.get("cls"))
            if src is not None:
                s["units"] = dict(src["units"])
    for cls, src in old_st.items():
        if cls not in seen:
            out["stations"].append(dict(src))
    if out["line"].get("units_line") is None and old_line is not None:
        out["line"]["units_line"] = (dict(old_line) if isinstance(old_line, dict)
                                     else old_line)
    return out


def parse_ledger_doc(doc):
    """日帳文件 → 驗過的 dict；**四收 v1/v2/v3/v4**（Part 0 §6 ＋ Part C §4 ＋ M3 v4）。

    · v1：委給 `wattr.parse_ledger_doc`（單一權威）→ 每站補 `units=None`、line 補
      `units_line=None`（UI「—」不炸）；`schema_ver` 保留 v1（`should_overwrite` 要看）。
    · v2/v3/v4/v5：station 白名單＝v1 鍵＋`units`（`units` 內再過 `_DOC_UNIT_KEYS` 白名單）。
      **v4 檔的站列 `rw` 沒有 `avail_down_sec`／`down_by_state`／`eids`**（2026-09-04 換尺
      之前寫的）——`rw` 整塊原樣透傳、**本檔不補、不猜**；「那把尺不在這份檔裡」由
      `board_monthly` 自己判（`n_days_rw_legacy`），parse 層一個字都不許反推。
      **v2 檔的 v3 新欄回 None**——不是壞檔：`r_good_of_units` 會從存量欄現場推導，
      所以換尺當天 v2 歷史照樣進基準（換尺不必回補）。
      **v3 檔的站列 `rw`／`families` 回 None、doc 級三鍵回 None**（同一條紀律：
      「這份檔沒有那一軸」≠「那一軸是空的」；月級靠 `n_days_rw` 誠實揭露涵蓋）。
      `schema_ver` **原樣保留**（v3 不得被冒充成 v4，否則 `should_overwrite` 判不出來
      該重寫、頁面會拿舊欄跑新軸）。
    · 其他 schema_ver／壞形 → None（絕不猜）。
    """
    if not isinstance(doc, dict):
        return None
    ver = doc.get("schema_ver")
    if ver == LEDGER_SCHEMA_V1:
        out = wattr.parse_ledger_doc(doc)
        if out is None:
            return None
        for s in out["stations"]:
            s["units"] = None
        out["line"].setdefault("units_line", None)
        for k in _DOC_V4_KEYS + _DOC_V7_KEYS + _DOC_V8_KEYS:
            out.setdefault(k, None)
        out.setdefault(PRODUCTS_KEY, None)
        return out
    if ver not in (LEDGER_SCHEMA_V2, LEDGER_SCHEMA_V3, LEDGER_SCHEMA_V4,
                   LEDGER_SCHEMA_V5, LEDGER_SCHEMA_V6, LEDGER_SCHEMA_V7, LEDGER_SCHEMA_VER):
        return None
    day = doc.get("day")
    if not isinstance(day, str) or not day:
        return None
    out = {"schema_ver": ver, "day": day,          # ★ 保留原版號（should_overwrite 要看）
           "source": doc.get("source"),
           "wo_source_status": doc.get("wo_source_status"),
           "computed_at": doc.get("computed_at"),
           "grid_min": doc.get("grid_min"),
           "thresholds": dict(doc.get("thresholds") or {}),
           "line": dict(doc.get("line") or {}),
           "stations": []}
    for k in _DOC_V4_KEYS + _DOC_V7_KEYS + _DOC_V8_KEYS:   # 舊檔缺鍵 ⇒ None（未知，不是 False）
        out[k] = doc.get(k)
    # ★ v7（#103）：產品子樹過白名單透傳；v6 以前的檔 ⇒ `None`＝「Kestrel 無帳」（顯「—」）。
    out[PRODUCTS_KEY] = parse_products(doc.get(PRODUCTS_KEY))
    out["line"].setdefault("units_line", None)
    for s in doc.get("stations") or ():
        if not (isinstance(s, dict) and s.get("cls")):
            continue
        st = {k: s.get(k) for k in wattr._DOC_STATION_KEYS}
        u = s.get("units")
        st["units"] = ({k: u.get(k) for k in _DOC_UNIT_KEYS}
                       if isinstance(u, dict) else None)
        out["stations"].append(st)
    return wattr._scrub(out)


def parse_ledger_docs(docs):
    """`[doc…]` → `(parsed, n_bad)`（v1/v2 混讀；壞檔只計數不炸）。"""
    good, bad = [], 0
    for d in docs or ():
        p = parse_ledger_doc(d)
        if p is None:
            bad += 1
        else:
            good.append(p)
    return good, bad


def to_v1_doc(doc):
    """v2/v3/v4 文件 → v1 視圖（剝 `units`/`units_line`／v4 的 doc 級三鍵、`schema_ver` 降回 v1）。

    用途＝把 v2 檔餵給 `wattr.pareto30`（時間榜聚合權威**只有一份**、不在本檔重寫）。
    v1 輸入原樣返還；壞形 → None。純函式、不 mutate 輸入。
    """
    p = parse_ledger_doc(doc)
    if p is None:
        return None
    if p.get("schema_ver") == LEDGER_SCHEMA_V1:
        return doc
    # ★ v7：`products` **一起剝掉**（D1「兩樹永不相加」在降視圖這一層的樣子——時間榜
    #   聚合權威 `wattr.pareto30` 一個 Kestrel 的位元都不該看得到）。
    out = {k: v for k, v in p.items()
           if k != "stations" and k not in _DOC_V4_KEYS + _DOC_V7_KEYS + _DOC_V8_KEYS
           and k != PRODUCTS_KEY}
    out["schema_ver"] = LEDGER_SCHEMA_V1
    out["line"] = {k: v for k, v in p["line"].items() if k != "units_line"}
    out["stations"] = [{k: s.get(k) for k in wattr._DOC_STATION_KEYS}
                       for s in p["stations"]]
    return out


def pareto30_time(docs, *, days=wattr.PARETO_DAYS, end_day=None):
    """時間榜（機台小時）＝`wattr.pareto30` 原封；只是先把 v2 檔降成 v1 視圖再餵。"""
    v1 = [d for d in (to_v1_doc(x) for x in docs or ()) if d is not None]
    # 保留壞檔計數語意：wattr.pareto30 自己會對餵進去的清單計 n_bad，
    # 這裡把 parse 不過的檔也補進去（以原樣傳入、讓它算成 bad）。
    bad = [x for x in (docs or ()) if parse_ledger_doc(x) is None]
    return wattr.pareto30(v1 + bad, days=days, end_day=end_day)


def should_persist(source):
    """C6 落地條件（沿用 v1 規則＝`source=='live'` only；權威在 wattr）。"""
    return wattr.should_persist(source)


def rw_roster_unknown(doc):
    """這份日帳的 rw 腿有沒有站是「**C-RW18 名冊補列證據未知**」（`roster_pad is None`）？

       ★ **單一出處**（2026-09-05 事故的修法）：`_backfill_ledger.doc_facts` 從本支
         `import` 重用，**不再自己寫一份**。原本兩邊各寫一份同義判準，結果只有回補的
         **計畫**那一份存在——`should_overwrite` 沒有這一條 ⇒ 計畫判 `retry×34`、
         落地卻在 `persist_ledger` 回 `already_ok`，34 天算了 1.9 小時**一天都沒落地**
         （只有「整份缺 units」那一天因為兩邊剛好都有判準而寫成功）。
         判準與覆寫閘住在同一個模組、由同一支函式回答，是那個事故唯一的結構性解。

       判準（逐位元同 `_backfill_ledger.doc_facts.rw_roster_unknown` 的原文）：
       **任一站**的 `rw` 是 dict ∧ `avail_down_sec is not None`（＝2026-09-04 換尺後的
       新尺 rw 腿）∧ `rw.roster_pad is None`（含**缺鍵**）。

       · `None`（含缺鍵）＝「2026-09-05 之前寫出的日帳**不知道**有沒有走名冊補列」
         ——**不把它偷當 False**：`False` 是本次建構的誠實降級（寬窗清單當時查不到），
         `None` 是沒有人主張過。兩種證據不得混掉（`board_monthly._rw_roster_pad_of_doc`
         同一條紀律）。
       · 沒有新尺 rw 的站（v4／舊尺／`rw` 是 None）**不觸發**——那種檔另有 `rw_upgrade`
         ／`v5_upgrade` 兩型先處理，這一條專門撿「其餘都完整、只缺新的誠實欄」的 legacy v5。
       · 壞形／None ⇒ False（證不出來就不主張；本支的下游是「放行覆寫」，
         誤判成 True 只是多重算一次，但誤判成 True 的**全域**版本會讓每份好檔天天重寫
         ⇒ 判準必須貼著資料、不猜）。
    """
    if not isinstance(doc, dict):
        return False
    for s in (doc.get("stations") or ()):
        if not isinstance(s, dict):
            continue
        rw = s.get("rw")
        if (isinstance(rw, dict) and rw.get("avail_down_sec") is not None
                and rw.get("roster_pad") is None):
            return True
    return False


# =============================================================================
# 5b. #182-P2 建帳證據旗（**單一出處**：`should_overwrite`／`_backfill_ledger.plan_day`／
#     自動重建鉤／凍結閘全部問這幾支；`board_monthly` 刻意不 import 本檔，它的本地
#     讀取器由本檔 selftest cross-pin 對齊＝[[backfill-plan-vs-write-gate]] 的教訓）
# =============================================================================
def rw_anchor_legacy(doc):
    """這份日帳**沒有證據**證明它是生產日 08:00 錨建的（缺 `rw_anchor` 或 ≠ "08:00"）？

       ＝#182 題一（回補 runner 日曆日窗、分母≈24h）的機械辨識。v7 以前的檔全中——那正是
       要的：修錨前的回補檔與修錨後的好檔在檔面上一模一樣，只能以「沒有證據」論。
       壞形／None ⇒ False（證不出來就不主張；它的下游是「放行覆寫」與「拒凍」）。"""
    if not isinstance(doc, dict):
        return False
    return doc.get("rw_anchor") != RW_ANCHOR


def cal_open_tail_pending(doc):
    """建帳當下日曆尾端有未閉合沉默與本日重疊、且**尚未**查明結果？（有 `cal_open_tail`
       dict ∧ 沒有 `resolved`）。"""
    if not isinstance(doc, dict):
        return False
    tail = doc.get("cal_open_tail")
    return isinstance(tail, dict) and not tail.get("resolved")


def wo_open_flags(doc):
    """`wo_open_at_build` 的工單號清單（壞形／None／舊檔 ⇒ `[]`）。"""
    if not isinstance(doc, dict):
        return []
    v = doc.get("wo_open_at_build")
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else []


def rw_retry_reason(doc):
    """這份日帳「還不是最終事實」的 #182-P2 理由 → 三個字面值之一或 `None`。

       優先序（先講根本的）：`rw_anchor_legacy`（整份分母的錨不可考）＞ `rw_cal_open_tail`
       （本日尾巴的 closure 還沒定形＝分母）＞ `rw_wo_open_at_build`（建帳時工單未結＝分子）。
       這是 `should_overwrite` 放行覆寫、`plan_day` 判 `retry`、凍結閘數天數的**同一支**。"""
    if not isinstance(doc, dict):
        return None
    if rw_anchor_legacy(doc):
        return RETRY_RW_ANCHOR_LEGACY
    if cal_open_tail_pending(doc):
        return RETRY_RW_CAL_OPEN_TAIL
    if wo_open_flags(doc):
        return RETRY_RW_WO_OPEN_AT_BUILD
    return None


def wo_open_at_build(wos_by_eid, win_start, win_end, *, now, eids=None, long_days=None):
    """建帳當下**還開著**、且在本窗 `[win_start, win_end)` 有 countable 段的工單號（排序 list）。

       判準（單一出處；落旗與「旗解了沒」都靠同一組定義）：
         · 工單活躍窗 `w8.wo_active_window(rec, now)` 的 end 是 None（ongoing）；
         · `wem.wo_segments([rec], ws, we)` 有 `countable` 且長度 > 0 的段；
         · `eids` 給了 ⇒ 只看這些機台（＝進得了分母的機台；名冊外的單不影響本帳）；
         · **建帳時已是長掛單（活躍跨度 > `long_days`）者不列**——C-DT6「長掛單永遠納入」
           是口徑、不是時點問題：它結不結案，這一天的數字都照它算；列了反而讓那一天永遠
           帶旗、永遠擋凍結（它可能永不結）。門檻唯一出處＝`_wo_ledger.LONG_WO_DAYS`。
       `wos_by_eid` 是 `wem.group_wos_by_eid` 的形（`{eid: [rec…]}`）。純函式（`now` 注入）。"""
    if long_days is None:
        from _wo_ledger import LONG_WO_DAYS as long_days     # 唯一出處（lazy：零 import 成本）
    keep = set(eids) if eids is not None else None
    out = set()
    for eid, recs in (wos_by_eid or {}).items():
        if keep is not None and eid not in keep:
            continue
        for rec in recs or ():
            if not isinstance(rec, dict):
                continue
            no = rec.get("work_order_no")
            if no is None:
                continue
            win = wattr.w8.wo_active_window(rec, now=now)
            if win is None or win[1] is not None:
                continue                                      # 無有效窗／已結＝不是「還開著」
            a0 = wattr.wem._coerce_dt(win[0])
            if a0 is not None and (now - a0).total_seconds() / 86400.0 > float(long_days):
                continue                                      # 長掛單（C-DT6）＝口徑、不列
            segs = wattr.wem.wo_segments([rec], win_start, win_end)
            if any(s.get("countable", True) and s.get("start") is not None
                   and s.get("end") is not None and s["end"] > s["start"] for s in segs):
                out.add(str(no))
    return sorted(out, key=lambda x: (len(x), x))


def ledger_rw_eids(rows):
    """站列（日帳 `doc["stations"]`／板 `board["rows"]`）→ 各站 `rw.eids` 的聯集（set），或 `None`。

       ★ 2026-10-02（`_FOLLOWUPS-20261002.md` ②；WO 35456／37100 型誤旗）：`wo_open_at_build` 的
         **範圍唯一出處**。`rw.eids`＝真正進了某站分母的機台（`board_attrib.build_ledger` 的
         `fleet`）＝這一天的數字（planned／停機／A／U／OEE／停機當量）只會被這些機台的工單動到。
         在 `KUN_ROSTER` 裡、卻不在任何站 `rw.eids` 的機台（例：Wire pull tester EID-38790）
         的工單**對該日帳貢獻 0**——落旗只會讓凍結閘被假旗稀釋。
       `None`＝**沒有任何一列帶 `rw` dict 且有 `eids` list**（24h 軸日／v4 以前舊檔／壞形）
       ＝不知道範圍 ⇒ 呼叫端不得收窄（保守：照舊全列）。空 set＝有 rw 列但每站都零機台。純函式。"""
    seen, out = False, set()
    for r in rows or ():
        rw = r.get("rw") if isinstance(r, dict) else None
        eids = rw.get("eids") if isinstance(rw, dict) else None
        if isinstance(eids, (list, tuple)):
            seen = True
            out.update(str(e) for e in eids if e)
    return out if seen else None


def narrow_wo_open_flags(flags, rw_eids, wo_eid):
    """**讀端**收窄：已落地日帳的舊 `wo_open_at_build` 旗 → 只留「機台在該日站 `rw.eids` 內」的單。

       與寫端（`_build_attrib_day` 用 `ledger_rw_eids(board rows)` 當 `wo_open_at_build(eids=)`）
       同一條範圍定義；舊檔不重寫，讀的時候收窄。保守三條（不知道就不丟旗）：
         · `rw_eids is None`（該帳沒有 rw 範圍）⇒ 原樣；
         · 工單查無機台（`wo_eid` 沒這張／值空）⇒ 留；
         · 機台在 `rw_eids` 內 ⇒ 留；**只有「確知機台、且確知不在範圍內」才丟**。
       `wo_eid`＝`{wo_no: eid}`（`board_data.wo_meta_eids`，sqlite ro）。保序、純函式。"""
    flags = [str(x) for x in flags or ()]
    if rw_eids is None:
        return flags
    keep = set(rw_eids)
    out = []
    for w in flags:
        e = (wo_eid or {}).get(w)
        if not e or str(e) in keep:
            out.append(w)
    return out


def wo_open_resolution(flags, meta):
    """旗上工單解了沒 → `(resolved: bool, pending: [wo…])`；純函式。

       `meta`＝`{wo_no: {"closed": bool, "long": bool}}`（`board_data.wo_meta_status` 讀
       `_state/wo_ledger.sqlite`（ro）的 `wo_meta`）。**解了**＝每一張都「已結案」或「已轉
       長掛旗」（SPEC-DOWNTIME §14 ㉘ ②）。查無 meta 的單＝**未解**（不知道就不主張；
       凍結閘會點名它，人明示 override 才放行）。空旗 ⇒ `(False, [])`（沒有東西要解）。"""
    flags = [str(x) for x in flags or ()]
    if not flags:
        return (False, [])
    pend = [w for w in flags
            if not (isinstance((meta or {}).get(w), dict)
                    and ((meta[w].get("closed") is True) or (meta[w].get("long") is True)))]
    return (not pend, pend)


def should_overwrite(existing_doc):
    """v4 覆寫政策（SPEC §6 ＋ Part C §4 ＋ M3）＝「`wo_source_status != ok` ∨ doc 非 v4
    ∨ **v4 但 `rw_axis is not True`** ∨ `units` 缺/計數腿有洞 ∨ 報廢源**整板**降級
    ∨ 任一站時間源當時查不到（`src_err`）」。

    · `units` 缺＝**整份文件沒有任何一站帶 units 塊**（v1→v4 升級重算的觸發）。
    · v2/v3 檔亦回 True＝「昨天」那份會被升上來（把 `r_good`／`rw` 等欄真的寫下來）；
      **更舊的天不回補、也不需要**——`r_good_of_units` 讀得出來、`rw` 靠回補工具補。
    · **`rw_axis is not True`（M3；2026-09-04 由 `is False` 放寬）**：那一天的日帳沒有站在
      應生產時段軸上——**可恢復的降級**（日曆 08:05 才落地，暖機 08:0x 打 `/daily` 就會凍出
      一份 24h 軸的檔）。日曆到位後重建應該能升級，所以覆寫必須放行——**與 `src_err` 完全
      同型**：那不是事實、是「當時還沒有」。
      ★ **`None` 與 `False` 同視**（2026-09-04）：舊版刻意不放行 `None`，理由是「None ＝
      沒人主張過（舊檔、或呼叫端沒傳），一把抓會讓每一份既有好檔每天被重寫一次」。
      那條理由在 `91f5634`（`board_data._doc_v4_meta` 的 carry-forward）之後**消失**了
      ——現在**每一次落地都會帶著主張**：`app._build_attrib_day` 主張三鍵，顆數腿
      （`get_unit_day`，不帶 `rw_meta=`）則逐鍵沿用既有檔 ⇒ 好檔的 `rw_axis` 恆是 `True`、
      不會再被抹回 `None`。反過來說，`91f5634` **之前**寫出的 v4 檔全是「doc 級 `None` ＋
      站列有 `rw`」——`board_monthly.coverage.n_days_rw` 數不到它們（月報自動凍結永遠
      `rw_incomplete`），而舊規則又不放行覆寫 ⇒ 那些日子**永久**卡死、只能靠人跑
      `_backfill_ledger --force`。放寬之後：昨天那份下一次 live 重建就自癒，更舊的天
      `_backfill_ledger` 也判得動（不再 `skip_locked`）。
      代價＝「主張過 `None` 的檔」每次重建會被重寫一次——那正是我們**要**它重寫。
    · **`roster_pad is None`（2026-09-05 事故；`rw_roster_unknown`）**：新尺 rw 腿但沒有
      C-RW18 名冊補列的證據＝**分母存疑**（那一天的站有沒有補齊名冊機台不知道）。
      這一條與 `_backfill_ledger.plan_day` 的 `retry` **同一支函式**——上一版只有計畫
      那一邊有，於是「計畫要重算、落地不放行」，34 天 `already_ok` 全部白跑。
      判在 `src_err` 之後、`units` 缺之前：先講「數字不是事實」，再講「分母的證據沒留」，
      最後才是「顆數帳缺」。
    · **`src_err`（2026-08-03 HIGH-2）**：那一站的時間欄不是事實、是「當時讀不到」
      （預算耗盡／timeline 查詢失敗）。不放行重寫的話，一次預算耗盡就把「那站昨天
      沒有任何時間帳」凍結成永久事實、且該日的 30d Pareto 從此少一站。
    · **計數腿有洞（LOW A-4）**：`note == no_rework_src` 或 `attempts is None` 的站。
      整批缺席的板呼叫端本來就不落地（`get_unit_day`），但**部分**缺口會靜靜落檔
      ——與報廢腿 degraded 同型的可恢復降級，覆寫判準必須對稱。
    · **報廢源降級改看板級（LOW B-1）**：舊版 `any(scrap_src_degraded)` ⇒ demo 那種
      「單站 Qlik 缺列」（結構性、明天也不會變）會讓整日**永久**可覆寫＝每次重建都重寫檔。
      `all(...)` 才是「整板 grid degraded」的簽名（`_grid_ctx` 降級時逐列皆真）。
    · 只對「昨天」做升級重算、更舊天不回補——**那是呼叫端（B2c）的排程紀律**，
      本函式只回答「這份檔可不可以被覆寫」。
    · None／無 schema_ver 的壞檔 → True（寫新檔/重寫）；**帶未知 schema_ver 的檔 → False**
      （更新版的檔＝別的（新）版本寫的，舊碼**永不 clobber**；同 `wattr.should_overwrite`）。
    """
    if not existing_doc:
        return True
    doc = parse_ledger_doc(existing_doc)
    if doc is None:
        ver = (existing_doc.get("schema_ver")
               if isinstance(existing_doc, dict) else None)
        if isinstance(ver, str) and ver.strip():
            return False                  # 未知/更新版：讀不懂就不要動它
        return True                       # 無版號＝真壞檔 → 重寫
    if str(doc.get("wo_source_status") or "").lower() != "ok":
        return True
    if doc.get("schema_ver") != LEDGER_SCHEMA_VER:
        return True
    if doc.get("rw_axis") is not True:        # M3：沒主張 rw 軸＝可恢復降級（同 src_err）
        return True
    if any(s.get("note") == wattr.NOTE_SRC_ERR for s in doc["stations"]):
        return True
    if rw_roster_unknown(doc):          # #64：新尺 rw 但名冊補列證據未知（roster_pad=None）
        return True                     #      ＝與 `_backfill_ledger.plan_day` 同一支
    if family_split_unknown(doc):       # #98：量得到報廢腿卻沒有家族拆分 ⇒ 該回補
        return True                     #      ＝同上，判準本體只有一份
    if rw_retry_reason(doc):            # #182-P2：錨不可考／日曆尾巴未定形／建帳時工單未結
        return True                     #      ＝與 plan_day／凍結閘同一支（放行≠觸發；何時重建由鉤決定）
    units = [s.get("units") for s in doc["stations"] if s.get("units")]
    if not units:
        return True
    if all(u.get("scrap_src_degraded") for u in units):
        return True
    if any(u.get("attempts") is None or u.get("note") == NOTE_NO_REWORK_SRC
           for u in units):
        return True
    return False


# =============================================================================
# 6. 顆數榜（30d Pareto 面板 B；單位＝顆、絕不與時間榜混排＝Part B §6 反模式）
# =============================================================================
REFUSE_SRC_ERR_ALL = "refused:src_err_all_over_measurable"   # `persist_ledger` 的拒絕理由
REFUSE_DEGRADED_OVER_OK = "refused:wo_degraded_over_ok"          # R48（2026-09-30）


def any_measurable(doc):
    """這份日帳文件**有沒有任何一站量得到**（站列 `unmeasurable` 為假）；純函式、零 I/O。

       ⚠ **缺 `unmeasurable` 鍵的站算「可測」＝保守**：本函式的下游是「拒絕覆寫」，
       把好檔誤判成壞檔會讓那一天永遠寫不進去。
    """
    sts = (doc or {}).get("stations") if isinstance(doc, dict) else None
    return any(not s.get("unmeasurable") for s in (sts or ()) if isinstance(s, dict))


def src_err_all(doc):
    """這份日帳文件是不是「**整天的時間軸都不是事實**」：有站 `src_err` ∧ 一站都不可測。

       ＝時間源當時整批讀不到（SQL 連線失敗／預算耗盡）那一天的形。與「整天結構性
       不可測」（全站 `no_fleet`／`zero_len`，沒有任何 `src_err`）**刻意分開**：後者是
       那一天真的量不到，重跑一百次也一樣；前者重跑就有了。
    """
    sts = (doc or {}).get("stations") if isinstance(doc, dict) else None
    sts = [s for s in (sts or ()) if isinstance(s, dict)]
    if not sts:
        return False
    return (any(s.get("note") == wattr.NOTE_SRC_ERR for s in sts)
            and not any_measurable(doc))


def degraded_would_clobber_ok(old_doc, new_doc):
    """R48（2026-09-30；Fable 代裁、Chris 可否決）：**工單源降級的新帳不得蓋工單源 ok 的舊帳**
       → 拒絕理由（`REFUSE_DEGRADED_OVER_OK`）／`None`＝不擋；純函式、零 I/O。

       規則（唯一一條）：舊檔是 dict 且 `wo_source_status == "ok"`（大小寫不拘）∧ 新帳
       `wo_source_status != "ok"` ⇒ 拒絕。其餘組合（無舊檔／壞舊檔、降級蓋降級、ok 蓋降級、
       ok 蓋 ok）一律放行。
       · 取值集合（grep 賦值處）＝`"ok"`／`"degraded"`（`ledger_doc`、`board_data` 的
         `wo_ok` 分支）、`"demo"`（`board_attrib` demo 板）、`None`（壞板／未主張）；
         **只有 `"ok"` 算好帳**，其餘一律視為降級（未知值不得被當成事實去蓋掉事實）。
       · 事故（2026-09-30）：cmms 不通（`wo=error:unreachable`）時服務自動重建，舊檔因帶
         `rw_retry_reason`（建帳時有未結工單）被 `should_overwrite` 放行，而第二道閘只擋
         「整天零可測」⇒ 09-28、09-29 的 `wo=live` 好帳被 `degraded` 帳覆寫、無備份。
       · **唯一出處**：`refuse_overwrite`（`persist_ledger` 的第二道）與
         `_backfill_ledger` 的 `--force` 還原（`force_restore_reason`）都呼叫這一支
         （[[backfill-plan-vs-write-gate]]：判準不得各判一次）。
       · 代價：cmms 長期不通時，ok 舊帳會停在舊的 MES 資料上、不再更新（直到 cmms 恢復、
         下一次重建以 ok 帳落地）。"""
    if not isinstance(old_doc, dict):
        return None
    if str(old_doc.get("wo_source_status") or "").lower() != "ok":
        return None
    nd = new_doc if isinstance(new_doc, dict) else {}
    if str(nd.get("wo_source_status") or "").lower() == "ok":
        return None
    return REFUSE_DEGRADED_OVER_OK


def refuse_overwrite(new_doc, existing_doc):
    """覆寫政策的**第二道**（2026-09-04）：**壞帳不得蓋好帳** → 拒絕理由／`None`＝可以寫。

    `should_overwrite` 問的是「**既有**這份檔可不可以被動」，本函式問的是「**要寫進去的
    這份**配不配蓋掉它」——兩個問題不同，2026-08-11 的事故就活在中間那道縫裡：
      那天 28 站 timeline 全部 SQL 連線失敗 ⇒ 新板每一站 `src_err`／`unmeasurable`、
      時間欄全 `None`，而既有檔是一份 21 站可測的**好帳**。`should_overwrite` 看既有檔
      （wo ok、v4、rw_axis…）當然是可以動的 ⇒ 好帳被一份「什麼都沒量到」的檔蓋掉，
      磁碟上只剩 `.bak-`（`read_ledger_docs` 不認那個檔名）。

    判準（**兩條**，都刻意窄）：①新 doc `src_err_all` ∧ 既有檔**有可測站** ⇒ 拒絕；
      ②**R48（2026-09-30）**：既有檔 `wo_source_status == ok` ∧ 新 doc 不是 ⇒ 拒絕
      （`degraded_would_clobber_ok`；兩條都中時回①的理由）。以下幾點講的是①：
      · 既有檔本來就零可測（或無檔／壞檔）⇒ **放行**：新檔不比舊檔差，而且拒絕會讓
        那一天連 `wo_h`／`units`／`rw` 這些仍然有效的欄位都寫不進去。
      · 新 doc 只是**部分**站 `src_err` ⇒ **放行**：那是日常（預算耗盡跳幾站），
        由 `should_overwrite` 的 `src_err` 條保證它明天會被重寫。
      · 拒絕時**既有檔與它的 `.bak-` 一個位元都不動**（呼叫端只是 return，不刪不改名）。
    ⚠ 這道閘擋不了 `_backfill_ledger.py --force`——那條路徑**先把舊檔改名移走**才算，
      落地時根本沒有「既有檔」可比。那一層的還原在 `_backfill_ledger` 的 `_restore`
      （runner 自己比對備份與落地檔），兩邊的判準是同一句話。
    """
    if src_err_all(new_doc) and any_measurable(existing_doc):
        return REFUSE_SRC_ERR_ALL
    # R48（2026-09-30）：工單源降級的新帳不得蓋 ok 舊帳（判準本體＝`degraded_would_clobber_ok`）
    return degraded_would_clobber_ok(existing_doc, new_doc)


def pareto30_units(docs, *, days=wattr.PARETO_DAYS, end_day=None):
    """日帳文件 → 顆數榜：站 × 三桶（scrap/rework/slow）＋全線佔比。

    · **只讀日帳、不重算**；v1 檔沒有 units ⇒ 只進 `n_days`、不進 `n_days_units`
      ——兩個涵蓋數必須分開揭露（§6：顆數榜涵蓋天數可比時間榜少）。
    · slow ＝ Σ per-day `max(0, speed_net)`（估算 ⇒ `approx_buckets` 標示、顯示恆帶 ≈）；
      scrap/rework ＝ 精確計數。桶間單位同為「顆」可以相加；**與時間榜永不相加**。
    · 窗內完全沒有帶 units 的日子 → None（面板顯 `dl_pareto_pcs_none`）。
    """
    parsed, n_bad = parse_ledger_docs(docs)
    if not parsed:
        return None
    end = wattr._dt(str(end_day)) if end_day else None
    if end is None:
        # `or datetime.min`＝壞 day 字串（手改/半寫檔）不得讓 max() 拿 None 去比較而 TypeError
        # ——與 `wattr.pareto30` 同一道守衛（verify V1-LOW）。
        end = max((wattr._dt(d["day"]) or datetime.min) for d in parsed)
    want = {(end - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(int(days))}
    used = [d for d in parsed if d["day"] in want]
    if not used:
        return None
    with_units = [d for d in used if any(s.get("units") for s in d["stations"])]
    if not with_units:
        return None

    line = {b: 0.0 for b in UNIT_BUCKETS}
    per = {}
    # ★ 舊欄（**按日**計）：一天內只要**任一站**是新尺就整天算「新尺日」⇒ 混尺程度被
    #   系統性低估（換尺當天 1 站升級、42 站還是舊尺，這個數字卻已經是 1/1）。
    #   新欄（**按站×日**計、additive）才是真正的混用比例；舊欄留著不動＝模板下一波才切。
    n_days_slow_good = sum(1 for d in with_units
                           if any((s.get("units") or {}).get("slow_good") is not None
                                  for s in d["stations"]))
    n_sd = n_sd_good = 0
    for d in with_units:
        for s in d["stations"]:
            u = s.get("units")
            if not u:
                continue
            scrap = u.get("qlik_fail")
            rework = u.get("rework_n")
            # 提案 E：慢速優先讀 v3 的 `slow_good`（頁面那把尺）、退回 v2 的 `speed_net`
            # （嘗試軸）。兩者同單位「顆」但**不同尺** ⇒ 混用天數要揭露（`n_days_slow_good`）
            # ——這是 30d 面板的已知取捨：換尺當天不可能有 30 天的 v3 檔，硬只收 v3 會讓
            # 慢速在榜上憑空消失（比揭露的混用更糟）。
            sn = u.get("slow_good")
            if sn is None:
                sn = u.get("speed_net")
            slow = max(0.0, float(sn)) if sn is not None else None
            if scrap is None and rework is None and slow is None:
                continue
            p = per.setdefault(s["cls"], {"cls": s["cls"], "n_days": 0, "lost_pcs": 0.0,
                                          "by_bucket": {b: 0.0 for b in UNIT_BUCKETS}})
            p["n_days"] += 1
            n_sd += 1
            if u.get("slow_good") is not None:
                n_sd_good += 1
            for b, v in (("scrap", scrap), ("rework", rework), ("slow", slow)):
                if v is None:
                    continue
                p["by_bucket"][b] += float(v)
                line[b] += float(v)
                p["lost_pcs"] += float(v)
    stations = []
    for p in per.values():
        p["by_bucket"] = {k: _r6(v) for k, v in p["by_bucket"].items()}
        p["lost_pcs"] = _r6(p["lost_pcs"])
        p["top"] = sorted(((b, p["by_bucket"][b]) for b in UNIT_BUCKETS
                           if p["by_bucket"][b] > 1e-9),
                          key=lambda kv: (-kv[1], UNIT_BUCKETS.index(kv[0])))
        stations.append(p)
    stations.sort(key=lambda x: (-(x["lost_pcs"] or 0.0), x["cls"]))
    line = {k: _r6(v) for k, v in line.items()}
    tot = sum(line.values())
    return {
        "days": int(days), "n_days": len(used), "n_days_units": len(with_units),
        # 慢速尺混用揭露（見上方註解）：< n_days_units 表示部分天數的慢速仍是嘗試軸口徑
        "n_days_slow_good": n_days_slow_good, "n_bad_docs": n_bad,
        # ★ additive（2026-08-03 修）：按**站×日**計的混尺揭露＝真正的比例；
        #   `n_station_days_slow_good / n_station_days` 才是「榜上有幾成是新尺」。
        "n_station_days": n_sd, "n_station_days_slow_good": n_sd_good,
        "n_degraded": sum(1 for d in used
                          if str(d.get("wo_source_status") or "").lower() != "ok"),
        "n_missing": len(want - {d["day"] for d in used}),
        "day_from": min(d["day"] for d in with_units),
        "day_to": max(d["day"] for d in with_units),
        "by_bucket": line, "total_lost_pcs": _r6(tot),
        "share": {b: (_r6(line[b] / tot) if tot > 0 else None) for b in UNIT_BUCKETS},
        "approx_buckets": ("slow",),
        "stations": stations,
        "thin": len(with_units) < max(1, int(days) // 4),
    }


# =============================================================================
# 7. i18n（Part B §8.2 逐字；en/zh 鍵集對稱；canonical cls 永不翻）
# =============================================================================
I18N_UNIT = {
    "en": {
        "at_p_scrap": "Scrap — first-FAIL landed here",
        "at_p_rework": "Rework — retested at this station",
        "at_p_slow": "Slow running — below own baseline rate",
        # §4 誠實合桶 pin（④微停再議不做 ⇒ 桶標籤必須自己講）；additive 於 §8.2 表
        "at_p_slow_sub": "slow / micro-stops (not split)",
        "at_p_est": "≈ estimated: (own baseline rate − actual) × producing hours.",
        "at_p_caliber": "PH account: scrap/rework per the Qlik first-FAIL caliber in "
                        "the same window; slow is an estimate against the station's own "
                        "baseline rate.",
        "at_p_unavail": "The PH account is not available for this data source.",
        # ── 提案 A：綠色切三段（生產機時 → 良品/重工/報廢）──
        "un_split_hdr": "Producing hours, split",
        "un_b_goodh": "Good PH",
        "un_b_reworkh": "Retesting",
        "un_b_scraph": "PH later scrapped",
        "un_split_q": ("The green \"producing\" band hides what the machine was actually "
                       "making. It is split by yesterday's own proportions — two stated "
                       "assumptions: every attempt takes the same time, and first attempts "
                       "scrap at the day's Qlik first-FAIL rate. The three parts add up to "
                       "the producing hours exactly."),
        # ── 提案 E：良品速率＝全頁唯一換算尺 ──
        "un_ruler": "good PH per clean machine-hour",
        "un_ruler_q": ("One ruler for the whole page: good PH ÷ clean machine-hours "
                       "(producing hours minus the time spent retesting and minus the time "
                       "spent on PH that were scrapped). Every cause below is converted "
                       "with this one number, so the parts add up exactly. It is the "
                       "station's own 7-day median, not yesterday's — otherwise running "
                       "slow would silently rewrite the yardstick."),
        "un_ruler_none": "No ruler yet for this station — causes cannot be converted to "
                         "PH, so the hours are shown on their own.",
        # ── 提案 C：不叫「損失」叫「未使用產能」；未解釋另列並標明「推定」 ──
        "un_unused": "Unused capacity",
        "un_unused_q": ("Potential good PH at the baseline rate over the full day, minus "
                        "the good PH actually made. Only causes somebody can act on are "
                        "added up here — unexplained time is listed separately and never "
                        "added in."),
        "un_presumed": "Presumed not scheduled / not loaded",
        # ★ 用詞紅線（提案 C）：這一段是**對比句**，最容易手滑寫回「not a loss / 損失」——
        #   全頁負斷言（`board_daily` selftest ⑤c）掃 I18N_UNIT/I18N_ATTRIB/I18N_DAILY 三張表，
        #   對比要用「不計入未使用產能」這一側講，不得把舊詞帶回來。
        "un_presumed_q": ("Idle time with no work order, no material shortage and no "
                          "downstream block. With no shift roster wired in, night shifts "
                          "and weekends land here — so this is a presumption, not capacity "
                          "anybody failed to use. It is deliberately excluded from the "
                          "unused-capacity total."),
        "un_faster": "ran faster than baseline",
        # ── 七桶**短名**（提案 F 句式與 H 藥丸專用；長名仍住 at_b_*/at_p_* 供圖例/明細帶）──
        #    ★ 句子裡不能用 "Unexplained (possibly manpower / scheduling / other)" 這種長名，
        #      也不能用 "Scrap — first-FAIL landed here"（模板 split('—') 那招在句子裡走味）。
        "un_s_wo": "downtime", "un_s_starved": "no material",
        # C-PC10：短名也不得寫成 "unscheduled"／"no shift"（那是 G1 才答得了的問題）
        "un_s_closure": "plant-wide stoppage",
        "un_s_blocked": "downstream blocked", "un_s_unexplained": "unexplained",
        "un_s_scrap": "scrap", "un_s_rework": "retesting", "un_s_slow": "running slow",
        # ── 提案 F 句式 v2（未達標＝全句、達標＝短句；WIP/OT **永不入句**＝DEMO 假數字
        #    寫進流暢中文會被當真）。`{…}` 由模板 replace 填。──
        "un_sent_full": "Missed by {gap} — unused capacity ≈{tot} PH: {causes}.",
        "un_sent_short": "On target. Unused capacity ≈{tot} PH: {causes}.",
        # ★ `un_sent_plain`＝**今天唯一會出現的損失句**（B2 `sentence_ctx` kind="plain"）：
        #   target 仍是 DEMO ⇒ `{gap}` 與「達標與否」都不得入句（達標由 ✓/✕ 符號講）。
        #   SALTMARSH 真目標源接通那天才會改用 full/short 兩式。
        "un_sent_plain": "Unused capacity ≈{tot} PH: {causes}.",
        # ★ 中性文案（2026-08-02 提案 C）：原句帶 "On target"＝把 DEMO target 的達標判定
        #   寫進句子；kind="clean" 在 target_real=False 時照樣會出現 ⇒ 必須無達標語意。
        "un_sent_met_clean": "No material unused capacity.",
        "un_sent_unexp": "Plus {h}h unexplained (≈{n} PH) — presumed not scheduled / not "
                         "loaded, not counted in unused capacity.",
        "un_sent_noruler": "No baseline rate yet for this station — causes are shown in "
                           "machine-hours only.",
        # unit 專屬 note 的人話（沒有這些鍵，模板只能把 machine key 印給人看＝verify V3-MED）
        "un_note_no_time_src": "No machine timeline for this station — the time leg cannot "
                               "be computed, so slow running is not measurable here.",
        "un_note_base_warming": "Baseline still warming up — not enough stored days yet to "
                                "take this station's own rate median.",
        "un_note_no_rework_src": "Rework source unavailable for this window — attempts and "
                                 "rework are not counted.",
        "un_note_no_qlik": "No Qlik row for this station in this window — scrap is not "
                           "counted here.",
        "un_note_counts_window_mismatch": ("Attempts anchored outside this window while the "
                                           "machine was producing (cross-midnight lot) — "
                                           "rates and the ruler breakdown are not computed "
                                           "for this day."),
        "un_note_qlik_window_mismatch": ("This station ran attempts yesterday but no PH "
                                         "finished its life in the Qlik window — the good "
                                         "rate would come out as zero, so the ruler "
                                         "breakdown is not computed for this day."),
    },
    "zh": {
        "at_p_scrap": "報廢——first-FAIL 落在本站",
        "at_p_rework": "重工——本站復測消耗",
        "at_p_slow": "慢速——低於自身基準速率",
        "at_p_slow_sub": "速率劣化（含微停，未拆分）",
        "at_p_est": "≈ 估算：（自身基準速率 − 實際）× 生產小時。",
        "at_p_caliber": "PH 帳：報廢/重工＝同一生產日的 Qlik first-FAIL 口徑；慢速＝對自身基準速率"
                        "的估算。",
        "at_p_unavail": "此資料源算不出 PH 帳。",
        # ── 提案 A：綠色切三段（生產機時 → 良品/重工/報廢）──
        "un_split_hdr": "生產機時的組成",
        "un_b_goodh": "做出良品",
        "un_b_reworkh": "重測",
        "un_b_scraph": "做了但報廢",
        # ⚠ tooltip 是**純文字節點**（Jinja autoescape、無 markdown 渲染器）⇒ 字面 `**` 會
        #   原樣印在畫面上。ZH 這三句 Part C 文案曾夾帶星號（2026-08-03 群 D）；勿再加回。
        "un_split_q": ("綠色那段「生產」藏住了機器實際在做什麼。這裡用當日自身比例切開"
                       "——兩個講明的假設：每次嘗試耗時相同、首次嘗試依當日 Qlik first-FAIL "
                       "率報廢。三段精確加總＝生產機時。"),
        # ── 提案 E：良品速率＝全頁唯一換算尺 ──
        "un_ruler": "PH／乾淨機台小時",
        "un_ruler_q": ("全頁一把尺：良品數 ÷ 乾淨機台小時（生產機時扣掉重測時數、再扣掉"
                       "做了卻報廢的時數）。下面每個原因都用這一個數字換算，所以帳能精確平。"
                       "用的是本站自身近 7 日中位數、不是昨天的——否則跑得慢會把尺一起改掉。"),
        "un_ruler_none": "本站還沒有尺——原因換算不成 PH，只列時數。",
        # ── 提案 C：不叫「損失」叫「未使用產能」；未解釋另列並標明「推定」 ──
        "un_unused": "未使用產能",
        "un_unused_q": ("以基準速率計、整天本可做出的良品，減去實際做出的良品。只把有人"
                        "可以動手的原因加總——未解釋的時間另外列、永不加進來。"),
        "un_presumed": "推定未派工／未進料",
        # ★ 同 en：對比句一律講「不計入未使用產能」那一側（負斷言掃三張 i18n 表）。
        "un_presumed_q": ("沒有工單、沒有缺料、下游也沒堵的閒置時間。班表源尚未接通，"
                          "夜班與週末會全部落在這裡——所以這是推定、不是沒用掉的產能，"
                          "刻意不計入未使用產能合計。"),
        "un_faster": "比基準快",
        # ── 七桶**短名**（同 en 註解）──
        "un_s_wo": "停機", "un_s_starved": "缺料",
        "un_s_closure": "全廠停線",              # C-PC10：永不寫成「沒排班／班表」
        "un_s_blocked": "下游堵", "un_s_unexplained": "未解釋",
        "un_s_scrap": "報廢", "un_s_rework": "重測", "un_s_slow": "跑得慢",
        # ── 提案 F 句式 v2（同 en 註解；WIP/OT 永不入句）──
        "un_sent_full": "差 {gap} PH——未使用產能 ≈{tot} PH：{causes}。",
        "un_sent_short": "已達標。未使用產能 ≈{tot} PH：{causes}。",
        # ★ 同 en 註解：今天唯一會出現的損失句；不提 target、不提達標。
        "un_sent_plain": "未使用產能 ≈{tot} PH：{causes}。",
        "un_sent_met_clean": "沒有可觀的未使用產能。",
        "un_sent_unexp": "另有未解釋 {h} 機時（≈{n} PH）——推定未派工／未進料，"
                         "不計入未使用產能。",
        "un_sent_noruler": "本站尚無基準速率——原因只以機台小時呈現。",
        # unit 專屬 note 的人話（同 en；模板不得把 machine key 直接印給人看）
        "un_note_no_time_src": "本站沒有機台 timeline——時間腿算不出來，慢速在這裡不可測。",
        "un_note_base_warming": "基準暖機中——累積的日子還不夠取本站自身速率中位數。",
        "un_note_no_rework_src": "本生產日重工源不可用——嘗試數與重工未計入。",
        "un_note_no_qlik": "本站本生產日無 Qlik 列——報廢未計入。",
        "un_note_counts_window_mismatch": "機台有在生產、但嘗試列錨在本生產日之外（跨午夜 lot）"
                                          "——本日不計速率與換算分解。",
        "un_note_qlik_window_mismatch": "本站昨天有嘗試、但本生產日 Qlik 母體掛零（沒有單位在"
                                        "本生產日完成生涯）——良品速率會算成零，本日不計換算分解。",
    },
}

UNIT_BUCKET_I18N_KEY = {"scrap": "at_p_scrap", "rework": "at_p_rework", "slow": "at_p_slow"}

# 七桶 → **長名**鍵（圖例/明細帶）＝時間四桶住 wattr、顆數三桶住本檔；一份、不重打。
# A5：wattr.BUCKET_I18N_KEY 仍留 closure 鍵（舊檔敘述用）⇒ 這裡**過濾到現行 LOSS_BUCKETS**
LOSS_BUCKET_I18N_KEY = {k: v for k, v in dict(wattr.BUCKET_I18N_KEY,
                                              **UNIT_BUCKET_I18N_KEY).items()
                        if k in LOSS_BUCKETS}
# 七桶 → **短名**鍵（句式 v2 與單一排序藥丸；全部住本檔）
LOSS_SHORT_I18N_KEY = {b: f"un_s_{b}" for b in LOSS_BUCKETS}
# 提案 A 三段 → i18n 鍵（生產機時的組成；固定序＝良品→重工→報廢）
SPLIT_SEGMENTS = ("good_h", "rework_h", "scrap_h")
SPLIT_I18N_KEY = {"good_h": "un_b_goodh", "rework_h": "un_b_reworkh",
                  "scrap_h": "un_b_scraph"}


# =============================================================================
# 8. demo（確定性合成；全桶型＋partial＋degraded＋吸收行＋speed_gain；走真引擎）
# =============================================================================
def _demo_att(cls, fleet, prod, wo, st, bl):
    """demo 時間帳 row（T1 一致：unexplained＝殘差；只帶 unit 引擎會讀的欄）。"""
    total = fleet * 24.0
    return {"cls": cls, "fleet_n": fleet, "total_mh": total, "producing_h": prod,
            "wo_union_h": wo, "starved_h": st, "blocked_h": bl,
            "unexplained_h": total - prod - wo - st - bl,
            "lost_h": total - prod, "unmeasurable": False, "note": None}


def _demo_att_unm(cls, note):
    return {"cls": cls, "fleet_n": 0, "total_mh": None, "producing_h": None,
            "wo_union_h": None, "starved_h": None, "blocked_h": None,
            "unexplained_h": None, "lost_h": None, "unmeasurable": True, "note": note}


# (cls → (attempts, first_attempts, qlik_pass, qlik_fail))：計數/報廢 demo 數字
_DEMO_COUNTS = {
    "segFrameBonding": (260, 250, 200, 40),          # 報廢大戶（hero worst=scrap）
    "segDiePlacement": (320, 300, 280, 12),           # 全桶正常日（identity True）
    "segFlexBonding": (200, 195, 190, 4),           # 快於基準（speed_gain）
    "segWireBonding": (150, 140, 130, 6),             # 基準首暖（partial；計數/報廢照顯）
    "segSealDispense": (180, 172, 160, 8),        # 時間源 src_err（速率桶「—」）
    "segPotting": (120, 116, 110, 3),          # 零工期站（時間「—」、顆數照算）
    "segCleanTest": (140, 133, 0, 0),          # 報廢源缺列（per-row degraded 示範）
    "segQASetup": (90, 88, 85, 2),         # 整窗無訊號站（nosig）
}

# ★ 顆數帳 demo 額外**補**的時間腿型（wattr 的 demo 產得出 zero_len/nosig，產不出
#   `src_err`＝「時間源當時查不到」，那是顆數帳 note 要驗的一型）。
#   ⚠ 只能補在 **wattr demo 沒有的站**上：補在它有的站等於同一天同一站兩份矛盾的時間腿
#   （頁面時間條照畫 11 機時、顆數列卻說「時間源查不到」）——正是本輪在修的那種不一致。
_DEMO_ATT_EXTRA = {
    "segSerialisation": wattr.NOTE_SRC_ERR,
}

_DEMO_BASE = {
    # r_good_base＝提案 E 的頁面尺（顆／乾淨機時）；刻意低於 r_base（嘗試軸），
    # 與 live 的方向一致（首次嘗試會報廢、重測時數被扣掉 ⇒ 良品速率恆較低）。
    # ⚠ 數字與 `wattr.demo_board()` 的 producing_h 綁在一起（2026-08-03 對齊後 Frame=8h、
    #   FlexBonding=4h、Wirebond=6h、DiePlacement=15h）——動任一邊都要重算下面的故事。
    "segFrameBonding": {"r_base": 33.0, "n_days": 7, "r_good_base": 32.0, "n_days_good": 7},
    "segDiePlacement": {"r_base": 22.0, "n_days": 7, "r_good_base": 19.0, "n_days_good": 7},
    # 45×4−200 = −20 ⇒ speed_gain 20（嘗試軸）；良品尺這站也刻意做成「比基準快」
    "segFlexBonding": {"r_base": 45.0, "n_days": 6, "r_good_base": 45.0, "n_days_good": 6},
    "segWireBonding": {"r_base": None, "n_days": 2,        # 首暖（<4 樣本）＝兩把尺同時暖機
                    "r_good_base": None, "n_days_good": 2},
    "segCleanTest": {"r_base": 12.0, "n_days": 5, "r_good_base": 10.0, "n_days_good": 5},
    # 只有嘗試軸有尺、良品尺缺（v2 舊檔推不出 r_good 的情形）⇒ ranked 空、藥丸顯「—」
    "segSealDispense": {"r_base": 15.0, "n_days": 7, "r_good_base": None, "n_days_good": 1},
}


def _demo_att_map(day):
    """demo 的時間帳 rows ＝**直接取 `wattr.demo_board(day)` 的同一份**（逐站原值）。

    ★ 2026-08-03 修（LOW D-demo）：本檔原本另外合成一份 `_DEMO_ATT`，與 attrib demo 的
      同站 `producing_h` 對不上（Frame 30 vs 8、FlexBonding 10 vs 4、Wirebond 12 vs 6）。
      /daily demo 頁的 24h 條**用時間帳畫底、用顆數帳切三段** ⇒ 三段和（30h）遠大於
      底條的生產段（8h）＝綠段溢出到 191%。兩份 demo 是同一天同一站，本來就該是同一組
      數字；改成**單一來源**之後這條不變式是結構性成立的（selftest 逐站釘）。
    """
    rows = {r.get("cls"): dict(r) for r in (wattr.demo_board(day=day).get("rows") or ())
            if isinstance(r, dict) and r.get("cls")}
    for cls, note in _DEMO_ATT_EXTRA.items():
        if cls not in rows:                       # 只補、不覆蓋（見 `_DEMO_ATT_EXTRA` ⚠）
            rows[cls] = _demo_att_unm(cls, note)
    return rows


def _demo_rework():
    st = {}
    for o, cls, mcl in mq.QLIK_STATIONS:
        a, f, _p, _f = _DEMO_COUNTS.get(cls, (0, 0, 0, 0))
        st[(cls, mcl)] = {"attempts": a, "first_attempts": f, "rework_n": a - f,
                          "n_fail_rows": 0}
    return {"stations": st,
            "diag": {"n_rows": sum(v[0] for v in _DEMO_COUNTS.values()),
                     "n_m0_attempts": 4, "n_offroute_attempts": 2, "n_bad_rows": 0,
                     "mcl_filter": "applied", "degraded": False}}


def _demo_grid():
    rows = []
    for o, cls, mcl in mq.QLIK_STATIONS:
        if cls == "segCleanTest":
            continue                    # 缺列 ⇒ per-row scrap degraded 示範
        _a, _f, qp, qf = _DEMO_COUNTS.get(cls, (0, 0, 0, 0))
        rows.append({"ord": o, "cls": cls, "mcl": mcl,
                     "display": mq.QLIK_DISPLAY.get(o, cls),
                     "total": qp + qf, "pass": qp, "fail": qf})
    return {"schema": mq.SCHEMA_ID, "stations": rows,
            "absorption": [{"station": "segPostLamInspection",
                            "mcl": "assyModule", "n_lots": 9}],
            "absorbed_total": 9, "diag": {"degraded": False}}


def _demo_docs(day):
    """demo 顆數榜輸入：3 份確定性日檔（day−1..day−3）。

    ★ 刻意**混版**：k=1 是 v3（帶 `slow_good`＝新尺）、k=2/3 是 v2（只有 `speed_net`）
      ⇒ 走過 `parse_ledger_doc` 三收、`r_good_of_units` 從 v2 推導、以及 30d 顆數榜的
      「慢速尺混用」揭露（`n_days_slow_good`）三條路徑。
    """
    d0 = wattr._dt(day)
    docs = []
    for k in (1, 2, 3):
        dd = (d0 - timedelta(days=k)).strftime("%Y-%m-%d")
        v3 = (k == 1)
        _extra = ({"good_h": 12.0, "rework_h": 1.5, "scrap_h": 0.5, "r_good": 21.0,
                   "r_good_base": 19.0, "base_good_n_days": 7, "slow_good": 6.0}
                  if v3 else {})
        _extra2 = ({"good_h": 21.0, "rework_h": 0.6, "scrap_h": 4.4, "r_good": 9.5,
                    "r_good_base": 7.5, "base_good_n_days": 7, "slow_good": 2.5}
                   if v3 else {})
        docs.append({
            # ★ 刻意保持 v3（不跟著升 v4）＝讓混版路徑真的走一次 v2/v3 兩種**歷史**檔；
            #   v4 的路徑由 U8x 自己的 `doc4` 蓋。
            "schema_ver": (LEDGER_SCHEMA_V3 if v3 else LEDGER_SCHEMA_V2),
            "day": dd, "source": "demo",
            "wo_source_status": "ok", "computed_at": f"{dd}T23:59:00",
            "grid_min": wattr.GRID_MIN, "thresholds": dict(wattr.TH),
            "line": {"units_line": None},
            "stations": [
                {**{kk: None for kk in wattr._DOC_STATION_KEYS}, "cls": "segDiePlacement",
                 "unmeasurable": False,
                 "units": {"attempts": 300 + k, "first_attempts": 290, "rework_n": 10 + k,
                           "qlik_pass": 270, "qlik_fail": 10 + k, "r_y": 20.0 + k,
                           "r_base": 22.0, "base_n_days": 7, "speed_net": 8.0,
                           "unit_bridge": 10 - k, "identity_ok": True,
                           "scrap_src_degraded": False, "note": None, **_extra}},
                {**{kk: None for kk in wattr._DOC_STATION_KEYS}, "cls": "segFrameBonding",
                 "unmeasurable": False,
                 "units": {"attempts": 250, "first_attempts": 245, "rework_n": 5,
                           "qlik_pass": 200, "qlik_fail": 40, "r_y": 8.5,
                           "r_base": 9.0, "base_n_days": 7, "speed_net": 3.0,
                           "unit_bridge": 5, "identity_ok": True,
                           "scrap_src_degraded": False, "note": None, **_extra2}},
            ],
        })
    return docs


def demo_board(day=DEMO_DAY):
    """demo 板（全合成＝頁面必掛大牌；確定性：同 day 兩呼逐位相同）。

    型態覆蓋（§8 demo 清單）：全桶（scrap/rework/slow）＋speed_gain＋partial（首暖）＋
    per-row scrap degraded＋吸收行（absorbed_total=9）＋zero_len/nosig/src_err/no_time_src
    ＋跨窗零嘗試（`counts_window_mismatch`：時間帳有、`_DEMO_COUNTS` 沒有的站）。
    ★ 時間腿與 `wattr.demo_board(day)` **同源**（`_demo_att_map`）＝逐站 producing_h 相等。
    """
    att = _demo_att_map(day)
    return build_board(day, att, _demo_grid(), _demo_rework(), _DEMO_BASE,
                       source="demo", pareto_docs=_demo_docs(day),
                       end_day=day, computed_at=f"{day}T23:59:00")


# =============================================================================
# 9. selftest（純離線；SPEC Part A §8「board_unit.py」節逐條 ＋ Part C 換尺新案）
#    ★ 因 Part C 換尺（[U-C] → [U-D] 單一尺）而更新的舊案，斷言文字內都標了「Part C 換尺」
#      或直接改寫成 [U-D]；U0x/U13x 兩節與 U7.6–U7.11、U8.5b/8.14–8.17、U9.11–9.13 全新。
# =============================================================================
def _selftest():
    fails = []
    n_cases = [0]

    def t(name, cond):
        n_cases[0] += 1
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    DAY = "2026-07-31"
    PH = "assyModule"
    DA = "segDiePlacement"
    Frame = "segFrameBonding"

    def _rw_many(spec):
        """`{cls: (attempts, first)}` → rework 聚合形（其餘 42 站補零）。"""
        st = {}
        for o, c, m in mq.QLIK_STATIONS:
            a, f = spec.get(c, (0, 0))
            st[(c, m)] = {"attempts": a, "first_attempts": f, "rework_n": a - f,
                          "n_fail_rows": 0}
        return {"stations": st, "diag": {"degraded": False}}

    def _rw_one(cls, mcl, attempts, first, rework_n=None):
        rw = _rw_many({cls: (attempts, first)})
        if rework_n is not None:                     # [U-B] 破的注入口（來源自稱值）
            rw["stations"][(cls, mcl)]["rework_n"] = rework_n
        return rw

    def _grid_many(spec, absorbed=0, family=True):
        """`{cls: (pass, fail)}` → Qlik grid 形（恆 43 列）。

        ★ #98：預設帶 `pass_by_family`（全部歸 Plover ⇒ 恆等式成立）＝**完整的 v6 素材**；
          `family=False` ＝家族欄缺席的舊素材（`pass_by_family` 一律 None）。
        """
        rows = []
        for o, c, m in mq.QLIK_STATIONS:
            p, f = spec.get(c, (0, 0))
            rows.append({"ord": o, "cls": c, "mcl": m, "total": p + f,
                         "pass": p, "fail": f,
                         "pass_by_family": (({"Plover": p} if p else {})
                                            if family else None)})
        ab = ([{"station": "segPostLamInspection", "mcl": PH,
                "n_lots": absorbed}] if absorbed else [])
        return {"stations": rows, "absorption": ab, "absorbed_total": absorbed,
                "diag": {"degraded": False}}

    def _grid_one(cls, qpass, qfail, absorbed=0):
        return _grid_many({cls: (qpass, qfail)}, absorbed=absorbed)

    att_da = _demo_att(DA, 2, 15.0, 4.0, 2.0, 1.0)     # total 48、prod 15、idle 33
    # ★ Part C 換尺後基準 fixture 一律**雙尺**：`r_base`（嘗試軸／[U-A]）＋
    #   `r_good_base`（良品尺／[U-D]＝頁面實際在用的那一把）。只給 r_base 的板 = 無尺板。
    base_da = {DA: {"r_base": 22.0, "n_days": 7, "r_good_base": 19.0, "n_days_good": 7}}

    # =========================================================================
    # U0x 提案 A/E 原子函式（split_producing / good_rate / r_good_of_units）
    #     ★ 這一節是新尺的全部算術；上層恆等式是它的推論，先把地基釘死。
    # =========================================================================
    sp = split_producing(15.0, 320, 20, 280, 12)
    t("U0.1 split_producing 三段精確和＝producing（1e-9）、三段恆非負",
      abs(sum(sp) - 15.0) < 1e-9 and all(v >= 0.0 for v in sp))
    t("U0.2 split 用**率**不用絕對數：rework_h=P×rw/at、scrap_h=(P−rework_h)×q",
      abs(sp[1] - 15.0 * 20 / 320) < 1e-12
      and abs(sp[2] - (15.0 - 15.0 * 20 / 320) * (12 / 292)) < 1e-12
      and abs(sp[0] - (15.0 - 15.0 * 20 / 320) * (1 - 12 / 292)) < 1e-12)
    sp0 = split_producing(10.0, 100, 10, 0, 0)
    t("U0.3 Qlik 母體 0（pass+fail=0）⇒ q=0.0＝量到了就是零：scrap_h=0、good_h=其餘",
      sp0[2] == 0.0 and abs(sp0[0] - 9.0) < 1e-12 and abs(sp0[1] - 1.0) < 1e-12)
    t("U0.4 報廢腿缺（qpass/qfail 任一 None）⇒ 整組 None（**絕不 q=0 頂替**＝虛報良品速率)",
      split_producing(15.0, 320, 20, None, 12) == (None, None, None)
      and split_producing(15.0, 320, 20, 280, None) == (None, None, None)
      and split_producing(None, 320, 20, 280, 12) == (None, None, None)
      and split_producing(15.0, 320, None, 280, 12) == (None, None, None))
    t("U0.5 全停日（P=0 ∧ attempts=0 ∧ rework=0）⇒ (0.0,0.0,0.0)＝[U-D] 照樣可算",
      split_producing(0.0, 0, 0, 0, 0) == (0.0, 0.0, 0.0)
      and split_producing(0.0, 0, 0, 50, 3) == (0.0, 0.0, 0.0))
    t("U0.6 來源不一致 ⇒ None：rework>attempts／P>0∧at=0／P=0∧at>0／P<0",
      split_producing(15.0, 320, 999, 280, 12) == (None, None, None)
      and split_producing(15.0, 0, 0, 280, 12) == (None, None, None)
      and split_producing(0.0, 320, 20, 280, 12) == (None, None, None)
      and split_producing(-1.0, 320, 20, 280, 12) == (None, None, None))
    t("U0.7 good_rate 分母下限（0.4h ⇒ None；恰 MIN_PROD_MH ⇒ 照算＝含等於）",
      good_rate(100, 0.4) is None and good_rate(100, MIN_PROD_MH) is not None
      and abs(good_rate(100, MIN_PROD_MH) - 100 / MIN_PROD_MH) < 1e-9)
    t("U0.8 good_rate：qlik_pass=0 且分母夠 ⇒ 0.0（不是 None）；任一 None ⇒ None 傳染",
      good_rate(0, 10.0) == 0.0 and good_rate(None, 10.0) is None
      and good_rate(100, None) is None)
    t("U0.9 r_good_of_units：v3 有 r_good 欄 ⇒ 直讀（不重算）",
      r_good_of_units({"r_good": 12.5}) == 12.5)
    # 手算：attempts 100 / r_y 10 ⇒ P=10；rework 20 ⇒ rework_h=2、first_h=8；
    #       q=8/80=0.1 ⇒ scrap_h=0.8、good_h=7.2 ⇒ r_good=72/7.2=10.0
    u_v2 = {"attempts": 100, "first_attempts": 80, "rework_n": 20, "r_y": 10.0,
            "qlik_pass": 72, "qlik_fail": 8}
    t("U0.10 r_good_of_units：v2 無 r_good ⇒ 由 attempts/r_y 還原 P 後推導（手算 10.0）",
      abs(r_good_of_units(u_v2) - 10.0) < 1e-6)
    t("U0.11 r_good_of_units：r_y／attempts／報廢腿缺 ⇒ None（不猜、不 0 頂替）",
      r_good_of_units(dict(u_v2, r_y=None)) is None
      and r_good_of_units(dict(u_v2, attempts=None)) is None
      and r_good_of_units(dict(u_v2, qlik_pass=None)) is None
      and r_good_of_units(None) is None and r_good_of_units({}) is None)
    # ── LOW A-2：負的 pass/fail＝壞值（q 會跑出 [0,1] ⇒ 某一段變負、堆疊條倒著畫，
    #    而 [U-D] 因為構造性照樣「平」＝錯得很安靜）。
    t("U0.12 split_producing：負 qlik_pass／qlik_fail ⇒ (None,None,None)（三段恆非負不變量）",
      split_producing(15.0, 320, 20, -1, 12) == (None, None, None)
      and split_producing(15.0, 320, 20, 280, -5) == (None, None, None)
      and split_producing(0.0, 0, 0, -1, 0) == (None, None, None))
    # ── MED A-1：Qlik 空母體日（有嘗試、pass+fail=0 且 grid 列在）＝毒尺來源。
    t("U0.13 qlik_window_mismatch 判定：有嘗試＋母體 0 ⇒ True；母體非 0／腿缺／零嘗試 ⇒ False",
      qlik_window_mismatch(100, 0, 0) is True
      and qlik_window_mismatch(100, 72, 8) is False
      and qlik_window_mismatch(0, 0, 0) is False          # 全停日＝合法的零、不是不同窗
      and qlik_window_mismatch(100, None, 0) is False     # 報廢腿缺＝另一種降級（no_qlik）
      and qlik_window_mismatch(None, 0, 0) is False)
    t("U0.14 r_good_of_units：空母體日 ⇒ None（v2 推導與 **v3 直讀**兩條路都擋；"
      "直讀路徑不擋的話，本修之前落地的 0.0 毒值會繼續流進基準）",
      r_good_of_units(dict(u_v2, qlik_pass=0, qlik_fail=0)) is None
      and r_good_of_units({"attempts": 100, "qlik_pass": 0, "qlik_fail": 0,
                           "r_good": 0.0}) is None
      and r_good_of_units({"attempts": 100, "qlik_pass": 0, "qlik_fail": 0,
                           "r_good": 12.5}) is None)
    # ★ 合法的零走不到 0.0：真全敗日（pass=0 ∧ fail>0）q=1 ⇒ good_h=0 ⇒ 被 MIN_PROD_MH
    #   擋成 None。這一案是 `base_rates` 那道 `<=0` 防線「不會誤傷合法零」的證明。
    sp_allfail = split_producing(10.0, 100, 0, 0, 90)
    t("U0.15 真全敗日（pass=0 ∧ fail>0）⇒ good_h=0 ⇒ r_good=None（不是 0.0、不誤傷）",
      abs(sp_allfail[0]) < 1e-12 and abs(sp_allfail[2] - 10.0) < 1e-12
      and good_rate(0, sp_allfail[0]) is None)

    # =========================================================================
    # U1x [U-A]／[U-B]／[U-D] 恆等式（★ Part C 換尺：頂層由 [U-C] 改為 [U-D]）
    # =========================================================================
    b = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12),
                    _rw_one(DA, PH, 320, 300), base_da)
    r = {x["cls"]: x for x in b["rows"]}[DA]
    t("U1.1 正常日 [U-A] ≤1e-6（330 = 320 + 10）",
      identity_error_ua(r) <= UA_TOL and abs(r["speed_net"] - 10.0) < 1e-9)
    t("U1.2 正常日 [U-B] 整數精確（320 = 300 + 20）",
      r["rework_n"] == 20 and r["attempts"] == r["first_attempts"] + r["rework_n"])
    # ★ Part C 換尺：舊 U1.3 驗的是 [U-C]（含 bridge、以 r_base 為尺）＝已退役；
    #   頁面唯一的頂層恆等式改成 [U-D]（單一尺、七桶、全單位＝顆）。
    t("U1.3 正常日 [U-D] ≤1e-6＋identity_ok=True（單一尺七桶精確平）",
      identity_error_ud(r) <= UD_TOL and r["identity_ok"] is True)
    lb = r["loss_by"]
    t("U1.3b [U-D] 逐項手算：LHS=19×48−280=632 ＝ Σ七桶；wo=19×4、unexplained=19×26",
      abs((19.0 * 48.0 - 280) - sum(lb[k] for k in LOSS_BUCKETS)) < 1e-6
      and abs(sum(lb[k] for k in LOSS_BUCKETS) - 632.0) < 1e-6
      and abs(lb["wo"] - 76.0) < 1e-9 and abs(lb["unexplained"] - 494.0) < 1e-9
      and abs(lb["rework"] - 19.0 * r["rework_h"]) < 1e-9
      and abs(lb["slow"] - r["slow_good"]) < 1e-12)
    t("U1.3c loss_by 用 canonical 桶名（`wo` 而非 `wo_union`）＋七鍵齊＋actionable 可算",
      set(lb) == set(LOSS_BUCKETS) and "wo_union" not in lb
      and r["loss_actionable"] is not None
      and abs(r["loss_actionable"]
              - sum(max(0.0, lb[k]) for k in ACTIONABLE_BUCKETS)) < 1e-9)
    t("U1.3d 提案 A 三段落在 row 上且精確和＝producing_h（good/rework/scrap）",
      abs(r["good_h"] + r["rework_h"] + r["scrap_h"] - r["producing_h"]) < 1e-9
      and abs(r["r_good"] - 280 / r["good_h"]) < 1e-9)
    t("U1.4 橋接＝定義式（300 − 292 = 8）", r["unit_bridge"] == 8)
    t("U1.5 r_y＝attempts/producing（320/15）", abs(r["r_y"] - 320 / 15.0) < 1e-9)
    t("U1.5b ranked（提案 H）依顆降冪取 3、不含 unexplained、approx 恆 True",
      [c["bucket"] for c in r["ranked"]] == ["wo", "starved", "blocked"]
      and all(c["approx"] is True for c in r["ranked"]))

    # 全停日：producing=0、attempts=0 ⇒ speed_net=0、split=(0,0,0)、損失全落時間四桶
    att_stop = _demo_att(DA, 2, 0.0, 20.0, 4.0, 0.0)
    b2 = build_board(DAY, {DA: att_stop}, _grid_one(DA, 0, 0),
                     _rw_one(DA, PH, 0, 0), base_da)
    r2 = {x["cls"]: x for x in b2["rows"]}[DA]
    t("U1.6 全停日 speed_net=0.0（不是 None、不是損失）", r2["speed_net"] == 0.0)
    t("U1.7 全停日 [U-A]/[U-D] ≤1e-6、identity True、三段=(0,0,0)（損失全落時間四桶）",
      identity_error_ua(r2) <= UA_TOL and identity_error_ud(r2) <= UD_TOL
      and r2["identity_ok"] is True
      and (r2["good_h"], r2["rework_h"], r2["scrap_h"]) == (0.0, 0.0, 0.0)
      and abs(r2["loss_by"]["wo"] - 19.0 * 20.0) < 1e-9
      and abs(sum(r2["loss_by"][k] for k in LOSS_BUCKETS) - 19.0 * 48.0) < 1e-6)
    t("U1.7b 全停日 good_h=0 ⇒ r_good=None（分母下限）但 loss_by 照算＝不連坐",
      r2["r_good"] is None and r2["loss_by"] is not None)

    # 快於基準日：嘗試軸 speed_net=−20；良品尺 slow_good 亦為負（＝好消息不進榜）
    att_fast = _demo_att(DA, 1, 10.0, 2.0, 1.0, 0.0)
    b3 = build_board(DAY, {DA: att_fast}, _grid_one(DA, 190, 4),
                     _rw_one(DA, PH, 200, 195),
                     {DA: {"r_base": 18.0, "n_days": 7, "r_good_base": 18.0,
                           "n_days_good": 7}})
    r3 = {x["cls"]: x for x in b3["rows"]}[DA]
    t("U1.8 快於基準日 [U-A]/[U-D] ≤1e-6（speed_net=−20）",
      identity_error_ua(r3) <= UA_TOL and identity_error_ud(r3) <= UD_TOL
      and abs(r3["speed_net"] + 20.0) < 1e-9)
    t("U1.9 speed_gain=20、speed_loss=0、slow_est_n=0（gain 不計損失）",
      r3["speed_gain"] == 20.0 and r3["speed_loss"] == 0.0 and r3["slow_est_n"] == 0)
    t("U1.9b 良品尺 slow_good<0＝比基準快 ⇒ 不進 ranked（好消息不是損失）",
      r3["slow_good"] < 0 and all(c["bucket"] != "slow" for c in r3["ranked"]))
    # [U-B] 輸入不一致（rework 源自稱 rework_n=99）⇒ identity False＋caveat（不修數字）
    b4 = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12),
                     _rw_one(DA, PH, 320, 300, rework_n=99), base_da)
    r4 = {x["cls"]: x for x in b4["rows"]}[DA]
    t("U1.10 [U-B] 破＝輸入不一致 ⇒ identity_ok=False＋caveat 揭露（[U-D] 仍構造性平）",
      r4["identity_ok"] is False and r4["rework_n"] == 99
      and identity_error_ud(r4) <= UD_TOL
      and any("inconsistent" in c for c in b4["diag"]["caveats"]))
    # producing≤0 但 attempts>0＝來源矛盾 ⇒ 速率桶與切分都不硬給
    att_z = _demo_att(DA, 1, 0.0, 24.0, 0.0, 0.0)
    b5 = build_board(DAY, {DA: att_z}, _grid_one(DA, 10, 1),
                     _rw_one(DA, PH, 50, 50), base_da)
    r5 = {x["cls"]: x for x in b5["rows"]}[DA]
    t("U1.11 producing=0 ∧ attempts>0 ⇒ speed_net/good_h/loss_by 全 None（矛盾不硬給）",
      r5["speed_net"] is None and r5["identity_ok"] is None and r5["r_y"] is None
      and r5["good_h"] is None and r5["loss_by"] is None and r5["ranked"] == [])
    # ── F5：[U-B] 破必須**短路 partial**（2026-08-03 verify）。無尺日（base 缺）以前會把
    #    「rework 源自稱值對不上」吞成 identity_ok=None ⇒ 該日照樣進基準樣本。
    b_ub = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12),
                       _rw_one(DA, PH, 320, 300, rework_n=999), {})
    r_ub = {x["cls"]: x for x in b_ub["rows"]}[DA]
    t("U1.12 [U-B] 破 ⇒ identity_ok=False **短路 partial**（無尺日也不得吞成 None）",
      r_ub["identity_ok"] is False and r_ub["r_base"] is None
      and r_ub["loss_by"] is None
      and any("inconsistent" in c for c in b_ub["diag"]["caveats"]))
    # ── F2：[U-D] 容差隨尺縮放。attrib 負殘差 clamp 會讓四桶和比 total−producing 多幾個
    #    1e-6 機時；乘上尺（19 顆/機時）就變成 1.9e-5 顆 > 舊的固定 1e-6 門檻 ⇒ 構造性平的
    #    帳被判 False。真正的來源不一致是整數顆級、照樣抓得到（下一案）。
    att_clamp = dict(att_da, unexplained_h=att_da["unexplained_h"] + 1e-6)
    rcl = {x["cls"]: x for x in build_board(
        DAY, {DA: att_clamp}, _grid_one(DA, 280, 12),
        _rw_one(DA, PH, 320, 300), base_da)["rows"]}[DA]
    t("U1.13 [U-D] 容差隨尺縮放：post-clamp 形（四桶多 1e-6h × 尺 19）⇒ identity_ok True",
      rcl["identity_ok"] is True and identity_error_ud(rcl) > UD_TOL
      and identity_error_ud(rcl) <= 19.0 * 1e-5 + UD_TOL)
    att_offby = dict(att_da, unexplained_h=att_da["unexplained_h"] + 1.0)
    rob = {x["cls"]: x for x in build_board(
        DAY, {DA: att_offby}, _grid_one(DA, 280, 12),
        _rw_one(DA, PH, 320, 300), base_da)["rows"]}[DA]
    t("U1.13b 整數顆級不平（四桶多 1.0h × 尺 19 ＝19 顆）⇒ identity_ok 仍 False（抓得到）",
      rob["identity_ok"] is False and identity_error_ud(rob) > 18.0)
    # ── F3：跨窗零嘗試日（機台在生產、嘗試列錨在窗外＝跨午夜 lot）＝來源窗口不一致。
    #    r_y 若落 0.0 會進 7d 中位樣本把基準永久拉低（比顯示層錯更嚴重）。
    b_cw = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12),
                       _rw_one(DA, PH, 0, 0), base_da)
    r_cw = {x["cls"]: x for x in b_cw["rows"]}[DA]
    t("U1.14 跨窗零嘗試（producing>0 ∧ attempts=0）⇒ r_y/speed_net/loss_by 全 None"
      "＋note=counts_window_mismatch＋caveat（r_y=0.0 會毒化 7d 基準）",
      r_cw["r_y"] is None and r_cw["speed_net"] is None and r_cw["loss_by"] is None
      and r_cw["identity_ok"] is None and r_cw["ranked"] == []
      and r_cw["note"] == NOTE_COUNTS_WINDOW
      and any("anchored outside" in c for c in b_cw["diag"]["caveats"]))
    t("U1.14b ★ 真全停日（producing==0 ∧ attempts==0）行為零變動（F3 防回歸負斷言）",
      r2["speed_net"] == 0.0 and r2["note"] != NOTE_COUNTS_WINDOW
      and (r2["good_h"], r2["rework_h"], r2["scrap_h"]) == (0.0, 0.0, 0.0)
      and r2["identity_ok"] is True)
    # ── F7：薄生產日的 slow_good<0 ＝口徑差（產出掛在幾乎沒跑的窗），不是「比基準快」。
    b_tg = build_board(DAY, {DA: _demo_att(DA, 2, 0.0, 20.0, 4.0, 0.0)},
                       _grid_one(DA, 50, 3), _rw_one(DA, PH, 0, 0), base_da)
    rtg = {x["cls"]: x for x in b_tg["rows"]}[DA]
    t("U1.15 薄生產日（good_h=0）slow_good<0 但 speed_gain_good=None＋hero 不累加假 gain"
      "（loss_by/[U-D] 原值不動）",
      rtg["slow_good"] < 0 and rtg["speed_gain_good"] is None
      and b_tg["hero"]["slow_gain_pcs"] is None
      and identity_error_ud(rtg) <= UD_TOL)
    # ── MED A-1 端到端：Qlik 空母體日（grid 列在、pass+fail=0）＝`counts_window_mismatch`
    #    的鏡像。舊碼在此落 `r_good = 0/good_h = 0.0` ⇒ 進 7d 中位 ⇒ `r_good_base=0.0`
    #    ⇒ 整板「未使用 ≈0、還比基準快 N 顆」而且一句 caveat 都沒有。
    b_qw = build_board(DAY, {DA: att_da}, _grid_one(DA, 0, 0),
                       _rw_one(DA, PH, 320, 300), base_da)
    r_qw = {x["cls"]: x for x in b_qw["rows"]}[DA]
    t("U1.16 Qlik 空母體日 ⇒ r_good=None（**不落 0.0**）、loss_by/ranked 不進 slow、"
      "note=qlik_window_mismatch＋caveat；三段切分（機時）仍照算",
      r_qw["r_good"] is None and r_qw["loss_by"] is None and r_qw["ranked"] == []
      and r_qw["slow_good"] is None and r_qw["identity_ok"] is None
      and r_qw["note"] == NOTE_QLIK_WINDOW
      and r_qw["good_h"] is not None
      and abs(r_qw["good_h"] + r_qw["rework_h"] + r_qw["scrap_h"]
              - r_qw["producing_h"]) < 1e-9
      and any("Qlik population is empty" in c for c in b_qw["diag"]["caveats"]))
    t("U1.16b 空母體但**零嘗試**（全停日型）不受影響：仍走全停日分支（三段 0、[U-D] 照算）",
      r2["note"] != NOTE_QLIK_WINDOW and r2["identity_ok"] is True)
    t("U1.15b 正常快站（good_h ≥ MIN_PROD_MH）⇒ speed_gain_good ＝ −slow_good > 0",
      r3["speed_gain_good"] is not None and r3["speed_gain_good"] > 0
      and abs(r3["speed_gain_good"] + r3["slow_good"]) < 1e-9
      and r3["good_h"] >= MIN_PROD_MH)

    # =========================================================================
    # U2x partial 模式（首暖／不可測；[U-D] 量不到 ⇒ None **不是 False**）
    # =========================================================================
    bw = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12),
                     _rw_one(DA, PH, 320, 300),
                     {DA: {"r_base": None, "n_days": 2, "r_good_base": None,
                           "n_days_good": 2}})
    rw_ = {x["cls"]: x for x in bw["rows"]}[DA]
    t("U2.1 兩尺皆首暖 ⇒ identity_ok=None、speed_net=None、loss_by=None（[U-A]/[U-D]「—」）",
      rw_["identity_ok"] is None and rw_["speed_net"] is None
      and rw_["loss_by"] is None and rw_["loss_actionable"] is None)
    t("U2.2 partial 下 [U-B] 與報廢照算照顯（rework=20、fail=12、bridge=8）",
      rw_["rework_n"] == 20 and rw_["qlik_fail"] == 12 and rw_["unit_bridge"] == 8)
    t("U2.3 首暖 note=base_warming＋兩個天數欄隨值回傳（base_n_days/base_good_n_days）",
      rw_["note"] == NOTE_BASE_WARMING and rw_["base_n_days"] == 2
      and rw_["base_good_n_days"] == 2)
    t("U2.3b 提案 A 三段不吃基準 ⇒ 首暖日仍算得出 good_h/r_good（可用性不被基準綁架）",
      rw_["good_h"] is not None and rw_["r_good"] is not None)

    # producing_h 不可測四種 note 各一案（zero_len/nosig/src_err/no_time_src）
    for note_key, att_in in (
            (wattr.NOTE_ZERO_LEN, {DA: _demo_att_unm(DA, wattr.NOTE_ZERO_LEN)}),
            (wattr.NOTE_NOSIG, {DA: _demo_att_unm(DA, wattr.NOTE_NOSIG)}),
            (wattr.NOTE_SRC_ERR, {DA: _demo_att_unm(DA, wattr.NOTE_SRC_ERR)}),
            (NOTE_NO_TIME_SRC, {})):
        bn = build_board(DAY, att_in, _grid_one(DA, 280, 12),
                         _rw_one(DA, PH, 320, 300), base_da)
        rn = {x["cls"]: x for x in bn["rows"]}[DA]
        t(f"U2.4 producing 不可測（{note_key}）⇒ 速率桶/損失「—」、報廢/重工照算、note 講對原因",
          rn["speed_net"] is None and rn["r_y"] is None and rn["identity_ok"] is None
          and rn["loss_by"] is None and rn["good_h"] is None
          and rn["qlik_fail"] == 12 and rn["rework_n"] == 20 and rn["note"] == note_key)

    # 只缺良品尺（v2 舊檔推不出 r_good 的站）——**不連坐嘗試軸**
    bnr = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12),
                      _rw_one(DA, PH, 320, 300),
                      {DA: {"r_base": 22.0, "n_days": 7, "r_good_base": None,
                            "n_days_good": 1}})
    rnr = {x["cls"]: x for x in bnr["rows"]}[DA]
    t("U2.5 只缺良品尺 ⇒ loss_by/ranked/actionable「—」但 [U-A]/[U-B]/報廢照算＋note 首暖",
      rnr["loss_by"] is None and rnr["ranked"] == []
      and rnr["loss_actionable"] is None and rnr["speed_net"] is not None
      and rnr["rework_n"] == 20 and rnr["qlik_fail"] == 12
      and rnr["identity_ok"] is None and rnr["note"] == NOTE_BASE_WARMING)
    # 報廢腿缺（grid degraded）⇒ 切分不成立 ⇒ [U-D] partial（**不是 False**＝不死鎖）
    bnq = build_board(DAY, {DA: att_da}, None, _rw_one(DA, PH, 320, 300), base_da)
    rnq = {x["cls"]: x for x in bnq["rows"]}[DA]
    t("U2.6 報廢腿缺 ⇒ [U-D] 量不到 ⇒ identity_ok=None（partial，**不是 False**）",
      rnq["identity_ok"] is None and rnq["loss_by"] is None
      and identity_error_ud(rnq) == 0.0)

    # =========================================================================
    # U3x 基準（base_rates）——雙尺各自取樣
    # =========================================================================
    def _doc_day(dd, r_y, wo="ok", identity=True, ver=2, r_good=None, no_scrap=False):
        """日帳 doc fixture。`ver` ∈ {1,2,3}；units 的數字讓推導後 r_good ≡ r_y
        （attempts 100 / rework 20 / pass 72 / fail 8 ⇒ good_h = 72/r_y）。"""
        st = {**{k: None for k in wattr._DOC_STATION_KEYS}, "cls": DA,
              "unmeasurable": False}
        if ver != 1:
            u = {k: None for k in _DOC_UNIT_KEYS}
            u.update({"attempts": 100, "first_attempts": 80, "rework_n": 20,
                      "qlik_pass": (None if no_scrap else 72),
                      "qlik_fail": (None if no_scrap else 8),
                      "r_y": r_y, "r_base": None, "base_n_days": 0,
                      "speed_net": None, "unit_bridge": 3, "identity_ok": identity,
                      "scrap_src_degraded": False, "note": None})
            if ver == 3:
                u["r_good"] = r_good
            st["units"] = u
        vers = {1: LEDGER_SCHEMA_V1, 2: LEDGER_SCHEMA_V2,
                3: LEDGER_SCHEMA_V3, 4: LEDGER_SCHEMA_V4, 5: LEDGER_SCHEMA_VER}
        return {"schema_ver": vers[ver], "day": dd, "source": "live",
                "wo_source_status": wo, "computed_at": f"{dd}T23:59:00",
                "grid_min": 30, "thresholds": {}, "line": {}, "stations": [st]}

    days5 = [f"2026-07-{26 + k}" for k in range(5)]                # 26..30（day=31 的前窗）
    docs5 = [_doc_day(dd, r) for dd, r in zip(days5, (10.0, 30.0, 20.0, 25.0, 15.0))]
    br = base_rates(docs5, DAY)
    t("U3.1 中位數（奇數 5 樣本 → 20.0）；v2 檔的良品尺由推導取樣（同樣 5 天）",
      br[DA]["r_base"] == 20.0 and br[DA]["n_days"] == 5
      and br[DA]["r_good_base"] == 20.0 and br[DA]["n_days_good"] == 5)
    br4 = base_rates(docs5[:4], DAY)
    t("U3.2 中位數（偶數 4 樣本 → (20+25)/2=22.5）", br4[DA]["r_base"] == 22.5)
    br3 = base_rates(docs5[:3], DAY)
    t("U3.3 樣本 <4 ⇒ r_base/r_good_base=None（首暖）、n_days 照回",
      br3[DA]["r_base"] is None and br3[DA]["r_good_base"] is None
      and br3[DA]["n_days"] == 3 and br3[DA]["n_days_good"] == 3)
    brd = base_rates(docs5[:3] + [_doc_day("2026-07-29", 99.0, wo="degraded")], DAY)
    t("U3.4 degraded 日不入基準樣本（兩把尺都不入）",
      brd[DA]["n_days"] == 3 and brd[DA]["n_days_good"] == 3)
    brf = base_rates(docs5[:3] + [_doc_day("2026-07-29", 99.0, identity=False)], DAY)
    t("U3.5 identity_ok=False 日不入樣本（identity=None 首暖日照收＝不死鎖）",
      brf[DA]["n_days"] == 3
      and base_rates(docs5[:4] + [_doc_day("2026-07-30", 40.0, identity=None)],
                     DAY)[DA]["n_days"] == 5)
    t("U3.6 v1 檔無 units ⇒ 不供樣本（不炸）",
      base_rates([_doc_day("2026-07-28", 10.0, ver=1)], DAY) == {})
    t("U3.7 day 自己的檔不入窗（重算日不得自我引用）",
      base_rates([_doc_day(DAY, 50.0)] + docs5, DAY)[DA]["n_days"] == 5)
    # ★ 雙尺各自獨立取樣：報廢腿缺的日子推不出 r_good ⇒ **只掉良品尺那一側**
    docs_mix = [_doc_day(dd, rr, no_scrap=(i >= 3))
                for i, (dd, rr) in enumerate(zip(days5, (10.0, 30.0, 20.0, 25.0, 15.0)))]
    bm = base_rates(docs_mix, DAY)
    t("U3.8 雙尺各自取樣：推不出 r_good 的日子只掉良品尺（n_days=5、n_days_good=3）",
      bm[DA]["n_days"] == 5 and bm[DA]["n_days_good"] == 3
      and bm[DA]["r_base"] == 20.0 and bm[DA]["r_good_base"] is None)
    docs_v3 = [_doc_day(dd, rr, ver=3, r_good=g)
               for dd, rr, g in zip(days5, (10.0, 30.0, 20.0, 25.0, 15.0),
                                    (5.0, 9.0, 7.0, 8.0, 6.0))]
    b3r = base_rates(docs_v3, DAY)
    t("U3.9 v3 檔 r_good 直讀（中位 7.0）、r_y 中位仍 20.0＝兩把尺互不干擾",
      b3r[DA]["r_good_base"] == 7.0 and b3r[DA]["r_base"] == 20.0)
    t("U3.9b v2/v3 混餵：兩種來源的樣本併在同一個中位裡（天數帳平）",
      base_rates(docs_v3[:2] + docs5[2:], DAY)[DA]["n_days_good"] == 5)
    # ★ F3 配套：跨窗零嘗試日的 units 塊（r_y=None、r_good 推不出）**天然不入兩把尺的樣本**
    doc_cw = _doc_day("2026-07-29", 99.0)
    doc_cw["stations"][0]["units"] = _units_block(r_cw)
    bcw = base_rates(docs5[:3] + [doc_cw], DAY)
    t("U3.11 F3：跨窗零嘗試日（r_y None）不入基準樣本（兩把尺都不收 ⇒ 中位不被 0 拉低）",
      bcw[DA]["n_days"] == 3 and bcw[DA]["n_days_good"] == 3)
    # ★ F5 配套：[U-B] 破的日子 identity_ok=False ⇒ 被基準排除（這正是短路的目的）
    doc_ub = _doc_day("2026-07-30", 99.0, identity=r_ub["identity_ok"])
    t("U3.12 F5：identity_ok=False 的日子被基準排除（短路後 partial 日也逃不掉）",
      r_ub["identity_ok"] is False
      and base_rates(docs5[:3] + [doc_ub], DAY)[DA]["n_days"] == 3)
    # ★ MED A-1 配套（洞 2）：空母體日的 units 塊**不得供樣本**——三道防線逐道釘。
    doc_qw = _doc_day("2026-07-29", 99.0)
    doc_qw["stations"][0]["units"] = _units_block(r_qw)
    bqw = base_rates(docs5[:3] + [doc_qw], DAY)
    t("U3.13 空母體日不入良品尺樣本（r_good=None ⇒ n_days_good 不增；r_y 那側照收）",
      bqw[DA]["n_days_good"] == 3 and bqw[DA]["n_days"] == 4)
    poison = [_doc_day(dd, 20.0, ver=3, r_good=0.0)
              for dd in ("2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29")]
    bp = base_rates(poison, DAY)
    t("U3.14 舊檔已落地的毒值（v3 檔 r_good=0.0）不入樣本 ⇒ r_good_base=None（無尺、非 0）",
      bp[DA]["r_good_base"] is None and bp[DA]["n_days_good"] == 0
      and bp[DA]["r_base"] == 20.0)
    dupe = _doc_day("2026-07-28", 10.0)
    dupe["stations"] = dupe["stations"] + [json.loads(json.dumps(dupe["stations"][0]))]
    t("U3.10 同日同站重複列只算一個樣本（天數不虛胖、中位不被加權兩次）",
      base_rates(docs5[:2] + [dupe], DAY)[DA]["n_days"] == 3
      and base_rates(docs5[:2] + [dupe], DAY)[DA]["n_days_good"] == 3)

    # =========================================================================
    # U4x 橋接三態 ＋ 分母下限
    # =========================================================================
    bz = build_board(DAY, {DA: att_da}, _grid_one(DA, 288, 12),
                     _rw_one(DA, PH, 320, 300), base_da)
    t("U4.1 unit_bridge=0（300 = 288+12）",
      {x["cls"]: x for x in bz["rows"]}[DA]["unit_bridge"] == 0)
    bneg = build_board(DAY, {DA: att_da}, _grid_one(DA, 300, 10),
                       _rw_one(DA, PH, 320, 300), base_da)
    rneg = {x["cls"]: x for x in bneg["rows"]}[DA]
    t("U4.2 unit_bridge 負（錨落昨日、活動在他日）＝−10、identity 照過（bridge 已不在恆等式裡）",
      rneg["unit_bridge"] == -10 and rneg["identity_ok"] is True
      and identity_error_ud(rneg) <= UD_TOL)
    t("U4.3 unit_bridge 正＝8（昨日活動尚未進 Qlik 母體）", r["unit_bridge"] == 8)
    t("U4.4 rework_time_est_h ≈ rework_n/r_base（20/22）",
      abs(r["rework_time_est_h"] - 20 / 22.0) < 1e-9)
    # 2026-08-02 live 事故：瞬時段站 producing≈0.0017h ⇒ r_y=14 萬/機台小時**且落日帳**
    # ＝四天後成為基準中位數（垃圾永久化）。分母 < MIN_PROD_MH ⇒ r_y=None（速率不可測）。
    thin = build_board(DAY, {DA: _demo_att(DA, 1, 0.0017, 0.0, 0.0, 0.0)},
                       _grid_one(DA, 280, 12), _rw_one(DA, PH, 320, 300), {})
    t("U4.5 producing < MIN_PROD_MH ⇒ r_y=None（不落天價、不毒化日帳基準）",
      {x["cls"]: x for x in thin["rows"]}[DA]["r_y"] is None
      and {x["cls"]: x for x in thin["rows"]}[DA]["attempts"] == 320)
    ok_thin = build_board(DAY, {DA: _demo_att(DA, 1, MIN_PROD_MH, 0.0, 0.0, 0.0)},
                          _grid_one(DA, 280, 12), _rw_one(DA, PH, 320, 300), {})
    t("U4.6 producing 恰＝MIN_PROD_MH（含等於）⇒ r_y 照算",
      {x["cls"]: x for x in ok_thin["rows"]}[DA]["r_y"] is not None)
    t("U4.7 良品尺同一把下限：good_h < MIN_PROD_MH ⇒ r_good=None（同一顆雷、同一道守衛）",
      {x["cls"]: x for x in thin["rows"]}[DA]["r_good"] is None)

    # =========================================================================
    # U5x 報廢源 degraded / absorption
    # =========================================================================
    for bad_grid, lbl in ((None, "None"),
                          ({"unavailable": "boom"}, "unavailable"),
                          (dict(_grid_one(DA, 280, 12),
                                diag={"degraded": True}), "diag.degraded")):
        bg = build_board(DAY, {DA: att_da}, bad_grid, _rw_one(DA, PH, 320, 300), base_da)
        rg = {x["cls"]: x for x in bg["rows"]}[DA]
        t(f"U5.1 報廢源 {lbl} ⇒ scrap None＋scrap_src_degraded＋[U-D] partial＋loss_by None",
          rg["qlik_fail"] is None and rg["scrap_src_degraded"] is True
          and rg["identity_ok"] is None and rg["scrap_n"] is None
          and rg["loss_by"] is None and bg["absorbed_total"] is None)
    b_ab0 = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12, absorbed=0),
                        _rw_one(DA, PH, 320, 300), base_da)
    t("U5.2 absorption N=0 也出行（absorbed_total=0 不是 None）",
      b_ab0["absorbed_total"] == 0 and b_ab0["absorption"] == [])
    b_ab9 = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12, absorbed=9),
                        _rw_one(DA, PH, 320, 300), base_da)
    t("U5.3 吸收行帶站名與顆數（隱形吸收站 D6）",
      b_ab9["absorbed_total"] == 9
      and b_ab9["absorption"][0]["station"] == "segPostLamInspection")

    # =========================================================================
    # U6x 43 站邊界
    # =========================================================================
    t("U6.1 rows 恆 43、ord 1..43 升冪（不藏站）",
      len(b["rows"]) == 43 and [x["ord"] for x in b["rows"]] == list(range(1, 44)))
    blab = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12),
                       _rw_one(DA, PH, 320, 300), base_da,
                       labels={DA: "DA", Frame: "Frame"})
    rmap = {x["cls"]: x for x in blab["rows"]}
    t("U6.2 labels 命中者帶友善名、其餘 None（渲染 seg 名）",
      rmap[DA]["label"] == "DA" and rmap[Frame]["label"] == "Frame"
      and rmap["segWireBonding"]["label"] is None)
    t("U6.3 display＝QLIK_DISPLAY（ord 10 → Die Placement）",
      rmap[DA]["display"] == "Die Placement")
    t("U6.4 無時間源站 note=no_time_src（≠ src_err ≠ zero_len）",
      rmap["segWireBonding"]["note"] == NOTE_NO_TIME_SRC
      and rmap["segWireBonding"]["producing_h"] is None)
    bz2 = build_board(DAY, {DA: _demo_att_unm(DA, wattr.NOTE_ZERO_LEN)},
                      _grid_one(DA, 280, 12), _rw_one(DA, PH, 320, 300), base_da)
    rz = {x["cls"]: x for x in bz2["rows"]}[DA]
    t("U6.5 zero_len 站＝時間「—」＋顆數照算（scrap 12、rework 20）",
      rz["note"] == wattr.NOTE_ZERO_LEN and rz["producing_h"] is None
      and rz["qlik_fail"] == 12 and rz["rework_n"] == 20)
    bnorw = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12), None, base_da)
    rn2 = {x["cls"]: x for x in bnorw["rows"]}[DA]
    t("U6.6 rework 源缺席 ⇒ attempts/rework None＋note=no_rework_src（報廢照算、切分「—」）",
      rn2["attempts"] is None and rn2["rework_n"] is None
      and rn2["note"] == NOTE_NO_REWORK_SRC and rn2["qlik_fail"] == 12
      and rn2["good_h"] is None and rn2["loss_by"] is None)
    rw_cls = {"stations": {DA: {"attempts": 320, "first_attempts": 300, "rework_n": 20,
                                "n_fail_rows": 0}},
              "diag": {"degraded": True}}
    bcls = build_board(DAY, {DA: att_da}, _grid_one(DA, 280, 12), rw_cls, base_da)
    t("U6.7 rework mcl 降級 ⇒ cls 單鍵 join 仍接上＋caveat",
      {x["cls"]: x for x in bcls["rows"]}[DA]["attempts"] == 320
      and any("cls-only" in c for c in bcls["diag"]["caveats"]))

    # =========================================================================
    # U7x hero / units_line（含提案 E/H 線級）
    # =========================================================================
    hb = build_board(DAY, {DA: att_da, Frame: _demo_att(Frame, 2, 30.0, 6.0, 3.0, 1.0)},
                     _demo_grid(), _demo_rework(), _DEMO_BASE)
    t("U7.1 hero.by_bucket 三桶鍵齊、worst 有 cls/bucket/n",
      set(hb["hero"]["by_bucket"]) == set(UNIT_BUCKETS)
      and set(hb["hero"]["worst"]) == {"cls", "bucket", "n"})
    t("U7.2 hero.top 依 n 降冪、只收非零", all(
        hb["hero"]["top"][i][1] >= hb["hero"]["top"][i + 1][1]
        for i in range(len(hb["hero"]["top"]) - 1))
      and all(v > 0 for _b, v in hb["hero"]["top"]))
    t("U7.3 units_line Σ 帳（scrap=Σ qlik_fail 可算站）",
      hb["units_line"]["scrap_n"] == sum(x["qlik_fail"] for x in hb["rows"]
                                         if x["qlik_fail"] is not None)
      and hb["units_line"]["n_units_partial"] == hb["hero"]["n_partial"])
    t("U7.4 approx_buckets 標 slow（顯示層恆帶 ≈）",
      hb["hero"]["approx_buckets"] == ("slow",))
    ball_none = build_board(DAY, {}, None, None, None)
    t("U7.5 全源缺席 ⇒ by_bucket 全 None（絕不 0）＋top 空＋worst None",
      ball_none["hero"]["by_bucket"] == {"scrap": None, "rework": None, "slow": None}
      and ball_none["hero"]["top"] == [] and ball_none["hero"]["worst"] is None)
    t("U7.6 hero 提案 E/H：loss_by 七桶齊、ranked 依顆降冪且**永不含 unexplained**",
      set(hb["hero"]["loss_by"]) == set(LOSS_BUCKETS)
      and all(c["bucket"] != "unexplained" for c in hb["hero"]["loss_ranked"])
      and all(hb["hero"]["loss_ranked"][i]["pcs"]
              >= hb["hero"]["loss_ranked"][i + 1]["pcs"]
              for i in range(len(hb["hero"]["loss_ranked"]) - 1)))
    t("U7.7 hero loss_actionable＝Σ 六個可行動桶；loss_unexplained 獨立揭露、不進合計",
      abs(hb["hero"]["loss_actionable"]
          - sum(hb["hero"]["loss_by"][k] for k in ACTIONABLE_BUCKETS)) < 1e-5
      and hb["hero"]["loss_unexplained"] == hb["hero"]["loss_by"]["unexplained"]
      and hb["hero"]["loss_unexplained"] > 0)
    t("U7.8 n_ruled + n_no_ruler ＝ rows 數（帳平）、n_ruled＝有尺的列數",
      hb["hero"]["n_ruled"] + hb["hero"]["n_no_ruler"] == len(hb["rows"])
      and hb["hero"]["n_ruled"] == sum(1 for x in hb["rows"]
                                       if x["loss_by"] is not None))
    # ★ 群 B fix ③：推定子句兩腿必須同範圍——`unexplained_ruled_h` 只加**有尺**的列，
    #   且與 `loss_unexplained` 用同一組列 ⇒ 讀者拿 n÷h 反算得到的速率落在真實尺區間內。
    _ruled = [x for x in hb["rows"] if isinstance(x.get("loss_by"), dict)]
    t("U7.8a unexplained_ruled_h＝Σ 有尺列的 unexplained_h（與 loss_unexplained 同一組列）",
      abs(hb["hero"]["unexplained_ruled_h"]
          - sum(float(x["unexplained_h"]) for x in _ruled)) < 1e-5
      and abs(hb["hero"]["loss_unexplained"]
              - sum(float(x["loss_by"]["unexplained"]) for x in _ruled)) < 1e-5
      and hb["hero"]["unexplained_ruled_h"] > 0)
    t("U7.8a2 兩腿可互導：Σ(r_good_base×unexplained_h) 逐列＝loss_unexplained（同尺同列）",
      abs(sum(float(x["r_good_base"]) * float(x["unexplained_h"]) for x in _ruled)
          - hb["hero"]["loss_unexplained"]) < 1e-5)
    t("U7.8a3 全板無尺 ⇒ unexplained_ruled_h None（絕不 0）",
      ball_none["hero"]["unexplained_ruled_h"] is None)
    t("U7.8b units_line 帶提案 E 線級三欄（落日帳 v3 的 line 塊）",
      hb["units_line"]["loss_actionable"] == hb["hero"]["loss_actionable"]
      and hb["units_line"]["loss_unexplained"] == hb["hero"]["loss_unexplained"]
      and hb["units_line"]["n_ruled"] == hb["hero"]["n_ruled"])
    # ★ 線級 slow 絕不淨額：DA 慢（r_good_base 25 > 實際 20.76）、Frame 快（5.0 < 8.32）
    two_base = {DA: {"r_base": 22.0, "n_days": 7, "r_good_base": 25.0, "n_days_good": 7},
                Frame: {"r_base": 9.0, "n_days": 7, "r_good_base": 5.0, "n_days_good": 7}}
    hb2 = build_board(DAY, {DA: att_da, Frame: _demo_att(Frame, 2, 30.0, 6.0, 3.0, 1.0)},
                      _grid_many({DA: (280, 12), Frame: (200, 40)}),
                      _rw_many({DA: (320, 300), Frame: (260, 250)}), two_base)
    rmap2 = {x["cls"]: x for x in hb2["rows"]}
    t("U7.9 線級 slow 絕不淨額：Σ max(0,slow) 只收慢的站、快的站進 slow_gain_pcs",
      rmap2[DA]["slow_good"] > 0 and rmap2[Frame]["slow_good"] < 0
      and abs(hb2["hero"]["loss_by"]["slow"] - rmap2[DA]["slow_good"]) < 1e-5
      and abs(hb2["hero"]["slow_gain_pcs"] + rmap2[Frame]["slow_good"]) < 1e-5)
    t("U7.10 loss_worst 逐桶指名最痛的站（slow→DA、scrap→Frame）",
      hb2["hero"]["loss_worst"]["slow"]["cls"] == DA
      and hb2["hero"]["loss_worst"]["scrap"]["cls"] == Frame
      and set(hb2["hero"]["loss_worst"]) == set(LOSS_BUCKETS))
    t("U7.11 全板無尺 ⇒ hero.loss_* 全 None（絕不 0）、n_ruled=0／n_no_ruler=43",
      ball_none["hero"]["loss_by"] is None
      and ball_none["hero"]["loss_ranked"] is None
      and ball_none["hero"]["loss_actionable"] is None
      and ball_none["hero"]["slow_gain_pcs"] is None
      and ball_none["hero"]["n_ruled"] == 0
      and ball_none["hero"]["n_no_ruler"] == 43)

    # =========================================================================
    # U8x 日帳 v1/v2/v3
    # =========================================================================
    att_board = wattr.daily_board({DA: wattr.build_ledger(
        DA, [{"eid": "E1", "records": [{"state": wattr.PROD_STATE,
                                        "start": wattr._dt("2026-07-31T00:00:00"),
                                        "end": wattr._dt("2026-08-01T00:00:00")}]}],
        {}, [(wattr._dt("2026-07-31T00:00:00"), 3.0, 30, 1)], None,
        {"rate_per_mh": 20.0},
        win=(wattr._dt("2026-07-31"), wattr._dt("2026-08-01")))},
        day=DAY, source="live", wo_source_status="ok", computed_at=f"{DAY}T23:59:00")
    doc4 = ledger_doc(att_board, b, day=DAY, source="live", wo_source_status="ok",
                      computed_at=f"{DAY}T23:59:00")
    # ★ 2026-09-04：`should_overwrite` 的「完好 v4」＝**doc 級 rw_axis 真的是 True** 的那種。
    #   `doc4` 本身刻意不帶主張（U8.6d 釘「ledger_doc 不猜」）＝`91f5634` 之前的檔形，
    #   放寬後它**應該**被判成可覆寫 ⇒ 期望 False 的那幾顆釘一律改用 `doc4ok`。
    doc4ok = dict(doc4, rw_axis=True, rw_reason="ok", rw_anchor=RW_ANCHOR)   # v8：錨證據也要在
    t("U8.1 v4 doc schema_ver 正確且 JSON 可序列化",
      doc4["schema_ver"] == LEDGER_SCHEMA_VER == "board.daily_ledger.v8"
      and json.dumps(doc4, sort_keys=True) is not None)
    back = parse_ledger_doc(doc4)
    t("U8.2 v4 round-trip 等於自己（parse(doc)==doc；含 doc 級三個新鍵）", back == doc4)
    t("U8.3 unit-only 站補進 doc（時間欄 None＋note=no_time_src、units 保留）",
      any(s["cls"] == Frame and s["note"] == NOTE_NO_TIME_SRC
          and s["units"] is not None for s in doc4["stations"]))
    doc1 = wattr.ledger_doc(att_board, day=DAY, source="live", wo_source_status="ok")
    back1 = parse_ledger_doc(doc1)
    t("U8.4 v1 檔照讀、units 補 None 不炸",
      back1 is not None and back1["schema_ver"] == LEDGER_SCHEMA_V1
      and all(s["units"] is None for s in back1["stations"])
      and back1["line"]["units_line"] is None)
    t("U8.5 未知 schema_ver → None（絕不猜）",
      parse_ledger_doc(dict(doc4, schema_ver="board.daily_ledger.v9")) is None
      and parse_ledger_doc("nope") is None and parse_ledger_doc(None) is None)
    doc_v2 = _doc_day("2026-07-29", 20.0)
    p_v2 = parse_ledger_doc(doc_v2)
    t("U8.5b v2 檔 parse：schema_ver **原樣保留 v2**（不冒充 v3/v4）、v3 新七欄補 None",
      p_v2 is not None and p_v2["schema_ver"] == LEDGER_SCHEMA_V2
      and all(p_v2["stations"][0]["units"][k] is None
              for k in ("good_h", "rework_h", "scrap_h", "r_good", "r_good_base",
                        "base_good_n_days", "slow_good"))
      and p_v2["stations"][0]["units"]["r_y"] == 20.0)
    t("U8.5c v4 station units 過 `_DOC_UNIT_KEYS` 白名單（外來鍵一律剝除）",
      set(parse_ledger_doc(doc4)["stations"][0]["units"] or {}) <= set(_DOC_UNIT_KEYS)
      and "bogus" not in (parse_ledger_doc(
          json.loads(json.dumps(doc4).replace('"note": null',
                                              '"note": null, "bogus": 1'))
      )["stations"][0]["units"] or {}))
    t("U8.6 should_overwrite：None→True／v1→True／**v2→True／v3→True（升 v4）**／完好 v4→False",
      should_overwrite(None) is True and should_overwrite(doc1) is True
      and should_overwrite(doc_v2) is True
      and should_overwrite(_doc_day("2026-07-29", 20.0, ver=3, r_good=20.0)) is True
      and should_overwrite(doc4ok) is False)
    # ── M3 v4：站列 `rw`／`families` ＋ doc 級 `rw_axis`/`rw_reason`/`families_status` ──
    doc_v3 = _doc_day("2026-07-29", 20.0, ver=3, r_good=20.0)
    p_v3 = parse_ledger_doc(doc_v3)
    t("U8.6a v3 檔照讀：schema_ver **原樣保留 v3**、站列 rw/families 補 None、doc 級三鍵補 None"
      "（None ≠ {}＝沒抓 ≠ 抓到零筆）",
      p_v3 is not None and p_v3["schema_ver"] == LEDGER_SCHEMA_V3
      and p_v3["stations"][0]["rw"] is None
      and p_v3["stations"][0]["families"] is None
      and all(p_v3[k] is None for k in _DOC_V4_KEYS)
      and p_v3["stations"][0]["units"]["r_good"] == 20.0)
    t("U8.6b v1 檔亦補 doc 級三鍵 None（讀端一把尺，不必逐版分支）",
      all(back1.get(k) is None for k in _DOC_V4_KEYS))
    doc_rwm = ledger_doc(att_board, b, day=DAY, source="live", wo_source_status="ok",
                         computed_at=f"{DAY}T23:59:00",
                         rw_axis=True, rw_reason="ok", families_status="skipped:budget",
                         rw_anchor=RW_ANCHOR)
    t("U8.6c doc 級三鍵 round-trip（呼叫端傳什麼就寫什麼、parse 原樣回）",
      doc_rwm["rw_axis"] is True and doc_rwm["rw_reason"] == "ok"
      and doc_rwm["families_status"] == "skipped:budget"
      and parse_ledger_doc(doc_rwm) == doc_rwm)
    t("U8.6d ledger_doc **不猜** doc 級三鍵：沒傳就是 None（不是 False、不是 'ok'）",
      doc4["rw_axis"] is None and doc4["rw_reason"] is None
      and doc4["families_status"] is None)
    t("U8.6e should_overwrite：**v4 且 rw_axis is not True → True**（沒站在應生產時段軸上"
      "＝可恢復降級，同 src_err）；rw_axis True → 不因此放行",
      should_overwrite(dict(doc4, rw_axis=False)) is True
      and should_overwrite(dict(doc4, rw_axis=True, rw_anchor=RW_ANCHOR)) is False
      and should_overwrite(doc_rwm) is False)
    t("U8.6f ★ 2026-09-04：**v4 且 doc 級 rw_axis 是 None（wo ok、units 在）→ True**——"
      "`91f5634` 之前寫出的檔全長這樣（doc null ＋ 站列有 rw），月報 n_days_rw 數不到它們；"
      "舊規則不放行覆寫 ⇒ 那些日子永久卡死。放寬後昨天那份下次 live 重建就自癒",
      should_overwrite(dict(doc4, rw_axis=None)) is True
      and doc4["rw_axis"] is None and should_overwrite(doc4) is True)
    t("U8.6g ★ 反面：doc 級 rw_axis True 的完好 v4 → **False**（放寬不得變成每天重寫好檔"
      "——carry-forward 之後好檔的主張不會再被抹回 None）",
      should_overwrite(doc4ok) is False
      and should_overwrite(dict(doc4ok, families_status=None)) is False)
    # 站列 rw 是引擎產的，透傳到 doc 並可 round-trip（`families` 由呼叫端掛）
    _rw_led = wattr.build_ledger(
        DA, [{"eid": "E1", "records": [{"state": wattr.PROD_STATE,
                                        "start": wattr._dt("2026-07-31T08:00:00"),
                                        "end": wattr._dt("2026-07-31T16:00:00")}]}],
        {}, [(wattr._dt("2026-07-31T00:00:00"), 3.0, 30, 1)], None, None,
        win=(wattr._dt("2026-07-31"), wattr._dt("2026-08-01")),
        rw_by_eid={"E1": [(wattr._dt("2026-07-31T08:00:00"),
                           wattr._dt("2026-07-31T18:00:00"))]})
    _rw_led["families"] = {"E1": {"Plover": 5}}
    doc_rw = ledger_doc(wattr.daily_board({DA: _rw_led}, day=DAY, source="live",
                                          wo_source_status="ok"), None,
                        day=DAY, source="live", wo_source_status="ok",
                        computed_at=f"{DAY}T23:59:00", rw_axis=True, rw_reason="ok",
                        families_status="ok")
    _srw = {s["cls"]: s for s in doc_rw["stations"]}[DA]
    t("U8.6f 站列 `rw`／`families` 透傳進 v4 doc 並 round-trip（EID／族名不被 §24 scrub 剝掉）",
      _srw["rw"]["src"] == "erw" and _srw["rw"]["n_eid"] == 1
      and abs(_srw["rw"]["planned_sec"] - 10 * 3600.0) < 1e-6
      and abs(_srw["rw"]["producing_sec"] - 8 * 3600.0) < 1e-6
      and _srw["families"] == {"E1": {"Plover": 5}}
      and parse_ledger_doc(doc_rw) == doc_rw)
    no_units = json.loads(json.dumps(doc4))
    for s in no_units["stations"]:
        s["units"] = None
    t("U8.7 should_overwrite：units 缺→True／wo degraded→True／scrap degraded→True",
      should_overwrite(no_units) is True
      and should_overwrite(dict(doc4, wo_source_status="degraded")) is True
      and should_overwrite(ledger_doc(att_board, build_board(
          DAY, {DA: att_da}, None, _rw_one(DA, PH, 320, 300), base_da),
          day=DAY, source="live", wo_source_status="ok")) is True)
    # ── HIGH-1：attrib-only 落地（degraded 日每 15 分鐘一次）**不得**把已落地的 units 毀掉。
    #    `ledger_doc(att_board, None)` 就是那條路徑產出的文件（units 全 None）。
    doc_att_only = ledger_doc(att_board, None, day=DAY, source="live",
                              wo_source_status="degraded", computed_at=f"{DAY}T23:59:00")
    t("U8.18a 前提：attrib-only doc 的 units 確實全 None（毀損迴圈的輸入形）",
      all(s.get("units") is None for s in doc_att_only["stations"])
      and doc_att_only["line"].get("units_line") is None)
    carried = carry_forward_units(doc_att_only, doc4)
    cmap = {s["cls"]: s for s in carried["stations"]}
    dmap4 = {s["cls"]: s for s in doc4["stations"] if s.get("units")}
    t("U8.18 ★ carry_forward：attrib-only 落地**不得移除既有 units**（逐站等於既有檔）"
      "＋既有檔獨有的 unit-only 站整列補回＋line.units_line 一併保留",
      all(cmap[c]["units"] == dmap4[c]["units"] for c in dmap4)
      and Frame in cmap and cmap[Frame]["units"] is not None
      and carried["line"].get("units_line") == doc4["line"].get("units_line")
      and parse_ledger_doc(carried) is not None)
    t("U8.18b carry_forward 只補洞不覆蓋：新 doc 有 units 的站一律以**新值**為準",
      carry_forward_units(doc4, doc_att_only)["stations"]
      == doc4["stations"]
      and carry_forward_units(doc4, None) is doc4
      and carry_forward_units(doc4, {"schema_ver": "board.daily_ledger.v9"}) is doc4)
    t("U8.18c carry_forward 純函式：不 mutate 輸入（attrib-only doc 事後仍全 None）",
      all(s.get("units") is None for s in doc_att_only["stations"]))
    # ── HIGH-2／LOW A-4／LOW B-1／MED B-3：覆寫政策四條新判準
    doc_src_err = json.loads(json.dumps(doc4ok))
    doc_src_err["stations"][0]["note"] = wattr.NOTE_SRC_ERR
    t("U8.19 should_overwrite：任一站 src_err（當時讀不到、非事實）→ True；乾淨 v4 → False",
      should_overwrite(doc_src_err) is True and should_overwrite(doc4ok) is False)
    # ── ★ 2026-09-05 事故：回補計畫判 retry×34、落地全 already_ok（1.9 小時零落地）──────
    #   根因＝`roster_pad is None` 這條事實只寫在 `_backfill_ledger.doc_facts`，覆寫閘
    #   完全不知道 ⇒ 兩份會漂移的事實。修法＝抽 `rw_roster_unknown` 放這裡、回補 import 重用。
    _RW5_OK = {"planned_sec": 3600, "health_down_sec": 0, "avail_down_sec": 0,
               "producing_sec": 3600, "n_eid": 1, "eids": ["E1"], "roster_pad": True}

    def _doc_rw(**kw):
        """`doc4ok` 的複本，第一站掛一份 rw（`kw` 覆蓋 `_RW5_OK` 的欄位；
           `roster_pad=...` 傳 `"drop"` ⇒ 整個鍵**不存在**＝真正的 legacy 形）。"""
        d = json.loads(json.dumps(doc4ok))
        rw = dict(_RW5_OK)
        rw.update({k: v for k, v in kw.items() if v != "drop"})
        for k, v in kw.items():
            if v == "drop":
                rw.pop(k, None)
        d["stations"][0]["rw"] = rw
        return d
    t("U8.23 ★ should_overwrite：新尺 rw 但 `roster_pad` **缺鍵**（2026-09-05 前的 legacy v5）"
      "→ True——這正是那 34 天的形，舊版判 False ⇒ `persist_ledger` 回 already_ok、白跑",
      should_overwrite(_doc_rw(roster_pad="drop")) is True
      and should_overwrite(_doc_rw(roster_pad=None)) is True)
    t("U8.23b ★ 反面：`roster_pad` 明確 True／False 都**不因此**放行覆寫（False 是本次的"
      "誠實降級、不是 legacy 未知；混掉兩種證據＝每天重寫好檔）",
      should_overwrite(_doc_rw(roster_pad=True)) is False
      and should_overwrite(_doc_rw(roster_pad=False)) is False)
    t("U8.23c ★ 沒有新尺 rw 的站不觸發（舊尺／`rw` 是 None／整份沒 rw）——那幾型另有"
      "`rw_upgrade`／`v5_upgrade` 先處理，這一條只撿「其餘都完整、只缺誠實欄」的 legacy v5",
      should_overwrite(_doc_rw(avail_down_sec=None, roster_pad="drop")) is False
      and should_overwrite(_doc_rw(avail_down_sec="drop", roster_pad="drop")) is False
      and should_overwrite(doc4ok) is False)
    # ── ★ #98 家族拆 PH（`SPEC-LANDING.md` §18.18）：日帳這一層的四根 ────────────
    #    ① 恆等式守衛 ② None ≠ {} ③ 回補判準單一出處 ④ 舊素材（家族欄缺席）不誤觸
    _fam_board = build_board(DAY, {DA: att_da}, _grid_many({DA: (280, 12)}),
                             _rw_many({DA: (320, 300)}), base_da)
    _fam_row = {x["cls"]: x for x in _fam_board["rows"]}[DA]
    _fam_zero = {x["cls"]: x for x in _fam_board["rows"]}["segSerialisation"]
    t("U8.24 ★ #98 站列 `units_by_family` ＝ grid 已算好的那一份照抄，"
      "恆等式 `qlik_pass ≡ Σ` 成立；零產出站＝`{}`（拆了但零）不是 None（沒拆）",
      _fam_row["units_by_family"] == {"Plover": 280}
      and sum(_fam_row["units_by_family"].values()) == _fam_row["qlik_pass"]
      and _fam_zero["units_by_family"] == {} and _fam_zero["qlik_pass"] == 0)
    t("U8.25 ★ 恆等式破 ⇒ 整片不落檔（回 None）——加不回總數的拆分寧可不寫（C-LD2）；"
      "家族欄缺席的舊素材同樣是 None（`None` ≠ `{}`）",
      family_counts_of({"Plover": 279}, 280) is None
      and family_counts_of({"Plover": 280}, 280) == {"Plover": 280}
      and family_counts_of(None, 280) is None
      and family_counts_of({"Plover": 280}, None) is None
      and family_counts_of({"Plover": 0, "Kestrel": 3}, 3) == {"Kestrel": 3}
      and list(family_counts_of({"Heron": 1, "Plover": 2}, 3)) == ["Plover", "Heron"])
    _fam_nofam = ledger_doc(att_board,
                            build_board(DAY, {DA: att_da},
                                        _grid_many({DA: (280, 12)}, family=False),
                                        _rw_many({DA: (320, 300)}), base_da),
                            day=DAY, source="live", wo_source_status="ok",
                            computed_at=f"{DAY}T23:59:00")
    t("U8.26 ★ 回補判準 `family_split_unknown` ＝ `should_overwrite` 與 "
      "`_backfill_ledger.plan_day` 的**同一支**：量得到報廢腿卻沒有拆分 ⇒ True；"
      "拆好的完整檔 ⇒ False（不得每天重寫好檔）",
      family_split_unknown(dict(_fam_nofam, rw_axis=True, rw_reason="ok")) is True
      and family_split_unknown(doc4ok) is False
      and should_overwrite(dict(_fam_nofam, rw_axis=True, rw_reason="ok")) is True)
    t("U8.27 `family_split_unknown` 對壞形不炸、也不主張（None／字串／未知版 ⇒ False）；"
      "報廢腿降級的站排除在外（那由既有降級條款管，重算一次只會永久可覆寫）",
      family_split_unknown(None) is False and family_split_unknown("x") is False
      and family_split_unknown({"schema_ver": "board.daily_ledger.v9"}) is False
      and family_split_unknown({
          "schema_ver": LEDGER_SCHEMA_VER, "day": DAY, "line": {}, "stations": [
              {"cls": DA, "units": {"qlik_pass": 5, "scrap_src_degraded": True}}]}) is False)
    t("U8.23d rw_roster_unknown 是純函式、對壞形不炸（None／字串／無站列／站列非 dict）",
      rw_roster_unknown(None) is False and rw_roster_unknown("x") is False
      and rw_roster_unknown({"stations": None}) is False
      and rw_roster_unknown({"stations": ["x", None]}) is False
      and rw_roster_unknown(_doc_rw(roster_pad="drop")) is True)
    # ── ★ 2026-09-04：覆寫政策的**第二道**＝壞帳不得蓋好帳（事故＝2026-08-11）────────
    #   `should_overwrite` 看的是既有檔；一份「整天零可測」的新板照樣能通過它，然後把
    #   21 站可測的好帳蓋掉、只在 `.bak-` 裡留下（`read_ledger_docs` 不認那個檔名）。
    doc_all_src = json.loads(json.dumps(doc4ok))
    for _s in doc_all_src["stations"]:
        _s["note"] = wattr.NOTE_SRC_ERR
        _s["unmeasurable"] = True
    doc_all_struct = json.loads(json.dumps(doc_all_src))
    for _s in doc_all_struct["stations"]:
        _s["note"] = wattr.NOTE_NO_FLEET        # 整天不可測、但**沒有任何 src_err**
    t("U8.19b ★ refuse_overwrite：整天零可測的新板**不得蓋掉**有可測站的既有檔"
      "（回明確理由，既有檔與 .bak- 一個位元不動）",
      refuse_overwrite(doc_all_src, doc4ok) == REFUSE_SRC_ERR_ALL
      and REFUSE_SRC_ERR_ALL.startswith("refused:"))
    t("U8.19c refuse_overwrite 刻意窄：**部分**站 src_err 照樣放行（那是日常，"
      "由 should_overwrite 的 src_err 條保證明天會重寫）",
      refuse_overwrite(doc_src_err, doc4ok) is None)
    t("U8.19d 既有檔本來就零可測／無檔／壞檔 ⇒ 放行（新檔不比舊檔差，"
      "擋下來連 wo_h／units／rw 這些仍有效的欄位都寫不進去）",
      refuse_overwrite(doc_all_src, doc_all_src) is None
      and refuse_overwrite(doc_all_src, None) is None
      and refuse_overwrite(doc_all_src, "x") is None)
    t("U8.19e ★ 「整天結構性不可測」（全站 no_fleet、零 src_err）**不算壞帳** ⇒ 放行"
      "（那是真的量不到，重跑一百次一樣；src_err 重跑就有了）",
      src_err_all(doc_all_struct) is False
      and refuse_overwrite(doc_all_struct, doc4ok) is None)
    t("U8.19f src_err_all／any_measurable：缺 `unmeasurable` 鍵的站算**可測**（保守，"
      "誤判成壞帳會讓那一天永遠寫不進去）；無站列 ⇒ 不是壞帳",
      any_measurable({"stations": [{"cls": "a"}]}) is True
      and any_measurable({"stations": [{"cls": "a", "unmeasurable": True}]}) is False
      and any_measurable(None) is False
      and src_err_all({"stations": []}) is False
      and src_err_all({"stations": [{"note": wattr.NOTE_SRC_ERR}]}) is False)
    t("U8.19g 乾淨的新板永遠不被這道閘擋（它只認一種形）",
      refuse_overwrite(doc4ok, doc4ok) is None
      and refuse_overwrite(doc4, doc4ok) is None)
    # ---- R48（2026-09-30）：工單源降級的新帳不得蓋 `wo_source_status == ok` 的舊帳 ----
    _r48_ok = dict(doc4ok, wo_source_status="ok")
    _r48_dg = dict(doc4ok, wo_source_status="degraded")
    t("U8.19h ★ R48 degraded_would_clobber_ok：舊 ok ∧ 新 degraded ⇒ 拒絕（理由 refused: 開頭）",
      degraded_would_clobber_ok(_r48_ok, _r48_dg) == REFUSE_DEGRADED_OVER_OK
      and REFUSE_DEGRADED_OVER_OK.startswith("refused:"))
    t("U8.19i R48 其餘組合照舊放行：無舊檔／壞舊檔、舊 degraded 蓋 degraded、"
      "ok 蓋 degraded、ok 蓋 ok",
      degraded_would_clobber_ok(None, _r48_dg) is None
      and degraded_would_clobber_ok("x", _r48_dg) is None
      and degraded_would_clobber_ok(_r48_dg, _r48_dg) is None
      and degraded_would_clobber_ok(_r48_dg, _r48_ok) is None
      and degraded_would_clobber_ok(_r48_ok, _r48_ok) is None)
    t("U8.19j R48 只有 \"ok\"（大小寫不拘）算好帳：新帳 None／error:*／demo 一律視為降級；"
      "舊帳非 ok 的任何值都不受保護",
      degraded_would_clobber_ok(_r48_ok, dict(_r48_ok, wo_source_status=None))
      == REFUSE_DEGRADED_OVER_OK
      and degraded_would_clobber_ok(_r48_ok, dict(_r48_ok, wo_source_status="error:x"))
      == REFUSE_DEGRADED_OVER_OK
      and degraded_would_clobber_ok(dict(_r48_ok, wo_source_status="OK"),
                                    dict(_r48_ok, wo_source_status="demo"))
      == REFUSE_DEGRADED_OVER_OK
      and degraded_would_clobber_ok(dict(_r48_ok, wo_source_status=None), _r48_dg) is None
      and degraded_would_clobber_ok(_r48_ok, dict(_r48_ok, wo_source_status="Ok")) is None)
    t("U8.19k R48 refuse_overwrite 串上這條：舊 ok 且帶 rw_retry_reason（should_overwrite 放行）"
      "∧ 新 degraded ⇒ refuse_overwrite 回 R48 理由；src_err_all 仍優先回原理由",
      refuse_overwrite(_r48_dg, _r48_ok) == REFUSE_DEGRADED_OVER_OK
      and refuse_overwrite(_r48_ok, _r48_dg) is None
      and refuse_overwrite(dict(doc_all_src, wo_source_status="degraded"), _r48_ok)
      == REFUSE_SRC_ERR_ALL)
    doc_cnt_hole = json.loads(json.dumps(doc4))
    for s in doc_cnt_hole["stations"]:
        if s.get("units"):
            s["units"]["attempts"] = None
            break
    doc_cnt_note = json.loads(json.dumps(doc4))
    for s in doc_cnt_note["stations"]:
        if s.get("units"):
            s["units"]["note"] = NOTE_NO_REWORK_SRC
            break
    t("U8.20 should_overwrite：計數腿**部分**缺口（attempts=None／no_rework_src）→ True"
      "（與報廢腿 degraded 對稱＝可恢復降級不得被鎖死）",
      should_overwrite(doc_cnt_hole) is True and should_overwrite(doc_cnt_note) is True)
    doc_one_scrap = json.loads(json.dumps(doc4ok))
    for s in doc_one_scrap["stations"]:
        if s.get("units"):
            s["units"]["scrap_src_degraded"] = True
            break
    doc_all_scrap = json.loads(json.dumps(doc4))
    for s in doc_all_scrap["stations"]:
        if s.get("units"):
            s["units"]["scrap_src_degraded"] = True
    t("U8.21 should_overwrite：**單站** scrap degraded（Qlik 缺列＝結構性）→ False；"
      "**整板** degraded（grid 掛了）→ True",
      should_overwrite(doc_one_scrap) is False and should_overwrite(doc_all_scrap) is True)
    t("U8.22 should_overwrite：未知/更新版 schema_ver 的檔 → **False（永不 clobber）**；"
      "無版號的壞檔 → True",
      should_overwrite(dict(doc4, schema_ver="board.daily_ledger.v9")) is False
      and should_overwrite(dict(doc4, schema_ver="")) is True
      and should_overwrite({"day": DAY, "stations": []}) is True
      and wattr.should_overwrite(dict(doc4, schema_ver="board.daily_ledger.v9")) is False
      and wattr.should_overwrite(dict(doc1, schema_ver="")) is True)
    dirty = json.loads(json.dumps(doc4))
    if dirty["stations"][0].get("units") is None:
        dirty["stations"][0]["units"] = {}
    dirty["stations"][0]["units"]["operator_name"] = "Alice"
    dirty["stations"][0]["units"]["headcount"] = 5
    clean = parse_ledger_doc(dirty)
    blob8 = json.dumps(clean, sort_keys=True, default=str)
    t("U8.8 _scrub/白名單：units 塊人員鍵被剝除（Alice/headcount 零出現）",
      "Alice" not in blob8 and "headcount" not in blob8 and "operator" not in blob8)
    t("U8.14 v3 units 塊含提案 A/E 七新欄且 round-trip 逐欄相等",
      all(k in ({s["cls"]: s["units"] for s in doc4["stations"]
                 if s.get("units")}[DA]) for k in ("good_h", "rework_h", "scrap_h",
                                                   "r_good", "r_good_base",
                                                   "base_good_n_days", "slow_good"))
      and {s["cls"]: s["units"] for s in doc4["stations"]
           if s.get("units")}[DA]["good_h"] is not None
      and {s["cls"]: s["units"] for s in back["stations"] if s.get("units")}[DA]
      == {s["cls"]: s["units"] for s in doc4["stations"] if s.get("units")}[DA])
    # 兩榜：v1+v2+v3 混讀、n_days_units < n_days 揭露
    mixed = _demo_docs(DAY) + [_doc_day("2026-07-27", 10.0, ver=1)]
    pt = pareto30_time(mixed, end_day=DAY)
    pu = pareto30_units(mixed, end_day=DAY)
    t("U8.9 時間榜（wattr 權威）吃 v1+v2+v3 混合檔（v2/v3 經 to_v1_doc 降視圖）",
      pt is not None and pt["n_days"] == 4)
    t("U8.10 顆數榜 n_days_units(3) < n_days(4) 分開揭露",
      pu is not None and pu["n_days"] == 4 and pu["n_days_units"] == 3)
    t("U8.11 顆數榜三桶鍵＋share＋approx 標示",
      set(pu["by_bucket"]) == set(UNIT_BUCKETS) and pu["approx_buckets"] == ("slow",)
      and pu["stations"][0]["top"][0][1] > 0)
    t("U8.12 顆數榜：窗內全 v1（無 units）→ None（面板誠實空板）",
      pareto30_units([_doc_day("2026-07-28", 10.0, ver=1)], end_day=DAY) is None)
    t("U8.13 to_v1_doc：v1 原樣返還、v2/v3 剝 units/units_line",
      to_v1_doc(doc1) is doc1
      and "units_line" not in to_v1_doc(doc4)["line"]
      and all("units" not in s for s in to_v1_doc(doc4)["stations"]))
    # ★ 顆數榜慢速：v3 讀 `slow_good`（良品尺）、v2 退 `speed_net`（嘗試軸）＋混用天數揭露
    pmap = {p["cls"]: p for p in pu["stations"]}
    t("U8.15 顆數榜慢速：v3 讀 slow_good、v2 退 speed_net；DA=6.0+8+8=22、Frame=2.5+3+3=8.5",
      abs(pmap[DA]["by_bucket"]["slow"] - 22.0) < 1e-6
      and abs(pmap[Frame]["by_bucket"]["slow"] - 8.5) < 1e-6)
    t("U8.16 顆數榜 n_days_slow_good 揭露尺混用（3 天裡只有 1 天是良品尺）",
      pu["n_days_slow_good"] == 1 and pu["n_days_units"] == 3)
    # ── LOW A-5：按日計會低估混尺程度（一天內任一站是新尺就整天算新尺）⇒ 補**站×日**兩欄。
    #    demo docs＝3 天 × 2 站＝6 個站日，其中 v3 的那天 2 站是新尺 ⇒ 2/6。
    t("U8.16b 顆數榜 additive 兩欄（站×日）：n_station_days_slow_good/n_station_days＝2/6"
      "＝真正的混尺比例（舊的按日欄仍在、不動）",
      pu["n_station_days"] == 6 and pu["n_station_days_slow_good"] == 2
      and pu["n_station_days_slow_good"] <= pu["n_station_days"]
      and pu["n_days_slow_good"] == 1)
    t("U8.17 pareto30_units 壞 day 字串不炸（datetime.min 守衛真的可用）",
      pareto30_units(_demo_docs(DAY) + [dict(_doc_day("2026-07-28", 10.0),
                                             day="not-a-date")]) is not None)

    # =========================================================================
    # U9x demo
    # =========================================================================
    dm = demo_board()
    dmap = {x["cls"]: x for x in dm["rows"]}
    t("U9.1 demo 確定性（同 day 兩呼逐位相同）",
      json.dumps(dm, sort_keys=True, default=str)
      == json.dumps(demo_board(), sort_keys=True, default=str))
    t("U9.2 demo rows 恆 43、source=demo", len(dm["rows"]) == 43 and dm["source"] == "demo")
    t("U9.3 demo 全桶現形（scrap 40／rework 20／slow>0）",
      dmap[Frame]["qlik_fail"] == 40 and dmap[DA]["rework_n"] == 20
      and dmap[DA]["speed_loss"] > 0)
    t("U9.4 demo speed_gain 現形（segFlexBonding gain=20）",
      dmap["segFlexBonding"]["speed_gain"] == 20.0
      and dmap["segFlexBonding"]["slow_est_n"] == 0)
    t("U9.5 demo partial 現形（segWireBonding 首暖）",
      dmap["segWireBonding"]["identity_ok"] is None
      and dmap["segWireBonding"]["note"] == NOTE_BASE_WARMING)
    t("U9.6 demo per-row scrap degraded 現形（segCleanTest）",
      dmap["segCleanTest"]["scrap_src_degraded"] is True
      and dmap["segCleanTest"]["qlik_fail"] is None)
    t("U9.7 demo 吸收行現形（absorbed_total=9）", dm["absorbed_total"] == 9)
    t("U9.8 demo 不可測型現形（zero_len/nosig/src_err/no_time_src）",
      dmap["segPotting"]["note"] == wattr.NOTE_ZERO_LEN
      and dmap["segQASetup"]["note"] == wattr.NOTE_NOSIG
      # src_err ＝ `_DEMO_ATT_EXTRA` 補的站（**wattr demo 沒有的站**；補在它有的站
      # 會造出「時間條照畫、顆數列說查不到」的矛盾＝本輪 D-demo 在修的那型）
      and dmap["segSerialisation"]["note"] == wattr.NOTE_SRC_ERR
      and dmap["segLamination"]["note"] == NOTE_NO_TIME_SRC)
    t("U9.9 demo 可測列恆等式全過（identity_ok 無 False；[U-A]+[U-D] 逐列）",
      all(x["identity_ok"] is not False for x in dm["rows"])
      and all(identity_error_ua(x) <= UA_TOL and identity_error_ud(x) <= UD_TOL
              for x in dm["rows"] if x["identity_ok"] is True))
    t("U9.10 demo 顆數榜掛上（3 天涵蓋、thin 標記）",
      dm["pareto30"] is not None and dm["pareto30"]["n_days_units"] == 3
      and dm["pareto30"]["thin"] is True)
    t("U9.11 ★ demo 全板 [U-D]：**所有 loss_by 非 None 的列**殘差 ≤1e-6（且真的有這種列）",
      all(identity_error_ud(x) <= UD_TOL for x in dm["rows"] if x["loss_by"])
      and sum(1 for x in dm["rows"] if x["loss_by"]) >= 3)
    t("U9.12 demo hero 提案 E/H 現形（ranked 非空、不含 unexplained、approx 全 True）",
      dm["hero"]["loss_ranked"]
      and all(c["approx"] is True and c["bucket"] != "unexplained"
              for c in dm["hero"]["loss_ranked"])
      and dm["hero"]["loss_actionable"] is not None)
    t("U9.13 demo 只有嘗試軸尺的站（segSealDispense）⇒ 藥丸「—」（loss_by/ranked 空）",
      dmap["segSealDispense"]["loss_by"] is None
      and dmap["segSealDispense"]["ranked"] == []
      and dm["diag"]["n_no_ruler"] == dm["hero"]["n_no_ruler"])
    # ── LOW D-demo：兩個 demo 板講的是同一天同一站。producing_h 對不上（舊值 Frame 30 vs 8）
    #    ⇒ /daily demo 的 24h 條**用時間帳畫底、用顆數帳切三段** ⇒ 綠段溢出到 191%。
    att_dm = {r["cls"]: r for r in wattr.demo_board().get("rows") or ()}
    t("U9.14 ★ demo 兩板逐站 producing_h 相等（時間腿同源；三段和恆 ≤ 底條的生產段）",
      att_dm and all(dmap[c]["producing_h"] == att_dm[c].get("producing_h")
                     for c in att_dm)
      and all(abs(dmap[c]["good_h"] + dmap[c]["rework_h"] + dmap[c]["scrap_h"]
                  - att_dm[c]["producing_h"]) < 1e-9
              for c in att_dm if dmap[c]["good_h"] is not None))
    t("U9.15 demo 亦覆蓋跨窗零嘗試型（時間帳有、`_DEMO_COUNTS` 沒有的站）",
      any(x["note"] == NOTE_COUNTS_WINDOW for x in dm["rows"]))

    # =========================================================================
    # U10x §24（零人員欄；含新增的 loss_by/ranked/hero 輸出）
    # =========================================================================
    att_dirty = dict(att_da, operator="Alice", technician_name="Bob")
    rw_dirty = _rw_one(DA, PH, 320, 300)
    rw_dirty["diag"]["operator_names"] = ["Carol", "Dave"]
    rw_dirty["diag"]["headcount"] = 4
    grid_dirty = _grid_one(DA, 280, 12)
    grid_dirty["absorption"] = [{"station": "segPostLamInspection",
                                 "mcl": PH, "n_lots": 3, "worker": "Eve"}]
    grid_dirty["absorbed_total"] = 3
    b24 = build_board(DAY, {DA: att_dirty}, grid_dirty, rw_dirty, base_da)
    blob = json.dumps(b24, sort_keys=True, default=str)
    doc24 = ledger_doc(att_board, b24, day=DAY, source="live", wo_source_status="ok")
    blob += json.dumps(doc24, sort_keys=True, default=str)
    t("U10.1 §24 全鏈負斷言：人名值零出現（Alice/Bob/Carol/Dave/Eve）",
      all(x not in blob for x in ("Alice", "Bob", "Carol", "Dave", "Eve")))
    t("U10.2 §24 全鏈負斷言：人員鍵零出現（operator/technician/worker/headcount）",
      all(x not in blob.lower() for x in ("operator", "technician", "worker",
                                          "headcount", "manpower", "badge")))
    t("U10.3 §24 新輸出面：loss_by/ranked/hero 的鍵全在桶名白名單內、零人員鍵",
      all(set(x["loss_by"]) == set(LOSS_BUCKETS) for x in b24["rows"] if x["loss_by"])
      and not any(wattr._person_ish(k) for x in b24["rows"]
                  for k in (x.get("loss_by") or {}))
      and not any(wattr._person_ish(c["bucket"]) for x in b24["rows"]
                  for c in (x.get("ranked") or []))
      and not any(wattr._person_ish(k) for k in (b24["hero"]["loss_by"] or {})))

    # =========================================================================
    # U11x i18n
    # =========================================================================
    t("U11.1 I18N_UNIT en/zh 鍵集合相等", set(I18N_UNIT["en"]) == set(I18N_UNIT["zh"]))
    t("U11.2 §8.2 六鍵齊（at_p_scrap/rework/slow/est/caliber/unavail）",
      all(k in I18N_UNIT["en"] for k in
          ("at_p_scrap", "at_p_rework", "at_p_slow", "at_p_est", "at_p_caliber",
           "at_p_unavail")))
    t("U11.3 桶→i18n 對映鍵與 UNIT_BUCKETS 對齊",
      set(UNIT_BUCKET_I18N_KEY) == set(UNIT_BUCKETS))
    t("U11.4 誠實合桶字樣在（含微停，未拆分＝④再議不做的自揭）",
      "micro-stops (not split)" in I18N_UNIT["en"]["at_p_slow_sub"]
      and "含微停" in I18N_UNIT["zh"]["at_p_slow_sub"])
    t("U11.5 估算句恆帶 ≈", I18N_UNIT["en"]["at_p_est"].startswith("≈")
      and I18N_UNIT["zh"]["at_p_est"].startswith("≈"))
    t("U11.6 提案 A/E/C/F/H 新鍵齊（split/ruler/unused/presumed/短名/句式/note 人話）",
      all(k in I18N_UNIT["en"] for k in
          ("un_split_hdr", "un_b_goodh", "un_b_reworkh", "un_b_scraph", "un_split_q",
           "un_ruler", "un_ruler_q", "un_ruler_none", "un_unused", "un_unused_q",
           "un_presumed", "un_presumed_q", "un_faster", "un_sent_full",
           "un_sent_short", "un_sent_plain", "un_sent_met_clean", "un_sent_unexp",
           "un_sent_noruler"))
      and all(f"un_s_{k}" in I18N_UNIT["en"] for k in LOSS_BUCKETS)
      and all(f"un_note_{k}" in I18N_UNIT["en"] for k in
              (NOTE_NO_TIME_SRC, NOTE_BASE_WARMING, NOTE_NO_REWORK_SRC, NOTE_NO_QLIK,
               NOTE_COUNTS_WINDOW)))
    t("U11.10 MED A-1 新 note 全套接線（UNIT_NOTE_KEYS ＋ en/zh 人話；缺一個就會把 machine "
      "key 印給人看）",
      NOTE_QLIK_WINDOW in UNIT_NOTE_KEYS
      and all(f"un_note_{NOTE_QLIK_WINDOW}" in I18N_UNIT[lg] for lg in ("en", "zh"))
      and "Qlik" in I18N_UNIT["en"][f"un_note_{NOTE_QLIK_WINDOW}"]
      and "母體" in I18N_UNIT["zh"][f"un_note_{NOTE_QLIK_WINDOW}"])
    t("U11.9 F3 新 note 進 UNIT_NOTE_KEYS ＋ en/zh 人話齊（模板 un_note_* fallback 才接得住）",
      NOTE_COUNTS_WINDOW in UNIT_NOTE_KEYS
      and all(f"un_note_{NOTE_COUNTS_WINDOW}" in I18N_UNIT[lg] for lg in ("en", "zh"))
      and "cross-midnight" in I18N_UNIT["en"][f"un_note_{NOTE_COUNTS_WINDOW}"]
      and "跨午夜" in I18N_UNIT["zh"][f"un_note_{NOTE_COUNTS_WINDOW}"])
    t("U11.7 句式鍵含 {gap}/{tot}/{causes}/{h}/{n} 佔位（模板 replace 的契約）",
      all("{gap}" in I18N_UNIT[lg]["un_sent_full"]
          and "{tot}" in I18N_UNIT[lg]["un_sent_full"]
          and "{causes}" in I18N_UNIT[lg]["un_sent_full"]
          and "{tot}" in I18N_UNIT[lg]["un_sent_short"]
          and "{causes}" in I18N_UNIT[lg]["un_sent_short"]
          and "{tot}" in I18N_UNIT[lg]["un_sent_plain"]
          and "{causes}" in I18N_UNIT[lg]["un_sent_plain"]
          and "{gap}" not in I18N_UNIT[lg]["un_sent_plain"]
          and "{h}" in I18N_UNIT[lg]["un_sent_unexp"]
          and "{n}" in I18N_UNIT[lg]["un_sent_unexp"] for lg in ("en", "zh")))
    t("U11.8 三張對映表鍵齊且**指向存在的 i18n 鍵**（短名/三段住本檔、長名可住 wattr）",
      set(LOSS_SHORT_I18N_KEY) == set(LOSS_BUCKETS)
      and set(LOSS_BUCKET_I18N_KEY) == set(LOSS_BUCKETS)
      and set(SPLIT_I18N_KEY) == set(SPLIT_SEGMENTS)
      and all(LOSS_SHORT_I18N_KEY[k] in I18N_UNIT[lg]
              for k in LOSS_BUCKETS for lg in ("en", "zh"))
      and all(SPLIT_I18N_KEY[k] in I18N_UNIT[lg]
              for k in SPLIT_SEGMENTS for lg in ("en", "zh"))
      and all((LOSS_BUCKET_I18N_KEY[k] in I18N_UNIT[lg]
               or LOSS_BUCKET_I18N_KEY[k] in wattr.I18N_ATTRIB[lg])
              for k in LOSS_BUCKETS for lg in ("en", "zh")))

    # =========================================================================
    # U12x 站序/常數 pin
    # =========================================================================
    t("U12.1 站序 import 自 mes_qlik（43 站、不另抄）",
      len(mq.QLIK_STATIONS) == 43 and build_board(DAY, {}, None, None, None,
                                                  stations=mq.QLIK_STATIONS)["rows"][0]
      ["cls"] == "segSerialisation")
    t("U12.2 誤差界寫死（UA/UD=1e-6、非 env；UC alias 仍在＝外部引用不炸）",
      UA_TOL == 1e-6 and UD_TOL == 1e-6 and UC_TOL == UD_TOL
      and identity_error_uc({}) == 0.0)
    # ── LOW B-2：loss_by 是 dict、另兩腿也齊，卻少一個桶＝結構破洞（鍵名寫錯／新桶漏接線）。
    #    回 0.0 ＝「帳是平的」＝**假平**；必須讓 identity_ok 判 False。
    hole = {"p_potential_good": 100.0, "qlik_pass": 10.0,
            "loss_by": {b: 1.0 for b in LOSS_BUCKETS if b != "scrap"}}
    t("U12.6 identity_error_ud：loss_by 缺桶 ⇒ inf（假平已死）；量不到（loss_by 非 dict）"
      "仍回 0.0＝無帳可驗（兩者語意不同、不可混）",
      identity_error_ud(hole) == float("inf")
      and not (identity_error_ud(hole) <= UD_TOL)
      and identity_error_ud({"loss_by": None, "p_potential_good": 1.0,
                             "qlik_pass": 1.0}) == 0.0
      and identity_error_ud({}) == 0.0)
    t("U12.3 _DOC_UNIT_KEYS＝v2 十三鍵 ＋ v3 七新欄在**尾端**（舊檔逐鍵讀位置不變）",
      len(_DOC_UNIT_KEYS_V2) == 13 and len(_DOC_UNIT_KEYS) == 21
      and _DOC_UNIT_KEYS[:13] == _DOC_UNIT_KEYS_V2
      and _DOC_UNIT_KEYS[13:20] == ("good_h", "rework_h", "scrap_h", "r_good",
                                    "r_good_base", "base_good_n_days", "slow_good")
      # ★ #98：v6 的 `units_by_family` 也在**尾端**（同一條紀律）
      and _DOC_UNIT_KEYS[20:] == ("units_by_family",))
    t("U12.4 v1..v8 schema 常數（v1 沿用 wattr、不重打字串；寫恆最新版）",
      LEDGER_SCHEMA_V1 == "board.daily_ledger.v1"
      and LEDGER_SCHEMA_V2 == "board.daily_ledger.v2"
      and LEDGER_SCHEMA_V3 == "board.daily_ledger.v3"
      and LEDGER_SCHEMA_V4 == "board.daily_ledger.v4"
      and LEDGER_SCHEMA_V5 == "board.daily_ledger.v5"
      and LEDGER_SCHEMA_V6 == "board.daily_ledger.v6"
      and LEDGER_SCHEMA_V7 == "board.daily_ledger.v7"
      and LEDGER_SCHEMA_VER == "board.daily_ledger.v8"
      and _DOC_V4_KEYS == ("rw_axis", "rw_reason", "families_status")
      and _DOC_V7_KEYS == ("products_status",)
      and _DOC_V8_KEYS == ("rw_anchor", "built_at", "wo_open_at_build", "cal_open_tail",
                           "roster_pad_unmeasurable"))
    t("U12.4b parse 閉集＝{v1(委派),v2,…,v8}；v0/v9/v10 一律 None（絕不猜未來版）",
      all(parse_ledger_doc(dict(doc4, schema_ver=v)) is None
          for v in ("board.daily_ledger.v0", "board.daily_ledger.v10",
                    "board.daily_ledger.v9", "board.unit.v1", ""))
      and all(parse_ledger_doc(dict(doc4, schema_ver=v)) is not None
              for v in (LEDGER_SCHEMA_V2, LEDGER_SCHEMA_V3, LEDGER_SCHEMA_V4,
                        LEDGER_SCHEMA_V5, LEDGER_SCHEMA_V6, LEDGER_SCHEMA_V7,
                        LEDGER_SCHEMA_VER)))
    # ★ 2026-09-04：v4 檔（舊尺）**一定**被判成可覆寫——它就是 `_backfill_ledger` 的
    #   `v5_upgrade` 母體；判不出來的話換尺當天的日帳會靜靜停在舊白名單上。
    t("U12.4d 帶 rw_axis=True 的 v4 檔仍然 should_overwrite（換尺＝可恢復降級，不是完好檔）",
      should_overwrite(dict(doc4ok, schema_ver=LEDGER_SCHEMA_V4)) is True)
    t("U12.4c wattr 站列白名單尾端＝(`rw`, `families`)（v4 的 additive 尾欄；舊鍵位置不動）",
      wattr._DOC_STATION_KEYS[-2:] == ("rw", "families")
      and wattr._DOC_STATION_KEYS[:17] == (
          "cls", "fleet_n", "total_mh", "producing_h", "wo_union_h", "wo_h",
          "wo_union_pre_h", "wo_overlap_prod_h", "starved_h", "blocked_h",
          "unexplained_h", "lost_h", "unmeasurable", "note", "closure_h",
          "rate_per_mh", "rate_src"))
    t("U12.5 損失桶常數（A5 換軸後）：可行動 ∪ 推定 ＝ LOSS_BUCKETS、互斥、七桶",
      set(ACTIONABLE_BUCKETS) | set(PRESUMED_BUCKETS) | set(NONPRODUCTION_BUCKETS)
      == set(LOSS_BUCKETS)
      and not (set(ACTIONABLE_BUCKETS) & set(PRESUMED_BUCKETS))
      and PRESUMED_BUCKETS == ("unexplained",)
      and NONPRODUCTION_BUCKETS == ()
      and LOSS_BUCKETS == tuple(wattr.BUCKETS) + UNIT_BUCKETS
      and len(LOSS_BUCKETS) == 7)
    # ★ A5：closure 桶已退場（QG-13 負斷言）——LOSS_BUCKETS 零 closure、ranked 永不出現。
    t("U12.5b A5：closure 已不在任何桶集（分母端扣減取代之；C-PC10 命名紀律仍留在字串層）",
      "closure" not in LOSS_BUCKETS and "closure" not in ACTIONABLE_BUCKETS
      and all(c["bucket"] != "closure"
              for c in ranked_losses({"loss_by": {b: 10.0 for b in LOSS_BUCKETS}},
                                     max_n=len(LOSS_BUCKETS)))
      and "schedul" not in I18N_UNIT["en"]["un_s_closure"]
      and "班" not in I18N_UNIT["zh"]["un_s_closure"].replace("全廠停線", ""))

    # =========================================================================
    # U13x ranked_losses（提案 H 單一排序）
    # =========================================================================
    lb_full = {"wo": 100.0, "starved": 50.0, "blocked": 5.0, "unexplained": 9999.0,
               "scrap": 30.0, "rework": 20.0, "slow": -7.0}
    rk = ranked_losses({"loss_by": lb_full})
    t("U13.1 ranked：unexplained **永不進榜**（提案 C）——即使它是最大的那一個",
      all(c["bucket"] != "unexplained" for c in rk))
    t("U13.2 ranked：依 pcs 降冪、cap 3、approx 恆 True（換算過的一律帶 ≈）",
      [c["bucket"] for c in rk] == ["wo", "starved", "scrap"] and len(rk) == 3
      and all(c["approx"] is True for c in rk))
    t("U13.3 ranked：負值（slow gain＝比基準快）不進榜",
      all(c["bucket"] != "slow"
          for c in ranked_losses({"loss_by": lb_full}, max_n=6)))
    t("U13.4 ranked：floor 生效（< min_pcs 的桶不進榜）",
      [c["bucket"] for c in ranked_losses({"loss_by": lb_full}, max_n=6, min_pcs=25)]
      == ["wo", "starved", "scrap"]
      and [c["bucket"] for c in ranked_losses({"loss_by": lb_full}, max_n=6)]
      == ["wo", "starved", "scrap", "rework", "blocked"])
    t("U13.5 ranked：loss_by 缺/None ⇒ []（顯「—」、**絕不填 0**＝T6）",
      ranked_losses({"loss_by": None}) == [] and ranked_losses(None) == []
      and ranked_losses({}) == []
      and ranked_losses({"loss_by": dict(lb_full, wo=None)})[0]["bucket"] == "starved")
    t("U13.6 ranked：acct 標對本帳（時間四桶→time、顆數三桶→pcs）",
      all(c["acct"] == ("pcs" if c["bucket"] in UNIT_BUCKETS else "time")
          for c in ranked_losses({"loss_by": lb_full}, max_n=6)))
    # ── LOW A-3：`NaN < floor` 恆 False ⇒ NaN 會通過 floor、又與任何值比較皆 False
    #    ⇒ **停在原位登上榜首**、頁面顯「≈nan 顆」。量不到就該顯「—」、不是排第一。
    nan_lb = dict(lb_full, wo=float("nan"))
    t("U13.7 ranked：NaN／±inf 一律不進榜（絕不登頂），其餘桶照常排序",
      [c["bucket"] for c in ranked_losses(nan_lb and {"loss_by": nan_lb}, max_n=6)]
      == ["starved", "scrap", "rework", "blocked"]
      and all(math.isfinite(c["pcs"])
              for c in ranked_losses({"loss_by": dict(lb_full,
                                                      wo=float("inf"))}, max_n=6)))

    # ── U14 產品子樹（#103；D1「一份帳、加 product 子樹、兩樹永不相加」）──────────────
    _ag = {"schema": "board.qlik.v1", "product": "kestrel", "stations": [
        {"ord": 1, "cls": "segAsnA", "mcl": "assy", "display": "Alpha",
         "display_pending": False, "total": 100, "pass": 90, "fail": 10, "n_null": 0,
         "pass_by_family": {"Kestrel": 90}},
        {"ord": 2, "cls": "segAsnB", "mcl": "assy", "display": None,
         "display_pending": True, "total": 80, "pass": 80, "fail": 0, "n_null": 1,
         "pass_by_family": {"Kestrel": 70, "Plover": 10}},
    ], "kpis": {}, "diag": {"degraded": False}}
    blk = product_block(_ag)
    t("U14.1 product_block：Kestrel grid → 子樹（站列白名單、恆等式守衛過的家族拆分）",
      blk["product"] == "kestrel" and blk["n_stations"] == 2 and blk["degraded"] is False
      and set(blk["stations"][0]) == set(_DOC_PRODUCT_STATION_KEYS)
      and blk["stations"][0]["qlik_pass"] == 90 and blk["stations"][0]["qlik_fail"] == 10
      and blk["stations"][0]["qlik_total"] == 100
      and blk["stations"][1]["units_by_family"] == {"Plover": 10, "Kestrel": 70}
      and blk["stations"][1]["display_pending"] is True)
    t("U14.2 grid 不可用／空站列 ⇒ 子樹**缺席（None）**，絕不寫一棵全 0 的樹",
      product_block({"unavailable": "snapshot_missing"}) is None
      and product_block({"stations": []}) is None and product_block(None) is None)
    t("U14.2b grid degraded ⇒ 子樹照寫、`degraded=True`、家族拆分整片 None（不可信不印）",
      (lambda b: b["degraded"] is True
       and all(s["units_by_family"] is None for s in b["stations"]))(
          product_block(dict(_ag, diag={"degraded": True}))))
    doc_np = ledger_doc(att_board, b, day=DAY, source="live", wo_source_status="ok",
                        computed_at=f"{DAY}T23:59:00", rw_axis=True, rw_reason="ok",
                        rw_anchor=RW_ANCHOR)
    doc_p = ledger_doc(att_board, b, day=DAY, source="live", wo_source_status="ok",
                       computed_at=f"{DAY}T23:59:00", rw_axis=True, rw_reason="ok",
                       products={"kestrel": blk}, products_status="ok", rw_anchor=RW_ANCHOR)
    t("U14.3 v7 加性：帶子樹的 doc 除了 `products`／`products_status` 之外**逐鍵等於**不帶的那份",
      {k: v for k, v in doc_p.items() if k not in (PRODUCTS_KEY, "products_status")}
      == {k: v for k, v in doc_np.items() if k not in (PRODUCTS_KEY, "products_status")}
      and doc_np[PRODUCTS_KEY] is None and doc_p[PRODUCTS_KEY]["kestrel"]["n_stations"] == 2)
    t("U14.3b v7 round-trip（parse(doc)==doc；子樹過白名單原樣回）",
      parse_ledger_doc(doc_p) == doc_p and parse_ledger_doc(doc_np) == doc_np)
    t("U14.4 ★★ 兩樹永不相加：加了 Kestrel 子樹之後，Plover 側每一支聚合的輸出**逐位元相同**",
      to_v1_doc(doc_p) == to_v1_doc(doc_np)
      and json.dumps(pareto30_units([doc_p]), sort_keys=True, default=str)
      == json.dumps(pareto30_units([doc_np]), sort_keys=True, default=str)
      and base_rates([doc_p], DAY) == base_rates([doc_np], DAY)
      and family_split_unknown(doc_p) == family_split_unknown(doc_np)
      and should_overwrite(doc_p) == should_overwrite(doc_np)
      and [s["units"] for s in parse_ledger_doc(doc_p)["stations"]]
      == [s["units"] for s in parse_ledger_doc(doc_np)["stations"]])
    t("U14.4b `to_v1_doc` 把 `products` 整棵剝掉（時間榜聚合權威看不到一個 Kestrel 位元）",
      PRODUCTS_KEY not in to_v1_doc(doc_p) and "products_status" not in to_v1_doc(doc_p))
    t("U14.5 `product_stations_of` ＝唯一讀取入口；v6 檔／缺子樹 ⇒ **None（無帳＝「—」）**",
      set(product_stations_of(doc_p)) == {"segAsnA", "segAsnB"}
      and product_stations_of(doc_p)["segAsnA"]["qlik_pass"] == 90
      and product_stations_of(doc_np) is None
      and product_stations_of(dict(doc_np, schema_ver=LEDGER_SCHEMA_V6)) is None
      and product_stations_of(doc_p, "heron") is None)
    t("U14.5b v6 檔照收（parse 不回 None）＝「Kestrel 無帳」，不是壞檔",
      parse_ledger_doc(dict(doc_np, schema_ver=LEDGER_SCHEMA_V6)) is not None
      and product_subtree_unknown(dict(doc_np, schema_ver=LEDGER_SCHEMA_V6)) is True
      and product_subtree_unknown(doc_p) is False)
    t("U14.6 `product_subtree_unknown` **不進 `should_overwrite`**（Kestrel 抓不到 ≠ Plover 不是事實）",
      should_overwrite(dict(doc_np, wo_source_status="ok")) is False
      and product_subtree_unknown(doc_np) is True)
    t("U14.7 `carry_forward_products` 只補洞不覆蓋（時間腿重建不得抹掉顆數腿寫的子樹）",
      (lambda cf: set(product_stations_of(cf)) == {"segAsnA", "segAsnB"}
       and cf["products_status"] == "ok")(carry_forward_products(doc_np, doc_p))
      and product_stations_of(carry_forward_products(doc_p, doc_np)) is not None
      and carry_forward_products(doc_p, None) == doc_p)
    t("U14.8 子樹**永不回寫站列**：`stations[*]` 一個 Kestrel cls 都沒有",
      not ({"segAsnA", "segAsnB"} & {s.get("cls") for s in doc_p["stations"]}))

    # =========================================================================
    # U15 #182-P2：建帳證據旗（v8）——retry 理由單一出處、落旗判準、旗解判準
    #   ⚠ fixture 日期一律**相對 today**（記憶 smoke-fixture-date-anchors）。
    # =========================================================================
    _today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    _y0 = _today - timedelta(days=2) + timedelta(hours=8)          # 前天 08:00 生產日
    _t0 = _y0 + timedelta(days=1)
    _now = _t0 + timedelta(minutes=12)                               # 隔天 08:12 建帳
    _good = dict(doc4ok, wo_open_at_build=[], cal_open_tail=None, built_at=_now.isoformat())
    t("U15.1 v8 證據齊全的好帳 ⇒ 沒有 retry 理由、should_overwrite False（不得天天重寫好檔）",
      rw_retry_reason(_good) is None and should_overwrite(_good) is False)
    t("U15.2 缺 `rw_anchor`（v7 以前全部）／錨 ≠ 08:00 ⇒ `rw_anchor_legacy` ⇒ 放行覆寫",
      rw_retry_reason(dict(_good, rw_anchor=None)) == RETRY_RW_ANCHOR_LEGACY
      and rw_retry_reason(dict(_good, rw_anchor="00:00")) == RETRY_RW_ANCHOR_LEGACY
      and should_overwrite(dict(_good, rw_anchor="00:00")) is True
      and rw_anchor_legacy({k: v for k, v in _good.items() if k != "rw_anchor"}) is True)
    _tail = {"from": (_t0 - timedelta(hours=2)).isoformat(), "overlap_sec": 7200}
    t("U15.3 日曆尾端未閉合沉默 ⇒ `rw_cal_open_tail`；就地標 resolved 後不再算（旗解了）",
      rw_retry_reason(dict(_good, cal_open_tail=_tail)) == RETRY_RW_CAL_OPEN_TAIL
      and should_overwrite(dict(_good, cal_open_tail=_tail)) is True
      and rw_retry_reason(dict(_good, cal_open_tail=dict(_tail, resolved="sealed_none"))) is None)
    t("U15.4 建帳時工單未結 ⇒ `rw_wo_open_at_build`；優先序 錨 ＞ 日曆尾巴 ＞ 工單",
      rw_retry_reason(dict(_good, wo_open_at_build=["24956"])) == RETRY_RW_WO_OPEN_AT_BUILD
      and rw_retry_reason(dict(_good, wo_open_at_build=["1"], cal_open_tail=_tail))
      == RETRY_RW_CAL_OPEN_TAIL
      and rw_retry_reason(dict(_good, wo_open_at_build=["1"], rw_anchor=None))
      == RETRY_RW_ANCHOR_LEGACY
      and wo_open_flags(dict(_good, wo_open_at_build=None)) == [])

    def _wo(no, opened, closed=None, eid="EID-1"):
        """normalize 後的工單形（`board_w8` 的 `status`／`at` 歷程鍵；台北 naive）。"""
        hist = [{"status": "OPEN", "at": opened}]
        if closed is not None:
            hist.append({"status": "COMPLETED", "at": closed})
        return {"work_order_no": no, "asset_id": eid, "work_type": "REACTIVE",
                "status": "OPEN" if closed is None else "COMPLETED",
                "opened_at": opened, "closed_at": closed, "status_history": hist}
    _wos = {"EID-1": [_wo(101, _y0 + timedelta(hours=3)),                       # 窗內開、還開著
                      _wo(102, _y0 + timedelta(hours=1), _y0 + timedelta(hours=4)),   # 已結
                      _wo(103, _y0 - timedelta(days=120))],                        # 長掛（>90d）
            "EID-2": [_wo(201, _y0 + timedelta(hours=5), eid="EID-2")],          # 別台、還開著
            "EID-3": [_wo(301, _t0 + timedelta(minutes=5), eid="EID-3")]}        # 窗後才開
    _flag = wo_open_at_build(_wos, _y0, _t0, now=_now, long_days=90)
    t("U15.5 落旗：只列「建帳時還開著 ∧ 本窗有 countable 段」的單；已結單不列、窗後才開不列、"
      "**建帳時已是 >90 天長掛單不列**（C-DT6 永遠納入＝口徑，不讓那天永遠帶旗）",
      _flag == ["101", "201"], )
    t("U15.5b `eids=` 只看進得了分母的機台（名冊外的單不影響本帳）",
      wo_open_at_build(_wos, _y0, _t0, now=_now, eids={"EID-1"}, long_days=90) == ["101"])
    # ★ 2026-10-02 範圍收窄（`_FOLLOWUPS-20261002.md` ②）：範圍＝各站 `rw.eids` 聯集
    _rows = [{"cls": "segA", "rw": {"eids": ["EID-1"]}},
             {"cls": "segB", "rw": {"eids": ["EID-3", "EID-1"]}},
             {"cls": "segZ", "unmeasurable": True, "rw": None}]
    t("U15.10 `ledger_rw_eids`＝各站 `rw.eids` 聯集；沒有任何 rw 列（24h 軸／舊檔）⇒ None＝不收窄",
      ledger_rw_eids(_rows) == {"EID-1", "EID-3"}
      and ledger_rw_eids([{"cls": "segA"}, {"cls": "segZ", "rw": None}]) is None
      and ledger_rw_eids(None) is None
      and ledger_rw_eids([{"rw": {"eids": []}}]) == set())
    t("U15.10b 寫端：`eids=ledger_rw_eids(rows)` ⇒ 不在任何站 rw.eids 的機台（EID-2）其單不落旗",
      wo_open_at_build(_wos, _y0, _t0, now=_now, eids=ledger_rw_eids(_rows),
                       long_days=90) == ["101"])
    _we = {"101": "EID-1", "201": "EID-2", "9": ""}
    t("U15.10c 讀端 `narrow_wo_open_flags`：確知機台且不在範圍才丟；範圍 None／查無機台／機台空 ⇒ 留（保序）",
      narrow_wo_open_flags(["201", "101", "9", "77"], {"EID-1"}, _we) == ["101", "9", "77"]
      and narrow_wo_open_flags(["201", "101"], None, _we) == ["201", "101"]
      and narrow_wo_open_flags(["201"], set(), _we) == []
      and narrow_wo_open_flags(None, {"EID-1"}, _we) == []
      and narrow_wo_open_flags(["201"], {"EID-2"}, None) == ["201"])
    _meta = {"101": {"closed": True, "long": False}, "201": {"closed": False, "long": True}}
    t("U15.6 旗解判準：每一張「已結案」或「已轉長掛旗」⇒ 解了；任一張還開著／查無 meta ⇒ 未解",
      wo_open_resolution(["101", "201"], _meta) == (True, [])
      and wo_open_resolution(["101", "201"], {"101": {"closed": True}}) == (False, ["201"])
      and wo_open_resolution([], _meta) == (False, []))
    import board_eqmeeting as _wem_pin
    t("U15.7 `RW_ANCHOR`＝生產日界唯一出處（`board_eqmeeting.PROD_DAY_START_H`）",
      RW_ANCHOR == f"{int(_wem_pin.PROD_DAY_START_H):02d}:00")
    _v8 = ledger_doc(att_board, b, day=DAY, source="live", wo_source_status="ok",
                     rw_axis=True, rw_reason="ok", rw_anchor=RW_ANCHOR,
                     built_at="2026-09-25T08:12:00", wo_open_at_build={"9", "10"},
                     cal_open_tail=_tail,
                     roster_pad_unmeasurable={"segX": {"roster_pad": True,
                                                       "roster_pad_eids": ["E9"],
                                                       "empty_track_eids": []}})
    t("U15.8 v8 五鍵 round-trip（parse 原樣回）；`to_v1_doc` 把它們剝掉；v7 檔讀出來五鍵全 None",
      parse_ledger_doc(_v8) == _v8 and _v8["wo_open_at_build"] == ["10", "9"]
      and not (set(_DOC_V8_KEYS) & set(to_v1_doc(_v8)))
      and all(parse_ledger_doc(dict({k: v for k, v in _v8.items() if k not in _DOC_V8_KEYS},
                                    schema_ver=LEDGER_SCHEMA_V7))[k] is None
              for k in _DOC_V8_KEYS))
    try:
        import board_monthly as _wmn_pin
        _cases = [_good, dict(_good, rw_anchor=None), dict(_good, cal_open_tail=_tail),
                  dict(_good, cal_open_tail=dict(_tail, resolved="sealed_none")),
                  dict(_good, wo_open_at_build=["7"]), dict(_good, wo_open_at_build=None),
                  {"schema_ver": LEDGER_SCHEMA_V7, "day": DAY, "stations": []}]
        _pin = all(_wmn_pin._rw_flags_of_doc(c)
                   == (rw_anchor_legacy(c), cal_open_tail_pending(c), wo_open_flags(c))
                   for c in _cases)
    except Exception as _e:                      # board_monthly 讀取器缺席＝cross-pin 紅
        _pin = False
    t("U15.9 ★ cross-pin：`board_monthly._rw_flags_of_doc`（凍結閘的本地讀取器）與本檔三支"
      "判準逐案同答（計畫與落地閘不得各判一次＝[[backfill-plan-vs-write-gate]]）", _pin)

    print()
    if fails:
        print(f"SELFTEST FAIL：{len(fails)}/{n_cases[0]} 案")
        for f in fails:
            print("  - " + f)
        return 1
    print(f"SELFTEST PASS（board_unit 全綠：{n_cases[0]} 案）")
    return 0


# =============================================================================
# CLI
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="board.unit.v1 顆數帳本引擎（純函式、零 I/O）")
    ap.add_argument("--selftest", action="store_true", help="全離線 selftest")
    ap.add_argument("--demo", action="store_true", help="印 demo 板 JSON（全合成）")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if args.demo:
        print(json.dumps(demo_board(), ensure_ascii=False, indent=2, default=str))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
