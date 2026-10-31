# -*- coding: utf-8 -*-
"""
wsynth_compare.py — 種子重播逐欄比對工具（A18b ④／Board 待辦 T3；SPEC-SYNTHLINE §5.9）
=================================================================================

一句話：把同一份 `tendline.seed.v1` 種子（目錄或 golden fixture）在**同一個時點 T** 用 Board 物化器
（`wsynth_cmms`）產出三種 wire 形，與「對方」（Tendline Work／cmms 的物化器）交回的 JSON
**逐欄比對**，印出報告（完全相同的鍵／值不同的鍵〔路徑與兩邊值〕／只在一邊出現的鍵）。

**離線、零網路、零 MES**：只讀三樣東西——呼叫端指定的種子（目錄或 fixture 檔）、`--other-dir`
（對方輸出）、（selftest 才有）暫存目錄。不開 socket（selftest 的「與 loopback 伺服器位元組相同」
交叉檢查除外，只綁 127.0.0.1）。不改任何既有模組的行為；本檔只 import `wsynth_clock`／`wsynth_cmms`／
`wsynth_seed`（selftest 另用 `wsynth_world`／`wsynth_params`）。

三個模式
  1) 比對（預設）：  --seed PATH --at T --other-dir DIR            → 報告＋退出碼
  2) 產 Board 期望輸出： --seed PATH --at T --emit-expected DIR         → 寫約定檔名的 JSON＋compare-manifest.json
  3) 自測：          --selftest

退出碼：0＝無非預期差異／1＝有非預期差異／2＝輸入錯誤（路徑、JSON、T、種子被拒讀、T 與對方 manifest 不符…）。

檔名約定（`--emit-expected` 寫出、`--other-dir` 讀入；**對方請照同名存檔**）
  active-in.json             GET /work-orders/active-in?start=&end=            （start/end 見 manifest）
  detail-<work_order_no>.json GET /work-orders/<no>/detail                      （每張在 T 已曝光的單一檔）
  a11-<asset_id>.json        GET /assets/<asset_id>/schedule-state-events      （裸 list；母機與座各一檔，見下）
  a11-batch.json             GET /assets/schedule-state?eids=&as_of=           （{items, unknown}）
  compare-manifest.json      （Board 寫；對方可原樣複製回來，供比對工具取同一組查詢參數；選用）
  · 內容＝端點回應本文（JSON；比對時重新解析，空白／鍵序不影響）。其他檔名一律忽略（報告列出）。
  · A11 兩種查詢（§2.3 #9）：對**每台有 A11 事件的母機**查一次（a11-<母機>.json），並對它每個
    `nest:<n>` lane 的座 id（`<母機>-N<n>`）再查一次（a11-<母機>-N<n>.json）。Board 的座查詢恆回 `[]`。
  · 對方若多出 Board 沒有的 detail／a11 檔（例如晚於 T 的單）＝「只在對方」（非預期）。

時點 T：`--at` 收 UTC `Z`（`2026-07-03T06:45:00Z`）、別名 `T1`／`T2`（草擬值，對應 golden fixture）、
`exposed`（種子 manifest 的 exposed_until）。

正規化（各自可關閉）與預期差異——見 `--help` 與 SPEC-SYNTHLINE §5.9。
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import os
import re
import sys
import tempfile
import urllib.parse

import wsynth_clock as C
import wsynth_cmms as K
import wsynth_seed as _seed

SCHEMA_MANIFEST = "wsynth.compare.expected.v1"
SCHEMA_REPORT = "wsynth.compare.report.v1"
MANIFEST_NAME = "compare-manifest.json"
FILE_ACTIVE_IN = "active-in.json"
FILE_BATCH = "a11-batch.json"

#: 草擬時點（A18b 回覆 T3；Fable 未定稿，golden fixture 專用——別的世界請直接給 Z 值）
DRAFT_TIMES = {
    "T1": "2026-07-03T06:45:00Z",     # 一鍵結單已 CLOSED／開單即部分座且已追加範圍的單正在 ON_HOLD TEST_RUN／剛復工／IN_PROGRESS
    "T2": "2026-07-03T16:00:00Z",     # golden exposed_until（全帳）
}

FORMS = ("active-in", "detail", "a11", "a11-seat", "a11-batch")
FORM_TITLE = {
    "active-in": "active-in      GET /work-orders/active-in",
    "detail": "detail         GET /work-orders/{no}/detail",
    "a11": "a11           GET /assets/{parent}/schedule-state-events",
    "a11-seat": "a11-seat      GET /assets/{parent}-N<n>/schedule-state-events",
    "a11-batch": "a11-batch     GET /assets/schedule-state",
}

BATCH_CAP = K.BATCH_MAX_EIDS

_DETAIL_FILE_RE = re.compile(r"^detail-([0-9]+)\.json$")
_A11_FILE_RE = re.compile(r"^a11-([A-Za-z0-9_.-]+)\.json$")
_SEAT_ID_RE = re.compile(r"-N[1-9][0-9]*$")
_NEST_RE = re.compile(r"^nest:([1-9][0-9]*)$")
_ROLE_RE = re.compile(r"^role:([a-z][a-z0-9_]*)$")
_HUMAN_SEED_RE = re.compile(r"^human:seed-([a-z][a-z0-9_]*)$")

#: 出現「人」字串的鍵（actor 對映只動這些；`assignees` 是 list[str]）
ACTOR_KEYS = ("source_actor", "assigned_person", "closed_by")
ACTOR_LIST_KEYS = ("assignees",)


class InputError(Exception):
    """輸入錯誤（退出碼 2）。"""


# ===========================================================================
# 預期差異表（來源：cmms 回覆 §2.3／§3 與 Board A18b 回覆 Q3／Q5-c／Q8／#9）
# ===========================================================================
# 每條：id、適用 wire 形（"*"＝全部）、一般化路徑前綴（索引／鍵標籤一律寫成 `[]`）、說明、條件。
# 命中 ⇒ 該筆差異列為「預期差異」：照樣列出兩邊值、但不使退出碼為 1。

def _board_empty(ctx):
    return ctx.get("board_empty", False)


EXPECTED_RULES = (
    {"id": "Q3-diagnosis", "forms": ("detail",), "paths": ("$.diagnosis",), "cond": None,
     "note": "cmms v0 does not store diagnosis; Board page never prints it while a journal exists (A18b Q3)"},
    {"id": "Q5c-people", "forms": ("detail",),
     "paths": ("$.assignees", "$.assigned_person", "$.closed_by"), "cond": None,
     "note": "assignees/assigned_person/closed_by are derived differently "
             "(Board from event roles; cmms from asset owner, none on synthetic machines; closed_by not written "
             "for new orders) (A18b Q5-c, cmms reply s3)"},
    {"id": "Q8-asset_type", "forms": ("*",), "paths": ("$.asset_type", "$.items[].asset_type"), "cond": None,
     "note": "cmms uses a fixed default asset_type; Board wire has no such field (A18b Q8)"},
    {"id": "N9-seat-query", "forms": ("a11-seat",), "paths": ("$",), "cond": _board_empty,
     "note": "seat-id query: Board materializer hangs seat-level A11 on the parent (query returns []); "
             "cmms books them on the seat (A18b s2.3 #9)"},
)


def _gen_path(path):
    """`$.items[wo=<no>].status_history[2].x` → `$.items[].status_history[].x`。"""
    return re.sub(r"\[[^\]]*\]", "[]", path)


def _path_match(gp, pat):
    if pat == "$":
        return True
    return gp == pat or gp.startswith(pat + ".") or gp.startswith(pat + "[]")


def classify(form, gp, kind, ctx, extra_rules=(), rules_on=True):
    """一筆非相同紀錄 → `(expected: bool, rule_id|None, note|None)`。kind ∈ differ／only_board／only_other。"""
    if not rules_on:
        return False, None, None
    for r in tuple(EXPECTED_RULES) + tuple(extra_rules):
        if r["forms"] != ("*",) and form not in r["forms"]:
            continue
        if r.get("cond") is not None and not r["cond"](ctx):
            continue
        if any(_path_match(gp, p) for p in r["paths"]):
            return True, r["id"], r["note"]
    return False, None, None


# ===========================================================================
# 1) 種子載入（目錄或 golden fixture）
# ===========================================================================

class Loaded:
    """已載入的種子：事件（依 seq）、世界資訊。"""

    def __init__(self, events, *, world_id, generator_version=None, horizon_start_z=None,
                 exposed_until_z=None, label="", warnings=()):
        self.events = list(events)
        self.world_id = world_id
        self.generator_version = generator_version
        ats = [e["at"] for e in self.events]
        self.horizon_start_z = horizon_start_z or (min(ats) if ats else None)
        self.exposed_until_z = exposed_until_z or (max(ats) if ats else None)
        self.label = label
        self.warnings = list(warnings)


def load_seed(path, *, golden_index=0, allow_test_world=False):
    """`path` 是目錄 ⇒ 種子目錄（`wsynth_cmms.SeedDirSource`：委派 `wsynth_seed` 驗證，拒絕 repo 內與真資料形路徑）；
    是檔案 ⇒ golden fixture JSON 的 `golden[golden_index]`（同樣經 `wsynth_seed.load_ledger` 驗過）。"""
    if not os.path.exists(path):
        raise InputError("seed path does not exist: %s" % path)
    try:
        if os.path.isdir(path):
            src = K.SeedDirSource(path, allow_test_world=allow_test_world)
            errs = [e for e in src.report.get("errors", ())]
            man = _read_dir_manifest(path)
            return Loaded(src._events, world_id=src.world_id, generator_version=man.get("generator_version"),
                          horizon_start_z=man.get("horizon_start"),
                          exposed_until_z=C.to_utc_z(src.exposed_until),
                          label="seed-dir", warnings=_seed_warnings(errs))
        with open(path, "rb") as fh:
            fx = json.loads(fh.read().decode("utf-8"))
        gold = fx["golden"][int(golden_index)]
        man, evs = gold["manifest"], gold["events"]
        rep = _seed.load_ledger(evs, manifest=man, complete=True, allow_test_world=allow_test_world)
        return Loaded(rep["events"], world_id=man["world_id"],
                      generator_version=man.get("generator_version"),
                      horizon_start_z=man.get("horizon_start"), exposed_until_z=man.get("exposed_until"),
                      label="golden[%d]" % int(golden_index), warnings=_seed_warnings(rep.get("errors", ())))
    except InputError:
        raise
    except (K.SeedDirRefused, K.SeedDirInvalid, _seed.SeedRejected) as exc:
        raise InputError("seed refused: %s" % exc) from exc
    except (OSError, ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError) as exc:
        raise InputError("seed unreadable (%s): %s" % (type(exc).__name__, exc)) from exc


def _read_dir_manifest(path):
    real = os.path.join(path, "seed") if os.path.isdir(os.path.join(path, "seed")) else path
    with open(os.path.join(real, "seed-manifest.json"), "rb") as fh:
        man, _errs = _seed.parse_manifest_bytes(fh.read())
    return man if isinstance(man, dict) else {}


def _seed_warnings(errors):
    errors = list(errors)
    if not errors:
        return []
    codes = sorted({e["code"] for e in errors})
    return ["seed ledger has %d row/ledger-level error(s) (bad rows dropped): %s" % (len(errors), ", ".join(codes))]


def resolve_at(s, loaded):
    """`--at` → UTC Z 字串。"""
    if s is None:
        raise InputError("--at is required (UTC Z, e.g. 2026-07-03T06:45:00Z; or T1/T2/exposed)")
    s = s.strip()
    if s in DRAFT_TIMES:
        return DRAFT_TIMES[s]
    if s == "exposed":
        if not loaded.exposed_until_z:
            raise InputError("--at exposed: seed has no exposed_until")
        return loaded.exposed_until_z
    if C.is_utc_z(s):
        try:
            C.from_utc_z(s)
        except ValueError as exc:
            raise InputError("--at is not a valid UTC Z time: %r (%s)" % (s, exc)) from exc
        return s
    raise InputError("--at must be UTC 'Z' form YYYY-MM-DDTHH:MM:SSZ (or T1/T2/exposed), got %r" % s)


# ===========================================================================
# 2) 請求集合與 Board 期望輸出
# ===========================================================================

def default_params(loaded, st):
    """查詢參數預設：active-in 窗＝[世界起點, T]（台北 naive，與 route 同格式）；batch＝全部 define 機台（≤200）、as_of＝T 的台北日。"""
    start = C.from_utc_z(loaded.horizon_start_z)
    eids = sorted(st.assets)
    return {
        "window_start": start.strftime(K.ACTIVE_IN_TS_FMT),
        "window_end": st.t.strftime(K.ACTIVE_IN_TS_FMT),
        "batch_eids": eids[:BATCH_CAP],
        "as_of": st.t.date().isoformat(),
    }


def request_for_name(name, params):
    """檔名 → `(form, path, query)`；不認得 ⇒ None。"""
    if name == FILE_ACTIVE_IN:
        q = urllib.parse.urlencode([("start", params["window_start"]), ("end", params["window_end"])])
        return "active-in", K.LIST_PATH, q
    if name == FILE_BATCH:
        pairs = []
        if params.get("batch_eids"):
            pairs.append(("eids", ",".join(params["batch_eids"])))
        pairs.append(("as_of", params["as_of"]))
        return "a11-batch", K.BATCH_PATH, urllib.parse.urlencode(pairs)
    m = _DETAIL_FILE_RE.match(name)
    if m:
        return "detail", "/work-orders/%d/detail" % int(m.group(1)), ""
    m = _A11_FILE_RE.match(name)
    if m:
        aid = m.group(1)
        form = "a11-seat" if _SEAT_ID_RE.search(aid) else "a11"
        return form, "/assets/%s/schedule-state-events" % urllib.parse.quote(aid, safe=""), ""
    return None


def required_names(st):
    """Board 端在此 T 應有的檔名集合（有序）。"""
    names = [FILE_ACTIVE_IN]
    names += ["detail-%d.json" % w for w in sorted(st.wos)]
    mothers = sorted({e["asset_id"] for e in st.a11})
    seats = set()
    for e in st.a11:
        m = _NEST_RE.match(e.get("sub_unit") or "")
        if m:
            sid = "%s-N%s" % (e["asset_id"], m.group(1))
            if sid in st.master:
                seats.add(sid)
    names += ["a11-%s.json" % a for a in mothers]
    names += ["a11-%s.json" % a for a in sorted(seats)]
    names.append(FILE_BATCH)
    return names


def board_fetch(st, params, name):
    """Board 物化器的回應：回 `(form, payload_bytes|None, http_status, request_str)`。404 ⇒ bytes None。"""
    req = request_for_name(name, params)
    if req is None:
        return None
    form, path, query = req
    code, body = K.route(st, path, query)
    rs = path + ("?" + query if query else "")
    if code == 404:
        return form, None, code, rs
    if code != 200:
        raise InputError("Board materializer answered %d for %s" % (code, rs))
    return form, body, code, rs


def build_expected(st, params):
    """→ {檔名: (form, bytes, request_str)}（有序）。"""
    out = {}
    for name in required_names(st):
        r = board_fetch(st, params, name)
        if r is None or r[1] is None:
            raise InputError("internal: required file %s has no Board payload" % name)
        out[name] = (r[0], r[1], r[3])
    return out


def build_manifest(loaded, st, params, expected):
    return {
        "schema": SCHEMA_MANIFEST,
        "world_id": loaded.world_id,
        "generator_version": loaded.generator_version,
        "t_utc": st.t_z,
        "t_taipei": st.t.isoformat(),
        "params": {"window_start": params["window_start"], "window_end": params["window_end"],
                   "batch_eids": list(params["batch_eids"]), "as_of": params["as_of"]},
        "files": {n: {"form": f, "request": rs} for n, (f, _b, rs) in expected.items()},
        "note": "Save each GET response body under the file name above (same T). "
                "Window/eids/as_of are the query parameters to use.",
    }


_EMIT_PATTERNS = (re.compile(r"^detail-[0-9]+\.json$"), re.compile(r"^a11-.+\.json$"),
                  re.compile(r"^active-in\.json$"), re.compile(r"^compare-manifest\.json$"))


def emit_expected(loaded, st, params, out_dir):
    """寫 Board 期望輸出到 `out_dir`。回寫出的檔名清單。輸出目錄不得在 repo 內（C-SY12，同 wsynth_emit）。"""
    try:
        real = K.check_seed_dir(out_dir)
    except K.SeedDirRefused as exc:
        raise InputError("output dir refused: %s" % exc) from exc
    expected = build_expected(st, params)
    os.makedirs(real, exist_ok=True)
    for n in os.listdir(real):          # 清掉舊的同約定檔（避免殘留上一個 T 的單）
        if any(p.match(n) for p in _EMIT_PATTERNS):
            os.remove(os.path.join(real, n))
    for n, (_f, body, _rs) in expected.items():
        with open(os.path.join(real, n), "wb") as fh:
            fh.write(body)
    man = build_manifest(loaded, st, params, expected)
    with open(os.path.join(real, MANIFEST_NAME), "wb") as fh:
        fh.write((json.dumps(man, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return sorted(expected) + [MANIFEST_NAME]


# ===========================================================================
# 3) 正規化
# ===========================================================================

def map_actor(v):
    """`human:seed-<x>` → `role:<x>`（其他原樣）。"""
    if isinstance(v, str):
        m = _HUMAN_SEED_RE.match(v)
        if m:
            return "role:" + m.group(1)
    return v


def _walk_actor(obj, stats):
    if isinstance(obj, dict):
        for k in list(obj):
            v = obj[k]
            if k in ACTOR_KEYS and isinstance(v, str):
                nv = map_actor(v)
                if nv != v:
                    stats["actor_mapped"] += 1
                obj[k] = nv
            elif k in ACTOR_LIST_KEYS and isinstance(v, list):
                for i, x in enumerate(v):
                    nx = map_actor(x)
                    if nx != x:
                        stats["actor_mapped"] += 1
                    v[i] = nx
            else:
                _walk_actor(v, stats)
    elif isinstance(obj, list):
        for x in obj:
            _walk_actor(x, stats)


def normalize(form, doc, opts, stats, side):
    """深拷貝後套用正規化。`opts`: actor_map(bool)、system_actors(set)。"""
    doc = copy.deepcopy(doc)
    if opts["actor_map"]:
        _walk_actor(doc, stats[side])
    sysa = opts["system_actors"]
    if form == "detail" and sysa and isinstance(doc, dict) and isinstance(doc.get("notes"), list):
        keep, dropped = [], 0
        for n in doc["notes"]:
            a = n.get("source_actor") if isinstance(n, dict) else None
            if a is not None and (a in sysa or map_actor(a) in sysa):
                dropped += 1
            else:
                keep.append(n)
        doc["notes"] = keep
        stats[side]["system_notes_excluded"] += dropped
    return doc


# ===========================================================================
# 4) 逐欄 diff
# ===========================================================================

def _label_fn(form, gp):
    if form == "active-in" and gp == "$.items":
        return lambda e: "wo=%s" % e.get("work_order_no")
    if form == "a11-batch" and gp == "$.items":
        return lambda e: "asset=%s" % e.get("asset_id")
    return None


def _leaf_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def diff_json(form, a, b):
    """Board 值 `a`、對方值 `b` → dict(same=[paths], differ=[(path, a, b)], only_board=[(path, a)], only_other=[(path, b)])。"""
    out = {"same": [], "differ": [], "only_board": [], "only_other": []}

    def rec(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            if not x and not y:
                out["same"].append(path)
                return
            for k in sorted(set(x) | set(y)):
                p = "%s.%s" % (path, k)
                if k in x and k in y:
                    rec(x[k], y[k], p)
                elif k in x:
                    out["only_board"].append((p, x[k]))
                else:
                    out["only_other"].append((p, y[k]))
        elif isinstance(x, list) and isinstance(y, list):
            if not x and not y:
                out["same"].append(path)
                return
            lab = _label_fn(form, _gen_path(path))
            if lab is not None and all(isinstance(e, dict) for e in x + y):
                lx, ly = [lab(e) for e in x], [lab(e) for e in y]
                if len(set(lx)) == len(lx) and len(set(ly)) == len(ly):
                    dx, dy = dict(zip(lx, x)), dict(zip(ly, y))
                    for k in sorted(set(dx) | set(dy)):
                        p = "%s[%s]" % (path, k)
                        if k in dx and k in dy:
                            rec(dx[k], dy[k], p)
                        elif k in dx:
                            out["only_board"].append((p, dx[k]))
                        else:
                            out["only_other"].append((p, dy[k]))
                    return
            for i in range(max(len(x), len(y))):
                p = "%s[%d]" % (path, i)
                if i < len(x) and i < len(y):
                    rec(x[i], y[i], p)
                elif i < len(x):
                    out["only_board"].append((p, x[i]))
                else:
                    out["only_other"].append((p, y[i]))
        else:
            if _leaf_eq(x, y):
                out["same"].append(path)
            else:
                out["differ"].append((path, x, y))

    rec(a, b, "$")
    return out


# ===========================================================================
# 5) 比對主流程
# ===========================================================================

def _read_other_dir(other_dir):
    if not os.path.isdir(other_dir):
        raise InputError("--other-dir is not a directory: %s" % other_dir)
    files, manifest, ignored = {}, None, []
    for n in sorted(os.listdir(other_dir)):
        full = os.path.join(other_dir, n)
        if not os.path.isfile(full):
            continue
        if n == MANIFEST_NAME:
            manifest = _load_json(full, n)
            continue
        if n.endswith(".json") and (n == FILE_ACTIVE_IN or n == FILE_BATCH or _DETAIL_FILE_RE.match(n)
                                    or _A11_FILE_RE.match(n)):
            files[n] = _load_json(full, n)
        else:
            ignored.append(n)
    return files, manifest, ignored


def _load_json(full, name):
    try:
        with open(full, "rb") as fh:
            return json.loads(fh.read().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise InputError("cannot read %s as JSON: %s" % (name, exc)) from exc


def _fresh_stats():
    return {"board": {"actor_mapped": 0, "system_notes_excluded": 0},
            "other": {"actor_mapped": 0, "system_notes_excluded": 0}}


def _empty_counts():
    return {"same": 0, "differ_unexpected": 0, "differ_expected": 0,
            "only_board_unexpected": 0, "only_board_expected": 0,
            "only_other_unexpected": 0, "only_other_expected": 0}


def compare(loaded, st, other_dir, params, opts, *, other_manifest=None, ignored=(), other_files=None):
    """主比對。回報告 dict（見模組檔頭）。`other_files`：{檔名: 已解析 JSON}。"""
    stats = _fresh_stats()
    sel = set(opts["forms"])
    required = [n for n in required_names(st) if request_for_name(n, params)[0] in sel]
    names = set(required)
    for n in other_files:
        r = request_for_name(n, params)
        if r is not None and r[0] in sel:
            names.add(n)

    forms = {f: {"files": [], "fields": {}, "counts": _empty_counts(),
                 "n_files": 0, "n_identical": 0, "n_with_diff": 0,
                 "n_missing_in_other": 0, "n_only_in_other": 0} for f in FORMS if f in sel}
    other_actors = set()

    for name in sorted(names, key=lambda n: (FORMS.index(request_for_name(n, params)[0]), n)):
        form, _p, _q = request_for_name(name, params)
        fr = forms[form]
        fr["n_files"] += 1
        board = board_fetch(st, params, name)
        board_doc = None if board is None or board[1] is None else json.loads(board[1].decode("utf-8"))
        request = board[3] if board else ""
        ent = {"file": name, "request": request, "status": "compared", "differ": [], "only_board": [],
               "only_other": [], "n_same": 0}
        has_other = name in other_files
        if board_doc is None and not has_other:
            continue
        if has_other and form == "detail" and isinstance(other_files[name], dict):
            for n in other_files[name].get("notes") or ():
                if isinstance(n, dict) and isinstance(n.get("source_actor"), str):
                    other_actors.add(n["source_actor"])
        if board_doc is None:                        # 只在對方
            ent["status"] = "only_in_other"
            ent["only_other"].append({"path": "$", "other": other_files[name], "expected": False,
                                      "rule": None, "note": "file exists only on the other side (Board has no such object at T)"})
            fr["n_only_in_other"] += 1
            fr["counts"]["only_other_unexpected"] += 1
        elif not has_other:                       # 只在 Board
            ent["status"] = "missing_in_other"
            ent["only_board"].append({"path": "$", "board": "<%d bytes>" % len(board[1]), "expected": False,
                                   "rule": None, "note": "file expected from the other side is missing"})
            fr["n_missing_in_other"] += 1
            fr["counts"]["only_board_unexpected"] += 1
        else:
            a = normalize(form, board_doc, opts, stats, "board")
            b = normalize(form, other_files[name], opts, stats, "other")
            ctx = {"board_empty": a == []}
            d = diff_json(form, a, b)
            ent["n_same"] = len(d["same"])
            fr["counts"]["same"] += len(d["same"])
            for p in d["same"]:
                fr["fields"].setdefault(_gen_path(p), _empty_counts())["same"] += 1
            for kind, recs in (("differ", d["differ"]), ("only_board", d["only_board"]),
                               ("only_other", d["only_other"])):
                for r in recs:
                    p = r[0]
                    gp = _gen_path(p)
                    exp, rid, note = classify(form, gp, kind, ctx, opts["extra_rules"], opts["rules_on"])
                    rec = {"path": p, "expected": exp, "rule": rid, "note": note}
                    if kind == "differ":
                        rec["board"], rec["other"] = r[1], r[2]
                    elif kind == "only_board":
                        rec["board"] = r[1]
                    else:
                        rec["other"] = r[1]
                    ent[kind].append(rec)
                    key = "%s_%s" % (kind, "expected" if exp else "unexpected")
                    fr["counts"][key] += 1
                    fr["fields"].setdefault(gp, _empty_counts())[key] += 1
        if ent["differ"] or ent["only_board"] or ent["only_other"]:
            fr["n_with_diff"] += 1
        else:
            fr["n_identical"] += 1
        fr["files"].append(ent)

    unexpected = sum(c["counts"][k] for c in forms.values()
                     for k in ("differ_unexpected", "only_board_unexpected", "only_other_unexpected"))
    expected_n = sum(c["counts"][k] for c in forms.values()
                     for k in ("differ_expected", "only_board_expected", "only_other_expected"))
    hints = list(loaded.warnings)
    if "detail" in sel and not opts["system_actors"]:
        cand = sorted(a for a in other_actors if not _ROLE_RE.match(map_actor(a)))
        hint = ("no --system-actor given: machine-level resume system journals are NOT filtered "
                "(A18b Q6; cmms identifies them by source_actor)")
        if cand:
            hint += "; non-seed actors seen in the other side's notes: %s" % ", ".join(cand)
        hints.append(hint)
    if loaded.exposed_until_z and st.t_z > loaded.exposed_until_z:
        hints.append("T (%s) is later than the seed's exposed_until (%s)" % (st.t_z, loaded.exposed_until_z))
    if not other_files:
        hints.append("no recognizable wire files found in the other directory")
    return {
        "schema": SCHEMA_REPORT,
        "world_id": loaded.world_id,
        "seed": loaded.label,
        "t_utc": st.t_z,
        "params": params,
        "options": {"actor_map": opts["actor_map"], "system_actors": sorted(opts["system_actors"]),
                    "expected_rules": opts["rules_on"], "forms": [f for f in FORMS if f in sel],
                    "extra_expected": [{"forms": list(r["forms"]), "paths": list(r["paths"])}
                                       for r in opts["extra_rules"]]},
        "normalization": {"actor_mapped_board": stats["board"]["actor_mapped"],
                          "actor_mapped_other": stats["other"]["actor_mapped"],
                          "system_notes_excluded_board": stats["board"]["system_notes_excluded"],
                          "system_notes_excluded_other": stats["other"]["system_notes_excluded"]},
        "other_manifest": other_manifest is not None,
        "ignored_files": list(ignored),
        "hints": hints,
        "forms": forms,
        "totals": {"unexpected": unexpected, "expected": expected_n},
        "exit": 1 if unexpected else 0,
    }


# ===========================================================================
# 6) 報告文字
# ===========================================================================

def _short(v, n=110):
    s = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return s if len(s) <= n else s[:n - 3] + "..."


def render_text(rep, *, show_same=False):
    L = []
    L.append("wsynth_compare  world_id=%s  seed=%s  T=%s" % (rep["world_id"], rep["seed"], rep["t_utc"]))
    o = rep["options"]
    L.append("normalization: actor-map=%s  system-actor=%s  expected-diff rules=%s  forms=%s" % (
        "on" if o["actor_map"] else "OFF",
        ",".join(o["system_actors"]) if o["system_actors"] else "(none)",
        "on" if o["expected_rules"] else "OFF", ",".join(o["forms"])))
    n = rep["normalization"]
    L.append("normalized: actor strings mapped Board=%d other=%d; system notes excluded Board=%d other=%d" % (
        n["actor_mapped_board"], n["actor_mapped_other"], n["system_notes_excluded_board"],
        n["system_notes_excluded_other"]))
    pr = rep["params"]
    L.append("query params: active-in start=%s end=%s | batch eids=%d as_of=%s" % (
        pr["window_start"], pr["window_end"], len(pr["batch_eids"]), pr["as_of"]))
    for h in rep["hints"]:
        L.append("NOTE: " + h)
    if rep["ignored_files"]:
        L.append("ignored files in the other dir: " + ", ".join(rep["ignored_files"]))
    for f in FORMS:
        fr = rep["forms"].get(f)
        if fr is None:
            continue
        c = fr["counts"]
        L.append("")
        L.append("=== wire form: %s" % FORM_TITLE[f])
        L.append("  files: %d compared, %d identical, %d with differences, %d missing in other, %d only in other" % (
            fr["n_files"], fr["n_identical"], fr["n_with_diff"], fr["n_missing_in_other"], fr["n_only_in_other"]))
        L.append("  keys: %d identical | value differs %d unexpected + %d expected | only-Board %d unexpected + %d expected "
                 "| only-other %d unexpected + %d expected" % (
                     c["same"], c["differ_unexpected"], c["differ_expected"], c["only_board_unexpected"],
                     c["only_board_expected"], c["only_other_unexpected"], c["only_other_expected"]))
        rows = []
        for gp in sorted(fr["fields"]):
            fc = fr["fields"][gp]
            bad = sum(v for k, v in fc.items() if k != "same")
            if bad or show_same:
                rows.append((gp, fc))
        if rows:
            L.append("  per-field (generalized path): same / differ(unexp,exp) / only-Board(unexp,exp) / only-other(unexp,exp)")
            for gp, fc in rows:
                L.append("    %-44s %5d / %d,%d / %d,%d / %d,%d" % (
                    gp, fc["same"], fc["differ_unexpected"], fc["differ_expected"],
                    fc["only_board_unexpected"], fc["only_board_expected"],
                    fc["only_other_unexpected"], fc["only_other_expected"]))
        for ent in fr["files"]:
            if not (ent["differ"] or ent["only_board"] or ent["only_other"]):
                if show_same:
                    L.append("  [%s] identical (%d keys)" % (ent["file"], ent["n_same"]))
                continue
            L.append("  [%s] %s (%d identical keys)" % (ent["file"], ent["status"], ent["n_same"]))
            for title, key in (("value differs", "differ"), ("only in Board", "only_board"), ("only in other", "only_other")):
                for r in ent[key]:
                    tag = "EXPECTED[%s]" % r["rule"] if r["expected"] else "UNEXPECTED"
                    if key == "differ":
                        vals = "Board=%s  other=%s" % (_short(r["board"]), _short(r["other"]))
                    elif key == "only_board":
                        vals = "Board=%s" % _short(r["board"])
                    else:
                        vals = "other=%s" % _short(r["other"])
                    extra = ("  -- " + r["note"]) if (r["note"] and not r["expected"]) else ""
                    L.append("    %-12s %-13s %s  %s%s" % (tag, title, r["path"], vals, extra))
        if show_same:
            L.append("  identical keys (generalized path: count):")
            for gp in sorted(fr["fields"]):
                if fr["fields"][gp]["same"]:
                    L.append("    %s: %d" % (gp, fr["fields"][gp]["same"]))
    t = rep["totals"]
    L.append("")
    L.append("RESULT: %d unexpected difference(s), %d expected difference(s) -> exit %d" % (
        t["unexpected"], t["expected"], rep["exit"]))
    return "\n".join(L) + "\n"


# ===========================================================================
# 7) CLI
# ===========================================================================

EPILOG = """\
file-name convention (written by --emit-expected, read from --other-dir; save the other side's responses under the same names):
  active-in.json                GET /work-orders/active-in?start=&end=
  detail-<work_order_no>.json   GET /work-orders/<no>/detail          one per order exposed at T
  a11-<asset_id>.json           GET /assets/<asset_id>/schedule-state-events (bare list); one per parent machine that has
                                A11 events AND one per seat id <parent>-N<n> (both queries are compared, A18b s2.3 #9)
  a11-batch.json                GET /assets/schedule-state?eids=&as_of=
  compare-manifest.json         optional; written by --emit-expected; carries T and the query parameters
expected differences (listed with both values, do not fail): diagnosis; assignees/assigned_person/closed_by; asset_type;
  seat-id A11 query where Board answers []   (--no-expected turns these off; --expect FORM:PATH adds one)
exit codes: 0 no unexpected difference | 1 unexpected difference | 2 input error
"""


def build_parser():
    ap = argparse.ArgumentParser(
        prog="wsynth_compare.py",
        description="Offline field-by-field comparison of the Tendline Board cmms materializer against the other side's "
                    "(Tendline Work / cmms) replay of the same seed at the same time T. No network, no MES.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="run the built-in self-test")
    ap.add_argument("--seed", metavar="PATH", help="seed directory (seed-manifest.json + day files) or golden fixture JSON file")
    ap.add_argument("--golden-index", type=int, default=0, help="which golden[] entry when --seed is a fixture file (default 0)")
    ap.add_argument("--allow-test-world", action="store_true", help="allow reading a test world")
    ap.add_argument("--at", metavar="T", help="time T: UTC 'Z' (2026-07-03T06:45:00Z), T1/T2 (draft times for the golden fixture), or 'exposed'")
    ap.add_argument("--emit-expected", metavar="DIR", help="write Board's expected output files (+compare-manifest.json) to DIR and exit")
    ap.add_argument("--other-dir", metavar="DIR", help="directory with the other side's JSON files (compare mode)")
    ap.add_argument("--forms", default=",".join(FORMS), help="comma list of wire forms to compare (default all): " + ",".join(FORMS))
    ap.add_argument("--no-actor-map", action="store_true", help="turn off normalization role:<x> <=> human:seed-<x>")
    ap.add_argument("--system-actor", action="append", default=[], metavar="VALUE",
                    help="source_actor of the machine-level resume system journal (repeatable); notes written by it are "
                         "excluded from both sides. If omitted, nothing is filtered and the report says so")
    ap.add_argument("--no-expected", action="store_true", help="treat expected differences as failures")
    ap.add_argument("--expect", action="append", default=[], metavar="FORM:PATH",
                    help="extra expected difference, e.g. a11:$[].recorded_at (path with [] for any index; FORM may be *)")
    ap.add_argument("--window-start", metavar="'YYYY-MM-DD HH:MM:SS'", help="active-in start (Taipei naive); default = seed start")
    ap.add_argument("--window-end", metavar="'YYYY-MM-DD HH:MM:SS'", help="active-in end (Taipei naive); default = T")
    ap.add_argument("--batch-eids", metavar="CSV", help="eids for the a11-batch query; default = every defined machine (max %d)" % BATCH_CAP)
    ap.add_argument("--as-of", metavar="YYYY-MM-DD", help="as_of for the a11-batch query; default = Taipei date of T")
    ap.add_argument("--show-same", action="store_true", help="also list identical files and identical key counts")
    ap.add_argument("--json", metavar="FILE", help="also write the full report as JSON to FILE")
    return ap


def _parse_extra(specs):
    out = []
    for s in specs:
        if ":" not in s:
            raise InputError("--expect needs FORM:PATH, got %r" % s)
        f, p = s.split(":", 1)
        if f != "*" and f not in FORMS:
            raise InputError("--expect: unknown form %r" % f)
        if not p.startswith("$"):
            raise InputError("--expect: path must start with $, got %r" % p)
        out.append({"id": "user", "forms": (f,), "paths": (p,), "cond": None, "note": "user-declared expected difference"})
    return out


def _date_ok(s):
    try:
        _dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _ts_ok(s):
    try:
        _dt.datetime.strptime(s, K.ACTIVE_IN_TS_FMT)
        return True
    except ValueError:
        return False


def run(args):
    """比對／產出主流程；拋 `InputError` ⇒ 呼叫端轉退出碼 2。回退出碼。"""
    if not args.seed:
        raise InputError("--seed is required")
    loaded = load_seed(args.seed, golden_index=args.golden_index, allow_test_world=args.allow_test_world)
    tz = resolve_at(args.at, loaded)
    st = K.replay(loaded.events, C.from_utc_z(tz))
    params = default_params(loaded, st)

    other_files = other_manifest = None
    ignored = []
    if args.other_dir and not args.emit_expected:
        other_files, other_manifest, ignored = _read_other_dir(args.other_dir)
        if other_manifest is not None:
            if not isinstance(other_manifest, dict):
                raise InputError("%s is not a JSON object" % MANIFEST_NAME)
            if other_manifest.get("t_utc") not in (None, tz):
                raise InputError("T mismatch: %s in the other dir says %s but --at resolved to %s"
                                 % (MANIFEST_NAME, other_manifest.get("t_utc"), tz))
            if other_manifest.get("world_id") not in (None, loaded.world_id):
                raise InputError("world_id mismatch: %s in the other dir says %s but the seed is %s"
                                 % (MANIFEST_NAME, other_manifest.get("world_id"), loaded.world_id))
            mp = other_manifest.get("params")
            if isinstance(mp, dict):
                for k in ("window_start", "window_end", "batch_eids", "as_of"):
                    if k in mp:
                        params[k] = mp[k]
    # CLI 覆寫（最優先）
    for opt, key in (("window_start", "window_start"), ("window_end", "window_end"), ("as_of", "as_of")):
        v = getattr(args, opt)
        if v:
            if (key == "as_of" and not _date_ok(v)) or (key != "as_of" and not _ts_ok(v)):
                raise InputError("--%s has a bad format: %r" % (opt.replace("_", "-"), v))
            params[key] = v
    if args.batch_eids:
        params["batch_eids"] = [e.strip() for e in args.batch_eids.split(",") if e.strip()][:BATCH_CAP]

    if args.emit_expected:
        files = emit_expected(loaded, st, params, args.emit_expected)
        print("wrote %d file(s) to %s (world_id=%s, T=%s)" % (len(files), args.emit_expected, loaded.world_id, tz))
        return 0

    if not args.other_dir:
        raise InputError("--other-dir is required in compare mode (or use --emit-expected DIR / --selftest)")
    forms = [f.strip() for f in args.forms.split(",") if f.strip()]
    bad = [f for f in forms if f not in FORMS]
    if bad or not forms:
        raise InputError("--forms: unknown form(s) %s (valid: %s)" % (bad or "(empty)", ",".join(FORMS)))
    opts = {"actor_map": not args.no_actor_map, "system_actors": set(args.system_actor),
            "rules_on": not args.no_expected, "extra_rules": _parse_extra(args.expect), "forms": forms}
    rep = compare(loaded, st, args.other_dir, params, opts, other_manifest=other_manifest,
                  ignored=ignored, other_files=other_files)
    sys.stdout.write(render_text(rep, show_same=args.show_same))
    if args.json:
        try:
            with open(args.json, "wb") as fh:
                fh.write((json.dumps(rep, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        except OSError as exc:
            raise InputError("cannot write --json file: %s" % exc) from exc
    return rep["exit"]


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = build_parser()
    args = ap.parse_args(argv)           # 參數錯 ⇒ argparse 自己以 2 結束
    if args.selftest:
        return _selftest()
    try:
        return run(args)
    except InputError as exc:
        sys.stderr.write("wsynth_compare: input error: %s\n" % exc)
        return 2


# ===========================================================================
# 8) 自測
# ===========================================================================

def _selftest():
    import contextlib
    import io
    import subprocess
    import urllib.request

    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.join(here, "_fixtures", "tendline.seed.v1.json")
    n_ok, fails = [0], []

    def ck(name, cond, detail=""):
        if cond:
            n_ok[0] += 1
        else:
            fails.append("%s %s" % (name, detail))
            print("FAIL: %s %s" % (name, detail))

    def cli(argv):
        """進程內跑 main；回 (exit, stdout, stderr)。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = main(argv)
            except SystemExit as exc:               # argparse 錯誤
                rc = exc.code
        return rc, out.getvalue(), err.getvalue()

    def rjson(path):
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))

    def wjson(path, obj):
        with open(path, "wb") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def run_cmp(seed, at, other, extra=(), jpath=None):
        argv = ["--seed", seed, "--at", at, "--other-dir", other] + list(extra)
        if jpath:
            argv += ["--json", jpath]
        rc, out, err = cli(argv)
        rep = rjson(jpath) if jpath and os.path.exists(jpath) else None
        return rc, out, err, rep

    def unexpected_paths(rep, form=None):
        r = []
        for f, fr in rep["forms"].items():
            if form and f != form:
                continue
            for ent in fr["files"]:
                for k in ("differ", "only_board", "only_other"):
                    for x in ent[k]:
                        if not x["expected"]:
                            r.append((ent["file"], k, x["path"]))
        return r

    def expected_recs(rep):
        r = []
        for fr in rep["forms"].values():
            for ent in fr["files"]:
                for k in ("differ", "only_board", "only_other"):
                    for x in ent[k]:
                        if x["expected"]:
                            r.append((ent["file"], k, x))
        return r

    # ---- (1) 常數／來源掃描 ------------------------------------------------
    with open(os.path.join(here, "wsynth_compare.py"), "rb") as fh:
        src = fh.read().decode("utf-8")
    code_only = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    ck("(1a) no EID literal in source", re.search(r"EID-[0-9]{5}", src) is None)
    ck("(1b) no work-order-number literal in source",
       re.search(r"(?<![0-9A-Za-z_.])7[1-9][0-9]{4}(?![0-9])", code_only) is None)
    ck("(1c) no socket/network import in the tool", re.search(r"^\s*(import|from)\s+(socket|http|requests)\b",
                                                              code_only, re.M) is None)
    ck("(1d) draft times are Z form", all(C.is_utc_z(v) for v in DRAFT_TIMES.values()))
    ck("(1e) every expected rule has id/forms/paths/note",
       all(r["id"] and r["forms"] and r["paths"] and r["note"] for r in EXPECTED_RULES))

    # ---- (2) golden 載入、T1／T2 語意 -------------------------------------
    loaded = load_seed(fixture)
    ck("(2a) golden loads (events present)", len(loaded.events) > 50, str(len(loaded.events)))
    t1z, t2z = DRAFT_TIMES["T1"], DRAFT_TIMES["T2"]
    ck("(2b) T2 == golden exposed_until", t2z == loaded.exposed_until_z, "%s vs %s" % (t2z, loaded.exposed_until_z))
    st1 = K.replay(loaded.events, C.from_utc_z(t1z))
    st2 = K.replay(loaded.events, C.from_utc_z(t2z))
    stat1 = {}
    for w in st1.wos.values():
        h = _history_status(w)
        stat1[h[0]] = stat1.get(h[0], 0) + 1
    ck("(2c) T1 has CLOSED / IN_PROGRESS / ON_HOLD orders", all(k in stat1 for k in ("CLOSED", "IN_PROGRESS", "ON_HOLD")), str(stat1))
    ck("(2d) T1 has an ON_HOLD with TEST_RUN", any(h[1] == "TEST_RUN" for h in (_history_status(w) for w in st1.wos.values())))
    ck("(2e) T1 exposes fewer orders than T2 or equal", len(st1.wos) <= len(st2.wos))
    ck("(2f) golden has seat-level A11 (nest lane) at T2", any(e.get("sub_unit") for e in st2.a11))

    # ---- (3) 暫存目錄：emit-expected、位元穩定、與伺服器位元相同 -----------
    with tempfile.TemporaryDirectory(prefix="wsynth_compare_") as td:
        seed_dir = os.path.join(td, "seed_in")
        os.makedirs(os.path.join(seed_dir, "seed"))
        gold = rjson(fixture)["golden"][0]
        for d in gold["manifest"]["days"]:
            day_evs = [e for e in gold["events"] if _seed.taipei_day(e["at"]) == d["day"]]
            with open(os.path.join(seed_dir, "seed", d["file"]), "wb") as fh:
                fh.write(_seed.day_bytes(day_evs))
        with open(os.path.join(seed_dir, "seed", "seed-manifest.json"), "wb") as fh:
            fh.write(json.dumps(gold["manifest"], ensure_ascii=False).encode("utf-8"))

        exp = {}
        for tag, tz in (("T1", t1z), ("T2", t2z)):
            d = os.path.join(td, "exp_" + tag)
            rc, out, err = cli(["--seed", fixture, "--at", tz, "--emit-expected", d])
            ck("(3a) emit-expected %s exits 0" % tag, rc == 0, "%s %s" % (rc, err))
            exp[tag] = d
        files1 = sorted(os.listdir(exp["T1"]))
        ck("(3b) emitted names follow the convention", all(
            n == MANIFEST_NAME or request_for_name(n, default_params(loaded, st1)) is not None for n in files1), str(files1))
        ck("(3c) emitted set has active-in, batch, detail*, a11*", FILE_ACTIVE_IN in files1 and FILE_BATCH in files1
           and any(n.startswith("detail-") for n in files1) and any(n.startswith("a11-EID") for n in files1))
        # 再產一次（另一個目錄）＝逐位元相同；由種子目錄產＝與由 fixture 產逐位元相同
        d_again = os.path.join(td, "exp_T1_again")
        cli(["--seed", fixture, "--at", t1z, "--emit-expected", d_again])
        d_dir = os.path.join(td, "exp_T1_fromdir")
        rc, _o, e = cli(["--seed", seed_dir, "--at", t1z, "--emit-expected", d_dir])
        ck("(3d) emit from a seed directory works", rc == 0, e)
        same_again = all(open(os.path.join(exp["T1"], n), "rb").read() == open(os.path.join(d_again, n), "rb").read()
                         for n in files1)
        ck("(3e) emit twice => byte-identical", same_again and sorted(os.listdir(d_again)) == files1)
        if rc == 0:
            ck("(3f) fixture source == seed-dir source (all files byte-identical)", sorted(os.listdir(d_dir)) == files1 and all(
                open(os.path.join(exp["T1"], n), "rb").read() == open(os.path.join(d_dir, n), "rb").read() for n in files1))
        # 與 loopback 伺服器的 HTTP 本文逐位元相同（同一個 route，守住「期望輸出＝假 cmms 端出的」）
        for tag, tz in (("T1", t1z), ("T2", t2z)):
            man = rjson(os.path.join(exp[tag], MANIFEST_NAME))
            src_obj = K.SeedDirSource(seed_dir)
            tt = C.from_utc_z(tz)
            try:
                with K.serve(src_obj, token="cmp-selftest", now_fn=lambda tt=tt: tt) as base:
                    bad = []
                    for n, meta in man["files"].items():
                        rq = urllib.request.Request(base + meta["request"], headers={"Authorization": "Bearer cmp-selftest"})
                        with urllib.request.urlopen(rq, timeout=10) as resp:
                            body = resp.read()
                        if body != open(os.path.join(exp[tag], n), "rb").read():
                            bad.append(n)
                    ck("(3g) %s emitted files == loopback HTTP bodies (%d files)" % (tag, len(man["files"])), not bad, str(bad[:3]))
            except OSError as exc:
                ck("(3g) %s loopback cross-check could run" % tag, False, str(exc))
        man1 = rjson(os.path.join(exp["T1"], MANIFEST_NAME))
        ck("(3h) manifest carries T, world_id, params, per-file request",
           man1["t_utc"] == t1z and man1["world_id"] == loaded.world_id and man1["params"]["batch_eids"]
           and all("request" in v for v in man1["files"].values()))
        ck("(3i) every a11 mother file has its seat files too",
           all(("a11-%s-N%s.json" % (e["asset_id"], _NEST_RE.match(e["sub_unit"]).group(1))) in files1
               for e in st1.a11 if e.get("sub_unit")))
        ck("(3j) emit dir inside the repo is refused (exit 2)", cli(["--seed", fixture, "--at", t1z, "--emit-expected",
                                                                        os.path.join(here, "_cmp_out_must_not_exist")])[0] == 2)
        ck("(3k) refused emit left nothing behind", not os.path.exists(os.path.join(here, "_cmp_out_must_not_exist")))

        # ---- (4) Board 對 Board 自比 ＝ 零差異 ---------------------------------
        for tag, tz in (("T1", t1z), ("T2", t2z)):
            rc, out, err, rep = run_cmp(fixture, tz, exp[tag], jpath=os.path.join(td, "r_self_%s.json" % tag))
            ck("(4a) self-compare %s exits 0" % tag, rc == 0, "%s %s" % (rc, err))
            ck("(4b) self-compare %s: zero differences of any kind" % tag,
               rep is not None and rep["totals"] == {"unexpected": 0, "expected": 0}, str(rep and rep["totals"]))
            ck("(4c) self-compare %s: all forms had files" % tag,
               rep is not None and all(fr["n_files"] > 0 for fr in rep["forms"].values()))
            ck("(4d) self-compare %s: identical keys counted" % tag, rep is not None and all(
                fr["counts"]["same"] > 0 for fr in rep["forms"].values()))
        rc, out, err = cli(["--seed", seed_dir, "--at", "exposed", "--other-dir", exp["T2"]])
        ck("(4e) seed dir + '--at exposed' vs T2 export exits 0", rc == 0, err)
        rc, out, err = cli(["--seed", fixture, "--at", "T2", "--other-dir", exp["T2"], "--show-same"])
        ck("(4f) --show-same prints identical key counts", rc == 0 and "identical keys (generalized path" in out)
        # 子進程 CLI 一次（退出碼經過真正的進程邊界）
        pr = subprocess.run([sys.executable, os.path.join(here, "wsynth_compare.py"), "--seed", fixture,
                             "--at", "T2", "--other-dir", exp["T2"]], capture_output=True, timeout=120,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        ck("(4g) subprocess CLI exit 0 on self-compare", pr.returncode == 0, str(pr.returncode))

        # ---- (5) 人工擾動：各自被正確歸類 -------------------------------
        base_dir = exp["T2"]

        def clone(tag):
            d = os.path.join(td, "pert_" + tag)
            os.makedirs(d)
            for n in os.listdir(base_dir):
                with open(os.path.join(base_dir, n), "rb") as fi, open(os.path.join(d, n), "wb") as fo:
                    fo.write(fi.read())
            return d

        det_files = sorted(n for n in os.listdir(base_dir) if n.startswith("detail-"))
        det_notes = [n for n in det_files if rjson(os.path.join(base_dir, n)).get("notes")]
        det_assignees = [n for n in det_files if rjson(os.path.join(base_dir, n)).get("assignees")]
        det_closed = [n for n in det_files if rjson(os.path.join(base_dir, n)).get("closed_by")]
        det_diag = [n for n in det_files if rjson(os.path.join(base_dir, n)).get("diagnosis")]
        ck("(5-pre) golden gives targets for every perturbation", det_notes and det_assignees and det_closed and det_diag
           and len(det_files) >= 3, "%d %d %d %d" % (len(det_notes), len(det_assignees), len(det_closed), len(det_diag)))

        # (5a) 改一個值
        d = clone("value")
        tgt = det_files[0]
        doc = rjson(os.path.join(d, tgt))
        old = doc["brief_description"]
        doc["brief_description"] = old + " (changed)"
        wjson(os.path.join(d, tgt), doc)
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5a.json"))
        up = unexpected_paths(rep) if rep else []
        ck("(5a) one changed value => exit 1, exactly one unexpected 'differ' at that path",
           rc == 1 and up == [(tgt, "differ", "$.brief_description")], "%s %s" % (rc, up))
        if rep:
            rec = [x for x in rep["forms"]["detail"]["files"][0]["differ"]][0]
            ck("(5a2) both values are listed", rec["board"] == old and rec["other"] == old + " (changed)")
        ck("(5a3) text report names the path and both values", "$.brief_description" in out and "(changed)" in out and "UNEXPECTED" in out)

        # (5b) 刪一個鍵（只在 Board）
        d = clone("delkey")
        doc = rjson(os.path.join(d, tgt))
        del doc["hold_reason"]
        wjson(os.path.join(d, tgt), doc)
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5b.json"))
        up = unexpected_paths(rep) if rep else []
        ck("(5b) deleted key => exit 1, one unexpected 'only_board'", rc == 1 and up == [(tgt, "only_board", "$.hold_reason")], "%s %s" % (rc, up))
        # 反向：對方多一個鍵
        d = clone("addkey")
        doc = rjson(os.path.join(d, tgt))
        doc["surprise_field"] = 1
        wjson(os.path.join(d, tgt), doc)
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5b2.json"))
        up = unexpected_paths(rep) if rep else []
        ck("(5b2) extra key on the other side => exit 1, 'only_other'", rc == 1 and up == [(tgt, "only_other", "$.surprise_field")], str(up))

        # (5c) actor 換形：role:<x> -> human:seed-<x>（全檔）
        d = clone("actor")
        n_swapped = [0]

        def swap(o):
            if isinstance(o, dict):
                return {k: swap(v) for k, v in o.items()}
            if isinstance(o, list):
                return [swap(x) for x in o]
            if isinstance(o, str):
                m = _ROLE_RE.match(o)
                if m:
                    n_swapped[0] += 1
                    return "human:seed-" + m.group(1)
            return o
        for n in os.listdir(d):
            if n != MANIFEST_NAME:
                wjson(os.path.join(d, n), swap(rjson(os.path.join(d, n))))
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5c.json"))
        ck("(5c) actor form swap with mapping on => zero differences, exit 0",
           rc == 0 and rep is not None and rep["totals"] == {"unexpected": 0, "expected": 0}, "%s %s" % (rc, rep and rep["totals"]))
        ck("(5c2) every swapped actor string was normalized", rep is not None and
           rep["normalization"]["actor_mapped_other"] == n_swapped[0] and n_swapped[0] > 10,
           "%s vs %s" % (rep and rep["normalization"], n_swapped[0]))
        rc, out, err, rep = run_cmp(fixture, "T2", d, extra=["--no-actor-map"], jpath=os.path.join(td, "r5c3.json"))
        up = unexpected_paths(rep) if rep else []
        actor_re = re.compile(r"(\.source_actor|\.assigned_person|\.closed_by|\.assignees\[[0-9]+\])$")
        ck("(5c3) --no-actor-map => unexpected differences at actor paths only (assignees/assigned_person/closed_by are "
           "expected-rule paths, so only source_actor stays unexpected)", rc == 1 and up and all(
               p.endswith(".source_actor") for _f, _k, p in up), "%s %s" % (rc, up[:3]))
        ck("(5c4) with mapping off no actor string is rewritten", rep is not None and rep["normalization"]["actor_mapped_other"] == 0)
        ck("(5c5) every differing actor path is an actor path", rep is not None and all(
            actor_re.search(x["path"]) for fr in rep["forms"].values() for ent in fr["files"]
            for k in ("differ", "only_board", "only_other") for x in ent[k]))

        # (5d) 插入一筆系統日誌
        d = clone("syslog")
        tn = det_notes[0]
        doc = rjson(os.path.join(d, tn))
        n_before = len(doc["notes"])
        doc["notes"].append({"occurred_at": t2z, "entry_type": "note", "body": "machine-level resume (auto)",
                             "source_actor": "system:cmp-selftest"})
        wjson(os.path.join(d, tn), doc)
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5d.json"))
        up = unexpected_paths(rep) if rep else []
        ck("(5d) injected system journal, no --system-actor => exit 1 and listed as 'only_other'",
           rc == 1 and up == [(tn, "only_other", "$.notes[%d]" % n_before)], "%s %s" % (rc, up))
        ck("(5d2) hint says nothing is filtered and lists the candidate actor",
           "NOT filtered" in out and "system:cmp-selftest" in out)
        rc, out, err, rep = run_cmp(fixture, "T2", d, extra=["--system-actor", "system:cmp-selftest"],
                                    jpath=os.path.join(td, "r5d3.json"))
        ck("(5d3) with --system-actor => zero differences, exit 0, 1 note excluded (other side)",
           rc == 0 and rep["totals"] == {"unexpected": 0, "expected": 0}
           and rep["normalization"]["system_notes_excluded_other"] == 1
           and rep["normalization"]["system_notes_excluded_board"] == 0, str(rep and rep["normalization"]))
        ck("(5d4) no 'NOT filtered' hint once a system actor is given", "NOT filtered" not in out)
        # 系統日誌的 actor 以 human:seed-形給出也能過濾（映射後比對）
        d2 = clone("syslog2")
        doc = rjson(os.path.join(d2, tn))
        doc["notes"].append({"occurred_at": t2z, "entry_type": "note", "body": "x", "source_actor": "human:seed-system"})
        wjson(os.path.join(d2, tn), doc)
        rc, out, err, rep = run_cmp(fixture, "T2", d2, extra=["--system-actor", "role:system"])
        ck("(5d5) --system-actor role:system also filters human:seed-system", rc == 0, "%s" % rc)
        # 系統日誌插在中間（影響後續索引）過濾後仍為零差異
        d3 = clone("syslog3")
        doc = rjson(os.path.join(d3, tn))
        doc["notes"].insert(0, {"occurred_at": t1z, "entry_type": "note", "body": "front", "source_actor": "system:cmp-selftest"})
        wjson(os.path.join(d3, tn), doc)
        rc, *_ = run_cmp(fixture, "T2", d3, extra=["--system-actor", "system:cmp-selftest"])
        ck("(5d6) system journal at the front is filtered before index alignment", rc == 0)

        # (5e) 預期差異欄：列出兩邊值、不使退出碼為 1
        d = clone("expected")
        tdg, tas, tcl = det_diag[0], det_assignees[0], det_closed[0]
        doc = rjson(os.path.join(d, tdg))
        old_diag = doc["diagnosis"]
        doc["diagnosis"] = None
        wjson(os.path.join(d, tdg), doc)
        doc = rjson(os.path.join(d, tas))
        old_as = list(doc["assignees"])
        doc["assignees"] = []
        doc["assigned_person"] = None
        wjson(os.path.join(d, tas), doc)
        doc = rjson(os.path.join(d, tcl))
        doc["closed_by"] = None
        wjson(os.path.join(d, tcl), doc)
        doc = rjson(os.path.join(d, tgt))
        doc["asset_type"] = "default"
        wjson(os.path.join(d, tgt), doc)
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5e.json"))
        er = expected_recs(rep) if rep else []
        rules = {x["rule"] for _f, _k, x in er}
        ck("(5e) expected-difference perturbations => exit 0 with 0 unexpected", rc == 0 and rep["totals"]["unexpected"] == 0,
           "%s %s" % (rc, rep and rep["totals"]))
        ck("(5e2) rules Q3 / Q5c / Q8 all fired", {"Q3-diagnosis", "Q5c-people", "Q8-asset_type"} <= rules, str(rules))
        diag_rec = [x for f, k, x in er if f == tdg and x["path"] == "$.diagnosis"]
        ck("(5e3) expected diagnosis difference lists both values", diag_rec and diag_rec[0]["board"] == old_diag
           and diag_rec[0]["other"] is None)
        ck("(5e4) assignees entry listed as expected only_board with its value",
           any(f == tas and k == "only_board" and x["path"] == "$.assignees[0]" and x["board"] == old_as[0] for f, k, x in er))
        ck("(5e5) text report prints EXPECTED tag with rule id", "EXPECTED[Q3-diagnosis]" in out)
        rc, out, err = cli(["--seed", fixture, "--at", "T2", "--other-dir", d, "--no-expected"])
        ck("(5e6) --no-expected turns the same differences into failures (exit 1)", rc == 1)
        rc, out, err = cli(["--seed", fixture, "--at", "T2", "--other-dir", d, "--expect", "detail:$.brief_description"])
        ck("(5e7) --expect adds a rule without disturbing the rest (still exit 0)", rc == 0)
        d4 = clone("userexpect")
        doc = rjson(os.path.join(d4, tgt))
        doc["brief_description"] += "!"
        wjson(os.path.join(d4, tgt), doc)
        rc, *_ = run_cmp(fixture, "T2", d4, extra=["--expect", "detail:$.brief_description"])
        ck("(5e8) user-declared expected path absorbs that difference (exit 0)", rc == 0)
        rc, *_ = run_cmp(fixture, "T2", d4, extra=["--expect", "a11:$.brief_description"])
        ck("(5e9) a user rule for another form does not absorb it (exit 1)", rc == 1)

        # (5f) A11 母機／座兩種查詢
        mother_files = sorted(n for n in os.listdir(base_dir) if n.startswith("a11-EID") and not _SEAT_ID_RE.search(n[:-5]))
        seat_files = sorted(n for n in os.listdir(base_dir) if n.startswith("a11-EID") and _SEAT_ID_RE.search(n[:-5]))
        ck("(5f-pre) both A11 query kinds exist in the export", mother_files and seat_files, "%s %s" % (mother_files, seat_files))
        d = clone("seat")
        sf = seat_files[0]
        parent = _SEAT_ID_RE.sub("", sf[len("a11-"):-5])
        nest_n = _SEAT_ID_RE.search(sf[:-5]).group(0)[2:]
        pm = [r for r in rjson(os.path.join(d, "a11-%s.json" % parent)) if r.get("sub_unit") == "nest:%s" % nest_n]
        wjson(os.path.join(d, sf), pm)              # cmms 把座事件登到座：座查詢回該座事件
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5f.json"))
        er = expected_recs(rep) if rep else []
        ck("(5f) seat query answering rows (Board []) => expected 'N9-seat-query', exit 0",
           rc == 0 and pm and any(x["rule"] == "N9-seat-query" for _f, _k, x in er) and rep["totals"]["unexpected"] == 0,
           "%s %s" % (rc, rep and rep["totals"]))
        ck("(5f2) the expected seat rows are listed with their values", any(x.get("other") for _f, _k, x in er))
        # 母機查詢的差異不是預期
        d = clone("mother")
        mf = mother_files[0]
        rows = rjson(os.path.join(d, mf))
        rows[0]["reason"] = (rows[0].get("reason") or "") + " ~"
        wjson(os.path.join(d, mf), rows)
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5f3.json"))
        up = unexpected_paths(rep, "a11") if rep else []
        ck("(5f3) parent query difference is unexpected", rc == 1 and up == [(mf, "differ", "$[0].reason")], "%s %s" % (rc, up))
        # 座查詢：Board 恆 []，對方回任何列都屬預期（同上，換內容再確認一次）
        d = clone("seat2")
        wjson(os.path.join(d, sf), [{"ts_effective": "x", "effective_date": "x", "sub_unit": "nest:%s" % nest_n,
                                     "state": "not_scheduled", "reason": None, "recorded_at": "x"}])
        rc, *_ = run_cmp(fixture, "T2", d)
        ck("(5f4) Board-empty seat answered with rows stays expected (exit 0)", rc == 0)

        # (5g) 整檔缺／多／不認得
        d = clone("files")
        os.remove(os.path.join(d, det_files[-1]))
        extra_no = max(int(_DETAIL_FILE_RE.match(n).group(1)) for n in det_files) + 1000
        wjson(os.path.join(d, "detail-%d.json" % extra_no), rjson(os.path.join(d, det_files[0])))
        wjson(os.path.join(d, "unrelated.json"), {"x": 1})
        with open(os.path.join(d, "readme.txt"), "w") as fh:
            fh.write("hi")
        rc, out, err, rep = run_cmp(fixture, "T2", d, jpath=os.path.join(td, "r5g.json"))
        fr = rep["forms"]["detail"] if rep else {}
        ck("(5g) missing file => counted, exit 1", rc == 1 and fr.get("n_missing_in_other") == 1, str(fr and fr.get("n_missing_in_other")))
        ck("(5g2) extra detail file => 'only in other' counted", fr.get("n_only_in_other") == 1)
        ck("(5g3) unrecognized files ignored but listed", rep and sorted(rep["ignored_files"]) == ["readme.txt", "unrelated.json"],
           str(rep and rep["ignored_files"]))
        rc, *_ = run_cmp(fixture, "T2", d, extra=["--forms", "active-in,a11,a11-seat,a11-batch"])
        ck("(5g4) --forms restricts the comparison (detail problems no longer count)", rc == 0)

        # (5h) 查詢參數與 manifest 取用
        d = clone("params")
        rc, out, err, rep = run_cmp(fixture, "T2", d, extra=["--window-start", "2026-07-03 12:00:00"],
                                    jpath=os.path.join(td, "r5h.json"))
        ck("(5h) a --window-start differing from the one the other side used shows differences (exit 1)",
           rc == 1 and rep is not None and any(f == FILE_ACTIVE_IN for f, _k, _p in unexpected_paths(rep)), str(rc))
        d_nm = clone("nomanifest")
        os.remove(os.path.join(d_nm, MANIFEST_NAME))
        rc, *_ = run_cmp(fixture, "T2", d_nm)
        ck("(5h2) works without compare-manifest.json (defaults)", rc == 0)
        d_wm = clone("manifestparams")
        agp = rjson(os.path.join(d_wm, MANIFEST_NAME))
        agp["params"]["as_of"] = "2026-07-04"
        wjson(os.path.join(d_wm, MANIFEST_NAME), agp)
        rc, out, err, rep = run_cmp(fixture, "T2", d_wm, jpath=os.path.join(td, "r5h3.json"))
        ck("(5h3) query params come from the other dir's manifest (as_of honoured)", rep and rep["params"]["as_of"] == "2026-07-04")

        # ---- (6) 輸入錯誤 ＝ 退出碼 2 ------------------------------------
        ck("(6a) missing other dir => 2", cli(["--seed", fixture, "--at", "T2", "--other-dir", os.path.join(td, "nope")])[0] == 2)
        ck("(6b) missing seed => 2", cli(["--seed", os.path.join(td, "nope.json"), "--at", "T2", "--other-dir", base_dir])[0] == 2)
        ck("(6c) naive --at => 2", cli(["--seed", fixture, "--at", "2026-07-03T06:45:00", "--other-dir", base_dir])[0] == 2)
        ck("(6d) garbage --at => 2", cli(["--seed", fixture, "--at", "tomorrow", "--other-dir", base_dir])[0] == 2)
        ck("(6e) no --at => 2", cli(["--seed", fixture, "--other-dir", base_dir])[0] == 2)
        ck("(6f) compare mode without --other-dir => 2", cli(["--seed", fixture, "--at", "T2"])[0] == 2)
        ck("(6g) T mismatch with the other dir's manifest => 2", cli(["--seed", fixture, "--at", "T1", "--other-dir", base_dir])[0] == 2)
        d = clone("badjson")
        with open(os.path.join(d, det_files[0]), "wb") as fh:
            fh.write(b"{not json")
        ck("(6h) unparsable JSON in the other dir => 2", cli(["--seed", fixture, "--at", "T2", "--other-dir", d])[0] == 2)
        d = clone("worldmismatch")
        agp = rjson(os.path.join(d, MANIFEST_NAME))
        agp["world_id"] = "0" * 16
        wjson(os.path.join(d, MANIFEST_NAME), agp)
        ck("(6i) world_id mismatch with the other dir's manifest => 2", cli(["--seed", fixture, "--at", "T2", "--other-dir", d])[0] == 2)
        ck("(6j) unknown --forms value => 2", cli(["--seed", fixture, "--at", "T2", "--other-dir", base_dir, "--forms", "bogus"])[0] == 2)
        ck("(6k) bad --expect => 2", cli(["--seed", fixture, "--at", "T2", "--other-dir", base_dir, "--expect", "nocolon"])[0] == 2)
        ck("(6l) bad --as-of => 2", cli(["--seed", fixture, "--at", "T2", "--other-dir", base_dir, "--as-of", "07/03"])[0] == 2)
        ck("(6m) seed directory with a broken manifest => 2", _broken_seed_exits_2(td, cli, here))
        ck("(6n) unknown flag => 2 (argparse)", cli(["--bogus"])[0] == 2)
        ck("(6o) a seed directory inside the repo is refused => 2",
           cli(["--seed", here, "--at", "T2", "--other-dir", base_dir])[0] == 2)

    # ---- (7) 產生器世界（真資料形）：自比零差異＋擾動 ------------------------
    try:
        import wsynth_params as P
        import wsynth_world as W
        t_mid = C.day_start(P.HORIZON_START) + _dt.timedelta(days=4, hours=15)
        world = W.build_world(t_mid, with_segments=False)
    except Exception as exc:          # 產生器在此環境無法建（例如缺名冊）⇒ 不算失敗、明示未跑
        world = None
        print("n/a: generated-world section not run (%s: %s)" % (type(exc).__name__, exc))
    if world is not None:
        gl = Loaded(world["events"], world_id=world["world_id"], generator_version=world["generator_version"],
                    horizon_start_z=world["horizon_start"], exposed_until_z=world["exposed_until"], label="generator")
        tz = C.to_utc_z(t_mid)
        stg = K.replay(gl.events, t_mid)
        pg = default_params(gl, stg)
        ck("(7a) generated world has orders and A11 events at T", len(stg.wos) > 10 and len(stg.a11) > 0,
           "%d %d" % (len(stg.wos), len(stg.a11)))
        expg = build_expected(stg, pg)
        with tempfile.TemporaryDirectory(prefix="wsynth_compare_gen_") as td2:
            for n, (_f, body, _rs) in expg.items():
                with open(os.path.join(td2, n), "wb") as fh:
                    fh.write(body)
            files, _m, _ig = _read_other_dir(td2)
            opts = {"actor_map": True, "system_actors": set(), "rules_on": True, "extra_rules": [], "forms": list(FORMS)}
            rep = compare(gl, stg, td2, pg, opts, other_files=files)
            ck("(7b) generated world self-compare: zero differences (%d files)" % len(files),
               rep["totals"] == {"unexpected": 0, "expected": 0} and rep["exit"] == 0, str(rep["totals"]))
            ck("(7c) generated world: every form saw files",
               all(fr["n_files"] > 0 for f, fr in rep["forms"].items() if f != "a11-seat")   # 這個 T 的產生器世界不一定有座級 A11
               and rep["forms"]["detail"]["n_files"] == len(stg.wos), str({f: fr["n_files"] for f, fr in rep["forms"].items()}))
            # 擾動：改 active-in 的某單狀態
            ai = files[FILE_ACTIVE_IN]
            ai["items"][0]["status"] = "VOIDED"
            files2 = dict(files)
            files2[FILE_ACTIVE_IN] = ai
            rep2 = compare(gl, stg, td2, pg, opts, other_files=files2)
            ck("(7d) generated world: a changed status in active-in is the only unexpected difference",
               rep2["totals"]["unexpected"] == 1 and rep2["exit"] == 1, str(rep2["totals"]))

    # ---- (8) --help 含檔名約定 -------------------------------------------
    h = io.StringIO()
    with contextlib.redirect_stdout(h):
        try:
            main(["--help"])
        except SystemExit:
            pass
    ht = h.getvalue()
    ck("(8a) --help documents the file-name convention", all(s in ht for s in (
        "active-in.json", "detail-<work_order_no>.json", "a11-<asset_id>.json", "a11-batch.json", "compare-manifest.json")))
    ck("(8b) --help documents exit codes and expected differences", "exit codes" in ht and "expected differences" in ht)

    print("wsynth_compare selftest: %d checks, %d FAIL" % (n_ok[0] + len(fails), len(fails)))
    return 1 if fails else 0


def _history_status(w):
    """selftest 小工具：工單物化狀態 → (最終 status, 最終 hold_reason)。"""
    hist = K._history(w)
    last = hist[-1]
    return last["to_status"], last["hold_reason"]


def _broken_seed_exits_2(td, cli, here):
    d = os.path.join(td, "broken_seed")
    os.makedirs(os.path.join(d, "seed"))
    with open(os.path.join(d, "seed", "seed-manifest.json"), "wb") as fh:
        fh.write(b"{broken")
    return cli(["--seed", d, "--at", "T2", "--other-dir", d])[0] == 2


if __name__ == "__main__":
    sys.exit(main())
