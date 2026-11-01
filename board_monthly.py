# -*- coding: utf-8 -*-
"""
board_monthly.py — 月報（生產組）的**月聚合核心**。規格＝`SPEC-MONTHLY.md`（WP-M1）。

**回答的問題**：這個「日曆月」裡，每一站少做了幾 PH——分成**停機當量／良率損失／重工**三種、
同一個貨幣（PH＝module），可排序、可比較。資料＝日帳（`_state/daily_ledger/*.json`）的
**日曆月**聚合，**零 MES、零 cmms、零 HTTP**。

**不是什麼**：不是 `/daily`（昨天一天）、不是 `/rework`（14–60 天滾動窗）、不是稼動頁。
本檔**只做模型**（純函式）；頁面、i18n、CSV 是 WP-M2 的事。

★ 本檔的 I/O 只有兩支：`write_snapshot` / `read_snapshot`（月快照，§3.5）。其餘全部純函式。

★ 鐵則（SPEC-MONTHLY §8；違反即 HIGH）
  C-MR1 **一把尺**：A／U 只走應生產時段軸 ⇒ A／U／OEE **只吃日帳 v4 的 `rw` 區塊**
        （`planned_sec`／`avail_down_sec`／`producing_sec`；v5 起換尺，見 RW_SEC_FIELDS）。日帳的 24h 軸欄
        （`producing_h`／`total_mh`）**一秒都不得**以 A、U 或 OEE 之名上頁；
        沒有 `rw` 的日**整天不進分母**（不是以 24h 補），沒有任何 `rw` 的站三欄恆 None。
  C-MR2 **跨站不 Σ 產出／良率**：`line` 只有 `rty_pct`（43 因子連乘），**沒有**「全站良率」。
        Σ 的只有**損失**（fail／rework／停機當量）——損失是可加的，良率不是。
  C-MR3 **無資料 `None`、絕不 0**：無尺站 `down_equiv_ph is None`；不可測站時間桶全 `None`。
  C-MR4 **當月＝部分月**：`partial=True`、**不寫快照**。
  C-MR5 **站清單不硬編**：站別、六態 key、43 站序**全部從資料／呼叫端來**，本檔零硬編清單。
  C-MR6 假設必印：`ASSUMPTIONS` 三句（月尺兩句＋as-of 一句）＝唯一一份，模板引用不重打。
  C-MR8 **快照凍結**：已存在 ⇒ 不重寫（回 `(False, "exists")`）；口徑改版＝換 `schema_id`。

★ 單位鐵則（Chris 2026-09-02 當場糾正）：數量單位一律 **PH**（module）——**不用「顆」／
  「pcs」**。`UNIT = "PH"`，欄位名一律 `*_ph`。

★ 「損失」頭數字＝**三損失合計**（停機當量＋良率損失＋重工）並附拆分（Chris 2026-09-02）。
  ⇒ `loss_total_ph` 是排序貨幣；無尺站算不出停機當量 ⇒ `loss_total_ph is None`、
  另給 `loss_total_excl_equiv_ph`（＝良率＋重工，**誠實的部分和**，不是假裝的合計）。

★ 唯一被允許的 `board_*` 相依＝**`board_oee`**（WP-M3／C4 起）：A↔U 的三行式子
  （`rw_split`）是 `/station`／`/equipment` 已經在用的**唯一出處**（SPEC-RUNWINDOW §6.7），
  月級再手抄一次就是第二把尺。`board_oee` 自己只 import `random`／`sys`（零 `mes_*`、零 I/O）
  ⇒ 下面那根「不得有 mes_*」的釘子照樣成立，selftest 另有一根釘死**只准這一支**。

★ 為什麼**不 import** `board_attrib` / `board_unit`（明明有 `pareto30` / `pareto30_units`）
  ① 窗語意不同：那兩支的窗是「`end_day` 往回 N 天」（`board_attrib.pareto30`），本檔要
     **日曆月**——用 `days=當月天數, end_day=月末` 薄包看似可行，但月初那幾天會**吃到上個月**
     （2 月 28 天／3 月 31 天各差一天，還會被 SPEC §9 A4「用滾動窗冒充月」踩中）。
  ② 那條 import 鏈會拖進 `mes_qlik` / `mes_wip` / `mes_timeline`（凍結模組、且 Claude 側
     `.claude/settings.json` 禁止執行連 MES 的東西）⇒ 本檔 selftest 有一根**釘子**：
     `import board_monthly` 之後 `sys.modules` 裡**不得**出現任何 `mes_*`。
  ③ 代價＝聚合迴圈在本檔重寫一次（~40 行）。**口徑沒有第二份**：欄位名、桶名、note key
     全部沿用日帳文件本身的鍵（日帳形狀的權威＝`board_attrib` 檔頭），本檔不另造名詞。
  月切語意借 `_probe_cost_rollup.month_key/month_start/month_add`（生產月＝生產日的月份；
  日帳的 `day` **本來就是生產日** ⇒ 月鍵＝`day[:7]`，不需要再算 08:00 日界）。

CLI：`python board_monthly.py --selftest`（純離線、零 MES、零真日帳）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
import tempfile
from calendar import monthrange

import board_oee as _oee          # ★ 唯一允許的 board_* 相依（A↔U 三行口徑的單一出處）
import core_source as _wrs            # 來源值／進程旗標唯一出處（零相依；快照來源隔離 Q-C）

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


# =============================================================================
# 1. 常數（**集中檔頭**；改這裡＝改口徑，要走 SPEC 修訂）
# =============================================================================
SCHEMA_ID = "board.monthly_ledger.v1"          # 月快照 schema（C-MR8：改口徑＝換 id、不原地改）
UNIT = "PH"                                 # 數量單位（module）——全檔、全頁一律用它
LEDGER_PREFIX = "board.daily_ledger."          # 吃得下的日帳版本（v1/v2/v3…；加法相容）
# ★ #98 家族拆分的**顯示鍵序**（R→A→M→未標）。語意權威＝`mes_qlik.family_keys()`
#   （＝`mes_family.FAMILIES` ＋ `mes_qlik.FAMILY_UNLABELED`）；本檔**不 import 那兩支**
#   （檔頭第 ② 條：那條鏈會拖進 `mes_*`）⇒ 以 `run_smoke` 的 cross-pin 保證逐位元一致，
#   同 `mes_family` 對 `board_tags.FAMILIES` 的作法。
FAMILY_ORDER = ("Plover", "Kestrel", "Heron", "UNLABELED")

# 月尺（SPEC §3.2）：該站**該月**合格生產日的 `r_good` 中位數。
RATE_MIN_PRODUCING_H = 2.0                  # 合格日門檻（沿用 app.EQM_RATE_MIN_PRODUCING_H）
RATE_MIN_DAYS = 5                           # 合格日 < 5 ⇒ 無尺（顯「—」、**絕不外推**）

# 日帳站列的四桶欄位名（**日帳文件的鍵，不是本檔發明的名詞**）
BUCKET_FIELDS = (("down_h", "wo_union_h"), ("starved_h", "starved_h"),
                 ("blocked_h", "blocked_h"), ("unexplained_h", "unexplained_h"))

# 日帳站列的 `note`＝「這一站當時的 timeline **讀不到**」（SQL 失敗／預算跳站）。
#   ⚠ 口徑權威＝`board_attrib.NOTE_SRC_ERR`，但本檔**不得 import 那支**（檔頭理由②：
#     那條 import 鏈會拖進 `mes_*`，selftest 有釘）⇒ 沿用**日帳文件自己的鍵**，同
#     `BUCKET_FIELDS`／`RW_SEC_FIELDS` 的作法：這裡寫的是「檔上長什麼樣」，不是新名詞。
NOTE_SRC_ERR = "src_err"

# 日帳 `rw` 區塊的三個秒欄（**日帳文件的鍵**；形狀權威＝`board_attrib` 檔頭）
#   ★ 2026-09-04（`SPEC-CAPACITY.md` §3.1）：停機欄由 v4 的 `health_down_sec`（窄稼動＝
#     只扣維修＋計畫保養）換成 v5 的 **`avail_down_sec`**（妥善率＝全部計停機工單）。
#   ⚠ **兩把尺一律不混算**：v4 檔沒有新欄 ⇒ 那一天**整天不進 A／U 的分母**，改記進
#     `coverage.n_days_rw_legacy` 誠實揭露（C-MR8「改口徑＝換 id、不原地改」）。
#     混算的話同一欄會半個月一把尺、而且**沒有任何測試會紅**。
RW_SEC_FIELDS = ("planned_sec", "avail_down_sec", "producing_sec")

# 站級／線級新增的 A／U／OEE 欄（**同時也是舊快照的回填清單**，見 `_fill_rw_defaults`）
RW_STATION_KEYS = ("n_days_rw", "planned_h", "health_down_h", "producing_rw_h",
                   "a_pct", "u_pct", "oee_pct", "rw_note")
RW_LINE_KEYS = RW_STATION_KEYS + ("n_stations_rw",)

# ★ W1／W3 座欄（`SPEC-CAPACITY-FORECAST.md` §2）——**全部由日帳 `rw` 推出來**，所以
#   ① 剝掉 `rw` 的負斷言把它們與 `RW_STATION_KEYS` 一視同仁；
#   ② 舊快照（#83 之前凍的）回填 `None`＝「沒算過」，**不是** 0、不是「零長停」（C-MR3）。
#   `equiv_factor` 例外：舊快照補 None，讀端把 None 當 1.0 用（那才是接線前的數字）。
#   ⚠ **`equiv_factor == 1.0` 與 `is None` 不是同一件事**：1.0＝「這個月的確乘了 1」（可能真的
#     零座長停，也可能整月都是舊日帳）；None＝整月零停機小時、根本沒有可加權的東西。
#     要分辨「量過沒有」看 `equiv_factor_days`／`equiv_factor_days_legacy` 這兩個計數。
SEAT_STATION_KEYS = ("equiv_factor", "equiv_factor_days", "equiv_factor_days_legacy",
                     "seats_installed", "seats_active", "seats_dormant",
                     "seats_legacy", "seats_days_legacy")

# 排序層級（SPEC §4.2）：有尺 → 無尺（以停機小時退場） → 結構性不可測
TIER_RATED, TIER_NO_RATE, TIER_UNMEASURABLE = 0, 1, 2

# C-MR6：頁尾常駐這三句（**唯一一份**；模板引用、不重打）
ASSUMPTIONS = {
    "en": (
        "Downtime equivalent = downtime hours x that station's median good-unit rate for "
        "this month (PH per producing machine-hour) x affected seats / installed seats.",
        "Backfill by another machine on the same station is not modelled, so the "
        "equivalent is an upper-bound estimate.",
        "Yield loss = sum of each day's as-of-next-morning first-FAIL units (PH).",
    ),
    "zh": (
        "停機當量＝停機小時 × 該站當月良品速率中位數（PH／生產機台小時）× 受影響座數÷安裝座數。",
        "未計同站他機補位 ⇒ 當量是上限估算。",
        "良率損失＝各日 as-of 次日的 first-FAIL 落點 PH 數加總。",
    ),
}


def _env_float(name, default):
    try:
        v = float(str(os.environ.get(name, "")).strip())
        return v if v > 0 else default
    except Exception:
        return default


def _env_int(name, default):
    try:
        v = int(str(os.environ.get(name, "")).strip())
        return v if v > 0 else default
    except Exception:
        return default


RATE_MIN_PRODUCING_H = _env_float("BOARD_MONTHLY_RATE_MIN_PRODUCING_H", RATE_MIN_PRODUCING_H)
RATE_MIN_DAYS = _env_int("BOARD_MONTHLY_RATE_MIN_DAYS", RATE_MIN_DAYS)


def _r6(v):
    return None if v is None else round(float(v), 6)


# =============================================================================
# 2. 月／日曆算術（純算術；語意借 `_probe_cost_rollup.month_key/month_start/month_add`）
# =============================================================================
def month_of(day):
    """生產日字串 `"YYYY-MM-DD"` → 生產月鍵 `"YYYY-MM"`；壞形 → None。

    ★ 日帳的 `day` **已經是生產日**（日界 08:00 在寫帳時就切好了）⇒ 月鍵就是前七碼，
      不必也**不可以**再套一次 `production_day()`（會把 08:00 前的東西再退一天＝切兩次）。
    """
    d = _as_date(day)
    return None if d is None else f"{d.year:04d}-{d.month:02d}"


def _as_date(v):
    """`date`/`datetime`/`"YYYY-MM-DD"`（容許帶時間） → `date`；壞形 → None。"""
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    s = str(v or "").strip()
    if len(s) < 10:
        return None
    try:
        return _dt.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (TypeError, ValueError):
        return None


def month_add(month, n):
    """月鍵 ± n 個月（純算術，不碰日曆庫）＝`_probe_cost_rollup.month_add` 同一條式子。"""
    y, m = int(str(month)[:4]), int(str(month)[5:7])
    t = (y * 12 + (m - 1)) + int(n)
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def month_first_day(month):
    """月鍵 → 該月 1 日（`date`）。"""
    y, m = int(str(month)[:4]), int(str(month)[5:7])
    return _dt.date(y, m, 1)


def month_last_day(month):
    """月鍵 → 該月最後一日（`date`）——**31／30／28／29 由 `monthrange` 決定，不硬編**。"""
    y, m = int(str(month)[:4]), int(str(month)[5:7])
    return _dt.date(y, m, monthrange(y, m)[1])


def month_days(month):
    """月鍵 → 該月每一天的 `"YYYY-MM-DD"`（升冪）。"""
    y, m = int(str(month)[:4]), int(str(month)[5:7])
    return [f"{y:04d}-{m:02d}-{d:02d}" for d in range(1, monthrange(y, m)[1] + 1)]


def last_settled_day(today):
    """`today` → **最新一個「日帳應該已經落地」的生產日**＝`today − 1`。

    日帳是隔天早上落的（as-of 次日，SPEC §3.3）⇒ 今天這天的帳**還不存在**，
    把它算進「應該有」會讓涵蓋率永遠差一天。"""
    t = _as_date(today)
    return None if t is None else (t - _dt.timedelta(days=1))


def as_of_day(month, today):
    """該月**涵蓋到哪一天**＝`min(月末, today−1)`；整個月都還沒開始 → None。"""
    t = last_settled_day(today)
    if t is None:
        return None
    end = min(month_last_day(month), t)
    return end.isoformat() if end >= month_first_day(month) else None


def is_partial(month, today):
    """這個月還沒過完嗎（C-MR4）？

    ★ **完整**的條件是 `月末 < today − 1`，不是 `月末 < today`：月末那天的日帳是**次日**才落的，
      在 `today == 月末+1` 就宣告完整＝可能在最後一天的帳落地**之前**把快照凍結掉
      （C-MR8 凍結後不重算 ⇒ 那一天永遠補不回來）。寧可晚一天。
    """
    t = last_settled_day(today)
    if t is None:
        return True                          # today 壞形 ⇒ 保守當部分月（不寫快照）
    return month_last_day(month) >= t


# =============================================================================
# 3. 日帳文件解析（形狀權威＝`board_attrib` 檔頭；本檔只做**讀端**的最小驗）
# =============================================================================
def parse_ledger_docs(docs, *, month=None):
    """`[doc…]` → `(parsed, n_bad)`；壞檔不炸、只計數（同 `board_attrib.parse_ledger_docs`）。

    `month` 給了就只留該月的（**日曆月切窗**；不是 end_day 往回 N 天）。
    同一天出現兩份（例：`.bak` 也被讀進來）⇒ **後者覆蓋前者**、只算一份。
    """
    good, bad, seen = {}, 0, 0
    for d in docs or ():
        seen += 1
        if not isinstance(d, dict):
            bad += 1
            continue
        ver = str(d.get("schema_ver") or "")
        day = d.get("day")
        sts = d.get("stations")
        if not ver.startswith(LEDGER_PREFIX) or not isinstance(sts, list) \
                or _as_date(day) is None:
            bad += 1
            continue
        day = str(day)[:10]
        if month is not None and day[:7] != str(month):
            continue
        good[day] = d
    return [good[k] for k in sorted(good)], bad


def _num(v):
    """數值欄 → float；`None`／壞形 → None（**絕不變 0**＝C-MR3）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return None if n is None else int(n)


# =============================================================================
# 4. 月尺（SPEC §3.2；§9 A7：**不得**拿 /equipment 那把 7 天現況尺換算整月）
# =============================================================================
def month_rate(day_rows, *, min_producing_h=None, min_days=None):
    """該站該月的 `(rate_ph_per_h, n_qualifying_days)`；合格日不足 ⇒ `(None, n)`。

    · 合格日＝該日 `producing_h >= RATE_MIN_PRODUCING_H` **且** `units.r_good` 有值。
      ⚠ 與 `app._eqm_pick_rate` 的差別：那支對 `producing_h` **缺席**採「視為合格」
        （理由＝門檻上線前的舊日帳）。月尺不吃這條寬待——月尺是**整個月的換算係數**，
        判不了的那天不採，代價只是樣本少一天；採了就是拿不知道分母的速率去乘整月的小時。
    · 尺＝合格日 `r_good` 的**中位數**（偶數筆＝中間兩筆平均，`statistics.median`）。
    · 合格日 < `RATE_MIN_DAYS`（5）⇒ None＝**無尺**（顯「—」、絕不外推）。
    """
    mp = RATE_MIN_PRODUCING_H if min_producing_h is None else float(min_producing_h)
    md = RATE_MIN_DAYS if min_days is None else int(min_days)
    xs = []
    for s in day_rows or ():
        ph = _num(s.get("producing_h"))
        if ph is None or ph < mp:
            continue
        r = _num((s.get("units") or {}).get("r_good"))
        if r is None or r <= 0:
            continue
        xs.append(r)
    if len(xs) < md:
        return (None, len(xs))
    return (_r6(statistics.median(xs)), len(xs))


# =============================================================================
# 5. 月模型（★ 本檔的主角）
# =============================================================================
def _rw_scale_of_doc(doc):
    """這一天的日帳站列用的是哪一把停機尺 → `"v5"`／`"legacy"`／`None`。**只看資料、不看版號**。

    版號是「檔」的事實，本函式問的是「這份檔裡有沒有那一欄」：手改檔、半升級檔、
    未來版的檔都由同一句話回答，不必在這裡維護一張版號對照表（純函式）。
    · `"v5"`     ＝至少一站帶 `rw.avail_down_sec`（2026-09-04 起的妥善率尺）。
    · `"legacy"` ＝有 `rw` 區塊、但**一站都沒有**那一欄（換尺前寫的檔）⇒ 一天都不收。
    · `None`     ＝這一天根本沒有任何站帶 `rw`（整日停線／日曆沒蓋到／v3 檔）
                  ——**這與「舊尺」不是同一件事**，要修的東西也不同。
    """
    any_rw = False
    for st in (doc or {}).get("stations") or ():
        if not isinstance(st, dict):
            continue
        rw = st.get("rw")
        if not isinstance(rw, dict):
            continue
        any_rw = True
        if rw.get("avail_down_sec") is not None:
            return "v5"
    return "legacy" if any_rw else None


def _src_err_of_doc(doc):
    """這一天的日帳有沒有「時間源當時讀不到」的站 → `(any_src_err, all_src_err)`；純函式。

    ★ 2026-09-04 的 src_err 防線（事故＝2026-08-11：28 站 timeline 全部 SQL 連線失敗）。
      那種日帳**每一個既有的誠實欄都看不見它**：`wo_source_status` 是 `"ok"`（cmms 好好的）、
      doc 級 `rw_axis` 是 `True`（軸算得出來）、站列的 `rw` 也在（尺是新的）⇒
      `n_degraded`／`n_days_rw`／`n_days_rw_legacy` 三個欄位全部正常，而那一天的停機與
      生產小時**全是 `None`**。少一整天的停機不會讓任何一格變紅，只會讓月總數變小。
    · `any_src_err` ＝任一站 `note == src_err`。
    · `all_src_err` ＝有 src_err ∧ **一站都不可測**（站列 `unmeasurable` 全為真）
      ＝那一天在軸的意義上等於**缺日**（凍結閘吃這一個）。
      ⚠ 缺 `unmeasurable` 鍵的站算「可測」＝保守（證不出整天全滅就不主張）。
    """
    any_src, any_meas, has_rows = False, False, False
    for st in (doc or {}).get("stations") or ():
        if not isinstance(st, dict):
            continue
        has_rows = True
        if st.get("note") == NOTE_SRC_ERR:
            any_src = True
        if not st.get("unmeasurable"):
            any_meas = True
    return (any_src, bool(has_rows) and any_src and not any_meas)


def _rw_roster_pad_of_doc(doc):
    """一天的 rw 是否混了 C-RW18 名冊補列證據 → `(padded, unpadded, all_unpadded)`。

    `rw.roster_pad` 是 2026-09-05 起的加法欄：True=那站成功走共用寬窗 helper，
    False=寬窗／timeline 未能補列，None／缺鍵=舊檔、未知。未知**不冒充** False；
    回補器會把它另外判 retry。`all_unpadded` 僅在這一天所有帶 rw 的站都明確 False
    才成立，正好與 src_err 的 any／day-list／all 三欄形同構。
    """
    vals = []
    for st in (doc or {}).get("stations") or ():
        if not isinstance(st, dict) or not isinstance(st.get("rw"), dict):
            continue
        vals.append(st["rw"].get("roster_pad"))
    padded = any(v is True for v in vals)
    unpadded = any(v is False for v in vals)
    return (padded, unpadded, bool(vals) and all(v is False for v in vals))


RW_ANCHOR = "08:00"                 # 生產日窗錨（權威＝`board_unit.RW_ANCHOR`；cross-pin U15.9）


def _rw_flags_of_doc(doc):
    """#182-P2 建帳證據旗 → `(anchor_legacy, cal_open_tail_pending, wo_open_at_build[])`；純函式。

    ⚠ **口徑權威是 `board_unit.rw_anchor_legacy`／`cal_open_tail_pending`／`wo_open_flags`**；
      本檔刻意不 import 它（檔頭第 ② 條：`sys.modules` 不得有 `mes_*`）⇒ 沿用日帳文件自己的
      鍵，兩邊由 `board_unit --selftest` U15.9 **逐案 cross-pin**（計畫與落地閘不得各判一次）。
    · anchor_legacy ＝缺 `rw_anchor` 或 ≠ "08:00"（v7 以前的檔全中＝錨不可考）。
    · cal_open_tail_pending ＝`cal_open_tail` 是 dict 且沒有 `resolved`。
    · wo_open_at_build ＝工單號清單（None／壞形 ⇒ `[]`）。
    """
    if not isinstance(doc, dict):
        return (False, False, [])
    tail = doc.get("cal_open_tail")
    wo = doc.get("wo_open_at_build")
    return (doc.get("rw_anchor") != RW_ANCHOR,
            isinstance(tail, dict) and not tail.get("resolved"),
            [str(x) for x in wo] if isinstance(wo, (list, tuple)) else [])


PRODUCT_DEFAULT = "plover"          # 這一份帳本體那棵樹（**不落檔**＝doc 自己）
PRODUCTS_KEY = "products"           # 日帳 v7 的產品子樹（權威＝`board_unit.PRODUCTS_KEY`）


def _product_stations_of_doc(doc, product):
    """一天的日帳 → `{cls: 產品子樹站列}`；沒有那一棵 ⇒ **`None`**（≠ `{}`）。

    ⚠ **口徑權威是 `board_unit.product_stations_of`**，本檔**刻意不 import 它**
      （它 import `mes_qlik` ⇒ 會撞檔頭第 ② 條「`sys.modules` 不得有 `mes_*`」那根釘）
      ⇒ 沿用**日帳文件自己的鍵**，同 `BUCKET_FIELDS`／`rw.seats` 的既有作法；
      兩邊的形狀由 `run_smoke` 的 cross-pin 對齊（同 `FAMILY_ORDER` 的處置）。
    ⚠ `None`＝這一天沒有 Kestrel 帳（顯「—」）；本函式**永不**回 `{}`。
    """
    ps = (doc or {}).get(PRODUCTS_KEY) if isinstance(doc, dict) else None
    if not isinstance(ps, dict):
        return None
    blk = ps.get(str(product))
    if not isinstance(blk, dict):
        return None
    out = {}
    for s in blk.get("stations") or ():
        if isinstance(s, dict) and s.get("cls"):
            out[str(s["cls"])] = s
    return out or None


def month_model(docs, month, *, today, calendar=None, route=None, states=None,
                product=PRODUCT_DEFAULT):
    """日帳文件 → **一個日曆月**的各站產能損失帳（純函式；零 I/O）。

    參數
      · `docs`     ：日帳文件（多寡不拘、可含別的月份——本函式自己切月）。
      · `month`    ：`"YYYY-MM"`（生產月鍵）。
      · `today`    ：`date`／`"YYYY-MM-DD"`；決定 `as_of` 與 `partial`（**注入式**＝可測）。
      · `calendar` ：可選，**`board_calendar.day_off` 形狀的 callable**（`date → (kind,label)`）。
                     給了就多算 `coverage.n_days_off` 與 `missing_days_workday`
                     （＝真正該追的缺日；週末/假日缺帳不是洞）。**本檔不 import 日曆模組**——
                     那會把「哪天放假」變成本檔的第二份事實。
      · `route`    ：可選，43 站序（`[cls…]`；權威＝`mes_qlik.QLIK_STATIONS`，由呼叫端注入
                     ＝C-MR5「站清單不硬編」）。給了 ⇒ RTY 對整條線連乘、**缺站以 1 計**；
                     沒給 ⇒ 用資料裡出現過的站（並在 `line.route_src` 標明）。
      · `states`   ：可選，六態 canonical key 的順序（權威＝`board_attrib.COUNTABLE_WO_STATES`）。
                     沒給 ⇒ 由資料裡的 `wo_h` 鍵推。**資料裡有、清單裡沒有的態一律附在後面**
                     （丟掉＝組成條的小時憑空消失）。
      · `product`  ：**#103（D1／D2）**。`"plover"`（預設）＝本體那棵樹，輸出**逐位元同舊版**。
                     其他值（現只有 `"kestrel"`）＝改吃日帳的 `products.<product>` 子樹。
                     ★ **只有數量腿（PH）**：子樹裡只有 `qlik_pass`／`qlik_fail`／`units_by_family`
                       （見 `board_unit._DOC_PRODUCT_STATION_KEYS` 的裁決）⇒ 時間欄
                       （停機、六態、A／U／OEE、月尺、當量）全部 `None`＝顯「—」。
                       **那不是缺陷，是口徑**：時間腿是**設備視角、不分產品**的
                       （`CONCEPT.md` §14.4），把 Kestrel PH 數除以整台機的生產機時＝
                       憑空發明第三把尺。
                     ★ **「混天 ⇒ 整片 None」的守衛只在被要求的那個產品內成立**：
                       沒有子樹的日子**整天不計**（`coverage.n_days_product_missing`），
                       不會把有子樹的日子拖成 `None`。⇒ Kestrel 從 2026-09-01 起有帳、
                       八月一天都沒有 ⇒ 八月 Kestrel 是**整片「—」**（零站列），
                       九月只要每天都有子樹就是完整的一個月。
                     ⚠ **兩棵樹永不相加**（Qlik 沒有「全線」那張表）——本函式一次只回
                       一個 product 的模型，呼叫端要兩個就呼叫兩次、分開顯示。

    回傳 `dict`：`{month, product, partial, as_of, unit, stations[], line{}, coverage{}}`。
    """
    product = str(product or PRODUCT_DEFAULT)
    used, n_bad = parse_ledger_docs(docs, month=month)
    aod = as_of_day(month, today)
    partial = is_partial(month, today)
    # 部分月＝**「至 as_of 日」的切面**：比 as_of 還新的帳（時鐘偏移／手動回填）不得混進來，
    # 否則同一個 `today` 算兩次會得到兩個數（趨勢圖上就是一條會自己動的線）。
    if aod is not None:
        used = [d for d in used if str(d.get("day"))[:10] <= aod]

    # ── 涵蓋（誠實欄；C-MR4／SPEC §3.1「必帶 n_days/n_missing」）──────────────
    all_days = month_days(month)
    expected = [d for d in all_days if aod is None or d <= aod]
    present = {str(d.get("day"))[:10] for d in used}
    missing = [d for d in expected if d not in present]
    coverage = {
        "n_days_expected": len(expected),
        "n_days_present": len(present & set(expected)),
        "missing_days": missing,
        "n_days_in_month": len(all_days),
        "n_bad_docs": n_bad,
        # 工單源降級日（`wo_source_status != ok`）＝停機小時可能低估，必須看得見
        "n_degraded": sum(1 for d in used
                          if str(d.get("wo_source_status") or "").lower() != "ok"),
        "n_days_off": None,
        "missing_days_workday": None,
        # 應生產時段軸涵蓋（doc 級 `rw_axis`）：**A／U／OEE 的分母是這幾天，不是 n_days_present**
        # ——v3 的舊日帳沒有這個鍵 ⇒ 一律不算（`.get` 回 None＝falsy），這正是要看見的洞。
        "n_days_rw": sum(1 for d in used if d.get("rw_axis")),
        # ★ 2026-09-04 換尺（`SPEC-CAPACITY.md` §3.1）：**有軸、但那把尺是舊的**的天數。
        #   ⚠ 刻意**不從 `n_days_rw` 扣掉**：`n_days_rw` 問的是「這天有沒有 RW 軸」
        #     （凍結閘吃它，語意一個位元不動）；本欄問的是「那一天的停機是用哪把尺量的」。
        #     兩個洞要分開看——「沒有軸」要重跑日曆，「舊尺」只要重算工單腿。
        #   ⚠ 整日停線／沒有任何站帶 `rw` 的日子**兩邊都不算**（那不是舊尺，是沒有資料）。
        "n_days_rw_legacy": sum(1 for d in used
                                if _rw_scale_of_doc(d) == "legacy"),
    }
    # ★ 2026-09-04 src_err 防線（`_src_err_of_doc` 有全文理由）：時間源當時讀不到的日子。
    #   ⚠ **加法欄、既有鍵的語意一個位元不動**——`n_days_present`／`n_days_rw` 照樣把
    #     那些天算進去（檔確實在、軸確實有），這兩欄回答的是別的問題。舊快照沒有這三個鍵
    #     ⇒ 讀端 `.get` 回 `None`（＝「沒算過」，不是 0），`validate_snapshot` 照樣放行。
    _se = [(str(d.get("day"))[:10], _src_err_of_doc(d)) for d in used]
    coverage["n_days_src_err"] = sum(1 for _d, (a, _l) in _se if a)
    coverage["days_src_err"] = sorted(_d for _d, (a, _l) in _se if a)
    coverage["n_days_src_err_all"] = sum(1 for _d, (_a, l) in _se if l)
    # ★ #64 C-RW18 日帳名冊補列的三個誠實欄（刻意與 src_err 同形）：False 才是
    # 「本次未補列」，None 是舊檔未知，不能把二者混在一起。月頁只在真有 True+False
    # 混尺時出一行揭露；這不是凍結阻擋條件（`_freeze_monthly` 僅警告）。
    _rp = [(str(d.get("day"))[:10], _rw_roster_pad_of_doc(d)) for d in used]
    coverage["n_days_rw_unpadded"] = sum(1 for _d, (_p, u, _a) in _rp if u)
    coverage["days_rw_unpadded"] = sorted(_d for _d, (_p, u, _a) in _rp if u)
    coverage["n_days_rw_unpadded_all"] = sum(1 for _d, (_p, _u, a) in _rp if a)
    coverage["rw_pad_mixed"] = (any(p for _d, (p, _u, _a) in _rp)
                                and any(u for _d, (_p, u, _a) in _rp))
    # ★ #182-P2（SPEC-DOWNTIME §14 ㉘／`_INVEST-RW-DENOMINATOR` §3.2 ③）：三種「這天的帳還不是
    #   最終事實」＝**凍結閘的硬條件**（`app._mr_freeze_plan` 的 `rw_unsettled`），每一種都點名日期。
    #   ⚠ 加法欄：既有鍵語意一個位元不動；舊快照沒有這些鍵 ⇒ 讀端 `.get` 回 None（沒算過）。
    _fl = [(str(d.get("day"))[:10], _rw_flags_of_doc(d)) for d in used]
    coverage["days_rw_anchor_legacy"] = sorted(_d for _d, (a, _c, _w) in _fl if a)
    coverage["n_days_rw_anchor_legacy"] = len(coverage["days_rw_anchor_legacy"])
    coverage["days_cal_open_tail"] = sorted(_d for _d, (_a, c, _w) in _fl if c)
    coverage["n_days_cal_open_tail"] = len(coverage["days_cal_open_tail"])
    coverage["wo_open_at_build_by_day"] = {_d: list(w) for _d, (_a, _c, w) in sorted(_fl) if w}
    coverage["days_wo_open_at_build"] = sorted(coverage["wo_open_at_build_by_day"])
    coverage["n_days_wo_open_at_build"] = len(coverage["days_wo_open_at_build"])
    if callable(calendar):
        try:
            offs = [d for d in expected if (calendar(_as_date(d)) or (None, None))[0]]
            coverage["n_days_off"] = len(offs)
            coverage["missing_days_workday"] = [d for d in missing if d not in set(offs)]
        except Exception:
            pass                              # 日曆壞掉不擋月報（欄位留 None＝沒算，不是 0）

    # ── 產品子樹的涵蓋（#103；**加法欄、Plover 路徑上恆為預設值**）───────────────
    coverage["product"] = product
    if product == PRODUCT_DEFAULT:
        coverage["n_days_product"] = coverage["n_days_present"]
        coverage["days_product_missing"] = []
    else:
        _pd = [(str(d.get("day"))[:10], _product_stations_of_doc(d, product)) for d in used]
        coverage["n_days_product"] = sum(1 for _d, m in _pd if m)
        coverage["days_product_missing"] = sorted(_d for _d, m in _pd if not m)

    # ── 逐站聚合 ────────────────────────────────────────────────────────────
    per = {}
    state_order = list(states or ())
    if product != PRODUCT_DEFAULT:
        # ★ 產品子樹：**只有數量腿（PH）**（時間欄一格都不碰 ⇒ `_finish_row` 自然全 None＝「—」）。
        #   ⚠ 站列的來源是子樹自己的 `cls`（Kestrel 45 站，含 12 個 Plover 沒有的站），
        #     **不是** doc 的 `stations`——兩棵樹的站清單本來就不同（§18.20 的 55 站流序
        #     就是這麼合成出來的）。
        for doc in used:
            pst = _product_stations_of_doc(doc, product)
            if not pst:
                continue                      # 那一天沒有這棵樹 ⇒ 整天不計（已在 coverage 現形）
            for cls, s in pst.items():
                p = per.setdefault(str(cls), _new_row(str(cls)))
                p["n_days"] += 1
                for key in ("qlik_pass", "qlik_fail"):
                    v = _int(s.get(key))
                    if v is not None:
                        p["_u"][key] = (p["_u"][key] or 0) + v
                ubf = s.get("units_by_family")
                if isinstance(ubf, dict):
                    p["_ubf"]["n_days"] += 1
                    for fk, fv in ubf.items():
                        n = _int(fv)
                        if n:
                            p["_ubf"]["counts"][fk] = p["_ubf"]["counts"].get(fk, 0) + n
                elif s.get("qlik_pass") is not None:
                    p["_ubf"]["n_days_legacy"] += 1
        stations = [_finish_row(p, state_order) for p in per.values()]
        stations.sort(key=lambda r: r["sort_key"])
        return {
            "month": str(month), "product": product,
            "partial": bool(partial), "as_of": aod, "unit": UNIT,
            "stations": stations, "line": _line_model(stations, route),
            "coverage": coverage,
        }
    for doc in used:
        for s in doc.get("stations") or ():
            if not isinstance(s, dict):
                continue
            cls = s.get("cls")
            if not cls:
                continue
            p = per.setdefault(str(cls), _new_row(str(cls)))
            p["_rows"].append(s)
            p["n_days"] += 1
            if s.get("unmeasurable"):
                note = str(s.get("note") or "unknown")
                p["unmeasurable_reasons"][note] = p["unmeasurable_reasons"].get(note, 0) + 1
            else:
                p["n_measurable"] += 1
                for out_key, doc_key in BUCKET_FIELDS:
                    v = _num(s.get(doc_key))
                    if v is not None:
                        p["_h"][out_key] += v
                for k, v in (s.get("wo_h") or {}).items():
                    if k not in state_order:
                        state_order.append(k)     # 資料裡有的態一律留（丟掉＝小時憑空消失）
                    hv = _num(v)
                    if hv is not None:
                        p["_st"][k] = p["_st"].get(k, 0.0) + hv
                # ★ W1 座數係數：**用當天的停機小時加權**（月級再平均一次係數＝把 0.5h 那天
                #   和 20h 那天當成一樣重，那是第二種尺）。日帳沒有這一欄 ⇒ 1.0（接線前的值）。
                _dh = _num(s.get("wo_union_h"))
                if _dh is not None and _dh > 0:
                    _rwd = s.get("rw") if isinstance(s.get("rw"), dict) else {}
                    _f = _num(_rwd.get("equiv_factor"))
                    if _f is None:
                        _f = 1.0
                        p["_ef"]["n_days_legacy"] += 1
                    else:
                        _f = min(1.0, max(0.0, _f))
                        p["_ef"]["n_days"] += 1
                    p["_ef"]["num"] += _dh * _f
                    p["_ef"]["den"] += _dh
            # 應生產時段軸（日帳 v4 `rw`）：**只 Σ `rw` 非 None 的日**（C-MR1）。
            #   ⚠ 與 `unmeasurable` **無關**：`rw` 的分母來自跑窗＋名冊（`erw_machine_windows`），
            #     不是 MES timeline ⇒ 零工期站量不到 24h 軸的時間，仍可能有應生產時段。
            rw = s.get("rw")
            if isinstance(rw, dict):
                # ★ W3 座帳（`rw.seats`）：舊日帳缺這個鍵 ⇒ **installed == active**＋
                #   `seats_legacy` 揭露（**不是**「那天零長停」）。讀法的唯一出處＝
                #   `board_capacity.rw_seats_of`；本檔不 import 那支（會拖進 `mes_*`、
                #   selftest 有釘）⇒ 沿用**日帳文件自己的鍵**，同 `BUCKET_FIELDS` 的作法。
                _sd = str(doc.get("day") or "")[:10]
                _sv = rw.get("seats")
                if isinstance(_sv, dict) and _sv.get("installed") is not None:
                    p["_seats"]["n_days"] += 1
                    if _sd >= p["_seats"]["day"]:
                        p["_seats"].update(
                            day=_sd, installed=_int(_sv.get("installed")),
                            active=_int(_sv.get("active")),
                            dormant=[str(x) for x in (_sv.get("dormant") or ())])
                else:
                    p["_seats"]["n_days_legacy"] += 1
                acc = p["_rw"]
                # ★ 2026-09-04：**只收帶新尺（`avail_down_sec`）的日子**。v4 舊尺日另計，
                #   絕不混進同一欄（見 RW_SEC_FIELDS 上方的口徑註）。判準看**資料**不看
                #   版號——版號是檔的事，這一欄要問的是「這一站這一天有沒有那把尺」。
                if rw.get("avail_down_sec") is None:
                    acc["n_days_legacy"] += 1
                    continue
                acc["n_days"] += 1
                for k in RW_SEC_FIELDS:
                    v = _num(rw.get(k))
                    if v is not None:
                        acc[k] += max(0.0, v)
            # 數量（PH）：**不可測站照樣有 units**（零工期站量不到時間、產出照常）
            u = s.get("units") or {}
            for key in ("qlik_pass", "qlik_fail", "rework_n", "attempts"):
                v = _int(u.get(key))
                if v is not None:
                    p["_u"][key] = (p["_u"][key] or 0) + v
            # ★ #98（`SPEC-LANDING.md` §18.18）：家族拆分只做**加總**，一個式子都不算。
            #   `None`＝那一天沒有這一腿（v5 以前的檔／家族欄缺席）⇒ 計進 `n_days_legacy`，
            #   **不當成零**（同 `rw` 舊尺那一條的紀律：沒有那一欄 ≠ 那一欄是 0）。
            ubf = u.get("units_by_family")
            if isinstance(ubf, dict):
                p["_ubf"]["n_days"] += 1
                for fk, fv in ubf.items():
                    n = _int(fv)
                    if n:
                        p["_ubf"]["counts"][fk] = p["_ubf"]["counts"].get(fk, 0) + n
            elif u.get("qlik_pass") is not None:
                p["_ubf"]["n_days_legacy"] += 1

    stations = [_finish_row(p, state_order) for p in per.values()]
    stations.sort(key=lambda r: r["sort_key"])

    # ── 線級（C-MR2：只 Σ 損失；良率只有 RTY）────────────────────────────────
    line = _line_model(stations, route)
    return {
        "month": str(month),
        "product": product,             # #103：加法欄（Plover 恆 "plover"＝既有行為的名字）
        "partial": bool(partial),
        "as_of": aod,
        "unit": UNIT,
        "stations": stations,
        "line": line,
        "coverage": coverage,
    }


def _new_row(cls):
    return {
        "cls": cls, "n_days": 0, "n_measurable": 0, "unmeasurable_reasons": {},
        "_rows": [], "_h": {k: 0.0 for k, _ in BUCKET_FIELDS}, "_st": {},
        "_u": {"qlik_pass": None, "qlik_fail": None, "rework_n": None, "attempts": None},
        # ★ #98：家族拆分的月加總（`counts` 空 ∧ n_days>0 ＝這站整月零產出，合法）
        "_ubf": {"counts": {}, "n_days": 0, "n_days_legacy": 0},
        "_rw": dict({k: 0.0 for k in RW_SEC_FIELDS}, n_days=0, n_days_legacy=0),
        # ★ W1（`SPEC-CAPACITY-FORECAST.md` §2）：停機當量的**座數係數**，按當日停機小時加權。
        #   `n_days_legacy`＝那一天的日帳沒有 `rw.equiv_factor`（#83 之前寫的）⇒ **以 1.0 計**
        #   ＝接線前的數字，一個位元不變（**不是** 0、也不是「那天零長停」）。
        "_ef": {"num": 0.0, "den": 0.0, "n_days": 0, "n_days_legacy": 0},
        # ★ W3：座帳只留**月內最後一天**那一份（逐日 as-of＝C-CF4；月列只印得下一個切面）。
        "_seats": {"day": "", "installed": None, "active": None, "dormant": [],
                   "n_days": 0, "n_days_legacy": 0},
    }


def _rw_cols(acc, yield_pct):
    """`rw` 累加器 → 站級 A／U／OEE 欄。**口徑不在本檔重寫**：三行式子一律呼叫
    `board_oee.rw_split`（`/station`／`/equipment` 同一支＝QG-13「分母永不各算各的」）。

    · 零 `rw` 日（`n_days_rw == 0`）⇒ 七欄全 None＝顯「—」（C-MR3；**不是 0**）。
    · `Σplanned == 0` ⇒ A／U／OEE 全 None（C-RW10：週日／整月停線不是 0% 也不是 100%），
      但三個小時欄照給（0.0 是**量到的 0**，與「沒量」不同）。
    · `U > 1` ⇒ `u_pct=None`、**絕不 clamp**（生產落在應生產時段之外＝矛盾，夾成 100%
      就把它藏起來了），並在 `rw_note` 留字串讓呈現層說得出「為什麼是 —」。
    · `OEE = A × U × (yield_pct/100)`；良率 None ⇒ OEE None（**不以 100% 頂替**）。
    """
    out = {k: None for k in RW_STATION_KEYS}
    out["n_days_rw"] = int(acc.get("n_days") or 0)
    if out["n_days_rw"] <= 0:
        # ★ 2026-09-04：這一站整月只有**舊尺**的 rw 日 ⇒ 七欄照樣「—」，但理由要說得出來
        #   （「沒有那一軸」與「那一軸還在舊尺上」是兩件事，回補要做的事也不同）。
        if int(acc.get("n_days_legacy") or 0) > 0:
            out["rw_note"] = "legacy_scale"
        return out
    planned = max(0.0, float(acc["planned_sec"]))
    health = min(max(0.0, float(acc["avail_down_sec"])), planned)    # 停機不可能 > 應生產時段
    prod = max(0.0, float(acc["producing_sec"]))
    out["planned_h"] = _r6(planned / 3600.0)
    out["health_down_h"] = _r6(health / 3600.0)
    out["producing_rw_h"] = _r6(prod / 3600.0)
    if planned <= 0:
        out["rw_note"] = "no_planned"
        return out
    sp = _oee.rw_split(prod, planned, health)
    a, u = sp["availability"], sp["utilization"]
    out["a_pct"] = None if a is None else _r6(100.0 * a)
    out["u_pct"] = None if u is None else _r6(100.0 * u)
    if u is None:
        out["rw_note"] = "no_up" if (planned - health) <= 0 else "u_gt_1"
    elif yield_pct is None:
        out["rw_note"] = "no_yield"
    if a is not None and u is not None and yield_pct is not None:
        out["oee_pct"] = _r6(100.0 * a * u * (float(yield_pct) / 100.0))
    return out


def _family_split(acc, qlik_pass):
    """`_ubf` 累加器 ＋ 月 `qlik_pass` → 站列 `qlik_pass_by_family`（純函式；#98）。

    · 一天都沒有這一腿（`n_days == 0`）⇒ `None`（「沒有那一軸」≠「那一軸是空的」）。
    · **恆等式守衛**：`Σ != qlik_pass` ⇒ `None`。混天（部分日子有拆分、部分沒有）天然
      落在這一條裡——那正是要它顯「—」的情形，不是要它印一組少算的拆分。
    · 鍵序／零值不寫鍵＝`FAMILY_ORDER`（R→A→M→未標）。⚠ 語意權威＝
      `mes_qlik.family_keys()`／`mes_family.FAMILIES`，**本檔刻意不 import**（會拖進
      `mes_*`＝檔頭那根釘）；`run_smoke` 有一根 cross-pin 兩邊逐位元對齊。
    """
    if int(acc.get("n_days") or 0) <= 0 or qlik_pass is None:
        return None
    try:
        counts = {k: int(v or 0) for k, v in (acc.get("counts") or {}).items()}
    except (TypeError, ValueError):
        return None
    out = {k: counts[k] for k in FAMILY_ORDER if counts.get(k)}
    for k in sorted(x for x in counts if x not in FAMILY_ORDER):
        if counts[k]:
            out[k] = counts[k]          # 不認得的家族排最後＝**永不靜默丟資料**
    return out if sum(out.values()) == int(qlik_pass) else None


def _finish_row(p, state_order):
    """站列收尾：算尺、算三損失、算 `sort_key`。**量不到一律 None、絕不 0**（C-MR3）。"""
    meas = p["n_measurable"] > 0
    rate, rate_days = month_rate(p["_rows"])
    u = p["_u"]
    fail, pas = u["qlik_fail"], u["qlik_pass"]
    rew, att = u["rework_n"], u["attempts"]

    denom = None if (fail is None and pas is None) else (fail or 0) + (pas or 0)
    yield_pct = _r6(100.0 * (pas or 0) / denom) if denom else None
    rework_pct = _r6(100.0 * (rew or 0) / att) if (att or 0) > 0 and rew is not None else None

    down_h = _r6(p["_h"]["down_h"]) if meas else None
    # ★★ W1（`SPEC-CAPACITY-FORECAST.md` §2）：**停機當量乘上座數係數**
    #    `equiv = down_h × rate × factor`。`factor` 是逐 WO 由
    #    `board_capacity.equiv_units_factor` 算好、按停機時數加權，落在日帳 `rw.equiv_factor`；
    #    這裡再按**當日停機小時**加權平均成月係數（同一種加權、做兩層）。
    #    · 無名冊機台／舊日帳沒有這一欄 ⇒ **1.0**＝接線前的數字，一個位元不變。
    #    · 係數只動**停機當量**這一條；A／U／良率／重工全部不碰（C-CF8）。
    ef = p["_ef"]
    factor = (ef["num"] / ef["den"]) if ef["den"] > 0 else None
    sea = p["_seats"]
    equiv = (_r6(down_h * rate * (1.0 if factor is None else factor))
             if (meas and rate is not None and down_h is not None) else None)
    # 三損失合計（Chris 2026-09-02：頭數字＝合計、附拆分）——無尺 ⇒ 合計 None（不是「少一項的合計」）
    excl = (fail or 0) + (rew or 0) if (fail is not None or rew is not None) else None
    total = _r6(equiv + excl) if (equiv is not None and excl is not None) else None

    if not meas:
        tier, primary = TIER_UNMEASURABLE, (excl or 0.0)
    elif total is None:
        tier, primary = TIER_NO_RATE, (down_h or 0.0)
    else:
        tier, primary = TIER_RATED, total

    row = {
        "cls": p["cls"],
        "n_days": p["n_days"],
        "n_measurable": p["n_measurable"],
        "unmeasurable_reasons": dict(p["unmeasurable_reasons"]),
        "down_h": down_h,
        "down_by_state": ({k: _r6(p["_st"].get(k, 0.0)) for k in state_order} if meas else None),
        "starved_h": _r6(p["_h"]["starved_h"]) if meas else None,
        "blocked_h": _r6(p["_h"]["blocked_h"]) if meas else None,
        "unexplained_h": _r6(p["_h"]["unexplained_h"]) if meas else None,
        "rate_ph_per_h": rate,
        "rate_days": rate_days,
        "down_equiv_ph": equiv,
        # ★ W1／W3 揭露欄（月報站列 hover 印它們）：`equiv_factor` ＝這個月停機當量實際乘的
        #   係數（None＝整月零停機小時 ⇒ 沒有可加權的東西，**不是** 1.0 也不是 0）；
        #   `seats_*` ＝月末那一天的座帳；`seats_legacy` True ＝這個月**沒有任何一天**的日帳
        #   記過座結構（#83 上線前的日子）⇒ 呈現層必須說「未記錄」，不得印成「零長停」。
        "equiv_factor": _r6(factor),
        "equiv_factor_days": ef["n_days"],
        "equiv_factor_days_legacy": ef["n_days_legacy"],
        "seats_installed": sea["installed"],
        "seats_active": sea["active"],
        "seats_dormant": list(sea["dormant"]),
        "seats_legacy": sea["n_days"] == 0,
        "seats_days_legacy": sea["n_days_legacy"],
        "qlik_fail": fail,
        "qlik_pass": pas,
        # ★ #98：`qlik_pass` 的家族拆分（總和不變）。**恆等式是硬前提**——
        #   `Σ qlik_pass_by_family ≡ qlik_pass`；不成立（例如整月只有部分日子帶這一腿）
        #   ⇒ 整片 `None`＝頁面退回單一數字，**絕不印一組加不回總數的拆分**（C-MR3）。
        #   `n_days_family_legacy` ＝有產出卻沒有這一腿的天數（回補要看的那一格）。
        "qlik_pass_by_family": _family_split(p["_ubf"], pas),
        "n_days_family": p["_ubf"]["n_days"],
        "n_days_family_legacy": p["_ubf"]["n_days_legacy"],
        "yield_pct": yield_pct,
        "rework_n": rew,
        "attempts": att,
        "rework_pct": rework_pct,
        "loss_total_ph": total,
        "loss_total_excl_equiv_ph": _r6(excl),
        # 排序：層級升冪 → 損失降冪 → cls 字典序（決定性、可 JSON 往返）
        # ★ 排序**不吃** A／U／OEE：那三欄是後加的，讓它們動排序＝同一份日帳在 v3/v4 之間換序。
        "sort_key": [tier, -float(primary), p["cls"]],
    }
    row.update(_rw_cols(p["_rw"], yield_pct))
    return row


def _line_model(stations, route):
    """線級：**只 Σ 損失**（C-MR2）＋ RTY 連乘。"""
    by_cls = {r["cls"]: r for r in stations}
    if route:
        route_cls, route_src = [str(c) for c in route], "caller"
    else:
        route_cls, route_src = sorted(by_cls), "observed"

    down_h = sum(r["down_h"] for r in stations if r["down_h"] is not None)
    rated = [r for r in stations if r["down_equiv_ph"] is not None]
    equiv = _r6(sum(r["down_equiv_ph"] for r in rated)) if rated else None
    fail = sum(r["qlik_fail"] or 0 for r in stations)
    rew = sum(r["rework_n"] or 0 for r in stations)
    total = _r6(equiv + fail + rew) if equiv is not None else None

    # RTY＝route 上每站良率連乘；**缺站（沒資料／算不出良率）以 1 計**並記 n_factors
    prod, n_factors = 1.0, 0
    for c in route_cls:
        r = by_cls.get(c)
        y = None if r is None else r["yield_pct"]
        if y is None:
            continue                          # 以 1 計＝不影響乘積（但要在 n_factors 現形）
        prod *= (y / 100.0)
        n_factors += 1
    rty = _r6(100.0 * prod) if n_factors else None
    out = {
        "down_h": _r6(down_h),
        "down_equiv_ph": equiv,
        "qlik_fail": fail,
        "rework_n": rew,
        "rty_pct": rty,
        "n_factors": n_factors,
        "n_route": len(route_cls),
        "route_src": route_src,
        "loss_total_ph": total,
        "loss_total_excl_equiv_ph": _r6(fail + rew),
        "n_stations": len(stations),
        "n_stations_rated": len(rated),
        "n_stations_unmeasurable": sum(1 for r in stations if r["n_measurable"] == 0),
    }
    out.update(_rw_line(stations, rty))
    return out


def _rw_line(stations, rty_pct):
    """線級 A／U／OEE（SPEC §3.4）：`A=Σ站up/Σ站planned`、`U=Σ生產/Σup`、`OEE=A×U×RTY`。

    ★ **這是設備視角的 Σ**（§14.4：interval-union、無產品去重）⇒ 時間可跨站相加；
      良率不可以，所以線級的 Q 只有 RTY 連乘（C-MR2）——`rty_pct is None ⇒ OEE None`。
    ★ Σ 的是**站列已經印出來的那些數字**（`planned_h`…），不是另一條聚合路徑
      ⇒ 「線級 ≡ Σ站」在頁面上是逐位元成立的（selftest 有恆等式釘）。
    """
    rows = [r for r in stations if (r.get("n_days_rw") or 0) > 0
            and r.get("planned_h") is not None]
    out = {k: None for k in RW_LINE_KEYS}
    out["n_stations_rw"] = len(rows)
    out["n_days_rw"] = 0
    if not rows:
        return out
    planned = _r6(sum(r["planned_h"] for r in rows))
    health = _r6(sum(r["health_down_h"] for r in rows))
    prod = _r6(sum(r["producing_rw_h"] for r in rows))
    out["planned_h"], out["health_down_h"], out["producing_rw_h"] = planned, health, prod
    # 線級的「涵蓋幾天」＝各站的 max（站與站的 rw 日不一定同一批；取 max 才不會低報涵蓋）
    out["n_days_rw"] = max(int(r["n_days_rw"]) for r in rows)
    if planned <= 0:
        out["rw_note"] = "no_planned"
        return out
    sp = _oee.rw_split(prod * 3600.0, planned * 3600.0, health * 3600.0)
    a, u = sp["availability"], sp["utilization"]
    out["a_pct"] = None if a is None else _r6(100.0 * a)
    out["u_pct"] = None if u is None else _r6(100.0 * u)
    if u is None:
        out["rw_note"] = "no_up" if (planned - health) <= 0 else "u_gt_1"
    elif rty_pct is None:
        out["rw_note"] = "no_rty"
    if a is not None and u is not None and rty_pct is not None:
        out["oee_pct"] = _r6(100.0 * a * u * (float(rty_pct) / 100.0))
    return out


# =============================================================================
# 6. 月快照（§3.5；C-MR8 產生後凍結）——**本檔唯一的 I/O**
# =============================================================================
SNAPSHOT_REQUIRED = ("schema_id", "month", "computed_at", "stations", "line", "coverage")


def snapshot_path(dir_path, month):
    return os.path.join(str(dir_path), f"{str(month)}.json")


def snapshot_doc(model, computed_at, products=None):
    """`model` → 可落地的快照文件（`schema_id` + `computed_at` 包在最外層）。

    `products`（#103／D2；**加法、預設 None ⇒ 逐位元同舊版**）＝`{product: 月模型}`
    ＝**與這個月一起凍結**的產品子樹月帳。⚠ 兩棵樹在快照裡也是分開的兩份模型，
    `schema_id` **不升版**（舊讀端 `.get("products")` 回 None＝「這份快照沒凍過 Kestrel」，
    照樣顯「—」；C-MR8 不因為多一個欄位就把已凍的月判成非法）。
    """
    doc = {"schema_id": SCHEMA_ID, "computed_at": str(computed_at)}
    doc.update(model)
    blocks = {str(k): v for k, v in (products or {}).items()
              if isinstance(v, dict) and v.get("month")}
    doc[PRODUCTS_KEY] = blocks or None
    # 合成產線 Q-C：合成進程凍出來的快照帶 `source="synth"`（讀端來源隔離靠它）；
    #   非合成進程**不加這一欄**＝逐位元同改動前（舊快照與 live 快照本來就沒有 source 欄）。
    if _wrs.is_synth_process():
        doc["source"] = _wrs.SOURCE_SYNTH
    return doc


def snapshot_product_model(snap, product):
    """已凍月快照 → 該 product 的月模型；沒凍過那一棵 ⇒ **None**（顯「—」，絕不回本體）。"""
    if not isinstance(snap, dict):
        return None
    if str(product or PRODUCT_DEFAULT) == PRODUCT_DEFAULT:
        return snap
    blk = (snap.get(PRODUCTS_KEY) or {}) if isinstance(snap.get(PRODUCTS_KEY), dict) else {}
    m = blk.get(str(product))
    return m if isinstance(m, dict) and m.get("month") else None


def validate_snapshot(doc):
    """讀端最小驗 → `(ok, reason)`。**拒讀比讀進壞資料好**（壞快照會被當成真月報）。"""
    if not isinstance(doc, dict):
        return (False, "not_a_dict")
    if doc.get("schema_id") != SCHEMA_ID:
        return (False, "schema_id")
    for k in SNAPSHOT_REQUIRED:
        if k not in doc:
            return (False, f"missing:{k}")
    if not isinstance(doc.get("stations"), list) or not isinstance(doc.get("line"), dict) \
            or not isinstance(doc.get("coverage"), dict):
        return (False, "bad_shape")
    m = str(doc.get("month") or "")
    if len(m) != 7 or m[4] != "-" or not (m[:4] + m[5:]).isdigit():
        return (False, "bad_month")
    if doc.get("partial"):
        return (False, "partial")             # C-MR4：部分月不該存在於快照區
    return (True, "ok")


def write_snapshot(model, dir_path, computed_at=None, products=None):
    """完整月 ⇒ 寫 `<dir>/YYYY-MM.json`；回 `(written: bool, reason: str)`。

    · `partial=True` ⇒ **不寫**（C-MR4）。
    · 檔已存在 ⇒ **不重寫**（C-MR8 凍結；口徑改版＝換 `schema_id` 重產全部）。
    · 原子寫（temp + `os.replace`）＝半寫檔不會被下一個 session 讀成真月報。
    · `products`（#103／D2；加法）＝**與這個月一起凍**的產品子樹月帳（`{product: 模型}`）。
      Kestrel 那個月一天都沒有子樹 ⇒ 呼叫端傳的模型是零站列的 ⇒ 讀回來仍然整片「—」，
      **不會**因此把整份快照擋掉（Plover 才是這個月的帳本體）。
    """
    if not isinstance(model, dict) or not model.get("month"):
        return (False, "no_model")
    if model.get("partial"):
        return (False, "partial")
    path = snapshot_path(dir_path, model["month"])
    if os.path.exists(path):
        return (False, "exists")
    doc = snapshot_doc(model, computed_at or _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                       products=products)
    ok, why = validate_snapshot(doc)
    if not ok:
        return (False, f"invalid:{why}")      # 自己都驗不過的東西不落地
    os.makedirs(str(dir_path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dir_path), prefix=".mon-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return (False, f"io:{type(exc).__name__}")
    return (True, "written")


def _fill_rw_defaults(doc):
    """**舊快照回填**（就地）：WP-M3 之前凍結的快照沒有 A／U／OEE 欄 ⇒ 補 **None**、不是 0。

    ★ 為什麼不在 `validate_snapshot` 要求這些鍵：C-MR8 說快照凍結後不重算，
      「新增欄位就把舊月判成非法」＝把已經凍結的歷史直接刪掉。加法相容 ⇒ 缺欄補 None，
      呈現層照 C-MR3 顯「—」（那個月確實沒有量過應生產時段軸，不是量到 0）。
    """
    if not isinstance(doc, dict):
        return doc
    for r in doc.get("stations") or ():
        if isinstance(r, dict):
            for k in RW_STATION_KEYS + SEAT_STATION_KEYS:
                r.setdefault(k, None)
    if isinstance(doc.get("line"), dict):
        for k in RW_LINE_KEYS:
            doc["line"].setdefault(k, None)
    if isinstance(doc.get("coverage"), dict):
        doc["coverage"].setdefault("n_days_rw", None)
    return doc


#   ★ 合成產線 Q-C（來源隔離，同日帳）：快照的 `source` 欄（合成進程凍的＝"synth"；live／舊快照
#     沒有這一欄）由 `core_source.ledger_doc_admitted` 判——非合成進程只拒合成快照、合成進程只收
#     合成快照。被拒＝**視同不存在**（`read_snapshot` 回 None、`list_snapshots` 不列）＋計數
#     （`snapshot_source_rejects()`）。快照區沒有合成快照時＝逐位元同改動前。
_SNAP_SRC_REJECTS = {"n": 0, "last": []}
_SNAP_SRC_OK_CACHE = {}          # path → ((mtime_ns, size), admitted)；list_snapshots 不必每次全讀


def snapshot_source_rejects(reset=False):
    """本進程累計被來源隔離拒掉的月快照 → `{"n": int, "last": [(month, source)…]}`（診斷用）。"""
    out = {"n": _SNAP_SRC_REJECTS["n"], "last": list(_SNAP_SRC_REJECTS["last"])}
    if reset:
        _SNAP_SRC_REJECTS["n"] = 0
        _SNAP_SRC_REJECTS["last"] = []
    return out


def _snap_reject(month, doc, diag):
    src = doc.get("source") if isinstance(doc, dict) else None
    _SNAP_SRC_REJECTS["n"] += 1
    _SNAP_SRC_REJECTS["last"] = (_SNAP_SRC_REJECTS["last"] + [(str(month), src)])[-32:]
    if isinstance(diag, dict):
        diag["n_source_rejected"] = int(diag.get("n_source_rejected") or 0) + 1
        diag.setdefault("source_rejected", []).append((str(month), src))


def _snapshot_admitted(path, month, diag=None):
    """快照檔的來源欄收不收（讀不動／壞檔 ⇒ True＝照舊列出，由 `read_snapshot` 自己回 None）。"""
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return True
    hit = _SNAP_SRC_OK_CACHE.get(path)
    if hit is not None and hit[0] == key:
        ok = hit[1]
    else:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return True
        ok = _wrs.ledger_doc_admitted(doc)
        _SNAP_SRC_OK_CACHE[path] = (key, ok)
        if not ok:
            _snap_reject(month, doc, diag)
        return ok
    if not ok and isinstance(diag, dict):
        diag["n_source_rejected"] = int(diag.get("n_source_rejected") or 0) + 1
        diag.setdefault("source_rejected", []).append((str(month), None))
    return ok


def read_snapshot(dir_path, month, diag=None):
    """`<dir>/YYYY-MM.json` → 快照 dict；缺檔／壞檔／驗不過／另一來源（Q-C）→ **None**（不炸整頁）。

    讀回來會過一次 `_fill_rw_defaults`（舊快照缺 A／U／OEE 欄 ⇒ 補 None）。
    `diag`（dict，選填）⇒ 累計 `n_source_rejected`／`source_rejected`。
    """
    path = snapshot_path(dir_path, month)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not _wrs.ledger_doc_admitted(doc):
        _snap_reject(month, doc, diag)
        return None
    return _fill_rw_defaults(doc) if validate_snapshot(doc)[0] else None


def list_snapshots(dir_path, diag=None):
    """快照區裡有哪些月（升冪）；目錄不存在 → `[]`。另一來源的快照（Q-C）不列。"""
    try:
        names = os.listdir(str(dir_path))
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(".json") and len(n) == 12 and n[4] == "-":
            if _snapshot_admitted(os.path.join(str(dir_path), n), n[:-5], diag):
                out.append(n[:-5])
    return sorted(out)


# =============================================================================
# 7. 仿真日帳（demo 源／selftest 共用；**真日帳永不進 fixture**）
# =============================================================================
SAMPLE_DOCS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_fixtures", "monthly_daily_docs_sample.json")


def load_sample_docs(path=None):
    """`_fixtures/monthly_daily_docs_sample.json` → `[doc…]`（合成日帳；數字全假）。

    ★ 為什麼不用真日帳當 fixture：真日帳含真實產出／良率數字（`_state/` 不進版控是有理由的），
      fixture 進版控 ⇒ 一旦當 fixture 就等於把產線數字 commit 進 repo。
    """
    p = path or SAMPLE_DOCS_FILE
    with open(p, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, list):
        return raw
    return list(raw.get("docs") or ())


# =============================================================================
# 8. selftest（純離線；零 MES、零真日帳、零網路）
# =============================================================================
def _selftest():
    n = [0]
    fails = []

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            fails.append(msg)

    TODAY = "2026-09-02"
    MONTH = "2026-08"

    # ── (1) 不得拖進任何 mes_* 模組（本檔的承重理由之一）─────────────────────
    mes_loaded = sorted(m for m in sys.modules if m.startswith("mes_"))
    ck(not mes_loaded, f"import board_monthly 之後不得有 mes_* 模組：{mes_loaded}")
    # ★ WP-M3／C4 放寬：**只准** `board_oee`（A↔U 三行口徑的單一出處），其餘一律不得拖進來。
    #   放寬的代價要看得見 ⇒ 這裡是白名單、不是「不檢查了」。
    _ALLOWED_Board = {"board_monthly", "board_oee"}
    _board_loaded = sorted(m for m in sys.modules
                        if m.startswith("board_") and m not in _ALLOWED_Board)
    ck(not _board_loaded, f"本檔只准 import board_oee，實得多餘的：{_board_loaded}")
    ck("board_oee" in sys.modules and _oee.rw_split is not None,
       "★ A／U 走 board_oee.rw_split（月級不得手抄第二把尺）")
    ck(not [m for m in sys.modules if m.startswith("mes_")],
       "★ 引 board_oee 之後仍不得有 mes_*（那支只 import random/sys）")

    # ── (2) 日曆月邊界（31／30／28／29；monthrange 決定、不硬編）─────────────
    ck(len(month_days("2026-08")) == 31, "8 月 31 天")
    ck(len(month_days("2026-09")) == 30, "9 月 30 天")
    ck(len(month_days("2026-02")) == 28, "2026-02 = 28 天")
    ck(len(month_days("2024-02")) == 29, "2024-02（閏年）= 29 天")
    ck(month_last_day("2026-02").isoformat() == "2026-02-28", "2026-02 月末 = 02-28")
    ck(month_add("2026-12", 1) == "2027-01" and month_add("2026-01", -1) == "2025-12",
       "month_add 跨年")
    ck(month_of("2026-08-01") == "2026-08" and month_of("2026-08-31") == "2026-08",
       "month_of 首末日")
    ck(month_of("bad") is None and month_of(None) is None, "month_of 壞形 → None")

    # ── (3) partial / as_of（C-MR4）────────────────────────────────────────
    ck(is_partial("2026-09", TODAY) is True, "當月＝部分月")
    ck(is_partial("2026-08", TODAY) is False, "8 月在 09-02 已完整")
    ck(is_partial("2026-08", "2026-09-01") is True,
       "09-01 時 8 月仍算部分月（08-31 的帳次日才落）")
    ck(as_of_day("2026-09", TODAY) == "2026-09-01", "當月 as_of = today−1")
    ck(as_of_day("2026-08", TODAY) == "2026-08-31", "完整月 as_of = 月末")
    ck(as_of_day("2026-10", TODAY) is None, "未來月 as_of = None")

    # ── (4) 仿真日帳 fixture ＋ 模型主釘 ─────────────────────────────────────
    docs = load_sample_docs()
    ck(len(docs) >= 20, f"仿真日帳應有 20 份以上，實得 {len(docs)}")
    ck(all(str(d.get("schema_ver", "")).startswith(LEDGER_PREFIX) for d in docs),
       "仿真日帳 schema_ver 必須是 board.daily_ledger.*")
    m = month_model(docs, MONTH, today=TODAY)

    ck(m["unit"] == "PH", "單位常數＝PH（不用「顆」／pcs）")
    ck(m["partial"] is False and m["as_of"] == "2026-08-31", "8 月模型＝完整月")
    ck(m["coverage"]["n_days_expected"] == 31, "涵蓋分母＝31 天")
    ck(m["coverage"]["n_days_present"] == len(docs), "涵蓋分子＝實得日數")
    ck(m["coverage"]["missing_days"] and
       m["coverage"]["n_days_present"] + len(m["coverage"]["missing_days"]) == 31,
       "缺日清單 ＋ 實得 ＝ 31（缺日必須被看見）")

    # ★ 承重釘子：Σ站 qlik_fail ≡ Σ日 qlik_fail（聚合不得漏站、不得雙計）
    sum_day = sum(int((s.get("units") or {}).get("qlik_fail") or 0)
                  for d in docs for s in d["stations"])
    sum_sta = sum(r["qlik_fail"] or 0 for r in m["stations"])
    ck(sum_day == sum_sta == m["line"]["qlik_fail"],
       f"Σ站 ≡ Σ日 ≡ line（{sum_sta} vs {sum_day} vs {m['line']['qlik_fail']}）")
    sum_day_rw = sum(int((s.get("units") or {}).get("rework_n") or 0)
                     for d in docs for s in d["stations"])
    ck(sum_day_rw == sum(r["rework_n"] or 0 for r in m["stations"]) == m["line"]["rework_n"],
       "Σ站 rework ≡ Σ日 rework ≡ line")

    by = {r["cls"]: r for r in m["stations"]}

    # ── (5) 無尺站 / 不可測站：None 不是 0（C-MR3）──────────────────────────
    thin = by["segAutomatedOQC"]
    ck(thin["rate_days"] < RATE_MIN_DAYS, "無尺站的合格日應 < 5")
    ck(thin["rate_ph_per_h"] is None, "無尺站 rate＝None")
    ck(thin["down_equiv_ph"] is None, "★ 無尺站 down_equiv_ph 恆 None（絕不外推）")
    ck(thin["loss_total_ph"] is None, "無尺站三損失合計＝None（不是少一項的合計）")
    ck(thin["loss_total_excl_equiv_ph"] is not None, "無尺站仍給 excl_equiv 部分和")
    ck(thin["down_h"] is not None and thin["down_h"] > 0, "無尺站仍有真實停機小時")

    zl = by["segAttachCureOven"]
    ck(zl["n_measurable"] == 0, "結構性不可測站＝零可測日")
    ck(zl["down_h"] is None and zl["starved_h"] is None and zl["down_by_state"] is None,
       "不可測站的時間桶全 None（絕不 0）")
    ck(zl["unmeasurable_reasons"].get("zero_len") == zl["n_days"],
       "不可測理由逐日計數（zero_len）")
    ck(zl["qlik_fail"] is not None, "★ 不可測站照樣有產出/良率（量不到時間 ≠ 沒生產）")

    mixed = by["segVacuumPackInspect"]
    ck(mixed["n_measurable"] < mixed["n_days"] and mixed["unmeasurable_reasons"].get("nosig"),
       "混合站：部分日 nosig，理由要現形")
    ck(mixed["n_measurable"] > 0 and mixed["down_h"] is not None,
       "混合站仍算得出時間帳（只用可測日）")

    # ── (6) 排序規則（有尺 → 無尺 → 不可測；同層依損失降冪）──────────────────
    tiers = [r["sort_key"][0] for r in m["stations"]]
    ck(tiers == sorted(tiers), "層級升冪：有尺 → 無尺 → 結構性不可測")
    rated = [r for r in m["stations"] if r["sort_key"][0] == TIER_RATED]
    ck([r["loss_total_ph"] for r in rated] == sorted(
        (r["loss_total_ph"] for r in rated), reverse=True), "有尺站依三損失合計降冪")
    ck(m["stations"][-1]["cls"] == "segAttachCureOven", "結構性不可測站沉到最後")
    ck(rated[0]["loss_total_ph"] == max(r["loss_total_ph"] for r in rated),
       "頭一列＝損失最大的站")

    # ── (7) 三損失合計＝拆分相加（Chris 2026-09-02 頭數字口徑）────────────────
    for r in rated:
        ck(abs(r["loss_total_ph"] - (r["down_equiv_ph"] + (r["qlik_fail"] or 0)
                                     + (r["rework_n"] or 0))) < 1e-6,
           f"{r['cls']}：合計 ≡ 停機當量＋良率＋重工")
    ck(abs(m["line"]["loss_total_ph"] - (m["line"]["down_equiv_ph"]
                                         + m["line"]["qlik_fail"]
                                         + m["line"]["rework_n"])) < 1e-6,
       "line 合計 ≡ 三拆分相加")

    # ── (8) 月尺＝中位數、且只吃合格日 ───────────────────────────────────────
    frame = by["segFrameBonding"]
    rows = [s for d in docs for s in d["stations"] if s["cls"] == "segFrameBonding"]
    xs = sorted(s["units"]["r_good"] for s in rows
                if (s.get("producing_h") or 0) >= RATE_MIN_PRODUCING_H
                and (s.get("units") or {}).get("r_good"))
    ck(frame["rate_days"] == len(xs), "rate_days＝合格日數")
    ck(abs(frame["rate_ph_per_h"] - statistics.median(xs)) < 1e-6, "月尺＝合格日 r_good 中位數")
    ck(abs(frame["down_equiv_ph"] - frame["down_h"] * frame["rate_ph_per_h"]) < 1e-6,
       "當量＝停機小時 × 月尺")
    lo, hi = min(xs), max(xs)
    ck(lo < frame["rate_ph_per_h"] < hi, "中位數落在樣本區間內（不是取最新一日的極值）")

    # ── (9) 良率／重工率：分母為零 ⇒ None，不是 0%／100%（C-MR3）─────────────
    ck(frame["yield_pct"] is not None and 0 < frame["yield_pct"] <= 100, "良率在 (0,100]")
    empty = month_model([{"schema_ver": "board.daily_ledger.v3", "day": "2026-08-05",
                          "stations": [{"cls": "segX", "unmeasurable": False,
                                        "producing_h": 0.0, "wo_union_h": 0.0,
                                        "units": {"attempts": 0, "rework_n": 0,
                                                  "qlik_pass": 0, "qlik_fail": 0}}]}],
                        MONTH, today=TODAY)
    e0 = empty["stations"][0]
    ck(e0["yield_pct"] is None, "Qlik 分母為零 ⇒ 良率 None（不是 100%）")
    ck(e0["rework_pct"] is None, "attempts 為零 ⇒ 重工率 None（不是 0%）")
    ck(e0["rate_ph_per_h"] is None and e0["down_equiv_ph"] is None, "無尺 ⇒ 當量 None")

    # ── (10) RTY：43 因子連乘、缺站以 1 計並記 n_factors（C-MR2）─────────────
    route = [r["cls"] for r in m["stations"]] + ["segGhostA", "segGhostB"]
    m_route = month_model(docs, MONTH, today=TODAY, route=route)
    exp = 1.0
    for r in m_route["stations"]:
        if r["yield_pct"] is not None:
            exp *= r["yield_pct"] / 100.0
    ck(abs(m_route["line"]["rty_pct"] - 100.0 * exp) < 1e-6, "RTY＝各站良率連乘")
    ck(m_route["line"]["n_route"] == len(route), "n_route＝呼叫端注入的站序長度")
    ck(m_route["line"]["n_factors"] == sum(1 for r in m_route["stations"]
                                           if r["yield_pct"] is not None),
       "n_factors＝真正有良率的站數（缺站以 1 計、不進 n_factors）")
    ck(abs(m_route["line"]["rty_pct"] - m["line"]["rty_pct"]) < 1e-9,
       "★ 缺站以 1 計 ⇒ 多兩個幽靈站不改變 RTY 數值（只改 n_route）")
    ck(m["line"]["route_src"] == "observed" and m_route["line"]["route_src"] == "caller",
       "route 來源要誠實標明")
    ck("yield_pct" not in m["line"], "C-MR2：line 沒有『全站良率』這種東西")

    # ── (11) 六態組成：key 由資料／呼叫端來，不硬編（C-MR5）──────────────────
    ck(len(frame["down_by_state"]) == 6, "六態組成有六個 key")
    ck(abs(sum(frame["down_by_state"].values())
           - sum(float(s["wo_h"][k]) for s in rows if not s["unmeasurable"]
                 for k in s["wo_h"])) < 1e-6, "六態 Σ ≡ 逐日 wo_h Σ")
    inj = month_model(docs, MONTH, today=TODAY, states=["維修", "新態(還沒見過)"])
    keys = list(inj["stations"][0]["down_by_state"])
    ck(keys[0] == "維修" and "新態(還沒見過)" in keys, "呼叫端注入的態序排前面")
    ck(len(keys) == 7 and abs(sum(inj["stations"][0]["down_by_state"].values())
                              - sum(m["stations"][0]["down_by_state"].values())) < 1e-6,
       "★ 資料裡有、清單裡沒有的態一律附在後面（小時不得憑空消失）")

    # ── (12) 日曆月切窗：別的月份的日帳不得混進來（§9 A4）────────────────────
    poison = list(docs) + [{"schema_ver": "board.daily_ledger.v3", "day": "2026-07-31",
                            "stations": [{"cls": "segFrameBonding", "unmeasurable": False,
                                          "producing_h": 8.0, "wo_union_h": 99.0,
                                          "wo_h": {}, "units": {"attempts": 9999,
                                                                "rework_n": 9999,
                                                                "qlik_pass": 1,
                                                                "qlik_fail": 9999}}]}]
    m_p = month_model(poison, MONTH, today=TODAY)
    ck(m_p["line"]["qlik_fail"] == m["line"]["qlik_fail"], "★ 7/31 的日帳不得進 8 月的帳")
    ck(m_p["coverage"]["n_days_present"] == m["coverage"]["n_days_present"], "涵蓋不受鄰月影響")
    dup = month_model(list(docs) + list(docs), MONTH, today=TODAY)
    ck(dup["line"]["qlik_fail"] == m["line"]["qlik_fail"], "同一天餵兩份 ⇒ 只算一份")
    # 部分月＝「至 as_of 日」的切面：比 as_of 新的帳不得混進來（同一個 today 算兩次要一樣）
    m_mid = month_model(docs, MONTH, today="2026-08-16")
    ck(m_mid["as_of"] == "2026-08-15" and m_mid["partial"] is True, "月中＝部分月")
    mid_fail = sum(int((s.get("units") or {}).get("qlik_fail") or 0) for d in docs
                   for s in d["stations"] if d["day"] <= "2026-08-15")
    ck(m_mid["line"]["qlik_fail"] == mid_fail,
       "★ 部分月只吃 as_of 當天（含）以前的帳")
    ck(m_mid["coverage"]["n_days_expected"] == 15, "部分月分母＝1 日到 as_of")

    # ── (13) 壞檔不炸、只計數 ───────────────────────────────────────────────
    bad = month_model(list(docs) + [None, {"schema_ver": "nope", "day": "2026-08-03",
                                           "stations": []},
                                    {"schema_ver": "board.daily_ledger.v3", "day": "x",
                                     "stations": []}], MONTH, today=TODAY)
    ck(bad["coverage"]["n_bad_docs"] == 3, "三份壞檔 ⇒ n_bad_docs=3")
    ck(bad["line"]["qlik_fail"] == m["line"]["qlik_fail"], "壞檔不影響數字")

    # ── (14) 快照：partial 不寫、已存在不重寫、非法拒讀（C-MR4／C-MR8）───────
    with tempfile.TemporaryDirectory() as td:
        cur = month_model(docs, "2026-09", today=TODAY)
        ok, why = write_snapshot(cur, td, "2026-09-02T08:00:00")
        ck(ok is False and why == "partial", "★ 部分月不寫快照")
        ck(list_snapshots(td) == [], "部分月不得在快照區留下檔案")

        ok, why = write_snapshot(m, td, "2026-09-02T08:00:00")
        ck(ok is True and why == "written", "完整月寫得出快照")
        ck(os.path.exists(os.path.join(td, "2026-08.json")), "檔名＝YYYY-MM.json")
        ck(list_snapshots(td) == ["2026-08"], "list_snapshots 列得出來")

        first = read_snapshot(td, "2026-08")
        ck(first is not None and first["schema_id"] == SCHEMA_ID, "讀得回、schema_id 對")
        ck(first["line"]["qlik_fail"] == m["line"]["qlik_fail"], "快照內容 ≡ 模型")
        ck(first["computed_at"] == "2026-09-02T08:00:00", "computed_at 由呼叫端注入")

        m2 = dict(m)
        m2["line"] = dict(m["line"], qlik_fail=-1)
        ok2, why2 = write_snapshot(m2, td, "2026-09-02T09:00:00")
        ck(ok2 is False and why2 == "exists", "★ 已存在不重寫（C-MR8 凍結）")
        ck(read_snapshot(td, "2026-08")["line"]["qlik_fail"] == m["line"]["qlik_fail"],
           "凍結後內容不變")
        ck(read_snapshot(td, "2026-07") is None, "沒有的月 → None")

        # 非法 fixture 一律拒讀
        fx = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_fixtures", "board.monthly_ledger.v1.json")
        with open(fx, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
        ck(fixture.get("schema_id") == SCHEMA_ID, "fixture schema_id")
        ck(len(fixture.get("golden") or []) >= 1, "fixture 至少一份合法 golden")
        ck(len(fixture.get("invalid") or []) >= 2, "fixture 至少兩份非法變體（QG-3.1）")
        for g in fixture["golden"]:
            ok_g, why_g = validate_snapshot(g)
            ck(ok_g, f"golden 應通過驗證，實得 {why_g}")
        for iv in fixture["invalid"]:
            ok_i, why_i = validate_snapshot(iv["record"])
            ck(not ok_i, f"非法變體必須被拒：{iv['case']}")
            ck(why_i == iv["expect_reason"],
               f"★ 變體要壞在宣稱的那一點：{iv['case']} 期望 {iv['expect_reason']} 實得 {why_i}")
        # golden 落地→讀回 round-trip（sort_key 經 JSON 變 list 仍可排序）
        td2 = os.path.join(td, "gold")
        gm = {k: v for k, v in fixture["golden"][0].items()
              if k not in ("schema_id", "computed_at")}
        okg, _ = write_snapshot(gm, td2, "2026-09-01T08:00:00")
        rt = read_snapshot(td2, gm["month"])
        ck(okg and rt is not None, "golden round-trip 寫得出、讀得回")
        ck(sorted(rt["stations"], key=lambda r: r["sort_key"]) == rt["stations"],
           "★ JSON 往返後 sort_key 仍是已排序的（list 也能排）")

    # ── (15) 日曆注入（週末缺帳不是洞）────────────────────────────────────────
    def fake_day_off(d):
        return ("weekend", None) if d.weekday() >= 5 else (None, None)

    cal_m = month_model(docs, MONTH, today=TODAY, calendar=fake_day_off)
    ck(cal_m["coverage"]["n_days_off"] == sum(
        1 for d in month_days(MONTH) if _as_date(d).weekday() >= 5), "n_days_off＝週末日數")
    ck(cal_m["coverage"]["missing_days_workday"] is not None
       and len(cal_m["coverage"]["missing_days_workday"]) <= len(cal_m["coverage"]["missing_days"]),
       "工作日缺帳 ⊆ 全部缺帳")
    ck(month_model(docs, MONTH, today=TODAY)["coverage"]["n_days_off"] is None,
       "沒給日曆 ⇒ n_days_off 留 None（沒算，不是 0）")
    ck(month_model(docs, MONTH, today=TODAY,
                   calendar=lambda d: 1 / 0)["coverage"]["n_days_off"] is None,
       "日曆壞掉不擋月報")

    # ── (16) 空輸入 ─────────────────────────────────────────────────────────
    z = month_model([], MONTH, today=TODAY)
    ck(z["stations"] == [] and z["line"]["rty_pct"] is None and z["line"]["down_equiv_ph"] is None,
       "零日帳 ⇒ 空站表、RTY/當量 None")
    ck(z["coverage"]["n_days_present"] == 0 and len(z["coverage"]["missing_days"]) == 31,
       "零日帳 ⇒ 31 天全缺")

    # ── (19) 應生產時段軸（日帳 v4 `rw`）→ 月級 A／U／OEE（WP-M3 C4；C-MR1）──────
    def _uu(pas, fail):
        return {"attempts": pas + fail, "rework_n": 0, "qlik_pass": pas, "qlik_fail": fail}

    def _row(cls, planned_h, health_h, prod_h, *, pas=99, fail=1, v5=True,
             roster_pad=None):
        """合成站列：`planned_h is None` ⇒ 那天沒有 rw（`rw: null`）。

        `v5=False` ⇒ **只寫 v4 的 `health_down_sec`**（2026-09-04 換尺之前的檔形）
        ⇒ 月級一律不收（`n_days_rw_legacy`）。"""
        r = {"cls": cls, "unmeasurable": False, "producing_h": prod_h,
             "wo_union_h": 0.0, "wo_h": {}, "starved_h": 0.0, "blocked_h": 0.0,
             "unexplained_h": 0.0, "units": _uu(pas, fail), "rw": None}
        if planned_h is not None:
            r["rw"] = {"planned_sec": planned_h * 3600.0,
                       "health_down_sec": health_h * 3600.0,
                       "producing_sec": prod_h * 3600.0, "n_eid": 1, "src": "erw"}
            if v5:
                r["rw"]["avail_down_sec"] = health_h * 3600.0
                r["rw"]["down_by_state"] = {"維修": health_h * 3600.0}
                r["rw"]["eids"] = ["E1"]
            if roster_pad is not None:
                r["rw"]["roster_pad"] = roster_pad
        return r

    def _d4(day, rows, *, axis=True, ver="board.daily_ledger.v5"):
        return {"schema_ver": ver, "day": day, "rw_axis": axis,
                "rw_reason": "ok" if axis else "calendar_uncovered",
                "families_status": None, "stations": rows}

    # 三天 × (planned 10h、health 2h、prod 6h) ⇒ up 24h ⇒ A=80%、U=75%、Y=99% ⇒ OEE=59.4%
    rwm = month_model([_d4(f"2026-08-0{i}", [_row("segA", 10.0, 2.0, 6.0)])
                       for i in (1, 2, 3)], MONTH, today=TODAY)
    ra = rwm["stations"][0]
    ck(ra["n_days_rw"] == 3, "n_days_rw＝有 rw 的日數")
    ck(ra["planned_h"] == 30.0 and ra["health_down_h"] == 6.0
       and ra["producing_rw_h"] == 18.0, "三個小時欄＝逐日 Σ（秒→小時）")
    ck(abs(ra["a_pct"] - 80.0) < 1e-6 and abs(ra["u_pct"] - 75.0) < 1e-6,
       "A＝Σup/Σplanned、U＝Σprod/Σup（口徑出自 board_oee.rw_split）")
    ck(abs(ra["oee_pct"] - 80.0 * 0.75 * 0.99) < 1e-6, "OEE＝A×U×(yield_pct/100)")
    ck(ra["rw_note"] is None, "正常情形不留 rw_note")
    ck(rwm["coverage"]["n_days_rw"] == 3, "coverage.n_days_rw＝doc 級 rw_axis 為真的天數")

    # 混餵：部分日有 rw ⇒ **只 Σ 有 rw 的日**（沒有的那天整天不進分母、不以 24h 補）
    mix = month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0)]),
                       _d4("2026-08-02", [_row("segA", 10.0, 2.0, 6.0)]),
                       _d4("2026-08-03", [_row("segA", 10.0, 2.0, 6.0)]),
                       _d4("2026-08-04", [_row("segA", None, 0.0, 9.9)], axis=False)],
                      MONTH, today=TODAY)
    mx = mix["stations"][0]
    ck(mx["n_days_rw"] == 3 and mx["planned_h"] == 30.0,
       "★ 混餵：沒有 rw 的日整天不進分母")
    ck(mx["a_pct"] == ra["a_pct"] and mx["u_pct"] == ra["u_pct"],
       "★ 多一個無 rw 日不得改變 A／U（否則就是拿 24h 軸稀釋 RW 軸）")
    ck(mix["coverage"]["n_days_rw"] == 3 and mix["coverage"]["n_days_present"] == 4,
       "rw_axis False 的日算涵蓋、不算 rw 涵蓋")

    # ── ★ 2026-09-04 換尺：**v4 舊尺日一律不混算**（`SPEC-CAPACITY.md` §3.1）────────
    #   舊檔的 `rw` 有 `planned_sec`／`producing_sec`、就是沒有 `avail_down_sec`。
    #   混算＝同一欄半個月一把尺，而且畫面上完全看不出來 ⇒ 整天不收、改成誠實揭露。
    legacy = month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0, v5=False)]),
                          _d4("2026-08-02", [_row("segA", 10.0, 2.0, 6.0, v5=False)])],
                         MONTH, today=TODAY)
    lg = legacy["stations"][0]
    ck(lg["n_days_rw"] == 0 and lg["a_pct"] is None and lg["u_pct"] is None
       and lg["oee_pct"] is None and lg["planned_h"] is None,
       "★ 只有 v4 舊尺的月 ⇒ A／U／OEE 全「—」（絕不拿舊白名單的停機冒充新口徑）")
    ck(lg["rw_note"] == "legacy_scale",
       "★ 「—」的理由說得出來：legacy_scale ≠ 沒有那一軸（回補要做的事不同）")
    ck(legacy["coverage"]["n_days_rw"] == 2
       and legacy["coverage"]["n_days_rw_legacy"] == 2,
       "★ coverage 把兩種洞分開：n_days_rw 仍數『有軸』（凍結閘的語意不動）、"
       "n_days_rw_legacy 另記『那把尺是舊的』2 天")
    ck(month_model([_d4("2026-08-01", [_row("segA", None, 0.0, 6.0)], axis=False)],
                   MONTH, today=TODAY)["coverage"]["n_days_rw_legacy"] == 0,
       "★ 整日沒有任何站帶 rw ⇒ **不算舊尺**（那是沒資料，不是量錯尺；要修的東西不同）")
    mixv = month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0)]),
                        _d4("2026-08-02", [_row("segA", 10.0, 2.0, 6.0)]),
                        _d4("2026-08-03", [_row("segA", 10.0, 2.0, 6.0)]),
                        _d4("2026-08-04", [_row("segA", 99.0, 99.0, 99.0, v5=False)])],
                       MONTH, today=TODAY)["stations"][0]
    ck(mixv["n_days_rw"] == 3 and mixv["planned_h"] == 30.0
       and abs(mixv["a_pct"] - 80.0) < 1e-6,
       "★ 新舊混餵：舊尺那天一秒都不進分母／分子（A 與純新尺月逐位元相同）")
    ck(_rw_scale_of_doc(_d4("x", [_row("segA", 1.0, 0.0, 1.0)])) == "v5"
       and _rw_scale_of_doc(_d4("x", [_row("segA", 1.0, 0.0, 1.0, v5=False)])) == "legacy"
       and _rw_scale_of_doc(_d4("x", [_row("segA", None, 0.0, 1.0)])) is None
       and _rw_scale_of_doc({}) is None,
       "_rw_scale_of_doc 看資料不看版號，且『舊尺』與『根本沒有 rw』分成兩答")

    # ── ★ #64 C-RW18 名冊補列誠實欄：與 src_err 同形，未知舊檔不假裝 unpadded ─────
    rp_docs = [
        _d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0, roster_pad=True)]),
        _d4("2026-08-02", [_row("segA", 10.0, 2.0, 6.0, roster_pad=False)]),
        _d4("2026-08-03", [_row("segA", 10.0, 2.0, 6.0)]),                 # legacy None
    ]
    rp = month_model(rp_docs, MONTH, today=TODAY)["coverage"]
    ck(rp["n_days_rw_unpadded"] == 1 and rp["days_rw_unpadded"] == ["2026-08-02"]
       and rp["n_days_rw_unpadded_all"] == 1,
       "★ roster_pad False 的日子進 n/days/all 三個誠實欄；缺鍵舊檔不偷算成 False")
    ck(rp["rw_pad_mixed"] is True,
       "★ 同月同時有明確 padded 與 unpadded ⇒ 只給呈現層一個混尺例外旗標")
    # ── ★ #182-P2：建帳證據旗三欄（凍結閘 `rw_unsettled` 的材料）逐日點名 ─────────────
    _ok8 = {"rw_anchor": "08:00", "wo_open_at_build": [], "cal_open_tail": None}
    fl_docs = [dict(_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0)]), **_ok8),
               _d4("2026-08-02", [_row("segA", 10.0, 2.0, 6.0)]),                 # v7：無錨
               dict(_d4("2026-08-03", [_row("segA", 10.0, 2.0, 6.0)]), **dict(
                   _ok8, cal_open_tail={"from": "2026-08-04T06:01:00", "overlap_sec": 7140})),
               dict(_d4("2026-08-04", [_row("segA", 10.0, 2.0, 6.0)]), **dict(
                   _ok8, wo_open_at_build=["37203", "37100"])),
               dict(_d4("2026-08-05", [_row("segA", 10.0, 2.0, 6.0)]), **dict(
                   _ok8, cal_open_tail={"from": "x", "overlap_sec": 1,
                                        "resolved": "sealed_none"}))]
    fc = month_model(fl_docs, MONTH, today=TODAY)["coverage"]
    ck(fc["days_rw_anchor_legacy"] == ["2026-08-02"] and fc["n_days_rw_anchor_legacy"] == 1
       and fc["days_cal_open_tail"] == ["2026-08-03"] and fc["n_days_cal_open_tail"] == 1
       and fc["days_wo_open_at_build"] == ["2026-08-04"]
       and fc["wo_open_at_build_by_day"] == {"2026-08-04": ["37203", "37100"]},
       "★ #182-P2：錨不可考／日曆尾巴未定形／建帳時工單未結三欄逐日點名（已 resolved 的尾巴不算）")
    ck(month_model([_d4("2026-08-01", [_row("segA", 1.0, 0.0, 1.0,
                                              roster_pad=True)])], MONTH, today=TODAY)
       ["coverage"]["rw_pad_mixed"] is False,
       "★ N=0 unpadded／只有舊檔都不叫混尺，不常駐揭露")
    ck(_rw_roster_pad_of_doc(_d4("x", [_row("segA", 1.0, 0.0, 1.0,
                                                 roster_pad=False)])) == (False, True, True)
       and _rw_roster_pad_of_doc(_d4("x", [_row("segA", 1.0, 0.0, 1.0)])) == (False, False, False),
       "_rw_roster_pad_of_doc 明分 False 與 legacy None")

    # ── ★ 2026-09-04 src_err 防線：時間源當時讀不到的日子要**看得見**（事故＝2026-08-11）──
    #   那種日帳的每一個既有誠實欄都看不見它：wo=ok、rw_axis=True、站列的 rw 還是新尺
    #   ⇒ n_degraded／n_days_rw／n_days_rw_legacy 三欄全部「正常」，而整天的停機是 None。
    def _srow(cls, note):
        """讀不到／不可測的站列（時間欄全 None＝**沒量到**，不是量到 0）。"""
        return {"cls": cls, "unmeasurable": True, "note": note,
                "producing_h": None, "wo_union_h": None, "wo_h": {},
                "starved_h": None, "blocked_h": None, "unexplained_h": None,
                "units": _uu(99, 1), "rw": None}

    se_docs = [
        _d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0)]),                    # 乾淨日
        _d4("2026-08-02", [_row("segA", 10.0, 2.0, 6.0),                      # 一站讀不到
                           _srow("segB", NOTE_SRC_ERR)]),
        _d4("2026-08-03", [_srow("segA", NOTE_SRC_ERR),                       # 08-11 的形：
                           _srow("segB", "no_fleet")]),                       #   整天零可測
    ]
    se = month_model(se_docs, MONTH, today=TODAY)["coverage"]
    ck(se["n_days_src_err"] == 2 and se["days_src_err"] == ["2026-08-02", "2026-08-03"],
       "★ coverage.n_days_src_err／days_src_err＝有站時間源讀不到的日子（逐日列出來，"
       "月報要指得出是哪幾天）")
    ck(se["n_days_src_err_all"] == 1,
       "★ n_days_src_err_all＝**整天零可測**的日子（凍結閘吃這一個：那天在軸的意義上"
       "等於缺日）——只有一站讀不到的 08-02 不算")
    _se_ctrl = month_model([_d4(f"2026-08-0{i}", [_row("segA", 10.0, 2.0, 6.0)])
                            for i in (1, 2, 3)], MONTH, today=TODAY)["coverage"]
    ck(all(se[k] == _se_ctrl[k] for k in ("n_days_present", "n_days_rw", "n_degraded",
                                          "missing_days", "n_days_rw_legacy",
                                          "n_bad_docs"))
       and se["n_days_present"] == 3,
       "★ 既有鍵的語意一個位元不動（present／rw／degraded／missing／legacy／bad 全部與"
       "「同樣三天但沒有 src_err」的對照組相同）——新欄是**加法**")
    ck(month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0)])],
                   MONTH, today=TODAY)["coverage"]["n_days_src_err"] == 0
       and month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0)])],
                       MONTH, today=TODAY)["coverage"]["days_src_err"] == [],
       "★ 乾淨的月 ⇒ 0／空列（零天的揭露不渲染，見 `_monthly_body.html` 的例外才出現）")
    ck(month_model([_d4("2026-08-01", [_srow("segA", "no_fleet")])],
                   MONTH, today=TODAY)["coverage"]["n_days_src_err_all"] == 0,
       "★ 整天不可測但**沒有任何 src_err**（結構性）⇒ 不算 src_err_all："
       "那是「這天真的量不到」，不是「當時讀不到」——要做的事完全不同")
    ck(_src_err_of_doc({"stations": [{"cls": "a", "note": NOTE_SRC_ERR}]}) == (True, False)
       and _src_err_of_doc({}) == (False, False),
       "_src_err_of_doc：缺 `unmeasurable` 鍵的站算**可測**（保守）；無站列 ⇒ 兩答皆假")
    _se_snap = snapshot_doc(month_model(se_docs, MONTH, today="2026-09-05"), "x")
    ck(validate_snapshot(_se_snap)[0] is True,
       "★ 帶新 coverage 鍵的快照驗得過（coverage 沒有閉集）")
    _old_snap = json.loads(json.dumps(_se_snap))
    for _k in ("n_days_src_err", "days_src_err", "n_days_src_err_all"):
        _old_snap["coverage"].pop(_k)
    ck(validate_snapshot(_old_snap)[0] is True
       and _fill_rw_defaults(_old_snap)["coverage"].get("n_days_src_err") is None,
       "★ **舊快照**（沒有這三個鍵）照樣驗得過，讀回來是 None＝「沒算過」不是 0（C-MR8）")
    # ★★★ **凍結月帳讀值不變**（#83 紅線；`SPEC-CAPACITY-FORECAST.md` §5）：
    #   已凍的快照是**落檔值、永不重算** ⇒ 加了 W1／W3 之後把它讀回來，
    #   **每一個既有欄一個位元都不變**，新欄一律 None＝「那個月沒算過座」。
    _frozen = json.loads(json.dumps(_se_snap))
    for _r in _frozen["stations"]:
        for _k in SEAT_STATION_KEYS:
            _r.pop(_k, None)
    _read_back = _fill_rw_defaults(json.loads(json.dumps(_frozen)))
    ck(validate_snapshot(_frozen)[0] is True
       and all(_read_back["stations"][i][k] == _r[k]
               for i, _r in enumerate(_frozen["stations"]) for k in _r)
       and _read_back["line"] == _frozen["line"]
       and all(_read_back["stations"][i][k] is None
               for i in range(len(_frozen["stations"])) for k in SEAT_STATION_KEYS),
       "★★ 凍結月帳：既有欄逐位元不變、W1／W3 新欄補 None（絕不重算歷史）")

    # U > 1（生產落在應生產時段之外）⇒ None、**絕不 clamp**
    hot = month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 30.0)])],
                      MONTH, today=TODAY)["stations"][0]
    ck(hot["u_pct"] is None and hot["rw_note"] == "u_gt_1",
       "★ U>1 ⇒ None＋rw_note（夾成 100% 會把矛盾藏起來）")
    ck(hot["a_pct"] is not None and hot["oee_pct"] is None,
       "U 不可信 ⇒ OEE 不給；但 A 照給（A 沒有被污染）")

    # Σplanned == 0（整月停線／週日）⇒ 三欄 None，但小時欄照給（量到的 0 ≠ 沒量）
    z0 = month_model([_d4("2026-08-01", [_row("segA", 0.0, 0.0, 0.0)])],
                     MONTH, today=TODAY)["stations"][0]
    ck(z0["n_days_rw"] == 1 and z0["planned_h"] == 0.0, "planned 0 是量到的 0")
    ck(z0["a_pct"] is None and z0["u_pct"] is None and z0["oee_pct"] is None
       and z0["rw_note"] == "no_planned",
       "★ Σplanned==0 ⇒ A／U／OEE 全 None（C-RW10：不是 0% 也不是 100%）")

    # 良率算不出來 ⇒ OEE None（**不以 100% 頂替**），A／U 照給
    ny = month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0, pas=0, fail=0)])],
                     MONTH, today=TODAY)["stations"][0]
    ck(ny["yield_pct"] is None and ny["oee_pct"] is None and ny["a_pct"] is not None
       and ny["rw_note"] == "no_yield", "良率 None ⇒ OEE None、A／U 照給")

    # v3 日帳（完全沒有 rw／rw_axis 這些鍵）⇒ 七欄全 None、其餘欄照常
    v3doc = _d4("2026-08-01", [_row("segA", None, 0.0, 6.0)], ver="board.daily_ledger.v3")
    for _k in ("rw_axis", "rw_reason", "families_status"):
        v3doc.pop(_k)
    v3doc["stations"][0].pop("rw")
    v3m = month_model([v3doc], MONTH, today=TODAY)
    v0 = v3m["stations"][0]
    ck(v0["n_days_rw"] == 0
       and all(v0[k] is None for k in RW_STATION_KEYS if k != "n_days_rw"),
       "★ v3 日帳（零 rw）⇒ A／U／OEE 欄全 None、n_days_rw=0")
    ck(v3m["line"]["a_pct"] is None and v3m["line"]["n_stations_rw"] == 0
       and v3m["coverage"]["n_days_rw"] == 0, "線級／涵蓋同理")

    # ★ 負斷言：把 fixture 的 rw 全剝掉（＝v3 形）⇒ **既有欄逐位元不變**
    stripped = json.loads(json.dumps(docs))
    for _d in stripped:
        for _k in ("rw_axis", "rw_reason", "families_status"):
            _d.pop(_k, None)
        _d["schema_ver"] = "board.daily_ledger.v3"
        for _s in _d["stations"]:
            _s.pop("rw", None)
    m_v3 = month_model(stripped, MONTH, today=TODAY)
    diff = [(a["cls"], k) for a, b in zip(m["stations"], m_v3["stations"])
            for k in a
            if k not in RW_STATION_KEYS + SEAT_STATION_KEYS and a[k] != b[k]]
    ck(not diff, f"★ 負斷言：加 rw 不得改動任何既有站欄，實得 {diff[:3]}")
    ck(not [k for k in m["line"] if k not in RW_LINE_KEYS
            and m["line"][k] != m_v3["line"][k]], "★ 負斷言：line 的既有欄也逐位元不變")
    ck(all(r[k] is None for r in m_v3["stations"]
           for k in RW_STATION_KEYS if k != "n_days_rw"), "剝掉 rw ⇒ 三欄全 None")

    # ── ★★ W1／W3 座欄（`SPEC-CAPACITY-FORECAST.md` §2／§5 釘）────────────────────
    def _board(cls, day_down_h, factor=None, seats=None, planned_h=10.0):
        """合成一天的站列：停機 `day_down_h` 小時、日帳可帶／不帶 `rw.equiv_factor`／`rw.seats`。"""
        r = _row(cls, planned_h, 0.0, 8.0, pas=100, fail=0)
        r["wo_union_h"] = day_down_h
        r["wo_h"] = {"維修": day_down_h}
        r["units"] = _uu(100, 0)
        r["units"]["r_good"] = 5.0                # 每天都合格 ⇒ 月尺＝5.0 PH/機台小時
        r["producing_h"] = 8.0
        if factor is not None:
            r["rw"]["equiv_factor"] = factor
        if seats is not None:
            r["rw"]["seats"] = seats
        return r

    def _wm(rows_by_day):
        return month_model([_d4(d, [r]) for d, r in rows_by_day], MONTH, today=TODAY)

    _DAYS = ["2026-08-%02d" % i for i in range(1, 7)]
    # ① 舊日帳（沒有 `equiv_factor`）⇒ **係數 1.0**＝接線前的數字，一個位元不變
    _w_legacy = _wm([(d, _board("segW", 4.0)) for d in _DAYS])["stations"][0]
    ck(_w_legacy["equiv_factor"] == 1.0 and _w_legacy["equiv_factor_days"] == 0
       and _w_legacy["equiv_factor_days_legacy"] == len(_DAYS)
       and abs(_w_legacy["down_equiv_ph"]
               - _w_legacy["down_h"] * _w_legacy["rate_ph_per_h"]) < 1e-6,
       "★ W1 舊日帳：缺 `equiv_factor` ⇒ 當量＝停機 × 尺（係數 1.0、逐位元同接線前）")
    # ② 係數 0.5／0.25／1 三例（§5）：當量 ≡ down_h × rate × factor
    for _f in (0.5, 0.25, 1.0):
        _w = _wm([(d, _board("segW", 4.0, factor=_f)) for d in _DAYS])["stations"][0]
        ck(abs(_w["equiv_factor"] - _f) < 1e-9
           and abs(_w["down_equiv_ph"] - _w["down_h"] * _w["rate_ph_per_h"] * _f) < 1e-6,
           f"★ W1 係數 {_f}：當量 ≡ 停機小時 × 月尺 × 係數")
    # ③ **按停機小時加權**（不是把每天的係數平均）：1h×0.25 ＋ 9h×1.0 ⇒ 0.925
    _w_mix = _wm([("2026-08-01", _board("segW", 1.0, factor=0.25)),
                  ("2026-08-02", _board("segW", 9.0, factor=1.0))])["stations"][0]
    ck(abs(_w_mix["equiv_factor"] - 0.925) < 1e-9,
       f"★ W1 係數按停機小時加權（不是逐日平均 0.625），實得 {_w_mix['equiv_factor']}")
    # ④ 一天有係數、一天沒有 ⇒ 沒有的那天以 1.0 計，並把兩種日子各自數出來
    _w_half = _wm([("2026-08-01", _board("segW", 5.0, factor=0.5)),
                   ("2026-08-02", _board("segW", 5.0))])["stations"][0]
    ck(abs(_w_half["equiv_factor"] - 0.75) < 1e-9
       and _w_half["equiv_factor_days"] == 1 and _w_half["equiv_factor_days_legacy"] == 1,
       "★ W1 混合月：缺欄的日子以 1.0 計、兩種日子分別現形")
    # ⑤ 整月零停機 ⇒ 係數 None（沒有可加權的東西；**不是** 1.0 也不是 0）
    ck(_wm([(d, _board("segW", 0.0, factor=0.5)) for d in _DAYS])["stations"][0]["equiv_factor"]
       is None, "★ W1 零停機 ⇒ equiv_factor None（C-MR3：沒量到就是 None）")
    # ⑥ W3 座欄：取**月內最後一天**那一份；整月無 `seats` ⇒ `seats_legacy` True
    _w_seat = _wm([("2026-08-01", _board("segW", 4.0, factor=0.5,
                                      seats={"installed": 4, "active": 3, "dormant": ["E1/nest:2"]})),
                   ("2026-08-02", _board("segW", 4.0, factor=0.5,
                                      seats={"installed": 4, "active": 2,
                                             "dormant": ["E1/nest:2", "E1/nest:4"]}))
                   ])["stations"][0]
    ck(_w_seat["seats_installed"] == 4 and _w_seat["seats_active"] == 2
       and _w_seat["seats_dormant"] == ["E1/nest:2", "E1/nest:4"]
       and _w_seat["seats_legacy"] is False,
       "★ W3 月列座帳＝月內最後一天的切面（逐日 as-of＝C-CF4）")
    ck(_w_legacy["seats_legacy"] is True and _w_legacy["seats_installed"] is None
       and _w_legacy["seats_days_legacy"] == len(_DAYS),
       "★ W3 舊日帳：`seats_legacy` True ＋ 座數 None（＝未記錄，**不是**零長停）")
    # ⑦ **C-CF8 負斷言**：只改座狀態（`seats`）而不動任何秒欄 ⇒ A／U／OEE 逐位元相等
    _u_before = _wm([(d, _board("segW", 4.0, factor=0.5,
                             seats={"installed": 4, "active": 4, "dormant": []}))
                     for d in _DAYS])["stations"][0]
    _u_after = _wm([(d, _board("segW", 4.0, factor=0.5,
                            seats={"installed": 4, "active": 1,
                                   "dormant": ["E1/nest:2", "E1/nest:3", "E1/nest:4"]}))
                    for d in _DAYS])["stations"][0]
    ck(all(_u_before[k] == _u_after[k] for k in RW_STATION_KEYS)
       and _u_before["down_equiv_ph"] == _u_after["down_equiv_ph"],
       "★★ C-CF8 負斷言：同一份日帳只改座狀態 ⇒ 妥善率／稼動率／OEE 一個位元都不動")

    # ── fixture 上的 rw（`_gen_monthly_fixtures` 產：部分天 rw_axis False、一站刻意無 rw）──
    rws = [r for r in m["stations"] if r["n_days_rw"]]
    ck(len(rws) >= 2, f"fixture 必須有帶 rw 的站（實得 {len(rws)}）")
    ck(m["coverage"]["n_days_rw"] < m["coverage"]["n_days_present"],
       "★ fixture 要有 rw_axis False 的日（否則「部分天沒有 RW 軸」這條路沒被走過）")
    norw = by["segAutomatedOQC"]
    ck(norw["n_days_rw"] == 0 and norw["a_pct"] is None and norw["u_pct"] is None
       and norw["oee_pct"] is None,
       "★ fixture 刻意留一站完全沒有 rw ⇒ A／U／OEE 顯「—」（C-RW9）")
    ck(norw["down_h"] is not None and norw["down_h"] > 0,
       "無 rw 的站仍有 24h 軸停機小時（兩把尺互不頂替）")
    frame2 = by["segFrameBonding"]
    ck(frame2["n_days_rw"] > 0 and frame2["oee_pct"] is not None, "fixture 主力站算得出 OEE")
    ck(abs(frame2["oee_pct"] - frame2["a_pct"] * frame2["u_pct"] / 100.0
           * frame2["yield_pct"] / 100.0) < 1e-4, "站級 OEE ≡ A×U×Y")
    ck(0 < frame2["a_pct"] <= 100.0 and 0 < frame2["u_pct"] <= 100.0,
       "fixture 的 A／U 落在 (0,100]")

    # ── 線級恆等式：Σ 的就是站列印出來的那些數字（不是第二條聚合路徑）──────────
    ln = m["line"]
    ck(abs(ln["planned_h"] - round(sum(r["planned_h"] for r in rws), 6)) < 1e-9
       and abs(ln["health_down_h"] - round(sum(r["health_down_h"] for r in rws), 6)) < 1e-9
       and abs(ln["producing_rw_h"] - round(sum(r["producing_rw_h"] for r in rws), 6)) < 1e-9,
       "★ 線級恆等式：Σ站 ≡ line 的三個小時欄")
    _up = ln["planned_h"] - ln["health_down_h"]
    ck(abs(ln["a_pct"] - 100.0 * _up / ln["planned_h"]) < 1e-5, "A_line＝Σ站up/Σ站planned")
    ck(abs(ln["u_pct"] - 100.0 * ln["producing_rw_h"] / _up) < 1e-5, "U_line＝Σprod/Σup")
    ck(abs(ln["oee_pct"] - ln["a_pct"] * ln["u_pct"] / 100.0 * ln["rty_pct"] / 100.0) < 1e-4,
       "OEE_line＝A×U×RTY（C-MR2：線級的 Q 只有 RTY）")
    ck(ln["n_stations_rw"] == len(rws) and ln["n_days_rw"] == max(r["n_days_rw"]
                                                                 for r in rws),
       "n_stations_rw＝有 rw 的站數；線級 n_days_rw＝站的 max")
    no_rty = month_model([_d4("2026-08-01", [_row("segA", 10.0, 2.0, 6.0, pas=0, fail=0)])],
                         MONTH, today=TODAY)["line"]
    ck(no_rty["rty_pct"] is None and no_rty["oee_pct"] is None
       and no_rty["a_pct"] is not None, "★ RTY None ⇒ 線級 OEE None（不以 100% 頂替）")

    # ── 舊快照（M3 之前凍結的）缺 A／U／OEE 欄 ⇒ 讀端補 None、不是 0（C-MR8 加法相容）──
    with tempfile.TemporaryDirectory() as td3:
        old = json.loads(json.dumps(m))
        for r in old["stations"]:
            for k in RW_STATION_KEYS:
                r.pop(k, None)
        for k in RW_LINE_KEYS:
            old["line"].pop(k, None)
        old["coverage"].pop("n_days_rw", None)
        ck(validate_snapshot(snapshot_doc(old, "x"))[0],
           "★ validate_snapshot 不強制新鍵（新增欄不得把已凍結的舊月判成非法）")
        okd, _w = write_snapshot(old, td3, "2026-09-02T08:00:00")
        back = read_snapshot(td3, "2026-08")
        ck(okd and back is not None, "缺新欄的舊快照仍寫得出、讀得回")
        ck(all(r[k] is None for r in back["stations"] for k in RW_STATION_KEYS),
           "★ 舊快照缺欄 ⇒ 讀端補 None（不是 0）")
        ck(all(back["line"][k] is None for k in RW_LINE_KEYS)
           and back["coverage"]["n_days_rw"] is None, "線級／涵蓋欄同理")
        ck(back["line"]["qlik_fail"] == ln["qlik_fail"], "回填不得動到既有欄")
        ck(list_snapshots(td3) == ["2026-08"], "回填不影響 list_snapshots")

    # ── (20) ★ #98 家族拆 PH（`SPEC-LANDING.md` §18.18）：月級只加總、不算式子 ─────
    def _fam_row(cls, pas, split, planned_h=10.0):
        r = _row(cls, planned_h, 0.0, 8.0, pas=pas, fail=0)
        if split is not None:
            r["units"]["units_by_family"] = dict(split)
        return r

    _FD = ["2026-08-01", "2026-08-02"]
    m_f = month_model([_d4(_FD[0], [_fam_row("segF", 10, {"Plover": 7, "Kestrel": 3})]),
                       _d4(_FD[1], [_fam_row("segF", 5, {"Kestrel": 5})])],
                      MONTH, today=TODAY)
    rf = m_f["stations"][0]
    ck(rf["qlik_pass"] == 15 and rf["qlik_pass_by_family"] == {"Plover": 7, "Kestrel": 8},
       "★ 家族拆分＝逐日相加；`qlik_pass` 一個位元不動")
    ck(sum(rf["qlik_pass_by_family"].values()) == rf["qlik_pass"],
       "★★ 恆等式：Σ qlik_pass_by_family ≡ qlik_pass（月級這一層）")
    ck(list(rf["qlik_pass_by_family"]) == ["Plover", "Kestrel"],
       "鍵序固定 R→A→M→未標（零家族不寫鍵、絕不印 0）")
    ck(rf["n_days_family"] == 2 and rf["n_days_family_legacy"] == 0, "涵蓋欄誠實計數")
    # 混天（有產出但那天沒有這一腿）⇒ 整片 None（**絕不印加不回總數的拆分**）
    m_mix = month_model([_d4(_FD[0], [_fam_row("segF", 10, {"Plover": 10})]),
                         _d4(_FD[1], [_fam_row("segF", 5, None)])], MONTH, today=TODAY)
    rm = m_mix["stations"][0]
    ck(rm["qlik_pass"] == 15 and rm["qlik_pass_by_family"] is None
       and rm["n_days_family"] == 1 and rm["n_days_family_legacy"] == 1,
       "★ 混天 ⇒ 拆分整片 None、涵蓋欄講得出為什麼（回補要看的那一格）")
    m_none = month_model([_d4(_FD[0], [_fam_row("segF", 10, None)])], MONTH, today=TODAY)
    ck(m_none["stations"][0]["qlik_pass_by_family"] is None
       and m_none["stations"][0]["n_days_family"] == 0,
       "★ 一天都沒有這一腿 ⇒ None（『沒有那一軸』≠『那一軸是空的』）")
    m_zero = month_model([_d4(_FD[0], [_fam_row("segF", 0, {})])], MONTH, today=TODAY)
    ck(m_zero["stations"][0]["qlik_pass_by_family"] == {}
       and m_zero["stations"][0]["n_days_family"] == 1,
       "★ 拆了但整月零產出＝`{}`（與 None 語意不同）")
    ck(FAMILY_ORDER == ("Plover", "Kestrel", "Heron", "UNLABELED"),
       "FAMILY_ORDER 鍵序 pin（語意權威＝mes_qlik.family_keys()，run_smoke 有 cross-pin）")
    # ★ 負斷言：加了家族拆分，既有站欄／line 一個位元不動
    _base_docs = [_d4(_FD[0], [_fam_row("segF", 10, {"Plover": 10})])]
    _strip_docs = json.loads(json.dumps(_base_docs))
    for _d in _strip_docs:
        for _s2 in _d["stations"]:
            (_s2.get("units") or {}).pop("units_by_family", None)
    _mb, _ms = month_model(_base_docs, MONTH, today=TODAY), month_model(_strip_docs, MONTH,
                                                                       today=TODAY)
    _fam_keys = ("qlik_pass_by_family", "n_days_family", "n_days_family_legacy")
    ck(not [(a["cls"], k) for a, b in zip(_mb["stations"], _ms["stations"]) for k in a
            if k not in _fam_keys and a[k] != b[k]],
       "★ 負斷言：加家族拆分不得改動任何既有站欄")
    ck(_mb["line"] == _ms["line"], "★ 負斷言：line 逐位元不變（家族不上線級）")

    # ── (17) 單位文案：全檔不得出現「顆」／pcs（Chris 2026-09-02）─────────────
    src = open(os.path.abspath(__file__), "r", encoding="utf-8").read()
    # 檔頭 docstring 與 selftest 本身不算（兩處都要引「不准用的字」才講得清規則）
    body = src.split('"""', 2)[-1].split("def _selftest", 1)[0]
    ck("pcs" not in body.lower(), "碼區不得出現 pcs")
    ck("顆" not in body, "碼區不得出現「顆」（單位一律 PH）")
    ck(all("顆" not in v for vs in ASSUMPTIONS.values() for v in vs), "假設文案一律用 PH")

    # ── (19) #103 產品子樹月帳（D1／D2；`_SPEC-QLIK-YIELD.md` §12）──────────────────
    _blk = {"product": "kestrel", "n_stations": 2, "degraded": False, "note": None,
            "stations": [
                {"cls": "segGrout", "ord": 18, "display": None, "display_pending": True,
                 "qlik_pass": 40, "qlik_fail": 2, "qlik_total": 42, "n_null": 0,
                 "units_by_family": {"Kestrel": 40}},
                {"cls": "segFrameBonding", "ord": 3, "display": "Frame Bonding",
                 "display_pending": False, "qlik_pass": 30, "qlik_fail": 0,
                 "qlik_total": 30, "n_null": 0, "units_by_family": {"Kestrel": 30}}]}
    _draw = load_sample_docs()
    _dnone = [{k: v for k, v in json.loads(json.dumps(d)).items()
               if k != PRODUCTS_KEY} for d in _draw]
    _dp = [dict(json.loads(json.dumps(d)), products={"kestrel": _blk}) for d in _dnone]
    _mr = month_model(_dnone, MONTH, today=TODAY)
    _mr2 = month_model(_dp, MONTH, today=TODAY)
    ck(json.dumps(_mr["stations"], sort_keys=True, default=str)
       == json.dumps(_mr2["stations"], sort_keys=True, default=str)
       and _mr["line"] == _mr2["line"],
       "★★ 負斷言（D1）：加了 products 子樹之後，Plover 月模型**逐位元不變**")
    _ma = month_model(_dp, MONTH, today=TODAY, product="kestrel")
    _arow = {r["cls"]: r for r in _ma["stations"]}
    _nday = _ma["coverage"]["n_days_product"]
    ck(_ma["product"] == "kestrel" and _mr["product"] == "plover"
       and set(_arow) == {"segGrout", "segFrameBonding"} and _nday > 0,
       "(19a) product=kestrel ⇒ 站列來自子樹（含 Plover 沒有的 segGrout）")
    _frame_plover = {r["cls"]: r for r in _mr2["stations"]}["segFrameBonding"]["qlik_pass"]
    ck(_arow["segGrout"]["qlik_pass"] == 40 * _nday
       and _arow["segFrameBonding"]["qlik_pass"] == 30 * _nday
       and _arow["segFrameBonding"]["qlik_pass"] != _frame_plover,
       "(19b) ★ 兩棵樹永不相加：同名站 `segFrameBonding` 在兩個模型是兩個獨立的數")
    ck(all(_arow["segGrout"][k] is None
           for k in ("down_h", "down_equiv_ph", "rate_ph_per_h", "a_pct", "u_pct",
                     "oee_pct", "rework_pct", "attempts")),
       "(19c) Kestrel 沒有時間腿／重工腿 ⇒ 那些欄一律 None＝顯「—」（不是 0）")
    ck(_arow["segGrout"]["qlik_pass_by_family"] == {"Kestrel": 40 * _nday}
       and _arow["segGrout"]["yield_pct"] is not None,
       "(19d) 家族拆分與良率照算（子樹自己那把尺、恆等式守衛同一支）")
    _mempty = month_model(_dnone, MONTH, today=TODAY, product="kestrel")
    ck(_mempty["stations"] == [] and _mempty["coverage"]["n_days_product"] == 0
       and _mempty["line"]["rty_pct"] is None and _mempty["line"]["n_stations"] == 0,
       "(19e) ★ D2：整月一天都沒有子樹（＝八月）⇒ **整片「—」**（零站列），不是零")
    _mix = [dict(d) for d in _dp]
    _mix[0] = {k: v for k, v in _mix[0].items() if k != PRODUCTS_KEY}
    _mm = month_model(_mix, MONTH, today=TODAY, product="kestrel")
    ck(_mm["coverage"]["n_days_product"] == _nday - 1
       and _mm["stations"] and _mm["coverage"]["days_product_missing"] == [
           str(_mix[0]["day"])[:10]],
       "(19f) ★ D2：混天的守衛**只在被要求的產品內**——缺子樹的日子整天不計、"
       "有子樹的日子照樣成帳（不會被拖成整片 None）")
    ck(snapshot_product_model(snapshot_doc(_mr, "x", products={"kestrel": _ma}),
                              "kestrel")["product"] == "kestrel"
       and snapshot_product_model(snapshot_doc(_mr, "x"), "kestrel") is None
       and snapshot_product_model(snapshot_doc(_mr, "x"), "plover")["month"] == MONTH
       and validate_snapshot(snapshot_doc(_mr, "x", products={"kestrel": _ma}))[0],
       "(19g) 快照與月一起凍 Kestrel 子樹；沒凍過 ⇒ None（顯「—」、絕不回本體）")
    ck(_product_stations_of_doc(_dp[0], "kestrel") is not None
       and _product_stations_of_doc(_dnone[0], "kestrel") is None
       and _product_stations_of_doc(_dp[0], "heron") is None,
       "(19h) 子樹讀取：沒有那一棵 ⇒ None（**永不回 {}**）")

    # ── (18) 兩份 fixture ＝產生器的決定性產物（手改就紅；慣例＝`_gen_product_tags --check`）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _gen_monthly_fixtures as _gen
    ck(_gen.main(["--check"]) == 0,
       "★ fixture 必須 ≡ `_gen_monthly_fixtures.py` 的產物（手改＝第二份形狀定義）")

    print(f"board_monthly selftest: {n[0] - len(fails)}/{n[0]} PASS")
    for f in fails:
        print("  FAIL:", f)
    return 0 if not fails else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tendline Board 月報聚合核心（純函式）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（零 MES、零真日帳）")
    ap.add_argument("--month", help="用仿真日帳算一個月、印摘要（demo 源）")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    month = a.month or "2026-08"
    m = month_model(load_sample_docs(), month,
                    today=_dt.date.today().strftime("%Y-%m-%d"))
    print(f"{m['month']} partial={m['partial']} as_of={m['as_of']} "
          f"({m['coverage']['n_days_present']}/{m['coverage']['n_days_expected']} days)")
    for r in m["stations"]:
        tot = "—" if r["loss_total_ph"] is None else f"{r['loss_total_ph']:,.0f} {UNIT}"
        print(f"  {r['cls']:<46} {tot:>14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
