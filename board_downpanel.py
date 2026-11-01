# -*- coding: utf-8 -*-
"""
board_downpanel.py — down-詳情面板『呈現模型』（純 view-model；B8 down 詳情批次）
==================================================================================

定位（CONCEPT §19.4 B8；GOVERNANCE §3.2 consumer 紀律）：
  /equipment 列內下鑽「這台為什麼壞、故障碼是什麼」＝消費 remote-diag `diagnose.py` 的
  `diag.diagnose.v1`（board_diag 解析）+ Board 狀態軸（now）+ D6 人工確認原因 + eqf 三態校準。
  本模組＝**純函式 view-model**（零 I/O、零 subprocess）：把 board_diag.parse_diagnose 的正規化
  視圖攤成模板可直接渲染的 ready dict。真跑 diagnose.py／抓 D6 detail 都在 app 層（I/O 邊界）。

  分工鐵則（B8 裁決）：狀態（是不是 down）＝Board；為什麼＝remote-diag（正面 only、沉默≠沒故障）；
  開單＝cmms。coverage 誠實渲染＝Board 前端責任——「僅 MES 覆蓋」必顯警語，「未 pull（unavailable）」
  vs「pull 到 0 事件」要分辨（R5）。設備軸 eqf 三態＝Board B3 owner classify（confirmed>seed>unvetted、
  calibration_info）——nuisance 不藏、unvetted 標低信心、Diag hint 並排不合併（不 defer）。

  面板七段（模板順序）：
    ① Header（EID/機名/窗/來源徽章）② 狀態（Board 軸 now）③ 人工確認原因（D6，最高可信度）
    ④ 為什麼（Diag candidate_reasons + signal summary）⑤ 設備故障碼 eqf 表（三態 + Diag hint + dominant）
    ⑥ 資料覆蓋（三態 + 僅-MES 警語）⑦ Footer（observed_at + 來源 + 開單）。

  demo＝走**真 parse_diagnose 管線**（吃 _fixtures/ 兩樣本、確定性）＋「全合成」大牌；零 subprocess。
"""
import os
import json

import board_diag as diag_mod

_FXDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures")


# ---------------------------------------------------------------------------
# build_panel — parse_diagnose 正規化視圖 → 模板 ready dict（純函式）
# ---------------------------------------------------------------------------

def build_panel(eid, name, days, source, *, parsed=None, reason=None,
                now=None, confirmed_items=None, ef_csv_configured=False,
                confirmed_overlay=None, demo=False):
    """→ ready dict（模板七段）。parsed=None → unavailable 卡（status 由 reason 決定）。

    參數：
      parsed          : board_diag.parse_diagnose 輸出（或 None＝失敗/未接通/snapshot）。
      reason          : parsed None 時的 REASONS（not_configured/timeout/snapshot/…）。
      now             : Board 狀態軸 {bucket,verdict,wo_no,state,mes_last_h,stale}（passthrough；可 None）。
      confirmed_items : D6 [{wo_no, code}]（app 層抓 detail；demo 合成；snapshot None）。
      ef_csv_configured: 設備軸 CSV 是否配置（coverage 誠實：未配置＝設備軸天生缺、非「沒故障」）。
      confirmed_overlay: 注入 confirmed overlay（selftest 用；預設 module EQF_CONFIRMED）。
      demo            : True → 面板掛「全合成」大牌。
    """
    base = {"eid": eid, "name": name or eid, "days": days, "source": source,
            "demo": bool(demo), "now": now or {},
            "ef_csv_configured": bool(ef_csv_configured)}

    if parsed is None:
        # 失敗/未接通/snapshot：誠實 unavailable 卡（不猜、不偽裝有診斷）
        status = "not_configured" if reason in (None, "not_configured") else \
                 "snapshot" if reason == "snapshot" else "unavailable"
        base.update({"status": status, "reason": reason,
                     "observed_at": None, "window": None, "mode": None,
                     "confirmed": {"present": False, "items": []},
                     "candidates": [], "eqf": None, "coverage": None})
        return base

    ef = parsed.get("equipment_failure") or {}
    ef_present = bool(ef.get("present"))
    ef_status = ef.get("status")
    events = ef.get("events") or []
    ef_unavailable = bool(ef_present and ef_status == "unavailable")   # in-scope 無 CSV
    ef_zero = bool(ef_present and not ef_unavailable and not events)    # pull 到 0 事件
    ef_absent = not ef_present
    warn_only_mes = ef_absent or ef_unavailable or ef_zero             # 「僅 MES」警語旗（R5）

    cov = parsed.get("coverage") or {}
    coverage = {"ok": cov.get("ok") or [], "missing": cov.get("missing") or [],
                "skipped": cov.get("skipped") or [],
                "ef_unavailable": ef_unavailable, "ef_zero": ef_zero,
                "ef_absent": ef_absent, "warn_only_mes": warn_only_mes,
                "ef_csv_configured": bool(ef_csv_configured)}

    # ④ 為什麼：candidate_reasons + 首個 supporting signal 的 summary/evidence_ref
    sigmap = {}
    for s in (parsed.get("sources") or []):
        for sig in (s.get("signals") or []):
            if sig.get("signal_id"):
                sigmap[sig["signal_id"]] = sig
    candidates = []
    for c in (parsed.get("candidate_reasons") or []):
        summary = evref = None
        for sid in (c.get("supporting_signals") or []):
            sig = sigmap.get(sid)
            if sig:
                summary = summary or sig.get("summary")
                evref = evref or sig.get("evidence_ref")
        evrefs = c.get("evidence_refs") or []
        candidates.append({"reason": c.get("reason"), "confidence": c.get("confidence"),
                           "summary": summary, "evidence_ref": evref or (evrefs[0] if evrefs else None),
                           "supporting_signals": list(c.get("supporting_signals") or [])})

    # ⑤ 設備故障碼 eqf 表：每 event 掛 calibration_info（confirmed>seed>unvetted）+ Diag hint
    eqf_events = []
    for e in events:
        cal = diag_mod.calibration_info(e.get("code"), overlay=confirmed_overlay)
        eqf_events.append({
            "code": e.get("code"), "n_events": e.get("n_events"), "share_pct": e.get("share_pct"),
            "first_seen": e.get("first_seen"), "last_seen": e.get("last_seen"),
            "family": e.get("family"), "confidence": e.get("confidence"),
            "calibration": cal, "diag_hint": e.get("diag_hint")})
    eqf = {"present": ef_present, "status": ef_status,
           "events": eqf_events, "top_codes": ef.get("top_codes") or [],
           "dominant_actionable": ef.get("dominant_actionable")}

    # ③ 人工確認原因（D6）：app 傳 items；每筆掛 calibration source（confirmed 者最高可信度）
    items = []
    for it in (confirmed_items or []):
        code = it.get("code")
        items.append({"wo_no": it.get("wo_no"), "code": code,
                      "confirmed": bool(code)})   # code=None＝該單未確認（不誤標「無故障」）
    confirmed = {"present": any(x["confirmed"] for x in items), "items": items}

    base.update({
        "status": "ok",
        "reason": None,
        "observed_at": parsed.get("observed_at"),
        "window": parsed.get("window"),
        "mode": parsed.get("mode"),
        "confirmed": confirmed,
        "candidates": candidates,
        "eqf": eqf,
        "coverage": coverage,
    })
    return base


# ---------------------------------------------------------------------------
# demo_panel — 走真 parse_diagnose 管線（吃 _fixtures/ 兩樣本、確定性）＋「全合成」大牌
# ---------------------------------------------------------------------------

def _seed(s):
    return sum(ord(c) for c in (s or "x"))


def _embed_demo_parsed():
    """把 _fixtures/ 的 equipment_failure 樣本嵌進 diagnose 樣本（形狀照 board_diag._embed_ef 前例、
       非杜撰）→ parse_diagnose 正規化視圖。含設備軸源（eqf 4 碼）+ hardware_failure 候選 +
       Diag nuisance 種子（top[].workorder_crosscheck / dominant_actionable / calibration）。"""
    with open(os.path.join(_FXDIR, "diag.diagnose.v1.sample.json"), encoding="utf-8") as f:
        diag = json.load(f)
    with open(os.path.join(_FXDIR, "failure_seed.v1.nuisance.sample.json"),
              encoding="utf-8") as f:
        efdoc = json.load(f)
    signals = []
    for t in efdoc["top"]:
        nev = t["n_events"]
        conf = "high" if nev >= 10 else "medium" if nev >= 3 else "low"
        m = {"n_events": nev, "share_pct": t["share_pct"], "event_code": t["event_code"],
             "family": t["family"], "first_seen": t["first_seen"], "last_seen": t["last_seen"]}
        if "workorder_crosscheck" in t:
            m["workorder_crosscheck"] = t["workorder_crosscheck"]
        signals.append({"signal_id": ("equipment.eqf.%s" % t["event_code"]).lower(),
                        "summary": "%s ×%d" % (t["event_code"], nev),
                        "confidence": conf,
                        "evidence_ref": "data/DEMO/reports/<ts>_error_report.md#equipment_failure",
                        "metrics": m})
    diag["sources"] = list(diag["sources"]) + [{
        "source": "equipment_failure", "axis": "equipment", "status": "ok",
        "calibration": efdoc.get("calibration"),
        "dominant_actionable": efdoc.get("dominant_actionable"),
        "signals": signals}]
    # coverage 一致化（demo 既嵌設備軸源＝已採集，避免「collected: mes only」誤導）
    cov = diag.get("coverage") if isinstance(diag.get("coverage"), dict) else {"ok": [], "missing": [], "skipped": []}
    if "equipment_failure" not in (cov.get("ok") or []):
        cov = dict(cov, ok=list(cov.get("ok") or []) + ["equipment_failure"])
    diag["coverage"] = cov
    top = efdoc["top"][0]
    diag["candidate_reasons"] = list(diag["candidate_reasons"]) + [{
        "reason": "equipment:hardware_failure_event",
        "supporting_signals": [("equipment.eqf.%s" % top["event_code"]).lower()],
        "confidence": "high",
        "evidence_refs": ["data/DEMO/reports/<ts>_error_report.md#equipment_failure"]}]
    return diag_mod.parse_diagnose(json.dumps(diag))


def _demo_confirmed(eid):
    """demo D6 人工確認原因（確定性）：從種子的 actionable 碼挑一（per-eid），標 DEMO。"""
    actionable = sorted(c for c, v in diag_mod.EQF_CALIBRATION.items()
                        if (v.get("state") if isinstance(v, dict) else v) == "actionable")
    if not actionable:
        return []
    code = actionable[_seed(eid) % len(actionable)]
    return [{"wo_no": 49000 + (_seed(eid) % 900), "code": code}]


def _demo_now(eid):
    """demo Board 狀態軸（確定性）：約半數 down（維修）；其餘 UP·待命（形狀對齊 wem._now_block）。
       ★ `wem._now_block` 2026-08-09 起有第三桶 `dormant`（A11 過渡橋；SPEC-DORMANT-CAPACITY §3.2），
         本函式**刻意不合成它**——這個面板是「為什麼這台壞了」的診斷工具，長停工位不是診斷標的。
         真值路徑（app 傳 board 列的 now）若遇長停仍會帶 bucket='dormant' 進來，
         `_equipment_diag.html` 有對應分支（顯 ◌ 長停、不會落進 else 顯成 UP）。"""
    if _seed(eid) % 2 == 0:
        return {"bucket": "down", "verdict": "down", "wo_no": 49000 + (_seed(eid) % 900),
                "state": "維修", "mes_last_h": None, "stale": False}
    return {"bucket": "up", "verdict": "up_idle", "wo_no": None, "state": None,
            "mes_last_h": round(1.0 + (_seed(eid) % 90) / 10.0, 1), "stale": False}


def demo_panel(eid, name, days):
    """全合成 down-詳情面板（頁面掛「全合成」大牌）。走真 parse_diagnose 管線（fixtures）；
       零 subprocess、零 I/O（除讀本地 fixture）；per-eid 確定性。"""
    parsed = _embed_demo_parsed()
    return build_panel(eid, name, days, "demo", parsed=parsed, reason=None,
                       now=_demo_now(eid), confirmed_items=_demo_confirmed(eid),
                       ef_csv_configured=True, demo=True)


# ---------------------------------------------------------------------------
# selftest（純函式、零 I/O 除讀本地 fixture、deterministic）
# ---------------------------------------------------------------------------

def _selftest():
    n = 0
    # 種子檔（真資料）在脫敏公開樹被排除 ⇒ 校準表為空；依賴種子的斷言標 skipped（不算失敗），種子在場時照斷言。
    _seed_present = os.path.exists(diag_mod._SEED_PATH)
    if not _seed_present:
        print("board_downpanel selftest: skipped: %s absent (seed-sourced calibration asserts)" % diag_mod.SEED_FILE)

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # ---- (A) demo_panel：走真管線、七段齊、全合成旗 ----
    p = demo_panel("EID-17431", "CAL#2", 14)
    ck(p["status"] == "ok" and p["demo"] is True, "demo：status ok + demo 旗")
    ck(p["eid"] == "EID-17431" and p["name"] == "CAL#2" and p["days"] == 14, "demo：header")
    ck(p["eqf"]["present"] is True and len(p["eqf"]["events"]) == 4,
       f"demo：設備軸 4 事件: {len(p['eqf']['events'])}")
    ck(p["eqf"]["dominant_actionable"]["event_code"] == "eqfLT_zeroLeakCheckFail",
       "demo：dominant_actionable 搬對")
    ck(len(p["candidates"]) >= 1 and any(c["reason"] == "equipment:hardware_failure_event"
                                         for c in p["candidates"]),
       "demo：候選含 hardware_failure_event")
    # 候選帶 summary（來自 supporting signal）
    hw = next(c for c in p["candidates"] if c["reason"] == "equipment:hardware_failure_event")
    ck(hw["summary"] and hw["evidence_ref"], "demo：候選帶 summary/evidence_ref")

    # ---- (B) eqf 三態 calibration：nuisance/actionable/unvetted 皆現、Diag hint 並排 ----
    by_code = {e["code"]: e for e in p["eqf"]["events"]}
    if _seed_present:
        ck(by_code["eqfClean_TempOutOfRange"]["calibration"]["state"] == "nuisance",
           "eqf：OutsideTemperatureRange → nuisance（種子 + 不藏）")
        ck(by_code["eqfClean_TempOutOfRange"]["calibration"]["source"] == "seed",
           "eqf：nuisance 來源 seed")
    # Diag hint 並排（verdict_hint）——不合併進 calibration
    ck(by_code["eqfClean_TempOutOfRange"]["diag_hint"]["verdict_hint"] == "nuisance",
       "eqf：Diag hint 並排（不合併）")
    ck("workorder_crosscheck" not in by_code["eqfClean_TempOutOfRange"],
       "eqf：workorder_crosscheck 原鍵不外洩")
    # 無種子的碼（airPressureLoss 不在 20 碼種子且無 crosscheck）→ unvetted + diag_hint None
    ck(by_code["eqfLT_airPressureLoss"]["calibration"]["state"] == "unvetted"
       and by_code["eqfLT_airPressureLoss"]["diag_hint"] is None,
       "eqf：未種子碼 → unvetted、無 crosscheck → diag_hint None")

    # ---- (C) D6 confirmed（demo 合成）：actionable 種子碼、confirmed 旗 ----
    if _seed_present:
        ck(p["confirmed"]["present"] is True and len(p["confirmed"]["items"]) == 1,
           "demo：D6 confirmed 合成一筆")
        ck(p["confirmed"]["items"][0]["confirmed"] is True
           and diag_mod.classify_eqf(p["confirmed"]["items"][0]["code"]) == "actionable",
           "demo：D6 挑 actionable 種子碼")
        ck(demo_panel("EID-17431", "CAL#2", 14)["confirmed"]["items"][0]["code"]
           == p["confirmed"]["items"][0]["code"], "demo：D6 per-eid 確定性")

    # ---- (D) coverage：demo 含 mes + equipment_failure 皆 ok → 非僅-MES 警語 ----
    ck(p["coverage"]["warn_only_mes"] is False,
       f"demo：設備軸有事件 → 非僅-MES 警語: {p['coverage']}")
    ck("mes" in p["coverage"]["ok"] and "equipment_failure" in p["coverage"]["ok"],
       f"demo：coverage.ok 含 mes + equipment_failure（一致化）: {p['coverage']['ok']}")

    # ---- (E) build_panel unavailable 族（parsed None）：status 由 reason 決定、誠實不猜 ----
    for reason, exp in [("not_configured", "not_configured"), ("timeout", "unavailable"),
                        ("snapshot", "snapshot"), ("script_missing", "unavailable"),
                        (None, "not_configured")]:
        u = build_panel("EID-1", "M", 7, "live", parsed=None, reason=reason)
        ck(u["status"] == exp and u["reason"] == reason and u["eqf"] is None
           and u["coverage"] is None and u["confirmed"]["present"] is False,
           f"unavailable：reason={reason} → status {exp}, got {u['status']}")
    ck(build_panel("EID-1", None, 7, "live", parsed=None, reason="timeout")["name"] == "EID-1",
       "unavailable：name 缺 → 退 EID")

    # ---- (F) coverage 僅-MES 警語三態（設備軸 absent / unavailable / zero）----
    def _parsed_ef(status, events):
        src = [{"source": "mes", "status": "ok", "signals": []}]
        if status is not None:
            sigs = [{"signal_id": "equipment.eqf.%s" % e, "confidence": "low",
                     "metrics": {"event_code": e, "n_events": 1, "share_pct": 1.0}}
                    for e in events]
            src.append({"source": "equipment_failure", "status": status, "signals": sigs})
        doc = {"schema_id": "diag.diagnose.v1", "asset_id": "EID-1", "observed_at": "2026-07-04T00:00:00",
               "coverage": {"ok": ["mes"], "missing": [], "skipped": []},
               "sources": src, "candidate_reasons": []}
        return diag_mod.parse_diagnose(json.dumps(doc))
    pa = build_panel("EID-1", "M", 7, "live", parsed=_parsed_ef(None, []))    # 設備軸 absent
    ck(pa["coverage"]["ef_absent"] is True and pa["coverage"]["warn_only_mes"] is True,
       "coverage：設備軸 absent → 僅-MES 警語")
    pu = build_panel("EID-1", "M", 7, "live", parsed=_parsed_ef("unavailable", []))  # 無 CSV
    ck(pu["coverage"]["ef_unavailable"] is True and pu["coverage"]["ef_zero"] is False
       and pu["coverage"]["warn_only_mes"] is True,
       "coverage：設備軸 unavailable（無 CSV）≠0 事件、仍警語")
    pz = build_panel("EID-1", "M", 7, "live", parsed=_parsed_ef("ok", []))    # pull 到 0 事件
    ck(pz["coverage"]["ef_zero"] is True and pz["coverage"]["ef_unavailable"] is False
       and pz["coverage"]["warn_only_mes"] is True,
       "coverage：設備軸 ok 但 0 事件 → ef_zero、警語（未當『沒問題』）")
    pe = build_panel("EID-1", "M", 7, "live", parsed=_parsed_ef("ok", ["eqfFoo"]))  # 有事件
    ck(pe["coverage"]["warn_only_mes"] is False, "coverage：有設備軸事件 → 非僅-MES 警語")

    # ---- (G) confirmed_items：null code 不誤標「無故障」；confirmed 旗 ----
    pc = build_panel("EID-1", "M", 7, "live", parsed=_parsed_ef("ok", ["eqfFoo"]),
                     confirmed_items=[{"wo_no": 1, "code": None}, {"wo_no": 2, "code": "eqfBar"}])
    ck(pc["confirmed"]["present"] is True and len(pc["confirmed"]["items"]) == 2, "D6：兩筆")
    ck(pc["confirmed"]["items"][0]["confirmed"] is False
       and pc["confirmed"]["items"][1]["confirmed"] is True,
       "D6：null code → confirmed False（未確認≠無故障）、有 code → True")

    # ---- (H) calibration overlay 注入 → confirmed 覆蓋 eqf 表態 ----
    ov = {"eqfFoo": {"confirmed_n": 2, "wo_nos": [1], "last_confirmed_at": None}}
    po = build_panel("EID-1", "M", 7, "live", parsed=_parsed_ef("ok", ["eqfFoo"]),
                     confirmed_overlay=ov)
    e0 = po["eqf"]["events"][0]
    ck(e0["calibration"]["state"] == "actionable" and e0["calibration"]["source"] == "confirmed",
       "eqf：注入 confirmed overlay → 該碼升 actionable/confirmed")

    print(f"[board_downpanel] selftest PASS — {n} 項斷言全過："
          f"demo_panel 走真 parse_diagnose 管線（設備軸 4 事件/候選 summary/dominant_actionable）；"
          f"eqf 三態（nuisance 不藏/unvetted 標記/Diag hint 並排不合併/原鍵不外洩）；"
          f"D6 confirmed（demo 合成 actionable、null≠無故障）；coverage 僅-MES 警語三態"
          f"（absent/unavailable/zero 分辨）；unavailable 族（reason→status）；confirmed overlay 注入。"
          f"純函式零 I/O（除讀本地 fixture）。")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="down-詳情面板 view-model（純函式；--selftest 離線自驗）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（讀本地 fixture、零 subprocess）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
    else:
        ap.error("純 view-model 模組；離線驗證：--selftest")


if __name__ == "__main__":
    main()
