"""board_downtime.py -- `/equipment/downtime/monthly`（舊 `/downtime`，09-24 改名＋301）讀端（SPEC-DOWNTIME.md §6／§7；ROADMAP #167 P2/P3/P4）。

一句話：讀 `_wo_ledger`（工單日帳 sqlite）→ 月 × 區停機小時（兩把尺、整月 × 全三區切換）、
KPI、點段 top-5、頁尾例外句、慣犯。**頁面與助理工具（`board_mcp.downtime_monthly`）
吃同一支 `downtime_model`**＝一份口徑、兩個出口（不各算一份）。

★ 鐵則（違反即作廢；全文 SPEC §10／§11）
  · C-DT1  一個月只用一把尺：該月任一天不是 clipped ⇒ 該月三區全改 raw（24h 軸）、顯著標示；
           不混算、不並列。
  · C-DT2  本檔**零分段**：只讀日帳的秒數／區間；chip 下的機台 union 走 `wem._union_intervals`
           ＋`wem._intersect_intervals`（與 `erw_row` 同一套區間代數）＝⑰ 裁定。
  · C-DT5  bar 高＝機台 union（`eid_day` 或 ivs 重做 union）；排名＝單張 Σ。兩者不互推。
  · C-DT9  台數一律從目錄＋名冊算（`roster_eids`），不硬編。
  · C-DT11 demo／snapshot 零 cmms 連結、demo 零 LLM（本檔零 I/O 到 cmms／gateway；那在 app 層）。
  · C-DT12 面板零人員欄（`TOP_FIELDS` 白名單）。
  · C-DT13 名冊外／Zone D／目錄外 24h 月與 clipped 月一律同樣不進 bar。
  · §7.4   Δ 不上色（只給符號與數）。
"""

import datetime as _dt
import json
import os
from pathlib import Path

import board_eqmeeting as wem
import _wo_ledger as wl

BASE = Path(__file__).resolve().parent
DEMO_FIXTURE = BASE / "_fixtures" / "wo_ledger_demo.json"
BAR_AREAS = wl.BAR_AREAS
AREA_LABEL = {k: v for k, v in wem.AREAS}
DEFAULT_N_MONTHS = 6                     # §14 ④
MAX_RANGE_MONTHS = 24                    # §14 ⑳（09-24 Chris 裁）：起迄範圍最多 24 個月
# 頁面路徑（09-24 Chris 裁：`/downtime` → `/equipment/downtime/monthly`；舊路徑 301）。
# ★ 唯一出處：app 路由、nav、page_ctx、登記表都吃這一個常數。
PAGE_PATH = "/equipment/downtime/monthly"
TOP_N = 5
REPEAT_MIN = 2                           # §14 ⑥：同區、複選月內進前五 ≥2 次
MIGRATED_NOTE_SHARE = 0.5                # 「歷史月份多為遷移單」那一句的觸發比例（顯示層）
TYPE_CHIPS = ("all", "repair", "pm")     # §7.3：類型 chip 只有 全部／維修／保養
_WT_OF_CHIP = {"repair": ("REACTIVE",), "pm": ("PM",)}
# C-DT12：面板與工具輸出的欄位白名單（**沒有任何人員欄**）
TOP_FIELDS = ("rank", "wo_no", "eid", "display", "work_type", "type", "h", "pct", "stage",
              "start", "end", "fault_code", "flags")

_BASE_DT = _dt.datetime(2000, 1, 1)
_DEMO_CACHE = {}


# ---------------------------------------------------------------------------
# 連線（live／demo／snapshot）
# ---------------------------------------------------------------------------

def _demo_conn():
    """demo＝`_fixtures/wo_ledger_demo.json`（`_gen_downtime_fixtures.py` 唯一寫者）載入記憶體
       sqlite（同一張 schema、同一支讀端）。進程內快取；fixture 改了（mtime）就重載。"""
    import sqlite3
    st = DEMO_FIXTURE.stat().st_mtime if DEMO_FIXTURE.exists() else None
    hit = _DEMO_CACHE.get("c")
    if hit and hit[0] == st:
        return hit[1], hit[2]
    doc = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(wl._SCHEMA.replace("PRAGMA journal_mode=WAL;", ""))
    for tbl, rows in (doc.get("tables") or {}).items():
        if not rows:
            continue
        cols = list(rows[0].keys())
        conn.executemany(f"INSERT INTO {tbl}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                         [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    _DEMO_CACHE["c"] = (st, conn, doc)
    return conn, doc


def open_source(source):
    """→ `(conn|None, info)`；info＝{"mode", "today", "reason", "demo"}。恆不 raise。

       · live：`_wo_ledger.connect(readonly=True)`；檔不存在 ⇒ reason='no_ledger'。
       · demo：合成 fixture；today＝fixture 的 `now`（**不錨今天**＝不會變成定時炸彈）。
       · snapshot：誠實不可用（§7.6；不拿 demo 頂替）。"""
    src = str(source or "").strip().lower()
    if src == "snapshot":
        return None, {"mode": "snapshot", "today": None, "reason": "snapshot", "demo": None}
    if src == "demo":
        try:
            conn, doc = _demo_conn()
        except Exception as e:                         # noqa: BLE001
            return None, {"mode": "demo", "today": None, "reason": f"fixture:{type(e).__name__}",
                          "demo": None}
        return conn, {"mode": "demo", "today": wem._coerce_dt(doc.get("now")), "reason": None,
                      "demo": doc}
    try:
        conn = wl.connect(readonly=True)
    except Exception as e:                             # noqa: BLE001
        return None, {"mode": "live", "today": _dt.datetime.now(), "reason":
                      f"ledger:{type(e).__name__}", "demo": None}
    if conn is None:
        return None, {"mode": "live", "today": _dt.datetime.now(), "reason": "no_ledger",
                      "demo": None}
    return conn, {"mode": "live", "today": _dt.datetime.now().replace(microsecond=0),
                  "reason": None, "demo": None}


# ---------------------------------------------------------------------------
# 月份
# ---------------------------------------------------------------------------

def month_options(conn, today):
    """`ledger_day` 最早月 → 當月（生產月）。"""
    r = conn.execute("SELECT MIN(day) FROM ledger_day").fetchone()
    if not r or not r[0]:
        return []
    return wl.months_between(r[0][:7], wl.production_month(today))


def default_months(opts, today, n=DEFAULT_N_MONTHS):
    """最近 n 個**完整**月（當月＝部分月、預設不選＝§14 ⑤）。"""
    cur = wl.production_month(today)
    full = [m for m in opts if m < cur]
    return full[-n:]


def parse_months(raw, opts, today):
    """`?months=2026-03,2026-04` → 白名單過濾、升冪；空／全壞 ⇒ 預設。"""
    if raw:
        pick = sorted({m.strip() for m in str(raw).split(",") if m.strip() in opts})
        if pick:
            return pick
    return default_months(opts, today)


def resolve_range(frm, to, opts, today, legacy_months=None, cap=MAX_RANGE_MONTHS):
    """起月／迄月（§6.2；09-24 Chris 裁：chip 改範圍下拉、放棄跳選）→ `(sel, capped)`。

       · `sel`＝`[frm, to]` 展開成**連續**月清單（只取 `opts` 內的月）；from > to ⇒ 交換。
       · 缺一端 ⇒ 該端取預設範圍（最近 6 個完整月）的同一端；兩端都缺 ⇒ 舊參數 `months=`
         （向後相容：取其 min／max 展開）；再缺 ⇒ 預設 6 個完整月。
       · 超過 `cap` 個月 ⇒ 保留**最近**的 `cap` 個（錨在迄月）、`capped=True`（頁面印一句）。
       · 白名單外的值一律視同缺席（不猜、不 500）。"""
    f = frm if frm in opts else None
    t = to if to in opts else None
    if f is None and t is None and legacy_months:
        pick = sorted({m.strip() for m in str(legacy_months).split(",") if m.strip() in opts})
        if pick:
            f, t = pick[0], pick[-1]
    dflt = default_months(opts, today) or opts[-1:]
    if f is None:
        f = dflt[0] if dflt else None
    if t is None:
        t = dflt[-1] if dflt else None
    if f is None or t is None:
        return [], False
    if f > t:
        f, t = t, f
    sel = [m for m in opts if f <= m <= t]
    capped = len(sel) > cap
    return (sel[-cap:] if capped else sel), capped


def last_raw_month(months_model, axis_since):
    """「{m} 以前無班次資料、以全日計」的 m＝**最後一個全日計（raw）月**（§7.2；不硬編）。
       有第一個整月應生產時段月（`axis_since`）⇒ 它的前一個月；否則＝選取內最末一個 24h 月。
       選取內沒有任何 24h 月 ⇒ None（那一句不渲染＝例外才出現）。"""
    raw = [x["month"] for x in months_model if x["axis"] == "24h"]
    if not raw:
        return None
    if axis_since:
        y, m = int(axis_since[:4]), int(axis_since[5:7])
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        prev = f"{y:04d}-{m:02d}"
        if prev >= raw[-1]:
            return prev
    return raw[-1]


def month_through(month, today):
    """該月「資料到哪一天」：過去月＝月底；當月＝**最後一個已收盤的生產日**（今天的生產日
       還沒收完＝不計，否則當月永遠有一天不涵蓋、永遠退 24h 軸）。當月一天都還沒收 ⇒ None。"""
    days = wl.month_days(month, today)
    if month == wl.production_month(today):
        days = days[:-1]
    return days[-1] if days else None


def month_range(month, today):
    """`(lo, hi)` 生產日字串（含）或 None。"""
    hi = month_through(month, today)
    return (month + "-01", hi) if hi else None


def month_axis(conn, month, today):
    """C-DT1：該月每一天（到 through）都有 `ledger_day.coverage_flag='clipped'` ⇒ 'runwindow'；
       否則 '24h'。粒度＝**月 × 全三區**（§6.1；⑦ 已裁）。"""
    rng = month_range(month, today)
    if not rng:
        return "24h"
    days = [d for d in wl.month_days(month, today) if d <= rng[1]]
    got = {r[0]: r[1] for r in conn.execute(
        "SELECT day, coverage_flag FROM ledger_day WHERE day BETWEEN ? AND ?", rng)}
    return "runwindow" if all(got.get(d) == "clipped" for d in days) else "24h"


# ---------------------------------------------------------------------------
# 母體
# ---------------------------------------------------------------------------

def roster_eids(catalog=None):
    """三區 × 名冊內的母機集合（C-DT9：由目錄＋名冊**算**，不硬編）。
       名冊判定走 `wem.erw_row`（非 None＝名冊內＝寫端 `in_roster` 的同一條判準）。"""
    cat = wem.EQMT_CATALOG if catalog is None else catalog
    probe = {"days": {}, "closures": []}
    return sorted({e for a, _n, e in cat
                   if a in BAR_AREAS and wem.erw_row(e, {}, [], probe) is not None})


def _names(catalog=None):
    cat = wem.EQMT_CATALOG if catalog is None else catalog
    return {e: n for _a, n, e in cat}


# ---------------------------------------------------------------------------
# 核心：月 × 區（bar）、排名、KPI、頁尾
# ---------------------------------------------------------------------------

def _dt_ivs(pairs):
    return [(_BASE_DT + _dt.timedelta(seconds=a), _BASE_DT + _dt.timedelta(seconds=b))
            for a, b in pairs]


def _meta_map(conn):
    return {r["wo_no"]: dict(r) for r in conn.execute("SELECT * FROM wo_meta")}


def _chip_ok(meta, typ, xlong):
    if meta is None:
        return typ == "all" and not xlong
    if xlong and meta.get("flag_long"):
        return False
    if typ in _WT_OF_CHIP and (meta.get("work_type") or "") not in _WT_OF_CHIP[typ]:
        return False
    return True


def _type_of(wt):
    wt = (wt or "").upper()
    return "repair" if wt == "REACTIVE" else ("pm" if wt == "PM" else "other")


def downtime_model(conn, months, today, typ="all", xlong=False, catalog=None):
    """**唯一讀端**（頁面與 MCP 工具同源）。回 dict：

       · `months`：[{month, axis('runwindow'|'24h'), partial, through, areas:{a: h}, total_h}]
       · `top`：{(month, area): [row…]}（row 欄位＝`TOP_FIELDS`；單張 Σ、當前 chip 重排）
       · `kpi`：total_h／n_wo／n_eids／avg_per_machine_h（分母＝roster_n）／top5_share／
                delta（{dh, pct} 或 None＋`delta_reason`）／mixed_axes
       · `footer`：例外才出現的計數（None＝不渲染）
       · `repeat`：{area: [{eid, name, k, n}]}（P3 慣犯；單月不算）"""
    typ = typ if typ in TYPE_CHIPS else "all"
    names = _names(catalog)
    roster = roster_eids(catalog)
    meta = _meta_map(conn)
    chip = (typ != "all") or bool(xlong)
    cur = wl.production_month(today)
    out_months, top, pop_rows = [], {}, []
    wo_totals = {}
    for m in months:
        axis = month_axis(conn, m, today)
        col = "clipped_sec" if axis == "runwindow" else "raw_sec"
        areas = {a: 0.0 for a in BAR_AREAS}
        rng = month_range(m, today) or (m + "-01", m + "-00")
        wrows = [dict(r) for r in conn.execute(
            "SELECT * FROM wo_day WHERE day BETWEEN ? AND ? AND in_roster=1", rng)]
        wrows = [r for r in wrows if r["area"] in BAR_AREAS
                 and _chip_ok(meta.get(r["wo_no"]), typ, xlong)]
        if not chip:
            for r in conn.execute("SELECT area, SUM(" + col + ") FROM eid_day WHERE day BETWEEN ? AND ? "
                                  "AND in_roster=1 GROUP BY area", rng):
                if r[0] in areas:
                    areas[r[0]] = (r[1] or 0) / 3600.0
        else:
            # ⑰：讀端依 wo_day 過濾後重做機台 union（同一套區間代數；24h 月＝raw ivs、
            #     應生產時段月＝ivs ∩ 該機該日 rw_iv）。
            rw = {}
            if axis == "runwindow":
                for r in conn.execute("SELECT day, eid, rw_iv FROM eid_day WHERE day BETWEEN ? AND ? "
                                      "AND in_roster=1", rng):
                    rw[(r["day"], r["eid"])] = json.loads(r["rw_iv"]) if r["rw_iv"] else []
            grp = {}
            for r in wrows:
                grp.setdefault((r["day"], r["eid"], r["area"]), []).extend(json.loads(r["ivs"] or "[]"))
            for (day, eid, area), pairs in grp.items():
                u = wem._union_intervals(_dt_ivs(pairs))
                if axis == "runwindow":
                    u = wem._intersect_intervals(u, wem._union_intervals(_dt_ivs(rw.get((day, eid), []))))
                areas[area] += wem._ivs_seconds(u) / 3600.0
        # 排名（單張 Σ；C-DT5）
        per = {}
        for r in wrows:
            v = r[col]
            if v is None:
                continue
            k = (r["area"], r["wo_no"])
            p = per.setdefault(k, {"sec": 0, "repair": 0, "parts": 0, "vendor": 0, "eid": r["eid"]})
            p["sec"] += v
            p[wl.STAGE_OF_KIND.get(r["kind"], "repair")] += v
            pop_rows.append((m, r["area"], r["wo_no"], r["eid"], v))
        for a in BAR_AREAS:
            seg_h = areas[a]
            items = sorted(((k[1], p) for k, p in per.items() if k[0] == a and p["sec"] > 0),
                           key=lambda t: (-t[1]["sec"], t[0]))
            rows = []
            for i, (wno, p) in enumerate(items[:TOP_N]):
                mt = meta.get(wno) or {}
                flags = [f for f, on in (("migrated", mt.get("flag_migrated")),
                                         ("long", mt.get("flag_long"))) if on]
                h = p["sec"] / 3600.0
                rows.append({
                    "rank": i + 1, "wo_no": wno, "eid": p["eid"], "display": names.get(p["eid"], p["eid"]),
                    "work_type": mt.get("work_type"), "type": _type_of(mt.get("work_type")),
                    "h": h, "pct": (100.0 * h / seg_h) if seg_h > 0 else None,
                    "stage": None if mt.get("flag_migrated") else
                    {k: p[k] / 3600.0 for k in ("repair", "parts", "vendor")},
                    "start": (mt.get("active_start") or "")[:10] or None,
                    "end": (mt.get("active_end") or "")[:10] or None,
                    "fault_code": mt.get("fault_code"), "flags": flags})
            top[(m, a)] = rows
        for (a, wno), p in per.items():
            wo_totals[wno] = wo_totals.get(wno, 0) + p["sec"]
        out_months.append({"month": m, "axis": axis, "partial": m >= cur,
                           "through": month_through(m, today), "areas": areas,
                           "total_h": sum(areas.values())})

    total_h = sum(x["total_h"] for x in out_months)
    axes = {x["axis"] for x in out_months}
    top5_h = sum(r["h"] for rows in top.values() for r in rows)
    delta, delta_reason = None, None
    if len(out_months) < 2:
        delta_reason = "single"
    elif len(axes) > 1:
        delta_reason = "cross_axis"                       # ③：跨尺不比
    else:
        last = out_months[-1]["total_h"]
        rest = [x["total_h"] for x in out_months[:-1]]
        avg = sum(rest) / len(rest)
        delta = {"dh": last - avg, "pct": (100.0 * (last - avg) / avg) if avg > 0 else None,
                 "base_months": [x["month"] for x in out_months[:-1]],
                 "last_month": out_months[-1]["month"]}
    kpi = {"total_h": total_h,
           "n_wo": len({wno for (_m, _a, wno, _e, v) in pop_rows if v > 0}),
           "n_eids": len({e for (_m, _a, _w, e, v) in pop_rows if v > 0}),
           "roster_n": len(roster),
           "avg_per_machine_h": (total_h / len(roster)) if roster else None,
           "top5_h": top5_h, "top5_share": (100.0 * top5_h / total_h) if total_h > 0 else None,
           "delta": delta, "delta_reason": delta_reason, "mixed_axes": len(axes) > 1}
    repeat = repeat_offenders(top, months)
    footer = footer_counts(conn, months, today, meta, pop_rows)
    return {"months": out_months, "top": top, "kpi": kpi, "footer": footer, "repeat": repeat,
            "typ": typ, "xlong": bool(xlong)}


def repeat_offenders(top, months):
    """P3 慣犯（§7.3／§14 ⑥）：同區、複選月內進前五 ≥2 次；只選一個月 ⇒ 不算。"""
    if len(months) < 2:
        return {}
    out = {}
    for a in BAR_AREAS:
        cnt, name = {}, {}
        for m in months:
            for eid in {r["eid"] for r in top.get((m, a), ())}:
                cnt[eid] = cnt.get(eid, 0) + 1
            for r in top.get((m, a), ()):
                name[r["eid"]] = r["display"]
        hits = [{"eid": e, "display": name[e], "k": k, "n": len(months)}
                for e, k in sorted(cnt.items(), key=lambda t: (-t[1], t[0])) if k >= REPEAT_MIN]
        if hits:
            out[a] = hits
    return out


def _overlaps(meta, m_lo, m_hi):
    s = meta.get("active_start") or meta.get("opened_at")
    e = meta.get("active_end") or meta.get("closed_at")
    if not s:
        return False
    return s[:10] <= m_hi and (e is None or e[:10] >= m_lo)


def footer_counts(conn, months, today, meta, pop_rows):
    """§7.1 頁尾（一行一件、**例外才出現**：值 None／0 ⇒ 不渲染）。"""
    if not months:
        return {}
    lo = months[0] + "-01"
    hi = month_through(months[-1], today) or (months[-1] + "-31")
    rngs = [r for r in (month_range(m, today) for m in months) if r]
    cond = " OR ".join("(day BETWEEN ? AND ?)" for _ in rngs) or "0"
    args = tuple(x for r in rngs for x in r)
    # 無綑歸屬（名冊外；24h 軸）
    r = conn.execute(f"SELECT COUNT(DISTINCT eid), SUM(raw_sec) FROM eid_day WHERE ({cond}) "
                     f"AND in_roster=0 AND area IN ('za','zb','zc')", args).fetchone()
    out_roster = {"n": r[0], "h": (r[1] or 0) / 3600.0} if r and r[0] else None
    r = conn.execute(f"SELECT COUNT(DISTINCT wo_no), SUM(raw_sec) FROM wo_day WHERE ({cond}) "
                     f"AND area=?", args + (wl.OFFCAT,)).fetchone()
    offcat = {"n": r[0], "h": (r[1] or 0) / 3600.0} if r and r[0] else None
    long_ = {}
    for (_m, _a, wno, _e, v) in pop_rows:
        if (meta.get(wno) or {}).get("flag_long"):
            long_[wno] = long_.get(wno, 0) + v
    longd = {"n": len(long_), "h": sum(long_.values()) / 3600.0} if long_ else None
    emaint = sum(1 for mt in meta.values() if mt.get("excluded_reason") == "emaint_pm"
                 and mt.get("area") in BAR_AREAS and _overlaps(mt, lo, hi))
    emaint_by_month = {}
    for m in months:
        m_hi = month_through(m, today) or (m + "-31")
        k = sum(1 for mt in meta.values() if mt.get("excluded_reason") == "emaint_pm"
                and mt.get("area") in BAR_AREAS and _overlaps(mt, m + "-01", m_hi))
        if k:
            emaint_by_month[m] = k
    oneclick = sum(1 for mt in meta.values() if mt.get("flag_oneclick_pm")
                   and mt.get("area") in BAR_AREAS and _overlaps(mt, lo, hi))
    mig_months = []
    for m in months:
        tot = sum(v for (mm, _a, _w, _e, v) in pop_rows if mm == m)
        mig = sum(v for (mm, _a, w, _e, v) in pop_rows if mm == m and (meta.get(w) or {}).get("flag_migrated"))
        if tot > 0 and mig / tot > MIGRATED_NOTE_SHARE:
            mig_months.append(m)
    r = conn.execute("SELECT MIN(day) FROM ledger_day WHERE coverage_flag='clipped'").fetchone()
    # 「口徑沿革：YYYY-MM 起應生產時段」＝**第一個整月都是應生產時段的月**（C-DT1 月尺），
    # 不是第一個有 clipped 日的月（2026-08 只有 24–31 日涵蓋、該月仍是 24h 軸）。
    first_rw = None
    if r and r[0]:
        for m in wl.months_between(r[0][:7], wl.production_month(today)):
            if month_range(m, today) and month_axis(conn, m, today) == "runwindow":
                first_rw = m
                break
    kv = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM ledger_meta")}
    return {"out_roster": out_roster, "offcat": offcat, "long": longd,
            "emaint_pm": emaint or None, "emaint_by_month": emaint_by_month,
            "oneclick_pm": oneclick or None, "migrated_months": mig_months or None,
            "axis_since": first_rw, "a11_since": (kv.get("a11_since") or None),
            "a11_source": kv.get("a11_source")}


# ---------------------------------------------------------------------------
# 版面幾何（inline SVG；§7.2）
# ---------------------------------------------------------------------------

def _nice_max(v):
    if v <= 0:
        return 10.0
    import math
    e = 10 ** math.floor(math.log10(v))
    for f in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if f * e >= v:
            return f * e
    return 10 * e


def chart_geometry(months_model, W=760, H=300, PADL=52, PADR=14, PADT=22, PADB=58):
    """→ {W,H, ticks, bars:[{x,w,month,axis,segs:[{area,y,h,val}], top_y}], seps:[x]}。
       柱段自下而上 Zone A → Zone B → Zone C；兩尺交界畫垂直虛線（§7.2 ⑫）。"""
    n = max(1, len(months_model))
    xw, yh = W - PADL - PADR, H - PADT - PADB
    ymax = _nice_max(max([x["total_h"] for x in months_model] + [0.0]))
    col = xw / n
    bw = min(56.0, col * 0.56)
    bars, seps = [], []
    for i, x in enumerate(months_model):
        cx = PADL + i * col + col / 2.0
        acc = 0.0
        segs = []
        for a in BAR_AREAS:
            v = x["areas"].get(a) or 0.0
            if v <= 0:
                continue
            y0 = PADT + (1 - (acc + v) / ymax) * yh
            h = v / ymax * yh
            segs.append({"area": a, "y": round(y0, 2), "h": round(h, 2), "val": v})
            acc += v
        bars.append({"x": round(cx - bw / 2.0, 2), "w": round(bw, 2), "cx": round(cx, 2),
                     "month": x["month"], "axis": x["axis"], "segs": segs,
                     "top_y": round(PADT + (1 - acc / ymax) * yh, 2), "total": acc})
        if i and months_model[i - 1]["axis"] != x["axis"]:
            seps.append(round(PADL + i * col, 2))
    ticks = [{"y": round(PADT + (1 - f) * yh, 2), "v": ymax * f} for f in (0, 0.25, 0.5, 0.75, 1)]
    return {"W": W, "H": H, "PADL": PADL, "PADT": PADT, "yh": yh, "xw": xw, "base_y": PADT + yh,
            "ticks": ticks, "bars": bars, "seps": seps, "ymax": ymax}


# ---------------------------------------------------------------------------
# P3：LLM 摘要（prompt／清洗／快取；呼叫本身在 app 層＝唯一 I/O 點）
# ---------------------------------------------------------------------------

# ⚠ 升版規則：prompt 文字或送出的輸入形狀一改就升 `dt-sum-vN`（快取鍵含它；不升＝舊摘要照回）。
#   v2（2026-09-24，#174 ①）：輸入改白話標籤、狀態／類型轉白話、時間只給台北日期、禁欄位名／代碼。
PROMPT_VERSION = "dt-sum-v2"
SUMMARY_FIELDS = ("brief_description", "diagnosis", "action_taken", "notes", "parts",
                  "work_type", "status", "opened_at", "closed_at", "confirmed_reason_code")


def detail_person_values(detail):
    """detail 裡**人員欄**的值（技術員、開單人、history 的操作者…）＝輸出清洗的黑名單。
       欄名判定沿用 `board_attrib._person_ish`（`_PERSON_EXACT`／`_PERSON_SUB` 的同一份字典）。"""
    import board_attrib as wattr
    vals = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if wattr._person_ish(k) or str(k).lower() in ("source_actor", "opened_by", "closed_by",
                                                                  "created_by", "updated_by",
                                                                  "author"):   # #174② 09-26 裁補（notes[].author）
                    if isinstance(v, str) and v.strip():
                        vals.add(v.strip())
                        if v.startswith("human:"):
                            vals.add(v[6:])
                    elif isinstance(v, (list, tuple)):
                        vals.update(str(x).strip() for x in v if isinstance(x, str) and x.strip())
                else:
                    walk(v)
        elif isinstance(o, (list, tuple)):
            for x in o:
                walk(x)
    walk(detail or {})
    return {v for v in vals if len(v) >= 2}


def _replace_names(s, names, ignore_case=False, min_len=1):
    """把 `names`（長的先換，避免短名先吃掉長名的一段）在 `s` 內換成「—」。→ (s, n_hits)。
       輸出清洗（`scrub_text` ①，區分大小寫、原行為）與摘要輸入剝除（`_strip_known_names`，
       不分大小寫、值先 strip、長度 < `min_len` 跳過）共用這一份替換邏輯。"""
    import re
    s = str(s or "")
    hits = 0
    pool = {str(n).strip() if ignore_case else n for n in names if n}
    for nm in sorted((n for n in pool if n and len(n) >= min_len), key=len, reverse=True):
        if ignore_case:
            s, k = re.subn(re.escape(nm), "—", s, flags=re.I)
            hits += k
        elif nm in s:
            hits += s.count(nm)
            s = s.replace(nm, "—")
    return s, hits


def _strip_known_names(text, names):
    """#174 ②（2026-09-26 Fable 代裁）：摘要**輸入**層剝「已知人名值」——`detail_person_values`
       的值在送 LLM 前從自由文字換成「—」（不分大小寫、值先 strip、< 2 字元跳過防誤殺全篇）。
       ⚠ 只管結構化人員欄出現過的值；裸生人名**不在此層**（靠 prompt 指令＋`scrub_text` 輸出清洗）。
       否決通用 NER：英文名與料號／工具名同形會誤殺、中文裸名抓不全。"""
    return _replace_names(text, names, ignore_case=True, min_len=2)[0]


def summary_input(detail):
    """送 LLM 的輸入＝`SUMMARY_FIELDS` 白名單（**人員欄在送出前就不送**＝§8、§24）。"""
    d = detail or {}
    out = {}
    for k in SUMMARY_FIELDS:
        v = d.get(k)
        if v in (None, "", [], {}):
            continue
        out[k] = v
    return out


# v2：送 LLM 的**白話**標籤與值映射（C-DT8：摘要不得講出欄位名、狀態碼、UTC 時戳；#174 ①）。
#   鍵名只留在程式裡；prompt 看不到 `SUMMARY_FIELDS` 任何原始鍵名。
_SUM_LABELS = {
    "en": {"brief_description": "Problem", "diagnosis": "Diagnosis", "action_taken": "Action taken",
           "notes": "Notes", "parts": "Parts used", "work_type": "Type", "status": "Status",
           "opened_at": "Opened (date)", "closed_at": "Closed (date)",
           "confirmed_reason_code": "Confirmed fault code"},
    "zh": {"brief_description": "問題描述", "diagnosis": "診斷", "action_taken": "處置",
           "notes": "備註", "parts": "使用零件", "work_type": "類型", "status": "狀態",
           "opened_at": "開單日期", "closed_at": "結案日期",
           "confirmed_reason_code": "確認故障碼"},
}
_SUM_WORK_TYPE = {"REACTIVE": {"en": "Repair", "zh": "維修"}, "PM": {"en": "PM", "zh": "保養"},
                  "PREVENTIVE": {"en": "PM", "zh": "保養"}}
_SUM_STATUS = {
    "OPEN": {"en": "open, not started", "zh": "已開單、未處理"},
    "IN_PROGRESS": {"en": "in progress", "zh": "處理中"},
    "ON_HOLD": {"en": "on hold", "zh": "暫停"},
    "COMPLETED": {"en": "completed", "zh": "已完成"},
    "CLOSED": {"en": "closed", "zh": "已結案"},
    "CANCELLED": {"en": "cancelled", "zh": "已取消"},
    "CANCELED": {"en": "cancelled", "zh": "已取消"},
}
_SUM_MISSING_KEYS = ("diagnosis", "action_taken", "notes")
_SUM_DROP_SUBKEYS = ("source_actor", "author", "entry_type", "opened_by", "closed_by",
                     "created_by", "updated_by")


def _sum_date(v):
    """ISO（常見 `…Z` UTC）→ 台北日期 `YYYY-MM-DD`；解不了回 None（不送、不硬塞原字串）。"""
    if v in (None, ""):
        return None
    if isinstance(v, _dt.datetime):
        t = v
    else:
        x = str(v).strip()
        try:
            t = _dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
        except ValueError:
            return x[:10] if len(x) >= 10 and x[4:5] == "-" and x[7:8] == "-" else None
    if t.tzinfo is not None:
        t = t.astimezone(_dt.timezone(_dt.timedelta(hours=8)))
    return t.strftime("%Y-%m-%d")


def _sum_enum(table, v, lang):
    """代碼值 → 白話；未映射的值轉成「小寫、底線改空格」（不讓 `ON_HOLD` 形原樣出現）。"""
    m = table.get(str(v).strip().upper())
    if m:
        return m.get(lang) or m["en"]
    return str(v).strip().replace("_", " ").lower()


def _sum_text(v):
    """notes／parts 等結構值攤成純文字（**不**帶子鍵名；`*_at` 子值轉台北日期）；人員子鍵一律不送。"""
    import board_attrib as wattr
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        vals = []
        for kk, vv in v.items():
            if wattr._person_ish(kk) or str(kk).lower() in _SUM_DROP_SUBKEYS:
                continue
            if isinstance(vv, (str, int, float)) and str(vv).strip():
                vals.append(_sum_date(vv) if str(kk).lower().endswith("_at") else str(vv).strip())
        return " ".join(x for x in vals if x)
    if isinstance(v, (list, tuple)):
        return "; ".join(x for x in (_sum_text(y) for y in v) if x)
    return str(v)


def summary_payload(detail, lang):
    """v2：`summary_input` 的白名單 → **白話標籤＋白話值**（送 LLM 的就是它；不含原始鍵名、
       狀態／類型代碼、UTC 時戳）。缺的敘述欄另回白話清單。→ (payload dict, missing labels)。
       `detail_hash` 仍以 `summary_input` 算（事實沒變就不重算；形狀變靠 `PROMPT_VERSION`）。"""
    lg = "en" if lang == "en" else "zh"
    lab = _SUM_LABELS[lg]
    inp = summary_input(detail)
    known = detail_person_values(detail)      # #174 ②：自由文字送出前剝已知人名值
    out = {}
    for k in SUMMARY_FIELDS:
        if k not in inp:
            continue
        v = inp[k]
        if k in ("opened_at", "closed_at"):
            v = _sum_date(v)
        elif k == "work_type":
            v = _sum_enum(_SUM_WORK_TYPE, v, lg)
        elif k == "status":
            v = _sum_enum(_SUM_STATUS, v, lg)
        else:
            v = _strip_known_names(_sum_text(v), known)
        if v:
            out[lab[k]] = v
    missing = [lab[k] for k in _SUM_MISSING_KEYS if lab[k] not in out]
    return out, missing


def summary_prompt(detail, lang):
    """結構固定「症狀→處置→結果或未結」、2–4 句、不腦補、資料單薄就白話說缺什麼（§8／C-DT8）。
       en／zh **各自生成**（同一份輸入各產一次，不是翻譯）。v2：輸入全白話、禁欄位名／代碼／時戳。"""
    payload, missing = summary_payload(detail, lang)
    if lang == "en":
        miss = ("Not recorded on this work order: " + ", ".join(missing) + "."
                if missing else "All narrative items are recorded.")
        return (
            "You summarise ONE maintenance work order for an equipment manager.\n"
            "Rules: 2-4 sentences; fixed order symptom -> action -> result (or what is still open). "
            "Use ONLY the facts in the record below; never invent a cause, action or outcome. "
            "If something is not recorded, say so in one plain sentence (e.g. 'the record does not "
            "say what was done'). "
            "Never write database field names, status codes, raw English enum values, ISO timestamps "
            "or clock times; use plain words and dates only. "
            "Never mention any person or name; attribute everything to the machine. "
            "Plain text only, no markdown, no preamble.\n"
            f"{miss}\n"
            "Write in English.\n"
            "WORK ORDER RECORD:\n" + json.dumps(payload, ensure_ascii=False, default=str))
    miss = ("這張工單以下項目未填：" + "、".join(missing) + "。" if missing else "敘述項目都有填。")
    return (
        "你要替設備主管摘要「一張」維修工單。\n"
        "規則：2–4 句；固定順序＝症狀 → 處置 → 結果（或尚未解決的事）。"
        "只能用下面紀錄裡的事實，不得自行推測原因、處置或結果。"
        "紀錄沒寫的，用一句白話帶過（例：「紀錄未寫處置」）。"
        "不得出現資料庫欄位名、狀態代碼、原始英文代碼值、ISO 時間戳或時刻；只用白話與日期。"
        "不得提到任何人或人名，一律歸因到機台。"
        "只輸出純文字，不要 markdown、不要開場白。\n"
        f"{miss}\n"
        "用繁體中文寫。\n"
        "工單紀錄：\n" + json.dumps(payload, ensure_ascii=False, default=str))


def scrub_text(text, names=()):
    """輸出人名清洗（§8「輸出再過一次」）：① detail 人員欄出現過的值（`detail_person_values`）
       一律換成「—」；② 以 `board_attrib._PERSON_EXACT`／`_PERSON_SUB` 的人員詞開頭、後接
       一個專有名詞（英文大寫開頭字／2–4 個中文字）⇒ 換成該詞＋「—」。回 `(text, n_hits)`。"""
    import re
    import board_attrib as wattr
    s, hits = _replace_names(text, names)
    roles = sorted({w for w in wattr._PERSON_EXACT if len(w) >= 4} | set(wattr._PERSON_SUB) - {"_name", "name_"},
                   key=len, reverse=True)
    pat = re.compile(r"\b(" + "|".join(re.escape(r) for r in roles) + r")s?\b[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
                     re.I)

    def _rep(m):
        nonlocal hits
        hits += 1
        return m.group(1) + " —"
    s = pat.sub(_rep, s)
    zh = re.compile(r"(技術員|工程師|操作員|作業員|人員|師傅)([一-鿿]{2,4})")

    def _rep_zh(m):
        nonlocal hits
        hits += 1
        return m.group(1)
    s = zh.sub(_rep_zh, s)
    return s, hits


def detail_hash(detail):
    import hashlib
    blob = json.dumps(summary_input(detail), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def summary_cache_get(conn, wo_no, lang, dhash):
    """快取命中條件＝(wo_no, lang, PROMPT_VERSION) 存在且 detail_hash 相同（§8）。"""
    r = conn.execute("SELECT detail_hash, body_json FROM wo_summary WHERE wo_no=? AND lang=? "
                     "AND prompt_version=?", (str(wo_no), lang, PROMPT_VERSION)).fetchone()
    if r and r[0] == dhash:
        try:
            return json.loads(r[1])
        except (TypeError, ValueError):
            return None
    return None


def summary_cache_put(conn, wo_no, lang, dhash, body):
    """app 是 `wo_summary` 的**唯一**寫者（事實列不碰；§5.5）。凍結不管摘要。"""
    conn.execute("INSERT INTO wo_summary(wo_no, lang, prompt_version, detail_hash, body_json, created_at) "
                 "VALUES(?,?,?,?,?,?) ON CONFLICT(wo_no, lang, prompt_version) DO UPDATE SET "
                 "detail_hash=excluded.detail_hash, body_json=excluded.body_json, "
                 "created_at=excluded.created_at",
                 (str(wo_no), lang, PROMPT_VERSION, dhash, json.dumps(body, ensure_ascii=False),
                  _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def summarize_one(detail, lang, post, budget_check=None, budget_commit=None, conn=None, wo_no=None):
    """一張單一語言的摘要。→ {"text"|None, "state": ok|cached|unavailable|budget|thin, "reason"}。

       · 快取命中 ⇒ 零呼叫（`state='cached'`）。
       · 保險絲（`summary` 桶）用盡 ⇒ `state='budget'`、不呼叫。
       · `post(prompt)` 回 None（gateway 不可達／逾時）⇒ `state='unavailable'`、**不寫快取**、不重試。
       · 成功 ⇒ 人名清洗 → 寫快取。"""
    dh = detail_hash(detail)
    if conn is not None and wo_no is not None:
        hit = summary_cache_get(conn, wo_no, lang, dh)
        if hit:
            return dict(hit, state="cached")
    if budget_check is not None and not budget_check():
        return {"text": None, "state": "budget", "reason": "budget"}
    if budget_commit is not None:
        budget_commit()
    reply = post(summary_prompt(detail, lang))
    if not reply:
        return {"text": None, "state": "unavailable", "reason": "gateway"}
    text, n = scrub_text(reply, detail_person_values(detail))
    body = {"text": text.strip(), "scrubbed": n}
    if conn is not None and wo_no is not None:
        try:
            summary_cache_put(conn, wo_no, lang, dh, body)
        except Exception:                               # noqa: BLE001（快取寫失敗不害死面板）
            pass
    return dict(body, state="ok")


# ---------------------------------------------------------------------------
# i18n（`dt_` 前綴；app.py `I18N[_l].update(I18N_DOWNTIME[_l])`；鍵集對稱在 selftest＋app assert）
# ---------------------------------------------------------------------------

I18N_DOWNTIME = {
    "en": {
        "nav_downtime": "Downtime · Monthly",
        "dt_title": "Downtime",
        "dt_sub": ("Monthly work-order downtime of Zones A / B / C — whole-machine view, "
                   "overlapping work orders on one machine counted once. Click a bar segment for "
                   "that area-month's top 5 work orders."),
        "dt_from": "From", "dt_to": "to", "dt_cap": "Up to {n} months.",
        "dt_type_all": "All", "dt_type_repair": "Repair", "dt_type_pm": "PM",
        "dt_type_other": "Other",
        "dt_partial": "{m} (to {d})",
        "dt_kpi_total": "Total downtime", "dt_kpi_total_tip": "Sum of the bars: per machine, overlapping work orders counted once.",
        "dt_kpi_wo": "Work orders", "dt_kpi_wo_tip": "Distinct work orders with downtime in the selection.",
        "dt_kpi_eids": "Machines down", "dt_kpi_eids_tip": "Distinct machines with downtime in the selection.",
        "dt_kpi_avg": "Avg per machine", "dt_kpi_avg_tip": "Total downtime ÷ {n} machines (Zone A + B + C machines on the cluster roster, current roster).",
        "dt_kpi_top5": "Top-5 share", "dt_kpi_top5_tip": "Σ of each area-month's top 5 ÷ total. Overlapping work orders count in each, so this can over-state.",
        "dt_kpi_delta": "Monthly change", "dt_kpi_delta_tip": "Last selected month vs the average of the other selected months ({b}).",
        "dt_delta_cross": "cross-axis, not compared", "dt_delta_single": "—",
        "dt_mixed": "Selection spans both rulers — the total adds full-day months and run-time months.",
        "dt_axis_24h": "full day", "dt_axis_rw": "run time",
        "dt_sep_note": "No shift data through {m}: full-day hours (switches after backfill).",
        "dt_legend": "Areas", "dt_h": "h",
        "dt_seg_tip": "{a} · {m} · {h} h · {n} machines · {axis}",
        "dt_table_view": "Table view",
        "dt_col_month": "Month", "dt_col_area": "Area", "dt_col_h": "Downtime h", "dt_col_axis": "Axis",
        "dt_panel_hint": "Click a bar segment to see that area-month's top 5 work orders.",
        "dt_panel_head": "{a} · {m} · {h} h ({axis})",
        "dt_repeat": "Repeat: {items}",
        "dt_col_rank": "#", "dt_col_wo": "Work order", "dt_col_machine": "Machine", "dt_col_type": "Type",
        "dt_col_hpct": "Downtime h (share)", "dt_col_stage": "Repair / parts / vendor",
        "dt_col_span": "Start → end", "dt_col_code": "Code", "dt_col_flags": "Flags",
        "dt_flag_migrated": "migrated: whole span", "dt_flag_long": "long-open",
        "dt_open": "open",
        "dt_overlap_note": "Overlapping time on one machine counts in each work order, but only once in the bar.",
        "dt_no_rows": "No work-order downtime in this segment.",
        "dt_ai_badge": "AI summary", "dt_ai_demo": "AI summary (demo)",
        "dt_ai_off": "AI summary not enabled yet.",
        "dt_ai_unavail": "Summary unavailable right now ({r}).",
        "dt_ai_budget": "Today's summary quota is used up.",
        "dt_ai_r_gateway": "assistant service unreachable", "dt_ai_r_detail": "work order detail unavailable",
        "dt_orig": "Original text",
        "dt_orig_none": "cmms has only a brief description for this work order.",
        "dt_f_brief": "Brief", "dt_f_diag": "Diagnosis", "dt_f_action": "Action taken",
        "dt_f_notes": "Notes", "dt_f_parts": "Parts",
        "dt_ft_roster": "No cluster assignment: {n} machines, {h} h full-day (not in the chart).",
        "dt_ft_zd": "Zone D is not on this page.",
        "dt_ft_offcat": "Work orders on assets outside the equipment catalog: {n}, {h} h full-day.",
        "dt_ft_long": "Long-open work orders (> {d} days): {n}, {h} h.",
        "dt_ft_emaint": "Excluded eMaint-era PM work orders: {n} (no status history; counting the whole span would badly over-state).",
        "dt_ft_migrated": "Historical months ({m}) are mostly migrated work orders, estimated open → close.",
        "dt_ft_oneclick": "PM work orders closed in one click (0 h measurable): {n}.",
        "dt_ft_axis": "Axis history: expected run time from {m}.",
        "dt_ft_a11": "Long-stop days deducted from {d}.",
        "dt_ft_cancel": "Cancelled / voided work orders are not counted.",
        "dt_ft_roster_now": "Cluster roster = today's roster (it has no history).",
        "dt_emaint_month": "eMaint-era PM not counted",
        "dt_snapshot_na": "The downtime page is not available on the snapshot source.",
        "dt_empty": "No work-order ledger yet. Run the ledger job first (python _wo_ledger.py --backfill …).",
        "dt_none_selected": "No months selected.",
        "dt_demo_note": "DEMO — fully synthetic work orders; no cmms links, no AI calls.",
        "dt_loading": "Loading…",
    },
    "zh": {
        "nav_downtime": "停機月報",
        "dt_title": "停機月報",
        "dt_sub": "Zone A／B／C 三區每月工單停機——整台機計、同機重疊工單只算一次。點柱段看該區該月前五張工單。",
        "dt_from": "起月", "dt_to": "至", "dt_cap": "最多 {n} 個月。",
        "dt_type_all": "全部", "dt_type_repair": "維修", "dt_type_pm": "保養",
        "dt_type_other": "其他",
        "dt_partial": "{m}（至 {d} 日）",
        "dt_kpi_total": "總停機", "dt_kpi_total_tip": "柱高加總：同一台機重疊的工單只算一次。",
        "dt_kpi_wo": "工單", "dt_kpi_wo_tip": "選取範圍內有停機的工單張數。",
        "dt_kpi_eids": "停機台數", "dt_kpi_eids_tip": "選取範圍內有停機的機台數。",
        "dt_kpi_avg": "每台平均", "dt_kpi_avg_tip": "總停機 ÷ {n} 台（Zone A＋B＋C 在綑名冊內的台數，用當下名冊）。",
        "dt_kpi_top5": "前五佔比", "dt_kpi_top5_tip": "各區各月前五加總 ÷ 總停機。同機重疊時段在各單分別計入，可能偏高。",
        "dt_kpi_delta": "月均變化", "dt_kpi_delta_tip": "最末選取月 vs 其餘選取月（{b}）平均。",
        "dt_delta_cross": "跨尺不比", "dt_delta_single": "—",
        "dt_mixed": "選取同時含兩把尺：總數是全日計月與應生產時段月的加總。",
        "dt_axis_24h": "全日計", "dt_axis_rw": "應生產時段",
        "dt_sep_note": "{m} 以前無班次資料、以全日計（回補後自動切換）。",
        "dt_legend": "區", "dt_h": "h",
        "dt_seg_tip": "{a} · {m} · {h} h · {n} 台 · {axis}",
        "dt_table_view": "表格檢視",
        "dt_col_month": "月份", "dt_col_area": "區", "dt_col_h": "停機 h", "dt_col_axis": "尺",
        "dt_panel_hint": "點柱段看該區該月前五張工單。",
        "dt_panel_head": "{a} · {m} · {h} h（{axis}）",
        "dt_repeat": "慣犯：{items}",
        "dt_col_rank": "#", "dt_col_wo": "工單", "dt_col_machine": "機台", "dt_col_type": "類型",
        "dt_col_hpct": "停機 h（佔比）", "dt_col_stage": "修／等料／等外包",
        "dt_col_span": "起訖", "dt_col_code": "碼", "dt_col_flags": "旗",
        "dt_flag_migrated": "遷移單＝整段估算", "dt_flag_long": "長掛單",
        "dt_open": "未結",
        "dt_overlap_note": "同機重疊時段在各單分別計入、在柱高只計一次。",
        "dt_no_rows": "這一段沒有工單停機。",
        "dt_ai_badge": "AI 摘要", "dt_ai_demo": "AI 摘要（示範）",
        "dt_ai_off": "AI 摘要尚未啟用。",
        "dt_ai_unavail": "摘要暫不可用（{r}）。",
        "dt_ai_budget": "今日摘要額度已用盡。",
        "dt_ai_r_gateway": "助理服務連不上", "dt_ai_r_detail": "工單詳情取不到",
        "dt_orig": "原文",
        "dt_orig_none": "cmms 這張單只有簡述。",
        "dt_f_brief": "簡述", "dt_f_diag": "診斷", "dt_f_action": "處置",
        "dt_f_notes": "備註", "dt_f_parts": "零件",
        "dt_ft_roster": "無綑歸屬 {n} 台、全日計 {h} h（未計入上圖）。",
        "dt_ft_zd": "Zone D 不在本頁。",
        "dt_ft_offcat": "目錄外工單 {n} 張、全日計 {h} h。",
        "dt_ft_long": "長掛單（超過 {d} 天）{n} 張、{h} h。",
        "dt_ft_emaint": "排除 eMaint 時代 PM 單 {n} 張（舊系統的保養單沒有狀態轉移紀錄，整段計入會嚴重高估）。",
        "dt_ft_migrated": "歷史月份（{m}）多為遷移單，以開單→結單整段估算。",
        "dt_ft_oneclick": "一鍵結單的保養單（量不到停機）{n} 張。",
        "dt_ft_axis": "口徑沿革：{m} 起應生產時段。",
        "dt_ft_a11": "長停扣除自 {d} 起。",
        "dt_ft_cancel": "取消／作廢的工單不計。",
        "dt_ft_roster_now": "綑名冊用當下名冊（名冊沒有歷史版本）。",
        "dt_emaint_month": "eMaint 時代 PM 單不計",
        "dt_snapshot_na": "快照源沒有停機月報。",
        "dt_empty": "還沒有工單日帳。先跑日帳寫者（python _wo_ledger.py --backfill …）。",
        "dt_none_selected": "沒有選取月份。",
        "dt_demo_note": "DEMO——工單全為合成；沒有 cmms 連結、不呼叫 AI。",
        "dt_loading": "載入中…",
    },
}


def _selftest():
    n = [0]

    def ck(c, m):
        if not c:
            raise AssertionError(m)
        n[0] += 1
    ck(set(I18N_DOWNTIME["en"]) == set(I18N_DOWNTIME["zh"]), "i18n 鍵集對稱")
    ck(all(k.startswith("dt_") or k == "nav_downtime" for k in I18N_DOWNTIME["en"]), "dt_ 前綴")
    ck(not any("窗" in v for v in I18N_DOWNTIME["zh"].values()), "zh 零「窗」（C-RW2 顯示層）")
    conn, info = open_source("demo")
    ck(conn is not None and info["today"] is not None, "demo 可開")
    opts = month_options(conn, info["today"])
    ms = default_months(opts, info["today"])
    ck(len(ms) == DEFAULT_N_MONTHS and all(m < wl.production_month(info["today"]) for m in ms),
       "預設＝最近 6 個完整月")
    _td = info["today"]
    ck(resolve_range("", "", opts, _td) == (ms, False), "範圍：兩端缺 ⇒ 預設 6 個完整月")
    ck(resolve_range(ms[-1], ms[0], opts, _td) == (ms, False), "範圍：from > to ⇒ 交換")
    ck(resolve_range("", "", opts, _td, legacy_months=f"{ms[0]},{ms[-1]}") == (ms, False),
       "範圍：舊 months= 取 min／max 展開（連續）")
    _big = resolve_range(opts[0], opts[-1], opts, _td, cap=3)
    ck(_big == (opts[-3:], True), "範圍：超過上限 ⇒ 保留最近 N 個、capped")
    ck(resolve_range("1999-01", "bogus", opts, _td) == (ms, False), "範圍：白名單外 ⇒ 預設")
    m = downtime_model(conn, ms, info["today"])
    ck(m["kpi"]["roster_n"] == len(roster_eids()), "分母＝名冊內三區台數（算的、不硬編）")
    ck(all(x["axis"] in ("24h", "runwindow") for x in m["months"]), "axis 值域")
    m2 = downtime_model(conn, ms, info["today"], xlong=True)
    ck(m2["kpi"]["total_h"] <= m["kpi"]["total_h"] + 1e-9, "排除長掛單不會變多")
    m3 = downtime_model(conn, ms, info["today"], typ="repair")
    ck(all(r["work_type"] == "REACTIVE" for rows in m3["top"].values() for r in rows), "類型 chip")
    ck(all(set(r) == set(TOP_FIELDS) for rows in m["top"].values() for r in rows), "TOP_FIELDS 白名單（C-DT12）")
    s, k = scrub_text("Technician John Smith replaced the belt; 技術員王小明更換皮帶", {"Wang"})
    ck("John" not in s and "王小明" not in s and k >= 2, "人名清洗")
    import re as _re
    _det = {"brief_description": "pump fault", "work_type": "REACTIVE", "status": "ON_HOLD",
            "opened_at": "2026-08-02T18:12:41Z", "closed_at": "2026-08-02T17:00:00Z",
            "notes": [{"occurred_at": "2026-08-03T01:00:00Z", "body": "waiting", "author": "Jane Doe"}],
            "technician": "John Smith"}
    for _lg in ("en", "zh"):
        _p = summary_prompt(_det, _lg)
        ck(not any(k in _p for k in SUMMARY_FIELDS if "_" in k)
           and not any(f'"{k}"' in _p for k in SUMMARY_FIELDS), f"v2 prompt 無原始鍵名（{_lg}）")
        ck(not _re.search(r"T\d\d:\d\d:\d\dZ", _p), f"v2 prompt 無 UTC 時戳（{_lg}）")
        ck("ON_HOLD" not in _p and "REACTIVE" not in _p, f"v2 prompt 無狀態／類型碼（{_lg}）")
        ck("Jane Doe" not in _p and "John Smith" not in _p, f"v2 prompt 無人名（{_lg}）")
    ck("2026-08-03" in summary_prompt(_det, "zh"), "opened_at 轉台北日期（18:12Z → 隔天）")
    ck("處置" in summary_prompt(_det, "zh") and "未填" in summary_prompt(_det, "zh"), "缺欄白話點名")
    # #174 ②：摘要輸入層剝已知人名值（detail_person_values → —）；裸名不管；短值不觸發。
    _det2 = {"brief_description": "JOHN SMITH reported pump fault", "diagnosis": "john smith found worn o-ring",
             "notes": [{"body": "Handed over by Jane Doe; Bob Lee checked seal", "author": "Jane Doe"}],
             "action_taken": "replaced o-ring, ran X test", "technician": " John Smith ", "opened_by": "X",
             "status_history": [{"to_status": "OPEN", "source_actor": "human:JANE DOE"}]}
    for _lg in ("en", "zh"):
        _pl, _ = summary_payload(_det2, _lg)
        _blob = json.dumps(_pl, ensure_ascii=False).lower()
        ck("john smith" not in _blob and "jane doe" not in _blob and _blob.count("—") >= 3,
           f"#174② 輸入剝已知人名值（含大小寫變體、strip）（{_lg}）")
        ck("Bob Lee" in json.dumps(_pl, ensure_ascii=False), f"#174② 名單外裸名原樣通過＝輸入層不管裸名（{_lg}）")
        ck("ran X test" in json.dumps(_pl, ensure_ascii=False), f"#174② 短值（≤1 字元）不觸發替換（{_lg}）")
    ck(_strip_known_names("x-ray by X", {"X", "—", " "}) == "x-ray by X", "#174② 短值直呼也不觸發")
    ck(scrub_text("jane doe ok", {"Jane Doe"})[0] == "jane doe ok", "輸出清洗 ① 行為不變（區分大小寫）")
    # #174② 09-26 裁補：notes[].author 收進黑名單（輸入層＋輸出清洗 ① 同步）；min_len 守門同樣生效。
    _det3 = {"brief_description": "pump fault",
             "notes": [{"body": "Mary Chen swapped the pump; Q verified", "author": "Mary Chen"},
                       {"body": "Q checked again", "author": "Q"}]}
    _pv3 = detail_person_values(_det3)
    ck("Mary Chen" in _pv3 and "Q" not in _pv3, "#174② author 入黑名單、1 字元 author 被 min_len 擋")
    for _lg in ("en", "zh"):
        _j3 = json.dumps(summary_payload(_det3, _lg)[0], ensure_ascii=False)
        ck("Mary Chen" not in _j3 and "— swapped the pump" in _j3 and "Q verified" in _j3
           and "Q checked again" in _j3, f"#174② 只在 author 的人名：輸入層換「—」、短 author 不觸發（{_lg}）")
    ck(scrub_text("Mary Chen fixed it", _pv3)[0] == "— fixed it", "#174② 輸出清洗 ① 也抓得到 author 值")
    print(f"board_downtime selftest: {n[0]} checks PASS")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
