#!/usr/bin/env python3
"""Tendline Board MCP server — Hermes sidebar 的唯讀領域工具面（v1，§14.11/§15/GOVERNANCE §8）。

> **定位**：`codex exec`（WSL gateway）經 streamable HTTP 打回本 app 的 `/mcp`，
> 只拿得到這裡註冊的 29 個**唯讀**工具——無 SQL、無檔案路徑、無寫入（QG-6 agent 層鐵則）。
> 7 個站/機隊軸（ping/list_stations/fleet_scoreboard/station_overview/station_units/
> yield_summary/updown_now）＋ 2 個設備軸（machine_lookup 解析 EID、equipment_downtime
> 工單停機／維修，皆 §14.4.1 純工單導向、非 MES gap）
> ＋ **2 個語意軸**（list_metrics／explain_metric＝指標登記表 `board_metrics` 的薄封裝，
> 答「怎麼算」；A1b，`SPEC-HERMES-TRUST.md` §4.1）
> ＋ **1 個值軸**（get_metric＝登記表那一筆的**值**；A2，§4.1／§12.2。可達面＝
> `board_metrics.VALUE_PATHS` 的 17 個 `(id, scope)` 組合，其餘誠實回 `no_value_path`）
> ＋ **記錄級 MES 工具**（station_variables／wo_serials_status＝`mes_hermes` 的凍結參數化
> 查詢；A2，§7.5／§7.6。**三支 A2 工具都不上 `/api/board`**，先只給 `/mcp` 觀察一輪）
> ＋ **材料／批號／出貨四支**（serial_genealogy／wo_materials／lot_where_used／shipment_lookup＝
> `mes_material` 的凍結參數化查詢；助理 MES 查詢第三批·包①，§7.6.8。同樣**不上 `/api/board`**）
> ＋ downtime_monthly（`/downtime` 停機月報；#167 P4）
> ＋ **MES 工具包② 九支**（station_failure_pareto／module_die_etest＝`mes_etest`；
> equipment_status_events／equipment_modules／equipment_calibration＝`mes_equipment`；
> segment_data／station_spec_limits／station_retest＝`mes_segment`；wo_route＝`mes_route`；
> §7.6.10。同樣**不上 `/api/board`**）
> ＋ **knowledge_card**（製程／設備知識卡的唯讀檢索＝`board_kb`；#175。文件值、不是量測；
> 同樣**不上 `/api/board`**）。
> **每個工具的回傳都多一個 `provenance` 出處信封**（§3.1 十一鍵；既有欄位一個位元不動＝C-HT9）。
> 工具全部**重用 app.py 既有 TTL 快取路徑**（load_scoreboard/_YB_CACHE/_UD_CACHE…），
> 一次 tool call 最壞＝首暖該快取（與網頁同成本），**絕不新增 raw MES 查詢路徑**（B4 perf 紀律）。

## 設計要點
- **source 由伺服端決定**（env `BOARD_SOURCE`，同 app.DEFAULT_SOURCE）——工具不收 source
  參數，agent 不能自行切 live。每個工具回傳都帶 `source`，persona 要求 demo 時明講合成資料。
- **days clamp 1..30**（防 agent 傳大窗打爆 MES）。
- **站名不猜**：station 類工具對未知 cls 回 `{"error": "unknown_station", ...}` + 已知站清單，
  agent 依 persona 必須先用 `list_stations` 解析、不得補全。
- **誠實降級**：任一資料側失敗 → 該欄 null + `unavailable_reason`，不 fallback 二次重查（FP-8）。
- **循環 import 解法**：工具函式體內才 `import app`（app.py 先 import 本模組掛載；執行期
  tool call 時 app 必已在 sys.modules）。模組頂層只 import mcp SDK + mes_timeline +
  **純常數／純函式姊妹模組**（board_eqmeeting/board_data/board_w8——皆不 import app、
  無 MES 連線、無 app 循環；machine_lookup 本體是純查表工具，靠它們的常數；2026-10-02 起
  加掛的長停 since／原因只 warm-only 窺快取〔`_dormant_lookup`〕、零取數）。

## 掛載（app.py 消費）
- `mcp` instance：stateless_http（每請求獨立、無 server-side session 可劫持）。
- `McpTransportProxy`：ASGI 轉接層 = ①靜態 bearer 閘（env `BOARD_MCP_TOKEN`；未設→503
  fail-closed、錯/缺→401——LAN 上 /mcp 不裸奔）②lifespan 每次啟動換新的 session manager
  （SDK 的 StreamableHTTPSessionManager 一個 instance 只能 run() 一次；cmms 實證 pattern）。

selftest：`python board_mcp.py --selftest`（BOARD_SOURCE=demo 直呼工具函式，零 HTTP、零 MES）。
"""
from __future__ import annotations

import concurrent.futures as _cf
import contextvars
import hashlib
import json as _json
import os
import re
import secrets as _secrets
import threading
import time
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import mes_timeline as mt
import board_eqmeeting as wem     # 純常數（EQMT_CATALOG 固定名冊）+ 純函式；不 import app、無 MES
import board_data as wd           # GEN2_BAY_POSITIONS（CAL Gen2 工位對映）；同上，不 import app
import board_w8 as w8             # 停機工單契約消費端純函式（wo_state 態解析）；stdlib only
import board_dormant as wdorm     # 長停 as-of 投影（純函式、零 I/O、零專案 import）；單機長停 since／原因的退路讀口
import board_metrics as M         # 指標登記表（純常數＋純函式、零 I/O、零 app import）；同上
import core_source as _wrs           # 來源值／進程旗標唯一出處（合成產線 WP-4a；零相依）
import board_kb as kb             # 知識卡唯讀 loader（#175；純標準庫、只讀 repo 內卡片檔、不 import app）

mcp = FastMCP(
    "board",
    # stateless：每個 HTTP 請求獨立（雲端/多 client 友善；bearer 閘逐請求驗、無舊 session 可劫持）
    stateless_http=True,
    # DNS-rebinding Host 檢查是「本機無驗證 server」的防線；Board /mcp 有靜態 bearer 且要吃
    # WSL→Windows host IP 的 Host 頭，allowlist 反而會擋（cmms 同裁決）。
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ---- helpers（純函式，selftest 可直測）---------------------------------------------

def _source() -> str:
    """source 由伺服端 env 決定（demo|snapshot|live；合成進程恆為 synth）；agent 無權切換。
       塌縮規則唯一出處＝`core_source.source_key`：非合成進程與改動前 `s if s in (三值) else "demo"`
       對所有輸入等價（env 設 synth 但進程不是合成進程＝未知值→demo）；合成進程（Q-J）→ synth。"""
    return _wrs.source_key(os.environ.get("BOARD_SOURCE", _wrs.DEFAULT_SOURCE))


def _clamp_days(days, default: int) -> int:
    """days clamp 到 1..30（B4 教訓：大窗=斷崖；30 天已是全 app 驗證上限）。"""
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = default
    return max(1, min(d, 30))


def _known_stations() -> list[str]:
    return list(mt.EQP_STATIONS)


_MH_MOD = None
_MH_MOD_TRIED = False


def _mes_hermes():
    """lazy import `mes_hermes`（未就緒 ⇒ None ＝ 工具誠實回 `unavailable`、不炸整個 MCP）。

    ⚠ **不放在模組頂層**：`mes_hermes` 會 import `mes_qlik`（pyodbc）；`board_mcp` 本身
      必須在沒有 DB driver 的環境也 import 得起來（selftest／demo 前門都靠這條）。
    """
    global _MH_MOD, _MH_MOD_TRIED
    if not _MH_MOD_TRIED:
        _MH_MOD_TRIED = True
        try:
            import mes_hermes as _m
            _MH_MOD = _m
        except Exception as e:                       # noqa: BLE001
            print(f"[board_mcp] mes_hermes unavailable ({type(e).__name__}: {str(e)[:120]})",
                  flush=True)
            _MH_MOD = None
    return _MH_MOD


_MM_MOD = None
_MM_MOD_TRIED = False


def _mes_material():
    """lazy import `mes_material`（同 `_mes_hermes` 的理由：它 import `mes_qlik`＝pyodbc）。"""
    global _MM_MOD, _MM_MOD_TRIED
    if not _MM_MOD_TRIED:
        _MM_MOD_TRIED = True
        try:
            import mes_material as _m
            _MM_MOD = _m
        except Exception as e:                       # noqa: BLE001
            print(f"[board_mcp] mes_material unavailable ({type(e).__name__}: "
                  f"{str(e)[:120]})", flush=True)
            _MM_MOD = None
    return _MM_MOD


def _unknown_station(cls: str) -> dict:
    """未知站名 → 誠實錯誤 + 已知清單（agent 依 persona 不得猜，須回頭 list_stations 解析）。"""
    return {"error": "unknown_station", "given": cls,
            "hint": "call list_stations and use an exact cls value; never guess station names",
            "known_stations": _known_stations()}


def _h(sec) -> float | None:
    """秒 → 小時（1 位小數；None 安全）。窄欄可讀性：agent 拿到的是人類單位。"""
    return round(sec / 3600.0, 1) if isinstance(sec, (int, float)) else None


def _round_h(v) -> float | None:
    """**小時數 → 一位小數**（`None` 保持 `None`）——`/mcp` 工具輸出層的**唯一收口**。

    ★ 2026-09-22（助理準確性包③；`SPEC-HERMES-TRUST.md` §12.13）：2026-09-22 12:45 三輪
      live 實測，助理把 `since_h` 原樣念成 `1849.8461111111112`。那**不是模型亂寫**——
      persona 的 Hard rule 2 明寫「quote as-is、never re-scale」，而工具這一端根本沒有
      round ⇒ 模型照規則辦事就會把 13 位小數念出來。**修在工具端**：persona 那條鐵則
      （數字只引不算）必須留著，能改的只有「送給它的數字長什麼樣」。
    ⚠ `bool` 是 `int` 的子類 ⇒ 明確排除（`True` 變 `1.0` 是無聲的型別事故）。
    ⚠ 只給**小時**欄用。百分比／台數／PH 各有自己的精度，別拿這支去套。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(float(v), 1)


def _trim_util_rec(s: dict) -> dict:
    """稼動計分板站 rec → 窄欄精簡形（欄位名對 mes_timeline fleet_scoreboard 輸出）。
       ★ 零工期站（util_unmeasurable，M4 後遺）：util_pct 給 **null**（不給 0！），避免 Hermes 對
         老闆說「稼動 0%」。此類站 MES 只記瞬時事件、段無時長→段長法稼動結構性不可測（≠沒運轉）。"""
    zlo = bool(s.get("zero_len_only"))
    return {
        "cls": s.get("cls"),
        "util_pct": None if zlo else (round(s["util_strict_pct"], 1)
                                      if isinstance(s.get("util_strict_pct"), (int, float)) else None),
        "producing_h": _h(s.get("producing_sec")),
        "active_window_h": _h(s.get("active_span_sec")),
        "fleet_size": s.get("fleet_size"),
        "n_seg": s.get("n_seg"),
        "util_unmeasurable": zlo,
    }


def _trim_qlik_row(r: dict) -> dict:
    """Qlik grid 站列 → 精簡形（公司口徑；欄名不與 raw 欄混用＝agent 不會誤把兩軌當同一件事）。"""
    return {
        "ord": r.get("ord"), "cls": r.get("cls"), "name": r.get("display"),
        "units": r.get("total"), "pass": r.get("pass"), "fail": r.get("fail"),
        "final_yield_pct": r.get("final_yield_pct"),
        "first_pass_yield_pct": r.get("fpy_pct"),
        "failed_elsewhere_first": r.get("absorbed_elsewhere"),
    }


def _trim_yield_rec(s: dict) -> dict:
    """產出/良率計分板站 rec → 精簡形（RAW v0 欄位，mes_yield build_station_record 輸出）。"""
    return {
        "cls": s.get("cls"),
        "output_units": s.get("output_units"),
        "good_units": s.get("good_units"),
        "has_yield_signal": s.get("has_yield_signal"),
        "fail_pct_raw": s.get("fail_pct_raw"),
        "fpy_raw_pct": s.get("fpy_raw_pct"),
        "final_pass_pct": s.get("final_pass_pct"),
        "rework_pct": s.get("rework_pct"),
    }


# ── D1 雙軌制：站級/線級＝Qlik first-FAIL 公司口徑；機台級＝raw MES。**兩個 caveat 必須分開**
#    （原本三個工具共用一個常數；改文案會讓機台級工具掛上錯的口徑說明）。
_CALIBER_QLIK = "qlik-first-fail"
_CALIBER_RAW_MACHINE = "raw-machine-level"
# 稼動/時間軸＝設備軸 raw（**不是**公司 yield 口徑）。station_overview 的 payload 同時含兩軸 ⇒
# 頂層標 `dual`、逐區塊各自帶軸標籤（結構性保證，不靠 docstring 提醒 agent）。
_CALIBER_UTIL_AXIS = "raw-equipment-axis"
_CALIBER_DUAL = "dual"
# ★ 2026-09-22（助理準確性包①；§12.13）：`updown_now` 的尺。**這是一次 caliber 變更**——
#   舊值 `raw-equipment-axis` 描述的是 MES 段的 interval-union，而這支工具現在報的是
#   `/equipment` 那張板的「此刻」（固定名冊、子工位列、DOWN⟺可計停機的開啟工單、
#   長停另一桶）。名冊與視角換了就是換了一把尺，沿用舊值等於讓讀端以為兩者可以互相對帳
#   ——2026-09-22 三輪 live 實測「6 台 vs 13 台」的誤判正是這樣來的。
_CALIBER_BOARD_NOW = "eqmt-board-now"
_DUAL_CALIBER_NOTE = (
    "This payload mixes TWO calibers on purpose. yield.caliber='" + _CALIBER_QLIK + "' is the "
    "company station/line semantics (see yield.caveat); util.caliber='" + _CALIBER_UTIL_AXIS
    + "' is the raw equipment/time axis (MES segments, interval-union) and is NOT restated in the "
    "company caliber. Never describe utilization/downtime figures as company-caliber, and never "
    "mix a yield caveat into a utilization answer.")

_YIELD_CAVEAT_QLIK = (
    "Company caliber (Qlik 'first FAIL in production line', SALTMARSH PH Engineering Dashboard v3.0): "
    "each unit is marked FAIL only once, at the station of its FIRST career failure (ordered by "
    "record key stpPK over full history since 2023-12-12); every other station shows it as PASS. "
    "Windows anchor on each unit's first attempt time. Some forced-FAIL stations are NOT in the "
    "43-station list, so visible FAIL counts do NOT add up to units lost — see absorbed_total. "
    "Scope: B product line only. first_pass_yield is the raw first-attempt result, not filtered "
    "to Fresh (company report's current behavior). Station totals are per-station — NEVER sum "
    "across stations. This is NOT the same caliber as per-machine yield (see station_units).")

#: ★ 2026-10-02 自評 ⑤：「上個月損失最多的是哪一站」被路由到 `yield_summary`，而它只算**良率損失**。
#:   三損失（停機當量／良率／重工，PH）的站別合計住在 `/monthly`（`board_monthly`）。
#: ★ 2026-10-02 第二批 ③：本工具加性 `monthly_loss` 區塊＝**`/monthly` 頁面同一支讀端**
#:   （`app._mr_docs`→`app._mr_model`＝`board_monthly.month_model`；已凍月讀凍結快照）——零新增 SQL。
#:   ⇒ 這句改成「良率區塊只算良率；問『損失』時用 `monthly_loss`」。下列子字串有釘、勿刪：
#:   `YIELD loss only`、`does NOT include downtime loss or rework loss`、`/monthly`、`not a calendar month`。
_YIELD_LOSS_SCOPE = (
    "The yield block (stations / kpis) covers YIELD loss only: units that failed (company "
    "caliber). It does NOT include downtime loss or rework loss. For 'loss' or 'which station "
    "lost the most', answer from monthly_loss instead: the per-station total of all three losses "
    "(downtime equivalent + yield loss + rework, in PH) for one calendar month, the same model "
    "as the /monthly page. days is a rolling window ending now, not a calendar month.")

#: 第二批 ③：`monthly_loss` 的回答規則（派工單原句的英文；工具 payload 一律英文）。
_MONTHLY_LOSS_CAVEAT = (
    "DEFAULT: answer a 'loss' question with the three-loss total (loss_total_ph = downtime "
    "equivalent + yield loss + rework, PH) and give the split; only when the user names ONE kind "
    "(downtime / yield / rework) answer with that kind. Rows are already ranked by loss - the "
    "monthly model's own loss order (the /monthly page table itself lists stations in line-flow "
    "order and shows the three parts per row): rank_basis 'total' rows first (by loss_total_ph), then "
    "'downtime_hours' rows (no month rate, so no downtime equivalent and no total - ranked by "
    "down_h; never call their total 0), then 'not_measurable' rows (no time ledger; only yield "
    "and rework are known). month is a calendar production month (08:00-08:00 production days); "
    "partial=true means the month is still running. The downtime equivalent is an upper-bound "
    "estimate (backfill by another machine is not modelled). Quote the numbers as given. "
    "TIME WINDOW: provenance.window / params describe the rolling days window of the yield "
    "block, NOT the month of monthly_loss; the period monthly_loss covers is monthly_window "
    "(month, from, to) - state that month when you answer.")
_MONTHLY_LOSS_TOP = 10
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MONTHLY_RANK_BASIS = {0: "total", 1: "downtime_hours", 2: "not_measurable"}   # board_monthly.TIER_*


def _monthly_window(block):
    """`monthly_loss` 區塊 → 加性 `monthly_window`＝{month, from, to, partial}（生產日 08:00-08:00，
       同 caveat 的 calendar production month）；區塊沒帶月份 ⇒ None。`provenance` 十一鍵是 SPEC §3.1
       釘死的 ⇒ 不動信封形狀，掛在 payload 與 `monthly_loss` 並列。"""
    m = (block or {}).get("month")
    if not (isinstance(m, str) and _MONTH_RE.match(m)) or not (block or {}).get("stations"):
        return None
    y, mo = int(m[:4]), int(m[5:7])
    ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return {"month": m, "from": f"{m}-01 08:00:00", "to": f"{ny:04d}-{nm:02d}-01 08:00:00",
            "partial": block.get("partial")}


def _ph_int(v):
    """PH → 整數（頁面 `_mr_ph` 的同一個取整）；None ⇒ None（**絕不 0**，C-MR3）。"""
    return None if v is None else int(round(float(v)))


def _monthly_loss_block(src, month=None) -> dict:
    """`/monthly` 頁面那一份月模型 → `monthly_loss` 區塊（**恆不 raise、零新增 SQL**）。

       · 讀端＝`app._mr_docs`／`app._mr_today`／`app._mr_months`／`app._mr_model`（頁面同一支；
         已凍月讀凍結快照、當月＝部分月）。`month` None ⇒ 最近一個**完整**月（＝「上個月」）。
       · 排序＝模型自己的 `sort_key`（`board_monthly._finish_row`：層級→損失降冪→cls）＝
         SPEC-MONTHLY §1 第一問「哪一站損失最大」的那個序（頁面站表另走 Qlik 流序＝顯示序，
         `app._mr_ctx` 的 `wst.flow_sorted`；損失序沒有被刪、就住在模型裡）。
       · PH 取整同頁面（`_mr_ph`）；`down_h` 一位小數（`_round_h`）。"""
    import app as _app
    base = {"month": None, "partial": None, "as_of": None, "frozen_at": None, "unit": "PH",
            "caveat": _MONTHLY_LOSS_CAVEAT, "stations": None, "n_stations": None,
            "truncated": None, "line": None, "unavailable_reason": None,
            "available_months": None}
    try:
        docs, degraded = _app._mr_docs(src)
        today = _app._mr_today(src)
        opts, cur = _app._mr_months(docs, today)
        full = [m for m in opts if m != cur]
        base["available_months"] = list(opts)
        if month is not None and not (isinstance(month, str) and _MONTH_RE.match(month.strip())):
            base["unavailable_reason"] = "bad_month (use YYYY-MM)"
            return base
        m = month.strip() if isinstance(month, str) else (full[-1] if full else None)
        if m is None:
            base["unavailable_reason"] = ("no complete month in the ledger" if not degraded
                                          else f"ledger not available ({degraded})")
            return base
        if m not in opts:
            base["month"] = m
            base["unavailable_reason"] = "month not available (see available_months)"
            return base
        model, frozen_at = _app._mr_model(docs, m, today, src)
        if not model:
            base["month"] = m
            base["unavailable_reason"] = "no monthly model for this month"
            return base
        route, disp = _app._mr_route()
        rows = []
        for r in (model.get("stations") or []):
            tier = (r.get("sort_key") or [None])[0]
            rows.append({"cls": r.get("cls"), "name": disp.get(r.get("cls")) or r.get("cls"),
                         "rank_basis": _MONTHLY_RANK_BASIS.get(tier, "unknown"),
                         "loss_total_ph": _ph_int(r.get("loss_total_ph")),
                         "down_equiv_ph": _ph_int(r.get("down_equiv_ph")),
                         "yield_loss_ph": _ph_int(r.get("qlik_fail")),
                         "rework_ph": _ph_int(r.get("rework_n")),
                         "loss_excl_downtime_ph": _ph_int(r.get("loss_total_excl_equiv_ph")),
                         "down_h": _round_h(r.get("down_h"))})
        ln = model.get("line") or {}
        base.update(
            month=model.get("month") or m, partial=bool(model.get("partial")),
            as_of=model.get("as_of"), frozen_at=(frozen_at or None),
            stations=rows[:_MONTHLY_LOSS_TOP], n_stations=len(rows),
            truncated=len(rows) > _MONTHLY_LOSS_TOP,
            line={"loss_total_ph": _ph_int(ln.get("loss_total_ph")),
                  "down_equiv_ph": _ph_int(ln.get("down_equiv_ph")),
                  "yield_loss_ph": _ph_int(ln.get("qlik_fail")),
                  "rework_ph": _ph_int(ln.get("rework_n")),
                  "n_stations_rated": ln.get("n_stations_rated")})
        if not rows:
            base["unavailable_reason"] = "no station rows for this month"
        return base
    except Exception as e:                               # noqa: BLE001（揭露腿永不炸工具）
        base["unavailable_reason"] = _err_category(e, "yield_summary.monthly_loss")
        return base

_YIELD_CAVEAT_RAW_MACHINE = (
    "RAW MES caliber (equipment axis, per-machine): includes retests/calibration iterations and "
    "OVERSTATES true defect rate. These per-machine numbers are deliberately NOT restated in the "
    "company first-FAIL caliber — station/line figures (yield_summary, station_overview.yield) "
    "use that other caliber and will not match. Per-machine DISTINCT counts must NEVER be summed "
    "to a station total (§26.10).")

#: ★ 2026-10-02 自評 ③（依據折疊區標 `rw-planned`、本句卻說「cmms 工單作用時數」——哪個對？）：
#:   **兩個都對、講的是不同欄**，舊句只講了一半。核對 `board_eqmeeting.build_row`／`_equipment_body.html`：
#:   · `total_stop_h`／`days[].stop_h`＝工單作用時數逐**日曆日** interval-union（24h 全日軸、**未**裁到
#:     應生產時段）＝頁面「停機」格 tooltip 第二句的「原始」值（`eq_col_stop_raw_tip`）；
#:   · `stop_h_rw`＝同一批工單段 ∩ 應生產時段＝頁面那一格**看得到的** Σ 與 `equip.stop_h` 登記的那把尺；
#:     `avail_pct`／`avail_delta_pp` 也在這條軸上（`rw-planned`）。
#:   ⇒ 信封 `caliber="rw-planned"` 與 `metric_ids` 描述的是 `stop_h_rw`／`avail_pct`；本句把 24h 那兩欄講清楚。
#:   ⚠ 前兩句逐字保留（selftest `ed-caliber`／`ed-fleet-caliber` 釘「cmms work-order」「NOT MES gaps」）。
_DOWNTIME_CALIBER = ("downtime = active cmms work-order hours (interval-union per day); NOT MES gaps. "
                     "A machine with no work order shows 0 here even if it was idle. "
                     "total_stop_h and days[].stop_h count those hours over the whole calendar day "
                     "(24h axis, the page's secondary 'raw' figure); stop_h_rw is the same tickets "
                     "clipped to the expected run window = the hours the /equipment page leads with "
                     "and the basis of avail_pct (null when no run window applies, e.g. a long stop). "
                     "Quote stop_h_rw when you point the user at the page.")

_CALIBER_RW_PLANNED = "rw-planned"   # 應生產時段（RW）軸；分母＝Σ_m |RW(m)|（§2.3；停機/妥善率走這把尺）

_UTIL_UNMEASURABLE_NOTE = ("util_unmeasurable=true: this station's MES segments are instantaneous event "
                           "markers with no duration, so segment-length utilization is structurally "
                           "unmeasurable — util is null, NOT zero. Report it as 'not measurable', never 0%. "
                           "Output/yield are unaffected.")


def _err_category(e, ctx="tool"):
    """例外 → 穩定分類碼（審查 R1 backlog：回 LLM 的錯誤只給類別、不回裸 str(e)＝
       pyodbc 例外含內部主機/DB 名，不帶進 LLM context）。完整例外印 server 終端供除錯。
       分類：timeout（HYT00/逾時）｜db_unavailable（連線/DB 層，含 08001=VPN 斷）｜unavailable（其餘）。"""
    name, s = type(e).__name__, str(e)
    low = s.lower()
    if isinstance(e, TimeoutError) or "hyt00" in low or "timeout" in low:
        cat = "timeout"
    elif name in ("OperationalError", "InterfaceError", "DatabaseError", "ProgrammingError", "Error") \
            or "08001" in low:
        cat = "db_unavailable"
    else:
        cat = "unavailable"
    print(f"[board_mcp] ERR {ctx} → {cat} ({name}: {s[:200]})", flush=True)
    return cat


# ---- 冷載有界等待（2026-10-02 助理自評 ⑥；gateway 一輪 240 秒）----------------------------
#: ★ 病灶（主線 `_logs/board-20261002.log` 02:29–02:41）：`equipment_downtime` 冷呼叫的
#:   `[eqm/body live]` 那一行 `mes_probe≈total`＝三分多鐘（`wos_eids` 與同時段頁面 7 天板不同＝
#:   工具問的是另一個窗、鍵不同＝整張板冷建；夜間暖機帶不跑 `/equipment`）。
#:   拆帳：WO LIST＋窗內明細在 `mt0` 之前＝不到一秒；A10 範圍事件 detail 逐張 ~0.2s、合計十幾秒
#:   （`_scope_src_with_events`）只是零頭；大頭是 `_eqm_rates_by_eid → _eqm_fleet_map →
#:   load_fleet_by_cls(live, 90)` 的逐站 MES `fetch_fleet`（`_FBC_CACHE` TTL 600s 已過期、預算 45s
#:   只在站與站之間檢查、單一查詢逾時上限 `LIVE_QUERY_TIMEOUT_SEC`=180s ⇒ 45＋≤180 秒；每輪只抓到
#:   少數幾站＝log `n_mem`）。`yield_summary(days=30)` 冷呼叫＝`[qlik plover d=30 live]` 約五分鐘
#:   （`load_qlik_grid` 本體；夜間不暖 d=30）。兩支都超過 gateway 240s
#:   ⇒ 使用者拿到 502、而背景查詢照樣跑完（舊行為：工具執行緒本來就不會因 gateway 斷線而停）。
#: ★ 解法＝**同一支既有 loader 丟背景執行緒、工具只等 `_COLD_WAIT_S`**：等得到就照舊回答；
#:   等不到就誠實回 `unavailable_reason="timeout"`＋`hint`（下一步：幾分鐘後再問），背景那一輪
#:   照樣跑完、落進**頁面同一份快取**（loader 自己的 single-flight 鍵）⇒ 下一問是熱的。
#:   **零新增 MES／cmms 查詢、零新 SQL 文本**：跑的就是原本那一支、原本那一次。
#: ⚠ 同鍵在飛就**接上同一個 future**、不另起執行緒（重問不會疊出第二輪掃描）。
#: ⚠ 例外照原樣往上拋（呼叫端既有的 `_err_category` 分支不變）；只有「等不到」才是新分支。
_COLD_WAIT_DEFAULT_S = 90.0
_COLD_INFLIGHT: dict = {}
_COLD_LOCK = threading.Lock()

_COLD_HINT = (
    "Still loading: this is the first request for this window in a while, so the same data the "
    "page uses is being prepared in the background (it can take a few minutes). Ask again in "
    "about {m} minutes. Nothing is implied about any machine or station by this wait.")


def _cold_wait_s() -> float:
    """工具等冷 loader 的秒數上限（env `BOARD_MCP_COLD_WAIT_S`；預設 90、夾 1..600）。
       ⚠ 要遠小於 gateway 的 240 秒：一輪可能連打兩三支工具＋模型本身的時間。"""
    try:
        v = float(os.environ.get("BOARD_MCP_COLD_WAIT_S", _COLD_WAIT_DEFAULT_S))
    except (TypeError, ValueError):
        v = _COLD_WAIT_DEFAULT_S
    return max(1.0, min(v, 600.0))


def _cold_start(key, fn):
    """`fn()` 丟背景 daemon 執行緒 → `Future`。同 `key` 已在飛 ⇒ 回同一個（不疊第二輪）。"""
    with _COLD_LOCK:
        fut = _COLD_INFLIGHT.get(key)
        if fut is not None and not fut.done():
            return fut
        fut = _cf.Future()
        _COLD_INFLIGHT[key] = fut

    def _run():
        try:
            fut.set_result(fn())
        except BaseException as e:                       # noqa: BLE001 —— 原樣交給等待端
            fut.set_exception(e)
        finally:
            with _COLD_LOCK:
                if _COLD_INFLIGHT.get(key) is fut:
                    del _COLD_INFLIGHT[key]

    threading.Thread(target=_run, name=f"board-mcp-cold-{key[0]}", daemon=True).start()
    return fut


def _cold_wait(fut, deadline):
    """等 `fut` 到 `deadline`（`time.monotonic()` 尺）→ `(True, 值)`｜`(False, None)`（等不到）。
       ⚠ `fn` 自己拋的例外**原樣重拋**（含它自己的 TimeoutError——以 `fut.done()` 區分，不靠例外型別）。"""
    try:
        return True, fut.result(timeout=max(0.0, deadline - time.monotonic()))
    except Exception:                                    # noqa: BLE001（Ctrl-C 等不吞）
        if fut.done():
            raise
        return False, None


def _cold_call(key, fn, deadline=None):
    """`_cold_start` ＋ `_cold_wait` 一步做完（`deadline` 省略＝現在＋`_cold_wait_s()`）。"""
    return _cold_wait(_cold_start(key, fn),
                      deadline if deadline is not None else time.monotonic() + _cold_wait_s())


def _cold_hint() -> str:
    """`timeout` 分支給人的下一步（分鐘數隨 env 走、至少 2 分鐘）。"""
    return _COLD_HINT.replace("{m}", str(max(2, int(round(_cold_wait_s() * 2 / 60.0)))))


#: 冷載等不到時的 `unavailable_reason`：沿用 `_err_category` 閉集裡既有的 `timeout`（**不新造值**），
#: 另掛加性布林 `still_loading=True` 讓讀端分得出「背景還在算、等一下會好」與「查詢本身逾時」。
_COLD_REASON = "timeout"


# ---- 出處信封 `provenance`（`SPEC-HERMES-TRUST.md` §3；A1b）---------------------------
#: **C-HT9 加性鐵則**：既有工具的回傳欄位**一個位元不動**，只多 `provenance` 這一鍵。
#: **單一出處**：全檔只有 `_attach()` 那一行把信封掛上 payload（smoke `[metrics]` 用「賦值只出現
#:   一次」的負斷言釘住；第二處＝第二份口徑，必紅）。
#: **C-HT10 穩定**：`evidence_ref` 只吃 tool／正規化參數／`fetched_at` ⇒ 同輸入逐位元相同。
#: ⚠ 信封**只描述這一次呼叫**，不改任何既有數字、也不是第二份口徑；WebMCP 路由直呼同一份
#:   函式（`app.py` 單一口徑鐵則）⇒ **路由層不得補第二份信封**（§3.4）。

#: §3.1 的十一鍵（順序＝SPEC 表；smoke 逐工具比對這個集合）。
PROVENANCE_KEYS = ("tool", "metric_ids", "queries", "window", "fetched_at",
                   "freshness", "row_count", "caliber", "confidence", "page",
                   "evidence_ref")

_TS_FMT = "%Y-%m-%d %H:%M:%S"           # 台北牆鐘 naive（與 mes_now／win_start 同形）
_NO_WINDOW = {"from": None, "to": None}  # 沒有時間窗的工具（ping／list_stations／machine_lookup…）

#: 工具 → 登記表 id。**只放 `board_metrics.ALL_METRICS` 裡真的存在的 id**；
#: 零數字工具＝空 tuple（**絕不編 id**）。smoke 釘：每個 id ∈ `M.ALL_METRICS`。
_TOOL_METRIC_IDS: dict[str, tuple[str, ...]] = {
    "ping": (),
    "list_stations": (),
    "machine_lookup": (),
    "list_metrics": (),
    "explain_metric": (),          # 逐次動態＝被問的那一筆（呼叫端覆寫）
    "get_metric": (),              # 同上（逐次動態；未知 id ⇒ 空 tuple，**絕不編 id**）
    "station_variables": (),       # 登記表裡沒有這一筆（記錄級字典）⇒ **絕不編 id**
    "wo_serials_status": (),       # 同上（記錄級事實）
    "serial_genealogy": (),        # 包①（§7.6.8）：記錄級事實、登記表無此筆 ⇒ **絕不編 id**
    "wo_materials": (),
    "lot_where_used": (),
    "shipment_lookup": (),
    # MES 工具包②（§7.6.10）：九支皆記錄級事實、登記表無此筆 ⇒ **絕不編 id**
    "station_failure_pareto": (),
    "module_die_etest": (),
    "equipment_status_events": (),
    "equipment_modules": (),
    "equipment_calibration": (),
    "segment_data": (),
    "station_spec_limits": (),
    "station_retest": (),
    "wo_route": (),
    "knowledge_card": (),          # #175：文件摘要、登記表無此筆 ⇒ **絕不編 id**
    "fleet_scoreboard": ("util.fleet_util_24h", "util.zero_len_unmeasurable"),
    "station_overview": ("util.fleet_util_24h", "util.zero_len_unmeasurable",
                         "station.quality"),
    "station_units": ("station.unit_bar",),
    "yield_summary": ("station.quality",),
    "equipment_downtime": ("equip.stop_h", "equip.avail_pct", "equip.avail_delta_pp",
                           "equip.now_bucket", "equip.mes_last_h"),
    # ★ 2026-09-22（包①）：換源到 `/equipment` 板之後 payload 裡**沒有** `mes_last_h` 這一格
    #   （板的 Now 欄有，但工具的列不帶它）⇒ 登記表 id 跟著收掉一筆，**不留不實的宣稱**。
    "updown_now": ("equip.now_bucket",),
    # ★ #167 P4：`/downtime` 八筆（頁面與工具同一支 `board_downtime.downtime_model`）
    "downtime_monthly": ("downtime.area_month_h", "downtime.total_h", "downtime.avg_per_machine_h",
                         "downtime.n_wo", "downtime.n_eids", "downtime.top5_share",
                         "downtime.delta_h", "downtime.wo_h"),
}

#: 工具 → 這次實際走到的固定查詢名／loader 名（全部 grep 得到的真名；**零新增 MES 查詢路徑**）。
_TOOL_QUERIES: dict[str, tuple[str, ...]] = {
    "ping": (),
    "list_stations": ("mes_timeline.EQP_STATIONS", "app.pretty_station"),
    "fleet_scoreboard": ("app.load_scoreboard",),
    "station_overview": ("app.load_scoreboard_one", "app.load_qlik_grid",
                         "board_data.qlik_rows_by_cls",
                         # 2026-10-02 自評 ④：`page_value`＝頁面那一份 payload（get_metric 同一路徑）
                         "app.station_axis_payload"),
    "station_units": ("app.load_yield_station", "app.load_scoreboard_one"),
    # 第二批 ③：`monthly_loss`＝`/monthly` 頁面同一支讀端（日帳＋凍結快照；零 MES）。
    "yield_summary": ("app.load_qlik_grid", "app._mr_docs", "app._mr_model"),
    # ★ 2026-10-02：長停登記（`dormant`）＝warm-only 窺板 → 退 `board_dormant.dormant_info`
    #   （A11 事件也只窺、零 HTTP）。三個名字都是既有讀口、**零新增查詢**。
    "machine_lookup": ("board_eqmeeting.EQMT_CATALOG", "board_data.GEN2_BAY_POSITIONS",
                       "app._peek_eqm", "app._peek_a11_events", "board_dormant.dormant_info"),
    # ★ 2026-10-02（自評 ⑥）：先窺頁面已暖的那一份（`_peek_eqm`，同鍵）、冷才 `load_eqm`
    #   （背景＋有界等待）——兩個都是既有讀口、**零新增查詢**。
    # ★ 2026-10-02 第二批 ②：工單文字＝`app.wo_detail_peek`（頁面已抓過的那一份，只窺）；demo＝
    #   `app._ld_wo_detail`（純合成、零 I/O）。**零新增 cmms 呼叫**。
    "equipment_downtime": ("app._peek_eqm", "app.load_eqm", "app.wo_detail_peek",
                           "app._ld_wo_detail"),
    # ★ 2026-09-22（包①）：**warm-only 窺視**（`_peek_eqm`）＝與 `/equipment` 頁面同一份快取；
    #   `load_eqm` 只出現在 demo 那一格（`_peek_eqm` 對 demo 直接即時合成、零成本）。
    #   **工具永不觸發 live 重建／全站 MES 掃描**——聊天一則訊息不該等數十秒。
    "updown_now": ("app._peek_eqm", "app.load_eqm", "app.wo_detail_peek", "app._ld_wo_detail"),
    "list_metrics": ("board_metrics.list_for_page",),
    "explain_metric": ("board_metrics.explain",),
    "get_metric": (),              # 逐次動態＝`VALUE_PATHS` 那一條的 loaders（呼叫端覆寫）
    "station_variables": ("mes_hermes.SQL_CLASS_EXISTS", "mes_hermes.SQL_CLASS_VARS"),
    "wo_serials_status": ("mes_hermes.SQL_WO_HEAD", "mes_hermes.SQL_WO_SERIALS",
                          "mes_hermes.SQL_WO_SEGMENTS"),   # W3b 走到才由呼叫端加
    # 包①（§7.6.8）：真名＝`mes_material.TOOL_QUERY_NAMES`（selftest 逐字比對；頂層不 import
    #   `mes_material`＝它會拉 pyodbc，本檔必須在沒有 DB driver 的環境也 import 得起來）。
    "serial_genealogy": ("mes_material.SQL_SG_B0", "mes_material.SQL_SG_B1",
                         "mes_material.SQL_SG_B6", "mes_material.SQL_SG_A6"),
    "wo_materials": ("mes_material.SQL_WM_HEAD", "mes_material.SQL_WM_SER",
                     "mes_material.SQL_WM_SEG", "mes_material.SQL_WM_A6"),
    "lot_where_used": ("mes_material.SQL_LW_C0", "mes_material.SQL_LW_C1",
                       "mes_material.SQL_LW_C3", "mes_material.SQL_LW_C4A"),
    "shipment_lookup": ("mes_material.SQL_SH_LOT", "mes_material.SQL_SH_WO",
                        "mes_material.SQL_SH_WINDOW"),
    # MES 工具包②（§7.6.10）：真名＝各模組 `TOOL_QUERY_NAMES`（selftest 逐字比對；頂層同樣不 import）。
    "station_failure_pareto": ("mes_etest.SQL_FP_DICT", "mes_etest.SQL_FP_SEG",
                               "mes_etest.SQL_FP_MFL", "mes_etest.SQL_FP_COVER",
                               "mes_etest.SQL_FP_RANK"),
    "module_die_etest": ("mes_etest.SQL_DE_PH", "mes_etest.SQL_DE_DIE",
                            "mes_etest.SQL_DE_DIE_READ", "mes_etest.SQL_DE_PH_REAL",
                            "mes_etest.SQL_DE_DIE_REAL", "mes_etest.SQL_DE_MFL"),
    "equipment_status_events": ("mes_equipment.SQL_EQ_MASTER", "mes_equipment.SQL_EV_SUMMARY",
                                "mes_equipment.SQL_EV_ROWS", "mes_equipment.SQL_EV_REP_COUNT"),
    "equipment_modules": ("mes_equipment.SQL_EQ_MASTER", "mes_equipment.SQL_MOD_EDGES"),
    "equipment_calibration": ("mes_equipment.SQL_EQ_MASTER", "mes_equipment.SQL_CAL_COUNT",
                              "mes_equipment.SQL_CAL_ROWS", "mes_equipment.SQL_CAL_LAST"),
    "segment_data": ("mes_segment.SQL_SD_COUNT", "mes_segment.SQL_SD_SEG_LAST",
                     "mes_segment.SQL_SD_SEG_FIRST", "mes_segment.SQL_SD_EQP",
                     "mes_segment.SQL_SD_REAL", "mes_segment.SQL_SD_INT",
                     "mes_segment.SQL_SD_LIMITS", "mes_segment.SQL_SD_DICT_UNSUPPORTED"),
    "station_spec_limits": ("mes_segment.SQL_SL_LIMITS",),
    "station_retest": ("mes_segment.SQL_RT_SEG", "mes_segment.SQL_RT_LOT",
                       "mes_segment.SQL_RT_SUMMARY", "mes_segment.SQL_RT_TOP"),
    "wo_route": ("mes_route.SQL_RT_HEAD", "mes_route.SQL_RT_ROUTE", "mes_route.SQL_RT_SER",
                 "mes_route.SQL_RT_SEG", "mes_route.SQL_RT_RUN_WO", "mes_route.SQL_RT_RUN_ORDER",
                 "mes_route.SQL_RT_RUN_ND"),
    # #167 P4：讀工單日帳（sqlite；demo＝fixture）＝**零 MES、零 cmms**。
    "downtime_monthly": ("board_downtime.open_source", "board_downtime.downtime_model"),
    # #175：讀 repo 內知識卡檔（唯讀、零 MES、零 SQL）。
    "knowledge_card": ("board_kb.load_index", "board_kb.search"),
}


def _now_ts() -> str:
    """現在（台北牆鐘 naive）。服務跑在台北時區的 Windows 上，全 repo 同一把尺。"""
    return datetime.now().strftime(_TS_FMT)


def _fmt_ts(v) -> str | None:
    """既有欄位的時刻 → `'YYYY-MM-DD HH:MM:SS'`；認不得回 None（**不猜、不補**）。"""
    if isinstance(v, datetime):
        return v.strftime(_TS_FMT)
    if isinstance(v, str):
        s = v.strip().replace("T", " ")
        if len(s) >= 19:
            try:
                return datetime.strptime(s[:19], _TS_FMT).strftime(_TS_FMT)
            except ValueError:
                return None
    return None


def _freshness() -> str:
    """demo／snapshot ＝ 合成或重放 ⇒ 誠實填 `fixture`；只有 live 才叫 `live`（§3.1 enum）。"""
    return "live" if _source() == "live" else "fixture"


def _window_back(fetched_at, days) -> dict:
    """`to`＝取得時刻、`from`＝往前推 days 天；算不出 ⇒ 兩端 None（誠實留白、不猜）。"""
    try:
        end = datetime.strptime(fetched_at, _TS_FMT)
    except (TypeError, ValueError):
        return dict(_NO_WINDOW)
    try:
        d = int(days)
    except (TypeError, ValueError):
        return dict(_NO_WINDOW)
    return {"from": (end - timedelta(days=d)).strftime(_TS_FMT), "to": fetched_at}


def _params_blob(params) -> str:
    """參數的**正規化字串**（`evidence_ref` 與 `params_hash` 共用的唯一一份 normalisation）。

    `sort_keys=True`＝鍵序不影響雜湊；`ensure_ascii=False`＝中文參數不被轉義成 `\\uXXXX`；
    `default=str`＝不可序列化的東西退成字串而不是炸掉（工具參數本來就只有純量）。
    ⚠ **改這一行等於改 `evidence_ref` 的值**（C-HT10 的逐位元穩定性建在它上面）。"""
    return _json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)


def _hash8(blob: str) -> str:
    """sha256 前 8 碼（全檔唯一的雜湊原語）。"""
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


def params_hash(params) -> str:
    """工具參數的 8 碼指紋（`tool_call.params_hash` 欄；§12.12）。

    ＝ `_hash8(_params_blob(params))`。**與 `evidence_ref` 的 `hash8` 共用同一份正規化
    （`_params_blob`）與同一個雜湊原語（`_hash8`），但不是同一個值**——信封那顆的輸入還
    多了 `tool` 與 `fetched_at`（同參數不同時刻要能分辨），而本欄要的正好相反：
    **同參數跨時刻同值**，才比對得出「這一輪跟上一輪問的是不是同一件事」。
    ⇒ 兩者的關係是「同一份配方的兩種取用」，不是「同一個數字」。"""
    return _hash8(_params_blob(params))


def _envelope(tool, *, metric_ids, queries, window, fetched_at, freshness,
              row_count, caliber, confidence, page, params) -> dict:
    """§3.1 的十一鍵信封（**全檔唯一的產生處**）。

    `evidence_ref = "<tool>:<iso-seconds>:<hash8>"`；`hash8 = sha256(tool|參數 JSON|fetched_at)[:8]`
    ⇒ **同工具同參數同 fetched_at 逐位元相同**（C-HT10）。⚠ 與 cmms 的 `evidence: <ref>`
    （D11）**格式不共用**、不得互相假設可解析（§3.3）。"""
    blob = f"{tool}|{_params_blob(params)}|{fetched_at}"
    hash8 = _hash8(blob)
    return {
        "tool": tool,
        "metric_ids": list(metric_ids),
        "queries": list(queries),
        "window": dict(window),
        "fetched_at": fetched_at,
        "freshness": freshness,
        "row_count": row_count,
        "caliber": caliber,
        "confidence": confidence,
        "page": page,
        "evidence_ref": f"{tool}:{fetched_at.replace(' ', 'T')}:{hash8}",
    }


def _attach(tool, out, *, params, caliber=_CALIBER_UTIL_AXIS, page=None, row_count=None,
            window=None, fetched_at=None, freshness=None, degraded=False,
            metric_ids=None, queries=None) -> dict:
    """把信封掛上 payload 並回傳**同一個 dict**（全檔唯一寫 `provenance` 的地方）。

    `confidence` 規則（§3.2／C-HT5）：`degraded`（任一資料側缺／降級）或 `row_count == 0`
    ⇒ `low`，其餘 `high`；**enum 外的值永不出現**，`low` 的下游是補問、不是「這台沒問題」。
    `caliber` 預設 `raw-equipment-axis`＝零數字工具（ping／list_stations／machine_lookup）
    的填法；有數字的工具**一律在呼叫端明寫**自己那把尺。"""
    ts = fetched_at or _now_ts()
    if _wrs.is_synth_process():             # C-SY11／Q-J：合成進程每一則工具回傳都帶合成揭露（非合成：不加鍵）
        out["synthetic_line"] = dict(_SYNTH_DISCLOSURE)
    out["provenance"] = _envelope(
        tool,
        metric_ids=(_TOOL_METRIC_IDS[tool] if metric_ids is None else metric_ids),
        queries=(_TOOL_QUERIES[tool] if queries is None else queries),
        window=(_NO_WINDOW if window is None else window),
        fetched_at=ts,
        freshness=(freshness or _freshness()),
        row_count=row_count,
        caliber=caliber,
        confidence=("low" if (degraded or row_count == 0) else "high"),
        page=page,
        params=params)
    _record_tool_call(tool, params, out)     # ☐2 (a)：有 turn 才落列；失敗自己吞（見該函式）
    return out


# ---- 合成產線（Q-J；WP-4a）：MES 記錄級工具族與知識卡在合成進程回「未提供」＋合成揭露 --------
#: 合成揭露（C-SY11）：合成進程裡每一則工具回傳都掛這一塊（`_attach`）。英文＝與既有 `hint` 同語言。
_SYNTH_DISCLOSURE = {"source": "synth",
                     "note": "SYNTHETIC LINE: this server runs a generated production line on a live clock; "
                             "no figure here is plant data."}   # WP-4b R-B5：去掉 C-SY11 禁字 `real`


def _synth_unavailable(tool, given, params, caliber):
    """合成進程下 MES 記錄級工具／知識卡的回覆：沿用既有的 `error: unavailable` 形（同
       `_material_unavailable`／`_pack2_unavailable`），外加 `unavailable_reason`。"""
    return _attach(tool, {"error": "unavailable", "given": given,
                          "unavailable_reason": "synth_not_provided",
                          "hint": "not provided on the synthetic line (this sandbox has no "
                                  "record-level MES data and no knowledge cards)"},
                   params=params, caliber=caliber, row_count=0, degraded=True)


def _synth_na(caliber):
    """裝飾器：**非合成進程原樣回傳原函式**（同一個物件＝註冊與行為逐位元不變）；合成進程換成
       「未提供」包裝（`functools.wraps`＝工具簽名／說明不變；絕不呼叫原函式＝不讀 fixture、不連 MES）。"""
    def deco(fn):
        if not _wrs.is_synth_process():
            return fn
        import functools
        import inspect
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def _na(*args, **kwargs):
            try:
                b = sig.bind(*args, **kwargs)
                b.apply_defaults()
                params = dict(b.arguments)
            except TypeError:
                params = {"args": list(args), **kwargs}
            given = next(iter(params.values()), None) if params else None
            return _synth_unavailable(fn.__name__, given, params, caliber)
        return _na
    return deco


# ---- ☐2 (a) turn ↔ 工具呼叫的綁定（2026-09-22；`SPEC-HERMES-TRUST.md` §12.12）-----------
#: gateway 每次 `codex exec` 多帶一條 `-c mcp_servers.board.http_headers.X-Board-Turn="<id>"`
#: ⇒ codex 打回來的**每一個** `/mcp` 請求都帶 `X-Board-Turn`。`McpTransportProxy` 在**認證閘之後**
#: 把它放進這個 contextvar，`_attach` 是唯一的讀取點 ⇒ 工具本身一位元不必改。
#: **沒有 header ⇒ `None` ⇒ 零列**（cmms／Telegram／selftest／`/api/board` 直呼都屬此類；
#: 與 `ROADMAP.md` #159 原句「沒有＝provenance 記 cmms」**不同**，本包裁定不寫列——
#: 一張 turn 綁定表裡塞沒有 turn 的列，讀端第一件事就是要把它們濾掉）。
Board_TURN: contextvars.ContextVar[int | None] = contextvars.ContextVar("board_turn", default=None)


def _record_tool_call(tool, params, out) -> None:
    """把這一次工具呼叫落進 `board_assistant.tool_call`（**全檔唯一的寫入點**）。

    三條鐵則：
      ① **沒有 turn 就什麼都不做**（連 import 都不做）——零 header 的呼叫端零成本。
      ② **整段包 try/except：觀測動作不得害死一次工具呼叫**（同 app 側 `record_prompt_stats`
         ／`llm_budget_commit` 的自帶 try 紀律）。永不 re-raise、永不改 `out`。
      ③ `import board_assistant` **在函式體內**（模組頂層只 import 純姊妹模組＝本檔的
         循環 import 紀律；`board_assistant` 開 sqlite 連線，不該在 import 期就拉進來）。

    存的 `result` ＝ **去掉 `provenance` 之後的 payload**（信封另存一欄，不重複）。
    """
    turn = Board_TURN.get()
    if turn is None:
        return
    try:
        import board_assistant as _wa
        _wa.tool_call_record(turn, tool, params_hash(params), out["provenance"],
                             {k: v for k, v in out.items() if k != "provenance"})
    except Exception:                       # noqa: BLE001 —— 刻意吞掉（見鐵則 ②）
        pass


# ---- EID 解析（純函式；machine_lookup / equipment_downtime / updown_now 共用）------------

def _norm_eid(raw) -> str | None:
    """任意輸入 → canonical 'EID-#####'（數字補零成 5 碼）；無法解析→None。
       接受 '11860' / 'eid-11860' / 'EID-11860' / 'EID11860'（大小寫、空白容忍）。
       數字部位非純數字（垃圾輸入）→ None（絕不猜）。>5 碼保留原長（zfill 不截）。"""
    if raw is None:
        return None
    s = str(raw).strip().upper().replace(" ", "")
    if s.startswith("EID-"):
        s = s[4:]
    elif s.startswith("EID"):
        s = s[3:]
    if not s.isdigit():
        return None
    return "EID-" + s.zfill(5)


# area_key → 人類 Area 標籤（與設備頁一致，源自 wem.AREAS）。
_AREA_LABELS = {k: label for k, label in wem.AREAS}

# CAL Gen2 工位 EID → (父機 EID, 工位顯示名)（reverse map of GEN2_BAY_POSITIONS）。
_GEN2_BAYS = {}
for _p, _mods in wd.GEN2_BAY_POSITIONS.items():
    for _meid, _mname, _ratio in _mods:
        _GEN2_BAYS[_meid] = (_p, _mname)
_GEN2_PARENTS = set(wd.GEN2_BAY_POSITIONS.keys())

# eid → (area_key, display_name)（EQMT_CATALOG 首見；現目錄 EID 全唯一）。
_CATALOG_IDX = {e[2]: (e[0], e[1]) for e in wem.EQMT_CATALOG}


# ---- tools（全唯讀；sync def → FastMCP 丟 threadpool，pyodbc 同步無妨）---------------

@mcp.tool()
def ping() -> dict:
    """Health check (read-only). Returns server status and active data source."""
    return _attach("ping", {"status": "ok", "app": "board", "source": _source()},
                   params={})


@mcp.tool()
def list_stations() -> dict:
    """List all known production stations (canonical MES class names). Call this FIRST to
    resolve a station name the user mentions — never guess or fabricate a cls value."""
    import app as _app
    stations = [{"cls": c, "name": _app.pretty_station(c)} for c in _known_stations()]
    return _attach("list_stations",
                   {"source": _source(), "anchor_cls": mt.CAL_CLASS, "stations": stations},
                   params={}, row_count=len(stations))


@mcp.tool()
def fleet_scoreboard(days: int = 3) -> dict:
    """Line-wide equipment-utilization scoreboard across all stations (equipment view,
    interval-union). days is clamped to 1..30. Utilization here = producing time / active window."""
    import app as _app
    d = _clamp_days(days, 3)
    src = _source()
    sb = _app.load_scoreboard(src, d, 15)
    fl = (sb or {}).get("fleet") or {}
    stations = [_trim_util_rec(s) for s in ((sb or {}).get("stations") or [])]
    out = {
        "source": src, "days": d, "mes_now": (sb or {}).get("mes_now"),
        "fleet": {"util_pct": round(100 * fl["util"], 1) if isinstance(fl.get("util"), (int, float)) else None,
                  "n_stations": fl.get("n_stations"), "n_active_stations": fl.get("n_active_stations"),
                  "n_machines": fl.get("n_machines"), "producing_h": _h(fl.get("producing_sec"))},
        "stations": stations,
    }
    if any(s.get("util_unmeasurable") for s in stations):     # 任一零工期站 → 頂層散文（防 Hermes 對老闆說 util 0%）
        out["util_note"] = _UTIL_UNMEASURABLE_NOTE
    ts = _fmt_ts((sb or {}).get("mes_now")) or _now_ts()      # 取得時刻＝資料的 mes_now，不是回應時刻
    return _attach("fleet_scoreboard", _app.json_safe(out), params={"days": d},
                   caliber=_CALIBER_UTIL_AXIS, page="/utilization",
                   row_count=len(stations), window=_window_back(ts, d), fetched_at=ts,
                   degraded=not stations)


@mcp.tool()
def station_overview(cls: str, days: int = 7) -> dict:
    """One station's utilization + station-level output/yield. cls must be an exact value from
    list_stations. Either side may be unavailable — that is reported honestly, not guessed.
    DUAL CALIBER (top-level caliber="dual"): the yield/output block carries
    yield.caliber="qlik-first-fail" (COMPANY station/line semantics); the utilization block carries
    util.caliber="raw-equipment-axis" (raw MES time) — never call the utilization figures
    company-caliber. Per-machine numbers (station_units) are raw MES too and will NOT match yield.
    LINKED PAGE: util.util_pct = producing / active span; the /station page shows utilization on
    the expected-run-window axis instead. page_value (with page_caliber) is the page's own
    figure; when you link the page, say which denominator your number uses (see page_note)."""
    import app as _app
    if cls not in _known_stations():
        return _attach("station_overview", _unknown_station(cls),
                       params={"cls": cls, "days": days}, row_count=0, degraded=True)
    d = _clamp_days(days, 7)
    src = _source()
    _deadline = time.monotonic() + _cold_wait_s()      # 頁面那一格（`page_value`）最多等到這裡
    # ★ 這個工具的 payload **本身就是雙軌**：yield 區塊＝公司口徑、util 區塊＝raw 設備軸。
    #   頂層因此**不掛單一 caliber**（掛了 agent 會把稼動數字也當公司口徑轉述）——改成
    #   `caliber="dual"` + 逐區塊自帶軸標籤（`yield.caliber` / `util.caliber`）＝結構性保證，
    #   不靠 docstring 的口頭提醒。
    out = {"source": src, "cls": cls, "days": d, "caliber": _CALIBER_DUAL,
           "caliber_note": _DUAL_CALIBER_NOTE}
    try:
        rec = _app.load_scoreboard_one(src, cls, d, 15)
        out["util"] = _trim_util_rec(rec) if rec else None
        if out.get("util"):
            out["util"]["caliber"] = _CALIBER_UTIL_AXIS
        if rec is None:
            out["util_unavailable_reason"] = "no activity in window (honest blank, not zero)"
        elif rec.get("zero_len_only"):                # 零工期站：util null 不可測（防「0%」誤述）
            out["util_note"] = _UTIL_UNMEASURABLE_NOTE
    except Exception as e:  # 誠實降級：不重查第二次（FP-8）
        out["util"] = None
        out["util_unavailable_reason"] = _err_category(e, "station_overview.util")
    # 站級 output/yield ＝ Qlik 口徑該站列（D2）；不在 43 站白名單／grid 不可用 → 誠實 null + 原因，
    # **絕不回退 raw**（回退會讓 agent 把兩種口徑混講）。
    try:
        import board_data as _wd
        grid = _app.load_qlik_grid(src, d)
        qrow = _wd.qlik_rows_by_cls(grid).get(cls)
        out["yield"] = _trim_qlik_row(qrow) if qrow else None
        if out["yield"]:      # 軸標籤與 caveat 掛在**這個區塊**（頂層是 dual、不再單軌）
            out["yield"]["caliber"] = _CALIBER_QLIK
            out["yield"]["caveat"] = _YIELD_CAVEAT_QLIK
        if qrow is None:
            out["yield_unavailable_reason"] = (
                grid.get("unavailable") or "station not in the 43-station company-caliber list "
                "(no company-caliber yield exists for it)")
    except Exception as e:
        out["yield"] = None
        out["yield_unavailable_reason"] = _err_category(e, "station_overview.yield")
    # ★ 2026-10-02 自評 ④：連結頁 `/station/{cls}` 的稼動＝**應生產時段軸**（`station.utilization`），
    #   本工具 `util.util_pct`＝活躍跨度分母 ⇒ CAL 自評時兩個數差了一倍多，助理卻沒講。
    #   加性五欄：頁面那一格的值（**讀頁面同一份 payload**＝`get_metric` 的 `app.station_axis_payload`
    #   路徑 `_gm_value`，不重算）＋口徑＋一句 `page_note`。取不到 ⇒ 值 null＋理由（不猜、不回退）。
    #   ⚠ 揭露腿自己的等待上限 `_PAGE_VALUE_WAIT_S`（短於冷載上限）：站頁 payload 冷時會跑該站
    #     timeline（＝開站頁的同一份成本），不讓「附帶一格」把整支工具拖到 90 秒。
    out.update(_station_page_value(src, cls, d, out.get("util"),
                                   min(_deadline, time.monotonic() + _PAGE_VALUE_WAIT_S)))
    # 信封的 caliber 跟著 payload 走＝`dual`（兩軸並存，逐區塊自帶軸標籤；頂層不掛單一口徑）。
    ts = _now_ts()
    n_rows = (1 if out.get("util") else 0) + (1 if out.get("yield") else 0)
    return _attach("station_overview", _app.json_safe(out), params={"cls": cls, "days": d},
                   caliber=_CALIBER_DUAL, page=f"/station/{cls}", row_count=n_rows,
                   window=_window_back(ts, d), fetched_at=ts,
                   degraded=bool(out.get("util_unavailable_reason")
                                 or out.get("yield_unavailable_reason")))


@mcp.tool()
def station_units(cls: str, days: int = 7) -> dict:
    """Per-machine (EID) output/yield breakdown inside one station — RAW MES caliber
    (caliber="raw-machine-level", equipment axis). cls must come from list_stations.
    Per-machine DISTINCT counts must NEVER be summed to a station total (§26.10), and they
    will NOT match the station/line company-caliber figures (yield_summary) by design."""
    import app as _app
    if cls not in _known_stations():
        return _attach("station_units", _unknown_station(cls),
                       params={"cls": cls, "days": days}, row_count=0, degraded=True)
    d = _clamp_days(days, 7)
    src = _source()
    try:
        ys = _app.load_yield_station(src, cls, d)
    except Exception as e:
        ts = _now_ts()
        return _attach("station_units",
                       {"source": src, "cls": cls, "days": d, "machines": None,
                        "unavailable_reason": _err_category(e, "station_units")},
                       params={"cls": cls, "days": d}, caliber=_CALIBER_RAW_MACHINE,
                       page=f"/station/{cls}", row_count=0,
                       window=_window_back(ts, d), fetched_at=ts, degraded=True)
    ms = ((ys or {}).get("station") or {}).get("machines") or []
    out = {
        "source": src, "cls": cls, "days": d,
        "caliber": _CALIBER_RAW_MACHINE, "caveat": _YIELD_CAVEAT_RAW_MACHINE,
        "nosum_note": "per-machine DISTINCT module counts; never sum machines to a station total (§26.10)",
        "machines": [{"eid": m.get("eid"), "desc": m.get("desc"),
                      "output_units": m.get("output_units"), "good_units": m.get("good_units"),
                      "n_seg": m.get("n_seg"), "fail_pct_raw": m.get("fail_pct_raw")} for m in ms],
    }
    # 零工期站（M4 後遺）：站級稼動不可測 → 加性旗（此工具不報 util、但讓 agent 知道別對此站說 util 0%）。
    try:
        urec = _app.load_scoreboard_one(src, cls, d, 15)   # 快取路徑（overview 同源）；失敗→靜默不加旗
        if urec and urec.get("zero_len_only"):
            out["util_unmeasurable"] = True
            out["util_note"] = _UTIL_UNMEASURABLE_NOTE
    except Exception:
        pass
    ts = _now_ts()
    return _attach("station_units", _app.json_safe(out), params={"cls": cls, "days": d},
                   caliber=_CALIBER_RAW_MACHINE, page=f"/station/{cls}", row_count=len(ms),
                   window=_window_back(ts, d), fetched_at=ts)


#: `monthly_loss` 區塊帶了站列 ⇒ 信封加引這一筆（**只在真的帶了才引**；絕不編 id）。
_MONTHLY_LOSS_METRIC = "monthly.station_loss_total_ph"


def _with_monthly_ids(base, block):
    return tuple(base) + ((_MONTHLY_LOSS_METRIC,) if (block or {}).get("stations") else ())


@mcp.tool()
def yield_summary(days: int = 3, month: str | None = None) -> dict:
    """Line-wide yield/output in the COMPANY caliber (caliber="qlik-first-fail"): the 43 stations
    in line order, plus the 7 rolled-throughput-yield KPIs (OVERALL / PH / PC / PPC / BEFORE_DA /
    FROM_DA / VOQC) and the "absorbed" count of units that failed at stations NOT in this list.
    ALWAYS use kpis.OVERALL.pct when asked for "line yield". days clamped to 1..30.
    Station "units" are per-station — never sum them. A null pct means no data, NEVER 100%.
    Per-machine yield (station_units) is a different, raw caliber and will not match.
    LOSS SCOPE: the stations / kpis blocks are YIELD loss only (failed units). For "loss" / "which
    station lost the most", use monthly_loss: per-station three-loss total (downtime equivalent
    + yield loss + rework, PH) for one calendar month, ranked by loss (the /monthly model's
    loss order) - answer
    with loss_total_ph and give the split unless the user names one kind (see
    monthly_loss.caveat). month = "YYYY-MM" (optional; default = the last complete month, i.e.
    "last month"). days is a rolling window ending now, not a calendar month. The envelope
    window describes that rolling days window; the period monthly_loss covers is monthly_window.
    still_loading=true (unavailable_reason "timeout") = being prepared in the background; ask again
    in a few minutes (NOT zero yield)."""
    import app as _app
    import board_data as _wd
    d = _clamp_days(days, 3)
    src = _source()
    # ★ 第二批 ③：月三損失（頁面同一支讀端、日帳＝零 MES）——三個回傳分支都帶（良率腿冷／不可用
    #   不影響這一塊）。`month` 只在給了時進信封參數＝既有呼叫的 evidence_ref 逐位元不變。
    _ml = _monthly_loss_block(src, month)
    _yp = {"days": d} if month is None else {"days": d, "month": month}
    # ★ 2026-10-02 自評 ⑥：`load_qlik_grid` 冷載 d=30 約五分鐘（> gateway 240s）⇒ 背景建＋有界等待；
    #   同一支 loader、同一個快取鍵（頁面／landing／MCP 共用）＝零新增查詢。
    loaded, grid = _cold_call(("qlik", src, d), lambda: _app.load_qlik_grid(src, d))
    if not loaded:
        ts = _now_ts()
        return _attach("yield_summary", _app.json_safe({
            "source": src, "days": d, "caliber": _CALIBER_QLIK,
            "stations": None, "kpis": None, "absorbed_total": None,
            "unavailable_reason": _COLD_REASON, "still_loading": True, "hint": _cold_hint(),
            "caveat": "Figures withheld — still being prepared for this window (NOT zero yield).",
            "loss_scope": _YIELD_LOSS_SCOPE,
            "monthly_loss": _ml, "monthly_window": _monthly_window(_ml),
        }), params=_yp, caliber=_CALIBER_QLIK, page="/yield", row_count=0,
            window=_window_back(ts, d), fetched_at=ts, degraded=True,
            metric_ids=_with_monthly_ids(_TOOL_METRIC_IDS["yield_summary"], _ml))
    if not _wd.qlik_ok(grid):
        ts = _now_ts()
        return _attach("yield_summary", _app.json_safe({
            "source": src, "days": d, "caliber": _CALIBER_QLIK,
            "stations": None, "kpis": None, "absorbed_total": None,
            "unavailable_reason": grid.get("unavailable") or "no data",
            "caveat": "Figures withheld — no company-caliber data for this window (NOT zero yield).",
            "loss_scope": _YIELD_LOSS_SCOPE,
            "monthly_loss": _ml, "monthly_window": _monthly_window(_ml),
        }), params=_yp, caliber=_CALIBER_QLIK, page="/yield", row_count=0,
            window=_window_back(ts, d), fetched_at=ts, degraded=True,
            metric_ids=_with_monthly_ids(_TOOL_METRIC_IDS["yield_summary"], _ml))
    kp = {k: {"pct": (v or {}).get("pct"), "n_stations": (v or {}).get("n_stations")}
          for k, v in (grid.get("kpis") or {}).items()}
    rows = [_trim_qlik_row(s) for s in (grid.get("stations") or [])]
    # 取得時刻／時間窗都沿用 grid 自己那一組（`cap`／`win_start`／`win_end`），不另造第二套。
    ts = _fmt_ts(grid.get("cap")) or _now_ts()
    w_from, w_to = _fmt_ts(grid.get("win_start")), _fmt_ts(grid.get("win_end"))
    win = {"from": w_from, "to": w_to} if (w_from and w_to) else _window_back(ts, d)
    return _attach("yield_summary", _app.json_safe({
        "source": src, "days": d,
        "win_start": grid.get("win_start"), "win_end": grid.get("win_end"),
        "as_of": grid.get("cap"),
        "caliber": _CALIBER_QLIK, "caveat": _YIELD_CAVEAT_QLIK,
        "kpis": kp,
        "absorbed_total": grid.get("absorbed_total"),
        "absorption": grid.get("absorption") or [],
        "stations": rows,
        "loss_scope": _YIELD_LOSS_SCOPE,
        "monthly_loss": _ml, "monthly_window": _monthly_window(_ml),
    }), params=_yp, caliber=_CALIBER_QLIK, page="/yield", row_count=len(rows),
        window=win, fetched_at=ts,
        metric_ids=_with_monthly_ids(_TOOL_METRIC_IDS["yield_summary"], _ml))


@mcp.tool()
def machine_lookup(eid: str) -> dict:
    """Resolve one equipment ID (EID) to its name, area, and Gen2-bay status.
    ALWAYS call this FIRST for any per-machine (EID) question — never enumerate
    stations (station_units) to find an EID. No fetch: it reads the fixed
    equipment-meeting catalog, the CAL Gen2 bay map, and (warm-cache peek only) the
    long-stop registration. Accepts loose input ("11860", "eid-11860", "EID-11860");
    normalizes to canonical "EID-#####". Garbage input is reported honestly
    (error:"bad_eid"); an unknown EID is NEVER guessed. If the machine is a registered
    long stop (not scheduled for production), `dormant` = {since, reason, note}: quote the
    date and the reason verbatim — it is a recorded decision, not a fault and not DOWN.
    `dormant` null = no long-stop registration; `dormant_registry` says whose registration
    it is ("unavailable" = the registry could not be read, NOT "in production").
    SEATS: `dormant_seats` lists this machine's seat-level (nest / sub-unit) long stops
    {parent_eid, unit, label, since, reason, dormant_registry, note} - reason null means no
    reason was filled in (say so, never invent one); [] = none; null = the /equipment board
    was not available so seats were not checked (NOT "none"). A seat ID such as
    "EID-11860-N2" resolves to its parent machine plus a `seat` block. After
    resolving, use equipment_downtime(eid=...) for downtime / repairs / availability, or
    updown_now(eid=...) for current status."""
    norm = _norm_eid(eid)
    _p = {"eid": eid}                         # 信封的參數＝**使用者原輸入**（正規化前），才對得回那一次呼叫
    # ★ 2026-10-02 第二批 ①：座 id 形（`EID-11860-N2`）⇒ 解析成母機、照母機回答＋加性 `seat` 區塊
    #   （座不是目錄上的一台；它的長停／停機在母機回應的 `dormant_seats`／`down_seats` 裡）。
    seat = _seat_input(eid) if norm is None else None
    if seat is not None:
        norm = seat["parent_eid"]
    if norm is None:
        return _attach("machine_lookup",
                       {"error": "bad_eid", "given": eid,
                        "hint": "EID must look like 11860 / eid-11860 / EID-11860 (numeric)"},
                       params=_p, row_count=0, degraded=True)
    src = _source()
    # ★ 2026-10-02（R41 §12.1-7 缺口）：長停 since／原因＝`_dormant_lookup`（warm-only、零取數）。
    #   加性兩欄 `dormant`／`dormant_registry`，正常三分支都掛；未命中分支（不在目錄）也掛——
    #   cmms 可能登了一台不在本板名冊的機台（`unlisted_long_stop_registrations` 同一題）。
    dv, dreg = _dormant_lookup(norm, src)
    # ★ 2026-10-02 第二批 ①：這台的**座級**長停（板上座列；板窺不到 ⇒ None＝沒查到、不是「沒有」）。
    _bd = _peek_board_quiet(src)
    _dk = {"dormant": dv, "dormant_registry": dreg,
           "dormant_seats": (_dormant_seats_view(_bd, norm) if _bd else None)}
    if seat is not None:
        _dk["seat"] = dict(seat, note=(
            "The input is a seat (nest) of " + seat["parent_eid"] + "; this answer is about its "
            "parent machine. That seat's long-stop registration, if any, is the dormant_seats "
            "entry whose unit is '" + str(seat["unit"]) + "'; a seat that is DOWN is listed by "
            "updown_now in down_seats."))
    _ids = _with_dormant_ids((), dv or _dk["dormant_seats"])
    gen2 = _GEN2_BAYS.get(norm)
    if norm in _CATALOG_IDX:
        area_key, name = _CATALOG_IDX[norm]
        out = {"eid": norm, "name": name, "area": _AREA_LABELS.get(area_key, area_key),
               "in_catalog": True,
               "hint": "use equipment_downtime(eid=...) for work-order downtime / repairs, "
                       "updown_now(eid=...) for current status"}
        if gen2:
            out["is_gen2_bay"] = True
            out["parent_eid"] = gen2[0]
            out["parent_note"] = ("module sub-position of parent " + gen2[0]
                                  + "; station rollups stay on the parent machine")
        if norm in _GEN2_PARENTS:                     # 父機亦在目錄（如 CLN#4 01066）→ 仍曝其子工位 EID
            out["is_gen2_parent"] = True
            out["module_eids"] = [m[0] for m in wd.GEN2_BAY_POSITIONS[norm]]
        out.update(_dk)
        return _attach("machine_lookup", out, params=_p, row_count=1,
                       page=f"/machine/{norm}", metric_ids=_ids)
    if gen2:  # 已知 Gen2 工位但不在目錄（父機層登錄）→ 仍誠實回工位身分
        parent_area, _pn = _CATALOG_IDX.get(gen2[0], (None, None))
        return _attach("machine_lookup",
                       {"eid": norm, "name": gen2[1], "in_catalog": False, "is_gen2_bay": True,
                        "parent_eid": gen2[0],
                        "area": _AREA_LABELS.get(parent_area) if parent_area else None,
                        "note": "module sub-position (not a standalone catalog entry); "
                                "use equipment_downtime(eid=...) / updown_now(eid=...)", **_dk},
                       params=_p, row_count=1, page=f"/machine/{norm}", metric_ids=_ids)
    if norm in _GEN2_PARENTS:  # Gen2/CLN 父機（工位聯集、非目錄列）→ 誠實指向工位
        return _attach("machine_lookup",
                       {"eid": norm, "in_catalog": False, "is_gen2_parent": True,
                        "module_eids": [m[0] for m in wd.GEN2_BAY_POSITIONS[norm]],
                        "note": "parent machine; its module sub-positions have their own EIDs", **_dk},
                       params=_p, row_count=1, page=f"/machine/{norm}", metric_ids=_ids)
    return _attach("machine_lookup",
                   {"eid": norm, "in_catalog": False,
                    "note": "not in the fixed equipment-meeting catalog; it may still exist "
                            "in MES station fleets — if you know the station, station_units(cls) "
                            "lists its machines", **_dk},
                   params=_p, row_count=0, degraded=True, metric_ids=_ids)


def _find_eqm_row(board, eid: str):
    """設備會議板（rows 分組於 areas 下）→ 指定 EID 的列（找不到→None）。"""
    for area in (board or {}).get("areas") or []:
        for r in area.get("rows") or []:
            if r.get("eid") == eid:
                return r
    return None


# ---- 單機長停登記（since／原因）——三支單機工具共用（2026-10-02；R41 §12.1-7 的 MCP 缺口）------
#: ★ 起因：`SPEC-DORMANT-CAPACITY.md` §12.1 第 7 點／ROADMAP「MCP `machine_lookup`／`equipment_downtime`
#:   單機回應無長停 since／原因」。頁面（`/equipment` Now 欄、`/availability`、`/utilization`、`/machine`
#:   下鑽）早已印「◌ 長停＋自 X＋原因」，助理卻只拿得到 `now.bucket="dormant"` 與一個 null 的妥善率
#:   ⇒ 答不出「從哪天、為什麼」，只好說「沒有數字」。
#: ★ **事實的唯一出處＝`board_dormant` 的 as-of 投影**（`dormant_info`；since＝本段長停起點、
#:   reason＝最後一筆 effective 事件的原因）。本檔**不另讀任何來源、不新增 SQL／HTTP**：
#:   ① 有板（`/equipment` 那張）⇒ 讀板列的 `dormant`＝頁面那一份（事件流腿＋批次現況腿，app 已投影好）；
#:   ② 無板（machine_lookup 窺不到）⇒ `wdorm.dormant_info(eid, 今天, 窺到的 A11 事件)`；
#:      事件窺不到 ⇒ `events=None` ⇒ `wdorm.bridge_rows()`——**合成進程恆空**（R-C3：橋上是工廠
#:      登記的真實日期與原因，合成世界不得外露；R-C1～R-C6／C-SY2a）。
#: ⚠ `dormant_registry`＝「這份登記是誰說的」（同頁面 `_a11_src_note` 的來源句；C-D3 排除必須看得見）。
#:   `unavailable`＝合成工單源不可達、長停事實不顯示——**不是**「這台在役」。
_DORMANT_NOTE = (
    "Registered long stop (not scheduled for production): a recorded decision, NOT a fault and "
    "NOT DOWN. `since` = first day of the current long-stop run (Asia/Taipei calendar date); "
    "`reason` = the registered free text, quote it verbatim. Availability / utilization are "
    "deliberately not computed for this machine (pages show '—'), so quote since and reason "
    "instead of any percentage.")
#: `dormant_registry` 閉集（工具面；頁面的來源句四形＋合成兩形的機器可讀版）。
DORMANT_REGISTRY = ("cmms", "cmms_cached", "bridge", "synthetic", "synthetic_cached", "unavailable")


def _is_synth_proc() -> bool:
    """合成進程？（`core_source` 旗標；讀不到＝當非合成，同 `board_dormant._synth_process`）。"""
    try:
        return bool(_wrs.is_synth_process())
    except Exception:                                    # noqa: BLE001
        return False


def _dormant_registry(status) -> str:
    """A11 取數狀態 → `DORMANT_REGISTRY` 之一。逐條對齊 `app._a11_src_note`：
       `live`＝登記源、`live_stale`＝登記源快取、其餘（None／gated／error:*）＝退橋常數；
       合成進程三形改說合成工單源，取不到＝`unavailable`（橋在合成進程恆空，**不得**說 bridge）。"""
    synth = _is_synth_proc()
    if status == "live":
        return "synthetic" if synth else "cmms"
    if status == "live_stale":
        return "synthetic_cached" if synth else "cmms_cached"
    return "unavailable" if synth else "bridge"


def _dormant_view(info):
    """`dormant_info` 形 dict（`{since, reason_zh, reason_en}`；或 None）→ 工具輸出形｜None。
       payload 一律英文：reason 取 `reason_en`、缺才退 `reason_zh`（cmms 登記兩欄同一份原文；
       座列 reason 恆 None＝照實 None，**不編原因**）。沒有 since 的（不該存在）⇒ None，不猜日期。"""
    if not info or not info.get("since"):
        return None
    return {"since": str(info["since"])[:10],
            "reason": info.get("reason_en") or info.get("reason_zh"),
            "note": _DORMANT_NOTE}


def _board_machine_row(board, eid):
    """板上該 EID 的**機台列**（座列不是台＝`#125` C-RW26）；沒有機台列才退任何同 EID 列。"""
    hit = None
    for area in (board or {}).get("areas") or []:
        for r in area.get("rows") or []:
            if r.get("eid") != eid:
                continue
            if not r.get("is_seat"):
                return r
            hit = hit or r
    return hit


def _dormant_lookup(norm, src, board=None):
    """單機長停登記 → `(dormant_view|None, dormant_registry)`。**恆不 raise、零取數**。

       · `board` 給了（`equipment_downtime`／`updown_now` 已拿到的那張板）⇒ 只讀板列；
       · 沒給（`machine_lookup`）⇒ `app._peek_eqm` warm-only 窺板（demo＝即時合成）；
       · 板上沒有這台／窺不到 ⇒ `wdorm.dormant_info`（as-of＝今天台北日）＋`app._peek_a11_events`
         （warm-only、零 HTTP；demo 恆 `None`＝同 `/machine` 路由）。"""
    import app as _app
    try:
        if board is None:
            pk = _app._peek_eqm(src, _app.EQM_DAYS, 15)
            board = pk[0] if pk else None
        if board:
            row = _board_machine_row(board, norm)
            if row is not None:
                return _dormant_view(row.get("dormant")), _dormant_registry(board.get("a11_status"))
        ev, st = (None, None) if src == "demo" else tuple(_app._peek_a11_events())[:2]
        info = wdorm.dormant_info(norm, datetime.now().date(), ev)
        return _dormant_view(info), _dormant_registry(st)
    except Exception as e:                               # noqa: BLE001（揭露腿永不炸工具）
        print(f"[board_mcp] dormant lookup {norm}: {type(e).__name__}", flush=True)
        return None, "unavailable"


def _with_dormant_ids(base, view):
    """登記表 id：**只在 payload 真的帶了長停登記時**加 `equip.dormant`（絕不編 id）。"""
    return tuple(base) + (("equip.dormant",) if view else ())


# ---- 座級長停（2026-10-02 第二批 ①；最優先＝答錯）--------------------------------------------
#: ★ 起因：`updown_now` 的名冊那一圈跳過**所有** `is_seat` 列（`#125` C-RW26：座不是台），所以
#:   `dormant` 清單只有**機台**長停；10-02 板上的長停全是**座級**（某台 nest 機的某一座）
#:   ⇒ 助理一筆都看不到、還答「沒有長停登記」。比照包⑤ 的 `down_seats`：**另一個欄另講**、
#:   **不進 `n_dormant`／`rows`**（座不是台；同一筆登記不得在一份 payload 裡出現兩次）。
#: ★ 事實的唯一出處＝**板上的座列**（`board_eqmeeting._seat_row` 的 `dormant`＝
#:   `board_capacity.seat_dormant_since`，餵的是建板那一份 A11 事件）。本檔**不另讀任何來源**；
#:   板窺不到 ⇒ `None`（＝沒查到，**不是**「沒有長停座」）。
#: ⚠ 合成進程（R-C1～R-C6）：座列的 since 只來自建板時餵進去的事件流（`events=None` ⇒ 座級恆無
#:   ＝`board_capacity._validated_events(None) == ()`），**從不碰橋常數** ⇒ 讀板列即安全。
#: ⚠ 座列的原因欄恆空（板上 `reason_zh／reason_en` 皆 None＝頁面原因欄空白）⇒ `reason` null
#:   ＝**登記沒有填原因**，照實說「原因未填」，**不編**。
_DORMANT_SEAT_NOTE = (
    "Seat-level long stop (one nest / sub-unit of the machine is registered as not scheduled "
    "for production): a recorded decision, NOT a fault and NOT DOWN, and the machine itself may "
    "still be producing. `since` = first day of the current long-stop run for that seat (Asia/"
    "Taipei calendar date). `reason` null = no reason was filled in for this registration (the "
    "page's reason column is blank) - say 'reason not filled in', never invent one.")


def _dormant_seat_rows(board, parent=None):
    """→ [座列 dict, …]（板序）＝板上**此刻** `now.bucket == "dormant"` 的**座列**（有 since）。
       `parent` 給了 ⇒ 只留那台母機的座。沒有 `parent_eid` 的座列 ⇒ 丟掉（同 `_down_seat_rows`：
       **不拿 `eid` 反推母機**）。判準原樣讀板的 `now`（開著的可計停機工單仍贏＝那一座在 `down_seats`）。"""
    return [r for a in ((board or {}).get("areas") or ()) for r in (a.get("rows") or ())
            if r.get("is_seat") and r.get("parent_eid")
            and (parent is None or r.get("parent_eid") == parent)
            and (r.get("now") or {}).get("bucket") == "dormant"
            and (r.get("dormant") or {}).get("since")]


def _dormant_seats_view(board, parent=None) -> list:
    """座級長停 → 工具輸出形（`down_seats` 的姊妹形）。registry＝同板的 A11 來源（`_dormant_registry`）。"""
    reg = _dormant_registry((board or {}).get("a11_status"))
    out = []
    for r in _dormant_seat_rows(board, parent):
        d = r.get("dormant") or {}
        out.append({"parent_eid": r.get("parent_eid"), "unit": r.get("seat_unit"),
                    "label": _seat_label(r.get("seat_unit")),
                    "since": str(d.get("since"))[:10],
                    "reason": d.get("reason_en") or d.get("reason_zh") or None,
                    "dormant_registry": reg, "note": _DORMANT_SEAT_NOTE})
    return out


def _peek_board_quiet(src):
    """`machine_lookup` 用的 warm-only 窺板 → board｜None（窺不到或拋錯都回 None；**恆不 raise**）。"""
    import app as _app
    try:
        pk = _app._peek_eqm(src, _app.EQM_DAYS, 15)
        return pk[0] if pk else None
    except Exception:                                    # noqa: BLE001
        return None


def _seat_input(raw):
    """座 id 形輸入（`EID-11860-N2`／`11860-n2`）→ `{seat_id, parent_eid, unit, label, registered}`｜None。
       解析的唯一出處＝`board_capacity.fold_seat_id`（NOTE-71 `-N`；本檔只做大小寫／補零正規化）。"""
    if raw is None:
        return None
    s = str(raw).strip().upper().replace(" ", "")
    head, sep, tail = s.rpartition("-N")
    if not sep or not tail.isdigit():
        return None
    parent = _norm_eid(head)
    if parent is None:
        return None
    try:
        import board_capacity as _wcap
        p, unit, known = _wcap.fold_seat_id(f"{parent}-N{int(tail)}")
    except Exception:                                    # noqa: BLE001
        return None
    if unit is None:
        return None
    return {"seat_id": f"{p}-N{int(tail)}", "parent_eid": p, "unit": unit,
            "label": _seat_label(unit), "registered": bool(known)}


def _eqm_connected(board) -> bool:
    """工單源是否接通（可報停機）：live 真工單 / demo 全合成＝可報；
       not_configured/snapshot_off/error:* ＝未接通（誠實不報 0）。"""
    return (board or {}).get("wo_status") in ("live", "demo")


#: cmms 結單態（`w8.wo_active_window` 的窗終點來源：首個 COMPLETED、無則首個終態）。
_WO_CLOSED_STATUSES = ("COMPLETED",) + tuple(w8.TERMINAL_STATUSES)

#: `work_orders[]` 四欄的讀法（加性 `work_orders_note`；2026-10-02 自評 ①）。
_WO_NOTE = (
    "work_orders[]: closed_at is set ONLY when the ticket is actually closed in cmms; null = still "
    "open. ongoing=true means the downtime is still running at as_of. counted_until is how far "
    "the hours are counted (for an ongoing ticket that is just as_of, the time the board was "
    "built) - it is NOT a close time. A ticket can be not ongoing yet still open (production "
    "resumed before the ticket was closed): then counted_until is when the downtime stopped and "
    "closed_at stays null. status is the ticket's current cmms status.")

#: ★ 2026-10-02 第二批 ②：工單文字（`brief`／`latest_note`）——頁面抽屜顯示的**同一份**文字。
#:   **零新增 cmms 呼叫**：demo＝`app._ld_wo_detail(…, "demo")`（純合成、零 I/O）；其餘＝只窺
#:   `app.wo_detail_peek`（頁面這個行程已經抓過的那一份）。窺不到＝`not_loaded`（**不是**「沒有簡述」）。
#:   §24：內文可能含人名——照頁面抽屜同一份文字的規則（`normalize_notes` 已去 source_actor），不另加工。
_WO_TEXT_MAX = 200
_WO_TEXT_SOURCES = ("demo", "page_cache", "not_loaded")
_WO_TEXT_NOTE = (
    " brief = the ticket's short description (head of the text); latest_note = the most recent "
    "work-log entry {type, at, text, text_truncated, text_chars} - the same text the Tendline Board "
    "machine drawer shows, quote it as written and never invent a cause that is not in it. "
    "latest_note.text is the LAST part (at most ~200 characters) of that log entry: work logs "
    "grow by appending, so the end is the current state. When text_truncated = 'head_omitted' "
    "the earlier part of the entry was left out (text_chars = the full length, text starts "
    "with an ellipsis): do not present the text as the whole record, and point to the work-order "
    "drawer for the complete history. text_source: 'demo' = "
    "synthetic demo text; 'page_cache' = already loaded by a Tendline Board page in this server "
    "session (text_fetched_at = when); 'not_loaded' = no page has opened this ticket yet, so "
    "the text is UNKNOWN (not empty) - say so and suggest opening the machine on /equipment, "
    "then asking again.")
_WO_NOTE += _WO_TEXT_NOTE


def _tail_text(body, limit=None):
    """長文 → `(text, truncated)`：不長原樣；超長取**尾段**（最後 ≤limit 字），起點對齊到最近的
       換行或句界（只在開頭 1/3 內找，找不到就硬切）、前綴「…」。Python str 以碼點計＝不切在字中間。
       cmms 進度紀錄是同一筆往下累加 ⇒ 現況在尾端。"""
    limit = _WO_TEXT_MAX if limit is None else limit
    if len(body) <= limit:
        return body, False
    tail = body[-limit:]
    win = tail[: max(1, limit // 3)]
    cut = -1
    nlp = win.find("\n")
    if nlp >= 0:
        cut = nlp + 1
    else:
        for i, ch in enumerate(win):
            if ch in "。！？；" or (ch in ".!?;" and i + 1 < len(tail) and tail[i + 1].isspace()):
                cut = i + 1
                break
    if cut > 0:
        tail = tail[cut:].lstrip()
    return "…" + tail, True


def _wo_text(src, wno) -> dict:
    """工單號 → `{brief, latest_note, text_source, text_fetched_at}`。**恆不 raise、零取數**。

       · `latest_note`＝`notes` 時間最新一筆（`normalize_notes` 升冪、此處再取 at 最大；平手取後者）
         ＝`{type, at, text, text_truncated, text_chars}`；長文取**尾段**（`_tail_text`），
         `text_truncated="head_omitted"`；`brief` 仍取頭；
         沒有事件流的歷史單 ⇒ 退頁面抽屜那一行（eMaint 最終診斷，`type="final_diagnosis"`、`at` null）
         ＝`app._ld_wo_card` 的同一條互斥規則（有事件流就不印靜態診斷）。
       · 拿不到 ⇒ 四欄照給、`text_source="not_loaded"`（不猜、不補）。"""
    import app as _app
    out = {"brief": None, "latest_note": None, "text_source": "not_loaded",
           "text_fetched_at": None}
    if wno is None:
        return out
    try:
        if src == "demo":
            det, _why = _app._ld_wo_detail(wno, "demo")
            tsrc, fetched = "demo", None
        else:
            det, fetched = _app.wo_detail_peek(wno)
            tsrc = "page_cache"
        if not isinstance(det, dict):
            return out
        brief = (det.get("brief_description") or "").strip() or None
        ln = None
        notes = list(det.get("notes") or ())
        if notes:
            nt = notes[-1] or {}
            try:                                          # 防禦：確保取時間最新（平手取後者）
                _best = None
                for n_ in notes:
                    if isinstance(n_, dict) and n_.get("at") is not None and (
                            _best is None or n_["at"] >= _best["at"]):
                        _best = n_
                if _best is not None:
                    nt = _best
            except Exception:                             # noqa: BLE001（型別混雜＝退回末筆）
                nt = notes[-1] or {}
            body = (nt.get("body") or "").strip()
            _tx, _tr = _tail_text(body)
            ln = {"type": (str(nt.get("entry_type") or "").strip() or None),
                  "at": _fmt_ts(nt.get("at")),
                  "text": _tx, "text_truncated": "head_omitted" if _tr else None,
                  "text_chars": len(body)}
        else:
            diag = (det.get("diagnosis") or "").strip()
            if diag:
                _tx, _tr = _tail_text(diag)
                ln = {"type": "final_diagnosis", "at": None,
                      "text": _tx, "text_truncated": "head_omitted" if _tr else None,
                      "text_chars": len(diag)}
        out.update(brief=brief, latest_note=ln, text_source=tsrc, text_fetched_at=fetched)
    except Exception as e:                               # noqa: BLE001（揭露腿永不炸工具）
        print(f"[board_mcp] wo text {wno}: {type(e).__name__}", flush=True)
    return out

#: 單機「已停多久」（加性三欄；2026-10-02 自評 ②）。
_ONGOING_NOTE = (
    "ongoing_since / ongoing_duration_h / ongoing_duration answer 'how long has it been down': "
    "from the start of the open work order that makes the machine DOWN now, to as_of - the same "
    "figure and the same rule as the Tendline Board machine card / machine-status window (e.g. "
    "'2d 4h'). All three are null when the machine is not DOWN now, when MES shows it producing "
    "while the ticket is still open, or when it is a registered long stop (quote dormant.since "
    "instead).")


def _trim_wos_win(row, win_end=None, src=None) -> list:
    """row.wos_win（{wno,start,end,work_type,status}）→ 工單精簡列。state 由 work_type 經
       w8.wo_state 解析（中文 canonical 態 as-is）；`status`＝板上帶的 cmms 現況（`wo_current`）。
       `win_end`＝板的 as-of（判 ongoing 用；None ⇒ 一律不判 ongoing、`closed_at` 只認結單態——保守側）。
       `src`（2026-10-02 第二批 ②）：給了 ⇒ 每列加性四欄 `brief`／`latest_note`／`text_source`／
       `text_fetched_at`（`_wo_text`；零取數）；None ⇒ 逐位元同加此參數前。"""
    out = []
    for x in (row.get("wos_win") or []):
        wt = x.get("work_type")
        state = w8.wo_state(wt)[0] if wt else None
        end, st = x.get("end"), x.get("status")
        # ★ 2026-10-02 自評 ①（最優先）：`build_row` 對**未結**單的 `end` 填的是板的 `win_end`
        #   （＝建板當下；`"end": we or win_end`）——舊版原樣塞進 `closed_at` ⇒ 助理寫出
        #   「已於 02:29:17 關閉，但目前狀態仍列為維修」。`closed_at` 只在真的結了才有值：
        #   · `ongoing`＝活躍窗延續到板的 as-of（`end == win_end`；FILES 既有慣例「end == win_end
        #     ＝ongoing 單」）；
        #   · `closed_at`＝**非 ongoing 且** cmms 現況是結單態（COMPLETED／CLOSED／CANCELLED／VOIDED）
        #     或遷移單無 history（`status` None＝`wo_active_window` 退 `closed_at` 那條路）；
        #     非 ongoing 但仍 OPEN（NOTE-60：已對齊復機、單還沒結）⇒ `closed_at` 仍 null；
        #   · `counted_until`＝停機時數**算到哪一刻**（＝原 `end`；ongoing 時就是 as-of）——舊語意搬到這欄。
        #   · `status`＝板上本來就帶的 cmms 現況（`wo_current` 末條；舊版寫死 None 是過時註記）。
        ongoing = win_end is not None and end is not None and end == win_end
        closed = (not ongoing) and end is not None and (
            st in _WO_CLOSED_STATUSES or (st is None and win_end is not None))
        item = {"wo_no": x.get("wno"), "state": state, "status": st,
                "work_type": wt, "opened_at": x.get("start"),
                "closed_at": end if closed else None,
                "ongoing": ongoing, "counted_until": end}
        if src is not None:
            item.update(_wo_text(src, x.get("wno")))
        out.append(item)
    return out


def _ongoing_down(board, row, norm):
    """單機「已停多久」→ `(since_dt|None, since_h|None)`。**恆不 raise、零取數**（只讀已拿到的板）。

       ★ 重用、不重算（2026-10-02 自評 ②）：`since_h`＝`app._ld_state_from_board(board)[eid]`
         ——landing 機台卡／機況窗印「2天 4小時」那一格的**同一支、同一份板**（工單活躍窗起點 →
         板的 `win_end`；#109「工單未關但 MES 在生產」⇒ None；`basis` 不是 `wo` ⇒ 不是停機的
         since，**不收**）。`since_dt`＝同一張單（`app._ld_row_wos(row)` 裡 `wno == now.wo_no`）
         的 `start`＝那個數字的起點本身。對不上單號 ⇒ 兩個都 None（不猜）。"""
    import app as _app
    try:
        st = (_app._ld_state_from_board(board) or {}).get(norm) or {}
        h = st.get("since_h")
        if h is None or st.get("basis") != "wo":
            return None, None
        wno = ((row or {}).get("now") or {}).get("wo_no")
        w = next((x for x in _app._ld_row_wos(row) if x.get("wno") == wno), None)
        if w is None or w.get("start") is None:
            return None, None
        return w["start"], float(h)
    except Exception as e:                               # noqa: BLE001（揭露腿永不炸工具）
        print(f"[board_mcp] ongoing-down {norm}: {type(e).__name__}", flush=True)
        return None, None


def _ongoing_txt(h):
    """小時 → 「2d 4h」形（`app._ld_since_txt` ＋ `_LD_I18N["en"]`＝頁面同一支格式器）；None ⇒ None。"""
    if h is None:
        return None
    import app as _app
    try:
        return _app._ld_since_txt(h, _app._LD_I18N["en"])
    except Exception:                                    # noqa: BLE001
        return None


def _eqm_board_for_tool(src, d, deadline):
    """`equipment_downtime` 拿板 → `(True, board, age_s|None)`｜`(False, None, None)`＝冷載等不到。

       ① **先窺頁面已暖的那一份**（`app._peek_eqm`：鍵逐位元同 `/equipment?days=d`、stale-tolerant
          ≤15 分）——頁面剛開過或暖機帶剛跑過就零成本；TTL 已過但仍在容忍內 ⇒ 照用（`as_of` 照實帶出）
          ＋背景重建一份（同一支 `load_eqm`、同一個 single-flight 鍵；**不等它**）。
       ② 窺不到 ⇒ `load_eqm` 丟背景、最多等到 `deadline`（`_cold_call`）。等不到 ⇒ `(None, None)`，
          背景照樣建完、落進頁面同一份快取 ⇒ 下一問是熱的。
       例外（loader 自己拋的）原樣往上拋＝呼叫端既有的 `_err_category` 分支。"""
    import app as _app
    key = ("eqm", src, d)
    pk = _app._peek_eqm(src, d, 15)
    if pk:
        board, age = pk
        if age is not None and age > getattr(_app, "EQM_TTL", 600):
            _cold_start(key, lambda: _app.load_eqm(src, d, 15))
        return True, board, age
    ok, board = _cold_call(key, lambda: _app.load_eqm(src, d, 15), deadline)
    return (True, board, None) if ok else (False, None, None)


def _trim_eqm_day(cells, day_labels) -> list:
    """cells{date:cell} → 依 day 順序（day_labels）的日停機列（S12 工單口徑）。
       ★ 2026-09-22（包③）：小時欄一律一位小數（`_round_h`；原為兩位）——`/mcp` 的每個
         `*_h` 用同一個精度，agent 才不必自己決定念幾位（而它照 persona 是不准動數字的）。"""
    days = []
    for ds in day_labels or []:
        c = (cells or {}).get(ds) or {}
        days.append({"date": ds,
                     "stop_h": _round_h(c.get("stop_h") or 0.0),
                     "by_state": {k: _round_h(v) for k, v in (c.get("by_state") or {}).items()},
                     "wo_nos": c.get("wo_nos") or [], "ongoing": bool(c.get("ongoing"))})
    return days


def _doc_fill(**kw):
    """把 docstring 裡的 `{key}` 佔位字換成常數值（**在 `@mcp.tool()` 讀 docstring 之前**套用）。
       用 str.replace、不用 str.format＝docstring 其他地方有大括號也不會炸。
       R45（2026-09-29）：久靜門檻天數由 `wem.QUIET_DAYS` 帶出，工具說明不再寫死數字。"""
    def deco(fn):
        doc = fn.__doc__ or ""
        for k, v in kw.items():
            doc = doc.replace("{" + k + "}", str(v))
        fn.__doc__ = doc
        return fn
    return deco


@mcp.tool()
@_doc_fill(quiet_days=f"{wem.QUIET_DAYS:g}")
def equipment_downtime(eid: str = None, days: int = 7) -> dict:
    """Work-order downtime / repairs for equipment (§14.4.1 pure work-order caliber, NOT MES gaps).
    days is clamped to 1..30. With eid → that machine's per-day downtime, availability, current
    status, and its work orders. Without eid → fleet summary (top machines by total downtime).
    now.bucket is down ONLY when a cmms work order says so; otherwise up. now.verdict refines it:
    "up_producing" = MES saw production in the last 2h; "up_idle" = no recent MES production, which
    may mean idle-ready, off-shift, or a machine that never logs MES — NOT a fault (silence never
    implies DOWN; there is no machine heartbeat source yet). now.stale is true when the machine has
    produced nothing for over {quiet_days} days: still UP, just worth asking about. now.mes_last_h = hours since
    last production (null = no production in the probe window, or the machine never logs MES).
    Downtime = active cmms work-order hours (interval-union). A machine with NO work order shows
    0 here even if it was idle. LONG STOP: a machine registered as not scheduled for production
    has now.bucket "dormant" (an open countable work order still wins and makes it down) and,
    with eid, a `dormant` block {since, reason, note}; its avail_pct is null BY DESIGN (not
    computed, not missing data) — quote since and reason verbatim instead of a percentage.
    `dormant` null = no long-stop registration; `dormant_registry` says whose registration it is
    ("unavailable" = registry unreadable, NOT "in production"); with eid, `dormant_seats` lists
    the machine's seat-level (nest / sub-unit) long stops (reason null = not filled in, never
    invent one). TICKET TEXT: each work_orders[] entry carries brief (short description) and
    latest_note {type, at, text} - quote them for "why is it down"; text_source "not_loaded" =
    the text is unknown (no page opened that ticket yet), NOT empty. HONESTY GATE: if the work-order
    source is not connected (not_configured / snapshot_off / error), NO downtime is reported and
    that is stated plainly (this does NOT mean zero downtime). Resolve an EID with
    machine_lookup first. WORK ORDERS: closed_at is null while a ticket is still open (never
    say it was closed); ongoing=true = downtime still running at as_of; counted_until = how far
    the hours are counted, NOT a close time. HOW LONG DOWN: with eid, ongoing_since /
    ongoing_duration_h / ongoing_duration give how long the current stoppage has lasted (same
    figure as the Tendline Board machine card). HOURS: total_stop_h is whole-calendar-day ticket hours;
    stop_h_rw is the expected-run-window figure the /equipment page leads with. STILL LOADING:
    still_loading=true (unavailable_reason "timeout") means the data is being prepared in the
    background - say so and suggest asking again in a few minutes; it implies nothing about any
    machine."""
    import app as _app
    d = _clamp_days(days, 7)
    src = _source()
    _p = {"eid": eid, "days": d}
    _ts = _now_ts()
    _win = _window_back(_ts, d)
    # 垃圾 EID 先擋（純函式、零取數）＝不必為了一個打錯的編號等冷載（2026-10-02 自評 ⑥）。
    if eid is not None and _norm_eid(eid) is None:
        return _attach("equipment_downtime", {"source": src, "error": "bad_eid", "given": eid},
                       params=_p, caliber=_CALIBER_RW_PLANNED, row_count=0,
                       window=_win, fetched_at=_ts, degraded=True)
    try:
        # stop_min=15＝設備頁預設，共用同一快取鍵；先窺頁面已暖那一份、冷才背景建＋有界等待（⑥）。
        loaded, board, _age = _eqm_board_for_tool(src, d, time.monotonic() + _cold_wait_s())
    except Exception as e:                             # 誠實降級：不 fallback 重查（FP-8）
        return _attach("equipment_downtime",
                       {"source": src, "window_days": d, "board": None,
                        "unavailable_reason": _err_category(e, "equipment_downtime")},
                       params=_p, caliber=_CALIBER_RW_PLANNED, page="/equipment",
                       row_count=0, window=_win, fetched_at=_ts, degraded=True)
    if not loaded:                                     # 冷載等不到：背景照建、誠實說「還在準備」
        return _attach("equipment_downtime",
                       {"source": src, "window_days": d, "board": None,
                        "unavailable_reason": _COLD_REASON, "still_loading": True,
                        "hint": _cold_hint()},
                       params=_p, caliber=_CALIBER_RW_PLANNED, page="/equipment",
                       row_count=0, window=_win, fetched_at=_ts, degraded=True)
    _as_of = (board or {}).get("win_end")              # 板的 as-of（＝停機時數算到的那一刻）

    if not _eqm_connected(board):                      # 工單源未接通 → 誠實、絕不捏 0
        return _attach("equipment_downtime",
                       {"source": src, "window_days": d,
                        "work_order_source": (board or {}).get("wo_status"),
                        "note": "work-order source not connected — downtime (cmms work-order hours) "
                                "cannot be reported; this does NOT mean zero downtime"},
                       params=_p, caliber=_CALIBER_RW_PLANNED, page="/equipment",
                       row_count=0, window=_win, fetched_at=_ts, degraded=True)

    day_labels = (board or {}).get("day_labels") or []

    if eid is not None:
        norm = _norm_eid(eid)
        if norm is None:
            return _attach("equipment_downtime", {"source": src, "error": "bad_eid", "given": eid},
                           params=_p, caliber=_CALIBER_RW_PLANNED, row_count=0,
                           window=_win, fetched_at=_ts, degraded=True)
        row = _find_eqm_row(board, norm)
        if row is None:
            return _attach("equipment_downtime",
                           {"source": src, "window_days": d, "eid": norm, "found": False,
                            "note": "not on the equipment board; confirm the EID with "
                                    "machine_lookup(eid)"},
                           params=_p, caliber=_CALIBER_RW_PLANNED, page="/equipment",
                           row_count=0, window=_win, fetched_at=_ts, degraded=True)
        now = row.get("now") or {}
        days_rows = _trim_eqm_day(row.get("cells"), day_labels)
        # ★ 2026-10-02（R41 §12.1-7 缺口）：長停 since／原因＝**同一張板的同一列**（頁面那一份；
        #   不另讀、不另投影）。加性兩欄，既有欄一個位元不動（C-HT9）。
        dv, dreg = _dormant_lookup(norm, src, board=board)
        _dseats = _dormant_seats_view(board, norm)          # 第二批 ①：這台的座級長停（同一張板）
        # ★ 2026-10-02 自評 ②：「停多久了」＝頁面機台卡那一格（`_ongoing_down` 重用、不重算）。
        _on_since, _on_h = _ongoing_down(board, row, norm)
        return _attach("equipment_downtime", _app.json_safe({
            "source": src, "eid": norm, "name": row.get("name"),
            "area": _AREA_LABELS.get(row.get("area"), row.get("area")),
            "window_days": d,
            "total_stop_h": _round_h(row.get("total_stop_h") or 0.0),   # 包③：一位小數
            # ★ 自評 ③：頁面帶頭的那個 Σ（應生產時段內）＝`equip.stop_h` 那把尺；框 inert／長停 ⇒ null
            "stop_h_rw": _round_h((row.get("erw") or {}).get("stop_h")) if row.get("erw") else None,
            "avail_pct": round(row["avail_pct"], 1) if isinstance(row.get("avail_pct"), (int, float)) else None,
            "avail_delta_pp": round(row["avail_delta_pp"], 1) if isinstance(row.get("avail_delta_pp"), (int, float)) else None,
            "now": {"bucket": now.get("bucket"), "verdict": now.get("verdict"),
                    "state": now.get("state"), "wo_no": now.get("wo_no"),
                    "mes_last_h": _round_h(now.get("mes_last_h")),      # 包③：一位小數
                    "stale": now.get("stale")},
            "days": days_rows,
            "work_orders": _trim_wos_win(row, _as_of, src=src),
            "work_orders_note": _WO_NOTE,
            "as_of": _as_of,
            "ongoing_since": _on_since,
            "ongoing_duration_h": _round_h(_on_h),
            "ongoing_duration": _ongoing_txt(_on_h),
            "ongoing_note": _ONGOING_NOTE,
            "caliber_note": _DOWNTIME_CALIBER,
            "dormant": dv, "dormant_registry": dreg,
            "dormant_seats": _dseats,
        }), params=_p, caliber=_CALIBER_RW_PLANNED, page=f"/machine/{norm}",
            row_count=len(days_rows), window=_win, fetched_at=_ts,
            metric_ids=_with_dormant_ids(_TOOL_METRIC_IDS["equipment_downtime"], dv or _dseats))

    # fleet 摘要：全列按 total_stop_h 降序、取前 10
    # `#125` C-RW26：座列不進 fleet 摘要（`n_machines` 是**台**數；座列的時數是母機那一份的
    # 子集 ⇒ 進 top-10 排行就是同一段停機上榜兩次）。
    allrows = [r for area in board.get("areas") or [] for r in area.get("rows") or []
               if not r.get("is_seat")]
    ranked = sorted(allrows, key=lambda r: (r.get("total_stop_h") or 0.0), reverse=True)
    top = [{"eid": r.get("eid"), "name": r.get("name"),
            "area": _AREA_LABELS.get(r.get("area"), r.get("area")),
            "total_stop_h": _round_h(r.get("total_stop_h") or 0.0),      # 包③：一位小數
            # 自評 ③：頁面帶頭的應生產時段 Σ（排序仍照 total_stop_h＝既有語意不動）
            "stop_h_rw": _round_h((r.get("erw") or {}).get("stop_h")) if r.get("erw") else None,
            "avail_pct": round(r["avail_pct"], 1) if isinstance(r.get("avail_pct"), (int, float)) else None,
            "now_bucket": (r.get("now") or {}).get("bucket")} for r in ranked[:10]]
    return _attach("equipment_downtime", _app.json_safe({
        "source": src, "window_days": d, "n_machines": len(allrows),
        "top": top, "truncated": len(allrows) > 10, "caliber_note": _DOWNTIME_CALIBER,
        "as_of": _as_of,
    }), params=_p, caliber=_CALIBER_RW_PLANNED, page="/equipment",
        row_count=len(allrows), window=_win, fetched_at=_ts)


_UD_ROW_CAP = 40
_UD_BUCKET_ORDER = {"down": 0, "unknown": 1, "up": 2}


def _eqm_since_h(row, now, win_end):
    """DOWN 列的「已停多久」＝`now.wo_no` 那張單的**活躍窗起點** → 板的 `win_end`（小時）。

    ★ 材料與規則**逐條同 `app._ld_state_from_board`**（landing 機台卡那一格）：板的 `now`
      不帶 since，四個消費端（landing 方塊／機況窗／機台卡／本工具）都只能從
      `wos_win` ＋ `wos_win_inh` 反查同一張單。**這裡不是第二把尺**——判 DOWN 的是板，
      這支只負責把板已經決定的那張單的開單時刻換算成小時。
    ⚠ 對不上單號／型別不對 ⇒ `None`（不講），**絕不猜一個數字**。"""
    wno = (now or {}).get("wo_no")
    if wno is None or win_end is None:
        return None
    for w in (list((row or {}).get("wos_win") or ())
              + list((row or {}).get("wos_win_inh") or ())):
        if w.get("wno") != wno:
            continue
        try:
            return max(0.0, (win_end - w["start"]).total_seconds() / 3600.0)
        except Exception:                       # noqa: BLE001 —— 型別對不上就不講
            return None
    return None


#: ★ 包① 的 `live_note` 兩形（2026-09-22；`SPEC-HERMES-TRUST.md` §12.13）。
#: 舊文案講的是「近 N 天 MES 出現過的那幾台」那把尺；換源之後**每一句都要換**——
#: 2026-09-22 三輪 live 實測的毛病全出在「數字沒錯、但沒講是哪個母體」。
_UD_NOTE_WIRED = (
    "ROSTER = the /equipment board (the fixed EQMT_CATALOG machine list, sub-position rows). "
    "This is the SAME board and the SAME rule the /equipment page paints, so n_down of n_total "
    "IS that page's 'Right now DOWN' figure. DOWN = exactly the rows the page paints DOWN right "
    "now (an open, countable work order; S12 staged caliber). Machines registered as not "
    "scheduled are listed separately in `dormant` and are NEVER counted in n_down. MES silence "
    "is NOT DOWN: a machine with no MES signal keeps whatever the board says (normally up). "
    "Heartbeat and button sources are still not wired. Always state this roster when you quote "
    "a count.")
_UD_NOTE_UNWIRED = (
    "ROSTER = the /equipment board (the fixed EQMT_CATALOG machine list, sub-position rows). "
    "The work-order source is NOT connected for this board, so NO machine can be reported as "
    "DOWN — that is a missing source, NOT a claim that nothing is down. MES silence is never a "
    "fault. Always state this roster when you quote a count.")
#: ★ 包⑤（2026-09-23；`SPEC-HERMES-TRUST.md` §12.15）：**座級 DOWN 的那一句**，兩形共用。
#: Chris 2026-09-23 01:2x live：畫面上 4 台紅機台 **＋ 一座紅**（`EID-26718` nest 2），
#: 工具卻只答得出 4 ⇒ 使用者得自己追問才問得出那一座。座是**產能單位、不是機台**（C-RW26）
#: ⇒ 正解不是把它加進 `n_down`（那會讓工具與頁面的台數對不起來、也會把同一段停機算兩次），
#: 而是**另一個欄另講**。兩形都掛：降級時座清單恆空，但「這個數字不含座」照樣要講清楚。
_UD_NOTE_SEATS = (
    " Seat-level (nest / sub-unit) rows that are DOWN right now are listed separately in "
    "`down_seats` (count = `n_down_seats`); `n_down` counts MACHINES only and NEVER includes "
    "them. A machine can be producing while one of its nests is down — when that happens, name "
    "the nest as well as the machine count.")
_UD_NOTE_WIRED += _UD_NOTE_SEATS
_UD_NOTE_UNWIRED += _UD_NOTE_SEATS
#: ★ 2026-10-02 第二批 ①：**座級長停的那一句**（同包⑤ 的形：兩形共用、`n_dormant` 只數台）。
_UD_NOTE_DORMANT_SEATS = (
    " Seat-level long stops (one nest / sub-unit registered as not scheduled) are listed "
    "separately in `dormant_seats` (count = `n_dormant_seats`); `n_dormant` counts MACHINES "
    "only. If `dormant` is empty but `dormant_seats` is not, there ARE long-stop registrations "
    "- at seat level - so never say there are none.")
_UD_NOTE_WIRED += _UD_NOTE_DORMANT_SEATS
_UD_NOTE_UNWIRED += _UD_NOTE_DORMANT_SEATS
#: ★ 包⑤（2026-09-23；§12.15）：`board_not_warmed` 的**下一步**（加性 `hint`）。
#: `unavailable_reason` 是給程式判斷的閉集（一個位元不動）；人要的是「那我現在該做什麼」。
#: 這一種不可用是**可自救**的——只是 `/equipment` 的快取 TTL 過了（約 25 分鐘沒人開那頁），
#: 不是資料源壞掉，而助理舊形只答得出「暫時看不到」。
_UD_HINT_NOT_WARMED = (
    "The /equipment board cache has expired (nobody opened that page in the last ~25 min) — "
    "reload /equipment once and ask again. Nothing is broken and no machine state is implied.")


def _down_seat_rows(board):
    """→ [座列 dict, …]（板序）＝板上**此刻** `now.bucket == "down"` 的**座列**（`is_seat`）。

       ★ 包⑤（2026-09-23；§12.15）。`updown_now` 的名冊那一圈明文跳過座列（`#125` C-RW26：
         座不是台）——那條**照舊**，所以這裡另走一圈，而不是去放寬那一圈。
       ★ 判準原樣讀板的 `now`（`_now_block` 已裁決過）：這裡一個新規則都不加。
       ⚠ 沒有 `parent_eid` 的座列（不該存在）⇒ 丟掉，**不拿 `eid` 反推母機**
         （同 `app._ld_seat_rows_by_parent`：猜錯會把停機掛到別台機器頭上）。"""
    return [r for a in (board.get("areas") or ()) for r in (a.get("rows") or ())
            if r.get("is_seat") and r.get("parent_eid")
            and (r.get("now") or {}).get("bucket") == "down"]


def _seat_label(unit) -> str:
    """座位 id → 工具回傳用的顯示字（`nest:2` → `nest 2`；形②子 EID 原樣＝它就是那一座的名字）。
       ⚠ 工具 payload 一律英文（同其餘每一欄）；頁面上的中文由前端自己翻。"""
    u = str(unit or "")
    return ("nest %s" % u.split(":", 1)[1]) if u.startswith("nest:") else u


@mcp.tool()
def updown_now(eid: str = None) -> dict:
    """Current Up/Down status, read straight off the /equipment board the user sees.
    ROSTER = that board's fixed machine list (EQMT_CATALOG, sub-position rows) — NOT "machines
    MES happened to see recently". DOWN = exactly the rows the page paints DOWN right now (an
    open, countable work order). Machines registered as not scheduled come back in a separate
    `dormant` list (each with since / reason) and are never counted as DOWN; with eid, a
    long-stopped machine carries a `dormant` block {since, reason, note} plus `dormant_registry`
    (quote since and reason verbatim). UNKNOWN = insufficient signal; silence is
    NEVER guessed as a fault. Seat-level (nest / sub-unit) rows that are DOWN come back in a
    separate `down_seats` list with `n_down_seats` — a machine can be producing while one of its
    nests is down, so name the nest too; `n_down` counts MACHINES only and never includes them.
    Seat-level LONG STOPS come back in `dormant_seats` with `n_dormant_seats` (each with since /
    reason; reason null = not filled in, never invent one) - if `dormant` is empty but
    `dormant_seats` is not, there ARE long-stop registrations, at seat level.
    With eid -> just that machine's current status plus its `dormant_seats` and `current_wo`
    (the ticket that makes it DOWN: brief / latest_note text, see text_source) (resolve the EID
    with machine_lookup first). Without eid -> DOWN machines first, then UNKNOWN, then UP; the
    row list is capped (least interesting UP rows drop first) and n_up_omitted reports how many
    UP rows were cut. board_built_at / age_min say how old the board snapshot is. If the board
    has not been warmed yet the tool says so honestly (unavailable_reason="board_not_warmed") —
    it NEVER triggers a board rebuild or an MES scan."""
    import app as _app
    src = _source()
    _p = {"eid": eid}
    # ★ 「此刻」工具：**沒有時間窗**（`window` 兩端 None），信封的 `fetched_at` 就是它的全部時間語意。
    _ts = _now_ts()
    # ★★ 包①（2026-09-22）：**peek-only**。`_peek_eqm` 是 warm-only 窺視（demo 那一格直接
    #   即時合成＝全合成、零成本），**永遠不會**為了一次 tool call 去觸發 live 全板重建或
    #   全站 MES 掃描。窺不到就誠實說「板還沒暖」，不是去把它暖起來。
    #   ⚠ 三個參數＝`/equipment` 路由的預設值（`DEFAULT_SOURCE`／`EQM_DAYS`／`stop_min=15`）
    #     ——鍵要與頁面那一份**逐位元相同**，否則永遠窺不到（同 `_peek_eqm` docstring 的警告）。
    try:
        pk = _app._peek_eqm(src, _app.EQM_DAYS, 15)
    except Exception as e:                               # 誠實降級：不 fallback 重查（FP-8）
        return _attach("updown_now",
                       {"source": src, "board": None,
                        "unavailable_reason": _err_category(e, "updown_now")},
                       params=_p, caliber=_CALIBER_BOARD_NOW, page="/equipment",
                       row_count=0, fetched_at=_ts, degraded=True)
    if not pk or not pk[0]:
        return _attach("updown_now",
                       {"source": src, "board": None,
                        "unavailable_reason": "board_not_warmed",
                        # ★ 包⑤（2026-09-23；§12.15）：加性 `hint`。`board_not_warmed` 是
                        #   **可自救**的一種不可用——快取 TTL 過了（大約 25 分鐘沒人開那頁）
                        #   而已，不是資料源壞掉。舊形只給一個 enum ⇒ 助理只能說「暫時看不到」，
                        #   使用者不知道自己按一下就好了。**`unavailable_reason` 一個位元不動**
                        #   （那是給程式判斷的閉集），這一行是給人看的下一步。
                        "hint": _UD_HINT_NOT_WARMED},
                       params=_p, caliber=_CALIBER_BOARD_NOW, page="/equipment",
                       row_count=0, fetched_at=_ts, degraded=True)
    board, age_s = pk[0], float(pk[1] or 0.0)
    wo_wired = _eqm_connected(board)
    win_end = board.get("win_end")
    live_note = _UD_NOTE_WIRED if wo_wired else _UD_NOTE_UNWIRED
    built_hm, age_min = board.get("built_hm"), int(round(age_s / 60.0))

    # 名冊＝板上的**機台列**：座列不是台（`#125` C-RW26，同 `_headline`）、dup EID 只算一次。
    seen, uniq = set(), []
    for _a in (board.get("areas") or ()):
        for r in (_a.get("rows") or ()):
            e = r.get("eid")
            if not e or r.get("is_seat") or e in seen:
                continue
            seen.add(e)
            uniq.append(r)
    roster_size = len(uniq)

    # `cls` ＝純常數對映（`board_capacity.NEST_EAV_UNITS`；板本身不帶站別）。
    # **不知道就給 None**——猜一個 cls 會讓 agent 連到別站的頁面（同 landing C-LD1）。
    try:
        cls_by_eid = _app._ld_station_map()
    except Exception:                                    # noqa: BLE001
        cls_by_eid = {}

    def _bucket(r):
        return (r.get("now") or {}).get("bucket")

    def _trim(r):
        now = r.get("now") or {}
        return {"eid": r.get("eid"), "cls": cls_by_eid.get(r.get("eid")),
                "desc": r.get("name"),
                "verdict": now.get("verdict"), "bucket": now.get("bucket"),
                "since_h": _round_h(_eqm_since_h(r, now, win_end)),   # 包③：一位小數
                # 2026-07-12 additive：工單源接真後 DOWN 列帶精確態 + 工單號（沉默仍永不判 DOWN）
                "now_state": now.get("state"),
                "wo_no": now.get("wo_no")}

    # ◌ 長停（dormant）＝**第三桶**：`/equipment` 有、舊的 `updown_now` 沒有。
    #   逐筆帶登記日與原因 ⇒ agent 不必（也不准）把它說成 DOWN。
    dormant = [{"eid": r.get("eid"), "desc": r.get("name"),
                "since": (r.get("dormant") or {}).get("since"),
                "reason": (r.get("dormant") or {}).get("reason_en")}
               for r in uniq if _bucket(r) == "dormant"]
    n_down = sum(1 for r in uniq if _bucket(r) == "down")
    n_up = sum(1 for r in uniq if _bucket(r) == "up")
    n_dormant = len(dormant)
    # 板只有 down／up／dormant 三桶；`n_unknown` 保留（C-HT9）＝**三桶以外的殘差**
    # （正常恆 0；上游哪天多一個桶，這裡會自己露出來，而不是默默算錯）。
    n_unknown = roster_size - n_down - n_up - n_dormant

    if eid is not None:
        norm = _norm_eid(eid)
        if norm is None:
            return _attach("updown_now", {"source": src, "error": "bad_eid", "given": eid},
                           params=_p, caliber=_CALIBER_BOARD_NOW, row_count=0,
                           fetched_at=_ts, degraded=True)
        match = next((r for r in uniq if r.get("eid") == norm), None)
        if match is None:
            return _attach("updown_now",
                           {"source": src, "eid": norm, "found": False,
                            "note": "not on the /equipment board roster; confirm the EID with "
                                    "machine_lookup(eid)"},
                           params=_p, caliber=_CALIBER_BOARD_NOW, page="/equipment",
                           row_count=0, fetched_at=_ts, degraded=True)
        # ★ 2026-10-02（R41 §12.1-7 缺口，同 machine_lookup／equipment_downtime）：單機分支補長停
        #   since／原因（fleet 分支的 `dormant` 清單本來就有）。讀**同一張板的同一列**、加性兩欄。
        dv, dreg = _dormant_lookup(norm, src, board=board)
        _dseats = _dormant_seats_view(board, norm)          # 第二批 ①：這台的座級長停（同一張板）
        _mt = _trim(match)
        # 第二批 ②：此刻讓它 DOWN 的那張單的文字（`now.wo_no`；沒有單 ⇒ null）。零取數（`_wo_text`）。
        _cwo = (dict({"wo_no": _mt["wo_no"]}, **_wo_text(src, _mt["wo_no"]),
                     note=_WO_TEXT_NOTE.strip())
                if _mt.get("wo_no") is not None else None)
        return _attach("updown_now",
                       _app.json_safe({"source": src, "mode": board.get("mode"),
                                       "wo_wired": wo_wired,
                                       "machine": _mt, "live_note": live_note,
                                       "roster_size": roster_size,
                                       "board_built_at": built_hm, "age_min": age_min,
                                       "dormant": dv, "dormant_registry": dreg,
                                       "dormant_seats": _dseats,
                                       "current_wo": _cwo}),
                       params=_p, caliber=_CALIBER_BOARD_NOW, page=f"/machine/{norm}",
                       row_count=1, fetched_at=_ts,
                       metric_ids=_with_dormant_ids(_TOOL_METRIC_IDS["updown_now"],
                                                    dv or _dseats))

    # DOWN → UNKNOWN → UP 排序後截斷（截掉的都是最不重要的 UP 列；防禦性、不倚賴上游排序）。
    # ◌ 長停列**不進 `rows`**：它們有自己那一桶（兩邊都放＝同一台在同一份 payload 裡出現兩次）。
    listable = [r for r in uniq if _bucket(r) != "dormant"]
    ordered = sorted(listable, key=lambda r: _UD_BUCKET_ORDER.get(_bucket(r), 9))
    kept, dropped = ordered[:_UD_ROW_CAP], ordered[_UD_ROW_CAP:]
    trimmed = [_trim(r) for r in kept]
    n_up_omitted = sum(1 for r in dropped if _bucket(r) == "up")
    # 停最久的那一台＝DOWN 列裡 `since_h` 最大的（**整個名冊**掃，不是只看被留下的 40 列）。
    _downs = [x for x in (_trim(r) for r in uniq if _bucket(r) == "down")
              if x["since_h"] is not None]
    ld = max(_downs, key=lambda x: x["since_h"]) if _downs else None
    # ◌ 包⑤（2026-09-23；§12.15）：**座級 DOWN ＝第四桶**，加性欄、**不進 `rows`**
    #   （同一段停機不得在一份 payload 裡出現兩次）、**不進 `n_down`**（座不是台）。
    down_seats = [{"parent_eid": r.get("parent_eid"), "unit": r.get("seat_unit"),
                   "label": _seat_label(r.get("seat_unit")),
                   "wo_no": (r.get("now") or {}).get("wo_no"),
                   "since_h": _round_h(_eqm_since_h(r, r.get("now") or {}, win_end))}
                  for r in _down_seat_rows(board)]
    # ◌ 第二批 ①（2026-10-02）：**座級長停＝第五桶**，同包⑤ 的形——加性欄、不進 `rows`／`n_dormant`。
    dormant_seats = _dormant_seats_view(board)
    return _attach("updown_now", _app.json_safe({
        "source": src, "mode": board.get("mode"),
        "n_up": n_up, "n_down": n_down,
        "n_unknown": n_unknown, "n_total": roster_size,
        "longest_down": ({"eid": ld["eid"], "since_h": ld["since_h"]} if ld else None),
        "rows": trimmed, "truncated": len(ordered) > _UD_ROW_CAP,
        "n_up_omitted": n_up_omitted,
        "wo_wired": wo_wired,
        "live_note": live_note,
        # ---- 包① 的加性欄（2026-09-22；§12.13）--------------------------------------
        # `roster_size` ＝ `n_total`（同一個數字、兩個名字）：`n_total` 是 C-HT9 留下來的
        # 既有欄，`roster_size` 才講得出「這個分母是一張固定名冊」——而三輪實測的誤判
        # 正是分母沒講清楚造成的。`n_dormant`／`dormant` ＝第三桶；`board_built_at`／
        # `age_min` ＝這張板是幾點建的、放了多久（頁面窄橫列的 as-of 同一件事）。
        "roster_size": roster_size,
        "n_dormant": n_dormant,
        "dormant": dormant,
        "board_built_at": built_hm,
        "age_min": age_min,
        # ---- 包⑤ 的加性欄（2026-09-23；§12.15）------------------------------------
        # `n_down` 仍是**機台**數、名冊不變；座級 DOWN 走這兩欄（第四桶）。
        "n_down_seats": len(down_seats),
        "down_seats": down_seats,
        # ---- 第二批 ① 的加性欄（2026-10-02）：座級長停（`n_dormant` 仍只數台）-----------------
        "n_dormant_seats": len(dormant_seats),
        "dormant_seats": dormant_seats,
    }), params=_p, caliber=_CALIBER_BOARD_NOW, page="/equipment",
        row_count=len(trimmed), fetched_at=_ts,
        # 帶了長停登記（台或座）才引用 `equip.dormant`（同單機分支的規則；絕不編 id）
        metric_ids=_with_dormant_ids(_TOOL_METRIC_IDS["updown_now"], dormant or dormant_seats))


# ---- 語意軸工具（指標登記表薄封裝；A1b，`SPEC-HERMES-TRUST.md` §4.1）------------------
#: ⚠ 這兩支**零 I/O、零 MES、零 app import**：它們答的是「怎麼算」，不是「現在多少」。
#:   值走既有九個工具與 A2 的 `get_metric`（§13 ☐8 已裁 ⇒ **不對 WebMCP 開**，只給 `/mcp`）。
#: ⚠ `freshness` 一律 **`static`**（A1b 修正輪；`board_metrics.FRESHNESS` 同批加這個值）：
#:   登記表是 repo 內**版本化的常數**、不是量測。填 `live` 會讓 agent 以為這裡有現場數字；
#:   填 `fixture` 更糟——那是「demo 的**數字**是合成的」的意思，會讀成「這個定義是假的」。

@mcp.tool()
def list_metrics(page: str = None) -> dict:
    """List the registered Tendline Board metrics (what each number on a page means and how it is
    calculated). Optional page filter: "/", "/utilization", "/station/{cls}" (or a concrete
    "/station/<cls>"), "/equipment", "/daily". Returns id / title (en+zh) / unit / caliber /
    status / pages for each metric — NOT the formula text. Use explain_metric(id) for that.
    An unknown page is NOT an error: it honestly returns an empty list. Call this FIRST to
    resolve a metric id — never guess or fabricate one."""
    items = M.list_for_page(page)
    return _attach("list_metrics",
                   {"page": page, "n": len(items), "metrics": items},
                   params={"page": page}, caliber=_CALIBER_DUAL, freshness="static",
                   row_count=len(items),
                   # 篩到東西才把該頁當「人可以去看的那一頁」；`/nope` 不掛假連結。
                   page=(page if items else None))


@mcp.tool()
def explain_metric(id: str, lang: str = "en") -> dict:  # noqa: A002 — 簽名由 SPEC §4.1 定死
    """Explain ONE metric: its one-sentence formula (in `lang`), caliber, unit, freshness,
    pitfalls, how to verify it yourself, contract-card numbers, and which pages show it.
    id must be an exact id from list_metrics (e.g. "util.availability"); lang is "en" or "zh".
    An unknown id or lang is reported honestly (error:"unknown_metric" / "unknown_lang") and
    is NEVER guessed. The caliber here is authoritative — never restate a number in a caliber
    other than the one this tool reports."""
    _p = {"id": id, "lang": lang}
    if lang not in M.LANGS:
        return _attach("explain_metric",
                       {"error": "unknown_lang", "given": lang,
                        "hint": "lang must be exactly 'en' or 'zh'",
                        "known_langs": list(M.LANGS)},
                       params=_p, freshness="static", row_count=0, degraded=True)
    try:
        doc = M.explain(id, lang)
    except ValueError:                       # 未知 id → 誠實錯誤物件，不吐 traceback、不猜
        return _attach("explain_metric",
                       {"error": "unknown_metric", "given": id,
                        "hint": "call list_metrics() and use an exact id; never guess a metric id",
                        "n_known": len(M.ALL_METRICS)},
                       params=_p, freshness="static", row_count=0, degraded=True)
    if _wrs.is_synth_process():             # WP-4b R-B4（SPEC-SYNTHLINE §7.1）：合成進程附退階語意（非合成：不加鍵）
        doc = dict(doc, synthetic_line_note=M.SYNTH_NOTE[lang])
    return _attach("explain_metric", doc, params=_p, caliber=doc["caliber"],
                   freshness="static", row_count=1,
                   metric_ids=(doc["id"],),          # 這一次講的就是這一筆
                   page=(doc.get("verify") or {}).get("page_url_tpl"))


# ---- 值軸工具 `get_metric`（A2；`SPEC-HERMES-TRUST.md` §4.1／§12.2）--------------------
#: ⚠ 這一支答的是「**現在多少**」，`explain_metric` 答的是「怎麼算」——**兩件事、兩個工具**。
#: ⚠ `freshness` 一律 `_freshness()`（live／fixture），**不是 `static`**：`static` 只給登記表
#:   常數工具；`get_metric` 即使這次沒有值，答的仍是一個量測面的問題。
#: ⚠ **零新增取數路徑**（§4.2-4）：可達面＝`board_metrics.VALUE_PATHS` 那 17 個 `(id, scope)`
#:   組合，全部是**既有頁面已經在走的 loader**（`load_scoreboard`／`load_scoreboard_one`／
#:   `load_qlik_grid`／`station_axis_payload`，皆 TTL 快取或由快取組成）。其餘一律回
#:   `no_value_path` ＋一個閉集裡的 `reason`。
#: ⚠ **RW 軸那 11 筆是 live only**（2026-09-21 接）：demo／snapshot 下回的是
#:   `value=null` ＋ `unavailable_reason`（＝「這一次取不到」），**不是** `no_value_path`
#:   （＝「這個指標沒有路徑」）。兩鍵恆在、一為 null，**不得合併**（§12.2）。

_SCOPE_FLEET = "fleet"
_WINDOW_MIN_D, _WINDOW_MAX_D = 1, 30          # 與 `_clamp_days` 上限同一把尺
_WINDOW_RE = re.compile(r"^([0-9]{1,4})d$")

#: `board_metrics.TRANSFORMS` 的名字 → 純函式（**取值的碼住這裡、不住登記表**＝§2.1 零 I/O）。
#: 非數值 ⇒ `None`（誠實留白），**絕不** 0。`bool` 刻意排除在數值之外（`True` 不是 1.0）。
_VALUE_TRANSFORMS = {
    "as_is": lambda v: v,
    "round1": lambda v: (round(float(v), 1)
                         if isinstance(v, (int, float)) and not isinstance(v, bool) else None),
    "pct100_round1": lambda v: (round(100.0 * float(v), 1)
                                if isinstance(v, (int, float)) and not isinstance(v, bool) else None),
    "as_bool": lambda v: bool(v),
    # RW 軸的 `|RW|` 在 `station_model` 裡是**秒**，登記表那一筆的 `unit` 是 `h`
    # ⇒ 換算住這裡（登記表零 I/O、也零算術；取值的碼只查表不換算）。
    "sec_to_h_round1": lambda v: (round(float(v) / 3600.0, 1)
                                  if isinstance(v, (int, float))
                                  and not isinstance(v, bool) else None),
}

#: **值不是純量**的 id（`get_metric` 的 `value` 是純量欄 ⇒ 結構上進不了）。
#: ★ 2026-09-21：這個集合原名 `_RW_AXIS_IDS`、原本 12 筆——組裝住在路由 handler
#:   `app.station_overview()`、沒有可重用的 loader。那一段已經抽成 `app.station_axis_payload`
#:   ⇒ **11 筆進了 `VALUE_PATHS`**，剩下的 `util.down_by_state` 留在這裡。
#: ⚠ `util.down_by_state` **不是漏接、也不是「還沒接」**：它的值是逐態 dict（每個 §23 狀態一個
#:   小時數），而 `get_metric` 的 `value` 是純量欄（smoke 釘「不是數就是 bool 就是 null」）
#:   ⇒ **沒有「接上去」這回事**。原先借 `rw_axis_not_exposed` 是不得已（閉集裡沒有合適的值），
#:   2026-09-21 立**第六個閉集值 `non_scalar_value`** 說真話，集合同批改成語意正確的名字
#:   （裁決存證＝`SPEC-HERMES-TRUST.md` §12.2「第六個閉集值」節）。
#: ⚠ 這裡**只放資料**（id 的集合）；判定規則住 `_build_no_value_reasons()`、函式裡不列 id。
_NON_SCALAR_IDS = frozenset({"util.down_by_state"})

#: 逐筆例外（**理由各自不同，不能併進上面那個集合**）。
_REASON_OVERRIDE = {
    # 值是逐機列（list）不是純量；`station_units(cls)` 已經在答這件事。
    "station.unit_bar": "use_station_units",
    # `app.load_nest_seats` 未被任何 MCP 工具消費 ⇒ 接它＝新增取數路徑。
    "station.seat_rows": "no_tool_path",
    # 門檻規則住 `board_tagui.tag_ph_mode`（渲染層），沒有任何 MCP 工具在走它。
    "station.tag_filter": "no_tool_path",
}

#: `no_value_path` 的 `reason` **閉集**（smoke 逐筆掃 53 筆：不是可達、就是這六個之一）。
#: ⚠ 末位的 `rw_axis_not_exposed` **2026-09-21 起零筆**——它是 A2 當日 12 筆 RW 軸的值，
#:   11 筆已進 `VALUE_PATHS`、第 12 筆改吃 `non_scalar_value`。**留在閉集只為歷史存證**
#:   （`SPEC-HERMES-TRUST.md` §12.2 當日那張表引用它）；**不得再借給新的情境**——
#:   字面是「還沒對外開」＝暗示有待做的接線，拿去講結構性的事就是說謊。
NO_VALUE_REASONS = ("non_scalar_value", "use_station_units", "no_tool_path",
                    "metric_draft", "out_of_scope_a2", "rw_axis_not_exposed")

#: 每個 `reason` 的一句話（**回傳裡不吐**，給讀碼的人與 SPEC 回填用；口徑住登記表不住這裡）。
NO_VALUE_REASON_NOTE = {
    "non_scalar_value": "值是逐態 dict（每個 §23 狀態一個小時數）、不是純量，get_metric 只回純量"
                        "；要逐態數字走 station_overview(cls) 或 equipment_downtime(eid)",
    "rw_axis_not_exposed": "【2026-09-21 起零筆·歷史存證】RW（應生產時段）軸上還沒對外開的那幾格",
    "use_station_units": "值是逐機列不是純量；station_units(cls) 已經在答",
    "no_tool_path": "這一格的 loader 沒有被任何 MCP 工具消費 ⇒ 接它＝新增取數路徑",
    "metric_draft": "登記表 status=draft、出處未補齊（SPEC §13 ☐6）",
    "out_of_scope_a2": "A2 只先接 /utilization 與 /station（SPEC §4.1／§12.2）",
}

_A2_PAGES = (M.P_UTIL, M.P_STATION)


def _build_no_value_reasons() -> dict[str, str]:
    """`{id: reason}`（**資料驅動**：函式裡不列 id，只寫規則）。

    判定順序固定：可達（`VALUE_PATHS`）⇒ 不進表；**非純量值** ⇒ `non_scalar_value`；
    逐筆例外 ⇒ 該值；`status=draft` ⇒ `metric_draft`；不在 A2 兩頁 ⇒ `out_of_scope_a2`；
    其餘（`/utilization`／`/station` 上新登記、又沒人接的）⇒ `no_tool_path`。
    """
    out: dict[str, str] = {}
    for mid, m in M.ALL_METRICS.items():
        if mid in M.VALUE_PATHS:
            continue
        if mid in _NON_SCALAR_IDS:
            out[mid] = "non_scalar_value"
        elif mid in _REASON_OVERRIDE:
            out[mid] = _REASON_OVERRIDE[mid]
        elif m.status == "draft":
            out[mid] = "metric_draft"
        elif not any(p in _A2_PAGES for p in m.pages):
            out[mid] = "out_of_scope_a2"
        else:
            out[mid] = "no_tool_path"
    return out


_NO_VALUE_REASON = _build_no_value_reasons()


def _parse_window(window):
    """`"<N>d"` → `N`（1..30）；形不對／超界 ⇒ `None`（**拒、不 clamp**）。

    ⚠ 與 `_clamp_days` 語意不同、刻意不共用：`days` 是「agent 給了、我們替它收斂」，
      `window` 是「agent 必須自己講清楚窗」（§4.2-3／§11 反模式 7）。
    """
    if not isinstance(window, str):
        return None
    mt_ = _WINDOW_RE.match(window.strip())
    if not mt_:
        return None
    n = int(mt_.group(1))
    return n if _WINDOW_MIN_D <= n <= _WINDOW_MAX_D else None


def _gm_page(tpl, scope):
    """登記表的 `page_url_tpl` → 這一次的頁（`{cls}` 代入實際 scope；fleet 用樣板原字串）。"""
    if not tpl:
        return None
    if "{cls}" in tpl:
        return tpl.replace("{cls}", scope) if scope != _SCOPE_FLEET else tpl
    return tpl


def _dig(obj, key_path):
    """`obj` 沿 `key_path` 取值 → `(value, found)`；中途不是 dict／缺鍵 ⇒ `(None, False)`。"""
    cur = obj
    for k in key_path:
        if not isinstance(cur, dict) or k not in cur:
            return None, False
        cur = cur[k]
    return cur, True


_GM_BLANK = {"value": None, "not_measurable": False, "unavailable_reason": None,
             "note": None, "ts": None, "row_count": 0}

#: RW 軸取不到值的三句（2026-09-21）。**三句都是 `unavailable_reason`＝「我們這一次取不到」**，
#: 與 `no_value_path` 的 `reason`＝「這個指標根本沒有路徑」**是兩件事、兩個鍵、不得合併**
#: （§12.2；兩鍵恆在、一為 null）。
#: ⚠ 既有值域裡沒有「live only／這個源不支援」的語意（現有五句講的都是「這個窗沒有資料」）
#:   ⇒ 新增第一句，並回填 SPEC §12.2。
_GM_RW_LIVE_ONLY = ("the expected-run-window axis exists only on the live source; this source "
                    "falls back to the 24h axis, where this number does not exist "
                    "(honest blank, not zero)")
_GM_RW_INERT = ("the run-window materials are inert for this window (no production calendar or "
                "no work-order facts), so the page falls back to the 24h axis - "
                "honest blank, not zero")
_GM_RW_BLANK = ("no value on the run-window axis for this window (the page shows a dash here; "
                "a dash is not zero)")


def _gm_value(vp, scope, days, src) -> dict:
    """走 `vp.loaders[0]` 那條**既有**路徑取一格值。回 `_GM_BLANK` 的形。

    資料側例外**往上拋**（呼叫端接成 `_err_category`，只回穩定分類碼、不夾 `str(e)`）。
    """
    import app as _app
    out = dict(_GM_BLANK)
    head = vp.loaders[0]
    tf = _VALUE_TRANSFORMS[vp.transform]

    if head == "app.load_scoreboard":                     # 機隊那一格
        sb = _app.load_scoreboard(src, days, 15)
        out["ts"] = _fmt_ts((sb or {}).get("mes_now"))
        raw, found = _dig(sb or {}, vp.key_path)
        if not found:
            out["unavailable_reason"] = "no fleet scoreboard for this window (honest blank, not zero)"
            return out
        out["value"] = tf(raw)

    elif head == "app.load_scoreboard_one":               # 單站 util rec
        rec = _app.load_scoreboard_one(src, scope, days, 15)
        if rec is None:
            # 逐字沿用 `station_overview` 的那一句（同一件事只有一種說法）。
            out["unavailable_reason"] = "no activity in window (honest blank, not zero)"
            return out
        # ★ 零工期站（M4 後遺）：段長法稼動結構性不可測 ⇒ **null + 不可測旗**，絕不回 0。
        #   `util.zero_len_unmeasurable` 本身問的就是這面旗，所以不套這條（否則它永遠 null）。
        if vp.key_path[-1] != "zero_len_only" and rec.get("zero_len_only"):
            out["not_measurable"] = True
            out["note"] = _UTIL_UNMEASURABLE_NOTE
            return out
        raw, found = _dig(rec, vp.key_path)
        if not found:
            out["unavailable_reason"] = "field not present on this station record"
            return out
        out["value"] = tf(raw)

    elif head == "app.load_qlik_grid":                    # 公司口徑站列（D2）
        import board_data as _wd
        grid = _app.load_qlik_grid(src, days)
        out["ts"] = _fmt_ts(grid.get("cap"))
        row = _wd.qlik_rows_by_cls(grid).get(scope)
        if row is None:
            # 逐字沿用 `station_overview` 的那一句；**絕不回退 raw**（回退＝兩種口徑混講）。
            out["unavailable_reason"] = (
                grid.get("unavailable") or "station not in the 43-station company-caliber list "
                "(no company-caliber yield exists for it)")
            return out
        raw, found = _dig(row, vp.key_path)
        if not found:
            out["unavailable_reason"] = "field not present on this station row"
            return out
        out["value"] = tf(raw)

    elif head == "app.station_axis_payload":              # `/station` 頭卡材料（RW 軸 11 筆）
        # ★ 走的是**頁在走的那一支**（2026-09-21 從 `station_overview` 抽出）⇒ 工具答的數字
        #   與頁上印的是同一格，不會出現「同一站同一秒兩把尺」（QG-13 的跨消費者形）。
        # ⚠ `tag=""`＝**預設全部、不做任何能力過濾**（§12.2）：`get_metric` 的簽名是
        #   `(id, scope, window)` 三參數、**不新增 tag 參數**，所以這裡不准偷偷帶一個。
        # ⚠ `stop_min=15`／`start=end=""`＝同上面兩條既有路徑的預設（零新增取數路徑）。
        ax = _app.station_axis_payload(src, days, 15, "", "", scope, "")
        out["ts"] = _fmt_ts((ax.get("data") or {}).get("mes_now"))
        st_ax = ax.get("st")
        if st_ax is None:
            out["unavailable_reason"] = "no activity in window (honest blank, not zero)"
            return out
        if not st_ax.get("rw_axis"):
            # 非 live ⇒ 三層擋的第一行就回 None（結構上沒有這條軸）；live 但算不出來＝inert。
            out["unavailable_reason"] = (_GM_RW_INERT if _wrs.is_live_like(src) else _GM_RW_LIVE_ONLY)
            return out
        # ★ 零工期站（M4 後遺）：`station_model` 把 A／U／OEE 全設 None ⇒ **null + 不可測旗**，
        #   絕不回 0（與 `load_scoreboard_one` 那條分支同一條紀律）。
        if st_ax.get("util_unmeasurable"):
            out["not_measurable"] = True
            out["note"] = _UTIL_UNMEASURABLE_NOTE
            return out
        raw, found = _dig(ax, vp.key_path)
        if not found:                                      # `cap` 為 None（|RW| ≤ 0）也走這裡
            out["unavailable_reason"] = "field not present on this station payload"
            return out
        out["value"] = tf(raw)
        if out["value"] is None:                           # 無尺／無計數 ⇒ 頁上是「—」
            out["unavailable_reason"] = _GM_RW_BLANK

    else:                                                  # pragma: no cover - 登記表加了沒接的 loader
        out["unavailable_reason"] = "unavailable"
        return out

    out["row_count"] = 0 if out["value"] is None else 1
    return out


#: `station_overview` 的「連結頁那一格」（2026-10-02 自評 ④）。**一筆、寫死 id**：頁面頭卡的稼動
#: ＝登記表 `station.utilization`（`VALUE_PATHS` 的 cls 路徑＝`app.station_axis_payload`）。
_PAGE_UTIL_METRIC = "station.utilization"
#: 揭露腿（`page_value`）最多等幾秒；等不到 ⇒ 值 null＋`page_value_reason="timeout"`，背景照建＝下一問是熱的。
_PAGE_VALUE_WAIT_S = 30.0


def _station_page_value(src, cls, d, util_rec, deadline) -> dict:
    """→ 加性五欄 `{page_value, page_caliber, page_metric_id, page_value_reason, page_note}`。

       ★ **讀頁面同一份 payload、不重算**：`_gm_value`＝`get_metric('station.utilization', cls, …)`
         走的那一條（`app.station_axis_payload`＝`/station/{cls}` 頭卡的唯一材料出處，QG-13）。
       ★ 冷時同 ⑥：背景跑、最多等到 `deadline`；等不到 ⇒ 值 null＋`page_value_reason="timeout"`。
       ⚠ 恆不 raise：揭露腿壞掉只讓這五欄退成 null＋分類碼，`util`／`yield` 兩塊一位元不受影響。"""
    m = M.ALL_METRICS[_PAGE_UTIL_METRIC]
    res = {"page_value": None, "page_caliber": m.caliber, "page_metric_id": m.id,
           "page_value_reason": None, "page_note": None}
    vp = next((v for v in (M.VALUE_PATHS.get(m.id) or ()) if v.scope_kind == "cls"), None)
    try:
        if vp is None:                                     # pragma: no cover - 登記表拔了路徑
            raise RuntimeError("no value path")
        ok, got = _cold_call(("station_axis", src, d, cls), lambda: _gm_value(vp, cls, d, src),
                             deadline)
    except Exception as e:                                 # noqa: BLE001
        ok, got = True, dict(_GM_BLANK, unavailable_reason=_err_category(
            e, "station_overview.page_value"))
    if not ok:
        res["page_value_reason"] = _COLD_REASON
    elif got.get("not_measurable"):
        res["page_value_reason"] = "not measurable (zero-length MES segments) - not zero"
    else:
        res["page_value"] = got.get("value")
        res["page_value_reason"] = got.get("unavailable_reason")
    u = (util_rec or {}).get("util_pct")
    u_txt = f"{u}%" if isinstance(u, (int, float)) else "not available"
    if res["page_value"] is not None:
        res["page_note"] = (
            f"The linked page /station/{cls} shows utilization on the {m.caliber} axis (producing "
            f"time / expected run window, i.e. the planned/rw-planned production time) = "
            f"{res['page_value']}%. When you quote this number, ALWAYS state its denominator: "
            "the scheduled (expected-to-run) time, not the active span. util.util_pct here uses a "
            f"different denominator (producing time / active span, {_CALIBER_UTIL_AXIS}) = "
            f"{u_txt}. Both are correct; if you quote one and link the page, say in one plain "
            "sentence which denominator each uses, or quote the page's figure." + _ACTIVE_SPAN_NOTE)
    else:
        res["page_note"] = (
            f"The utilization figure of the linked page /station/{cls} ({m.caliber} axis) is not "
            f"available here ({res['page_value_reason']}). util.util_pct ({u_txt}) uses the "
            "producing time / active span denominator, which can differ from what the page "
            "shows, so never present it as the page's number." + _ACTIVE_SPAN_NOTE)
    return res


#: ★ 2026-10-02 第二批 ④（唯讀調查的結論；碼不改計算）：同一站相隔約五小時問兩次，`util_pct` 差一倍、
#:   生產小時幾乎不變——**兩次都是 `load_scoreboard_one` 同一條路**（b8433e9／4536e9d 只加了
#:   `page_value` 揭露腿，util 區塊零改動；早那筆的信封 queries 沒有 `app.station_axis_payload`＝當時
#:   還是舊碼）。分母 `active_span_sec`＝**逐機台**「窗內第一段起點→最後一段終點」再 Σ
#:   （`mes_timeline.analyze`）：窗頭往後滑，幾台機台窗頭附近的零星早段滑出窗外 ⇒ 它們的跨度整段塌縮
#:   ⇒ 分母砍半。**定義下兩個都對、但這把尺對窗頭零星段極敏感**；站頁那一格是 `station.utilization`
#:   （應生產時段軸）＝頁面與登記表的正式數字。⇒ 修的是**回答規則**：預設引頁面那一格，`util_pct`
#:   只當診斷用。實測數字只寫在回報，不寫進碼（公開樹真實統計規則）。
_ACTIVE_SPAN_NOTE = (
    " Default to page_value when asked for this station's utilization. The active span behind "
    "util.util_pct is, per machine, first-to-last MES segment inside the rolling window, summed "
    "over machines - one isolated early segment sliding out of the window can halve it within "
    "hours, so the same question asked a few hours apart can get very different util_pct values.")


@mcp.tool()
def get_metric(id: str, scope: str, window: str) -> dict:  # noqa: A002 — 簽名由 SPEC §4.1 定死
    """Return the VALUE of ONE registered metric (explain_metric tells you how it is calculated;
    this tool tells you what it is right now). All three arguments are REQUIRED and are never
    guessed: id must be an exact id from list_metrics; scope is "fleet" or an exact cls from
    list_stations; window looks like "7d" (1-30 days, rejected outside that range, never clamped).
    Only a small set of (id, scope) pairs has a value path today — everything else is reported
    honestly as error:"no_value_path" with a reason, and you should then quote explain_metric
    instead of looking for the number somewhere else. A null value with not_measurable=true means
    the number is STRUCTURALLY unmeasurable (zero-length MES segments): say "not measurable",
    NEVER 0%. A null value with unavailable_reason means no data for this window: that is not zero
    either. unit and caliber come from the registry — never restate the number in another caliber."""
    _p = {"id": id, "scope": scope, "window": window}     # 信封的參數＝使用者原輸入（正規化前）
    m = M.ALL_METRICS.get(id) if isinstance(id, str) else None
    if m is None:                                          # 未知 id ⇒ 逐位元同 `explain_metric`
        return _attach("get_metric",
                       {"error": "unknown_metric", "given": id,
                        "hint": "call list_metrics() and use an exact id; never guess a metric id",
                        "n_known": len(M.ALL_METRICS)},
                       params=_p, metric_ids=(), queries=(), row_count=0, degraded=True)

    ids = (m.id,)
    page = None

    def _err(body, page=None):
        """錯誤分支**照樣掛信封**（§3.1a 末）；`degraded` ⇒ `confidence="low"`（C-HT5）。"""
        return _attach("get_metric", body, params=_p, metric_ids=ids, queries=(),
                       caliber=m.caliber, page=page, row_count=0, degraded=True)

    # 驗證順序固定：id → scope 缺 → window 缺 → window 形 → scope 值域 → id×scope → 取值。
    if not isinstance(scope, str) or not scope.strip():
        return _err({"error": "missing_scope",
                     "hint": "scope is required: 'fleet' or an exact cls from list_stations"})
    if not isinstance(window, str) or not window.strip():
        return _err({"error": "missing_window",
                     "hint": "window is required, e.g. '7d' (1-30 days)"})
    days = _parse_window(window)
    if days is None:
        return _err({"error": "bad_window", "given": window,
                     "hint": "window must look like '7d' (1-30 days)"})
    if scope != _SCOPE_FLEET and scope not in _known_stations():
        return _err({"error": "unknown_scope", "given": scope,
                     "hint": "scope must be 'fleet' or an exact cls; call list_stations first",
                     "known_stations": _known_stations()})

    page = _gm_page((m.verify or {}).get("page_url_tpl"), scope)
    paths = M.VALUE_PATHS.get(m.id) or ()
    kind = _SCOPE_FLEET if scope == _SCOPE_FLEET else "cls"
    vp = next((v for v in paths if v.scope_kind == kind), None)
    if vp is None:
        if paths:                                          # 有值、但不是這個粒度
            only = "/".join(sorted({v.scope_kind for v in paths}))
            return _err({"error": "scope_mismatch", "id": m.id, "given": scope,
                         "hint": (f"{m.id} has a value path only for scope_kind={only}; "
                                  + ("pass an exact cls from list_stations"
                                     if kind == _SCOPE_FLEET else "pass 'fleet'"))}, page)
        return _err({"error": "no_value_path", "id": m.id, "scope": scope,
                     "reason": _NO_VALUE_REASON[m.id],
                     "hint": (f"explain_metric('{m.id}') tells you how it is calculated "
                              "and which page shows it")}, page)

    src = _source()
    try:
        got = _gm_value(vp, scope, days, src)
    except Exception as e:                                 # 誠實降級：不重查第二次（FP-8）
        got = dict(_GM_BLANK, unavailable_reason=_err_category(e, "get_metric"))
    ts = got["ts"] or _now_ts()
    import app as _app
    out = {
        "source": src, "id": m.id, "scope": scope, "window": window, "days": days,
        "value": got["value"], "unit": m.unit, "caliber": m.caliber,
        "title": dict(m.title),
        "not_measurable": got["not_measurable"],
        "unavailable_reason": got["unavailable_reason"],
        "note": got["note"],
    }
    return _attach("get_metric", _app.json_safe(out), params=_p, metric_ids=ids,
                   queries=vp.loaders, caliber=m.caliber, page=page,
                   row_count=got["row_count"], window=_window_back(ts, days),
                   fetched_at=ts, degraded=(got["value"] is None))


# ---- MES 記錄級工具（A2；`SPEC-HERMES-TRUST.md` §7.5／§7.6；核心＝`mes_hermes`）---------
#: ⚠ 這幾支回的是**記錄級**事實（序號／批號／變數字典），比其他工具的彙總數細。
#:   **不對 `/api/board` 開**（與 A1b 對兩支語意工具的處置同形：先只給 `/mcp` 觀察一輪）。
#: ⚠ 口徑與限制住 `mes_hermes` 的 docstring，本層只做「參數驗證 ＋ 三源分派 ＋ 掛信封」。

_CALIBER_RECORD = _CALIBER_UTIL_AXIS   # 記錄級 MES 事實＝設備／時間軸 raw（**不是**公司 yield 口徑）


#: #181（cmms NOTE-83）：`wo_serials_status` 的三個**可選**瘦身參數的閉集與文案（單一出處）。
#: ⚠ 預設值＝現行全回：`status=None`／`summary_only=False`／`limit=None` ⇒ **走舊路徑、一個位元不多**
#:   （不掛 `serials_view`、`params` 只有 `wo_id` ⇒ `evidence_ref`／`params_hash` 同改碼前）。
_WS_VIEW_KEY = "serials_view"
_WS_STATUS_BY_NAME = None       # lazy：{casefold 名: status_id}（`mes_hermes.MATERIAL_STATUS` 反查）


def _ws_norm_view_args(mh, status, summary_only, limit):
    """三個瘦身參數 → `(status_id | None, summary_only, limit | None, err | None)`。

    **不猜、不 clamp**（同 `station_retest` 的 `days`）：
    - `status`：13 個 MaterialStatus 名（不分大小寫、去前後空白，如 "Scrapped"／"in process"）
      或整數 StatusId（`10`／`"10"`；值域外的整數也收＝對得到 `status_code` 為 null 的那幾支）。
      空白字串＝沒篩（LLM client 常把可選字串填 ""）。其他一律 `bad_status`、**不做近似比對**。
    - `summary_only`：必須是 bool（MCP 層由 pydantic 先轉；Python 直呼傳別的型別＝`bad_summary_only`）。
    - `limit`：`None` 或 ≥1 的整數；0／負數／bool／非整數＝`bad_limit`（**拒、不 clamp**）。
    """
    global _WS_STATUS_BY_NAME
    if _WS_STATUS_BY_NAME is None:
        _WS_STATUS_BY_NAME = {n.casefold(): i for i, n in mh.MATERIAL_STATUS.items()}
    sid = None
    if status is not None:
        if isinstance(status, bool):
            sid = "bad"
        elif isinstance(status, int):
            sid = status
        elif isinstance(status, str):
            s = status.strip()
            if not s:
                sid = None
            elif re.fullmatch(r"\d{1,6}", s):
                sid = int(s)
            else:
                sid = _WS_STATUS_BY_NAME.get(" ".join(s.split()).casefold(), "bad")
        else:
            sid = "bad"
        if sid == "bad":
            names = ", ".join(mh.MATERIAL_STATUS[i] for i in sorted(mh.MATERIAL_STATUS))
            return None, False, None, {
                "error": "bad_status", "given": status,
                "hint": ("status must be one MES MaterialStatus name (" + names + "; case-insensitive) "
                         "or an integer StatusId; no fuzzy matching. Call with summary_only=true to "
                         "see serials_view.status_counts for this order first.")}
    if not isinstance(summary_only, bool):
        return None, False, None, {
            "error": "bad_summary_only", "given": summary_only,
            "hint": "summary_only must be true or false"}
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        return None, False, None, {
            "error": "bad_limit", "given": limit,
            "hint": "limit must be an integer >= 1 (omit it to get every serial); out of range is "
                    "refused, not clamped"}
    return sid, summary_only, limit, None


def _ws_apply_view(out, *, status_id, summary_only, limit):
    """全量回傳 → 瘦身視圖（**回新 dict、不改傳入的那一份**＝demo fixture／快取物件不被污染）。

    口徑（#181 定；`SPEC-HERMES-TRUST.md` §12.17）：
    - **工單頭與鳥瞰永遠是全量**：`serial_count`／`n_serial_lot_dst`／`n_rework_instances`／
      `last_station_distribution`／`caveats`／`diag` 一個位元不動——參數只管逐支 `serials` 清單。
      （summary 是「這張工單長怎樣」的鳥瞰，被篩選污染會誤導。）
    - `status` 先篩、`limit` 後截；清單序＝全量的序（MES 查詢 `ORDER BY lot`）。
    - `summary_only=true` ⇒ **`serials` 鍵整個不出現**（缺席＝沒回，不是空清單）；`status` 仍算
      `n_matched`（「這張單報廢幾支」答得出來、不必列清單），`limit` 無作用（照實標 `limit_applied=false`）。
    - 自揭欄 `serials_view` 放在 `serials` **前面**（codex 頭尾各留 5,000 token ⇒ 自揭要在頭段）。
    """
    full = out.get("serials") or []
    matched = full if status_id is None else [s for s in full if s.get("status_id") == status_id]
    returned = None if summary_only else (matched[:limit] if limit is not None else matched)
    n_ret = 0 if returned is None else len(returned)
    counts = {}
    for s in full:
        k = s.get("status_id")
        c = counts.setdefault(k, {"status_id": k, "status_code": s.get("status_code"), "n_serial": 0})
        c["n_serial"] += 1
    status_counts = sorted(counts.values(),
                           key=lambda c: (-c["n_serial"], c["status_id"] is None, c["status_id"] or 0))
    code = None
    if status_id is not None:
        code = _mes_hermes().MATERIAL_STATUS.get(status_id)
    truncated = (not summary_only) and n_ret < len(matched)
    if summary_only:
        note = (f"summary_only：逐支序號清單未回（serials 鍵刻意缺席）；本單共 {len(full)} 支"
                + (f"、其中 status={code or status_id} 共 {len(matched)} 支" if status_id is not None else "")
                + "。要清單請不帶 summary_only（可加 status／limit 縮小）。")
    else:
        note = (f"本單共 {len(full)} 支"
                + (f"；status={code or status_id} 符合 {len(matched)} 支" if status_id is not None else "")
                + (f"；只回前 {n_ret} 支（limit={limit}，依序號 lot 排序），其餘 {len(matched) - n_ret} 支未列"
                   if truncated else
                   ("；沒有符合的序號（serials 為空清單＝真的 0 支，不是沒回）" if not matched
                    else f"；全數列出（{n_ret} 支）"))
                + "。")
    note += ("serial_count／n_serial_lot_dst／n_rework_instances／last_station_distribution "
             "一律是全量、不受 status／limit／summary_only 影響。")
    view = {
        "summary_only": summary_only,
        "status_filter": (None if status_id is None else {"status_id": status_id, "status_code": code}),
        "limit": limit,
        "limit_applied": (limit is not None and not summary_only),
        "n_total": len(full),
        "n_matched": len(matched),
        "n_returned": n_ret,
        "truncated": truncated,
        "serials_omitted": summary_only,
        "status_counts": status_counts,
        "note": note,
    }
    new = {}
    for k, v in out.items():
        if k == "serials":
            new[_WS_VIEW_KEY] = view
            if returned is not None:
                new["serials"] = returned
        else:
            new[k] = v
    if _WS_VIEW_KEY not in new:                 # 防呆：沒有 serials 鍵的形（不該發生）也要自揭
        new[_WS_VIEW_KEY] = view
    return new


@_synth_na(_CALIBER_RECORD)
def wo_serials_status(wo_id: str, status: str | int | None = None, summary_only: bool = False,
                      limit: int | None = None) -> dict:
    """For ONE work order: how many product serials it has and which station each of them last
    completed. wo_id is the MES work order ("WO910001/1") or the SAP order number ("9000100001");
    revisions of the same base order are collected together (see work_order.revisions).
    OPTIONAL, to keep a large order's reply small (all omitted = the full list, unchanged):
    status = one MES MaterialStatus name ("Scrapped", "WIP", "Hold", ...; case-insensitive) or an
    integer StatusId - only serials with that status are listed; summary_only = true - return the
    order header, last_station_distribution, caveats and serials_view.status_counts, but NOT the
    per-serial list (the "serials" key is then absent - absent means not returned, not empty);
    limit = N - list only the first N serials (ordered by serial/lot), after the status filter.
    Whenever any of these is used, serials_view says so: n_total / n_matched / n_returned /
    truncated. serial_count, last_station_distribution, n_serial_lot_dst and n_rework_instances
    ALWAYS describe the whole order, never the filtered list. If truncated=true, never say the
    list is complete. Use summary_only first for a big order, then status= for the list you need.
    READ THE caveats ARRAY BEFORE ANSWERING — it carries nine limits that change what you may
    say, in particular: (1) "last station" means the last COMPLETED process segment; this tool
    has no open-segment semantics, so never say a unit "is running at" a station. (2) n_seg is a
    LOWER BOUND on completed segments, never "which step it is on". (3) A null
    last_segment_duration_s with duration_not_measurable=true means NOT MEASURABLE, not 0 seconds
    (many stations record instantaneous segments). (4) serial_count and qty_requested are two
    different numbers — a live order legitimately has fewer serials than requested; never
    subtract them and call it missing material. (5) outgoing_status_id is printed as a NUMBER
    because MES has no lookup table for it. (6) last_result is the raw MES value (PASS/FAIL/null).
    Serials whose only trace is the MES internal event stream are reported honestly as
    no_process_segment with last_event_at. In demo mode an unknown order says
    unavailable_reason="not_in_demo_fixture" — that is NOT "no such work order"."""
    # ★ #181：`params` 只收**非預設**的瘦身參數 ⇒ 預設呼叫的 `params`＝`{"wo_id"}`，
    #   `evidence_ref`／`tool_call.params_hash` 與改碼前逐位元相同（C-HT10）。
    _p = {"wo_id": wo_id}
    if status is not None:
        _p["status"] = status
    if summary_only is not False:
        _p["summary_only"] = summary_only
    if limit is not None:
        _p["limit"] = limit
    mh = _mes_hermes()
    if mh is None:
        return _attach("wo_serials_status",
                       {"error": "unavailable", "given": wo_id,
                        "hint": "the MES record-level module is not available on this server"},
                       params=_p, caliber=_CALIBER_RECORD, row_count=0, degraded=True)
    norm, err = mh.normalize_wo_id(wo_id)
    if err is not None:                       # missing_wo_id／bad_wo_id（400 形）
        return _attach("wo_serials_status", err, params=_p, caliber=_CALIBER_RECORD,
                       row_count=0, degraded=True)
    # 瘦身參數壞 ⇒ 400 形、**不查 MES**（先驗參數再打 DB；工單號錯誤仍優先回，舊行為不變）
    v_sid, v_sum, v_lim, verr = _ws_norm_view_args(mh, status, summary_only, limit)
    if verr is not None:
        return _attach("wo_serials_status", verr, params=_p, caliber=_CALIBER_RECORD,
                       row_count=0, degraded=True)
    want_view = (v_sid is not None) or v_sum or (v_lim is not None)
    src = _source()
    try:
        import board_data as _wd
        out = _wd.get_wo_serials(src, norm)
    except Exception as e:                    # 誠實降級：不重查第二次（FP-8）
        # ★ `diag.error_detail`（2026-09-21 live 首跑教訓）：只回分類碼＋空 `diag` ⇒ 人與
        #   agent 都無從診斷。分類碼答「哪一類壞了」、`error_detail` 答「MES 說了什麼」。
        out = {"wo_id": norm, "found": False,
               "unavailable_reason": _err_category(e, "wo_serials_status"),
               "diag": {"error_detail": mh.error_detail(e)}}
    diag = out.get("diag") or {}
    n = out.get("serial_count") or 0
    # 窗＝`{from: since_used, to: fetched_at}`（段的時間下界 → 取數時刻），不另造第二套時間。
    ts = diag.get("fetched_at") or _now_ts()
    win = ({"from": _fmt_ts(diag.get("since_used")), "to": ts}
           if diag.get("since_used") else _NO_WINDOW)
    degraded = (not out.get("found")) or bool(out.get("unavailable_reason")) or any(
        diag.get(k) for k in ("serial_cap_hit", "segment_cap_hit", "event_cap_hit"))
    if want_view and out.get("found") is True:
        # ★ #181：視圖只在查到時套（查不到／降級形本來就 `serials: []`，原樣回）。
        #   `row_count` 仍＝`serial_count`（全量母體）：篩到 0 支是高信心的「0 支」，不是降級。
        out = _ws_apply_view(out, status_id=v_sid, summary_only=v_sum, limit=v_lim)
    return _attach("wo_serials_status", out, params=_p, caliber=_CALIBER_RECORD,
                   # ★ `page=None`：沒有「人可以自己去看那個數字的那一頁」（§3.1a）。
                   page=None, row_count=n, window=win, fetched_at=ts, degraded=degraded)


def _compact_json(out) -> str:
    """#181 (a)：**只給 `wo_serials_status`** 的緊湊序列化（FastMCP 全域預設是 `indent=2`、不動它）。

    用**與 FastMCP 同一支序列化器**（`pydantic_core.to_json(..., fallback=str)`）、只拿掉 `indent`
    ⇒ 鍵、值、鍵序、非 ASCII 原樣（µ／中文不轉義）都與舊輸出相同，**只少空白**（selftest 釘）。"""
    import pydantic_core
    return pydantic_core.to_json(out, fallback=str).decode()


def _wo_serials_status_mcp(wo_id: str, status: str | int | None = None, summary_only: bool = False,
                           limit: int | None = None) -> str:
    # `status: str | int`：JSON 整數 StatusId（`10`）要能過 FastMCP 的 pydantic 參數驗證
    # （pydantic v2 不把 int 轉 str；只寫 `str` 的話整數會在進本體前就被擋掉）。
    # MCP 面的薄殼：回**字串** ⇒ FastMCP 原樣放進 TextContent（不再套 indent=2）。
    # Python 直呼（selftest／smoke／in-process）仍拿 dict＝`wo_serials_status` 本體。
    return _compact_json(wo_serials_status(wo_id, status=status, summary_only=summary_only,
                                           limit=limit))


# `__name__` 對齊 ⇒ inputSchema 的 title 仍是 `wo_serials_statusArguments`（tools/list 只多三個可選參數）。
_wo_serials_status_mcp.__name__ = "wo_serials_status"
# `structured_output=False`：回傳型別是 str，自動偵測會包成 `{"result": "<整串 JSON>"}` 的
# structuredContent ⇒ 同一份內容送兩次、長度翻倍＝正好違背本單目的。明寫 False＝只有 TextContent
# （＝改碼前 `-> dict` 的 outputSchema 也是 null，形不變）。
mcp.add_tool(_wo_serials_status_mcp, name="wo_serials_status",
             description=wo_serials_status.__doc__, structured_output=False)


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def station_variables(cls: str) -> dict:
    """List the measurement / recipe VARIABLES registered for one production station (the MES
    data dictionary for that segment class): name, description, type and unit of measure,
    grouped by vdfClassID (process parameter / segment data / material failure code / equipment
    failure code). This is "what this station records", NOT "what it measured this time" — the
    values live in the segment EAV tables and this tool never touches them. Resolve the station
    with list_stations first; this tool also accepts MES segment classes outside that list, but
    it NEVER guesses: a class that does not exist is reported honestly (found=false,
    reason="class_not_in_mes"), and a class that exists with zero variables is reported as
    found=true with n_vars=0 — those are two different answers, do not merge them. In demo mode
    a class that is simply absent from the fixture says unavailable_reason="not_in_demo_fixture",
    which does NOT mean the station has no variables. segMESReserved is refused on purpose: it is
    the MES internal event audit stream, not a production station."""
    _p = {"cls": cls}
    mh = _mes_hermes()
    if mh is None:
        return _attach("station_variables",
                       {"error": "unavailable", "given": cls,
                        "hint": "the MES record-level module is not available on this server"},
                       params=_p, caliber=_CALIBER_RECORD, row_count=0, degraded=True)
    norm, err = mh.normalize_cls(cls)
    if err is not None:                       # missing_cls／bad_cls／excluded_class（400 形）
        return _attach("station_variables", err, params=_p, caliber=_CALIBER_RECORD,
                       row_count=0, degraded=True)
    src = _source()
    try:
        import board_data as _wd
        out = _wd.get_station_vars(src, norm)
    except Exception as e:                    # 誠實降級：不重查第二次（FP-8）
        out = {"cls": norm, "found": False,                      # ★ diag 同上（可診斷）
               "unavailable_reason": _err_category(e, "station_variables"),
               "diag": {"error_detail": mh.error_detail(e)}}
    n = out.get("n_vars") or 0
    return _attach("station_variables", out, params=_p, caliber=_CALIBER_RECORD,
                   # ★ 沒有「人可以自己去看那個數字的那一頁」⇒ `page=None`
                   #   （§3.1a：**不得為了欄位好看掛一個不相干的頁**）。
                   page=None, row_count=n,
                   # ★ 字典沒有時間窗（同 `updown_now` 的處置）；`freshness` 仍是 `_freshness()`
                   #   ——這是 MES 裡的資料，不是 repo 內的登記表常數（那才叫 `static`）。
                   window=_NO_WINDOW,
                   degraded=(not out.get("found")) or bool(out.get("unavailable_reason")))


# ---- 材料／批號／出貨（助理 MES 查詢第三批·包①；`SPEC-HERMES-TRUST.md` §7.6.8）----------
#: 核心＝`mes_material`（凍結參數化 SQL，逐字出自 `_PROBE-ASSISTANT-MES-FAMILY-20260922.sql`）。
#: 本層同 A2：**只做參數驗證 ＋ 三源分派 ＋ 掛信封**；口徑與 caveats 住 `mes_material`。
#: ⚠ 同樣**不上 `/api/board`**（`_Board_WEBMCP_TOOLS` 白名單一個位元不動）。

def _material_tool(tool, key, params, rows_of):
    """四支材料工具的共用尾段：三源取數（`board_data` 恆不 raise）→ 信封。"""
    mm = _mes_material()
    src = _source()
    try:
        import board_data as _wd
        out = _wd.get_mes_material(tool, src, key)
    except Exception as e:                    # 誠實降級：不重查第二次（FP-8）
        out = mm.blank(tool, key, found=False,
                       unavailable_reason=_err_category(e, tool))
        out["diag"]["error_detail"] = mm.error_detail(e)
    diag = out.get("diag") or {}
    ts = _fmt_ts(diag.get("fetched_at")) or _now_ts()
    if tool == "shipment_lookup":
        w = diag.get("window") or {}
        win = ({"from": _fmt_ts(w.get("from")), "to": _fmt_ts(w.get("to"))}
               if w.get("from") else _NO_WINDOW)
    else:
        win = ({"from": _fmt_ts(diag.get("since_used")), "to": ts}
               if diag.get("since_used") else _NO_WINDOW)
    degraded = ((not out.get("found")) or bool(out.get("unavailable_reason"))
                or bool(out.get("truncated")))
    return _attach(tool, out, params=params, caliber=_CALIBER_RECORD,
                   # ★ 沒有「人可以自己去看那個數字的那一頁」⇒ `page=None`（§3.1a）。
                   page=None, row_count=rows_of(out), window=win, fetched_at=ts,
                   degraded=degraded)


def _material_unavailable(tool, given, params):
    return _attach(tool, {"error": "unavailable", "given": given,
                          "hint": "the MES material module is not available on this server"},
                   params=params, caliber=_CALIBER_RECORD, row_count=0, degraded=True)


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def serial_genealogy(code: str) -> dict:
    """For ONE product serial (module / unit code, e.g. "J0SYN01"): which production
    MATERIAL LOTS it consumed (material class, lot, which station, when, for which stage), the
    stages it went through (module -> unit -> packaged unit -> ...), its last
    process segment with the machine(s), and the work orders it passed through.
    Example questions: "J0SYN01 用了哪些材料批號？", "What fluid lots went into J0SYN02?",
    "這顆後來組進哪個包裝、最後在哪一站？".
    READ caveats BEFORE ANSWERING: last_segment is the last RECORDED process segment (MES
    internal events excluded) — never say the unit "is running at" a station, and it is not a
    stop date. upstream_stage_links are the serial's own earlier stage being assembled into the
    next stage (same code) — they are NOT production materials. If truncated=true every list is
    a LOWER BOUND — never say "only these". Material lots belong to lot_where_used; a code MES
    does not know returns found=false, reason="no_material_matched". In demo mode an unknown
    serial says unavailable_reason="not_in_demo_fixture" — that is NOT "no such serial"."""
    _p = {"code": code}
    mm = _mes_material()
    if mm is None:
        return _material_unavailable("serial_genealogy", code, _p)
    norm, err = mm.normalize_code(code, kind="serial")
    if err is not None:
        return _attach("serial_genealogy", err, params=_p, caliber=_CALIBER_RECORD,
                       row_count=0, degraded=True)
    return _material_tool("serial_genealogy", norm, _p,
                          lambda o: len(o.get("materials") or []))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def wo_materials(wo_id: str) -> dict:
    """For ONE MES production work order ("WO910001/1", or the SAP number "9000100001"): which
    material LOTS its segments consumed, rolled up per lot (material class, definition, number of
    uses, number of product serials, total quantity with its unit, first/last use, stations).
    Example questions: "WO910001/1 用了哪些料的批號？", "Which DAF lots did WO910002/1 use?".
    READ caveats BEFORE ANSWERING: this reads the LATEST 1000 consumption records only — when
    truncated=true the lot list is a LOWER BOUND (older lots may be missing); say so and offer
    serial_genealogy for a complete per-serial list. Never add quantities across different lots
    or units. A null product_line on an old (2019-era) order is normal, not broken data.
    A work order MES does not know returns reason="no_production_request_matched"."""
    _p = {"wo_id": wo_id}
    mm = _mes_material()
    if mm is None:
        return _material_unavailable("wo_materials", wo_id, _p)
    norm, err = mm.normalize_wo_id(wo_id)
    if err is not None:                       # missing_wo_id／bad_wo_id（同 wo_serials_status）
        return _attach("wo_materials", err, params=_p, caliber=_CALIBER_RECORD,
                       row_count=0, degraded=True)
    return _material_tool("wo_materials", norm, _p,
                          lambda o: o.get("n_material_rows") or 0)


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def lot_where_used(lot: str) -> dict:
    """For ONE material lot (e.g. "LA-9100001-9001", "LT910001/1"): which product serials, work
    orders, stations and times consumed it (recall scope), plus the lot's own identity (class,
    initial / consumed / scrapped quantity, expiry) and its supplier / supplier lot / receipt
    date when MES still has the receipt record.
    Example questions: "LA-9100001-9001 這批料用在哪些工單？", "Which modules used DAF lot
    LA-9100002-9002?", "這批紙是哪家供應商、供應商批號多少？".
    READ caveats BEFORE ANSWERING: this reads the LATEST 1000 consumption records — if
    truncated=true the affected serials / work orders are a LOWER BOUND, never "the full recall
    list". It does NOT return machines. A not_measurable entry with reason
    "receipt_record_not_retained" means MES no longer keeps that receipt (old lots) — it does
    NOT mean the lot had no supplier."""
    _p = {"lot": lot}
    mm = _mes_material()
    if mm is None:
        return _material_unavailable("lot_where_used", lot, _p)
    norm, err = mm.normalize_code(lot, kind="lot")
    if err is not None:
        return _attach("lot_where_used", err, params=_p, caliber=_CALIBER_RECORD,
                       row_count=0, degraded=True)
    return _material_tool("lot_where_used", norm, _p, lambda o: len(o.get("used_in") or []))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def shipment_lookup(lot: str | None = None, wo_id: str | None = None,
                    days: int | None = None) -> dict:
    """Shipment records (customer, purchase order, carton, pallet, packlist, QA time) — one row
    per shipped module product. Give a product serial as `lot` (the PRIMARY key, e.g.
    "V0SYN01"); `wo_id` is a SECONDARY, UNVERIFIED lookup; give neither to list the latest
    shipments in the last `days` days (1-31, default 7; out of range is refused, not clamped).
    Example questions: "V0SYN01 出貨了嗎？出給誰？", "這禮拜出貨給誰？".
    READ caveats BEFORE ANSWERING: looking up by work order found 0 rows for all three probe
    orders (even a 2019 order that must have shipped) — so "not found by work order" does NOT
    mean "not shipped"; ask for a serial instead. Customer names are business data: remind the
    user to anonymise before sharing outside."""
    _p = {"lot": lot, "wo_id": wo_id, "days": days}
    mm = _mes_material()
    if mm is None:
        return _material_unavailable("shipment_lookup", lot or wo_id, _p)
    _mode, key, err = mm.normalize_ship_args(lot=lot, wo_id=wo_id, days=days)
    if err is not None:
        return _attach("shipment_lookup", err, params=_p, caliber=_CALIBER_RECORD,
                       row_count=0, degraded=True)
    return _material_tool("shipment_lookup", key, _p, lambda o: o.get("n_shipments") or 0)


# ---- MES 工具包②（9 支；`SPEC-HERMES-TRUST.md` §7.6.10；ROADMAP #136／#173／#176）------------
#: 核心＝`mes_etest`／`mes_equipment`／`mes_segment`／`mes_route`（凍結參數化 SQL，逐字出自第二／第三支探針；
#:   偏離清單、閉集、caveats 全文**住各模組**＝唯一出處）。本層同包①：**只做參數驗證 ＋ 三源分派 ＋
#:   `ok_payload` 驗形 ＋ 掛信封**。⚠ 同樣**不上 `/api/board`**（`_Board_WEBMCP_TOOLS` 一個位元不動）。
#: ⚠ 四個模組都 import `mes_material`（→ `mes_qlik`＝pyodbc）⇒ 一律函式體內 lazy import（同 `_mes_material`）。
_PACK2_MODS: dict[str, object] = {}
_PACK2_TRIED: set[str] = set()

#: 工具 → (核心模組名, `board_data` 分派函式名)。
_PACK2_TOOLS = {
    "station_failure_pareto": ("mes_etest", "get_mes_etest"),
    "module_die_etest": ("mes_etest", "get_mes_etest"),
    "equipment_status_events": ("mes_equipment", "get_mes_equipment"),
    "equipment_modules": ("mes_equipment", "get_mes_equipment"),
    "equipment_calibration": ("mes_equipment", "get_mes_equipment"),
    "segment_data": ("mes_segment", "get_mes_segment"),
    "station_spec_limits": ("mes_segment", "get_mes_segment"),
    "station_retest": ("mes_segment", "get_mes_segment"),
    "wo_route": ("mes_route", "get_mes_route"),
}


def _pack2_mod(name):
    """lazy import 包② 核心模組（未就緒 ⇒ None＝工具誠實回 `unavailable`、不炸整個 MCP）。"""
    if name not in _PACK2_TRIED:
        _PACK2_TRIED.add(name)
        try:
            import importlib
            _PACK2_MODS[name] = importlib.import_module(name)
        except Exception as e:                       # noqa: BLE001
            print(f"[board_mcp] {name} unavailable ({type(e).__name__}: {str(e)[:120]})",
                  flush=True)
            _PACK2_MODS[name] = None
    return _PACK2_MODS.get(name)


def _pack2_unavailable(tool, given, params):
    return _attach(tool, {"error": "unavailable", "given": given,
                          "hint": "the MES record-level module is not available on this server"},
                   params=params, caliber=_CALIBER_RECORD, row_count=0, degraded=True)


def _pack2_rows(tool, out) -> int:
    """`row_count`＝這一次回給 agent 的記錄列數（0 ⇒ confidence low；§3.2）。"""
    if tool == "station_failure_pareto":
        return out.get("n_segments") or 0
    if tool == "module_die_etest":
        return out.get("n_attempts") or 0
    if tool == "equipment_status_events":
        return out.get("n_events") or 0
    if tool == "equipment_modules":
        return out.get("n_edges") or 0
    if tool == "equipment_calibration":
        return len(out.get("records") or []) + (1 if out.get("last_before_window") else 0)
    if tool == "segment_data":
        return len(out.get("segments") or [])
    if tool == "station_spec_limits":
        return out.get("n_limit_rows") or 0
    if tool == "station_retest":
        return out.get("n_segments") or 0
    if tool == "wo_route":
        return len(out.get("route") or [])
    raise KeyError(tool)


def _pack2_tool(tool, key, params):
    """九支包② 工具的共用尾段：三源取數（`board_data.get_mes_*` 恆不 raise）→ 驗形 → 信封。

    `degraded`（⇒ confidence=low）＝查無／取不到／`truncated`／**形狀驗不過**。唯一放行：
    `station_spec_limits` 的 `found=true` ＋ `unavailable_reason="no_pass_criteria_limits"`
    （`mes_segment.FOUND_UNAVAILABLE_OK`：限值 view 零列＝「MES 裡存在但沒有限」，是答案、不是取不到；
    `row_count=0` 仍使 confidence=low——agent 要照 caveat 說「MES 沒有限值」、不是「沒超規」）。"""
    mod_name, getter = _PACK2_TOOLS[tool]
    mod = _pack2_mod(mod_name)
    src = _source()
    try:
        import board_data as _wd
        out = getattr(_wd, getter)(tool, src, key)
    except Exception as e:                    # 誠實降級：不重查第二次（FP-8）
        out = mod.blank(tool, key, found=False, unavailable_reason=_err_category(e, tool))
        out["diag"]["error_detail"] = mod.error_detail(e)
    diag = out.get("diag") or {}
    ts = _fmt_ts(diag.get("fetched_at")) or _now_ts()
    w = out.get("window") if isinstance(out.get("window"), dict) else {}
    if w.get("from"):
        win = {"from": _fmt_ts(w.get("from")), "to": _fmt_ts(w.get("to"))}
    elif diag.get("since_used"):
        win = {"from": _fmt_ts(diag.get("since_used")), "to": ts}
    else:
        win = _NO_WINDOW
    ur = out.get("unavailable_reason")
    ur_ok = bool(out.get("found")) and ur in getattr(mod, "FOUND_UNAVAILABLE_OK", ())
    shape_ok = mod.ok_payload(tool, out)
    if not shape_ok:
        out.setdefault("diag", {})["payload_shape_ok"] = False
    degraded = ((not out.get("found")) or (bool(ur) and not ur_ok)
                or bool(out.get("truncated")) or not shape_ok)
    return _attach(tool, out, params=params, caliber=_CALIBER_RECORD,
                   # ★ 沒有「人可以自己去看那個數字的那一頁」⇒ `page=None`（§3.1a）。
                   page=None, row_count=_pack2_rows(tool, out), window=win, fetched_at=ts,
                   degraded=degraded)


def _pack2_entry(tool, params, given, normalize):
    """參數驗證入口：模組載不到 ⇒ unavailable；`normalize(mod)` 回 `(key, err)`，err ⇒ 400 形＋low。"""
    mod = _pack2_mod(_PACK2_TOOLS[tool][0])
    if mod is None:
        return _pack2_unavailable(tool, given, params)
    key, err = normalize(mod)
    if err is not None:
        return _attach(tool, err, params=params, caliber=_CALIBER_RECORD, row_count=0,
                       degraded=True)
    return _pack2_tool(tool, key, params)


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def station_failure_pareto(cls: str, days: int = 7) -> dict:
    """Failure-code PARETO for ONE test station over the last `days` days (1-31, default 7; out
    of range is refused, not clamped): which MES material-failure codes the FAIL segments carried,
    ranked by the number of FAIL segments carrying each code (top 25), with FAIL / PASS / other
    segment counts and how many FAIL segments carry a code (fail_coverage).
    Only four stations are supported (probe-verified: 100% of FAIL segments carry a code, 0% of
    PASS): CAL (segCalibrationTest), CLN (segCleanTest), AET (segAuxElectricalTest),
    OQCT / OQC (segFinalOQC). Any other station returns not_measurable
    "station_unsupported" WITHOUT querying MES - that does NOT mean the station has no failures.
    ALSO USE THIS TOOL FOR OQC BINNING QUESTIONS ("OQC 分箱／bin 分布", "what fails at OQC"): the
    MES OQC bin data is not usable (only "Bin 3A" in 30 days, zero binning segments), so OQC
    failure content is answered by this failure-code ranking.
    Example questions: "CAL 這禮拜最常見的失效碼？", "CLN 近 14 天 FAIL 都是什麼碼？",
    "OQC 分箱／不良分布？", "Top failure codes at AET last week?".
    READ caveats BEFORE ANSWERING: ranking is by FAIL segments carrying the code (n_fail_seg);
    CLN and AET also have COUNT-type codes (value_kind="count": sum_value counts occurrences and
    must never be compared with segment counts). AET segments include module AND die segments,
    so segment counts are not module counts. If truncated=true only the top 25 codes are
    listed. This is not yield (use yield_summary); it never says WHY a unit failed. In demo mode
    the ranking is a fixed 7-day probe sample that does not change with `days`."""
    _p = {"cls": cls, "days": days}
    return _pack2_entry("station_failure_pareto", _p, cls,
                        lambda m: m.normalize_pareto_args(cls, days))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def module_die_etest(serial: str) -> dict:
    """Post-wire-bond electrical test (AET) of ONE MODULE serial: the module-level result
    of every test attempt (2 variables each) plus, for the LATEST attempt, the 11 dies: position,
    die lot, inferred substrate, start time, PASS/FAIL, 17 die-level variables and failure codes.
    The key is a MODULE serial. For a unit / packaged-unit serial, FIRST call
    serial_genealogy to find its module serial, then call this tool with that serial.
    Example questions: "J0SYN09 的 AET 電測結果？", "Which die of J0SYN09 failed AET?",
    "這顆 module 的 11 顆 die 各來自哪片 substrate？".
    READ caveats BEFORE ANSWERING: varAET_Iddq / varAET_Iposq exist at BOTH module level and die
    level on DIFFERENT scales - never compare or mix them across levels. `substrate` is INFERRED from
    the die lot (<substrate>_R<nn>_D<n>, the part before _R) - say "inferred". A die count other than
    11 returns not_measurable "die_count_mismatch"; a serial with no AET module segment returns
    not_measurable "serial_not_found". varAET_bad_actuators is sparse: a missing value is "not
    written", NOT 0. In demo mode an unknown serial says unavailable_reason="not_in_demo_fixture"
    - that is NOT "no such serial"."""
    _p = {"serial": serial}
    return _pack2_entry("module_die_etest", _p, serial,
                        lambda m: m.normalize_serial(serial))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def equipment_status_events(eid: str, days: int = 30) -> dict:
    """MES error / status EVENTS of ONE machine (MES EID, e.g. "EID-07905" or "7905") over the
    last `days` days (1-365, default 30; out of range is refused, not clamped): the latest 200
    events (time, error code, code category, MES dictionary label, status written with the event)
    plus FULL-WINDOW counts by code and by status, and the machine's MES master record.
    Example questions: "EID-07905 最近 30 天 MES 報了哪些錯誤碼？", "Which error code fires most on
    08158 this quarter?".
    READ caveats BEFORE ANSWERING: MES events are POINTS IN TIME - duration_s is always null and
    an event count is NOT downtime (hours come from equipment_downtime / downtime_monthly). Most
    events do not change the machine status, so an event does not mean the machine stopped. Zero
    events (reason "no_events_in_window") means MES recorded nothing - many machines (e.g.
    metrology) never write events - it does NOT mean "no faults". Only MES is read; the machine's
    own event log is not collected. If truncated=true the event LIST is the latest 200 but the
    counts are still full-window. An EID MES does not know returns not_measurable "eid_not_found"
    (MES EIDs only, not cmms asset numbers). Free-text comments are never returned."""
    _p = {"eid": eid, "days": days}
    return _pack2_entry("equipment_status_events", _p, eid,
                        lambda m: m.normalize_args("equipment_status_events", eid, days))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def equipment_modules(eid: str) -> dict:
    """Parent / child MODULE links of ONE machine in MES (MES EID): which machines it is a bay
    of (parents) and which modules hang under it (children) - EID, name, class, first / last time
    the link was recorded, and whether it appears in the latest configuration record.
    Example questions: "EID-07905 底下掛了哪些子槽位？", "Which host machine is 08158 a bay of?".
    READ caveats BEFORE ANSWERING: the MES link table is a HISTORY of configuration records, not a
    live snapshot - in_latest_config is an INFERENCE (MES has no "currently mounted" field);
    n_edges counts raw rows, n_children / n_parents are de-duplicated. Zero links (reason
    "no_module_edges") means a standalone machine in MES, not an error. If the machine has
    parents, its utilisation / downtime is usually recorded on the parent. An EID MES does not
    know returns not_measurable "eid_not_found"."""
    _p = {"eid": eid}
    return _pack2_entry("equipment_modules", _p, eid,
                        lambda m: m.normalize_args("equipment_modules", eid))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def equipment_calibration(eid: str, days: int = 30) -> dict:
    """CALIBRATION history of ONE machine as recorded in MES (MES EID) over the last `days` days
    (1-365, default 30): each record's time, overall PASS/FAIL, next due date and the parsed items
    (item, result, measured value with min / max, last calibrated, due, reference-part EID), plus
    the registered last-calibration / due dates. When the window has no record, the most recent
    earlier record is attached as last_before_window.
    Example questions: "EID-07905 上次校正是什麼時候？結果？", "Is 08158's calibration overdue?".
    READ caveats BEFORE ANSWERING: only whitelisted parsed fields are returned - the raw
    calibration text is never shown. A record whose content is an empty shell (payload_empty /
    not_measurable "calibration_payload_empty") means "written but empty" - NOT zero records and
    NOT a PASS; some machines keep calibration on their child modules (use equipment_modules to
    find them). reason "no_calibration_records" means MES has none - calibration may be managed
    outside MES, it does NOT mean "never calibrated". If truncated=true only the latest records
    are listed."""
    _p = {"eid": eid, "days": days}
    return _pack2_entry("equipment_calibration", _p, eid,
                        lambda m: m.normalize_args("equipment_calibration", eid, days))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def segment_data(code: str, station: str, which: str = "last") -> dict:
    """The MEASURED VALUES of ONE product serial at ONE station: for the chosen segment(s)
    (which = "last" (default) / "first" / "all", all = up to 20, newest first) the start time,
    PASS/FAIL, recipe (PR version), machine EID(s), every numeric variable's value with its three
    limit sets (spec / control / valid, matched per recipe version and variable) and per-variable
    flags (out_of_spec, control_exceeded, invalid_measurement, limit_ambiguous), plus failure
    codes. Each segment's summary.out_of_spec_variables is the "which variables were out of spec"
    view. `station` = a Tendline Board station (CAL / CLN / AET / OQCT or the full segment class; see
    list_stations).
    Example questions: "J0SYN17 在 CLN 的量測值？哪些超規？", "What did J0SYN17 measure at CLN the
    first time?", "這顆在 CLN 重測了幾次、每次的值？" (which="all").
    READ caveats BEFORE ANSWERING: OUT OF SPEC != SEGMENT FAIL - this tool answers "which variables
    exceeded spec", never "why it failed". Only the spec limits count as out of spec;
    control_exceeded is disclosed only; a value outside the valid limits is an invalid
    measurement and is not judged. A variable with no limits has null flags (NOT "in spec").
    Limits are per recipe version, never station-wide. Text and array variables are listed by
    name only (unsupported_variables), not valued. A variable MES did not write is "not written",
    NOT 0. Only segCleanTest is known to have limits. In demo mode an unknown serial says
    unavailable_reason="not_in_demo_fixture" - NOT "no such serial"."""
    _p = {"code": code, "station": station, "which": which}
    return _pack2_entry("segment_data", _p, code,
                        lambda m: m.normalize_segment_args(code, station, which))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def station_spec_limits(cls: str, recipe: str | None = None) -> dict:
    """SPEC LIMITS of ONE station from MES pass-criteria: per recipe (PR version, e.g.
    "QR910001/12"), per variable, the three limit sets spec / control / valid x min / max, with
    limit_ambiguous flags. By default only Active recipe versions are listed (others are counted
    in by_designation); give `recipe` to see one specific version.
    Example questions: "CLN 的規格上下限？", "What are the spec limits of QR910001/12?".
    READ caveats BEFORE ANSWERING: only spec is the PASS limit; control / valid semantics are
    inferred. Limits are per recipe version - never quote one number as "the station's limit".
    Only segCleanTest is known to have limits in MES: other stations return found=true with
    unavailable_reason="no_pass_criteria_limits" and n_limit_rows=0 - that means MES has NO
    pass-criteria limits for that station (reading the recipe itself is a separate task); it does
    NOT mean "no spec" or "everything passes". limit_ambiguous=true (MatchingIniRecordCount>1)
    means MES itself may have mis-identified the limit. Document values in knowledge cards may
    differ - MES wins."""
    _p = {"cls": cls, "recipe": recipe}
    return _pack2_entry("station_spec_limits", _p, cls,
                        lambda m: m.normalize_limits_args(cls, recipe))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def station_retest(cls: str, days: int = 7) -> dict:
    """RETEST pattern of ONE station over the last `days` days (1-31, default 7; out of range is
    refused, not clamped): total segments, FAIL / PASS / other, how many serials were tested more
    than once, the maximum segments per serial, FAILs among retested serials, a split by material
    class, and the top 50 serials by number of segments.
    Example questions: "CLN 這禮拜重測多少顆？", "Which serials were retested most at CAL?".
    READ caveats BEFORE ANSWERING: MES has NO retest-reason field - never say a unit was retested
    "because it failed"; n_segments and n_fail are separate numbers. CLN / AET segments include
    die segments (see by_material_class). n_serials_retested counts ALL material classes and is
    NOT a PH count: quote n_ph_retested as PH (modules), n_die_retested as dies (never PH) and
    n_other_retested as other classes. If truncated=true the counts / list are a LOWER BOUND.
    FAIL here means stpTestResult='FAIL'. In demo mode the numbers are a fixed probe sample of the
    latest 500 segments that does not change with `days`."""
    _p = {"cls": cls, "days": days}
    return _pack2_entry("station_retest", _p, cls,
                        lambda m: m.normalize_retest_args(cls, days))


@mcp.tool()
@_synth_na(_CALIBER_RECORD)
def wo_route(wo_id: str) -> dict:
    """ROUTE of ONE MES work order: header (product class / definition / line, workflow, quantity
    requested, start / completion), the station sequence (workflow order) and, for every step,
    which segment-definition (recipe / PR) version it ran: `resolution` = "unique" (only one
    version) / "ran_wo" (this order's serials ran exactly one of the candidates - chosen) /
    "ran_wo_multi" (they ran several - all listed by this order's segment count, none chosen) /
    "ambiguous" (no record - all listed by plant-wide 90-day volume, none chosen).
    THE WORK ORDER NUMBER MUST INCLUDE ITS REVISION SUFFIX "/n" (e.g. "WO910001/1", not
    "WO910001"): this tool matches the MES order id exactly; without "/n" MES finds nothing.
    Example questions: "WO910002/1 的路線是哪些站？每站跑哪一版 recipe？", "Which PR version did
    WO910001/1 run at step 7?".
    READ caveats BEFORE ANSWERING: steps with ran_wo_multi / ambiguous are listed in
    not_measurable "route_version_unresolved" - say the version is NOT determined, never pick one.
    published_at is disclosed only and is NEVER a reason to choose a version (the newest published
    version is often not run anywhere). Step order is the route definition; actual order may differ
    and Optional steps may be skipped. reason "wo_not_found" = MES has no such order id;
    "no_route" = the order exists but has no active route. For serial status use
    wo_serials_status; for materials use wo_materials. In demo mode an unknown order says
    unavailable_reason="not_in_demo_fixture" - NOT "no such work order"."""
    _p = {"wo_id": wo_id}
    return _pack2_entry("wo_route", _p, wo_id,
                        lambda m: m.normalize_args("wo_route", wo_id))


# ---- #175 知識卡（2026-09-26；`SPEC-HERMES-TRUST.md` §7.6.11）-----------------------------
#: ★ 讀端＝`board_kb.search`（唯一出處；本支只做參數驗證＋出處信封）。
#: ⚠ caliber＝`doc-reference`（文件值、不是量測；`board_metrics.CALIBERS` 2026-09-26 加）。
#: ⚠ 不上 `/api/board`（同包①／包②）；**不回檔案路徑、不帶 SQL**（QG-6；`_mes_sql_kb` 刻意不接）。
#: ⚠ 卡片目錄不在 ⇒ `found=false`＋`unavailable_reason="unavailable"`（白話＝這次拿不到資料）；
#:   查無 ⇒ `no_matching_card`（白話＝知識卡沒寫到這個主題）——**永不 raise**。
_CALIBER_DOC = "doc-reference"


@mcp.tool()
@_synth_na(_CALIBER_DOC)
def knowledge_card(query: str, section: str | None = None) -> dict:
    """Look up the Tendline Board KNOWLEDGE CARDS: English summaries of the official process
    specifications (Plover PR / Kestrel PS, MP: what a process step does, its MES stations,
    parameters and spec limits as documented, pass/fail meaning, defects and rework rules) and
    of the equipment manuals (EQ: what a machine does, modules, operating cycle, ALARM / error-code
    tables, preventive maintenance, calibration, troubleshooting). Use it for encyclopedia-type
    questions: what a product / process / machine term means (DAF, Frame, die vs module, substrate),
    what a station or machine is for, what an alarm or error code on a machine type means, what
    the documented PM or parameters are.
    `query` = a short ENGLISH term or topic (cards are English; common Chinese terms are also
    mapped), a card id ("QE910002", "QR910010", "QM910001"), an MES station class
    ("segWireBonding") or an alarm code / message ("E102", "Low Vacuum Error").
    `section` (optional) narrows the card text: "alarms", "pm", "calibration", "parameters",
    "troubleshooting", "glossary", "modules", "overview".
    Example questions: "DAF 是什麼？", "die 跟 module 差在哪？", "What is the wirebonder's
    PM?", "Frame bonder 的 Low Vacuum Error 是什麼意思？".
    Returns the best card (content capped at ~6000 characters; truncated=true means ask again
    with `section`), other matching cards, glossary rows and alarm-table rows, each with its
    card id and source documents.
    READ caveats BEFORE ANSWERING: card values are DOCUMENT values at the listed revision, NOT
    measurements - MES wins when they differ (live limits: station_spec_limits; a machine's
    actual error events: equipment_status_events; downtime: equipment_downtime). Cite the card id
    and source document. unavailable_reason="no_matching_card" means no card covers the topic -
    say so plainly and never invent document values. An EQ number is a machine TYPE's document
    set, not one machine (EID). This tool never returns numbers about this week's production."""
    _p = {"query": query, "section": section}
    key, err = kb.normalize_query(query, section)
    if err is not None:
        return _attach("knowledge_card", err, params=_p, caliber=_CALIBER_DOC,
                       freshness="static", row_count=0, degraded=True)
    out = kb.search(*key)
    shape_ok = kb.ok_payload(out)
    n = ((1 if out.get("card") else 0) + len(out.get("glossary") or [])
         + len(out.get("alarm_rows") or []))
    # ★ freshness＝`static`：卡片是版本化的文件常數（同登記表工具），不是 live／fixture 量測。
    return _attach("knowledge_card", out, params=_p, caliber=_CALIBER_DOC, freshness="static",
                   page=None, row_count=n,
                   degraded=(not out.get("found")) or not shape_ok
                   or bool((out.get("card") or {}).get("truncated")))


# ---- /mcp 掛載件（app.py 消費）------------------------------------------------------

_TURN_HEADER = b"x-board-turn"
_TURN_RE = re.compile(r"^[0-9]{1,18}$")


def turn_from_headers(headers) -> int | None:
    """原始 ASGI header 清單 → turn id（`X-Board-Turn`）；**任何不合格的輸入一律 `None`**。

    - key 比對**不分大小寫**（HTTP header 名本就不分；ASGI 規範是小寫，但不賴它）。
    - 值以 latin-1 解碼（ASGI header 的位元組語意）、去前後空白，**只接受 `^[0-9]{1,18}$`**：
      非數字／空字串／負號／超過 18 碼（sqlite INTEGER 是 64-bit，19 碼起可能溢位）一律 `None`。
    - **失敗就是「沒有 turn」，不是錯誤**：這條 header 是加性觀測面，壞值只該讓那一次呼叫
      不落列，不該讓 `/mcp` 回 4xx（亂填一個 header 就打得掉工具面＝新的攻擊面）。
    """
    for pair in headers or []:
        try:
            k, v = pair
            key = k.lower() if isinstance(k, bytes) else str(k).lower().encode("latin-1")
            if key != _TURN_HEADER:
                continue
            raw = v.decode("latin-1") if isinstance(v, bytes) else str(v)
        except Exception:                   # noqa: BLE001 —— 壞形狀＝沒有 turn
            return None
        s = raw.strip()
        return int(s) if _TURN_RE.match(s) else None
    return None


class McpTransportProxy:
    """`/mcp` 的 ASGI 轉接層：①靜態 bearer 閘 ②lifespan 每次啟動換新 session manager。

    - bearer：env `BOARD_MCP_TOKEN` 未設 → 503 fail-closed（內部服務不裸奔）；
      Authorization != `Bearer <token>` → 401（常數時間比較）。LAN 同事打 /mcp 沒 token 全擋。
    - SDK 的 StreamableHTTPSessionManager 一個 instance 只能 run() 一次；proxy + per-lifespan
      重建解耦（cmms api/app.py 實證 pattern；多次 TestClient 進出不炸）。
    """

    def __init__(self) -> None:
        self.app = None

    async def __call__(self, scope, receive, send) -> None:  # ASGI 介面
        from starlette.responses import JSONResponse
        expected = os.environ.get("BOARD_MCP_TOKEN") or None
        if expected is None:  # fail-closed：token 未設 → 一律拒
            await JSONResponse({"detail": "mcp token not configured"}, status_code=503)(scope, receive, send)
            return
        auth = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1")
                break
        if not (auth.startswith("Bearer ")
                and _secrets.compare_digest(auth[7:], expected)):
            await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        if self.app is None:  # lifespan 未運行（正常 uvicorn 啟動不該發生）
            await JSONResponse({"detail": "mcp transport not running"}, status_code=503)(scope, receive, send)
            return
        # ☐2 (a)：turn 綁定**只在認證閘全部放行之後**才設（未授權的請求不該有機會寫任何一列）。
        # 狀態碼與順序一位元不動；`reset` 放 finally＝這條 task 的 context 不留殘值。
        tok = Board_TURN.set(turn_from_headers(scope.get("headers") or []))
        try:
            await self.app(scope, receive, send)
        finally:
            Board_TURN.reset(tok)


def make_transport_apps():
    """回 (proxy, activate, deactivate)：app.py lifespan 用。

    activate() 於 lifespan 進入時呼叫 → 重建 session manager 並回 async context manager
    （呼叫端 `async with` 運行它）；deactivate() 於離開時清 proxy。
    """
    from mcp.server.fastmcp.server import StreamableHTTPASGIApp

    proxy = McpTransportProxy()

    def activate():
        mcp._session_manager = None          # 丟棄上輪 manager（run() once-per-instance）
        mcp.streamable_http_app()            # 官方惰性初始化路徑（帶 FastMCP settings）
        proxy.app = StreamableHTTPASGIApp(mcp.session_manager)
        return mcp.session_manager.run()

    def deactivate():
        proxy.app = None

    return proxy, activate, deactivate


# ---- #167 P4：`/downtime` 停機月報的助理覆蓋（2026-09-23；Chris：「助理也要能回答 /downtime」）--------
#: ★ **頁面與工具同一支讀端**＝`board_downtime.downtime_model`（C-HT：一份口徑、兩個出口）。
#: ⚠ 不上 `/api/board`（`app._Board_WEBMCP_TOOLS` 一個位元不動，同材料工具）；demo 讀 demo fixture、
#:   零 cmms、零 LLM；**不回 LLM 摘要、不回任何人員欄**（C-DT12；列欄位＝`TOP_FIELDS` 白名單）。
_CALIBER_WO_LEDGER = "wo-ledger"
_DT_AREA_ALIASES = {"za": "za", "zb": "zb", "zc": "zc", "zone a": "za", "zone b": "zb", "zone c": "zc"}
_DT_CAVEATS = (
    "TWO RULERS: each month carries axis='runwindow' (downtime clipped to the expected production "
    "time, same ruler as /equipment cells) or axis='24h' (raw 24h clock, used when ANY day of that "
    "month lacks run-window coverage). The two are NOT comparable — never compare, add or trend a "
    "24h month against a runwindow month; the page draws a dashed separator and prints no monthly "
    "change across rulers (kpi.delta is null with delta_reason='cross_axis').",
    "Bar hours are per MACHINE with overlapping work orders counted once (interval union); top-N "
    "work-order hours are per WORK ORDER, so two overlapping orders each include the overlap — "
    "their shares can add up to more than 100%.",
    "Population: only the Zone A / Zone B / Zone C machines on the current cluster roster. Zone D, machines "
    "without a cluster, and assets outside the equipment catalog are NOT in the bars (see footer "
    "counts). The roster has no history: today's roster is applied to past months.",
    "eMaint-era PM work orders (no status history) are EXCLUDED on purpose (counting their whole "
    "open->close span would badly over-state); footer.emaint_pm counts them. Historical months are "
    "mostly migrated work orders estimated open->close (flag 'migrated').",
    "avg_per_machine_h divides by roster_n (roster machines of the three areas), not by the number "
    "of machines that stopped.",
    "The ledger is refreshed daily at 08:40; the current month is partial (through = last closed "
    "production day). Cancelled / voided work orders are not counted.",
)


@mcp.tool()
def downtime_monthly(months: list[str] | None = None, area: str | None = None,
                     top: int = 5) -> dict:
    """The /equipment/downtime/monthly page (SALTMARSH equipment department's monthly downtime
    report): per month and per area (Zone A / Zone B / Zone C) the machine downtime hours from cmms work orders, the page KPIs (total
    hours, work orders, machines down, average per roster machine, top-5 share, monthly change),
    the top-N work orders of each area-month (work order number, machine EID, type, hours, share,
    repair / waiting-parts / waiting-vendor split, start/end, fault code, flags), and the footer
    exception counts. `months` = list of "YYYY-MM" (default = the latest 6 complete months, same
    as the page); `area` = "Zone A" | "Zone B" | "Zone C" (or the keys "za" | "zb" | "zc"; default all three); `top` = 1..5.
    Example questions: "上個月哪一區停機最多？", "Zone B 八月停機前五張單是哪些？",
    "Which machine keeps showing up in Zone C's top 5?", "停機月報的每台平均怎麼算？".
    READ caveats BEFORE ANSWERING: every month has an `axis` — 'runwindow' and '24h' months are
    two different rulers and must never be compared or trended against each other. Bar hours
    count overlapping work orders once per machine; per-work-order hours do not. No AI summaries
    and no people are returned — attribute to machines only. Formulas: explain_metric on the
    downtime.* ids (list_metrics(page="/equipment/downtime/monthly"))."""
    import board_downtime as _wdt
    _p = {"months": list(months) if months else None, "area": area, "top": top}
    src = _source()
    conn, info = _wdt.open_source(src)
    if conn is None:
        return _attach("downtime_monthly",
                       {"found": False, "unavailable_reason": info.get("reason") or "unavailable",
                        "hint": ("the downtime page is not available on the snapshot source"
                                 if info.get("reason") == "snapshot" else
                                 "no work-order ledger yet (the daily ledger job has not run)")},
                       params=_p, caliber=_CALIBER_WO_LEDGER, page=M.P_DOWNTIME, row_count=0,
                       degraded=True)
    today = info["today"]
    opts = _wdt.month_options(conn, today)
    want = [str(m).strip() for m in (months or []) if str(m).strip()]
    rejected = [m for m in want if m not in opts]
    sel = _wdt.parse_months(",".join(m for m in want if m in opts), opts, today)
    a_key = None
    if area:
        a_key = _DT_AREA_ALIASES.get(str(area).strip().lower())
        if a_key is None:
            return _attach("downtime_monthly",
                           {"found": False, "error": "bad_area", "given": area,
                            "hint": "area must be one of Zone A, Zone B, Zone C (Zone D is not on this page)"},
                           params=_p, caliber=_CALIBER_WO_LEDGER, page=M.P_DOWNTIME, row_count=0,
                           degraded=True)
    try:
        n_top = max(1, min(_wdt.TOP_N, int(top)))
    except (TypeError, ValueError):
        n_top = _wdt.TOP_N
    model = _wdt.downtime_model(conn, sel, today)
    areas = [a_key] if a_key else list(_wdt.BAR_AREAS)
    lab = _wdt.AREA_LABEL

    def _r1(v):
        return None if v is None else round(float(v), 1)
    months_out = [{"month": x["month"], "axis": x["axis"], "partial": x["partial"],
                   "through": x["through"],
                   "areas_h": {lab[a]: _r1(x["areas"][a]) for a in areas},
                   "total_h": _r1(sum(x["areas"][a] for a in areas))} for x in model["months"]]
    top_out = []
    for x in model["months"]:
        for a in areas:
            rows = []
            for r in model["top"].get((x["month"], a), ())[:n_top]:
                rr = {k: r[k] for k in _wdt.TOP_FIELDS}
                rr["h"], rr["pct"] = _r1(rr["h"]), _r1(rr["pct"])
                if rr["stage"]:
                    rr["stage"] = {k: _r1(v) for k, v in rr["stage"].items()}
                rows.append(rr)
            top_out.append({"month": x["month"], "area": lab[a], "axis": x["axis"],
                            "segment_h": _r1(x["areas"][a]), "rows": rows})
    k = model["kpi"]
    kpi = {"total_h": _r1(k["total_h"]), "n_wo": k["n_wo"], "n_eids": k["n_eids"],
           "roster_n": k["roster_n"], "avg_per_machine_h": _r1(k["avg_per_machine_h"]),
           "top5_share_pct": _r1(k["top5_share"]), "mixed_axes": k["mixed_axes"],
           "delta": (None if not k["delta"] else
                     {"dh": _r1(k["delta"]["dh"]), "pct": _r1(k["delta"]["pct"]),
                      "last_month": k["delta"]["last_month"],
                      "base_months": k["delta"]["base_months"]}),
           "delta_reason": k["delta_reason"]}
    if a_key:
        kpi["note"] = "kpi is for all three areas (the page's KPI row); months[].areas_h is filtered"
    ft = model["footer"] or {}
    footer = {"out_of_roster": ft.get("out_roster"), "offcat": ft.get("offcat"),
              "long_open": ft.get("long"), "emaint_pm": ft.get("emaint_pm"),
              "oneclick_pm": ft.get("oneclick_pm"), "migrated_months": ft.get("migrated_months"),
              "runwindow_since": ft.get("axis_since"), "long_stop_since": ft.get("a11_since")}
    for kk in ("out_of_roster", "offcat", "long_open"):
        if footer[kk]:
            footer[kk] = {"n": footer[kk]["n"], "h": _r1(footer[kk]["h"])}
    repeat = {lab[a]: v for a, v in (model.get("repeat") or {}).items() if a in areas}
    out = {"found": True, "months": months_out, "kpi": kpi, "top": top_out, "footer": footer,
           "repeat_offenders": repeat, "rejected_months": rejected,
           "available_months": opts, "caveats": list(_DT_CAVEATS)}
    first = sel[0] + "-01 08:00:00" if sel else None
    last = months_out[-1]["through"] if months_out else None
    win = ({"from": first,
            "to": (_wdt.wl.day_window(last, None)[2].strftime(_TS_FMT) if last else None)}
           if first else _NO_WINDOW)
    return _attach("downtime_monthly", out, params=_p, caliber=_CALIBER_WO_LEDGER,
                   page=M.P_DOWNTIME, row_count=len(months_out), window=win,
                   degraded=bool(rejected))


# ---- selftest（demo 直呼工具函式；零 HTTP、零 MES）-----------------------------------

def _selftest() -> None:
    os.environ["BOARD_SOURCE"] = "demo"
    n = 0

    def t(name, cond):
        nonlocal n
        assert cond, f"selftest FAIL: {name}"
        n += 1

    # 私有 fixture／知識卡不存在（例：脫敏公開樹）⇒ 依賴它們的段落標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    import mes_material as _fxm_a, mes_etest as _fxm_b, mes_equipment as _fxm_c, mes_segment as _fxm_d, mes_route as _fxm_e
    _MES_FX_FILES = tuple(f for _m in (_fxm_a, _fxm_b, _fxm_c, _fxm_d, _fxm_e) for f in _m.FIXTURE_FILES.values())

    def _fx_ok(label, *names):
        _d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures")
        miss = [nm for nm in names if not os.path.exists(os.path.join(_d, nm))]
        if miss:
            print("board_mcp selftest: skipped: %s (%s absent)" % (label, ", ".join(sorted(miss))))
        return not miss

    def _kb_ok():
        ok_ = kb.load_index() is not None
        if not ok_:
            print("board_mcp selftest: skipped: knowledge_card (knowledge cards absent)")
        return ok_

    # ping / source
    p = ping()
    t("ping-ok", p["status"] == "ok" and p["source"] == "demo")
    os.environ["BOARD_SOURCE"] = "bogus"
    t("source-normalized", _source() == "demo")
    os.environ["BOARD_SOURCE"] = "demo"

    # clamp
    t("clamp-high", _clamp_days(90, 3) == 30)
    t("clamp-low", _clamp_days(0, 3) == 1)
    t("clamp-bad", _clamp_days("x", 7) == 7)
    t("clamp-none", _clamp_days(None, 3) == 3)

    # list_stations
    ls = list_stations()
    t("stations-38", len(ls["stations"]) == len(mt.EQP_STATIONS))
    t("stations-anchor", ls["anchor_cls"] == mt.CAL_CLASS)
    t("stations-pretty", all(s["cls"] and s["name"] for s in ls["stations"]))

    # fleet_scoreboard（demo 即時）
    fs = fleet_scoreboard(days=90)                      # clamp → 30
    t("fs-clamped", fs["days"] == 30)
    t("fs-fleet", isinstance(fs["fleet"]["util_pct"], (int, float)))
    t("fs-stations", len(fs["stations"]) == len(mt.EQP_STATIONS))
    t("fs-trim-keys", set(fs["stations"][0]) == {"cls", "util_pct", "producing_h",
                                                 "active_window_h", "fleet_size", "n_seg",
                                                 "util_unmeasurable"})
    t("fs-demo-not-unmeasurable", all(not s["util_unmeasurable"] for s in fs["stations"]))
    t("fs-demo-no-note", "util_note" not in fs)        # demo 無零工期站 → 不掛頂層散文（加性）

    # fleet_scoreboard 頂層 util_note：任一站零工期 → 加頂層散文（防 Hermes 對老闆說 util 0%）
    import app as _app
    _orig_ls = _app.load_scoreboard
    _app.load_scoreboard = lambda *a, **k: {"mes_now": None, "fleet": {"util": 0.5}, "stations": [
        {"cls": "segZeroLen", "util_strict_pct": 0.0, "producing_sec": 0.0,
         "active_span_sec": 0.0, "fleet_size": 2, "n_seg": 40, "zero_len_only": True}]}
    try:
        fz = fleet_scoreboard(days=3)
        t("fs-note-when-zerolen", fz.get("util_note") == _UTIL_UNMEASURABLE_NOTE)
    finally:
        _app.load_scoreboard = _orig_ls

    # 零工期站（M4 後遺）：_trim_util_rec 對 zero_len rec → util_pct null（非 0）、util_unmeasurable True
    zrec = {"cls": "segZeroLen", "util_strict_pct": 0.0, "producing_sec": 0.0,
            "active_span_sec": 0.0, "fleet_size": 2, "n_seg": 40, "zero_len_only": True}
    zt = _trim_util_rec(zrec)
    t("trim-zerolen-null", zt["util_pct"] is None and zt["util_unmeasurable"] is True)
    nrec = {"cls": "segX", "util_strict_pct": 55.0, "producing_sec": 100.0,
            "active_span_sec": 200.0, "fleet_size": 2, "n_seg": 40}
    nt = _trim_util_rec(nrec)
    t("trim-normal-pct", nt["util_pct"] == 55.0 and nt["util_unmeasurable"] is False)

    # station_overview：未知站不猜
    bad = station_overview("segTotallyFake", 7)
    t("ov-unknown", bad["error"] == "unknown_station" and len(bad["known_stations"]) > 30)
    ov = station_overview(mt.CAL_CLASS, 7)
    t("ov-cal", ov["cls"] == mt.CAL_CLASS and ov["source"] == "demo")
    # D1 雙軌制：**同一個 payload 兩軸並存** ⇒ 頂層 caliber="dual"、軸標籤下沉到各區塊
    #  （頂層若標單一 caliber，agent 會把稼動數字也當公司口徑轉述＝結構縫，非文案問題）
    t("ov-caliber-dual", ov["caliber"] == _CALIBER_DUAL and _CALIBER_QLIK in ov["caliber_note"]
      and _CALIBER_UTIL_AXIS in ov["caliber_note"])
    t("ov-no-top-caveat", "caveat" not in ov)
    t("ov-sides", ("util" in ov) and ("yield" in ov))
    t("ov-yield-honest", ov.get("yield") is not None or bool(ov.get("yield_unavailable_reason")))
    if ov.get("yield"):
        t("ov-yield-axis", ov["yield"]["caliber"] == _CALIBER_QLIK
          and "first FAIL" in ov["yield"]["caveat"] and "RAW v0" not in ov["yield"]["caveat"])
    if ov.get("util"):
        t("ov-util-axis", ov["util"]["caliber"] == _CALIBER_UTIL_AXIS)
    # ---- 2026-10-02 自評 ④：連結頁那一格（`page_value`＝頁面同一份 payload；demo＝RW 軸不存在）----
    t("ov10-page-keys", {"page_value", "page_caliber", "page_metric_id", "page_value_reason",
                         "page_note"} <= set(ov))
    t("ov10-page-caliber", ov["page_caliber"] == M.ALL_METRICS["station.utilization"].caliber
      == _CALIBER_RW_PLANNED and ov["page_metric_id"] == "station.utilization")
    t("ov10-demo-honest-blank", ov["page_value"] is None and ov["page_value_reason"] == _GM_RW_LIVE_ONLY
      and "never present it as the page's number" in ov["page_note"])
    t("ov10-envelope-still-dual", ov["provenance"]["caliber"] == _CALIBER_DUAL
      and "app.station_axis_payload" in ov["provenance"]["queries"])
    _sv_ax = _app.station_axis_payload
    try:
        # live 形：頁面頭卡的稼動在 RW 軸（工具與站頁兩個數不同的那一型；值為合成）
        _app.station_axis_payload = lambda *a, **k: {"data": {"mes_now": None},
                                                     "st": {"rw_axis": True, "utilization": 0.47}}
        _ovp = station_overview(mt.CAL_CLASS, 7)
        _u10 = (_ovp.get("util") or {}).get("util_pct")
        t("ov10-page-value-from-page-payload", _ovp["page_value"] == 47.0
          and _ovp["page_value_reason"] is None)
        t("ov10-page-note-both-numbers", "= 47.0%" in _ovp["page_note"]
          and f"= {_u10}%" in _ovp["page_note"] and "different denominator" in _ovp["page_note"])
        t("ov10-page-note-denominator", "rw-planned" in _ovp["page_note"]
          and "ALWAYS state its denominator" in _ovp["page_note"])
        t("ov10-util-block-untouched", _ovp["util"] == ov["util"] and _ovp["yield"] == ov["yield"])
        _app.station_axis_payload = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("HYT00 mes-sql.example.com"))
        _ovx = station_overview(mt.CAL_CLASS, 7)
        t("ov10-page-error-classified", _ovx["page_value"] is None
          and _ovx["page_value_reason"] == "timeout" and _ovx["util"] == ov["util"]
          and "mes-sql" not in _json.dumps(_ovx, default=str))
        # 冷：頁面 payload 等不到 ⇒ 這一格 null＋timeout、util／yield 照常（等待上限＝min(冷載, 30s)）
        import threading as _thr_ov
        _ov_gate, _ov_env = _thr_ov.Event(), os.environ.get("BOARD_MCP_COLD_WAIT_S")
        os.environ["BOARD_MCP_COLD_WAIT_S"] = "1"
        _app.station_axis_payload = lambda *a, **k: _ov_gate.wait(10) and {
            "data": {"mes_now": None}, "st": {"rw_axis": True, "utilization": 0.47}}
        try:
            _t_ov = time.monotonic()
            _ovc = station_overview(mt.CAL_CLASS, 7)
            t("ov10-page-cold-bounded", _ovc["page_value"] is None
              and _ovc["page_value_reason"] == _COLD_REASON and _ovc["util"] == ov["util"]
              and time.monotonic() - _t_ov < 5.0 and _PAGE_VALUE_WAIT_S < _COLD_WAIT_DEFAULT_S)
        finally:
            _ov_gate.set()
            if _ov_env is None:
                os.environ.pop("BOARD_MCP_COLD_WAIT_S", None)
            else:
                os.environ["BOARD_MCP_COLD_WAIT_S"] = _ov_env
            _end_ov = time.monotonic() + 10.0
            while _COLD_INFLIGHT and time.monotonic() < _end_ov:
                time.sleep(0.02)
    finally:
        _app.station_axis_payload = _sv_ax

    # station_units（機台級＝raw；**必須掛另一個 caveat**，兩者不可共用一個常數）
    su = station_units(mt.CAL_CLASS, 7)
    t("su-machines", isinstance(su["machines"], list) and len(su["machines"]) >= 1)
    t("su-nosum", "never sum" in su["nosum_note"])
    t("su-unknown", station_units("segNope", 7)["error"] == "unknown_station")
    t("su-caliber", su["caliber"] == _CALIBER_RAW_MACHINE)
    t("su-caveat-split", su["caveat"].startswith("RAW MES caliber")
      and su["caveat"] != (ov.get("yield") or {}).get("caveat"))

    # yield_summary（線級＝Qlik grid：43 站 + 7 KPI + 吸收帳；不可用時誠實 null 不猜）
    ysum = yield_summary(3)
    t("ys-caliber", ysum["caliber"] == _CALIBER_QLIK)
    t("ys-no-line-total", "line_total" not in ysum)
    if ysum.get("stations") is None:
        t("ys-honest-unavailable",
          bool(ysum.get("unavailable_reason")) and ysum.get("kpis") is None
          and ysum.get("absorbed_total") is None)
    else:
        t("ys-43-stations", len(ysum["stations"]) == 43)
        t("ys-kpis", set(ysum["kpis"]) == {"OVERALL", "PH", "PC", "PPC",
                                           "BEFORE_DA", "FROM_DA", "VOQC"})
        t("ys-kpi-null-not-100",
          all((v.get("pct") is None) or isinstance(v.get("pct"), (int, float))
              for v in ysum["kpis"].values()))
        t("ys-absorbed", ysum.get("absorbed_total") is not None)
        t("ys-row-keys", set(ysum["stations"][0]) == {
            "ord", "cls", "name", "units", "pass", "fail", "final_yield_pct",
            "first_pass_yield_pct", "failed_elsewhere_first"})
    # 2026-10-02 自評 ⑤：「損失」只算到良率 ⇒ 工具自己講範圍（criteria 未升版；提案在回報）
    t("ys10-loss-scope", ysum.get("loss_scope") == _YIELD_LOSS_SCOPE
      and "does NOT include downtime loss or rework loss" in _YIELD_LOSS_SCOPE
      and "/monthly" in _YIELD_LOSS_SCOPE and "not a calendar month" in _YIELD_LOSS_SCOPE)
    t("mw-docstring-and-caveat", "monthly_window" in (yield_summary.__doc__ or "")
      and "monthly_window" in _MONTHLY_LOSS_CAVEAT)
    t("ys10-docstring-loss", "LOSS SCOPE" in (getattr(yield_summary, "__doc__", "") or ""))
    # ---- 第二批 ③：`monthly_loss`＝`/monthly` 頁面同一份月模型（三損失合計逐站，PH）------------
    _ml = ysum.get("monthly_loss") or {}
    t("ml3-block-keys", set(_ml) == {"month", "partial", "as_of", "frozen_at", "unit", "caveat",
                                     "stations", "n_stations", "truncated", "line",
                                     "unavailable_reason", "available_months"})
    t("ml3-caveat-default-total", "three-loss total" in _ml.get("caveat", "")
      and "only when the user names ONE kind" in _ml.get("caveat", "")
      and "monthly_loss" in _YIELD_LOSS_SCOPE)
    _ml_docs, _ = _app._mr_docs("demo")
    _ml_today = _app._mr_today("demo")
    _ml_opts, _ml_cur = _app._mr_months(_ml_docs, _ml_today)
    _ml_full = [m_ for m_ in _ml_opts if m_ != _ml_cur]
    if not _ml_full:
        print("board_mcp selftest: skipped: monthly_loss rows (no complete month in the demo ledger)")
    else:
        t("ml3-default-last-complete-month", _ml["month"] == _ml_full[-1] and _ml["partial"] is False)
        _ml_model, _ = _app._mr_model(_ml_docs, _ml["month"], _ml_today, "demo")
        _ml_rows = _ml_model["stations"]
        # ★★ 承重：與頁面同一份模型、同一個排序、同一個取整（`_mr_ph`）
        t("ml3-same-order-as-page", [r_["cls"] for r_ in _ml["stations"]]
          == [r_["cls"] for r_ in _ml_rows][:_MONTHLY_LOSS_TOP])
        t("ml3-same-numbers-as-page", all(
            (s_["loss_total_ph"], s_["down_equiv_ph"], s_["yield_loss_ph"], s_["rework_ph"])
            == tuple(None if r_[k_] is None else int(round(float(r_[k_])))
                     for k_ in ("loss_total_ph", "down_equiv_ph", "qlik_fail", "rework_n"))
            for s_, r_ in zip(_ml["stations"], _ml_rows)))
        t("ml3-page-format-agrees", all(
            (_app._mr_ph(r_["loss_total_ph"]) == "—") == (s_["loss_total_ph"] is None)
            for s_, r_ in zip(_ml["stations"], _ml_rows)))
        t("ml3-total-is-sum-of-split", all(
            s_["loss_total_ph"] is None
            or abs(s_["loss_total_ph"] - (s_["down_equiv_ph"] + s_["yield_loss_ph"]
                                          + s_["rework_ph"])) <= 2
            for s_ in _ml["stations"]))
        t("ml3-no-rate-total-null", all(s_["loss_total_ph"] is None
                                        for s_ in _ml["stations"] if s_["rank_basis"] != "total"))
        t("ml3-rank-basis-closed", all(s_["rank_basis"] in _MONTHLY_RANK_BASIS.values()
                                       for s_ in _ml["stations"]))
        t("ml3-line-same-as-landing-q3",
          _ml["line"]["loss_total_ph"] == (None if _ml_model["line"]["loss_total_ph"] is None
                                           else int(round(_ml_model["line"]["loss_total_ph"]))))
        t("ml3-cites-registry", ysum["provenance"]["metric_ids"]
          == ["station.quality", _MONTHLY_LOSS_METRIC] and _MONTHLY_LOSS_METRIC in M.ALL_METRICS
          and "app._mr_model" in ysum["provenance"]["queries"])
        _ml_exp = explain_metric(_MONTHLY_LOSS_METRIC, "en")
        t("ml3-explainable", "error" not in _ml_exp and _ml_exp.get("unit") == "PH"
          and _ml_exp.get("caliber") == "daily-ledger")
    _mw = ysum.get("monthly_window")
    if _ml.get("stations"):
        t("mw-shape", set(_mw) == {"month", "from", "to", "partial"} and _mw["month"] == _ml["month"]
          and _mw["from"] == _ml["month"] + "-01 08:00:00" and _mw["to"] > _mw["from"])
        t("mw-provenance-untouched", tuple(ysum["provenance"]) == PROVENANCE_KEYS
          and ysum["provenance"]["window"] != {"from": _mw["from"], "to": _mw["to"]})
    t("mw-dec-rollover", _monthly_window({"month": "2026-12", "stations": [1], "partial": False})["to"]
      == "2027-01-01 08:00:00")
    t("mw-none-without-rows", _monthly_window({"month": "2026-09", "stations": None}) is None
      and _monthly_window(None) is None)
    _ml_bad = yield_summary(3, month="2026-13")
    t("ml3-bad-month", _ml_bad["monthly_loss"]["unavailable_reason"].startswith("bad_month")
      and _ml_bad["monthly_loss"]["stations"] is None
      and _ml_bad["provenance"]["metric_ids"] == ["station.quality"]
      and _ml_bad["stations"] == ysum["stations"] and _ml_bad["monthly_window"] is None)
    t("ml3-absent-month", yield_summary(3, month="2001-01")["monthly_loss"]["unavailable_reason"]
      == "month not available (see available_months)")
    # 不給 month ⇒ 信封參數仍只有 days（既有呼叫的 evidence_ref 逐位元不變）
    t("ml3-params-unchanged", ysum["provenance"]["evidence_ref"].split(":")[-1]
      == _envelope("yield_summary", params={"days": 3}, metric_ids=(), queries=(),
                   window={"from": None, "to": None}, fetched_at=ysum["provenance"]["fetched_at"],
                   freshness="fixture", row_count=0, caliber="x", confidence="low",
                   page=None)["evidence_ref"].split(":")[-1])
    _sv_mrd = _app._mr_docs
    try:
        _app._mr_docs = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HYT00 mes-sql.example.com"))
        _ml_x = _monthly_loss_block("demo")
        t("ml3-never-raises", _ml_x["stations"] is None and _ml_x["unavailable_reason"] == "timeout"
          and "mes-sql" not in _json.dumps(_ml_x, default=str))
    finally:
        _app._mr_docs = _sv_mrd

    # ---- updown_now（包①：改吃 `/equipment` 那張板；2026-09-22、§12.13）-------------
    # ⚠ 這一段整組換過：材料不再是 `app.load_updown`（動態 MES 名冊）而是
    #   `app._peek_eqm`（固定名冊的設備會議板）。釘子跟著換的是**名冊與桶**，
    #   「DOWN 怎麼判」一個位元都沒動（仍是板的 `_now_block`）。
    import app as _app_hl
    un = updown_now()
    _hl = _app_hl.load_eqm("demo", _app_hl.EQM_DAYS, 15)["headline"]
    t("ud-counts", un["n_up"] + un["n_down"] + un["n_unknown"] + un["n_dormant"]
      == un["n_total"])                       # 四桶守恆（板只有三桶 ⇒ n_unknown 恆 0）
    t("ud-unknown-zero", un["n_unknown"] == 0)
    t("ud-cap", len(un["rows"]) <= _UD_ROW_CAP)
    t("ud-truncated-flag", un["truncated"] == (un["n_total"] - un["n_dormant"] > _UD_ROW_CAP))
    t("ud-row-keys", set(un["rows"][0]) == {"eid", "cls", "desc", "verdict", "bucket",
                                            "since_h", "now_state", "wo_no"})
    # ★★ 包① 的承重釘：工具與**頁面**是同一張板、同一條規則 ⇒ 三個頭數字逐位元相同。
    #    這一根紅掉＝「6 台 vs 13 台」那種兩把尺的誤判又回來了。
    t("ud-same-as-page-down", un["n_down"] == _hl["n_down"])
    t("ud-same-as-page-total", un["n_total"] == _hl["n_total"] == len(wem.EQMT_CATALOG))
    t("ud-same-as-page-dormant", un["n_dormant"] == _hl["n_dormant"] == len(un["dormant"]))
    t("ud-roster-size", un["roster_size"] == un["n_total"])
    # ◌ 長停**自己一桶、絕不進 DOWN、也不出現在 rows**（同一台不得在一份 payload 裡兩次）
    _dorm_eids = {d["eid"] for d in un["dormant"]}
    t("ud-dormant-shape", all(set(d) == {"eid", "desc", "since", "reason"} for d in un["dormant"]))
    t("ud-dormant-not-in-rows", _dorm_eids and not (_dorm_eids & {r["eid"] for r in un["rows"]}))
    t("ud-dormant-not-down", all(r["bucket"] != "dormant" for r in un["rows"]))
    # 板的時戳（頁面窄橫列的 as-of 同一件事）；demo ＝即時合成 ⇒ age 恆 0 分
    t("ud-built-at", isinstance(un["board_built_at"], str) and len(un["board_built_at"]) == 5)
    t("ud-age-min", un["age_min"] == 0)
    # demo 板的工單源＝全合成但**接通**（`wo_status="demo"`，同 `/equipment` demo 頁會畫停機）
    t("ud-wo-wired-flag", un.get("wo_wired") is True)
    t("ud-live-note-roster", "ROSTER = the /equipment board" in un["live_note"]
      and "NEVER counted in n_down" in un["live_note"]
      and "MES silence is NOT DOWN" in un["live_note"])
    # ★ 包③：每一個小時欄都是一位小數（2026-09-22 turn 1 的 `1849.8461111111112`）
    t("ud-since-1dp", all(r["since_h"] is None or round(r["since_h"], 1) == r["since_h"]
                          for r in un["rows"]))
    t("ud-longest-1dp", un["longest_down"] is None
      or round(un["longest_down"]["since_h"], 1) == un["longest_down"]["since_h"])
    t("ud-caliber", un["provenance"]["caliber"] == _CALIBER_BOARD_NOW
      and _CALIBER_BOARD_NOW != _CALIBER_UTIL_AXIS)
    t("ud-page", un["provenance"]["page"] == "/equipment")
    # ★★ peek-only：板還沒暖 ⇒ 誠實回 `board_not_warmed`，**絕不重建**（live 的板要掃
    #    全窗 WO LIST + MES 探測，聊天一則訊息不該等數十秒）。
    import app as _app_ud
    _saved_peek, _calls = _app_ud._peek_eqm, []
    try:
        _app_ud._peek_eqm = lambda *a, **kw: _calls.append(a) or None
        _cold = updown_now()
        t("ud-cold-reason", _cold["unavailable_reason"] == "board_not_warmed"
          and _cold["board"] is None)
        # ★ 包⑤（2026-09-23）：加性 `hint`＝可自救的下一步（enum 本身一位元未動）
        t("ud-cold-hint", _cold["hint"] == _UD_HINT_NOT_WARMED
          and "reload /equipment once and ask again" in _cold["hint"]
          and _cold["unavailable_reason"] == "board_not_warmed")
        t("ud-cold-conf", _cold["provenance"]["confidence"] == "low"
          and _cold["provenance"]["caliber"] == _CALIBER_BOARD_NOW)
        t("ud-peek-key", _calls == [("demo", _app_ud.EQM_DAYS, 15)])   # ＝/equipment 路由預設
    finally:
        _app_ud._peek_eqm = _saved_peek

    # ---- 包⑤：座級 DOWN（2026-09-23、§12.15）----------------------------------------
    # ⚠ **demo 板此刻零 DOWN 座**（兩張 showcase 座單都錨在窗末前兩天、早已結案）⇒ 正面案例
    #   走「拿 demo 板深拷一份、把那一座的 `now` 翻成 down」——**不動 demo fixture 本身**
    #   （加一張線上開著的座單會讓母機 `EID-26718` 也變 DOWN ⇒ headline 13→14 ⇒ 打翻整包
    #   與本功能無關的既有釘子）。負面案例才是真的 demo 板。
    t("ud-seats-demo-empty", un["n_down_seats"] == 0 and un["down_seats"] == [])
    t("ud-seats-key-additive",                    # 既有欄一位元未動＝C-HT9
      {"n_down_seats", "down_seats"} <= set(un) and un["n_down"] == _hl["n_down"])
    _bd_seat = _app_hl.load_eqm("demo", _app_hl.EQM_DAYS, 15)
    _bd_seat = dict(_bd_seat, areas=[dict(a, rows=list(a["rows"])) for a in _bd_seat["areas"]])
    _seat_src = next((r for a in _bd_seat["areas"] for r in a["rows"]
                      if r.get("is_seat") and r.get("parent_eid")), None)
    t("ud-seats-demo-has-seat-rows", _seat_src is not None)
    for _a in _bd_seat["areas"]:
        _a["rows"] = [
            (dict(r, now=dict(r.get("now") or {}, bucket="down", wo_no=49800))
             if r is _seat_src else r) for r in _a["rows"]]
    _saved_peek2 = _app_ud._peek_eqm
    try:
        _app_ud._peek_eqm = lambda *a, **kw: (_bd_seat, 0.0)
        _uns = updown_now()
        t("ud-seats-listed", _uns["n_down_seats"] == 1 == len(_uns["down_seats"]))
        t("ud-seats-shape", set(_uns["down_seats"][0])
          == {"parent_eid", "unit", "label", "wo_no", "since_h"})
        t("ud-seats-parent-and-label",
          _uns["down_seats"][0]["parent_eid"] == _seat_src["parent_eid"]
          and _uns["down_seats"][0]["label"] == _seat_label(_seat_src["seat_unit"]))
        # ★★ 承重：座**不進 n_down**（機台數與頁面 headline 仍逐位元相同）、**不進 rows**
        t("ud-seats-not-in-ndown", _uns["n_down"] == _hl["n_down"]
          and _uns["n_total"] == _hl["n_total"])
        t("ud-seats-not-in-rows",
          not any(r["eid"] == _seat_src["eid"] for r in _uns["rows"]))
        t("ud-seats-since-1dp", all(s["since_h"] is None or round(s["since_h"], 1) == s["since_h"]
                                    for s in _uns["down_seats"]))
        # 負斷言：座列沒有 `parent_eid` ⇒ 丟掉（不拿 eid 反推母機）
        _bd_orphan = dict(_bd_seat, areas=[
            dict(a, rows=[({k: v for k, v in r.items() if k != "parent_eid"}
                           if r.get("is_seat") else r) for r in a["rows"]])
            for a in _bd_seat["areas"]])
        t("ud-seats-orphan-dropped", _down_seat_rows(_bd_orphan) == [])
    finally:
        _app_ud._peek_eqm = _saved_peek2
    t("ud-seats-live-note", "listed separately in `down_seats`" in un["live_note"]
      and "counts MACHINES only" in un["live_note"]
      and "listed separately in `down_seats`" in _UD_NOTE_UNWIRED)
    t("ud-seat-label", _seat_label("nest:2") == "nest 2"
      and _seat_label("EID-05707") == "EID-05707" and _seat_label(None) == "")

    # ---- 第二批 ①：座級長停（2026-10-02；最優先＝答錯）------------------------------------
    # ⚠ demo 板此刻零長停座 ⇒ 正面案例同包⑤ 的手法：深拷 demo 板、把一座翻成長停（since 合成、
    #   原因空＝頁面原因欄空白那一形）；**demo fixture 本身一位元不動**。
    t("ds2-demo-additive", un["n_dormant_seats"] == len(un["dormant_seats"])
      and isinstance(un["dormant_seats"], list) and un["n_dormant"] == _hl["n_dormant"])
    _bd_ds = _app_hl.load_eqm("demo", _app_hl.EQM_DAYS, 15)
    _bd_ds = dict(_bd_ds, areas=[dict(a, rows=list(a["rows"])) for a in _bd_ds["areas"]])
    _ds_src = next((r for a in _bd_ds["areas"] for r in a["rows"]
                    if r.get("is_seat") and r.get("parent_eid")), None)
    _ds_since = "2001-02-03"
    for _a in _bd_ds["areas"]:
        _a["rows"] = [
            (dict(r, now=dict(r.get("now") or {}, bucket="dormant", verdict="dormant", wo_no=None),
                  dormant={"since": _ds_since, "reason_zh": None, "reason_en": None})
             if r is _ds_src else r) for r in _a["rows"]]
    _ds_par = _ds_src["parent_eid"]
    _ds_keys = {"parent_eid", "unit", "label", "since", "reason", "dormant_registry", "note"}
    _sv_pk_ds = _app_ud._peek_eqm
    try:
        _app_ud._peek_eqm = lambda *a, **kw: (_bd_ds, 0.0)
        _udz = updown_now()
        t("ds2-fleet-listed", _udz["n_dormant_seats"] == 1 == len(_udz["dormant_seats"])
          and set(_udz["dormant_seats"][0]) == _ds_keys
          and _udz["dormant_seats"][0]["parent_eid"] == _ds_par
          and _udz["dormant_seats"][0]["since"] == _ds_since
          and _udz["dormant_seats"][0]["label"] == _seat_label(_ds_src["seat_unit"]))
        t("ds2-reason-not-invented", _udz["dormant_seats"][0]["reason"] is None
          and "reason not filled in" in _udz["dormant_seats"][0]["note"]
          and "never invent" in _udz["dormant_seats"][0]["note"])
        # ★★ 承重：座**不進 n_dormant／n_down／rows**（台數與頁面 headline 逐位元相同）
        t("ds2-not-in-machine-counts", _udz["n_dormant"] == _hl["n_dormant"]
          and _udz["n_down"] == _hl["n_down"] and _udz["n_total"] == _hl["n_total"]
          and not any(r["eid"] == _ds_src["eid"] for r in _udz["rows"])
          and not any(d["eid"] == _ds_src["eid"] for d in _udz["dormant"]))
        t("ds2-registry-closed-set", _udz["dormant_seats"][0]["dormant_registry"] in DORMANT_REGISTRY)
        t("ds2-fleet-cites-dormant", "equip.dormant" in _udz["provenance"]["metric_ids"])
        t("ds2-live-note", "listed separately in `dormant_seats`" in _udz["live_note"]
          and "never say there are none" in _udz["live_note"]
          and "listed separately in `dormant_seats`" in _UD_NOTE_UNWIRED)
        # 單機三支：問母機要帶出它的長停座（同一張板、同一列）
        _udm = updown_now(eid=_ds_par)
        _edm = equipment_downtime(eid=_ds_par, days=_app_hl.EQM_DAYS)
        _mlm = machine_lookup(_ds_par)
        t("ds2-single-three-tools", all(
            [(s["parent_eid"], s["unit"], s["since"], s["reason"]) for s in x["dormant_seats"]]
            == [(_ds_par, _ds_src["seat_unit"], _ds_since, None)] for x in (_udm, _edm, _mlm)))
        t("ds2-single-cites-dormant", all("equip.dormant" in x["provenance"]["metric_ids"]
                                          for x in (_udm, _edm, _mlm)))
        t("ds2-current-wo-key", "current_wo" in _udm)
        # 別台母機 ⇒ 空清單（不是 None：板在、查過了、沒有）
        _other = next(e for _a2, _n2, e in wem.EQMT_CATALOG if e != _ds_par)
        t("ds2-other-machine-empty", updown_now(eid=_other)["dormant_seats"] == []
          and machine_lookup(_other)["dormant_seats"] == [])
        # 負斷言：沒有 parent_eid 的座列 ⇒ 丟掉（不拿 eid 反推母機）
        _bd_ds_orphan = dict(_bd_ds, areas=[
            dict(a, rows=[({k: v for k, v in r.items() if k != "parent_eid"}
                           if r.get("is_seat") else r) for r in a["rows"]])
            for a in _bd_ds["areas"]])
        t("ds2-orphan-dropped", _dormant_seats_view(_bd_ds_orphan) == [])
        # 座 id 形輸入 ⇒ 母機那一份＋`seat` 區塊（nest 形才有 `-N` 拼法）
        if str(_ds_src["seat_unit"]).startswith("nest:"):
            _sid = _ds_par.replace("EID-", "eid-") + "-n" + _ds_src["seat_unit"].split(":")[1]
            _mls_seat = machine_lookup(_sid)
            t("ds2-seat-id-resolves", _mls_seat.get("eid") == _ds_par
              and _mls_seat["seat"]["unit"] == _ds_src["seat_unit"]
              and _mls_seat["seat"]["parent_eid"] == _ds_par
              and _mls_seat["dormant_seats"][0]["unit"] == _ds_src["seat_unit"]
              and _mls_seat["provenance"]["tool"] == "machine_lookup")
        else:
            print("board_mcp selftest: skipped: seat-id input (demo seat row is not a nest seat)")
        # 板窺不到 ⇒ machine_lookup 的 dormant_seats＝None（沒查到，不是「沒有」）
        _app_ud._peek_eqm = lambda *a, **kw: None
        t("ds2-no-board-none", machine_lookup(_ds_par)["dormant_seats"] is None)
    finally:
        _app_ud._peek_eqm = _sv_pk_ds
    # 座 id 解析（純函式；唯一 parser＝`board_capacity.fold_seat_id`）
    _si = _seat_input("eid-11860-n2")
    t("ds2-seat-input-parse", _si is not None and _si["seat_id"] == "EID-11860-N2"
      and _si["parent_eid"] == "EID-11860" and _si["unit"] == "nest:2" and _si["label"] == "nest 2")
    t("ds2-seat-input-reject", _seat_input("11860") is None and _seat_input("abc-N2") is None
      and _seat_input("EID-11860-Nx") is None and _seat_input(None) is None)
    t("ds2-bad-eid-unchanged", set(machine_lookup("nonsense")) - {"provenance"}
      == {"error", "given", "hint"})

    # _norm_eid 邊界（純函式；補零 / 大小寫 / 無破折號 / 空白 / 垃圾）
    t("norm-bare", _norm_eid("11860") == "EID-11860")
    t("norm-lower", _norm_eid("eid-11860") == "EID-11860")
    t("norm-canon", _norm_eid("EID-11860") == "EID-11860")
    t("norm-nodash", _norm_eid("EID11860") == "EID-11860")
    t("norm-pad", _norm_eid("425") == "EID-00425")
    t("norm-space", _norm_eid("  EID-11860  ") == "EID-11860")
    t("norm-garbage", _norm_eid("abc") is None)
    t("norm-empty", _norm_eid("eid-") is None and _norm_eid(None) is None)

    # _err_category：回 LLM 的錯誤只給穩定分類碼、絕不夾裸 str(e)（infra 名不進 LLM context）
    t("err-timeout", _err_category(TimeoutError("query timed out"), "t") == "timeout")
    t("err-hyt00", _err_category(RuntimeError("HYT00 deadline"), "t") == "timeout")
    _FakeOpErr = type("OperationalError", (Exception,), {})
    t("err-db", _err_category(_FakeOpErr("08001 cannot reach server"), "t") == "db_unavailable")
    _leaky = _err_category(ValueError("boom mes-sql.example.com MES_DB"), "t")
    t("err-other", _leaky == "unavailable")
    t("err-no-leak", "mes-sql" not in _leaky and "PLANT1" not in _leaky and "boom" not in _leaky)

    # machine_lookup：目錄命中 / Gen2 工位 / 未命中 / 垃圾
    real_eid = wem.EQMT_CATALOG[0][2]
    ml = machine_lookup(real_eid.lower())                # 小寫也解析
    t("ml-found", ml["in_catalog"] is True and ml["eid"] == real_eid)
    t("ml-area", ml["area"] in _AREA_LABELS.values())
    t("ml-hint", "equipment_downtime" in ml["hint"])
    gid = next(iter(_GEN2_BAYS))
    mlg = machine_lookup(gid)
    t("ml-gen2", mlg.get("is_gen2_bay") is True
      and mlg.get("parent_eid") == _GEN2_BAYS[gid][0])
    mln = machine_lookup("EID-99999")
    t("ml-notfound", mln["in_catalog"] is False and "not in the fixed equipment-meeting catalog" in mln["note"])
    t("ml-bad", machine_lookup("nonsense")["error"] == "bad_eid")

    # equipment_downtime（demo：eid / fleet / 未知 eid / 垃圾）
    ede = equipment_downtime(eid=real_eid, days=5)
    t("ed-eid", ede["eid"] == real_eid and ede["window_days"] == 5)
    t("ed-caliber", "cmms work-order" in ede["caliber_note"])
    t("ed-now-keys", set(ede["now"]) == {"bucket", "verdict", "state", "wo_no",
                                         "mes_last_h", "stale"})
    # 沉默永不推 DOWN：agent 拿到的 bucket 只有 down/up 兩值（UNKNOWN 已撤，2026-07-10）。
    t("ed-now-bucket-binary", ede["now"]["bucket"] in ("down", "up"))
    t("ed-now-verdict", ede["now"]["verdict"] in ("down", "up_producing", "up_idle"))
    t("ed-days-len", isinstance(ede["days"], list) and len(ede["days"]) == 5)
    expected_labels = [ds for ds, _s, _e in wem.day_windows(wem._DEMO_ANCHOR, 5)]
    t("ed-days-order", [x["date"] for x in ede["days"]] == expected_labels)   # 對齊 day 順序
    t("ed-wos", isinstance(ede["work_orders"], list))
    edf = equipment_downtime(days=7)                     # fleet 摘要（無 eid）
    t("ed-fleet-n", edf["n_machines"] == len(wem.EQMT_CATALOG))
    t("ed-fleet-top", isinstance(edf["top"], list) and len(edf["top"]) <= 10)
    t("ed-fleet-sorted", all(edf["top"][i]["total_stop_h"] >= edf["top"][i + 1]["total_stop_h"]
                             for i in range(len(edf["top"]) - 1)))
    t("ed-fleet-caliber", "NOT MES gaps" in edf["caliber_note"])
    edu = equipment_downtime(eid="EID-99999", days=7)
    t("ed-unknown", edu["found"] is False and "machine_lookup" in edu["note"])
    t("ed-bad-eid", equipment_downtime(eid="garbage")["error"] == "bad_eid")

    # ---- 2026-10-02 助理自評 ①②③⑥（工單 closed_at／停多久／兩種停機時數／冷載）------------------
    _ed_board = _app.load_eqm("demo", 7, 15)
    _ed_we = _ed_board["win_end"]
    _ed_ld = _app._ld_state_from_board(_ed_board)
    _ed_dn = next((e for e, s in _ed_ld.items()
                   if s.get("basis") == "wo" and s.get("since_h") is not None), None)
    t("ed10-demo-has-down-machine", _ed_dn is not None)
    _edn = equipment_downtime(eid=_ed_dn, days=7)
    _edn_row = _find_eqm_row(_ed_board, _ed_dn)
    _edn_wno = _edn["now"]["wo_no"]
    _edn_cur = [w_ for w_ in _edn["work_orders"] if w_["wo_no"] == _edn_wno]
    # ① 最優先：仍開著的單**絕不**帶 closed_at（舊版填的是建板當下＝「已於 02:29:17 關閉」）
    t("ed10-open-wo-closed-at-null", len(_edn_cur) == 1 and _edn_cur[0]["closed_at"] is None
      and _edn_cur[0]["ongoing"] is True and _edn_cur[0]["status"] not in _WO_CLOSED_STATUSES)
    t("ed10-counted-until-is-as-of", _edn_cur and _edn_cur[0]["counted_until"] == _edn["as_of"]
      == _app.json_safe({"x": _ed_we})["x"])
    t("ed10-no-closed-at-while-ongoing", all(w_["closed_at"] is None for w_ in _edn["work_orders"]
                                             if w_["ongoing"]))
    t("ed10-closed-has-closed-at", all(w_["closed_at"] == w_["counted_until"]
                                       for w_ in _edn["work_orders"]
                                       if w_["status"] in _WO_CLOSED_STATUSES))
    # 第二批 ②：加性四欄 brief／latest_note／text_source／text_fetched_at（既有八欄一位元未動）
    t("ed10-wo-keys", all(set(w_) == {"wo_no", "state", "status", "work_type", "opened_at",
                                      "closed_at", "ongoing", "counted_until",
                                      "brief", "latest_note", "text_source", "text_fetched_at"}
                          for w_ in _edn["work_orders"]))
    # ---- 第二批 ②：工單文字＝頁面抽屜同一份（demo＝`_ld_wo_detail` 合成；零取數）-------------------
    _wt_cur = _edn_cur[0] if _edn_cur else {}
    _wt_det, _ = _app._ld_wo_detail(_edn_wno, "demo")
    _wt_card = _app._ld_wo_card(_wt_det, _app.I18N["en"])
    t("wt2-brief-same-as-drawer", _wt_cur.get("brief") == (_wt_card["brief"] or None)
      and _wt_cur.get("brief") and _wt_cur.get("text_source") == "demo")
    _wt_ln = _wt_cur.get("latest_note") or {}
    _wt_notes = list((_wt_det or {}).get("notes") or ())
    if _wt_notes:
        t("wt2-latest-note-is-last", _wt_ln.get("text") == _tail_text(_wt_notes[-1]["body"].strip())[0]
          and _wt_ln.get("type") == _wt_notes[-1]["entry_type"]
          and _wt_ln.get("at") == _fmt_ts(_wt_notes[-1]["at"]))
    else:
        t("wt2-latest-note-diag-fallback", _wt_ln.get("type") == "final_diagnosis"
          and _wt_ln.get("text") == (_wt_det or {}).get("diagnosis"))
    t("wt2-sources-closed-set", all(w_["text_source"] in _WO_TEXT_SOURCES for w_ in _edn["work_orders"]))
    t("wt2-note-says-not-loaded", "'not_loaded'" in _edn["work_orders_note"]
      and "never invent a cause" in _edn["work_orders_note"])
    # 偶數／奇數 demo 單兩形（事件流末筆 vs 歷史單的最終診斷）＋截斷
    _wt_even, _wt_odd = _wo_text("demo", 49800), _wo_text("demo", 49801)
    t("wt2-even-note", (_wt_even["latest_note"] or {}).get("type") == "progress"
      and _wt_even["brief"] == wem.demo_brief(49800))
    t("wt2-odd-diag", (_wt_odd["latest_note"] or {}).get("type") == "final_diagnosis"
      and _wt_odd["latest_note"]["text"] == wem.demo_diag(49801) and _wt_odd["latest_note"]["at"] is None)
    # live 形：**只窺**頁面快取——窺不到＝not_loaded（不是「沒有簡述」）；窺到＝那一份＋時刻；截 200 字
    _sv_wdp = getattr(_app, "wo_detail_peek")
    try:
        _app.wo_detail_peek = lambda w: (None, None)
        _wt_nl = _wo_text("live", 123)
        t("wt2-live-not-loaded", _wt_nl == {"brief": None, "latest_note": None,
                                            "text_source": "not_loaded", "text_fetched_at": None})
        _long = "x" * 260
        _app.wo_detail_peek = lambda w: ({"brief_description": " Spindle jam ",
                                          "notes": [{"at": datetime(2026, 7, 1, 9), "entry_type": "report",
                                                     "body": "first"},
                                                    {"at": datetime(2026, 7, 1, 11), "entry_type": "hold",
                                                     "body": _long}]}, "2026-07-01 12:00:00")
        _wt_pc = _wo_text("live", 123)
        t("wt2-live-page-cache", _wt_pc["brief"] == "Spindle jam" and _wt_pc["text_source"] == "page_cache"
          and _wt_pc["text_fetched_at"] == "2026-07-01 12:00:00"
          and _wt_pc["latest_note"] == {"type": "hold", "at": "2026-07-01 11:00:00",
                                        "text": "…" + "x" * _WO_TEXT_MAX,
                                        "text_truncated": "head_omitted", "text_chars": 260})
        _app.wo_detail_peek = lambda w: (_ for _ in ()).throw(RuntimeError("boom"))
        t("wt2-live-never-raises", _wo_text("live", 123)["text_source"] == "not_loaded")
    finally:
        _app.wo_detail_peek = _sv_wdp
    # 尾段截斷（2026-10-02 第三次親測：取頭＝拿到最舊步驟）
    _tl_new = "NEWSTATE: " + "n" * 140 + " ETA Friday"
    _tl_long = "OLDSTEP-" + "a" * 300 + "\n" + _tl_new
    _tl_t, _tl_f = _tail_text(_tl_long)
    t("tail-long-takes-end", _tl_f and _tl_t.startswith("…") and _tl_t.endswith("ETA Friday")
      and "OLDSTEP" not in _tl_t and len(_tl_t) <= _WO_TEXT_MAX + 1)
    t("tail-aligns-to-newline", _tl_t == "…" + _tl_new)
    t("tail-short-verbatim", _tail_text("short note") == ("short note", False)
      and _tail_text("") == ("", False))
    _tl_edge = "y" * _WO_TEXT_MAX
    t("tail-boundary-exact", _tail_text(_tl_edge) == (_tl_edge, False)
      and _tail_text("z" + _tl_edge)[1] is True and _tail_text("z" + _tl_edge)[0] == "…" + _tl_edge)
    _tl_cjk = "舊" * 80 + "。" + "新" * 140 + "。現況：等零件"
    _tl_c, _tl_cf = _tail_text(_tl_cjk)
    t("tail-multibyte-sentence", _tl_cf and _tl_c.startswith("…") and _tl_c.endswith("等零件")
      and "舊" not in _tl_c and len(_tl_c) <= _WO_TEXT_MAX + 1)
    _tl_sent = "b" * 200 + ". Now fixed. Verified OK today"
    t("tail-ascii-sentence", _tail_text(_tl_sent, 40)[0].endswith("Verified OK today")
      and _tail_text(_tl_sent, 40)[1] is True)
    # 多筆日誌：取 at 最新（輸入亂序也一樣）
    _sv_wdp2 = _app.wo_detail_peek
    try:
        _app.wo_detail_peek = lambda w: ({"notes": [
            {"at": datetime(2026, 7, 3), "entry_type": "progress", "body": "newest"},
            {"at": datetime(2026, 7, 1), "entry_type": "report", "body": "oldest"}]}, "x")
        t("tail-picks-newest-by-time", _wo_text("live", 1)["latest_note"]["text"] == "newest")
    finally:
        _app.wo_detail_peek = _sv_wdp2
    t("tail-note-says-last-part", "head_omitted" in _WO_TEXT_NOTE and "LAST part" in _WO_TEXT_NOTE)
    t("wt2-trim-without-src-unchanged",
      set(_trim_wos_win({"wos_win": [{"wno": 5, "start": datetime(2026, 7, 1), "end": datetime(2026, 7, 2),
                                      "status": None}]})[0])
      == {"wo_no", "state", "status", "work_type", "opened_at", "closed_at", "ongoing", "counted_until"})
    # app 端被動記錄：put → peek 同一份；過 TTL ＝沒抓過；壞輸入不炸
    _sv_seen = dict(_app._WO_DETAIL_SEEN)
    try:
        _app._wo_detail_seen_put(987654, {"brief_description": "b"})
        _pk_d, _pk_t = _app.wo_detail_peek(987654)
        t("wt2-app-seen-roundtrip", _pk_d == {"brief_description": "b"} and isinstance(_pk_t, str))
        _app._wo_detail_seen_put(None, {"x": 1})
        _app._wo_detail_seen_put(1, "not a dict")
        t("wt2-app-seen-rejects-bad", None not in _app._WO_DETAIL_SEEN and 1 not in _app._WO_DETAIL_SEEN)
        _m0, _ts0, _d0 = _app._WO_DETAIL_SEEN[987654]
        _app._WO_DETAIL_SEEN[987654] = (_m0 - _app._WO_DETAIL_SEEN_TTL - 1, _ts0, _d0)
        t("wt2-app-seen-ttl", _app.wo_detail_peek(987654) == (None, None))
    finally:
        _app._WO_DETAIL_SEEN.clear()
        _app._WO_DETAIL_SEEN.update(_sv_seen)
    t("ed10-wo-note", "closed_at is set ONLY when the ticket is actually closed" in _edn["work_orders_note"]
      and "NOT a close time" in _edn["work_orders_note"])
    # 純函式三形：已對齊復機但單未結（NOTE-60）／遷移單無 history／不知道 as-of（保守側）
    _t0, _t1, _t2 = datetime(2026, 7, 1, 8), datetime(2026, 7, 1, 12), datetime(2026, 7, 2, 9)
    _pra = _trim_wos_win({"wos_win": [{"wno": 1, "start": _t0, "end": _t1, "status": "OPEN",
                                       "work_type": "REACTIVE"}]}, _t2)[0]
    t("ed10-pra-open-not-closed", _pra["closed_at"] is None and _pra["ongoing"] is False
      and _pra["counted_until"] == _t1)
    _mig = _trim_wos_win({"wos_win": [{"wno": 2, "start": _t0, "end": _t1, "status": None}]}, _t2)[0]
    t("ed10-migration-closed", _mig["closed_at"] == _t1 and _mig["ongoing"] is False)
    _ong = _trim_wos_win({"wos_win": [{"wno": 3, "start": _t0, "end": _t2, "status": None}]}, _t2)[0]
    t("ed10-ongoing-by-as-of", _ong["ongoing"] is True and _ong["closed_at"] is None)
    _noasof = _trim_wos_win({"wos_win": [{"wno": 4, "start": _t0, "end": _t1, "status": None}]})[0]
    t("ed10-no-as-of-conservative", _noasof["closed_at"] is None and _noasof["ongoing"] is False)
    # ② 停多久＝landing 機台卡那一格（同一支 `_ld_state_from_board`、同一個格式器）
    _sh = _ed_ld[_ed_dn]["since_h"]
    _w_start = next(x["start"] for x in _app._ld_row_wos(_edn_row) if x.get("wno") == _edn_wno)
    t("ed10-ongoing-h-same-as-card", _edn["ongoing_duration_h"] == round(_sh, 1))
    t("ed10-ongoing-txt-same-as-card",
      _edn["ongoing_duration"] == _app._ld_since_txt(_sh, _app._LD_I18N["en"]))
    t("ed10-ongoing-since-is-wo-start", _edn["ongoing_since"] == _app.json_safe({"x": _w_start})["x"])
    t("ed10-ongoing-note", "how long has it been down" in _edn["ongoing_note"])
    _ed_up = next((e for e, s in _ed_ld.items() if s.get("since_h") is None
                   and (_find_eqm_row(_ed_board, e) or {}).get("now", {}).get("bucket") == "up"), None)
    _edu2 = equipment_downtime(eid=_ed_up, days=7)
    t("ed10-ongoing-null-when-up", _edu2["ongoing_since"] is None
      and _edu2["ongoing_duration_h"] is None and _edu2["ongoing_duration"] is None)
    t("ed10-ongoing-helper-never-raises", _ongoing_down(None, None, "EID-00000") == (None, None)
      and _ongoing_txt(None) is None)
    # ③ 兩種停機時數：`stop_h_rw`＝頁面帶頭的應生產時段 Σ（＝`equip.stop_h`），total_stop_h＝24h 原始
    t("ed10-stop-rw-is-erw", _edn["stop_h_rw"] == (round(_edn_row["erw"]["stop_h"], 1)
                                                  if _edn_row.get("erw") else None))
    t("ed10-caliber-note-both", "stop_h_rw" in _edn["caliber_note"] and "24h axis" in _edn["caliber_note"]
      and "cmms work-order" in _edn["caliber_note"] and "NOT MES gaps" in _edn["caliber_note"])
    _edf2 = equipment_downtime(days=7)
    t("ed10-fleet-as-of-and-rw", _edf2["as_of"] == _edn["as_of"]
      and all("stop_h_rw" in x for x in _edf2["top"]))
    t("ed10-queries-peek-first", _TOOL_QUERIES["equipment_downtime"][:2] == ("app._peek_eqm", "app.load_eqm"))
    # ⑥ 冷載：垃圾 EID 不取數；等不到 ⇒ still_loading＋hint；同鍵在飛不疊第二輪；loader 例外原樣分類
    import threading as _thr
    _sv_pk, _sv_le, _sv_qg = _app._peek_eqm, _app.load_eqm, _app.load_qlik_grid
    _sv_env = os.environ.get("BOARD_MCP_COLD_WAIT_S")
    _calls_le, _gate = [], _thr.Event()

    def _drain(timeout=10.0):
        _end = time.monotonic() + timeout
        while _COLD_INFLIGHT and time.monotonic() < _end:
            time.sleep(0.02)
        return not _COLD_INFLIGHT

    try:
        os.environ["BOARD_MCP_COLD_WAIT_S"] = "1"
        _app._peek_eqm = lambda *a, **kw: _calls_le.append(("peek",) + a) or None
        _app.load_eqm = lambda *a, **kw: _calls_le.append(("load",) + a) or (_gate.wait(10) and _ed_board)
        t("ed10-bad-eid-zero-fetch", equipment_downtime(eid="garbage")["error"] == "bad_eid"
          and _calls_le == [])
        _c1 = equipment_downtime(eid=_ed_dn, days=7)
        _c2 = equipment_downtime(eid=_ed_dn, days=7)
        t("ed10-cold-still-loading", _c1.get("still_loading") is True
          and _c1["unavailable_reason"] == "timeout" and "Ask again in about" in _c1["hint"]
          and _c1["provenance"]["confidence"] == "low" and _c1["board"] is None)
        t("ed10-cold-single-flight", _c2.get("still_loading") is True
          and sum(1 for c in _calls_le if c[0] == "load") == 1)
        _gate.set()
        t("ed10-cold-drains", _drain())
        _app.load_eqm = lambda *a, **kw: _ed_board
        _c3 = equipment_downtime(eid=_ed_dn, days=7)
        t("ed10-warm-after-cold", "still_loading" not in _c3 and _c3["eid"] == _ed_dn
          and _c3["ongoing_duration_h"] == _edn["ongoing_duration_h"])
        # 頁面那一份稍舊（TTL 已過、仍在窺視容忍內）⇒ 照用＋背景重建一次（不等它）
        _calls_le.clear()
        _app._peek_eqm = lambda *a, **kw: (_ed_board, float(getattr(_app, "EQM_TTL", 600)) + 60.0)
        _app.load_eqm = lambda *a, **kw: _calls_le.append(("load",) + a) or _ed_board
        _c4 = equipment_downtime(eid=_ed_dn, days=7)
        t("ed10-stale-peek-served", _c4["eid"] == _ed_dn and "still_loading" not in _c4)
        t("ed10-stale-peek-refreshes", _drain() and _calls_le == [("load", "demo", 7, 15)])
        # loader 自己的例外 ⇒ 既有 `_err_category` 分支（不是 still_loading）
        _app._peek_eqm = lambda *a, **kw: None

        def _le_boom(*a, **k):
            raise RuntimeError("HYT00 mes-sql.example.com exploded")
        _app.load_eqm = _le_boom
        _c5 = equipment_downtime(eid=_ed_dn, days=7)
        t("ed10-loader-error-classified", _c5["unavailable_reason"] == "timeout"
          and "still_loading" not in _c5 and "mes-sql" not in _json.dumps(_c5, default=str))
        # yield_summary 冷載同形（d=30 冷載數分鐘那一支）
        _qgate = _thr.Event()
        _app.load_qlik_grid = lambda *a, **kw: _qgate.wait(10) and _sv_qg(*a, **kw)
        _y1 = yield_summary(30)
        t("ys10-cold-still-loading", _y1.get("still_loading") is True
          and _y1["unavailable_reason"] == "timeout" and _y1["stations"] is None
          and _y1["kpis"] is None and "NOT zero yield" in _y1["caveat"]
          and _y1["provenance"]["confidence"] == "low" and "YIELD loss only" in _y1["loss_scope"])
        _qgate.set()
        t("ys10-cold-drains", _drain())
    finally:
        _gate.set()
        _app._peek_eqm, _app.load_eqm, _app.load_qlik_grid = _sv_pk, _sv_le, _sv_qg
        if _sv_env is None:
            os.environ.pop("BOARD_MCP_COLD_WAIT_S", None)
        else:
            os.environ["BOARD_MCP_COLD_WAIT_S"] = _sv_env
        _drain()
    # `_cold_wait_s` 夾值（壞值退預設、下限 1、上限 600）
    for _v, _want in (("abc", _COLD_WAIT_DEFAULT_S), ("0", 1.0), ("9999", 600.0), ("45", 45.0)):
        os.environ["BOARD_MCP_COLD_WAIT_S"] = _v
        t(f"cold-wait-clamp-{_v}", _cold_wait_s() == _want)
    if _sv_env is None:
        os.environ.pop("BOARD_MCP_COLD_WAIT_S", None)
    else:
        os.environ["BOARD_MCP_COLD_WAIT_S"] = _sv_env
    t("cold-hint-minutes", "about 3 minutes" in _cold_hint())

    # ---- 單機長停 since／原因（2026-10-02；R41 §12.1-7 的 MCP 缺口）--------------------------
    # 長停機 EID **由橋常數推導、不寫死**（公開樹的合成名冊會換掉 EID；同 WP-10d R-P11 慣例）。
    t("dm-not-dormant-ml", ml["dormant"] is None and ml["dormant_registry"] == "bridge"
      and ml["provenance"]["metric_ids"] == [])
    t("dm-not-dormant-ed", ede["dormant"] is None and ede["dormant_registry"] == "bridge"
      and ede["provenance"]["metric_ids"] == list(_TOOL_METRIC_IDS["equipment_downtime"]))
    t("dm-registry-enum", all(x in DORMANT_REGISTRY for x in (ml["dormant_registry"],
                                                              ede["dormant_registry"])))
    _dm_board = _app.load_eqm("demo", 5, 15)
    _dm_eids = [r[0] for r in wdorm.DORMANT_BRIDGE
                if (_board_machine_row(_dm_board, r[0]) or {}).get("dormant")]
    if not _dm_eids:
        print("board_mcp selftest: skipped: dormant since/reason (no bridge EID on the demo board)")
    else:
        _de = _dm_eids[0]
        _drow = _board_machine_row(_dm_board, _de)["dormant"]
        _want = {"since": str(_drow["since"])[:10],
                 "reason": _drow.get("reason_en") or _drow.get("reason_zh"), "note": _DORMANT_NOTE}
        _mdf = machine_lookup(_de)
        _edd = equipment_downtime(eid=_de, days=5)
        _udd = updown_now(eid=_de)
        # ★★ 承重：三支單機工具＝**頁面那一列**（同一張板、同一份投影），逐欄相同
        t("dm-ml-same-as-board", _mdf["dormant"] == _want and _mdf["dormant_registry"] == "bridge")
        t("dm-ed-same-as-board", _edd["dormant"] == _want and _edd["dormant_registry"] == "bridge")
        t("dm-ud-same-as-board", _udd["dormant"] == _want and _udd["dormant_registry"] == "bridge")
        # 與 `/machine/{eid}` 下鑽同一支投影（demo＝`events=None`＝橋常數；as-of 今天）
        _dinf = wdorm.dormant_info(_de, datetime.now().date(), None) or {}
        t("dm-same-as-machine-page", _want["since"] == _dinf.get("since")
          and _want["reason"] == _dinf.get("reason_en"))
        # fleet 分支既有的 `dormant` 清單（since／reason）與單機分支一致
        _ud_fl = {d["eid"]: d for d in un["dormant"]}
        t("dm-ud-fleet-agrees", _de in _ud_fl and _ud_fl[_de]["since"] == _want["since"]
          and _ud_fl[_de]["reason"] == _want["reason"])
        t("dm-ed-bucket-dormant", _edd["now"]["bucket"] == "dormant" and _edd["avail_pct"] is None)
        t("dm-metric-id-cited", _mdf["provenance"]["metric_ids"] == ["equip.dormant"]
          and _edd["provenance"]["metric_ids"][-1] == "equip.dormant"
          and _udd["provenance"]["metric_ids"] == ["equip.now_bucket", "equip.dormant"]
          and "equip.dormant" in M.ALL_METRICS)
        t("dm-since-iso", re.fullmatch(r"\d{4}-\d{2}-\d{2}", _want["since"]) is not None)
        t("dm-note-not-down", "NOT DOWN" in _DORMANT_NOTE and "verbatim" in _DORMANT_NOTE)
        # 退路：板窺不到 ⇒ `wdorm.dormant_info`（demo＝橋常數）——答案不變
        _sv_peek = _app._peek_eqm
        try:
            _app._peek_eqm = lambda *a, **kw: None
            _mlf = machine_lookup(_de)
            t("dm-fallback-registry-same", _mlf["dormant"] == _want
              and _mlf["dormant_registry"] == "bridge")
            _app._peek_eqm = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
            _mlx = machine_lookup(_de)
            t("dm-exception-honest", _mlx["dormant"] is None
              and _mlx["dormant_registry"] == "unavailable" and _mlx["in_catalog"] is True)
        finally:
            _app._peek_eqm = _sv_peek
        # ★★ 合成進程（R-C1～R-C6／C-SY2a）：取不到合成登記 ⇒ 橋常數**不得**外露（日期、原因皆不得
        #   出現在回應全文），`dormant_registry` 說 unavailable、不說 bridge。
        _sv_flag, _sv_peek, _sv_a11 = _wrs.is_synth_process, _app._peek_eqm, _app._peek_a11_events
        try:
            _wrs.is_synth_process = lambda: True
            _app._peek_eqm = lambda *a, **kw: None
            _app._peek_a11_events = lambda: (None, None, None)
            _mls = machine_lookup(_de)
            _blob = _json.dumps(_mls, ensure_ascii=False, default=str)
            _leak = [v for r in wdorm.DORMANT_BRIDGE for v in (r[1], r[3], r[4]) if v and v in _blob]
            t("dm-synth-no-bridge", _mls["dormant"] is None and not _leak
              and _mls["dormant_registry"] == "unavailable")
            # 第二批 ①：座級長停只讀板列；板窺不到 ⇒ None（**不**退橋常數、不猜）
            t("ds2-synth-no-board-no-seats", _mls["dormant_seats"] is None)
            _app._peek_a11_events = lambda: ([], "live", 0.0)       # 合成登記可讀、這台沒登
            _mls2 = machine_lookup(_de)
            t("dm-synth-registry-live", _mls2["dormant"] is None
              and _mls2["dormant_registry"] == "synthetic")
        finally:
            _wrs.is_synth_process, _app._peek_eqm, _app._peek_a11_events = _sv_flag, _sv_peek, _sv_a11
    # 純函式：來源閉集對照（逐條同 `app._a11_src_note`）＋ view 形
    t("dm-registry-map", [_dormant_registry(s) for s in ("live", "live_stale", None, "gated",
                                                         "error:x")]
      == ["cmms", "cmms_cached", "bridge", "bridge", "bridge"])
    t("dm-view-none", _dormant_view(None) is None and _dormant_view({}) is None
      and _dormant_view({"since": None, "reason_en": "x"}) is None)
    t("dm-view-reason-fallback", _dormant_view({"since": "2001-02-03", "reason_zh": "z",
                                                "reason_en": None})["reason"] == "z")
    t("dm-view-seat-no-reason", _dormant_view({"since": "2001-02-03", "reason_zh": None,
                                               "reason_en": None})["reason"] is None)

    # updown_now(eid=…) + DOWN-first 排序 + n_up_omitted
    # ★ 2026-10-02 第二批 ⑤：`ud-eid-roster` 比對兩次呼叫的 `board_built_at`（HH:MM）——demo 板每次
    #   即時合成 ⇒ 兩次呼叫跨分鐘邊界就 flake。改成**兩次呼叫吃同一張板**（窺一次、釘住、兩次都回它）
    #   ＝確定性；被測的仍是「eid 分支帶的名冊與板時戳＝fleet 分支那一份」。
    import app as _app_r
    _sv_pk_r = _app_r._peek_eqm
    _pk_once = _sv_pk_r("demo", _app_r.EQM_DAYS, 15)
    try:
        _app_r._peek_eqm = lambda *a, **kw: _pk_once
        un2 = updown_now()
        some_eid = un2["rows"][0]["eid"]
        une = updown_now(eid=some_eid)
    finally:
        _app_r._peek_eqm = _sv_pk_r
    ords = [_UD_BUCKET_ORDER.get(r["bucket"], 9) for r in un2["rows"]]
    t("ud-down-first", ords == sorted(ords))             # kept 列 bucket 單調（DOWN→UNKNOWN→UP）
    t("ud-omit-key", "n_up_omitted" in un2 and un2["n_up_omitted"] >= 0)
    t("ud-eid-machine", une["machine"]["eid"] == some_eid)
    t("ud-eid-notfound", updown_now(eid="EID-99999").get("found") is False)
    t("ud-eid-bad", updown_now(eid="xyz")["error"] == "bad_eid")
    # ★ 包①：eid 分支也帶名冊與板時戳（沒有它，單機答案照樣說不出「這是哪張名冊」）
    t("ud-eid-roster", une["roster_size"] == un2["n_total"]
      and une["board_built_at"] == un2["board_built_at"] and une["age_min"] == 0)
    t("ud-eid-caliber", une["provenance"]["caliber"] == _CALIBER_BOARD_NOW)
    # ★ 包①：目錄裡的機台**一定**在名冊上（舊的動態 MES 名冊會漏掉它們——2026-09-22
    #   實測 `EID-25749`／`EID-25526`／`EID-38790` 正是這樣從工具那把尺上消失的）
    t("ud-eid-catalog-present",
      all(updown_now(eid=e).get("found") is not False
          for _a, _n, e in wem.EQMT_CATALOG[:8]))

    # ---- A1b 出處信封 + 語意軸兩工具（`SPEC-HERMES-TRUST.md` §3／§4.1）-------------------
    # 十一鍵齊、enum 內、evidence_ref 穩定；**既有欄位一個位元不動**（C-HT9）由 smoke `[metrics]`
    # 的 `_EXPECTED_KEYS_BEFORE` 釘（那一組是改碼前實跑抓下來的基準）。
    _probe = [ping(), list_stations(), fleet_scoreboard(days=3),
              station_overview(mt.CAL_CLASS, 7), station_units(mt.CAL_CLASS, 7),
              yield_summary(3), machine_lookup(real_eid),
              equipment_downtime(eid=real_eid, days=5), updown_now(),
              list_metrics(), explain_metric("util.availability", "en"),
              get_metric("util.fleet_util_24h", "fleet", "3d"),
              station_variables("segDiePlacement"), wo_serials_status("WO910001/1"),
              serial_genealogy("J0SYN02"), wo_materials("WO910001/1"),
              lot_where_used("LA-9100001-9001"), shipment_lookup(lot="V0SYN01"),
              downtime_monthly(),
              station_failure_pareto("CAL"), module_die_etest("J0SYN09"),
              equipment_status_events("EID-07905"), equipment_modules("EID-07905"),
              equipment_calibration("EID-07905"), segment_data("J0SYN17", "CLN"),
              station_spec_limits("CLN"), station_retest("CLN"), wo_route("WO910002/1"),
              knowledge_card("DAF")]
    t("prov-29-tools", len(_probe) == 29
      and {p["provenance"]["tool"] for p in _probe} == set(_TOOL_QUERIES))
    t("prov-keys-identical",
      all(tuple(p["provenance"]) == PROVENANCE_KEYS for p in _probe))
    t("prov-enums", all(p["provenance"]["confidence"] in ("high", "medium", "low")
                        and p["provenance"]["freshness"] in M.FRESHNESS
                        and p["provenance"]["caliber"] in M.CALIBERS for p in _probe))
    import re as _re
    _EV_RE = _re.compile(r"^[a-z_]+:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}:[0-9a-f]{8}$")
    t("prov-evidence-ref", all(_EV_RE.match(p["provenance"]["evidence_ref"]) for p in _probe))
    t("prov-window-shape",
      all(set(p["provenance"]["window"]) == {"from", "to"} for p in _probe))
    t("prov-metric-ids-real",
      all(i in M.ALL_METRICS for ids in _TOOL_METRIC_IDS.values() for i in ids))
    # C-HT10：同 tool 同參數同 fetched_at ⇒ evidence_ref 逐位元相同（**不含時鐘**才驗得準）
    _e1 = _envelope("t", metric_ids=(), queries=(), window=_NO_WINDOW,
                    fetched_at="2026-09-19 14:03:11", freshness="fixture", row_count=1,
                    caliber=_CALIBER_UTIL_AXIS, confidence="high", page=None,
                    params={"a": 1, "b": 2})
    _e2 = _envelope("t", metric_ids=(), queries=(), window=_NO_WINDOW,
                    fetched_at="2026-09-19 14:03:11", freshness="fixture", row_count=1,
                    caliber=_CALIBER_UTIL_AXIS, confidence="high", page=None,
                    params={"b": 2, "a": 1})           # 參數順序不同 ⇒ 正規化後同一把
    t("prov-evidence-stable", _e1["evidence_ref"] == _e2["evidence_ref"])
    _e3 = _envelope("t", metric_ids=(), queries=(), window=_NO_WINDOW,
                    fetched_at="2026-09-19 14:03:11", freshness="fixture", row_count=1,
                    caliber=_CALIBER_UTIL_AXIS, confidence="high", page=None, params={"a": 9})
    t("prov-evidence-varies", _e1["evidence_ref"] != _e3["evidence_ref"])
    t("prov-demo-fixture", _freshness() == "fixture")   # demo ⇒ 誠實 fixture、不冒充 live

    # ---- #167 P4 `downtime_monthly`（demo fixture；頁面同一支讀端）--------------------------
    import board_downtime as _wdt_st
    dm = downtime_monthly()
    _c, _i = _wdt_st.open_source("demo")
    _sel = _wdt_st.default_months(_wdt_st.month_options(_c, _i["today"]), _i["today"])
    _mm = _wdt_st.downtime_model(_c, _sel, _i["today"])
    t("dt-found-default-6", dm["found"] and [x["month"] for x in dm["months"]] == _sel)
    t("dt-same-reader-as-page", dm["kpi"]["total_h"] == round(_mm["kpi"]["total_h"], 1)
      and dm["kpi"]["roster_n"] == _mm["kpi"]["roster_n"])
    t("dt-axis-enum", all(x["axis"] in ("runwindow", "24h") for x in dm["months"]))
    t("dt-caveat-two-rulers", any("NOT comparable" in c for c in dm["caveats"]))
    t("dt-no-person-no-summary", all(set(r) == set(_wdt_st.TOP_FIELDS)
                                     for g in dm["top"] for r in g["rows"]))
    t("dt-provenance", dm["provenance"]["caliber"] == "wo-ledger"
      and dm["provenance"]["page"] == M.P_DOWNTIME == "/equipment/downtime/monthly"
      and set(dm["provenance"]["metric_ids"]) <= set(M.ALL_METRICS))
    dm2 = downtime_monthly(months=["2026-08", "2026-09", "1999-01"], area="Zone C", top=2)
    import board_eqmeeting as _wem_dt      # WP-10d（R-P11）：區顯示標籤由 AREAS 推導（g5 起私有／公開兩樹皆＝Zone C）
    _wl_label = dict(_wem_dt.AREAS)["zc"]
    t("dt-area-top-rejected", dm2["rejected_months"] == ["1999-01"]
      and all(g["area"] == _wl_label and len(g["rows"]) <= 2 for g in dm2["top"])
      and dm2["provenance"]["confidence"] == "low")
    t("dt-cross-axis-delta-null", dm2["kpi"]["delta"] is None
      and dm2["kpi"]["delta_reason"] == "cross_axis")
    t("dt-bad-area", downtime_monthly(area="Zone D").get("error") == "bad_area")

    # list_metrics / explain_metric
    lm = list_metrics()
    t("lm-all", lm["n"] == len(M.ALL_METRICS) and len(lm["metrics"]) == lm["n"])
    lmu = list_metrics("/utilization")
    t("lm-page", lmu["n"] > 0 and all("/utilization" in m["pages"] for m in lmu["metrics"]))
    lmn = list_metrics("/nope")
    t("lm-unknown-page-empty", lmn["metrics"] == [] and "error" not in lmn
      and lmn["provenance"]["confidence"] == "low" and lmn["provenance"]["page"] is None)
    t("lm-no-formula", all("formula" not in m for m in lm["metrics"]))
    for _mid in M.ALL_METRICS:
        for _lg in ("en", "zh"):
            _d = explain_metric(_mid, _lg)
            assert "error" not in _d and _d["formula"] and _d["title"], f"explain {_mid}/{_lg}"
    t("em-all-two-langs", True)
    t("em-caliber-matches",
      explain_metric("util.availability")["provenance"]["caliber"]
      == M.ALL_METRICS["util.availability"].caliber)
    t("em-bad-id", explain_metric("nope.nope")["error"] == "unknown_metric")
    t("em-bad-lang", explain_metric("util.availability", "fr")["error"] == "unknown_lang")

    # ---- A2 `get_metric`（值軸；`SPEC-HERMES-TRUST.md` §4.1／§12.2 驗收釘 ①②）----------
    _GM_KEYS = {"source", "id", "scope", "window", "days", "value", "unit", "caliber",
                "title", "not_measurable", "unavailable_reason", "note", "provenance"}
    gm = get_metric("util.fleet_util_24h", "fleet", "3d")
    t("gm-happy-keys", set(gm) == _GM_KEYS)
    t("gm-happy-value", isinstance(gm["value"], (int, float)) and gm["days"] == 3
      and gm["unit"] == "%" and gm["not_measurable"] is False)
    t("gm-caliber-registry", gm["caliber"] == M.ALL_METRICS["util.fleet_util_24h"].caliber)
    t("gm-metric-ids", gm["provenance"]["metric_ids"] == ["util.fleet_util_24h"])
    t("gm-queries-real", gm["provenance"]["queries"] == ["app.load_scoreboard"])
    t("gm-freshness-not-static", gm["provenance"]["freshness"] == "fixture")
    t("gm-page-fleet", gm["provenance"]["page"] == "/utilization")
    gmc = get_metric("station.quality", mt.CAL_CLASS, "7d")
    t("gm-cls-keys", set(gmc) == _GM_KEYS and gmc["scope"] == mt.CAL_CLASS)
    t("gm-cls-page", gmc["provenance"]["page"] == f"/station/{mt.CAL_CLASS}")
    t("gm-cls-queries", gmc["provenance"]["queries"]
      == ["app.load_qlik_grid", "board_data.qlik_rows_by_cls"])
    t("gm-honest-blank", gmc["value"] is not None or bool(gmc["unavailable_reason"]))
    gmz = get_metric("util.zero_len_unmeasurable", mt.CAL_CLASS, "7d")
    t("gm-bool-metric", isinstance(gmz["value"], bool) or gmz["value"] is None)
    # 驗證順序與錯誤物件形（逐位元同 `app.py` 的 400 body：{"error","hint"}＋必要時 "given"）
    for _lbl, _out, _code in (
            ("missing-scope", get_metric("util.fleet_util_24h", "", "7d"), "missing_scope"),
            ("missing-window", get_metric("util.fleet_util_24h", "fleet", ""), "missing_window"),
            ("bad-window-high", get_metric("util.fleet_util_24h", "fleet", "90d"), "bad_window"),
            ("bad-window-zero", get_metric("util.fleet_util_24h", "fleet", "0d"), "bad_window"),
            ("bad-window-shape", get_metric("util.fleet_util_24h", "fleet", "7 days"), "bad_window"),
            ("unknown-scope", get_metric("util.fleet_util_24h", "segNope", "7d"), "unknown_scope"),
            ("unknown-id", get_metric("nope.nope", "fleet", "7d"), "unknown_metric"),
            # ⚠ 2026-09-21：原本這一格用 `util.availability`——它現在**有** cls 路徑
            #   ⇒ fleet 下回的是 `scope_mismatch`。換成真的一條路徑都沒有的那一筆。
            ("no-path", get_metric("util.down_by_state", "fleet", "7d"), "no_value_path"),
            ("scope-mismatch", get_metric("station.quality", "fleet", "7d"), "scope_mismatch")):
        t(f"gm-err-{_lbl}", _out.get("error") == _code)
        t(f"gm-err-{_lbl}-shape", {"error", "hint"} <= set(_out))
        t(f"gm-err-{_lbl}-prov", _out["provenance"]["confidence"] == "low")
        t(f"gm-err-{_lbl}-noleak",
          "Traceback" not in _json.dumps(_out, ensure_ascii=False, default=str))
    t("gm-unknown-id-no-fabricated-ids",
      get_metric("nope.nope", "fleet", "7d")["provenance"]["metric_ids"] == [])
    t("gm-no-value-hint",
      "explain_metric" in get_metric("util.down_by_state", "fleet", "7d")["hint"])
    # ★ RW 軸 11 筆（2026-09-21 `station_axis_payload` 抽出後可達）：demo 下是
    #   「有路徑、這一次沒有值」＝值的形 ＋ `unavailable_reason`，**不是** `no_value_path`。
    for _rwid in ("util.planned_h", "util.availability", "station.oee", "station.rate_good",
                  "station.cap_theory_ph"):
        _grw = get_metric(_rwid, mt.CAL_CLASS, "7d")
        t(f"gm-rw-{_rwid}-shape", set(_grw) == _GM_KEYS and "error" not in _grw)
        t(f"gm-rw-{_rwid}-honest",
          _grw["value"] is not None or _grw["not_measurable"]
          or bool(_grw["unavailable_reason"]))
        t(f"gm-rw-{_rwid}-queries",
          _grw["provenance"]["queries"] == ["app.station_axis_payload"])
    t("gm-rw-demo-live-only",
      get_metric("util.planned_h", mt.CAL_CLASS, "7d")["unavailable_reason"]
      == _GM_RW_LIVE_ONLY)                  # demo ⇒ 三層擋的第一行（結構上沒有這條軸）
    t("gm-rw-fleet-scope-mismatch",         # RW 軸是站級的事 ⇒ fleet 粒度誠實說不對
      get_metric("util.planned_h", "fleet", "7d")["error"] == "scope_mismatch")
    # ★ 53 筆全掃：不是可達、就是有一個閉集裡的 reason（零遺漏、零額外）
    _cov_bad = []
    for _mid in M.ALL_METRICS:
        for _sc in ("fleet", mt.CAL_CLASS):
            _o = get_metric(_mid, _sc, "7d")
            if "error" not in _o:
                continue
            if _o["error"] == "scope_mismatch":
                continue
            if _o["error"] != "no_value_path" or _o["reason"] not in NO_VALUE_REASONS:
                _cov_bad.append((_mid, _sc, _o.get("error"), _o.get("reason")))
    t("gm-53-coverage", not _cov_bad)
    t("gm-reason-table-total", len(_NO_VALUE_REASON) == len(M.ALL_METRICS) - len(M.VALUE_PATHS))
    # 2026-09-21：RW 軸 12 筆 → **11 筆進 `VALUE_PATHS`、剩 `util.down_by_state` 一筆**
    # （值是逐態 dict、不是純量）⇒ 第六個閉集值 `non_scalar_value`；同批把借來的
    # `rw_axis_not_exposed` 釘成**零筆**（留在閉集只為歷史存證，不得再借）。
    t("gm-non-scalar-1",
      sum(1 for r in _NO_VALUE_REASON.values() if r == "non_scalar_value") == 1
      and _NO_VALUE_REASON.get("util.down_by_state") == "non_scalar_value"
      and "rw_axis_not_exposed" not in set(_NO_VALUE_REASON.values()))
    t("gm-reasons-closed", set(_NO_VALUE_REASON.values()) <= set(NO_VALUE_REASONS))
    t("gm-transform-enum", set(_VALUE_TRANSFORMS) == set(M.TRANSFORMS))
    t("gm-window-parse", _parse_window("1d") == 1 and _parse_window("30d") == 30
      and _parse_window("31d") is None and _parse_window("0d") is None
      and _parse_window("7") is None and _parse_window(None) is None
      and _parse_window(" 7d ") == 7)
    t("gm-window-not-clamped", get_metric("util.fleet_util_24h", "fleet", "31d")["error"]
      == "bad_window")                      # `_clamp_days` 會收斂，這裡刻意**拒**
    # 零工期站：util 值 null ＋ 不可測旗 ＋ 散文（**絕不是 0**）
    _orig_s1 = _app.load_scoreboard_one
    _app.load_scoreboard_one = lambda *a, **k: {
        "cls": a[1] if len(a) > 1 else "segZeroLen", "util_strict_pct": 0.0,
        "producing_sec": 0.0, "active_span_sec": 0.0, "fleet_size": 2, "n_seg": 40,
        "zero_len_only": True}
    try:
        _gz = get_metric("util.fleet_util_24h", mt.CAL_CLASS, "7d")
        t("gm-zerolen-null", _gz["value"] is None and _gz["not_measurable"] is True
          and _gz["note"] == _UTIL_UNMEASURABLE_NOTE)
        t("gm-zerolen-low-conf", _gz["provenance"]["confidence"] == "low")
        _gzf = get_metric("util.zero_len_unmeasurable", mt.CAL_CLASS, "7d")
        t("gm-zerolen-flag-true", _gzf["value"] is True and _gzf["not_measurable"] is False)
    finally:
        _app.load_scoreboard_one = _orig_s1
    # rec is None ⇒ 誠實留白（逐字同 station_overview 那一句）
    _app.load_scoreboard_one = lambda *a, **k: None
    try:
        _gn = get_metric("util.fleet_util_24h", mt.CAL_CLASS, "7d")
        t("gm-no-activity", _gn["value"] is None
          and _gn["unavailable_reason"] == "no activity in window (honest blank, not zero)")
    finally:
        _app.load_scoreboard_one = _orig_s1
    # 資料側例外 ⇒ 穩定分類碼（**絕不夾 str(e)**、絕不吐 traceback）
    def _boom(*a, **k):
        raise RuntimeError("HYT00 mes-sql.example.com MES_DB exploded")

    _orig_qg = _app.load_qlik_grid
    _app.load_qlik_grid = _boom
    try:
        _ge = get_metric("station.quality", mt.CAL_CLASS, "7d")
        t("gm-err-category", _ge["unavailable_reason"] == "timeout" and _ge["value"] is None)
        t("gm-err-no-infra-leak",
          "mes-sql" not in _json.dumps(_ge, default=str) and "PLANT1" not in _json.dumps(_ge, default=str))
    finally:
        _app.load_qlik_grid = _orig_qg

    if _fx_ok("station_variables／wo_serials_status", "board.station_vars.v1.json", "board.wo_serials.v1.json"):
        # ---- A2 `station_variables`（記錄級字典；demo fixture、零 MES）-----------------------
        sv = station_variables("segDiePlacement")
        t("sv-found", sv["found"] is True and sv["n_vars"] > 0 and sv["source"] == "demo")
        t("sv-keys", set(sv) - {"provenance"} == set(_MH_MOD.STATION_VARS_KEYS) | {"source"})
        t("sv-groups-closed",
          {g["class_id"] for g in sv["groups"]} <= set(_MH_MOD.PDD_CLASS_IDS))
        t("sv-group-sum", sum(g["n"] for g in sv["groups"]) == sv["n_vars"])
        t("sv-census-closed", set(sv["uom_census"]) <= set(_MH_MOD.PDD_UOMS))
        t("sv-uom-roundtrip",
          _json.loads(_json.dumps(sv["uom_census"], ensure_ascii=False)) == sv["uom_census"])
        t("sv-prov-no-window", sv["provenance"]["window"] == {"from": None, "to": None})
        t("sv-prov-page-none", sv["provenance"]["page"] is None)
        t("sv-prov-no-metric-ids", sv["provenance"]["metric_ids"] == [])
        t("sv-prov-queries", sv["provenance"]["queries"]
          == ["mes_hermes.SQL_CLASS_EXISTS", "mes_hermes.SQL_CLASS_VARS"])
        t("sv-prov-freshness", sv["provenance"]["freshness"] == "fixture")   # 不是 static
        t("sv-validator", _MH_MOD.ok_station_vars(sv))
        sv0 = station_variables("segChassisAttach")
        t("sv-zero-vars", sv0["found"] is True and sv0["n_vars"] == 0)
        t("sv-zero-conf-low", sv0["provenance"]["confidence"] == "low")      # row_count 0
        svm = station_variables("segFlexBonding")                             # fixture 沒有這一筆
        t("sv-demo-miss", svm["found"] is False
          and svm["unavailable_reason"] == "not_in_demo_fixture"
          and svm.get("reason") is None)                                     # ≠ class_not_in_mes
        t("sv-reserved-refused", station_variables("segMESReserved")["error"] == "excluded_class")
        t("sv-missing-cls", station_variables("")["error"] == "missing_cls")
        t("sv-bad-cls", station_variables("seg Die%")["error"] == "bad_cls")
        t("sv-err-prov", all(station_variables(x)["provenance"]["confidence"] == "low"
                             for x in ("", "seg Die%", "segMESReserved")))
        t("sv-err-shape", all({"error", "hint"} <= set(station_variables(x))
                              for x in ("", "seg Die%", "segMESReserved")))

        # ---- A2 `wo_serials_status`（記錄級事實；demo fixture、零 MES）---------------------
        ws = wo_serials_status("WO910001/1")
        t("ws-found", ws["found"] is True and ws["serial_count"] == len(ws["serials"]))
        t("ws-keys", set(ws) - {"provenance"} == set(_MH_MOD.WO_SERIALS_KEYS) | {"source"})
        t("ws-validator", _MH_MOD.ok_wo_serials(ws))
        t("ws-caveats-nine", len(ws["caveats"]) == 9)
        _wsb = _json.dumps(ws, ensure_ascii=False, default=str)
        for _bad in _MH_MOD.FORBIDDEN_OPEN_KEYS:               # ① 未結段語意零出現
            t(f"ws-no-{_bad}", f'"{_bad}"' not in _wsb)
        t("ws-qty-parallel", ws["qty_requested"] is not None and ws["serial_count_note"])
        t("ws-rework", ws["n_rework_instances"] == ws["serial_count"] - ws["n_serial_lot_dst"])
        t("ws-diag-nine", all(k in ws["diag"] for k in
                              ("since_used", "serial_cap_hit", "segment_cap_hit", "event_cap_hit")))
        t("ws-prov-window", ws["provenance"]["window"]["from"] is not None
          and ws["provenance"]["window"]["to"] == ws["provenance"]["fetched_at"])
        t("ws-prov-page-none", ws["provenance"]["page"] is None)
        t("ws-prov-no-metric-ids", ws["provenance"]["metric_ids"] == [])
        t("ws-prov-row-count", ws["provenance"]["row_count"] == ws["serial_count"])
        t("ws-by-sap", wo_serials_status("9000100001")["found"] is True)     # 反向：SAP 單號
        ws_old = wo_serials_status("WO910003/5")
        t("ws-old-batch-stamp", ws_old["diag"]["assignment_time_usable"] is False
          and all(s["assigned_at"] is None for s in ws_old["serials"]))
        t("ws-old-no-seg-bucket",
          any(b["station"] == _MH_MOD.NO_SEGMENT_BUCKET for b in ws_old["last_station_distribution"]))
        wsm = wo_serials_status("WO900000/9")
        t("ws-demo-miss", wsm["found"] is False
          and wsm["unavailable_reason"] == "not_in_demo_fixture"
          and wsm.get("reason") is None)                       # ≠ no_production_request_matched
        for _bad in ("", "WO910001/1%", "WO 910001", "WO910001'"):
            _o = wo_serials_status(_bad)
            t(f"ws-bad-{_bad[:6] or 'empty'}",
              _o["error"] in ("missing_wo_id", "bad_wo_id") and {"error", "hint"} <= set(_o))
            t(f"ws-bad-{_bad[:6] or 'empty'}-prov", _o["provenance"]["confidence"] == "low")

    # ---- ★ live 連線炸掉 ⇒ `diag.error_detail` 要說得出 MES 說了什麼（2026-09-21 教訓）----
    # 真路徑＝`board_data.get_*` 接住例外（它恆不 raise），所以這裡 monkeypatch 連線、
    # 走 `BOARD_SOURCE=live` 打整條，**不是**直接呼叫某一層的內部函式。
    import board_data as _wd_probe

    _MSG = "('42S02', \"[42S02][SQL Server]Invalid object name '#board_pr'. (208)\")"

    class _ProbeProgrammingError(Exception):
        pass

    def _boom(*_a, **_k):
        raise _ProbeProgrammingError(_MSG)

    _old_open, _old_src = _MH_MOD.open_connection, os.environ.get("BOARD_SOURCE")
    _MH_MOD.open_connection = _boom
    os.environ["BOARD_SOURCE"] = "live"
    try:
        _ws_err = wo_serials_status("WO910001/1")
        _sv_err = station_variables("segDiePlacement")
    finally:
        _MH_MOD.open_connection = _old_open
        if _old_src is None:
            os.environ.pop("BOARD_SOURCE", None)
        else:
            os.environ["BOARD_SOURCE"] = _old_src
    t("ws-live-err-reason", _ws_err["found"] is False
      and _ws_err["unavailable_reason"] == "error:_ProbeProgrammingError")
    t("★ ws-live-err-detail", "42S02" in (_ws_err["diag"].get("error_detail") or "")
      and "#board_pr" in (_ws_err["diag"].get("error_detail") or ""))
    t("ws-live-err-keys", set(_ws_err) - {"provenance"}
      == set(_MH_MOD.WO_SERIALS_KEYS) | {"source"})   # 降級形的鍵集合不因 detail 改變
    t("★ sv-live-err-detail", "42S02" in (_sv_err["diag"].get("error_detail") or ""))
    t("sv-live-err-keys", set(_sv_err) - {"provenance"}
      == set(_MH_MOD.STATION_VARS_KEYS) | {"source"})
    # 反面：demo 路徑的 `diag` 一個位元都不多（detail 只在降級路徑出現）
    t("ws-demo-no-error-detail", "error_detail" not in (wo_serials_status("WO910001/1")["diag"] or {}))

    if _fx_ok("wo_serials_status slimming", "board.station_vars.v1.json", "board.wo_serials.v1.json"):
        # ---- #181 `wo_serials_status` 瘦身（cmms NOTE-83；`SPEC-HERMES-TRUST.md` §12.17）------------
        import pydantic_core as _pc
        _WS_ORDER = [k for k in _MH_MOD.WO_SERIALS_KEYS] + ["source", "provenance"]
        # ① 預設呼叫＝鍵與值同現行：鍵集合、無 serials_view、params 只有 wo_id（evidence_ref 同配方）
        t("ws181-default-no-view", _WS_VIEW_KEY not in ws and set(ws) == set(_WS_ORDER))
        _fa = ws["provenance"]["fetched_at"]
        t("ws181-default-evidence-ref-params-wo-id-only",
          ws["provenance"]["evidence_ref"].endswith(
              _hash8(f"wo_serials_status|{_params_blob({'wo_id': 'WO910001/1'})}|{_fa}")))
        _cj, _ij = _compact_json(ws), _pc.to_json(ws, fallback=str, indent=2).decode()
        t("ws181-compact-same-keys-values-order",      # 解回來逐鍵逐值逐序相同＝只差空白
          _json.dumps(_json.loads(_cj), ensure_ascii=False)
          == _json.dumps(_json.loads(_ij), ensure_ascii=False))
        t("ws181-compact-no-indent", "\n" not in _cj and len(_cj) < len(_ij))
        t("ws181-compact-non-ascii-verbatim", "\\u" not in _cj and "已完成" in _cj)   # 中文不轉義（同 FastMCP）
        t("ws181-mcp-shell-returns-compact",
          _json.loads(_wo_serials_status_mcp("WO910001/1"))["serial_count"] == ws["serial_count"]
          and "\n" not in _wo_serials_status_mcp("WO910001/1"))
        # ② status 篩選（demo 12 支全 WIP＝status_id 12）
        _wsw = wo_serials_status("WO910001/1", status="WIP")
        t("ws181-status-wip-all", len(_wsw["serials"]) == ws["serial_count"]
          and _wsw[_WS_VIEW_KEY]["n_matched"] == ws["serial_count"]
          and _wsw[_WS_VIEW_KEY]["status_filter"] == {"status_id": 12, "status_code": "WIP"}
          and _wsw[_WS_VIEW_KEY]["truncated"] is False)
        t("ws181-status-aliases", all(
            wo_serials_status("WO910001/1", status=x)[_WS_VIEW_KEY]["status_filter"]["status_id"] == 12
            for x in ("wip", " WIP ", "12", 12)))
        t("ws181-status-multiword", wo_serials_status("WO910001/1", status="  in   PROCESS ")
          [_WS_VIEW_KEY]["status_filter"] == {"status_id": 6, "status_code": "In Process"})
        _wss = wo_serials_status("WO910001/1", status="Scrapped")
        t("ws181-status-zero-match-honest", _wss["serials"] == [] and _wss[_WS_VIEW_KEY]["n_matched"] == 0
          and _wss["serial_count"] == ws["serial_count"]              # 頭仍是全量
          and _wss["provenance"]["confidence"] == ws["provenance"]["confidence"]   # 0 支≠降級
          and "真的 0 支" in _wss[_WS_VIEW_KEY]["note"])
        t("ws181-status-out-of-enum-int", wo_serials_status("WO910001/1", status=99)
          [_WS_VIEW_KEY]["status_filter"] == {"status_id": 99, "status_code": None})
        for _bs in ("scrap", "WIP;", True, 1.5):
            _o = wo_serials_status("WO910001/1", status=_bs)
            t(f"ws181-bad-status-{_bs!r}", _o.get("error") == "bad_status" and "Scrapped" in _o["hint"]
              and _o["provenance"]["confidence"] == "low" and _o["provenance"]["row_count"] == 0)
        _wse = wo_serials_status("WO910001/1", status="")
        t("ws181-blank-status-no-filter", _WS_VIEW_KEY not in _wse
          and len(_wse["serials"]) == ws["serial_count"])
        t("ws181-bad-wo-id-still-first",               # 工單號錯誤優先（舊行為）；參數壞不查 MES
          wo_serials_status("WO910001/1%", status="scrap").get("error") == "bad_wo_id")
        # ③ summary_only：serials 鍵缺席、鳥瞰全量、status_counts 加總＝serial_count
        _wsum = wo_serials_status("WO910001/1", summary_only=True)
        t("ws181-summary-no-serials-key", "serials" not in _wsum and _wsum[_WS_VIEW_KEY]["serials_omitted"])
        t("ws181-summary-header-full", all(_wsum[k] == ws[k] for k in (
            "work_order", "serial_count", "qty_requested", "n_serial_lot_dst", "n_rework_instances",
            "last_station_distribution", "caveats", "found", "wo_id")))
        t("ws181-summary-status-counts", sum(c["n_serial"] for c in _wsum[_WS_VIEW_KEY]["status_counts"])
          == ws["serial_count"])
        t("ws181-summary-view-where-serials-was",
          list(_wsum).index(_WS_VIEW_KEY) == list(ws).index("serials"))
        t("ws181-summary-params-recorded",             # 非預設參數才進 params（evidence_ref 可分辨）
          _wsum["provenance"]["evidence_ref"].endswith(_hash8(
              f"wo_serials_status|{_params_blob({'wo_id': 'WO910001/1', 'summary_only': True})}"
              f"|{_wsum['provenance']['fetched_at']}")))
        # ④ limit：先篩後截、序＝全量序、自揭 n_total／n_returned／truncated
        _wsl = wo_serials_status("WO910001/1", limit=5)
        _v = _wsl[_WS_VIEW_KEY]
        t("ws181-limit-truncates", _wsl["serials"] == ws["serials"][:5] and _v["n_returned"] == 5
          and _v["n_total"] == ws["serial_count"] and _v["truncated"] is True and _v["limit_applied"]
          and f"只回前 5 支" in _v["note"] and f"共 {ws['serial_count']} 支" in _v["note"])
        t("ws181-limit-view-before-serials", list(_wsl).index(_WS_VIEW_KEY) < list(_wsl).index("serials"))
        _wsl2 = wo_serials_status("WO910001/1", limit=1000)
        t("ws181-limit-big-not-truncated", _wsl2[_WS_VIEW_KEY]["truncated"] is False
          and len(_wsl2["serials"]) == ws["serial_count"])
        for _bl in (0, -1, True, "5", 2.0):
            _o = wo_serials_status("WO910001/1", limit=_bl)
            t(f"ws181-bad-limit-{_bl!r}", _o.get("error") == "bad_limit"
              and _o["provenance"]["confidence"] == "low")
        t("ws181-bad-summary-only", wo_serials_status("WO910001/1", summary_only="yes").get("error")
          == "bad_summary_only")
        t("ws181-demo-miss-no-view",                   # 查不到＝原樣回（serials: [] 形），不掛視圖
          _WS_VIEW_KEY not in wo_serials_status("WO900000/9", summary_only=True)
          and wo_serials_status("WO900000/9", summary_only=True)["unavailable_reason"] == "not_in_demo_fixture")
        t("ws181-fixture-not-mutated", "serials" in wo_serials_status("WO910001/1")
          and _WS_VIEW_KEY not in wo_serials_status("WO910001/1")
          and len(wo_serials_status("WO910001/1")["serials"]) == ws["serial_count"])
        # ⑤ 組合（合成 69 支：3 Scrapped／5 Hold／61 WIP；cmms NOTE-83 的量級）
        _syn_serials = []
        for _i in range(69):
            _s = dict(ws["serials"][_i % len(ws["serials"])])
            _s["lot"] = f"B0SYN{_i:03d}"
            _sid = 10 if _i in (7, 30, 61) else (5 if _i in (2, 11, 20, 44, 50) else 12)
            _s["status_id"], _s["status_code"] = _sid, _MH_MOD.MATERIAL_STATUS[_sid]
            _syn_serials.append(_s)
        _syn = dict(ws, serials=_syn_serials, serial_count=69, n_serial_lot_dst=69, n_rework_instances=0)
        _c1 = _ws_apply_view(_syn, status_id=10, summary_only=False, limit=2)
        t("ws181-combo-status-then-limit", [s["lot"] for s in _c1["serials"]] == ["B0SYN007", "B0SYN030"]
          and _c1[_WS_VIEW_KEY]["n_matched"] == 3 and _c1[_WS_VIEW_KEY]["n_returned"] == 2
          and _c1[_WS_VIEW_KEY]["truncated"] is True and "status=Scrapped 符合 3 支" in _c1[_WS_VIEW_KEY]["note"])
        _c2 = _ws_apply_view(_syn, status_id=10, summary_only=True, limit=2)
        t("ws181-combo-summary-status-counts-limit-ignored", "serials" not in _c2
          and _c2[_WS_VIEW_KEY]["n_matched"] == 3 and _c2[_WS_VIEW_KEY]["n_returned"] == 0
          and _c2[_WS_VIEW_KEY]["limit_applied"] is False and _c2[_WS_VIEW_KEY]["truncated"] is False)
        t("ws181-combo-status-counts-order",
          [(c["status_code"], c["n_serial"]) for c in _c2[_WS_VIEW_KEY]["status_counts"]]
          == [("WIP", 61), ("Hold", 5), ("Scrapped", 3)])
        t("ws181-combo-header-untouched", _c1["serial_count"] == 69
          and _c1["last_station_distribution"] == _syn["last_station_distribution"])
        t("ws181-combo-input-not-mutated", len(_syn["serials"]) == 69 and _WS_VIEW_KEY not in _syn)
        # ⑥ codex 上限實效（MCP 回傳固定截 10,000 token、頭尾各 5,000）。token 粗估＝CJK 字數＋
        #   其餘字元 /3.6（本 repo CLAUDE.md 的量法；JSON 標點密、實際 chars/token 可能低到 ~3.1 ⇒
        #   門檻留 ≥20% 餘裕）。**斷言寫死字元數**，換算依據即此註。
        def _tok(s):
            c = sum(1 for x in s if "一" <= x <= "鿿")
            return int(c + (len(s) - c) / 3.6)
        _L_ind = len(_pc.to_json(_syn, fallback=str, indent=2).decode())
        _L_full = len(_compact_json(_syn))
        _L_sum = len(_compact_json(_ws_apply_view(_syn, status_id=None, summary_only=True, limit=None)))
        _L_lim = len(_compact_json(_ws_apply_view(_syn, status_id=None, summary_only=False, limit=20)))
        _L_scr = len(_compact_json(_ws_apply_view(_syn, status_id=10, summary_only=False, limit=None)))
        t("ws181-69-compact-saves", _L_full < _L_ind * 0.85)
        t("ws181-69-summary-under-8k-chars", _L_sum < 8000
          and _tok(_compact_json(_ws_apply_view(_syn, status_id=None, summary_only=True, limit=None))) < 5000)
        t("ws181-69-limit20-under-20k-chars", _L_lim < 20000
          and _tok(_compact_json(_ws_apply_view(_syn, status_id=None, summary_only=False, limit=20))) < 8000)
        t("ws181-69-status-scrapped-under-8k-chars", _L_scr < 8000)

    if _fx_ok("mes_material／pack-2 tools", *_MES_FX_FILES):
        # ---- 包① 材料／批號／出貨四支（`mes_material`；demo fixture、零 MES）------------------
        _MM = _mes_material()
        t("mm-module", _MM is not None)
        t("mm-queries-match-module", all(_TOOL_QUERIES[tl] == _MM.TOOL_QUERY_NAMES[tl]
                                         for tl in _MM.TOOLS))
        t("mm-metric-ids-empty", all(_TOOL_METRIC_IDS[tl] == () for tl in _MM.TOOLS))
        sg = serial_genealogy("J0SYN02")
        t("sg-found", sg["found"] is True and sg["source"] == "demo")
        t("sg-validator", _MM.ok_payload("serial_genealogy", sg))
        t("sg-keys", set(sg) - {"provenance", "source"} == set(_MM.SERIAL_KEYS))
        t("sg-stages", [e["stage"] for e in sg["entities"]]
          == ["module", "unit", "packaged_unit"])
        t("sg-lots", sg["n_material_lots"] == 8 and sg["truncated"] is True)
        t("sg-prov", sg["provenance"]["page"] is None and sg["provenance"]["metric_ids"] == []
          and sg["provenance"]["caliber"] == _CALIBER_RECORD
          and sg["provenance"]["confidence"] == "low"          # truncated ⇒ degraded ⇒ low
          and sg["provenance"]["window"]["from"] == "2000-01-01 00:00:00"
          and sg["provenance"]["row_count"] == len(sg["materials"]))
        t("sg-queries", sg["provenance"]["queries"] == list(_MM.TOOL_QUERY_NAMES["serial_genealogy"]))
        t("sg-case-insensitive-demo", serial_genealogy("j0syn02")["found"] is True)
        _sgm = serial_genealogy("J0SYN01")
        t("sg-demo-miss", _sgm["found"] is False and _sgm["unavailable_reason"] == "not_in_demo_fixture"
          and _sgm["reason"] is None and set(_sgm) - {"provenance", "source"} == set(_MM.SERIAL_KEYS))
        for _bad in ("", "B0%", "x'--", "B0 2"):
            _o = serial_genealogy(_bad)
            t(f"sg-bad-{_bad[:4] or 'empty'}", _o["error"] in ("missing_serial", "bad_serial")
              and _o["provenance"]["confidence"] == "low")
        wm = wo_materials("WO910001/1")
        t("wm-found", wm["found"] and _MM.ok_payload("wo_materials", wm))
        t("wm-trunc-lower-bound", wm["truncated"] is True
          and any("下界" in c for c in wm["caveats"]))
        t("wm-by-sap", wo_materials("9000100001")["found"] is True)
        t("wm-2019-null-line", wo_materials("WO910003/5")["work_order"]["product_line"] is None)
        t("wm-bad", wo_materials("WO910001/1%")["error"] == "bad_wo_id")
        t("wm-demo-miss", wo_materials("WO900000/9")["unavailable_reason"] == "not_in_demo_fixture")
        lw = lot_where_used("LA-9100001-9001")
        t("lw-found", lw["found"] and _MM.ok_payload("lot_where_used", lw))
        t("lw-supplier", lw["supplier"]["received"][0]["supplier_lot"] == "20260811"
          and lw["supplier"]["supplier_names"] == ["寬侑"])
        lw_old = lot_where_used("LT910001/1")
        t("lw-old-not-measurable", lw_old["not_measurable"][0]["reason"]
          == "receipt_record_not_retained" and lw_old["supplier"]["received"] == [])
        t("lw-no-comment-field", "comment" not in _json.dumps(lw, ensure_ascii=False).lower())
        shp = shipment_lookup(lot="V0SYN01")
        t("sh-lot", shp["found"] and shp["mode"] == "lot" and shp["n_shipments"] == 1
          and _MM.ok_payload("shipment_lookup", shp))
        shw = shipment_lookup()
        t("sh-window", shw["mode"] == "window" and shw["truncated"] is True
          and shw["provenance"]["window"]["from"] is not None)
        shwo = shipment_lookup(wo_id="WO910001/1")
        t("sh-wo-unverified", shwo["found"] is False and shwo["reason"] == "no_shipment_record"
          and any("尚未驗證" in c for c in shwo["caveats"]))
        t("sh-days-refused", shipment_lookup(days=90)["error"] == "bad_days")
        t("sh-lot-first", shipment_lookup(lot="V0SYN01", wo_id="WO910001/1")["mode"] == "lot")
        # live 炸掉 ⇒ `diag.error_detail` 說得出 MES 說了什麼（同 A2 的教訓；`board_data` 接住）
        _old_mm_open, _old_src2 = _MM.open_connection, os.environ.get("BOARD_SOURCE")
        _MM.open_connection = _boom
        os.environ["BOARD_SOURCE"] = "live"
        try:
            _sg_err = serial_genealogy("J0SYN01")
            _sh_err = shipment_lookup(lot="V0SYN01")
        finally:
            _MM.open_connection = _old_mm_open
            if _old_src2 is None:
                os.environ.pop("BOARD_SOURCE", None)
            else:
                os.environ["BOARD_SOURCE"] = _old_src2
        t("★ sg-live-err-detail", _sg_err["found"] is False
          and _sg_err["unavailable_reason"] == "error:_ProbeProgrammingError"
          and "42S02" in (_sg_err["diag"].get("error_detail") or "")
          and set(_sg_err) - {"provenance", "source"} == set(_MM.SERIAL_KEYS))
        t("sh-live-err-keys", set(_sh_err) - {"provenance", "source"} == set(_MM.SHIP_KEYS)
          and _sh_err["provenance"]["confidence"] == "low")
        import board_data as _wd_ss
        t("mm-snapshot-unsupported",
          _wd_ss.get_mes_material("wo_materials", "snapshot", "WO910001/1")["unavailable_reason"]
          == "snapshot_unsupported")

        # ---- MES 工具包② 九支（`mes_etest`／`mes_equipment`／`mes_segment`／`mes_route`；demo fixture、零 MES）----
        _P2 = {n: _pack2_mod(n) for n in ("mes_etest", "mes_equipment", "mes_segment", "mes_route")}
        t("p2-modules", all(m is not None for m in _P2.values()))
        t("p2-tool-set", set(_PACK2_TOOLS) == {tl for m in _P2.values() for tl in m.TOOLS})
        t("p2-queries-match-module", all(_TOOL_QUERIES[tl] == _P2[_PACK2_TOOLS[tl][0]].TOOL_QUERY_NAMES[tl]
                                         for tl in _PACK2_TOOLS))
        t("p2-metric-ids-empty", all(_TOOL_METRIC_IDS[tl] == () for tl in _PACK2_TOOLS))
        _p2_demo = {
            "station_failure_pareto": station_failure_pareto("CAL"),
            "module_die_etest": module_die_etest("J0SYN09"),
            "equipment_status_events": equipment_status_events("7905"),
            "equipment_modules": equipment_modules("eid-07905"),
            "equipment_calibration": equipment_calibration("EID-07905", 365),
            "segment_data": segment_data("J0SYN17", "CLN"),
            "station_spec_limits": station_spec_limits("segCleanTest"),
            "station_retest": station_retest("CLN"),
            "wo_route": wo_route("WO910002/1"),
        }
        for _tl, _o in _p2_demo.items():
            _m = _P2[_PACK2_TOOLS[_tl][0]]
            t(f"p2-{_tl}-found", _o.get("found") is True and _o.get("source") == "demo")
            t(f"p2-{_tl}-validator", _m.ok_payload(_tl, _o) and "payload_shape_ok" not in _o["diag"])
            t(f"p2-{_tl}-prov", _o["provenance"]["tool"] == _tl
              and _o["provenance"]["caliber"] == _CALIBER_RECORD and _o["provenance"]["page"] is None
              and _o["provenance"]["metric_ids"] == []
              and _o["provenance"]["queries"] == list(_m.TOOL_QUERY_NAMES[_tl])
              and _o["provenance"]["freshness"] == "fixture")
        # confidence：truncated ⇒ low；完整 ⇒ high
        t("p2-pareto-high", _p2_demo["station_failure_pareto"]["provenance"]["confidence"] == "high"
          and _p2_demo["station_failure_pareto"]["provenance"]["window"]["from"] is not None)
        t("p2-limits-trunc-low", _p2_demo["station_spec_limits"]["truncated"] is True
          and _p2_demo["station_spec_limits"]["provenance"]["confidence"] == "low")
        t("p2-retest-trunc-low", _p2_demo["station_retest"]["truncated"] is True
          and _p2_demo["station_retest"]["provenance"]["confidence"] == "low")
        t("p2-route-window-since", _p2_demo["wo_route"]["provenance"]["window"]["from"]
          == "2000-01-01 00:00:00")
        t("p2-route-unresolved-listed", len(_p2_demo["wo_route"]["not_measurable"]) == 7
          and {x["reason"] for x in _p2_demo["wo_route"]["not_measurable"]}
          == {"route_version_unresolved"})
        t("p2-etest-11-dies", _p2_demo["module_die_etest"]["die_level"]["n_dies"] == 11)
        t("p2-ev-no-duration", all(e["duration_s"] is None
                                   for e in _p2_demo["equipment_status_events"]["events"]))
        # ★ 唯一放行：spec_limits found=true ＋ no_pass_criteria_limits（validator 過、不標 shape 壞）
        _sl0 = station_spec_limits("segLamination")
        t("★ p2-limits-found-unavailable-ok", _sl0["found"] is True
          and _sl0["unavailable_reason"] == "no_pass_criteria_limits" and _sl0["n_limit_rows"] == 0
          and _P2["mes_segment"].ok_payload("station_spec_limits", _sl0)
          and "payload_shape_ok" not in _sl0["diag"]
          and _sl0["provenance"]["confidence"] == "low")     # row_count=0 ⇒ low（照 caveat 說「MES 沒有限值」）
        # 不支援站：零 SQL、not_measurable
        _fpu = station_failure_pareto("segDiePlacement")
        t("p2-pareto-unsupported", _fpu["found"] is False
          and _fpu["not_measurable"][0]["reason"] == "station_unsupported"
          and _fpu["provenance"]["confidence"] == "low")
        t("p2-pareto-oqc-alias", station_failure_pareto("oqc")["station"]
          == "segFinalOQC")
        # degraded：查無（demo＝not_in_demo_fixture，**不是** MES 裡沒有）
        for _tl, _o in (("module_die_etest", module_die_etest("J0SYN19")),
                        ("equipment_status_events", equipment_status_events("EID-99999")),
                        ("equipment_modules", equipment_modules("EID-99999")),
                        ("equipment_calibration", equipment_calibration("EID-99999")),
                        ("segment_data", segment_data("J0SYN06", "CLN")),
                        ("station_spec_limits", station_spec_limits("CAL")),
                        ("station_retest", station_retest("CAL")),
                        ("wo_route", wo_route("WO900000/1")),
                        ("wo_route", wo_route("WO910001"))):          # 缺 /n ⇒ 逐字等號查無
            t(f"p2-{_tl}-demo-miss", _o["found"] is False
              and _o["unavailable_reason"] == "not_in_demo_fixture"
              and _o["provenance"]["confidence"] == "low"
              and _P2[_PACK2_TOOLS[_tl][0]].ok_payload(_tl, _o))
        # 零訊號機（35535）：found=true、n=0 ⇒ low、reason 在閉集
        _z = equipment_status_events("EID-35535")
        t("p2-ev-zero-signal", _z["found"] is True and _z["reason"] == "no_events_in_window"
          and _z["provenance"]["confidence"] == "low")
        # 參數錯誤（400 形；不 clamp）
        for _lbl, _o, _e in (("pareto-days", station_failure_pareto("CAL", 90), "bad_days"),
                             ("pareto-bad-station", station_failure_pareto("x y"), "bad_station"),
                             ("etest-bad-serial", module_die_etest("B0%"), "bad_serial"),
                             ("ev-bad-eid", equipment_status_events("abc"), "bad_eid"),
                             ("ev-days", equipment_status_events("07905", 999), "bad_days"),
                             ("mod-missing", equipment_modules(""), "missing_eid"),
                             ("sd-which", segment_data("J0SYN17", "CLN", "middle"), "bad_which"),
                             ("sd-station", segment_data("J0SYN17", "segNoSuch"),
                              "station_not_in_whitelist"),
                             ("sl-recipe", station_spec_limits("CLN", "PR 1;--"), "bad_recipe"),
                             ("rt-days", station_retest("CLN", 0), "bad_days"),
                             ("route-bad", wo_route("WO910001/1%"), "bad_wo_id")):
            t(f"p2-arg-{_lbl}", _o.get("error") == _e and _o["provenance"]["confidence"] == "low"
              and _o["provenance"]["row_count"] == 0)
        # live 炸掉 ⇒ 每個模組都說得出 MES 說了什麼（`board_data` 接住、鍵集合不變）
        _p2_old = {n: m.open_connection for n, m in _P2.items()}
        _old_src3 = os.environ.get("BOARD_SOURCE")
        for _m in _P2.values():
            _m.open_connection = _boom
        os.environ["BOARD_SOURCE"] = "live"
        try:
            _p2_err = {"station_failure_pareto": station_failure_pareto("CAL"),
                       "equipment_modules": equipment_modules("EID-07905"),
                       "station_retest": station_retest("CLN"),
                       "wo_route": wo_route("WO910002/1")}
        finally:
            for _n, _f in _p2_old.items():
                _P2[_n].open_connection = _f
            if _old_src3 is None:
                os.environ.pop("BOARD_SOURCE", None)
            else:
                os.environ["BOARD_SOURCE"] = _old_src3
        for _tl, _o in _p2_err.items():
            _m = _P2[_PACK2_TOOLS[_tl][0]]
            t(f"★ p2-{_tl}-live-err-detail", _o["found"] is False
              and _o["unavailable_reason"] == "error:_ProbeProgrammingError"
              and "42S02" in (_o["diag"].get("error_detail") or "")
              and set(_o) - {"provenance", "source"} == set(_m.KEYS_BY_TOOL[_tl])
              and _o["provenance"]["confidence"] == "low" and _o["provenance"]["freshness"] == "live")
        t("p2-snapshot-unsupported",
          _wd_ss.get_mes_segment("station_retest", "snapshot", {"cls": "segCleanTest", "days": 7})
          ["unavailable_reason"] == "snapshot_unsupported")
        # ★ 回歸釘（整合包 2026-09-24）：`board_data` 的 lazy-import 快取全域曾撞名（`_MQ`＝mes_qlik vs
        #   mes_equipment、`_MR`＝mes_rework vs mes_route）⇒ 先叫到的一方贏、另一方拿到錯的模組。
        _qm, _rwm = _wd_ss.qlik_module(), _wd_ss.rework_module()
        t("★ p2-wd-cache-no-collision", _wd_ss.mes_equipment_module().__name__ == "mes_equipment"
          and _wd_ss.mes_route_module().__name__ == "mes_route"
          and (_qm is None or _qm.__name__ == "mes_qlik")
          and (_rwm is None or _rwm.__name__ == "mes_rework"))

        # bearer 閘（ASGI 純測：未設 token→503、錯 token→401）
        import asyncio

        async def _probe(headers, env_token):
            old = os.environ.pop("BOARD_MCP_TOKEN", None)
            if env_token is not None:
                os.environ["BOARD_MCP_TOKEN"] = env_token
            try:
                proxy = McpTransportProxy()
                status = {}

                async def recv():
                    return {"type": "http.request"}

                async def send(msg):
                    if msg["type"] == "http.response.start":
                        status["code"] = msg["status"]

                scope = {"type": "http", "method": "GET", "path": "/mcp",
                         "headers": headers, "query_string": b""}
                await proxy(scope, recv, send)
                return status["code"]
            finally:
                os.environ.pop("BOARD_MCP_TOKEN", None)
                if old is not None:
                    os.environ["BOARD_MCP_TOKEN"] = old

        t("gate-no-token-503", asyncio.run(_probe([], None)) == 503)
        t("gate-wrong-401", asyncio.run(_probe([(b"authorization", b"Bearer nope")], "right")) == 401)
        t("gate-missing-401", asyncio.run(_probe([], "right")) == 401)
        # 對 token 但 lifespan 未運行 → 503（transport not running；閘本身已放行）
        t("gate-right-but-idle-503",
          asyncio.run(_probe([(b"authorization", b"Bearer right")], "right")) == 503)

    # ---- ☐2 (a) turn 綁定：header 解析／contextvar／單一落列點（§12.12）-------------------
    t("turn-default-none", Board_TURN.get() is None)          # 沒人設 ⇒ 預設就是「沒有 turn」
    t("turn-hdr-ok", turn_from_headers([(b"x-board-turn", b"42")]) == 42)
    t("turn-hdr-mixed-case", turn_from_headers([(b"X-Board-Turn", b"42")]) == 42)
    t("turn-hdr-spaces", turn_from_headers([(b"x-board-turn", b"  7 ")]) == 7)
    t("turn-hdr-among-others",
      turn_from_headers([(b"authorization", b"Bearer x"), (b"x-board-turn", b"9"),
                         (b"accept", b"*/*")]) == 9)
    for _bad in (b"abc", b"", b"-1", b"1.5", b"1" * 19, b"12 34"):
        t(f"turn-hdr-bad-{_bad[:6]!r}", turn_from_headers([(b"x-board-turn", _bad)]) is None)
    t("turn-hdr-missing", turn_from_headers([(b"authorization", b"Bearer x")]) is None)
    t("turn-hdr-empty-list", turn_from_headers([]) is None and turn_from_headers(None) is None)
    t("turn-hdr-18-digits", turn_from_headers([(b"x-board-turn", b"1" * 18)]) == int("1" * 18))

    # `params_hash` 與 `evidence_ref` 的 hash8 ＝**同一份配方的兩種取用**（見 params_hash docstring）
    t("ph-stable", params_hash({"days": 3}) == params_hash({"days": 3}))
    t("ph-key-order-irrelevant", params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1}))
    t("ph-differs", params_hash({"days": 3}) != params_hash({"days": 7}))
    t("ph-8-hex", _re.match(r"^[0-9a-f]{8}$", params_hash({"days": 3})) is not None)
    _ph_env = _envelope("fleet_scoreboard", metric_ids=(), queries=(), window=_NO_WINDOW,
                        fetched_at="2026-09-22 10:00:00", freshness="fixture", row_count=1,
                        caliber=_CALIBER_UTIL_AXIS, confidence="high", page=None,
                        params={"days": 3})
    t("★ ph-same-recipe",                       # 信封那顆＝同一份 normalisation＋同一個原語
      _ph_env["evidence_ref"].rsplit(":", 1)[1]
      == _hash8(f"fleet_scoreboard|{_params_blob({'days': 3})}|2026-09-22 10:00:00"))
    t("★ ph-not-the-same-number",               # 但**不是**同一個數字（信封多吃 tool＋時刻）
      _ph_env["evidence_ref"].rsplit(":", 1)[1] != params_hash({"days": 3}))

    # 落列：有 turn ⇒ 恰一列；沒 turn ⇒ 零列；**工具回傳的鍵集合一個位元不動**
    import tempfile as _tf

    _PING_KEYS_TODAY = {"status", "app", "source", "provenance"}
    # 加了寫入 hook 之後，ping 的鍵集合照舊（C-HT9：既有欄位一個位元不動）
    t("★ tc-ping-keys-unchanged", set(ping()) == _PING_KEYS_TODAY)
    _tc_dir = _tf.mkdtemp(prefix="board_mcp_tc_")
    _tc_db = os.path.join(_tc_dir, "tc.sqlite3")
    _tc_old = os.environ.get("BOARD_ASSISTANT_DB")
    os.environ["BOARD_ASSISTANT_DB"] = _tc_db
    try:
        import board_assistant as _wa_t

        _p_noturn = ping()                       # Board_TURN 未設（selftest 就是「沒有 header」那一類）
        t("★ tc-no-turn-zero-rows", _wa_t.tool_calls_for_turn(7, path=_tc_db) == []
          and set(_p_noturn) == _PING_KEYS_TODAY)

        _box = {}

        def _run_with_turn():
            Board_TURN.set(7)
            _box["ping"] = ping()
            _box["fs"] = fleet_scoreboard(days=3)

        contextvars.copy_context().run(_run_with_turn)
        t("tc-ctx-restored", Board_TURN.get() is None)   # copy_context ⇒ 外面那條 context 不受污染
        _rows_t = _wa_t.tool_calls_for_turn(7, path=_tc_db)
        t("★ tc-two-rows-in-call-order",
          [r["tool"] for r in _rows_t] == ["ping", "fleet_scoreboard"])
        # 存的信封 ＝ 工具回的那一份（逐鍵相同）
        _stored_prov = _rows_t[0]["provenance"]
        _ret_prov = _box["ping"].get("provenance")
        t("★ tc-provenance-roundtrip", _stored_prov == _ret_prov)
        t("tc-params-hash", _rows_t[1]["params_hash"] == params_hash({"days": 3}))
        t("★ tc-result-has-no-provenance", "provenance" not in _rows_t[0]["result"]
          and set(_rows_t[0]["result"]) == _PING_KEYS_TODAY - {"provenance"})
        t("tc-result-values", _rows_t[0]["result"]["status"] == "ok"
          and _rows_t[0]["result"]["source"] == "demo")
        t("tc-turn-id", all(r["turn_id"] == 7 for r in _rows_t))

        # ★ 寫入端炸掉 ⇒ 工具照樣回得出東西（觀測動作不得害死一次工具呼叫）
        _real_rec = _wa_t.tool_call_record

        def _boom(*a, **k):
            raise RuntimeError("disk on fire")

        _wa_t.tool_call_record = _boom
        try:
            _box2 = {}

            def _run_boom():
                Board_TURN.set(31)
                _box2["ping"] = ping()

            contextvars.copy_context().run(_run_boom)
        finally:
            _wa_t.tool_call_record = _real_rec
        t("★ tc-writer-failure-swallowed", set(_box2["ping"]) == _PING_KEYS_TODAY
          and _box2["ping"]["status"] == "ok")
        t("tc-writer-failure-zero-rows", _wa_t.tool_calls_for_turn(31, path=_tc_db) == [])
    finally:
        os.environ.pop("BOARD_ASSISTANT_DB", None)
        if _tc_old is not None:
            os.environ["BOARD_ASSISTANT_DB"] = _tc_old
        import shutil as _sh
        _sh.rmtree(_tc_dir, ignore_errors=True)

    if _kb_ok():
        # ---- #175 knowledge_card（讀 repo 內真卡片；零 MES、零 SQL）------------------------------
        _kc = knowledge_card("QE910002", "alarms")
        t("kc-found", _kc["found"] is True and _kc["card"]["card_id"] == "QE910002"
          and kb.ok_payload({k: v for k, v in _kc.items() if k != "provenance"}))
        t("kc-prov", _kc["provenance"]["tool"] == "knowledge_card"
          and _kc["provenance"]["caliber"] == "doc-reference" == _CALIBER_DOC
          and _kc["provenance"]["freshness"] == "static" and _kc["provenance"]["page"] is None
          and _kc["provenance"]["metric_ids"] == [])
        t("kc-caveat-mes-wins", any("MES is authoritative" in c for c in _kc["caveats"]))
        _kn = knowledge_card("weekend weather forecast")
        t("kc-no-match-honest", _kn["found"] is False
          and _kn["unavailable_reason"] == "no_matching_card"
          and _kn["provenance"]["confidence"] == "low" and _kn["card"] is None)
        _kb = knowledge_card("   ")
        t("kc-bad-query", _kb.get("error") == "bad_query" and _kb["provenance"]["confidence"] == "low")
        _old_root = os.environ.get(kb.ENV_ROOT)
        os.environ[kb.ENV_ROOT] = os.path.join(_tf.gettempdir(), "board-kb-definitely-missing-dir")
        try:
            _ku = knowledge_card("DAF")
        finally:
            os.environ.pop(kb.ENV_ROOT, None)
            if _old_root is not None:
                os.environ[kb.ENV_ROOT] = _old_root
        t("★ kc-missing-dir-degrades", _ku["found"] is False and _ku["unavailable_reason"] == "unavailable"
          and _ku["provenance"]["confidence"] == "low" and "Traceback" not in _json.dumps(_ku))
        t("kc-no-path-leak", kb.repo_root() not in _json.dumps(_kc, ensure_ascii=False))

    print(f"board_mcp selftest: {n} assertions PASS")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python board_mcp.py --selftest")
