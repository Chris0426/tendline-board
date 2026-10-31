#!/usr/bin/env python3
"""Tendline Board — MES-WIP（入站排隊顆數 → 可吃小時數）計算核心 `board.wip.v1`。

**這是什麼**：全線調度板 `/dispatch` 的 WIP 軸資料源（SPEC-DISPATCH.md §4/§5 的落地）。
回答一件事——**「站 S 前面積了多少料、按這站現在的速度可以吃幾小時？」**
**不是什麼**：不是良率（那是 `mes_qlik.py` 的 first-FAIL 口徑）、不是稼動（`mes_timeline.py`）、
不是實體盤點（SALTMARSH 盤點日後降級為對帳校準源，SPEC §13 Q-D6）。

語意權威（分歧一律以這兩份為準、不以本檔註解為準）：
  · `SPEC-DISPATCH.md` §4（API pin）／§5（口徑裁決）——本檔逐條實作。
  · `_QLIK-RECON-MAIN.md` §9.1（地雷 A–E）——lot 位置排序鍵、matStatus 現況欄語意。

一句話語意：**per-lot 取 max `stpPK` 那一列＝該 lot 的「現在在哪」**；那一列若是 PASS，
這顆料就排在「route 上的下一個非 pass-through 站」門口；FAIL 則卡在原站（不進任何佇列）。
`cover_h = queue_n / rate_uph`＝這站按目前速度吃得完排隊的小時數。

★ 鐵則（SPEC §1；違反即 HIGH）
  · **站序只有一份**＝`mes_qlik.QLIK_STATIONS`（人工審定 43 因子序）。本檔 **import 重用、
    絕不另抄一個位元**、絕不 runtime discovery（M4 教訓：自動發現會讓站靜默消失）。
  · **lot 位置排序鍵＝`stpPK`**，不是時戳、不是站序（J0SYN14：CAL 過→AutoOQC 死→回 CAL 復測，
    時序倒置、stpPK 不倒）。rework 迴圈由 max(stpPK) 自然解決。
  · **誠實**：量不到一律 `None`（消費面「—」），**絕不 0**。`rate_uph is None ⇒ cover_h is None`
    ——**絕不用別站或全線均速頂替**。
  · **人名紅線（§24 延伸，SPEC §9）**：本模組不取、不回、不記任何姓名/工號欄位。人力欄在
    v1 是佔位；探針（`_OPR-operator-probe.sql`）結果回來後才走 §9.3 的 additive 接線。

★ perf lore（B4/M4 教訓；改本檔前先讀）
  · **兩段式母體收斂**：[W1] 先收「近 `lookback_d` 天有 PASS/FAIL 判定段的 lot」→ `#temp` →
    [H1] 只掃這些 lot 的近 `h_lookback_d` 天列。**絕不全史全線裸掃、絕不對 view 塞動態 IN-list**。
    ⚠ `h_lookback_d` **恆 ≥ `lookback_d`**（`_clamp_h_lookback`；違者 clamp＋diag 自揭）——
    反過來的話，只在 (h_lookback, lookback] 有活動的 lot 一列都拉不到＝**靜默消失**。
  · 每條 SQL 尾 `OPTION (RECOMPILE)`；**絕不在 5 表大 view 上 DISTINCT/GROUP BY**
    （去重/取 max/聚合一律 Python）。
  · `COLLATE` 掛 **#temp 側**（保 view 側 seek）；撞 collation conflict → `collate_side="view"`
    重試（＝`mes_qlik.fetch_history` 同款機制）。
  · 本管線只用 `stpStartTime`、**不碰 `stpEndTime`**（日後若有人要加 end 過濾：一律 `>=`，
    `>` 會刪掉合法零工期段、八站整站隱形＝[[mes-zero-length-segments]]）。

★ 唯讀：對 view 只有 SELECT；唯一 DDL/DML 在 `#temp`（tempdb 不算寫 MES）。
★ 凍結模組 `mes_timeline.py` / `mes_yield.py`：**不 import、不執行、零 diff**。
  `_connect` 走 `mes_qlik._connect`（非凍結模組的自足複本，含 `SET ARITHABORT ON`）
  ＝**不再抄第三份連線碼**。

★ env（SPEC §5.5；預設值即 pin 值，env 只在 live 校準時動）
    BOARD_WIP_AGE_CAP_D=14   BOARD_WIP_LOOKBACK_D=21
    BOARD_WIP_RATE_WIN_H=4   BOARD_WIP_RATE_FALLBACK_D=7   BOARD_WIP_H_LOOKBACK_D=60

CLI:
    python mes_wip.py --days 21 [--cap "2026-08-01T10:30"] [--json]
    python mes_wip.py --selftest        # 純離線合成資料、不連 MES
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

# ── 語意權威＋積木一律 import 重用（SPEC §4.1 pin；43 站序不另抄一個位元）───────────
from mes_qlik import (                      # noqa: F401  （`_win`/`FRESH_CLASSES` 見下方使用點）
    QLIK_STATIONS, QLIK_DISPLAY, MLC_WHITELIST, EXCLUDED_CLASSES, LOT_PREFIXES,
    RESULT_KEEP, RESULT_PASS, RESULT_FAIL, ASSY_MLC, EOL_MLC, FRESH_CLASSES,
    SchemaDivergence, REQUIRED_SEG_COLUMNS,
    _connect, _sql_list, _process01_where, _mk_lots_temp, _dedup_rows, _dedup_lots_ci,
    _dt, _win, LOTS_TEMP,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# 1. 人工審定常數（改它＝人工動作；SPEC §4.1）
# =============================================================================
SCHEMA_ID = "board.wip.v1"
CALIBER_WIP = ("mes-wip live-lot queue (plover B-prefix, 43-route, stpPK-position)")

# ★ pass-through（無資料站）＝**人工審定**、絕不 runtime 推斷（SPEC §5.1 尾）。
#   證據＝`_QLIK-RECON-SCREEN-2026-07-31.md` 螢幕 42/43 列（ord30 den=0）。
#   誤校準的自揭＝`diag.zero_tput_with_queue`（佇列堆高但 30d 零吞吐 ⇒ 人工裁決後加進本常數）。
WIP_PASSTHROUGH_ORDS = (30,)

# ★ 活 lot 白名單（**正面列舉**；SPEC §5.3-5b）：
#   a) Hold/品保凍結類狀態（若存在）落 `excluded_dead` ＝正確——被凍的料機台吃不到、不該撐
#      cover；桶名叫 dead 是**排除桶命名**，不主張物理報廢。
#   b) 若 MES 另有第三個「活」狀態值，佇列會**靜默漏算** ⇒ 防護＝`diag.status_census`
#      （位置列 matStatus distinct 值→計數）live 一眼看全貌；補白名單＝人工改本常數、不 runtime 猜。
#   語意與 `mes_qlik.EOL_PASS_STATUSES` 同值但**獨立命名**（那是 line-end 強制 PASS、這是活料判定）。
LIVE_STATUSES = ("WIP", "Available")

# ★ `zero_tput_with_queue` 自揭窗（SPEC §5.1 尾）＝**30 天**、不是速率的 7d 窗。
#   用 7d 判「窗內零吞吐」會把休假週/低量偶發站也標成 pass-through 嫌疑（過度自揭 ⇒ 狼來了、
#   人工裁決訊號被稀釋）。[H1] 本來就拉 60d 列，30d 計數零成本。
#   實際使用窗＝min(本常數, h_lookback_eff)（列窗更短時不假稱看了 30 天；diag 自揭）。
ZERO_TPUT_WIN_D = 30


def _env_int(name, default):
    """env 覆寫（typo 不炸開機＝R1 審查輪教訓：壞值一律退回預設）。"""
    try:
        v = int(str(os.environ.get(name, "")).strip())
        return v if v > 0 else default
    except Exception:
        return default


AGE_CAP_D = _env_int("BOARD_WIP_AGE_CAP_D", 14)          # 超齡界（SPEC §5.5）
LOOKBACK_D = _env_int("BOARD_WIP_LOOKBACK_D", 21)        # [W1] 母體窗（恆 ≥ AGE_CAP_D）
RATE_WIN_H = _env_int("BOARD_WIP_RATE_WIN_H", 4)         # 速率短窗（小時）
RATE_FALLBACK_D = _env_int("BOARD_WIP_RATE_FALLBACK_D", 7)   # 速率 fallback 窗（天）
H_LOOKBACK_D = _env_int("BOARD_WIP_H_LOOKBACK_D", 60)    # [H1] 列拉取窗（天）

QUERY_TIMEOUT_SEC = 180

# [H1] 回傳 7 欄 tuple 的欄位索引（pin；純函式層全部吃這個形狀）
I_PK, I_LOT, I_TS, I_RES, I_PSC, I_MLC, I_STATUS = range(7)
ROW_ARITY = 7


# =============================================================================
# 2. 小工具（純函式）
# =============================================================================
_SQL_IDENT = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _ident(name):
    """欄名只允許識別字字元（承重欄名全來自常數，仍守一道＝注入面歸零）。"""
    if not name or not set(name) <= _SQL_IDENT:
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _as_dt(x):
    """None → None；datetime 直通；字串 → `_dt`（snapshot/合成路徑的容錯）。"""
    if x is None or isinstance(x, datetime):
        return x
    return _dt(x)


def _r(x, nd):
    """None-safe round（顯示精度；**cover 一律由未捨入的 rate 現算**、不吃捨入誤差）。"""
    return None if x is None else round(float(x), nd)


def _route_tuple(route):
    return tuple(QLIK_STATIONS if route is None else route)


def _route_maps(route, has_mcl=True):
    """route → (key→ord, ord→cls, ord→(cls,mcl))。

    ★ `has_mcl=False` ＝ `mclMaterialClassID` 欄 ABSENT 的**降級路徑**：改用 cls-only 對映
      （43 個 cls 互異＝selftest 0.3 釘死，無歧義）。**同時 `diag.degraded=True`＋caveat**
      ——不這樣做的話 (cls,mcl) 永遠對不上 route ⇒ 43 列全零，而「全零」在消費面與
      「線上真的沒料」長得一模一樣（＝最壞的靜默謊；同 `mes_qlik` SchemaDivergence 的理由）。
    """
    key_ord, cls_by_ord, pair_by_ord = {}, {}, {}
    for o, cls, mcl in route:
        key_ord[cls if not has_mcl else (cls, mcl)] = o
        cls_by_ord[o] = cls
        pair_by_ord[o] = (cls, mcl)
    return key_ord, cls_by_ord, pair_by_ord


def _row_key(sgc, mcl, has_mcl=True):
    return sgc if not has_mcl else (sgc, mcl)


def _target_map(route, passthrough):
    """`target(k) = min{ j>k : j ∉ passthrough }`（SPEC §5.1-②：**直接前驅＋跳站**）。

    末端（無下一站）→ None ＝ 該 lot 已走完 route（PASS 時歸 `excluded_done`）。
    """
    pt = frozenset(passthrough or ())
    ords = sorted(o for o, _c, _m in route)
    out = {}
    for i, o in enumerate(ords):
        nxt = None
        for j in ords[i + 1:]:
            if j not in pt:
                nxt = j
                break
        out[o] = nxt
    return out


def _clamp_lookback(lookback_d, age_cap_d):
    """`lookback_d` 恆 ≥ `age_cap_d`（SPEC §5.5），違者 clamp 並回一句 diag 說明。"""
    if lookback_d is None or lookback_d < age_cap_d:
        return age_cap_d, (f"lookback_d={lookback_d} < age_cap_d={age_cap_d} "
                           f"-> clamped to {age_cap_d}")
    return lookback_d, None


def _clamp_h_lookback(h_lookback_d, lookback_eff):
    """`h_lookback_d`（[H1] 列窗）恆 ≥ `lookback_eff`（[W1] 母體窗），違者 clamp＋自揭。

    ★ 為什麼是鐵則：[W1] 收「近 lookback 天有活動的 lot」，[H1] 只拉「近 h_lookback 天的列」。
      若 h_lookback < lookback，只在 (h_lookback, lookback] 有活動的 lot **一列都拉不到**
      ⇒ `latest_positions` 看不到它 ⇒ 不進任何桶、不進 live_total ＝**靜默消失**
      （唯一線索只有 `n_win_lots` 與 `n_position_lots` 的落差，而沒人會去比）。
      預設值（21 ≤ 60）永不觸發；這是 env 誤配面（`BOARD_WIP_LOOKBACK_D=90`）的防護。
    """
    if h_lookback_d is None or h_lookback_d < lookback_eff:
        return lookback_eff, (f"h_lookback_d={h_lookback_d} < lookback_d={lookback_eff} "
                              f"-> clamped to {lookback_eff} (otherwise lots whose only "
                              f"activity falls in that age band vanish silently)")
    return h_lookback_d, None


# =============================================================================
# 3. I/O 層（SQL builder 抽成純函式＝離線可斷言形狀；唯讀＝SELECT ＋ #temp）
# =============================================================================
def _sql_guard():
    """[G0] column guard SQL（承重 5 欄＋兩個可降級欄自揭）。"""
    req = ",\n           ".join(
        f"CASE WHEN COL_LENGTH('dbo.process_segment_v','{_ident(c)}') IS NULL THEN 0 ELSE 1 END"
        for c in REQUIRED_SEG_COLUMNS)
    return ("SELECT CASE WHEN OBJECT_ID('dbo.process_segment_v') IS NULL THEN 0 ELSE 1 END,\n"
            "       CASE WHEN COL_LENGTH('dbo.process_segment_v','mclMaterialClassID')"
            " IS NULL THEN 0 ELSE 1 END,\n"
            "       CASE WHEN COL_LENGTH('dbo.process_segment_v','matStatus')"
            " IS NULL THEN 0 ELSE 1 END,\n"
            f"       {req}\n"
            "OPTION (RECOMPILE)")


GUARD_KEYS = ("view_seg", "mclMaterialClassID", "matStatus") + tuple(REQUIRED_SEG_COLUMNS)


def assert_wip_guard(guard):
    """承重欄檢查（純函式＝離線可測）。ABSENT ⇒ `SchemaDivergence`、訊息逐欄具名。

    「停下」而不是「跑出全零板」的理由與 `mes_qlik` 同：全零板在消費面與「線上沒料」
    長得一樣＝靜默謊。呼叫端（`board_data.get_wip` → `app.load_wip`）接成
    `wip_unavailable("error:SchemaDivergence")`＝頁面顯「—」＋原因，仍不炸站。
    """
    if not guard.get("view_seg"):
        raise SchemaDivergence("schema divergence: dbo.process_segment_v ABSENT")
    missing = [c for c in REQUIRED_SEG_COLUMNS if not guard.get(c)]
    if missing:
        raise SchemaDivergence("schema divergence: "
                               + ", ".join(f"{c} ABSENT" for c in missing))
    return True


def fetch_wip_guard(cur):
    """[G0] 欄位守衛。回 dict（全 bool）：`GUARD_KEYS` 逐鍵。

    · 承重（view ＋ `REQUIRED_SEG_COLUMNS` 5 欄）ABSENT ⇒ raise `SchemaDivergence`。
    · 可降級：`mclMaterialClassID`（→ cls-only route 對映＋degraded）／
      `matStatus`（→ 死 lot 排除 SKIPPED、`diag.dead_filter` 自揭）。**兩者都不默默當正常。**
    """
    cur.execute(_sql_guard())
    r = cur.fetchone()
    guard = {k: bool(r[i]) for i, k in enumerate(GUARD_KEYS)}
    assert_wip_guard(guard)
    return guard


def _sql_recent_lots(has_mcl=True):
    """[W1] 近窗活 lot 收割 SQL（形狀＝`mes_qlik.fetch_window_lots` 同形）。

    **只回 lot 原始列**、`set()` 去重在 Python ＝絕不在 5 表大 view 上 DISTINCT（B4 斷崖）。
    """
    return ("SELECT v.matMaterialLotID\n"
            "FROM dbo.process_segment_v v\n"
            "WHERE v.stpStartTime >= ? AND v.stpStartTime < ?\n"
            + _process01_where(has_mcl) +
            "OPTION (RECOMPILE)")


def fetch_recent_lots(cur, cap, lookback_d, has_mcl=True, stats=None):
    """[W1] 母體收斂第一段：近 `lookback_d` 天有**任何段**的 lot 集合。

    ★ 這一段就是「絕不全史全線裸掃」的落地：後面 [H1] 只掃這些 lot。
    ★ Process01 四道過濾逐字重用（`_process01_where`）——含 `stpTestResult IN (PASS,FAIL)`，
      即 **NULL 判定列（正在機上跑、尚無判定）整筆不進母體**（SPEC §5.1 已知邊界：
      那類 lot 的可見位置仍停在前站 ⇒ 照樣計入下一站 queue_n。**絕不為此把 NULL 列撈回來
      當位置**——NULL 列語意不明，撈回＝另一種口徑發明）。
    """
    cap = _dt(cap)
    lo = cap - timedelta(days=lookback_d)
    cur.execute(_sql_recent_lots(has_mcl), lo, cap)
    raw = [r[0] for r in cur.fetchall()]
    lots = {x for x in raw if x is not None}
    if stats is not None:
        stats["n_win_rows"] = len(raw)
        stats["n_win_lots"] = len(lots)
        stats["win_lo"] = lo
    return lots


def _sql_lot_rows(has_mcl=True, has_status=True, collate_side="temp"):
    """[H1] `#temp` join 拉列 SQL（單 view、**無 IN-list**、COLLATE 掛 #temp 側）。"""
    if collate_side == "temp":
        join = f"JOIN {LOTS_TEMP} l ON l.lot COLLATE DATABASE_DEFAULT = v.matMaterialLotID\n"
    else:
        join = (f"JOIN {LOTS_TEMP} l "
                "ON l.lot = v.matMaterialLotID COLLATE Latin1_General_100_CI_AS_SC_UTF8\n")
    return ("SELECT v.stpPK, v.matMaterialLotID, v.stpStartTime, v.stpTestResult,\n"
            "       v.SegmentClassID, "
            + ("v.mclMaterialClassID" if has_mcl else "N'(absent)'") + ", "
            + ("v.matStatus" if has_status else "CAST(NULL AS nvarchar(64))") + "\n"
            "FROM dbo.process_segment_v v\n"
            + join +
            "WHERE v.stpStartTime >= ? AND v.stpStartTime < ?\n"
            + _process01_where(has_mcl) +
            "OPTION (RECOMPILE)")


def fetch_lot_rows(cur, cap, h_lookback_d, has_mcl=True, has_status=True,
                   collate_side="temp", stats=None):
    """[H1] 母體收斂第二段：只拉 `#temp` 裡那些 lot 的近 `h_lookback_d` 天列。

    回 7 欄 tuple list：`(stpPK, lot, ts, res, sgc, mcl, mlt_status)`。
    Python 端 `_dedup_rows`（view 若吐重複列，位置判定與速率 distinct 都不受影響，但
    `diag.n_dup_rows_removed` 要自揭）。
    ★ collation conflict ⇒ 呼叫端以 `collate_side="view"` 重試（犧牲 seek、保證能跑）。
    ★ `#temp` 存活於 connection 生命週期 ⇒ 必須與 `_mk_lots_temp` 同一條連線／cursor。
    """
    cap = _dt(cap)
    lo = cap - timedelta(days=h_lookback_d)
    cur.execute(_sql_lot_rows(has_mcl, has_status, collate_side), lo, cap)
    raw = [tuple(r) for r in cur.fetchall()]
    rows = _dedup_rows(raw)
    if stats is not None:
        stats["n_hist_raw_rows"] = len(raw)
        stats["n_hist_rows"] = len(rows)
        stats["n_dup_rows_removed"] = len(raw) - len(rows)
        stats["collate_side"] = collate_side
        stats["hist_lo"] = lo
    return rows


# =============================================================================
# 4. 純函式層（selftest 主戰場；全部離線可測）
# =============================================================================
def _pos_sort_key(r):
    """位置比較鍵＝`stpPK` 為主（鐵則 7），其餘欄只用來**打破同 stpPK 的平手**
       ＝確定性（SQL 回列順序不保證，平手時不可看誰先到）。"""
    return (r[I_PK], str(r[I_PSC]), str(r[I_MLC]), str(r[I_RES]), str(r[I_TS]))


def latest_positions(rows):
    """per-lot 取 **max stpPK** 那一列＝該 lot 的「現在在哪」。回 `{lot: 7 欄 tuple}`。

    · rework 迴圈自然解（SPEC §5.2）：復測＝新列、stpPK 更大 ⇒ 位置自動拉回復測站。
      **絕不用時戳或站序推位置**（J0SYN14：CAL 過→AutoOQC 死→回 CAL 復測，時序倒置、stpPK 不倒）。
    · lot/stpPK 為 None 的列丟棄（結構上不可達；防呆）。
    """
    out = {}
    for r in rows:
        if len(r) < ROW_ARITY:
            continue
        lot, pk = r[I_LOT], r[I_PK]
        if lot is None or pk is None:
            continue
        cur_r = out.get(lot)
        if cur_r is None or _pos_sort_key(r) > _pos_sort_key(cur_r):
            out[lot] = tuple(r)
    return out


def station_rates(rows, route, cap, rate_win_h, rate_fallback_d, *, has_mcl=True):
    """per-站（cls 為鍵）速率。回 `{cls: {n_4h, n_7d, rate_uph_4h, rate_uph_7d,
       rate_uph, rate_src}}`——route 每站必有一筆（無資料＝0/None，**不缺鍵**）。

    · 母體＝**位置無關**的該站列（PASS+FAIL 都算＝機台吞吐嘗試）。
    · 單位＝lot/h 的**淨清隊速度**、刻意用 **distinct lot 不用列數**（SPEC §5.4）：同窗內
      同 lot 復測多次仍只離隊一次；用嘗試列數會把復測重站的 cover 壓假低。
    · **7d fallback 的已知偏差（誠實講明、v1 不修）**：168h 分母含夜班/週末停線時段 ⇒
      `rate_uph_7d` 系統性低於上班時段真實速率 ⇒ fallback 時 `cover_h` 偏大（THICK 更易成立）。
      消費面必標 `rate_src`、footer 講明「cover 屬上界估計」。
    · fallback 序（pin）：4h → 7d → None；`rate_src ∈ {"4h","7d","none"}`。
    · **已知邊界（誠實講明、v1 不修）**：速率母體＝Process01 全母體（`LOT_PREFIXES` 含 B0/M0、
      且不看 lot 死活），而 `queue_n` 只算 B 前綴的活料 ⇒ 分子分母族群不完全相同。
      方向**保守**：rate 略偏高 ⇒ cover 略偏小 ⇒ **較不易**亮「需支援」（不會過度主張人力缺口）。
      規格 §5.4 明寫「位置無關、該站 distinct lot 數」＝照抄；要改是口徑 owner 的事。
    """
    cap = _dt(cap)
    route = _route_tuple(route)
    key_ord, cls_by_ord, _pair = _route_maps(route, has_mcl=has_mcl)
    w4 = cap - timedelta(hours=rate_win_h) if rate_win_h and rate_win_h > 0 else None
    w7 = (cap - timedelta(days=rate_fallback_d)
          if rate_fallback_d and rate_fallback_d > 0 else None)

    l4, l7 = {}, {}
    for r in rows:
        if len(r) < ROW_ARITY:
            continue
        ts = _as_dt(r[I_TS])
        lot = r[I_LOT]
        if ts is None or lot is None or ts >= cap:      # as-of cap：半開窗 [lo, cap)
            continue
        o = key_ord.get(_row_key(r[I_PSC], r[I_MLC], has_mcl))
        if o is None:
            continue
        cls = cls_by_ord[o]
        if w4 is not None and ts >= w4:
            l4.setdefault(cls, set()).add(lot)
        if w7 is not None and ts >= w7:
            l7.setdefault(cls, set()).add(lot)

    out = {}
    for o, cls, _mcl in route:
        n4 = len(l4.get(cls, ()))
        n7 = len(l7.get(cls, ()))
        r4 = (n4 / float(rate_win_h)) if (n4 > 0 and rate_win_h and rate_win_h > 0) else None
        r7 = (n7 / float(rate_fallback_d * 24)) if (n7 > 0 and rate_fallback_d
                                                    and rate_fallback_d > 0) else None
        if r4 is not None:
            rate, src = r4, "4h"
        elif r7 is not None:
            rate, src = r7, "7d"
        else:
            rate, src = None, "none"
        out[cls] = {"n_4h": n4, "n_7d": n7,
                    "rate_uph_4h": _r(r4, 6), "rate_uph_7d": _r(r7, 6),
                    "rate_uph": _r(rate, 6), "rate_src": src,
                    "_rate_exact": rate}          # cover 用未捨入值（底線鍵＝內部用）
    return out


def _distinct_lot_counts(rows, key_ord, cls_by_ord, lo, cap, has_mcl=True):
    """`{cls: 窗 [lo, cap) 內 distinct lot 數}`（位置無關；`station_rates` 的同款母體語意）。

    只供 `zero_tput_with_queue` 的 30d 判定用（**不進 station 欄**＝canonical pin 不動）。
    """
    seen = {}
    for r in rows:
        if len(r) < ROW_ARITY:
            continue
        ts, lot = _as_dt(r[I_TS]), r[I_LOT]
        if ts is None or lot is None or ts >= cap or ts < lo:
            continue
        o = key_ord.get(_row_key(r[I_PSC], r[I_MLC], has_mcl))
        if o is None:
            continue
        seen.setdefault(cls_by_ord[o], set()).add(lot)
    return {c: len(s) for c, s in seen.items()}


def derive_wip(rows, cap, *, route=None, passthrough=WIP_PASSTHROUGH_ORDS,
               age_cap_d=AGE_CAP_D, rate_win_h=RATE_WIN_H,
               rate_fallback_d=RATE_FALLBACK_D, has_status=True,
               # ↓ 以下為 additive keyword-only（pin 簽名前段一字未動）
               has_mcl=True, lookback_d=LOOKBACK_D, h_lookback_d=H_LOOKBACK_D,
               guard=None, n_win_lots=None, n_hist_rows=None,
               n_dup_rows_removed=0, timing=None, zero_tput_d=ZERO_TPUT_WIN_D):
    """SPEC §5 全部口徑的落地點：`latest_positions` → 排除桶 → 佇列歸屬 → rates → cover。

    回 §4.4 canonical（`elapsed_s=None`＝純函式路徑無 I/O 耗時，`compute_wip` 會覆寫）。

    不變式（selftest 釘死）：
      · `len(stations) == len(route)`、ord 升冪；
      · `line.live_total == Σqueue_n + Σstale_n + Σfail_hold_n + diag.n_pos_res_unexpected`；
      · `len(positions) == live_total + Σ excluded_*`（每顆料恰好落一個桶）；
      · `rate_uph is None ⇔ cover_h is None`；`queue_n==0 ∧ rate 有值 ⇒ cover_h==0.0`。
    """
    cap = _dt(cap)
    route = _route_tuple(route)
    pt = frozenset(passthrough or ())
    key_ord, cls_by_ord, pair_by_ord = _route_maps(route, has_mcl=has_mcl)
    tgt = _target_map(route, pt)
    lookback_eff, clamp_note = _clamp_lookback(lookback_d, age_cap_d)
    h_lookback_eff, h_clamp_note = _clamp_h_lookback(h_lookback_d, lookback_eff)
    # 自揭窗不得聲稱看得比 [H1] 列窗更遠（列窗短時一律縮到列窗）
    ztd = max(1, min(int(zero_tput_d or ZERO_TPUT_WIN_D), int(h_lookback_eff)))

    positions = latest_positions(rows)
    rates = station_rates(rows, route, cap, rate_win_h, rate_fallback_d, has_mcl=has_mcl)
    n_zt = _distinct_lot_counts(rows, key_ord, cls_by_ord,
                                cap - timedelta(days=ztd), cap, has_mcl=has_mcl)

    ords = [o for o, _c, _m in route]
    queue = {o: 0 for o in ords}
    stale = {o: 0 for o in ords}
    fail_hold = {o: 0 for o in ords}
    ex = {"excluded_non_b": 0, "excluded_assy": 0, "excluded_done": 0,
          "excluded_off_route": 0, "excluded_dead": 0}
    census = {}
    live_total = 0
    n_null_ts = n_res_unexpected = 0
    n_pos_ship_receipt = 0                       # 位置落出貨/收料段（FRESH_CLASSES）自揭
    age_limit = timedelta(days=age_cap_d)

    for _lot, r in positions.items():
        pk, lot, ts, res, sgc, mcl, status = (r[I_PK], r[I_LOT], _as_dt(r[I_TS]),
                                              r[I_RES], r[I_PSC], r[I_MLC], r[I_STATUS])
        if has_status:                           # §5.3-5b：值集自揭（含非白名單值現形）
            k = status if status is not None else "(null)"
            census[k] = census.get(k, 0) + 1
        if sgc in FRESH_CLASSES:
            n_pos_ship_receipt += 1

        o = key_ord.get(_row_key(sgc, mcl, has_mcl))

        # ---- 排除桶（SPEC §5.3；**優先序由上而下、先中先出**）---------------------
        if (lot or "")[:1] != "J":                                   # ① 非主線 plover
            ex["excluded_non_b"] += 1
            continue
        if mcl == ASSY_MLC:                                           # ② 可靠度測試
            ex["excluded_assy"] += 1
            continue
        if mcl == EOL_MLC or (o is not None and res == RESULT_PASS   # ③ 已出 route（線尾）
                              and tgt.get(o) is None):
            ex["excluded_done"] += 1
            continue
        if o is None:                                                # ④ 已離線體
            ex["excluded_off_route"] += 1
            continue
        if has_status and status not in LIVE_STATUSES:               # ⑤ 死 lot（現況欄）
            ex["excluded_dead"] += 1
            continue
        # 註：⑥ Fresh/Sorting **不切**（與 yield 口徑刻意不同）——Sorting 再投入的 lot
        #     物理上佔用佇列與機台，調度板照計；yield 頁才管顯示切面。

        # ---- 活 lot ------------------------------------------------------------
        live_total += 1
        if res == RESULT_FAIL:                   # 末列 FAIL＝卡在原站、不進任何佇列
            fail_hold[o] += 1
            continue
        if res != RESULT_PASS:                   # Process01 保證只有 PASS/FAIL；到這裡＝分歧
            n_res_unexpected += 1
            continue

        t = tgt[o]                               # PASS ∧ 無下一站已在桶 ③ 收走 ⇒ 必非 None
        if ts is None:                           # 齡不可測（SQL 路徑結構上不可達；合成才有）
            n_null_ts += 1
            queue[t] += 1
        elif (cap - ts) > age_limit:             # ⑦ 超齡＝單獨計數、不丟棄也不混入排隊
            stale[t] += 1
        else:
            queue[t] += 1

    # ---- 站列組裝 --------------------------------------------------------------
    stations, zero_tput = [], []
    for o, cls, mcl in sorted(route, key=lambda x: x[0]):
        rt = rates.get(cls, {"n_4h": 0, "n_7d": 0, "rate_uph_4h": None, "rate_uph_7d": None,
                             "rate_uph": None, "rate_src": "none", "_rate_exact": None})
        q = queue[o]
        exact = rt.get("_rate_exact")
        cover = _r(q / exact, 3) if exact else None      # rate None ⇒ cover None（誠實鐵則）
        if q > 0 and n_zt.get(cls, 0) == 0:
            zero_tput.append(cls)                        # pass-through 誤校準自揭（§5.1 尾）
        stations.append({
            "ord": o, "cls": cls, "mcl": mcl, "display": QLIK_DISPLAY.get(o, cls),
            "queue_n": q, "stale_n": stale[o], "fail_hold_n": fail_hold[o],
            "n_4h": rt["n_4h"], "n_7d": rt["n_7d"],
            "rate_uph_4h": rt["rate_uph_4h"], "rate_uph_7d": rt["rate_uph_7d"],
            "rate_uph": rt["rate_uph"], "rate_src": rt["rate_src"],
            "cover_h": cover,
            "passthrough": o in pt,
        })

    line = {
        "live_total": live_total,
        "queue_total": sum(queue.values()),                # additive（headline 用）
        "stale_total": sum(stale.values()),
        "fail_hold_total": sum(fail_hold.values()),
        "excluded_dead": ex["excluded_dead"], "excluded_done": ex["excluded_done"],
        "excluded_assy": ex["excluded_assy"],
        "excluded_off_route": ex["excluded_off_route"],
        "excluded_non_b": ex["excluded_non_b"],
    }

    # ---- diag ------------------------------------------------------------------
    route_ok = (len(route) == len(QLIK_STATIONS)
                and [o for o, _c, _m in sorted(route, key=lambda x: x[0])]
                == [o for o, _c, _m in QLIK_STATIONS])
    route_check = "ok" if route_ok else f"!! route mismatch: n={len(route)} (exp 43)"
    caveats = []
    if not has_mcl:
        caveats.append("mclMaterialClassID ABSENT -> route matched on cls only; "
                       "Process01 whitelist lost a dimension (NOT company-caliber equivalent)")
    if not has_status:
        caveats.append("matStatus ABSENT -> dead-lot exclusion SKIPPED; "
                       "queue/cover may include scrapped or shipped lots (upper bound)")
    if n_res_unexpected:
        caveats.append(f"{n_res_unexpected} position rows carry a stpTestResult outside "
                       f"{RESULT_KEEP} (Process01 should have excluded them)")
    degraded = bool((not has_mcl) or n_res_unexpected or not route_ok)

    diag = {
        "guard": dict(guard or {}),
        "n_win_lots": (len(positions) if n_win_lots is None else n_win_lots),
        "n_hist_rows": (len(rows) if n_hist_rows is None else n_hist_rows),
        "dead_filter": "applied" if has_status else "SKIPPED(matStatus ABSENT)",
        "status_census": census,
        "beyond_lookback_note": (f"stale 只涵蓋 (age_cap={age_cap_d}d, "
                                 f"lookback={lookback_eff}d] 齡帶（§5.5）；更久遠的擱置在製"
                                 f"不在此板，盤點歸 SALTMARSH 對帳（未來）。母體＝近 {lookback_eff}d "
                                 f"有 PASS/FAIL 判定段的 lot（Process01；正在機上跑、尚無判定"
                                 f"的 NULL 列不進母體＝§5.1 已知邊界）"),
        "zero_tput_with_queue": zero_tput,
        "zero_tput_win_d": ztd,
        # --- additive 自揭（非 pin；B2 白名單讀取、未知欄忽略）---
        "mcl_filter": "applied" if has_mcl else "SKIPPED(mcl ABSENT)",
        "n_position_lots": len(positions),
        "n_dup_rows_removed": n_dup_rows_removed,
        "n_pos_null_ts": n_null_ts,
        "n_pos_res_unexpected": n_res_unexpected,
        "n_pos_ship_receipt": n_pos_ship_receipt,
        "lookback_clamped": clamp_note,
        "h_lookback_clamped": h_clamp_note,
        "route_check": route_check,
        "route_n": len(route),
        "passthrough_ords": sorted(pt),
        "rate_window_note": (f"rate_uph_4h＝近 {rate_win_h}h distinct lot／{rate_win_h}；"
                             f"rate_uph_7d＝近 {rate_fallback_d}d distinct lot／"
                             f"{rate_fallback_d * 24}（含停線時段⇒ cover 屬上界估計）；"
                             f"速率母體＝Process01 全母體（含 M0 前綴與已死料），queue 只算 B "
                             f"活料 ⇒ rate 略偏高、cover 偏保守"),
        "timing": dict(timing or {}),
        "degraded": degraded,
        "caveats": caveats,
    }

    return {
        "schema": SCHEMA_ID,
        "ok": True,               # 失敗一律 raise（消費端接成 wip_unavailable）；品質旗＝diag.degraded
        "caliber": CALIBER_WIP,
        "cap": cap.isoformat(),   # naive 台北牆鐘 isoformat（pin：字串不是 datetime）
        "params": {"age_cap_d": age_cap_d, "rate_win_h": rate_win_h,
                   "rate_fallback_d": rate_fallback_d, "lookback_d": lookback_eff,
                   "h_lookback_d": h_lookback_eff},
        "stations": stations,
        "line": line,
        "diag": diag,
        "elapsed_s": None,
    }


CANONICAL_KEYS = ("schema", "ok", "caliber", "cap", "params", "stations", "line",
                  "diag", "elapsed_s")
STATION_KEYS = ("ord", "cls", "mcl", "display", "queue_n", "stale_n", "fail_hold_n",
                "n_4h", "n_7d", "rate_uph_4h", "rate_uph_7d", "rate_uph", "rate_src",
                "cover_h", "passthrough")
LINE_KEYS = ("live_total", "queue_total", "stale_total", "fail_hold_total",
             "excluded_dead", "excluded_done", "excluded_assy", "excluded_off_route",
             "excluded_non_b")


# =============================================================================
# 5. I/O 編排
# =============================================================================
def compute_wip(cur, cap=None, *, route=None, age_cap_d=AGE_CAP_D,
                rate_win_h=RATE_WIN_H, rate_fallback_d=RATE_FALLBACK_D,
                lookback_d=LOOKBACK_D, h_lookback_d=H_LOOKBACK_D):
    """唯一 I/O 編排點：guard → [W1] → `#temp` → [H1] → `derive_wip`。

    · `cap=None` ⇒ `SELECT GETDATE()` 一次、全程沿用（＝as-of now 的 live 語意；
      **絕不在中途重取 now**，否則母體窗與速率窗會對不齊）。
    · `SchemaDivergence` 往上拋（`board_data` 接成 `wip_unavailable`）。
    · ⚠ `cur` 必須是同一條連線的 cursor（`#temp` 存活於 connection 生命週期）。
    """
    t_all = time.perf_counter()
    timing, st = {}, {}

    t0 = time.perf_counter()
    guard = fetch_wip_guard(cur)
    timing["guard_ms"] = int((time.perf_counter() - t0) * 1000)
    has_mcl = guard["mclMaterialClassID"]
    has_status = guard["matStatus"]

    if cap is None:
        cur.execute("SELECT GETDATE()")
        cap = cur.fetchone()[0]
    cap = _dt(cap)

    lookback_eff, _note = _clamp_lookback(lookback_d, age_cap_d)
    # [H1] 列窗恆 ≥ [W1] 母體窗——**必須在 fetch 之前 clamp**，否則 (h_lookback, lookback]
    # 齡帶的 lot 一列都拉不到＝靜默消失（見 `_clamp_h_lookback` docstring）。
    h_lookback_eff, _h_note = _clamp_h_lookback(h_lookback_d, lookback_eff)
    _win((cap - timedelta(days=lookback_eff), cap))     # 母體窗非空守衛（反向/零窗直接擋下）

    t0 = time.perf_counter()
    lots = fetch_recent_lots(cur, cap, lookback_eff, has_mcl=has_mcl, stats=st)
    timing["w_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    if lots:
        _mk_lots_temp(cur, lots)
        try:
            rows = fetch_lot_rows(cur, cap, h_lookback_eff, has_mcl=has_mcl,
                                  has_status=has_status, collate_side="temp", stats=st)
        except Exception as e:                          # COLLATE 換側回退（軌一 QA-S 事故）
            if "collation conflict" not in str(e).lower():
                raise
            rows = fetch_lot_rows(cur, cap, h_lookback_eff, has_mcl=has_mcl,
                                  has_status=has_status, collate_side="view", stats=st)
    else:
        rows = []
    timing["h_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    wip = derive_wip(rows, cap, route=route, age_cap_d=age_cap_d, rate_win_h=rate_win_h,
                     rate_fallback_d=rate_fallback_d, has_status=has_status,
                     has_mcl=has_mcl, lookback_d=lookback_eff, h_lookback_d=h_lookback_eff,
                     guard=guard, n_win_lots=st.get("n_win_lots"),
                     n_hist_rows=st.get("n_hist_rows"),
                     n_dup_rows_removed=st.get("n_dup_rows_removed", 0))
    timing["derive_ms"] = int((time.perf_counter() - t0) * 1000)
    timing["total_ms"] = sum(v for k, v in timing.items()
                             if k.endswith("_ms") and k != "total_ms")
    wip["diag"]["timing"] = timing
    # clamp 自揭：derive 收到的已是 clamp 後的值 ⇒ 它自己重算恆為 None、必須由這裡回填。
    wip["diag"]["lookback_clamped"] = _note
    wip["diag"]["h_lookback_clamped"] = _h_note
    wip["diag"]["collate_side"] = st.get("collate_side")
    wip["elapsed_s"] = round(time.perf_counter() - t_all, 3)
    return wip


def run(days=None, cap=None, age_cap_d=AGE_CAP_D, rate_win_h=RATE_WIN_H,
        rate_fallback_d=RATE_FALLBACK_D, h_lookback_d=H_LOOKBACK_D,
        timeout=QUERY_TIMEOUT_SEC):
    """CLI/snapshot 用的 live 編排（開一條連線、跑完關掉）。回 canonical。

    `days` ＝ `[W1]` 母體窗天數（＝`lookback_d`；未給用預設 21）。
    """
    conn, cur = _connect(timeout=timeout)
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        wip = compute_wip(cur, cap if cap is not None else now,
                          age_cap_d=age_cap_d, rate_win_h=rate_win_h,
                          rate_fallback_d=rate_fallback_d,
                          lookback_d=(days if days else LOOKBACK_D),
                          h_lookback_d=h_lookback_d)
        wip["mes_now"] = now.isoformat() if isinstance(now, datetime) else str(now)
        return wip
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =============================================================================
# 6. 呈現（CLI）
# =============================================================================
def _f(x, w=7, nd=2):
    return (f"{x:>{w}.{nd}f}" if isinstance(x, (int, float)) else f"{'—':>{w}}")


def render(wip):
    d, p = wip["diag"], wip["params"]
    print(f"=== MES-WIP 調度板資料（{wip['schema']}）　cap={wip['cap']} ===")
    print(f"  口徑＝{wip['caliber']}")
    print(f"  參數＝age_cap {p['age_cap_d']}d｜rate 窗 {p['rate_win_h']}h／"
          f"fallback {p['rate_fallback_d']}d｜母體 {p['lookback_d']}d｜列窗 {p['h_lookback_d']}d")
    if d["degraded"]:
        print("  ⚠ DEGRADED：資料品質門檻未過，以下數字標記為不可信（見 diag.caveats）。")
    if d["dead_filter"] != "applied":
        print(f"  ⚠ 死 lot 過濾降級：{d['dead_filter']}")
    print(f"\n  ── 43 站（ord 序；cover「—」＝該站窗內無吞吐、覆蓋不可測，非 0）──")
    print(f"    {'#':>3} {'站':<44}{'排隊':>6}{'超齡':>6}{'卡站':>6}"
          f"{'rate/h':>9}{'源':>5}{'cover_h':>9}")
    print("    " + "-" * 92)
    for s in wip["stations"]:
        tag = " ·跳站" if s["passthrough"] else ""
        print(f"    {s['ord']:>3} {(s['display'] + tag)[:43]:<44}{s['queue_n']:>6}"
              f"{s['stale_n']:>6}{s['fail_hold_n']:>6}"
              f"{_f(s['rate_uph'], 9, 3)}{s['rate_src']:>5}{_f(s['cover_h'], 9)}")
    ln = wip["line"]
    print(f"\n  ── 線級 ──")
    print(f"    活 lot {ln['live_total']}（排隊 {ln['queue_total']}／超齡 {ln['stale_total']}"
          f"／卡站 FAIL {ln['fail_hold_total']}）")
    print(f"    排除：死 {ln['excluded_dead']}／已出線 {ln['excluded_done']}／ASSY "
          f"{ln['excluded_assy']}／離 route {ln['excluded_off_route']}／非 B "
          f"{ln['excluded_non_b']}")
    print(f"\n  ── diag ──")
    for k in ("n_win_lots", "n_hist_rows", "n_position_lots", "n_dup_rows_removed",
              "dead_filter", "mcl_filter", "status_census", "zero_tput_with_queue",
              "zero_tput_win_d", "n_pos_null_ts", "n_pos_res_unexpected",
              "n_pos_ship_receipt", "lookback_clamped", "h_lookback_clamped",
              "route_check", "passthrough_ords", "timing",
              "degraded", "caveats", "beyond_lookback_note"):
        print(f"    {k:<24}{d.get(k)}")
    if wip.get("elapsed_s") is not None:
        print(f"    {'elapsed_s':<24}{wip['elapsed_s']}")


# =============================================================================
# 7. CLI
# =============================================================================
def _hint(e):
    low = str(e).lower()
    if isinstance(e, SchemaDivergence) or "schema divergence" in low or "invalid column" in low:
        return (" → **schema 分歧**（報表 view 少了承重欄）。不是連線問題、也不是線上沒料；"
                "先對 SPEC-DISPATCH.md §4.2 重新校 [G0] 欄位守衛，勿改數字。")
    if any(k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")):
        return " → 連不到 MES。確認已連 VPN/內網。"
    if "login failed" in low or "28000" in str(e):
        return " → 登入被拒。確認 AD 帳號對報表庫有讀權。"
    return ""


def main():
    ap = argparse.ArgumentParser(
        description="Tendline Board MES-WIP（入站排隊／可吃小時數；唯讀）")
    ap.add_argument("--days", type=int, default=None,
                    help=f"[W1] 母體窗天數（＝lookback_d；預設 {LOOKBACK_D}）")
    ap.add_argument("--cap", default=None,
                    help="as-of 上界（YYYY-MM-DD[THH:MM]）；未給＝GETDATE()")
    ap.add_argument("--age-cap-d", type=int, default=AGE_CAP_D, help="超齡界（天）")
    ap.add_argument("--rate-win-h", type=int, default=RATE_WIN_H, help="速率短窗（小時）")
    ap.add_argument("--rate-fallback-d", type=int, default=RATE_FALLBACK_D,
                    help="速率 fallback 窗（天）")
    ap.add_argument("--h-lookback-d", type=int, default=H_LOOKBACK_D,
                    help="[H1] 列拉取窗（天）")
    ap.add_argument("--json", action="store_true", help="輸出 canonical JSON")
    ap.add_argument("--selftest", action="store_true", help="離線確定性單測（不連 MES）")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    try:
        wip = run(days=args.days, cap=(_dt(args.cap) if args.cap else None),
                  age_cap_d=args.age_cap_d, rate_win_h=args.rate_win_h,
                  rate_fallback_d=args.rate_fallback_d, h_lookback_d=args.h_lookback_d)
    except Exception as e:
        print(f"[mes_wip] 查詢失敗: {type(e).__name__}{_hint(e)}\n  {str(e)[:300]}")
        sys.exit(2)

    if args.json:
        import json
        print(json.dumps(wip, ensure_ascii=False, default=str))
        return
    render(wip)


# =============================================================================
# 8. 離線單測（純合成資料、零 MES、零網路；SPEC §12 離線清單逐項）
# =============================================================================
class _FakeCur:
    """離線假 cursor：讓 `fetch_*`／`compute_wip` 整條 I/O 編排在**零網路**下可測。

    只認得本模組真正會送出的四種 SQL（guard／GETDATE／[W1]／[H1]）＋ `#temp` DDL/INSERT；
    `collate_fail_once=True` 時第一次 [H1]（#temp 側 COLLATE）拋 collation conflict＝驗回退。
    """

    def __init__(self, guard_row, win_lots, hist_rows, now=None, collate_fail_once=False):
        self.guard_row = guard_row
        self.win_lots = list(win_lots)
        self.hist_rows = list(hist_rows)
        self.now = now
        self.collate_fail_once = collate_fail_once
        self.sql_log = []
        self.temp_inserted = []
        self._next = []

    def execute(self, sql, *params):
        self.sql_log.append((sql, params))
        s = sql.strip()
        if s.startswith("SELECT CASE WHEN OBJECT_ID"):
            self._next = [tuple(self.guard_row)]
        elif s.startswith("SELECT GETDATE()"):
            self._next = [(self.now,)]
        elif s.startswith("SELECT v.matMaterialLotID"):
            self._next = [(x,) for x in self.win_lots]
        elif s.startswith("SELECT v.stpPK"):
            if self.collate_fail_once and "COLLATE DATABASE_DEFAULT" in s:
                self.collate_fail_once = False
                raise RuntimeError("Cannot resolve the collation conflict between "
                                   "'Latin1_General_100_CI_AS_SC_UTF8' and 'X'")
            self._next = [tuple(r) for r in self.hist_rows]
        else:
            self._next = []                     # #temp DDL 等
        return self

    def executemany(self, sql, seq):
        self.sql_log.append((sql, ("<executemany>", len(seq))))
        self.temp_inserted.extend(x[0] for x in seq)
        return self

    def fetchone(self):
        return self._next[0] if self._next else None

    def fetchall(self):
        return list(self._next)


def _selftest():
    print("=== mes_wip --selftest（MES-WIP 確定性語意；純離線合成資料）===")
    from mes_qlik import _T                     # 測試小工具重用（不再抄一份斷言 harness）
    t = _T()

    PH, PC, PPC = "assyModule", "assyUnit", "assyPackagedUnit"
    DA = "segDiePlacement"                                   # ord 10
    CURE = "segAttachCureOven"                    # ord 12
    PRECURE = "segPreCureVisualInspection"                # ord 11
    CAL = "segCalibrationTest"                       # ord 32
    AutoOQC = "segAutomatedOQC"       # ord 33
    CHASSIS = "segChassisAttach"            # ord 29
    ASSY = "segFinalAssembly"                         # ord 30（pass-through）
    DRYW = "segDryUnitWeightMeasurement"             # ord 31
    VPACK = "segVacuumPackInspect"            # ord 43（線尾）
    SHIP = "segShipPackaging"                             # 不在 route（出貨段）

    CAP = datetime(2026, 8, 1, 10, 0, 0)

    def H(hours):                              # cap 前 N 小時
        return CAP - timedelta(hours=hours)

    def R(pk, lot, ts, res, sgc, mcl, status="WIP"):
        return (pk, lot, ts, res, sgc, mcl, status)

    def wip_of(rows, **kw):
        return derive_wip(rows, CAP, **kw)

    def row(w, ordn):
        return w["stations"][ordn - 1]

    # ---- 0. 常數與 route 健全性（接錯＝全部作廢）------------------------------
    t.eq(len(QLIK_STATIONS), 43, "0.1 route 43 站（import 重用、非另抄）")
    t.eq([s[0] for s in QLIK_STATIONS], list(range(1, 44)), "0.2 ord 1..43")
    t.eq(len({s[1] for s in QLIK_STATIONS}), 43, "0.3 cls 互異（cls-only 降級對映無歧義）")
    t.eq(WIP_PASSTHROUGH_ORDS, (30,), "0.4 pass-through＝人工審定常數 (30,)")
    t.eq(LIVE_STATUSES, ("WIP", "Available"), "0.5 活 lot 白名單")
    t.eq(SCHEMA_ID, "board.wip.v1", "0.6 schema pin")
    t.eq(QLIK_STATIONS[29][1], ASSY, "0.7 ord30＝segFinalAssembly")
    t.eq(QLIK_STATIONS[42][1], VPACK, "0.8 ord43＝VacuumPacking（線尾）")
    tgt = _target_map(QLIK_STATIONS, WIP_PASSTHROUGH_ORDS)
    t.eq(tgt[29], 31, "0.9 target(29)=31（跳過 pass-through 30）")
    t.eq(tgt[30], 31, "0.10 target(30)=31（位置落 pass-through 時仍有下一站）")
    t.eq(tgt[42], 43, "0.11 target(42)=43")
    t.ok(tgt[43] is None, "0.12 target(43)=None（線尾無下一站）")
    t.eq(_clamp_lookback(7, 14), (14, "lookback_d=7 < age_cap_d=14 -> clamped to 14"),
         "0.13 lookback 恆 ≥ age_cap（違者 clamp＋自揭）")
    t.eq(_clamp_lookback(21, 14), (21, None), "0.14 合法 lookback 不動")
    t.eq(_clamp_h_lookback(60, 21), (60, None), "0.15 合法 h_lookback 不動")
    hl, hn = _clamp_h_lookback(60, 90)
    t.eq(hl, 90, "0.16 h_lookback < lookback → clamp 到 lookback（否則該齡帶 lot 靜默消失）")
    t.ok(hn is not None and "vanish" in hn, "0.17 h_lookback clamp 自揭具名")
    t.eq(_clamp_h_lookback(None, 21)[0], 21, "0.18 h_lookback=None → 退回 lookback（不炸）")
    t.eq(ZERO_TPUT_WIN_D, 30, "0.19 zero_tput 自揭窗＝30d 常數（非速率的 7d 窗）")

    # ---- 1. 位置＝max stpPK（rework 倒置；J0SYN14 形）-------------------------
    # CAL 復測列 stpPK 更大但**時戳更早** ⇒ 位置必須是 CAL（用時戳會誤判成 AutoOQC）。
    rows1 = [R(700, "J0SYN14", H(50), "PASS", CAL, PC),
             R(800, "J0SYN14", H(30), "FAIL", AutoOQC, PC),      # 時序上最晚？不：pk 較小
             R(900, "J0SYN14", H(40), "PASS", CAL, PC)]       # pk 最大、時戳較早
    pos1 = latest_positions(rows1)
    t.eq(pos1["J0SYN14"][I_PK], 900, "1.1 位置取 max stpPK（非最晚時戳）")
    t.eq(pos1["J0SYN14"][I_PSC], CAL, "1.2 位置站＝復測站 CAL")
    w1 = wip_of(rows1)
    t.eq(row(w1, 33)["queue_n"], 1, "1.3 CAL PASS ⇒ 排 ord33 AutoOQC 門口")
    t.eq(row(w1, 34)["queue_n"], 0, "1.4 不排到更下游")
    t.eq(w1["line"]["live_total"], 1, "1.5 一顆活 lot")
    # 同 stpPK 平手 → 確定性（不看輸入順序）
    tie = [R(5, "J0TIE", H(3), "PASS", DA, PH), R(5, "J0TIE", H(3), "PASS", CAL, PC)]
    t.eq(latest_positions(tie)["J0TIE"], latest_positions(list(reversed(tie)))["J0TIE"],
         "1.6 同 stpPK 平手＝確定性（與輸入順序無關）")

    # ---- 2. pass-through 跳站歸屬 ---------------------------------------------
    rows2 = [R(10, "J0PT", H(5), "PASS", CHASSIS, PC)]        # ord29 PASS
    w2 = wip_of(rows2)
    t.eq(row(w2, 30)["queue_n"], 0, "2.1 pass-through 站不收佇列")
    t.eq(row(w2, 31)["queue_n"], 1, "2.2 佇列跳到 ord31")
    t.ok(row(w2, 30)["passthrough"] is True, "2.3 ord30 旗標 passthrough=True")
    t.ok(row(w2, 31)["passthrough"] is False, "2.4 其餘站 passthrough=False")
    t.eq(len([s for s in w2["stations"] if s["passthrough"]]), 1, "2.5 只有一個 pass-through")
    # 位置**落在** pass-through 站（理論上 den=0，仍不可炸）
    w2b = wip_of([R(11, "J0PT2", H(5), "PASS", ASSY, PC)])
    t.eq(row(w2b, 31)["queue_n"], 1, "2.6 位置落 pass-through 站 → 照樣往下一站排")

    # ---- 3. 排除桶（逐桶＋優先序）----------------------------------------------
    rows3 = [
        R(1, "V00001", H(5), "PASS", DA, PH),                       # ① 非 B
        R(2, "J0ASSY", H(5), "PASS", DA, ASSY_MLC),                   # ② ASSY
        R(3, "J0EOL", H(5), "PASS", DA, EOL_MLC),                   # ③ line-end mcl
        R(4, "J0END", H(5), "PASS", VPACK, PPC),                    # ③ 線尾 PASS
        R(5, "J0OFF", H(5), "PASS", SHIP, PH),                      # ④ 離 route
        R(6, "J0DEAD", H(5), "PASS", DA, PH, "Scrapped"),           # ⑤ 死 lot
        R(7, "J0OK", H(5), "PASS", DA, PH, "Available"),            # 活（Available 也算活）
    ]
    w3 = wip_of(rows3)
    ln3 = w3["line"]
    t.eq(ln3["excluded_non_b"], 1, "3.1 桶①非 B")
    t.eq(ln3["excluded_assy"], 1, "3.2 桶②ASSY")
    t.eq(ln3["excluded_done"], 2, "3.3 桶③已出線（line-end mcl ＋ 線尾 PASS）")
    t.eq(ln3["excluded_off_route"], 1, "3.4 桶④離 route")
    t.eq(ln3["excluded_dead"], 1, "3.5 桶⑤死 lot")
    t.eq(ln3["live_total"], 1, "3.6 只剩一顆活")
    t.eq(row(w3, 11)["queue_n"], 1, "3.7 活的那顆排 ord11")
    t.eq(sum(v for k, v in ln3.items() if k.startswith("excluded_")) + ln3["live_total"],
         len(latest_positions(rows3)), "3.8 每顆料恰落一個桶（帳平）")
    t.eq(w3["diag"]["n_pos_ship_receipt"], 1, "3.9 出貨/收料段位置自揭")
    # 優先序：非 B 的死 lot 記在桶①（先中先出、不重複計）
    w3b = wip_of([R(1, "V0DEAD", H(5), "PASS", DA, PH, "Scrapped")])
    t.eq((w3b["line"]["excluded_non_b"], w3b["line"]["excluded_dead"]), (1, 0),
         "3.10 優先序：非 B 先於死 lot")
    # 優先序：離 route 的死 lot 記在桶④
    w3c = wip_of([R(1, "J0X", H(5), "PASS", SHIP, PH, "Scrapped")])
    t.eq((w3c["line"]["excluded_off_route"], w3c["line"]["excluded_dead"]), (1, 0),
         "3.11 優先序：離 route 先於死 lot")
    # Fresh/Sorting 不切：Sorting 再投入的 lot 照樣佔佇列
    t.eq(wip_of([R(9, "J0SORT", H(5), "PASS", DA, PH)])["line"]["live_total"], 1,
         "3.12 Fresh/Sorting 不切（與 yield 口徑刻意不同）")

    # ---- 4. 末列 FAIL＝卡站（不進任何佇列）-------------------------------------
    rows4 = [R(20, "J0F1", H(5), "FAIL", DA, PH),
             R(21, "J0F2", H(5), "FAIL", DA, PH),
             R(22, "J0P", H(5), "PASS", DA, PH)]
    w4 = wip_of(rows4)
    t.eq(row(w4, 10)["fail_hold_n"], 2, "4.1 FAIL 卡在原站（ord10）")
    t.eq(row(w4, 10)["queue_n"], 0, "4.2 卡站 FAIL 不進本站佇列")
    t.eq(row(w4, 11)["queue_n"], 1, "4.3 只有 PASS 那顆往下排")
    t.eq(row(w4, 11)["fail_hold_n"], 0, "4.4 卡站不外溢到下一站")
    t.eq(w4["line"]["fail_hold_total"], 2, "4.5 fail_hold_total")
    t.eq(w4["line"]["live_total"], 3, "4.6 卡站的仍是活 lot")
    # 線尾 FAIL ⇒ 卡在 ord43（不是 excluded_done）
    w4b = wip_of([R(30, "J0E", H(5), "FAIL", VPACK, PPC)])
    t.eq((row(w4b, 43)["fail_hold_n"], w4b["line"]["excluded_done"]), (1, 0),
         "4.7 線尾 FAIL＝卡站、非已出線")

    # ---- 5. age cap 邊界（恰 14d ±1s）＋ stale 不進 cover ------------------------
    exactly = CAP - timedelta(days=14)
    rows5 = [R(40, "J0A", exactly + timedelta(seconds=1), "PASS", DA, PH),   # 齡 < 14d
             R(41, "J0B", exactly, "PASS", DA, PH),                          # 齡 == 14d
             R(42, "J0C", exactly - timedelta(seconds=1), "PASS", DA, PH)]   # 齡 > 14d
    w5 = wip_of(rows5)
    t.eq(row(w5, 11)["queue_n"], 2, "5.1 齡 ≤14d（含恰 14d）進 queue_n")
    t.eq(row(w5, 11)["stale_n"], 1, "5.2 齡 >14d 進 stale_n（單獨計數）")
    t.eq(w5["line"]["stale_total"], 1, "5.3 stale_total")
    t.eq(w5["line"]["live_total"], 3, "5.4 超齡料**不丟棄**（仍是活 lot）")
    # cover 只用 queue_n（stale 混進去會虛胖「可吃小時」＝調度誤導）
    w5b = wip_of(rows5, rate_win_h=4, rate_fallback_d=7)
    r11 = row(w5b, 11)
    t.ok(r11["cover_h"] is None, "5.5 ord11 本身零吞吐 ⇒ cover None")
    rows5c = rows5 + [R(50 + i, f"B0R{i}", H(1), "PASS", PRECURE, PH) for i in range(4)]
    w5c = wip_of(rows5c)                      # ord11 近 4h 有 4 顆 ⇒ rate=1.0/h
    t.eq(row(w5c, 11)["rate_uph"], 1.0, "5.6 rate=4 顆/4h=1.0")
    t.eq(row(w5c, 11)["queue_n"], 2, "5.7 queue 仍 2")
    t.eq(row(w5c, 11)["cover_h"], 2.0, "5.8 cover=queue/rate（stale 不進分子）")
    t.eq(row(w5c, 11)["stale_n"], 1, "5.9 stale 照顯（不丟棄）")
    # age_cap 可調
    w5d = wip_of(rows5, age_cap_d=1)
    t.eq((row(w5d, 11)["queue_n"], row(w5d, 11)["stale_n"]), (0, 3), "5.10 age_cap 可調")

    # ---- 6. rate：distinct lot 語意 ＋ fallback 序 ------------------------------
    # 同 lot 同窗多列 ⇒ 計一次（清隊速度、不是嘗試列數）
    rows6 = [R(60, "J0S", H(1), "FAIL", DA, PH),
             R(61, "J0S", H(0.5), "FAIL", DA, PH),
             R(62, "J0S", H(0.2), "PASS", DA, PH)]
    w6 = wip_of(rows6)
    t.eq(row(w6, 10)["n_4h"], 1, "6.1 同 lot 同窗多列 → distinct 計一次")
    t.eq(row(w6, 10)["rate_uph_4h"], 0.25, "6.2 rate_4h=1/4")
    t.eq(row(w6, 10)["rate_src"], "4h", "6.3 rate_src=4h")
    # 4h 空、7d 有 → fallback
    rows6b = [R(70, "J0T", H(30), "PASS", DA, PH), R(71, "J0U", H(40), "PASS", DA, PH)]
    w6b = wip_of(rows6b)
    t.eq(row(w6b, 10)["n_4h"], 0, "6.4 近 4h 無吞吐")
    t.eq(row(w6b, 10)["n_7d"], 2, "6.5 近 7d 兩顆")
    t.eq(row(w6b, 10)["rate_src"], "7d", "6.6 fallback 到 7d")
    t.close(row(w6b, 10)["rate_uph"], 2.0 / 168.0, 1e-6, "6.7 rate_7d=2/168")
    t.ok(row(w6b, 10)["rate_uph_4h"] is None, "6.8 4h 無資料 → None（不是 0）")
    # 兩窗都空 → None/none/cover None
    w6c = wip_of([R(80, "J0V", H(24 * 20), "PASS", DA, PH)])   # 20 天前（7d 外、且已超齡）
    t.ok(row(w6c, 10)["rate_uph"] is None, "6.9 兩窗皆空 → rate None")
    t.eq(row(w6c, 10)["rate_src"], "none", "6.10 rate_src=none")
    t.eq(row(w6c, 11)["stale_n"], 1, "6.11a 20 天前的位置＝超齡（不進 queue）")
    # 有排隊、但目標站近 7d 零吞吐 ⇒ cover 不可測（顯「—」），queue_n 照顯
    w6c2 = wip_of([R(81, "J0V2", H(5), "PASS", DA, PH)])
    t.ok(row(w6c2, 11)["cover_h"] is None,
         "6.11 rate None ⇒ cover None（絕不 0、絕不用別站均速頂替）")
    t.eq(row(w6c2, 11)["queue_n"], 1, "6.12 cover 不可測不影響 queue_n 照顯")
    # rate 有值但 queue=0 → cover 0.0（不是 None）
    t.eq(row(w6, 11)["cover_h"], None, "6.13 ord11 無 rate → None")
    t.eq(row(w6, 10)["cover_h"], 0.0, "6.14 有 rate、queue=0 → cover 0.0")
    # cap 之後的列不算（as-of 半開窗）
    w6d = wip_of([R(90, "J0W", CAP, "PASS", DA, PH),
                  R(91, "J0W2", CAP + timedelta(hours=1), "PASS", DA, PH)])
    t.eq(row(w6d, 10)["n_4h"], 0, "6.15 ts >= cap 的列不進速率窗")
    # 速率窗參數可調
    w6e = wip_of(rows6, rate_win_h=2)
    t.eq(row(w6e, 10)["rate_uph_4h"], 0.5, "6.16 rate_win_h 可調（1/2）")
    # 除零防護
    w6f = wip_of(rows6, rate_win_h=0)
    t.ok(row(w6f, 10)["rate_uph_4h"] is None, "6.17 rate_win_h=0 → None（不除零）")
    t.eq(row(w6f, 10)["rate_src"], "7d", "6.18 短窗失效 → 退 7d")
    # 速率吃 (cls,mcl) 複合鍵：同 cls 掛錯 mcl 不算這站吞吐
    w6g = wip_of([R(95, "J0M", H(1), "PASS", DA, PC)])
    t.eq(row(w6g, 10)["n_4h"], 0, "6.19 route 鍵＝(cls,mcl) 複合（mcl 不符不計）")
    t.eq(w6g["line"]["excluded_off_route"], 1, "6.20 (cls,mcl) 不在 route → off_route")

    # ---- 7. matStatus 缺 → dead_filter SKIPPED ＋ status_census ------------------
    rows7 = [R(100, "J0L1", H(5), "PASS", DA, PH, "WIP"),
             R(101, "J0L2", H(5), "PASS", DA, PH, "Available"),
             R(102, "J0L3", H(5), "PASS", DA, PH, "Scrapped"),
             R(103, "J0L4", H(5), "PASS", DA, PH, "Hold"),
             R(104, "J0L5", H(5), "PASS", DA, PH, None)]
    w7 = wip_of(rows7)
    t.eq(w7["diag"]["dead_filter"], "applied", "7.1 dead_filter applied")
    t.eq(w7["diag"]["status_census"],
         {"WIP": 1, "Available": 1, "Scrapped": 1, "Hold": 1, "(null)": 1},
         "7.2 status_census 值集自揭（非白名單值現形＝Q-D9 的回填素材）")
    t.eq(w7["line"]["excluded_dead"], 3, "7.3 非白名單（含 Hold/None）→ excluded_dead")
    t.eq(row(w7, 11)["queue_n"], 2, "7.4 只有 WIP/Available 進佇列")
    w7b = wip_of(rows7, has_status=False)
    t.eq(w7b["diag"]["dead_filter"], "SKIPPED(matStatus ABSENT)", "7.5 欄缺 → SKIPPED 具名")
    t.eq(w7b["diag"]["status_census"], {}, "7.6 欄缺 → status_census {}")
    t.eq(w7b["line"]["excluded_dead"], 0, "7.7 欄缺 → 死 lot 桶不作用")
    t.eq(row(w7b, 11)["queue_n"], 5, "7.8 欄缺 → 全部進佇列（**上界**、非默默當全活）")
    t.ok(any("matStatus ABSENT" in c for c in w7b["diag"]["caveats"]), "7.9 caveat 具名")
    t.ok(w7b["diag"]["degraded"] is False,
         "7.10 dead 過濾降級**不**設 degraded（消費面走 dead_filter footer 註記）")

    # ---- 8. mcl 欄缺 → cls-only 降級對映 ＋ degraded ----------------------------
    rows8 = [R(110, "J0N", H(5), "PASS", DA, "(absent)")]
    w8a = wip_of(rows8)                          # 正常路徑：mcl 對不上 → off_route
    t.eq(w8a["line"]["excluded_off_route"], 1, "8.1 mcl 不符 → off_route（正常路徑）")
    w8b = wip_of(rows8, has_mcl=False)
    t.eq(row(w8b, 11)["queue_n"], 1, "8.2 has_mcl=False → cls-only 對映仍歸得到站")
    t.ok(w8b["diag"]["degraded"] is True, "8.3 mcl ABSENT → degraded=True（單一信任旗）")
    t.eq(w8b["diag"]["mcl_filter"], "SKIPPED(mcl ABSENT)", "8.4 mcl_filter 自揭")
    t.ok(any("mclMaterialClassID ABSENT" in c for c in w8b["diag"]["caveats"]),
         "8.5 caveat 具名（不是靜默全零板）")
    t.ok(w8a["diag"]["degraded"] is False, "8.6 mcl 在 → 不 degraded")

    # ---- 9. zero_tput_with_queue（pass-through 誤校準自揭）----------------------
    w9 = wip_of([R(120, "J0Z", H(5), "PASS", DA, PH)])     # ord11 有佇列、30d 零吞吐
    t.eq(w9["diag"]["zero_tput_with_queue"], [PRECURE], "9.1 佇列>0 且 30d 吞吐 0 → 自揭")
    t.eq(w9["diag"]["zero_tput_win_d"], ZERO_TPUT_WIN_D, "9.1b 自揭窗＝30d（進 diag 可驗）")
    w9b = wip_of([R(120, "J0Z", H(5), "PASS", DA, PH),
                  R(121, "J0Z2", H(1), "PASS", PRECURE, PH)])
    t.ok(PRECURE not in w9b["diag"]["zero_tput_with_queue"],
         "9.3 有吞吐的站不再進自揭清單")
    t.eq(w9b["diag"]["zero_tput_with_queue"], [CURE],
         "9.4 佇列往下游推進後，換下一個零吞吐站現形（清單隨佇列走、非站別黑名單）")
    # ★ 30d 窗的價值（本輪修的那條）：7d 內零吞吐、但 30d 內有量的站 **不該**被標成
    #   pass-through 嫌疑——用 7d 判會把休假週/低量偶發站都拖進來（狼來了、稀釋人工裁決訊號）。
    w9c = wip_of([R(122, "J0Z3", H(5), "PASS", DA, PH),                    # ord11 有佇列
                  R(123, "J0Z4", H(24 * 10), "PASS", PRECURE, PH)])        # 10 天前有吞吐
    t.eq(row(w9c, 11)["n_7d"], 0, "9.5 該站近 7d 確實零吞吐（速率仍退 none）")
    t.ok(PRECURE not in w9c["diag"]["zero_tput_with_queue"],
         "9.6 但 30d 內有吞吐 ⇒ **不**進自揭清單（7d 判定會誤報＝本輪修正）")
    # 列窗比自揭窗短時，窗自動縮到列窗（不假稱看了 30 天）
    w9d = wip_of([R(124, "J0Z5", H(5), "PASS", DA, PH)], h_lookback_d=10, lookback_d=10,
                 age_cap_d=7)
    t.eq(w9d["diag"]["zero_tput_win_d"], 10, "9.7 列窗 <30d → 自揭窗縮到列窗（誠實）")

    # ---- 10. 齡不可測／判定分歧（防呆分支）-------------------------------------
    w10 = wip_of([R(130, "J0T1", None, "PASS", DA, PH)])
    t.eq(row(w10, 11)["queue_n"], 1, "10.1 ts None → 齡不可測、留在 queue_n")
    t.eq(row(w10, 11)["stale_n"], 0, "10.2 ts None 不被誤判成超齡")
    t.eq(w10["diag"]["n_pos_null_ts"], 1, "10.3 n_pos_null_ts 自揭")
    w10b = wip_of([R(131, "J0T2", H(5), "UNKNOWN", DA, PH)])
    t.eq(w10b["diag"]["n_pos_res_unexpected"], 1, "10.4 非 PASS/FAIL 判定自揭")
    t.ok(w10b["diag"]["degraded"] is True, "10.5 判定分歧 → degraded")
    t.eq((row(w10b, 11)["queue_n"], row(w10b, 10)["fail_hold_n"]), (0, 0),
         "10.6 分歧列不進佇列也不進卡站（不猜）")
    t.eq(w10b["line"]["live_total"], 1, "10.7 但仍計為活 lot（帳要平）")

    # ---- 11. canonical 形狀（鍵全集＋不變式）------------------------------------
    w11 = wip_of([])
    t.eq(tuple(w11.keys()), CANONICAL_KEYS, "11.1 頂層鍵全集＋順序")
    t.eq(w11["schema"], SCHEMA_ID, "11.2 schema pin")
    t.eq(w11["caliber"], CALIBER_WIP, "11.3 caliber pin")
    t.ok(w11["ok"] is True, "11.4 ok=True（失敗一律 raise）")
    t.eq(w11["cap"], "2026-08-01T10:00:00", "11.5 cap＝naive 台北牆鐘 isoformat 字串")
    t.eq(sorted(w11["params"]), sorted(["age_cap_d", "rate_win_h", "rate_fallback_d",
                                        "lookback_d", "h_lookback_d"]), "11.6 params 鍵")
    t.eq(len(w11["stations"]), 43, "11.7 空輸入仍 43 列（不藏站）")
    t.eq([s["ord"] for s in w11["stations"]], list(range(1, 44)), "11.8 ord 升冪 1..43")
    t.eq(tuple(w11["stations"][0].keys()), STATION_KEYS, "11.9 station 鍵全集＋順序")
    t.eq(tuple(w11["line"].keys()), LINE_KEYS, "11.10 line 鍵全集＋順序")
    t.ok(all(s["queue_n"] == 0 and s["stale_n"] == 0 and s["fail_hold_n"] == 0
             for s in w11["stations"]), "11.11 空輸入全零")
    t.ok(all(s["rate_uph"] is None and s["cover_h"] is None for s in w11["stations"]),
         "11.12 空輸入 rate/cover 全 None（不是 0）")
    t.ok(all(s["rate_src"] == "none" for s in w11["stations"]), "11.13 rate_src=none")
    t.eq(w11["stations"][9]["display"], QLIK_DISPLAY[10], "11.14 display 取自 QLIK_DISPLAY")
    t.ok(w11["elapsed_s"] is None, "11.15 純函式路徑 elapsed_s=None（不假造耗時）")
    t.ok(w11["diag"]["degraded"] is False, "11.16 空盤不算 degraded")
    t.eq(w11["diag"]["route_check"], "ok", "11.17 route_check ok")
    t.ok("age_cap=14d" in w11["diag"]["beyond_lookback_note"], "11.18 齡帶邊界誠實聲明")
    for w, lbl in ((w1, "c1"), (w3, "c3"), (w4, "c4"), (w5c, "c5c"), (w6b, "c6b"),
                   (w7, "c7"), (w10b, "c10b")):
        st = w["stations"]
        t.eq(w["line"]["live_total"],
             sum(s["queue_n"] + s["stale_n"] + s["fail_hold_n"] for s in st)
             + w["diag"]["n_pos_res_unexpected"], f"11.19 live_total 帳平（{lbl}）")
        t.eq(w["line"]["queue_total"], sum(s["queue_n"] for s in st),
             f"11.20 queue_total 帳平（{lbl}）")
        t.ok(all((s["cover_h"] is None) == (s["rate_uph"] is None) for s in st),
             f"11.21 cover None ⇔ rate None（{lbl}）")
        t.ok(all(s["queue_n"] >= 0 and s["stale_n"] >= 0 for s in st),
             f"11.22 計數非負（{lbl}）")
        t.eq(len(st), 43, f"11.23 恆 43 列（{lbl}）")
    # route 接錯 → 自檢報錯＋degraded
    w11b = wip_of([], route=QLIK_STATIONS[:5])
    t.ok(w11b["diag"]["route_check"].startswith("!!"), "11.24 route 接錯 → 自檢報錯")
    t.ok(w11b["diag"]["degraded"] is True, "11.25 route 接錯 → degraded")
    t.eq(len(w11b["stations"]), 5, "11.26 route 短 → 列數跟著（不假造 43 列）")

    # ---- 12. SQL builder 形狀（perf lore 逐條可斷言）-----------------------------
    s_guard = _sql_guard()
    s_w1 = _sql_recent_lots(True)
    s_h1 = _sql_lot_rows(True, True, "temp")
    s_h1v = _sql_lot_rows(True, True, "view")
    s_h1n = _sql_lot_rows(False, False, "temp")
    for name, s in (("guard", s_guard), ("W1", s_w1), ("H1", s_h1), ("H1-view", s_h1v)):
        t.ok(s.rstrip().endswith("OPTION (RECOMPILE)"), f"12.1 {name} 尾 RECOMPILE")
        t.ok("DISTINCT" not in s.upper(), f"12.2 {name} 無 DISTINCT（去重在 Python）")
        t.ok("GROUP BY" not in s.upper(), f"12.3 {name} 無 GROUP BY（聚合在 Python）")
        t.ok("stpEndTime" not in s, f"12.4 {name} 不碰 stpEndTime")
    t.ok(LOTS_TEMP in s_h1, "12.5 [H1] 走 #temp join")
    t.ok("matMaterialLotID IN (" not in s_h1, "12.6 [H1] **無** lot IN-list（B4 鐵則）")
    t.ok("COLLATE DATABASE_DEFAULT" in s_h1, "12.7 COLLATE 掛 #temp 側（保 view 側 seek）")
    t.ok("COLLATE Latin1_General_100_CI_AS_SC_UTF8" in s_h1v, "12.8 回退＝view 側 COLLATE")
    t.ok("dbo.process_segment_v" in s_h1 and s_h1.count("FROM ") == 1,
         "12.9 [H1] 單 view（不雙展開＝B4 教訓）")
    t.ok("v.stpStartTime >= ? AND v.stpStartTime < ?" in s_h1, "12.10 半開窗、全參數化")
    t.ok(_sql_list(RESULT_KEEP) in s_w1 and _sql_list(LOT_PREFIXES) in s_w1,
         "12.11 [W1] Process01 過濾逐字重用")
    t.ok(_sql_list(MLC_WHITELIST) in s_w1 and _sql_list(EXCLUDED_CLASSES) in s_w1,
         "12.12 [W1] mcl 白名單＋排除站清單在")
    t.ok(_sql_list(MLC_WHITELIST) not in _sql_recent_lots(False),
         "12.13 has_mcl=False → mcl 過濾整條拿掉")
    t.ok("N'(absent)'" in s_h1n and "CAST(NULL AS nvarchar(64))" in s_h1n,
         "12.14 欄缺 → 佔位值（形狀不變、欄位不消失）")
    t.ok("'" not in s_guard.replace("'dbo.process_segment_v'", "")
         .replace("'mclMaterialClassID'", "").replace("'matStatus'", "")
         .replace("".join(f"'{c}'" for c in ()), "")
         .replace("'stpPK'", "").replace("'stpStartTime'", "")
         .replace("'matMaterialLotID'", "").replace("'stpTestResult'", "")
         .replace("'SegmentClassID'", ""),
         "12.15 guard SQL 只含常數欄名（無自由字串）")
    try:
        _ident("stpPK; DROP TABLE x --")
        t.ok(False, "12.16 _ident 應拒絕非識別字")
    except ValueError:
        t.ok(True, "12.16 _ident 拒絕非識別字")

    # ---- 13. [G0] 守衛（承重 raise／可降級不 raise）------------------------------
    good = {k: True for k in GUARD_KEYS}
    t.ok(assert_wip_guard(good) is True, "13.1 全欄在 → 通過")
    for col in REQUIRED_SEG_COLUMNS:
        try:
            assert_wip_guard(dict(good, **{col: False}))
            t.ok(False, f"13.2 承重欄 {col} ABSENT 應 raise")
        except SchemaDivergence as e:
            t.ok(col in str(e), f"13.2 承重欄 {col} ABSENT → SchemaDivergence（具名）")
    try:
        assert_wip_guard(dict(good, view_seg=False))
        t.ok(False, "13.3 view ABSENT 應 raise")
    except SchemaDivergence:
        t.ok(True, "13.3 process_segment_v ABSENT → SchemaDivergence")
    t.ok(assert_wip_guard(dict(good, matStatus=False, mclMaterialClassID=False)) is True,
         "13.4 兩個可降級欄 ABSENT **不** raise（走降級分支）")
    t.ok("schema" in _hint(SchemaDivergence("schema divergence: stpPK ABSENT")).lower(),
         "13.5 _hint 涵蓋 schema 分歧（不誤導成連線問題）")

    # ---- 14. compute_wip 整條編排（假 cursor、零網路）-----------------------------
    guard_row = (1, 1, 1) + tuple(1 for _ in REQUIRED_SEG_COLUMNS)
    hist = [R(200, "J0AA", H(2), "PASS", DA, PH),
            R(201, "J0BB", H(3), "PASS", CURE, PH),
            R(202, "J0CC", H(1), "FAIL", DA, PH),
            R(203, "V0DD", H(1), "PASS", DA, PH)]
    fc = _FakeCur(guard_row, ["J0AA", "J0BB", "J0CC", "V0DD"], hist, now=CAP)
    w14 = compute_wip(fc, CAP)
    t.eq(w14["schema"], SCHEMA_ID, "14.1 compute_wip 回 canonical")
    t.eq(row(w14, 11)["queue_n"], 1, "14.2 B0AA（DA PASS）排 ord11")
    t.eq(row(w14, 13)["queue_n"], 1, "14.3 B0BB（Cure PASS）排 ord13")
    t.eq(row(w14, 10)["fail_hold_n"], 1, "14.4 B0CC 卡在 ord10")
    t.eq(w14["line"]["excluded_non_b"], 1, "14.5 M0DD 排除")
    t.eq(w14["diag"]["n_win_lots"], 4, "14.6 [W1] lot 數進 diag")
    t.eq(w14["diag"]["n_hist_rows"], 4, "14.7 [H1] 列數進 diag")
    t.eq(sorted(fc.temp_inserted), ["J0AA", "J0BB", "J0CC", "V0DD"], "14.8 lot 走 #temp 灌入")
    t.ok(w14["elapsed_s"] is not None and w14["elapsed_s"] >= 0, "14.9 elapsed_s 有值")
    t.ok(all(k in w14["diag"]["timing"] for k in ("guard_ms", "w_ms", "h_ms", "derive_ms",
                                                  "total_ms")), "14.10 timing 分段")
    t.eq(w14["diag"]["guard"]["matStatus"], True, "14.11 guard 進 diag")
    t.eq(w14["diag"]["collate_side"], "temp", "14.12 collate_side 自揭")
    t.eq(fc.sql_log[0][0], _sql_guard(), "14.13 第一條 SQL＝column guard")
    win_sql = [s for s, _p in fc.sql_log if s.startswith("SELECT v.matMaterialLotID")]
    t.eq(len(win_sql), 1, "14.14 [W1] 只跑一次（兩段式母體收斂）")
    hist_sql = [s for s, _p in fc.sql_log if s.startswith("SELECT v.stpPK")]
    t.eq(len(hist_sql), 1, "14.15 [H1] 只跑一次")
    w1_params = [p for s, p in fc.sql_log if s.startswith("SELECT v.matMaterialLotID")][0]
    t.eq(w1_params, (CAP - timedelta(days=LOOKBACK_D), CAP), "14.16 [W1] 窗＝lookback_d")
    h1_params = [p for s, p in fc.sql_log if s.startswith("SELECT v.stpPK")][0]
    t.eq(h1_params, (CAP - timedelta(days=H_LOOKBACK_D), CAP), "14.17 [H1] 窗＝h_lookback_d")
    # cap=None → 走 GETDATE()
    fc2 = _FakeCur(guard_row, ["J0AA"], [R(200, "J0AA", H(2), "PASS", DA, PH)], now=CAP)
    t.eq(compute_wip(fc2, None)["cap"], CAP.isoformat(), "14.18 cap=None → GETDATE() 一次")
    # collation conflict → view 側回退
    fc3 = _FakeCur(guard_row, ["J0AA"], [R(200, "J0AA", H(2), "PASS", DA, PH)],
                   now=CAP, collate_fail_once=True)
    w14c = compute_wip(fc3, CAP)
    t.eq(w14c["diag"]["collate_side"], "view", "14.19 collation conflict → view 側回退")
    t.eq(row(w14c, 11)["queue_n"], 1, "14.20 回退後結果一致")
    # 非 collation 的例外照樣往上拋（不吞錯）
    fc4 = _FakeCur(guard_row, ["J0AA"], [], now=CAP)

    def _boom(sql, *p):
        if sql.strip().startswith("SELECT v.stpPK"):
            raise RuntimeError("Invalid column name 'stpPK'")
        return _FakeCur.execute(fc4, sql, *p)
    fc4.execute = _boom
    try:
        compute_wip(fc4, CAP)
        t.ok(False, "14.21 非 collation 例外應往上拋")
    except RuntimeError as e:
        t.ok("Invalid column" in str(e), "14.21 非 collation 例外往上拋（不吞錯）")
    # 承重欄 ABSENT → SchemaDivergence（整條停下、不產全零板）
    bad_row = (1, 1, 1) + tuple(0 if c == "stpPK" else 1 for c in REQUIRED_SEG_COLUMNS)
    try:
        compute_wip(_FakeCur(bad_row, [], [], now=CAP), CAP)
        t.ok(False, "14.22 承重欄缺應 raise")
    except SchemaDivergence:
        t.ok(True, "14.22 承重欄缺 → SchemaDivergence（不產全零板）")
    # [W1] 空集合 → 不建 #temp、不跑 [H1]、回全零板（合法空線）
    fc5 = _FakeCur(guard_row, [], [], now=CAP)
    w14e = compute_wip(fc5, CAP)
    t.eq(w14e["line"]["live_total"], 0, "14.23 無活 lot → 全零板")
    t.eq(len([s for s, _p in fc5.sql_log if s.startswith("SELECT v.stpPK")]), 0,
         "14.24 無 lot → 不跑 [H1]（省一趟）")
    t.eq(fc5.temp_inserted, [], "14.25 無 lot → 不灌 #temp")
    # matStatus 欄 ABSENT → 降級但不炸
    fc6 = _FakeCur((1, 1, 0) + tuple(1 for _ in REQUIRED_SEG_COLUMNS), ["J0AA"],
                   [R(200, "J0AA", H(2), "PASS", DA, PH, None)], now=CAP)
    w14f = compute_wip(fc6, CAP)
    t.eq(w14f["diag"]["dead_filter"], "SKIPPED(matStatus ABSENT)", "14.26 欄缺 → 降級具名")
    t.ok("CAST(NULL AS nvarchar(64))" in
         [s for s, _p in fc6.sql_log if s.startswith("SELECT v.stpPK")][0],
         "14.27 欄缺 → SQL 用佔位值（不查不存在的欄）")
    # lookback < age_cap → clamp（母體窗不得比超齡界還短）
    fc7 = _FakeCur(guard_row, [], [], now=CAP)
    w14g = compute_wip(fc7, CAP, lookback_d=3, age_cap_d=14)
    t.eq(w14g["params"]["lookback_d"], 14, "14.28 lookback<age_cap → clamp 到 age_cap")
    t.eq([p for s, p in fc7.sql_log if s.startswith("SELECT v.matMaterialLotID")][0],
         (CAP - timedelta(days=14), CAP), "14.29 clamp 後的窗真的送進 [W1]")
    t.ok(w14g["diag"]["lookback_clamped"] is not None,
         "14.30 clamp 自揭真的進 diag（live 路徑；derive 自己重算恆 None ⇒ 必須回填）")
    # ★ env 誤配面：lookback(90) > h_lookback(60) → [H1] 列窗必須跟著撐大，
    #   否則只在 (60,90] 有活動的 lot 一列都拉不到＝靜默消失（本輪修的 MED）。
    fc8 = _FakeCur(guard_row, ["J0AA"], [R(200, "J0AA", H(2), "PASS", DA, PH)], now=CAP)
    w14h = compute_wip(fc8, CAP, lookback_d=90, h_lookback_d=60, age_cap_d=14)
    t.eq(w14h["params"]["lookback_d"], 90, "14.31 lookback=90 照用（≥ age_cap）")
    t.eq(w14h["params"]["h_lookback_d"], 90, "14.32 h_lookback 被撐到 90（≥ 母體窗）")
    t.eq([p for s, p in fc8.sql_log if s.startswith("SELECT v.stpPK")][0],
         (CAP - timedelta(days=90), CAP), "14.33 撐大後的窗真的送進 [H1]（無 lot 靜默消失）")
    t.ok(w14h["diag"]["h_lookback_clamped"] is not None,
         "14.34 h_lookback clamp 大聲自揭（不是靜默調參）")
    t.ok(f"lookback={90}d" in w14h["diag"]["beyond_lookback_note"],
         "14.35 齡帶聲明用真實齡帶（不再宣稱一個拉不到列的窗）")
    fc9 = _FakeCur(guard_row, ["J0AA"], [R(200, "J0AA", H(2), "PASS", DA, PH)], now=CAP)
    w14i = compute_wip(fc9, CAP)
    t.ok(w14i["diag"]["h_lookback_clamped"] is None and w14i["diag"]["lookback_clamped"] is None,
         "14.36 預設值（21≤60）不觸發任何 clamp（逐位元同舊行為）")

    # ---- 15. 人名紅線（§24 延伸；SPEC §9.1）--------------------------------------
    # 合成輸入刻意帶「人名欄」——本模組的列形狀只有 7 欄，多餘欄位結構上不可能進輸出。
    with_names = [tuple(list(R(300, "J0HR", H(2), "PASS", DA, PH)) + ["王小明", "EMP0042"]),
                  tuple(list(R(301, "J0HR2", H(2), "PASS", DA, PH)) + ["李小華", "EMP0043"])]
    w15 = derive_wip(with_names, CAP)
    blob = repr(w15)
    t.ok("王小明" not in blob and "李小華" not in blob and "EMP00" not in blob,
         "15.1 輸出零人名/工號（多餘欄位結構上不進 canonical）")
    t.eq(row(w15, 11)["queue_n"], 2, "15.2 帶人名欄的輸入照樣算得出人數以外的東西")
    t.eq(ROW_ARITY, 7, "15.3 列形狀恆 7 欄（人員欄無處可進）")
    t.ok(all("head" not in k and "opr" not in k.lower() and "name" not in k.lower()
             for s in w15["stations"] for k in s),
         "15.4 station 鍵無人力欄（v1＝消費面佔位，§9.2）")

    # ---- 16. 輔助函式 -----------------------------------------------------------
    t.eq(_r(None, 3), None, "16.1 _r None-safe")
    t.eq(_r(1 / 3, 3), 0.333, "16.2 _r 捨入")
    t.eq(_as_dt("2026-08-01T10:00"), CAP, "16.3 _as_dt 字串")
    t.ok(_as_dt(None) is None, "16.4 _as_dt None")
    t.eq(_as_dt(CAP), CAP, "16.5 _as_dt datetime 直通")
    t.eq(_row_key("a", "b", True), ("a", "b"), "16.6 route 鍵＝(cls,mcl)")
    t.eq(_row_key("a", "b", False), "a", "16.7 降級鍵＝cls")
    t.eq(_dedup_lots_ci(["J0A", "j0a", "J0B"]), ["J0A", "J0B"],
         "16.8 #temp CI 去重（重用 mes_qlik）")
    t.eq(len(_dedup_rows([R(1, "A", CAP, "PASS", DA, PH), R(1, "A", CAP, "PASS", DA, PH)])),
         1, "16.9 完全重複列去重（重用 mes_qlik）")
    t.eq(latest_positions([("x",)]), {}, "16.10 短列丟棄（防呆）")
    t.eq(latest_positions([R(None, "J0", CAP, "PASS", DA, PH)]), {}, "16.11 stpPK None 丟棄")

    # ---- 17. render 呈現層 smoke（輸出丟棄；防 f-string 格式 bug 在 live 才炸）------
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        render(w14)          # 正常盤
        render(w11b)         # degraded（route 接錯）
        render(w7b)          # dead_filter 降級
        render(w11)          # 空盤（全 None）
    out = buf.getvalue()
    t.ok("MES-WIP" in out and SCHEMA_ID in out, "17.1 render 不炸、標頭有 schema")
    t.ok("—" in out, "17.2 不可測欄顯「—」（絕不 0）")
    t.ok("DEGRADED" in out, "17.3 degraded 大聲印出")
    t.ok("死 lot 過濾降級" in out, "17.4 dead_filter 降級大聲印出")
    t.ok("·跳站" in out, "17.5 pass-through 站照列＋跳站註記（不藏站）")
    t.ok("王小明" not in out and "EMP00" not in out, "17.6 render 亦零人名（§24）")

    print(f"\n  斷言 {t.n} 條、失敗 {len(t.bad)} 條")
    for b in t.bad:
        print(f"    FAIL  {b}")
    print(f"\n  → {'PASS ✅' if not t.bad else 'FAIL ❌'}")
    if t.bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
