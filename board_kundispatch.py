#!/usr/bin/env python3
"""綑級調度核心，schema ``board.kun_dispatch.v1``。

裁決出處：``SPEC-DISPATCH-V2.md``。本檔只有可重放的判定與配對；不讀 MES、不寫狀態檔。
資料層保留綑代號，顯示層不得直接顯示它們（C-KD9）。
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import tempfile
from datetime import datetime, timedelta

from _probe_kun_activity import (KUN_ROSTER, REGION_SHIFTS, SHIFT_WINDOWS,
                                 _AREA2REGION, production_day)
from board_dispatch import _upstream_map, build_board
from board_manpower import PLOVER_BLOCKS


SCHEMA_ID = "board.kun_dispatch.v1"
BLIND_KUNS = ('ZC-6',)

# §15 的人工審定對照。第一欄保持規格的 PLOVER 列字面；16／17 是一個站 class 對兩列。
CLS2KUN = {'segSerialisation': ('1', 'ZA-1', 'machine'),
           'segFramePlasma': ('2', 'ZA-1', 'machine'),
           'segFrameBonding': ('3', 'ZA-1', 'machine'),
           'segStraightnessCheck': ('4', 'ZA-1', 'machine'),
           'segManualCheckA': ('5', 'ZA-1', 'manual'),
           'segPreLamPlasma': ('6', 'ZA-2', 'machine'),
           'segLamination': ('7', 'ZA-2', 'machine'),
           'segLamLeakTest': ('8', 'ZA-2', 'machine'),
           'segManualCheckB': ('9', 'ZA-2', 'manual'),
           'segDiePlacement': ('10', 'ZA-3', 'machine'),
           'segAttachCureOven': ('11', 'ZA-3', 'machine'),
           'segPostCureAlignCheck': ('12／13', 'ZA-3', 'machine'),
           'segManualCheckC': ('14', 'ZA-3', 'manual'),
           'segFlexBonding': ('15', 'ZB-1', 'machine'),
           'segBendTest': ('16', 'ZB-1', 'machine'),
           'segWireBonding': ('17', 'ZB-2', 'machine'),
           'segPreEncapPlasma': ('18', 'ZB-2', 'machine'),
           'segSealDispense': ('19', 'ZB-2', 'machine'),
           'segUVCure': ('20', 'ZB-2', 'machine'),
           'segPotting': ('21', 'ZB-3', 'machine'),
           'segThermalCure': ('22', 'ZB-3', 'machine'),
           'segModuleElectricalTest': ('23', 'ZB-3', 'machine'),
           'segPlasmaActivation': ('24', 'ZC-1', 'machine'),
           'segManifoldAttach': ('25', 'ZC-1', 'machine'),
           'segChassisAttach': ('26', 'ZC-1', 'machine'),
           'segPassThroughStep': ('—', 'passthrough', 'passthrough'),
           'segCalibrationTest': ('27', 'ZC-3', 'machine'),
           'segAutomatedOQC': ('28', 'ZC-4', 'machine'),
           'segFinalOQC': ('29', 'ZC-4', 'manual'),
           'segCleanTest': ('30', 'ZC-5', 'machine'),
           'segQASetup': ('31', 'ZC-7', 'machine'),
           'segVacuumPackInspect': ('32', 'ZC-7', 'machine'),
           'segManualPackC': ('33', 'ZC-7', 'manual'),
           'segBlindVisualStep': ('34', 'ZC-6', 'blind')}

# §15 的唯一例外：ZC-2／ZC-5 共用母機 27600；其子工位只計入 ZC-5 的分母。
_PARENT_OWNER = {'EID-54346': 'ZC-5'}
_KUN_ORDER = tuple(k for k, _a, _n, _e in KUN_ROSTER)
_ROSTER_BY_KUN = {k: (a, n, tuple(e)) for k, a, n, e in KUN_ROSTER}


def demo_operator_by_kun():
    """Landing demo 的綑級 observed 人數（純合成、固定、零 I/O）。

    數值故意不沿用名義編制；它只模擬 `board.operator.v1.by_kun` 的聚合後形，讓
    landing 可驗證 chip 0／正數與跨綑各自計數的呈現，而不把任何識別值帶進 demo。
    """
    kuns = tuple(dict.fromkeys(mapping[1] for mapping in CLS2KUN.values()
                               if mapping[2] != "passthrough"))
    return {kun: (i % 4) for i, kun in enumerate(kuns)}

# v1 已定義的列欄，這是 D1 消費時複製的白名單，而非自行重算站級 truth table。
_V1_ROW_KEYS = (
    "ord", "cls", "mcl", "display", "queue_n", "stale_n", "fail_hold_n",
    "n_4h", "n_7d", "rate_uph_4h", "rate_uph_7d", "rate_uph", "rate_src",
    "cover_h", "passthrough", "util_4h", "base_7d", "base_days_n", "util_note",
    "fleet_n", "down_n", "upstream_label", "rate_fallback_d", "lamp", "row",
    "reason_key", "reason_args", "util_low", "util_src", "mp",
)


def _env_float(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _now(value=None):
    if value is None:
        return datetime.now()
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    raise TypeError("now must be datetime, ISO string, or None")


def _iso(value):
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else None


def _row_tokens(value):
    """§15 的一格多列字面（16／17）轉成 PLOVER 原列號集合。"""
    return tuple(x for x in str(value).replace("/", "／").split("／") if x and x != "—")


def _flat_plover_rows():
    return tuple(row for _area, rows in PLOVER_BLOCKS for row in rows)


def _direct_eid_classes():
    """由唯一 PLOVER 機器可讀副本導出 (kun,eid)->class，不另抄名冊。"""
    out = {}
    plover = _flat_plover_rows()
    for cls, (row_no, kun, kind) in CLS2KUN.items():
        if kind != "machine":
            continue
        wanted = set(_row_tokens(row_no))
        for no, _en, _zh, row_kun, eids in plover:
            if row_kun != kun or str(no) not in wanted:
                continue
            for eid in eids:
                out.setdefault((kun, eid), []).append(cls)
    return {key: tuple(dict.fromkeys(value)) for key, value in out.items()}


_DIRECT_EID_CLASSES = _direct_eid_classes()


def _normalise_eid_classes(value):
    """把呼叫端注入的 ``eid→classes`` 或 ``cls→eids`` 正規化為前者。

    這是 runtime timeline 的機台事實入口，不讀檔、不改寫名冊；同一 EID 的 class
    保留輸入順序，讓跨綑服務的一台機可按所有站隊列判定工作。
    """
    out = {}
    if not isinstance(value, dict):
        return out
    for key, raw_values in value.items():
        values = (raw_values,) if isinstance(raw_values, str) else raw_values
        if isinstance(values, set):
            values = tuple(sorted(values))
        if not isinstance(key, str) or not isinstance(values, (list, tuple, set)):
            continue
        if key in CLS2KUN:  # cls → [eid, ...]
            for eid in values:
                if isinstance(eid, str) and eid:
                    out.setdefault(eid, []).append(key)
        else:  # eid → (cls, ...)
            for cls in values:
                if isinstance(cls, str) and cls:
                    out.setdefault(key, []).append(cls)
    return {eid: tuple(dict.fromkeys(classes)) for eid, classes in out.items()}


def _parent_children(layout_doc):
    out = {}
    for rec in (layout_doc or {}).get("parents") or ():
        parent = rec.get("parent_eid") if isinstance(rec, dict) else None
        children = rec.get("children") if isinstance(rec, dict) else None
        if parent and isinstance(children, (list, tuple)):
            out[str(parent)] = tuple(str(x) for x in children if x)
    return out


def _child_parent(parent_children):
    return {child: parent for parent, children in parent_children.items() for child in children}



def _parent_owners(parent_children):
    """母機的子工位應在哪一綑計分母；一般母機由唯一綑自然推出。"""
    seen = {}
    for kun, _area, _name, eids in KUN_ROSTER:
        for eid in eids:
            if eid in parent_children:
                seen.setdefault(eid, []).append(kun)
    owners = {parent: kuns[0] for parent, kuns in seen.items() if len(set(kuns)) == 1}
    owners.update({p: k for p, k in _PARENT_OWNER.items() if p in parent_children})
    return owners


def _effective_eids(kun, roster_eids, parent_children, *, for_distance=False):
    """母 EID 不計台；距離可同時看到共用母機的子工位，分母則遵 §15 owner。"""
    owners = _parent_owners(parent_children)
    child_to_parent = _child_parent(parent_children)
    out = []
    for eid in roster_eids:
        parent = child_to_parent.get(eid, eid)
        if parent in parent_children:
            if not for_distance and owners.get(parent) not in (None, kun):
                continue
            for child in parent_children[parent]:
                if child not in out:
                    out.append(child)
        elif eid not in out:
            out.append(eid)
    return tuple(out)


def _classes_for_eid(kun, eid, parent_children, eid_classes=None):
    """PLOVER 直接列 ∪ runtime EID 對照；子工位另沿用母機的兩種對照。"""
    runtime = _normalise_eid_classes(eid_classes)
    parent = _child_parent(parent_children).get(eid)
    ids = (eid, parent) if parent else (eid,)
    classes = []
    for current in ids:
        classes.extend(_DIRECT_EID_CLASSES.get((kun, current), ()))
        classes.extend(runtime.get(current, ()))
    return tuple(dict.fromkeys(classes))


def _station_context(wip):
    stations = list((wip or {}).get("stations") or ())
    by_cls = {s.get("cls"): s for s in stations if isinstance(s, dict) and s.get("cls")}
    prev_by_cls, prev = {}, None
    for s in sorted((x for x in stations if isinstance(x, dict)), key=lambda x: x.get("ord") or 0):
        cls = s.get("cls")
        if cls:
            prev_by_cls[cls] = prev
        if not s.get("passthrough") and cls:
            prev = cls
    # 呼叫既有 v1 純函式，確保所謂上游與 v1 的 pass-through 跳過規則一致。
    _upstream_map(stations)
    return by_cls, prev_by_cls


def _has_recent_throughput(records):
    """任一可測 class 的近 4h distinct lot 數 > 0 ⇒ 該機近 4h 有吞吐（正面訊號）。

    ``n_4h`` 缺值（``None``／非數）⇒ 不否決，交回原規則；這是「只用正面訊號、
    不把空窗當證據」的 A12 pin 相容寫法。
    """
    for rec in records:
        if not isinstance(rec, dict):
            continue
        n4 = rec.get("n_4h")
        if isinstance(n4, bool) or not isinstance(n4, (int, float)):
            continue
        if n4 > 0:
            return True
    return False


def machine_work_state(eid, wip, down_eids=(), dormant_fn=None, now=None, *, classes=None):
    """§5.1 一台機 → dormant/down/dry/unknown/work（無任何外部 I/O）。

    route 第一站沒有上游、或上游站不在 WIP 時一律回 ``work``；這是 C-KD3 的
    保守側，所以第一站機台永不判 ``dry``。

    ★ 2026-09-05 補：判 ``dry`` 之前多一道**正面訊號否決**——該機任一 class 的
    站 ``n_4h``（``board.wip.v1`` 既有欄，近 4h distinct lot 數）``> 0`` ⇒ 直接回
    ``work``。理由：``queue_n`` 只數**在站前等待**的 lot，**不含正在機上跑**
    （NULL 判定列）的 lot，所以「隊列 0」不等於「機沒在動」。2026-09-05 live 實例：
    CAL（``segCalibrationTest``）``queue_n=0``／``cover_h=0`` 但站級
    ``util_4h=45%``、``n_4h>0``，舊規則把 5/6 台判 ``dry``、綑燈落 ``na``；一旦
    配置人數登記進來就會**多報可借**，違反 C-KD3（寧可少報可借、絕不多報）的方向。
    ``n_4h is None`` ⇒ 不否決（沿用原規則，不拿空窗當證據）。
    ``kun_dry_at``／feed 預測不受此影響——料盡預測仍走 ``cover_h``。
    """
    now = _now(now)
    if dormant_fn is not None and dormant_fn(eid, now.date()):
        return "dormant"
    if eid in set(down_eids or ()):
        return "down"
    by_cls, prev_by_cls = _station_context(wip)
    classes = tuple(classes or ())
    if not classes:
        return "unknown"
    records = [by_cls.get(cls) for cls in classes]
    # 一台跨站服務時，任一可測 class 有隊列就已足以證明它有工作（同 08057）。
    if any(rec is not None and rec.get("queue_n") is not None and rec.get("queue_n") > 0
           for rec in records):
        return "work"
    if any(rec is None for rec in records):
        return "unknown"
    queues = [rec.get("queue_n") for rec in records]
    if any(q is None for q in queues):
        return "unknown"
    # EID-08057 兩站：任一有料即 work；其餘多站情況也用同一保守規則。
    if any(q > 0 for q in queues):
        return "work"
    # ★ 正面訊號否決：隊列雖空，但近 4h 有 lot 流過（含正在機上跑的 NULL 判定列）
    #   ⇒ 機在動，不得判 dry（見 docstring 的 CAL 實例與 C-KD3）。
    if _has_recent_throughput(records):
        return "work"
    upstream_queues = []
    for cls in classes:
        upstream = prev_by_cls.get(cls)
        up_rec = by_cls.get(upstream) if upstream else None
        if up_rec is None or up_rec.get("queue_n") is None:
            return "work"  # 未能正面證明兩腿皆空，依 C-KD3 保守視為有工作。
        upstream_queues.append(up_rec.get("queue_n"))
    return "dry" if upstream_queues and all(q == 0 for q in upstream_queues) else "work"


def _feed_hours(wip, classes):
    by_cls, prev_by_cls = _station_context(wip)
    feeds = {}
    for cls in classes:
        station = by_cls.get(cls)
        upstream = by_cls.get(prev_by_cls.get(cls)) if prev_by_cls.get(cls) else None
        cover = station.get("cover_h") if station else None
        up_cover = upstream.get("cover_h") if upstream else None
        feeds[cls] = None if cover is None or up_cover is None else float(cover) + float(up_cover)
    return feeds


def kun_dry_at(wip, classes, now=None):
    """§5.2 成員機器站的 feed_h → 最晚料盡；回傳同時保留可測 feed 材料。"""
    now = _now(now)
    feeds = _feed_hours(wip, tuple(classes or ()))
    measurable = [h for h in feeds.values() if h is not None]
    if not measurable:
        return {"dry_at": None, "dry_src": "none", "feed_h": feeds}
    return {"dry_at": _iso(now + timedelta(hours=max(measurable))),
            "dry_src": "cover", "feed_h": feeds}


def dry_now_running(row, as_of, *, tol_min=1.0):
    """§5.2 顯示層判斷（純函式；2026-10-02 SPEC-DISPATCH-V2 §13 ⑯(c)）：料盡預測＝**此刻**、
    但綑內機器站近 4h 仍有吞吐（§5.1 ``n_4h`` 否決生效＝機台在跑）⇒ ``True``。

    頁面此時印「佇列空、機台在跑」而不是一個等於現在的時刻——回測：``dry_at == now`` 1582 筆，
    多數綑燈是 ok，因為在機上跑的 lot 不進 ``queue_n``。**只認正面訊號**：成員**機器**站
    （``CLS2KUN`` 標 machine）任一 ``n_4h > 0``；``n_4h`` 缺值／route 第一站「無上游 ⇒ work」的
    保守規則**不算**「在跑」（那不是觀測）⇒ 回 ``False``、照舊印時刻。``kun_dry_at`` 一個位元不動。
    """
    row = row if isinstance(row, dict) else {}
    try:
        dry_at = _now(row.get("dry_at")) if row.get("dry_at") else None
        now = _now(as_of) if as_of else None
    except (TypeError, ValueError):
        return False
    if dry_at is None or now is None or dry_at - now > timedelta(minutes=float(tol_min)):
        return False
    for station in row.get("stations") or ():
        if not isinstance(station, dict):
            continue
        mapping = CLS2KUN.get(station.get("cls"))
        if not mapping or mapping[1] != row.get("kun") or mapping[2] != "machine":
            continue
        n4 = station.get("n_4h")
        if isinstance(n4, (int, float)) and not isinstance(n4, bool) and n4 > 0:
            return True
    return False


def _state_counts(states):
    return {state: sum(1 for value in states.values() if value == state)
            for state in ("dormant", "down", "dry", "unknown", "work")}


def lend_level_from_frac(staff_n, frac, all_idle=False, *, lend_frac, half_frac):
    """§5.3 可借數的**唯一公式**（純函式；`_wip_backtest` 的重放也 import 這一支，不抄規則）。

    回 ``(now, half)``；``staff_n is None`` ⇒ ``None``（不主張，C-KD8）。

    ★ 2026-10-02 小人數單調規則（`_WIP-BACKTEST-20261002.md` §④、`_FOLLOWUPS-20261002.md` ③-1；
      主持人裁、Chris 可否決；SPEC-DISPATCH-V2 §13 ⑭）：
      ① **N＝0（本班未配人）⇒ 永不主張借**——連 all_idle 也只給 0（燈照 down／need／na 判，
         可借 0）。舊公式在 0.25≤idle<0.5 會給「半人」＝建議從一個本班沒人的綑借半個人（回測 93 張）。
      ② **N≥1 單調**：idle<半人門檻 無；半人門檻≤idle<整人門檻 半人；idle≥整人門檻 整人
         ``min(N−1, floor(N·idle))``，**算出 0 則降為半人**——即 N=1 在 idle≥0.5 時仍是半人
         （C-KD4 至少留一人 ⇒ 只能借半個）。舊公式此處給 0 且不亮＝閒得越多越不提示（回測 125 張）。
      門檻值本身不動（env 0.5／0.25）；all_idle 不變（整綑 N 人）。
    """
    if staff_n is None:
        return None
    n = int(staff_n)
    if n <= 0:
        return 0, False
    if all_idle:
        return n, False
    if frac is None:
        return 0, False
    if frac >= lend_frac:
        now = min(n - 1, math.floor(n * frac))
        return (now, False) if now > 0 else (0, True)
    if frac >= half_frac:
        return 0, True
    return 0, False


def _lend_level(states, staff_n, manual_with_queue_n, *, lend_frac, half_frac):
    c = _state_counts(states)
    denom = len(states) - c["dormant"]
    if staff_n is None or denom <= 0:
        return {"now": 0, "half": False, "all_idle": False, "denom": denom}
    idle = c["down"] + c["dry"]
    all_idle = idle == denom and manual_with_queue_n == 0 and c["unknown"] == 0
    frac = idle / denom
    now, half = lend_level_from_frac(staff_n, frac, all_idle,
                                     lend_frac=lend_frac, half_frac=half_frac)
    return {"now": now, "half": half, "all_idle": all_idle, "denom": denom}


def lend_of(machine_states, staff_n, manual=None, *, horizon_h=None, feed_h_by_eid=None,
            lend_frac=None, half_frac=None, manual_heavy=False):
    """§5.3 可借數；輸入是 ``{eid: state}``，因此可單獨離線釘三個 Chris 例子。"""
    if staff_n is None:
        return None
    states = dict(machine_states or {})
    manual_with_queue_n = int((manual or {}).get("with_queue_n") or 0)
    lend_frac = _env_float("BOARD_KD_LEND_FRAC", 0.5) if lend_frac is None else float(lend_frac)
    half_frac = _env_float("BOARD_KD_HALF_FRAC", 0.25) if half_frac is None else float(half_frac)
    now_level = _lend_level(states, staff_n, manual_with_queue_n,
                            lend_frac=lend_frac, half_frac=half_frac)
    future = dict(states)
    if horizon_h is not None:
        for eid, state in tuple(future.items()):
            feed = (feed_h_by_eid or {}).get(eid)
            if state == "work" and feed is not None and feed <= horizon_h:
                future[eid] = "dry"
    horizon_level = _lend_level(future, staff_n, manual_with_queue_n,
                                lend_frac=lend_frac, half_frac=half_frac)
    if manual_heavy:
        # ZC-7 的人工工作量量不到，永不據此主張可借。
        return {"now": 0, "half": False, "at_horizon": 0,
                "reason": None}
    return {"now": now_level["now"], "half": now_level["half"],
            "at_horizon": horizon_level["now"],
            "reason": ("kd_r_lend_partial" if now_level["now"] > 0 else
                       "kd_r_lend_half" if now_level["half"] else None)}


def need_of(rows, kun=None, *, max_need=None):
    """§5.4：站級 need_help 只作綑級需支援的材料，絕不改 v1 truth table。"""
    max_need = _env_int("BOARD_KD_NEED_MAX", 2) if max_need is None else int(max_need)
    stations = []
    for row in rows or ():
        cls = row.get("cls") if isinstance(row, dict) else None
        mapping = CLS2KUN.get(cls)
        if not mapping or mapping[2] in ("passthrough", "blind"):
            continue
        if kun is not None and mapping[1] != kun:
            continue
        if row.get("lamp") == "need_help":
            stations.append(cls)
    return {"n": min(len(stations), max_need), "stations": stations,
            "reason": "kd_r_need" if stations else None}


def lamp_of(machines, manual, lend, need, *, wip_ready=True, blind=False, manual_heavy=False):
    """§5.5 的先中先贏。回 ``(lamp, reason_key)``。

    ★ 2026-10-02：原第 5 列「``lend.at_horizon > lend.now`` ⇒ ok＋預告 chip（``kd_r_lend_later``）」
      **撤掉**——回測班末前兌現 10／707（1.4%）。``lend.at_horizon`` 仍留在回傳形狀（回測與日後
      重評用），只是不再驅動任何燈或字（SPEC-DISPATCH-V2 §5.5、§13 ⑮）。
    """
    machines = machines or {}
    roster_n = machines.get("roster_n") or 0
    dormant_n = machines.get("dormant_n") or 0
    denom = roster_n - dormant_n
    unknown_n = machines.get("unknown_n") or 0
    work_n = machines.get("work_n") or 0
    down_n = machines.get("down_n") if machines.get("down_n") is not None else 0
    dry_n = machines.get("dry_n") or 0
    manual_q = (manual or {}).get("with_queue_n") or 0
    all_idle = (denom > 0 and down_n + dry_n == denom and manual_q == 0 and unknown_n == 0)
    if blind:
        return "na", "kd_r_blind"
    if denom <= 0:
        return "na", "kd_r_nofleet"
    if not wip_ready or (unknown_n > 0 and work_n == 0):
        return "na", "kd_r_manual_heavy" if manual_heavy else "kd_r_nodiag"
    if (need or {}).get("n", 0) > 0:
        return "need", "kd_r_need"
    if manual_heavy:
        return "ok", "kd_r_manual_heavy"
    if all_idle:
        return "down", "kd_r_all_idle"
    if lend is not None and ((lend.get("now") or 0) > 0 or lend.get("half")):
        return "lend", "kd_r_lend_partial" if lend.get("now") else "kd_r_lend_half"
    return "ok", "kd_r_ok"


# ---- §5.6 亮燈去抖（2026-10-02；SPEC-DISPATCH-V2 §5.6／§13 ⑰）---------------------------------
#: 「可借」家族的燈＝回測的「亮燈」口徑（`_wip_backtest.fires_actual`）＝`pair_kuns` 的 lender 燈。
LIT_LAMPS = ("lend", "down")
#: 被去抖壓住時的理由鍵（燈落 `ok`、頁面燈字印「確認中」，見 app `_kd_ctx`）。
LIT_HELD_REASON = "kd_r_lend_confirming"


def lit_min_snaps():
    """亮燈需連續幾張快照（＝幾次 live 重算）；env `BOARD_KD_LIT_MIN_SNAPS`，預設 2，≤1＝關閉去抖。"""
    return max(1, _env_int("BOARD_KD_LIT_MIN_SNAPS", 2))


def lit_gap_min():
    """相鄰兩次重算間隔超過此分鐘數 ⇒ 連續性斷（同 `_wip_backtest.GAP_MAX_MIN`）；env `BOARD_KD_LIT_GAP_MIN`。"""
    return _env_float("BOARD_KD_LIT_GAP_MIN", 90)


def debounce_lamp(lamp, reason, prev_streak, *, min_snaps=None):
    """§5.6 去抖（純函式）：回 ``(lamp, reason, streak, held)``。

    ``streak``＝本綑「可借家族燈」連續亮的快照數（含本張）；非 lend／down ⇒ 0（熄燈**立即**、不去抖）。
    ``streak < min_snaps`` ⇒ 壓成 ``ok``＋``kd_r_lend_confirming``（不進配對、不進 landing 行動清單）。
    方向＝**晚亮、不多亮**（C-KD3「少報可借、絕不多報」）；代價＝晚亮一個快照節奏（日約 20 分／夜約 60 分）。
    ``need``／``na`` 不去抖（需支援不是借人建議）。
    """
    min_snaps = lit_min_snaps() if min_snaps is None else max(1, int(min_snaps))
    if lamp not in LIT_LAMPS:
        return lamp, reason, 0, False
    try:
        streak = max(0, int(prev_streak or 0)) + 1
    except (TypeError, ValueError):
        streak = 1
    if streak >= min_snaps:
        return lamp, reason, streak, False
    return "ok", LIT_HELD_REASON, streak, True


def debounce_prev(hist, cap, *, gap_min=None):
    """§5.6 跨輪狀態機（純函式）：給「上一張快照」的 streak 表，回 ``(prev_streaks, advance)``。

    ``hist``＝呼叫端（app）保存的 ``{"cap", "streaks", "prev_streaks"}``；``cap``＝本次 WIP 的 ``cap``。
    - ``cap`` 解析不了 ⇒ ``(None, False)``（不知道是哪一張 ⇒ 全部當第一張、也不寫回）。
    - 無歷史 ⇒ ``(None, True)``（冷啟動：本張全當第一張＝亮燈晚一輪，保守側）。
    - **同一張**（同 ``cap`` 再組一次板，例：`load_kun_dispatch` TTL 到期而 WIP 快取仍是同一份）⇒
      回當初那一次用的 ``prev_streaks``、``advance=False``——同一份 WIP 組兩次**絕不算兩張**。
    - 較新的一張且間隔 ≤ ``gap_min`` ⇒ ``(hist.streaks, True)``；間隔過大 ⇒ ``(None, True)``（連續性斷）。
    - 較舊的一張（時鐘倒退／亂序）⇒ ``(None, False)``，不倒帶歷史。
    """
    try:
        cur = _now(cap) if cap else None
    except (TypeError, ValueError):
        cur = None
    if cur is None:
        return None, False
    if not isinstance(hist, dict) or not hist.get("cap"):
        return None, True
    try:
        last = _now(hist.get("cap"))
    except (TypeError, ValueError):
        return None, True
    if cur == last:
        return hist.get("prev_streaks"), False
    if cur < last:
        return None, False
    gap = lit_gap_min() if gap_min is None else float(gap_min)
    if (cur - last) > timedelta(minutes=gap):
        return None, True
    return hist.get("streaks"), True


def lit_streaks(board):
    """板 → ``{kun: lit_streak}``（給呼叫端存成下一輪的 ``lit_prev``）。"""
    return {row.get("kun"): int(row.get("lit_streak") or 0)
            for row in ((board or {}).get("kuns") or ()) if isinstance(row, dict) and row.get("kun")}


def horizon_of(now, region):
    """C-KD7：指定區的本班末；同時重疊時取較早班末，避免多報可借。"""
    now = _now(now)
    base_day = production_day(now)
    start = datetime.combine(base_day, datetime.min.time()) + timedelta(hours=8)
    minute = (now - start).total_seconds() / 60.0
    active = []
    for shift in REGION_SHIFTS.get(region, ()):
        win = SHIFT_WINDOWS.get(shift)
        if win and win[0] <= minute < win[1]:
            active.append((shift, win[1]))
    if active:
        shift, end_min = min(active, key=lambda x: x[1])
        end = start + timedelta(minutes=end_min)
        return {"kind": "shift_end", "shift": shift, "end": _iso(end),
                "hours_left": round(max(0.0, (end - now).total_seconds() / 3600.0), 3),
                "region": region}
    hours = _env_float("BOARD_KD_HORIZON_FALLBACK_H", 2)
    end = now + timedelta(hours=hours)
    return {"kind": "fallback", "shift": None, "end": _iso(end),
            "hours_left": round(hours, 3), "region": region}


def _layout_xy(layout_doc):
    """layout 正規化左上角還原為 DXF 世界座標再換公尺（§16 的存證計法）。"""
    doc = layout_doc or {}
    meta = doc.get("meta") or {}
    factor = 0.0254 if meta.get("dxf_insunits") == 1 else 0.001
    sx = float(meta.get("dxf_units_per_norm_x") or 1.0)
    sy = float(meta.get("dxf_units_per_norm_y") or 1.0)
    out = {}
    for record in list(doc.get("machines") or ()) + list(doc.get("extras") or ()):
        if not isinstance(record, dict) or not record.get("eid"):
            continue
        try:
            out[record["eid"]] = (float(record["x"]) * sx * factor,
                                  float(record["y"]) * sy * factor)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def layout_dist_matrix(layout_doc):
    """C-KD6／§16：所有綑對綑取最近機台；缺任一側座標則值為 None。"""
    parent_children = _parent_children(layout_doc)
    xy = _layout_xy(layout_doc)
    by_kun = {kun: _effective_eids(kun, eids, parent_children, for_distance=True)
              for kun, _area, _name, eids in KUN_ROSTER}
    matrix = {kun: {} for kun in _KUN_ORDER}
    for i, left in enumerate(_KUN_ORDER):
        for right in _KUN_ORDER[i + 1:]:
            candidates = [math.hypot(xy[a][0] - xy[b][0], xy[a][1] - xy[b][1])
                          for a in by_kun[left] for b in by_kun[right]
                          if a in xy and b in xy]
            value = round(min(candidates), 1) if candidates else None
            matrix[left][right] = value
            matrix[right][left] = value
        matrix[left][left] = 0.0
    return matrix


def _dist_of(dist, left, right):
    if not dist:
        return None
    if isinstance(dist, dict):
        nested = dist.get(left)
        if isinstance(nested, dict):
            return nested.get(right)
        return dist.get((left, right), dist.get((right, left)))
    return None


def _route_adjacent(left, right):
    """目前以 `KUN_ROSTER` 順序判 route 相鄰；ZC-2 夾在 ZC-1／ZC-3 是已知 v2.1 議題。"""
    try:
        return abs(_KUN_ORDER.index(left) - _KUN_ORDER.index(right)) == 1
    except ValueError:
        return False


def pair_kuns(kuns, dist, horizon=None):
    """§6 greedy 配對；同區主建議，跨區只進一筆 fallback。"""
    order = {kun: i for i, kun in enumerate(_KUN_ORDER)}
    lenders, borrowers = [], []
    for item in kuns or ():
        lamp = item.get("lamp")
        lend = item.get("lend") or {}
        capacity = float(lend.get("now") or 0)
        half = bool(lend.get("half")) and capacity == 0
        if lamp in ("lend", "down") and (capacity > 0 or half):
            lenders.append(dict(item, _remaining=capacity, _half=half))
        need = (item.get("need") or {}).get("n") or 0
        if lamp == "need" and need > 0:
            borrowers.append(dict(item, _remaining=float(need)))

    half_max = _env_float("BOARD_KD_HALF_MAX_M", 15)

    def candidates(cross):
        result = []
        for lender in lenders:
            for borrower in borrowers:
                is_cross = lender.get("region") != borrower.get("region")
                if is_cross != cross:
                    continue
                d = _dist_of(dist, lender.get("kun"), borrower.get("kun"))
                adjacent = _route_adjacent(lender.get("kun"), borrower.get("kun"))
                result.append((lender, borrower, d, adjacent))
        return sorted(result, key=lambda row: (
            row[0].get("region") != row[1].get("region"),
            float("inf") if row[2] is None else row[2],
            not row[3], row[0]["_remaining"] if row[0]["_remaining"] > 0 else 0.5,
            order.get(row[0].get("kun"), 999), order.get(row[1].get("kun"), 999),
        ))

    def make_pair(lender, borrower, d, adjacent, *, cross=False):
        if lender["_remaining"] > 0:
            # 整人可借不可被切成 0.5；半人只有 §5.3 明示的 half lender 才能主張。
            if borrower["_remaining"] < 1:
                return None
            n = min(lender["_remaining"], borrower["_remaining"])
            half = False
        else:
            if d is None or d > half_max or borrower["_remaining"] < 0.5:
                return None
            n, half = 0.5, True
        lender["_remaining"] -= n
        borrower["_remaining"] -= n
        if half:
            lender["_half"] = False
        reason = ("kd_p_cross" if cross else "kd_p_half" if half else
                  "kd_p_nodist" if d is None else "kd_p_route" if adjacent else "kd_p_nearest")
        return {"from": lender["kun"], "to": borrower["kun"], "n": int(n) if n.is_integer() else n,
                "half": half, "dist_m": d, "route_adjacent": adjacent,
                "cross_region": cross, "reason": reason}

    pairs = []
    for lender, borrower, d, adjacent in candidates(False):
        if borrower["_remaining"] <= 0 or (lender["_remaining"] <= 0 and not lender["_half"]):
            continue
        made = make_pair(lender, borrower, d, adjacent)
        if made:
            pairs.append(made)

    fallback = []
    # 只有同區配完仍有任一側剩餘，才看跨區，且最多一筆。
    if any(x["_remaining"] > 0 or x["_half"] for x in lenders) and any(x["_remaining"] > 0 for x in borrowers):
        for lender, borrower, d, adjacent in candidates(True):
            if borrower["_remaining"] <= 0 or (lender["_remaining"] <= 0 and not lender["_half"]):
                continue
            made = make_pair(lender, borrower, d, adjacent, cross=True)
            if made:
                fallback.append(made)
                break

    for rank, row in enumerate(pairs, 1):
        row["rank"] = rank
    for rank, row in enumerate(fallback, 1):
        row["rank"] = rank
    unmatched = {
        "lend": [x["kun"] for x in sorted(lenders, key=lambda x: order.get(x["kun"], 999))
                 if x["_remaining"] > 0 or x["_half"]],
        "need": [x["kun"] for x in sorted(borrowers, key=lambda x: order.get(x["kun"], 999))
                 if x["_remaining"] > 0],
    }
    return {"pairs": pairs, "fallback_pairs": fallback, "unmatched": unmatched}


def _plover_rows_for_kun(kun):
    return [str(no) for no, _en, _zh, row_kun, _eids in _flat_plover_rows() if row_kun == kun]


def _plover_display(cls):
    """class → PLOVER 英文站名（`CLS2KUN` 的列號 ＋ 綑 回查唯一機器可讀副本）。

       ⚠ **只給 demo 合成的顯示欄用**（live 的 `display` 一律由 v1 `build_board` 給）：
         demo 原本把 `cls` 原字塞進 `display` ⇒ 展開層印出 `segSerialisation`
         這種內部識別字。對不到列就誠實退回 `cls`，不猜名字；判定腿一個位元不吃它。"""
    mapping = CLS2KUN.get(cls)
    if not mapping:
        return cls
    wanted, kun = set(_row_tokens(mapping[0])), mapping[1]
    for no, en, zh, row_kun, _eids in _flat_plover_rows():
        if row_kun == kun and str(no) in wanted:
            return en or zh or cls
    return cls


def _first_station(kun):
    for _no, en, zh, row_kun, eids in _flat_plover_rows():
        if row_kun == kun and eids:
            return en or zh
    return _ROSTER_BY_KUN[kun][1]


def _normalise_down_eids(value):
    if isinstance(value, dict):
        found = set()
        for nested in value.values():
            if isinstance(nested, str):
                found.add(nested)
            elif isinstance(nested, (set, tuple, list)):
                found.update(str(x) for x in nested if isinstance(x, str))
        return found
    return set(value or ())


def _nominal_doc(nominal):
    """接受 loader 的 (doc,status)、或 D2 傳入的 envelope/raw doc，保持核心不做檔案 I/O。"""
    if isinstance(nominal, tuple) and len(nominal) == 2:
        return nominal[0], nominal[1], None
    if isinstance(nominal, dict) and "doc" in nominal:
        return nominal.get("doc"), nominal.get("status", "ok"), nominal.get("stamp")
    return nominal if isinstance(nominal, dict) else None, "ok" if nominal else "absent", None


def _staff_for(doc, status, kun, shift):
    if doc is None or status != "ok" or not shift:
        return None, status
    value = ((doc.get("by_kun") or {}).get(kun) or {}).get(shift)
    if isinstance(value, int) and not isinstance(value, bool):
        return value, "nominal:" + str(doc.get("as_of") or "unknown")
    return None, "absent"


def _measured_for(classes, opr):
    if not isinstance(opr, dict):
        return None, {cls: None for cls in classes}
    source = opr.get("stations") or {}
    by_station = {}
    values = []
    for cls in classes:
        cell = source.get(cls)
        value = cell.get("n_now") if isinstance(cell, dict) and not cell.get("auto") else None
        by_station[cls] = value if isinstance(value, int) and not isinstance(value, bool) else None
        if by_station[cls] is not None:
            values.append(by_station[cls])
    return (max(values) if values else None), by_station


def _manual_summary(wip, classes):
    by_cls, _prev = _station_context(wip)
    return {"stations_n": len(classes),
            "with_queue_n": sum(1 for cls in classes
                                if (by_cls.get(cls) or {}).get("queue_n") is not None
                                and (by_cls.get(cls) or {}).get("queue_n") > 0)}


def build_kun_board(wip, util_ctx, down_eids_by_cls_or_set, dormant_fn, nominal, layout_doc,
                    opr=None, now=None, mode="live", wo_gate=True, *, eid_classes=None,
                    lit_prev=None):
    """組裝 §4 ``board.kun_dispatch.v1``；``eid_classes`` 是呼叫端注入的 timeline 機台事實。

    ``lit_prev``＝上一張快照的 ``{kun: lit_streak}``（§5.6 去抖；呼叫端以 ``debounce_prev`` 取得）。
    ``None``＝沒有上一張（冷啟動／斷層）⇒ 本張每一盞 lend／down 都只算第一張、壓成「確認中」。
    """
    now = _now(now)
    wip = wip or {}
    v1 = build_board(wip, util_ctx=util_ctx, wo_gate=wo_gate, mode=mode, opr=opr)
    v1_rows = [{key: copy.deepcopy(row.get(key)) for key in _V1_ROW_KEYS}
               for row in v1.get("rows") or ()]
    rows_by_cls = {row.get("cls"): row for row in v1_rows if row.get("cls")}
    parent_children = _parent_children(layout_doc)
    normal_eid_classes = _normalise_eid_classes(eid_classes)
    down_set = _normalise_down_eids(down_eids_by_cls_or_set) if wo_gate else set()
    doc, nominal_status, nominal_stamp = _nominal_doc(nominal)
    horizons = {region: horizon_of(now, region) for region in sorted(set(_AREA2REGION.values()))}
    kuns = []
    wip_ready = bool(wip.get("stations"))
    min_snaps = lit_min_snaps()

    for kun, area, _roster_name, raw_eids in KUN_ROSTER:
        region = _AREA2REGION[area]
        horizon = horizons[region]
        eids = _effective_eids(kun, raw_eids, parent_children)
        states, classes_by_eid = {}, {}
        for eid in eids:
            classes = _classes_for_eid(kun, eid, parent_children, normal_eid_classes)
            classes_by_eid[eid] = classes
            states[eid] = machine_work_state(eid, wip, down_set, dormant_fn, now, classes=classes)
        c = _state_counts(states)
        roster_n = len(states)
        denom = roster_n - c["dormant"]
        machine_classes = tuple(dict.fromkeys(cls for values in classes_by_eid.values() for cls in values))
        dry_info = kun_dry_at(wip, machine_classes, now)
        feed_by_eid = {}
        for eid, classes in classes_by_eid.items():
            values = [dry_info["feed_h"].get(cls) for cls in classes]
            feed_by_eid[eid] = max(values) if values and all(v is not None for v in values) else None
        manual_classes = tuple(cls for cls, mapping in CLS2KUN.items()
                               if mapping[1] == kun and mapping[2] == "manual")
        manual = _manual_summary(wip, manual_classes)
        staff_n, staff_src = _staff_for(doc, nominal_status, kun, horizon.get("shift"))
        lend = lend_of(states, staff_n, manual, horizon_h=horizon.get("hours_left"),
                       feed_h_by_eid=feed_by_eid, manual_heavy=(kun == "ZC-7"))
        need = need_of(v1_rows, kun)
        machines = {"roster_n": roster_n, "dormant_n": c["dormant"],
                    "down_n": c["down"] if wo_gate else None, "dry_n": c["dry"],
                    "work_n": c["work"], "unknown_n": c["unknown"],
                    "down_eids": sorted(eid for eid, state in states.items() if state == "down"),
                    "dry_eids": sorted(eid for eid, state in states.items() if state == "dry")}
        idle = (c["down"] if wo_gate else 0) + c["dry"]
        idle_frac = (idle / denom) if denom > 0 else None
        lamp_raw, reason_raw = lamp_of(machines, manual, lend, need, wip_ready=wip_ready,
                                       manual_heavy=(kun == "ZC-7"))
        # §5.6 去抖：燈（與配對）吃去抖後的值；原判定留在 `lamp_raw`（WIP 快照也記它＝下一輪的連續性材料）。
        lamp, lamp_reason, lit_streak, lit_held = debounce_lamp(
            lamp_raw, reason_raw, (lit_prev or {}).get(kun, 0), min_snaps=min_snaps)
        measured_classes = tuple(dict.fromkeys(machine_classes + manual_classes))
        measured_ge, measured_by_station = _measured_for(measured_classes, opr)
        # ⚠ 展開層的成員**不是** `measured_classes`（2026-09-05 D2 審出的漏站）：後者的機器腿
        #   來自 EID 對照（`classes_by_eid`）⇒ 一個 7d 無活動、名冊裡又沒有 PLOVER 代表 EID 的
        #   機器站（實測 `segSerialisation`／`segVacuumPackInspect`）會
        #   **整列從展開層消失**——那不是「量不到」而是「連列都不在」，兩者在頁面上長得一樣，
        #   卻是完全不同的兩件事。展開層＝**該綑在 `CLS2KUN` 的全部成員站**（machine＋manual）
        #   之中 v1 有列的那些，依 `ord` 排序；量測腿（`measured_*`）的口徑一個位元不動。
        kun_classes = tuple(cls for cls, mapping in CLS2KUN.items() if mapping[1] == kun)
        station_rows = sorted(
            (copy.deepcopy(rows_by_cls[cls]) for cls in kun_classes if cls in rows_by_cls),
            key=lambda row: row.get("ord") or 0)
        kuns.append({
            "kun": kun, "region": region, "area": area, "first_station": _first_station(kun),
            "rows": _plover_rows_for_kun(kun), "staff_n": staff_n, "staff_src": staff_src,
            "machines": machines, "manual": manual, "idle_frac": idle_frac,
            "dry_at": dry_info["dry_at"], "dry_src": dry_info["dry_src"], "lend": lend,
            "need": need, "lamp": lamp, "lamp_reason": lamp_reason,
            # additive（2026-10-02 §5.6）：去抖前的燈、連續亮燈張數、是否被壓住。
            "lamp_raw": lamp_raw, "lit_streak": lit_streak, "lit_held": lit_held,
            "measured_ge": measured_ge, "measured_by_station": measured_by_station,
            "stations": station_rows,
        })

    pairing = pair_kuns(kuns, layout_dist_matrix(layout_doc), horizons)
    notes = []
    if not wip_ready:
        notes.append("wip_unavailable")
    if not wo_gate:
        notes.append("wo_gate_off")
    if nominal_status != "ok":
        notes.append("nominal_" + str(nominal_status))
    if not layout_doc:
        notes.append("layout_absent")
    return {
        "schema": SCHEMA_ID, "ok": True, "mode": mode, "as_of": wip.get("cap"),
        # §4 只有單一 horizon 槽；實際判定全數用 diag.horizons 的逐區值（見交接回報的契約缺口）。
        "horizon": horizons.get("ZAB") or next(iter(horizons.values())), "kuns": kuns,
        "pairs": pairing["pairs"], "fallback_pairs": pairing["fallback_pairs"],
        "unmatched": pairing["unmatched"],
        "diag": {"nominal_stamp": nominal_stamp, "layout_reviewed": bool((layout_doc or {}).get("reviewed")),
                 "wo_gate": bool(wo_gate), "opr": "ok" if isinstance(opr, dict) else "off",
                 "wip_elapsed_s": wip.get("elapsed_s"), "blind_kuns": list(BLIND_KUNS),
                 "notes": notes, "horizons": horizons,
                 "lit_debounce": {"min_snaps": min_snaps,
                                  "prev": "none" if lit_prev is None else "ok",
                                  "held": sorted(row["kun"] for row in kuns if row.get("lit_held"))}},
    }


def _demo_layout():
    """純合成座標：只服務 demo 的可重放 pairing 情境，不冒充 layout 真值。"""
    positions = {"ZA-1": 0, "ZA-2": 10, "ZA-3": 20, "ZB-1": 20,
                 "ZB-2": 30, "ZB-3": 40, "ZB-4": 30, "ZC-1": 20,
                 "ZC-2": 40, "ZC-3": 42, "ZC-4": 44, "ZC-5": 46, "ZC-7": 48}
    machines = []
    for kun, _area, _name, eids in KUN_ROSTER:
        for eid in eids:
            machines.append({"eid": eid, "x": positions[kun], "y": 0.0, "w": 0.0, "h": 0.0})
    return {"schema_id": "board.layout.v1", "reviewed": False,
            "meta": {"dxf_insunits": 4, "dxf_units_per_norm_x": 1000, "dxf_units_per_norm_y": 1000},
            "machines": machines, "extras": [], "parents": []}


def _demo_eid_classes():
    """合成 runtime timeline 對照：刻意涵蓋名冊兄弟機，不以 PLOVER 代表 EID 撐 demo。"""
    out = {}
    for cls, (_row, kun, kind) in CLS2KUN.items():
        if kind != "machine" or kun not in _ROSTER_BY_KUN:
            continue
        for eid in _ROSTER_BY_KUN[kun][2]:
            out.setdefault(eid, []).append(cls)
    return {eid: tuple(dict.fromkeys(classes)) for eid, classes in out.items()}


def demo_kun_dispatch(now=None, down=None):
    """§11 全合成 demo；固定時點與輸入，無隨機／無 I/O。`down`（選配）＝換掉劇本的停機名單（selftest 公開樹變體用；None＝劇本原樣）。"""
    args, kwargs = _demo_inputs(now, down=down)
    # §5.6 去抖的 demo 劇本（2026-10-02）：合成「上一張快照」＝每綑都已連續亮了門檻減一張
    #   （⇒ 本張若亮就確認、照亮），**唯獨 `_DEMO_CONFIRMING_KUN` 是第一次亮**（⇒ 「確認中」）。
    #   選 ZA-1 是因為它在原劇本本就落在 unmatched（沒有配對）⇒ 配對帶／備案一個位元不變
    #   （fixture 的 `unmatched.lend` 因此少了 ZA-1——它不再是「可借沒人要」，而是「還在確認」）。
    #   公開樹的合成名冊：demo 主燈劇本本來就對不到（selftest 該段 skipped），此處只是多一個 prev 鍵。
    prev = {kun: lit_min_snaps() - 1 for kun in _KUN_ORDER if kun != _DEMO_CONFIRMING_KUN}
    board = build_kun_board(*args, **kwargs, lit_prev=prev)
    board["diag"]["notes"].append("synthetic")
    return board


def _demo_inputs(now=None, down=None):
    """demo 的組板輸入（``(args, kwargs)``，不含 ``lit_prev``）；selftest 用它重組冷啟動板。
    `down`（選配）＝取代劇本停機名單的 EID 集合（None＝下方具名劇本原樣）。"""
    now = _now(now or datetime(2026, 9, 5, 14, 32))

    def station(ordn, cls, queue=3, cover=1.0):
        return {"ord": ordn, "cls": cls, "mcl": "demo", "display": _plover_display(cls),
                "queue_n": queue, "stale_n": 0, "fail_hold_n": 0, "n_4h": 4,
                "n_7d": 80, "rate_uph_4h": 4.0, "rate_uph_7d": 1.0,
                "rate_uph": 4.0, "rate_src": "4h", "cover_h": cover,
                "passthrough": False}

    stations = [
        station(1, "segSerialisation"), station(2, "segFramePlasma"),
        station(3, "segFrameBonding"), station(5, "segStraightnessCheck"),
        station(7, "segPreLamPlasma"), station(8, "segLamination"),
        station(11, "segLamLeakTest"), station(12, "segDiePlacement"),
        station(14, "segAttachCureOven"), station(16, "segPostCureAlignCheck"),
        station(18, "segFlexBonding", queue=20, cover=5), station(20, "segWireBonding"),
        station(21, "segAuxElectricalTest", cover=0.1),
        station(22, "segPreEncapPlasma", cover=0.1),
        station(23, "segPotting", queue=20, cover=5),
        station(24, "segSealDispense", cover=0.1), station(25, "segUVCure", cover=0.1),
        station(27, "segPlasmaActivation", queue=20, cover=5),
        station(28, "segManifoldAttach", queue=20, cover=5),
    ]
    util = {row["cls"]: {"util_4h": 0.30, "base_7d": 0.30, "fleet_n": 3, "down_n": 0}
            for row in stations}
    for cls in ("segFlexBonding", "segPotting", "segPlasmaActivation", "segManifoldAttach"):
        util[cls].update({"util_4h": 0.05, "base_7d": 0.30})
    wip = {"schema": "board.wip.v1", "ok": True, "cap": _iso(now), "stations": stations,
           "line": {}, "diag": {}, "elapsed_s": 0.0,
           "params": {"rate_fallback_d": 7}}
    staff = {kun: {"day": 2, "swing": 2, "night": 2} for kun in _KUN_ORDER if kun != "ZC-1"}
    nominal = {"schema": "board.kun_nominal_staff.v1", "as_of": "2026-09-05", "by_kun": staff}
    down = {"EID-25872", "EID-12724",  # ZA-1 partial lender
            "EID-32176", "EID-35684", "EID-00425",  # ZA-2 lender
            "EID-11428", "EID-25335",  # ZA-3 half
            # ★ 2026-09-20：`EID-38790`（Wire pull tester）入 ZB-2 綑 ⇒ 這個 demo 劇本要的
            #   「整綑停」必須連它一起停，否則 ZB-2 的主燈不再是 down、§11 那根會紅。
            "EID-25590", "EID-25098", "EID-25983", "EID-38790"} if down is None else set(down)  # ZB-2 all idle
    return ((wip, util, down,lambda _eid, _day: False, nominal, _demo_layout()),
            {"opr": None, "now": now, "mode": "demo", "wo_gate": True,
             "eid_classes": _demo_eid_classes()})


#: demo 的「確認中」綑（§5.6）；見 `demo_kun_dispatch` 註。
_DEMO_CONFIRMING_KUN = "ZA-1"

#: demo 停機劇本的「角色」形（R-P11 公開樹自測的合成變體用）：綑 → 停機佔名冊比例（取名冊序前 ceil(N×比例) 台）。
#   ＝具名劇本的結構：ZA-1／ZA-2 一半停（partial lender）、ZA-3 四分之一（half）、ZB-2 整綑停（all idle）。
#   私有樹以不計數 assert 核對「每綑停機台數＝本表公式」（劇本或名冊漂移即紅）。
_DEMO_DOWN_FRAC = (("ZA-1", 0.5), ("ZA-2", 0.5), ("ZA-3", 0.25), ("ZB-2", 1.0))


def _demo_down_by_role():
    """依 `_DEMO_DOWN_FRAC` 從（合成）名冊推出停機名單；綑不在名冊 ⇒ None。"""
    out = set()
    for kun, frac in _DEMO_DOWN_FRAC:
        if kun not in _ROSTER_BY_KUN:
            return None
        eids = _ROSTER_BY_KUN[kun][2]
        out |= set(eids[:math.ceil(len(eids) * frac)])
    return out


class _Checks:
    def __init__(self):
        self.n = 0

    def ok(self, condition, message):
        self.n += 1
        if not condition:
            raise AssertionError(message)


def _layout_table_check(check):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "layout_eid_xy.json")
    if not os.path.exists(path):
        print("layout_eid_xy.json 不在 worktree：§16 可選斷言 skip")
        return
    with open(path, "r", encoding="utf-8") as fh:
        matrix = layout_dist_matrix(json.load(fh))
    expected = {
        ("ZA-1", "ZA-2"): 4.7, ("ZA-1", "ZA-3"): 2.1, ("ZA-2", "ZA-3"): 6.7,
        ("ZB-1", "ZB-2"): 6.4, ("ZB-2", "ZB-3"): 9.1, ("ZB-3", "ZB-4"): 18.8,
        ("ZA-3", "ZB-4"): 3.8, ("ZC-1", "ZC-5"): 31.2, ("ZC-2", "ZC-5"): 0.0,
        ("ZC-5", "ZC-7"): 17.7,
    }
    for (left, right), value in expected.items():
        check.ok(matrix[left][right] is not None and abs(matrix[left][right] - value) <= 0.1,
                 f"§16 {left}/{right}={matrix[left][right]}（預期 {value}±0.1）")


def _ledger_eid_classes_check(check):
    """可選離線材料驗證：ledger fixture 缺席不影響 selftest。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_fixtures", "cls_eids_from_ledger_20260905.json")
    if not os.path.exists(path):
        print("cls_eids_from_ledger_20260905.json 不在 worktree：EID→class 可選斷言 skip")
        return
    with open(path, "r", encoding="utf-8") as fh:
        by_cls = (json.load(fh) or {}).get("by_cls") or {}
    normal = _normalise_eid_classes(by_cls)
    check.ok(all(cls in normal.get(eid, ())
                 for cls, eids in by_cls.items() for eid in eids if isinstance(eid, str)),
             "fixture：cls→EID 可正規化為完整 EID→classes")


def _selftest():
    t = _Checks()
    now = datetime(2026, 9, 5, 14, 32)
    # WP-10d（R-P11）：class 數是名冊常數的結構計數（私有樹釘 43；公開樹＝合成名冊的 class 表，只驗非空且含 pass-through）
    import core_paths as _wp
    _pub = _wp.is_public_tree()
    t.ok((len(CLS2KUN) > 0 and any(m[2] == "passthrough" for m in CLS2KUN.values())
          if _pub else (len(CLS2KUN) == 43 and CLS2KUN["segFinalAssembly"][2] == "passthrough")),
         "§15：43 class（含 pass-through）")
    t.ok(any(sum(1 for m in CLS2KUN.values() if m[1] == k) >= 2 for k in {m[1] for m in CLS2KUN.values()})
         if _pub else (CLS2KUN["segAuxElectricalTest"][1] == CLS2KUN["segModuleElectricalTest"][1] == "ZB-4"),
         "§15：08057 的兩站同綑")
    demo_opr = demo_operator_by_kun()
    t.ok(set(demo_opr) == {m[1] for m in CLS2KUN.values() if m[2] != "passthrough"}
         and all(isinstance(n, int) and n >= 0 for n in demo_opr.values()),
         "§18.8 demo by_kun：完整非 passthrough 綑鍵、僅固定聚合整數、零 I/O")

    # §5.1 五態，含上游正負釘與 08057 的兩站規則。
    w = {"stations": [{"ord": 1, "cls": "up", "queue_n": 0, "cover_h": 1},
                      {"ord": 2, "cls": "a", "queue_n": 0, "cover_h": 1},
                      {"ord": 3, "cls": "b", "queue_n": 3, "cover_h": 1}]}
    t.ok(machine_work_state("EID-X", w, dormant_fn=lambda _e, _d: True, now=now, classes=("a",)) == "dormant",
         "5.1 dormant 優先")
    t.ok(machine_work_state("EID-X", w, {"EID-X"}, now=now, classes=("a",)) == "down", "5.1 down")
    t.ok(machine_work_state("EID-X", w, now=now, classes=("a",)) == "dry", "5.1 本站與上游均零→dry")
    t.ok(machine_work_state("EID-X", w, now=now, classes=("b",)) == "work", "5.1 任一隊列→work")
    t.ok(machine_work_state("EID-X", {"stations": []}, now=now, classes=("a",)) == "unknown", "5.1 缺 WIP→unknown")
    t.ok(machine_work_state("EID-08057", w, now=now, classes=("a", "b")) == "work", "5.1 08057 任一站有料→work")
    t.ok(machine_work_state("EID-08057", {"stations": [{"ord": 1, "cls": "up", "queue_n": 0},
                                                     {"ord": 2, "cls": "a", "queue_n": 0},
                                                     {"ord": 3, "cls": "b", "queue_n": 0}]},
                            now=now, classes=("a", "b")) == "dry", "5.1 08057 兩站皆空→dry")
    # ★ 本組原本拿 `EID-25983` 當「名冊有、PLOVER 代表列沒有」的例子——
    #   當時發現 SALTMARSH 原表漏列 Wirebond，補進 `PLOVER_BLOCKS` 後，25983 自此
    #   **走得到直接列**，不再是這條路徑的例子。改用 `EID-24742`（ZC-3 CAL Gen2 母機：在
    #   `KUN_ROSTER`、不在任何 PLOVER 列，`board_manpower` §8 也是把它掛色帶尾），
    #   **被測的性質一字未改**；25983 的新行為另立一根正面釘（見下）。
    sibling_by_cls = {"segCalibrationTest": ["EID-24742"]}
    sibling_by_eid = {"EID-24742": ("segCalibrationTest",)}
    t.ok(_normalise_eid_classes(sibling_by_cls) == _normalise_eid_classes(sibling_by_eid),
         "HIGH-1：兩種 eid_classes 形同值")
    sibling_classes = _classes_for_eid("ZC-3", "EID-24742", {}, sibling_by_cls)
    t.ok(("ZC-3", "EID-24742") not in _DIRECT_EID_CLASSES
         and sibling_classes == ("segCalibrationTest",),
         "HIGH-1：名冊兄弟機由 eid_classes 補 class，不靠 PLOVER 代表列")
    t.ok(machine_work_state("EID-24742",
                            {"stations": [{"ord": 1, "cls": "segCalibrationTest",
                                           "queue_n": 2}]},
                            now=now, classes=sibling_classes) == "work",
         "HIGH-1：名冊兄弟機注入後不再 unknown")
    # ★ 2026-09-20 Chris：`/manpower` 20 列補 `EID-25983`（Wirebond#7；SALTMARSH 原表漏列）
    #   ⇒ 它**升級成直接列 EID**：零注入就有 class、零注入就判得出 work（原本恆 unknown）。
    #   ⚠ `EID-38790`（Wire pull tester）同綑但**刻意不進 PLOVER 列** ⇒ 它仍走注入那條路。
    # PLOVER 名冊為空（例：脫敏公開樹的作業指導書流程表被抽掉）⇒ 依賴 PLOVER 列的斷言標 skipped、不算失敗；有名冊 ⇒ 照舊全比對。
    if not _flat_plover_rows():
        print("board_kundispatch selftest: skipped: PLOVER_BLOCKS empty (25983 direct-row pin)")
    else:
        t.ok(_DIRECT_EID_CLASSES.get(("ZB-2", "EID-25983")) == ("segWireBonding",)
             and _classes_for_eid("ZB-2", "EID-25983", {}, None) == ("segWireBonding",)
             and machine_work_state("EID-25983",
                                    {"stations": [{"ord": 1, "cls": "segWireBonding", "queue_n": 2}]},
                                    now=now,
                                    classes=_classes_for_eid("ZB-2", "EID-25983", {}, None)) == "work",
             "★ 2026-09-20：25983 補進 PLOVER 20 列後走直接列，零注入即判得出 work")
    t.ok(("ZB-2", "EID-38790") not in _DIRECT_EID_CLASSES
         and _classes_for_eid("ZB-2", "EID-38790", {}, None) == (),
         "★ 2026-09-20：Wire pull tester 入綑但不進 PLOVER 列 ⇒ 沒有直接列 class")
    cross_classes = _classes_for_eid("ZA-3", "EID-09763", {},
                                     {"EID-09763": ("segStraightnessCheck",
                                                      "segPostCureAlignCheck")})
    t.ok(machine_work_state("EID-09763", {"stations": [
             {"ord": 1, "cls": "segStraightnessCheck", "queue_n": 0},
             {"ord": 2, "cls": "segPostCureAlignCheck", "queue_n": 1}]},
             now=now, classes=cross_classes) == "work",
         "HIGH-1：跨綑兩 class 任一有隊列即 work，綑歸屬不改")
    t.ok(_classes_for_eid("ZC-3", "EID-24742", {}, None) == () and
         machine_work_state("EID-24742", w, now=now, classes=()) == "unknown",
         "HIGH-1：未注入 eid_classes 維持既有 unknown 行為")
    t.ok(machine_work_state("EID-X", {"stations": [{"ord": 1, "cls": "first", "queue_n": 0}]},
                            now=now, classes=("first",)) == "work",
         "備註：route 第一站零隊列仍保守 work，不判 dry")

    # ★ 2026-09-05 CAL 形：queue_n 不含「正在機上跑」的 lot ⇒ n_4h>0 否決 dry。
    _NO_N4H = object()  # 哨兵：連 n_4h 欄都沒有（舊 fixture 形）

    def _cal_wip(n4):
        station = {"ord": 2, "cls": "cal", "queue_n": 0, "cover_h": 0.0}
        if n4 is not _NO_N4H:
            station["n_4h"] = n4
        return {"stations": [{"ord": 1, "cls": "up", "queue_n": 0, "cover_h": 0.0, "n_4h": 0},
                             station]}
    t.ok(machine_work_state("EID-X", _cal_wip(12), now=now, classes=("cal",)) == "work",
         "5.1 ★ CAL 形：本站與上游隊列 0 但近 4h 有吞吐（n_4h=12）→ work，不判 dry")
    t.ok(machine_work_state("EID-X", _cal_wip(0), now=now, classes=("cal",)) == "dry",
         "5.1 ★ 同形 n_4h=0（近 4h 零吞吐）→ 仍 dry")
    t.ok(machine_work_state("EID-X", _cal_wip(None), now=now, classes=("cal",)) == "dry" and
         machine_work_state("EID-X", _cal_wip(_NO_N4H), now=now, classes=("cal",)) == "dry",
         "5.1 ★ n_4h 缺值（None／無此欄）不否決，照舊 dry")
    t.ok(machine_work_state("EID-08057", {"stations": [
             {"ord": 1, "cls": "up", "queue_n": 0, "n_4h": 0},
             {"ord": 2, "cls": "a", "queue_n": 0, "n_4h": 0},
             {"ord": 3, "cls": "b", "queue_n": 0, "n_4h": 7}]},
             now=now, classes=("a", "b")) == "work",
         "5.1 ★ 08057 兩站皆空隊列，任一站 n_4h>0 → work")
    _ledger_eid_classes_check(t)

    dry = kun_dry_at(w, ("a", "b"), now)
    t.ok(dry["dry_src"] == "cover" and dry["dry_at"] == _iso(now + timedelta(hours=2)), "5.2 最晚 feed_h")
    t.ok(kun_dry_at({"stations": [{"ord": 1, "cls": "a", "cover_h": None}]}, ("a",), now)["dry_src"] == "none",
         "5.2 任一 cover None 不猜")

    # §5.3 Chris 三例與人工站反釘。
    states6 = {str(i): "down" if i < 3 else "work" for i in range(6)}
    t.ok(lend_of(states6, 2)["now"] == 1, "5.3 Chris：2人6台3 down→借1")
    t.ok(lend_of({str(i): "down" for i in range(6)}, 2)["now"] == 2, "5.3 Chris：全 idle→整綑2")
    t.ok(lend_of({str(i): "down" if i < 2 else "work" for i in range(6)}, 2)["half"],
         "5.3 Chris：2/6→半人")
    t.ok(lend_of({str(i): "down" for i in range(6)}, 2, {"with_queue_n": 1})["now"] == 1,
         "5.3 負釘：人工有隊列不得整綑借")
    t.ok(lend_of(states6, None) is None, "5.3 staff None→lend None")
    t.ok(lend_of({"d": "dormant", "a": "down", "b": "work"}, 2)["now"] == 1,
         "5.3 dormant 不進分母")
    t.ok(lamp_of({"roster_n": 2, "dormant_n": 0, "down_n": 0, "dry_n": 1,
                  "work_n": 0, "unknown_n": 1}, {}, {"now": 1, "half": False, "at_horizon": 1},
                 {"n": 0})[0] == "na", "5.3/5.5 unknown 不算 idle 且不得主張")

    needs = need_of([{"cls": "segDiePlacement", "lamp": "need_help"},
                     {"cls": "segFlexBonding", "lamp": "need_help"},
                     {"cls": "segWireBonding", "lamp": "need_help"}], max_need=2)
    t.ok(needs["n"] == 2 and len(needs["stations"]) == 3, "5.4 need 上限2")
    t.ok(need_of([{"cls": "segDiePlacement", "lamp": "ok"}])["n"] == 0, "5.4 非 need_help 不計")
    base_m = {"roster_n": 4, "dormant_n": 0, "down_n": 0, "dry_n": 0,
              "work_n": 4, "unknown_n": 0}
    t.ok(lamp_of(base_m, {}, None, {"n": 1})[0] == "need",
         "5.4 staff 缺席仍照判 need")

    t.ok(lamp_of(base_m, {}, {"now": 0, "half": False, "at_horizon": 0}, {"n": 0}, blind=True)[0] == "na", "5.5 #0 blind")
    t.ok(lamp_of(dict(base_m, work_n=0, unknown_n=1), {}, {"now": 0}, {"n": 0})[0] == "na", "5.5 #1 unknown")
    t.ok(lamp_of(base_m, {}, {"now": 1}, {"n": 1})[0] == "need", "5.5 #2 need 優先")
    t.ok(lamp_of(dict(base_m, dry_n=4, work_n=0), {}, {"now": 2}, {"n": 0})[0] == "down", "5.5 #3 all idle")
    t.ok(lamp_of(base_m, {}, {"now": 1, "half": False, "at_horizon": 1}, {"n": 0})[0] == "lend", "5.5 #4 lend")
    # ★ 2026-10-02 §13 ⑮：「預告」列撤掉——at_horizon > now 不再改燈或理由（回測兌現 1.4%）。
    t.ok(lamp_of(base_m, {}, {"now": 0, "half": False, "at_horizon": 1}, {"n": 0}) == ("ok", "kd_r_ok"),
         "5.5 ★ 預告列已撤：at_horizon>now 仍是 ok／kd_r_ok（不再出 kd_r_lend_later）")
    t.ok(lamp_of(base_m, {}, {"now": 0, "half": False, "at_horizon": 0}, {"n": 0}) == ("ok", "kd_r_ok"), "5.5 #6 ok")

    # ★ 2026-10-02 §13 ⑭ 小人數單調規則（門檻不動 0.5／0.25）。
    _lf = dict(lend_frac=0.5, half_frac=0.25)
    t.ok(lend_level_from_frac(0, 0.3, **_lf) == (0, False) and lend_level_from_frac(0, 0.6, **_lf) == (0, False)
         and lend_level_from_frac(0, 1.0, True, **_lf) == (0, False),
         "5.3 ★ N=0（本班未配人）永不主張借：0.25–0.5 不再給半人、≥0.5 與整綑閒也是 0")
    t.ok(lend_level_from_frac(1, 0.5, **_lf) == (0, True) and lend_level_from_frac(1, 0.75, **_lf) == (0, True),
         "5.3 ★ N=1 且 idle≥0.5（未整綑閒）⇒ 至少半人（舊公式給 0 不亮）")
    t.ok(lend_level_from_frac(1, 0.3, **_lf) == (0, True) and lend_level_from_frac(1, 0.2, **_lf) == (0, False)
         and lend_level_from_frac(1, 1.0, True, **_lf) == (1, False),
         "5.3 ★ N=1 其餘不變：0.25–0.5 半人、<0.25 無、整綑閒借 1")
    t.ok(lend_level_from_frac(2, 0.5, **_lf) == (1, False) and lend_level_from_frac(3, 2 / 3, **_lf) == (2, False)
         and lend_level_from_frac(2, 0.4, **_lf) == (0, True) and lend_level_from_frac(None, 0.9, **_lf) is None,
         "5.3 ★ N≥2 公式一位元不變；staff None ⇒ 不主張")
    _n1 = {str(i): "down" if i < 2 else "work" for i in range(3)}            # idle 2/3、N=1
    t.ok(lend_of(_n1, 1)["half"] is True and lend_of(_n1, 1)["now"] == 0
         and lamp_of(dict(base_m, roster_n=3, down_n=2, work_n=1), {}, lend_of(_n1, 1), {"n": 0})
         == ("lend", "kd_r_lend_half"),
         "5.3 ★ N=1、3 台 2 台掛單 ⇒ 燈 lend（半人）")
    _n0 = lend_of({str(i): "down" if i < 1 else "work" for i in range(3)}, 0)   # idle 1/3、N=0
    t.ok(_n0 is not None and _n0["now"] == 0 and _n0["half"] is False
         and lamp_of(dict(base_m, roster_n=3, down_n=1, work_n=2), {}, _n0, {"n": 0}) == ("ok", "kd_r_ok"),
         "5.3 ★ N=0、1/3 台掛單 ⇒ 燈 ok（舊公式亮半人）")
    _n0all = lend_of({str(i): "down" for i in range(3)}, 0)
    t.ok(_n0all["now"] == 0 and lamp_of(dict(base_m, roster_n=3, down_n=3, work_n=0), {}, _n0all,
                                        {"n": 0})[0] == "down",
         "5.3 ★ N=0 整綑閒：燈照 down（事實）、可借 0（配對不會選它）")

    # ★ 2026-10-02 §5.6 去抖：連續 ≥2 張快照才亮（lend／down）；熄燈立即；need／na 不去抖。
    t.ok(debounce_lamp("lend", "kd_r_lend_half", 0, min_snaps=2) == ("ok", LIT_HELD_REASON, 1, True)
         and debounce_lamp("lend", "kd_r_lend_half", 1, min_snaps=2) == ("lend", "kd_r_lend_half", 2, False),
         "5.6 第一張亮 ⇒ 確認中（ok）；第二張連續 ⇒ 照亮")
    t.ok(debounce_lamp("down", "kd_r_all_idle", None, min_snaps=2)[:2] == ("ok", LIT_HELD_REASON)
         and debounce_lamp("down", "kd_r_all_idle", 3, min_snaps=2)[:3] == ("down", "kd_r_all_idle", 4),
         "5.6 down（整綑無工作）同一個去抖")
    t.ok(debounce_lamp("need", "kd_r_need", 0, min_snaps=2) == ("need", "kd_r_need", 0, False)
         and debounce_lamp("na", "kd_r_nodiag", 5, min_snaps=2) == ("na", "kd_r_nodiag", 0, False)
         and debounce_lamp("ok", "kd_r_ok", 5, min_snaps=2) == ("ok", "kd_r_ok", 0, False),
         "5.6 need／na／ok 不去抖、streak 歸零（熄燈立即）")
    t.ok(debounce_lamp("lend", "kd_r_lend_partial", 0, min_snaps=1) == ("lend", "kd_r_lend_partial", 1, False),
         "5.6 min_snaps=1 ⇒ 去抖關閉")
    _c0, _c1 = "2026-09-05T14:32:00", "2026-09-05T14:52:00.500000"
    _h = {"cap": _c0, "streaks": {"ZA-1": 1}, "prev_streaks": {"ZA-1": 7}}
    t.ok(debounce_prev(None, _c0) == (None, True) and debounce_prev(_h, None) == (None, False)
         and debounce_prev(_h, "not-a-time") == (None, False),
         "5.6 狀態機：無歷史 ⇒ 冷啟動（全當第一張）；cap 不明 ⇒ 不寫回")
    t.ok(debounce_prev(_h, _c0) == ({"ZA-1": 7}, False),
         "5.6 狀態機：同一張 WIP 再組一次板 ⇒ 用當初的 prev、不前進（同一份不算兩張）")
    t.ok(debounce_prev(_h, _c1, gap_min=90) == ({"ZA-1": 1}, True)
         and debounce_prev(_h, "2026-09-05T16:05:00", gap_min=90) == (None, True)
         and debounce_prev(_h, "2026-09-05T14:00:00", gap_min=90) == (None, False),
         "5.6 狀態機：下一張（≤90 分）接續；斷層 >90 分重來；較舊的一張不倒帶")

    # ★ 2026-10-02 §13 ⑯(c)：dry_at==now 但機器站近 4h 有吞吐 ⇒「佇列空、機台在跑」（顯示層判斷）。
    _mc, (_mrow, _mkun, _mkind) = next((c, m) for c, m in CLS2KUN.items() if m[2] == "machine")
    _manual_c = next((c for c, m in CLS2KUN.items() if m[1] == _mkun and m[2] == "manual"), None)
    _r = {"kun": _mkun, "dry_at": "2026-09-05T14:32:00",
          "stations": [{"cls": _mc, "n_4h": 3, "queue_n": 0}]}
    t.ok(dry_now_running(_r, "2026-09-05T14:32:00.920000") is True
         and dry_now_running(_r, "2026-09-05T14:31:30") is True,
         "5.2 ★ dry_at≈now（±1 分，cap 帶微秒）且機器站 n_4h>0 ⇒ 機台在跑")
    t.ok(dry_now_running(dict(_r, dry_at="2026-09-05T15:32:00"), "2026-09-05T14:32:00") is False
         and dry_now_running(dict(_r, dry_at=None), "2026-09-05T14:32:00") is False
         and dry_now_running(_r, None) is False,
         "5.2 ★ 料盡在未來／無預測／無 as_of ⇒ 不改字")
    t.ok(dry_now_running(dict(_r, stations=[{"cls": _mc, "n_4h": 0}]), "2026-09-05T14:32:00") is False
         and dry_now_running(dict(_r, stations=[{"cls": _mc, "n_4h": None}]), "2026-09-05T14:32:00") is False
         and (_manual_c is None or dry_now_running(dict(_r, stations=[{"cls": _manual_c, "n_4h": 5}]),
                                                   "2026-09-05T14:32:00") is False)
         and dry_now_running(dict(_r, kun="__other__"), "2026-09-05T14:32:00") is False,
         "5.2 ★ 只認本綑機器站的正面吞吐：n_4h 0／缺值／人工站／別綑 ⇒ 不主張在跑")

    # C-KD7 的期望值一律由 SHIFT_WINDOWS／REGION_SHIFTS 現算（本段零班表時刻字面：班表被置換成合成值時期望值跟著走）。
    #   ref：某區、某「生產日 08:00 起算分鐘」→ (kind, 最早結束的班名, 該班末分鐘)。
    def _kd7_ref(region, minute):
        act = [(SHIFT_WINDOWS[s][1], s) for s in REGION_SHIFTS[region]
               if SHIFT_WINDOWS[s][0] <= minute < SHIFT_WINDOWS[s][1]]
        if not act:
            return "fallback", None, None
        end_min, shift = min(act)
        return "shift_end", shift, end_min

    def _kd7_minute(at):                                   # 生產日 08:00 為起點（同 horizon_of 的座標定義）
        pd = production_day(at)
        return (at - (datetime.combine(pd, datetime.min.time()) + timedelta(hours=8))).total_seconds() / 60.0

    day = horizon_of(now, "ZAB")
    _k, _s, _e = _kd7_ref("ZAB", _kd7_minute(now))
    t.ok(_k == "shift_end" and day["kind"] == "shift_end" and day["shift"] == _s
         and day["end"] == (datetime.combine(production_day(now), datetime.min.time())
                            + timedelta(hours=8, minutes=_e)).isoformat(timespec="seconds"),
         "C-KD7 ZAB 班末（14:32 落在某一班窗內、取最早班末）")
    _at6 = datetime(2026, 9, 5, 6, 0)
    t.ok(horizon_of(_at6, "ZC")["kind"] == _kd7_ref("ZC", _kd7_minute(_at6))[0], "C-KD7 班窗外 fallback")
    _at18 = datetime(2026, 9, 5, 18, 0)
    t.ok(horizon_of(_at18, "ZAB")["shift"] == _kd7_ref("ZAB", _kd7_minute(_at18))[1] and
         horizon_of(_at18, "ZC")["kind"] == _kd7_ref("ZC", _kd7_minute(_at18))[0], "C-KD7 per-region")

    # WP-10d（R-P11）：版面距離／共用母機的具名機台由名冊推導（私有樹＝ZA-1／ZA-2 的 EID-25872／32176／12724、CLN EID-16077／27600；
    #   公開樹＝合成綑名冊的前兩綑與合成母機）。期望值（1.3 m、只算 ZC-5）是形，不是真值。
    import board_scope as _wsc
    if _wp.is_public_tree():
        _ka, _kb = _KUN_ORDER[0], _KUN_ORDER[1]
        _a1, _a2, _b1 = _ROSTER_BY_KUN[_ka][2][0], _ROSTER_BY_KUN[_ka][2][1], _ROSTER_BY_KUN[_kb][2][0]
        _lp = list(_wsc.PARENT_EXPANSION)[1]
        _lc = _wsc.PARENT_EXPANSION[_lp][0]
        _sp = next(iter(_PARENT_OWNER))
        _own = _PARENT_OWNER[_sp]
        _oth = next(k for k in _KUN_ORDER if k != _own and _sp in _ROSTER_BY_KUN[k][2])
        _skids = tuple(_wsc.PARENT_EXPANSION[_sp])
    else:
        _ka, _kb = "ZA-1", "ZA-2"
        _a1, _a2, _b1, _lp, _lc = "EID-25872", "EID-12724", "EID-32176", "EID-16077", "EID-14732"
        _sp, _own, _oth = "EID-27600", "ZC-5", "ZC-2"
        _skids = ("EID-27639", "EID-27661", "EID-27706", "EID-27789")
    synthetic = {"meta": {"dxf_insunits": 1, "dxf_units_per_norm_x": 100, "dxf_units_per_norm_y": 100},
                 "machines": [{"eid": _a1, "x": 0, "y": 0}, {"eid": _b1, "x": 1, "y": 0}],
                 "extras": [{"eid": _a2, "x": 0.5, "y": 0}],
                 "parents": [{"parent_eid": _lp, "children": [_lc]}]}
    lm = layout_dist_matrix(synthetic)
    t.ok(abs(lm[_ka][_kb] - 1.3) < 0.01, "C-KD6 inches→m、machines+extras")
    t.ok(lm["ZC-2"]["ZC-5"] is None, "C-KD6 缺座標→None")
    shared = {_sp: _skids}
    wl2 = _effective_eids(_oth, _ROSTER_BY_KUN[_oth][2], shared)
    wl5 = _effective_eids(_own, _ROSTER_BY_KUN[_own][2], shared)
    t.ok(not wl2 and set(shared[_sp]).issubset(wl5), "§15 共用母機子工位只算 ZC-5")
    _layout_table_check(t)

    # §6：同區、距離、route、half 上限、跨區只備案、兩側 unmatched。
    mini = [
        {"kun": "ZA-1", "region": "ZAB", "lamp": "lend", "lend": {"now": 1, "half": False}, "need": {"n": 0}},
        {"kun": "ZA-2", "region": "ZAB", "lamp": "lend", "lend": {"now": 0, "half": True}, "need": {"n": 0}},
        {"kun": "ZA-3", "region": "ZAB", "lamp": "need", "lend": None, "need": {"n": 1}},
        {"kun": "ZB-2", "region": "ZAB", "lamp": "lend", "lend": {"now": 1, "half": False}, "need": {"n": 0}},
        {"kun": "ZC-1", "region": "ZC", "lamp": "need", "lend": None, "need": {"n": 2}},
    ]
    dm = {"ZA-1": {"ZA-3": 8.0, "ZC-1": 30.0}, "ZA-2": {"ZA-3": 16.0, "ZC-1": 10.0},
          "ZB-2": {"ZA-3": 100.0, "ZC-1": 20.0},
          "ZA-3": {"ZA-1": 8.0, "ZA-2": 16.0, "ZB-2": 100.0},
          "ZC-1": {"ZA-1": 30.0, "ZA-2": 10.0, "ZB-2": 20.0}}
    paired = pair_kuns(mini, dm)
    t.ok(paired["pairs"][0]["from"] == "ZA-1" and paired["pairs"][0]["to"] == "ZA-3", "6 同區距離先")
    t.ok(not any(p["half"] for p in paired["pairs"]), "6 半人超過15m不得配")
    t.ok(len(paired["fallback_pairs"]) == 1 and paired["fallback_pairs"][0]["cross_region"], "6 跨區僅fallback一筆")
    t.ok("ZB-2" in paired["unmatched"]["lend"] and "ZC-1" in paired["unmatched"]["need"], "6 unmatched 兩側列出")

    # demo_kun_dispatch 的「停機名單」是私有名冊的具名 EID（公開樹的合成綑名冊對不到）⇒ 公開樹改用同一劇本的角色形
    #   （`_demo_down_by_role`：每綑停機比例同具名劇本、取合成名冊序前幾台＝合成 fixture 變體），主燈劇本斷言照跑（R-P11）。
    _demo_down = None
    if _pub:
        _demo_down = _demo_down_by_role()
    else:          # 角色表＝具名劇本的結構（不計入斷言數）
        _lit = _demo_inputs(now)[0][2]
        assert all(sum(1 for e in _ROSTER_BY_KUN[k][2] if e in _lit) == math.ceil(len(_ROSTER_BY_KUN[k][2]) * f)
                   for k, f in _DEMO_DOWN_FRAC), "_DEMO_DOWN_FRAC 與具名 demo 停機劇本不符"
    if not _pub or _demo_down is not None:
        board = demo_kun_dispatch(now, down=_demo_down)
        lamps = {row["lamp"] for row in board["kuns"]}
        t.ok(board["schema"] == SCHEMA_ID and board["mode"] == "demo", "§4 canonical schema/mode")
        t.ok({"lend", "need", "down", "ok", "na"}.issubset(lamps), "§11 demo 各主燈")
        t.ok(any(row.get("lend", {}).get("half") for row in board["kuns"] if row.get("lend")), "§11 demo half")
        t.ok(any(pair["half"] and pair["route_adjacent"] for pair in board["pairs"]), "§11 demo half 相鄰配對")
        t.ok(len(board["fallback_pairs"]) <= 1 and board["diag"]["opr"] == "off", "§4 pairing/opr diag")
        t.ok(next(row for row in board["kuns"] if row["kun"] == "ZC-1")["staff_n"] is None, "§11 配置未登記")
        t.ok(next(row for row in board["kuns"] if row["kun"] == "ZC-7")["lamp"] == "na", "§11 ZC-7 人工重 na")
        # ★ 2026-10-02 §5.6 demo 劇本：ZA-1 第一次亮 ⇒ 確認中、不進配對也不進 unmatched；其餘照亮。
        _c = next(row for row in board["kuns"] if row["kun"] == _DEMO_CONFIRMING_KUN)
        t.ok(_c["lamp"] == "ok" and _c["lamp_reason"] == LIT_HELD_REASON and _c["lamp_raw"] == "lend"
             and _c["lit_held"] is True and _c["lit_streak"] == 1
             and _DEMO_CONFIRMING_KUN not in board["unmatched"]["lend"]
             and all(_DEMO_CONFIRMING_KUN not in (p["from"], p["to"])
                     for p in board["pairs"] + board["fallback_pairs"])
             and board["diag"]["lit_debounce"]["held"] == [_DEMO_CONFIRMING_KUN],
             "§11 ★ demo 確認中：raw lend 壓成 ok、零配對、unmatched 不列")
        t.ok(all(row["lamp"] == row["lamp_raw"] for row in board["kuns"] if row["kun"] != _DEMO_CONFIRMING_KUN),
             "§11 ★ demo 其餘綑去抖後與原判定同燈（劇本只動一綑）")
        _da, _dk = _demo_inputs(now, down=_demo_down)
        _cold =build_kun_board(*_da, **_dk, lit_prev=None)
        t.ok(not any(row["lamp"] in LIT_LAMPS for row in _cold["kuns"])
             and not _cold["pairs"] and not _cold["fallback_pairs"]
             and _cold["diag"]["lit_debounce"]["prev"] == "none"
             and {row["kun"] for row in _cold["kuns"] if row["lit_held"]}
             == {row["kun"] for row in board["kuns"] if row["lamp_raw"] in LIT_LAMPS},
             "5.6 ★ 冷啟動（lit_prev=None）：每盞 lend／down 都壓成確認中、零配對（晚亮不多亮）")

    else:
        print("board_kundispatch selftest: skipped: demo_kun_dispatch lamp scenario is bound to private roster EIDs (synthetic roster lacks a scenario kun)")

    # ★ 展開層漏站（2026-09-05 D2 審）：**未注入 `eid_classes`** 時機器腿全空，
    #   但展開層仍必須列出該綑在 `CLS2KUN` 的全部成員站（machine＋manual）——正釘 ZA-1 的
    #   第 1 列 `segSerialisation`（7d 無活動、名冊無代表 EID ⇒ 舊碼整列消失）。
    # WP-10d（R-P11）：ZA-1 的成員站由 CLS2KUN 推導（私有樹恰為原先寫死的五站、同序；公開樹＝合成站表的 ZA-1 五站）
    bare_cls_list = tuple(c for c, m in CLS2KUN.items() if m[1] == "ZA-1")
    _bare_manual = next(c for c in bare_cls_list if CLS2KUN[c][2] == "manual")
    assert _pub or bare_cls_list == ("segSerialisation", "segFramePlasma",
                                     "segFrameBonding", "segBondedFrameVisualInspection",
                                     "segStraightnessCheck")       # 私有樹的原五站（不計入斷言數）
    bare_stations = [{"ord": n, "cls": cls, "mcl": "demo", "display": _plover_display(cls),
                      "queue_n": 1, "stale_n": 0, "fail_hold_n": 0, "n_4h": 4, "n_7d": 80,
                      "rate_uph_4h": 4.0, "rate_uph_7d": 1.0, "rate_uph": 4.0,
                      "rate_src": "4h", "cover_h": 1.0, "passthrough": False}
                     for n, cls in enumerate(bare_cls_list, start=1)]
    bare_wip = {"schema": "board.wip.v1", "ok": True, "cap": _iso(now), "stations": bare_stations,
                "line": {}, "diag": {}, "elapsed_s": 0.0, "params": {"rate_fallback_d": 7}}
    bare_util = {row["cls"]: {"util_4h": 0.30, "base_7d": 0.30, "fleet_n": 1, "down_n": 0}
                 for row in bare_stations}
    bare = build_kun_board(bare_wip, bare_util, set(), lambda _e, _d: False, None, None,
                           opr=None, now=now, mode="demo", wo_gate=True)
    bare_za1 = next(row for row in bare["kuns"] if row["kun"] == "ZA-1")
    bare_cls = [row.get("cls") for row in bare_za1["stations"]]
    t.ok(bare_cls_list[0] in bare_cls,
         "展開層：未注入 eid_classes 時 ZA-1 仍列得出 segSerialisation")
    t.ok(_bare_manual in bare_cls,
         "展開層：人工站同列（成員＝CLS2KUN 全部成員，不只機器腿）")
    t.ok(all(CLS2KUN.get(cls, ("", ""))[1] == "ZA-1" for cls in bare_cls),
         "展開層：只列本綑成員站，不摻別綑")
    t.ok(bare_cls == [row.get("cls") for row in
                      sorted(bare_za1["stations"], key=lambda row: row.get("ord") or 0)],
         "展開層：依 v1 ord 排序")
    if not _flat_plover_rows():
        print("board_kundispatch selftest: skipped: PLOVER_BLOCKS empty (PLOVER display-name pin)")
    t.ok((not _flat_plover_rows() or _plover_display("segSerialisation").startswith("Module"))
         and _plover_display("segNotAStation") == "segNotAStation",
         "demo 顯示名：PLOVER 英文站名；對不到誠實退回 cls")
    t.ok(not _flat_plover_rows() or not any(str(row.get("display") or "").startswith("seg")
                                            for kun_row in board["kuns"] for row in kun_row["stations"]),
         "§11 demo：展開層印站名、不印 class 內部字")

    fallback_board = demo_kun_dispatch(datetime(2026, 9, 5, 6, 0))
    t.ok(fallback_board["diag"]["horizons"]["ZAB"]["kind"] == _kd7_ref("ZAB", _kd7_minute(datetime(2026, 9, 5, 6, 0)))[0],
         "§11 fallback demo")
    fixture_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures", "board.kun_dispatch.v1.json")
    if not os.path.exists(fixture_path):
        # 私有 golden 不存在（例：脫敏公開樹）⇒ 標 skipped、不算失敗；存在 ⇒ 照舊全比對。
        print("board_kundispatch selftest: skipped: board.kun_dispatch.v1.json absent")
    else:
        with open(fixture_path, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
        t.ok(fixture.get("schema") == SCHEMA_ID and fixture.get("mode") == "demo" and
             fixture.get("pairs") == board["pairs"] and fixture.get("fallback_pairs") == board["fallback_pairs"] and
             fixture.get("unmatched") == board["unmatched"], "fixture：demo 配對投影與核心同形同值")

    # loader 只在 selftest 用暫存檔，驗格式、round/diag、small_night alias、stale。
    from board_data import load_kun_nominal_staff
    with tempfile.TemporaryDirectory(prefix="board_kd_nominal_") as td:
        path = os.path.join(td, "_KUN-NOMINAL-STAFF.json")
        doc = {"schema": "board.kun_nominal_staff.v1", "as_of": "2026-09-05", "by_kun": {"ZA-1": {"small_night": 1.5}}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        loaded, status = load_kun_nominal_staff(path=path, today=datetime(2026, 9, 6).date())
        t.ok(status == "ok" and loaded["by_kun"]["ZA-1"]["night"] == 2 and loaded.get("_diag"), "§14 alias/四捨五入 diag")
        _old, stale = load_kun_nominal_staff(path=path, today=datetime(2027, 1, 1).date())
        t.ok(stale == "stale", "§14 超60日 stale")
    print(f"board_kundispatch selftest: {t.n} 斷言 PASS")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Tendline Board 綑級調度核心（純離線）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true", help="印固定 demo canonical JSON")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    if args.json:
        print(json.dumps(demo_kun_dispatch(), ensure_ascii=False, indent=2))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
