#!/usr/bin/env python3
"""Shared presentation helpers for product-capability tags (SPEC-PRODUCT-TAGS §6).

All capability facts and URL parsing stay delegated to :mod:`board_tags`.  Pages
consume the small, template-ready dictionaries returned here so chip/origin/filter
semantics cannot drift between equipment, utilization, station, and cost.
"""

import os

from board_tags import (FAMILIES, LETTER, PRODUCT_TAGS, filter_eids,
                          parse_tag_param, record_of, tags_of)

# ══════════════════════════════════════════════════════════════════════════════
# 活動層（M3 C6；SPEC-PRODUCT-TAGS §2.6-a）
# ══════════════════════════════════════════════════════════════════════════════
#   能力層（`board_tags`＝730 天靜態普查）回答「這台**會跑**什麼」；
#   活動層（日帳 v4 的 `families` 欄，讀腿＝`board_data.read_family_activity`）
#   回答「這台**這幾天真的在跑**什麼」。**兩者永不互相頂替**（C-PT1）。
#
#   ★ 三態判準（Chris 2026-09-04 裁；SPEC §2.6-a 的表就是這裡唯一的依據）：
#     · 窗內有該 family 的段        ⇒ recent=True 、**實心**、`tag_tip_recent`
#     · 站被查過、這台這個 family 零段 ⇒ recent=False、**空心**、`tag_tip_idle`
#     · 站沒被查過（沒查／timeline skipped／尚無 v4 檔）
#                                   ⇒ recent=None 、**維持實心**、`tag_tip_unknown`
#   ⚠ **不做第三種形**——`default` 台維持空心、走 `tag_tip_default`。看到有人加
#     `.ptag.dashed` 就是把裁決做反了。把「沒查」畫成空心＝把「不知道」說成「閒著」，
#     那正是 C-PT3 要防的事，也是這一整套設計最貴的錯。
#   ⚠ `?tag=` 的過濾語意**一個位元不動**（C-PT4／C-PT5）：`tag_filter` 沒有、也不會有
#     `activity` 參數。活動層只影響 chip 的形與 tooltip，**不影響過濾結果**。
RECENT_DAYS_ENV = "BOARD_TAG_RECENT_DAYS"
RECENT_DAYS_DEFAULT = 7          # Chris 2026-09-03 裁：窗＝7 天（與 /equipment 探測窗同尺）


def recent_days():
    """近期窗（天）＝**活動層窗的唯一出處**；env `BOARD_TAG_RECENT_DAYS` 覆寫。

    壞值／非正數 ⇒ 回預設 7（URL/env 是人可以亂打的東西，不炸）。呼叫端請一律走本函式
    而不是讀 `RECENT_DAYS` 常數——後者是 import 當下的快照，改 env 不會生效。
    """
    try:
        n = int(str(os.environ.get(RECENT_DAYS_ENV, "")).strip() or RECENT_DAYS_DEFAULT)
    except (TypeError, ValueError):
        return RECENT_DAYS_DEFAULT
    return n if n > 0 else RECENT_DAYS_DEFAULT


RECENT_DAYS = recent_days()      # import 當下的快照（測試/顯示用；判定一律走 recent_days()）

# tooltip 的 as-of 未知時的佔位（chip 的 title 永遠印得出一個東西，不留空洞）
AS_OF_UNKNOWN = "—"

# chip 的 css 閉集（模板只吃這四種；`.ptag.recent` 不需要自己的規則＝`.observed` 已是實心）
CSS_OBSERVED = "observed"
CSS_RECENT = "observed recent"
CSS_IDLE = "observed idle"
CSS_DEFAULT = "default"
CSS_VALUES = (CSS_OBSERVED, CSS_RECENT, CSS_IDLE, CSS_DEFAULT)

TIP_KEYS = ("tag_tip_observed", "tag_tip_default",
            "tag_tip_recent", "tag_tip_idle", "tag_tip_unknown")


def _split_activity(activity):
    """`activity` → `(act_map | None, meta)`。`act_map is None` ＝ **legacy 模式**。

    吃三種形：
      · `None`                      ⇒ `(None, {})`＝活動層未接線 ⇒ 輸出與 C6 之前**逐位元相同**。
      · `read_family_activity()` 回的 `(act, meta)` 二元組（正規形）。
      · 裸 `act` dict（沒有 meta）    ⇒ `stations_seen` 視為空 ⇒ 除了正訊號外一律 unknown
                                      （保守側：寧可說「不知道」，也不說「閒著」）。
    壞形一律退回 legacy，不炸。
    """
    if activity is None:
        return None, {}
    if isinstance(activity, tuple) or isinstance(activity, list):
        if len(activity) != 2:
            return None, {}
        act, meta = activity
        if not isinstance(act, dict):
            return None, {}
        return act, (meta if isinstance(meta, dict) else {})
    if isinstance(activity, dict):
        # `{"activity": …, "meta": …}` 也收（呼叫端習慣不同，不值得為此炸掉整頁）
        if "activity" in activity or "meta" in activity:
            act = activity.get("activity")
            meta = activity.get("meta")
            if not isinstance(act, dict):
                return None, {}
            return act, (meta if isinstance(meta, dict) else {})
        return activity, {}
    return None, {}


def _covered(eid, meta):
    """這顆 EID 的**普查站是不是全都被查過**（⇒ 「零段」才能讀成「閒著」）。

    ⚠ 刻意用 `all` 而不是 `any`：一台跨兩站的機器只查了一站時，另一站仍可能在跑
    ——那是「不知道」，不是「閒著」。表外機（`record_of` 回 None）恆 False。
    """
    seen = meta.get("stations_seen")
    if not seen:
        return False
    rec = record_of(eid)
    stations = tuple(rec.get("stations") or ()) if isinstance(rec, dict) else ()
    if not stations:
        return False
    seen = set(seen)
    return all(s in seen for s in stations)


def _seg_count(value):
    """活動層的段數 → 非負 int（`True` 這種 bool 一律當 0；壞值當 0）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def tag_chip(eid, activity=None):
    """Return the one canonical row-chip model for ``eid``.

    ``families`` contains observed raw segment counts for the tooltip.  Default
    rows intentionally have the same ``RAM`` capability letters and differ only
    by ``origin``/``tip_key`` (C-PT3).

    ⚠ **Two shapes, two consumers — do not collapse them into one** (SPEC §6.3):
    · ``chips`` = what humans see: **one chip per letter**, in FAMILIES order,
      each with its own ``facts`` for the per-chip tooltip.  Templates render
      only this.  Glueing the letters back into a single chip reads ``RA`` as a
      product name (Chris 2026-09-02; SPEC §7 A13).
    · ``letters`` = the machine-readable string (``"RAM"``) for the CSV
      ``product_tags`` column and ``board_export``.  It stays compact and is
      **never** used as chip text.

    ``activity`` (M3 C6, SPEC §2.6-a) = ``board_data.read_family_activity()``'s
    ``(activity, meta)`` pair.  Each chip then carries its own ``recent`` /
    ``css`` / ``tip_key`` / ``tip_args``; **omitting it reproduces the pre-C6
    output bit for bit** (``css == origin``, the two legacy tip keys, and
    ``tip_args`` whose only consumed slot is ``{families}``).
    """
    tags, origin = tags_of(eid)
    # ⚠ `record_of`, not `PRODUCT_TAGS.get`: a form-④ renamed main EID (SPEC §2.5.3)
    # carries its segments under alias EIDs, so reading the raw census dict would
    # render an *observed* chip whose tooltip count is blank.
    rec = record_of(eid) or {}
    counts = rec.get("families") or {}
    facts = " · ".join(f"{family} {int(counts[family]):,}"
                       for family in FAMILIES if family in counts)
    act_map, meta = _split_activity(activity)
    live = act_map is not None
    n_days = recent_days()
    ent = act_map.get(eid) if live else None
    ent = ent if isinstance(ent, dict) else {}
    seen = ent.get("families")
    seen = seen if isinstance(seen, dict) else {}
    # as-of ＝這台最後一次有 family 資料的日 → 退窗內最後一天 → 退「—」。
    # ⚠ tooltip 一定要印得出 as-of：不印＝拿舊資料冒充「近期」（§2.6 ②）。
    as_of = ent.get("as_of") or meta.get("as_of") or meta.get("end_day") or AS_OF_UNKNOWN
    covered = _covered(eid, meta) if live else False

    def _chip(family):
        n_cap = int(counts[family]) if family in counts else None
        f_facts = f"{family} {n_cap:,}" if n_cap is not None else ""
        n_recent = _seg_count(seen.get(family))
        if not live or origin == "default":
            # default 台一律 recent=None、空心照舊（★ 不做第三種形；Chris 2026-09-04）。
            recent, css = None, (CSS_DEFAULT if origin == "default" else CSS_OBSERVED)
            tip_key = "tag_tip_default" if origin == "default" else "tag_tip_observed"
        elif n_recent > 0:
            recent, css, tip_key = True, CSS_RECENT, "tag_tip_recent"
        elif covered:
            recent, css, tip_key = False, CSS_IDLE, "tag_tip_idle"
        else:
            # 站沒被查過 ⇒ **維持實心**、只換 tooltip（把它畫成空心＝說謊）。
            recent, css, tip_key = None, CSS_OBSERVED, "tag_tip_unknown"
        return {
            "family": family,
            "letter": LETTER[family],
            # observed segment count, or None for a default (no-signal) row.
            "n": n_cap,
            # Per-chip tooltip payload: observed = "Plover N,NNN"; default = ""
            # (the default tip text carries no {families} slot, C-PT3).
            "facts": f_facts,
            "recent": recent,
            "css": css,
            "tip_key": tip_key,
            "n_recent": n_recent,
            # ⚠ 模板一律 `t[c.tip_key].format(**c.tip_args)`：**超集**傳參，
            #   `str.format` 忽略用不到的鍵 ⇒ 加鍵不必動五個模板。
            "tip_args": {"families": f_facts, "family": family,
                         "letter": LETTER[family], "n": n_recent,
                         "n_txt": f"{n_recent:,}", "days": n_days, "as_of": as_of},
        }

    chips = tuple(_chip(family) for family in FAMILIES if family in tags)
    return {
        "letters": "".join(LETTER[f] for f in FAMILIES if f in tags),
        "chips": chips,
        "origin": origin,
        "tip_key": "tag_tip_default" if origin == "default" else "tag_tip_observed",
        "families": tuple((f, int(counts[f])) for f in FAMILIES if f in counts),
        "facts": facts,
        # ---- 活動層診斷欄（模板不吃；selftest／除錯用）----
        "activity_on": live,
        "covered": covered,
        "as_of": as_of,
        "days": n_days,
    }


def tag_filter(raw, eids):
    """Parse ``?tag=`` and return one shared filter/toggle/notice model.

    Matching is delegated to ``board_tags.filter_eids`` (OR; empty/unknown =
    all).  The input order is retained.  ``also`` counts capabilities outside the
    active selection among matched rows and is only display material.
    """
    families = parse_tag_param(raw)
    source_eids = list(eids)
    matched = filter_eids(source_eids, families)
    selected = set(families)
    also = []
    if families:
        for family in FAMILIES:
            if family in selected:
                continue
            n = sum(1 for eid in matched if family in tags_of(eid)[0])
            if n:
                also.append({"family": family, "n": n})
    toggles = []
    for family in FAMILIES:
        next_families = tuple(f for f in FAMILIES
                              if (f in selected) != (f == family))
        toggles.append({
            "family": family,
            "letter": LETTER[family],
            "active": family in selected,
            "param": ",".join(LETTER[f] for f in next_families),
        })
    return {
        "active": bool(families),
        "families": families,
        "param": ",".join(LETTER[f] for f in families),
        "matched": matched,
        "matched_set": frozenset(matched),
        "n": len(matched),
        "total": len(source_eids),
        "label": " or ".join(families),
        "also": tuple(also),
        "toggles": tuple(toggles),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 產能瀑布在 `?tag=` 下的門檻規則（Chris 2026-09-05 裁 1；SPEC-CAPACITY §3.7）
# ══════════════════════════════════════════════════════════════════════════════
#   舊規則（C-CP10 原文）＝「tag 有效 ⇒ 瀑布整條退成小時」。Chris 2026-09-05 當面推翻一半：
#   **被濾掉的機台如果只佔本站生產機時的一小塊，退成小時是把一張看得懂的卡變成看不懂的卡**。
#   ⇒ 門檻規則：`excl_share ≤ 門檻` ⇒ **時間三層用過濾後的小時 × 站級速率**（尺不換，
#     動作與 `/equipment` 的 ≈PH 同型）、**不良／良品層照印全站站級值**（產品視角，不按能力拆）；
#     超過門檻才退成小時，而且**要說出超過多少**。
#   ⚠ **share 的分母是「生產機時」不是台數**：一台整窗閒著的機被濾掉，對 PH 的影響是零；
#     用台數當分母會把它算成「濾掉 20%」而白白退場。
#   ⚠ 門檻只決定**顯示形**，一個算術都不改：`capacity_waterfall` 收到什麼就算什麼。
CP_TAG_PH_MAX_EXCL_ENV = "BOARD_CP_TAG_PH_MAX_EXCL"
CP_TAG_PH_MAX_EXCL_DEFAULT = 0.05

# Kestrel 專機例外 chip 的門檻（Chris 2026-09-05 裁 3；SPEC-PRODUCT-TAGS §6.1）
AONLY_CHIP_MIN_ENV = "BOARD_TAG_AONLY_CHIP_MIN"
AONLY_CHIP_MIN_DEFAULT = 0.10


def _env_ratio(name, default):
    """env → 0..1 的比例（壞值／空 ⇒ 預設）。**負值刻意放行**——測試要用它強制走另一支。"""
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if v <= 1.0 else default


def cp_tag_ph_max_excl():
    """瀑布在 `?tag=` 下仍印 PH 的最大排除佔比（env 覆寫；每次呼叫都讀＝測試可切換）。"""
    return _env_ratio(CP_TAG_PH_MAX_EXCL_ENV, CP_TAG_PH_MAX_EXCL_DEFAULT)


def aonly_chip_min():
    """Kestrel 專機 chip 的最低佔比（低於它**整顆不渲染**＝零常駐版位）。"""
    return _env_ratio(AONLY_CHIP_MIN_ENV, AONLY_CHIP_MIN_DEFAULT)


def tag_ph_mode(producing_full_sec, producing_kept_sec, max_excl=None):
    """`?tag=` 下瀑布要不要繼續印 PH。**純函式、零 I/O（env 只在 `max_excl is None` 時讀）。**

       `producing_full_sec`＝**全站**生產機時、`producing_kept_sec`＝**顯示的那幾台**的生產機時。
       → `{"mode": "full"|"off", "excl_share", "pct", "kept_pct", "max_excl"}`。
       ⚠ 全站生產機時為 0 ⇒ `share = 0`（沒有東西可以被濾掉 ⇒ 不因為除以零而退場）。
    """
    full = max(0.0, float(producing_full_sec or 0.0))
    kept = max(0.0, float(producing_kept_sec or 0.0))
    share = 0.0 if full <= 0 else min(1.0, max(0.0, (full - kept) / full))
    thr = cp_tag_ph_max_excl() if max_excl is None else float(max_excl)
    return {"mode": ("full" if share <= thr else "off"), "excl_share": share,
            "pct": int(round(share * 100.0)), "kept_pct": int(round((1.0 - share) * 100.0)),
            "max_excl": thr}


def kestrel_only_eids(eids):
    """輸入機台集合裡的 **observed Kestrel-only** 機台（保留輸入順序、去重）。

       ⚠ `origin="default"` 的機台**一台都不算**：它的 `tags_of` 回的是 `R+A+M`
         （零訊號預設全能力，C-PT3）——把它讀成「Kestrel 專機」就是把「我們沒量到」
         說成「它只做 Kestrel」，那是憑空製造的假事實。
    """
    out, seen = [], set()
    for eid in eids or ():
        if not eid or eid in seen:
            continue
        seen.add(eid)
        fams, origin = tags_of(eid)
        if origin == "observed" and tuple(fams) == ("Kestrel",):
            out.append(eid)
    return tuple(out)


def aonly_chip(eids, idle_aonly, idle_total, min_share=None):
    """Kestrel 專機的「該跑沒跑」佔比 chip → `None`＝**整顆不渲染**（例外才出現）。

       `eids`＝真正進了分母的那幾台 A-only 機（呼叫端已與該頁的分母成員取交集）。
       `idle_aonly`／`idle_total` 只要**彼此同單位**即可（本函式算的是比例）：
       `/utilization` 餵秒（`erw_state_split.unexp_sec`）、`/station` 餵小時
       （`capacity_waterfall.idle_h`）——**同頁的兩個量必須出自同一支函式**，
       只是餵不同的機台集合。另算一份就是第二份事實，而且漂了沒有任何測試會紅。
    """
    ids = tuple(e for e in (eids or ()) if e)
    if not ids:
        return None
    total = max(0.0, float(idle_total or 0.0))
    if total <= 0:
        return None
    share = min(1.0, max(0.0, float(idle_aonly or 0.0) / total))
    thr = aonly_chip_min() if min_share is None else float(min_share)
    if share < thr:
        return None
    return {"n": len(ids), "eids": ids, "eids_txt": ", ".join(ids),
            "pct": int(round(share * 100.0)), "share": share, "min_share": thr}


# ══════════════════════════════════════════════════════════════════════════════
# --selftest（純離線：只 import `board_tags`，零 I/O、零 MES、零 app）
# ══════════════════════════════════════════════════════════════════════════════
def selftest():
    """活動層三態判準的逐格釘（SPEC-PRODUCT-TAGS §2.6-a 那張表就是這裡的規格）。

    ⚠ 三個承重的負斷言，改碼時哪一條紅了都不要「調斷言」：
      ① `activity=None` ⇒ 輸出與活動層之前**逐位元相同**（五個模板的 HTML 因此不動）。
      ② `default` 台**恆** `recent=None`＋空心＋`tag_tip_default`（不做第三種形）。
      ③ `tag_filter` 沒有 `activity` 參數（`?tag=` 一個位元不動＝C-PT4／C-PT5）。
    """
    import inspect

    fails = []
    n_checks = [0]

    def ck(cond, name):
        n_checks[0] += 1
        if not cond:
            fails.append(name)
        return bool(cond)

    # 普查表裡挑的樣本（值取自 `board_tags.PRODUCT_TAGS`，換普查檔會動的是**數字不是形**）
    OBS = "EID-00643"            # observed R+A、單站 segFlexBonding
    OBS_ST = "segFlexBonding"
    MULTI = "EID-05303"          # observed R+A、**跨兩站**（覆蓋判定用 all 不是 any）
    MULTI_ST = ("segAuxElectricalTest", "segModuleElectricalTest")
    DEF = "EID-26554"            # default（730 天零產品訊號 ⇒ R+A+M 空心）
    OFF = "EID-99999"            # 普查表外（`record_of` → None）
    # WP-10d（R-P11）：公開樹的普查表是合成的 ⇒ 樣本機台改由普查表推導（形不變：單站 observed R+A／跨兩站 observed／default）；私有樹照舊用上面的具名樣本
    import core_paths as _wp
    if _wp.is_public_tree():
        OBS, OBS_ST = next((e, r["stations"][0]) for e, r in PRODUCT_TAGS.items()
                           if r["origin"] == "observed" and tuple(r["tags"]) == tuple(FAMILIES[:2])
                           and len(r["stations"]) == 1)
        MULTI, MULTI_ST = next((e, tuple(r["stations"])) for e, r in PRODUCT_TAGS.items()
                               if r["origin"] == "observed" and len(r["stations"]) == 2)
        DEF = next(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "default")

    # ---- ① legacy：不帶 activity ⇒ 逐位元同活動層之前 ------------------------------
    legacy = tag_chip(OBS)
    ck(legacy == tag_chip(OBS, None), "legacy: activity=None 與不帶參數同")
    ck(legacy == tag_chip(OBS, activity="garbage"), "legacy: 壞形退 legacy 不炸")
    ck(legacy == tag_chip(OBS, activity=(1, 2, 3)), "legacy: 長度不對的序列退 legacy")
    ck([c["css"] for c in legacy["chips"]] == ["observed", "observed"],
       "legacy: css 就是 origin")
    ck({c["tip_key"] for c in legacy["chips"]} == {"tag_tip_observed"},
       "legacy: tip_key 是舊的 observed 鍵")
    ck(all(c["recent"] is None for c in legacy["chips"]), "legacy: recent 全 None")
    ck(legacy["activity_on"] is False, "legacy: activity_on False")
    # 舊模板算 tooltip 的方式是 `.format(families=c.facts)`；新的是 `.format(**c.tip_args)`
    # ——同一句樣板、同一個輸出，才叫逐位元相同。
    tpl = "MES 730-day observed: {families}"
    ck(all(tpl.format(**c["tip_args"]) == tpl.format(families=c["facts"])
           for c in legacy["chips"]), "legacy: tip_args 算出的 tooltip 與舊算法同字")
    ck([c["letter"] for c in legacy["chips"]] == ["R", "A"], "legacy: 一顆 chip 一個字母")
    ck(legacy["letters"] == "RA" and legacy["origin"] == "observed", "legacy: letters/origin")
    ck(tag_chip(DEF)["letters"] == "RAM", "legacy: default 台仍是 RAM 三顆")

    # ---- ② 三態判準逐格 ---------------------------------------------------------------
    meta = {"stations_seen": [OBS_ST], "as_of": "2026-09-04"}
    act_run = ({OBS: {"families": {"Plover": 12}, "days": 3, "as_of": "2026-09-03"}}, meta)
    m = tag_chip(OBS, activity=act_run)
    by_fam = {c["family"]: c for c in m["chips"]}
    ck(by_fam["Plover"]["recent"] is True, "態①: 窗內有段 ⇒ recent True")
    ck(by_fam["Plover"]["css"] == "observed recent", "態①: css observed recent（實心）")
    ck(by_fam["Plover"]["tip_key"] == "tag_tip_recent", "態①: tag_tip_recent")
    ck(by_fam["Plover"]["tip_args"]["n"] == 12
       and by_fam["Plover"]["tip_args"]["n_txt"] == "12", "態①: tooltip 帶近 N 天段數")
    ck(by_fam["Plover"]["tip_args"]["as_of"] == "2026-09-03",
       "態①: as-of 取這台自己最後一次有資料的日")
    ck(by_fam["Kestrel"]["recent"] is False, "態②: 站查過、這個 family 零段 ⇒ False")
    ck(by_fam["Kestrel"]["css"] == "observed idle", "態②: css observed idle（空心）")
    ck(by_fam["Kestrel"]["tip_key"] == "tag_tip_idle", "態②: tag_tip_idle")
    ck(by_fam["Kestrel"]["tip_args"]["days"] == recent_days(), "態②: tooltip 帶窗天數")
    # 站沒被查過 ⇒ 維持實心＋「活動未知」（**不是**空心）
    unk = tag_chip(OBS, activity=({}, {"stations_seen": ["segSomethingElse"],
                                       "as_of": "2026-09-04"}))
    ck(all(c["recent"] is None for c in unk["chips"]), "態③: 站沒查過 ⇒ recent None")
    ck(all(c["css"] == "observed" for c in unk["chips"]), "態③: **維持實心**、不得空心")
    ck(all(c["tip_key"] == "tag_tip_unknown" for c in unk["chips"]), "態③: tag_tip_unknown")
    ck(all(c["tip_args"]["as_of"] == "2026-09-04" for c in unk["chips"]),
       "態③: as-of 退到窗內最後一天")
    ck(all(c["tip_args"]["as_of"] == AS_OF_UNKNOWN
           for c in tag_chip(OBS, activity=({}, {}))["chips"]),
       "態③: 完全沒有 as-of ⇒ 佔位符，tooltip 不留空洞")
    # 尚無 v4 檔（stations_seen 空）＝全體 unknown，**不是**全體 idle
    ck(all(c["tip_key"] == "tag_tip_unknown"
           for c in tag_chip(OBS, activity=({}, {"stations_seen": []}))["chips"]),
       "態③: 尚無 v4 檔 ⇒ unknown（不得讀成閒著）")
    # 正訊號永遠贏過「沒查過」——量到就是量到了
    hit = tag_chip(OBS, activity=({OBS: {"families": {"Plover": 5}}}, {}))
    ck({c["family"]: c["tip_key"] for c in hit["chips"]}["Plover"] == "tag_tip_recent",
       "態①優先: 有正訊號 ⇒ recent，即使站不在 stations_seen")
    # 跨站機：只查了一站 ⇒ 另一站仍可能在跑 ⇒ **unknown 不是 idle**（`all` 不是 `any`）
    half = tag_chip(MULTI, activity=({}, {"stations_seen": [MULTI_ST[0]]}))
    ck(all(c["css"] == "observed" for c in half["chips"]),
       "覆蓋: 跨站機只查一站 ⇒ unknown（覆蓋判定用 all）")
    full = tag_chip(MULTI, activity=({}, {"stations_seen": list(MULTI_ST)}))
    ck(all(c["css"] == "observed idle" for c in full["chips"]),
       "覆蓋: 兩站都查過、零段 ⇒ idle")
    ck(half["covered"] is False and full["covered"] is True, "覆蓋: covered 旗對得上")

    # ---- ③ default 台：恆 None＋空心＋default tooltip（★ 不做第三種形）---------------
    for act in (({DEF: {"families": {"Plover": 99}}}, {"stations_seen": ["segAuxElectricalTest"]}),
                ({}, {"stations_seen": ["segAuxElectricalTest"]}),
                ({}, {})):
        d = tag_chip(DEF, activity=act)
        ck(all(c["recent"] is None for c in d["chips"]), "default: recent 恆 None")
        ck(all(c["css"] == "default" for c in d["chips"]), "default: 恆空心 `default`")
        ck(all(c["tip_key"] == "tag_tip_default" for c in d["chips"]),
           "default: 恆 tag_tip_default（活動層不得回頭改標籤，C-PT1）")
    ck(tag_chip(OFF, activity=({}, {"stations_seen": ["segFlexBonding"]}))["origin"] == "default",
       "表外機: 仍走 default 那條路")

    # ---- ④ css／tip_key 是閉集：沒有第四態、也沒有 `.ptag.dashed` -----------------------
    seen_css, seen_tip = set(), set()
    for act in (None, ({}, {}), ({OBS: {"families": {"Plover": 1}}}, {"stations_seen": [OBS_ST]}),
                ({}, {"stations_seen": [OBS_ST]})):
        for eid in (OBS, MULTI, DEF, OFF):
            for c in tag_chip(eid, activity=act)["chips"]:
                seen_css.add(c["css"])
                seen_tip.add(c["tip_key"])
    ck(seen_css <= set(CSS_VALUES), f"閉集: css ⊆ {CSS_VALUES}（實得 {sorted(seen_css)}）")
    ck(seen_tip <= set(TIP_KEYS), f"閉集: tip_key ⊆ TIP_KEYS（實得 {sorted(seen_tip)}）")
    ck("dashed" not in " ".join(seen_css), "閉集: 沒有虛線框（Chris 裁：不做第三種形）")

    # ---- ⑤ 窗天數：預設 7、env 覆寫、壞值退預設 ----------------------------------------
    ck(RECENT_DAYS_DEFAULT == 7, "窗: 預設 7 天（Chris 2026-09-03）")
    old = os.environ.get(RECENT_DAYS_ENV)
    try:
        for raw, want in (("14", 14), ("", 7), ("abc", 7), ("0", 7), ("-3", 7), (" 5 ", 5)):
            os.environ[RECENT_DAYS_ENV] = raw
            ck(recent_days() == want, f"窗: env={raw!r} ⇒ {want}")
        os.environ.pop(RECENT_DAYS_ENV, None)
        ck(recent_days() == RECENT_DAYS_DEFAULT, "窗: env 未設 ⇒ 預設")
    finally:
        if old is None:
            os.environ.pop(RECENT_DAYS_ENV, None)
        else:
            os.environ[RECENT_DAYS_ENV] = old

    # ---- ⑥ 髒資料不炸（活動層是讀落地 JSON 來的，形不保證乾淨）-------------------------
    dirty = ({OBS: {"families": {"Plover": True, "Kestrel": "x"}}}, {"stations_seen": [OBS_ST]})
    dc = {c["family"]: c for c in tag_chip(OBS, activity=dirty)["chips"]}
    ck(dc["Plover"]["tip_key"] == "tag_tip_idle", "髒值: bool 不當段數（True ≠ 1 段）")
    ck(dc["Kestrel"]["tip_key"] == "tag_tip_idle", "髒值: 字串不當段數")
    ck(tag_chip(OBS, activity=({OBS: "not-a-dict"}, {"stations_seen": [OBS_ST]}))
       ["chips"][0]["tip_key"] == "tag_tip_idle", "髒值: 壞 entry 當零段")
    ck(_split_activity(None) == (None, {}) and _split_activity({"a": 1})[1] == {},
       "_split_activity: 三種形")

    # ---- ⑦ `?tag=` 一個位元不動（C-PT4／C-PT5）-----------------------------------------
    ck("activity" not in inspect.signature(tag_filter).parameters,
       "tag_filter: 沒有 activity 參數（過濾仍按能力）")
    ck(tag_filter("A", [OBS, DEF, MULTI])["matched"] == filter_eids([OBS, DEF, MULTI],
                                                                    ("Kestrel",)),
       "tag_filter: 結果仍等於 board_tags.filter_eids")
    # 能力字母不受活動層影響（活動層永不回頭改標籤）
    ck(tag_chip(OBS)["letters"]
       == tag_chip(OBS, activity=({}, {"stations_seen": [OBS_ST]}))["letters"],
       "能力層: letters 不因活動層而變")
    ck(tag_chip(OBS)["families"]
       == tag_chip(OBS, activity=act_run)["families"], "能力層: 730 天段數不因活動層而變")
    ck([c["family"] for c in tag_chip(DEF, activity=act_run)["chips"]] == list(FAMILIES),
       "順序: chips 恆 FAMILIES 序")

    # ---- ⑧ 產能瀑布的 tag 門檻（Chris 2026-09-05 裁 1；SPEC-CAPACITY §3.7）-------------
    ck(tag_ph_mode(100.0, 100.0)["mode"] == "full"
       and tag_ph_mode(100.0, 100.0)["excl_share"] == 0.0,
       "tag_ph_mode: 一台都沒濾掉 ⇒ full、share 0")
    ck(tag_ph_mode(100.0, 97.0, max_excl=0.05)["mode"] == "full"
       and tag_ph_mode(100.0, 97.0, max_excl=0.05)["pct"] == 3,
       "tag_ph_mode: 3% ≤ 5% ⇒ full、pct 3")
    ck(tag_ph_mode(100.0, 60.0, max_excl=0.05)["mode"] == "off"
       and tag_ph_mode(100.0, 60.0, max_excl=0.05)["pct"] == 40,
       "tag_ph_mode: 40% > 5% ⇒ off、pct 40")
    ck(tag_ph_mode(100.0, 95.0, max_excl=0.05)["mode"] == "full",
       "tag_ph_mode: 邊界含等號（share == 門檻 ⇒ full）")
    # ⚠ 分母為 0＝沒有東西可以被濾掉 ⇒ share 0（**不是**除以零、也不是退場）
    ck(tag_ph_mode(0.0, 0.0)["excl_share"] == 0.0 and tag_ph_mode(0.0, 0.0)["mode"] == "full",
       "tag_ph_mode: 全站生產機時 0 ⇒ share 0")
    ck(tag_ph_mode(100.0, 150.0)["excl_share"] == 0.0,
       "tag_ph_mode: 子集比全站大（不該發生）⇒ 夾成 0、不出負數")
    ck(tag_ph_mode(100.0, 60.0)["kept_pct"] == 60, "tag_ph_mode: kept_pct ＝ 100 − pct")
    _old_env = os.environ.get(CP_TAG_PH_MAX_EXCL_ENV)
    try:
        os.environ[CP_TAG_PH_MAX_EXCL_ENV] = "-1"
        ck(tag_ph_mode(100.0, 100.0)["mode"] == "off",
           "env: 門檻 −1 ⇒ 連 0% 都退場（測試用的強制開關真的有效）")
        os.environ[CP_TAG_PH_MAX_EXCL_ENV] = "not-a-number"
        ck(cp_tag_ph_max_excl() == CP_TAG_PH_MAX_EXCL_DEFAULT, "env: 壞值 ⇒ 回預設")
        os.environ[CP_TAG_PH_MAX_EXCL_ENV] = "7"
        ck(cp_tag_ph_max_excl() == CP_TAG_PH_MAX_EXCL_DEFAULT, "env: >1 的比例 ⇒ 回預設")
    finally:
        if _old_env is None:
            os.environ.pop(CP_TAG_PH_MAX_EXCL_ENV, None)
        else:
            os.environ[CP_TAG_PH_MAX_EXCL_ENV] = _old_env

    # ---- ⑨ Kestrel 專機例外 chip（Chris 2026-09-05 裁 3）--------------------------------
    ck(kestrel_only_eids([OBS, DEF, MULTI]) == tuple(
        e for e in (OBS, DEF, MULTI) if tags_of(e)[1] == "observed"
        and tuple(tags_of(e)[0]) == ("Kestrel",)),
       "kestrel_only_eids: 只收 observed 的 A-only")
    ck(all(tags_of(e)[1] == "observed" for e in kestrel_only_eids([DEF])) is True
       and kestrel_only_eids([DEF]) == (),
       "kestrel_only_eids: default 台（R+A+M）一台都不算＝不把「沒量到」說成「專機」")
    _ao = tuple(e for e in PRODUCT_TAGS
                if tags_of(e)[1] == "observed" and tuple(tags_of(e)[0]) == ("Kestrel",))
    ck(len(_ao) > 0 and kestrel_only_eids(_ao + _ao) == _ao,
       "kestrel_only_eids: 去重、保序")
    _c = aonly_chip(("EID-1", "EID-2"), 40.0, 100.0, min_share=0.10)
    ck(_c and _c["n"] == 2 and _c["pct"] == 40 and _c["eids_txt"] == "EID-1, EID-2",
       "aonly_chip: 達門檻 ⇒ 出 chip（n／pct／清單）")
    ck(aonly_chip(("EID-1",), 5.0, 100.0, min_share=0.10) is None,
       "aonly_chip: 低於門檻 ⇒ **整顆 None**（零常駐版位）")
    ck(aonly_chip((), 40.0, 100.0, min_share=0.10) is None, "aonly_chip: 沒有 A-only ⇒ None")
    ck(aonly_chip(("EID-1",), 40.0, 0.0, min_share=0.10) is None,
       "aonly_chip: 該跑沒跑總量 0 ⇒ None（不除以零、也不印 100%）")
    ck(aonly_chip(("EID-1",), 10.0, 100.0, min_share=0.10)["pct"] == 10,
       "aonly_chip: 邊界含等號（share == 門檻 ⇒ 出）")
    ck(aonly_chip(("EID-1",), 500.0, 100.0, min_share=0.10)["share"] == 1.0,
       "aonly_chip: 子集大於總量（不該發生）⇒ 夾成 100%，不出 500%")
    _old_min = os.environ.get(AONLY_CHIP_MIN_ENV)
    try:
        os.environ[AONLY_CHIP_MIN_ENV] = "0.9"
        ck(aonly_chip(("EID-1",), 40.0, 100.0) is None, "env: 門檻 0.9 ⇒ 40% 不渲染")
        ck(aonly_chip_min() == 0.9, "env: aonly_chip_min 讀得到")
    finally:
        if _old_min is None:
            os.environ.pop(AONLY_CHIP_MIN_ENV, None)
        else:
            os.environ[AONLY_CHIP_MIN_ENV] = _old_min

    return fails, n_checks[0]


def _run_selftest():
    fails, n = selftest()
    if fails:
        print("board_tagui selftest FAIL:")
        for f in fails:
            print("  - " + f)
        return 1
    print(f"board_tagui selftest OK ({n} checks)")
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        sys.exit(_run_selftest())
    print(__doc__)
