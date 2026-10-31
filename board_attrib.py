#!/usr/bin/env python3
"""Tendline Board — /daily 昨日缺口「時間帳本歸因」引擎 `board.attrib.v1`。規格＝`SPEC-DAILY-ATTRIB.md`。

**回答的問題**：昨天這站「沒在生產的機台時數」到底花到哪去了——
逐段歸因成 **工單停機／斷料／下游堵／未解釋** 四桶，讓「就是沒達標」升級成
「缺 120 顆＝維修 4.2h(~85顆)＋斷料 1.5h(~30顆)＋未解釋 0.3h」的可決策診斷句。
**不是什麼**：不是稼動頁（`/utilization` 一字不動）、不是良率口徑（那是 `mes_qlik`）、
不是考勤/人力工具（§24；本檔零人員欄，連計數欄都沒有＝T7）。

本檔＝**B1 的純函式歸因引擎**（零 I/O、零 MES、零 HTTP、零檔案；`--selftest` 全離線）。
日帳落地（`_state/daily_ledger/*.json`）的**文件形狀**由本檔定義，**實際讀寫由 B2 做**。

★ 鐵則（SPEC §1；違反即 HIGH）
  T1 **帳本恆等式**：`fleet_n × 24h ＝ 生產 ＋ wo_union_h ＋ 全廠停線 ＋ 斷料 ＋ 下游堵 ＋ 未解釋`
     （≤1e-6h）。恆等式的工單項＝`wo_union_h`（countable interval-union、**先扣生產重疊**）；
     S12 分態時數 `wo_h` 只做顯示組成（Σ 可 > union）、**絕不進恆等式**。
     **機隊名冊 pin**：`昨日 timeline fleet ∪ 當日有 countable 工單的 EID`——整天在修、零生產段的
     機台不在 `fetch_fleet` 名冊裡，不補＝整台 24h 從帳本消失、維修桶系統性低估。
     本引擎**自己也補一次**（`wo_segs` 出現的 EID 一律入帳），呼叫端補漏也不會靜默掉帳。
     ★ **G2 不改分母**（仍 `fleet_n × 24h`）：closure 只是把原本落在未解釋/斷料/下游堵的
       時數**移進新桶**（SPEC §7 G2；「分母改排定時間」仍待 G1，本頁不做）。
  T2 **不雙計**：優先序 **生產 ＞ 工單 ＞ 全廠停線(G2 日曆 closure) ＞ 斷料 ＞ 下游堵 ＞ 未解釋**；
     工單與生產重疊的時間歸生產、差額由 `wo_overlap_prod_h` 揭露（＝與 /equipment 對帳時的
     解釋項）。**工單排在 closure 之上＝週末進廠維修是真的在修**（最強證據優先）。
  T3 **殘差誠實**：殘差桶標籤恆為「未解釋（可能人力/排班/其他）」。**絕不因為排除了其他原因
     就標成人力**——缺證據≠人力。升格唯一路徑＝G1 排班檔（SPEC §7；D-OP3：operator 訊號
     長在 segment 列上 ⇒「閒置且無人操作紀錄」恆真、零鑑別力，**永不得用於本頁殘差**）。
  T4 不依賴 target：歸因錨在損失時間（真值）；`est` 顆數＝溝通用估算（顯示恆帶 ≈）。
  T5 口徑軸＝**設備軸 raw**（沿用 S12／凍結 timeline 語意）。
  T6 **量不到顯「—」**：零工期八站／整窗無訊號 → `unmeasurable=True`、各桶 `None`，**絕不填 0**。
  T7 §24：本檔輸出零人員資料（帳本／日帳文件／demo 皆無人員欄）。
  T8 **凍結模組 `mes_timeline.py`/`mes_yield.py` 不 import、不執行、零 diff**；
     設備軸模組（`board_eqmeeting`/`board_w8`）與 `mes_wip`/`mes_qlik` 只 import 重用、零改動。

★ 兩層分離（SPEC §3；覆核 HIGH「網格與恆等式相容性」）
  1. **量**：閒置秒數一律**連續精確量**＝`total_mh − 生產 − wo_union`；未解釋＝**精確殘差**。
  2. **標籤**：網格只決定「每段閒置時間貼哪個標籤」，入桶量＝該片**實際閒置秒數**
     （**絕非名目 grid_min**）⇒ 恆等式恆成立、無捨入破口。

★ as-of 陷阱（SPEC §2，覆核 HIGH）：`mes_wip.latest_positions` **不吃 cap**（位置＝全列集
  max stpPK）⇒ 每個網格點 `t_i` 必須先把 rows 過濾成 `ts < t_i` 再重放。本檔提供
  `rows_asof` / `replay_series` 兩個純函式把這件事收口（selftest 釘「未來列不得影響 t_i 位置」）。

CLI：`python board_attrib.py --selftest`（純離線、不連任何東西）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import mes_qlik as mq                 # 43 站序權威（站序只有一份、絕不另抄）
import mes_wip as mw                  # WIP 重放（derive_wip 純函式）＋ pass-through 人工審定常數
import board_eqmeeting as wem       # S12 工單段口徑 + 時戳/union helper（唯讀重用、不改檔）
import board_metrics as wmet        # A4（#138）：歸因桶標籤／口徑句的唯一出處（純常數、零 I/O、零 app 匯入）
import board_oee as woee           # 停機態白名單的**唯一出處**（絕不在本檔重打一份；
#                                       `board_oee` 只 import random/sys ⇒ 無相依環）
#                                       · `RW_AVAIL_DOWN_STATES`＝妥善率尺（v5 `avail_down_sec`）
#                                       · `RW_HEALTH_STATES`   ＝舊窄稼動尺（v5 相容欄）
import core_source as _wrs             # 來源值／進程旗標唯一出處（合成產線 Q-C；零相依）
import board_w8 as w8               # canonical 工單態名（不自己再寫一份字串）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# =============================================================================
# 1. 常數 / env（預設值即 pin 值；env 只在 live 校準時動＝SPEC §10 Q1/Q2）
# =============================================================================
SCHEMA_ID = "board.attrib.v1"
LEDGER_SCHEMA_VER = "board.daily_ledger.v1"
CALIBER = ("daily time ledger (equipment axis / raw; work orders per S12; "
           "starved & blocked split by MES-WIP replay; residual = unexplained)")

PROD_STATE = "生產"                    # §23 canonical key（不翻、不改）

# 四個損失桶（顯示順序＝T2 優先序＝規格敘述順序；`top` 依時數排序、不依此序）。
#   ★ 2026-08-23 A5 形 (i)（`SPEC-RUNWINDOW.md` §4.3；存證＝`SPEC-DAILY-ATTRIB.md` §8.4）：
#     **`closure` 桶退場**——全廠停線已在分母端被扣掉（該跑窗＝班次窗∪擴窗−closure），
#     再當桶＝算兩次。#35 的 closure 偵測成果**沒有消失**：它從「帳本裡的一桶」變成
#     「分母的扣減項」（同一份 closure 資料的兩種用法）。舊日帳的 `closure_h` 讀端照舊
#     `.get() or 0.0` 雙收（identity_error 對新舊兩形都成立）。
#     C-PC10 命名鐵則照舊：**永不叫「沒排班」/「班表」/schedule**（G1 真排班檔仍不存在）。
BUCKETS = ("wo", "starved", "blocked", "unexplained")

# 不可測 note key（**穩定 key、非文案**；UI 以 i18n 對映＝R4 前端鐵則）
NOTE_ZERO_LEN = "zero_len"            # 零工期八站：段長法結構性不可測
NOTE_NOSIG = "nosig"                  # 整窗零訊號且零工單
NOTE_NO_FLEET = "no_fleet"            # 機隊為空（連名冊都沒有）
NOTE_NO_WINDOW = "no_window"          # 推不出觀測窗
# 來源側失敗（該站 timeline 當時查不到／預算跳站）＝**不是「名冊上沒有機台」**。
#   ★ 兩者渲染成同一句＝把「我沒查到」說成「那站沒開線」（R5 誠實鐵則：降級要講對原因）。
NOTE_SRC_ERR = "src_err"
# A8（SPEC-RUNWINDOW §4.3）：該站機隊**全數**不在綑名冊 ⇒ 該跑窗不適用 ⇒ 整站「不適用」
#   （不是 0、不是 24h、不進可行動損失）。逐站少數非名冊機台走 diag.na_*（細目、不進分母）。
NOTE_NOT_IN_KUN = "not_in_kun"

# ★ #182-P2 F2（2026-09-25；`_INVEST-NOSIG-DRIFT-20260925.md` §6、T6 一致化）：
#   **零工期八站＝站級常數**（段 100% 零工期＝段長法結構性不可測）。人工審定清單、
#   **永不 runtime discovery**（charter §1.3；記憶 mes-zero-length-segments §2–§3；
#   出處＝`_CODE-REVIEW-FINDINGS.md` M4 表＝04-mes-text2sql 400 天普查、八站皆在 `EQP_STATIONS`）。
#   為什麼要常數而不是沿用「全機隊 `zero_len_only`」閘：那道閘要求 `n_zlo == fleet_n`，
#   一台零工期機＋一台 C-RW18 名冊補列 stub（整窗 `不明`）就過不了 ⇒ 變成「可測、0 生產、
#   整窗分母」（09-14/15/16 GroutThermalCure 的舊檔形）。F1 讓 stub 變多，不加這條就會把
#   零工期站每天 ~33 h 的「0 生產分母」灌進 U。**由呼叫端以 `zero_len_classes=` 傳入**
#   （`board_data.build_attrib_board`＝live 日帳路徑）；demo 板不傳＝合成劇本逐位元不變。
ZERO_LEN_STATIONS = frozenset({'segUVNitrogenCheck',
                               'segPotting',
                               'segFinalOQC',
                               'segAttachCureOven',
                               'segQASetup'})

# S12 countable 態鍵集（T1：`wo_h` 鍵集＝**全部 countable 態、含兩個 hold 態**）。
# ★ 由 w8/wem 的常數推導，**不在本檔重打字串**（漂移會靜默改變組成條的語意）。
COUNTABLE_WO_STATES = tuple(dict.fromkeys(
    [w8.wo_state(wt)[0] for wt in ("REACTIVE", "PM", "CONVERSION")]
    + [w8.BUCKET_STATE] + list(wem.HOLD_DOWN_STATES.values())))


def _env_int(name, default):
    """env 覆寫（typo 不炸開機＝R1 審查輪教訓：壞值一律退回預設）。"""
    try:
        v = int(str(os.environ.get(name, "")).strip())
        return v if v > 0 else default
    except Exception:
        return default


def _env_float(name, default, allow_zero=False):
    try:
        v = float(str(os.environ.get(name, "")).strip())
        return v if (v > 0 or (allow_zero and v >= 0)) else default
    except Exception:
        return default


GRID_MIN = _env_int("BOARD_ATTRIB_GRID_MIN", 30)        # 網格粒度（分）＝48 點/日
KEEP_DAYS = _env_int("BOARD_ATTRIB_KEEP_DAYS", 90)      # 日帳保留天數（SPEC Q3）
PARETO_DAYS = _env_int("BOARD_ATTRIB_PARETO_DAYS", 30)  # Pareto 窗（SPEC Q4：v0 固定 30d）

TH = {
    # C1 斷料：`queue+stale == 0` ∨ `cover < 0.5h`（**與 /dispatch thin(1h) 刻意不同、名字分開**）
    "starved_cover_h": _env_float("BOARD_ATTRIB_STARVED_COVER", 0.5),
    # C2 下游堵：下游 `cover > 4h`
    "blocked_cover_h": _env_float("BOARD_ATTRIB_BLOCKED_COVER", 4.0),
    # C10 活動鄰近 gate（off-shift 防護）：t_i ±2h 內要有生產訊號才可判斷料/下游堵
    "active_near_h": _env_float("BOARD_ATTRIB_ACTIVE_NEAR_H", 2.0, allow_zero=True),
    # C8 chips 雙軌 gate 的時數腿
    "chip_min_lost_h": _env_float("BOARD_ATTRIB_CHIP_MIN_LOST_H", 1.0),
}


def thresholds(**over):
    """預設判準 + 覆寫（未知鍵一律忽略＝env/呼叫端 typo 不會靜默改變語意）。"""
    th = dict(TH)
    for k, v in (over or {}).items():
        if k in th and v is not None:
            th[k] = float(v)
    return th


def _th(th):
    """呼叫端可傳完整/部分 th；一律過 `thresholds` 正規化（且不 mutate 模組常數）。"""
    return thresholds(**(th or {}))


# =============================================================================
# 2. 43 站序：下游站語意（C2/C9）——站序只有一份＝`mes_qlik.QLIK_STATIONS`
# =============================================================================
def route_index():
    """{cls: ord}（43 站；cls 互異＝mes_wip selftest 已釘）。"""
    return {c: o for o, c, _m in mq.QLIK_STATIONS}


def _ord_cls():
    return {o: c for o, c, _m in mq.QLIK_STATIONS}


def downstream_of(cls, passthrough=mw.WIP_PASSTHROUGH_ORDS):
    """route 上的**下一個非 pass-through 站** cls；線尾／不在 route → None（C2）。

    ★ 語意權威＝43 route 序，**不是 /daily 的顯示序**（顯示序有多處與 route 倒置）。
      pass-through 名單重用 `mes_wip.WIP_PASSTHROUGH_ORDS`（人工審定、絕不 runtime 推斷）。
    """
    idx = route_index()
    o = idx.get(cls)
    if o is None:
        return None
    pt = frozenset(passthrough or ())
    by_ord = _ord_cls()
    for j in sorted(x for x in by_ord if x > o):
        if j not in pt:
            return by_ord[j]
    return None


def assert_route_coverage(cls_list, label="BOSS_STATIONS"):
    """C9 載入時守衛：清單裡每個 cls 都要在 43 站序內，否則**大聲炸**。

    不這樣做的話，日後有人改一個 cls 名字 → 下游對映靜默變 None → 下游堵桶恆 0
    而**不會有任何錯誤訊息**（＝最壞的靜默謊）。呼叫端（`board_daily`）在模組載入時呼叫。
    """
    idx = route_index()
    bad = [c for c in (cls_list or []) if c not in idx]
    if bad:
        raise KeyError(f"{label} 有 cls 不在 43 站 route 序（C9 守衛）：{bad}")
    return True


# =============================================================================
# 3. 區間代數（純函式；帳本的精確量都靠這幾支）
# =============================================================================
def _dt(v):
    """時戳 → naive 台北牆鐘 datetime（重用 wem 的解析器，不再寫第三份）。"""
    return wem._coerce_dt(v)


def _merge(ivs):
    """[(a,b)…] → 排序後合併重疊的互斥區間（壞/零長區間丟棄）。"""
    clean = []
    for a, b in ivs or ():
        if a is None or b is None or b <= a:
            continue
        clean.append((a, b))
    clean.sort()
    out = []
    for a, b in clean:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def _clip(ivs, lo, hi):
    out = []
    for a, b in ivs or ():
        a2 = a if a > lo else lo
        b2 = b if b < hi else hi
        if b2 > a2:
            out.append((a2, b2))
    return out


def _sub(base, cut):
    """base（互斥升冪）減去 cut（任意）→ 互斥升冪。"""
    cut = _merge(cut)
    out = []
    for a, b in base:
        cur = a
        for c, d in cut:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append((cur, c if c < b else b))
            if d > cur:
                cur = d
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return out


def _inter(x, y):
    """兩組區間的交集（皆先 merge）。"""
    x, y = _merge(x), _merge(y)
    out, i, j = [], 0, 0
    while i < len(x) and j < len(y):
        a = x[i][0] if x[i][0] > y[j][0] else y[j][0]
        b = x[i][1] if x[i][1] < y[j][1] else y[j][1]
        if b > a:
            out.append((a, b))
        if x[i][1] < y[j][1]:
            i += 1
        else:
            j += 1
    return out


def _hours(ivs):
    return sum((b - a).total_seconds() for a, b in (ivs or ())) / 3600.0


def _r6(x):
    """帳本一律 6 位小數（＝3.6 毫秒）；**未解釋桶由殘差反算**⇒ 恆等式誤差 ≤5e-7h < 1e-6h。"""
    return None if x is None else round(float(x), 6)


# ── 全廠停線 closure（G2；SPEC-PROD-CALENDAR）────────────────────────────────────
def norm_closures(closures, lo, hi):
    """`[{"start","end","uncertain_eids"}…]` → `[(a, b, frozenset(uncertain))…]`（clip 到窗）。

    · 時刻走 `_dt()`＝**台北牆鐘 naive**（呼叫端負責把 wire UTC Z 轉過來；轉換一條路住
      `board_data`）。壞/零長/窗外一律丟棄（**絕不猜**）。
    · `uncertain_eids`＝C-PC12 兜底：該 closure 期間**該綑有 countable 工單活躍** ⇒ 整綑
      成員 EID 進這個集合；那些機台的該段時間**不判停線也不判停機**、改記「不確定」
      （落未解釋殘差＋`diag.closure_uncertain_h`）。集合空＝該 closure 對全機隊都成立。
    · 不做 merge：closure 各自帶自己的 uncertain 集合，合併會把兩個集合搞混
      （重疊時的「不確定優先」由 `_closure_split` 逐機處理）。
    """
    lo, hi = _dt(lo), _dt(hi)
    out = []
    for c in closures or ():
        if not isinstance(c, dict):
            continue
        a, b = _dt(c.get("start")), _dt(c.get("end"))
        if a is None or b is None:
            continue
        a = a if a > lo else lo
        b = b if b < hi else hi
        if b <= a:
            continue
        unc = c.get("uncertain_eids") or ()
        out.append((a, b, frozenset(e for e in unc if isinstance(e, str) and e)))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def _closure_split(idle, key, closures_n):
    """單機閒置片 ∩ closure → `(certain, uncertain, rest)`（三組互斥區間、精確秒數）。

    ★ 重疊 closure 的裁決＝**不確定優先**：同一秒若在「本機不確定」的 closure 內，
      就算另一個 closure 對本機成立也一律算不確定（保守側＝不主張停線）。
    """
    unc_iv = _merge([(a, b) for a, b, u in closures_n if key in u])
    cert_iv = _sub(_merge([(a, b) for a, b, u in closures_n if key not in u]), unc_iv)
    cert = _inter(idle, cert_iv)
    unc = _inter(idle, unc_iv)
    return cert, unc, _sub(idle, _merge(cert + unc))


# =============================================================================
# 4. 網格 / WIP 重放（as-of 陷阱收口）
# =============================================================================
def grid_points(win_start, win_end, grid_min=GRID_MIN):
    """[win_start, win_end) 內每 `grid_min` 分一點（含 win_start、不含 win_end）。"""
    a, b = _dt(win_start), _dt(win_end)
    if a is None or b is None or b <= a:
        return []
    step = timedelta(minutes=max(1, int(grid_min or GRID_MIN)))
    out, t = [], a
    while t < b:
        out.append(t)
        t = t + step
    return out


def rows_asof(rows, t, ts_index=mw.I_TS):
    """`ts < t` 的列（as-of 裁切；**排序仍靠 stpPK、ts 只做裁切**＝SPEC §2）。

    ts 為 None 的列一律排除（SQL 路徑結構上不可達；合成資料才有）——把「無時戳」的列
    塞進每一個 as-of 前綴，等於讓它在 00:00 就存在＝憑空發明位置。
    """
    t = _dt(t)
    out = []
    for r in rows or ():
        if len(r) <= ts_index:
            continue
        ts = _dt(r[ts_index])
        if ts is not None and ts < t:
            out.append(r)
    return out


def replay_series(rows, points, *, cls_list=None, route=None, **wip_kw):
    """48 點 WIP 重放 → `{cls: [(t_i, cover_h, queue_n, stale_n), …]}`（**零額外 SQL**）。

    每點 `t_i`：`derive_wip(rows_asof(rows, t_i), cap=t_i, …)`
    ——**先過濾再重放**（`latest_positions` 不吃 cap＝SPEC §2 as-of 陷阱）。
    · rows 先按 ts 排一次 → 逐點用**前綴指標**推進（O(rows·log rows + Σprefix)、不重掃全表）。
    · `cls_list=None` ⇒ 回全 43 站；給清單則只留那些站（省記憶體，語意不變）。
    · `**wip_kw` 直通 `mes_wip.derive_wip`（`age_cap_d`/`rate_win_h`/`has_status`…）。
      cover 用的速率窗錨在 `t_i`（＝「此刻這站吃得完排隊的小時數」），與 /dispatch 同式。
    """
    keep = None if cls_list is None else set(cls_list)
    pts = [p for p in (_dt(p) for p in (points or ())) if p is not None]
    pts.sort()
    ok_rows = [r for r in (rows or ()) if len(r) > mw.I_TS and _dt(r[mw.I_TS]) is not None]
    ok_rows.sort(key=lambda r: _dt(r[mw.I_TS]))
    ts_sorted = [_dt(r[mw.I_TS]) for r in ok_rows]

    out, i = {}, 0
    for t in pts:
        while i < len(ts_sorted) and ts_sorted[i] < t:
            i += 1
        wip = mw.derive_wip(ok_rows[:i], t, route=route, **wip_kw)
        for s in wip.get("stations") or ():
            cls = s.get("cls")
            if keep is not None and cls not in keep:
                continue
            out.setdefault(cls, []).append((t, s.get("cover_h"), s.get("queue_n"),
                                            s.get("stale_n")))
    return out


def _norm_series(series):
    """`[(t, cover_h, queue_n, stale_n)]` 或同名 dict → `{t: (cover, queue, stale)}` ＋升冪 t 清單。

    None／空 ⇒ `(None, [])`＝**WIP 源不可知**（不是「佇列為 0」）。
    """
    if not series:
        return (None, [])
    pts = {}
    for e in series:
        if isinstance(e, dict):
            t = _dt(e.get("t") or e.get("ts"))
            cov, q, s = e.get("cover_h"), e.get("queue_n"), e.get("stale_n")
        else:
            if len(e) < 2:
                continue
            t = _dt(e[0])
            cov = e[1]
            q = e[2] if len(e) > 2 else None
            s = e[3] if len(e) > 3 else None
        if t is None:
            continue
        pts[t] = (None if cov is None else float(cov),
                  None if q is None else int(q),
                  None if s is None else int(s))
    return (pts, sorted(pts)) if pts else (None, [])


# =============================================================================
# 5. 閒置片分類（§3 依序短路；每一條的 None 語意都是承重的）
# =============================================================================
def classify_idle(wip_pt, dn_pt, gate_ok, th=None):
    """單一網格點的閒置標籤 → `"starved"|"blocked"|"unexplained"`（依序短路、先中先贏）。

    1. **活動鄰近 gate（C10）**：`gate_ok=False`（該站 t_i ±`active_near_h` 內無生產訊號）
       → 未解釋。＝排班源到位前的 **off-shift 防護**（夜班/週末 `queue==0` 會把大片未排班
       時間誤標「斷料」）。SALTMARSH 排班檔進來後此 gate 由排定時間取代。
    2. **斷料**：`(queue_n + stale_n) == 0`（**含 stale**：門口有超齡老料就不算斷料）
       ∨ `cover_h < starved_cover_h`。
       **`cover_h is None`（rate 缺）時只剩 queue+stale 那一腿**——None **絕不當 0**。
       queue/stale 任一為 None（不可知）時該腿也不成立（不可拿未知主張「門口空了」）。
    3. **下游堵**：下游 `cover_h > blocked_cover_h`。
       **下游 cover None／下游不存在（線尾）→ 不可判 → 未解釋**（None 絕不當 0 也不當 ∞）。
    4. 其餘 → 未解釋。
    """
    th = _th(th)
    if not gate_ok:
        return "unexplained"
    if wip_pt is None:                     # 本站 WIP 狀態不可知 ⇒ 不主張任何原因
        return "unexplained"
    cov, q, s = wip_pt
    if q is not None and s is not None and (q + s) == 0:
        return "starved"
    if cov is not None and cov < th["starved_cover_h"]:
        return "starved"
    if dn_pt is not None:
        dn_cov = dn_pt[0]
        if dn_cov is not None and dn_cov > th["blocked_cover_h"]:
            return "blocked"
    return "unexplained"


def _cell_of(t_sorted, t):
    """升冪點清單中 ≤ t 的**最後一點**（as-of 取值）；t 早於首點 → None。"""
    lo, hi, ans = 0, len(t_sorted) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if t_sorted[mid] <= t:
            ans = t_sorted[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


# =============================================================================
# 6. build_ledger — 歸因引擎本體（SPEC §3 API pin）
# =============================================================================
def _machine_key(m, i):
    eid = (m or {}).get("eid")
    return eid if isinstance(eid, str) and eid else f"?m{i}"


def _prod_intervals(m, lo, hi):
    """單機生產區間（clip 到窗）。records 已是 per-machine interval-union 的結果
       ⇒ 機內不重複計（與 `board_data.aggregate`／`au_chips` 同口徑）。"""
    ivs = []
    for r in (m or {}).get("records") or ():
        if not isinstance(r, dict) or r.get("state") != PROD_STATE:
            continue
        a, b = _dt(r.get("start")), _dt(r.get("end"))
        if a is None or b is None:
            continue
        ivs.append((a, b))
    return _merge(_clip(_merge(ivs), lo, hi))


def _wo_intervals(segs, lo, hi):
    """單機 countable 工單區間 + 分態原始時數（S12：`countable=False` 的 uptime-hold 不計）。

    分態時數＝**原始加總**（跨態重疊時 Σ 可 > union＝沿用 /equipment footer 口徑）。
    """
    ivs, by_state = [], {}
    for s in segs or ():
        if not isinstance(s, dict) or not s.get("countable", True):
            continue
        a, b = _dt(s.get("start")), _dt(s.get("end"))
        if a is None or b is None:
            continue
        a2 = a if a > lo else lo
        b2 = b if b < hi else hi
        if b2 <= a2:
            continue
        ivs.append((a2, b2))
        st = s.get("state")
        by_state[st] = by_state.get(st, 0.0) + (b2 - a2).total_seconds() / 3600.0
    return _merge(ivs), by_state


def _wo_intervals_by_state(segs, lo, hi, base=None):
    """單機 countable 工單的**逐態秒數**：同態**先各自 union、再 ∩ `base`**（→ `{state: sec}`）。

    ★ 為什麼不能用 `_wo_intervals` 回的 `by_state`：那一份是**原始加總**（跨段重疊時 Σ 可 >
      union＝/equipment footer 的顯示口徑），拿去當 `rw.health_down_sec` 會把同一台機同一段
      時間算兩次，讓窄稼動 A 被系統性低估。本函式與 `app.rw_station_payload` 逐字同形
      （同態 union → ∩ 應生產時段），**口徑只有一份**。
    · `base=None` ⇒ 不裁窗（只做「同態 union ∩ [lo,hi)」）；rw 軸下呼叫端一律傳 base＝RW∩窗。
    · 零秒態不入回傳（`{}` 而不是 `{"維修": 0.0}`＝別造沒發生過的態）。
    """
    per = {}
    for s in segs or ():
        if not isinstance(s, dict) or not s.get("countable", True):
            continue
        a, b = _dt(s.get("start")), _dt(s.get("end"))
        if a is None or b is None:
            continue
        a2 = a if a > lo else lo
        b2 = b if b < hi else hi
        if b2 <= a2:
            continue
        per.setdefault(s.get("state"), []).append((a2, b2))
    out = {}
    for st, ivs in per.items():
        u = _merge(ivs)
        if base is not None:
            u = _inter(u, base)
        sec = sum((y - x).total_seconds() for x, y in u)
        if sec > 0:
            out[st] = sec
    return out


def _wo_union_sec(segs, lo, hi, base, states):
    """單機**白名單態的跨態 union** ∩ `base` → 秒（M3 v5：`rw.avail_down_sec` 的口徑）。

    ★ 與 `_wo_intervals_by_state` 的分工（兩個量、兩個問題，**不得互相頂替**）：
      · 本函式＝「這台機**總共**有多久是停的」——**跨態先合併再算**，兩張重疊的工單
        （例：維修單與等料單同時掛著）只算一次 ⇒ 這是 A 的分子（`/equipment` 妥善率、
        `board_avail.avail_model` 同一個口徑）。
      · `_wo_intervals_by_state`＝「各態各佔多久」——逐態各自 union、**跨態不合併**
        ⇒ Σ 可 > 本函式的值（事件視角，畫組成用）。
    ⚠ **白名單、不是「其餘皆停機」**（`board_oee.RW_AVAIL_DOWN_STATES` 的紀律）：
      不在名單裡的態一秒都不扣。純函式。
    """
    ivs = []
    for s in segs or ():
        if not isinstance(s, dict) or not s.get("countable", True):
            continue
        if s.get("state") not in states:
            continue
        a, b = _dt(s.get("start")), _dt(s.get("end"))
        if a is None or b is None:
            continue
        a2 = a if a > lo else lo
        b2 = b if b < hi else hi
        if b2 <= a2:
            continue
        ivs.append((a2, b2))
    u = _merge(ivs)
    if base is not None:
        u = _inter(u, base)
    return sum((y - x).total_seconds() for x, y in u)


def _derive_win(machines, wo_segs, wip_series):
    """窗未給時的推導：取所有可見時戳的最小值 → 落到台北午夜 → `[d, d+1)`（＝/daily 日曆窗）。

    **建議呼叫端一律明給 `win=`**；本推導只是防呆（推不出來 → `(None, None)`＝不可測）。
    """
    cands = []
    for m in machines or ():
        for r in (m or {}).get("records") or ():
            if isinstance(r, dict):
                cands.append(_dt(r.get("start")))
    for segs in (wo_segs or {}).values():
        for s in segs or ():
            if isinstance(s, dict):
                cands.append(_dt(s.get("start")))
    for e in wip_series or ():
        cands.append(_dt(e.get("t")) if isinstance(e, dict) else _dt(e[0]))
    cands = [c for c in cands if c is not None]
    if not cands:
        return (None, None)
    d = min(cands)
    lo = datetime(d.year, d.month, d.day)
    return (lo, lo + timedelta(days=1))


def _unmeasurable(cls, note, win, fleet_n, th, diag):
    """T6：不可測站的 canonical 形（各桶 **None**、絕不 0）。"""
    return {
        "cls": cls, "fleet_n": fleet_n,
        "total_mh": None, "producing_h": None, "wo_union_h": None, "wo_h": None,
        "wo_union_pre_h": None, "wo_overlap_prod_h": None, "wo_top": None,
        "closure_h": None,
        "starved_h": None, "blocked_h": None, "unexplained_h": None, "lost_h": None,
        "est": None, "unmeasurable": True, "note": note, "top": [],
        "win_start": win[0].isoformat() if win[0] else None,
        "win_end": win[1].isoformat() if win[1] else None,
        "th": dict(th), "schema": SCHEMA_ID, "caliber": CALIBER,
        "diag": dict(diag, caveats=list(dict.fromkeys(diag.get("caveats") or []))),
    }


def unavailable_ledger(cls, note=NOTE_SRC_ERR, *, win=None, th=None, diag=None):
    """**來源側失敗**的站 → 不可測帳本（各桶 None、`note` 預設 `src_err`）。

    ★ 為什麼要有這支：呼叫端（`board_data.build_attrib_board`）遇到「該站 timeline 查詢
      失敗／整體預算跳站」時，若只是把 `machines=[]` 丟進 `build_ledger`，引擎會誠實回
      `no_fleet`＝「窗內本站名冊上沒有機台」——但事實是**機台存在、只是我們沒查到**。
      兩者在會議上讀起來天差地遠（後者會被讀成「那站昨天沒開線」）⇒ 分開的 note key。
    · `fleet_n` 恆 0（我們確實不知道有幾台），不猜任何桶。
    """
    lo, hi = (win or (None, None))
    d = dict(diag or {})
    d["caveats"] = list(d.get("caveats") or []) + [
        "station source unavailable (timeline query failed or skipped by budget) - "
        "this is NOT the same as an empty roster"]
    return _unmeasurable(cls, note, (_dt(lo), _dt(hi)), 0, _th(th), d)


MIN_PROD_MH = 0.5      # 換算率分母下限（機台小時）；呼叫端可用 rate_ctx["min_prod_mh"] 覆寫
                       # （app 端接 env `BOARD_ATTRIB_MIN_PROD_H`；board_unit 的 r_y 同一把尺）


def _rate_per_mh(rate_ctx, producing_h):
    """C5 **per-machine-hour** 速率（→ `(rate|None, src)`）。

    ★ 反模式（§9）：**站級 rate × 機台小時**會把 est 放大 `fleet_n` 倍。這裡的單位一律是
      「每一個**機台小時**產幾顆」＝`昨日 distinct lot ÷ 昨日生產機台小時`（＝
      `oee.actual_rate_uph` 語意）。分母預設就用本帳本自己的 `producing_h`＝分子分母同窗同站。
    · `rate_ctx` 接受：`{"rate_per_mh":…}` 直給／`{"lots_day":…}`／`{"rate_uph_y24":…}`
      （y24 錨的站級 lot/h → ×24 還原成當日顆數）；可另給 `prod_mh` 覆寫分母。
    · 分母 ≤0（零生產機台小時）→ `(None,'no_prod_hours')`；`lots_day==0` 且分母>0 →
      **0.0（量到了就是零）**，不是 None（比照 `oee.actual_rate_uph` 的 0 vs None 分野）。
    ★ **分母下限（2026-08-02 live 事故）**：零工期／瞬時段站的 `producing_h` 可能是 0.0017h
      這種數量級 → `237 ÷ 0.0017 ≈ 14 萬顆/機台小時`，est 直接顯「≈3,411,881 顆」＝整頁信任
      歸零。分母 < `min_prod_mh`（預設 0.5、rate_ctx 可覆寫）→ `(None,'thin_prod_hours')`
      ＝顯「—」不換算。**直給 `rate_per_mh` 的路徑不套下限**（demo/測試的受信輸入）。
    """
    if not rate_ctx:
        return (None, None)
    try:
        if rate_ctx.get("rate_per_mh") is not None:
            return (float(rate_ctx["rate_per_mh"]), rate_ctx.get("rate_src") or "given")
        lots = rate_ctx.get("lots_day")
        if lots is None and rate_ctx.get("rate_uph_y24") is not None:
            lots = float(rate_ctx["rate_uph_y24"]) * 24.0
        if lots is None:
            return (None, rate_ctx.get("rate_src") or "no_lots")
        denom = rate_ctx.get("prod_mh")
        denom = producing_h if denom is None else float(denom)
        if denom is None or denom <= 0:
            return (None, "no_prod_hours")
        if denom < float(rate_ctx.get("min_prod_mh", MIN_PROD_MH)):
            return (None, "thin_prod_hours")
        return (float(lots) / denom, rate_ctx.get("rate_src") or "y24")
    except Exception:
        return (None, "bad_rate_ctx")


def _seat_count(seats, key, field):
    """WP-C2：`seats` 裡某台機的座數（`installed`／`active`），**缺席一律當 1**。

       ⚠ 「缺席當 1」不是猜——`installed_units()` 的名冊裡沒有的機台**就是單座機**
       （`board_capacity` 的定義：一般機台＝1 座）。座帳整批算不出來時呼叫端傳 `None`／`{}`，
       那時整站就退回「每台 1 座」＝`seat_h ≡ producing_h` ⇒ `r_seat ≡ rate_per_mh`
       ＝**接線前的數字逐位元不變**（讀端據此永遠拿得到一個不會說謊的下限）。
       壞值（非整數／負數／None）同樣當 1，絕不炸整份帳。"""
    s = (seats or {}).get(key)
    if not isinstance(s, dict):
        return 1
    v = s.get(field)
    if v is None:
        return 1
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return n if n >= 0 else 1


def build_ledger(cls, machines, wo_segs, wip_series, dn_series, rate_ctx, *,
                 grid_min=GRID_MIN, th=TH, win=None, dn_cls=None, wo_source=None,
                 closures=None, rw_by_eid=None, seats=None, equiv_factor=None,
                 zero_len_classes=None, avail_rollup=None):
    """單站昨日時間帳本（SPEC §3 API pin；`win`/`dn_cls`/`wo_source`/`closures` ＝additive kw-only）。

    ★ `avail_rollup`（R44 C-AV16、additive kw-only；Fable 2026-09-29）＝`{形③ 父機 EID:
      {"avail_down_sec", "down_by_state", "excl_sec"}}`，由呼叫端（`board_data.
      build_attrib_board`）以 `board_eqmeeting.parent_seat_rollup` 算好（在役座推導：
      Σ 座停機 ∩ RW(座) ÷ n_在役）。**只在 rw 軸、只替換那幾台父機的 `rw.avail_down_sec`／
      `rw.down_by_state` 兩個加項**（`excl_sec`＝全座長停日自 `planned_sec` 排除）；
      `wo_union_h` 等恆等式項、`health_down_sec` 相容欄一個位元不動。`None`／`{}`／
      不在表內的 EID ⇒ 逐位元舊行為。本引擎只收數字、不 import `board_capacity`。
    ★ `zero_len_classes`（#182-P2 F2、additive kw-only）＝站級零工期常數集（通常就是
      `ZERO_LEN_STATIONS`）。`cls` 在裡面 ⇒ **有機隊就直接判 `zero_len`**（T6：段長法結構性
      不可測、不進分母、不進任何桶），不再看機隊裡有沒有混進 stub／純工單機。
      `None`（預設）＝逐位元舊行為（只有全機隊 `zero_len_only` 才判）。

    輸入
      · `cls`         站 canonical id（§23/43 route 的 key，不翻譯）。
      · `machines`    `[{"eid","records":[{state,start,end}…],"zero_len_only":bool}…]`
                      ＝該站昨日 per-machine 生產區間（timeline records）**＋名冊補集（T1）**。
                      本引擎另外把 `wo_segs` 出現但不在 `machines` 的 EID 一併補進機隊
                      （`diag.n_roster_added`）＝呼叫端漏補也不會靜默掉帳。
      · `wo_segs`     `{eid: [wem.wo_segments 段]}`（S12：只有 `countable=True` 進帳）。
                      **`None` ＝工單源降級**（cmms 當機）：此時**整片閒置一律灌未解釋**、
                      `wo_h=None`、`diag.wo_source='degraded'`（C6）——絕不拿「沒有工單」
                      當「沒有維修」，也絕不把維修時間誤標成斷料/下游堵。
      · `wip_series`  本站 `[(t_i, cover_h, queue_n, stale_n)…]`（`replay_series` 產）。
      · `dn_series`   **下游站**同形序列；`None`＝線尾或不可知 ⇒ 下游堵桶不可能成立（C2）。
      · `rate_ctx`    est 換算（見 `_rate_per_mh`）；`None`／不可得 → `est=None`、帳本小時照給。
      · `rw_by_eid`   ★ **#39 該跑窗分母**（A5 形 (i)；`SPEC-RUNWINDOW.md` §4.3）
                      ＝`{eid: [(start_dt, end_dt)…]}`＝該機本生產日的該跑窗絕對區間
                      （呼叫端以 `board_runwindow` 算好、**certain closure 已扣**）。
                      · **None ＝ 舊 24h 軸**（分母 `fleet_n × win_h`；inert fallback，
                        頁面顯著標示）——除了 closure 桶退場外行為與 #35 之前逐位相同。
                      · 有值 ⇒ 分母＝Σ|RW∩win|；**各桶皆 ∩ RW**（C-RW11 分子分母同窗）；
                        窗外生產／窗外工單**不入帳、逐項揭露**（diag.producing_outside_rw_h
                        ／wo_outside_rw_h——證據不丟、只是不在這本帳的問責範圍）；
                        eid 不在 dict ＝非名冊 ⇒ **不進分母**、整台記「不適用」細目
                        （diag.na_*；A8：不進任何損失桶、不進可行動損失）。
      · `closures`    全廠停線清單（`[{"start","end","uncertain_eids"}…]`）。
                      ★ A5 之後**只剩一個用途**＝C-PC12 兜底的「不確定」標記（rw 軸下
                      uncertain closure 沒被扣分母，其閒置改記未解釋＋
                      `diag.closure_uncertain_h`＝誠實殘差、不判停線也不判停機）。
                      **沒有 rw_by_eid 時傳 closures ⇒ 忽略＋caveat**（closure 桶已退場；
                      分母修正只有該跑窗一條路＝單一真相）。
      · `seats`       ★ **W3 座帳**（`SPEC-CAPACITY-FORECAST.md` §2；additive kw-only）
                      ＝`{eid: {"installed": N, "active": n, "dormant": [座 id…]}}`，
                      **由呼叫端用 `board_capacity.machine_seats()` 算好傳進來**
                      （C-CF3：座名冊的唯一住處是 `board_capacity`；本引擎不 import 它、
                      也不自己數座位——那條 import 鏈會把 `mes_timeline` 拖進來）。
                      · `None`／`{}` ⇒ **逐位元維持接線前的行為**（`rw` 不長 `seats`，
                        `planned_sec` 就是舊值；讀端據此判 `seats_legacy`）。
                      · 有值 ⇒ `rw` 多三欄，見下面回傳說明。
      · `equiv_factor` ★ **W1 停機當量座數係數**（0..1，additive kw-only）＝該站本日
                      逐 WO `board_capacity.equiv_units_weighted()` 按停機時數加權後的值，
                      同樣由呼叫端算好（本引擎只負責讓它進得了文件、一秒都不算它）。
                      `None` ⇒ 不寫這一欄 ⇒ 月級當 1.0（＝接線前的數字，一個位元不變）。
    回傳（SPEC §3 pin 欄位 ＋ 明確標示的 additive 欄位）
      `wo_union_h`＝**恆等式的工單項**（countable union、已扣生產重疊）；
      `wo_union_pre_h`＝**扣生產前**的 countable union ＝與 /equipment drill 對帳的那個數
      （§8 live 驗收項；兩者差＝`wo_overlap_prod_h`）；
      `wo_h`＝S12 分態顯示組成（鍵集＝全部 countable 態、Σ 可 > union、**不進恆等式**）。
      ★ **`rw`（M3 v4 additive、只有 rw 軸才有這個鍵；v5 再加三欄）**＝
      `{planned_sec, health_down_sec, producing_sec, n_eid, src:"erw",
        avail_down_sec, down_by_state, eids}`（站級、單位**秒**）
      ★ 給了 `seats`／`equiv_factor` 才多的加法欄（W3／W1）＝
      `{seats:{installed,active,dormant[]}, planned_sec_nameplate, equiv_factor}`
      ＝月級 A/U/OEE 的材料（消費端一律轉呼 `board_oee.rw_split`、**不在別處重寫三行口徑**）。
      · `avail_down_sec`（**v5、A 的分子**）走白名單 `board_oee.RW_AVAIL_DOWN_STATES`
        （＝`/equipment` 妥善率同一把尺）、**跨態 union** ∩ RW。
      · `health_down_sec`（**v4 舊尺、v5 起純相容欄**）＝`RW_HEALTH_STATES`（維修／計畫保養）、
        同態先 union 再 ∩ RW。**新的消費者一律讀 `avail_down_sec`。**
      · `down_by_state`（v5）＝白名單各態各自 union ∩ RW（事件視角，Σ 可 > `avail_down_sec`）。
      · `eids`（v5）＝進分母的機台清單（排序）＝分母的可稽核性。
      ★ **WP-C2（2026-09-12；`SPEC-DAILY-ATTRIB.md` §8.6／`SPEC-RUNWINDOW.md` §6.3-b）＝
        三個加法欄，語意如下**（`rw` 軸恆有；`seats` 缺席時座數一律當 1）：
      · `by_eid`＝`{eid: {producing_sec, seats_active, seats_installed}}`
        ＝**逐機台的 ∩RW 生產秒數**（與站級 `producing_sec` **同一把尺、同一個迴圈**，
        Σ 對得上）＋ 那一天的座帳。**分母的可稽核性從「哪幾台」升級成「哪幾台各跑多久」。**
      · `seat_h`＝`Σ_機台 producing_h × seats_active`＝**座‧小時分母**（小時）。
      · `r_seat`＝站產出 ÷ `seat_h`＝**站級每座速率**（分子與 `rate_per_mh` **同一個**
        ＝`rate_per_mh × producing_h`，不另算第二份產出）。`rate` 不可得／`seat_h` 為 0
        ⇒ `None`（不猜）。
      ★★ **回歸性質（`_selftest` 有釘）**：全站皆 1 座 ⇒ `seat_h ≡ producing_h`（**同一個
        累加器、同序、×1** ⇒ 浮點逐位元相等）⇒ `r_seat ≡ rate_per_mh`。混座站（CAL／CLN／
        Leak Test 等）才會分岔——那正是 Chris 2026-09-12 要的那件事。
      **這一整塊不是恆等式的一項**（T1 一位元未動，`rw` 純屬揭露欄）。
    """
    th = _th(th)
    lo, hi = (win if win else _derive_win(machines, wo_segs, wip_series))
    lo, hi = _dt(lo), _dt(hi)
    wo_ok = wo_segs is not None
    wo_source = wo_source or ("ok" if wo_ok else "degraded")
    diag = {"wo_source": wo_source, "downstream": dn_cls, "caveats": []}
    # 防呆：`wo_segs=None` 但呼叫端說 `wo_source='ok'` ＝**矛盾輸入**。若照字面信任，降級短路
    # 不會觸發 ⇒ 維修時間會被網格分類成斷料/下游堵、而 `wo_union_h` 是 0（＝把「沒有工單資料」
    # 講成「沒有維修」的最壞形）。一律以**證據**（wo_segs 在不在）為準、並自揭。
    if not wo_ok and wo_source == "ok":
        wo_source = "degraded"
        diag["wo_source"] = wo_source
        diag["caveats"].append("caller declared wo_source='ok' with no work-order segments "
                               "-> forced to degraded (evidence wins over the label)")

    if lo is None or hi is None or hi <= lo:
        return _unmeasurable(cls, NOTE_NO_WINDOW, (None, None), 0, th, diag)

    # ---- 機隊名冊（T1：timeline fleet ∪ 有 countable 工單的 EID）--------------
    machines = list(machines or ())
    fleet = []                                   # [(key, machine dict|None)]
    seen = set()
    for i, m in enumerate(machines):
        k = _machine_key(m, i)
        if k in seen:
            diag["caveats"].append(f"duplicate machine key {k} in input (kept once)")
            continue
        seen.add(k)
        fleet.append((k, m or {}))
    added = []
    for eid, segs in sorted((wo_segs or {}).items()):
        if eid in seen or not isinstance(eid, str) or not eid:
            continue
        if any(isinstance(s, dict) and s.get("countable", True) for s in segs or ()):
            seen.add(eid)
            fleet.append((eid, {"eid": eid, "records": []}))
            added.append(eid)
    diag["n_roster_added"] = len(added)
    diag["roster_added"] = added
    diag["n_machines_in"] = len(machines)

    fleet_n = len(fleet)
    win_h = (hi - lo).total_seconds() / 3600.0
    diag["win_hours"] = round(win_h, 6)
    if fleet_n == 0:
        return _unmeasurable(cls, NOTE_NO_FLEET, (lo, hi), 0, th, diag)

    n_zlo = sum(1 for _k, m in fleet if m.get("zero_len_only"))
    diag["n_zero_len_machines"] = n_zlo
    if zero_len_classes and cls in zero_len_classes:
        # F2：站級常數（T6 一致化）——機隊裡混了 stub／純工單機也一樣是「—」。
        diag["zero_len_station"] = True
        diag["caveats"].append("zero-length station (station-level constant, T6): segments "
                               "carry no duration, utilization is structurally unmeasurable")
        return _unmeasurable(cls, NOTE_ZERO_LEN, (lo, hi), fleet_n, th, diag)
    if n_zlo == fleet_n:                     # 零工期八站：段長法結構性不可測（T6）
        return _unmeasurable(cls, NOTE_ZERO_LEN, (lo, hi), fleet_n, th, diag)
    if n_zlo:
        diag["caveats"].append(
            f"{n_zlo}/{fleet_n} machines are zero-length-only; their producing time is "
            f"structurally unmeasurable and lands in the idle buckets (upper bound)")

    # ---- #39 該跑窗分母（A5 形 (i)）＋ C-PC12 不確定標記 ---------------------------
    rw_on = rw_by_eid is not None
    diag["axis"] = "erw" if rw_on else "24h"
    closures_n = norm_closures(closures, lo, hi)
    diag["closure_n"] = len(closures_n)
    # uncertain 標記需要工單資料佐證（C-PC12）：wo degraded ⇒ 連標記都不做（寧可不主張）。
    cl_on = rw_on and bool(closures_n) and wo_source == "ok"
    diag["closure_active"] = cl_on
    if closures_n and not rw_on:
        diag["caveats"].append(
            "closures were supplied without the expected-run-window axis -> ignored "
            "(the closure bucket is retired; the only path for the denominator fix "
            "is the expected run window itself = single truth)")
    if rw_on:
        # A8：非名冊機台整台移出分母、記「不適用」細目（工單時數揭露、不入任何桶）。
        kept, na_eids, na_wo = [], [], 0.0
        for key, m in fleet:
            if key in rw_by_eid:
                kept.append((key, m))
            else:
                na_eids.append(key)
                if wo_ok:
                    _wiv_na, _ = _wo_intervals((wo_segs or {}).get(key), lo, hi)
                    na_wo += _hours(_wiv_na)
        diag["na_n"] = len(na_eids)
        diag["na_eids"] = sorted(na_eids)
        diag["na_mh"] = _r6(len(na_eids) * win_h)
        diag["na_wo_h"] = _r6(na_wo)
        if na_wo > 1e-9:
            diag["caveats"].append(
                f"{_r6(na_wo)}h of work-order downtime falls on machines outside the "
                f"kun catalog -> disclosed here, not booked (the expected run window "
                f"does not apply to them)")
        fleet = kept
        fleet_n = len(fleet)
        if fleet_n == 0:
            return _unmeasurable(cls, NOTE_NOT_IN_KUN, (lo, hi), 0, th, diag)
        n_zlo = sum(1 for _k, m in fleet if m.get("zero_len_only"))
        diag["n_zero_len_machines"] = n_zlo
        if n_zlo == fleet_n:
            return _unmeasurable(cls, NOTE_ZERO_LEN, (lo, hi), fleet_n, th, diag)

    # ---- 逐機：生產 / 工單 / 閒置（精確量；T2 優先序 生產 ＞ 工單 ＞ 斷料 ＞ 下游堵）----
    #   rw 軸（A5）：各桶皆 ∩ RW（C-RW11 分子分母同窗）；窗外生產／工單不入帳、逐項揭露。
    prod_all, idle_pieces = [], []            # idle_pieces: [(a, b)]（跨機可重疊、各自計）
    prod_h = wo_pre_h = wo_eff_h = ov_h = 0.0
    closure_unc_h = 0.0
    rw_mh_acc = out_prod_h = out_wo_h = 0.0
    rw_mh_active_acc = 0.0                    # W3：宣告分母（至少還有一座在役的機台）
    rw_health_sec = 0.0                       # M3 v4：窄稼動扣除（**舊尺**、v5 起只當相容欄）
    rw_avail_sec = 0.0                        # M3 v5：妥善率扣除（白名單**跨態 union** ∩ RW）
    rw_down_by_state = {}                     # M3 v5：逐態 union ∩ RW（事件視角、Σ 可 > 上一行）
    # ★ WP-C2（2026-09-12）：逐機台生產與**座‧小時分母**。與 `prod_h` **同一個迴圈、同一個
    #   `_hours(p)`、同序累加** ⇒ 全站皆 1 座時 `seat_h_acc` 與 `prod_h` **浮點逐位元相等**
    #   （`x * 1` 不動位元、加法順序相同）⇒ `r_seat ≡ rate_per_mh`＝純單座站的數字不變。
    #   分兩個迴圈算、或先 `_r6` 再加，這個相等性就只剩「幾乎相等」——而那正是
    #   「Seal 的當量憑空跳了 1PH」的產地。
    prod_h_by_eid = {}
    seat_h_acc = 0.0
    wo_by_state = {s: 0.0 for s in COUNTABLE_WO_STATES}
    n_records = 0
    for key, m in fleet:
        for r in m.get("records") or ():
            if isinstance(r, dict):
                n_records += 1
        base = [(lo, hi)]
        if rw_on:
            base = _inter(_merge([(_dt(a), _dt(b)) for a, b in rw_by_eid.get(key) or ()
                                  if _dt(a) is not None and _dt(b) is not None]),
                          [(lo, hi)])
            _base_h = _hours(base)
            _roll_k = (avail_rollup or {}).get(key)
            if _roll_k and _roll_k.get("excl_sec"):
                # R44：全座長停日（n_在役＝0）不主張 ⇒ 自應生產時段分母排除
                _base_h = max(0.0, _base_h - float(_roll_k["excl_sec"]) / 3600.0)
            rw_mh_acc += _base_h
            if seats:
                # W3：**一座都不剩的機台整台不進宣告分母**（Chris 裁「長停 EID 從產能預測剔除」）；
                # 部分座長停的機台**照舊整窗進分母**——`month_rate` 量的就是「當時開著那幾座」的
                # 產出（C-CF6），按 n/N 縮機時＝把同一件事扣兩次，而且會讓 U 因座而變（C-CF8）。
                _s = seats.get(key) or {}
                if int(_s.get("active", 1) or 0) > 0:
                    rw_mh_active_acc += _base_h
        p_all = _prod_intervals(m, lo, hi)
        prod_all.extend(p_all)                # gate 用**全部**生產訊號（訊號是事實、不裁）
        p = _inter(p_all, base) if rw_on else p_all
        _ph_eid = _hours(p)                   # ★ WP-C2：算一次、三處共用（同一把尺）
        out_prod_h += _hours(p_all) - _ph_eid
        prod_h += _ph_eid
        prod_h_by_eid[key] = _ph_eid
        # 座‧小時分母：一座機＝機台小時（×1 不動位元）；多座機按**在役**座數加權
        #（`installed` 不進分母——長停的座今天沒在產，把它算進去等於把 r_seat 稀釋掉）。
        seat_h_acc += _ph_eid * _seat_count(seats, key, "active")
        if wo_ok:
            wivs_all, bys = _wo_intervals((wo_segs or {}).get(key), lo, hi)
            for st, h in bys.items():
                wo_by_state[st] = wo_by_state.get(st, 0.0) + h
                if st not in COUNTABLE_WO_STATES and st is not None:
                    diag["caveats"].append(f"unexpected countable work-order state {st!r}")
        else:
            wivs_all = []
        if rw_on and wo_ok:
            # M3 v4／v5：三個 rw 停機量**同一批段、三種聚合**（`SPEC-CAPACITY.md` §3.1）：
            #   · `health_down_sec`（**舊尺、相容欄**）＝維修＋計畫保養，逐態 union Σ。
            #   · `down_by_state`（**新**）＝白名單各態各自 union ∩ RW（事件視角、畫組成用）。
            #   · `avail_down_sec`（**新，A 的分子**）＝白名單**跨態 union** ∩ RW
            #     （兩張重疊的工單只算一次＝`/equipment` 妥善率同一個口徑）。
            # ⚠ 三個都走**白名單**：不在名單裡的態一秒都不扣（不知道 ≠ 壞了）。
            _roll_k = (avail_rollup or {}).get(key)
            for _st, _sec in _wo_intervals_by_state(
                    (wo_segs or {}).get(key), lo, hi, base).items():
                if _st in woee.RW_HEALTH_STATES:
                    rw_health_sec += _sec
                if _st in woee.RW_AVAIL_DOWN_STATES and _roll_k is None:
                    rw_down_by_state[_st] = rw_down_by_state.get(_st, 0.0) + _sec
            if _roll_k is not None:
                # ★ R44 C-AV16：形③ 父機＝在役座推導（呼叫端算好的同一支 rollup）
                for _st, _sec in (_roll_k.get("down_by_state") or {}).items():
                    if _st in woee.RW_AVAIL_DOWN_STATES and _sec > 0:
                        rw_down_by_state[_st] = rw_down_by_state.get(_st, 0.0) + _sec
                rw_avail_sec += float(_roll_k.get("avail_down_sec") or 0.0)
            else:
                rw_avail_sec += _wo_union_sec((wo_segs or {}).get(key), lo, hi, base,
                                              woee.RW_AVAIL_DOWN_STATES)
        wivs = _inter(wivs_all, base) if rw_on else wivs_all
        out_wo_h += _hours(wivs_all) - _hours(wivs)
        wo_pre_h += _hours(wivs)
        ov = _inter(wivs, p)
        ov_h += _hours(ov)
        wo_eff = _sub(wivs, p)               # 工單扣掉生產重疊＝進恆等式的那一項
        wo_eff_h += _hours(wo_eff)
        idle = _sub(_sub(base, p), wivs)
        if cl_on:
            # C-PC12：uncertain closure（分母沒被扣的那些）∩ 閒置 ⇒ 誠實殘差（未解釋＋diag）。
            # certain 段照理已被呼叫端從 RW 扣掉（∩ base 應為空）；若呼叫端漏扣，留在 idle
            # 讓網格照常分類＝不雙重主張。
            _cert, unc, rest = _closure_split(idle, key, closures_n)
            closure_unc_h += _hours(unc)
            idle = _merge(rest + _cert)
        idle_pieces.extend(idle)
    diag["n_records"] = n_records
    if rw_on:
        diag["producing_outside_rw_h"] = _r6(out_prod_h)
        diag["wo_outside_rw_h"] = _r6(out_wo_h)
        if out_prod_h > 1e-6:
            diag["caveats"].append(
                f"{_r6(out_prod_h)}h of production fell outside the expected run window "
                f"(kept as evidence, not booked -> the window expansion evidence comes "
                f"from peers, never from the machine itself)")

    # 整窗零訊號且零工單 ⇒ 不可測（T6）：24h×N 全灌未解釋只是噪音，不是診斷。
    # ★ 但「純工單機台（零生產段）」**必須**入帳（T1）——它有 countable 工單，走不到這裡。
    if n_records == 0 and wo_pre_h <= 0:
        diag["caveats"].append("no MES records and no countable work orders in window")
        return _unmeasurable(cls, NOTE_NOSIG, (lo, hi), fleet_n, th, diag)

    prod_union = _merge(prod_all)             # 站級生產訊號（活動鄰近 gate 用）

    # ---- 網格標籤（只決定「貼哪個標籤」；入桶量＝實際閒置秒數）---------------
    wip_pts, wip_ts = _norm_series(wip_series)
    dn_pts, dn_ts = _norm_series(dn_series)
    diag["wip_points"] = len(wip_ts)
    diag["dn_points"] = len(dn_ts)
    diag["downstream_known"] = bool(dn_pts)
    if wo_source != "ok":
        diag["caveats"].append("work-order source degraded -> all idle time booked to "
                               "unexplained (never re-labelled as starved/blocked)")
    if not wip_ts:
        diag["caveats"].append("WIP series unavailable -> idle time cannot be split "
                               "(all unexplained)")

    near = timedelta(hours=float(th["active_near_h"]))
    gate_cache = {}

    def _gate_ok(t):
        if th["active_near_h"] <= 0:          # gate 關閉（env 明示）＝不做 off-shift 防護
            return True
        v = gate_cache.get(t)
        if v is None:
            a, b = t - near, t + near
            v = any(s < b and e > a for s, e in prod_union)
            gate_cache[t] = v
        return v

    # 「不確定」（C-PC12）＝**誠實殘差**：不判停線、也不判停機 ⇒ 一開始就記在未解釋上
    # （closure 桶 inert 時 `closure_unc_h` 恆為 0 ⇒ 舊行為逐位不變）。
    bucket_h = {"starved": 0.0, "blocked": 0.0, "unexplained": closure_unc_h}
    ungridded_h = gated_h = 0.0
    if wo_source != "ok" or not wip_ts:
        # 降級路徑：整片閒置一律未解釋（不做任何原因主張）
        bucket_h["unexplained"] += _hours(idle_pieces)
        ungridded_h = 0.0 if wip_ts else _hours(idle_pieces)
    else:
        # 網格切片：cell(t_i) = [t_i, 下一點)，末點延伸到 win_end；早於首點＝未網格化
        cells = []
        for i, t in enumerate(wip_ts):
            end = wip_ts[i + 1] if i + 1 < len(wip_ts) else hi
            if end > hi:
                end = hi
            if end > t:
                cells.append((t, end))
        for a, b in idle_pieces:
            if not cells:
                # 退化分支：`wip_ts` 非空但**沒有任何有效 cell**（所有網格點 ≥ win_end）。
                # ★ 整片一次記完就 `continue`——舊寫法的「早於首 cell」與「晚於末 cell」兩段
                #   在 cells 為空時會對同一片各記一次（雙計污染 diag 的 labeled/ungridded；
                #   精確殘差 `unexplained_h` 不受影響、恆等式仍成立，但 diag 會說謊）。
                h = (b - a).total_seconds() / 3600.0
                if h > 0:
                    bucket_h["unexplained"] += h
                    ungridded_h += h
                continue
            if a < cells[0][0]:
                cut = cells[0][0]
                seg_b = b if b < cut else cut
                if seg_b > a:
                    h = (seg_b - a).total_seconds() / 3600.0
                    bucket_h["unexplained"] += h
                    ungridded_h += h
            for cs, ce in cells:
                x = a if a > cs else cs
                y = b if b < ce else ce
                if y <= x:
                    continue
                h = (y - x).total_seconds() / 3600.0
                ok = _gate_ok(cs)
                lab = classify_idle(wip_pts.get(cs), dn_pts.get(_cell_of(dn_ts, cs))
                                    if dn_pts else None, ok, th)
                bucket_h[lab] += h
                if not ok:
                    gated_h += h
            if b > cells[-1][1]:
                tail_from = cells[-1][1]
                x = a if a > tail_from else tail_from
                if b > x:
                    h = (b - x).total_seconds() / 3600.0
                    bucket_h["unexplained"] += h
                    ungridded_h += h

    # ---- 帳本（未解釋＝**精確殘差** ⇒ T1 恆等式無捨入破口）--------------------
    #   A5：rw 軸的分母＝Σ|RW∩win|；舊軸＝fleet_n × win_h。closure_h 恆 0（桶已退場；
    #   欄位保留＝日帳 schema 尾欄相容、舊檔讀端 `.get() or 0.0` 不變）。
    total_mh = _r6(rw_mh_acc) if rw_on else _r6(fleet_n * win_h)
    producing_h = _r6(prod_h)
    wo_union_h = _r6(wo_eff_h) if wo_ok else 0.0
    closure_h = 0.0
    starved_h = _r6(bucket_h["starved"])
    blocked_h = _r6(bucket_h["blocked"])
    unexplained_h = _r6(total_mh - producing_h - wo_union_h
                        - starved_h - blocked_h)
    if -1e-6 < unexplained_h < 0:            # 浮點噪音（結構上不會有實質負值）
        unexplained_h = 0.0
    if unexplained_h < 0:
        diag["caveats"].append(f"negative residual {unexplained_h}h clamped to 0 "
                               f"(input intervals may exceed the window)")
        unexplained_h = 0.0
    diag["unexplained_labeled_h"] = _r6(bucket_h["unexplained"])
    diag["ungridded_idle_h"] = _r6(ungridded_h)
    diag["off_shift_gated_h"] = _r6(gated_h)
    # C-PC12：綑內有 countable 工單活躍 ⇒ 該段標「不確定」（計入未解釋、不進 closure 桶）。
    diag["closure_uncertain_h"] = _r6(closure_unc_h)

    wo_h = None
    wo_top = None
    if wo_ok:
        wo_h = {k: _r6(v) for k, v in wo_by_state.items()}
        cand = [(k, v) for k, v in wo_h.items() if v and v > 0]
        cand.sort(key=lambda kv: (-kv[1], kv[0]))
        wo_top = cand[0] if cand else None

    lost_h = _r6(total_mh - producing_h)
    rate, rate_src = _rate_per_mh(rate_ctx, producing_h)
    diag["rate_per_mh"] = rate
    diag["rate_src"] = rate_src
    est = None
    if rate is not None:
        est = {"unit": "module", "rate_per_mh": _r6(rate), "rate_src": rate_src,
               "wo": int(round(wo_union_h * rate)),
               "starved": int(round(starved_h * rate)),
               "blocked": int(round(blocked_h * rate)),
               "unexplained": int(round(unexplained_h * rate)),
               "lost_total": int(round(lost_h * rate))}

    diag["caveats"] = list(dict.fromkeys(diag["caveats"]))    # 同一句不重複刷屏
    vals = {"wo": wo_union_h, "starved": starved_h,
            "blocked": blocked_h, "unexplained": unexplained_h}
    top = sorted(((b, vals[b]) for b in BUCKETS if vals[b] > 1e-9),
                 key=lambda kv: (-kv[1], BUCKETS.index(kv[0])))

    out = {
        # ---- SPEC §3 pin ----
        "cls": cls, "fleet_n": fleet_n, "total_mh": total_mh,
        "producing_h": producing_h, "wo_union_h": wo_union_h, "wo_h": wo_h,
        "wo_overlap_prod_h": _r6(ov_h), "closure_h": closure_h,
        "starved_h": starved_h, "blocked_h": blocked_h, "unexplained_h": unexplained_h,
        "est": est, "unmeasurable": False, "note": None, "top": top,
        # ---- additive（明確標示；消費端白名單讀取即可忽略）----
        "wo_union_pre_h": _r6(wo_pre_h) if wo_ok else None,   # ＝/equipment 對帳項（§8）
        "wo_top": wo_top, "lost_h": lost_h,
        "win_start": lo.isoformat(), "win_end": hi.isoformat(),
        "th": dict(th), "schema": SCHEMA_ID, "caliber": CALIBER, "diag": diag,
    }
    # ---- M3 日帳 v4：`rw` 區塊（**additive、rw 軸才有；沒有 rw_by_eid 時整個鍵缺席**）----
    #   ★ 為什麼「缺席」而不是「None」：A5 的 inert-fallback 地板是「沒傳 rw ⇒ 輸出逐位元同前」。
    #     多一個 `rw: None` 就已經不是逐位元了（selftest 有負斷言釘住鍵集）。
    #   ★ 單位是**秒**（不是小時）：這一塊是餵給 `board_oee.rw_split` 的材料，那支的口徑
    #     是秒；在這裡先換成小時、月級再乘回去＝多一次捨入、且會出現第二種尺。
    #   · `planned_sec` ≡ `total_mh × 3600`（rw 軸下 total_mh 就是 Σ|RW∩窗|＝同一個數字的兩種單位）。
    #   · `n_eid` ≡ `fleet_n`（A8 已把非名冊機台移出分母 ⇒ 這裡數的就是進分母的台數）。
    #   · `producing_sec` ＝ **∩RW 之後**的生產（窗外生產不入帳、只在 diag 揭露）。
    #   ★★ **M3 v5（2026-09-04；`SPEC-CAPACITY.md` §3.1「v5 走加法、不是改名」）**：
    #     三個新欄，`health_down_sec` **原值照寫不動**（拿舊截圖對帳的人查得到）。
    #     · `avail_down_sec` ＝妥善率尺（白名單**跨態 union** ∩ RW）＝**月級 A 的分子**。
    #     · `down_by_state`  ＝逐態 union ∩ RW（事件視角；`Σ 可 > avail_down_sec`，
    #                          因為跨態重疊在這裡不合併——**兩個數字不是同一個問題**）。
    #     · `eids`           ＝真正進分母的機台（排序）。**新增它的理由是可回補性**：
    #                          v4 只留 `n_eid`，於是「planned_sec 是哪幾台加出來的」在檔上
    #                          不可考 ⇒ 想做零 MES 的軸級升級時，分子重算了、分母卻無從對帳。
    #                          有了這一欄，日後換停機口徑就能**只重算工單腿**（見
    #                          `_backfill_ledger.plan_day` 的 `v5_upgrade`）。
    #   ★★★ **W3（2026-09-06；`SPEC-CAPACITY-FORECAST.md` §2）＝三個加法欄，語意如下**：
    #     · `seats`                 ＝`{installed, active, dormant[]}`（**分母成員的座結構要落檔**）。
    #                                 缺這個鍵＝**舊日帳**（那一天的座結構沒有記錄），讀端一律
    #                                 `installed == active` ＋ `seats_legacy` 揭露，**不得**當成
    #                                 「那天零長停」（`board_capacity.rw_seats_of` 是唯一讀法）。
    #     · `planned_sec`           ＝**宣告**分母（Σ 至少還有一座在役的機台）＝產能／預測吃的那個。
    #     · `planned_sec_nameplate` ＝**銘牌**分母＝接線前的舊值（`total_mh × 3600`），一個位元不動。
    #     ⚠ **A／U 的分母永遠是 `planned_sec`**（月級／`/utilization` 都照舊讀它）——這兩個數字
    #       只有在「整台機一座都不剩」時才會分岔，而那正是 `SPEC-DORMANT-CAPACITY.md` §3.1
    #       明文要求的「不進分母不進分子」。**部分座長停不動任何分母 ⇒ 稼動率不因座而變（C-CF8）。**
    if rw_on:
        out["rw"] = {"planned_sec": _r6(total_mh * 3600.0),
                     "health_down_sec": _r6(rw_health_sec),
                     "producing_sec": _r6(producing_h * 3600.0),
                     "n_eid": fleet_n, "src": "erw",
                     "avail_down_sec": _r6(rw_avail_sec),
                     "down_by_state": {k: _r6(v) for k, v in sorted(rw_down_by_state.items())},
                     "eids": sorted(k for k, _m in fleet)}
        # ★★ WP-C2（2026-09-12；Chris 裁「把 Gen2 子 EID 視為一台 Gen1 機器、得到平均的
        #   艦隊生產速率」）＝**座‧小時尺**。三個加法欄，`seats` 給不給都寫（缺席＝每台 1 座）：
        #     · `by_eid`  逐機台 ∩RW 生產秒數＋當日座帳（Σ producing_sec ≡ 站級 producing_sec）。
        #     · `seat_h`  Σ producing_h × active seats（小時）＝新分母。
        #     · `r_seat`  站產出 ÷ seat_h；**分子與 `rate_per_mh` 同一個**（`rate × producing_h`），
        #                 絕不另算第二份產出——第二份產出就是第二份會漂移的事實。
        #   ⚠ 寫成 `rate * (prod_h / seat_h_acc)` 而不是 `rate * prod_h / seat_h_acc`：
        #     全站皆 1 座時 `seat_h_acc` 與 `prod_h` 逐位元相等 ⇒ 括號內恰為 `1.0`
        #     ⇒ `r_seat` 與 `rate` **逐位元相同**（乘完再除不保證回得來）。
        out["rw"]["by_eid"] = {
            k: {"producing_sec": _r6(prod_h_by_eid.get(k, 0.0) * 3600.0),
                "seats_active": _seat_count(seats, k, "active"),
                "seats_installed": _seat_count(seats, k, "installed")}
            for k, _m in sorted(fleet, key=lambda km: km[0])}
        out["rw"]["seat_h"] = _r6(seat_h_acc)
        out["rw"]["r_seat"] = (_r6(rate * (prod_h / seat_h_acc))
                               if (rate is not None and seat_h_acc > 0) else None)
        if seats:
            _fl = [k for k, _m in fleet]
            _dorm = sorted(d for k in _fl for d in ((seats.get(k) or {}).get("dormant") or ()))
            out["rw"]["seats"] = {
                "installed": sum(int((seats.get(k) or {}).get("installed", 1) or 0) for k in _fl),
                "active": sum(int((seats.get(k) or {}).get("active", 1) or 0) for k in _fl),
                "dormant": _dorm}
            out["rw"]["planned_sec_nameplate"] = out["rw"]["planned_sec"]
            out["rw"]["planned_sec"] = _r6(rw_mh_active_acc * 3600.0)
        if equiv_factor is not None:
            try:
                out["rw"]["equiv_factor"] = _r6(float(equiv_factor))
            except (TypeError, ValueError):
                pass                          # 壞值＝不寫（月級當 1.0），絕不炸整份帳
    return out


def identity_error(led):
    """T1 恆等式殘差（h）：不可測站回 0.0（無帳本可驗）。selftest/live 對帳都用這支。"""
    if not led or led.get("unmeasurable"):
        return 0.0
    # `closure_h` 用 `.get()`＋`or 0.0`：G2 之前產的板/日檔沒有這個鍵（那些時數當時誠實
    # 落在其他桶裡）⇒ 缺鍵預設 0.0，恆等式對新舊兩形都成立、且不雙計。
    return abs(float(led["total_mh"]) - (float(led["producing_h"]) + float(led["wo_union_h"])
               + float(led.get("closure_h") or 0.0)
               + float(led["starved_h"]) + float(led["blocked_h"])
               + float(led["unexplained_h"])))


def chips_gate(led, met=None, th=None):
    """C8 chips 顯示閘＝**雙軌**：`met is False` ∨ `lost_h > chip_min_lost_h`。

    ＝真診斷的可見性不被（仍是 DEMO 的）target 單獨把持。不可測站永不顯 chips。
    """
    th = _th(th)
    if not led or led.get("unmeasurable"):
        return False
    if met is False:
        return True
    return float(led.get("lost_h") or 0.0) > th["chip_min_lost_h"]


# =============================================================================
# 7. daily_board — 20 站帳本 → /daily 消費形（SPEC §3 API pin）
# =============================================================================
def daily_board(rows_by_cls, *, order=None, day=None, source=None,
                wo_source_status=None, pareto_docs=None, end_day=None,
                pareto_days=PARETO_DAYS, computed_at=None):
    """`{cls: build_ledger 回傳}` → `{"rows", "hero", "pareto30", …}`。

    · `order`＝cls 顯示序（`board_daily.BOSS_STATIONS` 的 cls 序）；未給＝dict 插入序。
      order 裡缺帳本的 cls **不捏造**（略過）；rows 之外的 cls 附在後面（不藏站）。
    · hero＝「昨日全線損失 top 原因」一行所需的線級聚合（**只統計可測站**；不可測站另計數）。
    · `pareto30`＝由日帳文件（C6）聚合；沒有文件 → `None`（**不即時重算 30 天**＝§9 反模式）。
    """
    rows_by_cls = dict(rows_by_cls or {})
    seq = [c for c in (order or []) if c in rows_by_cls]
    seq += [c for c in rows_by_cls if c not in seq]
    rows = [rows_by_cls[c] for c in seq]

    meas = [r for r in rows if not r.get("unmeasurable")]
    by_bucket = {b: 0.0 for b in BUCKETS}
    total_mh = producing_h = lost_h = 0.0
    for r in meas:
        total_mh += float(r["total_mh"] or 0.0)
        producing_h += float(r["producing_h"] or 0.0)
        lost_h += float(r["lost_h"] or 0.0)
        by_bucket["wo"] += float(r["wo_union_h"] or 0.0)
        # A5：closure 桶已退場——新板 closure_h 恆 0；舊日檔的 closure_h 不進桶
        #   （那些小時當時屬「全廠沒開」＝新框裡本來就不在分母，混進可行動桶＝雙重語意）。
        by_bucket["starved"] += float(r["starved_h"] or 0.0)
        by_bucket["blocked"] += float(r["blocked_h"] or 0.0)
        by_bucket["unexplained"] += float(r["unexplained_h"] or 0.0)
    by_bucket = {k: _r6(v) for k, v in by_bucket.items()}
    top = sorted(((b, by_bucket[b]) for b in BUCKETS if by_bucket[b] > 1e-9),
                 key=lambda kv: (-kv[1], BUCKETS.index(kv[0])))

    worst = None
    for r in meas:
        for b, h in (r.get("top") or ()):
            if worst is None or h > worst["h"]:
                worst = {"cls": r["cls"], "bucket": b, "h": h}
            break                                  # 每站只看它自己的第一名
    est_sum, n_rate = {b: 0 for b in BUCKETS}, 0
    for r in meas:
        e = r.get("est")
        if not e:
            continue
        n_rate += 1
        for b in BUCKETS:
            est_sum[b] += int(e.get(b) or 0)
    # C-PC12「不確定」的線級合計（**不是一個桶**——它已計在 unexplained 裡；這裡只做揭露，
    # UI 用它決定要不要出那一行 note。缺 diag 的舊板/他源 → 0.0）。
    cl_unc = _r6(sum(float(((r.get("diag") or {}).get("closure_uncertain_h")) or 0.0)
                     for r in meas))
    hero = {
        "n_stations": len(rows), "n_measurable": len(meas),
        "n_unmeasurable": len(rows) - len(meas),
        "closure_uncertain_h": cl_unc,
        "total_mh": _r6(total_mh), "producing_h": _r6(producing_h), "lost_h": _r6(lost_h),
        "by_bucket": by_bucket, "top": top, "worst": worst,
        "est": (dict(est_sum, unit="module") if n_rate else None),
        "est_partial": bool(n_rate and n_rate < len(meas)),
        "n_rate_missing": len(meas) - n_rate,
    }
    return {
        "schema": SCHEMA_ID, "caliber": CALIBER,
        "day": day, "source": source, "wo_source_status": wo_source_status,
        "computed_at": computed_at,
        "rows": rows, "hero": hero,
        "pareto30": (pareto30(pareto_docs, days=pareto_days, end_day=end_day)
                     if pareto_docs else None),
    }


# =============================================================================
# 8. 日帳文件（C6）——**形狀在這裡定義、讀寫由 B2 做**
# =============================================================================
# ★ `closure_h` 是 **additive 尾欄**（2026-08-22 G2）：`LEDGER_SCHEMA_VER` 不升版——
#   舊日檔沒有這個鍵，讀端一律 `.get(...) or 0.0`（**那些時數當時已誠實落在其他桶、不雙計**）。
# ★ `rate_per_mh`／`rate_src` 同為 **additive 尾欄**（2026-08-24）：一樣不升版；
#   舊日檔沒有這兩個鍵 ⇒ parse 後恆 `None`，讀端據此顯「—」/不換算（**絕不猜速率**）。
# ★ `rw`／`families` 同為 **additive 尾欄**（2026-09-03 M3 日帳 v4）：本檔的
#   `LEDGER_SCHEMA_VER`（v1）仍不升版——**升版的是 `board_unit`（v3→v4）**，因為實際落盤
#   的文件形狀由那支組裝。舊日檔沒有這兩個鍵 ⇒ parse 後恆 `None`（`None ≠ {}`：
#   「沒抓」與「抓到零筆」是兩件事，讀端不得混）。
#   · `rw`       ＝ `build_ledger` 在 rw 軸下產的站級 A/U 材料（見該函式 docstring）。
#   · `families` ＝ 呼叫端（`app._build_attrib_day`）事後掛到板列上的產品族活動計數
#                  `{eid: {family: n}}`；**本引擎一秒都不算它**，只負責讓它進得了文件。
_DOC_STATION_KEYS = ("cls", "fleet_n", "total_mh", "producing_h", "wo_union_h", "wo_h",
                     "wo_union_pre_h", "wo_overlap_prod_h", "starved_h", "blocked_h",
                     "unexplained_h", "lost_h", "unmeasurable", "note", "closure_h",
                     "rate_per_mh", "rate_src", "rw", "families")

# §24/T7 防呆：這些鍵**永遠不得**出現在日帳文件裡（呼叫端手滑塞進來也要被剝掉）。
#   ★ 短 token（mp/name/user…）**只做全等比對**——用子字串會誤殺自家欄位
#     （踩過：`computed_at` 裡有 "mp"、`n_names` vs `filename`）。長 token 才做子字串。
_PERSON_EXACT = frozenset((
    "mp", "name", "names", "user", "users", "actor", "source_actor", "staff", "crew",
    "shift_lead", "person", "people", "operator", "operators", "assignee", "assignees",
    "technician", "technicians", "employee", "employees", "headcount", "manpower",
    "badge", "worker", "workers", "person_id", "personid", "psgpersonid", "operatorid"))
_PERSON_SUB = ("person", "operator", "assignee", "technician", "employee",
               "headcount", "manpower", "badge", "worker", "_name", "name_")


def _person_ish(k):
    s = str(k).strip().lower()
    return s in _PERSON_EXACT or any(tok in s for tok in _PERSON_SUB)


def _scrub(d):
    """遞迴剝除人員相關鍵（T7；帳本本來就不產生它們，這是**輸入端手滑**的最後一道）＋
       把 tuple 攤成 list ＝**JSON 原生形**（否則 `top` 的 tuple 落檔後讀回來變 list、
       round-trip 不等於自己＝日後對帳/比較會出現假差異）。"""
    if isinstance(d, dict):
        return {k: _scrub(v) for k, v in d.items() if not _person_ish(k)}
    if isinstance(d, (list, tuple)):
        return [_scrub(x) for x in d]
    return d


def should_persist(source):
    """C6 落地條件＝**本進程的 live 類來源值 only**（demo/snapshot 落日帳＝§9 反模式）。

    合成產線 Q-C（R-4）：來源值屬 `core_source.LIVE_LIKE`，且必須是**本進程自己的**那一個——
    非合成進程只落 `live`（synth＝拒收，逐位元同改動前）；合成進程只落 `synth`（`live` 帳絕不會
    從合成進程寫出去）。合成進程要落地時先斷言狀態目錄有 `SYNTH_WORLD.json` 標記（沒有＝
    `core_source.SynthStateError`、大聲失敗；清冊 §5-7 第 4 點）。"""
    s = str(source or "").strip().lower()
    if not (s in _wrs.LIVE_LIKE and (s == _wrs.SOURCE_SYNTH) == _wrs.is_synth_process()):
        return False
    _wrs.assert_state_marker()          # 非合成進程：什麼都不做
    return True


def should_overwrite(existing_doc):
    """C6：`degraded` 日**允許重算覆寫**（＝非無條件 immutable）；`ok` 日不覆寫。

    `existing_doc=None`（尚無檔）→ True（寫新檔）。
    parse 不出來時分兩種（2026-08-03 修 MED B-3）：
      · **帶 schema_ver 但本版讀不懂**（v2/v3／更新版的檔）→ **False，永不 clobber**。
        本函式是「`board_unit` 模組缺席」時的退路；舊版程式碼把讀不懂的新版好檔判成
        壞檔而重寫，等於用降級路徑把完整的顆數帳整份輾掉（且無法還原）。
      · **無／空 schema_ver**（半寫檔、手改壞的檔）→ True（重寫）。
    """
    if not existing_doc:
        return True
    doc = parse_ledger_doc(existing_doc)
    if doc is None:
        ver = existing_doc.get("schema_ver") if isinstance(existing_doc, dict) else None
        if isinstance(ver, str) and ver.strip():
            return False
        return True
    return str(doc.get("wo_source_status") or "").lower() != "ok"


def _doc_station(r):
    """板列 → 日帳 station 文件（白名單投影 ＋ `est` 攤平出兩個尾欄）。

    ★ 為什麼 station 留速率、`line` 卻 `pop("est")`（看似矛盾、其實不違反 T4）：
      T4 說的是「**權威單位是小時**」——被剝掉的是 `est` 裡那些**估算出來的顆數總量**
      （`wo`/`starved`/`blocked`/`lost_total`），它們是溝通用的換算結果、不是帳本事實，
      落檔會變成第二份會漂移的「產出數字」。而 `rate_per_mh` **不是估算總量、是換算係數**
      （顆／機台小時，SPEC-RUNWINDOW §4.1 的實際生產錨）：它是那一天真的量到的比值，
      落檔後讀端仍要自己拿小時去乘。⇒ 留係數不留總量 ＝ 權威仍然只有小時那一份。
      `rate_src` 一起帶＝**沒有來源標籤的係數不可信**（`y24`／`given`／`demo`…）。
    ★ `rw`／`families`（M3 v4）＝**純透傳**（白名單投影自動涵蓋）：`rw` 由 `build_ledger`
      在 rw 軸下產出、24h 軸下該鍵不存在 ⇒ 這裡 `.get()` 得 None＝誠實的「這天沒有新軸」；
      `families` 是呼叫端掛上來的，引擎不生成也不驗證內容（只保證它進得了文件）。
    """
    st = {k: r.get(k) for k in _DOC_STATION_KEYS}
    est = r.get("est")
    st["rate_per_mh"] = est.get("rate_per_mh") if isinstance(est, dict) else None
    st["rate_src"] = est.get("rate_src") if isinstance(est, dict) else None
    return st


def ledger_doc(board, *, day=None, source=None, wo_source_status=None, computed_at=None,
               grid_min=GRID_MIN, th=TH):
    """`daily_board` 回傳 → 日帳落地文件（**僅聚合數字、零人員欄**；C6 四個 pin 全帶）。

       station 級帶 `rate_per_mh`/`rate_src`（見 `_doc_station` 的 T4 說明）——
       用途＝服務重啟後 `/equipment` 的產出當量有磁碟退路可讀（`app._eqm_rates_by_eid`），
       不必為了一格數字去觸發 `/daily` 的全建構（SPEC-WARM-CACHE §9③ warm-only 鐵則）。"""
    day = day or board.get("day")
    src = source or board.get("source")
    wss = wo_source_status or board.get("wo_source_status")
    doc = {
        "schema_ver": LEDGER_SCHEMA_VER,          # ← C6 pin ①
        "day": str(day) if day is not None else None,
        "source": src,                            # ← C6 pin ②
        "wo_source_status": wss,                  # ← C6 pin ③（ok / degraded）
        "computed_at": (computed_at or board.get("computed_at")
                        or datetime.now().replace(microsecond=0).isoformat()),  # ← pin ④
        "grid_min": int(grid_min or GRID_MIN),
        "thresholds": dict(_th(th)),
        "line": dict(board.get("hero") or {}),
        "stations": [_doc_station(r) for r in (board.get("rows") or ())],
    }
    doc["line"].pop("est", None)                  # est＝溝通用估算，權威單位是小時（T4）
    doc["line"].pop("est_partial", None)
    doc["line"].pop("n_rate_missing", None)
    return _scrub(doc)


def parse_ledger_doc(doc):
    """日帳文件 → 驗過的 dict（未知欄忽略＝additive-safe）；schema_ver/day 不對 → `None`。

    **絕不猜**：schema_ver 不符就當讀不到（呼叫端計入 `n_bad`、Pareto 揭露涵蓋不足）。
    """
    if not isinstance(doc, dict):
        return None
    if doc.get("schema_ver") != LEDGER_SCHEMA_VER:
        return None
    day = doc.get("day")
    if not isinstance(day, str) or not day:
        return None
    out = {"schema_ver": LEDGER_SCHEMA_VER, "day": day,
           "source": doc.get("source"),
           "wo_source_status": doc.get("wo_source_status"),
           "computed_at": doc.get("computed_at"),
           "grid_min": doc.get("grid_min"),
           "thresholds": dict(doc.get("thresholds") or {}),
           "line": dict(doc.get("line") or {}),
           "stations": []}
    for s in doc.get("stations") or ():
        if isinstance(s, dict) and s.get("cls"):
            out["stations"].append({k: s.get(k) for k in _DOC_STATION_KEYS})
    return _scrub(out)


def parse_ledger_docs(docs):
    """`[doc…]` → `(parsed, n_bad)`（壞檔不炸整頁、只計數）。"""
    good, bad = [], 0
    for d in docs or ():
        p = parse_ledger_doc(d)
        if p is None:
            bad += 1
        else:
            good.append(p)
    return good, bad


def expired_days(days, today, keep_days=KEEP_DAYS):
    """保留 `keep_days` 天輪替（C6/Q3）→ 該刪的日期字串（純函式；刪檔是 B2 的事）。"""
    today = _dt(today) or datetime.now()
    floor = (datetime(today.year, today.month, today.day)
             - timedelta(days=int(keep_days or KEEP_DAYS)))
    out = []
    for d in days or ():
        dd = _dt(str(d))
        if dd is None or dd < floor:
            out.append(d)
    return sorted(out)


def pareto30(docs, *, days=PARETO_DAYS, end_day=None):
    """30 天原因 Pareto（C6）：日帳文件 → 全線桶佔比 ＋ 站 × top-cause。

    · **只讀日帳、不重算**（§9 反模式：為 Pareto 每次重算 30 天）。
    · **涵蓋誠實**：`n_days`（實際有的日子）／`n_degraded`（工單源降級日）／`n_missing`
      （窗內缺的日子）全部回出去，UI 必須揭露（不揭露＝把 22 天的 Pareto 說成 30 天）。
    """
    parsed, n_bad = parse_ledger_docs(docs)
    if not parsed:
        return None
    end = _dt(str(end_day)) if end_day else None
    if end is None:
        end = max(_dt(d["day"]) or datetime.min for d in parsed)
    want = {(end - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(int(days))}
    have = {d["day"] for d in parsed}
    used = [d for d in parsed if d["day"] in want]
    if not used:
        return None

    line = {b: 0.0 for b in BUCKETS}
    legacy_cl = 0.0
    per = {}
    for d in used:
        for s in d["stations"]:
            if s.get("unmeasurable"):
                continue
            cls = s["cls"]
            p = per.setdefault(cls, {"cls": cls, "n_days": 0, "lost_h": 0.0,
                                     "by_bucket": {b: 0.0 for b in BUCKETS}})
            p["n_days"] += 1
            p["lost_h"] += float(s.get("lost_h") or 0.0)
            # ★ A5（2026-08-23）：closure 桶退場——**舊日檔**的 `closure_h`（#35 時期、24h 軸）
            #   不進任何桶，改累計成 `legacy_closure_h` 揭露欄：那些小時在新框裡屬「本來就
            #   不該跑」＝不是可行動損失；塞進四桶＝雙重語意，丟掉不揭露＝數字憑空縮。
            #   換軸日之後的日檔 closure_h 恆 0 ⇒ 這欄會隨 30d 窗自然歸零。
            legacy_cl += float(s.get("closure_h") or 0.0)
            for b, key in (("wo", "wo_union_h"), ("starved", "starved_h"),
                           ("blocked", "blocked_h"), ("unexplained", "unexplained_h")):
                v = float(s.get(key) or 0.0)
                p["by_bucket"][b] += v
                line[b] += v
    stations = []
    for p in per.values():
        p["by_bucket"] = {k: _r6(v) for k, v in p["by_bucket"].items()}
        p["lost_h"] = _r6(p["lost_h"])
        p["top"] = sorted(((b, p["by_bucket"][b]) for b in BUCKETS
                           if p["by_bucket"][b] > 1e-9),
                          key=lambda kv: (-kv[1], BUCKETS.index(kv[0])))
        stations.append(p)
    stations.sort(key=lambda x: (-(x["lost_h"] or 0.0), x["cls"]))
    line = {k: _r6(v) for k, v in line.items()}
    tot = sum(line.values())
    return {
        "days": int(days), "n_days": len(used), "n_bad_docs": n_bad,
        "n_degraded": sum(1 for d in used
                          if str(d.get("wo_source_status") or "").lower() != "ok"),
        "n_missing": len(want - have),
        "day_from": min(d["day"] for d in used), "day_to": max(d["day"] for d in used),
        "by_bucket": line, "total_lost_h": _r6(tot),
        # A5 過渡揭露：舊軸日檔的全廠停線時數（不是桶；30d 窗滾過切換日後自然歸零）
        "legacy_closure_h": _r6(legacy_cl),
        "share": {b: (_r6(line[b] / tot) if tot > 0 else None) for b in BUCKETS},
        "stations": stations,
        "thin": len(used) < max(1, int(days) // 4),     # 小樣本標記（UI 必顯）
    }


# =============================================================================
# 9. i18n（前端鐵則 R4：EN 預設 + 中文可切、鍵集對稱；canonical 態 key 不翻）
#    ★ T3 的殘差標籤住在這裡＝**唯一一份**，B2 不得另造措辭。
# =============================================================================
I18N_ATTRIB = {
    "en": {
        "at_hdr": "Where yesterday's time went",
        "at_b_producing": "Producing",
        # C-PC10 命名鐵則：**不得**寫成 "unscheduled" / "no shift" / "schedule" ——
        # 這個桶說的是「全廠沒在生產」，不是「SALTMARSH 沒排班」（那是 G1 才答得了的問題）。
        "at_b_closure": "Plant-wide stoppage (calendar-backed)",
        "at_closure_uncertain": "Some machines had an open work order during a plant-wide "
                                "stoppage — that time is marked uncertain and left in "
                                "unexplained rather than called a stoppage.",
        "at_note_wo_degraded": "The work-order source was unavailable — all idle time is "
                               "booked as unexplained rather than guessed.",
    },
    "zh": {
        "at_hdr": "昨天的時間花到哪去了",
        "at_b_producing": "生產",
        # C-PC10：**不得**寫成「沒排班」「班表」——它證明的是「全廠沒在生產」。
        "at_b_closure": "全廠停線（日曆佐證）",
        "at_closure_uncertain": "全廠停線時段內有機台掛著工單——該時段標為不確定、留在未解釋，"
                                "不判成停線。",
        "at_note_wo_degraded": "工單源當時不可用——閒置時間一律記為未解釋，不猜原因。",
    },
}
# ★ A4（#138）：歸因桶標籤（`at_b_*`）與口徑句／備註（`at_stale`、`at_gate`、`at_note_*`…）的文字已搬進
#   `board_metrics.TIPS`（family="I18N_ATTRIB" 的那些列）＝**仍是唯一一份**，只是住處換成登記表；
#   鍵名不變，模板與本檔的碼照舊讀 `I18N_ATTRIB[lang][key]`。T3 殘差措辭（`at_b_unexplained`）同此。
wmet.merge_tips(I18N_ATTRIB, "I18N_ATTRIB")

NOTE_I18N_KEY = {NOTE_ZERO_LEN: "at_note_zero_len", NOTE_NOSIG: "at_note_nosig",
                 NOTE_NO_FLEET: "at_note_no_fleet",
                 # 「推不出窗」≠「窗內無訊號」＝兩件事，各自一句（原本共用 nosig 文案輕度失真）。
                 NOTE_NO_WINDOW: "at_note_no_window",
                 NOTE_SRC_ERR: "at_note_src_err",
                 NOTE_NOT_IN_KUN: "at_note_not_in_kun"}
BUCKET_I18N_KEY = {"wo": "at_b_wo", "closure": "at_b_closure", "starved": "at_b_starved",
                   "blocked": "at_b_blocked", "unexplained": "at_b_unexplained"}


# =============================================================================
# 10. demo 合成（§5：確定性、涵蓋全部桶型＋不可測＋degraded；全部走**真引擎**）
# =============================================================================
DEMO_DAY = "2026-07-31"

#   scenario ＝ (cls, 型別)；型別決定 demo 合成的 machines/wo/wip 形狀。
#   ★ 全部丟進 `build_ledger` 跑真引擎 ⇒ demo 板不可能與 live 板說不同故事（口徑分兩處寫死
#     是 demo 自相矛盾的經典來源）。**零人員欄**（T7）。
DEMO_SCENARIOS = (
    ("segDiePlacement", "wo"),               # 工單主導（長修 + 等料）
    ("segFlexBonding", "starved"),         # 斷料主導（門口空、上游沒供上）
    ("segWireBonding", "blocked"),           # 下游堵（下游 cover 12h）
    ("segCalibrationTest", "mixed"),  # 混合（四桶都有）
    ("segFrameBonding", "offshift"),        # 只有一班在跑 → 其餘落未解釋（gate）
    ("segSealDispense", "degraded"),     # 工單源降級 → 全灌未解釋
    ("segPotting", "zero_len"),       # 零工期八站 → 不可測「—」
    ("segQASetup", "nosig"),      # 整窗無訊號、無工單 → 不可測「—」
    ("segVacuumPackInspect", "tail"),   # 線尾：下游堵桶結構上恆 0
    ("segCleanTest", "norate"),       # rate 不可得 → est=None、帳本小時照給
    # ⚠ 2026-08-22 拆除：#35 曾在此加兩個 `closure` demo 情境（給已移除的「全廠停線」桶
    #   示範用）。UI 拆掉後留著它們＝demo 頁上兩根 24h 堆疊條加不到 100%（時數落在一個
    #   沒人畫的桶裡）⇒ 隨 UI 一起退場。**closure 的計算層（`norm_closures` /
    #   `_closure_split` / `build_ledger(closures=)`）與其純函式 selftest 全數保留。**
)


def _demo_seg(no, eid, a, b, state, countable=True):
    """demo 工單段（形狀＝`wem.wo_segments` 的輸出；**零人員欄**）。"""
    return {"start": a, "end": b, "state": state, "countable": countable,
            "ongoing": False, "confidence": "high", "source": "cmms_workorder",
            "work_order_no": no}


def _demo_runs(d0, start_h, end_h, every_h, dur_min):
    """demo 生產區間：從 `start_h` 起每 `every_h` 小時跑 `dur_min` 分鐘。

    ★ 斷料/下游堵的站**本來就是斷斷續續在跑**（有料就跑一下、跑完又等）——用「連跑 8 小時
      然後整晚空著」當 demo 會被 C10 活動鄰近 gate 正確地判成未解釋（夜裡沒訊號＝可能沒排班），
      demo 就演不出斷料/下游堵。這裡的涓流形狀＝那兩種故事的**真實樣子**。
    """
    out, h = [], float(start_h)
    while h < end_h:
        a = d0 + timedelta(hours=h)
        out.append({"state": PROD_STATE, "start": a, "end": a + timedelta(minutes=dur_min)})
        h += every_h
    return out


def _demo_series(day0, pattern, grid_min=GRID_MIN):
    """demo WIP 序列：pattern ∈ {"full","empty","thin","fat"} → 48 點。"""
    pts = []
    for t in grid_points(day0, day0 + timedelta(days=1), grid_min):
        if pattern == "empty":
            pts.append((t, 0.0, 0, 0))
        elif pattern == "thin":
            pts.append((t, 0.2, 2, 0))
        elif pattern == "fat":
            pts.append((t, 12.0, 120, 3))
        else:
            pts.append((t, 3.0, 30, 1))
    return pts


def demo_ledgers(day=DEMO_DAY, grid_min=GRID_MIN, th=TH):
    """§5 demo 源：`{cls: ledger}`（確定性、涵蓋全部桶型；全走 `build_ledger`）。"""
    d0 = _dt(day) or _dt(DEMO_DAY)
    d0 = datetime(d0.year, d0.month, d0.day)
    d1 = d0 + timedelta(days=1)
    H = lambda h: d0 + timedelta(hours=h)      # noqa: E731（demo 內部縮寫）
    out = {}
    for cls, kind in DEMO_SCENARIOS:
        dn_cls = downstream_of(cls)
        # demo 速率：多數站直給 per-machine-hour（讀數才像真的）；`wo` 情境刻意走
        # `lots_day ÷ 本站生產機台小時` 的推導路徑＝兩條 est 取數路都在 demo 上跑過。
        rate = ({"lots_day": 240, "rate_src": "demo"} if kind == "wo"
                else {"rate_per_mh": 20.0, "rate_src": "demo"})
        machines, wo, series, dn = [], {}, _demo_series(d0, "full", grid_min), None
        if kind == "wo":
            machines = [{"eid": "EID-11860", "records": [
                            {"state": PROD_STATE, "start": H(0), "end": H(9)}]},
                        {"eid": "EID-25335", "records": [
                            {"state": PROD_STATE, "start": H(0), "end": H(6)}]}]
            wo = {"EID-11860": [_demo_seg(24101, "EID-11860", H(9), d1, "維修")],
                  "EID-25335": [_demo_seg(24102, "EID-25335", H(6), H(20), "等料(停機)"),
                                _demo_seg(24103, "EID-25335", H(19), H(23),
                                          "暫停·試跑", countable=False)]}
        elif kind == "starved":
            # 有料就跑一下、跑完又等 ⇒ 全日都有生產訊號（gate 開）、門口卻是空的
            machines = [{"eid": "EID-00643", "records": _demo_runs(d0, 0.25, 24, 2, 20)}]
            series = _demo_series(d0, "empty", grid_min)
        elif kind == "blocked":
            machines = [{"eid": "EID-25098", "records": _demo_runs(d0, 0.5, 24, 2, 30)}]
            dn = _demo_series(d0, "fat", grid_min)
        elif kind == "mixed":
            machines = [{"eid": "EID-17431", "records": [
                            {"state": PROD_STATE, "start": H(0), "end": H(10)}]},
                        {"eid": "EID-00884", "records": [
                            {"state": PROD_STATE, "start": H(2), "end": H(14)}]}]
            wo = {"EID-17431": [_demo_seg(33707, "EID-17431", H(10), H(15), "維修")]}
            # 前半日門口空（斷料）、後半日下游塞爆（下游堵）、夜間無訊號（未解釋）
            series = [(t, (0.0 if t < H(12) else 3.0), (0 if t < H(12) else 30), 0)
                      for t in grid_points(d0, d1, grid_min)]
            dn = [(t, (1.0 if t < H(12) else 12.0), 10, 0)
                  for t in grid_points(d0, d1, grid_min)]
        elif kind == "offshift":
            machines = [{"eid": "EID-12724", "records": [
                {"state": PROD_STATE, "start": H(8), "end": H(16)}]}]
            series = _demo_series(d0, "empty", grid_min)      # 全日門口空、但只有白班有訊號
        elif kind == "degraded":
            machines = [{"eid": "EID-26190", "records": [
                {"state": PROD_STATE, "start": H(0), "end": H(11)}]}]
            wo = None                                          # ← 工單源降級
        elif kind == "zero_len":
            machines = [{"eid": "EID-37482", "zero_len_only": True, "records": [
                {"state": PROD_STATE, "start": H(3), "end": H(3)}]}]
        elif kind == "nosig":
            machines = [{"eid": "EID-08122", "records": []}]
        elif kind == "tail":
            machines = [{"eid": "EID-27854", "records": [
                {"state": PROD_STATE, "start": H(0), "end": H(13)}]}]
            series = _demo_series(d0, "thin", grid_min)
        elif kind == "norate":
            machines = [{"eid": "EID-01066", "records": [
                {"state": PROD_STATE, "start": H(0), "end": H(12)}]}]
            rate = None
        out[cls] = build_ledger(cls, machines, wo, series, dn, rate,
                                grid_min=grid_min, th=th, win=(d0, d1), dn_cls=dn_cls)
    return out


def demo_board(day=DEMO_DAY, grid_min=GRID_MIN, th=TH):
    """demo 板（全合成假資料＝頁面必掛大牌；**日帳絕不落 demo**＝`should_persist` 擋）。"""
    leds = demo_ledgers(day, grid_min, th)
    return daily_board(leds, order=[c for c, _k in DEMO_SCENARIOS], day=day,
                       source="demo", wo_source_status="demo",
                       computed_at=f"{day}T23:59:00")


# =============================================================================
# 11. selftest（純離線；SPEC §8 清單逐條）
# =============================================================================
def _mk(day="2026-07-31"):
    d0 = datetime(2026, 7, 31) if day == "2026-07-31" else _dt(day)
    return d0, d0 + timedelta(days=1)


def _series(d0, d1, cover, q=0, s=0, grid_min=30):
    return [(t, cover, q, s) for t in grid_points(d0, d1, grid_min)]


def _selftest():
    fails = []

    def t(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    d0, d1 = _mk()
    H = lambda h: d0 + timedelta(hours=h)      # noqa: E731
    th = thresholds()
    FULL = _series(d0, d1, 3.0, 30, 1)          # 料充足、非斷料非堵
    EMPTY = _series(d0, d1, 0.0, 0, 0)          # 門口空＝斷料
    FAT = _series(d0, d1, 12.0, 120, 0)         # 下游塞爆
    prod24 = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": d0, "end": d1}]}]

    # ---- C9 / route ---------------------------------------------------------
    t("route: 43 stations, BOSS 20/20 covered (C9 assert)",
      len(route_index()) == 43 and assert_route_coverage(
          ["segFrameBonding", "segDiePlacement", "segCalibrationTest",
           "segVacuumPackInspect"]))
    try:
        assert_route_coverage(["segNotAStation"])
        ok = False
    except KeyError:
        ok = True
    t("route: unknown cls raises loudly (never a silent None downstream)", ok)
    t("route: downstream skips the pass-through ord 30 (29 → 31, never 30)",
      downstream_of("segChassisAttach") == "segDryUnitWeightMeasurement"
      and _ord_cls()[30] == "segFinalAssembly"
      and 30 in mw.WIP_PASSTHROUGH_ORDS)
    t("route: downstream of the pass-through station itself is the next real station",
      downstream_of("segFinalAssembly") == "segDryUnitWeightMeasurement")
    t("route: an off-route cls has no downstream (never guessed)",
      downstream_of("segNotAStation") is None and downstream_of(None) is None)
    t("route: line tail has no downstream → blocked bucket can never fire",
      downstream_of("segVacuumPackInspect") is None)

    # ---- 區間代數 -----------------------------------------------------------
    t("interval algebra: subtract/intersect exact",
      _hours(_sub([(d0, H(10))], [(H(2), H(4))])) == 8.0
      and _hours(_inter([(d0, H(10))], [(H(8), H(12))])) == 2.0)

    # ---- T1 恆等式 ＋ 全生產日 ----------------------------------------------
    led = build_ledger("segX", prod24, {}, FULL, None, {"lots_day": 240},
                       win=(d0, d1), th=th)
    t("T1 identity holds on a fully producing day (<=1e-6h)", identity_error(led) <= 1e-6)
    t("all-producing day: 24h producing, every loss bucket 0",
      led["producing_h"] == 24.0 and led["total_mh"] == 24.0 and led["lost_h"] == 0.0
      and led["starved_h"] == 0.0 and led["blocked_h"] == 0.0
      and led["unexplained_h"] == 0.0 and led["top"] == [])

    # ---- all-WO 日 ＋ **純工單機台（零生產段）必須入帳**（T1 名冊 pin）------
    wo_only = {"EID-99": [_demo_seg(1, "EID-99", d0, d1, "維修")]}
    led = build_ledger("segX", [], wo_only, FULL, None, None, win=(d0, d1), th=th)
    t("T1 roster: a machine with ONLY work orders (no production segment) is booked",
      led["fleet_n"] == 1 and led["wo_union_h"] == 24.0 and led["diag"]["n_roster_added"] == 1
      and identity_error(led) <= 1e-6)
    t("all-WO day: nothing leaks into starved/blocked/unexplained",
      led["starved_h"] == 0.0 and led["blocked_h"] == 0.0 and led["unexplained_h"] == 0.0
      and led["top"] == [("wo", 24.0)] and led["wo_top"] == ("維修", 24.0))
    t("all-WO day: wo_h key set = all countable S12 states (hold states never collapsed)",
      set(led["wo_h"]) >= set(COUNTABLE_WO_STATES) and len(COUNTABLE_WO_STATES) == 6
      and "等料(停機)" in led["wo_h"] and "等外包(停機)" in led["wo_h"])

    # 名冊補集在**呼叫端已補**的情況下不重複計（冪等）
    led_b = build_ledger("segX", [{"eid": "EID-99", "records": []}], wo_only, FULL, None,
                         None, win=(d0, d1), th=th)
    t("T1 roster: pre-supplied roster is not double counted (idempotent)",
      led_b["fleet_n"] == 1 and led_b["diag"]["n_roster_added"] == 0
      and led_b["wo_union_h"] == 24.0)

    # ---- T2 優先序：工單與生產重疊 → 歸生產、差額由 wo_overlap_prod_h 揭露 ---
    m = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": d0, "end": H(10)}]}]
    wo = {"E1": [_demo_seg(2, "E1", H(8), H(14), "維修")]}
    led = build_ledger("segX", m, wo, FULL, None, None, win=(d0, d1), th=th)
    t("T2 priority: producing wins over an overlapping work order (no double count)",
      led["producing_h"] == 10.0 and led["wo_union_pre_h"] == 6.0
      and led["wo_overlap_prod_h"] == 2.0 and led["wo_union_h"] == 4.0
      and identity_error(led) <= 1e-6)
    t("§8 reconciliation: wo_union_pre_h == wo_union_h + wo_overlap_prod_h",
      abs(led["wo_union_pre_h"] - (led["wo_union_h"] + led["wo_overlap_prod_h"])) < 1e-9)
    t("S12: uptime-hold segments never enter the ledger",
      build_ledger("segX", m, {"E1": [_demo_seg(3, "E1", H(10), H(14), "暫停·試跑",
                                                countable=False)]},
                   FULL, None, None, win=(d0, d1), th=th)["wo_union_h"] == 0.0)

    # ---- 純斷料日 -----------------------------------------------------------
    m = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": H(i), "end": H(i + 0.5)}
                                   for i in range(0, 24, 2)]}]     # 全日都有生產訊號
    led = build_ledger("segX", m, {}, EMPTY, None, None, win=(d0, d1), th=th)
    t("pure starved day: every idle hour lands in starved (queue+stale == 0)",
      led["starved_h"] > 15 and led["blocked_h"] == 0.0 and led["unexplained_h"] == 0.0
      and identity_error(led) <= 1e-6 and led["top"][0][0] == "starved")

    # ---- 下游堵日 -----------------------------------------------------------
    led = build_ledger("segX", m, {}, FULL, FAT, None, win=(d0, d1), th=th,
                       dn_cls="segY")
    t("blocked day: downstream cover 12h > 4h → blocked",
      led["blocked_h"] > 15 and led["starved_h"] == 0.0 and identity_error(led) <= 1e-6)
    t("priority: starved beats blocked when both would fire",
      build_ledger("segX", m, {}, EMPTY, FAT, None, win=(d0, d1),
                   th=th)["blocked_h"] == 0.0)

    # ---- 下游 None ＝ 不可判 → 未解釋（絕不當 0 也不當 ∞）------------------
    DN_NONE = [(t_, None, 5, 0) for t_ in grid_points(d0, d1)]
    led = build_ledger("segX", m, {}, FULL, DN_NONE, None, win=(d0, d1), th=th)
    t("downstream cover None → not judgeable → unexplained (never blocked)",
      led["blocked_h"] == 0.0 and led["unexplained_h"] > 15)
    led = build_ledger("segX", m, {}, FULL, None, None, win=(d0, d1), th=th)
    t("no downstream series (line tail) → blocked stays exactly 0",
      led["blocked_h"] == 0.0 and led["unexplained_h"] > 15)

    # ---- cover None 兩腿語意（C1）------------------------------------------
    COVNONE_Q0 = [(t_, None, 0, 0) for t_ in grid_points(d0, d1)]
    led = build_ledger("segX", m, {}, COVNONE_Q0, None, None, win=(d0, d1), th=th)
    t("cover None + queue+stale == 0 → still starved (first leg alone)",
      led["starved_h"] > 15)
    COVNONE_Q5 = [(t_, None, 5, 0) for t_ in grid_points(d0, d1)]
    led = build_ledger("segX", m, {}, COVNONE_Q5, None, None, win=(d0, d1), th=th)
    t("cover None + queue > 0 → NEVER starved (None is not 0)",
      led["starved_h"] == 0.0 and led["unexplained_h"] > 15)
    STALE_ONLY = [(t_, 0.1, 0, 4) for t_ in grid_points(d0, d1)]
    led = build_ledger("segX", m, {}, STALE_ONLY, None, None, win=(d0, d1), th=th)
    t("C1: over-aged material at the door still counts as material (cover leg decides)",
      led["starved_h"] > 15)     # cover 0.1 < 0.5 → 斷料（但不是靠 queue+stale==0 那腿）
    STALE_FAT = [(t_, 6.0, 0, 4) for t_ in grid_points(d0, d1)]
    led = build_ledger("segX", m, {}, STALE_FAT, None, None, win=(d0, d1), th=th)
    t("C1: queue 0 but stale 4 with fat cover → not starved",
      led["starved_h"] == 0.0)

    # ---- off-shift gate（C10）----------------------------------------------
    day_only = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": H(8),
                                           "end": H(16)}]}]
    led = build_ledger("segX", day_only, {}, EMPTY, None, None, win=(d0, d1), th=th)
    t("C10 off-shift gate: night idle with no nearby production is NOT called starved",
      led["unexplained_h"] > 10 and led["starved_h"] < 6
      and led["diag"]["off_shift_gated_h"] > 10 and identity_error(led) <= 1e-6)
    t("C10: the ±2h fringe around the shift is still judgeable (gate is proximity, not shift)",
      led["starved_h"] > 3)
    off = thresholds(active_near_h=0)
    led2 = build_ledger("segX", day_only, {}, EMPTY, None, None, win=(d0, d1), th=off)
    t("C10: gate can be disabled by env (then the whole day is starved)",
      led2["starved_h"] == 16.0 and led2["unexplained_h"] == 0.0)

    # ---- 兩層分離：入桶量＝實際閒置秒數（**不是名目 grid_min**）-------------
    m3 = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": d0, "end": H(0.25)},
                                    {"state": PROD_STATE, "start": H(0.75), "end": d1}]}]
    led = build_ledger("segX", m3, {}, EMPTY, None, None, win=(d0, d1), th=th)
    t("two-layer split: a 30-min idle sliver books 0.5h (never the nominal grid cell)",
      abs(led["starved_h"] - 0.5) < 1e-9 and identity_error(led) <= 1e-6)
    led = build_ledger("segX", m3, {}, EMPTY, None, None, win=(d0, d1), th=th, grid_min=17)
    t("two-layer split: booked hours are grid-size independent",
      abs(led["starved_h"] - 0.5) < 1e-9)

    # ---- 混合日 -------------------------------------------------------------
    # E1 連跑一段後進維修；E2 涓流（全日保持生產訊號＝gate 開，讓四桶都可被判定）
    mix_m = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": H(1), "end": H(9)}]},
             {"eid": "E2", "records": _demo_runs(d0, 0.25, 24, 2, 20)}]
    mix_wo = {"E1": [_demo_seg(9, "E1", H(9), H(13), "維修")]}
    # 前半日門口空＝斷料；12–15 點下游 cover 不可測＝未解釋；15 點後下游塞爆＝下游堵
    mix_s = [(t_, (0.0 if t_ < H(12) else 3.0), (0 if t_ < H(12) else 30), 0)
             for t_ in grid_points(d0, d1)]
    mix_dn = [(t_, (1.0 if t_ < H(12) else (None if t_ < H(15) else 12.0)), 10, 0)
              for t_ in grid_points(d0, d1)]
    led = build_ledger("segX", mix_m, mix_wo, mix_s, mix_dn, {"lots_day": 400},
                       win=(d0, d1), th=th, dn_cls="segY")
    t("mixed day: all four buckets non-zero and identity still exact",
      led["wo_union_h"] > 0 and led["starved_h"] > 0 and led["blocked_h"] > 0
      and led["unexplained_h"] > 0 and identity_error(led) <= 1e-6)
    t("mixed day: top is sorted desc and only lists non-zero buckets",
      [b for b, _h in led["top"]] == sorted([b for b, _h in led["top"]],
                                            key=lambda b: -dict(led["top"])[b])
      and all(h > 0 for _b, h in led["top"]))
    t("diag: labelled unexplained matches the exact residual",
      abs(led["diag"]["unexplained_labeled_h"] - led["unexplained_h"]) < 1e-6)

    # ---- #39 該跑窗分母（A5 形 (i)；SPEC-RUNWINDOW §4.3／SPEC-DAILY-ATTRIB §8.4）------
    # ★ 負斷言先行：`rw_by_eid` 未傳 ⇒ **舊 24h 軸逐位不變**（inert fallback 的地板）；
    #   closures 傳了但沒有 rw ⇒ **忽略＋caveat**（closure 桶已退場、分母修正只有該跑窗一條路）。
    base_m = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": H(8), "end": H(16)}]}]
    l_none = build_ledger("segX", base_m, {}, EMPTY, None, {"lots_day": 100},
                          win=(d0, d1), th=th)
    l_cl_norw = build_ledger("segX", base_m, {}, EMPTY, None, {"lots_day": 100},
                             win=(d0, d1), th=th,
                             closures=[{"start": d0, "end": H(6)}])
    t("A5 negative: no rw_by_eid = the old 24h axis, bit-identical; closures without rw are ignored",
      l_none["total_mh"] == 24.0 and l_none["closure_h"] == 0.0
      and {k: v for k, v in l_cl_norw.items() if k != "diag"}
          == {k: v for k, v in l_none.items() if k != "diag"}
      and any("ignored" in c for c in l_cl_norw["diag"]["caveats"])
      and identity_error(l_none) <= 1e-6)
    t("A5: the closure bucket is retired (not in BUCKETS, not in est, not in top)",
      BUCKETS == ("wo", "starved", "blocked", "unexplained")
      and "closure" not in l_none["est"]
      and all(b != "closure" for b, _h in l_none["top"]))

    # rw 軸：E1 該跑窗 08:00–16:40（8.667h）；生產 08–16 全在窗內 ⇒ 閒置 0.667h
    RW1 = {"E1": [(H(8), H(16) + timedelta(minutes=40))]}
    l_rw = build_ledger("segX", base_m, {}, EMPTY, None, None, win=(d0, d1), th=th,
                        rw_by_eid=RW1)
    t("A5: denominator = sum of |RW| (8.667h), not fleet×24h; identity exact on the new axis",
      abs(l_rw["total_mh"] - (8 + 40 / 60)) < 1e-6 and l_rw["diag"]["axis"] == "erw"
      and abs(l_rw["producing_h"] - 8.0) < 1e-9
      and identity_error(l_rw) <= 1e-6)
    # ★★ C-RW4 迴歸釘（QG-13）：同一組 rw 下停機 1h→8h ⇒ 分母恆定、未解釋/工單變化守恆
    wo_a = {"E1": [_demo_seg(41, "E1", H(16), H(17), "維修")]}      # 窗外 1h（16:40 後 20 分在窗外）
    l_rw_a = build_ledger("segX", base_m, wo_a, EMPTY, None, None, win=(d0, d1), th=th,
                          rw_by_eid=RW1)
    t("A5/C-RW11: only the in-window part of a work order is booked (40min of a 16:00-17:00 order)",
      abs(l_rw_a["wo_union_h"] - 40 / 60) < 1e-6
      and abs(l_rw_a["diag"]["wo_outside_rw_h"] - 20 / 60) < 1e-6
      and abs(l_rw_a["total_mh"] - l_rw["total_mh"]) < 1e-9
      and identity_error(l_rw_a) <= 1e-6)
    # 窗外生產＝揭露不入帳（證據不丟；擴窗證據永遠來自別台機）
    m_ot = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": H(8), "end": H(20)}]}]
    l_ot = build_ledger("segX", m_ot, {}, EMPTY, None, None, win=(d0, d1), th=th,
                        rw_by_eid=RW1)
    t("A5: production outside the window is disclosed (diag), never booked, denominator fixed",
      abs(l_ot["producing_h"] - (8 + 40 / 60)) < 1e-6
      and abs(l_ot["diag"]["producing_outside_rw_h"] - (3 + 20 / 60)) < 1e-6
      and abs(l_ot["total_mh"] - l_rw["total_mh"]) < 1e-9
      and any("outside the expected run window" in c for c in l_ot["diag"]["caveats"])
      and identity_error(l_ot) <= 1e-6)

    # A8：非名冊機台整台移出分母、進「不適用」細目（工單時數揭露、不入桶）
    m_na = base_m + [{"eid": "E9", "records": [{"state": PROD_STATE,
                                                "start": H(0), "end": H(24)}]}]
    wo_na = {"E9": [_demo_seg(42, "E9", H(2), H(6), "維修")]}
    l_na = build_ledger("segX", m_na, wo_na, EMPTY, None, None, win=(d0, d1), th=th,
                        rw_by_eid=RW1)
    t("A8: a non-roster machine leaves the denominator entirely (na_* line item, wo disclosed)",
      abs(l_na["total_mh"] - l_rw["total_mh"]) < 1e-9 and l_na["fleet_n"] == 1
      and l_na["diag"]["na_n"] == 1 and l_na["diag"]["na_eids"] == ["E9"]
      and abs(l_na["diag"]["na_mh"] - 24.0) < 1e-9
      and abs(l_na["diag"]["na_wo_h"] - 4.0) < 1e-9
      and identity_error(l_na) <= 1e-6)
    t("A8: a station whose whole fleet is outside the kun catalog reads NOT-APPLICABLE",
      build_ledger("segX", [{"eid": "E9", "records": []}], {}, EMPTY, None, None,
                   win=(d0, d1), th=th, rw_by_eid={})["note"] == NOTE_NOT_IN_KUN)

    # C-PC12（rw 軸形）：uncertain closure 沒被扣分母，其閒置＝誠實殘差（未解釋＋diag）
    RW2 = {"E1": [(H(8), H(18))]}                     # 10h 窗（含 16–18 閒置）
    l_unc = build_ledger("segX", base_m, {}, EMPTY, None, None, win=(d0, d1), th=th,
                         rw_by_eid=RW2,
                         closures=[{"start": H(16), "end": H(18),
                                    "uncertain_eids": ["E1"]}])
    t("C-PC12 on the rw axis: uncertain-closure idle lands in unexplained + diag, denominator uncut",
      abs(l_unc["total_mh"] - 10.0) < 1e-9
      and abs(l_unc["diag"]["closure_uncertain_h"] - 2.0) < 1e-9
      and l_unc["unexplained_h"] >= 2.0 and identity_error(l_unc) <= 1e-6)
    # degraded 工單源 ⇒ 連 uncertain 標記都不做（C-PC12 需要工單佐證）；rw 分母照常
    l_deg2 = build_ledger("segX", base_m, None, EMPTY, None, None, win=(d0, d1), th=th,
                          rw_by_eid=RW2,
                          closures=[{"start": H(16), "end": H(18),
                                     "uncertain_eids": ["E1"]}])
    t("C-PC12: with a degraded work-order source even the uncertainty marking is off",
      l_deg2["diag"]["closure_active"] is False
      and l_deg2["diag"]["closure_uncertain_h"] == 0.0
      and abs(l_deg2["total_mh"] - 10.0) < 1e-9 and identity_error(l_deg2) <= 1e-6)

    # C10 gate 作用域＝窗內（RW 內夜段閒置、無鄰近訊號 ⇒ 未解釋，不誤標斷料）
    RW3 = {"E1": [(H(0), H(24))]}
    l_gate = build_ledger("segX", base_m, {}, EMPTY, None, None, win=(d0, d1), th=th,
                          rw_by_eid=RW3)
    t("C10 gate still guards idle inside the window (far-from-signal idle -> unexplained)",
      l_gate["diag"]["off_shift_gated_h"] > 0 and identity_error(l_gate) <= 1e-6)

    t("A5: malformed closure entries are dropped, never guessed",
      norm_closures([{"start": None, "end": H(3)}, {"start": H(5), "end": H(5)},
                     "nope", {"start": H(1), "end": H(3)}], d0, d1)
      == [(H(1), H(3), frozenset())])
    t("T6: closure_h stays None on unmeasurable rows (schema tail unchanged for old readers)",
      build_ledger("segX", [{"eid": "E1", "records": []}], {}, FULL, None, None,
                   win=(d0, d1), th=th)["closure_h"] is None)

    # ---- M3 日帳 v4：站級 `rw` 區塊（A/U 材料）＋ `_wo_intervals_by_state` -------------
    # ★ 負斷言先行（本輪地板）：沒有 `rw_by_eid` ⇒ **連鍵都不長出來**、輸出逐位元同前。
    t("V4 negative: without rw_by_eid the `rw` key does not exist at all (bit-identical output)",
      "rw" not in l_none and "rw" not in l_cl_norw
      and "rw" not in build_ledger("segX", prod24, {}, FULL, None, {"lots_day": 240},
                                   win=(d0, d1), th=th)
      and "rw" not in build_ledger("segX", [{"eid": "E1", "records": []}], {}, FULL,
                                   None, None, win=(d0, d1), th=th))
    t("V4: `rw` rides on the rw axis only; planned_sec == total_mh*3600, n_eid == fleet_n, src='erw'",
      isinstance(l_rw.get("rw"), dict)
      and abs(l_rw["rw"]["planned_sec"] - l_rw["total_mh"] * 3600.0) < 1e-6
      and abs(l_rw["rw"]["producing_sec"] - l_rw["producing_h"] * 3600.0) < 1e-6
      and l_rw["rw"]["n_eid"] == l_rw["fleet_n"] == 1
      and l_rw["rw"]["src"] == "erw")
    t("V4: `rw` never touches the T1 identity (adding the block leaves every bucket untouched)",
      {k: v for k, v in l_rw.items() if k != "rw"}
      == {k: v for k, v in build_ledger("segX", base_m, {}, EMPTY, None, None,
                                        win=(d0, d1), th=th, rw_by_eid=RW1).items()
          if k != "rw"}
      and identity_error(l_rw) <= 1e-6 and l_rw["rw"]["health_down_sec"] == 0.0)
    # A8：非名冊機台移出分母後，`n_eid` 數的是**進分母的台數**（不是輸入的台數）
    t("V4: n_eid counts the machines that actually entered the denominator (A8 already applied)",
      l_na["rw"]["n_eid"] == l_na["fleet_n"] == 1
      and abs(l_na["rw"]["planned_sec"] - l_na["total_mh"] * 3600.0) < 1e-6)
    # ---- W3 座帳（`SPEC-CAPACITY-FORECAST.md` §2；additive、預設關） -------------------
    # 負斷言先行：不傳 `seats` ⇒ **三個鍵一個都不長**、輸出逐位元同前。
    t("W3 negative: without `seats` the rw block is bit-identical (no seats / no nameplate)",
      "seats" not in l_rw["rw"] and "planned_sec_nameplate" not in l_rw["rw"]
      and "equiv_factor" not in l_rw["rw"])
    _RWS = {"E1": [(H(8), H(18))], "E2": [(H(8), H(18))]}          # 兩台各 10h
    _MS = [{"eid": "E1", "records": []}, {"eid": "E2", "records": []}]
    _WOS = {"E1": [_demo_seg(61, "E1", H(9), H(10), "維修")]}       # 有工單 ⇒ 兩台都可測
    _l_seat_none = build_ledger("segX", _MS, _WOS, EMPTY, None, None, win=(d0, d1), th=th,
                                rw_by_eid=_RWS)
    # ① 部分座長停（E1 四座停兩座）⇒ **分母一個位元不動** ⇒ A／U 完全不變（C-CF8 的負斷言）
    _l_part = build_ledger("segX", _MS, _WOS, EMPTY, None, None, win=(d0, d1), th=th,
                           rw_by_eid=_RWS,
                           seats={"E1": {"installed": 4, "active": 2,
                                         "dormant": ["E1/nest:2", "E1/nest:4"]},
                                  "E2": {"installed": 1, "active": 1, "dormant": []}})
    t("W3 C-CF8: partial seat dormancy never moves planned_sec (A/U bit-identical)",
      abs(_l_part["rw"]["planned_sec"] - _l_seat_none["rw"]["planned_sec"]) < 1e-9
      and abs(_l_part["rw"]["planned_sec_nameplate"]
              - _l_seat_none["rw"]["planned_sec"]) < 1e-9
      and _l_part["rw"]["producing_sec"] == _l_seat_none["rw"]["producing_sec"]
      and _l_part["rw"]["avail_down_sec"] == _l_seat_none["rw"]["avail_down_sec"])
    t("W3: seats aggregate over the machines that entered the denominator, dormant ids sorted",
      _l_part["rw"]["seats"] == {"installed": 5, "active": 3,
                                 "dormant": ["E1/nest:2", "E1/nest:4"]})
    # ② 整台機一座都不剩 ⇒ 宣告分母掉那一台、銘牌保留舊值（SPEC-DORMANT §3.1「不進分母」）
    _l_off = build_ledger("segX", _MS, _WOS, EMPTY, None, None, win=(d0, d1), th=th,
                          rw_by_eid=_RWS,
                          seats={"E1": {"installed": 1, "active": 0, "dormant": ["E1"]},
                                 "E2": {"installed": 1, "active": 1, "dormant": []}})
    t("W3: a machine with zero active seats leaves the declared denominator, nameplate keeps it",
      abs(_l_off["rw"]["planned_sec"] - 10 * 3600.0) < 1e-6
      and abs(_l_off["rw"]["planned_sec_nameplate"] - 20 * 3600.0) < 1e-6
      and _l_off["rw"]["seats"] == {"installed": 2, "active": 1, "dormant": ["E1"]}
      and abs(_l_off["total_mh"] - 20.0) < 1e-9 and identity_error(_l_off) <= 1e-6)
    # ③ W1 係數只是搭便車的加法欄；壞值不寫、不炸
    t("W1: equiv_factor rides along as an additive field; a bad value is dropped, never raised",
      build_ledger("segX", _MS, _WOS, EMPTY, None, None, win=(d0, d1), th=th,
                   rw_by_eid=_RWS, equiv_factor=0.5)["rw"]["equiv_factor"] == 0.5
      and "equiv_factor" not in build_ledger("segX", _MS, _WOS, EMPTY, None, None,
                                             win=(d0, d1), th=th, rw_by_eid=_RWS,
                                             equiv_factor="nope")["rw"])

    # ---- WP-C2 座‧小時尺（`SPEC-DAILY-ATTRIB.md` §8.6；Chris 2026-09-12） -------------
    #   兩台各 10h 該跑窗、各整窗生產（`_MS` 的機台無 records ⇒ 生產 0）⇒ 先造有生產的形。
    _P10 = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": H(8), "end": H(18)}]},
            {"eid": "E2", "records": [{"state": PROD_STATE, "start": H(8), "end": H(13)}]}]
    _l_c2 = build_ledger("segX", _P10, _WOS, EMPTY, None, {"rate_per_mh": 8.0},
                         win=(d0, d1), th=th, rw_by_eid=_RWS)
    t("C2 negative: `by_eid` rides on the rw axis only (no rw_by_eid ⇒ no rw block at all)",
      "rw" not in build_ledger("segX", _P10, _WOS, EMPTY, None, {"rate_per_mh": 8.0},
                               win=(d0, d1), th=th))
    t("C2: `by_eid` sums to the station producing_sec (same loop, same ruler)",
      abs(sum(v["producing_sec"] for v in _l_c2["rw"]["by_eid"].values())
          - _l_c2["rw"]["producing_sec"]) < 1e-3
      and set(_l_c2["rw"]["by_eid"]) == set(_l_c2["rw"]["eids"]))
    t("C2: without a seat ledger every machine counts as one seat "
      "(seat_h ≡ producing_h ⇒ r_seat ≡ rate_per_mh, bit-identical)",
      all(v["seats_active"] == 1 and v["seats_installed"] == 1
          for v in _l_c2["rw"]["by_eid"].values())
      and _l_c2["rw"]["seat_h"] == _l_c2["producing_h"]
      and _l_c2["rw"]["r_seat"] == _l_c2["est"]["rate_per_mh"] == 8.0)
    # 混座站：E1 四座在役、E2 單座 ⇒ seat_h = 10*4 + 5*1 = 45h、產出 = 8 × 15h = 120PH
    _l_mix = build_ledger("segX", _P10, _WOS, EMPTY, None, {"rate_per_mh": 8.0},
                          win=(d0, d1), th=th, rw_by_eid=_RWS,
                          seats={"E1": {"installed": 4, "active": 4, "dormant": []},
                                 "E2": {"installed": 1, "active": 1, "dormant": []}})
    t("C2: a mixed-seat station divides by seat-hours, not machine-hours "
      "(this is the whole point — CAL/CLN stop being averaged over 5 unequal machines)",
      abs(_l_mix["rw"]["seat_h"] - 45.0) < 1e-6
      and abs(_l_mix["rw"]["r_seat"] - 120.0 / 45.0) < 1e-6
      and _l_mix["rw"]["by_eid"]["E1"]["seats_active"] == 4
      and _l_mix["rw"]["by_eid"]["E2"]["seats_active"] == 1)
    t("C2: dormant seats leave the seat-hour denominator (active, never installed)",
      abs(build_ledger("segX", _P10, _WOS, EMPTY, None, {"rate_per_mh": 8.0},
                       win=(d0, d1), th=th, rw_by_eid=_RWS,
                       seats={"E1": {"installed": 4, "active": 2,
                                     "dormant": ["E1/nest:3", "E1/nest:4"]},
                              "E2": {"installed": 1, "active": 1, "dormant": []}}
                       )["rw"]["seat_h"] - 25.0) < 1e-6)
    t("C2: no rate ⇒ r_seat is None (never invented); seat_h is still reported",
      build_ledger("segX", _P10, _WOS, EMPTY, None, None,
                   win=(d0, d1), th=th, rw_by_eid=_RWS)["rw"]["r_seat"] is None
      and build_ledger("segX", _P10, _WOS, EMPTY, None, None,
                       win=(d0, d1), th=th, rw_by_eid=_RWS)["rw"]["seat_h"] > 0)
    # 零生產（seat_h=0）⇒ 分母 0 ⇒ None（不是 0.0、也不是 inf）
    _l_z = build_ledger("segX", _MS, _WOS, EMPTY, None, {"rate_per_mh": 8.0},
                        win=(d0, d1), th=th, rw_by_eid=_RWS)
    t("C2: a zero seat-hour denominator yields r_seat None (not 0.0, not a division error)",
      _l_z["rw"]["seat_h"] == 0.0 and _l_z["rw"]["r_seat"] is None)
    # 座帳只涵蓋一部分機台（`machine_seats` 對某台算不出來）⇒ 沒涵蓋的那台當單座，不是當 0
    t("C2: a machine missing from the seat ledger degrades to one seat, never raises",
      build_ledger("segX", _P10, _WOS, EMPTY, None, {"rate_per_mh": 8.0},
                   win=(d0, d1), th=th, rw_by_eid=_RWS,
                   seats={"E2": {"installed": 1, "active": 1, "dormant": []}}
                   )["rw"]["by_eid"]["E1"] == {"producing_sec": _r6(10 * 3600.0),
                                               "seats_active": 1, "seats_installed": 1}
      and _seat_count(None, "E1", "active") == _seat_count({"E1": None}, "E1", "active")
      == _seat_count({"E1": {"active": "x"}}, "E1", "active") == 1)
    t("C2: the three new fields never touch the T1 identity nor any older rw field",
      identity_error(_l_mix) <= 1e-6
      and {k: v for k, v in _l_mix["rw"].items()
           if k not in ("by_eid", "seat_h", "r_seat")}
      == {k: v for k, v in build_ledger(
          "segX", _P10, _WOS, EMPTY, None, {"rate_per_mh": 8.0}, win=(d0, d1), th=th,
          rw_by_eid=_RWS, seats={"E1": {"installed": 4, "active": 4, "dormant": []},
                                 "E2": {"installed": 1, "active": 1, "dormant": []}}
      )["rw"].items() if k not in ("by_eid", "seat_h", "r_seat")})

    # 白名單：維修/計畫保養扣；等料(停機)／調機（legacy）**不扣**（那是廣效率 U 那一側）
    RW4 = {"E1": [(H(6), H(18))]}                    # 12h 該跑窗
    wo_wl = {"E1": [_demo_seg(51, "E1", H(6), H(8), "維修"),
                    _demo_seg(52, "E1", H(8), H(9), "計畫保養"),
                    _demo_seg(53, "E1", H(9), H(12), "等料(停機)"),
                    _demo_seg(54, "E1", H(12), H(14), "調機")]}
    l_wl = build_ledger("segX", base_m, wo_wl, EMPTY, None, None, win=(d0, d1), th=th,
                        rw_by_eid=RW4)
    t("V4: health_down_sec is a WHITELIST (維修+計畫保養 = 3h); 等料(停機)/調機 never deducted",
      abs(l_wl["rw"]["health_down_sec"] - 3 * 3600.0) < 1e-6
      and set(woee.RW_HEALTH_STATES) == {"維修", "計畫保養"}
      and l_wl["wo_h"]["等料(停機)"] == 3.0 and l_wl["wo_h"]["調機"] == 2.0
      and identity_error(l_wl) <= 1e-6)
    # health ∩ RW：窗外那一段不扣（分子分母同窗＝C-RW11）
    wo_out = {"E1": [_demo_seg(55, "E1", H(4), H(8), "維修")]}     # 04–06 在 RW 之外
    l_ho = build_ledger("segX", base_m, wo_out, EMPTY, None, None, win=(d0, d1), th=th,
                        rw_by_eid=RW4)
    t("V4: health_down_sec is intersected with the run window (only 06:00-08:00 of a 04:00-08:00 order)",
      abs(l_ho["rw"]["health_down_sec"] - 2 * 3600.0) < 1e-6
      and abs(l_ho["diag"]["wo_outside_rw_h"] - 2.0) < 1e-6)
    # 同態先 union 再 ∩：重疊的兩張維修單不得算兩次（`wo_h` 的原始加總故意仍是 5h）
    wo_ov = {"E1": [_demo_seg(56, "E1", H(8), H(11), "維修"),
                    _demo_seg(57, "E1", H(9), H(11), "維修")]}
    l_ov = build_ledger("segX", base_m, wo_ov, EMPTY, None, None, win=(d0, d1), th=th,
                        rw_by_eid=RW4)
    t("V4: same-state overlap is unioned first (3h, not 5h) while wo_h keeps the raw sum (5h)",
      abs(l_ov["rw"]["health_down_sec"] - 3 * 3600.0) < 1e-6
      and abs(l_ov["wo_h"]["維修"] - 5.0) < 1e-9)
    # 工單源 degraded ⇒ 不主張任何停機（health 恆 0；分母照常）
    l_hdeg = build_ledger("segX", base_m, None, EMPTY, None, None, win=(d0, d1), th=th,
                          rw_by_eid=RW4)
    t("V4: a degraded work-order source claims zero health downtime (never 'no data' = 'no repair')",
      l_hdeg["rw"]["health_down_sec"] == 0.0
      and abs(l_hdeg["rw"]["planned_sec"] - 12 * 3600.0) < 1e-6)
    # ---- ★ M3 日帳 v5（2026-09-04；`SPEC-CAPACITY.md` §3.1「加法、不是改名」）---------
    t("V5: `avail_down_sec` deducts the FULL down whitelist (維修2+保養1+等料3+調機2 = 8h) "
      "while `health_down_sec` keeps the old narrow value (3h) side by side",
      abs(l_wl["rw"]["avail_down_sec"] - 8 * 3600.0) < 1e-6
      and abs(l_wl["rw"]["health_down_sec"] - 3 * 3600.0) < 1e-6)
    t("V5: `down_by_state` is the per-state (event) view - its sum may exceed avail_down_sec, "
      "and every key is on the whitelist",
      set(l_wl["rw"]["down_by_state"]) == {"維修", "計畫保養", "等料(停機)", "調機"}
      and abs(sum(l_wl["rw"]["down_by_state"].values()) - 8 * 3600.0) < 1e-6
      and all(k in woee.RW_AVAIL_DOWN_STATES for k in l_wl["rw"]["down_by_state"]))
    # ★ 跨態 union：兩張**不同態**的重疊單只算一次（A 的分子），但 `down_by_state` 各記各的
    wo_xov = {"E1": [_demo_seg(63, "E1", H(8), H(12), "維修"),
                     _demo_seg(64, "E1", H(10), H(14), "等料(停機)")]}
    l_xov = build_ledger("segX", base_m, wo_xov, EMPTY, None, None, win=(d0, d1), th=th,
                         rw_by_eid=RW4)
    t("V5: cross-state overlap is unioned for avail_down_sec (6h, not 8h) but each state "
      "keeps its own hours in down_by_state (4h + 4h) - two questions, two numbers",
      abs(l_xov["rw"]["avail_down_sec"] - 6 * 3600.0) < 1e-6
      and abs(l_xov["rw"]["down_by_state"]["維修"] - 4 * 3600.0) < 1e-6
      and abs(l_xov["rw"]["down_by_state"]["等料(停機)"] - 4 * 3600.0) < 1e-6
      and sum(l_xov["rw"]["down_by_state"].values()) > l_xov["rw"]["avail_down_sec"])
    t("V5: avail_down_sec is intersected with the run window, exactly like the old column",
      abs(l_ho["rw"]["avail_down_sec"] - 2 * 3600.0) < 1e-6)
    t("V5: a degraded work-order source claims zero downtime on the new column too "
      "(never 'no data' = 'availability 100%')",
      l_hdeg["rw"]["avail_down_sec"] == 0.0 and l_hdeg["rw"]["down_by_state"] == {})
    t("V5: `eids` lists the machines that actually entered the denominator (len == n_eid) "
      "- that is what makes a later work-order-only recompute auditable",
      l_wl["rw"]["eids"] == ["E1"] and len(l_wl["rw"]["eids"]) == l_wl["rw"]["n_eid"]
      and l_na["rw"]["eids"] == sorted(l_na["rw"]["eids"])
      and len(l_na["rw"]["eids"]) == l_na["rw"]["n_eid"])
    t("V5 negative: a non-whitelist state never reaches either new column "
      "(不明/待機 = 'we do not know', not 'it is broken')",
      all(build_ledger("segX", base_m,
                       {"E1": [_demo_seg(65, "E1", H(8), H(11), _st)]},
                       EMPTY, None, None, win=(d0, d1), th=th,
                       rw_by_eid=RW4)["rw"]["avail_down_sec"] == 0.0
          for _st in ("不明", "待機", "流程等待", "非排定")))
    # ---- ★ R44 C-AV16（2026-09-29）：形③ 父機 `avail_rollup` 覆寫（呼叫端算好的座推導）----
    _ar = {"E1": {"avail_down_sec": 1.5 * 3600.0, "down_by_state": {"維修": 1.5 * 3600.0,
                                                                     "不明": 9.0},
                  "excl_sec": 0.0}}
    l_ar = build_ledger("segX", base_m, wo_xov, EMPTY, None, None, win=(d0, d1), th=th,
                        rw_by_eid=RW4, avail_rollup=_ar)
    t("R44: avail_rollup replaces avail_down_sec / down_by_state for that EID (whitelist "
      "still bites) and nothing else in the ledger moves",
      abs(l_ar["rw"]["avail_down_sec"] - 1.5 * 3600.0) < 1e-6
      and l_ar["rw"]["down_by_state"] == {"維修": 1.5 * 3600.0}
      and l_ar["rw"]["health_down_sec"] == l_xov["rw"]["health_down_sec"]
      and l_ar["wo_union_h"] == l_xov["wo_union_h"]
      and l_ar["rw"]["planned_sec"] == l_xov["rw"]["planned_sec"])
    t("R44 negative: avail_rollup=None / {} / another EID => bit-identical ledger",
      build_ledger("segX", base_m, wo_xov, EMPTY, None, None, win=(d0, d1), th=th,
                   rw_by_eid=RW4, avail_rollup={}) == l_xov
      and build_ledger("segX", base_m, wo_xov, EMPTY, None, None, win=(d0, d1), th=th,
                       rw_by_eid=RW4, avail_rollup={"E9": _ar["E1"]}) == l_xov)
    _ar_x = {"E1": dict(_ar["E1"], excl_sec=12 * 3600.0)}
    t("R44: excl_sec (no seat in service) leaves the planned denominator",
      build_ledger("segX", base_m, wo_xov, EMPTY, None, None, win=(d0, d1), th=th,
                   rw_by_eid=RW4, avail_rollup=_ar_x)["rw"]["planned_sec"] == 0.0)
    # `_wo_union_sec` 純函式直測（白名單、跨態 union、uncountable 段不算）
    t("_wo_union_sec: whitelist + cross-state union + countable-only",
      abs(_wo_union_sec([_demo_seg(66, "E1", H(8), H(12), "維修"),
                         _demo_seg(67, "E1", H(10), H(14), "計畫保養"),
                         _demo_seg(68, "E1", H(14), H(16), "不明"),
                         _demo_seg(69, "E1", H(16), H(18), "維修", countable=False)],
                        d0, d1, None, woee.RW_AVAIL_DOWN_STATES) - 6 * 3600.0) < 1e-9
      and _wo_union_sec([], d0, d1, None, woee.RW_AVAIL_DOWN_STATES) == 0.0)
    # `_wo_intervals_by_state` 純函式直測（零秒態不入回傳；base=None ⇒ 只裁窗）
    _bys = _wo_intervals_by_state(
        [_demo_seg(58, "E1", H(8), H(11), "維修"),
         _demo_seg(59, "E1", H(9), H(11), "維修"),
         _demo_seg(60, "E1", H(11), H(12), "計畫保養"),
         _demo_seg(61, "E1", H(20), H(21), "維修", countable=False)],
        d0, d1, [(H(9), H(11) + timedelta(minutes=30))])
    t("_wo_intervals_by_state: per-state union then intersect; uncountable segs and zero-second states dropped",
      abs(_bys["維修"] - 2 * 3600.0) < 1e-9 and abs(_bys["計畫保養"] - 1800.0) < 1e-9
      and set(_bys) == {"維修", "計畫保養"}
      and _wo_intervals_by_state([], d0, d1, [(d0, d1)]) == {}
      and abs(_wo_intervals_by_state(
          [_demo_seg(62, "E1", H(8), H(11), "維修")], d0, d1)["維修"] - 3 * 3600.0) < 1e-9)
    # 文件層：`rw`/`families` 是尾欄、透傳、且 `_scrub` 不吃 EID／族名
    _b_v4 = daily_board({"segX": dict(l_wl, families={"E1": {"Plover": 3, "Kestrel": 1}})},
                        day="2026-07-31", source="live", wo_source_status="ok")
    _d_v4 = ledger_doc(_b_v4, day="2026-07-31", source="live", wo_source_status="ok")
    _s_v4 = _d_v4["stations"][0]
    t("V4 doc: `rw`/`families` are additive TAIL keys, passed through, and survive `_scrub`",
      _DOC_STATION_KEYS[-2:] == ("rw", "families")
      and _DOC_STATION_KEYS[:17] == ("cls", "fleet_n", "total_mh", "producing_h",
                                     "wo_union_h", "wo_h", "wo_union_pre_h",
                                     "wo_overlap_prod_h", "starved_h", "blocked_h",
                                     "unexplained_h", "lost_h", "unmeasurable", "note",
                                     "closure_h", "rate_per_mh", "rate_src")
      and _s_v4["rw"]["src"] == "erw" and _s_v4["rw"]["n_eid"] == 1
      and _s_v4["families"] == {"E1": {"Plover": 3, "Kestrel": 1}}
      and json.dumps(_d_v4, sort_keys=True) is not None)
    t("V4 doc: a 24h-axis / unmeasurable row reads rw=None and families=None (None != {})",
      _doc_station(l_none)["rw"] is None and _doc_station(l_none)["families"] is None
      and _doc_station(build_ledger("segX", [{"eid": "E1", "records": []}], {}, FULL,
                                    None, None, win=(d0, d1), th=th))["rw"] is None)


    # ---- est per-machine-hour（C5；§9 反模式：站級 rate × 機台小時）---------
    # 3 台機、每台生產 8h ＝ 24 生產機台小時、當日 240 顆 ⇒ 每機台小時 10 顆
    m3f = [{"eid": f"E{i}", "records": [{"state": PROD_STATE, "start": d0, "end": H(8)}]}
           for i in range(3)]
    wo1 = {"E0": [_demo_seg(11, "E0", H(8), H(9), "維修")]}         # 一台停 1h
    led = build_ledger("segX", m3f, wo1, FULL, None, {"lots_day": 240},
                       win=(d0, d1), th=th)
    t("C5 est: ONE machine down 1h ≈ one machine-hour of capacity (10), not 3×",
      led["est"]["rate_per_mh"] == 10.0 and led["est"]["wo"] == 10)
    wo3 = {f"E{i}": [_demo_seg(12 + i, f"E{i}", H(8), H(9), "維修")] for i in range(3)}
    led3 = build_ledger("segX", m3f, wo3, FULL, None, {"lots_day": 240},
                        win=(d0, d1), th=th)
    t("C5 est: the WHOLE station down 1h = 3 machine-hours = 30",
      led3["est"]["wo"] == 30)
    t("C5 est: rate_ctx=None → est None, ledger hours still delivered",
      build_ledger("segX", m3f, wo1, FULL, None, None, win=(d0, d1),
                   th=th)["est"] is None)
    t("C5 est: rate 0 lots with real producing hours → measured 0.0 (not None)",
      build_ledger("segX", m3f, wo1, FULL, None, {"lots_day": 0}, win=(d0, d1),
                   th=th)["est"]["rate_per_mh"] == 0.0)
    t("C5 est: zero producing hours → rate None (no divide-by-zero fiction)",
      build_ledger("segX", [], wo_only, FULL, None, {"lots_day": 240}, win=(d0, d1),
                   th=th)["est"] is None)
    # 分母下限（2026-08-02 live 事故：瞬時段站 0.0017h 生產 → est 顯 ≈340 萬顆）
    t("C5 est: producing below MIN_PROD_MH → (None,'thin_prod_hours')（不顯天文數字）",
      _rate_per_mh({"lots_day": 237}, 0.0017) == (None, "thin_prod_hours"))
    t("C5 est: floor 邊界＝恰在下限（含等於）照算",
      _rate_per_mh({"lots_day": 10}, MIN_PROD_MH) == (10.0 / MIN_PROD_MH, "y24_raw"
                   ) or _rate_per_mh({"lots_day": 10}, MIN_PROD_MH)[0] == 10.0 / MIN_PROD_MH)
    t("C5 est: rate_ctx 可覆寫下限（env 由 app 端接）",
      _rate_per_mh({"lots_day": 10, "min_prod_mh": 2.0}, 1.0) == (None, "thin_prod_hours"))
    t("C5 est: 直給 rate_per_mh 不套下限（demo/測試受信輸入）",
      _rate_per_mh({"rate_per_mh": 10.0}, 0.001)[0] == 10.0)

    # ---- 工單源降級（C6）----------------------------------------------------
    led = build_ledger("segX", day_only, None, EMPTY, FAT, None, win=(d0, d1), th=th)
    t("degraded WO source: everything idle → unexplained, wo_h None, flagged",
      led["diag"]["wo_source"] == "degraded" and led["wo_h"] is None
      and led["starved_h"] == 0.0 and led["blocked_h"] == 0.0
      and led["unexplained_h"] == 16.0 and identity_error(led) <= 1e-6)

    # ---- T6 不可測（絕不填 0）----------------------------------------------
    zl = build_ledger("segX", [{"eid": "E1", "zero_len_only": True, "records": []}], {},
                      FULL, None, None, win=(d0, d1), th=th)
    t("T6 zero-length-only station → unmeasurable, every bucket None (never 0)",
      zl["unmeasurable"] and zl["note"] == NOTE_ZERO_LEN and zl["producing_h"] is None
      and zl["starved_h"] is None and zl["unexplained_h"] is None and zl["est"] is None)
    ns = build_ledger("segX", [{"eid": "E1", "records": []}], {}, FULL, None, None,
                      win=(d0, d1), th=th)
    t("T6 no signal + no work order → unmeasurable 'nosig' (not 24h of unexplained)",
      ns["unmeasurable"] and ns["note"] == NOTE_NOSIG)
    # ★ #182-P2 F2：零工期八站＝站級常數。一台零工期機＋一台 C-RW18 stub（整窗不明）
    #   舊閘（n_zlo == fleet_n）過不了 ⇒ 「可測、0 生產」；給了常數集 ⇒ 直接 zero_len。
    _stub = {"eid": "E2", "records": [{"state": "不明", "start": d0, "end": d1}],
             "roster_nosig": True}
    _zmix = [{"eid": "E1", "zero_len_only": True, "records": []}, _stub]
    # WP-10d（R-P11）：零工期站清單是名冊常數（私有樹＝8 站、釘 segThermalCure；公開樹＝合成名冊的站集）
    import core_paths as _wp
    _pub = _wp.is_public_tree()
    _zc = sorted(ZERO_LEN_STATIONS)[0] if _pub else "segThermalCure"
    zold = build_ledger(_zc, _zmix, {}, FULL, None, None, win=(d0, d1), th=th)
    znew = build_ledger(_zc, _zmix, {}, FULL, None, None, win=(d0, d1), th=th,
                        zero_len_classes=ZERO_LEN_STATIONS)
    t("#182-P2 F2: ZL8 station + roster stub → zero_len via the station constant "
      "(without it the old gate books a 0-production denominator)",
      not zold["unmeasurable"] and znew["unmeasurable"] and znew["note"] == NOTE_ZERO_LEN
      and znew["total_mh"] is None and znew["diag"].get("zero_len_station") is True)
    zoth = build_ledger("segX", _zmix, {}, FULL, None, None, win=(d0, d1), th=th,
                        zero_len_classes=ZERO_LEN_STATIONS)
    t("#182-P2 F2: a non-ZL8 station with the same mixed fleet stays measurable "
      "(the constant is per-station, not per-machine)",
      not zoth["unmeasurable"] and (zoth["total_mh"] or 0) > 0)
    try:
        import mes_timeline as _mlt
        _ast = set(_mlt.EQP_STATIONS)
    except Exception:
        _ast = None
    t("#182-P2 F2: ZERO_LEN_STATIONS is the 8-station hand-curated list, all in EQP_STATIONS "
      "(never runtime discovery)",
      (len(ZERO_LEN_STATIONS) > 0 if _pub else len(ZERO_LEN_STATIONS) == 8)
      and (_ast is None or ZERO_LEN_STATIONS <= _ast))
    nf = build_ledger("segX", [], {}, FULL, None, None, win=(d0, d1), th=th)
    t("T6 empty roster → unmeasurable 'no_fleet'",
      nf["unmeasurable"] and nf["note"] == NOTE_NO_FLEET and nf["fleet_n"] == 0)
    t("T6: identity_error is 0 for unmeasurable rows (no ledger to check)",
      identity_error(zl) == 0.0)
    # 形狀對等：不可測列與正常列的 key 集必須一模一樣，否則模板取一個欄位就 KeyError
    ok_led = build_ledger("segX", prod24, {}, FULL, None, {"lots_day": 24},
                          win=(d0, d1), th=th)
    t("shape parity: unmeasurable rows carry exactly the same keys as normal rows",
      set(zl) == set(ok_led) == set(ns) == set(nf))

    # 來源側失敗（timeline 查不到／預算跳站）≠「名冊上沒有機台」——兩者不同 note、不同文案
    se = unavailable_ledger("segX", win=(d0, d1))
    t("src_err: a failed/skipped source is its own note, never reported as an empty roster",
      se["unmeasurable"] and se["note"] == NOTE_SRC_ERR and se["note"] != nf["note"]
      and se["fleet_n"] == 0 and set(se) == set(ok_led)
      and NOTE_I18N_KEY[NOTE_SRC_ERR] != NOTE_I18N_KEY[NOTE_NO_FLEET]
      and any("NOT the same as an empty roster" in c for c in se["diag"]["caveats"]))
    t("note keys: no_window has its own wording (it is not 'no signal in the window')",
      NOTE_I18N_KEY[NOTE_NO_WINDOW] != NOTE_I18N_KEY[NOTE_NOSIG]
      and all(NOTE_I18N_KEY[n] in I18N_ATTRIB[lg]
              for n in (NOTE_SRC_ERR, NOTE_NO_WINDOW) for lg in ("en", "zh")))

    # 矛盾輸入防呆：宣稱 wo_source='ok' 卻沒給 wo_segs ⇒ 以證據為準（強制 degraded）
    idle_m = [{"eid": "E1", "records": [{"state": PROD_STATE, "start": d0, "end": H(1)}]}]
    lie = build_ledger("segX", idle_m, None, EMPTY, None, None, win=(d0, d1), th=th,
                       wo_source="ok")
    t("wo_source: None segments + a claimed 'ok' source is forced to degraded (evidence wins)",
      lie["diag"]["wo_source"] == "degraded" and lie["starved_h"] == 0.0
      and lie["blocked_h"] == 0.0 and lie["unexplained_h"] == 23.0
      and identity_error(lie) <= 1e-6)

    # 退化網格（所有 WIP 點都 ≥ win_end ⇒ 無有效 cell）：整片閒置只記一次
    far = [(d1 + timedelta(hours=1), 3.0, 5, 0)]
    dg = build_ledger("segX", idle_m, {}, far, None, None, win=(d0, d1), th=th)
    t("degenerate grid: idle time is booked once (labelled total == exact residual)",
      abs(dg["diag"]["unexplained_labeled_h"] - dg["unexplained_h"]) <= 1e-6
      and abs(dg["diag"]["ungridded_idle_h"] - 23.0) <= 1e-6
      and identity_error(dg) <= 1e-6)

    # ---- as-of 陷阱（SPEC §2；replay_series / rows_asof）--------------------
    def R(pk, lot, ts, res, sgc, mcl="assyModule", status="WIP"):
        return (pk, lot, ts, res, sgc, mcl, status)

    cls_a = mq.QLIK_STATIONS[9][1]        # ord 10 = segDiePlacement
    cls_b = mq.QLIK_STATIONS[10][1]       # ord 11（下游）
    rows = [R(1, "J0001", H(1), mq.RESULT_PASS, cls_a),      # 09:00 前：料在 b 門口
            R(2, "J0001", H(20), mq.RESULT_PASS, cls_b)]     # 20:00 才前進（未來列）
    pts = [H(6), H(23)]
    ser = replay_series(rows, pts, cls_list=[cls_b], route=mq.QLIK_STATIONS,
                        age_cap_d=14, lookback_d=21, h_lookback_d=60)
    q_at_6 = ser[cls_b][0][2]
    q_at_23 = ser[cls_b][1][2]
    t("as-of: a future row does NOT move the position at t_i (queue 1 → 0)",
      q_at_6 == 1 and q_at_23 == 0)
    t("as-of: rows_asof is a strict `ts < t` cut",
      len(rows_asof(rows, H(1))) == 0 and len(rows_asof(rows, H(1.5))) == 1)
    t("as-of: rows with no timestamp are dropped (never invented at 00:00)",
      len(rows_asof([R(3, "J0002", None, mq.RESULT_PASS, cls_a)], H(23))) == 0)
    t("grid_points: 48 points per day at 30 min, half-open",
      len(grid_points(d0, d1, 30)) == 48 and grid_points(d0, d1, 30)[0] == d0
      and grid_points(d0, d1, 30)[-1] == d1 - timedelta(minutes=30))

    # ---- 真 S12 段形狀（wem.wo_segments）相容 pin --------------------------
    raw = w8.normalize_wo({"work_order_no": 777, "asset_id": "E1", "work_type": "REACTIVE",
                           "opened_at": "2026-07-31T02:00:00Z",     # UTC → 台北 10:00
                           "closed_at": "2026-07-31T06:00:00Z",     # → 14:00
                           "status_history": []})
    segs = wem.wo_segments([raw], d0, d1)
    led = build_ledger("segX", prod24[:0] + [{"eid": "E1", "records": []}], {"E1": segs},
                       FULL, None, None, win=(d0, d1), th=th)
    t("shape pin: real w8.normalize_wo → wem.wo_segments feeds the ledger unchanged",
      segs and abs(led["wo_union_h"] - 4.0) < 1e-9 and led["wo_h"]["維修"] == 4.0)

    # ---- daily_board / hero ------------------------------------------------
    board = demo_board()
    by = {r["cls"]: r for r in board["rows"]}
    t("demo: every scenario produced a row, order preserved",
      len(board["rows"]) == len(DEMO_SCENARIOS)
      and [r["cls"] for r in board["rows"]] == [c for c, _k in DEMO_SCENARIOS])
    t("demo ①: work-order dominant station", by["segDiePlacement"]["top"][0][0] == "wo")
    t("demo ②: starved dominant", by["segFlexBonding"]["top"][0][0] == "starved")
    t("demo ③: blocked dominant", by["segWireBonding"]["top"][0][0] == "blocked")
    t("demo ④: mixed station has all four buckets",
      len(by["segCalibrationTest"]["top"]) == 4)
    t("demo ⑤: off-shift station is unexplained dominant (never starved)",
      by["segFrameBonding"]["top"][0][0] == "unexplained")
    t("demo ⑥: degraded WO source → all unexplained + flag",
      by["segSealDispense"]["diag"]["wo_source"] == "degraded"
      and by["segSealDispense"]["top"] == [("unexplained",
                                             by["segSealDispense"]["unexplained_h"])])
    t("demo ⑦/⑧: unmeasurable rows show None not 0",
      by["segPotting"]["unmeasurable"] and by["segQASetup"]["unmeasurable"]
      and by["segPotting"]["starved_h"] is None)
    t("demo ⑨: line-tail station never books blocked",
      by["segVacuumPackInspect"]["blocked_h"] == 0.0)
    t("demo ⑩: rate unavailable → est None but hours still there",
      by["segCleanTest"]["est"] is None and by["segCleanTest"]["lost_h"] > 0)
    # ⚠ 2026-08-22 拆除：#35 的兩個 closure demo 情境隨「全廠停線」桶的 UI 一起退場
    #   （留著會讓 demo 頁的 24h 堆疊條加不到 100%）。closure 的**計算層**斷言完好如初
    #   ——見本檔上方 G2/T2 優先序/C-PC12 那一整區（`build_ledger(closures=)` 直打）。
    t("A5: the closure bucket is fully retired from the board (rows 0.0, hero has no key)",
      all(r["closure_h"] == 0.0 for r in board["rows"] if not r["unmeasurable"])
      and "closure" not in board["hero"]["by_bucket"]
      and board["hero"]["closure_uncertain_h"] == 0.0
      and daily_board({})["hero"]["closure_uncertain_h"] == 0.0)
    t("demo: identity holds for every measurable row",
      all(identity_error(r) <= 1e-6 for r in board["rows"]))
    t("demo is deterministic (same input → same board)",
      json.dumps(demo_board(), sort_keys=True, default=str)
      == json.dumps(demo_board(), sort_keys=True, default=str))
    hero = board["hero"]
    t("hero: aggregates only measurable stations and counts the rest",
      hero["n_unmeasurable"] == 2 and hero["n_measurable"] == len(DEMO_SCENARIOS) - 2)
    t("hero: bucket sum + producing == total machine-hours",
      abs(hero["total_mh"] - (hero["producing_h"] + sum(hero["by_bucket"].values())))
      <= 1e-6)
    t("hero: worst names a station and its dominant bucket",
      hero["worst"] and hero["worst"]["cls"] and hero["worst"]["bucket"] in BUCKETS)
    t("hero: est marked partial when some stations lack a rate",
      hero["est_partial"] is True and hero["n_rate_missing"] >= 1)
    t("daily_board: order honours the given cls sequence, extras are not hidden",
      [r["cls"] for r in daily_board({"a": by["segDiePlacement"], "b": by["segWireBonding"]},
                                     order=["b"])["rows"]]
      == [by["segWireBonding"]["cls"], by["segDiePlacement"]["cls"]])
    t("daily_board: empty input does not crash",
      daily_board({})["hero"]["n_stations"] == 0
      and daily_board({})["pareto30"] is None)

    # ---- C8 chips 雙軌 gate -------------------------------------------------
    t("C8 chips gate: met False shows chips even below the hour threshold",
      chips_gate(by["segDiePlacement"], met=False) is True)
    t("C8 chips gate: real loss shows chips even when the DEMO target says met",
      chips_gate(by["segDiePlacement"], met=True) is True)
    t("C8 chips gate: unmeasurable rows never show chips",
      chips_gate(by["segPotting"], met=False) is False)

    # ---- 日帳文件 round-trip（C6）------------------------------------------
    doc = ledger_doc(board, day="2026-07-31", source="live", wo_source_status="ok",
                     computed_at="2026-08-01T00:05:00")
    back = parse_ledger_doc(doc)
    t("C6 doc: schema_ver / source / wo_source_status / computed_at all pinned",
      doc["schema_ver"] == LEDGER_SCHEMA_VER and doc["source"] == "live"
      and doc["wo_source_status"] == "ok" and doc["computed_at"] == "2026-08-01T00:05:00")
    t("C6 doc: JSON round-trip is lossless for the pinned fields",
      back is not None and json.loads(json.dumps(doc)) == doc
      and len(back["stations"]) == len(board["rows"])
      and back["stations"][0]["cls"] == board["rows"][0]["cls"])
    t("C6 doc: a wrong schema_ver is rejected (never silently mis-read)",
      parse_ledger_doc(dict(doc, schema_ver="board.daily_ledger.v0")) is None
      and parse_ledger_doc({"schema_ver": LEDGER_SCHEMA_VER}) is None
      and parse_ledger_doc("nope") is None)
    t("C6 doc: unknown additive keys are ignored, not fatal",
      parse_ledger_doc(dict(doc, future_field=1)) is not None)
    # ---- 速率尾欄（2026-08-24；/equipment 產出當量的磁碟退路）----------------
    _rate_rows = {s["cls"]: s for s in doc["stations"]}
    _est_rows = {r["cls"]: r for r in board["rows"] if isinstance(r.get("est"), dict)}
    _noest_rows = [r["cls"] for r in board["rows"] if not isinstance(r.get("est"), dict)]
    t("rate tail: est.rate_per_mh / rate_src land on the station doc verbatim",
      bool(_est_rows) and all(
          _rate_rows[c]["rate_per_mh"] == _est_rows[c]["est"]["rate_per_mh"]
          and _rate_rows[c]["rate_src"] == _est_rows[c]["est"]["rate_src"]
          for c in _est_rows))
    t("rate tail: a row without est persists rate_per_mh/rate_src as None (never guessed)",
      all(_rate_rows[c]["rate_per_mh"] is None and _rate_rows[c]["rate_src"] is None
          for c in _noest_rows))
    _rt = parse_ledger_doc(json.loads(json.dumps(doc)))
    t("rate tail: survives a JSON round-trip through parse (whitelist covers it)",
      _rt is not None and {s["cls"]: s["rate_per_mh"] for s in _rt["stations"]}
      == {s["cls"]: s["rate_per_mh"] for s in doc["stations"]}
      and any(s["rate_per_mh"] for s in _rt["stations"]))
    # 舊檔（欄位根本不存在）＝additive-safe：照樣 parse 得出來，速率恆 None（不猜）
    _old = json.loads(json.dumps(doc))
    for _s in _old["stations"]:
        _s.pop("rate_per_mh", None)
        _s.pop("rate_src", None)
    _old_p = parse_ledger_doc(_old)
    t("rate tail: an old doc that predates the field still parses, rate is None",
      _old_p is not None and len(_old_p["stations"]) == len(doc["stations"])
      and all(s["rate_per_mh"] is None and s["rate_src"] is None
              for s in _old_p["stations"]))
    t("rate tail: line still drops est entirely (T4 — hours stay the authority)",
      "est" not in doc["line"] and "rate_per_mh" not in doc["line"])
    # A5 過渡：**舊軸日檔**（#35 時期帶非零 closure_h）在新 Pareto 裡＝legacy 揭露欄、
    #   不是桶（塞進四桶＝雙重語意；丟掉不揭露＝數字憑空縮）。新板 closure_h 恆 0。
    _legacy_doc = json.loads(json.dumps(doc))
    _legacy_doc["stations"][0]["closure_h"] = 10.0        # 手捏一份舊軸日檔
    _p_leg = pareto30([_legacy_doc], days=30, end_day="2026-07-31")
    _p_now = pareto30([doc], days=30, end_day="2026-07-31")
    t("A5 transition: legacy closure_h from old-axis docs lands in legacy_closure_h, not a bucket",
      "closure" not in _p_leg["by_bucket"]
      and abs(_p_leg["legacy_closure_h"] - 10.0) < 1e-9
      and _p_now["legacy_closure_h"] == 0.0
      and _p_leg["by_bucket"] == _p_now["by_bucket"])
    t("A5: new boards persist closure_h == 0.0 (schema tail kept for old readers)",
      all(st.get("closure_h") == 0.0 or st.get("closure_h") is None
          for st in doc["stations"]))
    good, bad = parse_ledger_docs([doc, {"schema_ver": "x"}, None])
    t("C6 doc: bad docs are counted, not thrown", len(good) == 1 and bad == 2)
    t("C6: only live is persisted (demo/snapshot never land)",
      should_persist("live") and not should_persist("demo")
      and not should_persist("snapshot") and not should_persist(None))
    t("C6: degraded days may be recomputed and overwritten; ok days may not",
      should_overwrite(None) is True
      and should_overwrite(dict(doc, wo_source_status="degraded")) is True
      and should_overwrite(doc) is False
      and should_overwrite({"garbage": 1}) is True)
    t("C6: retention rotates by day string",
      expired_days(["2026-01-01", "2026-07-30", "2026-07-31"], "2026-08-01", keep_days=90)
      == ["2026-01-01"])

    # ---- T7 §24：零人員資料 -------------------------------------------------
    dirty_m = [{"eid": "E1", "operator": "Wang Xiao Ming", "headcount": 3,
                "records": [{"state": PROD_STATE, "start": d0, "end": H(5),
                             "technician": "李小華"}]}]
    dirty_wo = {"E1": [dict(_demo_seg(5, "E1", H(5), H(9), "維修"),
                            assignees=["A12345"], operator_badge="A12345")]}
    dled = build_ledger("segX", dirty_m, dirty_wo, FULL, None, None, win=(d0, d1), th=th)
    dboard = daily_board({"segX": dled}, day="2026-07-31", source="live")
    blob = repr(dboard) + json.dumps(ledger_doc(dboard, day="2026-07-31", source="live",
                                                wo_source_status="ok"), ensure_ascii=False)
    t("T7 §24: no person-ish field or value survives into the ledger / board / doc",
      not any(k in blob for k in ("Wang Xiao Ming", "李小華", "A12345", "operator",
                                  "assignee", "technician", "headcount")))
    t("T7 §24: the dirty input is still correctly accounted for (scrub ≠ drop data)",
      dled["producing_h"] == 5.0 and dled["wo_union_h"] == 4.0)

    # ---- Pareto（C6）-------------------------------------------------------
    docs = []
    for k in range(5):
        day = (datetime(2026, 7, 31) - timedelta(days=k)).strftime("%Y-%m-%d")
        b = demo_board(day)
        docs.append(ledger_doc(b, day=day, source="live",
                               wo_source_status=("degraded" if k == 2 else "ok"),
                               computed_at=f"{day}T23:59:00"))
    p = pareto30(docs, days=30, end_day="2026-07-31")
    t("pareto: aggregates only the days inside the window and discloses coverage",
      p["n_days"] == 5 and p["n_missing"] == 25 and p["n_degraded"] == 1
      and p["thin"] is True and p["day_to"] == "2026-07-31")
    t("pareto: station rows sorted by lost hours, each with its own top cause",
      p["stations"] and p["stations"][0]["lost_h"] >= p["stations"][-1]["lost_h"]
      and all(s["top"][0][0] in BUCKETS for s in p["stations"] if s["top"]))
    t("pareto: line shares sum to 1 (or None when nothing was lost)",
      abs(sum(v for v in p["share"].values() if v) - 1.0) < 1e-3)
    t("pareto: unmeasurable stations never enter the aggregate",
      all(s["cls"] not in ("segPotting", "segQASetup") for s in p["stations"]))
    t("pareto: no docs → None (never a silently empty chart)",
      pareto30([]) is None and pareto30(None) is None)

    # ---- i18n（R4）---------------------------------------------------------
    en, zh = I18N_ATTRIB["en"], I18N_ATTRIB["zh"]
    t("R4 i18n: EN/ZH key sets are symmetric and non-empty",
      set(en) == set(zh) and all(en[k] and zh[k] for k in en))
    t("T3: the residual label is the SPEC wording, in one place only",
      zh["at_b_unexplained"] == "未解釋（可能人力/排班/其他）"
      and "manpower" in en["at_b_unexplained"].lower())
    t("T3: no bucket is ever labelled as manpower on its own",
      not any(k for k, v in BUCKET_I18N_KEY.items()
              if k != "unexplained" and "manpower" in en[v].lower()))
    t("R4 i18n: every note key and bucket key has a phrase",
      all(v in en for v in NOTE_I18N_KEY.values())
      and all(v in en for v in BUCKET_I18N_KEY.values()))
    # ★ C-PC10 命名鐵則（負斷言）：closure 桶**永遠不得**被講成「班表／沒排班／schedule」
    #   ——那是 G1 才答得了的問題；叫錯名字會讓人拿它去回答計畫類問題。
    t("C-PC10: the closure bucket is named 'plant-wide stoppage', never a schedule",
      en["at_b_closure"] == "Plant-wide stoppage (calendar-backed)"
      and zh["at_b_closure"] == "全廠停線（日曆佐證）"
      and not any(w in en["at_b_closure"].lower()
                  for w in ("schedul", "shift", "roster", "manpower"))
      and not any(w in zh["at_b_closure"] for w in ("班表", "排班", "人力")))
    t("C-PC10: the uncertainty note exists in both languages and names work orders",
      "at_closure_uncertain" in en and "at_closure_uncertain" in zh
      and "work order" in en["at_closure_uncertain"].lower()
      and "工單" in zh["at_closure_uncertain"])

    print(("FAILED: " + "; ".join(fails)) if fails else "ALL PASS")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(
        description="Tendline Board /daily 時間帳本歸因引擎（純函式；--selftest 全離線）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（不連 MES/網路）")
    ap.add_argument("--demo", action="store_true", help="印一份 demo 帳本（JSON）")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if a.demo:
        print(json.dumps(demo_board(), ensure_ascii=False, indent=2, default=str))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
