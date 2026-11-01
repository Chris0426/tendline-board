#!/usr/bin/env python3
"""Tendline Board → cmms 定期推送端 `board.last_production.v1`（MES 末筆生產快照）。

**節奏＝每 10 分鐘**（2026-08-15 Chris 拍板；原契約談的是每小時，改動理由與知會義務見
`SPEC-LASTPROD-PUSH.md`＋`install-lastprod-task.ps1` 檔頭的 cadence note）。

**這是什麼**：把「每台設備在 MES 上最後一個 production segment 的**四件事實**——結束時刻、
起點、物料 designation、測試結果」定期推一份快照給 cmms（`POST <cmms>/ingest/last-production`）。
（2026-08-17 前只有結束時刻；加 start/designation＝cmms 第十七輪「停機終點對齊」的 ask，
SPEC v0.9–v1.0。**2026-08-22 再加 `last_segment_result`**＝#31／cmms NOTE-56「排除已知 FAIL」
的 ask（SPEC v1.4 答定命名值域；其消費端 2026-08-20 已先 live ⇒ 我方出貨那一刻即生效）。
`last_segment_start` ＝**最新那一段的起點**、不是「這波連續生產從哪開始」——我方**不合併段**；
`last_segment_designation` ＝ MES `matDesignation` 原值直送（dummy print 的結構性記號
`Engineering (R&D)`，WO-35966 窗實測）；
`last_segment_result` ＝ MES `stpTestResult` **原值直送**（不 upper／不 trim／不映射／不補預設；
`varchar(75)`、⚠ **非查找表背書的閉集**——實測 `PASS`/`FAIL`/NULL，我方只能宣告觀測值）
——**判斷留在 cmms 端，我方只送事實**。）cmms 端的報修單／維修單上有一顆
**「修正停機起點」**按鈕（**文案以 cmms live 為準**；我方先後寫錯兩次：「對齊 MES 停機起點」
→「對齊 MES 末筆生產」→ 2026-08-16 cmms 重做該功能後正式文案＝「修正停機起點」），
吃的就是這份快照；**在我方開始推之前那顆按鈕沒有快照可讀、
誠實不渲染**（cmms NOTE-41 第四點）——所以這支腳本是整條 `failure_started_at` 線的起點：

  ★ **2026-08-16 對面改了消費方式（我方 payload／節奏／端點全不變，但影響見下）**：
    cmms 從「只留最新一列的 upsert 快照」改成**逐筆留痕**（值有變才 append、保留 90 天），
    按鈕改成查「**開單時刻之前（嚴格）最接近的一筆**」。原因是他們發現舊做法方向是錯的
    ——快照是「推播當下」的末筆、不是「開單前」的末筆，機台開單後不會靜止
    （⚠ 括號裡原寫「維修跑 Dummy CAL 在 MES 眼裡就是生產」——**2026-08-17 MES 實測更正**：
    那晚 dummy 只有兩列且帶 `Engineering (R&D)` 記號，把末筆推到深夜的是**修復後恢復的
    常態生產**；「不會靜止」這個承重句不變，見 SPEC v0.9 ③）⇒ 卡片會提議一個**晚於開單**
    的起點、按下去反而**砍掉**停機。他們另加了 domain 方向鐵閘（`fsa >= opened_at` 一律拒）。
  ⇒ **對本檔的兩個實質後果**（別再引用舊結論）：
    ① **推送頻率不再是「對面零成本」**——留痕的列數與**我方的節奏**直接相關，
       且「這一輪沒推成」會在對面留下**永久的歷史空洞**（不再自我痊癒）。
       cmms 2026-08-16 上午給的「日後改頻率不必再知會」是在 upsert 前提下講的，
       **該前提當天下午就被他們自己改掉了** ⇒ **改節奏仍要知會**（SPEC §11.6）。
    ② **推一個較舊的值不再會被下一輪蓋掉** ⇒ `partial_snapshot()` 的舊值地板（見下）。

    我方推送端上線 → 按鈕才有快照可讀 → 現場才按得到 → `fsa` 才會有值
                  → 我方消費它（待辦 4）才有意義

**不是什麼**：不是稼動（`mes_timeline`）、不是良率（`mes_qlik`）、不是 WIP（`mes_wip`）、
不是重工（`mes_rework`）。本檔**不做任何判斷**——不判「這台停了沒」、不判「這是不是試跑」。

語意權威（分歧一律以文件為準、不以本檔註解為準）：
  · `SPEC-LASTPROD-PUSH.md`（本條線的唯一裁決存證）
  · `CONCEPT.md` §19.3 —— cmms NOTE-38 三（形狀提案＋四條 pin）／我方 08-11 三件回覆
    relay ■三（`lookback_days` ＋ null 語意反提案）／NOTE-39 二（端點逐字形狀、錯誤碼）／
    08-14 relay 第七節（NOTE-41 因果鏈）。

═══ 四條 pin（cmms NOTE-39 二逐字；違反即錯，勿重議）════════════════════════════
  ① **反靜默**：「不在 `items` 裡」**不表達任何語意**。
     · 「近 `lookback_days` 天內無生產訊號」＝ 進 `items` 且 `last_segment_end: null`
     · 「我方 MES 不認識這台」＝ 進 `unknown[]`
     （cmms 端絕不因某台缺席就清掉它的快照列 —— 缺席在對面是「沒有新消息」。）
  ② **時刻走 UTC ISO `Z`**。MES 回的是**台北牆鐘 naive**（UTC+8、台灣無日光節約），
     一律經 `to_utc_z()` 轉換。台北日曆日那條軸只給 `since`／`effective_date` 那類**日期**欄，
     **兩條軸不混**——本檔沒有任何日期欄，全部都是時刻。
  ③ **只要事實不要判斷**：**零工期段（`start == end`）原值照送、不濾**。那八個結構上
     全是瞬時段的站（進料檢／序號寫入／烘烤打點），其 `last_segment_end` 等於段起點
     **是正確的**，濾掉才是製造錯誤（[[mes-zero-length-segments]]／M4 事故）。
  ④ `unknown[]` 對方**只計數不落庫**（那是我方的認知狀態，不是他們資產主檔的事實）。

═══ `full_window_rebuild`（2026-08-16 加；**唯一一個會讓對面刪資料的欄位**）════════════
  信封層布林、**每輪都在**（欄名我方定，cmms 第十輪 §一 要的旗標）。
  **語意＝「這一輪是覆蓋，不是觀測」**，值由 `rebuild_claim(incremental, partial)` 決定。
  · `true` ⇒ cmms 對**本輪帶非 null 值的每個 EID**，刪掉史表中**大於本輪值**的列
    （我方剛宣告「MES 上沒有那些段了」，留著就是留著已知為假的事實）。
  · `false`（含**欄位缺席**＝旗標上線前的舊輪）⇒ 對面**只 append、永不刪**。
  ★ 為什麼需要它：對面史表 PK＝`(asset_id, last_segment_end)`＋`ON CONFLICT DO NOTHING`，
    而「T 之前末筆」取符合條件者中**最大**的 ⇒ 我方推一個較小的值時**大的舊值仍會贏**，
    被永久保留成偏晚的錨點＝**停機算少**（與 WO-35966 同方向）。
  ★ 為什麼要我方宣告而不是他們猜：「值變小」分不出「我方宣告的覆蓋」與「我方某天的 bug」
    ——前者該刪、後者刪了就是丟掉好資料。**只有我方知道自己在重建。**
  ⚠ **`null` 不受此旗標影響**：對面不把 null 寫進史表（null 是涵蓋主張、不是觀測值），
    刪除規則也只作用在帶非 null 值的 EID 上 ⇒ **一輪覆蓋不可能清空某台的歷史**。

═══ 鐵則（違反即作廢）═══════════════════════════════════════════════════════════
  · **絕不寫全史 `MAX(stpEndTime)`**（B4 效能事故形狀：5 表大 view 上的 blocking
    aggregate，`fetch_fleet` 的 `SELECT DISTINCT` 在 7→14 天之間就有斷崖、d30 從 65s 起跳）。
    ⇒ 本檔的查詢**逐 class 點查、窗掃、零聚合下推**（view 上零 `DISTINCT`／零 `GROUP BY`、
    尾帶 `OPTION (RECOMPILE)`、無動態 EID `IN`-list），**最大值全部在 Python 算**。
    形狀與 `mes_timeline.fetch_segments`（已實證 @30d 秒級）逐字同型。
  · **站清單只有一份**＝`mes_timeline.EQP_STATIONS`（import 重用；**絕不另抄一個位元、
    絕不 runtime discovery**＝M4 教訓）。凍結模組的**常數引用**依 `CLAUDE.md` 明文
    「常數/純函式引用不在此限、且優於複製」——本檔**不呼叫**其任何 I/O 進入點。
  · **名冊只有一份**＝`board_eqmeeting.EQMT_CATALOG` ∪ `board_scope.PARENT_EXPANSION`
    （母機與其顯式子女；同樣 import 不抄）。
  · **end-filter 用 `>=` 不用 `>`**（`>` 會刪掉合法零工期段、八站整站隱形）。
  · **§24 人名紅線**：不取、不回、不記任何姓名／工號欄位；`stpPersonID` 於全檔 SQL 文本
    **零出現**（selftest 負斷言釘死＝`ALL_SQL()`）。
  · **兩把 token 不互相代用**：讀 API 票＝`BOARD_CMMS_TOKEN`；本檔用的**寫入票**＝
    `CMMS_Board_INGEST_TOKEN`（`/ingest/` 在 cmms 的 static bearer 豁免清單內）。
    **token 值永不入 repo、永不入 log、永不進任何 agent 的輸入。**
  · **唯讀**：對 MES 只有 SELECT；零 DDL/DML（連 `#temp` 都不需要）。

═══ 牆鐘 vs 計時器（`SPEC-WARM-CACHE.md` §8 的教訓，本檔逐條套用）═════════════════
  2026-08-14 暖機任務首日事故的根因是 **Modern Standby（S0ix）把所有計時器一起凍結**：
  `-TimeoutSec`／`MaxRoundMinutes`／`ExecutionTimeLimit` 三者全是計時器，機器一睡就停表，
  那支請求牆鐘 9,598 秒、清醒只有 ≈505 秒。**只有牆鐘追得到真實流逝。**
  ⇒ 本檔的預算 `_Budget` **同時**看 `time.monotonic()` 與 `datetime.now()`，**取兩者較大**
  （睡眠期間 QPC 是否推進屬平台相依，取大者＝結構上不吃虧）。逾預算＝停止掃描，
  **只送已經查到的事實子集、絕不憑空補 null**（見下方「部分掃描」）。

═══ 部分掃描的處理（本檔最需要小心的一條）═══════════════════════════════════════
  `last_segment_end: null` 是一個**主張**（「近 N 天內無訊號」）——它只有在**該窗被完整
  掃過**時才為真。若掃描因預算／錯誤中斷：
    · **不送任何 null**、**不更新狀態檔**（保住覆蓋不變式）；
    · 只送本輪真的查到的**非空事實**（那些逐筆為真）；缺席的那些由 pin ① 保證**不表達語意**；
    · **★ 舊值當地板**（`partial_snapshot`，2026-08-16 加）：只掃了前 k 站時，**跨多個 class
      的 EID（母機）**可能在本輪拿到比上一輪推過**更舊**的值 ⇒ 一律與狀態檔取 max。
      這不是涵蓋主張（那條由 null 管），是不把**已知事實降級**；
    · exit code 5（partial）＝排程 Last Result 看得見，但資料仍有送達。

═══ 增量狀態（為什麼 `lookback_days=30` 不等於每輪掃 30 天）═══════════════════════
  每 10 分鐘對 38 個 class 各掃 30 天＝重複做同一份工。本檔改為：
    · 狀態檔 `_state/last_production.json` 保存 `{eid: 末筆結束時刻}` 與**連續覆蓋起點**；
    · 每輪只掃 `--refresh-days`（預設 2 天，寬裕吸收遲到列與時鐘偏移），
      與舊值取 max（max 是單調的 ⇒ 合併永遠安全）；
    · **覆蓋不連續就自動退回整窗掃描**（首跑／狀態檔壞掉／停機超過 refresh 窗／
      `lookback_days` 改過／`--full`）。**寧可多掃一次，不要靜默少掃。**
  ⇒ 送出的 `lookback_days` **永遠是我方這次真的涵蓋到的天數**（cmms 逐筆存、顯示端讀
    payload 永不寫死 ⇒ 寫錯就是說謊）。

CLI:
    python core_lastprod_push.py                 # 查 MES → 組 payload → POST（排程任務走這條）
    python core_lastprod_push.py --dry-run       # 查 MES → 印 payload → **不送、不寫狀態**
    python core_lastprod_push.py --full          # 強制整窗掃描（忽略增量狀態）
    python core_lastprod_push.py --selftest      # 純離線斷言：零 MES、零網路、零檔案寫入
    python core_lastprod_push.py --status        # 印狀態檔摘要（含 station_lot；不連任何東西）

  ⚠ **任務已武裝之後，不要用 `--full` 送真的**（`--dry-run --full` 無妨，它不 POST）。
    `MultipleInstancesPolicy=IgnoreNew` 管的是**任務**——`schtasks /Run` 受它管，
    在 shell 直接跑這支**不受**；而 `--full` 又正好是**最可能宣告 `full_window_rebuild=true`
    （＝讓 cmms 刪列）的那一輪** ⇒ 「會刪列的輪」與「唯一能與排程輪並行的輪」是同一種輪。
    **要整窗重建：把 `_state/last_production.json` 挪開，再 `schtasks /Run /TN TendlineBoardLastProdPush`**
    （`plan_window` 會走 `full:no_state` ⇒ 效果相同、但走受管的那條路）。
    刻意**不加檔案鎖**：鎖的失效型是「靜默不推」，而對面改成留痕之後，靜默不推＝在他們史表留
    **永久空洞**。完整論證與 147 輪實測＝`SPEC-LASTPROD-PUSH.md` §8.1。

exit code（排程任務靠 Last Result 判健康；**每一種都先落 log**）：
    0  成功（HTTP 200）
    1  未預期例外
    2  not_configured（缺 base url 或缺 ingest token）＝**零網路嘗試**
    3  HTTP 400（`schema_id` 不合——不猜不容錯）
    4  HTTP 401（缺或錯 token）
    5  部分掃描（已送事實子集、狀態未更新）／HTTP 200 但掃描不完整
    6  HTTP 422（信封形不合、`lookback_days < 1`）
    7  其他 HTTP 碼／逾時／連不上
    8  MES 取數失敗（schema 分歧／連線失敗／整輪零涵蓋）
    9  HTTP 503（對方未設 token）
"""
import argparse
import hashlib
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import core_paths as _wp   # 執行期產物路徑唯一出處（Q-F；零相依）
import board_atomic as _wat   # 原子寫共用 helper（零相依）

# ── 積木一律 import 重用（絕不另抄一個位元）─────────────────────────────────────
#   · `mes_timeline.EQP_STATIONS`：39 個「有設備」的 segment class（人工審定、非 runtime
#     discovery；2026-09-02 加第 39 站 `segTopSealLube`）。凍結模組的**常數引用**依
#     CLAUDE.md 明文允許且優於複製；本檔不呼叫其 I/O。
#   · `mes_timeline.fold_eid`：形④ 別名折疊（§10.8-4）。**推送名冊上的 `EID-38106` 在 MES
#     沒有自己的段**，段全記在計數主 `EID-38041` 上 ⇒ 不折就會把「那台機從未生產」推給
#     cmms（＝拿「我沒折」當「它沒跑」）。折疊表只有一份，住凍結核心。
#   · `mes_qlik._connect` / `SchemaDivergence`：連線語意單一來源（同 `mes_wip`／`mes_rework` 慣例）。
#   · 名冊兩份常數：`EQMT_CATALOG`（固定名冊）＋`PARENT_EXPANSION`（母機→子工位）。
from mes_timeline import EQP_STATIONS, fold_eid, EID_RENAME, EID_ALIAS_DROP   # noqa: F401
from mes_qlik import _connect, SchemaDivergence            # noqa: F401
from board_eqmeeting import EQMT_CATALOG, STATION_LOT_PROXY
from board_scope import PARENT_EXPANSION
#   · 子工位名冊（v5 `sub_units`）＝`board_capacity`（形① nest-EAV／形② co-tag）。
#     **import 同一份、絕不另抄**（C-LP9 的同一條紀律）。
import board_capacity as _wcap                              # noqa: E402

#  ⚠ **stderr 也要 reconfigure**：本檔的 log 是中文，而排程任務底下的主控台編碼是
#    cp950/cp1252 ⇒ 一行中文就能讓 `print(..., file=sys.stderr)` 丟 UnicodeEncodeError
#    把整輪推送炸掉（「日誌把服務弄死」是最沒必要的死法）。
for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# =============================================================================
# 1. 人工審定常數（改任一項＝人工動作，且多半要與 cmms 對版）
# =============================================================================
SCHEMA_ID = "board.last_production.v1"     # cmms 端 400（不猜不容錯）就是比對這一字串
INGEST_PATH = "/ingest/last-production"

TAIPEI_OFFSET_H = 8                     # 台灣無日光節約（1980 起）⇒ 固定 UTC+8，不查 tz db

DEFAULT_LOOKBACK_D = 30                 # env BOARD_LASTPROD_LOOKBACK_D
#   ★ 30 的出處（SPEC §5「假設 A1」）：**唯一有實證的窗寬**——B4 的 P12a 對
#   `dbo.segment_equipment_v` 帶 `stpEndTime` 過濾實測 @30d = 3.5s；90 天從未實測。
#   relay 的例子寫 90、cmms 回信的例子寫 30，**雙邊都沒有定案值**（我方原話：「窗寬我方會
#   實測後定」）。⇒ 起手用有證據的那個；要放寬到 90 必須先照 perf lore 實測兩個窗寬。
DEFAULT_REFRESH_D = 2                   # env BOARD_LASTPROD_REFRESH_D（增量輪的窗）
#   2 天的理由：允許遲到列與時鐘偏移；**每 10 分鐘一輪 ⇒ 2 天有 288 倍餘裕**。
#   ⚠ 原本這行寫的理由是「MES ETL 落後 ~85 分鐘」——**2026-08-15 實測證偽**（見下方
#     TOTAL_BUDGET_SEC 那段）。餘裕本身照留（遲到列是真的），但別再引用那個數字。

HTTP_TIMEOUT_SEC = 30.0                 # 單次 POST 的 socket 逾時
HTTP_RETRIES = 1                        # 只對 timeout／unreachable 重試（HTTP 碼一律不重試）
QUERY_TIMEOUT_SEC = 180                 # 單條 SQL 的 ODBC 逾時（38 條各自）
TOTAL_BUDGET_SEC = float(os.environ.get("BOARD_LASTPROD_BUDGET_SEC", "480"))
#   8 分鐘牆鐘硬限；任務 ExecutionTimeLimit 設 PT10M（腳本自己先收手＝日誌講得出原因）。
#   ★ 預算必須 **< 推送間隔**：10 分鐘一輪的節奏下，一輪卡 25 分鐘會靜默吃掉兩輪
#     （`IgnoreNew` 只保證不重疊、不保證不漏）。實測一輪整窗 ~20s、增量 ~16s ⇒ 480s 有 24 倍餘裕。
#
#   ═══ ★ 2026-08-15 實測：「MES ETL 落後 ~85 分鐘」對這條查詢路徑不成立 ═══
#   我方 08-11 relay 用「ETL 本來就落後 ~85 分鐘、『即時』是假的」當理由，拒掉 cmms 原提的
#   即時拉取、改成每小時推。但 08-15 首跑 `--dry-run` 實測：`observed_at 17:49:33Z`，
#   最新一筆 `EID-00884` 是 `17:45:28Z`＝**只差 4 分 05 秒**（另有 5m57s／6m33s／12m 三筆）。
#   ⇒ `segment_equipment_v` 這條路徑**近乎即時**；那個 85 分鐘應屬別條管線（Qlik reload／
#     報表庫刷新），**不得再拿來論證推送節奏**。此為推送間隔改 10 分鐘的依據之一。
#   ⚠ 但也別倒過來衝太密：資料自身解析度就是 ~4–6 分鐘，**5 分鐘以下純粹重送相同值**。

STATE_REL = os.path.join(_wp.STATE_DIRNAME, "last_production.json")   # 相對形（保留相容）
SUB_UNITS_ENV = "BOARD_LASTPROD_SUB_UNITS"
# `sub_units` is an opt-in wire addition.  The local state field has existed
# since v5, but cmms NOTE-71(iv) has not yet confirmed that its ingest ignores
# unknown item keys.  Keep the wire off unless an operator explicitly enables
# it at the same User environment scope as the scheduled task's other settings.
#   ★★ 本檔有第二個「唯讀」消費者（2026-08-17，SPEC v1.2 第七則）：`/equipment` Now 欄經
#     `board_data.parse_lastprod_state` 讀它 ⇒ **改 STATE_SCHEMA 或 eids 欄位語意，必須同輪
#     更新該 reader**（它對不認識的 schema 整檔拒收＝頁面退回 MES 探測，安全但退掉換源收益）。
STATE_SCHEMA = "board.lastprod.state.v6"
#   ★ v5→v6（2026-09-08，#104(b)「生產（推斷）」＝`SPEC-LANDING.md` §18.26）：
#     信封層**加性**一個鍵 `station_lot`＝`{站: {"last_prod_end": Z|null, "asof": Z}}`
#     （另一個可選的診斷鍵 `station_lot_err`）。
#     · **`eids` 與 `sub_units` 的語意一個位元不動** ⇒ 同 v4→v5，這是加欄不是換口徑。
#     · 名冊唯一出處＝`board_eqmeeting.STATION_LOT_PROXY`（五站六台；本檔不長第二份）。
#     · 值走**新的固定參數化查詢** `build_station_lot_sql`（站別、零設備條件）。
#     · **`build_payload` 一個位元不碰它**：cmms payload `board.last_production.v1` 照舊
#       （C-LP 既有釘全綠；這一欄是**本地 reader 欄**，只給 landing 讀）。
#     · **v5 與 v4 都可就地升級**（`STATE_SCHEMA_UPGRADABLE`）：拒收的代價是**對外**的
#       （下一輪 `full:no_state` ⇒ `full_window_rebuild=true` ⇒ cmms 那側刪資料重建）。
#     · `board_data.parse_lastprod_state` **同輪**認得 v6（reader 同步鐵則，見本檔上方）。
#   ★ v4→v5（2026-09-08，Chris「每一台有子工位的機台都要各自上色」＝`SPEC-LANDING.md` §18.23）：
#     信封層**加性**一個鍵 `sub_units`＝`{eid: {unit_id: 末筆生產級段結束時刻 Z}}`。
#     · **`eids` 的語意一個位元不動**（v4 的 8 個欄逐字保留）⇒ 這是加欄不是換口徑。
#     · 名冊唯一出處仍是 `board_capacity`（形① `nest:<n>`、形② 子 EID）；本檔不長第二份。
#     · 形② 的值**零新查詢**——子 EID 本來就是 `segment_equipment_v` 上的 EID、
#       既有全站掃描早就把它們的 production-grade 末段算出來了（實測 live 狀態檔 v4 內
#       `EID-05707`／`EID-07757`／`EID-25460`／`EID-27182` 四筆都在）；本檔只是把它們
#       **投影**到母機底下，不重查。
#     · 形① 的值要一組**新的固定參數化查詢**（`build_nest_sql`）：nest 編號在 EAV 兩表裡、
#       `segment_equipment_v` 上根本沒有這個欄（`_NEST-PROBE-41-README.md` 2026-08-31 事故）。
#     · ★★ **v4 檔就地升級、不整窗重建**（2026-09-08 Fable 審後裁，**與 v1→v2／v3→v4 相反**）：
#       `upgrade_state()` 把 schema 字串改 v5、補 `sub_units = {}`、其餘欄逐字保留 ⇒ 本輪照
#       incremental 走、**`full_window_rebuild` 恆 False**。理由＝那兩次是換口徑（`eids` 欄位
#       語意變了、舊值當新值用會靜靜講錯話），這次只是加欄；而拒收的代價是**對外**的
#       （排程任務下一輪就會讓 cmms 刪資料重建，未經 Chris 同意、未先 relay）。逐條見
#       `STATE_SCHEMA_UPGRADABLE` 上方註解與 `SPEC-LANDING.md` §18.23.5。
#     · `board_data.parse_lastprod_state` 那側同樣**同時收 v4 與 v5**
#       ——`eids` 的語意沒變，讓 `/equipment` 在「新碼已上線、push 還沒跑過」的空窗期不退回探測。
#     · cmms payload `board.last_production.v1` **一個位元不變**（`build_payload` 不碰 `sub_units`）。
#   ★ v3→v4（2026-09-07，Chris 生產級訊號裁決）：`eids` 記錄加 `prod_*` 四鍵，保存最近一段
#     `matDesignation ∈ {Production, Engineering (Baseline)}` 的原始段事實；較新的
#     Engineering (R&D) 段仍照常留在既有 end/start/designation/result 四鍵。**刻意不寫 v3
#     遷移**（同 v1→v2 前例）：`load_state` 對 v3 檔回 None ⇒ 部署後第一輪走
#     `full:no_state` 整窗重建。cmms payload `board.last_production.v1` 一個位元不變。
#   ★ v1→v2（2026-08-17）：`eids` 的值從單一 Z 字串變成 `{end, start, designation}` 記錄
#     （cmms 第十七輪加欄）。**刻意不寫 v1 遷移**：`load_state` 對 v1 檔回 None ⇒ 部署後
#     第一輪走 `full:no_state` 整窗重建＝**史上第一次真的送出 `full_window_rebuild=true`**
#     （對面刪除分支首次對真實資料執行）——這是**預告過的一次性事件**，部署前必須先 relay。
LOG_DIR_REL = "_logs"

# Chris 2026-09-07 領域裁決：只有這兩個 MES material designation 是「生產訊號」。
# Engineering (R&D) 是維修／製程試跑，不得拿來當復機或生產證據；比較保留 MES 原字面、
# 不 upper／trim／映射。
PRODUCTION_DESIGNATIONS = ("Production", "Engineering (Baseline)")

# #122(a), 2026-09-10：Kestrel 的四個設備段站，只給 last-production 推送補掃。
# 出處＝`_AUDIT-PUSH-COVERAGE-20260909.md` §8.1／探針
# `_PROBE-PUSH-COVERAGE-RESULT.tsv` P1。這是**推送專用附加清單、不是稼動口徑**：
# `EQP_STATIONS` 的 39 站是另一個裁決（`ROADMAP #122 (b)`），本包不動它。
# `segBottomOringLubrication` 刻意不收（4 段查無生產 lot）；`segMESReserved` 也刻意不收
# （系統／借用桶）。(b) 若日後把四站納入 39 站，2.14c 會紅，提醒撤掉此附加清單。
LASTPROD_EXTRA_CLASSES = ('segSealDispenseDamAndFill',
                          'segGrout',
                          'segUVCurePostDamAndFill',
                          'segUVCurePostGrout')


def lastprod_scan_classes():
    """推送掃描站＝凍結的 39 稼動站，後接 #122(a) 四個 Kestrel 專用補掃站。

    這是推送的資料完整性補丁，不是 `EQP_STATIONS` 的第二份或其稼動口徑延伸；兩清單
    必須互斥，結果也必須無重複。"""
    base = tuple(EQP_STATIONS)
    extra = tuple(LASTPROD_EXTRA_CLASSES)
    assert not set(base) & set(extra), "LASTPROD_EXTRA_CLASSES 不得與 EQP_STATIONS 重疊"
    out = base + extra
    assert len(out) == len(set(out)), "lastprod scan classes 不得重複"
    return out


# `ALL_SQL()` 在 #122(a) 前的逐位元 SHA-256：掃描範圍可擴，SQL 文本不可偷換。
ALL_SQL_PRE_LASTPROD_EXTRA_SHA256 = "f4e6d89d1d80f147c00304368c23319e1031eb9521ed818d23e20fa184230678"

# ingest token 的來源（**值永不入 repo、永不入 log**）
TOKEN_ENV = "CMMS_Board_INGEST_TOKEN"
BASE_ENV = "BOARD_CMMS_BASE_URL"       # 與讀取線共用 base（同一台 cmms）
READ_TOKEN_ENV = "BOARD_CMMS_TOKEN"    # ★ 只用來做「別拿讀取票去寫」的負向檢查
SECRETS_FILE_ENV = "BOARD_CMMS_SECRETS_FILE"
DEFAULT_SECRETS_FILE = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")), ".secrets", "cmms.md")

# 推送範圍：roster＝只推名冊；roster+observed＝名冊 ∪ 本窗 MES 見過的所有 EID（預設）
SCOPE_ROSTER = "roster"
SCOPE_ROSTER_OBSERVED = "roster+observed"
DEFAULT_SCOPE = os.environ.get("BOARD_LASTPROD_SCOPE", SCOPE_ROSTER_OBSERVED)

# exit code 表（docstring 的機器版；classify_response 直接吐這些值）
EXIT_OK, EXIT_UNEXPECTED, EXIT_NOT_CONFIGURED = 0, 1, 2
EXIT_HTTP_400, EXIT_HTTP_401, EXIT_PARTIAL = 3, 4, 5
EXIT_HTTP_422, EXIT_HTTP_OTHER, EXIT_MES, EXIT_HTTP_503 = 6, 7, 8, 9
# WP-X1b：退出碼狀態檔（`_state/task_exit/TendlineBoardLastProdPush.json`）的名稱表與任務名。
# conhost --headless 不傳回子進程退出碼 ⇒ schtasks Last Result 恆 0；真退出碼只能靠這檔。
TASK_NAME = "TendlineBoardLastProdPush"
EXIT_NAMES = {EXIT_OK: "EXIT_OK", EXIT_UNEXPECTED: "EXIT_UNEXPECTED",
              EXIT_NOT_CONFIGURED: "EXIT_NOT_CONFIGURED", EXIT_HTTP_400: "EXIT_HTTP_400",
              EXIT_HTTP_401: "EXIT_HTTP_401", EXIT_PARTIAL: "EXIT_PARTIAL",
              EXIT_HTTP_422: "EXIT_HTTP_422", EXIT_HTTP_OTHER: "EXIT_HTTP_OTHER",
              EXIT_MES: "EXIT_MES", EXIT_HTTP_503: "EXIT_HTTP_503"}

REPO = os.path.dirname(os.path.abspath(__file__))


def state_file_path():
    """狀態檔路徑（呼叫時才讀 `BOARD_STATE_DIR`；未設＝`<REPO>/_state/last_production.json` 逐字）。"""
    return _wp.state_path("last_production.json", repo=REPO)


# =============================================================================
# 2. 純函式：時間軸（pin ②）
# =============================================================================
def taipei_to_utc_dt(dt_taipei_naive):
    """MES 台北牆鐘 naive datetime → **naive UTC** datetime（秒精度）。None → None。

    ★ pin ②的落點之一：MES 的 `stpEndTime` 是台北牆鐘 naive（台灣無日光節約、固定 UTC+8）。
      **不可裸呼 `astimezone()`**——那會吃執行機器的本地時區＝平台相依（cmms 側 `_as_utc`
      踩過同一顆坑，NOTE-39 有記）。本檔一律用固定偏移直接扣。
    ★ 轉換在 **I/O 邊界一次做完**（`fetch_last_ends`）⇒ 邊界之後的一切（狀態檔、比較、
      序列化）全部活在 UTC 一種時間裡，**結構上不可能混軸**。
    ★ 微秒截掉：MES 帶微秒時逐輪推送會產生無意義的位元差異。
    """
    if dt_taipei_naive is None:
        return None
    d = dt_taipei_naive
    if getattr(d, "tzinfo", None) is not None:
        # 已帶時區者先正規化到台北牆鐘，再走同一條路徑（避免兩套語意）
        d = d.astimezone(_fixed_tz(TAIPEI_OFFSET_H)).replace(tzinfo=None)
    return (d - timedelta(hours=TAIPEI_OFFSET_H)).replace(microsecond=0)


def to_utc_z(dt_taipei_naive):
    """MES 台北牆鐘 naive datetime → UTC ISO `Z`（秒精度）。None → None。"""
    d = taipei_to_utc_dt(dt_taipei_naive)
    return None if d is None else d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixed_tz(hours):
    from datetime import timezone
    return timezone(timedelta(hours=hours))


def parse_utc_z(s):
    """UTC ISO `Z` → naive UTC datetime。解析不了 → None（狀態檔壞掉不炸整輪）。"""
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if not t.endswith("Z"):
        return None
    try:
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def utc_now():
    """現在（naive UTC，秒精度）。單一取現在的地方＝selftest 可注入。"""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


# =============================================================================
# 3. 純函式：名冊（pin：per-EID 逐台、含模組 EID，不捲到母機）
# =============================================================================
def roster_eids(catalog=None, parents=None):
    """推送名冊＝`EQMT_CATALOG` ∪ 形③ 母機 ∪ 其顯式子女（去重、排序）。

    ★ 為什麼母機也要推：cmms 的維修單**開得在母機上**（A10 那條線的起因就是這個），
      那張單上的對齊按鈕一樣需要快照。母機在 `segment_equipment_v` 上有自己的列
      （一個 segment 同時掛母機與模組），所以它拿到的是**自己的事實**、不是子工位的聚合
      ——「不捲到母機」講的是**不做聚合**，不是「不推母機」。
    ★ `EID-27617`（CLN-2 SF E-Test）不在 `PARENT_EXPANSION` 亦不在名冊 ⇒ 天然不入列。
    """
    cat = EQMT_CATALOG if catalog is None else catalog
    par = PARENT_EXPANSION if parents is None else parents
    out = {eid for _area, _name, eid in cat}
    for parent, kids in par.items():
        out.add(parent)
        out.update(kids)
    return sorted(out)


# =============================================================================
# 4. 純函式：SQL builder（離線可斷言形狀；唯讀、零聚合下推）
# =============================================================================
def build_lastprod_sql(with_designation=True, with_result=True, hi_days_ago=None):
    """逐 class 拉「窗內每一段的 (EID, 結束時刻, 起點, 物料 designation, 測試結果)」。
    回 `(sql, param_names)`。

    ★ 形狀＝`mes_timeline.fetch_segments` 逐字同型（該形狀 B4 已實證 @30d 秒級）：
      · **一次只點查一個 class**（`SegmentClassID = ?`）——絕不全線掃描；
      · **無 EID `IN`-list**（B2 事故：設備側欄位的 IN-list 在大窗翻轉計劃 → HYT00）；
      · **view 上零 `DISTINCT`／零 `GROUP BY`／零 `MAX()`**（B4 事故：blocking aggregate
        在 7→14 天之間斷崖）⇒ 取最大值在 Python 做；
      · 尾 `OPTION (RECOMPILE)`（各站大小差異極大、參數嗅探爛計劃）；
      · `stpEndTime IS NOT NULL AND stpEndTime >= stpStartTime`——**`>=` 不是 `>`**
        （pin ③：零工期段是合法瞬時事件段，`>` 會讓八個站整站隱形＝M4 事故）。
    ★ 2026-08-17 起回**四欄**（v0.1–v0.8 只回兩欄）：cmms 第十七輪要「停機終點對齊」，
      `stpStartTime`（末段起點）與 `matDesignation`（物料 designation；dummy print 的
      結構性記號＝`Engineering (R&D)`，WO-35966 窗實測）從此**是事實的一部分**。
      加欄零聚合、零 join、列數不變 ⇒ 不動 C-LP1 的任何一條。
    ★ `with_designation=False`＝降級形（`matDesignation` 欄從 view 消失時仍推核心事實；
      guard 判定，見 `GUARD_KEYS`）。
    ★ `with_result=False`＝第二個降級形（2026-08-22 #31 加 `stpTestResult`，同一級：輔助欄
      消失 ⇒ 該欄全 null、核心事實照推）。⚠ 從此有**兩個**可降級欄 ⇒ **四種組合全部要
      登記進 `ALL_SQL()`**（清單缺一形＝負斷言有漏網）。
    ★ 欄序固定＝eid, end, start[, designation][, result]——呼叫端 `fetch_last_ends` 依旗標
      算索引，**result 恆在 designation 之後**（兩旗標獨立、次序不重排）。

    ═══ ★★ `hi_days_ago`＝**選配上界**（2026-08-28 加；預設 None ⇒ 一個位元都不變）══════
      · **`None`（預設）⇒ 輸出字串與 `param_names` 與 2026-08-28 之前逐位元相同**
        （推送本體、`_probe_kun_activity`、`core_prod_calendar` 三個既有呼叫者零改動；
        selftest 有 byte-identical 釘）。
      · 給值 ⇒ 在**下界錨的正後方**多一行
        `AND se.stpStartTime < DATEADD(day, -?, GETDATE())`，
        `param_names` 變成 `("cls", "days_lo", "days_hi")`
        ⇒ 掃描窗從「[now-days, now]」變成**半開有界窗 `[now-lo, now-hi)`**。
      · **`<` 是半開、不是 `<=`**：相鄰兩段用 `<=` 會把邊界上那一段**各算一次**
        （分母是計數 ⇒ 重複計數是靜默的錯）。
      · ⚠ **值本身不進 SQL 文本**（照樣是 `?` 參數、呼叫端 bind）——這個參數收的是
        **語意**（「這一輪要不要上界、上界在幾天前」），不是字串拼接的素材：
        **不同的 `hi_days_ago` 值產生逐位元相同的 SQL**（selftest 釘）⇒ 零注入面。
        收值而不是收布林旗標，是為了讓呼叫端的那一行自己說得出上界在哪
        （傳布林的版本，`?` 綁錯順序時沒有任何東西會紅）。
      ★ **為什麼可以加**：2026-08-28 Chris 域內實測（`SPEC-RUN-COST.md` §9.13-11）——
        三站 × 三片**等寬 91 天**切片（近／中／遠）全部 **7.4–12.3s**、比值 0.60–1.09
        ⇒ **斷崖是「窗寬」造成、不是「資料位置」**（判準 A 命中）⇒ 有界窗可行。
        送出去實測過的那個形，就是這裡長出來的形（`_probe_cost_rollup.build_slice_sql`
        的 candidate ＝ 本形逐位元相同，selftest 兩邊互釘）。
      ⚠ **推送路徑（`fetch_last_ends`）不用它**：推送要的是「近 N 天的末筆」，窗恆錨在 now
        ⇒ 那條路徑永遠走 `hi_days_ago=None`。上界是**回補探針**的東西。
    """
    if hi_days_ago is not None and (isinstance(hi_days_ago, bool)
                                    or not isinstance(hi_days_ago, (int, float))
                                    or hi_days_ago < 0):
        # 拒絕構造（不猜、不硬拼）：布林是「把語意參數當旗標用」的典型誤用，
        # 負值是「lo/hi 寫反」的典型誤用——兩種都會安靜地量到／算到錯的窗。
        raise ValueError("hi_days_ago 必須是 None 或非負數（不接受布林）"
                         f"，got {hi_days_ago!r}")
    cols = "se.astEquipmentID, se.stpEndTime, se.stpStartTime"
    if with_designation:
        cols += ", se.matDesignation"
    if with_result:
        cols += ", se.stpTestResult"
    hi_line = ("" if hi_days_ago is None
               else "  AND se.stpStartTime < DATEADD(day, -?, GETDATE())\n")
    sql = (f"SELECT {cols}\n"
           "FROM dbo.segment_equipment_v se\n"
           "WHERE se.SegmentClassID = ?\n"
           "  AND se.stpStartTime >= DATEADD(day, -?, GETDATE())\n"
           + hi_line +
           "  AND se.stpStartTime <= GETDATE()\n"
           "  AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime\n"
           "OPTION (RECOMPILE)")
    return sql, (("cls", "days") if hi_days_ago is None
                 else ("cls", "days_lo", "days_hi"))


def nest_query_pairs(register=None):
    """形① 要點查的 `(class, EAV 變數名)` 組合（去重、排序）。**名冊唯一出處＝
    `board_capacity.NEST_EAV_UNITS`**（**多台收斂成少數幾組**；2026-09-08 深夜 AOI 那幾台
    退出名冊，見 `SPEC-LANDING.md` §18.30.4／**C-LD46**；組數隨之減少）。
    ⚠ **本函式碼本身一個字不用改**：組合全部由名冊推（這裡零硬編 EID、零硬編
      `varBLSM_Nest`／`varPostCure_Nest`）——那正是「名冊唯一出處」買到的東西。

    ⚠ 為什麼是 (class, var) 而不是 EID：對 live view 下 `astEquipmentID IN (…)` 是 B2 事故
      形（大窗翻轉計劃 → HYT00）⇒ **逐 class 點查、EID 在 Python 篩**（同 `fetch_last_ends`）。
    """
    reg = _wcap.NEST_EAV_UNITS if register is None else register
    return tuple(sorted({(v["main_cls"], v["var"]) for v in reg.values()}))


def build_nest_sql(with_designation=True):
    """形① nest-EAV 的窗掃 SQL。回 `(sql, param_names)`；參數序＝**`("var", "cls", "days")`**
    （＝文本裡 `?` 出現的順序：`var` 在 join 條件上、`cls` 在 where 上）。

    ★ **每一個表名／欄名都有出處**（`_NEST-PROBE-41-README.md` 的逐欄出處表；那份 PART A
      已由 Chris 於域內 SSMS 實跑過）：
        · `dbo.segment_equipment_v se`：`astEquipmentID`／`stpPK`／`stpStartTime`／
          `stpEndTime`／`SegmentClassID`（probe PART A）＋`matDesignation`
          （**本檔既有 `build_lastprod_sql` 就在選它**，同一個 view、同一個別名）。
        · `dbo.segment_integer_data_t si`：`stpPK`／`vdfPK`／`sgiInteger`（probe PART A）。
        · `dbo.production_data_definition_t vdf`：`vdfPK`／`vdfProductionDataID`（probe PART A）。
      **沒有任何一個欄名是猜的**——2026-08-31 `se.NestNo` 事故就是猜出來的（Msg 207）。

    ★ 效能形狀＝`build_lastprod_sql` 與 probe PART A 的交集，一條不放寬：
      逐 class 點查、零 EID `IN`-list、view 上零 `DISTINCT`／`GROUP BY`／`MAX()`
      （最大值一律在 Python 算）、`stpEndTime >= stpStartTime`（`>=` 不是 `>`）、
      窗恆錨在 `GETDATE()`、尾 `OPTION (RECOMPILE)`。
      ⚠ 比推送本體多兩個 join，但**兩張都是小維度表**且以 `stpPK`／`vdfPK` 相接
      （probe PART A 在 91 天片上跑過同一個形）；本檔的窗是推送自己的窗（增量輪 2 天）。

    ★ `var` 是**參數不是字面**（`vdf.vdfProductionDataID = ?`）：值來自我方名冊、不是使用者
      輸入，但走參數就結構上零注入面，且不同 var 產生**逐位元相同**的 SQL（selftest 釘）。
    ★ `with_designation=False`＝降級形（同 `build_lastprod_sql` 的那一級）：沒有 designation
      就沒有「生產級」這個概念 ⇒ 呼叫端一律不產出 `sub_units`（不猜）。
    """
    cols = "se.astEquipmentID, se.stpEndTime, se.stpStartTime"
    if with_designation:
        cols += ", se.matDesignation"
    cols += ", si.sgiInteger"
    sql = (f"SELECT {cols}\n"
           "FROM dbo.segment_equipment_v se\n"
           "JOIN dbo.segment_integer_data_t si ON si.stpPK = se.stpPK\n"
           "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = si.vdfPK\n"
           " AND vdf.vdfProductionDataID = ?\n"
           "WHERE se.SegmentClassID = ?\n"
           "  AND se.stpStartTime >= DATEADD(day, -?, GETDATE())\n"
           "  AND se.stpStartTime <= GETDATE()\n"
           "  AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime\n"
           "OPTION (RECOMPILE)")
    # ⚠ 參數順序＝**SQL 文本裡出現的順序**（var 在 join 上、cls 在 where 上）。
    return sql, ("var", "cls", "days")


def build_station_lot_sql():
    """#104(b) 站別 lot 推斷的窗掃 SQL。回 `(sql, param_names)`；參數序＝**`("cls", "days")`**。

    ★ **這一條與本檔其他每一條查詢不同的地方只有一個：它問的是「站」不是「機」。**
      對 `board_eqmeeting.STATION_LOT_PROXY` 的五個站各點查一次，把窗內每一段的
      `(stpEndTime, stpStartTime, matDesignation)` 拉回來，**最大值在 Python 算**
      （`max_prod_end_by_station`）。`SPEC-LANDING.md` §18.26.3 那句「取 `MAX(stpEndTime)`」
      講的是**語意**；SQL 文本裡**一個聚合都不准有**（`ALL_SQL()` 的 2.1／2.3／2.4 負斷言
      掃的就是這件事＝B4 斷崖）。

    ★ **零設備條件**（`astEquipmentID` 在文本裡零出現）：這五站的 lot 段在
      `dbo.segment_equipment_v` 上**本來就沒有列**——那正是這幾台治具「量不到」的原因。
      加上設備條件會查到空集合，然後把「我問錯了」講成「它沒在跑」。

    ═══ 表與欄的出處（[[ssms-sql-column-provenance]]：每一個名字都追得到跑過的查詢）═══
      **逐字抄 `_PROBE-NOMES-STATION.sql` STEP 1 建 `#seg` 的那一段**（Chris 2026-09-08
      19:38 在 SSMS 實跑成功、結果存證＝`_PROBE-NOMES-STATION-RESULT.tsv`）：
        · `dbo.process_segment_v v` —— `stpStartTime`／`stpEndTime`／`stpTestResult`／
          `SegmentClassID`／`matMaterialLotID`／`matDesignation`／`mclMaterialClassID`。
          該探針的欄位出處表另有第二來源（MES KB 的 `table_column_inventory.csv`），
          兩份都寫在探針檔頭 ①。
      **與探針 `#seg` 的差異只有三處，逐條說得出理由**：
        ① 窗錨從 `@ws`／`@we`（台北日曆日）改成本檔慣用的 `DATEADD(day, -?, GETDATE())`
           ＋ `<= GETDATE()`——推送問的是「近 N 天的末筆」，窗恆錨在 now
           （與 `build_lastprod_sql`／`build_nest_sql` 同形）。
        ② 加 `stpEndTime IS NOT NULL AND stpEndTime >= stpStartTime`——探針把它算成 `closed`
           欄留給 STEP2 當分母，本檔要的是「末筆結束時刻」⇒ 沒收尾的段沒有結束時刻。
           **`>=` 絕不 `>`**（零工期段是合法瞬時事件段＝M4 事故）。
        ③ 探針的 `SegmentClassID NOT IN (三個站)` **不抄**：本檔是**逐站點查**
           （`= ?`），而參數只可能來自 `STATION_LOT_PROXY` 的五個站——那三個被排除的站
           結構上不可能成為參數（selftest 有負斷言）⇒ 抄過來是一條永遠為真的死條件。
      **designation 的過濾刻意留在 Python**（`PRODUCTION_DESIGNATIONS`，與
      `max_prod_end_by_eid`／`max_prod_end_by_unit` **同一把尺**）：SQL 裡多一份字面清單
      ＝同一個裁決在兩個地方各寫一次，哪天 Chris 改口徑就會漏改一邊。
    """
    sql = ("SELECT v.stpEndTime, v.stpStartTime, v.matDesignation\n"
           "FROM dbo.process_segment_v v\n"
           "WHERE v.SegmentClassID = ?\n"
           "  AND v.stpStartTime >= DATEADD(day, -?, GETDATE())\n"
           "  AND v.stpStartTime <= GETDATE()\n"
           "  AND v.stpEndTime IS NOT NULL AND v.stpEndTime >= v.stpStartTime\n"
           "  AND v.stpTestResult IN (N'PASS', N'FAIL')\n"
           "  AND LEFT(v.matMaterialLotID, 2) IN (N'B0', N'M0')\n"
           "  AND v.mclMaterialClassID IN (N'assyModule', N'assyUnit',\n"
           "                               N'assyPackagedUnit', N'assyEndOfLine',\n"
           "                               N'assyProductionReliabilityTesting')\n"
           "OPTION (RECOMPILE)")
    return sql, ("cls", "days")


def build_known_eids_sql():
    """設備主檔全量 EID（~1,000 列的小維度 view）。用來分流 pin ① 的 `unknown[]`。

    「我方 MES 不認識這台」＝ 這個 EID **不在設備主檔裡**；
    「近 N 天沒有訊號」＝ 在主檔裡但窗內零段。**兩者互斥**，不得混為一談。
    """
    return "SELECT e.astEquipmentID\nFROM dbo.equipment_v e\nOPTION (RECOMPILE)"


GUARD_KEYS = ("view_seg_ast", "astEquipmentID", "stpStartTime", "stpEndTime",
              "SegmentClassID", "matDesignation", "stpTestResult",
              "view_equipment", "eq_astEquipmentID")
BEARING_GUARD_KEYS = ("view_seg_ast", "astEquipmentID", "stpStartTime", "stpEndTime",
                      "SegmentClassID")
# ★ `matDesignation` 與 `stpTestResult`（#31，2026-08-22）**刻意不進 BEARING**（可降級，
#   同 `view_equipment` 那級）：任一消失時退回少一欄的 SQL 繼續推核心事實（end/start），
#   該欄全 null＋log WARN。放進 bearing ＝ 一個輔助欄的 schema 變動就讓整條推送停擺 ⇒
#   對面史表出現**永久空洞**——那正是 §8.1 ③「不加檔案鎖」拒絕過的同一種 fail-dangerous
#   交換。`stpTestResult` 另有 MES KB stale-risk 標記（SPEC v1.4 ②：Qlik 主力 script 在用、
#   對帳殘差零 ⇒ 判定是活欄），可降級＝那個風險真發生時的自動安全網。


def build_column_guard_sql():
    """column guard 的 SQL 文本（純函式＝離線可斷言形狀；欄序＝`GUARD_KEYS`）。

    兩級：**承重**（`segment_equipment_v` 與其四欄）ABSENT ⇒ `SchemaDivergence` 具名停下
    ——停下而不是跑出「全部 null」的快照，因為**全 null 的快照在對面看起來就是
    「整條線 30 天沒生產」**＝最壞的靜默謊。
    **可降級**（`equipment_v`）ABSENT ⇒ `unknown[]` 判定 SKIPPED、全部 fail-open 進 `items`
    （「窗內無訊號」對那些 EID 仍然為真，只是少了一個更精確的分流）。
    """
    return """
    SELECT CASE WHEN OBJECT_ID('dbo.segment_equipment_v') IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.segment_equipment_v','astEquipmentID')           IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.segment_equipment_v','stpStartTime')             IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.segment_equipment_v','stpEndTime')               IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.segment_equipment_v','SegmentClassID') IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.segment_equipment_v','matDesignation')           IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.segment_equipment_v','stpTestResult')            IS NULL THEN 0 ELSE 1 END,
           CASE WHEN OBJECT_ID('dbo.equipment_v') IS NULL THEN 0 ELSE 1 END,
           CASE WHEN COL_LENGTH('dbo.equipment_v','astEquipmentID')                   IS NULL THEN 0 ELSE 1 END
    OPTION (RECOMPILE)
    """


def assert_guard(guard):
    """承重欄檢查（純函式＝離線可測）。ABSENT ⇒ `SchemaDivergence`，訊息逐欄具名。"""
    if not guard.get("view_seg_ast"):
        raise SchemaDivergence("schema divergence: dbo.segment_equipment_v ABSENT")
    missing = [c for c in BEARING_GUARD_KEYS[1:] if not guard.get(c)]
    if missing:
        raise SchemaDivergence("schema divergence: "
                               + ", ".join(f"{c} ABSENT" for c in missing))


def ALL_SQL():
    """本模組會送出的**全部 SQL 文本**（selftest 形狀／負斷言的唯一清單）。

    新增任何查詢**必須**登記在這裡——否則 `stpPersonID` 零出現、`MAX(` 零出現、
    view 上零 `DISTINCT`／`GROUP BY` 那幾條負斷言會有漏網之魚
    （`mes_operator.ALL_SQL` / `mes_rework.ALL_SQL` 同款制度）。

    ★ 2026-08-28：**有界形（`hi_days_ago` 給值）四種組合一併登記**（共 8 條窗掃形）。
      推送本體不走有界形，但**本模組建得出來的 SQL 就是本模組的責任**——負斷言掃的是
      「這個 builder 會不會吐出違規文本」，漏登記＝那四條文本沒有人掃。
    """
    return (build_lastprod_sql(with_designation=True, with_result=True)[0],
            build_lastprod_sql(with_designation=True, with_result=False)[0],
            build_lastprod_sql(with_designation=False, with_result=True)[0],
            build_lastprod_sql(with_designation=False, with_result=False)[0],
            build_lastprod_sql(with_designation=True, with_result=True, hi_days_ago=0)[0],
            build_lastprod_sql(with_designation=True, with_result=False, hi_days_ago=0)[0],
            build_lastprod_sql(with_designation=False, with_result=True, hi_days_ago=0)[0],
            build_lastprod_sql(with_designation=False, with_result=False, hi_days_ago=0)[0],
            # ★ 2026-09-08 v5：形① nest-EAV 的兩形（有／無 designation）一併登記
            #   ——負斷言（`stpPersonID` 零出現、view 上零 `DISTINCT`／`GROUP BY`／`MAX(`）
            #   掃的是「這個 builder 吐得出來的文本」，漏登記＝那兩條沒有人掃。
            build_nest_sql(with_designation=True)[0],
            build_nest_sql(with_designation=False)[0],
            # ★ 2026-09-08 v6：#104(b) 站別 lot 推斷那一條（`SPEC-LANDING.md` §18.26.3）。
            #   它是本檔**唯一**打 `dbo.process_segment_v` 的查詢 ⇒ 更要進負斷言清單。
            build_station_lot_sql()[0],
            build_known_eids_sql(), build_column_guard_sql())


# =============================================================================
# 5. 純函式：觀測合併 / 狀態 / payload
# =============================================================================
def _seg_key(rec):
    """段記錄 `(end, start, designation, result)` 的全序 key（**比較的主軸恆是 end**）。

    ★ tiebreak 只為**確定性**：兩段 end 相同時對「末筆結束時刻」完全等價，取 key 較大者
      ＝逐輪重跑挑同一段（start 較晚者、再比 designation、再比 result 字串）
      ——不表達任何語意偏好。
    """
    end, start, desig, result = rec
    return (end, start, desig or "", result or "")


def max_end_by_eid(rows, prev=None):
    """`[(eid, end_dt, start_dt, designation, result), …]` → `{eid: (end, start, designation, result)}`。

    ★ 2026-08-17 起值是**整段記錄**（cmms 第十七輪加欄；2026-08-22 #31 再加 result）；
      選段仍＝end 最大（argmax）。**配對原子性**：start／designation／result 必須跟
      **自己那一段的 end** 一起走——選了哪段的 end 就送哪段的 start／result，
      絕不把 A 段的 end 配 B 段的 start（那是一個沒人量過的假段）。
    ★ pin ③：**零工期段照收**——對 `end` 的內容零判斷（不濾、不圓整）；`start == end` 合法。
    ★ `prev` 可帶入既有 map 續算（增量輪；key 比較單調 ⇒ 合併恆安全）。
    ★ 防呆：`start` 為 None（SQL 過濾 `end >= start` 結構上保證非 null；只可能來自注入的
      假 cursor）⇒ 塌成零工期形 `start = end`——不發明第二種形狀、也不丟掉真的 end。
    """
    out = dict(prev or {})
    for eid, end, start, desig, result in rows:
        if not eid or end is None:
            continue
        rec = (end, end if start is None else start, desig, result)
        cur = out.get(eid)
        if cur is None or _seg_key(rec) > _seg_key(cur):
            out[eid] = rec
    return out


def max_prod_end_by_eid(rows, prev=None):
    """Production-grade argmax for the same MES rows as :func:`max_end_by_eid`.

    Only the closed designation set in ``PRODUCTION_DESIGNATIONS`` is eligible.  The
    returned record remains the complete, paired `(end, start, designation, result)`
    fact of the selected segment; an Engineering (R&D) segment may be newer in the
    ordinary last-segment map without replacing this map.  Missing designation is a
    deliberate degraded form: no production-grade fact is invented.
    """
    out = dict(prev or {})
    for eid, end, start, desig, result in rows:
        if not eid or end is None or desig not in PRODUCTION_DESIGNATIONS:
            continue
        rec = (end, end if start is None else start, desig, result)
        cur = out.get(eid)
        if cur is None or _seg_key(rec) > _seg_key(cur):
            out[eid] = rec
    return out


def nest_unit_id(n):
    """nest 編號 → `UNIT_ID`。**形式的唯一出處是 `board_capacity.installed_units`**，
    這裡只是同一條規則的反向構造（`"nest:%d"`）；兩邊由 selftest 互釘。"""
    return "nest:%d" % int(n)


def max_prod_end_by_unit(rows, prev=None, register=None):
    """形① MES 列 → `{eid: {unit_id: end_utc}}`（**只收生產級 designation**）。

    `rows`＝`[(eid, end_utc, start_utc, designation, nest_int), …]`（時區已在 I/O 邊界轉好）。
    · 只收 `PRODUCTION_DESIGNATIONS`（與 `max_prod_end_by_eid` **同一把尺**，不另立門檻）。
    · 只收名冊上的 EID，且該 nest 編號**必須在該台宣告的 nests 裡**——
      `EID-26718` 的 `nest 0` 是結構性噪音（名冊註解逐字），收進來就是憑空多一個座位。
    · 值只留 `end`（landing 只問「多久以前」）；要 start／result 再加，不預先發明欄位。
    · `prev` 可帶入續算（逐 class 累積；`max` 單調 ⇒ 合併恆安全）。
    """
    reg = _wcap.NEST_EAV_UNITS if register is None else register
    out = {k: dict(v) for k, v in (prev or {}).items()}
    for eid, end, _start, desig, nest in rows:
        if not eid or end is None or desig not in PRODUCTION_DESIGNATIONS:
            continue
        spec = reg.get(eid)
        if spec is None:
            continue
        try:
            n = int(nest)
        except (TypeError, ValueError):
            continue
        if n not in tuple(spec["nests"]):
            continue
        unit = nest_unit_id(n)
        cur = out.setdefault(eid, {})
        if cur.get(unit) is None or end > cur[unit]:
            cur[unit] = end
    return out


def max_prod_end_by_station(rows, prev=None):
    """#104(b)：站別 lot 段列 → `{站: 末筆生產級段結束時刻}`（純函式）。

    `rows`＝`[(cls, end_utc, start_utc, designation), …]`（時區已在 I/O 邊界轉好）。
    · **只收 `PRODUCTION_DESIGNATIONS`**——與 `max_prod_end_by_eid`／`max_prod_end_by_unit`
      **同一把尺**（一個裁決不得有三份實作）。非生產級段不是生產（`segASSYThermalCycling`
      30 天 44 段裡只有 7 段是生產級，那一站多數時候本來就該點不亮）。
    · 值只留 `end`：landing 只問「多久以前」（同 `max_prod_end_by_unit` 的取捨）。
    · `prev` 可帶入續算（逐站累積／增量輪與舊值取 max；`max` 單調 ⇒ 合併恆安全）。
    ⚠ **`SPEC-LANDING.md` §18.26.3 那句「取 `MAX(stpEndTime)`」就落在這一行**——
      SQL 端一個聚合都不准有（B4 斷崖），最大值一律在 Python 算。
    """
    out = dict(prev or {})
    for cls, end, _start, desig in rows:
        if not cls or end is None or desig not in PRODUCTION_DESIGNATIONS:
            continue
        if out.get(cls) is None or end > out[cls]:
            out[cls] = end
    return out


def merge_station_lot(prev, observed, now):
    """站別末筆的合併 → 狀態檔的 `{站: {"last_prod_end": dt|None, "asof": dt}}`（純函式）。

    · `observed`＝`{站: end|None}`，**鍵在場＝這一輪真的掃過這一站**（值 None＝掃過、
      窗內零生產級段）——同 pin ① 的精神：缺席不表達任何語意。
    · `last_prod_end` 逐站取 `max(舊, 新)`（hi-water、單調）；`asof` 一律換成**本輪的 now**
      ——它答的是「我上次確認這一站是什麼時候」，不是段的時刻。
    · **沒掃到的站整筆原樣保留**（含它舊的 `asof`）＝查詢失敗隔離的落點：讀端看得出
      那一站的資訊有多舊，不會把「我沒問」讀成「它沒動」。
    ⚠ **刻意不做時間修剪**：這張表最多五個鍵（`STATION_LOT_PROXY`），長不胖；而讀端
      （landing）自己有一把 2 小時的近期閘 ⇒ 舊值留著不會讓任何方塊亮起來。
    """
    out = {}
    for cls, v in (prev or {}).items():
        if isinstance(v, dict):
            out[str(cls)] = dict(v)
    for cls, end in (observed or {}).items():
        cur = (out.get(str(cls)) or {}).get("last_prod_end")
        if end is not None and (cur is None or end > cur):
            cur = end
        out[str(cls)] = {"last_prod_end": cur, "asof": now}
    return out


def _station_lot_from_state(state):
    """狀態檔的 `station_lot` → `{站: {"last_prod_end": dt|None, "asof": dt}}`。

    壞值逐筆跳過、不炸（同 `_sub_units_from_state` 的紀律）；`asof` 解析不了就**整筆丟**
    ——沒有時點的「末筆」講不出年齡，留著只會讓讀端拿一個不知道多舊的值去點亮方塊。
    """
    out = {}
    for cls, v in ((state or {}).get("station_lot") or {}).items():
        if not isinstance(v, dict):
            continue
        asof = parse_utc_z(v.get("asof"))
        if asof is None:
            continue
        out[str(cls)] = {"last_prod_end": parse_utc_z(v.get("last_prod_end")), "asof": asof}
    return out


def _station_lot_to_state(sl):
    """`{站: {...}}` → 狀態檔形（`Z` 字串／`null`；鍵排序＝逐輪落盤位元穩定）。"""
    return {cls: {"last_prod_end": (None if (sl[cls] or {}).get("last_prod_end") is None
                                    else _z(sl[cls]["last_prod_end"])),
                  "asof": _z(sl[cls]["asof"])}
            for cls in sorted(sl) if isinstance(sl.get(cls), dict) and sl[cls].get("asof")}


def cotag_sub_units(prod_by_eid, register=None):
    """形② `{母機: {子 EID: end_utc}}`——**從既有的 production-grade map 投影，零新查詢**。

    子 EID 本來就是 `segment_equipment_v` 上的設備 ID，全站掃描早就把它們算進
    `max_prod_end_by_eid` 了（live v4 狀態檔實測四顆子 EID 都在）⇒ 這裡只是換一個
    索引方式，**不是第二次量測**。查不到的子 EID 就不出現（缺席＝不知道，不是 0）。
    """
    reg = _wcap.COTAG_CHILD_UNITS if register is None else register
    out = {}
    for parent, kids in reg.items():
        got = {}
        for kid in kids:
            rec = (prod_by_eid or {}).get(kid)
            if rec is not None and rec[0] is not None:
                got[kid] = rec[0]
        if got:
            out[parent] = got
    return out


def merge_sub_units(prev, observed):
    """子工位末筆時刻的合併：逐 `(eid, unit)` 取 `max`（單調 ⇒ 合併恆安全）。

    ⚠ 與 `eids` 的「整筆取代（配對原子性）」**刻意不同**：這裡的值是單一時刻、沒有需要
      配對的欄位 ⇒ 逐鍵取 max 就是正確的，而且增量輪才不會把上一輪的座位打回不知道。
    """
    out = {k: dict(v) for k, v in (prev or {}).items()}
    for eid, units in (observed or {}).items():
        cur = out.setdefault(eid, {})
        for unit, end in (units or {}).items():
            if end is None:
                continue
            if cur.get(unit) is None or end > cur[unit]:
                cur[unit] = end
    return {k: v for k, v in out.items() if v}


def _sub_units_from_state(state):
    """狀態檔的 `sub_units` → `{eid: {unit: naive UTC datetime}}`（壞值逐筆跳過、不炸）。"""
    out = {}
    for eid, units in ((state or {}).get("sub_units") or {}).items():
        if not isinstance(units, dict):
            continue
        got = {}
        for unit, z in units.items():
            dt = parse_utc_z(z)
            if dt is not None:
                got[str(unit)] = dt
        if got:
            out[str(eid)] = got
    return out


def _sub_units_to_state(sub):
    """`{eid: {unit: datetime}}` → 狀態檔形（`Z` 字串；鍵全排序＝逐輪落盤位元穩定）。"""
    return {eid: {u: _z(sub[eid][u]) for u in sorted(sub[eid])}
            for eid in sorted(sub) if sub[eid]}


def sub_units_feature_enabled(env=None):
    """Whether the explicitly opt-in NOTE-71 item field is enabled.

    Scheduled tasks do not inherit an interactive shell, so this deliberately
    reads an environment mapping at call time.  Only conventional explicit true
    values enable the field; absent, misspelled, and empty values are safe-off.
    """
    env = os.environ if env is None else env
    return str(env.get(SUB_UNITS_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def _sub_units_rejection_streak(state):
    value = (state or {}).get("sub_units_rejection_streak")
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def sub_units_auto_disabled(state):
    """True once two consecutive schema/validation rejections have been recorded."""
    return bool((state or {}).get("sub_units_auto_disabled"))


def note_sub_units_rejected(state, now):
    """Additive local metadata after cmms rejects the optional item field.

    The first rejection is retried immediately without the field.  A second
    consecutive rejection trips a durable safe-off latch: subsequent scheduled
    rounds preserve the core payload and make no more optional-field attempts
    until an operator restarts the feature deliberately (one successful
    flag-off round clears the latch).  No state schema bump is needed because
    these are additive diagnostic keys.
    """
    out = dict(state or {})
    streak = _sub_units_rejection_streak(out) + 1
    out["sub_units_rejected_at"] = _z(now)
    out["sub_units_rejection_streak"] = streak
    if streak >= 2:
        out["sub_units_auto_disabled"] = True
    return out


def clear_sub_units_rejection_streak(state):
    """A successful enabled-field POST breaks the consecutive-rejection streak."""
    out = dict(state or {})
    if "sub_units_rejection_streak" in out or "sub_units_auto_disabled" in out:
        out["sub_units_rejection_streak"] = 0
        out["sub_units_auto_disabled"] = False
    return out


def _rec_from_state(v):
    """狀態檔一筆 → `(end, start, designation, result)`；壞值 → None（容錯不炸，同 6.13 紀律）。

    **配對原子性的守門**：end 或 start 解析不了、或 `start > end`（時間倒轉＝壞檔）
    ⇒ **整筆丟棄**，絕不拿 end 去發明一個 start。designation／result 非字串 ⇒ 壓成 None
    （非承重欄）。
    """
    if not isinstance(v, dict):
        return None
    end = parse_utc_z(v.get("end"))
    start = parse_utc_z(v.get("start"))
    if end is None or start is None or start > end:
        return None
    desig = v.get("designation")
    result = v.get("result")
    return (end, start,
            desig if isinstance(desig, str) and desig else None,
            result if isinstance(result, str) and result else None)


def _rec_to_state(rec):
    """`(end, start, designation, result)` → ordinary v4 state fields."""
    end, start, desig, result = rec
    return {"end": _z(end), "start": _z(start), "designation": desig, "result": result}


def _prod_rec_from_state(v):
    """v4 production-grade fields → paired segment record, or ``None``.

    All four fields must describe one segment.  The all-null form is the intentional
    no-production/degraded form; a half record is rejected rather than stitched from
    unrelated fields.
    """
    if not isinstance(v, dict):
        return None
    raw = (v.get("prod_end"), v.get("prod_start"),
           v.get("prod_designation"), v.get("prod_result"))
    if raw == (None, None, None, None):
        return None
    end = parse_utc_z(raw[0])
    start = parse_utc_z(raw[1])
    if end is None or start is None or start > end:
        return None
    desig, result = raw[2], raw[3]
    if desig not in PRODUCTION_DESIGNATIONS:
        return None
    return (end, start, desig, result if isinstance(result, str) and result else None)


def _prod_fields(rec):
    """Production-grade record → fixed v4 field set (all-null when unavailable)."""
    if rec is None:
        return {"prod_end": None, "prod_start": None,
                "prod_designation": None, "prod_result": None}
    end, start, desig, result = rec
    return {"prod_end": _z(end), "prod_start": _z(start),
            "prod_designation": desig, "prod_result": result}


# ★★ v4 是**可就地升級**的（2026-09-08 Fable 審後裁；`SPEC-LANDING.md` §18.23.5）。
#   ⚠ 這與 v1→v2／v3→v4 的「整份拒收」**刻意不同，理由要記死**：
#     那兩次是**換口徑**（`eids` 的欄位語意變了 ⇒ 舊值當新值用會靜靜講錯話 ⇒ 只能重建）；
#     v4→v5 只是在信封層**加一個鍵**，`eids` 的 8 個欄一個位元都沒動。
#   ⇒ 拒收 v4 買不到任何正確性，卻要付一個**對外**的代價：`core_lastprod_push` 是主線目錄下
#     每 10 分鐘跑的排程任務，cherry-pick 進主線的下一輪就會拒收 → `full:no_state` →
#     送出 `full_window_rebuild=true` → **cmms 那側刪資料重建**。那是狀態改動，
#     沒有經 Chris 同意、也沒有先 relay ⇒ **不得發生**。
#   ⇒ 升級形＝`schema` 字串改 v5、補 `sub_units = {}`、**其餘欄逐字保留** ⇒ 本輪照 incremental 走，
#     `sub_units` 由本輪查詢補上後寫回 v5。座位在首輪之前是空的＝空心格＝誠實。
#   v3 以下仍整份拒收（維持舊行為＝那幾次真的是換口徑）。
#   ★ 2026-09-08 v5→v6（#104(b)）**同一條理由再套一次**：加的是信封層一個鍵
#     `station_lot`，`eids`／`sub_units` 的語意一個位元沒動 ⇒ v5 檔就地升級。
#     ⇒ 可升級集合現在有**兩版**（v4 也仍在：v4 檔升上來時 `sub_units` 與
#     `station_lot` 兩個加性鍵一起補空，本輪查詢自然把它們填上）。
STATE_SCHEMA_UPGRADABLE = ("board.lastprod.state.v4", "board.lastprod.state.v5")


def upgrade_state(doc):
    """狀態檔 dict → **本版 schema 的 dict**；不認得／形不對 ⇒ `None`。純函式（不讀檔）。

    · 已是 v5 ⇒ 原樣回（`sub_units` 不是 dict 的壞檔壓成 `{}`，不因一個加性欄丟掉整份覆蓋）。
    · v4 ⇒ 就地升級（見上方註解）。**不就地改傳入的 dict**。
    · 其他（v3 以下、缺 `eids`、不是 dict）⇒ `None` ⇒ 呼叫端走 `full:no_state`。
    """
    if not isinstance(doc, dict) or not isinstance(doc.get("eids"), dict):
        return None
    schema = doc.get("schema")
    if schema == STATE_SCHEMA:
        # 兩個加性鍵各自容錯（壞成非 dict ⇒ 壓空），**絕不因一個加性欄丟掉整份覆蓋**。
        out = doc
        if not isinstance(out.get("sub_units"), dict):
            out = dict(out, sub_units={})
        if not isinstance(out.get("station_lot"), dict):
            out = dict(out, station_lot={})
        return out
    if schema in STATE_SCHEMA_UPGRADABLE:
        out = dict(doc)
        out["schema"] = STATE_SCHEMA
        # v4 檔兩個都要補；v5 檔 `sub_units` 照舊保留、只補 `station_lot`。
        if not isinstance(out.get("sub_units"), dict):
            out["sub_units"] = {}
        out["station_lot"] = (out.get("station_lot")
                              if isinstance(out.get("station_lot"), dict) else {})
        return out
    return None


def prune_sub_units(sub_units, roster=None):
    """寫端載入用：把 `sub_units` 裡**不在名冊**的 eid key 丟掉。
    回 `(pruned_dict, dropped_eids_tuple)`；純函式（不讀檔、不就地改傳入物）。

    ★ 為什麼要這一支（`SPEC-LANDING.md` §18.30.4-3／**C-LD46**）：
      AOI 三台 2026-09-08 深夜退出子工位名冊，而**磁碟上的狀態檔還寫著它們的座**。
      不丟 ⇒ 這三台的 `nest:1..5` 會被一輪一輪搬下去、永遠不會消失（`merge_sub_units`
      只取較新、不會删 key）——就算今天沒有任何消費者讀它，那也是一份**名冊已經否認的
      事實**留在檔裡，下一個人讀到會以為那三台有座。
    ★ **schema 版號不跳**：`sub_units` 是加性欄、語意一個位元沒動，只是少了幾個 key
      （同 §18.30.4-3）；`build_payload` 從來不碰 `sub_units` ⇒ **wire payload 一個位元不變**。
    ★ `roster=None` ⇒ 現場問 `board_capacity.machines_with_units()`（**名冊唯一出處**，
      本檔永不抄第二份）；selftest 可以傳合成名冊進來。
    ★ 壞值容錯：不是 dict（含 None）⇒ 回 `({}, ())`；**不炋**（同 `_sub_units_from_state` 的紀律）。
    """
    if not isinstance(sub_units, dict):
        return {}, ()
    keep = set(_wcap.machines_with_units() if roster is None else roster)
    dropped = tuple(sorted(e for e in sub_units if e not in keep))
    if not dropped:
        return sub_units, ()
    return {e: v for e, v in sub_units.items() if e in keep}, dropped


def state_upgraded_from(doc):
    """本輪讀到的是哪一版（給 log／exit 診斷用；已是本版 ⇒ `None`）。純函式。"""
    schema = (doc or {}).get("schema") if isinstance(doc, dict) else None
    return schema if schema in STATE_SCHEMA_UPGRADABLE else None


def load_state(path):
    """讀狀態檔 → **升級到本版 schema 的** dict（壞掉／不存在／太舊 ⇒ None＝下輪整窗掃描，不炸）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return upgrade_state(doc)


def plan_window(state, now, lookback_days, refresh_days, force_full=False):
    """決定這一輪要掃幾天，並回覆理由（純函式＝離線可測）。

    回 `(days, reason, incremental)`。**任何一個不確定都退回整窗**——
    寧可多掃一次，不要靜默少掃（少掃的後果是把「我沒看」講成「它沒生產」）。
    """
    if force_full:
        return (lookback_days, "full:forced", False)
    if not state:
        return (lookback_days, "full:no_state", False)
    if int(state.get("lookback_days") or 0) != int(lookback_days):
        return (lookback_days, "full:lookback_changed", False)
    upd = parse_utc_z(state.get("updated_at"))
    cov = parse_utc_z(state.get("covered_from"))
    if upd is None or cov is None:
        return (lookback_days, "full:bad_state_time", False)
    if cov > now - timedelta(days=lookback_days):
        # 既有覆蓋不足以支撐「近 lookback 天無訊號」這句話
        return (lookback_days, "full:coverage_short", False)
    gap_d = (now - upd).total_seconds() / 86400.0
    if gap_d < 0:
        return (lookback_days, "full:clock_went_backwards", False)
    need = max(float(refresh_days), gap_d + 1.0)      # +1 天邊界餘裕
    if need >= lookback_days:
        return (lookback_days, "full:gap_too_long", False)
    return (int(math.ceil(need)), f"incremental:{gap_d:.2f}d_gap", True)


def merge_state(state, observed, now, window_days, lookback_days, incremental,
                prod_observed=None, prod_designation_available=True,
                sub_units_observed=None, station_lot_observed=None,
                station_lot_err=None):
    """把本輪觀測合併進狀態 → 新狀態 dict（純函式）。

    · 值：`max(舊, 新)`（單調）。
    · `covered_from`：整窗掃描 ⇒ `now - window`；增量且與舊覆蓋相接 ⇒ `min(舊, now - window)`。
    · 修剪：舊於 `2 × lookback` 的條目丟掉（檔案不會長胖；仍在 1×~2× 之間者留著，
      這樣它們才送得出「近 N 天無訊號」的 `null` 而不是靜默消失）。
    """
    win_start = now - timedelta(days=window_days)
    prev_vals, prev_prod = {}, {}
    if incremental and state:
        for k, v in (state.get("eids") or {}).items():
            rec = _rec_from_state(v)
            if rec is not None:
                prev_vals[k] = rec
            prod = _prod_rec_from_state(v)
            if prod is not None:
                prev_prod[k] = prod
    vals = dict(prev_vals)
    for eid, rec in observed.items():
        cur = vals.get(eid)
        if cur is None or _seg_key(rec) > _seg_key(cur):
            vals[eid] = rec          # ★ 整筆取代（配對原子性）——絕不逐欄取 max
    prods = dict(prev_prod)
    if prod_designation_available:
        for eid, rec in (prod_observed or {}).items():
            cur = prods.get(eid)
            if cur is None or _seg_key(rec) > _seg_key(cur):
                prods[eid] = rec      # same paired-record discipline as ordinary last segment
    else:
        # A missing source column means no production-grade claim is supportable.  Do
        # not retain a stale prior claim through a degraded scan.
        prods = {}
    if incremental and state:
        old_cov = parse_utc_z(state.get("covered_from"))
        covered_from = min(old_cov, win_start) if old_cov else win_start
    else:
        covered_from = win_start
    floor = now - timedelta(days=2 * lookback_days)
    vals = {k: v for k, v in vals.items() if v[0] >= floor}     # 修剪看 end
    prods = {k: v for k, v in prods.items() if k in vals and v[0] >= floor}
    eids = {}
    for k, rec in sorted(vals.items()):
        item = _rec_to_state(rec)
        item.update(_prod_fields(prods.get(k)))
        eids[k] = item
    # ★ v5 `sub_units`（加性）：與 `prods` 同一條降級規則——沒有 designation 欄就沒有
    #   「生產級」可言 ⇒ 整張表清空（不留上一輪的舊主張），不猜。
    if prod_designation_available:
        sub = merge_sub_units(_sub_units_from_state(state) if (incremental and state) else None,
                              sub_units_observed)
        # 與 `vals` 同一條修剪：舊於 2×lookback 的座位丟掉（檔案不會長胖）。
        sub = {e: {u: t for u, t in units.items() if t >= floor}
               for e, units in sub.items()}
        sub = {e: units for e, units in sub.items() if units}
    else:
        sub = {}
    out = {
        "schema": STATE_SCHEMA,
        "updated_at": _z(now),
        "covered_from": _z(covered_from),
        "lookback_days": int(lookback_days),
        # v5：子工位末筆生產級段結束時刻（`{eid: {unit_id: Z}}`）。
        # ⚠ **缺席＝不知道**（同 pin ①的精神）：座位沒出現在這裡，讀端一律畫空心，
        #   絕不當成「這一座沒在跑」。cmms payload 不含此鍵。
        "sub_units": _sub_units_to_state(sub),
        # ★ v6 `station_lot`（#104(b)、`SPEC-LANDING.md` §18.26）：`{站: {last_prod_end, asof}}`。
        #   · **加性、與 cmms payload 無關**（`build_payload` 不碰它）。
        #   · 查詢失敗（`station_lot_observed` 空）⇒ **保留上一輪整張表**、只在 `station_lot_err`
        #     記一句——絕不因為新功能掛掉就讓既有推送或既有值消失。
        #   · 增量與整窗都保留舊值（hi-water）：這張表的讀端有自己的 2 小時近期閘，
        #     舊值留著不會點亮任何方塊，卻能讓「這一站上次生產是什麼時候」不被一次失敗抹掉。
        "station_lot": _station_lot_to_state(
            merge_station_lot(_station_lot_from_state(state), station_lot_observed, now)),
        "station_lot_err": (str(station_lot_err)[:200] if station_lot_err else None),
        # Explicitly records whether all-null prod_* means ``no qualifying segment``
        # or ``designation column unavailable``; readers can honestly fall back only
        # in the latter case.
        "prod_designation_available": bool(prod_designation_available),
        "eids": eids,
    }
    # NOTE-71 wire rollout diagnostics are additive local metadata.  Preserve
    # them across ordinary state rewrites so two consecutive cmms rejections can
    # fail-safe the optional field without ever holding back the core payload.
    for key in ("sub_units_rejected_at", "sub_units_rejection_streak",
                "sub_units_auto_disabled"):
        if isinstance(state, dict) and key in state:
            out[key] = state[key]
    return out


def rebuild_claim(incremental, partial):
    """本輪要不要宣告 `full_window_rebuild`（＝「這一輪是**覆蓋**，不是觀測」）。純函式。

    ★ 2026-08-16 cmms 第十輪要的旗標。**它是一個主張，不是一個狀態描述** ——
      對面收到 `true` 會**刪掉史表中大於本輪值的列**（他們的 PK＝`(asset_id, last_segment_end)`、
      `ON CONFLICT DO NOTHING`，而「T 之前末筆」取符合條件者中**最大**的
      ⇒ 我方推一個較小的值時，**大的舊值仍在、仍會贏得查詢**，被永久保留成偏晚的錨點
      ＝停機算少，與 WO-35966 同一個方向）。⇒ **宣告錯的代價是對面刪掉真的歷史。**

    ⇒ 只有**兩個條件同時成立**才宣告：
      · `incremental is False`＝本輪掃的是**整個 `lookback_days` 窗**（`plan_window` 的七個
        全掃觸發之一），不是 2 天的增量窗；增量輪只看得到窗的尾巴，**沒有資格說「MES 上沒有
        更晚的段了」**。
      · `partial is False`＝全站**全部掃完**。沒掃完就宣告，等於拿「我沒看」當「它不存在」
        ——與 C-LP6 擋 `null` 的是同一條紀律（**兩者都是涵蓋主張**）。

    ⚠ **誠實邊界（已寫進 relay，不是 bug）**：整窗重掃的窗過濾在 `stpStartTime`、值取
      `stpEndTime` ⇒ 若真有一段**起點滑出 30 天窗、終點還在窗內**，重掃看不到它而增量靠 max
      留著 ⇒ 宣告覆蓋會讓對面刪掉一個**真的**值。那需要一個跨越 30 天窗界的 production
      segment（生產段實際是分鐘～小時級）⇒ 結構上可能、現實上沒有。**別為它加啟發式**
      （加了就是拿一個沒發生過的情形去弱化唯一一條說得出口的保證）。
    """
    return (not incremental) and (not partial)


def partial_snapshot(state, observed):
    """partial 輪要送的事實集 ＝ `max(狀態檔既有值, 本輪掃到的值)`（純函式）。

    ★ 2026-08-16 加。**不是效能優化，是防降級**：partial ＝ 全站只掃了前 k 站，而**同一個
      EID 可以橫跨多個 class**（母機的 segment 同時掛在子工位的類別上）⇒ 只用本輪的
      `observed`，母機可能拿到「已掃到的那幾站裡的最大值」＝**比上一輪推過的還舊**。
    ★ 為什麼 2026-08-16 之後這件事變嚴重：cmms 端**改成逐筆留痕**（值有變才 append、
      保留 90 天），那顆按鈕改成查「開單時刻之前最接近的一筆」⇒ **推一個較舊的值不再會被
      下一輪自動蓋掉**，它會留在歷史裡，而且可能正好被選中 ⇒ 鑄出一個偏早的
      `failure_started_at`。latest-only 時代這種降級一輪就自我痊癒，現在不會。
    ★ **不違反 C-LP6**：C-LP6 擋的是 partial 輪送 `null`（那是一個**涵蓋主張**）；舊值是
      **已經觀測過的事實**，重送它不主張任何涵蓋。`build_payload(partial=True)` 照舊把落窗外的
      一律略過、一個 null 都不送。
    ★ 舊值不會無限留著：`merge_state` 的 `2 × lookback` 修剪與 `_val` 的窗外判定照舊生效。
    """
    out = {}
    for k, v in ((state or {}).get("eids") or {}).items():
        rec = _rec_from_state(v)
        if rec is not None:
            out[k] = rec
    for eid, rec in (observed or {}).items():
        cur = out.get(eid)
        if cur is None or _seg_key(rec) > _seg_key(cur):
            out[eid] = rec           # ★ 整筆取代（配對原子性）
    return out


# ⚠ **partial 輪沒有 production-grade 對應物、也不需要**（2026-09-07 驗證輪刪掉 codex 初稿裡
#   一支叫 `partial_prod_snapshot` 的死函式）：`partial_snapshot` 只餵**本輪 payload**，而
#   partial 輪 `new_state = None`＝**狀態檔根本不寫**（見 `run()`），所以 `prod_*` 在那條路上
#   沒有落點；payload 又不帶 `prod_*`（7.15b 負釘）。多一支沒人呼叫、沒人測的平行實作，
#   只是替未來的自己準備第二份會漂移的事實。


def _z(dt_utc_naive):
    """naive UTC datetime → ISO `Z`（狀態檔內部用；與 `to_utc_z` 分開＝那支吃台北牆鐘）。"""
    return dt_utc_naive.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def effective_lookback_days(state, now, lookback_days):
    """送出去的 `lookback_days` ＝**我方這次真的涵蓋到的天數**（不是設定值）。

    cmms 逐筆存這個值、顯示端一律讀 payload（「近 N 天內無生產紀錄」那句話就是它）
    ⇒ **寫死或高報等於說謊**。回 int；覆蓋不足 1 天 ⇒ 0（呼叫端據此拒送 null）。
    """
    cov = parse_utc_z((state or {}).get("covered_from"))
    if cov is None:
        return 0
    days = int((now - cov).total_seconds() // 86400)
    return max(0, min(int(lookback_days), days))


def _sub_units_for_wire(eid, sub_units, cutoff):
    """Return one nested-EAV item's complete NOTE-71 wire map and dropped units.

    The local state map is intentionally sparse (no observation is omitted).
    The wire map is intentionally complete: every physically installed nest is
    present and an absent, invalid, or stale observation becomes ``null``.  The
    roster remains the sole source of both membership and numeric order.
    """
    if eid not in _wcap.NEST_EAV_UNITS:
        return None, ()
    records = (sub_units or {}).get(eid)
    if not isinstance(records, dict):
        records = {}
    installed = tuple(_wcap.installed_units(eid))
    allowed = set(installed)
    dropped = tuple(sorted(str(unit) for unit in records if unit not in allowed))
    out = {}
    for unit in sorted(installed, key=lambda u: int(u.split(":", 1)[1])):
        value = records.get(unit)
        if not isinstance(value, datetime) or (cutoff is not None and value < cutoff):
            value = None
        wire_id = _wcap.seat_wire_id(eid, unit)
        # NEST_EAV_UNITS has only canonical nest IDs.  Keep this assertion close
        # to the serializer so a future roster shape cannot silently emit a bad
        # cmms asset ID.
        assert wire_id is not None
        out[wire_id] = None if value is None else _z(value)
    return out, dropped


def build_payload(now, lookback_days, snapshot, roster, known_eids,
                  scope=SCOPE_ROSTER_OBSERVED, partial=False, rebuild=False,
                  sub_units=None):
    """組出 `board.last_production.v1` 信封（純函式＝離線可測；**這裡是四條 pin 的落點**）。

    參數：
      · `snapshot`：`{eid: (end, start, designation, result)}`＝我方目前掌握的末段記錄
        （naive UTC datetime ×2 ＋ str|None ×2；2026-08-17 前是單一 end datetime、
        2026-08-22 #31 起帶 result）。
      · `roster`：名冊 EID（**保證出現在 items 或 unknown**）。
      · `known_eids`：MES 設備主檔 EID 集合；`None` ＝ 主檔查不到（降級 ⇒ 不分流 unknown）。
      · `partial=True`：本輪掃描不完整 ⇒ **一個 null 都不送**（null 是主張，沒掃完不能講），
        只送查到的事實；缺席由 pin ① 保證不表達語意。
      · `rebuild=True`：宣告 `full_window_rebuild`＝「**這一輪是覆蓋，不是觀測**」
        （值由 `rebuild_claim()` 決定，**呼叫端不得自己算**）。
      · `sub_units`：feature-gated local-state map ``{eid: {unit: UTC datetime}}``.
        Only the six nested-EAV machines gain the optional item key; ``None`` / an
        empty map, and every partial round, preserve the previous payload byte for
        byte.  Co-tag and parent-expansion units never have NOTE-71 ``-N`` IDs.

    回 `(payload, diag)`。items 依 `asset_id` 排序＝逐輪可 diff。
    """
    cutoff = now - timedelta(days=lookback_days) if lookback_days > 0 else None
    unknown, items = [], []
    seen = set()
    wire_sub_units = sub_units if isinstance(sub_units, dict) and sub_units and not partial else None
    dropped_sub_units = []

    def _val(eid):
        rec = snapshot.get(eid)
        if rec is None:
            return None
        end, start, desig, result = rec
        if cutoff is not None and end < cutoff:
            return None      # 有值但落在窗外 ⇒ 語意上就是「近 N 天內無訊號」
        # ★ 窗判定**只看 end**：跨窗界的段（start < cutoff、end 在窗內）start 原值照送
        #   ——那是事實；把它 null 掉或夾到 cutoff 都是改寫（pin ③）。
        return (_z(end), _z(start), desig, result)   # snapshot 進來就已經是 UTC（轉換在 I/O 邊界一次做完）

    def _item(eid, v):
        # ★ 四欄配對出現：v=None ⇒ 四欄全 null（「無訊號」對四個事實同時成立）；
        #   有值 ⇒ end/start 恆非 null（同一段的兩端）、designation／result 可各自 null
        #   （MES 原值本就可 NULL／降級形）。
        e, s, dsg, res = v if v is not None else (None, None, None, None)
        item = {"asset_id": eid, "last_segment_end": e,
                "last_segment_start": s, "last_segment_designation": dsg,
                "last_segment_result": res}
        if wire_sub_units is not None and eid in _wcap.NEST_EAV_UNITS:
            item["sub_units"], dropped = _sub_units_for_wire(eid, wire_sub_units, cutoff)
            dropped_sub_units.extend((eid, unit) for unit in dropped)
        return item

    for eid in sorted(roster):
        if known_eids is not None and eid not in known_eids:
            unknown.append(eid)          # pin ①：我方 MES 不認識這台
            seen.add(eid)
            continue
        v = _val(eid)
        if v is None and partial:
            seen.add(eid)
            continue                     # 沒掃完 ⇒ 不主張「無訊號」（pin ①：缺席無語意）
        items.append(_item(eid, v))
        seen.add(eid)

    if scope == SCOPE_ROSTER_OBSERVED:
        for eid in sorted(snapshot):
            if eid in seen:
                continue
            v = _val(eid)
            if v is None:
                continue                 # 名冊外的 EID 只送事實，不替它主張「無訊號」
            items.append(_item(eid, v))
            seen.add(eid)

    items.sort(key=lambda it: it["asset_id"])
    payload = {
        "schema_id": SCHEMA_ID,
        "observed_at": _z(now),
        "lookback_days": int(lookback_days),
        "full_window_rebuild": bool(rebuild),
        "items": items,
        "unknown": unknown,
    }
    diag = {
        "n_items": len(items),
        "n_null": sum(1 for it in items if it["last_segment_end"] is None),
        "n_unknown": len(unknown),
        "n_offroster": len(items) - sum(1 for it in items if it["asset_id"] in set(roster)),
        "partial": bool(partial),
        "rebuild": bool(rebuild),
        "scope": scope,
        "n_sub_units_items": sum(1 for it in items if "sub_units" in it),
        "sub_units_dropped": tuple(sorted(dropped_sub_units)),
    }
    return payload, diag


# =============================================================================
# 6. 純函式：token 解析（**值永不入 log**）
# =============================================================================
_TOKEN_CHARS = r"[A-Za-z0-9._\-+/=]{8,}"
_TOKEN_NOISE = ("cmms.md", ".secrets", ".env", "bearer", "token")


def extract_token(text, key=TOKEN_ENV):
    """從 markdown 便條裡撈出 `key` 的值（容忍各種包裝）。找不到 → None。

    容忍的形：`KEY=v` / `KEY: v` / `` `KEY` = `v` `` / `**KEY**：v` / `- KEY` 換行後單獨一行 v。
    ★ 刻意保守：只接受識別字字元的候選、排除看起來像檔名／提示詞的字串。撈錯總比
      撈到一個「像 token 的路徑」然後拿去打 API 好——後者的症狀是 401，很難查。
    ★ 本函式**只回值，不 log、不 print**；呼叫端也只 log 來源不 log 值。
    """
    if not text or key not in text:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if key not in line:
            continue
        tail = line.split(key, 1)[1]
        tail = tail.lstrip(" \t`*_:=\"'<>|：（(【[")   # 半形＋全形包裝一起剝
        m = re.match(_TOKEN_CHARS, tail)
        if m:
            cand = m.group(0)
            if _plausible_token(cand, key):
                return cand
        # 值可能在下一個非空行（`- **KEY**` 換行寫值的形）
        for nxt in lines[i + 1:i + 3]:
            bare = nxt.strip().strip("`*_ \t\"'")
            if not bare:
                continue
            if re.fullmatch(_TOKEN_CHARS, bare) and _plausible_token(bare, key):
                return bare
            break
    return None


def _plausible_token(cand, key):
    if not cand or cand == key or len(cand) < 8:
        return False
    low = cand.lower()
    return not any(n in low for n in _TOKEN_NOISE)


def resolve_ingest_token(secrets_path=None, env=None):
    """取 ingest token → `(token, source)`；沒有 → `(None, "missing")`。

    順序：① env `CMMS_Board_INGEST_TOKEN` ② secrets 檔（預設 `~\\.secrets\\cmms.md`）。
    ★ **絕不 fallback 到讀取票 `BOARD_CMMS_TOKEN`**——兩把票不互相代用
      （`/ingest/` 在 cmms 的 static bearer 豁免清單內；拿讀取票去寫只會拿到 401，
       而「用錯票」比「沒票」難查十倍）。
    """
    env = os.environ if env is None else env
    tok = (env.get(TOKEN_ENV) or "").strip()
    if tok:
        return (tok, "env")
    path = secrets_path or env.get(SECRETS_FILE_ENV) or DEFAULT_SECRETS_FILE
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return (None, "missing")
    tok = extract_token(text, TOKEN_ENV)
    return (tok, "secrets_file") if tok else (None, "missing")


def resolve_base_url(env=None):
    env = os.environ if env is None else env
    base = (env.get(BASE_ENV) or "").strip().rstrip("/")
    return base or None


# =============================================================================
# 7. 純函式：回應分類（錯誤碼處置表；每一種都要落 log ＋ 非零 exit）
# =============================================================================
def classify_response(status, body_text=None, reason=None):
    """→ `{ok, kind, exit, note}`。**這張表就是接手單裡那四個錯誤碼的處置。**

    · `reason` 非 None ＝ 連 HTTP 都沒完成（timeout / unreachable / bad_json）。
    · 400/401/503/422 各自有具名 kind＝日誌一眼看得出要找誰修：
        400 → 我方 payload 的 `schema_id` 不對（**cmms 不猜不容錯**，改碼才會好）
        401 → token 錯或過期（**檢查是不是拿了讀取票**）
        503 → **對方**沒設 token（我方零責任，等 cmms）
        422 → 信封形不合（欄位缺漏／型別錯／`lookback_days < 1`）——我方 bug
    """
    if reason is not None:
        return {"ok": False, "kind": reason, "exit": EXIT_HTTP_OTHER,
                "note": f"POST 未完成：{reason}"}
    if status == 200:
        return {"ok": True, "kind": "ok", "exit": EXIT_OK, "note": "accepted"}
    table = {
        400: ("http_400_schema_id", EXIT_HTTP_400,
              "cmms 拒收：schema_id 不合（不猜不容錯）＝我方常數與契約不同版，改碼"),
        401: ("http_401_unauthorized", EXIT_HTTP_401,
              "cmms 拒收：缺或錯 bearer＝檢查 CMMS_Board_INGEST_TOKEN（★ 別拿讀取票）"),
        422: ("http_422_envelope", EXIT_HTTP_422,
              "cmms 拒收：信封形不合（欄位/型別/lookback_days<1）＝我方 payload bug"),
        503: ("http_503_no_token", EXIT_HTTP_503,
              "cmms 端未設 CMMS_Board_INGEST_TOKEN＝對方的閘，我方零動作、等他們"),
    }
    if status in table:
        kind, code, note = table[status]
        return {"ok": False, "kind": kind, "exit": code, "note": note}
    return {"ok": False, "kind": f"http_{status}", "exit": EXIT_HTTP_OTHER,
            "note": f"未預期 HTTP {status}"}


def summarize_ingest_response(body_text):
    """200 回應 → **一行 log 摘要**（純函式＝離線可測、grep 得到；呼叫端不得內聯）。

    ★ **這支存在的理由＝回答 cmms 的一個 ask（「我方回應多一鍵會不會炸你們」）。**
      結構性的答案是**不會**，而且理由不在這裡：`classify_response()` **只看 HTTP 狀態碼、
      完全不讀 body** ⇒ 回應的形狀對我方的 `ok`／exit code／狀態檔寫入**零影響**。
      body 唯一的去處就是這一行 log，而這裡只用三個 `.get()` 取值
      ⇒ **多的鍵連讀都不會被讀到**，少的鍵印成 `None`。
    ⚠ **2026-08-17 補掉的一格**：原本這段內聯在 `run()` 且只 `except ValueError`
      ⇒ 「合法 JSON 但頂層不是 object」（`[...]`／`"ok"`／`123`）會讓 `.get` 丟
      **AttributeError**，被 `main()` 吃成 **exit 1，而且那一輪的狀態檔不會寫**
      （對面其實已經 200 收下了，只有我方少記一輪、下一輪窗變寬自癒）。
      **回應的形狀永遠不該影響我方的成敗判定** ⇒ 一律吞成一句話。
    """
    try:
        b = json.loads(body_text or "{}")
    except (ValueError, TypeError):
        return "200（回應不是 JSON，內容不解讀）"
    if not isinstance(b, dict):
        return "200（回應 JSON 頂層不是 object，內容不解讀）"
    return (f"200 accepted={b.get('accepted')} "
            f"unknown_skipped={b.get('unknown_skipped')} "
            f"unmatched={b.get('unmatched_assets')}")


# =============================================================================
# 8. 牆鐘預算（SPEC-WARM-CACHE §8：計時器會跟著機器一起睡，牆鐘不會）
# =============================================================================
class _Budget:
    """同時看 monotonic 與牆鐘，**取兩者較大**當已用時間。

    Modern Standby 期間 `time.monotonic()` 是否推進屬平台相依；牆鐘一定推進。取大者
    ⇒ 機器睡過一覺醒來，這支腳本會**立刻**發現預算已經燒光而收手，不會抱著資源繼續跑。
    """

    def __init__(self, seconds, mono=None, wall=None):
        # 兩個時鐘都可注入＝selftest 打得到**真正的** elapsed()（不是覆寫一支假的來自我證明）
        self._mono = mono or time.monotonic
        self._wall = wall or datetime.now
        self.limit = float(seconds)
        self.t0 = self._mono()
        self.w0 = self._wall()

    def elapsed(self):
        mono = self._mono() - self.t0
        wall = (self._wall() - self.w0).total_seconds()
        return max(mono, wall)

    def left(self):
        return self.limit - self.elapsed()

    def exhausted(self):
        return self.left() <= 0


# =============================================================================
# 9. I/O：MES 取數
# =============================================================================
def fetch_column_guard(cur):
    cur.execute(build_column_guard_sql())
    r = cur.fetchone()
    guard = {k: bool(r[i]) for i, k in enumerate(GUARD_KEYS)}
    assert_guard(guard)
    return guard


def fetch_known_eids(cur, guard):
    """設備主檔 EID 集合；主檔缺席 ⇒ None（降級：不分流 `unknown[]`）。"""
    if not guard.get("view_equipment") or not guard.get("eq_astEquipmentID"):
        return None
    cur.execute(build_known_eids_sql())
    return {r[0] for r in cur.fetchall() if r[0]}


def folded_rows(rows, i_desig, i_result):
    """MES 原始列 → `max_end_by_eid` 吃的五元組；**形④ 折疊與時區轉換都在這裡一次做完**。

    ★ 折疊（`mes_timeline.fold_eid`，§10.8-4；表在凍結核心、本檔零複製）：
      · **rename**：`EID-38041`（MES 計數主）→ `EID-38106`（推送名冊那一列）。
        不折 ⇒ 名冊列永遠沒有 last-production ⇒ cmms 端看起來像「這台從未生產」。
      · **drop**：`EID-38072` 的段是主 EID 的**子集** ⇒ 丟掉零損失；留著就會多出一台
        不存在的機器的生產紀錄（它本來就不在推送名冊上，這裡只是把語意講明白）。
      · 兩張表以外的每一顆 EID **逐位元恆等**（`fold_eid` 是恆等函式）。
    ★ 時區（pin ②）：`stpEndTime`／`stpStartTime` 走**同一支** `taipei_to_utc_dt`——
      出了這裡之後一切都是 UTC，結構上不可能混軸。
    ★ 純函式（除了時區轉換）、可離線餵假列斷言。"""
    for r in rows:
        eid = fold_eid(r[0])
        if eid is None:                      # drop 表：別名段不另計（絕不相加＝防雙算）
            continue
        yield (eid, taipei_to_utc_dt(r[1]), taipei_to_utc_dt(r[2]),
               (r[i_desig] if i_desig is not None else None),
               (r[i_result] if i_result is not None else None))


def fetch_last_ends(cur, days, budget, log, stations=None, with_designation=True,
                    with_result=True):
    """逐 class 掃窗 → `(ordinary, production_grade, n_scanned, n_rows)`。

    ``ordinary`` is the existing all-designation last segment map.  ``production_grade``
    is a second argmax over the exact same rows, restricted to
    ``PRODUCTION_DESIGNATIONS``; with no designation column it is empty by design.

    ★ **時區轉換就在這裡一次做完**（`taipei_to_utc_dt`）——MES 回台北牆鐘，出了這支函式
      之後的一切（狀態檔／比較／序列化）全部是 UTC ⇒ 結構上不可能混軸（pin ②）。
      `stpStartTime` 走**同一支**轉換（兩個時刻欄不得各自為政）。
    ★ `with_designation=False`／`with_result=False`＝降級形（guard 判該欄消失）：少一欄的
      SQL、該欄全 None——核心事實照推，不因輔助欄停擺。兩旗標獨立（四種組合都合法）。
    ★ 逐 class 之間檢查預算＝**不會在最後一站才發現來不及**。中斷的後果由呼叫端處理
      （partial ⇒ 一個 null 都不送）。
    """
    rows_by_cls, n_rows = fetch_last_rows(cur, days, budget, log, stations=stations,
                                          with_designation=with_designation,
                                          with_result=with_result)
    acc, prod_acc, _n = aggregate_last_rows(rows_by_cls, with_designation=with_designation,
                                            with_result=with_result)
    return acc, prod_acc, len(rows_by_cls), n_rows


def last_row_indices(with_designation=True, with_result=True):
    """`build_lastprod_sql` 欄序＝eid, end, start[, designation][, result] ⇒ `(i_desig, i_result)`。"""
    i_desig = 3 if with_designation else None
    i_result = (4 if with_designation else 3) if with_result else None
    return i_desig, i_result


def fetch_last_rows(cur, days, budget, log, stations=None, with_designation=True,
                    with_result=True):
    """**只做 I/O**：逐 class 掃窗 → `([(cls, rows), …] 依掃描順序, n_rows)`。

    聚合（折疊＋時區＋argmax）在純函式 `aggregate_last_rows`（Q-G 等價抽取；`compose_round`
    與 `fetch_last_ends` 共用同一支）。預算檢查與 log 與抽取前逐字相同。
    """
    sql, _ = build_lastprod_sql(with_designation=with_designation,
                                with_result=with_result)
    if stations is None:
        st = list(lastprod_scan_classes())
        log("[lastprod] scan classes = 39 + 4 extra (LASTPROD_EXTRA_CLASSES)")
    else:
        st = list(stations)
    out, n_rows = [], 0
    for cls in st:
        if budget.exhausted():
            log(f"[lastprod] budget exhausted after {len(out)}/{len(st)} stations "
                f"({budget.elapsed():.0f}s) -> partial")
            break
        t0 = time.monotonic()
        cur.execute(sql, cls, days)
        rows = cur.fetchall()
        out.append((cls, rows))
        n_rows += len(rows)
        log(f"[lastprod] scan {cls} rows={len(rows)} {time.monotonic() - t0:.1f}s")
    return out, n_rows


def aggregate_last_rows(rows_by_cls, with_designation=True, with_result=True):
    """純函式：`[(cls, MES 原始列), …]` → `(ordinary, production_grade, n_rows)`。

    逐 class 依序 `folded_rows` → `max_end_by_eid(prev=acc)`（有 designation 欄才算
    `max_prod_end_by_eid`）——與抽取前 `fetch_last_ends` 迴圈內的累積**逐步相同**。
    """
    i_desig, i_result = last_row_indices(with_designation, with_result)
    acc, prod_acc, n_rows = {}, {}, 0
    for _cls, rows in rows_by_cls:
        folded = tuple(folded_rows(rows, i_desig, i_result))
        acc = max_end_by_eid(folded, prev=acc)
        if with_designation:
            prod_acc = max_prod_end_by_eid(folded, prev=prod_acc)
        n_rows += len(rows)
    return acc, prod_acc, n_rows


def nest_rows(rows, with_designation=True):
    """形① MES 原始列 → `max_prod_end_by_unit` 吃的五元組（時區在這裡一次轉完）。

    欄序＝`build_nest_sql` 的 `SELECT`：eid, end, start[, designation], nest。
    ⚠ **不套 `fold_eid`**：形① 的機台都不在 rename／drop 表裡（selftest 有負斷言），
      而且折疊後的 EID 對不回 `NEST_EAV_UNITS` 的鍵＝把座位掛到別台機上。
    """
    i_nest = 4 if with_designation else 3
    for r in rows:
        yield (r[0], taipei_to_utc_dt(r[1]), taipei_to_utc_dt(r[2]),
               (r[3] if with_designation else None), r[i_nest])


def fetch_nest_units(cur, days, budget, log, pairs=None, with_designation=True):
    """形① 逐 `(class, var)` 點查 → `({eid: {unit: end_utc}}, n_scanned, n_rows)`。

    ★ 便宜：`nest_query_pairs()` 目前只有**少數幾條**（形① 機台收斂成 class/var 組；AOI 退座前更多，
      2026-09-08 深夜 C-LD46），窗＝推送
      自己的窗（增量輪 2 天）。逐組之間看預算＝不會在最後一組才發現來不及。
    ★ 沒有 designation 欄 ⇒ **整組不查**（回空）：那一形產不出「生產級」主張，
      而 `sub_units` 的語意就是生產級末段。不查比查了亂標誠實。
    """
    rows_by_pair, n_rows = fetch_nest_rows(cur, days, budget, log, pairs=pairs,
                                           with_designation=with_designation)
    return aggregate_nest_rows(rows_by_pair), len(rows_by_pair), n_rows


def fetch_nest_rows(cur, days, budget, log, pairs=None, with_designation=True):
    """**只做 I/O**：形① 逐 `(class, var)` 點查 → `([((cls, var), rows), …], n_rows)`。
    沒有 designation 欄 ⇒ 整組不查（回 `([], 0)`；語意同 `fetch_nest_units`）。"""
    if not with_designation:
        return [], 0
    sql, _ = build_nest_sql(with_designation=True)
    todo = list(nest_query_pairs() if pairs is None else pairs)
    out, n_rows = [], 0
    for cls, var in todo:
        if budget.exhausted():
            log(f"[lastprod] nest budget exhausted after {len(out)}/{len(todo)} pairs")
            break
        t0 = time.monotonic()
        cur.execute(sql, var, cls, days)
        rows = cur.fetchall()
        out.append(((cls, var), rows))
        n_rows += len(rows)
        log(f"[lastprod] nest scan {cls}/{var} rows={len(rows)} "
            f"{time.monotonic() - t0:.1f}s")
    return out, n_rows


def aggregate_nest_rows(rows_by_pair):
    """純函式：`[((cls, var), MES 原始列), …]` → `{eid: {unit_id: end_utc}}`（逐組依序累積）。"""
    acc = {}
    for _pair, rows in rows_by_pair:
        acc = max_prod_end_by_unit(tuple(nest_rows(rows, True)), prev=acc)
    return acc


def station_lot_rows(rows, cls):
    """站別 lot 段的 MES 原始列 → `max_prod_end_by_station` 吃的四元組（時區在這裡一次轉完）。

    欄序＝`build_station_lot_sql` 的 `SELECT`：end, start, designation。站別**由呼叫端帶**
    （SQL 是逐站點查，回傳列裡不重複那一欄＝少一欄傳輸、也少一個會漂的口徑）。
    ⚠ **不套 `fold_eid`**：這一條查的是**站**，列裡根本沒有 EID。
    """
    for r in rows:
        yield (cls, taipei_to_utc_dt(r[0]), taipei_to_utc_dt(r[1]), r[2])


def fetch_station_lot(cur, days, budget, log, stations=None):
    """#104(b) 逐站點查 → `({站: end_utc|None}, n_scanned, n_rows)`。

    ★ **鍵在場＝這一站真的掃過**；值 `None`＝掃過、窗內零生產級段（pin ① 的精神：
      缺席不表達任何語意，所以「掃過但沒有」必須用**在場的 None** 表達，不能用缺席）。
    ★ 便宜：五站、窗＝推送自己的窗（增量輪 2 天）。逐站之間看預算＝不會在最後一站才發現來不及。
    ⚠ 這支**不吞例外**：失敗要讓呼叫端看得到（`run()` 在那裡做失敗隔離＝保留上一輪值
      ＋記 `station_lot_err`＋**絕不阻斷 cmms POST**）。把 try 寫在這裡，
      呼叫端就分不出「掃完是空的」與「根本沒掃到」。
    """
    rows_by_cls, n_rows = fetch_station_lot_rows(cur, days, budget, log, stations=stations)
    return aggregate_station_lot_rows(rows_by_cls), len(rows_by_cls), n_rows


def station_lot_last(cls, rows):
    """純函式：一站的原始列 → 該站末筆生產級段結束時刻（None＝掃過、窗內零生產級段）。"""
    return max_prod_end_by_station(tuple(station_lot_rows(rows, cls))).get(cls)


def fetch_station_lot_rows(cur, days, budget, log, stations=None):
    """**只做 I/O**：#104(b) 逐站點查 → `([(cls, rows), …], n_rows)`。**不吞例外**（同
    `fetch_station_lot`）。log 的 `last=` 經同一支 `station_lot_last` 算，逐字同抽取前。"""
    sql, _ = build_station_lot_sql()
    todo = list(STATION_LOT_PROXY if stations is None else stations)
    out, n_rows = [], 0
    for cls in todo:
        if budget.exhausted():
            log(f"[lastprod] station_lot budget exhausted after {len(out)}/{len(todo)} stations")
            break
        t0 = time.monotonic()
        cur.execute(sql, cls, days)
        rows = cur.fetchall()
        out.append((cls, rows))
        n_rows += len(rows)
        log(f"[lastprod] station_lot scan {cls} rows={len(rows)} "
            f"last={station_lot_last(cls, rows)} {time.monotonic() - t0:.1f}s")
    return out, n_rows


def aggregate_station_lot_rows(rows_by_cls):
    """純函式：`[(cls, 原始列), …]` → `{站: end_utc|None}`（★ 掃過就有鍵，值可以是 None）。"""
    out = {}
    for cls, rows in rows_by_cls:
        out[cls] = station_lot_last(cls, rows)
    return out


# =============================================================================
# 10. I/O：HTTP POST（stdlib urllib；強制 timeout；token 只進 header、絕不入 log）
# =============================================================================
def _is_tls_verify_error(exc):
    """True iff `exc` (or its `.reason`) is a TLS certificate-verification failure.

    Log-marking only (`tls_verify=fail`): the returned reason / retry behaviour
    stay `unreachable`, and no TLS check is relaxed.
    """
    import ssl
    for e in (exc, getattr(exc, "reason", None)):
        if isinstance(e, ssl.SSLCertVerificationError):
            return True
        if isinstance(e, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(e):
            return True
    return False


def post_payload(base, token, payload, log, timeout=HTTP_TIMEOUT_SEC, retries=HTTP_RETRIES):
    """→ `(status, body_text, reason)`；`reason` 非 None ＝ 連 HTTP 都沒完成。"""
    url = base.rstrip("/") + INGEST_PATH
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": f"Bearer {token}",        # header 帶 token；URL 不帶、log 不印
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        t0 = time.monotonic()
        outcome = "?"
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                outcome = f"http_{resp.status}"
                return (resp.status, body, None)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            outcome = f"http_{e.code}"
            return (e.code, body, None)            # 明確回應＝不重試
        except urllib.error.URLError as e:
            last = "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "unreachable"
            outcome = last + (" tls_verify=fail" if _is_tls_verify_error(e) else "")
        except (socket.timeout, TimeoutError):
            last, outcome = "timeout", "timeout"
        except OSError as e:
            last = "unreachable"
            outcome = last + (" tls_verify=fail" if _is_tls_verify_error(e) else "")
        finally:
            log(f"[lastprod] POST {INGEST_PATH} attempt={attempt} "
                f"{time.monotonic() - t0:.2f}s -> {outcome}")
    return (None, None, last or "unreachable")


def payload_has_sub_units(payload):
    """Whether a built payload contains the opt-in item field (pure, tolerant)."""
    return any(isinstance(item, dict) and "sub_units" in item
               for item in (payload or {}).get("items", []))


def post_with_sub_units_fallback(base, token, payload, fallback_payload, log, sender=post_payload):
    """POST once, then immediately retry without optional sub-units on 400/422.

    Returns ``(status, body, reason, rejected_status)``.  The final triplet is
    always the response that controls state advancement; ``rejected_status`` is
    present only when the first POST was rejected for schema/validation and the
    fallback was actually sent.  This keeps the core snapshot independent from
    an additive consumer rollout.
    """
    status, body, reason = sender(base, token, payload, log)
    if (fallback_payload is not None and payload_has_sub_units(payload)
            and status in (400, 422)):
        log(f"[lastprod] sub_units rejected by cmms (http_{status}) -> resent without sub_units")
        final_status, final_body, final_reason = sender(base, token, fallback_payload, log)
        return final_status, final_body, final_reason, status
    return status, body, reason, None


# =============================================================================
# 11. 日誌（無視窗鐵則的配套：沒有 console 可看 ⇒ 一切落檔）
# =============================================================================
def make_logger(repo=REPO, echo=True):
    log_dir = os.path.join(repo, LOG_DIR_REL)
    path = os.path.join(log_dir, "lastprod-" + datetime.now().strftime("%Y%m%d") + ".log")

    def log(line):
        stamp = datetime.now().strftime("%H:%M:%S")
        msg = f"{stamp} {line}"
        if echo:
            try:
                print(msg, file=sys.stderr)
            except (UnicodeEncodeError, OSError):
                pass                               # 主控台編碼／管線斷掉不該讓推送失敗
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass                                   # 日誌寫不進去不該讓推送失敗
    return log


# =============================================================================
# 11.5 編排（純函式；Q-G 等價抽取——`run()` 與合成產線 `wsynth_tick` 共用同一份）
# =============================================================================
def _read_raw_state(path):
    """狀態檔 → 原始 dict；缺檔／壞 JSON → None（`upgrade_state` 再判版本）。**唯一的讀檔 I/O**。"""
    try:
        with open(path, "r", encoding="utf-8") as _f:
            return json.load(_f)
    except (OSError, ValueError):
        return None


def plan_round(raw_state, now, lookback, refresh_days, force_full=False, scope=DEFAULT_SCOPE):
    """純函式：狀態升級＋名冊修剪＋`plan_window` → `{state, days, why, incremental, logs}`。

    ★ 要在 I/O 之前跑（`days` 決定查詢窗）；`logs` 由呼叫端依序寫出（與抽取前逐字同）。
    """
    logs = []
    log = logs.append
    state = upgrade_state(raw_state)
    # ★ C-LD46（`SPEC-LANDING.md` §18.30.4-3）：磁碟上的舊檔可能還寫著**已退出名冊**的
    #   機台的座（AOI 三台）⇒ 載入就丟掉，否則 `merge_sub_units` 只取較新、那些 key
    #   永遠搬不走。**版號不跳**（加性欄少幾個 key）、**wire payload 不變**。
    if state is not None:
        _sub_keep, _sub_dropped = prune_sub_units(state.get("sub_units"))
        if _sub_dropped:
            state = dict(state, sub_units=_sub_keep)
            log("[lastprod] sub_units pruned: " + ", ".join(_sub_dropped)
                + "（不在 board_capacity 名冊；C-LD46）")
    _upg = state_upgraded_from(raw_state) if state is not None else None
    if _upg:
        # ★ 升級是**無聲會出事**的那一類事：不 log 的話，日後看不出「那一輪為什麼沒有整窗重建」。
        log(f"[lastprod] state {_upg} → {STATE_SCHEMA} in place "
            "(sub_units={} until this round fills it; NO full-window rebuild)")
    days, why, incremental = plan_window(state, now, lookback, refresh_days,
                                         force_full=force_full)
    log(f"[lastprod] window={days}d ({why}) lookback={lookback}d scope={scope}")
    return {"state": state, "days": days, "why": why, "incremental": incremental, "logs": logs}


def compose_round(plan, now, lookback, scope, rows_by_cls, nest_rows_by_pair=None,
                  station_lot_rows_by_cls=None, station_lot_err=None, known=None,
                  with_designation=True, with_result=True, total_stations=None,
                  roster=None, sub_units_on=None, elapsed_s=0.0):
    """純函式：原子段（MES 原始列形）＋本輪計畫 → 要推送的內容。

    輸入
      · `plan`：`plan_round()` 的回傳（state／days／incremental）。
      · `rows_by_cls`：`[(class, rows), …]`，**只列真的掃過的 class**、依掃描順序
        （列形＝`build_lastprod_sql` 欄序，台北牆鐘 naive）。`len` ＝掃到幾站。
      · `nest_rows_by_pair`：`[((class, var), rows), …]`；`None`＝形① I/O 失敗（只留 co-tag）。
      · `station_lot_rows_by_cls`：`[(class, rows), …]`；`None`＝I/O 失敗，`station_lot_err` 帶原因。
      · `known`：主檔 EID 集合（None＝降級、不分流 `unknown[]`）。
      · `total_stations`／`roster`／`sub_units_on`：None ⇒ 取模組預設
        （`len(EQP_STATIONS)`／`roster_eids()`／`sub_units_feature_enabled()`）。
      · `elapsed_s`：只用於 log 的秒數。
    回傳 dict：`exit`（None＝照常；否則 run() 應回的 exit code）、`logs`（依序寫出）、
      `payload`、`fallback_payload`（有 sub_units 才有）、`new_state`（partial ⇒ None）、
      `partial`、`rebuild`、`diag`、`snapshot`、`eff_lookback`、`sub_units_on`、
      `observed`、`prod_observed`、`sub_observed`、`station_lot_observed`、`station_lot_err`、
      `scanned`、`n_rows`。
    ★ 本函式**零 I/O**（不讀檔、不連線、不寫檔、不看時鐘）；同輸入 ⇒ 同輸出。
      與抽取前 `run()` 的 ②③ 逐字同義——失敗隔離（形①／站別 lot）與 log 字面照舊。
    """
    logs = []
    log = logs.append
    state, days, incremental = plan["state"], plan["days"], plan["incremental"]
    rnd = {"exit": None, "logs": logs, "payload": None, "fallback_payload": None,
           "new_state": None, "partial": None, "rebuild": None, "diag": None,
           "snapshot": None, "eff_lookback": None, "sub_units_on": None,
           "observed": None, "prod_observed": None, "sub_observed": None,
           "station_lot_observed": None, "station_lot_err": station_lot_err,
           "scanned": 0, "n_rows": 0}
    rows_by_cls = list(rows_by_cls)
    try:
        observed, prod_observed, n_rows = aggregate_last_rows(
            rows_by_cls, with_designation=with_designation, with_result=with_result)
        sub_observed = cotag_sub_units(prod_observed) if with_designation else {}
    except Exception as e:                          # noqa: BLE001（同抽取前 MES 區塊的兜底）
        log(f"[lastprod] MES query failed: {type(e).__name__}: {e}")
        rnd["exit"] = EXIT_MES
        return rnd
    scanned = len(rows_by_cls)
    nest_scanned = nest_rows_n = 0
    if nest_rows_by_pair is not None:
        nest_rows_by_pair = list(nest_rows_by_pair) if with_designation else []
        try:
            nest_units = aggregate_nest_rows(nest_rows_by_pair)
            sub_observed = merge_sub_units(sub_observed, nest_units)
            nest_scanned = len(nest_rows_by_pair)
            nest_rows_n = sum(len(r) for _p, r in nest_rows_by_pair)
        except Exception as e:                      # noqa: BLE001
            log(f"[lastprod] WARN nest scan failed: {type(e).__name__}: {e}"
                "（sub_units 這一輪只留 co-tag 投影；核心事實不受影響）")
    sl_err = station_lot_err
    station_lot_obs = {}
    if station_lot_rows_by_cls is not None:
        station_lot_rows_by_cls = list(station_lot_rows_by_cls)
        try:
            station_lot_obs = aggregate_station_lot_rows(station_lot_rows_by_cls)
            log(f"[lastprod] station_lot stations={len(station_lot_rows_by_cls)}/"
                f"{len(STATION_LOT_PROXY)} "
                f"rows={sum(len(r) for _c, r in station_lot_rows_by_cls)} with_end="
                f"{sum(1 for v in station_lot_obs.values() if v is not None)}")
        except Exception as e:                      # noqa: BLE001
            sl_err = f"{type(e).__name__}: {e}"
            station_lot_obs = {}
            log(f"[lastprod] WARN station_lot scan failed: {sl_err}"
                "（保留上一輪的站別末筆；cmms 推送不受影響）")
    log(f"[lastprod] sub_units eids={len(sub_observed)} "
        f"units={sum(len(v) for v in sub_observed.values())} "
        f"nest_pairs={nest_scanned}/{len(nest_query_pairs())} nest_rows={nest_rows_n}")
    rnd.update(observed=observed, prod_observed=prod_observed, sub_observed=sub_observed,
               station_lot_observed=station_lot_obs, station_lot_err=sl_err,
               scanned=scanned, n_rows=n_rows)

    total_stations = len(EQP_STATIONS) if total_stations is None else total_stations
    partial = scanned < total_stations
    if scanned == 0:
        log("[lastprod] 零站掃到 ⇒ 本輪無事實可送")
        rnd["exit"] = EXIT_MES
        return rnd
    log(f"[lastprod] stations={scanned}/{total_stations} rows={n_rows} "
        f"eids_with_signal={len(observed)} {elapsed_s:.0f}s")

    # ── 狀態合併（partial ⇒ 不更新，保住覆蓋不變式）───────────────────────────────
    if partial:
        # 沒掃完 ⇒ 只送事實、零 null；`lookback_days` 誠實報**本輪真的查了幾天**
        # （不是設定值——那會宣稱一個我們沒查完的涵蓋）。
        # ★ 舊值當地板（`partial_snapshot`）＝**不得把已知的較新事實降級成本輪掃到的較舊值**
        #   （母機跨多 class、只掃到一半；對面留痕之後降級不再自我痊癒）。
        snapshot = partial_snapshot(state, observed)
        eff_lookback = max(1, min(lookback, days))
        new_state = None
    else:
        new_state = merge_state(state, observed, now, days, lookback, incremental,
                                prod_observed=prod_observed,
                                prod_designation_available=with_designation,
                                sub_units_observed=sub_observed,
                                station_lot_observed=station_lot_obs,
                                station_lot_err=sl_err)
        snapshot = {k: _rec_from_state(v) for k, v in new_state["eids"].items()}
        snapshot = {k: v for k, v in snapshot.items() if v is not None}
        eff_lookback = effective_lookback_days(new_state, now, lookback)
        if eff_lookback < 1:
            log("[lastprod] 覆蓋不足 1 天 ⇒ 當作 partial 處理（不送 null）")
            partial, new_state = True, None
            eff_lookback = lookback

    roster = roster_eids() if roster is None else roster
    # ★ 覆蓋宣告：規則在 `rebuild_claim()`（純函式＝測得到、grep 得到），這裡**不得**內聯條件。
    #   `incremental` 來自 `plan_window`、`partial` 來自「全站掃完了沒」——兩者都要為 False。
    rebuild = rebuild_claim(incremental, partial)
    if sub_units_on is None:
        sub_units_on = sub_units_feature_enabled()
    sub_units_suppressed = sub_units_on and sub_units_auto_disabled(state)
    if sub_units_suppressed:
        log("[lastprod] sub_units feature auto-disabled after consecutive cmms rejections; core payload only")
    # Feature off is deliberately passed as None (not an empty sentinel): the
    # default payload must remain byte-identical.  A partial scan has no
    # coverage claim, so its optional map is forbidden even when enabled.
    payload_sub_units = None
    if sub_units_on and not sub_units_suppressed and not partial:
        payload_sub_units = _sub_units_from_state(new_state if new_state is not None else state)
    payload, diag = build_payload(now, eff_lookback, snapshot, roster, known,
                                  scope=scope, partial=partial, rebuild=rebuild,
                                  sub_units=payload_sub_units)
    log(f"[lastprod] payload items={diag['n_items']} null={diag['n_null']} "
        f"unknown={diag['n_unknown']} offroster={diag['n_offroster']} "
        f"lookback_days={payload['lookback_days']} partial={partial} "
        f"full_window_rebuild={rebuild} sub_units_items={diag['n_sub_units_items']}")
    if diag["sub_units_dropped"]:
        omitted = ", ".join("%s/%s" % pair for pair in diag["sub_units_dropped"])
        log("[lastprod] sub_units state units outside installed roster omitted from wire: " + omitted)
    # 有 sub_units 才有退路 payload（cmms 拒收加性欄時退回核心形；見 post_with_sub_units_fallback）
    fallback_payload = None
    if payload_has_sub_units(payload):
        fallback_payload, _ = build_payload(now, eff_lookback, snapshot, roster, known,
                                            scope=scope, partial=partial, rebuild=rebuild,
                                            sub_units=None)
    rnd.update(payload=payload, fallback_payload=fallback_payload, new_state=new_state,
               partial=partial, rebuild=rebuild, diag=diag, snapshot=snapshot,
               eff_lookback=eff_lookback, sub_units_on=sub_units_on)
    return rnd


# =============================================================================
# 12. 主流程
# =============================================================================
def run(argv=None):
    ap = argparse.ArgumentParser(description="推送 board.last_production.v1 給 cmms")
    ap.add_argument("--dry-run", action="store_true", help="查 MES、印 payload、不送不寫狀態")
    ap.add_argument("--full", action="store_true",
                    help="強制整窗掃描（忽略增量狀態）。⚠ 任務已武裝時不要用它送真的："
                         "改成把狀態檔挪開 + schtasks /Run（見檔頭／SPEC §8.1）")
    ap.add_argument("--status", action="store_true", help="印狀態檔摘要（不連 MES 不連網）")
    ap.add_argument("--lookback-days", type=int,
                    default=int(os.environ.get("BOARD_LASTPROD_LOOKBACK_D",
                                               DEFAULT_LOOKBACK_D)))
    ap.add_argument("--refresh-days", type=int,
                    default=int(os.environ.get("BOARD_LASTPROD_REFRESH_D",
                                               DEFAULT_REFRESH_D)))
    ap.add_argument("--scope", choices=(SCOPE_ROSTER, SCOPE_ROSTER_OBSERVED),
                    default=DEFAULT_SCOPE)
    a = ap.parse_args(argv)

    log = make_logger()
    state_path = state_file_path()

    if a.status:
        st = load_state(state_path)
        if not st:
            print("no state file (next run scans the full window)")
            return EXIT_OK
        print(json.dumps({k: v for k, v in st.items() if k != "eids"},
                         ensure_ascii=False, indent=2))
        print(f"eids tracked: {len(st.get('eids') or {})}")
        return EXIT_OK

    budget = _Budget(TOTAL_BUDGET_SEC)
    lookback = max(1, int(a.lookback_days))

    # ── ① 設定（缺 token/base ⇒ 零網路嘗試、誠實 exit）─────────────────────────
    base = resolve_base_url()
    token, tok_src = resolve_ingest_token()
    if not a.dry_run and (not base or not token):
        why = []
        if not base:
            why.append(f"{BASE_ENV} 未設")
        if not token:
            why.append(f"{TOKEN_ENV} 未設且 secrets 檔取不到")
        log("[lastprod] not_configured: " + "、".join(why) + "（零網路嘗試）")
        return EXIT_NOT_CONFIGURED
    if token and os.environ.get(READ_TOKEN_ENV, "").strip() == token:
        # 兩把票不互相代用；相同值＝有人把讀取票塞進 ingest 變數，先擋下來
        log("[lastprod] not_configured: ingest token 與讀取票同值＝拿錯票，拒送")
        return EXIT_NOT_CONFIGURED
    if not a.dry_run:
        log(f"[lastprod] token source={tok_src} (值不入 log)")

    # ── ② MES 取數（I/O）→ 編排一律在純函式 `plan_round`／`compose_round`（Q-G）────────
    now = utc_now()
    _raw_state = _read_raw_state(state_path)
    plan = plan_round(_raw_state, now, lookback, max(1, int(a.refresh_days)),
                      force_full=a.full, scope=a.scope)
    for _line in plan["logs"]:
        log(_line)
    days = plan["days"]

    try:
        conn, cur = _connect(QUERY_TIMEOUT_SEC)
    except Exception as e:                          # noqa: BLE001（連不上 MES 就是連不上）
        log(f"[lastprod] MES connect failed: {type(e).__name__}: {e}")
        return EXIT_MES
    known, with_desig, with_result = None, False, False
    rows_by_cls, nest_rows_by_pair, station_lot_rows_by_cls, sl_err = [], None, None, None
    try:
        guard = fetch_column_guard(cur)
        known = fetch_known_eids(cur, guard)
        if known is None:
            log("[lastprod] WARN dbo.equipment_v 不可用 ⇒ unknown[] 判定 SKIPPED（fail-open）")
        with_desig = bool(guard.get("matDesignation"))
        if not with_desig:
            log("[lastprod] WARN matDesignation 欄不可用 ⇒ 降級少一欄 SQL"
                "（designation 全 null；核心事實照推）")
        with_result = bool(guard.get("stpTestResult"))
        if not with_result:
            log("[lastprod] WARN stpTestResult 欄不可用 ⇒ 降級少一欄 SQL"
                "（result 全 null；核心事實照推）")
        rows_by_cls, _n_rows = fetch_last_rows(
            cur, days, budget, log, with_designation=with_desig, with_result=with_result)
        # ★ v5 子工位（`SPEC-LANDING.md` §18.23）：形② 零新查詢（從 `prod_observed` 投影）、
        #   形① 點查。⚠ **失敗只讓 `sub_units` 空**、絕不讓整輪推送掛掉——這是加性欄位，
        #   把核心事實（cmms 要的那份 payload）押在它身上就是把加分變門檻。
        #   I/O 失敗 ⇒ `nest_rows_by_pair=None`（`compose_round` 只留 co-tag 投影）。
        try:
            nest_rows_by_pair, _nest_n = fetch_nest_rows(
                cur, days, budget, log, with_designation=with_desig)
        except Exception as e:                      # noqa: BLE001
            nest_rows_by_pair = None
            log(f"[lastprod] WARN nest scan failed: {type(e).__name__}: {e}"
                "（sub_units 這一輪只留 co-tag 投影；核心事實不受影響）")
        # ★ v6 站別 lot（`SPEC-LANDING.md` §18.26.3）：五站點查。⚠ **失敗隔離**——
        #   任一站查失敗 ⇒ 本輪 `station_lot_observed` 空 ⇒ `merge_state` 保留上一輪整張表、
        #   在 `station_lot_err` 記一句，**cmms 的 POST 一個位元不受影響**。
        #   （[[live-worktree-cherry-pick-window]]：進主線 10 分鐘內排程就會撿走這支，
        #    新功能掛掉不能拖垮已經在跑的推送。）
        try:
            station_lot_rows_by_cls, _sl_n = fetch_station_lot_rows(cur, days, budget, log)
        except Exception as e:                      # noqa: BLE001
            sl_err = f"{type(e).__name__}: {e}"
            station_lot_rows_by_cls = None
            log(f"[lastprod] WARN station_lot scan failed: {sl_err}"
                "（保留上一輪的站別末筆；cmms 推送不受影響）")
    except SchemaDivergence as e:
        log(f"[lastprod] SchemaDivergence: {e}（停下，不送全 null 的假快照）")
        return EXIT_MES
    except Exception as e:                          # noqa: BLE001
        log(f"[lastprod] MES query failed: {type(e).__name__}: {e}")
        return EXIT_MES
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # ── ③ 編排（純函式；聚合→狀態合併→payload）──────────────────────────────────
    rnd = compose_round(plan, now, lookback, a.scope, rows_by_cls,
                        nest_rows_by_pair=nest_rows_by_pair,
                        station_lot_rows_by_cls=station_lot_rows_by_cls,
                        station_lot_err=sl_err, known=known,
                        with_designation=with_desig, with_result=with_result,
                        elapsed_s=budget.elapsed())
    for _line in rnd["logs"]:
        log(_line)
    if rnd["exit"] is not None:
        return rnd["exit"]
    payload, partial, new_state = rnd["payload"], rnd["partial"], rnd["new_state"]
    sub_units_on = rnd["sub_units_on"]

    # ── ④ dry-run 出口（不送、不寫狀態）──────────────────────────────────────
    if a.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_PARTIAL if partial else EXIT_OK

    # ── ⑤ POST ──────────────────────────────────────────────────────────────
    fallback_payload = rnd["fallback_payload"]
    status, body, reason, rejected_status = post_with_sub_units_fallback(
        base, token, payload, fallback_payload, log)
    res = classify_response(status, body, reason)
    if res["ok"]:
        # 摘要規則在 `summarize_ingest_response()`（純函式＝測得到），**這裡不得內聯**：
        # 回應的形狀永遠不該影響下面那行狀態寫入與 exit code。
        log("[lastprod] " + summarize_ingest_response(body))
        if new_state is not None:
            if rejected_status is not None:
                new_state = note_sub_units_rejected(new_state, now)
                if sub_units_auto_disabled(new_state):
                    log("[lastprod] sub_units rejected in two consecutive rounds -> feature auto-disabled until restart")
            elif payload_has_sub_units(payload):
                new_state = clear_sub_units_rejection_streak(new_state)
            elif not sub_units_on and _sub_units_rejection_streak(new_state):
                # Turning the env flag off for one successful scheduled round
                # is the explicit operator action that restarts this optional
                # rollout.  It never withholds the core payload and avoids a
                # permanent latch merely because scheduled runs are short-lived.
                new_state = clear_sub_units_rejection_streak(new_state)
                log("[lastprod] sub_units safe-off latch cleared after explicit flag-off round")
            _write_state(state_path, new_state, log)
        return EXIT_PARTIAL if partial else EXIT_OK

    log(f"[lastprod] FAILED {res['kind']}: {res['note']}")
    if body:
        log("[lastprod] body: " + body[:400].replace("\n", " "))
    return res["exit"]


def _write_state(path, doc, log):
    """原子寫（先寫 .tmp 再 replace）——寫到一半斷電也不會留下半份狀態檔。"""
    try:
        _wat.atomic_write_json(path, doc)   # 唯一暫存名＋fsync＋os.replace（見 board_atomic）
    except OSError as e:
        log(f"[lastprod] WARN 狀態檔寫入失敗（下輪自動整窗掃描）: {e}")


# =============================================================================
# 13. selftest（純離線：零 MES、零網路、零檔案寫入）
# =============================================================================
def _selftest():
    n = [0]

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            print(f"FAIL [{n[0]}] {msg}")
            raise SystemExit(1)

    D = datetime
    now = D(2026, 8, 15, 6, 0, 0)                 # naive UTC

    # ── 0. 常數與契約識別 ────────────────────────────────────────────────────
    ck(SCHEMA_ID == "board.last_production.v1", "0.1 schema_id 字面值（cmms 400 就是比這個）")
    ck(INGEST_PATH == "/ingest/last-production", "0.2 端點路徑")
    ck(TAIPEI_OFFSET_H == 8, "0.3 台北固定 UTC+8")
    ck(DEFAULT_LOOKBACK_D == 30, "0.4 lookback 預設 30（唯一有實證的窗寬）")
    ck(TOKEN_ENV == "CMMS_Board_INGEST_TOKEN" and READ_TOKEN_ENV == "BOARD_CMMS_TOKEN",
       "0.5 兩把 token 的 env 名字不同")
    ck(STATE_SCHEMA == "board.lastprod.state.v6"
       and STATE_SCHEMA_UPGRADABLE == ("board.lastprod.state.v4", "board.lastprod.state.v5"),
       "0.6 ★ 狀態檔 schema v6，且 **v4／v5 檔都可就地升級**（v3 以下仍整份拒收 ⇒ "
       "full:no_state）。prod_*／sub_units／station_lot 都是本地 reader 欄，cmms payload 不變")
    ck(SUB_UNITS_ENV == "BOARD_LASTPROD_SUB_UNITS"
       and not sub_units_feature_enabled({})
       and sub_units_feature_enabled({SUB_UNITS_ENV: "1"}),
       "0.7 NOTE-71 item field is safe-off by default and only an explicit env flag enables it")

    # ── 1. pin ②：UTC ISO Z ─────────────────────────────────────────────────
    ck(to_utc_z(D(2026, 8, 11, 12, 12, 0)) == "2026-08-11T04:12:00Z", "1.1 台北→UTC −8h")
    ck(to_utc_z(D(2026, 8, 11, 12, 12, 0, 123456)) == "2026-08-11T04:12:00Z", "1.2 微秒截掉")
    ck(to_utc_z(None) is None, "1.3 None → None")
    ck(to_utc_z(D(2026, 1, 1, 3, 0, 0)) == "2025-12-31T19:00:00Z", "1.4 跨年跨日邊界")
    ck(to_utc_z(D(2026, 8, 11, 12, 0, 0)).endswith("Z"), "1.5 恆帶 Z")
    ck(parse_utc_z("2026-08-11T04:12:00Z") == D(2026, 8, 11, 4, 12, 0), "1.6 round-trip")
    ck(parse_utc_z("2026-08-11T04:12:00") is None, "1.7 無 Z ⇒ 拒讀（不猜時區）")
    ck(parse_utc_z("") is None and parse_utc_z(None) is None, "1.8 空值不炸")
    ck(parse_utc_z("2026-13-99T00:00:00Z") is None, "1.9 壞日期不炸")
    ck(_z(D(2026, 8, 15, 6, 0, 0)) == "2026-08-15T06:00:00Z", "1.10 狀態檔序列化同格式")
    ck(to_utc_z(D(2026, 8, 11, 12, 12, 0, 0)) == _z(D(2026, 8, 11, 4, 12, 0)),
       "1.11 兩支序列化器對同一時刻一致")
    ck(taipei_to_utc_dt(D(2026, 8, 11, 12, 12, 0)) == D(2026, 8, 11, 4, 12, 0),
       "1.12 台北 naive → UTC naive")
    ck(taipei_to_utc_dt(None) is None, "1.13 None 傳遞")
    ck(taipei_to_utc_dt(D(2026, 8, 11, 12, 12, 0, tzinfo=_fixed_tz(0)))
       == D(2026, 8, 11, 12, 12, 0), "1.14 已帶時區者正規化後再扣（不重複扣）")

    # ── 2. SQL 形狀與負斷言（perf lore ＋ §24 紅線）──────────────────────────
    all_sql = ALL_SQL()
    joined = "\n".join(all_sql)
    ck("MAX(" not in joined.upper().replace("MAX (", "MAX("),
       "2.1 ★ 全史 MAX(stpEndTime) 形狀零出現（B4 事故）")
    ck("stpPersonID" not in joined, "2.2 ★ §24：人員欄零出現")
    ck("DISTINCT" not in joined.upper(), "2.3 view 上零 DISTINCT（B4 斷崖）")
    ck("GROUP BY" not in joined.upper(), "2.4 view 上零 GROUP BY（同上）")
    ck(all(s.rstrip().endswith("OPTION (RECOMPILE)") or "OPTION (RECOMPILE)" in s
           for s in all_sql), "2.5 每條 SQL 帶 RECOMPILE")
    seg_sql = build_lastprod_sql()[0]
    ck("stpEndTime >= se.stpStartTime" in seg_sql,
       "2.6 ★ end-filter 用 >= 不用 >（零工期段八站整站隱形＝M4 事故）")
    ck("stpEndTime > se.stpStartTime" not in seg_sql, "2.7 負斷言：不得出現嚴格 >")
    ck("IN (" not in seg_sql, "2.8 無 EID IN-list（B2 大窗翻計劃事故）")
    ck(seg_sql.count("?") == 2 and "SegmentClassID = ?" in seg_sql,
       "2.9 逐 class 點查、兩個參數（cls, days）")
    ck("DATEADD(day, -?, GETDATE())" in seg_sql, "2.10 窗掃（絕不查全史）")
    ck("SELECT se.astEquipmentID, se.stpEndTime, se.stpStartTime, se.matDesignation,"
       " se.stpTestResult" in seg_sql,
       "2.11 回五欄（2026-08-17 加 start＋designation；2026-08-22 #31 加 stpTestResult；"
       "零聚合零 join）")
    deg_sql = build_lastprod_sql(with_designation=False, with_result=False)[0]
    ck("SELECT se.astEquipmentID, se.stpEndTime, se.stpStartTime\n" in deg_sql
       and "matDesignation" not in deg_sql and "stpTestResult" not in deg_sql,
       "2.11b 雙降級形＝三欄（兩個輔助欄都消失時核心事實照推）")
    deg_d = build_lastprod_sql(with_designation=False, with_result=True)[0]
    deg_r = build_lastprod_sql(with_designation=True, with_result=False)[0]
    ck("matDesignation" not in deg_d and "stpTestResult" in deg_d
       and "matDesignation" in deg_r and "stpTestResult" not in deg_r,
       "2.11b2 ★ 兩個降級旗標各自獨立（四種組合都是合法形、全部在 ALL_SQL 登記）")
    for _deg in (deg_sql, deg_d, deg_r):
        ck(_deg.split("FROM")[1] == seg_sql.split("FROM")[1],
           "2.11c ★ 各形只差 SELECT 欄——WHERE／OPTION 逐位相同（不得長出第二種過濾語意）")
    # ── 2.11d–2.11n 形① nest-EAV 查詢（v5；`SPEC-LANDING.md` §18.23）────────────
    nest_sql, nest_params = build_nest_sql()
    ck(nest_params == ("var", "cls", "days") and nest_sql.count("?") == 3,
       "2.11d nest SQL：三個參數、順序＝文本裡 `?` 的順序")
    ck("SELECT se.astEquipmentID, se.stpEndTime, se.stpStartTime, se.matDesignation,"
       " si.sgiInteger" in nest_sql,
       "2.11e nest SQL 回五欄（末欄＝nest 編號 sgiInteger）")
    ck("dbo.segment_integer_data_t si ON si.stpPK = se.stpPK" in nest_sql
       and "dbo.production_data_definition_t vdf ON vdf.vdfPK = si.vdfPK" in nest_sql
       and "vdf.vdfProductionDataID = ?" in nest_sql,
       "2.11f ★ EAV join 逐字＝`_NEST-PROBE-41.sql` PART A（欄名沒有一個是猜的；"
       "2026-08-31 `se.NestNo` Msg 207 事故）")
    ck("NestNo" not in nest_sql, "2.11g ★ 負斷言：`NestNo` 這個欄名永不復辟")
    ck("stpEndTime >= se.stpStartTime" in nest_sql
       and "stpEndTime > se.stpStartTime" not in nest_sql,
       "2.11h nest SQL：end-filter 用 >=（零工期段鐵則）")
    ck("IN (" not in nest_sql and "astEquipmentID IN" not in nest_sql,
       "2.11i nest SQL：零 EID IN-list（EID 篩選在 Python 做）")
    ck("DATEADD(day, -?, GETDATE())" in nest_sql and "OPTION (RECOMPILE)" in nest_sql,
       "2.11j nest SQL：窗掃 ＋ RECOMPILE")
    _nest_deg = build_nest_sql(with_designation=False)[0]
    ck("matDesignation" not in _nest_deg
       and _nest_deg.split("FROM")[1] == nest_sql.split("FROM")[1],
       "2.11k nest 降級形只差 SELECT 欄（WHERE／join 逐位相同）")
    ck(nest_query_pairs() == tuple(sorted({(v["main_cls"], v["var"])
                                           for v in _wcap.NEST_EAV_UNITS.values()}))
       and len(nest_query_pairs()) == 3,
       "2.11l ★ 形① 點查組合＝名冊導出的 class/var 組（不另抄清單；"
       "2026-09-08 深夜 C-LD46 AOI 退出名冊後組數減少）")
    # ★★ C-LD46 負斷言（§18.30.4-3）：AOI 那幾台退出名冊 ⇒ 它們的兩個 EAV 變數**不得
    #    再出現在任何一條送出去的 SQL 裡**（否則就是在問一些已經被否認的座）。
    ck(not any(v in _q for _q in ALL_SQL()
               for v in ("varBLSM_Nest", "varPostCure_Nest"))
       and not any(v in nest_sql for v in ("varBLSM_Nest", "varPostCure_Nest"))
       and not {v for _c, v in nest_query_pairs()} & {"varBLSM_Nest", "varPostCure_Nest"},
       "2.11l-b ★ C-LD46：`varBLSM_Nest`／`varPostCure_Nest` 已完全退出點查組合與 SQL 文本")
    ck(all(cls in EQP_STATIONS for cls, _v in nest_query_pairs()),
       "2.11m ★ 五個 class 全在固定站清單內（非 runtime discovery）")
    ck(not (set(_wcap.NEST_EAV_UNITS) & (set(EID_RENAME) | set(EID_ALIAS_DROP))),
       "2.11n ★ 負斷言：形① 機台不在 rename／drop 表 ⇒ nest 列刻意不套 fold_eid")
    ck("UPDATE" not in joined.upper() and "DELETE" not in joined.upper()
       and "INSERT" not in joined.upper() and " INTO " not in joined.upper(),
       "2.12 唯讀：零 DML、零 SELECT…INTO（08-02 #temp scope 事故）")
    ck("dbo.segment_equipment_v" in seg_sql, "2.13 走設備側 view")
    ck(len(EQP_STATIONS) == 39 and EQP_STATIONS[0] == "segCalibrationTest",
       "2.14 站清單 import 自凍結模組、未另抄（2026-09-02 起 39 站）")
    ck(EQP_STATIONS[38] == "segTopSealLube",
       "2.14b 第 39 站＝Top O-ring（#41 裁決 (c)；站數變了這裡就要一起變）")
    ck(len(LASTPROD_EXTRA_CLASSES) == 4
       and all(cls not in EQP_STATIONS for cls in LASTPROD_EXTRA_CLASSES),
       "2.14c ★ #122(a) 四個推送附加站皆不得進 EQP_STATIONS（(b) 落地時撤掉附加清單）")
    _scan_classes = lastprod_scan_classes()
    ck(len(_scan_classes) == 43
       and _scan_classes[:39] == tuple(EQP_STATIONS)
       and _scan_classes[39:] == LASTPROD_EXTRA_CLASSES
       and len(set(_scan_classes)) == len(_scan_classes),
       "2.14d ★ lastprod scan＝39 稼動站在前＋4 推送附加站在後，零重複")

    class _Cur:
        """只供 #122(a) 假掃描：形狀同端到端 `_FakeCur`，不連 MES。"""
        def __init__(self, rows_by_cls=None):
            self.rows_by_cls = rows_by_cls or {}
            self.calls, self._r = [], []

        def execute(self, sql, *params):
            self.calls.append((sql, params))
            self._r = self.rows_by_cls.get(params[0], [])

        def fetchall(self):
            return list(self._r)

    _extra_end = D(2026, 9, 10, 18, 0, 0)          # MES 台北牆鐘
    _base_end = D(2026, 9, 9, 18, 0, 0)
    _default_cur = _Cur({
        EQP_STATIONS[0]: [("EID-25749", _base_end, _base_end, "Production", "PASS")],
        LASTPROD_EXTRA_CLASSES[0]: [("EID-25749", _extra_end, _extra_end,
                                     "Production", "PASS")],
    })
    _scan_log = []
    _ordinary, _production, _n_scanned, _n_rows = fetch_last_ends(
        _default_cur, 30, _Budget(60), _scan_log.append)
    _explicit_classes = ("segWireBonding", LASTPROD_EXTRA_CLASSES[1])
    _explicit_cur = _Cur()
    _, _, _n_explicit, _ = fetch_last_ends(
        _explicit_cur, 30, _Budget(60), lambda _s: None, stations=_explicit_classes)
    # SQL 文字逐位元 sha256 釘的是**私有樹**的原文；公開樹的 SQL 經識別字置換、位元組必然不同 ⇒ 該一條標 skipped
    # （其餘掃描站數／參數／log 斷言照跑）；私有樹照釘。
    _pub_tree = _wp.is_public_tree()
    if not _pub_tree:
        import board_private_pins as _pp
    if _pub_tree:
        print("core_lastprod_push selftest: skipped: ALL_SQL sha256 pin (public tree, SQL text is sanitised)")
    ck(_n_scanned == 43 and _n_rows == 2
       and [params[0] for _sql, params in _default_cur.calls] == list(_scan_classes)
       and _scan_log[0] == "[lastprod] scan classes = 39 + 4 extra (LASTPROD_EXTRA_CLASSES)"
       and _n_explicit == len(_explicit_classes)
       and [params[0] for _sql, params in _explicit_cur.calls] == list(_explicit_classes)
       and (_pub_tree or hashlib.sha256("\x1e".join(ALL_SQL()).encode("utf-8")).hexdigest()
            == ALL_SQL_PRE_LASTPROD_EXTRA_SHA256),
       "2.14e ★ 預設掃 43、stations= 照給；ALL_SQL() 與 #122(a) 前逐位元相同")
    ck(taipei_to_utc_dt(_extra_end) > taipei_to_utc_dt(_base_end)
       and _ordinary["EID-25749"][0] == taipei_to_utc_dt(_extra_end)
       and _production["EID-25749"][0] == taipei_to_utc_dt(_extra_end)
       and _ordinary["EID-25749"] == _production["EID-25749"],
       "2.14f ★ 附加站列走既有 folded_rows→argmax：ordinary／production_grade 都以前移末段收下")

    # ── 2c. 形④ 別名折疊（§10.8-4）：推送名冊那一列拿得到自己的 last-production ────
    #   `EID-38106` 在 MES 沒有段（計數主是 `EID-38041`）⇒ 不折就會把「從未生產」推給 cmms。
    # WP-10d（R-P11）：折疊表的具名 EID 由凍結核心常數推導（私有樹＝38041→38106、38072 丟；公開樹＝合成名冊的同形一對）
    _rn_c, _rn_m = next(iter(EID_RENAME.items()))
    _dr = next(iter(EID_ALIAS_DROP))
    _D = datetime(2026, 9, 1, 10, 0, 0)
    _rows = [("EID-00425", _D, _D, "d1", "PASS"),
             (_rn_c, _D, _D, "d2", "PASS"),
             (_dr, _D + timedelta(hours=5), _D, "d3", "PASS")]
    _folded = list(folded_rows(_rows, 3, 4))
    ck([r[0] for r in _folded] == ["EID-00425", _rn_m],
       "2.15 ★ rename 38041→38106、drop 38072（別名段不另計＝防雙算）")
    ck(_folded[0][3:] == ("d1", "PASS") and _folded[1][3:] == ("d2", "PASS"),
       "2.15b 折疊不動輔助欄（配對原子性不受影響）")
    ck(len(list(folded_rows([("EID-00425", _D, _D, None, None)], None, None))) == 1
       and list(folded_rows([("EID-00425", _D, _D, None, None)], None, None))[0][3:] == (None, None),
       "2.15c 降級形（無 designation／result）照走同一支折疊")
    _acc = max_end_by_eid(folded_rows(_rows, 3, 4))
    ck(set(_acc) == {"EID-00425", _rn_m} and _dr not in _acc
       and _acc[_rn_m][0] == taipei_to_utc_dt(_D),
       "2.15d ★ 38072 那筆**較晚**的 end 不得冒名成為 38106 的末段（drop 是丟、不是併）")
    ck(fold_eid(_rn_m) == _rn_m and EID_RENAME[_rn_c] == _rn_m
       and _dr in EID_ALIAS_DROP,
       "2.15e 折疊表 import 自凍結核心（本檔零複製）")

    # ── 2b. ★★ 選配上界 `hi_days_ago`（2026-08-28；SPEC-RUN-COST §9.13-11）───────
    #   身分三條：**預設逐位元不變**、**有界形恰多一段上界**、**值不進 SQL 文本**。
    #   第一條沒釘住 ⇒ 三個既有呼叫者（推送本體／`_probe_kun_activity`／`core_prod_calendar`）
    #   會在沒有人動它們的情況下換了一條 SQL；那是本專案最貴的一種靜默改動。
    _LO_ANCHOR = "  AND se.stpStartTime >= DATEADD(day, -?, GETDATE())\n"
    _HI_LINE = "  AND se.stpStartTime < DATEADD(day, -?, GETDATE())\n"
    for _wd in (True, False):
        for _board in (True, False):
            _base = build_lastprod_sql(with_designation=_wd, with_result=_board)
            _dflt = build_lastprod_sql(with_designation=_wd, with_result=_board, hi_days_ago=None)
            ck(_dflt == _base,
               f"2.15 ★★ `hi_days_ago=None` ⇒ **與不給參數逐位元相同**（wd={_wd} board={_board}）")
    ck(seg_sql.count("?") == 2 and _HI_LINE not in seg_sql
       and build_lastprod_sql()[1] == ("cls", "days"),
       "2.15b ★★ 預設形**本體無上界**、仍是 class+days 兩參數（推送窗恆錨在 now）")
    _bnd, _bp = build_lastprod_sql(with_designation=True, with_result=True, hi_days_ago=91)
    ck(_bp == ("cls", "days_lo", "days_hi") and _bnd.count("?") == len(_bp) == 3,
       "2.16 ★ 有界形參數＝(class, 下界天數, 上界天數)、`?` 數與參數名數相符")
    ck(_bnd.replace(_HI_LINE, "") == seg_sql,
       "2.16b ★★ 有界形 ＝ 預設形 ＋ **唯一一段附加**（拿掉那一行就逐字回到預設形）")
    _bl, _sl2 = _bnd.splitlines(), seg_sql.splitlines()
    ck(_bl == _sl2[:_sl2.index(_LO_ANCHOR.rstrip("\n")) + 1] + [_HI_LINE.rstrip("\n")]
       + _sl2[_sl2.index(_LO_ANCHOR.rstrip("\n")) + 1:],
       "2.16c ★★ 上界那一行**就插在下界錨的正後方**、其餘每一行逐字不動")
    ck(len(_bl) == len(_sl2) + 1, "2.16d 有界形只多一行（多／少都是「不只附加」）")
    ck("stpStartTime < DATEADD" in _bnd and "stpStartTime <= DATEADD" not in _bnd,
       "2.17 ★★ 上界是**半開 `<`**（`<=` ⇒ 相鄰兩段把邊界上那一段各算一次＝重複計數）")
    ck(all(build_lastprod_sql(hi_days_ago=_h)[0] == _bnd for _h in (0, 1, 91, 182, 3650)),
       "2.18 ★★ **值不進 SQL 文本**：不同 `hi_days_ago` 產生逐位元相同的 SQL（零注入面）")
    ck(build_lastprod_sql(hi_days_ago=0)[1] == ("cls", "days_lo", "days_hi"),
       "2.18b ★ `hi_days_ago=0` 是**合法上界**（`is not None` 判定，不是真值判定）")
    for _bad in (True, False, -1, "91", [], object()):
        try:
            build_lastprod_sql(hi_days_ago=_bad)
            ck(False, f"2.19 非法上界 {_bad!r} 應 raise")
        except ValueError:
            ck(True, f"2.19 ★ 非法上界 {_bad!r} ⇒ **拒絕構造**（布林＝把語意參數當旗標；負值＝lo/hi 寫反）")
    ck(sum(1 for s in all_sql if _HI_LINE in s) == 4,
       "2.20 ★ 有界形**四種降級組合全部登記進 `ALL_SQL()`**（漏登記＝那些文本沒有人掃負斷言）")
    ck("stpEndTime >= se.stpStartTime" in _bnd and "OPTION (RECOMPILE)" in _bnd
       and "stpPersonID" not in _bnd and "IN (" not in _bnd,
       "2.21 ★ 有界形**繼承每一條鐵則**（`>=` end-filter／RECOMPILE／零人名／零 IN-list）")

    # ── 2b. #104(b) 站別 lot 查詢（`SPEC-LANDING.md` §18.26.3；C-LD44）──────────
    #   這一條是本檔**唯一**打 `dbo.process_segment_v` 的查詢，也是唯一一條「問站不問機」的。
    #   它會讓 landing 上一顆本來空心的方塊變綠 ⇒ 形狀的每一條都要釘死。
    sl_sql, sl_pn = build_station_lot_sql()
    ck(sl_pn == ("cls", "days") and sl_sql.count("?") == 2
       and "SegmentClassID = ?" in sl_sql,
       "2.22 ★ 固定、參數化、逐站點查（兩個參數 cls/days；站名絕不進 SQL 文本）")
    ck("dbo.process_segment_v" in sl_sql and "segment_equipment_v" not in sl_sql
       and "astEquipmentID" not in sl_sql,
       "2.23 ★★ **零設備條件**：這五站的 lot 段在設備 view 上本來就沒有列（那正是這幾台"
       "量不到的原因）⇒ 加設備條件就是把『我問錯了』講成『它沒在跑』")
    ck(all(c in sl_sql for c in ("v.stpEndTime", "v.stpStartTime", "v.matDesignation",
                                 "v.stpTestResult", "v.matMaterialLotID",
                                 "v.mclMaterialClassID"))
       and "assyModule" in sl_sql and "N'PASS', N'FAIL'" in sl_sql
       and "LEFT(v.matMaterialLotID, 2)" in sl_sql,
       "2.24 ★ 表與欄逐字抄 `_PROBE-NOMES-STATION.sql` STEP1 的 `#seg`（Chris 09-08 實跑過）"
       "＝[[ssms-sql-column-provenance]]：每個欄名都追得到跑過的查詢")
    ck("stpEndTime >= v.stpStartTime" in sl_sql
       and "stpEndTime > v.stpStartTime" not in sl_sql,
       "2.25 ★ end-filter 用 >= 不用 >（零工期段是合法瞬時事件段＝M4 事故）")
    ck("DATEADD(day, -?, GETDATE())" in sl_sql and sl_sql.rstrip().endswith("OPTION (RECOMPILE)"),
       "2.26 窗恆錨在 now、尾帶 RECOMPILE（同 build_lastprod_sql／build_nest_sql）")
    ck("MAX(" not in sl_sql.upper() and "GROUP BY" not in sl_sql.upper()
       and "DISTINCT" not in sl_sql.upper(),
       "2.27 ★★ 聚合零下推——§18.26.3 那句『取 MAX(stpEndTime)』落在 Python"
       "（`max_prod_end_by_station`），不落在 view 上（B4 斷崖）")
    ck(all(f"N'{d}'" not in sl_sql for d in PRODUCTION_DESIGNATIONS)
       and "matDesignation IN" not in sl_sql and "matDesignation =" not in sl_sql,
       "2.28 ★ designation 過濾**刻意留在 Python**（同一把尺只有一份；SQL 裡多一份字面清單"
       "＝哪天 Chris 改口徑會漏改一邊。⚠ 比對的是**帶引號的字面值**：`assyProduction"
       "ReliabilityTesting` 這個物料類別裡本來就含 `Production` 這個子字串）")
    ck(sl_sql in ALL_SQL() and sum(1 for s in ALL_SQL() if "process_segment_v" in s) == 1,
       "2.29 ★ 已登記進 `ALL_SQL()`（漏登記＝上面那一整排負斷言掃不到這條文本）")
    ck(all(c not in EQP_STATIONS for c in STATION_LOT_PROXY)
       and all(c not in ("segScrapMaterial", "segPostLaminationPeelTestVisualInspection",
                         "segUnitDieLevelVisualPQInspection")
               for c in STATION_LOT_PROXY),
       "2.30 ★ 參數只可能來自 `STATION_LOT_PROXY`：五站都不在設備稼動名冊、也不在探針排除的"
       "三站 ⇒ 探針那條 `NOT IN` 抄過來會是永遠為真的死條件（所以不抄）")

    # ── 3. guard ────────────────────────────────────────────────────────────
    full = {k: True for k in GUARD_KEYS}
    assert_guard(full)
    ck(True, "3.1 全欄在 ⇒ 不 raise")
    for k in BEARING_GUARD_KEYS:
        bad = dict(full)
        bad[k] = False
        try:
            assert_guard(bad)
            ck(False, f"3.2 承重欄 {k} ABSENT 應 raise")
        except SchemaDivergence as e:
            ck(k in str(e) or "segment_equipment_v" in str(e), f"3.2 {k} 具名 raise")
    soft = dict(full)
    soft["view_equipment"] = False
    assert_guard(soft)
    ck(True, "3.3 equipment_v ABSENT ⇒ 可降級、不 raise")
    soft2 = dict(full)
    soft2["matDesignation"] = False
    assert_guard(soft2)
    ck(True, "3.3b ★ matDesignation ABSENT ⇒ 可降級、不 raise"
             "（輔助欄消失不得讓推送停擺＝對面史表永久空洞）")
    soft3 = dict(full)
    soft3["stpTestResult"] = False
    assert_guard(soft3)
    ck(True, "3.3c ★ stpTestResult ABSENT ⇒ 可降級、不 raise（#31 同級；"
             "MES KB stale-risk 真發生時的自動安全網）")
    ck(len(GUARD_KEYS) == build_column_guard_sql().count("CASE WHEN"),
       "3.4 GUARD_KEYS 與 guard SQL 欄序同長（兩者必須同步改）")

    # ── 4. pin ③＋配對原子性：argmax 段記錄（2026-08-22 起值＝(end, start, designation, result)）──
    t = D(2026, 8, 15, 9, 0, 0)
    h = timedelta(hours=1)
    m = max_end_by_eid([("EID-1", t, t - h, "Production", "PASS"),
                        ("EID-1", t - 5 * h, t - 6 * h, "Engineering (R&D)", "FAIL"),
                        ("EID-2", t, t, None, None)])
    ck(m == {"EID-1": (t, t - h, "Production", "PASS"), "EID-2": (t, t, None, None)},
       "4.1 per-EID 取 end 最大的**整段**")
    ck(m["EID-1"][1] == t - h and m["EID-1"][3] == "PASS",
       "4.2 ★ 配對原子性：start／result 是**勝出那段**自己的（落選段 FAIL 不得滲入）")
    zero = max_end_by_eid([("EID-Z", t, t, "Production", "PASS")])   # 零工期段：end == start
    ck(zero["EID-Z"] == (t, t, "Production", "PASS"),
       "4.3 ★ 零工期段原值照收（八站結構上全是這種段；start==end 合法非髒值）")
    ck(max_end_by_eid([("EID-1", None, t, "x", None), (None, t, t, "x", None),
                       ("", t, t, "x", None)]) == {},
       "4.4 None end／空 EID 丟棄、不炸")
    ck(max_end_by_eid([("EID-1", t - timedelta(days=1), t - timedelta(days=1), "P", "FAIL")],
                      prev={"EID-1": (t, t - h, "Production", "PASS")})["EID-1"]
       == (t, t - h, "Production", "PASS"),
       "4.5 增量合併＝key 單調（較舊的新觀測不得蓋掉既有整段記錄）")
    a = max_end_by_eid([("EID-T", t, t - h, "A", "PASS"), ("EID-T", t, t - 2 * h, "B", "FAIL")])
    b = max_end_by_eid([("EID-T", t, t - 2 * h, "B", "FAIL"), ("EID-T", t, t - h, "A", "PASS")])
    ck(a == b == {"EID-T": (t, t - h, "A", "PASS")},
       "4.6 ★ 同 end 的 tiebreak 確定性（與輸入順序無關＝逐輪重跑挑同一段）")
    ck(max_end_by_eid([("EID-N", t, None, "P", None)])["EID-N"] == (t, t, "P", None),
       "4.7 防呆：start=None（只可能來自注入）⇒ 塌成零工期形，不發明值也不丟 end")
    ck(max_end_by_eid([("EID-R", t, t - h, "Production", None)])["EID-R"][3] is None,
       "4.8 ★ result 可獨立為 null（MES 原值本就可 NULL；不補預設＝原值直送）")
    _prod_newer_rnd = [("EID-P", t - h, t - 2 * h, "Production", "PASS"),
                       ("EID-P", t, t - h, "Engineering (R&D)", "FAIL")]
    ck(max_prod_end_by_eid(_prod_newer_rnd) ==
       {"EID-P": (t - h, t - 2 * h, "Production", "PASS")},
       "4.9 ★ 較新的 Engineering (R&D) 不得蓋掉較舊的 Production-grade 段")
    ck(max_prod_end_by_eid([("EID-RND", t, t - h, "Engineering (R&D)", "PASS")]) == {},
       "4.10 ★ 只有 R&D ⇒ prod_* 四欄全部無事實（不把試跑說成生產）")

    # ── 5. 窗規劃（不確定一律退整窗）────────────────────────────────────────
    ck(plan_window(None, now, 30, 2)[:2] == (30, "full:no_state"), "5.1 首跑整窗")
    ck(plan_window({}, now, 30, 2)[0] == 30, "5.2 空狀態整窗")
    good = {"schema": STATE_SCHEMA, "lookback_days": 30,
            "updated_at": _z(now - timedelta(hours=1)),
            "covered_from": _z(now - timedelta(days=40)), "eids": {}}
    d, why, inc = plan_window(good, now, 30, 2)
    ck((d, inc) == (2, True) and why.startswith("incremental"), "5.3 正常 ⇒ 增量 2 天")
    ck(plan_window(good, now, 30, 2, force_full=True)[0] == 30, "5.4 --full 強制整窗")
    ck(plan_window(dict(good, lookback_days=7), now, 30, 2)[1] == "full:lookback_changed",
       "5.5 lookback 改過 ⇒ 整窗")
    short = dict(good, covered_from=_z(now - timedelta(days=3)))
    ck(plan_window(short, now, 30, 2)[1] == "full:coverage_short",
       "5.6 ★ 覆蓋不足以支撐『近 30 天無訊號』⇒ 整窗（不靜默少掃）")
    gap = dict(good, updated_at=_z(now - timedelta(days=5)))
    ck(plan_window(gap, now, 30, 2)[0] == 6, "5.7 停過 5 天 ⇒ 窗自動擴到 6 天")
    huge = dict(good, updated_at=_z(now - timedelta(days=60)))
    ck(plan_window(huge, now, 30, 2)[1] == "full:gap_too_long", "5.8 停太久 ⇒ 整窗")
    back = dict(good, updated_at=_z(now + timedelta(hours=2)))
    ck(plan_window(back, now, 30, 2)[1] == "full:clock_went_backwards", "5.9 時鐘倒退 ⇒ 整窗")
    ck(plan_window(dict(good, covered_from="garbage"), now, 30, 2)[1] == "full:bad_state_time",
       "5.10 狀態時戳壞掉 ⇒ 整窗")

    # ── 6. 狀態合併與有效 lookback ───────────────────────────────────────────
    def _R(end, mins=20, dsg="Production", res="PASS"):   # 測試用段記錄：start＝end−mins 分
        return (end, end - timedelta(minutes=mins), dsg, res)

    obs = {"EID-A": _R(D(2026, 8, 15, 5, 0, 0))}
    st1 = merge_state(None, obs, now, 30, 30, False,
                      prod_observed=obs, prod_designation_available=True)
    ck(st1["covered_from"] == _z(now - timedelta(days=30)), "6.1 整窗 ⇒ 覆蓋起點＝now−30d")
    ck(effective_lookback_days(st1, now, 30) == 30, "6.2 有效 lookback ＝ 30")
    ck(st1["eids"]["EID-A"] == {"end": "2026-08-15T05:00:00Z",
                                "start": "2026-08-15T04:40:00Z",
                                "designation": "Production", "result": "PASS",
                                "prod_end": "2026-08-15T05:00:00Z",
                                "prod_start": "2026-08-15T04:40:00Z",
                                "prod_designation": "Production", "prod_result": "PASS"},
       "6.2b ★ 狀態檔 v4 形＝ordinary + prod_* paired facts（鍵序固定）")
    st2 = merge_state(st1, {"EID-B": _R(D(2026, 8, 15, 5, 30, 0))}, now, 2, 30, True)
    ck(set(st2["eids"]) == {"EID-A", "EID-B"}, "6.3 增量保留舊 EID（不是每輪重來）")
    ck(st2["covered_from"] == st1["covered_from"], "6.4 增量不縮短覆蓋起點")
    ck(effective_lookback_days(st2, now, 30) == 30, "6.5 增量後有效 lookback 仍 30")
    old = merge_state(None, {"EID-OLD": _R(now - timedelta(days=70))}, now, 30, 30, False)
    ck("EID-OLD" not in old["eids"], "6.6 舊於 2×lookback 的條目修剪掉（判定看 end）")
    keep = merge_state(None, {"EID-MID": _R(now - timedelta(days=45))}, now, 30, 30, False)
    ck("EID-MID" in keep["eids"], "6.7 1×~2× 之間的留著（它要送 null 而不是靜默消失）")
    ck(effective_lookback_days({}, now, 30) == 0, "6.8 無覆蓋 ⇒ 0（呼叫端據此拒送 null）")
    ck(load_state(os.path.join(REPO, "no-such-state.json")) is None, "6.9 狀態檔不存在 ⇒ None")
    mixA = merge_state(st1, {"EID-A": (now, now - timedelta(minutes=3),
                                       "Engineering (R&D)", "FAIL")},
                       now, 2, 30, True)
    ck(mixA["eids"]["EID-A"]["start"] == _z(now - timedelta(minutes=3))
       and mixA["eids"]["EID-A"]["designation"] == "Engineering (R&D)"
       and mixA["eids"]["EID-A"]["result"] == "FAIL",
       "6.9b ★ 整筆取代：新段勝出時 start/designation/result 一起換（不逐欄取 max＝不造假段）")
    ck(mixA["eids"]["EID-A"]["prod_end"] == st1["eids"]["EID-A"]["prod_end"],
       "6.9c ★ generic 較新的 R&D 段不改 production-grade prod_end")
    no_desig = merge_state(st1, {"EID-A": _R(now, dsg="Engineering (R&D)")}, now, 2, 30, True,
                           prod_observed={}, prod_designation_available=False)
    ck(no_desig["prod_designation_available"] is False
       and all(no_desig["eids"]["EID-A"][k] is None
               for k in ("prod_end", "prod_start", "prod_designation", "prod_result")),
       "6.9d ★ designation 不可用 ⇒ prod_* 全 null（不保留舊 claim、不猜）")

    # ── 6c. v5 `sub_units`（子工位末筆生產級段；`SPEC-LANDING.md` §18.23）────────────
    ck(STATE_SCHEMA == "board.lastprod.state.v6", "6.20 狀態檔版本＝v6")
    # ★★ 6.20a–6.20i：**v4 就地升級、絕不整窗重建**（2026-09-08 Fable 審後裁；§18.23.5）。
    #   這一組是**對外**保護：`core_lastprod_push` 是主線每 10 分鐘的排程任務，拒收 v4 就等於
    #   下一輪自動對 cmms 送出 `full_window_rebuild=true`（他們據此刪列重建）。
    _v4_doc = {"schema": "board.lastprod.state.v4", "updated_at": _z(now - timedelta(minutes=10)),
               "covered_from": _z(now - timedelta(days=30)), "lookback_days": 30,
               "prod_designation_available": True,
               "eids": {"EID-A": _rec_to_state(_R(now - timedelta(hours=1)))}}
    _v4_doc["eids"]["EID-A"].update(_prod_fields(_R(now - timedelta(hours=1))))
    _up = upgrade_state(_v4_doc)
    ck(_up is not None and _up["schema"] == STATE_SCHEMA and _up["sub_units"] == {}
       and _up["station_lot"] == {},
       "6.20a v4 檔就地升級成本版、兩個加性鍵（`sub_units`／`station_lot`）都補空 dict")
    ck(_up["eids"] == _v4_doc["eids"]
       and all(_up[k] == _v4_doc[k] for k in ("updated_at", "covered_from", "lookback_days",
                                              "prod_designation_available")),
       "6.20b ★ 升級只動 `schema` 與新鍵——`eids` 與覆蓋三欄**逐字保留**（那正是不必重建的理由）")
    ck(_v4_doc["schema"] == "board.lastprod.state.v4" and "sub_units" not in _v4_doc,
       "6.20c 純函式：不就地改傳入的 dict")
    _d4, _w4, _i4 = plan_window(_up, now, 30, 2)
    ck(_i4 is True and _w4.startswith("incremental") and _d4 <= 2,
       "6.20d ★ 升級後照 incremental 走——**不是** full:no_state（那一句就是刪資料的觸發器）")
    ck(rebuild_claim(incremental=_i4, partial=False) is False,
       "6.20e ★★ 對外保護：升級輪的 `full_window_rebuild` 恆 False（cmms 不會被要求刪列）")
    ck(upgrade_state(dict(_v4_doc, schema="board.lastprod.state.v3")) is None
       and plan_window(None, now, 30, 2)[1] == "full:no_state",
       "6.20f v3 以下仍整份拒收 ⇒ full:no_state（那幾次真的是換口徑，行為不放寬）")
    ck(upgrade_state(None) is None and upgrade_state({"schema": STATE_SCHEMA}) is None
       and upgrade_state({"schema": STATE_SCHEMA, "eids": []}) is None,
       "6.20g 形不對（非 dict／缺 eids／eids 不是 dict）⇒ None，不炸")
    _v5_ok = dict(_v4_doc, schema=STATE_SCHEMA, sub_units={"EID-A": {"nest:1": _z(now)}},
                  station_lot={})
    ck(upgrade_state(_v5_ok) is _v5_ok,
       "6.20h 已是本版且形正確 ⇒ **原物件回傳**（零複製、零改寫）")
    ck(upgrade_state(dict(_v5_ok, sub_units="broken"))["sub_units"] == {},
       "6.20i 本版檔的 `sub_units` 壞掉只壓成空 dict——不因一個加性欄丟掉整份覆蓋主張")
    ck(state_upgraded_from(_v4_doc) == "board.lastprod.state.v4"
       and state_upgraded_from(dict(_v4_doc, schema="board.lastprod.state.v5"))
       == "board.lastprod.state.v5"
       and state_upgraded_from(_v5_ok) is None and state_upgraded_from(None) is None,
       "6.20j 升級來源可被 log 說出來（無聲升級＝日後看不出那一輪為什麼沒重建）")
    ck(st1["sub_units"] == {}, "6.21 沒有子工位觀測 ⇒ 空 dict（不是缺鍵、也不是 null）")
    # WP-10d（R-P11）：具名機台由名冊常數推導（私有樹＝EID-00425／26596／26718／12724、co-tag 母機 08158〔子 05707、07757〕）
    _nl_r = list(_wcap.NEST_EAV_UNITS)
    _N0, _N1, _NF, _NL = _nl_r[0], _nl_r[1], _nl_r[3], _nl_r[4]
    _CP = list(_wcap.COTAG_CHILD_UNITS)[0]
    _CC1, _CC2 = _wcap.COTAG_CHILD_UNITS[_CP][:2]
    ck(nest_unit_id(3) == "nest:3"
       and all(nest_unit_id(n) in _wcap.installed_units(_NL)
               for n in _wcap.NEST_EAV_UNITS[_NL]["nests"]),
       "6.22 ★ `nest:<n>` 的形與 `board_capacity.installed_units` 逐位一致（互釘）")
    _ne = D(2026, 8, 15, 6, 0, 0)
    _nrows = [(_NL, _ne, _ne - timedelta(minutes=4), "Production", 3),
              (_NL, _ne - timedelta(hours=5), _ne, "Production", 3),   # 較舊、不勝出
              (_NL, _ne, _ne, "Engineering (R&D)", 4),                 # 非生產級
              (_NF, _ne, _ne, "Production", 0),                        # 名冊外的座位
              ("EID-NOPE", _ne, _ne, "Production", 1)]                         # 名冊外的機台
    _nu = max_prod_end_by_unit(_nrows)
    ck(_nu == {_NL: {"nest:3": _ne}},
       "6.23 ★ 形①：只收生產級、只收名冊宣告的座位、argmax 取 end 最大"
       "（`EID-26718` 的 nest 0 是結構性噪音，收進來就是憑空多一座）")
    ck(max_prod_end_by_unit([(_NL, _ne, _ne, "Engineering (Baseline)", 1)])
       == {_NL: {"nest:1": _ne}},
       "6.23b 生產級閉集與 `max_prod_end_by_eid` 同一把尺（Baseline 也算）")
    ck(max_prod_end_by_unit([(_NL, _ne, _ne, "Production", None)]) == {}
       and max_prod_end_by_unit([(_NL, None, _ne, "Production", 1)]) == {},
       "6.23c 壞列逐筆跳過（nest 非整數／end 缺）——不炸、也不猜")
    _cot = cotag_sub_units({_CC1: (_ne, _ne, "Production", "PASS"),
                            _CC2: (_ne - timedelta(hours=1), _ne, "Production", None)})
    ck(_cot == {_CP: {_CC1: _ne, _CC2: _ne - timedelta(hours=1)}},
       "6.24 ★ 形②＝從既有 production-grade map **投影**（零新查詢）、掛在母機底下")
    ck(cotag_sub_units({}) == {},
       "6.24b 形②：一顆子 EID 都沒觀測到 ⇒ 整台缺席（缺席＝不知道，不是 0）")
    ck(merge_sub_units({"E": {"u1": _ne - timedelta(hours=3), "u2": _ne}},
                       {"E": {"u1": _ne}})
       == {"E": {"u1": _ne, "u2": _ne}},
       "6.25 ★ 逐 (eid, unit) 取 max：增量輪不得把上一輪的座位打回不知道")
    _st_sub = merge_state(None, obs, now, 30, 30, False, prod_observed=obs,
                          sub_units_observed={_NL: {"nest:1": now - timedelta(hours=1)},
                                              _N0: {"nest:2": now - timedelta(days=70)}})
    ck(_st_sub["sub_units"] == {_NL: {"nest:1": _z(now - timedelta(hours=1))}},
       "6.26 ★ 落盤形＝Z 字串；舊於 2×lookback 的座位與 `eids` 同一條修剪")
    _st_sub2 = merge_state(_st_sub, obs, now, 2, 30, True, prod_observed=obs,
                           sub_units_observed={_NL: {"nest:2": now}})
    ck(_st_sub2["sub_units"][_NL] == {"nest:1": _z(now - timedelta(hours=1)),
                                              "nest:2": _z(now)},
       "6.27 增量輪把新座位併進舊表（不是每輪重來）")
    ck(merge_state(_st_sub, obs, now, 2, 30, True, prod_observed={},
                   prod_designation_available=False)["sub_units"] == {},
       "6.28 ★ designation 不可用 ⇒ `sub_units` 整張清空（同 prod_* 的降級規則、不留舊 claim）")
    ck(_sub_units_from_state({"sub_units": {"E": {"u": "not-a-time", "v": _z(now)}}})
       == {"E": {"v": now}},
       "6.29 讀端：壞時刻逐筆跳過（狀態檔壞掉不炸整輪）")
    ck(list(_sub_units_to_state({"B": {"z": now, "a": now}, "A": {"q": now}}))
       == ["A", "B"]
       and list(_sub_units_to_state({"B": {"z": now, "a": now}})["B"]) == ["a", "z"],
       "6.30 落盤鍵全排序 ⇒ 逐輪位元穩定（同一份事實不得產生兩份檔）")

    # ── 6d. v6 `station_lot`（站別末筆生產級 lot 段；§18.26.3、C-LD44）──────────────
    _sl_cls = "segDieArrayTaping"
    _sl_now = D(2026, 8, 15, 6, 0, 0)
    _sl_rows = [(_sl_cls, _sl_now, _sl_now - timedelta(minutes=4), "Production"),
                (_sl_cls, _sl_now + timedelta(hours=1), _sl_now, "Engineering (R&D)"),
                (_sl_cls, _sl_now - timedelta(hours=9), _sl_now, "Engineering (Baseline)"),
                (_sl_cls, None, _sl_now, "Production")]
    ck(max_prod_end_by_station(_sl_rows) == {_sl_cls: _sl_now},
       "6.31 ★ 站別 argmax：只收生產級（較新的 R&D 段不勝出）、Baseline 也算、壞列跳過")
    ck(max_prod_end_by_station([], prev={_sl_cls: _sl_now}) == {_sl_cls: _sl_now}
       and max_prod_end_by_station(
           [(_sl_cls, _sl_now - timedelta(hours=1), _sl_now, "Production")],
           prev={_sl_cls: _sl_now}) == {_sl_cls: _sl_now},
       "6.31b `prev` 續算、取 max（單調 ⇒ 合併恆安全；增量輪不得把舊值打回不知道）")
    _sl_merged = merge_station_lot(None, {_sl_cls: _sl_now, "segWireBondingPullTest": None},
                                   _sl_now + timedelta(minutes=3))
    ck(_sl_merged[_sl_cls]["last_prod_end"] == _sl_now
       and _sl_merged["segWireBondingPullTest"]["last_prod_end"] is None
       and all(v["asof"] == _sl_now + timedelta(minutes=3) for v in _sl_merged.values()),
       "6.32 ★ **掃過但窗內零生產級段＝在場的 None**（缺席不表達語意＝pin ① 的精神）；"
       "`asof` 是「我上次確認這一站」的時點，不是段的時刻")
    _sl_keep = merge_station_lot(_sl_merged, {}, _sl_now + timedelta(hours=1))
    ck(_sl_keep == _sl_merged,
       "6.33 ★★ **查詢失敗隔離**：本輪沒掃到任何站 ⇒ 上一輪整張表原樣保留（含它舊的 asof）"
       "——讀端看得出資訊有多舊，不會把『我沒問』讀成『它沒動』")
    _sl_older = merge_station_lot(_sl_merged, {_sl_cls: _sl_now - timedelta(days=3)},
                                  _sl_now + timedelta(hours=1))
    ck(_sl_older[_sl_cls]["last_prod_end"] == _sl_now
       and _sl_older[_sl_cls]["asof"] == _sl_now + timedelta(hours=1),
       "6.34 hi-water：較舊的觀測不覆蓋較新的末筆，但 asof 照樣前進")
    _sl_st = merge_state(None, obs, now, 30, 30, False, prod_observed=obs,
                         station_lot_observed={_sl_cls: now - timedelta(minutes=30)})
    ck(_sl_st["station_lot"] == {_sl_cls: {"last_prod_end": _z(now - timedelta(minutes=30)),
                                          "asof": _z(now)}}
       and _sl_st["station_lot_err"] is None,
       "6.35 落盤形＝`{站: {last_prod_end, asof}}` 的 Z 字串（鍵排序、逐輪位元穩定）")
    _sl_st2 = merge_state(_sl_st, obs, now, 2, 30, True, prod_observed=obs,
                          station_lot_observed={}, station_lot_err="ProgrammingError: boom")
    ck(_sl_st2["station_lot"] == _sl_st["station_lot"]
       and _sl_st2["station_lot_err"] == "ProgrammingError: boom",
       "6.36 ★★ 失敗那一輪：值保留上一輪、錯誤留一句話——**cmms payload 與推送流程零影響**")
    ck("station_lot" not in json.dumps(
        build_payload(now, 30, {"EID-A": _R(now)}, ("EID-A",), None)[0], ensure_ascii=False)
       and "station_lot_err" not in json.dumps(
           build_payload(now, 30, {"EID-A": _R(now)}, ("EID-A",), None)[0],
           ensure_ascii=False),
       "6.37 ★★ **cmms payload 一個位元不變**：`build_payload` 連 station_lot 這個字都沒有")
    _sl_v5 = dict(_v4_doc, schema="board.lastprod.state.v5", sub_units={"EID-A": {"nest:1": _z(now)}})
    _sl_up = upgrade_state(_sl_v5)
    ck(_sl_up["schema"] == STATE_SCHEMA and _sl_up["station_lot"] == {}
       and _sl_up["sub_units"] == {"EID-A": {"nest:1": _z(now)}}
       and _sl_up["eids"] == _sl_v5["eids"],
       "6.38 ★ v5→v6 就地升級：補 `station_lot={}`、`sub_units` 與 `eids` **逐字保留**")
    ck(upgrade_state(_sl_v5) is not _sl_v5 and "station_lot" not in _sl_v5,
       "6.38b 純函式：不就地改傳入的 v5 dict")
    _sl_d, _sl_w, _sl_i = plan_window(_sl_up, now, 30, 2)
    ck(_sl_i is True and _sl_w.startswith("incremental")
       and rebuild_claim(incremental=_sl_i, partial=False) is False,
       "6.39 ★★ 對外保護：v5→v6 那一輪照 incremental 走、`full_window_rebuild` 恆 False"
       "（拒收 v5 就等於自動要求 cmms 刪列重建）")
    _sl_v6 = dict(_sl_up)
    ck(upgrade_state(_sl_v6) is _sl_v6, "6.40 已是 v6 且形正確 ⇒ 原物件回傳（零複製）")
    ck(upgrade_state(dict(_sl_v6, station_lot="broken"))["station_lot"] == {}
       and upgrade_state(dict(_sl_v6, sub_units="broken"))["sub_units"] == {},
       "6.41 兩個加性鍵壞掉都只壓成空 dict——不因一個加性欄丟掉整份覆蓋主張")
    ck(_station_lot_from_state({"station_lot": {
        "A": {"last_prod_end": _z(now), "asof": _z(now)},
        "B": {"last_prod_end": "not-a-time", "asof": _z(now)},
        "C": {"last_prod_end": _z(now), "asof": "not-a-time"},
        "D": "broken"}}) == {"A": {"last_prod_end": now, "asof": now},
                             "B": {"last_prod_end": None, "asof": now}},
       "6.42 讀端：`asof` 解析不了 ⇒ **整筆丟**（沒有時點的末筆講不出年齡）；"
       "末筆壞掉只壓成 None（那一站就是『掃過、沒有』）")
    ck(list(_station_lot_to_state({"b": {"last_prod_end": now, "asof": now},
                                   "a": {"last_prod_end": None, "asof": now}})) == ["a", "b"],
       "6.43 落盤鍵排序 ⇒ 同一份事實不得產生兩份檔")

    # ── 6e. C-LD46：寫端載入把**已退出名冊**的 eid 座丟掉（`SPEC-LANDING.md` §18.30.4-3）─
    _pr_in = {_NL: {"nest:1": _z(now)},
              "EID-26161": {"nest:1": _z(now), "nest:5": _z(now)},
              "EID-09763": {"nest:3": _z(now)},
              "EID-38316": {"nest:2": _z(now)},
              _CP: {_CC1: _z(now)}}
    _pr_out, _pr_drop = prune_sub_units(_pr_in)
    ck(_pr_drop == tuple(sorted(("EID-09763", "EID-26161", "EID-38316")))   # 回傳為排序後 tuple；字面不依賴編號排序
       and set(_pr_out) == {_NL, _CP},
       "6.44 ★ C-LD46：舊狀態檔裡 AOI 三台的座被丟掉"
       "（不丟 ⇒ `merge_sub_units` 只取較新、那些 key 永遠搬不走）")
    ck(all(_pr_out[e] is _pr_in[e] for e in _pr_out) and len(_pr_in) == 5,
       "6.45 ★ 其餘 key **逐位元同**（同一個內層物件），且不就地改傳入的 dict")
    ck(prune_sub_units({}) == ({}, ()) and prune_sub_units(None) == ({}, ())
       and prune_sub_units("broken") == ({}, ()),
       "6.46 空 dict／None／壞值都不炋（同 `_sub_units_from_state` 的紀律）")
    _pr_keep = {_NL: {"nest:1": _z(now)}}
    ck(prune_sub_units(_pr_keep) == (_pr_keep, ())
       and prune_sub_units(_pr_keep)[0] is _pr_keep,
       "6.47 沒有要丟的 ⇒ **原物件回傳**（零複製、零 log）")
    ck(prune_sub_units({"EID-ZZZ": {"nest:1": _z(now)}}, roster=("EID-ZZZ",))[1] == ()
       and prune_sub_units({_NL: {"nest:1": _z(now)}}, roster=())[1] == (_NL,),
       "6.48 `roster=` 可注入 ⇒ selftest 不必靠真名冊（預設仍是名冊唯一出處）")
    ck(STATE_SCHEMA == "board.lastprod.state.v6"
       and set(_wcap.machines_with_units()).isdisjoint(
           {"EID-09763", "EID-26161", "EID-38316"}),
       "6.49 ★ C-LD46 **不跳版**：`sub_units` 只是少幾個 key、語意一個位元沒動；"
       "三台確實不在名冊上")

    # ── 6b. partial 輪的事實集：舊值當地板（2026-08-16；防降級，不是涵蓋主張）────────
    #   母機的 segment 橫跨多個 class ⇒ 只掃到前 k 站時，本輪 observed 可能是「已掃到的那幾站
    #   裡的最大值」＝比上一輪推過的還舊。對面 2026-08-16 起逐筆留痕 ⇒ 降級不再自我痊癒。
    stp = merge_state(None, {"EID-A": _R(now - timedelta(hours=2))}, now, 30, 30, False)
    ck(partial_snapshot(stp, {"EID-A": _R(now - timedelta(hours=9))})["EID-A"]
       == _R(now - timedelta(hours=2)),
       "6.10 ★ partial：本輪掃到的較舊值不得覆蓋既有較新事實（母機跨多 class、只掃一半）"
       "——而且擋下的是**整段**（連 start/designation 都不得半套滲入）")
    ps2 = partial_snapshot(stp, {"EID-A": _R(now - timedelta(minutes=5)), "EID-N": _R(now)})
    ck(ps2["EID-A"] == _R(now - timedelta(minutes=5)) and ps2["EID-N"] == _R(now),
       "6.11 partial：較新的觀測照樣取代、名冊外/新 EID 照收（地板不是凍結）")
    ck(partial_snapshot(None, {"EID-A": _R(now)}) == {"EID-A": _R(now)},
       "6.12 partial：無狀態檔 ⇒ 只有本輪事實（首跑不會憑空生值）")
    ck(partial_snapshot({"eids": {"EID-X": "garbage", "EID-Y": None,
                                  "EID-W": {"end": "2026-08-15T05:00:00Z"}}}, {}) == {},
       "6.13 partial：狀態檔壞值丟棄、不炸（含 v1 字串形＝v2 起整筆拒收）")

    # ── 6d. 段記錄 ↔ 狀態檔（配對原子性的守門）──────────────────────────────
    r0 = (now, now - timedelta(minutes=7), "Production", "PASS")
    ck(_rec_from_state(_rec_to_state(r0)) == r0, "6.19 記錄 round-trip")
    ck(_prod_rec_from_state(_prod_fields(r0)) == r0,
       "6.19b ★ v4 prod_* paired-record round-trip")
    ck(_prod_rec_from_state(_prod_fields(None)) is None,
       "6.19c ★ v4 prod_* all-null 是合法無生產／降級形")
    r1 = (now, now, None, None)
    ck(_rec_from_state(_rec_to_state(r1)) == r1, "6.20 零工期＋null designation/result round-trip")
    ck(_rec_from_state("2026-08-15T05:00:00Z") is None,
       "6.21 ★ v1 字串形 ⇒ 整筆拒收（不拿 end 發明 start；換版靠 schema 擋＋整窗重建）")
    ck(_rec_from_state({"end": _z(now), "start": _z(now + timedelta(minutes=1)),
                        "designation": None}) is None,
       "6.22 ★ start > end（時間倒轉＝壞檔）⇒ 整筆丟棄")
    ck(_rec_from_state({"end": _z(now), "start": _z(now), "designation": 123, "result": 456})
       == (now, now, None, None),
       "6.23 designation/result 非字串 ⇒ 壓成 None（非承重欄，不因它丟掉時刻事實）")
    ck(_rec_from_state({"end": _z(now), "start": _z(now), "designation": "Production"})
       == (now, now, "Production", None),
       "6.24 ★ v2 遺形（缺 result 鍵）在記錄層容忍為 None——但 load_state 對 v2 檔整檔拒收，"
       "此容忍只服務注入測試，不是遷移路徑")

    # ── 6c. 覆蓋宣告 rebuild_claim（★ 唯一一個會讓對面**刪資料**的欄位）─────────────
    #   真值表逐格釘死：只有「整窗（非增量）」∧「全站掃完（非 partial）」才宣告。
    ck(rebuild_claim(incremental=False, partial=False) is True,
       "6.14 ★ 整窗掃完 ⇒ 宣告覆蓋（這是旗標唯一為真的一格）")
    ck(rebuild_claim(incremental=True, partial=False) is False,
       "6.15 ★ 增量輪不得宣告覆蓋（只看得到窗的尾巴，沒資格說『沒有更晚的段了』）")
    ck(rebuild_claim(incremental=False, partial=True) is False,
       "6.16 ★ 沒掃完不得宣告覆蓋（拿『我沒看』當『它不存在』＝C-LP6 同一條紀律）")
    ck(rebuild_claim(incremental=True, partial=True) is False, "6.17 增量＋partial ⇒ False")
    #   ★ 負斷言：條件式全檔只有一份（在 `rebuild_claim` 裡）——`run()` 不得內聯。
    #     needle 用**串接**組出來，否則這條斷言會命中自己的原始碼＝無論碼怎麼改都綠
    #     （同 13.2／13.5 的手法；本 repo 抓過好幾次這型假信心）。
    _src_self = open(os.path.abspath(__file__), "r", encoding="utf-8").read()
    _needle = "(not " + "incremental) and (not partial)"
    ck(_src_self.count(_needle) == 1,
       "6.18 ★ 覆蓋宣告的條件式全檔只有一份（在 rebuild_claim 裡）；run() 不得自己內聯")

    # ── 7. payload：四條 pin 的落點 ─────────────────────────────────────────
    roster = ["EID-01", "EID-02", "EID-99"]
    snap = {"EID-01": (D(2026, 8, 15, 4, 12, 0), D(2026, 8, 15, 3, 55, 0), "Production", "PASS"),
            "EID-77": (D(2026, 8, 15, 3, 0, 0), D(2026, 8, 15, 2, 58, 0),
                       "Engineering (R&D)", "FAIL")}
    known = {"EID-01", "EID-02", "EID-77"}
    p, dg = build_payload(now, 30, snap, roster, known)
    ck(p["schema_id"] == SCHEMA_ID, "7.1 信封帶 schema_id")
    ck(p["observed_at"] == "2026-08-15T06:00:00Z" and p["lookback_days"] == 30,
       "7.2 observed_at ＋ lookback_days")
    ids = [it["asset_id"] for it in p["items"]]
    ck(ids == sorted(ids), "7.3 items 依 asset_id 排序（逐輪可 diff）")
    ck({"asset_id": "EID-01", "last_segment_end": "2026-08-15T04:12:00Z",
        "last_segment_start": "2026-08-15T03:55:00Z",
        "last_segment_designation": "Production",
        "last_segment_result": "PASS"} in p["items"],
       "7.4 有訊號 ⇒ 四件事實成組（end/start/designation/result 同一段）")
    ck({"asset_id": "EID-02", "last_segment_end": None,
        "last_segment_start": None, "last_segment_designation": None,
        "last_segment_result": None} in p["items"],
       "7.5 ★ pin ①：主檔有、窗內無訊號 ⇒ items 內四欄全 null（不是缺席）")
    ck(p["unknown"] == ["EID-99"], "7.6 ★ pin ①：MES 不認識 ⇒ unknown[]")
    ck("EID-99" not in ids, "7.7 unknown 與 items 互斥")
    ck("EID-77" in ids, "7.8 名冊外但有訊號 ⇒ 照送事實（scope=roster+observed）")
    p2, _ = build_payload(now, 30, snap, roster, known, scope=SCOPE_ROSTER)
    ck("EID-77" not in [i["asset_id"] for i in p2["items"]], "7.9 scope=roster ⇒ 只推名冊")
    stale = {"EID-01": (now - timedelta(days=40), now - timedelta(days=40, minutes=9),
                        "P", "PASS")}
    p3, _ = build_payload(now, 30, stale, ["EID-01"], known | {"EID-01"})
    ck(p3["items"][0]["last_segment_end"] is None
       and p3["items"][0]["last_segment_start"] is None
       and p3["items"][0]["last_segment_designation"] is None
       and p3["items"][0]["last_segment_result"] is None,
       "7.10 值落在 lookback 窗外 ⇒ 四欄一起 null（『近 N 天內無訊號』對整段成立）")
    p4, dg4 = build_payload(now, 30, snap, roster, known, partial=True)
    ck(all(i["last_segment_end"] is not None for i in p4["items"]),
       "7.11 ★ partial ⇒ 一個 null 都不送（null 是主張，沒掃完不能講）")
    ck("EID-02" not in [i["asset_id"] for i in p4["items"]] and dg4["partial"],
       "7.12 partial 缺席＝pin ① 保證不表達語意")
    p5, _ = build_payload(now, 30, snap, roster, None)
    ck(p5["unknown"] == [] and "EID-99" in [i["asset_id"] for i in p5["items"]],
       "7.13 主檔不可用 ⇒ 不分流 unknown、fail-open 進 items")
    ck(set(p.keys()) == {"schema_id", "observed_at", "lookback_days",
                         "full_window_rebuild", "items", "unknown"},
       "7.14 ★ 信封只有這六個鍵（2026-08-16 五→六：cmms 第十輪要的覆蓋旗標；加欄是拍板不是順手）")
    ck(p["full_window_rebuild"] is False,
       "7.14b ★ 預設不宣告覆蓋（漏傳參數 ⇒ 保守側＝對面只 append 不刪）")
    pr, dgr = build_payload(now, 30, snap, roster, known, rebuild=True)
    ck(pr["full_window_rebuild"] is True and dgr["rebuild"] is True,
       "7.14c 宣告覆蓋 ⇒ 信封與 diag 一致（log 講的與送出去的是同一件事）")
    ck(isinstance(pr["full_window_rebuild"], bool),
       "7.14d ★ 恆布林（對面拿它決定要不要刪列；型別漂移不得靠對方 coerce 收尾）")
    ck(all(set(i.keys()) == {"asset_id", "last_segment_end", "last_segment_start",
                             "last_segment_designation", "last_segment_result"}
           for i in p["items"]),
       "7.15 ★ item 恰五鍵（2026-08-22 四→五＝#31／NOTE-56 拍板；has_open_segment 仍不送——理由見下）")
    ck(all(not any(k.startswith("prod_") for k in i) for i in p["items"]),
       "7.15b ★ negative：v4 prod_* 永不進 board.last_production.v1 payload（fixture 不變）")
    _su_roster = sorted(_wcap.NEST_EAV_UNITS) + [_CP]
    _su_snap = {eid: _R(now) for eid in _su_roster}
    _su_state = {_N1: {"nest:1": now - timedelta(minutes=4),
                                 "nest:2": now - timedelta(days=31),
                                 "nest:99": now - timedelta(minutes=1)}}
    _su_off, _ = build_payload(now, 30, _su_snap, _su_roster, set(_su_roster))
    _su_on, _su_diag = build_payload(now, 30, _su_snap, _su_roster, set(_su_roster),
                                     sub_units=_su_state)
    _su_by_eid = {item["asset_id"]: item for item in _su_on["items"]}
    ck(json.dumps(_su_off, ensure_ascii=False, separators=(",", ":"))
       == json.dumps(build_payload(now, 30, _su_snap, _su_roster, set(_su_roster),
                                   sub_units=None)[0], ensure_ascii=False, separators=(",", ":")),
       "7.15c ★ sub_units=None preserves the pre-feature payload byte for byte")
    ck(set(eid for eid, item in _su_by_eid.items() if "sub_units" in item)
       == set(_wcap.NEST_EAV_UNITS)
       and "sub_units" not in _su_by_eid[_CP],
       "7.15d only the six nested-EAV machines gain the optional item key; co-tag items do not")
    _wire_26596 = _su_by_eid[_N1]["sub_units"]
    ck(list(_wire_26596) == [f"{_N1}-N{_n}" for _n in _wcap.NEST_EAV_UNITS[_N1]["nests"]]
       and _wire_26596[(_N1 + "-N1")] == _z(now - timedelta(minutes=4))
       and _wire_26596[(_N1 + "-N2")] is None
       and _wire_26596[(_N1 + "-N3")] is None
       and sum(len(item["sub_units"]) for item in _su_by_eid.values()
               if "sub_units" in item) == sum(len(_v["nests"]) for _v in _wcap.NEST_EAV_UNITS.values())
        and (_pub_tree or sum(len(_v["nests"]) for _v in _wcap.NEST_EAV_UNITS.values())
             == _pp.PINS["lastprod.nested_seats_n"]),
       "7.15e wire sub_units are numeric-order complete census maps; stale/missing seats are null")
    ck(_su_diag["sub_units_dropped"] == ((_N1, "nest:99"),)
       and all("nest:99" not in item.get("sub_units", {}) for item in _su_on["items"]),
       "7.15f state residue outside the installed roster is diagnosed and never shipped")
    _su_partial, _ = build_payload(now, 30, _su_snap, _su_roster, set(_su_roster),
                                   partial=True, sub_units=_su_state)
    ck(not any("sub_units" in item for item in _su_partial["items"]),
       "7.15g partial scan forbids every optional sub_units key (same no-claim discipline as nulls)")
    # ═══ ★ 2026-08-15 理由更正（行為不變、理由變了，別再引用舊的）═══════════════
    #   舊理由：「該欄不在 cmms 最終信封裡，而 422＝信封形不合 ⇒ 多送會整輪被拒」。
    #   **cmms 已實測回覆，這個理由被證偽**：信封層與 item 層**兩層**都是 pydantic v2
    #   預設 `extra="ignore"` ⇒ 多送的鍵**靜默忽略、照樣 200**（他們實際送過一筆帶
    #   `has_open_segment` 的 payload 驗過，並加了一條測試釘死「多餘欄位必須 200」）。
    #   ⇒ **我方日後要 additive 加欄，不需要再對版一次。**
    #   **現在仍然不送的理由換成**：cmms **尚未消費**該欄（今天送會被靜默丟棄、不落庫、
    #   不影響顯示）⇒ 送了零效益，卻要為它維護 fixture／validator／這條斷言。
    #   ⚠ 另一半事實：該欄當初是**他們自己提的、我方 relay 採納了，是他們實作時掉的**
    #     （NOTE-36 ③ pin 4 原文是「建議忽略，**或**另給 has_open_segment」二選一）。
    #   ⇒ **解除條件**：cmms push「消費端切片上線」那一句 ⇒ 我方加欄，同輪要改的四處＝
    #     本斷言／`build_payload`／`_fixtures/board.last_production.v1.json`（重跑 builder 產生）／
    #     `selftest_fixtures.LP_ITEM_KEYS` 與其 invalid 變體。
    ck(json.dumps(p)[:1] == "{", "7.16 payload 可序列化")
    zt = D(2026, 8, 15, 3, 0, 0)
    zp, _ = build_payload(now, 30, {"EID-Z": (zt, zt, "Production", "PASS")},
                          ["EID-Z"], {"EID-Z"})
    ck(zp["items"][0]["last_segment_end"] == "2026-08-15T03:00:00Z"
       and zp["items"][0]["last_segment_start"] == "2026-08-15T03:00:00Z",
       "7.17 ★ pin ③：零工期段 start==end 原封送出（未被任何判斷改寫）")
    ck(dg["n_null"] == 1 and dg["n_unknown"] == 1, "7.18 diag 計數")
    ck(all((i["last_segment_end"] is None) == (i["last_segment_start"] is None)
           for i in p["items"]),
       "7.19 ★ 配對不變式：end 與 start 恆同 null／同非 null（不存在半段事實）")
    e77 = next(i for i in p["items"] if i["asset_id"] == "EID-77")
    ck(e77["last_segment_designation"] == "Engineering (R&D)"
       and e77["last_segment_result"] == "FAIL",
       "7.20 ★ designation／result 原字串直送（不翻譯、不分類、不判斷——判斷留在 cmms 端；"
       "FAIL 是事實不是判定，『排除已知 FAIL』的謂詞在 cmms）")
    xw = {"EID-X": (now - timedelta(days=1), now - timedelta(days=31), None, None)}
    px, _ = build_payload(now, 30, xw, ["EID-X"], {"EID-X"})
    ck(px["items"][0]["last_segment_end"] is not None
       and px["items"][0]["last_segment_start"] == _z(now - timedelta(days=31))
       and px["items"][0]["last_segment_designation"] is None,
       "7.21 跨窗界段：窗判定只看 end；start 落窗外仍原值照送（夾到 cutoff＝改寫事實）；"
       "designation 可為 null（MES 原值／降級形）")
    rn = {"EID-RN": (now - timedelta(hours=1), now - timedelta(hours=2), "Production", None)}
    prn, _ = build_payload(now, 30, rn, ["EID-RN"], {"EID-RN"})
    ck(prn["items"][0]["last_segment_end"] is not None
       and prn["items"][0]["last_segment_result"] is None,
       "7.22 ★ result 可獨立為 null（有段但 MES 該欄 NULL＝合法原值；"
       "cmms 謂詞 result IS NULL OR result <> 'FAIL' 對它照常放行）")

    # ── 8. 名冊 ─────────────────────────────────────────────────────────────
    r = roster_eids()
    ck(len(r) == len(set(r)) and r == sorted(r), "8.1 名冊去重排序")
    ck(len(r) >= len(EQMT_CATALOG), "8.2 至少涵蓋 EQMT_CATALOG 全量（名冊台數會變、不寫死）")
    for parent, kids in PARENT_EXPANSION.items():
        ck(parent in r, f"8.3 母機 {parent} 在名冊（cmms 的維修單開在它身上）")
        for k in kids:
            ck(k in r, f"8.4 子工位 {k} 分得開（不捲到母機）")
    ck("EID-27617" not in r, "8.5 EID-27617 永不入列（不在名冊、不在展開表）")
    r2 = roster_eids(catalog=[("za", "x", "EID-A")], parents={"EID-P": ("EID-C",)})
    ck(r2 == ["EID-A", "EID-C", "EID-P"], "8.6 純函式：可注入名冊")

    # ── 9. 錯誤碼處置表 ─────────────────────────────────────────────────────
    ck(classify_response(200)["exit"] == EXIT_OK, "9.1 200 ⇒ exit 0")
    c400 = classify_response(400, '{"detail":"bad schema_id"}')
    ck(c400["exit"] == EXIT_HTTP_400 and not c400["ok"] and "schema_id" in c400["kind"],
       "9.2 400 ⇒ 具名、非零 exit")
    ck(classify_response(401)["exit"] == EXIT_HTTP_401, "9.3 401 ⇒ exit 4")
    ck(classify_response(503)["exit"] == EXIT_HTTP_503, "9.4 503（對方未設 token）⇒ exit 9")
    ck(classify_response(422)["exit"] == EXIT_HTTP_422, "9.5 422 ⇒ exit 6")
    ck(classify_response(500)["exit"] == EXIT_HTTP_OTHER, "9.6 其他碼 ⇒ exit 7")
    ck(classify_response(None, reason="timeout")["exit"] == EXIT_HTTP_OTHER
       and classify_response(None, reason="timeout")["kind"] == "timeout",
       "9.7 逾時 ⇒ 非零且具名")
    ck(classify_response(None, reason="unreachable")["ok"] is False, "9.8 連不上 ⇒ 非 ok")
    codes = {classify_response(s)["exit"] for s in (400, 401, 422, 503)}
    ck(len(codes) == 4 and 0 not in codes, "9.9 ★ 四個錯誤碼彼此可分辨、且全部非零")
    ck("讀取票" in classify_response(401)["note"], "9.10 401 的說明指向『別拿錯票』")
    _retry_calls, _retry_log = [], []
    def _fake_sub_units_sender(_base, _token, candidate, _log):
        _retry_calls.append(candidate)
        return ((400, '{"detail":"unknown item key"}', None)
                if len(_retry_calls) == 1 else (200, '{"accepted":1}', None))
    _fallback, _ = build_payload(now, 30, _su_snap, _su_roster, set(_su_roster))
    _rs, _rb, _rr, _rejected = post_with_sub_units_fallback(
        "https://cmms.example", "test-token", _su_on, _fallback, _retry_log.append,
        sender=_fake_sub_units_sender)
    ck((_rs, _rr, _rejected) == (200, None, 400)
       and len(_retry_calls) == 2
       and payload_has_sub_units(_retry_calls[0])
       and not payload_has_sub_units(_retry_calls[1])
       and _retry_log == ["[lastprod] sub_units rejected by cmms (http_400) -> resent without sub_units"],
       "9.10b ★ 400 on the optional field immediately retries the same snapshot without it")
    import ssl as _ssl
    _real_urlopen = urllib.request.urlopen
    def _tls_logs(exc):
        def _raise(*_a, **_k):
            raise exc
        urllib.request.urlopen = _raise
        _lg = []
        try:
            _res = post_payload("https://cmms.example", "test-token", {"items": []}, _lg.append, retries=0)
        finally:
            urllib.request.urlopen = _real_urlopen
        return _res, " ".join(_lg)
    _tres, _tlog = _tls_logs(urllib.error.URLError(_ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier")))
    ck(_tres == (None, None, "unreachable") and "-> unreachable tls_verify=fail" in _tlog
       and "test-token" not in _tlog,
       "9.10c SSLCertVerificationError => log carries tls_verify=fail, reason stays unreachable")
    _cres, _clog = _tls_logs(urllib.error.URLError(ConnectionRefusedError(10061, "refused")))
    ck(_cres == (None, None, "unreachable") and "tls_verify" not in _clog and "-> unreachable" in _clog,
       "9.10d plain connection failure => no tls_verify marker")
    _reject_once = note_sub_units_rejected({}, now)
    _reject_twice = note_sub_units_rejected(_reject_once, now + timedelta(minutes=10))
    _reject_reset = clear_sub_units_rejection_streak(_reject_twice)
    ck(_reject_once["sub_units_rejected_at"] == _z(now)
       and _reject_once["sub_units_rejection_streak"] == 1
       and sub_units_auto_disabled(_reject_once) is False
       and sub_units_auto_disabled(_reject_twice) is True
       and _reject_reset["sub_units_rejection_streak"] == 0
       and sub_units_auto_disabled(_reject_reset) is False,
       "9.10c rejection metadata is additive; two consecutive 400/422 rejections safely disable the feature until reset")
    #  ★ 9.11–9.16＝回答 cmms「我方回應多一鍵會不會炸你們」那個 ask 的**機器版**。
    #    承重語意＝**回應的形狀不得影響成敗判定**（exit code 與狀態檔寫入）。
    ck(classify_response(200, '{"accepted":115,"新鍵":"x"}')["exit"] == EXIT_OK
       and classify_response(200, "totally not json")["exit"] == EXIT_OK
       and classify_response(200, None)["exit"] == EXIT_OK,
       "9.11 ★ 200 的 exit 完全不看 body（多鍵／非 JSON／無 body 一律 exit 0）")
    s_extra = summarize_ingest_response(
        '{"accepted":115,"unknown_skipped":0,"unmatched_assets":[],"gate_dropped":3}')
    ck("accepted=115" in s_extra and "gate_dropped" not in s_extra,
       "9.12 ★ 回應多一鍵＝只是沒被讀到（不炸、也不會混進摘要）")
    ck("None" in summarize_ingest_response('{"accepted":1}'),
       "9.13 少鍵 ⇒ 印 None，不丟例外")
    ck("不是 JSON" in summarize_ingest_response("<html>502</html>"),
       "9.14 非 JSON 回應 ⇒ 具名一句話")
    ck(all("不解讀" in summarize_ingest_response(x) for x in ('[1,2]', '"ok"', '123')),
       "9.15 ★ 合法 JSON 但頂層不是 object ⇒ 吞成一句話"
       "（2026-08-17 前這一格會 AttributeError ⇒ exit 1 且該輪狀態檔不寫）")
    ck(summarize_ingest_response(None) and summarize_ingest_response(""),
       "9.16 空 body 不炸")

    # ── 10. token 解析（值永不入 log；解析器容忍 markdown 包裝）──────────────
    T = "abc123DEF456ghi789"
    ck(extract_token(f"CMMS_Board_INGEST_TOKEN={T}") == T, "10.1 KEY=value")
    ck(extract_token(f"CMMS_Board_INGEST_TOKEN: {T}") == T, "10.2 KEY: value")
    ck(extract_token(f"- `CMMS_Board_INGEST_TOKEN` = `{T}`") == T, "10.3 backtick 包裝")
    ck(extract_token(f"**CMMS_Board_INGEST_TOKEN**：{T}") == T, "10.4 粗體＋全形冒號")
    ck(extract_token(f"## CMMS_Board_INGEST_TOKEN\n\n`{T}`\n") == T, "10.5 值在下一行")
    ck(extract_token("token 值在 `.secrets\\cmms.md`（`CMMS_Board_INGEST_TOKEN`）") is None,
       "10.6 ★ 只提到名字的散文 ⇒ 撈不到（不亂猜）")
    ck(extract_token("") is None and extract_token(None) is None, "10.7 空輸入不炸")
    ck(extract_token("OTHER_TOKEN=xyz") is None, "10.8 別的 key 不誤撈")
    ck(_plausible_token("cmms.md", TOKEN_ENV) is False, "10.9 檔名不當 token")
    ck(_plausible_token("short", TOKEN_ENV) is False, "10.10 太短不當 token")
    env0 = {TOKEN_ENV: T}
    ck(resolve_ingest_token(env=env0) == (T, "env"), "10.11 env 優先")
    ck(resolve_ingest_token(secrets_path=os.path.join(REPO, "no-such.md"),
                            env={}) == (None, "missing"),
       "10.12 ★ 檔案不存在 ⇒ missing（呼叫端零網路嘗試）")
    ck(resolve_ingest_token(env={READ_TOKEN_ENV: "readticketreadticket",
                                SECRETS_FILE_ENV: os.path.join(REPO, "no-such.md")})
       == (None, "missing"),
       "10.13 ★ 檔案不存在時絕不 fallback 到讀取票")
    #   ★ 上一條走的是「檔案打不開」那條早退路徑；這一條讓檔案**真的存在但沒有 token**
    #     ——兩條路徑都要釘，否則「fallback 到讀取票」的變異只會在其中一條被抓到。
    ck(resolve_ingest_token(secrets_path=os.path.join(REPO, "requirements.txt"),
                            env={READ_TOKEN_ENV: "readticketreadticket"}) == (None, "missing"),
       "10.13b ★ 檔案有但無 token ⇒ 仍 missing（絕不 fallback 到讀取票）")
    ck(resolve_base_url(env={}) is None, "10.14 缺 base ⇒ None")
    ck(resolve_base_url(env={BASE_ENV: "https://x.example.com/"}) == "https://x.example.com",
       "10.15 base 去尾斜線")

    # ── 11. 牆鐘預算（計時器會睡，牆鐘不會）─────────────────────────────────
    b = _Budget(10)
    ck(b.elapsed() >= 0 and b.left() <= 10, "11.1 預算初始值")
    ck(_Budget(0).exhausted(), "11.2 零預算立刻耗盡")

    # ★ 模擬 Modern Standby：**monotonic 停表、牆鐘照走**（注入時鐘＝打真正的 elapsed()，
    #   不是覆寫一支假的 elapsed 來自我證明——後者是這個 repo 抓過好幾次的假信心型）。
    wall_box = [D(2026, 8, 14, 9, 2, 0)]
    b2 = _Budget(60, mono=lambda: 100.0, wall=lambda: wall_box[0])
    ck(not b2.exhausted(), "11.3 睡前：預算還在")
    wall_box[0] = D(2026, 8, 14, 11, 39, 57)      # 醒來：牆鐘走了 2h38m、monotonic 沒動
    ck(b2.elapsed() > 9000 and b2.exhausted(),
       "11.4 ★ 計時器凍結時仍以牆鐘判定逾時（SPEC-WARM-CACHE §8 的 9,467 秒那件）")
    mono_box = [100.0]
    b3 = _Budget(60, mono=lambda: mono_box[0], wall=lambda: D(2026, 8, 14, 9, 2, 0))
    mono_box[0] = 900.0                          # 反向：牆鐘不動、monotonic 走
    ck(b3.elapsed() == 800.0 and b3.exhausted(), "11.5 兩個時鐘取大（反向亦成立）")

    # ── 12. 端到端（假 cursor／假網路，仍是零 I/O）──────────────────────────
    class _FakeCur:
        def __init__(self, rows_by_cls, eids):
            self.rows_by_cls, self.eids, self._r = rows_by_cls, eids, []
            self.n_exec = 0

        def execute(self, sql, *params):
            self.n_exec += 1
            if "COL_LENGTH" in sql:
                self._r = [tuple(1 for _ in GUARD_KEYS)]
            elif sql == build_known_eids_sql():
                # 比對函式本身的輸出、不比視圖名子字串：公開樹的視圖識別字被置換後，
                # 設備端掃描 SQL 的視圖名會包含主檔視圖名的子字串 ⇒ 子字串比對會把掃描誤判成主檔查詢。
                self._r = [(e,) for e in self.eids]
            else:
                self._r = self.rows_by_cls.get(params[0], [])

        def fetchone(self):
            return self._r[0]

        def fetchall(self):
            return list(self._r)

    tt = D(2026, 8, 15, 13, 5, 0)                  # 台北牆鐘
    ts = D(2026, 8, 15, 12, 58, 0)
    cur = _FakeCur({"segCalibrationTest": [("EID-24742", tt, ts, "Production", "PASS"),
                                                ("EID-26841", tt, tt, None, None)]},
                   ["EID-24742", "EID-26841"])
    g = fetch_column_guard(cur)
    ck(all(g.values()), "12.1 假 guard 全 1")
    ck(fetch_known_eids(cur, g) == {"EID-24742", "EID-26841"}, "12.2 主檔 EID 集合")
    ck(fetch_known_eids(cur, dict(g, view_equipment=False)) is None,
       "12.3 主檔缺席 ⇒ None（降級）")
    acc, prod_acc, scanned, nrows = fetch_last_ends(
        cur, 30, _Budget(60), lambda s: None,
        stations=["segCalibrationTest", "segWireBonding"])
    ck(scanned == 2 and nrows == 2, "12.4 逐站掃描累加")
    ck(acc["EID-24742"] == (D(2026, 8, 15, 5, 5, 0), D(2026, 8, 15, 4, 58, 0),
                            "Production", "PASS"),
       "12.4b ★ 時區轉換在 I/O 邊界一次做完、end/start 走同一支（台北 13:05/12:58 → UTC）")
    ck(acc["EID-26841"] == (D(2026, 8, 15, 5, 5, 0), D(2026, 8, 15, 5, 5, 0), None, None),
       "12.4c 零工期段＋DB null designation/result 原樣通過 I/O 邊界")
    ck(prod_acc == {"EID-24742": acc["EID-24742"]},
       "12.4c2 ★ production-grade argmax 只收 closed Production/Baseline set")
    cur3 = _FakeCur({"segCalibrationTest": [("EID-24742", tt, ts)]},
                    ["EID-24742"])
    acc3, prod3, _, _ = fetch_last_ends(cur3, 30, _Budget(60), lambda s: None,
                                        stations=["segCalibrationTest"],
                                        with_designation=False, with_result=False)
    ck(acc3["EID-24742"] == (D(2026, 8, 15, 5, 5, 0), D(2026, 8, 15, 4, 58, 0), None, None),
       "12.4d ★ 雙降級形：三欄列不炸、designation/result=None、核心事實照推")
    ck(prod3 == {}, "12.4d2 ★ 無 designation 降級 ⇒ prod_* 沒有可宣告的事實")
    cur4 = _FakeCur({"segCalibrationTest": [("EID-24742", tt, ts, "FAIL")]},
                    ["EID-24742"])
    acc4, _prod4, _, _ = fetch_last_ends(cur4, 30, _Budget(60), lambda s: None,
                                         stations=["segCalibrationTest"],
                                         with_designation=False, with_result=True)
    ck(acc4["EID-24742"] == (D(2026, 8, 15, 5, 5, 0), D(2026, 8, 15, 4, 58, 0), None, "FAIL"),
       "12.4e ★ 單降級形（缺 designation、有 result）：索引依旗標算、r[3] 落到 result 不錯位")
    acc2, prod2, sc2, _ = fetch_last_ends(cur, 30, _Budget(0), lambda s: None,
                                          stations=["segCalibrationTest"])
    ck(sc2 == 0 and acc2 == {} and prod2 == {}, "12.5 ★ 預算已耗盡 ⇒ 一站都不掃（不硬幹）")

    # ── 13. 訊息面（log 不得含 token）───────────────────────────────────────
    ck("Authorization" not in ALL_SQL()[0], "13.1 SQL 與 header 不混")
    src = open(os.path.abspath(__file__), "r", encoding="utf-8").read()
    #  ⚠ 這幾條的字面值一律用**串接**組出來——直接寫在斷言裡會讓斷言自己命中原始碼
    #    （最壞的假信心：無論碼怎麼改都綠）。
    hdr = '"Authorization": f"' + "Bearer {token}\""
    ck(src.count(hdr) == 1, "13.2 Bearer header 只組在一個地方（POST）")
    ck("值不入 log" in src, "13.3 log 紀律寫在碼裡")
    ck('log(f"[lastprod] token source' in src, "13.4 只 log 來源不 log 值")
    ck(("print(" + "token") not in src, "13.5 負斷言：token 不 print")
    ck(("log(" + "token") not in src, "13.6 負斷言：token 不落 log")
    ck(("Bearer " + "{token}") in src
       and ("?" + "token=") not in src and ("&" + "token=") not in src,
       "13.7 token 只走 header，永不進 URL")
    #  ⚠ 字面值一律串接組出來（直接寫會讓斷言自己命中原始碼＝最壞的假信心）。
    ck(src.count("get(" + "'accepted')") == 1,
       "13.8 ★ 負斷言：回應解讀只有一個地方（`summarize_ingest_response`）——"
       "誰在 run() 裡重新內聯 json.loads 就會紅")

    # ── 14. Q-G：`compose_round` ≡ `run()`（同一份原子段；I/O 全注入：零檔案、零網路、零 MES）──
    #   `run()` 的 `_connect`／`utc_now`／token／base／POST／logger／狀態讀寫全部換成記憶體替身，
    #   跑真的 `run()`；再把**同一份原子段**直接餵 `plan_round` → `compose_round`，逐位元比對
    #   POST 出去的 payload／fallback、寫下的狀態、log 行（含順序）與 exit code。
    import copy as _cp14
    _G14 = globals()
    _T14 = D(2026, 9, 29, 11, 0, 0)                    # 台北牆鐘（列的時刻形）
    _classes14 = list(lastprod_scan_classes())
    _roster14 = list(roster_eids())
    _extra14 = ["EID-99999", "EID-38041", "EID-38072"]  # 名冊外／rename／drop 各一
    _desigs14 = ("Production", "Engineering (R&D)", None, "Engineering (Baseline)")
    _results14 = ("PASS", "FAIL", None)
    _rows14 = {}
    for _i, _c in enumerate(_classes14):
        _lst = []
        for _k in range(4):
            _e = (_roster14 + _extra14)[(_i * 3 + _k) % (len(_roster14) + len(_extra14))]
            _end = _T14 - timedelta(hours=_i, minutes=7 * _k)
            _start = _end if _k == 3 else _end - timedelta(minutes=10 + _k)   # 含零工期段
            _lst.append((_e, _end, _start, _desigs14[(_i + _k) % 4], _results14[(_i + _k) % 3]))
        _rows14[_c] = _lst
    _nest14 = {}
    for _j, (_c, _v) in enumerate(nest_query_pairs()):
        _lst = []
        for _e, _spec in sorted(_wcap.NEST_EAV_UNITS.items()):
            if _spec["var"] != _v:
                continue
            for _nn in tuple(_spec["nests"])[:3] + (0, 99):
                _end = _T14 - timedelta(minutes=13 * _nn + _j)
                _lst.append((_e, _end, _end - timedelta(minutes=5),
                             _desigs14[_nn % 4], _nn))
        _nest14[(_c, _v)] = _lst
    _sl14 = {}
    for _j, _c in enumerate(STATION_LOT_PROXY):
        _sl14[_c] = [(_T14 - timedelta(minutes=30 * _m + _j), _T14 - timedelta(minutes=30 * _m + _j + 4),
                      _desigs14[(_m + _j) % 4]) for _m in range(3)] if _j != 1 else []
    _known14 = set(_roster14[:-2]) | {"EID-38106"}

    def _proj14(rows, wd, board):
        """5 欄原形 → 依 SQL 變體投影（eid, end, start[, desig][, result]）。"""
        out = []
        for r in rows:
            t = list(r[:3])
            if wd:
                t.append(r[3])
            if board:
                t.append(r[4])
            out.append(tuple(t))
        return out

    class _Cur14:
        def __init__(self, guard_row, fail=()):
            self.guard_row, self.fail, self._r = tuple(guard_row), set(fail), []
            self.variants = {build_lastprod_sql(wd, board)[0]: (wd, board)
                             for wd in (True, False) for board in (True, False)}
            self.nest_sql = build_nest_sql(with_designation=True)[0]
            self.sl_sql = build_station_lot_sql()[0]

        def execute(self, sql, *params):
            if sql == build_column_guard_sql():
                self._r = [self.guard_row]
            elif sql == build_known_eids_sql():
                self._r = [(e,) for e in sorted(_known14)]
            elif sql in self.variants:
                wd, board = self.variants[sql]
                self._r = _proj14(_rows14.get(params[0], []), wd, board)
            elif sql == self.nest_sql:
                if "nest" in self.fail:
                    raise RuntimeError("injected nest failure")
                self._r = list(_nest14.get((params[1], params[0]), []))
            elif sql == self.sl_sql:
                if "sl" in self.fail:
                    raise RuntimeError("injected station_lot failure")
                self._r = list(_sl14.get(params[0], []))
            else:
                raise AssertionError("unexpected SQL in selftest 14")

        def fetchone(self):
            return self._r[0]

        def fetchall(self):
            return list(self._r)

    class _Conn14:
        def close(self):
            pass

    class _Budget14:
        """前 `k` 次 `exhausted()` 為 False、之後恆 True（模擬預算燒光＝partial 輪）。"""
        def __init__(self, k):
            self.k, self.n = k, 0

        def exhausted(self):
            self.n += 1
            return self.n > self.k

        def elapsed(self):
            return 0.0

    def _drive14(raw_state, now_utc, guard_row, sub_on, fail=(), budget_k=None, full=False):
        cap = {"posts": [], "state": None, "logs": []}
        keys = ("_connect", "utc_now", "resolve_base_url", "resolve_ingest_token",
                "post_with_sub_units_fallback", "make_logger", "_read_raw_state",
                "_write_state", "sub_units_feature_enabled", "_Budget")
        saved = {k: _G14[k] for k in keys}
        cur14 = _Cur14(guard_row, fail)

        def _post14(_b, _t, pl, fb, _log):
            cap["posts"].append((_cp14.deepcopy(pl), _cp14.deepcopy(fb)))
            return 200, '{"ok": true}', None, None
        try:
            _G14["_connect"] = lambda _timeout: (_Conn14(), cur14)
            _G14["utc_now"] = lambda: now_utc
            _G14["resolve_base_url"] = lambda env=None: "http://127.0.0.1:9"
            _G14["resolve_ingest_token"] = lambda secrets_path=None, env=None: (
                "selftest14-" + "q" * 16, "selftest")
            _G14["post_with_sub_units_fallback"] = _post14
            _G14["make_logger"] = lambda repo=None, echo=True: cap["logs"].append
            _G14["_read_raw_state"] = lambda _p: _cp14.deepcopy(raw_state)
            _G14["_write_state"] = lambda _p, doc, _log: cap.__setitem__(
                "state", json.loads(json.dumps(doc)))
            _G14["sub_units_feature_enabled"] = lambda env=None: sub_on
            _G14["_Budget"] = ((lambda _s: _Budget14(10 ** 6)) if budget_k is None
                               else (lambda _s: _Budget14(budget_k)))
            argv = ["--lookback-days", "30", "--refresh-days", "2",
                    "--scope", SCOPE_ROSTER_OBSERVED] + (["--full"] if full else [])
            rc = run(argv)
        finally:
            _G14.update(saved)
        return rc, cap

    def _compose14(raw_state, now_utc, guard_row, sub_on, fail=(), budget_k=None, full=False):
        g = dict(zip(GUARD_KEYS, guard_row))
        wd, board = bool(g["matDesignation"]), bool(g["stpTestResult"])
        k = 10 ** 6 if budget_k is None else budget_k
        plan = plan_round(_cp14.deepcopy(raw_state), now_utc, 30, 2, force_full=full,
                          scope=SCOPE_ROSTER_OBSERVED)
        rows = [(c, _proj14(_rows14.get(c, []), wd, board)) for c in _classes14][:k]
        k -= len(rows)
        if "nest" in fail:
            nest = None
        elif not wd:
            nest = []
        else:
            pairs = list(nest_query_pairs())
            nest = [((c, v), list(_nest14.get((c, v), []))) for c, v in pairs][:max(0, k)]
            k -= len(nest)
        if "sl" in fail:
            sl, sl_err = None, "RuntimeError: injected station_lot failure"
        else:
            sl, sl_err = [(c, list(_sl14.get(c, []))) for c in STATION_LOT_PROXY][:max(0, k)], None
        rnd = compose_round(plan, now_utc, 30, SCOPE_ROSTER_OBSERVED, rows,
                            nest_rows_by_pair=nest, station_lot_rows_by_cls=sl,
                            station_lot_err=sl_err, known=set(_known14),
                            with_designation=wd, with_result=board, sub_units_on=sub_on)
        return plan, rnd

    def _is_subseq14(small, big):
        it = iter(big)
        return all(any(x == y for y in it) for x in small)

    def _js14(x):
        return json.dumps(x, ensure_ascii=False, default=str)

    _full_g = tuple(1 for _ in GUARD_KEYS)
    _nodesig_g = tuple(0 if k == "matDesignation" else 1 for k in GUARD_KEYS)
    _NOW14 = D(2026, 9, 29, 3, 0, 0)                  # UTC naive（＝台北 11:00）
    _cases14 = []
    _prev_state14 = None
    for _name, _kw in (("full-window/no-state/sub-off", dict(raw_state=None, sub_on=False)),
                       ("incremental/with-state/sub-on", dict(raw_state="PREV", sub_on=True)),
                       ("partial/budget", dict(raw_state=None, sub_on=True, budget_k=7)),
                       ("degraded/no-designation", dict(raw_state=None, sub_on=True,
                                                        guard_row=_nodesig_g)),
                       ("nest-io-fail", dict(raw_state="PREV", sub_on=True, fail=("nest",))),
                       ("station-lot-io-fail", dict(raw_state="PREV", sub_on=False, fail=("sl",))),
                       ("zero-stations", dict(raw_state=None, sub_on=False, budget_k=0)),
                       ("forced-full/with-state", dict(raw_state="PREV", sub_on=True, full=True))):
        _kw = dict(_kw)
        _now = _NOW14 + (timedelta(minutes=20) if _kw.get("raw_state") == "PREV" else timedelta(0))
        if _kw.get("raw_state") == "PREV":
            _kw["raw_state"] = _prev_state14
        _kw.setdefault("guard_row", _full_g)
        _rc, _cap = _drive14(now_utc=_now, **_kw)
        _plan, _rnd = _compose14(now_utc=_now, **_kw)
        if _name.startswith("full-window"):
            _prev_state14 = _cap["state"]
        _want_rc = (_rnd["exit"] if _rnd["exit"] is not None
                    else (EXIT_PARTIAL if _rnd["partial"] else EXIT_OK))
        ck(_rc == _want_rc, f"14.{_name} exit code run()={_rc} compose={_want_rc}")
        if _rnd["exit"] is None:
            ck(len(_cap["posts"]) == 1
               and _js14(_cap["posts"][0][0]) == _js14(_rnd["payload"])
               and _js14(_cap["posts"][0][1]) == _js14(_rnd["fallback_payload"]),
               f"14.{_name} ★ POST payload／fallback 逐位元相同（含鍵序）")
            _ws = _rnd["new_state"]
            if _ws is not None and payload_has_sub_units(_rnd["payload"]):
                _ws = clear_sub_units_rejection_streak(_ws)
            _ws = None if _ws is None else json.loads(json.dumps(_ws))
            ck(_js14(_cap["state"]) == _js14(_ws), f"14.{_name} ★ 寫下的狀態逐位元相同")
        else:
            ck(not _cap["posts"] and _cap["state"] is None, f"14.{_name} 早退：零 POST、零寫狀態")
        ck(_is_subseq14(_plan["logs"] + _rnd["logs"], _cap["logs"]),
           f"14.{_name} plan/compose 的 log 行依序出現在 run() 的 log 裡")
        _cases14.append(_name)
    ck(_prev_state14 is not None and _prev_state14.get("schema") == STATE_SCHEMA,
       "14.x 整窗輪寫出的狀態可當下一輪輸入（incremental 案例真的吃到舊狀態）")
    # 純度：同輸入兩次 ⇒ 同輸出；輸入不被改寫。
    _p1, _r1 = _compose14(raw_state=_prev_state14, now_utc=_NOW14 + timedelta(minutes=20),
                          guard_row=_full_g, sub_on=True)
    _snap_prev = _js14(_prev_state14)
    _p2, _r2 = _compose14(raw_state=_prev_state14, now_utc=_NOW14 + timedelta(minutes=20),
                          guard_row=_full_g, sub_on=True)
    ck(_js14({k: v for k, v in _r1.items()}) == _js14({k: v for k, v in _r2.items()})
       and _js14(_prev_state14) == _snap_prev,
       "14.y compose_round 確定性（同輸入同輸出）且不改寫輸入狀態")
    ck(_r1["payload"]["full_window_rebuild"] is False and _r1["new_state"] is not None,
       "14.z incremental 輪不宣告整窗重建（覆蓋宣告規則照舊走 rebuild_claim）")

    # ── 15. MES 連線失敗 ⇒ 非 0 退出碼（WP-X1）──────────────────────────────────
    _codes15 = [EXIT_OK, EXIT_UNEXPECTED, EXIT_NOT_CONFIGURED, EXIT_HTTP_400, EXIT_HTTP_401,
                EXIT_PARTIAL, EXIT_HTTP_422, EXIT_HTTP_OTHER, EXIT_MES, EXIT_HTTP_503]
    ck(len(set(_codes15)) == len(_codes15), "15.1 退出碼表各值互異")
    ck(EXIT_MES == 8 and EXIT_MES != EXIT_OK, "15.2 EXIT_MES＝8（非 0）")
    _s15 = {k: _G14[k] for k in ("_connect", "utc_now", "resolve_base_url",
                                 "resolve_ingest_token", "make_logger", "_read_raw_state",
                                 "_write_state")}
    _cap15 = {"logs": [], "state": None}

    def _boom15(_t):
        raise OSError("selftest: connect refused")
    try:
        _G14["_connect"] = _boom15
        _G14["utc_now"] = lambda: datetime(2026, 9, 29, 3, 0, 0)
        _G14["resolve_base_url"] = lambda env=None: "http://127.0.0.1:9"
        _G14["resolve_ingest_token"] = lambda secrets_path=None, env=None: (
            "selftest15-" + "q" * 16, "selftest")
        _G14["make_logger"] = lambda repo=None, echo=True: _cap15["logs"].append
        _G14["_read_raw_state"] = lambda _p: None
        _G14["_write_state"] = lambda _p, doc, _log: _cap15.__setitem__("state", doc)
        _rc15 = run(["--lookback-days", "30", "--refresh-days", "2"])
        _rc15d = run(["--dry-run", "--lookback-days", "30", "--refresh-days", "2"])
    finally:
        _G14.update(_s15)
    ck(_rc15 == EXIT_MES, "15.3 MES 連線失敗（整輪沒讀到資料）⇒ run() 回 EXIT_MES")
    ck(_rc15d == EXIT_MES, "15.4 dry-run 同樣回 EXIT_MES")
    ck(_cap15["state"] is None, "15.5 MES 連線失敗不寫狀態檔")
    ck(any("MES connect failed" in _l for _l in _cap15["logs"]), "15.6 log 有 MES connect failed")

    # ── 16. 退出碼狀態檔（WP-X1b）：真 main_recorded() → _state/task_exit/<任務>.json ──────
    #   寫檔只落系統暫存目錄（BOARD_STATE_DIR 暫時改向）；真 `_state/` 一個位元不碰。
    try:
        import core_task_exit as _wte16
    except ImportError:
        _wte16 = None
        print("  16 skipped: core_task_exit not present")
    if _wte16 is not None:
        import tempfile as _tf16
        import shutil as _sh16
        import board_atomic as _wat16
        ck(TASK_NAME == "TendlineBoardLastProdPush" and set(EXIT_NAMES) == set(_codes15)
           and EXIT_NAMES[EXIT_MES] == "EXIT_MES", "16.0 任務名＝安裝腳本的 $taskName；名稱表涵蓋全部退出碼")
        _d16 = _tf16.mkdtemp(prefix="wrlp16-")
        _sv16 = (sys.argv, os.environ.get(_wp.ENV_STATE_DIR), _G14["run"], _G14["make_logger"],
                 _wat16.atomic_write_json)
        _res16 = {}

        def _main16(rc_or_exc, argv=None):
            def _r(*_a, **_k):
                if isinstance(rc_or_exc, BaseException):
                    raise rc_or_exc
                return rc_or_exc
            _G14["run"] = _r
            sys.argv = ["core_lastprod_push.py"] + list(argv or [])
            try:
                main_recorded()
                return "no-exit"
            except SystemExit as _e:
                return _e.code
        try:
            os.environ[_wp.ENV_STATE_DIR] = _d16
            _G14["make_logger"] = lambda repo=None, echo=True: (lambda _m: None)
            _res16["first"] = _wte16.read_status(TASK_NAME)
            _res16["ok"] = (_main16(EXIT_OK), _wte16.read_status(TASK_NAME))
            _res16["mes"] = (_main16(EXIT_MES), _wte16.read_status(TASK_NAME))
            _res16["unexp"] = (_main16(RuntimeError("selftest16")), _wte16.read_status(TASK_NAME))
            _res16["dry"] = (_main16(EXIT_MES, ["--dry-run"]), _wte16.read_status(TASK_NAME))

            def _wfail(*_a, **_k):
                raise PermissionError(13, "selftest16 denied")
            _wat16.atomic_write_json = _wfail
            _res16["wfail"] = (_main16(EXIT_MES), _wte16.read_status(TASK_NAME))
            _wat16.atomic_write_json = _sv16[4]
            _res16["path"] = _wte16.status_path(TASK_NAME)
        finally:
            sys.argv = _sv16[0]
            if _sv16[1] is None:
                os.environ.pop(_wp.ENV_STATE_DIR, None)
            else:
                os.environ[_wp.ENV_STATE_DIR] = _sv16[1]
            _G14["run"], _G14["make_logger"] = _sv16[2], _sv16[3]
            _wat16.atomic_write_json = _sv16[4]
            _sh16.rmtree(_d16, ignore_errors=True)
        ck(_res16["first"] == (None, "no record yet"), "16.1 首輪無狀態檔＝正常狀態（不報錯）")
        ck(os.path.normcase(_res16["path"]).startswith(os.path.normcase(_d16))
           and _res16["path"].endswith(os.path.join("task_exit", "TendlineBoardLastProdPush.json")),
           "16.2 路徑＝<state>/task_exit/TendlineBoardLastProdPush.json（走 core_paths）")
        _rc, (_doc, _w) = _res16["ok"]
        ck(_rc == EXIT_OK and _doc and _doc["exit_code"] == 0 and _doc["exit_name"] == "EXIT_OK"
           and set(_doc) == {"task", "exit_code", "exit_name", "started_at", "finished_at",
                             "host_pid"}, f"16.3 成功：退出 0、狀態檔 0/EXIT_OK 六鍵 {_doc}")
        _rc, (_doc, _w) = _res16["mes"]
        ck(_rc == EXIT_MES and _doc["exit_code"] == 8 and _doc["exit_name"] == "EXIT_MES",
           f"16.4 MES 失敗：退出 8、狀態檔 8/EXIT_MES {_doc}")
        _rc, (_doc, _w) = _res16["unexp"]
        ck(_rc == EXIT_UNEXPECTED and _doc["exit_code"] == 1
           and _doc["exit_name"] == "EXIT_UNEXPECTED",
           f"16.5 run() 拋未捕捉例外：main 收成 1、狀態檔 1/EXIT_UNEXPECTED {_doc}")
        _rc, (_doc, _w) = _res16["dry"]
        ck(_rc == EXIT_MES and _doc["exit_name"] == "EXIT_UNEXPECTED",
           "16.6 --dry-run 不寫狀態檔（保留上一筆）、退出碼照舊")
        _rc, (_doc, _w) = _res16["wfail"]
        ck(_rc == EXIT_MES and _doc["exit_name"] == "EXIT_UNEXPECTED",
           "16.7 寫檔失敗：退出碼仍 8、不拋、舊紀錄不被毀")

    print(f"selftest PASS ({n[0]} checks)")
    return 0


# =============================================================================
def main():
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except Exception as e:                          # noqa: BLE001
        try:
            make_logger()(f"[lastprod] UNEXPECTED {type(e).__name__}: {e}")
        except Exception:
            pass
        raise SystemExit(EXIT_UNEXPECTED)


def main_recorded():
    """排程入口（WP-X1b）：照跑 `main()`，結束時把真退出碼寫進 `_state/task_exit/<任務>.json`。

    import 放在函式內且失敗即退回裸 `main()`：狀態檔是診斷加分項，絕不能擋推送本身
    （公開樹不帶 `core_task_exit` 時也照跑）。寫檔失敗不改退出碼、不拋（core_task_exit 保證）。"""
    try:
        import core_task_exit as _wte
    except Exception:                               # noqa: BLE001
        return main()
    return _wte.run_recorded(TASK_NAME, main, names=EXIT_NAMES)


if __name__ == "__main__":
    main_recorded()
