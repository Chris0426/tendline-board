# -*- coding: utf-8 -*-
"""board_basicauth.py — **隧道閘**（tunnel gate）：只對「經 cloudflare 隧道進來的流量」設限。

## 這是什麼（與 anon 版的差別）
本檔的祖先是 anon worktree 的 `board_basicauth.py`（那版＝獨立 `:8020` demo 實例的
無差別 Basic Auth 包裝：**任何**請求都要帳密）。主線的處境完全不同——**同一個 `:8000`
同時服務兩種人**：

| 誰 | 從哪來 | 要什麼待遇 |
|---|---|---|
| Chris／同事 | 本機 127.0.0.1 或 LAN | **完全不變**：不要密碼、Hermes/翻譯照用 |
| SALTMARSH 設備部門 | 公司外 → cloudflare quick tunnel | 要共用密碼；**agent 端點（`/mcp`／`/api/board`）與 O3 暖機看不到** |
| cmms（Fly.io 雲） | 公網 → 固定入口 Worker → 隧道 | **只有 `POST /mcp` 一條**、要 bearer（2026-09-23 窄道，見下方同名節） |

⚠ **2026-09-22 起 Hermes 助理與翻譯對隧道流量放行**（C-B4 局部重開；理由與新的花費防線＝
`BLOCKED_PREFIXES` 上方那段）。上表第二列原本寫「Hermes/翻譯看不到」，**那句自此失效**。

⇒ 本模組不是「把整台服務鎖起來」，而是**依請求來源分流**。這是與 anon 版的**語意差異**，
不是移植疏漏（SPEC-SALTMARSH-ACCESS.md §1 C-B3）。

## 判定「隧道流量」＝ cloudflared 注入的標頭
cloudflared 在把請求交給本機 origin 之前，一定會帶上 `Cf-Ray`（CF 邊緣的請求識別碼）
與 `CF-Connecting-IP`（真實訪客 IP）。本機/LAN 直連的瀏覽器**不會**有這兩個標頭。
⇒ `is_tunnel(headers)` ＝ 這兩個任一存在。

### ★ 偽造方向安全性（fail-safe；本模組最重要的一條）
標頭是**使用者可控**的，所以要問：偽造能得到什麼？
- **LAN 使用者自己加 `Cf-Ray`** ⇒ 他把自己**從寬鬆側搬到嚴格側**：本來直接進，現在被要密碼、
  `/mcp`／`/api/board`／O3 暖機對他 404。**只會多擋，永不多放。**
- **隧道訪客想「拿掉」標頭以逃避檢查** ⇒ 做不到：標頭是 cloudflared 在 origin 側**注入**的，
  不是訪客送的；訪客送 `Cf-Ray: x` 只會被 CF 覆寫／或再疊一份，兩種結果都仍判定為隧道。

⇒ **偽造只有一個方向可走，而那個方向是安全的**。這條寫進 selftest（`fail-safe direction`），
日後任何人想把判定改成「白名單 IP」或「反過來預設嚴格」之前，先讀這段。

## ★ 隧道帳號分層（2026-08-27 Chris 拍板；本模組第二個承重概念）
**為什麼要分層**：公司內網有 client isolation ⇒ **同事的筆電連不到本機 `:8000`**
⇒ Aldergrove Precision 同仁想看 Tendline Board，**只能走同一條 cloudflare 隧道**。於是「隧道＝外部 SALTMARSH」
這個原本成立的等式**破了**：同一條隧道現在同時載兩種人，而他們該看到的東西不一樣。

| tier | 憑證 env | 看不到什麼 |
|---|---|---|
| `saltmarsh` | `BOARD_TUNNEL_USER` / `_PASS`（原本那組） | agent 端點／O3 暖機 ＋ **`/budget`／`/cost`（採購金額）** |
| `aldergrove` | `BOARD_TUNNEL_ALDERGROVE_USER` / `BOARD_TUNNEL_ALDERGROVE_PASS`（**新**） | agent 端點／O3 暖機（端點曝光考量，與機密分級無關） |

⇒ **封鎖清單變成「每層一份」**：`blocked_for(tier)`。`BLOCKED_PREFIXES`（今日＝`/mcp`、
`/landing/opr/warm`、`/api/board`）**兩層都擋**（擋的是端點曝光與對外觸發取數，不是資料機密）；
`/budget`、`/cost` **只擋 saltmarsh**。

**aldergrove 那組未設 ⇒ 該層根本不存在**（`build_expects` 不會產出它）⇒ 全隧道行為與分層前
**逐位元相同**。密碼由 Chris 自己填進 `internal-file`，**本檔沒有、也永遠不會有任何預設值**。

## 三道閘的順序（先認層、再封鎖）
隧道流量進來：
1. **認層**（`auth_tier`）：把 `Authorization` 對每一組憑證比一次，回 `saltmarsh` / `aldergrove` / `None`。
   ⚠ **比完才回、不早退**（別讓「哪一組對了」變成可量的時間差）；**兩組撞值 ⇒ 取最嚴的 `saltmarsh`**。
2. **該層的封鎖清單** → **一律 404，不論憑證對不對**。
   為什麼是 404 不是 403：403 等於承認「這裡有東西、你沒權限」；404＝對那一層而言根本不存在
   這些路徑。共用密碼會被轉貼，密碼不該是 LLM 端點的唯一防線。
   ⚠ **未認證（tier=None）套的是「兩層共同」那份**（`BLOCKED_PREFIXES`）——所以
   「無憑證打 `/mcp` → 404」這條行為原封不動；而無憑證打 `/budget` → **401**
   （不是 404），否則 aldergrove 的人永遠等不到帳密框。401 不洩漏任何資訊：這個閘在路由**之前**，
   `/budget` 與 `/根本不存在的路徑` 對未認證者的回應完全一樣。
3. 其餘路徑 → 認過層就 PASS，沒認過就 Basic Auth（`401 + WWW-Authenticate`，瀏覽器跳帳密框）。
比對走 `hmac.compare_digest`（常數時間、防 timing）。

**路徑比對＝精確 segment**：`/mcp` 與 `/mcp/**` 命中；**`/mcpfoo` 不命中**
（naive `startswith` 會誤傷未來新增的同前綴路徑，反過來也可能被繞——一律用 segment 邊界）。

## ★★ OPEN ACCESS 暫時開放（`BOARD_OPEN_ACCESS=1`；Chris 2026-09-02 指示）
**指示原話**：「為了讓不同部門的長官可以來 Tendline Board 看，我要暫時開放整個 Tendline Board（包含 /budget /cost
頁面）給任何連進服務的人，不需要帳號密碼」。

⇒ 旗標為 `"1"` 時，**隧道流量不再被要求帳密、也不再分層**：`/budget`、`/cost` 與其餘所有頁面
一律 PASS。**憑證 env 一個都不刪**（`build_expects` 照常產出、關掉旗標即逐位元回到原狀）。

| | 旗標關（預設） | 旗標開 |
|---|---|---|
| 非隧道（本機/LAN） | PASS | PASS（**完全沒差**——它本來就沒被檢查過） |
| 隧道 ＋ 無憑證 | 401 CHALLENGE | **200 PASS** |
| 隧道 ＋ `/budget`／`/cost` | saltmarsh 404／aldergrove 200 | **一律 200** |
| 隧道 ＋ `/mcp`／`/api/board`／O3 暖機 | 404 | **仍 404**（見下） |
| 隧道 ＋ **`POST /mcp`**（2026-09-23 窄道） | 要 bearer（503／401／429） | **一樣要 bearer**（窄道不看本旗標） |
| websocket ＋ 隧道 | close 1008 | 仍 close 1008 |

### ⚠ `BLOCKED_PREFIXES` **不在開放範圍內**（Chris 2026-08-05 C-B4；2026-09-22 局部重開後仍成立）
開放的是「**看**」，不是把 agent 端點暴露出去。今日清單＝`/mcp`、`/landing/opr/warm`、`/api/board`
——前後兩條是 agent 工具面（`board_mcp` 同一批函式）、中間那條會**對外觸發 MES 取數**。
⇒ 本旗標**只鬆開 `TIER_PREFIXES` 與 Basic Auth 挑戰，不碰 `BLOCKED_PREFIXES`**。
⚠ **2026-09-22 起 `/assistant` 與兩條翻譯路徑已不在這份清單裡**（C-B4 局部重開）
⇒ 開放模式下隧道訪客**可以**用助理與翻譯；花費那一面由 `BOARD_LLM_DAILY_MAX` 保險絲守，
**不再由這份清單代打**。

### ⚠ 開放模式的鬆綁**只寫在 `decide()` 一個地方**
`blocked_for()` 刻意**不認識** `TIER_OPEN`（傳進去照樣回最嚴的那份）——「不是 aldergrove 就從嚴」
這條 fail-safe 預設不因本功能而破。要找「為什麼這個請求沒被擋」，只有 `decide()` 開頭那一個分支。

### 回復（一行）
`internal-file` 把 `BOARD_OPEN_ACCESS=1` 改成 `0`（或整行刪掉）→ 跑 `.\restart-board.ps1`。

## nav 也要分層（`tier_of_request`）
閘住的是**路徑**，但 nav 上那顆「Budget」標籤住在**別的頁面裡**——閘看不到它。
⇒ app.py 的模板 context processor 用 `tier_of_request(hmap, environ)` 自己再認一次層，
`saltmarsh` 與**認不出層的隧道流量**都不渲染那顆標籤（fail-safe 方向：認不出 ⇒ 不給）。
⚠ 這一支**刻意不看 `BOARD_TUNNEL_AUTH`**：閘沒開的時候 `/budget` 本來就人人可達，
nav 藏不藏沒有安全意義；讓它只吃憑證 env ⇒ 冒煙測試可以在不掛閘的 app 上驗 nav 分層。
⚠ 但它**必須看 `BOARD_OPEN_ACCESS`**：開放模式下路徑真的通了，標籤再藏就變成
「頁面看得到、入口找不到」的假貼心 ⇒ 開放時回 `TIER_OPEN`，`_nav.html` 據此照常渲染。

## env 閘（預設 OFF＝逐位元不變）
`BOARD_TUNNEL_AUTH=1` 才啟用；未設 ⇒ app.py 檔尾根本不 wrap、本模組不參與任何請求。
（例外：`BOARD_OPEN_ACCESS=1` 時 `should_wrap` 也回 True——閘仍要掛著，
**因為 `BLOCKED_PREFIXES` 的 404 沒有第二個人在守**。）
憑證 `BOARD_TUNNEL_USER` / `BOARD_TUNNEL_PASS`（＋選配的 `BOARD_TUNNEL_ALDERGROVE_*`）
由 gitignored `internal-file` 注入，**絕不入版控、絕不進日誌**。

## ★★ `POST /mcp` 隧道窄道（cmms 專用；Chris 2026-09-23 裁、#168）
**指示原話**：「1，附三個條件：bearer fail-closed、速率限制、/mcp 只放行工具呼叫不放行別的路徑
— 同意 開工」。
**為什麼**：CONCEPT §19.3 第四十三輪承諾 cmms「走 LAN 打 `/mcp`」——但 cmms 跑在 **Fly.io**、
只能走公網 ⇒ 14 支工具全叫不通。唯一的公網路徑＝固定入口 Worker → quick tunnel → `:8000`，
而這條路對 `/mcp` 一律 404（`BLOCKED_PREFIXES`）。
**形（全部住在 `_TunnelGateASGI.__call__` 的 `_mcp_lane` 分支；`decide()` 一個字沒動）**：
1. **只認一個 (method, path)**：`is_mcp_lane()`＝隧道 ∧ **`POST`** ∧ path **恰為 `/mcp`**。
   `/mcp/`、`/mcp/x`、GET／DELETE／HEAD 都**不進窄道**、落回 `decide()` ⇒ 照舊 404。
   ⚠ `/api/board`、`/landing/opr/warm` **照舊 404**；`BLOCKED_PREFIXES` 常數一個位元沒改
   （`/mcp` 仍在裡面——窄道是在它**前面**挖一條單一路徑的洞，不是把它移出清單）。
2. **bearer 在閘上先驗、fail-closed**（`mcp_bearer_verdict()`；token 與 `board_mcp` 同一個 env
   `BOARD_MCP_TOKEN`、**每個請求現讀**）：未設 ⇒ **503** JSON `mcp token not configured`
   （Worker 自己的 503 是 HTML「Starting up」頁 ⇒ cmms 分得出是哪一層）；缺／錯 ⇒ **401**
   ＋`WWW-Authenticate: Bearer`（**不是 404**：cmms 要能分辨「網路不通」與「token 錯」）。
   比對＝`hmac.compare_digest`（常數時間）。Basic 帳密**不能**代替 bearer（一個 Authorization
   標頭放不下兩種 scheme ⇒ 窄道只認 bearer；`board_mcp.McpTransportProxy` 仍會再驗一次＝雙閘）。
3. **OPEN ACCESS 不鬆窄道**：開放的是「看」，不是工具面 ⇒ `BOARD_OPEN_ACCESS=1` 下無 bearer 仍 401。
   窄道分支寫在 `decide()` **之前**、完全不看 `self._open` ⇒ 開放模式「唯一鬆綁點在 decide()」
   那條不變量不受影響（窄道只會**收緊**：原本 404 的地方改成要 bearer 才過）。
4. **速率限制**（`SlidingWindowLimiter`）：鍵＝`cf-connecting-ip`（沒有就 token 指紋
   sha256 前 12 碼），滑動窗 **60 次／60 秒**（`BOARD_MCP_TUNNEL_RPM` 可覆寫、壞值回預設），
   超過 ⇒ **429**＋`Retry-After`（秒、≥1）。純記憶體、單進程、不落盤（重啟即清零＝可接受）。
   ⚠ **順序＝503 → 401 → 429**（限速放在 bearer **之後**）：Worker 的子請求對 origin 可能共用
   同一顆 egress IP ⇒ 若先限速，**任何拿到固定網址的人灌錯 token 就能把 cmms 的額度吃光**
   （DoS 正牌使用者）。token 是長隨機字串、401 不碰任何工具與 MES ⇒ 未認證的量不需要這把尺；
   這把尺防的是「**認證過的 agent 迴圈把 MES 打爆**」。
5. **每筆一行 log**（stdout、`[tunnel-gate] mcp ip=… status=… method=… tool=…`）：token **永不印**
   （連指紋都不印）；jsonrpc `method`／`tools/call` 的工具名要看 body ⇒ 只在 Content-Type 含
   `json` 且 body ≤ 64KB 時 peek、解析失敗記 `method=?`、**peek 過的訊息原樣重放給下游**、
   log 失敗不影響放行（觀測動作不得害死一次呼叫）。欄位值過濾成 `[A-Za-z0-9_./:,?-]`（防 log 注入）。
6. **本機／LAN 一個位元不變**：無隧道標頭 ⇒ `is_mcp_lane` 為 False ⇒ 不經此分支、不 peek、不 log。

selftest：`python board_basicauth.py --selftest`（純離線、零網路）。
"""
import base64
import collections
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------
#: 隧道訊號（任一存在＝隧道流量）。全小寫，比對前先 lower()。
#: - `cf-ray` / `cf-connecting-ip`：**cloudflared 對 origin 注入**（直開 trycloudflare 網址也有）
#: - `x-board-tunnel`：**固定入口 Worker 主動加**（`internal-file/worker.js`）＝第二道保險。
#:   為什麼要第二道：若哪天 CF 改了注入行為、前兩個標頭消失，閘會**靜默失效＝live 對外全開**。
#:   多一個我方自己控制的訊號 ⇒ 失效需要兩件事同時發生。偽造它一樣只會把自己搬到嚴格側。
TUNNEL_HEADERS = ("cf-ray", "cf-connecting-ip", "x-board-tunnel")

#: **所有**隧道層共同 404 的路徑前綴。
#:
#: ★★ **2026-09-22 C-B4 局部重開**（Chris 原話：「可接受。開包，走 (b)」；存證＝
#:   `SPEC-SALTMARSH-ACCESS.md` C-B4 修訂框「2026-09-22 局部重開」＋ §3.4a）。
#:   **前提變了**：Board 助理現在**就是要給 SALTMARSH（從隧道連進來的）人員用**——FAB 已開
#:   （`_hermes.env` `BOARD_ASSISTANT_UI=1`、2026-09-22），而公司內網有 client isolation
#:   ⇒ 隧道是他們唯一的路。C-B4 當初關掉的兩條理由裡，**第②條（無界燒 LLM）現在有了
#:   專門的答案＝每日總輪數保險絲 `BOARD_LLM_DAILY_MAX`**（`board_assistant.llm_budget_*`、
#:   全站全流量、含內網），所以「用路徑 404 當花費上限」這個代打不再需要。
#:   ⇒ **移出三條**：`/assistant`（含子路徑）、`/equipment/translate`、`/equipment/cell/translate`。
#:   ⇒ **`/mcp` 續擋**：那是 gateway 在**本機**呼叫的工具面，瀏覽器用不到 ⇒ 對隧道曝出去
#:     只有壞處沒有好處（且它是 agent 端點，不是給人看的頁）。
#:   ⚠ **放行的是「路徑通了」，不是「無界燒錢」**——花費那一面自此由保險絲守，
#:     兩者是**不同的機制擋不同的事**，別把保險絲拆了又不把這三條放回來。
#:
#: 今日清單（三條，理由各不相同、刻意不合併成一份）：
#: - `/mcp`                  Hermes 唯讀工具面（bearer 閘 fail-closed，但不該對外可見）
#: - `/landing/opr/warm`     landing 底下唯一一支會主動打 MES 的端點（見下）
#: - `/api/board`               WebMCP 工具面（`/mcp` 同一批函式的 HTTP 面；「Board 內網先開」）
#: ⚠ 這幾條擋的是 **端點曝光與對外觸發取數**，不是資料機密 ⇒ **aldergrove 層一樣擋**。
#: ⚠ 加/減這份清單＝改對外曝光面，必須同步 SPEC-SALTMARSH-ACCESS.md §1 C-B3／C-B4 與 §3 升級清單
#:   （§5 紅線 7），並同步本檔 selftest 與 `run_smoke` 的 `[tunnel-gate]`／`[budget]` 兩組。
#:
#: ★ 以下兩段是**歷史存證**（三條已於 2026-09-22 移出，但理由要留著——日後誰想
#:   「順手把 `/mcp` 也開了」，得先讀懂當初為什麼擋、以及今天為什麼只開那三條）：
#: - `/assistant`            Hermes 側欄（含 /panel、/{cid}、/turn/{id}…）——**2026-09-22 放行**
#: - `/equipment/cell/translate`  drill 整格一鍵翻譯——**2026-09-22 放行**
#: - `/equipment/translate`       整頁一鍵翻譯整段（2026-09-02 補登記）——**2026-09-22 放行**
#:
#: ★ **2026-09-06 加 `/landing/opr/warm`**（§18.8 的 O3 暖機觸發點）。
#:   它與上面四條的理由不同、但同族：**這是 landing 底下唯一一支會主動打 MES 的端點**
#:   （其餘 landing 端點一律只 `_peek_*`，C-LD18）。`BOARD_OPEN_ACCESS=1` 之下隧道
#:   不再要帳密 ⇒ 任何人都可以靠重整頁把查詢打上 MES。擋的是【對外觸發取數】、
#:   不是資料機密 ⇒ **aldergrove 層一樣擋**（同 C-B4 的形）。
#:   ⚠ **本機／LAN 完全不受影響**：這道閘只看隧道標頭，`warm-board.ps1` 走
#:     `127.0.0.1` ⇒ 照常打得到（這是「暖機只能由我方排程器發起」的閘，
#:     不是把功能關掉）。app 側另有 120s 節流（`LD_OPR_WARM_MIN_S`）當第二道。
#:   ⚠ 登記的是這**一條完整路徑**、不是 `/landing` 整段：`/landing/body`／
#:     `/landing/state` 是長官要看的頁，擋了就是把頁面擋掉。
#:
#: ★ **2026-09-02 補登記的漏網端點**（審查抓到；**不是新裁決，是 C-B4 的同類端點漏登記**）：
#:   `/equipment/translate/all`（`app.py` `@app.post`，2026-08-17 加）走 `_hermes_translate`
#:   → WSL gateway → 本機 codex，與 `/equipment/cell/translate` **完全同一條 LLM 管線**，
#:   卻整整兩週半不在本清單裡。開放模式上線後這個洞會變成「匿名帶隧道標頭即可無界燒 LLM」。
#: ⚠ **登記的是 `/equipment/translate` 整段、不是 `/equipment/translate/all` 那一條**：
#:   本次事故的成因就是「有人加了新 route 忘了回來登記」——擋整段 ⇒ 日後任何
#:   `/equipment/translate/*` 新端點**自動**在保護內，同一類錯誤不會再犯第二次。
#:   （這與 `/mcp` 蓋 `/mcp/`、`/cost` 蓋 `/cost/drill` 是同一個「一條路徑蓋整棵子樹」的寫法。）
#:   今日該段底下只有 `/equipment/translate/all` 一條 route ⇒ 擋整段零副作用。
#: ★ **2026-09-07 加 `/api/board`**（WebMCP 內網唯讀試點，`SPEC-WEBMCP.md` §4）。
#:   它是 `board_mcp` 那 6 個唯讀工具的 HTTP 面（頁面上的 `document.modelContext` 工具
#:   靠它取數）⇒ **與 `/mcp` 同一批函式、同一個理由**：agent 端點不對隧道曝。
#:   ⚠ 這是**兩層擋的第二層**：第一層是 `_nav.html` 對隧道流量根本不注入 `_webmcp.html`
#:     （頁面上沒有工具可註冊）。版面那層只是省事，**真正的防線是這裡的 404**
#:     ——同 `/budget` 標籤與 `/budget` 404 的分工。
#:   ⚠ 擋的是 `/api/board` **整段**（`/api/board/list_stations`…自動在保護內），
#:     `/api/data`／`/api/qlik` 等既有 `/api/*` 端點**一個位元不受影響**
#:     （`_segments_match` 精確 segment 比對：`/api` 不是前綴、`/api/wrx` 不命中）。
BLOCKED_PREFIXES = ("/mcp", "/landing/opr/warm", "/api/board")

#: **只擋 `saltmarsh` 層**的路徑前綴（2026-08-27）：採購金額頁。
#: - `/budget`  設備維護預算與訂單追蹤（`SPEC-BUDGET.md` §3）
#: - `/cost`    維護成本頁 v1＝採購側花費（**2026-08-28 加**；`SPEC-RUN-COST.md` §9）
#:   ⚠ 兩者讀的是**同一顆** opswiki-mcp canonical SQLite、同樣是金額 ⇒ 機密邊界必須一致。
#:   ⚠ **一條路徑蓋整棵子樹**（`_segments_match`）⇒ `/cost/body`、`/cost/drill` 自動同擋：
#:     fragment 不是後門。
#: ⚠ 這是**資料機密**分級，與上面那三條的理由不同——所以是兩份清單，不是一份加註解。
#: ⚠ 加/減這份清單＝改機密邊界，必須同步 `SPEC-BUDGET.md` §3 與 `SPEC-RUN-COST.md` §9。
TIER_PREFIXES = ("/budget", "/cost")

#: 隧道帳號層（順序＝比對順序，也是**撞值時的優先序：最嚴的排前面**）
TIER_SALTMARSH = "saltmarsh"           # 外部組裝代工廠：看產線，不看錢
TIER_ALDERGROVE = "aldergrove"     # Aldergrove Precision 同仁走隧道進來（公司內網 client isolation ⇒ 連不到 :8000）
TIERS = (TIER_SALTMARSH, TIER_ALDERGROVE)

#: ★ OPEN ACCESS 的「層」（2026-09-02）：**不是一組憑證**，是「這個請求正被開放模式服務」的標記。
#: ⚠ 刻意**不**放進 `TIERS`：`TIERS` 是「有憑證可認的層」，開放模式沒有憑證可認。
#: ⚠ 刻意**不**進 `blocked_for()` 的寬鬆側（`blocked_for(TIER_OPEN)` 仍回最嚴那份，selftest 有釘）——
#:   開放模式的鬆綁只住在 `decide()` 開頭那一個分支，全檔唯一一處。
TIER_OPEN = "open"

#: 決策 enum（`decide()` 的回傳；ASGI 層與 selftest 共用同一組字面值）
PASS = "pass"           # 放行給 inner app
BLOCK = "block"         # 404（封鎖清單）
CHALLENGE = "challenge"  # 401 + WWW-Authenticate

DEFAULT_REALM = "Tendline Board"

ENV_ENABLE = "BOARD_TUNNEL_AUTH"
ENV_USER = "BOARD_TUNNEL_USER"
ENV_PASS = "BOARD_TUNNEL_PASS"
#: ⚠ **本檔沒有、也不得有這兩個的預設值**：憑證由 Chris 填進 gitignored `internal-file`。
#: 未設 ⇒ `build_expects` 不產出 aldergrove 層 ⇒ 全隧道行為與分層前逐位元相同。
ENV_ALDERGROVE_USER = "BOARD_TUNNEL_ALDERGROVE_USER"
ENV_ALDERGROVE_PASS = "BOARD_TUNNEL_ALDERGROVE_PASS"
#: ★ 暫時開放（Chris 2026-09-02）。**恰為 "1"** 才開（與 `ENV_ENABLE` 同一種寫法紀律）。
#: 未設／任何其他值 ⇒ 逐位元回到分層閘的原行為。
ENV_OPEN_ACCESS = "BOARD_OPEN_ACCESS"

#: ★★ `POST /mcp` 隧道窄道（Chris 2026-09-23、#168；模組 docstring 同名節）。
#: ⚠ 窄道是**一個 (method, path) 對**，不是前綴：`/mcp/`、`/mcp/x`、GET 一律不進。
MCP_LANE_METHOD = "POST"
MCP_LANE_PATH = "/mcp"
#: bearer 與 `board_mcp.McpTransportProxy` **同一個 env**（token 不變、cmms 已有）。
ENV_MCP_TOKEN = "BOARD_MCP_TOKEN"
#: 每鍵每 60 秒上限；env 覆寫須為正整數，否則回預設（壞值不得變成「不限」）。
ENV_MCP_TUNNEL_RPM = "BOARD_MCP_TUNNEL_RPM"
MCP_TUNNEL_RPM_DEFAULT = 60
MCP_RATE_WINDOW_S = 60.0
#: 為了 log 的 method／工具名，最多 peek 這麼多位元組（超過就記 `method=?`、照常放行）。
MCP_PEEK_MAX = 64 * 1024
#: 窄道判決 enum（`mcp_bearer_verdict()` 回傳）
MCP_OK = "mcp_ok"
MCP_NO_TOKEN = "mcp_no_token"     # 503
MCP_UNAUTH = "mcp_unauth"         # 401


# ---------------------------------------------------------------------------
# 純函式核心（零 I/O、零 ASGI 依賴；selftest 直接打這幾支）
# ---------------------------------------------------------------------------
def header_map(raw_headers):
    """ASGI `scope["headers"]`（list of (bytes, bytes)）→ {小寫 str: str}。

    ASGI 規範保證 key 已是小寫，仍防禦性 `.lower()`（TestClient/中介層不見得守規）。
    重複標頭取**第一個**（與 anon 版一致；Authorization 不該重複，重複＝可疑，取第一個
    最保守——攻擊者無法用「疊第二個合法值」繞過第一個非法值）。"""
    out = {}
    for k, v in raw_headers or []:
        try:
            key = k.decode("latin-1", "replace").lower() if isinstance(k, bytes) else str(k).lower()
            val = v.decode("latin-1", "replace") if isinstance(v, bytes) else str(v)
        except Exception:
            continue
        if key not in out:
            out[key] = val
    return out


def is_tunnel(hmap):
    """帶 cloudflared 注入標頭 ⇒ 隧道流量。偽造方向安全性見模組 docstring。"""
    return any(h in hmap for h in TUNNEL_HEADERS)


def _segments_match(path, prefix):
    """精確 segment 前綴比對：`/assistant` 命中 `/assistant`、`/assistant/panel`；
    **不**命中 `/assistantfoo`、`/assistants`。"""
    if path == prefix:
        return True
    return path.startswith(prefix + "/")


def is_blocked_path(path, prefixes=BLOCKED_PREFIXES):
    """路徑落在**給定的**封鎖清單 ⇒ 404。清單哪一份由 `blocked_for(tier)` 決定。

    尾斜線正規化：`/mcp/` 視同 `/mcp`（FastAPI 那側 `/mcp` 是精確 Route，但別讓
    一個斜線變成繞過閘的縫）。"""
    p = path or "/"
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/") or "/"
    return any(_segments_match(p, pre) for pre in prefixes)


def blocked_for(tier, base=BLOCKED_PREFIXES, tiered=TIER_PREFIXES):
    """某一層要擋的完整清單（純函式）。

    - `aldergrove` ⇒ 只有 `base`（Hermes／翻譯）。
    - **其餘一切（`saltmarsh`、以及認不出層的 `None`）⇒ `base + tiered`**。
      ⚠ 預設值刻意寫成「不是 aldergrove 就從嚴」而不是「是 saltmarsh 才從嚴」：日後多一個層
      忘了改這裡，新層拿到的是**嚴的**那份，不是鬆的。"""
    return tuple(base) if tier == TIER_ALDERGROVE else tuple(base) + tuple(tiered)


def expected_header(user, password):
    """組出這組憑證對應的 `Authorization` 標頭完整字串（比對用，非輸出用）。"""
    tok = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return "Basic " + tok


def auth_ok(hmap, expect):
    """常數時間比對整條 Authorization 標頭（scheme 也一起比＝非 Basic 自動不過）。"""
    provided = hmap.get("authorization", "")
    if not provided:
        return False
    return hmac.compare_digest(provided, expect)


def build_expects(user, password, aldergrove_user="", aldergrove_pass=""):
    """憑證 → `((tier, expected_header), …)`，**順序＝比對順序＝撞值優先序**。

    **任何一層缺帳號或密碼 ⇒ 不產出該層**（＝該層不存在）。aldergrove 未設是常態（行為退回
    分層前）；saltmarsh 未設則只發生在「閘沒開、只是 nav 想認層」的路徑上——那時空憑證**絕不能**
    變成一組能被 `Basic <base64 of ':'>` 撞中的期望值，所以一樣是「不產出」。
    ⚠ 這是「新增一層」的唯一入口：沒有第二個地方能無中生有一個 tier。"""
    out = []
    for tier, u, p in ((TIER_SALTMARSH, user, password), (TIER_ALDERGROVE, aldergrove_user, aldergrove_pass)):
        if u and p:
            out.append((tier, expected_header(u, p)))
    return tuple(out)


def auth_tier(hmap, expects):
    """認層（純函式）：回 `saltmarsh` / `aldergrove` / `None`。

    ⚠ **每一組都比完、不早退**：早退會讓「第一組就對」與「第二組才對」有可量的時間差。
    ⚠ **命中取第一個**（`expects` 以最嚴的層排前面）⇒ 兩組憑證被設成同一組值時，
      使用者拿到的是**權限較小**的那層，不是較大的那層。"""
    provided = hmap.get("authorization", "")
    hit = None
    for tier, exp in expects:
        ok = bool(provided) and hmac.compare_digest(provided, exp)
        if ok and hit is None:
            hit = tier
    return hit


def decide(path, hmap, expects, base=BLOCKED_PREFIXES, tiered=TIER_PREFIXES,
           open_access=False):
    """閘的**全部決策邏輯**（純函式）：回 `(verdict, tier)`，verdict ∈ PASS/BLOCK/CHALLENGE。

    順序刻意如此：
      0. **`open_access` ⇒ 隧道流量只剩 `base` 那三條 404，其餘全 PASS**（見下）
      1. 非隧道流量 → **(PASS, None)**（本機/LAN 完全不受影響、逐位元不變）
      2. 隧道 → **先認層**（`auth_tier`）
      3. 隧道 ∧ **該層的**封鎖路徑 → **BLOCK**（不論憑證對不對）
      4. 隧道 ∧ 認出層 → PASS
      5. 其餘 → CHALLENGE

    ★ 第 0 步＝**本檔唯一一處鬆綁**（Chris 2026-09-02「暫時開放整個 Tendline Board 給任何連進服務的人」）。
      · 放在「非隧道」之前或之後都不改結果（非隧道本來就 PASS），寫在隧道判定之後是為了
        讓「本機/LAN 那條路徑逐位元沒被動過」在讀碼時一眼可見。
      · **只套 `base`、完全不看 `tiered`、也不看 `expects`** ⇒ 有沒有帶憑證、帶的是哪一層，
        在開放模式下都不影響結果（回傳的 tier 恆為 `TIER_OPEN`＝誠實標記「這是開放模式服務的」）。
      · **`base` 照擋**：開放的是「看」，不是把 LLM 端點暴露出去（C-B4 照舊）。

    ★ 未認證（tier=None）那格刻意只套 `base`，**不套 `tiered`**：
      · 套了會讓 aldergrove 的人第一次打 `/budget` 就吃 404、**永遠等不到帳密框**；
      · 不套也不洩漏任何東西——這個閘在路由**之前**，未認證者打 `/budget` 與打
        `/根本不存在` 拿到的都是同一個 401。
      而 `/assistant` 無憑證仍 404（它在 `base` 裡）＝分層前的行為原封不動。
    """
    if not is_tunnel(hmap):
        return PASS, None
    if open_access:
        if is_blocked_path(path, base):
            return BLOCK, TIER_OPEN
        return PASS, TIER_OPEN
    tier = auth_tier(hmap, expects)
    prefixes = tuple(base) if tier is None else blocked_for(tier, base, tiered)
    if is_blocked_path(path, prefixes):
        return BLOCK, tier
    return (PASS, tier) if tier is not None else (CHALLENGE, None)


def creds_from_env(environ):
    """env → `(enabled, user, password)`（saltmarsh 層）。純函式（吃 dict，不讀 os.environ）。

    `BOARD_TUNNEL_AUTH` 必須**恰為 "1"**（不接受 "true"/"yes"：一個值一種寫法，
    避免「我以為開了其實沒開」）。啟用但缺憑證 ⇒ `enabled=True` 但 user/pass 空，
    呼叫端 `wrap()` 會 raise＝**fail-closed，絕不「啟用了卻不擋」**。"""
    enabled = str(environ.get(ENV_ENABLE, "")).strip() == "1"
    return enabled, environ.get(ENV_USER, ""), environ.get(ENV_PASS, "")


def aldergrove_creds_from_env(environ):
    """env → `(user, password)`（aldergrove 層）。**缺席是合法狀態**，不是設定錯誤：
    未設 ⇒ 該層不存在 ⇒ 全隧道行為與分層前逐位元相同。**本檔沒有預設值。**"""
    return environ.get(ENV_ALDERGROVE_USER, ""), environ.get(ENV_ALDERGROVE_PASS, "")


def expects_from_env(environ):
    """env → `expects`（`build_expects` 的 env 版）。給 nav 分層與 app 檔尾共用。"""
    _en, u, p = creds_from_env(environ)
    mu, mp = aldergrove_creds_from_env(environ)
    return build_expects(u, p, mu, mp)


def open_access_from_env(environ):
    """env → 開放模式開了沒（純函式）。**恰為 "1"**（與 `creds_from_env` 同一種寫法紀律：
    一個值一種寫法，避免「我以為開了其實沒開」／更怕的「我以為關了其實還開著」）。

    ⚠ 這是 Chris 2026-09-02 明示的**暫時**措施 ⇒ 關掉它必須是一個動作、不是一次改碼。"""
    return str(environ.get(ENV_OPEN_ACCESS, "")).strip() == "1"


def tier_of_request(hmap, environ):
    """給**模板 context processor** 用：這個請求屬於哪一層。

    回 `(is_tunnel, tier)`。非隧道 ⇒ `(False, None)`（本機/LAN＝完整權限）。
    ⚠ 刻意不看 `BOARD_TUNNEL_AUTH`（理由見模組 docstring「nav 也要分層」）。
    ★ 但**看 `BOARD_OPEN_ACCESS`**：開放模式下 `/budget`／`/cost` 真的通了，
      標籤再藏就是「頁面看得到、入口找不到」⇒ 回 `TIER_OPEN`，`_nav.html` 據此渲染。
      （非隧道那格不受影響：本機/LAN 本來就回 `(False, None)`＝完整權限。）"""
    tun = is_tunnel(hmap)
    if not tun:
        return False, None
    if open_access_from_env(environ):
        return True, TIER_OPEN
    return True, auth_tier(hmap, expects_from_env(environ))


def should_wrap(environ):
    """app.py 檔尾用：要不要掛 middleware。未設 env ⇒ False ⇒ 完全不 wrap。

    ★ 開放模式也回 True：閘看起來「什麼都放行」，但 `BLOCKED_PREFIXES` 那幾條 404
      **沒有第二個人在守**——不掛閘＝Hermes／MCP／翻譯直接對外曝，違反 C-B4。"""
    return creds_from_env(environ)[0] or open_access_from_env(environ)


# ---------------------------------------------------------------------------
# ★★ `POST /mcp` 隧道窄道（2026-09-23、#168）——純函式＋一個記憶體限速器
# ---------------------------------------------------------------------------
def is_mcp_lane(method, path, hmap):
    """隧道 ∧ `POST` ∧ path **恰為** `/mcp` ⇒ 走窄道（純函式）。

    ⚠ **不做尾斜線正規化、不做前綴比對**（與 `is_blocked_path` 刻意相反）：窄道是**開洞**，
      洞要開到最小——`/mcp/` 與 `/mcp/x` 落回 `decide()` 照舊 404。
    ⚠ 非隧道 ⇒ 恆 False ⇒ 本機／LAN 永遠不經這條（承重不變量，SPEC-SALTMARSH-ACCESS §5 紅線 6）。"""
    return (is_tunnel(hmap)
            and str(method or "").upper() == MCP_LANE_METHOD
            and path == MCP_LANE_PATH)


def _as_bytes(s):
    return s.encode("utf-8", "replace") if isinstance(s, str) else bytes(s or b"")


def mcp_bearer_verdict(hmap, token):
    """窄道的認證判決（純函式）：token 未設 ⇒ `MCP_NO_TOKEN`（503、fail-closed）；
    `Authorization` 不是 `Bearer <token>` ⇒ `MCP_UNAUTH`（401）；對 ⇒ `MCP_OK`。
    比對走 `hmac.compare_digest`（bytes、常數時間；非 ASCII 也不 raise）。"""
    if not token:
        return MCP_NO_TOKEN
    auth = hmap.get("authorization", "")
    if not auth.startswith("Bearer "):
        return MCP_UNAUTH
    return MCP_OK if hmac.compare_digest(_as_bytes(auth[7:]), _as_bytes(token)) else MCP_UNAUTH


def mcp_rate_key(hmap):
    """限速鍵：`cf-connecting-ip`；沒有就 token 指紋（sha256 前 12 碼、**不是 token 本身**）；
    兩者皆無 ⇒ `anon`。"""
    ip = (hmap.get("cf-connecting-ip") or "").strip()
    if ip:
        return "ip:" + ip
    auth = hmap.get("authorization", "")
    if auth:
        return "tok:" + hashlib.sha256(_as_bytes(auth)).hexdigest()[:12]
    return "anon"


def mcp_rpm_from_env(environ):
    """env → 每 60 秒上限。須為正整數；未設／壞值／≤0 ⇒ 預設 60（壞值不得變成「不限」）。"""
    try:
        v = int(str(environ.get(ENV_MCP_TUNNEL_RPM, "")).strip())
    except (TypeError, ValueError):
        return MCP_TUNNEL_RPM_DEFAULT
    return v if v > 0 else MCP_TUNNEL_RPM_DEFAULT


class SlidingWindowLimiter:
    """純記憶體滑動窗限速（單進程、不落盤）。`hit(key)` → `(allowed, retry_after_s)`。

    - 被拒的那次**不記入**窗（否則持續灌的人永遠出不來、且 deque 無界成長）。
    - `retry_after_s`＝窗內最舊一筆滿 60 秒還要幾秒（上取整、≥1）。
    - 鍵數上限 `max_keys`：滿了就先清掉已全過期的鍵；仍滿就從最久沒用的鍵開始丟（有界記憶體）。"""

    def __init__(self, limit, window_s=MCP_RATE_WINDOW_S, clock=None, max_keys=4096):
        self.limit = int(limit)
        self.window = float(window_s)
        self._clock = clock or time.monotonic
        self._max_keys = int(max_keys)
        self._hits = collections.OrderedDict()

    def _gc(self, now):
        for k in [k for k, dq in self._hits.items() if not dq or now - dq[-1] >= self.window]:
            del self._hits[k]
        while len(self._hits) >= self._max_keys:          # 留一格給新鍵
            self._hits.popitem(last=False)

    def hit(self, key):
        now = self._clock()
        dq = self._hits.get(key)
        if dq is None:
            if len(self._hits) >= self._max_keys:
                self._gc(now)
            dq = self._hits[key] = collections.deque()
        while dq and now - dq[0] >= self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            return False, max(1, int(math.ceil(self.window - (now - dq[0]))))
        dq.append(now)
        self._hits.move_to_end(key)
        return True, 0


_LOG_SAFE = re.compile(r"[^A-Za-z0-9_./:,?-]")


def _log_tok(v, maxlen=64):
    """log 欄位值過濾（防 log 注入／換行偽造）：只留 `[A-Za-z0-9_./:,?-]`、截 64 字；空 ⇒ `-`。"""
    s = _LOG_SAFE.sub("", str(v if v is not None else ""))[:maxlen]
    return s or "-"


def mcp_jsonrpc_summary(body):
    """body bytes → `(method, tool)`（純函式；**任何失敗都回 `("?", "-")`、絕不 raise**）。

    單筆：`method`；`tools/call` 時 `tool`＝`params.name`。batch（list）：各值以 `,` 串（前 5 筆）。"""
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:                                   # noqa: BLE001 —— 觀測不得害死呼叫
        return "?", "-"
    items = obj if isinstance(obj, list) else [obj]
    methods, tools = [], []
    for it in items[:5]:
        if not isinstance(it, dict):
            methods.append("?")
            continue
        m = it.get("method")
        methods.append(m if isinstance(m, str) and m else "?")
        if m == "tools/call":
            p = it.get("params")
            n = p.get("name") if isinstance(p, dict) else None
            tools.append(n if isinstance(n, str) and n else "?")
    if not methods:
        return "?", "-"
    return ",".join(methods), (",".join(tools) if tools else "-")


def format_mcp_log(ip, status, method, tool):
    """一行 log（與 app.py 的 `[tunnel-gate]` 前綴一致）。**永不含 token**（呼叫端也不傳）。"""
    return (f"[tunnel-gate] mcp ip={_log_tok(ip)} status={int(status or 0)} "
            f"method={_log_tok(method)} tool={_log_tok(tool)}")


def _default_log(line):
    try:
        print(line, flush=True)
    except Exception:                                   # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# ASGI 掛鉤（薄殼：只負責把 decide() 的結果翻成 HTTP 回應）
# ---------------------------------------------------------------------------
class _TunnelGateASGI:
    def __init__(self, inner, user, password, realm=DEFAULT_REALM,
                 prefixes=BLOCKED_PREFIXES, aldergrove_user="", aldergrove_pass="",
                 tiered=TIER_PREFIXES, open_access=False,
                 environ=None, mcp_rpm=None, clock=None, log=None):
        if not (user and password) and not open_access:
            raise RuntimeError(
                "tunnel gate: user/password 不得為空（fail-closed；"
                "設了 BOARD_TUNNEL_AUTH=1 就必須同時給 USER/PASS）")
        self.inner = inner
        # ★★ POST /mcp 窄道（2026-09-23）：token **每個請求現讀**（與 McpTransportProxy 同）；
        #   `environ` 只為 selftest 注入，正式路徑＝`os.environ`。
        self._environ = os.environ if environ is None else environ
        _rpm = mcp_rpm if mcp_rpm is not None else mcp_rpm_from_env(self._environ)
        self._mcp_limiter = SlidingWindowLimiter(_rpm, MCP_RATE_WINDOW_S, clock=clock)
        self._log = log or _default_log
        # ★ 開放模式下憑證**照常存起來**（不刪、不清空）：旗標一關就逐位元回到原行為，
        #   不需要重新設定任何東西。`decide()` 在開放模式根本不看 `self._expects`。
        self._expects = build_expects(user, password, aldergrove_user, aldergrove_pass)
        self._realm = realm
        self._prefixes = tuple(prefixes)
        self._tiered = tuple(tiered)
        self._open = bool(open_access)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            # lifespan 原樣穿透。websocket：**隧道流量一律拒**（1008 policy violation）。
            # 今日 app 零 ws 路由＝理論縫，但「未來誰加一條 ws 路由，閘就靜默失守」
            # 是本模組自己 fail-safe 哲學不允許的陷阱——http 面的封鎖清單與 Basic Auth
            # 都不會自動蓋到 ws handshake，這裡不擋就沒人擋。
            if (scope.get("type") == "websocket"
                    and is_tunnel(header_map(scope.get("headers")))):
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.inner(scope, receive, send)
        hmap = header_map(scope.get("headers"))
        # ★★ POST /mcp 窄道（2026-09-23、#168）：寫在 decide() **之前**、不看 self._open
        #   ⇒ OPEN ACCESS 不鬆它；只認 (POST, "/mcp")，其餘一律落回 decide()（`/mcp` 仍在
        #   BLOCKED_PREFIXES ⇒ GET／`/mcp/`／`/mcp/x` 照舊 404）。非隧道恆不進（本機/LAN 不變）。
        if is_mcp_lane(scope.get("method", "GET"), scope.get("path", "/"), hmap):
            return await self._mcp_lane(scope, receive, send, hmap)
        verdict, _tier = decide(scope.get("path", "/"), hmap, self._expects,
                                self._prefixes, self._tiered, self._open)
        if verdict == PASS:
            return await self.inner(scope, receive, send)
        if verdict == BLOCK:
            return await self._plain(send, 404, b"404 Not Found")
        body = b"401 Unauthorized"
        await send({"type": "http.response.start", "status": 401,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate",
                         f'Basic realm="{self._realm}", charset="UTF-8"'.encode("latin-1")),
                    ]})
        await send({"type": "http.response.body", "body": body})

    async def _peek_body(self, hmap, receive):
        """為了 log 讀 body（**只在 JSON 且 ≤ 64KB**）。回 `(buffered_msgs, body_or_None)`。
        讀過的訊息一律留著、由呼叫端原樣重放給下游（不改變放行結果）。"""
        if "json" not in hmap.get("content-type", "").lower():
            return [], None
        try:
            if int(hmap.get("content-length", "0") or 0) > MCP_PEEK_MAX:
                return [], None
        except ValueError:
            return [], None
        msgs, chunks, size = [], [], 0
        while True:
            msg = await receive()
            msgs.append(msg)
            if msg.get("type") != "http.request":
                return msgs, None                       # disconnect 等：照樣重放、不解析
            chunk = msg.get("body", b"") or b""
            size += len(chunk)
            chunks.append(chunk)
            if size > MCP_PEEK_MAX:
                return msgs, None                       # 超量：停 peek、其餘由下游自己讀
            if not msg.get("more_body", False):
                return msgs, b"".join(chunks)

    async def _mcp_lane(self, scope, receive, send, hmap):
        """窄道本體：peek（log 用）→ 503 → 401 → 429 → 放行（重放 body、截 status 記 log）。"""
        ip = hmap.get("cf-connecting-ip", "")
        method, tool = "?", "-"
        buffered = []
        try:
            buffered, body = await self._peek_body(hmap, receive)
            if body is not None:
                method, tool = mcp_jsonrpc_summary(body)
        except Exception:                               # noqa: BLE001 —— peek 失敗不改放行
            pass
        token = self._environ.get(ENV_MCP_TOKEN) or None
        verdict = mcp_bearer_verdict(hmap, token)
        if verdict == MCP_NO_TOKEN:
            self._log(format_mcp_log(ip, 503, method, tool))
            return await self._json(send, 503, b'{"detail":"mcp token not configured"}')
        if verdict == MCP_UNAUTH:
            self._log(format_mcp_log(ip, 401, method, tool))
            return await self._json(send, 401, b'{"detail":"unauthorized"}',
                                    [(b"www-authenticate", b'Bearer realm="Tendline Board MCP"')])
        allowed, retry = self._mcp_limiter.hit(mcp_rate_key(hmap))
        if not allowed:
            self._log(format_mcp_log(ip, 429, method, tool))
            return await self._json(send, 429, b'{"detail":"rate limited"}',
                                    [(b"retry-after", str(retry).encode("ascii"))])

        pending = list(buffered)

        async def _replay():
            if pending:
                return pending.pop(0)
            return await receive()

        status = [0]

        async def _send(msg):
            if msg.get("type") == "http.response.start" and not status[0]:
                status[0] = int(msg.get("status") or 0)
            await send(msg)

        try:
            await self.inner(scope, _replay, _send)
        finally:
            self._log(format_mcp_log(ip, status[0] or 500, method, tool))

    @staticmethod
    async def _json(send, status, body, extra=()):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())] + list(extra)})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _plain(send, status, body):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def wrap(app, user, password, realm=DEFAULT_REALM, prefixes=BLOCKED_PREFIXES,
         aldergrove_user="", aldergrove_pass="", open_access=False,
         environ=None, mcp_rpm=None, clock=None, log=None):
    """把整個 ASGI app 包上隧道閘。缺 saltmarsh 憑證＝raise（fail-closed）。
    aldergrove 憑證**選配**：不給＝該層不存在＝分層前的行為。

    `open_access=True`（Chris 2026-09-02 暫時開放）：隧道流量不再要帳密、不再分層擋
    `/budget`／`/cost`；`BLOCKED_PREFIXES` **仍 404**。此模式下憑證變成選配
    （沒有東西要認）——但閘**仍必須掛著**，因為那三條 404 沒有第二個人在守。

    ★★ 2026-09-23 窄道參數（全選配、正式路徑全不給）：`environ`（讀 `BOARD_MCP_TOKEN`／
    `BOARD_MCP_TUNNEL_RPM`，預設 `os.environ`）、`mcp_rpm`（覆寫上限）、`clock`（限速器時鐘）、
    `log`（一行 log 的出口，預設 stdout）——後三個只為 selftest／smoke 注入。"""
    return _TunnelGateASGI(app, user, password, realm=realm, prefixes=prefixes,
                           aldergrove_user=aldergrove_user, aldergrove_pass=aldergrove_pass,
                           open_access=open_access, environ=environ, mcp_rpm=mcp_rpm,
                           clock=clock, log=log)


# ---------------------------------------------------------------------------
# selftest（純離線；迷你 ASGI harness）
# ---------------------------------------------------------------------------
def _selftest():
    import asyncio
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    n_fail = [0]

    def ck(cond, name):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            n_fail[0] += 1

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"secret page"})

    def call(app, path="/", headers=None, scope_type="http", method=None, body=b"",
             chunks=None):
        """method 不給＝scope 不帶 `method`（ASGI 殼預設 GET；既有案一律如此）。
        `chunks` 給了就分多段送（驗 peek 重放）。"""
        sent = []
        parts = list(chunks) if chunks is not None else [body]
        q = [{"type": "http.request", "body": c, "more_body": i < len(parts) - 1}
             for i, c in enumerate(parts)]

        async def _send(msg):
            sent.append(msg)

        async def _recv():
            return q.pop(0) if q else {"type": "http.disconnect"}

        scope = {"type": scope_type, "path": path, "headers": headers or []}
        if method is not None:
            scope["method"] = method
        asyncio.run(app(scope, _recv, _send))
        status = next((m["status"] for m in sent
                       if m["type"] == "http.response.start"), None)
        body = b"".join(m.get("body", b"") for m in sent
                        if m["type"] == "http.response.body")
        return status, body, sent

    GOOD = expected_header("saltmarsh", "s3cret").encode("latin-1")
    BAD = expected_header("saltmarsh", "wrong").encode("latin-1")
    AGP = expected_header("agp", "agp-p@ss").encode("latin-1")
    CF = (b"cf-ray", b"8a1b2c3d4e5f-TPE")            # cloudflared 注入
    CFIP = (b"cf-connecting-ip", b"203.0.113.9")
    app = wrap(inner, "saltmarsh", "s3cret")                # 單層（aldergrove 未設）＝分層前的形
    app2 = wrap(inner, "saltmarsh", "s3cret", aldergrove_user="agp", aldergrove_pass="agp-p@ss")

    # --- (1) env 閘：未設＝不掛 -------------------------------------------------
    ck(should_wrap({}) is False, "env 未設 → should_wrap False（middleware 完全不掛）")
    ck(should_wrap({ENV_ENABLE: "0"}) is False, "BOARD_TUNNEL_AUTH=0 → 不掛")
    ck(should_wrap({ENV_ENABLE: "true"}) is False,
       "BOARD_TUNNEL_AUTH=true → 不掛（只認 '1'，一個值一種寫法）")
    en, u, p = creds_from_env({ENV_ENABLE: "1", ENV_USER: "saltmarsh", ENV_PASS: "s3cret"})
    ck(en and u == "saltmarsh" and p == "s3cret", "creds_from_env 三值取出正確")
    try:
        wrap(inner, "saltmarsh", "")
        ck(False, "啟用但缺密碼 → raise（fail-closed）")
    except RuntimeError:
        ck(True, "啟用但缺密碼 → raise（fail-closed）")

    # --- (2) 隧道流量：Basic Auth 三態 -----------------------------------------
    st, body, _ = call(app, "/", [CF])
    ck(st == 401 and b"secret page" not in body, "隧道 ＋ 無憑證 → 401、不洩內容")
    st, _, sent = call(app, "/", [CF])
    ck(any(k.lower() == b"www-authenticate" and b"Basic realm=" in v
           for m in sent if m["type"] == "http.response.start"
           for k, v in m["headers"]), "401 帶 WWW-Authenticate（瀏覽器跳原生帳密框）")
    st, _, _ = call(app, "/", [CF, (b"authorization", BAD)])
    ck(st == 401, "隧道 ＋ 錯憑證 → 401")
    st, body, _ = call(app, "/", [CF, (b"authorization", GOOD)])
    ck(st == 200 and body == b"secret page", "隧道 ＋ 對憑證 → 200 原內容")
    st, _, _ = call(app, "/", [CF, (b"authorization", b"Bearer xyz")])
    ck(st == 401, "隧道 ＋ 非 Basic scheme → 401")
    st, body, _ = call(app, "/", [CFIP, (b"authorization", GOOD)])
    ck(st == 200, "CF-Connecting-IP 亦判定為隧道（三訊號任一即可）")
    st, _, _ = call(app, "/", [CFIP])
    ck(st == 401, "只有 CF-Connecting-IP、無憑證 → 401")
    st, _, _ = call(app, "/", [(b"x-board-tunnel", b"1")])
    ck(st == 401, "X-Board-Tunnel（Worker 加的第二道訊號）亦判定為隧道 → 401")
    st, _, _ = call(app, "/mcp", [(b"x-board-tunnel", b"1"), (b"authorization", GOOD)])
    ck(st == 404, "X-Board-Tunnel ＋ 封鎖路徑 → 404（第二道訊號與 CF 標頭等價）")

    # --- (3) 封鎖清單：帶對憑證仍 404 -------------------------------------------
    for path in ("/mcp", "/landing/opr/warm", "/api/board", "/api/board/list_stations"):
        st, body, _ = call(app, path, [CF, (b"authorization", GOOD)])
        ck(st == 404 and b"secret page" not in body,
           f"隧道 ＋ 對憑證 ＋ 封鎖路徑 {path} → 404（不論憑證）")
    st, _, _ = call(app, "/mcp", [CF])
    ck(st == 404, "封鎖路徑無憑證 → 404（不是 401：對外那面它根本不存在）")
    # --- (3b) ★★ 2026-09-22 C-B4 局部重開（Chris：「可接受。開包，走 (b)」）-------
    #   助理與兩條翻譯路徑**已移出**封鎖清單 ⇒ 隧道流量打得到。這一組是**反向釘**：
    #   誰把它們加回 BLOCKED_PREFIXES，紅的是**行為**（200 變 404），不是一條清單長度。
    #   ⚠ 花費那一面**沒有被放生**，改由 `board_assistant.llm_budget_*`
    #     （env `BOARD_LLM_DAILY_MAX`、全站全流量含內網）守 ⇒ 兩個機制擋兩件不同的事。
    for path in ("/assistant", "/assistant/panel", "/assistant/turn/abc123",
                 "/equipment/cell/translate", "/equipment/translate",
                 "/equipment/translate/all"):
        st, body, _ = call(app, path, [CF, (b"authorization", GOOD)])
        ck(st == 200 and body == b"secret page",
           f"★★ C-B4 局部重開：隧道 ＋ 對憑證 ＋ {path} → 200（SALTMARSH 要用助理／翻譯）")
    st, _, _ = call(app, "/assistant", [CF])
    ck(st == 401,
       "★★ C-B4 局部重開：隧道 ＋ **無憑證** ＋ /assistant → 401（不再是 404）"
       "——它自此是一條普通頁面路徑，照 Basic Auth 那條路走")
    st, _, _ = call(app, "/mcp/", [CF, (b"authorization", GOOD)])
    ck(st == 404, "尾斜線 /mcp/ 同樣封鎖（不留一個斜線的縫）")

    # --- (4) 路徑 segment 精確比對（不誤傷同前綴的別的路徑）---------------------
    ck(is_blocked_path("/mcp") and is_blocked_path("/mcp/probe"),
       "segment 比對：/mcp 與其子路徑命中")
    ck(not is_blocked_path("/mcpfoo"), "segment 比對：/mcpfoo 不命中")
    ck(not is_blocked_path("/mcps"), "segment 比對：/mcps 不命中")
    ck(not is_blocked_path("/equipment"), "segment 比對：/equipment 不命中")
    ck(not is_blocked_path("/equipment/cell"), "segment 比對：/equipment/cell 不命中")
    ck(not is_blocked_path("/mcpx"), "segment 比對：/mcpx 不命中")
    # ★★ 2026-09-22 C-B4 局部重開的**清單面反向釘**（與 (3b) 的行為釘成對）
    ck(not is_blocked_path("/assistant") and not is_blocked_path("/assistant/panel"),
       "★★ C-B4 局部重開：`/assistant` 與子路徑**不再**命中封鎖清單")
    ck(not is_blocked_path("/equipment/cell/translate")
       and not is_blocked_path("/equipment/translate")
       and not is_blocked_path("/equipment/translate/all"),
       "★★ C-B4 局部重開：兩條翻譯路徑**不再**命中封鎖清單")
    ck("/assistant" not in BLOCKED_PREFIXES
       and "/equipment/translate" not in BLOCKED_PREFIXES
       and "/equipment/cell/translate" not in BLOCKED_PREFIXES,
       "★★ 接線釘：三條 LLM 路徑真的已移出 BLOCKED_PREFIXES（不是只改了註解）")
    ck(BLOCKED_PREFIXES == ("/mcp", "/landing/opr/warm", "/api/board"),
       "★★ 全等釘：共同封鎖清單＝三條（`/mcp`／`/landing/opr/warm`／`/api/board`）"
       "——改它＝改對外曝光面，必須連同 SPEC-SALTMARSH-ACCESS §5 紅線 7 一起改")
    ck("/landing/opr/warm" in BLOCKED_PREFIXES
       and is_blocked_path("/landing/opr/warm")
       and not is_blocked_path("/landing/body") and not is_blocked_path("/landing/state"),
       "★ 接線釘：O3 暖機那**一條完整路徑**仍擋、landing 本體不連坐")
    # ★ 2026-09-07 WebMCP 內網唯讀試點（`SPEC-WEBMCP.md` §4）
    ck("/api/board" in BLOCKED_PREFIXES,
       "★ 接線釘：`/api/board` 真的在 BLOCKED_PREFIXES（WebMCP 工具面不對隧道曝）")
    ck(is_blocked_path("/api/board") and is_blocked_path("/api/board/list_stations"),
       "★ segment 比對：`/api/board` 整段命中（六個工具路徑自動在保護內）")
    ck(not is_blocked_path("/api") and not is_blocked_path("/api/data")
       and not is_blocked_path("/api/qlik") and not is_blocked_path("/api/wrx"),
       "★ segment 比對：既有 `/api/*`（data／qlik）與 `/api/wrx` 不被誤傷")
    st, _, _ = call(app, "/api/board/list_stations", [CF, (b"authorization", GOOD)])
    ck(st == 404, "隧道 ＋ 對憑證 ＋ /api/board/list_stations → 404（WebMCP 對外關）")
    st, body, _ = call(app, "/mcpfoo", [CF, (b"authorization", GOOD)])
    ck(st == 200, "隧道 ＋ 對憑證 ＋ /mcpfoo → 200（未被前綴誤傷）")

    # --- (5) 非隧道流量：完全不受影響（本輪最承重的一組）-----------------------
    for path in ("/", "/equipment", "/assistant", "/mcp", "/equipment/cell/translate",
                 "/equipment/translate/all", "/api/board/list_stations",
                 "/landing/opr/warm"):
        st, body, _ = call(app, path, [])
        ck(st == 200 and body == b"secret page",
           f"非隧道 {path} → 200 原樣（本機/LAN 逐位元不變、連封鎖路徑都照走）")
    st, body, _ = call(app, "/", [(b"x-forwarded-for", b"1.2.3.4")])
    ck(st == 200, "一般 proxy 標頭（X-Forwarded-For）不算隧道訊號")

    # --- (6) 偽造方向安全性（fail-safe direction）------------------------------
    st_forge, _, _ = call(app, "/", [CF])                       # LAN 自加 Cf-Ray
    st_plain, _, _ = call(app, "/", [])                          # 同一人不加
    ck(st_plain == 200 and st_forge == 401,
       "fail-safe direction：偽造 Cf-Ray 只會把自己從寬鬆側搬到嚴格側（多擋、永不多放）")
    st_forge_blocked, _, _ = call(app, "/mcp", [CF, (b"authorization", GOOD)])
    ck(st_forge_blocked == 404,
       "fail-safe direction：偽造者連 /mcp 都拿不到（偽造無法換到任何多的權限）")

    # --- (7) 雜項：大小寫、non-http scope、decide 純函式直測 --------------------
    st, _, _ = call(app, "/", [(b"CF-Ray", b"x"), (b"Authorization", GOOD)])
    ck(st == 200, "header key 大小寫容忍（ASGI 慣例小寫、防禦性 .lower() 仍收）")
    st, body, _ = call(app, "/", None, scope_type="lifespan")
    ck(body == b"secret page", "non-http scope 原樣穿透（不擋 lifespan）")
    _, _, sent_ws = call(app, "/", [CF], scope_type="websocket")
    ck(any(m.get("type") == "websocket.close" and m.get("code") == 1008 for m in sent_ws)
       and not any(m.get("type") == "http.response.body" for m in sent_ws),
       "websocket ＋ 隧道標頭 → close 1008（未來加 ws 路由也不會靜默失守）")
    _, body_ws, _ = call(app, "/", [], scope_type="websocket")
    ck(body_ws == b"secret page",
       "websocket 無隧道標頭 → 穿透（本機/LAN ws 不受影響；inner 假 app 直接回 body）")
    exp = expected_header("saltmarsh", "s3cret")
    E1 = build_expects("saltmarsh", "s3cret")
    ck(decide("/", {}, E1)[0] == PASS, "decide：無隧道標頭 → PASS")
    ck(decide("/", {"cf-ray": "x"}, E1)[0] == CHALLENGE, "decide：隧道無憑證 → CHALLENGE")
    ck(decide("/mcp", {"cf-ray": "x", "authorization": exp}, E1)[0] == BLOCK,
       "decide：隧道對憑證封鎖路徑 → BLOCK")
    ck(decide("/", {"cf-ray": "x", "authorization": exp}, E1) == (PASS, TIER_SALTMARSH),
       "decide：隧道對憑證一般路徑 → (PASS, saltmarsh)")
    hm = header_map([(b"CF-Ray", b"a"), (b"cf-ray", b"b")])
    ck(hm["cf-ray"] == "a", "重複標頭取第一個（疊第二個合法值繞不過第一個）")

    # --- (8) ★ 隧道帳號分層（2026-08-27）--------------------------------------
    #   前提：公司內網 client isolation ⇒ Aldergrove Precision 同仁也只能走隧道 ⇒ 隧道不再等於 SALTMARSH。
    E2 = build_expects("saltmarsh", "s3cret", "agp", "agp-p@ss")
    ck(len(E1) == 1 and len(E2) == 2, "build_expects：aldergrove 未設＝該層不存在")
    ck(build_expects("saltmarsh", "s3cret", "agp", "")[0][0] == TIER_SALTMARSH
       and len(build_expects("saltmarsh", "s3cret", "agp", "")) == 1,
       "build_expects：aldergrove 只給帳號不給密碼 ⇒ 不產出該層（半套＝沒有）")
    ck(auth_tier({"authorization": exp}, E2) == TIER_SALTMARSH, "認層：saltmarsh 憑證 → saltmarsh")
    ck(auth_tier({"authorization": expected_header("agp", "agp-p@ss")}, E2) == TIER_ALDERGROVE,
       "認層：aldergrove 憑證 → aldergrove")
    ck(auth_tier({"authorization": "Basic zzz"}, E2) is None, "認層：亂憑證 → None")
    ck(auth_tier({}, E2) is None, "認層：沒有 Authorization → None")
    ck(auth_tier({"authorization": exp},
                 build_expects("same", "pw", "same", "pw")) is None
       or auth_tier({"authorization": expected_header("same", "pw")},
                    build_expects("same", "pw", "same", "pw")) == TIER_SALTMARSH,
       "認層：兩層撞同一組值 ⇒ 取最嚴的 saltmarsh（不是最後一個命中）")
    ck(blocked_for(TIER_ALDERGROVE) == BLOCKED_PREFIXES,
       "清單：aldergrove 只擋 Hermes／翻譯（那三條是 LLM 花費，不是機密分級）")
    ck(blocked_for(TIER_SALTMARSH) == BLOCKED_PREFIXES + TIER_PREFIXES,
       "清單：saltmarsh 多擋 /budget（採購金額）")
    ck(blocked_for(None) == blocked_for(TIER_SALTMARSH) and blocked_for("future") == blocked_for(TIER_SALTMARSH),
       "清單：認不出的層一律從嚴（日後多一層忘了改這裡，拿到的是嚴的那份）")

    # 四格硬釘（① saltmarsh 看不到 /budget ② aldergrove 看得到 ③ aldergrove 仍看不到 Hermes ④ 回歸）
    st, body, _ = call(app2, "/budget", [CF, (b"authorization", GOOD)])
    ck(st == 404 and b"secret page" not in body,
       "★① 隧道 ＋ saltmarsh 憑證 ＋ /budget → 404（採購金額不對 SALTMARSH 曝）")
    st, _, _ = call(app2, "/budget/body", [CF, (b"authorization", GOOD)])
    ck(st == 404, "★① /budget/body 同樣 404（fragment 不是後門）")
    st, body, _ = call(app2, "/budget", [CF, (b"authorization", AGP)])
    ck(st == 200 and body == b"secret page",
       "★② 隧道 ＋ aldergrove 憑證 ＋ /budget → 200（同仁走隧道才看得到 Tendline Board）")
    st, _, _ = call(app2, "/api/board/list_stations", [CF, (b"authorization", AGP)])
    ck(st == 404, "★③ 隧道 ＋ aldergrove 憑證 ＋ /api/board → 仍 404（agent 端點兩層都擋）")
    st, _, _ = call(app2, "/mcp", [CF, (b"authorization", AGP)])
    ck(st == 404, "★③ 隧道 ＋ aldergrove 憑證 ＋ /mcp → 仍 404")
    st, body, _ = call(app2, "/assistant/panel", [CF, (b"authorization", AGP)])
    ck(st == 200 and body == b"secret page",
       "★③ 2026-09-22：隧道 ＋ aldergrove 憑證 ＋ /assistant → **200**（C-B4 局部重開）")
    st, _, _ = call(app, "/budget", [CF, (b"authorization", GOOD)])
    ck(st == 404, "★④ 回歸：aldergrove 未設時 saltmarsh 憑證打 /budget 仍 404")
    for path, hdrs, want in (("/", [CF], 401), ("/", [CF, (b"authorization", BAD)], 401),
                             ("/", [CF, (b"authorization", GOOD)], 200),
                             ("/mcp", [CF], 404), ("/mcp", [CF, (b"authorization", GOOD)], 404),
                             ("/", [], 200), ("/assistant", [], 200)):
        s1, _, _ = call(app, path, hdrs)
        s2, _, _ = call(app2, path, hdrs)
        ck(s1 == want and s2 == want,
           f"★④ 回歸：{path} {'帶' if hdrs else '無'}標頭 → {want}（分層前後逐位元相同）")
    st, _, _ = call(app2, "/budget", [CF])
    ck(st == 401,
       "未認證打 /budget → 401 不是 404（否則 aldergrove 的人永遠等不到帳密框；"
       "閘在路由之前 ⇒ 401 與不存在的路徑無從分辨、零洩漏）")

    # --- (8b) ★ `/cost` 維護成本頁（2026-08-28 加進 TIER_PREFIXES）---------------
    #   與 `/budget` **同一顆 opswiki-mcp DB、同樣是金額** ⇒ 機密邊界必須一致。
    #   這一組刻意與上面 `/budget` 那四格**平行寫一次**：日後誰把 `/cost` 從清單裡拿掉，
    #   紅的不會只是一條「清單長度」的斷言，而是四個具體的行為。
    ck("/cost" in TIER_PREFIXES and "/budget" in TIER_PREFIXES,
       f"★ `/cost` 與 `/budget` 都在 TIER_PREFIXES（實得 {TIER_PREFIXES}）")
    # ⚠ 這個迷你 harness 的 `path` 直接進 ASGI `scope["path"]`＝**不含 querystring**
    #   （真的 ASGI 伺服器會先把 `?…` 拆到 `query_string`）。寫成 `/cost?lang=zh` 會變成
    #   一個「路徑裡有問號」的假案例，紅得莫名其妙。帶 query 的那一面由 run_smoke 的
    #   `cost_page_round` 用 TestClient 驗。
    for path in ("/cost", "/cost/body", "/cost/drill"):
        st, body, _ = call(app2, path, [CF, (b"authorization", GOOD)])
        ck(st == 404 and b"secret page" not in body,
           f"★① 隧道 ＋ saltmarsh 憑證 ＋ {path} → 404（採購金額不對 SALTMARSH 曝；fragment 不是後門）")
    st, body, _ = call(app2, "/cost", [CF, (b"authorization", AGP)])
    ck(st == 200 and body == b"secret page",
       "★② 隧道 ＋ aldergrove 憑證 ＋ /cost → 200（同仁走隧道才看得到 Tendline Board）")
    st, _, _ = call(app2, "/cost/drill", [CF, (b"authorization", AGP)])
    ck(st == 200, "★② aldergrove 憑證 ＋ /cost/drill → 200（整棵子樹一致）")
    st, _, _ = call(app2, "/cost", [CF])
    ck(st == 401,
       "★ 未認證打 /cost → 401 不是 404（否則 aldergrove 的人永遠等不到帳密框；"
       "閘在路由之前 ⇒ 與不存在的路徑無從分辨、零洩漏）")
    st, _, _ = call(app, "/cost", [CF, (b"authorization", GOOD)])
    ck(st == 404, "★④ 回歸：aldergrove 未設時 saltmarsh 憑證打 /cost 仍 404")
    ck(not is_blocked_path("/costly", blocked_for(TIER_SALTMARSH))
       and not is_blocked_path("/costs", blocked_for(TIER_SALTMARSH)),
       "segment 比對：/costly、/costs 不被 /cost 誤傷")
    ck(is_blocked_path("/cost/", blocked_for(TIER_SALTMARSH)),
       "尾斜線 /cost/ 同樣封鎖（不留一個斜線的縫）")
    ck(not is_blocked_path("/cost", blocked_for(TIER_ALDERGROVE)),
       "aldergrove 層不擋 /cost（那是機密分級，不是 LLM 花費）")

    # nav 分層（tier_of_request：模板 context processor 用的那一支）
    ENVB = {ENV_USER: "saltmarsh", ENV_PASS: "s3cret",
            ENV_ALDERGROVE_USER: "agp", ENV_ALDERGROVE_PASS: "agp-p@ss"}
    ck(tier_of_request({}, ENVB) == (False, None), "nav：本機/LAN → (False, None)＝完整權限")
    ck(tier_of_request({"cf-ray": "x", "authorization": exp}, ENVB) == (True, TIER_SALTMARSH),
       "nav：隧道 saltmarsh → 藏 Budget 標籤")
    ck(tier_of_request({"cf-ray": "x", "authorization": expected_header("agp", "agp-p@ss")},
                       ENVB) == (True, TIER_ALDERGROVE), "nav：隧道 aldergrove → 渲染 Budget 標籤")
    ck(tier_of_request({"cf-ray": "x"}, ENVB) == (True, None),
       "nav：隧道但認不出層 → tier None ⇒ 模板 fail-safe 藏標籤")
    ck(expects_from_env({}) == (), "nav：env 全空 ⇒ 零層（空憑證絕不變成可撞的期望值）")
    ck(auth_tier({"authorization": expected_header("", "")}, expects_from_env({})) is None,
       "nav：`Basic <base64 of ':'>` 撞不到任何層（空憑證陷阱）")
    ck(aldergrove_creds_from_env({}) == ("", ""), "aldergrove 憑證無預設值（Chris 自己填 internal-file）")
    ck(f'"{ENV_ALDERGROVE_PASS}"' in __doc__ or ENV_ALDERGROVE_PASS in __doc__,
       "aldergrove env 名寫在 docstring（下一個人找得到要設什麼）")

    # --- (9) ★★ OPEN ACCESS 暫時開放（Chris 2026-09-02）------------------------
    #   指示原話：「為了讓不同部門的長官可以來 Tendline Board 看，我要暫時開放整個 Tendline Board（包含 /budget
    #   /cost 頁面）給任何連進服務的人，不需要帳號密碼」。
    #   這一組守兩件相反方向的事：**開的時候真的全開**、**關的時候逐位元回到原狀**。
    #   ⚠ 反模式警告：日後想「順手把 /mcp 也開了」的人，(9c) 那幾條會紅——
    #     那不是測試太嚴，那是 C-B4（LLM 端點對外 404）還活著。
    app_open = wrap(inner, "saltmarsh", "s3cret", aldergrove_user="agp", aldergrove_pass="agp-p@ss",
                    open_access=True)

    # (9a) env 讀取：恰為 "1"（一個值一種寫法；「我以為關了其實還開著」比反過來更可怕）
    ck(open_access_from_env({ENV_OPEN_ACCESS: "1"}) is True, "open：env=1 → True")
    for bad in ("0", "true", "yes", "TRUE", " ", "", "2"):
        ck(open_access_from_env({ENV_OPEN_ACCESS: bad}) is False,
           f"open：env={bad!r} → False（只認 '1'）")
    ck(open_access_from_env({}) is False, "open：env 未設 → False（預設關）")
    ck(open_access_from_env({ENV_OPEN_ACCESS: " 1 "}) is True,
       "open：前後空白容忍（`internal-file` 是手改的檔）")
    ck(should_wrap({ENV_OPEN_ACCESS: "1"}) is True,
       "open：只設 OPEN_ACCESS 也要掛閘（BLOCKED_PREFIXES 的 404 沒有第二個人在守）")
    ck(should_wrap({}) is False, "open：兩個 env 都沒設 → 不掛（預設 OFF 未被本功能污染）")
    ck(should_wrap({ENV_ENABLE: "1"}) is True, "open：舊 env 單獨仍掛（回歸）")
    # 開放模式下憑證變選配（沒有東西要認）——但這**不放寬**非開放路徑的 fail-closed
    wrap(inner, "", "", open_access=True)          # 不 raise ＝ 通過
    ck(True, "open：開放模式缺憑證不 raise（此模式下沒有東西要認）")
    try:
        wrap(inner, "", "", open_access=False)
        ck(False, "open：**非**開放模式缺憑證仍 raise（fail-closed 未被本功能放寬）")
    except RuntimeError:
        ck(True, "open：**非**開放模式缺憑證仍 raise（fail-closed 未被本功能放寬）")

    # (9b) 開的時候真的全開：隧道 ＋ **零憑證** ⇒ 全站 200（含 /budget、/cost 全系列）
    for path in ("/", "/equipment", "/utilization", "/daily", "/saltmarsh/weekly",
                 "/budget", "/budget/body", "/cost", "/cost/body", "/cost/drill",
                 "/cost/notes", "/cost/forecast", "/station/segAssembly"):
        st, body, _ = call(app_open, path, [CF])
        ck(st == 200 and body == b"secret page",
           f"★ open ＋ 隧道 ＋ **無帳密** ＋ {path} → 200（長官直接看得到）")
    st, _, _ = call(app_open, "/budget", [CF, (b"authorization", GOOD)])
    ck(st == 200, "★ open：帶 saltmarsh 憑證打 /budget 也 200（開放模式不再分層）")
    st, _, _ = call(app_open, "/cost", [CF, (b"authorization", BAD)])
    ck(st == 200, "★ open：憑證**錯的**也 200（開放模式根本不認憑證）")
    st, _, _ = call(app_open, "/", [CFIP])
    ck(st == 200, "★ open：CF-Connecting-IP 那條訊號同樣放行")
    st, _, _ = call(app_open, "/", [(b"x-board-tunnel", b"1")])
    ck(st == 200, "★ open：X-Board-Tunnel（Worker 第二道訊號）同樣放行")
    st, body, _ = call(app_open, "/", [])
    ck(st == 200 and body == b"secret page",
       "★ open：非隧道（本機/LAN）照舊 200——它本來就沒被檢查過，開放模式對它完全沒差")

    # (9c) ⚠ 開放的是「看」，不是把 LLM 端點暴露出去（C-B4 照舊；Chris 2026-08-05）
    for path in ("/mcp", "/mcp/", "/landing/opr/warm",
                 "/api/board", "/api/board/list_stations", "/api/board/updown_now"):
        st, body, _ = call(app_open, path, [CF])
        ck(st == 404 and b"secret page" not in body,
           f"★ open ＋ {path} → **仍 404**（開放的是「看」，不是 agent 端點／對外觸發取數）")
    # ★★ 2026-09-22 C-B4 局部重開：2026-09-02 那條 HIGH-1 釘（「不擋＝無界燒 LLM」）
    #    **已由保險絲接手**（`board_assistant.llm_budget_*`／`BOARD_LLM_DAILY_MAX`，
    #    全站全流量含內網）⇒ 這裡從「必須 404」翻成「必須 200」，而燒錢那件事沒有被放生。
    st, body, _ = call(app_open, "/equipment/translate/all", [CF])
    ck(st == 200 and body == b"secret page",
       "★★ open ＋ 隧道 ＋ **匿名** POST `/equipment/translate/all` → 200"
       "（C-B4 局部重開；花費上限改由 BOARD_LLM_DAILY_MAX 保險絲守）")
    st, body, _ = call(app_open, "/equipment/translate/all", [])
    ck(st == 200 and body == b"secret page",
       "★ open：本機/LAN 打整頁翻譯照舊 200")
    st, body, _ = call(app_open, "/assistant", [CF, (b"authorization", AGP)])
    ck(st == 200 and body == b"secret page",
       "★★ open ＋ aldergrove 憑證 ＋ /assistant → 200（C-B4 局部重開）")
    st, body, _ = call(app_open, "/assistant", [])
    ck(st == 200 and body == b"secret page",
       "★ open：本機/LAN 打 /assistant 照舊 200（Hermes 本體零改動）")
    _, _, sent_ws_o = call(app_open, "/", [CF], scope_type="websocket")
    ck(any(m.get("type") == "websocket.close" and m.get("code") == 1008 for m in sent_ws_o),
       "★ open ＋ 隧道 websocket → 仍 close 1008（開放模式不鬆 ws 這條縫）")

    # (9d) 鬆綁只住在 decide() 一個地方：`blocked_for` 的 fail-safe 預設未被本功能破壞
    ck(TIER_OPEN not in TIERS, "open：TIER_OPEN 不在 TIERS（它不是一組可認的憑證層）")
    ck(blocked_for(TIER_OPEN) == BLOCKED_PREFIXES + TIER_PREFIXES,
       "★ open：`blocked_for(TIER_OPEN)` **仍回最嚴那份**——鬆綁只在 decide()，"
       "「不是 aldergrove 就從嚴」這條 fail-safe 預設沒有被本功能破掉")
    ck(decide("/budget", {"cf-ray": "x"}, E2, open_access=True) == (PASS, TIER_OPEN),
       "decide：open ＋ 隧道 ＋ /budget → (PASS, open)")
    ck(decide("/mcp", {"cf-ray": "x"}, E2, open_access=True) == (BLOCK, TIER_OPEN),
       "decide：open ＋ 隧道 ＋ /mcp → (BLOCK, open)")
    ck(decide("/budget", {}, E2, open_access=True) == (PASS, None),
       "decide：open ＋ 非隧道 → (PASS, None)（那條路徑一個字都沒被動過）")
    ck(decide("/budget", {"cf-ray": "x"}, (), open_access=True) == (PASS, TIER_OPEN),
       "decide：open ＋ 零憑證設定 ⇒ 仍 PASS（開放模式不依賴 expects）")

    # (9e) nav：開放模式要渲染 Budget／Cost 標籤（否則「頁面看得到、入口找不到」）
    ENVO = dict(ENVB, **{ENV_OPEN_ACCESS: "1"})
    ck(tier_of_request({"cf-ray": "x"}, ENVO) == (True, TIER_OPEN),
       "★ open nav：隧道無憑證 → (True, 'open') ⇒ `_nav.html` 渲染 Budget/Cost")
    ck(tier_of_request({"cf-ray": "x", "authorization": exp}, ENVO) == (True, TIER_OPEN),
       "★ open nav：隧道帶 saltmarsh 憑證也 → 'open'（開放模式不分層）")
    ck(tier_of_request({}, ENVO) == (False, None),
       "★ open nav：本機/LAN 仍 (False, None)＝完整權限（未被本功能改）")

    # (9f) ★★ 關掉旗標 ⇒ **逐位元回到原狀**（本組是「暫時」兩個字的技術保證）
    #   同一組憑證造三個 app：不給 kwarg／明給 False／給 True，前兩者必須逐格相同。
    app_off_a = wrap(inner, "saltmarsh", "s3cret", aldergrove_user="agp", aldergrove_pass="agp-p@ss")
    app_off_b = wrap(inner, "saltmarsh", "s3cret", aldergrove_user="agp", aldergrove_pass="agp-p@ss",
                     open_access=False)
    MATRIX = (("/", [CF]), ("/", [CF, (b"authorization", BAD)]),
              ("/", [CF, (b"authorization", GOOD)]), ("/", [CF, (b"authorization", AGP)]),
              ("/", []), ("/budget", [CF]), ("/budget", [CF, (b"authorization", GOOD)]),
              ("/budget", [CF, (b"authorization", AGP)]), ("/budget", []),
              ("/cost", [CF]), ("/cost", [CF, (b"authorization", GOOD)]),
              ("/cost/drill", [CF, (b"authorization", AGP)]), ("/cost", []),
              ("/assistant", [CF]), ("/assistant", [CF, (b"authorization", AGP)]),
              ("/assistant", []), ("/mcp", [CF, (b"authorization", GOOD)]),
              ("/equipment/cell/translate", [CF, (b"authorization", AGP)]),
              # ★ 2026-09-02 補登記的整頁翻譯段也要進回歸矩陣（閉模式行為不因本次修補而變）
              ("/equipment/translate/all", [CF]),
              ("/equipment/translate/all", [CF, (b"authorization", AGP)]),
              ("/equipment/translate/all", []),
              ("/assistantfoo", [CF, (b"authorization", GOOD)]))
    for path, hdrs in MATRIX:
        s_ref, b_ref, _ = call(app2, path, hdrs)          # 分層閘原貌（本檔上半段驗過的那個）
        s_a, b_a, _ = call(app_off_a, path, hdrs)
        s_b, b_b, _ = call(app_off_b, path, hdrs)
        ck(s_ref == s_a == s_b and b_ref == b_a == b_b,
           f"★★ open 關閉 ⇒ 逐位元回到原狀：{path} → {s_ref}"
           f"（實得 {s_a}/{s_b}；body 同 {b_ref == b_a == b_b}）")
    ck(tier_of_request({"cf-ray": "x", "authorization": exp}, ENVB) == (True, TIER_SALTMARSH)
       and tier_of_request({"cf-ray": "x"}, ENVB) == (True, None),
       "★★ open 關閉 ⇒ nav 分層回到原狀（saltmarsh→saltmarsh、認不出→None）")
    ck(f'"{ENV_OPEN_ACCESS}"' in __doc__ or ENV_OPEN_ACCESS in __doc__,
       "open：env 名寫在 docstring（下一個人找得到要關哪一個）")

    # --- (10) ★★ POST /mcp 隧道窄道（Chris 2026-09-23：「1，附三個條件：bearer fail-closed、
    #   速率限制、/mcp 只放行工具呼叫不放行別的路徑 — 同意 開工」；#168）--------------------
    #   ⚠ 上面 (2)(3)(6)(8)(9c) 那幾條 `/mcp` → 404 **全部仍綠、語意未變**：它們打的是
    #     **GET**（harness 不帶 method＝ASGI 殼預設 GET），窄道只認 POST。
    TOK = "tok-" + "x" * 40
    BEAR = ("Bearer " + TOK).encode("latin-1")
    WRONG = b"Bearer tok-wrong"
    JSONH = (b"content-type", b"application/json")
    Board1 = (b"x-board-tunnel", b"1")
    ENVT = {ENV_MCP_TOKEN: TOK}
    RPC_LIST = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    RPC_CALL = (b'{"jsonrpc":"2.0","id":2,"method":"tools/call",'
                b'"params":{"name":"ping","arguments":{}}}')

    async def echo(scope, receive, send):              # 下游假 app：把收到的 body 回吐
        buf = b""
        while True:
            m = await receive()
            buf += m.get("body", b"") or b""
            if not m.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"mcp:" + buf})

    def lane_app(environ=ENVT, open_access=False, rpm=None, clock=None, logs=None):
        return wrap(echo, "saltmarsh", "s3cret", open_access=open_access, environ=environ,
                    mcp_rpm=rpm, clock=clock,
                    log=(logs.append if logs is not None else (lambda _l: None)))

    def post(a, path, hdrs, **kw):
        kw.setdefault("body", RPC_LIST)
        return call(a, path, hdrs, method="POST", **kw)

    # (10a) 純函式
    ck(is_mcp_lane("POST", "/mcp", {"cf-ray": "x"}), "窄道：隧道 POST /mcp → 進窄道")
    ck(is_mcp_lane("post", "/mcp", {"x-board-tunnel": "1"}),
       "窄道：method 大小寫容忍、X-Board-Tunnel 同等")
    for m, pth in (("GET", "/mcp"), ("DELETE", "/mcp"), ("HEAD", "/mcp"), ("POST", "/mcp/"),
                   ("POST", "/mcp/x"), ("POST", "/mcpfoo"), ("POST", "/api/board"),
                   ("POST", "/api/board/list_stations"), ("POST", "/landing/opr/warm")):
        ck(not is_mcp_lane(m, pth, {"cf-ray": "x"}), f"窄道：{m} {pth} **不**進窄道（洞開到最小）")
    ck(not is_mcp_lane("POST", "/mcp", {}), "窄道：非隧道 POST /mcp 不進窄道（本機/LAN 不經此分支）")
    ck(mcp_bearer_verdict({"authorization": "Bearer " + TOK}, TOK) == MCP_OK, "bearer：對 → OK")
    ck(mcp_bearer_verdict({"authorization": "Bearer " + TOK}, "") == MCP_NO_TOKEN
       and mcp_bearer_verdict({}, None) == MCP_NO_TOKEN,
       "bearer：token 未設 → NO_TOKEN（fail-closed）")
    ck(mcp_bearer_verdict({}, TOK) == MCP_UNAUTH, "bearer：沒帶 → UNAUTH")
    ck(mcp_bearer_verdict({"authorization": "Bearer nope"}, TOK) == MCP_UNAUTH, "bearer：錯 → UNAUTH")
    ck(mcp_bearer_verdict({"authorization": "bearer " + TOK}, TOK) == MCP_UNAUTH
       and mcp_bearer_verdict({"authorization": TOK}, TOK) == MCP_UNAUTH,
       "bearer：scheme 必須恰為 `Bearer `（裸 token／小寫 scheme 不收）")
    ck(mcp_bearer_verdict({"authorization": "Bearer 中文ü"}, TOK) == MCP_UNAUTH,
       "bearer：非 ASCII 不 raise（bytes 比對）")
    ck(mcp_bearer_verdict({"authorization": expected_header("saltmarsh", "s3cret")}, TOK) == MCP_UNAUTH,
       "bearer：Basic 隧道帳密**不能**代替 bearer")
    ck(mcp_rate_key({"cf-connecting-ip": "203.0.113.9"}) == "ip:203.0.113.9",
       "限速鍵：cf-connecting-ip")
    _rk = mcp_rate_key({"authorization": "Bearer " + TOK})
    ck(_rk.startswith("tok:") and TOK not in _rk and len(_rk) == 16,
       "限速鍵：無 IP ⇒ token 指紋（sha256 前 12 碼、不含 token 本身）")
    ck(mcp_rpm_from_env({}) == 60 and mcp_rpm_from_env({ENV_MCP_TUNNEL_RPM: "5"}) == 5,
       "限速：預設 60、env 可覆寫")
    ck(all(mcp_rpm_from_env({ENV_MCP_TUNNEL_RPM: b}) == 60 for b in ("0", "-3", "abc", "")),
       "限速：壞值／≤0 ⇒ 回預設 60（壞值不得變成「不限」）")
    ck(mcp_jsonrpc_summary(RPC_LIST) == ("tools/list", "-"), "log 摘要：tools/list")
    ck(mcp_jsonrpc_summary(RPC_CALL) == ("tools/call", "ping"), "log 摘要：tools/call 帶工具名")
    ck(mcp_jsonrpc_summary(b"{not json") == ("?", "-"), "log 摘要：壞 JSON ⇒ method=?（不 raise）")
    ck(mcp_jsonrpc_summary(b"[" + RPC_LIST + b"," + RPC_CALL + b"]")
       == ("tools/list,tools/call", "ping"), "log 摘要：batch 以逗號串")
    _ln = format_mcp_log("1.2.3.4\n[tunnel-gate] fake", 200, "tools/call", "ping;rm -rf")
    ck("\n" not in _ln and ";" not in _ln and " -rf" not in _ln,
       f"log：欄位值過濾（防 log 注入）：{_ln}")

    # (10b) 行為：隧道 ＋ POST /mcp
    A = lane_app()
    st, body, _ = post(A, "/mcp", [Board1, JSONH, (b"authorization", BEAR)])
    ck(st == 200 and body == b"mcp:" + RPC_LIST,
       "★★ 隧道 ＋ POST /mcp ＋ 對 bearer → 放行到下游（body 原樣重放、一個位元不少）")
    st, body, sent = post(A, "/mcp", [Board1, JSONH])
    ck(st == 401 and b"mcp:" not in body and any(
        k == b"www-authenticate" and v.startswith(b"Bearer")
        for m in sent if m["type"] == "http.response.start" for k, v in m["headers"]),
       "★★ 隧道 ＋ 無 bearer → 401 ＋ WWW-Authenticate: Bearer（不是 404：cmms 分得出 token 錯）")
    st, body, _ = post(A, "/mcp", [Board1, JSONH, (b"authorization", WRONG)])
    ck(st == 401 and b"mcp:" not in body, "★★ 隧道 ＋ 錯 bearer → 401")
    st, body, _ = post(A, "/mcp", [CF, (b"authorization", GOOD)])
    ck(st == 401, "★★ 隧道 ＋ POST /mcp ＋ **Basic 帳密** → 401（窄道只認 bearer）")
    st, body, _ = post(lane_app(environ={}), "/mcp", [Board1, JSONH, (b"authorization", BEAR)])
    ck(st == 503 and b"mcp token not configured" in body and b"mcp:" not in body,
       "★★ Board 未設 BOARD_MCP_TOKEN → 503 JSON（fail-closed；與 Worker 的 HTML 503 分得出）")
    st, body, _ = post(lane_app(environ={ENV_MCP_TOKEN: ""}), "/mcp",
                       [Board1, JSONH, (b"authorization", b"Bearer ")])
    ck(st == 503, "★★ token 設成空字串 ⇒ 視同未設 → 503（空 bearer 撞不中空 token）")
    for m in ("GET", "DELETE", "HEAD"):
        st, body, _ = call(A, "/mcp", [Board1, (b"authorization", BEAR)], method=m)
        ck(st == 404 and b"mcp:" not in body, f"★★ 隧道 ＋ {m} /mcp ＋ 對 bearer → **仍 404**")
    for pth in ("/mcp/", "/mcp/x", "/api/board", "/api/board/list_stations", "/landing/opr/warm"):
        st, body, _ = post(A, pth, [Board1, JSONH, (b"authorization", BEAR)])
        ck(st == 404 and b"mcp:" not in body, f"★★ 隧道 ＋ POST {pth} ＋ 對 bearer → **仍 404**")
    ck(BLOCKED_PREFIXES == ("/mcp", "/landing/opr/warm", "/api/board") and is_blocked_path("/mcp"),
       "★★ 接線釘：`/mcp` **仍在** BLOCKED_PREFIXES（窄道是在前面開洞、不是移出清單）")
    ck(decide("/mcp", {"cf-ray": "x", "authorization": exp}, E1)[0] == BLOCK
       and decide("/mcp", {"cf-ray": "x"}, E2, open_access=True)[0] == BLOCK,
       "★★ decide() 一個字沒動：/mcp 在 decide 層仍 BLOCK（窄道只住 ASGI 殼）")

    # (10c) OPEN ACCESS 不鬆窄道
    AO = lane_app(open_access=True)
    st, body, _ = post(AO, "/mcp", [Board1, JSONH])
    ck(st == 401 and b"mcp:" not in body,
       "★★ OPEN ACCESS ＋ 隧道 ＋ POST /mcp ＋ 無 bearer → **仍 401**（開放的是「看」、不是工具面）")
    st, body, _ = post(AO, "/mcp", [Board1, JSONH, (b"authorization", BEAR)])
    ck(st == 200 and body == b"mcp:" + RPC_LIST,
       "★ OPEN ACCESS ＋ 對 bearer → 200（開放模式不多擋也不多放）")
    st, _, _ = call(AO, "/mcp", [Board1], method="GET")
    ck(st == 404, "★ OPEN ACCESS ＋ GET /mcp → 仍 404")

    # (10d) 速率限制：第 61 次 ⇒ 429 ＋ Retry-After；窗滑過去即恢復
    now = [1000.0]
    AR = lane_app(clock=lambda: now[0])
    H = [Board1, (b"cf-connecting-ip", b"198.51.100.7"), JSONH, (b"authorization", BEAR)]
    codes = [post(AR, "/mcp", H)[0] for _ in range(60)]
    ck(codes == [200] * 60, f"★★ 限速：同一 IP 60 次／分鐘內全 200（實得 {set(codes)}）")
    st, body, sent = post(AR, "/mcp", H)
    ra = [v for m in sent if m["type"] == "http.response.start" for k, v in m["headers"]
          if k == b"retry-after"]
    ck(st == 429 and b"mcp:" not in body and bool(ra) and int(ra[0]) >= 1,
       f"★★ 限速：第 61 次 → 429 ＋ Retry-After（{ra}）")
    st, _, _ = post(AR, "/mcp", [Board1, (b"cf-connecting-ip", b"198.51.100.8"), JSONH,
                                 (b"authorization", BEAR)])
    ck(st == 200, "★ 限速：另一個 IP 不受牽連（以 cf-connecting-ip 為鍵）")
    now[0] += 60.5
    st, _, _ = post(AR, "/mcp", H)
    ck(st == 200, "★ 限速：窗滑過 60 秒即恢復（被拒那次不記入窗）")
    AR2 = lane_app(clock=lambda: 5.0)
    HB = [Board1, (b"cf-connecting-ip", b"198.51.100.9"), JSONH]
    bad = [post(AR2, "/mcp", HB + [(b"authorization", WRONG)])[0] for _ in range(70)]
    st, _, _ = post(AR2, "/mcp", HB + [(b"authorization", BEAR)])
    ck(set(bad) == {401} and st == 200,
       "★★ 限速在 bearer **之後**：同一 IP 70 次錯 token（全 401）吃不掉正牌使用者的額度")
    AR3 = lane_app(environ={ENV_MCP_TOKEN: TOK, ENV_MCP_TUNNEL_RPM: "2"}, clock=lambda: 1.0)
    ck([post(AR3, "/mcp", H)[0] for _ in range(3)] == [200, 200, 429],
       "限速：env `BOARD_MCP_TUNNEL_RPM=2` 接得上 wrap()（第 3 次 429）")
    _lim = SlidingWindowLimiter(2, 60.0, clock=lambda: 0.0)
    ck([_lim.hit("k")[0] for _ in range(3)] == [True, True, False], "限速器：上限 2 → 第 3 次拒")
    _lim2 = SlidingWindowLimiter(1, 60.0, clock=lambda: 0.0, max_keys=8)
    for i in range(50):
        _lim2.hit(f"k{i}")
    ck(len(_lim2._hits) <= 8, f"限速器：鍵數有界（實得 {len(_lim2._hits)}）")

    # (10e) log：每筆一行、token 永不印、method／工具名
    logs = []
    A = lane_app(logs=logs)
    post(A, "/mcp", [Board1, (b"cf-connecting-ip", b"203.0.113.9"), JSONH,
                     (b"authorization", BEAR)], body=RPC_CALL)
    post(A, "/mcp", [Board1, JSONH, (b"authorization", WRONG)])
    post(lane_app(environ={}, logs=logs), "/mcp", [Board1, JSONH])
    post(A, "/mcp", [Board1, (b"content-type", b"text/plain"), (b"authorization", BEAR)])
    ck(len(logs) == 4 and all(l.startswith("[tunnel-gate] mcp ") for l in logs),
       f"★★ log：每筆一行、前綴 `[tunnel-gate] mcp`（{len(logs)} 行）")
    ck(bool(logs) and logs[0]
       == "[tunnel-gate] mcp ip=203.0.113.9 status=200 method=tools/call tool=ping",
       f"★★ log：ip／status／method／工具名（{logs[:1]}）")
    ck(len(logs) == 4 and "status=401 method=tools/list" in logs[1] and "status=503" in logs[2],
       "★ log：401／503 也記、method 照樣解析")
    ck(len(logs) == 4 and "method=? " in logs[3] and "status=200" in logs[3],
       "★ log：非 JSON Content-Type ⇒ 不 peek、method=?、照常放行")
    ck(not any(TOK in l or "tok-wrong" in l or "Bearer" in l for l in logs),
       "★★ log：token（對的、錯的）與 scheme **永不印**")
    logs2 = []
    st, body, _ = post(lane_app(logs=logs2), "/mcp", [Board1, JSONH, (b"authorization", BEAR)],
                       chunks=[RPC_CALL[:10], RPC_CALL[10:30], RPC_CALL[30:]])
    ck(st == 200 and body == b"mcp:" + RPC_CALL and bool(logs2) and "tool=ping" in logs2[0],
       "★ peek：多段 body 讀完照樣重放、下游收到完整 body、log 解析得出")
    big = (b'{"jsonrpc":"2.0","id":1,"method":"tools/list","pad":"'
           + b"a" * (MCP_PEEK_MAX + 10) + b'"}')
    logs3 = []
    st, body, _ = post(lane_app(logs=logs3), "/mcp", [Board1, JSONH, (b"authorization", BEAR)],
                       chunks=[big[:40000], big[40000:]])
    ck(st == 200 and body == b"mcp:" + big and bool(logs3) and "method=?" in logs3[0],
       "★ peek：> 64KB ⇒ 停 peek、method=?、下游仍收到完整 body（不改放行結果）")
    logs4 = []
    post(lane_app(logs=logs4), "/mcp", [Board1, JSONH, (b"content-length", b"999999"),
                                        (b"authorization", BEAR)])
    ck(bool(logs4) and "method=?" in logs4[0], "★ peek：Content-Length > 64KB ⇒ 根本不讀、method=?")

    # (10f) 本機／LAN 一個位元不變：無隧道標頭 ⇒ 不經窄道、不 log、原樣到下游
    logs5 = []
    AL = lane_app(environ={}, logs=logs5)                 # 連 token 都沒設
    for m in ("POST", "GET", "DELETE"):
        st, body, _ = call(AL, "/mcp", [JSONH], method=m, body=RPC_LIST if m == "POST" else b"")
        ck(st == 200 and body.startswith(b"mcp:"),
           f"★★ LAN 直連 {m} /mcp（無隧道標頭、無 bearer、token 未設）→ 原樣到下游（閘不插手）")
    ck(logs5 == [], "★★ LAN 直連不產生任何窄道 log（不經此分支）")
    ck(call(app, "/mcp", [])[0] == 200, "★ 回歸：舊 app 非隧道 /mcp 仍 200（(5) 組同形）")
    ck(ENV_MCP_TOKEN in __doc__ and ENV_MCP_TUNNEL_RPM in __doc__,
       "窄道：兩個 env 名寫在 docstring（下一個人找得到）")

    print("-" * 60)
    print(f"board_basicauth selftest: "
          f"{'ALL PASS' if not n_fail[0] else str(n_fail[0]) + ' FAIL'}")
    return 1 if n_fail[0] else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
