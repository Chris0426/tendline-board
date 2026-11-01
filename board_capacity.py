# -*- coding: utf-8 -*-
"""
board_capacity.py — 設備子單元產能名冊與排程狀態 as-of 投影（純函式、零 I/O、selftest）
=========================================================================================

規格權威＝`SPEC-RUNWINDOW.md` §10.8／`SPEC-DORMANT-CAPACITY.md` §11.5／
`CONCEPT.md` §19.3 A13。

═══ 這在答什麼問題 ═══════════════════════════════════════════════════════════
  「這台設備實裝幾個可獨立停排的子單元、D 日其中幾個仍排產？」本檔以一份宣告式名冊
  統一回答。MES 有三種形狀：整數 EAV nest、同段 co-tag 的子 EID、以及母機展開的 module
  EID；後者直接 import `board_scope.PARENT_EXPANSION`，絕不另抄一份會漂移的字典。

═══ 規則 ═════════════════════════════════════════════════════════════════════
  1. `UNIT_ID`：nest-EAV 用 ``nest:<n>``；co-tag/module 子單元直接用 child EID。
  2. 軸＝Asia/Taipei 日曆日。D 日長停＝D 日台北日終以前最後一筆 effective 事件為
     ``not_scheduled``。輸入是 `_a11_adapt` 產出的 naive `effective_date`，**不是** wire
     `ts_effective`；adapter 把日期放進本地事件形的 `ts_effective` 鍵。Board 不做 UTC/+08 換算，
     aware/offset 輸入一律拒絕。
  3. nest-EAV 的無 ``sub_unit`` 事件作用於全部 nests；同一時刻以輸入順序較後者定調。
     co-tag/module 是獨立 EID，逐 child 委派給 `board_dormant.is_dormant`，母機事件不傳遞。
  4. 沒有事件＝全部在役。狀態是登記事實，永不由 MES 沉默、astStatus 或活動門檻推導（C-D1）。
  5. alias 與 serial stage 只由 `primary_of` 摺回實體主機；它們自己不另長一份 unit 名冊。

# 消費紀律
  消費者＝未來 `/equipment` capacity chip、`/utilization` 分母、downtime-equivalent coefficient。
  本名冊是 unit list 的唯一住處；templates 或其他模組不得複製第二份。`equiv_units_factor`
  只提供係數、尚未接線。永不從 MES silence 推論 dormant（C-D1），所有投影一律由呼叫者傳 day，
  函式內不讀時鐘。本檔與 `board_dormant` 使用完全相同的 as-of 規則；本檔只多加沒有自身
  EID 的 `nest:<n>` 軸。帶 `sub_unit` 的列**絕不可**直接餵給 `board_dormant.is_dormant`
  （它不認得 `sub_unit`，會把整台機誤標長停）；只有 child-EID 的無 sub_unit 列能委派。
"""
import argparse
import datetime as _dt
import inspect
import re
import sys

import mes_timeline as _mt          # 只取常數（EID_RENAME／EID_ALIAS_DROP）；import 無副作用、不連 MES
import board_dormant as _dormant
import board_scope as _scope

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


STATE_NOT_SCHEDULED = "not_scheduled"
STATE_IN_PRODUCTION = "in_production"


# ---------------------------------------------------------------------------
# 唯一子單元名冊（逐列 evidence source；不要複製到 template）
#
# ★★ **C-LD46 子工位＝物理工位**（Chris 2026-09-08 深夜；`SPEC-LANDING.md` §18.30.4）
#    只有**會各自故障、各自算產能**的座才進 `NEST_EAV_UNITS`／`COTAG_CHILD_UNITS`；
#    **載盤（tray）slot、追蹤代號、量測序號一類永不進名冊**。
#    ⚠ **MES 有 nest 變數不等於有子工位**——名冊列要有 Chris 認過的**物理事實**；
#    首例＝AOI 三台（`EID-09763`／`EID-26161`／`EID-38316`）於 2026-09-08 深夜退出（見下方註解保留的三列）。
# ---------------------------------------------------------------------------

NEST_EAV_UNITS = {'EID-54769': {'var': 'varNestPosition', 'main_cls': 'segFrameBonding', 'nests': (1, 2, 3, 4)},
                  'EID-54336': {'var': 'varNestSlot',
                                'main_cls': 'segLamination',
                                'nests': (1, 2, 3, 4, 5, 6)},
                  'EID-54466': {'var': 'varNestSlot', 'main_cls': 'segLamination', 'nests': (1, 2, 3)},
                  'EID-55593': {'var': 'varStationIndex',
                                'main_cls': 'segFlexBonding',
                                'nests': (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)},
                  'EID-54798': {'var': 'varStationIndex', 'main_cls': 'segFlexBonding', 'nests': (1, 2, 3, 4)}}

# cmms NOTE-71 seat asset IDs have exactly one parser: this module owns both
# the physical-seat roster and the wire-to-unit mapping. Consumers must call
# `fold_seat_id`, never copy this regex or parse the `-N` suffix themselves.
SEAT_ID_RE = re.compile(r"^(EID-\d{5})-N([1-9]\d?)$")

COTAG_CHILD_UNITS = {'EID-54519': ('EID-55528', 'EID-54998', 'EID-54891'),
                     'EID-55462': ('EID-55508', 'EID-54899')}

# 形② 母機的主站別。`NEST_EAV_UNITS` 每一列本來就帶 `main_cls`，形② 沒有地方放
# ⇒ 補在這裡（**同一份名冊的第二欄，不是第二份名冊**）。值只有一個用途：
# 給 `SUBUNIT_AXIS` 當鍵。⚠ 不接進 `app._ld_station_map`（那會替這兩台長出一條
# 站頁連結＝另一個裁決，不順手做）。
# Evidence: _NEST-PROBE-41-DISCUSSION.md §2b（Leak Test 母子）＋
#   `mes_timeline.EQP_STATIONS` 含 `segLamLeakTest`。
COTAG_MAIN_CLS = {'EID-54519': 'segLamLeakTest', 'EID-55462': 'segLamLeakTest'}

# ═══ 子工位在畫面上的排列軸（Chris 2026-09-08 裁；`SPEC-LANDING.md` §18.23）═════════
#   **唯一出處**：顯示層（landing tile／`_gen_layout` 母機等分）一律讀這裡，不得各自判斷。
#   · `"y"` ⇒ 由下往上排、**nest 1 在最下**；`"x"` ⇒ 由左往右排、**nest 1 在最左**。
#   · 鍵＝該子工位所屬機台的**主站別**（`subunit_main_cls`）；查無 ⇒ `SUBUNIT_AXIS_DEFAULT`。
#   ⚠ 這是**版面規則不是量測**：Chris 看實體機台的排列方向定的（Leak Test 與 Frame Bond
#     的座位在機台上是上下疊、其餘是左右排），不要拿長寬比反推。
#   ★★ **2026-09-09 新增第三個值 `"grid"`（#121；`SPEC-LANDING.md` §18.31）**：
#      Chris 原句「/landing layout Press 二台的 4 nest 改成四格分布（也就是機台圖示切十字，
#      分成四塊），就只是改 layout 中二台 Press 的圖示，其他不變」。
#      · 語意＝**兩欄、順時針（蛇形／boustrophedon）、由左上開始**：`cols=2`、
#        `rows=ceil(k/2)`、index i ⇒ `row = i//2`、`col = i%2 if row%2==0 else 1-(i%2)`
#        （row 0 由左到右、row 1 由右到左、row 2 再由左到右…），row 0 在最上
#        ⇒ k=4 正好是「切十字、四塊」且**順時針**（nest 1 左上、2 右上、3 右下、4 左下）。
#      · ⚠ **2026-09-09 Chris 二裁**（原句「格序改為: nest 1 左上、2 右上、3 右下、4 左下」）：
#        第一版的列優先（1 左上、2 右上、3 左下、4 右下）**已作廢**；只有第 3／4 格對調，
#        幾何尺寸、pad、退回 `x` 內帶的門檻一個位元不變。
#      · **只有 Press（`segLamination`）用它**；Flex Bonding／Leak Test／Frame 三種軸與
#        幾何一個位元不變（那三種仍是 `x`／`y`）。
#      · 幾何仍在 `app.ld_subunit_geometry`：格短邊撐不到 `LD_CELL_FULL_MIN` ⇒ **退回 `"x"`
#        的現行行為**（內帶），不發明第三種畫法。
SUBUNIT_AXIS_DEFAULT = "x"
SUBUNIT_AXIS = {'segLamLeakTest': 'y', 'segFrameBonding': 'y', 'segLamination': 'grid'}
# 形③ 母機展開（CAL／CLN Gen2）的等分軸。**2026-09-08 起顯式**：`_gen_layout.split_shared`
# 原本由外框長寬比推（`w >= h`），三台實測都推成 `"x"` ⇒ 顯式化**不改任何座標**
# （`layout_eid_xy.json` 的 `parents[*].axis` 三筆本來就是 `"x"`，selftest／smoke 各一根釘）。
# 顯式的價值：哪天有人換了 DXF、母機外框變成直的，槽位順序不會**靜默**翻面。
PARENT_EXPANSION_AXIS = "x"

# Evidence: board_scope.py A10 governed parent/module register. Identity is intentional.
PARENT_EXPANSION = _scope.PARENT_EXPANSION

SERIAL_UNITS = {'EID-54804': 'EID-55546'}

# 形④ 別名。⚠ **值域的唯一出處分兩半，兩半都不在本檔自己長**：
#   · Aligner×3 ＝ MES 讀取層**用不到**（`dependent-equipment export` 已把它們當模組排除掉、
#     主 EID 恆為同窗候選＝子集性質保證）⇒ 只在 WO／`primary_of` 面需要摺回主機，住這裡。
#   · Top O-ring 的 `EID-38072` ＝ **MES 讀取層要主動丟**（那層沒有邊可用）
#     ⇒ 值住 `mes_timeline.EID_ALIAS_DROP`，本檔 **import 同一個物件**、絕不另抄。
_ALIGNER_ALIASES = {'EID-54400': 'EID-54791', 'EID-55301': 'EID-54573'}
ALIASES = dict(_ALIGNER_ALIASES)
ALIASES.update(_mt.EID_ALIAS_DROP)      # EID-38072 -> EID-38106（單一事實源）

# 形④ 的另一半：**改名**（MES 計數主 → 名冊列）。`EID-38106` 在 MES 沒有自己的段，
# `EID-38041` 才是計數主 ⇒ 讀取層改名、本檔 import 同一張表，`primary_of` 一併折過去。
# Evidence: mes_timeline.EID_RENAME（SPEC-RUNWINDOW.md §10.8-4／§10.8-8）。
RENAME = _mt.EID_RENAME


# ---------------------------------------------------------------------------
# Register API
# ---------------------------------------------------------------------------

def fold_seat_id(asset_id):
    """Fold a cmms NOTE-71 seat asset ID to ``(parent_eid, unit_id, seat_known)``.

    This is the **only** parser for cmms ``<EID>-N<n>`` seat asset IDs. A
    non-seat value is returned unchanged with ``(None, True)`` so callers can
    apply it freely without changing ordinary asset IDs. A syntactically valid
    but unregistered seat still folds to its real parent machine; only
    ``seat_known`` becomes false.
    """
    if not isinstance(asset_id, str):
        return (asset_id, None, True)
    match = SEAT_ID_RE.fullmatch(asset_id)
    if match is None:
        return (asset_id, None, True)
    parent = match.group(1)
    nest_no = int(match.group(2))
    seat_known = parent in NEST_EAV_UNITS and nest_no in NEST_EAV_UNITS[parent]["nests"]
    return (parent, "nest:%d" % nest_no, seat_known)


def event_seat_units(parent_eid, scope_members, sub_units):
    """Return the sorted named-seat union for one event (C-RW28; NOTE-79).

    ``sub_units`` and NOTE-79 seat asset IDs carried in ``scope_members`` name
    the same additive seat set.  Only scope IDs which :func:`fold_seat_id`
    assigns to *parent_eid* contribute; malformed values and other parents are
    deliberately ignored.  This keeps NOTE-71 ``-N`` parsing solely here, via
    :func:`fold_seat_id` (C-RW20), while consumers retain the roster as their
    separate C-CF3 authority.
    """
    sub_values = () if isinstance(sub_units, (str, bytes)) else (sub_units or ())
    scope_values = () if isinstance(scope_members, (str, bytes)) else (scope_members or ())
    try:
        units = {u for u in sub_values if isinstance(u, str) and u}
    except TypeError:
        units = set()
    try:
        for member in scope_values:
            parent, unit, _known = fold_seat_id(member)
            if parent == parent_eid and isinstance(unit, str) and unit:
                units.add(unit)
    except TypeError:
        pass
    return tuple(sorted(units))


def seat_wire_id(eid, unit_id):
    """Return cmms NOTE-71's seat asset ID for a ``nest:<n>`` unit.

    This is deliberately the inverse *formatter* of :func:`fold_seat_id`, not
    a second roster lookup: syntactically valid seat IDs still fold to their
    parent even when that seat is not currently registered.  Callers that need
    the physical census must use :func:`installed_units` separately.  Non-nest
    units (co-tag children and parent-expansion EIDs) have no NOTE-71 seat-ID
    spelling and return ``None``.
    """
    if not isinstance(eid, str) or not isinstance(unit_id, str):
        return None
    match = re.fullmatch(r"nest:([1-9]\d*)", unit_id)
    if match is None:
        return None
    return "%s-N%d" % (eid, int(match.group(1)))


def fold_seat_members(members):
    """Return a deduplicated parent-EID set for cmms scope-event members.

    Seat parsing remains solely in :func:`fold_seat_id`; this helper exists so
    scope consumers never reimplement the NOTE-71 `-N` rule.
    """
    return frozenset(fold_seat_id(member)[0] for member in members)

def installed_units(eid):
    """Return the declared UNIT_ID tuple for *eid*, or ``()`` when it has no units."""
    if eid in NEST_EAV_UNITS:
        return tuple("nest:%d" % n for n in NEST_EAV_UNITS[eid]["nests"])
    if eid in COTAG_CHILD_UNITS:
        return COTAG_CHILD_UNITS[eid]
    if eid in PARENT_EXPANSION:
        return PARENT_EXPANSION[eid]
    return ()


def unit_kind(eid):
    """Return ``nest_eav``, ``cotag_children``, ``parent_expansion``, or ``None``."""
    if eid in NEST_EAV_UNITS:
        return "nest_eav"
    if eid in COTAG_CHILD_UNITS:
        return "cotag_children"
    if eid in PARENT_EXPANSION:
        return "parent_expansion"
    return None


def machines_with_units():
    """All machine EIDs represented by this register, sorted."""
    return tuple(sorted(set(NEST_EAV_UNITS) | set(COTAG_CHILD_UNITS) | set(PARENT_EXPANSION)))


def subunit_main_cls(eid):
    """機台 → 其子工位所屬的 MES 主站別；不在名冊（或形③ 母機）⇒ ``None``。

    形① 讀 `NEST_EAV_UNITS[eid]["main_cls"]`、形② 讀 `COTAG_MAIN_CLS`；
    形③（`PARENT_EXPANSION`）的子女各有自己的 EID 與站別，母機本身無主站別 ⇒ None。
    """
    if eid in NEST_EAV_UNITS:
        return NEST_EAV_UNITS[eid].get("main_cls")
    if eid in COTAG_CHILD_UNITS:
        return COTAG_MAIN_CLS.get(eid)
    return None


def subunit_axis(eid):
    """機台 → 子工位排列軸 ``"x"``／``"y"``／``"grid"``（純查表；不看幾何、不看長寬比）。

    · 形①／形②：查 `SUBUNIT_AXIS[subunit_main_cls(eid)]`，查無 ⇒ `SUBUNIT_AXIS_DEFAULT`。
    · 形③（`PARENT_EXPANSION` 母機）：`PARENT_EXPANSION_AXIS`（顯式 `"x"`）。
    · 沒有子工位的機台 ⇒ 仍回預設軸（呼叫端先問 `installed_units` 才畫，
      這裡不用 None 表達「沒有子工位」——那件事已經有一個更好的問法）。
    """
    if eid in PARENT_EXPANSION and eid not in NEST_EAV_UNITS and eid not in COTAG_CHILD_UNITS:
        return PARENT_EXPANSION_AXIS
    cls = subunit_main_cls(eid)
    return SUBUNIT_AXIS.get(cls, SUBUNIT_AXIS_DEFAULT)


def primary_of(eid):
    """Fold a rename, alias or serial stage into its primary machine; otherwise return *eid*.

    Rename is checked first: ``EID-38041`` is the MES counting EID for the roster row
    ``EID-38106``, so it folds even though it is not a 形④ alias itself."""
    if eid in RENAME:
        return RENAME[eid]
    if eid in ALIASES:
        return ALIASES[eid]
    if eid in SERIAL_UNITS:
        return SERIAL_UNITS[eid]
    return eid


def is_alias(eid):
    return eid in ALIASES


def is_renamed(eid):
    """True for a MES counting EID that the read layer renames to a roster EID."""
    return eid in RENAME


def is_serial_unit(eid):
    return eid in SERIAL_UNITS


# ---------------------------------------------------------------------------
# Taipei-naive date normalization and as-of projection
# ---------------------------------------------------------------------------

def _as_naive_datetime(x):
    """A11 adapted date/datetime -> naive datetime; aware/offset/basic forms raise ValueError."""
    if isinstance(x, _dt.datetime):
        value = x
    elif isinstance(x, _dt.date):
        value = _dt.datetime.combine(x, _dt.time())
    elif isinstance(x, str):
        raw = x.strip()
        # `fromisoformat` accepts compact YYYYMMDD and offsets; the A11 adapter contract accepts neither.
        date_part = raw[:10]
        if (len(raw) not in (10, 19) or len(date_part) != 10 or
                date_part[4:5] != "-" or date_part[7:8] != "-" or
                (len(raw) == 19 and raw[10:11] not in (" ", "T"))):
            raise ValueError("not an A11 effective_date: %r" % (x,))
        try:
            value = _dt.datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("not an A11 effective_date: %r" % (x,)) from exc
    else:
        raise ValueError("not an A11 effective_date: %r" % (x,))
    if value.tzinfo is not None:
        raise ValueError("aware/offset effective_date is forbidden: %r" % (x,))
    return value.replace(microsecond=0)


def _as_date(x):
    """A11 adapted date/day input -> Taipei calendar date, without timezone conversion."""
    return _as_naive_datetime(x).date()


def _validated_events(events):
    """Validate the complete supplied event stream; malformed rows must never mean 'active'."""
    if events is None:
        return ()
    out = []
    for row in events:
        if not isinstance(row, dict):
            raise TypeError("event row must be dict: %r" % (row,))
        if "eid" not in row or "ts_effective" not in row or "state" not in row:
            raise ValueError("event row requires eid, ts_effective, and state: %r" % (row,))
        _as_naive_datetime(row["ts_effective"])
        out.append(row)
    return tuple(out)


def _nest_effective_states(eid, day, events=None):
    rows = _validated_events(events)
    d = _as_date(day)
    units = installed_units(eid)
    if not units:
        return {}
    whole = None
    per_unit = {}
    valid = frozenset(units)
    for index, event in enumerate(rows):
        if event.get("eid") != eid:
            continue
        event_date = _as_date(event["ts_effective"])
        if event_date > d:
            continue
        state = event["state"]
        sub = event.get("sub_unit")
        candidate = (event_date, index, state)
        if sub is None:
            if whole is None or candidate[:2] > whole[:2]:
                whole = candidate
        elif sub in valid:
            previous = per_unit.get(sub)
            if previous is None or candidate[:2] > previous[:2]:
                per_unit[sub] = candidate
    states = {}
    for unit in units:
        local = per_unit.get(unit)
        # Match board_dormant: calendar day first, then later input index within that day.
        chosen = max((item for item in (whole, local) if item is not None), default=None,
                     key=lambda item: item[:2])
        states[unit] = chosen[2] if chosen is not None else STATE_IN_PRODUCTION
    return states


def unknown_sub_units(eid, events):
    """Return ``{unknown_sub_unit: event_count}`` for this EID; whole-machine rows are excluded."""
    kind = unit_kind(eid)
    units = frozenset(installed_units(eid))
    valid = units if kind == "nest_eav" else frozenset()
    relevant_eids = {eid} if kind == "nest_eav" else ({eid} | set(units))
    counts = {}
    for event in _validated_events(events):
        if event.get("eid") not in relevant_eids:
            continue
        sub = event.get("sub_unit")
        if sub is not None and sub not in valid:
            counts[sub] = counts.get(sub, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def _eid_child_dormant_units(eid, day, events=None):
    """Delegate independent child EIDs to dormant without ever forwarding sub_unit rows."""
    rows = _validated_events(events)
    d = _as_date(day)
    out = []
    for child in installed_units(eid):
        child_rows = [row for row in rows if row.get("eid") == child and row.get("sub_unit") is None]
        if _dormant.is_dormant(child, d, child_rows):
            out.append(child)
    return tuple(out)


def active_units(eid, day, events=None):
    """Installed units not dormant as of the end of Taipei calendar *day*."""
    _as_date(day)
    units = installed_units(eid)
    dormant = frozenset(dormant_units(eid, day, events))
    return tuple(unit for unit in units if unit not in dormant)


def dormant_units(eid, day, events=None):
    """Installed units whose effective state is ``not_scheduled`` at day end."""
    _as_date(day)
    kind = unit_kind(eid)
    if kind in ("cotag_children", "parent_expansion"):
        return _eid_child_dormant_units(eid, day, events)
    states = _nest_effective_states(eid, day, events)
    return tuple(unit for unit in installed_units(eid) if states.get(unit) == STATE_NOT_SCHEDULED)


def capacity_factor(eid, day, events=None):
    """Active/installed capacity, or ``None`` for an EID without registered units."""
    _as_date(day)
    rows = _validated_events(events)
    units = installed_units(eid)
    return None if not units else len(active_units(eid, day, rows)) / len(units)


def capacity_split(eid, day, events=None):
    """Return installed/active counts, dormant UNIT_IDs, and the capacity factor."""
    _as_date(day)
    units = installed_units(eid)
    dormant = dormant_units(eid, day, events)
    active = len(units) - len(dormant)
    return {"installed": len(units), "active": active, "dormant": dormant,
            "factor": None if not units else active / len(units)}


def equiv_units_factor(eid, affected_units, day, events=None):
    """Downtime-equivalent coefficient: ``len(affected ∩ active) / installed``.

    Wired into W1 (``app._equiv_factor_by_cls`` → daily ledger / ``/monthly``) and, since
    2026-09-12, into the ``/equipment`` seat coefficient (``board_eqmeeting.seat_factors_for``
    → ``build_board(seat_factors=…)``; ``SPEC-RUNWINDOW.md`` §6.3-b / C-RW24). Unknown affected
    UNIT_IDs and units already dormant on *day* contribute zero.
    """
    if isinstance(affected_units, str):
        raise TypeError("affected_units must be an iterable of UNIT_IDs, not str")
    _as_date(day)
    rows = _validated_events(events)
    units = installed_units(eid)
    if not units:
        return None
    affected = set(affected_units or ())
    whole_markers = {eid} | {serial for serial, machine in SERIAL_UNITS.items() if machine == eid}
    active = frozenset(active_units(eid, day, rows))
    if affected.intersection(whole_markers):
        return len(active) / len(units)
    return len(affected.intersection(active)) / len(units)


# ---------------------------------------------------------------------------
# 座（seat）層：`SPEC-CAPACITY-FORECAST.md` §1／§2 的唯一引擎（W1–W4 三個消費者共用）
# ---------------------------------------------------------------------------
#   **座**＝可獨立停排的最小產能單位：一般機台＝1 座（`WHOLE_UNIT`）；形①＝每個 nest 一座；
#   形②③＝每個子 EID 一座。座名冊的**唯一住處就是本檔**（C-CF3）——消費者一律 import 這裡的
#   函式，不得在 app／template／attrib 裡自己數座位。
#
#   ★ 與 `dormant_units()` 的差別（**刻意的**，不要「順手統一」）：
#     `dormant_units()` 對形②③ 只逐 child 委派 `board_dormant.is_dormant`、**母機事件不傳遞**
#     （檔頭規則 3）。座層要回答的是「這台機今天還有幾座能排產」⇒ **母機自己被登記長停時，
#     它的每一座都不能排產**。所以 `machine_seats()` 在委派之前先問一次整機謂詞。
#     兩支各自回答不同的問題，**都對**；把其中一支改成另一支就會有一邊變錯。
#
#   ★ 這一層一律 as-of 逐日（C-CF4）、狀態只來自登記（C-CF2）、函式內不讀時鐘（C-D6）。

WHOLE_UNIT = "whole"        # 沒有登記子單元的機台，它自己就是那一座（seat_id ⇒ 只印 EID）


def seat_id(eid, unit):
    """座的可讀識別：整機座只印 EID，子單元座印 ``EID-xxxxx/nest:2``（落檔與 title 共用一種寫法）。"""
    return eid if unit == WHOLE_UNIT else "%s/%s" % (eid, unit)


def machine_seats(eid, day, events=None):
    """一台機在 *day* 的座帳 → ``{"installed", "active", "dormant", "whole"}``。

    · 沒有登記子單元 ⇒ `installed == 1`、座 id ＝ `WHOLE_UNIT`。
    · **整機被登記長停 ⇒ `active == 0`、全部座進 dormant**（見本節開頭那段「刻意的差別」）。
      `board_dormant.events_for` 自己會濾掉 `sub_unit` lane，所以整份事件流直接餵它是安全的；
      `events=None` ⇒ 走 `board_dormant` 的過渡橋常數（與全 app 同一個慣例）。
    · 否則子單元座逐座走 `dormant_units()`。
    """
    _as_date(day)
    units = installed_units(eid)
    seats = units or (WHOLE_UNIT,)
    if _dormant.is_dormant(eid, day, events):
        return {"installed": len(seats), "active": 0, "dormant": tuple(seats), "whole": not units}
    dormant = dormant_units(eid, day, events) if units else ()
    return {"installed": len(seats), "active": len(seats) - len(dormant),
            "dormant": tuple(dormant), "whole": not units}


def station_seats(eids, day, events=None, planned_sec_by_eid=None):
    """一站（一組 EID）在 *day* 的座帳；給了 `planned_sec_by_eid` 就一併算宣告／銘牌分母。

    回 `{"installed", "active", "dormant"[], "by_eid"{}}`，另在有 planned 時多三欄：

    · `planned_sec`           ＝**宣告**（declared）分母＝Σ 至少還有一座在役的機台的整窗秒。
      **一座都不剩的機台整台不進分母**（＝Chris 裁「長停 EID 從產能預測中剔除」）。
      ⚠ **部分座長停的機台不按 n/N 縮**——`month_rate` 是「每生產機台小時幾 PH」、量的就是
      「當時開著那幾座」的產出（C-CF6）⇒ 把機時按座縮，會把已經反映在速率裡的事情再扣一次，
      而且會讓稼動率因為座而變（違反 C-CF8）。座少了的代價出現在下面那條 `loss_seat_h`。
    · `planned_sec_nameplate` ＝**銘牌**＝Σ 全部機台的整窗秒（＝接線前的舊值，一個位元不動）。
    · `loss_seat_h`           ＝長停座的產能時間當量（乘上站速率 `r` 就是「長停產能損失 N PH」）：
        - 還有 n>0 座在役的機台：`h × (N − n) / n`（那 n 座每小時做 `r`，所以一座值 `r/n`）；
        - 一座都不剩的機台：`h`（整台份；**不乘 N**——沒有任何在役座可以校準它的每座速率，
          憑空乘上去就是發明一個沒量過的數。這是刻意的**下限**估計）。
    """
    _as_date(day)
    plan = None if planned_sec_by_eid is None else dict(planned_sec_by_eid)
    installed = active = 0
    dormant, by_eid = [], {}
    plan_all = plan_active = loss_h = 0.0
    for eid in sorted({e for e in (eids or ()) if e}):
        s = machine_seats(eid, day, events)
        installed += s["installed"]
        active += s["active"]
        ids = [seat_id(eid, u) for u in s["dormant"]]
        dormant.extend(ids)
        by_eid[eid] = {"installed": s["installed"], "active": s["active"], "dormant": ids}
        if plan is not None:
            sec = max(0.0, float(plan.get(eid) or 0.0))
            plan_all += sec
            if s["active"] > 0:
                plan_active += sec
                loss_h += (sec / 3600.0) * (s["installed"] - s["active"]) / s["active"]
            else:
                loss_h += sec / 3600.0
    out = {"installed": installed, "active": active, "dormant": sorted(dormant), "by_eid": by_eid}
    if plan is not None:
        out.update({"planned_sec": plan_active, "planned_sec_nameplate": plan_all,
                    "loss_seat_h": loss_h})
    return out


def seat_dormant_since(eid, unit, day, events=None):
    """那一座在 *day* 這**一段**長停的起點（`'YYYY-MM-DD'`）；不是長停 ⇒ `None`。

    「自哪天」是 C-D3 要印在 title 上的東西（排除必須看得見）。整機座與 child-EID 座直接
    委派 `board_dormant.dormant_info`（同一條 as-of 與「復機後再度長停取新那一段」的語意）；
    nest 座沒有自己的 EID ⇒ 在這裡走**與 `_nest_effective_states` 完全相同**的挑選規則
    （整機列作用於全部座、同日以輸入順序較後者定調），再往回走連續的 `not_scheduled`。
    """
    d = _as_date(day)
    kind = unit_kind(eid)
    if kind != "nest_eav":
        target = eid if (unit == WHOLE_UNIT or not installed_units(eid)) else unit
        rows = [r for r in _validated_events(events) if r.get("sub_unit") is None]
        info = _dormant.dormant_info(target, d, rows)
        return None if info is None else info["since"]
    seq = []
    for index, event in enumerate(_validated_events(events)):
        if event.get("eid") != eid:
            continue
        event_date = _as_date(event["ts_effective"])
        if event_date > d:
            continue
        sub = event.get("sub_unit")
        if sub is None or sub == unit:
            seq.append((event_date, index, event.get("state")))
    seq.sort(key=lambda item: item[:2])
    if not seq or seq[-1][2] != STATE_NOT_SCHEDULED:
        return None
    i = len(seq) - 1
    while i > 0 and seq[i - 1][2] == STATE_NOT_SCHEDULED:
        i -= 1
    return seq[i][0].isoformat()


def equiv_units_weighted(rows, day, events=None):
    """W1 的停機當量係數 → `(factor|None, total_down_h)`。

    `rows` ＝ `[(eid, affected_units, down_h)…]`（一列一張工單）：
      · `affected_units` 空／None ⇒ **整機**（A10 `sub_units == []` 的語意）
        ⇒ 係數＝`active/installed`（整機工單在 4 座機上只停得了還開著的那幾座）。
      · 非空 ⇒ 那幾座 ⇒ 係數＝`|affected ∩ active| / installed`（`equiv_units_factor`）。
      · **名冊上沒有子單元的機台 ⇒ 係數 1.0**（＝接線前的行為，逐位元不變）。
    係數按 `down_h` 加權平均；總停機時數 ≤ 0 ⇒ 回 `(None, 0.0)`（沒有可加權的東西，
    呼叫端一律當 1.0 用＝不改任何數字）。
    """
    _as_date(day)
    total = num = 0.0
    for row in rows or ():
        eid, affected, down_h = row
        try:
            h = float(down_h or 0.0)
        except (TypeError, ValueError):
            h = 0.0
        if h <= 0:
            continue
        f = equiv_units_factor(eid, (tuple(affected) if affected else (eid,)), day, events)
        total += h
        num += h * (1.0 if f is None else f)
    return ((None, 0.0) if total <= 0 else (num / total, total))


# ── 讀端（日帳 `rw` 區塊）：**單一出處**，計畫端與落地端、月報與頁面都走這兩支 ──────────
def rw_seats_of(rw):
    """日帳 `rw` → `{"installed", "active", "dormant"[], "legacy": bool}`。

    ★ 舊日帳（#83 之前寫的）**沒有 `seats`** ⇒ 視為 `installed == active == n_eid`、
    `dormant == []`、`legacy=True`（＝「那一天的座結構沒有記錄」，**不是**「那一天零長停」）。
    呼叫端必須把 `legacy` 揭露出來，不得靜默當成 0 長停（C-CF1）。"""
    d = rw if isinstance(rw, dict) else {}
    s = d.get("seats")
    if not isinstance(s, dict):
        n = int(d.get("n_eid") or 0)
        return {"installed": n, "active": n, "dormant": [], "legacy": True}
    try:
        installed = int(s.get("installed") or 0)
        active = int(s.get("active") or 0)
    except (TypeError, ValueError):
        n = int(d.get("n_eid") or 0)
        return {"installed": n, "active": n, "dormant": [], "legacy": True}
    return {"installed": installed, "active": active,
            "dormant": list(s.get("dormant") or ()), "legacy": False}


def rw_planned_sec(rw, nameplate=False):
    """日帳 `rw` 的分母秒：預設**宣告**（`planned_sec`）；`nameplate=True` 回**銘牌**。
       舊日帳沒有 `planned_sec_nameplate` ⇒ 兩者同值（那時候兩個口徑就是同一個數）。"""
    d = rw if isinstance(rw, dict) else {}
    declared = d.get("planned_sec")
    if not nameplate:
        return declared
    v = d.get("planned_sec_nameplate")
    return declared if v is None else v


# ---------------------------------------------------------------------------
# --selftest（offline、deterministic）
# ---------------------------------------------------------------------------

def _selftest():
    D = _dt.date
    DT = _dt.datetime
    n = 0

    def ck(condition, message):
        nonlocal n
        assert condition, message
        n += 1

    # WP-10d（R-P11）：具名機台由名冊常數推導（私有樹：第 2 把鍵＝EID-26596（Press#2）、末把鍵＝EID-13219（Frame#3）；公開樹＝合成名冊的同位置）。
    import core_paths as _wp
    _pub = _wp.is_public_tree()
    if not _pub:
        import board_private_pins as _pp
    _sp = list(NEST_EAV_UNITS)[1]
    _sp2 = list(NEST_EAV_UNITS)[-1]
    _n2 = max(NEST_EAV_UNITS[_sp2]["nests"])
    # A0. cmms NOTE-71 seat IDs fold at the roster boundary, including
    # unregistered seats (which retain their real parent but disclose unknown).
    ck(fold_seat_id("EID-00425") == ("EID-00425", None, True),
       "A0 bare EID is an identity fold")
    ck(fold_seat_id(f"{_sp}-N2") == (_sp, "nest:2", True),
       "A0 registered Press seat folds to its parent and nest unit")
    ck(fold_seat_id(f"{_sp2}-N{_n2}") == (_sp2, f"nest:{_n2}", True),
       "A0 registered Frame seat 12 folds to its parent and nest unit")
    ck(fold_seat_id(f"{_sp}-N7") == (_sp, "nest:7", False),
       "A0 unknown seat number still folds to its real parent")
    ck(fold_seat_id("EID-99999-N1") == ("EID-99999", "nest:1", False),
       "A0 unknown seat parent still folds without dropping the record")
    ck(all(fold_seat_id(value) == (value, None, True)
           for value in (None, f"{_sp}-N02", f"{_sp}#2", f"{_sp.lower()}-n2")),
       "A0 malformed NOTE-71 forms are exact identity folds")
    ck(fold_seat_members({f"{_sp}-N2", f"{_sp}-N3", "EID-00425"})
       == {_sp, "EID-00425"},
       "A0 scope members deduplicate after seat-to-parent folding")

    # A. register identity and integrity
    ck(PARENT_EXPANSION is _scope.PARENT_EXPANSION, "A1 parent register imported by identity")
    # ★ C-LD46（§18.30.4）：形① AOI 載盤機退座 ⇒ 台數與座數同步調整（私有樹釘值見下兩根）
    ck(_pub or len(NEST_EAV_UNITS) == _pp.PINS["capacity.nest_eav_n"],
       "A2 EAV machine count matches the private pin")
    ck(_pub or sum(len(v["nests"]) for v in NEST_EAV_UNITS.values()) == _pp.PINS["capacity.nest_eav_seats_n"],
       "A2b form-1 seat count matches the private pin")
    ck(len(COTAG_CHILD_UNITS) == 2, "A3 two co-tag machines")
    ck(len(PARENT_EXPANSION) == 3, "A4 three parent machines")
    ck(machines_with_units() == tuple(sorted(machines_with_units())), "A5 sorted machine API")
    ck(_pub or len(machines_with_units()) == _pp.PINS["capacity.unit_machines_n"],
       "A6 unit-bearing machine count matches the private pin")
    composite_ids = [(eid, unit) for eid in machines_with_units() for unit in installed_units(eid)]
    ck(len(composite_ids) == len(set(composite_ids)), "A7 UNIT_ID unique in its machine namespace")
    child_ids = [u for eid in set(COTAG_CHILD_UNITS) | set(PARENT_EXPANSION) for u in installed_units(eid)]
    ck(len(child_ids) == len(set(child_ids)), "A8 child EID belongs to only one machine")
    forbidden = set(ALIASES) | set(SERIAL_UNITS) | set(child_ids)
    ck(not forbidden.intersection(machines_with_units()), "A9 alias/serial/child is not a unit-bearing machine")
    ck(not set(ALIASES).intersection(SERIAL_UNITS), "A10 aliases and serial stages do not collide")
    ck(all(unit.startswith("nest:") for eid in NEST_EAV_UNITS for unit in installed_units(eid)), "A11 nest UNIT_ID form")
    ck(all(unit.startswith("EID-") for eid in set(COTAG_CHILD_UNITS) | set(PARENT_EXPANSION)
           for unit in installed_units(eid)), "A12 child UNIT_ID form")
    ck(installed_units("EID-NOPE") == (), "A13 unknown EID has no units")
    ck(unit_kind("EID-NOPE") is None, "A14 unknown EID has no kind")
    if not _pub:   # A15–A20：具名機台的種類／主站（公開樹＝合成名冊，見下方 skipped）
        ck(unit_kind("EID-00425") == "nest_eav", "A15 EAV kind")
        ck(unit_kind("EID-08158") == "cotag_children", "A16 co-tag kind")
        ck(unit_kind("EID-24742") == "parent_expansion", "A17 parent kind")
        # A18–A26 子工位排列軸（Chris 2026-09-08；`SPEC-LANDING.md` §18.23、C-LD40）
        ck(subunit_main_cls("EID-00425") == "segLamination", "A18 EAV main_cls read from register")
        ck(subunit_main_cls("EID-08158") == "segLamLeakTest"
           and subunit_main_cls("EID-25402") == "segLamLeakTest",
           "A19 co-tag main_cls read from register")
        ck(subunit_main_cls("EID-24742") is None and subunit_main_cls("EID-NOPE") is None,
           "A20 parent machine and unknown EID have no own main_cls")
    ck(set(COTAG_MAIN_CLS) == set(COTAG_CHILD_UNITS),
       "A21 every co-tag machine declares its station exactly once")
    if not _pub:   # A22–A24b：具名機台的排列軸
        ck(subunit_axis("EID-08158") == "y" and subunit_axis("EID-25402") == "y",
           "A22 Leak Test lays its sub-units bottom-to-top")
        ck(subunit_axis("EID-12724") == "y" and subunit_axis("EID-13219") == "y",
           "A23 Frame Bond lays its sub-units bottom-to-top")
        # ⚠ A24 原本還列了 AOI 三台（`EID-09763`／`EID-26161`／`EID-38316`）；
        #    C-LD46 退出名冊後它們根本沒有座可排 ⇒ 拿掉（負釘在 B7–B9）。
        # ★ #121（2026-09-09；§18.31）：Press 兩台從 `x` 換成 `grid` ⇒ 從 A24 移到 A24b。
        ck(all(subunit_axis(e) == "x" for e in ("EID-00643", "EID-26718")),
           "A24 every other unit-bearing machine lays its sub-units left-to-right")
        ck(subunit_axis("EID-00425") == subunit_axis("EID-26596") == "grid"
            and SUBUNIT_AXIS["segLamination"] == "grid",
            "A24b Press uses its declared grid layout (#121)")
    ck(all(subunit_axis(p) == PARENT_EXPANSION_AXIS == "x" for p in PARENT_EXPANSION),
       "A25 CAL/CLN Gen2 parent split axis is explicit x, not aspect-ratio derived")
    ck(set(SUBUNIT_AXIS.values()) <= {"x", "y", "grid"} and SUBUNIT_AXIS_DEFAULT == "x"
       and all(subunit_axis(e) in ("x", "y", "grid") for e in machines_with_units()),
       "A26 axis vocabulary is exactly {x, y, grid}")

    # B. declared counts and exact mappings
    if not _pub:   # B1–B15：具名機台的座數
        ck(len(installed_units("EID-00425")) == _pp.PINS["capacity.press_1_seats_n"], "B1 Press#1 seat count matches the private pin")
        ck(len(installed_units("EID-26596")) == _pp.PINS["capacity.press_2_seats_n"], "B2 Press#2 seat count matches the private pin")
        ck(len(installed_units("EID-00643")) == _pp.PINS["capacity.fa_1_seats_n"], "B3 FA#1 seat count matches the private pin")
        ck(len(installed_units("EID-26718")) == _pp.PINS["capacity.fa_5_seats_n"], "B4 FA#5 seat count matches the private pin; noise nest excluded")
        ck(len(installed_units("EID-12724")) == _pp.PINS["capacity.frame_2_seats_n"], "B5 Frame#2 seat count matches the private pin")
        ck(len(installed_units("EID-13219")) == _pp.PINS["capacity.frame_3_seats_n"], "B6 Frame#3 seat count matches the private pin")
        # ★★ B7–B9 從「各有 n 座」翻成**負釘**（C-LD46；§18.30.4）：這三台的 nest 編號
        #    是客製載盤 slot 的**追蹤代號**，不會故障也不算產能 ⇒ 不是子工位。
        #    （原釘：「B7 AOI Post Cure has n」等三根。）
        ck(installed_units("EID-09763") == () and "EID-09763" not in machines_with_units(),
           "B7 AOI Post Cure has NO seats (tray slots are not sub-units; C-LD46)")
        ck(installed_units("EID-26161") == () and "EID-26161" not in machines_with_units(),
           "B8 AOI BLSM has NO seats (tray slots are not sub-units; C-LD46)")
        ck(installed_units("EID-38316") == () and "EID-38316" not in machines_with_units(),
           "B9 AOI Kestrel has NO seats (tray slots are not sub-units; C-LD46)")
        ck(len(installed_units("EID-08158")) == _pp.PINS["capacity.leak_1_seats_n"], "B10 Leak#1 seat count matches the private pin")
        ck(len(installed_units("EID-25402")) == _pp.PINS["capacity.leak_kestrel_seats_n"], "B11 Leak Kestrel seat count matches the private pin")
        ck(len(installed_units("EID-24742")) == _pp.PINS["capacity.cal_seats_n"], "B12 CAL seat count matches the private pin")
        ck(len(installed_units("EID-16077")) == _pp.PINS["capacity.cln_di_seats_n"], "B13 CLN DI seat count matches the private pin")
        ck(len(installed_units("EID-27600")) == _pp.PINS["capacity.cln_sf_seats_n"], "B14 CLN SF seat count matches the private pin")
        ck(installed_units("EID-26718") == ("nest:1", "nest:2", "nest:3"), "B15 nest 0 absent")
    _seat_roundtrip = [(eid, unit, seat_wire_id(eid, unit))
                       for eid in sorted(NEST_EAV_UNITS)
                       for unit in installed_units(eid)]
    ck((_pub or len(_seat_roundtrip) == _pp.PINS["capacity.nest_eav_seats_n"])
       and all(wire is not None and fold_seat_id(wire) == (eid, unit, True)
               for eid, unit, wire in _seat_roundtrip),
       "B16 NOTE-71 wire formatter round-trips every registered physical seat")
    ck(seat_wire_id(_sp, "nest:2") == f"{_sp}-N2"
       and seat_wire_id(_sp, "nest:02") is None
       and seat_wire_id(_sp, "EID-05707") is None,
       "B17 seat_wire_id only formats canonical nest units; co-tag children have no -N wire form")

    # C. primary folding
    # C 段具名 EID 由名冊常數推導（私有樹＝別名 01450／24821／01529、串接 16589／27617、改名 38041→38106、丟棄別名 38072）
    _ca, _cp = next(iter(_ALIGNER_ALIASES.items()))
    _ca2, _ca3 = list(_ALIGNER_ALIASES)[1], list(_ALIGNER_ALIASES)[-1]
    _sr, _spar = next(iter(SERIAL_UNITS.items()))
    _sr2 = list(SERIAL_UNITS)[-1]
    _m0 = list(NEST_EAV_UNITS)[0]
    _rn_c, _rn_m = next(iter(_mt.EID_RENAME.items()))
    _dr = next(iter(_mt.EID_ALIAS_DROP))
    ck(primary_of(_ca) == _cp, "C1 alias folds")
    ck(primary_of(_sr) == _spar, "C2 serial folds")
    ck(primary_of(_m0) == _m0, "C3 ordinary EID unchanged")
    ck(is_alias(_ca2) is True, "C4 alias predicate")
    ck(is_alias(_sr) is False, "C5 serial is not alias")
    ck(is_serial_unit(_sr2) is True, "C6 serial predicate")
    ck(is_serial_unit(_ca3) is False, "C7 alias is not serial")
    # C8-C15 Top O-ring 形④（2026-09-02 #41 裁決 (c)；SPEC-RUNWINDOW.md §10.8-4／§10.8-8）
    ck(RENAME is _mt.EID_RENAME, "C8 rename table imported by identity (single source)")
    ck(all(ALIASES[k] == v for k, v in _mt.EID_ALIAS_DROP.items()),
       "C9 alias drop table imported from the frozen core, never re-typed")
    ck(primary_of(_rn_c) == _rn_m, "C10 MES counting EID folds to the roster row")
    ck(primary_of(_dr) == _rn_m, "C11 Vantor alias folds to the roster row")
    ck(primary_of(_rn_m) == _rn_m, "C12 roster row is its own primary")
    ck(is_renamed(_rn_c) is True and is_alias(_rn_c) is False,
       "C13 a rename is not an alias")
    ck(is_alias(_dr) is True and is_renamed(_dr) is False,
       "C14 an alias is not a rename")
    ck((_pub or set(_ALIGNER_ALIASES) == {"EID-01450", "EID-24821", "EID-01529"})
       and not set(_ALIGNER_ALIASES) & set(_mt.EID_ALIAS_DROP),
       "C15 Aligner aliases stay WO-side only; the read layer drops none of them")

    # D–I：綁具名座數、橋座對、串聯父機與 co-tag 的角色。私有樹＝原本寫死的真編號（一位元不動）；
    #   公開樹（WP-10e，R-P19）＝由合成名冊常數推導同一組角色（各座數角色的機台／橋上兩座同屬一台父機／
    #   串聯單元父機／co-tag 母子）；推不出 ⇒ skipped（R-P11）。A11 fixtures 只帶 sub_unit、不帶 EID，角色換了照樣成立。
    if not _pub:
        _F1, _F2, _L12, _FA = "EID-00425", "EID-26596", "EID-12724", "EID-26718"
        _PC, _B1, _B2 = "EID-24742", "EID-26841", "EID-26887"
        _DIP, _DIC, _DIS = "EID-16077", "EID-14732", "EID-16589"
        _CTP, _CTC = "EID-08158", "EID-05707"
        _DI_EXP = _pp.PINS["capacity.di_expected_factor"]
        _roles = True
    else:
        _n4 = [e for e, r in NEST_EAV_UNITS.items() if len(r["nests"]) == 4]
        _n12 = [e for e, r in NEST_EAV_UNITS.items() if len(r["nests"]) == 12]
        _n3 = [e for e, r in NEST_EAV_UNITS.items() if len(r["nests"]) == 3]
        _brs = {r[0] for r in _dormant.DORMANT_BRIDGE}
        _pcs = [p for p, cs in PARENT_EXPANSION.items() if len(cs) == 4 and len(set(cs) & _brs) == 2]
        _sus = [(s, p) for s, p in SERIAL_UNITS.items() if len(PARENT_EXPANSION.get(p, ())) >= 2]
        _roles = bool(len(_n4) >= 2 and _n12 and _n3 and _pcs and _sus and COTAG_CHILD_UNITS)
        if _roles:
            _F1, _F2 = _n4[:2]
            _L12, _FA = _n12[0], _n3[0]
            _PC = _pcs[0]
            _B1, _B2 = [c for c in PARENT_EXPANSION[_PC] if c in _brs]
            _DIS, _DIP = _sus[0]
            _DIC = PARENT_EXPANSION[_DIP][0]
            _DI_EXP = (len(PARENT_EXPANSION[_DIP]) - 1) / len(PARENT_EXPANSION[_DIP])
            _CTP = next(iter(COTAG_CHILD_UNITS))
            _CTC = COTAG_CHILD_UNITS[_CTP][0]

    if _roles:
        # D. as-of event behavior
        day = D(2026, 8, 10)
        ck(active_units(_F1, day, []) == installed_units(_F1), "D1 no events all active")
        ck(dormant_units(_F1, day, []) == (), "D2 no events none dormant")
        backfill = [{"eid": _F1, "sub_unit": "nest:2", "ts_effective": "2025-01-01",
                     "state": STATE_NOT_SCHEDULED}]
        ck(dormant_units(_F1, day, backfill) == ("nest:2",), "D3 pure-date backfill")
        later = backfill + [{"eid": _F1, "sub_unit": "nest:2", "ts_effective": "2026-01-01",
                             "state": STATE_IN_PRODUCTION}]
        ck(dormant_units(_F1, day, later) == (), "D4 later event supersedes")
        stop_all = [{"eid": _F1, "ts_effective": "2026-08-01 08:00:00",
                     "state": STATE_NOT_SCHEDULED}]
        ck(active_units(_F1, day, stop_all) == (), "D5 whole-machine stop empties nest machine")
        release = stop_all + [{"eid": _F1, "sub_unit": "nest:3", "ts_effective": "2026-08-02T08:00:00",
                               "state": STATE_IN_PRODUCTION}]
        ck(active_units(_F1, day, release) == ("nest:3",), "D6 later unit release after EID stop")
        same_time = stop_all + [{"eid": _F1, "sub_unit": "nest:3", "ts_effective": "2026-08-01T08:00:00",
                                 "state": STATE_IN_PRODUCTION}]
        ck(active_units(_F1, day, same_time) == ("nest:3",), "D7 same-ts later input wins across channels")
        same_day_out_of_order = [
            {"eid": _F1, "ts_effective": "2026-08-01T09:00:00", "state": STATE_NOT_SCHEDULED},
            {"eid": _F1, "sub_unit": "nest:3", "ts_effective": "2026-08-01T08:00:00",
             "state": STATE_IN_PRODUCTION},
        ]
        ck("nest:3" in active_units(_F1, day, same_day_out_of_order),
           "D7b same-day later input wins despite earlier clock time")
        ck(_as_naive_datetime(D(2026, 8, 1)) == DT(2026, 8, 1), "D8 date effective value is midnight")
        ck(_as_naive_datetime("2026-08-01 08:00:00") == DT(2026, 8, 1, 8), "D9 space datetime accepted")
        ck(_as_naive_datetime("2026-08-01T08:00:00") == DT(2026, 8, 1, 8), "D10 T datetime accepted")
        edge = [{"eid": _F1, "sub_unit": "nest:1", "ts_effective": "2026-08-10T23:59:59",
                 "state": STATE_NOT_SCHEDULED},
                {"eid": _F1, "sub_unit": "nest:2", "ts_effective": "2026-08-11T00:00:00",
                 "state": STATE_NOT_SCHEDULED}]
        ck(dormant_units(_F1, day, edge) == ("nest:1",), "D11 day-end included, next midnight excluded")
        other = edge + [{"eid": _F2, "ts_effective": "2020-01-01T00:00:00",
                         "state": STATE_NOT_SCHEDULED}]
        ck(dormant_units(_F1, day, other) == ("nest:1",), "D12 other EID ignored")
        unknown = edge + [{"eid": _F1, "sub_unit": "nest:99", "ts_effective": "2020-01-01T00:00:00",
                           "state": STATE_NOT_SCHEDULED},
                          {"eid": _F1, "sub_unit": "nest:99", "ts_effective": "2021-01-01T00:00:00",
                           "state": STATE_IN_PRODUCTION}]
        ck(dormant_units(_F1, day, unknown) == ("nest:1",), "D13 unknown unit ignored")
        ck(unknown_sub_units(_F1, unknown) == {"nest:99": 2}, "D14 unknown unit counted")
        ck(unknown_sub_units(_F2, unknown) == {}, "D15 diagnostics ignore other EID")
        ck(_as_date("2026-08-10") == day, "D16 pure-date string day")
        ck(_as_date(D(2026, 8, 10)) == day, "D17 datetime.date day")

        # E. factors and split
        press = [{"eid": _F2, "sub_unit": unit, "ts_effective": "2026-08-01",
                 "state": STATE_NOT_SCHEDULED} for unit in ("nest:2", "nest:4")]
        ck(capacity_factor(_F2, day, press) == 0.5, "E1 Press#2 2/4")
        ck(capacity_split(_F2, day, press) == {"installed": 4, "active": 2,
           "dormant": ("nest:2", "nest:4"), "factor": 0.5}, "E2 Press split")
        frame = [{"eid": _L12, "sub_unit": "nest:10", "ts_effective": "2026-08-01",
                "state": STATE_NOT_SCHEDULED}]
        ck(capacity_factor(_L12, day, frame) == 11 / 12, "E3 Frame#2 11/12")
        ck(capacity_split("EID-NOPE", day, []) == {"installed": 0, "active": 0, "dormant": (), "factor": None},
           "E4 unknown split")
        ck(capacity_factor("EID-NOPE", day, []) is None, "E5 unknown factor")
        ck(equiv_units_factor(_F2, ["nest:1"], day, []) == 1 / 4, "E6 one-nest WO is 1/N")
        ck(equiv_units_factor(_F2, ["nest:2"], day, press) == 0.0, "E7 dormant affected nest is zero")
        ck(equiv_units_factor(_F2, ["nest:1", "nest:99"], day, []) == 1 / 4, "E8 unknown affected ignored")
        ck(equiv_units_factor("EID-NOPE", ["nest:1"], day, []) is None, "E9 unknown EID equivalent is None")
        ck(equiv_units_factor(_F2, [_F2], day, press) == 0.5, "E10 machine EID means all active units")
        di_stop = [{"eid": _DIC, "ts_effective": "2026-08-01", "state": STATE_NOT_SCHEDULED}]
        ck(equiv_units_factor(_DIP, [_DIS], day, di_stop) == _DI_EXP,
           "E11 serial stage means all installed-and-active units")
        try:
            equiv_units_factor(_F2, "nest:1", day, [])
            ck(False, "E12 string affected_units raises")
        except TypeError:
            ck(True, "E12 string affected_units raises")

        # F. A11 additive `sub_unit` goldens：adapter → nest projector → factor
        #    離線讀 cmms 位元 fixture；adapter 才是唯一把 wire row 變成投影事件的入口。
        import json
        import os
        import board_data as _data
        fx_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures")
        with open(os.path.join(fx_dir, "contract_asset_schedule_state.v1.sub_unit_single.json"),
                  encoding="utf-8") as fh:
            g2 = [_data._a11_adapt(_F2, row) for row in json.load(fh)]
        with open(os.path.join(fx_dir, "contract_asset_schedule_state.v1.sub_unit_two_lanes.json"),
                  encoding="utf-8") as fh:
            g3 = [_data._a11_adapt(_F2, row) for row in json.load(fh)]
        ck(_nest_effective_states(_F2, day, g2)["nest:2"] == STATE_NOT_SCHEDULED
           and capacity_factor(_F2, day, g2) == 0.75,
           "F1 G2 fixture 經 _a11_adapt → nest projector：nest:2 停、factor=0.75")
        ck(_nest_effective_states(_F2, day, g3)["nest:2"] == STATE_NOT_SCHEDULED
           and _nest_effective_states(_F2, day, g3)["nest:4"] == STATE_NOT_SCHEDULED
           and capacity_factor(_F2, day, g3) == 0.5,
           "F2 G3 fixture 經 _a11_adapt → nest projector：nest:2/4 停、factor=0.5")

        # F3–F6（**2026-09-10 第四輪對版；來源＝cmms NOTE-72 ①**）：座級長停事件以 `sub_unit`
        #   lane **投影**進母機串流——cmms 把登在座資產（`EID-xxxxx` 加座號後綴）上的 effective
        #   事件改寫成母機串流裡的一筆、`sub_unit="nest:<n>"`（其餘欄位原樣、零新欄、零 schema
        #   bump）⇒ **Board 側零改碼**：走的仍是 `_a11_adapt` → 兩條 lane 這一條既有路。
        #   ⚠ **`fold_seat_id` 不接 A11**：A11 的 row 沒有 asset id 欄，座身分是以 `sub_unit`
        #     到達的。存證＝`SPEC-DORMANT-CAPACITY.md` §6.1 第四輪／`SPEC-RUNWINDOW.md` §10.8-9 節尾。
        with open(os.path.join(fx_dir, "contract_asset_schedule_state.v1.seat_projection.json"),
                  encoding="utf-8") as fh:
            g6 = [_data._a11_adapt(_F2, row) for row in json.load(fh)]
        g6_day = D(2026, 2, 1)      # 座停（生效日 2026-01-06）之後、整機停（2026-04-02）之前
        g6_after = D(2026, 5, 1)    # 整機停之後
        ck(_nest_effective_states(_F2, g6_day, g6)["nest:2"] == STATE_NOT_SCHEDULED
           and capacity_factor(_F2, g6_day, g6) == 0.75
           and unknown_sub_units(_F2, g6) == {},
           "F3 G6 投影 golden 經 _a11_adapt → nest projector：nest:2 停、factor=0.75，"
           "且座號在名冊內（unknown_sub_units 空）")
        g6_seat = [e for e in g6 if e.get("sub_unit") is not None]
        g6_whole = [e for e in g6 if e.get("sub_unit") is None]
        ck(_dormant.is_dormant(_F2, g6_after, g6) is True
           and _dormant.events_for(_F2, g6) == _dormant.events_for(_F2, g6_whole)
           and _dormant.events_for(_F2, g6_seat) == []
           and _dormant.is_dormant(_F2, g6_after, g6_seat) is False,
           "F4 G6：整機 lane 那筆進整機謂詞，座 lane 那筆**永不進**（events_for 的結構防呆仍成立）")
        ck(capacity_factor(_F2, g6_after, g6) == 0.0
           and capacity_split(_F2, g6_after, g6)["active"] == 0
           and machine_seats(_F2, g6_after, g6)["active"] == 0,
           "F4b G6：整機登記長停 ⇒ 每一座都不能排產（**現行行為、本輪不改**）")
        ck(_nest_effective_states(_F2, g6_day, g6 + [dict(g6_seat[0])])
           == _nest_effective_states(_F2, g6_day, g6)
           and capacity_factor(_F2, g6_day, g6 + [dict(g6_seat[0])]) == 0.75,
           "F5 G6：同 lane 新（投影）舊（0057 直登）**不去重是安全的**——重複同一筆事件，"
           "「lane 取最新」的規則讓結果逐位相同")
        g6_rev = list(reversed(g6))
        ck(_nest_effective_states(_F2, g6_day, g6_rev)
           == _nest_effective_states(_F2, g6_day, g6)
           and capacity_factor(_F2, g6_day, g6_rev) == 0.75
           and _dormant.is_dormant(_F2, g6_after, g6_rev) is True
           and capacity_factor(_F2, g6_after, g6_rev) == 0.0,
           "F6 G6：兩筆反序餵入 ⇒ F3／F4 結果不變（挑選依 ts_effective，不靠輸入順序）")

        # G. child-EID delegation, no parent propagation, and sub_unit quarantine
        cal_events = [
            {"eid": _B1, "ts_effective": "2023-05-23", "state": STATE_NOT_SCHEDULED},
            {"eid": _B2, "ts_effective": "2023-05-11", "state": STATE_NOT_SCHEDULED},
        ]
        cal = capacity_split(_PC, day, cal_events)
        ck(cal == {"installed": 4, "active": 2, "dormant": (_B1, _B2), "factor": 0.5},
           "F1 CAL child-EID bridge events produce 2/4")
        cal_wd = _dormant.capacity_split(list(installed_units(_PC)),
                                         [(DT(2026, 8, 10), DT(2026, 8, 11))], cal_events)
        ck((cal["active"], cal["installed"]) == (cal_wd["n_active_end"], cal_wd["n_total"]) == (2, 4),
           "F2 capacity split equals board_dormant on four children")
        ck(capacity_factor(_PC, day, (row for row in cal_events)) ==
           capacity_factor(_PC, day, cal_events) == 0.5,
           "F2b capacity_factor generator equals list")
        ck(equiv_units_factor(_PC, [_PC], day, (row for row in cal_events)) ==
           equiv_units_factor(_PC, [_PC], day, cal_events) == 0.5,
           "F2c equiv_units_factor generator equals list")
        parent_stop = [{"eid": _PC, "ts_effective": "2020-01-01", "state": STATE_NOT_SCHEDULED}]
        ck(capacity_factor(_PC, day, parent_stop) == 1.0, "F3 parent event does not propagate")
        bad_child_shape = [{"eid": _PC, "sub_unit": _B1,
                            "ts_effective": "2023-05-23", "state": STATE_NOT_SCHEDULED}]
        ck(capacity_factor(_PC, day, bad_child_shape) == 1.0, "F4 parent sub_unit shape does not project")
        ck(unknown_sub_units(_PC, bad_child_shape) == {_B1: 1}, "F5 parent sub_unit shape reported unknown")
        leak_events = [{"eid": _CTC, "ts_effective": D(2026, 8, 1), "state": STATE_NOT_SCHEDULED}]
        ck(dormant_units(_CTP, day, leak_events) == (_CTC,), "F6 co-tag child delegates by own EID")
        child_sub = [{"eid": _B1, "sub_unit": "nest:1", "ts_effective": "2020-01-01",
                      "state": STATE_NOT_SCHEDULED}]
        ck(capacity_factor(_PC, day, child_sub) == 1.0, "F7 child sub_unit row never reaches dormant")
        ck(unknown_sub_units(_PC, child_sub) == {"nest:1": 1}, "F8 child sub_unit row reported unknown")

        # G. malformed input and timezone rejection
        for bad in (DT(2026, 8, 1, tzinfo=_dt.timezone.utc), "2026-08-01T00:00:00Z",
                    "2026-08-01T00:00:00+08:00", "20260801", "not-a-time"):
            try:
                _as_naive_datetime(bad)
                ck(False, "G rejected timestamp: %r" % (bad,))
            except ValueError:
                ck(True, "G rejected timestamp: %r" % (bad,))
        try:
            active_units(_F1, day, [(_F1, "2026-01-01", STATE_NOT_SCHEDULED)])
            ck(False, "G6 non-dict row raises")
        except TypeError:
            ck(True, "G6 non-dict row raises")
        for i, malformed in enumerate(({"eid": _F1, "state": STATE_NOT_SCHEDULED},
                                       {"eid": _F1, "ts_effective": "2026-01-01"}), 7):
            try:
                dormant_units(_F1, day, [malformed])
                ck(False, "G%d missing event field raises" % i)
            except ValueError:
                ck(True, "G%d missing event field raises" % i)
        ck(active_units(_F1, day, None) == installed_units(_F1), "G9 events=None means no events")
        try:
            unknown_sub_units(_F1, [None])
            ck(False, "G10 diagnostics validates rows")
        except TypeError:
            ck(True, "G10 diagnostics validates rows")
        try:
            capacity_factor("EID-NOPE", "20260810", None)
            ck(False, "G11 malformed day raises even for unknown EID")
        except ValueError:
            ck(True, "G11 malformed day raises even for unknown EID")
        try:
            active_units(_F1, day,
                         [{"ts_effective": "2026-01-01", "state": STATE_NOT_SCHEDULED}])
            ck(False, "G12 missing eid raises")
        except ValueError:
            ck(True, "G12 missing eid raises")

        # C-RW28／NOTE-79：事件指名座的唯一聯集（scope 座 ID + A10 sub_units）。
        ck(event_seat_units(_FA, (), ("nest:2",)) == ("nest:2",),
           "G13 C-RW28 only sub_units names that seat")
        ck(event_seat_units(_FA, (f"{_FA}-N2",), ()) == ("nest:2",),
           "G14 C-RW28 NOTE-79 scope seat ID names that seat")
        ck(event_seat_units(_FA, (f"{_FA}-N2", f"{_FA}-N1"),
                            ("nest:2", "nest:3", "nest:1"))
           == ("nest:1", "nest:2", "nest:3"),
           "G15 C-RW28 unions and de-duplicates both legs in sorted order")
        ck(event_seat_units(_F2, (f"{_FA}-N2",), ()) == (),
           "G16 C-RW28 ignores a scope seat folded to another parent")

        # I. seat layer（`SPEC-CAPACITY-FORECAST.md` §1／§2；W1–W4 共用的唯一引擎）
        I_DAY = D(2026, 9, 1)
        NEST2 = [{"eid": _F2, "sub_unit": "nest:2", "ts_effective": D(2026, 8, 1),
                  "state": STATE_NOT_SCHEDULED}]
        NEST24 = NEST2 + [{"eid": _F2, "sub_unit": "nest:4", "ts_effective": D(2026, 8, 1),
                           "state": STATE_NOT_SCHEDULED}]
        WHOLE_OFF = [{"eid": _F2, "ts_effective": D(2026, 8, 1),
                      "state": STATE_NOT_SCHEDULED}]
        ck(seat_id(_F1, WHOLE_UNIT) == _F1, "I1 whole seat id is the bare EID")
        ck(seat_id(_F2, "nest:2") == f"{_F2}/nest:2", "I2 sub-unit seat id")
        ck(machine_seats("EID-99999", I_DAY, []) == {"installed": 1, "active": 1,
                                                     "dormant": (), "whole": True},
           "I3 a machine with no register is exactly one seat")
        ck(machine_seats(_F2, I_DAY, NEST24)["active"] == 2
           and machine_seats(_F2, I_DAY, NEST24)["installed"] == 4,
           "I4 two of four nests dormant")
        ck(machine_seats(_F2, I_DAY, WHOLE_OFF)["active"] == 0
           and len(machine_seats(_F2, I_DAY, WHOLE_OFF)["dormant"]) == 4,
           "I5 a whole-machine registration darkens every seat")
        ck(machine_seats(_PC, I_DAY, [{"eid": _PC, "ts_effective": D(2026, 8, 1),
                                               "state": STATE_NOT_SCHEDULED}])["active"] == 0,
           "I6 parent-expansion machine also honours the whole-machine predicate")
        ck(dormant_units(_PC, I_DAY, [{"eid": _PC, "ts_effective": D(2026, 8, 1),
                                               "state": STATE_NOT_SCHEDULED}]) == (),
           "I7 dormant_units still does not forward parent events (deliberate difference)")
        _pl = {_F2: 36000.0, _F1: 36000.0}
        _ss = station_seats([_F2, _F1], I_DAY, NEST24, _pl)
        ck(_ss["installed"] == 8 and _ss["active"] == 6, "I8 station seats aggregate")
        ck(_ss["dormant"] == [f"{_F2}/nest:2", f"{_F2}/nest:4"], "I9 dormant seat ids sorted")
        ck(abs(_ss["planned_sec"] - 72000.0) < 1e-9
           and abs(_ss["planned_sec_nameplate"] - 72000.0) < 1e-9,
           "I10 partial-seat dormancy never shrinks the declared denominator (C-CF6/C-CF8)")
        ck(abs(_ss["loss_seat_h"] - 10.0) < 1e-9,
           "I11 loss = h x (N-n)/n = 10 x 2/2 for the four-nest machine")
        _ss0 = station_seats([_F2, _F1], I_DAY, WHOLE_OFF, _pl)
        ck(abs(_ss0["planned_sec"] - 36000.0) < 1e-9
           and abs(_ss0["planned_sec_nameplate"] - 72000.0) < 1e-9,
           "I12 a machine with zero active seats leaves the declared denominator")
        ck(abs(_ss0["loss_seat_h"] - 10.0) < 1e-9, "I13 fully dormant machine loses exactly its window")
        ck(station_seats([], I_DAY, [], {}) == {"installed": 0, "active": 0, "dormant": [],
                                                "by_eid": {}, "planned_sec": 0.0,
                                                "planned_sec_nameplate": 0.0, "loss_seat_h": 0.0},
           "I14 empty station")
        ck("planned_sec" not in station_seats([_F1], I_DAY, []),
           "I15 no planned input means no denominator keys")
        # W1 加權係數（§5 三例：整機 WO 於 4 座 2 停 ⇒ 0.5／`nest:1` ⇒ 0.25／無名冊 ⇒ 1）
        ck(equiv_units_weighted([(_F2, (), 4.0)], I_DAY, NEST24)[0] == 0.5,
           "I16 whole-machine WO on a 4-seat machine with 2 dormant = 0.5")
        ck(equiv_units_weighted([(_F2, ("nest:1",), 4.0)], I_DAY, NEST24)[0] == 0.25,
           "I17 one named seat on a 4-seat machine = 0.25")
        ck(equiv_units_weighted([("EID-99999", (), 4.0)], I_DAY, NEST24)[0] == 1.0,
           "I18 a machine with no unit register keeps factor 1")
        _f, _t = equiv_units_weighted([(_F2, (), 3.0), ("EID-99999", (), 1.0)], I_DAY, NEST24)
        ck(abs(_f - (3.0 * 0.5 + 1.0) / 4.0) < 1e-12 and _t == 4.0,
           "I19 factors are weighted by down hours, not averaged")
        ck(equiv_units_weighted([], I_DAY, []) == (None, 0.0)
           and equiv_units_weighted([(_F2, (), 0.0)], I_DAY, NEST24) == (None, 0.0),
           "I20 zero down hours means no factor at all (caller uses 1.0)")
        # 「自哪天」（C-D3）
        ck(seat_dormant_since(_F2, "nest:2", I_DAY, NEST24) == "2026-08-01",
           "I26 nest seat dormant-since")
        ck(seat_dormant_since(_F2, "nest:1", I_DAY, NEST24) is None,
           "I27 a live seat has no since")
        ck(seat_dormant_since(_F2, "nest:2", I_DAY,
                              NEST2 + [{"eid": _F2, "sub_unit": "nest:2",
                                        "ts_effective": D(2026, 8, 5),
                                        "state": STATE_NOT_SCHEDULED}]) == "2026-08-01",
           "I28 a repeated registration keeps the start of THIS dormant run")
        ck(seat_dormant_since(_F2, "nest:2", I_DAY,
                              NEST2 + [{"eid": _F2, "sub_unit": "nest:2",
                                        "ts_effective": D(2026, 8, 5),
                                        "state": STATE_IN_PRODUCTION},
                                       {"eid": _F2, "sub_unit": "nest:2",
                                        "ts_effective": D(2026, 8, 20),
                                        "state": STATE_NOT_SCHEDULED}]) == "2026-08-20",
           "I29 after a return to production, since is the NEW run")
        ck(seat_dormant_since("EID-99999", WHOLE_UNIT, I_DAY,
                              [{"eid": "EID-99999", "ts_effective": D(2026, 7, 3),
                                "state": STATE_NOT_SCHEDULED}]) == "2026-07-03",
           "I30 whole-machine since delegates to board_dormant")

        # 讀端：舊日帳（缺 seats）⇒ installed == active ＋ legacy 旗
        ck(rw_seats_of({"n_eid": 3}) == {"installed": 3, "active": 3, "dormant": [], "legacy": True},
           "I21 legacy rw block reads as installed == active")
        ck(rw_seats_of({"n_eid": 2, "seats": {"installed": 8, "active": 6,
                                              "dormant": [f"{_F2}/nest:2"]}})
           == {"installed": 8, "active": 6, "dormant": [f"{_F2}/nest:2"], "legacy": False},
           "I22 new rw block reads its own seats")
        ck(rw_seats_of(None)["legacy"] is True and rw_seats_of({"seats": 7})["legacy"] is True,
           "I23 malformed seats degrade to legacy, never to zero dormant")
        ck(rw_planned_sec({"planned_sec": 10.0}) == 10.0
           and rw_planned_sec({"planned_sec": 10.0}, nameplate=True) == 10.0,
           "I24 legacy rw block: declared and nameplate are the same number")
        ck(rw_planned_sec({"planned_sec": 8.0, "planned_sec_nameplate": 10.0}) == 8.0
           and rw_planned_sec({"planned_sec": 8.0, "planned_sec_nameplate": 10.0},
                              nameplate=True) == 10.0,
           "I25 new rw block: two denominators, two answers")

    else:
        print("board_capacity selftest: skipped: sections D-I (as-of events / factors / A11 fixtures / seat layer) need roster roles the synthetic roster does not provide (4-seat pair, 12-seat, 3-seat, bridge seat pair, serial parent, co-tag)")

    # H. structural no-clock and no-timezone-conversion pins
    for fn in (active_units, dormant_units, capacity_factor, capacity_split):
        ck(tuple(inspect.signature(fn).parameters) == ("eid", "day", "events"),
           "H %s house signature order" % fn.__name__)
    ck(tuple(inspect.signature(equiv_units_factor).parameters) == ("eid", "affected_units", "day", "events"),
       "H equiv_units_factor house signature order")
    for name, fn in sorted((name, obj) for name, obj in globals().items()
                           if inspect.isfunction(obj) and obj.__module__ == __name__ and name != "_selftest"):
        sig = inspect.signature(fn)
        for pname, parameter in sig.parameters.items():
            ck(not isinstance(parameter.default, (_dt.date, _dt.datetime)),
               "H %s(%s) has no date default" % (name, pname))
        ck("now" not in sig.parameters, "H %s has no now parameter" % name)
    source = inspect.getsource(sys.modules[__name__])
    body = source.split("def _selftest")[0]
    ck(all(token not in body for token in ("date.today", ".today()", "datetime.now", "utcnow", "time.time")),
       "H module body has no clock reads")
    forbidden_plus8 = "timedelta(" + "hours=8)"
    ck(forbidden_plus8 not in source, "H source has no +8h conversion")

    print("board_capacity selftest: %d checks PASS" % n)
    return 0


def main():
    parser = argparse.ArgumentParser(description="equipment unit capacity as-of projection")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
