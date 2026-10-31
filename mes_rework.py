#!/usr/bin/env python3
"""Tendline Board — 昨日重工/復測（窗內嘗試列計數）計算核心 `board.rework.v1`。

**這是什麼**：/daily「顆數帳本」的重工腿（SPEC-DAILY-UNIT.md Part A §2 的落地）。
回答一件事——**「昨天每站有多少次『嘗試』其實是同一顆在重做？」**
**不是什麼**：不是良率（`mes_qlik.py` first-FAIL 口徑）、不是 WIP 佇列（`mes_wip.py`）、
不是稼動（`mes_timeline.py`）。本檔是 Tendline Board 的**第三種語意＝窗內活動計數**。

語意權威（分歧一律以 SPEC 為準、不以本檔註解為準）：
  · `SPEC-DAILY-UNIT.md` Part A §2（口徑）／§2.4（模組形狀 pin）／§8（selftest 清單）。

一句話語意：昨日窗 `[Y 00:00, Y+1 00:00)`（台北 naive）內，per-站（(sgc, mcl) 鍵）
`attempts ＝ 窗內 B 列數`、`first_attempts ＝ distinct lot 數`、
`rework_n ＝ attempts − first_attempts`（＝Σ per-lot max(0, n−1)）。
**「次≈顆」假設**（1 次嘗試 ≈ 1 顆產能機會）＝消費層顯示恆帶 ≈；待 Chris Q1 裁決。

★ 承重裁決（SPEC §2.1；違反即 HIGH）
  · **單位鍵＝lot（module 序號），不用 AllPHID**——重工是**設備視角**（同一顆在同站被
    機台重複做＝被吃掉的產能），與 Sorting/Fresh 維度無關；AllPHID 的 `sf` 會把「包裝後
    回頭重做」拆成新單位＝漏算重工，且 [PK] 全史包裝邊界為一個窗內計數拉全史＝成本不成比例。
  · **站鍵＝(sgc_raw, mcl)**＝與 43 站 `(cls, mcl)` join 維度對齊（同 sgc 不同 mcl＝不同站列）。
  · **與 Qlik output 的關係（消費面 footer 必講）**：Qlik `total`＝per-AllPHID 末次收斂後
    一單位一列 ⇒ 同單位復測 N 次在 Qlik 仍是 1 ⇒ **重工天生不灌水 Qlik output**。
    重工帳算的正是被 Qlik 塌掉的那部分（第 2+ 次嘗試列）＝兩帳**互補、零雙計**。

★ 鐵則（違反即作廢）
  · 站序只有一份＝`mes_qlik.QLIK_STATIONS`（import 重用、絕不另抄一個位元、絕不
    runtime discovery＝M4 教訓）。
  · `_connect`／`_process01_where`／column guard 一律 **import `mes_qlik` 重用不抄**
    （單一連線語意/口徑來源；同 `mes_wip` 慣例）。
  · **主帳只算 B 前綴**（與 Qlik/WIP 主線一致）；M0 列另計 `diag.n_m0_attempts`
    （自揭、不進帳；v1 取捨寫消費面 footer）。
  · 不在 43 站白名單的 (sgc, mcl) 列＝`diag.n_offroute_attempts`（自揭、不進 43 列帳）。
  · **§24 人名紅線**：本模組不取、不回、不記任何姓名/工號欄位；`stpPersonID` 於全檔
    SQL 文本**零出現**（selftest 負斷言釘死＝`ALL_SQL()`）。

★ perf lore（B4/M4 教訓；改本檔前先讀）
  · **單段窗掃、不需要兩段式 `#temp`**（SPEC §2.3）：本查詢無「窗內 lot 的全史」步——
    母體就是窗內列本身（＝`mes_wip` [W1] 同型）。單日窗實測量級 ~2 萬列（R4：7d=142k）
    ⇒ 秒級。其餘 perf 鐵則全守：view 上零 DISTINCT/GROUP BY（聚合全 Python）、
    SQL 尾 `OPTION (RECOMPILE)`、無動態 lot IN-list。
  · 時戳只用 `stpStartTime`、**不碰 `stpEndTime`**（`>` 會刪掉合法零工期段、八站整站
    隱形＝[[mes-zero-length-segments]]；本檔連讀都不讀）。

★ 唯讀：對 view 只有 SELECT；零 DDL/DML（連 `#temp` 都不需要）。
★ 凍結模組 `mes_timeline.py` / `mes_yield.py`：不 import、不執行、零 diff。
★ 降級語意：承重欄 ABSENT ⇒ `SchemaDivergence`（消費端接成 unavailable、絕不跑全零帳）；
  `mclMaterialClassID` ABSENT ⇒ **degraded=True 自揭**、站鍵退化為 sgc-only（cls 單鍵；
  43 個 cls 互異＝無歧義，selftest 0.x 釘死）＋caveat——絕不默默當正常。

CLI:
    python mes_rework.py --day 2026-08-01 [--json]     # 指定日窗 [day, day+1)
    python mes_rework.py [--json]                      # 未給 --day＝昨天
    python mes_rework.py --selftest                    # 純離線合成資料、不連 MES
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta

# ── 語意權威＋積木一律 import 重用（SPEC §2.4 pin；43 站序不另抄一個位元）──────────
from mes_qlik import (                      # noqa: F401
    QLIK_STATIONS, QLIK_DISPLAY, MLC_WHITELIST, EXCLUDED_CLASSES, LOT_PREFIXES,
    RESULT_KEEP, RESULT_PASS, RESULT_FAIL,
    SchemaDivergence, REQUIRED_SEG_COLUMNS, GUARD_KEYS,
    _connect, _sql_list, _process01_where, _dedup_rows, _dt, _win,
    fetch_column_guard, assert_guard,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# 1. 人工審定常數（SPEC §2.4 pin）
# =============================================================================
SCHEMA_ID = "board.rework.v1"
CALIBER_REWORK = ("in-window attempt count (B-prefix mainline; "
                  "rework_n = attempts - distinct lots per (cls,mcl) station)")

QUERY_TIMEOUT_SEC = 120                     # 單日窗 ~2 萬列＝秒級；寬裕上界防呆

# 窗內列 6 欄 tuple 的欄位索引（pin；純函式層全部吃這個形狀＝SQL SELECT 欄序）
I_PK, I_LOT, I_PSC, I_MLC, I_RES, I_TS = range(6)
ROW_ARITY = 6

MAIN_PREFIX = "J0"                          # 主帳前綴（與 Qlik/WIP 主線一致）
M0_PREFIX = "V0"                            # 另計自揭、不進帳（Q3 待 Chris 裁決升格與否）


# =============================================================================
# 2. I/O 層（SQL builder 純函式＝離線可斷言形狀；唯讀＝單條 SELECT、零 #temp）
# =============================================================================
def build_rework_sql(win, has_mcl=True):
    """昨日窗嘗試列拉取 SQL（SPEC §2.3 逐字落地）。回 `(sql, params)`。

    · **單段窗掃**（無兩段式 `#temp`）：母體＝窗內列本身，無「窗內 lot 的全史」步。
    · Process01 過濾＝`mes_qlik._process01_where` **逐字重用**，但**刻意 `with_result=False`**
      （2026-08-03 LOW C-6）：原本 SQL 端就濾掉 `stpTestResult` 非 PASS/FAIL 的列 ⇒
      「昨天有多少列判定是 NULL」在本模組**零自揭**（NULL 率若某天暴衝＝MES 寫入異常，
      而 attempts 只會安靜地變少）。改成母體收進來、**Python 端分桶記 `n_null_result_rows`
      且不進 attempts** ⇒ **口徑一個位元不變**、掃描量不變（窗過濾照舊、只多帶 NULL 列）。
    · `has_mcl=False`（欄 ABSENT 降級）：mcl 白名單那道自動消失（`_process01_where`
      內建分支）、SELECT 以 `N'(absent)'` 佔位保持 6 欄 arity——**全 SQL 文本零出現
      `mclMaterialClassID`**（selftest 釘死；欄不存在時引用即炸）。
    · 聚合零下推：view 上零 DISTINCT/GROUP BY（B4 斷崖教訓）、計數全在 Python。
    """
    ws, we = _win(win)
    sql = ("SELECT v.stpPK, v.matMaterialLotID, v.SegmentClassID,\n"
           "       " + ("v.mclMaterialClassID" if has_mcl else "N'(absent)'")
           + ", v.stpTestResult, v.stpStartTime\n"
           "FROM dbo.process_segment_v v\n"
           "WHERE v.stpStartTime >= ? AND v.stpStartTime < ?\n"
           + _process01_where(has_mcl, with_result=False) +
           "OPTION (RECOMPILE)")
    return sql, (ws, we)


def ALL_SQL():
    """本模組會送出的**全部 SQL 文本**（selftest 形狀/負斷言的唯一清單）。

    新增任何查詢**必須**登記在這裡——否則 `stpPersonID`/`stpEndTime` 零出現的
    負斷言會有漏網之魚（`mes_operator.ALL_SQL` 同款制度）。
    ⚠ column guard 的 SQL 屬 `mes_qlik.fetch_column_guard`（該檔 selftest 已驗）、
    不在本清單；本模組自己**只有這一條**查詢的兩個分支。
    """
    win = (datetime(2026, 1, 1), datetime(2026, 1, 2))
    return (build_rework_sql(win, has_mcl=True)[0],
            build_rework_sql(win, has_mcl=False)[0])


# =============================================================================
# 3. 純函式層（selftest 主戰場；全部離線可測）
# =============================================================================
def _station_key(sgc, mcl, has_mcl=True):
    """站鍵：正常＝(sgc, mcl)（與 43 站 join 維度對齊）；mcl ABSENT 降級＝sgc 單鍵。"""
    return (sgc, mcl) if has_mcl else sgc


def aggregate_attempts(rows, stations=QLIK_STATIONS, *, has_mcl=True):
    """窗內列 → per-站嘗試/重工計數（SPEC §2.2 口徑；純函式、零 I/O）。

    回（SPEC §2.4 pin）::

        {"stations": {鍵: {"attempts", "first_attempts", "rework_n", "n_fail_rows"}},
         "diag": {"n_rows", "n_m0_attempts", "n_offroute_attempts", "n_bad_rows",
                  "n_null_result_rows", "mcl_filter", "degraded", ...additive}}

    · 鍵＝`(cls, mcl)`（`has_mcl=False` 降級＝cls 單鍵；43 cls 互異＝無歧義）。
      **stations 每站必有一鍵**（空輸入＝43 鍵全在、全 0——「沒重工」與「沒資料」
      由消費端以 diag.n_rows 區分，站鍵不缺）。
    · `attempts`＝窗內 B 列數；`first_attempts`＝distinct lot 數；
      `rework_n = attempts − first_attempts`（整數精確、恆 ≥0）。
    · `n_fail_rows`＝raw FAIL 列數（**參考欄、非報廢口徑**——報廢＝Qlik first-FAIL，
      在 `get_qlik_grid` 那條腿；兩者母體不同、絕不互相頂替）。
    · **逐列分桶優先序（pin、先中先出）**：①壞列（arity 不足／lot 或 sgc 為 None）→
      `n_bad_rows`；**②判定非 PASS/FAIL（含 NULL）→ `n_null_result_rows`（不進 attempts）**；
      ③M0 前綴 → `n_m0_attempts`；④非 B0 前綴（Process01 結構上不可達；防呆）→
      `n_bad_rows`；⑤站鍵不在白名單 → `n_offroute_attempts`；⑥進帳。
      ②**故意排在 M0/off-route 之前**＝`n_null_result_rows` 是**全母體**的 NULL 率訊號
      （不分主線/M0/在不在 route），這樣它才答得了「MES 判定寫入是不是出事了」。
      ⚠ 合規輸入（SQL 母體本來就只有 PASS/FAIL 的舊行為）下②恆不中 ⇒
      `attempts`/`first_attempts`/`rework_n`/`n_fail_rows` **逐位不變**。
    """
    stations = tuple(stations)
    board = {}
    lots = {}
    for _o, cls, mcl in stations:
        k = _station_key(cls, mcl, has_mcl)
        board[k] = {"attempts": 0, "first_attempts": 0, "rework_n": 0, "n_fail_rows": 0}
        lots[k] = set()

    n_m0 = n_off = n_bad = n_null = 0
    for r in rows:
        if len(r) < ROW_ARITY:                              # ① 壞列（防呆、不炸）
            n_bad += 1
            continue
        lot, sgc, mcl, res = r[I_LOT], r[I_PSC], r[I_MLC], r[I_RES]
        if lot is None or sgc is None:                      # ① 壞列
            n_bad += 1
            continue
        if res not in RESULT_KEEP:                          # ② NULL 判定列：自揭、不進帳
            n_null += 1
            continue
        lp = str(lot)[:2]
        if lp == M0_PREFIX:                                 # ③ M0 自揭、不進帳
            n_m0 += 1
            continue
        if lp != MAIN_PREFIX:                               # ④ 結構上不可達（防呆）
            n_bad += 1
            continue
        k = _station_key(sgc, mcl, has_mcl)
        st = board.get(k)
        if st is None:                                      # ⑤ off-route 自揭、不進帳
            n_off += 1
            continue
        st["attempts"] += 1                                 # ⑥ 進帳
        if res == RESULT_FAIL:
            st["n_fail_rows"] += 1
        lots[k].add(lot)

    for k, st in board.items():
        st["first_attempts"] = len(lots[k])
        st["rework_n"] = st["attempts"] - st["first_attempts"]

    caveats = []
    if not has_mcl:
        caveats.append("mclMaterialClassID ABSENT -> station key degraded to cls only; "
                       "Process01 whitelist lost a dimension "
                       "(NOT company-caliber equivalent)")
    diag = {
        "n_rows": len(rows),
        "n_m0_attempts": n_m0,
        "n_offroute_attempts": n_off,
        "n_bad_rows": n_bad,
        "n_null_result_rows": n_null,       # 判定非 PASS/FAIL 的列（自揭、不進 attempts）
        "mcl_filter": "applied" if has_mcl else "SKIPPED(mcl ABSENT)",
        "degraded": not has_mcl,
        # --- additive 自揭（非 pin；消費端白名單讀取、未知欄忽略）---
        "caveats": caveats,
    }
    return {"stations": board, "diag": diag}


# =============================================================================
# 4. I/O 編排
# =============================================================================
def compute_rework(cur, win):
    """唯一 I/O 編排點：guard → 窗掃 → Python 去重 → `aggregate_attempts`。

    回 §2.4 pin 形＋additive：`schema`/`caliber`/`win`/`elapsed_s`、
    `diag.guard`/`diag.n_raw_rows`/`diag.n_dup_rows_removed`/`diag.timing`。
    · 承重欄 ABSENT ⇒ `SchemaDivergence` 往上拋（消費端 `board_data` 接成
      unavailable＝頁面「—」＋原因；**絕不跑出全零帳**——全零帳與「昨天真的零重工」
      在消費面長得一模一樣＝最壞的靜默謊）。
    · mcl ABSENT ⇒ degraded 路徑（SQL 零引用該欄、站鍵退化 cls 單鍵、diag 自揭）。
    · view 若吐完全重複列 ⇒ `_dedup_rows`（重用 `mes_qlik`）——重複列會**虛胖 rework_n**
      （同列算兩次嘗試＝假重工），去重後 `n_dup_rows_removed` 自揭。
    """
    t_all = time.perf_counter()
    timing = {}

    t0 = time.perf_counter()
    guard = fetch_column_guard(cur)                          # 承重缺欄在這裡就 raise
    timing["guard_ms"] = int((time.perf_counter() - t0) * 1000)
    has_mcl = guard["mclMaterialClassID"]

    ws, we = _win(win)
    sql, params = build_rework_sql((ws, we), has_mcl)
    t0 = time.perf_counter()
    cur.execute(sql, *params)
    raw = [tuple(r) for r in cur.fetchall()]
    timing["fetch_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    rows = _dedup_rows(raw)
    agg = aggregate_attempts(rows, has_mcl=has_mcl)
    timing["agg_ms"] = int((time.perf_counter() - t0) * 1000)
    timing["total_ms"] = sum(v for k, v in timing.items()
                             if k.endswith("_ms") and k != "total_ms")

    agg["diag"]["guard"] = dict(guard)
    agg["diag"]["n_raw_rows"] = len(raw)
    agg["diag"]["n_dup_rows_removed"] = len(raw) - len(rows)
    agg["diag"]["timing"] = timing
    return {
        "schema": SCHEMA_ID,
        "ok": True,           # 失敗一律 raise（消費端接成 unavailable）；品質旗＝diag.degraded
        "caliber": CALIBER_REWORK,
        "win": [ws.isoformat(), we.isoformat()],
        "stations": agg["stations"],
        "diag": agg["diag"],
        "elapsed_s": round(time.perf_counter() - t_all, 3),
    }


CANONICAL_KEYS = ("schema", "ok", "caliber", "win", "stations", "diag", "elapsed_s")
STATION_KEYS = ("attempts", "first_attempts", "rework_n", "n_fail_rows")
DIAG_PIN_KEYS = ("n_rows", "n_m0_attempts", "n_offroute_attempts", "n_bad_rows",
                 "n_null_result_rows", "mcl_filter", "degraded")


def run(day=None, timeout=QUERY_TIMEOUT_SEC):
    """CLI/snapshot 用的 live 編排（開一條連線、跑完關掉）。回 canonical。

    `day`＝日期字串/対象日（窗＝`[day 00:00, day+1 00:00)` 台北 naive）；
    未給＝**昨天**（本機今日 − 1；/daily 的窗語意）。
    """
    if day is None:
        d0 = date.today() - timedelta(days=1)
    else:
        d0 = _dt(str(day)).date() if not isinstance(day, date) else day
    ws = datetime(d0.year, d0.month, d0.day)
    win = (ws, ws + timedelta(days=1))
    conn, cur = _connect(timeout=timeout)
    try:
        return compute_rework(cur, win)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =============================================================================
# 5. 呈現（CLI）
# =============================================================================
def to_jsonable(res):
    """canonical → JSON 可序列化形（tuple 站鍵 → ord 序 station list）。

    ⚠ 只對 **預設 route（QLIK_STATIONS）** 的結果有意義；自訂 stations 的呼叫端
    自行攤平。degraded（cls 單鍵）路徑自動以 cls 取鍵。
    """
    has_mcl = res["diag"].get("mcl_filter") == "applied"
    st = res["stations"]
    rows = []
    for o, cls, mcl in QLIK_STATIONS:
        d = st.get(_station_key(cls, mcl, has_mcl))
        if d is None:
            continue
        rows.append({"ord": o, "cls": cls, "mcl": mcl,
                     "display": QLIK_DISPLAY.get(o, cls), **d})
    out = {k: v for k, v in res.items() if k != "stations"}
    out["stations"] = rows
    return out


def render(res):
    d = res["diag"]
    print(f"=== 重工/復測帳（{res['schema']}）　win={res['win'][0]} .. {res['win'][1]} ===")
    print(f"  口徑＝{res['caliber']}")
    if d["degraded"]:
        print("  ⚠ DEGRADED：mcl 欄降級（站鍵 cls 單鍵）、與公司口徑不再等價（見 diag.caveats）。")
    print(f"\n  ── 43 站（ord 序；rework_n＝attempts − distinct lot；次≈顆＝消費層恆帶 ≈）──")
    print(f"    {'#':>3} {'站':<44}{'attempts':>9}{'first':>7}{'rework':>7}{'failR':>7}")
    print("    " + "-" * 80)
    js = to_jsonable(res)
    for s in js["stations"]:
        print(f"    {s['ord']:>3} {s['display'][:43]:<44}{s['attempts']:>9}"
              f"{s['first_attempts']:>7}{s['rework_n']:>7}{s['n_fail_rows']:>7}")
    tot_a = sum(s["attempts"] for s in js["stations"])
    tot_r = sum(s["rework_n"] for s in js["stations"])
    print(f"\n  ── 線級 ──")
    print(f"    attempts 合計 {tot_a}／rework 合計 {tot_r}")
    print(f"\n  ── diag ──")
    for k in ("n_rows", "n_raw_rows", "n_dup_rows_removed", "n_m0_attempts",
              "n_offroute_attempts", "n_bad_rows", "n_null_result_rows",
              "mcl_filter", "degraded", "caveats", "timing"):
        if k in d:
            print(f"    {k:<24}{d.get(k)}")
    if res.get("elapsed_s") is not None:
        print(f"    {'elapsed_s':<24}{res['elapsed_s']}")


# =============================================================================
# 6. CLI
# =============================================================================
def _hint(e):
    low = str(e).lower()
    if isinstance(e, SchemaDivergence) or "schema divergence" in low or "invalid column" in low:
        return (" → **schema 分歧**（報表 view 少了承重欄）。不是連線問題、也不是昨天沒生產；"
                "先對 SPEC-DAILY-UNIT.md Part A §2 重新校 column guard，勿改數字。")
    if any(k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")):
        return " → 連不到 MES。確認已連 VPN/內網。"
    if "login failed" in low or "28000" in str(e):
        return " → 登入被拒。確認 AD 帳號對報表庫有讀權。"
    return ""


def main():
    ap = argparse.ArgumentParser(
        description="Tendline Board 重工/復測帳（昨日窗嘗試列計數；唯讀）")
    ap.add_argument("--day", default=None,
                    help="対象日 YYYY-MM-DD（窗＝[day, day+1)）；未給＝昨天")
    ap.add_argument("--json", action="store_true", help="輸出 canonical JSON")
    ap.add_argument("--selftest", action="store_true", help="離線確定性單測（不連 MES）")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    try:
        res = run(day=args.day)
    except Exception as e:
        print(f"[mes_rework] 查詢失敗: {type(e).__name__}{_hint(e)}\n  {str(e)[:300]}")
        sys.exit(2)

    if args.json:
        import json
        print(json.dumps(to_jsonable(res), ensure_ascii=False, default=str))
        return
    render(res)


# =============================================================================
# 7. 離線單測（純合成資料、零 MES、零網路；SPEC Part A §8 mes_rework 節逐項）
# =============================================================================
class _FakeCur:
    """離線假 cursor：讓 `compute_rework` 整條 I/O 編排在**零網路**下可測。

    只認得本模組真正會送出的兩種 SQL（`mes_qlik.fetch_column_guard` 的 [G0]＋
    本檔的窗掃）；其餘 SQL ⇒ 空結果（防呆）。
    """

    def __init__(self, guard_row, win_rows):
        self.guard_row = tuple(guard_row)
        self.win_rows = list(win_rows)
        self.sql_log = []
        self._next = []

    def execute(self, sql, *params):
        self.sql_log.append((sql, params))
        s = sql.strip()
        if s.startswith("SELECT CASE WHEN OBJECT_ID"):
            self._next = [self.guard_row]
        elif s.startswith("SELECT v.stpPK"):
            self._next = [tuple(r) for r in self.win_rows]
        else:
            self._next = []
        return self

    def fetchone(self):
        return self._next[0] if self._next else None

    def fetchall(self):
        return list(self._next)


def _guard_row(mcl=1):
    """[G0] guard row（**鍵序＝`mes_qlik.GUARD_KEYS` 現算**）；預設全 PRESENT。

    ⚠ 絕不硬編欄數／位置——guard 加一欄（如 2026-08-03 的 `rqs_rqsPK`）時，硬編版本會
    靜默錯位或 IndexError；本版跟著 `GUARD_KEYS` 走。
    """
    return tuple(mcl if k == "mclMaterialClassID" else 1 for k in GUARD_KEYS)


def _selftest():
    print("=== mes_rework --selftest（重工/復測窗內計數；純離線合成資料）===")
    from mes_qlik import _T                     # 測試小工具重用（不再抄一份斷言 harness）
    t = _T()

    PH, PC, PPC = "assyModule", "assyUnit", "assyPackagedUnit"
    DA = "segDiePlacement"                                   # ord 10
    WB = "segWireBonding"                                    # ord 16
    CAL = "segCalibrationTest"                       # ord 32
    AutoOQC = "segAutomatedOQC"       # ord 33
    VPACK = "segVacuumPackInspect"            # ord 43
    FILM = "segPostLamInspection"              # 不在 43 站白名單（隱形吸收站）
    SHIP = "segShipPackaging"                             # 不在 route（出貨段）

    Y = datetime(2026, 8, 1)                              # 昨日窗 [Y, Y+1)
    WIN = (Y, Y + timedelta(days=1))

    def TS(h, mi=0):
        return Y + timedelta(hours=h, minutes=mi)

    def R(pk, lot, sgc, mcl, res="PASS", ts=None):
        return (pk, lot, sgc, mcl, res, ts if ts is not None else TS(9))

    def agg(rows, **kw):
        return aggregate_attempts(rows, **kw)

    def st(a, cls, mcl, has_mcl=True):
        return a["stations"][_station_key(cls, mcl, has_mcl)]

    # ---- 0. 常數與 route 健全性（接錯＝全部作廢）------------------------------
    t.eq(SCHEMA_ID, "board.rework.v1", "0.1 schema pin")
    t.eq(len(QLIK_STATIONS), 43, "0.2 route 43 站（import 重用、非另抄）")
    t.eq([s[0] for s in QLIK_STATIONS], list(range(1, 44)), "0.3 ord 1..43")
    t.eq(len({s[1] for s in QLIK_STATIONS}), 43, "0.4 cls 互異（cls 單鍵降級無歧義）")
    t.eq(ROW_ARITY, 6, "0.5 列 arity=6（SQL SELECT 欄序 pin）")
    t.eq((MAIN_PREFIX, M0_PREFIX), ("J0", "V0"), "0.6 前綴 pin（＝LOT_PREFIXES 拆兩用途）")
    t.eq(set(LOT_PREFIXES), {MAIN_PREFIX, M0_PREFIX}, "0.7 前綴與 Process01 母體一致")
    t.eq(_station_key("a", "b", True), ("a", "b"), "0.8 站鍵＝(sgc,mcl)")
    t.eq(_station_key("a", "b", False), "a", "0.9 降級站鍵＝sgc 單鍵")

    # ---- 1. SQL builder 形狀（SPEC §8：形狀斷言＋負斷言）-----------------------
    sql_t, prm_t = build_rework_sql(WIN, has_mcl=True)
    sql_f, prm_f = build_rework_sql(WIN, has_mcl=False)
    t.ok(sql_t.rstrip().endswith("OPTION (RECOMPILE)"), "1.1 尾 RECOMPILE（has_mcl=True）")
    t.ok(sql_f.rstrip().endswith("OPTION (RECOMPILE)"), "1.2 尾 RECOMPILE（has_mcl=False）")
    t.ok("DISTINCT" not in sql_t.upper(), "1.3 view 上零 DISTINCT（B4 斷崖鐵則）")
    t.ok("GROUP BY" not in sql_t.upper(), "1.4 view 上零 GROUP BY（聚合全 Python）")
    t.ok("matMaterialLotID IN" not in sql_t, "1.5 無動態 lot IN-list")
    t.ok("JOIN" not in sql_t.upper(), "1.6 單 view、無 join、無 #temp")
    t.eq(len(prm_t), 2, "1.7 參數恰 2 個（窗界；常值清單走 _sql_list 內嵌）")
    t.eq(prm_t, (Y, Y + timedelta(days=1)), "1.8 參數＝半開窗 [Y, Y+1)")
    t.ok("v.stpStartTime >= ?" in sql_t and "v.stpStartTime < ?" in sql_t,
         "1.9 窗＝stpStartTime 半開（>= 與 <）")
    t.ok("mclMaterialClassID IN" in sql_t, "1.10 has_mcl=True 帶 mcl 白名單那道")
    t.ok("mclMaterialClassID" not in sql_f, "1.11 has_mcl=False 全文零引用 mcl 欄（引用即炸）")
    t.ok("N'(absent)'" in sql_f, "1.12 降級以 '(absent)' 佔位保 6 欄 arity")
    # ★ 1.13（2026-08-03 LOW C-6 改）：判定過濾**刻意不下推 SQL**——母體收 NULL 判定列
    #   供自揭（Python 端分桶、不進 attempts）。負斷言防有人「順手加回去」。
    t.ok("stpTestResult IN (" not in sql_t,
         "1.13 判定過濾不在 SQL（NULL 列進母體供自揭；口徑仍由 Python 守）")
    t.ok("stpTestResult IN (" not in sql_f, "1.13b 降級分支同樣不下推判定過濾")
    t.ok("v.stpTestResult" in sql_t, "1.13c 判定欄仍在 SELECT（Python 端分桶要用）")
    t.ok("LEFT(v.matMaterialLotID, 2) IN (N'J0', N'V0')" in sql_t,
         "1.14 Process01 前綴過濾（重用非抄）")
    for c in EXCLUDED_CLASSES:
        t.ok(c in sql_t, f"1.15 排除站 {c} 在 SQL（Process01 第三道）")
    sqls = ALL_SQL()
    t.eq(len(sqls), 2, "1.16 ALL_SQL 登記 2 條（新增查詢必須登記）")
    all_text = "\n".join(sqls)
    t.eq(all_text.count("stpEndTime"), 0, "1.17 ★ stpEndTime 全 SQL 文本零出現（負斷言）")
    t.eq(all_text.count("stpPersonID"), 0, "1.18 ★ stpPersonID 全 SQL 文本零出現（§24 負斷言）")
    t.ok("OperatorId" not in all_text, "1.19 ★ 任何人員識別欄零出現（§24）")
    # 反向窗直接擋（_win 守衛重用）
    try:
        build_rework_sql((WIN[1], WIN[0]))
        t.ok(False, "1.20 反向窗必 raise")
    except ValueError:
        t.ok(True, "1.20 反向窗必 raise（_win 守衛重用）")

    # ---- 2. 重工核心：同 lot 同站 3 列 ⇒ attempts=3/first=1/rework=2 ----------
    a2 = agg([R(1, "J0AAA", CAL, PC, "FAIL", TS(1)),
              R(2, "J0AAA", CAL, PC, "FAIL", TS(3)),
              R(3, "J0AAA", CAL, PC, "PASS", TS(5))])
    t.eq(st(a2, CAL, PC)["attempts"], 3, "2.1 attempts=3")
    t.eq(st(a2, CAL, PC)["first_attempts"], 1, "2.2 first_attempts=1（distinct lot）")
    t.eq(st(a2, CAL, PC)["rework_n"], 2, "2.3 rework_n=2（=Σ per-lot max(0,n−1)）")
    t.eq(st(a2, CAL, PC)["n_fail_rows"], 2, "2.4 n_fail_rows=2（raw 參考、非報廢口徑）")
    t.eq(a2["diag"]["n_rows"], 3, "2.5 n_rows=3")
    t.eq(a2["diag"]["degraded"], False, "2.6 正常路徑不 degraded")

    # ---- 3. 同 lot 跨站不算重工 ------------------------------------------------
    a3 = agg([R(1, "J0BBB", DA, PH), R(2, "J0BBB", WB, PH), R(3, "J0BBB", CAL, PC)])
    for cls, mcl in ((DA, PH), (WB, PH), (CAL, PC)):
        t.eq(st(a3, cls, mcl)["attempts"], 1, f"3.x {cls} attempts=1")
        t.eq(st(a3, cls, mcl)["rework_n"], 0, f"3.y {cls} 跨站不算重工")
    t.eq(sum(v["attempts"] for v in a3["stations"].values()), 3, "3.4 三站各一次、帳平")

    # ---- 4. 同 sgc 不同 mcl 分開計（站鍵含 mcl；43 站 join 維度對齊）-----------
    # 43 站 cls 互異 ⇒ 「同 sgc 錯 mcl」＝off-route，不得混進正確站列
    a4 = agg([R(1, "J0CCC", DA, PH),          # 正確站鍵 (DA, PH)＝ord10
              R(2, "J0CCC", DA, PC),          # 同 sgc、錯 mcl ⇒ off-route
              R(3, "J0CCC", DA, PC)])
    t.eq(st(a4, DA, PH)["attempts"], 1, "4.1 正確 (sgc,mcl) 進帳 1 列")
    t.eq(st(a4, DA, PH)["rework_n"], 0, "4.2 錯 mcl 列不混入 ⇒ 無假重工")
    t.eq(a4["diag"]["n_offroute_attempts"], 2, "4.3 錯 mcl 2 列落 off-route 自揭")

    # ---- 5. M0 列入 diag 不入帳 ------------------------------------------------
    a5 = agg([R(1, "V0XXX", DA, PH), R(2, "V0XXX", DA, PH), R(3, "J0DDD", DA, PH)])
    t.eq(a5["diag"]["n_m0_attempts"], 2, "5.1 M0 列計 n_m0_attempts")
    t.eq(st(a5, DA, PH)["attempts"], 1, "5.2 帳內只有 B 列")
    t.eq(st(a5, DA, PH)["rework_n"], 0, "5.3 M0 重複不產生假重工")
    # 優先序：M0 且 off-route ⇒ 記 M0（②先於④、先中先出）
    a5b = agg([R(1, "V0YYY", SHIP, PH)])
    t.eq((a5b["diag"]["n_m0_attempts"], a5b["diag"]["n_offroute_attempts"]), (1, 0),
         "5.4 優先序：M0 先於 off-route")

    # ---- 6. off-route 列入 diag（隱形站/出貨段）--------------------------------
    a6 = agg([R(1, "J0EEE", FILM, PH), R(2, "J0FFF", SHIP, PH)])
    t.eq(a6["diag"]["n_offroute_attempts"], 2, "6.1 白名單外 (sgc,mcl) 全落 off-route")
    t.eq(sum(v["attempts"] for v in a6["stations"].values()), 0, "6.2 不進 43 列帳")

    # ---- 7. 壞列（None lot/sgc）計 n_bad_rows 不炸 -----------------------------
    a7 = agg([R(1, None, DA, PH), R(2, "J0GGG", None, PH), ("short",),
              R(3, "X9ZZZ", DA, PH),                    # 非 B0 非 M0（結構上不可達；防呆）
              R(4, "J0GGG", DA, PH)])
    t.eq(a7["diag"]["n_bad_rows"], 4, "7.1 None lot／None sgc／短列／怪前綴 全計 bad")
    t.eq(st(a7, DA, PH)["attempts"], 1, "7.2 好列照常進帳")
    t.eq(a7["diag"]["n_rows"], 5, "7.3 n_rows＝輸入全列數（含壞列）")

    # ---- 8. 空輸入 ⇒ 43 站鍵全在、全 0 ----------------------------------------
    a8 = agg([])
    t.eq(len(a8["stations"]), 43, "8.1 空輸入 43 站鍵全在（站鍵不缺＝「沒資料」不藏站）")
    t.ok(all(v == {"attempts": 0, "first_attempts": 0, "rework_n": 0, "n_fail_rows": 0}
             for v in a8["stations"].values()), "8.2 全 0（不是 None——計數語意、非量測）")
    t.ok(all(_station_key(c, m) in a8["stations"] for _o, c, m in QLIK_STATIONS),
         "8.3 鍵＝(cls,mcl) 與 43 站逐一對齊")
    t.eq(a8["diag"]["n_rows"], 0, "8.4 n_rows=0")

    # ---- 9. mcl ABSENT ⇒ degraded＋cls 單鍵退化 -------------------------------
    a9 = agg([( 1, "J0HHH", DA, "(absent)", "PASS", TS(2)),
              (2, "J0HHH", DA, "(absent)", "FAIL", TS(4))], has_mcl=False)
    t.eq(a9["diag"]["degraded"], True, "9.1 degraded=True 自揭")
    t.eq(a9["diag"]["mcl_filter"], "SKIPPED(mcl ABSENT)", "9.2 mcl_filter 具名 SKIPPED")
    t.ok(len(a9["diag"]["caveats"]) == 1 and "cls only" in a9["diag"]["caveats"][0],
         "9.3 caveat 講明維度損失")
    t.eq(len(a9["stations"]), 43, "9.4 降級仍 43 鍵全在")
    t.eq(a9["stations"][DA]["attempts"], 2, "9.5 cls 單鍵進帳")
    t.eq(a9["stations"][DA]["rework_n"], 1, "9.6 降級路徑重工照算")
    t.ok(DA in a9["stations"] and (DA, PH) not in a9["stations"],
         "9.7 降級鍵形＝cls（不混 (cls,mcl)）")

    # ---- 10. compute_rework 端到端（FakeCur；零網路）---------------------------
    rows10 = [R(1, "J0AAA", CAL, PC, "FAIL", TS(1)),
              R(2, "J0AAA", CAL, PC, "PASS", TS(3)),
              R(2, "J0AAA", CAL, PC, "PASS", TS(3)),     # 完全重複列（view dup）
              R(3, "V0XXX", DA, PH, "PASS", TS(5)),
              R(4, "J0EEE", FILM, PH, "FAIL", TS(6))]
    cur10 = _FakeCur(_guard_row(mcl=1), rows10)
    r10 = compute_rework(cur10, WIN)
    t.eq(r10["schema"], SCHEMA_ID, "10.1 canonical schema")
    t.eq(sorted(r10.keys()), sorted(CANONICAL_KEYS), "10.2 canonical 鍵全集")
    t.eq(r10["win"], [Y.isoformat(), (Y + timedelta(days=1)).isoformat()],
         "10.3 win iso 半開窗")
    t.eq(r10["stations"][(CAL, PC)]["attempts"], 2, "10.4 dup 去重後 attempts=2（不虛胖）")
    t.eq(r10["stations"][(CAL, PC)]["rework_n"], 1, "10.5 rework=1")
    t.eq(r10["diag"]["n_dup_rows_removed"], 1, "10.6 去重自揭")
    t.eq(r10["diag"]["n_raw_rows"], 5, "10.7 raw 列數自揭")
    t.eq(r10["diag"]["n_m0_attempts"], 1, "10.8 M0 自揭")
    t.eq(r10["diag"]["n_offroute_attempts"], 1, "10.9 off-route 自揭")
    t.ok(all(k in r10["diag"] for k in DIAG_PIN_KEYS), "10.10 diag pin 鍵全在")
    t.ok(all(k in r10["diag"]["timing"] for k in ("guard_ms", "fetch_ms", "agg_ms")),
         "10.11 timing 三段全在")
    t.ok(r10["elapsed_s"] is not None, "10.12 elapsed_s 有值")
    sent = "\n".join(s for s, _p in cur10.sql_log)
    t.ok("stpEndTime" not in sent, "10.13 ★ 端到端送出的 SQL 零 stpEndTime")
    t.ok("stpPersonID" not in sent, "10.14 ★ 端到端送出的 SQL 零 stpPersonID（§24）")

    # ---- 11. compute：mcl ABSENT 降級端到端 -----------------------------------
    cur11 = _FakeCur(_guard_row(mcl=0),
                     [(1, "J0III", DA, "(absent)", "PASS", TS(1)),
                      (2, "J0III", DA, "(absent)", "PASS", TS(2))])
    r11 = compute_rework(cur11, WIN)
    t.eq(r11["diag"]["degraded"], True, "11.1 端到端 degraded")
    t.eq(r11["stations"][DA]["rework_n"], 1, "11.2 cls 單鍵重工照算")
    sent11 = "\n".join(s for s, _p in cur11.sql_log if s.strip().startswith("SELECT v.stpPK"))
    t.ok("mclMaterialClassID" not in sent11, "11.3 降級 SQL 零引用 mcl 欄（引用即炸）")

    # ---- 12. compute：承重欄 ABSENT ⇒ SchemaDivergence（絕不跑全零帳）---------
    bad = list(_guard_row())
    bad[GUARD_KEYS.index("view_seg")] = 0                 # view ABSENT
    try:
        compute_rework(_FakeCur(tuple(bad), []), WIN)
        t.ok(False, "12.1 view ABSENT 必 raise")
    except SchemaDivergence:
        t.ok(True, "12.1 view ABSENT ⇒ SchemaDivergence（消費端接 unavailable）")
    g12 = list(_guard_row())
    g12[GUARD_KEYS.index("stpPK")] = 0                    # 承重欄 stpPK ABSENT
    try:
        compute_rework(_FakeCur(tuple(g12), []), WIN)
        t.ok(False, "12.2 承重欄 ABSENT 必 raise")
    except SchemaDivergence as e:
        t.ok("stpPK" in str(e), "12.2 承重欄 ABSENT ⇒ SchemaDivergence 具名")

    # ---- 13. to_jsonable / render smoke（防 f-string 格式 bug 在 live 才炸）----
    import contextlib
    import io
    import json
    j13 = to_jsonable(r10)
    t.eq(len(j13["stations"]), 43, "13.1 jsonable 43 列、ord 序")
    t.eq([s["ord"] for s in j13["stations"]], list(range(1, 44)), "13.2 ord 升冪")
    t.eq(j13["stations"][31]["attempts"], 2, "13.3 ord32=CAL attempts=2")
    t.ok(json.dumps(j13, ensure_ascii=False, default=str), "13.4 json.dumps 可序列化")
    j13d = to_jsonable(r11)
    t.eq(len(j13d["stations"]), 43, "13.5 降級路徑 jsonable 仍 43 列")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        render(r10)          # 正常盤
        render(r11)          # degraded 盤
    out = buf.getvalue()
    t.ok("board.rework.v1" in out, "13.6 render 不炸、標頭有 schema")
    t.ok("DEGRADED" in out, "13.7 degraded 大聲印出")
    t.ok("王小明" not in out and "EMP00" not in out and "stpPersonID" not in out,
         "13.8 ★ render 零人名零人員鍵（§24）")

    # ---- 14. §24 全鏈負斷言：含人名鍵的（假想）輸入 ⇒ 輸出零人名 ---------------
    #     防未來有人把 SELECT 加寬——多餘欄（含人名）進 tuple 尾也絕不進輸出。
    poisoned = [(1, "J0JJJ", DA, PH, "PASS", TS(1), "王小明", "EMP0042")]
    a14 = agg(poisoned)
    blob = json.dumps(to_jsonable({"schema": SCHEMA_ID, "ok": True, "caliber": CALIBER_REWORK,
                                   "win": ["x", "y"], "stations": a14["stations"],
                                   "diag": a14["diag"], "elapsed_s": 0.0}),
                      ensure_ascii=False, default=str)
    t.eq(st(a14, DA, PH)["attempts"], 1, "14.1 多餘尾欄不影響計數")
    t.ok("王小明" not in blob and "EMP0042" not in blob, "14.2 ★ 輸出全文零人名（§24 全鏈）")

    # ---- 15. NULL 判定列自揭（2026-08-03 LOW C-6）------------------------------
    #  母體改由 SQL 收進來（不再下推 stpTestResult 過濾）⇒ Python 端分桶、**不進 attempts**。
    #  ★ 承重：合規輸入（全 PASS/FAIL）下所有既有計數**逐位不變**（15.1 凍結對照組）。
    compliant = [R(1, "J0AAA", CAL, PC, "FAIL", TS(1)),
                 R(2, "J0AAA", CAL, PC, "PASS", TS(3)),
                 R(3, "J0BBB", CAL, PC, "PASS", TS(4)),
                 R(4, "V0XXX", DA, PH, "PASS", TS(5)),
                 R(5, "J0EEE", FILM, PH, "FAIL", TS(6))]
    a15 = agg(compliant)
    t.eq({k: dict(v) for k, v in a15["stations"].items() if v["attempts"]},
         {(CAL, PC): {"attempts": 3, "first_attempts": 2, "rework_n": 1, "n_fail_rows": 1}},
         "15.1 ★ 合規輸入（全 PASS/FAIL）帳面凍結對照＝改動前後逐位相同")
    t.eq((a15["diag"]["n_m0_attempts"], a15["diag"]["n_offroute_attempts"],
          a15["diag"]["n_bad_rows"], a15["diag"]["n_null_result_rows"]), (1, 1, 0, 0),
         "15.2 合規輸入 n_null_result_rows=0、其餘桶不變")
    # NULL 判定列：進母體、記自揭、不進 attempts、不製造假重工
    a15b = agg(compliant + [R(6, "J0AAA", CAL, PC, None, TS(7)),
                            R(7, "J0AAA", CAL, PC, "UNKNOWN", TS(8))])
    t.eq(a15b["diag"]["n_null_result_rows"], 2, "15.3 NULL 與非 PASS/FAIL 值皆計 null 桶")
    t.eq(st(a15b, CAL, PC)["attempts"], 3, "15.4 NULL 列不進 attempts（口徑不變）")
    t.eq(st(a15b, CAL, PC)["rework_n"], 1, "15.5 NULL 列不製造假重工")
    t.eq(a15b["diag"]["n_rows"], 7, "15.6 n_rows 含 NULL 列（＝母體列數）")
    # 優先序：② NULL 先於 ③M0／⑤off-route（NULL 率要是全母體訊號）
    t.eq((agg([R(1, "V0ZZZ", DA, PH, None, TS(1))])["diag"]["n_null_result_rows"],
          agg([R(1, "V0ZZZ", DA, PH, None, TS(1))])["diag"]["n_m0_attempts"]), (1, 0),
         "15.7 優先序：NULL 先於 M0")
    t.eq(agg([R(1, "J0QQQ", SHIP, PH, None, TS(1))])["diag"]["n_offroute_attempts"], 0,
         "15.8 優先序：NULL 先於 off-route")
    t.eq(agg([R(1, None, DA, PH, None, TS(1))])["diag"]["n_bad_rows"], 1,
         "15.9 優先序：壞列仍先於 NULL（①在②之前）")
    # 端到端：FakeCur 帶 NULL 列 ⇒ diag 自揭、帳不變
    cur15 = _FakeCur(_guard_row(mcl=1),
                     [R(1, "J0AAA", CAL, PC, "PASS", TS(1)),
                      R(2, "J0AAA", CAL, PC, None, TS(2))])
    r15 = compute_rework(cur15, WIN)
    t.eq(r15["diag"]["n_null_result_rows"], 1, "15.10 端到端 NULL 自揭")
    t.eq(r15["stations"][(CAL, PC)]["attempts"], 1, "15.11 端到端 attempts 不含 NULL 列")
    t.ok("n_null_result_rows" in DIAG_PIN_KEYS, "15.12 新桶進 diag pin（消費端可讀）")

    print(f"\n  斷言 {t.n} 條、失敗 {len(t.bad)} 條")
    for b in t.bad:
        print(f"    FAIL  {b}")
    print(f"\n  → {'PASS ✅' if not t.bad else 'FAIL ❌'}")
    if t.bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
