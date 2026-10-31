"""`/availability` 妥善率頁的呈現模型（純函式、零 I/O）。

規格＝`SPEC-AVAILABILITY.md`（§2 口徑／§5 鐵則 C-AV1–12／§6 反模式）。

**這一支不算分母、也不定義停機段**——它只做「∩ 一下、切一下、加總一下」：

  · 分母 `Σ|RW|` 的唯一出處＝`board_eqmeeting.erw_machine_windows`（QG-13：全 Board 一支）；
    本模組吃它的**輸出**（`{eid: {"win": [(s0,s1)…], "sec": …}}`，秒座標相對窗首）。
  · 停機段的唯一出處＝`board_eqmeeting.wo_segments_scoped`（＝`/equipment`、landing
    同一支；scope events／A11 長停工位排除都已在裡面）；本模組吃它的**輸出**。
  · 停機**白名單**與**優先序**由呼叫端傳入（`app.RW_DOWN_STATES` / `app.AVAIL_STATE_ORDER`）
    ——模組裡刻意**不留第二份副本**，副本就是下一個會漂移的事實。

⚠ **停機是白名單、不是「其餘皆停機」**（C-AV3）：`不明`／`待機`／`流程等待`／`該跑沒跑`／
  `非排定`／`生產` 一律留在**妥善**那一側。把「不知道」畫成「壞了」是這個專案最貴的一種謊
  （`app.py` `STATE_COLORS` 上方紀律 ③、`SPEC-RUNWINDOW` §6.6-a ⑤／⑤b、
  `app.rw_donut_segments` 的 ★★ 段——同一條規則第五次寫下）。selftest 有負斷言。

⚠ **不得 import `app`**（selftest 有 AST 釘）。唯一的 `board_*` 相依＝`board_runwindow`
  的區間代數（`iv_union`／`iv_intersect`／`iv_subtract`／`iv_total`），那是全 Board 同一套。
"""
from __future__ import annotations

import datetime as _dt
import sys

import board_runwindow as wrw


# ---------------------------------------------------------------------------
# 0) 輸入轉換
# ---------------------------------------------------------------------------
def _sec(x, win_start):
    """datetime／ISO 字串 → 距 `win_start` 的秒數；不可解析 ⇒ None。"""
    if isinstance(x, str):
        try:
            x = _dt.datetime.fromisoformat(x)
        except ValueError:
            return None
    if not isinstance(x, _dt.datetime):
        return None
    return (x - win_start).total_seconds()


def segs_to_offsets(segs, win_start):
    """`wem.wo_segments_scoped(...)` 的輸出 → `[(state, s0, s1)]`（秒座標、相對 `win_start`）。

       · `countable is False` 的段（S12 uptime-hold＝機台可生產）**丟掉**——它不是停機。
       · **不做白名單過濾**：白名單在 `avail_model` 才咬，這樣「餵一段 `不明` 進去」
         才是模型的負斷言，而不是在門口就被吃掉（C-AV3 的測試要打得到）。
       · 起訖不合法（缺、倒序、零長）⇒ 跳過。純函式。"""
    out = []
    for s in segs or ():
        if not isinstance(s, dict):
            continue
        if not s.get("countable", True):
            continue
        a = _sec(s.get("start"), win_start)
        b = _sec(s.get("end"), win_start)
        if a is None or b is None or b <= a:
            continue
        out.append((s.get("state") or "維修", a, b))
    return out


def full_window_mwin(eids, span):
    """**24h 全日軸退路**的 `mwin`（`SPEC-AVAILABILITY` §2.7）：每台機的「該跑窗」＝整個觀測窗。

       ⇒ 分母＝`n × span`、扣項＝窗內 union ⇒ 與 `app.au_chips` 的退路那一段**同一個數字**
       （selftest 釘）。**呼叫端必須同時掛 `av_fallback_note`**——退路上永遠不得印
       「應生產時段」字樣（C-AV8）。`uncov`／`marks` 刻意留空：24h 軸上沒有收工線、
       也沒有「日曆未涵蓋」這個概念。"""
    span = float(span or 0.0)
    if span <= 0:
        return {}
    return {e: {"win": [(0.0, span)], "uncov": [], "marks": [],
                "sec": span, "span": span}
            for e in sorted({x for x in (eids or ()) if x})}


# ---------------------------------------------------------------------------
# 1) 逐機：停機 ∩ RW → 優先序切分
# ---------------------------------------------------------------------------
def _ordered(states, order):
    """優先序（`order` 內的照 `order`、其餘照字典序接在後面）。"""
    order = tuple(order or ())
    rank = {s: i for i, s in enumerate(order)}
    return sorted(states, key=lambda s: (rank.get(s, len(order)), s))


def machine_split(segs, win_iv, down_states, order=()):
    """單台機：`[(state, s0, s1)]` ＋ 該機的 RW 窗 → `(down_iv, {state: [iv]})`。

       三步，順序不可換（`SPEC-AVAILABILITY` §2.1／§2.3）：
         ① **白名單過濾**——不在 `down_states` 裡的態一秒都不扣（C-AV3）。
         ② **逐態先自我 union、再 ∩ RW**（同 `wem._erw_by_state`／`app.rw_station_payload`）
            ⇒ 同一態的重疊工單段不雙計。
         ③ **優先序切分**：排前面的態先認領秒數，後面的態只拿還沒被認領的
            ⇒ 每一秒恰屬一態、`Σ by_state ≡ |down_iv|`（跨態重疊也不雙計）。
            ★ 沒有 ③ 的話兩張重疊的工單會讓圓餅總和 > 分母＝圖對自己的總數說謊（§6 反模式 2）。"""
    per = {}
    for st, a, b in segs or ():
        if st not in down_states:
            continue                                   # ① 白名單（不明/待機/… 留在妥善側）
        if b <= a:
            continue
        per.setdefault(st, []).append((a, b))
    claimed, out = [], {}
    for st in _ordered(per.keys(), order):
        # ② 同態自我 union → ∩ RW；③ 再扣掉已被更高優先序認領的秒數
        iv = wrw.iv_subtract(wrw.iv_intersect(wrw.iv_union(per[st]), win_iv), claimed)
        if wrw.iv_total(iv) > 0:
            out[st] = iv
            claimed = wrw.iv_union(claimed + iv)
    return claimed, out


def _ratio(down_sec, rw_sec):
    """A＝1 − 停機/分母。`|RW| = 0` ⇒ **None**（C-AV6／C-RW10：永不 0%、永不 100%）。"""
    if not rw_sec or rw_sec <= 0:
        return None
    return max(0.0, min(1.0, 1.0 - down_sec / rw_sec))


# ---------------------------------------------------------------------------
# 2) 機隊模型
# ---------------------------------------------------------------------------
def avail_model(mwin, segs_by_eid, down_states, order=(), days=None, fleet_eids=None,
                rollups=None):
    """機隊妥善率模型。**純函式、零 I/O。**

       入參
         `mwin`        ＝`wem.erw_machine_windows(...)` 的輸出（或 `full_window_mwin` 的退路形）。
                        **名冊外 EID 根本不在裡面**（C-RW9）⇒ 不進分母也不進分子。
         `segs_by_eid` ＝`{eid: [(state, s0, s1)]}`（`segs_to_offsets` 的輸出）。
         `down_states` ＝停機白名單（`app.RW_DOWN_STATES`）。
         `order`       ＝逐態切分的優先序（`app.AVAIL_STATE_ORDER`）。
         `days`        ＝`[(label, d0_sec, d1_sec)]` 生產日欄 ⇒ 逐日趨勢；省略 ⇒ `trend=[]`。
         `fleet_eids`  ＝進**機隊彙總**的 EID（預設＝`mwin` 全部鍵）。
                        ⚠ 子工位附掛列要進 `mwin`（機台帶要畫）但**不進機隊分母**
                        （它們是細分視圖、不計入合計）⇒ 呼叫端傳父機清單。

       回
         `{"rw_sec", "down_sec", "up_sec", "A", "by_state", "n_machines", "eids",
           "by_eid": {eid: {"rw_sec","down_sec","up_sec","A","by_state","down_iv"}},
           "trend": [{"label","rw_sec","down_sec","A"}]}`

       ★ **恆等式（C-AV5，selftest 釘死）**：`up_sec + Σ by_state.values() ≡ rw_sec`。
         這不是巧合、是 `machine_split` 的優先序切分**構造上**保證的。

       ★ R44（C-AV16，Fable 2026-09-29）`rollups`＝`{父機 EID: rollup_model(...) 的輸出}`
         （呼叫端經 `board_eqmeeting.parent_seat_rollup` 取得；本模組不 import 它）。
         在 `rollups` 裡的父機**不吃** `segs_by_eid` 裡自己的段——它的停機＝在役座推導
         （Σ 座停機 ∩ RW(座) ÷ n_在役、逐日）。分母仍是 `mwin` 那一台的 `sec`（C-AV1 不動）；
         只有「全座長停」的日子自分母排除（n_在役＝0 ⇒ 那段不主張）。
         `by_eid[父]` 多兩個加法鍵：`band`＝`[(state, a, b, k, n, seats)]`（軌條 opacity＝k/n）、
         `rollup`＝`{n_active, seats_active, n_seats}`。**不在 `rollups` 的 EID 逐位元不變。**"""
    mwin = mwin or {}
    fleet = ([e for e in fleet_eids if e in mwin] if fleet_eids is not None
             else sorted(mwin.keys()))
    by_eid = {}
    rollups = rollups or {}
    for eid, w in mwin.items():
        win_iv = w.get("win") or []
        roll = rollups.get(eid)
        if roll is not None:
            # ★ R44 C-AV16：父機停機＝在役座推導（`rollup_model`）；分母仍是這一台的 RW。
            rw_sec = (float(w.get("sec") or 0.0) if not roll.get("excluded_rw_sec")
                      else float(roll.get("rw_sec") or 0.0))
            down_sec = float(roll.get("down_sec") or 0.0)
            by_eid[eid] = {
                "rw_sec": rw_sec, "down_sec": down_sec,
                "up_sec": max(0.0, rw_sec - down_sec),
                "A": _ratio(down_sec, rw_sec),
                "by_state": dict(roll.get("by_state") or {}),
                "down_iv": [(st, a, b) for st, a, b, _k, _n, _s in roll.get("band") or ()],
                "band": list(roll.get("band") or ()),
                "rollup": {"n_active": roll.get("n_active"),
                           "seats_active": tuple(roll.get("seats_active") or ()),
                           "n_seats": roll.get("n_seats")},
                "_roll": roll,
            }
            continue
        rw_sec = float(w.get("sec") or 0.0)
        down_iv, st_iv = machine_split((segs_by_eid or {}).get(eid) or [], win_iv,
                                       down_states, order)
        down_sec = wrw.iv_total(down_iv)
        by_eid[eid] = {
            "rw_sec": rw_sec, "down_sec": down_sec,
            "up_sec": max(0.0, rw_sec - down_sec),
            "A": _ratio(down_sec, rw_sec),
            "by_state": {st: wrw.iv_total(iv) for st, iv in st_iv.items()},
            # 畫機台帶用：已切分（互不重疊）、已 ∩ RW 的片段，按時間排序
            "down_iv": sorted(((st, a, b) for st, iv in st_iv.items() for a, b in iv),
                              key=lambda p: (p[1], p[2])),
            "_down_union": down_iv, "_win": win_iv, "_st_iv": st_iv,
        }
    rw_sec = sum(by_eid[e]["rw_sec"] for e in fleet)
    down_sec = sum(by_eid[e]["down_sec"] for e in fleet)
    by_state = {}
    for e in fleet:
        for st, sec in by_eid[e]["by_state"].items():
            by_state[st] = by_state.get(st, 0.0) + sec
    trend = []
    for label, d0, d1 in (days or ()):
        if d1 <= d0:
            continue
        d_rw = d_down = 0.0
        d_by = {}
        for e in fleet:
            v = by_eid[e]
            if "_roll" in v:                      # ★ R44：父機逐日＝座推導的同一份加權片
                rt = rollup_window(v["_roll"], d0, d1)
                d_rw += rt["rw_sec"]
                d_down += rt["down_sec"]
                for st, sec in rt["by_state"].items():
                    if sec > 0:
                        d_by[st] = d_by.get(st, 0.0) + sec
                continue
            d_rw += wrw.iv_total(wrw.iv_intersect(v["_win"], [(d0, d1)]))
            d_down += wrw.iv_total(wrw.iv_intersect(v["_down_union"], [(d0, d1)]))
            # 逐態也切到這一天：柱子的堆疊要與圓餅同一套片（否則同一頁兩種歸片語意）。
            # 逐態片段本來就互不重疊 ⇒ 這裡直接相加、恆等式跟著保住。
            for st, iv in v["_st_iv"].items():
                sec = wrw.iv_total(wrw.iv_intersect(iv, [(d0, d1)]))
                if sec > 0:
                    d_by[st] = d_by.get(st, 0.0) + sec
        trend.append({"label": label, "rw_sec": d_rw, "down_sec": d_down,
                      "up_sec": max(0.0, d_rw - d_down), "by_state": d_by,
                      "A": _ratio(d_down, d_rw)})
    for v in by_eid.values():                     # 內部鍵不外流（模板拿不到就不會誤用）
        v.pop("_down_union", None)
        v.pop("_win", None)
        v.pop("_st_iv", None)
        v.pop("_roll", None)
    return {"rw_sec": rw_sec, "down_sec": down_sec,
            "up_sec": max(0.0, rw_sec - down_sec),
            "A": _ratio(down_sec, rw_sec),
            "by_state": by_state, "n_machines": len(fleet), "eids": list(fleet),
            "by_eid": by_eid, "trend": trend}


# ---------------------------------------------------------------------------
# 3) ★ R44（C-AV16，Fable 2026-09-29）：形③ 多座父機＝在役座推導
# ---------------------------------------------------------------------------
#   機台級不變（妥善率的單位仍是機台、父機仍以 1 台計、分母仍是它的 RW）；改的是父機
#   「自己的停機」怎麼來：
#       parent_down ＝ Σ_日 Σ_{s ∈ 在役(日)} |seat_down(s) ∩ RW(s) ∩ 日| ÷ n_在役(日)
#   · 座段（`seat_segs`）＝該座**自己 ∪ 繼承**的停機段（由 `board_eqmeeting.
#     parent_seat_rollup` 產；本模組只做區間代數，不定義段、不判長停）。
#   · 逐座先走 `machine_split`（白名單 → 同態 union → ∩ RW → 優先序切分）＝**與一般機台
#     同一支**，再乘上 1/n 權重 ⇒ 同座「自己 ∪ 繼承」的重疊天然不雙計（union 在 split 裡）。
#   · n_在役＝0 的日子：那段父機 RW 不主張（自分母排除、`excluded_rw_sec` 記帳）。
#   純函式、零 I/O。


def rollup_model(seat_segs, day_active, rw_parent, rw_seats=None, down_states=None, order=()):
    """在役座推導的父機停機 → dict（秒座標、相對同一個窗首）。

       入參
         `seat_segs`  ＝`{座: [(state, s0, s1)]}`（`segs_to_offsets` 的輸出）。
         `day_active` ＝`[(d0, d1, (在役座…))]` 台北日曆日切片（逐日 as-of；呼叫端算）。
         `rw_parent`  ＝父機 RW `[(s0, s1)]`；`rw_seats`＝`{座: [(s0, s1)]}`（缺 ⇒ 父機窗）。
         `down_states`＝停機白名單；`None` ⇒ 段裡出現的態全算（24h／事件視角消費者用）。
       回
         `{"rw_sec", "rw_iv", "excluded_rw_sec", "down_sec", "A", "by_state"（優先序切分、加權）,
           "by_state_union"（逐態 union、加權＝事件視角）, "pieces"[(st,a,b,w)],
           "band"[(st,a,b,k,n,seats)], "n_active", "seats_active", "n_seats"}`
       ★ 恆等式：`Σ by_state ≡ down_sec`（逐座切分片互不重疊 × 同一權重）。"""
    rw_parent = wrw.iv_union(rw_parent or [])
    rw_seats = rw_seats or {}
    seat_segs = seat_segs or {}
    if down_states is None:
        ds = frozenset(st for segs in seat_segs.values() for st, _a, _b in (segs or ()))
    else:
        ds = frozenset(down_states)
    rw_iv, excl, pieces, band = [], [], [], []
    by_union = {}
    last_active = ()
    n_seats = len({s for _d0, _d1, act in (day_active or ()) for s in act}
                  | set(seat_segs.keys()))
    for d0, d1, active in day_active or ():
        if d1 <= d0:
            continue
        day = [(d0, d1)]
        act = tuple(active or ())
        n = len(act)
        last_active = act
        rw_day = wrw.iv_intersect(rw_parent, day)
        if n == 0:
            excl.extend(rw_day)                         # 全座長停（整機未登記）⇒ 不主張
            continue
        rw_iv.extend(rw_day)
        w = 1.0 / n
        seat_down, seat_st = {}, {}
        for s in act:
            win_s = wrw.iv_intersect(rw_seats.get(s, rw_parent), day)
            segs = seat_segs.get(s) or []
            d_iv, st_iv = machine_split(segs, win_s, ds, order)
            seat_down[s], seat_st[s] = d_iv, st_iv
            for st, iv in st_iv.items():
                for a, b in iv:
                    pieces.append((st, a, b, w))
            per = {}
            for st, a, b in segs:
                if st in ds and b > a:
                    per.setdefault(st, []).append((a, b))
            for st, ivs in per.items():
                sec = wrw.iv_total(wrw.iv_intersect(wrw.iv_union(ivs), win_s))
                if sec > 0:
                    by_union[st] = by_union.get(st, 0.0) + sec * w
        # 軌條：任一在役座停機的時段；k＝停機座數（opacity＝k/n）；態＝該刻最高優先序
        cuts = sorted({x for s in act for a, b in seat_down[s] for x in (a, b)})
        for x, y in zip(cuts, cuts[1:]):
            if y <= x:
                continue
            downs = tuple(s for s in act if any(a <= x and y <= b for a, b in seat_down[s]))
            if not downs:
                continue
            sts = {st for s in downs for st, iv in seat_st[s].items()
                   if any(a <= x and y <= b for a, b in iv)}
            st = _ordered(sts, order)[0] if sts else "維修"
            if (band and band[-1][2] == x and band[-1][0] == st and band[-1][5] == downs
                    and band[-1][4] == n):
                band[-1] = (st, band[-1][1], y, len(downs), n, downs)
            else:
                band.append((st, x, y, len(downs), n, downs))
    rw_sec = wrw.iv_total(rw_iv)
    down_sec = 0.0
    by_state = {}
    for st, a, b, w in pieces:
        down_sec += (b - a) * w
        by_state[st] = by_state.get(st, 0.0) + (b - a) * w
    return {"rw_sec": rw_sec, "rw_iv": wrw.iv_union(rw_iv),
            "excluded_rw_sec": wrw.iv_total(excl),
            "down_sec": down_sec, "A": _ratio(down_sec, rw_sec),
            "by_state": by_state, "by_state_union": by_union,
            "pieces": pieces, "band": band,
            "n_active": len(last_active), "seats_active": last_active, "n_seats": n_seats}


def rollup_window(roll, lo, hi):
    """`rollup_model` 的輸出切到 `[lo, hi)` → `{"rw_sec", "down_sec", "by_state"}`
       （逐日趨勢／日帳切日用；與整窗同一份加權片 ⇒ 各日相加 ≡ 整窗）。"""
    rw = wrw.iv_total(wrw.iv_intersect((roll or {}).get("rw_iv") or [], [(lo, hi)]))
    down, by = 0.0, {}
    for st, a, b, w in (roll or {}).get("pieces") or ():
        x, y = max(a, lo), min(b, hi)
        if y > x:
            down += (y - x) * w
            by[st] = by.get(st, 0.0) + (y - x) * w
    return {"rw_sec": rw, "down_sec": down, "by_state": by}


# ══════════════════════════════════════════════════════════════════════════════
# --selftest（純離線：零 I/O、零 MES、**零 app**）
# ══════════════════════════════════════════════════════════════════════════════
DOWN = frozenset({"維修", "計畫保養", "等料(停機)", "等外包(停機)", "故障停機",
                  "調機", "其他維護", "舊制工單"})
ORDER = ("維修", "計畫保養", "等料(停機)", "等外包(停機)", "故障停機",
         "調機", "其他維護", "舊制工單")
H = 3600.0


def _w(*ivs):
    """測試用 mwin 條目（秒座標）。"""
    ivs = list(ivs)
    return {"win": ivs, "uncov": [], "marks": [],
            "sec": sum(b - a for a, b in ivs), "span": 24 * H}


def selftest():
    ok = fail = 0
    out = []

    def t(label, cond, detail=""):
        nonlocal ok, fail
        out.append(("PASS" if cond else "FAIL", label, "" if cond else detail))
        ok += bool(cond)
        fail += (not cond)

    # ── (A) 恆等式：up + Σby_state ≡ Σ|RW|（C-AV5）────────────────────────────
    mw = {"E1": _w((0, 8 * H)), "E2": _w((0, 8 * H))}
    segs = {"E1": [("維修", 1 * H, 3 * H), ("計畫保養", 5 * H, 6 * H)],
            "E2": [("等料(停機)", 0, 2 * H)]}
    m = avail_model(mw, segs, DOWN, ORDER)
    t("(A) up + Σby_state ≡ Σ|RW| (identity holds by construction)",
      abs(m["up_sec"] + sum(m["by_state"].values()) - m["rw_sec"]) < 1e-6,
      f'up={m["up_sec"]} by={m["by_state"]} rw={m["rw_sec"]}')
    t("(A) Σ|RW| = 16h for two machines with an 8h run window each",
      abs(m["rw_sec"] - 16 * H) < 1e-6, str(m["rw_sec"]))
    t("(A) down = 2h repair + 1h PM + 2h parts-wait = 5h",
      abs(m["down_sec"] - 5 * H) < 1e-6, str(m["down_sec"]))
    t("(A) A = 1 - 5/16", abs(m["A"] - (1 - 5.0 / 16)) < 1e-9, str(m["A"]))
    t("(A) by_state carries every down state that actually took time",
      set(m["by_state"]) == {"維修", "計畫保養", "等料(停機)"}, str(m["by_state"]))

    # ── (B) 同態重疊不雙計（先自我 union）────────────────────────────────────
    m2 = avail_model({"E1": _w((0, 8 * H))},
                     {"E1": [("維修", 1 * H, 4 * H), ("維修", 2 * H, 5 * H)]},
                     DOWN, ORDER)
    t("(B) two overlapping repair orders on one machine count as 4h, not 6h "
      "(same-state self-union before the intersection)",
      abs(m2["down_sec"] - 4 * H) < 1e-6, str(m2["down_sec"]))
    t("(B) by_state agrees with the union, not with the sum of the raw segments",
      abs(m2["by_state"]["維修"] - 4 * H) < 1e-6, str(m2["by_state"]))

    # ── (C) 跨態重疊不雙計（優先序切分）──────────────────────────────────────
    m3 = avail_model({"E1": _w((0, 8 * H))},
                     {"E1": [("維修", 1 * H, 4 * H), ("計畫保養", 2 * H, 5 * H)]},
                     DOWN, ORDER)
    t("(C) repair 1-4h overlapping PM 2-5h counts as 4h of downtime, not 6h",
      abs(m3["down_sec"] - 4 * H) < 1e-6, str(m3["down_sec"]))
    t("(C) the overlap is claimed by the higher-priority state (repair 3h, PM 1h) so the "
      "slices still sum to the union",
      abs(m3["by_state"]["維修"] - 3 * H) < 1e-6
      and abs(m3["by_state"]["計畫保養"] - 1 * H) < 1e-6
      and abs(sum(m3["by_state"].values()) - m3["down_sec"]) < 1e-6, str(m3["by_state"]))
    m3b = avail_model({"E1": _w((0, 8 * H))},
                      {"E1": [("計畫保養", 2 * H, 5 * H), ("維修", 1 * H, 4 * H)]},
                      DOWN, ORDER)
    t("(C) the slicing is driven by ORDER, not by the input order of the segments",
      m3b["by_state"] == m3["by_state"], f'{m3b["by_state"]} vs {m3["by_state"]}')

    # ── (D) ★ 白名單負斷言（C-AV3）：不知道 ≠ 壞了 ──────────────────────────
    for st in ("不明", "待機", "流程等待", "該跑沒跑", "非排定", "生產"):
        md = avail_model({"E1": _w((0, 8 * H))}, {"E1": [(st, 1 * H, 5 * H)]}, DOWN, ORDER)
        t(f"(D) a '{st}' segment stays on the AVAILABLE side - it never becomes downtime "
          "(painting 'we do not know' as 'it is broken' is the most expensive lie on this page)",
          md["down_sec"] == 0 and md["A"] == 1.0 and md["by_state"] == {},
          f'down={md["down_sec"]} by={md["by_state"]}')

    # ── (E) |RW| = 0 ⇒ None（C-AV6／C-RW10）────────────────────────────────
    mz = avail_model({"E1": {"win": [], "uncov": [], "marks": [], "sec": 0.0, "span": 8 * H}},
                     {"E1": [("維修", 0, 4 * H)]}, DOWN, ORDER)
    t("(E) |RW| = 0 gives A = None (never 0%, never 100%) - a rest day is not a bad day",
      mz["A"] is None and mz["by_eid"]["E1"]["A"] is None, str(mz["A"]))
    t("(E) downtime outside the run window is not counted either (0h, not 4h)",
      mz["down_sec"] == 0, str(mz["down_sec"]))

    # ── (F) 名冊外 EID：mwin 沒有它 ⇒ 不進分母也不進分子（C-RW9）─────────────
    mo = avail_model({"E1": _w((0, 8 * H))},
                     {"E1": [("維修", 0, 2 * H)], "E9": [("維修", 0, 8 * H)]}, DOWN, ORDER)
    t("(F) an EID absent from mwin (off-roster) enters neither the denominator nor the "
      "numerator - we never borrow a neighbouring cluster's window",
      mo["rw_sec"] == 8 * H and mo["down_sec"] == 2 * H and "E9" not in mo["by_eid"],
      f'rw={mo["rw_sec"]} down={mo["down_sec"]} eids={list(mo["by_eid"])}')

    # ── (G) ★ C-PC11：分母永不取單機自身的訊號窗 ────────────────────────────
    #        停 4h 與停 20h 的**同一台機**，分母必須一模一樣——否則機器停著不修，
    #        妥善率反而變好看，而且沒有任何測試會紅。
    g4 = avail_model({"E1": _w((0, 24 * H))}, {"E1": [("維修", 0, 4 * H)]}, DOWN, ORDER)
    g20 = avail_model({"E1": _w((0, 24 * H))}, {"E1": [("維修", 0, 20 * H)]}, DOWN, ORDER)
    t("(G) C-PC11: a machine down 4h and the same machine down 20h share the identical "
      "denominator - downtime must never shrink the denominator",
      g4["rw_sec"] == g20["rw_sec"] == 24 * H, f'{g4["rw_sec"]} vs {g20["rw_sec"]}')
    t("(G) C-PC11: and the metric therefore really does get worse (83% vs 17%)",
      g4["A"] > g20["A"] and abs(g4["A"] - 5.0 / 6) < 1e-9, f'{g4["A"]} vs {g20["A"]}')

    # ── (H) 逐日趨勢：分母＝該日 Σ|RW|（C-RW10 同一條）────────────────────────
    days = [("D1", 0, 24 * H), ("D2", 24 * H, 48 * H), ("D3", 48 * H, 72 * H)]
    mt_ = avail_model({"E1": _w((8 * H, 16 * H), (32 * H, 40 * H))},
                      {"E1": [("維修", 10 * H, 12 * H)]}, DOWN, ORDER, days=days)
    tr = {d["label"]: d for d in mt_["trend"]}
    t("(H) the daily trend denominator is that day's Σ|RW|, not 24h",
      tr["D1"]["rw_sec"] == 8 * H and tr["D2"]["rw_sec"] == 8 * H, str(tr))
    t("(H) day 1 loses 2h to a repair, day 2 loses none",
      abs(tr["D1"]["A"] - 0.75) < 1e-9 and tr["D2"]["A"] == 1.0, str(tr))
    t("(H) a day with no run window at all reports None, never 0%",
      tr["D3"]["rw_sec"] == 0 and tr["D3"]["A"] is None, str(tr["D3"]))
    t("(H) Σ trend rw_sec ≡ Σ|RW| when the day columns tile the window",
      abs(sum(d["rw_sec"] for d in mt_["trend"]) - mt_["rw_sec"]) < 1e-6)
    t("(H) each day carries the same slices as the donut (up + Σby_state ≡ that day's Σ|RW|) "
      "- one slicing rule per page, not one per chart",
      all(abs(d["up_sec"] + sum(d["by_state"].values()) - d["rw_sec"]) < 1e-6
          for d in mt_["trend"]), str(mt_["trend"]))
    t("(H) Σ trend by_state ≡ fleet by_state when the day columns tile the window",
      abs(sum(v for d in mt_["trend"] for v in d["by_state"].values())
          - sum(mt_["by_state"].values())) < 1e-6)
    t("(H) the per-day working keys never leak either",
      all("_st_iv" not in v for v in mt_["by_eid"].values()))

    # ── (I) fleet_eids：子工位進 mwin（畫帶）但不進機隊分母 ──────────────────
    mf = avail_model({"P1": _w((0, 8 * H)), "C1": _w((0, 8 * H))},
                     {"P1": [("維修", 0, 2 * H)], "C1": [("維修", 0, 8 * H)]},
                     DOWN, ORDER, fleet_eids=["P1"])
    t("(I) sub-position rows are drawn (they are in by_eid) but never enter the fleet "
      "denominator - they are a breakdown view, not extra machines",
      mf["rw_sec"] == 8 * H and mf["n_machines"] == 1 and "C1" in mf["by_eid"],
      f'rw={mf["rw_sec"]} n={mf["n_machines"]}')

    # ── (J) 退路形（§2.7）：滿窗 RW ⇒ 分母＝n×span、扣項＝窗內 union ─────────
    fw = full_window_mwin(["E1", "E2"], 24 * H)
    t("(J) full_window_mwin gives every machine the whole window and no shift marks",
      fw["E1"]["sec"] == 24 * H and fw["E1"]["win"] == [(0.0, 24 * H)]
      and fw["E1"]["uncov"] == [] and fw["E1"]["marks"] == [], str(fw["E1"]))
    mfb = avail_model(fw, {"E1": [("維修", 0, 2 * H), ("維修", 1 * H, 3 * H)],
                           "E2": [("不明", 0, 24 * H)]}, DOWN, ORDER)
    t("(J) the 24h fallback denominator is n × span (48h for two machines over a day)",
      mfb["rw_sec"] == 48 * H, str(mfb["rw_sec"]))
    t("(J) the fallback numerator equals the in-window union (3h, not 4h) - identical to "
      "downtime_by_eid_scoped's down_sec",
      abs(mfb["down_sec"] - 3 * H) < 1e-6, str(mfb["down_sec"]))
    t("(J) full_window_mwin of an empty span is empty (never a zero-length denominator)",
      full_window_mwin(["E1"], 0) == {})

    # ── (K) 機台帶材料：片段互不重疊、每片都在 RW 內、按時間排序 ─────────────
    mb = avail_model({"E1": _w((2 * H, 10 * H))},
                     {"E1": [("維修", 0, 4 * H), ("計畫保養", 3 * H, 6 * H),
                             ("等料(停機)", 8 * H, 12 * H)]}, DOWN, ORDER)
    piv = mb["by_eid"]["E1"]["down_iv"]
    t("(K) band pieces are clipped into the run window (nothing before 2h or after 10h)",
      all(2 * H <= a and b <= 10 * H for _s, a, b in piv), str(piv))
    t("(K) band pieces never overlap each other (the slicing already de-duplicated them)",
      all(piv[i][2] <= piv[i + 1][1] for i in range(len(piv) - 1)), str(piv))
    t("(K) band pieces are sorted by time, and their total is the machine's down_sec",
      abs(sum(b - a for _s, a, b in piv) - mb["by_eid"]["E1"]["down_sec"]) < 1e-6, str(piv))
    t("(K) internal working keys never leak to the template",
      "_win" not in mb["by_eid"]["E1"] and "_down_union" not in mb["by_eid"]["E1"])

    # ── (L) segs_to_offsets：uptime-hold 丟掉、白名單**不**在門口咬 ──────────
    base = _dt.datetime(2026, 9, 1, 8, 0, 0)
    raw = [{"state": "維修", "start": base + _dt.timedelta(hours=1),
            "end": base + _dt.timedelta(hours=3), "countable": True},
           {"state": "待機", "start": base, "end": base + _dt.timedelta(hours=1),
            "countable": True},
           {"state": "維修", "start": base, "end": base + _dt.timedelta(hours=5),
            "countable": False},
           {"state": "維修", "start": base, "end": base}]
    offs = segs_to_offsets(raw, base)
    t("(L) uptime-hold (countable=False) segments are dropped - the machine could produce",
      len(offs) == 2, str(offs))
    t("(L) non-down states survive the conversion (the whitelist bites in avail_model, "
      "so the C-AV3 negative assertion can actually reach the model)",
      ("待機", 0.0, 1 * H) in offs, str(offs))
    t("(L) zero-length and reversed segments are skipped",
      all(b > a for _s, a, b in offs))
    t("(L) ISO-string timestamps are accepted as well as datetimes",
      segs_to_offsets([{"state": "維修", "start": base.isoformat(),
                        "end": (base + _dt.timedelta(hours=2)).isoformat()}], base)
      == [("維修", 0.0, 2 * H)])

    # ── (M) 邊界：空輸入不炸、不回垃圾 ──────────────────────────────────────
    me = avail_model({}, {}, DOWN, ORDER)
    t("(M) an empty mwin yields A=None and zero machines (never a fabricated 100%)",
      me["A"] is None and me["n_machines"] == 0 and me["rw_sec"] == 0, str(me))
    t("(M) segs_to_offsets tolerates None / junk entries",
      segs_to_offsets(None, base) == [] and segs_to_offsets([None, 5], base) == [])
    t("(M) an unknown down state still gets a slice (it sorts after ORDER, never dropped)",
      avail_model({"E1": _w((0, 4 * H))}, {"E1": [("維修", 0, 1 * H)]},
                  DOWN | {"新態"}, ORDER)["down_sec"] == 1 * H)
    m_new = avail_model({"E1": _w((0, 4 * H))},
                        {"E1": [("新態", 0, 2 * H), ("維修", 1 * H, 3 * H)]},
                        DOWN | {"新態"}, ORDER)
    t("(M) a state outside ORDER is ranked last but still sums correctly",
      abs(sum(m_new["by_state"].values()) - m_new["down_sec"]) < 1e-6
      and abs(m_new["down_sec"] - 3 * H) < 1e-6, str(m_new["by_state"]))

    # ── (O) ★ R44 C-AV16：形③ 父機＝在役座推導（`rollup_model`）────────────────
    S4 = ("S1", "S2", "S3", "S4")
    D1 = [(0, 24 * H, S4)]
    full = {s: [("維修", 0, 24 * H)] for s in S4}
    r_all = rollup_model(full, D1, [(0, 8 * H)], down_states=DOWN, order=ORDER)
    t("(O) every in-service seat down the whole run window => parent A = 0% (not 100%)",
      r_all["A"] == 0.0 and abs(r_all["down_sec"] - 8 * H) < 1e-6, str(r_all["A"]))
    half = {"S1": [("維修", 0, 24 * H)], "S2": [("維修", 0, 24 * H)]}
    r_half = rollup_model(half, D1, [(0, 8 * H)], down_states=DOWN, order=ORDER)
    t("(O) two of four in-service seats down all window => parent A = 50%",
      abs(r_half["A"] - 0.5) < 1e-9, str(r_half["A"]))
    r_two = rollup_model(half, [(0, 24 * H, ("S1", "S2"))], [(0, 8 * H)],
                         down_states=DOWN, order=ORDER)
    t("(O) dormant seats leave numerator AND denominator (2 in service, both down => 0%)",
      r_two["A"] == 0.0 and r_two["n_active"] == 2, str(r_two["A"]))
    r_none = rollup_model(full, [(0, 24 * H, ())], [(0, 8 * H)], down_states=DOWN, order=ORDER)
    t("(O) no seat in service (machine itself not registered dormant) => A = None and its RW "
      "leaves the denominator (C-AV6 / C-AV15 form)",
      r_none["A"] is None and r_none["rw_sec"] == 0 and r_none["excluded_rw_sec"] == 8 * H,
      str(r_none))
    ovl = {"S1": [("維修", 1 * H, 4 * H), ("維修", 2 * H, 5 * H), ("計畫保養", 3 * H, 6 * H)]}
    r_ovl = rollup_model(ovl, D1, [(0, 8 * H)], down_states=DOWN, order=ORDER)
    t("(O) own ∪ inherited overlap on one seat is unioned before weighting (5h/4, not 8h/4)",
      abs(r_ovl["down_sec"] - 5 * H / 4) < 1e-6, str(r_ovl["down_sec"]))
    t("(O) Σ by_state ≡ down_sec (slices of one seat never overlap)",
      abs(sum(r_ovl["by_state"].values()) - r_ovl["down_sec"]) < 1e-6, str(r_ovl["by_state"]))
    t("(O) the whitelist still bites per seat ('不明' on every seat stays available)",
      rollup_model({s: [("不明", 0, 8 * H)] for s in S4}, D1, [(0, 8 * H)],
                   down_states=DOWN, order=ORDER)["down_sec"] == 0)
    stag = {"S1": [("維修", 0, 4 * H)], "S2": [("維修", 2 * H, 4 * H)]}
    r_b = rollup_model(stag, D1, [(0, 8 * H)], down_states=DOWN, order=ORDER)
    t("(O) band: opacity k/n per elementary piece (0-2h 1/4, 2-4h 2/4)",
      [(a, b, k, n) for _s, a, b, k, n, _x in r_b["band"]]
      == [(0, 2 * H, 1, 4), (2 * H, 4 * H, 2, 4)], str(r_b["band"]))
    twod = [(0, 24 * H, S4), (24 * H, 48 * H, ("S1", "S2"))]
    r_2d = rollup_model({"S1": [("維修", 0, 48 * H)]}, twod, [(0, 8 * H), (24 * H, 32 * H)],
                        down_states=DOWN, order=ORDER)
    t("(O) n_active is per day (day1 1/4 x 8h + day2 1/2 x 8h = 6h)",
      abs(r_2d["down_sec"] - 6 * H) < 1e-6 and r_2d["n_active"] == 2, str(r_2d["down_sec"]))
    w1, w2 = rollup_window(r_2d, 0, 24 * H), rollup_window(r_2d, 24 * H, 48 * H)
    t("(O) rollup_window per day sums back to the whole window",
      abs(w1["down_sec"] + w2["down_sec"] - r_2d["down_sec"]) < 1e-6
      and abs(w1["rw_sec"] + w2["rw_sec"] - r_2d["rw_sec"]) < 1e-6, f"{w1} {w2}")
    mw_r = {"P": _w((0, 8 * H)), "E": _w((0, 8 * H))}
    sg_r = {"P": [("維修", 0, 8 * H)], "E": [("維修", 0, 2 * H)]}
    m_plain = avail_model(mw_r, sg_r, DOWN, ORDER, days=[("D", 0, 24 * H)])
    m_r = avail_model(mw_r, sg_r, DOWN, ORDER, days=[("D", 0, 24 * H)], rollups={"P": r_half})
    t("(O) rollups: the parent ignores its own segments and takes the seat roll-up (50%)",
      abs(m_r["by_eid"]["P"]["A"] - 0.5) < 1e-9 and m_plain["by_eid"]["P"]["A"] == 0.0,
      str(m_r["by_eid"]["P"]))
    t("(O) rollups: an EID outside `rollups` is bit-identical (negative assertion)",
      m_r["by_eid"]["E"] == m_plain["by_eid"]["E"]
      and avail_model(mw_r, sg_r, DOWN, ORDER, rollups={}) == avail_model(mw_r, sg_r, DOWN, ORDER))
    t("(O) rollups: identity up + Σby_state ≡ Σ|RW| still holds, and the trend agrees",
      abs(m_r["up_sec"] + sum(m_r["by_state"].values()) - m_r["rw_sec"]) < 1e-6
      and abs(m_r["trend"][0]["down_sec"] - m_r["down_sec"]) < 1e-6, str(m_r["trend"]))
    t("(O) rollups: the parent row carries band + rollup keys, never the internal _roll",
      "band" in m_r["by_eid"]["P"] and m_r["by_eid"]["P"]["rollup"]["n_active"] == 4
      and "_roll" not in m_r["by_eid"]["P"])

    # ── (N) import 紀律：不得 import app／mes_*（AST 釘）──────────────────────
    import ast
    import pathlib
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    t("(N) this module never imports app (it is a pure model, not a route)",
      "app" not in mods, str(sorted(mods)))
    t("(N) this module never imports any mes_* module (zero I/O, zero MES)",
      not any(x.startswith("mes_") for x in mods), str(sorted(mods)))
    t("(N) the only board_* dependency is board_runwindow (one interval algebra for "
      "the whole Tendline Board)",
      {x for x in mods if x.startswith("board_")} == {"board_runwindow"},
      str(sorted(mods)))
    t("(N) sys.modules is clean of mes_* after importing this module",
      not any(k.startswith("mes_") for k in sys.modules))

    for st, label, detail in out:
        print(f"{st}  {label}" + (f"   << {detail}" if detail else ""))
    print(f"\nboard_avail selftest: {ok}/{ok + fail} PASS, {fail} FAIL")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("usage: python board_avail.py --selftest")
