# -*- coding: utf-8 -*-
"""
wsynth_cmms_selftest.py — `wsynth_cmms` 的自測（SPEC-SYNTHLINE WP-5）
======================================================================

跑法：`python wsynth_cmms.py --selftest`（或直接 `python wsynth_cmms_selftest.py`）。

結構
  · 父進程：用 WP-1 產生器產 14 天世界、物化、啟動 loopback 伺服器，做純函式與 HTTP 面的檢查。
    T 由世界自己挑（`_pick_t`）；「病」的 wire 形在 [8b] 另建一個世界，建到每一種病都第一次
    完整出現為止（時刻由世界算出，可能落在 14 天內，也可能延長），並釘它是較長世界的前綴。
    ⇒ 本檔不寫死任何與世界內容有關的數字，產生器升版不必跟著改。
  · 子進程（`--client-probe`）：**只 import Tendline Board 既有的傳輸模組與正規化函式**，經既有的
    環境變數機制（`BOARD_CMMS_BASE_URL`／`BOARD_CMMS_TOKEN`／`BOARD_A11_LIVE`）指向
    loopback 伺服器，逐端點打一遍、把結果以 JSON 回給父進程比對。
    ⇒ 測試用環境變數**只存在於子進程的 env**（`subprocess` 的 `env=`），父進程與使用者環境零寫入。
  · 時鐘：父進程以 `wsynth_clock.now(environ={...})` 凍結 T，不動 `os.environ`。

C-SY1 負斷言：物化與伺服期間攔 `builtins.open`／`io.open`／`os.open`，任何指向
`_fixtures`／`_state`／snapshot 的開檔即紅；另以乾淨子進程驗 `import wsynth_cmms` 不帶入
任何 Tendline Board 資料模組。本檔與 `wsynth_cmms.py` 原始碼不含站名、機台名、EID 或編號字面值。
"""
from __future__ import annotations

import builtins
import datetime as _dt
import http.client
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 契約詞彙（不隨世界版本變動）：這兩個 hold_reason 機台可生產、不計停機
WMT, TEST_RUN = "WAITING_MACHINE_TIME", "TEST_RUN"


# ===========================================================================
# 子進程：Tendline Board 真碼當 client
# ===========================================================================

def _iso(d):
    return None if d is None else d.strftime("%Y-%m-%dT%H:%M:%S")


def _scope_wire_checks(ck, details, children_of):
    scoped = [n for n, d in details.items() if d["scope_events"]]
    change = [n for n, d in details.items() if len(d["scope_events"]) >= 2]
    ck("parent WO scope events: members are that parent's children",
       scoped and all(set(ev["scope"]) <= children_of.get(details[n]["asset_id"], set())
                      for n in scoped for ev in details[n]["scope_events"]),
       "scoped=%d" % len(scoped))
    ck("scope change appends a later, wider scope event",
       change and all(details[n]["scope_events"][0]["ts"] < details[n]["scope_events"][-1]["ts"]
                      and len(details[n]["scope_events"][-1]["scope"]) >
                      len(details[n]["scope_events"][0]["scope"]) for n in change),
       "n=%d" % len(change))
    return scoped, change


def _run_probe(base, token, plan):
    """子進程跑 `--client-probe`；測試用 env 只給子進程（`env=`），不寫父進程或使用者環境。"""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("BOARD_"):
            del env[k]
    env.update({"BOARD_CMMS_BASE_URL": base, "BOARD_CMMS_TOKEN": token,
                "BOARD_A11_LIVE": "1", "PYTHONIOENCODING": "utf-8"})
    return subprocess.run([sys.executable, os.path.abspath(__file__), "--client-probe"],
                          input=json.dumps(plan).encode("utf-8"), capture_output=True,
                          env=env, cwd=HERE, timeout=600)


def _client_probe():
    """讀 stdin 的計畫、以 Board 既有模組打伺服器、JSON 寫 stdout。"""
    plan = json.loads(sys.stdin.read())
    real_stdout, sys.stdout = sys.stdout, sys.stderr     # 模組的 print 不得污染 JSON 回傳
    import board_w8 as w8
    import board_w8_transport as w8t
    t = _dt.datetime.fromisoformat(plan["t"])
    out = {"configured": w8t.is_configured(), "windows": [], "wo": {}, "a11": {},
           "batch": []}

    for s, e in plan["windows"]:
        row = {}
        for mode in ("auto", "detail"):
            res, reason = w8t.fetch_active_work_orders(s, e, mode=mode)
            if reason is not None:
                row[mode] = {"reason": reason}
                continue
            row[mode] = {"reason": None, "mode": res["mode"], "count": res["count"],
                         "truncated": res["truncated"], "detail_errors": res["detail_errors"],
                         "wnos": [w["work_order_no"] for w in res["work_orders"]],
                         "seats": {str(w["work_order_no"]): [w["asset_id"], w["seat_unit"],
                                                              w["seat_known"]]
                                   for w in res["work_orders"] if w.get("seat_unit")}}
        out["windows"].append(row)

    cfg = w8t._config()
    for wno in plan["wnos"]:
        r = {}
        det, reason = w8t.fetch_pm_wo_detail(wno)                  # 呼叫表 #8／#11／#13
        r["pm_reason"] = reason
        # 呼叫表 #9／#10：私有路徑（app._eqm_wo_details 的同一串呼叫）
        payload, preason = w8t._http_get_json(w8t._detail_url(cfg[0], wno), cfg[1],
                                              w8t.DEFAULT_TIMEOUT)
        pdet, preason2 = (w8t._parse_detail_envelope(payload) if preason is None
                          else (None, preason))
        r["private_reason"] = preason2
        r["private_same"] = (pdet == det)
        if det is not None:
            n = w8.normalize_wo(det)
            r["normalize_ok"] = n is not None
            r["notes_raw"] = len(det.get("notes") or [])
            r["notes_norm"] = len(w8.normalize_notes(det))
            evs, dropped = w8.normalize_scope_events(det.get("scope_events"))
            r["scope_n"] = len(evs)
            r["scope_dropped"] = dropped
            r["scope_last"] = evs[-1]["scope"] if evs else None
            if n is not None:
                n2 = dict(n)
                n2["scope_events"] = det.get("scope_events")
                r["effective_seat_units"] = w8.effective_seat_units(n2)
                win = w8.wo_active_window(n, now=t)
                r["window"] = None if win is None else [_iso(win[0]), _iso(win[1])]
                r["cancelled"] = w8.wo_is_cancelled(n)
                r["hist"] = [[h["status"], _iso(h["at"]), h["hold_reason"]]
                             for h in n["status_history"]]
                r["asset_id"] = n["asset_id"]
                r["seat_unit"] = n["seat_unit"]
                r["pra_block"] = w8.pra_block_reason(n, now=t)
        out["wo"][str(wno)] = r

    for eid in plan["eids_events"]:
        res, reason = w8t.fetch_schedule_state_events(eid)       # 呼叫表 #15
        out["a11"][eid] = {"reason": reason,
                           "rows": None if res is None else res["rows"]}

    for b in plan["batch"]:
        res, reason = w8t.fetch_schedule_state_batch(b["eids"], as_of=b["as_of"])  # #16
        out["batch"].append({"reason": reason,
                             "items": None if res is None else res["items"],
                             "unknown": None if res is None else res["unknown"],
                             "pages": None if res is None else res["pages"]})

    # 呼叫表 #15／#16／#17：board_data 的 A11 兩條腿（閘由子進程 env 打開）
    if not plan.get("wd", True):
        real_stdout.write(json.dumps(out, ensure_ascii=False))
        real_stdout.flush()
        return 0
    import board_data as wd
    evs, status, _age = wd.get_dormant_events(force=True)
    out["dormant"] = {"status": status, "n": None if evs is None else len(evs),
                      "gate": wd.a11_live_enabled(), "n_eids": len(wd.a11_eids()),
                      "eids": list(wd.a11_eids())}
    states, unknown, bstatus, _age = wd.get_schedule_state(plan["fleet"], force=True)
    out["sched_state"] = {"status": bstatus, "n": None if states is None else len(states),
                          "unknown": unknown}
    real_stdout.write(json.dumps(out, ensure_ascii=False))
    real_stdout.flush()
    return 0


# ===========================================================================
# 父進程
# ===========================================================================

class _Checks:
    def __init__(self):
        self.rows = []

    def ck(self, name, ok, info=""):
        self.rows.append((name, bool(ok), info))
        print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + str(info)) if info else ""))
        return bool(ok)

    @property
    def failed(self):
        return [r for r in self.rows if not r[1]]


class _OpenSpy:
    """攔 `builtins.open`／`io.open`／`os.open`，記下指向真資料形路徑的開檔。"""

    BAD = re.compile(r"(^|[\\/])(_fixtures|_state)([\\/]|$)|(^|[\\/])snapshot", re.I)

    def __init__(self):
        self.hits = []
        self.n = 0

    def __enter__(self):
        self._b, self._i, self._o = builtins.open, io.open, os.open
        spy = self

        def wrap(real):
            def f(file, *a, **k):
                spy.n += 1
                if isinstance(file, (str, bytes, os.PathLike)) and spy.BAD.search(os.fsdecode(file)):
                    spy.hits.append(os.fsdecode(file))
                return real(file, *a, **k)
            return f
        builtins.open, io.open, os.open = wrap(self._b), wrap(self._i), wrap(self._o)
        return self

    def __exit__(self, *exc):
        builtins.open, io.open, os.open = self._b, self._i, self._o
        return False


def _z(d):
    import wsynth_clock as C
    return C.to_utc_z(d)


def _wo_events(world):
    by = {}
    for e in world["events"]:
        if e["kind"].startswith("wo."):
            by.setdefault(e["work_order_no"], []).append(e)
    return by


def _ref_end(evs, t_z):
    """參考實作（獨立於物化器）：首個 COMPLETED／終態的時刻（≤T）；無 ⇒ None。"""
    for e in sorted(evs, key=lambda e: e["seq"]):
        if e["kind"] == "wo.status" and e["at"] <= t_z and \
                e["to_status"] in ("COMPLETED", "CLOSED", "CANCELLED", "VOIDED"):
            return e["at"]
    return None


def _battery(st, extra_windows=()):
    """一組涵蓋所有端點的請求（path, query）。"""
    import urllib.parse as up
    fmt = "%Y-%m-%d %H:%M:%S"
    t = st.t
    wins = [(t - _dt.timedelta(days=d), t - _dt.timedelta(days=d - 1)) for d in (1, 3, 7)]
    wins += list(extra_windows)
    reqs = []
    for s, e in wins:
        reqs.append(("/work-orders/active-in",
                     up.urlencode({"start": s.strftime(fmt), "end": e.strftime(fmt)})))
    for wno in sorted(st.wos):
        reqs.append(("/work-orders/%d/detail" % wno, ""))
    for aid in sorted(st.master):
        reqs.append(("/assets/%s/schedule-state-events" % up.quote(aid, safe=""), ""))
    reqs.append(("/assets/schedule-state", ""))
    reqs.append(("/assets/schedule-state", up.urlencode({"eids": ",".join(sorted(st.master)[:150])})))
    for d in range(0, 15, 2):
        day = (t - _dt.timedelta(days=d)).date().isoformat()
        reqs.append(("/assets/schedule-state", up.urlencode({"as_of": day})))
    return reqs


def _all_bytes(mat, reqs, t):
    return [mat.get(p, q, t) for p, q in reqs]


def _pick_t(C, wev, T0, U14):
    """由世界自己挑 T（不寫死）：14 天窗內（由晚往早）第一個同時具備下列性質的整點＋7 分——
    T 之後仍有新開單、有跨越 T 的單、窗 [T-2d, T-1h] 內「仍開著／窗內結／窗前結」三種單都有。"""
    for d in range(11, 2, -1):                    # 由晚往早：T 前有較長歷史
        for h in range(24):
            T = T0 + _dt.timedelta(days=d, hours=h, minutes=7)
            if T >= U14:
                return None
            tz = C.to_utc_z(T)
            Ws, We = T - _dt.timedelta(days=2), T - _dt.timedelta(hours=1)
            future = straddle = openc = inside = before = 0
            for evs in wev.values():
                op = [e for e in evs if e["kind"] == "wo.open"][0]
                if op["at"] > tz:
                    future += 1
                    continue
                if any(e["kind"] == "wo.status" and e["at"] > tz for e in evs):
                    straddle += 1
                end = _ref_end(evs, tz)
                if end is None:
                    openc += C.from_utc_z(op["at"]) <= We
                elif Ws <= C.from_utc_z(end) <= We:
                    inside += 1
                elif C.from_utc_z(end) < Ws:
                    before += 1
            if future and straddle and openc and inside and before:
                return T
    return None


def _first_features(C, P, world):
    """在（較長的）世界裡找每一種「病」第一次完整出現的時刻 → {名稱: (完成時刻, 相關工單號或事件)}。
    完全由世界計算；產生器升版後自動跟著走。"""
    wev = _wo_events(world)
    defs = [e for e in world["events"] if e["kind"] == "asset.define"]
    nest_seats = {s["asset_id"] for d in defs for s in d["seats"]
                  if (s.get("unit") or "").startswith("nest:")}
    feats = {}

    def put(name, at, ref):
        if name not in feats or at < feats[name][0]:
            feats[name] = (at, ref)
    for n, evs in sorted(wev.items()):
        evs = sorted(evs, key=lambda e: e["seq"])
        st = [e for e in evs if e["kind"] == "wo.status"]
        for i in range(len(st) - 2):
            tri = st[i:i + 3]
            if [e["to_status"] for e in tri] == ["IN_PROGRESS", "COMPLETED", "CLOSED"] and \
                    len({e["at"] for e in tri}) == 1:
                put("one_click", tri[-1]["at"], n)
        for e in st:
            if e["to_status"] == "ON_HOLD":
                th = C.to_utc_z(C.from_utc_z(e["at"]) + _dt.timedelta(minutes=1))
                put("hold:" + str(e["hold_reason"]), th, n)
        op = evs[0]
        if op["asset_id"] in nest_seats:
            put("nest_seat_wo", op["at"], n)
        if any(e["kind"] == "wo.scope" for e in evs):
            put("scope_change", evs[-1]["at"], n)
    for e in world["events"]:
        if e["kind"] == "asset.schedule_state" and e["state"] == "not_scheduled":
            put("a11_whole" if e["sub_unit"] is None else "a11_lane", e["at"], e["seq"])
    want = ["one_click", "nest_seat_wo", "scope_change", "a11_whole", "a11_lane"] + \
        ["hold:" + r for r in sorted(P.HOLD_MIN)]
    return feats, want


def _a11_checks(ck, M, C, mat, t, a11_evs, label):
    whole = [e for e in a11_evs if e["sub_unit"] is None]
    lane = [e for e in a11_evs if e["sub_unit"] is not None]
    ok = True
    for e in a11_evs:
        rows = json.loads(mat.get("/assets/%s/schedule-state-events" % e["asset_id"], "", t)[1])
        r = [x for x in rows if x["effective_date"] == e["effective_date"]
             and x["sub_unit"] == e["sub_unit"]]
        eff = _dt.date.fromisoformat(e["effective_date"])
        ok &= bool(r) and r[0]["state"] == e["state"] and \
            r[0]["ts_effective"] == _z(C.day_start(eff)) and r[0]["recorded_at"] == e["at"]
    ck("%s A11 per-EID rows: effective_date + Taipei-midnight ts_effective + recorded_at" % label,
       ok, "whole=%d lane=%d" % (len(whole), len(lane)))
    ok = True
    for e in whole:
        eff = _dt.date.fromisoformat(e["effective_date"])
        on = json.loads(mat.get("/assets/schedule-state", "eids=%s&as_of=%s" % (e["asset_id"], eff), t)[1])
        before = json.loads(mat.get("/assets/schedule-state", "eids=%s&as_of=%s" % (
            e["asset_id"], eff - _dt.timedelta(days=1)), t)[1])
        ok &= on["items"][0]["state"] == e["state"] and on["items"][0]["since"] == e["effective_date"]
        ok &= before["items"][0]["since"] is None or before["items"][0]["since"] < e["effective_date"]
    ck("%s A11 batch as-of flips on effective_date (whole-machine lane)" % label, ok)
    ok = True
    for e in lane:
        prior_whole = [w for w in whole if w["asset_id"] == e["asset_id"]
                       and w["effective_date"] <= e["effective_date"]]
        if prior_whole:
            continue
        b = json.loads(mat.get("/assets/schedule-state", "eids=%s&as_of=%s" % (
            e["asset_id"], e["effective_date"]), t)[1])
        ok &= b["items"][0]["state"] == "in_production"
    ck("%s A11 batch ignores seat lanes" % label, ok)
    return whole, lane


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--client-probe" in argv:
        return _client_probe()

    import wsynth_clock as C
    import wsynth_params as P
    import wsynth_world as W
    import wsynth_emit as E
    import wsynth_cmms as M

    ck = _Checks().ck
    chk = ck.__self__
    T0 = C.day_start(P.HORIZON_START)
    U14 = T0 + _dt.timedelta(days=14)

    print("[1] world")
    world = W.build_world(U14, with_segments=False)
    world_seg = W.build_world(U14)
    ck("events identical with/without segments", world["events"] == world_seg["events"])
    wev = _wo_events(world)
    T = _pick_t(C, wev, T0, U14)
    ck("T chosen from the world (future opens, straddlers, open/inside/before cases)",
       T is not None, T)
    if T is None:
        return _finish(chk, None)
    T_z = C.to_utc_z(T)
    frozen = {C.ENV_FREEZE: T.isoformat()}
    now_T = lambda: C.now(frozen)                  # noqa: E731（凍結時鐘，不動 os.environ）
    ck("wsynth_clock frozen via explicit environ", now_T() == T and not C.is_frozen())

    spy = _OpenSpy()
    with spy:
        matA = M.Materializer(M.WorldSource(world), now_fn=now_T)
        st = matA.state()
        ck("state T equals frozen clock", st.t == T)
        ck("replay: zero unknown kinds / orphans",
           st.skipped_unknown_kind == 0 and st.skipped_orphan == 0)
        ck("replay: single world_id", st.world_ids == {world["world_id"]})

        # ---------------- 確定性 ----------------
        print("[2] determinism")
        reqs = _battery(st)
        a1 = _all_bytes(matA, reqs, T)
        matB = M.Materializer(M.WorldSource(W.build_world(U14, with_segments=False)), now_fn=now_T)
        ck("same world, same T => byte-identical responses (independent rebuild)",
           a1 == _all_bytes(matB, reqs, T), "requests=%d" % len(reqs))
        ck("all battery responses 200", all(c == 200 for c, _b in a1))
        matG = M.Materializer(M.GeneratorSource(), now_fn=now_T)
        ck("GeneratorSource at T == world source at T (bytewise)",
           a1 == _all_bytes(matG, reqs, T))

        # ---------------- 曝光 ----------------
        print("[3] exposure")
        exact = M.Materializer(M.WorldSource(W.build_world(T, with_segments=False)), now_fn=now_T)
        ck("world built exactly to T == 14-day world replayed to T (bytewise)",
           a1 == _all_bytes(exact, reqs, T))
        zs = set()
        ds = set()
        for _c, b in a1:
            s = b.decode("utf-8")
            zs.update(re.findall(r'"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"', s))
            ds.update(re.findall(r'"(\d{4}-\d{2}-\d{2})"', s))
        ck("no UTC timestamp after T in any response", zs and max(zs) <= T_z,
           "max=%s T=%s" % (max(zs) if zs else None, T_z))
        ck("no calendar date after T.date() in any response",
           ds and max(ds) <= T.date().isoformat())
        future_open = [n for n, evs in wev.items()
                       if any(e["kind"] == "wo.open" and e["at"] > T_z for e in evs)]
        straddle = [n for n, evs in wev.items()
                    if any(e["kind"] == "wo.open" and e["at"] <= T_z for e in evs)
                    and any(e["kind"] == "wo.status" and e["at"] > T_z for e in evs)]
        full = json.loads(matA.get("/work-orders/active-in",
                                   "start=%s&end=%s" % (T0.strftime("%Y-%m-%d+%H:%M:%S"),
                                                        U14.strftime("%Y-%m-%d+%H:%M:%S")),
                                   T)[1])
        listed = {i["work_order_no"] for i in full["items"]}
        ck("WO opened after T: detail 404 and absent from list",
           future_open and all(matA.get("/work-orders/%d/detail" % n, "", T)[0] == 404
                               for n in future_open)
           and not (set(future_open) & listed), "n=%d" % len(future_open))
        ok = bool(straddle)
        for n in straddle:
            last_le = [e for e in sorted(wev[n], key=lambda e: e["seq"])
                       if e["kind"] == "wo.status" and e["at"] <= T_z]
            d = json.loads(matA.get("/work-orders/%d/detail" % n, "", T)[1])
            exp = last_le[-1]["to_status"] if last_le else "OPEN"
            ok &= d["status"] == exp and all(h["changed_at"] <= T_z for h in d["status_history"])
        ck("straddling WO shows only its <=T lifecycle", ok, "n=%d" % len(straddle))
        pra_evs = [e for evs in wev.values() for e in evs
                   if e["kind"] == "wo.field" and e["field"] == "production_resumed_at"]
        ok = True
        for e in pra_evs:
            tw = C.from_utc_z(e["at"])
            before = json.loads(matA.get("/work-orders/%d/detail" % e["work_order_no"], "",
                                         tw - _dt.timedelta(seconds=1))[1])
            at = json.loads(matA.get("/work-orders/%d/detail" % e["work_order_no"], "", tw)[1])
            ok &= before["production_resumed_at"] is None and at["production_resumed_at"] == e["value"]
        ck("pra appears exactly when its field event is exposed (1 s before: null)", ok,
           "n=%d" % len(pra_evs))

        # ---------------- 活躍窗語意 ----------------
        print("[4] active-in window semantics")
        Ws, We = T - _dt.timedelta(days=2), T - _dt.timedelta(hours=1)
        q = "start=%s&end=%s" % (Ws.strftime("%Y-%m-%d+%H:%M:%S"), We.strftime("%Y-%m-%d+%H:%M:%S"))
        got = {i["work_order_no"] for i in json.loads(matA.get("/work-orders/active-in", q, T)[1])["items"]}
        exp_in, cases = set(), {"open": [], "closed_in": [], "closed_before": []}
        for n, evs in wev.items():
            op = [e for e in evs if e["kind"] == "wo.open"][0]
            if op["at"] > T_z:
                continue
            end = _ref_end(evs, T_z)
            end_tp = C.from_utc_z(end) if end else T
            if C.from_utc_z(op["at"]) <= We and end_tp >= Ws:
                exp_in.add(n)
            if end is None and C.from_utc_z(op["at"]) <= We:
                cases["open"].append(n)
            elif end is not None and Ws <= C.from_utc_z(end) <= We:
                cases["closed_in"].append(n)
            elif end is not None and C.from_utc_z(end) < Ws:
                cases["closed_before"].append(n)
        ck("active-in set == independent reference (cmms window rule)", got == exp_in,
           "got=%d exp=%d" % (len(got), len(exp_in)))
        ck("case still-open WO included", cases["open"] and set(cases["open"]) <= got,
           "n=%d" % len(cases["open"]))
        ck("case WO ended inside window included",
           cases["closed_in"] and set(cases["closed_in"]) <= got, "n=%d" % len(cases["closed_in"]))
        ck("case WO ended before window excluded",
           cases["closed_before"] and not (set(cases["closed_before"]) & got),
           "n=%d" % len(cases["closed_before"]))
        bad = matA.get("/work-orders/active-in", "start=%s&end=x" % T.date().isoformat(), T)[0]
        ck("malformed start/end => 422", bad == 422)
        one_aid = sorted(wev[sorted(got)[0]], key=lambda e: e["seq"])[0]["asset_id"]
        fl = json.loads(matA.get("/work-orders/active-in", q + "&asset_id=" + one_aid, T)[1])
        ck("asset_id filter exact on wire id",
           fl["items"] and all(i["asset_id"] == one_aid for i in fl["items"]))

        # ---------------- wire 不變式（與世界內容無關的性質）----------------
        print("[5] wire invariants on every WO / A11 row at T")
        details = {n: json.loads(matA.get("/work-orders/%d/detail" % n, "", T)[1]) for n in st.wos}
        _wire_invariants(ck, P, details, "[14d@T]")
        a11_evs = [e for e in world["events"] if e["kind"] == "asset.schedule_state" and e["at"] <= T_z]
        _a11_checks(ck, M, C, matA, T, a11_evs, "[14d@T]")
        unk = sorted(st.master)[0] + "-" + "Q"
        b = json.loads(matA.get("/assets/schedule-state", "eids=%s,%s" % (sorted(st.master)[0], unk), T)[1])
        ck("A11 batch unknown EID goes to unknown[]", b["unknown"] == [unk] and len(b["items"]) == 1)
        ck("A11 per-EID unknown asset => 404",
           matA.get("/assets/%s/schedule-state-events" % unk, "", T)[0] == 404)
        many = ",".join("%s-%d" % (unk, i) for i in range(M.BATCH_MAX_EIDS + 1))
        ck("A11 batch > cap => 422", matA.get("/assets/schedule-state", "eids=" + many, T)[0] == 422)
        mx = M.Materializer(M.WorldSource(world), now_fn=now_T, extra_master=[unk])
        ck("extra_master (default empty) only adds existence, no events",
           not M.Materializer(M.WorldSource(world)).extra_master
           and json.loads(mx.get("/assets/%s/schedule-state-events" % unk, "", T)[1]) == [])

        # ---------------- 種子目錄來源 ----------------
        print("[6] seed-dir source")
        tmp = tempfile.mkdtemp(prefix="wsynth-cmms-selftest-")
        out = E.emit(world, os.path.join(tmp, "w"))
        sd = M.Materializer(M.SeedDirSource(out), now_fn=now_T)
        ck("seed-dir source == world source (bytewise)", a1 == _all_bytes(sd, reqs, T))
        _seed_marker_checks(ck, M, P, out)
        _replay_kind_checks(ck, M, world, T)
        refused = 0
        for p in (os.path.join(HERE, "tmp-seed"), os.path.join(tmp, "_fixtures", "x"),
                  os.path.join(tmp, "_state"), os.path.join(tmp, "snapshot-x")):
            try:
                M.check_seed_dir(p)
            except M.SeedDirRefused:
                refused += 1
        ck("seed dir refused: repo / _fixtures / _state / snapshot", refused == 4)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

        # ---------------- 伺服器 ----------------
        print("[7] loopback server")
        try:
            M.make_server(M.WorldSource(world), host="0.0.0.0")
            ck("non-loopback host refused", False)
        except ValueError:
            ck("non-loopback host refused", True)
        token = "selftest-" + world["world_id"]
        with M.serve(M.WorldSource(world), token=token, now_fn=now_T) as base:
            host, port = base.split("//", 1)[1].split(":")
            ck("bound to 127.0.0.1 on an ephemeral port", host == "127.0.0.1" and int(port) > 0, base)

            def req(method, path, auth=True, body=None):
                c = http.client.HTTPConnection(host, int(port), timeout=10)
                h = {"Authorization": "Bearer " + token} if auth else {}
                c.request(method, path, body=body, headers=h)
                r = c.getresponse()
                data = r.read()
                allow = r.getheader("Allow")
                c.close()
                return r.status, data, allow
            codes = {m: req(m, "/work-orders/active-in", body=b"{}" if m in ("POST", "PUT", "PATCH") else None)
                     for m in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")}
            ck("non-GET => 405 with Allow: GET",
               all(v[0] == 405 and v[2] == "GET" for v in codes.values()),
               {k: v[0] for k, v in codes.items()})
            ck("missing bearer => 401", req("GET", reqs[0][0] + "?" + reqs[0][1], auth=False)[0] == 401)
            ck("uncovered phase-2 endpoints => 404",
               all(req("GET", p.replace("{no}", "1"))[0] == 404 for p in M.NOT_COVERED))
            http_same = True
            for (p, qq), (c0, b0) in list(zip(reqs, a1))[:60]:
                s1, d1, _ = req("GET", p + ("?" + qq if qq else ""))
                s2, d2, _ = req("GET", p + ("?" + qq if qq else ""))
                http_same &= (s1, d1) == (c0, b0) == (s2, d2)
            ck("HTTP body == materializer bytes; repeated GET byte-identical", http_same)

            # ---- Tendline Board 真碼當 client（子進程；env 只在子進程）----
            print("[8] Tendline Board transport + normalizers as client (subprocess)")
            fmt = "%Y-%m-%d %H:%M:%S"
            windows = [(Ws.strftime(fmt), We.strftime(fmt)),
                       (T0.strftime(fmt), U14.strftime(fmt)),
                       ((T - _dt.timedelta(days=7)).strftime(fmt), T.strftime(fmt))]
            fleet = sorted(st.assets)
            plan = {"t": T.isoformat(), "windows": windows, "wnos": sorted(st.wos),
                    "eids_events": sorted(st.master),
                    "batch": [{"eids": sorted(st.master), "as_of": None},
                              {"eids": sorted(st.master), "as_of": (T - _dt.timedelta(days=5)).date().isoformat()},
                              {"eids": None, "as_of": None},
                              {"eids": [fleet[0], unk], "as_of": None}],
                    "fleet": fleet}
            proc = _run_probe(base, token, plan)
            ck("client probe exit 0", proc.returncode == 0,
               proc.stderr.decode("utf-8", "replace")[-800:] if proc.returncode else "")
            if proc.returncode != 0:
                return _finish(chk, spy)
            res = json.loads(proc.stdout.decode("utf-8"))
            http_log = proc.stderr.decode("utf-8", "replace")
            n_get = len(re.findall(r"\[w8_transport\] GET ", http_log))
            ck("transport configured via env only (base URL + token)", res["configured"])
            bad_out = [o for o in re.findall(r"\[w8_transport\] GET \S+ attempt=\d+ [\d.]+s -> (\S+)",
                                             http_log) if o != "ok"]
            ck("transport timing log: every GET ok", n_get > 0 and not bad_out,
               "GETs=%d non-ok=%d" % (n_get, len(bad_out)))
            ok = True
            info = []
            for (s, e), row in zip(windows, res["windows"]):
                sv = json.loads(matA.get("/work-orders/active-in", "start=%s&end=%s" % (
                    s.replace(" ", "+"), e.replace(" ", "+")), T)[1])
                n_srv = len(sv["items"])
                for mode in ("auto", "detail"):
                    r = row[mode]
                    ok &= r["reason"] is None and r["count"] == n_srv and \
                        r["detail_errors"] == 0 and not r["truncated"]
                    info.append("%s:%s/%d" % (mode, r.get("count"), n_srv))
                ok &= row["auto"]["mode"] == "list_inline" and row["detail"]["mode"] == "list_plus_detail"
            ck("fetch_active_work_orders inline + list_plus_detail: zero rejects", ok, " ".join(info))
            wo = res["wo"]
            _w8_acceptance(ck, wo, details, "[14d@T]")
            ok = True
            for n in cases["open"]:
                if details[n]["production_resumed_at"] is None:
                    ok &= wo[str(n)]["window"][1] is None
            for n in cases["closed_before"]:
                ok &= wo[str(n)]["window"][1] is not None and \
                    wo[str(n)]["window"][1] < Ws.strftime("%Y-%m-%dT%H:%M:%S")
            ck("W8 wo_active_window agrees: open => ongoing, ended-before => end < window", ok)
            a11 = res["a11"]
            ck("fetch_schedule_state_events ok for every master asset",
               all(v["reason"] is None for v in a11.values()), "n=%d" % len(a11))
            ck("A11 rows round-trip unchanged",
               all(a11[aid]["rows"] == json.loads(matA.get(
                   "/assets/%s/schedule-state-events" % aid, "", T)[1]) for aid in a11))
            bt = res["batch"]
            ck("fetch_schedule_state_batch ok (explicit, as_of, whole fleet, with unknown)",
               all(b["reason"] is None for b in bt), [b["reason"] for b in bt])
            ck("batch pages == ceil(master / cap) (transport chunking path)",
               bt[0]["pages"] == -(-len(st.master) // M.BATCH_MAX_EIDS), "pages=%s master=%d" % (
                   bt[0]["pages"], len(st.master)))
            ck("batch unknown surfaced", bt[3]["unknown"] == [unk])
            dm = res["dormant"]
            missing = sorted(set(dm["eids"]) - st.master)
            roster = set(dm["eids"])
            n_exp = sum(1 for e in a11_evs if e["asset_id"] in roster)
            ck("no extra assets supplied: board_data.get_dormant_events => live, "
               "every long-stop roster EID in the synthetic master, event count matches",
               dm["status"] == "live" and not missing and dm["n"] == n_exp and dm["n_eids"] > 0,
               "status=%s roster=%d missing=%d n=%s exp=%d" % (dm["status"], dm["n_eids"],
                                                               len(missing), dm["n"], n_exp))
            ss = res["sched_state"]
            ck("board_data.get_schedule_state => live, no unknown",
               ss["status"] == "live" and not ss["unknown"], ss)

        # ---- [8b] 病的 wire 形：世界建到「每一種病都第一次完整出現」為止 ----
        # 由世界自己算出時刻（可能落在 14 天窗內，也可能要延長）；延長後的世界必須是較長世界的
        # 前綴（逐位元），⇒ 產生器升版時本節不必改。
        print("[8b] reproduced diseases (world built to the first full occurrence of each)")
        w_long = W.build_world(T0 + _dt.timedelta(days=P.HISTORY_DAYS), with_segments=False)
        feats, want = _first_features(C, P, w_long)
        absent = [f for f in want if f not in feats]
        ck("generator produces every reproduced disease within the history horizon", not absent, absent)
        if not absent:
            Td = max(C.from_utc_z(feats[f][0]) for f in want)
            print("  info disease world ends %s (day %d of the horizon; 14-day window %s)"
                  % (Td, (Td - T0).days, "suffices" if Td <= U14 else "extended"))
            wd = W.build_world(Td, with_segments=False)
            now_Td = lambda: Td                   # noqa: E731
            matD = M.Materializer(M.WorldSource(wd), now_fn=now_Td)
            stD = matD.state()
            ck("disease world is a prefix of the longer world (bytewise at Td)",
               _all_bytes(matD, _battery(stD), Td) ==
               _all_bytes(M.Materializer(M.WorldSource(w_long), now_fn=now_Td), _battery(stD), Td))
            detD = {n: json.loads(matD.get("/work-orders/%d/detail" % n, "", Td)[1]) for n in stD.wos}
            _wire_invariants(ck, P, detD, "[disease@Td]")
            one_click = [n for n, d in detD.items()
                         if len(d["status_history"]) >= 4
                         and [x["to_status"] for x in d["status_history"][-3:]] == ["IN_PROGRESS", "COMPLETED", "CLOSED"]
                         and len({x["changed_at"] for x in d["status_history"][-3:]}) == 1]
            ck("one-click close: three transitions share one timestamp", one_click,
               "n=%d" % len(one_click))
            wevD = _wo_events(wd)
            ok = True
            for reason in sorted(P.HOLD_MIN):
                n = feats["hold:" + reason][1]
                e = [x for x in sorted(wevD[n], key=lambda x: x["seq"])
                     if x["kind"] == "wo.status" and x["to_status"] == "ON_HOLD"
                     and x["hold_reason"] == reason][0]
                th = C.from_utc_z(e["at"])
                item = json.loads(matD.get("/work-orders/%d/detail" % n, "", th)[1])
                hh = [x for x in item["status_history"] if x["to_status"] == "ON_HOLD"][-1]
                ok &= (item["status"] == "ON_HOLD" and item["hold_reason"] == reason
                       and hh["hold_reason"] == reason
                       and hh["is_downtime"] == (reason not in (WMT, TEST_RUN)))
            ck("each generated hold reason: ON_HOLD status, WO hold_reason, is_downtime per vocab",
               ok, sorted(P.HOLD_MIN))
            defs = stD.assets
            nest_seats = {s["asset_id"]: (a, s["unit"]) for a, d in defs.items()
                          for s in d["seats"] if (s["unit"] or "").startswith("nest:")}
            children_of = {a: {s["asset_id"] for s in d["seats"]} for a, d in defs.items()
                           if "contains_module" in d["roles"]}
            seat_wos = [n for n, d in detD.items() if d["asset_id"] in nest_seats]
            ck("seat-level WO on nest seat wire id", seat_wos, "n=%d" % len(seat_wos))
            scoped, change = _scope_wire_checks(ck, detD, children_of)
            ok, n_open_scope = True, 0
            for n, evs in wevD.items():
                evs = sorted(evs, key=lambda e: e["seq"])
                op = evs[0]
                if not op["scope"]:
                    continue
                n_open_scope += 1
                se = detD[n]["scope_events"]
                later = [e for e in evs if e["kind"] == "wo.scope"]
                ok &= (se[0]["ts"] == op["at"] == detD[n]["opened_at"] and se[0]["scope"] == op["scope"]
                       and len(se) == 1 + len(later)
                       and [x["ts"] for x in se[1:]] == [e["at"] for e in later])
            ck("SPEC 5.4-7: open-with-partial-scope => first scope event at wo.open.at with "
               "wo.open.scope, then one per wo.scope", ok and n_open_scope > 0, "n=%d" % n_open_scope)
            a11D = [e for e in wd["events"] if e["kind"] == "asset.schedule_state"]
            whole, lane = _a11_checks(ck, M, C, matD, Td, a11D, "[disease@Td]")
            ck("long stops present (whole-machine and seat lane)", whole and lane,
               "whole=%d lane=%d" % (len(whole), len(lane)))
            with M.serve(M.WorldSource(wd), token=token, now_fn=now_Td) as baseD:
                pD = {"t": Td.isoformat(), "wnos": sorted(set(one_click) | set(seat_wos) | set(scoped)),
                      "eids_events": sorted({e["asset_id"] for e in a11D}), "batch": [],
                      "windows": [(T0.strftime(fmt), Td.strftime(fmt))], "fleet": sorted(stD.assets)}
                procD = _run_probe(baseD, token, pD)
            ck("disease-world client probe exit 0", procD.returncode == 0,
               procD.stderr.decode("utf-8", "replace")[-800:] if procD.returncode else "")
            if procD.returncode == 0:
                rD = json.loads(procD.stdout.decode("utf-8"))
                woD = rD["wo"]
                _w8_acceptance(ck, woD, detD, "[disease@Td]")
                ok = bool(one_click)
                for n in one_click:
                    h = woD[str(n)]["hist"]
                    ip = [x for x in h if x[0] == "IN_PROGRESS"][0][1]
                    ok &= [x[0] for x in h[-3:]] == ["IN_PROGRESS", "COMPLETED", "CLOSED"] and \
                        woD[str(n)]["window"][1] == ip
                ck("one-click via W8: order kept, window end == IN_PROGRESS time (MTTR 0)", ok)
                seat_map = rD["windows"][0]["auto"].get("seats") or {}
                ok = bool(seat_map)
                for n, (aid, unit, known) in seat_map.items():
                    wid = detD[int(n)]["asset_id"]
                    ok &= wid in nest_seats and nest_seats[wid] == (aid, unit) and known
                for n in seat_wos:
                    ok &= woD[str(n)]["seat_unit"] == nest_seats[detD[n]["asset_id"]][1]
                ck("normalize_wo folds nest seat wire id to parent + seat_unit", ok,
                   "n=%d" % len(seat_map))
                ok = bool(scoped)
                for n in scoped:
                    r = woD[str(n)]
                    last = detD[n]["scope_events"][-1]["scope"]
                    ok &= r["scope_dropped"] == 0 and r["scope_last"] == last and \
                        set(r["effective_seat_units"]) <= set(last)
                ck("scoped parent WO via W8: zero drops, latest scope; effective_seat_units within "
                   "scope (form-3 children are EIDs, not seat ids => [] by W8 design)", ok,
                   "n=%d" % len(scoped))
                dmD = rD["dormant"]
                rosterD = set(dmD["eids"])
                n_expD = sum(1 for e in a11D if e["asset_id"] in rosterD)
                ck("disease world, no extra assets: get_dormant_events => live, roster fully in "
                   "master, event count matches", dmD["status"] == "live"
                   and not (rosterD - stD.master) and dmD["n"] == n_expD,
                   "status=%s roster=%d n=%s exp=%d" % (dmD["status"], len(rosterD), dmD["n"], n_expD))
                ck("A11 per-EID via transport ok and round-trips (disease world)",
                   all(v["reason"] is None and v["rows"] == json.loads(matD.get(
                       "/assets/%s/schedule-state-events" % aid, "", Td)[1])
                       for aid, v in rD["a11"].items()), "n=%d" % len(rD["a11"]))

    # ---------------- C-SY1 負斷言 ----------------
    print("[9] zero-real-data negatives")
    ck("no _fixtures/_state/snapshot opens during materialize+serve", not spy.hits,
       "opens=%d hits=%s" % (spy.n, spy.hits[:5]))
    clean = subprocess.run(
        [sys.executable, "-c",
         "import sys; import wsynth_cmms; "
         "print(','.join(sorted(m for m in sys.modules "
         "if m.startswith(('board', 'mes_', 'app', 'snapshot')) or m == 'app')))"],
        capture_output=True, cwd=HERE, timeout=120,
        env={k: v for k, v in os.environ.items() if not k.startswith("BOARD_")})
    leaked = clean.stdout.decode("utf-8").strip()
    ck("import wsynth_cmms pulls in no Tendline Board / MES modules", clean.returncode == 0 and not leaked,
       leaked or clean.stderr.decode("utf-8", "replace")[-300:])
    return _finish(chk, None)


def _wire_invariants(ck, P, details, label):
    """每張單都應成立、與世界內容無關的 wire 性質。"""
    ds = list(details.values())
    ck("%s downtime_minutes never stored (null on every detail)" % label,
       ds and all(d["downtime_minutes"] is None for d in ds), "n=%d" % len(ds))
    ck("%s every history head: from null -> OPEN at opened_at" % label,
       all(d["status_history"][0]["from_status"] is None and
           d["status_history"][0]["to_status"] == "OPEN" and
           d["status_history"][0]["changed_at"] == d["opened_at"] for d in ds))
    ck("%s no history entry before opened_at; nothing after a terminal status" % label,
       all(all(h["changed_at"] >= d["opened_at"] for h in d["status_history"]) and
           all(h["to_status"] not in ("CLOSED", "CANCELLED", "VOIDED")
               for h in d["status_history"][:-1]) for d in ds))
    ck("%s person fields only role strings" % label,
       all((d["closed_by"] in (None,) + P.ROLES) and (d["assigned_person"] in (None,) + P.ROLES)
           and all(a in P.ROLES for a in d["assignees"])
           and all(h["source_actor"] in P.ROLES for h in d["status_history"])
           and all(x["source_actor"] in P.ROLES for x in d["notes"]) for d in ds))
    ck("%s hold_reason on WO only while ON_HOLD" % label,
       all((d["hold_reason"] is not None) == (d["status"] == "ON_HOLD") for d in ds))
    ck("%s closed_at set iff a CLOSED transition exists" % label,
       all((d["closed_at"] is not None) ==
           any(h["to_status"] == "CLOSED" for h in d["status_history"]) for d in ds))


def _w8_acceptance(ck, wo, details, label):
    ck("%s every WO: fetch_pm_wo_detail and private _http_get_json path both ok and equal" % label,
       wo and all(r["pm_reason"] is None and r["private_reason"] is None and r["private_same"]
                  for r in wo.values()), "n=%d" % len(wo))
    ck("%s every detail accepted by normalize_wo" % label, all(r.get("normalize_ok") for r in wo.values()))
    ck("%s normalize_notes keeps every note" % label,
       all(r["notes_raw"] == r["notes_norm"] for r in wo.values()))
    ck("%s normalize_scope_events drops nothing" % label,
       all(r["scope_dropped"] == 0 for r in wo.values())
       and all(r["scope_n"] == len(details[int(n)]["scope_events"]) for n, r in wo.items()))
    ck("%s wo_active_window defined for every WO" % label,
       all(r["window"] is not None for r in wo.values()))
    ck("%s pra direction gate never blocks (pra_block_reason None)" % label,
       all(r["pra_block"] is None for r in wo.values()))
    cancelled = [n for n, d in details.items() if d["status"] == "CANCELLED" and str(n) in wo]
    ck("%s cancelled WOs recognised by wo_is_cancelled" % label,
       all(wo[str(n)]["cancelled"] for n in cancelled), "n=%d" % len(cancelled))


def _seed_marker_checks(ck, M, P, out):
    """種子目錄來源＝委派 `wsynth_seed`（SPEC §5.1／§5.4-8～11）：測試世界標記、日檔集合、
    非 UTF-8、續讀指紋。所有錯誤類別以 `wsynth_seed` 為準。"""
    import hashlib
    import shutil
    import wsynth_seed as S

    def read_man(d):
        with open(os.path.join(d, "seed", "seed-manifest.json"), "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))

    def write_man(d, m):
        with open(os.path.join(d, "seed", "seed-manifest.json"), "wb") as fh:
            fh.write(json.dumps(m).encode("utf-8"))

    def fresh(tag):
        d = out + "-" + tag
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(out, d)
        return d

    def outcome(fn):
        try:
            fn()
            return "ok"
        except S.SeedRejected as exc:
            return "rejected:" + exc.code
        except M.SeedDirInvalid:
            return "invalid"

    man = read_man(out)
    ck("marker key aligned with the contract (TEST_MARKER_KEYS[0] in manifest optional keys)",
       M.TEST_MARKER_KEYS[0] in S.MANIFEST_OPTIONAL_KEYS)
    ck("default-flag world is not a test world and carries no marker",
       not M.manifest_is_test_world(man) and M.TEST_MARKER_KEYS[0] not in man)
    base_src = M.SeedDirSource(out)
    ck("seed-dir read of a clean world: no errors, nothing skipped",
       base_src.report["ok"] and base_src.report["skipped_unknown_kind"] == 0
       and base_src.report["skipped_reserved"] == 0, base_src.report["codes"])

    # 測試世界標記：預設拒讀（wsynth_seed.SeedRejected test_world）、明確參數允許
    res = []
    for variant in ("marker", "flags+marker"):
        d = fresh("marked")
        m2 = dict(man)
        m2[M.TEST_MARKER_KEYS[0]] = True
        if variant == "flags+marker":
            f = dict(m2.get("flags") or P.DEFAULT_FLAGS)
            f[sorted(f)[0]] = not f[sorted(f)[0]]
            m2["flags"] = f
        write_man(d, m2)
        res.append((variant, outcome(lambda: M.SeedDirSource(d)),
                    outcome(lambda: M.SeedDirSource(d, allow_test_world=True))))
        shutil.rmtree(d, ignore_errors=True)
    ck("test-world manifest refused by default (SeedRejected test_world), accepted with "
       "allow_test_world=True", all(r == "rejected:test_world" and a == "ok" for _v, r, a in res), res)

    # 日檔集合：少一個列出的檔 ⇒ invalid；多一個未列的檔 ⇒ invalid
    days = man["days"]
    d = fresh("missing")
    os.remove(os.path.join(d, "seed", days[-1]["file"]))
    miss = outcome(lambda: M.SeedDirSource(d))
    shutil.rmtree(d, ignore_errors=True)
    d = fresh("extra")
    last = days[-1]["day"]
    extra_name = S.day_file_name((_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat())
    with open(os.path.join(d, "seed", extra_name), "wb") as fh:
        fh.write(b"")
    extra = outcome(lambda: M.SeedDirSource(d))
    shutil.rmtree(d, ignore_errors=True)
    ck("day-file set must be complete: missing listed file / unlisted extra file => invalid",
       miss == "invalid" and extra == "invalid", (miss, extra))

    # 非 UTF-8：manifest 壞 ⇒ invalid（不拋未處理例外）；日檔一行壞 ⇒ 該行丟、記 encoding、其餘照讀
    d = fresh("enc")
    with open(os.path.join(d, "seed", "seed-manifest.json"), "wb") as fh:
        fh.write(b"\xff\xfe{")
    enc_man = outcome(lambda: M.SeedDirSource(d))
    shutil.rmtree(d, ignore_errors=True)
    d = fresh("enc2")
    tgt = days[-1]
    fp = os.path.join(d, "seed", tgt["file"])
    with open(fp, "rb") as fh:
        raw = fh.read()
    lines = raw.split(b"\n")
    lines[0] = lines[0].replace(b'"actor":"', b'"actor":"\xff', 1)
    raw2 = b"\n".join(lines)
    with open(fp, "wb") as fh:
        fh.write(raw2)
    m3 = dict(man)
    m3["days"] = [dict(x, sha256=hashlib.sha256(raw2).hexdigest()) if x["file"] == tgt["file"] else x
                  for x in man["days"]]
    write_man(d, m3)
    try:
        src = M.SeedDirSource(d)
        enc_line = ("ok", {"encoding", "manifest_mismatch"} <= set(src.report["codes"])
                    and src.report["rejected"] is None,
                    len(src.report["events"]) == len(base_src.report["events"]) - 1)
    except Exception as exc:                        # noqa: BLE001
        enc_line = ("raised " + type(exc).__name__ + ": " + str(exc)[:200], False, False)
    shutil.rmtree(d, ignore_errors=True)
    ck("non-UTF-8: manifest => invalid without crash; bad day line => dropped, 'encoding' (+ its "
       "day-count cascade) recorded, rest read", enc_man == "invalid" and enc_line == ("ok", True, True), (enc_man, enc_line))

    # 續讀指紋：同一世界 reload ok；指紋不符 ⇒ SeedRejected state_mismatch
    same = outcome(base_src.reload)
    other = dict(base_src.fingerprint)
    other["generator_version"] = other["generator_version"] + "-other"
    mism = outcome(lambda: M.SeedDirSource(out, fingerprint=other))
    ck("resume fingerprint: reload ok; mismatching fingerprint => SeedRejected state_mismatch",
       same == "ok" and mism == "rejected:state_mismatch", (same, mism))


def _replay_kind_checks(ck, M, world, T):
    """保留 kind 與未知 kind：物化器（記憶體世界路徑）跳過並分開計數，回應不變。"""
    import wsynth_seed as S
    evs = list(world["events"])
    last = evs[-1]
    at = min(e["at"] for e in evs if e["kind"] != "asset.define")
    extra = []
    for i, k in enumerate(list(S.RESERVED_KINDS) + ["x.future_kind"]):
        extra.append({"schema": last["schema"], "world_id": last["world_id"], "seq": last["seq"] + 1 + i,
                      "at": at, "kind": k, "asset_id": last["asset_id"], "actor": last["actor"]})
    st0 = M.replay(evs, T)
    st1 = M.replay(evs + extra, T)
    ck("replay: reserved kinds skipped and counted apart from unknown kinds",
       st1.skipped_reserved == len(S.RESERVED_KINDS) and st1.skipped_unknown_kind == 1
       and st0.skipped_reserved == 0 and st0.skipped_unknown_kind == 0)
    ck("replay: skipped kinds leave every response unchanged",
       all(M.route(st0, p, q) == M.route(st1, p, q) for p, q in _battery(st0)))
    # seed v1.1：inventory.item ⇒ 料號主檔（名稱來源），不進資產主檔、不計保留；wire 形不因它改變
    names = M.item_names(st0)
    part_codes = {p["item_code"] for w in st0.wos.values() for p in w["part"]}
    ck("replay v1.1: item master from inventory.item covers every replayed wo.part code, names non-empty, "
       "sentinel not in the asset master",
       names and part_codes <= set(names) and all(v.strip() for v in names.values())
       and S.ITEM_ASSET_ID not in st0.master, (len(names), sorted(part_codes - set(names))[:3]))
    no_items = [e for e in evs if e["kind"] != "inventory.item"]
    st2 = M.replay(no_items, T)
    ck("replay v1.1: dropping every inventory.item changes no wire response (names are a side table)",
       not st2.items and all(M.route(st0, p, q) == M.route(st2, p, q) for p, q in _battery(st0)))


def _source_literal_scan(ck):
    """原始碼不含站名、機台名、EID、編號字面值。"""
    import mes_timeline as mt
    import board_eqmeeting as wem
    names = set(mt.EQP_STATIONS)
    for _area, name, _eid in wem.EQMT_CATALOG:
        names.add(name)
    for fn in ("wsynth_cmms.py", "wsynth_cmms_selftest.py"):
        with open(os.path.join(HERE, fn), "r", encoding="utf-8") as fh:
            src = fh.read()
        eids = re.findall(r"EID-\d", src)
        nums = re.findall(r"(?<![\w.])\d{5,}(?![\w.])", src)
        hits = sorted(n for n in names if len(n) >= 3 and n in src)
        ck("%s: no EID / 5+ digit / station or machine name literals" % fn,
           not eids and not nums and not hits, (eids[:3], nums[:3], hits[:5]))


def _finish(chk, spy):
    print("[10] source literals")
    _source_literal_scan(chk.ck)
    bad = chk.failed
    print("wsynth_cmms selftest: %d checks, %d failed" % (len(chk.rows), len(bad)))
    for name, _ok, info in bad:
        print("  FAILED:", name, info)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
