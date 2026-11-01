# -*- coding: utf-8 -*-
"""
wsynth_seed.py — `tendline.seed.v1.1` 種子契約的 validator 與 normalize（SPEC-SYNTHLINE WP-2；§5）
=================================================================================================

v1.1（2026-10-02，DRAFT；SPEC §5.8）：`inventory.item` 入閉集（`asset_id`＝哨兵 `ITEM_ASSET_ID`）；
帳級新增 `unknown_item`／`item_redefined`／`scope_non_reactive`／`pra_non_reactive`／`pra_not_after_start`；
`fsa_after_open` 收緊為「不嚴格早於」；schema 字串 `tendline.seed.v1.1`（v1 ⇒ schema_mismatch）。

定位
  · 契約＝工單事件帳（JSONL，一行一事件）＋ `seed-manifest.json`。Board 定、Board 與 cmms 共用。
  · 本檔是**契約的可執行版**：以 WP-1 產生器（`wsynth_world`／`wsynth_emit`）**實際產出的形**為準；
    規範全文＝SPEC-SYNTHLINE §5（含 §5.7 位元形與雜湊、測試向量）。
  · 純函式、**零 I/O**：不開檔、不連網、不讀環境變數。讀檔由呼叫端做（本檔 `--selftest` 的
    主程式、`_fixtures/selftest_fixtures.py` 都是呼叫端）。位元組的解析（`split_day_bytes`、
    `parse_manifest_bytes`）吃呼叫端讀到的 bytes，本身不開檔。
  · 閉集一律寫**字面值**（＝對版時朗讀的那一份）；selftest 另釘它們與 `wsynth_params`、
    `wsynth_world`、`board_w8`、`board_eqmeeting` 逐值相等，任一邊改了而另一邊沒跟上 ⇒ 紅。

讀法（QG-3 ②③）
  · **拒讀**（`SeedRejected`，整份不讀）：`schema` 不符、`world_id` 與預期不符、續讀時 manifest
    指紋與 state 不符（`state_mismatch`）、測試世界未經明示允許（`test_world`）。
  · **寬進（消費方模式，預設）**：未知 `kind` 跳過並計數；保留 kind（`pm.schedule`，
    不屬 v1.1）跳過並另計；事件多出未知欄位 ⇒ normalize 丟掉。
  · **嚴格模式（宣告方自驗，`strict=True`）**：保留 kind ⇒ `reserved_kind`；未知 kind ⇒ `closed_set`；
    多出欄位 ⇒ `envelope`。
  · 每一行**不論 kind 是否已知**，先驗全部共同欄位（含 actor 角色閉集）；通過後才決定跳不跳。
  · 行級壞資料 ⇒ 該行丟棄、記錯誤，不炸；非 UTF-8 ⇒ `encoding`，不拋例外。

錯誤紀錄＝`{code, seq, line, detail}`（`seq`＝該行 seq 欄的值、`line`＝該行在本次輸入中的
1 起位置；manifest 層錯誤兩者皆 null）。錯誤類別閉集 `ERROR_CODES`：
  拒讀   schema_mismatch  world_mismatch  state_mismatch  test_world
  行級   envelope  encoding  field  closed_set  range  reserved_kind
  帳級   seq_order  at_order  horizon  exposure
         wo_first_not_open  wo_duplicate_open  wo_asset_mismatch  wo_status_chain
         wo_after_terminal  hold_reason_missing  hold_reason_unexpected  is_downtime_mismatch
         fsa_after_open  unknown_asset  asset_redefined
         unknown_item  item_redefined  scope_non_reactive  pra_non_reactive  pra_not_after_start（v1.1）
  manifest  manifest  manifest_mismatch

CLI：`python wsynth_seed.py --selftest`
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import re

import wsynth_clock as _clock

# ===========================================================================
# 契約字面值（對版朗讀的那一份；selftest 釘與宣告方逐值相等）
# ===========================================================================

SCHEMA = "tendline.seed.v1.1"                   # v1.1（2026-10-02）；v1 字串一律 schema_mismatch
MANIFEST_SCHEMA = "tendline.seed.manifest.v1.1"

COMMON_KEYS = ("schema", "world_id", "seq", "at", "kind", "asset_id", "actor")

ROLES = ("role:operator", "role:technician", "role:engineer", "role:planner", "role:system")

# 現行閉集（名稱沿用 KINDS_V1＝「v1 家族」；v1.1 起含 inventory.item）
KINDS_V1 = ("asset.define", "wo.open", "wo.status", "wo.scope", "wo.note", "wo.part",
            "wo.field", "asset.schedule_state", "inventory.item")
KINDS_V1_1 = KINDS_V1
RESERVED_KINDS = ("pm.schedule",)                       # 不屬 v1.1；宣告方不得產出
KINDS_PHASE1 = KINDS_V1                                 # 舊名（WP-2 初版）
KINDS = KINDS_V1 + RESERVED_KINDS

# inventory.item 的共同欄位 asset_id＝哨兵（料號主檔沒有資產；非 null、合 EID 形；不是資產、
# 永不由 asset.define 定義、消費方不得拿它查資產主檔）。SPEC §5.2／§5.8-①。
ITEM_ASSET_ID = "EID-00000"

# 每種 kind 的專屬欄位。全部「鍵必在」；標可空者值可為 null。
KIND_KEYS = {
    "asset.define": ("station_cls", "stations", "display_name", "area", "parent_asset_id",
                     "seats", "roles", "in_service_from"),
    "wo.open": ("work_order_no", "work_type", "brief_description", "failure_started_at",
                "scope", "sub_units", "pm_source_id"),
    "wo.status": ("work_order_no", "from_status", "to_status", "hold_reason", "is_downtime"),
    "wo.scope": ("work_order_no", "scope", "sub_units"),
    "wo.note": ("work_order_no", "entry_type", "body"),
    "wo.part": ("work_order_no", "item_code", "quantity"),
    "wo.field": ("work_order_no", "field", "value"),
    "asset.schedule_state": ("state", "effective_date", "sub_unit", "reason"),
    "inventory.item": ("item_code", "display_name"),
}
# fleet_digest 的投影鍵（＝`wsynth_world.DEFINE_KEYS`；selftest 釘）
DEFINE_KEYS = ("station_cls", "stations", "display_name", "area", "parent_asset_id",
               "seats", "roles", "in_service_from")

WORK_TYPES = ("PM", "REACTIVE")
WO_STATUSES = ("OPEN", "IN_PROGRESS", "ON_HOLD", "COMPLETED", "CLOSED", "CANCELLED", "VOIDED")
TERMINAL_STATUSES = ("CLOSED", "CANCELLED", "VOIDED")
HOLD_REASONS = ("WAITING_MACHINE_TIME", "TEST_RUN", "WAITING_PARTS", "WAITING_VENDOR", "OTHER")
UPTIME_HOLD_REASONS = ("WAITING_MACHINE_TIME", "TEST_RUN")      # is_downtime=false 的兩值
NOTE_ENTRY_TYPES = ("report", "progress", "hold", "resume", "part", "note")
WO_FIELDS = ("diagnosis", "action_taken", "confirmed_reason_code", "production_resumed_at",
             "downtime_estimated", "scheduled_date", "assigned_vendor")
WO_FIELDS_EMITTED_V1 = ("diagnosis", "action_taken", "production_resumed_at")
ASSET_ROLES = ("contains_module", "cotag", "alias_of")
SCHEDULE_STATES = ("not_scheduled", "in_production")

# 資料特性旗標（「病」旗標；SPEC §5.1、§5.7）。全為預設＝正式世界。
DISEASE_FLAGS = ("zero_length_stations", "on_hold_reasons", "one_click_close", "long_stop",
                 "seat_level_wo")
DEFAULT_FLAGS = {name: True for name in DISEASE_FLAGS}

# 合成保留區段（第二批裁決 Q-N）
WO_NO_FIRST, WO_NO_LAST = 710001, 799999
ITEM_CODE_RE = re.compile(r"^TL(\d{6})$")
ITEM_CODE_FIRST, ITEM_CODE_LAST = 900001, 999999

MANIFEST_KEYS = ("schema", "world_id", "generator_version", "fleet_digest", "horizon_start",
                 "exposed_until", "days")
MANIFEST_OPTIONAL_KEYS = ("flags", "test_only")
MANIFEST_DAY_KEYS = ("day", "file", "n_events", "sha256")
# 續讀指紋（F7）。v1.1 審查 Finding 1 起含 manifest `schema`：消費方快取／續讀鍵＝(world_id, schema)
# 以上；v1 讀者存下的 state（指紋無 schema）在 v1.1 讀者續讀 ⇒ state_mismatch（不混讀兩版帳形）。
STATE_FINGERPRINT_KEYS = ("schema", "world_id", "generator_version", "fleet_digest", "horizon_start")

REJECT_CODES = ("schema_mismatch", "world_mismatch", "state_mismatch", "test_world")
LINE_CODES = ("envelope", "encoding", "field", "closed_set", "range", "reserved_kind")
LEDGER_CODES = ("seq_order", "at_order", "horizon", "exposure",
                "wo_first_not_open", "wo_duplicate_open", "wo_asset_mismatch", "wo_status_chain",
                "wo_after_terminal", "hold_reason_missing", "hold_reason_unexpected",
                "is_downtime_mismatch", "fsa_after_open", "unknown_asset", "asset_redefined",
                "unknown_item", "item_redefined", "scope_non_reactive", "pra_non_reactive",
                "pra_not_after_start")
MANIFEST_CODES = ("manifest", "manifest_mismatch")
ERROR_CODES = REJECT_CODES + LINE_CODES + LEDGER_CODES + MANIFEST_CODES

_WORLD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ASSET_ID_RE = re.compile(r"^EID-\d{5}(-N[1-9]\d*)?$")
_MACHINE_ID_RE = re.compile(r"^EID-\d{5}$")
_NEST_UNIT_RE = re.compile(r"^nest:([1-9]\d*)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SeedRejected(ValueError):
    """拒讀（整份不讀）。`code` ∈ REJECT_CODES。"""

    def __init__(self, code, detail=""):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


class Unreadable:
    """`split_day_bytes` 對讀不出的行放的佔位（非 UTF-8 ⇒ encoding；非 JSON ⇒ envelope）。"""

    __slots__ = ("code", "detail")

    def __init__(self, code, detail):
        self.code = code
        self.detail = detail

    def __repr__(self):
        return "Unreadable(%s)" % self.code


# ===========================================================================
# 小工具（純）
# ===========================================================================

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v):
    return isinstance(v, str)


def _nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def _is_utc_z(v):
    return isinstance(v, str) and _clock.is_utc_z(v)


def _is_date(v):
    if not (isinstance(v, str) and _DATE_RE.match(v)):
        return False
    try:
        _dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def taipei_day(at_utc_z: str) -> str:
    """事件 `at`（UTC Z）→ 所屬種子檔的台北日（日界＝台北 00:00，Q16）。"""
    return _clock.from_utc_z(at_utc_z).date().isoformat()


def is_downtime(to_status, hold_reason, work_type) -> bool:
    """wire 旗標 `is_downtime` 的契約定義（＝`board_eqmeeting._phase_kind(...)=='count'`，
    但 COMPLETED／終態恆 false）。"""
    if to_status == "IN_PROGRESS":
        return True
    if to_status == "ON_HOLD":
        return hold_reason not in UPTIME_HOLD_REASONS
    if to_status == "OPEN":
        return work_type == "REACTIVE"
    return False


# ===========================================================================
# 位元形與雜湊（SPEC §5.7；另一語言的實作者照規格就能重算）
# ===========================================================================

def canonical_json(obj) -> str:
    """canonical JSON 文字：鍵依 code point 排序、分隔 `,` `:` 無空白、非 ASCII 不跳脫、
    不做 Unicode 正規化。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_line(event) -> bytes:
    """一行的正規位元組＝canonical JSON 的 UTF-8 ＋ LF。"""
    return (canonical_json(event) + "\n").encode("utf-8")


def day_bytes(events) -> bytes:
    """某日檔的正規位元組＝依序串接各行 `canonical_line`（檔尾因此恆有 LF）。"""
    return b"".join(canonical_line(e) for e in events)


def day_sha256(events) -> str:
    """manifest `days[].sha256`＝SHA-256(日檔全部位元組) 的小寫 hex。"""
    return hashlib.sha256(day_bytes(events)).hexdigest()


def day_file_name(day: str) -> str:
    return "seed-%s.jsonl" % day


def fleet_digest_of(define_events) -> str:
    """`fleet_digest`＝SHA-256(UTF-8(canonical_json(投影陣列))) 的小寫 hex；
    投影陣列＝每個 asset.define 取 `asset_id`＋DEFINE_KEYS 八鍵，依 asset_id code point 升冪。"""
    payload = [dict({"asset_id": e["asset_id"]}, **{k: e[k] for k in DEFINE_KEYS if k in e})
               for e in sorted(define_events, key=lambda e: e["asset_id"])]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def flags_are_default(flags) -> bool:
    return flags is None or dict(flags) == DEFAULT_FLAGS


def world_id_of(seed: str, generator_version: str, fleet_digest: str, flags=None) -> str:
    """`world_id`＝BLAKE2b（**digest_size＝8 位元組**，非 512 位元截斷）的小寫 hex（16 字）；
    輸入＝UTF-8( seed ‖ 0x1F ‖ generator_version ‖ 0x1F ‖ fleet_digest )；
    旗標非預設時再接 UTF-8( 0x1F ‖ "flags=" ‖ canonical_json(旗標五鍵) )。"""
    msg = "%s\x1f%s\x1f%s" % (seed, generator_version, fleet_digest)
    if not flags_are_default(flags):
        msg += "\x1fflags=" + canonical_json(dict(sorted(dict(flags).items())))
    return hashlib.blake2b(msg.encode("utf-8"), digest_size=8).hexdigest()


def _err(errors, code, seq, line, detail):
    errors.append({"code": code, "seq": seq, "line": line, "detail": detail})


# ===========================================================================
# 位元組解析（吃呼叫端讀到的 bytes；不拋未處理例外）
# ===========================================================================

def split_day_bytes(raw: bytes):
    """日檔位元組 → list（每行一個 json 物件；讀不出的行＝`Unreadable` 佔位）。
    逐行獨立解碼：一行壞不牽連其他行。"""
    out = []
    if not isinstance(raw, (bytes, bytearray)):
        return [Unreadable("encoding", "day file is not bytes")]
    for chunk in bytes(raw).split(b"\n"):
        if chunk == b"":
            continue
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            out.append(Unreadable("encoding", "line is not valid UTF-8 (%s)" % exc.reason))
            continue
        try:
            out.append(json.loads(text))
        except ValueError:
            out.append(Unreadable("envelope", "line is not valid JSON"))
    return out


def parse_manifest_bytes(raw: bytes):
    """manifest 位元組 → `(manifest|None, errors[(code, detail)])`。"""
    if not isinstance(raw, (bytes, bytearray)):
        return None, [("encoding", "manifest is not bytes")]
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, [("encoding", "manifest is not valid UTF-8 (%s)" % exc.reason)]
    try:
        return json.loads(text), []
    except ValueError:
        return None, [("manifest", "manifest is not valid JSON")]


# ===========================================================================
# 一行事件
# ===========================================================================

def check_event(ev, *, strict=False):
    """一行的行級檢查（不含跨行不變式、不含 schema／world_id 拒讀判定）。
    回傳 `(status, errors)`：status ∈ {"ok", "bad", "unknown_kind", "reserved_kind"}；
    errors＝[(code, detail)]。共同欄位（含 actor 閉集）對每一行都先驗，通過才看 kind。
    strict 下 unknown／reserved kind 以 status "bad" 回報（`closed_set`／`reserved_kind`）。"""
    if isinstance(ev, Unreadable):
        return "bad", [(ev.code, ev.detail)]
    if not isinstance(ev, dict):
        return "bad", [("envelope", "line is not a JSON object")]
    missing = [k for k in COMMON_KEYS if k not in ev]
    if missing:
        return "bad", [("envelope", "missing common keys %s" % missing)]
    errs = []
    if not _is_int(ev["seq"]) or ev["seq"] < 1:
        errs.append(("envelope", "seq must be a positive int"))
    if not _is_utc_z(ev["at"]):
        errs.append(("envelope", "at must be UTC ISO Z with second precision"))
    if not _is_str(ev["kind"]):
        errs.append(("envelope", "kind must be str"))
    if not (_is_str(ev["asset_id"]) and _ASSET_ID_RE.match(ev["asset_id"])):
        errs.append(("envelope", "asset_id must be EID-ddddd or EID-ddddd-N<n>"))
    if not _is_str(ev["world_id"]) or not _WORLD_ID_RE.match(ev["world_id"]):
        errs.append(("envelope", "world_id must be 16 lowercase hex"))
    if not _is_str(ev["schema"]):
        errs.append(("envelope", "schema must be str"))
    if ev["actor"] not in ROLES:
        errs.append(("closed_set", "actor %r not a role string" % (ev["actor"],)))
    if errs:
        return "bad", errs
    kind = ev["kind"]
    if kind in RESERVED_KINDS:
        if strict:
            return "bad", [("reserved_kind", "%s is reserved, not part of v1" % kind)]
        return "reserved_kind", []
    if kind not in KIND_KEYS:
        if strict:
            return "bad", [("closed_set", "unknown kind %r" % kind)]
        return "unknown_kind", []
    keys = KIND_KEYS[kind]
    miss = [k for k in keys if k not in ev]
    if miss:
        return "bad", [("field", "%s missing %s" % (kind, miss))]
    if strict:
        extra = sorted(set(ev) - set(COMMON_KEYS) - set(keys))
        if extra:
            errs.append(("envelope", "unexpected keys %s" % extra))
    errs.extend(_check_kind_fields(kind, ev))
    return ("bad" if errs else "ok"), errs


def _check_wo_no(ev, errs):
    n = ev["work_order_no"]
    if not _is_int(n):
        errs.append(("field", "work_order_no must be int"))
    elif not WO_NO_FIRST <= n <= WO_NO_LAST:
        errs.append(("range", "work_order_no %d outside synthetic range" % n))


def _seat_ok(owner, s):
    """座形（F3）：unit＝`nest:<n>` ⇒ asset_id＝owner＋`-N<n>`；unit＝子機 EID ⇒ asset_id＝unit。"""
    if not (isinstance(s, dict) and set(s) == {"unit", "asset_id"}):
        return False
    unit, aid = s["unit"], s["asset_id"]
    if not (_is_str(unit) and _is_str(aid)):
        return False
    m = _NEST_UNIT_RE.match(unit)
    if m:
        return aid == "%s-N%s" % (owner, m.group(1))
    if _MACHINE_ID_RE.match(unit):
        return aid == unit and unit != owner
    return False


def _check_item_code(ic, errs):
    m = ITEM_CODE_RE.match(ic) if _is_str(ic) else None
    if m is None:
        errs.append(("field", "item_code must be TL + 6 digits"))
    elif not ITEM_CODE_FIRST <= int(m.group(1)) <= ITEM_CODE_LAST:
        errs.append(("range", "item_code %s outside synthetic range" % ic))


def _check_kind_fields(kind, ev):
    errs = []
    if kind == "asset.define":
        if not _MACHINE_ID_RE.match(ev["asset_id"]):
            errs.append(("field", "asset.define asset_id must be a machine id (no -N suffix)"))
        elif ev["asset_id"] == ITEM_ASSET_ID:
            errs.append(("field", "asset.define must not define the item-master sentinel %s" % ITEM_ASSET_ID))
        if not (ev["station_cls"] is None or _nonempty_str(ev["station_cls"])):
            errs.append(("field", "station_cls must be str or null"))
        if not _str_list(ev["stations"]):
            errs.append(("field", "stations must be list[str]"))
        if not _nonempty_str(ev["display_name"]):
            errs.append(("field", "display_name must be non-empty str"))
        if not (ev["area"] is None or _nonempty_str(ev["area"])):
            errs.append(("field", "area must be str or null"))
        p = ev["parent_asset_id"]
        if not (p is None or (_is_str(p) and _MACHINE_ID_RE.match(p))):
            errs.append(("field", "parent_asset_id must be a machine EID or null"))
        seats = ev["seats"]
        if not (isinstance(seats, list) and all(_seat_ok(ev["asset_id"], s) for s in seats)):
            errs.append(("field", "seats must be list[{unit, asset_id}] with nest:<n> -> <owner>-N<n> "
                                  "or child EID -> same EID"))
        roles = ev["roles"]
        if not _str_list(roles):
            errs.append(("field", "roles must be list[str]"))
        elif any(r not in ASSET_ROLES for r in roles):
            errs.append(("closed_set", "roles %r outside closed set" % (roles,)))
        if not _is_date(ev["in_service_from"]):
            errs.append(("field", "in_service_from must be YYYY-MM-DD"))
    elif kind == "wo.open":
        _check_wo_no(ev, errs)
        if ev["work_type"] not in WORK_TYPES:
            errs.append(("closed_set", "work_type %r" % (ev["work_type"],)))
        if not _nonempty_str(ev["brief_description"]):
            errs.append(("field", "brief_description must be non-empty str"))
        f = ev["failure_started_at"]
        if not (f is None or _is_utc_z(f)):
            errs.append(("field", "failure_started_at must be UTC Z or null"))
        if not _str_list(ev["scope"]):
            errs.append(("field", "scope must be list[str]"))
        if not _str_list(ev["sub_units"]):
            errs.append(("field", "sub_units must be list[str]"))
        if not (ev["pm_source_id"] is None or _nonempty_str(ev["pm_source_id"])):
            errs.append(("field", "pm_source_id must be str or null"))
    elif kind == "wo.status":
        _check_wo_no(ev, errs)
        for k in ("from_status", "to_status"):
            if ev[k] not in WO_STATUSES:
                errs.append(("closed_set", "%s %r" % (k, ev[k])))
        if not (ev["hold_reason"] is None or _is_str(ev["hold_reason"])):
            errs.append(("field", "hold_reason must be str or null"))
        if not isinstance(ev["is_downtime"], bool):
            errs.append(("field", "is_downtime must be bool"))
    elif kind == "wo.scope":
        _check_wo_no(ev, errs)
        if not _str_list(ev["scope"]):
            errs.append(("field", "scope must be list[str]"))
        if not _str_list(ev["sub_units"]):
            errs.append(("field", "sub_units must be list[str]"))
    elif kind == "wo.note":
        _check_wo_no(ev, errs)
        if ev["entry_type"] not in NOTE_ENTRY_TYPES:
            errs.append(("closed_set", "entry_type %r" % (ev["entry_type"],)))
        if not _nonempty_str(ev["body"]):
            errs.append(("field", "body must be non-empty str"))
    elif kind == "wo.part":
        _check_wo_no(ev, errs)
        _check_item_code(ev["item_code"], errs)
        if not (_is_int(ev["quantity"]) and ev["quantity"] >= 1):
            errs.append(("field", "quantity must be int >= 1"))
    elif kind == "wo.field":
        _check_wo_no(ev, errs)
        fld, val = ev["field"], ev["value"]
        if fld not in WO_FIELDS:
            errs.append(("closed_set", "field %r" % (fld,)))
        elif fld == "production_resumed_at":
            if not _is_utc_z(val):
                errs.append(("field", "production_resumed_at value must be UTC Z"))
        elif fld == "scheduled_date":
            if not _is_date(val):
                errs.append(("field", "scheduled_date value must be YYYY-MM-DD"))
        elif fld == "downtime_estimated":
            # v1 不產；型別未與 cmms 對版 ⇒ 只收 JSON 純量（非 null）
            if val is None or isinstance(val, (list, dict)):
                errs.append(("field", "downtime_estimated value must be a non-null scalar"))
        elif not _nonempty_str(val):
            errs.append(("field", "%s value must be non-empty str" % fld))
    elif kind == "asset.schedule_state":
        if ev["state"] not in SCHEDULE_STATES:
            errs.append(("closed_set", "state %r" % (ev["state"],)))
        if not _is_date(ev["effective_date"]):
            errs.append(("field", "effective_date must be YYYY-MM-DD"))
        su = ev["sub_unit"]
        if not (su is None or (_is_str(su) and _NEST_UNIT_RE.match(su))):
            errs.append(("field", "sub_unit must be nest:<n> or null"))
        if not _nonempty_str(ev["reason"]):
            errs.append(("field", "reason must be non-empty str"))
    elif kind == "inventory.item":
        if ev["asset_id"] != ITEM_ASSET_ID:
            errs.append(("field", "inventory.item asset_id must be the item-master sentinel %s"
                         % ITEM_ASSET_ID))
        _check_item_code(ev["item_code"], errs)
        if not _nonempty_str(ev["display_name"]):
            errs.append(("field", "display_name must be non-empty str"))
    return errs


def _gate(ev, expected_world_id):
    """拒讀判定（只看 schema 與 world_id）。回 None 或 (code, detail)。"""
    if isinstance(ev, dict):
        if "schema" in ev and ev["schema"] != SCHEMA:
            return "schema_mismatch", "schema %r != %r" % (ev["schema"], SCHEMA)
        if expected_world_id is not None and "world_id" in ev and ev["world_id"] != expected_world_id:
            return "world_mismatch", "world_id %r != %r" % (ev["world_id"], expected_world_id)
    return None


def normalize_event(ev, *, expected_world_id=None, strict=False):
    """一行 → `(normalized|None, reason|None)`。

    · schema／world_id 不符 ⇒ raise `SeedRejected`（拒讀）。
    · 共同欄位先驗（任何 kind）；壞 ⇒ `(None, <第一個錯誤 code>)`，不拋例外。
    · 未知 kind ⇒ `(None, "unknown_kind")`；保留 kind ⇒ `(None, "reserved_kind")`（strict 下同碼）。
    · 合法 ⇒ 白名單複本（共同欄＋該 kind 專屬欄；多出的欄丟掉）。"""
    g = _gate(ev, expected_world_id)
    if g:
        raise SeedRejected(*g)
    status, errs = check_event(ev, strict=strict)
    if status in ("unknown_kind", "reserved_kind"):
        return None, status
    if status != "ok":
        return None, errs[0][0]
    keep = COMMON_KEYS + KIND_KEYS[ev["kind"]]
    return {k: copy.deepcopy(ev[k]) for k in keep}, None


# ===========================================================================
# manifest
# ===========================================================================

def check_manifest(m):
    """manifest 形狀。schema 不符 ⇒ raise `SeedRejected`；其他回 [(code, detail)]。"""
    if not isinstance(m, dict):
        return [("manifest", "manifest is not an object")]
    if m.get("schema") != MANIFEST_SCHEMA:
        raise SeedRejected("schema_mismatch", "manifest schema %r" % (m.get("schema"),))
    errs = []
    miss = [k for k in MANIFEST_KEYS if k not in m]
    if miss:
        return [("manifest", "missing %s" % miss)]
    extra = sorted(set(m) - set(MANIFEST_KEYS) - set(MANIFEST_OPTIONAL_KEYS))
    if extra:
        errs.append(("manifest", "unexpected manifest keys %s" % extra))
    if not (_is_str(m["world_id"]) and _WORLD_ID_RE.match(m["world_id"])):
        errs.append(("manifest", "world_id must be 16 lowercase hex"))
    if not _nonempty_str(m["generator_version"]):
        errs.append(("manifest", "generator_version must be non-empty str"))
    if not (_is_str(m["fleet_digest"]) and _HEX64_RE.match(m["fleet_digest"])):
        errs.append(("manifest", "fleet_digest must be 64 lowercase hex"))
    for k in ("horizon_start", "exposed_until"):
        if not _is_utc_z(m[k]):
            errs.append(("manifest", "%s must be UTC Z" % k))
    if not errs and m["exposed_until"] < m["horizon_start"]:
        errs.append(("manifest", "exposed_until precedes horizon_start"))
    flags = m.get("flags")
    if "flags" in m and not (isinstance(flags, dict) and set(flags) == set(DISEASE_FLAGS)
                             and all(isinstance(v, bool) for v in flags.values())):
        errs.append(("manifest", "flags must map exactly the five data-trait flags to bool"))
    elif "test_only" in m and m["test_only"] is not True:
        errs.append(("manifest", "test_only, when present, must be true"))
    elif "flags" in m and not flags_are_default(flags) and m.get("test_only") is not True:
        errs.append(("manifest", "non-default flags require test_only=true"))
    days = m["days"]
    if not isinstance(days, list):
        return errs + [("manifest", "days must be a list")]
    prev = None
    for d in days:
        if not (isinstance(d, dict) and set(d) == set(MANIFEST_DAY_KEYS)):
            errs.append(("manifest", "day entry must have exactly %s" % (MANIFEST_DAY_KEYS,)))
            continue
        if not _is_date(d["day"]) or d["file"] != day_file_name(d["day"]):
            errs.append(("manifest", "day/file mismatch %r" % (d,)))
        if not (_is_int(d["n_events"]) and d["n_events"] >= 0):
            errs.append(("manifest", "n_events must be int >= 0"))
        if not (_is_str(d["sha256"]) and _HEX64_RE.match(d["sha256"])):
            errs.append(("manifest", "sha256 must be 64 lowercase hex"))
        if prev is not None and _is_str(d["day"]) and d["day"] <= prev:
            errs.append(("manifest", "days must be strictly ascending"))
        prev = d["day"] if _is_str(d["day"]) else prev
    return errs


def is_test_world(m) -> bool:
    return isinstance(m, dict) and (m.get("test_only") is True
                                    or ("flags" in m and isinstance(m["flags"], dict)
                                        and not flags_are_default(m["flags"])))


def manifest_fingerprint(m):
    """manifest 的不可變指紋（續讀時比對；F7）。"""
    return {k: m.get(k) for k in STATE_FINGERPRINT_KEYS}


def check_day_files(manifest, files, *, partial=False):
    """呼叫端讀到的日檔原始位元組 `{file_name: bytes}` 對 manifest 的集合、sha256、行數。
    · 完整模式（預設）：manifest 列的每一個檔都必須在場；多出來的檔也算錯。
    · `partial=True`：允許只給部分已列的檔（明確參數才開）；多出來的檔仍算錯。
    回 [(code, detail)]（code＝manifest_mismatch）。"""
    errs = []
    by_file = {d["file"]: d for d in manifest.get("days", []) if isinstance(d, dict) and "file" in d}
    if not partial:
        for name in sorted(set(by_file) - set(files)):
            errs.append(("manifest_mismatch", "listed file %s is missing" % name))
    for name in sorted(files):
        raw = files[name]
        d = by_file.get(name)
        if d is None:
            errs.append(("manifest_mismatch", "file %s not listed in manifest" % name))
            continue
        if not isinstance(raw, (bytes, bytearray)):
            errs.append(("manifest_mismatch", "file %s is not bytes" % name))
            continue
        if hashlib.sha256(raw).hexdigest() != d["sha256"]:
            errs.append(("manifest_mismatch", "sha256 mismatch for %s" % name))
        n = raw.count(b"\n")
        if n != d["n_events"] or (raw and not raw.endswith(b"\n")) or b"\r" in raw:
            errs.append(("manifest_mismatch", "line count / line ending mismatch for %s" % name))
    return errs


# ===========================================================================
# 一整份帳（跨行不變式）
# ===========================================================================

def new_state():
    """增量讀取的結轉狀態（可 JSON 化）。cmms「每天只收新一天」用：前一段的 state 餵下一段。

    `master_complete`（v1.1 審查 Finding 2）＝**主檔基線已完整**：這條讀取鏈自帳的第一行（seq 1）
    起逐行讀到現在 ⇒ 每個早於目前位置的 `asset.define`／`inventory.item` 都已看過。為 True 時
    引用檢查（`unknown_asset`／`unknown_item`）一律強制——「主檔晚於引用它的事件」即錯。
    由 `validate_ledger` 在「無前段（last_seq＝0）且本段第一行 seq＝1」時設定，隨 state 結轉；
    從中段起讀的鏈永遠 False（看不到被略過的主檔，不判）。"""
    return {"fingerprint": None, "world_id": None, "last_seq": 0, "last_at": None,
            "master_complete": False,
            "assets": {}, "items": {}, "wos": {}, "day_counts": {}}


def validate_ledger(events, *, manifest=None, expected_world_id=None, state=None,
                    complete=False, strict=False, allow_test_world=False):
    """驗一整份帳（或一段接續的帳）。**永不拋例外**；拒讀條件記在 `report["rejected"]`。

    參數
      events            list（依檔內順序）；元素為已 json.loads 的物件或 `Unreadable`。
      manifest          有則驗 manifest、以其 world_id 為預期、驗曝光、逐日行數、fleet_digest。
      expected_world_id 無 manifest 時的預期 world_id（皆無 ⇒ state 或第一行）。
      state             前一段的結轉（`new_state()` 形，可經 JSON 往返）；None ⇒ 從頭。
                        state 帶 manifest 指紋；續讀時與本次 manifest 不符 ⇒ 拒讀 state_mismatch。
      complete          True ⇒ 整份帳讀到此為止：seq 自 1 起（無 state 時）、manifest 日集合必與已讀日相等；
                        且主檔引用檢查**一律強制**（帳宣稱完整 ⇒ 主檔基線必在帳內；無 asset.define／
                        inventory.item 的完整帳＝每個引用都報 unknown_*）。
                        False 時引用檢查只在 `state.master_complete`（自 seq 1 起讀）時強制。
      strict            True ⇒ 宣告方自驗（保留／未知 kind、多出欄位都算錯）。
      allow_test_world  True ⇒ 允許讀測試世界（非預設旗標／test_only）；預設拒讀。

    回傳 report：ok, rejected, codes, errors[{code, seq, line, detail}], n_lines, n_valid,
      skipped_unknown_kind, unknown_kinds{}, skipped_reserved, state, events（normalized）。"""
    errors = []
    rejected = None
    st = copy.deepcopy(state) if state is not None else new_state()
    for k, v in new_state().items():
        st.setdefault(k, copy.deepcopy(v))

    manifest_ok = False
    if manifest is not None:
        try:
            m_errs = check_manifest(manifest)
            for code, detail in m_errs:
                _err(errors, code, None, None, detail)
            manifest_ok = not m_errs
        except SeedRejected as exc:
            rejected = exc.code
            _err(errors, exc.code, None, None, exc.detail)
        if rejected is None and is_test_world(manifest) and not allow_test_world:
            rejected = "test_world"
            _err(errors, "test_world", None, None, "manifest marks a test-only world")
        if rejected is None and isinstance(manifest, dict):
            fp = manifest_fingerprint(manifest)
            if st["fingerprint"] is not None and st["fingerprint"] != fp:
                rejected = "state_mismatch"
                _err(errors, "state_mismatch", None, None,
                     "manifest fingerprint %r != state %r" % (fp, st["fingerprint"]))
            elif st["fingerprint"] is None:
                st["fingerprint"] = fp

    exp_world = None
    if isinstance(manifest, dict) and _is_str(manifest.get("world_id")):
        exp_world = manifest["world_id"]
    elif expected_world_id is not None:
        exp_world = expected_world_id
    elif st.get("world_id"):
        exp_world = st["world_id"]
    else:
        for ev in events:
            if isinstance(ev, dict) and _is_str(ev.get("world_id")):
                exp_world = ev["world_id"]
                break
    if rejected is None and st.get("world_id") and exp_world != st["world_id"]:
        rejected = "state_mismatch"
        _err(errors, "state_mismatch", None, None,
             "state world_id %r != %r" % (st["world_id"], exp_world))
    if rejected is None:
        st["world_id"] = exp_world

    horizon = exposed = None
    if manifest_ok:
        horizon, exposed = manifest.get("horizon_start"), manifest.get("exposed_until")

    normalized = []
    unknown = {}
    n_reserved = 0
    defines = []
    # seq 規則：恰為前一行＋1 才對；倒退或重複 ⇒ seq_order，且該行仍佔一個位置（前一值＋1）；
    # 跳號 ⇒ seq_order，並以該行的值重新對齊。⇒ 單一壞值只報一筆。
    prev_seq = st["last_seq"] if st["last_seq"] else (0 if (complete or state is not None) else None)
    last_at = st["last_at"]
    # 主檔基線（Finding 2）：無前段且本段自 seq 1 起 ⇒ 這條鏈看得到全部主檔
    if not st["last_seq"] and events and isinstance(events[0], dict) and events[0].get("seq") == 1 \
            and _is_int(events[0].get("seq")):
        st["master_complete"] = True
    enforce_master = bool(complete or st["master_complete"])

    for pos, ev in enumerate(events, start=1):
        seq = ev.get("seq") if isinstance(ev, dict) else None
        seq_i = seq if _is_int(seq) else None
        if seq_i is not None:
            if prev_seq is None or seq_i == prev_seq + 1:
                prev_seq = seq_i
            elif seq_i <= prev_seq:
                _err(errors, "seq_order", seq_i, pos, "seq %d not greater than previous %d"
                     % (seq_i, prev_seq))
                prev_seq += 1
            else:
                _err(errors, "seq_order", seq_i, pos, "seq gap: expected %d" % (prev_seq + 1))
                prev_seq = seq_i
        elif prev_seq is not None:
            prev_seq += 1                     # 讀不出 seq 的行（壞行）仍佔一個位置
        at = ev.get("at") if isinstance(ev, dict) else None
        if _is_utc_z(at):
            if last_at is not None and at < last_at:
                _err(errors, "at_order", seq_i, pos, "at %s earlier than previous %s" % (at, last_at))
            last_at = at if last_at is None or at > last_at else last_at
            day = taipei_day(at)
            st["day_counts"][day] = st["day_counts"].get(day, 0) + 1
            if horizon and at < horizon:
                _err(errors, "horizon", seq_i, pos, "at %s before horizon_start" % at)
            if exposed and at > exposed:
                _err(errors, "exposure", seq_i, pos, "at %s after exposed_until %s" % (at, exposed))
        g = _gate(ev, exp_world)
        if g:
            rejected = rejected or g[0]
            _err(errors, g[0], seq_i, pos, g[1])
            continue
        status, errs = check_event(ev, strict=strict)
        if status == "unknown_kind":
            unknown[ev["kind"]] = unknown.get(ev["kind"], 0) + 1
            continue
        if status == "reserved_kind":
            n_reserved += 1
            continue
        if status != "ok":
            for code, detail in errs:
                _err(errors, code, seq_i, pos, detail)
            continue
        nev = {k: copy.deepcopy(ev[k]) for k in COMMON_KEYS + KIND_KEYS[ev["kind"]]}
        normalized.append(nev)
        if nev["kind"] == "asset.define":
            defines.append(nev)
            _ledger_define(nev, st, errors, pos)
        elif nev["kind"] == "inventory.item":
            _ledger_item(nev, st, errors, pos)
        else:
            _ledger_event(nev, st, errors, pos, enforce_master)

    if prev_seq is not None:
        st["last_seq"] = prev_seq
    st["last_at"] = last_at

    # manifest ↔ 事件：逐日行數（累計含 state）、日集合（complete）、fleet_digest（本段含 define 時）
    if manifest_ok and rejected is None:
        listed = {d.get("day"): d for d in manifest["days"]}
        seen = st["day_counts"]
        for day in sorted(seen):
            d = listed.get(day)
            if d is None:
                _err(errors, "manifest_mismatch", None, None, "day %s has events but is not listed" % day)
            elif d.get("n_events") != seen[day]:
                # 已讀日（累計含 state）的行數必須等於 manifest；讀不出的行無法歸日 ⇒ 會連帶在此報出
                _err(errors, "manifest_mismatch", None, None,
                     "day %s: manifest n_events %r != %d lines read" % (day, d.get("n_events"), seen[day]))
        if complete:
            extra = sorted(set(listed) - set(seen))
            if extra:
                _err(errors, "manifest_mismatch", None, None, "listed days without events %s" % extra)
        if defines and fleet_digest_of(defines) != manifest["fleet_digest"]:
            _err(errors, "manifest_mismatch", None, None, "fleet_digest != digest of asset.define events")

    codes = sorted({e["code"] for e in errors})
    return {
        "ok": not errors,
        "rejected": rejected,
        "codes": codes,
        "errors": errors,
        "n_lines": len(events),
        "n_valid": len(normalized),
        "skipped_unknown_kind": sum(unknown.values()),
        "unknown_kinds": dict(sorted(unknown.items())),
        "skipped_reserved": n_reserved,
        "state": st,
        "events": normalized,
    }


def load_ledger(events, **kw):
    """消費端讀法：拒讀條件 ⇒ raise `SeedRejected`；否則回 `validate_ledger` 的 report
    （其餘錯誤只記不炸，壞行已丟）。"""
    rep = validate_ledger(events, **kw)
    if rep["rejected"]:
        detail = next((e["detail"] for e in rep["errors"] if e["code"] == rep["rejected"]), "")
        raise SeedRejected(rep["rejected"], detail)
    return rep


def _ledger_define(ev, st, errors, pos):
    aid = ev["asset_id"]
    assets = st["assets"]
    if aid in assets:
        _err(errors, "asset_redefined", ev["seq"], pos, "asset %s defined twice" % aid)
        return
    assets[aid] = {"seat_units": [s["unit"] for s in ev["seats"]],
                   "seat_ids": [s["asset_id"] for s in ev["seats"]],
                   "parent": ev["parent_asset_id"]}
    # 座 wire id（EID-x-N<n>）也是合法的事件 asset_id
    for s in ev["seats"]:
        if s["asset_id"] != s["unit"]:
            assets.setdefault(s["asset_id"], {"seat_units": [], "seat_ids": [], "parent": aid})


def _ledger_item(ev, st, errors, pos):
    """v1.1 料號主檔：同一 item_code 只定義一次（asset_id 是哨兵，不做資產查核）。"""
    code = ev["item_code"]
    items = st["items"]
    if code in items:
        _err(errors, "item_redefined", ev["seq"], pos, "item %s defined twice" % code)
        return
    items[code] = ev["display_name"]


def _ledger_event(ev, st, errors, pos, enforce_master):
    """`enforce_master`＝主檔基線完整（complete 或 state.master_complete）。為 False 時（中段切片）
    不判 unknown_asset／unknown_item——「看過任何一筆主檔」不等於「基線完整」（Finding 2）。"""
    kind, seq, aid = ev["kind"], ev["seq"], ev["asset_id"]
    assets = st["assets"]
    if enforce_master and aid not in assets:
        _err(errors, "unknown_asset", seq, pos, "asset %s not defined" % aid)
    if kind == "asset.schedule_state":
        su = ev["sub_unit"]
        if su is not None and aid in assets and su not in assets[aid]["seat_units"]:
            _err(errors, "unknown_asset", seq, pos, "sub_unit %s not a seat of %s" % (su, aid))
        return
    if not kind.startswith("wo."):
        return
    key = str(ev["work_order_no"])
    wos = st["wos"]
    wo = wos.get(key)
    if kind == "wo.open":
        if wo is not None:
            _err(errors, "wo_duplicate_open", seq, pos, "WO %s opened twice" % key)
            return
        fsa = ev["failure_started_at"]
        if fsa is not None and fsa >= ev["at"]:          # v1.1：嚴格早於（相等亦拒）
            _err(errors, "fsa_after_open", seq, pos, "failure_started_at not strictly before wo.open at")
        if ev["scope"] and ev["work_type"] != "REACTIVE":
            _err(errors, "scope_non_reactive", seq, pos,
                 "WO %s: non-empty wo.open scope on a %s work order" % (key, ev["work_type"]))
        # down_start＝停機起點（fsa，無則開單時刻）；UTC Z 同形 ⇒ 字串比較即時刻比較
        wos[key] = {"status": "OPEN", "work_type": ev["work_type"], "asset_id": aid,
                    "terminal": False, "down_start": fsa if fsa is not None else ev["at"]}
        _check_scope(ev, st, errors, pos)
        return
    if wo is None:
        _err(errors, "wo_first_not_open", seq, pos, "WO %s: first event is %s, not wo.open" % (key, kind))
        return
    if aid != wo["asset_id"]:
        _err(errors, "wo_asset_mismatch", seq, pos, "WO %s asset %s != %s" % (key, aid, wo["asset_id"]))
    if wo["terminal"]:
        _err(errors, "wo_after_terminal", seq, pos, "WO %s: %s after terminal %s" % (key, kind, wo["status"]))
        return
    if kind == "wo.scope":
        if wo["work_type"] != "REACTIVE":
            _err(errors, "scope_non_reactive", seq, pos,
                 "WO %s: wo.scope on a %s work order" % (key, wo["work_type"]))
        _check_scope(ev, st, errors, pos, owner=wo["asset_id"])
        return
    if kind == "wo.part":
        # 主檔基線完整（complete，或自 seq 1 起讀的鏈）才判；中段切片不報（同 unknown_asset 守門）
        code = ev["item_code"]
        if enforce_master and code not in st["items"]:
            _err(errors, "unknown_item", seq, pos,
                 "WO %s: item %s not defined by an earlier inventory.item" % (key, code))
        return
    if kind == "wo.field":
        if ev["field"] == "production_resumed_at":
            if wo["work_type"] != "REACTIVE":
                _err(errors, "pra_non_reactive", seq, pos,
                     "WO %s: production_resumed_at on a %s work order" % (key, wo["work_type"]))
            elif wo.get("down_start") is not None and not ev["value"] > wo["down_start"]:
                _err(errors, "pra_not_after_start", seq, pos,
                     "WO %s: production_resumed_at %s not after downtime start %s"
                     % (key, ev["value"], wo["down_start"]))
        return
    if kind != "wo.status":
        return
    frm, to, hr = ev["from_status"], ev["to_status"], ev["hold_reason"]
    if frm != wo["status"]:
        _err(errors, "wo_status_chain", seq, pos, "WO %s from_status %s != current %s" % (key, frm, wo["status"]))
    if to == "ON_HOLD":
        if hr not in HOLD_REASONS:
            _err(errors, "hold_reason_missing", seq, pos,
                 "WO %s ON_HOLD without a known hold_reason (%r)" % (key, hr))
    elif hr is not None:
        _err(errors, "hold_reason_unexpected", seq, pos, "WO %s hold_reason on %s" % (key, to))
    if ev["is_downtime"] is not is_downtime(to, hr, wo["work_type"]):
        _err(errors, "is_downtime_mismatch", seq, pos,
             "WO %s is_downtime %r on %s/%s" % (key, ev["is_downtime"], to, hr))
    wo["status"] = to
    wo["terminal"] = to in TERMINAL_STATUSES


def _check_scope(ev, st, errors, pos, owner=None):
    aid = owner or ev["asset_id"]
    a = st["assets"].get(aid)
    if a is None:
        return
    for m in ev["scope"]:
        if m not in a["seat_ids"]:
            _err(errors, "unknown_asset", ev["seq"], pos, "scope member %s not a seat of %s" % (m, aid))


def error_keys(report):
    """錯誤紀錄的比對形（fixture 用）：[{code, seq, line}]，保持報告順序。"""
    return [{"code": e["code"], "seq": e["seq"], "line": e["line"]} for e in report["errors"]]


# ===========================================================================
# fixture（`_fixtures/tendline.seed.v1.json`）
# ===========================================================================

FIXTURE_NAME = "tendline.seed.v1.json"


def apply_mutation(golden, ops):
    """golden `{manifest, events}` 的深拷貝上套用 invalid／tolerated 案的變異（純函式）。

    op 形（`seq`＝golden 中事件原本的 seq；golden 的 seq 自 1 連續 ⇒ 位置＝seq−1）：
      {"op": "set", "seq": N, "fields": {k: v, ...}}      改一行的欄位值
      {"op": "swap", "seqs": [a, b]}                      交換兩行位置、**seq 欄位留在原位**
      {"op": "manifest_set", "fields": {k: v}}            改 manifest 頂層欄
      {"op": "manifest_day_set", "day": D, "fields": {…}} 改 manifest.days 中某日的欄
    結構性 op（v1.1 審查 Finding 3；以行的**原 seq 欄值**定址；必須排在所有 set／swap 之後）：
      {"op": "move", "seq": A, "before_seq": B}           把 A 行移到 B 行之前（B＝null ⇒ 移到帳尾）
      {"op": "delete", "seqs": [a, b]}                    刪除原 seq 落在 [a, b] 的行
      {"op": "split_day", "seq": N, "days": k}            原 seq ≥ N 的行整體後移 k 天（`at`、
                                                          `failure_started_at`、`production_resumed_at` 值
                                                          一起移），manifest `exposed_until` 同移 k 天
    有結構性 op 時，套完後 seq 依位置重編為 1..n，manifest `days` 依事件重算（台北日、行數、sha256）。"""
    rec = copy.deepcopy({"manifest": golden["manifest"], "events": golden["events"]})
    evs = rec["events"]
    structural = False

    def pos_of(seq):
        for i, e in enumerate(evs):
            if e.get("seq") == seq:
                return i
        raise ValueError("no line with seq %r" % (seq,))

    for op in ops:
        kind = op["op"]
        if structural and kind in ("set", "swap"):
            raise ValueError("%s after a structural op (positions moved)" % kind)
        if kind == "move":
            structural = True
            ev = evs.pop(pos_of(op["seq"]))
            evs.insert(len(evs) if op.get("before_seq") is None else pos_of(op["before_seq"]), ev)
        elif kind == "delete":
            structural = True
            a, b = op["seqs"]
            evs[:] = [e for e in evs if not (a <= e.get("seq", 0) <= b)]
        elif kind == "split_day":
            structural = True
            delta = _dt.timedelta(days=op.get("days", 1))

            def sh(z):
                return _clock.to_utc_z(_clock.from_utc_z(z) + delta)
            for e in evs:
                if e.get("seq", 0) < op["seq"]:
                    continue
                e["at"] = sh(e["at"])
                if e.get("kind") == "wo.open" and e.get("failure_started_at"):
                    e["failure_started_at"] = sh(e["failure_started_at"])
                if e.get("kind") == "wo.field" and e.get("field") == "production_resumed_at":
                    e["value"] = sh(e["value"])
            rec["manifest"]["exposed_until"] = sh(rec["manifest"]["exposed_until"])
        elif kind == "set":
            evs[op["seq"] - 1].update(copy.deepcopy(op["fields"]))
        elif kind == "swap":
            a, b = op["seqs"][0] - 1, op["seqs"][1] - 1
            sa, sb = evs[a]["seq"], evs[b]["seq"]
            evs[a], evs[b] = evs[b], evs[a]
            evs[a]["seq"], evs[b]["seq"] = sa, sb
        elif kind == "manifest_set":
            rec["manifest"].update(copy.deepcopy(op["fields"]))
        elif kind == "manifest_day_set":
            for d in rec["manifest"]["days"]:
                if d["day"] == op["day"]:
                    d.update(copy.deepcopy(op["fields"]))
        else:
            raise ValueError("unknown mutation op %r" % (kind,))
    if structural:
        by_day = {}
        for i, e in enumerate(evs, start=1):
            e["seq"] = i
            by_day.setdefault(taipei_day(e["at"]), []).append(e)
        rec["manifest"]["days"] = [{"day": d, "file": day_file_name(d), "n_events": len(by_day[d]),
                                    "sha256": day_sha256(by_day[d])} for d in sorted(by_day)]
    return rec


def check_vector(vec):
    """一組測試向量（SPEC §5.7）：由 inputs 重算每個值，逐一等於 expect。回 [不符的鍵]。"""
    inp, exp = vec["inputs"], vec["expect"]
    got = {}
    got["canonical_lines"] = [canonical_line(e).decode("utf-8") for e in inp["day_events"]]
    got["day_sha256"] = day_sha256(inp["day_events"])
    defines = [e for e in inp["day_events"] if e.get("kind") == "asset.define"]
    got["fleet_digest"] = fleet_digest_of(defines)
    got["world_id"] = world_id_of(inp["seed"], inp["generator_version"], got["fleet_digest"],
                                  inp.get("flags"))
    got["world_id_input_utf8_hex"] = world_id_input(inp["seed"], inp["generator_version"],
                                                    got["fleet_digest"], inp.get("flags")).hex()
    return [k for k in exp if got.get(k) != exp[k]], got


def world_id_input(seed, generator_version, fleet_digest, flags=None) -> bytes:
    """`world_id` 的 BLAKE2b 輸入位元組（測試向量用；與 `world_id_of` 同一條路）。"""
    msg = "%s\x1f%s\x1f%s" % (seed, generator_version, fleet_digest)
    if not flags_are_default(flags):
        msg += "\x1fflags=" + canonical_json(dict(sorted(dict(flags).items())))
    return msg.encode("utf-8")


def check_fixture(fx, raw=None):
    """fixture 承重驗證（本檔 `--selftest` 與 `_fixtures/selftest_fixtures.py` 共用這一份；
    不在兩處各寫）。`fx`＝已解析的 fixture；`raw`＝檔案位元組（有則驗 LF）。
    回 `(n_golden, n_invalid, n_tolerated, n_slices, n_vectors)`；不符即 AssertionError。
    `slices`（v1.1 審查 Finding 3）：golden＋變異後**分段讀**（fresh／carry state、有無 manifest、
    complete 與否），逐段比對完整錯誤紀錄；另以一次讀完比對 `whole_expect_errors`。"""
    if raw is not None:
        assert b"\r" not in raw, "tendline.seed fixture contains CR (fixtures are LF-only)"
    for k in ("schema_id", "_description", "provenance", "golden", "invalid", "tolerated",
              "slices", "test_vectors"):
        assert k in fx, "tendline.seed fixture missing top-level key %s" % k
    assert fx["schema_id"] == SCHEMA, "fixture schema_id is not the contract literal"
    for p in fx["provenance"]:
        assert p.get("field") and p.get("source"), "provenance entry lacks field/source"
    assert fx["golden"], "fixture has no golden"
    assert len(fx["invalid"]) >= 6, "fixture needs >= 6 invalid cases"
    assert len(fx["test_vectors"]) >= 2, "fixture needs >= 2 test vectors"

    n_golden = 0
    for gi, g in enumerate(fx["golden"]):
        m, evs = g["manifest"], g["events"]
        rep = validate_ledger(evs, manifest=m, complete=True, strict=True)
        assert rep["ok"], "golden[%d] rejected: %s" % (gi, rep["errors"][:5])
        assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1)), \
            "golden[%d] seq must run 1..n (mutations address events by seq)" % gi
        kinds = {e["kind"] for e in evs}
        assert kinds == set(KINDS_V1), \
            "golden[%d] must cover every v1 kind; missing %s" % (gi, sorted(set(KINDS_V1) - kinds))
        assert len(m["days"]) == 1, "golden[%d] must be exactly one day" % gi
        files = {day_file_name(m["days"][0]["day"]): day_bytes(evs)}
        assert not check_day_files(m, files), "golden[%d] manifest sha256/n_events drifted" % gi
        wi = g["world_inputs"]
        defines = [e for e in evs if e["kind"] == "asset.define"]
        assert m["world_id"] == world_id_of(wi["seed"], m["generator_version"],
                                            fleet_digest_of(defines), m.get("flags")), \
            "golden[%d] world_id is not computed from its world_inputs" % gi
        assert wi["generator_version"] == m["generator_version"]
        _assert_placeholder_only(evs, gi)
        n_golden += 1

    base = fx["golden"][0]
    n_invalid = 0
    for iv in fx["invalid"]:
        tag = iv.get("case", "")[:48]
        assert iv.get("case") and iv.get("mutation") and isinstance(iv.get("expect_errors"), list) \
            and iv["expect_errors"], "invalid[%s] lacks case/mutation/expect_errors" % tag
        assert all(e["code"] in ERROR_CODES for e in iv["expect_errors"])
        if len(iv["expect_errors"]) > 1:
            assert "cascade" in iv["case"], \
                "invalid[%s]: multi-error case must say 'cascade' in its name" % tag
        rec = apply_mutation(base, iv["mutation"])
        rep = validate_ledger(rec["events"], manifest=rec["manifest"], complete=True, strict=True)
        assert error_keys(rep) == iv["expect_errors"], \
            "invalid[%s]: expected %s, got %s" % (tag, iv["expect_errors"], error_keys(rep))
        codes = {e["code"] for e in iv["expect_errors"]}
        rej = sorted(codes & set(REJECT_CODES))
        if rej:
            try:
                load_ledger(rec["events"], manifest=rec["manifest"], complete=True)
            except SeedRejected as exc:
                assert exc.code == rej[0], "invalid[%s]: rejected with %s" % (tag, exc.code)
            else:
                raise AssertionError("invalid[%s]: load_ledger did not refuse to read" % tag)
        n_invalid += 1

    n_tol = 0
    for tv in fx["tolerated"]:
        tag = tv.get("case", "")[:48]
        rec = apply_mutation(base, tv["mutation"])
        rep = load_ledger(rec["events"], manifest=rec["manifest"], complete=True)
        for k, v in tv["expect"].items():
            assert rep[k] == v, "tolerated[%s]: %s=%r, expected %r" % (tag, k, rep[k], v)
        if "expect_strict_errors" in tv:
            srep = validate_ledger(rec["events"], manifest=rec["manifest"], complete=True, strict=True)
            assert error_keys(srep) == tv["expect_strict_errors"], \
                "tolerated[%s]: strict expected %s, got %s" % (tag, tv["expect_strict_errors"],
                                                                error_keys(srep))
        n_tol += 1

    n_sl = 0
    for sc in fx["slices"]:
        tag = sc.get("case", "")[:48]
        assert sc.get("case") and isinstance(sc.get("valid"), bool) and sc.get("reads"), \
            "slices[%s] lacks case/valid/reads" % tag
        rec = apply_mutation(base, sc.get("mutation", []))
        evs = rec["events"]
        whole = validate_ledger(evs, manifest=rec["manifest"], complete=True, strict=True)
        assert error_keys(whole) == sc["whole_expect_errors"], \
            "slices[%s]: whole read expected %s, got %s" % (tag, sc["whole_expect_errors"], error_keys(whole))
        st = None
        chain = []
        for ri, rd in enumerate(sc["reads"]):
            assert rd["state"] in ("fresh", "carry"), "slices[%s] read %d: state" % (tag, ri)
            a, b = rd["seqs"]
            chunk = [e for e in evs if a <= e["seq"] <= b]
            rep = validate_ledger(chunk, manifest=rec["manifest"] if rd["manifest"] else None,
                                  state=json.loads(json.dumps(st)) if rd["state"] == "carry" else None,
                                  complete=rd["complete"], strict=True)
            assert error_keys(rep) == rd["expect_errors"], \
                "slices[%s] read %d: expected %s, got %s" % (tag, ri, rd["expect_errors"], error_keys(rep))
            assert not rep["rejected"], "slices[%s] read %d rejected %s" % (tag, ri, rep["rejected"])
            st = rep["state"]
            chain.extend((e["code"], e["seq"]) for e in rep["errors"])
        if sc.get("chain_covers_ledger"):
            # 自 seq 1 起逐段帶 state 讀完整份帳 ≡ 一次讀完（錯誤的 code／seq 一致；line 是段內位置故不比）
            assert chain == [(e["code"], e["seq"]) for e in whole["errors"]], \
                "slices[%s]: chained read %s != whole read" % (tag, chain)
        if sc["valid"]:
            assert not chain, "slices[%s] is marked valid but a read reported errors" % tag
        else:
            assert chain, "slices[%s] is marked invalid but no read reported an error" % tag
        n_sl += 1

    n_vec = 0
    for vec in fx["test_vectors"]:
        bad, got = check_vector(vec)
        assert not bad, "test_vector[%s]: %s differ (got %s)" % (vec.get("name"), bad,
                                                                {k: got[k] for k in bad})
        n_vec += 1
    return n_golden, n_invalid, n_tol, n_sl, n_vec


_REAL_LOOKING_EID = re.compile(r"EID-(\d{5})")


def _assert_placeholder_only(evs, gi):
    """fixture 的 EID 一律佔位 00000–00099；角色字串。"""
    blob = json.dumps(evs, ensure_ascii=False)
    for num in _REAL_LOOKING_EID.findall(blob):
        assert int(num) <= 99, "golden[%d] carries a non-placeholder EID %s" % (gi, num)
    for e in evs:
        assert e["actor"].startswith("role:"), "golden[%d] actor is not a role string" % gi


# ===========================================================================
# selftest（本段是「呼叫端」：讀 fixture 檔、跑產生器；上面的函式本身零 I/O）
# ===========================================================================

def _selftest():
    import builtins
    import datetime as dt
    import os
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    results = []

    def t(name, cond, detail=""):
        results.append(bool(cond))
        print("  %s  %s%s" % ("PASS" if cond else "FAIL", name,
                             ("  — " + str(detail)[:300]) if detail and not cond else ""))

    import wsynth_params as P
    import wsynth_world as W
    import wsynth_emit as E

    print("[1] contract literals == declarer constants")
    t("SCHEMA / MANIFEST_SCHEMA == wsynth_params",
      SCHEMA == P.SEED_SCHEMA and MANIFEST_SCHEMA == P.SEED_MANIFEST_SCHEMA)
    t("ROLES == wsynth_params.ROLES", ROLES == P.ROLES)
    t("KINDS_V1 == KINDS_EMITTED_PHASE1; KINDS_V1+RESERVED == wsynth_params.KINDS",
      KINDS_V1 == P.KINDS_EMITTED_PHASE1 and KINDS == P.KINDS)
    t("WO_FIELDS / ASSET_ROLES == wsynth_params", WO_FIELDS == P.WO_FIELDS and ASSET_ROLES == P.ASSET_ROLES)
    t("DEFINE_KEYS == wsynth_world.DEFINE_KEYS", DEFINE_KEYS == W.DEFINE_KEYS)
    t("DISEASE_FLAGS / DEFAULT_FLAGS == wsynth_params",
      DISEASE_FLAGS == P.DISEASES and DEFAULT_FLAGS == P.DEFAULT_FLAGS)
    t("WO number range == wsynth_params (710001..799999)",
      (WO_NO_FIRST, WO_NO_LAST) == (P.WO_NO_FIRST, P.WO_NO_LAST))
    t("item code range == wsynth_params (TL900001..TL999999)",
      (ITEM_CODE_FIRST, ITEM_CODE_LAST) == (P.ITEM_CODE_FIRST, P.ITEM_CODE_LAST)
      and P.ITEM_CODE_PREFIX == "TL" and P.ITEM_CODE_WIDTH == 6)
    t("v1.1 item-master sentinel == wsynth_params.ITEM_ASSET_ID (EID-00000; matches the EID shape)",
      ITEM_ASSET_ID == P.ITEM_ASSET_ID and _MACHINE_ID_RE.match(ITEM_ASSET_ID) is not None)
    t("v1.1 closed set: inventory.item in, reserved = pm.schedule only",
      "inventory.item" in KINDS_V1 and RESERVED_KINDS == ("pm.schedule",)
      and set(KIND_KEYS) == set(KINDS_V1))
    t("HOLD_DOWN_REASONS + UPTIME == HOLD_REASONS (as sets)",
      set(P.HOLD_DOWN_REASONS) | set(UPTIME_HOLD_REASONS) == set(HOLD_REASONS))
    t("note entry types used by templates == NOTE_ENTRY_TYPES", set(P.NOTE_TEMPLATES) == set(NOTE_ENTRY_TYPES))
    try:
        import board_w8 as w8
        import board_eqmeeting as wem
        t("WO_STATUSES / TERMINAL == board_w8", WO_STATUSES == w8.STATUSES
          and TERMINAL_STATUSES == w8.TERMINAL_STATUSES)
        t("HOLD_REASONS == board_w8.HOLD_REASONS_KNOWN", HOLD_REASONS == w8.HOLD_REASONS_KNOWN)
        t("NOTE_ENTRY_TYPES ⊆ board_w8.NOTE_ENTRY_TYPES_KNOWN",
          set(NOTE_ENTRY_TYPES) <= set(w8.NOTE_ENTRY_TYPES_KNOWN))
        t("UPTIME_HOLD_REASONS == board_eqmeeting.UPTIME_HOLD_REASONS",
          UPTIME_HOLD_REASONS == wem.UPTIME_HOLD_REASONS)
        combos = [(s, h, wt) for s in WO_STATUSES for h in (None,) + HOLD_REASONS for wt in WORK_TYPES]
        t("is_downtime ≡ _phase_kind=='count' (non-terminal, non-COMPLETED) ≡ wsynth_world.is_downtime",
          all(is_downtime(s, h, wt) == W.is_downtime(s, h, wt) for s, h, wt in combos)
          and all(is_downtime(s, h, wt) == (wem._phase_kind(s, h, wt) == "count")
                  for s, h, wt in combos if s not in ("COMPLETED",) + TERMINAL_STATUSES))
    except ImportError as exc:     # pragma: no cover
        t("board_w8 / board_eqmeeting importable", False, exc)

    print("[2] fixture golden / invalid / tolerated / test vectors")
    path = os.path.join(here, "_fixtures", FIXTURE_NAME)
    with open(path, "rb") as fh:
        raw = fh.read()
    fx = json.loads(raw.decode("utf-8"))
    try:
        ng, ni, nt, ns, nv = check_fixture(fx, raw)
        t("check_fixture: %d golden ok, %d invalid each matched on full error records, %d tolerated, "
          "%d sliced/multi-day reads, %d test vectors" % (ng, ni, nt, ns, nv), True)
    except AssertionError as exc:
        t("check_fixture", False, exc)
    for iv in fx["invalid"]:
        rec = apply_mutation(fx["golden"][0], iv["mutation"])
        rep = validate_ledger(rec["events"], manifest=rec["manifest"], complete=True, strict=True)
        t("invalid → %-40s %s" % (",".join("%s@%s" % (e["code"], e["seq"]) for e in iv["expect_errors"]),
                                  iv["case"][:60]),
          error_keys(rep) == iv["expect_errors"], error_keys(rep))
    required = {"seq_order", "wo_after_terminal", "hold_reason_missing", "world_mismatch",
                "exposure", "wo_first_not_open"}
    t("the six mandated invalid classes are all present",
      required <= {e["code"] for iv in fx["invalid"] for e in iv["expect_errors"]})
    for vec in fx["test_vectors"]:
        bad, _got = check_vector(vec)
        t("test vector %s: every hash reproduced" % vec["name"], not bad, bad)
        pin_fd = W.fleet_digest([dict(e) for e in vec["inputs"]["day_events"] if e["kind"] == "asset.define"])
        pin_wid = W.world_id_of(vec["inputs"]["seed"], vec["inputs"]["generator_version"], pin_fd,
                                vec["inputs"].get("flags"))
        t("test vector %s: wsynth_world.fleet_digest / world_id_of agree" % vec["name"],
          pin_fd == vec["expect"]["fleet_digest"] and pin_wid == vec["expect"]["world_id"])

    print("[3] normalize / tolerance / reject (F1 F2 F3 F8)")
    g = fx["golden"][0]
    ev0 = copy.deepcopy(next(e for e in g["events"] if e["kind"] == "wo.note"))
    ev0["extra_future_field"] = 1
    n, why = normalize_event(ev0)
    t("normalize drops unknown extra keys (whitelist), no error", why is None and "extra_future_field" not in n)
    n, why = normalize_event(dict(ev0, kind="wo.attachment"))
    t("unknown kind → (None, 'unknown_kind'), no exception", n is None and why == "unknown_kind")
    n, why = normalize_event(dict(ev0, kind="wo.attachment"), strict=True)
    t("strict: unknown kind → closed_set", n is None and why == "closed_set")
    n, why = normalize_event(dict(ev0, kind="pm.schedule"))
    t("F1 reserved kind (consumer) → (None, 'reserved_kind'), skipped", n is None and why == "reserved_kind")
    n, why = normalize_event(dict(ev0, kind="pm.schedule"), strict=True)
    t("F1 reserved kind (strict) → error reserved_kind", n is None and why == "reserved_kind")
    n, why = normalize_event(dict(ev0, kind="wo.attachment", actor="role:manager"))
    t("F2 unknown kind with bad actor → closed_set (common fields first)", n is None and why == "closed_set")
    n, why = normalize_event(dict(ev0, kind="pm.schedule", at="2026-07-03 01:00:00"))
    t("F2 reserved kind with bad at → envelope (common fields first)", n is None and why == "envelope")
    it0 = copy.deepcopy(next(e for e in g["events"] if e["kind"] == "inventory.item"))
    n, why = normalize_event(it0, strict=True)
    t("v1.1 inventory.item normalizes (strict) with the sentinel asset_id", why is None
      and set(n) == set(COMMON_KEYS) | {"item_code", "display_name"} and n["asset_id"] == ITEM_ASSET_ID)
    t("v1.1 inventory.item with a machine asset_id → field",
      normalize_event(dict(it0, asset_id="EID-00001")) == (None, "field"))
    t("v1.1 inventory.item with empty display_name → field / out-of-range code → range",
      normalize_event(dict(it0, display_name=" ")) == (None, "field")
      and normalize_event(dict(it0, item_code="TL" + "100001")) == (None, "range"))  # 拼接＝合成區段外的負向測試值，不寫成料號形字面（發佈線形狀掃描）
    d_any = copy.deepcopy(next(e for e in g["events"] if e["kind"] == "asset.define"))
    t("v1.1 asset.define may not define the item-master sentinel → field",
      normalize_event(dict(d_any, asset_id=ITEM_ASSET_ID, seats=[])) == (None, "field"))
    try:
        normalize_event(dict(it0, schema="tendline.seed.v1"))
        t("v1.1 reader refuses a v1 line (schema_mismatch)", False)
    except SeedRejected as exc:
        t("v1.1 reader refuses a v1 line (schema_mismatch)", exc.code == "schema_mismatch")
    try:
        check_manifest(dict(g["manifest"], schema="tendline.seed.manifest.v1"))
        t("v1.1 reader refuses a v1 manifest (schema_mismatch)", False)
    except SeedRejected as exc:
        t("v1.1 reader refuses a v1 manifest (schema_mismatch)", exc.code == "schema_mismatch")
    # 無 state 的中段切片（看不到主檔）不報 unknown_item（同 unknown_asset 守門）
    mid = [e for e in g["events"] if e["kind"] not in ("asset.define", "inventory.item")]
    t("v1.1 mid-ledger slice without master context: no unknown_item / unknown_asset",
      not {"unknown_item", "unknown_asset"} & set(validate_ledger(mid)["codes"]))
    d0 = copy.deepcopy(next(e for e in g["events"] if e["kind"] == "asset.define" and e["seats"]
                            and e["seats"][0]["unit"].startswith("nest:")))
    t("F3 golden nest seats accepted", normalize_event(d0)[1] is None)
    for label, seat in (("nest seat with another machine's id", {"unit": "nest:1", "asset_id": "EID-00002-N1"}),
                        ("nest seat with mismatched seat number", {"unit": "nest:1", "asset_id": d0["asset_id"] + "-N2"}),
                        ("child-EID seat whose asset_id differs", {"unit": "EID-00003", "asset_id": "EID-00004"}),
                        ("child-EID seat pointing at a -N id", {"unit": "EID-00003", "asset_id": "EID-00003-N1"}),
                        ("unit neither nest:<n> nor EID", {"unit": "slot-1", "asset_id": "EID-00003"})):
        bad = dict(d0, seats=[seat])
        t("F3 rejects %s" % label, normalize_event(bad) == (None, "field"))
    for bad, code in ((dict(ev0, schema="tendline.seed.v2"), "schema_mismatch"),
                      (dict(ev0, world_id="ffffffffffffffff"), "world_mismatch")):
        try:
            normalize_event(bad, expected_world_id=g["manifest"]["world_id"])
            t("normalize refuses %s" % code, False)
        except SeedRejected as exc:
            t("normalize refuses %s" % code, exc.code == code)
    try:
        check_manifest(dict(g["manifest"], schema="tendline.seed.manifest.v0"))
        t("manifest schema mismatch refused", False)
    except SeedRejected as exc:
        t("manifest schema mismatch refused", exc.code == "schema_mismatch")
    gb = day_bytes(g["events"])
    lines = gb.split(b"\n")
    lines[21] = b"\xff\xfe" + lines[21][2:]          # line 22 = the wo.part line (not a define/item)
    broken = b"\n".join(lines)
    try:
        parsed = split_day_bytes(broken)
        rep = validate_ledger(parsed, manifest=g["manifest"])
        t("F8 non-UTF-8 line → encoding error at that line (cascade: manifest day count), "
          "no exception, rest validated",
          error_keys(rep) == [{"code": "encoding", "seq": None, "line": 22},
                              {"code": "manifest_mismatch", "seq": None, "line": None}]
          and rep["n_valid"] == len(g["events"]) - 1, error_keys(rep))
    except Exception as exc:        # noqa: BLE001
        t("F8 non-UTF-8 line handled without exception", False, repr(exc))
    try:
        man, merrs = parse_manifest_bytes(b"\xff{}")
        t("F8 non-UTF-8 manifest → (None, encoding)", man is None and merrs[0][0] == "encoding")
        t("F8 non-bytes input → encoding, not an exception",
          isinstance(split_day_bytes("text")[0], Unreadable)
          and parse_manifest_bytes(None)[1][0][0] == "encoding")
    except Exception as exc:        # noqa: BLE001
        t("F8 manifest bytes handled without exception", False, repr(exc))
    rep = validate_ledger(g["events"] + [Unreadable("envelope", "x")], manifest=g["manifest"])
    t("unparseable line counted as envelope, rest still validated",
      rep["codes"] == ["envelope"] and rep["n_valid"] == len(g["events"]))
    ext = copy.deepcopy(g["events"])
    ext[[i for i, e in enumerate(ext) if e["kind"] == "wo.note"][0]]["extra_future_field"] = "x"
    t("extra key: tolerated by default, error only in strict mode",
      validate_ledger(ext, manifest=g["manifest"], complete=True)["ok"]
      and validate_ledger(ext, manifest=g["manifest"], complete=True, strict=True)["codes"] == ["envelope"])

    print("[4] day-file set (F5) and test worlds (F6)")
    m = g["manifest"]
    name = m["days"][0]["file"]
    t("F5 complete set passes", not check_day_files(m, {name: gb}))
    t("F5 missing listed file → manifest_mismatch", check_day_files(m, {}) ==
      [("manifest_mismatch", "listed file %s is missing" % name)])
    t("F5 extra unlisted file → manifest_mismatch",
      [c for c, _ in check_day_files(m, {name: gb, "seed-2026-07-04.jsonl": b""})] == ["manifest_mismatch"])
    t("F5 partial=True allows a subset (explicit)", not check_day_files(m, {}, partial=True))
    t("F5 partial=True still rejects an unlisted file",
      bool(check_day_files(m, {"seed-2026-07-04.jsonl": b""}, partial=True)))
    flags_off = dict(DEFAULT_FLAGS, long_stop=False)
    tm = dict(m, flags=flags_off, test_only=True)
    try:
        load_ledger(g["events"], manifest=tm, complete=True)
        t("F6 test-only world refused by default", False)
    except SeedRejected as exc:
        t("F6 test-only world refused by default", exc.code == "test_world")
    t("F6 test-only world readable with allow_test_world=True",
      validate_ledger(g["events"], manifest=tm, complete=True, allow_test_world=True)["ok"])
    t("F6 non-default flags without test_only → manifest error",
      "manifest" in validate_ledger(g["events"], manifest=dict(m, flags=flags_off), complete=True,
                                    allow_test_world=True)["codes"])
    t("F6 world_id ignores default flags, includes non-default flags",
      world_id_of("s", "v", "0" * 64) == world_id_of("s", "v", "0" * 64, DEFAULT_FLAGS)
      != world_id_of("s", "v", "0" * 64, flags_off))
    until0 = dt.datetime.combine(P.HORIZON_START + dt.timedelta(days=2), dt.time(0, 0))
    wt = W.build_world(until0, flags={"long_stop": False}, with_segments=False)
    mt = json.loads(E.render(wt)["seed/seed-manifest.json"].decode("utf-8"))
    wd = W.build_world(until0, with_segments=False)
    md = json.loads(E.render(wd)["seed/seed-manifest.json"].decode("utf-8"))
    t("F6 emitter marks a non-default-flag world test_only=true", mt.get("test_only") is True)
    t("F6 emitter leaves the default world without test_only", "test_only" not in md)
    t("F6 emitter world_id == world_id_of(seed, version, fleet_digest, flags)",
      mt["world_id"] == world_id_of(P.DEFAULT_SEED, P.GENERATOR_VERSION, mt["fleet_digest"], mt["flags"])
      and md["world_id"] == world_id_of(P.DEFAULT_SEED, P.GENERATOR_VERSION, md["fleet_digest"]))

    print("[5] zero I/O")
    real_open = builtins.open
    opened = []

    def spy(*a, **k):
        opened.append(a[0] if a else k)
        raise AssertionError("wsynth_seed opened a file")
    builtins.open = spy
    try:
        validate_ledger(g["events"], manifest=g["manifest"], complete=True, strict=True)
        check_fixture(fx)
        split_day_bytes(gb)
        for e in g["events"]:
            normalize_event(e)
    finally:
        builtins.open = real_open
    t("validate / normalize / check_fixture / split open no file", not opened, opened[:2])

    print("[6] cross-pin: WP-1 generator, default seed, 14 days → validator, zero errors")
    until = dt.datetime.combine(P.HORIZON_START + dt.timedelta(days=14), dt.time(0, 0))
    w = W.build_world(until)
    files = E.render(w)
    man = json.loads(files["seed/seed-manifest.json"].decode("utf-8"))
    day_files = {k.split("/", 1)[1]: v for k, v in files.items()
                 if k.startswith("seed/seed-") and k.endswith(".jsonl")}
    all_events = []
    for nm in sorted(day_files):
        all_events.extend(split_day_bytes(day_files[nm]))
    rep = validate_ledger(all_events, manifest=man, complete=True, strict=True)
    t("whole 14-day ledger: %d events, zero errors (strict, complete)" % len(all_events),
      rep["ok"] and rep["n_valid"] == len(all_events) and len(man["days"]) == 14,
      rep["errors"][:5])
    t("emitted day files match manifest (complete set, sha256, line counts)",
      not check_day_files(man, day_files), check_day_files(man, day_files)[:3])
    t("canonical_line == emitter bytes (every day file)",
      all(day_bytes(split_day_bytes(v)) == v for v in day_files.values()))
    defines = [e for e in all_events if e["kind"] == "asset.define"]
    t("fleet_digest_of(asset.define events) == generator fleet_digest",
      fleet_digest_of(defines) == w["fleet_digest"] == man["fleet_digest"])
    t("world_id_of(default seed, version, fleet_digest) == manifest world_id",
      world_id_of(P.DEFAULT_SEED, P.GENERATOR_VERSION, man["fleet_digest"], man.get("flags"))
      == man["world_id"])
    st = None
    inc_errors = []
    for nm in sorted(day_files):
        r = validate_ledger(split_day_bytes(day_files[nm]), manifest=man, state=st, strict=True)
        inc_errors.extend(r["errors"])
        st = json.loads(json.dumps(r["state"]))
    t("day-by-day incremental read with carried state: zero errors", not inc_errors, inc_errors[:3])
    seen_kinds = {e["kind"] for e in all_events}
    if "asset.schedule_state" not in seen_kinds:
        # 長停（asset.schedule_state）是 per-asset-per-day 的稀有擲骰、亂數鍵含 EID 字串；EID 不同
        # （例：脫敏公開樹的假編號）時 14 天內可能一次都抽不到——那是種子巧合、不是產生器缺陷。
        # 只在自測裡把該擲骰機率強制為 1（in-process、finally 還原）重建一次，確認「長停路徑能發出此 kind」；
        # 不改產生器、不改預設種子的預設行為。預設窗已抽到的樹（私有樹）不進此分支、斷言數不變。
        _p0 = P.P_DORMANT_START_PER_DAY
        P.P_DORMANT_START_PER_DAY = 1.0
        try:
            _wf = W.build_world(until, with_segments=False)
            _ff = E.render(_wf)
        finally:
            P.P_DORMANT_START_PER_DAY = _p0
        for _k, _v in _ff.items():
            if _k.startswith("seed/seed-") and _k.endswith(".jsonl"):
                seen_kinds |= {e["kind"] for e in split_day_bytes(_v)}
        print("  note: default seed drew no asset.schedule_state in 14 days; forced-dormancy probe used")
    t("every v1.1 kind except wo.scope appears in 14 days (wo.scope is rare; see golden)",
      seen_kinds >= set(KINDS_V1) - {"wo.scope"},
      sorted(set(KINDS_V1) - seen_kinds))
    n_def = len(defines)
    items_ev = [e for e in all_events if e["kind"] == "inventory.item"]
    t("v1.1 generator: the whole item pool (%d) right after the %d defines, at horizon start, "
      "code-ascending, sentinel asset_id, role:system" % (P.ITEM_CODE_POOL, n_def),
      [e["seq"] for e in items_ev] == list(range(n_def + 1, n_def + 1 + P.ITEM_CODE_POOL))
      and [(e["item_code"], e["display_name"]) for e in items_ev] == W.item_master()
      and all(e["at"] == man["horizon_start"] and e["asset_id"] == ITEM_ASSET_ID
              and e["actor"] == "role:system" for e in items_ev))
    parts = [e for e in all_events if e["kind"] == "wo.part"]
    t("v1.1 generator: every wo.part item_code is defined by an earlier inventory.item (%d parts)" % len(parts),
      parts and all(e["item_code"] in {i["item_code"] for i in items_ev if i["seq"] < e["seq"]}
                    for e in parts))
    opens = {e["work_order_no"]: e for e in all_events if e["kind"] == "wo.open"}
    fsa_eq = [e["seq"] for e in opens.values() if e["failure_started_at"] == e["at"]]
    scope_pm = [e["seq"] for e in all_events if (e["kind"] == "wo.open" and e["scope"]
                                                  or e["kind"] == "wo.scope")
                and opens[e["work_order_no"]]["work_type"] != "REACTIVE"]
    pra_bad = [e["seq"] for e in all_events if e["kind"] == "wo.field" and e["field"] == "production_resumed_at"
               and (opens[e["work_order_no"]]["work_type"] != "REACTIVE"
                    or not e["value"] > (opens[e["work_order_no"]]["failure_started_at"]
                                         or opens[e["work_order_no"]]["at"]))]
    t("v1.1 generator guarantees hold on 14 days (fsa never == open; scope REACTIVE-only; "
      "pra REACTIVE-only and after downtime start)", not fsa_eq and not scope_pm and not pra_bad,
      (fsa_eq[:3], scope_pm[:3], pra_bad[:3]))

    print("[7] F7 resume: state → JSON → restore → next manifest ≡ one-shot read")
    t1 = dt.datetime.combine(P.HORIZON_START + dt.timedelta(days=6), dt.time(13, 17, 0))
    t2 = dt.datetime.combine(P.HORIZON_START + dt.timedelta(days=11), dt.time(9, 41, 0))

    def ledger_at(tt):
        ff = E.render(W.build_world(tt, with_segments=False))
        mm = json.loads(ff["seed/seed-manifest.json"].decode("utf-8"))
        evs = []
        for k in sorted(ff):
            if k.startswith("seed/seed-") and k.endswith(".jsonl"):
                evs.extend(split_day_bytes(ff[k]))
        return mm, evs
    m1, e1 = ledger_at(t1)
    m2, e2 = ledger_at(t2)
    t("prefix: ledger at T1 is a prefix of ledger at T2", e2[:len(e1)] == e1 and len(e2) > len(e1))
    r1 = validate_ledger(e1, manifest=m1, strict=True)
    saved = json.dumps(r1["state"], sort_keys=True)
    restored = json.loads(saved)
    r2 = validate_ledger(e2[len(e1):], manifest=m2, state=restored, complete=True, strict=True)
    full = validate_ledger(e2, manifest=m2, complete=True, strict=True)
    t("resumed read ok and equals one-shot read (events, errors, final state)",
      r1["ok"] and r2["ok"] and full["ok"]
      and r1["events"] + r2["events"] == full["events"]
      and r2["state"] == full["state"], (r1["errors"][:2], r2["errors"][:2]))
    other = dict(m2, generator_version="tendline-synth-gX")
    try:
        load_ledger(e2[len(e1):], manifest=other, state=json.loads(saved), complete=True)
        t("F7 fingerprint mismatch on resume → state_mismatch", False)
    except SeedRejected as exc:
        t("F7 fingerprint mismatch on resume → state_mismatch", exc.code == "state_mismatch")
    for key, val in (("fleet_digest", "f" * 64), ("horizon_start", "2026-07-01T16:00:00Z")):
        rr = validate_ledger(e2[len(e1):], manifest=dict(m2, **{key: val}), state=json.loads(saved))
        t("F7 %s change on resume → state_mismatch" % key, rr["rejected"] == "state_mismatch")
    t("F7 resume fingerprint includes the manifest schema (cache/resume key ⊇ (world_id, schema))",
      "schema" in STATE_FINGERPRINT_KEYS and r1["state"]["fingerprint"]["schema"] == MANIFEST_SCHEMA)
    v1_state = json.loads(saved)
    v1_state["fingerprint"].pop("schema", None)          # 形同 v1 讀者存下的 state（指紋無 schema）
    t("F7 state saved by a v1 reader (fingerprint without schema) → state_mismatch on a v1.1 resume",
      validate_ledger(e2[len(e1):], manifest=m2, state=v1_state)["rejected"] == "state_mismatch")

    print("[8] master baseline (v1.1 review Finding 2)")
    t("a read from seq 1 sets state.master_complete; it survives the JSON round trip",
      r1["state"]["master_complete"] is True and json.loads(saved)["master_complete"] is True)
    t("a stateless mid-ledger slice never sets master_complete",
      validate_ledger(e2[len(e1):])["state"]["master_complete"] is False)
    no_master = [e for e in e2 if e["kind"] not in ("asset.define", "inventory.item")]
    rep_nm = validate_ledger(no_master, complete=True)
    t("complete=True without any asset.define / inventory.item → every reference reported "
      "(unknown_asset on all %d lines, unknown_item on every wo.part)" % len(no_master),
      sum(1 for e in rep_nm["errors"] if e["code"] == "unknown_asset") == len(no_master)
      and sum(1 for e in rep_nm["errors"] if e["code"] == "unknown_item")
      == sum(1 for e in no_master if e["kind"] == "wo.part"))
    t("the same lines without complete=True (no from-start chain) → not judged",
      not {"unknown_asset", "unknown_item"} & set(validate_ledger(no_master)["codes"]))

    n_fail = results.count(False)
    print("[wsynth_seed selftest] %s — %d checks, %d failed" % ("PASS" if not n_fail else "FAIL",
                                                                len(results), n_fail))
    return 0 if not n_fail else 1


if __name__ == "__main__":
    import sys as _sys
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8")
    if "--selftest" in _sys.argv[1:]:
        _sys.exit(_selftest())
    print(__doc__)
