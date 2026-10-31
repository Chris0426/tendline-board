#!/usr/bin/env python3
"""Tendline Board — 設備 Up/Down 總表（呈現模型；2026-07-03 Chris 需求：老闆開會最想知道
「那台停很久的設備現在到底能不能生產」）。

═══ 口徑（Chris 拍板）═══════════════════════════════════════════════════════
UP   ＝ 現在可生產：正在跑（producing）或 閒置待命（idle-ready）都算。
DOWN ＝ 現在不可生產：維修中 / 保養中。
UNKNOWN ＝ 訊號不足 —— **沉默絕不猜 DOWN**（沒訊號≠故障，gap≠fault §10.2/§23；
           例：下班關機也會沒訊號）。UNKNOWN 的正路＝the corporate chat platform 問工程師（§24 補問）。

═══ 裁決階梯（正面訊號優先、由強到弱；Tendline 套件證據綜合）═══════════════════════
  1. 作業員機上按鈕回報故障（on-box）      → DOWN·維修   ——人的確認蓋過一切
  2. cmms 有 open 維修/保養工單            → DOWN·維修/保養
  3. MES 近期有生產紀錄（≤MES_RECENT_H）   → UP·生產中
  4. 心跳活著 + 無 open 工單 + 暫無生產    → UP·閒置待命（可生產、等工作）
  5. 以上皆無                              → UNKNOWN（the corporate chat platform 補問，不猜）
本階梯 = 唯一真裁決邏輯（`classify()`）；demo 與 live 都走它、只是證據來源不同。

═══ 兩種證據來源（"管道逐一打通、逐源換真"）═════════════════════════════════
  demo（source=demo）＝**全模擬**：per-EID 確定性合成四源證據（頁面掛「全模擬」大牌）。
  live/snapshot      ＝**MES 證據真值**：由凍結 mes_timeline.fetch_segments 在短回看窗
                       取每台最近一段生產活動、算距今 age（`build_board_live`）；age≤2h→
                       UP·生產、age>2h/窗內無段→UNKNOWN（MES 沉默**不推 DOWN**）。
                       **cmms 工單源（規則 2）＝2026-07-12 已接真**：caller（app）以 A3 LIST
                       + `board_eqmeeting._now_block` 的 S12 分階段判定產出「有計停機工單→DOWN」，
                       再把 `wo={"kind","id"}`/`now_state`/`wo_since_h` 附進 ev（本模組不重寫 S12、
                       不 import eqmeeting＝避免與其循環相依；wo 有值→`classify` 規則 2 觸發＝階梯本體不動）。
                       **按鈕/心跳兩源在 live 仍未接通**（待 on-box 部署／jumpbox）→ 在 live
                       **不是模擬值、而是「未接通」**。cmms env 未配置時 wo 亦不傳（wo_wired=False）＝
                       退回「只有 MES」的誠實舊行為（UP·生產 或 UNKNOWN）。
本模組不連 MES、不碰凍結核心；live 證據由 board_data / app 供給（它們才擁有 MES／cmms 存取）。
"""
import random

# verdict → (§23 canonical state key（借色）, up/down/unknown 粗桶)
VERDICTS = {
    "up_producing": ("生產", "up"),
    "up_idle":      ("待機", "up"),
    "down_repair":  ("故障停機", "down"),
    "down_pm":      ("計畫保養", "down"),
    "unknown":      ("不明", "unknown"),
}

# MES「近期生產」門檻（小時）：最近一段生產距今 ≤ 此值 → UP·生產（規則 3）。
MES_RECENT_H = 2.0


def classify(ev):
    """唯一真裁決邏輯（純函式、可 selftest）。ev＝證據 dict：
       {"operator": "reported"|None, "wo": {"kind":..}|None,
        "mes_last_h": float|None, "heartbeat": "alive"|"none"|None}。
       缺席（None）＝該源無訊號（live 下未接通的源即 None）→ 該規則不觸發。
       回 VERDICTS 的 verdict key。**沉默（全 None）→ unknown，絕不猜 DOWN。**"""
    if ev.get("operator") == "reported":            # 規則 1：人的確認蓋過一切
        return "down_repair"
    wo = ev.get("wo")
    if wo:                                           # 規則 2：cmms open 工單
        return "down_pm" if wo.get("kind") == "PM" else "down_repair"
    mes_h = ev.get("mes_last_h")
    if mes_h is not None and mes_h <= MES_RECENT_H:  # 規則 3：MES 近期生產
        return "up_producing"
    if ev.get("heartbeat") == "alive":               # 規則 4：心跳活著、可用待命
        return "up_idle"
    return "unknown"                                 # 規則 5：訊號不足、不猜


def _seed(s):
    return sum(ord(c) for c in (s or "x"))


def _row(eid, desc, cls, ev, since_h, live):
    """由證據 ev 走 classify → 組單機 row（demo/live 共用；verdict 一律由 classify 決定）。"""
    v = classify(ev)
    state, bucket = VERDICTS[v]
    return {"eid": eid, "desc": desc, "cls": cls,
            "verdict": v, "state": state, "bucket": bucket,
            "since_h": since_h, "ev": ev, "live": live}


def _machine_row(eid, desc, cls):
    """單機 **demo** 模擬（確定性、可重現）：先抽情境、合成**與情境一致**的四源證據，
       再一律過 `classify`（＝真裁決邏輯）得 verdict。合成證據對每情境都會 classify 回自身
       （selftest 驗證），故 demo verdict 分佈不變、且證明「裁決階梯是真的」。"""
    rnd = random.Random(_seed(eid) * 31 + _seed(cls) * 7 + 11)
    r = rnd.random()
    if r < 0.55:
        scenario = "up_producing"
    elif r < 0.80:
        scenario = "up_idle"
    elif r < 0.87:
        scenario = "down_repair"
    elif r < 0.93:
        scenario = "down_pm"
    else:
        scenario = "unknown"

    wo = hb = op = None
    if scenario == "up_producing":
        mes_h = rnd.uniform(0.05, 1.5)          # 最近一段生產距今（小時，≤門檻）
        hb = "alive"
        since_h = rnd.uniform(0.3, 8)           # 此狀態持續
    elif scenario == "up_idle":
        mes_h = rnd.uniform(2.5, 10)
        hb = "alive"
        since_h = mes_h
    elif scenario == "down_repair":
        mes_h = rnd.uniform(6, 70)
        since_h = rnd.uniform(2, max(3.0, mes_h * 0.8))
        wo = {"kind": "REACTIVE", "id": f"WO-{rnd.randint(41000, 48999)}"}
        hb = "alive" if rnd.random() < 0.8 else "none"
        op = "reported" if rnd.random() < 0.5 else None
    elif scenario == "down_pm":
        mes_h = rnd.uniform(2, 24)
        since_h = rnd.uniform(0.5, 8)
        wo = {"kind": "PM", "id": f"WO-{rnd.randint(51000, 55999)}"}
        hb = "alive"
    else:                                        # unknown：沒有任何正面訊號 → 不猜
        mes_h = rnd.uniform(8, 40)
        hb = "none"
        since_h = None

    # Deterministic R&D showcase: any-segment may be newer, while `mes_last_h` stays the
    # production-grade age used by classify. Most demo rows remain ordinary Production.
    is_rnd = (scenario != "unknown" and _seed(eid) % 19 == 0)
    mes_any_h = max(0.02, mes_h - rnd.uniform(0.2, min(2.0, mes_h))) if is_rnd else mes_h
    ev = {"mes_last_h": mes_h, "mes_any_last_h": mes_any_h,
          "mes_last_designation": "Engineering (R&D)" if is_rnd else "Production",
          "mes_meta": {"desig_available": True},
          "wo": wo, "heartbeat": hb, "operator": op}
    return _row(eid, desc, cls, ev, since_h, live=False)


def _live_row(eid, desc, cls, mes_last_h, mes_any_last_h=None, mes_last_designation=None,
              mes_meta=None, wo=None, wo_since_h=None, now_state=None):
    """單機 **live/snapshot** row：MES 最近生產 age（mes_last_h，可為 None＝窗內無段）＋
       cmms 工單源（wo，2026-07-12 接真；caller 以 S12 判定產出、None＝無計停機工單或 env 未接通）。
       wo 有值→classify 規則 2 觸發＝DOWN（階梯本體不動）；wo=None→落回 MES 規則（UP·生產/UNKNOWN）。
       按鈕/心跳兩源未接通（None）。since_h＝wo_since_h（僅 WO 判 DOWN 者有值＝S12 分階段的連續停機
       run 起點距今；MES-only 的 UP/UNKNOWN 列仍 None、不虛構）。now_state＝當前 cmms 精確態
       （維修/計畫保養/等料(停機)…；供顯示層 chip，無 wo→None）。"""
    ev = {"mes_last_h": mes_last_h,
          "mes_any_last_h": mes_last_h if mes_any_last_h is None else mes_any_last_h,
          "mes_last_designation": mes_last_designation,
          "mes_meta": mes_meta or {"desig_available": False},
          "wo": wo, "heartbeat": None, "operator": None}
    row = _row(eid, desc, cls, ev, since_h=wo_since_h, live=True)
    row["now_state"] = now_state
    return row


_ORDER = {"down": 0, "unknown": 1, "up": 2}


def _finalize(rows, mode, wo_wired=False):
    """共用彙總：排序（老闆優先序 DOWN→UNKNOWN→UP、停/靜最久在前）+ 計數 + 停最久 callout。
       mode ∈ {"sim","live"}：驅動頁面文案（sim＝全模擬大牌；live＝MES 真、其餘未接通）。
       wo_wired（live 專用）＝cmms 工單源是否接通（env 配置 + LIST 成功）→ 驅動摘要卡/證據欄
       顯真值 vs「未接通」；接通後 live 亦可能有 DOWN 列（規則 2）。"""
    if mode == "live":
        def key(r):
            a = r["ev"].get("mes_last_h")
            b = r["bucket"]
            if b == "up":                                # UP：最新生產在前（age 小 → 前）
                return (_ORDER["up"], a if a is not None else 0.0)
            if b == "down":                              # DOWN（工單源接通後才有）：停最久在前
                return (_ORDER["down"], -(r["since_h"] or 0.0))
            # UNKNOWN：最久靜默在前（無訊號=最靜默 → 最前）
            return (_ORDER["unknown"], -(a if a is not None else 1e12))
        rows.sort(key=key)
    else:
        rows.sort(key=lambda r: (_ORDER[r["bucket"]], -(r["since_h"] or 9999)))
    n_up = sum(1 for r in rows if r["bucket"] == "up")
    n_down = sum(1 for r in rows if r["bucket"] == "down")
    n_unknown = sum(1 for r in rows if r["bucket"] == "unknown")
    downs = [r for r in rows if r["bucket"] == "down" and r["since_h"]]
    longest = max(downs, key=lambda r: r["since_h"], default=None)
    return {"rows": rows, "n_up": n_up, "n_down": n_down, "n_unknown": n_unknown,
            "n_total": len(rows), "longest_down": longest, "wo_wired": wo_wired,
            "mode": mode, "simulated": (mode == "sim")}


def build_board(station_machines):
    """組全線 Up/Down board（**demo/全模擬**）。station_machines = [(cls, [(eid, desc), …]), …]
       （呼叫端提供，機台 EID 一律來自既有 canonical 管道——不在此捏造）。"""
    rows = []
    for cls, machines in station_machines:
        for eid, desc in machines:
            rows.append(_machine_row(eid, desc, cls))
    return _finalize(rows, "sim", wo_wired=False)


def build_board_live(station_evidence, wo_wired=False):
    """組全線 Up/Down board（**live/snapshot**：MES 真證據；cmms 工單源 opt-in 接真）。
       station_evidence = [(cls, [(eid, desc, ev_dict), …]), …]，ev_dict 至少含 "mes_last_h"；
       工單源接通時（wo_wired=True）caller 另附 additive 鍵 "wo"/"wo_since_h"/"now_state"
       （由 app 以 S12 判定預先算好——本模組不 import eqmeeting、不重寫判定）。
       （EID 皆真、不捏造；由 board_data 從凍結 fetch_fleet/fetch_segments 產生。）"""
    rows = []
    for cls, machines in station_evidence:
        for eid, desc, ev in machines:
            ev = ev or {}
            rows.append(_live_row(eid, desc, cls, ev.get("mes_last_h"),
                                  mes_any_last_h=ev.get("mes_any_last_h"),
                                  mes_last_designation=ev.get("mes_last_designation"),
                                  mes_meta=ev.get("mes_meta"),
                                  wo=ev.get("wo"), wo_since_h=ev.get("wo_since_h"),
                                  now_state=ev.get("now_state")))
    return _finalize(rows, "live", wo_wired=wo_wired)


# ---------------------------------------------------------------------------
# 離線 selftest（純函式、不連 MES、不 import app）——證明裁決階梯真、demo 過階梯回自身。
# ---------------------------------------------------------------------------
def _selftest():
    fails = []

    def t(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # 1) classify 五規則
    t("cls-operator", classify({"operator": "reported", "wo": None,
                                 "mes_last_h": 0.1, "heartbeat": "alive"}) == "down_repair")
    t("cls-wo-reactive", classify({"operator": None, "wo": {"kind": "REACTIVE"},
                                    "mes_last_h": 0.1, "heartbeat": "alive"}) == "down_repair")
    t("cls-wo-pm", classify({"operator": None, "wo": {"kind": "PM"},
                             "mes_last_h": None, "heartbeat": "alive"}) == "down_pm")
    t("cls-mes-recent", classify({"operator": None, "wo": None,
                                  "mes_last_h": 1.0, "heartbeat": None}) == "up_producing")
    t("cls-mes-boundary", classify({"mes_last_h": MES_RECENT_H}) == "up_producing")
    t("cls-heartbeat", classify({"operator": None, "wo": None,
                                 "mes_last_h": 9.0, "heartbeat": "alive"}) == "up_idle")
    t("cls-silence-unknown", classify({"operator": None, "wo": None,
                                       "mes_last_h": None, "heartbeat": None}) == "unknown")
    t("cls-stale-unknown", classify({"mes_last_h": 30.0}) == "unknown")   # 靜默久≠DOWN

    # 2) demo 每情境合成證據 classify 回自身（＝demo 走真階梯、分佈不變）。
    #    用「跨多站 + 分散 EID」的目錄＝貼近真 demo 目錄形狀（多站各數台、EID 分散），
    #    使五情境（含 7% unknown 尾）都取樣得到（純序列 EID 的相關 seed 會漏取尾桶）。
    catalog = [("seg%02d" % s, [("EID-%05d" % ((s * 7 + j * 101) % 90000 + 1000), "d")
                                for j in range(8)]) for s in range(38)]
    board = build_board(catalog)
    ok_self = all(classify(r["ev"]) == r["verdict"] for r in board["rows"])
    t("demo-rows-consistent", ok_self)
    t("demo-mode-sim", board["mode"] == "sim" and board["simulated"] is True)
    t("demo-all-verdicts-present",
      {r["verdict"] for r in board["rows"]} == set(VERDICTS))
    t("demo-counts-sum", board["n_up"] + board["n_down"] + board["n_unknown"] == board["n_total"])
    # DOWN 證據結構：凡判 down_* 者，必有 wo 或 operator（規則 1/2）——demo 合成不亂標 DOWN。
    t("demo-down-has-evidence",
      all((r["ev"].get("wo") or r["ev"].get("operator") == "reported")
          for r in board["rows"] if r["bucket"] == "down"))
    _demo_caliber = [_machine_row(f"EID-{i:05d}", "demo", "segCAL") for i in range(1, 200)]
    _demo_rnd = [r for r in _demo_caliber if r["ev"].get("mes_last_designation") == "Engineering (R&D)"]
    t("demo-production-caliber-rnd-showcase",
      bool(_demo_rnd)
      and all(r["ev"].get("mes_any_last_h") < r["ev"].get("mes_last_h")
              and r["ev"].get("mes_meta", {}).get("desig_available") is True
              for r in _demo_rnd))

    # 3) live board：只 UP·生產 / UNKNOWN（三源未接通 → 絕無 DOWN）
    ev = [("segCAL", [("EID-A", "a", {"mes_last_h": 0.5}),     # → up_producing
                      ("EID-B", "b", {"mes_last_h": 5.0}),     # → unknown（>門檻）
                      ("EID-C", "c", {"mes_last_h": None})])]  # → unknown（窗內無段）
    lb = build_board_live(ev)
    t("live-mode", lb["mode"] == "live" and lb["simulated"] is False)
    t("live-no-down", lb["n_down"] == 0 and lb["longest_down"] is None)
    t("live-up-count", lb["n_up"] == 1 and lb["n_unknown"] == 2)
    t("live-verdicts", {r["verdict"] for r in lb["rows"]} <= {"up_producing", "unknown"})
    t("live-since-none", all(r["since_h"] is None for r in lb["rows"]))
    t("live-wo-wired-default", lb["wo_wired"] is False)      # 無參預設 False（既有行為逐位元）
    # 排序：UNKNOWN（最靜默＝None 在前）→ UP
    order_ok = [r["verdict"] for r in lb["rows"]] == ["unknown", "unknown", "up_producing"]
    t("live-order", order_ok)
    t("live-eids-preserved", {r["eid"] for r in lb["rows"]} == {"EID-A", "EID-B", "EID-C"})

    # 4) live board + cmms 工單源接真（規則 2）：wo 有值 → DOWN；now_state/since_h 帶入；排序停最久在前
    ev2 = [("segCAL", [
        ("EID-D", "d", {"mes_last_h": 0.3, "wo": {"kind": "REACTIVE", "id": 9101},
                        "wo_since_h": 5.0, "now_state": "維修"}),          # → down_repair
        ("EID-E", "e", {"mes_last_h": 8.0, "wo": {"kind": "PM", "id": 9102},
                        "wo_since_h": 30.0, "now_state": "計畫保養"}),      # → down_pm
        ("EID-F", "f", {"mes_last_h": 0.5}),                               # → up_producing
        ("EID-G", "g", {"mes_last_h": None})])]                            # → unknown
    lb2 = build_board_live(ev2, wo_wired=True)
    by = {r["eid"]: r for r in lb2["rows"]}
    t("live-wo-wired-flag", lb2["wo_wired"] is True)
    t("live-wo-down", by["EID-D"]["verdict"] == "down_repair" and by["EID-D"]["bucket"] == "down")
    t("live-wo-pm", by["EID-E"]["verdict"] == "down_pm" and by["EID-E"]["bucket"] == "down")
    t("live-wo-now-state", by["EID-D"]["now_state"] == "維修" and by["EID-E"]["now_state"] == "計畫保養")
    t("live-down-count", lb2["n_down"] == 2 and lb2["n_up"] == 1 and lb2["n_unknown"] == 1)
    t("live-longest", lb2["longest_down"] is not None and lb2["longest_down"]["eid"] == "EID-E")
    t("live-since-passthru", by["EID-D"]["since_h"] == 5.0 and by["EID-F"]["since_h"] is None)
    # 排序：DOWN（停最久=30h 在前）→ UNKNOWN → UP
    t("live-down-sort", [r["eid"] for r in lb2["rows"]] == ["EID-E", "EID-D", "EID-G", "EID-F"])
    # 回歸鎖：舊形 ev（無 wo 鍵）＝與 3) 案結果同（wo=None → 不觸發規則 2）
    lb3 = build_board_live([("segCAL", [("EID-A", "a", {"mes_last_h": 0.5}),
                                        ("EID-B", "b", {"mes_last_h": 5.0}),
                                        ("EID-C", "c", {"mes_last_h": None})])])
    t("live-no-wo-unchanged",
      lb3["n_down"] == 0 and lb3["longest_down"] is None and lb3["wo_wired"] is False
      and [r["verdict"] for r in lb3["rows"]] == ["unknown", "unknown", "up_producing"])

    print("-" * 40)
    print("SELFTEST: " + ("PASS" if not fails else "FAIL: " + ", ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
