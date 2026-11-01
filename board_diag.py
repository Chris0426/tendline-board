# -*- coding: utf-8 -*-
"""
board_diag.py — remote-diag `diag.diagnose.v1` 設備軸「消費端 stub」（Diag diagnose.py → Tendline Board）
=================================================================================================

定位（CONCEPT §19.4 B8 D2 四條件；GOVERNANCE §3.2.2 consumer-stub-first）：
  Tendline Board 下鑽「診斷 X@時段T」＝**執行 remote-diag 自己的 CLI（`diagnose.py` / `fleet_diagnose.py`）
  作 on-demand subprocess、解析其版本化 `diag.*` JSON**，絕不 fork/重寫其歸因邏輯（防火牆「不抄碼」）。
  本檔把該消費紀律先寫成可測純函式＋subprocess 接線殼——fixture 驅動、零 I/O、deterministic。

  B8 D2 四約定（逐條落地）：
    ① **on-demand subprocess + hard timeout**：`run_diagnose` 每次呼叫強制 timeout（預設 120s，
       env `BOARD_Diag_TIMEOUT` 可覆寫）、全鏈唯讀 → 卡死可任意 kill 無副作用（Diag §1）。
    ② **消費其版本化 artifact、我不 fork**：只讀 `--json` stdout（§1「JSON＝最後一段非空 `{` 起始行」）、
       解析其凍結 schema，絕不引入/複製其 adapter 邏輯。
    ③ **schema_id pin + additive 容忍**：`parse_diagnose` 只認 `diag.diagnose.v1`（否則拒讀，不冒險解讀）；
       未知鍵/未知 source 一律忽略、永不崩（Diag additive-only 鐵則下欄位只增不改）。
    ④ **部署對齊 down-詳情面板＝非當前批次**：本檔**不接進 app.py / 儀表板**（B8 附錄；面板建置時再排線）。

設備軸 ≠ mfl 產品/良率軸（C2 / CONTRACTS §2-J）：
  remote-diag 2026-07-07 additive 凍結 `failure_seed.v1`——`diagnose` 的
  `sources[].source=="equipment_failure"`（signal_id 命名空間 `equipment.eqf.*`、source-local `_units`）
  ＋候選原因 `equipment:hardware_failure_event`。**設備軸＝故障『事件計數』（eqf* FailureCode 次數／
  首末見／人讀 descr），無 material_class／重測去重／FPY／fail_rate**——那些是 `mes.failmode.*`
  產品/良率軸概念。**勿跨軸合併、勿把設備故障事件當良率讀**；比率欄唯一＝`share_pct`＝percent_0_100
  （讀 source-local `_units` 判單位，勿套 diagnose 頂層 `rate_*`=fraction_0_1 規則，§2-J 地雷）。
  設備軸校準（eqf code → actionable/nuisance/unvetted）＝**B3 owner 落 Board**（`EQF_CALIBRATION`；
  種子＝cmms B10 objective seed，import 時由 `load_eqf_seed` 讀 `_fixtures/eqf_crosscheck_seed_all107_365d.json`）。

錯誤模型（**任何錯誤不外拋**，GOVERNANCE §3.3「失敗＝該卡 unavailable」）：
  `run_diagnose` 一律回 `(parsed, None)` 或 `(None, reason)`，reason ∈ REASONS：
    not_configured — 無 script_path 且 env `BOARD_Diag_SCRIPT` 未設（disabled≠error）
    script_missing — 明確給了 path 但檔不存在
    bad_window     — window 參數形狀非法（int 天數 / (start,end) 二元組）
    timeout        — subprocess 逾時（timeout kill、無副作用）
    spawn_error    — 無法起 subprocess（OSError/FileNotFound）
    exit_<rc>      — diagnose 非零離開（§1：diagnose 0＝成功〔MES 斷線也 0〕、非零＝真錯）
    no_json        — stdout 找不到 `{` 起始的 JSON 行
    bad_json       — JSON 解析失敗
    schema_rejected— schema_id 非 diag.diagnose.v1（QG-3.3 拒讀）
  ★ 禁「逾時→fallback 再打重查詢」（perf-saga FP-8）：逾時＝該次少讀、不重打。

用法：
  python board_diag.py --selftest    # 離線零 subprocess 自驗（注入 fake runner + 讀本地 fixture）
  （live 由 Chris 在域內機器跑〔Diag §1：`py diagnose.py --ast <EID> --days <N> --json`〕；Claude 不連 MES。）

樣板：consume 紀律照 board_w8_transport.py（(None,reason) 誠實降級、injectable transport/runner、
  純標準庫、零新依賴）；不抄 remote-diag 內容（只執行其 CLI + 解析其凍結 JSON）。
"""
import os
import sys
import json
import argparse

import core_paths as _wp   # 執行期產物路徑唯一出處（Q-F；零相依）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 契約常數（來源＝remote-diag CONTRACTS.md §0 #1/#8、§1、§2-J；byte-copy 金樣本在 _fixtures/）
# ---------------------------------------------------------------------------

SCHEMA_ID_DIAGNOSE = "diag.diagnose.v1"                # §1 執行契約 pin（accept-only；schema_version alias 亦收）
SCHEMA_ID_EQUIPMENT_FAILURE = "failure_seed.v1"   # standalone 設備軸（參照；stub 消費的是 diagnose 內嵌形）

EQUIPMENT_SOURCE = "equipment_failure"              # diagnose sources[].source 值（設備軸源）
EQF_SIGNAL_NS = "equipment.eqf."                    # 設備軸 signal_id 命名空間（≠產品軸 mes.failmode.*）
HARDWARE_FAILURE_REASON = "equipment:hardware_failure_event"   # 設備軸候選原因值
EF_UNITS_NOTE = ("share_pct=percent_0_100（設備軸唯一比率欄）；讀 source-local _units 判單位、勿臆測、"
                 "勿套 diagnose 頂層 rate_*=fraction_0_1 規則（§2-J）")

CONFIDENCE = ("high", "medium", "low")              # C3 正面 only enum

DEFAULT_TIMEOUT = 120.0                             # 秒；Diag §1 建議 diagnose 外層 ≤120s
ENV_SCRIPT = "BOARD_Diag_SCRIPT"                    # diagnose.py 絕對路徑（未設＝disabled）
ENV_TIMEOUT = "BOARD_Diag_TIMEOUT"                  # 覆寫 DEFAULT_TIMEOUT（秒）

REASONS = ("not_configured", "script_missing", "bad_window", "timeout", "spawn_error",
           "exit_<rc>", "no_json", "bad_json", "schema_rejected")   # 文件用；exit_<rc> 為前綴族


# ---------------------------------------------------------------------------
# 設備軸校準（B3 owner 落 Board；三態誠實：unvetted 不隱藏、nuisance 不丟棄）
# ---------------------------------------------------------------------------
#
# 種子＝cmms B10 objective seed（eqf × REACTIVE 工單「存在性＋時間窗」交叉核對；§19.4 B10）。
# byte-copy 金樣本在 `_fixtures/eqf_crosscheck_seed_all107_365d.json`（Diag/cmms top-40/365d、as_of 2026-07-07T16:00Z、拉取 2026-07-08）。
# **§24-clean**：種子僅 existence+time-window、絕無人員欄位（method.wo_scope 明載；載入端亦不 surface）。
# verdict_hint 是 **HINT 非最終裁決**——最終策展＝Board classifier（B3 owner）；本模組把 hint 映三態：
#   real_fault → actionable（真故障域、可行動）
#   nuisance   → nuisance（雜訊：de-weight＋標記、**非丟棄**，仍呈現）
#   insufficient_data → unvetted（證據不足＝未定案、不隱藏、標低信心 surfacing）
#   未知/缺 hint → unvetted（保守）
# nuisance 不丟棄、unvetted 不隱藏——down-詳情面板（B8、後續批次）消費 classify_eqf 三態呈現。
HINT_TO_STATE = {
    "real_fault": "actionable",
    "nuisance": "nuisance",
    "insufficient_data": "unvetted",
}

SEED_FILE = "eqf_crosscheck_seed_all107_365d.json"   # bundled 真校準種子（_fixtures/ 下；40碼/365d 擴碼版 2026-07-08）


def load_eqf_seed(path):
    """讀 eqf_workorder_crosscheck_seed.v1 種子檔 → dict：code → {state, hint, ratio, matched_wos}。

    - verdict_hint → state 依 HINT_TO_STATE（real_fault→actionable／nuisance→nuisance／
      insufficient_data→unvetted；未知或缺 hint → "unvetted" 保守）。
    - **tolerant**：缺檔／壞 JSON／非 dict／缺 codes[] → `{}`（永不 crash；import 時安全）。
    - 每筆只搬 §24-clean 欄（state/hint/ratio/matched_wos）——matched_work_orders 為工單號 list
      （存在性，非人員）；**絕不 surface 任何人員欄**（種子本無、載入端亦不引入）。
    - 未預期鍵一律忽略（additive 容忍）。
    - richer 形（值為 dict）與 classify_eqf 相容：classify_eqf 會由 dict 取 state。
    """
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    codes = doc.get("codes")
    if not isinstance(codes, list):
        return {}
    out = {}
    for c in codes:
        if not isinstance(c, dict):
            continue
        code = c.get("code")
        if not isinstance(code, str) or not code:
            continue
        hint = c.get("verdict_hint")
        state = HINT_TO_STATE.get(hint, "unvetted")
        ratio = c.get("ratio")
        ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
        mwos = ev.get("matched_work_orders")
        out[code] = {
            "state": state,
            "hint": hint if isinstance(hint, str) else None,
            "ratio": ratio if isinstance(ratio, (int, float)) else None,
            "matched_wos": list(mwos) if isinstance(mwos, list) else [],
        }
    return out


# 於 import 時載入 bundled 真種子（graceful：缺檔 → 空表 + flag、classify_eqf 仍全回 unvetted、不崩）。
_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures", SEED_FILE)
EQF_CALIBRATION = load_eqf_seed(_SEED_PATH)   # code → {state, hint, ratio, matched_wos}（缺種子→{}）
EQF_CALIBRATION_LOADED = bool(EQF_CALIBRATION)   # 種子是否成功載入（缺檔＝False、誠實、不崩）


def classify_eqf(code, *, table=None):
    """eqf code → "actionable" | "nuisance" | "unvetted"（三態誠實；契約：**只回 3 態字串**）。

    - 命中校準表且解出值為 actionable/nuisance → 回該值。
    - **未知 code（不在表）→ 預設 "unvetted"**（不隱藏、標低信心 surfacing，非靜默丟棄）。
    - nuisance ≠ 丟棄：呼叫端應 de-weight＋標記、仍呈現（誠實揭露「已知雜訊」）。
    table：可注入替代校準表（selftest 用；預設＝模組級 EQF_CALIBRATION＝bundled 種子）。
    值形相容 richer（dict：取其 "state"）與精簡（str）兩式；無法解出 → 保守 unvetted。
    """
    tbx = EQF_CALIBRATION if table is None else table
    val = tbx.get(code)
    if isinstance(val, dict):
        val = val.get("state")
    return val if val in ("actionable", "nuisance") else "unvetted"


# ---------------------------------------------------------------------------
# 人工確認學習層 overlay（D6 confirmed_reason_code → eqf classifier；§10.2 學習回圈）
# ---------------------------------------------------------------------------
#
# 種子（EQF_CALIBRATION）＝cmms B10 objective 交叉核對（客觀、批次）。**confirmed overlay** ＝
# REACTIVE 工單上工程師人工填的 `confirmed_reason_code`（D6，最高可信度、人在現場拍板）——
# 由 board_eqf_learn.harvest 收成 `_state/eqf_confirmed_overlay.json`（schema
# board.eqf_confirmed_overlay.v1、零人員欄）；本模組 import 時 tolerant 載入為 EQF_CONFIRMED。
# precedence（calibration_info）＝**confirmed > seed > unvetted**：人工確認過的碼一律 actionable
# （人在現場報修＝真故障域）；若種子把它判 nuisance＝**衝突旗標**（升 actionable 但標記、不默蓋、
# 供 §24 誠實揭露「機器判雜訊但人確認為真」）。overlay 缺檔＝graceful 空層、退回純種子分類。

CONFIRMED_OVERLAY_SCHEMA = "board.eqf_confirmed_overlay.v1"
CONFIRMED_OVERLAY_FILE = os.path.join("_state", "eqf_confirmed_overlay.json")


def load_confirmed_overlay(path):
    """讀 board.eqf_confirmed_overlay.v1 → dict：code → {confirmed_n, wo_nos, last_confirmed_at}。
    tolerant（缺檔／壞 JSON／非 dict／缺 codes → {}；import 安全）；**§24 clean**：只搬計數/工單號/
    時戳，絕無人員欄（產生端 harvest 亦不寫入人員欄）。未預期鍵忽略（additive 容忍）。"""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    codes = doc.get("codes")
    if not isinstance(codes, dict):
        return {}
    out = {}
    for code, v in codes.items():
        if not isinstance(code, str) or not code or not isinstance(v, dict):
            continue
        out[code] = {
            "confirmed_n": v.get("confirmed_n") if isinstance(v.get("confirmed_n"), int) else 0,
            "wo_nos": list(v.get("wo_nos")) if isinstance(v.get("wo_nos"), list) else [],
            "last_confirmed_at": v.get("last_confirmed_at")
            if isinstance(v.get("last_confirmed_at"), str) else None,
        }
    return out


# ⚠ R-17：import 當下就讀（原時機保留）⇒ BOARD_STATE_DIR 必須在 import 前設。
_CONFIRMED_PATH = _wp.state_path("eqf_confirmed_overlay.json",
                                 repo=os.path.dirname(os.path.abspath(__file__)))
EQF_CONFIRMED = load_confirmed_overlay(_CONFIRMED_PATH)     # code → {...}（缺檔→{}、graceful）
EQF_CONFIRMED_LOADED = bool(EQF_CONFIRMED)


def calibration_info(code, overlay=None):
    """eqf code → {state, source, conflict, seed_state, confirmed_n}（precedence 決策，down 面板消費）。
      - source ∈ {"confirmed", "seed", "unvetted"}；state ∈ {"actionable","nuisance","unvetted"}。
      - **confirmed**（code 在 overlay）→ state=actionable、source=confirmed；conflict=True 若種子判
        nuisance（人工確認勝出但標記、不默蓋）。
      - 否則落種子：在 EQF_CALIBRATION → source=seed、state=classify_eqf；不在 → source=unvetted。
    overlay：可注入替代 confirmed 層（selftest 用；預設＝模組級 EQF_CONFIRMED）。純函式、零 I/O。"""
    ov = EQF_CONFIRMED if overlay is None else overlay
    seed_state = classify_eqf(code)
    seed_known = code in EQF_CALIBRATION
    entry = (ov or {}).get(code)
    if entry is not None:
        return {"state": "actionable", "source": "confirmed",
                "conflict": (seed_state == "nuisance"), "seed_state": seed_state,
                "confirmed_n": (entry.get("confirmed_n") if isinstance(entry, dict) else None)}
    return {"state": seed_state, "source": ("seed" if seed_known else "unvetted"),
            "conflict": False, "seed_state": seed_state, "confirmed_n": 0}


# ---------------------------------------------------------------------------
# 例外（模組內部；絕不外拋給 caller——run_diagnose 一律轉 (None, reason)）
# ---------------------------------------------------------------------------

class DiagParseError(Exception):
    """parse_diagnose 內部信號：帶 .reason（∈ REASONS 非 subprocess 族）。"""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class DiagTimeout(Exception):
    """runner 逾時信號（default runner 由 subprocess.TimeoutExpired 轉；fake runner 可直接 raise）。"""


# ---------------------------------------------------------------------------
# 1) build_diagnose_cmd — 純 argv builder（Diag §1 簽章：--ast / --days|--start+--end / --json）
# ---------------------------------------------------------------------------

def build_diagnose_cmd(script_path, eid, window, *, equipment_failure_csv=None):
    """組 diagnose.py 的 subprocess argv（純函式；opt-in 設備軸旗標只在給 csv 時加）。

    Diag §1 執行契約簽章＝`py diagnose.py --ast <EID> [--days N | --start S --end E] --json`。
    flag 名一字不改自 remote-diag diagnose.py argparse（--ast / --days / --start / --end /
    --equipment-failure-csv / --json）——**讀其 argparse 定義、非臆測**。

    window：
      - int（天數）→ `--days N`（§1 canonical 形）。
      - (start, end) 二元組/list → `--start S --end E`（ISO 'YYYY-MM-DD[THH:MM:SS]'）。
      - None → 不加窗旗標（caller 責任）。
      - 其餘 → ValueError（run_diagnose 會轉 (None,"bad_window")、不外拋）。
    equipment_failure_csv：給了才加 `--equipment-failure-csv <path>`（opt-in 設備軸；不給＝不注入
      設備軸源、覆蓋乾淨。§2-J：設備軸 ≠ 產品/良率軸）。
    cmd[0]＝sys.executable（可直接餵 subprocess.run）。
    """
    cmd = [sys.executable, script_path, "--ast", str(eid)]
    if window is None:
        pass
    elif isinstance(window, (tuple, list)) and len(window) == 2:
        cmd += ["--start", str(window[0]), "--end", str(window[1])]
    elif isinstance(window, int) and not isinstance(window, bool):
        cmd += ["--days", str(window)]
    else:
        raise ValueError(f"bad window: {window!r}（需 int 天數或 (start,end) 二元組）")
    cmd += ["--json"]
    if equipment_failure_csv:
        cmd += ["--equipment-failure-csv", str(equipment_failure_csv)]
    return cmd


# ---------------------------------------------------------------------------
# 2) parse_diagnose — JSON 抽取 + schema_id pin + additive 容忍 + 正規化視圖
# ---------------------------------------------------------------------------

def _extract_json(text):
    """Diag §1：JSON＝stdout **最後一段非空 `{` 起始行**（其餘為 banner/警告、勿解析）。
    回 dict 或 raise DiagParseError(no_json/bad_json)。"""
    if not isinstance(text, str):
        raise DiagParseError("bad_json")
    line = None
    for ln in text.splitlines():
        if ln.lstrip().startswith("{"):
            line = ln.strip()        # 取最後一條（覆寫）
    if line is None:
        raise DiagParseError("no_json")
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        raise DiagParseError("bad_json")
    if not isinstance(obj, dict):
        raise DiagParseError("bad_json")
    return obj


def parse_diagnose(text):
    """diagnose --json stdout（str）→ 正規化視圖 dict；schema 不符/壞 JSON → raise DiagParseError。

    schema pin（accept-only diag.diagnose.v1；schema_version alias 亦收）＝QG-3.3 拒讀不冒險解讀。
    additive 容忍：未知頂層鍵、未知 source、未知 signal metrics 一律忽略、永不崩（Diag additive-only）。

    正規化視圖（消費端只依賴這些欄；其餘當 opaque）：
      {
        "schema_id":  "diag.diagnose.v1",
        "asset_id":   <asset_id 或 equipment_id alias；機台級 EID>,
        "observed_at":<observed_at 或 generated_at；可 None（§1 沉默點①：library import 邊界可空）>,
        "sources":    [{"source","status","signals":[{"signal_id","confidence","metrics"}]}],
        "equipment_failure": {present, status, events:[…eqf…], top_codes:[…]},   # 設備軸（§2-J）
        "candidate_reasons": [{"reason","confidence","supporting_signals","evidence_refs"}],
      }
    """
    payload = _extract_json(text)
    sid = payload.get("schema_id") or payload.get("schema_version")
    if sid != SCHEMA_ID_DIAGNOSE:
        raise DiagParseError("schema_rejected")    # QG-3.3：非 v1 一律拒讀（bump v2 走對版）

    # sources（additive 容忍：逐 source/signal 型別健檢、壞的跳過不崩）
    sources = []
    for s in (payload.get("sources") or []):
        if not isinstance(s, dict):
            continue
        sigs = []
        for sig in (s.get("signals") or []):
            if not isinstance(sig, dict):
                continue
            sigs.append({
                "signal_id": sig.get("signal_id"),
                "confidence": sig.get("confidence"),
                "summary": sig.get("summary"),           # 白話一行（down 面板「為什麼？」用）
                "evidence_ref": sig.get("evidence_ref"),  # verbatim 佐證小字
                "metrics": sig.get("metrics") if isinstance(sig.get("metrics"), dict) else {},
            })
        # 保留 source-level calibration / dominant_actionable（Diag nuisance 種子輸出；additive 容忍）
        sources.append({"source": s.get("source"), "status": s.get("status"),
                        "calibration": s.get("calibration"),
                        "dominant_actionable": s.get("dominant_actionable"),
                        "signals": sigs})

    # candidate_reasons（additive 容忍）
    reasons = []
    for c in (payload.get("candidate_reasons") or []):
        if not isinstance(c, dict):
            continue
        reasons.append({
            "reason": c.get("reason"),
            "confidence": c.get("confidence"),
            "supporting_signals": c.get("supporting_signals") if isinstance(c.get("supporting_signals"), list) else [],
            "evidence_refs": c.get("evidence_refs") if isinstance(c.get("evidence_refs"), list) else [],
        })

    # coverage / window / mode（頂層；down 面板誠實渲染「未 pull vs 0 事件」＝R5、B8②）
    cov = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    coverage = {"ok": cov.get("ok") if isinstance(cov.get("ok"), list) else [],
                "missing": cov.get("missing") if isinstance(cov.get("missing"), list) else [],
                "skipped": cov.get("skipped") if isinstance(cov.get("skipped"), list) else []}

    return {
        "schema_id": SCHEMA_ID_DIAGNOSE,
        "asset_id": payload.get("asset_id") or payload.get("equipment_id"),
        "observed_at": payload.get("observed_at") or payload.get("generated_at"),
        "window": payload.get("window") if isinstance(payload.get("window"), dict) else None,
        "mode": payload.get("mode"),
        "coverage": coverage,
        "sources": sources,
        "equipment_failure": _equipment_failure_block(sources),
        "candidate_reasons": reasons,
    }


def _num(v):
    """數值 pin：int/float 過、bool/str/其餘 → None（F2：Diag 端若吐 string 數字＝降級顯「—」、
    不讓模板 printf 500＋壞 panel 以 ok 毒快取；additive 容忍紀律補齊到設備軸兩數值欄）。"""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _equipment_failure_block(sources):
    """從正規化 sources[] 抽設備軸源（source=="equipment_failure"）→ 精簡塊。
    present＝該源在場（可能 status=unavailable＝in-scope 但無 CSV，events 空＝誠實）；
    events 只收 signal_id 落 `equipment.eqf.*` 命名空間者（跨軸隔離護欄）。
    數值欄（n_events/share_pct/diag_hint.ratio）過 `_num` 型別 pin（F2）。"""
    ef_src = next((s for s in sources if s.get("source") == EQUIPMENT_SOURCE), None)
    block = {"present": ef_src is not None,
             "status": ef_src.get("status") if ef_src else None,
             "events": [], "top_codes": [],
             # Diag nuisance 種子輸出的 source-level 欄（additive；Board 保持 B3 獨立分類、不 defer）：
             "diag_calibration": ef_src.get("calibration") if ef_src else None,
             "dominant_actionable": ef_src.get("dominant_actionable") if ef_src else None}
    if ef_src is None:
        return block
    for sig in ef_src["signals"]:
        sid = sig.get("signal_id") or ""
        if not sid.startswith(EQF_SIGNAL_NS):
            continue                              # 命名空間隔離：非 equipment.eqf.* 不當設備軸事件
        m = sig.get("metrics") or {}
        code = m.get("event_code") or sid[len(EQF_SIGNAL_NS):]
        # Diag 每事件的 workorder_crosscheck → 抽成獨立 diag_hint（**不混主欄**；Board event 不留原鍵）
        wc = m.get("workorder_crosscheck") if isinstance(m.get("workorder_crosscheck"), dict) else None
        diag_hint = ({"verdict_hint": wc.get("verdict_hint") if isinstance(wc.get("verdict_hint"), str) else None,
                    "ratio": _num(wc.get("ratio"))}
                   if wc else None)
        block["events"].append({
            "code": code, "signal_id": sid,
            "n_events": _num(m.get("n_events")), "share_pct": _num(m.get("share_pct")),
            "family": m.get("family"),
            "first_seen": m.get("first_seen"), "last_seen": m.get("last_seen"),
            "confidence": sig.get("confidence"),
            "diag_hint": diag_hint,
        })
    block["top_codes"] = [e["code"] for e in block["events"]]   # producer 已按 n_events 遞減排序
    return block


# ---------------------------------------------------------------------------
# 3) extract_equipment_axis — 設備軸 Board-facing 摘要（+校準三態）
# ---------------------------------------------------------------------------

def extract_equipment_axis(parsed, *, calibration_table=None):
    """正規化 parse_diagnose 視圖 → 設備軸 Board-facing 摘要。
    收 equipment_failure 源 + equipment.eqf.* signals + equipment:hardware_failure_event 候選；
    每 event 掛 classify_eqf 三態（actionable/nuisance/unvetted）＋彙總計數。"""
    ef = parsed.get("equipment_failure") or {"present": False, "status": None,
                                             "events": [], "top_codes": []}
    cand = next((c for c in (parsed.get("candidate_reasons") or [])
                 if c.get("reason") == HARDWARE_FAILURE_REASON), None)

    events = [dict(e, calibration=classify_eqf(e.get("code"), table=calibration_table))
              for e in ef.get("events", [])]
    summary = {"actionable": 0, "nuisance": 0, "unvetted": 0}
    for e in events:
        summary[e["calibration"]] += 1

    return {
        "present": ef.get("present", False),
        "status": ef.get("status"),
        "units_note": EF_UNITS_NOTE,
        "events": events,
        "top_codes": ef.get("top_codes", []),
        "hardware_failure_candidate": {
            "present": cand is not None,
            "confidence": cand.get("confidence") if cand else None,
            "supporting_signals": cand.get("supporting_signals") if cand else [],
        },
        "calibration_summary": summary,
    }


# ---------------------------------------------------------------------------
# 4) run_diagnose — on-demand subprocess + hard timeout（B8 D2①；任何錯誤回 (None,reason)）
# ---------------------------------------------------------------------------

def _resolve_timeout(timeout):
    if timeout is not None:
        return float(timeout)
    env = os.environ.get(ENV_TIMEOUT, "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return DEFAULT_TIMEOUT


def _subprocess_runner(cmd, timeout):
    """default runner：起 diagnose subprocess、UTF-8 capture stdout（Diag 已 reconfigure utf-8）。
    回 (returncode, stdout)；逾時 raise DiagTimeout；起不來 raise OSError。全鏈唯讀、kill 安全。"""
    import subprocess
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout,
                            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise DiagTimeout()
    return (cp.returncode, cp.stdout or "")


def run_diagnose(eid, window, *, script_path=None, equipment_failure_csv=None,
                 timeout=None, _runner=None):
    """on-demand 跑 remote-diag diagnose.py（B8 D2①）→ (parsed_view, None) 或 (None, reason)。

    guard（disabled≠error）：script_path 未給且 env BOARD_Diag_SCRIPT 未設 → (None,"not_configured")。
    _runner：可注入 `(cmd, timeout)->(returncode, stdout)`（selftest 用；可 raise DiagTimeout/OSError）；
             None＝真 subprocess（_subprocess_runner）。**注入時跳過 script 存在檢查**（測試不落地檔）。
    任何失敗一律 (None, reason)、絕不外拋、絕不逾時 fallback 重打（FP-8）。
    """
    script = script_path or os.environ.get(ENV_SCRIPT, "").strip()
    if not script:
        return (None, "not_configured")
    if _runner is None and not os.path.exists(script):
        return (None, "script_missing")

    try:
        cmd = build_diagnose_cmd(script, eid, window,
                                 equipment_failure_csv=equipment_failure_csv)
    except ValueError:
        return (None, "bad_window")

    runner = _runner or _subprocess_runner
    to = _resolve_timeout(timeout)
    try:
        rc, out = runner(cmd, to)
    except DiagTimeout:
        return (None, "timeout")            # 逾時＝該次少讀、**不重打**（FP-8）
    except OSError:
        return (None, "spawn_error")

    if rc != 0:
        # §1：diagnose exit 0＝成功（MES 斷線也 0＋coverage 誠實）；非零＝真錯（如 2＝參數錯）。
        return (None, "exit_%s" % rc)

    try:
        return (parse_diagnose(out), None)
    except DiagParseError as e:
        return (None, e.reason)


# ---------------------------------------------------------------------------
# selftest（離線零 subprocess：注入 fake runner + 讀本地 byte-copy fixture）
# ---------------------------------------------------------------------------

def _selftest():
    import os.path as _osp
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    fxdir = _osp.join(_osp.dirname(_osp.abspath(__file__)), "_fixtures")
    with open(_osp.join(fxdir, "diag.diagnose.v1.sample.json"), encoding="utf-8") as f:
        diag_raw = f.read()                          # 真 byte-copy（EID-SAMPLE、無設備軸源）
    with open(_osp.join(fxdir, "failure_seed.v1.sample.json"), encoding="utf-8") as f:
        ef_doc = json.load(f)                        # 真 byte-copy standalone 設備軸樣本

    # ---- 造「含設備軸源」的 diagnose text：形狀嚴格對映 Diag diagnose.py:250-294 _equipment_failure_source ----
    #      （非杜撰——逐欄照其碼；事件資料取自 standalone 樣本 top[]，confidence 用其 ≥10/≥3 門檻）
    def _embed_ef(base_raw, ef):
        doc = json.loads(base_raw)
        signals = []
        for t in ef["top"]:
            nev = t["n_events"]
            conf = "high" if nev >= 10 else "medium" if nev >= 3 else "low"
            signals.append({
                "signal_id": ("equipment.eqf.%s" % t["event_code"]).lower(),
                "summary": "%s ×%d" % (t["event_code"], nev),
                "metrics": {"n_events": nev, "share_pct": t["share_pct"],
                            "event_code": t["event_code"], "family": t["family"],
                            "first_seen": t["first_seen"], "last_seen": t["last_seen"]},
                "window_breakdown": [], "confidence": conf,
                "evidence_ref": "data/EID-SAMPLE/reports/<ts>_error_report.md#equipment_failure",
            })
        doc["sources"] = list(doc["sources"]) + [{
            "source": "equipment_failure", "axis": "equipment", "status": "ok",
            "detail_if_not": None, "_units": {"*_pct": "percent_0_100"},
            "freshness": {"source": "mes", "source_clock": "mes_server", "via": "bridge_csv",
                          "etl_latency_min": 60},
            "signals": signals}]
        top = ef["top"][0]
        top_conf = "high" if top["n_events"] >= 10 else "medium" if top["n_events"] >= 3 else "low"
        if top_conf in ("high", "medium"):
            doc["candidate_reasons"] = list(doc["candidate_reasons"]) + [{
                "reason": "equipment:hardware_failure_event",
                "supporting_signals": [("equipment.eqf.%s" % top["event_code"]).lower()],
                "confidence": top_conf,
                "evidence_refs": ["data/EID-SAMPLE/reports/<ts>_error_report.md#equipment_failure"]}]
        return doc, top_conf

    with_doc, top_conf = _embed_ef(diag_raw, ef_doc)
    with_text = json.dumps(with_doc)
    without_text = diag_raw

    # ---- (A) parse without 設備軸源：schema pin 過、asset_id alias、equipment_failure.present False ----
    pw = parse_diagnose(without_text)
    ck(pw["schema_id"] == SCHEMA_ID_DIAGNOSE, "without：schema_id pin")
    ck(pw["asset_id"] == "EID-SAMPLE", f"without：asset_id alias, got {pw['asset_id']}")
    ck(pw["observed_at"] == "2026-07-04T14:57:58", "without：observed_at 取得")
    ck(pw["equipment_failure"]["present"] is False and pw["equipment_failure"]["events"] == [],
       "without：無設備軸源 → present False / events 空")
    ck(any(s["source"] == "mes" for s in pw["sources"]), "without：MES 源仍在")

    # ---- (B) parse with 設備軸源：present True、events 對映、top_codes、候選原因 ----
    pv = parse_diagnose(with_text)
    ef = pv["equipment_failure"]
    ck(ef["present"] is True and ef["status"] == "ok", "with：設備軸源 present/status")
    ck(len(ef["events"]) == len(ef_doc["top"]), f"with：events 數＝top 數, got {len(ef['events'])}")
    ck(all(e["signal_id"].startswith(EQF_SIGNAL_NS) for e in ef["events"]),
       "with：所有 event signal_id 落 equipment.eqf.* 命名空間")
    ck(ef["events"][0]["code"] == ef_doc["top"][0]["event_code"]
       and ef["events"][0]["n_events"] == ef_doc["top"][0]["n_events"]
       and ef["events"][0]["share_pct"] == ef_doc["top"][0]["share_pct"],
       "with：首 event 欄位對映 standalone top[0]")
    ck(ef["top_codes"] == [t["event_code"] for t in ef_doc["top"]],
       f"with：top_codes 依序, got {ef['top_codes']}")
    # F2 型別 pin：Diag 若吐 string/bool 數值 → None（模板顯「—」、不 printf 500 不毒快取）
    ck(_num(42) == 42 and _num(0.5) == 0.5, "F2 pin：int/float 原樣過")
    ck(_num("42.0") is None and _num(True) is None and _num(None) is None and _num([1]) is None,
       "F2 pin：str/bool/None/list → None")
    _bad = json.loads(with_text)
    for _s in _bad.get("sources", []):
        if _s.get("source") == EQUIPMENT_SOURCE:
            for _g in _s.get("signals", []):
                _m = _g.get("metrics") or {}
                if "share_pct" in _m:
                    _m["share_pct"] = "42.0"          # 注入 string 數字（Diag 未來 bug 模擬）
                if "n_events" in _m:
                    _m["n_events"] = "7"
                wc0 = _m.get("workorder_crosscheck")
                if isinstance(wc0, dict):
                    wc0["ratio"] = "0.5"
    _pb = parse_diagnose(json.dumps(_bad))
    _ebad = _pb["equipment_failure"]["events"]
    ck(all(e["share_pct"] is None and e["n_events"] is None for e in _ebad),
       "F2 pin：注入 string 數值 → share_pct/n_events 全 None（降級不炸）")
    ck(all((e["diag_hint"] is None) or (e["diag_hint"]["ratio"] is None) for e in _ebad),
       "F2 pin：diag_hint.ratio string → None")

    # ---- (C) extract_equipment_axis：候選原因 + 三態校準彙總（顯式空表 → 全 unvetted）----
    #      模組級 EQF_CALIBRATION 現已載入真種子（含此樣本的 eqfClean_TempOutOfRange=nuisance
    #      等），故此處**顯式傳空表**以隔離測「空校準表 → 全 unvetted」的彙總邏輯。
    axis = extract_equipment_axis(pv, calibration_table={})
    ck(axis["present"] is True and axis["hardware_failure_candidate"]["present"] is True,
       "axis：設備軸在場 + hardware_failure 候選在場")
    ck(axis["hardware_failure_candidate"]["confidence"] == top_conf,
       f"axis：候選信心＝top_conf({top_conf})")
    ck(axis["calibration_summary"]["unvetted"] == len(ef_doc["top"])
       and axis["calibration_summary"]["actionable"] == 0
       and axis["calibration_summary"]["nuisance"] == 0,
       f"axis：空校準表 → 全 unvetted, got {axis['calibration_summary']}")
    ck(all(e["calibration"] == "unvetted" for e in axis["events"]), "axis：每 event 掛 unvetted")
    axis_none = extract_equipment_axis(pw)
    ck(axis_none["present"] is False and axis_none["events"] == []
       and axis_none["hardware_failure_candidate"]["present"] is False,
       "axis：無設備軸 → present False、候選 absent")

    # ---- (D) additive 容忍：未知頂層鍵 + 未知 source → 仍解析、設備軸不受擾 ----
    add = json.loads(with_text)
    add["brand_new_top_key"] = {"future": 1}
    add["sources"] = list(add["sources"]) + [{
        "source": "future_source_x", "status": "ok",
        "signals": [{"signal_id": "future.sig.z", "confidence": "high", "metrics": {"z": 1}}]}]
    pa = parse_diagnose(json.dumps(add))
    ck(pa["equipment_failure"]["present"] is True
       and len(pa["equipment_failure"]["events"]) == len(ef_doc["top"]),
       "additive：未知鍵/源不影響設備軸事件數")
    ck(any(s["source"] == "future_source_x" for s in pa["sources"])
       and not any(e["signal_id"] == "future.sig.z" for e in pa["equipment_failure"]["events"]),
       "additive：未知 source 保留於 sources、但不溜進設備軸（命名空間隔離）")

    # ---- (E) schema pin 拒讀 + 壞 JSON 族 ----
    bad_v2 = json.loads(diag_raw)
    bad_v2["schema_id"] = "diag.diagnose.v2"
    bad_v2["schema_version"] = "diag.diagnose.v2"
    for txt, reason in [(json.dumps(bad_v2), "schema_rejected"),
                        ("banner only\nno json here", "no_json"),
                        ("WARN foo\n{not valid json", "bad_json")]:
        try:
            parse_diagnose(txt)
            ck(False, f"應 raise DiagParseError({reason})")
        except DiagParseError as e:
            ck(e.reason == reason, f"parse reject reason 應 {reason}, got {e.reason}")

    # ---- (F) build_diagnose_cmd：--ast/--json、--days vs --start/--end、opt-in 旗標 ----
    c1 = build_diagnose_cmd("/x/diagnose.py", "EID-00884", 7)
    ck(c1[1:] == ["/x/diagnose.py", "--ast", "EID-00884", "--days", "7", "--json"],
       f"cmd int window → --days, got {c1[1:]}")
    ck("--equipment-failure-csv" not in c1, "cmd：無 csv → 無 opt-in 旗標")
    c2 = build_diagnose_cmd("/x/diagnose.py", "EID-00884", ("2026-06-01", "2026-06-08"),
                            equipment_failure_csv="/tmp/ef.csv")
    ck("--start" in c2 and "--end" in c2 and c2[c2.index("--start") + 1] == "2026-06-01",
       f"cmd tuple window → --start/--end, got {c2[1:]}")
    ck(c2[c2.index("--equipment-failure-csv") + 1] == "/tmp/ef.csv",
       "cmd：給 csv → opt-in 旗標帶值")
    c3 = build_diagnose_cmd("/x/diagnose.py", "EID-1", None)
    ck("--days" not in c3 and "--start" not in c3 and c3[-1] == "--json",
       "cmd：window None → 無窗旗標")

    # ---- (G) classify_eqf 三態（注入表 str/dict 兩式 → actionable/nuisance/unvetted）----
    tbx = {"eqfA_act": "actionable", "eqfB_noise": "nuisance"}
    ck(classify_eqf("eqfA_act", table=tbx) == "actionable", "注入表(str)：actionable")
    ck(classify_eqf("eqfB_noise", table=tbx) == "nuisance", "注入表(str)：nuisance")
    ck(classify_eqf("eqfC_unknown", table=tbx) == "unvetted", "注入表：未知 → unvetted")
    ck(classify_eqf("eqfX", table={"eqfX": "bogus"}) == "unvetted", "非法校準值 → 保守 unvetted")
    ck(classify_eqf("eqfX", table={}) == "unvetted", "空表 → unvetted")
    # richer 形（值為 dict、取 state）亦相容：classify_eqf 契約＝只回 3 態字串
    tbx_rich = {"eqfR_act": {"state": "actionable", "ratio": 0.9, "matched_wos": [1, 2]},
                "eqfR_noise": {"state": "nuisance", "ratio": 0.0, "matched_wos": []},
                "eqfR_uv": {"state": "unvetted", "ratio": 0.1, "matched_wos": [3]},
                "eqfR_bad": {"ratio": 0.5}}   # 無 state → unvetted
    ck(classify_eqf("eqfR_act", table=tbx_rich) == "actionable", "注入表(dict)：state=actionable")
    ck(classify_eqf("eqfR_noise", table=tbx_rich) == "nuisance", "注入表(dict)：state=nuisance")
    ck(classify_eqf("eqfR_uv", table=tbx_rich) == "unvetted", "注入表(dict)：state=unvetted")
    ck(classify_eqf("eqfR_bad", table=tbx_rich) == "unvetted", "注入表(dict)：缺 state → unvetted")

    # ---- (G2) load_eqf_seed：GOLDEN 合成種子 hint→state 映射 + tolerant + §24 clean ----
    _here = _osp.dirname(_osp.abspath(__file__))
    golden = load_eqf_seed(_osp.join(_here, "_fixtures", "eqf_workorder_crosscheck_seed.v1.json"))
    ck(classify_eqf("eqfReal_Fault", table=golden) == "actionable",
       "GOLDEN：real_fault → actionable")
    ck(classify_eqf("eqfNuisance", table=golden) == "nuisance", "GOLDEN：nuisance → nuisance")
    ck(classify_eqf("eqfSparse", table=golden) == "unvetted", "GOLDEN：insufficient_data → unvetted")
    ck(classify_eqf("eqfNotInSeed", table=golden) == "unvetted", "GOLDEN：未知 code → unvetted")
    ck(golden["eqfReal_Fault"]["hint"] == "real_fault"
       and golden["eqfReal_Fault"]["ratio"] == 1.0
       and golden["eqfReal_Fault"]["matched_wos"] == [1001],
       f"GOLDEN：richer 欄（hint/ratio/matched_wos）搬對, got {golden['eqfReal_Fault']}")
    # §24：載入 dict 每筆只 surface {state,hint,ratio,matched_wos}、絕無人員欄
    _person_keys = {"author", "person", "assignee", "actor", "operator", "user",
                    "changed_by", "closed_by"}
    for _code, _v in golden.items():
        ck(set(_v.keys()) == {"state", "hint", "ratio", "matched_wos"},
           f"§24：載入值欄位集僅 state/hint/ratio/matched_wos, got {set(_v.keys())}")
        ck(not (_person_keys & set(_v.keys())), "§24：載入值不含任何人員欄")
    # tolerant：缺檔 → 空 dict（不崩）+ classify_eqf 仍 unvetted
    missing = load_eqf_seed(_osp.join(_here, "_fixtures", "no_such_seed_file.json"))
    ck(missing == {}, "tolerant：缺檔 → 空 dict（不崩）")
    ck(classify_eqf("eqfAnything", table=missing) == "unvetted", "tolerant：空表 → unvetted")
    # tolerant：缺 codes[] → 空 dict（指向沒有 codes[] 的既有 fixture＝equipment_failure 樣本）
    nocodes = load_eqf_seed(_osp.join(_here, "_fixtures", "failure_seed.v1.sample.json"))
    ck(nocodes == {}, "tolerant：缺 codes[] → 空 dict（不崩）")

    # ---- (G3) bundled 真種子（module-level EQF_CALIBRATION）：載入 + 具體碼映射 + 三態彙總 ----
    # 種子檔（真資料）在脫敏公開樹被排除 ⇒ 缺檔標 skipped、不算失敗；種子在場時下列斷言一條不放寬。
    _seed_present = _osp.exists(_SEED_PATH)
    if not _seed_present:
        print("board_diag selftest: skipped: %s absent (bundled-seed asserts G3)" % SEED_FILE)
    else:
        ck(EQF_CALIBRATION_LOADED is True and len(EQF_CALIBRATION) == 40,
           f"bundled：真種子載入 40 碼, got loaded={EQF_CALIBRATION_LOADED} n={len(EQF_CALIBRATION)}")
        ck(classify_eqf("eqfClean_TempOutOfRange") == "nuisance",
           "bundled：eqfClean_TempOutOfRange(nuisance) → nuisance（坐實 Diag noise-code call）")
        ck(classify_eqf("eqfCLN_DiWaterSensorStuck") == "actionable",
           "bundled：eqfCLN_DiWaterSensorStuck(real_fault) → actionable")
        ck(classify_eqf("eqfCLN_MainPlateDidNotExtend") == "unvetted",
           "bundled：eqfCLN_MainPlateDidNotExtend(insufficient_data) → unvetted")
    ck(classify_eqf("eqfNeverSeen") == "unvetted", "bundled：未知 code → unvetted")
    # ★ 分佈以「實際交付檔」為準（40碼/365d 擴碼版 2026-07-08）：
    #   real_fault×14→actionable、nuisance×5、insufficient_data×21→unvetted。
    #   （交付 seed 之實測；勿改回任何「預期」數字——檔案＝ground truth。長窗稀釋致部分碼較 60d 版
    #   翻轉〔如 eqfCLN_DiWaterFillTimeout insufficient_data→real_fault〕＝Board 直取 verdict_hint、不調門檻。）
    _states = [v["state"] for v in EQF_CALIBRATION.values()]
    if _seed_present:
        ck(_states.count("actionable") == 14 and _states.count("nuisance") == 5
           and _states.count("unvetted") == 21,
           f"bundled：三態分佈 actionable14/nuisance5/unvetted21, got "
           f"a={_states.count('actionable')}/n={_states.count('nuisance')}/u={_states.count('unvetted')}")

    # ---- (G4) Diag live nuisance 種子形狀對版（2026-07-07 Diag relay：帶種子金樣本「對形」）----
    #      Diag 上線消費 cmms 官方種子（nuisance 降權不刪、--nuisance-seed opt-in）後，standalone
    #      failure_seed.v1 輸出新增三處：top[].workorder_crosscheck{verdict_hint,ratio,…}、
    #      頂層 dominant_actionable、calibration block{nuisance_codes_applied,…}。金樣本 byte-copy＝
    #      _fixtures/failure_seed.v1.nuisance.sample.json（md5 f995b04b…）。
    #      鎖三件事：① additive 容忍（新欄不崩、workorder_crosscheck 不溜進 Board event 欄）；
    #      ② 跨消費者一致——Board 獨立 classify_eqf 對 calibration.nuisance_codes_applied 的碼＝nuisance
    #         （B3 owner 保持獨立分類、不 defer Diag hint；兩邊消費同一份真種子故一致）；
    #      ③ ★golden 是「形狀 fixture、值合成」——top[].workorder_crosscheck.verdict_hint 為合成值
    #         （EID-SAMPLE/equipment_id 999），**勿對它做值等斷言**（真種子值以本地 EQF_CALIBRATION 為準；
    #         例：eqfLT_zeroLeakCheckFail 樣本合成 real_fault、真種子實為 insufficient_data→unvetted）。
    with open(_osp.join(fxdir, "failure_seed.v1.nuisance.sample.json"), encoding="utf-8") as f:
        nz_doc = json.load(f)
    # 把樣本 top[]（含 workorder_crosscheck）＋calibration block 嵌進 diagnose 形，走真 parser
    nz_diag = json.loads(diag_raw)
    nz_signals = []
    for t in nz_doc["top"]:
        m = {"n_events": t["n_events"], "share_pct": t["share_pct"],
             "event_code": t["event_code"], "family": t["family"],
             "first_seen": t["first_seen"], "last_seen": t["last_seen"]}
        if "workorder_crosscheck" in t:
            m["workorder_crosscheck"] = t["workorder_crosscheck"]   # Diag 新欄（應被 Board 忽略）
        nz_signals.append({"signal_id": ("equipment.eqf.%s" % t["event_code"]).lower(),
                           "confidence": "high", "metrics": m})
    nz_diag["sources"] = list(nz_diag["sources"]) + [{
        "source": "equipment_failure", "axis": "equipment", "status": "ok",
        "calibration": nz_doc.get("calibration"),                    # Diag 新 block（應被 Board 忽略）
        "dominant_actionable": nz_doc.get("dominant_actionable"),    # Diag 新欄（應被 Board 忽略）
        "signals": nz_signals}]
    nz_axis = extract_equipment_axis(parse_diagnose(json.dumps(nz_diag)))
    ck(nz_axis["present"] is True and len(nz_axis["events"]) == len(nz_doc["top"]),
       f"nuisance 對版：additive 不崩、events 數＝top 數, got {len(nz_axis['events'])}")
    ck(all("workorder_crosscheck" not in e for e in nz_axis["events"]),
       "nuisance 對版：workorder_crosscheck 不溜進 Board event 欄（additive 隔離）")
    # ② 跨消費者一致：Diag calibration.nuisance_codes_applied ⊆ 我方分 nuisance（同真種子）
    _nz_applied = (nz_doc.get("calibration") or {}).get("nuisance_codes_applied") or []
    if _seed_present:   # 需 bundled 真種子（公開樹缺檔 ⇒ 已於 G3 印 skipped）
        for _nc in _nz_applied:
            ck(classify_eqf(_nc) == "nuisance",
               f"nuisance 對版：Diag nuisance_codes_applied 之 {_nc} → Board 亦 nuisance（跨消費者一致）")
    ck(len(_nz_applied) >= 1, "nuisance 對版：金樣本至少宣告一個 nuisance code")

    # ---- (G5) down 面板 additive：coverage/window/mode 頂層 + signals summary/evidence_ref ----
    ck(pw["coverage"]["ok"] == ["mes"] and pw["coverage"]["missing"] == []
       and pw["coverage"]["skipped"] == [], f"coverage 頂層搬對: {pw['coverage']}")
    ck(pw["mode"] == "standard" and isinstance(pw["window"], dict)
       and pw["window"].get("anchor") == "mes_server", "window/mode 頂層搬對")
    mes_src = next(s for s in pw["sources"] if s["source"] == "mes")
    ck(mes_src["signals"][0]["summary"] and mes_src["signals"][0]["evidence_ref"],
       "signals 搬 summary/evidence_ref（down 面板『為什麼？』）")
    # diag_hint：nuisance 金樣本 event 帶 workorder_crosscheck → 抽成 diag_hint（不留原鍵）
    nz_ef = parse_diagnose(json.dumps(nz_diag))["equipment_failure"]
    _e0 = nz_ef["events"][0]
    ck(_e0.get("diag_hint") is not None and _e0["diag_hint"]["verdict_hint"] == "nuisance"
       and "workorder_crosscheck" not in _e0,
       f"diag_hint 抽出、原鍵不留於 Board event: {_e0.get('diag_hint')}")
    ck(nz_ef.get("dominant_actionable", {}).get("event_code") == "eqfLT_zeroLeakCheckFail"
       and (nz_ef.get("diag_calibration") or {}).get("nuisance_codes_applied"),
       "block-level dominant_actionable / diag_calibration 保留（source-level 搬上來）")
    # 無 crosscheck 的 event → diag_hint None（additive 容忍、不崩）
    ck(nz_ef["events"][-1].get("diag_hint") is None, "無 crosscheck event → diag_hint None")

    # ---- (G6) calibration_info：precedence confirmed > seed > unvetted + 衝突旗 ----
    ov = {"eqfCLN_DiWaterSensorStuck": {"confirmed_n": 3, "wo_nos": [11, 12], "last_confirmed_at": None},
          "eqfClean_TempOutOfRange": {"confirmed_n": 1, "wo_nos": [21], "last_confirmed_at": None}}
    if _seed_present:   # 三條都吃 bundled 真種子的具體碼（公開樹缺檔 ⇒ 已於 G3 印 skipped）
        ci = calibration_info("eqfCLN_DiWaterSensorStuck", overlay=ov)   # 種子=actionable、confirmed
        ck(ci["state"] == "actionable" and ci["source"] == "confirmed" and ci["conflict"] is False
           and ci["confirmed_n"] == 3, f"confirmed 覆蓋（種子亦 actionable、無衝突）: {ci}")
        ci2 = calibration_info("eqfClean_TempOutOfRange", overlay=ov)  # 種子=nuisance、人工確認
        ck(ci2["state"] == "actionable" and ci2["source"] == "confirmed" and ci2["conflict"] is True
           and ci2["seed_state"] == "nuisance",
           f"confirmed 勝出但標衝突旗（種子 nuisance）: {ci2}")
        ci3 = calibration_info("eqfCLN_DiWaterSensorStuck", overlay={})     # 純種子（無 confirmed）
        ck(ci3["state"] == "actionable" and ci3["source"] == "seed" and ci3["conflict"] is False,
           f"純種子 actionable: {ci3}")
    ci4 = calibration_info("eqfNeverSeenCode", overlay={})              # 未知 → unvetted
    ck(ci4["state"] == "unvetted" and ci4["source"] == "unvetted", f"未知碼 → unvetted: {ci4}")

    # ---- (G7) load_confirmed_overlay：tolerant + §24 clean ----
    ck(load_confirmed_overlay(_osp.join(_here, "_fixtures", "no_such_overlay.json")) == {},
       "confirmed overlay 缺檔 → {}（graceful）")
    ck(load_confirmed_overlay(_osp.join(fxdir, "diag.diagnose.v1.sample.json")) == {},
       "confirmed overlay 缺 codes dict → {}（tolerant）")

    # ---- (H) run_diagnose：guard + 注入 fake runner（零 subprocess）----
    saved_s = os.environ.pop(ENV_SCRIPT, None)
    saved_t = os.environ.pop(ENV_TIMEOUT, None)
    try:
        # not_configured：無 path、無 env
        ck(run_diagnose("EID-1", 7) == (None, "not_configured"), "guard：未設 → not_configured")
        # script_missing：明確給不存在的 path（真 runner，但存在檢查在 spawn 前短路＝零 subprocess）
        ck(run_diagnose("EID-1", 7, script_path="/no/such/diagnose.py") == (None, "script_missing"),
           "guard：path 不存在 → script_missing（未起 subprocess）")
        # 注入 ok runner（吃 with_text）→ 全鏈解析、設備軸在場
        cap = {}

        def fake_ok(cmd, timeout):
            cap["cmd"], cap["timeout"] = cmd, timeout
            return (0, "banner line\n[info] blah\n" + with_text)   # 前置 banner 驗 §1 抽取
        res, reason = run_diagnose("EID-9", 14, script_path="/x/diagnose.py",
                                   equipment_failure_csv="/tmp/ef.csv", _runner=fake_ok)
        ck(reason is None and res["equipment_failure"]["present"] is True,
           f"run ok：設備軸解析, got reason={reason}")
        ck("--equipment-failure-csv" in cap["cmd"] and "--days" in cap["cmd"],
           "run ok：cmd 帶 opt-in 旗標 + --days")
        ck(cap["timeout"] == DEFAULT_TIMEOUT, f"run ok：預設 timeout {DEFAULT_TIMEOUT}")
        # env 覆寫 timeout + env script path
        os.environ[ENV_SCRIPT] = "/env/diagnose.py"
        os.environ[ENV_TIMEOUT] = "33"
        cap2 = {}

        def fake_cap(cmd, timeout):
            cap2["cmd"], cap2["timeout"] = cmd, timeout
            return (0, with_text)
        res, reason = run_diagnose("EID-9", 7, _runner=fake_cap)
        ck(reason is None and cap2["timeout"] == 33.0 and cap2["cmd"][1] == "/env/diagnose.py",
           f"run：env 覆寫 timeout/script, got t={cap2.get('timeout')}")
        # timeout / spawn_error / exit_<rc> / bad stdout / schema reject（全注入、零 subprocess）
        def fake_to(cmd, timeout):
            raise DiagTimeout()
        ck(run_diagnose("EID-9", 7, _runner=fake_to) == (None, "timeout"), "run：逾時 → timeout（不重打）")

        def fake_os(cmd, timeout):
            raise OSError("spawn boom")
        ck(run_diagnose("EID-9", 7, _runner=fake_os) == (None, "spawn_error"), "run：起不來 → spawn_error")
        ck(run_diagnose("EID-9", 7, _runner=lambda c, t: (2, "")) == (None, "exit_2"),
           "run：非零離開 → exit_2（§1）")
        ck(run_diagnose("EID-9", 7, _runner=lambda c, t: (0, "no json at all")) == (None, "no_json"),
           "run：stdout 無 JSON → no_json")
        ck(run_diagnose("EID-9", 7, _runner=lambda c, t: (0, json.dumps(bad_v2))) == (None, "schema_rejected"),
           "run：v2 stdout → schema_rejected")
        # bad_window 經 run（不外拋）
        ck(run_diagnose("EID-9", object(), _runner=lambda c, t: (0, with_text)) == (None, "bad_window"),
           "run：非法 window → bad_window（不外拋）")
    finally:
        os.environ.pop(ENV_SCRIPT, None)
        os.environ.pop(ENV_TIMEOUT, None)
        if saved_s is not None:
            os.environ[ENV_SCRIPT] = saved_s
        if saved_t is not None:
            os.environ[ENV_TIMEOUT] = saved_t

    print("[board_diag] selftest PASS — %d 項斷言全過：" % n
          + "parse_diagnose（schema pin diag.diagnose.v1／asset_id alias／設備軸 present in|out／"
            "equipment.eqf.* 命名空間隔離／top_codes 排序）；additive 容忍（未知頂層鍵/未知 source 不崩、"
            "不溜進設備軸）；extract_equipment_axis（hardware_failure 候選 + 三態校準彙總）；"
            "schema 拒讀/no_json/bad_json；build_diagnose_cmd（--ast/--json、--days vs --start/--end、"
            "opt-in --equipment-failure-csv）；classify_eqf 三態（注入表 str/dict 兩式、非法值保守）；"
            "load_eqf_seed（GOLDEN real_fault→actionable/nuisance→nuisance/insufficient_data→unvetted、"
            "richer 欄 hint/ratio/matched_wos、§24 載入值無人員欄、缺檔/缺 codes[] graceful 空 dict）；"
            "bundled 真種子 EQF_CALIBRATION 40 碼三態彙總 a14/n5/u21（eqfClean_TempOutOfRange→"
            "nuisance、eqfCLN_DiWaterSensorStuck→actionable）；run_diagnose 全 (None,reason) 誠實降級"
            "（not_configured/script_missing/timeout 不重打/spawn_error/exit_<rc>/schema_rejected/"
            "bad_window、env 覆寫 timeout+script）。**零 subprocess/零網路/不連 MES**（全注入 fake runner）；"
            "只執行 remote-diag CLI + 解析其凍結 JSON、不 fork 其邏輯（B8 D2）。")


# ---------------------------------------------------------------------------
# 沉默點 / 待對版清單（消費被迫自選之處；接通 down-詳情面板前對版、勿自行發明填補）
# ---------------------------------------------------------------------------
# R1  JSON 抽取＝Diag §1「最後一段非空 `{` 起始行」（金樣本皆單行 compact）。若 Diag 日後改 pretty
#     multiline，§1 契約即破——屆時對版、勿在此擅自改成「從首 `{` 到結尾」猜法。
# R2  schema pin＝accept-only diag.diagnose.v1（schema_version alias 亦收）。破壞性變更 Diag 必 bump v2
#     ＋走 §19.6 push；additive 新欄本檔天然容忍（未知鍵忽略）。
# R3  exit code：本檔跑 diagnose（0=成功即使 MES 斷、非零=真錯）。**fleet_diagnose 的 exit=失敗站數、
#     非零時 stdout 仍是有效部分 JSON（§1）——若日後加 run_fleet 消費，勿套本檔「非零即丟」規則**。
#     （2026-07-07 Diag relay：此契約落差 Diag 端已補實作、我方防呆本就對；run_fleet 尚未建、規則先記著。）
# R4  observed_at 可 None（§1 沉默點①：CLI 邊界才保證非 null；library import 可空）——消費端容忍、
#     自蓋收訖時刻（本 stub 不蓋、只忠實傳遞 None）。
# R5  設備軸 events「present 但 status=unavailable」＝in-scope 無 CSV（誠實），events 空非「無故障」；
#     UI 渲染須分辨「未 pull（unavailable）」vs「pull 到 0 事件」（B8② 誠實覆蓋、勿當沒問題）。
# R6  EQF_CALIBRATION 種子＝**Diag/cmms B10 objective seed 已到位**（eqf×工單交叉核對 top-40/365d、
#     `_fixtures/eqf_crosscheck_seed_all107_365d.json` byte-copy、import 時 load_eqf_seed 載入）。
#     verdict_hint 是 HINT 非最終裁決＝Board classifier（B3 owner）策展；insufficient_data→unvetted
#     （不隱藏）、nuisance→de-weight＋標記（不丟棄）。cmms 增補新種子＝換檔即可（additive、無碼改）。
#     缺檔 graceful（EQF_CALIBRATION_LOADED=False、全回 unvetted、不崩）。
#     ★2026-07-07：Diag 亦已 live 消費同一份 cmms 種子（--nuisance-seed opt-in），其 standalone
#     failure_seed.v1 輸出嵌 top[].workorder_crosscheck.verdict_hint + calibration block。
#     **Board 保持 B3 獨立分類、不 defer Diag 嵌入 hint**（兩邊同源故一致；Diag hint 是交叉驗證非權威）。
#     新欄全 additive 容忍（selftest G4 對版：金樣本 byte-copy `failure_seed.v1.nuisance.sample.json`、
#     新欄不溜進 event、nuisance_codes_applied ⊆ Board nuisance）。golden 值合成、勿對非 nuisance 碼值等斷言。
# R7  metrics 內鍵 opaque（§2-J：diagnose 頂層 rate_*=fraction_0_1 vs 設備軸 source-local
#     share_pct=percent_0_100）——本檔只搬設備軸 share_pct、不跨軸讀 rate_*，避免 100× 地雷。
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="remote-diag diag.diagnose.v1 設備軸消費端 stub（B8 D2 subprocess 消費；"
                    "現階段 --selftest 離線自驗，plain 只印設定狀態）。")
    ap.add_argument("--selftest", action="store_true",
                    help="離線零 subprocess 自驗（注入 fake runner + 讀本地 byte-copy fixture）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    script = os.environ.get(ENV_SCRIPT, "").strip()
    if not script:
        print("[board_diag] disabled（未設定）：需 env BOARD_Diag_SCRIPT=<remote-diag diagnose.py 絕對路徑>"
              "（+ 可選 BOARD_Diag_TIMEOUT 秒）。B8 D2：on-demand subprocess 跑其 CLI、解析 diag.diagnose.v1"
              " JSON、不 fork 其邏輯。live 由 Chris 在域內機器跑（Claude 不連 MES）。離線驗證：--selftest。")
    else:
        print("[board_diag] configured：script=%s（timeout=%.0fs）。設備軸消費就緒；"
              "**未接進儀表板**（down-詳情面板＝B8 附錄、非當前批次）。" % (script, _resolve_timeout(None)))


if __name__ == "__main__":
    main()
