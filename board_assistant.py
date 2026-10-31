# -*- coding: utf-8 -*-
"""
board_assistant.py — Hermes sidebar 對話持久化 store（stdlib sqlite3 同步版）
================================================================================

語意母本＝cmms `domain/assistant/{models,service}.py`（ADR-020，跨 repo 讀合法；抄語意
不抄碼）。差異：Tendline Board 前門**無登入**，擁有權＝瀏覽器匿名 id `aid`（非 user_account FK）；
且 Board 用**標準庫 sqlite3 同步版**（無 SQLAlchemy、無 async）—— FastAPI sync route 跑於
threadpool，故每次操作**開新連線**、不共享 connection。

擁有權模型（domain 層強制，不倚賴 route 守門）：
  每個讀寫都以 `aid` 過濾 / 驗證；非本 aid 一律 None / 空清單 / 拒（AssistantError）。
  匿名 id 只是「這個瀏覽器的」隔離鍵，不是身分認證 —— 但足以避免同機不同分頁互看，
  且**絕不把 per-person 身分落 DB**（§24 D1/D2；本 store 只存 aid+對話內容）。

惰性建會話：「新對話」按鈕不落 DB；首則訊息（add_turn cid=None）才建。
add_turn 同 transaction 寫 user+assistant 兩則並推進 updated_at（切換列「最近活動」排序）。
開啟中對話每 aid 上限 MAX_OPEN（防無限累積）。

U5 兩段式送出（2026-07-30；語意母本＝cmms 經驗包 U5）
--------------------------------------------------------------------------------
痛點：慢問答（30–120s，重型分析可到數分鐘）走「一條 route 內同步打 gateway、成功才落庫」
＝使用者轉頁/關分頁整輪蒸發，且新會話標籤要等回答才出現。
修法＝把一輪拆成兩段，DB 是唯一交會點：

    begin_turn ──► user 列（真內容）＋ assistant 佔位列（content=''、status='pending'）
       │                                    ▲                    │
       │  phase 1（route 內、快、瞬間回應）  │                    │ phase 2（背景 worker）
       ▼                                    │                    ▼
    前端拿到 turn_id 開始輪詢 ────────────────┘        complete_turn（content=reply、status=NULL）
                                                     或 fail_turn（status='failed'+error_kind）
                                                            │
                                            retry_turn ◄────┘（failed → 重置 pending、原地重跑）

狀態欄語意（`message.status`，只有 assistant 列用；user 列恆 NULL）：
  NULL       = done（**舊資料同義**＝U5 之前寫的列一位元不改、照舊渲染）
  'pending'  = 已受理、答案還在跑（可離開頁面，回來還在）
  'failed'   = 這一輪沒答成，`error_kind` 說明為什麼（gw_down/ai_timeout/ai_fail/interrupted）

誠實鐵則：pending 是 pending、failed 是 failed；app 重啟造成的孤兒 pending 由呼叫端顯性
著地成 `interrupted` 失敗（fail_turn）＋重試鈕，**絕不永遠轉圈**。
半成品絕不進 prompt：recent_history 只收完成輪（見該函式 docstring）。

schema migration＝additive 且冪等（`_connect` 內一次 PRAGMA，缺欄才 ALTER）：舊 DB 零遷移
成本、直接可用；新欄 status/error_kind/page_ctx/locale/user_msg_id 見 `_NEW_MESSAGE_COLS`。

U6 Jev shadow 六欄（2026-09-21；`SPEC-HERMES-TRUST.md` §7.7.2、§5.2 第一批加性欄）
--------------------------------------------------------------------------------
`jev_predicted` / `jev_confidence` / `jev_model` / `jev_criteria_ver` / `jev_error` / `jev_ms`
＝Jev（TypeSafe）router 的 **shadow mode 紀錄：只記錄、不決策**。寫入走 `record_jev()`，
與這一輪答得怎麼樣**完全無關**——它不影響 `status`、不影響回覆內容、不影響任何路由。

誠實鐵則三條：
  ① **`record_jev` 絕不碰 `status`**：`complete_turn`／`fail_turn`／`retry_turn` 的 CAS 守衛
     就是 `status`，shadow 這種加分紀錄不得踩進狀態機。
  ② 六欄全 nullable、無 default ⇒ 舊列與「shadow 沒開」的列一律讀出 NULL（＝沒紀錄），
     **NULL 不代表 Jev 答錯**，對帳時要排除。
  ③ `jev_criteria_ver` 必存：換 criteria 版本＝換量尺，分箱表不得混版（§7.7 門檻隨版本重校）。
對帳工具＝`_jev_shadow_report.py`（讀本表、印分箱與留出題候選）。
#172（2026-09-23，§7.7.5）加性兩欄 `jev_applied`（tool／ask／refuse／none／skip）＋`jev_threshold`＝router 決策面
實際做了什麼；寫入走 `record_jev_router()`（同三條鐵則、不碰 status），`/health` 由 `jev_applied_counts()` 一次 GROUP BY。

U7 👍/👎 回饋兩欄（2026-09-21；`SPEC-HERMES-TRUST.md` §13 ☐11、CONCEPT 第四十一輪）
--------------------------------------------------------------------------------
`rating`（`'up'`／`'down'`／NULL）＋`rated_at`（ISO 秒字串、同 `created_at` 型別）
＝使用者對**這一則回答**的兩態回饋。寫入走 `rate_turn()`（使用者動作，**帶 aid**），
與 U6 的 `record_jev()`（系統觀測，不帶 aid）是不同族的兩件事。

誠實鐵則四條：
  ① **`rate_turn` 絕不碰 `status`**（同 U6 鐵則①：U5 狀態機的 CAS 守衛不得被加分紀錄踩到）。
  ② **只有 done 的一輪可評**（本層加嚴、SPEC ☐11 未提）：pending 的 `content` 是空字串、
     failed 根本沒答成 —— 對一個沒有答案的佔位列按讚沒有語意，而且 `_jev_shadow_report --dump`
     的留出題候選必須有問句**與答案**才有標註價值。非 done ⇒ 回 `{"error": "not_rateable"}`。
  ③ **再按同一顆＝取消**：`rating` 與 `rated_at` **一起清成 NULL**——留下「沒有評分卻有評分
     時間」的鬼列比沒有紀錄更糟。改評（up→down）則兩欄一起覆寫。
  ④ **不落 per-person 身分**（§24）：擁有權是 `aid`（瀏覽器），評分歸這一則回答、不歸人；
     這兩欄不進考績、不回流 prompt、**不從回饋學**（無 fine-tune／不 few-shot）。
迴路＝`_jev_shadow_report.py --dump`：有 rating 的列即使 Jev 六欄全 NULL 也**必須**進留出題
候選（否則就只是「只存不用」）。

U8 上下文三微調（2026-09-22；`SPEC-HERMES-TRUST.md` §13 ☐13、§12.9）
--------------------------------------------------------------------------------
☐13 已裁「上下文維持**有界近期**」（不摘要、不全餵、不只餵單句）；本節是那個決定的三個
微調 —— **口徑一個字沒變**，只是把「餵哪一段、每則餵多長、到底餵了多少」講清楚：

  ① **對話 idle 自動切段**（`recent_history(..., idle_split_s=HISTORY_IDLE_SPLIT_S)`）：
     `aid` ＝**瀏覽器不是人**（Board 前門無登入）⇒ 產線共用機一個 aid 可能是好幾個人輪流問。
     兩則訊息隔超過 2 小時，後面那位問的多半是新題 ⇒ 只餵最近那一段。
     **純讀、gap-based、不改任何狀態**：不寫 `closed_at`、不動 conversation 列、不加欄
     ——切段是「餵給模型的那個視窗」問題，不是對話**生命週期**問題（那是使用者按結束的事）。
  ② **歷史裡 assistant 回覆每則截 ~600 字**（`cap_history(..., assistant_max_chars=…)`）：
     追問只需要**結論**（「那 #8 呢」要的是上一則的主詞，不是它整段推導）。user 列不截
     ——使用者原句短，而且那是**問題本身**，截掉等於改題目。
  ③ **落帳兩欄 `prompt_chars`／`history_turns`**（`record_prompt_stats()`）：沒數字就永遠
     在猜「上下文到底多大」。⚠ `prompt_chars` **只量 app 側送出的 `message`**（ctx_block＋
     使用者原句），**不含 persona 與 history**：persona 住 gateway（~6 KB／turn）、app 這側
     量不到 —— 寧可欄位語意窄而誠實，也不要一個看起來像「總量」其實少一大塊的數字。

三者都不碰 U5 狀態機：`record_prompt_stats` 與 `record_jev` 同一條鐵則（**絕不寫 `status`**）。

U12 #177 失敗答法守門（2026-09-25；`plain_failure_guard()`，純函式、不碰 DB）
--------------------------------------------------------------------------------
工具查無／資料源連不上時，對外回覆一律白話（「這次拿不到，不代表沒有」），`found=false`／
`unavailable_reason`／`OperationalError` 等內部字樣不外露；`found=true`＋放行碼（`no_pass_criteria_limits`）
是答案、不是失敗。app 在 `complete_turn` 前呼叫一次；細節見該節註解。

U13 `tool_call` 時刻統一（2026-09-25；QG-7 回答層第 5 項＝四十七 ▶⑥）
--------------------------------------------------------------------------------
新列 `tool_call.created_at` 改寫台北牆鐘（＝`message.created_at` 同形，原本差 8h）；UTC 進加性欄
`created_at_utc`（同一瞬間，`_now_pair()`）。**舊列不回溯**：舊列 `created_at` 本來就是 UTC、新欄 NULL，
reader `tool_calls_for_turn()` 以 `COALESCE(created_at_utc, created_at)` 給加性鍵 `created_at_utc`＝恆 UTC
⇒ 匯出 CSV／JSON 的 `created_at_utc` 欄語意不變（仍可與 `exported_at` 相減）。

連線紀律（每操作一條）：
  sqlite3.connect(path, timeout=5) + PRAGMA journal_mode=WAL + PRAGMA busy_timeout=5000；
  寫入包在單一 transaction；首用自動建表（CREATE TABLE IF NOT EXISTS）。

DB 路徑：env `BOARD_ASSISTANT_DB`，預設 repo 根 `board_assistant.sqlite3`
（呼叫端 .gitignore 已排定＝產線對話不入版控）。

用法：
  python board_assistant.py --selftest   # 離線自驗（用 tempfile、絕不動預設 DB 檔）
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime

import core_paths as _wp   # 執行期產物路徑唯一出處（Q-F；零相依）

# Windows 主控台預設 cp1252 → 繁中 print 會炸；比照 repo 慣例把 stdout 轉 utf-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---- 常數 ----
_TITLE_MAX = 60          # 對話標題截短字元數（首則使用者訊息）
MAX_OPEN = 10            # 每 aid 開啟中對話上限（超過拒建、需先結束一個）
DEFAULT_HISTORY_TURNS = 8  # recent_history 預設輪數（user+assistant 交錯 → *2 則）
_HISTORY_MAX_CHARS = 8000  # cap_history 預設總字元上限
_PAGE_CTX_MAX = 400        # assistant 佔位列存的原始 page_ctx 上限（retry 重跑用同一份）

# ---- U8 上下文三微調（2026-09-22；見檔頭「U8」節）----
# 兩則相鄰訊息間隔超過這個秒數 ⇒ recent_history 在那裡切，只餵較新的那一段。
# 2h＝☐13 的建議值；**公開常數、可用 kwarg 覆寫**（測試與呼叫端都不必改碼就能換窗）。
HISTORY_IDLE_SPLIT_S = 7200
# 歷史裡**單則 assistant 回覆**的字元上限（超過就截＋接一個省略號）；user 列不套。
_HISTORY_ASSISTANT_MAX_CHARS = 600
# 被截時接在尾巴的記號（單一省略號＋前導空白）＝讓模型看得出來「這裡還有、只是沒給你」。
_TRUNC_MARK = " …"

# U5 失敗分類（error_kind 取值；三分類沿用 app 端 `_gw_error_kind`，interrupted 為本層新增）
# ★ PA2（`_handoff/PLAN-board-assistant-phase2-20261030.md`）：共用 gateway 兩類，**只會由合成進程著地**
#   （app 端 `_gw_error_kind` 的 shared_gw 分支）：quota＝429 每來源每日提問上限；budget＝503 預算用罄。
ERROR_KINDS = ("gw_down", "ai_timeout", "ai_fail", "interrupted", "quota", "budget")


def client_key(client_ip, secret) -> str:
    """純函式（PA2）：來源 IP → gateway 分桶用的不可逆鍵＝HMAC-SHA256(key=secret, msg=ip) 的 hex **前 32 字**。

    - 只存這個鍵、**不存原始 IP**（`message.client_key`）；secret 由 app 端給（`BOARD_HERMES_SECRET`）
      ⇒ 換 secret 等於換一批鍵（舊列不回溯）。
    - ip 或 secret 不是非空字串 ⇒ `"unknown"`（全部未知來源共用一桶；寧共桶也不發明身分）。
    """
    import hashlib
    import hmac
    if not isinstance(client_ip, str) or not client_ip or not isinstance(secret, str) or not secret:
        return "unknown"
    return hmac.new(secret.encode("utf-8"), client_ip.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

# ---- U9 每日 LLM 呼叫保險絲（2026-09-22；`SPEC-HERMES-TRUST.md` §12.10）--------------
# Chris 2026-09-22 拍板（原話：「可接受。開包，走 (b)」）：C-B4 局部重開之後
# （`/assistant` 與兩條翻譯路徑對隧道放行），**花費那一面需要一個獨立的防線**
# ⇒ **全站、全流量（含內網）一個每日 LLM 呼叫上限**。
#
# 為什麼是「一個桶」而不是 per-aid 配額／分層／白名單：**那些都是身分制**，而 Board 前門
# 沒有登入、擁有權只到匿名 `aid`（§14.7／§24）。保險絲要回答的問題只有一個——
# 「今天會不會有人把 Chris 的 codex 額度燒光」——一個總數就答得完；做成配額表反而
# 需要「誰是誰」這件我方刻意不做的事。**不做**：per-aid 配額、分層、白名單、管理介面。
#
# 兩個 kind 各有自己的桶（2026-09-22 Chris 裁 (a)；`SPEC-HERMES-TRUST.md` §12.10 追記）：
# `assistant`（一輪 NL 問答）與 `translate`（一次翻譯 gateway 呼叫）雖然**都打同一顆 WSL
# gateway → 本機 codex**，但**翻譯快取是進程內的**（`board_translate._CACHE`，重啟即清）
# ⇒ 每次重啟後中文頁面的每則自由文字備註各燒一次翻譯呼叫；共用一桶時，極端情況會把當天
# 助理的額度整個吃光——那正是保險絲**不該**造成的失效（該擋的是暴衝，不是把人鎖在門外）。
# 因此 **kind = 桶**：各自一個 env、各自一個上限，互不吃對方的額度；分 kind 落列照舊看得出
# 今天是誰在燒。（(b) 翻譯快取落盤＝跨重啟不重翻，另案排後，不在本次。）
LLM_KINDS = ("assistant", "translate", "summary")
# ★ 2026-09-23 #167 P3：第三個桶 `summary`＝`/downtime` 點段面板的工單 LLM 摘要（SPEC-DOWNTIME §8）。
#   一個 kind 一個桶（§12.10-a）：摘要燒光不會把助理／翻譯鎖在門外，反之亦然。
#   `/health` 的既有 `caps`（兩桶）**逐位元不動**（smoke 釘著它）；本桶走加性鍵 `summary_used`／`summary_cap`，
#   `by_kind` 自然帶出。
SUMMARY_DAILY_MAX_DEFAULT = 100          # §14 ② Fable 裁（≈ 20 次點段 × 5 張）
ENV_SUMMARY_DAILY_MAX = "BOARD_SUMMARY_DAILY_MAX"
#: 助理桶每日上限（Chris 指定 200）。env `BOARD_LLM_DAILY_MAX` 覆寫；**`0`＝不限**。
LLM_DAILY_MAX_DEFAULT = 200
ENV_LLM_DAILY_MAX = "BOARD_LLM_DAILY_MAX"
#: 翻譯桶每日上限（Chris 裁 (a)＝600）。env `BOARD_TRANSLATE_DAILY_MAX`；**`0`＝不限**。
#  為什麼比助理大：一次整頁翻譯＝逐批數十次呼叫，而且每次重啟都要重翻一遍（快取在進程內）
#  ⇒ 它的正常量級本來就比「人一句一句問」高一個數量級。
TRANSLATE_DAILY_MAX_DEFAULT = 600
ENV_TRANSLATE_DAILY_MAX = "BOARD_TRANSLATE_DAILY_MAX"

# U7 👍/👎 值域（**TEXT 不用 ±1**：SPEC §13 ☐11 與 CONCEPT 第四十一輪逐字都寫 up／down／NULL，
# 且 `--dump` 的 JSON 是給人讀的，`"down"` 比 `-1` 誠實）。NULL＝沒評過、不在此列。
RATINGS = ("up", "down")


class AssistantError(Exception):
    """助理對話領域錯誤（擁有權違反 / 已結束 / 超過上限）。"""


# ---------------------------------------------------------------------------
# 純函式輔助
# ---------------------------------------------------------------------------

def _now() -> str:
    """台北牆鐘 naive ISO 秒字串（對齊 repo 慣例：MES/W8 皆台北 naive）。"""
    return datetime.now().isoformat(timespec="seconds")


def _title_from(text: str) -> str:
    """首則使用者訊息 → 對話標題（壓平空白、截 ~60 字；空 → 佔位 '…'）。"""
    flat = " ".join((text or "").split())
    if not flat:
        return "…"
    return flat[:_TITLE_MAX]


def _ts_gap_s(older: str | None, newer: str | None) -> float | None:
    """兩個 ISO 秒字串相差幾秒（newer - older）；任一側 NULL／壞格式 → **None**（＝量不出來）。

    U8① 用它判 idle。**量不出來一律當「不切」**（保守：寧可多餵一點舊上下文，也不要因為
    一個壞時戳就把使用者剛剛那半句話的前文砍掉）。
    """
    try:
        return (datetime.fromisoformat(newer) - datetime.fromisoformat(older)).total_seconds()
    except (TypeError, ValueError):
        return None


def cap_history(history: list[dict], max_chars: int = _HISTORY_MAX_CHARS,
                assistant_max_chars: int = _HISTORY_ASSISTANT_MAX_CHARS) -> list[dict]:
    """把歷史裁到 max_chars 總字元內（純函式）：
      0. **U8②：`role=='assistant'` 的每一則先各自截到 assistant_max_chars**（超過才截、
         截完接 `_TRUNC_MARK`）。user 列不套這一關（原句短，且那是問題本身）。
      1. 單則 content 超 max_chars → 截斷到 max_chars。
      2. 裁後總字元仍超限 → **從最舊丟**，直到不超。
    保序（舊→新）；role 原樣保留。空輸入回空。
    `assistant_max_chars` 非正數 ⇒ 關掉第 0 關（只跑既有的 1、2）＝可注入的關閉開關。
    ⚠ 順序不可換：先逐則截 assistant、**再**算總量丟最舊 —— 反過來會先丟掉整輪舊對話，
    然後才發現截一截其實塞得下（同樣的預算餵到的輪數變少）。
    """
    capped: list[dict] = []
    for h in history or []:
        content = h.get("content", "") or ""
        if (h.get("role") == "assistant" and assistant_max_chars > 0
                and len(content) > assistant_max_chars):
            content = content[:assistant_max_chars] + _TRUNC_MARK
        if len(content) > max_chars:
            content = content[:max_chars]
        capped.append({"role": h.get("role"), "content": content})

    def _total(items: list[dict]) -> int:
        return sum(len(x["content"]) for x in items)

    while capped and _total(capped) > max_chars:
        capped.pop(0)  # 從最舊丟
    return capped


# ---------------------------------------------------------------------------
# 連線 / schema（每操作一條新連線）
# ---------------------------------------------------------------------------

def _db_path(path: str | None = None) -> str:
    if path:
        return path
    env = os.environ.get("BOARD_ASSISTANT_DB")
    if env:
        return env
    return _wp.repo_root_path(_wp.ASSISTANT_DB_FILE, repo=os.path.dirname(os.path.abspath(__file__)))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aid TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_conversation_aid ON conversation(aid);
CREATE TABLE IF NOT EXISTS message(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_message_conversation ON message(conversation_id);
CREATE TABLE IF NOT EXISTS llm_budget(
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(day, kind)
);
CREATE TABLE IF NOT EXISTS tool_call(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL,
    tool TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    provenance TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_call_turn ON tool_call(turn_id, id);
"""


# U5 additive 欄（只加在 message 表；一律 nullable、無預設＝舊列讀出 NULL）
#   status       assistant 列用：NULL=done（舊資料語意）/ 'pending' / 'failed'；user 列恆 NULL
#   error_kind   failed 時的分類（見 ERROR_KINDS）；其餘 NULL
#   page_ctx     送出當下的原始 page_ctx（cap _PAGE_CTX_MAX）＝「問的當下你在哪頁」比 retry
#                當下在哪頁忠實；retry 重跑用同一份
#   locale       送出時算好的 reply_locale（retry 重跑用同一份）
#   user_msg_id  assistant 列 → 配對 user 列 id。**歷史過濾與 retry 的唯一正確錨**
#                （message id 全域遞增、跨對話可交錯，`turn_id-1` 不可靠、禁用）
#   ---- U6 Jev shadow（2026-09-21；見檔頭「U6」節）----
#   jev_predicted     Jev choice 題選出的路由（工具名／'ask'／'refuse'）；**只記錄不決策**
#   jev_confidence    該選項的 confidence（0–1；官方＝分布集中度重標，**不是**最大機率）
#   jev_model         回應回報的模型（如 `jev-1.13.0`）＝換版可辨識
#   jev_criteria_ver  送出時用的 fixture `criteria_version`（混版的分箱表沒有意義）
#   jev_error         失敗 kind（no_key/timeout/http_<code>/net/bad_response…）；成功為 NULL
#   jev_ms            這次呼叫耗時（毫秒）
#   ---- U7 👍/👎 回饋（2026-09-21；見檔頭「U7」節）----
#   rating            'up'／'down'／NULL（NULL＝沒評過，**不是**「評成中立」）；只有 done 的
#                     assistant 列可有值；再按同一顆＝連同 rated_at 一起清回 NULL
#   rated_at          最後一次評分的時刻（ISO 秒字串、同 created_at 型別）；取消時一起清 NULL
#   ---- U8 上下文落帳（2026-09-22；見檔頭「U8」節③）----
#   prompt_chars      這一輪 app 側**送出的 `message`** 字元數（ctx_block＋使用者原句）。
#                     ⚠ **不含 persona 與 history**：persona 住 gateway、app 這側量不到；
#                     history 另有 `history_turns`。**別把它當「總 token 量」讀**。
#   history_turns     cap 後**真的餵進去的歷史則數**（user／assistant 各算一則，**不是對數**）。
#                     0＝這一輪沒有歷史（新對話、或 idle 切段後自己就是第一則）。
#   ---- U6 加性兩欄：Jev router 決策面（#172 第三步，2026-09-23；§7.7.5）----
#   jev_applied       這一輪 router 實際做了什麼（`JEV_APPLIED` 五態閉集）：
#                     tool／ask／refuse＝加了 `[router]` 指令；none＝問到了但低於門檻、未加；
#                     skip＝逾時／錯誤、退回現行路徑。**NULL＝router 沒開（純 shadow）或舊列**。
#   jev_threshold     判定當下用的門檻（REAL；env 可調 ⇒ 每列自帶，對帳不必猜當時值）。
_NEW_MESSAGE_COLS = (
    ("status", "TEXT"),
    ("error_kind", "TEXT"),
    ("page_ctx", "TEXT"),
    ("locale", "TEXT"),
    ("user_msg_id", "INTEGER"),
    ("jev_predicted", "TEXT"),
    ("jev_confidence", "REAL"),
    ("jev_model", "TEXT"),
    ("jev_criteria_ver", "INTEGER"),
    ("jev_error", "TEXT"),
    ("jev_ms", "INTEGER"),
    ("rating", "TEXT"),
    ("rated_at", "TEXT"),
    ("prompt_chars", "INTEGER"),
    ("history_turns", "INTEGER"),
    ("jev_applied", "TEXT"),
    ("jev_threshold", "REAL"),
    # PA2 加性一欄：client_key＝送出當下來源 IP 的 HMAC（見 `client_key`；**原始 IP 不落庫**）。
    #   只有合成進程收單時寫；非合成進程與舊列＝NULL。retry 不碰（沿用送出當下那份）。
    ("client_key", "TEXT"),
)


def _migrate_message(conn: sqlite3.Connection) -> None:
    """U5 additive migration（冪等）：一次 PRAGMA 讀現有欄位，缺哪個才 ALTER。

    成本＝每連線一次 `PRAGMA table_info`（不掃資料）。舊 DB 零遷移腳本即可用。
    併發撞車（另一條連線剛好搶先 ALTER 完）→ sqlite 回 "duplicate column name"，
    語意上就是「已經加好了」＝吞掉；其餘 OperationalError 照樣拋（別把真故障靜音）。
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(message)").fetchall()}
    for col, decl in _NEW_MESSAGE_COLS:
        if col in have:
            continue
        try:
            conn.execute(f"ALTER TABLE message ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


# U13 `tool_call` 加性欄（2026-09-25 QG-7 回答層第 5 項；四十七接手單 ▶⑥）
#   created_at_utc    這一列的 UTC 時刻（ISO 秒字串、無時區後綴）。**新列才有**；
#                     舊列（本欄加入前寫的）＝NULL，且那些舊列的 `created_at` **本來就是 UTC**
#                     ⇒ 讀端的 UTC 值＝`COALESCE(created_at_utc, created_at)`，**不猜時區、不回溯改舊列**。
#   自本欄起 `created_at` 改寫台北牆鐘（＝`message.created_at` 同一種寫法、同一個 `_now()` 形），
#   兩個字串取自**同一個瞬間**（`_now_pair()`）。
_NEW_TOOL_CALL_COLS = (
    ("created_at_utc", "TEXT"),
)


def _migrate_tool_call(conn: sqlite3.Connection) -> None:
    """U13 additive migration（冪等；形逐條照 `_migrate_message`）。"""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(tool_call)").fetchall()}
    for col, decl in _NEW_TOOL_CALL_COLS:
        if col in have:
            continue
        try:
            conn.execute(f"ALTER TABLE tool_call ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _connect(path: str | None = None) -> sqlite3.Connection:
    """開新連線（每操作一條、不共享）；設 WAL + busy_timeout；首用建表 + U5 欄位 migration。"""
    conn = sqlite3.connect(_db_path(path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    _migrate_message(conn)
    _migrate_tool_call(conn)
    return conn


# ---------------------------------------------------------------------------
# 讀取（全部 aid-scoped＝擁有權強制）
# ---------------------------------------------------------------------------

def list_open(aid: str, path: str | None = None) -> list[dict]:
    """本 aid 開啟中（closed_at IS NULL）對話，依 updated_at desc（同秒退回 id desc）。
    每筆 dict 含 id/title/updated_at。"""
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM conversation "
            "WHERE aid=? AND closed_at IS NULL "
            "ORDER BY updated_at DESC, id DESC",
            (aid,),
        ).fetchall()
        return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]
    finally:
        conn.close()


def get_conversation(aid: str, cid: int, path: str | None = None) -> dict | None:
    """單筆對話；非本 aid 擁有 → None（不洩漏他人對話存在與否）。"""
    conn = _connect(path)
    try:
        r = conn.execute(
            "SELECT id, aid, title, created_at, updated_at, closed_at "
            "FROM conversation WHERE id=? AND aid=?",
            (cid, aid),
        ).fetchone()
        return dict(r) if r is not None else None
    finally:
        conn.close()


def get_messages(aid: str, cid: int, path: str | None = None) -> list[dict]:
    """對話全部訊息（依 id 遞增時序）；非本 aid → 空清單（擁有權強制）。

    U5：每筆補 status/error_kind（模板據此渲染 pending 佔位／failed 重試鈕）；
    舊列兩者皆 NULL＝done＝逐位元照舊渲染。
    U7：每筆補 rating/rated_at（panel 還原路徑也走同一份單輪 partial ⇒ 沒有這兩欄就會
    「關分頁回來評過的那顆不見了」）。
    U11（#153）：每筆補 `tool_call_count`（模板據此決定要不要渲染匯出 CSV 的小連結）。
    ⚠ **一次查完整段對話**、不是逐則 `COUNT(*)`——panel 一次渲染整段對話
      （動輒十幾則），逐則查就是 N+1。單則路徑（poll／retry／rate）另走 `tool_basis_for_turn()`。
    U14（#137 A3「依據」折疊區）：每筆再補 `tool_basis`＝該輪工具呼叫的 chip 清單（`basis_chip`
      形、依呼叫順序；零呼叫＝空 list）。**同一次查詢**取 `turn_id, tool, provenance`，
      `tool_call_count` 改由它的長度供（值與舊 `GROUP BY` 逐筆相同）＝仍是一次、不是兩次。
    """
    conn = _connect(path)
    try:
        owned = conn.execute(
            "SELECT 1 FROM conversation WHERE id=? AND aid=?", (cid, aid)
        ).fetchone()
        if owned is None:
            return []
        rows = conn.execute(
            "SELECT id, role, content, created_at, status, error_kind, rating, rated_at, "
            "       jev_applied, jev_predicted, jev_confidence "
            "FROM message WHERE conversation_id=? ORDER BY id",
            (cid,),
        ).fetchall()
        basis_by_turn: dict[int, list] = {}
        for r in conn.execute(
                "SELECT turn_id, tool, provenance FROM tool_call WHERE turn_id IN "
                "(SELECT id FROM message WHERE conversation_id=?) ORDER BY id",
                (cid,)).fetchall():
            basis_by_turn.setdefault(int(r["turn_id"]), []).append(
                basis_chip(r["tool"], _json_or_raw(r["provenance"])))
        out = []
        for r in rows:
            d = dict(r)
            chips = basis_by_turn.get(int(r["id"]), [])
            d["tool_call_count"] = len(chips)
            d["tool_basis"] = chips
            out.append(d)
        return out
    finally:
        conn.close()


def recent_history(
    aid: str,
    cid: int,
    max_turns: int = DEFAULT_HISTORY_TURNS,
    before_id: int | None = None,
    path: str | None = None,
    idle_split_s: int = HISTORY_IDLE_SPLIT_S,
) -> list[dict]:
    """最近 max_turns 輪訊息 [{role, content}]（由舊到新，供 gateway prompt）；非本 aid → 空。

    U5 過濾規則（**只餵完成輪**，半成品絕不進 prompt）：
      - assistant 列：只收 `status IS NULL`（done；含舊資料）。pending 的 content 是空字串、
        failed 的是沒答成 —— 餵進去會毒化上下文。
      - 該輪的 **user 列一併排除**：一問懸著沒答案，只餵問題會讓 codex 以為要補答舊題。
        判準＝「作為某 assistant 列的 user_msg_id、且該列 status IS NOT NULL」。
        （舊資料的 assistant 列 user_msg_id 為 NULL → 配不上 → user 列照收，語意不變。）
      - `before_id`：只取 id < before_id。worker 重跑時傳該 turn 的 user_msg_id ⇒ 歷史＝
        「這一輪之前的完成輪」，不會把自己這一問算進去。

    U8① idle 自動切段（`idle_split_s`，預設 `HISTORY_IDLE_SPLIT_S`＝2h）：
      取回那一疊（已由舊到新）**從最新一則往回走**，遇到第一個相鄰間隔 > idle_split_s 就在
      那裡切，只回**切點之後（較新）**的那一段。往回走＝取「最近一次長時間空白」，前面還有
      幾個空白都不管（那些更舊、更不可能是同一個人同一個話題）。
      **純讀**：不寫 `closed_at`、不動 conversation 一個位元、不加任何欄（見檔頭 U8①）。
      時戳量不出來（NULL／壞格式）⇒ **那個邊界不切**、繼續往回走（保守＝照舊全餵）。
      `idle_split_s <= 0` ⇒ 整個關掉（可注入的關閉開關）。
    回傳形狀仍是 `[{role, content}]`（`created_at` 只在函式內用完即丟，**不外流**）。
    """
    conn = _connect(path)
    try:
        owned = conn.execute(
            "SELECT 1 FROM conversation WHERE id=? AND aid=?", (cid, aid)
        ).fetchone()
        if owned is None:
            return []
        rows = conn.execute(
            "SELECT role, content, created_at FROM message m WHERE m.conversation_id=? "
            "AND (? IS NULL OR m.id < ?) "
            "AND ( (m.role='assistant' AND m.status IS NULL) "
            "      OR (m.role='user' AND NOT EXISTS ("
            "            SELECT 1 FROM message a WHERE a.user_msg_id=m.id "
            "              AND a.status IS NOT NULL)) ) "
            "ORDER BY m.id DESC LIMIT ?",
            (cid, before_id, before_id, max(0, max_turns) * 2),
        ).fetchall()
        rows = list(rows)
        rows.reverse()  # 由舊到新還原對話順序
        if idle_split_s > 0:                        # U8①：從最新往回找第一個長空白就切
            cut = 0
            for i in range(len(rows) - 1, 0, -1):
                gap = _ts_gap_s(rows[i - 1]["created_at"], rows[i]["created_at"])
                if gap is not None and gap > idle_split_s:
                    cut = i
                    break
            rows = rows[cut:]
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    finally:
        conn.close()


def count_open(aid: str, path: str | None = None) -> int:
    """本 aid 開啟中對話數（上限守門用；不載列）。"""
    conn = _connect(path)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM conversation WHERE aid=? AND closed_at IS NULL", (aid,)
        ).fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 寫入（單 transaction）
# ---------------------------------------------------------------------------

def add_turn(
    aid: str,
    cid: int | None,
    user_content: str,
    assistant_content: str,
    path: str | None = None,
) -> int:
    """同 transaction 寫一輪（user + assistant 兩則），回 conversation id。

    - cid=None → 惰性建會話（title = user_content 截短；套開啟中上限 MAX_OPEN，超過 raise）。
    - 既有 cid → 驗 aid 擁有 + 未關閉（否則 raise，不寫）。
    - 每輪推進 updated_at（切換列「最近活動」排序）。
    """
    conn = _connect(path)
    try:
        now = _now()
        conn.execute("BEGIN")
        if cid is None:
            n_open = conn.execute(
                "SELECT COUNT(*) FROM conversation WHERE aid=? AND closed_at IS NULL", (aid,)
            ).fetchone()[0]
            if n_open >= MAX_OPEN:
                conn.rollback()
                raise AssistantError("open conversation limit reached")
            cur = conn.execute(
                "INSERT INTO conversation(aid, title, created_at, updated_at, closed_at) "
                "VALUES(?, ?, ?, ?, NULL)",
                (aid, _title_from(user_content), now, now),
            )
            cid = int(cur.lastrowid)
        else:
            r = conn.execute(
                "SELECT closed_at FROM conversation WHERE id=? AND aid=?", (cid, aid)
            ).fetchone()
            if r is None or r["closed_at"] is not None:
                conn.rollback()
                raise AssistantError("conversation not found, not owned, or closed")
        conn.execute(
            "INSERT INTO message(conversation_id, role, content, created_at) VALUES(?, ?, ?, ?)",
            (cid, "user", user_content, now),
        )
        conn.execute(
            "INSERT INTO message(conversation_id, role, content, created_at) VALUES(?, ?, ?, ?)",
            (cid, "assistant", assistant_content, now),
        )
        conn.execute("UPDATE conversation SET updated_at=? WHERE id=?", (now, cid))
        conn.commit()
        return int(cid)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# U5 兩段式送出（begin → complete / fail → retry；全部 aid-scoped、單 transaction）
# ---------------------------------------------------------------------------

def _cap_page_ctx(page_ctx: str | None) -> str | None:
    """page_ctx 存前截斷（None 保持 None＝「沒有上下文」≠「空字串」）。"""
    if page_ctx is None:
        return None
    return str(page_ctx)[:_PAGE_CTX_MAX]


def _fetch_turn_row(conn: sqlite3.Connection, aid: str, turn_id: int) -> sqlite3.Row | None:
    """取本 aid 擁有的 assistant 佔位列（join conversation 驗擁有）；否則 None。

    限定 role='assistant'＝turn_id 只可能指佔位列；拿 user 列 id 來問一律 None（不洩資訊）。
    """
    return conn.execute(
        "SELECT m.id, m.conversation_id, m.status, m.error_kind, m.content, "
        "       m.page_ctx, m.locale, m.user_msg_id, m.created_at, m.rating, m.rated_at, "
        "       m.jev_applied, m.jev_predicted, m.jev_confidence, m.client_key, "
        "       (SELECT u.content FROM message u WHERE u.id=m.user_msg_id) AS user_content "
        "FROM message m JOIN conversation c ON c.id=m.conversation_id "
        "WHERE m.id=? AND c.aid=? AND m.role='assistant'",
        (turn_id, aid),
    ).fetchone()


def _turn_dict(r: sqlite3.Row) -> dict:
    """佔位列 → 對外 dict；status 的 NULL 在此翻成顯性 'done'（呼叫端不必再判 None）。"""
    return {
        "id": r["id"],
        "conversation_id": r["conversation_id"],
        "status": r["status"] or "done",
        "error_kind": r["error_kind"],
        "content": r["content"],
        "page_ctx": r["page_ctx"],
        "locale": r["locale"],
        "user_msg_id": r["user_msg_id"],
        "user_content": r["user_content"],
        "created_at": r["created_at"],
        # U7：模板據此把已評的那一顆常顯（拿不到現況＝按完一下就回到「未評」）
        "rating": r["rating"],
        "rated_at": r["rated_at"],
        # #172：router chip（例外才出現；只在 jev_applied ∈ {tool, ask, refuse}）
        "jev_applied": r["jev_applied"],
        "jev_predicted": r["jev_predicted"],
        "jev_confidence": r["jev_confidence"],
        # PA2：worker 交給 gateway 當 `client_id`（只有合成進程收單時有值；其餘 None）。模板不印。
        "client_key": r["client_key"],
    }


def begin_turn(
    aid: str,
    cid: int | None,
    user_content: str,
    page_ctx: str | None,
    locale: str | None,
    path: str | None = None,
    client_key: str | None = None,
) -> tuple[int, int]:
    """phase 1：同 transaction 寫 user 列 + assistant **佔位列**（pending），回 (cid, turn_id)。

    - cid=None → 惰性建會話（title 截短；套 MAX_OPEN，超過 raise＝與 add_turn 同語意）。
    - 既有 cid → 驗 aid 擁有 + 未關閉（否則 raise，不寫）。
    - 佔位列：content=''、status='pending'、存下送出當下的 page_ctx/locale 與配對 user_msg_id。
    - 推進 updated_at ⇒ **新會話在送出瞬間就存在**（切換列標籤立刻出現，不必等回答）。
    - `client_key`（PA2 加性 kwarg）＝來源 IP 的 HMAC（`client_key()` 的輸出），存佔位列；缺省 None ⇒ NULL
      （非合成進程不傳＝寫入值同改前）。⚠ 本參數名遮住模組級 `client_key()`——函式體內不呼叫它。
    """
    conn = _connect(path)
    try:
        now = _now()
        conn.execute("BEGIN")
        if cid is None:
            n_open = conn.execute(
                "SELECT COUNT(*) FROM conversation WHERE aid=? AND closed_at IS NULL", (aid,)
            ).fetchone()[0]
            if n_open >= MAX_OPEN:
                conn.rollback()
                raise AssistantError("open conversation limit reached")
            cur = conn.execute(
                "INSERT INTO conversation(aid, title, created_at, updated_at, closed_at) "
                "VALUES(?, ?, ?, ?, NULL)",
                (aid, _title_from(user_content), now, now),
            )
            cid = int(cur.lastrowid)
        else:
            r = conn.execute(
                "SELECT closed_at FROM conversation WHERE id=? AND aid=?", (cid, aid)
            ).fetchone()
            if r is None or r["closed_at"] is not None:
                conn.rollback()
                raise AssistantError("conversation not found, not owned, or closed")
        cur = conn.execute(
            "INSERT INTO message(conversation_id, role, content, created_at) VALUES(?, ?, ?, ?)",
            (cid, "user", user_content, now),
        )
        user_msg_id = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO message(conversation_id, role, content, created_at, "
            "                    status, page_ctx, locale, user_msg_id, client_key) "
            "VALUES(?, 'assistant', '', ?, 'pending', ?, ?, ?, ?)",
            (cid, now, _cap_page_ctx(page_ctx), locale, user_msg_id, client_key),
        )
        turn_id = int(cur.lastrowid)
        conn.execute("UPDATE conversation SET updated_at=? WHERE id=?", (now, cid))
        conn.commit()
        return int(cid), turn_id
    finally:
        conn.close()


def complete_turn(aid: str, turn_id: int, reply: str, path: str | None = None) -> None:
    """phase 2 成功：pending 佔位列 → content=reply、status=NULL(=done)、error_kind 清空。

    非 pending（已 done / 已 failed / 不存在 / 非本 aid）→ AssistantError
    ＝防重複完成與亂寫。**狀態轉移走 CAS**（UPDATE 帶 `status='pending'` 守衛、`rowcount==1`
    才算贏）⇒ 併發下真的只有一條會贏；前置 `_fetch_turn_row` 只為給人看的錯誤訊息，
    **不是** 正確性的依據（check-then-act 之間的空窗由 CAS 收）。
    """
    conn = _connect(path)
    try:
        r = _fetch_turn_row(conn, aid, turn_id)
        if r is None:
            raise AssistantError("turn not found or not owned")
        if r["status"] != "pending":
            raise AssistantError(f"turn not pending (status={r['status'] or 'done'})")
        now = _now()
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE message SET content=?, status=NULL, error_kind=NULL "
            "WHERE id=? AND status='pending'",
            (reply, turn_id),
        )
        if cur.rowcount != 1:                   # 讀到寫之間被別條路徑著地了 → 這條輸、不覆寫
            conn.rollback()
            raise AssistantError("turn no longer pending (lost race)")
        conn.execute(
            "UPDATE conversation SET updated_at=? WHERE id=?", (now, r["conversation_id"])
        )
        conn.commit()
    finally:
        conn.close()


def fail_turn(aid: str, turn_id: int, error_kind: str, path: str | None = None) -> None:
    """phase 2 失敗：pending → status='failed' + error_kind（見 ERROR_KINDS）。

    - 已 failed 再 fail → no-op（冪等；保留最初的 error_kind＝第一手死因較忠實）。
    - 已 done → AssistantError（不得把答成的一輪改成失敗）。
    - 不存在 / 非本 aid → AssistantError。
    **狀態轉移走 CAS**（UPDATE 帶 `status='pending'` 守衛）：輸掉競態時再讀一次現況——
    已 failed ⇒ 回到冪等 no-op（第一手死因留著）、已 done ⇒ raise（不得改判失敗）。
    """
    conn = _connect(path)
    try:
        r = _fetch_turn_row(conn, aid, turn_id)
        if r is None:
            raise AssistantError("turn not found or not owned")
        if r["status"] == "failed":
            return  # 冪等 no-op
        if r["status"] != "pending":
            raise AssistantError("turn already completed")
        now = _now()
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE message SET status='failed', error_kind=? WHERE id=? AND status='pending'",
            (error_kind, turn_id),
        )
        if cur.rowcount != 1:                   # 輸掉競態 → 依現況分流（no-op 或 raise）
            conn.rollback()
            r2 = _fetch_turn_row(conn, aid, turn_id)
            if r2 is not None and r2["status"] == "failed":
                return                          # 別條路徑先著地了：保留它的 error_kind
            raise AssistantError("turn already completed")
        conn.execute(
            "UPDATE conversation SET updated_at=? WHERE id=?", (now, r["conversation_id"])
        )
        conn.commit()
    finally:
        conn.close()


def get_turn(aid: str, turn_id: int, path: str | None = None) -> dict | None:
    """單一 turn 現況（poll 用）；非本 aid / 不存在 → None（不洩他人 turn 存在與否）。

    回 {id, conversation_id, status('pending'|'failed'|'done'), error_kind, content,
        page_ctx, locale, user_msg_id, user_content, created_at}。
    """
    conn = _connect(path)
    try:
        r = _fetch_turn_row(conn, aid, turn_id)
        return _turn_dict(r) if r is not None else None
    finally:
        conn.close()


def retry_turn(aid: str, turn_id: int, path: str | None = None) -> dict:
    """failed → 重置 pending（error_kind 清空、推進 updated_at），回 get_turn 同形 dict。

    回傳含重跑所需的 conversation_id/user_content/page_ctx/locale/user_msg_id
    （**用送出當下存下的那份**，不是 retry 當下的）。
    非 failed → AssistantError：pending 中不可 retry（防雙跑）、done 不可 retry。
    **狀態轉移走 CAS**（UPDATE 帶 `status='failed'` 守衛、`rowcount==1` 才算贏）——這條是
    併發最要命的一條：兩個分頁同時按重試若兩邊都贏，同一題會起兩個 worker、**雙燒 gateway
    配額**。前置 `_fetch_turn_row` 只給友善錯誤訊息用，正確性靠 CAS。
    """
    conn = _connect(path)
    try:
        r = _fetch_turn_row(conn, aid, turn_id)
        if r is None:
            raise AssistantError("turn not found or not owned")
        if r["status"] != "failed":
            raise AssistantError(f"turn not failed (status={r['status'] or 'done'})")
        now = _now()
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE message SET status='pending', error_kind=NULL "
            "WHERE id=? AND status='failed'", (turn_id,)
        )
        if cur.rowcount != 1:                   # 別人先重試/先著地了 → 這條輸，絕不第二次起跑
            conn.rollback()
            raise AssistantError("turn no longer failed (lost race)")
        conn.execute(
            "UPDATE conversation SET updated_at=? WHERE id=?", (now, r["conversation_id"])
        )
        conn.commit()
        r2 = _fetch_turn_row(conn, aid, turn_id)
        return _turn_dict(r2)
    finally:
        conn.close()


def rate_turn(aid: str, turn_id: int, rating: str, path: str | None = None) -> dict | None:
    """U7 👍/👎：把使用者對**這一則回答**的兩態回饋寫進 `rating`／`rated_at`。

    回傳三態（呼叫端據此分流，見檔頭 U7 節）：
      - `None`              ＝這個 turn 不存在／非本 aid 擁有／不是 assistant 列（**不洩資訊**）。
      - `{"error": "not_rateable"}` ＝存在且是本人的，但**不是 done**（pending／failed）。
      - `_turn_dict(...)`   ＝寫好了，回更新後的現況（模板據此把已評的那顆標 is-active）。
    非法 `rating` ⇒ `AssistantError`（呼叫端翻 4xx）：值域是封閉的 `RATINGS`，**不靜默吞**。

    鐵則：
      ① **絕不碰 `status`**（U5 狀態機的 CAS 守衛；同 `record_jev` 鐵則①）。
      ② **擁有權＋僅 assistant 列**兩件事由 `_fetch_turn_row` 一次滿足（它已 join conversation
         驗 aid 並限定 `role='assistant'`）——不自己另寫一條 SQL、不繞過它。
      ③ **再按同一顆＝取消**：兩欄一起清 NULL（不留「沒有評分卻有評分時間」的鬼列）。
      ④ 這裡**不套 `_assist_rate_ok`**（那是 LLM 配額節流；按讚不燒 gateway 配額，名字撞而已）。
    """
    if rating not in RATINGS:
        raise AssistantError(f"bad rating {rating!r} (expect one of {RATINGS})")
    conn = _connect(path)
    try:
        r = _fetch_turn_row(conn, aid, turn_id)
        if r is None:
            return None
        if r["status"] is not None:                 # NULL＝done；pending／failed 一律拒
            return {"error": "not_rateable"}
        toggle_off = (r["rating"] == rating)        # 同值＝取消（兩欄一起清）
        new_rating = None if toggle_off else rating
        new_at = None if toggle_off else _now()
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE message SET rating=?, rated_at=? WHERE id=? AND role='assistant'",
            (new_rating, new_at, turn_id),
        )
        conn.commit()
        r2 = _fetch_turn_row(conn, aid, turn_id)
        return _turn_dict(r2) if r2 is not None else None
    finally:
        conn.close()


def record_jev(turn_id: int, r, path: str | None = None) -> bool:
    """U6：把一次 Jev shadow 呼叫的結果記到 assistant 列的六個加性欄。回 True＝真的寫到一列。

    `r`＝`board_jev.JevResult`（鴨子型別：只取那六個屬性，不 import 它＝本 store 維持零相依）。

    鐵則（見檔頭 U6 節）：
      ① **絕不碰 `status`**（那是 U5 狀態機 CAS 的守衛）；本函式與一輪答得成不成完全無關。
      ② `role='assistant'` 守衛＝只寫佔位／答案列；拿 user 列 id 來寫**一列都不動**（回 False）。
      ③ **不帶 `aid`**：這是系統自己的觀測紀錄，不是使用者可讀寫的資源；`turn_id` 由呼叫端
         （背景 worker）從自己剛建的那一輪拿，不來自請求。§24：不落 per-person 身分。
      ④ 可重複呼叫（後寫覆蓋前寫）——shadow 一個 turn 只打一次，重複寫沒有語意問題。
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE message SET jev_predicted=?, jev_confidence=?, jev_model=?, "
            "                   jev_criteria_ver=?, jev_error=?, jev_ms=? "
            "WHERE id=? AND role='assistant'",
            (getattr(r, "predicted", None),
             getattr(r, "confidence", None),
             getattr(r, "model", None),
             getattr(r, "criteria_version", None),
             getattr(r, "error", None),
             getattr(r, "elapsed_ms", None),
             turn_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


JEV_APPLIED = ("tool", "ask", "refuse", "none", "skip")   # 與 board_jev.ROUTER_APPLIED 同值（本 store 零相依、不 import）


def record_jev_router(turn_id: int, applied: str, threshold, path: str | None = None) -> bool:
    """U6 加性兩欄（#172 第三步；`SPEC-HERMES-TRUST.md` §7.7.5）：記下 router 這一輪**實際做了什麼**。

    鐵則（與 `record_jev` 同一組）：
      ① **絕不碰 `status`**；② `role='assistant'` 守衛（拿 user 列 id 一列都不動 ⇒ False）；
      ③ 不帶 `aid`；④ `applied` 必須在 `JEV_APPLIED` 五態閉集、`threshold` 必須是數字
         （連 bool 都拒）⇒ 否則 `ValueError`——這兩欄要拿來算「直採率」，靜默吞壞值＝統計表說謊。
    """
    if applied not in JEV_APPLIED:
        raise ValueError(f"bad jev_applied={applied!r} (expect one of {JEV_APPLIED})")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError(f"bad jev_threshold={threshold!r} (expect number)")
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE message SET jev_applied=?, jev_threshold=? WHERE id=? AND role='assistant'",
            (applied, float(threshold), turn_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def jev_applied_counts(day: str | None = None, path: str | None = None) -> dict:
    """`/health` 用：某一天（預設今天，`created_at` 的日期前綴）各 `jev_applied` 態的列數。

    **一次 `GROUP BY`**；五態一律回（沒有的補 0）；NULL（router 沒開／舊列）不計。
    """
    d = day or datetime.now().strftime("%Y-%m-%d")
    out = {k: 0 for k in JEV_APPLIED}
    conn = _connect(path)
    try:
        for r in conn.execute(
            "SELECT jev_applied AS a, COUNT(*) AS n FROM message "
            "WHERE role='assistant' AND jev_applied IS NOT NULL AND substr(created_at,1,10)=? "
            "GROUP BY jev_applied", (d,)
        ).fetchall():
            if r["a"] in out:
                out[r["a"]] = int(r["n"])
        return out
    finally:
        conn.close()


def record_prompt_stats(turn_id: int, prompt_chars: int, history_turns: int,
                        path: str | None = None) -> bool | None:
    """U8③：把這一輪**送出當下的上下文大小**記到 assistant 列的兩個加性欄。

    回傳：`True`＝真的寫到一列；**`None`＝查無那一列／那不是 assistant 列**（不寫、不 raise
    ——同 `record_jev` 的「拿錯 id 就一列都不動」，觀測動作不該用例外吵醒呼叫端）。

    `prompt_chars`＝app 側送出的 `message` 字元數（ctx_block＋使用者原句；**不含 persona 與
    history**，persona 在 gateway 側、app 量不到）。
    `history_turns`＝cap 後餵進去的歷史**則數**（user＋assistant 各算一則，**不是對數**）。

    鐵則（與 `record_jev` 同一組）：
      ① **絕不碰 `status`**（U5 狀態機 CAS 的守衛）；本函式與這一輪答得成不成完全無關。
      ② `role='assistant'` 守衛＝只寫佔位／答案列；拿 user 列 id 來寫**一列都不動**。
      ③ **不帶 `aid`**：系統自己的觀測紀錄，`turn_id` 由背景 worker 從自己剛建的那輪拿。
      ④ 兩個值**必須是非負 int**，否則 `ValueError`（連 `bool` 都拒——`True` 當字元數是 bug
         不是資料）。這兩欄之後要拿來做成本統計，**靜默吞壞值＝統計表永遠在說謊**。
      ⑤ 可重複呼叫（後寫覆蓋前寫）；retry 重跑同一輪會用同一份 prompt 再寫一次，值相同。
    """
    for name, val in (("prompt_chars", prompt_chars), ("history_turns", history_turns)):
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise ValueError(f"bad {name}={val!r} (expect non-negative int)")
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE message SET prompt_chars=?, history_turns=? WHERE id=? AND role='assistant'",
            (prompt_chars, history_turns, turn_id),
        )
        conn.commit()
        return True if cur.rowcount == 1 else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# U9 每日 LLM 呼叫保險絲（2026-09-22；`SPEC-HERMES-TRUST.md` §12.10）
# ---------------------------------------------------------------------------

def llm_budget_day(now: datetime | None = None) -> str:
    """今天的桶鍵＝**Asia/Taipei 日曆日**（`YYYY-MM-DD`）。

    ⚠ 實作上就是 `datetime.now()` 的日期——本 repo 的慣例是「Board 主機跑在台北牆鐘、
    MES/W8 皆台北 naive」（同 `_now()`）。**刻意不引 zoneinfo**：換一種取法就等於在
    這顆 DB 裡放進第二種時間語意，跨日那一刻兩邊會不一致。`now` 參數只給測試用。
    """
    return (now or datetime.now()).strftime("%Y-%m-%d")


def llm_budget_cap(environ=None, kind: str = "assistant") -> int:
    """該桶的每日上限（次／天）。**一個 kind 一個桶、一個 env**（2026-09-22 Chris 裁 (a)）：

    - `kind="translate"` → env `BOARD_TRANSLATE_DAILY_MAX`，未設＝`TRANSLATE_DAILY_MAX_DEFAULT`
      （600）；其餘（`assistant`）→ env `BOARD_LLM_DAILY_MAX`，未設＝`LLM_DAILY_MAX_DEFAULT`（200）。
    - `0` ＝ **不限**（明示關掉那一桶保險絲的唯一寫法）。
    - 垃圾值（非整數、負數）→ **退回該桶的預設值**，不是不限：保險絲讀不懂設定時要**繼續保護**，
      「設錯一個字就等於全開」是這種閘最典型的失效方式。
    - `kind` 預設 `"assistant"` ＝ **向後相容**：既有呼叫端 `llm_budget_cap({})` 仍回 200。
      兩個 env **互不外溢**（設 `BOARD_LLM_DAILY_MAX` 不會改到翻譯桶，反之亦然）。
    """
    env_key, default = ((ENV_TRANSLATE_DAILY_MAX, TRANSLATE_DAILY_MAX_DEFAULT)
                        if kind == "translate"
                        else (ENV_SUMMARY_DAILY_MAX, SUMMARY_DAILY_MAX_DEFAULT)
                        if kind == "summary"
                        else (ENV_LLM_DAILY_MAX, LLM_DAILY_MAX_DEFAULT))
    raw = str((environ if environ is not None else os.environ)
              .get(env_key, "")).strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return n if n >= 0 else default


def _llm_used(conn: sqlite3.Connection, day: str, kind: str | None = None) -> int:
    """當日已用次數。`kind` 給了＝**只算那一桶**（防線用）；`None`＝所有 kind 合計（觀測用）。"""
    if kind is None:
        r = conn.execute("SELECT COALESCE(SUM(n), 0) AS n FROM llm_budget WHERE day=?",
                         (day,)).fetchone()
    else:
        r = conn.execute("SELECT COALESCE(SUM(n), 0) AS n FROM llm_budget "
                         "WHERE day=? AND kind=?", (day, kind)).fetchone()
    return int(r["n"] or 0)


def llm_budget_check(kind: str, path: str | None = None, day: str | None = None,
                     cap: int | None = None) -> tuple[bool, int, int]:
    """還能不能再打一次 gateway？回 `(allowed, used, cap)`。**只讀、不加數**。

    `allowed` ＝ `cap <= 0`（不限）或 `used < cap`。`used`／`cap` ＝ **`kind` 自己那一桶**
    （2026-09-22 裁 (a)；以前是兩 kind 合計對一個上限）⇒ 翻譯燒光不會把助理鎖在門外，反之亦然。
    `kind` 必須在 `LLM_KINDS` 裡（打錯字要當場炸，不要靜默記到一個沒人看的桶）。
    DB 缺表 → `_connect` 的 `executescript` 當場建（`CREATE TABLE IF NOT EXISTS`）。
    """
    if kind not in LLM_KINDS:
        raise ValueError(f"bad kind={kind!r} (expect one of {LLM_KINDS})")
    cap = llm_budget_cap(kind=kind) if cap is None else int(cap)
    day = day or llm_budget_day()
    conn = _connect(path)
    try:
        used = _llm_used(conn, day, kind)
    finally:
        conn.close()
    return ((cap <= 0 or used < cap), used, cap)


def llm_budget_commit(kind: str, path: str | None = None,
                      day: str | None = None) -> int:
    """**真的送出一次 gateway 呼叫**時加一。回加完之後**該 kind 當日的次數**
       （2026-09-22 裁 (a) 起是「那一桶」的數、不再是兩 kind 合計；目前無呼叫端用這個回傳值，
       app.py 兩處 `llm_budget_commit(...)` 都是丟棄回傳的 fire-and-forget）。

    鐵則：
      ① **計的是送出去的那一次，不是收單的那一次**——被保險絲擋下、被速率限制擋下、
         或翻譯快取命中（根本沒打 gateway）的，一次都不計。
      ② upsert（`ON CONFLICT … n = n + 1`）＝跨重啟持久、併發安全（單一 UPDATE 語句）。
      ③ **絕不碰 `message` 表**：這顆表與那一輪答得成不成完全無關（同 `record_prompt_stats`
         的「觀測動作不該吵醒呼叫端」，但本函式是**防線**、寫失敗要讓呼叫端看得到）。
      ④ 跨日自動重置——沒有清理作業：新的一天就是新的一個 `day` 鍵，舊列留著當歷史。
    """
    if kind not in LLM_KINDS:
        raise ValueError(f"bad kind={kind!r} (expect one of {LLM_KINDS})")
    day = day or llm_budget_day()
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO llm_budget(day, kind, n) VALUES(?, ?, 1) "
            "ON CONFLICT(day, kind) DO UPDATE SET n = n + 1",
            (day, kind))
        total = _llm_used(conn, day, kind)
        conn.commit()
        return total
    finally:
        conn.close()


def llm_budget_status(path: str | None = None, day: str | None = None) -> dict:
    """`/health` 的加性欄：`{day, used, cap, by_kind, caps, translate_used}`（唯讀、零副作用）。

    ⚠ **`used`／`cap` 自 2026-09-22 裁 (a) 起＝「助理那一桶」**，以前是兩 kind 合計對一個上限。
      為什麼不把 `used` 改成 dict 或直接拿掉：① 分桶之後「合計對一個上限」這個數**沒有任何
      閘在用**，留著等於留一個沒有意義的數；② Chris 要在 `/health` 一眼看到的就是助理那條
      保險絲（頁面翻譯燒得快是常態、不是警訊）⇒ 讓最常看的那一格保持在最短路徑上。
      兩桶的完整現況仍在加性的 `caps`（各桶上限）與 `translate_used`（翻譯桶已用）；
      `by_kind` 原封不動（逐 kind 落列數）。

    ⚠ **揭露只到 `/health`，不加任何頁面 UI**（[[ui-addition-restraint]]）——保險絲是
    例外才會發生的事，為它長一塊常駐版面的成本比效益高；真的燒到上限時，使用者看到的是
    那一則誠實泡泡（助理）或既有的錯誤 chip（翻譯），那才是他需要知道的那一刻。
    """
    day = day or llm_budget_day()
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT kind, n FROM llm_budget WHERE day=? ORDER BY kind", (day,)).fetchall()
    finally:
        conn.close()
    by_kind = {r["kind"]: int(r["n"] or 0) for r in rows}
    return {"day": day,
            "used": by_kind.get("assistant", 0), "cap": llm_budget_cap(kind="assistant"),
            "by_kind": by_kind,
            "caps": {"assistant": llm_budget_cap(kind="assistant"),
                     "translate": llm_budget_cap(kind="translate")},
            "translate_used": by_kind.get("translate", 0),
            # #167 P3 加性：摘要桶（`caps` 兩鍵刻意不動）
            "summary_used": by_kind.get("summary", 0),
            "summary_cap": llm_budget_cap(kind="summary")}


# ---------------------------------------------------------------------------
# U10 `tool_call`：turn ↔ 工具呼叫日誌（2026-09-22；`SPEC-HERMES-TRUST.md` §12.12／§13 ☐2 (a)）
# ---------------------------------------------------------------------------
# **第四張表、零遷移腳本**（`_SCHEMA` 的 `CREATE TABLE IF NOT EXISTS`＝缺表自建，同 `llm_budget`）。
# 寫入端＝`board_mcp._record_tool_call`（`/mcp` 收到 `X-Board-Turn` 才寫；沒 header ⇒ **零列**）。
# 鐵則三條（與 `llm_budget_commit` 同族，但本表是**觀測**不是防線）：
#   ① **絕不碰 `message` 表**、② **絕不碰 `llm_budget`**（`/mcp` 純取數、不燒 LLM 配額）、
#   ③ 寫失敗由**呼叫端**吞（觀測動作不得害死一次工具呼叫）——本函式自己照樣 raise，
#      因為 selftest 要驗得到參數驗證；吞的紀律寫在 `board_mcp._record_tool_call`。

_TOOL_CALL_RESULT_MAX = 1_000_000   # result JSON 位元組上限；超過只存截斷標記（見下）


def _utc_now() -> str:
    """UTC ISO-8601 秒字串 `YYYY-MM-DDTHH:MM:SS`（**不帶時區後綴**）。

    ⚠ U13（2026-09-25）起 writer 不再直接用它，改用 `_now_pair()`（同一瞬間的台北＋UTC 兩個字串）；
    保留本函式＝既有呼叫面不動。工具呼叫的人可讀時刻仍可看 `provenance.fetched_at`
    （那一份與頁面同口徑），與本表時刻**不保證相等**，這是設計不是 bug。
    """
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _now_pair() -> tuple[str, str]:
    """同一瞬間 → `(台北牆鐘, UTC)` 兩個 ISO 秒字串（皆無時區後綴）。

    台北那個與 `_now()` 同形（`datetime.now().isoformat(timespec="seconds")`＝本機牆鐘）⇒
    `tool_call.created_at` 與 `message.created_at` 可直接比對／相減（四十七 ▶⑥：原本差 8h）。
    UTC 那個進加性欄 `created_at_utc`（CSV `created_at_utc` 欄與 `exported_at` 同牆鐘、可相減）。
    ⚠ 兩個取自**同一個** `datetime.now(timezone.utc)`，不是各叫一次 now（跨秒邊界會差 1 秒）。
    """
    from datetime import timezone
    t = datetime.now(timezone.utc)
    local = t.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    return local, t.strftime("%Y-%m-%dT%H:%M:%S")


def tool_call_record(turn_id: int, tool: str, params_hash: str, provenance: dict,
                     result, path: str | None = None) -> int:
    """落一列工具呼叫日誌；回新列 id。

    參數：
      `turn_id`    對應 `message.id`（assistant 佔位列的 id＝前端的 turn id）。必須是 int ≥ 1
                   （`bool` 也拒——`True` 會悄悄變成 turn 1）；否則 `ValueError`。
      `tool`       非空字串（工具名）；否則 `ValueError`。
      `params_hash` 字串（`board_mcp.params_hash()` 的 sha256 前 8 碼）；非字串 `ValueError`。
      `provenance` §3.1 信封 dict → `json.dumps(..., ensure_ascii=False, sort_keys=True,
                   default=str)`（**sort_keys**＝同一份信封逐位元可比）。非 dict `ValueError`。
      `result`     工具回傳 payload（**已去掉 `provenance` 鍵**，由呼叫端負責）→
                   `json.dumps(..., ensure_ascii=False, default=str)`（**不 sort_keys**＝
                   保留工具自己的鍵序，匯出 CSV（#153）要的是原樣）。
                   ⚠ 序列化後**超過 1,000,000 位元組** ⇒ 改存
                   `{"_truncated": true, "bytes": <n>}`（整列不會因為一個巨大回傳而爆表；
                   `_truncated` 是**誠實標記**，讀端看到它就知道原值沒留）。
                   序列化不了（`default=str` 都救不回來）⇒ `ValueError`。

    連線自開自關（每操作一條、不共享），單一 INSERT。
    """
    if isinstance(turn_id, bool) or not isinstance(turn_id, int) or turn_id < 1:
        raise ValueError(f"bad turn_id={turn_id!r} (expect int >= 1)")
    if not isinstance(tool, str) or not tool:
        raise ValueError(f"bad tool={tool!r} (expect non-empty str)")
    if not isinstance(params_hash, str):
        raise ValueError(f"bad params_hash={params_hash!r} (expect str)")
    if not isinstance(provenance, dict):
        raise ValueError(f"bad provenance={provenance!r} (expect dict)")
    try:
        prov_json = json.dumps(provenance, ensure_ascii=False, sort_keys=True, default=str)
        res_json = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        raise ValueError(f"tool_call payload not JSON-able: {e}") from e
    n_bytes = len(res_json.encode("utf-8"))
    if n_bytes > _TOOL_CALL_RESULT_MAX:
        res_json = json.dumps({"_truncated": True, "bytes": n_bytes}, ensure_ascii=False)
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        # U13：`created_at`＝台北牆鐘（同 message）、`created_at_utc`＝同一瞬間的 UTC
        _ts_local, _ts_utc = _now_pair()
        cur = conn.execute(
            "INSERT INTO tool_call(turn_id, tool, params_hash, provenance, result_json, "
            "created_at, created_at_utc) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (int(turn_id), tool, params_hash, prov_json, res_json, _ts_local, _ts_utc))
        new_id = int(cur.lastrowid)
        conn.commit()
        return new_id
    finally:
        conn.close()


def tool_calls_for_turn(turn_id: int, path: str | None = None) -> list[dict]:
    """這一輪的工具呼叫日誌，**依 `id` 遞增**（＝呼叫順序）。查無 → `[]`。

    每筆 dict：`id`／`turn_id`／`tool`／`params_hash`／`provenance`（**解析後的 dict**）／
    `result`（解析後的值；截斷過的會是 `{"_truncated": true, "bytes": n}`）／`created_at`
    ／`result_json`（**存進去的那一份原字串，一位元未動**）。
    解析不了的（理論上不該發生）⇒ 該欄原字串直接回（**不吞成 None**：壞資料要看得見）。
    ⚠ `result_json` 是 #153 匯出 CSV 的那一格：匯出要的是**工具回傳的那一份原值**
      （C-HT1），重新 `json.dumps` 一次就可能換掉鍵序／空白／`ensure_ascii` ⇒ 不是原值。
    U13（2026-09-25）加性鍵 `created_at_utc`＝**恆為 UTC**：新列取 DB 同名欄；舊列（NULL）取
    `created_at`（那時就是 UTC 寫的）。⇒ 要 UTC 的讀者（匯出 CSV／JSON）一律讀這個鍵；
    `created_at` 新列起是台北牆鐘（同 `message.created_at`），**舊列不回溯**。
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT id, turn_id, tool, params_hash, provenance, result_json, created_at, "
            "COALESCE(created_at_utc, created_at) AS created_at_utc "
            "FROM tool_call WHERE turn_id=? ORDER BY id", (int(turn_id),)).fetchall()
    finally:
        conn.close()

    def _load(raw):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    return [{"id": int(r["id"]), "turn_id": int(r["turn_id"]), "tool": r["tool"],
             "params_hash": r["params_hash"], "provenance": _load(r["provenance"]),
             "result": _load(r["result_json"]), "created_at": r["created_at"],
             "result_json": r["result_json"], "created_at_utc": r["created_at_utc"]}
            for r in rows]


def tool_call_count(turn_id: int, path: str | None = None) -> int:
    """這一輪的工具呼叫**列數**（單一 `SELECT COUNT(*)`，走 `tool_call_turn` 索引）。

    只給**單則**渲染路徑用（poll／retry／rate 的 `_assist_turn_resp`）——模板據此決定要不要
    渲染匯出 CSV 的小連結（C-HT6 例外才出現：零列 ⇒ 零版位、零 markup）。
    ⚠ **panel 一次渲染整段對話時不要逐則呼叫它**（N+1）；那條路徑由 `get_messages` 的
      一次 `GROUP BY` 供數。壞 `turn_id`（非整數）⇒ `ValueError`（同 sqlite 的 `int()`）。
    """
    conn = _connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM tool_call WHERE turn_id=?",
                                (int(turn_id),)).fetchone()["n"])
    finally:
        conn.close()


def _json_or_raw(raw):
    """解析 JSON 字串；解析不了 ⇒ 原值（同 `tool_calls_for_turn._load`：壞資料要看得見、不吞成 None）。"""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


# ---------------------------------------------------------------------------
# U14 #137 A3 面板「依據」折疊區的 chip（`SPEC-HERMES-TRUST.md` §5.1）
# ---------------------------------------------------------------------------
# 每顆 chip 印：工具名 · 時間窗 · 新鮮度 · 口徑 · 信心；可點 → 信封的 `page`。
# ★ 純函式、零 I/O：輸入是**已落庫的**信封（`tool_call.provenance`），輸出只給模板用。
# ★ 信封是工具產的、但經 DB 回讀 ⇒ 一律當不可信字串處理：字串化＋截長；`page` 只收
#   site-relative 安全形（`/` 開頭、非 `//`、無反斜線／空白／引號／角括號／**未代入的 `{…}` 樣板**
#   ——`explain_metric` 的 `page` 是 `verify.page_url_tpl`，可能是 `/station/{cls}` 這種樣板，
#   連過去就是一個打不開的頁＝比不連更糟，§3.1a「掛錯比留白糟」）。過不了 ⇒ `None`＝不掛連結。
# ★ 缺欄 ⇒「—」（不是空白、不是 0；同 CSV 匯出「取不到就是空格」的語意在 UI 端的形）。
# ★ 枚舉值（freshness／caliber／confidence）**原樣顯示、不翻譯**：它們是信封的 canonical 值，
#   與 CSV 匯出同一個字（使用者拿面板去對 CSV 對得上）；只有折疊區標題走 i18n。
_BASIS_PAGE_RE = re.compile(r"^/(?!/)[A-Za-z0-9_\-./?=&%:+,~]*$")
_BASIS_PAGE_MAX = 256
_BASIS_FIELD_MAX = 64
_BASIS_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?$")


def _basis_str(v, cap: int = _BASIS_FIELD_MAX) -> str:
    """None／空 ⇒ ""；其餘 str() 後去控制字元、截到 `cap`（UI 用，不是事實存證）。"""
    if v is None:
        return ""
    s = "".join(c for c in str(v) if ord(c) >= 0x20 and ord(c) != 0x7F).strip()
    return s[:cap]


def _basis_window(win) -> tuple[str, str]:
    """信封 `window` → `(chip 用的短形, tooltip 用的全形)`；無窗 ⇒ `("—", "")`。

    同日 ⇒ `YYYY-MM-DD HH:MM–HH:MM`；跨日 ⇒ `YYYY-MM-DD HH:MM → YYYY-MM-DD HH:MM`
    （chip 只到分鐘，秒在 tooltip 全形裡）；格式認不得 ⇒ 短形＝全形截 48 字。"""
    if not isinstance(win, dict):
        return "—", ""
    fs, ts = _basis_str(win.get("from")), _basis_str(win.get("to"))
    if not fs and not ts:
        return "—", ""
    full = f"{fs or '…'} → {ts or '…'}"
    mf, mt_ = _BASIS_TS_RE.match(fs), _BASIS_TS_RE.match(ts)
    if mf and mt_:
        if mf.group(1) == mt_.group(1):
            return f"{mf.group(1)} {mf.group(2)}–{mt_.group(2)}", full
        return f"{mf.group(1)} {mf.group(2)} → {mt_.group(1)} {mt_.group(2)}", full
    return full[:48], full


def _basis_page(page) -> str | None:
    """信封 `page` → 可安全當 href 的 site-relative 路徑，或 `None`（不掛連結）。"""
    if not isinstance(page, str) or not page or len(page) > _BASIS_PAGE_MAX:
        return None
    return page if _BASIS_PAGE_RE.match(page) else None


def basis_chip(tool, provenance) -> dict:
    """一列 `tool_call` → 一顆「依據」chip（§5.1 五欄＋連結＋tooltip 素材）。

    回傳鍵定死：`tool`／`window`／`window_full`／`freshness`／`caliber`／`confidence`／
    `fetched_at`／`evidence_ref`／`page`（`None`＝不掛連結）。`provenance` 不是 dict
    （壞資料）⇒ 五欄除工具名外一律「—」、`page=None`——**不 raise**（觀測面不得害死一則渲染）。
    """
    p = provenance if isinstance(provenance, dict) else {}
    win, win_full = _basis_window(p.get("window"))
    return {
        "tool": _basis_str(tool) or "—",
        "window": win,
        "window_full": win_full,
        "freshness": _basis_str(p.get("freshness")) or "—",
        "caliber": _basis_str(p.get("caliber")) or "—",
        "confidence": _basis_str(p.get("confidence")) or "—",
        "fetched_at": _basis_str(p.get("fetched_at")),
        "evidence_ref": _basis_str(p.get("evidence_ref"), 128),
        "page": _basis_page(p.get("page")),
    }


def tool_basis_for_turn(turn_id: int, path: str | None = None) -> list[dict]:
    """這一輪的「依據」chip 清單（**依 `id` 遞增**＝呼叫順序）；查無 → `[]`。

    只給**單則**渲染路徑（`app._assist_turn_resp`，只有 done 才呼叫）；panel 走 `get_messages`
    同一次查詢（避免 N+1）。壞 `turn_id`（非整數）⇒ `ValueError`（同 `tool_call_count`）。
    只讀 `tool`／`provenance` 兩欄——`result_json` 可能上 MB，面板用不到就不搬。
    """
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT tool, provenance FROM tool_call WHERE turn_id=? ORDER BY id",
                            (int(turn_id),)).fetchall()
    finally:
        conn.close()
    return [basis_chip(r["tool"], _json_or_raw(r["provenance"])) for r in rows]


def close(aid: str, cid: int, path: str | None = None) -> None:
    """結束對話（設 closed_at；冪等）。非本 aid 擁有 → AssistantError（不動他人資料）。"""
    conn = _connect(path)
    try:
        r = conn.execute(
            "SELECT closed_at FROM conversation WHERE id=? AND aid=?", (cid, aid)
        ).fetchone()
        if r is None:
            raise AssistantError("conversation not found or not owned")
        if r["closed_at"] is not None:
            return  # 已結束：冪等 no-op
        now = _now()
        conn.execute("BEGIN")
        conn.execute("UPDATE conversation SET closed_at=?, updated_at=? WHERE id=?", (now, now, cid))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# U12 #177 失敗答法守門（2026-09-25；`ROADMAP.md` #177；`SPEC-HERMES-TRUST.md` §12.16）
# ---------------------------------------------------------------------------
# 病灶（`_ASSISTANT-EVAL-20260924-TOOLKIT2.md` Q1／Q3，MES 離網那一晚）：
#   Q1「工具回傳 found=false，沒有任何量測段…」＝把工具欄位照抄給使用者；
#   Q3「目前工具無法回答…」＝把「這次沒拿到」講成能力缺口（使用者會以為沒有這種查詢）。
# 兩層分工（**同一件事的語意層與確定性層，缺一不可**）：
#   ① `hermes/gateway.py` persona 規則 8 ＝**語意**：白話兩句（連不上／查無）＋禁字＋放行例。
#      住 WSL、要 `deploy-hermes.ps1` 重推才生效；而且模型是機率性的。
#   ② 本函式 ＝**確定性**：`app._assist_worker` 落庫前**一次**。只做兩件事——
#      (a) 對外文字裡的內部字樣（`found=…`／`unavailable_reason`（含值）／閉集 reason 碼／`error:…`／
#          例外類名／Traceback／pyodbc／SQLSTATE…）→ 換成白話；整句**只剩**內部字樣且**沒有獨立數字**
#          ⇒ 整句拿掉（「工具回傳 found=false」這種句子沒有可保留的事實）。
#      (b) 本輪資料工具**全部**取不到（或 (a) 抓到失敗字樣、且本輪沒有任何一支成功）而回覆裡**沒有**
#          「不代表…」那一句 ⇒ 句尾補一句標準白話（`FAIL_NOTICE`，與 persona 的 GOOD 句**逐字相同**，
#          smoke `[assist-plain]` 跨檔釘）。
#   **不改數字、不改沒命中的句子**：零命中且不必補句 ⇒ 回傳**同一個字串物件**（逐位元不變）。
#   ⚠ 放行例（**不是失敗**）：`found=true` ＋ `unavailable_reason` ∈ `FOUND_OK_REASONS`
#      （＝`mes_segment.FOUND_UNAVAILABLE_OK`；smoke 釘子集關係）⇒ 換成它**自己的**白話
#      （「MES 沒有登記這站的限值」），**永不**套「連不上／查無」那兩句、也不因它補句。
#   ⚠ 本 store 零相依（不 import mes_*／board_mcp）：閉集碼在這裡**抄一份**，漂移由 smoke 跨檔釘抓。

#: 標準白話兩句（**與 `hermes/gateway.py` 規則 8 的 GOOD 句逐字相同**；改一邊＝smoke 紅）。
FAIL_NOTICE = {
    "unreachable": {"zh": "這次拿不到資料（資料來源連不上），不代表沒有。",
                    "en": "I couldn't get the data this time (the data source couldn't be reached)"
                          " — that does not mean there is none."},
    "not_found": {"zh": "這次查不到這筆紀錄，不代表沒有發生。",
                  "en": "No record came back this time — that does not mean it didn't happen."},
}
#: 內部字樣換成的白話片語（句中替換用；整句刪除時不用）。
_FAIL_PLAIN = {
    "unreachable": {"zh": "資料來源連不上", "en": "the data source couldn't be reached"},
    "not_found": {"zh": "查無紀錄", "en": "no record came back"},
    "found": {"zh": "有紀錄", "en": "a record was found"},
    "reason": {"zh": "原因", "en": "reason"},
}
#: 放行例：`found=true` 仍可帶的 `unavailable_reason`（是答案、不是取不到）→ 它自己的白話。
FOUND_OK_REASONS = {
    "no_pass_criteria_limits": {"zh": "MES 沒有登記這站的限值",
                                "en": "MES holds no limits for this station"},
}
#: 有專屬白話的查無碼（換字、**不整句刪**＝保住「這是什麼情形」；demo 那句尤其不能丟：persona 規則 7）。
_REASON_PLAIN = {
    "not_in_demo_fixture": {"zh": "離線預覽資料裡沒有這筆", "en": "this record isn't in the offline preview data"},
    "station_unsupported": {"zh": "這站不在這項查詢的範圍內", "en": "this station isn't covered by this lookup"},
}
#: 中性碼（#175）：換成自己的白話、**不算失敗**（不觸發「不代表沒有發生」補句）——
#: 知識卡查無＝「卡片沒寫到這個主題」，不是「這次沒拿到資料」也不是「查無紀錄」。
_REASON_NEUTRAL = {
    "no_matching_card": {"zh": "知識卡裡沒有寫到這個主題", "en": "no knowledge card covers this topic"},
}
#: 「連不上／這次沒拿到」類（`error:` 前綴與 `*_module_missing` 另以規則判）。
UNREACHABLE_REASONS = frozenset((
    "timeout", "db_unavailable", "unavailable", "unreachable", "not_configured", "board_not_warmed",
    "calendar_absent", "snapshot_unsupported", "fixture_unavailable", "bad_shape"))
#: 「查無」類（其餘未列的值也歸這類＝保守：講「查不到、不代表沒有」永遠不會說錯話）。
NOT_FOUND_REASONS = frozenset((
    "not_in_demo_fixture", "class_not_in_mes", "no_production_request_matched", "no_material_matched",
    "wo_not_found", "no_route", "station_unsupported", "serial_not_found_at_station", "serial_not_found",
    "recipe_not_found", "eid_not_found", "no_segments_in_window", "no_events_in_window",
    "no_module_edges", "no_calibration_records", "no_calibration_in_window"))
#: 解析／目錄類工具：本身成功不算「這輪拿到資料」、失敗也不算資料失敗（判本輪結果時略過）。
_GUARD_NEUTRAL_TOOLS = frozenset(("ping", "list_stations", "machine_lookup", "list_metrics",
                                  "explain_metric",
                                  # #175：文件參考工具——查無卡不是「這次沒拿到資料」，不補「不代表沒有發生」那句
                                  "knowledge_card"))

# ⚠ 一律用 ASCII 邊界而非 `\b`：Python 的 `\b` 把中文字當詞字元 ⇒「回傳found=false」會漏抓。
_GL, _GR = r"(?<![A-Za-z0-9_])", r"(?![A-Za-z0-9_])"
_GQ = "[`\"'“”「」]?"
_GUARD_EXC = ("Operational", "Programming", "Interface", "Database", "Integrity", "Data",
              "NotSupported", "Internal", "Timeout", "Connection", "ConnectionRefused",
              "ConnectionReset", "BrokenPipe", "OS", "Key", "Attribute", "Type", "Value", "Runtime",
              "JSONDecode", "HTTP", "URL", "Index", "Import", "ModuleNotFound", "FileNotFound",
              "Permission")
_GUARD_CODES = sorted((NOT_FOUND_REASONS | set(FOUND_OK_REASONS) | set(_REASON_PLAIN) | set(_REASON_NEUTRAL)
                       | {r for r in UNREACHABLE_REASONS if "_" in r}), key=len, reverse=True)
# (regex, kind)；同一處多支命中 ⇒ 取起點最早、最長的那一支（見 `_guard_scan`）。
_GUARD_RES = (
    (re.compile(r"[`\"']?" + _GL + r"unavailable_reason" + _GR + r"[`\"']?"
                r"(?:\s*[=:：]\s*" + _GQ + r"(?P<v>[A-Za-z][A-Za-z0-9_:.\-]*)" + _GQ + r")?"), "ur"),
    (re.compile(r"[`\"']?" + _GL + r"found" + _GR + r"[`\"']?\s*[=:：]\s*[`\"']?"
                r"(?P<v>false|true|null|none)" + _GR + r"[`\"']?", re.I), "found"),
    (re.compile(r"`?" + _GL + r"error:[A-Za-z_][A-Za-z0-9_.]*`?"), "err"),
    (re.compile(r"`?" + _GL + r"(?:[a-z_][a-z0-9_]*\.)*(?:" + "|".join(_GUARD_EXC) + r")Error"
                + _GR + r"`?"), "err"),
    (re.compile(_GL + r"(?:Traceback(?: \(most recent call last\):?)?|pyodbc|error_detail|SQLSTATE"
                r"|08001|08S01|HYT00|HYT01|42S02|28000)" + _GR + r"|Named Pipes Provider"), "err"),
    (re.compile(r"`?" + _GL + r"(?P<v>" + "|".join(_GUARD_CODES)
                + r"|[a-z]+(?:_[a-z]+)*_module_missing)" + _GR + r"`?"), "code"),
)
#: 句中「獨立數字」＝事實（不是 EID／序號／工單號裡的數字）：有它 ⇒ 該句只換字、不整句刪。
_GUARD_FACT_NUM = re.compile(r"(?<![A-Za-z0-9_\-/.])\d+(?:[.,]\d+)?(?![A-Za-z0-9_\-/])")
_GUARD_NOTICE = re.compile(r"不代表|並不表示|不表示|does not mean|doesn't mean|not mean", re.I)
_GUARD_BARE_LINE = re.compile(r"^[\s\-*•·\d.、)）(（:：，,。]*$")


def reason_class(reason) -> str:
    """`unavailable_reason` 值 → `"unreachable"`（連不上／這次沒拿到）／`"not_found"`（查無）／
       `"ok"`（放行例、是答案）。空值與未知值 ⇒ `"not_found"`（保守）。"""
    r = (reason or "").strip() if isinstance(reason, str) else ""
    if not r:
        return "not_found"
    if r in FOUND_OK_REASONS:
        return "ok"
    if r.startswith("error:") or r in UNREACHABLE_REASONS or r.endswith("_module_missing"):
        return "unreachable"
    return "not_found"


def _guard_result_class(res):
    """單一工具回傳 → `"unreachable"`／`"not_found"`／`None`（＝這支拿到東西，或不是「取不到」形）。
       只認兩種失敗形：`found is False`（有這鍵且為 False）與 `{"error": "unavailable"}`（模組缺）。
       400 形（`bad_wo_id`／`bad_days`…）＝輸入要改、不是取不到 ⇒ `None`。"""
    if not isinstance(res, dict):
        return None
    if res.get("found") is False:
        c = reason_class(res.get("unavailable_reason"))
        return "not_found" if c == "ok" else c
    if res.get("error") == "unavailable":
        return "unreachable"
    return None


def turn_outcome(tool_calls) -> str | None:
    """本輪工具呼叫（`tool_calls_for_turn()` 的列，或 `{"tool","result"}` dict）→
       `None`（沒有資料工具呼叫＝不知道）／`"ok"`（資料工具全成功）／`"mixed"`（有成有敗）／
       `"unreachable"`／`"not_found"`（**全部**取不到；有任一支連不上 ⇒ unreachable）。
       解析／目錄類（`_GUARD_NEUTRAL_TOOLS`）略過。"""
    if not tool_calls:
        return None
    fails, ok = [], 0
    for c in tool_calls:
        if not isinstance(c, dict) or c.get("tool") in _GUARD_NEUTRAL_TOOLS:
            continue
        k = _guard_result_class(c.get("result"))
        if k is None:
            ok += 1
        else:
            fails.append(k)
    if not fails:
        return "ok" if ok else None
    if ok:
        return "mixed"
    return "unreachable" if "unreachable" in fails else "not_found"


def _guard_lang(locale, text) -> str:
    s = (locale or "").strip().lower() if isinstance(locale, str) else ""
    if s:
        return "zh" if s.startswith("zh") else "en"
    return "zh" if re.search(r"[一-鿿]", text or "") else "en"


def _guard_classify(kind, m):
    """一個命中 → (cls, generic, phrase)。`cls`∈{unreachable, not_found, ok, None}；
       `generic`＝這個字樣本身沒有可保留的資訊（整句只剩它時可整句刪）。"""
    if kind == "found":
        if m.group("v").lower() == "true":
            return "ok", False, _FAIL_PLAIN["found"]
        return "not_found", True, _FAIL_PLAIN["not_found"]
    if kind == "err":
        return "unreachable", True, _FAIL_PLAIN["unreachable"]
    v = m.group("v")                                     # "ur"（值可缺）或 "code"
    if kind == "ur" and not v:
        return None, True, _FAIL_PLAIN["reason"]
    if v in _REASON_NEUTRAL:                             # #175：換白話、不算失敗
        return None, False, _REASON_NEUTRAL[v]
    c = reason_class(v)
    if c == "ok":
        return "ok", False, FOUND_OK_REASONS[v]
    if v in _REASON_PLAIN:
        return c, False, _REASON_PLAIN[v]
    return c, True, _FAIL_PLAIN[c]


def _guard_scan(s):
    spans = []
    for rx, kind in _GUARD_RES:
        for m in rx.finditer(s):
            if m.end() > m.start():
                spans.append((m.start(), m.end(), kind, m))
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    kept, last = [], -1
    for st, en, kind, m in spans:
        if st >= last:
            cls, generic, phrase = _guard_classify(kind, m)
            kept.append((st, en, cls, generic, phrase, m.group(0)))
            last = en
    return kept


def _guard_split_sentences(line):
    """一行 → 句子片段（**串回去＝原行逐字**）。句尾＝中英句號類、分號；英文句點只在其後是
       空白或行尾時才算（`6.4 h`、`EID-13219.` 以外的小數點不切）。"""
    out, start, i, n = [], 0, 0, len(line)
    while i < n:
        ch = line[i]
        if ch in "。！？；!?;" or (ch == "." and (i + 1 == n or line[i + 1].isspace())):
            j = i + 1
            while j < n and line[j] in "。！？；!?;.":
                j += 1
            while j < n and line[j] in " \t":
                j += 1
            out.append(line[start:j])
            start = i = j
            continue
        i += 1
    if start < n:
        out.append(line[start:])
    return out


def plain_failure_guard(text, locale=None, tool_calls=None):
    """#177 確定性守門（見本節檔頭）。回 `(text, hits)`；`hits` 空 ⇒ `text` 是**原物件**。

    `locale`＝turn 的 `zh-TW`／`en`（缺 ⇒ 看文字有沒有中文）；`tool_calls`＝本輪
    `tool_calls_for_turn()` 的列（缺／空 ⇒ 只靠文字裡的字樣判）。**純函式、永不 raise**
    （呼叫端仍自帶 try：守門失敗絕不害死一輪回答）。"""
    if not isinstance(text, str) or not text:
        return text, []
    lang = _guard_lang(locale, text)
    hits, tok_cls, changed = [], set(), False
    new_lines = []
    for line in text.split("\n"):
        pieces = _guard_split_sentences(line)
        out, line_changed = [], False
        for p in pieces:
            toks = _guard_scan(p)
            if not toks:
                out.append(p)
                continue
            line_changed = True
            hits.extend(t[5].strip("`\"' ")[:48] for t in toks)
            tok_cls.update(t[2] for t in toks if t[2])
            rest, prev = [], 0
            for st, en, *_ in toks:
                rest.append(p[prev:st])
                prev = en
            rest.append(p[prev:])
            if all(t[3] for t in toks) and not _GUARD_FACT_NUM.search("".join(rest)):
                continue                                  # 整句只有內部字樣、沒有數字事實 ⇒ 拿掉
            buf, prev = [], 0
            for st, en, _c, _g, phrase, _raw in toks:
                buf.append(p[prev:st])
                buf.append(phrase[lang])
                prev = en
            buf.append(p[prev:])
            out.append("".join(buf))
        if line_changed:
            changed = True
            nl = "".join(out).rstrip()
            if _GUARD_BARE_LINE.match(nl):
                nl = ""
            new_lines.append(nl)
        else:
            new_lines.append(line)
    res = turn_outcome(tool_calls)
    fail_tok = tok_cls & {"unreachable", "not_found"}
    if res in ("unreachable", "not_found"):
        cls = "unreachable" if (res == "unreachable" or "unreachable" in fail_tok) else "not_found"
    elif res is None and fail_tok:
        cls = "unreachable" if "unreachable" in fail_tok else "not_found"
    else:
        cls = None                                        # 本輪有成功的資料工具 ⇒ 不替它下結論
    out_text = text
    if changed:
        out_text = re.sub(r"\n{3,}", "\n\n", "\n".join(new_lines)).strip()
    if not out_text.strip():
        cls = cls or "not_found"                          # 全被拿掉 ⇒ 至少留一句誠實白話
    if cls and not _GUARD_NOTICE.search(out_text):
        notice = FAIL_NOTICE[cls][lang]
        out_text = (out_text + "\n\n" + notice) if out_text.strip() else notice
        hits.append(f"+notice:{cls}")
    if not hits:
        return text, []
    return out_text, hits


# ---------------------------------------------------------------------------
# selftest（用 tempfile；絕不動預設 DB 檔）
# ---------------------------------------------------------------------------

def _selftest() -> None:
    import shutil
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="board_assistant_test_")
    dbp = os.path.join(tmpdir, "test.sqlite3")
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    def force_updated(db: str, cid: int, ts: str) -> None:
        """把 conversation.updated_at 壓成指定值 —— 讓「排序」與「推進」都可 deterministic 斷言
        （_now() 只到秒：同秒內兩次寫入字串會相等、跨秒又必然不等 ⇒ 不釘死就是 flaky）。"""
        raw = sqlite3.connect(db)
        raw.execute("UPDATE conversation SET updated_at=? WHERE id=?", (ts, cid))
        raw.commit()
        raw.close()

    OLD_TS = "2000-01-01T00:00:00"

    try:
        A1, A2 = "aid-browser-1", "aid-browser-2"

        # ---- 惰性建會話 + title 截斷 ----
        long_msg = "設備 CAL 稼動率為什麼掉？" + "字" * 100
        cid1 = add_turn(A1, None, long_msg, "回覆一", path=dbp)
        ck(isinstance(cid1, int) and cid1 > 0, "惰性建會話應回正整數 id")
        conv = get_conversation(A1, cid1, path=dbp)
        ck(conv is not None, "本人應查得到自己的對話")
        ck(len(conv["title"]) == _TITLE_MAX, f"title 應截到 {_TITLE_MAX} 字")
        ck(conv["closed_at"] is None, "新建對話 closed_at 應為 None")

        # title 空 → 佔位
        cid_blank = add_turn(A1, None, "   ", "空 title 回覆", path=dbp)
        ck(get_conversation(A1, cid_blank, path=dbp)["title"] == "…", "空 title 應為佔位 '…'")

        # ---- message 時序（get_messages 依 id 遞增） ----
        msgs = get_messages(A1, cid1, path=dbp)
        ck(len(msgs) == 2, "一輪應有 user+assistant 兩則")
        ck(msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant", "訊息時序：user 先 assistant 後")
        ck(msgs[0]["content"] == long_msg, "user 內容原樣保留")

        # ---- add_turn 到既有對話（追加一輪） ----
        cid1b = add_turn(A1, cid1, "第二問", "第二答", path=dbp)
        ck(cid1b == cid1, "既有 cid 追加應回同一 id")
        ck(len(get_messages(A1, cid1, path=dbp)) == 4, "追加一輪後應有 4 則")

        # ---- aid 隔離（擁有權強制） ----
        ck(get_conversation(A2, cid1, path=dbp) is None, "他人 aid 不得查得對話")
        ck(get_messages(A2, cid1, path=dbp) == [], "他人 aid 取訊息應回空")
        ck(recent_history(A2, cid1, path=dbp) == [], "他人 aid 取歷史應回空")
        ck(list_open(A2, path=dbp) == [], "A2 尚無對話、list_open 應空")
        raised = False
        try:
            close(A2, cid1, path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "close 他人對話應 raise AssistantError")
        # 確認 A1 對話未被動到
        ck(get_conversation(A1, cid1, path=dbp)["closed_at"] is None, "他人 close 失敗後本人對話不受影響")

        # ---- list_open 排序（updated_at desc） ----
        # ★ 時戳釘死再斷言：原本靠「同秒 tie → 退回 id desc」，但上面 add_turn 推進過 cid1
        #   的 updated_at ⇒ 跨秒執行必敗（flaky，非邏輯錯）。排序語意本來就是 updated_at desc。
        force_updated(dbp, cid1, "2020-01-01T00:00:00")
        force_updated(dbp, cid_blank, "2020-01-02T00:00:00")
        opens = list_open(A1, path=dbp)
        ck([o["id"] for o in opens][0] == cid_blank, "list_open 最近活動者應在最前")
        ck(all("title" in o and "updated_at" in o for o in opens), "list_open 每筆含 title/updated_at")

        # ---- recent_history 輪序（舊→新）+ 只回最近 8 輪 ----
        cidh = add_turn(A1, None, "u0", "a0", path=dbp)
        for i in range(1, 12):  # 再加 11 輪 → 共 12 輪
            add_turn(A1, cidh, f"u{i}", f"a{i}", path=dbp)
        hist = recent_history(A1, cidh, path=dbp)  # 預設 8 輪
        ck(len(hist) == 16, "預設 recent_history 應回 8 輪 = 16 則")
        ck(hist[0]["content"] == "u4", "只回最近 8 輪：最舊應為 u4（丟掉 u0..u3）")
        ck(hist[0]["role"] == "user" and hist[1]["role"] == "assistant", "歷史輪序：user→assistant")
        ck(hist[-1]["content"] == "a11", "歷史最新應為 a11")
        # max_turns 自訂
        ck(len(recent_history(A1, cidh, max_turns=2, path=dbp)) == 4, "max_turns=2 應回 4 則")

        # ---- cap_history 純函式：超 8000 從最舊丟 ----
        big = [{"role": "user", "content": "x" * 4000},
               {"role": "assistant", "content": "y" * 4000},
               {"role": "user", "content": "z" * 4000}]
        capped = cap_history(big, max_chars=8000)
        ck(sum(len(m["content"]) for m in capped) <= 8000, "cap_history 後總字元不超上限")
        ck(len(capped) == 2 and capped[0]["content"][0] == "y", "cap_history 從最舊丟（丟掉第一則 x）")
        # 單則超限截斷
        one = cap_history([{"role": "user", "content": "q" * 12000}], max_chars=8000)
        ck(len(one) == 1 and len(one[0]["content"]) == 8000, "單則超限應截斷到 max_chars")
        ck(cap_history([]) == [], "cap_history 空輸入回空")

        # ---- close 冪等 + closed 對話 add_turn 拒 ----
        close(A1, cid1, path=dbp)
        ck(get_conversation(A1, cid1, path=dbp)["closed_at"] is not None, "close 後 closed_at 應有值")
        close(A1, cid1, path=dbp)  # 再 close 一次：冪等、不 raise
        ck(True, "close 冪等（第二次不 raise）")
        ck(cid1 not in [o["id"] for o in list_open(A1, path=dbp)], "已關閉對話不再出現在 list_open")
        raised = False
        try:
            add_turn(A1, cid1, "還想問", "…", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "closed 對話 add_turn 應 raise")

        # ---- 上限 MAX_OPEN=10 拒第 11 ----
        B = "aid-limit"
        # 目前 B 尚無開啟中對話；建到剛好 10 個
        for i in range(MAX_OPEN):
            add_turn(B, None, f"conv{i}", "ok", path=dbp)
        ck(count_open(B, path=dbp) == MAX_OPEN, f"應有 {MAX_OPEN} 個開啟中對話")
        raised = False
        try:
            add_turn(B, None, "第 11 個", "ok", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "超過開啟中上限應 raise")
        # 結束一個後可再建
        first_open = list_open(B, path=dbp)[-1]["id"]
        close(B, first_open, path=dbp)
        ck(count_open(B, path=dbp) == MAX_OPEN - 1, "結束一個後開啟中數 -1")
        add_turn(B, None, "結束後補建", "ok", path=dbp)
        ck(count_open(B, path=dbp) == MAX_OPEN, "結束一個後可再建、回到上限")

        # ---- add_turn 到不存在的 cid 拒 ----
        raised = False
        try:
            add_turn(A1, 999999, "不存在", "…", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "add_turn 到不存在 cid 應 raise")

        # ===================================================================
        # U5 兩段式送出
        # ===================================================================

        # ---- A1 migration：手工建「舊 schema」DB → _connect 後五欄齊、舊列語意不變 ----
        legacy = os.path.join(tmpdir, "legacy.sqlite3")
        raw = sqlite3.connect(legacy)
        raw.executescript(
            "CREATE TABLE conversation(id INTEGER PRIMARY KEY AUTOINCREMENT, aid TEXT NOT NULL,"
            " title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT);"
            "CREATE TABLE message(id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,"
            " created_at TEXT NOT NULL);"
        )
        raw.execute("INSERT INTO conversation(aid,title,created_at,updated_at,closed_at)"
                    " VALUES('aid-legacy','舊對話','2026-07-01T09:00:00','2026-07-01T09:00:00',NULL)")
        raw.execute("INSERT INTO message(conversation_id,role,content,created_at)"
                    " VALUES(1,'user','舊問','2026-07-01T09:00:00')")
        raw.execute("INSERT INTO message(conversation_id,role,content,created_at)"
                    " VALUES(1,'assistant','舊答','2026-07-01T09:00:00')")
        raw.commit()
        pre = {r[1] for r in raw.execute("PRAGMA table_info(message)").fetchall()}
        raw.close()
        ck("status" not in pre, "前置條件：舊 schema 本來就沒有 status 欄")

        c = _connect(legacy)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(message)").fetchall()}
        c.close()
        ck(all(col in cols for col, _ in _NEW_MESSAGE_COLS),
           "migration 後所有加性欄（U5 五欄＋U6 Jev 六欄＋U7 兩欄＋U8 兩欄）應全在")
        ck({"prompt_chars", "history_turns"} <= cols,
           "U8 上下文落帳兩欄應由**同一條** additive migration 帶上來（舊 DB 零遷移腳本）")
        ck({"jev_predicted", "jev_confidence", "jev_model",
            "jev_criteria_ver", "jev_error", "jev_ms"} <= cols,
           "U6 Jev shadow 六欄應由同一條 additive migration 帶上來（舊 DB 零遷移腳本）")
        ck({"rating", "rated_at"} <= cols,
           "U7 👍/👎 兩欄應由**同一條** additive migration 帶上來（舊 DB 零遷移腳本）")
        c = _connect(legacy)  # 第二次連線：冪等、不重複 ALTER、不炸
        cols2 = {r["name"] for r in c.execute("PRAGMA table_info(message)").fetchall()}
        c.close()
        ck(cols2 == cols, "migration 冪等：再連一次欄位集合不變")
        # PA2：client_key 一欄走同一條 additive migration（舊庫原本沒有、連一次就有、再連不重加）
        ck("client_key" not in pre and "client_key" in cols and "client_key" in cols2,
           "PA2 client_key 欄由同一條 additive migration 帶上來且冪等")
        ck(get_turn("aid-legacy", 2, path=legacy)["client_key"] is None,
           "PA2 舊列 client_key 讀出 NULL（不回溯）")

        legacy_msgs = get_messages("aid-legacy", 1, path=legacy)
        ck(len(legacy_msgs) == 2, "舊 DB 訊息照讀得到")
        ck(legacy_msgs[1]["status"] is None and legacy_msgs[1]["error_kind"] is None,
           "舊列 status/error_kind 讀出 NULL＝done 渲染語意")
        ck([m["content"] for m in recent_history("aid-legacy", 1, path=legacy)] == ["舊問", "舊答"],
           "舊列（assistant user_msg_id 為 NULL）仍全數進歷史＝語意不變")

        # ---- A2 begin_turn：user 列 + pending 佔位列（同 txn）----
        U = "aid-u5"
        cidu, t1 = begin_turn(U, None, "U5 首問", "path=/equipment&eid=EID-13219", "zh", path=dbp)
        ck(isinstance(cidu, int) and cidu > 0 and isinstance(t1, int) and t1 > 0,
           "begin_turn 應回 (cid, turn_id) 正整數對")
        m = get_messages(U, cidu, path=dbp)
        ck(len(m) == 2, "begin_turn 應寫 user + assistant 佔位兩則")
        ck(m[0]["role"] == "user" and m[0]["content"] == "U5 首問", "user 列存真內容")
        ck(m[0]["status"] is None, "user 列 status 恆 NULL")
        ck(m[1]["role"] == "assistant" and m[1]["content"] == "" and m[1]["status"] == "pending",
           "assistant 佔位列＝空內容 + pending")
        ck(m[1]["id"] == t1, "回傳的 turn_id 就是 assistant 佔位列 id")

        t = get_turn(U, t1, path=dbp)
        ck(t["status"] == "pending" and t["error_kind"] is None, "get_turn 新輪＝pending 無 error_kind")
        ck(t["conversation_id"] == cidu, "get_turn 帶回 conversation_id")
        ck(t["user_msg_id"] == m[0]["id"], "user_msg_id 錨到配對 user 列（非 turn_id-1 推算）")
        ck(t["user_content"] == "U5 首問", "get_turn join 回 user_content")
        ck(t["page_ctx"] == "path=/equipment&eid=EID-13219" and t["locale"] == "zh",
           "page_ctx/locale 存的是送出當下的值")
        ck(get_turn(U, m[0]["id"], path=dbp) is None, "拿 user 列 id 當 turn_id 查 → None（不洩）")
        ck(get_turn(U, 999999, path=dbp) is None, "不存在的 turn_id → None")

        # 新會話在 begin 瞬間就存在（U5 價值：標籤不必等回答）
        ck(cidu in [o["id"] for o in list_open(U, path=dbp)], "begin_turn 後新會話立刻出現在 list_open")

        # ---- complete_turn：pending → done，且推進 updated_at ----
        force_updated(dbp, cidu, OLD_TS)
        complete_turn(U, t1, "這是答案", path=dbp)
        ck(get_conversation(U, cidu, path=dbp)["updated_at"] > OLD_TS, "complete_turn 應推進 updated_at")
        t = get_turn(U, t1, path=dbp)
        ck(t["status"] == "done" and t["content"] == "這是答案" and t["error_kind"] is None,
           "complete_turn 後＝done + 真內容")
        m = get_messages(U, cidu, path=dbp)
        ck(m[1]["status"] is None, "done 在 DB 裡就是 status NULL＝與舊資料同語意")
        ck(len(m) == 2 and m[0]["role"] == "user" and m[1]["role"] == "assistant",
           "完成的一輪仍是 user+assistant 兩則、時序正確（成功語意不變）")

        raised = False
        try:
            complete_turn(U, t1, "重複完成", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "double-complete 應 raise（防重複完成/亂寫）")
        ck(get_turn(U, t1, path=dbp)["content"] == "這是答案", "double-complete 失敗後內容不被覆寫")

        raised = False
        try:
            retry_turn(U, t1, path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "retry 已完成的 turn 應 raise")
        raised = False
        try:
            fail_turn(U, t1, "ai_fail", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "已 done 的 turn 不得改判 failed")

        # ---- fail → retry → complete 全鏈 ----
        cidu2, t2 = begin_turn(U, cidu, "第二問", None, "en", path=dbp)
        ck(cidu2 == cidu, "既有 cid 的 begin_turn 應回同一會話")
        raised = False
        try:
            retry_turn(U, t2, path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "pending 中不可 retry（防雙跑）")

        force_updated(dbp, cidu, OLD_TS)
        fail_turn(U, t2, "ai_timeout", path=dbp)
        ck(get_conversation(U, cidu, path=dbp)["updated_at"] > OLD_TS, "fail_turn 應推進 updated_at")
        t = get_turn(U, t2, path=dbp)
        ck(t["status"] == "failed" and t["error_kind"] == "ai_timeout", "fail_turn → failed + 分類")
        fail_turn(U, t2, "gw_down", path=dbp)  # 冪等 no-op
        ck(get_turn(U, t2, path=dbp)["error_kind"] == "ai_timeout",
           "已 failed 再 fail＝no-op，保留最初 error_kind")
        raised = False
        try:
            complete_turn(U, t2, "不該寫進去", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "failed 的 turn 不可直接 complete（要先 retry）")

        force_updated(dbp, cidu, OLD_TS)
        r = retry_turn(U, t2, path=dbp)
        ck(get_conversation(U, cidu, path=dbp)["updated_at"] > OLD_TS, "retry_turn 應推進 updated_at")
        ck(r["status"] == "pending" and r["error_kind"] is None, "retry_turn → 重置 pending、清 error_kind")
        ck(r["id"] == t2 and r["conversation_id"] == cidu, "retry_turn 回同一 turn/會話（原地重試）")
        ck(r["user_content"] == "第二問" and r["locale"] == "en" and r["page_ctx"] is None,
           "retry 帶回送出當下存的 user_content/locale/page_ctx（page_ctx None 保持 None）")
        ck(len(get_messages(U, cidu, path=dbp)) == 4,
           "retry 不產生重複 user 泡泡（仍是 2 輪 4 則）")
        complete_turn(U, t2, "補答成功", path=dbp)
        ck(get_turn(U, t2, path=dbp)["status"] == "done", "fail→retry→complete 全鏈可著地")

        # ---- interrupted（app 重啟孤兒 pending 顯性著地，不永遠轉圈）----
        _, t_orphan = begin_turn(U, cidu, "重啟前送出的問題", None, "zh", path=dbp)
        fail_turn(U, t_orphan, "interrupted", path=dbp)
        ck(get_turn(U, t_orphan, path=dbp)["error_kind"] == "interrupted",
           "孤兒 pending 可著地成 interrupted 失敗")
        ck("interrupted" in ERROR_KINDS, "interrupted 屬 ERROR_KINDS 分類")

        # ---- PA2：共用 gateway 合約（client_key／quota／budget；`_handoff/PLAN-board-assistant-phase2-20261030.md`）----
        ck({"quota", "budget"} <= set(ERROR_KINDS)
           and ERROR_KINDS[:4] == ("gw_down", "ai_timeout", "ai_fail", "interrupted"),
           "PA2 ERROR_KINDS 加性兩值 quota／budget（既有四值與順序不動）")
        k1 = client_key("203.0.113.7", "s3cret")
        ck(isinstance(k1, str) and len(k1) == 32 and all(ch in "0123456789abcdef" for ch in k1),
           "PA2 client_key 定長 32 字小寫 hex")
        ck(client_key("203.0.113.7", "s3cret") == k1, "PA2 client_key 同入同出")
        import hashlib as _hl
        import hmac as _hm
        ck(k1 == _hm.new(b"s3cret", b"203.0.113.7", _hl.sha256).hexdigest()[:32],
           "PA2 client_key＝HMAC-SHA256(key=secret, msg=ip) 前 32 字")
        ck(client_key("203.0.113.7", "other") != k1, "PA2 client_key 不同 secret ⇒ 不同值")
        ck(client_key("203.0.113.8", "s3cret") != k1, "PA2 client_key 不同 ip ⇒ 不同值")
        ck(all(client_key(ip, sec) == "unknown" for ip, sec in
               (("", "s3cret"), (None, "s3cret"), ("203.0.113.7", ""), ("203.0.113.7", None), ("", ""),
                (12345, "s3cret"), ("203.0.113.7", b"s3cret"))),
           "PA2 client_key：ip 或 secret 空／非字串 ⇒ 'unknown'")
        ck("203.0.113.7" not in k1, "PA2 client_key 不含原始 IP")
        UPA = "aid-pa2"                                  # 獨立 aid／會話：不擾動上下文既有的計數斷言
        _, t_pa = begin_turn(UPA, None, "PA2 限額", None, "en", path=dbp, client_key=k1)
        ck(get_turn(UPA, t_pa, path=dbp)["client_key"] == k1, "PA2 begin_turn(client_key=) 存進佔位列、_turn_dict 帶出")
        ck(get_turn(U, t_orphan, path=dbp)["client_key"] is None, "PA2 begin_turn 缺省 ⇒ client_key NULL（同改前）")
        _raw = sqlite3.connect(dbp)
        _ipcol = [r for r in _raw.execute("SELECT * FROM message WHERE id=?", (t_pa,)).fetchall()
                  if any(v == "203.0.113.7" for v in r)]
        _raw.close()
        ck(not _ipcol, "PA2 原始 IP 不落庫（佔位列任何欄都沒有它）")
        fail_turn(UPA, t_pa, "quota", path=dbp)
        ck(get_turn(UPA, t_pa, path=dbp)["status"] == "failed" and get_turn(UPA, t_pa, path=dbp)["error_kind"] == "quota",
           "PA2 fail_turn('quota') 可著地")
        r_pa = retry_turn(UPA, t_pa, path=dbp)
        ck(r_pa["status"] == "pending" and r_pa["client_key"] == k1, "PA2 retry 沿用送出當下存的 client_key")
        fail_turn(UPA, t_pa, "budget", path=dbp)
        ck(get_turn(UPA, t_pa, path=dbp)["error_kind"] == "budget", "PA2 fail_turn('budget') 可著地")

        # ---- PA2：app.py 的兩支純函式（`_gw_error_kind`／`_client_ip`）——公開樹跑不到 wiring selftest，
        #   本檔又不能 import app（重、會起整個 app）⇒ 用 ast 從 app.py **原樣抽出**那幾個定義在空命名空間執行
        #   ＝測的就是實際跑的碼（不是副本）。`_SYNTH_PROC` 在命名空間給 False＝非合成進程的預設分支。----
        import ast as _ast
        _app_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
        with open(_app_py, encoding="utf-8") as _f:
            _tree = _ast.parse(_f.read(), _app_py)
        _want = {"_gw_error_kind", "_client_ip", "_GW_TIMEOUT_EXC", "_GW_DOWN_EXC",
                 "_synth_mcp_target_ok", "_synth_url_parts", "_SYNTH_ASSIST_FORBIDDEN_PORTS",
                 "_SYNTH_ASSIST_TUNNEL_OFFSET"}
        _nodes = [nd for nd in _tree.body
                  if (isinstance(nd, _ast.FunctionDef) and nd.name in _want)
                  or (isinstance(nd, _ast.Assign) and any(isinstance(t, _ast.Name) and t.id in _want
                                                          for t in nd.targets))]
        import core_source as _cs                       # `_synth_mcp_target_ok` 用 `_wrs.is_loopback_host`（純函式）
        _ns = {"_SYNTH_PROC": False, "_wrs": _cs}
        exec(compile(_ast.Module(body=_nodes, type_ignores=[]), _app_py, "exec"), _ns)
        ck(_want <= set(_ns), "PA2 app.py 純函式抽得到（_gw_error_kind／_client_ip／_synth_mcp_target_ok 與其常數）")
        gk = _ns["_gw_error_kind"]
        ck(gk("HTTPError", 429, "daily question limit reached", shared_gw=True) == "quota",
           "PA2 _gw_error_kind 共用 gateway 429 ⇒ quota")
        ck(gk("HTTPError", 503, "Daily BUDGET exhausted", shared_gw=True) == "budget",
           "PA2 _gw_error_kind 共用 gateway 503＋budget（不分大小寫）⇒ budget")
        ck(gk("HTTPError", 503, "backend not configured", shared_gw=True) == "ai_fail",
           "PA2 _gw_error_kind 共用 gateway 503 其他 detail ⇒ ai_fail（照舊）")
        # cmms `infra/hermes/CONTRACT.md` §3 的實際 detail 字串
        ck(gk("HTTPError", 503, "monthly budget exhausted", shared_gw=True) == "budget"
           and gk("HTTPError", 503, "daily budget exhausted", shared_gw=True) == "budget"
           and gk("HTTPError", 429, "daily question limit reached", shared_gw=True) == "quota",
           "PA2 _gw_error_kind 合約字串：monthly／daily budget exhausted ⇒ budget；429 ⇒ quota")
        ck(all(gk("HTTPError", 503, d, shared_gw=True) == "ai_fail" for d in
               ("ledger unavailable", "persona not available: board", "mcp target not configured: board",
                "agent backend not configured: anthropic credentials rejected", "gateway secret not configured",
                "mcp target not supported by the codex backend: board")),
           "PA2 _gw_error_kind 合約字串：503 ledger／persona／mcp target／agent backend／secret ⇒ ai_fail")
        ck(gk("HTTPError", 502, "codex timed out", shared_gw=True) == "ai_timeout"
           and gk("HTTPError", 502, "boom", shared_gw=True) == "ai_fail"
           and gk("URLError", None, "Connection refused", shared_gw=True) == "gw_down"
           and gk("TimeoutError", 429, "", shared_gw=True) == "ai_timeout",
           "PA2 _gw_error_kind 共用 gateway 其餘判定序不變（502 逾時／502 其他／連線層／例外型別逾時先贏）")
        ck(gk("HTTPError", 429, "daily question limit reached") == "ai_fail"
           and gk("HTTPError", 503, "budget") == "ai_fail"
           and gk("HTTPError", 429, "x", shared_gw=False) == "ai_fail",
           "PA2 _gw_error_kind 非合成進程（缺省讀 _SYNTH_PROC=False）429／503-budget 照舊 ai_fail＝分類逐位元同改前")
        mt = _ns["_synth_mcp_target_ok"]
        _DK = "http://host.docker.internal:18050/mcp"
        ck(mt(_DK, 8050, _DK)[0] is True and mt(_DK, 8050, "  " + _DK + " ")[0] is True,
           "PA2 _synth_mcp_target_ok expected 逐字相等（env 去頭尾空白）⇒ 非 loopback 放行")
        ck(mt(_DK, 8050, "http://host.docker.internal:18050/mcp/")[0] is False
           and mt("http://HOST.docker.internal:18050/mcp", 8050, _DK)[0] is False
           and mt("http://172.17.0.1:18050/mcp", 8050, _DK)[0] is False,
           "PA2 _synth_mcp_target_ok expected 不相等（差一字／大小寫／同埠別主機）⇒ 拒")
        _bad = ("http://host.docker.internal:18051/mcp", "http://host.docker.internal:18000/mcp",
                "http://host.docker.internal:18050/other", "https://host.docker.internal:18050/mcp",
                "http://u:p@host.docker.internal:18050/mcp", "http://host.docker.internal/mcp")
        ck(all(mt(u, 8050, u)[0] is False for u in _bad) and mt(_DK, 8051, _DK)[0] is False
           and mt("http://host.docker.internal:18000/mcp", 8000, "http://host.docker.internal:18000/mcp")[0] is False
           and mt(_DK, None, _DK)[0] is False,
           "PA2 _synth_mcp_target_ok expected 相等但埠／路徑／scheme／userinfo／正式埠／本埠未知不對 ⇒ 仍拒")
        ck(mt(_DK, 8050)[0] is False and mt(_DK, 8050, None)[0] is False and mt(_DK, 8050, "  ")[0] is False
           and mt("http://127.0.0.1:18050/mcp", 8050)[0] is True and mt("http://127.0.0.1:8050/mcp", 8050, None)[0] is True,
           "PA2 _synth_mcp_target_ok expected 未設（None／空白）⇒ 原 loopback 規則：非 loopback 拒、loopback 放行")
        ck(mt("http://127.0.0.1:18050/mcp", 8050, _DK)[0] is False,
           "PA2 _synth_mcp_target_ok expected 有設時 loopback 但不等於 expected ⇒ 拒（不留第二條路）")
        cip = _ns["_client_ip"]
        ck(cip("198.51.100.4", "203.0.113.9") == "198.51.100.4",
           "PA2 _client_ip 對端非 loopback ⇒ 忽略 XFF、回對端")
        ck(cip("127.0.0.1", "198.18.0.66, 10.0.0.1 , 203.0.113.9") == "203.0.113.9"
           and cip("127.0.0.1", "spoofed-junk, 203.0.113.9") == "203.0.113.9",
           "PA2 _client_ip 對端 loopback、XFF 多段 ⇒ 取最後一段（最近一跳代理附加；前段偽造無效）")
        ck(cip("127.0.0.1", "203.0.113.9") == "203.0.113.9" and cip("::1", "2001:db8::5") == "2001:db8::5",
           "PA2 _client_ip 對端 loopback、XFF 單段 ⇒ 就是它（v4／v6；同改前）")
        ck(cip("127.0.0.1", "10.0.0.1,  203.0.113.9  ") == "203.0.113.9"
           and cip("127.0.0.1", " 203.0.113.9\t") == "203.0.113.9",
           "PA2 _client_ip XFF 尾端／前後空白去掉")
        ck(cip("127.0.0.1", "203.0.113.9, ") == "",
           "PA2 _client_ip XFF 最後一段是空 ⇒ ''（不退回前段）")
        ck(cip("127.0.0.1", "") == "127.0.0.1" and cip("127.0.0.1", None) == "127.0.0.1"
           and cip(None, None) == "" and cip("", "203.0.113.9") == "",
           "PA2 _client_ip 空值：XFF 空 ⇒ 回對端；對端空 ⇒ ''")
        ck(cip("127.0.0.1", "203.0.113.9:443") == "" and cip("127.0.0.1", "evil.example") == ""
           and cip("testclient", None) == "" and cip(12345, None) == "" and cip("127.0.0.1", "1" * 80) == "",
           "PA2 _client_ip 壞值（ip:port／主機名／非字串／過長）⇒ ''")

        # ---- page_ctx cap 400 ----
        _, t_cap = begin_turn(U, cidu, "cap 測試", "P" * 900, "zh", path=dbp)
        ck(len(get_turn(U, t_cap, path=dbp)["page_ctx"]) == _PAGE_CTX_MAX,
           f"page_ctx 應截到 {_PAGE_CTX_MAX} 字")

        # ---- 擁有權：五個新 API 對他人 aid 全拒 ----
        raised = False
        try:
            begin_turn(A2, cidu, "偷寫別人的會話", None, "zh", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "begin_turn 到他人會話應 raise")
        ck(get_turn(A2, t1, path=dbp) is None, "get_turn 他人 turn → None")
        raised = False
        try:
            complete_turn(A2, t_cap, "亂寫", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "complete_turn 他人 turn 應 raise")
        raised = False
        try:
            fail_turn(A2, t_cap, "ai_fail", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "fail_turn 他人 turn 應 raise")
        raised = False
        try:
            retry_turn(A2, t_orphan, path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "retry_turn 他人 turn 應 raise")
        ck(get_turn(U, t_cap, path=dbp)["status"] == "pending"
           and get_turn(U, t_orphan, path=dbp)["status"] == "failed",
           "他人 aid 的多次嘗試後，本人 turn 狀態一位元未動")

        # ---- MAX_OPEN 在 begin_turn 生效 ----
        L = "aid-u5-limit"
        for i in range(MAX_OPEN):
            begin_turn(L, None, f"u5conv{i}", None, "zh", path=dbp)
        ck(count_open(L, path=dbp) == MAX_OPEN, f"begin_turn 建到 {MAX_OPEN} 個開啟中對話")
        raised = False
        try:
            begin_turn(L, None, "第 11 個", None, "zh", path=dbp)
        except AssistantError:
            raised = True
        ck(raised, "begin_turn 超過 MAX_OPEN 應 raise")
        ck(count_open(L, path=dbp) == MAX_OPEN, "拒建後開啟中數不變（未留半套資料）")

        # ---- CAS 狀態轉移（併發鏡 MED）：兩條路徑交錯時只有一條能贏 ----
        # 真實情境＝兩個分頁同時按重試／worker 與 poll 的 interrupted 判定同時著地。
        # 兩條各自開連線、各自先讀後寫；**deterministic 重現法**＝讓輸家的**前置檢查**讀到
        # 贏家提交前的舊值（一次性 monkeypatch `_fetch_turn_row`），DB 真值則已被贏家改掉
        # ⇒ 前置檢查放行、CAS 攔下。這正是 check-then-act 之間那道空窗。
        # ★ 造假只作用於**第一次**讀取：輸家後續的重讀（fail_turn 的分流）本來就會看到真值。
        C = "aid-u5-cas"
        orig_fetch = _fetch_turn_row

        def loser_reads_stale(row, fn, *a, **kw):
            """以「前置讀到 row 這份舊值」的視角跑 fn（一次性）；回 (raised, error)。"""
            state = {"n": 0}

            def one_shot(conn, aid_, tid, _r=row):
                if tid == _r["id"] and state["n"] == 0:
                    state["n"] = 1
                    return _r
                return orig_fetch(conn, aid_, tid)
            globals()["_fetch_turn_row"] = one_shot
            try:
                fn(*a, **kw)
                return False
            except AssistantError:
                return True
            finally:
                globals()["_fetch_turn_row"] = orig_fetch

        cidc, tc = begin_turn(C, None, "同一題被按兩次重試", None, "zh", path=dbp)
        fail_turn(C, tc, "ai_timeout", path=dbp)
        stale_failed = dict(get_turn(C, tc, path=dbp))           # 輸家手上那份「還是 failed」
        won = retry_turn(C, tc, path=dbp)                        # 贏家：failed → pending
        ck(won["status"] == "pending", "雙重試：第一條應成功翻成 pending")
        ck(loser_reads_stale(stale_failed, retry_turn, C, tc, path=dbp),
           "雙重試競態：輸的一條必須 raise（否則同題起兩個 worker＝雙燒配額）")
        ck(get_turn(C, tc, path=dbp)["status"] == "pending",
           "雙重試競態：DB 只剩**一個** pending（輸家沒把它再翻一次）")

        # 贏家著地成 done；輸家（前置讀到舊 pending）的 complete/fail 都要被 CAS 擋下
        stale_pending_tc = dict(get_turn(C, tc, path=dbp))       # 此刻＝pending（輸家手上那份）
        complete_turn(C, tc, "贏家的答案", path=dbp)
        ck(loser_reads_stale(stale_pending_tc, complete_turn, C, tc, "輸家覆寫", path=dbp),
           "double-complete 競態：輸的一條必須 raise")
        ck(loser_reads_stale(stale_pending_tc, fail_turn, C, tc, "ai_fail", path=dbp),
           "fail 競態：已完成的一輪不得被改判 failed")
        t_cas = get_turn(C, tc, path=dbp)
        ck(t_cas["status"] == "done" and t_cas["content"] == "贏家的答案",
           "競態落幕：DB 只剩贏家寫下的那一份（內容未被覆寫）")
        ck(len(get_messages(C, cidc, path=dbp)) == 2,
           "競態不產生額外訊息列（仍是 user+assistant 兩則）")

        # fail 的冪等 no-op 在 CAS 之後仍成立（輸家前置讀到舊 pending、實際已 failed
        # → 重讀看到 failed → 不 raise、不覆寫第一手死因）
        _, tc2 = begin_turn(C, cidc, "冪等 fail", None, "zh", path=dbp)
        stale_pending2 = dict(get_turn(C, tc2, path=dbp))
        fail_turn(C, tc2, "gw_down", path=dbp)                   # 贏家先著地
        ck(not loser_reads_stale(stale_pending2, fail_turn, C, tc2, "ai_fail", path=dbp),
           "fail 競態：輸家對已 failed 的一輪＝冪等 no-op（不 raise）")
        ck(get_turn(C, tc2, path=dbp)["error_kind"] == "gw_down",
           "fail 競態：保留第一手死因（不被輸家覆寫）")

        # ---- recent_history 排除 pending/failed 輪（含其 user 列）+ before_id ----
        H = "aid-u5-hist"
        cidh2, ha = begin_turn(H, None, "q1", None, "zh", path=dbp)
        complete_turn(H, ha, "a1", path=dbp)
        _, hb = begin_turn(H, cidh2, "q2-failed", None, "zh", path=dbp)
        fail_turn(H, hb, "ai_fail", path=dbp)
        _, hc = begin_turn(H, cidh2, "q3-pending", None, "zh", path=dbp)
        _, hd = begin_turn(H, cidh2, "q4", None, "zh", path=dbp)
        complete_turn(H, hd, "a4", path=dbp)

        hist2 = [x["content"] for x in recent_history(H, cidh2, path=dbp)]
        ck(hist2 == ["q1", "a1", "q4", "a4"],
           "recent_history 只收完成輪：failed/pending 的 assistant 與其 user 列全排除")
        ck("" not in hist2, "pending 佔位的空字串絕不進 prompt")
        ck("q2-failed" not in hist2 and "q3-pending" not in hist2,
           "懸著沒答案的提問不進 prompt（避免 codex 以為要補答舊題）")

        anchor = get_turn(H, hd, path=dbp)["user_msg_id"]
        hist3 = [x["content"] for x in recent_history(H, cidh2, before_id=anchor, path=dbp)]
        ck(hist3 == ["q1", "a1"], "before_id：只取這一輪之前的完成輪")
        ck("q4" not in hist3, "before_id 為嚴格小於：自己這一問不入歷史")
        ck(recent_history(H, cidh2, before_id=1, path=dbp) == [], "before_id 極小值 → 空歷史")
        ck([x["content"] for x in recent_history(H, cidh2, max_turns=1, path=dbp)] == ["q4", "a4"],
           "max_turns 與 U5 過濾併用：只回最近一輪完成輪")
        ck(recent_history(A2, cidh2, path=dbp) == [], "他人 aid 取 U5 歷史仍為空")

        # ---- U6 Jev shadow 六欄（`record_jev`）：只記錄、不決策 ----------------------
        class _JR:                                   # 鴨子型別的 board_jev.JevResult
            def __init__(self, predicted=None, confidence=None, model=None,
                         criteria_version=0, error=None, elapsed_ms=0):
                self.predicted, self.confidence, self.model = predicted, confidence, model
                self.criteria_version, self.error, self.elapsed_ms = criteria_version, error, elapsed_ms

        def jev_row(tid: int) -> dict:
            raw2 = sqlite3.connect(dbp)
            raw2.row_factory = sqlite3.Row
            row = raw2.execute(
                "SELECT role, status, jev_predicted, jev_confidence, jev_model, "
                "       jev_criteria_ver, jev_error, jev_ms FROM message WHERE id=?", (tid,)
            ).fetchone()
            raw2.close()
            return dict(row)

        J = "aid-u6-jev"
        cidj, tj = begin_turn(J, None, "EID-13219 昨天停多久", "/equipment", "zh-TW", path=dbp)
        uj = get_turn(J, tj, path=dbp)["user_msg_id"]
        ck(jev_row(tj)["jev_predicted"] is None and jev_row(tj)["jev_error"] is None,
           "新列的 Jev 六欄應全 NULL（＝沒紀錄，不是答錯）")
        ck(record_jev(tj, _JR("equipment_downtime", 0.97, "jev-1.13.0", 2, None, 612), path=dbp),
           "record_jev 寫 assistant 列應回 True")
        rowj = jev_row(tj)
        ck(rowj["jev_predicted"] == "equipment_downtime"
           and abs(rowj["jev_confidence"] - 0.97) < 1e-9
           and rowj["jev_model"] == "jev-1.13.0" and rowj["jev_criteria_ver"] == 2
           and rowj["jev_error"] is None and rowj["jev_ms"] == 612,
           "record_jev 六欄應逐欄落庫")
        ck(rowj["status"] == "pending",
           "★ record_jev 絕不碰 status（U5 狀態機的 CAS 守衛就是它）")
        complete_turn(J, tj, "答案", path=dbp)       # 記過 Jev 之後仍可正常著地
        ck(get_turn(J, tj, path=dbp)["status"] == "done"
           and jev_row(tj)["jev_predicted"] == "equipment_downtime",
           "complete_turn 與 Jev 六欄互不干擾（著地後紀錄仍在）")
        ck(record_jev(tj, _JR(error="timeout", criteria_version=2, elapsed_ms=20000), path=dbp),
           "record_jev 可重複呼叫（後寫覆蓋）")
        rowj2 = jev_row(tj)
        ck(rowj2["jev_error"] == "timeout" and rowj2["jev_predicted"] is None
           and rowj2["jev_confidence"] is None,
           "失敗紀錄＝error kind 有值、predicted／confidence 為 NULL")
        ck(not record_jev(uj, _JR("ask", 0.5, "m", 2, None, 1), path=dbp),
           "record_jev 對 user 列不得寫（rowcount 0 → False）")
        ck(jev_row(uj)["jev_predicted"] is None and jev_row(uj)["role"] == "user",
           "user 列一位元未動")
        ck(not record_jev(999999, _JR("ask", 0.5, "m", 2, None, 1), path=dbp),
           "record_jev 對不存在的 id → False")
        ck(len(get_messages(J, cidj, path=dbp)) == 2,
           "record_jev 不產生任何額外訊息列")

        # ---- U6 加性兩欄：router 決策面（`record_jev_router`；#172、§7.7.5）------------
        def rt_row(tid: int) -> dict:
            raw4 = sqlite3.connect(dbp)
            raw4.row_factory = sqlite3.Row
            row = raw4.execute("SELECT role, status, jev_applied, jev_threshold "
                               "FROM message WHERE id=?", (tid,)).fetchone()
            raw4.close()
            return dict(row)

        ck({"jev_applied", "jev_threshold"} <= {c for c, _ in _NEW_MESSAGE_COLS},
           "#172 兩欄在 `_NEW_MESSAGE_COLS`（同一條 additive migration）")
        ck(rt_row(tj)["jev_applied"] is None and rt_row(tj)["jev_threshold"] is None,
           "舊列／router 沒開的列兩欄 NULL（NULL≠skip）")
        _, tr0 = begin_turn(J, cidj, "router pending", "/equipment", "zh-TW", path=dbp)
        ck(record_jev_router(tr0, "tool", 0.9, path=dbp), "record_jev_router 寫 assistant 列 → True")
        ck(rt_row(tr0) == {"role": "assistant", "status": "pending",
                           "jev_applied": "tool", "jev_threshold": 0.9},
           "★ 兩欄落庫且**不碰 status**（仍 pending）")
        ck(get_turn(J, tr0, path=dbp)["jev_applied"] == "tool"
           and get_turn(J, tr0, path=dbp)["jev_predicted"] is None,
           "get_turn 帶出 jev_applied／jev_predicted（chip 用）")
        ck(not record_jev_router(uj, "ask", 0.9, path=dbp) and rt_row(uj)["jev_applied"] is None,
           "user 列不寫（→ False、一位元不動）")
        _bad = 0
        for badv in (("maybe", 0.9), ("tool", "0.9"), ("tool", True), (None, 0.9)):
            try:
                record_jev_router(tr0, badv[0], badv[1], path=dbp)
            except ValueError:
                _bad += 1
        ck(_bad == 4, "非五態／非數字門檻一律 ValueError（不靜默吞）")
        _five = []
        for st in JEV_APPLIED:
            _, tx = begin_turn(J, cidj, "router " + st, "/equipment", "zh-TW", path=dbp)
            record_jev_router(tx, st, 0.9, path=dbp)
            _five.append(tx)
        cnt = jev_applied_counts(path=dbp)
        ck(cnt == {"tool": 2, "ask": 1, "refuse": 1, "none": 1, "skip": 1},
           f"jev_applied_counts 五態計數（tool 含前面那列）＝{cnt}")
        ck(jev_applied_counts(day="1999-01-01", path=dbp) == {k: 0 for k in JEV_APPLIED},
           "別的日子五態全 0（缺的補 0、不缺鍵）")
        ck(any(m.get("jev_applied") == "refuse" for m in get_messages(J, cidj, path=dbp)),
           "get_messages 帶 jev_applied（panel 還原路徑的 chip）")

        # ---- U7 👍/👎 兩欄（`rate_turn`）：使用者動作、帶 aid、只有 done 可評 -----------
        def rate_row(tid: int) -> dict:
            raw3 = sqlite3.connect(dbp)
            raw3.row_factory = sqlite3.Row
            row = raw3.execute(
                "SELECT role, status, content, rating, rated_at FROM message WHERE id=?", (tid,)
            ).fetchone()
            raw3.close()
            return dict(row)

        R = "aid-u7-rate"
        cidr, tr = begin_turn(R, None, "CAL 昨天稼動多少", "/station/CAL", "zh-TW", path=dbp)
        ur = get_turn(R, tr, path=dbp)["user_msg_id"]
        ck(rate_row(tr)["rating"] is None and rate_row(tr)["rated_at"] is None,
           "新列的 U7 兩欄應全 NULL（＝沒評過，不是評成中立）")
        ck(rate_turn(R, tr, "up", path=dbp) == {"error": "not_rateable"},
           "★ pending 的一輪不可評（本層加嚴：沒有答案的佔位列按讚沒有語意）")
        ck(rate_row(tr)["rating"] is None, "拒評之後一位元未寫")
        complete_turn(R, tr, "昨天 CAL 稼動 71.2%", path=dbp)

        d1 = rate_turn(R, tr, "up", path=dbp)
        ck(d1 is not None and d1.get("rating") == "up" and d1.get("rated_at"),
           "done 的一輪可評：回更新後 dict、rating='up'、rated_at 有值")
        row1 = rate_row(tr)
        ck(row1["rating"] == "up" and row1["rated_at"] is not None, "up 逐欄落庫")
        ck(row1["status"] is None and row1["content"] == "昨天 CAL 稼動 71.2%",
           "★ rate_turn 絕不碰 status／content（U5 狀態機的 CAS 守衛就是 status）")

        d2 = rate_turn(R, tr, "down", path=dbp)
        ck(d2.get("rating") == "down" and d2.get("rated_at"),
           "改評 up→down：兩欄一起覆寫")

        d3 = rate_turn(R, tr, "down", path=dbp)
        ck(d3.get("rating") is None and d3.get("rated_at") is None,
           "★ 再按同一顆＝取消：rating 與 rated_at **一起**清成 NULL（不留鬼列）")
        ck(rate_row(tr)["rated_at"] is None,
           "取消後不得留下「沒有評分卻有評分時間」的鬼列")

        ck(rate_turn(R, tr, "up", path=dbp).get("rating") == "up", "取消後可再評")
        ck(rate_turn(A2, tr, "down", path=dbp) is None,
           "★ 擁有權：別的 aid 對他人 turn 評分 → None")
        ck(rate_row(tr)["rating"] == "up", "他人評分未動到原列一位元")
        ck(rate_turn(R, ur, "up", path=dbp) is None,
           "拿 user 列 id 來評 → None（`_fetch_turn_row` 的 role='assistant' 守衛）")
        ck(rate_row(ur)["rating"] is None and rate_row(ur)["role"] == "user",
           "user 列一位元未動")
        ck(rate_turn(R, 999999, "up", path=dbp) is None, "不存在的 turn → None")

        for bad in ("meh", "UP", "", None, 1, "up ", "neutral"):
            raised_bad = False
            try:
                rate_turn(R, tr, bad, path=dbp)
            except AssistantError:
                raised_bad = True
            ck(raised_bad, f"非法 rating {bad!r} 必須 raise（值域封閉、不靜默吞）")
        ck(rate_row(tr)["rating"] == "up", "非法值不得改動既有評分")

        _, tr_f = begin_turn(R, cidr, "會失敗的一題", None, "zh", path=dbp)
        fail_turn(R, tr_f, "ai_fail", path=dbp)
        ck(rate_turn(R, tr_f, "down", path=dbp) == {"error": "not_rateable"},
           "failed 的一輪不可評（同 pending）")
        ck(rate_row(tr_f)["rating"] is None, "failed 列一位元未寫")

        msgs_r = get_messages(R, cidr, path=dbp)
        ck(len(msgs_r) == 4, "rate_turn 不產生任何額外訊息列")
        ck(msgs_r[1]["rating"] == "up" and msgs_r[1]["rated_at"] is not None,
           "get_messages 帶出 rating／rated_at（panel 還原路徑要用）")
        ck(all("rating" in m and "rated_at" in m for m in msgs_r),
           "get_messages 每一列都有這兩鍵（user 列為 NULL）")
        ck(legacy_msgs[1].get("rating") is None,
           "舊 DB 的列 rating 讀出 NULL＝沒評過（逐位元照舊渲染）")

        # ---- U8 上下文三微調（2026-09-22；§13 ☐13／§12.9）---------------------------
        # ① idle 自動切段：**純讀**（下面每一根都只驗回傳值，conversation 一個位元不動）
        X = "aid-u8"
        cidx = add_turn(X, None, "x-q1", "x-a1", path=dbp)
        add_turn(X, cidx, "x-q2", "x-a2", path=dbp)
        add_turn(X, cidx, "x-q3", "x-a3", path=dbp)

        def set_created(mapping: dict) -> None:
            """把某幾則訊息的 created_at 壓成指定值（同 `force_updated` 的理由：_now() 只到
            秒 ⇒ 不釘死時戳，「間隔幾小時」這種斷言就是 flaky）。"""
            raw5 = sqlite3.connect(dbp)
            for content, ts in mapping.items():
                raw5.execute("UPDATE message SET created_at=? WHERE content=?", (ts, content))
            raw5.commit()
            raw5.close()

        D = "2026-09-20T"
        set_created({"x-q1": D + "07:59:59", "x-a1": D + "08:00:00",   # ← a1→q2 剛好 7200
                     "x-q2": D + "10:00:00", "x-a2": D + "10:00:01",   # ← a2→q3 是 7201
                     "x-q3": D + "12:00:02", "x-a3": D + "12:00:03"})
        conv_before = get_conversation(X, cidx, path=dbp)
        hx = [m["content"] for m in recent_history(X, cidx, path=dbp)]
        ck(hx == ["x-q3", "x-a3"],
           "★ U8①：間隔 7201 秒 > 2h ⇒ 在那裡切，只回切點之後（較新）那一段")
        ck("x-q2" not in hx and "x-a2" not in hx and "x-q1" not in hx,
           "U8①：切點之前（較舊）那一段一則都不餵")
        ck(get_conversation(X, cidx, path=dbp) == conv_before,
           "★ U8①**純讀**：切段不寫 closed_at、不動 conversation 列一個位元")
        ck(len(get_messages(X, cidx, path=dbp)) == 6, "U8①：切段不刪任何訊息列（只是不餵）")
        ck([m["content"] for m in recent_history(X, cidx, idle_split_s=7201, path=dbp)]
           == ["x-q1", "x-a1", "x-q2", "x-a2", "x-q3", "x-a3"],
           "★ U8①：門檻是**嚴格大於** ⇒ idle_split_s=7201 時那個 7201 的間隔不切（kwarg 覆寫生效）")
        ck(len(recent_history(X, cidx, idle_split_s=0, path=dbp)) == 6,
           "U8①：idle_split_s<=0 ⇒ 整個關掉（可注入的關閉開關）")
        ck([m["content"] for m in recent_history(X, cidx, idle_split_s=7199, path=dbp)]
           == ["x-q3", "x-a3"],
           "★ U8①：門檻壓到 7199 ⇒ 兩個間隔都夠長，取**最近**那一個（往回走遇到第一個就停；"
           "往前走的寫法會錯切在較舊的那個、多餵一整段別人的對話）")

        cidy = add_turn(X, None, "y-q1", "y-a1", path=dbp)
        add_turn(X, cidy, "y-q2", "y-a2", path=dbp)
        set_created({"y-q1": D + "07:59:59", "y-a1": D + "08:00:00",
                     "y-q2": D + "10:00:00", "y-a2": D + "10:00:01"})   # 間隔**恰好** 7200
        ck([m["content"] for m in recent_history(X, cidy, path=dbp)]
           == ["y-q1", "y-a1", "y-q2", "y-a2"],
           "★ U8①：間隔恰好 7200 ⇒ **不切**（> 不是 >=；邊界值不得靠運氣）")

        set_created({"x-a2": "not-a-timestamp"})
        ck(len(recent_history(X, cidx, path=dbp)) == 6,
           "★ U8①：壞掉的 created_at ⇒ 那個邊界不切（保守＝照舊全餵，不因壞時戳砍掉前文）")
        # NULL 走不到 DB（`created_at` 是 NOT NULL），但契約寫「NULL 視為不切」⇒ 釘在純函式上
        ck(_ts_gap_s(None, D + "12:00:00") is None and _ts_gap_s(D + "08:00:00", None) is None,
           "U8①：`_ts_gap_s` 對 None 回 None＝量不出來（呼叫端據此不切；與壞值同一條規則）")
        ck(_ts_gap_s(D + "08:00:00", D + "10:00:01") == 7201.0,
           "U8①：`_ts_gap_s` 正常路徑真的算得出秒數（不是整支被 except 吞掉）")

        # ② 歷史裡 assistant 回覆每則截 ~600 字（user 不截）
        long_a, long_u = "答" * 900, "問" * 900
        ca = cap_history([{"role": "assistant", "content": long_a}])[0]["content"]
        ck(ca[:600] == long_a[:600] and ca == long_a[:600] + _TRUNC_MARK,
           "★ U8②：assistant 超 600 ⇒ 截到 600 ＋接省略號（讓模型知道被截）")
        ck(len(ca) == 600 + len(_TRUNC_MARK), f"U8②：截後長度＝600+{len(_TRUNC_MARK)}")
        ck(cap_history([{"role": "user", "content": long_u}])[0]["content"] == long_u,
           "★ U8②：user 列**不截**（原句是問題本身，截掉等於改題目）")
        ck(cap_history([{"role": "assistant", "content": "a" * 600}])[0]["content"] == "a" * 600,
           "U8②：恰好 600 ⇒ 不截、也不加省略號")
        ck(cap_history([{"role": "assistant", "content": long_a}],
                       assistant_max_chars=0)[0]["content"] == long_a,
           "U8②：assistant_max_chars<=0 ⇒ 關掉這一關（可注入的關閉開關）")
        mixed = [{"role": "assistant", "content": "A" * 5000},
                 {"role": "user", "content": "B" * 5000},
                 {"role": "assistant", "content": "C" * 5000}]
        m8 = cap_history(mixed, max_chars=6000)
        ck([x["content"][0] for x in m8] == ["B", "C"] and len(m8[1]["content"]) == 602,
           "★ U8②順序：**先逐則截 assistant、再算總量丟最舊** ⇒ 同樣 6000 預算留下 2 則")
        ck(len(cap_history(mixed, max_chars=6000, assistant_max_chars=0)) == 1,
           "U8②：不截 assistant 的話同樣預算只留得下 1 則＝順序反過來就是退步（對照組）")

        # ③ 落帳兩欄 `prompt_chars`／`history_turns`
        def ps_row(tid: int) -> dict:
            raw7 = sqlite3.connect(dbp)
            raw7.row_factory = sqlite3.Row
            r7 = raw7.execute("SELECT role, status, content, prompt_chars, history_turns "
                              "FROM message WHERE id=?", (tid,)).fetchone()
            raw7.close()
            return dict(r7) if r7 is not None else {}

        cidp, tp = begin_turn(X, None, "落帳這一題", None, "zh", path=dbp)
        up = get_turn(X, tp, path=dbp)["user_msg_id"]
        _, tp2 = begin_turn(X, cidp, "還在跑的一題", None, "zh", path=dbp)
        ck(record_prompt_stats(tp2, 77, 2, path=dbp) is True and ps_row(tp2)["status"] == "pending",
           "★ U8③：**打 gateway 之前**就落帳 ⇒ pending 列可寫，且 status 仍是 pending")
        complete_turn(X, tp, "答好了", path=dbp)
        ck(record_prompt_stats(tp, 1234, 6, path=dbp) is True, "U8③：寫到一列回 True")
        ck(ps_row(tp)["prompt_chars"] == 1234 and ps_row(tp)["history_turns"] == 6,
           "U8③：兩欄逐欄落庫")
        ck(ps_row(tp)["status"] is None and ps_row(tp)["content"] == "答好了",
           "★ U8③：**絕不碰 `status`**（U5 CAS 守衛）、也不碰 content")
        ck(record_prompt_stats(tp, 0, 0, path=dbp) is True and ps_row(tp)["prompt_chars"] == 0
           and ps_row(tp)["history_turns"] == 0,
           "U8③：可重複呼叫（後寫覆蓋前寫）；0 是合法值（新對話＝零歷史）")
        ck(record_prompt_stats(up, 5, 1, path=dbp) is None and ps_row(up)["prompt_chars"] is None,
           "★ U8③：拿 user 列 id 來寫 → None、user 列一位元不動（role='assistant' 守衛）")
        ck(record_prompt_stats(999999, 5, 1, path=dbp) is None, "U8③：查無那一列 → None（不 raise）")
        for bad in (-1, 1.5, "8", None, True):
            raised_ps = False
            try:
                record_prompt_stats(tp, bad, 0, path=dbp)
            except ValueError:
                raised_ps = True
            ck(raised_ps, f"U8③：prompt_chars={bad!r} 必須 raise ValueError（壞值進統計表＝說謊）")
        for bad in (-3, "2", None):
            raised_ps = False
            try:
                record_prompt_stats(tp, 10, bad, path=dbp)
            except ValueError:
                raised_ps = True
            ck(raised_ps, f"U8③：history_turns={bad!r} 必須 raise ValueError")
        ck(ps_row(tp)["prompt_chars"] == 0 and ps_row(tp)["history_turns"] == 0,
           "U8③：非法值不得改動既有落帳（raise 在開連線之前）")
        _, tp3 = begin_turn(X, cidp, "沒落過帳的一題", None, "zh", path=dbp)
        ck(ps_row(tp3)["prompt_chars"] is None and ps_row(tp3)["history_turns"] is None,
           "U8③：沒落過帳的列讀出 NULL＝**沒量到**（不是 0；0 是真的量到零歷史）")

        # ---- U9 每日 LLM 呼叫保險絲（2026-09-22；`SPEC-HERMES-TRUST.md` §12.10）----
        # ⚠ 這一組用**獨立的 tempfile DB**（`dbb`）：保險絲是全域計數，混進上面那顆
        #   會讓「用了幾次」跟著別組的順序漂移，紅起來沒人知道是誰動的。
        dbb = os.path.join(tmpdir, "budget.sqlite3")
        DAY, DAY2 = "2026-09-22", "2026-09-23"
        # (a) 缺表自建：全新檔第一次讀就要拿得到 0（`_SCHEMA` 的 IF NOT EXISTS）
        ok0, used0, cap0 = llm_budget_check("assistant", path=dbb, day=DAY, cap=5)
        ck(ok0 is True and used0 == 0 and cap0 == 5,
           "U9：DB 缺表自建 ⇒ 第一次 check 回 (True, 0, cap)")
        ck(os.path.exists(dbb), "U9：check 真的建出 DB 檔（不是在記憶體裡假裝）")
        # (b) cap=0 ＝不限（明示關掉保險絲的唯一寫法）
        for _ in range(3):
            llm_budget_commit("assistant", path=dbb, day=DAY)
        ok, used, cap = llm_budget_check("assistant", path=dbb, day=DAY, cap=0)
        ck(ok is True and used == 3 and cap == 0,
           "U9：cap=0 ⇒ 不限（已用 3 次照樣 allowed；used 仍誠實回報）")
        # (c) cap=1：第一次過、commit 後第二次擋
        ck(llm_budget_check("assistant", path=dbb, day=DAY2, cap=1)[0] is True,
           "U9：新的一天 cap=1 ⇒ 第一次 allowed（跨日重置）")
        llm_budget_commit("assistant", path=dbb, day=DAY2)
        ok2, used2, cap2 = llm_budget_check("assistant", path=dbb, day=DAY2, cap=1)
        ck(ok2 is False and used2 == 1 and cap2 == 1,
           "U9：cap=1 ＋ 已用 1 ⇒ **第二次被擋**（allowed=False，值照實回）")
        # (d) 跨日重置＝新的一個 day 鍵，不必清理作業；舊列原封留著當歷史
        ck(llm_budget_check("assistant", path=dbb, day="2026-09-24", cap=1)[0] is True,
           "U9：再跨一天 ⇒ 又能打（跨日自動重置，無清理作業）")
        ck(llm_budget_check("assistant", path=dbb, day=DAY, cap=99)[1] == 3,
           "U9：舊日子的計數原封保留（歷史不被重置抹掉）")
        # (e) ★ **兩個 kind ＝兩個獨立的桶**（2026-09-22 Chris 裁 (a)）：各算各的 used、
        #     各有各的上限，互不吃對方的額度；分 kind 落列照舊看得出誰在燒。
        llm_budget_commit("translate", path=dbb, day=DAY2)
        ok3, used3, _ = llm_budget_check("assistant", path=dbb, day=DAY2, cap=2)
        ck(ok3 is True and used3 == 1,
           "U9：★ **兩桶獨立**——translate 燒掉的那一次**不算進 assistant 的 used**")
        ck(llm_budget_check("translate", path=dbb, day=DAY2, cap=2)[1] == 1,
           "U9：translate 這側 check 讀到的是**翻譯桶自己**的 used（不是合計）")
        stt = llm_budget_status(path=dbb, day=DAY2)
        ck(stt["day"] == DAY2 and stt["used"] == 1 and stt["translate_used"] == 1
           and stt["by_kind"] == {"assistant": 1, "translate": 1}
           and set(stt["caps"]) == {"assistant", "translate"},
           f"U9：status 的 `used`＝**助理桶**、`translate_used`＝翻譯桶、`by_kind` 原封"
           f"、加性 `caps` 兩桶上限（實得 {stt!r}）")
        # (f) commit 回傳加完後**那一桶**的次數；upsert 跨連線持久（每操作一條新連線）
        ck(llm_budget_commit("translate", path=dbb, day=DAY2) == 2,
           "U9：commit 回傳加完後**該 kind 當日的次數**（不是兩 kind 合計）")
        # #167 P3：第三桶 `summary`（各自一桶、各自一個 env；`caps` 兩鍵不動、加性鍵帶出）
        ck(llm_budget_cap({}, kind="summary") == SUMMARY_DAILY_MAX_DEFAULT == 100
           and llm_budget_cap({ENV_SUMMARY_DAILY_MAX: "0"}, kind="summary") == 0
           and llm_budget_cap({ENV_LLM_DAILY_MAX: "7"}, kind="summary") == 100,
           "#167：summary 桶預設 100、0＝不限、助理 env 不外溢")
        ck(llm_budget_commit("summary", path=dbb, day="2099-12-31") == 1
           and llm_budget_check("assistant", path=dbb, day="2099-12-31", cap=99)[1] == 0
           and llm_budget_status(path=dbb, day="2099-12-31")["summary_used"] == 1
           and set(llm_budget_status(path=dbb, day="2099-12-31")["caps"]) == {"assistant", "translate"},
           "#167：summary 燒一次不進助理桶；status 加性 summary_used；caps 兩鍵不動")
        ck(llm_budget_check("translate", path=dbb, day=DAY2, cap=99)[1] == 2,
           "U9：upsert 跨連線持久（重開連線讀得到）")
        conn_u = _connect(dbb)
        try:
            ck(_llm_used(conn_u, DAY2) == 3
               and _llm_used(conn_u, DAY2, "assistant") == 1
               and _llm_used(conn_u, DAY2, "translate") == 2,
               "U9：`_llm_used` 不給 kind＝**所有 kind 合計**（觀測用；防線一律給 kind）")
        finally:
            conn_u.close()
        # (e2) ★ 裁 (a) 的那個情境本身：翻譯狂燒**不吃**助理的額度；助理燒到底也**擋不住**翻譯
        DAY3 = "2026-09-25"
        for _ in range(5):
            llm_budget_commit("translate", path=dbb, day=DAY3)
        ok_a3, used_a3, cap_a3 = llm_budget_check("assistant", path=dbb, day=DAY3, cap=5)
        ck(ok_a3 is True and used_a3 == 0 and cap_a3 == 5,
           "U9：★ 5 次翻譯呼叫之後，助理那一桶**仍是 0/5、整份額度原封**"
           "（＝裁 (a) 要解決的那個失效：重啟後整頁重翻不該把助理鎖在門外）")
        for _ in range(5):
            llm_budget_commit("assistant", path=dbb, day=DAY3)
        ck(llm_budget_check("assistant", path=dbb, day=DAY3, cap=5)[0] is False,
           "U9：助理桶滿 5/5 ⇒ 助理這側被擋")
        ok_t3, used_t3, _ = llm_budget_check("translate", path=dbb, day=DAY3, cap=10)
        ck(ok_t3 is True and used_t3 == 5,
           "U9：★ **助理桶滿的同一刻，翻譯照樣放行**（翻譯只看自己那 5/10）")
        # (g) kind 打錯字當場炸（靜默記到沒人看的桶＝保險絲失效的第一步）
        for bad_kind in ("assist", "", None, "Assistant"):
            raised_k = False
            try:
                llm_budget_check(bad_kind, path=dbb, day=DAY2)
            except ValueError:
                raised_k = True
            ck(raised_k, f"U9：check 的 kind={bad_kind!r} 必須 raise ValueError")
            raised_k = False
            try:
                llm_budget_commit(bad_kind, path=dbb, day=DAY2)
            except ValueError:
                raised_k = True
            ck(raised_k, f"U9：commit 的 kind={bad_kind!r} 必須 raise ValueError")
        # (h) cap 的 env 解析：未設＝預設 200；`0`＝不限；**垃圾值退回預設、不是不限**
        ck(llm_budget_cap({}) == LLM_DAILY_MAX_DEFAULT == 200,
           f"U9：env 未設 → 預設 {LLM_DAILY_MAX_DEFAULT}（Chris 指定 200）")
        ck(llm_budget_cap({ENV_LLM_DAILY_MAX: "0"}) == 0, "U9：env=0 → 0（不限）")
        ck(llm_budget_cap({ENV_LLM_DAILY_MAX: " 50 "}) == 50, "U9：env 前後空白容忍")
        for junk in ("abc", "-1", "1.5", "", "  "):
            ck(llm_budget_cap({ENV_LLM_DAILY_MAX: junk}) == LLM_DAILY_MAX_DEFAULT,
               f"U9：env={junk!r}（垃圾/負數）→ **退回預設、不是不限**"
               "（設錯一個字就全開＝這種閘最典型的失效方式）")
        # (h2) ★ 翻譯桶自己的 env（裁 (a)）：同一套規則、**另一個 env、另一個預設**
        ck(llm_budget_cap({}, kind="translate") == TRANSLATE_DAILY_MAX_DEFAULT == 600,
           f"U9：★ 翻譯桶 env 未設 → 預設 {TRANSLATE_DAILY_MAX_DEFAULT}（Chris 裁 (a)）")
        ck(llm_budget_cap({}) == 200 and llm_budget_cap({}, kind="assistant") == 200,
           "U9：★ `kind` 預設＝`assistant` ⇒ 既有呼叫端 `llm_budget_cap({})` 仍回 200（向後相容）")
        ck(llm_budget_cap({ENV_TRANSLATE_DAILY_MAX: "0"}, kind="translate") == 0,
           "U9：翻譯桶 env=0 → 0（不限）")
        ck(llm_budget_cap({ENV_TRANSLATE_DAILY_MAX: " 42 "}, kind="translate") == 42,
           "U9：翻譯桶 env 前後空白容忍")
        for junk in ("abc", "-1", "1.5", "", "  "):
            ck(llm_budget_cap({ENV_TRANSLATE_DAILY_MAX: junk},
                              kind="translate") == TRANSLATE_DAILY_MAX_DEFAULT,
               f"U9：翻譯桶 env={junk!r}（垃圾/負數）→ **退回 600、不是不限**")
        ck(llm_budget_cap({ENV_LLM_DAILY_MAX: "7"}, kind="translate") == 600
           and llm_budget_cap({ENV_TRANSLATE_DAILY_MAX: "7"}) == 200,
           "U9：★ 兩個 env **互不外溢**（設助理的不會改到翻譯桶，反之亦然）")
        # (i) 日鍵＝台北牆鐘日曆日（同 `_now()` 的時間語意，刻意不引 zoneinfo）
        ck(llm_budget_day(datetime(2026, 9, 22, 23, 59, 59)) == "2026-09-22"
           and llm_budget_day(datetime(2026, 9, 23, 0, 0, 0)) == "2026-09-23",
           "U9：日鍵＝`YYYY-MM-DD`、午夜換日（Asia/Taipei 牆鐘，同 `_now()`）")
        ck(len(llm_budget_day()) == 10 and llm_budget_day()[4] == "-",
           "U9：不帶參數的 day 也是 `YYYY-MM-DD`")
        # (j) ★ 保險絲**絕不碰 message 表**（它與那一輪答得成不成完全無關）
        conn_b = _connect(dbb)
        try:
            tabs = {r["name"] for r in conn_b.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn_b.close()
        ck({"llm_budget", "message", "conversation"} <= tabs,
           f"U9：llm_budget 與既有兩表並存＝additive（實得 {sorted(tabs)}）")
        ck(_connect(dbb).execute("SELECT COUNT(*) c FROM message").fetchone()["c"] == 0,
           "U9：整組保險絲測試**一列 message 都沒寫**（兩件事互不相干）")

        # ---- U10 `tool_call` 表（2026-09-22；§12.12／§13 ☐2 (a)）--------------------
        # ⚠ 同 U9：獨立 tempfile DB（`dbt`），別讓別組的列漂進這裡的計數。
        dbt = os.path.join(tmpdir, "toolcall.sqlite3")
        conn_t = _connect(dbt)
        try:
            tabs_t = {r["name"] for r in conn_t.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            idx_t = {r["name"] for r in conn_t.execute(
                "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
            cols_t = [r["name"] for r in conn_t.execute(
                "PRAGMA table_info(tool_call)").fetchall()]
        finally:
            conn_t.close()
        ck("tool_call" in tabs_t and {"llm_budget", "message", "conversation"} <= tabs_t,
           f"U10：`_connect` 缺表自建 tool_call、與既有三表並存＝additive（實得 {sorted(tabs_t)}）")
        ck("tool_call_turn" in idx_t, f"U10：`tool_call(turn_id, id)` 索引也自建（實得 {sorted(idx_t)}）")
        ck(cols_t == ["id", "turn_id", "tool", "params_hash", "provenance",
                      "result_json", "created_at", "created_at_utc"],
           f"U10＋U13：原七欄逐欄定死、U13 加性欄 `created_at_utc` 只准排在最後（實得 {cols_t}）")
        PROV_A = {"tool": "ping", "confidence": "high", "row_count": None}
        PROV_B = {"tool": "fleet_scoreboard", "confidence": "high", "row_count": 38}
        rid1 = tool_call_record(7, "ping", "abc12345", PROV_A,
                                {"status": "ok", "source": "demo"}, path=dbt)
        rid2 = tool_call_record(7, "fleet_scoreboard", "def67890", PROV_B,
                                {"days": 3, "fleet": {"util_pct": 21.4}}, path=dbt)
        ck(isinstance(rid1, int) and isinstance(rid2, int) and rid2 > rid1,
           f"U10：writer 回新列 id 且遞增（{rid1} → {rid2}）")
        rows_t = tool_calls_for_turn(7, path=dbt)
        ck(len(rows_t) == 2 and [r["tool"] for r in rows_t] == ["ping", "fleet_scoreboard"],
           f"U10：reader 依 id 遞增＝**呼叫順序**（實得 {[r['tool'] for r in rows_t]}）")
        ck(rows_t[0]["provenance"] == PROV_A and rows_t[1]["provenance"] == PROV_B,
           "U10：`provenance` 存進去什麼、讀出來就是什麼（dict → JSON → dict 無失真）")
        ck(rows_t[1]["result"] == {"days": 3, "fleet": {"util_pct": 21.4}},
           f"U10：`result` 巢狀結構往返無失真（實得 {rows_t[1]['result']!r}）")
        ck(rows_t[0]["params_hash"] == "abc12345" and rows_t[0]["turn_id"] == 7,
           "U10：`params_hash`／`turn_id` 原值回讀")
        ck(len(rows_t[0]["created_at"]) == 19 and rows_t[0]["created_at"][10] == "T",
           f"U10：`created_at` ＝ `YYYY-MM-DDTHH:MM:SS`（U13 起台北牆鐘；實得 {rows_t[0]['created_at']!r}）")
        # ── U13（2026-09-25；四十七 ▶⑥）：`created_at` 與 message 同一種寫法、UTC 另進加性欄 ──
        _u12_off = datetime.now().astimezone().utcoffset()
        _u12_loc = datetime.fromisoformat(rows_t[0]["created_at"])
        _u12_utc = datetime.fromisoformat(rows_t[0]["created_at_utc"])
        ck(_u12_loc - _u12_utc == _u12_off,
           f"U13：★ 新列 `created_at`（本機牆鐘）−`created_at_utc`（UTC）＝本機時差 {_u12_off}"
           f"（同一瞬間兩種寫法；實得 {rows_t[0]['created_at']!r} vs {rows_t[0]['created_at_utc']!r}）")
        ck(len(rows_t[0]["created_at_utc"]) == 19 and rows_t[0]["created_at_utc"][10] == "T"
           and "+" not in rows_t[0]["created_at_utc"] and "Z" not in rows_t[0]["created_at_utc"],
           "U13：`created_at_utc` ＝ UTC `YYYY-MM-DDTHH:MM:SS`（無時區後綴、與舊 `created_at` 同形）")
        ck(len(_now()) == len(rows_t[0]["created_at"]) and "+" not in rows_t[0]["created_at"]
           and abs((datetime.fromisoformat(_now()) - _u12_loc).total_seconds()) < 120,
           "U13：★ 新列 `created_at` 與 `message.created_at`（`_now()`）同形同牆鐘（不再差 8h）")
        _pl, _pu = _now_pair()
        ck(datetime.fromisoformat(_pl) - datetime.fromisoformat(_pu) == _u12_off,
           "U13：`_now_pair()` 兩個字串取自同一瞬間（差值恰＝時差、不會因跨秒差 1 秒）")
        # 舊列不回溯：模擬 U13 前的舊 DB（七欄、created_at 是 UTC），_connect 後加欄、舊列欄值 NULL、reader COALESCE
        _dbo = os.path.join(tmpdir, "toolcall_legacy.sqlite3")
        _rawo = sqlite3.connect(_dbo)
        _rawo.execute("CREATE TABLE tool_call(id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id INTEGER NOT NULL,"
                      " tool TEXT NOT NULL, params_hash TEXT NOT NULL, provenance TEXT NOT NULL,"
                      " result_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        _rawo.execute("INSERT INTO tool_call(turn_id,tool,params_hash,provenance,result_json,created_at)"
                      " VALUES(5,'ping','old00000','{}','{}','2026-09-24T12:00:00')")
        _rawo.commit()
        _rawo.close()
        _old = tool_calls_for_turn(5, path=_dbo)
        ck(_old[0]["created_at"] == "2026-09-24T12:00:00" and _old[0]["created_at_utc"] == "2026-09-24T12:00:00",
           f"U13：★ 舊列**不回溯**：`created_at` 原字串一位元不動、`created_at_utc` 由 reader 取舊值（那時就是 UTC；實得 {_old[0]!r}）")
        _c_o = _connect(_dbo)
        try:
            _raw_old_utc = _c_o.execute("SELECT created_at_utc FROM tool_call WHERE turn_id=5").fetchone()[0]
        finally:
            _c_o.close()
        ck(_raw_old_utc is None, "U13：舊列在 DB 裡 `created_at_utc` 仍是 NULL（migration 只加欄、不 UPDATE 任何列）")
        tool_call_record(5, "ping", "new00000", {}, {}, path=_dbo)
        _mix = tool_calls_for_turn(5, path=_dbo)
        ck(len(_mix) == 2 and _mix[1]["created_at_utc"] != _mix[1]["created_at"] or _u12_off.total_seconds() == 0,
           "U13：舊庫升級後新寫的列走新寫法（created_at 本機牆鐘、created_at_utc UTC）")
        _connect(_dbo).close()
        ck(len(tool_calls_for_turn(5, path=_dbo)) == 2, "U13：migration 冪等（再連一次不炸、不重加欄）")
        # 兩輪隔離：別輪的列絕不漏進來
        tool_call_record(8, "ping", "zzz00000", PROV_A, {"status": "ok"}, path=dbt)
        ck(len(tool_calls_for_turn(7, path=dbt)) == 2
           and len(tool_calls_for_turn(8, path=dbt)) == 1,
           "U10：**兩輪彼此隔離**（turn 7 不會撿到 turn 8 的列）")
        ck(tool_calls_for_turn(999, path=dbt) == [],
           "U10：沒有工具呼叫的 turn ⇒ **空 list**（不是錯、不是 None）")
        # 參數驗證：壞值當場炸（靜默寫進去＝日誌開始說謊的第一步）
        for bad_turn in (0, -1, "7", None, True, 1.5):
            raised_t = False
            try:
                tool_call_record(bad_turn, "ping", "h", {}, {}, path=dbt)
            except ValueError:
                raised_t = True
            ck(raised_t, f"U10：turn_id={bad_turn!r} 必須 raise ValueError")
        for bad_tool in ("", None, 3):
            raised_t = False
            try:
                tool_call_record(7, bad_tool, "h", {}, {}, path=dbt)
            except ValueError:
                raised_t = True
            ck(raised_t, f"U10：tool={bad_tool!r} 必須 raise ValueError")
        for bad_prov in (None, "x", [1]):
            raised_t = False
            try:
                tool_call_record(7, "ping", "h", bad_prov, {}, path=dbt)
            except ValueError:
                raised_t = True
            ck(raised_t, f"U10：provenance={bad_prov!r}（非 dict）必須 raise ValueError")
        raised_t = False
        try:
            tool_call_record(7, "ping", 123, {}, {}, path=dbt)
        except ValueError:
            raised_t = True
        ck(raised_t, "U10：params_hash 非字串必須 raise ValueError")
        ck(len(tool_calls_for_turn(7, path=dbt)) == 2,
           "U10：非法值**一列都沒寫進去**（raise 在開連線之前）")
        # 1 MB 截斷：整列不因一個巨大回傳而爆表，且**誠實標記**
        big = {"rows": ["x" * 1000] * 1200}          # ≈1.2 MB JSON
        rid_big = tool_call_record(9, "wo_serials_status", "big00000", PROV_A, big, path=dbt)
        row_big = tool_calls_for_turn(9, path=dbt)[0]
        ck(isinstance(rid_big, int) and row_big["result"].get("_truncated") is True
           and row_big["result"]["bytes"] > _TOOL_CALL_RESULT_MAX,
           f"U10：result JSON 超過 {_TOOL_CALL_RESULT_MAX} 位元組 ⇒ 存截斷標記"
           f"（實得 {row_big['result']!r}）")
        ck(row_big["provenance"] == PROV_A and row_big["tool"] == "wo_serials_status",
           "U10：截斷只發生在 `result`，信封與工具名**照常保留**")
        small = {"rows": ["x" * 1000] * 10}
        tool_call_record(9, "ping", "small000", PROV_A, small, path=dbt)
        ck(tool_calls_for_turn(9, path=dbt)[1]["result"] == small,
           "U10：沒超過上限的照原樣存（截斷是例外、不是常態）")
        # ★ 本表**絕不碰 message／llm_budget**（它與那一輪答得成不成、與 LLM 配額都無關）
        conn_t2 = _connect(dbt)
        try:
            n_msg_t = conn_t2.execute("SELECT COUNT(*) c FROM message").fetchone()["c"]
            n_bud_t = conn_t2.execute("SELECT COUNT(*) c FROM llm_budget").fetchone()["c"]
            n_tc_t = conn_t2.execute("SELECT COUNT(*) c FROM tool_call").fetchone()["c"]
        finally:
            conn_t2.close()
        ck(n_msg_t == 0 and n_bud_t == 0,
           f"U10：★ 整組 tool_call 測試**一列 message／llm_budget 都沒寫**"
           f"（實得 message={n_msg_t} llm_budget={n_bud_t}）")
        ck(n_tc_t == 5, f"U10：tool_call 實際列數＝5（turn7 兩列＋turn8 一列＋turn9 兩列；"
                        f"實得 {n_tc_t}）")

        # -------------------------------------------------------------------
        # U11 `tool_call_count` ＋ `result_json` 原字串（#153 匯出 CSV 的兩個前置）
        # -------------------------------------------------------------------
        ck(tool_call_count(999, path=dbt) == 0,
           "U11：沒有工具呼叫的 turn ⇒ `tool_call_count` ＝ **0**（不是 None、不是錯）")
        ck(tool_call_count(8, path=dbt) == 1,
           f"U11：恰一列的 turn ⇒ 1（實得 {tool_call_count(8, path=dbt)}）")
        ck(tool_call_count(7, path=dbt) == 2,
           f"U11：N 列的 turn ⇒ N（turn 7 兩列；實得 {tool_call_count(7, path=dbt)}）")
        # 逐輪隔離：新增一列只動那一輪的數字，別輪一位元不動
        _n7_before, _n9_before = tool_call_count(7, path=dbt), tool_call_count(9, path=dbt)
        tool_call_record(8, "ping", "iso00000", PROV_A, {"status": "ok"}, path=dbt)
        ck(tool_call_count(8, path=dbt) == 2
           and tool_call_count(7, path=dbt) == _n7_before
           and tool_call_count(9, path=dbt) == _n9_before,
           "U11：**逐輪隔離**——給 turn 8 加一列，turn 7／9 的數字一位元不動")
        ck(tool_call_count(7, path=dbt) + tool_call_count(8, path=dbt)
           + tool_call_count(9, path=dbt) == 6,
           "U11：三輪加總＝全表列數（沒有列被算兩次、也沒有列漏掉）")
        _raised_tc = False
        try:
            tool_call_count("x", path=dbt)
        except ValueError:
            _raised_tc = True
        ck(_raised_tc, "U11：`turn_id` 非整數 ⇒ ValueError（不靜默回 0＝假的『這輪沒工具』）")
        # `result_json` ＝**存進去的那一份原字串**（#153 匯出要的是工具原值、不是重序列化）
        _rows_rj = tool_calls_for_turn(7, path=dbt)
        ck(all("result_json" in r and isinstance(r["result_json"], str) for r in _rows_rj),
           "U11：reader 每筆都帶 `result_json`（原字串）")
        ck(json.loads(_rows_rj[1]["result_json"]) == _rows_rj[1]["result"],
           "U11：`result_json` 解析回來＝`result`（同一份東西的兩種取用，不是兩份事實）")
        ck(_rows_rj[1]["result_json"] == json.dumps({"days": 3, "fleet": {"util_pct": 21.4}},
                                                    ensure_ascii=False, default=str),
           f"U11：原字串逐位元＝writer 當時 `json.dumps` 的輸出（鍵序／空白未被換掉；"
           f"實得 {_rows_rj[1]['result_json']!r}）")
        ck(json.loads(tool_calls_for_turn(9, path=dbt)[0]["result_json"]).get("_truncated")
           is True,
           "U11：截斷過的那一列，`result_json` 就是那個**誠實標記**（不是半截 JSON）")
        # `get_messages` 也供數（panel 一次渲染整段對話 ⇒ 一次 GROUP BY、不是逐則 COUNT）
        _msgs_tc = get_messages(A1, cid1, path=dbp)
        ck(all("tool_call_count" in m for m in _msgs_tc),
           "U11：`get_messages` **每一列**都有 `tool_call_count`（user 列也有，為 0）")
        ck(all(m["tool_call_count"] == 0 for m in _msgs_tc),
           "U11：還沒有任何工具呼叫 ⇒ 整段對話的 `tool_call_count` 全 0")
        _asst_ids = [m["id"] for m in _msgs_tc if m["role"] == "assistant"]
        tool_call_record(_asst_ids[0], "ping", "gm000000", PROV_A, {"status": "ok"}, path=dbp)
        tool_call_record(_asst_ids[0], "ping", "gm000001", PROV_A, {"status": "ok"}, path=dbp)
        _msgs_tc2 = {m["id"]: m["tool_call_count"] for m in get_messages(A1, cid1, path=dbp)}
        ck(_msgs_tc2[_asst_ids[0]] == 2
           and all(v == 0 for k, v in _msgs_tc2.items() if k != _asst_ids[0]),
           f"U11：★ `get_messages` 的數字落在**對的那一則**上（實得 {_msgs_tc2}）")

        # ---- U14 #137 A3「依據」折疊區的 chip（`SPEC-HERMES-TRUST.md` §5.1）----
        PROV_FULL = {"tool": "fleet_scoreboard", "metric_ids": [], "queries": [],
                     "window": {"from": "2026-09-30 08:00:00", "to": "2026-10-01 08:00:00"},
                     "fetched_at": "2026-10-01 09:00:00", "freshness": "live", "row_count": 3,
                     "caliber": "raw-equipment-axis", "confidence": "high",
                     "page": "/utilization",
                     "evidence_ref": "fleet_scoreboard:2026-10-01T09:00:00:abcd1234"}
        _c = basis_chip("fleet_scoreboard", PROV_FULL)
        ck(set(_c) == {"tool", "window", "window_full", "freshness", "caliber", "confidence",
                       "fetched_at", "evidence_ref", "page"},
           f"U14：chip 鍵定死（實得 {sorted(_c)}）")
        ck((_c["tool"], _c["freshness"], _c["caliber"], _c["confidence"])
           == ("fleet_scoreboard", "live", "raw-equipment-axis", "high"),
           "U14：§5.1 五欄的四個字串欄原樣（枚舉值不翻譯＝與 CSV 匯出同一個字）")
        ck(_c["window"] == "2026-09-30 08:00 → 2026-10-01 08:00"
           and _c["window_full"] == "2026-09-30 08:00:00 → 2026-10-01 08:00:00",
           f"U14：跨日窗短形到分鐘、全形留秒（{_c['window']!r}／{_c['window_full']!r}）")
        _c_sd = basis_chip("x", dict(PROV_FULL, window={"from": "2026-10-01 08:00:00",
                                                        "to": "2026-10-01 09:30:00"}))
        ck(_c_sd["window"] == "2026-10-01 08:00–09:30", f"U14：同日窗收成一個日期（{_c_sd['window']!r}）")
        ck(_c["page"] == "/utilization" and _c["fetched_at"] == "2026-10-01 09:00:00"
           and _c["evidence_ref"] == PROV_FULL["evidence_ref"],
           "U14：page／fetched_at／evidence_ref 原樣（tooltip 與連結素材）")
        _c_none = basis_chip("ping", {"window": {"from": None, "to": None}})
        ck(_c_none["window"] == "—" and _c_none["window_full"] == ""
           and _c_none["freshness"] == _c_none["caliber"] == _c_none["confidence"] == "—"
           and _c_none["page"] is None,
           "U14：缺欄 ⇒「—」（不是空白、不是 0）；無 page ⇒ None＝不掛連結")
        for _ok_pg in ("/machine/EID-12724", "/station/segCalibrationTest",
                       "/utilization?cls=segCalibrationTest&days=7", "/"):
            ck(basis_chip("t", {"page": _ok_pg})["page"] == _ok_pg,
               f"U14：安全 site-relative page 照收 {_ok_pg!r}")
        for _bad_pg in ("//evil.example/x", "javascript:alert(1)", "http://x/y", "/station/{cls}",
                        "/a\\b", "/a b", '/a"b', "/a<b>", "relative/path", "", None, 5,
                        "/" + "a" * 300):
            ck(basis_chip("t", {"page": _bad_pg})["page"] is None,
               f"U14：★ 不安全／樣板／非 site-relative 的 page ⇒ None（不掛連結）：{_bad_pg!r}")
        _c_bad = basis_chip("ping", "not-a-dict")
        ck(_c_bad["tool"] == "ping" and _c_bad["window"] == "—" and _c_bad["page"] is None,
           "U14：壞信封（非 dict）⇒ 不 raise、工具名照留、其餘「—」")
        ck(basis_chip("pi\nng\x7f", {})["tool"] == "ping" and basis_chip(None, {})["tool"] == "—",
           "U14：工具名去控制字元；缺 ⇒「—」")
        ck(len(basis_chip("t", {"caliber": "c" * 500})["caliber"]) == _BASIS_FIELD_MAX,
           "U14：超長欄截到上限（UI 素材、不是事實存證；原值在 CSV）")
        _tb7 = tool_basis_for_turn(7, path=dbt)
        ck([c["tool"] for c in _tb7] == [r["tool"] for r in tool_calls_for_turn(7, path=dbt)]
           and len(_tb7) == tool_call_count(7, path=dbt),
           f"U14：`tool_basis_for_turn` 依呼叫順序、顆數＝`tool_call_count`（實得 {[c['tool'] for c in _tb7]}）")
        ck(tool_basis_for_turn(999, path=dbt) == [], "U14：零呼叫的 turn ⇒ 空 list（例外才出現的依據）")
        _raised_tb = False
        try:
            tool_basis_for_turn("x", path=dbt)
        except ValueError:
            _raised_tb = True
        ck(_raised_tb, "U14：`turn_id` 非整數 ⇒ ValueError（同 tool_call_count）")
        _gm_tb = {m["id"]: m.get("tool_basis") for m in get_messages(A1, cid1, path=dbp)}
        ck(all(isinstance(v, list) for v in _gm_tb.values())
           and [c["tool"] for c in _gm_tb[_asst_ids[0]]] == ["ping", "ping"]
           and all(v == [] for k, v in _gm_tb.items() if k != _asst_ids[0]),
           f"U14：★ `get_messages` 同一次查詢供 `tool_basis`、落在對的那一則（實得 {_gm_tb}）")
        ck({m["id"]: m["tool_call_count"] for m in get_messages(A1, cid1, path=dbp)} == _msgs_tc2,
           "U14：改成同一次查詢後 `tool_call_count` 逐則與 U11 那一份相同（數字不漂）")

        # ---- U12 #177 失敗答法守門（純函式；`_ASSISTANT-EVAL-20260924-TOOLKIT2.md` Q1–Q3 原文當 fixture）----
        g = plain_failure_guard
        BLACK = ("found=", "found =", "unavailable_reason", "OperationalError", "error:", "Traceback",
                 "pyodbc", "error_detail", "SQLSTATE", "08001")
        TC_ERR = [{"tool": "segment_data",
                   "result": {"found": False, "unavailable_reason": "error:OperationalError"}}]
        TC_NF = [{"tool": "wo_materials",
                  "result": {"found": False, "unavailable_reason": "no_production_request_matched"}}]
        TC_OK = [{"tool": "station_spec_limits",
                  "result": {"found": True, "unavailable_reason": "no_pass_criteria_limits"}}]
        N_UR, N_NF = FAIL_NOTICE["unreachable"], FAIL_NOTICE["not_found"]
        for _k, _d in FAIL_NOTICE.items():
            ck(all(b not in _d[_l] for _l in ("zh", "en") for b in BLACK)
               and "不代表" in _d["zh"] and "does not mean" in _d["en"],
               f"U12：標準句 {_k} 兩語皆白話（零內部字樣）且帶「不代表／does not mean」")
        Q1 = ("目前查不到 J0SYN03 在 segCalibrationTest 的製程數據，因為即時資料庫連線逾時。\n\n"
              "工具回傳 found=false，沒有任何量測段或數值可報告。")
        o1, h1 = g(Q1, "zh-TW", TC_ERR)
        ck(o1.startswith("目前查不到 J0SYN03 在 segCalibrationTest 的製程數據，因為即時資料庫連線逾時。")
           and o1.endswith(N_UR["zh"]) and N_NF["zh"] not in o1,
           f"U12 ①②：★ Q1 連線失敗（工具 error:OperationalError）⇒ 首句原樣留、補「連不上」白話句、不串「查無」句（{o1!r}）")
        ck(all(b not in o1 for b in BLACK) and "工具回傳" not in o1,
           "U12 ③：★ Q1「工具回傳 found=false」整句拿掉（只有內部字樣、沒有數字事實）")
        ck("found=false" in h1 and "+notice:unreachable" in h1, f"U12：hits 記得住命中了什麼（{h1}）")
        o1b, _ = g(Q1, "zh-TW", None)
        ck(o1b.endswith(N_NF["zh"]) and "found" not in o1b,
           "U12：沒有工具日誌（舊 gateway 不帶 turn）⇒ 只靠字樣判；found=false 單獨出現＝查無句")
        Q3 = "目前工具無法回答 CAL 這 7 天最常見的失效碼。"
        o3, h3 = g(Q3, "zh-TW", TC_ERR)
        ck(o3 == Q3 + "\n\n" + N_UR["zh"] and h3 == ["+notice:unreachable"],
           "U12 ②：★ Q3 本輪工具全部連不上、回覆沒說「不代表沒有」⇒ 原文不動、句尾補一句")
        Q2 = ("EID-07905 最近 30 天的錯誤碼目前查不到；MES 連線逾時，所以不能回報錯誤碼清單。"
              "這不代表沒有錯誤。")
        o2, h2 = g(Q2, "zh-TW", TC_ERR)
        ck(o2 is Q2 and h2 == [], "U12：★ Q2 已經白話且有「不代表」⇒ **同一個物件**（零改動、不重複補句）")
        nf_txt = "WO999999/1 查詢回 unavailable_reason=no_production_request_matched。"
        onf, _ = g(nf_txt, "zh-TW", TC_NF)
        ck(onf == N_NF["zh"] and N_UR["zh"] not in onf,
           f"U12 ①：★ 查無（no_production_request_matched）⇒「查無」白話句、不串「連不上」（{onf!r}）")
        onf_en, _ = g("Lookup returned found=false.", "en", TC_NF)
        ck(onf_en == N_NF["en"], f"U12：英文查無 ⇒ 英文標準句（{onf_en!r}）")
        leaks = [
            ('The lookup failed: unavailable_reason="error:OperationalError" (pyodbc.OperationalError 08001).', "en"),
            ("`found`: `false`，`unavailable_reason`: `timeout`。", "zh-TW"),
            ("Traceback (most recent call last): OperationalError", "en"),
            ("查詢失敗（error:ProgrammingError），error_detail 顯示 SQLSTATE 42S02。", "zh-TW"),
            ("工具回傳found=false、mes_segment_module_missing。", "zh-TW"),     # 中文緊貼：不能靠 \b
        ]
        for _t, _loc in leaks:
            _o, _h = g(_t, _loc, None)
            ck(_h and all(b not in _o for b in BLACK) and _GUARD_NOTICE.search(_o),
               f"U12 ③：★ 黑名單字樣一個都不出現在對外文字＋有「不代表」句（{_t!r} → {_o!r}）")
        o_en, _ = g('The lookup failed: unavailable_reason="error:OperationalError".', "en", TC_ERR)
        ck(o_en == N_UR["en"], f"U12：英文連不上 ⇒ 英文標準句（{o_en!r}）")
        # ④ 放行例：found=true ＋ no_pass_criteria_limits 是**答案**，不是失敗
        ok_plain = "MES 沒有登記 segLamination 這站的限值；這不代表沒有規格。"
        oo, ho = g(ok_plain, "zh-TW", TC_OK)
        ck(oo is ok_plain and ho == [], "U12 ④：★ 放行例的正確白話回覆 ⇒ 同一個物件（不被改、不補失敗句）")
        ok_bare = "segLamination 的限值：MES 沒有登記。"
        ob, hb = g(ok_bare, "zh-TW", TC_OK)
        ck(ob is ok_bare and hb == [],
           "U12 ④：★ 放行例回覆沒說「不代表」也**不補句**（本輪不是取不到；turn_outcome=ok）")
        ok_leak = 'segLamination 回 found=true、unavailable_reason="no_pass_criteria_limits"。'
        ol, hl = g(ok_leak, "zh-TW", TC_OK)
        ck(FOUND_OK_REASONS["no_pass_criteria_limits"]["zh"] in ol and "segLamination" in ol
           and all(b not in ol for b in BLACK) and N_UR["zh"] not in ol and N_NF["zh"] not in ol
           and not any(x.startswith("+notice") for x in hl),
           f"U12 ④：★ 放行例漏字 ⇒ 換成**它自己的**白話（MES 沒有登記限值）、不套連不上／查無句（{ol!r}）")
        ck(reason_class("no_pass_criteria_limits") == "ok"
           and turn_outcome(TC_OK) == "ok",
           "U12 ④：reason_class／turn_outcome 都把放行例判成 ok（不是失敗）")
        # 不誤傷：成功回覆、數字、混合輪
        succ = "EID-13219 had 6.4 h of work-order downtime over the last 7 days."
        os_, hs = g(succ, "en", [{"tool": "equipment_downtime", "result": {"eid": "EID-13219"}}])
        ck(os_ is succ and hs == [], "U12：成功回覆（有數字、無內部字樣）⇒ 同一個物件")
        mixed = "第一次 found=false，改用 CAL 後共 12 筆。"
        om, hm = g(mixed, "zh-TW", [TC_ERR[0], {"tool": "segment_data", "result": {"found": True}}])
        ck("12 筆" in om and "found" not in om and not any(x.startswith("+notice") for x in hm),
           f"U12：★ 有獨立數字的句子**只換字不整句刪**；本輪有成功的資料工具 ⇒ 不補失敗句（{om!r}）")
        demo_t = "J0SYN06 是 not_in_demo_fixture，所以沒有值。"
        od, _ = g(demo_t, "zh-TW", [{"tool": "segment_data",
                                    "result": {"found": False, "unavailable_reason": "not_in_demo_fixture"}}])
        ck("離線預覽資料" in od and "not_in_demo_fixture" not in od,
           f"U12：demo 查無碼有專屬白話 ⇒ 換字保留「離線預覽」（persona 規則 7 不能丟）（{od!r}）")
        # turn_outcome／reason_class 判定表
        ck(turn_outcome(None) is None and turn_outcome([]) is None
           and turn_outcome([{"tool": "machine_lookup", "result": {"eid": "EID-07905"}}]) is None,
           "U12：沒有資料工具呼叫 ⇒ None（不知道、不補句）")
        ck(turn_outcome([{"tool": "machine_lookup", "result": {"eid": "EID-07905"}}] + TC_ERR)
           == "unreachable",
           "U12：★ 解析工具（machine_lookup）成功不算「拿到資料」⇒ 先解析再取數失敗仍判 unreachable")
        ck(turn_outcome(TC_ERR + TC_NF) == "unreachable" and turn_outcome(TC_NF) == "not_found",
           "U12：全部取不到時任一支連不上 ⇒ unreachable；全查無 ⇒ not_found")
        ck(turn_outcome([{"tool": "wo_route", "result": {"error": "bad_wo_id", "hint": "…"}}]) == "ok",
           "U12：400 形（輸入要改）不是「取不到」⇒ 不觸發補句")
        ck(turn_outcome([{"tool": "wo_route", "result": {"error": "unavailable", "given": "x"}}])
           == "unreachable", "U12：模組缺（error=unavailable）⇒ unreachable")
        ck([reason_class(x) for x in ("error:OperationalError", "timeout", "db_unavailable",
                                      "mes_route_module_missing", "serial_not_found_at_station",
                                      "not_in_demo_fixture", None, "whatever_new_code")]
           == ["unreachable"] * 4 + ["not_found"] * 4,
           "U12：reason_class 判定表（未知碼保守歸查無）")
        # 永不 raise／非字串原樣回
        ck(g(None) == (None, []) and g("") == ("", []) and g("ok", "en", "garbage")[0] == "ok",
           "U12：None／空字串／壞 tool_calls ⇒ 原樣回、不 raise")
        # #175 knowledge_card：中性工具＋中性碼（查無卡≠「不代表沒有發生」）
        _kc_calls = [{"tool": "knowledge_card",
                      "result": {"found": False, "unavailable_reason": "no_matching_card"}}]
        ck(turn_outcome(_kc_calls) is None, "U12：knowledge_card 屬中性工具（查無不算資料失敗）")
        _kt, _kh = g("查詢結果 unavailable_reason=no_matching_card，BFRAME 沒有卡。", "zh-TW", _kc_calls)
        ck("no_matching_card" not in _kt and "知識卡裡沒有寫到這個主題" in _kt
           and FAIL_NOTICE["not_found"]["zh"] not in _kt,
           "U12：no_matching_card 換自己的白話、不補失敗句：%r" % _kt)

        print(f"[board_assistant] selftest PASS — {n} 項斷言全過："
              f"惰性建/title 截斷/aid 隔離/上限 {MAX_OPEN}/close 冪等/closed 拒寫/"
              f"recent_history 輪序 8 輪/cap_history 從最舊丟/message 時序｜"
              f"U5：舊 schema migration 冪等/begin→complete 生命週期/fail→retry→complete 全鏈/"
              f"interrupted 著地/狀態機全 raise/**CAS 併發只有一條會贏**/五 API 擁有權/MAX_OPEN 於 begin/"
              f"歷史排除 pending·failed 輪 + before_id/page_ctx cap {_PAGE_CTX_MAX}｜"
              f"U6：Jev shadow 六欄 additive migration/record_jev 寫入·覆寫·"
              f"**不碰 status**·非 assistant 列不寫/#172 router 兩欄 jev_applied·jev_threshold（五態閉集·五態計數）｜"
              f"U7：👍/👎 兩欄 additive migration/rate_turn 新評·改評·"
              f"**再按同一顆＝取消（兩欄一起清）**·只有 done 可評·擁有權·"
              f"非 assistant 列拒·非法值 raise·**不碰 status**｜"
              f"U8：idle 切段 {HISTORY_IDLE_SPLIT_S}s（>「不是」>=·壞值/NULL 不切·kwarg 覆寫·"
              f"**純讀不動 conversation**）/assistant 每則截 {_HISTORY_ASSISTANT_MAX_CHARS}＋省略號·"
              f"user 不截·**先截再丟最舊**/落帳兩欄 additive migration·record_prompt_stats "
              f"三態·pending 列可寫·**不碰 status**·非法值 raise｜"
              f"U9：每日 LLM 保險絲（缺表自建/cap=0 不限/cap=1 第二次擋/跨日重置＋舊日保留/"
              f"**兩 kind ＝兩個獨立的桶**（翻譯狂燒不吃助理額度·助理桶滿仍放行翻譯）/"
              f"status `used`＝助理桶＋`caps`／`translate_used` 加性/by_kind/"
              f"upsert 跨連線持久/kind 打錯字 raise/"
              f"env 垃圾值**退回預設（助理 {LLM_DAILY_MAX_DEFAULT}／翻譯 "
              f"{TRANSLATE_DAILY_MAX_DEFAULT}）不是不限**·兩 env 互不外溢/日鍵午夜換日/"
              f"**一列 message 都不碰**）｜"
              f"U10：`tool_call` 表（缺表自建＋索引/七欄定死/writer 回遞增 id/"
              f"reader 依 id＝呼叫順序/信封與 result 往返無失真/**兩輪隔離**/查無回空 list/"
              f"turn_id·tool·provenance·params_hash 非法值 raise 且零寫入/"
              f"result 超 {_TOOL_CALL_RESULT_MAX} 位元組**存截斷標記**（信封照留）/"
              f"**一列 message 與 llm_budget 都不碰**）｜"
              f"U11：`tool_call_count` 0/1/N·逐輪隔離·加總＝全表·非整數 raise/"
              f"`result_json` 原字串（解析＝result·逐位元＝writer 輸出·截斷標記照留）/"
              f"`get_messages` 一次查詢供數且落在對的那一則｜"
              f"U14 #137「依據」chip：鍵定死·五欄原樣·窗短形/全形·缺欄「—」·page 安全形白名單"
              f"（拒 //、scheme、樣板、反斜線、空白、引號）·壞信封不 raise·單則 reader 依呼叫順序·"
              f"get_messages 同一次查詢供 tool_basis｜"
              f"U12 #177 失敗答法守門：Q1 連不上整句拿掉＋補句/Q3 補句/Q2 已白話零改動/查無句/"
              f"黑名單字樣零外露（含中文緊貼）/**放行例 no_pass_criteria_limits 換自己的白話、不補失敗句**/"
              f"成功與有數字句不誤傷/解析工具不算拿到資料/400 形不補句/永不 raise｜"
              f"PA2 共用 gateway：client_key 定長 HMAC·同入同出·空值 unknown·換 secret 換值·原始 IP 不落庫/"
              f"client_key 欄 additive 冪等·retry 沿用/quota·budget 可著地/app `_gw_error_kind` 429·503-budget·"
              f"503 其他＋合約 detail 字串＋非合成照舊/app `_synth_mcp_target_ok` expected 相等放行·不等拒·埠路徑仍查·未設回 loopback 規則/app `_client_ip` 非 loopback 忽略 XFF·loopback 取最後一段·單段·去空白·空值·壞值。"
              f"DB＝tempfile、未動預設檔。")
    finally:
        # 清乾淨 tempfile（含 WAL/SHM 旁檔）
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Hermes sidebar 對話持久化 store（stdlib sqlite3；--selftest 離線自驗）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（tempfile、不動預設 DB）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
    else:
        ap.error("持久化 store 模組；請用 --selftest 自驗，或於程式中 import 使用其 API。")
