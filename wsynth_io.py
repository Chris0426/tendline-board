# -*- coding: utf-8 -*-
"""wsynth_io.py — 合成產線的 MES 讀取面（SPEC-SYNTHLINE WP-3；**只在合成進程被 import**）
==========================================================================================

一句話：把 `wsynth_world` 產生的合成世界，以**與被取代的真 fetcher 逐形相同**的回傳，交給
`board_data`／`board_nest` 的 fetcher 入口（呼叫端依**進程旗標**分派，裁決 Q-A／Q13）。
之後的 `_clip`、零工期偵測、`analyze`、`timeline_records`、`aggregate`、`fleet_scoreboard`、
`aggregate_nest_seats`……**全部照原碼走**——本檔只回原子資料（C-SY8）。

依據：SPEC-SYNTHLINE §2.1、C-SY1／C-SY3／C-SY4／C-SY7／C-SY8／C-SY10；`_SYNTHLINE-RULINGS.md`
Q3、Q13、Q-A、Q-B、Q-E、Q-H、Q-I；`_SYNTHLINE-WP0-CALLSITES.md` §0／§1／§5／§6、R-6／R-8／R-12。

被取代的真 fetcher 與本檔的同形函式（形狀＝清冊 §1.1）
------------------------------------------------------
============================================ ==========================================
真函式（不動）                                本檔
============================================ ==========================================
`SELECT GETDATE()`（MES 伺服器時鐘＝窗錨）    `now()`＝`wsynth_clock.now()` 取整秒
`mes_timeline.fetch_fleet(cur, days, cls)`    `fetch_fleet(days, cls, now=None)`
`mes_timeline.fetch_segments(cur, eids, …)`   `fetch_segments(eids, days, cls, now=None)`
`mes_timeline.scoreboard_all_stations(...)`   `scoreboard_all_stations(days, stop_min, stations, now)`
`board_data.fetch_segments_desig(...)`      `fetch_segments_desig(eids, days, cls, now, desig_available)`
`board_data.fetch_segments_desig_guard`     `fetch_segments_desig_guard()`（恆 True）
`board_nest.fetch_nest_seat_rows(...)`      `fetch_nest_seat_rows(cls, var, start, end, now=None)`
`board_nest.fetch_cotag_rows(...)`          `fetch_cotag_rows(eid, cls, start, end, now=None)`
============================================ ==========================================

語意（每一條都對著凍結 SQL 的 WHERE 寫；凍結模組一個位元不動，C-SY10）
------------------------------------------------------------------
* **窗**：`start ∈ [now − days, now]`，且段已收尾（`end` 非空、`end >= start`）。
* **曝光（R-6／Q-H）**：段在 `end <= now` 才存在——凍結 SQL 只回已收尾的段、以起點篩窗；
  合成世界裡「結束時刻在未來」的段＝MES 上的開放段＝不回。
* **站別折疊**：逐 `mes_timeline.classes_for(cls)` 各掃一次再串接（同凍結碼）。
* **機台級 fleet**：候選中「其父也是候選」者排除（＝`dependent-equipment export` 模組排除）；父子邊取自
  合成世界的資產定義（`parent_asset_id`：形③ 父機與其座、形② 母機與其 co-tag 子機）。
  之後 `fold_eid`、以折疊後 EID 去重保留第一筆、依 EID 排序（同凍結碼）。
* **形③（Q-I）**：父機與其座都有段；父機的段＝座的段逐段 co-tag 的聯集（合成世界的約定，不是對
  真實 MES 的主張）。因此 fleet 只列父機、座被模組排除擋掉；座的段由 `fetch_segments(座 EID)`
  另取（`expand_machines_band`／`_gen2_module_aggs_live` 的既有路徑）。
* **座級列**：`fetch_nest_seat_rows` 以區間重疊形篩窗（`end >= start AND start < end`，同
  `build_seat_sql`），只回持有該 `var` 的機台（名冊常數 `board_capacity.NEST_EAV_UNITS`）；
  `fetch_cotag_rows` 逐子 EID。回傳欄序同真函式（六欄／五欄）。
* **時間**：一律台北牆鐘 naive `datetime`（秒精度）。

快取（「過去的日子不重算」）
--------------------------
世界以**日**為單位逐日產生（`wsynth_world.generate_day`，只依前一日結轉）。第一次需要某一天時才
產，產過的日子永不重算；時鐘往前走到新的一天，只追加那一天。一天的段全數落在該日之內
（產生器在午夜截斷），所以「今天」也只產一次，曝光由查詢時的 `now` 決定。

第一期不涵蓋的 fetcher（Q-E；良率、Qlik、WIP、人力、M12 工具族、產品族、重工）
------------------------------------------------------------------------
由 `board_data` 在合成進程下回各自既有的「未提供」形（`qlik_unavailable`、`wip_unavailable`……
reason＝`NOT_PROVIDED`），沒有既有降級形的（例：良率計分板）則拋 `SynthNotProvided`——與 live
連線失敗走同一條呼叫端降級路徑。**永不回 demo 資料、永不讀 fixture。**

零真資料（C-SY1）
-----------------
本檔不開任何檔案；唯一輸入＝`wsynth_world`（seed、版號、名冊常數、`wsynth_params`）與時鐘。
selftest 以 `builtins.open`／`io.open`／`os.open` 攔截做負斷言，並掃描本檔原始碼不含站名、機台名、
編號字面值。

自測：`python wsynth_io.py --selftest [-v]`（所有測試跑在子進程；合成旗標、凍結時鐘、狀態目錄
改向都只在子進程內設；寫檔只寫系統暫存目錄）。
"""
import datetime as _dt
import sys
import threading
from collections import namedtuple

import core_source as _wsrc

if __name__ != "__main__" and not _wsrc.is_synth_process():
    raise ImportError("wsynth_io is synthetic-process only (%s must be %r before first use)"
                      % (_wsrc.ENV_SOURCE, _wsrc.SOURCE_SYNTH))

import mes_timeline as _mt          # 凍結模組：只用純函式與常數（fold_eid／classes_for／analyze／fleet_scoreboard）
import wsynth_clock as _clk
import wsynth_params as _P

#: 「第一期未提供」的 reason 字串（各 `*_unavailable(reason, …)` 與 `families_status` 用）。
NOT_PROVIDED = "synth_not_provided"

_DAY = _dt.timedelta(days=1)

#: 快取內的一段（台北 naive）。
Seg = namedtuple("Seg", "start end eid unit cls designation result stp_seq")


class SynthNotProvided(RuntimeError):
    """第一期合成產線未提供的 MES 讀取（Q-E）。呼叫端把它當「該源不可用」走既有降級路徑。"""


def not_provided(what):
    """→ `SynthNotProvided` 例外實例（呼叫端 `raise`）。訊息帶 `NOT_PROVIDED` 以利日誌辨識。"""
    return SynthNotProvided("%s: %s (synthetic line phase 1)" % (NOT_PROVIDED, what))


# =============================================================================
# 時鐘（C-SY4）
# =============================================================================
def now():
    """合成進程的「MES 伺服器時鐘」＝`wsynth_clock.now()` 取整秒（台北 naive）。
    正式運行恆等於 `datetime.now()`；測試可經 `BOARD_SYNTH_NOW` 凍結。"""
    return _clk.now().replace(microsecond=0)


def _anchor(t):
    return now() if t is None else t


# =============================================================================
# 世界快取（逐日、前綴穩定）
# =============================================================================
def _seg_of(d):
    p = _dt.datetime.fromisoformat
    return Seg(p(d["start"]), p(d["end"]), d["eid"], d["unit"], d["cls"],
               d["designation"], d["result"], d["stp_seq"])


class _World:
    """一個 seed 的合成世界：逐日產生、產過的日子不重算。"""

    def __init__(self, seed, ctx_kw):
        import wsynth_world as _W
        self._W = _W
        self.ctx = _W.make_context(seed, **ctx_kw)
        self.start_day = self.ctx.horizon_start
        self.next_day = self.start_day
        self.carry = _W.initial_carry(self.ctx)
        self.days = {}                  # date -> {cls: tuple[Seg]}（依 start, end, stp_seq 排序）
        self.n_generated = 0
        self.desc = {a["asset_id"]: a["display_name"] for a in self.ctx.fleet}
        # 模組排除的父子邊（＝dependent-equipment export 的合成對應）：child -> parent
        self.parent_of = {a["asset_id"]: a["parent_asset_id"]
                          for a in self.ctx.fleet if a.get("parent_asset_id")}
        self.lock = threading.Lock()

    def ensure(self, until_day):
        """把世界產到 `until_day`（含）。已產的日子不動。"""
        if self.next_day > until_day:
            return
        with self.lock:
            while self.next_day <= until_day:
                _evs, segs, self.carry = self._W.generate_day(self.ctx, self.next_day, self.carry)
                by_cls = {}
                for d in segs:
                    s = _seg_of(d)
                    by_cls.setdefault(s.cls, []).append(s)
                self.days[self.next_day] = {c: tuple(sorted(v, key=lambda s: (s.start, s.end, s.stp_seq,
                                                                              s.eid, s.unit or "")))
                                            for c, v in by_cls.items()}
                self.next_day += _DAY
                self.n_generated += 1

    def iter_day_range(self, first_day, last_day):
        d = max(first_day, self.start_day)
        while d <= last_day:
            yield d, self.days.get(d) or {}
            d += _DAY


_WORLD = None
_WORLD_LOCK = threading.Lock()
_WORLD_CFG = {"seed": _P.DEFAULT_SEED, "ctx_kw": {}}


def configure(seed=None, **ctx_kw):
    """**只給測試用**：換 seed／context 參數並清掉快取。正式運行不呼叫（預設 seed＝`wsynth_params`）。"""
    global _WORLD
    with _WORLD_LOCK:
        _WORLD_CFG["seed"] = _P.DEFAULT_SEED if seed is None else seed
        _WORLD_CFG["ctx_kw"] = dict(ctx_kw)
        _WORLD = None


def reset_cache():
    """**只給測試用**：丟掉快取（下一次查詢從 horizon 起重產）。"""
    global _WORLD
    with _WORLD_LOCK:
        _WORLD = None


def world(t=None):
    """取世界（必要時追加產到 `t` 的那一天）。"""
    global _WORLD
    w = _WORLD
    if w is None:
        with _WORLD_LOCK:
            if _WORLD is None:
                _WORLD = _World(_WORLD_CFG["seed"], _WORLD_CFG["ctx_kw"])
            w = _WORLD
    w.ensure(_anchor(t).date())
    return w


def cache_info():
    """快取狀態（測試與日誌用）。"""
    w = _WORLD
    if w is None:
        return {"built": False}
    return {"built": True, "world_id": w.ctx.world_id, "n_generated": w.n_generated,
            "first_day": w.start_day.isoformat(),
            "last_day": (w.next_day - _DAY).isoformat() if w.n_generated else None}


def history_start():
    """合成世界可用歷史的第一天（`date`）＝世界的 horizon 起點（Q20：上線日往前 `HISTORY_DAYS` 天）。
    不觸發產生：世界已建就讀它的 `start_day`，否則讀設定（測試 `configure(horizon_start=…)` 或
    `wsynth_params.HORIZON_START`）。WP-4b R-B2（Q-M）「展示世界不足 180 天」標示的唯一出處。"""
    w = _WORLD
    if w is not None:
        return w.start_day
    return _WORLD_CFG["ctx_kw"].get("horizon_start", _P.HORIZON_START)


def world_meta():
    """合成揭露用的世界識別（world_id、版號、seed）。"""
    w = world()
    return {"world_id": w.ctx.world_id, "generator_version": w.ctx.version, "seed": w.ctx.seed}


# =============================================================================
# 原子查詢（凍結 SQL 的 WHERE 語意）
# =============================================================================
def _closed_rows(cls_list, lo, t, w):
    """`cls ∈ cls_list`、`lo <= start <= t`、已收尾（`end <= t`、`end >= start`）的段。
    逐站依給定順序串接（同凍結碼逐別名各送一次再串接）。"""
    out = []
    for c in cls_list:
        for _d, by_cls in w.iter_day_range(lo.date(), t.date()):
            for s in by_cls.get(c, ()):
                if lo <= s.start <= t and s.start <= s.end <= t:
                    out.append(s)
    return out


def _window(days, t):
    return t - _dt.timedelta(days=days)


def fetch_fleet(days, cls=_mt.CAL_CLASS, now=None):
    """同形於 `mes_timeline.fetch_fleet`：`list[(eid, desc)]`，機台級（模組排除後）、折疊、依 EID 排序。"""
    t = _anchor(now)
    w = world(t)
    rows = _closed_rows(_mt.classes_for(cls), _window(days, t), t, w)
    cand = {s.eid for s in rows}
    if not cand:
        return []
    modules = {e for e in cand if w.parent_of.get(e) in cand}
    folded = {}
    for eid in sorted(cand - modules):
        e = _mt.fold_eid(eid)
        if e is not None and e not in folded:
            folded[e] = w.desc.get(eid, eid)
    return sorted(folded.items(), key=lambda x: x[0])


def fetch_segments(eids, days, cls=_mt.CAL_CLASS, now=None):
    """同形於 `mes_timeline.fetch_segments`：`{eid: [(start, end), …]}`（只回 `eids` 內者、折疊後比對）。"""
    if not eids:
        return {}
    eidset = set(eids)
    t = _anchor(now)
    w = world(t)
    out = {}
    for s in _closed_rows(_mt.classes_for(cls), _window(days, t), t, w):
        e = _mt.fold_eid(s.eid)
        if e is not None and e in eidset:
            out.setdefault(e, []).append((s.start, s.end))
    return out


def fetch_segments_desig_guard():
    """同形於 `board_data.fetch_segments_desig_guard`：合成世界恆有 designation 欄。"""
    return True


def fetch_segments_desig(eids, days, cls=_mt.CAL_CLASS, now=None, desig_available=True):
    """同形於 `board_data.fetch_segments_desig`：`{eid: [(start, end, designation|None), …]}`。"""
    if not eids:
        return {}
    eidset = set(eids)
    t = _anchor(now)
    w = world(t)
    out = {}
    for s in _closed_rows(_mt.classes_for(cls), _window(days, t), t, w):
        e = _mt.fold_eid(s.eid)
        if e is not None and e in eidset:
            out.setdefault(e, []).append((s.start, s.end, s.designation if desig_available else None))
    return out


def scoreboard_all_stations(days=4, stop_min=_mt.STOP_MIN_DEFAULT, stations=None, now=None):
    """同形於 `mes_timeline.scoreboard_all_stations`：`(now, board, skipped)`。

    ★ 本函式**不做任何算術**：逐站呼叫本檔的 `fetch_fleet`／`fetch_segments`，再交給凍結純函式
      `analyze`／`fleet_scoreboard`（與凍結迴圈逐行同義；清冊 §6 #1）。selftest 釘它與「同一批段
      經 `board_data.station_rollup_from_data(get_live(...))`」逐欄相等（C-SY8 交叉釘）。"""
    stations = _mt.EQP_STATIONS if stations is None else stations
    thr = stop_min * 60
    t = _anchor(now)
    board, skipped = [], []
    for cls in stations:
        try:
            fleet = fetch_fleet(days, cls, now=t)
            segs = fetch_segments([e for e, _ in fleet], days, cls, now=t)
            results = {eid: _mt.analyze(segs.get(eid, []), t, thr) for eid, _ in fleet}
            board.append(_mt.fleet_scoreboard(cls, fleet, results))
        except Exception as e:                      # 同凍結迴圈：單站失敗記 skipped、續跑
            skipped.append({"cls": cls, "reason": "%s: %s" % (type(e).__name__, str(e)[:200])})
    return t, board, skipped


def _overlap_rows(cls, start, end, t, w, keep):
    """區間重疊形（`seg.end >= start AND seg.start < end`）＋已收尾（`seg.end <= t`）；`cls` 精確比對
    （同 `build_seat_sql`／`build_cotag_sql`：不展開別名站）。"""
    out = []
    hi = min(end, t)
    for _d, by_cls in w.iter_day_range(start.date() - _DAY, hi.date()):
        for s in by_cls.get(cls, ()):
            if s.end >= start and s.start < end and s.start <= s.end <= t and keep(s):
                out.append(s)
    return out


def _nest_int(unit):
    head, sep, tail = str(unit or "").partition(":")
    if head != "nest" or not sep or not tail.isdigit():
        return None
    return int(tail)


def fetch_nest_seat_rows(cls, var, start, end, now=None):
    """同形於 `board_nest.fetch_nest_seat_rows`：`[(eid, start, end, desig, result, nest:int), …]`。
    只回持有 `var` 的機台（`board_capacity.NEST_EAV_UNITS` 的 `var` 欄；名冊常數，只讀）的座級段。"""
    import board_capacity as _wcap
    holders = {e for e, rec in _wcap.NEST_EAV_UNITS.items() if rec.get("var") == var}
    t = _anchor(now)
    w = world(t)
    rows = _overlap_rows(cls, start, end, t, w,
                         lambda s: s.eid in holders and _nest_int(s.unit) is not None)
    return [(s.eid, s.start, s.end, s.designation, s.result, _nest_int(s.unit)) for s in rows]


def fetch_cotag_rows(eid, cls, start, end, now=None):
    """同形於 `board_nest.fetch_cotag_rows`：`[(eid, start, end, desig, result), …]`（五欄）。"""
    t = _anchor(now)
    w = world(t)
    rows = _overlap_rows(cls, start, end, t, w, lambda s: s.eid == eid)
    return [(s.eid, s.start, s.end, s.designation, s.result) for s in rows]


def closed_rows(cls, days, now=None):
    """逐 class 點查形的原子段（`wsynth_tick` 的 P1 生產日曆、P4 last_production 用；WP-6）。

    語意＝`core_lastprod_push.build_lastprod_sql`／`build_nest_sql` 的 WHERE：`SegmentClassID = ?`
    （**精確比對、不展開別名站**）、`start ∈ [now − days, now]`、已收尾（`end <= now`、`end >= start`）。
    → `list[Seg]`（依 start 排序）。只回原子段、不聚合（C-SY8）；窗語意與 `fetch_segments` 同一支
    `_closed_rows`（不另寫第二份）。"""
    t = _anchor(now)
    w = world(t)
    return _closed_rows((cls,), _window(days, t), t, w)


def nest_number(unit):
    """段的 `unit`（`nest:<n>`）→ 座號 int；不是巢座形 ⇒ None（`fetch_nest_seat_rows` 同一條規則）。"""
    return _nest_int(unit)


def exposed_segments(now=None):
    """測試／交叉驗用：到 `now` 為止已曝光（`end <= now`）的全部段，依 (end, stp_seq, eid, unit) 排序
    （＝`wsynth_world.build_world(...)["segments"]` 的排序）。"""
    t = _anchor(now)
    w = world(t)
    out = []
    for _d, by_cls in w.iter_day_range(w.start_day, t.date()):
        for segs in by_cls.values():
            out.extend(s for s in segs if s.end <= t)
    out.sort(key=lambda s: (s.end, s.stp_seq, s.eid, s.unit or ""))
    return out


__all__ = [
    "NOT_PROVIDED", "Seg", "SynthNotProvided", "not_provided", "now", "configure", "reset_cache",
    "world", "cache_info", "world_meta", "fetch_fleet", "fetch_segments", "fetch_segments_desig_guard",
    "fetch_segments_desig", "scoreboard_all_stations", "fetch_nest_seat_rows", "fetch_cotag_rows",
    "exposed_segments", "closed_rows", "nest_number",
]


# ============================================================================ selftest ===
#   以下只給 `--selftest`。上面的轉接層不依賴本節任何東西（原始碼掃描只掃上半部）。
#   架構：父進程（非合成）做原始碼掃描、再起三個子進程——
#     synth   ：合成旗標＋凍結時鐘＋狀態目錄改向到系統暫存目錄；跑絆線後 import 本模組，跑 G1–G9。
#     nonsynth：無合成旗標；只跑 demo 路徑，釘「一個 wsynth_ 模組都沒被載入」與路由判斷等價。
#     guard   ：無合成旗標；`import wsynth_io` 必須被拒。
# =========================================================================================
_SELFTEST_MARK = "# ============================================================================ selftest ==="


def _t1():
    """凍結時鐘 T1＝horizon 起日＋60 天的 14:37（由參數推得，不寫日期字面值）。"""
    return _dt.datetime.combine(_P.HORIZON_START + _dt.timedelta(days=60), _dt.time(14, 37))


class _FakeConn:
    timeout = None

    def close(self):
        pass


class _FakeMES:
    """**只給測試**：以合成世界的段模擬凍結 SQL 的回列（讓真 fetcher 的 Python 後段真跑，拿來與本檔
    的同形函式逐值對比）。資料來源＝`wsynth_world.build_world(T)["segments"]`（產生器自己的曝光），
    WHERE 條件逐條照 SQL 文本獨立寫一次——不經本檔的查詢碼，才不是自己比自己。"""

    def __init__(self, segments, ctx, t):
        P = _dt.datetime.fromisoformat
        self.t = t
        self.rows = [(P(s["start"]), P(s["end"]), s["eid"], s["unit"], s["cls"], s["designation"],
                      s["result"]) for s in segments]
        names = sorted({a["asset_id"] for a in ctx.fleet} | {r[2] for r in self.rows})
        self.pk = {e: i + 1 for i, e in enumerate(names)}
        self.desc = {a["asset_id"]: a["display_name"] for a in ctx.fleet}
        self.edges = [(self.pk[a["parent_asset_id"]], self.pk[a["asset_id"]])
                      for a in ctx.fleet if a.get("parent_asset_id")]
        import board_capacity as _wcap
        self.var_of = {e: rec.get("var") for e, rec in _wcap.NEST_EAV_UNITS.items()}
        self._out = []
        self.n_exec = 0

    def execute(self, sql, *p):
        self.n_exec += 1
        s = " ".join(sql.split())
        t = self.t
        if s == "SELECT GETDATE()":
            self._out = [(t,)]
        elif "COL_LENGTH" in s:
            self._out = [(1,)]
        elif "astPK_parent" in s:
            self._out = list(self.edges)
        elif "sgiInteger" in s:
            var, cls, a, b = p
            self._out = [(r[2], r[0], r[1], r[5], r[6], int(r[3].split(":")[1])) for r in self.rows
                         if r[4] == cls and r[3] and r[3].startswith("nest:")
                         and self.var_of.get(r[2]) == var and r[1] >= a and r[0] < b and r[1] >= r[0]]
        elif "astEquipmentID = ?" in s:
            eid, cls, a, b = p
            self._out = [(r[2], r[0], r[1], r[5], r[6]) for r in self.rows
                         if r[2] == eid and r[4] == cls and r[1] >= a and r[0] < b and r[1] >= r[0]]
        else:
            cls, days = p
            lo = t - _dt.timedelta(days=days)
            hit = [r for r in self.rows if r[4] == cls and lo <= r[0] <= t and r[1] >= r[0]]
            if "astPK," in s:
                self._out = [(self.pk[r[2]], r[2], self.desc.get(r[2], r[2])) for r in hit]
            elif "matDesignation" in s:
                self._out = [(r[2], r[0], r[1], r[5]) for r in hit]
            else:
                self._out = [(r[2], r[0], r[1]) for r in hit]

    def fetchall(self):
        return list(self._out)

    def fetchone(self):
        return self._out[0] if self._out else None


def _norm_source(x):
    """深比較用：把 `source` 鍵的 live／synth 值抹平（兩條路徑唯一允許的差異＝標示）。"""
    if isinstance(x, dict):
        return {k: ("*" if k == "source" and v in (_wsrc.SOURCE_LIVE, _wsrc.SOURCE_SYNTH)
                    else _norm_source(v)) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return type(x)(_norm_source(v) for v in x)
    return x


def _union_sec(ivs):
    tot, cur_s, cur_e = 0.0, None, None
    for a, b in sorted(ivs):
        if cur_s is None or a > cur_e:
            if cur_s is not None:
                tot += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = a, b
        elif b > cur_e:
            cur_e = b
    if cur_s is not None:
        tot += (cur_e - cur_s).total_seconds()
    return tot


def _union_list(ivs):
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def _hand_states(ivs, t, thr_sec):
    """獨立手算（不呼叫凍結核心）：聯集 → 間隔 < thr 併 run → 生產＝Σrun、總＝t−首段起、不明＝其餘。"""
    u = _union_list(ivs)
    if not u:
        return None
    runs = [list(u[0])]
    for a, b in u[1:]:
        if (a - runs[-1][1]).total_seconds() < thr_sec:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    prod = sum((b - a).total_seconds() for a, b in runs)
    total = (t - u[0][0]).total_seconds()
    return {"prod": prod, "unknown": total - prod, "total": total,
            "strict": sum((b - a).total_seconds() for a, b in u)}


def _synth_child_tests():
    """合成子進程內的 G1–G9（回 `[[name, ok, detail], …]`）。"""
    import builtins
    import io as _io
    import os
    import time
    import mes_timeline as mt
    import board_data as wd
    import board_nest as wn
    import board_capacity as wcap
    import board_scope as wscope
    import wsynth_world as W
    import core_source as wsrc

    R = []

    def ck(name, cond, detail=""):
        R.append([name, bool(cond), str(detail)[:400]])

    T1 = now()
    ck("G0 clock frozen at T1 (child env only)", T1 == _t1() and _clk.is_frozen(), T1)
    ck("G0 tripwire passed and driver stub installed",
       (wsrc.tripwire_summary() or {}).get("ok") and wsrc.is_driver_stub(sys.modules.get("pyodbc")))
    stub = sys.modules["pyodbc"]
    n_connect = [0]
    _orig_connect = stub.connect

    def _count_connect(*a, **k):
        n_connect[0] += 1
        return _orig_connect(*a, **k)
    stub.connect = _count_connect

    # ---------------- G1 exposure／determinism／cross-day cache ----------------
    reset_cache()
    t0 = time.time()
    exp1 = exposed_segments(T1)
    ck("G1 world builds (first build seconds)", exp1, round(time.time() - t0, 1))
    ck("G1 R-6/Q-H: every exposed segment has end <= now", all(s.end <= T1 for s in exp1))
    w = world(T1)
    today = w.days.get(T1.date()) or {}
    hidden = [s for v in today.values() for s in v if s.end > T1]
    ck("G1 today's segments that end after now exist in the cache but are not exposed",
       hidden and not any(s.end > T1 for s in exp1), len(hidden))
    ck("G1 no segment longer than the generator bound (Q-H short runs)",
       all((s.end - s.start) <= _dt.timedelta(minutes=_P.SEG_MINUTES[1]) for s in exp1))
    ref1 = W.build_world(T1)
    key = lambda s: (s["start"], s["end"], s["eid"], s["unit"], s["cls"], s["designation"],
                     s["result"], s["stp_seq"])
    ref_t = [key(s) for s in ref1["segments"]]
    mine_t = [(s.start.isoformat(), s.end.isoformat(), s.eid, s.unit, s.cls, s.designation,
               s.result, s.stp_seq) for s in exp1]
    ck("G1 exposed segments == wsynth_world.build_world(T1)['segments'] (same order)",
       ref_t == mine_t, (len(ref_t), len(mine_t)))
    ck("G1 world_id matches the generator", world_meta()["world_id"] == ref1["world_id"])
    ck("G1 determinism: a second query returns the same", exposed_segments(T1) == exp1)
    reset_cache()
    ck("G1 determinism: a rebuilt cache returns the same", exposed_segments(T1) == exp1)
    info1 = cache_info()
    w = world(T1)
    ids1 = {d: id(v) for d, v in w.days.items()}
    calls = [0]
    _orig_gen = W.generate_day

    def _count_gen(*a, **k):
        calls[0] += 1
        return _orig_gen(*a, **k)
    W.generate_day = _count_gen
    try:
        T2 = T1 + _DAY + _dt.timedelta(hours=3)
        os.environ[_clk.ENV_FREEZE] = T2.isoformat()
        exp2 = exposed_segments()
    finally:
        W.generate_day = _orig_gen
        os.environ[_clk.ENV_FREEZE] = T1.isoformat()
    info2 = cache_info()
    ck("G1 cross-day: moving the clock one day generates exactly one new day",
       calls[0] == 1 and info2["n_generated"] == info1["n_generated"] + 1, (calls[0], info1, info2))
    ck("G1 cross-day: past days are the same cached objects (not recomputed)",
       all(id(w.days[d]) == i for d, i in ids1.items()))
    ref2 = W.build_world(T2)
    ck("G1 cross-day: exposed(T2) == build_world(T2)",
       [key(s) for s in ref2["segments"]] == [(s.start.isoformat(), s.end.isoformat(), s.eid, s.unit,
                                                s.cls, s.designation, s.result, s.stp_seq) for s in exp2])
    ck("G1 prefix stability: querying the past after extension is unchanged", exposed_segments(T1) == exp1)
    ck("G1 now() is second-floored clock time", now() == T1)

    # ---------------- G2 same shape AND same values as the real fetchers (fake SQL rows) ----------------
    fake = _FakeMES(ref1["segments"], w.ctx, T1)
    stations = list(mt.EQP_STATIONS)
    bad = []
    n_cmp = 0
    for days in (1, 3, 7, 30):
        for cls in stations:
            a = mt.fetch_fleet(fake, days, cls)
            b = fetch_fleet(days, cls, now=T1)
            n_cmp += 1
            if a != b or not all(isinstance(x, tuple) and len(x) == 2 and all(isinstance(v, str) for v in x)
                                 for x in b):
                bad.append(("fleet", days, cls))
                continue
            eids = [e for e, _ in a]
            sa = mt.fetch_segments(fake, eids, days, cls)
            sb = fetch_segments(eids, days, cls, now=T1)
            if {k: sorted(v) for k, v in sa.items()} != {k: sorted(v) for k, v in sb.items()}:
                bad.append(("segments", days, cls))
            if not all(isinstance(v, list) and all(isinstance(iv, tuple) and len(iv) == 2
                                                   and all(isinstance(x, _dt.datetime) and x.tzinfo is None
                                                           for x in iv) for iv in v) for v in sb.values()):
                bad.append(("segments-type", days, cls))
            da = wd.fetch_segments_desig(fake, eids, days, cls, desig_available=True)
            db = fetch_segments_desig(eids, days, cls, now=T1)
            if {k: sorted(v) for k, v in da.items()} != {k: sorted(v) for k, v in db.items()}:
                bad.append(("desig", days, cls))
    ck("G2 fetch_fleet／fetch_segments／fetch_segments_desig == real fetchers on the same rows "
       "(39 stations x 4 windows)", not bad and n_cmp == 4 * len(stations), bad[:6])
    ck("G2 desig guard: same answer as the real guard", wd.fetch_segments_desig_guard(fake) is True
       and fetch_segments_desig_guard() is True)
    ck("G2 alias stations fold into the main station (classes_for loop)",
       all(fetch_fleet(30, c, now=T1) == mt.fetch_fleet(fake, 30, c) for c in mt.CLASS_ALIASES))
    lo7 = T1 - _dt.timedelta(days=7)
    bad = []
    n_seat = 0
    for cls in sorted({rec["main_cls"] for rec in wcap.NEST_EAV_UNITS.values()}):
        for var in sorted({rec["var"] for rec in wcap.NEST_EAV_UNITS.values() if rec["main_cls"] == cls}):
            ra = wn.fetch_nest_seat_rows(cls, var, lo7, T1, cur=fake)
            rb = fetch_nest_seat_rows(cls, var, lo7, T1, now=T1)
            n_seat += len(rb)
            if sorted(ra) != sorted(rb) or not all(len(r) == 6 and isinstance(r[5], int) for r in rb):
                bad.append((cls, var))
    ck("G2 fetch_nest_seat_rows == real function on the same rows (six columns, int nest)",
       not bad and n_seat > 0, (bad, n_seat))
    bad = []
    n_cot = 0
    for mother, kids in sorted(wcap.COTAG_CHILD_UNITS.items()):
        cls = wcap.subunit_main_cls(mother)
        for kid in kids:
            ra = wn.fetch_cotag_rows(kid, cls, lo7, T1, cur=fake)
            rb = fetch_cotag_rows(kid, cls, lo7, T1, now=T1)
            n_cot += len(rb)
            if sorted(ra) != sorted(rb) or not all(len(r) == 5 for r in rb):
                bad.append(kid)
    ck("G2 fetch_cotag_rows == real function on the same rows (five columns)", not bad and n_cot > 0,
       (bad, n_cot))
    _orig_mt_connect = mt._connect
    mt._connect = lambda login_timeout=None: (_FakeConn(), fake)
    try:
        real_sb = mt.scoreboard_all_stations(days=3, stop_min=15)
    finally:
        mt._connect = _orig_mt_connect
    mine_sb = scoreboard_all_stations(days=3, stop_min=15, now=T1)
    ck("G2 scoreboard_all_stations == frozen function on the same rows (now, board, skipped)",
       real_sb == mine_sb and not mine_sb[2] and len(mine_sb[1]) == len(stations))

    # ---------------- G3 live branch vs synth branch of every page function ----------------
    class _NonSynth:
        def __getattr__(self, name):
            return getattr(wsrc, name)

        @staticmethod
        def is_synth_process():
            return False

    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return T1 if tz is None else _dt.datetime.now(tz)

    def as_live(fn, frozen_dt=False):
        saved = (wd._synth_proc, mt._connect, wn._wsrc, wd.datetime)
        wd._synth_proc = lambda: False
        mt._connect = lambda login_timeout=None: (_FakeConn(), fake)
        wn._wsrc = _NonSynth()
        if frozen_dt:                   # 只有日曆窗那支用 `datetime.now()` 算 fetch_days
            wd.datetime = _FrozenDT
        wd._ROSTER_FLEET_CACHE.clear()
        try:
            return fn()
        finally:
            wd._synth_proc, mt._connect, wn._wsrc, wd.datetime = saved
            wd._ROSTER_FLEET_CACHE.clear()

    def as_synth(fn):
        wd._ROSTER_FLEET_CACHE.clear()
        c0 = n_connect[0]
        try:
            return fn()
        finally:
            wd._ROSTER_FLEET_CACHE.clear()
            if n_connect[0] != c0:
                ck("G3 synth branch touched the driver stub", False, fn)

    cln = wd.CLN_CLASS
    nest_cls = sorted({rec["main_cls"] for rec in wcap.NEST_EAV_UNITS.values()})
    cot_cls = sorted({wcap.subunit_main_cls(m) for m in wcap.COTAG_CHILD_UNITS})
    y0 = _dt.datetime.combine(T1.date() - _DAY, _dt.time(0))
    t0d = y0 + _DAY
    cases = []
    for cls in [mt.CAL_CLASS, cln] + nest_cls + cot_cls:
        for days in (1, 7):
            cases.append(("get_live %s d%d" % (cls, days),
                          (lambda c=cls, d=days: wd.get_live(d, 15, None, None, c))))
        cases.append(("get_live %s range" % cls,
                      (lambda c=cls: wd.get_live(3, 15, y0.date().isoformat(), y0.date().isoformat(), c))))
        cases.append(("roster_fleet %s" % cls, (lambda c=cls: wd.roster_fleet(c, 7, "live"))))
        cases.append(("roster_fleet anchored %s" % cls,
                      (lambda c=cls: wd.roster_fleet(c, 7, "live", wide_days=40))))
    cases += [
        ("updown default windows", lambda: wd._updown_evidence_live()),
        ("updown 7/7", lambda: wd._updown_evidence_live(fleet_days=7, lookback_days=7)),
        ("fleet_by_cls d3", lambda: wd.get_fleet_by_cls_live(days=3)),
        ("fleet_by_cls d30 ordered", lambda: wd.get_fleet_by_cls_live(
            days=30, order={c: "2026" for c in stations[:5]})),
        ("scoreboard d3", lambda: wd.get_scoreboard_live(3, 15)),
        ("scoreboard_one CAL d7", lambda: wd.get_scoreboard_one_live(mt.CAL_CLASS, 7, 15)),
        ("scoreboard range yesterday", lambda: wd._scoreboard_range_live(y0, t0d, 15, None)),
        ("scoreboard range subset", lambda: wd._scoreboard_range_live(y0, t0d, 15, stations[:6])),
    ]
    for cls in (mt.CAL_CLASS, cln):
        cases.append(("gen2 module aggs %s" % cls, (lambda c=cls: wd._gen2_module_aggs_live(
            c, 7, 15, sorted({k for p, ks in wscope.PARENT_EXPANSION.items() for k in ks})))))
        cases.append(("band %s d7" % cls, (lambda c=cls: wd.expand_machines_band(
            "live", c, 7, 15, wd.get_live(7, 15, None, None, c)))))
        cases.append(("units %s d7" % cls, (lambda c=cls: wd.expand_units_timeline(
            "live", c, 7, 15, {m["eid"]: m["agg"] for m in wd.get_live(7, 15, None, None, c)["machines"]}))))
    for cls in nest_cls + cot_cls:
        cases.append(("load_seats %s" % cls, (lambda c=cls: wn.load_seats("live", c, 7))))
    bad = []
    for name, fn in cases:
        a = as_live(fn, frozen_dt=name.startswith("scoreboard range"))
        b = as_synth(fn)
        if _norm_source(a) != _norm_source(b) or a is None or b is None:
            bad.append(name)
    ck("G3 every page-level data function: synth branch == live branch fed the same rows "
       "(only the source label differs)", not bad and len(cases) > 30, (len(cases), bad[:6]))
    d_s = wd.get_live(7, 15, None, None, mt.CAL_CLASS)
    ck("G3 synth outputs carry source=synth (never live)",
       d_s["source"] == _wsrc.SOURCE_SYNTH and wd.get_scoreboard_live(3, 15)["source"] == _wsrc.SOURCE_SYNTH
       and wn.load_seats("synth", nest_cls[0], 7)["diag"]["source"] == _wsrc.SOURCE_SYNTH)
    ck("G3 window anchor is the synthetic clock (win_end == now)", d_s["win_end"] == T1 == d_s["mes_now"])

    # ---------------- G4 §0 shape pins against the demo/documented shapes ----------------
    dm = wd.get_demo(7, 15, None, None, mt.CAL_CLASS)
    ck("G4 get_live top-level keys == demo keys", set(d_s) == set(dm), sorted(set(d_s) ^ set(dm)))
    ck("G4 machine dict keys == demo machine keys",
       all(set(m) == set(dm["machines"][0]) for m in d_s["machines"]) and d_s["machines"])
    ck("G4 agg keys == demo agg keys", set(d_s["fleet"]) == set(dm["fleet"]))
    rec_keys = {"eid", "state", "conf", "start", "end", "sec"}
    ck("G4 record keys (§0: eid,state,conf,start,end,sec[,note])",
       all(rec_keys <= set(r) <= rec_keys | {"note"} for m in d_s["machines"] for r in m["records"]))
    sbw = wd.get_scoreboard_live(3, 15)
    sdm = wd._demo_scoreboard(3, 15)
    ck("G4 scoreboard canonical keys == demo; station keys == demo",
       set(sbw) == set(sdm) and all(set(s) == set(sdm["stations"][0]) for s in sbw["stations"]))
    ev = wd.get_updown_evidence("synth")
    ck("G4 updown evidence shape [(cls,[(eid,desc,{4 keys})])]",
       ev and all(isinstance(c, str) and all(len(t) == 3 and set(t[2]) == {
           "mes_last_h", "mes_any_last_h", "mes_last_designation", "mes_meta"} for t in ms) for c, ms in ev))
    fbc = wd.get_fleet_by_cls_live(days=3)
    ck("G4 fleet_by_cls shape {cls: [(eid, desc)]} (non-empty stations only)",
       fbc and all(v and all(isinstance(t, tuple) and len(t) == 2 for t in v) for v in fbc.values()))
    ls = wn.load_seats("synth", nest_cls[0], 7)
    ck("G4 load_seats shape (seats/win_end/diag/err; per-seat six counters)",
       set(ls) == {"seats", "win_end", "diag", "err"} and ls["seats"]
       and all(set(a) == set(wn._new_seat()) for u in ls["seats"].values() for a in u.values()))
    band = wd.expand_machines_band("synth", mt.CAL_CLASS, 7, 15, d_s)
    ck("G4 band child keys == _child_machine keys",
       band and all(set(k) == set(wd._child_machine("x", "", [], None, "p")) for v in band.values() for k in v))

    # ---------------- G5 aggregation: engine output == independent hand computation ----------------
    thr = 15 * 60
    by_cls_ref = {}
    for s in ref1["segments"]:
        by_cls_ref.setdefault(s["cls"], []).append(s)
    P = _dt.datetime.fromisoformat
    busiest = max(by_cls_ref, key=lambda c: len(by_cls_ref[c]))
    zl = sorted(W.default_zero_len_stations() & set(by_cls_ref))
    bad = []
    n_m = 0
    for cls in sorted({mt.CAL_CLASS, busiest, cln} | set(zl[:1])):
        data = wd.get_live(7, 15, None, None, cls)
        lo, hi = T1 - _dt.timedelta(days=7), T1
        flo = T1 - _dt.timedelta(days=8)
        cand = {s["eid"] for s in by_cls_ref.get(cls, ()) if flo <= P(s["start"]) <= T1}
        par = {a["asset_id"]: a["parent_asset_id"] for a in w.ctx.fleet if a.get("parent_asset_id")}
        fleet_hand = sorted(e for e in cand if par.get(e) not in cand)
        if [m["eid"] for m in data["machines"]] != fleet_hand:
            bad.append(("fleet", cls))
        fleet_prod = fleet_total = 0.0
        for m in data["machines"]:
            ivs = []
            for s in by_cls_ref.get(cls, ()):
                if s["eid"] != m["eid"] or not (flo <= P(s["start"]) <= T1):
                    continue
                a, b = max(P(s["start"]), lo), min(P(s["end"]), hi)
                if b > a:
                    ivs.append((a, b))
            h = _hand_states(ivs, hi, thr)
            n_m += 1
            if h is None:
                if m["records"]:
                    bad.append(("nosig", m["eid"]))
                continue
            got = m["agg"]["by_state"]
            if (abs(got.get("生產", 0) - h["prod"]) > 1e-6 or abs(got.get("不明", 0) - h["unknown"]) > 1e-6
                    or abs(m["agg"]["total_sec"] - h["total"]) > 1e-6
                    or abs(m["_a"]["producing_sec"] - h["strict"]) > 1e-6):
                bad.append(("states", m["eid"]))
            fleet_prod += h["prod"]
            fleet_total += h["total"]
        if fleet_total and abs(data["fleet"]["util"] - fleet_prod / fleet_total) > 1e-9:
            bad.append(("fleet-util", cls))
        if cls in zl and not any(m["zero_len_only"] for m in data["machines"]):
            bad.append(("zero-len", cls))
    ck("G5 per-machine state distribution (生產／不明／total／strict) and fleet util == hand computation "
       "from the world's own segments", not bad and n_m > 5, (n_m, bad[:6]))
    bad = []
    n_st = 0
    for cls in stations:
        if cls in zl:
            continue
        fl = fetch_fleet(7, cls, now=T1)
        sg = fetch_segments([e for e, _ in fl], 7, cls, now=T1)
        rec_py = wd.station_rollup_from_data(
            wd._live_build(fl, sg, T1 - _dt.timedelta(days=7), T1, 7, 15, "synth"), cls)
        rec_io = scoreboard_all_stations(days=7, stop_min=15, stations=[cls], now=T1)[1][0]
        if rec_py is None:
            if rec_io["n_seg"]:
                bad.append(cls)
            continue
        n_st += 1
        if {k: v for k, v in rec_py.items() if k != "zero_len_only"} != rec_io:
            bad.append(cls)
    ck("G5 C-SY8 cross pin: scoreboard station rec == station_rollup_from_data(_live_build(same segments))",
       not bad and n_st > 20, (n_st, bad[:5]))

    # ---------------- G6 Q-I: 形③ parent and seats ----------------
    bad = []
    for p, kids in sorted(wscope.PARENT_EXPANSION.items()):
        a = w.ctx.by_id.get(p)
        for cls in (a or {}).get("stations") or ():
            fl = [e for e, _ in fetch_fleet(30, cls, now=T1)]
            if p not in fl or set(kids) & set(fl):
                bad.append(("fleet", p, cls))
            ps = fetch_segments([p], 30, cls, now=T1).get(p, [])
            ks = fetch_segments(list(kids), 30, cls, now=T1)
            if _union_list(ps) != _union_list([iv for v in ks.values() for iv in v]) or not ps:
                bad.append(("union", p, cls))
            ap = mt.analyze(ps, T1, thr)
            kid_prod = [mt.analyze(v, T1, thr)["producing_sec"] for v in ks.values() if v]
            if not ap or not kid_prod or not (max(kid_prod) - 1e-6 <= ap["producing_sec"]
                                              <= sum(kid_prod) + 1e-6):
                bad.append(("bounds", p, cls))
            sb1 = scoreboard_all_stations(days=30, stop_min=15, stations=[cls], now=T1)[1][0]
            if sb1["fleet_size"] != len(fl):
                bad.append(("fleet_size", p, cls))
            data = wd.get_live(30, 15, None, None, cls)
            kids_rows = wd.expand_machines_band("synth", cls, 30, 15, data).get(p) or []
            pm = next((m for m in data["machines"] if m["eid"] == p), None)
            if [k["eid"] for k in kids_rows] != list(kids) or pm is None or any(
                    (k["_a"] or {}).get("producing_sec", 0) > pm["_a"]["producing_sec"] + 1e-6 for k in kids_rows):
                bad.append(("band", p, cls))
    ck("G6 Q-I: parent listed once, seats excluded from fleet, parent union == seats union, "
       "max(seat) <= parent <= Σseat, band rows == roster seats", not bad and wscope.PARENT_EXPANSION, bad[:6])
    bad = []
    for mother, kids in sorted(wcap.COTAG_CHILD_UNITS.items()):
        cls = wcap.subunit_main_cls(mother)
        mom = {(s.start, s.end, s.designation, s.result) for s in _closed_rows([cls], T1 - _dt.timedelta(days=31),
                                                                          T1, w) if s.eid == mother}
        fl = [e for e, _ in fetch_fleet(30, cls, now=T1)]
        if mother not in fl or set(kids) & set(fl):
            bad.append(("fleet", mother))
        for kid in kids:
            rows = fetch_cotag_rows(kid, cls, T1 - _dt.timedelta(days=30), T1, now=T1)
            if not all((r[1], r[2], r[3], r[4]) in mom for r in rows):
                bad.append(("cotag-subset", kid))
    ck("G6 形② co-tag: mother listed, children excluded, child rows ⊂ mother's segments", not bad, bad)
    # R44 seat-down derivation vs what utilization sees: no seat produces while R44 counts it down
    evs = ref1["events"]
    by_wo = {}
    for e in evs:
        if e["kind"].startswith("wo."):
            by_wo.setdefault(e["work_order_no"], []).append(e)
    fz = _clk.from_utc_z
    down = {}
    n_win = 0
    kid_parent = {k: p for p, ks in wscope.PARENT_EXPANSION.items() for k in ks}
    for wno, es in by_wo.items():
        op = es[0]
        asset = op["asset_id"]
        if asset not in wscope.PARENT_EXPANSION and asset not in kid_parent:
            continue
        if op["work_type"] == "REACTIVE":
            s0 = fz(op["failure_started_at"]) if op["failure_started_at"] else fz(op["at"])
            e0 = next((fz(e["value"]) for e in es if e["kind"] == "wo.field"
                       and e["field"] == "production_resumed_at"), None)
            if e0 is None:
                e0 = next((fz(e["at"]) for e in es if e["kind"] == "wo.status" and e["to_status"] in
                           ("COMPLETED", "CLOSED", "CANCELLED", "VOIDED")), T1)
        else:
            s0 = next((fz(e["at"]) for e in es if e["kind"] == "wo.status" and e["to_status"] == "IN_PROGRESS"),
                      None)
            if s0 is None:
                continue
            e0 = next((fz(e["at"]) for e in es if e["kind"] == "wo.status" and e["to_status"] == "COMPLETED"), T1)
        if asset in kid_parent:
            down.setdefault(asset, []).append((s0, e0))
            n_win += 1
            continue
        pieces = [(s0, op["scope"])] + [(fz(e["at"]), e["scope"]) for e in es if e["kind"] == "wo.scope"]
        scoped = {}
        for ts, sc in pieces:
            for k in sc or ():
                scoped.setdefault(k, max(ts, s0))
        targets = scoped if (op["work_type"] == "REACTIVE" and scoped) else \
            {k: s0 for k in wscope.PARENT_EXPANSION[asset]}
        for k, ts in targets.items():
            if ts < e0:
                down.setdefault(k, []).append((ts, e0))
                n_win += 1
    viol = []
    for p, kids in wscope.PARENT_EXPANSION.items():
        for cls in (w.ctx.by_id.get(p) or {}).get("stations") or ():
            segs = fetch_segments_desig(list(kids), 60, cls, now=T1)
            for k, ivs in segs.items():
                for a, b, dg in ivs:
                    if dg in _P.PRODUCTION_GRADE and any(a < y and b > x for x, y in down.get(k, ())):
                        viol.append((k, a.isoformat()))
    ck("G6 R44 consistency: no seat shows a production-grade segment inside its own or its parent's "
       "down window (REACTIVE scope rule; PM whole parent)", not viol and n_win > 0, (n_win, viol[:4]))

    # ---------------- G7 page functions run clean under the tripwire ----------------
    c0 = n_connect[0]
    outs = {}
    errs = []
    calls7 = [
        ("get_data", lambda: wd.get_data("synth", 7, 15, cls=mt.CAL_CLASS)["machines"]),
        ("get_data(demo collapses)", lambda: wd.get_data("demo", 7, 15, cls=mt.CAL_CLASS)["machines"]),
        ("roster_fleet", lambda: wd.roster_fleet(mt.CAL_CLASS, 7, "synth")[0]),
        ("updown", lambda: wd.get_updown_evidence("synth")),
        ("fleet_by_cls", lambda: wd.get_fleet_by_cls_live(days=3)),
        ("scoreboard", lambda: wd.get_scoreboard("synth", 3, 15)["stations"]),
        ("scoreboard_one", lambda: wd.get_scoreboard_one_live(mt.CAL_CLASS, 7, 15)["stations"]),
        ("scoreboard_range", lambda: wd.get_scoreboard_range("synth", y0, t0d)["stations"]),
        ("band", lambda: wd.expand_machines_band("synth", mt.CAL_CLASS, 7, 15, d_s)),
        ("units", lambda: wd.expand_units_timeline("synth", mt.CAL_CLASS, 7, 15,
                                                   {m["eid"]: m["agg"] for m in d_s["machines"]})),
        ("seats nest", lambda: wn.load_seats("synth", nest_cls[0], 7)["seats"]),
        ("seats cotag", lambda: wn.load_seats("synth", cot_cls[0], 7)["seats"]),
    ]
    for name, fn in calls7:
        try:
            outs[name] = fn()
            if not outs[name]:
                errs.append((name, "empty"))
        except Exception as e:
            errs.append((name, "%s: %s" % (type(e).__name__, e)))
    ck("G7 phase-1 data functions return non-empty with zero driver-stub hits",
       not errs and n_connect[0] == c0, (errs, n_connect[0] - c0))
    na = []
    if wd.get_qlik_grid("synth").get("unavailable") != NOT_PROVIDED:
        na.append("qlik")
    if wd.get_wip("synth").get("unavailable") != NOT_PROVIDED:
        na.append("wip")
    if wd.get_operator_class("synth").get("unavailable") != NOT_PROVIDED:
        na.append("operator_class")
    if wd.get_operator("synth").get("unavailable") != NOT_PROVIDED:
        na.append("operator")
    if wd.get_station_vars("synth", mt.CAL_CLASS).get("unavailable_reason") != NOT_PROVIDED:
        na.append("station_vars")
    if wd.get_wo_serials("synth", "x").get("unavailable_reason") != NOT_PROVIDED:
        na.append("wo_serials")
    for fn in (wd.get_mes_material, wd.get_mes_etest, wd.get_mes_equipment, wd.get_mes_route,
               wd.get_mes_segment):
        r = fn("any_tool", "synth", None)
        if r.get("unavailable_reason") != NOT_PROVIDED or r.get("source") != _wsrc.SOURCE_SYNTH:
            na.append(fn.__name__)
    for name, fn in (("yield_scoreboard", lambda: wd.get_yield_scoreboard("synth", 3)),
                     ("yield_range", lambda: wd.get_yield_scoreboard_range("synth", y0, t0d)),
                     ("yield_station", lambda: wd.get_yield_station("synth", mt.CAL_CLASS, 4)),
                     ("yield_machines", lambda: wd.get_yield_machines_from_fleet("synth", mt.CAL_CLASS, 4,
                                                                                [("x", "")])),
                     ("wip_rows", lambda: wd.fetch_wip_rows_live(T1))):
        try:
            fn()
            na.append(name + ":no-raise")
        except SynthNotProvided:
            pass
        except Exception as e:
            na.append("%s:%s" % (name, type(e).__name__))
    st = {}
    if wd.fetch_day_families_live(y0, t0d, stats=st) is not None or st.get("status") != "skipped:source":
        na.append("families")
    ck("G7 phase-2 fetchers: explicit not-provided shapes (reason=%s), never demo/fixture, zero stub hits"
       % NOT_PROVIDED, not na and n_connect[0] == c0, (na, n_connect[0] - c0))

    # ---------------- G8 zero real data: the adapter opens nothing ----------------
    opened = []
    sv = (builtins.open, _io.open, os.open)

    def spy(orig):
        def f(p, *a, **k):
            opened.append(str(p))
            return orig(p, *a, **k)
        return f
    builtins.open, _io.open, os.open = spy(sv[0]), spy(sv[1]), spy(sv[2])
    try:
        reset_cache()
        fetch_fleet(7, mt.CAL_CLASS, now=T1)
        fetch_segments(["x"], 7, mt.CAL_CLASS, now=T1)
        fetch_nest_seat_rows(nest_cls[0], sorted(r["var"] for r in wcap.NEST_EAV_UNITS.values())[0],
                             lo7, T1, now=T1)
        fetch_cotag_rows("x", cot_cls[0], lo7, T1, now=T1)
        scoreboard_all_stations(days=1, now=T1)
        exposed_segments(T1)
    finally:
        builtins.open, _io.open, os.open = sv
    ck("G8 C-SY1: building the world and every adapter query open zero files (no fixture/_state/snapshot)",
       not opened, opened[:5])
    loaded = sorted(n for n in sys.modules if n.startswith("wsynth_"))
    ck("G8 the adapter pulls in only the generator family (no cmms/seed emitters)",
       set(loaded) <= {"wsynth_io", "wsynth_world", "wsynth_clock", "wsynth_params"}, loaded)

    # ---------------- G9 R-8: families skipped does not make a synth ledger day unmeasurable ----------------
    import board_attrib as wattr
    import board_unit as wu
    leds = {}
    for cls in stations:
        dd = wd.get_live(1, 15, y0.date().isoformat(), y0.date().isoformat(), cls)
        leds[cls] = wattr.build_ledger(cls, dd["machines"], {}, None, None, None, win=(y0, t0d),
                                       zero_len_classes=wattr.ZERO_LEN_STATIONS)
    board = wattr.daily_board(leds, day=y0.strftime("%Y-%m-%d"), source=_wsrc.SOURCE_SYNTH,
                              wo_source_status="ok")
    st = {}
    wd.fetch_day_families_live(y0, t0d, stats=st)
    doc = wu.ledger_doc(board, None, day=y0.strftime("%Y-%m-%d"), source=_wsrc.SOURCE_SYNTH,
                        wo_source_status="ok", rw_axis=True, rw_reason="ok",
                        families_status=st.get("status"))
    doc_ok = wu.ledger_doc(board, None, day=y0.strftime("%Y-%m-%d"), source=_wsrc.SOURCE_SYNTH,
                           wo_source_status="ok", rw_axis=True, rw_reason="ok", families_status="ok")
    notes = sorted({s.get("note") for s in doc["stations"] if s.get("unmeasurable")} - {None})
    meas = [s for s in doc["stations"] if not s.get("unmeasurable")]
    ck("G9 R-8: families_status='skipped:source' in a synth day; the day is measurable, and no "
       "unmeasurable note comes from families",
       doc["families_status"] == "skipped:source" and wu.any_measurable(doc) and len(meas) > 10
       and not any("famil" in str(n) for n in notes)
       and [s.get("unmeasurable") for s in doc["stations"]] == [s.get("unmeasurable") for s in doc_ok["stations"]]
       and wu.should_overwrite(doc) == wu.should_overwrite(doc_ok), (len(meas), notes))
    ck("G9 zero-length stations are the only structural unmeasurables (T6)",
       {s["cls"] for s in doc["stations"] if s.get("note") == wattr.NOTE_ZERO_LEN}
       <= set(W.default_zero_len_stations()),
       notes)

    stub.connect = _orig_connect
    ck("G* driver stub never called during the whole synth child", n_connect[0] == 0, n_connect[0])
    return R


_NONSYNTH_CODE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
R = []
def ck(name, cond, detail=""):
    R.append([name, bool(cond), str(detail)[:400]])
import mes_timeline as mt
import board_data as wd
import board_nest as wn
import board_capacity as wcap
import core_source as wsrc
ck("N0 not a synthetic process", not wsrc.is_synth_process())
d = wd.get_data("demo", 7, 15, cls=mt.CAL_CLASS)
ck("N1 demo path unchanged (source=demo, machines present)", d["source"] == "demo" and d["machines"])
ck("N1 synth request in a live/demo process never reaches a live or synth path",
   wd.get_data("synth", 7, 15, cls=mt.CAL_CLASS)["source"] == "demo"
   and wd.roster_fleet(mt.CAL_CLASS, 7, "synth") == (None, "off")
   and wd.get_scoreboard("synth", 3, 15)["source"] == "demo")
nest_cls = sorted({r["main_cls"] for r in wcap.NEST_EAV_UNITS.values()})[0]
ck("N1 load_seats demo unchanged; synth rejected to empty facts",
   wn.load_seats("demo", nest_cls, 7)["diag"] == {"source": "demo"}
   and wn.load_seats("synth", nest_cls, 7)["seats"] == {})
ck("N1 band/units demo paths still run", isinstance(wd.expand_machines_band("demo", mt.CAL_CLASS, 7, 15, d), dict))
vals = ["live", "demo", "snapshot", "synth", "LIVE", "", None, "garbage", " live", 0]
ck("N2 _live_route(x) == (x == 'live') for every probe input",
   all(wd._live_route(v) == (v == "live") for v in vals), [(v, wd._live_route(v)) for v in vals])
ck("N2 _synth_proc() is False", wd._synth_proc() is False)
loaded = sorted(n for n in sys.modules if n.startswith("wsynth_"))
ck("N3 no wsynth_ module loaded in a non-synthetic process", not loaded, loaded)
try:
    import wsynth_io
    ck("N4 import wsynth_io refused outside a synthetic process", False)
except ImportError:
    ck("N4 import wsynth_io refused outside a synthetic process", True)
print(json.dumps(R))
'''

_SYNTH_CODE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import core_source
core_source.startup_tripwire()
import wsynth_io
print(json.dumps(wsynth_io._synth_child_tests()))
'''


def _repo_dir():
    import os
    return os.path.dirname(os.path.abspath(__file__))


def _run_child(code, env_over, timeout):
    import json
    import os
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="wsynth_io_child_") as td:
        env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
        for k, v in (env_over or {}).items():
            env[k] = v.replace("{TD}", td)
        r = subprocess.run([sys.executable, "-c", code, _repo_dir()], env=env, cwd=td,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    last = (r.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        return json.loads(last[0])
    except ValueError:
        return [["child crashed rc=%s" % r.returncode, False,
                 ((r.stdout or "")[-1500:] + "\n" + (r.stderr or "")[-1500:])]]


def _source_scan():
    """原始碼掃描（只掃轉接層＝selftest 標記之上）：無站名、無機台／編號字面值、無開檔。"""
    import io
    import re
    import tokenize
    with io.open(__file__, "r", encoding="utf-8") as f:
        src = f.read()
    head = src.split(_SELFTEST_MARK, 1)[0]
    pat_eid = re.compile("E" + "ID" + r"-\d")
    pat_cls = re.compile("s" + "eg" + "[A-Z][a-z]")
    pat_num = re.compile(r"\d{5}")
    bad_str, bad_num, opens = [], [], []
    toks = list(tokenize.generate_tokens(io.StringIO(head).readline))
    for i, t in enumerate(toks):
        if t.type == tokenize.STRING:
            if pat_eid.search(t.string) or pat_cls.search(t.string) or pat_num.search(t.string):
                bad_str.append((t.start[0], t.string[:60]))
        elif t.type == tokenize.NUMBER:
            try:
                v = abs(float(t.string))
            except ValueError:
                v = 0
            if v >= 10 ** 3:
                bad_num.append((t.start[0], t.string))
        elif t.type == tokenize.NAME and t.string == "open" and i + 1 < len(toks) and toks[i + 1].string == "(":
            opens.append(t.start[0])
    return bad_str, bad_num, opens, len(head.splitlines())


def _selftest(argv=()):
    verbose = "-v" in argv
    results = []

    def add(group, rows):
        for name, ok, detail in rows:
            results.append((group, name, ok, detail))

    bad_str, bad_num, opens, n_lines = _source_scan()
    add("scan", [["S1 adapter source has no station / machine / 5-digit literals", not bad_str, bad_str[:5]],
                 ["S2 adapter source has no numeric literal >= 1000", not bad_num, bad_num[:5]],
                 ["S3 adapter source never calls open()", not opens, opens],
                 ["S4 adapter part is the upper half only", n_lines > 50, n_lines]])
    add("guard", _run_child(_NONSYNTH_CODE, {}, 300))
    add("synth", _run_child(_SYNTH_CODE, {_wsrc.ENV_SOURCE: _wsrc.SOURCE_SYNTH,
                                          "BOARD_STATE_DIR": "{TD}/state",
                                          _clk.ENV_FREEZE: _t1().isoformat()}, 900))
    n_fail = 0
    for group, name, ok, detail in results:
        if not ok:
            n_fail += 1
        if verbose or not ok:
            print("%s [%s] %s%s" % ("ok  " if ok else "FAIL", group, name,
                                     "" if ok and not verbose else "  -- %s" % (detail,)))
    print("wsynth_io selftest: %d checks, %d failed" % (len(results), n_fail))
    return 1 if n_fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest(sys.argv[1:]))
    print(__doc__)
