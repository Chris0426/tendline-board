#!/usr/bin/env python3
"""Tendline Board — 全線調度板（`/dispatch`）呈現模型。規格＝`SPEC-DISPATCH.md`（Chris 已拍板 D1–D7）。

**回答的問題**：整條線一屏、一列一站——「這站現在需不需要從別站暫調人手？」
**不是什麼**：不是稼動頁（`/utilization` 一字不動）、不是良率頁、不是考勤工具（§9 人名紅線）。

本檔＝**B2 消費面的純函式核心**（零 I/O、零 MES、零 HTTP；`--selftest` 全離線）：
  · `util_window` / `util_daily_median`  — §7：從 `app.load()` 既有 7d timeline data 導出
                                          4h 滾動 util 與 7d 逐日基準（**零新 SQL 路徑**）。
  · `down_eids`                          — 否決閘①：此刻掛「計停機」工單的 EID 集合
                                          （重用 `board_eqmeeting.wo_segments` 的 S12 分階段口徑）。
  · `operator_cell` / `operator_summary`  — §9.3：`board.operator.v1` → 人力欄四態
                                          （count／auto／nosig／off；**永不把 auto 站的 1 當人數**）。
  · `classify` / `build_board`           — §6 truth table（10 列、先中先贏、逐列 selftest 釘死）。
  · `DEMO_STATIONS` / `demo_util_ctx` / `demo_dispatch`
                                          — §11 確定性合成（12 情境全覆蓋；`board_data._demo_wip`
                                            共用同一張表 ⇒ cover 與 util 兩軸情境對得上）。

★ 鐵則（違反即 HIGH；全文見 SPEC §1）
  1. 凍結模組 `mes_timeline.py`/`mes_yield.py` 本檔**不 import、不執行**；設備軸模組
     （`board_eqmeeting`/`board_w8`）只 import 重用、零改動。
  2. **誠實**：量不到一律 `None`（消費面「—」），**絕不 0、絕不假值**；`need_help` 需 cover 與
     util/rate **兩軸都有值**才可主張（單軸不亮紅）。
  3. **燈不得只靠色相**：`lamp` 是 enum，模板必附文字標籤（色盲下讀字即可）。
  4. **原因短語必帶數字**（使用者要能自己驗證燈為何亮）＝`reason_args` 一律預先渲染成
     可讀字串（`cover`/`cur`/`base`/`ratio`…），i18n 只負責句型。
  5. **人名紅線（§24 延伸）**：本檔不接受、不儲存、不輸出任何姓名/工號欄位。人力欄吃的是
     `board.operator.v1` 的**整數計數**（`mes_operator` 已在 SQL 內 `COUNT(DISTINCT)` 完畢、
     識別值連 Python 都不進）；本檔**只讀白名單五個計數欄**，其餘一律不搬（SPEC §9.1/§9.3）。
  7. **人力不參與燈判定（D-OP3 pin）**：`classify` **不吃** `mp`——人員訊號長在 segment 列上，
     「閒置＝無 segment＝無人員訊號」恆真、零鑑別力；燈規則要吃 headcount＝另開裁決，
     本輪不偷接。`mp` 只在 `build_board` 事後掛到列上（顯示欄），**永不進 `classify` 的輸入**。
  6. 站序權威＝`mes_qlik.QLIK_STATIONS`（經 `board.wip.v1` 的 station 列帶進來）；**本檔不另存
     任何站清單**（`DEMO_STATIONS` 只是 demo 情境參數表、鍵是 cls、不是 route）。

CLI：`python board_dispatch.py --selftest`（純離線、不連任何東西）
"""
import sys
from datetime import datetime, timedelta

import board_eqmeeting as wem     # S12 分階段工單段（否決閘①）；唯讀重用、不改檔
import board_w8 as w8             # normalize_wo（selftest 合成工單用）；唯讀重用

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


SCHEMA_ID = "board.dispatch.v1"
CALIBER_DISPATCH = ("dispatch quadrant (MES-WIP coverage × station util vs its own baseline; "
                    "work-order veto per S12)")

# 燈態 enum（**不含 pass-through**：pass-through 是無燈灰列、`lamp=None`）
LAMPS = ("need_help", "ok", "lend", "down")

# 判準預設值（**env 全可調、名字 pin 死**；SPEC §6.1／D5：live 校準改 env 不改碼）
DEFAULT_THRESHOLDS = {
    "cover_thick_h": 4.0,     # BOARD_DISP_COVER_THICK_H：cover ≥ 此值＝WIP 厚
    "cover_thin_h": 1.0,      # BOARD_DISP_COVER_THIN_H ：cover ≤ 此值＝WIP 薄
    "util_low_ratio": 0.7,    # BOARD_DISP_UTIL_LOW_RATIO：util_4h < base_7d × ratio ＝偏低
    "rate_low_ratio": 0.7,    # BOARD_DISP_RATE_LOW_RATIO：util 不可測站的速率代理同款比值
    "base_floor": 0.05,       # BOARD_DISP_BASE_FLOOR：基準低於此值視同「基準不足」（防低基準噪音互除的假紅）
    "util_win_h": 4.0,        # 滾動 util 窗（小時）
    "base_days": 7,           # 基準窗（天）
    "base_min_days": 3,       # 有效日 < 此值 → base_7d=None（基準不足）
}

PROD_STATE = "生產"           # §23 canonical key（不翻、不改）


def thresholds(**over):
    """預設判準 + 覆寫（未知鍵一律忽略＝env typo 不會靜默改變語意）。"""
    th = dict(DEFAULT_THRESHOLDS)
    for k, v in (over or {}).items():
        if k in th and v is not None:
            th[k] = v
    return th


# =============================================================================
# 1. util 取數（§7）：**零新 SQL 路徑**——只從 `app.load()` 的 canonical records 導出
# =============================================================================
def _dt(v):
    return wem._coerce_dt(v)


def _win_of(data):
    """(win_start, win_end)；win_end 缺→退 mes_now（同 au_chips 慣例）。任一缺→(None, None)。"""
    d = data or {}
    ws = _dt(d.get("win_start"))
    we = _dt(d.get("win_end")) or _dt(d.get("mes_now"))
    if ws is None or we is None or we <= ws:
        return (None, None)
    return (ws, we)


def _station_flags(data):
    """(machines, zlo, nosig)：
       zlo  ＝機隊非空且**全員** zero_len_only（＝段長法結構性不可測；閘②）；
       nosig＝機隊非空但整窗一筆 record 都沒有（＝整窗無訊號）。
       兩者文案必須分開（既有紀律：`zero_len` ≠ `nosig`）。"""
    machines = (data or {}).get("machines") or []
    if not machines:
        return (machines, False, True)
    zlo = all(bool(m.get("zero_len_only")) for m in machines)
    nosig = not any((m.get("records") or []) for m in machines)
    return (machines, zlo, nosig)


def _prod_sec(machines, lo, hi):
    """[lo, hi] 內的生產秒（設備視角：records 已是 per-machine interval-union 後的結果
       ⇒ 跨機相加無機內雙計；與 `board_data.aggregate`／`au_chips` 同口徑）。"""
    tot = 0.0
    for m in machines:
        for r in (m.get("records") or []):
            if r.get("state") != PROD_STATE:
                continue
            a, b = _dt(r.get("start")), _dt(r.get("end"))
            if a is None or b is None:
                continue
            a2 = a if a > lo else lo
            b2 = b if b < hi else hi
            if b2 > a2:
                tot += (b2 - a2).total_seconds()
    return tot


def util_window(data, hours=4.0):
    """站級滾動 util（→ `(util|None, note)`；note ∈ {None,'zero_len','nosig','nowin'}）。

    · 錨＝**該站自己的 `data["win_end"]`**（＝load 當下的 mes_now），**不自己拿
      `datetime.now()`**——同一份資料自洽、TTL 內穩定（SPEC §7）。
    · 分母＝`n_machines × 實際涵蓋秒`（窗比 data 短時自動夾到 `win_start`）＝設備視角。
    · zlo（零工期八站）→ `(None,'zero_len')`；整窗無訊號 → `(None,'nosig')`。
      **兩者一律不回 0.0**（0% 會被讀成「從不運轉」＝誤導）。
    """
    ws, we = _win_of(data)
    if ws is None:
        return (None, "nowin")
    machines, zlo, nosig = _station_flags(data)
    if not machines:
        return (None, "nosig")
    if zlo:
        return (None, "zero_len")
    if nosig:
        return (None, "nosig")
    lo = we - timedelta(hours=float(hours))
    if lo < ws:
        lo = ws
    span = (we - lo).total_seconds()
    if span <= 0:
        return (None, "nowin")
    denom = span * len(machines)
    if denom <= 0:
        return (None, "nowin")
    return (max(0.0, min(1.0, _prod_sec(machines, lo, we) / denom)), None)


def util_daily_median(data, min_days=3):
    """站級 7d 基準＝**逐日 util 的中位數**（→ `(base|None, note, n_days)`）。

    · 日界＝台北牆鐘 00:00（data 本身即台北 naive；不做任何時區轉換）。
    · 每日 util ＝該日生產秒 /（`n_machines` × 該日落窗秒）。**已裁決讀法（SPEC §7，
      2026-08-01 收斂）：有效日＝該日落窗秒 > 0**——**含整日零生產的停線日（以 0% 計入）**，
      不是「該日有 record 才算」。方向＝保守：含週末的窗基準被壓低 ⇒ 紅燈更難亮、
      「可支援」更易亮，且更常撞 `BASE_FLOOR` 落 LOW_UNKNOWN（＝不主張）。兩種讀法的差異
      要到排班源（Q-D8）接線後才消失；在那之前刻意選誠實/保守側。
      夜班/週末的系統性影響見 SPEC §6.1 的「已知誤亮」註記。
    · 有效日 < `min_days` → `(None,'thin_base',n)`＝基準不足（消費面落 LOW_UNKNOWN、不亮燈）。
    · zlo/nosig 語意與 `util_window` 同（不回 0.0）。
    """
    ws, we = _win_of(data)
    if ws is None:
        return (None, "nowin", 0)
    machines, zlo, nosig = _station_flags(data)
    if not machines:
        return (None, "nosig", 0)
    if zlo:
        return (None, "zero_len", 0)
    if nosig:
        return (None, "nosig", 0)
    n = len(machines)
    vals = []
    day = datetime(ws.year, ws.month, ws.day)
    while day < we:
        nxt = day + timedelta(days=1)
        lo = day if day > ws else ws
        hi = nxt if nxt < we else we
        span = (hi - lo).total_seconds()
        if span > 0:
            vals.append(max(0.0, min(1.0, _prod_sec(machines, lo, hi) / (span * n))))
        day = nxt
    if len(vals) < int(min_days):
        return (None, "thin_base", len(vals))
    vals.sort()
    k = len(vals) // 2
    med = vals[k] if len(vals) % 2 else (vals[k - 1] + vals[k]) / 2.0
    return (med, None, len(vals))


# =============================================================================
# 2. 否決閘①：此刻掛「計停機」工單的機台（S12 分階段口徑，重用 wem.wo_segments）
# =============================================================================
def down_eids(wos_by_eid, now, probe_min=5.0):
    """→ `set(eid)`：此刻仍在「算停機」階段的機台（＝低 util 有機器解釋、不該被判成缺人）。

    口徑＝`board_eqmeeting.wo_segments_scoped`（**A10 擴散 R2、2026-08-16 換上**；
    S12 分階段：REACTIVE 自開單起算；PM/調機/暫桶自 IN_PROGRESS 起算；
    `WAITING_MACHINE_TIME`/`TEST_RUN` hold ＝機台可生產不計）——與 `/equipment` 的
    「現在」欄同一套規則，**本檔不重寫任何判定**。
    `ongoing`＝該 countable 段延伸到 `now` ⇒ 仍停機中。
    ★ **母機 EID 走機器視角**（SPEC-DORMANT §5.3 兩投影表）：母單範圍縮到某幾個工位時，
      其餘工位還在生產 ⇒「這台機器」此刻不算停、不進本集合（scoped 版把裁後段的
      `ongoing` 重算掉＝結構性成立；母單須由 app 層先帶好 `scope_events`，沒帶＝整機
      ＝高估側降級，同 `/equipment`）。
    ★ **dormant 刻意傳 `[]`（關）**：本函式答「此刻」不答「算多少」——與 `_now_block`
      的 Now 欄同一條例外（SPEC-DORMANT §5.1 ②：直接壓在長停工位上的單，此刻狀態照顯）。
    ★ **取消／作廢單自動不計**（2026-08-11 拍板①）——過濾住在 `wem.wo_segments` 裡（唯一落點），
      本檔**不再寫第二份 `wo_is_cancelled`**（第二份 predicate＝漂移的種子）。selftest 有釘子守。
    `wos_by_eid=None`（工單源未接通）→ 回 `None`（**不是空集合**：空集合會被讀成「沒人掛單」＝
    假裝有閘；`None` 讓 build_board 關掉閘①並在 footer 誠實註記）。
    """
    if wos_by_eid is None:
        return None
    now = _dt(now)
    if now is None:
        return None
    lo = now - timedelta(minutes=float(probe_min))
    out = set()
    for eid, wos in (wos_by_eid or {}).items():
        try:
            segs = wem.wo_segments_scoped(eid, wos, lo, now, dormant_events=[])
        except Exception:                      # 壞資料不該讓整板掛掉（該台當作沒掛單＝保守）
            continue
        if any(s.get("countable") and s.get("ongoing") for s in segs):
            out.add(eid)
    return out


# =============================================================================
# 2b. 人力欄（§9.3 D-OP1–D-OP4）：`board.operator.v1` → 四態欄值
#     ★ 這一欄**只顯示、不判燈**（D-OP3；`classify` 看不到它）。
#     ★ 四態語意不可互相冒充（顯錯一態就是說謊）：
#         off   ＝源未接通/查詢失敗/分類層降級 → 整欄「—」＋ footer `dp_mp_off`
#         auto  ＝機器帳號站（登入帳號跟機不跟人）→「—」＋ `dp_mp_auto`（**絕不顯示那個 1**）
#         nosig ＝該站近 class_d 天完全無列（canonical 缺鍵）→「—」＋ `dp_mp_nosig`
#         count ＝真計數（**含真 0**：窗內無人操作紀錄；淡色 0，**不是**「—」）
# =============================================================================
OPR_STATES = ("off", "auto", "nosig", "count")
# 白名單：本檔只從 `board.operator.v1` 的站別節點搬這幾個**整數**欄；其餘（含未來 additive 欄）
# 一律不搬＝任何識別值就算哪天混進上游，也進不了板／模板／log。
_OPR_DIAG_KEYS = ("d7_persons", "d7_blocks", "d7_rows")


def _cnt(v):
    """→ int（且**排除 bool**：`True` 是 int 子類，混進來會渲染成「1 個人」）；否則 None。"""
    return v if (isinstance(v, int) and not isinstance(v, bool)) else None


def operator_ok(opr):
    """人力源可用＝有 canonical、`ok` 為真、無 `unavailable`、`stations` 是 dict、且**分類層沒降級**。

    最後一條是承重的：`diag.degraded`（分類層零列）代表**沒有東西可以判自動站** ⇒ 機器帳號站
    會以「1 人」之姿混進來＝假在場。此時整欄落 `off`（誠實「—」）比顯半套數字安全。
    """
    if not isinstance(opr, dict) or not opr.get("ok") or opr.get("unavailable"):
        return False
    if not isinstance(opr.get("stations"), dict):
        return False
    return not (opr.get("diag") or {}).get("degraded")


def operator_cell(cls, opr):
    """→ `{"state", "n_now", "n_4h", "d7_persons", "d7_blocks", "d7_rows"}`（恆為完整鍵集）。

    `state` ∈ `OPR_STATES`（語意見本節抬頭）。**計數欄只在 `count` 態有值**——`auto`/`nosig`/`off`
    一律 `None`（模板據此渲染「—」；把 auto 站的 1 端出去就是 D-OP2 明令禁止的假在場）。
    形狀不合（上游漂移、n_now 不是 int…）→ 保守落 `nosig`＝「—」，**絕不猜一個數字**。
    """
    cell = {"state": "off", "n_now": None, "n_4h": None,
            "d7_persons": None, "d7_blocks": None, "d7_rows": None}
    if not operator_ok(opr):
        return cell
    st = (opr.get("stations") or {}).get(cls)
    if not isinstance(st, dict):
        cell["state"] = "nosig"                   # 缺鍵＝近 class_d 天無列（**不是 0**）
        return cell
    diag = st.get("diag") or {}
    for k in _OPR_DIAG_KEYS:
        cell[k] = _cnt(diag.get(k))
    if st.get("auto"):
        cell["state"] = "auto"                    # 分類依據照樣帶出＝使用者可在 ?-hover 自驗
        return cell
    n_now, n_4h = _cnt(st.get("n_now")), _cnt(st.get("n_4h"))
    if n_now is None or n_4h is None:
        cell["state"] = "nosig"
        return cell
    cell.update({"state": "count", "n_now": n_now, "n_4h": n_4h})
    return cell


def operator_summary(opr):
    """板級人力摘要（footer 用）：`{ok, line_now, line_4h, n_auto, n_count, n_nosig, params,
       cap, unavailable}`。**line 是 SQL 內全線去重的上界**（D-OP4：與各站相加不相等屬預期、
       母體含 route 外與 auto 站的機器帳號）——footer 必須用 `dp_mp_line_note` 講明。"""
    ok = operator_ok(opr)
    o = opr if isinstance(opr, dict) else {}
    line = o.get("line") or {}
    p = o.get("params") or {}
    return {"ok": ok,
            "line_now": _cnt(line.get("n_now")) if ok else None,
            "line_4h": _cnt(line.get("n_4h")) if ok else None,
            "params": {k: p.get(k) for k in ("now_min", "ref_h", "class_d",
                                             "auto_max_persons", "auto_min_blocks")},
            "cap": o.get("cap"),
            "unavailable": o.get("unavailable") or (None if ok else "degraded")}


# =============================================================================
# 3. 象限判定（§6 truth table；10 列、先中先贏、逐列 selftest 釘死）
#    ⚠ **人力（`mp`）不是輸入**（D-OP3 pin）——見檔頭鐵則 7。
# =============================================================================
def _pct(v):
    return "—" if v is None else f"{100.0 * v:.0f}%"


def _rate_t(v):
    return "—" if v is None else f"~{v:.1f}/h"


def _cover_t(v):
    return "—" if v is None else f"{v:.1f}h"


def util_axis(st, th):
    """鑑別軸取值（→ dict）：優先 util 軸；util **結構性不可測**（zlo/非設備站/無 timeline/
       有效日 <3）時改用**速率代理**（`rate_4h < rate_7d × rate_low_ratio`，兩者皆 >0 才可判）。
       兩者皆不可判 → `low=None`＝LOW_UNKNOWN（truth table 第 3 列，不亮任何燈）。

       ★ `base_floor`（SPEC §6.1，2026-08-01 裁決）：`base_7d` 有值但**低於門檻**＝這站自己的
         尺不可信（防「3% < 5%×0.7」這種低基準噪音互除的假紅）⇒ **直接落 LOW_UNKNOWN、
         不再試速率代理**。理由：proxy 是給「util 本來就量不到」的站用的；低勤務站的 4h 速率
         是 0.25/h 級的**量化值**（`n_4h` 只有個位數），r4=0.25 vs r7=0.5 就判 low ⇒ 噪音只會
         比 util 軸更大＝把 BASE_FLOOR 想擋的假紅從側門放回來。回傳帶 `note="base_floor"`
         讓消費面給出**專屬**短語（帶 base 與門檻兩個數字＝使用者可自驗），不與「稼動不可測」
         混為一談。
    """
    util, base = st.get("util_4h"), st.get("base_7d")
    if base is not None and base < th["base_floor"]:
        return {"low": None, "src": None, "note": "base_floor",
                "cur": _pct(util) if util is not None else "—", "base": _pct(base),
                "ratio": f"{th['util_low_ratio']:g}"}
    if util is not None and base is not None:
        return {"low": bool(util < base * th["util_low_ratio"]), "src": "util", "note": None,
                "cur": _pct(util), "base": _pct(base),
                "ratio": f"{th['util_low_ratio']:g}"}
    r4, r7 = st.get("rate_uph_4h"), st.get("rate_uph_7d")
    if r4 is not None and r7 is not None and r4 > 0 and r7 > 0:
        return {"low": bool(r4 < r7 * th["rate_low_ratio"]), "src": "proxy", "note": None,
                "cur": _rate_t(r4), "base": _rate_t(r7),
                "ratio": f"{th['rate_low_ratio']:g}"}
    # r4 == 0 且 r7 > 0 ＝「近 4h 完全沒動、7d 有量」——看似最該亮紅，但 4h 零吞吐同時也是
    # 「這 4h 根本沒排班」的樣子（G1 無排班源、SPEC §6.1）。v1 不猜：落 LOW_UNKNOWN。
    return {"low": None, "src": None, "note": None,
            "cur": _pct(util) if util is not None else "—",
            "base": _pct(base) if base is not None else "—",
            "ratio": f"{th['util_low_ratio']:g}"}


def classify(st, th=None):
    """§6.2 truth table（先中先贏）→ `{lamp, row, reason_key, reason_args, util_low, util_src}`。

    | # | 條件 | 燈 |
    | 0 | passthrough                      | 無燈（灰列） |
    | 1 | down_n == fleet_n > 0            | down |
    | 2 | UTIL_LOW 且 down_n ≥ 1           | ok（否決閘①：機器病非人力） |
    | 3 | LOW_UNKNOWN                      | ok（鑑別軸缺料、不主張；base<floor 走專屬短語） |
    | 4 | cover 不可測                     | ok（單軸不亮紅） |
    | 5 | THICK 且 UTIL_LOW                | **need_help** |
    | 6 | THICK 且 非 UTIL_LOW             | ok（機飽和） |
    | 7 | THIN 且 UTIL_LOW                 | ok（斷料、看上游） |
    | 8 | THIN 且 非 UTIL_LOW              | **lend** |
    | 9 | 其餘                             | ok |
    """
    th = th or thresholds()
    if st.get("passthrough"):
        return {"lamp": None, "row": 0, "reason_key": "dp_passthrough", "reason_args": {},
                "util_low": None, "util_src": None}

    ax = util_axis(st, th)
    low, cover = ax["low"], st.get("cover_h")
    down_n, fleet_n = st.get("down_n"), st.get("fleet_n") or 0
    base_args = {"cover": _cover_t(cover), "cur": ax["cur"], "base": ax["base"],
                 "ratio": ax["ratio"], "up": st.get("upstream_label") or "—",
                 "down": down_n if down_n is not None else "—", "fleet": fleet_n,
                 "rd": st.get("rate_fallback_d") or 7,
                 "floor": _pct(th["base_floor"])}

    def out(row, lamp, key):
        return {"lamp": lamp, "row": row, "reason_key": key, "reason_args": base_args,
                "util_low": low, "util_src": ax["src"]}

    if down_n is not None and fleet_n > 0 and down_n >= fleet_n:
        return out(1, "down", "dp_r_down_all")
    if low is True and down_n is not None and down_n >= 1:
        return out(2, "ok", "dp_r_down_veto")
    if low is None:                          # 第 3 列有兩種成因，短語必須分開（都帶數字）
        return out(3, "ok", "dp_r_thinbase" if ax.get("note") == "base_floor"
                   else "dp_r_nodiag")
    if cover is None:
        return out(4, "ok", "dp_r_nocover")
    if cover >= th["cover_thick_h"]:
        return out(5, "need_help", "dp_r_need") if low else out(6, "ok", "dp_r_sat")
    if cover <= th["cover_thin_h"]:
        return out(7, "ok", "dp_r_starved") if low else out(8, "lend", "dp_r_lend")
    return out(9, "ok", "dp_r_ok")


# =============================================================================
# 4. build_board — `board.wip.v1` × util/工單脈絡 → 43 列調度板
# =============================================================================
_ROW_KEYS = ("ord", "cls", "mcl", "display", "queue_n", "stale_n", "fail_hold_n",
             "n_4h", "n_7d", "rate_uph_4h", "rate_uph_7d", "rate_uph", "rate_src",
             "cover_h", "passthrough")


def _upstream_map(stations):
    """{ord: 上游（route 前一個非 pass-through 站）的 display}（斷料短語點名用；純 route 推導）。"""
    out, prev = {}, None
    for s in sorted(stations, key=lambda r: r.get("ord") or 0):
        out[s.get("ord")] = prev
        if not s.get("passthrough"):
            prev = s.get("display") or s.get("cls")
    return out


def build_board(wip, util_ctx=None, th=None, wo_gate=True, mode="live",
                skipped_n=0, wo_status=None, opr=None):
    """`board.wip.v1`（§4.4 pin）＋ per-station util/工單脈絡 → 調度板 canonical。

    · `wip`＝B1 `mes_wip.compute_wip` 的回傳（或 demo 合成的同形 dict）。**白名單讀取**：
      只吃 `_ROW_KEYS`＋`line`／`diag`／`cap`／`params`，未知欄一律忽略（B1 加欄＝additive）。
    · `util_ctx`＝`{cls: {"util_4h","base_7d","util_note","down_n","fleet_n"}}`；缺站＝該站
      util 不可測（`util_note='non_ast'`＝route 上但無設備 timeline）。
    · `wo_gate=False`（工單源未接通）→ 全站 `down_n=None`：閘① 失效、footer 誠實註記
      「寧可多亮紅被人工否決，不可假裝有閘」。
    · `opr`＝`board.operator.v1`（§9.3；**additive、可為 None**＝人力源未接通 → 整欄 `off`「—」）。
      **在 `classify` 之後才掛上**（`r["mp"]`）＝結構上保證人力永不參與燈判定（D-OP3 pin）。
    · 回傳恆為完整 43 列（含 pass-through 灰列）；**不藏站**。
    """
    th = th or thresholds()
    util_ctx = util_ctx or {}
    stations = list((wip or {}).get("stations") or [])
    ups = _upstream_map(stations)
    rows = []
    for s in sorted(stations, key=lambda r: r.get("ord") or 0):
        cls = s.get("cls")
        r = {k: s.get(k) for k in _ROW_KEYS}
        r["passthrough"] = bool(s.get("passthrough"))
        u = dict(util_ctx.get(cls) or {})
        r["util_4h"] = u.get("util_4h")
        r["base_7d"] = u.get("base_7d")
        r["base_days_n"] = u.get("base_days_n")
        r["util_note"] = u.get("util_note") or (None if cls in util_ctx else "non_ast")
        r["fleet_n"] = u.get("fleet_n") or 0
        r["down_n"] = (u.get("down_n") if wo_gate else None)
        r["upstream_label"] = ups.get(s.get("ord"))
        r["rate_fallback_d"] = ((wip or {}).get("params") or {}).get("rate_fallback_d", 7)
        r.update(classify(r, th))          # ← 燈先判完（此時列上還沒有 `mp`＝D-OP3 結構保證）
        r["mp"] = operator_cell(cls, opr)  # 人力欄（§9.3 四態；純顯示、不回頭改燈）
        rows.append(r)

    n = {"need_help": 0, "ok": 0, "lend": 0, "down": 0, "pass": 0}
    for r in rows:
        n["pass" if r["lamp"] is None else r["lamp"]] += 1
    line = dict((wip or {}).get("line") or {})
    diag = dict((wip or {}).get("diag") or {})
    osum = operator_summary(opr)
    osum.update({"n_auto": sum(1 for r in rows if r["mp"]["state"] == "auto"),
                 "n_count": sum(1 for r in rows if r["mp"]["state"] == "count"),
                 "n_nosig": sum(1 for r in rows if r["mp"]["state"] == "nosig")})
    return {
        "schema": SCHEMA_ID, "caliber": CALIBER_DISPATCH, "mode": mode,
        "cap": (wip or {}).get("cap"), "params": dict((wip or {}).get("params") or {}),
        "thresholds": dict(th), "rows": rows,
        "n_need": n["need_help"], "n_ok": n["ok"], "n_lend": n["lend"],
        "n_down": n["down"], "n_pass": n["pass"], "n_rows": len(rows),
        "queue_total": sum((r["queue_n"] or 0) for r in rows),
        "stale_total": line.get("stale_total", sum((r["stale_n"] or 0) for r in rows)),
        "fail_hold_total": line.get("fail_hold_total",
                                    sum((r["fail_hold_n"] or 0) for r in rows)),
        "line": line, "diag": diag, "opr": osum,
        "wo_gate": bool(wo_gate), "wo_status": wo_status,
        "dead_filter": diag.get("dead_filter"),
        "skipped_n": int(skipped_n or 0),
        "worst": next((r for r in sorted(rows, key=lambda x: -(x["cover_h"] or 0.0))
                       if r["lamp"] == "need_help"), None),
    }


# =============================================================================
# 5. demo 合成（§11：確定性、12 情境全覆蓋；board_data._demo_wip 共用本表）
# =============================================================================
#   ★ 一張表兩邊用 ⇒ cover 軸（wip 側）與 util 軸（設備側）的情境**必然對得上**；
#     分兩處寫死是 demo 常見的自相矛盾來源（燈與數字說不同故事）。
#   ★ 全合成＝頁面掛「全合成假資料」大牌（誠實鐵則 5）。**零人名欄**（§9 紅線）。
DEMO_STATIONS = {
    # ① 需支援：WIP 厚（36/6.0h）、util 12% ≪ 自身基準 34%×0.7
    "segDiePlacement": {"queue_n": 36, "stale_n": 0, "fail_hold_n": 0, "n_4h": 24, "n_7d": 700,
                     "util_4h": 0.12, "base_7d": 0.34, "fleet_n": 3, "down_n": 0},
    # ② 機飽和（正常＋提示）＋ ⑫ 卡站 FAIL 2
    "segWireBonding": {"queue_n": 25, "stale_n": 0, "fail_hold_n": 2, "n_4h": 20, "n_7d": 690,
                    "util_4h": 0.33, "base_7d": 0.34, "fleet_n": 3, "down_n": 0},
    # ③ 可支援：WIP 薄且順跑
    "segFlexBonding": {"queue_n": 2, "stale_n": 0, "fail_hold_n": 0, "n_4h": 20, "n_7d": 660,
                      "util_4h": 0.30, "base_7d": 0.31, "fleet_n": 2, "down_n": 0},
    # ④ 斷料（正常＋提示、點名上游）
    "segFrameBonding": {"queue_n": 1, "stale_n": 0, "fail_hold_n": 0, "n_4h": 16, "n_7d": 640,
                      "util_4h": 0.09, "base_7d": 0.30, "fleet_n": 2, "down_n": 0},
    # ⑤ 否決閘①：util 低、但 2/5 台掛維修單 → 機器病非人力
    "segCalibrationTest": {"queue_n": 20, "stale_n": 0, "fail_hold_n": 0, "n_4h": 16,
                                "n_7d": 600, "util_4h": 0.11, "base_7d": 0.30,
                                "fleet_n": 5, "down_n": 2},
    # ⑥ 整站維修中 → Down 燈
    "segSealDispense": {"queue_n": 8, "stale_n": 0, "fail_hold_n": 0, "n_4h": 12, "n_7d": 580,
                         "util_4h": 0.02, "base_7d": 0.28, "fleet_n": 3, "down_n": 3},
    # ⑦ 閘②：零工期八站之一 → util「—」、改速率代理（4h 0.5/h ≪ 7d 1.19/h）
    "segPotting": {"queue_n": 3, "stale_n": 0, "fail_hold_n": 0, "n_4h": 2, "n_7d": 200,
                       "util_4h": None, "base_7d": None, "util_note": "zero_len",
                       "fleet_n": 3, "down_n": 0},
    # ⑧ 目檢站（非設備站）＋近 4h 零吞吐 → 代理不可判＝診斷不足
    "segPreCureVisualInspection": {"queue_n": 6, "stale_n": 0, "fail_hold_n": 0, "n_4h": 0,
                                   "n_7d": 500, "util_4h": None, "base_7d": None,
                                   "util_note": "non_ast", "fleet_n": 0, "down_n": 0},
    # ⑨ 近 7d 零吞吐 → cover「—」＝覆蓋不可測（即使 util 有值也不主張）
    "segDieArrayTaping": {"queue_n": 4, "stale_n": 0, "fail_hold_n": 0, "n_4h": 0, "n_7d": 0,
                          "util_4h": 0.20, "base_7d": 0.25, "fleet_n": 2, "down_n": 0},
    # ⑩ 超齡在製 3 顆（琥珀 chip、**不參與燈判定**）
    "segQASetup": {"queue_n": 8, "stale_n": 3, "fail_hold_n": 0, "n_4h": 16, "n_7d": 600,
                           "util_4h": 0.28, "base_7d": 0.30, "fleet_n": 2, "down_n": 0},
}
# ⑪ ord 30（segFinalAssembly）＝pass-through 灰列，由 board_data._demo_wip 依
#    `WIP_PASSTHROUGH_ORDS` 標旗（本表不重複寫死站序）。

DEMO_RATE_WIN_H = 4.0
DEMO_RATE_FALLBACK_D = 7


def demo_station_wip(ordn, cls, rate_win_h=DEMO_RATE_WIN_H,
                     rate_fallback_d=DEMO_RATE_FALLBACK_D, passthrough=False):
    """單站 demo 的 wip 側欄位（queue/stale/fail_hold/rates/cover）。
       未列於 `DEMO_STATIONS` 的站＝seeded rnd（穩定、每次相同；一律落 ok/lend＝不製造假紅）。
       cover ＝ queue_n / rate_uph（rate 無 → None ＝「—」）——與 SPEC §5.4 同式。"""
    import random
    if passthrough:                        # pass-through：不歸屬佇列、無吞吐（灰列）
        d = {"queue_n": 0, "stale_n": 0, "fail_hold_n": 0, "n_4h": 0, "n_7d": 0}
    elif cls in DEMO_STATIONS:
        d = {k: DEMO_STATIONS[cls][k] for k in ("queue_n", "stale_n", "fail_hold_n",
                                                "n_4h", "n_7d")}
    else:
        rnd = random.Random(int(ordn) * 811 + 7)
        n4 = rnd.randint(8, 24)
        d = {"queue_n": rnd.randint(1, 14), "stale_n": 0, "fail_hold_n": 0,
             "n_4h": n4, "n_7d": n4 * 40 + rnd.randint(0, 120)}
    r4 = (d["n_4h"] / float(rate_win_h)) if d["n_4h"] else None
    r7 = (d["n_7d"] / (float(rate_fallback_d) * 24.0)) if d["n_7d"] else None
    rate = r4 if r4 else (r7 if r7 else None)
    src = "4h" if r4 else ("7d" if r7 else "none")
    d.update({"rate_uph_4h": r4, "rate_uph_7d": r7, "rate_uph": rate, "rate_src": src,
              "cover_h": (d["queue_n"] / rate) if rate else None,
              "passthrough": bool(passthrough)})
    return d


def demo_util_ctx(wip):
    """demo 的 util／工單脈絡（與 `DEMO_STATIONS` 同表 ⇒ 與 cover 軸情境一致）。
       未列站＝seeded、`util/base ≥ 0.85` ⇒ 結構上永不落 UTIL_LOW（demo 不製造假紅）。"""
    import random
    ctx = {}
    for s in ((wip or {}).get("stations") or []):
        cls, ordn = s.get("cls"), s.get("ord") or 0
        if s.get("passthrough"):
            ctx[cls] = {"util_4h": None, "base_7d": None, "util_note": "passthrough",
                        "fleet_n": 0, "down_n": 0}
            continue
        if cls in DEMO_STATIONS:
            d = DEMO_STATIONS[cls]
            ctx[cls] = {"util_4h": d.get("util_4h"), "base_7d": d.get("base_7d"),
                        "util_note": d.get("util_note"), "base_days_n": 7,
                        "fleet_n": d.get("fleet_n") or 0, "down_n": d.get("down_n") or 0}
            continue
        rnd = random.Random(int(ordn) * 331 + 5)
        base = 0.22 + rnd.randint(0, 16) / 100.0
        ctx[cls] = {"util_4h": base * (0.85 + rnd.randint(0, 25) / 100.0), "base_7d": base,
                    "util_note": None, "base_days_n": 7,
                    "fleet_n": 1 + rnd.randint(0, 3), "down_n": 0}
    return ctx


# ---- demo 人力（§9.3；四態全覆蓋：count／真 0／auto／nosig）--------------------------
#   ★ **零人名欄**（§9.1 紅線）——demo 合成的也只有整數計數，連假名字都不造。
#   ★ 情境要能讓人在 demo 上「看見四態」，否則接線對不對只有 live 才知道。
DEMO_OPERATOR = {
    # 一般人工站（主值 + 4h 參考值；d7_* ＝分類依據，?-hover 可自驗「為何不是自動站」）
    "segDiePlacement":              {"n_now": 3, "n_4h": 5, "d7": (9, 40, 1180)},
    "segWireBonding":               {"n_now": 4, "n_4h": 6, "d7": (11, 41, 1320)},
    "segCalibrationTest":   {"n_now": 5, "n_4h": 9, "d7": (11, 42, 1610)},
    "segFrameBonding":             {"n_now": 2, "n_4h": 4, "d7": (7, 38, 900)},
    "segSealDispense":          {"n_now": 1, "n_4h": 3, "d7": (6, 36, 780)},
    "segFlexBonding":             {"n_now": 2, "n_4h": 3, "d7": (5, 34, 640)},
    "segQASetup":        {"n_now": 2, "n_4h": 4, "d7": (8, 39, 810)},
    "segPreCureVisualInspection": {"n_now": 1, "n_4h": 2, "d7": (4, 22, 300)},
    # ★ 真 0（窗內無人操作紀錄）＝淡色 0、**不是**「—」（近 7d 零吞吐的閒置站，語意對得上）
    "segDieArrayTaping":         {"n_now": 0, "n_4h": 0, "d7": (3, 11, 90)},
    # ★ 自動站（R4 實證形：人少 + 日夜都在跑）→ 人力欄「—」＋自動站 tooltip，**那個 1 不顯示**
    "segPotting":            {"n_now": 1, "n_4h": 1, "d7": (1, 41, 5200)},
    "segSerialisation": {"n_now": 1, "n_4h": 2, "d7": (2, 40, 4100)},
}
DEMO_OPR_NOW_MIN = 60
DEMO_OPR_REF_H = 4
DEMO_OPR_CLASS_D = 7
DEMO_OPR_MAX_PERSONS = 2
DEMO_OPR_MIN_BLOCKS = 12


def _demo_auto(d7_persons, d7_blocks, max_persons, min_blocks):
    """D-OP2 判定＝**重用 `mes_operator.classify_auto_station`**（規則只有一份）。

    lazy import＋等價 fallback：`mes_operator` 缺席時 demo 板照樣能跑（誠實降級精神），
    但兩者對同一輸入**必須同答**＝selftest 釘住 ⇒ 規則日後在 `mes_operator` 改字
    （例如加第三腿），demo 板不會靜默與 live 分類分岔。
    """
    try:
        import mes_operator as mo
        return bool(mo.classify_auto_station(d7_persons, d7_blocks,
                                             max_persons=max_persons, min_blocks=min_blocks))
    except ImportError:
        if d7_persons is None or d7_blocks is None:
            return False                    # None ＝不可知 ⇒ 不主張自動站（同 mes_operator）
        try:
            return int(d7_persons) <= int(max_persons) and int(d7_blocks) >= int(min_blocks)
        except (TypeError, ValueError):
            return False


def _demo_line(total, ratio):
    """demo 線級去重值（**恆滿足 D-OP4 不變式 `line ≤ Σ 各非 auto 站`**）。

    舊式 `max(1, int(total*ratio))` 在退化情境（傳入站全是 auto／pass-through ⇒ Σ=0）會吐
    `1 > 0`＝板子自己違反自己 footer 講的話。Σ=0 就回 0（沒有人就是沒有人）。
    """
    t = int(total or 0)
    if t <= 0:
        return 0
    return min(max(1, int(t * ratio)), t)


def demo_operator(wip, now_min=DEMO_OPR_NOW_MIN, ref_h=DEMO_OPR_REF_H,
                  class_d=DEMO_OPR_CLASS_D, max_persons=DEMO_OPR_MAX_PERSONS,
                  min_blocks=DEMO_OPR_MIN_BLOCKS):
    """確定性合成 `board.operator.v1`（demo 專用；形狀逐鍵照 SPEC §9.3 pin）。

    · `auto` **不硬填**——走與 live 完全相同的判準（`d7_persons ≤ max` 且 `d7_blocks ≥ min`）
      算出來，所以 demo 上看到的自動站就是規則本身的示範（改門檻 env，demo 也會跟著變）。
    · **pass-through 站一律缺鍵**＝`nosig`（那種站本來就沒有 segment 列，語意天然對）。
    · `line` 刻意 **< Σ 各站**（D-OP4：同一人跨多站作業 ⇒ SQL 去重後必然更小）。
    """
    import random
    stations = {}
    for s in ((wip or {}).get("stations") or []):
        cls, ordn = s.get("cls"), s.get("ord") or 0
        if s.get("passthrough"):
            continue                                    # 缺鍵＝nosig（不是 0）
        if cls in DEMO_OPERATOR:
            d = DEMO_OPERATOR[cls]
            n_now, n_4h, (p, b, n) = d["n_now"], d["n_4h"], d["d7"]
        else:
            # 未列站＝seeded 小數字（0–2）。刻意壓小：R4 實測全線 7d 才 45 人、24h 32 人
            # ⇒ demo 若每站都塞 1–4，線級合計會飆到 60+＝一眼看得出是假的，反而讓人不信版面。
            rnd = random.Random(int(ordn) * 977 + 13)
            n_now = rnd.randint(0, 2)
            n_4h = n_now + rnd.randint(0, 2)
            p, b, n = n_4h + rnd.randint(1, 5), 30 + rnd.randint(0, 12), 400 + ordn * 7
        stations[cls] = {"n_now": int(n_now), "n_4h": int(n_4h),
                         "auto": _demo_auto(p, b, max_persons, min_blocks),
                         "diag": {"d7_persons": int(p), "d7_blocks": int(b),
                                  "d7_rows": int(n)}}
    live = [v for v in stations.values() if not v["auto"]]
    sum_now = sum(v["n_now"] for v in live)
    sum_ref = sum(v["n_4h"] for v in live)
    return {
        "schema": "board.operator.v1", "ok": True,
        "cap": (wip or {}).get("cap"),
        "params": {"now_min": int(now_min), "ref_h": int(ref_h), "class_d": int(class_d),
                   "auto_max_persons": int(max_persons), "auto_min_blocks": int(min_blocks)},
        "stations": stations,
        # 跨站去重後的上界（demo 取 ~45%／~32%＝與「多數人跨 ≥2 站、窗越長重疊越多」同向；
        # 4h 窗的去重率必然比 60min 窗更兇，所以 ref 的係數更小不是筆誤）
        "line": {"n_now": _demo_line(sum_now, 0.45), "n_4h": _demo_line(sum_ref, 0.32)},
        "diag": {"degraded": False, "demo": True, "person_col": "OperatorId",
                 "auto_rule": f"d7_persons <= {int(max_persons)} AND "
                              f"d7_blocks >= {int(min_blocks)}"},
        "elapsed_s": 0.0,
    }


def demo_dispatch(wip, th=None, opr=None):
    """demo 板＝`build_board(demo wip, demo util_ctx, demo operator)`（全合成；工單閘視為接通）。
       `opr` 可覆寫＝smoke 用來注入「人力源未接通」情境（傳 `False` ⇒ 明確不合成）。"""
    o = demo_operator(wip) if opr is None else (opr or None)
    return build_board(wip, demo_util_ctx(wip), th or thresholds(), wo_gate=True,
                       mode="demo", wo_status="demo", opr=o)


# =============================================================================
# 6. selftest（純離線；SPEC §12 離線閘）
# =============================================================================
def _mk_data(win_start, win_end, machines):
    """合成 canonical timeline data（`machines[i]["records"]` 形；供 util_* 測試）。"""
    return {"source": "demo", "mes_now": win_end, "win_start": win_start,
            "win_end": win_end, "machines": machines}


def _mach(eid, runs, zlo=False):
    return {"eid": eid, "zero_len_only": zlo,
            "records": [{"eid": eid, "state": PROD_STATE, "start": a, "end": b,
                         "sec": (b - a).total_seconds()} for a, b in runs]}


def _wo(no, eid, opened, closed=None, wt="REACTIVE", hist=None, status=None):
    """raw cmms WO → `w8.normalize_wo`（走真正的正規化路徑＝形狀不會偷偷漂掉）。"""
    return w8.normalize_wo({"work_order_no": no, "asset_id": eid, "work_type": wt,
                            "opened_at": opened, "closed_at": closed, "status": status,
                            "status_history": hist or []})


def _selftest():
    fails = []

    def t(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    th = thresholds()

    # ---- util_window ---------------------------------------------------------
    we = datetime(2026, 8, 1, 12, 0, 0)
    ws = we - timedelta(days=7)
    d1 = _mk_data(ws, we, [_mach("E1", [(we - timedelta(hours=2), we)]),
                           _mach("E2", [(we - timedelta(hours=1), we)])])
    u, note = util_window(d1, 4)
    t("util_window: 2 machines, 3h prod in 4h×2 → 0.375", note is None and abs(u - 0.375) < 1e-9)
    u, note = util_window(_mk_data(ws, we, [_mach("E1", [], zlo=True)]), 4)
    t("util_window: zero-length-only station → (None,'zero_len') never 0.0",
      u is None and note == "zero_len")
    u, note = util_window(_mk_data(ws, we, [_mach("E1", [])]), 4)
    t("util_window: no records in window → (None,'nosig')", u is None and note == "nosig")
    t("util_window: no machines → (None,'nosig')", util_window(_mk_data(ws, we, []), 4)
      == (None, "nosig"))
    t("util_window: no window → (None,'nowin')", util_window({"machines": [_mach("E", [])]}, 4)
      == (None, "nowin"))
    # 窗比 4h 短 → 分母自動夾到 win_start（不虛胖分母、不偽造 0%）
    short = _mk_data(we - timedelta(hours=2), we,
                     [_mach("E1", [(we - timedelta(hours=1), we)])])
    u, _ = util_window(short, 4)
    t("util_window: window shorter than probe → denominator clamps to win_start (0.5)",
      abs(u - 0.5) < 1e-9)

    # ---- util_daily_median ---------------------------------------------------
    # 7 天、每天固定產 6h（=25%），跨日界切桶
    runs = [(ws + timedelta(days=i, hours=1), ws + timedelta(days=i, hours=7)) for i in range(7)]
    dm = _mk_data(ws, we, [_mach("E1", runs)])
    b, note, nd = util_daily_median(dm)
    t("util_daily_median: 8 buckets across day boundary, median ≈ 0.25",
      note is None and nd >= 7 and 0.20 < b < 0.30)
    lo2 = we - timedelta(hours=20)                     # 只跨 2 個日桶（07-31 尾 + 08-01 頭）
    b, note, nd = util_daily_median(
        _mk_data(lo2, we, [_mach("E1", [(we - timedelta(hours=3), we)])]))
    t("util_daily_median: <3 effective days → (None,'thin_base')",
      b is None and note == "thin_base" and nd == 2)
    b, note, _ = util_daily_median(_mk_data(ws, we, [_mach("E1", [], zlo=True)]))
    t("util_daily_median: zlo → (None,'zero_len')", b is None and note == "zero_len")
    # 停線日（該日落窗秒 >0 但零生產）＝**有效日、以 0% 計入**（SPEC §7 已裁決讀法、保守側）
    #   7 天窗、只有 3 天各產 12h（=50%）、其餘 4 天全停 ⇒ 逐日 [0,0,0,0,.5,.5,.5](+尾桶 0)
    #   → 中位數必然是 0%（不是 50%）；用「該日有 record 才算」的讀法會得到 50%。
    stop_runs = [(ws + timedelta(days=i, hours=1), ws + timedelta(days=i, hours=13))
                 for i in (0, 1, 2)]
    b, note, nd = util_daily_median(_mk_data(ws, we, [_mach("E1", stop_runs)]))
    t("util_daily_median: idle days count as 0% effective days (conservative, SPEC §7)",
      note is None and nd >= 7 and b is not None and b < 0.10)

    # ---- down_eids -----------------------------------------------------------
    now = datetime(2026, 8, 1, 12, 0, 0)
    wos = {"EID-1": [_wo(1, "EID-1", now - timedelta(hours=5))],                 # open REACTIVE → down
           "EID-2": [_wo(2, "EID-2", now - timedelta(hours=9),
                         closed=now - timedelta(hours=1))],                      # 已結案 → 不 down
           "EID-3": [_wo(3, "EID-3", now - timedelta(hours=5), wt="PM")]}        # PM 只 OPEN → 不計停機
    de = down_eids(wos, now)
    t("down_eids: open REACTIVE counts as down now", de == {"EID-1"})
    t("down_eids: work-order source not connected → None (never an empty set)",
      down_eids(None, now) is None)
    # 取消／作廢單不得把機台鎖在否決閘①（2026-08-11 拍板①）。過濾住在 `wem.wo_segments`＝
    # 本檔零判定碼；這兩條釘的就是「那條結構性繼承還在」。劇本＝prod 真案 WO 35185 型：
    # REACTIVE、開單很早、`closed_at` NULL（CANCELLED 恆 NULL）、history 只有一筆 CANCELLED。
    cx_hist = [{"to_status": "CANCELLED", "changed_at": now - timedelta(hours=1)}]
    t("down_eids: cancelled work order never gates a machine (status leg)",
      down_eids({"EID-9": [_wo(35185, "EID-9", now - timedelta(days=47), status="CANCELLED")]},
                now) == set())
    # ⚠ 誠實標註：history 腿這條在**本函式**是非承重的（有 history 的取消單，活躍窗止於取消
    #   時刻 ⇒ 本來就不會涵蓋 now、也不會是 ongoing）。留著是形狀文件；真正會咬人的是上面那條
    #   status 腿（空 history ⇒ 窗永遠 ongoing ⇒ 機台被永久鎖在否決閘①）。
    t("down_eids: cancelled work order never gates a machine (history leg)",
      down_eids({"EID-9": [_wo(35209, "EID-9", now - timedelta(days=47), hist=cx_hist)]},
                now) == set())
    t("down_eids: an OPEN work order on the same machine still gates it (not a false green)",
      down_eids({"EID-9": [_wo(35267, "EID-9", now - timedelta(days=47))]},
                now) == {"EID-9"})
    # ---- down_eids × A10 機器視角（R2、2026-08-16）---------------------------
    #   母單 ongoing、範圍已縮到 Bay#1 ⇒「這台機器」此刻不算停 ⇒ 不進否決閘①。
    #   （scoped 版把裁後段 ongoing 重算掉＝結構性成立；wem N3-2 另有段級釘。）
    import board_scope as _wsc
    import board_w8 as _w8
    # WP-10d（R-P11）：母機／橋上長停工位由名冊常數推導（私有樹＝EID-24742／EID-26841；公開樹是合成名冊）
    import board_dormant as _wdm
    _pa = list(_wsc.PARENT_EXPANSION)[0]
    _dm = _wdm.DORMANT_BRIDGE[0][0]
    _pwo = _wo(70001, _pa, now - timedelta(hours=5))
    _pwo["scope_events"] = _w8.normalize_scope_events(
        [{"ts": now - timedelta(hours=3), "scope": [_wsc.PARENT_EXPANSION[_pa][0]]}])[0]
    t("down_eids: parent WO narrowed to one position → machine NOT gated (machine view, R2)",
      down_eids({_pa: [_pwo]}, now) == set())
    _pwo2 = _wo(70002, _pa, now - timedelta(hours=5))
    _pwo2["scope_events"] = []                        # legacy 整機 ⇒ 照舊亮
    t("down_eids: parent WO with legacy [] scope still gates (whole machine)",
      down_eids({_pa: [_pwo2]}, now) == {_pa})
    # dormant 例外（SPEC-DORMANT §5.1 ②）：此刻判定不吃長停 ⇒ 直接壓在長停工位上的
    # ongoing 單照亮（與 /equipment Now 欄同一條刻意保留的不一致面）。
    t("down_eids: NOW-question deliberately ignores dormant (SPEC-DORMANT §5.1 ②)",
      down_eids({_dm: [_wo(70003, _dm, now - timedelta(hours=2))]},
                now) == {_dm})

    # ---- truth table：10 列逐列釘（先中先贏） --------------------------------
    def S(**kw):
        base = {"passthrough": False, "cover_h": 2.0, "util_4h": 0.30, "base_7d": 0.30,
                "rate_uph_4h": 4.0, "rate_uph_7d": 4.0, "down_n": 0, "fleet_n": 3}
        base.update(kw)
        return base

    t("row0 passthrough → no lamp,先於一切",
      classify(S(passthrough=True, cover_h=99, util_4h=0.0, down_n=3), th)["lamp"] is None
      and classify(S(passthrough=True), th)["row"] == 0)
    t("row1 whole fleet on work orders → down",
      classify(S(down_n=3, fleet_n=3), th)["lamp"] == "down")
    r = classify(S(cover_h=6.0, util_4h=0.10, down_n=1, fleet_n=5), th)
    t("row2 UTIL_LOW + any machine on a WO → veto (ok, not need_help)",
      r["row"] == 2 and r["lamp"] == "ok" and r["reason_key"] == "dp_r_down_veto")
    r = classify(S(cover_h=6.0, util_4h=None, base_7d=None,
                   rate_uph_4h=None, rate_uph_7d=None), th)
    t("row3 LOW_UNKNOWN → ok + nodiag（thick 也不亮紅）",
      r["row"] == 3 and r["lamp"] == "ok" and r["reason_key"] == "dp_r_nodiag")
    r = classify(S(cover_h=None, util_4h=0.05, base_7d=0.30), th)
    t("row4 cover unmeasurable → ok（單軸絕不亮紅）",
      r["row"] == 4 and r["lamp"] == "ok" and r["reason_key"] == "dp_r_nocover")
    r = classify(S(cover_h=6.0, util_4h=0.10, base_7d=0.34), th)
    t("row5 THICK + UTIL_LOW → need_help", r["row"] == 5 and r["lamp"] == "need_help")
    r = classify(S(cover_h=6.0, util_4h=0.33, base_7d=0.34), th)
    t("row6 THICK + not low → ok/saturated", r["row"] == 6 and r["reason_key"] == "dp_r_sat")
    r = classify(S(cover_h=0.4, util_4h=0.09, base_7d=0.30, upstream_label="Up Stn"), th)
    t("row7 THIN + UTIL_LOW → ok/starved + upstream named",
      r["row"] == 7 and r["reason_key"] == "dp_r_starved"
      and r["reason_args"]["up"] == "Up Stn")
    r = classify(S(cover_h=0.4, util_4h=0.30, base_7d=0.31), th)
    t("row8 THIN + not low → lend", r["row"] == 8 and r["lamp"] == "lend")
    r = classify(S(cover_h=2.0, util_4h=0.30, base_7d=0.31), th)
    t("row9 middle cover → ok", r["row"] == 9 and r["reason_key"] == "dp_r_ok")

    # 邊界：THICK/THIN 恰好在門檻上（≥ / ≤，逐字照 SPEC §6.1）
    t("boundary cover == thick → THICK 側",
      classify(S(cover_h=4.0, util_4h=0.10, base_7d=0.34), th)["row"] == 5)
    t("boundary cover == thin → THIN 側",
      classify(S(cover_h=1.0, util_4h=0.30, base_7d=0.31), th)["row"] == 8)

    # BASE_FLOOR：基準恰 5%±ε
    t("BASE_FLOOR: base 4.9% → 視同基準不足 → LOW_UNKNOWN（防低基準假紅）",
      classify(S(cover_h=6.0, util_4h=0.01, base_7d=0.049, rate_uph_4h=None,
                 rate_uph_7d=None), th)["row"] == 3)
    t("BASE_FLOOR: base 5.0% → 仍是有效基準（>= floor）",
      classify(S(cover_h=6.0, util_4h=0.01, base_7d=0.05), th)["row"] == 5)
    # ★ 爭議路徑（2026-08-01 裁決）：base<floor 時**不落速率代理**——低勤務站的 4h 速率是
    #   0.25/h 級量化值（r4=0.25 vs r7=0.5 就判 low），從側門把 BASE_FLOOR 想擋的假紅放回來。
    r = classify(S(cover_h=6.0, util_4h=0.01, base_7d=0.03,
                   rate_uph_4h=0.25, rate_uph_7d=0.5), th)
    t("BASE_FLOOR: thin baseline never falls through to the rate proxy (no side-door red)",
      r["row"] == 3 and r["lamp"] == "ok" and r["util_src"] is None
      and r["reason_key"] == "dp_r_thinbase")
    t("BASE_FLOOR: its reason phrase carries the baseline AND the floor (self-verifiable)",
      r["reason_args"]["base"] == "3%" and r["reason_args"]["floor"] == "5%"
      and r["reason_args"]["cur"] == "1%")
    # 對照：base 本來就是 None（zlo/非設備站）＝**結構性**不可測 → proxy 照常適用
    r2 = classify(S(cover_h=6.0, util_4h=None, base_7d=None,
                    rate_uph_4h=0.25, rate_uph_7d=0.5), th)
    t("BASE_FLOOR: base=None (structurally unmeasurable) still uses the proxy",
      r2["row"] == 5 and r2["util_src"] == "proxy")

    # 閘②：util 不可測 → 速率代理；proxy 判 low 時第 5 列照樣可亮 need_help
    r = classify(S(cover_h=6.0, util_4h=None, base_7d=None,
                   rate_uph_4h=0.5, rate_uph_7d=1.19), th)
    t("gate②: util unmeasurable → rate proxy can still light need_help",
      r["row"] == 5 and r["lamp"] == "need_help" and r["util_src"] == "proxy")
    r = classify(S(cover_h=6.0, util_4h=None, base_7d=None,
                   rate_uph_4h=0.0, rate_uph_7d=1.19), th)
    t("gate②: rate proxy needs both rates > 0 → otherwise LOW_UNKNOWN", r["row"] == 3)

    # down_n=None（工單源未接通）→ 第 1/2 列全跳過（寧可多亮紅、不假裝有閘）
    r = classify(S(cover_h=6.0, util_4h=0.10, base_7d=0.34, down_n=None, fleet_n=3), th)
    t("wo source off: rows 1&2 skipped → still need_help (never a fake veto)",
      r["row"] == 5 and r["lamp"] == "need_help")

    # 原因短語必帶數字（使用者要能自驗）
    r = classify(S(cover_h=6.0, util_4h=0.10, base_7d=0.34), th)
    a = r["reason_args"]
    t("reason args carry three numbers (cover / current / baseline)",
      a["cover"] == "6.0h" and a["cur"] == "10%" and a["base"] == "34%" and a["ratio"] == "0.7")

    # ---- demo 板：12 情境 ----------------------------------------------------
    route = [(o, c, "assyModule") for o, c in enumerate(
        ["segSerialisation", "segFramePlasma", "segFrameBonding",
         "segDiePlacement", "segFlexBonding", "segWireBonding", "segSealDispense",
         "segPotting", "segPreCureVisualInspection", "segCalibrationTest",
         "segDieArrayTaping", "segQASetup", "segFinalAssembly"], start=1)]
    st = []
    for o, c, m in route:
        pt = (c == "segFinalAssembly")
        row = {"ord": o, "cls": c, "mcl": m, "display": c}
        row.update(demo_station_wip(o, c, passthrough=pt))
        st.append(row)
    wip = {"schema": "board.wip.v1", "ok": True, "cap": "2026-08-01T12:00:00",
           "params": {"rate_fallback_d": 7}, "stations": st,
           "line": {"stale_total": 3, "fail_hold_total": 2}, "diag": {}}
    b = demo_dispatch(wip)
    by = {r["cls"]: r for r in b["rows"]}
    t("demo ①: segDiePlacement → need_help", by["segDiePlacement"]["lamp"] == "need_help")
    t("demo ②: segWireBonding → ok/saturated + fail_hold chip",
      by["segWireBonding"]["reason_key"] == "dp_r_sat" and by["segWireBonding"]["fail_hold_n"] == 2)
    t("demo ③: segFlexBonding → lend", by["segFlexBonding"]["lamp"] == "lend")
    t("demo ④: segFrameBonding → ok/starved",
      by["segFrameBonding"]["reason_key"] == "dp_r_starved")
    t("demo ⑤: segCalibrationTest → ok/down veto",
      by["segCalibrationTest"]["reason_key"] == "dp_r_down_veto")
    t("demo ⑥: segSealDispense → down", by["segSealDispense"]["lamp"] == "down")
    t("demo ⑦: segPotting → need_help via rate proxy",
      by["segPotting"]["lamp"] == "need_help"
      and by["segPotting"]["util_src"] == "proxy"
      and by["segPotting"]["util_note"] == "zero_len")
    t("demo ⑧: segPreCureVisualInspection → ok/nodiag (non-EQP)",
      by["segPreCureVisualInspection"]["reason_key"] == "dp_r_nodiag")
    t("demo ⑨: segDieArrayTaping → cover None + nocover",
      by["segDieArrayTaping"]["cover_h"] is None
      and by["segDieArrayTaping"]["reason_key"] == "dp_r_nocover")
    t("demo ⑩: segQASetup → stale chip, lamp unaffected",
      by["segQASetup"]["stale_n"] == 3 and by["segQASetup"]["lamp"] == "ok")
    t("demo ⑪: pass-through row kept, no lamp",
      by["segFinalAssembly"]["lamp"] is None
      and by["segFinalAssembly"]["passthrough"] is True)
    t("demo board counts add up", b["n_need"] + b["n_ok"] + b["n_lend"] + b["n_down"]
      + b["n_pass"] == b["n_rows"] == len(route))
    t("demo board: worst = highest-cover need_help station",
      (b["worst"] or {}).get("cls") == "segDiePlacement")
    t("pass-through never enters the need/lend pools",
      all(r["lamp"] is None for r in b["rows"] if r["passthrough"]))

    # 上游點名＝route 前一個非 pass-through 站（跳站正確）
    ups = _upstream_map(st)
    t("upstream map skips nothing but itself (direct predecessor)",
      ups[3] == "segFramePlasma")

    # ---- 人力欄（§9.3 D-OP1–D-OP4）：四態 + 不參與燈判定 ---------------------
    _opr = {"schema": "board.operator.v1", "ok": True, "cap": "2026-08-01T12:00:00",
            "params": {"now_min": 60, "ref_h": 4, "class_d": 7,
                       "auto_max_persons": 2, "auto_min_blocks": 12},
            "stations": {
                "A": {"n_now": 3, "n_4h": 5, "auto": False,
                      "diag": {"d7_persons": 9, "d7_blocks": 40, "d7_rows": 1200}},
                "Z": {"n_now": 0, "n_4h": 0, "auto": False,
                      "diag": {"d7_persons": 3, "d7_blocks": 8, "d7_rows": 40}},
                "M": {"n_now": 1, "n_4h": 1, "auto": True,
                      "diag": {"d7_persons": 1, "d7_blocks": 41, "d7_rows": 5200}}},
            "line": {"n_now": 3, "n_4h": 5}, "diag": {"degraded": False}}
    t("mp: normal station → count state with both windows",
      operator_cell("A", _opr) == {"state": "count", "n_now": 3, "n_4h": 5,
                                   "d7_persons": 9, "d7_blocks": 40, "d7_rows": 1200})
    c = operator_cell("Z", _opr)
    t("mp: n_now == 0 is a REAL zero (count state), never an em dash",
      c["state"] == "count" and c["n_now"] == 0)
    c = operator_cell("M", _opr)
    t("mp: machine-account station → auto, and the fake '1' is dropped (D-OP2)",
      c["state"] == "auto" and c["n_now"] is None and c["n_4h"] is None
      and c["d7_persons"] == 1 and c["d7_blocks"] == 41)
    c = operator_cell("NOPE", _opr)
    t("mp: keyless station (no rows in class window) → nosig, not 0",
      c["state"] == "nosig" and c["n_now"] is None)
    t("mp: source not connected → off for every station",
      operator_cell("A", None)["state"] == "off"
      and operator_cell("A", {"ok": False, "unavailable": "x"})["state"] == "off")
    t("mp: classification layer degraded → whole column off (never half-classified counts)",
      operator_cell("A", dict(_opr, diag={"degraded": True}))["state"] == "off")
    t("mp: shape drift (n_now not an int / True) → nosig, never a guessed number",
      operator_cell("A", dict(_opr, stations={"A": {"n_now": "3", "n_4h": 5,
                                                    "auto": False}}))["state"] == "nosig"
      and operator_cell("A", dict(_opr, stations={"A": {"n_now": True, "n_4h": True,
                                                        "auto": False}}))["state"] == "nosig")
    t("mp: cell key set is complete in every state (template never hits an undefined key)",
      all(set(operator_cell(k, o)) == {"state", "n_now", "n_4h", "d7_persons",
                                       "d7_blocks", "d7_rows"}
          for k, o in (("A", _opr), ("M", _opr), ("NOPE", _opr), ("A", None))))
    osum = operator_summary(_opr)
    t("mp: summary carries the deduplicated line count + params (D-OP4 footer)",
      osum["ok"] and osum["line_now"] == 3 and osum["params"]["now_min"] == 60)
    t("mp: summary of a dead source is honest (ok False, counts None)",
      operator_summary(None) == {"ok": False, "line_now": None, "line_4h": None,
                                 "params": {k: None for k in ("now_min", "ref_h", "class_d",
                                                              "auto_max_persons",
                                                              "auto_min_blocks")},
                                 "cap": None, "unavailable": "degraded"})

    # ★ D-OP3 pin：人力**不參與燈判定**——同一板餵/不餵 opr，燈與原因逐列必須完全相同
    b_no = build_board(wip, demo_util_ctx(wip), th, mode="demo")
    b_op = build_board(wip, demo_util_ctx(wip), th, mode="demo", opr=demo_operator(wip))
    t("D-OP3: headcount never changes a lamp (same lamps/reasons with and without opr)",
      [(r["lamp"], r["row"], r["reason_key"]) for r in b_no["rows"]]
      == [(r["lamp"], r["row"], r["reason_key"]) for r in b_op["rows"]])
    t("D-OP3: classify() cannot even see the headcount (no `mp` in its input keys)",
      "mp" not in classify(S(cover_h=6.0, util_4h=0.10, base_7d=0.34), th))
    t("mp: opr absent → every row is 'off' and the board says so",
      all(r["mp"]["state"] == "off" for r in b_no["rows"]) and b_no["opr"]["ok"] is False)

    # demo 合成：四態都看得到（否則 demo 驗不出接線對不對）
    by2 = {r["cls"]: r for r in b_op["rows"]}
    t("demo mp: an auto station is present and shows no number",
      by2["segPotting"]["mp"]["state"] == "auto"
      and by2["segPotting"]["mp"]["n_now"] is None)
    t("demo mp: a real-zero station is present",
      by2["segDieArrayTaping"]["mp"]["state"] == "count"
      and by2["segDieArrayTaping"]["mp"]["n_now"] == 0)
    t("demo mp: a normal counted station is present",
      by2["segDiePlacement"]["mp"]["state"] == "count"
      and by2["segDiePlacement"]["mp"]["n_now"] == 3)
    t("demo mp: pass-through row has no operator signal (nosig, not 0)",
      by2["segFinalAssembly"]["mp"]["state"] == "nosig")
    t("demo mp: board summary counts the four states and line ≤ Σ stations (dedup)",
      b_op["opr"]["ok"] and b_op["opr"]["n_auto"] >= 1 and b_op["opr"]["n_count"] >= 3
      and b_op["opr"]["n_nosig"] >= 1
      and b_op["opr"]["line_now"] <= sum(r["mp"]["n_now"] or 0 for r in b_op["rows"]))
    t("demo mp: auto flag is computed by the live rule, not hard-coded",
      demo_operator(wip, max_persons=0)["stations"]["segPotting"]["auto"] is False)
    # demo 的 auto 判定必須**就是** mes_operator 的規則（同輸入同輸出）——否則規則在
    # mes_operator 改字時，demo 板會靜默與 live 分類分岔。
    try:
        import mes_operator as _mo
        _cases = ((1, 41), (2, 12), (3, 12), (2, 11), (0, 40), (None, 12), (2, None),
                  ("x", 12), (9, 14))
        t("demo mp: _demo_auto is mes_operator.classify_auto_station (same rule, one place)",
          all(_demo_auto(p, b, 2, 12) is _mo.classify_auto_station(p, b, max_persons=2,
                                                                   min_blocks=12)
              for p, b in _cases))
    except ImportError:                      # 模組缺席＝fallback 路徑，仍釘住邊界語意
        t("demo mp: _demo_auto fallback keeps the None/boundary semantics",
          _demo_auto(2, 12, 2, 12) is True and _demo_auto(None, 12, 2, 12) is False)
    # D-OP4 不變式：`line ≤ Σ 各非 auto 站`——**含退化情境**（傳入站全是 auto ⇒ Σ=0 ⇒ line=0，
    # 舊式 max(1, …) 會吐 1 > 0＝板子違反自己 footer 講的話）。
    _auto_only = {"stations": [{"cls": "segPotting", "ord": 7},
                               {"cls": "segSerialisation", "ord": 8}],
                  "line": {}, "diag": {}, "params": {}}
    _oa = demo_operator(_auto_only)
    t("demo mp: an all-automated board reports 0 line-wide, never a phantom 1 (D-OP4)",
      all(v["auto"] for v in _oa["stations"].values())
      and _oa["line"]["n_now"] == 0 and _oa["line"]["n_4h"] == 0)

    # ---- §24 人名紅線：合成輸入帶人名欄 → 輸出零人名 ------------------------
    dirty = {"assignee": "Wang Xiao Ming", "technician": "李小華", "operator_badge": "A12345"}
    wip2 = {"stations": [dict(st[0], **dirty)], "line": {}, "diag": {}, "params": {}}
    _opr_dirty = {"schema": "board.operator.v1", "ok": True, "diag": {"degraded": False},
                  "params": {}, "line": {"n_now": 2, "n_4h": 3},
                  "stations": {st[0]["cls"]: dict(
                      {"n_now": 2, "n_4h": 3, "auto": False,
                       "diag": {"d7_persons": 5, "d7_blocks": 30, "d7_rows": 100,
                                "stpPersonID": "CHANG, CHRIS"}}, **dirty)}}
    b2 = build_board(wip2, {st[0]["cls"]: dict(demo_util_ctx(wip)[st[0]["cls"]], **dirty)},
                     opr=_opr_dirty)
    blob = repr(b2)
    t("§24: person-ish fields never survive into the board (wip, util ctx AND operator)",
      not any(k in blob for k in ("assignee", "technician", "operator_badge", "stpPersonID",
                                  "Wang Xiao Ming", "李小華", "A12345", "CHANG, CHRIS")))
    t("§24: the headcount cell is a five-integer white list — nothing else is copied",
      b2["rows"][0]["mp"] == {"state": "count", "n_now": 2, "n_4h": 3,
                              "d7_persons": 5, "d7_blocks": 30, "d7_rows": 100})

    # ---- build_board 降級路徑 -----------------------------------------------
    b3 = build_board(wip, demo_util_ctx(wip), th, wo_gate=False, mode="live")
    t("wo_gate=False → every down_n is None (no fake veto) and board flags it",
      all(r["down_n"] is None for r in b3["rows"]) and b3["wo_gate"] is False)
    t("build_board tolerates an empty wip (no crash, zero rows)",
      build_board({}, {})["n_rows"] == 0)
    t("build_board is white-list: unknown B1 columns are ignored (additive-safe)",
      "assignee" not in b2["rows"][0])

    print(("FAILED: " + "; ".join(fails)) if fails else "ALL PASS")
    return 1 if fails else 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Tendline Board dispatch board presentation model")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（不連 MES/網路）")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
