# -*- coding: utf-8 -*-
"""
board_runwindow.py — 「該跑窗」（expected run window, erw）計算核心。契約 `board.runwindow.v1`。

規格＝`SPEC-RUNWINDOW.md` v1（2026-08-23 Chris 逐項拍板定案；本檔實作 §3 口徑與 §6/§6.2 裁決）。
**純函式、零 I/O、零 MES**——raw 檔的讀取住 `board_data`（C-MP3 loader 只有一份），
本檔只吃已載入的 dict。

一句話口徑（SPEC §3.1）：
    該跑窗 RW(m, D) = ( ShiftWin(R) ∪ OT(m, D) ) − Closure(D, m)
    · ShiftWin＝區的班次窗聯集（ZC＝day∪night 17.33h；ZAB＝day∪swing 聯集後連續 16.67h）。
    · OT＝窗外空隙段的**三層階梯**擴窗（C-RW14）：
        ① 同綑同機種其他機（SAME_KIND，人工審定）之 prod slot envelope
        ② 同綑其他機之 prod slot envelope
        ③ 人力細桶 > 0 ⇒ 該空隙段整段計入（兜底；整綑全倒但人在現場）
      **永不取機台自身訊號**（C-RW4：壞掉的機器不得因自己沉默而縮分母，
      也不得因自己加班而擴自己的窗——擴窗證據一律來自別台機或人力軸）。
    · Closure＝全廠停線（生產日曆），逐分鐘扣減；uncertain（該綑當日有 countable 工單）
      ⇒ 對該機不扣（C-RW7 保守側）。

時鐘與座標：
    · 生產日 D ＝ [D 08:00, D+1 08:00) 台北牆鐘（C-PC1）；本檔一律用「生產日分鐘」
      0..1440（0＝08:00）。slot i（10 分鐘）覆蓋分鐘 [i*10, i*10+10)。
    · 班窗／區映射／slot 編解碼**全部 import `_probe_kun_activity`**（唯一出處、零第二份）。

鐵則對照（動本檔前先讀 SPEC §2）：
    C-RW3  訊號只擴窗、永不砍窗——班次窗是下界；窗內沉默不改變任何位元（selftest 釘死）。
    C-RW4  擴窗訊號永不取機台自身——`run_window()` 在取 peers 時**明確剔除自己**（selftest 釘死）。
    C-RW9  非名冊機台 → `None`（不適用；呼叫端顯「—」，永不外推）。
    C-RW10 |RW| = 0 ⇒ 呼叫端顯「—」，永不 0%/100%。
    C-RW14 三層階梯逐段自上而下取第一個有訊號的層；**永不跳層、永不混層取聯集**；
           用了哪一層逐段記錄（`gaps[i]["tier"]`）＝A10 揭露的輸入。
    C-RW15 機種分組＝PLOVER 同列（`SAME_KIND`，人工審定 2026-08-23）；永不 runtime 從機名推。
    C-PC9  永不硬編星期——本檔零 weekday 分支；「週日不該跑」由 closure 訊號 judge。

fixture（QG-3 fixture-first）：
    python board_runwindow.py --make-fixture   → `_fixtures/board.runwindow.v1.json`
    golden＝本檔真函式對四形縮樣實跑的輸出（ZC 兩段細桶／ZAB 單段／無窗外桶／uncertain closure），
    不得手改。
"""
from __future__ import annotations

import datetime as _dt
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _probe_kun_activity import (          # 唯一出處（SALTMARSH-KUN-STRUCTURE.md §5.1/§5.2 逐字）
    KUN_ROSTER, SHIFT_WINDOWS, REGION_SHIFTS, _AREA2REGION, _EID2KUN,
    SLOT_MIN, SLOTS_PER_DAY, PROD_DAY_START_H, hex_to_slots,
)

SCHEMA = "board.runwindow.v1"
DAY_MIN = SLOTS_PER_DAY * SLOT_MIN                      # 1440

# ── 換軸告示的到期日（C-RW6 修訂；2026-08-23 晚 Chris 拍板，存證＝SPEC-RUNWINDOW §6.4）──
#   C-RW6 原本要求「口徑切換必須在頁面上顯著可見」＝**永久**一行常駐標語。修訂理由：那條
#   規則保護的是**切換期**（妥善率整批下降被例會讀成機隊變爛），不是永久看板租金；
#   頁首版位很貴（Chris 原話），而定義的永久居所是**欄表頭 tooltip ＋ 頁尾誠實段 ＋ time bar
#   本身**（SPEC §6.2② 原話：切換日的信任問題「主要靠它、不靠文字」）。
#   ⇒ 告示改成**有到期日**：到期後三頁的常駐口徑行自動消失，定義不消失。
#   ⚠ 判斷用的是**該頁自己的窗尾日期**（demo/snapshot 亦然），**不是 wall clock**——
#     讀 wall clock 會讓 demo 與快照的版面隨執行日漂移（QG-4.2 確定性）。
SWITCH_DATE = _dt.date(2026, 8, 23)                     # 換軸日（顯示用文案的日期）
SWITCH_NOTICE_UNTIL = _dt.date(2026, 8, 31)             # 常駐告示最後一天（含當天）


def switch_notice_on(asof):
    """換軸告示是否還要顯示（三頁共用的唯一判斷）。asof＝該頁窗尾的 date/datetime。

       True＝仍在過渡期（顯示一行「2026-08-23 起換軸」）；False＝到期，版面收回。
       None／型別不符 ⇒ False（不猜、不硬掛版位）。"""
    if isinstance(asof, _dt.datetime):
        asof = asof.date()
    if not isinstance(asof, _dt.date):
        return False
    return SWITCH_DATE <= asof <= SWITCH_NOTICE_UNTIL

# ── 綑成員／區（從 KUN_ROSTER 導出；資料本尊在 probe、此處零重打）──────────────
KUN_MEMBERS = {k: tuple(eids) for k, _a, _n, eids in KUN_ROSTER}
KUN_REGION = {k: _AREA2REGION[a] for k, a, _n, _e in KUN_ROSTER}


def kuns_of(eid):
    """eid → 所屬綑 tuple（共用 EID 屬兩綑；非名冊 → ()）。"""
    return tuple(_EID2KUN.get(eid) or ())


# ── 同機種分組（C-RW15；階梯第一層的參照集）──────────────────────────────────
# ★ 人工審定 2026-08-23：PLOVER 三張表「同一列多顆 EID」＋ EQMT_CATALOG 機名交叉檢查，
#   兩把鑰匙互證後轉錄（存證＝SALTMARSH-KUN-STRUCTURE.md §2c-2）。**永不 runtime 從機名字串推**。
#   維護通道＝Chris 人工告知（§3-3）。單機工站不列（tier 1 對它們天然為空 ⇒ 落 tier 2）。
_SAME_KIND_GROUPS = (('EID-54769', 'EID-54428'),
                     ('EID-54336', 'EID-54466'),
                     ('EID-54519', 'EID-55462'),
                     ('EID-55357', 'EID-54508'),
                     ('EID-55032', 'EID-55352'),
                     ('EID-55459', 'EID-54748'),
                     ('EID-55593', 'EID-54798', 'EID-54733'),
                     ('EID-54655', 'EID-55109'),
                     ('EID-54348', 'EID-54597', 'EID-54471'),
                     ('EID-54977', 'EID-54950'),
                     ('EID-54959', 'EID-55219'),
                     ('EID-55058', 'EID-54573'),
                     ('EID-55040', 'EID-54306'),
                     ('EID-54912', 'EID-54681', 'EID-54775', 'EID-54457'),
                     ('EID-54829', 'EID-55130'),
                     ('EID-54358', 'EID-54395', 'EID-55074', 'EID-55513', 'EID-55177'),
                     ('EID-55196', 'EID-54760', 'EID-54380'),
                     ('EID-55053', 'EID-55338'),
                     ('EID-55518', 'EID-54791'),
                     ('EID-54506', 'EID-54411'))
SAME_KIND = {e: g for g in _SAME_KIND_GROUPS for e in g}


# ── 分鐘 interval 代數（[lo, hi) 半開；全部回傳新 list、不 mutate 輸入）────────
def iv_union(ivs):
    """[(lo,hi)…]（可重疊/亂序）→ 合併後有序不重疊清單。"""
    out = []
    for lo, hi in sorted((lo, hi) for lo, hi in ivs if hi > lo):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def iv_subtract(ivs, cuts):
    """ivs − cuts（兩者皆 [(lo,hi)…]）→ 有序不重疊清單。"""
    out = list(iv_union(ivs))
    for clo, chi in iv_union(cuts):
        nxt = []
        for lo, hi in out:
            if chi <= lo or clo >= hi:
                nxt.append((lo, hi))
                continue
            if lo < clo:
                nxt.append((lo, clo))
            if chi < hi:
                nxt.append((chi, hi))
        out = nxt
    return out


def iv_intersect(ivs_a, ivs_b):
    """兩清單交集 → 有序不重疊清單。"""
    a, b, out = iv_union(ivs_a), iv_union(ivs_b), []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return out


def iv_total(ivs):
    """總分鐘數（假設已 union；防禦性起見仍先 union）。"""
    return sum(hi - lo for lo, hi in iv_union(ivs))


# ── 班次窗與窗外空隙段（分鐘；全由 SHIFT_WINDOWS 推導、本檔零時刻字面）──────────
def shift_iv(region):
    """區 → 班次窗聯集的分鐘 interval 清單（ZC 兩段、ZAB 聯集後一段）。"""
    return iv_union([tuple(SHIFT_WINDOWS[sh]) for sh in REGION_SHIFTS[region]])


def outside_gaps(region):
    """區 → 窗外空隙段 ((name, lo, hi), …)，名稱＝`outside_<區>_<序>`（1 起算、按起點排序）
       ＝人力 raw 細桶鍵的同一套推導（`_probe_kun_manpower` 亦由 SHIFT_WINDOWS 推導 ⇒ 同源同名）。"""
    gaps = iv_subtract([(0, DAY_MIN)], shift_iv(region))
    return tuple((f"outside_{region}_{i + 1}", lo, hi) for i, (lo, hi) in enumerate(gaps))


# ── raw 存取（純 dict 讀法；I/O 在 board_data）────────────────────────────────
def slots_coverage(slots_raw):
    """slot raw → (首日, 末日) iso 字串；空/壞 → (None, None)。
       ⚠ 保守：coverage 取**實際出現過的日鍵** min/max——窗邊緣的全靜日會縮 coverage 一天，
       方向＝少主張（C-RW8 的正確側）。"""
    days = sorted((slots_raw or {}).get("by_prod_day_slots") or ())
    return (days[0], days[-1]) if days else (None, None)


def decode_day_slots(slots_raw, day_iso):
    """slot raw ＋ 生產日 → {eid: frozenset(prod slot)}；當日未涵蓋 → None。
       日鍵在 coverage 內但缺席＝全廠零訊號日（合法、回空 dict）——**缺鍵 ≠ 未涵蓋**。"""
    lo, hi = slots_coverage(slots_raw)
    if lo is None or not (lo <= day_iso <= hi):
        return None
    day = (slots_raw.get("by_prod_day_slots") or {}).get(day_iso) or {}
    return {e: frozenset(hex_to_slots((rec or {}).get("prod") or "0" * 36))
            for e, rec in day.items()}


def manpower_coverage(mp_raw):
    """人力 raw → (首日, 末日) iso；同 slots_coverage 的保守讀法。"""
    days = sorted((mp_raw or {}).get("by_prod_day_shift") or ())
    return (days[0], days[-1]) if days else (None, None)


def manpower_day(mp_raw, day_iso):
    """人力 raw ＋ 生產日 → {kun: {bucket: n}}；當日未涵蓋 → None。
       綑當日缺鍵＝零段（真無訊號，raw notes 明訂）——由呼叫端 `.get(kun) or {}` 處理。"""
    lo, hi = manpower_coverage(mp_raw)
    if lo is None or not (lo <= day_iso <= hi):
        return None
    return (mp_raw.get("by_prod_day_shift") or {}).get(day_iso) or {}


# ── 核心：單機單日該跑窗 ─────────────────────────────────────────────────────
def _peer_envelope(peers, day_slots, gap_lo, gap_hi):
    """peers 的 prod slot ∪ ∩ 空隙段 → envelope (lo, hi) 分鐘；無訊號 → None。
       envelope 取 slot 邊界（10 分鐘粒度、不內插；SPEC §3.1）。"""
    s_lo, s_hi = gap_lo // SLOT_MIN, gap_hi // SLOT_MIN
    hit = sorted(i for p in peers for i in (day_slots.get(p) or ()) if s_lo <= i < s_hi)
    if not hit:
        return None
    return (max(gap_lo, hit[0] * SLOT_MIN), min(gap_hi, (hit[-1] + 1) * SLOT_MIN))


def run_window(eid, day_slots, manpower_kun, closures_min=()):
    """單機單日該跑窗。

    輸入（全部是**當日**的綑級素材；raw 的載入與逐日切片在呼叫端）：
        eid          — 機台。非名冊 ⇒ 回 None（C-RW9 不適用）。
        day_slots    — `decode_day_slots` 輸出（{eid: slotset}）；**None＝slot 源當日未涵蓋**
                       ⇒ 階梯 ①② 不可用。⚠ 此時**不得**讓階梯 ③ 單獨擴整段——那會靜默
                       重演「全段擴窗」的塌陷（SPEC §6.1 A1 反證）⇒ ③ 一併停用、記 degraded。
        manpower_kun — `manpower_day` 輸出（{kun: {bucket: n}}）；None＝人力源當日未涵蓋
                       ⇒ 階梯 ③ 不可用、記 degraded。
        closures_min — [(lo, hi, uncertain_for_me)]，已 clip 到本生產日、分鐘座標；
                       `uncertain_for_me=True` ⇒ 該段對本機不扣（C-RW7）。

    輸出 dict：
        iv       — [(lo, hi)…] 分鐘 interval（已扣 closure、有序不重疊）。
        min      — 總分鐘。呼叫端遇 0 顯「—」（C-RW10）。
        gaps     — 逐空隙段階梯紀錄 [{name, tier(1|2|3|None), lo, hi}]（A10 揭露輸入）。
        degraded — () 或 ("slots_uncovered"｜"manpower_uncovered"｜"no_fine_bucket", …)。
        region / kuns — 歸屬（共用 EID 取兩綑聯集擴窗＝保守放寬，SPEC §3.4）。
    """
    kuns = kuns_of(eid)
    if not kuns:
        return None                                    # C-RW9：非名冊 ⇒ 不適用
    regions = sorted({KUN_REGION[k] for k in kuns})
    region = regions[0]                                # 現行名冊：共用 EID 不跨區（ZC-2/ZC-5 皆 ZC）
    base = shift_iv(region)
    degraded = []
    expansions, gap_log = [], []
    # 階梯一律剔除自己（C-RW4）：peers 集合明確 `- {eid}`。
    kind_peers = tuple(e for e in (SAME_KIND.get(eid) or ()) if e != eid)
    kun_peers = tuple({e for k in kuns for e in KUN_MEMBERS[k]} - {eid})
    for name, lo, hi in outside_gaps(region):
        tier, span = None, None
        if day_slots is None:
            if "slots_uncovered" not in degraded:
                degraded.append("slots_uncovered")     # ①②③ 全停（見 docstring ⚠）
        else:
            if kind_peers:
                span = _peer_envelope(kind_peers, day_slots, lo, hi)
                if span:
                    tier = 1
            if span is None and kun_peers:
                span = _peer_envelope(kun_peers, day_slots, lo, hi)
                if span:
                    tier = 2
            if span is None:
                if manpower_kun is None:
                    if "manpower_uncovered" not in degraded:
                        degraded.append("manpower_uncovered")
                else:
                    n = 0
                    seen_fine = False
                    for k in kuns:
                        buckets = manpower_kun.get(k) or {}
                        if name in buckets:
                            seen_fine = True
                            n = max(n, int(buckets.get(name) or 0))
                        elif "outside" in buckets and "no_fine_bucket" not in degraded:
                            degraded.append("no_fine_bucket")   # 舊 raw 只有總桶＝降級（§3.1）
                    if seen_fine and n > 0:
                        tier, span = 3, (lo, hi)               # 兜底＝整段計入
        if span:
            expansions.append(span)
        gap_log.append({"name": name, "tier": tier,
                        "lo": (span[0] if span else None), "hi": (span[1] if span else None)})
    window = iv_union(list(base) + expansions)
    cuts = [(lo, hi) for lo, hi, unc in (closures_min or ()) if not unc]
    iv = iv_subtract(window, cuts)
    return {"iv": iv, "min": iv_total(iv), "gaps": gap_log,
            "degraded": tuple(degraded), "region": region, "kuns": kuns}


def closures_to_day_min(closures, day_start, eid):
    """`closures_with_uncertainty` 輸出（台北牆鐘 dict 清單）→ 本生產日的分鐘座標
       [(lo, hi, uncertain_for_me)]。`day_start`＝該生產日 08:00 的 datetime。"""
    out = []
    for c in closures or ():
        a, b = c.get("start"), c.get("end")
        if a is None or b is None:
            continue
        lo = max(0.0, (a - day_start).total_seconds() / 60.0)
        hi = min(float(DAY_MIN), (b - day_start).total_seconds() / 60.0)
        if hi > lo:
            unc = eid in (c.get("uncertain_eids") or ())
            out.append((lo, hi, unc))
    return out


def to_abs(day_start, ivs):
    """分鐘 interval → 絕對 datetime interval（`day_start`＝該生產日 08:00）。"""
    import datetime as _dt
    return [(day_start + _dt.timedelta(minutes=lo), day_start + _dt.timedelta(minutes=hi))
            for lo, hi in ivs]


# ── fixture（QG-3；golden 由真函式實跑產生、不得手改）──────────────────────────
def _fixture_slot_ranges():
    """fixture 的訊號 slot 位置——**全由 SHIFT_WINDOWS 推導的空隙段定位**（本檔零班表時刻字面）：
       peer＝ZC 第一段空隙起點後第 2 個 slot 起 15 個；self＝ZC 最末段空隙終點前 14..5 個 slot（10 個）；
       zab＝ZAB 第一段空隙起點後第 2 個 slot 起 6 個；sh＝ZC 第一段空隙起點後第 4 個 slot 起 4 個。
       空隙段落在哪由班表決定，形狀（有窗外訊號／自己的窗外訊號／ZAB 單段）不變。"""
    gw, gd = outside_gaps("ZC"), outside_gaps("ZAB")
    w0, wend = gw[0][1] // SLOT_MIN, gw[-1][2] // SLOT_MIN
    d0 = gd[0][1] // SLOT_MIN
    return {"wl_peer": range(w0 + 2, w0 + 17), "wl_self": range(wend - 14, wend - 4),
            "zab": range(d0 + 2, d0 + 8), "sh": range(w0 + 4, w0 + 8)}


def _fixture_inputs():
    """四形縮樣（selftest 與 --make-fixture 共用同一份輸入 ⇒ golden 永遠可重建）。"""
    # 一切用真名冊 EID（虛構 EID 會被 C-RW9 擋掉＝測不到路徑）。
    wl_eid = "EID-36255"                                # ZC-4 AutoOQC（同機種有多台）
    zab_eid = "EID-25590"                                # ZB-2 Wirebond（同機種有多台）
    sr = _fixture_slot_ranges()
    slots_3d = {"by_prod_day_slots": {
        # 形一（ZC 細桶日）：同機種 peer 37932 在第一段窗外空隙有訊號（15 個 slot）
        "2026-08-10": {"EID-37932": {"prod": _hex(set(sr["wl_peer"]))},
                       "EID-36255": {"prod": _hex(set(sr["wl_self"]))}},  # 自己最末段窗外空隙＝不得擴自己
        # 形二（ZAB 單段日）：綑 peer 25098（非同機種路徑測不到；wirebond 同機種有多台
        #   ⇒ 此日給 25983 訊號＝tier 1）在 ZAB 窗外空隙（6 個 slot）
        "2026-08-11": {"EID-25983": {"prod": _hex(set(sr["zab"]))}},
        # 形三（無窗外訊號日）：全空 ⇒ 憑人力桶（形三給 0 ⇒ 不擴）
        "2026-08-12": {},
    }}
    mp_3d = {"by_prod_day_shift": {
        "2026-08-10": {"ZC-4": {"day": 3, "night": 2, "outside": 1,
                                "outside_ZC_1": 1, "outside_ZC_2": 0}},
        "2026-08-11": {"ZB-2": {"day": 2, "swing": 2, "outside": 0, "outside_ZAB_1": 0}},
        "2026-08-12": {"ZC-4": {"day": 3, "night": 2, "outside": 0,
                                "outside_ZC_1": 0, "outside_ZC_2": 0}},
    }}
    import datetime as _dt
    day_start = _dt.datetime(2026, 8, 10, PROD_DAY_START_H, 0, 0)
    # 形四（uncertain closure）：closure 蓋 20:00–23:00（分鐘 720–900），對 wl_eid 標 uncertain
    closures = [{"start": day_start + _dt.timedelta(minutes=720),
                 "end": day_start + _dt.timedelta(minutes=900),
                 "uncertain_eids": (wl_eid,)}]
    return wl_eid, zab_eid, slots_3d, mp_3d, closures, day_start


def _hex(slots):
    n = 0
    for i in slots:
        n |= 1 << i
    return format(n, "036x")


def make_fixture(path=None):
    import json, os
    wl_eid, zab_eid, slots, mp, closures, day_start = _fixture_inputs()
    def rw(eid, day, cl=()):
        return run_window(eid, decode_day_slots(slots, day), manpower_day(mp, day), cl)
    golden = {
        "form1_wl_fine": rw(wl_eid, "2026-08-10"),
        "form2_zab_single": rw(zab_eid, "2026-08-11"),
        "form3_no_outside": rw(wl_eid, "2026-08-12"),
        "form4_uncertain_closure": rw(wl_eid, "2026-08-10",
                                      closures_to_day_min(closures, day_start, wl_eid)),
    }
    doc = {"schema_id": SCHEMA,
           "provenance": {"built_by": "board_runwindow.py --make-fixture",
                          "spec": "SPEC-RUNWINDOW.md v1 (2026-08-23)",
                          "note": "golden＝真函式對 _fixture_inputs() 實跑輸出，不得手改"},
           "golden": golden,
           "invalid": {"non_roster_eid": {"input_eid": "EID-99999", "expect": None,
                                          "reject_by": "consumer"}}}
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_fixtures", "board.runwindow.v1.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
    return path


# ── 公開樹自測的合成 fixture 變體（R-P11；只供 selftest，私有樹不呼叫）──────────────
def _pub_roles():
    """合成名冊上選出 fixture 的同形角色 → ({fixture EID: 合成 EID}, {fixture 綑: 合成綑})；選不到任一角色 ⇒ None。
       角色（私有樹的原機台＝括號；鍵是 `_fixture_inputs` 與 selftest 的字面，公開樹裡兩邊經同一張對照表換名、逐字一致）：
         ZC 同機種一對（AutoOQC 36255＋peer 37932，ZC-4）｜ZAB 同機種組（Wirebond 25590＋組末 25983，ZB-2）｜
         ZAB 同機種組的機＋同綑非同機種 peer（26190＋25964）｜兩綑共用機＋其綑非同機種 peer（27600＋01066，ZC 區）。
       只用單綑機（共用機另列）、決定性（名冊與分組的宣告序）。"""
    def region(e):
        ks = kuns_of(e)
        return KUN_REGION[ks[0]] if ks else None

    def solo(e, reg):
        return len(kuns_of(e)) == 1 and region(e) == reg

    def kind_pair(reg, min_len):
        for g in _SAME_KIND_GROUPS:
            if len(g) >= min_len and all(solo(e, reg) for e in g) and len({kuns_of(e)[0] for e in g}) == 1:
                return g
        return None

    g_wl, g_zab = kind_pair("ZC", 2), (kind_pair("ZAB", 3) or kind_pair("ZAB", 2))
    if g_wl is None or g_zab is None:
        return None
    t2 = t2p = None
    for g in _SAME_KIND_GROUPS:
        if g is g_zab or not all(solo(e, "ZAB") for e in g):
            continue
        k = kuns_of(g[0])[0]
        p = next((e for e in KUN_MEMBERS[k] if solo(e, "ZAB") and e not in g), None)
        if p is not None:
            t2, t2p = g[0], p
            break
    sh = next((e for e, ks in _EID2KUN.items() if len(ks) > 1 and region(e) == "ZC"), None)
    shp = None if sh is None else next(
        (e for k in kuns_of(sh) for e in KUN_MEMBERS[k]
         if e != sh and len(kuns_of(e)) == 1 and e not in (SAME_KIND.get(sh) or ())), None)
    if t2 is None or shp is None:
        return None
    emap = {"EID-36255": g_wl[0], "EID-37932": g_wl[1], "EID-25590": g_zab[0], "EID-25983": g_zab[-1],
            "EID-26190": t2, "EID-25964": t2p, "EID-27600": sh, "EID-01066": shp}
    kmap = {"ZC-4": kuns_of(g_wl[0])[0], "ZB-2": kuns_of(g_zab[0])[0]}
    return emap, kmap


def _remap(obj, emap, kmap):
    """fixture 輸入的確定性換名（EID 與綑鍵；dict 鍵與值、list／tuple 逐項；其餘原樣）。"""
    if isinstance(obj, str):
        return emap.get(obj, kmap.get(obj, obj))
    if isinstance(obj, dict):
        return {_remap(k, emap, kmap): _remap(v, emap, kmap) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_remap(x, emap, kmap) for x in obj)
    return obj


# ── selftest（純離線）────────────────────────────────────────────────────────
def selftest():
    import datetime as _dt
    n = [0]

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            raise AssertionError(f"[{n[0]}] {msg}")

    # interval 代數
    ck(iv_union([(10, 20), (15, 30), (40, 50)]) == [(10, 30), (40, 50)], "union 合併重疊")
    ck(iv_subtract([(0, 100)], [(20, 30), (90, 200)]) == [(0, 20), (30, 90)], "subtract 兩刀")
    ck(iv_intersect([(0, 50)], [(40, 60), (70, 80)]) == [(40, 50)], "intersect")
    ck(iv_total([(0, 10), (5, 20)]) == 20, "total 先 union")

    # 班次窗／空隙段（全由 SHIFT_WINDOWS 推導；期望值＝以「逐分鐘覆蓋表」獨立重算，與被測的
    # interval 代數無共用碼，本檔零班表時刻字面——有真班表與合成班表兩種資料下都成立）
    def _ref_cov(region):
        cov = [False] * DAY_MIN
        for sh in REGION_SHIFTS[region]:
            lo_, hi_ = SHIFT_WINDOWS[sh]
            for m_ in range(max(0, lo_), min(DAY_MIN, hi_)):
                cov[m_] = True
        return cov

    def _ref_runs(cov, val):
        runs, i_ = [], 0
        while i_ < DAY_MIN:
            if cov[i_] == val:
                j_ = i_
                while j_ < DAY_MIN and cov[j_] == val:
                    j_ += 1
                runs.append((i_, j_))
                i_ = j_
            else:
                i_ += 1
        return runs

    cov_wl, cov_zab = _ref_cov("ZC"), _ref_cov("ZAB")
    win_wl, win_zab = _ref_runs(cov_wl, True), _ref_runs(cov_zab, True)
    gap_wl, gap_zab = _ref_runs(cov_wl, False), _ref_runs(cov_zab, False)
    base_wl = sum(cov_wl)
    ck(shift_iv("ZC") == win_wl, "ZC 班次窗＝各班窗逐分鐘聯集的連續段")
    ck(iv_total(shift_iv("ZC")) == base_wl, "ZC 班次窗總分鐘＝逐分鐘覆蓋表計數")
    ck(shift_iv("ZAB") == win_zab, "ZAB 班次窗＝各班窗逐分鐘聯集的連續段（交疊窗合併）")
    ck(outside_gaps("ZC") == tuple((f"outside_ZC_{i + 1}", lo_, hi_) for i, (lo_, hi_) in enumerate(gap_wl)),
       "ZC 空隙段＝覆蓋表補集、命名同人力 raw 細桶鍵")
    ck(outside_gaps("ZAB") == tuple((f"outside_ZAB_{i + 1}", lo_, hi_) for i, (lo_, hi_) in enumerate(gap_zab)),
       "ZAB 空隙段＝覆蓋表補集")
    sr = _fixture_slot_ranges()

    def _env(rng):                                      # 一串 slot 的 envelope（slot 邊界、不內插）
        return (min(rng) * SLOT_MIN, (max(rng) + 1) * SLOT_MIN)

    # 名冊導出
    # WP-10d（R-P11）：具名機台改由名冊常數推導（私有樹推出來＝原先的 27600／Wirebond；公開樹是合成名冊）。
    import core_paths as _wp
    _pub_tree = _wp.is_public_tree()
    _shared = next(e for e, ks in _EID2KUN.items() if len(ks) > 1)       # 共用 EID（屬兩綑）
    _g3 = next(g for g in _SAME_KIND_GROUPS if len(g) >= 3)              # 第一組三台以上的同機種（私有＝Wirebond）
    ck(len(kuns_of(_shared)) == 2 and kuns_of(_shared) == tuple(_EID2KUN[_shared])
       and (_pub_tree or (_shared == "EID-27600" and kuns_of(_shared) == ("ZC-2", "ZC-5"))),
       "共用 EID 屬兩綑")
    ck(kuns_of("EID-99999") == (), "非名冊 → 空")
    ck(SAME_KIND[_g3[0]] == tuple(_g3)
       and (_pub_tree or _g3 == ("EID-25590", "EID-25098", "EID-25983")), "Wirebond 同機種三台")
    ck("EID-00587" not in SAME_KIND, "Flex Bend and Test 單機站不在機種分組（tier1 天然空）")

    wl_eid, zab_eid, slots, mp, closures, day_start = _fixture_inputs()
    # 階梯 ② 與共用 EID 擴窗的具名角色（私有樹＝原寫死的四台；公開樹由 _pub_roles 換成合成名冊的同形機台）
    _t2, _t2p, _sh, _shp = "EID-26190", "EID-25964", "EID-27600", "EID-01066"

    # 階梯／closure／共用 EID 擴窗＝綁死具名名冊角色（ZC-4 AutoOQC 的同機種 peer、ZB-2 Wirebond、跨 ZC-2／ZC-5 的共用機）的構造測試。
    # 公開樹（R-P11）：fixture 的 EID 是私有名冊經對照表換過的編號、合成名冊對不到 ⇒ 以「角色」從合成名冊選同形機台
    #   （_pub_roles：同機種 peer、ZAB 同機種組、同綑非同機種 peer、兩綑共用機與其綑 peer），把 fixture 輸入確定性換名後照跑同一組斷言
    #   （合成 fixture 變體）；選不到角色才 skipped。私有樹零換名、斷言一條不動。
    _run_ladder = True
    if _pub_tree:
        _roles = _pub_roles()
        if _roles is None:
            _run_ladder = False
            print("board_runwindow selftest: skipped: ladder/closure/shared-EID fixtures are bound to named roster EIDs (synthetic roster lacks one of the roles)")
        else:
            _emap, _kmap = _roles
            wl_eid, zab_eid, slots, mp, closures = _remap((wl_eid, zab_eid, slots, mp, closures), _emap, _kmap)
            _t2, _t2p, _sh, _shp = (_emap[e] for e in ("EID-26190", "EID-25964", "EID-27600", "EID-01066"))
    if _run_ladder:
        # ── C-RW14 階梯四形（QG-13 構造測試）───────────────────────────────
        # ① 同機種有訊號 ⇒ tier 1（ZC 第一段空隙內 37932 的 15 個 slot ⇒ envelope＝首末 slot 邊界）
        r1 = run_window(wl_eid, decode_day_slots(slots, "2026-08-10"), manpower_day(mp, "2026-08-10"))
        g1 = {g["name"]: g for g in r1["gaps"]}
        env1 = _env(sr["wl_peer"])
        ck(g1["outside_ZC_1"]["tier"] == 1 and (g1["outside_ZC_1"]["lo"], g1["outside_ZC_1"]["hi"]) == env1,
           f"① 同機種訊號 ⇒ tier1 envelope＝{env1}（got {g1['outside_ZC_1']}）")
        # ★ C-RW4：自己在最末段空隙的訊號不得擴自己 ⇒ 最末段 tier=None（有兩段空隙時逐段驗；
        #   單段空隙時自己的訊號與 peer 同段，envelope 恰等於 peer 的 envelope 即證明自己沒被算進去）
        _last_wl = g1[f"outside_ZC_{len(gap_wl)}"]
        if len(gap_wl) > 1:
            ck(_last_wl["tier"] is None, "★C-RW4 自己的窗外訊號永不擴自己的窗")
        else:
            ck((_last_wl["lo"], _last_wl["hi"]) == env1, "★C-RW4 自己的窗外訊號永不擴自己的窗（單段空隙形）")
        ck(r1["min"] == base_wl + (env1[1] - env1[0]), "① 該跑窗＝班次窗 + 擴 envelope 分鐘")
        # ② 同機種全靜、同綑其他機有訊號 ⇒ tier 2：構造 wirebond 全靜、綑 peer 25098...
        #   （用 zab_eid 日：25983 是同機種 ⇒ 先驗 tier1；再改給非同機種 peer 驗 tier2）
        r2 = run_window(zab_eid, decode_day_slots(slots, "2026-08-11"), manpower_day(mp, "2026-08-11"))
        g2 = {g["name"]: g for g in r2["gaps"]}
        ck(g2["outside_ZAB_1"]["tier"] == 1
           and (g2["outside_ZAB_1"]["lo"], g2["outside_ZAB_1"]["hi"]) == _env(sr["zab"]),
           "② 前置：同機種 25983 訊號 ⇒ tier1（ZAB 空隙內 6 個 slot ⇒ 首末 slot 邊界）")
        slots_t2 = {"by_prod_day_slots": {"2026-08-11": {
            _t2p: {"prod": _hex(set(sr["zab"]))}}}}   # ZB-3 綑 peer、非 wirebond
        r2b = run_window(_t2, decode_day_slots(slots_t2, "2026-08-11"),
                         manpower_day(mp, "2026-08-11"))
        g2b = {g["name"]: g for g in r2b["gaps"]}
        ck(g2b["outside_ZAB_1"]["tier"] == 2, "② 同機種全靜、同綑其他機有訊號 ⇒ tier2")
        # ③ 整綑全靜＋人力桶 >0 ⇒ 整段：mp 給 ZC-4 晚段桶 1、slot 全空
        slots_empty = {"by_prod_day_slots": {"2026-08-10": {}}}
        r3 = run_window(wl_eid, decode_day_slots(slots_empty, "2026-08-10"), manpower_day(mp, "2026-08-10"))
        g3 = {g["name"]: g for g in r3["gaps"]}
        ck(g3["outside_ZC_1"]["tier"] == 3
           and (g3["outside_ZC_1"]["lo"], g3["outside_ZC_1"]["hi"]) == gap_wl[0],
           "③ 整綑全靜＋人力桶>0 ⇒ 兜底整段")
        if len(gap_wl) > 1:                                 # 人力 fixture 的 outside_ZC_2 桶＝0
            ck(g3["outside_ZC_2"]["tier"] is None, "③ 桶=0 的段不擴")
        # ④ 整綑全靜＋人力桶 0 ⇒ 不擴（形三日）
        r4 = run_window(wl_eid, decode_day_slots(slots, "2026-08-12"), manpower_day(mp, "2026-08-12"))
        ck(all(g["tier"] is None for g in r4["gaps"]) and r4["min"] == base_wl,
           "④ 零訊號零人力 ⇒ 該跑窗＝班次窗（不擴）")

        # ── C-RW3 永不砍窗：窗內沉默（slot 全空）⇒ 班次窗逐位不變 ─────────────
        ck(r4["iv"] == shift_iv("ZC"), "C-RW3 窗內沉默 ⇒ 班次窗一個位元不動")

        # ── C-RW9 / 輸入形 ────────────────────────────────────────────────
        ck(run_window("EID-99999", {}, {}) is None, "C-RW9 非名冊 ⇒ None")
        r_deg = run_window(wl_eid, None, manpower_day(mp, "2026-08-10"))
        ck(r_deg["min"] == base_wl and "slots_uncovered" in r_deg["degraded"]
           and all(g["tier"] is None for g in r_deg["gaps"]),
           "slot 源未涵蓋 ⇒ ①②③ 全停（防塌陷）＋degraded 標")
        r_deg2 = run_window(wl_eid, decode_day_slots(slots_empty, "2026-08-10"), None)
        ck("manpower_uncovered" in r_deg2["degraded"] and r_deg2["min"] == base_wl,
           "人力源未涵蓋 ⇒ tier3 不可用＋degraded 標")
        mp_old = {"by_prod_day_shift": {"2026-08-10": {kuns_of(wl_eid)[0]: {"day": 3, "outside": 2}}}}   # 私有樹＝ZC-4
        r_old = run_window(wl_eid, decode_day_slots(slots_empty, "2026-08-10"),
                           manpower_day(mp_old, "2026-08-10"))
        ck("no_fine_bucket" in r_old["degraded"] and all(g["tier"] is None for g in r_old["gaps"]),
           "舊 raw 只有總桶 ⇒ 不擴＋降級標（不拿總桶猜細段）")

        # ── closure 扣減（C-RW5/C-RW7/C-RW10）────────────────────────────────
        cm = closures_to_day_min(closures, day_start, zab_eid)      # zab_eid 不在 uncertain ⇒ 要扣
        ck(cm == [(720.0, 900.0, False)], "closure → 分鐘座標")
        r_cut = run_window(wl_eid, decode_day_slots(slots, "2026-08-10"),
                           manpower_day(mp, "2026-08-10"),
                           [(720.0, 900.0, False)])
        ck(r_cut["min"] == r1["min"] - 180, "closure 720–900 落在班次窗內 ⇒ 扣 180 分")
        r_unc = run_window(wl_eid, decode_day_slots(slots, "2026-08-10"),
                           manpower_day(mp, "2026-08-10"),
                           closures_to_day_min(closures, day_start, wl_eid))
        ck(r_unc["min"] == r1["min"], "C-RW7 uncertain closure 對本機不扣（分母不縮）")
        r_all = run_window(wl_eid, decode_day_slots(slots, "2026-08-12"),
                           manpower_day(mp, "2026-08-12"), [(0.0, 1440.0, False)])
        ck(r_all["min"] == 0 and r_all["iv"] == [], "C-RW10 closure 蓋滿 ⇒ |RW|=0（呼叫端顯「—」）")

        # ── C-RW4 迴歸釘（#36 那顆、改寫成本框的形）：停機多寡不進本函式的任何輸入 ⇒
        #    同一組綑級輸入下，分母恆定（把「停機 4h→20h」對映成「自己的 slot 有/無」再驗一次）
        slots_self_20h = {"by_prod_day_slots": {"2026-08-12": {
            wl_eid: {"prod": _hex(set(range(0, 120)))}}}}          # 自己狂跑 20h
        r_self = run_window(wl_eid, decode_day_slots(slots_self_20h, "2026-08-12"),
                            manpower_day(mp, "2026-08-12"))
        ck(r_self["iv"] == r4["iv"], "★C-RW4 自身訊號（多↔零）⇒ 該跑窗逐位不變")

        # ── 共用 EID：兩綑聯集擴窗（ZC-2/ZC-5 的 27600）────────────────────────
        slots_sh = {"by_prod_day_slots": {"2026-08-10": {
            _shp: {"prod": _hex(set(sr["sh"]))}}}}   # ZC-5 綑 peer（CLN#4）
        r_sh = run_window(_sh, decode_day_slots(slots_sh, "2026-08-10"), None)
        g_sh = {g["name"]: g for g in r_sh["gaps"]}
        ck(g_sh["outside_ZC_1"]["tier"] == 2, "共用 EID 吃兩綑聯集的 peers（ZC-5 訊號擴 ZC-2/5 共用機）")

    # ── coverage 讀法 ────────────────────────────────────────────────────
    ck(slots_coverage(slots) == ("2026-08-10", "2026-08-12"), "slot coverage＝日鍵 min/max")
    ck(decode_day_slots(slots, "2026-08-13") is None, "窗外日 ⇒ None（未涵蓋）")
    ck(decode_day_slots(slots, "2026-08-12") == {}, "窗內缺鍵日 ⇒ 空 dict（全靜、非未涵蓋）")
    ck(manpower_day(mp, "2026-07-01") is None, "人力窗外日 ⇒ None")

    # to_abs
    ab = to_abs(day_start, [(0, 60)])
    ck(ab[0][0] == day_start and (ab[0][1] - ab[0][0]).total_seconds() == 3600, "to_abs")

    # ── 換軸告示到期日（C-RW6 修訂；SPEC §6.4）──────────────────────────────
    ck(switch_notice_on(_dt.date(2026, 8, 23)) and switch_notice_on(_dt.date(2026, 8, 31)),
       "換軸告示：切換日與到期日當天都仍顯示（閉區間）")
    ck(not switch_notice_on(_dt.date(2026, 9, 1)),
       "換軸告示：過期就收版位（C-RW6 修訂＝保護切換期、不是永久看板）")
    ck(not switch_notice_on(_dt.date(2026, 8, 22)),
       "換軸告示：切換日之前不顯示（那天還是舊軸）")
    ck(switch_notice_on(_dt.datetime(2026, 8, 25, 3, 0)) and not switch_notice_on(None)
       and not switch_notice_on("2026-08-25"),
       "換軸告示：吃 datetime；None／字串一律 False（不猜、不硬掛版位）")

    print(f"board_runwindow selftest OK ({n[0]} checks)")
    return n[0]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--make-fixture", action="store_true")
    a = ap.parse_args()
    if a.make_fixture:
        print("fixture →", make_fixture())
    else:
        selftest()
