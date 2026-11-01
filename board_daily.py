#!/usr/bin/env python3
"""Tendline Board — 老闆視角『昨日戰報』呈現模型（/daily 頁）。

老闆第一問＝「昨天每一站都出貨達標了嗎？」→ 本模組把凍結核心算出的『真』訊號
（稼動 sb + 產出/良率 yb 兩個 canonical 計分板）鋪成 **43 站 Qlik route 序總表**
（`DAILY_STATIONS`＝`mes_qlik.QLIK_STATIONS` join BOSS 友善名；SPEC-DAILY-UNIT Part 0），
逐站疊：①產出 vs 目標 ②為什麼沒達標（時間帳 `led` ＋ 顆數帳 `pcs` 兩本帳並排、
**永不相加**）③在製(WIP)緩衝夠不夠 ④要補多少加班。

═══ 誠實鐵則（與 board_oee 同、前端必貫徹標示）═════════════════════════════
本模組**不連 MES、不改凍結核心**；只吃 board_data 的 canonical sb/yb（純算術），
且**直接重用 board_oee.station_model / production_model**（不另造 OEE 口徑）。
『真』：output / util(A×U) / quality（RAW v0、§26.5 口徑待 SME）。
『DEMO』（源未接、確定性假值、全標）：target/overtime（§26.6 SALTMARSH 訂單源）、
  WIP / WIP 目標（§26.10 在製盤點源未接）、A↔U 拆分（§23.4）。
窗＝**日曆昨天 [Y 00:00, Y+1 00:00) 台北 naive 牆鐘**（2026-07-07 改；非滾動 24h）＝
  誠實說明落 daily_window_note、具體窗字串走 win_lbl（app 端算）。
════════════════════════════════════════════════════════════════════════════
"""
import random
import sys

import mes_qlik as mqk              # 43 站序/顯示名唯一權威（SPEC-DAILY-UNIT §1.1；不另抄）
import mes_timeline as mt
import mes_yield as my              # 凍結；selftest 以 fake-cursor 驗 win additive path（不連 MES）
import board_attrib as wattr      # 昨日缺口時間帳本歸因引擎（`SPEC-DAILY-ATTRIB.md`；純函式）
import board_oee as oee
import board_unit as wunit        # 顆數帳本引擎（`SPEC-DAILY-UNIT.md`；純函式、本檔只消費）

# ── 老闆視角 20 站（老闆顯示名, canonical cls）；順序＝產線流向，UI 恆依此排 ──
# 每個 cls 皆為凍結核心 mt.EQP_STATIONS 真名（模組載入時 assert 防手滑）。
# 中信心站（Chris 待確認）於註解標明；擇一代理、絕不與同軸他站相加（§26.10）。
BOSS_STATIONS = [
    ("Frame", "segFrameBonding"),
    ("Press", "segLamination"),
    ("Leak Test", "segLamLeakTest"),
    ("Die Placement", "segDiePlacement"),
    ("Die Placement Cure", "segAttachCureOven"),
    ("Flex Bonding", "segFlexBonding"),
    ("Wire bond", "segWireBonding"),
    ("Seal", "segSealDispense"),
    ("Seal Cure", "segThermalCure"),          # 中信心，Chris 確認
    ("Potting", "segPotting"),
    ("UV cure", "segUVCure"),                  # 中信心
    ("PlasmaPrep", "segPlasmaActivation"),                             # 中信心（4 個 surface-activation 站擇一、勿相加 §26.10）
    ("CAL", "segCalibrationTest"),
    ("CLN", "segCleanTest"),
    ("AutoOQC", "segAutomatedOQC"),
    ("Chassis Attach", "segChassisAttach"),
    ("Manifold Attach", "segManifoldAttach"),
    ("Final Assembly", "segFinalOQC"),    # 中信心（真組裝站=eqp0 無訊號；改流量代理：OQC 6305顆/30d vs LeakTest 87段=抽測，2026-07-07 Chris 質疑後換；若與 AutoOQC 語意重複再調）
    ("QAConfig", "segQASetup"),                             # 中信心
    ("Vacuum Pack", "segVacuumPackInspect"),
]

# 手滑守衛：上表所有 cls 必須是凍結核心 EQP_STATIONS 真名（否則 runtime fetch 會空/炸）。
# 對映結果＝逐一驗過全 20 對上（見任務對照），無修正。
_EQP = set(mt.EQP_STATIONS)
_BAD = [c for _n, c in BOSS_STATIONS if c not in _EQP]
assert not _BAD, f"BOSS_STATIONS 有 cls 不在 EQP_STATIONS（手滑）：{_BAD}"

# C9 第二道守衛（SPEC-DAILY-ATTRIB §4）：20 站必須全在 **43 站 route 序**內，否則
# `downstream_of` 靜默回 None → 下游堵桶恆 0 **而且沒有任何錯誤訊息**（最壞的靜默謊）。
# 日後有人改一個 cls 名字，這行會在**載入時**大聲炸。2026-08-01 已逐一驗過 20/20 命中。
wattr.assert_route_coverage([c for _n, c in BOSS_STATIONS], "BOSS_STATIONS")

# ── 43 站 master（SPEC-DAILY-UNIT Part A §1.1）────────────────────────────────
# 站清單＝`mes_qlik.QLIK_STATIONS` import 重用（站序/ord/(cls,mcl) 唯一權威、絕不另抄）；
# 顯示名優先序＝BOSS 友善名（20 站）＞ seg canonical 名（label=None ⇒ 渲染層用 QLIK_DISPLAY）。
# 這是兩份**人工審定常數**在載入時的確定性 join——不是 runtime discovery（M4 紅線）。
_BOSS_LABEL = {cls: label for label, cls in BOSS_STATIONS}
DAILY_STATIONS = tuple((o, cls, _BOSS_LABEL.get(cls)) for o, cls, _m in mqk.QLIK_STATIONS)
assert len(DAILY_STATIONS) == 43, f"DAILY_STATIONS 應 43 站（實得 {len(DAILY_STATIONS)}）"
_MLC = {cls: mcl for _o, cls, mcl in mqk.QLIK_STATIONS}

# ── #103（`_SPEC-QLIK-YIELD.md` §12／D3-b）：Kestrel 的 45 站 master ────────────────
#   站清單＝`mes_qlik.KESTREL_STATIONS`（#102 已落地的人工審定常數）**import 重用、絕不另抄**。
#   ⚠ **沒有 BOSS 友善名**：那 20 個名字是 Chris 對 Plover 產線講的話（`BOSS_STATIONS`），
#     不是 Kestrel 的；共同站沿用 `QLIK_DISPLAY`、12 個獨有站用 `KESTREL_DISPLAY_DERIVED`
#     的推導名（#105，Chris 2026-09-08 裁）⇒ 這裡一律 `label=None`，渲染層自己查顯示名。
KESTREL_DAILY_STATIONS = tuple((o, cls, None) for o, cls, _m in mqk.KESTREL_STATIONS)
PRODUCT_STATIONS = {"plover": DAILY_STATIONS, "kestrel": KESTREL_DAILY_STATIONS}

# 時間帳站集＝QLIK ∩ EQP 交集（SPEC-DAILY-UNIT §1.2；route 序）。交集外的站（43 站中無
# 稼動源者）時間欄「—」＋note `no_time_src`（≠ zero_len ≠ nosig ≠ src_err——四 key 四句話）。
ATTRIB_STATIONS = tuple(cls for _o, cls, _l in DAILY_STATIONS if cls in _EQP)
assert all(c in set(ATTRIB_STATIONS) for _n, c in BOSS_STATIONS), \
    "BOSS 20 站必須全在 43∩EQP 交集內（時間帳擴站不得丟掉老闆站）"


def _seed(cls):
    """獨立於 board_oee._seed 的 seed base（同慣例、不同 stream）。"""
    return sum(ord(c) for c in (cls or "x")) * 137 + 11


def demo_wip(cls, output):
    """DEMO 在製量（WIP）：盤點源未接（§26.10）→ per-cls 確定性假值、**獨立 seed stream**。
       以 output 為量級錨：WIP 約 output 的 5–40%（同 cls 兩呼必同值）。"""
    if not output:
        return None
    rnd = random.Random(_seed(cls) * 17 + 3)
    return max(1, round(output * rnd.uniform(0.05, 0.40)))


def demo_wip_target(cls, output):
    """DEMO 在製目標（WIP 目標緩衝）：per-cls 確定性、獨立 stream。約 WIP 的 0.8–1.5 倍。"""
    w = demo_wip(cls, output)
    if w is None:
        return None
    rnd = random.Random(_seed(cls) * 19 + 5)
    return max(1, round(w * rnd.uniform(0.8, 1.5)))


def _demo_coverage(cls):
    """DEMO 人力覆蓋係數（一人顧 N 台/站；人力 CSV 未接）：per-cls 確定性 2–4。"""
    return random.Random(_seed(cls) * 23 + 7).randint(2, 4)


# 提案 C（2026-08-03）：**未解釋永不進任何損失榜**——它是「推定未派工／未進料」、不是
# 有人沒用掉的產能。時間帳 fallback chips 過去照 `led["top"]` 全收 ⇒ 一個佔比極大的桶會
# 直接以 ≈顆 chip 的形式登頂，正是驗收第②關要擋的東西。獨立揭露走 `ledger_presume`。
CHIP_EXCLUDED_BUCKETS = ("unexplained",)


def ledger_chips(led, met=None, max_n=2):
    """C8 chips＝**真時數桶**（不再是 DEMO target 反推的 OEE waterfall）。

    回 `[{"bucket","h","est","share"}…]`（依時數降冪、最多 `max_n` 桶；零時數桶不出）。
    · cap 預設 3→2（SPEC-DAILY-UNIT Part B §3.4：欄空間與顆數帳 chips 共享）。
    · gate＝`board_attrib.chips_gate` 的**雙軌**（`met is False` ∨ 損失時數 > 閾值）
      ⇒ 真診斷的可見性不被（仍是 DEMO 的）target 單獨把持。
    · 不可測站（零工期八站／整窗無訊號）**永不出 chips**（T6：那不是「四桶都 0」）。
    · `est` 為 None 時只給小時——**帳本的權威單位本來就是小時**，顆數只是溝通用估算（T4）。
    · ★ **未解釋桶永不入榜**（`CHIP_EXCLUDED_BUCKETS`；selftest 負斷言釘死）＝提案 C；
      它改由 `ledger_presume` 以中性「推定」註記行獨立現形。
    """
    if not wattr.chips_gate(led, met=met):
        return []
    est = (led or {}).get("est") or {}
    lost = float((led or {}).get("lost_h") or 0.0)
    out = []
    for bucket, h in (led.get("top") or []):
        if bucket in CHIP_EXCLUDED_BUCKETS:
            continue
        out.append({"bucket": bucket, "h": h,
                    "est": (est.get(bucket) if est else None),
                    "share": (h / lost if lost > 0 else None)})
        if len(out) >= max_n:
            break
    return out


def ledger_presume(led):
    """未解釋時數的**中性「推定」註記行**材料（提案 C；鏡像 hero 卡的 `presume` 行）。

    回 `{"h": 機時, "est": ≈顆|None}`，或 None（不可測／量不到／恰為 0 ⇒ 不出這行）。
    · 只在**無尺 fallback 路徑**用（有尺列的推定量由 `sent.unexp_h/unexp_n` 講、單位是顆）。
    · `est` 走時間帳自己的 per-bucket 換算（wattr est）＝這條路徑上唯一還合法的第二把尺
      ——因為這列本來就沒有良品速率尺可用；量不到就只講機時，絕不填 0。
    """
    if not led or led.get("unmeasurable"):
        return None
    h = led.get("unexplained_h")
    if h is None or float(h) <= 0:
        return None
    est = (led.get("est") or {}).get("unexplained")
    return {"h": float(h), "est": est}


# ── 顆數帳 chips / hero 跨帳排序（SPEC-DAILY-UNIT Part B §3.4／§4.2）──────────
# 顆數桶 → i18n 鍵（ctx 名 `pkey`；鍵住 board_unit.I18N_UNIT＝唯一一份、本檔不另造）。
PCS_BUCKET_I18N_KEY = dict(wunit.UNIT_BUCKET_I18N_KEY)

# display floor（§3.4）：n < floor 的顆數 chip 不出（live 校準＝改 env 不改碼；預設 1＝非零即顯）。
PCS_CHIP_MIN = wattr._env_int("BOARD_PCS_CHIP_MIN", 1)


def pcs_chips(urow, max_n=2):
    """顆數帳 chips（§3.4 gate）：非零即顯、cap `max_n`（依 n 降冪）、floor 走 env。

    回 `[{"bucket","n","approx"}…]`——報廢/重工＝精確計數（approx=False）、慢速恆帶 ≈
    （approx=True＝顯示層必印 ≈）。量不到（None）不出 chip＝T6（不是 0）。
    urow＝board_unit board row（顯示欄 `scrap_n`/`rework_n`/`slow_est_n`）；None → []。
    """
    if not urow:
        return []
    floor = max(1, PCS_CHIP_MIN)
    cand = []
    for b, v, approx in (("scrap", urow.get("scrap_n"), False),
                         ("rework", urow.get("rework_n"), False),
                         ("slow", urow.get("slow_est_n"), True)):
        if v is not None and v >= floor:
            cand.append({"bucket": b, "n": int(v), "approx": approx})
    cand.sort(key=lambda c: (-c["n"], wunit.UNIT_BUCKETS.index(c["bucket"])))
    return cand[:max_n]


def hero_top_cause(att_hero, pcs_hero):
    """hero「昨日最大損失」跨兩本帳排序（§4.2；純函式）。

    公共排序幣＝**顆**：時間桶用既有 est（per-machine-hour 換算、恆帶 ≈）、顆數桶原生。
    排序鍵三層＝① pcs 降冪 → ② 精確（approx=False）勝過估算 → ③ 顆數帳勝過時間帳。
    **反模式（selftest 釘死）**：永不跨帳相加、永無「gap≡Σ桶」、≈ 在估算值上不可省。

    回 `{"top": cand|None, "alt": 另一本帳第一名|None, "unranked_mh": float|None,
        "n_candidates": int}`；cand＝`{"acct"("time"|"pcs"), "bucket", "pcs", "mh"(time
    only), "approx", "worst_cls"}`。
    · `unranked_mh`＝est 缺（rate 缺站/est_partial）而**退出排序**的時間桶時數合計
      ——時間損失絕不因換算不了而靜默消失（`dl_hero_unranked` 揭露行的數字源）。
    · pcs_hero 缺/不可用 → 只剩時間帳 candidates＝整卡退回今日行為（降級 byte-可比）。
    """
    ah = att_hero or {}
    ph = pcs_hero or {}
    est = ah.get("est") or {}
    tw = ((ah.get("worst") or {}).get("cls"))
    pw = ((ph.get("worst") or {}).get("cls"))
    cands, unranked = [], 0.0
    for b, h in (ah.get("top") or ()):
        e = est.get(b) if est else None
        if e:
            cands.append({"acct": "time", "bucket": b, "mh": float(h or 0.0),
                          "pcs": float(e), "approx": True, "worst_cls": tw})
        else:
            unranked += float(h or 0.0)          # 換算不了 ⇒ 退出排序、但要被揭露
    for p, n in (ph.get("top") or ()):
        if n:
            cands.append({"acct": "pcs", "bucket": p, "mh": None, "pcs": float(n),
                          "approx": (p == "slow"), "worst_cls": pw})
    # 三層排序鍵（False<True ⇒ 精確在前；"pcs" 帳在前）
    cands.sort(key=lambda c: (-c["pcs"], c["approx"], 0 if c["acct"] == "pcs" else 1))
    top = cands[0] if cands else None
    alt = next((c for c in cands if top and c["acct"] != top["acct"]), None)
    return {"top": top, "alt": alt,
            "unranked_mh": (round(unranked, 2) if unranked > 1e-9 else None),
            "n_candidates": len(cands)}


# ── 提案 F 句式 v2 ＋ 提案 C 用詞（ROADMAP §2.1；2026-08-02 Chris 拍板 A/E/H/F/C）──
# 句子裡最多列幾個原因（`ranked` 站級本來就是 top-3；線級是全六桶 ⇒ 這裡再 cap 一次）。
SENT_CAUSE_MAX = 3


def sentence_ctx(row, *, target_real=False):
    """單站一句人話的**材料**（純函式；本函式不組任何字串）；無句可講 → None。

    回 `{"kind","gap","tot","causes","unexp_h","unexp_n"}`——模板取 `t.un_sent_<kind>`
    （`clean` ⇒ `un_sent_met_clean`）再 replace `{gap}/{tot}/{causes}`：
      · `kind` ∈ {"full","short","plain","clean","noruler"}。
      · `causes` ＝ `[(bucket, pcs:int)…]` 最多 `SENT_CAUSE_MAX` 個，取自 `ranked`
        （提案 H 單一尺排序、已排除未解釋與負值）；零值省略。桶名走 `skey[bucket]`
        ＝`board_unit.LOSS_SHORT_I18N_KEY`（**措辭唯一一份住 board_unit**、本檔不另造）。
      · `unexp_h`/`unexp_n` ＝提案 C 的「推定未派工／未進料」獨立子句材料
        （`un_sent_unexp`）；任一量不到 ⇒ 兩者皆 None ⇒ 模板不出該子句。

    ★★ 兩條鐵則（selftest 釘死、勿放寬）
      1. **WIP / 加班永不入句**——兩者皆 DEMO 合成值（§26.10），寫進流暢中文會被當成真的。
      2. **`{gap}` 只在 `target_real=True` 時才有值**。今天呼叫端恆傳 `False`：target 仍是
         DEMO（§26.6 SALTMARSH 訂單源未接）⇒ 達標與否交給 ✓/✕ 符號講、句子完全不碰
         ——這與 WIP 入句是同一個陷阱。SALTMARSH 真目標源接通那天把呼叫端改成 `True`，
         `full`/`short` 兩式自動生效（本函式與文案都不必再動）。
    """
    pcs = (row or {}).get("pcs")
    if not pcs:
        return None                       # 顆數帳該站無列（或整列無資料）＝沒有句子可講
    lb = pcs.get("loss_by")
    if not isinstance(lb, dict):
        # 無尺（首暖／報廢腿缺／時間腿缺）：**時間軸本身可測**才值得講「還沒有尺」；
        # 時間軸本來就量不到（零工期/無訊號/查不到）⇒ 那句話講了也是廢話，交給
        # 既有 `led_note`/`led_chips` 的降級路徑說原因。
        led = (row or {}).get("led")
        if led and not led.get("unmeasurable"):
            return {"kind": "noruler", "gap": None, "tot": None, "causes": [],
                    "unexp_h": None, "unexp_n": None}
        return None
    la = pcs.get("loss_actionable")
    la = float(la) if la is not None else 0.0
    met = row.get("met")
    gap = None
    if target_real and met is False:
        kind = "full"
        g = row.get("gap")
        gap = int(round(abs(float(g)))) if g is not None else None
    elif target_real and met:
        kind = "short"
    elif la < 1:
        kind = "clean"                    # 可行動未使用產能不足 1 顆＝沒什麼好講的
    else:
        kind = "plain"
    causes = [(c["bucket"], int(round(float(c["pcs"]))))
              for c in (pcs.get("ranked") or ())][:SENT_CAUSE_MAX]
    causes = [(b, n) for b, n in causes if n]
    # 合計 ≥1 顆、卻沒有任何**單桶**達得到 display floor（薄薄攤在六個桶上）⇒ 句子會變成
    # 「未使用產能 ≈2 顆：。」＝空冒號。退回 clean（誠實：確實沒有可指名的原因）。
    if kind in ("full", "short", "plain") and not causes:
        kind, gap = "clean", None
    uh, un = pcs.get("unexplained_h"), pcs.get("loss_unexplained")
    if uh is None or un is None:
        uh = un = None                    # 任一腿缺 ⇒ 整個子句不出（絕不半句、絕不填 0）
    else:
        uh, un = round(float(uh), 1), int(round(float(un)))
    return {"kind": kind, "gap": gap, "tot": int(round(la)), "causes": causes,
            "unexp_h": uh, "unexp_n": un}


def line_sentence_ctx(unit_hero, att_hero=None):
    """線級版句式材料（提案 H hero 卡）：把線級聚合包成 `_row` 同形餵 `sentence_ctx`。

    kind 只會是 plain/clean/noruler——**線級沒有 target**（達標是逐站的事）⇒ 恆
    `target_real=False` ⇒ 永不 full/short。

    ★★ 兩腿同範圍（2026-08-03 群 B fix ③）：`unexplained_h` 取
    **`unit_hero["unexplained_ruled_h"]`**（只 Σ 有尺的列）、不再取時間帳線級
    `att_hero.by_bucket["unexplained"]`（那是**全部可測站**的 Σ）。舊寫法讓句子的
    `{h}`（全站機時）與 `{n}`（只有尺站的顆）來自不同站集 ⇒ 讀者拿 n÷h 反算速率
    對不上任何一站。時間帳全站的未解釋機時在時間帳區塊本來就有顯示、資訊不丟。
    `att_hero` 保留在簽章上僅為呼叫端相容（**本函式不再讀它**）。
    """
    uh = unit_hero or {}
    lb = uh.get("loss_by")
    pcs = {"loss_by": (lb if isinstance(lb, dict) else None),
           "loss_actionable": uh.get("loss_actionable"),
           "loss_unexplained": uh.get("loss_unexplained"),
           "unexplained_h": uh.get("unexplained_ruled_h"),
           "ranked": uh.get("loss_ranked") or []}
    # led 佔位＝「線級時間軸可測」：線上總有站量得到，所以無尺時講 noruler 是對的。
    return sentence_ctx({"pcs": pcs, "met": None, "gap": None,
                         "led": {"unmeasurable": False}}, target_real=False)


def _row(label, cls, u, y, window_sec, demo, qlik_row=None, qlik_on=False, led=None,
         ordn=None, pcs=None, display_map=None, mcl_map=None, display_src_map=None):
    """單站『昨日戰報』列：站級三軸（reuse oee.station_model）＋ WIP/加班（demo）＋無訊號旗標。
       qlik_on＝D1/D2 站級公司口徑（產出＝Qlik TOTAL、良率＝Qlik final_yield）；無對映列 → 誠實留白。
       led＝`board_attrib.build_ledger` 的該站時間帳本（可 None＝該源/該站無帳本 → 帳本欄「—」）。
       ordn＝43 站 route 序（QLIK ord）；pcs＝`board_unit` board row（可 None＝顆數帳
       該源不可用/該站無列 → 顆數欄「—」、零 chips——絕不填 0＝T6）。"""
    st = oee.station_model(u or {"cls": cls}, y, window_sec, demo,
                           qlik_row=qlik_row, qlik_on=qlik_on)
    output = st["output"]
    active = st["active_sec"] or 0.0
    no_signal = (active <= 0) and not (output or 0)  # 窗內無 MES 訊號（active=0 且 output 空）

    wip = wip_target = wip_short = ot_wip_h = None
    if not no_signal and demo:
        wip = demo_wip(cls, output)
        wip_target = demo_wip_target(cls, output)
        if wip is not None and wip_target is not None:
            wip_short = max(0, wip_target - wip)           # WIP 缺口（<目標＝在製不足）
            rate = st.get("actual_rate_uph")               # 真實絕對速率（件/機台小時）；無→誠實 None
            cov = _demo_coverage(cls)
            if wip_short > 0 and rate and rate > 0:
                ot_wip_h = wip_short / rate / cov           # 補 WIP 缺口所需加班（率真·覆蓋 demo）

    r = {
        "label": label, "cls": cls,
        # 43 站 master 導覽欄（SPEC-DAILY-UNIT Part B §3.1）：ord＝route 序、display＝公司
        # 慣用顯示名（QLIK_DISPLAY）、mcl＝PH/PC/PPC 群組維度；label＝BOSS 友善名或 None。
        # ⚠ #103：`ord` 在**每個產品裡各自編號**（Kestrel ord 18＝`segGrout`，Plover ord 18
        #   是完全不同的一站）⇒ 顯示名一定要查**那個產品自己的** display 表，拿 Plover 那張
        #   對 Kestrel 的 ord 查，會印出一個完全無關的站名（2026-09-08 臨時埠親看時抓到）。
        #   查不到 ⇒ 印 canonical id（§11.1-4「顯示名待抄錄、不捏造」）。
        "ord": ordn,
        "display": (((display_map if display_map is not None else mqk.QLIK_DISPLAY)
                     .get(ordn) or cls) if ordn else cls),
        # ★ #105 加性欄：顯示名來源（"qlik"／"derived"／查不到＝None）。唯一出處＝
        #   `mes_qlik.PRODUCTS[product]["display_src"]`（本檔不自己判斷是哪一張表給的名字）。
        "display_source": ((display_src_map or {}).get(ordn) if ordn else None),
        "mcl": (mcl_map if mcl_map is not None else _MLC).get(cls),
        "no_signal": no_signal, "has_output": st["has_output"],
        "qlik": st["qlik"], "output_withheld": st["output_withheld"],
        # Layer-1（output 真；target/gap/cause/overtime demo — 皆源自 oee.station_model）
        "output": output, "target": st["target"], "gap": st["gap"],
        "gap_pct": st["gap_pct"], "met": st["met"], "cause": st["cause"],
        "overtime_h": st["overtime_h"], "actual_rate_uph": st.get("actual_rate_uph"),
        # Layer-2 OEE 三因子（util=A×U 真；A/U 個別拆分 demo；quality RAW v0）
        "availability": st["availability"], "utilization": st["utilization"],
        "quality": st["quality"], "quality_pct": st["quality_pct"], "oee": st["oee"],
        # WIP / 加班（demo；無訊號站四欄 None）
        "wip": wip, "wip_target": wip_target, "wip_short": wip_short, "ot_wip_h": ot_wip_h,
        # 昨日時間帳本歸因（真值；`SPEC-DAILY-ATTRIB.md`）。led=None＝該源無帳本、
        # led["unmeasurable"]＝結構性量不到（顯「—」＋ note，**絕不填 0**＝T6）。
        "led": led,
        "led_chips": ledger_chips(led, met=st["met"]),
        # 提案 C：未解釋不進 chips，改走中性「推定」註記行（**只在無尺 fallback 路徑渲染**）。
        "led_presume": ledger_presume(led),
        "led_unmeasurable": bool(led and led.get("unmeasurable")),
        "led_note": (led or {}).get("note"),
        # 顆數帳（真值；`SPEC-DAILY-UNIT`）。pcs=None＝該源/該站無板 → 顆數欄「—」。
        # 兩本帳**永不相加**（Part 0 鐵則 5）——本列只並排、不合成任何跨帳總數。
        "pcs": pcs,
        "pcs_chips": pcs_chips(pcs),
        "pcs_note": (pcs or {}).get("note"),
        # 提案 H：單一尺 top-3 藥丸（顆；已排除未解釋與負值）——模板直接吃這個。
        # **`led_chips`/`pcs_chips` 刻意保留**＝顆數尺不可用（首暖/報廢腿缺/無時間源）時
        # 的降級 fallback：模板 `r.ranked` 空就退回兩帳 chips（今日行為零改）。
        "ranked": (pcs or {}).get("ranked") or [],
    }
    # ★ 退役的第二把尺（wattr `est`＝`r_y` 換算、**而且含未解釋**）在**有尺列**一律禁顯
    #   （SPEC-DAILY-UNIT Part C §C3：est 不得再出現在損失語句裡）。同一列並排兩把尺＝
    #   三個互相矛盾的顆數；無尺列則保留它——那是那列唯一講得出口的換算。
    r["ruled"] = isinstance((pcs or {}).get("loss_by"), dict) or bool(r["ranked"])
    r["led_est_show"] = bool(led and led.get("est")) and not r["ruled"]
    # 提案 F：一句人話的材料（需要 met/gap/led/pcs ⇒ 併好 dict 之後才算得出來）。
    # target_real=False＝今天的呼叫端恆定值（DEMO target 不入句；見 sentence_ctx 鐵則 2）。
    r["sent"] = sentence_ctx(r, target_real=False)
    return r


def boss_model(sb, yb, window_sec=86400, demo=True, qlik_grid=None, qlik_on=False,
               attrib=None, unit=None, product="plover"):
    """組『昨日戰報』model：**43 站 route 序 rows**（SPEC-DAILY-UNIT Part 0；恆全列、不藏站）
       + hero 單一結論 + 線級 OEE（口徑＝43 站設備可測子集）。
       sb/yb＝board_data.get_scoreboard / get_yield_scoreboard 的 canonical。
       sb 或 yb 為 None → 對應欄誠實 None（不拋、不重查、不猜）。
       **qlik_on=False（預設）＝raw 路徑不變**；qlik_on=True（D2）：站級產出/良率列改吃
       Qlik grid（cls 同名 join 43 列），grid 不可用/無對映 → 該列「—」（絕不拿 raw 頂替）。
       **attrib=None（預設）＝無帳本行為**；給了 `board_data.get_attrib_day` 的板
       （`board.attrib.v1`）就把昨日時間帳本掛上逐列（`led`/`led_chips`）＋ hero 的
       「昨日全線損失 top 原因」＝SPEC-DAILY-ATTRIB §5。
       **unit=None（預設）＝顆數帳欄「—」**；給了 `board_data.get_unit_day` 的板
       （`board.unit.v1`）就逐列掛 `pcs`/`pcs_chips` ＋ hero 跨兩帳排序（`hero_top`）。
       兩本帳**永不相加**（Part 0 鐵則 5）。
       **`product`（#103／D3-b；加性、預設 `"plover"` ⇒ 逐位元同舊版）**：`"kestrel"` ⇒
       站列換成 `KESTREL_DAILY_STATIONS`（45），**且時間帳／顆數帳兩腿一律關掉**
       （`attrib`／`unit` 忽略）。理由是口徑不是省事：那兩本帳的分母是**設備視角、
       不分產品**的機時（`CONCEPT.md` §14.4），把 Kestrel 的 PH 掛上整台機的停機與
       生產機時＝憑空發明第三把尺。Kestrel 這一頁上只有 Qlik 那一腿是真的，
       其餘一律「—」（C-MR3：量不到就是量不到，不是 0）。"""
    product = product if product in PRODUCT_STATIONS else "plover"
    stations_master = PRODUCT_STATIONS[product]
    # 顯示名／mcl 一律查**那個產品自己的**表（`ord` 在每個產品裡各自編號；見 `_row`）
    _prof = (getattr(mqk, "PRODUCTS", {}) or {}).get(product) or {}
    disp_map = _prof.get("display") if product != "plover" else mqk.QLIK_DISPLAY
    mcl_map = ({c: m for _o, c, m in (_prof.get("stations") or ())}
               if product != "plover" else _MLC)
    disp_src_map = _prof.get("display_src") or {}      # #105（缺席 ⇒ 全 None、頁面零註記）
    if product != "plover":
        attrib, unit = None, None       # ★ 見 docstring：兩本帳是設備視角、不分產品
    umap = {s.get("cls"): s for s in (sb.get("stations") if sb else [])}
    ymap = {s.get("cls"): s for s in (yb.get("stations") if yb else [])}
    qmap = {r.get("cls"): r for r in ((qlik_grid or {}).get("stations") or [])
            if not (qlik_grid or {}).get("unavailable")}
    # 歸因板不可用（snapshot／模組缺／live 失敗）→ lmap 空 ⇒ 每列 led=None ⇒ 帳本欄「—」＋
    # 頁面顯降級說明；**絕不退回舊的 DEMO cause 假裝是真歸因**（那正是 C8 要移出的東西）。
    att_ok = bool(attrib) and not attrib.get("unavailable") and bool(attrib.get("rows"))
    lmap = {r.get("cls"): r for r in ((attrib or {}).get("rows") or [])} if att_ok else {}
    # 顆數帳板（同款誠實降級：不可用 ⇒ pmap 空 ⇒ 每列 pcs=None、零 chips、hero 退時間帳）。
    unit_ok = bool(unit) and not unit.get("unavailable") and bool(unit.get("rows"))
    pmap = {r.get("cls"): r for r in ((unit or {}).get("rows") or [])} if unit_ok else {}

    rows = [_row(label, cls, umap.get(cls), ymap.get(cls), window_sec, demo,
                 qlik_row=qmap.get(cls), qlik_on=qlik_on, led=lmap.get(cls),
                 ordn=o, pcs=pmap.get(cls), display_map=disp_map, mcl_map=mcl_map,
                 display_src_map=disp_src_map)
            for o, cls, label in stations_master]  # 恆依該產品的 Qlik route 序（不藏站）

    targeted = [r for r in rows if r["met"] is not None]
    behind = [r for r in targeted if r["met"] is False]
    worst = min(behind, key=lambda r: r["gap"]) if behind else None
    hero = {
        "n_on": sum(1 for r in targeted if r["met"]),
        "n_targeted": len(targeted),
        "n_behind": len(behind),
        "n_nosignal": sum(1 for r in rows if r["no_signal"]),
        "worst": ({"label": worst["label"], "cls": worst["cls"], "gap": worst["gap"]}
                  if worst else None),
        "total_ot_wip_h": sum(r["ot_wip_h"] for r in rows if r["ot_wip_h"] is not None),
        # 「昨日全線損失 top 原因」一行（SPEC §5）：直接掛 attrib 的線級聚合（只統計可測站）。
        # worst_cause＝(bucket, h)；worst_cause_label＝該桶最痛的那一站（給人一個可以去問的對象）。
        "attrib": ((attrib or {}).get("hero") if att_ok else None),
        "worst_cause": (((attrib or {}).get("hero") or {}).get("top") or [None])[0]
                       if att_ok else None,
        "worst_cause_station": _label_of((((attrib or {}).get("hero") or {})
                                          .get("worst") or {}).get("cls")) if att_ok else None,
        # 顆數帳線級聚合＋跨兩本帳 top（Part B §4.2；純函式、selftest 釘排序鍵）。
        "unit": ((unit or {}).get("hero") if unit_ok else None),
    }
    # 既有跨兩帳 top（雙卡）＝**降級 fallback、原封保留**：模板先看 `hero.loss`（新單一尺
    # 卡），沒有才走 `hero_top`/`hero_top_cause` 這條今日路徑（byte-可比）。
    hero["hero_top"] = hero_top_cause(hero["attrib"], hero["unit"])

    # ── 提案 H 線級單一尺卡（ROADMAP §2.1；單位全部＝顆、同一把 r_good_base 尺）──────
    #    unit 板不可用 ⇒ None ⇒ 模板整卡退回 hero_top 雙卡（今日行為）。
    #    ★ `unexplained_h` 取引擎的 **`unexplained_ruled_h`**（只 Σ 有尺的列）＝與同一行
    #      的 `unexplained`（顆）同一組站；取時間帳全站 Σ 會讓推定行左右兩個數字來自
    #      不同站集（群 B fix ③）。全站未解釋機時在時間帳區塊仍照顯示。
    _uh = hero["unit"] if unit_ok else None
    hero["loss"] = ({
        "ranked": _uh.get("loss_ranked"),
        "actionable": _uh.get("loss_actionable"),
        "unexplained": _uh.get("loss_unexplained"),
        "unexplained_h": _uh.get("unexplained_ruled_h"),
        "worst": _uh.get("loss_worst"),
        "slow_gain": _uh.get("slow_gain_pcs"),
        "n_ruled": _uh.get("n_ruled"),
        "n_no_ruler": _uh.get("n_no_ruler"),
    } if _uh else None)
    hero["sent_line"] = line_sentence_ctx(_uh, hero["attrib"]) if _uh else None

    # 線級 OEE：對 43∩可測站子集呼 oee.production_model（設備視角 A/U 可 Σ、§14.4；
    # scope 文案＝`daily_oee_scope`「43 站產線——設備可測子集」）。sb 缺 → line=None（誠實）。
    line = None
    if sb and umap:
        _seq = [c for _o, c, _l in stations_master]
        sub_sb = {"stations": [umap[c] for c in _seq if c in umap]}
        sub_yb = {"stations": [ymap[c] for c in _seq if c in ymap],
                  "rollup": (yb.get("rollup") if yb else {})} if yb else None
        pm = oee.production_model(sub_sb, sub_yb, window_sec, demo,
                                  qlik_rows=qmap, qlik_kpis=(qlik_grid or {}).get("kpis"),
                                  qlik_on=qlik_on)
        line = pm["line"] if pm else None

    return {
        "rows": rows, "hero": hero, "line": line,
        # Part B §10.1 模板讀 `m.hero_top` ⇒ 頂層 alias（同一份物件、非複本）。
        "hero_top": hero["hero_top"],
        "qlik": bool(qlik_on),
        # 昨日時間帳本歸因（真值）：整板掛上供 hero/drill/Pareto 消費；不可用時
        # `attrib_ok=False` ＋ `attrib_reason` ＝頁面顯降級說明（不出任何桶）。
        "attrib": (attrib if att_ok else None),
        "attrib_ok": att_ok,
        "attrib_reason": (None if att_ok else (attrib or {}).get("unavailable") or "not_wired"),
        "pareto30": ((attrib or {}).get("pareto30") if att_ok else None),
        # 顆數帳板（真值；`board.unit.v1`）：30d 顆數榜＝面板 B（與時間榜**永不混排**＝Part B §6）。
        "unit": (unit if unit_ok else None),
        "unit_ok": unit_ok,
        "unit_reason": (None if unit_ok else (unit or {}).get("unavailable") or "not_wired"),
        "pareto30_units": ((unit or {}).get("pareto30") if unit_ok else None),
        "window_sec": window_sec, "demo": demo,
        # C8 DEMO 記帳：`cause`（OEE waterfall）本來就靠 DEMO target 反推 ⇒ 併入 demo_fields；
        # **時間帳本四桶＝真值、不在此列**（那是 T4「不依賴 target」的落地點）。
        # 顆數帳三桶＝真值（Qlik/重工計數/自身基準估算）——顆數格**永不掛 DEMO 徽章**（§7）。
        "demo_fields": list(oee.DEMO_FIELDS) + ["wip", "wip_target", "wip_short", "ot_wip_h",
                                                "cause"],
        "real_fields": ["led", "led_chips", "pcs", "pcs_chips"],
    }


def _label_of(cls):
    """cls → 老闆顯示名（不在 20 站表內→原樣回 cls；None→None）。"""
    if not cls:
        return None
    for label, c in BOSS_STATIONS:
        if c == cls:
            return label
    return cls


# ── i18n（lead 會 merge 進 app.I18N；前端鐵則：英文預設、中文可切）──
# daily_flow_hdr／daily_rows_hdr 已退役（SPEC-DAILY-UNIT Part B §8.1：兩表合併成 43 站總表）；
# run_smoke /daily body 標記改抓 `dl_tbx_hdr`/`dl_map_hdr` 的新獨特字串（§11；B2d 同步）。
I18N_DAILY = {
    "en": {
        "nav_daily": "Daily",
        "daily_sub": "Yesterday — did every station deliver?",
        "daily_win_pfx": "Window",
        "daily_window_note": "Yesterday = the production day 08:00–08:00 (Taipei; 00:00–07:59 belongs to the previous production day).",
        "q_daily_window": "The window is yesterday's production day — 08:00 to 08:00 in Taipei wall-clock time (the local machine clock, which is the same timezone as the MES clock); 00:00–07:59 belongs to the previous production day. Not a rolling 24 hours.",
        "daily_range_unsupported": "Snapshot is a pre-aggregated roll-up and cannot be re-cut to a production day — the figures below cover the snapshot's own window, not strictly yesterday 08:00–08:00.",
        "daily_nosignal": "no signal in window",
        # 休息日橫幅（2026-08-27 Chris 拍板；SPEC-DAILY-UNIT Part D）：資料一個位元都不動，
        # 只把「整板空白」從**看起來像壞掉**變成**看得懂的空**。live 才顯、日曆說不出來就不顯。
        "dl_restday": "Yesterday ({d}) was a non-production day — blank rows are expected.",
        "dl_restday_some": "{n} station(s) still logged production time.",
        # ── 43 站合併總表＋mini-map＋兩本帳（SPEC-DAILY-UNIT Part B §8.1 全表）──
        "dl_tbx_hdr": "Station table — did it deliver, and why not",
        "dl_tbx_hint": "Click a row for the full breakdown; the station name opens the "
                       "station drill.",
        "dl_map_hdr": "Line map — 43 stations in route order",
        "dl_grp_ph": "Module assembly (ord 1–26)",
        "dl_grp_pc": "Unit assembly (ord 27–38)",
        "dl_grp_ppc": "Packaged unit (ord 39–43)",
        "dl_col_causes": "Why — time · PH",
        "dl_pcs_unit": "PH",
        "dl_no_eq": "Not an equipment-axis station — no machine timeline, so no time ledger.",
        "dl_detail_open": "full breakdown",
        # 提案 C 用詞（2026-08-02）：headline 一律「未使用產能」語系，不再說「損失」
        # ——「未解釋佔比極大」被講成損失，老闆一問「你損失了四倍目標的量？」整頁信任就沒了。
        "dl_hero_time_top": "Top time-account cause",
        "dl_hero_pcs_top": "Top PH-account cause",
        "dl_hero_unranked": "plus {h} machine-hours that cannot be converted to PH "
                            "(no rate) — listed in the table, excluded from this ranking.",
        "dl_rank_note": "Both accounts are ranked in estimated PH; time-account causes "
                        "convert at each station's own PH per machine-hour (≈), "
                        "PH-account causes are native counts. The two accounts are "
                        "never added together.",
        "dl_pareto_time_sub": "Time account (machine-hours)",
        "dl_pareto_pcs_sub": "PH account (PH)",
        # ── 總表圖例（2026-08-02 Chris 回饋：legend 掛表尾＋借用 Pareto 面板標題字＝
        #    讀者以為在講 Pareto。改掛表頭、自帶標題、逐桶人話 tooltip）──
        "dl_leg_hdr": "How to read the Why / time columns",
        "dl_leg_time": "time bar & solid chips — machine-hours",
        "dl_leg_pcs": "dashed chips — PH",
        "dl_leg_scrap_q": ("PH whose first FAIL landed at this station yesterday "
                           "(Qlik caliber — the same ruler as /yield)."),
        "dl_leg_rework_q": ("The same PH retested at this station — every extra attempt "
                            "eats one PH of capacity."),
        "dl_leg_slow_q": ("Producing, but below this station's own 7-day baseline rate — "
                          "≈ estimated PH not made while running. Shows “—” until enough "
                          "days are stored (baseline still warming)."),
        # 2026-08-03 verify F6：舊文案只說「非零才出現」＝把藥丸講成逐桶清單，但提案 H
        # 之後藥丸是**所有原因換算成顆後的單一排序前三**（會蓋掉排第四的桶）。
        "dl_leg_note": ("Chips are the top three causes once converted to PH — they "
                        "appear only when non-zero; expand any row for the exact figure of "
                        "every bucket."),
        "dl_pareto_pcs_none": "No PH-account daily files yet — this panel appears once "
                              "live days accumulate.",
        "dl_pareto_scope": "Days stored before the 43-station change cover only the "
                           "original 20 stations.",
        # 換尺過渡期揭露（提案 E）：v3 之前的日帳只有嘗試軸的慢速數字，兩把尺不同。
        # ★ 粒度＝**站×日**（2026-08-03 群 D fix ⑥）：按「日」算會漏掉「同一天裡只有部分站
        #   落新尺」的混用（單日單站的板更是永遠不亮），而榜是逐站加總的。
        "dl_pareto_mixed_ruler": ("Slow uses the new good-rate ruler for {k} of {n} "
                                  "station-days; the rest still carry the attempt-rate "
                                  "figure (ruler-transition disclosure)."),
        "c_wip": "WIP",
        "q_wip": "Work-in-progress on hand vs a buffer target. The inventory source is not wired yet, so both figures are DEMO synthetic values seeded per station.",
        "c_ot_wip": "OT to refill WIP",
        "q_ot_wip": "Estimated overtime to refill the WIP shortfall = shortfall ÷ actual station rate (real) ÷ DEMO manpower coverage.",
        "daily_oee_scope": "43-station line — equipment-measurable subset",
        "daily_to_overview": "Period analysis → Overview",
        "daily_map_hint": "Hover a station to see the underlying MES class (cls).",
        # ── 昨日缺口時間帳本歸因（SPEC-DAILY-ATTRIB §5）；桶名/口徑句住 board_attrib.I18N_ATTRIB ──
        "dl_led_col": "Where the time went",
        # 提案 C：合計/headline 一律「Unused」；純時間帳內部欄的佔比句同步改「unused time」。
        "dl_lost": "Unused",
        "dl_mh": "machine-hours",
        "dl_mh_short": "mh",
        # 前導空白刻意保留：模板寫成 `{{ … }}%{{ t.dl_of_lost }}` ⇒ 少了它會渲染成
        # 「62%of the unused time」（ZH 以全形「／」開頭、天生不需要）。
        "dl_of_lost": " of the unused time",
        "dl_hero_cause": "Top actionable cause yesterday",
        "dl_hero_worst_at": "worst at",
        "dl_led_unavail": "The time ledger is not available for this data source "
                          "(it needs live per-machine segments plus the WIP queue).",
        "dl_led_none": "no ledger",
        "dl_led_open": "time ledger",
        # G2（`SPEC-PROD-CALENDAR`）：只在真的有停線時數時才渲染 ⇒ 沒有日曆＝**零文案**。
        # C-PC10：講「production calendar」，**永遠不講 schedule/shift**。
        "dl_cal_src": "from the production calendar generated {ts}",
        "dl_total_mh": "Machine-hours in the day",
        "dl_wo_states": "Work-order hours by state",
        "dl_wo_states_q": "Per the Equipment page: hours are summed per state, so the sum "
                          "can exceed the union when two orders overlap. Only the union "
                          "enters the ledger.",
        # §6.4：常駐句只講「分母是什麼」；換軸告示拆成 dl_rw_switch（到期收版位）
        "dl_rw_axis": ("Ledger denominator = expected run window (working hours + measured "
                       "overtime - plant closures) per kun-catalog machine."),
        "dl_rw_switch": ("Since 2026-08-23 the ledger denominator is the expected run window, "
                         "not 24h x fleet."),
        "dl_rw_inert": ("Ledger denominator temporarily 24h x fleet (old basis) - production "
                        "calendar unavailable or this lane is fully synthetic."),
        "dl_rw_na": ("{n} machine(s) outside the kun catalog are excluded from the ledger "
                     "(not applicable; {h}h of their work-order downtime disclosed here, "
                     "not booked)."),
        "dl_unassigned": "{n} machine(s) with work orders could not be placed on any of the "
                         "20 station rosters (unknown to the equipment catalogue, or listed "
                         "at more than one station) — their downtime is not in this ledger.",
        "dl_wo_added": "{n} machine(s) with no production yesterday were added back to their "
                       "station from the equipment catalogue, so a full day under repair is "
                       "still booked.",
        "dl_wo_other": "{n} machine(s) with work orders belong to stations outside these 20 "
                       "— out of scope for this page, not a gap in the ledger.",
        "dl_pareto_hdr": "30-day cause Pareto — what is worth fixing",
        "dl_pareto_none": "No daily ledgers stored yet — the Pareto appears once live days "
                          "accumulate (one file per day, live only).",
        "dl_pareto_thin": "Small sample — treat the ranking as provisional.",
        "dl_pareto_c_station": "Station",
        "dl_pareto_c_lost": "Unused (machine-hours)",
        "dl_pareto_c_top": "Top cause",
        "dl_caliber_hdr": "How the ledger is computed",
    },
    "zh": {
        "nav_daily": "日報",
        "daily_sub": "昨天 — 每一站都達標出貨了嗎？",
        "daily_win_pfx": "生產日",
        "daily_window_note": "昨天＝生產日 08:00–08:00（台北；00:00–07:59 屬前一生產日）。",
        "q_daily_window": "此處的「昨天」＝昨天那個生產日，台北牆鐘 08:00 到 08:00（本機時鐘＝與 MES 時鐘同時區）；清晨 00:00–07:59 算前一個生產日。不是滾動的 24 小時。",
        "daily_range_unsupported": "快照為預先彙總盤、無法重切成生產日——下方數字＝快照本身的涵蓋範圍，非嚴格昨天 08:00–08:00。",
        "daily_nosignal": "本生產日無訊號",
        # 休息日橫幅（同 en 註解）。⚠ 措辭不得出現「窗」（§6.4 中央釘 `dejargon_round`）。
        "dl_restday": "昨日（{d}）為休息日（非生產日）——整板空屬預期。",
        "dl_restday_some": "仍有 {n} 站有生產紀錄。",
        # ── 43 站合併總表＋mini-map＋兩本帳（SPEC-DAILY-UNIT Part B §8.1 全表）──
        "dl_tbx_hdr": "逐站總表 — 達標了嗎、沒達標為什麼",
        "dl_tbx_hint": "點列展開完整明細；點站名進站別下鑽。",
        "dl_map_hdr": "產線圖 — 43 站依 route 序",
        "dl_grp_ph": "模組組裝（ord 1–26）",
        "dl_grp_pc": "單元組裝（ord 27–38）",
        "dl_grp_ppc": "包裝出貨（ord 39–43）",
        "dl_col_causes": "原因 — 時間帳 · PH 帳",
        "dl_pcs_unit": "PH",
        "dl_no_eq": "非設備軸站——沒有機台 timeline、無時間帳本。",
        "dl_detail_open": "完整明細",
        # 提案 C 用詞（同 en 註解）：headline 一律「未使用產能」語系、不再說「損失」。
        "dl_hero_time_top": "時間帳最大原因",
        "dl_hero_pcs_top": "PH 帳最大原因",
        "dl_hero_unranked": "另有 {h} 機時無法換算 PH 數（無速率）——表中照列、不參與此排序。",
        "dl_rank_note": "兩本帳以估算 PH 數共同排序；時間帳原因用各站自身「每機台小時產能」換算"
                        "（≈）、PH 帳原因為原生計數。兩帳永不相加。",
        "dl_pareto_time_sub": "時間帳（機台小時）",
        "dl_pareto_pcs_sub": "PH 帳（PH）",
        # ── 總表圖例（同 en 註解；表頭自帶標題、逐桶人話）──
        "dl_leg_hdr": "「原因／時間分帳」兩欄怎麼讀",
        "dl_leg_time": "時間分帳條與實線藥丸——機時",
        "dl_leg_pcs": "虛線藥丸——PH",
        "dl_leg_scrap_q": "昨日 first-FAIL 落在本站的 PH 數（Qlik 口徑——與 /yield 同一把尺）。",
        "dl_leg_rework_q": "同一個 PH 在本站重測——每多跑一次就吃掉一個 PH 的產能。",
        "dl_leg_slow_q": ("有在生產、但低於本站自身 7 日基準速率——≈ 估算邊跑邊少做的 PH 數。"
                          "日帳天數不足時顯「—」（基準暖機中）。"),
        # 同 en 註解（verify F6）：藥丸＝換算成顆後的**單一排序前三**，不是逐桶清單。
        "dl_leg_note": "藥丸＝各原因換算成 PH 後的前三大，且只在非零時出現；"
                       "展開任一列可見每個桶的精確數。",
        "dl_pareto_pcs_none": "尚未累積 PH 帳日檔——live 日子累積後此面板才會出現。",
        "dl_pareto_scope": "43 站改版前落地的日帳只涵蓋原 20 站。",
        # 換尺過渡期揭露（同 en 註解；粒度＝站×日）
        "dl_pareto_mixed_ruler": "慢速有 {k}／{n} 個站日已用良品尺；其餘仍為換尺前的"
                                 "嘗試軸數字（過渡期揭露）。",
        "c_wip": "在製",
        "q_wip": "在製量（WIP）與緩衝目標對比。在製盤點源尚未接通，故兩個數字皆為 per-站 確定性 DEMO 合成值。",
        "c_ot_wip": "補在製加班",
        "q_ot_wip": "補回在製缺口所需加班＝缺口 ÷ 實際站速率（真）÷ DEMO 人力覆蓋係數。",
        "daily_oee_scope": "43 站產線——設備可測子集",
        "daily_to_overview": "期間分析 → 總覽",
        "daily_map_hint": "游標移到站名可看底層 MES class（cls）。",
        # ── 昨日缺口時間帳本歸因（SPEC-DAILY-ATTRIB §5）；桶名/口徑句住 board_attrib.I18N_ATTRIB ──
        "dl_led_col": "時間去哪了",
        # 提案 C（同 en 註解）：合計/headline 改「未使用」；佔比句改「／未使用時間」。
        "dl_lost": "未使用",
        "dl_mh": "機台小時",
        "dl_mh_short": "機時",
        "dl_of_lost": "／未使用時間",
        "dl_hero_cause": "昨日最大可行動原因",
        "dl_hero_worst_at": "最痛的站",
        "dl_led_unavail": "此資料源算不出時間帳本（需要 live 的逐機台區間＋WIP 佇列）。",
        "dl_led_none": "無帳本",
        "dl_led_open": "時間帳本",
        # C-PC10：講「生產日曆」，永不講「班表／排班」。
        "dl_cal_src": "來源＝{ts} 產出的生產日曆",
        "dl_total_mh": "當日機台小時",
        "dl_wo_states": "工單時數分態",
        "dl_wo_states_q": "與設備頁同口徑：分態時數各自加總，兩張單重疊時 Σ 分態可大於聯集；"
                          "只有聯集進帳本。",
        # §6.4：中文顯示層一律「應生產時段」；換軸告示拆出去、到期自動收版位
        "dl_rw_axis": "帳本分母＝應生產時段（正常班＋實測加班延長−全廠停線；只計綑名冊機台）。",
        "dl_rw_switch": "2026-08-23 起：帳本分母從「24h × 機隊」改成「應生產時段」。",
        "dl_rw_inert": "帳本分母暫為 24h × 機隊（舊口徑）——生產日曆缺席、或本 lane 為全合成。",
        "dl_rw_na": "{n} 台非綑名冊機台不進帳本（不適用；其工單停機 {h}h 於此揭露、不入桶）。",
        "dl_unassigned": "有 {n} 台開了工單的機台放不進這 20 站的任一名冊（設備目錄查無、"
                         "或同時掛在多站）——它們的停機不在本帳本內。",
        "dl_wo_added": "有 {n} 台昨日零生產的機台已由設備目錄歸回原站——整天在修也照樣入帳。",
        "dl_wo_other": "有 {n} 台開了工單的機台屬於這 20 站以外的站——本頁不管它，不是帳本破洞。",
        "dl_pareto_hdr": "30 天原因 Pareto — 值得投資修哪一件",
        "dl_pareto_none": "尚未累積日帳——Pareto 會在 live 日子累積後出現（一天一檔、僅 live 落地）。",
        "dl_pareto_thin": "小樣本——排名先當參考。",
        "dl_pareto_c_station": "站別",
        "dl_pareto_c_lost": "未使用（機台小時）",
        "dl_pareto_c_top": "最大原因",
        "dl_caliber_hdr": "帳本怎麼算的",
    },
}


# ── selftest（不連 MES；純算術＋合成 fixture）──
def _selftest():
    import re
    ok = 0

    def _a(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    # ① 20 cls 全 ∈ EQP_STATIONS
    for _n, c in BOSS_STATIONS:
        _a(c in _EQP, f"{c} 不在 EQP_STATIONS")
    _a(len(BOSS_STATIONS) == 20, "BOSS_STATIONS 應 20 站")

    # ①b 43 站 master（SPEC-DAILY-UNIT §1.1/§1.2）：QLIK join、交集守衛
    _a(len(DAILY_STATIONS) == 43, "DAILY_STATIONS 應 43 站")
    _a([c for _o, c, _l in DAILY_STATIONS] == [c for _o, c, _m in mqk.QLIK_STATIONS],
       "DAILY_STATIONS cls 序需逐位等於 QLIK_STATIONS（站序唯一權威、不另抄）")
    _a(all(l is None or (l, c) in [(n, cc) for n, cc in BOSS_STATIONS]
           for _o, c, l in DAILY_STATIONS), "label 只能是 BOSS 友善名或 None")
    _a(sum(1 for _o, _c, l in DAILY_STATIONS if l is not None) == 20,
       "43 站中恰 20 站帶 BOSS 友善名")
    _a(all(c in _EQP for c in ATTRIB_STATIONS), "ATTRIB_STATIONS ⊂ EQP_STATIONS（時間帳站集）")
    _a(all(c in set(ATTRIB_STATIONS) for _n, c in BOSS_STATIONS),
       "BOSS 20 全在 43∩EQP 交集內")

    # ② rows 順序恆等 DAILY_STATIONS（43 站 route 序、恆全列不藏站）
    fake_sb = {"stations": [
        {"cls": "segCalibrationTest", "fleet_size": 3,
         "producing_sec": 40000.0, "active_span_sec": 70000.0},
        {"cls": "segCleanTest", "fleet_size": 4,
         "producing_sec": 20000.0, "active_span_sec": 60000.0},
        {"cls": "segFrameBonding", "fleet_size": 2,
         "producing_sec": 0.0, "active_span_sec": 0.0},   # 無訊號站
    ]}
    fake_yb = {"stations": [
        {"cls": "segCalibrationTest", "output_units": 900,
         "final_pass_pct": 98.0, "has_yield_signal": True},
        {"cls": "segCleanTest", "output_units": 500,
         "fpy_raw_pct": 88.0, "has_yield_signal": True},
    ], "rollup": {"anchor_cls": "segCalibrationTest"}}
    m = boss_model(fake_sb, fake_yb, window_sec=86400, demo=True)
    _a([r["cls"] for r in m["rows"]] == [c for _o, c, _l in DAILY_STATIONS],
       "rows 順序需恆等 DAILY_STATIONS（QLIK 43 站 route 序）")
    _a(len(m["rows"]) == 43, "rows 應 43 列")
    _a([r["ord"] for r in m["rows"]] == list(range(1, 44)), "ord 應 1..43 升冪")
    _a({r["mcl"] for r in m["rows"]} == {"assyModule", "assyUnit",
                                         "assyPackagedUnit"}, "mcl 三群組齊")
    _a({r["cls"]: r for r in m["rows"]}["segDiePlacement"]["label"] == "Die Placement"
       and {r["cls"]: r for r in m["rows"]}["segWireBondingInspection"]["label"] is None,
       "BOSS 站帶友善名、非 BOSS 站 label=None（渲染層退 display）")
    _a({r["cls"]: r for r in m["rows"]}["segDiePlacement"]["display"] == "Die Placement"
       and {r["cls"]: r for r in m["rows"]}["segCalibrationTest"]["display"]
       == "Unit Calibration Test", "display＝QLIK_DISPLAY")

    # ③ demo_wip 確定性（同 cls 兩呼同值）＋量級錨
    _a(demo_wip("segCalibrationTest", 900) == demo_wip("segCalibrationTest", 900),
       "demo_wip 需確定性")
    _a(demo_wip_target("segCleanTest", 500) == demo_wip_target("segCleanTest", 500),
       "demo_wip_target 需確定性")
    _a(demo_wip("segCalibrationTest", 900) is not None, "有 output 站 WIP 非 None")
    _a(demo_wip("segFrameBonding", 0) is None, "無 output → WIP None")

    # ④ 合成 sb/yb：無訊號站四欄 None、未達標站有 cause、ot_wip_h 非負、CAL/CLN 有 output
    rmap = {r["cls"]: r for r in m["rows"]}
    frame = rmap["segFrameBonding"]
    _a(frame["no_signal"] is True, "Frame 應無訊號")
    for f in ("wip", "wip_target", "wip_short", "ot_wip_h"):
        _a(frame[f] is None, f"無訊號站 {f} 應 None")
    _a(frame["met"] is None, "無訊號站無達標判定")
    cal = rmap["segCalibrationTest"]
    _a(cal["output"] == 900, "CAL output 應 900")
    _a(cal["met"] is not None, "CAL 有 output → 有達標判定")
    for r in m["rows"]:
        _a(r["ot_wip_h"] is None or r["ot_wip_h"] >= 0, "ot_wip_h 不可為負")
        if r["met"] is False:
            _a(r["cause"] is not None, "未達標站應有 cause 歸因")
    _a(m["hero"]["n_targeted"] >= 1, "hero n_targeted 應 >=1")
    _a(m["hero"]["n_nosignal"] >= 1, "hero n_nosignal 應 >=1（Frame）")
    _a(m["hero"]["total_ot_wip_h"] >= 0, "total_ot_wip_h 非負")
    _a(m["line"] is not None, "有 sb → line 非 None")
    _a("availability" in m["line"] and "oee" in m["line"], "line 應含 OEE 因子")

    # sb=None 不炸：所有列無訊號、line=None、hero 誠實
    mn = boss_model(None, None, window_sec=86400, demo=True)
    _a(len(mn["rows"]) == 43, "sb=None 仍回 43 列")
    _a(all(r["no_signal"] for r in mn["rows"]), "sb=None → 全列無訊號")
    _a(mn["line"] is None, "sb=None → line None")
    _a(mn["hero"]["n_targeted"] == 0, "sb=None → 無達標判定")

    # yb=None（有 sb 無良率）：output 空、target None、不炸
    my_ = boss_model(fake_sb, None, window_sec=86400, demo=True)
    _a(all(r["target"] is None for r in my_["rows"]), "yb=None → target 全 None")

    # ④b Qlik 口徑換源（D2；additive、預設路徑不變）
    _a(all(r["qlik"] is False for r in m["rows"]), "qlik_on 預設 False（20 列全走舊 raw 路徑）")
    qgrid = {"schema": "board.qlik_grid.v1",
             "stations": [{"ord": 32, "cls": "segCalibrationTest", "total": 696,
                           "pass": 690, "fail": 6, "final_yield_pct": 99.1379310344}],
             "kpis": {"OVERALL": {"pct": 77.713}}}
    mq = boss_model(fake_sb, fake_yb, window_sec=86400, demo=True,
                    qlik_grid=qgrid, qlik_on=True)
    qmap_ = {r["cls"]: r for r in mq["rows"]}
    _a(qmap_["segCalibrationTest"]["output"] == 696,
       "qlik_on → CAL 產出＝Qlik TOTAL 696（非 raw 900）")
    _a(abs(qmap_["segCalibrationTest"]["quality_pct"] - 99.1379310344) < 1e-9,
       "qlik_on → CAL 良率＝Qlik final_yield")
    _a(qmap_["segCleanTest"]["output"] is None
       and qmap_["segCleanTest"]["output_withheld"] is True,
       "qlik_on + 無 Qlik 對映列 → 產出誠實留白（非 raw 500、非 0）")
    _a(qmap_["segCleanTest"]["target"] is None and qmap_["segCleanTest"]["wip"] is None,
       "留白列不生 demo 目標/WIP（不對留白數字疊假目標）")
    _a(abs(mq["line"]["quality"] - 0.77713) < 1e-9, "daily 線級 Quality＝OVERALL RTY")
    mq0 = boss_model(fake_sb, fake_yb, window_sec=86400, demo=True,
                     qlik_grid={"unavailable": "snapshot_missing"}, qlik_on=True)
    _a(all(r["output"] is None for r in mq0["rows"]),
       "grid 不可用 → 20 列產出全留白（D8 誠實降級、絕不回退 raw）")
    _a(mq0["line"] is None or mq0["line"]["quality"] is None,
       "grid 不可用 → 線級 Quality None")

    # ④c 昨日時間帳本歸因（SPEC-DAILY-ATTRIB §5 接線；attrib=None 時 byte-identical）
    _a(all(r["led"] is None and r["led_chips"] == [] for r in m["rows"]),
       "attrib=None（預設）→ 每列 led None、零 chips＝舊行為")
    _a(m["attrib_ok"] is False and m["attrib"] is None and m["pareto30"] is None,
       "attrib=None → attrib_ok False、pareto30 None")
    _a("cause" in m["demo_fields"] and "led" in m["real_fields"],
       "C8 DEMO 記帳：cause 屬 demo（靠 DEMO target 反推）、led 屬真值")

    ab = wattr.demo_board()                       # demo 板走真引擎（全桶型 + 不可測 + degraded）
    ma = boss_model(fake_sb, fake_yb, window_sec=86400, demo=True, attrib=ab)
    amap = {r["cls"]: r for r in ma["rows"]}
    _a(ma["attrib_ok"] is True and ma["hero"]["attrib"] is not None,
       "attrib 給了 → hero 掛線級聚合")
    _a(len(ma["rows"]) == 43, "掛帳本後仍恆 43 列")
    _a(amap["segDiePlacement"]["led"] is not None
       and amap["segDiePlacement"]["led"]["cls"] == "segDiePlacement", "帳本 join 逐列對得上 cls")
    _a(amap["segLamination"]["led"] is None,
       "demo 帳本沒有的站 → led None（不捏造 0）")
    # T6：不可測站四桶 None、絕不 0，且**永不出 chips**
    zl = amap["segPotting"]
    _a(zl["led_unmeasurable"] is True and zl["led_note"] == "zero_len"
       and zl["led"]["starved_h"] is None and zl["led"]["wo_union_h"] is None,
       "零工期站帳本各桶 None（T6：量不到顯「—」、絕不 0）")
    _a(zl["led_chips"] == [], "不可測站永不出 chips")
    _a(amap["segQASetup"]["led_note"] == "nosig", "無訊號站 note＝nosig（與 zero_len 分開）")
    # T1 恆等式：每個可測站都要平（≤1e-6h）
    for r in ma["rows"]:
        if r["led"] and not r["led"]["unmeasurable"]:
            _a(wattr.identity_error(r["led"]) <= 1e-6,
               f"{r['cls']} 帳本恆等式殘差需 ≤1e-6h")
    # C8 雙軌 gate：損失大的站即使 met 為 None（無 target）也要看得到 chips
    da = amap["segDiePlacement"]
    _a(da["led_chips"] and da["led_chips"][0]["bucket"] == "wo",
       "工單主導站 → 第一個 chip＝工單桶（雙軌 gate：不靠 DEMO target 才顯示）")
    _a(all(c["h"] > 0 for r in ma["rows"] for c in r["led_chips"]), "chips 不出零時數桶")
    _a(all(0 <= (c["share"] or 0) <= 1.0000001
           for r in ma["rows"] for c in r["led_chips"]), "chip share 落 [0,1]")
    # ★ 群 D fix ②：未解釋**永不進 chips**（驗收第②關 + 提案 C）；它改走中性推定註記行。
    _a(all(c["bucket"] not in CHIP_EXCLUDED_BUCKETS
           for r in ma["rows"] for c in r["led_chips"]),
       "T-C1：ledger_chips 桶集永不含 unexplained（推定不是損失、更不該登頂）")
    _a(CHIP_EXCLUDED_BUCKETS == ("unexplained",), "排除集就這一個桶（改了要重議）")
    _unex_led = {"unmeasurable": False, "lost_h": 100.0,
                 "unexplained_h": 90.0,
                 "top": [("unexplained", 90.0), ("wo", 8.0), ("starved", 2.0)],
                 "est": {"unexplained": 1800, "wo": 160, "starved": 40,
                         "lost_total": 2000}}
    _uc = ledger_chips(_unex_led, met=False)
    _a([c["bucket"] for c in _uc] == ["wo", "starved"],
       "未解釋被跳過後 cap 仍取滿 2 個（不是把名額浪費掉）")
    # 中性「推定」註記行＝未解釋的獨立揭露（鏡像 hero presume 行）
    _pr = ledger_presume(_unex_led)
    _a(_pr == {"h": 90.0, "est": 1800}, "ledger_presume 回 (機時, ≈顆)")
    _a(ledger_presume({"unmeasurable": True, "unexplained_h": 5.0}) is None
       and ledger_presume({"unmeasurable": False, "unexplained_h": 0.0}) is None
       and ledger_presume({"unmeasurable": False, "unexplained_h": None}) is None
       and ledger_presume(None) is None,
       "不可測／零／量不到／無帳本 ⇒ 不出推定行（絕不填 0）")
    _a(ledger_presume({"unmeasurable": False, "unexplained_h": 3.0, "est": None})
       == {"h": 3.0, "est": None}, "est 缺 ⇒ 只講機時（不猜顆數）")
    _a(all(r["led_presume"] is None or r["led_presume"]["h"] > 0 for r in ma["rows"]),
       "逐列 led_presume 恆為 None 或正時數")
    _a(any(r["led_presume"] for r in ma["rows"]),
       "demo 帳本至少一列有推定行（否則上一條是空集恆真）")
    _a(all(r["ruled"] is False and r["led_est_show"] is bool(r["led"]
                                                             and r["led"].get("est"))
           for r in ma["rows"]),
       "unit 板缺 ⇒ 全列無尺 ⇒ est 顯示閘＝舊行為（降級 byte-可比）")
    # T3：殘差桶標籤只有一份、住 board_attrib（本檔不得另造措辭）
    _a("manpower" in wattr.I18N_ATTRIB["en"]["at_b_unexplained"]
       and "人力" in wattr.I18N_ATTRIB["zh"]["at_b_unexplained"],
       "殘差標籤＝『未解釋（可能人力/排班/其他）』、來源唯一")
    for _l in ("en", "zh"):
        _a(not any(k.startswith("at_b_") and k != "at_b_unexplained"
                   and ("manpower" in wattr.I18N_ATTRIB[_l][k]
                        or "人力" in wattr.I18N_ATTRIB[_l][k])
                   for k in wattr.I18N_ATTRIB[_l]),
           "T3：除殘差外沒有任何桶被標成人力")
    # C9：20 站全在 43 route 序內（載入時已 assert，這裡再釘一次＝改名必紅）
    _a(wattr.assert_route_coverage([c for _n, c in BOSS_STATIONS], "BOSS_STATIONS"),
       "C9：BOSS 20 站全在 43 站 route 序內")
    _a(wattr.downstream_of("segVacuumPackInspect") is None,
       "線尾站無下游（下游堵桶結構上恆 0）")
    # T7 §24：接線後的列/hero 一樣零人員欄
    _bad = [k for r in ma["rows"] for k in (r.get("led") or {})
            if any(tok in str(k).lower() for tok in ("person", "operator", "name", "staff"))]
    _a(not _bad, f"T7：帳本列不得出現人員欄（{_bad}）")

    # ④d 歸因板不可用（snapshot／模組缺）→ 誠實降級、不退回假歸因
    mu = boss_model(fake_sb, fake_yb, window_sec=86400, demo=True,
                    attrib={"schema": "board.attrib.v1", "unavailable": "snapshot_unsupported",
                            "rows": []})
    _a(mu["attrib_ok"] is False and mu["attrib_reason"] == "snapshot_unsupported",
       "attrib unavailable → 帶原因、不假裝有帳本")
    _a(all(r["led"] is None for r in mu["rows"]), "attrib unavailable → 每列 led None")

    # ④e 顆數帳板接線（SPEC-DAILY-UNIT；unit=None 時顆數欄「—」、零 chips）
    _a(all(r["pcs"] is None and r["pcs_chips"] == [] for r in m["rows"]),
       "unit=None（預設）→ 每列 pcs None、零顆數 chips")
    _a(m["unit_ok"] is False and m["unit"] is None and m["pareto30_units"] is None,
       "unit=None → unit_ok False、顆數榜 None")
    _a("pcs" in m["real_fields"] and "pcs_chips" in m["real_fields"],
       "顆數帳＝真值（real_fields；顆數格永不掛 DEMO 徽章）")
    ub = wunit.demo_board()                        # demo 板走真引擎（全桶型＋partial＋degraded）
    mp = boss_model(fake_sb, fake_yb, window_sec=86400, demo=True, attrib=ab, unit=ub)
    pmap_ = {r["cls"]: r for r in mp["rows"]}
    _a(mp["unit_ok"] is True and mp["hero"]["unit"] is not None,
       "unit 給了 → hero 掛顆數線級聚合")
    _a(pmap_["segDiePlacement"]["pcs"] is not None
       and pmap_["segDiePlacement"]["pcs"]["cls"] == "segDiePlacement",
       "顆數板 join 逐列對得上 cls")
    _a(pmap_["segFrameBonding"]["pcs_chips"]
       and pmap_["segFrameBonding"]["pcs_chips"][0]["bucket"] == "scrap"
       and pmap_["segFrameBonding"]["pcs_chips"][0]["n"] == 40,
       "報廢大戶站第一個顆數 chip＝scrap 40")
    _a(mp["pareto30_units"] is not None, "unit 板帶 pareto30 → 顆數榜掛上")
    muu = boss_model(fake_sb, fake_yb, window_sec=86400, demo=True,
                     unit={"schema": "board.unit.v1", "unavailable": "snapshot_unsupported",
                           "rows": []})
    _a(muu["unit_ok"] is False and muu["unit_reason"] == "snapshot_unsupported"
       and all(r["pcs"] is None for r in muu["rows"]),
       "unit unavailable → 帶原因、每列 pcs None（不假裝有顆數帳）")

    # ④f pcs_chips gate（Part B §3.4）：非零即顯、cap 2、n 降冪、slow 恆帶 ≈、None 不出
    urow_full = {"scrap_n": 5, "rework_n": 12, "slow_est_n": 8}
    ch = pcs_chips(urow_full)
    _a(len(ch) == 2 and ch[0]["bucket"] == "rework" and ch[1]["bucket"] == "slow",
       "pcs_chips cap 2、依 n 降冪（12>8>5 → rework, slow）")
    _a(ch[1]["approx"] is True and ch[0]["approx"] is False,
       "slow 恆帶 ≈（approx=True）、精確桶 approx=False")
    _a(pcs_chips({"scrap_n": 0, "rework_n": None, "slow_est_n": 3})
       == [{"bucket": "slow", "n": 3, "approx": True}],
       "0 不出（floor）、None 不出（T6 量不到≠0）")
    _a(pcs_chips(None) == [], "pcs=None → 零 chips")

    # ④g hero_top_cause（Part B §4.2）：排序鍵三層＋unranked 揭露＋降級 byte-可比
    ah_ = {"top": [("wo", 5.0), ("starved", 2.0)], "est": {"wo": 85, "starved": 30},
           "worst": {"cls": "segCalibrationTest", "bucket": "wo", "h": 4.0}}
    ph_ = {"top": [("scrap", 85), ("rework", 12)],
           "worst": {"cls": "segDiePlacement", "bucket": "scrap", "n": 40}}
    ht = hero_top_cause(ah_, ph_)
    _a(ht["top"]["acct"] == "pcs" and ht["top"]["bucket"] == "scrap",
       "tie 85 顆：精確（scrap）勝過估算（wo est）＝第二層排序鍵")
    _a(ht["alt"] is not None and ht["alt"]["acct"] == "time",
       "次行恆列另一本帳的第一名")
    _a(ht["top"]["worst_cls"] == "segDiePlacement", "worst 站跟著勝出帳的 hero.worst")
    _a(set(ht) == {"top", "alt", "unranked_mh", "n_candidates"},
       "回傳形固定——沒有任何跨帳相加的總數欄（反模式釘死）")
    ht2 = hero_top_cause(ah_, None)
    _a(ht2["top"]["acct"] == "time" and ht2["top"]["approx"] is True,
       "pcs 板缺 → 退回時間帳排序（降級 byte-可比）、est 換算恆帶 ≈")
    ht3 = hero_top_cause({"top": [("wo", 5.0)], "est": None,
                          "worst": {"cls": "x", "bucket": "wo", "h": 5.0}}, ph_)
    _a(ht3["unranked_mh"] == 5.0 and ht3["top"]["acct"] == "pcs",
       "est 缺 → 時間桶退出排序但 unranked_mh 揭露（絕不靜默消失）")
    _a(hero_top_cause(None, None) == {"top": None, "alt": None, "unranked_mh": None,
                                      "n_candidates": 0},
       "兩帳皆缺 → 空結果不炸")
    _a(set(PCS_BUCKET_I18N_KEY) == set(wunit.UNIT_BUCKETS),
       "pkey 對映與 UNIT_BUCKETS 對齊（鍵住 board_unit＝唯一一份）")

    # ④h 提案 F 句式 v2 `sentence_ctx`（ROADMAP §2.1；全 kind 各至少一案）────────────
    def _urow(**kw):
        """合成 unit row（只帶 sentence_ctx 會讀的欄）；`ranked` 恆由 loss_by 現算。"""
        base = {"loss_by": {"wo": 100.0, "starved": 20.0, "blocked": 0.0,
                            "unexplained": 300.0, "scrap": 40.0, "rework": 10.0,
                            "slow": -5.0},
                "loss_actionable": 170.0, "loss_unexplained": 300.0,
                "unexplained_h": 12.34}
        base.update(kw)
        base["ranked"] = wunit.ranked_losses({"loss_by": base["loss_by"]})
        return base

    _DFLT = object()                       # 哨兵：`led=None`（無帳本）與「用預設」必須分得開

    def _srow(pcs, met=None, gap=None, led=_DFLT):
        return {"pcs": pcs, "met": met, "gap": gap,
                "led": ({"unmeasurable": False} if led is _DFLT else led),
                # ↓ 兩個 DEMO 欄故意放進來：句式**永不**碰它們（鐵則 1，下方負斷言）
                "wip": 999, "wip_target": 1234, "ot_wip_h": 7.5, "overtime_h": 3.0}

    SENT_KEYS = {"kind", "gap", "tot", "causes", "unexp_h", "unexp_n"}
    sp = sentence_ctx(_srow(_urow()))
    _a(set(sp) == SENT_KEYS, "sentence_ctx 回傳形固定＝六鍵（模板契約）")
    _a(sp["kind"] == "plain" and sp["tot"] == 170,
       "kind=plain（target 不真、可行動未使用產能 ≥1 顆）、tot＝round(loss_actionable)")
    _a(sp["causes"] == [("wo", 100), ("scrap", 40), ("starved", 20)],
       "causes＝ranked 前三、(桶, 顆整數)、依顆降冪")
    _a(all(b != "unexplained" for b, _n in sp["causes"]),
       "提案 C：未解釋永不進 causes（它有自己的推定子句）")
    _a(all(b != "slow" for b, _n in sp["causes"]), "負值桶（比基準快）不進 causes")
    _a(sp["unexp_h"] == 12.3 and sp["unexp_n"] == 300,
       "推定子句材料＝(unexplained_h 一位小數, round(loss_unexplained))")
    # ★ 鐵則 1：WIP/OT 永不入句 ／ 鐵則 2：target_real=False ⇒ gap 恆 None、kind 永不 full/short
    _a(sp["gap"] is None, "target_real=False ⇒ gap 恆 None（DEMO target 不入句）")
    _a(all(b in wunit.LOSS_BUCKETS for b, _n in sp["causes"]),
       "causes 的桶名只能是七個 canonical 損失桶（WIP/OT 不是桶、進不來）")
    _a(not any(tok in str(sp).lower() for tok in ("wip", "overtime", "ot_", "999", "1234")),
       "T-F1：WIP/加班（DEMO 合成值）不得出現在句式材料的任何角落")
    sp_t = sentence_ctx(_srow(_urow(), met=False, gap=-120.0))
    _a(sp_t["kind"] == "plain" and sp_t["gap"] is None,
       "T-F2：met=False 但 target_real=False ⇒ 仍是 plain、gap 仍 None（釘死）")
    sp_t2 = sentence_ctx(_srow(_urow(), met=True))
    _a(sp_t2["kind"] == "plain", "T-F2：met=True 且 target_real=False ⇒ 仍是 plain")
    # target_real=True（SALTMARSH 真目標源接通那天）：full/short 兩式才啟用
    sf = sentence_ctx(_srow(_urow(), met=False, gap=-120.0), target_real=True)
    _a(sf["kind"] == "full" and sf["gap"] == 120,
       "target_real=True ∧ met=False → full、gap＝|gap| 整數")
    ss = sentence_ctx(_srow(_urow(), met=True), target_real=True)
    _a(ss["kind"] == "short" and ss["gap"] is None, "target_real=True ∧ met → short")
    # clean：可行動未使用產能不足 1 顆
    _zero = {b: 0.0 for b in wunit.LOSS_BUCKETS}
    sc = sentence_ctx(_srow(_urow(loss_by=dict(_zero, unexplained=300.0),
                                  loss_actionable=0.0)))
    _a(sc["kind"] == "clean" and sc["causes"] == [], "kind=clean（可行動 <1 顆、無 causes）")
    _a(sc["unexp_n"] == 300, "clean 仍帶推定子句材料（未解釋照樣現形）")
    # clean 退化：合計 ≥1 顆但沒有單桶達 floor ⇒ 退 clean（不出「≈2 顆：。」空冒號）
    sc2 = sentence_ctx(_srow(_urow(loss_by=dict(_zero, wo=0.4, scrap=0.4, rework=0.4),
                                   loss_actionable=1.2)))
    _a(sc2["kind"] == "clean" and sc2["causes"] == [],
       "合計 ≥1 但無單桶達 display floor → 退 clean（避免空冒號句）")
    # noruler：無尺、但時間軸可測
    sn = sentence_ctx(_srow(_urow(loss_by=None, loss_actionable=None,
                                  loss_unexplained=None, unexplained_h=None)))
    _a(sn["kind"] == "noruler" and sn["tot"] is None and sn["causes"] == [],
       "kind=noruler（loss_by 缺、時間軸可測）")
    # 無句：pcs 缺 ／ 無尺且時間軸本來就量不到
    _a(sentence_ctx(_srow(None)) is None, "pcs 缺 → 無句（None）")
    _a(sentence_ctx({"pcs": None}) is None, "整列無資料 → 無句")
    _a(sentence_ctx(_srow(_urow(loss_by=None), led={"unmeasurable": True})) is None,
       "無尺且時間軸不可測 → 無句（那句話是廢話；原因交給 led_note）")
    _a(sentence_ctx(_srow(_urow(loss_by=None), led=None)) is None,
       "無尺且無時間帳本 → 無句")
    # 推定子句：任一腿缺 ⇒ 兩者皆 None（絕不半句、絕不填 0）
    sh = sentence_ctx(_srow(_urow(unexplained_h=None)))
    _a(sh["unexp_h"] is None and sh["unexp_n"] is None,
       "unexplained_h 缺 → 推定子句整段不出（不填 0）")
    sh2 = sentence_ctx(_srow(_urow(loss_unexplained=None)))
    _a(sh2["unexp_h"] is None and sh2["unexp_n"] is None, "顆側缺 → 同樣整段不出")
    # 句式鍵齊（文案唯一一份住 board_unit；本檔不另造措辭）
    for _l in ("en", "zh"):
        for _k in ("un_sent_plain", "un_sent_met_clean", "un_sent_noruler",
                   "un_sent_unexp", "un_sent_full", "un_sent_short"):
            _a(_k in wunit.I18N_UNIT[_l], f"句式鍵 {_k} 需在 wunit.I18N_UNIT[{_l}]")
    _a("On target" not in wunit.I18N_UNIT["en"]["un_sent_met_clean"]
       and "已達標" not in wunit.I18N_UNIT["zh"]["un_sent_met_clean"],
       "clean 文案不得帶達標語意（target 仍是 DEMO、達標由 ✓/✕ 講）")
    _a("{gap}" not in wunit.I18N_UNIT["en"]["un_sent_plain"]
       and "{gap}" not in wunit.I18N_UNIT["zh"]["un_sent_plain"],
       "un_sent_plain 不得帶 {gap} 佔位")

    # ④i line_sentence_ctx（線級；恆 target_real=False ⇒ 永不 full/short）
    _lh = {"loss_by": {"wo": 500.0, "starved": 200.0, "blocked": 0.0, "unexplained": 900.0,
                       "scrap": 300.0, "rework": 100.0, "slow": 50.0},
           "loss_actionable": 1150.0, "loss_unexplained": 900.0}
    _lh["loss_ranked"] = wunit.ranked_losses({"loss_by": _lh["loss_by"]},
                                             max_n=len(wunit.ACTIONABLE_BUCKETS))
    _lh["unexplained_ruled_h"] = 44.5
    sl = line_sentence_ctx(_lh, {"by_bucket": {"unexplained": 999.9}})
    _a(sl["kind"] == "plain" and sl["tot"] == 1150, "線級句＝plain、tot＝線級可行動合計")
    _a(len(sl["causes"]) == 3 and sl["causes"][0] == ("wo", 500),
       "線級 loss_ranked 是全六桶 → 句子仍 cap 3（可讀性）")
    # ★ 群 B fix ③：兩腿同範圍——h 取 unit hero 的 `unexplained_ruled_h`（只有尺的列），
    #   **不再**取時間帳線級 by_bucket（全部可測站）。傳進去的 999.9 必須被忽略。
    _a(sl["unexp_h"] == 44.5 and sl["unexp_n"] == 900,
       "線級推定子句兩腿同範圍：h＝unexplained_ruled_h、n＝loss_unexplained")
    _a(line_sentence_ctx(dict(_lh, unexplained_ruled_h=None),
                         {"by_bucket": {"unexplained": 44.5}})["unexp_h"] is None,
       "T-B3：unexplained_ruled_h 缺 ⇒ 整個推定子句不出（絕不回頭吃時間帳全站值）")
    _a(line_sentence_ctx({"loss_by": None}, None)["kind"] == "noruler",
       "線級無尺 → noruler")
    _a(line_sentence_ctx(None, None) is not None, "unit hero 空 dict 化 → 仍回 noruler 不炸")

    # ④j `_row` 接線（sent / ranked）＋ hero 單一尺卡的降級鏈
    _a(all("sent" in r and "ranked" in r for r in mp["rows"]),
       "每列都帶 sent（可為 None）與 ranked（恆 list）")
    _a(all(isinstance(r["ranked"], list) for r in mp["rows"]), "ranked 恆 list、絕不 None")
    _a(all(r["ranked"] == ((r["pcs"] or {}).get("ranked") or []) for r in mp["rows"]),
       "ranked 逐列＝引擎 ranked（本檔不重排、不加工）")
    _a(any(r["sent"] and r["sent"]["kind"] == "plain" for r in mp["rows"]),
       "demo 板至少一列 kind=plain")
    _a(all(not r["sent"] or r["sent"]["kind"] not in ("full", "short") for r in mp["rows"]),
       "★ 呼叫端恆 target_real=False ⇒ 全板永不出現 full/short（DEMO target 不入句）")
    _a(all(not r["sent"] or r["sent"]["gap"] is None for r in mp["rows"]),
       "★ 全板 gap 恆 None")
    _a(all(r["sent"] is None for r in m["rows"]),
       "unit=None（顆數帳不可用）→ 每列 sent None（模板退回兩帳 chips）")
    _a(all(r["ranked"] == [] for r in m["rows"]), "unit=None → ranked 全空")
    # 降級 fallback 保留：led_chips/pcs_chips 行為零改
    _a(any(r["led_chips"] for r in mp["rows"]) and any(r["pcs_chips"] for r in mp["rows"]),
       "新句式上線後 led_chips/pcs_chips 仍照舊產出（降級 fallback 不得被拿掉）")
    # ★ 群 D fix ①：退役的第二把尺（wattr est、且合計含未解釋）在**有尺列**禁顯
    _a(all(("ruled" in r and "led_est_show" in r) for r in mp["rows"]),
       "每列都帶 ruled/led_est_show（模板的 est 顯示閘）")
    _a(all(not r["led_est_show"] for r in mp["rows"] if r["ruled"]),
       "T-C2：有尺列一律不顯 wattr est（一列兩把尺＝三個互相矛盾的顆數）")
    _a(all(r["led_est_show"] == bool(r["led"] and r["led"].get("est"))
           for r in mp["rows"] if not r["ruled"]),
       "無尺列保留 est（那列唯一講得出口的換算、不是損失語句）")
    _a(any(r["ruled"] for r in mp["rows"]) and any(not r["ruled"] for r in mp["rows"]),
       "demo 板兩種列都要有（否則上面兩條全稱斷言是空集恆真）")
    _a(all(r["ruled"] == (bool(r["ranked"]) or isinstance((r["pcs"] or {}).get("loss_by"),
                                                          dict))
           for r in mp["rows"]), "ruled 判準＝有 ranked 或有 loss_by（模板 fallback 同一條）")
    # hero.loss
    hl = mp["hero"]["loss"]
    _a(hl is not None and set(hl) == {"ranked", "actionable", "unexplained", "unexplained_h",
                                      "worst", "slow_gain", "n_ruled", "n_no_ruler"},
       "hero.loss 形固定（單一尺卡的全部材料）")
    _a(hl["ranked"] == mp["hero"]["unit"]["loss_ranked"]
       and hl["actionable"] == mp["hero"]["unit"]["loss_actionable"],
       "hero.loss 直接掛引擎線級聚合（本檔不另算）")
    # ★ 群 B fix ③：推定行左右兩個數字必須同一組站——顆＝有尺列的 Σ、機時也取有尺列的 Σ
    #   （`unexplained_ruled_h`）。取時間帳全站 Σ 會讓 n÷h 反算不出任何一站的速率。
    _a(hl["unexplained_h"] == mp["hero"]["unit"]["unexplained_ruled_h"],
       "hero.loss.unexplained_h＝unit hero 的 unexplained_ruled_h（與顆腿同一組列）")
    _a(hl["unexplained_h"] is not None
       and hl["unexplained_h"] <= (mp["hero"]["attrib"]["by_bucket"]["unexplained"] + 1e-9),
       "有尺列的未解釋機時必 ≤ 時間帳全站 Σ（子集關係；全站值仍在時間帳區塊顯示）")
    _a(mp["hero"]["sent_line"] is not None
       and mp["hero"]["sent_line"]["kind"] in ("plain", "clean", "noruler"),
       "線級句 kind 只會是 plain/clean/noruler")
    # ★ 降級鏈：unit 不可用 ⇒ hero.loss/sent_line None，而 hero_top（今日雙卡）原封不動
    _a(m["hero"]["loss"] is None and m["hero"]["sent_line"] is None,
       "unit=None → hero.loss/sent_line 皆 None（模板退回 hero_top 雙卡）")
    _a(m["hero"]["hero_top"] == hero_top_cause(m["hero"]["attrib"], m["hero"]["unit"])
       and m["hero_top"] is m["hero"]["hero_top"],
       "hero_top 降級路徑原封保留（byte-可比、頂層 alias 仍同一物件）")
    _a(muu["hero"]["loss"] is None and muu["hero"]["sent_line"] is None,
       "unit unavailable → 同樣退回（不假裝有單一尺卡）")

    # ⑤ I18N_DAILY en/zh 鍵集合相等＋§8.1 增刪
    _a(set(I18N_DAILY["en"]) == set(I18N_DAILY["zh"]), "en/zh 鍵集合需相等")
    _a(len(I18N_DAILY["en"]) >= 14, "I18N_DAILY 應 >=14 鍵")
    for _l in ("en", "zh"):
        _a("daily_flow_hdr" not in I18N_DAILY[_l] and "daily_rows_hdr" not in I18N_DAILY[_l],
           "daily_flow_hdr/daily_rows_hdr 已退役（§8.1；run_smoke 標記改抓 dl_tbx_hdr）")
        for k in ("dl_tbx_hdr", "dl_map_hdr", "dl_col_causes", "dl_pcs_unit", "dl_no_eq",
                  "dl_hero_time_top", "dl_hero_pcs_top", "dl_hero_unranked", "dl_rank_note",
                  "dl_pareto_time_sub", "dl_pareto_pcs_sub", "dl_pareto_pcs_none",
                  "dl_pareto_scope", "dl_grp_ph", "dl_grp_pc", "dl_grp_ppc",
                  "dl_tbx_hint", "dl_detail_open"):
            _a(k in I18N_DAILY[_l], f"§8.1 新鍵 {k} 需在 {_l}")
        _a("{h}" in I18N_DAILY[_l]["dl_hero_unranked"], "dl_hero_unranked 需帶 {h} 佔位")
        _a("43" in I18N_DAILY[_l]["daily_oee_scope"], "daily_oee_scope 改 43 站口徑文案")
    _a(set(wattr.I18N_ATTRIB["en"]) == set(wattr.I18N_ATTRIB["zh"]),
       "I18N_ATTRIB en/zh 鍵集合需相等（頁面直接吃它、缺鍵＝空白格）")
    _a(set(wunit.I18N_UNIT["en"]) == set(wunit.I18N_UNIT["zh"]),
       "I18N_UNIT en/zh 鍵集合需相等（app 會 merge 進 I18N）")

    # ⑤c 提案 C 用詞（ROADMAP §2.1）：headline 一律「未使用產能」語系、不再說「損失」
    _a(I18N_DAILY["en"]["dl_lost"] == "Unused" and I18N_DAILY["zh"]["dl_lost"] == "未使用",
       "dl_lost＝Unused／未使用（不再是 Lost／損失）")
    _a("Unused" in I18N_DAILY["en"]["dl_pareto_c_lost"]
       and "未使用" in I18N_DAILY["zh"]["dl_pareto_c_lost"],
       "Pareto 欄頭同步改未使用語系")
    _a("actionable" in I18N_DAILY["en"]["dl_hero_cause"]
       and "可行動" in I18N_DAILY["zh"]["dl_hero_cause"],
       "hero 標題＝『最大可行動原因』（未解釋不在合計內、標題必須說清楚）")
    _a("unused time" in I18N_DAILY["en"]["dl_of_lost"]
       and "未使用時間" in I18N_DAILY["zh"]["dl_of_lost"],
       "時間帳內部佔比句改『／未使用時間』")
    _a(I18N_DAILY["en"]["dl_of_lost"].startswith(" "),
       "dl_of_lost 前導空白必須保留（模板 `{{ … }}%{{ t.dl_of_lost }}` 直接相接）")
    # ★ 負斷言 scope＝**頁面吃得到的三張 i18n 表**（2026-08-03 群 D fix ⑤-1）：只掃
    #   I18N_DAILY 會漏掉 `un_presumed_q`/`un_sent_unexp` 這種住在 board_unit 的對比句
    #   ——它們一樣渲染在 /daily 上，「損失」照樣出現在畫面。**無 allowlist、零豁免**：
    #   對比要用「不計入未使用產能」那一側講（en 同理，不得回到 "a loss"）。
    for _l in ("en", "zh"):
        for _tn, _tbl in (("I18N_DAILY", I18N_DAILY), ("I18N_UNIT", wunit.I18N_UNIT),
                          ("I18N_ATTRIB", wattr.I18N_ATTRIB)):
            _bad_w = [k for k, v in _tbl[_l].items()
                      if any(w in str(v) for w in ("Lost", "損失"))
                      or "loss" in str(v).lower()]
            _a(not _bad_w, f"{_tn}[{_l}] 不得再出現 Lost／loss／損失（提案 C）：{_bad_w}")
    # 單位鐵則（Chris 2026-09-02 既有口徑；2026-10-02 /daily 補齊）：zh 三張表不得再有量詞「顆」
    #   ＝數量單位一律 PH。同三張表、無 allowlist。
    for _tn, _tbl in (("I18N_DAILY", I18N_DAILY), ("I18N_UNIT", wunit.I18N_UNIT),
                      ("I18N_ATTRIB", wattr.I18N_ATTRIB)):
        _bad_k = [k for k, v in _tbl["zh"].items() if "顆" in str(v)]
        _a(not _bad_k, f"{_tn}[zh] 不得再出現「顆」（數量單位一律 PH）：{_bad_k}")
        # EN 對偶（2026-10-02）：en 三張表不得再有 pcs／pieces／units 當數量單位（一律 PH）。
        #   公開樹：名稱表把一個產品名詞的公開別名定成 unit（產品名、不是數量單位）⇒ units? 那一腿
        #   在公開樹必誤報；只驗 pcs／pieces 並印 skipped（R-P11）。私有樹照舊三腿全驗。
        _en_pat = r"\b(pcs?|pieces?|units?)\b"
        if _tn == "I18N_DAILY":
            import core_paths as _dl_wp
            _dl_public = _dl_wp.is_public_tree()
            if _dl_public:
                print("board_daily selftest: skipped: en no-'unit(s)' leg of the PH-unit check "
                      "(public tree: a product noun's public alias is 'unit'); pcs/pieces legs still checked")
        if _dl_public:
            _en_pat = r"\b(pcs?|pieces?)\b"
        _bad_e = [k for k, v in _tbl["en"].items()
                  if re.search(_en_pat, str(v), re.I)]
        _a(not _bad_e, f"{_tn}[en] 不得再出現 pcs／pieces／units（數量單位一律 PH）：{_bad_e}")
    _a(I18N_DAILY["en"]["dl_pcs_unit"] == "PH" and I18N_DAILY["zh"]["dl_pcs_unit"] == "PH",
       "dl_pcs_unit 中英皆 PH")
    # ⑤-2 群 D：tooltip/句子是純文字節點（無 markdown 渲染器）⇒ 字面 `**` 會原樣印在畫面上。
    for _l in ("en", "zh"):
        _star = [k for k, v in wunit.I18N_UNIT[_l].items() if "**" in str(v)]
        _a(not _star, f"I18N_UNIT[{_l}] 不得含字面 `**`（會原樣印在頁面上）：{_star}")
        _star2 = [k for k, v in I18N_DAILY[_l].items() if "**" in str(v)]
        _a(not _star2, f"I18N_DAILY[{_l}] 不得含字面 `**`：{_star2}")

    # ⑤d 2026-08-03 verify F4/F6：換尺過渡揭露鍵 ＋ 圖例句改講「單一排序前三」
    for _l in ("en", "zh"):
        _a("dl_pareto_mixed_ruler" in I18N_DAILY[_l],
           f"F4 換尺過渡揭露鍵需在 {_l}（模板條件式吃它）")
        _a("{k}" in I18N_DAILY[_l]["dl_pareto_mixed_ruler"]
           and "{n}" in I18N_DAILY[_l]["dl_pareto_mixed_ruler"],
           "dl_pareto_mixed_ruler 需帶 {k}/{n} 佔位（模板 replace 的契約）")
    _a("top three" in I18N_DAILY["en"]["dl_leg_note"]
       and "前三" in I18N_DAILY["zh"]["dl_leg_note"],
       "F6 圖例句需講明藥丸＝換算成顆後的**單一排序前三**（不是逐桶清單）")
    _a("non-zero" in I18N_DAILY["en"]["dl_leg_note"]
       and "非零" in I18N_DAILY["zh"]["dl_leg_note"],
       "F6 改寫後「非零才出現」這件事不得掉（Chris 08-02 走查就是問這個）")

    # ⑤b §24：顆數列/chips 零人員欄（防甲介面日後長出人員欄被無腦透傳）
    _bad_p = [k for r in mp["rows"] for k in (r.get("pcs") or {})
              if any(tok in str(k).lower() for tok in ("person", "operator", "name",
                                                       "staff", "headcount"))]
    _a(not _bad_p, f"§24：顆數列不得出現人員欄（{_bad_p}）")

    # ⑥ 凍結 mes_yield win additive path＝byte-identical 舊路徑 / 半開日曆窗（fake-cursor、離線、不連 MES）
    #    QG-1 護欄：win=None 必發原滾動窗 SQL（DATEADD/GETDATE、days 參數）；win=(s,e) 必發半開窗
    #    （無 GETDATE、`>= ?` + `< ?`、參數帶兩個 datetime、stpEndTime 過濾保留）。
    import datetime as _dt

    class _FakeCur:                     # 捕捉 (sql, params)、餵最小 fetch 結果（不執行任何真 SQL）
        def __init__(self, one=None, all_seq=None):
            self.calls = []
            self._one = one
            self._all_seq = list(all_seq or [])

        def execute(self, sql, *params):
            self.calls.append((sql, params))
            return self

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._all_seq.pop(0) if self._all_seq else []

    win = (_dt.datetime(2026, 7, 6), _dt.datetime(2026, 7, 7))
    ONE9 = (2, 0, 0, 0, 0.0, None, 0.0, 0, 0)   # fetch_station_raw 9 欄
    FPY1 = (0, 0, 0, 0, 0, 0)                    # fetch_fpy_fact_segment 6 欄（r[0]=0→回 None，但 execute 已捕捉）

    def _leg(sql):                      # 舊滾動窗指紋
        return ("DATEADD" in sql and "GETDATE" in sql)

    def _wdw(sql):                      # 半開日曆窗指紋
        return ("GETDATE" not in sql and ">= ?" in sql and "< ?" in sql)

    # fetch_station_raw
    fc = _FakeCur(one=ONE9); my.fetch_station_raw(fc, 3, "segX")
    _a(_leg(fc.calls[-1][0]) and fc.calls[-1][1] == ("segX", 3), "station_raw win=None＝舊路徑+params(cls,days)")
    fc = _FakeCur(one=ONE9); my.fetch_station_raw(fc, 3, "segX", win=win)
    _a(_wdw(fc.calls[-1][0]) and fc.calls[-1][1] == ("segX", win[0], win[1]),
       "station_raw win 半開窗+params(cls,s,e)")

    # fetch_product_split
    fc = _FakeCur(); my.fetch_product_split(fc, 3, "segX")
    _a(_leg(fc.calls[-1][0]) and fc.calls[-1][1] == ("segX", 3), "product_split win=None＝舊路徑")
    fc = _FakeCur(); my.fetch_product_split(fc, 3, "segX", win=win)
    _a(_wdw(fc.calls[-1][0]) and fc.calls[-1][1] == ("segX", win[0], win[1]), "product_split win 半開窗")

    # fetch_fpy_fact_segment（StartTime 欄；pscpk=None 早退不 execute）
    _a(my.fetch_fpy_fact_segment(_FakeCur(), 3, None) is None, "fpy pscpk=None → None（不查）")
    fc = _FakeCur(one=FPY1); my.fetch_fpy_fact_segment(fc, 3, "PSC1")
    _a(_leg(fc.calls[-1][0]) and "StartTime" in fc.calls[-1][0] and fc.calls[-1][1] == ("PSC1", 3),
       "fpy win=None＝舊路徑+params(pscpk,days)")
    fc = _FakeCur(one=FPY1); my.fetch_fpy_fact_segment(fc, 3, "PSC1", win=win)
    _a(_wdw(fc.calls[-1][0]) and fc.calls[-1][1] == ("PSC1", win[0], win[1]), "fpy win 半開窗")

    # fetch_machine_yield（Y1→Y2 兩次 execute；Y1 回一列 fleet-eid 使 Y2 可達、Y2 回空列）
    fc = _FakeCur(all_seq=[[(1, "EID-1")], []]); my.fetch_machine_yield(fc, ["EID-1"], 3, "segX")
    _a(len(fc.calls) == 2, "machine_yield 應 2 次 execute（Y1+Y2）")
    _a(_leg(fc.calls[0][0]) and "stpEndTime" in fc.calls[0][0] and fc.calls[0][1] == ("segX", 3),
       "machine_yield Y1 win=None＝舊路徑+stpEndTime 過濾保留")
    _a(_leg(fc.calls[1][0]) and fc.calls[1][1] == ("segX", 3), "machine_yield Y2 win=None＝舊路徑")
    fc = _FakeCur(all_seq=[[(1, "EID-1")], []]); my.fetch_machine_yield(fc, ["EID-1"], 3, "segX", win=win)
    _a(len(fc.calls) == 2, "machine_yield(win) 應 2 次 execute")
    _a(_wdw(fc.calls[0][0]) and "stpEndTime" in fc.calls[0][0] and fc.calls[0][1] == ("segX", win[0], win[1]),
       "machine_yield Y1 win 半開窗+stpEndTime 過濾保留")
    _a(_wdw(fc.calls[1][0]) and fc.calls[1][1] == ("segX", win[0], win[1]), "machine_yield Y2 win 半開窗")

    # 半開語意：窗尾用 `< ?`（非 `<= ?`）＝日曆日 [Y 00:00, Y+1 00:00) 不重疊次日
    fc = _FakeCur(one=ONE9); my.fetch_station_raw(fc, 3, "segX", win=win)
    _a("<= ?" not in fc.calls[-1][0], "半開窗尾＝`< ?`、非 `<= ?`")

    # ── #103（`_SPEC-QLIK-YIELD.md` §12／D3-b）：`/daily` 的產品維度 ────────────────
    _ar_by_ord = {r["ord"]: r for r in boss_model(None, None, product="kestrel")["rows"]}
    # ⚠ 這根釘 #105 之後換釘：原本釘「ord 18 印 canonical id `segGrout`」——那正是 #105
    #   要改掉的行為（Chris 2026-09-08 裁「先用推導名」）。**沒被削弱的那一半原樣保留**：
    #   ord 18 的名字絕不得等於 Plover 同 ord 的站名（拿錯表冒充的那個 bug）。
    _a(_ar_by_ord[18]["cls"] == "segGrout" and _ar_by_ord[18]["display"] == "Grouting"
       and _ar_by_ord[18]["display_source"] == "derived"
       and _ar_by_ord[3]["display"] == mqk.QLIK_DISPLAY[3]
       and _ar_by_ord[3]["display_source"] == "qlik"
       and _ar_by_ord[18]["display"] != mqk.QLIK_DISPLAY[18],
       "#103/#105 ★ 顯示名查**該產品自己的** display 表（ord 各自編號；共同站沿用 "
       "QLIK_DISPLAY＝qlik、12 個獨有站用推導名＝derived、絕不拿 Plover 同 ord 的站名冒充）")
    _a(all(r["display"] != r["cls"] for r in _ar_by_ord.values())
       and sum(1 for r in _ar_by_ord.values() if r["display_source"] == "derived") == 12
       and all(r["display_source"] == "qlik"
               for r in boss_model(None, None)["rows"] if r["ord"]),
       "#105 ★ Kestrel 45 站都印得出名字（12 站 derived）；Plover 43 站來源恆 qlik")
    _a(len(KESTREL_DAILY_STATIONS) == 45
       and [c for _o, c, _l in KESTREL_DAILY_STATIONS]
       == [c for _o, c, _m in mqk.KESTREL_STATIONS]
       and all(l is None for _o, _c, l in KESTREL_DAILY_STATIONS),
       "KESTREL_DAILY_STATIONS cls 序逐位等於 mes_qlik.KESTREL_STATIONS，且無 BOSS 友善名"
       "（那 20 個名字是對 Plover 產線講的；顯示名一律由渲染層查 mes_qlik 的 display 表）")
    _m_r = boss_model(None, None)
    _m_a = boss_model(None, None, product="kestrel")
    _a([r["cls"] for r in _m_r["rows"]] == [c for _o, c, _l in DAILY_STATIONS]
       and [r["cls"] for r in _m_a["rows"]] == [c for _o, c, _l in KESTREL_DAILY_STATIONS]
       and boss_model(None, None, product="zzz")["rows"] == _m_r["rows"],
       "#103：product=kestrel ⇒ 45 站 route 序；壞值退 plover（不炸）")
    _fake_att = {"rows": [{"cls": DAILY_STATIONS[0][1], "producing_h": 1.0}],
                 "hero": {"top": [("wo", 1.0)]}}
    _fake_un = {"rows": [{"cls": DAILY_STATIONS[0][1], "attempts": 5}], "hero": {}}
    _a(boss_model(None, None, attrib=_fake_att, unit=_fake_un,
                  product="kestrel")["hero"]["attrib"] is None
       and all(r.get("led") is None and r.get("pcs") is None
               for r in boss_model(None, None, attrib=_fake_att, unit=_fake_un,
                                   product="kestrel")["rows"]),
       "#103 ★ Kestrel 一律關掉時間帳／數量帳兩腿（那兩本帳是設備視角、不分產品——"
       "把 Kestrel 的 PH 掛上整台機的機時＝憑空發明第三把尺）")
    import json as _json_pd
    _a(_json_pd.dumps(boss_model(None, None, attrib=_fake_att, unit=_fake_un),
                      sort_keys=True, default=str)
       == _json_pd.dumps(boss_model(None, None, attrib=_fake_att, unit=_fake_un,
                                    product="plover"), sort_keys=True, default=str),
       "#103 ★ 負斷言：`product=` 預設值＝現值 ⇒ 既有呼叫路徑輸出逐位元相同")

    print(f"board_daily selftest OK — {ok} assertions passed")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows console 繁中 print 防炸（repo 慣例）
    except Exception:
        pass
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python board_daily.py --selftest")
