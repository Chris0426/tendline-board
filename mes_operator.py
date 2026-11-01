#!/usr/bin/env python3
"""Tendline Board — MES operator headcount（「這站此刻約有幾雙手」）計算核心 `board.operator.v1`。

**這是什麼**：全線調度板 `/dispatch` **人力欄**的資料源（`SPEC-DISPATCH.md` §9.3 的落地）。
回答一件事——**「近 N 分鐘內，這站有幾個不重複的人在操作？」**
**不是什麼**：不是考勤、不是排班（那要等 SALTMARSH 排班檔 G1）、不是人員定位、
**更不是「誰在哪站」**——本模組永遠不回答「是誰」。

語意權威（分歧一律以這份為準、不以本檔註解為準）：
  · `SPEC-DISPATCH.md` §9.1（人名紅線全文）／§9.2（R4 探針判讀）／§9.3（D-OP1–D-OP4 裁決
    ＋本模組 API pin）。探針原檔＝`_OPR2-operator-coverage.sql`（唯讀、只輸出計數）。

★★ 人名紅線（§24 延伸；違反即 HIGH）
  · **人數不是人名。** 姓名/工號只允許活在 **SQL 查詢層**做跨機去重（同一人同時段操作兩台機
    不能算兩個人），**聚合成人數的那一刻即丟**。本模組的介面**只有整數計數**——
    `COUNT(DISTINCT ...)` 全部在 SQL 內完成，**識別值連 Python 都不進**（＝§24 最強形）。
  · **person key pin ＝ `OperatorId`（數字 ID）**。`stpPersonID`（姓名格式欄）**任何查詢一律
    不 SELECT**——全檔 SQL 文本零出現，`_selftest` 有負斷言釘死（`ALL_SQL()`）。
  · 不落快取、不落報表、不進前台、不進 log、不進 fixture、不進 demo 合成。
    任何「順便看看誰在哪站」的需求＝破壞契約，一律拒。

★ 口徑（D-OP1；改＝回 SPEC 改字，不在這裡改）
  · 主值 `n_now` ＝「近 `now_min` 分鐘內有**操作紀錄**的不重複人數」＝**在場代理、有滯後**
    （人在但沒碰機→不被計；剛離站→還被計一陣）。**無紀錄 ≠ 現場無人**（tooltip 明講）。
  · 次值 `n_4h` ＝ 同義但窗＝`ref_h` 小時（參考值、看班別規模）。
  · **窗內零列＝真 0**（消費面顯淡色 0）；**route 站近 `class_d` 天完全無列＝缺鍵**
    （消費面顯「—」＋`dp_mp_nosig`）。**兩者語意不同、絕不互相冒充。**

★ 自動站（D-OP2；確定性規則，**勿 runtime 學習**）
  近 `class_d` 天 `d7_persons ≤ max_persons` **且** `d7_blocks ≥ min_blocks`
  ⇒ `auto=True`＝「機器帳號站」（MES 登入帳號綁機台不綁人；R4 實證：segSubstrateReceiveSupplierData
  7d 大量列僅 1 人）⇒ 消費面顯「—」＋`dp_mp_auto`，**絕不把 1 當在場人數**（假在場）。
  分類依據（`diag.d7_*`）隨值回傳＝使用者可在 ?-hover 自行驗算；門檻誤判走 **env 調、不改碼**。

★ D-OP3 恆真句陷阱（紅線；全文另釘 `SPEC-DAILY-ATTRIB` §7）
  人員訊號**長在 segment 列上**——機台閒置＝無 segment＝無人員訊號 ⇒「閒置且無人操作紀錄」
  對任何閒置片**恆真、零鑑別力**。**此源永不得用於 /daily 殘差拆分或任何「閒置歸因」**，
  也**不參與 dispatch 燈判定**（燈規則要吃 headcount＝另開裁決，不在本輪偷接）。

★ D-OP4 線級去重
  `line` ＝ **SQL 內全線 `COUNT(DISTINCT OperatorId)`**（R4 C4：24h 內 32 人中多數跨 ≥2 站）
  ⇒ **與 per-station 相加不相等屬預期**，消費面 footer 要講明（`dp_mp_line_note`）。
  ⚠ 已知上界性質（`diag.line_scope` 自揭）：line 的母體＝**掃描窗內全部站**，含 route 以外
    的站與 auto 站的機器帳號 ⇒ 它是**上界**、不是「產線上的活人數」。hero 若要用，
    消費端自行決定要不要顯（本模組不替它裁決、也不另發明第二個數字）。

★ perf lore（B4/M4 教訓；改本檔前先讀）
  · **兩段式**：先 `CREATE TABLE #temp`＋`INSERT ... SELECT`（**窄窗**掃單一 view）→
    再對 `#temp` 聚合。**絕不在 5 表大 view 上 DISTINCT/GROUP BY**（blocking aggregate
    隨窗斷崖）。**絕不用 `SELECT ... INTO #temp` 帶參數**——pyodbc 帶參數走 prepare
    （`sp_prepexec`）＝巢狀 scope，`#temp` 在該句結束即被丟，下一句聚合直接
    `Invalid object name`＝2026-08-01 live `ProgrammingError` 事故（離線 selftest 照不出）。
  · 每條 SQL 尾 `OPTION (RECOMPILE)`（DDL 除外）；**絕不對 view 塞動態 IN-list**（本檔連
    站過濾都不套——窄窗全線列數極小，route 交集歸 Python）。
  · `COLLATE` 釘 **#temp 側**（CREATE TABLE 欄定義＋INSERT 運算式雙保險）＝不封死 view 側
    seek，也免掉聚合時的 collation conflict（軌一 QA-S 事故）。
  · 兩層窗、兩層 TTL：counts 層（分鐘級窗、短 TTL）與分類層（`class_d` 天、長 TTL）
    **分開兩支查詢**，消費端各自快取（`BOARD_OPR_TTL` / `BOARD_OPR_CLASS_TTL`）。
  · 本管線只用 `stpStartTime`、**不碰 `stpEndTime`**（日後若有人要加 end 過濾：一律 `>=`，
    `>` 會刪掉合法零工期段、八站整站隱形＝[[mes-zero-length-segments]]）。

★ 唯讀：對 view 只有 SELECT；唯一 DDL/DML 在 `#temp`（tempdb 不算寫 MES）。
★ 凍結模組 `mes_timeline.py` / `mes_yield.py`：**不 import、不執行、零 diff**。
  `_connect` 走 `mes_qlik._connect`（非凍結、含 `SET ARITHABORT ON`）＝不再抄第四份連線碼。

★ env（SPEC §9.3；預設值即 pin 值，env 只在 live 校準時動。app 端讀、以參數傳入）
    BOARD_OPR_NOW_MIN=60          BOARD_OPR_REF_H=4        BOARD_OPR_CLASS_D=7
    BOARD_OPR_AUTO_MAX_PERSONS=2  BOARD_OPR_AUTO_MIN_BLOCKS=12
    （消費端快取層：BOARD_OPR_TTL=300／BOARD_OPR_CLASS_TTL=3600，本模組不讀）

CLI:
    python mes_operator.py [--cap "2026-08-01T10:30"] [--now-min 60] [--json]
    python mes_operator.py --selftest        # 純離線合成資料、不連 MES
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

# ── 積木一律 import 重用（SPEC §9.3 pin；站序不另抄一個位元）─────────────────────
from mes_qlik import (
    QLIK_STATIONS, SchemaDivergence, _connect, _dt,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# 1. 常數（改它＝人工動作；SPEC §9.3 pin）
# =============================================================================
SCHEMA_ID = "board.operator.v1"
CALIBER_OPR = ("mes-operator presence proxy (distinct OperatorId in window; "
               "counts only, never names)")

# ★ person key pin。**只有這一個**——`stpPersonID`（姓名格式）永不出現在任何 SQL 文本。
PERSON_COL = "OperatorId"

# 0-sentinel：R4 實測 `n_op_zero=0`，但 0 是 MES 常見「系統代打」佔位值 ⇒ 一律當作
# 「無人員訊號」剔除（若哪天冒出來，`diag.n_op_zero` 會大聲自揭，不會靜默灌水 distinct）。
OP_ZERO_SENTINEL = "0"

OPR_TEMP = "#board_opr"          # counts 層（分鐘級窄窗）
OPR7_TEMP = "#board_opr7"        # 分類層（class_d 天）
LINE_KEY = "__line__"         # [O1] 線級哨兵列（**不是站名**；解析後即丟）

# #temp 側 collation（軌一 QA-S 事故：裸 nvarchar 吃 tempdb collation ⇒ 聚合/比較炸）
TEMP_COLLATE = "Latin1_General_100_CI_AS_SC_UTF8"

# [G0] 承重欄位：任一 ABSENT ⇒ raise（沒有這些欄，人力欄整條沒有意義）。
REQUIRED_OPR_COLUMNS = ("SegmentClassID", "stpStartTime", PERSON_COL)

QUERY_TIMEOUT_SEC = 180


def _env_int(name, default):
    """env 覆寫（typo 不炸開機＝R1 審查輪教訓：壞值/非正數一律退回預設）。"""
    try:
        v = int(str(os.environ.get(name, "")).strip())
        return v if v > 0 else default
    except Exception:
        return default


NOW_MIN = _env_int("BOARD_OPR_NOW_MIN", 60)              # 主值窗（分鐘）
REF_H = _env_int("BOARD_OPR_REF_H", 4)                   # 參考值窗（小時）
CLASS_D = _env_int("BOARD_OPR_CLASS_D", 7)               # 分類層窗（天）
AUTO_MAX_PERSONS = _env_int("BOARD_OPR_AUTO_MAX_PERSONS", 2)
AUTO_MIN_BLOCKS = _env_int("BOARD_OPR_AUTO_MIN_BLOCKS", 12)

BLOCK_MIN = 240        # 「4h 塊」＝240 分鐘（D-OP2 的塊定義；改它＝改分類語意）


# =============================================================================
# 2. 小工具（純函式）
# =============================================================================
_SQL_IDENT = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _ident(name):
    """欄名只允許識別字字元（承重欄名全來自常數，仍守一道＝注入面歸零）。"""
    if not name or not set(name) <= _SQL_IDENT:
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _int0(x):
    """DB/合成值 → int（None/壞值 → 0）。**人數欄永遠是 int，沒有 None、沒有 float。**"""
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _route_cls(route=None):
    """route → 有序去重的 cls tuple。

    接受三種形狀：`None`（＝`QLIK_STATIONS` 全 43 站、ord 序）／`(ord, cls, mcl)` 元組序列
    （＝`mes_wip` 慣例）／cls 字串序列。**set/frozenset 會先排序**＝輸出順序恆定
    （否則 stations 的插入序隨執行變動，消費面表格會跳動）。
    """
    seq = QLIK_STATIONS if route is None else route
    if isinstance(seq, (set, frozenset)):
        seq = sorted(seq)
    out, seen = [], set()
    for item in seq:
        cls = item[1] if isinstance(item, (tuple, list)) else str(item)
        if cls and cls not in seen:
            seen.add(cls)
            out.append(cls)
    return tuple(out)


def classify_auto_station(d7_persons, d7_blocks, *,
                          max_persons=AUTO_MAX_PERSONS, min_blocks=AUTO_MIN_BLOCKS):
    """D-OP2：**機器帳號站**判定（純函式、確定性；`True` ⇒ 消費面顯「—」不顯人數）。

    規則＝`d7_persons ≤ max_persons` **且** `d7_blocks ≥ min_blocks`
    ——「人少」**且**「日夜都在跑」才算自動站。少了後半，低量人工站（一天做兩次、只有一個
    技術員）會被誤標成自動站而整站顯「—」；少了前半，正常人工站會被當機器。

    邊界語意（釘死、勿憑印象改）：
      · `d7_persons` 恰 `max_persons` ⇒ **True**（含等於）。
      · `d7_blocks` 恰 `min_blocks`   ⇒ **True**（含等於）。
      · `d7_persons == 0`（有列但完全不記名）⇒ 落 True 分支＝顯「—」。tooltip 講的成因
        （帳號跟機）在這一格未必精確，但**方向誠實**（人數確實不可測），比顯「0 人」好。
      · 任一輸入 `None`（分類層缺資料）⇒ **False**＝不主張自動站（不可知不亂扣帽子）。
    """
    if d7_persons is None or d7_blocks is None:
        return False
    try:
        p, b = int(d7_persons), int(d7_blocks)
    except (TypeError, ValueError):
        return False
    return p <= int(max_persons) and b >= int(min_blocks)


# =============================================================================
# 3. I/O 層（SQL builder 抽成純函式＝離線可斷言形狀；唯讀＝SELECT ＋ #temp）
# =============================================================================
def _sql_drop(temp):
    """#temp 前次中止殘留防呆（DDL，不帶 RECOMPILE）。"""
    if not temp.startswith("#") or not set(temp[1:]) <= _SQL_IDENT:
        raise ValueError(f"unsafe temp table name: {temp!r}")
    return f"IF OBJECT_ID('tempdb..{temp}') IS NOT NULL DROP TABLE {temp};"


def _op_expr(alias="s"):
    """person key 正規化運算式（**唯一取值點**；型別無關＋0-sentinel 剔除＋COLLATE 釘死）。

    ★ 為什麼 CAST 成字串：`OperatorId` 若是字串型，`NULLIF(op, 0)` 會走隱式轉換（可能整條
      炸掉或悄悄改語意）；統一 CAST 成 nvarchar ⇒ 數值/字串兩型都安全，distinct 基數不變。
    ★ 為什麼 COLLATE 掛這裡：#temp 欄若吃 **tempdb** collation，之後的比較/聚合可能撞
      collation conflict（軌一 QA-S 事故）。CREATE TABLE 欄定義已釘死；運算式再帶一次
      ＝雙保險，view 側完全不受影響、seek 不受害。
    ★ 空字串與 `'0'` → NULL（`COUNT(DISTINCT)` 天然忽略 NULL ⇒ 不灌水人數）。
    """
    a = _ident(alias)
    raw = f"LTRIM(RTRIM(CAST({a}.{_ident(PERSON_COL)} AS nvarchar(64))))"
    return (f"NULLIF(NULLIF({raw}, N''), N'{OP_ZERO_SENTINEL}')"
            f" COLLATE {TEMP_COLLATE}")


def _cls_expr(alias="s"):
    """站別欄同樣把 collation 釘在 **#temp 側**（materialize 當下）。

    ★ 為什麼連 cls 都要釘：`[O1b]` 的 `UNION ALL` 那支是**字面值** `N'__line__'`
      （吃當前 DB 預設 collation），與 #temp 欄 collation 不同時 SQL Server 直接
      `Cannot resolve the collation conflict` 整條炸（＝軌一 QA-S 事故的同一族）。
      欄定義、運算式、字面值三處都明寫同一個 collation ⇒ 這個坑永久關閉。
    """
    a = _ident(alias)
    return f"{a}.SegmentClassID COLLATE {TEMP_COLLATE}"


def _op_zero_expr(alias="s"):
    a = _ident(alias)
    return (f"CASE WHEN LTRIM(RTRIM(CAST({a}.{_ident(PERSON_COL)} AS nvarchar(64))))"
            f" = N'{OP_ZERO_SENTINEL}' THEN 1 ELSE 0 END")


def _op_null_expr(alias="s"):
    a = _ident(alias)
    return f"CASE WHEN {a}.{_ident(PERSON_COL)} IS NULL THEN 1 ELSE 0 END"


def _sql_guard():
    """[G0] column guard SQL（view ＋ 承重 3 欄）。"""
    req = ",\n           ".join(
        f"CASE WHEN COL_LENGTH('dbo.process_segment_v','{_ident(c)}') IS NULL THEN 0 ELSE 1 END"
        for c in REQUIRED_OPR_COLUMNS)
    return ("SELECT CASE WHEN OBJECT_ID('dbo.process_segment_v') IS NULL THEN 0 ELSE 1 END,\n"
            f"       {req}\n"
            "OPTION (RECOMPILE)")


GUARD_KEYS = ("view_seg",) + tuple(REQUIRED_OPR_COLUMNS)


def assert_operator_guard(guard):
    """承重欄檢查（純函式＝離線可測）。ABSENT ⇒ `SchemaDivergence`、訊息逐欄具名。

    「停下」而不是「回一盤全 0」的理由：全 0 在消費面與「現場真的沒人操作」長得一模一樣
    ＝靜默謊。呼叫端（`board_data` → `app`）接成 `operator_unavailable`
    ＝人力欄整欄「—」＋`dp_mp_off`，仍不炸站。
    """
    if not guard.get("view_seg"):
        raise SchemaDivergence("schema divergence: dbo.process_segment_v ABSENT")
    missing = [c for c in REQUIRED_OPR_COLUMNS if not guard.get(c)]
    if missing:
        raise SchemaDivergence("schema divergence: "
                               + ", ".join(f"{c} ABSENT" for c in missing))
    return True


def fetch_operator_guard(cur):
    """[G0] 欄位守衛。回 dict（全 bool）：`GUARD_KEYS` 逐鍵。承重 ABSENT ⇒ raise。"""
    cur.execute(_sql_guard())
    r = cur.fetchone()
    guard = {k: bool(r[i]) for i, k in enumerate(GUARD_KEYS)}
    assert_operator_guard(guard)
    return guard


def _sql_create_counts():
    """[O1a-DDL] counts 層 `#temp` 建表（**無參數＝直送批次 ⇒ session scope**）。

    ★★ 為什麼是 CREATE＋INSERT、不是 `SELECT ... INTO`（2026-08-01 live 事故的修）：
      pyodbc 對**帶參數**的 statement 走 prepare（`sp_prepexec`）＝巢狀 scope——
      `SELECT ... INTO #t` 在該 statement 結束時 `#t` 就被丟掉，下一句聚合
      `FROM #t` 直接 `Invalid object name` ⇒ `ProgrammingError`（人力欄整欄降級）。
      `CREATE TABLE`（無參數→外層 scope）＋帶參數 `INSERT ... SELECT`＝
      `mes_qlik`/`mes_wip` 在 live 已驗證的同一形，本檔照它。
    ★ collation 直接釘在**欄定義**上（tempdb 預設 collation 的坑在建表當下關閉）。
    """
    return (f"CREATE TABLE {OPR_TEMP} (\n"
            f"    cls nvarchar(128) COLLATE {TEMP_COLLATE} NULL,\n"
            "    ts datetime2(3) NULL,\n"
            f"    op nvarchar(64) COLLATE {TEMP_COLLATE} NULL,\n"
            "    op_zero int NULL,\n"
            "    op_null int NULL);")


def _sql_insert_counts():
    """[O1a] counts 層母體物化：**一次窄窗掃描**、單一 view、無 join、無 GROUP BY/DISTINCT。

    窗＝`max(now_min 分, ref_h 時)`（呼叫端算好用參數帶）。
    **刻意不套 Process01 過濾**：`stpTestResult IS NULL`（正在機上跑、還沒判定）那些列
    **也是有人在操作的證據**——人力欄問的是「有沒有人在動」，不是「良率母體」。
    **刻意不套站過濾**：窄窗全線列數極小（R4：7d 全線 142k 列 ⇒ 4h ≈ 3.4k 列），
    route 交集在 Python 做＝免掉對 view 塞動態 IN-list（B4 鐵則）。
    """
    return (f"INSERT INTO {OPR_TEMP} (cls, ts, op, op_zero, op_null)\n"
            f"SELECT {_cls_expr('s')},\n"
            "       s.stpStartTime,\n"
            f"       {_op_expr('s')},\n"
            f"       {_op_zero_expr('s')},\n"
            f"       {_op_null_expr('s')}\n"
            "FROM   dbo.process_segment_v AS s\n"
            "WHERE  s.stpStartTime >= ? AND s.stpStartTime < ?\n"
            "OPTION (RECOMPILE)")


def _sql_agg_counts():
    """[O1b] 對 `#temp` 聚合（小結果集＝合規）：per-cls 兩窗 distinct ＋ 線級哨兵列。

    ★ **`COUNT(DISTINCT op)` 一律在 SQL 內完成**＝識別值連 Python 都不進（§24 最強形）。
    ★ 兩窗都用 `CASE WHEN ts >= ?` 明寫（**不靠掃描窗邊界隱含**）——否則 `ref_h*60 < now_min`
      的 env 誤配會讓 `n_4h` 悄悄變成「now 窗的數字」卻掛著 4h 的名字。
    ★ 線級哨兵列＝`__line__`：跨站去重只有 SQL 做得到（Python 手上只有計數）。
      空表時 `UNION ALL` 這一支仍回一列 0 ⇒ 消費端永遠拿得到 `line`。
    """
    return ("SELECT cls,\n"
            "       COUNT(DISTINCT CASE WHEN ts >= ? THEN op END) AS n_now,\n"
            "       COUNT(DISTINCT CASE WHEN ts >= ? THEN op END) AS n_ref\n"
            f"FROM   {OPR_TEMP}\n"
            "GROUP BY cls\n"
            "UNION ALL\n"
            f"SELECT N'{LINE_KEY}' COLLATE {TEMP_COLLATE},\n"
            "       COUNT(DISTINCT CASE WHEN ts >= ? THEN op END),\n"
            "       COUNT(DISTINCT CASE WHEN ts >= ? THEN op END)\n"
            f"FROM   {OPR_TEMP}\n"
            "OPTION (RECOMPILE)")


def _sql_stats_counts():
    """[O1c] counts 層自揭（掃描列數／0-sentinel／NULL／站數）。純 diag、不影響數字。"""
    return ("SELECT COUNT(*) AS n_rows,\n"
            "       SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS n_rows_now,\n"
            "       SUM(op_zero) AS n_op_zero,\n"
            "       SUM(op_null) AS n_op_null,\n"
            "       COUNT(DISTINCT cls) AS n_cls\n"
            f"FROM   {OPR_TEMP}\n"
            "OPTION (RECOMPILE)")


def _sql_create_class():
    """[O2a-DDL] 分類層 `#temp` 建表（無參數＝外層 scope；理由同 `_sql_create_counts`）。"""
    return (f"CREATE TABLE {OPR7_TEMP} (\n"
            f"    cls nvarchar(128) COLLATE {TEMP_COLLATE} NULL,\n"
            f"    op nvarchar(64) COLLATE {TEMP_COLLATE} NULL,\n"
            "    blk int NULL);")


def _sql_insert_class():
    """[O2a] 分類層母體物化（`class_d` 天窄欄）：cls／op／4h 塊號。單 view、無 join。

    ★ 塊號用 **`DATEDIFF(MINUTE, ts, cap) / 240`**（不是 `DATEDIFF(HOUR, ...)/4`）：
      HOUR 版數的是**整點跨越次數**，`cap` 非整點時會多冒一塊、且塊界會隨呼叫時刻漂移
      ＝分類門檻在半夜與白天不同義。這個坑 `_OPR2` 探針 [C2] 已經踩過並改成分鐘除法，
      本檔照它（自揭在 `diag.blk_expr`）。
    ★ 這是**長 TTL 層**：7d 掃描比 counts 層貴一個量級，消費端必須用小時級快取
      （`BOARD_OPR_CLASS_TTL`），**絕不跟 counts 層共用一個 TTL**。
    """
    return (f"INSERT INTO {OPR7_TEMP} (cls, op, blk)\n"
            f"SELECT {_cls_expr('s')},\n"
            f"       {_op_expr('s')},\n"
            f"       DATEDIFF(MINUTE, s.stpStartTime, ?) / {BLOCK_MIN}\n"
            "FROM   dbo.process_segment_v AS s\n"
            "WHERE  s.stpStartTime >= ? AND s.stpStartTime < ?\n"
            "OPTION (RECOMPILE)")


def _sql_agg_class():
    """[O2b] 對 `#temp` 聚合：per-cls (d7_persons, d7_blocks, d7_rows)。

    `d7_blocks` ＝ distinct 塊號（**塊內 ≥1 列即活躍**，不看該列有沒有 op
    ——「這站日夜都在跑」問的是機台活動密度，不是記名率）。
    """
    return ("SELECT cls,\n"
            "       COUNT(DISTINCT op)  AS d7_persons,\n"
            "       COUNT(DISTINCT blk) AS d7_blocks,\n"
            "       COUNT(*)            AS d7_rows\n"
            f"FROM   {OPR7_TEMP}\n"
            "GROUP BY cls\n"
            "OPTION (RECOMPILE)")


def _sql_agg_kun_counts(n_pairs):
    """[O3b] 對 `#temp` 聚合：近窗**綑級**不重複人數；成員表只以參數 `VALUES` 帶入。

    ★★ **為什麼是「對 #temp 聚合」而不是直接對大 view 做 `COUNT(DISTINCT)`**：
      本模組（與 `mes_wip`）的兩段式紀律＝**大 view 只做無聚合的窄窗掃描**、
      `DISTINCT`／`GROUP BY` 一律留給 `#temp`（`_sql_agg_counts` 的 `2.14 聚合不碰大 view`）。
      對 `dbo.process_segment_v` 直接下 blocking aggregate ＝ 2026-07 MES perf saga 的真兇形；
      而且「每綑一個相關子查詢」還會把它乘上綑數。**這裡一列都不掃 view**——母體是
      `[O1a]` 已經物化好的那張 `#board_opr`（同一條連線、同一個窄窗）。
    ★ 人名紅線：`op` 欄在物化當下就是清洗過的 person 運算式，`COUNT(DISTINCT op)`
      仍**全在 SQL 內**完成，結果集只有 `(kun, n)` 兩欄。識別值連 Python 都不進。
    ★ `GROUP BY m.kun` ＋ `COUNT(DISTINCT t.op)`（不是攤平站級結果再於 Python 相加）
      才能保證「**綑內 distinct、跨綑不去重**」——同一人 60 分鐘內跨兩綑，成員表把
      他的兩個站分別 join 到兩個 kun，兩綑各算一次（§18.8 明示）。
    ★★ **`VALUES` 只能當 derived table、不能當 CTE 本體**（2026-09-06 live 事故的修）：
      `WITH km (cls, kun) AS (VALUES ...)` 是 ANSI/Postgres 形、**T-SQL 不收**——
      CTE 本體必須是 query expression ⇒ SQL Server 回 `Incorrect syntax near the
      keyword 'VALUES'`（156）＋`Statement(s) could not be prepared`（8180），
      整支 O3 在 live 直接 `ProgrammingError`（離線 selftest 只驗文本形狀、照不出）。
      合法形＝**表值建構式放在 `FROM`／`JOIN` 的括號內、帶別名與欄名清單**
      （`(VALUES (?,?),(?,?)) AS m (cls, kun)`）⇒ 本句與 `[O1b]` 同形：
      一句平的 `SELECT ... FROM #temp GROUP BY`，零 CTE、零相關子查詢。
    ★ `LEFT JOIN`（不是 `JOIN`）：窗內完全沒有列的綑仍回一列 `0`＝「量到 0」，
      與「綑不存在」語意分離（`build_kun_counts` 的 9b.2）。窗述詞掛在 `ON`、
      **不可移到 `WHERE`**（移了就把那些 0 濾掉、退化成 inner join）。
    """
    try:
        n_pairs = int(n_pairs)
    except (TypeError, ValueError):
        raise ValueError("n_pairs must be a positive integer")
    if n_pairs < 1:
        raise ValueError("n_pairs must be positive")
    vals = ",\n           ".join(
        f"(CAST(? AS nvarchar(128)) COLLATE {TEMP_COLLATE}, "
        f"CAST(? AS nvarchar(128)) COLLATE {TEMP_COLLATE})"
        for _ in range(n_pairs))
    return ("SELECT m.kun AS kun,\n"
            "       COUNT(DISTINCT t.op) AS n_now\n"
            "FROM   (VALUES\n           " + vals + ") AS m (cls, kun)\n"
            f"LEFT JOIN {OPR_TEMP} AS t\n"
            "       ON t.cls = m.cls\n"
            "      AND t.ts >= ?\n"
            "GROUP BY m.kun\n"
            "OPTION (RECOMPILE)")


def ALL_SQL():
    """本模組會送出的**全部 SQL 文本（含 DDL）**（selftest 形狀/負斷言的唯一清單）。

    新增任何查詢**必須**登記在這裡——否則 `stpPersonID` 零出現的負斷言會有漏網之魚。
    """
    # O3 的 VALUES 長度依 membership 而變；兩列是完整且具代表性的 SQL 形，
    # 每一個實際送出的版本只差重複的 parameter tuple，故負斷言仍完整覆蓋。
    return (_sql_guard(), _sql_create_counts(), _sql_insert_counts(), _sql_agg_counts(),
            _sql_stats_counts(), _sql_create_class(), _sql_insert_class(), _sql_agg_class(),
            _sql_agg_kun_counts(2))


def fetch_operator_counts(cur, cap, now_min=NOW_MIN, ref_h=REF_H, stats=None):
    """[O1] 近 `now_min` 分／`ref_h` 時 逐站不重複人數（＋線級去重哨兵列）。

    回 `[(cls, n_now, n_4h), ...]`，含一列 `(LINE_KEY, n_now, n_4h)`。
    ⚠ `cur` 必須與後續聚合同一條連線（`#temp` 存活於 connection 生命週期）。
    """
    cap = _dt(cap)
    now_min = max(1, int(now_min))
    ref_min = max(1, int(ref_h) * 60)
    scan_lo = cap - timedelta(minutes=max(now_min, ref_min))
    now_lo = cap - timedelta(minutes=now_min)
    ref_lo = cap - timedelta(minutes=ref_min)

    cur.execute(_sql_drop(OPR_TEMP))
    cur.execute(_sql_create_counts())                 # DDL 無參數＝外層 scope（#temp 存活）
    cur.execute(_sql_insert_counts(), scan_lo, cap)

    if stats is not None:
        cur.execute(_sql_stats_counts(), now_lo)
        r = cur.fetchone()
        if r is not None:
            stats["n_scan_rows"] = _int0(r[0])
            stats["n_scan_rows_now"] = _int0(r[1])
            stats["n_op_zero"] = _int0(r[2])
            stats["n_op_null"] = _int0(r[3])
            stats["n_cls_seen_win"] = _int0(r[4])
        stats["scan_lo"] = scan_lo
        stats["now_lo"] = now_lo
        stats["ref_lo"] = ref_lo

    cur.execute(_sql_agg_counts(), now_lo, ref_lo, now_lo, ref_lo)
    return [(str(r[0]), _int0(r[1]), _int0(r[2])) for r in cur.fetchall()]


def fetch_operator_kun_counts(cur, cap, memberships, now_min=NOW_MIN):
    """[O3] 近 `now_min` 分鐘按綑的 observed distinct 人數，回 ``[(kun, n_now)]``。

    ``memberships`` 是 ``[(cls, kun), ...]`` 的**非人員**對照表；由呼叫端依
    D-OP2 先剔除自動站，再以 bind parameters 送進 SQL 的 VALUES table。綑內
    SQL distinct、跨綑刻意不去重；Python 僅拿聚合後的 int，永不接觸 ID。

    ★ **母體＝ `[O1a]` 的同一支物化**（`#board_opr`）：drop → create（DDL 無參數＝外層
      scope）→ 無聚合 INSERT → 對 `#temp` 聚合。**這支不新增任何掃 view 的形**，
      窗也只有 `now_min` 一個（比 `fetch_operator_counts` 的 `max(now, ref)` 更窄）。
    ⚠ `cur` 必須與物化同一條連線（`#temp` 存活於 connection 生命週期）。
    """
    cap = _dt(cap)
    now_min = max(1, int(now_min))
    pairs, seen = [], set()
    for item in memberships or ():
        try:
            cls, kun = str(item[0]), str(item[1])
        except (IndexError, TypeError):
            continue
        if not cls or not kun or (cls, kun) in seen:
            continue
        seen.add((cls, kun))
        pairs.append((cls, kun))
    if not pairs:
        return []
    now_lo = cap - timedelta(minutes=now_min)
    cur.execute(_sql_drop(OPR_TEMP))
    cur.execute(_sql_create_counts())              # DDL 無參數＝外層 scope（#temp 存活）
    cur.execute(_sql_insert_counts(), now_lo, cap)
    params = [v for pair in pairs for v in pair]
    params.append(now_lo)
    cur.execute(_sql_agg_kun_counts(len(pairs)), *params)
    return [(str(r[0]), _int0(r[1])) for r in cur.fetchall()]


def fetch_auto_stats(cur, cap, class_d=CLASS_D, stats=None):
    """[O2] 近 `class_d` 天逐站 (d7_persons, d7_blocks, d7_rows)（自動站分類的唯一輸入）。

    回 `[(cls, d7_persons, d7_blocks, d7_rows), ...]`。**長 TTL 層**（見 `_sql_insert_class`）。
    """
    cap = _dt(cap)
    class_d = max(1, int(class_d))
    lo = cap - timedelta(days=class_d)

    cur.execute(_sql_drop(OPR7_TEMP))
    cur.execute(_sql_create_class())                 # DDL 無參數＝外層 scope（#temp 存活）
    cur.execute(_sql_insert_class(), cap, lo, cap)   # [O2a] 物化（無結果集）
    cur.execute(_sql_agg_class())                    # [O2b] 對 #temp 聚合
    rows = [(str(r[0]), _int0(r[1]), _int0(r[2]), _int0(r[3])) for r in cur.fetchall()]
    if stats is not None:
        stats["class_lo"] = lo
        stats["n_cls_seen_class"] = len(rows)
    return rows


# =============================================================================
# 4. 純函式層（canonical 組裝；selftest 主戰場、全部離線可測）
# =============================================================================
CANONICAL_KEYS = ("schema", "ok", "cap", "params", "stations", "line", "by_kun", "diag", "elapsed_s")
STATION_KEYS = ("n_now", "n_4h", "auto", "diag")
STATION_DIAG_KEYS = ("d7_persons", "d7_blocks", "d7_rows")
LINE_KEYS = ("n_now", "n_4h")


def build_kun_counts(rows, memberships):
    """聚合後的 ``[(kun, int)]`` → 完整綑鍵集（純函式、零識別值）。

    `0` 只代表 SQL 已在該綑成員集合內量到零筆；不跨綑併集是刻意的——一名作業員
    同時出現在兩綑時，兩綑各自都要看得到該觀察值。這裡絕不接收 station/person 明細。
    """
    out = {}
    for item in memberships or ():
        try:
            kun = str(item[1])
        except (IndexError, TypeError):
            continue
        if kun:
            out.setdefault(kun, 0)
    for item in rows or ():
        try:
            kun, n = str(item[0]), _int0(item[1])
        except (IndexError, TypeError):
            continue
        if kun in out:
            out[kun] = n
    return out


def build_operator(count_rows, auto_rows, cap, *, now_min=NOW_MIN, ref_h=REF_H,
                   class_d=CLASS_D, max_persons=AUTO_MAX_PERSONS,
                   min_blocks=AUTO_MIN_BLOCKS, route=None, stats=None, timing=None,
                   guard=None):
    """[O1]+[O2] 原始列 → canonical `board.operator.v1`（純函式；零 I/O、零識別值）。

    鍵集鐵則（消費端據此分辨三種「沒有數字」）：
      · **缺鍵** ＝ route 站近 `class_d` 天完全無列 ⇒ 「—」＋`dp_mp_nosig`。
      · `auto=True` ＝ 機器帳號站 ⇒ 「—」＋`dp_mp_auto`。
      · `n_now=0` ＝ **真的 0**（窗內無人操作紀錄）⇒ 淡色 0，**不是**「—」。
    """
    cap = _dt(cap)
    route_cls = _route_cls(route)
    route_set = set(route_cls)

    counts, line = {}, None
    n_dup_cls = 0
    for cls, n_now, n_ref in (count_rows or ()):
        if cls == LINE_KEY:
            line = (_int0(n_now), _int0(n_ref))
            continue
        if cls in counts:
            n_dup_cls += 1
        counts[cls] = (_int0(n_now), _int0(n_ref))

    auto_stats = {}
    for cls, p, b, n in (auto_rows or ()):
        if cls == LINE_KEY:
            continue
        auto_stats[cls] = (_int0(p), _int0(b), _int0(n))

    stations = {}
    n_auto = 0
    auto_list = []
    n_win_not_in_class = 0
    for cls in route_cls:                       # route 序（＝ord 序）＝輸出順序恆定
        has_class = cls in auto_stats
        has_win = cls in counts
        if not has_class and not has_win:
            continue                            # 缺鍵：7d 無列＝無訊號（**不是 0**）
        if not has_class:
            # 分類層窗內沒看到、counts 窗內卻有列（極罕見：class_d 極短或跨窗邊界）。
            # 照樣出鍵（它確實有活動），d7_* 記 0 ⇒ classify 天然回 False（不亂扣自動站）。
            n_win_not_in_class += 1
        p, b, n = auto_stats.get(cls, (0, 0, 0))
        n_now, n_ref = counts.get(cls, (0, 0))
        auto = classify_auto_station(p, b, max_persons=max_persons, min_blocks=min_blocks)
        if auto:
            n_auto += 1
            auto_list.append(cls)
        stations[cls] = {
            "n_now": n_now,
            "n_4h": n_ref,
            "auto": bool(auto),
            "diag": {"d7_persons": p, "d7_blocks": b, "d7_rows": n},
        }

    st = dict(stats or {})
    line_n = line if line is not None else (0, 0)
    sum_now = sum(v["n_now"] for v in stations.values())
    sum_ref = sum(v["n_4h"] for v in stations.values())

    caveats = []
    degraded = False
    if not auto_stats:
        degraded = True
        caveats.append("classification window returned no rows -> every station keyless "
                       "(consumer shows '—' for the whole column, which is honest but "
                       "usually means the window/connection is wrong, not that the line is idle)")
    if line is None:
        caveats.append("line sentinel row missing -> line reported as 0 "
                       "(should be impossible: the UNION ALL branch returns a row even "
                       "for an empty #temp)")
    if n_dup_cls:
        caveats.append(f"duplicate cls rows in counts result: {n_dup_cls} (last wins)")
    if n_win_not_in_class:
        caveats.append(f"{n_win_not_in_class} station(s) present in the counts window but "
                       f"absent from the {class_d}d classification window")

    diag = {
        "person_col": PERSON_COL,
        "caliber": CALIBER_OPR,
        "n_scan_rows": st.get("n_scan_rows"),
        "n_scan_rows_now": st.get("n_scan_rows_now"),
        "n_op_zero": st.get("n_op_zero"),
        "n_op_null": st.get("n_op_null"),
        "n_cls_seen_win": st.get("n_cls_seen_win", len(counts)),
        "n_cls_seen_class": len(auto_stats),
        "n_cls_route": len(route_cls),
        "n_stations_out": len(stations),
        "n_keyless_route_cls": len(route_set) - len(stations),
        "n_offroute_cls_seen": len([c for c in auto_stats if c not in route_set]),
        "n_auto": n_auto,
        "auto_stations": auto_list,
        "sum_station_n_now": sum_now,
        "sum_station_n_4h": sum_ref,
        # D-OP4 自揭：line 的母體＝掃描窗內**全部**站（含 route 外與 auto 站的機器帳號）
        # ⇒ 它是上界、與 per-station 相加不相等屬預期（消費面 footer `dp_mp_line_note`）。
        "line_scope": "all stations in scan window (superset of route; includes auto/machine "
                      "accounts) -> upper bound, deduplicated in SQL",
        "blk_expr": f"DATEDIFF(MINUTE, ts, cap) / {BLOCK_MIN}",
        "auto_rule": (f"d7_persons <= {int(max_persons)} AND d7_blocks >= {int(min_blocks)}"),
        "windows": {"scan_lo": st.get("scan_lo"), "now_lo": st.get("now_lo"),
                    "ref_lo": st.get("ref_lo"), "class_lo": st.get("class_lo")},
        "column_guard": guard,
        "timing": timing or {},
        "degraded": degraded,
        "caveats": caveats,
    }

    return {
        "schema": SCHEMA_ID,
        "ok": True,
        "cap": cap.isoformat(),
        "params": {"now_min": int(now_min), "ref_h": int(ref_h), "class_d": int(class_d),
                   "auto_max_persons": int(max_persons), "auto_min_blocks": int(min_blocks)},
        "stations": stations,
        "line": {"n_now": line_n[0], "n_4h": line_n[1]},
        # additive v1 field: the ordinary per-station path has no O3 material;
        # the landing warmer fills it with `build_kun_counts` instead.
        "by_kun": {},
        "diag": diag,
        "elapsed_s": None,
    }


# =============================================================================
# 5. I/O 編排
# =============================================================================
def compute_operator(cur, cap=None, *, now_min=NOW_MIN, ref_h=REF_H, class_d=CLASS_D,
                     max_persons=AUTO_MAX_PERSONS, min_blocks=AUTO_MIN_BLOCKS, route=None):
    """唯一 I/O 編排點：guard → [O1] → [O2] → `build_operator`。

    · `cap=None` ⇒ `SELECT GETDATE()` **一次、全程沿用**（as-of now；絕不中途重取 now，
      否則 now 窗、4h 窗、7d 分類窗會互相錯位）。
    · 例外一律**往上拋**（`SchemaDivergence`／連線錯／逾時）——`board_data` 接成
      `operator_unavailable` ⇒ 人力欄整欄「—」＋`dp_mp_off`，**絕不吞成一盤 0**。
    · ⚠ `cur` 必須是同一條連線的 cursor（兩張 `#temp` 存活於 connection 生命週期）。
    """
    t_all = time.perf_counter()
    timing, st = {}, {}

    t0 = time.perf_counter()
    guard = fetch_operator_guard(cur)
    timing["guard_ms"] = int((time.perf_counter() - t0) * 1000)

    if cap is None:
        cur.execute("SELECT GETDATE()")
        cap = cur.fetchone()[0]
    cap = _dt(cap)

    t0 = time.perf_counter()
    count_rows = fetch_operator_counts(cur, cap, now_min=now_min, ref_h=ref_h, stats=st)
    timing["o1_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    auto_rows = fetch_auto_stats(cur, cap, class_d=class_d, stats=st)
    timing["o2_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    out = build_operator(count_rows, auto_rows, cap, now_min=now_min, ref_h=ref_h,
                         class_d=class_d, max_persons=max_persons, min_blocks=min_blocks,
                         route=route, stats=st, timing=timing, guard=guard)
    timing["derive_ms"] = int((time.perf_counter() - t0) * 1000)
    timing["total_ms"] = sum(v for k, v in timing.items()
                             if k.endswith("_ms") and k != "total_ms")
    out["diag"]["timing"] = timing
    out["elapsed_s"] = round(time.perf_counter() - t_all, 3)
    return out


def run(cap=None, now_min=NOW_MIN, ref_h=REF_H, class_d=CLASS_D,
        max_persons=AUTO_MAX_PERSONS, min_blocks=AUTO_MIN_BLOCKS,
        route=None, timeout=QUERY_TIMEOUT_SEC):
    """CLI/snapshot 用的 live 編排（開一條連線、跑完關掉）。回 canonical。"""
    conn, cur = _connect(timeout=timeout)
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        out = compute_operator(cur, cap if cap is not None else now, now_min=now_min,
                               ref_h=ref_h, class_d=class_d, max_persons=max_persons,
                               min_blocks=min_blocks, route=route)
        out["mes_now"] = now.isoformat() if isinstance(now, datetime) else str(now)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =============================================================================
# 6. 呈現（CLI；**零人名**——本模組手上根本沒有人名可印）
# =============================================================================
def render(out):
    d, p = out["diag"], out["params"]
    print(f"=== MES operator headcount（{out['schema']}）　cap={out['cap']} ===")
    print(f"  口徑＝{d['caliber']}")
    print(f"  參數＝now {p['now_min']}min／ref {p['ref_h']}h／分類 {p['class_d']}d"
          f"｜自動站規則 {d['auto_rule']}")
    if d["degraded"]:
        print("  ⚠ DEGRADED：分類層零列，以下鍵集不可信（見 diag.caveats）。")
    print("\n  ── 逐站（route 序；「—」＝不可測，非 0；缺列＝近 "
          f"{p['class_d']}d 無訊號）──")
    print(f"    {'站':<50}{'now':>6}{'4h':>6}   {'d7人':>5}{'d7塊':>6}{'d7列':>8}")
    print("    " + "-" * 84)
    for cls, s in out["stations"].items():
        val = "—" if s["auto"] else f"{s['n_now']}"
        ref = "—" if s["auto"] else f"{s['n_4h']}"
        tag = " ·自動站" if s["auto"] else ""
        print(f"    {(cls + tag)[:49]:<50}{val:>6}{ref:>6}   "
              f"{s['diag']['d7_persons']:>5}{s['diag']['d7_blocks']:>6}"
              f"{s['diag']['d7_rows']:>8}")
    ln = out["line"]
    print(f"\n  ── 線級（SQL 去重；**與逐站相加不相等屬預期**）──")
    print(f"    now {ln['n_now']}／4h {ln['n_4h']}　"
          f"（逐站相加＝{d['sum_station_n_now']}／{d['sum_station_n_4h']}）")
    print(f"    ⚠ {d['line_scope']}")
    print(f"\n  ── diag ──")
    for k in ("person_col", "n_scan_rows", "n_scan_rows_now", "n_op_zero", "n_op_null",
              "n_cls_seen_win", "n_cls_seen_class", "n_cls_route", "n_stations_out",
              "n_keyless_route_cls", "n_offroute_cls_seen", "n_auto", "auto_stations",
              "blk_expr", "windows", "timing", "degraded", "caveats"):
        print(f"    {k:<22}{d.get(k)}")
    if out.get("elapsed_s") is not None:
        print(f"    {'elapsed_s':<22}{out['elapsed_s']}")


# =============================================================================
# 7. CLI
# =============================================================================
def _hint(e):
    low = str(e).lower()
    if isinstance(e, SchemaDivergence) or "schema divergence" in low or "invalid column" in low:
        return (" → **schema 分歧**（報表 view 少了承重欄）。不是連線問題、也不是現場沒人；"
                "先對 SPEC-DISPATCH.md §9.3 重新校 [G0] 欄位守衛，勿改數字。")
    if any(k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")):
        return " → 連不到 MES。確認已連 VPN/內網。"
    if "login failed" in low or "28000" in str(e):
        return " → 登入被拒。確認 AD 帳號對報表庫有讀權。"
    return ""


def main():
    ap = argparse.ArgumentParser(
        description="Tendline Board MES operator headcount（人數 only、永不記名；唯讀）")
    ap.add_argument("--cap", default=None,
                    help="as-of 上界（YYYY-MM-DD[THH:MM]）；未給＝GETDATE()")
    ap.add_argument("--now-min", type=int, default=NOW_MIN, help=f"主值窗（分鐘；預設 {NOW_MIN}）")
    ap.add_argument("--ref-h", type=int, default=REF_H, help=f"參考值窗（小時；預設 {REF_H}）")
    ap.add_argument("--class-d", type=int, default=CLASS_D, help=f"分類窗（天；預設 {CLASS_D}）")
    ap.add_argument("--auto-max-persons", type=int, default=AUTO_MAX_PERSONS)
    ap.add_argument("--auto-min-blocks", type=int, default=AUTO_MIN_BLOCKS)
    ap.add_argument("--json", action="store_true", help="輸出 canonical JSON")
    ap.add_argument("--selftest", action="store_true", help="離線確定性單測（不連 MES）")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    try:
        out = run(cap=(_dt(args.cap) if args.cap else None), now_min=args.now_min,
                  ref_h=args.ref_h, class_d=args.class_d,
                  max_persons=args.auto_max_persons, min_blocks=args.auto_min_blocks)
    except Exception as e:
        print(f"[mes_operator] 查詢失敗: {type(e).__name__}{_hint(e)}\n  {str(e)[:300]}")
        sys.exit(2)

    if args.json:
        import json
        print(json.dumps(out, ensure_ascii=False, default=str))
        return
    render(out)


# =============================================================================
# 8. 離線單測（純合成資料、零 MES、零網路；SPEC-DISPATCH §12 離線清單逐項）
# =============================================================================
class _FakeCur:
    """離線假 cursor：讓 `fetch_*`／`compute_operator` 整條 I/O 編排在**零網路**下可測。

    只認得本模組真正會送出的 SQL（guard／GETDATE／[O1a/b/c]／[O2a/b]／O3 綑級 aggregate）＋ `#temp` DDL。
    `sql_log` 保留全部文本與參數＝形狀/參數/負斷言的證據來源。
    """

    def __init__(self, guard_row=(1, 1, 1, 1), count_rows=(), auto_rows=(), kun_rows=(),
                 stats_row=(0, 0, 0, 0, 0), now=None):
        self.guard_row = tuple(guard_row)
        self.count_rows = list(count_rows)
        self.auto_rows = list(auto_rows)
        self.kun_rows = list(kun_rows)
        self.stats_row = tuple(stats_row)
        self.now = now
        self.sql_log = []
        self._next = []

    def execute(self, sql, *params):
        self.sql_log.append((sql, params))
        s = sql.strip()
        if s.startswith("SELECT CASE WHEN OBJECT_ID"):
            self._next = [self.guard_row]
        elif s.startswith("SELECT GETDATE()"):
            self._next = [(self.now,)]
        elif s.startswith("CREATE TABLE") or s.startswith("INSERT INTO"):
            self._next = []                       # DDL／物化 INSERT：無結果集
        elif s.startswith("SELECT COUNT(*) AS n_rows"):
            self._next = [self.stats_row]
        elif f"FROM   {OPR7_TEMP}" in s:
            self._next = [tuple(r) for r in self.auto_rows]
        elif "AS m (cls, kun)" in s:
            self._next = [tuple(r) for r in self.kun_rows]
        elif f"FROM   {OPR_TEMP}" in s:
            self._next = [tuple(r) for r in self.count_rows]
        else:
            self._next = []                       # #temp DDL 等
        return self

    def fetchone(self):
        return self._next[0] if self._next else None

    def fetchall(self):
        return list(self._next)


def _selftest():
    print("=== mes_operator --selftest（人數口徑確定性；純離線合成資料、零人名）===")
    from mes_qlik import _T                       # 斷言 harness 重用（不另抄一份）
    t = _T()

    DA = "segDiePlacement"                            # ord 10（route 內、人工站）
    CAL = "segCalibrationTest"                # ord 32（route 內、人工站）
    WB = "segWireBonding"                             # ord 16（route 內）
    FA = "segFlexBonding"                           # ord 14（route 內、R4 實證 auto）
    SER = "segSerialisation"              # ord 1（route 內、R4 實證 auto）
    QAC = "segQASetup"                     # ord 39（route 內）
    SUBSTRATE = "segSubstrateReceiveSupplierData"          # **不在 route**（R4：大量列僅 1 人）
    CAP = datetime(2026, 8, 1, 10, 30, 0)

    # ---- 0. 常數與 pin 健全性（接錯＝全部作廢）--------------------------------
    t.eq(SCHEMA_ID, "board.operator.v1", "0.1 schema pin")
    t.eq(PERSON_COL, "OperatorId", "0.2 person key pin＝OperatorId（數字 ID）")
    t.eq((NOW_MIN, REF_H, CLASS_D), (60, 4, 7), "0.3 窗預設＝60min/4h/7d")
    t.eq((AUTO_MAX_PERSONS, AUTO_MIN_BLOCKS), (2, 12), "0.4 自動站門檻預設 2/12")
    t.eq(BLOCK_MIN, 240, "0.5 4h 塊＝240 分鐘")
    t.eq(LINE_KEY, "__line__", "0.6 線級哨兵鍵")
    t.eq(len(_route_cls(None)), 43, "0.7 route 預設＝QLIK_STATIONS 43 站（import 重用）")
    t.eq(_route_cls(None)[0], SER, "0.8 route 序＝ord 序（ord1）")
    t.eq(_route_cls(None)[31], CAL, "0.9 ord32＝CAL")
    t.ok(SUBSTRATE not in _route_cls(None), "0.10 SubstrateReceive 不在 43 站 route")
    t.eq(_route_cls([DA, CAL, DA]), (DA, CAL), "0.11 route 接 cls 字串序列＋去重")
    t.eq(_route_cls({CAL, DA}), tuple(sorted((CAL, DA))),
         "0.12 set 輸入 → 字典序（輸出順序恆定、不隨 hash 漂移）")
    t.eq(_route_cls([(10, DA, "assyModule")]), (DA,), "0.13 route 接 (ord,cls,mcl) 形")
    t.eq(_int0(None), 0, "0.14 _int0 None→0")
    t.eq(_int0("3"), 3, "0.15 _int0 字串→int")

    # ---- 1. classify_auto_station 四象限＋邊界（D-OP2；門檻誤判＝整站顯錯）-------
    t.ok(classify_auto_station(1, 42) is True, "1.1 少人×高密度＝自動站")
    t.ok(classify_auto_station(2, 12) is True, "1.2 persons 恰 2＋blocks 恰 12 ⇒ True（含等於）")
    t.ok(classify_auto_station(2, 11) is False, "1.3 blocks 門檻−1 ⇒ False")
    t.ok(classify_auto_station(3, 12) is False, "1.4 persons 門檻+1 ⇒ False")
    t.ok(classify_auto_station(3, 42) is False, "1.5 多人×高密度＝人工站")
    t.ok(classify_auto_station(1, 3) is False, "1.6 少人×低密度＝低量人工站（**不可標自動站**）")
    t.ok(classify_auto_station(9, 14) is False, "1.7 R4 實例：9 人 14 塊＝人工站")
    t.ok(classify_auto_station(0, 20) is True, "1.8 0 人×高密度＝完全不記名 ⇒ 亦顯「—」")
    t.ok(classify_auto_station(0, 1) is False, "1.9 0 人×低密度 ⇒ 不主張自動站")
    t.ok(classify_auto_station(None, 42) is False, "1.10 d7_persons None ⇒ False（不可知不扣帽）")
    t.ok(classify_auto_station(1, None) is False, "1.11 d7_blocks None ⇒ False")
    t.ok(classify_auto_station("x", 42) is False, "1.12 壞值 ⇒ False（不炸）")
    # env 門檻可調（門檻誤判走 env、不改碼）
    t.ok(classify_auto_station(5, 6, max_persons=5, min_blocks=6) is True, "1.13 env 門檻恰好")
    t.ok(classify_auto_station(6, 6, max_persons=5, min_blocks=6) is False, "1.14 env persons+1")
    t.ok(classify_auto_station(5, 5, max_persons=5, min_blocks=6) is False, "1.15 env blocks−1")
    t.ok(classify_auto_station(3, 12, max_persons=1) is False, "1.16 收緊 max_persons")

    # ---- 2. SQL 形狀＋★人名負斷言（§24；漏一條就是紅線破口）----------------------
    sqls = ALL_SQL()
    t.eq(len(sqls), 9, "2.1 ALL_SQL 登記 9 條（新增查詢必須登記；含 O3 綑級查詢）")
    for i, s in enumerate(sqls):
        if not s.lstrip().startswith("CREATE TABLE"):
            t.ok(s.rstrip().endswith("OPTION (RECOMPILE)"), f"2.2.{i} 尾 RECOMPILE（DDL 除外）")
        t.ok("stpPersonID" not in s, f"2.3.{i} ★ stpPersonID 零出現（負斷言）")
        t.ok("Operator.Name" not in s, f"2.3b.{i} ★ Operator.Name 零出現（負斷言）")
        t.ok(" IN (" not in s, f"2.4.{i} 無動態 IN-list（B4 鐵則）")
        # 「無 join」的承重是 **B4 鐵則：大 view 只做無 join 的窄窗掃描**。
        # `[O3b]` 的 `LEFT JOIN #board_opr`（另一側＝2 欄的 bind `VALUES` 表值建構式、
        # 零 view）是**唯一**例外，且逐字釘死＝不會悄悄放行對 view 的 join。
        if "process_segment_v" in s:
            t.ok("JOIN" not in s.upper(), f"2.5.{i} ★ 掃 view 的句子零 join（B4 鐵則）")
        else:
            t.ok("JOIN" not in s.upper()
                 or (s.upper().count("JOIN") == 1 and f"LEFT JOIN {OPR_TEMP} AS t" in s),
                 f"2.5.{i} #temp 聚合唯一允許的 join＝O3 的 LEFT JOIN {OPR_TEMP}"
                 "（另一側是 bind VALUES 成員表，不是 view）")
        t.ok("stpEndTime" not in s, f"2.6.{i} 不碰 stpEndTime（零工期段教訓）")
    all_text = "\n".join(sqls) + "\n".join(_sql_drop(x) for x in (OPR_TEMP, OPR7_TEMP))
    t.eq(all_text.count("stpPersonID"), 0, "2.7 ★ 全 SQL 文本（含 DDL）零 stpPersonID")
    t.eq(all_text.count("Operator.Name"), 0, "2.7b ★ 全 SQL 文本零 Operator.Name")
    t.eq(all_text.count(PERSON_COL), 5, "2.8 OperatorId 只出現在 guard×1＋兩物化的三個運算式"
                                        "（O3 讀的是 #temp 的 `op` 欄，不再多一次）")
    # 物化層＝CREATE（無參數＝外層 scope）＋ INSERT ... SELECT（2026-08-01 scope 事故的修）：
    # 單一 view、無聚合（blocking aggregate 禁令）、絕不用 SELECT ... INTO
    for name, s in (("counts", _sql_insert_counts()), ("class", _sql_insert_class())):
        t.ok("dbo.process_segment_v" in s, f"2.9.{name} 掃描單一 view")
        t.ok("GROUP BY" not in s, f"2.10.{name} view 上無 GROUP BY")
        t.ok("DISTINCT" not in s, f"2.11.{name} view 上無 DISTINCT")
        t.ok(s.startswith("INSERT INTO") and "SELECT" in s and "INTO   " not in s,
             f"2.12.{name} ★ INSERT...SELECT、絕非 SELECT INTO（pyodbc prepare scope 事故）")
        t.ok(TEMP_COLLATE in s, f"2.13.{name} COLLATE 掛 #temp 側")
    for name, s in (("counts", _sql_create_counts()), ("class", _sql_create_class())):
        t.ok(s.startswith(f"CREATE TABLE {OPR_TEMP if name == 'counts' else OPR7_TEMP}"),
             f"2.12b.{name} CREATE TABLE 建對表")
        t.ok("?" not in s, f"2.12c.{name} ★ DDL 零參數（帶參數＝prepare scope、#temp 陪葬）")
        t.eq(s.count(TEMP_COLLATE), 2, f"2.12d.{name} 欄定義釘 collation（cls＋op）")
    # 聚合層：只碰 #temp、不碰 view
    for name, s in (("counts", _sql_agg_counts()), ("class", _sql_agg_class()),
                    ("stats", _sql_stats_counts()), ("kun", _sql_agg_kun_counts(2))):
        t.ok("process_segment_v" not in s, f"2.14.{name} 聚合不碰大 view")
        t.ok(s.count("#board_opr") >= 1, f"2.15.{name} 聚合在 #temp 上")
    t.eq(_sql_agg_counts().count("COUNT(DISTINCT"), 4,
         "2.16 兩窗×(per-cls＋線級)＝4 個 SQL 端 COUNT(DISTINCT)（識別值不進 Python）")
    t.ok(f"N'{LINE_KEY}'" in _sql_agg_counts(), "2.17 線級哨兵列在 SQL 內")
    t.ok(f"N'{LINE_KEY}' COLLATE {TEMP_COLLATE}" in _sql_agg_counts(),
         "2.17b ★ UNION 的字面值與 #temp 欄同 collation（否則整條 collation conflict）")
    for name, s in (("counts", _sql_insert_counts()), ("class", _sql_insert_class())):
        t.eq(s.count(TEMP_COLLATE), 2, f"2.17c.{name} cls 與 op 兩運算式仍帶 collation（雙保險）")
    t.ok(_sql_agg_counts().count("CASE WHEN ts >= ?") == 4,
         "2.18 兩窗都明寫述詞（不靠掃描窗隱含＝env 誤配不會偷換語意）")
    t.ok(f"/ {BLOCK_MIN}" in _sql_insert_class() and "DATEDIFF(MINUTE" in _sql_insert_class(),
         "2.19 塊號用分鐘除法（非 DATEDIFF(HOUR)/4＝整點跨越 artifact）")
    _o3sql = _sql_agg_kun_counts(2)
    t.ok("VALUES" in _o3sql and _o3sql.count("?") == 5,
         "2.19b O3 membership 以 parameter VALUES table 傳入（兩組 (cls,kun)＋一個窗下界）")
    t.eq(_o3sql.count("COUNT(DISTINCT"), 1,
         "2.19c O3 只做一次 SQL 端 COUNT(DISTINCT)（識別值不進 Python）")
    t.ok("COUNT(DISTINCT t.op)" in _o3sql and "GROUP BY m.kun" in _o3sql,
         "2.19d O3 綑內 SQL 去重（母體＝#temp 的 op 欄，0/空字串在物化時已成 NULL）")
    t.ok(PERSON_COL not in _o3sql and "process_segment_v" not in _o3sql,
         "2.19e ★ O3 是純 #temp 聚合：不碰大 view、文本連 OperatorId 都沒有"
         "（blocking aggregate 禁令；MES perf saga 的形）")
    t.ok(_sql_agg_kun_counts(1).count("?") == 3 and _sql_agg_kun_counts(7).count("?") == 15,
         "2.19f O3 參數數＝2×成員數＋1（VALUES 長度隨 membership 變、其餘逐字相同）")
    t.ok(_sql_drop(OPR_TEMP).startswith("IF OBJECT_ID('tempdb..#board_opr')"), "2.20 #temp 防呆 DDL")
    try:
        _sql_drop("#bad; DROP TABLE x --")
        t.ok(False, "2.21 _sql_drop 應拒絕非識別字")
    except ValueError:
        t.ok(True, "2.21 _sql_drop 拒絕非識別字")
    try:
        _op_expr("s; --")
        t.ok(False, "2.22 _op_expr alias 應驗證")
    except ValueError:
        t.ok(True, "2.22 _op_expr alias 驗證")

    # ---- 3. guard（承重欄 ABSENT ⇒ 具名停下，不吐一盤 0）------------------------
    t.eq(GUARD_KEYS, ("view_seg", "SegmentClassID", "stpStartTime", "OperatorId"),
         "3.1 guard 鍵集")
    t.ok(assert_operator_guard({k: True for k in GUARD_KEYS}), "3.2 全在 ⇒ 通過")
    for miss in GUARD_KEYS:
        g = {k: True for k in GUARD_KEYS}
        g[miss] = False
        try:
            assert_operator_guard(g)
            t.ok(False, f"3.3.{miss} 應 raise SchemaDivergence")
        except SchemaDivergence as e:
            t.ok(miss in str(e) or "process_segment_v" in str(e),
                 f"3.3.{miss} SchemaDivergence 逐欄具名")

    # ---- 4. canonical 形狀＋「缺鍵 ≠ 0」語意 ------------------------------------
    counts4 = [(DA, 3, 5), (CAL, 0, 2), (FA, 1, 1), (SUBSTRATE, 1, 1), (LINE_KEY, 4, 7)]
    auto4 = [(DA, 9, 14, 1234), (CAL, 11, 20, 900), (FA, 1, 40, 5000),
             (SUBSTRATE, 1, 42, 4900), (QAC, 4, 6, 80)]
    o4 = build_operator(counts4, auto4, CAP)
    t.eq(tuple(o4.keys()), CANONICAL_KEYS, "4.1 canonical 鍵全集（順序即 pin）")
    t.eq(o4["schema"], SCHEMA_ID, "4.2 schema")
    t.ok(o4["ok"] is True, "4.3 ok=True")
    t.eq(o4["cap"], "2026-08-01T10:30:00", "4.4 cap＝naive 台北牆鐘 isoformat")
    t.eq(o4["params"], {"now_min": 60, "ref_h": 4, "class_d": 7,
                        "auto_max_persons": 2, "auto_min_blocks": 12}, "4.5 params 形狀")
    t.eq(o4["by_kun"], {}, "4.5b canonical v1 有 additive by_kun（station path 不填）")
    t.eq(tuple(o4["line"].keys()), LINE_KEYS, "4.6 line 鍵集")
    for cls, s in o4["stations"].items():
        t.eq(tuple(s.keys()), STATION_KEYS, f"4.7.{cls} station 鍵集")
        t.eq(tuple(s["diag"].keys()), STATION_DIAG_KEYS, f"4.8.{cls} station diag 鍵集")
        t.ok(isinstance(s["n_now"], int) and isinstance(s["n_4h"], int),
             f"4.9.{cls} 人數欄是 int（沒有 None、沒有 float）")
        t.ok(isinstance(s["auto"], bool), f"4.10.{cls} auto 是 bool")
    # 缺鍵語意：route 站 7d 無列 ⇒ **不出鍵**（≠ 0）
    t.ok(WB not in o4["stations"], "4.11 7d 無列的 route 站＝缺鍵（消費面「—」、非 0）")
    t.ok(QAC in o4["stations"], "4.12 7d 有列但窗內零列 ⇒ 出鍵")
    t.eq((o4["stations"][QAC]["n_now"], o4["stations"][QAC]["n_4h"]), (0, 0),
         "4.13 ★ 窗內零列＝真 0（與缺鍵語意不同、絕不互相冒充）")
    t.eq(o4["stations"][CAL]["n_now"], 0, "4.14 counts 給 0 就是 0")
    t.eq(o4["stations"][DA]["n_4h"], 5, "4.15 n_4h 取第三欄")
    t.eq(o4["diag"]["n_keyless_route_cls"], 43 - len(o4["stations"]), "4.16 缺鍵站數自揭")
    # route 交集：非 route 站不出鍵（但仍計入 line＝上界，已自揭）
    t.ok(SUBSTRATE not in o4["stations"], "4.17 ★ 非 route 站不出鍵（route 交集在 Python）")
    t.eq(o4["diag"]["n_offroute_cls_seen"], 1, "4.18 route 外站數自揭")
    # 站序＝route 序（消費面表格不跳動）
    t.eq(list(o4["stations"].keys()), [c for c in _route_cls(None) if c in o4["stations"]],
         "4.19 stations 依 route(ord) 序")

    # ---- 5. 自動站落地（D-OP2；R4 實證形狀）------------------------------------
    t.ok(o4["stations"][FA]["auto"] is True, "5.1 1 人×40 塊 ⇒ auto=True（機器帳號站）")
    t.ok(o4["stations"][DA]["auto"] is False, "5.2 9 人×14 塊 ⇒ 人工站")
    t.ok(o4["stations"][QAC]["auto"] is False, "5.3 4 人×6 塊（低量）⇒ 人工站")
    t.eq(o4["stations"][FA]["diag"], {"d7_persons": 1, "d7_blocks": 40, "d7_rows": 5000},
         "5.4 分類依據隨值回傳（?-hover 可自行驗算）")
    t.eq(o4["diag"]["n_auto"], 1, "5.5 auto 站數")
    t.eq(o4["diag"]["auto_stations"], [FA], "5.6 auto 站清單")
    t.ok("d7_persons <= 2 AND d7_blocks >= 12" in o4["diag"]["auto_rule"], "5.7 規則字串自揭")
    # ★ auto 站的 n_now 仍照實回（消費面自己決定顯「—」）＝不在資料層抹掉證據
    t.eq(o4["stations"][FA]["n_now"], 1, "5.8 auto 站計數照實回（顯示層才轉「—」）")
    # env 調門檻 ⇒ 分類跟著動、不改碼
    o5 = build_operator(counts4, auto4, CAP, max_persons=9, min_blocks=14)
    t.ok(o5["stations"][DA]["auto"] is True, "5.9 放寬門檻 ⇒ DA 被歸自動站（env 可調）")
    t.eq(o5["params"]["auto_max_persons"], 9, "5.10 params 反映實際門檻")

    # ---- 6. 線級去重（D-OP4）----------------------------------------------------
    t.eq(o4["line"], {"n_now": 4, "n_4h": 7}, "6.1 線級取哨兵列")
    t.ok(o4["line"]["n_now"] <= o4["diag"]["sum_station_n_now"] + 1, "6.2 線級 ≤ 逐站和＋route 外")
    # 純 route 母體下的嚴格不變式：line ≤ Σ站（同一人跨站被重複計在各站、線級去重）
    counts6 = [(DA, 3, 4), (CAL, 2, 3), (LINE_KEY, 4, 5)]
    auto6 = [(DA, 5, 20, 10), (CAL, 5, 20, 10)]
    o6 = build_operator(counts6, auto6, CAP)
    t.ok(o6["line"]["n_now"] <= sum(s["n_now"] for s in o6["stations"].values()),
         "6.3 ★ line ≤ Σ站（去重不變式；純 route 母體）")
    t.ok(o6["line"]["n_4h"] <= sum(s["n_4h"] for s in o6["stations"].values()),
         "6.4 ★ 4h 同不變式")
    t.ok(o6["line"]["n_now"] < sum(s["n_now"] for s in o6["stations"].values()),
         "6.5 跨站作業 ⇒ 嚴格小於（相加會重複計人＝D-OP4 的理由）")
    t.ok(isinstance(o6["line"]["n_now"], int), "6.6 line 是 int")
    t.ok("upper bound" in o6["diag"]["line_scope"], "6.7 line 母體＝掃描窗全站（上界）自揭")
    # 哨兵列缺席（理論上不可能）⇒ 0＋caveat，不炸
    o6b = build_operator([(DA, 1, 1)], auto6, CAP)
    t.eq(o6b["line"], {"n_now": 0, "n_4h": 0}, "6.8 哨兵缺席 ⇒ line 0")
    t.ok(any("line sentinel" in c for c in o6b["diag"]["caveats"]), "6.9 哨兵缺席大聲自揭")
    t.ok(LINE_KEY not in o6["stations"], "6.10 ★ 哨兵鍵永不被當成站名輸出")
    t.ok(LINE_KEY not in build_operator(counts6, list(auto6) + [(LINE_KEY, 1, 1, 1)],
                                        CAP)["stations"],
         "6.11 分類層若冒出哨兵鍵亦丟棄")

    # ---- 7. 空盤／degraded（分類層零列＝整欄「—」而非一盤 0）---------------------
    o7 = build_operator([(LINE_KEY, 0, 0)], [], CAP)
    t.eq(o7["stations"], {}, "7.1 分類層零列 ⇒ 零鍵（全部「—」）")
    t.ok(o7["diag"]["degraded"] is True, "7.2 degraded=True")
    t.ok(any("keyless" in c for c in o7["diag"]["caveats"]), "7.3 caveat 具名")
    t.eq(o7["line"], {"n_now": 0, "n_4h": 0}, "7.4 line 0")
    o7b = build_operator([], [(DA, 3, 20, 5)], CAP)
    t.eq((o7b["stations"][DA]["n_now"], o7b["stations"][DA]["n_4h"]), (0, 0),
         "7.5 counts 層零列＋7d 有列 ⇒ 真 0（出鍵）")
    t.ok(o7b["diag"]["degraded"] is False, "7.6 counts 層零列不算 degraded（夜班冷清屬正常）")

    # ---- 8. 端到端 compute_operator（_FakeCur；零網路）＋參數窗邊界 --------------
    cur = _FakeCur(guard_row=(1, 1, 1, 1),
                   count_rows=[(DA, 3, 5), (FA, 1, 1), (LINE_KEY, 3, 6)],
                   auto_rows=[(DA, 9, 14, 1234), (FA, 1, 40, 5000)],
                   stats_row=(3400, 900, 0, 0, 41), now=CAP)
    o8 = compute_operator(cur, cap=None)
    t.eq(o8["cap"], "2026-08-01T10:30:00", "8.1 cap=None ⇒ 取 GETDATE() 一次全程沿用")
    t.eq(sum(1 for s, _p in cur.sql_log if s.strip().startswith("SELECT GETDATE()")), 1,
         "8.2 ★ GETDATE 只取一次（絕不中途重取 now）")
    t.eq(o8["line"], {"n_now": 3, "n_4h": 6}, "8.3 端到端線級")
    t.eq(o8["stations"][DA]["n_now"], 3, "8.4 端到端逐站")
    t.ok(o8["stations"][FA]["auto"] is True, "8.5 端到端 auto 分類")
    t.eq(o8["diag"]["n_scan_rows"], 3400, "8.6 diag 掃描列數")
    t.eq((o8["diag"]["n_op_zero"], o8["diag"]["n_op_null"]), (0, 0), "8.7 0-sentinel/NULL 自揭")
    t.ok(o8["elapsed_s"] is not None and o8["diag"]["timing"]["total_ms"] >= 0, "8.8 計時就緒")
    t.ok(o8["diag"]["column_guard"]["view_seg"] is True, "8.9 guard 進 diag")
    # 送出的 SQL 逐條檢查：零人名、兩張 #temp 都有先 DROP 防呆
    log_text = "\n".join(s for s, _p in cur.sql_log)
    t.eq(log_text.count("stpPersonID"), 0, "8.10 ★ 端到端送出的 SQL 零 stpPersonID")
    t.ok(f"IF OBJECT_ID('tempdb..{OPR_TEMP}')" in log_text
         and f"IF OBJECT_ID('tempdb..{OPR7_TEMP}')" in log_text, "8.11 兩張 #temp 皆防呆 DROP")
    # 窗邊界（掃描窗＝max(now,ref)；兩個述詞窗各自正確）
    scan_params = [p for s, p in cur.sql_log if s.startswith(f"INSERT INTO {OPR_TEMP} ")][0]
    t.eq(scan_params, (CAP - timedelta(hours=4), CAP), "8.12 counts 掃描窗＝max(60min,4h)")
    agg_params = [p for s, p in cur.sql_log if f"FROM   {OPR_TEMP}" in s
                  and "GROUP BY cls" in s][0]
    t.eq(agg_params, (CAP - timedelta(minutes=60), CAP - timedelta(hours=4),
                      CAP - timedelta(minutes=60), CAP - timedelta(hours=4)),
         "8.13 聚合述詞＝(now_lo, ref_lo)×2")
    cls_params = [p for s, p in cur.sql_log if s.startswith(f"INSERT INTO {OPR7_TEMP} ")][0]
    t.eq(cls_params, (CAP, CAP - timedelta(days=7), CAP), "8.14 分類窗＝(cap, cap−7d, cap)")
    # now_min > ref_h*60 的 env 誤配：掃描窗跟著變大、n_4h 仍是 ref 窗語意（述詞明寫）
    cur2 = _FakeCur(count_rows=[(LINE_KEY, 0, 0)], auto_rows=[(DA, 3, 20, 5)], now=CAP)
    compute_operator(cur2, cap=CAP, now_min=600, ref_h=4)
    sp2 = [p for s, p in cur2.sql_log if s.startswith(f"INSERT INTO {OPR_TEMP} ")][0]
    t.eq(sp2, (CAP - timedelta(minutes=600), CAP), "8.15 now_min>ref ⇒ 掃描窗取 max")
    ap2 = [p for s, p in cur2.sql_log if f"FROM   {OPR_TEMP}" in s and "GROUP BY cls" in s][0]
    t.eq(ap2[1], CAP - timedelta(hours=4), "8.16 n_4h 述詞仍釘在 ref 窗（不被掃描窗偷換）")
    # guard 失敗 ⇒ 往上拋（不吞成一盤 0）
    try:
        compute_operator(_FakeCur(guard_row=(1, 1, 1, 0), now=CAP), cap=CAP)
        t.ok(False, "8.17 OperatorId ABSENT 應 raise")
    except SchemaDivergence:
        t.ok(True, "8.17 ★ OperatorId ABSENT ⇒ SchemaDivergence 往上拋（不回 0）")
    # ★ 兩段式必須「物化 → 聚合」成對送出：漏掉聚合那一句 ⇒ 分類層永遠空、
    #   auto 永遠 False、機器帳號站全線顯假人數（build 過程真的踩過一次、由本斷言抓到）。
    cur3 = _FakeCur(count_rows=[(DA, 1, 1), (LINE_KEY, 1, 1)],
                    auto_rows=[(DA, 3, 20, 5)], now=CAP)
    fetch_operator_counts(cur3, CAP, stats={})
    seq = [s.strip()[:24] for s, _p in cur3.sql_log]
    t.eq(len(seq), 5, "8.19 [O1]＝DROP＋CREATE＋INSERT＋stats＋聚合 五句")
    t.ok(cur3.sql_log[1][0].startswith("CREATE TABLE"),
         "8.19b [O1] 第二句＝CREATE（無參數＝外層 scope；先建才 INSERT）")
    t.ok(f"FROM   {OPR_TEMP}" in cur3.sql_log[-1][0] and "GROUP BY cls" in cur3.sql_log[-1][0],
         "8.20 [O1] 末句＝#temp 聚合（漏掉它＝整盤 0）")
    cur4 = _FakeCur(auto_rows=[(DA, 3, 20, 5)], now=CAP)
    got4 = fetch_auto_stats(cur4, CAP, stats={})
    t.eq(len(cur4.sql_log), 4, "8.21 [O2]＝DROP＋CREATE＋INSERT＋聚合 四句")
    t.eq(got4, [(DA, 3, 20, 5)], "8.22 ★ [O2] 真的取回聚合列（漏聚合＝auto 永遠 False）")
    # route 參數穿透
    o8c = compute_operator(_FakeCur(count_rows=[(DA, 2, 2), (CAL, 1, 1), (LINE_KEY, 2, 2)],
                                    auto_rows=[(DA, 5, 20, 9), (CAL, 5, 20, 9)], now=CAP),
                           cap=CAP, route=[DA])
    t.eq(list(o8c["stations"]), [DA], "8.18 route 參數穿透（非 route 站不出鍵）")

    # ---- 9. ★§24 輸出探針：canonical 全文序列化後零識別值 -----------------------
    import json
    blob = json.dumps(o4, ensure_ascii=False, default=str)
    t.ok("stpPersonID" not in blob, "9.1 ★ 輸出零 stpPersonID 字樣")
    for probe in ("王小明", "EMP0", "operator_name", "OperatorName", "@aldergrove"):
        t.ok(probe not in blob, f"9.2.{probe} 輸出零人名/帳號形")
    # 站層值域：只有 int/bool/dict —— 沒有任何自由字串可夾帶識別值
    for cls, s in o4["stations"].items():
        t.ok(all(isinstance(v, (int, bool)) for k, v in s.items() if k != "diag"),
             f"9.3.{cls} 站層值只有 int/bool")
        t.ok(all(isinstance(v, int) for v in s["diag"].values()),
             f"9.4.{cls} 站層 diag 值只有 int")
    t.ok(all(isinstance(v, int) for v in o4["line"].values()), "9.5 line 值只有 int")
    # 輸入若帶第五欄（未來 additive 或誤傳）也不會被帶進輸出
    o9 = build_operator([(DA, 1, 1), (LINE_KEY, 1, 1)],
                        [(DA, 3, 20, 5)], CAP)
    t.eq(tuple(o9["stations"][DA].keys()), STATION_KEYS, "9.6 輸出鍵集固定（白名單組裝）")

    # ---- 9b. O3 綑級觀察（沒有 ID fixture；只驗 aggregate 形）-------------------
    # 兩個綑各為 2；同一人若跨綑，本契約仍保留兩個 2（不做跨綑去重）。FA 是 D-OP2
    # auto 站，呼叫端沒有把它放進 membership，故它沒有機會混進任一綑。
    km = [(DA, "K-A"), (CAL, "K-A"), (WB, "K-B"), (SER, "K-B")]
    kc = _FakeCur(kun_rows=[("K-A", 2), ("K-B", 2)], now=CAP)
    got_k = fetch_operator_kun_counts(kc, CAP, km)
    kmap = build_kun_counts(got_k, km)
    t.eq(kmap, {"K-A": 2, "K-B": 2},
         "9b.1 two bundles may both observe 2; cross-bundle people are intentionally not deduped")
    t.eq(build_kun_counts([], km), {"K-A": 0, "K-B": 0},
         "9b.2 SQL-empty bundle is measured 0, not a missing key")
    t.ok(all("OperatorId" not in str(p) for _s, p in kc.sql_log)
         and all("stpPersonID" not in s and "Operator.Name" not in s for s, _p in kc.sql_log),
         "9b.3 O3 parameters/results carry no person identifier; SQL keeps both name paths absent")
    # drop → CREATE → INSERT（無聚合）→ 對 #temp 聚合＝與 [O1] 逐字同一組物化 SQL。
    t.eq([s.strip().split("\n")[0][:12] for s, _p in kc.sql_log],
         ["IF OBJECT_ID", "CREATE TABLE", "INSERT INTO ", "SELECT m.kun"],
         "9b.4 O3 走兩段式：drop→create→無聚合 INSERT→#temp 聚合（形同 [O1]）")
    t.ok(kc.sql_log[-1][0].count("COUNT(DISTINCT") == 1
         and "process_segment_v" not in kc.sql_log[-1][0],
         "9b.5 ★ 聚合那一句一列 view 都不掃（blocking aggregate 禁令）")
    t.ok(sum("dbo.process_segment_v" in s for s, _p in kc.sql_log) == 1,
         "9b.6 整支 O3 只掃一次窄窗大 view（不是每綑掃一次）")
    t.eq(len(kc.sql_log[-1][1]), 2 * len(km) + 1,
         "9b.7 綑成員全部以 bind parameter 進 SQL（零字串內插）")

    # ---- 10. env 解析（typo 不炸開機）------------------------------------------
    os.environ["_Board_OPR_TEST"] = "abc"
    t.eq(_env_int("_Board_OPR_TEST", 60), 60, "10.1 壞值 → 預設")
    os.environ["_Board_OPR_TEST"] = "-5"
    t.eq(_env_int("_Board_OPR_TEST", 60), 60, "10.2 非正數 → 預設")
    os.environ["_Board_OPR_TEST"] = " 30 "
    t.eq(_env_int("_Board_OPR_TEST", 60), 30, "10.3 合法值（含空白）")
    del os.environ["_Board_OPR_TEST"]
    t.eq(_env_int("_Board_OPR_TEST_MISSING", 7), 7, "10.4 未設 → 預設")

    # ---- 11. render 呈現層 smoke（防 f-string 格式 bug 在 live 才炸）------------
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        render(o4)          # 正常盤（含 auto 站與 0 值站）
        render(o7)          # 空盤／degraded
    out_txt = buf.getvalue()
    t.ok(SCHEMA_ID in out_txt, "11.1 render 不炸、標頭有 schema")
    t.ok("—" in out_txt, "11.2 auto 站顯「—」（絕不顯 1）")
    t.ok("DEGRADED" in out_txt, "11.3 degraded 大聲印出")
    t.ok("·自動站" in out_txt, "11.4 auto 站照列＋標記（不藏站）")
    t.ok("stpPersonID" not in out_txt and "王小明" not in out_txt, "11.5 ★ render 亦零人名")
    t.ok("相加不相等屬預期" in out_txt, "11.6 線級去重註記（D-OP4）")

    print(f"\n  斷言 {t.n} 條、失敗 {len(t.bad)} 條")
    for b in t.bad:
        print(f"    FAIL  {b}")
    print(f"\n  → {'PASS ✅' if not t.bad else 'FAIL ❌'}")
    if t.bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
