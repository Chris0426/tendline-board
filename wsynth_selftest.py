# -*- coding: utf-8 -*-
"""
wsynth_selftest.py — 合成產線 WP-1 的集中自測（wsynth_clock／params／world／emit）
===================================================================================

`python wsynth_selftest.py [--only NAME[,NAME…]] [--stats] [--slow]`（--slow＝multiseed 跑 200 個 seed）

十二組（對應派工單「自測至少涵蓋」1–12）：
  determinism  prefix  exposure  lifecycle  mes_consistency  diseases  zero_real_data
  source_scan  ranges  schema  clock  sanity
所有輸出只寫系統暫存目錄（C-SY12）；不連 MES、不打任何端點、不讀 `_fixtures`／`_state`。
失敗照實印 FAIL，不遷就。
"""
from __future__ import annotations

import bisect
import collections
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.abspath(__file__))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import wsynth_clock as C      # noqa: E402
import wsynth_emit as E       # noqa: E402
import wsynth_params as P     # noqa: E402
import wsynth_world as W      # noqa: E402

OWN_FILES = ("wsynth_clock.py", "wsynth_params.py", "wsynth_world.py", "wsynth_emit.py",
             "wsynth_selftest.py")

_RESULTS = []


def t(name, cond, detail=""):
    _RESULTS.append((name, bool(cond)))
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name, ("  — " + str(detail)) if detail and not cond else ""))
    return bool(cond)


def _dt_iso(s):
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


def _full_until():
    return _dt.datetime.combine(P.GO_LIVE_DATE, _dt.time(0, 0))


def _files_hash(files):
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8") + b"\0" + files[rel] + b"\0")
    return h.hexdigest()


_CACHE = {}


def full_world():
    if "full" not in _CACHE:
        t0 = time.time()
        _CACHE["full"] = W.build_world(_full_until())
        _CACHE["full_secs"] = time.time() - t0
    return _CACHE["full"]


# ---------------------------------------------------------------------------
# 1) determinism
# ---------------------------------------------------------------------------

_SUB_SCRIPT = r"""
import sys, os, hashlib, datetime as dt
sys.path.insert(0, os.getcwd())
import wsynth_world as W, wsynth_emit as E, wsynth_params as P
w = W.build_world(dt.datetime.combine(P.GO_LIVE_DATE, dt.time(0, 0)))
files = E.render(w)
h = hashlib.sha256()
for rel in sorted(files):
    h.update(rel.encode('utf-8') + b'\0' + files[rel] + b'\0')
print('HASH=' + h.hexdigest())
"""


def _sub_hash(hashseed):
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(hashseed)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop(C.ENV_FREEZE, None)
    r = subprocess.run([sys.executable, "-c", _SUB_SCRIPT], cwd=REPO, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    m = re.search(r"HASH=([0-9a-f]{64})", r.stdout)
    return m.group(1) if m else ("ERR rc=%s %s" % (r.returncode, r.stderr[-400:]))


def test_determinism():
    print("[1] determinism")
    a = full_world()
    b = W.build_world(_full_until())
    fa, fb = E.render(a), E.render(b)
    ha, hb = _files_hash(fa), _files_hash(fb)
    t("same input twice in-process → identical JSONL+manifest bytes", ha == hb, (ha, hb))
    t("manifest bytes identical", fa["seed/seed-manifest.json"] == fb["seed/seed-manifest.json"]
      and fa["mes/mes-manifest.json"] == fb["mes/mes-manifest.json"])
    h0 = _sub_hash(0)
    h1 = _sub_hash(4242)
    t("separate process PYTHONHASHSEED=0 → same hash", h0 == ha, h0)
    t("separate process PYTHONHASHSEED=4242 → same hash", h1 == ha, h1)
    c = W.build_world(_full_until(), "another-seed")
    t("different seed → different world_id and bytes",
      c["world_id"] != a["world_id"] and _files_hash(E.render(c)) != ha)
    # 凍結時鐘不影響世界（產生器不取時間）
    old = os.environ.get(C.ENV_FREEZE)
    try:
        u = _dt.datetime(2026, 8, 1, 12, 0, 0)
        os.environ[C.ENV_FREEZE] = "2026-08-20T00:00:00"
        x = _files_hash(E.render(W.build_world(u, with_segments=False)))
        os.environ[C.ENV_FREEZE] = "2027-01-01T00:00:00"
        y = _files_hash(E.render(W.build_world(u, with_segments=False)))
    finally:
        if old is None:
            os.environ.pop(C.ENV_FREEZE, None)
        else:
            os.environ[C.ENV_FREEZE] = old
    t("world independent of the clock env (generator never reads now)", x == y)
    _test_salt_decoupling()


# g3 世界的亂數流釘（v1.1 審查 Finding 1；與名冊無關）：以**舊構造**（鹽＝版號字串
# "tendline-synth-g3"）直接算 blake2b 得到的值。g4 起 Rng 改吃 WORLD_RNG_SALT，必須逐值相同。
_G3_RNG_PINS = ((("pin", 1), "e8fdbba6d4f70c06"), (("pin", 4), "f79cd63e9bc6d803"),
                (("pin", 6), "1c48f6a22be44c3f"))


def _test_salt_decoupling():
    """解耦前後同種子世界內容不變（段與工單逐位元相同，只有 world_id／generator_version 不同）。"""
    t("WORLD_RNG_SALT pinned to the g3 value; GENERATOR_VERSION moved past it",
      P.WORLD_RNG_SALT == "tendline-synth-g3" and P.GENERATOR_VERSION != P.WORLD_RNG_SALT)
    r = W.Rng(P.DEFAULT_SEED, P.WORLD_RNG_SALT)
    t("Rng(default seed, WORLD_RNG_SALT) reproduces the g3 random stream (pinned blake2b values)",
      all(r.u(*k) == int(h, 16) / 18446744073709551616.0 for k, h in _G3_RNG_PINS))
    u = _dt.datetime.combine(P.HORIZON_START + _dt.timedelta(days=14), _dt.time(0, 0))
    cur = W.build_world(u)
    # 舊（g3）組態＝版號字串與鹽同為 g3；與現行（g4 版號、g3 鹽）只應差在 world_id／版號
    old = W.build_world(u, ctx=W.make_context(version=P.WORLD_RNG_SALT))
    odd = W.build_world(u, ctx=W.make_context(version="tendline-synth-selftest-other"))

    def strip(rows):
        return [{k: v for k, v in r.items() if k != "world_id"} for r in rows]
    t("generator_version is not an RNG input: g3-version world ≡ current world (events and segments "
      "byte-identical except world_id; 14 days, %d events, %d segments)"
      % (len(cur["events"]), len(cur["segments"])),
      strip(cur["events"]) == strip(old["events"]) == strip(odd["events"])
      and strip(cur["segments"]) == strip(old["segments"]) == strip(odd["segments"])
      and [e["work_order_no"] for e in cur["events"] if e["kind"] == "wo.open"]
      == [e["work_order_no"] for e in old["events"] if e["kind"] == "wo.open"])
    t("…but world_id follows generator_version (g4 world_id != g3 world_id)",
      cur["world_id"] != old["world_id"] != odd["world_id"]
      and cur["generator_version"] == P.GENERATOR_VERSION)
    other_salt = W.build_world(u, ctx=W.make_context(rng_salt="tendline-synth-selftest-salt"),
                               with_segments=False)
    t("changing WORLD_RNG_SALT re-draws the world (salt is the reset knob)",
      strip(other_salt["events"]) != strip(cur["events"]))


# ---------------------------------------------------------------------------
# 2) prefix stability ＋ per-day independent recompute
# ---------------------------------------------------------------------------

def test_prefix():
    print("[2] prefix stability")
    u0 = _dt.datetime.combine(P.HORIZON_START + _dt.timedelta(days=40), _dt.time(13, 37, 11))
    base = W.build_world(u0)
    fb = E.render(base)
    for label, delta in (("+1 day", 1), ("+7 days", 7)):
        ext = W.build_world(u0 + _dt.timedelta(days=delta))
        fe = E.render(ext)
        ok_files = all(rel in fe and fe[rel].startswith(fb[rel])
                       for rel in fb if rel.endswith(".jsonl"))
        t("%s: every previously exposed JSONL file is a byte prefix of the new one" % label, ok_files)
        t("%s: event list is a prefix (seq and every field unchanged)" % label,
          ext["events"][:len(base["events"])] == base["events"])
        t("%s: segment list is a prefix" % label,
          ext["segments"][:len(base["segments"])] == base["segments"])
        t("%s: new events all later than old exposure" % label,
          all(e["at"] > base["exposed_until"] for e in ext["events"][len(base["events"]):]))
    # 逐日可獨立重算：由 JSON 往返後的 carry 單獨重算某日＝完整跑法該日
    ctx = W.make_context()
    carry = W.initial_carry(ctx)
    per_day = []
    day = ctx.horizon_start
    for _ in range(12):
        snap = json.loads(json.dumps(carry))
        evs, segs, carry = W.generate_day(ctx, day, carry)
        per_day.append((day, snap, evs, segs, W.carry_digest(carry)))
        day += _dt.timedelta(days=1)
    ok = True
    for d, snap, evs, segs, dig in per_day[3:]:
        ctx2 = W.make_context()
        evs2, segs2, carry2 = W.generate_day(ctx2, d, snap)
        ok = ok and evs2 == evs and segs2 == segs and W.carry_digest(carry2) == dig
    t("day d recomputed from serialized carry alone ≡ sequential run (days 4–12)", ok)
    try:
        W.generate_day(ctx, ctx.horizon_start + _dt.timedelta(days=5), per_day[0][1] | {"day": ctx.horizon_start.isoformat()})
        t("carry/day mismatch rejected", False)
    except ValueError:
        t("carry/day mismatch rejected", True)


# ---------------------------------------------------------------------------
# 3) exposure
# ---------------------------------------------------------------------------

def test_exposure():
    print("[3] exposure")
    w = full_world()
    until = C.from_utc_z(w["exposed_until"])
    t("no event at > exposed_until", all(C.from_utc_z(e["at"]) <= until for e in w["events"]))
    t("no segment end > exposed_until", all(_dt_iso(s["end"]) <= until for s in w["segments"]))
    longest = max((_dt_iso(s["end"]) - _dt_iso(s["start"]) for s in w["segments"]), default=None)
    t("Q-H: no single segment longer than 90 minutes", longest is not None
      and longest <= _dt.timedelta(minutes=90), longest)
    blob = b"".join(E.render(w).values())
    t("R-20: no output bytes of the form Board+6 digits (case-insensitive)",
      re.search(rb"(?i)wr\d{6}", blob) is None)
    u = _dt.datetime.combine(P.HORIZON_START + _dt.timedelta(days=20), _dt.time(9, 15, 0))
    w2 = W.build_world(u)
    t("partial-day exposure respected (mid-day until)",
      all(C.from_utc_z(e["at"]) <= u for e in w2["events"])
      and all(_dt_iso(s["end"]) <= u for s in w2["segments"]))
    m = json.loads(E.render(w2)["seed/seed-manifest.json"])
    t("manifest exposed_until == until (UTC Z)", m["exposed_until"] == C.to_utc_z(u))
    # 有未來事件被扣住（證明曝光在做事）
    w3 = W.build_world(u + _dt.timedelta(days=3))
    t("later exposure reveals events hidden before", len(w3["events"]) > len(w2["events"]))


# ---------------------------------------------------------------------------
# 4) lifecycle invariants (§5.4-2)
# ---------------------------------------------------------------------------

def _wo_groups(events):
    g = collections.OrderedDict()
    for e in events:
        if "work_order_no" in e:
            g.setdefault(e["work_order_no"], []).append(e)
    return g


def wo_invariant_violations(events):
    """§5.4-2 ＋ g2 時刻邊界：回傳違規 list（空＝全過）。純讀事件，不看產生器內部。
    每張工單：恰一個 wo.open 且為最早（seq 最小、at 最早）；任何事件 at 不早於開單 at、
    不晚於終態 at；終態後（seq）無事件；from_status 串接；ON_HOLD 帶 hold_reason。"""
    bad = []
    groups = collections.OrderedDict()
    for e in events:
        if "work_order_no" in e:
            groups.setdefault(e["work_order_no"], []).append(e)
    for wo, evs in groups.items():
        opens = [e for e in evs if e["kind"] == "wo.open"]
        if len(opens) != 1:
            bad.append((wo, "n_open", len(opens)))
            continue
        op = opens[0]
        if evs[0] is not op:
            bad.append((wo, "open_not_first_seq", evs[0]["kind"]))
        term = None
        cur = "OPEN"
        for e in evs:
            if e["at"] < op["at"]:
                bad.append((wo, "before_open", e["kind"], e["at"], op["at"]))
            if term is not None:
                bad.append((wo, "after_terminal", e["kind"], e["at"]))
            if e["kind"] == "wo.status":
                if e["from_status"] != cur:
                    bad.append((wo, "chain", cur, e["from_status"]))
                if (e["to_status"] == "ON_HOLD") != (e["hold_reason"] is not None):
                    bad.append((wo, "hold_reason", e["to_status"], e["hold_reason"]))
                cur = e["to_status"]
                if cur in ("CLOSED", "CANCELLED", "VOIDED"):
                    term = e
        if term is not None and any(e["at"] > term["at"] for e in evs):
            bad.append((wo, "after_terminal_at", term["at"]))
    return bad


def test_lifecycle():
    print("[4] WO lifecycle invariants")
    import board_w8 as w8
    import board_eqmeeting as wem
    w = full_world()
    groups = _wo_groups(w["events"])
    one_open = first_is_open = chain_ok = after_term_ok = hold_ok = downtime_ok = True
    status_ok = asset_ok = True
    bad = []
    for wo, evs in groups.items():
        opens = [e for e in evs if e["kind"] == "wo.open"]
        if len(opens) != 1:
            one_open = False
            bad.append(("open", wo))
            continue
        if evs[0]["kind"] != "wo.open" or min(e["seq"] for e in evs) != opens[0]["seq"]:
            first_is_open = False
        wt = opens[0]["work_type"]
        cur = "OPEN"
        terminal_seq = None
        for e in evs:
            if e["asset_id"] != opens[0]["asset_id"]:
                asset_ok = False
            if terminal_seq is not None and e["seq"] > terminal_seq:
                after_term_ok = False
                bad.append(("after_terminal", wo, e["kind"]))
            if e["kind"] != "wo.status":
                continue
            if e["from_status"] != cur:
                chain_ok = False
                bad.append(("chain", wo, cur, e["from_status"]))
            if e["to_status"] not in w8.STATUSES:
                status_ok = False
            if e["to_status"] == "ON_HOLD":
                if e["hold_reason"] not in w8.HOLD_REASONS_KNOWN:
                    hold_ok = False
            elif e["hold_reason"] is not None:
                hold_ok = False
            expect = wem._phase_kind(e["to_status"], e["hold_reason"], wt) == "count"
            if e["to_status"] in ("COMPLETED",) + w8.TERMINAL_STATUSES:
                expect = False
            if e["is_downtime"] is not expect:
                downtime_ok = False
            cur = e["to_status"]
            if cur in w8.TERMINAL_STATUSES:
                terminal_seq = e["seq"]
    t("each WO has exactly one wo.open", one_open, bad[:3])
    t("wo.open is the earliest event of its WO", first_is_open)
    t("every wo.status.from_status == previous status", chain_ok, bad[:3])
    t("no event after a terminal status", after_term_ok, bad[:3])
    t("ON_HOLD always carries a known hold_reason; others null", hold_ok)
    t("is_downtime == board_eqmeeting._phase_kind(...) == 'count'", downtime_ok)
    t("to_status within W8 seven states", status_ok)
    t("all events of a WO share its asset_id", asset_ok)
    t("WOs exist in the 90-day world", len(groups) > 100, len(groups))
    tb = wo_invariant_violations(w["events"])
    t("g2: every WO event at in [wo.open at, terminal at] (scope/note/part/field/status)", not tb, tb[:3])
    # 不重現的病：無空 history 遷移單（每張已結束的單都有 status）、無重複開單（同 asset 窗不重疊）
    windows = _reactive_windows(w)
    by_asset = collections.defaultdict(list)
    for wo, (asset, ws, we, _o) in windows.items():
        by_asset[asset].append((ws, we, wo))
    dup = []
    for asset, ivs in by_asset.items():
        ivs.sort()
        for (a0, a1, n0), (b0, b1, n1) in zip(ivs, ivs[1:]):
            if b0 < a1:
                dup.append((asset, n0, n1))
    t("no duplicate/overlapping REACTIVE WOs on the same asset (Q5: not reproduced)", not dup, dup[:3])


# ---------------------------------------------------------------------------
# 5) MES consistency (§5.4-3; C-SY7)
# ---------------------------------------------------------------------------

def _reactive_windows(w):
    """REACTIVE：[fsa or opened, first pra | first COMPLETED/terminal | until)。"""
    until = C.from_utc_z(w["exposed_until"])
    out = {}
    for wo, evs in _wo_groups(w["events"]).items():
        op = evs[0]
        if op["kind"] != "wo.open" or op["work_type"] != "REACTIVE":
            continue
        start = C.from_utc_z(op["failure_started_at"]) if op["failure_started_at"] else C.from_utc_z(op["at"])
        end = None
        for e in evs:
            if e["kind"] == "wo.field" and e["field"] == "production_resumed_at":
                end = C.from_utc_z(e["value"])
                break
        if end is None:
            for e in evs:
                if e["kind"] == "wo.status" and e["to_status"] in ("COMPLETED", "CLOSED", "CANCELLED", "VOIDED"):
                    end = C.from_utc_z(e["at"])
                    break
        out[wo] = (op["asset_id"], start, end or until, evs)
    return out


class _SegIndex:
    def __init__(self, segments):
        self.by = collections.defaultdict(list)
        for s in segments:
            self.by[(s["eid"], s["unit"])].append((_dt_iso(s["start"]), _dt_iso(s["end"]), s))
        for v in self.by.values():
            v.sort(key=lambda r: r[0])
        self.keys_by_eid = collections.defaultdict(list)
        for k in self.by:
            self.keys_by_eid[k[0]].append(k)

    def rows(self, eid, unit="*"):
        if unit == "*":
            out = []
            for k in self.keys_by_eid.get(eid, ()):
                out.append(k)
            return out
        return [(eid, unit)] if (eid, unit) in self.by else []

    def hits(self, key, ws, we):
        lst = self.by.get(key, ())
        starts = [r[0] for r in lst]
        i = bisect.bisect_left(starts, ws - _dt.timedelta(days=2))
        out = []
        for s, e, rec in lst[i:]:
            if s >= we:
                break
            if (s == e and ws <= s < we) or (s < we and e > ws):
                out.append(rec)
        return out


def _row_keys_for_wo(ctx, idx, asset_id, scope_pieces):
    """回傳 [(key, from_ts)]；scope_pieces＝[(ts, scope)]（形③）。"""
    import board_capacity as wcap
    parent, unit, _known = wcap.fold_seat_id(asset_id)
    if unit is not None:
        return [((parent, unit), None)]
    a = ctx.by_id.get(asset_id)
    keys = []
    if a and a["children"]:
        cur_scope = None
        kids_from = {}
        for ts, scope in scope_pieces:
            if scope:
                for c in scope:
                    kids_from.setdefault(c, ts)
        if not kids_from:
            for c in a["children"]:
                keys += [(k, None) for k in idx.rows(c)]
            keys += [(k, None) for k in idx.rows(asset_id)]
        else:
            for c, ts in sorted(kids_from.items()):
                keys += [(k, ts) for k in idx.rows(c)]
                keys += [((asset_id, c), ts)] if (asset_id, c) in idx.by else []
        return keys
    keys += [(k, None) for k in idx.rows(asset_id)]
    if a:
        for c in a["cotag_children"]:
            keys += [(k, None) for k in idx.rows(c)]
    p = ctx.parent_of.get(asset_id)
    if p and (p, asset_id) in idx.by:
        keys.append(((p, asset_id), None))
    return keys


def _union(ivs):
    out = []
    for s, e in sorted(ivs):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def test_mes_consistency():
    print("[5] MES ↔ WO consistency")
    w = full_world()
    ctx = w["ctx"]
    idx = _SegIndex(w["segments"])
    windows = _reactive_windows(w)
    viol = []
    trial_allowed = collections.defaultdict(list)   # key -> [(s,e)]
    n_checked = 0
    for wo, (asset, ws, we, evs) in windows.items():
        pieces = [(ws, evs[0]["scope"])] + [(C.from_utc_z(e["at"]), e["scope"])
                                            for e in evs if e["kind"] == "wo.scope"]
        for key, from_ts in _row_keys_for_wo(ctx, idx, asset, pieces):
            s0 = max(ws, from_ts) if from_ts else ws
            for rec in idx.hits(key, s0, we):
                if rec["designation"] in P.PRODUCTION_GRADE:
                    viol.append((wo, key, rec["start"]))
            n_checked += 1
        # TEST_RUN 區間
        hs = None
        for e in evs:
            if e["kind"] != "wo.status":
                continue
            if e["to_status"] == "ON_HOLD" and e["hold_reason"] == "TEST_RUN":
                hs = C.from_utc_z(e["at"])
            elif hs is not None:
                he = C.from_utc_z(e["at"])
                for key, _f in _row_keys_for_wo(ctx, idx, asset, pieces):
                    trial_allowed[key].append((hs, he))
                hs = None
        if hs is not None:
            for key, _f in _row_keys_for_wo(ctx, idx, asset, pieces):
                trial_allowed[key].append((hs, C.from_utc_z(w["exposed_until"])))
    t("no production-grade segment inside any REACTIVE [fsa|opened, pra|completed) window",
      not viol, viol[:5])
    t("windows actually checked against segment rows", n_checked > 100, n_checked)
    # 試跑段只出現在 TEST_RUN 暫停期間
    bad_trial = []
    n_trial = 0
    for s in w["segments"]:
        if s["designation"] != P.DESIGNATION_TRIAL:
            continue
        n_trial += 1
        st, en = _dt_iso(s["start"]), _dt_iso(s["end"])
        ok = any(a <= st and en <= b for a, b in trial_allowed.get((s["eid"], s["unit"]), ()))
        if not ok:
            bad_trial.append((s["eid"], s["unit"], s["start"]))
    t("every trial segment lies inside a TEST_RUN hold of a WO covering that row",
      not bad_trial and n_trial > 0, (n_trial, bad_trial[:3]))
    # PM：IN_PROGRESS→COMPLETED 期間無生產級段
    pm_viol = []
    for wo, evs in _wo_groups(w["events"]).items():
        if evs[0]["kind"] != "wo.open" or evs[0]["work_type"] != "PM":
            continue
        ip = next((C.from_utc_z(e["at"]) for e in evs if e["kind"] == "wo.status"
                   and e["to_status"] == "IN_PROGRESS"), None)
        done = next((C.from_utc_z(e["at"]) for e in evs if e["kind"] == "wo.status"
                     and e["to_status"] == "COMPLETED"), None)
        if ip is None:
            continue
        done = done or C.from_utc_z(w["exposed_until"])
        for key, _f in _row_keys_for_wo(ctx, idx, evs[0]["asset_id"], [(ip, [])]):
            for rec in idx.hits(key, ip, done):
                if rec["designation"] in P.PRODUCTION_GRADE:
                    pm_viol.append((wo, key, rec["start"]))
    t("no production-grade segment during PM IN_PROGRESS", not pm_viol, pm_viol[:3])
    # 長停：not_scheduled 期間該資產（或該座）零段
    dorm_viol = []
    n_dorm = 0
    a11 = [e for e in w["events"] if e["kind"] == "asset.schedule_state"]
    for e in a11:
        if e["state"] != "not_scheduled":
            continue
        n_dorm += 1
        f = _dt.datetime.fromisoformat(e["effective_date"])
        back = next((x for x in a11 if x["asset_id"] == e["asset_id"] and x["sub_unit"] == e["sub_unit"]
                     and x["state"] == "in_production" and x["seq"] > e["seq"]), None)
        b = _dt.datetime.fromisoformat(back["effective_date"]) if back else C.from_utc_z(w["exposed_until"])
        if e["sub_unit"]:
            keys = [(e["asset_id"], e["sub_unit"])]
        else:
            keys = idx.rows(e["asset_id"])
            for c in ctx.by_id[e["asset_id"]]["cotag_children"]:
                keys += idx.rows(c)
            p = ctx.parent_of.get(e["asset_id"])
            if p:
                keys += [(p, e["asset_id"])]
        for k in keys:
            for rec in idx.hits(k, f, b):
                dorm_viol.append((e["asset_id"], e["sub_unit"], rec["start"]))
    t("no segment at all while an asset/seat is not_scheduled (A11)", not dorm_viol and n_dorm > 0,
      (n_dorm, dorm_viol[:3]))
    # 形③ 父機段 ≡ 座段的 co-tag（同 stp_seq）
    by_stp = collections.defaultdict(list)
    for s in w["segments"]:
        by_stp[s["stp_seq"]].append(s)
    parent_ok = True
    for s in w["segments"]:
        a = ctx.by_id.get(s["eid"])
        if a and a["children"]:
            src = [x for x in by_stp[s["stp_seq"]] if x["eid"] == s["unit"]]
            if len(src) != 1 or src[0]["start"] != s["start"] or src[0]["end"] != s["end"]:
                parent_ok = False
                break
    t("形③ parent rows are exact co-tags of a seat row (same stp_seq/start/end)", parent_ok)
    union_ok = True
    n_par = 0
    for a in ctx.fleet:
        if not a["children"]:
            continue
        n_par += 1
        pu = _union([(r[0], r[1]) for k in idx.rows(a["asset_id"]) for r in idx.by[k]])
        cu = _union([(r[0], r[1]) for c in a["children"] for k in idx.rows(c) for r in idx.by[k]])
        if pu != cu or not pu:
            union_ok = False
    t("Q-I: every 形③ parent's segment union == union of its seats' segments (convention)",
      union_ok and n_par > 0, n_par)


# ---------------------------------------------------------------------------
# 6) diseases present / absent
# ---------------------------------------------------------------------------

def _disease_census(w):
    ctx = w["ctx"]
    ev = w["events"]
    groups = _wo_groups(ev)
    holds = {e["hold_reason"] for e in ev if e["kind"] == "wo.status" and e["to_status"] == "ON_HOLD"}
    oneclick = {"REACTIVE": 0, "PM": 0}
    for wo, evs in groups.items():
        st = [e for e in evs if e["kind"] == "wo.status"]
        ats = collections.Counter(e["at"] for e in st)
        tos = {e["at"]: [] for e in st}
        for e in st:
            tos[e["at"]].append(e["to_status"])
        for at, lst in tos.items():
            if {"IN_PROGRESS", "COMPLETED", "CLOSED"} <= set(lst):
                oneclick[evs[0]["work_type"]] += 1
    import board_capacity as wcap
    seat_wo = sum(1 for e in ev if e["kind"] == "wo.open" and wcap.fold_seat_id(e["asset_id"])[1])
    scoped = sum(1 for e in ev if e["kind"] == "wo.open" and e["scope"])
    child_wo = sum(1 for e in ev if e["kind"] == "wo.open" and e["asset_id"] in ctx.parent_of)
    scope_ev = sum(1 for e in ev if e["kind"] == "wo.scope")
    a11 = [e for e in ev if e["kind"] == "asset.schedule_state"]
    zero = [s for s in w["segments"] if s["start"] == s["end"]]
    return {"holds": holds, "oneclick": oneclick, "seat_wo": seat_wo, "scoped": scoped,
            "child_wo": child_wo, "scope_ev": scope_ev,
            "a11_not_sched": sum(1 for e in a11 if e["state"] == "not_scheduled"),
            "a11_sub_unit": sum(1 for e in a11 if e["sub_unit"]),
            "zero": len(zero), "zero_cls": {s["cls"] for s in zero}}


def test_diseases():
    print("[6] diseases (Q5) present in the 90-day world, gone when the flag is off")
    import board_w8 as w8
    w = full_world()
    c = _disease_census(w)
    zl = W.default_zero_len_stations()
    t("zero-length segments present, only at the eight zero-length stations",
      c["zero"] > 0 and c["zero_cls"] <= zl, (c["zero"], sorted(c["zero_cls"] - zl)))
    t("every ON_HOLD hold_reason appears", c["holds"] == set(w8.HOLD_REASONS_KNOWN), c["holds"])
    t("one-click close present (REACTIVE and PM)", c["oneclick"]["REACTIVE"] > 0 and c["oneclick"]["PM"] > 0,
      c["oneclick"])
    t("long stop (A11 not_scheduled) present, incl. a seat sub_unit lane",
      c["a11_not_sched"] > 0 and c["a11_sub_unit"] > 0, (c["a11_not_sched"], c["a11_sub_unit"]))
    t("seat-level WOs present (nest -N, 形③ scoped parent, seat's own WO, wo.scope)",
      c["seat_wo"] > 0 and c["scoped"] > 0 and c["child_wo"] > 0 and c["scope_ev"] > 0,
      (c["seat_wo"], c["scoped"], c["child_wo"], c["scope_ev"]))
    until = _full_until()
    off = {}
    for flag in ("on_hold_reasons", "one_click_close", "long_stop", "seat_level_wo"):
        off[flag] = _disease_census(W.build_world(until, flags={flag: False}, with_segments=False))
    t("flag on_hold_reasons off → no ON_HOLD at all", not off["on_hold_reasons"]["holds"])
    t("flag one_click_close off → no one-click close",
      off["one_click_close"]["oneclick"] == {"REACTIVE": 0, "PM": 0})
    t("flag long_stop off → no A11 events", off["long_stop"]["a11_not_sched"] == 0)
    s = off["seat_level_wo"]
    t("flag seat_level_wo off → no seat/scoped/child WOs, no wo.scope, no sub_unit A11",
      s["seat_wo"] == 0 and s["scoped"] == 0 and s["child_wo"] == 0 and s["scope_ev"] == 0
      and s["a11_sub_unit"] == 0)
    u14 = _dt.datetime.combine(P.HORIZON_START + _dt.timedelta(days=14), _dt.time(0, 0))
    on14 = _disease_census(W.build_world(u14))
    off14 = _disease_census(W.build_world(u14, flags={"zero_length_stations": False}))
    t("flag zero_length_stations off → no zero-length segments (14-day world; on>0)",
      on14["zero"] > 0 and off14["zero"] == 0, (on14["zero"], off14["zero"]))
    try:
        W.make_context(flags={"etl_lag": True})
        t("unknown disease flag rejected (ETL lag etc. not reproducible)", False)
    except ValueError:
        t("unknown disease flag rejected (ETL lag etc. not reproducible)", True)


# ---------------------------------------------------------------------------
# 7) zero real data (C-SY1)
# ---------------------------------------------------------------------------

_NEG_SCRIPT = r"""
import builtins, io, os, sys, json, importlib.abc, importlib.machinery, datetime as dt
sys.path.insert(0, os.getcwd())
opened = []
_real_open = builtins.open
def _spy(f, *a, **k):
    opened.append(os.path.abspath(str(f)) if not isinstance(f, int) else '<fd>')
    return _real_open(f, *a, **k)
builtins.open = _spy
io.open = _spy
_real_os_open = os.open
def _spy_os(p, *a, **k):
    opened.append(os.path.abspath(str(p)))
    return _real_os_open(p, *a, **k)
os.open = _spy_os
imported = []
class Rec(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        imported.append(name)
        return None
sys.meta_path.insert(0, Rec())
import wsynth_world as W, wsynth_emit as E, wsynth_params as P
w = W.build_world(dt.datetime.combine(P.HORIZON_START + dt.timedelta(days=10), dt.time(0, 0)))
out = E.emit(w, sys.argv[1])
print('JSON=' + json.dumps({'opened': opened, 'imported': imported, 'modules': sorted(sys.modules), 'out': out}))
"""

FORBIDDEN_MODULES = ("pyodbc", "board_data", "app", "core_lastprod_push", "core_prod_calendar",
                     "board_nest", "board_manpower", "board_layout_regions", "_gen_layout",
                     "board_kundispatch", "board_w8_transport", "mes_family", "mes_hermes",
                     "mes_material", "_wo_ledger", "board_assistant", "board_mcp")
FORBIDDEN_PATH_PARTS = ("_fixtures", "_state", "snapshot", "_KUN-", "layout_eid_xy",
                        "_kb_a", "_kb_b", "_ENV-SENSOR", "_PROD-CALENDAR",
                        "_SALTMARSH-", ".sqlite")


def test_zero_real_data():
    print("[7] zero real data (negative assertions)")
    tmp = tempfile.mkdtemp(prefix="wsynth-neg-")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", _NEG_SCRIPT, tmp], cwd=REPO, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    m = re.search(r"JSON=(\{.*\})", r.stdout)
    if not t("instrumented subprocess ran", m is not None, r.stderr[-600:]):
        return
    d = json.loads(m.group(1))
    opened = d["opened"]
    bad = [p for p in opened if any(part.lower() in p.lower() for part in FORBIDDEN_PATH_PARTS)
           and not p.lower().startswith(os.path.abspath(tmp).lower())]
    t("no forbidden path opened (_fixtures/_state/snapshot/_KUN-*/layout/KB/…)", not bad, bad[:5])
    outside = [p for p in opened if not os.path.abspath(p).lower().startswith(os.path.abspath(tmp).lower())]
    t("the only files opened are the emitter's own outputs in the temp dir", not outside, outside[:5])
    loaded = set(d["modules"])
    bad_mod = sorted(m for m in FORBIDDEN_MODULES if m in loaded)
    t("no forbidden module loaded (pyodbc, board_data, app, probes, transports…)", not bad_mod, bad_mod)
    t("emit refuses to write inside the repo", _refuses_repo())
    # PRODUCT_TAGS：只讀 stations 一欄
    src = W._roster_sources()
    touched = collections.Counter()

    class Guard(dict):
        def __getitem__(self, k):
            touched[k] += 1
            if k != "stations":
                raise KeyError("forbidden field read: %s" % k)
            return dict.__getitem__(self, k)

        def get(self, k, default=None):
            return self[k] if k in self else default

    src2 = dict(src)
    src2["product_tags"] = {e: Guard(v) for e, v in src["product_tags"].items()}
    try:
        f2 = W.fleet_from_roster(src2)
        ok = f2 == W.fleet_from_roster(src) and set(touched) == {"stations"}
    except KeyError as exc:
        ok = False
        touched["ERR:" + str(exc)] += 1
    t("fleet reads only the `stations` field of PRODUCT_TAGS (no counts/families/dates)", ok, dict(touched))


def _refuses_repo():
    w = W.build_world(_dt.datetime.combine(P.HORIZON_START, _dt.time(1, 0)), with_segments=False)
    try:
        E.emit(w, os.path.join(REPO, "_synth_out_should_not_exist"))
        return False
    except E.EmitRefused:
        return not os.path.exists(os.path.join(REPO, "_synth_out_should_not_exist"))


# ---------------------------------------------------------------------------
# 8) source scan
# ---------------------------------------------------------------------------

def test_source_scan():
    print("[8] own source contains no station / machine name / 5-digit EID literal")
    import mes_timeline as mt
    import board_attrib as wattr
    import board_capacity as wcap
    import board_eqmeeting as wem
    import _probe_kun_activity as pka
    stations = set(mt.EQP_STATIONS) | set(wattr.ZERO_LEN_STATIONS) | set(mt.CLASS_ALIASES)
    for al in mt.CLASS_ALIASES.values():
        stations |= set(al)
    stations |= {v["main_cls"] for v in wcap.NEST_EAV_UNITS.values()} | set(wcap.COTAG_MAIN_CLS.values())
    import board_tags as wtags
    names = {n for _a, n, _e in wem.EQMT_CATALOG} | {n for _k, _a, n, _e in pka.KUN_ROSTER}         | set(wtags.FAMILIES)
    eid_digits = {e[4:] for _a, _n, e in wem.EQMT_CATALOG}
    hits = []
    for fn in OWN_FILES:
        with open(os.path.join(REPO, fn), encoding="utf-8") as fh:
            src = fh.read()
        for m in re.finditer(r"EID-\d{5}", src):
            hits.append((fn, "EID literal", m.group(0)))
        for m in re.finditer(r"(?<![\w.])\d{5}(?![\w])", src):
            hits.append((fn, "5-digit literal", m.group(0)))
        for s in stations:
            if s in src:
                hits.append((fn, "station", s))
        for n in names:
            if re.search(r"(?<![\w])%s(?![\w])" % re.escape(n), src):
                hits.append((fn, "machine/kun name", n))
        for dgt in eid_digits:
            if re.search(r"(?<!\d)%s(?!\d)" % dgt, src):
                hits.append((fn, "EID digits", dgt))
    t("no station names, machine/cluster/product-family names, EID literals or 5-digit numbers",
      not hits, hits[:8])
    # 只掃程式碼 token（docstring／註解裡「禁 hash()、禁 now()」的說明文字不算）
    import io
    import tokenize
    with open(os.path.join(REPO, "wsynth_world.py"), encoding="utf-8") as fh:
        toks = tokenize.generate_tokens(io.StringIO(fh.read()).readline)
        wsrc = " ".join(tok.string for tok in toks
                        if tok.type not in (tokenize.STRING, tokenize.COMMENT)
                        and not (hasattr(tokenize, "FSTRING_MIDDLE") and tok.type == tokenize.FSTRING_MIDDLE))
        wsrc = re.sub(r"\s*([.(])\s*", r"\1", wsrc)
    bad = [p for p in (r"datetime\.now", r"\.now\(", r"time\.time\(", r"import random",
                       r"(?<![\w.])hash\(", r"(?<![\w.])open\(")
           if re.search(p, wsrc)]
    t("generator source has no now()/time()/random/hash()/open()", not bad, bad)


# ---------------------------------------------------------------------------
# 9) ranges
# ---------------------------------------------------------------------------

def test_ranges():
    print("[9] synthetic identifiers inside the declared ranges")
    w = full_world()
    ctx = w["ctx"]
    wos = sorted({e["work_order_no"] for e in w["events"] if e["kind"] == "wo.open"})
    t("WO numbers are %d-digit ints in [%d, %d]" % (P.WO_NO_WIDTH, P.WO_NO_FIRST, P.WO_NO_LAST),
      all(isinstance(n, int) and P.WO_NO_FIRST <= n <= P.WO_NO_LAST and len(str(n)) == P.WO_NO_WIDTH
          for n in wos))
    t("WO numbers consecutive from the first (no gaps, no reuse)",
      wos == list(range(P.WO_NO_FIRST, P.WO_NO_FIRST + len(wos))))
    t("first WO number is 710001 by construction (Q-N)", wos and wos[0] == P.WO_NO_FIRST == 71 * 10 ** 4 + 1)
    ipat = re.compile(r"^%s(\d{%d})$" % (re.escape(P.ITEM_CODE_PREFIX), P.ITEM_CODE_WIDTH))
    item_bounds_ok = (W.item_code(0) == "TL900001" and P.ITEM_CODE_LAST == 999999)
    try:
        W.item_code(P.ITEM_CODE_LAST - P.ITEM_CODE_FIRST + 1)
        item_bounds_ok = False
    except OverflowError:
        pass
    lot_bounds_ok = W.lot_of(1) == "S000001" and W.lot_of(P.SERIAL_LAST) == "S0ZZZZZ"
    try:
        W.lot_of(P.SERIAL_LAST + 1)
        lot_bounds_ok = False
    except OverflowError:
        pass
    items = [e["item_code"] for e in w["events"] if e["kind"] == "wo.part"]
    t("item codes match TL+6 digits inside TL900001–TL999999 (Q-N)",
      items and all(ipat.match(i) and P.ITEM_CODE_FIRST <= int(ipat.match(i).group(1))
                    <= min(P.ITEM_CODE_LAST, P.ITEM_CODE_FIRST + P.ITEM_CODE_POOL - 1) for i in items)
      and item_bounds_ok)
    lpat = re.compile(r"^S0[0-9A-Z]{5}$")
    t("lots are serial-equivalent S000001–S0ZZZZZ (Q-N) and equal f(stp_seq)",
      all(lpat.match(s["lot"]) and "S000001" <= s["lot"] <= "S0ZZZZZ"
          and s["lot"] == W.lot_of(s["stp_seq"]) for s in w["segments"]))
    t("lot/serial bounds: first=S000001, last=S0ZZZZZ, overflow raises", lot_bounds_ok)
    import board_capacity as wcap
    fleet_ids = set(ctx.by_id)
    ev_assets = {e["asset_id"] for e in w["events"] if e["kind"] != "inventory.item"}
    ok = all(a in fleet_ids or (wcap.fold_seat_id(a)[1] and wcap.fold_seat_id(a)[0] in fleet_ids)
             for a in ev_assets)
    t("every event asset_id (inventory.item excepted) is a fleet EID or a registered seat of one "
      "(Q2: no invented EIDs)", ok)
    inv = [e for e in w["events"] if e["kind"] == "inventory.item"]
    t("seed v1.1: inventory.item asset_id is the item-master sentinel (all-zero, not a fleet EID, "
      "not a seat), the pool is exactly item_code(0..POOL-1)",
      inv and all(e["asset_id"] == P.ITEM_ASSET_ID for e in inv)
      and P.ITEM_ASSET_ID not in fleet_ids and P.ITEM_ASSET_ID not in ev_assets
      and set(P.ITEM_ASSET_ID[4:]) == {"0"}
      and [e["item_code"] for e in inv] == [W.item_code(k) for k in range(P.ITEM_CODE_POOL)])
    seg_ids = {s["eid"] for s in w["segments"]}
    cot = {c for a in ctx.fleet for c in a["cotag_children"]}
    t("every segment eid is a fleet EID or a declared co-tag child", seg_ids <= fleet_ids | cot,
      sorted(seg_ids - fleet_ids - cot)[:5])


# ---------------------------------------------------------------------------
# 10) schema
# ---------------------------------------------------------------------------

COMMON = {"schema", "world_id", "seq", "at", "kind", "asset_id", "actor"}
KIND_KEYS = {
    "asset.define": set(W.DEFINE_KEYS),
    "wo.open": {"work_order_no", "work_type", "brief_description", "failure_started_at", "scope",
                "sub_units", "pm_source_id"},
    "wo.status": {"work_order_no", "from_status", "to_status", "hold_reason", "is_downtime"},
    "wo.scope": {"work_order_no", "scope", "sub_units"},
    "wo.note": {"work_order_no", "entry_type", "body"},
    "wo.part": {"work_order_no", "item_code", "quantity"},
    "wo.field": {"work_order_no", "field", "value"},
    "asset.schedule_state": {"state", "effective_date", "sub_unit", "reason"},
    "inventory.item": {"item_code", "display_name"},          # seed v1.1
}
SEG_KEYS = {"schema", "world_id", "eid", "unit", "cls", "start", "end", "designation", "result",
            "lot", "stp_seq", "operator_hash"}


def test_schema():
    print("[10] event / segment field types and closed sets (§5.2 §5.3 §5.4-6)")
    import mes_timeline as mt
    import board_w8 as w8
    w = full_world()
    errs = []
    prev = 0
    for e in w["events"]:
        k = e.get("kind")
        if k not in P.KINDS_EMITTED_PHASE1:
            errs.append(("kind", k))
            continue
        if set(e) != COMMON | KIND_KEYS[k]:
            errs.append(("keys", k, sorted(set(e) ^ (COMMON | KIND_KEYS[k]))))
        if e["schema"] != P.SEED_SCHEMA or e["world_id"] != w["world_id"]:
            errs.append(("schema/world", e["seq"]))
        if not isinstance(e["seq"], int) or e["seq"] != prev + 1:
            errs.append(("seq", e["seq"]))
        prev = e["seq"]
        if not C.is_utc_z(e["at"]):
            errs.append(("at", e["at"]))
        if e["actor"] not in P.ROLES:
            errs.append(("actor", e["actor"]))
        if not isinstance(e["asset_id"], str):
            errs.append(("asset_id", e["seq"]))
        if k.startswith("wo.") and (not isinstance(e["work_order_no"], int) or isinstance(e["work_order_no"], bool)):
            errs.append(("wo_no", e["seq"]))
        if k == "wo.open":
            if e["work_type"] not in ("PM", "REACTIVE"):
                errs.append(("work_type", e["work_type"]))
            if e["failure_started_at"] is not None and not C.is_utc_z(e["failure_started_at"]):
                errs.append(("fsa", e["seq"]))
            if e["failure_started_at"] is not None and e["failure_started_at"] >= e["at"]:
                errs.append(("fsa<opened", e["seq"]))
            if not (isinstance(e["scope"], list) and all(isinstance(x, str) for x in e["scope"])):
                errs.append(("scope", e["seq"]))
            if not (isinstance(e["sub_units"], list)):
                errs.append(("sub_units", e["seq"]))
            if not isinstance(e["brief_description"], str) or not e["brief_description"]:
                errs.append(("brief", e["seq"]))
        elif k == "wo.status":
            if e["to_status"] not in w8.STATUSES or e["from_status"] not in w8.STATUSES:
                errs.append(("status", e["seq"]))
            if not isinstance(e["is_downtime"], bool):
                errs.append(("is_downtime", e["seq"]))
        elif k == "wo.note":
            if e["entry_type"] not in w8.NOTE_ENTRY_TYPES_KNOWN or not isinstance(e["body"], str):
                errs.append(("note", e["seq"]))
        elif k == "wo.part":
            if not isinstance(e["quantity"], int) or e["quantity"] < 1:
                errs.append(("qty", e["seq"]))
        elif k == "wo.field":
            if e["field"] not in P.WO_FIELDS or not isinstance(e["value"], str):
                errs.append(("field", e["seq"]))
            if e["field"] == "production_resumed_at" and not C.is_utc_z(e["value"]):
                errs.append(("pra", e["seq"]))
        elif k == "asset.schedule_state":
            if e["state"] not in ("not_scheduled", "in_production"):
                errs.append(("state", e["seq"]))
            try:
                _dt.date.fromisoformat(e["effective_date"])
            except (TypeError, ValueError):
                errs.append(("eff_date", e["seq"]))
            if e["sub_unit"] is not None and not isinstance(e["sub_unit"], str):
                errs.append(("sub_unit", e["seq"]))
        elif k == "asset.define":
            if not set(e["roles"]) <= set(P.ASSET_ROLES) or not isinstance(e["seats"], list):
                errs.append(("define", e["asset_id"]))
            if e["station_cls"] is not None and e["station_cls"] not in mt.EQP_STATIONS:
                errs.append(("station_cls", e["asset_id"]))
        elif k == "inventory.item":
            if e["asset_id"] != P.ITEM_ASSET_ID or e["actor"] != "role:system" \
                    or not isinstance(e["display_name"], str) or not e["display_name"].strip() \
                    or not re.match(r"^%s\d{%d}$" % (re.escape(P.ITEM_CODE_PREFIX), P.ITEM_CODE_WIDTH),
                                    e["item_code"]):
                errs.append(("inventory.item", e["seq"]))
    t("every event: exact key set, types, closed sets; seq contiguous from 1", not errs, errs[:6])
    serr = []
    for s in w["segments"][:]:
        if set(s) != SEG_KEYS:
            serr.append(("keys", sorted(set(s) ^ SEG_KEYS)))
            break
        try:
            a, b = _dt_iso(s["start"]), _dt_iso(s["end"])
        except ValueError:
            serr.append(("time", s["start"]))
            continue
        if a > b or s["designation"] not in P.DESIGNATIONS or s["result"] not in P.RESULTS \
                or s["cls"] not in mt.EQP_STATIONS or not isinstance(s["stp_seq"], int) \
                or s["schema"] != P.MES_SCHEMA or s["world_id"] != w["world_id"] \
                or not (s["unit"] is None or isinstance(s["unit"], str)):
            serr.append(("seg", s["eid"], s["start"]))
    t("every segment: exact key set, Taipei-naive times, start<=end, closed sets", not serr, serr[:5])
    m = json.loads(E.render(w)["seed/seed-manifest.json"])
    t("seed manifest fields (§5.1)",
      m["schema"] == P.SEED_MANIFEST_SCHEMA and m["world_id"] == w["world_id"]
      and m["generator_version"] == P.GENERATOR_VERSION and len(m["world_id"]) == 16
      and C.is_utc_z(m["horizon_start"]) and C.is_utc_z(m["exposed_until"])
      and all(set(d) == {"day", "file", "n_events", "sha256"} for d in m["days"])
      and sum(d["n_events"] for d in m["days"]) == len(w["events"]))
    t("world_id == blake2b(seed‖version‖fleet_digest)[:16]",
      w["world_id"] == W.world_id_of(P.DEFAULT_SEED, P.GENERATOR_VERSION, w["fleet_digest"]))


# ---------------------------------------------------------------------------
# 11) clock
# ---------------------------------------------------------------------------

def test_clock():
    print("[11] clock")
    env = {C.ENV_FREEZE: "2026-09-29T14:30:05"}
    t("frozen env var → now() == frozen value", C.now(env) == _dt.datetime(2026, 9, 29, 14, 30, 5))
    t("is_frozen reflects env", C.is_frozen(env) and not C.is_frozen({}))
    for bad in ("2026-09-29T14:30:05Z", "2026-09-29T14:30:05+08:00", "nonsense", " "):
        try:
            C.now({C.ENV_FREEZE: bad})
            ok = bad.strip() == "" and False
        except C.ClockError:
            ok = True
        t("frozen value %r rejected loudly" % bad, ok)
    a = _dt.datetime.now()
    b = C.now({})
    t("unfrozen now() tracks datetime.now() (same source)", abs((b - a).total_seconds()) < 2)
    for off in (_dt.timedelta(0), _dt.timedelta(hours=9), _dt.timedelta(hours=-8)):
        try:
            C.check_utc_offset(off)
            t("UTC offset %s rejected" % off, False)
        except C.ClockOffsetError:
            t("UTC offset %s rejected" % off, True)
    t("UTC offset +8 accepted", C.check_utc_offset(_dt.timedelta(hours=8)) == _dt.timedelta(hours=8))
    try:
        C.check_utc_offset()
        host_ok = True
    except C.ClockOffsetError as exc:
        host_ok = False
        print("      host:", exc)
    t("this host's real UTC offset is +8", host_ok, C.local_utc_offset())
    d = _dt.datetime(2026, 9, 29, 7, 59, 59)
    t("to_utc_z / from_utc_z round-trip, second precision",
      C.to_utc_z(d) == "2026-09-28T23:59:59Z" and C.from_utc_z(C.to_utc_z(d)) == d
      and C.to_utc_z(d.replace(microsecond=999)) == "2026-09-28T23:59:59Z")
    try:
        C.to_utc_z(d.replace(tzinfo=_dt.timezone.utc))
        t("aware datetime rejected by to_utc_z", False)
    except ValueError:
        t("aware datetime rejected by to_utc_z", True)
    w = full_world()
    m = json.loads(E.render(w)["seed/seed-manifest.json"])
    by_day = all(C.from_utc_z(json.loads(line)["at"]).date().isoformat() == dd["day"]
                 for dd in m["days"][:5]
                 for line in E.render(w)["seed/" + dd["file"]].decode("utf-8").splitlines())
    t("seed files split at Taipei 00:00 (Q16)", by_day)


# ---------------------------------------------------------------------------
# 12) sanity
# ---------------------------------------------------------------------------

def test_sanity():
    print("[12] world sanity")
    import board_capacity as wcap
    import board_scope as wscope
    w = full_world()
    ctx = w["ctx"]
    rows = collections.defaultdict(list)
    for s in w["segments"]:
        rows[(s["eid"], s["unit"])].append((s["start"], s["end"]))
    ov = []
    for k, lst in rows.items():
        lst.sort()
        for (a0, a1), (b0, b1) in zip(lst, lst[1:]):
            if b0 < a1:
                ov.append((k, a0, b0))
                break
    t("per machine (and per seat unit) segments never overlap", not ov, ov[:3])
    t("no segment crosses Taipei midnight (end may equal the next 00:00:00)",
      all(s["start"][:10] == s["end"][:10] or s["end"][11:] == "00:00:00"
          and _dt_iso(s["end"]) - _dt_iso(s["start"][:10] + "T00:00:00") == _dt.timedelta(days=1)
          for s in w["segments"]))
    # g3（WP-6b）：段（含零工期段）永不恰好結束在生產日界 08:00:00；且夜班窗確實存在（防空泛通過）
    bd = "%02d:00:00" % P.PROD_DAY_START_H
    at_bd = [(s["eid"], s["unit"], s["start"], s["end"]) for s in w["segments"] if s["end"][11:] == bd]
    near = sum(1 for s in w["segments"] if s["end"][11:] == "%02d:59:59" % (P.PROD_DAY_START_H - 1))
    t("g3: no segment (zero-length included) ends exactly on the %s production-day boundary; "
      "night-shift tails truncated 1s earlier (%d segments end at %02d:59:59)" % (bd, near, P.PROD_DAY_START_H - 1),
      not at_bd and near > 0, at_bd[:3])
    _bd0 = _dt.datetime.combine(P.HORIZON_START, _dt.time(P.PROD_DAY_START_H))
    _one = _dt.timedelta(seconds=1)
    t("g3: off_day_boundary — tail on the boundary moves 1s earlier, a zero-length point on it moves 1s later, "
      "anything else unchanged",
      W.off_day_boundary(_bd0 - _dt.timedelta(minutes=30), _bd0) == (_bd0 - _dt.timedelta(minutes=30), _bd0 - _one)
      and W.off_day_boundary(_bd0, _bd0) == (_bd0 + _one, _bd0 + _one)
      and W.off_day_boundary(_bd0, _bd0 + _dt.timedelta(minutes=5)) == (_bd0, _bd0 + _dt.timedelta(minutes=5))
      and W.off_day_boundary(_bd0 + _one, _bd0 + _one) == (_bd0 + _one, _bd0 + _one))
    seat_bad = []
    for e in w["events"]:
        if e["kind"] == "wo.open":
            p, unit, known = wcap.fold_seat_id(e["asset_id"])
            if unit is not None and not known:
                seat_bad.append(("unknown seat", e["asset_id"]))
            if e["scope"]:
                if e["asset_id"] not in wscope.PARENT_EXPANSION or \
                        not set(e["scope"]) <= set(wscope.PARENT_EXPANSION[e["asset_id"]]):
                    seat_bad.append(("scope", e["asset_id"]))
        if e["kind"] == "wo.scope":
            if e["asset_id"] not in wscope.PARENT_EXPANSION or \
                    not set(e["scope"]) <= set(wscope.PARENT_EXPANSION[e["asset_id"]]):
                seat_bad.append(("wo.scope", e["asset_id"]))
        if e["kind"] == "asset.schedule_state" and e["sub_unit"]:
            if e["sub_unit"] not in wcap.installed_units(e["asset_id"]):
                seat_bad.append(("sub_unit", e["asset_id"], e["sub_unit"]))
    seg_units_bad = [s for s in w["segments"] if s["unit"] and not (
        s["unit"] in wcap.installed_units(s["eid"]))]
    t("seat-level events only on machines that have that seat", not seat_bad, seat_bad[:3])
    t("segment unit always a registered seat of its eid", not seg_units_bad,
      [(s["eid"], s["unit"]) for s in seg_units_bad[:3]])
    defs = {e["asset_id"]: e for e in w["events"] if e["kind"] == "asset.define"}
    rel_bad = []
    inv = {c: p for p, cs in wscope.PARENT_EXPANSION.items() for c in cs}
    inv.update({c: p for p, cs in wcap.COTAG_CHILD_UNITS.items() for c in cs})
    for eid, d in defs.items():
        if d["parent_asset_id"] != inv.get(eid):
            rel_bad.append(("parent", eid))
        units = tuple(s["unit"] for s in d["seats"])
        if units != tuple(wcap.installed_units(eid)):
            rel_bad.append(("seats", eid, units))
        for s in d["seats"]:
            if s["unit"].startswith("nest:") and s["asset_id"] != wcap.seat_wire_id(eid, s["unit"]):
                rel_bad.append(("seat wire id", eid))
    t("asset.define parent/seat relations == roster (PARENT_EXPANSION, installed_units)", not rel_bad, rel_bad[:3])
    t("every fleet machine defined exactly once, all at horizon start",
      len(defs) == len(ctx.fleet) == sum(1 for e in w["events"] if e["kind"] == "asset.define")
      and all(e["at"] == w["horizon_start"] for e in defs.values()))
    t("asset.define events come first (seq 1..N)",
      [e["kind"] for e in w["events"][:len(defs)]] == ["asset.define"] * len(defs))
    nd, npool = len(defs), P.ITEM_CODE_POOL
    first_item = {}
    for e in w["events"]:
        if e["kind"] == "inventory.item":
            first_item.setdefault(e["item_code"], e["seq"])
    late = [e["seq"] for e in w["events"] if e["kind"] == "wo.part"
            and not first_item.get(e["item_code"], 10 ** 9) < e["seq"]]
    t("seed v1.1: inventory.item = the next %d events (seq N+1..N+POOL) at horizon start; every wo.part "
      "cites an item defined earlier; no item defined twice" % npool,
      [e["kind"] for e in w["events"][nd:nd + npool]] == ["inventory.item"] * npool
      and all(e["at"] == w["horizon_start"] for e in w["events"][nd:nd + npool])
      and sum(1 for e in w["events"] if e["kind"] == "inventory.item") == npool == len(first_item)
      and not late, late[:3])


_A11_SCRIPT = r"""
import sys, os, json
sys.path.insert(0, os.getcwd())
import board_data as wd
print('A11=' + json.dumps(list(wd.a11_eids())))
"""


def test_roster_coverage():
    print("[15] fleet covers the long-stop roster (board_data.a11_eids) + reverse check")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 另開進程 import board_data（本進程與產生器都不載入它，C-SY1 負斷言不受影響）
    r = subprocess.run([sys.executable, "-c", _A11_SCRIPT], cwd=REPO, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    m = re.search(r"A11=(\[.*\])", r.stdout)
    if not t("a11_eids() readable in a subprocess", m is not None, r.stderr[-400:]):
        return
    a11 = json.loads(m.group(1))
    w = full_world()
    defs = {e["asset_id"] for e in w["events"] if e["kind"] == "asset.define"}
    missing = [e for e in a11 if e not in defs]
    t("every long-stop roster asset (%d) has an asset.define in the world" % len(a11), not missing, missing)
    import mes_timeline as mt
    import board_capacity as wcap
    import board_dormant as wdorm
    import board_eqmeeting as wem
    import board_scope as wscope
    import _probe_kun_activity as pka
    rosters = {e for _a, _n, e in wem.EQMT_CATALOG}
    rosters |= set(wscope.PARENT_EXPANSION) | {c for cs in wscope.PARENT_EXPANSION.values() for c in cs}
    rosters |= set(wcap.NEST_EAV_UNITS) | set(wcap.COTAG_CHILD_UNITS)
    rosters |= {c for cs in wcap.COTAG_CHILD_UNITS.values() for c in cs}
    rosters |= set(wdorm.A11_BASELINE_EIDS) | {e for _k, _a, _n, es in pka.KUN_ROSTER for e in es}
    orphan = sorted(defs - rosters)
    print("      reverse check: fleet assets not in any roster constant:", orphan or "none")
    t("reverse: every fleet asset appears in some roster constant", not orphan, orphan)
    passive = [a["asset_id"] for a in w["ctx"].fleet if a.get("passive")]
    print("      passive (define-only) assets:", len(passive))
    ev_assets = {e["asset_id"] for e in w["events"] if e["kind"] != "asset.define"}
    t("passive assets carry no WO / A11 events", not (set(passive) & ev_assets))


# ---------------------------------------------------------------------------
# 13) multi-seed（g2；主持人 2026-09-29 追加）
#     預設跑 20 個 seed；`--slow` 跑 200 個（>3 分鐘的慢測試以旗標開）。
# ---------------------------------------------------------------------------

MULTISEED_N_DEFAULT = 20
MULTISEED_N_SLOW = 200
MULTISEED_DAYS = 30


def _census_counts(w):
    ev = w["events"]
    c = collections.Counter()
    for e in ev:
        c["kind:" + e["kind"]] += 1
        if e["kind"] == "wo.status" and e["to_status"] == "ON_HOLD":
            c["hold:" + e["hold_reason"]] += 1
        if e["kind"] == "wo.open":
            c["wo:" + e["work_type"]] += 1
    d = _disease_census(w)
    sub = sum(1 for e in ev if e["kind"] == "asset.schedule_state"
              and e["state"] == "not_scheduled" and e["sub_unit"])
    c["disease:zero_length_segment"] += d["zero"]
    c["disease:one_click_REACTIVE"] += d["oneclick"]["REACTIVE"]
    c["disease:one_click_PM"] += d["oneclick"]["PM"]
    c["disease:long_stop_machine"] += d["a11_not_sched"] - sub
    c["disease:long_stop_seat_sub_unit"] += sub
    c["disease:seat_wo_nest_N"] += d["seat_wo"]
    c["disease:seat_wo_parent_scoped"] += d["scoped"]
    c["disease:seat_wo_own_child"] += d["child_wo"]
    c["disease:scope_added_midway"] += d["scope_ev"]
    return c


def _validate_with_seed_module(w):
    import wsynth_seed as S
    files = E.render(w)
    man = json.loads(files["seed/seed-manifest.json"].decode("utf-8"))
    evs = []
    for rel in sorted(files):
        if rel.startswith("seed/seed-") and rel.endswith(".jsonl"):
            evs.extend(S.split_day_bytes(files[rel]))
    return S.validate_ledger(evs, manifest=man, complete=True, strict=True)


MULTISEED_EXPECTED = (
    ["kind:" + k for k in P.KINDS_EMITTED_PHASE1]
    + ["hold:" + h for h in ("WAITING_MACHINE_TIME", "TEST_RUN", "WAITING_PARTS", "WAITING_VENDOR", "OTHER")]
    + ["wo:PM", "wo:REACTIVE", "disease:zero_length_segment", "disease:one_click_REACTIVE",
       "disease:one_click_PM", "disease:long_stop_machine", "disease:long_stop_seat_sub_unit",
       "disease:seat_wo_nest_N", "disease:seat_wo_parent_scoped", "disease:seat_wo_own_child",
       "disease:scope_added_midway"])


def test_multiseed(n=None):
    n = n or MULTISEED_N_DEFAULT
    print("[13] multi-seed: %d seeds x %d days - invariants + wsynth_seed strict validator + census"
          % (n, MULTISEED_DAYS))
    until = _dt.datetime.combine(P.HORIZON_START + _dt.timedelta(days=MULTISEED_DAYS), _dt.time(0, 0))
    total = collections.Counter()
    seeds_with = collections.Counter()
    inv_bad = []
    val_bad = []
    per_seed_scope = []
    t0 = time.time()
    for i in range(n):
        seed = "multiseed-%03d" % i
        w = W.build_world(until, seed)
        v = wo_invariant_violations(w["events"])
        if v:
            inv_bad.append((seed, v[:2]))
        rep = _validate_with_seed_module(w)
        if not rep["ok"] or rep["errors"]:
            val_bad.append((seed, rep["codes"], rep["errors"][:2]))
        c = _census_counts(w)
        per_seed_scope.append(c["disease:scope_added_midway"])
        total.update(c)
        seeds_with.update(k for k, v2 in c.items() if v2 > 0)
    secs = time.time() - t0
    t("all %d seeds: WO invariants incl. at in [open, terminal]" % n, not inv_bad, inv_bad[:3])
    t("all %d seeds: wsynth_seed.validate_ledger(strict, complete) zero errors" % n, not val_bad, val_bad[:2])
    zero = [k for k in MULTISEED_EXPECTED if total[k] == 0]
    print("    census over %d worlds (%.1fs): total / worlds-with" % (n, secs))
    for k in MULTISEED_EXPECTED:
        print("      %-36s %8d  %4d" % (k, total[k], seeds_with[k]))
    print("      scope_added_midway per 30-day world: min %d / max %d / mean %.2f"
          % (min(per_seed_scope), max(per_seed_scope), sum(per_seed_scope) / len(per_seed_scope)))
    t("every event kind and every reproduced characteristic appears at least once", not zero, zero)
    _CACHE["multiseed_secs"] = secs


def test_scope_rate():
    print("[14] mid-WO scope additions (g2: >= 10 per 90-day world)")
    w = full_world()
    n = sum(1 for e in w["events"] if e["kind"] == "wo.scope")
    t("default-seed 90-day world has >= 10 wo.scope (mid-WO scope additions)", n >= 10, n)
    counts = []
    for i in range(5):
        wi = W.build_world(_full_until(), "scope-rate-%d" % i, with_segments=False)
        counts.append(sum(1 for e in wi["events"] if e["kind"] == "wo.scope"))
    print("      other seeds' 90-day wo.scope counts:", counts)
    t("5 other seeds' 90-day worlds each have >= 10 wo.scope", all(c >= 10 for c in counts), counts)


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# emit CLI 定版（SPEC-SYNTHLINE §5.6 呼叫介面；退出碼 0／2／3）
# ---------------------------------------------------------------------------

def _tree_bytes(root):
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root).replace(os.sep, "/")] = fh.read()
    return out


def _run_cli(argv):
    import contextlib
    import io
    so, se = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        try:
            rc = E.main(argv)
        except SystemExit as exc:          # argparse 的 -h／參數錯
            rc = exc.code
    return rc, so.getvalue(), se.getvalue()


def test_emit_cli():
    import shutil
    print("[13] emit CLI: --seed-only, UTC Z --until, --json, exit codes, validate-before-swap")
    base = tempfile.mkdtemp(prefix="wsynth-cli-")
    try:
        z, nv = "2026-07-03T06:45:00Z", "2026-07-03T14:45:00"
        a, b, c, d = (os.path.join(base, x) for x in "abcd")
        rc_a, out_a, err_a = _run_cli(["--out", a, "--until", z, "--json"])
        rc_b, out_b, _e = _run_cli(["--out", b, "--until", nv])
        rc_c, _o, _e = _run_cli(["--out", c, "--until", z, "--seed-only"])
        rc_d, _o, _e = _run_cli(["--out", d, "--until", z])
        t("full output exits 0 for UTC Z and Taipei naive forms", rc_a == 0 and rc_b == 0, (rc_a, rc_b))
        ta, tb, tc, td = (_tree_bytes(x) for x in (a, b, c, d))
        t("UTC Z and Taipei naive --until give bit-identical output (seed and mes)",
          ta == tb and len(ta) > 2)
        t("same arguments twice give bit-identical output", ta == td)
        t("--seed-only writes only seed/ (no mes/ directory)",
          rc_c == 0 and not os.path.exists(os.path.join(c, "mes")) and tc and all(k.startswith("seed/") for k in tc))
        t("--seed-only seed/ is bit-identical to the full output's seed/",
          tc == {k: v for k, v in ta.items() if k.startswith("seed/")})
        t("no staging directory left behind", not [n for x in (a, b, c, d) for n in os.listdir(x) if n.startswith(".emit-stage-")])
        # --json
        lines = out_a.strip().splitlines()
        try:
            js = json.loads(lines[0])
        except Exception:
            js = {}
        man = json.loads(ta["seed/seed-manifest.json"].decode("utf-8"))
        t("--json prints exactly one stdout line, parseable, human line goes to stderr",
          len(lines) == 1 and js and "world_id=" in err_a and "world_id=" not in out_a)
        t("--json summary fields match the manifest",
          js.get("schema") == E.SUMMARY_SCHEMA and js.get("world_id") == man["world_id"]
          and js.get("generator_version") == man["generator_version"]
          and js.get("fleet_digest") == man["fleet_digest"]
          and js.get("horizon_start") == man["horizon_start"]
          and js.get("exposed_until") == man["exposed_until"]
          and js.get("n_day_files") == len(man["days"])
          and js.get("n_events") == sum(x["n_events"] for x in man["days"])
          and js.get("seed_only") is False and os.path.samefile(js.get("out", ""), a), js)
        t("without --json the human line stays on stdout", "world_id=" in _run_cli(["--out", os.path.join(base, "e"), "--until", nv, "--seed-only"])[1])
        # 退出碼 2
        rc_repo, _o, e_repo = _run_cli(["--out", os.path.join(REPO, "_synth_cli_should_not_exist"), "--until", nv])
        t("exit 2: output dir inside the repo (nothing created)",
          rc_repo == 2 and not os.path.exists(os.path.join(REPO, "_synth_cli_should_not_exist")), (rc_repo, e_repo))
        t("exit 2: unparseable --until", _run_cli(["--out", os.path.join(base, "x"), "--until", "yesterday"])[0] == 2)
        t("exit 2: --until with a UTC offset is not accepted",
          _run_cli(["--out", os.path.join(base, "x"), "--until", "2026-07-03T14:45:00+08:00"])[0] == 2)
        t("exit 2: unknown option (argparse)", _run_cli(["--no-such-flag"])[0] == 2)
        saved = C.local_utc_offset
        C.local_utc_offset = lambda: _dt.timedelta(0)
        try:
            rc_tz, _o, e_tz = _run_cli(["--out", os.path.join(base, "x"), "--until", z])
        finally:
            C.local_utc_offset = saved
        t("exit 2: host UTC offset is not +08:00 (also for the UTC Z form; the check is not skipped)",
          rc_tz == 2 and "+08:00" in e_tz and not os.path.exists(os.path.join(base, "x")), (rc_tz, e_tz))
        # 退出碼 3：驗證失敗 ⇒ 不動輸出目錄
        keep = os.path.join(base, "keep")
        _run_cli(["--out", keep, "--until", nv])
        before = _tree_bytes(keep)
        fresh = os.path.join(base, "fresh")
        saved_v = E.validate_seed_dir
        E.validate_seed_dir = lambda _d: ["forced: validation failure"]
        try:
            rc_k, o_k, e_k = _run_cli(["--out", keep, "--until", "2026-07-03T16:00:00Z", "--json"])
            rc_f, _o, _e = _run_cli(["--out", fresh, "--until", z])
        finally:
            E.validate_seed_dir = saved_v
        t("exit 3: validation failure; nothing on stdout, existing output untouched, no new dir",
          rc_k == 3 and rc_f == 3 and not o_k.strip() and "validation" in e_k
          and _tree_bytes(keep) == before and not os.path.exists(fresh)
          and not [n for n in os.listdir(keep) if n.startswith(".emit-stage-")], (rc_k, rc_f, e_k))
        # seed-only 不動 mes/
        mes_before = {k: v for k, v in _tree_bytes(keep).items() if k.startswith("mes/")}
        _run_cli(["--out", keep, "--until", "2026-07-03T16:00:00Z", "--seed-only"])
        after = _tree_bytes(keep)
        t("--seed-only leaves an existing mes/ untouched and refreshes seed/",
          {k: v for k, v in after.items() if k.startswith("mes/")} == mes_before
          and after["seed/seed-manifest.json"] != before["seed/seed-manifest.json"])
        # 驗證器真的擋得住壞檔（不靠 monkeypatch）
        bad = os.path.join(base, "bad")
        shutil.copytree(os.path.join(a, "seed"), bad)
        day = sorted(n for n in os.listdir(bad) if n.endswith(".jsonl"))[0]
        with open(os.path.join(bad, day), "ab") as fh:
            fh.write(b'{"junk":1}\n')
        t("validate_seed_dir passes a good seed and rejects a tampered one",
          E.validate_seed_dir(os.path.join(a, "seed")) == [] and E.validate_seed_dir(bad) != [])
        rc_h, o_h, _e = _run_cli(["--help"])
        t("--help documents the exit codes and the new flags",
          rc_h == 0 and all(s in o_h for s in ("exit codes", "--seed-only", "--json", "UTC Z", "+08:00")))
    finally:
        shutil.rmtree(base, ignore_errors=True)


TESTS = collections.OrderedDict([
    ("clock", test_clock), ("determinism", test_determinism), ("prefix", test_prefix),
    ("exposure", test_exposure), ("lifecycle", test_lifecycle),
    ("mes_consistency", test_mes_consistency), ("diseases", test_diseases),
    ("zero_real_data", test_zero_real_data), ("source_scan", test_source_scan),
    ("ranges", test_ranges), ("schema", test_schema), ("sanity", test_sanity),
    ("multiseed", test_multiseed), ("scope_rate", test_scope_rate),
    ("roster_coverage", test_roster_coverage), ("emit_cli", test_emit_cli),
])


def stats():
    w = full_world()
    files = E.render(w)
    seed_bytes = sum(len(v) for k, v in files.items() if k.startswith("seed/"))
    mes_bytes = sum(len(v) for k, v in files.items() if k.startswith("mes/"))
    ev = w["events"]
    opens = [e for e in ev if e["kind"] == "wo.open"]
    print("[stats] default seed, horizon %s → until %s" % (w["horizon_start"], w["exposed_until"]))
    print("  world_id", w["world_id"], "fleet_digest", w["fleet_digest"][:16])
    print("  machines", len(w["ctx"].fleet), dict(collections.Counter(a["gen"] for a in w["ctx"].fleet)))
    print("  WOs by type", dict(collections.Counter(e["work_type"] for e in opens)))
    print("  events by kind", dict(sorted(collections.Counter(e["kind"] for e in ev).items())))
    print("  segments", len(w["segments"]), dict(collections.Counter(s["designation"] for s in w["segments"])))
    print("  bytes seed=%d mes=%d files=%d" % (seed_bytes, mes_bytes, len(files)))
    print("  build secs %.2f" % _CACHE.get("full_secs", -1))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1].split(",")
    t0 = time.time()
    for name, fn in TESTS.items():
        if only and name not in only:
            continue
        try:
            if name == "multiseed" and "--slow" in argv:
                fn(MULTISEED_N_SLOW)
            else:
                fn()
        except Exception as exc:          # 照實記 FAIL，不吞
            import traceback
            traceback.print_exc()
            t("%s raised %s: %s" % (name, type(exc).__name__, exc), False)
    if "--stats" in argv or not only:
        stats()
    n_fail = sum(1 for _n, ok in _RESULTS if not ok)
    print("wsynth selftest: %d checks, %d FAIL, %.1fs" % (len(_RESULTS), n_fail, time.time() - t0))
    print("ALL PASS" if n_fail == 0 else "FAILED")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
