# -*- coding: utf-8 -*-
"""
wsynth_world.py — 合成產線的世界產生器（SPEC-SYNTHLINE WP-1；純函式、零 I/O）
================================================================================

一句話：`(seed, WORLD_RNG_SALT, fleet, 手寫參數)` → 一個確定性的合成世界（`generator_version` 只進
`world_id` 與 manifest、不進亂數；兩者解耦見 `wsynth_params` 檔頭「版號兩個」），同時投影成
兩份帳——工單事件帳 `tendline.seed.v1.1`（Board 與 cmms 共用；v1.1 起含料號主檔 `inventory.item`）與 MES 段帳
`tendline.mes_segments.v1`（Board 私用）。

鐵則對照（全文＝SPEC-SYNTHLINE §3 與裁決 RULINGS-v1）
  · C-SY1 零真資料：唯一輸入＝seed、版號、機隊定義、`wsynth_params`。本檔不開任何檔、
    不讀 `_fixtures`／snapshot／`_state`／人力名冊 JSON／平面圖 JSON／知識卡。
  · Q2 機隊＝**由程式名冊常數反推**（`fleet_from_roster`）：只 import 常數，不抄任何站名、
    機台名、EID 字面值；凍結模組（`mes_timeline`）只 import 不改。
    `board_tags.PRODUCT_TAGS` **只讀 `stations` 一欄**（機台會出現在哪些站＝結構），
    段數、family、first/last_seen 等統計欄一律不讀（N1）。
  · C-SY5 亂數＝`blake2b(seed‖WORLD_RNG_SALT‖key)` 計數器式派生（g3 以前鹽＝版號；g4 起解耦）；禁 `random`、`hash()`、set 迭代序、
    產生器內取現在時間。所有迭代都走排序過的 list／tuple。
  · 逐日確定性＋前綴穩定：`generate_day(ctx, day, carry)` 只依 `(ctx, day, carry)`；carry 是
    可 JSON 化、可摘要（`carry_digest`）的前日結轉狀態。延長時間軸只**追加**，不改寫已曝光的行。
  · C-SY7 單一世界兩份投影：每日先產工單事件（含其停機區間），再依區間產 MES 段；
    停機期間無生產級段，TEST_RUN 暫停期間只有試跑段（`Engineering (R&D)`）。
  · C-SY8 只產原子資料：段、事件。不產任何聚合數字。
  · Q7 人名欄一律角色字串閉集；notes 一律模板句。
  · Q16／Q17：種子檔日界＝台北 00:00；事件 `at`＝UTC `Z`、秒精度。MES 段＝台北 naive。

曝光規則（C-SY4；第二批 Q-H）：事件 `at <= until`、段 `end <= until` 才曝光（下游凍結查詢
只回已結束的段、以 start 篩窗 ⇒ 段看 end 不看 start）；生產 run 切成 10–90 分短段，無段
超過 90 分。`until` 由呼叫端傳入（正式運行＝`wsynth_clock.now()`），本檔永不自己取時間。

★★ 形③ 父機與座的段歸屬（第二批 Q-I）——**這是合成世界的約定，不是對真實 MES 行為的主張**：
  父機與它的每個座（子 EID）都各自有段；父機的段＝其所有座的段的**聯集**，逐段以同一
  `stp_seq`／start／end co-tag 在父機 EID 上（`unit` 欄記來源座）。父機本身不另產獨立段。
  同理，形② co-tag 機的子 EID 段＝母機段逐段 co-tag 到其中一個子 EID。

第一期範圍（第二批 Q-E）：只產稼動＋工單所需的原子資料（段、工單事件、A11）；不為良率、
顆數、在製品產生任何東西（段的 `result`＝stpTestResult 語意、供 last_production 用）。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json

import wsynth_params as P
from wsynth_clock import day_start, to_utc_z

_MIN = _dt.timedelta(minutes=1)
_DAY = _dt.timedelta(days=1)


# ===========================================================================
# 0) 計數器式亂數（C-SY5）
# ===========================================================================

class Rng:
    """`blake2b(seed‖salt‖scope‖key)` 派生的無狀態亂數。同 key 永遠同值、與呼叫順序無關。
    `salt`＝`wsynth_params.WORLD_RNG_SALT`（**不是** generator_version；g4 起解耦，鹽固定 g3 值
    ⇒ 世界內容不隨版號重抽）。"""

    __slots__ = ("_pre",)

    def __init__(self, seed: str, salt: str):
        self._pre = ("%s\x1f%s\x1f" % (seed, salt)).encode("utf-8")

    def u(self, *key) -> float:
        msg = self._pre + "\x1f".join(_key_str(k) for k in key).encode("utf-8")
        h = hashlib.blake2b(msg, digest_size=8).digest()
        return int.from_bytes(h, "big") / 18446744073709551616.0   # 2**64

    def randint(self, lo: int, hi: int, *key) -> int:
        v = lo + int(self.u(*key) * (hi - lo + 1))
        return hi if v > hi else v

    def chance(self, p: float, *key) -> bool:
        return self.u(*key) < p

    def choice(self, seq, *key):
        return seq[min(len(seq) - 1, int(self.u(*key) * len(seq)))]

    def weighted(self, pairs, *key):
        total = sum(w for _v, w in pairs)
        x = self.u(*key) * total
        acc = 0.0
        for v, w in pairs:
            acc += w
            if x < acc:
                return v
        return pairs[-1][0]


def _key_str(k) -> str:
    if isinstance(k, _dt.datetime):
        return k.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(k, (tuple, list)):
        return "(" + ",".join(_key_str(x) for x in k) + ")"
    return str(k)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ===========================================================================
# 1) 機隊定義（Q2：由程式名冊常數反推；只 import、不抄字面）
# ===========================================================================

def _roster_sources():
    """import 既有模組的名冊常數。全部是純常數模組、import 零 I/O（selftest 以攔截驗證）。"""
    import mes_timeline as _mt              # 凍結模組：只取常數
    import board_capacity as _wcap
    import board_dormant as _wdorm
    import board_eqmeeting as _wem
    import board_scope as _wscope
    import board_tags as _wtags
    return {
        "catalog": tuple(_wem.EQMT_CATALOG),
        "parent_expansion": dict(_wscope.PARENT_EXPANSION),
        "nest_units": {e: tuple(v["nests"]) for e, v in _wcap.NEST_EAV_UNITS.items()},
        "cotag_units": {e: tuple(v) for e, v in _wcap.COTAG_CHILD_UNITS.items()},
        "station_order": tuple(_mt.EQP_STATIONS),
        "class_aliases": {k: tuple(v) for k, v in _mt.CLASS_ALIASES.items()},
        "product_tags": _wtags.PRODUCT_TAGS,
        # A11 覆蓋面的基線播種名冊（`board_data.a11_eids()` 第④段的同一個常數；g2 起入機隊）
        "a11_baseline": tuple(_wdorm.A11_BASELINE_EIDS),
        "seat_wire_id": _wcap.seat_wire_id,
    }


def default_zero_len_stations():
    """零工期八站（人工審定常數；唯一出處＝`board_attrib.ZERO_LEN_STATIONS`）。"""
    import board_attrib as _wattr
    return frozenset(_wattr.ZERO_LEN_STATIONS)


def _stations_of(eid, src):
    """這台機會出現的稼動站（`EQP_STATIONS` 路由序）。只讀 PRODUCT_TAGS 的 `stations` 欄；
    別名站依 `CLASS_ALIASES` 折回主站（C-RW21 同語意）。"""
    rec = src["product_tags"].get(eid)
    if rec is None:
        return ()
    inv = {a: main for main, al in src["class_aliases"].items() for a in al}
    raw = rec["stations"]
    folded = {inv.get(s, s) for s in raw}
    return tuple(s for s in src["station_order"] if s in folded)


def fleet_from_roster(src=None):
    """由名冊常數反推第一期機隊。回傳依 asset_id 排序的 list[dict]。

    每台欄位（`define` 內＝進 `asset.define` 事件的部分；其餘為產生器內部用）：
      asset_id, area, display_name, station_cls, stations, parent_asset_id, seats, roles,
      gen（own＝自己產段／derived＝形③ 父機，段由座的段 co-tag 而來／none＝無稼動站）,
      units（自己產段的單位：nest 機＝各 `nest:n`，其餘＝(None,)）, cotag_children, children
    """
    src = src or _roster_sources()
    pe = src["parent_expansion"]
    nests = src["nest_units"]
    cotag = src["cotag_units"]
    parent_of = {c: p for p in sorted(pe) for c in pe[p]}
    cotag_parent_of = {c: p for p in sorted(cotag) for c in cotag[p]}
    area_of = {}
    name_of = {}
    for area, name, eid in src["catalog"]:
        area_of.setdefault(eid, area)
        name_of.setdefault(eid, name)
    # g2：機隊＝設備目錄 ∪ 形③ 父機 ∪ 形② co-tag 子機 ∪ A11 基線名冊。後兩類若不在目錄裡，
    # 只有資產定義（passive：不產工單、不產自己的段、不擲長停），讓長停覆蓋面
    # （`board_data.a11_eids()`）每一顆都查得到資產，不致在假 cmms 回 404 而整批退橋。
    extra = set(cotag_parent_of) | set(src.get("a11_baseline", ()))
    ids = sorted(set(area_of) | set(pe) | extra)
    fleet = []
    for eid in ids:
        children = tuple(pe.get(eid, ()))
        passive = eid not in area_of and not children
        area = area_of.get(eid)
        if area is None and children:
            area = next((area_of[c] for c in children if c in area_of), None)
        if area is None and eid in cotag_parent_of:
            area = area_of.get(cotag_parent_of[eid])
        stations = _stations_of(eid, src)
        if passive:
            gen = "none"
            units = ()
        elif children:
            gen = "derived"
            units = ()
        elif stations:
            gen = "own"
            units = tuple("nest:%d" % n for n in nests[eid]) if eid in nests else (None,)
        else:
            gen = "none"
            units = ()
        if eid in nests:
            seats = [{"unit": "nest:%d" % n, "asset_id": src["seat_wire_id"](eid, "nest:%d" % n)}
                     for n in nests[eid]]
        elif eid in cotag:
            seats = [{"unit": c, "asset_id": c} for c in cotag[eid]]
        elif children:
            seats = [{"unit": c, "asset_id": c} for c in children]
        else:
            seats = []
        roles = []
        if children:
            roles.append("contains_module")
        if eid in cotag:
            roles.append("cotag")
        fleet.append({
            "asset_id": eid,
            "area": area,
            # 形③ 父機不在設備目錄 ⇒ 無顯示名；以 asset_id 代（不造名字，見回報「解讀」）
            "display_name": name_of.get(eid, eid),
            "station_cls": stations[0] if stations else None,
            "stations": list(stations),
            "parent_asset_id": parent_of.get(eid) or cotag_parent_of.get(eid),
            "seats": seats,
            "roles": roles,
            "gen": gen,
            "units": units,
            "cotag_children": tuple(cotag.get(eid, ())),
            "children": children,
            "nests": tuple(nests.get(eid, ())),
            "passive": passive,
        })
    return fleet


DEFINE_KEYS = ("station_cls", "stations", "display_name", "area", "parent_asset_id",
               "seats", "roles", "in_service_from")


def fleet_digest(fleet) -> str:
    payload = [{"asset_id": a["asset_id"], **{k: a[k] for k in DEFINE_KEYS if k in a}}
               for a in sorted(fleet, key=lambda a: a["asset_id"])]
    return hashlib.sha256(_canon(payload).encode("utf-8")).hexdigest()


def world_id_of(seed: str, version: str, fdigest: str, flags=None) -> str:
    """spec：`blake2b(seed‖generator_version‖fleet_digest)` 前 16 hex。
    旗標非預設（只在測試）時把旗標併入，避免「關掉病的世界」與正式世界同 id。"""
    msg = "%s\x1f%s\x1f%s" % (seed, version, fdigest)
    if flags is not None and dict(flags) != P.DEFAULT_FLAGS:
        msg += "\x1fflags=" + _canon(dict(sorted(flags.items())))
    return hashlib.blake2b(msg.encode("utf-8"), digest_size=8).hexdigest()


# ===========================================================================
# 2) Context
# ===========================================================================

class Context:
    def __init__(self, seed, version, fleet, flags, horizon_start, zero_len_stations,
                 seat_wire_id, rng_salt=P.WORLD_RNG_SALT):
        self.seed = seed
        self.version = version            # generator_version：只進 world_id／manifest
        self.rng_salt = rng_salt          # 亂數鹽：只進 Rng（解耦，見 wsynth_params 檔頭）
        self.fleet = sorted(fleet, key=lambda a: a["asset_id"])
        self.by_id = {a["asset_id"]: a for a in self.fleet}
        self.flags = dict(flags)
        self.horizon_start = horizon_start
        self.zero_len = frozenset(zero_len_stations)
        self.seat_wire_id = seat_wire_id
        self.rng = Rng(seed, rng_salt)
        self.fleet_digest = fleet_digest(self.fleet_for_define())
        self.world_id = world_id_of(seed, version, self.fleet_digest, self.flags)
        self.parent_of = {c: a["asset_id"] for a in self.fleet for c in a["children"]}

    def fleet_for_define(self):
        out = []
        for a in self.fleet:
            d = dict(a)
            d["in_service_from"] = self.horizon_start.isoformat()
            out.append(d)
        return out

    def flag(self, name):
        return bool(self.flags.get(name))


def make_context(seed=P.DEFAULT_SEED, *, version=P.GENERATOR_VERSION, fleet=None, flags=None,
                 horizon_start=P.HORIZON_START, zero_len_stations=None, seat_wire_id=None,
                 rng_salt=P.WORLD_RNG_SALT):
    if fleet is None or seat_wire_id is None:
        src = _roster_sources()
        fleet = fleet if fleet is not None else fleet_from_roster(src)
        seat_wire_id = seat_wire_id or src["seat_wire_id"]
    fl = dict(P.DEFAULT_FLAGS)
    if flags:
        unknown = sorted(set(flags) - set(P.DISEASES))
        if unknown:
            raise ValueError("unknown disease flags: %s" % unknown)
        fl.update({k: bool(v) for k, v in flags.items()})
    zl = default_zero_len_stations() if zero_len_stations is None else zero_len_stations
    return Context(seed, version, fleet, fl, horizon_start, zl, seat_wire_id, rng_salt)


# ===========================================================================
# 3) carry（前日結轉；可 JSON 化）
# ===========================================================================

def initial_carry(ctx):
    return {"day": None, "next_wo": P.WO_NO_FIRST, "next_stp": 1,
            "busy": {}, "blocks": [], "trials": [], "dormant": {}}


def carry_digest(carry) -> str:
    return hashlib.sha256(_canon(carry).encode("utf-8")).hexdigest()


def _iso(d: _dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def _dtp(s: str) -> _dt.datetime:
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


def _bkey(kind, eid, unit=None) -> str:
    return "%s|%s|%s" % (kind, eid, unit or "")


# ===========================================================================
# 4) 工單生命週期（先產事件＋停機區間；C-SY7）
# ===========================================================================

def is_downtime(to_status, hold_reason, work_type) -> bool:
    """與 `board_eqmeeting._phase_kind` 同語意的 wire 旗標（count ⇒ True）。"""
    if to_status == "IN_PROGRESS":
        return True
    if to_status == "ON_HOLD":
        return hold_reason not in ("WAITING_MACHINE_TIME", "TEST_RUN")
    if to_status == "OPEN":
        return work_type == "REACTIVE"
    return False


class _DayBuf:
    """一日的輸出緩衝＋同時刻事件的確定序。"""

    def __init__(self, ctx, day, carry):
        self.ctx = ctx
        self.day = day
        self.ord = (day - ctx.horizon_start).days
        self.events = []          # [(sortkey, event)]
        self.counter = 0
        self.busy = dict(carry["busy"])
        self.blocks = [list(b) for b in carry["blocks"]]
        self.trials = [list(t) for t in carry["trials"]]
        self.dormant = {k: dict(v) for k, v in carry["dormant"].items()}
        self.next_wo = carry["next_wo"]
        self.next_stp = carry["next_stp"]

    def ev(self, at, kind, asset_id, actor, **fields):
        at = at.replace(microsecond=0)
        e = {"schema": P.SEED_SCHEMA, "world_id": self.ctx.world_id, "at": to_utc_z(at),
             "kind": kind, "asset_id": asset_id, "actor": actor}
        e.update(fields)
        self.events.append(((at, self.ord, self.counter), e))
        self.counter += 1
        return e

    def block(self, key, s, e):
        if e > s:
            self.blocks.append([key, _iso(s), _iso(e)])

    def trial(self, eid, unit, s, e, tid):
        if e > s:
            self.trials.append([eid, unit or "", _iso(s), _iso(e), tid])

    def new_wo(self):
        n = self.next_wo
        if n > P.WO_NO_LAST:
            raise OverflowError("synthetic WO range exhausted")
        self.next_wo += 1
        return n


def _minutes(rng, rng_range, *key):
    return _dt.timedelta(minutes=rng.randint(rng_range[0], rng_range[1], *key))


def _trial_targets(ctx, asset, scope_children, seat_unit):
    """TEST_RUN 期間試跑段落在哪些 (eid, unit) 上。"""
    a = asset
    if seat_unit:
        return [(a["asset_id"], seat_unit)]
    if a["children"]:
        kids = scope_children if scope_children else list(a["children"])
        return [(c, None) for c in kids if ctx.by_id.get(c, {}).get("gen") == "own"]
    if a["gen"] != "own":
        return []
    return [(a["asset_id"], a["units"][0])]


def _emit_reactive(buf, asset, t_f, *, wire_id, block_targets, seat_unit=None,
                   scope_children=None, archetype):
    """一張 REACTIVE 單的完整生命週期（事件＋停機／試跑區間）。回傳機台可再故障的時刻。"""
    ctx, rng = buf.ctx, buf.ctx.rng
    wo = buf.new_wo()
    first_idx = len(buf.events)
    K = ("wo", wo)
    symptom = rng.choice(P.SYMPTOMS, *K, "symptom")
    has_fsa = rng.chance(P.P_FSA, *K, "fsa")
    opened = t_f + (_minutes(rng, P.FSA_LEAD_MIN, *K, "lead") if has_fsa else _dt.timedelta(0))
    win_start = t_f
    scope = sorted(scope_children or [])
    buf.ev(opened, "wo.open", wire_id, "role:operator",
           work_order_no=wo, work_type="REACTIVE",
           brief_description=P.BRIEF_REACTIVE.format(symptom=symptom),
           failure_started_at=to_utc_z(t_f) if has_fsa else None,
           scope=list(scope), sub_units=[], pm_source_id=None)
    buf.ev(opened, "wo.note", wire_id, "role:operator", work_order_no=wo, entry_type="report",
           body=P.NOTE_TEMPLATES["report"].format(symptom=symptom))

    def status(at, frm, to, actor, hold=None):
        buf.ev(at, "wo.status", wire_id, actor, work_order_no=wo, from_status=frm,
               to_status=to, hold_reason=hold, is_downtime=is_downtime(to, hold, "REACTIVE"))

    def finish_fields(t_done):
        buf.ev(t_done, "wo.field", wire_id, "role:technician", work_order_no=wo,
               field="diagnosis", value=P.DIAGNOSIS_TEMPLATE.format(symptom=symptom))
        action = rng.choice(P.ACTIONS, *K, "action")
        buf.ev(t_done, "wo.field", wire_id, "role:technician", work_order_no=wo,
               field="action_taken", value=P.ACTION_TEMPLATE.format(action=action))
        buf.ev(t_done, "wo.note", wire_id, "role:technician", work_order_no=wo,
               entry_type="note", body=P.NOTE_TEMPLATES["note"].format(action=action))

    def pra(value):
        lag = _minutes(rng, P.PRA_WRITE_LAG_MIN, *K, "pralag")
        buf.ev(value + lag, "wo.field", wire_id, "role:technician", work_order_no=wo,
               field="production_resumed_at", value=to_utc_z(value))

    def close(t_done):
        if not rng.chance(P.P_STAY_COMPLETED, *K, "stay"):
            status(t_done + _minutes(rng, P.CLOSE_AFTER_MIN, *K, "close"),
                   "COMPLETED", "CLOSED", "role:engineer")

    down = []            # 生產級段禁止的區間
    trials = []          # 只准試跑段的區間
    t_ip = opened + _minutes(rng, P.RESPONSE_MIN, *K, "resp")
    repair = _minutes(rng, P.REPAIR_MIN, *K, "repair")

    if archetype == "cancelled":
        t_x = opened + _minutes(rng, P.CANCEL_AFTER_MIN, *K, "cancel")
        status(t_x, "OPEN", "CANCELLED", "role:engineer")
        down.append((win_start, t_x))
        t_end = t_x
    elif archetype == "one_click":
        t_c = t_ip + repair
        # 一鍵結單：note 先寫（終態後不得再有事件），三轉移同一時戳（MTTR＝0 的病）
        buf.ev(t_c, "wo.note", wire_id, "role:technician", work_order_no=wo, entry_type="note",
               body=P.NOTE_TEMPLATES["note"].format(action=rng.choice(P.ACTIONS, *K, "action")))
        status(t_c, "OPEN", "IN_PROGRESS", "role:technician")
        status(t_c, "IN_PROGRESS", "COMPLETED", "role:technician")
        status(t_c, "COMPLETED", "CLOSED", "role:technician")
        down.append((win_start, t_c))
        t_end = t_c
    else:
        status(t_ip, "OPEN", "IN_PROGRESS", "role:technician")
        buf.ev(t_ip, "wo.note", wire_id, "role:technician", work_order_no=wo,
               entry_type="progress", body=P.NOTE_TEMPLATES["progress"].format(symptom=symptom))
        if archetype == "plain":
            t_done = t_ip + repair
            status(t_done, "IN_PROGRESS", "COMPLETED", "role:technician")
            finish_fields(t_done)
            if rng.chance(P.P_PRA, *K, "pra"):
                pra(t_done)
            close(t_done)
            down.append((win_start, t_done))
            t_end = t_done
        elif archetype == "hold_down":
            reason = rng.choice(P.HOLD_DOWN_REASONS, *K, "reason")
            t_h = t_ip + _dt.timedelta(minutes=rng.randint(10, 60, *K, "pre_hold"))
            status(t_h, "IN_PROGRESS", "ON_HOLD", "role:technician", hold=reason)
            buf.ev(t_h, "wo.note", wire_id, "role:technician", work_order_no=wo,
                   entry_type="hold", body=P.NOTE_TEMPLATES["hold"].format(reason=reason))
            if reason == "WAITING_PARTS":
                k = rng.randint(0, P.ITEM_CODE_POOL - 1, *K, "item")
                item = item_code(k)
                qty = rng.randint(1, 4, *K, "qty")
                buf.ev(t_h, "wo.part", wire_id, "role:technician", work_order_no=wo,
                       item_code=item, quantity=qty)
                buf.ev(t_h, "wo.note", wire_id, "role:technician", work_order_no=wo,
                       entry_type="part",
                       body=P.NOTE_TEMPLATES["part"].format(item_code=item, quantity=qty))
            t_r = t_h + _minutes(rng, P.HOLD_MIN[reason], *K, "hold")
            status(t_r, "ON_HOLD", "IN_PROGRESS", "role:technician")
            buf.ev(t_r, "wo.note", wire_id, "role:technician", work_order_no=wo,
                   entry_type="resume", body=P.NOTE_TEMPLATES["resume"].format(reason=reason))
            t_done = t_r + repair
            status(t_done, "IN_PROGRESS", "COMPLETED", "role:technician")
            finish_fields(t_done)
            if rng.chance(P.P_PRA, *K, "pra"):
                pra(t_done)
            close(t_done)
            down.append((win_start, t_done))
            t_end = t_done
        elif archetype == "hold_test":
            t_fix = t_ip + repair
            status(t_fix, "IN_PROGRESS", "ON_HOLD", "role:technician", hold="TEST_RUN")
            buf.ev(t_fix, "wo.note", wire_id, "role:technician", work_order_no=wo,
                   entry_type="hold", body=P.NOTE_TEMPLATES["hold"].format(reason="TEST_RUN"))
            t_done = t_fix + _minutes(rng, P.HOLD_MIN["TEST_RUN"], *K, "hold")
            status(t_done, "ON_HOLD", "COMPLETED", "role:technician")
            finish_fields(t_done)
            pra(t_done)
            close(t_done)
            down.append((win_start, t_done))
            trials.append((t_fix, t_done))
            t_end = t_done
        elif archetype == "hold_wmt":
            t_tmp = t_ip + repair
            pra(t_tmp)
            status(t_tmp, "IN_PROGRESS", "ON_HOLD", "role:technician",
                   hold="WAITING_MACHINE_TIME")
            buf.ev(t_tmp, "wo.note", wire_id, "role:technician", work_order_no=wo,
                   entry_type="hold",
                   body=P.NOTE_TEMPLATES["hold"].format(reason="WAITING_MACHINE_TIME"))
            t_r = t_tmp + _minutes(rng, P.HOLD_MIN["WAITING_MACHINE_TIME"], *K, "hold")
            status(t_r, "ON_HOLD", "IN_PROGRESS", "role:technician")
            buf.ev(t_r, "wo.note", wire_id, "role:technician", work_order_no=wo,
                   entry_type="resume",
                   body=P.NOTE_TEMPLATES["resume"].format(reason="WAITING_MACHINE_TIME"))
            t_done = t_r + _dt.timedelta(minutes=rng.randint(30, 120, *K, "final"))
            status(t_done, "IN_PROGRESS", "COMPLETED", "role:technician")
            finish_fields(t_done)
            close(t_done)
            down.append((win_start, t_tmp))
            down.append((t_r, t_done))
            t_end = t_done
        else:  # pragma: no cover
            raise ValueError(archetype)

    # scope 變更（形③ scoped 單中途追加一座）
    scope_from = {c: win_start for c in scope}
    asset_children = list(asset["children"])
    if scope and ctx.flag("seat_level_wo") and len(scope) < len(asset_children) \
            and archetype not in ("cancelled",) \
            and rng.chance(P.P_SCOPE_CHANGE, *K, "scope_change"):
        extra = [c for c in asset_children if c not in scope]
        add = rng.choice(extra, *K, "scope_add")
        # g2 修正：時刻由 IN_PROGRESS 起算（恆晚於開單），並夾在 (開單, 停機終點) 之內
        # ——g1 由故障起點起算，開單延遲長而整段短時會早於開單（違反「開單是最早事件」）。
        frac = 0.2 + 0.4 * rng.u(*K, "scope_t")
        t_sc = (t_ip + (t_end - t_ip) * frac).replace(microsecond=0)
        t_sc = min(max(t_sc, opened + _MIN), t_end - _MIN)
        new_scope = sorted(scope + [add])
        buf.ev(t_sc, "wo.scope", wire_id, "role:technician", work_order_no=wo,
               scope=list(new_scope), sub_units=[])
        scope_from[add] = t_sc

    _assert_wo_bounds(buf, first_idx, wo)
    # 停機區間落鍵
    for kind, eid, unit in block_targets:
        for s, e in down:
            buf.block(_bkey(kind, eid, unit), s, e)
    for c, t0 in sorted(scope_from.items()):
        for s, e in down:
            s2 = max(s, t0)
            buf.block(_bkey("u", asset["asset_id"], c), s2, e)
    # 試跑區間
    for s, e in trials:
        targets = _trial_targets(ctx, asset, [c for c, t0 in sorted(scope_from.items()) if t0 <= s],
                                 seat_unit)
        for eid, unit in targets:
            buf.trial(eid, unit, s, e, wo)
    return t_end


def _assert_wo_bounds(buf, first_idx, wo):
    """產生端自檢：同一工單每筆事件 開單時刻 <= at <= 終態時刻，且開單在最前、終態後無事件。"""
    evs = sorted([(k, e) for k, e in buf.events[first_idx:] if e.get("work_order_no") == wo],
                 key=lambda x: x[0])
    if not evs or evs[0][1]["kind"] != "wo.open":
        raise AssertionError("WO %s: wo.open is not the earliest event" % wo)
    t_open = evs[0][0][0]
    term_seen = False
    for (at, _o, _c), e in evs:
        if at < t_open or term_seen:
            raise AssertionError("WO %s: %s at %s outside [open, terminal]" % (wo, e["kind"], at))
        if e["kind"] == "wo.status" and e["to_status"] in ("CLOSED", "CANCELLED", "VOIDED"):
            term_seen = True


def item_code(k: int) -> str:
    n = P.ITEM_CODE_FIRST + k
    if not P.ITEM_CODE_FIRST <= n <= P.ITEM_CODE_LAST:
        raise OverflowError("synthetic item code out of range")
    return "%s%0*d" % (P.ITEM_CODE_PREFIX, P.ITEM_CODE_WIDTH, n)


def item_name(k: int) -> str:
    """料號池第 k 筆的顯示名（seed v1.1 `inventory.item.display_name`）：模板句，類別依 k 確定性輪替。
    **不碰 RNG**（確定性索引）⇒ 加產料號主檔不改任何既有事件內容（SPEC §5.8-④）。"""
    cats = P.ITEM_CATEGORIES
    return P.ITEM_NAME_TEMPLATE.format(category=cats[k % len(cats)], index=k // len(cats) + 1)


def item_master():
    """整個合成料號池（`wo.part` 只從這個池抽：`item_code(randint(0, ITEM_CODE_POOL-1))`），依料號升冪。"""
    return [(item_code(k), item_name(k)) for k in range(P.ITEM_CODE_POOL)]


_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _base36(n: int, width: int) -> str:
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = _B36[r] + out
    return out.rjust(width, "0")


def lot_of(stp_seq: int) -> str:
    """段的 lot＝等同序號的 lot（Q-N：S000001–S0ZZZZZ），由 stp_seq 派生。"""
    n = P.SERIAL_FIRST + stp_seq - 1
    if not P.SERIAL_FIRST <= n <= P.SERIAL_LAST:
        raise OverflowError("synthetic serial range exhausted")
    return P.SERIAL_PREFIX + _base36(n, P.SERIAL_WIDTH)


def _emit_pm(buf, asset, day, interval):
    ctx, rng = buf.ctx, buf.ctx.rng
    eid = asset["asset_id"]
    K0 = ("pm", eid, day)
    opened = day_start(day) + _dt.timedelta(hours=P.PROD_DAY_START_H) \
        + _minutes(rng, P.PM_OPEN_MIN, *K0, "open")
    if _dtp(buf.busy.get(eid, "1900-01-01T00:00:00")) > opened or _is_dormant(buf, eid, None, opened):
        return
    wo = buf.new_wo()
    first_idx = len(buf.events)
    K = ("wo", wo)
    buf.ev(opened, "wo.open", eid, "role:system", work_order_no=wo, work_type="PM",
           brief_description=P.BRIEF_PM.format(interval=interval), failure_started_at=None,
           scope=[], sub_units=[], pm_source_id=None)

    def status(at, frm, to, actor):
        buf.ev(at, "wo.status", eid, actor, work_order_no=wo, from_status=frm, to_status=to,
               hold_reason=None, is_downtime=is_downtime(to, None, "PM"))

    t_ip = opened + _minutes(rng, P.PM_START_AFTER_MIN, *K, "start")
    t_done = t_ip + _minutes(rng, P.PM_WORK_MIN, *K, "work")
    # note 先寫（一鍵結單時終態與 note 同一時戳，終態後不得再有事件）
    buf.ev(t_done, "wo.note", eid, "role:technician", work_order_no=wo, entry_type="note",
           body=P.NOTE_TEMPLATES["note"].format(action=rng.choice(P.ACTIONS, *K, "action")))
    if ctx.flag("one_click_close") and rng.chance(P.P_PM_ONE_CLICK, *K, "oneclick"):
        status(t_done, "OPEN", "IN_PROGRESS", "role:technician")
        status(t_done, "IN_PROGRESS", "COMPLETED", "role:technician")
        status(t_done, "COMPLETED", "CLOSED", "role:technician")
    else:
        status(t_ip, "OPEN", "IN_PROGRESS", "role:technician")
        status(t_done, "IN_PROGRESS", "COMPLETED", "role:technician")
        if not rng.chance(P.P_STAY_COMPLETED, *K, "stay"):
            status(t_done + _minutes(rng, P.CLOSE_AFTER_MIN, *K, "close"),
                   "COMPLETED", "CLOSED", "role:engineer")
    _assert_wo_bounds(buf, first_idx, wo)
    # 實體停機：機台在 PM 施工期間不生產（一鍵結單時 Board 看不到這段＝病，MES 看得到空檔）
    buf.block(_bkey("m", eid), t_ip, t_done)
    buf.busy[eid] = _iso(t_done)


def _is_dormant(buf, eid, unit, at):
    for key in (_bkey("m", eid), _bkey("u", eid, unit) if unit else None):
        if key and key in buf.dormant:
            d = buf.dormant[key]
            f = _dt.date.fromisoformat(d["from"])
            u = _dt.date.fromisoformat(d["until"]) if d["until"] else None
            if at.date() >= f and (u is None or at.date() < u):
                return True
    return False


def _maybe_dormancy(buf, asset, day):
    ctx, rng = buf.ctx, buf.ctx.rng
    eid = asset["asset_id"]
    # 已結束的長停從結轉移除
    for key in sorted(buf.dormant):
        d = buf.dormant[key]
        if d["until"] and _dt.date.fromisoformat(d["until"]) <= day:
            del buf.dormant[key]
    if not ctx.flag("long_stop"):
        return
    if asset["children"]:
        return                                   # 形③ 父機本身不登記；座（子 EID）各自登記
    if asset["asset_id"] in ctx.parent_of and not ctx.flag("seat_level_wo"):
        return                                   # 形③ 座的長停屬座級事件
    # 長停以「可獨立停排的單位」為粒度擲骰（座＝最小產能單位，SPEC-CAPACITY-FORECAST）：
    # 整機一條 lane；形① 機台的每個座各一條 sub_unit lane（座級＝seat_level_wo 旗標）。
    lanes = [None]
    if asset["nests"] and ctx.flag("seat_level_wo"):
        lanes += ["nest:%d" % n for n in asset["nests"]]
    for unit in lanes:
        if _bkey("m", eid) in buf.dormant:
            return
        key = _bkey("u", eid, unit) if unit else _bkey("m", eid)
        if key in buf.dormant:
            continue
        K = ("dorm", eid, unit or "", day)
        if rng.chance(P.P_DORMANT_START_PER_DAY, *K):
            _start_dormancy(buf, eid, unit, key, day, K)
            if unit is None:
                return


def _start_dormancy(buf, eid, unit, key, day, K):
    rng = buf.ctx.rng
    if rng.chance(P.P_DORMANT_OPEN_ENDED, *K, "open"):
        until = None
    else:
        until = day + _dt.timedelta(days=rng.randint(P.DORMANT_DAYS[0], P.DORMANT_DAYS[1], *K, "len"))
    buf.dormant[key] = {"from": day.isoformat(), "until": until.isoformat() if until else None}
    rec = _dt.timedelta(hours=P.A11_RECORD_HOUR)
    buf.ev(day_start(day) + rec, "asset.schedule_state", eid, "role:planner",
           state="not_scheduled", effective_date=day.isoformat(), sub_unit=unit,
           reason=P.A11_REASON_TEMPLATE["not_scheduled"])
    if until is not None:
        buf.ev(day_start(until) + rec, "asset.schedule_state", eid, "role:planner",
               state="in_production", effective_date=until.isoformat(), sub_unit=unit,
               reason=P.A11_REASON_TEMPLATE["in_production"])


def _reactive_archetype(ctx, key):
    pairs = []
    fold = 0.0
    for name, w in P.REACTIVE_ARCHETYPES:
        off = ((name.startswith("hold_") and not ctx.flag("on_hold_reasons"))
               or (name == "one_click" and not ctx.flag("one_click_close")))
        if off:
            fold += w
        else:
            pairs.append((name, w))
    pairs = [(n, w + fold) if n == "plain" else (n, w) for n, w in pairs]
    return ctx.rng.weighted(pairs, *key)


def _maybe_reactive(buf, asset, day):
    ctx, rng = buf.ctx, buf.ctx.rng
    eid = asset["asset_id"]
    is_child = eid in ctx.parent_of
    if is_child and not ctx.flag("seat_level_wo"):
        return                                   # 形③ 座自己的單＝座級事件
    tier = P.P_FAIL_PARENT if asset["children"] else rng.choice(P.P_FAIL_TIERS, "failtier", eid)
    K = ("fail", eid, day)
    if not rng.chance(tier, *K):
        return
    t_f = day_start(day) + _dt.timedelta(seconds=rng.randint(0, 24 * 3600 - 1, *K, "t"))
    archetype = _reactive_archetype(ctx, K + ("arch",))
    seat_unit = None
    scope_children = None
    if asset["nests"] and ctx.flag("seat_level_wo") and rng.chance(P.P_SEAT_WO_NEST, *K, "seat"):
        seat_unit = "nest:%d" % rng.choice(asset["nests"], *K, "nest")
        wire_id = ctx.seat_wire_id(eid, seat_unit)
        targets = [("u", eid, seat_unit)]
    elif asset["children"]:
        wire_id = eid
        if ctx.flag("seat_level_wo") and rng.chance(P.P_PARENT_SCOPED, *K, "scoped"):
            kids = list(asset["children"])
            n = rng.randint(1, len(kids) - 1, *K, "nscope")
            order = sorted(kids, key=lambda c: rng.u(*K, "pick", c))
            scope_children = sorted(order[:n])
            targets = []
        else:
            targets = [("m", eid, None)]
    else:
        wire_id = eid
        targets = [("m", eid, None)]
    busy_key = wire_id
    if _dtp(buf.busy.get(busy_key, "1900-01-01T00:00:00")) > t_f:
        return
    if _is_dormant(buf, eid, seat_unit, t_f):
        return
    t_end = _emit_reactive(buf, asset, t_f, wire_id=wire_id, block_targets=targets,
                           seat_unit=seat_unit, scope_children=scope_children, archetype=archetype)
    buf.busy[busy_key] = _iso(t_end)


# ===========================================================================
# 5) MES 段（由事件區間導出；C-SY7）
# ===========================================================================

def _plant_open(ctx, pday):
    wd = pday.weekday()
    return not ctx.rng.chance(P.P_PLANT_CLOSED_BY_WEEKDAY[wd], "plant", pday)


def _night_shift(ctx, pday):
    return ctx.rng.chance(P.P_NIGHT_SHIFT, "night", pday)


def shift_windows(ctx, day):
    """與台北日 `day` 相交的開線班窗（生產日界 08:00）。"""
    out = []
    for pday in (day - _DAY, day):
        if not _plant_open(ctx, pday):
            continue
        s = day_start(pday) + _dt.timedelta(hours=P.PROD_DAY_START_H)
        h = _dt.timedelta(hours=P.SHIFT_HOURS)
        out.append((s, s + h))
        if _night_shift(ctx, pday):
            out.append((s + h, s + 2 * h))
    lo, hi = day_start(day), day_start(day) + _DAY
    return [(s, e) for s, e in out if e > lo and s < hi]


def _candidates(ctx, eid, unit, win, day):
    """班窗 win 內 (eid, unit) 的候選段，只留 start 落在 day、且截在午夜與班窗尾。"""
    rng = ctx.rng
    util = rng.choice(P.UTIL_TIERS, "util", eid)
    lo, hi = day_start(day), day_start(day) + _DAY
    ws, we = win
    t = ws
    k = 0
    out = []
    while t < we and t < hi:
        K = ("seg", eid, unit or "", ws, k)
        if rng.chance(util, *K, "run"):
            s = t
            e = s + _minutes(rng, P.SEG_MINUTES, *K, "dur")
            e = min(e, we, hi)
            if s >= lo:
                out.append((s, e))
            t = e + _minutes(rng, P.SEG_GAP_MINUTES, *K, "gap")
        else:
            t = t + _minutes(rng, P.IDLE_MINUTES, *K, "idle")
        k += 1
    return out


_SEC = _dt.timedelta(seconds=1)


def off_day_boundary(s, e):
    """g3（2026-10-02，主持人明示重置；WP-6b）：**段永不恰好結束在生產日界 08:00:00**。

    為什麼：`core_prod_calendar.quorum_days` 以含端點的方式把「結束在 08:00:00」的段算進**新**生產日，
    而全廠沉默（closure）由同一刻起算 ⇒ 該日若此後整日無訊號，就成了「有訊號的日整日被 closure 蓋」、
    `ok_calendar` 拒收（WP-6 tick 實測：g2 夜班窗尾恰在 08:00:00 截斷，日曆步驟每輪 rc 3）。
    凍結的 `core_prod_calendar` 不改；由產生器避開這個端點（確定性、只動恰好落在界上的段）：
      · 有長度的段結束在界上 ⇒ 結束提前 1 秒（＝班次窗截到界前 1 秒；仍屬前一生產日）；
      · 零工期段落在界上（只可能是早班窗起點／試跑點）⇒ 整點往後 1 秒（仍屬新生產日、不改它歸屬哪天）。
    其餘段原樣回傳。"""
    if e.hour == P.PROD_DAY_START_H and e.minute == 0 and e.second == 0 and e.microsecond == 0:
        if s == e:
            return s + _SEC, e + _SEC
        return s, e - _SEC
    return s, e


def _intervals_for(blocks_by_key, keys):
    out = []
    for k in keys:
        out.extend(blocks_by_key.get(k, ()))
    return out


def _overlaps(s, e, ivs):
    for a, b in ivs:
        if s == e:
            if a <= s < b:
                return True
        elif s < b and e > a:
            return True
    return False


def _row_keys(ctx, eid, unit):
    keys = [_bkey("m", eid)]
    if unit:
        keys.append(_bkey("u", eid, unit))
    p = ctx.parent_of.get(eid)
    if p:
        keys.append(_bkey("m", p))
        keys.append(_bkey("u", p, eid))
    return keys


def _dormant_ivs(buf, keys, day):
    out = []
    for k in keys:
        d = buf.dormant.get(k)
        if d:
            f = day_start(_dt.date.fromisoformat(d["from"]))
            u = day_start(_dt.date.fromisoformat(d["until"])) if d["until"] else \
                day_start(day) + 2 * _DAY
            out.append((f, u))
    return out


def _segments_for_day(buf, day):
    ctx, rng = buf.ctx, buf.ctx.rng
    lo, hi = day_start(day), day_start(day) + _DAY
    blocks_by_key = {}
    for key, s, e in buf.blocks:
        blocks_by_key.setdefault(key, []).append((_dtp(s), _dtp(e)))
    wins = shift_windows(ctx, day)
    zl_on = ctx.flag("zero_length_stations")
    own = []    # (start, end, eid, unit, cls, designation)
    for a in ctx.fleet:
        if a["gen"] != "own":
            continue
        eid = a["asset_id"]
        stations = a["stations"]
        for unit in a["units"]:
            keys = _row_keys(ctx, eid, unit)
            block = _intervals_for(blocks_by_key, keys) + _dormant_ivs(buf, keys, day)
            rows = []
            for win in wins:
                for s, e in _candidates(ctx, eid, unit, win, day):
                    cls = rng.choice(stations, "cls", eid, unit or "", s)
                    if zl_on and cls in ctx.zero_len:
                        e = s
                    ks = s                                    # 亂數鍵用候選起點（g3 位移前）
                    s, e = off_day_boundary(s, e)
                    if _overlaps(s, e, block):
                        continue
                    desig = P.DESIGNATION_BASELINE if rng.chance(
                        P.P_ENGINEERING_BASELINE, "desig", eid, unit or "", ks) \
                        else P.DESIGNATION_PRODUCTION
                    rows.append((s, e, eid, unit, cls, desig))
            own.extend(rows)
    # 試跑段（TEST_RUN 暫停期間；只准試跑 designation）
    for eid, unit, s0, e0, tid in sorted(buf.trials):
        a = ctx.by_id.get(eid)
        if not a or a["gen"] != "own":
            continue
        unit = unit or None
        dorm = _dormant_ivs(buf, _row_keys(ctx, eid, unit), day)
        ts, te = _dtp(s0), _dtp(e0)
        t = ts
        k = 0
        while t < te and t < hi:
            K = ("trial", tid, eid, unit or "", k)
            t = t + _minutes(rng, P.TRIAL_EVERY_MINUTES, *K, "every")
            s = t
            e = min(s + _minutes(rng, P.TRIAL_SEG_MINUTES, *K, "dur"), te, hi)
            k += 1
            if s >= te or s >= hi or e <= s:
                break
            if s >= lo:
                cls = rng.choice(a["stations"], "cls", eid, unit or "", s)
                if zl_on and cls in ctx.zero_len:
                    e = s
                es, ee = off_day_boundary(s, e)           # g3：只動輸出，迴圈游標照原值走
                if not _overlaps(es, ee, dorm):
                    own.append((es, ee, eid, unit, cls, P.DESIGNATION_TRIAL))
            t = e
    # 同一 (eid, unit) 去重疊（保守：丟後到者）
    own.sort(key=lambda r: (r[2], r[3] or "", r[0], r[1], r[5]))
    kept = []
    last_end = {}
    for r in own:
        k = (r[2], r[3] or "")
        le = last_end.get(k)
        if le is not None and r[0] < le:
            continue
        kept.append(r)
        last_end[k] = max(r[1], le) if le else r[1]
    kept.sort(key=lambda r: (r[0], r[2], r[3] or "", r[5]))
    out = []
    for s, e, eid, unit, cls, desig in kept:
        stp = buf.next_stp
        buf.next_stp += 1
        result = "FAIL" if rng.chance(P.P_SEG_FAIL, "result", eid, unit or "", s) else "PASS"
        base = {"schema": P.MES_SCHEMA, "world_id": ctx.world_id, "cls": cls, "start": _iso(s),
                "end": _iso(e), "designation": desig, "result": result, "lot": lot_of(stp),
                "stp_seq": stp, "operator_hash": None}
        out.append(dict(base, eid=eid, unit=unit))
        a = ctx.by_id[eid]
        if a["cotag_children"]:
            child = rng.choice(a["cotag_children"], "cotag", stp)
            out.append(dict(base, eid=child, unit=None))
        p = ctx.parent_of.get(eid)
        if p:
            out.append(dict(base, eid=p, unit=eid))
    return out


# ===========================================================================
# 6) 逐日產生
# ===========================================================================

def _pm_interval(ctx, eid):
    return ctx.rng.choice(P.PM_INTERVAL_DAYS, "pmint", eid)


def generate_day(ctx, day, carry, *, with_segments=True):
    """一日的世界：回傳 `(events, segments, carry_out)`。

    `events` 為 `[(sortkey, event_dict)]`（event 尚無 `seq`）；`segments` 為 list[dict]。
    只依 `(ctx, day, carry)`，不看 horizon 終點、不看現在時刻。"""
    if carry.get("day") is not None and _dt.date.fromisoformat(carry["day"]) + _DAY != day:
        raise ValueError("carry is for %s, cannot generate %s" % (carry["day"], day))
    buf = _DayBuf(ctx, day, carry)
    if day == ctx.horizon_start:
        at0 = day_start(day)
        for a in ctx.fleet_for_define():
            buf.ev(at0, "asset.define", a["asset_id"], "role:system",
                   **{k: a[k] for k in DEFINE_KEYS})
        # seed v1.1：料號主檔緊接資產定義、同一刻、一次產完整池（先於任何 wo.part；不碰 RNG）
        for code, name in item_master():
            buf.ev(at0, "inventory.item", P.ITEM_ASSET_ID, "role:system",
                   item_code=code, display_name=name)
    for a in ctx.fleet:
        if a.get("passive"):
            continue                              # 只有資產定義：無工單、無長停登記
        _maybe_dormancy(buf, a, day)
        # PM：形③ 座由父機 PM 涵蓋；其餘有自身 PM 週期
        if a["asset_id"] not in ctx.parent_of:
            interval = _pm_interval(ctx, a["asset_id"])
            phase = ctx.rng.randint(0, interval - 1, "pmphase", a["asset_id"])
            if (day - P.PM_EPOCH).days % interval == phase:
                _emit_pm(buf, a, day, interval)
        _maybe_reactive(buf, a, day)
    segments = _segments_for_day(buf, day) if with_segments else []
    nxt = day_start(day) + _DAY
    carry_out = {
        "day": day.isoformat(),
        "next_wo": buf.next_wo,
        "next_stp": buf.next_stp if with_segments else carry["next_stp"],
        "busy": {k: v for k, v in sorted(buf.busy.items()) if _dtp(v) > nxt},
        "blocks": sorted([b for b in buf.blocks if _dtp(b[2]) > nxt]),
        "trials": sorted([t for t in buf.trials if _dtp(t[3]) > nxt]),
        "dormant": {k: buf.dormant[k] for k in sorted(buf.dormant)},
    }
    return buf.events, segments, carry_out


# ===========================================================================
# 7) 世界＝逐日累積＋曝光
# ===========================================================================

def build_world(until, seed=P.DEFAULT_SEED, *, ctx=None, with_segments=True, keep_carries=False,
                **ctx_kw):
    """產出曝光到 `until`（台北 naive）為止的世界。

    回傳 dict：world_id, seed, generator_version, fleet_digest, flags, horizon_start,
    exposed_until（皆為字串）、events（依 seq 排序、含 seq）、segments（依 (end, stp_seq, eid)
    排序）、carries（keep_carries=True 時：每日產生前的 carry）。"""
    if not isinstance(until, _dt.datetime) or until.tzinfo is not None:
        raise ValueError("until must be a naive Taipei datetime")
    until = until.replace(microsecond=0)
    ctx = ctx or make_context(seed, **ctx_kw)
    if until < day_start(ctx.horizon_start):
        raise ValueError("until precedes horizon start")
    carry = initial_carry(ctx)
    all_ev = []
    all_seg = []
    carries = []
    day = ctx.horizon_start
    while day <= until.date():
        if keep_carries:
            carries.append((day.isoformat(), json.loads(_canon(carry))))
        evs, segs, carry = generate_day(ctx, day, carry, with_segments=with_segments)
        all_ev.extend(evs)
        all_seg.extend(segs)
        day += _DAY
    exposed = sorted([(k, e) for k, e in all_ev if k[0] <= until], key=lambda x: x[0])
    events = []
    for i, (_k, e) in enumerate(exposed, start=1):
        e2 = {"seq": i}
        e2.update(e)
        events.append(e2)
    segs = [s for s in all_seg if s["end"] <= _iso(until)]
    segs.sort(key=lambda s: (s["end"], s["stp_seq"], s["eid"], s["unit"] or ""))
    return {
        "world_id": ctx.world_id,
        "seed": ctx.seed,
        "generator_version": ctx.version,
        "fleet_digest": ctx.fleet_digest,
        "flags": dict(sorted(ctx.flags.items())),
        "horizon_start": to_utc_z(day_start(ctx.horizon_start)),
        "exposed_until": to_utc_z(until),
        "events": events,
        "segments": segs,
        "carries": carries,
        "ctx": ctx,
    }
