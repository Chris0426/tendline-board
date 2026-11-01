# -*- coding: utf-8 -*-
"""
board_w8_transport.py — W8 停機融合契約「傳輸層殼」（cmms HTTP 讀 API client）
===============================================================================

定位（GOVERNANCE §3.2.2 consumer-stub-first 的**傳輸半邊**）：
  純語意核心 `board_w8.py`（93→ 斷言 selftest、CLOSED）已把 W8 契約寫成可測純函式。
  本檔補**唯一缺口＝傳輸層**：cmms A3 HTTP 讀 API（`list_work_orders_active_in`）client，
  帶 static bearer（CM-3）。cmms 端點上線那天「只插線」——設好兩個環境變數即可通，
  凍結核心 `board_w8.py` 一行不改（本檔只 import 其 normalize/window 純函式）。

  ⚠ 本檔**不接進 app.py / 儀表板**（go-live gate 之後、lead 排程）；自足、可 --selftest。

契約來源（唯一真相）：
  - **A3 規格**＝CONCEPT §19.7「A3 規格（CM-2）」＋ ADR 表 A3 列（HTTP JSON + static bearer；
    MCP 留 agent 面 Phase 2）。
  - **W8 語意**＝CONCEPT §19.3 A8/W8（work_type→§23 態、活躍窗、遷移 caveat、治理白名單）＝
    全在 `board_w8.py`；本檔只負責「把 bytes 變成過 normalize 的 WO 列表」。
  - **fixture**＝`_fixtures/w8.workorder.json`（Board 自製 golden/invalid）＋
    `_fixtures/contract_wo_active_in.v1.json`/`contract_wo_detail.v1.json`
    （**cmms 真 golden 位元拷貝**，2026-07-05 A3 對版交付＝wire 形唯一真相）。

A3 兩式（Board 兩式都要能吃；cmms 擇一實作）：
  ① **一次式（inline）**：列表端點直接回每 WO 含 inline `status_history`（全欄位）。
     → 直接逐筆過 `normalize_wo`。
  ② **退階式（list + detail）**：列表端點只回輕量列（7 欄、無 inline history），
     Board 對「窗內相交子集」逐筆打 `/detail` 補齊 status_history。
     → **有界 N+1**：硬 cap（`detail_cap`，預設 200）；超過就 `detail_truncated=True`＋
        **誠實少讀**（cap 外的 WO 不補 detail、**不納入結果**——避免用 opened_at→closed_at
        列層回退偽裝成「遷移單」污染 `migration_fallback_rate` 訊號，FP-6）。**絕不無界 N+1。**
  `mode="auto"`（預設）：看列表列有無 `status_history` 鍵自動判 ①/②。

★★ 診斷指紋：**`unreachable` 幾乎不是 cmms 掛掉**（2026-08-18 兩端對證存證）★★
  查 `unreachable` 之前先看這三條，可以省掉一整輪誤判（我方那天連猜錯兩次）：
    ① **計時行的秒數**：`attempt=0 **0.00s** -> unreachable` ＝連 TCP handshake 都沒發生
       ＝DNS 解析失敗／無路由。**服務端掛掉不會長這樣**——cmms 在 Fly 上，app 死掉時
       邊緣仍回 502/503 ⇒ 我方會分類成 `http_502`。**看到 0.00s 就往本機網路查。**
    ② **同一時刻 MES 有沒有一起死**：`08001 Named Pipes Provider` / `08S01 TCP Provider`
       出現在 `/landing`／`/dispatch`／`/updown` ⇒ 公網 HTTPS 與內網 SQL **兩個獨立目的地
       同時失聯**，共同因子只能在用戶端。（`08001` 退具名管道＝主機名解析失敗的典型。）
    ③ **Windows 事件**：`WLAN-AutoConfig 4003 "limited connectivity"`＋
       `Kernel-Power 172 "Connectivity state in standby: Disconnected"`＝已連上 AP 但無 DNS。
  2026-08-18 實例：這三條全中，cmms 端事後查證**四項全無**，且**同一時間邊緣正常服務他方
  流量**（積極證據）⇒ 封包沒離開我方。全文與 cmms 回覆＝`internal-relay-note`。
  ⇒ 這也是 **#24 stale-serve** 的由來：那 45 分鐘裡工單事實一分鐘前才讀到過、卻被丟掉。

錯誤模型（**任何錯誤不外拋**、GOVERNANCE §3.3「失敗＝該卡 unavailable」）：
  一律回 `(None, reason)`，reason ∈：
    not_configured   — 缺 BOARD_CMMS_BASE_URL / BOARD_CMMS_TOKEN（disabled、不炸不轟炸）
    unreachable      — 連不上（DNS/拒連/網路）**先讀上面的診斷指紋**
    timeout          — 逾時（帶 timeout、單次重試上限 1）
    http_<code>      — HTTP 非 2xx（如 http_500 / http_401）；伺服器明確回應、不重試
    bad_schema       — envelope 結構 pin 不符（QG-3.3 拒讀，不冒險解讀；schema_id 非 in-band）
    bad_json         — 回應非合法 JSON
  A11 兩支另有四個**專屬 reason**（語意各自不同，呼叫端據此顯不同的話）：
    missing_effective_date — 事件流列缺 `effective_date`＝cmms 尚未部署 NOTE-31 補欄
    bad_as_of / empty_eids — 批次讀的**呼叫端參數錯**（皆零網路短路，不是 cmms 的問題）
    incomplete_scope       — 批次讀違反「scope 內每台恆回一筆」＝cmms 行為回歸，非形狀破損
  成功回 `(result, None)`，result＝統一形狀（見 `fetch_active_work_orders` docstring）。
  **coverage 誠實**（QG-3.5）：`truncated` / `detail_truncated` / `detail_errors` 一路帶到回傳，
  消費端好顯示「這次少讀了」。

live 鐵則：
  - 每次 HTTP 呼叫**強制 timeout**＋**計時 print**（stderr，不污染 stdout）；token 絕不入 log。
  - **禁「逾時→fallback 再打重查詢」**（FP-8）：單筆 detail 失敗＝該 WO 少讀（計入 detail_errors、
    coverage 誠實），不重打；列表層失敗＝整批 (None, reason)。單次重試上限 1、僅對 transient
    （timeout/unreachable）、不對 HTTPError/bad_json。

沉默點（見檔尾清單）：S13/S14/S16 **已對版 CLOSED（2026-07-05 A3 relay v2）**——
  真路徑 `/work-orders/active-in`、envelope `{items,truncated}`、detail 平鋪、schema 契約級。

用法：
  python board_w8_transport.py --selftest   # 離線零網路自驗（注入 fake transport + fixture）
  （A3 已部署 prod：設 BOARD_CMMS_BASE_URL=https://work.example.com +
   BOARD_CMMS_TOKEN 即插即用。live 拉取由 Chris 在域內機器跑、Claude 不連。）

樣板：consume 紀律照 remote-diag `adapters/board_labels.py`；純 Python 標準庫、零新依賴。
"""
import os
import sys
import json
import time
import socket
import argparse
import urllib.parse
import urllib.error
import urllib.request

import board_w8 as w8   # 凍結核心（純函式；本檔只 import、不改）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 設定常數
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 10.0     # 秒；每次 HTTP 呼叫強制帶
DETAIL_CAP = 200           # 退階式 /detail 有界 N+1 硬上限（超過＝誠實少讀 + truncated）
TOTAL_BUDGET_SEC = 60.0    # 整體 wall-clock 預算（退階式最壞 200×2×10s≈67min → 收口到 1 分鐘；
                           # 超時＝停止 detail、detail_truncated=True 誠實少讀，非炸整批）
MAX_RETRIES = 1            # 單次重試上限（僅 transient：timeout/unreachable）

REASONS = ("not_configured", "unreachable", "timeout",
           "http_<code>", "bad_schema", "bad_json")   # 文件用；http_<code> 為前綴族

# schema_id（S7/S16 CLOSED，2026-07-05 A3 對版）：**契約級識別、非 in-band**——
# 真回應 body 無 schema_id 欄（LIST={items,truncated}、detail=平鋪 WO）。形狀 pin 由
# golden fixture（_fixtures/contract_wo_*.v1.json，位元拷貝自 cmms repo）＋cmms 端
# 契約測試（形變即紅＝機械 push）承擔；破壞性變更必 bump v2。
LIST_SCHEMA_ID = "contract_wo_active_in.v1"
DETAIL_SCHEMA_ID = "contract_wo_detail.v1"

# A3 端點路徑（S13/S14 CLOSED，2026-07-05 對版＝prod 部署 + cmms work_orders.py 路由實讀）
_LIST_PATH = "/work-orders/active-in"
_DETAIL_PATH_TMPL = "/work-orders/{wno}/detail"


# ---------------------------------------------------------------------------
# 設定讀取（env 驅動；缺任一＝disabled）
# ---------------------------------------------------------------------------

def _config():
    """回 (base_url, token) 或 None（缺任一環境變數＝disabled）。token 絕不入 log。"""
    base = os.environ.get("BOARD_CMMS_BASE_URL", "").strip()
    token = os.environ.get("BOARD_CMMS_TOKEN", "").strip()
    if not base or not token:
        return None
    return (base.rstrip("/"), token)


def is_configured():
    return _config() is not None


# ---------------------------------------------------------------------------
# HTTP 基元（stdlib urllib；強制 timeout + 計時 print + 有界重試）
# ---------------------------------------------------------------------------

def _http_get_json(url, token, timeout, retries=MAX_RETRIES):
    """GET url（Bearer token）→ (payload, None) 或 (None, reason)。
    - 強制 timeout；每次呼叫計時 print 到 stderr（token 不入 log）。
    - HTTPError→http_<code>（不重試）；bad JSON→bad_json（不重試）；
      timeout/unreachable→重試上限 retries。"""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",     # header 帶 token；URL 不帶
        "Accept": "application/json",
    })
    last = None
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        outcome = "?"
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
                outcome = "ok"
                return (payload, None)
            except (ValueError, UnicodeDecodeError):
                outcome = "bad_json"
                return (None, "bad_json")          # 伺服器已回應、不重試
        except urllib.error.HTTPError as e:
            outcome = f"http_{e.code}"
            return (None, f"http_{e.code}")        # 明確回應、不重試
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                last, outcome = "timeout", "timeout"
            else:
                last, outcome = "unreachable", "unreachable"
        except (socket.timeout, TimeoutError):
            last, outcome = "timeout", "timeout"
        except OSError:
            last, outcome = "unreachable", "unreachable"
        finally:
            dt = time.monotonic() - t0
            # 計時 print（live 鐵則）——URL path 只印路徑段、不印 query（避免 asset_id 噪音）、不印 token
            path = urllib.parse.urlsplit(url).path
            print(f"[w8_transport] GET {path} attempt={attempt} {dt:.2f}s -> {outcome}",
                  file=sys.stderr)
    return (None, last or "unreachable")


# ---------------------------------------------------------------------------
# URL builder（A3 假設路徑；沉默點——接通前只改這兩處）
# ---------------------------------------------------------------------------

def _list_url(base, start, end, asset_id):
    q = {"start": start, "end": end}
    if asset_id:
        q["asset_id"] = asset_id
    return f"{base}{_LIST_PATH}?" + urllib.parse.urlencode(q)


def _detail_url(base, wno):
    return f"{base}{_DETAIL_PATH_TMPL.format(wno=urllib.parse.quote(str(wno)))}"


# ---------------------------------------------------------------------------
# envelope 解析（schema_id 驗證＝QG-3.3；形狀錯＝bad_schema）
# ---------------------------------------------------------------------------

def _parse_list_envelope(payload):
    """列表回應 → (rows, truncated, schema_id, reason)。
    真形狀（S13 CLOSED、golden 鎖形）＝`{items:[...], truncated:bool}`；schema_id 非 in-band
    → 結構 pin＝items(list)+truncated 兩鍵齊備，schema_id 回傳 pin 常數（供下游標記）。"""
    if not isinstance(payload, dict):
        return (None, None, None, "bad_schema")
    rows = payload.get("items")
    if not isinstance(rows, list) or "truncated" not in payload:
        return (None, None, None, "bad_schema")     # 形不符＝拒讀、不冒險解讀
    return (rows, bool(payload.get("truncated", False)), LIST_SCHEMA_ID, None)


def _parse_detail_envelope(payload):
    """detail 回應 → (wo_dict, reason)。真形狀（S14 CLOSED、golden 鎖形）＝**平鋪 WO 物件**
    （無包裹、無 in-band schema_id；contract_wo_detail.v1）。結構 pin＝必含 work_order_no。"""
    if not isinstance(payload, dict) or "work_order_no" not in payload:
        return (None, "bad_schema")
    return (payload, None)


# ---------------------------------------------------------------------------
# 主入口：fetch_active_work_orders（一次式 / 退階式，統一 normalize 後回傳）
# ---------------------------------------------------------------------------

def fetch_active_work_orders(start, end, asset_id=None, *, mode="auto",
                             timeout=DEFAULT_TIMEOUT, detail_cap=DETAIL_CAP,
                             total_budget_sec=TOTAL_BUDGET_SEC, transport=None):
    """查窗內活躍 WO（A3）→ 統一 normalize 後形狀，或 (None, reason)。

    參數：
      start, end : naive 'YYYY-MM-DD HH:MM:SS'（時鐘 pin 同 W8 S10；本層原樣傳給 cmms、
                   不改寫；A3 窗語意＝「活躍於窗內」由 **cmms 伺服器端**過濾）。
      asset_id   : 可選 EID 過濾（A3）。
      mode       : "auto"（預設，看列有無 status_history 判 inline/detail）｜"inline"｜"detail"。
      timeout    : 每次 HTTP 呼叫秒數。
      detail_cap : 退階式 /detail 有界 N+1 硬上限。
      total_budget_sec : 整體 wall-clock 預算（含列表+全部 detail）；超過即停止後續
                   detail、detail_truncated=True 誠實少讀（防 cmms 半死時單次呼叫
                   拖到數十分鐘＝perf-saga 阻塞型態的 HTTP 版）。None＝不設限。
      transport  : 可注入 callable `(url)->(payload, reason)`（供離線測試）；
                   None＝走真 HTTP（讀 env 設定；缺設定回 (None,"not_configured")）。

    回傳成功＝(result, None)，result＝dict：
      {
        "schema_id":       <列表 envelope 的 schema_id>,
        "work_orders":     [normalize_wo() 輸出, ...],   # 已過凍結核心 normalize、可直餵 classify_gaps
        "mode":            "list_inline" | "list_plus_detail",
        "truncated":       bool,    # 總覽旗標＝list_truncated or detail_truncated
        "list_truncated":  bool,    # cmms 列表端點自報分頁截斷
        "detail_truncated":bool,    # 退階式超過 detail_cap＝誠實少讀
        "detail_errors":   int,     # 退階式個別 /detail 失敗筆數（該 WO 少讀、不炸整批）
        "count":           int,     # work_orders 長度
      }
    回傳失敗＝(None, reason)，reason ∈ REASONS。
    """
    # --- transport 決定（None＝真 HTTP、需設定；否則用注入的 fake）---
    if transport is None:
        cfg = _config()
        if cfg is None:
            return (None, "not_configured")          # disabled：不炸、不警報轟炸
        base, token = cfg
        transport = lambda url: _http_get_json(url, token, timeout)
    else:
        base = "http://work.example.com"                   # 注入測試：URL 僅供 fake transport 比對

    # --- 1) 列表查詢（A3：伺服器端已按窗過濾、只回相交 WO）---
    _t0 = time.monotonic()                           # 整體預算起點（列表+detail 全含）
    payload, reason = transport(_list_url(base, start, end, asset_id))
    if reason is not None:
        return (None, reason)
    rows, list_trunc, sid, reason = _parse_list_envelope(payload)
    if reason is not None:
        return (None, reason)

    # --- 2) 判 inline vs 退階 ---
    dict_rows = [r for r in rows if isinstance(r, dict)]
    have_inline = bool(dict_rows) and all("status_history" in r for r in dict_rows)
    use_detail = (mode == "detail") or (mode == "auto" and rows and not have_inline)
    if mode == "inline":
        use_detail = False

    normalized = []
    detail_trunc = False
    detail_errs = 0

    if use_detail:
        # 退階式：對「窗內相交子集」（＝列表回的全部列，A3 已窗過濾）逐筆打 /detail、有界 cap
        capped = rows[:detail_cap]
        detail_trunc = len(rows) > detail_cap        # 超過＝誠實少讀（cap 外不補、不納入）
        for r in capped:
            if total_budget_sec is not None and (time.monotonic() - _t0) > total_budget_sec:
                detail_trunc = True                   # 整體預算用盡 → 停止、誠實少讀（不炸整批）
                break
            wno = r.get("work_order_no") if isinstance(r, dict) else None
            if wno is None:
                continue                              # 列缺 PK → 無從打 detail、跳過
            dpayload, dreason = transport(_detail_url(base, wno))
            if dreason is not None:
                detail_errs += 1                      # FP-8：個別失敗不重打、計入 coverage
                continue
            wo, dreason = _parse_detail_envelope(dpayload)
            if dreason is not None:
                detail_errs += 1
                continue
            n = w8.normalize_wo(wo)                    # 過凍結核心 normalize（壞資料合法回 None）
            if n is not None:
                normalized.append(n)
    else:
        for r in rows:
            n = w8.normalize_wo(r)
            if n is not None:
                normalized.append(n)

    return ({
        "schema_id": sid,
        "work_orders": normalized,
        "mode": "list_plus_detail" if use_detail else "list_inline",
        "truncated": bool(list_trunc or detail_trunc),
        "list_truncated": bool(list_trunc),
        "detail_truncated": bool(detail_trunc),
        "detail_errors": detail_errs,
        "count": len(normalized),
    }, None)


# ===========================================================================
# PM 板三路源（SPEC-PM-BOARD §4／§8.1；**additive——上面既有函式一個位元不動**）
# ===========================================================================
# 三條路（cmms 2026-08-04 CM1–CM11 回覆定案）：
#   ① `/pm-schedules`  ＝**主源**（backlog／owner 負載／預警／標工／寬限／suppress）；
#      整批排程列一頁掃完、同一把 bearer（CM2/CM6/CM7）。
#   ② `/work-orders?work_type=PM&<開單日窗>&limit=1000&offset=…` ＝完成度母體與單況（CM1）。
#      ⚠ LIST **無到期日窗**（CM1）⇒ 這裡只能用**開單日**超抓，再由消費端（board_data →
#        board_pm.score）**按到期日過濾**＝C-P1 due-based 的唯一合法作法。
#      ★ wire ＝**裸 list ＋ limit/offset 翻頁**（S-PM8，2026-08-10 實測）——**不是** active-in
#        的信封形；不帶 limit＝伺服端預設 100＝靜默截斷 10 倍。
#   ③ `/tasks/{task_no}/steps`（S2）＝展開卡的模板細項；`/work-orders/{no}/detail`＝單況腿。
#   ④ `/tasks?limit=1000`（S-PM4 定案）＝**task_no → 人話名稱字典**（441 筆一頁抓完、同把
#      bearer）。名稱層 **fail-open**：抓不到就顯代號，**絕不因字典失敗降級整板**。
#   ⑤ `/inventory-items?limit=1000&offset=…`（S-PM6 定案、2026-08-05 五輪）＝**item_code →
#      零件人話名字典**（展開卡「用料」欄用）。**列數超過單頁上限 ⇒ 兩頁取完**；同族 fail-open。
#
# 沉默點（**2026-08-04 cmms A9 第四輪回覆＝S-PM1/2/3/4 全部 CLOSED**；存證＝SPEC §8.4）：
#   S-PM1 **CLOSED**：`/pm-schedules` wire ＝**裸 JSON list**（無信封、無 `truncated`）。
#         `limit` 預設 100、上限 1000 ⇒ **呼叫必帶 `limit=1000`**（prod 現量在單頁上限內；不帶就被
#         預設 100 靜默截斷＝把 backlog 講成乾淨）。破千時 cmms 另 relay offset 翻頁。
#         ⚠ fixture 的 `{_meta,total_rows_in_prod,rows}` ＝**交檔手做包裝、不是 wire 形**——
#         讀 fixture 的路徑要自己剝 `rows`，本層只收 wire 形（兩者分清楚、勿互相冒充）。
#   S-PM2 **CLOSED**：日期窗參數名＝`opened_on_or_after`/`opened_on_or_before`
#         （**兩者含端、date-only、過濾軸＝`opened_date`**）。舊的暫 pin `opened_from`/
#         `opened_to` 已廢、負斷言釘死不得復活。
#   S-PM3 **CLOSED**：`/tasks/{task_no}/steps` 形狀＝實 fixture 兩支已到位
#         （`pm_board_fixture_task_steps_{YPHYM,CAL1DM}.v1.json`，prod v87 真回應）。
#   S-PM4 **CLOSED（不必新切片）**：`GET /tasks/{task_no}` 回 `{task_no, description,
#         is_active}`、`task_id` 的值即 `task_no`、`description` 就是人話名；一次抓全量走
#         `GET /tasks?limit=1000`。⚠ **tasks 的 `is_active` 只是字典屬性**——殭屍判準仍是
#         `/pm-schedules` 側的旗標，兩者**不得混用**（故本層回傳雖帶此欄，`board_data`
#         只取 `task_no→description` 進板＝結構上防呆）。
#   S-PM5（殘餘、且**無風險**）：`/tasks` LIST 的 envelope 未經文字對版（四輪回覆只講
#         「一次抓全量 441 筆」）。同科端點兩支都是裸 list ⇒ 本層**雙收**裸 list ∨
#         `{items[,truncated]}`；猜錯只會 `bad_schema` → 名稱層 fail-open 顯代號、板不受影響。
#   S-PM8 **CLOSED-empirical**（2026-08-10 PM live 首開日實測；**本檔今天唯一的修法來源**）：
#         `/work-orders` LIST 的 wire ＝**裸 JSON list**（30d 窗回 81 列、每列 24 欄含
#         `work_order_no`），**不是** `/work-orders/active-in` 的 `{items, truncated}` 信封形。
#         原本重用 `_parse_list_envelope` ⇒ 整條 WO 腿恆 `bad_schema`。
#         limit 語意同族：**預設 100**（365d PM 窗真量接近但低於單頁上限、不帶 limit 只回 100）、
#         `limit=1000` 被尊重、`offset` 被尊重（`offset=1` 位移一列、`offset=1000` 回 0）。
#         ⇒ 修法＝雙收（信封留容錯、不翻頁）＋每頁帶 `limit=1000`＋offset 翻頁＋跨頁去重。
#         ⚠ **殘餘（尚無 cmms 文字 pin）**：limit 上界、以及翻頁時的**排序穩定性**。排序不穩
#         ⇒ 跨頁去重可容忍重複、**但防不了漏列**（已列入 relay 待對版；存證＝SPEC §4.1）。
#
# ★ 三條鐵則沿用上半檔：env 缺任一 ⇒ `(None,"not_configured")`（disabled、不炸不轟炸）｜
#   每次呼叫強制 timeout＋stderr 計時 print（token 絕不入 log）｜錯誤族＝同一張 REASONS 表。
# ★ **正規化不在本層**：列一律回 raw dict，欄位語意（14 欄正規化、四態判定）全在
#   `board_pm.parse_sched`／`classify_wo`＝一份規則一個落點（本檔只做「形狀 pin＋拒讀」）。
# ---------------------------------------------------------------------------

_PM_SCHED_PATH = "/pm-schedules"                  # CM2/CM6/CM7；同把 bearer
# ★ S-PM1 定案：伺服端 `limit` **預設 100、上限 1000**。prod 現量在 1000 內 ⇒ 必須顯式帶 1000，
#   否則靜默只拿到前 100 條排程＝backlog／owner／預警全部建在 1/10 的主源上（而頁面看起來
#   完全正常）。這個常數是承重的，改小＝謊報。
_PM_SCHED_LIMIT = 1000
_PM_WO_PATH = "/work-orders"                      # CM1
_PM_WO_Q_TYPE = "work_type"
# ★ S-PM2 定案（cmms A9 四輪）：**含端、date-only、過濾軸＝`opened_date`**。
#   舊暫 pin `opened_from`/`opened_to` 已廢（selftest 負斷言釘死不得復活）。
_PM_WO_Q_FROM = "opened_on_or_after"
_PM_WO_Q_TO = "opened_on_or_before"
# ★ S-PM8 CLOSED-empirical（2026-08-10 PM live 首開日實測；存證＝SPEC §4.1）：
#   `/work-orders` 的 wire ＝**裸 JSON list**（與 `/pm-schedules`、`/tasks` 同族），**不是**
#   `/work-orders/active-in` 的 `{items, truncated}` 信封形——原本重用 `_parse_list_envelope`
#   ⇒ 整條 WO 腿 `bad_schema`（完成度／趨勢／個人表全部降級）。
#   伺服端 limit 語意亦同族：**預設 100**（365d PM 窗真量接近但低於單頁上限、不帶 limit 只回 100）、
#   `limit=1000` 被尊重、`offset` 被尊重。⇒ 這兩個常數都是承重的：
#   `_PM_WO_LIMIT` 改小＝把最舊的月份講成「那時候沒有保養到期」（謊報，§11 ⑥ 的鏡像）。
#   ★ 2026-08-10 cmms NOTE-33 §六 **文字 pin（兩個都是既成行為、不需新決策）**：
#     ① `limit` 是 `Query(default=100, ge=1, le=1000)` ⇒ **>1000 回 422、不 clamp**
#        （與批次讀「>200 且給 eids → 422」同一條紀律：cmms 不靜默改小消費端要的量）
#        ⇒ **1000 是伺服端硬上限，本常數不得調大**；量長過 1000 的正解是翻頁（已做）
#        或請 cmms 加 keyset cursor（見下方 docstring「量再長怎麼辦」）。
#     ② 排序＝`ORDER BY work_order_no DESC`，且 `work_order_no` 是 **PK**
#        ⇒ 唯一、全序、跨頁決定性，**不需要 tie-break 欄**。
_PM_WO_LIMIT = 1000
_PM_WO_MAX_PAGES = 5                              # 涵蓋 5,000 列；365d 現量在單頁內 ⇒ 正常 1 頁
_PM_STEPS_PATH_TMPL = "/tasks/{task_no}/steps"    # S2（S-PM3 CLOSED：兩支真 fixture 到位）
_PM_TASKS_PATH = "/tasks"                         # S-PM4 CLOSED：人話名稱字典
_PM_TASKS_LIMIT = 1000                            # prod 441 筆 ⇒ 一頁抓完
# ★ S-PM6 CLOSED（cmms A9 五輪，2026-08-05）：零件人話名字典。
#   prod 現 **列數 > limit 上限 1000** ⇒ **必須翻頁**（offset=0 與 offset=1000 兩次）；
#   只打一頁＝靜默少一批名字，而頁面看起來完全正常（那些零件只是「剛好」顯代號）。
_PM_INV_PATH = "/inventory-items"
_PM_INV_LIMIT = 1000
_PM_INV_MAX_PAGES = 2                             # 兩頁涵蓋 2,000 列；回滿仍自揭 truncated

PM_SCHED_SCHEMA_ID = "pm_schedules.v1"            # 契約級識別、非 in-band（同 LIST/DETAIL 紀律）
PM_WO_SCHEMA_ID = "pm_work_orders.v1"
PM_TASKS_SCHEMA_ID = "pm_tasks.v1"
PM_INV_SCHEMA_ID = "pm_inventory_items.v1"


def _pm_transport(transport, timeout):
    """回 `(transport_callable, base, None)` 或 `(None, None, "not_configured")`。
       注入 fake 時 base 只供 fake 比對 URL（與 `fetch_active_work_orders` 同款）。"""
    if transport is not None:
        return (transport, "http://work.example.com", None)
    cfg = _config()
    if cfg is None:
        return (None, None, "not_configured")     # disabled：零網路短路
    base, token = cfg
    return ((lambda url: _http_get_json(url, token, timeout)), base, None)


def _pm_day(v):
    """日期參數 → 'YYYY-MM-DD'（收 date/datetime/str；str 只取前 10 碼、不臆測其他格式）。"""
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()[:10]


def _pm_dual_payload(payload):
    """裸 `list` → (rows, False)；`{items:list[,truncated]}` → (rows, truncated)；
       其餘 → (None, None)＝bad_schema（QG-3.3 拒讀、不冒險解讀）。

    ⚠ **只給 envelope 未對版／需要容錯的端點用**（`/tasks`＝S-PM5 未對版且該腿 fail-open；
      `/work-orders`＝S-PM8 已實測定形為裸 list，信封形留作容錯路徑、**且不對它翻頁**）。
      已對版且無容錯需求的端點（`/pm-schedules`／`/inventory-items`）一律收斂成單一形——
      雙收會讓「形狀變了」這件事永遠照不出來。"""
    if isinstance(payload, list):
        return (payload, False)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return (payload["items"], bool(payload.get("truncated", False)))
    return (None, None)


def fetch_pm_schedules(*, limit=_PM_SCHED_LIMIT, timeout=DEFAULT_TIMEOUT, transport=None):
    """PM 排程母版 LIST（`GET {base}/pm-schedules?limit=N`）→ (result, None) | (None, reason)。

    result＝`{"schema_id": PM_SCHED_SCHEMA_ID, "rows": [raw dict…], "truncated": bool,
              "dropped_n": int, "count": int}`
      · ★ **wire ＝裸 JSON list（S-PM1 CLOSED，2026-08-04 cmms 定案）**——無信封、無
        `truncated` 欄。非 list ⇒ `bad_schema` 誠實降級（**不再雙收 `{items,truncated}`**：
        對版後還留著另一種形，等於永遠照不出「cmms 改了形」這件事）。
        ⚠ fixture 檔的 `{_meta,total_rows_in_prod,rows}` 是**交檔手做包裝、不是 wire 形**，
        剝殼在讀 fixture 的那一端做（selftest／`_fixtures` loader），不在本層。
      · `rows` **原樣 raw**（正規化在 `board_pm.parse_sched`＝一份規則）。
      · 列結構 pin＝dict 且 `pm_id` 非空；不符者丟棄並計 `dropped_n`（**壞列不污染主源、
        也不整批拒讀**——整批列裡混一筆髒資料不該讓整頁黑掉；丟幾筆頁上可揭露）。
      · **值不可當基準**（§8.2 Q-CM9）：8/9 eMaint 重灌後 next_due/last_* 會重置。
      · ★ **`limit_hit` 自揭**（2026-08-04 verify LOW）：裸 `list` 形**沒有** `truncated` 欄
        ⇒ 伺服端若按 limit 截斷、本層原本恆回 `truncated=False`＝頁面把**被截斷的主源**
        講成「backlog 乾淨」（§5 反模式），而 `count=1000` 看起來完全正常、沒人會發現。
        故：**回列數 ≥ limit ⇒ `truncated=True` ＋ `limit_hit=True`**（保守側自揭；
        剛好等於 limit 而其實沒被截斷時只是多講一句，比漏講安全）。**S-PM1 定案後這條
        判準從「暫時性」升格為唯一的截斷偵測手段**——裸 list 家族沒有別的訊號可用。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    url = f"{base}{_PM_SCHED_PATH}?" + urllib.parse.urlencode({"limit": int(limit)})
    payload, reason = tr(url)
    if reason is not None:
        return (None, reason)
    if not isinstance(payload, list):                 # S-PM1：只收裸 list
        return (None, "bad_schema")
    rows, trunc = payload, False
    kept, dropped = [], 0
    for r in rows:
        if isinstance(r, dict) and str(r.get("pm_id") or "").strip():
            kept.append(r)
        else:
            dropped += 1
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 0
    limit_hit = bool(lim) and len(rows) >= lim      # 回滿 limit＝可能被截斷（保守自揭）
    return ({"schema_id": PM_SCHED_SCHEMA_ID, "rows": kept,
             "truncated": bool(trunc) or limit_hit, "limit_hit": limit_hit,
             "dropped_n": dropped, "count": len(kept)}, None)


def fetch_pm_work_orders(opened_from, opened_to, *, work_type="PM", limit=_PM_WO_LIMIT,
                         timeout=DEFAULT_TIMEOUT, transport=None):
    """PM 工單 LIST（`GET {base}/work-orders?work_type=PM
       &opened_on_or_after=&opened_on_or_before=&limit=&offset=`）
       → (result, None) | (None, reason)。

    ★ S-PM2 CLOSED（cmms A9 四輪）：兩個日期參數**皆含端、date-only、過濾軸＝`opened_date`**。
    ★ S-PM8 CLOSED-empirical（2026-08-10 live 實測，PM live 閘首開日）：**wire ＝裸 JSON list**
      ——這支與 `/pm-schedules`、`/tasks` 同族，**不是** `/work-orders/active-in` 的
      `{items, truncated}` 信封形。原本重用 `_parse_list_envelope` ⇒ 整條 WO 腿恆 `bad_schema`
      ＝完成度／12 個月趨勢／個人表全部降級（而 backlog 照建 ⇒ 頁面看起來只是「還在暖機」）。

    result＝`{"schema_id": PM_WO_SCHEMA_ID, "rows": [raw dict…], "truncated": bool,
              "dropped_n": int, "dedup_n": int, "pages": int, "count": int}`
      · **不過 `w8.normalize_wo`**——PM 四態要的 `scheduled_date`/`closed_date`/`closed_time`/
        `status`/`assigned_person`/`pm_source_id` 全在 normalize 的白名單之外（那把尺是給
        停機融合用的）。本層只做形狀 pin，語意在 `board_pm`。
      · **回應雙收**：裸 list（live 實形）∨ `{items[,truncated]}` 信封（**容錯路徑**、保留是為了
        cmms 哪天改回家族形時不至於整頁黑掉）。兩者以外＝`bad_schema` 拒讀。
        ⚠ **信封形視為單頁、不翻頁**（沿用它自報的 `truncated`）——那是容錯路徑不是實形，
        對它翻頁等於替一個沒對版的形狀發明分頁語意。
      · ★ **limit＋offset 翻頁**（承重）：伺服端 `limit` **預設 100**（實測 365d PM 窗真量在單頁上限內、
        不帶 limit 只回 100）⇒ 每次請求顯式帶 `_PM_WO_LIMIT`；單頁回滿即以 `offset` 續抓。
        不翻頁的失敗型＝**最舊的月份結構性空白，而頁面只會說「那時候沒有保養到期」**
        （§11 ⑥ `pm_wo_lookback_d` 那條的鏡像＝把抓不到講成沒發生）。
        抓滿 `_PM_WO_MAX_PAGES` 且末頁仍回滿 ⇒ `truncated=True` 誠實自揭（**絕不無界迴圈**）。
      · **跨頁以 `work_order_no` 去重**（計 `dedup_n`）：翻頁之間若有新單插入，整體位移會讓
        同一張單在兩頁都出現（快照語意、非交易）。⚠ 鍵**原樣不正規化**（`34081` 與 `"34081"`
        視為相異）——正規化＝發明第二個身分（CM8 同族鐵則）；保守側只會少去重、不會誤刪。

      ★★ **翻頁安全性論證（cmms NOTE-33 §六；我方原本問錯了重點，這段記下正解）**
        我方原本問的是「排序穩不穩」——**真正決定安不安全的是漂移的方向**：
          · 新單編號＝`max+1`、排序 `work_order_no DESC` ⇒ 新列一律插在**頭部**
            ⇒ 既有列**往後位移** ⇒ 翻頁只會**重複**（page N+1 開頭重複 page N 尾巴）、
            **結構上不可能漏列** ⇒ **上面那條去重就已經完備，不必再加東西**。
          · **漏列需要「列離開結果集」**，只有兩條路：
            (a) 硬刪——cmms domain **無工單硬刪路徑**（取消/作廢都是狀態轉移、列永遠留著），
                唯一硬刪是 cutover 維運腳本、非 runtime 行為 ⇒ 實務不發生。
            (b) ★ **可變過濾軸**——這條會真的咬人：若帶 `status=OPEN` 翻頁，某單在 page N 與
                N+1 之間結案 ⇒ 它離開結果集 ⇒ 後面的列**往前**位移 ⇒ 那一列被跳過＝**真漏列**，
                而且**去重擋不住**（它根本沒出現過，沒有重複可比對）。
        ⇒ **鐵則：翻頁時絕不帶可變過濾軸。** 用不可變軸圈窗（`work_type` / `asset_id` /
          `opened_on_or_after|_before` 皆不可變）、**狀態一律在客戶端過濾**
          （`board_pm.classify_wo` 本來就這樣做）。
        ⇒ 本函式**刻意不提供 `status` 參數**——不是忘了做，是**做了就會漏列**。
          selftest 有結構釘（簽名不得出現 `status`、組出的 URL 不得含 `status=`）。
      · **量再長怎麼辦**（現量 < 1000＝單頁，翻頁要到成長後才真正踩到）：
        `_PM_WO_MAX_PAGES` 給到 5,000 列；再長且非帶可變軸不可 ⇒ 請 cmms 加 additive
        cursor（`work_order_no_below=<上頁末筆單號>`）做 keyset 翻頁＝視窗釘在單號而非位移，
        重複與漏列同時消滅。**cmms 已表態 additive、零 migration、schema 不變，我方開口就做。**
      · 列 pin＝dict 且含 `work_order_no`；不符者丟棄計 `dropped_n`。
      · **任一頁失敗＝整批 `(None, reason)`**：半批比空批更毒——完成度母體殘缺不會被任何人
        看出來，只會靜默壓低分數（空批至少會走 §5 降級卡）。
      · ⚠ **窗是開單日、不是到期日**（CM1：LIST 無到期日窗）⇒ 呼叫端必須超抓
        （`board_data.pm_wo_lookback_d`）再按 due 過濾；本層不隱瞞這件事、也不假裝有 due 窗。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = _PM_WO_LIMIT
    lim = max(1, lim)
    f, t = _pm_day(opened_from), _pm_day(opened_to)
    kept, dropped, dedup, pages = [], 0, 0, 0
    seen = set()
    truncated = False
    for page in range(_PM_WO_MAX_PAGES):
        q = {_PM_WO_Q_TYPE: work_type}
        if f:
            q[_PM_WO_Q_FROM] = f
        if t:
            q[_PM_WO_Q_TO] = t
        q["limit"] = lim                       # ★ 不帶＝伺服端預設 100＝靜默截斷 10 倍
        q["offset"] = page * lim
        payload, reason = tr(f"{base}{_PM_WO_PATH}?" + urllib.parse.urlencode(q))
        if reason is not None:
            return (None, reason)              # 半批比空批更毒 ⇒ 任一頁失敗＝整批失敗
        rows, env_trunc = _pm_dual_payload(payload)     # 裸 list（實形）∨ 信封（容錯）
        if rows is None:
            return (None, "bad_schema")
        pages += 1
        for r in rows:
            if not isinstance(r, dict) or r.get("work_order_no") is None:
                dropped += 1
                continue
            wno = r["work_order_no"]
            key = wno if isinstance(wno, (str, int, float, bool)) else str(wno)
            if key in seen:
                dedup += 1                     # 翻頁間插入新單造成的跨頁重複
                continue
            seen.add(key)
            kept.append(r)
        if not isinstance(payload, list):      # 信封形＝容錯路徑：單頁、沿用其 truncated
            truncated = bool(env_trunc)
            break
        if len(rows) < lim:
            break                              # 沒回滿＝沒有下一頁
        if page == _PM_WO_MAX_PAGES - 1:
            truncated = True                   # 末頁仍回滿 ⇒ 還有第 N+1 頁、誠實自揭
    return ({"schema_id": PM_WO_SCHEMA_ID, "rows": kept, "truncated": truncated,
             "dropped_n": dropped, "dedup_n": dedup, "pages": pages,
             "count": len(kept)}, None)


def fetch_tasks(*, limit=_PM_TASKS_LIMIT, timeout=DEFAULT_TIMEOUT, transport=None):
    """PM task 字典（S-PM4：`GET {base}/tasks?limit=N`）→ (result, None) | (None, reason)。

    **為什麼要這支**：`/pm-schedules` 只曝 `task_id`（其值＝`task_no`，如 `_3TN905K21`），
    整頁對長官只有代號可讀。cmms A9 四輪定案：`/tasks` 的 `description` 就是人話名
    （例 `YPTR` → `Plasma Oven Tray Replacement`）、441 筆一頁抓完、同把 bearer、**不需要
    新切片**。⇒ 抓一次 cache 成 map、餵給呈現層。

    result＝`{"schema_id": PM_TASKS_SCHEMA_ID, "rows": [{"task_no","description","is_active"}…],
              "truncated": bool, "limit_hit": bool, "dropped_n": int, "count": int}`
      · 列 pin＝dict 且 `task_no` 非空字串（`str(...)` 後非空）；`description` 非 str → None
        （**名稱缺 ≠ 整列丟**：該 task 顯代號即可，丟列反而讓別的 task 也拿不到名字）。
      · `task_no` **opaque string**：零 trim、零正規化、零大小寫轉換——它是 join 鍵，
        正規化＝發明第二個身分（CM8 同族鐵則；legacy 值域含 `_3TN905K21` 這種前導底線）。
      · ⚠ `is_active` **只是字典屬性**：原樣帶出供診斷，但**殭屍判準永遠是 `/pm-schedules`
        側的旗標**（`board_data` 只取 `task_no→description` 進板＝結構上不可能混用）。
      · envelope＝S-PM5 唯一殘餘 → 雙收（猜錯＝bad_schema ⇒ 名稱層 fail-open 顯代號）。
      · **名稱層 fail-open 是本函式存在的前提**：任何 reason 都只讓頁面退回代號顯示，
        呼叫端**絕不得**因為字典掛掉就把整板降級（那是拿裝飾品當承重牆）。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    url = f"{base}{_PM_TASKS_PATH}?" + urllib.parse.urlencode({"limit": int(limit)})
    payload, reason = tr(url)
    if reason is not None:
        return (None, reason)
    rows, trunc = _pm_dual_payload(payload)
    if rows is None:
        return (None, "bad_schema")
    kept, dropped = [], 0
    for r in rows:
        if not isinstance(r, dict):
            dropped += 1
            continue
        tno = r.get("task_no")
        tno = str(tno) if tno is not None else ""
        if not tno:
            dropped += 1
            continue
        desc = r.get("description")
        kept.append({"task_no": tno,                      # opaque：零 strip、零正規化
                     "description": desc if isinstance(desc, str) else None,
                     "is_active": r.get("is_active")})
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 0
    limit_hit = bool(lim) and len(rows) >= lim            # 同 sched：裸 list 無 truncated 可讀
    return ({"schema_id": PM_TASKS_SCHEMA_ID, "rows": kept,
             "truncated": bool(trunc) or limit_hit, "limit_hit": limit_hit,
             "dropped_n": dropped, "count": len(kept)}, None)


def fetch_inventory_items(*, limit=_PM_INV_LIMIT, timeout=DEFAULT_TIMEOUT, transport=None):
    """零件人話名字典（S-PM6：`GET {base}/inventory-items?limit=&offset=`）
       → (result, None) | (None, reason)。

    **為什麼要這支**：模板細項的用料欄只有 `item_code`（`XC910002`），工程師看得懂、長官看不懂。
    cmms A9 五輪定案：`/inventory-items` 是現成切片（`InventoryItemRead` 14 欄、同把 bearer、
    裸 JSON list），人話欄兩支——`name`（短名）與 `description`（長敘述）。
    ⇒ 抓一次 cache 成 map（顯示鏈在 `board_pm.item_names_map`）。

    ★ **兩頁取完**（本函式與別支不同的唯一一點）：全量現值 **列數 > limit 上限 1000**
      ⇒ `offset=0` 與 `offset=1000` 兩次呼叫。**只帶 limit 不帶 offset＝靜默少一批名字**
      （而且頁面完全正常、只是有些零件顯代號）。第一頁沒回滿就不打第二頁（省一次往返）。
    ★ **≥2000 列 ⇒ `truncated=True`**：現值不會發生，但 cmms 若成長破 2000，第三頁的存在
      必須看得見（同 `fetch_pm_schedules` 的 limit_hit 精神：裸 list 沒有 `truncated` 欄可讀，
      回滿就是唯一訊號）。
    ★ 第二頁失敗 ⇒ **回第一頁的 1000 個名字 ＋ `truncated=True` ＋ `page_error=<reason>`**
      （不是整支失敗）：名稱層 fail-open，少一部分名字只讓那些列顯代號；為了第二頁的逾時
      而丟掉已經拿到的 1000 個名字，是拿裝飾品的故障去懲罰使用者。

    result＝`{"schema_id": PM_INV_SCHEMA_ID,
              "rows": [{"item_code","name","description",
                        "vendor_part_no","supplier","is_obsolete"}…],
              "truncated": bool, "page_error": reason|None, "pages": int,
              "dropped_n": int, "count": int}`
      · ★ **2026-08-28 欄位白名單放寬**（cmms NOTE-63；對版 md5
        `9014ec86a77cff02fb9e144ba9ba853a`＝`_fixtures/contract_inventory_items.v1.json`）：
        原本只留三欄（PM 卡的人話名），現在多留 `vendor_part_no`／`supplier`／`is_obsolete`
        給 `/cost` 的第三層料號比對。**這是加欄、不是換形**：PM 那條線一個字都沒動。
      · ⚠ **仍然刻意不留 `unit_cost`**（NOTE-63【五】3：我方明講不用；`/cost` 的錢
        只從 opswiki ledger 來、不從零件主檔來）。
      · 列 pin＝dict 且 `item_code` 非空（`str()` 後）；`name`/`description` 非 str → None
        （**名稱缺 ≠ 整列丟**：該列顯代號即可）。
      · `item_code` **opaque string**：零 trim、零正規化——它是字典鍵，也是 cmms 深連結
        `/app/inventory/{item_code}` 的路徑段（消費端負責百分比編碼）。
      · 非 list ⇒ `bad_schema`（wire ＝裸 list，已由 cmms 明文定案；不雙收）。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = _PM_INV_LIMIT
    lim = max(1, lim)
    kept, dropped, pages, page_error = [], 0, 0, None
    truncated = False
    for page in range(_PM_INV_MAX_PAGES):
        url = f"{base}{_PM_INV_PATH}?" + urllib.parse.urlencode(
            {"limit": lim, "offset": page * lim})
        payload, reason = tr(url)
        if reason is not None:
            if page == 0:
                return (None, reason)          # 第一頁就掛＝這支拿不到，交給呼叫端 fail-open
            page_error, truncated = reason, True
            break
        if not isinstance(payload, list):
            if page == 0:
                return (None, "bad_schema")
            page_error, truncated = "bad_schema", True
            break
        pages += 1
        for r in payload:
            if not isinstance(r, dict):
                dropped += 1
                continue
            code = r.get("item_code")
            code = str(code) if code is not None else ""
            if not code:
                dropped += 1
                continue
            nm, ds = r.get("name"), r.get("description")
            vpn = r.get("vendor_part_no")
            sup = r.get("supplier")
            kept.append({"item_code": code,                 # opaque：零 strip、零正規化
                         "name": nm if isinstance(nm, str) else None,
                         "description": ds if isinstance(ds, str) else None,
                         # ★ 2026-08-28 白名單放寬（cmms NOTE-63【二】盤點回覆）：
                         #   `/cost` 第三層要用**供應商料號**當比對鍵。
                         #   ⚠ `vendor_part_no` **原值直送、零正規化**——清洗規則住
                         #     `board_partsmatch`（他們明講不做正規化，且給了三個
                         #     會咬人的真值形狀：一格雙編號／含斜線／中間有空白）。
                         "vendor_part_no": vpn if isinstance(vpn, str) else None,
                         "supplier": sup if isinstance(sup, str) else None,
                         # `is_obsolete` 語意＝**「不再採購，庫存可繼續領用」**（NOTE-63【二】②，
                         # 三條碼上證據）⇒ **不得**讀成「這個型號不會再出現在採購標題裡」。
                         "is_obsolete": bool(r.get("is_obsolete"))})
        if len(payload) < lim:
            break                                           # 沒回滿＝沒有下一頁
        if page == _PM_INV_MAX_PAGES - 1:
            truncated = True                                # 最後一頁仍回滿 ⇒ 可能還有第三頁
    return ({"schema_id": PM_INV_SCHEMA_ID, "rows": kept, "truncated": truncated,
             "page_error": page_error, "pages": pages,
             "dropped_n": dropped, "count": len(kept)}, None)


# ★ SPEC-RUN-COST.md §3／§7.2-2：同一把讀取線 bearer、同一個 base URL（`_pm_transport` 沿用）。
_COST_AGG_PATH = "/cost/aggregate"


def fetch_cost_aggregate(from_date, to_date, *, bucket="month", assets=None,
                         timeout=DEFAULT_TIMEOUT, transport=None):
    """cmms 領料成本彙總（`GET {base}/cost/aggregate?from_date=&to_date=&bucket=[&assets=]`）
       → (payload, None) | (None, reason)。

    **本函式刻意薄**：`SPEC-RUN-COST.md` §8.8 閘門把「解析／normalize」與「`Decimal`／`amount`
    相關的碼」全部釘在消費端（`board_runcost.parse_aggregate`）——那支才做 13 鍵齊全、
    `schema_id` 字面驗證、金額十進位字串解析。本函式只管**網路 ＋ 最外層 pin**（payload 必須
    是 `dict`，否則 `bad_schema`），深層驗證刻意不重複一份在這裡（同 `_parse_detail_envelope`
    「結構 pin、不冒險解讀」的分工，只是把 pin 的深度交給消費端）。

    ⚠ **v1 常態零 `assets`**：SPEC §8.1-2 連帶裁決「請求時不帶 `assets` 參數」——帶了就沒有
      「名冊外設備」那一格（§8.1-2④），總額也不再是 cmms 帳的可信總額（§3 承重 4：
      `Σ rows.amount + Σ pool_rows.amount` 只在不帶 `assets` 時才是可信總額）。`assets` 參數
      留在簽章上只為將來若真要切片，**呼叫端必須自己決定要付這個代價**，本函式不代為決定。
    ⚠ 同把 bearer、同一個 base URL（`BOARD_CMMS_BASE_URL`／`BOARD_CMMS_TOKEN`，
      SPEC §7.2-2「不另開票」；`_pm_transport` 沿用，**零第二份設定讀取**）。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    q = {"from_date": _pm_day(from_date), "to_date": _pm_day(to_date), "bucket": bucket}
    if assets:
        q["assets"] = ",".join(str(a) for a in assets)
    url = f"{base}{_COST_AGG_PATH}?" + urllib.parse.urlencode(q)
    payload, reason = tr(url)
    if reason is not None:
        return (None, reason)
    if not isinstance(payload, dict):
        return (None, "bad_schema")
    return (payload, None)


def _seq_sort_key(row):
    """`proc_seq` 排序鍵：數值可解 →(0, 值, "")｜不可解 →(1, 0.0, 原字串)｜None →(2, 0.0, "")。
       **恆不 raise、恆可比較**（int/str/None 混型是 S2 未對版前的合法可能性）。"""
    v = (row or {}).get("proc_seq")
    if v is None or isinstance(v, bool):
        return (2, 0.0, "")
    try:
        return (0, float(v), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(v))


def fetch_task_steps(task_no, *, timeout=DEFAULT_TIMEOUT, transport=None):
    """PM 模板細項（S2：`GET {base}/tasks/{task_no}/steps`）→ (rows, None) | (None, reason)。

    · 200 `[]` → `([], None)`＝**cmms 有此 task、但無細項**（不是錯誤；展開卡另有文案）。
    · 404 → `(None, "http_404")`＝task 已不存在（呼叫端翻 `gone` 文案）。
    · 非 list → bad_schema。列 pin＝**`description`（str）**；`parts` 缺→`[]`。
      ⚠ `proc_seq` **可為 null（§8.2 值域鐵則：可空可重複、勿當唯一鍵）** ⇒ **不得拿它當列
        pin**——原本 `proc_seq is None` 就丟列，會靜默吃掉合法步驟，全 null 的 task 更會整批
        翻成 bad_schema（把合法資料謊報成 schema 分歧）。null 由消費端顯「—」。
    · **非空清單全數不合形 → bad_schema**（不可靜默塌成 `[]`——那會被頁面說成「cmms 無細項」
      ＝把 schema 分歧謊報成資料現實）。
    · cmms 不記 per-WO 逐項打勾（CM3）⇒ 本層回的是**模板**，無完成狀態欄可帶。
    · ★ 排序鍵**型別安全**（2026-08-04 verify LOW）：`proc_seq` 可空、可重複、且 S2 未對版前
      **可能 int 與 str 並存**（`10` 與 `"20"`）；直接 `sort` 會 `TypeError` 外拋，違反檔頭
      pin 的錯誤模型「任何錯誤不外拋、一律回 (None, reason)」，且下游 `get_pm_item` 的
      catch-all 會把它收斂成整卡 error＝**細項腿把工單腿一起拖死**（兩腿獨立容忍破功）。
      故排序鍵＝(數值可解者依數值｜不可解者依字串殿後｜None 最後)，恆不 raise。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    url = base + _PM_STEPS_PATH_TMPL.format(task_no=urllib.parse.quote(str(task_no)))
    payload, reason = tr(url)
    if reason is not None:
        return (None, reason)
    if not isinstance(payload, list):
        return (None, "bad_schema")
    out = []
    for r in payload:
        if not isinstance(r, dict):
            continue
        desc = r.get("description")
        if not isinstance(desc, str):
            continue
        parts = r.get("parts")
        parts = [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []
        out.append({"proc_seq": r.get("proc_seq"), "description": desc, "parts": parts})
    if payload and not out:
        return (None, "bad_schema")
    out.sort(key=_seq_sort_key)                       # 依 proc_seq 升冪（S2 預告；型別安全）
    return (out, None)


# ---------------------------------------------------------------------------
# A11 資產排程狀態事件（`GET {base}/assets/{asset_id}/schedule-state-events`）
# ---------------------------------------------------------------------------
# 契約唯一真相＝`SPEC-DORMANT-CAPACITY.md` §6.1（＋cmms NOTE-27 對版、NOTE-31 additive 補欄）；
# golden＝`_fixtures/contract_asset_schedule_state.v1.json`
#   （md5 **7428d6f17dab02ae597c0db1884e963a**、432 B、LF、CR=0）。
# ★ 2026-09-03 換版（additive `sub_unit`；schema 仍 v1、原地補欄，缺席語意＝整台機）——
#   舊值 `dc1217de91203ef7d1d1275b4696a415`（388 B）**作廢**。同輪另入庫兩支 sub_unit 樣本：
#   `_fixtures/contract_asset_schedule_state.v1.sub_unit_single.json`（0a3ccc6f5e1744cc3d60f8e37d6d8af7、222 B）
#   `_fixtures/contract_asset_schedule_state.v1.sub_unit_two_lanes.json`（530572d726163afb686e2df8d042e054、441 B）
#   來源＝cmms relay `internal-relay-note` ■六（走 A13 退路、A14 ② 被 reject）。
# ⚠ 傳輸層對 `sub_unit` **不做形狀硬驗**（additive 未知欄一律原樣搬運）；投影紀律在
#   `board_capacity`（`sub_unit` 列絕不轉交 `board_dormant.is_dormant`）。
A11_SCHED_STATE_PATH_TMPL = "/assets/{asset_id}/schedule-state-events"
A11_SCHED_STATE_SCHEMA_ID = "contract_asset_schedule_state.v1"
# 值域（v1 只有兩態、無 retract）；與 `board_dormant.STATE_*` 同值——此處刻意寫死字面值
# 而非 import：傳輸層對呈現層零相依（`board_dormant` 是零專案相依的純函式層）。
A11_STATES = ("not_scheduled", "in_production")


def _a11_date_ok(v):
    """`effective_date` 形狀 pin＝**恰好 'YYYY-MM-DD' 的台北日曆日**（NOTE-31 additive 補欄）。

    ★ **全鏈禁止任何 +8h／UTC→台北換算**：投影只讀本欄，`ts_effective` 原樣帶出但不參與
      日期推導。曾經最容易寫錯的一行就是「拿 `ts_effective` 前 10 碼當日期」——
      `2025-10-23T16:00:00Z` 的前 10 碼是 10-23，而該筆的台北生效日是 **10-24**（差一天，
      而且整條長停區間會整段左移一日，畫面看起來完全正常）。
    """
    if not isinstance(v, str) or len(v) != 10 or v[4] != "-" or v[7] != "-":
        return False
    try:
        _ = (int(v[:4]), int(v[5:7]), int(v[8:10]))
    except ValueError:
        return False
    import datetime as _dt
    try:
        _dt.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def fetch_schedule_state_events(asset_id, *, timeout=DEFAULT_TIMEOUT, transport=None):
    """A11 資產排程狀態事件（長停／復機登記）→ (result, None) | (None, reason)。

    result＝`{"schema_id": A11_SCHED_STATE_SCHEMA_ID, "rows": [raw dict…], "count": int}`
      · `rows` **原樣 raw**（adapter／投影住 `board_data`＋`board_dormant`＝一份規則）。

    wire 形（cmms NOTE-27/31 已文字對版＋golden 鎖形）＝**裸 JSON list**，每筆五欄恆在：
      `ts_effective`(UTC Z 字串)／`effective_date`("YYYY-MM-DD" 台北日曆日)／
      `state` ∈ {not_scheduled, in_production}／`reason`(自由文字，可 null)／`recorded_at`。
      `ts_effective` 升冪、只曝 effective（更正走內部 supersede）。

    · ★ **不雙收信封形**（比照 `/pm-schedules` S-PM1 收斂紀律）：本端點已文字對版且有 golden
      鎖形，留著第二種形狀等於永遠照不出「cmms 改了形」這件事。非 list ⇒ `bad_schema`。
    · ★ **空 `[]` ＝該資產全程排產中**（合法、`count=0`），**不是查無資料、不分兩路**。
      資產不存在＝404 ⇒ `http_404`（我方只打已知 EID ⇒ 404 是異常、照錯誤族回，不吞）。
    · ★ **絕不做「壞列丟棄」**（與 `/pm-schedules` 的 `dropped_n` 紀律**刻意相反**）：
      排程狀態是**狀態機**，投影＝「生效日 ≤ D 的最後一筆定調」。丟掉中間任何一筆
      （例如那筆 `in_production` 復機事件），剩下的事件流仍然完全合法、投影照樣算得出來，
      只是**答案是錯的**——一台早就復機的工位會被永遠當成長停、直接從產能分母消失。
      **殘缺比空更毒** ⇒ 任一列不合形 ⇒ 整批拒讀（`bad_schema` / `missing_effective_date`）。
    · ★ `missing_effective_date`＝**部署前訊號**（NOTE-31 是 2026-08-10 的 additive 補欄）：
      cmms 尚未部署帶此欄的版本時，列會少這一欄。此時**唯一安全的行為是整批不用**——
      退而求其次去截 `ts_effective` 就是上面 `_a11_date_ok` 講的那個差一天的錯。
      呼叫端據此退回過渡橋常數並在頁面揭露（絕不靜默）。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    url = base + A11_SCHED_STATE_PATH_TMPL.format(
        asset_id=urllib.parse.quote(str(asset_id), safe=""))
    payload, reason = tr(url)
    if reason is not None:
        return (None, reason)
    if not isinstance(payload, list):                 # 裸 list only（不雙收信封）
        return (None, "bad_schema")
    for r in payload:
        if not isinstance(r, dict):
            return (None, "bad_schema")
        if not _a11_date_ok(r.get("effective_date")):
            return (None, "missing_effective_date")   # 部署前訊號（先驗，勿誤報 bad_schema）
        if r.get("state") not in A11_STATES:
            return (None, "bad_schema")
    return ({"schema_id": A11_SCHED_STATE_SCHEMA_ID, "rows": list(payload),
             "count": len(payload)}, None)


# ---------------------------------------------------------------------------
# A11 批次現況讀（`GET {base}/assets/schedule-state?eids=&as_of=`）
# ---------------------------------------------------------------------------
# 契約唯一真相＝`SPEC-DORMANT-CAPACITY.md` §6.1「兩條讀取路徑分工」（形狀＝cmms NOTE-33【一】
# 雙邊定案、NOTE-34 交付並對版）；golden＝`_fixtures/contract_asset_schedule_state_batch.v1.json`
# （md5 `2b4e4072b2d8fda1773294ec9a667a1d`）。
#
# ★ 與上面 per-EID 那支的分工（**別混用**）：
#   本支只答「**現在**」＝板面著色，整份名冊一次問完；**歷史 as-of 一律走 per-EID 事件流**
#   （12 個月趨勢的逐日分母）。拿本支去算趨勢＝用今天的結論回頭套整條歷史。
A11_BATCH_PATH = "/assets/schedule-state"
A11_BATCH_SCHEMA_ID = "contract_asset_schedule_state_batch.v1"
A11_BATCH_MAX_EIDS = 200      # cmms 硬上限（顯式模式去重後 >200 → 422，**不靜默截斷**）


def fetch_schedule_state_batch(eids=None, *, as_of=None,
                               timeout=DEFAULT_TIMEOUT, transport=None):
    """A11 批次現況讀（一次問一整份名冊）→ (result, None) | (None, reason)。

    result＝`{"schema_id": A11_BATCH_SCHEMA_ID, "items": [raw dict…], "unknown": [eid…],
              "count": int, "unknown_n": int, "pages": int, "explicit": bool}`
      · `items` **原樣 raw**（adapter／投影住 `board_data`＋`board_dormant`＝一份規則，
        同 per-EID 那支）。每筆四欄：`asset_id`／`state`／`since`／`reason`。
      · `since`＝**台北日曆日字串**（現況那筆 effective 事件的生效日）或 `None`（無事件）。
        與事件流的 `effective_date` **同值同軸** ⇒ 全鏈零 UTC 換算（同 `_a11_date_ok` 那條）。

    wire 形（cmms NOTE-33【一】／NOTE-34 定案）＝**信封 `{items, unknown}`**
    ——⚠ 與 per-EID 那支的**裸 list 恰好相反**，兩支各自 pin 各自的形，不共用判斷。

    · 兩個參數皆選填；`as_of` 省略＝台北今天。`eids` 省略＝**全 asset 主檔（含退役機）**、
      且該模式 `unknown` 恆為 `[]`。
    · ★ **`eids=[]`（空清單）⇒ `empty_eids`、零網路**，**絕不**退化成「省略＝問全機隊」。
      這條看似瑣碎，卻是本函式最容易出人命的一行：呼叫端的名冊若因故載不出來（catalog
      取數失敗、過濾條件寫錯）就會遞一個空 list 進來，而「安靜地改問 cmms 全部資產」與呼叫端
      想要的東西毫無關係，卻長得完全正常。**兩種模式必須由呼叫端明示，不得由資料推導。**
    · ★ **>200 自動切塊**（`A11_BATCH_MAX_EIDS`）而非讓伺服器回 422：去重後依序切、逐塊
      呼叫、結果合併。**任一塊失敗 ⇒ 整批不用**（同 per-EID 的「部分換源＝同頁混兩份真相」）。
      現值名冊遠小於單塊上限 ⇒ 恆為單塊；切塊是為了「日後名冊長大」不會靜默半路 422。

    ★★ **承重檢查一：scope 內每台恆回一筆**（顯式模式；違反 ⇒ `incomplete_scope`）
      cmms 明文保證「scope 內每台在 `items` 恰好出現一次、欄位恆在；無事件＝
      `{state:"in_production", since:null, reason:null}`」，並**刻意不做**「只回有事件
      的那幾台」省流版。這條保證是我方消費端的地基：**若 cmms 哪天退回省流版，
      我方 `items` 查無此 EID 會被讀成「這台在排產」——與「這台我沒問到」長得一模一樣**，
      正是 A11 存在要消滅的那類靜默。故本層逐塊驗 `covered == set(chunk)`，不符即整批拒讀。
      ⇒ 這個回歸**在傳輸層就紅**，而不是在頁面上變成一台悄悄不見的長停機器。

    ★★ **承重檢查二：`unknown[]` 是錯誤，不是雜訊**（cmms NOTE-34【三】硬紀律）
      非空 `unknown` ＝我方送的 EID 在 cmms 主檔查無 ⇒ **兩邊名冊漂了**（打錯、或新機
      cmms 還沒建檔）。本層**照實回**（`unknown` / `unknown_n` 兩個鍵恆在）、**不吞、
      不轉成錯誤族**——`items` 對已知那些台仍然有效，把整批打成失敗反而是過度反應。
      ⚠ **但呼叫端不得忽略**：SPEC §6.1 要求頁上紅字報出來，不得當成「這台大概沒事」。
      裸 list 之所以不行、信封之所以存在，唯一理由就是這件事。

    · 排序：cmms 保證 `items`／`unknown` 皆依 EID 升冪。**我方刻意不驗、也不依賴**——
      消費端一律建 dict 查表。把便利當契約＝日後 cmms 一個良性的排序改動就讓板面死掉。
      selftest 有結構釘：**餵亂序照樣成功且內容集合相同**（我方既不排序、也不要求排序）。
    · 列 pin（任一列不合形 ⇒ **整批拒讀**，同 per-EID 的「殘缺比空更毒」）：dict／
      `asset_id` 非空（opaque string，零 trim 零正規化）／`state` ∈ `A11_STATES`／
      `since` 為 None 或合法 `YYYY-MM-DD`／同一塊內 `asset_id` 不重複、且與 `unknown` 互斥。
    · `as_of` 非合法日 ⇒ `bad_as_of`、**零網路**（絕不把壞日期送上線讓伺服器猜）。
    · 非 dict 信封 ⇒ `bad_schema`（**不雙收**裸 list：留第二種形＝永遠照不出 cmms 改形）。
    · ⚠ **部署前＝`http_404`**（cmms NOTE-34【五】：實作完成 ≠ 已部署）。呼叫端據此退回
      既有路徑並在頁面揭露；404 不得被讀成「這批機器都沒登記」。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    if as_of is not None and not _a11_date_ok(as_of):
        return (None, "bad_as_of")                # 零網路：壞日期絕不出門
    if eids is None:
        chunks, explicit = [None], False          # 省略＝全機隊（unknown 恆 []）
    else:
        want, seen = [], set()
        for e in eids:
            s = str(e) if e is not None else ""   # opaque：零 trim、零正規化
            if not s or s in seen:
                continue
            seen.add(s)
            want.append(s)
        if not want:
            return (None, "empty_eids")           # ★ 絕不退化成「問全機隊」（見 docstring）
        chunks = [want[i:i + A11_BATCH_MAX_EIDS]
                  for i in range(0, len(want), A11_BATCH_MAX_EIDS)]
        explicit = True
    items, unknown = [], []
    for chunk in chunks:
        q = {}
        if chunk is not None:
            q["eids"] = ",".join(chunk)
        if as_of is not None:
            q["as_of"] = as_of
        url = base + A11_BATCH_PATH + (("?" + urllib.parse.urlencode(q)) if q else "")
        payload, reason = tr(url)
        if reason is not None:
            return (None, reason)                 # ★ 任一塊失敗 ⇒ 整批不用
        if not isinstance(payload, dict):         # 信封形 only（與 per-EID 裸 list 相反）
            return (None, "bad_schema")
        rows, unk = payload.get("items"), payload.get("unknown")
        if not isinstance(rows, list) or not isinstance(unk, list):
            return (None, "bad_schema")
        covered = set()
        for r in rows:
            if not isinstance(r, dict):
                return (None, "bad_schema")
            aid = r.get("asset_id")
            aid = str(aid) if aid is not None else ""
            if not aid or aid in covered:         # 空鍵／重複＝「恰好一筆」被破壞
                return (None, "bad_schema")
            if r.get("state") not in A11_STATES:
                return (None, "bad_schema")
            since = r.get("since")
            if since is not None and not _a11_date_ok(since):
                return (None, "bad_schema")
            covered.add(aid)
            items.append(r)
        for u in unk:
            us = str(u) if u is not None else ""
            if not us or us in covered:           # unknown 與 items 必須互斥
                return (None, "bad_schema")
            covered.add(us)
            unknown.append(us)
        if chunk is not None and covered != set(chunk):
            return (None, "incomplete_scope")     # ★★ 承重檢查一（見 docstring）
    return ({"schema_id": A11_BATCH_SCHEMA_ID, "items": items, "unknown": unknown,
             "count": len(items), "unknown_n": len(unknown),
             "pages": len(chunks), "explicit": explicit}, None)


def fetch_pm_wo_detail(wno, *, timeout=DEFAULT_TIMEOUT, transport=None):
    """單張 WO detail（`GET {base}/work-orders/{no}/detail`）→ (detail_raw, None) | (None, reason)。

    重用既有 `_detail_url`＋`_parse_detail_envelope`（**不複製一份解析邏輯**）。
    ⚠ `http_404` 由呼叫端**容忍**：`/pm-schedules.last_work_order_no` 是 soft-ref、
      無 FK 保證、~7% 指向已清除舊單（§8.2 CM10）＝已知現實、不是錯誤。
    """
    tr, base, reason = _pm_transport(transport, timeout)
    if reason is not None:
        return (None, reason)
    payload, reason = tr(_detail_url(base, wno))
    if reason is not None:
        return (None, reason)
    det, reason = _parse_detail_envelope(payload)
    if reason is not None:
        return (None, reason)
    return (det, None)


# ---------------------------------------------------------------------------
# selftest（離線零網路：注入 fake transport + 讀本地 fixture；絕無真 HTTP）
# ---------------------------------------------------------------------------

def _selftest():
    import datetime as _dt
    import os.path as _osp

    NOW = _dt.datetime(2026, 7, 1, 12, 0, 0)   # 與 board_w8 selftest 對齊、deterministic
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    def _fx_ok(*names):
        """golden fixture 不存在（例：脫敏公開樹不帶 cmms 交檔 golden）⇒ 該段標 skipped、不算失敗；存在 ⇒ 照舊全比對。"""
        miss = [nm for nm in names
                if not _osp.exists(_osp.join(_osp.dirname(_osp.abspath(__file__)), "_fixtures", nm))]
        if miss:
            print("board_w8_transport selftest: skipped: %s absent" % ", ".join(miss))
        return not miss

    # ---- 載入 fixture golden（inline 形狀本體）----
    fx_path = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                        "_fixtures", "w8.workorder.json")
    with open(fx_path, encoding="utf-8") as f:
        fx = json.load(f)
    golden = fx["golden"]

    # ===== fake transport 工廠（純函式、零網路）=====
    def make_transport(list_payload, detail_map=None, list_reason=None,
                       detail_reason=None, capture=None):
        """回一個 (url)->(payload,reason) 的 fake。
        list_payload/list_reason＝列表端點回應；detail_map＝{wno:payload}、detail_reason＝統一失敗。"""
        detail_map = detail_map or {}

        def _t(url):
            if capture is not None:
                capture.append(url)
            path = urllib.parse.urlsplit(url).path
            if path == _LIST_PATH:
                if list_reason is not None:
                    return (None, list_reason)
                return (list_payload, None)
            if path.endswith("/detail"):
                if detail_reason is not None:
                    return (None, detail_reason)
                wno = int(path.split("/")[2])
                return (detail_map.get(wno), None) if wno in detail_map \
                    else (None, "http_404")
            return (None, "unreachable")
        return _t

    def _pipeline_windows(res):
        """把結果 WO 過凍結核心 wo_active_window（證 fetch→normalize→活躍窗全鏈）。"""
        return {w["work_order_no"]: w8.wo_active_window(w, now=NOW)
                for w in res["work_orders"]}

    # ---------- (A) not_configured：transport=None + 無 env（零網路短路）----------
    saved = {k: os.environ.pop(k, None)
             for k in ("BOARD_CMMS_BASE_URL", "BOARD_CMMS_TOKEN")}
    try:
        ck(fetch_active_work_orders("2026-06-01 00:00:00", "2026-06-30 00:00:00")
           == (None, "not_configured"), "缺 env → not_configured")
        os.environ["BOARD_CMMS_BASE_URL"] = "http://work.example.com"
        ck(fetch_active_work_orders("2026-06-01 00:00:00", "2026-06-30 00:00:00")
           == (None, "not_configured"), "缺 token → not_configured")
        ck(is_configured() is False, "只設 base → is_configured False")
    finally:
        os.environ.pop("BOARD_CMMS_BASE_URL", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    # ---------- (B) 一次式 inline：fixture golden 走完整管線 ----------
    list_inline = {"items": golden, "truncated": False}        # 真 envelope 形（S13 CLOSED）
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00",
        transport=make_transport(list_inline))
    ck(reason is None and res is not None, "inline：應成功")
    ck(res["mode"] == "list_inline", f"inline：mode 應 list_inline，got {res['mode']}")
    ck(res["count"] == len(golden) and res["truncated"] is False, "inline：count/truncated")
    wins = _pipeline_windows(res)
    for g in golden:
        exp = g["_active_window_expected"]
        want = (w8._parse_dt(exp["start"]),
                None if exp["end"] is None else w8._parse_dt(exp["end"]))
        ck(wins[g["work_order_no"]] == want,
           f"inline 管線窗不符 wo={g['work_order_no']}: {wins[g['work_order_no']]} vs {want}")
    # 治理：per-person（source_actor）不得外洩到 normalized 條目
    for w in res["work_orders"]:
        for e in w["status_history"]:
            ck(set(e.keys()) == {"status", "at", "hold_reason"},
               f"inline：條目欄位外洩 {e}")

    # ---------- (B2) QG-3.7：cmms **真 golden envelope** 原樣過 transport 全鏈 ----------
    # contract_wo_active_in.v1.json＝位元拷貝自 cmms repo；wire 形一字不差直接餵。
    cg_path = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                        "_fixtures", "contract_wo_active_in.v1.json")
    if _osp.exists(cg_path):
        with open(cg_path, encoding="utf-8") as f:
            cg = json.load(f)
        res, reason = fetch_active_work_orders(
            "2026-07-01 00:00:00", "2026-07-05 00:00:00",
            transport=make_transport(cg))
        ck(reason is None and res["mode"] == "list_inline" and res["count"] == 2,
           f"真 golden：inline 2 筆全過，got {reason}/{res}")
        ck(res["schema_id"] == LIST_SCHEMA_ID and res["truncated"] is False,
           "真 golden：schema pin + truncated 傳遞")
        NOW2 = _dt.datetime(2026, 7, 10, 12, 0, 0)   # golden 時戳落 7/1–7/3 台北
        wins2 = {w["work_order_no"]: w8.wo_active_window(w, now=NOW2)
                 for w in res["work_orders"]}
        # ★ 待辦 4（2026-08-16）：golden[0] 自帶 `failure_started_at` 07:30Z＝15:30 台北
        #   （< opened 17:12）⇒ 窗起點自此＝fsa（w8.wo_active_window 的對齊規則）。
        # ★ NOTE-60（2026-08-22）：同 golden 亦自帶 `production_resumed_at` 15:20Z＝23:20 台北
        #   ⇒ 窗終點自此＝pra（min(終態次日 00:50, pra 23:20)＝對齊優先於結單時刻）。
        ck(wins2[34752] == (_dt.datetime(2026, 7, 1, 15, 30),
                            _dt.datetime(2026, 7, 2, 23, 20)),
           f"真 golden wo34752：起點＝fsa 15:30（待辦 4）、終點＝pra 23:20（NOTE-60）, "
           f"got {wins2[34752]}")
        ck(wins2[35438] == (_dt.datetime(2026, 7, 3, 9, 0), None),
           f"真 golden wo35438：still-open 遷移單 09:00 台北→ongoing, got {wins2[35438]}")

    # ---------- (C) 退階式 list+detail：列表輕量列、/detail 補齊 ----------
    light_rows = [{k: g[k] for k in ("work_order_no", "asset_id", "work_type",
                                     "opened_at", "closed_at")} for g in golden]  # 無 status_history
    list_light = {"items": light_rows, "truncated": False}
    detail_map = {g["work_order_no"]: g for g in golden}       # detail＝平鋪 WO（S14 CLOSED）
    cap = []
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00", asset_id="EID-17431",
        transport=make_transport(list_light, detail_map, capture=cap))
    ck(reason is None and res["mode"] == "list_plus_detail", "detail：auto 應判退階")
    ck(res["count"] == len(golden), "detail：全數補齊")
    ck(_pipeline_windows(res) == wins, "detail：管線窗應等同 inline")
    ck(any("/work-orders/active-in" in u and "asset_id=EID-17431" in u for u in cap),
       "detail：asset_id 應進列表 URL query（真路徑 active-in）")
    ck(any(u.endswith("/detail") for u in cap), "detail：應打 /detail")

    # ---------- (D) 退階式 detail_cap：超過＝誠實少讀 + detail_truncated ----------
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00", detail_cap=1,
        transport=make_transport(list_light, detail_map))
    ck(reason is None and res["detail_truncated"] is True and res["truncated"] is True,
       "cap：detail_truncated 應 True")
    ck(res["count"] == 1, f"cap：cap 外不補、只讀 1，got {res['count']}")

    # ---------- (D2) 整體 wall-clock 預算：用盡→停止 detail、誠實少讀（不炸整批）----------
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00", total_budget_sec=0,
        transport=make_transport(list_light, detail_map))
    ck(reason is None and res["detail_truncated"] is True and res["count"] == 0,
       f"budget：預算 0 應立停 detail＋truncated，got count={res['count']}")

    # ---------- (E) 退階式個別 detail 失敗：不炸整批、計入 detail_errors ----------
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00",
        transport=make_transport(list_light, detail_map, detail_reason="timeout"))
    ck(reason is None, "detail 全失敗：整批仍不炸（回 result 非 reason）")
    ck(res["detail_errors"] == len(golden) and res["count"] == 0,
       f"detail 全失敗：errs 計數 + 0 補齊，got errs={res['detail_errors']}")

    # ---------- (F) mode="inline" 強制：輕量列（無 history）→ 遷移回退管線 ----------
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00", mode="inline",
        transport=make_transport(list_light))
    ck(reason is None and res["mode"] == "list_inline", "inline 強制：不打 detail")
    fb, tot = w8.migration_fallback_rate(res["work_orders"])
    ck(tot >= 1 and fb == tot,
       "inline 強制吃輕量列＝全走遷移回退（FP-6 訊號；auto 才會避開）")

    # ---------- (G) 列表 truncated 旗標傳遞 ----------
    res, _ = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00",
        transport=make_transport({"items": golden, "truncated": True}))
    ck(res["list_truncated"] is True and res["truncated"] is True, "list truncated 應傳遞")

    # ---------- (H) 錯誤 case：http_500 / timeout / bad_json / bad_schema ----------
    for rsn in ("http_500", "timeout", "unreachable", "bad_json"):
        ck(fetch_active_work_orders("2026-06-01 00:00:00", "2026-07-01 00:00:00",
                                    transport=make_transport(None, list_reason=rsn))
           == (None, rsn), f"列表失敗 {rsn} 應原樣回")
    # envelope 缺 truncated（結構 pin 兩鍵齊備）→ bad_schema（QG-3.3 拒讀）
    ck(fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00",
        transport=make_transport({"items": golden}))
       == (None, "bad_schema"), "缺 truncated → bad_schema")
    # envelope 形狀錯（缺 items，如舊假設 work_orders 鍵）→ bad_schema
    ck(fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00",
        transport=make_transport({"work_orders": golden, "truncated": False}))
       == (None, "bad_schema"), "缺 items → bad_schema")
    # 非 dict payload → bad_schema
    ck(fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00",
        transport=make_transport(["not", "a", "dict"]))
       == (None, "bad_schema"), "非 dict payload → bad_schema")
    # detail 形狀錯（缺 work_order_no 結構 pin）→ 該筆計 detail_errors（不炸整批）
    bad_detail = {g["work_order_no"]: {"not_a_wo": True} for g in golden}
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00",
        transport=make_transport(list_light, bad_detail))
    ck(reason is None and res["detail_errors"] == len(golden) and res["count"] == 0,
       "detail schema 錯 → 計 detail_errors、整批不炸")

    # ---------- (I) 壞 WO 混入列表（inline）→ normalize 丟棄、不炸 ----------
    dirty = {"truncated": False, "items": golden + [
        {"work_order_no": "abc", "asset_id": "EID-X", "status_history": []},  # 壞 wo_no
        {"asset_id": "EID-Y", "work_type": "PM", "status_history": []},       # 缺 wo_no
        "garbage",                                                            # 非 dict
    ]}
    res, reason = fetch_active_work_orders(
        "2026-06-01 00:00:00", "2026-07-01 00:00:00", transport=make_transport(dirty))
    ck(reason is None and res["count"] == len(golden),
       f"壞 WO 應被 normalize 丟棄、僅 golden 存活，got {res['count']}")

    # =======================================================================
    # (P) PM 板三路源（additive；全部 fake transport、零真 HTTP）
    # =======================================================================
    def make_pm_transport(routes, capture=None):
        """routes: {path: payload} ∨ {path: ("!reason", reason)}；查無 path → http_404。"""
        def _t(url):
            if capture is not None:
                capture.append(url)
            path = urllib.parse.urlsplit(url).path
            if path not in routes:
                return (None, "http_404")
            v = routes[path]
            if isinstance(v, tuple) and len(v) == 2 and v[0] == "!reason":
                return (None, v[1])
            return (v, None)
        return _t

    # ---------- (P-A) not_configured：四支全部零網路短路 ----------
    saved = {k: os.environ.pop(k, None)
             for k in ("BOARD_CMMS_BASE_URL", "BOARD_CMMS_TOKEN")}
    try:
        ck(fetch_pm_schedules() == (None, "not_configured"),
           "PM：fetch_pm_schedules 缺 env → not_configured")
        ck(fetch_pm_work_orders("2026-03-01", "2026-08-04") == (None, "not_configured"),
           "PM：fetch_pm_work_orders 缺 env → not_configured")
        ck(fetch_task_steps("YPTR") == (None, "not_configured"),
           "PM：fetch_task_steps 缺 env → not_configured")
        ck(fetch_pm_wo_detail(34081) == (None, "not_configured"),
           "PM：fetch_pm_wo_detail 缺 env → not_configured")
        ck(fetch_tasks() == (None, "not_configured"),
           "PM：fetch_tasks 缺 env → not_configured（名稱字典也吃同一道 fail-closed 閘）")
        ck(fetch_inventory_items() == (None, "not_configured"),
           "PM：fetch_inventory_items 缺 env → not_configured（S-PM6 同一道 fail-closed 閘、"
           "**零網路**）")
        ck(fetch_cost_aggregate("2026-01-01", "2026-08-31") == (None, "not_configured"),
           "cost/aggregate：缺 env → not_configured（同一道 fail-closed 閘、零網路）")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    if _fx_ok("pm_board_fixture_pm_schedules.v1.json"):
        # ---------- (P-B) /pm-schedules：**只收裸 list**（S-PM1 CLOSED）----------
        sx_path = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                            "_fixtures", "pm_board_fixture_pm_schedules.v1.json")
        with open(sx_path, encoding="utf-8") as f:
            # ★ 交檔包裝 vs wire 形：fixture 是 `{_meta,total_rows_in_prod,rows}`＝cmms 交檔時
            #   手做的包裝；wire 是裸 list。剝殼在**讀 fixture 的這一端**做（S-PM1 定案）。
            sx_rows = json.load(f)["rows"]
        cap = []
        res, reason = fetch_pm_schedules(
            transport=make_pm_transport({_PM_SCHED_PATH: sx_rows}, capture=cap))
        ck(reason is None and res["count"] == len(sx_rows) and res["dropped_n"] == 0,
           "PM sched（裸 list ＝唯一 wire 形）：全列收下、零丟棄")
        # **只釘形不釘值**（Q-CM9：8/9 重灌後 next_due/last_* 會重置）
        ck(all(isinstance(r, dict) and "pm_id" in r and "asset_id" in r
               and "standard_hours" in r and "completion_window_days" in r
               and "is_suppressed" in r for r in res["rows"]),
           "PM sched：列形 pin（pm_id/asset_id/標工/寬限/suppress 欄在）")
        ck(res["schema_id"] == PM_SCHED_SCHEMA_ID and res["truncated"] is False,
           "PM sched：schema pin + truncated")
        ck(any(u.endswith("/pm-schedules?limit=1000") for u in cap),
           "PM sched：URL 帶 limit=1000（S-PM1：伺服端預設 100、prod 列數超過 100 ⇒ 不帶就靜默截斷）")
        ck(res["rows"][0] is sx_rows[0], "PM sched：rows 原樣 raw（正規化留給 board_pm）")
        # ★ S-PM1 收斂的負斷言：家族信封形**不再被接受**（留著雙收＝永遠照不出 cmms 改形）
        ck(fetch_pm_schedules(transport=make_pm_transport(
            {_PM_SCHED_PATH: {"items": sx_rows, "truncated": False}})) == (None, "bad_schema"),
           "PM sched：`{items,truncated}` 信封形 → bad_schema（S-PM1 已定案＝裸 list 唯一）")
        # 壞列丟棄（不整批拒讀）
        res3, _ = fetch_pm_schedules(transport=make_pm_transport(
            {_PM_SCHED_PATH: sx_rows + [{"asset_id": "EID-1"}, {"pm_id": "  "}, "garbage"]}))
        ck(res3["count"] == len(sx_rows) and res3["dropped_n"] == 3,
           f"PM sched：缺 pm_id/空 pm_id/非 dict 三種壞列丟棄計數，got {res3['dropped_n']}")
        # ★ 回滿 limit 的裸 list：伺服端沒有 truncated 欄可講 ⇒ **本層必須自揭**（否則
        #   backlog/owner/預警建在被截斷的主源上、頁面零揭露＝假稱 backlog 乾淨）
        _full = [{"pm_id": "P%d" % i, "asset_id": "EID-1"} for i in range(3)]
        res5, _ = fetch_pm_schedules(limit=3, transport=make_pm_transport({_PM_SCHED_PATH: _full}))
        ck(res5["limit_hit"] is True and res5["truncated"] is True and res5["count"] == 3,
           "PM sched：裸 list 回滿 limit → limit_hit + truncated 自揭（假稱乾淨才是反模式）")
        res6, _ = fetch_pm_schedules(limit=4, transport=make_pm_transport({_PM_SCHED_PATH: _full}))
        ck(res6["limit_hit"] is False and res6["truncated"] is False,
           "PM sched：未回滿 limit → 不亂掛 truncated（保守自揭不等於恐嚇）")
        # envelope 形錯 → bad_schema
        for bad in ({"rows": sx_rows}, "nope", 42):
            ck(fetch_pm_schedules(transport=make_pm_transport({_PM_SCHED_PATH: bad}))
               == (None, "bad_schema"), "PM sched：envelope 形錯 → bad_schema")
        # 錯誤族原樣回
        for rsn in ("http_500", "timeout", "unreachable", "bad_json"):
            ck(fetch_pm_schedules(transport=make_pm_transport(
                {_PM_SCHED_PATH: ("!reason", rsn)})) == (None, rsn),
               f"PM sched：{rsn} 原樣回")

    if _fx_ok("pm_board_fixture_list_rows.v1.json"):
        # ---------- (P-C) /work-orders?work_type=PM：query 組裝 + raw 保真 ----------
        wx_path = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                            "_fixtures", "pm_board_fixture_list_rows.v1.json")
        with open(wx_path, encoding="utf-8") as f:
            wx_rows = json.load(f)["rows"]
        cap = []
        # ★ S-PM8（2026-08-10 live 實測）：wire ＝**裸 list**（不是 active-in 的信封形）
        res, reason = fetch_pm_work_orders(
            _dt.date(2026, 3, 7), _dt.datetime(2026, 8, 4, 9, 0),
            transport=make_pm_transport({_PM_WO_PATH: wx_rows}, capture=cap))
        ck(reason is None and res["count"] == 2 and res["dropped_n"] == 0
           and res["truncated"] is False and res["pages"] == 1 and res["dedup_n"] == 0,
           f"PM wo：★ 裸 list（S-PM8 實形）兩列全收、單頁即完、零丟棄零去重，got {res}")
        u = cap[-1]
        ck("work_type=PM" in u and "opened_on_or_after=2026-03-07" in u
           and "opened_on_or_before=2026-08-04" in u,
           f"PM wo：query 帶 work_type + S-PM2 定案的日期窗參數名（含端、date-only、"
           f"軸＝opened_date；date/datetime 皆正規化），got {u}")
        # ★ S-PM2 負斷言：暫 pin 的舊名已廢，**任何路徑都不得再組出它們**（改回去＝打到不存在的
        #   參數 ⇒ cmms 多半直接忽略 ⇒ 窗變成「全部」＝靜默超抓，頁面看起來完全正常）
        ck("opened_from" not in u and "opened_to" not in u
           and _PM_WO_Q_FROM == "opened_on_or_after" and _PM_WO_Q_TO == "opened_on_or_before",
           f"PM wo 負斷言：舊名 opened_from/opened_to 零出現於 query 組裝，got {u}")
        # ★ 承重負斷言：**不過 normalize_wo** ⇒ 四態要用的欄必須原封還在
        r0 = res["rows"][0]
        ck(all(k in r0 for k in ("scheduled_date", "closed_date", "closed_time",
                                 "status", "assigned_person", "brief_description")),
           "PM wo：raw 保真（normalize_wo 會丟掉的四態承重欄一個都不能少）")
        ck(r0 is wx_rows[0], "PM wo：rows 原樣 raw、零複製轉換")
        # ★ S-PM8 承重負斷言：**每一頁都必須帶 limit**——伺服端預設 100，365d 窗真量在單頁內 ⇒
        #   不帶就靜默截斷 10 倍，而最舊的月份會結構性空白、頁面卻說「那時候沒有保養到期」
        ck(f"limit={_PM_WO_LIMIT}" in u and "offset=0" in u and _PM_WO_LIMIT == 1000,
           f"PM wo 負斷言：URL 必含 limit=（伺服端預設 100＝靜默截斷 10 倍），got {u}")
        # ★★ S-PM8 續：**翻頁時絕不帶可變過濾軸**（cmms NOTE-33 §六(b)）——
        #    帶 status 翻頁時，某單在兩頁之間結案就會離開結果集、後面的列往前位移 ⇒ **真漏列**，
        #    而跨頁去重**擋不住**（那列根本沒出現過）。不可變軸（work_type／asset_id／開單日窗）
        #    的漂移方向只會造成重複（新單插頭部、既有列往後位移）⇒ 去重已完備。
        #    這兩條釘子擋的是「日後有人為了少寫一行客戶端過濾而加 status 參數」。
        import inspect as _inspect
        _wo_params = set(_inspect.signature(fetch_pm_work_orders).parameters)
        ck("status" not in _wo_params,
           f"PM wo 結構釘：簽名**不得**出現 status（帶可變過濾軸翻頁＝真漏列、去重擋不住），"
           f"got {sorted(_wo_params)}")
        ck(all("status=" not in u2 for u2 in cap),
           f"PM wo 負斷言：組出的 URL 不得含 status=（狀態一律客戶端過濾），got {cap}")
        # ★ 翻頁：fake 依 offset 回 1000 + 1000 + 300（＝三頁）
        cap = []

        def _wo_pager(pages, cap=cap):
            def _t(url):
                cap.append(url)
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                return (pages.get(int(q.get("offset", ["0"])[0]), []), None)
            return _t

        def _wo_rows_n(prefix, n):
            return [{"work_order_no": "%s%04d" % (prefix, i), "status": "CLOSED"}
                    for i in range(n)]
        _w1, _w2, _w3 = (_wo_rows_n("A", 1000), _wo_rows_n("B", 1000), _wo_rows_n("C", 300))
        res_pg, _ = fetch_pm_work_orders("2026-03-01", "2026-08-04",
                                         transport=_wo_pager({0: _w1, 1000: _w2, 2000: _w3}))
        ck(res_pg["count"] == 2300 and res_pg["pages"] == 3 and res_pg["truncated"] is False
           and res_pg["dedup_n"] == 0,
           f"PM wo：★ 回滿就翻頁、多頁列全到（只打一頁＝最舊的月份結構性空白，"
           f"而頁面只會說「那時候沒有保養到期」），got count={res_pg['count']} "
           f"pages={res_pg['pages']}")
        ck(len(cap) == 3
           and all(f"limit={_PM_WO_LIMIT}" in u2 for u2 in cap)
           and [int(urllib.parse.parse_qs(urllib.parse.urlsplit(u2).query)["offset"][0])
                for u2 in cap] == [0, 1000, 2000],
           f"PM wo：每頁都帶 limit=1000、offset 遞增 0/1000/2000，got {cap}")
        # ★ 跨頁重複（翻頁之間有新單插入 ⇒ 整體位移 ⇒ 同一張單兩頁都出現）→ 去重並計數
        _dup = _wo_rows_n("A", 5) + _wo_rows_n("D", 295)
        res_dd, _ = fetch_pm_work_orders("2026-03-01", "2026-08-04",
                                         transport=_wo_pager({0: _w1, 1000: _dup}, cap=[]))
        ck(res_dd["count"] == 1295 and res_dd["dedup_n"] == 5,
           f"PM wo：跨頁重複 work_order_no 去重、dedup_n 計數（快照語意非交易），"
           f"got count={res_dd['count']} dedup_n={res_dd['dedup_n']}")
        # ★ 抓滿 MAX_PAGES 仍回滿 ⇒ truncated 自揭、**且不再打第 N+1 頁**（絕不無界迴圈）
        cap2 = []
        _small = [{"work_order_no": "S%d" % i} for i in range(2)]

        def _always_full(url):
            cap2.append(url)
            return (_small, None)
        res_mx, _ = fetch_pm_work_orders("2026-03-01", "2026-08-04", limit=2,
                                         transport=_always_full)
        ck(res_mx["pages"] == _PM_WO_MAX_PAGES and res_mx["truncated"] is True
           and len(cap2) == _PM_WO_MAX_PAGES,
           f"PM wo：滿 {_PM_WO_MAX_PAGES} 頁仍回滿 ⇒ truncated 自揭且停手（不無界迴圈），"
           f"got pages={res_mx['pages']} calls={len(cap2)}")
        ck(res_mx["count"] == 2 and res_mx["dedup_n"] == 2 * (_PM_WO_MAX_PAGES - 1),
           f"PM wo：五頁同內容 ⇒ 去重後仍是 2 列（跨頁去重對「排序不穩」是保守側防護），"
           f"got {res_mx['count']}/{res_mx['dedup_n']}")
        # 信封形＝容錯路徑（回歸；cmms 哪天改回家族形不至於整頁黑掉）；**且不翻頁**
        cap3 = []
        res_env, _ = fetch_pm_work_orders(
            "2026-03-01", "2026-08-04",
            transport=make_pm_transport({_PM_WO_PATH: {"items": wx_rows, "truncated": True}},
                                        capture=cap3))
        ck(res_env["count"] == 2 and res_env["truncated"] is True and res_env["pages"] == 1
           and len(cap3) == 1,
           "PM wo：`{items,truncated}` 信封形仍收＝容錯路徑（沿用其 truncated、**視為單頁不翻頁**"
           "——那是容錯路徑不是實形，替沒對版的形狀發明分頁語意才是反模式）")
        res_env2, _ = fetch_pm_work_orders(
            "2026-03-01", "2026-08-04",
            transport=make_pm_transport({_PM_WO_PATH: {"items": wx_rows}}))
        ck(res_env2["count"] == 2 and res_env2["truncated"] is False,
           "PM wo：信封缺 truncated ⇒ 當 False（容錯路徑寬鬆收；實形本來就沒這個欄）")
        for bad in ({"rows": wx_rows}, "nope", 42, None):
            ck(fetch_pm_work_orders("2026-03-01", "2026-08-04",
                                    transport=make_pm_transport({_PM_WO_PATH: bad}))
               == (None, "bad_schema"),
               f"PM wo：裸 list／信封以外的形（{type(bad).__name__}）→ bad_schema 拒讀")
        res4, _ = fetch_pm_work_orders("2026-03-01", "2026-08-04", transport=make_pm_transport(
            {_PM_WO_PATH: wx_rows + [{"asset_id": "EID-1"}, "junk"]}))
        ck(res4["dropped_n"] == 2 and res4["count"] == 2,
           "PM wo：壞列（缺 work_order_no／非 dict）丟棄計數，其餘照收")
        ck(fetch_pm_work_orders("2026-03-01", "2026-08-04", transport=make_pm_transport(
            {_PM_WO_PATH: ("!reason", "timeout")})) == (None, "timeout"),
           "PM wo：timeout 原樣回")

        def _p2fail_wo(url):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            return (_w1, None) if int(q["offset"][0]) == 0 else (None, "timeout")
        ck(fetch_pm_work_orders("2026-03-01", "2026-08-04", transport=_p2fail_wo)
           == (None, "timeout"),
           "PM wo：第二頁失敗 ⇒ **整批失敗**（半批比空批更毒：完成度母體殘缺沒人看得出來、"
           "只會靜默壓低分數；空批至少走 §5 降級卡）")

    # ---------- (P-D) /tasks/{no}/steps：ok / 空陣列 / 404 / 壞形 ----------
    steps_path = _PM_STEPS_PATH_TMPL.format(task_no="YPTR")
    rows, reason = fetch_task_steps("YPTR", transport=make_pm_transport({steps_path: [
        {"proc_seq": 20, "description": "Replace filter",
         "parts": [{"item_code": "FLT-1", "quantity": 1}]},
        {"proc_seq": 10, "description": "Lock out power"},
        {"proc_seq": 30, "description": 42},          # 壞列（description 非 str）→ 丟
    ]}))
    ck(reason is None and [r["proc_seq"] for r in rows] == [10, 20],
       f"PM steps：依 proc_seq 升冪 + 壞列丟棄，got {rows}")
    ck(rows[0]["parts"] == [] and rows[1]["parts"][0]["item_code"] == "FLT-1",
       "PM steps：parts 缺→[]、有料原樣帶")
    ck(fetch_task_steps("YPTR", transport=make_pm_transport({steps_path: []}))
       == ([], None), "PM steps：200 空陣列＝cmms 無細項（不是錯誤）")
    ck(fetch_task_steps("NOPE", transport=make_pm_transport({}))
       == (None, "http_404"), "PM steps：404＝task 不存在（呼叫端翻 gone）")
    ck(fetch_task_steps("YPTR", transport=make_pm_transport({steps_path: {"items": []}}))
       == (None, "bad_schema"), "PM steps：非 list → bad_schema")
    ck(fetch_task_steps("YPTR", transport=make_pm_transport(
        {steps_path: [{"x": 1}, "junk"]})) == (None, "bad_schema"),
       "PM steps：非空清單全數不合形 → bad_schema（**不得靜默塌成空＝謊報無細項**）")
    # ★ proc_seq 混型（int 與 str 並存）：排序**不得外拋**——外拋會被下游 catch-all 收斂成
    #   整卡 error＝細項腿把工單腿一起拖死（兩腿獨立容忍破功）
    mixed, reason = fetch_task_steps("YPTR", transport=make_pm_transport({steps_path: [
        {"proc_seq": "20", "description": "B"}, {"proc_seq": 10, "description": "A"},
        {"proc_seq": "abc", "description": "C"}, {"proc_seq": None, "description": "D"}]}))
    ck(reason is None and [r["description"] for r in mixed] == ["A", "B", "C", "D"],
       f"PM steps：proc_seq int/str/非數字/None 混型不外拋，數值序在前、None 殿後，got {mixed}")
    ck(fetch_task_steps("YPTR", transport=make_pm_transport({steps_path: [
        {"proc_seq": True, "description": "bool"}]}))[0][0]["proc_seq"] is True,
       "PM steps：bool 不當數字排（型別安全鍵不因 True==1 而錯位），列本身照樣帶出")
    nul, reason = fetch_task_steps("YPTR", transport=make_pm_transport({steps_path: [
        {"proc_seq": None, "description": "X"}, {"proc_seq": None, "description": "Y"}]}))
    ck(reason is None and [r["description"] for r in nul] == ["X", "Y"],
       "PM steps：proc_seq 全 null 的 task 照樣帶回步驟（§8.2 可空鐵則）——**丟列或翻 "
       "bad_schema 都是把合法資料謊報掉**；序號欄由消費端顯「—」")

    if _fx_ok("pm_board_fixture_task_steps_YPHYM.v1.json", "pm_board_fixture_task_steps_CAL1DM.v1.json"):
        # ---------- (P-D2) S2 真 fixture round-trip（prod v87 真回應、S-PM3 CLOSED）----------
        # 兩支各釘一個值域：YPHYM＝`parts[].quantity` 全 null（顯「—」不補 0）；
        # CAL1DM＝八步 `proc_seq` 全 10（重複值域全量資料唯一實例 ⇒ **勿當唯一鍵**）。
        for fx_name, task_no, want_n in (
                ("pm_board_fixture_task_steps_YPHYM.v1.json", "YPHYM", 2),
                ("pm_board_fixture_task_steps_CAL1DM.v1.json", "CAL1DM", 8)):
            with open(_osp.join(_osp.dirname(_osp.abspath(__file__)), "_fixtures", fx_name),
                      encoding="utf-8") as f:
                wire = json.load(f)["response"]       # 交檔包裝 → wire 形＝裸 list
            p = _PM_STEPS_PATH_TMPL.format(task_no=task_no)
            got, reason = fetch_task_steps(task_no, transport=make_pm_transport({p: wire}))
            ck(reason is None and len(got) == want_n,
               f"PM steps fixture {task_no}：{want_n} 步全收（真回應零丟列）")
            ck([r["description"] for r in got] == [r["description"] for r in wire],
               f"PM steps fixture {task_no}：**依回傳順序枚舉**——server 已排序、client 不重排、"
               f"更不以 proc_seq 去重（八步同標 10 若當唯一鍵會塌成一步）")
        with open(_osp.join(_osp.dirname(_osp.abspath(__file__)), "_fixtures",
                            "pm_board_fixture_task_steps_YPHYM.v1.json"), encoding="utf-8") as f:
            y_wire = json.load(f)["response"]
        y_got, _ = fetch_task_steps("YPHYM", transport=make_pm_transport(
            {_PM_STEPS_PATH_TMPL.format(task_no="YPHYM"): y_wire}))
        ck(all(p["quantity"] is None for r in y_got for p in r["parts"]),
           "PM steps fixture YPHYM：`parts[].quantity` **原樣 null 帶到消費端**（傳輸層絕不補 0"
           "——補 0 就是把「造冊未清點」謊報成「用量為零」）")
        with open(_osp.join(_osp.dirname(_osp.abspath(__file__)), "_fixtures",
                            "pm_board_fixture_task_steps_CAL1DM.v1.json"), encoding="utf-8") as f:
            a_wire = json.load(f)["response"]
        a_got, _ = fetch_task_steps("CAL1DM", transport=make_pm_transport(
            {_PM_STEPS_PATH_TMPL.format(task_no="CAL1DM"): a_wire}))
        ck(len({r["proc_seq"] for r in a_got}) == 1 and len(a_got) == 8,
           "PM steps fixture CAL1DM：八步 proc_seq 全同值仍是八列（**重複不去重**）")

    # ---------- (P-F) /tasks 人話名稱字典（S-PM4 CLOSED）----------
    tk_wire = [{"task_no": "YPTR", "description": "Plasma Oven Tray Replacement",
                "is_active": True},
               {"task_no": "_3TN905K21", "description": "AttachRig Probe Card Replacement",
                "is_active": True},
               {"task_no": "CAL1DM", "description": "Kestrel Cal PTM Gen1 Daily",
                "is_active": False},          # 停用 task：字典照收（**與殭屍判準無關**）
               {"task_no": "NONAME", "description": None, "is_active": True},
               {"task_no": "", "description": "x"}, {"nope": 1}, "junk"]
    cap = []
    res, reason = fetch_tasks(transport=make_pm_transport({_PM_TASKS_PATH: tk_wire},
                                                          capture=cap))
    ck(reason is None and res["count"] == 4 and res["dropped_n"] == 3,
       f"PM tasks：四列收下（含 description=null 者）、三種壞列丟棄，got {res}")
    ck(any(u.endswith("/tasks?limit=1000") for u in cap),
       "PM tasks：URL 帶 limit=1000（prod 441 筆一頁抓完）")
    by = {r["task_no"]: r["description"] for r in res["rows"]}
    ck(by["_3TN905K21"] == "AttachRig Probe Card Replacement" and by["NONAME"] is None,
       "PM tasks：`description` 就是人話名；非 str → None（**名稱缺不丟列**——丟了別的欄"
       "也一起沒了，而缺名只該讓那一列顯代號）")
    ck("_3TN905K21" in by, "PM tasks：`task_no` opaque（前導底線的 legacy 值零 trim／零正規化）")
    ck(res["schema_id"] == PM_TASKS_SCHEMA_ID and res["truncated"] is False
       and res["limit_hit"] is False, "PM tasks：schema pin + 未回滿 limit 不亂掛 truncated")
    _tfull = [{"task_no": "T%d" % i, "description": "n%d" % i} for i in range(3)]
    r_lim, _ = fetch_tasks(limit=3, transport=make_pm_transport({_PM_TASKS_PATH: _tfull}))
    ck(r_lim["limit_hit"] is True and r_lim["truncated"] is True,
       "PM tasks：回滿 limit → 自揭（名稱缺一半而頁面沒說＝看起來像 cmms 沒填名字）")
    # S-PM5：envelope 未對版 ⇒ 雙收；猜錯只落 bad_schema（名稱層 fail-open、板不受影響）
    r_env, _ = fetch_tasks(transport=make_pm_transport(
        {_PM_TASKS_PATH: {"items": tk_wire, "truncated": False}}))
    ck(r_env["count"] == 4, "PM tasks：S-PM5 未對版 ⇒ 信封形亦收（唯一還雙收的端點）")
    ck(fetch_tasks(transport=make_pm_transport({_PM_TASKS_PATH: "nope"}))
       == (None, "bad_schema"), "PM tasks：形錯 → bad_schema（呼叫端 fail-open 顯代號）")
    for rsn in ("http_500", "timeout", "http_404"):
        ck(fetch_tasks(transport=make_pm_transport({_PM_TASKS_PATH: ("!reason", rsn)}))
           == (None, rsn), f"PM tasks：{rsn} 原樣回（由呼叫端決定 fail-open）")

    if _fx_ok("pm_board_fixture_inventory_items.v1.json"):
        # ---------- (P-G) /inventory-items 零件人話名字典（S-PM6；**兩頁取完**）----------
        ix_path = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                            "_fixtures", "pm_board_fixture_inventory_items.v1.json")
        with open(ix_path, encoding="utf-8") as f:
            # 交檔包裝（`{_meta, request, response_rows}`）→ wire 形＝裸 list；剝殼在讀 fixture 這端
            ix_rows = json.load(f)["response_rows"]
        cap = []
        res, reason = fetch_inventory_items(
            transport=make_pm_transport({_PM_INV_PATH: ix_rows}, capture=cap))
        ck(reason is None and res["count"] == 4 and res["dropped_n"] == 0
           and res["pages"] == 1 and res["truncated"] is False and res["page_error"] is None,
           f"PM inv：prod fixture 四列全收、單頁即完（未回滿 limit）、零丟棄，got {res}")
        ck(res["schema_id"] == PM_INV_SCHEMA_ID, "PM inv：schema pin")
        ck(len(cap) == 1 and cap[0].endswith("/inventory-items?limit=1000&offset=0"),
           f"PM inv：第一頁帶 limit=1000&offset=0；**沒回滿就不打第二頁**（省一次往返），got {cap}")
        by = {r["item_code"]: r for r in res["rows"]}
        ck(by["XC910002"]["name"] == "Pump filter"
           and by["XC910003"]["name"] is None and by["XC910003"]["description"]
           and by["XC910004"]["name"] is None and by["XC910004"]["description"] is None,
           "PM inv：**兩支人話欄原樣帶出**（name 多數為 null＝cmms 實測；顯示鏈由 board_pm "
           "決定＝name→description→item_code，傳輸層不代為挑）")
        # ★ 承重：列數 > limit 1000 ⇒ 必須翻第二頁；只打一頁＝靜默少一批名字
        _p1 = [{"item_code": "A%04d" % i, "name": "n%d" % i} for i in range(1000)]
        _p2 = [{"item_code": "B%04d" % i, "name": "m%d" % i} for i in range(300)]
        cap = []

        def _pager(pages):
            def _t(url):
                cap.append(url)
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                return (pages.get(int(q.get("offset", ["0"])[0]), []), None)
            return _t
        res2, _ = fetch_inventory_items(transport=_pager({0: _p1, 1000: _p2}))
        ck(res2["count"] == len(_p1) + len(_p2) and res2["pages"] == 2 and res2["truncated"] is False,
           f"PM inv：★ 回滿第一頁 ⇒ 打第二頁、全列全到（prod 現值；只打一頁就是靜默少一批"
           f"名字，而頁面看起來完全正常），got count={res2['count']} pages={res2['pages']}")
        ck(len(cap) == 2 and cap[1].endswith("offset=1000"),
           f"PM inv：第二頁 URL 帶 offset=1000（同 limit），got {cap}")
        res3, _ = fetch_inventory_items(transport=_pager({0: _p1, 1000: _p1}))
        ck(res3["count"] == 2000 and res3["truncated"] is True,
           "PM inv：兩頁都回滿（≥2000）⇒ truncated 自揭——現值不會發生，但 cmms 成長破 2000 時"
           "**第三頁的存在必須看得見**（裸 list 沒有 truncated 欄可讀，回滿是唯一訊號）")
        # 第二頁失敗＝**保住第一頁的名字**＋自揭（為了裝飾品的故障丟掉 1000 個名字才是反模式）
        res4, reason4 = fetch_inventory_items(transport=_pager({0: _p1}))   # offset=1000 → []
        ck(reason4 is None and res4["count"] == 1000 and res4["truncated"] is False,
           "PM inv：第二頁空陣列＝真的只有 1000 列（不亂掛 truncated）")

        def _p2fail(url):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            return (_p1, None) if int(q.get("offset", ["0"])[0]) == 0 else (None, "timeout")
        res5, reason5 = fetch_inventory_items(transport=_p2fail)
        ck(reason5 is None and res5["count"] == 1000 and res5["truncated"] is True
           and res5["page_error"] == "timeout" and res5["pages"] == 1,
           f"PM inv：第二頁逾時 ⇒ 保留第一頁 1000 個名字 ＋ truncated ＋ page_error 自揭"
           f"（名稱層 fail-open：少一部分名字只讓那些列顯代號），got {res5['page_error']}")
        # 第一頁失敗／形錯＝這支拿不到 → 呼叫端 fail-open（顯代號）
        for rsn in ("http_500", "timeout", "unreachable", "bad_json"):
            ck(fetch_inventory_items(transport=make_pm_transport(
                {_PM_INV_PATH: ("!reason", rsn)})) == (None, rsn),
               f"PM inv：第一頁 {rsn} 原樣回")
        for bad in ({"items": ix_rows, "truncated": False}, "nope", 42):
            ck(fetch_inventory_items(transport=make_pm_transport({_PM_INV_PATH: bad}))
               == (None, "bad_schema"),
               "PM inv：非裸 list（含家族信封形）→ bad_schema（wire 已由 cmms 明文定案，不雙收）")
        res6, _ = fetch_inventory_items(transport=make_pm_transport(
            {_PM_INV_PATH: ix_rows + [{"name": "no code"}, {"item_code": ""}, "junk"]}))
        ck(res6["count"] == 4 and res6["dropped_n"] == 3,
           f"PM inv：缺碼／空碼／非 dict 三種壞列丟棄計數，got {res6['dropped_n']}")
        # ★ 2026-08-28 欄位白名單放寬（cmms NOTE-63）：`/cost` 第三層要的三欄。
        res6b, _ = fetch_inventory_items(transport=make_pm_transport(
            {_PM_INV_PATH: [{"item_code": "XS910001", "name": "Fuse", "description": "d",
                             "vendor_part_no": "738-2905/ 0154.125DR", "is_obsolete": True,
                             "supplier": "RS Components", "unit_cost": 2.56}]}))
        ck(res6b["rows"][0]["vendor_part_no"] == "738-2905/ 0154.125DR",
           "★★ PM inv：`vendor_part_no` **原值直送**（含空白與斜線；清洗規則住 "
           "board_partsmatch，不在 transport）")
        ck(res6b["rows"][0]["is_obsolete"] is True
           and res6b["rows"][0]["supplier"] == "RS Components",
           "★ PM inv：`is_obsolete`／`supplier` 也留下（NOTE-63 三問全在）")
        ck("unit_cost" not in res6b["rows"][0],
           "★★ PM inv：**`unit_cost` 仍然不留**（NOTE-63【五】3：我方明講不用——"
           "`/cost` 的錢只從 opswiki ledger 來）")
        res7, _ = fetch_inventory_items(transport=make_pm_transport(
            {_PM_INV_PATH: [{"item_code": " EC-1 ", "name": 42, "description": None}]}))
        ck(res7["rows"][0] == {"item_code": " EC-1 ", "name": None, "description": None,
                               "vendor_part_no": None, "supplier": None, "is_obsolete": False},
           "PM inv：item_code **opaque**（零 trim／零正規化——它同時是字典鍵與 cmms 深連結的"
           "路徑段）；非 str 的名稱欄收成 None、**整列不丟**（該列顯代號即可）")

    # ---------- (P-H) /cost/aggregate：ok / 帶對查詢參數 / 零 assets / 壞形 / reason 原樣回 ----------
    ca_cap = []
    ca_payload = {"schema_id": "cmms.cost_aggregate.v1", "rows": [], "pool_rows": []}
    ca_res, ca_reason = fetch_cost_aggregate(
        "2026-01-01", "2026-08-31",
        transport=make_pm_transport({_COST_AGG_PATH: ca_payload}, capture=ca_cap))
    ck(ca_reason is None and ca_res == ca_payload,
       "cost/aggregate：正常回應**原樣**回傳（深層驗證留給 board_runcost.parse_aggregate，"
       "本檔只管網路 ＋ 最外層 dict pin）")
    ck(len(ca_cap) == 1
       and "from_date=2026-01-01" in ca_cap[0] and "to_date=2026-08-31" in ca_cap[0]
       and "bucket=month" in ca_cap[0] and "assets=" not in ca_cap[0],
       f"cost/aggregate：查詢參數帶對，且**零 assets**（§8.1-2 連帶裁決：帶了就沒有「名冊外"
       f"設備」那一格，總額也不再是可信總額），got {ca_cap}")
    ca_cap2 = []
    fetch_cost_aggregate("2026-01-01", "2026-08-31", assets=["EID-1", "EID-2"],
                         transport=make_pm_transport({_COST_AGG_PATH: ca_payload},
                                                     capture=ca_cap2))
    ck("assets=EID-1%2CEID-2" in ca_cap2[0],
       "cost/aggregate：呼叫端明講要切片時才帶 assets（本函式不代為決定要不要付這個代價）")
    for bad in ([1, 2, 3], "nope", 42, None):
        ck(fetch_cost_aggregate("2026-01-01", "2026-08-31",
                                transport=make_pm_transport({_COST_AGG_PATH: bad}))
           == (None, "bad_schema"),
           f"cost/aggregate：非 dict payload（{bad!r}）→ bad_schema（最外層 pin，深層驗證"
           f"不在本檔）")
    for rsn in ("http_500", "http_401", "timeout", "unreachable", "bad_json"):
        ck(fetch_cost_aggregate("2026-01-01", "2026-08-31",
                                transport=make_pm_transport({_COST_AGG_PATH: ("!reason", rsn)}))
           == (None, rsn), f"cost/aggregate：{rsn} 原樣回（同把 fail-closed 紀律）")
    # ★ golden 位元拷貝也能原樣穿過本函式（信封形驗證留給消費端）
    _cag_path = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                          "_fixtures", "contract_cost_aggregate.v1.json")
    if _osp.exists(_cag_path):
        with open(_cag_path, encoding="utf-8") as f:
            _cag_golden = json.load(f)
        g_res, g_reason = fetch_cost_aggregate(
            "2023-01-01", "2026-08-31",
            transport=make_pm_transport({_COST_AGG_PATH: _cag_golden}))
        ck(g_reason is None and g_res is _cag_golden,
           "cost/aggregate：golden fixture 原樣穿過（本函式不解析、不拷貝、不動一個鍵）")

    if _fx_ok("pm_board_fixture_detail_34081.v1.json"):
        # ---------- (P-E) /work-orders/{no}/detail：ok / 404 soft-ref / 壞形 ----------
        dx_path = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                            "_fixtures", "pm_board_fixture_detail_34081.v1.json")
        with open(dx_path, encoding="utf-8") as f:
            dx = json.load(f)
        det_raw = dx.get("detail") or dx.get("rows") or dx
        if isinstance(det_raw, list):
            det_raw = det_raw[0]
        det, reason = fetch_pm_wo_detail(34081, transport=make_pm_transport(
            {_DETAIL_PATH_TMPL.format(wno=34081): det_raw}))
        ck(reason is None and det["work_order_no"] == 34081,
           "PM detail：fixture golden 平鋪 WO 過形狀 pin")
        ck(det is det_raw, "PM detail：原樣 raw（單況呈現在 board_data、不在傳輸層）")
        ck(fetch_pm_wo_detail(99999, transport=make_pm_transport({})) == (None, "http_404"),
           "PM detail：404 原樣回（soft-ref ~7% 已知現實、由呼叫端容忍）")
        ck(fetch_pm_wo_detail(34081, transport=make_pm_transport(
            {_DETAIL_PATH_TMPL.format(wno=34081): {"nope": 1}})) == (None, "bad_schema"),
           "PM detail：缺 work_order_no → bad_schema")

    # ══════════════════════════════════════════════════════════════════════
    # A11 資產排程狀態事件（`/assets/{id}/schedule-state-events`；NOTE-27/31 對版）
    #   golden round-trip＝`_fixtures/contract_asset_schedule_state.v1.json`
    #   ⚠ 該檔是**裸 list 位元拷貝**（wire 形本身），不是 `{_meta,rows}` 交檔包裝形。
    # ══════════════════════════════════════════════════════════════════════
    _A11_EID = "EID-26841"
    _a11_path = A11_SCHED_STATE_PATH_TMPL.format(asset_id=_A11_EID)
    _a11_path_fx = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                             "_fixtures", "contract_asset_schedule_state.v1.json")
    with open(_a11_path_fx, encoding="utf-8") as fh:
        _a11_golden = json.load(fh)
    ck(isinstance(_a11_golden, list) and len(_a11_golden) == 2,
       "A11 golden：檔本身就是裸 list（wire 形位元拷貝、無交檔包裝殼）")

    def _a11_tr(payload, path=_a11_path, reason=None, capture=None):
        def _t(url):
            if capture is not None:
                capture.append(url)
            if reason is not None:
                return (None, reason)
            if not url.endswith(path):
                return (None, "http_404")
            return (payload, None)
        return _t

    _urls = []
    res, reason = fetch_schedule_state_events(_A11_EID,
                                              transport=_a11_tr(_a11_golden, capture=_urls))
    ck(reason is None and res["schema_id"] == A11_SCHED_STATE_SCHEMA_ID and res["count"] == 2,
       f"A11：golden round-trip 過形狀 pin，got {reason}")
    ck(res["rows"] == _a11_golden and res["rows"] is not _a11_golden,
       "A11：rows 原樣 raw（正規化住 board_data／board_dormant）、且不回傳呼叫端的同一個物件")
    ck(_urls and _urls[0].endswith("/assets/EID-26841/schedule-state-events")
       and "?" not in _urls[0],
       f"A11：URL＝{{base}}/assets/{{id}}/schedule-state-events（無 query），got {_urls}")
    ck(res["rows"][0]["effective_date"] == "2025-10-24"
       and res["rows"][0]["ts_effective"][:10] == "2025-10-23",
       "A11：★ golden 本身就是 off-by-one 的真實例（ts_effective 前 10 碼 10-23 vs "
       "台北生效日 10-24）——投影只讀 effective_date")
    # opaque asset_id：含斜線／空白也要能安全成 URL（cmms 資產鍵有 legacy 髒值）
    _u2 = []
    fetch_schedule_state_events("EID/17 071", transport=_a11_tr(None, path="zzz", capture=_u2))
    ck(_u2 and "EID%2F17%20071" in _u2[0],
       f"A11：asset_id 一律 percent-encode（opaque string、含 / 也不得改變路徑段），got {_u2}")

    ck(fetch_schedule_state_events(_A11_EID, transport=_a11_tr([])) [0]["count"] == 0,
       "A11：空 `[]` ＝該資產全程排產中（合法 ok、count=0；**不是查無資料、不分兩路**）")
    ck(fetch_schedule_state_events("EID-99999", transport=_a11_tr(_a11_golden))
       == (None, "http_404"),
       "A11：資產不存在＝404 原樣回（我方只打已知 EID ⇒ 404 是異常、不吞）")
    ck(fetch_schedule_state_events(_A11_EID, transport=_a11_tr({"items": _a11_golden}))
       == (None, "bad_schema"),
       "A11：信封形 → bad_schema（**不雙收**；留第二種形＝永遠照不出 cmms 改形）")
    ck(fetch_schedule_state_events(_A11_EID, transport=_a11_tr(["nope"]))
       == (None, "bad_schema"),
       "A11：列非 dict → bad_schema")
    _bad_state = [dict(_a11_golden[0]), dict(_a11_golden[1], state="retracted")]
    ck(fetch_schedule_state_events(_A11_EID, transport=_a11_tr(_bad_state))
       == (None, "bad_schema"),
       "A11：★ 未知 state 出現在**第二筆** → **整批**拒讀（絕不丟壞列留好列——"
       "丟掉復機那筆，剩下的事件流仍然合法、投影照算，只是答案永遠錯）")
    _predeploy = [{k: v for k, v in r.items() if k != "effective_date"} for r in _a11_golden]
    ck(fetch_schedule_state_events(_A11_EID, transport=_a11_tr(_predeploy))
       == (None, "missing_effective_date"),
       "A11：缺 effective_date＝**部署前訊號**（NOTE-31 additive 補欄）→ 整批不用、"
       "絕不退而求其次截 ts_effective")
    for _bad_d in ("2025-10-4", "2025/10/24", "2025-10-24T00:00:00Z", "2025-13-01", None, 20251024):
        ck(fetch_schedule_state_events(_A11_EID, transport=_a11_tr(
            [dict(_a11_golden[0], effective_date=_bad_d)])) == (None, "missing_effective_date"),
           f"A11：effective_date 非 'YYYY-MM-DD' 合法日 → missing_effective_date（{_bad_d!r}）")
    for _rsn in ("timeout", "unreachable", "http_500", "bad_json"):
        ck(fetch_schedule_state_events(_A11_EID, transport=_a11_tr(None, reason=_rsn))
           == (None, _rsn), f"A11：錯誤族原樣回（{_rsn}）")
    _old_env = {k: os.environ.pop(k, None) for k in
                ("BOARD_CMMS_BASE_URL", "BOARD_CMMS_TOKEN")}
    try:
        ck(fetch_schedule_state_events(_A11_EID) == (None, "not_configured"),
           "A11：未配置＝not_configured 短路（**零網路**）")
    finally:
        for _k, _v in _old_env.items():
            if _v is not None:
                os.environ[_k] = _v
    # 負斷言（結構釘）：本檔任何地方都不得出現 UTC→台北的 +8 小時換算
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        _src = fh.read()
    # ⚠ needle **必須用拼接造出來**——直接寫字面值會讓斷言把自己掃到（恆紅）。
    _n_h8 = "hours=" + str(8) + ")"
    _n_s8 = "+ " + str(8) + " * " + str(3600)
    ck(_n_h8 not in _src and ("timedelta(" + _n_h8) not in _src and _n_s8 not in _src,
       "A11 負斷言：傳輸層零 +8h 換算（日期只讀 effective_date；未來有人「好心」補轉換即紅燈）")

    # ══════════════════════════════════════════════════════════════════════
    # A11 批次現況讀（`/assets/schedule-state`；形狀＝NOTE-33【一】、golden＝NOTE-34）
    #   golden round-trip＝`_fixtures/contract_asset_schedule_state_batch.v1.json`
    #   ⚠ 這支是**信封形** `{items, unknown}`——與上面 per-EID 的裸 list **恰好相反**。
    #   ⚠ 本 fixture 第一筆的 since=2025-10-24 是 cmms **設計期合成值**（NOTE-34【四】）：
    #     它與我方 MES 對帳無關、**不構成任何佐證**。真值＝橋常數那兩個 2023 年的日期
    #     （§9b 已結案），`board_dormant` 負斷言 A6 釘死——別讓這個 10-24 爬進投影層。
    # ══════════════════════════════════════════════════════════════════════
    _b_fx = _osp.join(_osp.dirname(_osp.abspath(__file__)),
                      "_fixtures", "contract_asset_schedule_state_batch.v1.json")
    with open(_b_fx, encoding="utf-8") as fh:
        _b_golden = json.load(fh)
    ck(isinstance(_b_golden, dict) and set(_b_golden) == {"items", "unknown"},
       "A11 batch golden：檔本身就是信封 {items, unknown}（wire 形位元拷貝、無交檔包裝殼）")
    _B_EIDS = ["EID-09670", "EID-27639", "EID-27706", "EID-99999"]
    _b_ok = {"asset_id": "EID-A", "state": "in_production", "since": None, "reason": None}

    def _b_tr(payload, reason=None, capture=None):
        def _t(url):
            if capture is not None:
                capture.append(url)
            if reason is not None:
                return (None, reason)
            return (payload, None)
        return _t

    def _b_echo(url):
        """回「照 scope 每台一筆」的合格回應（切塊案共用）。"""
        got = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["eids"][0].split(",")
        return ({"items": [dict(_b_ok, asset_id=e) for e in got], "unknown": []}, None)

    _u = []
    _r, _x = fetch_schedule_state_batch(_B_EIDS, as_of="2026-08-10",
                                        transport=_b_tr(_b_golden, capture=_u))
    ck(_x is None and _r["schema_id"] == A11_BATCH_SCHEMA_ID and _r["count"] == 3
       and _r["unknown"] == ["EID-99999"] and _r["unknown_n"] == 1
       and _r["pages"] == 1 and _r["explicit"] is True,
       f"A11 batch：golden round-trip 過形狀 pin（3 items ＋ 1 unknown），got {_x} {_r}")
    ck(len(_u) == 1 and "/assets/schedule-state?eids=" in _u[0]
       and "EID-09670%2CEID-27639%2CEID-27706%2CEID-99999" in _u[0]
       and "as_of=2026-08-10" in _u[0],
       f"A11 batch URL＝{{base}}/assets/schedule-state?eids=<逗號串>&as_of=<台北日>，got {_u}")
    _by = {r["asset_id"]: r for r in _r["items"]}
    ck(_by["EID-09670"]["state"] == "not_scheduled"
       and _by["EID-09670"]["since"] == "2025-10-24" and _by["EID-09670"]["reason"],
       "A11 batch 樣本①：長停態（state／since／reason 三欄齊）")
    ck(_by["EID-27639"]["state"] == "in_production"
       and _by["EID-27639"]["since"] == "2026-03-02" and _by["EID-27639"]["reason"] is None,
       "A11 batch 樣本②：**復機**——有事件但現況在產；`since` 是**復機日**不是長停日 "
       "（把 since 一律讀成「長停自」會讓一台在產的機器顯成長停）")
    ck(_by["EID-27706"]["state"] == "in_production" and _by["EID-27706"]["since"] is None,
       "A11 batch 樣本③：無事件＝{in_production, since:null}（**不是查無**、不分兩路）")
    ck(_r["items"][0] is _b_golden["items"][0],
       "A11 batch：items 原樣 raw 帶出（adapter／投影住 board_data＋board_dormant＝一份規則）")

    _u2 = []
    fetch_schedule_state_batch(_B_EIDS, transport=_b_tr(_b_golden, capture=_u2))
    ck(_u2 and "as_of=" not in _u2[0],
       f"A11 batch：as_of 省略 ⇒ query 不帶該參數（＝cmms 用台北今天，我方不自己猜日期），"
       f"got {_u2}")
    _u3 = []
    _r3, _x3 = fetch_schedule_state_batch(
        transport=_b_tr({"items": [], "unknown": []}, capture=_u3))
    ck(_x3 is None and _r3["explicit"] is False and len(_u3) == 1 and "?" not in _u3[0],
       f"A11 batch：eids 省略＝全機隊（無 query、explicit=False），got {_u3}")

    # ---- ★ 兩個零網路短路（呼叫端參數錯，不是 cmms 的問題）----
    _u4 = []
    ck(fetch_schedule_state_batch([], transport=_b_tr(_b_golden, capture=_u4))
       == (None, "empty_eids") and _u4 == [],
       "A11 batch ★：eids=[] ⇒ empty_eids、**零網路**——絕不退化成「省略＝問全機隊」。"
       "呼叫端名冊載不出來時遞空 list，安靜地改問 cmms 全部資產長得完全正常")
    for _bd in ("2026-8-10", "2026-08-10T00:00:00Z", "2026-13-01", 20260810, ""):
        _u5 = []
        ck(fetch_schedule_state_batch(_B_EIDS, as_of=_bd,
                                      transport=_b_tr(_b_golden, capture=_u5))
           == (None, "bad_as_of") and _u5 == [],
           f"A11 batch：as_of 非合法 'YYYY-MM-DD' → bad_as_of、零網路（{_bd!r}）")

    # ---- ★★ 承重檢查一：scope 內每台恆回一筆 ----
    _slim = {"items": [r for r in _b_golden["items"] if r["state"] == "not_scheduled"],
             "unknown": ["EID-99999"]}
    ck(fetch_schedule_state_batch(_B_EIDS, transport=_b_tr(_slim)) == (None, "incomplete_scope"),
       "A11 batch ★★：cmms 若退回「只回有事件的那幾台」省流版 ⇒ incomplete_scope 整批拒讀。"
       "不驗這條的話，沒回來的機台會被讀成「這台在排產」——與「這台我沒問到」長得一模一樣，"
       "正是 A11 要消滅的那類靜默")
    _r6, _x6 = fetch_schedule_state_batch(["EID-A"],
                                          transport=_b_tr({"items": [_b_ok], "unknown": []}))
    ck(_x6 is None and _r6["unknown"] == [] and _r6["unknown_n"] == 0,
       "A11 batch：`unknown`／`unknown_n` **兩鍵恆在**（空也在）——信封存在的唯一理由就是"
       "讓「兩邊名冊漂了」看得見；鍵時有時無等於把它藏回去")

    # ---- ★ >200 自動切塊（不讓伺服器 422）＋任一塊失敗整批不用 ----
    _many = [f"EID-{i:05d}" for i in range(1, 251)]        # 250 台 ⇒ 200 + 50
    _sizes = []

    def _chunk_tr(url):
        res, _ = _b_echo(url)
        _sizes.append(len(res["items"]))
        return (res, None)

    _r7, _x7 = fetch_schedule_state_batch(_many, transport=_chunk_tr)
    ck(_x7 is None and _r7["pages"] == 2 and _sizes == [200, 50] and _r7["count"] == 250
       and [i["asset_id"] for i in _r7["items"]] == _many,
       f"A11 batch ★：去重後 >200 自動切塊（200＋50）並合併——**不讓伺服器回 422 半路死**，"
       f"got pages={_r7['pages']} sizes={_sizes}")
    _seq = {"i": 0}

    def _fail2_tr(url):
        _seq["i"] += 1
        return (None, "timeout") if _seq["i"] == 2 else _b_echo(url)

    ck(fetch_schedule_state_batch(_many, transport=_fail2_tr) == (None, "timeout"),
       "A11 batch：第二塊失敗 ⇒ **整批 None**（半份名冊的著色＝同一頁上混兩份真相）")
    _u8 = []
    fetch_schedule_state_batch(["EID-A", "EID-A", "EID-B", None, ""],
                               transport=_b_tr({"items": [_b_ok,
                                                          dict(_b_ok, asset_id="EID-B")],
                                                "unknown": []}, capture=_u8))
    ck(_u8 and "eids=EID-A%2CEID-B" in _u8[0],
       f"A11 batch：呼叫端重複／空 EID **先去重再送**（>200 的切塊判定也在去重之後），got {_u8}")

    # ---- 不依賴 cmms 的升冪保證 ----
    _shuf = {"items": list(reversed(_b_golden["items"])), "unknown": _b_golden["unknown"]}
    _r9, _x9 = fetch_schedule_state_batch(_B_EIDS, transport=_b_tr(_shuf))
    ck(_x9 is None and {i["asset_id"] for i in _r9["items"]}
       == {i["asset_id"] for i in _b_golden["items"]},
       "A11 batch：items 亂序照樣過——cmms 的 EID 升冪是**便利、不是我方契約**；"
       "我方不排序也不要求排序（消費端一律建 dict 查表）")

    # ---- 形狀族（任一列不合形＝整批拒讀）----
    ck(fetch_schedule_state_batch(_B_EIDS, transport=_b_tr(_b_golden["items"]))
       == (None, "bad_schema"),
       "A11 batch：裸 list → bad_schema（**不雙收**——本支是信封、per-EID 那支才是裸 list）")
    for _lbl, _bad in (
            ("items 非 list", {"items": {}, "unknown": []}),
            ("unknown 缺席", {"items": [_b_ok]}),
            ("列非 dict", {"items": ["nope"], "unknown": []}),
            ("asset_id 空", {"items": [dict(_b_ok, asset_id="")], "unknown": []}),
            ("state 未知(retracted)", {"items": [dict(_b_ok, state="retracted")], "unknown": []}),
            ("since 非合法日", {"items": [dict(_b_ok, since="2026-3-2")], "unknown": []}),
            ("asset_id 重複", {"items": [_b_ok, dict(_b_ok)], "unknown": []}),
            ("unknown 與 items 相交", {"items": [_b_ok], "unknown": ["EID-A"]}),
    ):
        ck(fetch_schedule_state_batch(["EID-A"], transport=_b_tr(_bad)) == (None, "bad_schema"),
           f"A11 batch：{_lbl} → bad_schema（殘缺比空更毒＝整批拒讀，絕不丟壞列留好列）")

    ck(fetch_schedule_state_batch(_B_EIDS, transport=_b_tr(None, reason="http_404"))
       == (None, "http_404"),
       "A11 batch ★：**部署前＝http_404**（NOTE-34【五】實作完成 ≠ 已部署）——呼叫端退回"
       "既有路徑並揭露，**絕不讀成「這批機器都沒登記」**")
    for _rsn in ("timeout", "unreachable", "http_500", "http_422", "bad_json"):
        ck(fetch_schedule_state_batch(_B_EIDS, transport=_b_tr(None, reason=_rsn))
           == (None, _rsn), f"A11 batch：錯誤族原樣回（{_rsn}）")
    _old_env2 = {k: os.environ.pop(k, None) for k in
                 ("BOARD_CMMS_BASE_URL", "BOARD_CMMS_TOKEN")}
    try:
        ck(fetch_schedule_state_batch(_B_EIDS) == (None, "not_configured"),
           "A11 batch：未配置＝not_configured 短路（**零網路**）")
    finally:
        for _k, _v in _old_env2.items():
            if _v is not None:
                os.environ[_k] = _v

    print(f"[board_w8_transport] selftest PASS — {n} 項斷言全過："
          f"not_configured 短路（零網路）；一次式 inline 走 fixture golden 完整管線"
          f"（fetch→normalize→活躍窗、per-person 不外洩）；退階式 list+detail 補齊 + 有界 cap "
          f"誠實少讀 + 個別失敗計 detail_errors 不炸；truncated 旗標傳遞；"
          f"錯誤族 http_<code>/timeout/unreachable/bad_json/bad_schema 全回 (None,reason)；"
          f"壞 WO 丟棄。**PM 板五支全離線過（A9 四輪對版後）：sched **只收裸 list**"
          f"（S-PM1）／wo **裸 list 實形＋limit=1000＋offset 翻頁＋跨頁去重**（S-PM8，"
          f"2026-08-10 live 實測）＋raw 保真＋`opened_on_or_after|before` 新參數名＋舊名負斷言"
          f"（S-PM2）／steps 四態＋兩支 prod 真 fixture round-trip（S-PM3：quantity null 不補 0、"
          f"proc_seq 重複不去重）／tasks 人話名稱字典 fail-open（S-PM4）／detail 404 容忍**；"
          f"**六支＝再加 inventory-items 零件名字典（S-PM6，A9 五輪）：全列兩頁取完"
          f"（offset=0/1000）、≥2000 自揭 truncated、第二頁失敗保留第一頁、item_code opaque**。"
          f"**七支＝再加 A11 `/assets/{{id}}/schedule-state-events`（換源，NOTE-27/31）："
          f"golden 裸 list round-trip（含 ts_effective 10-23 vs effective_date 10-24 的 "
          f"off-by-one 真實例）／空 [] ＝全程排產中（合法非查無）／404 原樣回／信封形不雙收／"
          f"**任一列不合形整批拒讀、絕不丟壞列**／缺 effective_date＝部署前訊號 "
          f"missing_effective_date／asset_id percent-encode／零 +8h 換算負斷言**。"
          f"**八支＝再加 A11 批次現況讀 `/assets/schedule-state`（NOTE-33【一】形狀／"
          f"NOTE-34 golden 2b4e4072… 對版，**cmms 尚未部署 ⇒ 現在打 prod 是 404**）："
          f"信封 {{items,unknown}} golden round-trip（長停／復機／無事件三形）／"
          f"**scope 內每台恆回一筆＝incomplete_scope 承重檢查**（省流版回歸在傳輸層就紅）／"
          f"`unknown`/`unknown_n` 兩鍵恆在／eids=[] ⇒ empty_eids 零網路（絕不退化成問全機隊）／"
          f"as_of 壞日 ⇒ bad_as_of 零網路／>200 自動切塊 200+50 且任一塊失敗整批不用／"
          f"先去重再送／**不依賴 cmms 的 EID 升冪**／裸 list 不雙收**。"
          f"**絕無真 HTTP**（全注入 fake transport）；凍結核心 board_w8 未改。")


# ---------------------------------------------------------------------------
# 沉默點清單（傳輸層被迫自選之處；接通前對版、勿自行發明填補）
# ---------------------------------------------------------------------------
# 承 board_w8.py S1–S12（語意層）。本檔傳輸層沉默點（2026-07-05 A3 對版後狀態）：
# S13 **列表端點＝CLOSED**：真形＝`GET {base}/work-orders/active-in?start=&end=[&asset_id=]`
#     （naive 台北 'YYYY-MM-DD HH:MM:SS'；prod 部署+cmms work_orders.py:86 路由實讀）、
#     回 `{items:[全量形×N], truncated}`（v1 上限 2000；壞格式 422→http_422）。
#     golden＝_fixtures/contract_wo_active_in.v1.json（位元拷貝）。
# S14 **detail 端點＝CLOSED**：`GET {base}/work-orders/{no}/detail` 回**平鋪 WorkOrderDetail**
#     （cmms work_orders.py:136；golden＝contract_wo_detail.v1.json）。原假設路徑恰與真路徑
#     一致；envelope 假設（work_order 包裹）已改平鋪。A3 回全量形→退階路徑正常不觸發（備援留著）。
# S15 **inline vs 退階 判別**＝以列表列有無 `status_history` 鍵自動判（mode="auto"）。A3 真回應
#     恆帶 inline history → 恆走 inline；顯式 mode= 可強制覆蓋（不變）。
# S16 **schema_id pin＝CLOSED（併 S7）**：contract_wo_active_in.v1 / contract_wo_detail.v1＝
#     契約級識別、非 in-band；結構 pin＝items+truncated 兩鍵（list）/work_order_no（detail）；
#     BOARD_CMMS_W8_SCHEMA_ID env override 已移除（無 in-band 值可比）。
# S17 **重試策略**＝單次、僅 transient（timeout/unreachable）、不對 HTTPError/bad_json。
#     若 cmms 對 429/503 期望退避重試，接通後有實測再議（現一律不對 http_<code> 重試）。
# S-PM1～S-PM4（2026-08-04 PM 板 additive）＝**OPEN**，全文見上方 PM 區塊檔頭：
#     `/pm-schedules` envelope 雙收待收斂｜`/work-orders` 日期窗參數名待對版｜
#     `/tasks/{no}/steps` 形狀＝cmms 預告｜task 人話名稱欄未曝。
#     ⇒ cmms push「可接線」時，對版一次即改 `_PM_*` 那幾個常數（語意層零改動）。
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="W8 傳輸層殼（cmms A3 HTTP 讀 API client；static bearer）。"
                    "cmms 端點未上線；現階段 --selftest 離線自驗，plain 只印設定狀態。")
    ap.add_argument("--selftest", action="store_true",
                    help="離線零網路自驗（注入 fake transport + fixture）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    # plain 執行：不連網、只印設定狀態（live 拉取由 Chris 在域內機器跑）
    cfg = _config()
    if cfg is None:
        print("[board_w8_transport] disabled（未設定）：需 env BOARD_CMMS_BASE_URL"
              "（=https://work.example.com）+ BOARD_CMMS_TOKEN（CMMS_READ_API_TOKEN，"
              "Chris 側頻道交付；A3/W8 同一把 bearer）。S13–S16 已對版 CLOSED（2026-07-05）、"
              "設好 env 即插即用。離線驗證：--selftest。")
    else:
        base, _ = cfg
        print(f"[board_w8_transport] configured：base={base}（token 已設、不顯示）。"
              f"HTTP 形狀已對版 CLOSED（2026-07-05 A3；見檔尾 S13–S17）。"
              f"live 拉取由 Chris 在域內機器跑（Claude 不連）。")


if __name__ == "__main__":
    main()
