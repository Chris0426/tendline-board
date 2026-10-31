#!/usr/bin/env python3
"""Tendline Board — 單日 per-EID 產品 family 活動計數（`board.family_day.v1`）。

**這是什麼**：日帳 v4 站列 `families` 欄的**資料腿**（M3 C2；`SPEC-PRODUCT-TAGS.md` §2.6
「實心＝近期在跑」活動層的唯一 MES 路徑）。回答一件事——
**「這一天、這一站，每台機跑的段分別屬於哪個產品 family、各幾段？」**

**不是什麼**：
  · **不是產品能力標籤**——標籤＝730 天普查的靜態檔（`board_tags.py`，Chris 域內手跑、
    md5 釘死）＝**歷史總帳**，沒有「最近」的概念。本檔＝**活動層**：能力層說「會跑」，
    本檔說「這幾天真的在跑」。兩層**永不互相頂替**（C-PT3：把「沒量到」讀成「這台不行」
    是這一整套設計最貴的錯）。
  · 不是良率（`mes_yield`）、不是稼動（`mes_timeline`）、不是重工（`mes_rework`）、
    不是 WIP（`mes_wip`）。

語意權威（分歧一律以 SPEC 為準、不以本檔註解為準）：
  · `SPEC-PRODUCT-TAGS.md` §1（family canonical 三個值、不翻譯）／§2.1（UNKNOWN 永不成為
    標籤）／§2.6（活動層提案與三個前提）。
  · `SPEC-MONTHLY.md` §3.4（日帳 v4 形；`families` 欄的 doc 級狀態鍵＝`families_status`）。

★ 承重口徑（違反即錯）
  · **family 判定＝`process_segment_v.SegmentProductLineCode`**，**不是 lot 前綴**。
    lot 前綴 `B0/M0` 是 `mes_rework`／`mes_qlik` 的「產品 lot vs 非產品」過濾，與 family
    無關（M3 計畫 §0 發現 3）。唯一出處＝`_probe_product_tags_730d_v2.sql:5531-5558` 的
    CASE（2026-09-02 Chris 域內實跑）；本檔 `normalize_family()` **逐字對齊那個 CASE**。
  · **`UNKNOWN` 永不進輸出**（`{}` 而不是 `{"UNKNOWN": n}`）——它不是 family，是
    「NULL／substrate／非產品段」。自揭走 `stats["n_unknown_pairs"]`。
  · **`None ≠ {}`**：本檔的 `{}`＝「查過了，這站這天零 family 段」；「沒查」由呼叫端
    （`board_data.fetch_day_families_live` 回 `None` ＋ doc 級 `families_status`）表達。
    把「沒查到」寫成 `{}` ＝把「不知道」畫成「閒著」＝ C-PT3 要防的那件事。
  · **形④ 別名折疊**：`mes_timeline.fold_eid`（`EID_RENAME`／`EID_ALIAS_DROP`）**import
    同一個物件、絕不另抄表**。`EID-38041` → `EID-38106`；`EID-38072` 的列**整列丟掉**
    （其段⊂主 EID，留著＝雙算，`SPEC-PRODUCT-TAGS.md` §7 A14／`SPEC-RUNWINDOW.md` §10.8-8）。

★ 查詢形狀＝**已在生產跑的那一種**（QG-1 §2「探針忠實重現 app 真正送出的那條 query」）
  Y1／Y2 兩條**單源**查詢 ＋ **Python 端 `stpPK` join**——逐字同
  `mes_yield.fetch_machine_yield()`（`mes_yield.py:246-265` Y1／`:292-302` Y2 的 `win` 分支，
  凍結、生產中）與普查 Plan B（`_probe_product_tags_730d_v2.sql:5503-5518`）。
  **絕不**把 `process_segment_v` 與 `segment_equipment_v` 直接 join（B2 事故）。

  perf 鐵則（B2/B4/M4 的血；改本檔前先讀 `GOVERNANCE.md` QG-2）
  · 逐站點查（`SegmentClassID = ?` 單一參數）——**永不**跨站 IN-list／UNION。
  · **零 `astEquipmentID IN (...)`**（B2：equipment 側 IN-list 會翻計劃，7d 4s → 14d 90s+）。
  · 對大 view **零 `DISTINCT`／零 `GROUP BY`**（B4 斷崖）——聚合全在 Python。
  · 兩條都 `OPTION (RECOMPILE)` 收尾。
  · **Y1 帶 `stpEndTime IS NOT NULL AND stpEndTime >= stpStartTime`**（B4 d=30 斷崖修）；
    **`>=` 不是 `>`**（M4：嚴格大於刪掉合法零工期段、八站整站隱形＝[[mes-zero-length-segments]]）。
  · **Y2 刻意不濾 `stpEndTime`**——逐字同 `mes_yield` Y2 與普查 Y2（該檔頭明寫）；
    未配對的 `stpPK` 在 Python join 自然丟棄，與生產行為一致。

★ 欄位出處（[[ssms-sql-column-provenance]]：每個表名／欄名都要追得到跑過的查詢）
  | 欄 | 來源 view | 已跑過的查詢 | schema inventory（`..\\MES knowledge base\\workspace\\08_schema_query_hardening\\artifacts\\table_column_inventory.csv`） |
  |---|---|---|---|
  | `stpPK`                    | `dbo.segment_equipment_v` | `mes_yield.py:248`／probe `:5504` | `stpPK` 列（bigint） |
  | `astEquipmentID`           | `dbo.segment_equipment_v` | `mes_yield.py:248`／probe `:5504` | `:5125`（varchar 75） |
  | `SegmentClassID` | `dbo.segment_equipment_v` | `mes_yield.py:260`／probe `:5506` | `:5120`（varchar 75） |
  | `stpStartTime`             | `dbo.segment_equipment_v` | `mes_yield.py:261`／probe `:5507` | `:5082`（datetime2） |
  | `stpEndTime`               | `dbo.segment_equipment_v` | `mes_yield.py:263`／probe `:5509` | `:5083`（datetime2, nullable） |
  | `stpPK`                    | `dbo.process_segment_v`   | `mes_yield.py:294`／probe `:5496` | `:4367`（bigint） |
  | `SegmentProductLineCode`   | `dbo.process_segment_v`   | probe `:5496`（Y2 SELECT）＋ CASE `:5535-5539` | `:4455`（varchar 75, nullable） |
  | `SegmentClassID` | `dbo.process_segment_v`   | `mes_yield.py:298`／probe `:5498` | `:4388`（varchar 75） |
  | `stpStartTime`             | `dbo.process_segment_v`   | `mes_yield.py:299`／probe `:5499` | `:4372`（datetime2） |
  ⚠ **Plan A 已實證死亡**：`se.SegmentProductLineCode`（直取 `segment_equipment_v`）
    ＝`Msg 207 Invalid column name`（2026-08-29 Chris 域內實跑，probe 檔頭存證）。
    這是**經驗事實不是假設**——不要再試。**沒有別的欄名可用。**

★ §24 人名紅線：本模組不取、不回、不記任何姓名/工號欄位；`stpPersonID` 於全檔 SQL 文本
  **零出現**（selftest 負斷言釘死＝`ALL_SQL()`）。
★ 唯讀：對 view 只有 SELECT；零 DDL/DML、零 `#temp`。
★ 凍結模組 `mes_timeline.py`／`mes_yield.py`：**只 import 常數/純函式、不執行、零 diff**。
★ **`run()` 自己絕不開連線**——cursor 一律由呼叫端傳入（`board_data` 一條連線跑完全部
  站；`--probe` 自己開）。理由：43 站各開一條連線＝43 次 Kerberos 握手。

CLI:
    python mes_family.py --selftest                     # 純離線合成假列、零 MES、零網路
    python mes_family.py --probe --day 2026-09-03       # ⚠ **只由 Chris 域內跑**（QG-2 計時）
    python mes_family.py --probe --day 2026-09-03 --wide-days 7
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta

# ── 連線／窗守衛單一出處（同 `mes_rework` 慣例：不抄第二份 `_connect`／`_win`）────────
from mes_qlik import _connect, _dt, _win            # noqa: F401
# ── 形④ 別名折疊唯一出處（純表＋純函式；import 無副作用、不連 MES）──────────────────
from mes_timeline import (                          # noqa: F401
    EID_ALIAS_DROP, EID_RENAME, EQP_STATIONS, fold_eid,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# 1. 人工審定常數
# =============================================================================
SCHEMA_ID = "board.family_day.v1"
CALIBER_FAMILY = ("per-EID product-family segment count in window "
                  "(SegmentProductLineCode normalized per census CASE; "
                  "UNKNOWN never a family; alias-folded EIDs)")

# canonical family 三值＋順序（`SPEC-PRODUCT-TAGS.md` §1；**不翻譯**）。
# ⚠ 值與順序必須與 `board_tags.FAMILIES` 一致——selftest 有 cross-pin（該檔是 GENERATED、
#   本檔在 MES 層，刻意不 import 它：資料層不該依賴呈現層的產生物）。
FAMILIES = ("Plover", "Kestrel", "Heron")
UNKNOWN = "UNKNOWN"

# `normalize_family` 的對照表＝普查 CASE 的三個 WHEN（`_probe_product_tags_730d_v2.sql:5535-5537`）。
_FAMILY_BY_CODE = {"plover": "Plover", "kestrel": "Kestrel", "heron": "Heron"}

QUERY_TIMEOUT_SEC = 180          # 單站單日窗＝秒級；寬裕上界防呆（呼叫端另有預算）

# 兩條查詢的列形（pin；純函式層全部吃這個形狀＝SQL SELECT 欄序）
I_Y1_PK, I_Y1_EID = 0, 1
I_Y2_PK, I_Y2_FAM = 0, 1
Y1_ARITY = 2
Y2_ARITY = 2

# `cls` 防呆字元集：站別 class id 都是識別字（`mes_timeline.EQP_STATIONS`）。
# cls 本身是**參數**（`?`）、不進 SQL 文本 ⇒ 這道只為了早點抓到呼叫端傳錯型別/垃圾值。
_CLS_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


# =============================================================================
# 2. I/O 層（SQL builder 純函式＝離線可斷言形狀；唯讀＝兩條單源 SELECT）
# =============================================================================
def build_family_sql(cls, win_start, win_end):
    """單站單窗的 Y1／Y2 兩條 SELECT。回 `((y1_sql, params), (y2_sql, params))`。

    形狀逐字同 `mes_yield.fetch_machine_yield()` 的 `win` 分支（`mes_yield.py:257-265`
    Y1／`:293-302` Y2），只換 SELECT 欄：Y2 取 `SegmentProductLineCode`（普查 Y2
    `_probe_product_tags_730d_v2.sql:5495-5501`）而不是良率三欄。

    · 參數恰 3 個、兩條相同：`(cls, ws, we)`；窗＝半開 `[ws, we)`（`stpStartTime`）。
    · **Y1 帶 end 過濾（`IS NOT NULL` ＋ `>=`）**；**Y2 刻意不帶**（見模組 docstring）。
    · 兩條各自 `OPTION (RECOMPILE)` 收尾；view 上零 DISTINCT／GROUP BY／JOIN／IN-list。
    """
    if not isinstance(cls, str) or not cls or not set(cls) <= _CLS_SAFE:
        raise ValueError(f"cls must be an identifier-like station class id: {cls!r}")
    ws, we = _win((win_start, win_end))
    y1 = ("SELECT se.stpPK, se.astEquipmentID\n"
          "FROM dbo.segment_equipment_v se\n"
          "WHERE se.SegmentClassID = ?\n"
          "  AND se.stpStartTime >= ?\n"
          "  AND se.stpStartTime < ?\n"
          "  AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime\n"
          "OPTION (RECOMPILE)")
    y2 = ("SELECT s.stpPK, s.SegmentProductLineCode\n"
          "FROM dbo.process_segment_v s\n"
          "WHERE s.SegmentClassID = ?\n"
          "  AND s.stpStartTime >= ?\n"
          "  AND s.stpStartTime < ?\n"
          "OPTION (RECOMPILE)")
    params = (cls, ws, we)
    return (y1, params), (y2, params)


def ALL_SQL():
    """本模組會送出的**全部 SQL 文本**（selftest 形狀/負斷言的唯一清單）。

    新增任何查詢**必須**登記在這裡——否則 `stpPersonID` 零出現、`DISTINCT`／`GROUP BY`
    零下推、`astEquipmentID IN` 零出現的負斷言會有漏網之魚（`mes_rework.ALL_SQL` 同款制度）。
    """
    win = (datetime(2026, 1, 1), datetime(2026, 1, 2))
    y1, y2 = build_family_sql("segCalibrationTest", win[0], win[1])
    return (y1[0], y2[0])


def open_connection(timeout=QUERY_TIMEOUT_SEC):
    """開一條唯讀連線（`mes_qlik._connect` 重用）。回 `(conn, cur)`。

    ⚠ **`run()` 不呼叫本函式**——一條連線跑多站是呼叫端的責任（`board_data`
    `fetch_day_families_live` 開一條、跑完全部站再關；43 站各開一條＝43 次握手）。
    本函式只給 `--probe` CLI 與那個呼叫端用。
    """
    return _connect(timeout=timeout)


# =============================================================================
# 3. 純函式層（selftest 主戰場；全部離線可測）
# =============================================================================
def normalize_family(code):
    """`SegmentProductLineCode` → `Plover`／`Kestrel`／`Heron`／`UNKNOWN`（純函式）。

    **逐字對齊普查 CASE**（`_probe_product_tags_730d_v2.sql:5534-5540`）::

        WHEN LOWER(ISNULL(family_raw, '')) = 'plover'  THEN 'Plover'
        WHEN LOWER(ISNULL(family_raw, '')) = 'kestrel' THEN 'Kestrel'
        WHEN LOWER(ISNULL(family_raw, '')) = 'heron'   THEN 'Heron'
        WHEN family_raw IS NULL THEN 'UNKNOWN (NULL -- substrate/non-product segment)'
        ELSE 'UNKNOWN (unrecognized code: ' + family_raw + ')'

    · **NULL 與「不認識的碼」都回單一 `UNKNOWN`**——普查把兩者印成兩種字串是為了人工
      triage（那是一次性報表）；本檔是每日活動計數，`UNKNOWN` 一律不進輸出，兩者的差別
      在消費面零意義。要 triage 就去看普查。
    · `rstrip(" ")`＝重現 SQL Server 的**尾空白補齊比較**（`'plover  ' = 'plover'` 為真）；
      **前導空白不 strip**（SQL 端也不會相等）⇒ 回 `UNKNOWN`。
    """
    if code is None:
        return UNKNOWN
    return _FAMILY_BY_CODE.get(str(code).lower().rstrip(" "), UNKNOWN)


def aggregate_families(rows_y1, rows_y2, stats=None):
    """Y1（stpPK→EID）＋ Y2（stpPK→family 原碼）→ `{eid: {family: n}}`（純函式、零 I/O）。

    · **join 在 Python 端以 `stpPK` 做**（絕不在 SQL join 兩個大 view）；Y1 有、Y2 沒有的
      `stpPK` 自然丟棄（`n_unmatched_pairs` 自揭）——與 `mes_yield._agg_machine_yield`
      和普查 Stage 2 的 `INNER JOIN` 行為一致。
    · **一段掛多台機（fan-out）＝每台各計一次**（設備視角、`§14.4` NO 去重）；普查
      `GROUP BY eid` 同義。
    · **同一個 `(stpPK, eid)` 對只計一次**（`n_dup_pairs` 自揭）——view 若吐出完全重複的
      列，計兩次＝憑空多出一段活動（同 `mes_rework._dedup_rows` 的防護；普查那條
      `INNER JOIN` 沒有這道，但它是一次性人工報表、本檔是每天自動落盤的帳）。
    · **`UNKNOWN` 不寫進輸出**（`n_unknown_pairs` 自揭）；某 eid 全部段都是 UNKNOWN ⇒
      **該 eid 整個不出現**（消費端用「站有沒有被查過」判 unknown，不靠空 dict）。
    · 形④ 別名折疊在**進帳前**做：`EID-38072` 整列丟（`n_alias_dropped`）、
      `EID-38041` 改名成 `EID-38106`；折疊後同一 `stpPK` 的兩列會撞上 `(pk, eid)` 去重
      ⇒ **結構性不可能雙算**。
    · 輸出順序確定性：eid 升冪、family 依 `FAMILIES`（日帳落盤要逐位元可重現）。
    """
    fam_by_pk = {}
    n_bad_y2 = n_conflict = 0
    for r in rows_y2:
        r = tuple(r)
        if len(r) < Y2_ARITY or r[I_Y2_PK] is None:
            n_bad_y2 += 1
            continue
        pk = r[I_Y2_PK]
        f = normalize_family(r[I_Y2_FAM])
        prev = fam_by_pk.get(pk)
        if prev is None:
            fam_by_pk[pk] = f
        elif prev != f:
            n_conflict += 1                 # 先中先出（不讓後到的列改寫已定的 family）

    acc = {}
    seen = set()
    n_bad_y1 = n_dup = n_unmatched = n_unknown = n_alias = 0
    for r in rows_y1:
        r = tuple(r)
        if len(r) < Y1_ARITY:
            n_bad_y1 += 1
            continue
        pk, raw_eid = r[I_Y1_PK], r[I_Y1_EID]
        if pk is None or raw_eid is None:
            n_bad_y1 += 1
            continue
        eid = fold_eid(raw_eid)
        if eid is None:                      # `EID_ALIAS_DROP`：其段⊂主 EID ⇒ 整列丟
            n_alias += 1
            continue
        key = (pk, eid)
        if key in seen:
            n_dup += 1
            continue
        seen.add(key)
        f = fam_by_pk.get(pk)
        if f is None:
            n_unmatched += 1
            continue
        if f == UNKNOWN:
            n_unknown += 1
            continue
        d = acc.setdefault(eid, {})
        d[f] = d.get(f, 0) + 1

    out = {eid: {f: acc[eid][f] for f in FAMILIES if f in acc[eid]}
           for eid in sorted(acc)}
    if stats is not None:
        stats.update({
            "n_rows_y1": len(rows_y1), "n_rows_y2": len(rows_y2),
            "n_pk_y2": len(fam_by_pk), "n_pairs": len(seen),
            "n_bad_rows_y1": n_bad_y1, "n_bad_rows_y2": n_bad_y2,
            "n_dup_pairs": n_dup, "n_unmatched_pairs": n_unmatched,
            "n_unknown_pairs": n_unknown, "n_alias_dropped": n_alias,
            "n_pk_family_conflicts": n_conflict,
            "n_eids": len(out),
            "n_segments": sum(sum(v.values()) for v in out.values()),
        })
    return out


# =============================================================================
# 4. I/O 編排（cursor 由呼叫端傳入；本層絕不開連線）
# =============================================================================
def run(cls, win, cur, stats=None):
    """一站一窗：Y1 → （有列才）Y2 → `aggregate_families`。回 `{eid: {family: n}}`。

    · `cur`＝**呼叫端的** cursor（`open_connection` 的第二個回傳值）——本函式**不開、
      不關連線**。
    · **Y1 零列 ⇒ 不送 Y2**（`y2_skipped=True` 自揭）：結果一樣是 `{}`，但省掉一條
      大 view 掃描（43 站×1 日裡多數站是零列或小量）。
    · 失敗一律往上拋（呼叫端接成 `None` ＋ doc 級 `families_status`）——**絕不回 `{}`**：
      `{}` 的意思是「查過了、零 family 段」，拿它頂替失敗＝把「不知道」畫成「閒著」。
    · `stats`＝呼叫端可傳入的 dict，本函式把逐段計時與 `aggregate_families` 的自揭欄
      寫進去（additive、不影響回傳形）。
    """
    ws, we = _win(win)
    (y1_sql, y1_params), (y2_sql, y2_params) = build_family_sql(cls, ws, we)
    timing = {}

    t0 = time.perf_counter()
    cur.execute(y1_sql, *y1_params)
    rows_y1 = [tuple(r) for r in cur.fetchall()]
    timing["y1_ms"] = int((time.perf_counter() - t0) * 1000)

    rows_y2 = []
    y2_skipped = not rows_y1
    if not y2_skipped:
        t0 = time.perf_counter()
        cur.execute(y2_sql, *y2_params)
        rows_y2 = [tuple(r) for r in cur.fetchall()]
        timing["y2_ms"] = int((time.perf_counter() - t0) * 1000)
    else:
        timing["y2_ms"] = 0

    t0 = time.perf_counter()
    local = {}
    out = aggregate_families(rows_y1, rows_y2, stats=local)
    timing["agg_ms"] = int((time.perf_counter() - t0) * 1000)
    timing["total_ms"] = timing["y1_ms"] + timing["y2_ms"] + timing["agg_ms"]
    if stats is not None:
        stats.update(local)
        stats.update({"cls": cls, "y2_skipped": y2_skipped, "timing": timing,
                      "win": [ws.isoformat(), we.isoformat()], "schema": SCHEMA_ID})
    return out


def day_window(day=None):
    """`day`（str/date/datetime/None）→ 單日半開窗 `[day 00:00, day+1 00:00)`（台北 naive）。

    未給＝**昨天**（同 `mes_rework.run` 的 /daily 窗語意）。
    """
    if day is None:
        d0 = date.today() - timedelta(days=1)
    elif isinstance(day, datetime):
        d0 = day.date()
    elif isinstance(day, date):
        d0 = day
    else:
        d0 = _dt(str(day)).date()
    ws = datetime(d0.year, d0.month, d0.day)
    return (ws, ws + timedelta(days=1))


# =============================================================================
# 5. `--probe`：QG-2 計時（⚠ **只由 Chris 域內跑**；agent 只跑 --selftest）
# =============================================================================
def probe(day=None, wide_days=7, stations=None, timeout=QUERY_TIMEOUT_SEC):
    """三段計時（QG-2「至少兩個窗寬」）：1 站×1 日 → 全站×1 日 → 全站×N 日。

    印每站毫秒與合計；不落盤、不改任何檔。回 `dict`（供 `--json`）。
    """
    stations = list(stations or EQP_STATIONS)
    ws, we = day_window(day)
    wide_ws = ws - timedelta(days=max(0, int(wide_days) - 1))
    conn, cur = open_connection(timeout=timeout)
    out = {"schema": SCHEMA_ID, "caliber": CALIBER_FAMILY,
           "day": ws.strftime("%Y-%m-%d"), "n_stations": len(stations),
           "wide_days": int(wide_days), "phases": {}}
    try:
        for name, sts, win in (("a_1station_1day", stations[:1], (ws, we)),
                               ("b_allstations_1day", stations, (ws, we)),
                               (f"c_allstations_{int(wide_days)}day", stations,
                                (wide_ws, we))):
            t_all = time.perf_counter()
            per = []
            n_eids = n_segs = 0
            for cls in sts:
                st = {}
                fams = run(cls, win, cur, stats=st)
                per.append((cls, st["timing"]["total_ms"], st["n_rows_y1"],
                            st["n_rows_y2"], len(fams)))
                n_eids += len(fams)
                n_segs += st["n_segments"]
            elapsed = round(time.perf_counter() - t_all, 2)
            out["phases"][name] = {
                "win": [win[0].isoformat(), win[1].isoformat()],
                "n_stations": len(sts), "elapsed_s": elapsed,
                "n_eid_rows": n_eids, "n_segments": n_segs,
                "slowest": sorted(per, key=lambda r: -r[1])[:5],
            }
            print(f"\n=== {name}：{len(sts)} 站　窗 {win[0]:%Y-%m-%d %H:%M} .. "
                  f"{win[1]:%Y-%m-%d %H:%M}　合計 {elapsed}s ===")
            print(f"    {'站':<46}{'ms':>8}{'y1':>9}{'y2':>9}{'eids':>6}")
            print("    " + "-" * 78)
            for cls, ms, n1, n2, ne in per:
                print(f"    {cls[:45]:<46}{ms:>8}{n1:>9}{n2:>9}{ne:>6}")
            print(f"    → {len(sts)} 站合計 {elapsed}s／段 {n_segs}／機台列 {n_eids}")
            if name.startswith("b_") and elapsed > 30:
                print("    ⚠ 全站×1 日 >30s ⇒ M3 計畫 §3 C0 的退路：Y2 改單條全站"
                      "（無站過濾）＋Y1 逐站，重量一次再定。")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _hint(e):
    low = str(e).lower()
    if "invalid column" in low:
        return (" → **欄名不存在**。⚠ Plan A（`se.SegmentProductLineCode`）已實證死亡"
                "（2026-08-29 Msg 207）；本檔的 Y2 必須打 `dbo.process_segment_v`。"
                "先對模組 docstring 的欄位出處表校一次，勿改口徑。")
    if any(k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")):
        return " → 連不到 MES。確認已連 VPN/內網。"
    if "login failed" in low or "28000" in str(e):
        return " → 登入被拒。確認 AD 帳號對報表庫有讀權。"
    return ""


def main():
    ap = argparse.ArgumentParser(
        description="Tendline Board 單日 per-EID 產品 family 活動計數（唯讀；兩條單源查詢）")
    ap.add_argument("--probe", action="store_true",
                    help="QG-2 三段計時（⚠ 只由 Chris 域內跑；會連 MES）")
    ap.add_argument("--day", default=None, help="対象日 YYYY-MM-DD（窗＝[day, day+1)）；未給＝昨天")
    ap.add_argument("--wide-days", type=int, default=7, help="第三段的窗寬（天；預設 7）")
    ap.add_argument("--json", action="store_true", help="probe 結果輸出 JSON")
    ap.add_argument("--selftest", action="store_true", help="離線確定性單測（不連 MES）")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())
    if not args.probe:
        ap.error("要嘛 --selftest（離線），要嘛 --probe（連 MES；只由 Chris 域內跑）")
    try:
        res = probe(day=args.day, wide_days=args.wide_days)
    except Exception as e:
        print(f"[mes_family] 查詢失敗: {type(e).__name__}{_hint(e)}\n  {str(e)[:300]}")
        sys.exit(2)
    if args.json:
        import json
        print(json.dumps(res, ensure_ascii=False, default=str))


# =============================================================================
# 6. 離線單測（純合成假列、零 MES、零網路）
# =============================================================================
class _FakeCur:
    """離線假 cursor：讓 `run()` 整條編排在**零網路**下可測。

    只認得本模組真正會送出的兩條 SQL（Y1／Y2 的開頭）；其餘 ⇒ 空結果（防呆）。
    """

    def __init__(self, y1_rows=(), y2_rows=()):
        self.y1_rows = [tuple(r) for r in y1_rows]
        self.y2_rows = [tuple(r) for r in y2_rows]
        self.sql_log = []
        self._next = []

    def execute(self, sql, *params):
        self.sql_log.append((sql, params))
        s = sql.strip()
        if s.startswith("SELECT se.stpPK"):
            self._next = list(self.y1_rows)
        elif s.startswith("SELECT s.stpPK"):
            self._next = list(self.y2_rows)
        else:
            self._next = []
        return self

    def fetchall(self):
        return list(self._next)


def _selftest():
    print("=== mes_family --selftest（單日 per-EID family 活動計數；純離線合成假列）===")
    from mes_qlik import _T                    # 測試小工具重用（不再抄一份斷言 harness）
    t = _T()

    CAL = "segCalibrationTest"
    DA = "segDiePlacement"
    Y = datetime(2026, 9, 1)
    WIN = (Y, Y + timedelta(days=1))
    E1, E2 = "EID-10201", "EID-10470"
    # WP-10d（R-P11）：折疊表的具名 EID 由 mes_timeline 常數推導（私有樹＝38041→38106、38072 丟；公開樹＝合成名冊的同形一對）
    import core_paths as _wp
    import mes_timeline as _mt0
    REN, MAIN = next(iter(_mt0.EID_RENAME.items()))
    DROP = next(iter(_mt0.EID_ALIAS_DROP))
    _pub = _wp.is_public_tree()

    # ---- 0. 常數與折疊表身分（接錯＝全部作廢）--------------------------------
    t.eq(SCHEMA_ID, "board.family_day.v1", "0.1 schema pin")
    t.eq(FAMILIES, ("Plover", "Kestrel", "Heron"), "0.2 FAMILIES＝R/A/M 順序（SPEC §1）")
    t.eq(UNKNOWN, "UNKNOWN", "0.3 UNKNOWN 單一值（不留普查那兩種長字串）")
    t.eq((Y1_ARITY, Y2_ARITY), (2, 2), "0.4 兩條列 arity=2（SQL SELECT 欄序 pin）")
    t.eq((I_Y1_PK, I_Y1_EID, I_Y2_PK, I_Y2_FAM), (0, 1, 0, 1), "0.5 欄位索引 pin")
    import mes_timeline as _mt
    t.ok(fold_eid is _mt.fold_eid and EID_RENAME is _mt.EID_RENAME
         and EID_ALIAS_DROP is _mt.EID_ALIAS_DROP,
         "0.6 ★ 折疊表/函式＝mes_timeline 同一個物件（不抄第二份）")
    t.eq(EID_RENAME, {REN: MAIN} if _pub else {"EID-38041": "EID-38106"}, "0.7 rename 表＝38041→38106")
    t.eq(EID_ALIAS_DROP, {DROP: MAIN} if _pub else {"EID-38072": "EID-38106"}, "0.8 drop 表＝38072")
    t.ok(len(EQP_STATIONS) >= 39 and CAL in EQP_STATIONS and DA in EQP_STATIONS,
         "0.9 站清單＝mes_timeline.EQP_STATIONS（import 重用、永不 runtime discovery）")
    t.eq(set(_FAMILY_BY_CODE.values()), set(FAMILIES),
         "0.10 CASE 對照表值域＝FAMILIES（無第四種 family 偷渡）")
    try:
        import board_tags as _wt
        t.eq(FAMILIES, _wt.FAMILIES, "0.11 ★ cross-pin：與 board_tags.FAMILIES 逐位元同")
    except Exception:
        t.ok(True, "0.11 board_tags 不可用 ⇒ cross-pin 跳過（不擋離線單測）")

    # ---- 1. SQL 形狀＋負斷言（QG-2 §3；B2/B4/M4 血淚全在這一節）--------------
    (y1, p1), (y2, p2) = build_family_sql(CAL, *WIN)
    t.ok(y1.rstrip().endswith("OPTION (RECOMPILE)"), "1.1 Y1 尾 RECOMPILE")
    t.ok(y2.rstrip().endswith("OPTION (RECOMPILE)"), "1.2 Y2 尾 RECOMPILE")
    t.ok("FROM dbo.segment_equipment_v se" in y1, "1.3 Y1 單源＝segment_equipment_v")
    t.ok("FROM dbo.process_segment_v s" in y2, "1.4 Y2 單源＝process_segment_v")
    t.eq(p1, (CAL, Y, Y + timedelta(days=1)), "1.5 Y1 參數＝(cls, ws, we)")
    t.eq(p2, p1, "1.6 Y2 參數與 Y1 逐位元同（同一站同一窗）")
    t.eq(len(p1), 3, "1.7 參數恰 3 個（cls 走參數、不進 SQL 文本）")
    t.ok("se.stpStartTime >= ?" in y1 and "se.stpStartTime < ?" in y1,
         "1.8 Y1 窗＝stpStartTime 半開（>= 與 <）")
    t.ok("s.stpStartTime >= ?" in y2 and "s.stpStartTime < ?" in y2,
         "1.9 Y2 窗＝stpStartTime 半開")
    t.ok("se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime" in y1,
         "1.10 ★ Y1 帶 end 過濾（B4 d=30 斷崖修）")
    t.ok("se.stpEndTime > se.stpStartTime" not in y1,
         "1.11 ★★ Y1 end 過濾是 `>=` 不是 `>`（M4：`>` 刪掉零工期段＝八站整站隱形）")
    t.eq(y2.count("stpEndTime"), 0,
         "1.12 Y2 刻意零 stpEndTime（逐字同 mes_yield Y2／普查 Y2；未配對 pk 在 Python 丟）")
    all_text = "\n".join(ALL_SQL())
    t.eq(len(ALL_SQL()), 2, "1.13 ALL_SQL 登記 2 條（新增查詢必須登記）")
    t.ok("DISTINCT" not in all_text.upper(), "1.14 ★ 大 view 上零 DISTINCT（B4 斷崖）")
    t.ok("GROUP BY" not in all_text.upper(), "1.15 ★ 大 view 上零 GROUP BY（聚合全 Python）")
    t.ok("JOIN" not in all_text.upper(),
         "1.16 ★ 零 JOIN（兩個大 view 永不直接 join＝B2 事故）")
    t.ok("astEquipmentID IN" not in all_text and " IN (" not in all_text,
         "1.17 ★ 零 IN-list（B2：equipment 側 IN-list 翻計劃）")
    t.ok("#" not in all_text, "1.18 零 #temp（app 路徑不需要落地暫存表）")
    t.eq(all_text.count("stpPersonID"), 0, "1.19 ★ stpPersonID 全 SQL 文本零出現（§24）")
    t.ok("OperatorId" not in all_text and "PersonName" not in all_text,
         "1.20 ★ 任何人員識別欄零出現（§24）")
    t.eq(y1.count("SegmentProductLineCode"), 0,
         "1.21 ★ Y1 零 SegmentProductLineCode（Plan A 實證 Msg 207、不得復辟）")
    t.eq(y2.count("SegmentProductLineCode"), 1, "1.22 family 原碼只從 Y2 取")
    t.ok("matMaterialLotID" not in all_text,
         "1.23 lot 欄不取（family≠lot 前綴；lot 樣本是普查的人工 triage 欄）")
    try:
        build_family_sql(CAL, WIN[1], WIN[0])
        t.ok(False, "1.24 反向窗必 raise")
    except ValueError:
        t.ok(True, "1.24 反向窗必 raise（_win 守衛重用）")
    for bad in (None, "", "seg'; DROP--", 42, "seg Class"):
        try:
            build_family_sql(bad, *WIN)
            t.ok(False, f"1.25 壞 cls 必 raise: {bad!r}")
        except ValueError:
            t.ok(True, f"1.25 壞 cls 必 raise: {bad!r}")

    # ---- 2. normalize_family 逐字對照 CASE 四分支 -----------------------------
    t.eq(normalize_family("Plover"), "Plover", "2.1 CASE ①plover → Plover")
    t.eq(normalize_family("KESTREL"), "Kestrel", "2.2 CASE ②kestrel → Kestrel（LOWER）")
    t.eq(normalize_family("heron"), "Heron", "2.3 CASE ③heron → Heron")
    t.eq(normalize_family(None), UNKNOWN, "2.4 CASE ④NULL → UNKNOWN（substrate/非產品段）")
    t.eq(normalize_family("Aligner"), UNKNOWN, "2.5 CASE ⑤不認識的碼 → UNKNOWN")
    t.eq(normalize_family(""), UNKNOWN, "2.6 空字串 → UNKNOWN（ISNULL(...,'') 同義）")
    t.eq(normalize_family("plover  "), "Plover",
         "2.7 尾空白 → Plover（重現 SQL Server 尾空白補齊比較）")
    t.eq(normalize_family(" plover"), UNKNOWN, "2.8 前導空白 → UNKNOWN（SQL 端也不相等）")
    t.eq(normalize_family(42), UNKNOWN, "2.9 非字串 → UNKNOWN（不炸）")
    t.ok(all(normalize_family(f) == f for f in FAMILIES),
         "2.10 canonical 值自身冪等（Plover/Kestrel/Heron）")
    t.ok(UNKNOWN not in FAMILIES, "2.11 ★ UNKNOWN 永不是 family（SPEC §2.1 C-PT2）")

    # ---- 3. aggregate_families：join 正確性與各條防護 -------------------------
    def A(y1_rows, y2_rows, stats=None):
        return aggregate_families(y1_rows, y2_rows, stats=stats)

    s3 = {}
    a3 = A([(1, E1), (2, E1), (3, E2)],
           [(1, "Plover"), (2, "Kestrel"), (3, "heron")], s3)
    t.eq(a3, {E1: {"Plover": 1, "Kestrel": 1}, E2: {"Heron": 1}},
         "3.1 stpPK join 正確（每台機各 family 段數）")
    t.eq(s3["n_pairs"], 3, "3.2 三對 (pk,eid) 全進帳")
    t.eq((s3["n_unmatched_pairs"], s3["n_unknown_pairs"], s3["n_dup_pairs"]), (0, 0, 0),
         "3.3 乾淨輸入三個桶全 0")
    t.eq(s3["n_segments"], 3, "3.4 n_segments 自揭＝3")
    t.eq(list(a3[E1]), ["Plover", "Kestrel"], "3.5 family 鍵序＝FAMILIES（落盤可重現）")
    t.eq(list(A([(1, E2), (2, E1)], [(1, "Plover"), (2, "Plover")])), [E1, E2],
         "3.6 eid 鍵序＝升冪（落盤可重現）")

    s4 = {}
    a4 = A([(1, E1), (2, E1)], [(1, None), (2, "substrate-only")], s4)
    t.eq(a4, {}, "3.7 ★ 全 UNKNOWN 的機台整個不出現（不寫 {'UNKNOWN': n}、也不寫空 dict）")
    t.eq(s4["n_unknown_pairs"], 2, "3.8 UNKNOWN 走自揭欄")
    t.ok(a4 == {} and a4 is not None, "3.9 ★ {} ≠ None（查過零段 vs 沒查，語意不同）")

    s5 = {}
    a5 = A([(1, E1), (9, E1)], [(1, "Plover")], s5)
    t.eq(a5, {E1: {"Plover": 1}}, "3.10 Y1 有、Y2 沒有的 stpPK 自然丟（同普查 INNER JOIN）")
    t.eq(s5["n_unmatched_pairs"], 1, "3.11 未配對自揭")

    s6 = {}
    a6 = A([(1, E1), (1, E1), (1, E2)], [(1, "Plover")], s6)
    t.eq(a6, {E1: {"Plover": 1}, E2: {"Plover": 1}},
         "3.12 同段掛兩台＝各計一次（fan-out、設備視角）；完全重複列只計一次")
    t.eq(s6["n_dup_pairs"], 1, "3.13 重複 (pk,eid) 對自揭（不虛胖活動）")

    s7 = {}
    a7 = A([(1, REN), (2, DROP), (3, MAIN)],
           [(1, "Kestrel"), (2, "Kestrel"), (3, "Kestrel")], s7)
    t.eq(a7, {MAIN: {"Kestrel": 2}},
         "3.14 ★ 形④ 折疊：38041 改名進主、38072 整列丟（不相加＝不雙算）")
    t.eq(s7["n_alias_dropped"], 1, "3.15 alias drop 自揭")
    t.ok(DROP not in a7 and REN not in a7, "3.16 別名 EID 一顆都不進輸出")
    s7b = {}
    a7b = A([(5, REN), (5, MAIN)], [(5, "Kestrel")], s7b)
    t.eq(a7b, {MAIN: {"Kestrel": 1}},
         "3.17 ★ 同一 stpPK 同掛別名與主 ⇒ 折疊後撞 (pk,eid) 去重＝結構性不可能雙算")
    t.eq(s7b["n_dup_pairs"], 1, "3.18 那一列走 dup 桶（自揭、不靜默）")

    s8 = {}
    a8 = A([(1, None), (None, E1), ("short",), (2, E1)],
           [(1, "Plover"), (None, "Plover"), ("short2",), (2, "Plover")], s8)
    t.eq(a8, {E1: {"Plover": 1}}, "3.19 壞列不炸、好列照進帳")
    t.eq((s8["n_bad_rows_y1"], s8["n_bad_rows_y2"]), (3, 2), "3.20 壞列兩邊各自自揭")

    s9 = {}
    a9 = A([(1, E1)], [(1, "Plover"), (1, "Kestrel")], s9)
    t.eq(a9, {E1: {"Plover": 1}}, "3.21 同 pk 兩種 family ⇒ 先中先出（不改寫）")
    t.eq(s9["n_pk_family_conflicts"], 1, "3.22 pk family 衝突自揭")

    s10 = {}
    t.eq(A([], [], s10), {}, "3.23 空輸入 ⇒ {}（不是 None、也不是站鍵全 0）")
    t.eq((s10["n_rows_y1"], s10["n_rows_y2"], s10["n_eids"]), (0, 0, 0),
         "3.24 空輸入自揭全 0")
    t.eq(A([(1, E1)], []), {}, "3.25 Y2 全空 ⇒ 沒有一段能定 family ⇒ {}")
    poisoned = A([(1, E1, "王小明")], [(1, "Plover", "EMP0042", "王小明")])
    t.eq(poisoned, {E1: {"Plover": 1}},
         "3.26 ★ 多餘尾欄（含人名）不影響計數、也不進輸出（§24 全鏈）")

    # ---- 4. run() 端到端（FakeCur；零網路）------------------------------------
    cur = _FakeCur([(1, E1), (2, E2), (3, DROP)],
                   [(1, "Plover"), (2, "heron"), (3, "Plover")])
    st = {}
    r = run(CAL, WIN, cur, stats=st)
    t.eq(r, {E1: {"Plover": 1}, E2: {"Heron": 1}}, "4.1 run 端到端結果")
    t.eq(len(cur.sql_log), 2, "4.2 恰送兩條查詢（Y1、Y2）")
    t.ok(cur.sql_log[0][0].strip().startswith("SELECT se.stpPK")
         and cur.sql_log[1][0].strip().startswith("SELECT s.stpPK"),
         "4.3 順序＝Y1 先、Y2 後（Y1 零列時不送 Y2）")
    t.eq(cur.sql_log[0][1], (CAL, Y, Y + timedelta(days=1)), "4.4 Y1 參數傳到底")
    t.eq(cur.sql_log[1][1], (CAL, Y, Y + timedelta(days=1)), "4.5 Y2 參數傳到底")
    t.eq(st["cls"], CAL, "4.6 stats 帶 cls")
    t.eq(st["y2_skipped"], False, "4.7 有 Y1 列 ⇒ Y2 照送")
    t.ok(all(k in st["timing"] for k in ("y1_ms", "y2_ms", "agg_ms", "total_ms")),
         "4.8 stats.timing 四段全在")
    t.eq(st["win"], [Y.isoformat(), (Y + timedelta(days=1)).isoformat()],
         "4.9 stats.win＝iso 半開窗")
    t.eq(st["schema"], SCHEMA_ID, "4.10 stats 帶 schema")
    t.eq(st["n_alias_dropped"], 1, "4.11 端到端仍走折疊（38072 丟）")
    sent = "\n".join(s for s, _p in cur.sql_log)
    t.ok("stpPersonID" not in sent and "DISTINCT" not in sent.upper()
         and "GROUP BY" not in sent.upper(),
         "4.12 ★ 端到端真正送出的 SQL 過三道負斷言")

    cur_e = _FakeCur([], [(1, "Plover")])
    st_e = {}
    t.eq(run(DA, WIN, cur_e, stats=st_e), {}, "4.13 Y1 零列 ⇒ {}")
    t.eq(len(cur_e.sql_log), 1, "4.14 ★ Y1 零列 ⇒ **不送 Y2**（省一條大 view 掃描）")
    t.eq((st_e["y2_skipped"], st_e["timing"]["y2_ms"]), (True, 0), "4.15 y2_skipped 自揭")
    t.eq(run(DA, WIN, _FakeCur([(1, E1)], [(1, "Plover")])), {E1: {"Plover": 1}},
         "4.16 stats=None 也能跑（可選參數）")
    try:
        run(CAL, (WIN[1], WIN[0]), _FakeCur())
        t.ok(False, "4.17 反向窗必 raise（run 也守）")
    except ValueError:
        t.ok(True, "4.17 反向窗必 raise（run 也守）")

    # ---- 5. day_window（窗語意）----------------------------------------------
    t.eq(day_window("2026-09-01"), (Y, Y + timedelta(days=1)),
         "5.1 day_window＝[day 00:00, day+1 00:00)")
    t.eq(day_window(date(2026, 9, 1)), day_window("2026-09-01"), "5.2 date 與字串等價")
    t.eq(day_window(datetime(2026, 9, 1, 13, 45)), day_window("2026-09-01"),
         "5.3 datetime 取其日（時分秒不影響窗）")
    _yd = day_window()
    t.eq((_yd[1] - _yd[0]), timedelta(days=1), "5.4 未給 day＝昨天、窗寬恰 1 天")

    # ---- 6. 模組不得在 import 時連線（零副作用）--------------------------------
    t.ok("pyodbc" not in sys.modules,
         "6.1 ★ import 本模組不得帶進 pyodbc（連線只在 open_connection 內部發生）")

    if t.bad:
        print(f"\n✗ {len(t.bad)} 條失敗：")
        for b in t.bad:
            print(f"    - {b}")
        print(f"mes_family selftest: {t.n - len(t.bad)}/{t.n} PASS")
        return 1
    print(f"mes_family selftest: {t.n} 斷言 PASS")
    return 0


if __name__ == "__main__":
    main()
