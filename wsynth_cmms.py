# -*- coding: utf-8 -*-
"""
wsynth_cmms.py — 合成產線的假 cmms（SPEC-SYNTHLINE WP-5；第二批裁決 Q-D：loopback HTTP 伺服器）
================================================================================================

一句話：把 `tendline.seed.v1.1` 事件流**重播到時刻 T**，物化成 Tendline Board 向 cmms 請求的每一種
wire JSON（UTC `Z`、秒精度），再用一個**只綁 127.0.0.1、只回 GET** 的 HTTP 伺服器端出來。
Board 端零改碼：設 `BOARD_CMMS_BASE_URL=http://127.0.0.1:<port>`＋`BOARD_CMMS_TOKEN`
（呼叫表 #19 的既有機制），`board_w8_transport` 的公開函式與兩個走私有 `_http_get_json`
的呼叫端（呼叫表 #9／#10）都走同一條真碼路。

定位
  · **只給 CI 與本機**（裁決 Q8）：sandbox 上 Tendline Board 直連 Tendline Work，不經本檔。
  · 本檔的語意是**對 cmms 的模仿**，不是 Board 的語意：窗過濾照 cmms `_active_window_end`、
    `hold_reason` 只在 ON_HOLD 時有值、`closed_at` 只由 CLOSED 轉移寫入、`downtime_minutes`
    恆 null（cmms 2026-09-11 起新單恆 null）、`downtime_estimated` 恆 false 除非事件寫入。
    ⚠ AP-12：與 Tendline Work 重播的逐欄對版由 Chris 帶（WP-2 golden seed 到位後）。

端點（cmms wire 形；`/work-orders/active-in` 等路徑對照 cmms 路由實讀）
  GET /work-orders/active-in?start=&end=[&asset_id=]   → {items:[…], truncated}
  GET /work-orders/{no}/detail                         → 平鋪 WO（contract_wo_detail.v1）
  GET /assets/{asset_id}/schedule-state-events         → 裸 list（contract_asset_schedule_state.v1）
  GET /assets/schedule-state?eids=&as_of=              → {items, unknown}（…_batch.v1）
  其餘 GET ⇒ 404 `{"detail":…}`；非 GET ⇒ 405（`Allow: GET`）；缺／錯 bearer ⇒ 401。
  **刻意不涵蓋**（第一期外、呼叫端在 synth 下不開）：/pm-schedules、/work-orders（PM LIST）、
  /tasks、/tasks/{no}/steps、/inventory-items、/cost/aggregate ⇒ 一律 404（誠實降級，不假裝有）。

鐵則
  · C-SY1：本檔**不開任何檔**，除了呼叫端明示的種子目錄（且拒絕 repo 內、`_fixtures`、
    `_state`、snapshot 形的路徑）。資料來源＝記憶體內的世界、產生器，或種子目錄。
  · C-SY4：時刻 T 一律取自 `wsynth_clock.now()`（測試可注入 `now_fn`，不必動 os.environ）。
    曝光規則＝事件 `at <= T`；`T` 以後的事件不進任何回應。
  · C-SY5：同一世界同一 T ⇒ 回應逐位元相同（JSON 鍵序固定、緊湊分隔、UTF-8）。
  · Q7：人名欄（`closed_by`／`assigned_person`／`assignees`／`source_actor`）一律由事件的
    `actor` 角色字串填，永不造名字。
  · 本檔不含任何站名、機台名、EID 或工單號字面值（selftest 掃原始碼釘住）。

CLI
  python wsynth_cmms.py [--port N] [--token T] [--seed S | --seed-dir DIR [--allow-test-world]]
                                                                            # 前景服務，Ctrl-C 結束
  python wsynth_cmms.py --selftest                                            # 見 wsynth_cmms_selftest.py
純標準函式庫；本 repo 內只 import `wsynth_clock`／`wsynth_world`／`wsynth_params`／`wsynth_seed`
（種子目錄的解析、驗證、拒讀全部委派 `wsynth_seed`，本檔不留第二份解析邏輯）。
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import http.server
import json
import os
import re
import sys
import threading
import urllib.parse

import wsynth_clock as C
import wsynth_params as P
import wsynth_seed as _seed
from wsynth_world import is_downtime as _is_downtime

# ---------------------------------------------------------------------------
# 常數（cmms 端的值；對照 cmms repo 路由與服務層實讀，非 Board 端的常數）
# ---------------------------------------------------------------------------
LOOPBACK = "127.0.0.1"
DEFAULT_TOKEN = "wsynth-loopback-token"
LIST_PATH = "/work-orders/active-in"
BATCH_PATH = "/assets/schedule-state"
_DETAIL_RE = re.compile(r"^/work-orders/([0-9]+)/detail$")
_A11_EVENTS_RE = re.compile(r"^/assets/([^/]+)/schedule-state-events$")
ACTIVE_IN_CAP = 2000          # cmms `_ACTIVE_IN_CAP`
BATCH_MAX_EIDS = 200          # cmms `MAX_BATCH_EIDS`（去重後超過 ⇒ 422，不截斷）
ACTIVE_IN_TS_FMT = "%Y-%m-%d %H:%M:%S"
TERMINAL = ("CLOSED", "CANCELLED", "VOIDED")
WO_KINDS = ("wo.open", "wo.status", "wo.scope", "wo.note", "wo.part", "wo.field")

COVERED = (
    ("GET", LIST_PATH, "contract_wo_active_in.v1"),
    ("GET", "/work-orders/{no}/detail", "contract_wo_detail.v1"),
    ("GET", "/assets/{asset_id}/schedule-state-events", "contract_asset_schedule_state.v1"),
    ("GET", BATCH_PATH, "contract_asset_schedule_state_batch.v1"),
)
NOT_COVERED = ("/pm-schedules", "/work-orders", "/tasks", "/tasks/{no}/steps",
               "/inventory-items", "/cost/aggregate")


class SeedDirRefused(RuntimeError):
    """種子目錄指向 repo 內或真資料形的路徑。"""


class SeedDirInvalid(RuntimeError):
    """種子目錄不完整或雜湊不符。"""


def dumps(obj) -> bytes:
    """回應序列化的單一出處（伺服器與 selftest 共用 ⇒ 逐位元比對有意義）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _tp(z):
    """UTC Z → 台北 naive；None 透傳。"""
    return None if z is None else C.from_utc_z(z)


# ===========================================================================
# 1) 重播：事件流 → T 時刻的狀態（純函式）
# ===========================================================================

class State:
    """T 時刻的物化狀態。只由 `replay()` 產生；欄位皆為衍生自事件的原子值。"""

    __slots__ = ("t", "t_z", "assets", "master", "items", "wos", "a11", "skipped_unknown_kind",
                 "skipped_reserved",
                 "skipped_orphan", "world_ids")

    def __init__(self, t):
        self.t = t
        self.t_z = C.to_utc_z(t)
        self.assets = {}          # asset_id → define 事件
        self.master = set()       # 資產主檔（define 的 asset_id ∪ 各座 asset_id）
        self.items = {}           # seed v1.1 料號主檔：item_code → inventory.item 事件（不入資產主檔）
        self.wos = {}             # wno → {"open", "status", "scope", "note", "part", "field"}
        self.a11 = []             # asset.schedule_state 事件
        self.skipped_unknown_kind = 0
        self.skipped_reserved = 0
        self.skipped_orphan = 0
        self.world_ids = set()


def replay(events, t):
    """事件流（任意序）→ `State`（只納入 `at <= t`；依 `seq` 重播）。

    · 未知 `kind` ⇒ 跳過並計數；保留 kind（`wsynth_seed.RESERVED_KINDS`）⇒ 跳過並另計
      （§5.4 第 1 條：閉集由宣告方把關、消費方寬進）。
    · `inventory.item`（v1.1）⇒ 料號主檔 `State.items`（名稱來源，見 `item_names`；不改 wire）。
    · `wo.*` 在其 `wo.open` 之前出現 ⇒ 跳過並計數（產生器不變式保證不會發生；防禦）。"""
    t = t.replace(microsecond=0)
    st = State(t)
    tz = st.t_z
    for e in sorted((e for e in events if e["at"] <= tz), key=lambda e: e["seq"]):
        k = e["kind"]
        st.world_ids.add(e.get("world_id"))
        if k == "asset.define":
            st.assets[e["asset_id"]] = e
            st.master.add(e["asset_id"])
            for s in e.get("seats") or ():
                if s.get("asset_id"):
                    st.master.add(s["asset_id"])
        elif k == "asset.schedule_state":
            st.a11.append(e)
        elif k == "inventory.item":
            # seed v1.1：料號主檔（asset_id 是哨兵、不是資產 ⇒ 不進 master）；同料號後寫蓋前寫
            # （驗證器另以 item_redefined 擋；物化器照 cmms upsert 語意寬進）
            st.items[e["item_code"]] = e
        elif k in WO_KINDS:
            wno = e["work_order_no"]
            if k == "wo.open":
                st.wos[wno] = {"open": e, "status": [], "scope": [], "note": [], "part": [],
                               "field": {}}
                continue
            w = st.wos.get(wno)
            if w is None:
                st.skipped_orphan += 1
                continue
            if k == "wo.field":
                w["field"][e["field"]] = e          # 同欄後寫蓋前寫（seq 序）
            else:
                w[k.split(".", 1)[1]].append(e)
        elif k in _seed.RESERVED_KINDS:
            st.skipped_reserved += 1                # 保留 kind（不屬 v1.1）：跳過、與未知分開計
        else:
            st.skipped_unknown_kind += 1
    return st


# ===========================================================================
# 2) 物化：狀態 → wire JSON（純函式；鍵序照 cmms Pydantic 模型宣告序）
# ===========================================================================

def _history(w):
    """cmms `status_history`：首筆＝開單（from null → OPEN），其後依 seq。"""
    op = w["open"]
    wt = op["work_type"]
    out = [{"from_status": None, "to_status": "OPEN", "hold_reason": None,
            "changed_at": op["at"], "source_actor": op["actor"],
            "is_downtime": _is_downtime("OPEN", None, wt)}]
    for e in w["status"]:
        out.append({"from_status": e["from_status"], "to_status": e["to_status"],
                    "hold_reason": e.get("hold_reason") if e["to_status"] == "ON_HOLD" else None,
                    "changed_at": e["at"], "source_actor": e["actor"],
                    "is_downtime": bool(e["is_downtime"])})
    return out


def _field(w, name, default=None):
    e = w["field"].get(name)
    return default if e is None else e["value"]


def _core(w, hist):
    """LIST 與 detail 共用的單值欄（cmms `_transition` 語意）。"""
    op = w["open"]
    last = hist[-1]
    status = last["to_status"]
    closed = [h["changed_at"] for h in hist if h["to_status"] == "CLOSED"]
    return {
        "status": status,
        "hold_reason": last["hold_reason"] if status == "ON_HOLD" else None,
        "opened_at": op["at"],
        "closed_at": closed[-1] if closed else None,     # 只由 CLOSED 轉移寫入（取消不清）
        "failure_started_at": op.get("failure_started_at"),
        "production_resumed_at": _field(w, "production_resumed_at"),
    }


def _window_end(hist, closed_at, now):
    """cmms `_active_window_end`：首個 COMPLETED／終態；無 ⇒ closed_at；再無 ⇒ now。"""
    for h in hist:
        if h["to_status"] == "COMPLETED" or h["to_status"] in TERMINAL:
            return _tp(h["changed_at"])
    return _tp(closed_at) if closed_at is not None else now


def active_in(st, start, end, asset_id=None):
    """`GET /work-orders/active-in` → `{items, truncated}`。

    窗語意＝cmms：活躍窗 `[opened_at, 首個 COMPLETED／終態（無 ⇒ closed_at；再無 ⇒ T）]`
    與 `[start, end]` 相交（兩端含）。依工單號降冪、上限 `ACTIVE_IN_CAP`。"""
    rows = []
    for wno in sorted(st.wos, reverse=True):
        w = st.wos[wno]
        op = w["open"]
        if asset_id is not None and op["asset_id"] != asset_id:
            continue
        hist = _history(w)
        core = _core(w, hist)
        opened = _tp(core["opened_at"])
        if not (opened <= end and _window_end(hist, core["closed_at"], st.t) >= start):
            continue
        rows.append({
            "work_order_no": wno, "asset_id": op["asset_id"], "work_type": op["work_type"],
            "opened_at": core["opened_at"], "closed_at": core["closed_at"],
            "status": core["status"], "hold_reason": core["hold_reason"],
            "failure_started_at": core["failure_started_at"],
            "production_resumed_at": core["production_resumed_at"],
            "status_history": hist,
        })
    return {"items": rows[:ACTIVE_IN_CAP], "truncated": len(rows) > ACTIVE_IN_CAP}


def _roles(w, hist, role):
    seen = []
    for h in hist:
        a = h["source_actor"]
        if a == role and a not in seen:
            seen.append(a)
    return seen


def detail(st, wno):
    """`GET /work-orders/{no}/detail` → 平鋪 WO；未曝光或不存在 ⇒ None（404）。"""
    w = st.wos.get(wno)
    if w is None:
        return None
    op = w["open"]
    hist = _history(w)
    core = _core(w, hist)
    closed_evs = [h for h in hist if h["to_status"] == "CLOSED"]
    assignees = _roles(w, hist, "role:technician")
    est = _field(w, "downtime_estimated", False)
    scope_events = []
    if op.get("scope"):
        # SPEC §5.4-7（兩 repo 對版點）：開單即部分座 ⇒ 範圍記在 wo.open.scope、不另發 wo.scope；
        # 物化時在**開單同一刻**產生 scope_events 首筆，之後每個 wo.scope 再各一筆
        scope_events.append({"ts": op["at"], "scope": list(op["scope"]),
                             "sub_units": list(op.get("sub_units") or [])})
    for e in w["scope"]:
        scope_events.append({"ts": e["at"], "scope": list(e["scope"]),
                             "sub_units": list(e.get("sub_units") or [])})
    return {
        "work_order_no": wno,
        "asset_id": op["asset_id"],
        "work_type": op["work_type"],
        "status": core["status"],
        "brief_description": op.get("brief_description"),
        "diagnosis": _field(w, "diagnosis"),
        "external_ref": None,
        "opened_date": _tp(op["at"]).date().isoformat(),
        "scheduled_date": _field(w, "scheduled_date"),
        "work_start_time": None,          # eMaint 匯入欄；cmms 原生流程不寫
        "work_complete_time": None,
        "closed_date": None,
        "closed_time": None,
        "closed_by": closed_evs[-1]["source_actor"] if closed_evs else None,
        "assigned_vendor": _field(w, "assigned_vendor"),
        "assigned_person": assignees[0] if assignees else None,
        "action_taken": _field(w, "action_taken"),
        "confirmed_reason_code": _field(w, "confirmed_reason_code"),
        "pm_source_id": op.get("pm_source_id"),
        "opened_at": core["opened_at"],
        "closed_at": core["closed_at"],
        "hold_reason": core["hold_reason"],
        # cmms 2026-09-11 起不再計算停機時數：新單恆 null（不另存第二份停機量）
        "downtime_minutes": None,
        "downtime_estimated": bool(est),
        "failure_started_at": core["failure_started_at"],
        "production_resumed_at": core["production_resumed_at"],
        "status_history": hist,
        "parts": [{"item_code": e["item_code"], "quantity": float(e["quantity"])}
                  for e in w["part"]],
        "notes": [{"occurred_at": e["at"], "entry_type": e["entry_type"], "body": e["body"],
                   "source_actor": e["actor"]} for e in w["note"]],
        "notes_truncated": False,
        "assignees": assignees,
        "scope_events": scope_events,
    }


def item_names(st):
    """seed v1.1 料號主檔 → `{item_code: display_name}`（領料顯示用料號名的來源）。

    **不改任何 wire 形**：`contract_wo_detail.v1` 的 `parts[]` 只有 `item_code`／`quantity`；
    `/inventory-items` 仍不涵蓋（`NOT_COVERED`；第一期 Board 在合成進程不讀它）。"""
    return {code: e["display_name"] for code, e in sorted(st.items.items())}


def _a11_row(e):
    eff = _dt.date.fromisoformat(e["effective_date"])
    return {"ts_effective": C.to_utc_z(C.day_start(eff)),
            "effective_date": e["effective_date"],
            "sub_unit": e.get("sub_unit"),
            "state": e["state"],
            "reason": e.get("reason"),
            "recorded_at": e["at"]}


def schedule_state_events(st, asset_id):
    """`GET /assets/{id}/schedule-state-events` → 裸 list；資產不在主檔 ⇒ None（404）。
    所有 lane（整機＋工位）混在同一條 `ts_effective` 升冪序（同刻依 seq）。"""
    if asset_id not in st.master:
        return None
    evs = [e for e in st.a11 if e["asset_id"] == asset_id]
    evs.sort(key=lambda e: (e["effective_date"], e["seq"]))
    return [_a11_row(e) for e in evs]


def schedule_state_batch(st, eids=None, as_of=None):
    """`GET /assets/schedule-state` → `{items, unknown}`。只看整機 lane（sub_unit is None）；
    as-of＝生效日 ≤ as_of 的最後一筆；無事件 ⇒ in_production／since null。"""
    day = as_of or st.t.date()
    if eids is None:
        scope, unknown = sorted(st.master), []
    else:
        scope = sorted(a for a in eids if a in st.master)
        unknown = sorted(a for a in eids if a not in st.master)
    cut = day.isoformat()
    latest = {}
    for e in sorted(st.a11, key=lambda e: (e["effective_date"], e["seq"])):
        if e.get("sub_unit") is None and e["effective_date"] <= cut:
            latest[e["asset_id"]] = e
    items = []
    for aid in scope:
        e = latest.get(aid)
        if e is None:
            items.append({"asset_id": aid, "state": "in_production", "since": None,
                          "reason": None})
        else:
            items.append({"asset_id": aid, "state": e["state"], "since": e["effective_date"],
                          "reason": e.get("reason")})
    return {"items": items, "unknown": unknown}


# ===========================================================================
# 3) 路由：(path, query) → (status, body)（純函式；伺服器與 selftest 共用）
# ===========================================================================

def _err(code, msg):
    return code, dumps({"detail": msg})


def _parse_eids(raw):
    """cmms `_parse_eids`：CSV → 去空白、丟空 token、去重保序。"""
    return list(dict.fromkeys(e.strip() for e in raw.split(",") if e.strip()))


def route(st, path, query):
    """GET 路由。回 `(http_status, body_bytes)`。"""
    q = urllib.parse.parse_qs(query, keep_blank_values=True)

    def one(name):
        v = q.get(name)
        return v[-1] if v else None

    if path == LIST_PATH:
        s, e = one("start"), one("end")
        if s is None or e is None:
            return _err(422, "start and end are required")
        try:
            start = _dt.datetime.strptime(s, ACTIVE_IN_TS_FMT)
            end = _dt.datetime.strptime(e, ACTIVE_IN_TS_FMT)
        except ValueError as exc:
            return _err(422, "start/end must be naive 'YYYY-MM-DD HH:MM:SS': %s" % exc)
        return 200, dumps(active_in(st, start, end, one("asset_id")))
    if path == BATCH_PATH:
        raw_as_of = one("as_of")
        as_of = None
        if raw_as_of is not None:
            try:
                as_of = _dt.date.fromisoformat(raw_as_of)
            except ValueError:
                return _err(422, "as_of must be YYYY-MM-DD")
        raw = one("eids")
        eids = None if raw is None else _parse_eids(raw)
        if eids is not None and len(eids) > BATCH_MAX_EIDS:
            return _err(422, "eids exceeds %d (got %d)" % (BATCH_MAX_EIDS, len(eids)))
        return 200, dumps(schedule_state_batch(st, eids, as_of))
    m = _DETAIL_RE.match(path)
    if m:
        d = detail(st, int(m.group(1)))
        if d is None:
            return _err(404, "work_order %s not found" % m.group(1))
        return 200, dumps(d)
    m = _A11_EVENTS_RE.match(path)
    if m:
        aid = urllib.parse.unquote(m.group(1))
        rows = schedule_state_events(st, aid)
        if rows is None:
            return _err(404, "asset %s not found" % aid)
        return 200, dumps(rows)
    return _err(404, "Not Found")


# ===========================================================================
# 4) 資料來源（記憶體世界／產生器／種子目錄）
# ===========================================================================

class WorldSource:
    """`wsynth_world.build_world()` 的結果（記憶體內）。只含到其 `exposed_until` 為止的事件。"""

    def __init__(self, world):
        self.world_id = world["world_id"]
        self.exposed_until = C.from_utc_z(world["exposed_until"])
        self._events = list(world["events"])

    def events_until(self, t):
        return self._events


class GeneratorSource:
    """直接由產生器供事件：T 超過已建範圍就把世界建到 T 所在台北日的日終。

    因產生器逐日確定、前綴穩定（C-SY5），「建到日終再以 at <= T 過濾」≡「建到 T」。
    ⇒ 沒有 tick 間隔，回應永遠追到牆鐘（R-14 在本機路徑上不存在）。"""

    def __init__(self, seed=P.DEFAULT_SEED, *, ctx=None, **ctx_kw):
        import wsynth_world as W
        self._W = W
        self._ctx = ctx or W.make_context(seed, **ctx_kw)
        self.world_id = self._ctx.world_id
        self._built_day = None
        self._events = []
        self._lock = threading.Lock()
        self.exposed_until = None

    def events_until(self, t):
        day = t.date()
        with self._lock:
            if self._built_day is None or day > self._built_day:
                eod = C.day_start(day) + _dt.timedelta(days=1) - _dt.timedelta(seconds=1)
                w = self._W.build_world(eod, ctx=self._ctx, with_segments=False)
                self._events = w["events"]
                self._built_day = day
            return self._events


_FORBIDDEN_PARTS = ("_fixtures", "_state")

def check_seed_dir(path, repo_root=None):
    """拒絕 repo 內、`_fixtures`／`_state`／snapshot 形的路徑（C-SY1／C-SY12）。回傳絕對路徑。"""
    root = repo_root or os.path.dirname(os.path.abspath(__file__))
    real = os.path.realpath(path)
    r = os.path.normcase(os.path.realpath(root))
    p = os.path.normcase(real)
    if p == r or p.startswith(r.rstrip("\\/") + os.sep):
        raise SeedDirRefused("seed dir inside the repo is refused: %s" % path)
    parts = [x.lower() for x in re.split(r"[\\/]+", p) if x]
    for x in parts:
        if x in _FORBIDDEN_PARTS or x.startswith("snapshot"):
            raise SeedDirRefused("seed dir looks like a real-data path: %s" % path)
    return real


# 測試世界標記：正式鍵名＝`test_only`（SPEC §5.1）。判定與拒讀一律委派 `wsynth_seed`
# （`is_test_world`／`load_ledger(allow_test_world=)`，錯誤類別＝`wsynth_seed.SeedRejected`）。
TEST_MARKER_KEYS = ("test_only",)


def manifest_is_test_world(man):
    """manifest → 是不是測試世界（委派 `wsynth_seed.is_test_world`）。"""
    return _seed.is_test_world(man)


class SeedDirSource:
    """種子目錄（`wsynth_emit` 產出的 `seed/` 子目錄或其父目錄）。

    **解析與驗證全部委派 `wsynth_seed`**（本類只負責開檔）：
      · manifest 位元組 → `parse_manifest_bytes`；壞形／非 UTF-8 ⇒ `SeedDirInvalid`。
      · 日檔集合 → `check_day_files`（完整模式：manifest 列的每個檔都要在場、多出的檔也算錯、
        逐檔 sha256／行數／LF）⇒ 不符 `SeedDirInvalid`。
      · 日檔位元組 → `split_day_bytes`（逐行獨立解碼；非 UTF-8 行＝`encoding`、非 JSON＝`envelope`，
        不拋例外）→ `load_ledger(..., complete=True)`：拒讀條件（schema／world_id／test_world／
        續讀指紋 state_mismatch）⇒ `wsynth_seed.SeedRejected`；未知 kind 與保留 kind 跳過並分開計數；
        其餘行級／帳級錯誤只記不炸（`self.report["errors"]`），壞行丟棄。
      · manifest 形狀錯（`manifest`）⇒ `SeedDirInvalid`；壞行連帶的逐日行數 `manifest_mismatch`
        只記（檔案層完整性已由 `check_day_files` 把關）。
    續讀（tick 覆寫後重讀）：`reload()` 以第一次讀到的 manifest 指紋為 state 重讀，指紋
    （`wsynth_seed.STATE_FINGERPRINT_KEYS`＝schema、world_id、generator_version、fleet_digest、horizon_start）
    任一不同 ⇒ `SeedRejected(state_mismatch)`。
    只含到 manifest `exposed_until` 為止的事件（tick 之間會落後牆鐘＝R-14）。"""

    def __init__(self, path, *, allow_test_world=False, fingerprint=None):
        real = check_seed_dir(path)
        if os.path.isdir(os.path.join(real, "seed")):
            real = os.path.join(real, "seed")
        self.path = path
        self.allow_test_world = allow_test_world
        with open(os.path.join(real, "seed-manifest.json"), "rb") as fh:
            man, m_errs = _seed.parse_manifest_bytes(fh.read())
        if m_errs:
            raise SeedDirInvalid("manifest unreadable: %s" % (m_errs,))
        files = {}
        for name in sorted(os.listdir(real)):
            if name.startswith("seed-") and name.endswith(".jsonl"):
                with open(os.path.join(real, name), "rb") as fh:
                    files[name] = fh.read()
        try:
            f_errs = _seed.check_day_files(man, files)
        except (AttributeError, TypeError) as exc:      # manifest 不是物件等（形狀錯）
            raise SeedDirInvalid("manifest shape: %s" % exc) from exc
        if f_errs:
            raise SeedDirInvalid("day files do not match manifest: %s" % (f_errs,))
        lines = []
        for d in man["days"]:
            lines.extend(_seed.split_day_bytes(files[d["file"]]))
        state = None
        if fingerprint is not None:
            state = _seed.new_state()
            state["fingerprint"] = dict(fingerprint)
        rep = _seed.load_ledger(lines, manifest=man, complete=True, state=state,
                                allow_test_world=allow_test_world)
        # manifest 形狀錯（`manifest`）⇒ 不可讀。帳級 `manifest_mismatch`（逐日行數）不另拒：
        # 檔案層的集合／sha256／行數已由 check_day_files 驗過，此處的不符只可能是讀不出的行
        # 無法歸日（wsynth_seed 的既定連帶報錯）⇒ 與壞行同級，只記不炸。
        bad = [e for e in rep["errors"] if e["code"] == "manifest"]
        if bad:
            raise SeedDirInvalid("manifest-level errors: %s" % (bad[:5],))
        self.report = rep
        self.fingerprint = _seed.manifest_fingerprint(man)
        self.test_world = _seed.is_test_world(man)
        self.world_id = man["world_id"]
        self.exposed_until = C.from_utc_z(man["exposed_until"])
        self._events = rep["events"]

    def reload(self):
        """重讀同一目錄（tick 覆寫後）；指紋必須與第一次相同。"""
        return SeedDirSource(self.path, allow_test_world=self.allow_test_world,
                             fingerprint=self.fingerprint)

    def events_until(self, t):
        return self._events


# ===========================================================================
# 5) 物化器（快取 T → State）
# ===========================================================================

class Materializer:
    """來源＋時鐘 → 每個請求的回應。`now_fn` 預設 `wsynth_clock.now`（C-SY4）。

    有 `exposed_until` 的來源（記憶體世界／種子目錄）：T 超過它就以它為準（不假裝知道之後）。"""

    def __init__(self, source, now_fn=None, cache=8, extra_master=()):
        self.source = source
        # 資產主檔補充（呼叫端明示；只讓這些 ID「存在、無事件」＝cmms 的「從頭到尾排產中」）。
        # 用途＝Board 名冊裡有、合成世界 asset.define 沒有的 EID（見 WP-5 回報 G-1）；預設空。
        self.extra_master = tuple(sorted(set(extra_master)))
        self.now_fn = now_fn or C.now
        self._cache = {}
        self._order = []
        self._cap = cache
        self._lock = threading.Lock()

    def effective_t(self, t=None):
        t = (t or self.now_fn()).replace(microsecond=0)
        ex = getattr(self.source, "exposed_until", None)
        if ex is not None and t > ex:
            t = ex
        return t

    def state(self, t=None):
        t = self.effective_t(t)
        with self._lock:
            st = self._cache.get(t)
            if st is not None:
                return st
        st = replay(self.source.events_until(t), t)
        st.master.update(self.extra_master)
        with self._lock:
            self._cache[t] = st
            self._order.append(t)
            while len(self._order) > self._cap:
                self._cache.pop(self._order.pop(0), None)
        return st

    def get(self, path, query="", t=None):
        return route(self.state(t), path, query)


# ===========================================================================
# 6) loopback HTTP 伺服器（唯讀、只綁 127.0.0.1）
# ===========================================================================

class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def _make_handler(mat, token, quiet):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "wsynth-cmms/1"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):     # noqa: N802
            if not quiet:
                sys.stderr.write("[wsynth_cmms] " + (fmt % args) + "\n")

        def _send(self, code, body, extra=()):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):                      # noqa: N802
            auth = self.headers.get("Authorization", "")
            if auth != "Bearer " + token:
                self._send(401, dumps({"detail": "missing or invalid bearer token"}))
                return
            parts = urllib.parse.urlsplit(self.path)
            try:
                t = mat.effective_t()
                code, body = mat.get(parts.path, parts.query, t)
            except Exception as exc:           # noqa: BLE001（伺服器不得因單一請求崩）
                code, body = 500, dumps({"detail": "internal error: %s" % type(exc).__name__})
                t = None
            extra = [("X-Synth-T", C.to_utc_z(t))] if t is not None else []
            self._send(code, body, extra)

        def _not_allowed(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            self._send(405, dumps({"detail": "Method Not Allowed"}), [("Allow", "GET")])

        do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _not_allowed

    return Handler


def make_server(source=None, *, port=0, token=DEFAULT_TOKEN, now_fn=None, quiet=True,
                host=LOOPBACK):
    """建立（未啟動的）伺服器。**只准 127.0.0.1**；`port=0`＝挑空閒埠。"""
    if host != LOOPBACK:
        raise ValueError("wsynth_cmms binds loopback only (got %r)" % (host,))
    if not token:
        raise ValueError("token must be non-empty")
    mat = source if isinstance(source, Materializer) else \
        Materializer(source if source is not None else GeneratorSource(), now_fn=now_fn)
    srv = _Server((LOOPBACK, int(port)), _make_handler(mat, token, quiet))
    srv.materializer = mat
    return srv


@contextlib.contextmanager
def serve(source=None, *, port=0, token=DEFAULT_TOKEN, now_fn=None, quiet=True):
    """測試用：啟動伺服器（背景執行緒）→ yield base URL → 結束時關閉。

        with serve(WorldSource(world), token="t", now_fn=lambda: T) as base:
            # 子進程 env：BOARD_CMMS_BASE_URL=base、BOARD_CMMS_TOKEN="t"
    """
    srv = make_server(source, port=port, token=token, now_fn=now_fn, quiet=quiet)
    th = threading.Thread(target=srv.serve_forever, name="wsynth-cmms", daemon=True)
    th.start()
    try:
        yield "http://%s:%d" % srv.server_address[:2]
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Loopback fake cmms for the synthetic line (CI/local only).")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    ap.add_argument("--seed", default=P.DEFAULT_SEED)
    ap.add_argument("--seed-dir")
    ap.add_argument("--allow-test-world", action="store_true",
                    help="read a seed dir whose manifest marks it as a test world")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        import wsynth_cmms_selftest
        return wsynth_cmms_selftest.main([])
    C.check_utc_offset()
    src = (SeedDirSource(a.seed_dir, allow_test_world=a.allow_test_world) if a.seed_dir
           else GeneratorSource(a.seed))
    srv = make_server(src, port=a.port, token=a.token, quiet=not a.verbose)
    print("wsynth_cmms world_id=%s base=http://%s:%d" % ((src.world_id,) + srv.server_address[:2]),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
