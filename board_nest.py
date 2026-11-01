#!/usr/bin/env python3
"""座級產出／良品的**唯一**讀取與聚合層（`ROADMAP` #110 (3) 形①＋`#126` 形②：`/station/{cls}` 逐座列）。

定位
----
`board_capacity` 是**座的名冊**、本檔是**座的事實**（窗內那一座跑了幾段、PASS／FAIL 幾段、
末段何時）。兩者刻意分家：名冊是物理事實（C-LD46，Chris 認過），本檔量的是 MES 訊號——
**名冊驅動列、資料只填格**（`SPEC-CAPACITY-FORECAST.md` §7.4：座是名冊事實不是資料事實，
缺座印「—」＋原因）。

名冊有**兩形**（`SPEC-CAPACITY-FORECAST.md` §0 的形①／形②；本檔兩形都做）：

===== ===================================== ================== =========================
形     名冊                                  `unit` 長什麼樣     資料腿
===== ===================================== ================== =========================
①     `board_capacity.NEST_EAV_UNITS`     `"nest:3"`         `build_seat_sql`（EAV 座號）
②     `board_capacity.COTAG_CHILD_UNITS`  `"EID-05707"`      `build_cotag_sql`（子 EID 自身段）
===== ===================================== ================== =========================

形② 的座**有自己的 EID**（MES 段上 `astEquipmentID` 就是它），所以不必查 EAV 座號——
差別只在「unit 是子 EID 字串、不是 `nest:n`」。**顯示、原因詞、口徑、A／U／OEE 恆「—」
全部共用**（`ROADMAP` `#126` (3)(4)；`SPEC-CAPACITY.md` §11.7）。
⚠ 形③（CAL Gen2／CLN 槽位列）**不歸本檔**——那些子槽位有自己的 EID **且自己在該跑窗名冊上**，
走的是 `board_data.expand_units_timeline` 的機台列，不是座列。

鐵則（違反即錯）
----------------
- **C-CP16**（`SPEC-CAPACITY.md` §11）：座列由名冊驅動；原因詞只有三個
  （`no_data`／`stale`／`not_fresh`）；**座級數字永不進站級任何 Σ**；座列的
  A／U／OEE／速率**恆「—」**（C-CF5 形① 不算 cap_util、C-CF8 稼動率永不按座）。
- **座級「產出」＝該座 prod-grade 段數**，與站級 PH（module 顆數）**不是同一個量**
  ⇒ 表下小字明講「不加總」；任何把它 Σ 起來對站級的動作都是錯的。
- 本檔**不算速率**（C-CP1：速率只有一把＝`board_monthly.month_rate`）。
- MES 讀取＝**固定審過的參數化 query**（`build_seat_sql`／`build_cotag_sql`）、唯讀、view 上零聚合
  （`DISTINCT`／`GROUP BY`／`MAX()` 一個都沒有——最大值一律在 Python 算；MES perf saga
  的斷崖就是 view 上的 blocking aggregate）。**只有 `source == "live"` 會連 MES。**

CLI（離線可跑、零 MES）
-----------------------
  python board_nest.py --selftest
"""
import argparse
import datetime as _dt
import re
import sys

import board_capacity as _wcap
import core_source as _wsrc                               # 來源值／合成進程旗標唯一出處（零相依）
from core_lastprod_push import PRODUCTION_DESIGNATIONS   # 生產級 designation 的**唯一**清單

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ═══ 原因詞（**只有三個**；`SPEC-CAPACITY-FORECAST.md` §7.5 判準）══════════════════
#   ⚠ 不得發明第四個。判準要改 ⇒ 先改 §7.5 與 `_PROBE-NEST-PRODUCTION-README.md`
#     特別分析 A 的三判準，再改這裡。
REASON_NO_DATA = "no_data"        # 覆蓋度：該座在窗內連一段都沒有
REASON_STALE = "stale"            # 最近活性：該座早已停用、末段遠早於窗末
REASON_NOT_FRESH = "not_fresh"    # 該座（或整台機）在窗末數日全靜默
SEAT_REASONS = (REASON_NO_DATA, REASON_STALE, REASON_NOT_FRESH)

# 判準的兩個門檻（天）。14 天＝README 特別分析 A 的「最近活性」窗；3 天＝「窗末數日」。
SEAT_STALE_DAYS = 14
SEAT_FRESH_DAYS = 3

_NEST_UNIT_RE = re.compile(r"^nest:([1-9]\d*)$")


# ---------------------------------------------------------------------------
# 名冊投影（**零第二份名冊**：全部從 `board_capacity.NEST_EAV_UNITS` 推）
# ---------------------------------------------------------------------------
def _roster(roster=None):
    return _wcap.NEST_EAV_UNITS if roster is None else roster


def _cotag(cotag=None):
    return _wcap.COTAG_CHILD_UNITS if cotag is None else cotag


def roster_for_cls(cls, roster=None, cotag=None):
    """回 `((eid, var, (unit, ...)), ...)`＝該站別在名冊上的機台與座，**名冊順序**（形① 先、形② 後）。

    · **形①**（`NEST_EAV_UNITS`）：`var`＝EAV 變數名（非 None）、`unit`＝`"nest:n"`。
    · **形②**（`COTAG_CHILD_UNITS`）：**`var is None` 就是形② 的記號**、`unit`＝子 EID 字串。
      站別由 `board_capacity.subunit_main_cls(eid)` 判（`COTAG_MAIN_CLS`＝同一份名冊的第二欄）
      ——**零第二份名冊**：本檔一個子 EID、一個 var 名都不硬編（smoke／selftest 有 identity 釘）。
    · 不在名冊的站別 ⇒ `()`（呼叫端據此完全不打 MES＝其餘 35 站逐位元原行為）。

    ⚠ 呼叫端要分形時**只准問 `var is None`**，不要自己 parse unit 字串
      （`nest_no` 是唯一 parser，回 `None` 就是形②）。
    """
    out = []
    for eid, rec in _roster(roster).items():
        if rec.get("main_cls") != cls:
            continue
        out.append((eid, rec.get("var"),
                    tuple("nest:%d" % n for n in (rec.get("nests") or ()))))
    for eid, kids in _cotag(cotag).items():
        if _wcap.subunit_main_cls(eid) != cls:
            continue
        out.append((eid, None, tuple(kids or ())))
    return tuple(out)


def cls_has_seats(cls, roster=None, cotag=None):
    """本站別有沒有名冊座（＝這一頁要不要多一條資料腿）。"""
    return bool(roster_for_cls(cls, roster, cotag))


def nest_no(unit_id):
    """`"nest:3"` → `3`；非 nest 形 ⇒ `None`（**唯一** parser，別在別處寫第二個）。"""
    if not isinstance(unit_id, str):
        return None
    m = _NEST_UNIT_RE.fullmatch(unit_id)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# 固定審過的參數化 query（唯讀；**每一個表名／欄名都追溯得到跑過的查詢**）
# ---------------------------------------------------------------------------
def build_seat_sql(with_designation=True):
    """逐座產出／良品的窗掃 SQL。回 `(sql, param_names)`；參數序＝**`("var","cls","start","end")`**
    （＝文本裡 `?` 出現的順序：`var` 在 join 條件上、`cls`／窗兩端在 where 上）。

    ═══ 逐欄出處（[[ssms-sql-column-provenance]]：沒有一個名字是猜的）═══════════════
      · `dbo.segment_equipment_v se` —— `astEquipmentID`／`stpPK`／`stpStartTime`／
        `stpEndTime`／`SegmentClassID`／`matDesignation`／`stpTestResult`
        → `_PROBE-NEST-PRODUCTION.sql` 建 `#ns` 的那一段（Chris 2026-09-08 在 SSMS 實跑、
          結果存證＝`_PROBE-NEST-PRODUCTION-RESULT.tsv`）＋`core_lastprod_push.build_nest_sql`
          （`_NEST-PROBE-41` PART A 實跑）。
      · `dbo.segment_integer_data_t si` —— `stpPK`／`vdfPK`／`sgiInteger`
        → 同上兩處（座號**不在** `segment_equipment_v` 上；2026-08-31 `se.NestNo` 事故就是猜的）。
      · `dbo.production_data_definition_t vdf` —— `vdfPK`／`vdfProductionDataID`
        → 同上兩處。
      · `var` 的值（`varLAM_NestID` 等）與 `cls` → `board_capacity.NEST_EAV_UNITS`
        （逐台 Evidence 在該檔）。
      · 生產級 designation 值 → `core_lastprod_push.PRODUCTION_DESIGNATIONS`（本檔 import，
        **不另抄一份字面清單**）；過濾**刻意留在 Python**＝同一把尺只有一個住處。
      放棄的欄：無。

    ═══ 效能形狀（一條不放寬）═══════════════════════════════════════════════════════
      逐 class 點查、**零 EID `IN`-list**（B2 事故形）、view 上**零** `DISTINCT`／
      `GROUP BY`／`MAX()`（聚合全在 Python）、`stpEndTime >= stpStartTime` 用 `>=` 不用
      `>`（[[mes-zero-length-segments]]：零工期段是合法瞬時事件段）、尾 `OPTION (RECOMPILE)`。
      兩個 join 都是**小維度表**、以 `stpPK`／`vdfPK` 相接（探針 PART A 在 91 天片上跑過同形）。

    ═══ 與探針 STEP1 的**唯一**差異（說得出理由才准有）═════════════════════════════
      探針的窗是 `stpStartTime >= @ws AND stpStartTime < @we`（**只看起點**）；
      本檔是 `stpEndTime >= @start AND stpStartTime < @end`（**區間重疊形**）。
      理由：站頁的窗是滾動窗，**跨越窗起點的那一段是窗內的真實活動**，只看起點會把它整段
      丟掉（同一個病在 M4 零工期事故裡付過一次學費）。兩端仍各有一個上／下界 ⇒ 不是無界掃描。
      ⛳ **這一條請 Chris 在域內 SSMS 對 30 天窗量一次執行計畫**（本 repo 的 agent 永不連 MES）。

    `with_designation=False`＝降級形（同 `build_nest_sql` 的那一級）：沒有 designation 就沒有
    「生產級」這個概念 ⇒ 呼叫端一律不產出座列（不猜）。
    """
    cols = "se.astEquipmentID, se.stpStartTime, se.stpEndTime"
    if with_designation:
        cols += ", se.matDesignation"
    cols += ", se.stpTestResult, si.sgiInteger"
    sql = (f"SELECT {cols}\n"
           "FROM dbo.segment_equipment_v se\n"
           "JOIN dbo.segment_integer_data_t si ON si.stpPK = se.stpPK\n"
           "JOIN dbo.production_data_definition_t vdf ON vdf.vdfPK = si.vdfPK\n"
           " AND vdf.vdfProductionDataID = ?\n"
           "WHERE se.SegmentClassID = ?\n"
           "  AND se.stpEndTime >= ?\n"
           "  AND se.stpStartTime < ?\n"
           "  AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime\n"
           "OPTION (RECOMPILE)")
    return sql, ("var", "cls", "start", "end")


def fetch_nest_seat_rows(cls, var, start, end, cur=None):
    """跑 `build_seat_sql` 取原始段列（**唯讀、零聚合**）。回 `[(eid, start, end, desig, result, nest), ...]`。

    `cur` 給測試／共用連線用；`None` ⇒ 自己開一條（唯讀、Kerberos 整合驗證）。
    ⚠ 只有 `source == "live"` 的路徑會走到這裡。
    """
    sql, _names = build_seat_sql()
    if cur is not None:
        cur.execute(sql, var, cls, start, end)
        return list(cur.fetchall())
    import mes_timeline as _mt                     # 延後 import：離線 selftest 不需要 pyodbc
    conn, own = _mt._connect()
    try:
        conn.timeout = 180                          # 同 `board_data.LIVE_QUERY_TIMEOUT_SEC`
        own.execute(sql, var, cls, start, end)
        return list(own.fetchall())
    finally:
        conn.close()


def build_cotag_sql(with_designation=True):
    """**形②**（co-tag 子 EID）逐座產出／良品的窗掃 SQL。回 `(sql, param_names)`；
    參數序＝**`("eid","cls","start","end")`**（＝文本裡 `?` 出現的順序，四個都在 where 上）。

    ═══ 它與 `build_seat_sql` 的關係（**唯一**的兩處差異；`ROADMAP` `#126` (2)）═══════
      1. **去掉兩個 EAV join 與 `si.sgiInteger` 欄**——形② 的座**有自己的 EID**，座號不必查
         EAV（`segment_integer_data_t`／`production_data_definition_t` 整組不需要）。
      2. **多一個 `se.astEquipmentID = ?`**——逐子 EID **點查**（Leak Test 頁＝4 次）。
         ⚠ 這**不是** B2 事故形：B2 的病是 `IN (…)` 一長串 EID 餵給 view，這裡是單值等值述詞。
      其餘（表名、欄名、窗述詞、`>=`、尾 `OPTION (RECOMPILE)`、view 上零聚合）**逐字沿用**。

    ═══ 逐欄出處（[[ssms-sql-column-provenance]]：沒有一個名字是猜的）═══════════════
      · `dbo.segment_equipment_v se` —— `astEquipmentID`／`stpStartTime`／`stpEndTime`／
        `SegmentClassID`／`matDesignation`／`stpTestResult`
        → **每一欄都在 `build_seat_sql` 的出處表裡**（同一個 view、同一組欄）＝
          `_PROBE-NEST-PRODUCTION.sql` 建 `#ns` 的那一段（Chris 2026-09-08 在 SSMS 實跑）
          ＋`core_lastprod_push.build_nest_sql`（`_NEST-PROBE-41` PART A 實跑）。**零新猜欄。**
      · `eid` 的值（`EID-05707` 等）與 `cls` → `board_capacity.COTAG_CHILD_UNITS`／
        `COTAG_MAIN_CLS`（逐列 Evidence＝`_DORMANT-CANDIDATES-RESULT.txt` 730 天 Leak Test
        co-tag 結果：05707／07757 各 4.4 萬段、同站別 `segLamLeakTest`、末段 08-31）。
      · 生產級 designation 值 → `core_lastprod_push.PRODUCTION_DESIGNATIONS`（本檔 import）。
      放棄的欄：`si.sgiInteger`（形② 不需要座號——**見上面差異 1**，這是刻意放棄不是遺漏）。

    ═══ 效能形狀（計畫已量過）═════════════════════════════════════════════════════
      `_PROBE-NEST-SQLPLAN-RESULT.txt` 的判讀：成本＝**窗內全廠段數 × 2 次 lookup**、
      與本站列數無關 ⇒ 去掉兩個 join 只會更便宜；4 次點查 7d 窗約 2s、由
      `app.load_nest_seats` 的 TTL 擋住重複打。
    """
    cols = "se.astEquipmentID, se.stpStartTime, se.stpEndTime"
    if with_designation:
        cols += ", se.matDesignation"
    cols += ", se.stpTestResult"
    sql = (f"SELECT {cols}\n"
           "FROM dbo.segment_equipment_v se\n"
           "WHERE se.astEquipmentID = ?\n"
           "  AND se.SegmentClassID = ?\n"
           "  AND se.stpEndTime >= ?\n"
           "  AND se.stpStartTime < ?\n"
           "  AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime\n"
           "OPTION (RECOMPILE)")
    return sql, ("eid", "cls", "start", "end")


def fetch_cotag_rows(eid, cls, start, end, cur=None):
    """跑 `build_cotag_sql` 取原始段列（**唯讀、零聚合**）。回 `[(eid, start, end, desig, result), ...]`
    ——**五欄**（形① 是六欄；第六欄是座號，形② 不需要）。

    `cur` 給測試／共用連線用；`None` ⇒ 自己開一條（唯讀、Kerberos 整合驗證）。
    ⚠ 只有 `source == "live"` 的路徑會走到這裡。
    """
    sql, _names = build_cotag_sql()
    if cur is not None:
        cur.execute(sql, eid, cls, start, end)
        return list(cur.fetchall())
    import mes_timeline as _mt                     # 延後 import：離線 selftest 不需要 pyodbc
    conn, own = _mt._connect()
    try:
        conn.timeout = 180
        own.execute(sql, eid, cls, start, end)
        return list(own.fetchall())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 純函式：聚合（**可用假 rows 驗；零 I/O、零 MES**）
# ---------------------------------------------------------------------------
def _new_seat():
    return {"n_seg": 0, "n_prodgrade": 0, "n_pass": 0, "n_fail": 0,
            "last_end": None, "first_start": None}


def aggregate_nest_seats(rows, roster=None, win_end=None, unit_of=None):
    """原始段列 → `({母機 eid: {unit: {...}}}, diag)`。**純函式**；**形①／形② 共用這一支**。

    · **形①**（`unit_of is None`）：`rows` 的元素＝**六欄**
      `(eid, stpStartTime, stpEndTime, matDesignation, stpTestResult, sgiInteger)`；
      母機＝`eid` 本身、`unit`＝`"nest:<sgiInteger>"`，**允許集**由名冊的 `nests` 推。
    · **形②**（`unit_of` 是 `{MES 段上的 eid: (母機 eid, unit)}` 對照表）：`rows` 是
      `fetch_cotag_rows` 的**五欄**（沒有座號欄，也不會去讀 `row[5]`）；母機與 unit 直接查表。

      ★ **為什麼選「`unit_of` 對照表」而不是「五欄補第六欄 None」**（`ROADMAP` `#126` (3) 二擇一）：
      形② 的 MES 列上只有**子** EID，**沒有任何一欄說得出它的母機是誰**——補一個 `None` 進第六欄
      之後，這支仍得再查一次名冊才知道要掛到哪一台，等於把同一件事拆成兩處做。對照表把
      「母機是誰」與「這個 EID 准不准進來」合成**同一個查詢**，於是「名冊外一律丟棄並計數」
      仍然只有一個住處（表裡沒有的 EID ⇒ `dropped_eid`），呼叫端也不必偽造欄位。

    · **名冊外一律丟棄並計數**：不在名冊／不在對照表的 EID（`dropped_eid`）、名冊上該台沒有的
      座號（`dropped_seat`，**`nest 0` 屬於這一類**＝C-LD46「nest 0 不是座位」）。
      丟棄要**計數**、不要靜默——安靜丟掉的列是本專案最貴的錯誤類型。
    · `win_end` 非 None ⇒ 起點 `>= win_end` 的列不計（防禦性上界；SQL 已有同條件）。
    · `n_pass`／`n_fail` **只數 prod-grade 段**（良率是生產級的良率；工程／試跑段不進良率分母）。
    · `last_end`／`first_start` 取 max／min（**在 Python 算**；view 上零聚合＝perf saga）。

    ⚠ 回傳是 `(seats, diag)` 兩元組（規格寫的是單一 dict）——丟棄計數必須有地方去，
      而純函式不該自己 print；呼叫端負責揭露。
    """
    reg = _roster(roster)
    allowed = {eid: {"nest:%d" % n for n in (rec.get("nests") or ())}
               for eid, rec in reg.items()}
    seats, diag = {}, {"n_rows": 0, "dropped_eid": 0, "dropped_seat": 0, "dropped_shape": 0}
    for row in (rows or ()):
        diag["n_rows"] += 1
        try:
            eid, s_start, s_end, desig, result = (row[0], row[1], row[2], row[3], row[4])
            nest = (row[5] if unit_of is None else None)
        except (IndexError, TypeError):
            diag["dropped_shape"] += 1
            continue
        if unit_of is None:
            if eid not in allowed:
                diag["dropped_eid"] += 1
                continue
            try:
                unit = "nest:%d" % int(nest)
            except (TypeError, ValueError):
                diag["dropped_seat"] += 1
                continue
            if unit not in allowed[eid]:           # nest 0 與名冊外座號都走這條
                diag["dropped_seat"] += 1
                continue
            parent = eid
        else:
            ent = unit_of.get(eid)
            if ent is None:                        # 對照表外的 EID＝名冊外（形② 沒有第二層座號）
                diag["dropped_eid"] += 1
                continue
            parent, unit = ent
        if win_end is not None and s_start is not None and s_start >= win_end:
            diag["dropped_shape"] += 1
            continue
        agg = seats.setdefault(parent, {}).setdefault(unit, _new_seat())
        agg["n_seg"] += 1
        if s_end is not None and (agg["last_end"] is None or s_end > agg["last_end"]):
            agg["last_end"] = s_end
        if s_start is not None and (agg["first_start"] is None or s_start < agg["first_start"]):
            agg["first_start"] = s_start
        if desig not in PRODUCTION_DESIGNATIONS:
            continue
        agg["n_prodgrade"] += 1
        if result == "PASS":
            agg["n_pass"] += 1
        elif result == "FAIL":
            agg["n_fail"] += 1
    return seats, diag


def machine_last_end(seats_for_eid):
    """整台機所有座的 `max(last_end)`；全空 ⇒ `None`。"""
    ends = [a.get("last_end") for a in (seats_for_eid or {}).values()
            if a and a.get("last_end") is not None]
    return max(ends) if ends else None


def seat_reason(agg, mach_last_end, win_end,
                stale_days=SEAT_STALE_DAYS, fresh_days=SEAT_FRESH_DAYS):
    """`SPEC-CAPACITY-FORECAST.md` §7.5 的三判準。回 `None`（無註記）或三個原因詞之一。

    優先序（**固定**）：無資料 → 陳舊 → 新鮮度不足。
      · 無資料：窗內 `n_seg == 0`（或該座整個不在 `seats` 裡）。
      · 陳舊：`last_end < win_end − 14d`。
      · 新鮮度不足：`last_end` 落在 `[win_end−14d, win_end−3d)`，
        **或整台機的 `max(last_end) < win_end − 3d`**（整台靜默＝該座的數字也不該被讀成「現行」）。
    """
    if win_end is None:
        return None
    if not agg or (agg.get("n_seg") or 0) <= 0 or agg.get("last_end") is None:
        return REASON_NO_DATA
    if agg["last_end"] < win_end - _dt.timedelta(days=stale_days):
        return REASON_STALE
    if agg["last_end"] < win_end - _dt.timedelta(days=fresh_days):
        return REASON_NOT_FRESH
    if mach_last_end is not None and mach_last_end < win_end - _dt.timedelta(days=fresh_days):
        return REASON_NOT_FRESH
    return None


def _fmt_ts(ts):
    if ts is None:
        return None
    try:
        return ts.strftime("%Y-%m-%d %H:%M")
    except AttributeError:
        return str(ts)


def seat_row(eid, unit, agg, reason, orphan=False):
    """一座一列（鍵**沿用** `board_oee.station_detail` 的 `units[i]` 形 ＋ 四個加性鍵）。

    · 產出＝`n_prodgrade`（**該座 prod-grade 段數**，與站級 PH 口徑不同、**不得加總對站級**）。
    · 良品＝`n_pass`；良率＝`n_pass /(n_pass+n_fail)`（**只算 prod-grade 段**；分母 0 ⇒ `None`）。
    · **A／U／OEE／bar 恆空**（C-CF5／C-CF8）——`has_util=False`、`rw_axis=False`、
      `util_unmeasurable=False` ⇒ 模板三格天然走 `—` 分支。
    · `no_data`／`stale` ⇒ 產出／良品／良率一律 `None`（印「—」）；`not_fresh` ⇒ **數字照印**。
    · **形②**（`unit` 是子 EID 字串）：`nest_no` 回 `None` ⇒ `seat_nest is None`＝模板判形的
      **唯一**依據（`seat_unit` 印成「子工位 EID-05707」）。其餘每一欄**逐位元與形① 同一條路**
      ——`#126` (4)：A／U／OEE 恆「—」＋同一個裁決 tooltip、產出仍是**段數**不是 PH。
      ⚠ 形② 的子 EID 技術上量得到自己的稼動（它有自己的段），**本輪刻意不算**：那是 C-CF8
        的另一個裁決（工單登在母機、子 EID 不在該跑窗名冊）＝**範圍選擇不是物理限制**。
    """
    blank = reason in (REASON_NO_DATA, REASON_STALE)
    a = agg or _new_seat()
    n_pass, n_fail = (a.get("n_pass") or 0), (a.get("n_fail") or 0)
    q = (n_pass / (n_pass + n_fail) * 100.0) if (n_pass + n_fail) > 0 else None
    return {
        "comp": [],
        "eid": eid, "desc": "",
        "output": (None if blank else (a.get("n_prodgrade") or 0)),
        "good": (None if blank else n_pass),
        "fail_pct": None,
        "quality_pct": (None if blank else q),
        "availability": None, "utilization": None, "util": None, "oee": None,
        "has_util": False, "util_unmeasurable": False,
        "rw_axis": False, "rw_planned_sec": None,
        "parent_eid": eid,
        # ── 加性鍵（座列專用；站級 Σ 路徑一個都不讀）────────────────────────────
        "is_seat": True,
        "seat_unit": unit,
        "seat_nest": nest_no(unit),
        "seat_reason": reason,
        "seat_last_end": _fmt_ts(a.get("last_end")),
        "seat_orphan": bool(orphan),
    }


def seat_rows_for_units(units, cls, seats, win_end, roster=None, allow_eids=None, cotag=None):
    """把座列插進 `station_detail` 的 `units`。回 `(new_units, has_seats)`。**純函式**。

    · **列由名冊驅動**（§7.4）：名冊上該站別的每一台、每一座都成列，`seats` 有沒有事實只
      決定格子印數字還是「—」＋原因。
    · 母機列在表上 ⇒ 座列**緊接其後**（名冊座號順序）；母機列不在表上（demo 的合成機隊）
      ⇒ 該台的座列**整組附在表尾**、並標 `seat_orphan`（模板據此在小字補印母機 EID）。
    · `allow_eids` 非 None（`?tag=` 能力篩選有效）⇒ **只保留母機仍在表上的那幾台的座**。
      理由：tag 篩選是使用者明確選的子集，替看不見的機台印座列＝在篩選過的表上長出沒被選的機台。
    · **輸入的每一個 dict 原封不動**（identity 保持）——座列不進站級任何 Σ、也不改母機列。
    · **形②（co-tag 子 EID）逐位元沿用上面每一條**（`ROADMAP` `#126` (4)）：插入位置、orphan、
      `?tag=` 規則一個字都沒有為它改——`roster_for_cls` 已經把兩形投影成同一個形狀。
    """
    rows = list(units or ())
    reg_rows = roster_for_cls(cls, roster, cotag)
    if not reg_rows:
        return rows, False
    present = {r.get("eid"): i for i, r in enumerate(rows) if not r.get("is_seat")}
    seats = seats or {}
    out, appended, has = [], [], False
    # 先算好每台機的座列（母機是否在表上只影響「插哪裡」，不影響「有沒有」）
    by_parent = {}
    for eid, _var, units_ids in reg_rows:
        if allow_eids is not None and eid not in allow_eids:
            continue
        mach = seats.get(eid) or {}
        mle = machine_last_end(mach)
        orphan = eid not in present
        made = []
        for unit in units_ids:
            agg = mach.get(unit)
            made.append(seat_row(eid, unit, agg,
                                 seat_reason(agg, mle, win_end), orphan=orphan))
        if made:
            has = True
            if orphan:
                appended.extend(made)
            else:
                by_parent[eid] = made
    for r in rows:
        out.append(r)
        kids = by_parent.pop(r.get("eid"), None) if not r.get("is_seat") else None
        if kids:
            out.extend(kids)
    out.extend(appended)
    return out, has


# ---------------------------------------------------------------------------
# demo／snapshot（**零 MES**）
# ---------------------------------------------------------------------------
# demo 的兩顆樣例座（**刻意照著真實探針的形狀挑**，讓「無資料」「陳舊」兩種顯示在離線頁上看得到）：
#   · `EID-26596` nest 2 全空  → `_PROBE-NEST-PRODUCTION-README.md` 特別分析 A：Press#2 nest2/4 全空。
#   · `EID-00425` nest 1 早停  → 同上：Press#1 nest1 僅 3 段、停於 08-14。
DEMO_SEAT_PINS = {('EID-54466', 'nest:2'): 'no_data', ('EID-54336', 'nest:1'): 'stale'}


def _demo_seed(*parts):
    """跨進程穩定的整數種子（**不用 `hash()`**——str 的 hash 每個進程都不一樣）。

    用 `zlib.crc32`：確定性、跨進程穩定，而且**相鄰的座號不會給出相鄰的數字**
    （第一版用累加式雜湊，`nest:2/3/4` 產出印成 146/147/148＝一眼看出是假的）。
    """
    import zlib
    return zlib.crc32("|".join(str(p) for p in parts).encode("utf-8"))


def demo_seats(cls, win_end, roster=None, days=7, cotag=None):
    """離線合成的座級事實（確定性；同輸入永遠同輸出）。回 `{eid: {unit: agg}}`。

    ⚠ demo 的機隊 EID 是合成的（`board_data._demo_station_rec`），名冊上的六台**不在**
      demo 的 yield 機台清單裡 ⇒ demo 的座列一律是 `seat_orphan`（表尾一組、小字標母機）。
      這是誠實的：demo 本來就沒有這六台。
    ★ **形② 走同一個迴圈、零特判**（`ROADMAP` `#126` (6)）：`roster_for_cls` 已經把兩形都投影成
      `(eid, var, units)`，`unit` 是 `"nest:3"` 還是 `"EID-05707"` 對合成完全沒差（種子吃字串）。
      Leak Test 的兩台母機同樣不在 demo 機隊裡 ⇒ 四列同樣是 orphan，**不特判**。
    """
    out = {}
    for eid, _var, units_ids in roster_for_cls(cls, roster, cotag):
        mach = {}
        for unit in units_ids:
            pin = DEMO_SEAT_PINS.get((eid, unit))
            if pin == REASON_NO_DATA:
                continue                            # 窗內零段＝該座整個不出現在事實裡
            seed = _demo_seed(eid, unit)
            if pin == REASON_STALE:
                n_seg, age_h = 3, 24 * (SEAT_STALE_DAYS + 12)
            else:
                n_seg = 120 + seed % 260
                age_h = 2 + seed % 30
            n_prod = max(1, int(n_seg * 0.92))
            n_fail = (seed % 11)
            n_pass = max(0, n_prod - n_fail)
            last = win_end - _dt.timedelta(hours=age_h)
            mach[unit] = {"n_seg": n_seg, "n_prodgrade": n_prod,
                          "n_pass": n_pass, "n_fail": n_fail,
                          "last_end": last,
                          "first_start": last - _dt.timedelta(days=max(1, days - 1))}
        if mach:
            out[eid] = mach
    return out


def load_seats(source, cls, days, roster=None, now=None, cotag=None):
    """站頁的資料腿入口。回 `{"seats":…, "win_end":…, "diag":…, "err":…}`。

    · `live`     → 打 MES。**兩形各走各的固定 query、同一條連線、同一個 `win_end`**：
                   形① `build_seat_sql` 逐 `var` 一條點查、形② `build_cotag_sql` 逐**子 EID**
                   一條點查（Leak Test ＝4 次）。窗＝`[now−days, now)`，`now` 取自 MES 自己的
                   `GETDATE()`——兩形共用同一個 `win_end` 是**原因詞判準的前提**（§7.5 的
                   「窗末數日」在兩形之間必須是同一刻，否則同一張表上兩把尺）。
    · `demo`     → `demo_seats`（合成、確定性；兩形同一個迴圈）。
    · 其餘（`snapshot` 等）→ **空事實**＝每一座都印「—（無資料）」。
      snapshot 檔裡本來就沒有座級資料，這是事實不是降級。
    """
    if not cls_has_seats(cls, roster, cotag):
        return {"seats": {}, "win_end": None, "diag": None, "err": None}
    synth = _wsrc.is_synth_process()        # 合成進程旗標（Q-A：I/O 看進程旗標、不看 source）
    if source == "demo" and not synth:      # 合成進程任何 source 都塌成 synth（Q-B）＝永不走 demo 合成器
        win_end = now or _dt.datetime.now()
        return {"seats": demo_seats(cls, win_end, roster, days, cotag=cotag),
                "win_end": win_end, "diag": {"source": "demo"}, "err": None}
    if not _wsrc.is_live_like(_wsrc.normalize_source(source)):   # §5 nest:541（S）；非合成進程≡`!= "live"`
        return {"seats": {}, "win_end": (now or _dt.datetime.now()),
                "diag": {"source": source}, "err": None}
    reg_rows = roster_for_cls(cls, roster, cotag)
    # 形② 的對照表：`{MES 段上的子 EID: (母機 eid, unit)}`。**由名冊推、零第二份清單**；
    # 它同時是形② 的允許集（表外的 EID ⇒ `dropped_eid`）。
    unit_of = {child: (eid, child)
               for eid, var, kids in reg_rows if var is None for child in kids}
    if synth:
        # 清冊 §1 #15–#17（M10）：兩形同一個 `win_end`（＝合成時鐘，取代 `GETDATE()`），原始列由
        # `wsynth_io` 同形供應（六欄／五欄），其後照走 `aggregate_nest_seats`（聚合只有這一支）。
        import wsynth_io as _io                    # 延遲 import：live／demo 進程永不載入
        win_end = _io.now()
        start = win_end - _dt.timedelta(days=days)
        rows, cot_rows = [], []
        for var in sorted({v for _e, v, _u in reg_rows if v}):
            rows.extend(_io.fetch_nest_seat_rows(cls, var, start, win_end, now=win_end))
        for child in unit_of:
            cot_rows.extend(_io.fetch_cotag_rows(child, cls, start, win_end, now=win_end))
    else:
        import mes_timeline as _mt
        conn, cur = _mt._connect()
        try:
            conn.timeout = 180
            cur.execute("SELECT GETDATE()")
            win_end = cur.fetchone()[0]
            start = win_end - _dt.timedelta(days=days)
            rows, cot_rows = [], []
            for var in sorted({v for _e, v, _u in reg_rows if v}):
                rows.extend(fetch_nest_seat_rows(cls, var, start, win_end, cur=cur))
            for child in unit_of:                       # 逐子 EID 點查（名冊順序；零 IN-list）
                cot_rows.extend(fetch_cotag_rows(child, cls, start, win_end, cur=cur))
        finally:
            conn.close()
    seats, diag = aggregate_nest_seats(rows, roster, win_end)
    if unit_of:
        # **同一支** `aggregate_nest_seats`（形② 帶對照表）；兩形的事實合進同一個 `seats`。
        seats2, diag2 = aggregate_nest_seats(cot_rows, roster, win_end, unit_of=unit_of)
        for eid, mach in seats2.items():
            seats.setdefault(eid, {}).update(mach)
        for k in ("n_rows", "dropped_eid", "dropped_seat", "dropped_shape"):
            diag[k] += diag2[k]
    diag["source"] = _wsrc.SOURCE_SYNTH if synth else "live"
    return {"seats": seats, "win_end": win_end, "diag": diag, "err": None}


# ═══ selftest（純函式、零 I/O、零 MES）══════════════════════════════════════════════
def _selftest():
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    D = _dt.datetime
    WE = D(2026, 9, 12, 8, 0, 0)
    REG = {"EID-00425": {"var": "varLAM_NestID", "main_cls": "segLamination",
                         "nests": (1, 2, 3, 4)},
           "EID-26596": {"var": "varLAM_NestID", "main_cls": "segLamination",
                         "nests": (1, 2, 3, 4)},
           "EID-99999": {"var": "varOther", "main_cls": "segOther", "nests": (1, 2)}}

    # ---------- (A) SQL 文本的形狀（效能與出處紀律都在文本上）----------
    sql, names = build_seat_sql()
    ck(names == ("var", "cls", "start", "end"), "參數序＝文本裡 ? 的順序")
    ck(sql.count("?") == 4, f"四個參數位；got {sql.count('?')}")
    for bad in ("DISTINCT", "GROUP BY", "MAX(", "ORDER BY", "COUNT(", "SUM("):
        ck(bad not in sql.upper(), f"view 上零聚合：{bad} 不得出現")
    ck("stpEndTime >= se.stpStartTime" in sql, "零工期段：>= 不是 >")
    ck(" IN (" not in sql and "astEquipmentID =" not in sql, "零 EID IN-list（B2 事故形）")
    ck(sql.rstrip().endswith("OPTION (RECOMPILE)"), "尾 OPTION (RECOMPILE)")
    ck("se.stpTestResult" in sql and "si.sgiInteger" in sql, "良率與座號兩欄都在")
    sql2, _ = build_seat_sql(with_designation=False)
    ck("matDesignation" not in sql2 and "matDesignation" in sql, "降級形沒有 designation")
    ck(build_seat_sql()[0] == sql, "同輸入逐位元同 SQL")

    # ---------- (B) 名冊投影 ----------
    ck([e for e, _v, _u in roster_for_cls("segLamination", REG)]
       == ["EID-00425", "EID-26596"], "逐站別投影＝名冊順序")
    ck(roster_for_cls("segLamination", REG)[0][2]
       == ("nest:1", "nest:2", "nest:3", "nest:4"), "座號展開成 nest:<n>")
    ck(roster_for_cls("segNoSuchStation", REG) == (), "名冊外站別 ⇒ 空")
    ck(cls_has_seats("segLamination", REG) and not cls_has_seats("segX", REG),
       "cls_has_seats")
    ck(nest_no("nest:7") == 7 and nest_no("nest:0") is None and nest_no("EID-1") is None,
       "nest_no 是唯一 parser（nest:0 不是合法座）")

    # ---------- (C) aggregate_nest_seats：丟棄、取 max、只數 prod-grade ----------
    t = D(2026, 9, 10, 12, 0, 0)
    rows = [
        ("EID-00425", t, t, "Production", "PASS", 1),
        ("EID-00425", t, t + _dt.timedelta(hours=2), "Production", "FAIL", 1),
        ("EID-00425", t - _dt.timedelta(days=1), t, "Engineering (Baseline)", "PASS", 1),
        ("EID-00425", t, t, "Dummy", "PASS", 1),            # 非 prod-grade：只進 n_seg
        ("EID-00425", t, t, "Production", "PASS", 0),       # nest 0 ⇒ 丟（C-LD46）
        ("EID-00425", t, t, "Production", "PASS", 9),       # 名冊外座號 ⇒ 丟
        ("EID-77777", t, t, "Production", "PASS", 1),       # 名冊外 EID ⇒ 丟
        ("EID-00425", t, t, "Production", None, 3),
    ]
    seats, diag = aggregate_nest_seats(rows, REG, WE)
    a1 = seats["EID-00425"]["nest:1"]
    ck(a1["n_seg"] == 4 and a1["n_prodgrade"] == 3, f"n_seg/n_prodgrade：{a1}")
    ck(a1["n_pass"] == 2 and a1["n_fail"] == 1, f"pass/fail 只數 prod-grade：{a1}")
    ck(a1["last_end"] == t + _dt.timedelta(hours=2), "last_end 取 max")
    ck(a1["first_start"] == t - _dt.timedelta(days=1), "first_start 取 min")
    ck(diag["dropped_seat"] == 2 and diag["dropped_eid"] == 1,
       f"丟棄要計數不要靜默：{diag}")
    ck(seats["EID-00425"]["nest:3"]["n_prodgrade"] == 1
       and seats["EID-00425"]["nest:3"]["n_pass"] == 0, "result 非 PASS/FAIL ⇒ 不進良率兩欄")
    ck("nest:2" not in seats["EID-00425"] and "EID-26596" not in seats,
       "沒有事實的座／機**不會**被 aggregate 憑空造出來（名冊驅動是顯示層的事）")
    _s2, d2 = aggregate_nest_seats([("EID-00425", WE, WE, "Production", "PASS", 1)], REG, WE)
    ck(d2["dropped_shape"] == 1, "起點 >= win_end 的列不計（防禦性上界）")
    ck(aggregate_nest_seats(None, REG, WE)[0] == {}, "空輸入 ⇒ 空事實")

    # ---------- (D) 三個原因詞（四種情境 ＋ 整台靜默）----------
    fresh = {"n_seg": 9, "last_end": WE - _dt.timedelta(hours=5)}
    old = {"n_seg": 9, "last_end": WE - _dt.timedelta(days=20)}
    mid = {"n_seg": 9, "last_end": WE - _dt.timedelta(days=8)}
    ck(seat_reason(None, fresh["last_end"], WE) == REASON_NO_DATA, "座不在事實裡 ⇒ 無資料")
    ck(seat_reason({"n_seg": 0, "last_end": None}, None, WE) == REASON_NO_DATA,
       "n_seg==0 ⇒ 無資料")
    ck(seat_reason(old, WE - _dt.timedelta(hours=1), WE) == REASON_STALE,
       "末段早於 win_end−14d ⇒ 陳舊（整台還活著也一樣）")
    ck(seat_reason(mid, WE - _dt.timedelta(hours=1), WE) == REASON_NOT_FRESH,
       "末段落在 [−14d, −3d) ⇒ 新鮮度不足")
    ck(seat_reason(fresh, WE - _dt.timedelta(hours=1), WE) is None, "新鮮 ⇒ 無註記")
    ck(seat_reason(fresh, WE - _dt.timedelta(days=9), WE) == REASON_NOT_FRESH,
       "★ 整台機 max(last_end) 落後 3 天 ⇒ 該座數字照印、但標新鮮度不足")
    ck(set(SEAT_REASONS) == {REASON_NO_DATA, REASON_STALE, REASON_NOT_FRESH}
       and len(SEAT_REASONS) == 3, "★ 原因詞只有三個（不得發明第四個）")
    ck(machine_last_end({"nest:1": old, "nest:2": fresh}) == fresh["last_end"],
       "machine_last_end 取 max")
    ck(machine_last_end({}) is None and machine_last_end(None) is None, "全空 ⇒ None")

    # ---------- (E) seat_row：口徑與「—」----------
    r = seat_row("EID-00425", "nest:3",
                 {"n_seg": 10, "n_prodgrade": 8, "n_pass": 6, "n_fail": 2,
                  "last_end": WE, "first_start": WE}, None)
    ck(r["output"] == 8 and r["good"] == 6, "產出＝prod-grade 段數、良品＝n_pass")
    ck(abs(r["quality_pct"] - 75.0) < 1e-9, "良率＝pass/(pass+fail)，只算 prod-grade")
    ck(all(r[k] is None for k in ("availability", "utilization", "util", "oee")),
       "★ C-CF5／C-CF8：座列 A／U／OEE 恆 None")
    ck(r["has_util"] is False and r["util_unmeasurable"] is False and r["comp"] == [],
       "★ 座列不得走 util_unmeasurable／bar（那是別的語意）")
    ck(r["is_seat"] is True and r["seat_unit"] == "nest:3" and r["seat_nest"] == 3,
       "座列的三個識別鍵")
    ck(r["parent_eid"] == "EID-00425", "parent_eid＝母機（縮排/群組與子 EID 同形）")
    rz = seat_row("EID-00425", "nest:2", None, REASON_NO_DATA)
    ck(rz["output"] is None and rz["good"] is None and rz["quality_pct"] is None,
       "無資料 ⇒ 三個數字都印「—」")
    rs = seat_row("EID-00425", "nest:1", {"n_seg": 3, "n_prodgrade": 3, "n_pass": 3,
                                          "n_fail": 0, "last_end": WE}, REASON_STALE)
    ck(rs["output"] is None and rs["seat_last_end"] == "2026-09-12 08:00",
       "陳舊 ⇒ 數字「—」、末段時刻進 title")
    rn = seat_row("EID-00425", "nest:4", {"n_seg": 3, "n_prodgrade": 3, "n_pass": 3,
                                          "n_fail": 1, "last_end": WE}, REASON_NOT_FRESH)
    ck(rn["output"] == 3 and rn["good"] == 3, "★ 新鮮度不足 ⇒ 數字照印（只加註記）")
    r0 = seat_row("EID-00425", "nest:4", {"n_seg": 3, "n_prodgrade": 0, "n_pass": 0,
                                          "n_fail": 0, "last_end": WE}, None)
    ck(r0["quality_pct"] is None, "良率分母 0 ⇒ None（不印 0%）")

    # ---------- (F) seat_rows_for_units：名冊驅動、插入位置、identity ----------
    parent = {"eid": "EID-00425", "output": 111, "good": 100}
    other = {"eid": "EID-70000", "output": 5, "good": 5}
    seats2 = {"EID-00425": {"nest:3": {"n_seg": 5, "n_prodgrade": 5, "n_pass": 5,
                                       "n_fail": 0, "last_end": WE}}}
    new, has = seat_rows_for_units([parent, other], "segLamination", seats2, WE, REG)
    ck(has is True, "有名冊座 ⇒ has_seats")
    ck([r.get("seat_unit") for r in new]
       == [None, "nest:1", "nest:2", "nest:3", "nest:4", None,
           "nest:1", "nest:2", "nest:3", "nest:4"],
       f"母機在表上 ⇒ 座列緊接其後；母機不在表上 ⇒ 整組附表尾。got={[r.get('seat_unit') for r in new]}")
    ck(new[0] is parent and new[5] is other,
       "★ 母機列 dict **原物件**未被替換（逐位元不變、不進任何 Σ）")
    ck(all(r["seat_orphan"] is False for r in new[1:5])
       and all(r["seat_orphan"] is True for r in new[6:]),
       "母機不在表上的那台標 seat_orphan")
    ck(all(r["parent_eid"] == "EID-26596" for r in new[6:]), "orphan 組的母機是 26596")
    ck(new[3]["output"] == 5 and new[1]["output"] is None,
       "★ 有事實的座印數字、名冊上沒事實的座印「—」（列由名冊驅動、格由資料填）")
    n2, h2 = seat_rows_for_units([parent], "segNoSuchStation", seats2, WE, REG)
    ck(n2 == [parent] and h2 is False, "名冊外站別 ⇒ 零座列、原表原樣")
    n3, _ = seat_rows_for_units([parent, other], "segLamination", seats2, WE, REG,
                                allow_eids={"EID-00425"})
    ck({r.get("parent_eid") for r in n3 if r.get("is_seat")} == {"EID-00425"},
       "?tag= 有效 ⇒ 只留仍在表上的母機的座")
    n4, h4 = seat_rows_for_units([], "segLamination", {}, WE, REG)
    ck(h4 is True and len(n4) == 8 and all(r["seat_reason"] == REASON_NO_DATA for r in n4),
       "★ 零事實仍成 8 列（不得為事實不足隱藏座）")

    # ---------- (G) demo／snapshot ----------
    # WP-10d（R-P11）：demo 樣例座釘（DEMO_SEAT_PINS）的鍵是名冊常數；公開樹是合成名冊 ⇒ 樣例機台與座號由釘表推導
    #   （私有樹推出來＝EID-26596 nest:2／EID-00425 nest:1、REG 照用上面那份，一位元不動）。
    import core_paths as _wp
    _pin_nd = next(k for k, v in DEMO_SEAT_PINS.items() if v == REASON_NO_DATA)
    _pin_st = next(k for k, v in DEMO_SEAT_PINS.items() if v == REASON_STALE)
    if _wp.is_public_tree():
        _nn = max(4, *(int(k[1].split(":")[1]) for k in (_pin_nd, _pin_st)))
        REG_D = {e: {"var": "varLAM_NestID", "main_cls": "segLamination", "nests": tuple(range(1, _nn + 1))}
                 for e in dict.fromkeys((_pin_nd[0], _pin_st[0]))}
    else:
        REG_D = REG
    d1 = demo_seats("segLamination", WE, REG_D)
    d2 = demo_seats("segLamination", WE, REG_D)
    ck(d1 == d2, "demo 確定性（同輸入同輸出）")
    ck(_pin_nd[1] not in d1.get(_pin_nd[0], {}), "demo 樣例：26596 nest2 無資料")
    dn, _ = seat_rows_for_units([], "segLamination", d1, WE, REG_D)
    got = {(r["parent_eid"], r["seat_unit"]): r["seat_reason"] for r in dn}
    ck(got[_pin_nd] == REASON_NO_DATA, "demo 看得到「無資料」")
    ck(got[_pin_st] == REASON_STALE, "demo 看得到「陳舊」")
    ck(sum(1 for v in got.values() if v is None) >= 5, "其餘座是新鮮的（有數字可看）")
    snap = load_seats("snapshot", "segLamination", 7, REG, now=WE)
    ck(snap["seats"] == {} and snap["win_end"] == WE, "snapshot ⇒ 空事實（不是降級、是事實）")
    sn, _ = seat_rows_for_units([], "segLamination", snap["seats"], WE, REG)
    ck(all(r["seat_reason"] == REASON_NO_DATA and r["output"] is None for r in sn),
       "★ snapshot 座列全「—（無資料）」")
    ck(load_seats("demo", "segNoSuchStation", 7, REG)["seats"] == {},
       "名冊外站別 ⇒ 連合成都不做")

    # ---------- (H) fetch 走的是同一條文本（假 cursor、零 MES）----------
    class _Cur:
        def __init__(self):
            self.calls = []

        def execute(self, sql_, *args):
            self.calls.append((sql_, args))

        def fetchall(self):
            return [("EID-00425", t, t, "Production", "PASS", 2)]

    cur = _Cur()
    out = fetch_nest_seat_rows("segLamination", "varLAM_NestID",
                               WE - _dt.timedelta(days=7), WE, cur=cur)
    ck(cur.calls[0][0] == build_seat_sql()[0], "fetch 用的就是 build_seat_sql 的文本")
    ck(cur.calls[0][1] == ("varLAM_NestID", "segLamination",
                           WE - _dt.timedelta(days=7), WE), "參數序＝(var, cls, start, end)")
    ck(len(out) == 1, "回原始段列、零聚合")

    # ---------- (I) 形② co-tag 子 EID（`ROADMAP` #126；名冊＝真的 COTAG_CHILD_UNITS）----------
    #   ⚠ 這一組刻意用**真名冊**（不是假的 REG）——形② 的整個價值就是「名冊上有、頁面上也要有」，
    #     用假名冊驗等於驗一件沒人在乎的事。零 MES（只讀 dict 與文本）。
    LEAK = "segLamLeakTest"
    co_rows = roster_for_cls(LEAK, REG)
    ck(len(co_rows) == 2 and all(v is None for _e, v, _u in co_rows),
       f"★ 形② 投影進同一份名冊清單，`var is None` 就是記號：{co_rows}")
    ck([e for e, _v, _u in co_rows] == sorted(_wcap.COTAG_CHILD_UNITS)
       or {e for e, _v, _u in co_rows} == set(_wcap.COTAG_CHILD_UNITS),
       "形② 的母機＝COTAG_CHILD_UNITS 的鍵（零第二份名冊）")
    ck(all(set(u) == set(_wcap.COTAG_CHILD_UNITS[e]) for e, _v, u in co_rows),
       "形② 的 unit＝子 EID 字串本身（不轉成 nest:n）")
    ck(all(nest_no(u) is None for _e, _v, units in co_rows for u in units),
       "★ 形② 的 unit 過不了 nest_no ⇒ seat_nest is None＝模板判形的唯一依據")
    ck(cls_has_seats(LEAK, REG) is True, "Leak Test 自此有座（cls_has_seats 為 True）")
    ck(roster_for_cls(LEAK, REG, cotag={}) == () and cls_has_seats(LEAK, REG, cotag={}) is False,
       "★ 形② 名冊清空 ⇒ 該站零座列（變異測試的另一半）")
    ck(roster_for_cls("segLamination", REG)
       == roster_for_cls("segLamination", REG, cotag={}),
       "★ 形② 一個位元都沒動到形① 的站別（其餘 35 站逐位元原行為）")

    # ---------- (I-2) build_cotag_sql：兩處差異、零新猜欄 ----------
    csql, cnames = build_cotag_sql()
    ck(cnames == ("eid", "cls", "start", "end"), f"參數序＝文本裡 ? 的順序；got {cnames}")
    ck(csql.count("?") == 4, f"四個參數位；got {csql.count('?')}")
    ck("se.astEquipmentID = ?" in csql, "★ 差異 2：逐子 EID 點查的等值述詞")
    ck(" IN (" not in csql, "零 IN-list（B2 事故形＝一長串 EID 餵給 view，不是單值等值）")
    for gone in ("segment_integer_data_t", "production_data_definition_t",
                 "sgiInteger", "vdfProductionDataID"):
        ck(gone not in csql, f"★ 差異 1：形② 不查 EAV 座號 ⇒ {gone} 整個不出現")
    for bad in ("DISTINCT", "GROUP BY", "MAX(", "ORDER BY", "COUNT(", "SUM("):
        ck(bad not in csql.upper(), f"view 上零聚合：{bad} 不得出現")
    ck("stpEndTime >= se.stpStartTime" in csql, "零工期段：>= 不是 >")
    ck(csql.rstrip().endswith("OPTION (RECOMPILE)"), "尾 OPTION (RECOMPILE)")
    ck("JOIN" not in csql.upper(), "形② 零 join（座就是 EID）")
    _seat_sql = build_seat_sql()[0]
    ck(all(tok in _seat_sql for tok in
           ("dbo.segment_equipment_v se", "se.astEquipmentID", "se.stpStartTime",
            "se.stpEndTime", "se.matDesignation", "se.stpTestResult",
            "se.SegmentClassID")),
       "（前提）出處表列的那幾欄確實都在 build_seat_sql 文本裡")
    ck(all(tok in _seat_sql for tok in
           [x.strip() for x in csql.split("SELECT ")[1].split("\n")[0].split(",")]),
       "★★ 形② 的每一欄都在 build_seat_sql 的出處表裡＝**零新猜欄**"
       "（[[ssms-sql-column-provenance]]）")
    ck("matDesignation" not in build_cotag_sql(with_designation=False)[0],
       "降級形沒有 designation")
    ck(build_cotag_sql()[0] == csql and csql != _seat_sql, "同輸入逐位元同 SQL、且與形① 不同條")

    # ---------- (I-3) aggregate：同一支、unit_of 對照表 ----------
    # WP-10d（R-P11）：形② 的母機／子 EID 由名冊常數推導（私有樹＝EID-08158〔子 05707、07757〕／EID-25402；公開樹＝合成名冊）
    _cp1, _cp2 = list(_wcap.COTAG_CHILD_UNITS)[:2]
    _ck1, _ck2 = tuple(_wcap.COTAG_CHILD_UNITS[_cp1]), tuple(_wcap.COTAG_CHILD_UNITS[_cp2])
    UO = {c: (_cp1, c) for c in _ck1[:2]}
    crows = [
        (_ck1[0], t, t, "Production", "PASS"),
        (_ck1[0], t, t + _dt.timedelta(hours=1), "Production", "FAIL"),
        (_ck1[0], t, t, "Dummy", "PASS"),                 # 非 prod-grade：只進 n_seg
        (_ck1[1], t, t, "Production", "PASS"),
        ("EID-99998", t, t, "Production", "PASS"),            # 對照表外 ⇒ 丟、且計數
    ]
    cseats, cdiag = aggregate_nest_seats(crows, REG, WE, unit_of=UO)
    ck(set(cseats) == {_cp1}, f"★ 事實掛在**母機**底下：{list(cseats)}")
    ck(set(cseats[_cp1]) == {_ck1[0], _ck1[1]},
       "★ unit＝子 EID 字串（不是 nest:n）")
    ca = cseats[_cp1][_ck1[0]]
    ck(ca["n_seg"] == 3 and ca["n_prodgrade"] == 2 and ca["n_pass"] == 1 and ca["n_fail"] == 1,
       f"prod-grade 與良率兩欄的口徑與形① 同一條路：{ca}")
    ck(ca["last_end"] == t + _dt.timedelta(hours=1), "last_end 在 Python 取 max（view 零聚合）")
    ck(cdiag["dropped_eid"] == 1 and cdiag["n_rows"] == 5,
       f"★ 對照表外一律丟棄**並計數**（安靜丟掉的列最貴）：{cdiag}")
    ck(aggregate_nest_seats([(_ck1[0], WE, WE, "Production", "PASS")], REG, WE,
                            unit_of=UO)[1]["dropped_shape"] == 1,
       "起點 >= win_end 的列不計（兩形共用同一個防禦性上界）")
    ck(aggregate_nest_seats([(_ck1[0], t, t)], REG, WE, unit_of=UO)[1]["dropped_shape"] == 1,
       "欄數不足 ⇒ dropped_shape（不是靜默吃掉）")
    ck(aggregate_nest_seats(crows, REG, WE)[1]["dropped_shape"] == 5,
       "★ 不帶 unit_of ⇒ 五欄列全部走 dropped_shape（形① 路徑逐位元未變）")
    ck(machine_last_end(cseats[_cp1]) == t + _dt.timedelta(hours=1),
       "machine_last_end 取兩個子 EID 的 max（#126 (3)）")

    # ---------- (I-4) 列形：seat_nest None、A/U/OEE 恆「—」----------
    cr = seat_row(_cp1, _ck1[0], ca, None)
    ck(cr["is_seat"] is True and cr["seat_unit"] == _ck1[0] and cr["seat_nest"] is None,
       f"★ 形② 列：seat_nest is None、seat_unit＝子 EID：{ {k: cr[k] for k in ('seat_unit', 'seat_nest')} }")
    ck(cr["output"] == 2 and cr["good"] == 1 and abs(cr["quality_pct"] - 50.0) < 1e-9,
       "產出＝該子 EID 的 prod-grade **段數**（不是 PH）、良品／良率同形①")
    ck(all(cr[k] is None for k in ("availability", "utilization", "util", "oee"))
       and cr["has_util"] is False and cr["util_unmeasurable"] is False and cr["comp"] == [],
       "★★ C-CF8／C-CF5：形② 的 A／U／OEE **恆 None**（子 EID 技術上量得到＝範圍選擇不是物理限制）")
    ck(cr["parent_eid"] == _cp1, "parent_eid＝母機")
    ck(seat_row(_cp1, _ck1[0], ca, REASON_STALE)["output"] is None
       and seat_row(_cp1, _ck1[0], ca, REASON_NOT_FRESH)["output"] == 2,
       "三個原因詞的「空／不空」規則與形① **完全共用**（seat_reason 一個位元不動）")

    # ---------- (I-5) seat_rows_for_units：插入位置／orphan／?tag= 逐位元沿用 ----------
    lk_parent = {"eid": _cp1, "output": 900, "good": 880}
    cn, chas = seat_rows_for_units([lk_parent], LEAK, cseats, WE, REG)
    ck(chas is True and cn[0] is lk_parent, "母機列是**原物件**（不進站級任何 Σ）")
    ck([r.get("seat_unit") for r in cn[1:1 + len(_ck1)]] == list(_ck1)
       and all(r["seat_orphan"] is False for r in cn[1:1 + len(_ck1)]),
       f"母機在表上 ⇒ 兩子列緊接其後：{[r.get('seat_unit') for r in cn]}")
    ck([r.get("seat_unit") for r in cn[1 + len(_ck1):]] == list(_ck2)
       and all(r["seat_orphan"] is True for r in cn[1 + len(_ck1):]),
       "另一台母機不在表上 ⇒ 整組附表尾並標 orphan（demo 一定走這條）")
    ck(all(r["seat_reason"] == REASON_NO_DATA and r["output"] is None for r in cn[1 + len(_ck1):]),
       "★ 名冊上有、事實裡沒有 ⇒ 照列、印「—（無資料）」（不得為事實不足隱藏座）")
    cn2, _ = seat_rows_for_units([lk_parent], LEAK, cseats, WE, REG,
                                 allow_eids={_cp1})
    ck({r.get("parent_eid") for r in cn2 if r.get("is_seat")} == {_cp1},
       "?tag= 有效 ⇒ 只留仍在表上的母機的子列（§11.5 逐位元沿用）")
    cn3, ch3 = seat_rows_for_units([lk_parent], LEAK, {}, WE, REG, cotag={})
    ck(cn3 == [lk_parent] and ch3 is False, "★ 形② 名冊清空 ⇒ 原表原樣（變異測試）")

    # ---------- (I-6) demo／snapshot：同一個迴圈、零特判 ----------
    cd1 = demo_seats(LEAK, WE, REG)
    ck(cd1 == demo_seats(LEAK, WE, REG), "demo 確定性（同輸入同輸出）")
    ck(set(cd1) == set(_wcap.COTAG_CHILD_UNITS)
       and all(set(v) == set(_wcap.COTAG_CHILD_UNITS[k]) for k, v in cd1.items()),
       f"★ demo 對形② 也合成（四座全有）：{ {k: sorted(v) for k, v in cd1.items()} }")
    cdn, _ = seat_rows_for_units([], LEAK, cd1, WE, REG)
    ck(len(cdn) == len(_ck1) + len(_ck2) and all(r["seat_orphan"] is True and r["output"] is not None
                             for r in cdn),
       "demo 機隊沒有這兩台母機 ⇒ 四列 orphan、但都有合成數字（誠實：demo 本來就沒有這兩台）")
    csnap = load_seats("snapshot", LEAK, 7, REG, now=WE)
    csn, _ = seat_rows_for_units([], LEAK, csnap["seats"], WE, REG)
    ck(len(csn) == len(_ck1) + len(_ck2) and all(r["seat_reason"] == REASON_NO_DATA and r["output"] is None
                             for r in csn),
       "★ snapshot 形② 四列全「—（無資料）」（是事實不是降級）")

    # ---------- (I-7) fetch_cotag_rows 走的是同一條文本（假 cursor、零 MES）----------
    cur2 = _Cur()
    out2 = fetch_cotag_rows(_ck1[0], LEAK, WE - _dt.timedelta(days=7), WE, cur=cur2)
    ck(cur2.calls[0][0] == build_cotag_sql()[0], "fetch 用的就是 build_cotag_sql 的文本")
    ck(cur2.calls[0][1] == (_ck1[0], LEAK, WE - _dt.timedelta(days=7), WE),
       f"★ 參數序＝(eid, cls, start, end)：{cur2.calls[0][1]}")
    ck(len(out2) == 1, "回原始段列、零聚合")

    print(f"board_nest selftest OK（{n} 斷言）")
    return n


def main():
    ap = argparse.ArgumentParser(description="座級產出／良品讀取層（#110 (3) 形①＋#126 形②）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（零 MES）")
    ap.add_argument("--sql", action="store_true", help="印出兩條固定審過的 SQL 文本（給域內 SSMS 審）")
    args = ap.parse_args()
    if args.sql:
        # **兩條都印**（先 nest 後 cotag、各有標題行）——域內審 SQL 的人要看得到這一頁
        # 到底送出哪幾條；只印一條就是把另一條藏起來。
        for title, fn in (("build_seat_sql —— 形① nest-EAV（`NEST_EAV_UNITS`；逐 var 點查）",
                           build_seat_sql),
                          ("build_cotag_sql —— 形② co-tag 子 EID（`COTAG_CHILD_UNITS`；逐 EID 點查）",
                           build_cotag_sql)):
            sql, names = fn()
            print("-- ═══ " + title + " ═══")
            print("-- params: " + ", ".join(names))
            print(sql)
            print()
        return 0
    if args.selftest:
        _selftest()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
