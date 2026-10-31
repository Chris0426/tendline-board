#!/usr/bin/env python3
"""Tendline Board — OEE / 達標 呈現模型（老闆兩層視角）。

把凍結核心算出的『真』訊號（稼動 scoreboard + 產出/良率 scoreboard）組成老闆要的兩層：
  Layer 1 達標總覽 — 各站 產出 vs 目標 → 缺口 → 補回加班（operational，期間可選）。
  Layer 2 部門 KPI — OEE = 窄稼動(Availability) × 廣效率(Utilization) × 良率(Quality)。
並產出『全時時間帳本』（24h 絕對組成：生產/idle/down/PM/off-shift，加總＝總機時）。

═══ 誠實鐵則（前端必貫徹標示）═══════════════════════════════════════════════
本模組**不連 MES、不改凍結核心**；只吃 board_data 的 canonical sb/yb（純算術）。
有些輸入『現在沒有源』，為了 demo 先以**確定性假值**填、且全部標 demo：
  • 目標 target          — SALTMARSH 訂單/排程源未接（§26.6 / G1）→ demo 假目標
  • 加班 overtime + 覆蓋 — 人力 CSV 未接（一人多站多台）→ demo 假覆蓋係數
  • A↔U 拆分(down/PM↔idle)— v1 停機全→不明（§23.4，待 cmms/排程/補問裂解）
        → **A×U 乘積＝真實機隊稼動(util)、未造假**；只有『把非生產時間拆成
          設備健康損失 vs 效率損失』這一刀是 demo（角落明標）。
  • off-shift            — 班表源未接 → 以(總機時 − 活躍窗)近似、標『demo/未觀測』
**真實未造假**：output（distinct lot, RAW v0）、producing/util（設備視角 §14.4）、
              quality（FPY/final, RAW v0、§26.5 口徑待 SME）。
口徑（2026-06-28 與 Chris 談定，落 CONCEPT §14.4）：
  窄稼動＝只背設備健康(down/PM/維修)；廣效率＝idle/待料/沒人力/作業 lag 都進這（§14.4 OEE
  損失歸戶）。跨站不加總 output/quality（§26.10）→ 線級 quality 走 anchor 站(CAL)；
  A/U 為設備視角、可跨站 Σ（§14.4 無產品去重）。
════════════════════════════════════════════════════════════════════════════
"""
import random
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---- demo 旋鈕（確定性、per-station seed；真值一接源即覆寫，這些全是 §-pending 槽位）----
# 標明哪些 model 欄位是 demo 造的（前端據此打『假』標）。
DEMO_FIELDS = ("target", "gap", "gap_pct", "met", "overtime_h", "coverage",
               "down_sec", "pm_sec", "idle_sec", "off_shift_sec",
               "availability", "utilization")  # A/U 個別值＝demo 拆分；A×U(=util) 為真


def _seed(cls):
    return sum(ord(c) for c in (cls or "x")) * 131 + 7


def demo_target(cls, output):
    """DEMO 產出目標（§26.6 訂單源未接）：per-station 確定性、**獨立 seed stream**。
       landing（station_model）與 /output 頁共用此函式 → 同站同窗必同目標（跨頁一致）。"""
    if not output:
        return None
    rnd = random.Random(_seed(cls) * 7 + 3)
    return max(1, round(output * rnd.uniform(0.82, 1.18)))


def demo_yield_target(cls):
    """DEMO 良率目標（§26.5 口徑待 SME；Chris 2026-07-03：先掛 ~99% 假目標）：
       per-station 確定性 98.5–99.5%、獨立 seed stream。回 pct（float）。"""
    rnd = random.Random(_seed(cls) * 13 + 5)
    return round(rnd.uniform(98.5, 99.5), 1)


def _round_to_sum(parts, total):
    """把 float 部分四捨五入成**和恰為 total** 的整數（最大餘數法）。避免 69+114+5=188≠187。"""
    floors = {k: int(v) for k, v in parts.items()}
    rem = int(round(total)) - sum(floors.values())
    order = sorted(parts, key=lambda k: parts[k] - floors[k], reverse=True)
    for i in range(max(0, rem)):
        floors[order[i % len(order)]] += 1
    return floors


def _pick_quality(y, qlik_row=None, qlik_on=False):
    """良率 Quality 因子。
       **qlik_on=True（D1 雙軌制、站級/線級面）**：只認 Qlik 口徑該站 `final_yield_pct`
       （first-FAIL 語意）；該站不在 43 站白名單或無資料 → None（OEE 視為 1.0＝既有誠實行為）。
       **絕不在 Qlik 口徑下退回 raw**（D4：站級不並列 raw、也不偷偷用 raw 補洞）。
       qlik_on=False（既有 raw 路徑、機台級/舊行為）：末試 final → 首試 FPY → (100−raw fail%)。"""
    if qlik_on:
        v = (qlik_row or {}).get("final_yield_pct")
        return (v, "qlik_final") if isinstance(v, (int, float)) else (None, None)
    if y:
        if y.get("final_pass_pct") is not None:
            return y["final_pass_pct"], "final"
        if y.get("fpy_raw_pct") is not None:
            return y["fpy_raw_pct"], "fpy"
        if y.get("has_yield_signal") and y.get("fail_pct_raw") is not None:
            return max(0.0, 100.0 - y["fail_pct_raw"]), "1-fail"
    return None, None


# ═══ A↔U 真實拆分（工單口徑，2026-07-07 W8/A3 live 後）════════════════════════════
#   在 cmms W8/A3 接通前，A↔U 拆分只能造 demo（見上 §誠實鐵則）。接通後改吃真工單停機：
#     base_h  ＝ fleet_size × window_h（24h 全時基準；同 /equipment A%、§14.4.1 G1-前近似）
#     A       ＝ 1 − wo_down_h / base_h            （窄稼動：只被工單停機拉低＝設備健康 F4）
#     U       ＝ producing_h / (base_h − wo_down_h)（廣效率：up 之中有沒有一直產）
#     ★恆等   A×U ≡ producing_h / base_h ＝『真實 24h 稼動』（未造假）——只有把非生產拆成
#            『設備健康損失 A』vs『效率損失 U』這一刀改由真工單驅動，乘積仍是真 util。
#   口徑差異（誠實揭露）：base＝fleet×window（24h），**不是** util_strict 欄的 producing/active_span；
#   兩者基準不同（active_span 已排除 off-shift），故此 A×U 會 ≤ util_strict 欄（更保守、更誠實）。
#   除零守恆：wo_down ≥ base → A=0、U 未定義(None)；base=0 → 全 0/None。
def real_split(producing_sec, fleet_size, window_sec, wo_down_sec):
    """工單口徑 A↔U 真實拆分（24h 基準、恆等保持）。回 dict：
       {base_sec, wo_down_sec(clamped), availability(A), utilization(U|None), util24(=A×U)}。
       純函式、確定性、零 I/O。恆等：A×U == producing/base（up>0 時，浮點 <1e-9）。"""
    base = (fleet_size or 0) * float(window_sec or 0.0)       # fleet-hours（秒）
    prod = max(0.0, float(producing_sec or 0.0))
    wd = max(0.0, float(wo_down_sec or 0.0))
    if base <= 0:
        return {"base_sec": 0.0, "wo_down_sec": 0.0,
                "availability": 0.0, "utilization": None, "util24": 0.0}
    wd = min(wd, base)                                        # 停機不可能 > fleet-hours（clamp 誠實）
    util24 = prod / base                                     # ＝A×U（真、24h 基準）
    a = 1.0 - wd / base                                      # 窄稼動 A
    up = base - wd
    u = (prod / up) if up > 0 else None                     # 廣效率 U；wd≥base → None（除零守恆）
    return {"base_sec": base, "wo_down_sec": wd,
            "availability": a, "utilization": u, "util24": util24}


# ═══ §6.7（2026-08-25 裁決）：站級三軸換到**應生產時段**軸 ═══════════════════════════
#   存證＝`SPEC-RUNWINDOW.md` **§6.7**（Chris：「就一起換了，不應該還留著沒換的頁，
#   造成與其他頁的口徑不一致」）。收的是 §6.5-4 留下的待辦：`/station/{cls}` 與著陸頁 `/`
#   共用本支 `station_model`，而它的分母原本是 `active_span_sec`（活躍期間）／`fleet×24h`
#   ⇒ 與 `/utilization`／`/equipment` 的**應生產時段**軸兩把尺。
#
#   ★★ 本模組**不算該跑窗、一秒都不算**（§6.7 反模式 2）——`rw` 是呼叫端算好餵進來的：
#      分母出自 `board_eqmeeting.erw_machine_windows`（＝`/utilization` 圓餅與
#      `/equipment` 同一支、QG-13「永不各算各的」），停機出自 `wo_segments_scoped` ∩ 該跑窗。
#      本檔只做純算術。⇒ 這裡**沒有**任何 `erw_*`／日曆／綑名冊的 import。
#
#   口徑（三行，改動前先讀 SPEC §6.7 ＋ **`SPEC-CAPACITY.md` §3.1**）：
#      planned ＝ |RW|（Σ 站內**名冊**機台；名冊外不進分母也不進分子＝C-RW9）
#      up      ＝ |RW| − **全部計停機工單** ∩ RW     ← **白名單**，見 RW_AVAIL_DOWN_STATES
#                （★ 2026-09-04 Chris 裁：由「維修＋計畫保養」擴成 `/equipment` 妥善率那把尺）
#      A ＝ up / |RW|；U ＝ 生產 / up；OEE ＝ A × U × Q
#   ⚠ **`util24` 這個名字在這條軸上不存在**（A7／C-RW16 的同一條紀律：舊名裝新定義是禁區）——
#     本節一律回 `util_rw`，而且只有 `real_split()`（24h 基準）才保留 `util24` 鍵。
#   ⚠ `|RW| = 0` ⇒ A／U／OEE 全 None（C-RW10：永不 0%／100%），呼叫端顯「—」。
# ═══ ★★ 2026-09-04 Chris 裁決：**A 的口徑統一到 `/equipment` 妥善率** ═══════════════
#   存證＝`SPEC-CAPACITY.md` §2.4／§3.1（**反轉** `SPEC-RUNWINDOW.md` §6.7-4 ③ 與
#   §6.7-5 反模式 11：站頁的 A 從此**就叫妥善率**，因為口徑真的一樣了）。
#   · 舊（窄稼動）：只扣 維修＋計畫保養 ⇒ 等料／等外包／legacy 落在 U 那一側。
#   · 新（妥善率）：**全部計停機工單**都扣 ⇒ 與 `/equipment`／`/utilization`／`/availability`
#     同一把尺。代數後果（SPEC §3.2，selftest 釘死）：**A 變低、U 變高、A×U 與 OEE 一個
#     位元不動**（`A×U ≡ 生產/planned`，扣除項只是在 A 與 U 之間換邊）。
#
# ⚠ `LEGACY_WO_STATE` **2026-09-04 從下面的組成節搬到這裡**（一字未改、只換位置）：
#   下面那個位置在 `RW_AVAIL_DOWN_STATES` **之後**，留在原處會 NameError。
LEGACY_WO_STATE = "舊制工單"        # eMaint legacy／未分類工單的收斂顯示桶（灰＝/equipment --st-legacy）
RW_AVAIL_DOWN_STATES = frozenset({
    "維修", "計畫保養", "等料(停機)", "等外包(停機)", "故障停機",
    # eMaint legacy 的三種輸入具名 ＋ 本檔自己的收斂桶
    "調機", "其他維護", "其他維護(子類待定)", LEGACY_WO_STATE,
})
#   ⚠ **仍然是白名單、不是「其餘皆停機」**（同 `app.RW_DOWN_STATES` 的紀律③、
#     `SPEC-RUNWINDOW` §6.7-5 反模式 6）：`不明`／`待機`／`流程等待`／`非排定` 一律**不扣**
#     ——把「不知道」畫成「壞了」是這個專案最貴的一種謊。今天餵進來的 `by_state` 只會有
#     cmms countable 工單態（`app.rw_station_payload` 只收 `wem.wo_segments_scoped` 的段）
#     ⇒ 寫成「其餘皆停機」今天結果一樣、明天就會出事，**所以形要保住**。
#   ⚠ `"其他維護(子類待定)"` ＝ `board_w8.BUCKET_STATE` 的**照抄一份**：本檔**刻意不
#     import `board_w8`**（`board_monthly._selftest` 有一根 `_ALLOWED_Board =
#     {board_monthly, board_oee}` 的釘子，多一支就紅）。防漂移靠 run_smoke 的**同字
#     等式釘**——與 `RW_IDLE_STATE ≡ wem.UNEXPLAINED_STATE` 用的是同一招。
#   ⚠ `app.RW_DOWN_STATES` **派生自本集合**（＋顯示層專用的總包桶）＝單一出處、零重打。

RW_HEALTH_STATES = frozenset({"維修", "計畫保養"})
#   ⚠ **舊尺、相容欄專用——三軸再也不吃它**（2026-09-04 起）。留著的唯一理由是
#     `board_attrib` 的日帳 v5 仍要寫一欄 `health_down_sec`（舊值照寫，讓拿舊截圖
#     對帳的人查得到；`SPEC-CAPACITY.md` §3.1「v5 走加法、不是改名」）。
#     **任何新的消費者一律用 `RW_AVAIL_DOWN_STATES`。**
RW_IDLE_STATE = "該跑沒跑"          # ＝ `board_eqmeeting.UNEXPLAINED_STATE`（§23 canonical；
#                                     本檔不 import 它＝避免相依環，run_smoke 有同字等式釘）


def rw_avail_down_sec(by_state):
    """`by_state`（**已裁到應生產時段**的逐態秒）→ 妥善率要扣的秒數（**白名單 Σ**）。純函式。

       ＝2026-09-04 起三軸 A 的扣除項（`SPEC-CAPACITY.md` §3.1）。
       ⚠ 這是 Σ **不是 union**：呼叫端餵進來的 `by_state` 已經「逐態先自我 union 再 ∩ RW」
         （`app.rw_station_payload`／`board_attrib._wo_intervals_by_state` 同形），
         跨態重疊由 `rw_split` 的 `min(hd, planned)` clamp 收口（與換白名單前同一條路）。"""
    return sum(max(0.0, float(sec or 0.0))
               for st, sec in (by_state or {}).items() if st in RW_AVAIL_DOWN_STATES)


def rw_health_down_sec(by_state):
    """**舊尺**（窄稼動＝只扣維修＋計畫保養）。⚠ 三軸不再用它，見 `RW_HEALTH_STATES`。"""
    return sum(max(0.0, float(sec or 0.0))
               for st, sec in (by_state or {}).items() if st in RW_HEALTH_STATES)


def rw_split(producing_sec, planned_sec, health_down_sec):
    """應生產時段軸的 A↔U 拆分（§6.7）。回 dict：
       {planned_sec, health_down_sec(clamped), availability(A|None), utilization(U|None),
        util_rw(=A×U|None)}。純函式、確定性、零 I/O。**刻意沒有 `util24` 鍵**（見上）。

       · `planned_sec <= 0` ⇒ A/U/util_rw 全 **None**（C-RW10：週日/整日停線不是 0% 也不是 100%）。
       · `up == 0`（整段都在維修/保養）⇒ A=0、U=None（除零守恆，同 `real_split`）。
       · **U > 1 ⇒ None、絕不 clamp**：那代表「量到的生產時數比應生產時段還長」
         （生產落在應生產時段之外），夾成 100% 會把矛盾藏起來——顯「—」才看得見。"""
    planned = max(0.0, float(planned_sec or 0.0))
    if planned <= 0:
        return {"planned_sec": 0.0, "health_down_sec": 0.0,
                "availability": None, "utilization": None, "util_rw": None}
    hd = min(max(0.0, float(health_down_sec or 0.0)), planned)   # 停機不可能 > 應生產時段
    prod = max(0.0, float(producing_sec or 0.0))
    up = planned - hd
    a = up / planned
    u = (prod / up) if up > 0 else None
    if u is not None and u > 1.0 + 1e-9:
        u = None                                                 # 矛盾不 clamp（見 docstring）
    return {"planned_sec": planned, "health_down_sec": hd,
            "availability": a, "utilization": u,
            "util_rw": (a * u) if u is not None else None}




# ═══ ★★ 產能瀑布（2026-09-04 Chris 裁；存證＝`SPEC-CAPACITY.md` §2.6／§3.4）═════════
#   老闆問的那個問題：「這一站這段時間，本來能做幾顆、少了哪幾層、剩下幾顆」。
#
#   理論產能 T = planned_h × r
#   − 停機   D = down_h    × r          （down_h ＝ RW_AVAIL_DOWN_STATES ∩ RW）
#   − 該跑沒跑 I = idle_h  × r
#   − 不良   F = qlik_fail              （PH 計數、直接可用）
#   ＝ 良品   G = qlik_pass              ★ **量到的數**（Qlik 口徑，與產出頁／月報同一個）
#   ＋ 重工當量 W = (T−D−I−F) − G        ★ 殘差落在這裡 ⇒ **恆等式 T−D−I−F ≡ G＋W**
#
#   ★★ **2026-09-04 Fable 自糾（`SPEC-CAPACITY.md` §3.4／§10 ③）：主字必須是量到的數。**
#     初版把最底列印成殘差 `T−D−I−F`（＝實測 ＋ 重工當量），比 `qlik_pass` **高**。
#     那是**對不上的頭數字**：老闆會拿這一格去對 `/output` 與 `/monthly`，而那兩頁印的是
#     `qlik_pass`。⇒ 主字改印實測，殘差**顯式成獨立一項**「＋重工當量」，恆等式改形
#     （不是消失——它從「殘差＝良品」變成「殘差＝良品＋重工當量」，一樣構造性成立）。
#     ⚠ `W` 嚴格說是 `r·rework_h ＋ 速率殘差`：重工是它的主項，但當實際速率偏離當月中位數
#       時它也會吸收那一塊 ⇒ **tooltip 要同時給帳上的重工估計值**，`|W|/G > 15%` 出 chip。
#       `W < 0`（實際比中位速率快）**不是重工** ⇒ 顯示層改標「速率殘差」，不硬叫重工。
#
#   ★ 為什麼一個「計數」可以放進一條「乘出來」的鏈裡（SPEC §3.4 的推導，摘要）：
#     `r_good = qlik_pass / good_h`，而 `good_h` **已經扣掉重工與報廢時數**
#     （`board_unit` 提案 E：「兩者都扣 ⇒ r_good 對報廢率中性、四個桶彼此正交」）。
#     代入 `scrap_h = first_h·q`、`good_h = first_h·(1−q)`、`q = fail/(pass+fail)`：
#         r × scrap_h ＝ qlik_pass · q/(1−q) ≡ qlik_fail
#     ⇒ 「不良層用量到的整數」與「不良層用 r 乘出來」**是同一個數**。取整數（無捨入）。
#     ⇒ 這同時也證明 `/monthly` 三損失合計把 `down_h×r` 和 `qlik_fail` 相加**不是混尺**。
#
#   ★ 重工為什麼是**加項**而不是第四層扣除（＝Chris 裁的「重工不另扣」在算術上的樣子）：
#         T − D − I ＝ r × producing_h ＝ qlik_pass + r·rework_h + qlik_fail
#         ⇒ (T−D−I−F) − qlik_pass ＝ r·rework_h ≥ 0
#     那段機時**確實被生產用掉了、只是沒有變成新的良品**⇒ 它站在良品旁邊當加項，
#     而不是再從良品裡扣一次（扣了就是同一筆損失扣兩次）。
#     版面上畫成良品段內的**斜紋**、旁邊顯式標「＋重工當量 N PH」（C-CP4）。
#
#   ⚠ 溢出（生產 ＋ 停機 > |RW|）**吃「生產」、停機不縮**——與 `_composition_rw` **同一套
#     分配**（§6.7-1）。不同套的話瀑布的層長與組成條的桶長會對不起來。
#   ⚠ `r is None`（無尺）⇒ **只回小時、PH 全 None**（C-CP2：絕不外推）。
#   ⚠ `planned <= 0` ⇒ 回 None（C-RW10：沒有應生產時段就沒有這張圖，不畫 0）。
def capacity_waterfall(planned_sec, avail_down_sec, producing_sec, rate_ph_per_h=None,
                       qlik_fail=None, qlik_pass=None, rework_equiv_h=None,
                       rework_pct=None, resid_tol=0.15):
    """應生產時段 ＋ 良品速率 → 產能瀑布。**純函式、確定性、零 I/O。**

       回 `None`＝這一站這段時間沒有應生產時段（顯「—」，不是畫一張全 0 的圖）。
       回 dict：小時四欄恆有值；PH 六欄在 `ph_ok` 為真時才有值。
    """
    planned_h = max(0.0, float(planned_sec or 0.0)) / 3600.0
    if planned_h <= 0:
        return None
    # ── 時間三層：與 `_composition_rw` **同一套分配**（停機先落位、生產吸收溢出）──
    down_h = min(max(0.0, float(avail_down_sec or 0.0)) / 3600.0, planned_h)
    rest_h = planned_h - down_h
    prod_h = min(max(0.0, float(producing_sec or 0.0)) / 3600.0, rest_h)
    idle_h = rest_h - prod_h
    out = {"planned_h": planned_h, "down_h": down_h, "idle_h": idle_h,
           "producing_h": prod_h,
           "theory_ph": None, "down_ph": None, "idle_ph": None, "fail_ph": None,
           # ★ `good_ph` ＝**量到的** `qlik_pass`（主字）。殘差住 `equiv_ph`。
           "good_ph": None, "equiv_ph": None, "residual_ph": None,
           "rework_ledger_ph": None, "rework_pct": None, "rework_share": None,
           "rate_ph_per_h": None, "ph_ok": False, "resid_flag": False,
           "contradiction": False}
    r = None if rate_ph_per_h is None else float(rate_ph_per_h)
    # ⚠ **`qlik_pass` 也是硬前提**（2026-09-04 自糾）：最底列的主字就是它，
    #   沒有它就沒有可印的良品數 ⇒ 整條 PH 停用（半套 PH 比沒有 PH 更糟）。
    if r is None or r <= 0 or qlik_fail is None or qlik_pass is None:
        return out                                   # 無尺／無計數 ⇒ 只畫小時（C-CP2）
    out["rate_ph_per_h"] = r
    theory = int(round(planned_h * r))
    parts = _round_to_sum({"down": down_h * r, "idle": idle_h * r, "prod": prod_h * r},
                          theory)
    fail = max(0, int(qlik_fail))
    residual = parts["prod"] - fail                  # ≡ theory − down − idle − fail
    good = int(qlik_pass)                            # ★ 主字＝量到的數
    out.update({"theory_ph": theory, "down_ph": parts["down"], "idle_ph": parts["idle"],
                "fail_ph": fail, "residual_ph": residual,
                "good_ph": good, "equiv_ph": residual - good, "ph_ok": True,
                # 殘差為負＝「量到的不良比估出來的產出還多」——真矛盾，**不夾成 0**
                # （夾了就把它藏起來，同 `U > 1` 的紀律）。
                "contradiction": residual < 0})
    if good > 0:
        out["resid_flag"] = abs(out["equiv_ph"]) / good > float(resid_tol)
    # 良品段的斜紋＝重工當量佔「良品＋當量」的比例（負當量不畫斜紋，那不是重工）。
    if residual > 0 and out["equiv_ph"] > 0:
        out["rework_share"] = min(1.0, out["equiv_ph"] / float(residual))
    if rework_equiv_h is not None:                   # 帳上的重工估計（給 tooltip 對照用）
        out["rework_ledger_ph"] = int(round(max(0.0, float(rework_equiv_h)) * r))
    if rework_pct is not None:
        out["rework_pct"] = float(rework_pct)
    return out


# 工單態 → 全時組成桶（前端 state_color 上色）。
#   ★ 2026-08-16 Chris 裁決＝machine-time 分類全站統一到 /equipment 那套：
#     維修／計畫保養／等料(停機)／等外包(停機) **直通**（各自 canonical 色）；
#     legacy（eMaint 調機/其他維護(子類待定)）與無法對上新分類者收進單一灰桶「舊制工單」
#     ——**時數不藏**（那是真停機），只是不再假裝它是一種現行的 machine-time 類。
#   ⚠ 勿退回舊版「等料/等外包塞進其他維護」——cmms 已無 machine-time 的 調機/其他維護 分類。
# ⚠ `LEGACY_WO_STATE` 已搬到 `RW_AVAIL_DOWN_STATES` 上方（2026-09-04；那個集合要用它）。
_WO_DOWN_STATES = ("維修", "計畫保養", "等料(停機)", "等外包(停機)")
_WO_COMP_ORDER = ("生產",) + _WO_DOWN_STATES + (LEGACY_WO_STATE,)
REMAINDER_STATE = "未拆分"          # 剩餘＝idle/off-shift（排程源未接前不再細拆；單一灰桶）


def _map_wo_comp(state):
    """工單分態 → 組成桶 key。/equipment 四個計停機態直通；其餘（legacy/未知）→『舊制工單』。"""
    return state if state in _WO_DOWN_STATES else LEGACY_WO_STATE


def _composition_real(producing, wd, by_state, total_cap):
    """真實工單口徑全時組成（24h）：生產(真) + 工單停機(真，按 by_state 比例攤到 union 總量)
       + 單一灰『未拆分』剩餘（idle/off-shift；排程源未接前不再細拆）。加總＝total_cap。
       cross-source overlap（工單窗與 MES 生產在牆鐘上重疊）時 clamp：down 桶 ≤ total_cap−producing。"""
    total = total_cap if total_cap > 0 else ((producing + wd) if (producing + wd) > 0 else 1.0)
    comp_prod = min(producing, total)
    down_total = min(wd, max(0.0, total - comp_prod))         # clamp overlap（不讓桶溢出全時）
    mapped = {}
    for st, sec in (by_state or {}).items():
        mapped[_map_wo_comp(st)] = mapped.get(_map_wo_comp(st), 0.0) + max(0.0, float(sec or 0.0))
    ssum = sum(mapped.values())
    buckets = {}
    if down_total > 0 and ssum > 0:
        for k, sec in mapped.items():
            buckets[k] = down_total * (sec / ssum)           # 按 by_state 比例攤到 union 總量
    elif down_total > 0:
        buckets[LEGACY_WO_STATE] = down_total                # 有停機但無態明細 → 誠實落灰桶
    remainder = max(0.0, total - comp_prod - sum(buckets.values()))
    out = []
    if comp_prod > 0:
        out.append(("生產", comp_prod))
    for k in _WO_DOWN_STATES + (LEGACY_WO_STATE,):
        if buckets.get(k, 0.0) > 0:
            out.append((k, buckets[k]))
    if remainder > 0:
        out.append((REMAINDER_STATE, remainder))
    return [{"state": s, "sec": sec, "pct": sec / total * 100.0} for s, sec in out]


def _composition_rw(producing, by_state, planned_sec):
    """應生產時段內的狀態組成（§6.7；加總＝`planned_sec`，不是 24h 全時）。

       生產（真）＋ cmms 逐態（**呼叫端已裁到應生產時段**、legacy 收灰桶）＋ 殘量＝`該跑沒跑`。
       ⚠ 殘量的名字**不是「未拆分」也不是「不明」**：換軸之後「應生產時段內、沒有生產訊號、
         也沒有任何工單紀錄」就是**該跑沒跑**（§6.6-b 一桶一名，兩語同字）。

       ★★ **溢出（生產 ＋ 停機 > |RW|）吃「生產」，停機桶永不縮**（2026-08-25 對抗式驗收裁決；
          §6.7-1）。理由是**兩個桶的可信度不同、不是對稱的**：
            · 停機 ＝ cmms 工單 **∩ 應生產時段**（已裁切、逐態先自我 union）＝**可信**；
            · 生產 ＝ `analyze()` 的**全期間聚合、未依應生產時段裁切**＝**已知的近似值**（§6.7-4 ②）。
          ⇒ 縮停機會讓**組成條上的維修長度**與**卡片上的 A**（`1 − 停機/|RW|`，用未縮的值）
            互相打臉，而且縮掉的正是那個可信的量。⇒ 溢出一律由近似的那一側吸收。
          ⚠ 生產被吃掉時 `rw_split` 的 `U` 通常同時落 None（`producing > up`＝矛盾不 clamp）
            ⇒ 卡片顯「—」、條上照樣看得到停機 ＝ **兩邊講的是同一件事**。
       ⚠ 唯一還會縮停機的情況＝**跨態重疊**讓 `Σ停機 > |RW|`（同態已 union、不會自己造成）：
         那時 total 本身放不下，等比縮是唯一選擇（同 `rw_split` 對 `health_down` 的 clamp）。
       純函式。"""
    total = max(0.0, float(planned_sec or 0.0))
    if total <= 0:
        return []
    mapped = {}
    for st, sec in (by_state or {}).items():
        if st == "生產":
            continue                                   # 生產走量測值（producing），不重複計
        k = _map_wo_comp(st)
        mapped[k] = mapped.get(k, 0.0) + max(0.0, float(sec or 0.0))
    dsum = sum(mapped.values())
    if dsum > total and dsum > 0:                      # 跨態重疊：total 放不下 ⇒ 最後手段
        mapped = {k: v * (total / dsum) for k, v in mapped.items()}
        dsum = total
    # ★ 停機先落位、**不縮**；生產只拿得到剩下的空間（溢出由近似的那一側吸收）
    comp_prod = max(0.0, min(max(0.0, float(producing or 0.0)), total - dsum))
    out = []
    if comp_prod > 0:
        out.append(("生產", comp_prod))
    for k in _WO_DOWN_STATES + (LEGACY_WO_STATE,):
        if mapped.get(k, 0.0) > 0:
            out.append((k, mapped[k]))
    rest = max(0.0, total - comp_prod - sum(mapped.values()))
    if rest > 0:
        out.append((RW_IDLE_STATE, rest))
    return [{"state": s, "sec": sec, "pct": sec / total * 100.0} for s, sec in out]


def rollup_downtime_by_cls(down_by_eid, fleet_by_cls):
    """per-EID 工單停機 → per-站(cls) 彙總。down_by_eid={eid:{down_sec,by_state}}、
       fleet_by_cls={cls:[(eid,desc),…]}。站級停機＝Σ 各機台停機（並列產能＝相加、非跨機 union）。
       回 {cls:{down_sec,by_state}}。純函式、零 I/O。"""
    out = {}
    for cls, fleet in (fleet_by_cls or {}).items():
        dsec, bystate = 0.0, {}
        for item in (fleet or []):
            eid = item[0] if isinstance(item, (list, tuple)) else item
            d = (down_by_eid or {}).get(eid)
            if not d:
                continue
            dsec += float(d.get("down_sec") or 0.0)
            for st, sec in (d.get("by_state") or {}).items():
                bystate[st] = bystate.get(st, 0.0) + float(sec or 0.0)
        out[cls] = {"down_sec": dsec, "by_state": bystate}
    return out


def station_model(u, y, window_sec, demo=True, wo=None, qlik_row=None, qlik_on=False, rw=None):
    """單站 Layer-1 + Layer-2 model。u=稼動 scoreboard 站 rec、y=yield 站 rec（可 None）。
       window_sec=觀測窗秒（全時帳本分母錨）。回 dict（含 real/demo 混合，欄位語意見 docstring）。
       wo=None → A↔U 拆分為 DEMO（今行為，見上）；wo={'down_sec','by_state'}（工單口徑真停機）
       → A↔U 拆分改吃真工單（real_split，24h 基準）、全時組成改真桶＋灰剩餘。target/overtime 恆 demo。
       **qlik_on=False（預設）＝舊行為 byte-identical**（raw output/quality）。
       **qlik_on=True（D1/D2 站級面）**：output＝Qlik TOTAL、quality＝Qlik final_yield；
       qlik_row=None（該站不在 43 站白名單／grid 不可用）→ output/quality **誠實留白 None**
       （`output_withheld=True`），**絕不回退 raw**。稼動軸(A/U/停機)完全不受影響＝雙軌制。

       ★ **rw（§6.7；2026-08-25 裁決）＝應生產時段軸**。`rw={'planned_sec', 'by_state', …}`
         （**呼叫端算好、已裁到應生產時段、已只含名冊機台**——本檔不算該跑窗，見上方節註）
         ⇒ A／U／OEE／組成全部改吃這條軸，且**優先於 `wo`**（同一頁不並存兩把尺）。
         `rw=None`（預設）＝**逐位元退回今天的行為**（demo／snapshot 恆走這條＝QG-4.2 混源禁令）。
         `rw['planned_sec'] <= 0`（名冊外站、整期間停線）⇒ A／U／OEE **None**＝顯「—」
         （C-RW10／C-RW9：永不 0%／100%、永不拿鄰綑代打）。"""
    cls = u.get("cls")
    rnd = random.Random(_seed(cls))
    fleet = u.get("fleet_size") or 0
    # 零工期站（M4 後遺；board_data 標）：MES 段無時長→段長法稼動結構性不可測→A/U/OEE 顯「—」（None）、
    #   不讓 0 進乘積。output/quality 不受影響（走 yield、與段時長無關）。
    zlo = bool(u.get("zero_len_only"))
    producing = float(u.get("producing_sec") or 0.0)          # REAL
    active = float(u.get("active_span_sec") or 0.0)           # REAL（活躍窗＝設備視角『排定』proxy）
    total_cap = (fleet or 0) * float(window_sec or 0)         # 全時機時（24h 絕對視圖分母）
    q_pct, q_src = _pick_quality(y, qlik_row, qlik_on)
    quality = (q_pct / 100.0) if q_pct is not None else None
    real = wo is not None
    rw_on = rw is not None
    rw_planned = rw_hd = None

    if rw_on:
        # —— §6.7 應生產時段軸（分母與停機都由呼叫端裁好；本檔只做算術）——
        #   ★ 2026-09-04：扣除項換成**妥善率白名單**（`SPEC-CAPACITY.md` §3.1）。
        rw_hd = rw_avail_down_sec(rw.get("by_state"))
        rs = rw_split(producing, rw.get("planned_sec"), rw_hd)
        availability = rs["availability"]
        utilization = rs["utilization"]
        rw_planned = rs["planned_sec"]
        rw_hd = rs["health_down_sec"]
        util_rw = rs["util_rw"]
        oee = (util_rw * (quality if quality is not None else 1.0)) if util_rw is not None else None
        composition = _composition_rw(producing, rw.get("by_state") or {}, rw_planned)
        down = pm = idle = off_shift = 0.0                    # 真桶走 composition；demo 欄位歸零
        wo_down_sec = rw_hd                                   # ＝窄稼動扣除項（維修＋計畫保養∩RW）
        util_real = util_rw
    elif real:
        # —— 工單口徑真拆分（24h 基準；A×U＝真 util、未造假）——
        wo_down_sec = max(0.0, float((wo or {}).get("down_sec") or 0.0))
        rs = real_split(producing, fleet, window_sec, wo_down_sec)
        availability = rs["availability"]
        utilization = rs["utilization"]                       # 可能 None（wd≥base）
        util24 = rs["util24"]
        oee = util24 * (quality if quality is not None else 1.0)   # ≡A×U×Q（U None 時仍成立）
        composition = _composition_real(producing, rs["wo_down_sec"],
                                        (wo or {}).get("by_state") or {}, total_cap)
        down = pm = idle = off_shift = 0.0                    # 真桶走 composition；demo 欄位歸零
        util_real = util24
    else:
        # —— DEMO 拆分（今行為，不變）：非生產時間拆 down/PM/idle、off-shift＝總機時−活躍窗 ——
        wo_down_sec = None
        non_prod = max(0.0, active - producing)               # REAL 非生產（窗內）
        down = non_prod * (rnd.uniform(0.15, 0.38) if demo else 0.0)   # DEMO：設備故障/維修
        pm = non_prod * (rnd.uniform(0.08, 0.20) if demo else 0.0)     # DEMO：計畫保養
        idle = max(0.0, non_prod - down - pm)                 # 殘＝idle/待料/作業 lag（廣效率損失）
        off_shift = max(0.0, total_cap - active)              # DEMO/approx：未觀測/off-shift
        up = producing + idle                                 # 設備 up（= active − down − pm）
        planned = active                                      # §23.3 off-shift 不入分母
        availability = (up / planned) if planned > 0 else 0.0 # 窄：只 down/PM 拉低
        utilization = (producing / up) if up > 0 else 0.0     # 廣：idle/待料/作業 lag 拉低
        util24 = availability * utilization
        oee = availability * utilization * (quality if quality is not None else 1.0)
        composition = _composition(producing, idle, down, pm, off_shift, total_cap, active)
        util_real = (producing / active) if active > 0 else 0.0

    # —— Layer 1：產出 vs 目標（output 真；target/overtime demo，待 SALTMARSH 源）——
    #   qlik_on＝站級公司口徑：output＝Qlik TOTAL（AllPHID 計數、B+Fresh、Start 側首次落窗）；
    #   無對映列 → None＝誠實留白（withheld），**不是 0、也不回退 raw**（D1/D4/D8）。
    if qlik_on:
        _q_total = (qlik_row or {}).get("total")
        output = _q_total if isinstance(_q_total, int) else None
    else:
        output = (y.get("output_units") if y else 0) or 0     # REAL（distinct lot, RAW v0）
    output_withheld = qlik_on and output is None
    # 零工期站 producing/active 恆 0、稼動段長法不可測（zlo），但產出走 lot count 與段時長無關 →
    #   仍須照常顯示產出（含 demo target/met），不因 active==0 被藏。
    #   qlik_on 同理更徹底：Qlik TOTAL 是 first-attempt 落窗的單位計數，**與本站段時長完全無關**
    #   ⇒ 有數字就顯示（不因該站本窗 MES 稼動訊號為零而藏起真實產出）。
    has_output = bool(output) and output > 0 and (qlik_on or active > 0 or zlo)
    target = gap = gap_pct = None
    met = None
    overtime_h = coverage = None
    if has_output and demo:
        target = demo_target(cls, output)                    # DEMO：獨立 stream、與 /output 頁同源
        gap = output - target                                # <0 ⇒ 落後
        gap_pct = (gap / target * 100.0) if target else 0.0
        met = gap >= 0
        if gap < 0:                                          # 估補回加班（demo：率真、覆蓋假）
            rate_uph = output / (producing / 3600.0) if producing > 0 else 0.0  # 件/機台小時
            if rate_uph > 0:                                 # producing==0（零工期站）→無法估速率→加班留 None（非 0）
                mach_h = abs(gap) / rate_uph
                coverage = rnd.randint(2, 4)                 # DEMO：一人顧 N 台（一人多站多台）
                overtime_h = mach_h / coverage

    # —— 缺口歸因 waterfall（落後時）：把 |gap| 依 OEE 三損失比例拆成 設備/效率/良率 三因 ——
    #   設備損失 al=(1−A)、效率損失 ul=A(1−U)、良率損失 ql=A·U(1−Q)；三者和＝1−OEE。
    #   ＝Layer-1『為什麼沒達標』直接落到 Layer-2 三 KPI（口徑見模組 docstring）。
    cause = None
    # ★ §6.7：`availability is None`＝應生產時段＝0（C-RW10 顯「—」）⇒ **沒有可信的三軸基底**
    #   ⇒ 歸因整塊撤（同 zlo 的既有處置）。舊軸恆有 A（0.0 也算），這個守衛只在新軸會生效。
    if gap is not None and gap < 0 and availability is not None:
        _u = utilization if utilization is not None else 1.0   # U 未定義(工單全停) → 效率損失視為 0
        al = 1.0 - availability
        ul = availability * (1.0 - _u)
        ql = (availability * _u * (1.0 - quality)) if quality is not None else 0.0
        denom = al + ul + ql
        if denom > 0:
            g = abs(gap)
            parts = _round_to_sum({"avail": g * al / denom, "effi": g * ul / denom,
                                   "yield": g * ql / denom}, g)        # 和恰＝|gap|
            cause = {**parts, "total": g, "has_quality": quality is not None}

    if zlo:
        # 零工期站：稼動軸不可測 → A/U/OEE/util_real 全 None（誠實「—」，非 0）；
        #   全時組成/缺口歸因依賴稼動拆分 → 一併撤（無可信基底）。output/target/met/quality 保留。
        availability = utilization = oee = util_real = None
        composition = []
        cause = None

    return {
        "cls": cls, "fleet_size": fleet, "has_output": has_output,
        "util_unmeasurable": zlo,
        # Layer 1（output 真；其餘 demo）。qlik＝站級公司口徑在用；output_withheld＝該站無 Qlik 列
        # ⇒ 顯「—」（誠實留白、非 0、非 raw 頂替）。
        "qlik": bool(qlik_on), "output_withheld": output_withheld,
        "output": output, "target": target, "gap": gap, "gap_pct": gap_pct, "met": met,
        "overtime_h": overtime_h, "coverage": coverage, "cause": cause,
        # Layer 2 OEE 因子（rw＝應生產時段軸；real＝工單口徑真拆分、24h 基準；demo＝造 A↔U 拆分）
        "availability": availability, "utilization": utilization,
        "real_split": real, "wo_down_sec": wo_down_sec,
        # ★ §6.7：`rw_axis` True ⇒ 上面三軸的分母是**應生產時段**（不是 24h、也不是活躍期間）。
        #   顯示層據此換標籤與 tooltip——**舊名裝新定義是禁區**（A7／C-RW16 同一條紀律）。
        #   `rw_planned_sec`＝|RW|（None＝不在這條軸上）；`rw_meta`＝呼叫端的涵蓋／台數／告示旗。
        # ★ 2026-09-04 鍵改名 `rw_health_down_sec` → `rw_avail_down_sec`（口徑換了，
        #   舊名裝新定義是禁區＝§6.7-2／C-CP8）。**這個鍵在改名前零消費者**（模板與
        #   `board_mcp` 都沒讀它）⇒ 改名不連鎖。
        "rw_axis": rw_on, "rw_planned_sec": rw_planned, "rw_avail_down_sec": rw_hd,
        "rw_meta": (rw.get("meta") if rw_on else None),
        "quality": quality, "quality_pct": q_pct, "quality_src": q_src,
        "oee": oee, "util_real": util_real,  # =A×U 校驗（real＝util24、demo＝producing/active）
        # F3b 效能軸（§26.10）：滿載基準(rated takt)待 SME → performance=None、**不入 OEE 乘積**
        #   （守 A×U＝真實 util 恆等、3 因子 OEE byte 不變）；actual_rate_uph＝真實絕對速率
        #   (module/機台小時)、**非 Performance%**（無 ideal 分母、不主張口徑）＝可揭露降載盲點的事實。
        #   ⚠ output 與 producing 須同窗 → 只在站頁(_station_overview)顯示（同 days）；勿渲染於跨窗 fleet/rollup 視圖（會扭曲此絕對速率）。
        #   ★ 守衛是 `output is not None` **而不是** truthy：真的 output==0（機台在生產、產出
        #     計數為零）是**有意義的 0.0 速率**，顯「—」等於把「量到了、就是零」說成「量不到」；
        #     只有 output 被扣住（None＝該站無 Qlik 列／grid 不可用＝D1 切換後的新狀態）才回 None。
        "performance": None, "perf_pending": True,
        "actual_rate_uph": (output / (producing / 3600.0)
                            if (output is not None and producing > 0) else None),
        # 全時帳本（machine-seconds；§23-ish 粗 5 桶，加總＝total_cap 或 active）
        "producing_sec": producing, "idle_sec": idle, "down_sec": down,
        "pm_sec": pm, "off_shift_sec": off_shift,
        "active_sec": active, "total_cap_sec": total_cap,
        "composition": composition,   # real＝真工單桶+灰剩餘；demo＝造 5 桶（見上分支）
    }


# 全時帳本粗桶 → 借用 §23 canonical state key（前端 STATE_COLORS 直接上色；非 §23 細態語意）。
COMPOSITION_STATES = [
    ("生產", "producing_sec"), ("待機", "idle_sec"), ("故障停機", "down_sec"),
    ("計畫保養", "pm_sec"), ("非排定", "off_shift_sec"),
]


def _composition(producing, idle, down, pm, off_shift, total_cap, active):
    """24h 絕對組成（加總＝total_cap；fleet 缺 total 時退回 active）。每片 {state,sec,pct}。"""
    vals = {"生產": producing, "待機": idle, "故障停機": down,
            "計畫保養": pm, "非排定": off_shift}
    total = total_cap if total_cap > 0 else (active if active > 0 else 1.0)
    if total_cap <= 0:
        vals.pop("非排定", None)                              # 無全時分母則不畫 off-shift（避免假滿格）
    out = []
    for state, _f in COMPOSITION_STATES:
        sec = vals.get(state, 0.0)
        if sec <= 0:
            continue
        out.append({"state": state, "sec": sec, "pct": sec / total * 100.0})
    return out


def production_model(sb, yb, window_sec, demo=True, wo_down_by_cls=None,
                     qlik_rows=None, qlik_kpis=None, qlik_on=False, rw_by_cls=None):
    """組裝全線 model：逐站 join 稼動(sb)+良率(yb) → stations[] + line rollup + worst[]。
       sb/yb＝board_data.get_scoreboard / get_yield_scoreboard 的 canonical 輸出。
       wo_down_by_cls=None → A↔U 拆分為 DEMO（今行為）；提供 {cls:{down_sec,by_state}}（工單口徑真停機）
       → 該 cls 的 A↔U 拆分改吃真工單（real_split）；線級亦改 24h 基準真拆分。model['real_split'] 標記。
       **qlik_on=False（預設）＝舊行為 byte-identical**。qlik_on=True（D1/D2/D3）：
       每站 output/quality 換 Qlik 口徑（qlik_rows={cls:row}），**線級 Quality＝OVERALL RTY**
       （取代 anchor 站 final_pass）；grid 不可用（qlik_kpis 無 OVERALL）→ q_line None（顯「—」）。

       ★ **rw_by_cls（§6.7；2026-08-25）＝應生產時段軸**：`{cls: {'planned_sec','by_state',…}}`。
         非 None ⇒ **每一站都上這條軸**（`rw_by_cls` 沒有的站補 `planned_sec=0` ⇒ 顯「—」）——
         **刻意不做「有的站新軸、沒有的站舊軸」**：同一張表兩把尺正是本輪要消滅的東西。
         線級亦改 Σ|RW| 基準。`rw_by_cls=None`（預設）＝**逐位元退回今天的行為**。"""
    if not sb or not sb.get("stations"):
        return None
    yby = {s.get("cls"): s for s in (yb.get("stations") if yb else [])}
    anchor_cls = (yb.get("rollup", {}) or {}).get("anchor_cls") if yb else None
    real = wo_down_by_cls is not None
    rw_on = rw_by_cls is not None
    qrows = qlik_rows or {}

    stations = []
    for u in sb["stations"]:
        wo = wo_down_by_cls.get(u.get("cls")) if real else None
        # 新軸開著 ⇒ 缺該站材料的補空殼（planned=0 ⇒「—」，C-RW9 永不外推），**不退回舊軸**。
        rw = (rw_by_cls.get(u.get("cls")) or {"planned_sec": 0.0, "by_state": {}}) if rw_on else None
        stations.append(station_model(u, yby.get(u.get("cls")), window_sec, demo, wo=wo,
                                      qlik_row=qrows.get(u.get("cls")), qlik_on=qlik_on, rw=rw))

    if qlik_on:
        # 線級 Quality＝OVERALL RTY（43 站因子連乘；D3）——不再用 anchor 站 final_pass 代表全線。
        _ov = ((qlik_kpis or {}).get("OVERALL") or {}).get("pct")
        q_line = (_ov / 100.0) if isinstance(_ov, (int, float)) else None
    else:
        anchor = next((s for s in stations if s["cls"] == anchor_cls), None)
        q_line = anchor["quality"] if anchor and anchor["quality"] is not None else None

    # 零工期站（稼動不可測）排除於線級 A/U/OEE/組成 之外（生產 0 但非真「沒運轉」→ 計入會誤拉低線級）。
    # 仍保留於 stations（顯示）與 targeted/worst（Layer-1 output 與段時長無關、誠實）。
    um = [s for s in stations if not s.get("util_unmeasurable")]

    if rw_on:
        # —— §6.7 線級：Σ|RW| 當分母、Σ(**全部計停機工單**∩RW) 當妥善率扣除、Σ生產當分子 ——
        #   （★ 2026-09-04 換白名單＝`SPEC-CAPACITY.md` §3.1；線級跟著站級走、絕不各算各的）
        #   （設備視角機時可跨站相加＝§14.4 無產品去重；口徑與站級**同一支 `rw_split`**）
        #   ⚠ **`|RW|=0` 的站不進線級的任何一項**——不進分母**也不進分子**（C-RW9 的兩面；
        #     只擋分母＝把名冊外機台的生產秒灌進線級 U，那是憑空的加分）。
        um_rw = [s for s in um if (s.get("rw_planned_sec") or 0.0) > 0]
        sum_prod = sum(s["producing_sec"] for s in um_rw)
        sum_planned = sum((s.get("rw_planned_sec") or 0.0) for s in um_rw)
        sum_hd = sum((s.get("rw_avail_down_sec") or 0.0) for s in um_rw)
        rs = rw_split(sum_prod, sum_planned, sum_hd)
        a_line, u_line, util24_line = rs["availability"], rs["utilization"], rs["util_rw"]
        oee_line = (util24_line * (q_line if q_line is not None else 1.0)
                    if util24_line is not None else None)
        comp_tot = {}
        for s in um_rw:
            for c in s["composition"]:
                comp_tot[c["state"]] = comp_tot.get(c["state"], 0.0) + c["sec"]
        ctot = sum(comp_tot.values()) or 1.0
        _order = list(_WO_COMP_ORDER) + [RW_IDLE_STATE]
        line_comp = [{"state": k, "sec": comp_tot[k], "pct": comp_tot[k] / ctot * 100.0}
                     for k in _order if comp_tot.get(k, 0.0) > 0]
    elif real:
        # —— 線級真拆分（24h 基準）：Σ 各站 fleet-hours 當 base、Σ 工單停機、Σ 生產 → real_split ——
        sum_prod = sum(s["producing_sec"] for s in um)
        sum_base = sum(s["total_cap_sec"] for s in um)
        sum_wd = sum((s["wo_down_sec"] or 0.0) for s in um if s.get("wo_down_sec") is not None)
        rs = real_split(sum_prod, 1, sum_base, sum_wd)         # fleet=1、window=sum_base ⇒ base=sum_base
        a_line, u_line, util24_line = rs["availability"], rs["utilization"], rs["util24"]
        oee_line = util24_line * (q_line if q_line is not None else 1.0)
        # 線級全時組成＝Σ 各站真桶（machine-seconds 相加合法），依 _WO_COMP_ORDER+剩餘 排序。
        comp_tot = {}
        for s in um:
            for c in s["composition"]:
                comp_tot[c["state"]] = comp_tot.get(c["state"], 0.0) + c["sec"]
        ctot = sum(comp_tot.values()) or 1.0
        _order = list(_WO_COMP_ORDER) + [REMAINDER_STATE]
        line_comp = [{"state": k, "sec": comp_tot[k], "pct": comp_tot[k] / ctot * 100.0}
                     for k in _order if comp_tot.get(k, 0.0) > 0]
    else:
        # —— 線級：A/U 設備視角可 Σ（§14.4 無去重）；quality 走 anchor（§26.10 不跨站池化）——
        sum_prod = sum(s["producing_sec"] for s in um)
        sum_up = sum(s["producing_sec"] + s["idle_sec"] for s in um)
        sum_planned = sum(s["active_sec"] for s in um)
        a_line = (sum_up / sum_planned) if sum_planned > 0 else 0.0
        u_line = (sum_prod / sum_up) if sum_up > 0 else 0.0
        util24_line = (sum_prod / sum_planned) if sum_planned else 0.0
        oee_line = a_line * u_line * (q_line if q_line is not None else 1.0)
        # 線級全時組成（Σ 各桶；設備視角機時相加合法）
        comp_keys = ["生產", "待機", "故障停機", "計畫保養", "非排定"]
        field = {"生產": "producing_sec", "待機": "idle_sec", "故障停機": "down_sec",
                 "計畫保養": "pm_sec", "非排定": "off_shift_sec"}
        comp_tot = {k: sum(s[field[k]] for s in um) for k in comp_keys}
        ctot = sum(comp_tot.values()) or 1.0
        line_comp = [{"state": k, "sec": comp_tot[k], "pct": comp_tot[k] / ctot * 100.0}
                     for k in comp_keys if comp_tot[k] > 0]

    targeted = [s for s in stations if s["met"] is not None]
    on_target = sum(1 for s in targeted if s["met"])
    worst = sorted([s for s in targeted if not s["met"]], key=lambda s: s["gap"])  # 最負在前
    total_overtime = sum((s["overtime_h"] or 0.0) for s in stations)

    return {
        "stations": stations,
        "anchor_cls": anchor_cls,
        "qlik": bool(qlik_on),                  # True＝站級/線級 output·quality 走 Qlik first-FAIL 口徑
        "qlik_kpis": (qlik_kpis or {}) if qlik_on else {},
        "real_split": real,                     # True＝A↔U 拆分吃真工單（24h 基準）；False＝DEMO 拆分
        "rw_axis": rw_on,                       # True＝三軸分母＝應生產時段（§6.7；顯示層據此換標籤）
        "rw_planned_sec": (sum((s.get("rw_planned_sec") or 0.0) for s in um) if rw_on else None),
        "rw_n_stations": (sum(1 for s in um if (s.get("rw_planned_sec") or 0.0) > 0)
                          if rw_on else None),   # 線級真正有應生產時段的站數（其餘顯「—」）
        "line": {
            "availability": a_line, "utilization": u_line, "quality": q_line,
            "oee": oee_line, "util_real": util24_line,
            "real_split": real, "rw_axis": rw_on,
            "performance": None, "perf_pending": True,   # F3b：線級無速率(output 不可線級 Σ §26.10)；滿載基準待 SME
            "n_stations": len(stations),
            "n_targeted": len(targeted), "n_on_target": on_target,
            "n_behind": len(worst), "total_overtime_h": total_overtime,
            "composition": line_comp,
        },
        "worst": worst,
        "window_sec": window_sec, "demo": demo, "demo_fields": list(DEMO_FIELDS),
    }


def station_detail(u_station, y_rec, tl_machines, window_sec, demo=True, rw=None, rw_by_eid=None):
    """單站總覽 model（一致動線 L1）：站級三軸(reuse station_model)＋最小生產單位(子 EID)清單。
       u_station=稼動 scoreboard 站 rec；y_rec=yield 站 rec(含 machines)；
       tl_machines={eid: timeline agg}(per-machine 稼動，僅已接站如 CAL；其餘站此處 None→子EID 稼動留白)。
       誠實：output/good/util(A×U) 真；A↔U 拆分/quality 口徑＝RAW/demo（同 station_model）。

       ★ **rw／rw_by_eid（§6.7）＝應生產時段軸**。`rw_by_eid={eid:{'planned_sec','by_state'}}`
         非 None ⇒ **每一列都上這條軸**（缺該 EID ⇒ planned=0 ⇒ A/U/OEE 顯「—」＝C-RW9），
         `rnd.uniform` 的 DEMO 拆分整段不執行。⚠ **站級換了、子工位沒換＝站頁內部兩把尺**，
         所以這兩個參數必須同進同出（呼叫端同一輪傳）。兩者皆 None ⇒ 逐位元退回今天的行為。"""
    cls = (u_station or {}).get("cls") or (y_rec or {}).get("cls")
    st = station_model(u_station or {"cls": cls}, y_rec, window_sec, demo, rw=rw)
    tlm = tl_machines or {}
    rw_on = rw_by_eid is not None
    units = []
    for ym in ((y_rec or {}).get("machines") or []):
        eid = ym.get("eid")
        out = ym.get("output_units") or 0
        good = ym.get("good_units") or 0
        q_pct = (good / out * 100.0) if out > 0 else None        # 每機 RAW pass 率(distinct module)
        a = u = util = oee = None
        agg = tlm.get(eid)
        zlo_u = bool(agg and agg.get("zero_len_only"))            # 零工期工位：稼動不可測（同站級）
        if agg and not zlo_u:                                     # 有 timeline → util(A×U) 真
            producing = agg.get("producing_sec") or 0.0
            active = agg.get("total_sec") or 0.0
            if rw_on:                                             # §6.7：應生產時段軸（A/U 皆真）
                _w = rw_by_eid.get(eid) or {"planned_sec": 0.0, "by_state": {}}
                rs = rw_split(producing, _w.get("planned_sec"),
                              rw_avail_down_sec(_w.get("by_state")))   # ★ 2026-09-04 同站級
                a, u, util = rs["availability"], rs["utilization"], rs["util_rw"]
                if util is not None and q_pct is not None:
                    oee = util * (q_pct / 100.0)
            else:                                                 # 舊行為：A/U 拆分 demo（不變）
                util = (producing / active) if active > 0 else 0.0
                nonp = max(0.0, active - producing)
                rnd = random.Random(_seed(eid))
                down = nonp * (rnd.uniform(0.15, 0.38) if demo else 0.0)
                pm = nonp * (rnd.uniform(0.08, 0.20) if demo else 0.0)
                up = producing + max(0.0, nonp - down - pm)
                a = (up / active) if active > 0 else 0.0
                u = (producing / up) if up > 0 else 0.0
                if q_pct is not None:
                    oee = a * u * (q_pct / 100.0)
        # ★ 2026-09-04（`SPEC-CAPACITY.md` §2.7）：**每台一條同軸的橫 bar**。
        #   材料＝與站級頭卡**同一支** `_composition_rw`（生產／cmms 逐態／該跑沒跑），
        #   呼叫端用 `rw_color()` 把 cmms 各態總包成單一停機色 ⇒ 三態、與機隊同色同圖例。
        #   ⚠ 沒有 rw 材料（舊軸／名冊外／零工期）⇒ `comp = []`＝**整格空白**，
        #     不畫灰條——灰條會被讀成「有量到、全是未知」。
        _comp = []
        if rw_on and agg and not zlo_u:
            _w = rw_by_eid.get(eid) or {}
            if (_w.get("planned_sec") or 0) > 0:
                _comp = _composition_rw(agg.get("producing_sec") or 0.0,
                                        _w.get("by_state") or {}, _w.get("planned_sec"))
        units.append({
            "comp": _comp,
            "eid": eid, "desc": ym.get("desc", ""),
            "output": out, "good": good, "fail_pct": ym.get("fail_pct_raw"),
            "quality_pct": q_pct, "availability": a, "utilization": u, "util": util,
            "oee": oee, "has_util": agg is not None and not zlo_u,
            "util_unmeasurable": zlo_u,                 # 零工期工位：稼動顯「—」+不可測 tooltip（≠沒訊號）
            "rw_axis": rw_on,                           # §6.7：這一列的三軸分母＝應生產時段
            "rw_planned_sec": ((rw_by_eid.get(eid) or {}).get("planned_sec") if rw_on else None),
            "parent_eid": ym.get("parent_eid"),        # 子工位列→父機（縮排/群組；covers CAL+CLN、live 與 demo 皆標）
        })
    return {"cls": cls, "station": st, "units": units, "rw_axis": rw_on,
            "has_machine_util": any(x["has_util"] for x in units), "demo": demo}


# ═══ selftest（純函式、零 I/O、零 MES；注入 fake sb/yb/wo_down）═══════════════════════
def _selftest():
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    W = 86400.0                                             # 1 天窗（秒）

    # ---------- (A) real_split 恆等 A×U == producing/base ----------
    for (prod, fleet, wd) in [(43200, 2, 3600), (10000, 1, 0), (50000, 3, 100000),
                              (0, 2, 0), (86400, 1, 43200)]:
        rs = real_split(prod, fleet, W, wd)
        base = fleet * W
        wd_c = min(wd, base)
        ck(abs(rs["util24"] - prod / base) < 1e-9, f"util24＝producing/base ({prod},{fleet},{wd})")
        ck(abs(rs["availability"] - (1.0 - wd_c / base)) < 1e-9, "A＝1−wd/base")
        if rs["utilization"] is not None:
            ck(abs(rs["availability"] * rs["utilization"] - rs["util24"]) < 1e-9,
               f"恆等 A×U==util24 ({prod},{fleet},{wd})")

    # ---------- (B) 除零/clamp 守恆 ----------
    rs0 = real_split(1000, 0, W, 0)                         # fleet=0 → base=0
    ck(rs0["util24"] == 0.0 and rs0["availability"] == 0.0 and rs0["utilization"] is None,
       "base=0 → 全 0 / U None")
    rsf = real_split(1000, 1, W, W)                         # wd==base（全停）
    ck(rsf["availability"] == 0.0 and rsf["utilization"] is None, "wd≥base → A=0、U None")
    rsc = real_split(1000, 1, W, W * 5)                     # wd>base → clamp
    ck(rsc["wo_down_sec"] == W and rsc["availability"] == 0.0, "wd>base → clamp 到 base、A=0")

    # ---------- (C) rollup_downtime_by_cls：per-EID → per-cls（並列產能＝相加）----------
    dbe = {"EID-1": {"down_sec": 3600.0, "by_state": {"維修": 3600.0}},
           "EID-2": {"down_sec": 7200.0, "by_state": {"計畫保養": 7200.0}},
           "EID-9": {"down_sec": 999.0, "by_state": {"維修": 999.0}}}     # 不在任何 fleet
    fbc = {"segA": [("EID-1", "d1"), ("EID-2", "d2")], "segB": [("EID-3", "d3")]}
    roll = rollup_downtime_by_cls(dbe, fbc)
    ck(abs(roll["segA"]["down_sec"] - 10800.0) < 1e-9, "segA 停機＝Σ 兩機（相加非 union）")
    ck(roll["segA"]["by_state"]["維修"] == 3600.0 and roll["segA"]["by_state"]["計畫保養"] == 7200.0,
       "segA by_state 合併")
    ck(roll["segB"]["down_sec"] == 0.0, "segB 無工單 → 0（誠實工單口徑）")
    ck("EID-9" not in str(roll) or roll.get("segA", {}).get("down_sec") == 10800.0,
       "不在 fleet 的 EID-9 不進任何站（無 double-attribution）")

    # ---------- (D) station_model：real 分支 A×U×Q == oee，composition 加總＝total_cap ----------
    u = {"cls": "segX", "fleet_size": 2, "producing_sec": 0.5 * 2 * W,   # 50% util24
         "active_span_sec": 1.2 * W}
    y = {"cls": "segX", "final_pass_pct": 90.0, "output_units": 500}
    wo = {"down_sec": 0.1 * 2 * W, "by_state": {"維修": 0.06 * 2 * W, "計畫保養": 0.04 * 2 * W}}
    sm = station_model(u, y, W, demo=True, wo=wo)
    ck(sm["real_split"] is True, "wo 提供 → real_split True")
    base = 2 * W
    ck(abs(sm["availability"] - (1 - (0.1 * base) / base)) < 1e-9, "站 A＝1−wd/base（0.9）")
    ck(abs(sm["oee"] - (0.5 * 0.90)) < 1e-9, "站 oee＝util24×Q（0.5×0.9）")
    ck(sm["utilization"] is not None
       and abs(sm["availability"] * sm["utilization"] * sm["quality"] - sm["oee"]) < 1e-9,
       "站恆等 A×U×Q==oee")
    csum = sum(c["sec"] for c in sm["composition"])
    ck(abs(csum - base) < 1.0, f"real composition 加總＝total_cap，got {csum} vs {base}")
    states = {c["state"] for c in sm["composition"]}
    ck("生產" in states and "維修" in states and "計畫保養" in states and REMAINDER_STATE in states,
       "real composition 含 生產/維修/計畫保養/未拆分 桶")
    # 2026-08-16 統一語言：等料/等外包直通自己的桶；legacy（調機/暫桶）→ 舊制工單灰桶。
    wo_u = {"down_sec": 0.2 * 2 * W,
            "by_state": {"等料(停機)": 0.1 * 2 * W, "調機": 0.05 * 2 * W,
                         "其他維護(子類待定)": 0.05 * 2 * W}}
    sm_u = station_model(u, y, W, demo=True, wo=wo_u)
    st_u = {c["state"]: c["sec"] for c in sm_u["composition"]}
    ck("等料(停機)" in st_u, f"等料(停機) 直通自己的桶（不收斂）: {sorted(st_u)}")
    ck(LEGACY_WO_STATE in st_u and "調機" not in st_u and "其他維護" not in st_u
       and "其他維護(子類待定)" not in st_u,
       f"legacy 態收進『{LEGACY_WO_STATE}』單一灰桶、不再以 調機/其他維護 出現: {sorted(st_u)}")
    ck(abs(st_u[LEGACY_WO_STATE] - 2 * st_u["等料(停機)"] * 0.5) < 1e-6
       or abs(st_u[LEGACY_WO_STATE] - st_u["等料(停機)"]) < 1e-6,
       f"legacy 桶＝調機+暫桶合計（與等料等量）: {st_u}")
    # 剩餘桶＝idle/off-shift（不再造 demo down/pm）
    ck(sm["down_sec"] == 0.0 and sm["pm_sec"] == 0.0, "real 分支：demo down/pm 欄位歸零")

    # ---------- (E) station_model：demo 分支不變（wo=None）＝今行為 ----------
    sm_d = station_model(u, y, W, demo=True)                # 同輸入、無 wo
    ck(sm_d["real_split"] is False, "wo=None → real_split False（demo 拆分）")
    ck(abs(sm_d["util_real"] - (u["producing_sec"] / u["active_span_sec"])) < 1e-9,
       "demo util_real＝producing/active（今口徑不變）")
    ck(sm_d["down_sec"] > 0 and sm_d["pm_sec"] > 0, "demo 分支：造 down/pm 拆分（不變）")

    # ---------- (F) production_model：demo（wo_down_by_cls=None）vs real ----------
    sb = {"stations": [
        {"cls": "segX", "fleet_size": 2, "producing_sec": 0.5 * 2 * W, "active_span_sec": 1.2 * W},
        {"cls": "segY", "fleet_size": 1, "producing_sec": 0.8 * W, "active_span_sec": 0.9 * W}]}
    yb = {"stations": [{"cls": "segX", "final_pass_pct": 90.0, "output_units": 500}],
          "rollup": {"anchor_cls": "segX"}}
    m_demo = production_model(sb, yb, W, demo=True)
    ck(m_demo["real_split"] is False and m_demo["line"]["real_split"] is False, "demo model：real_split False")

    wo_by_cls = {"segX": {"down_sec": 0.1 * 2 * W, "by_state": {"維修": 0.2 * W, "計畫保養": 0.16 * W * 0.5}},
                 "segY": {"down_sec": 0.0, "by_state": {}}}
    m_real = production_model(sb, yb, W, demo=True, wo_down_by_cls=wo_by_cls)
    ck(m_real["real_split"] is True and m_real["line"]["real_split"] is True, "real model：real_split True")
    L = m_real["line"]
    sum_prod = 0.5 * 2 * W + 0.8 * W
    sum_base = 2 * W + 1 * W
    ck(abs(L["util_real"] - sum_prod / sum_base) < 1e-9, "線 util24＝Σprod/Σbase")
    if L["utilization"] is not None:
        ck(abs(L["availability"] * L["utilization"] - L["util_real"]) < 1e-9, "線恆等 A×U==util24")
    ck(abs(L["oee"] - L["util_real"] * 0.90) < 1e-9, "線 oee＝util24×Q_anchor")
    # segY 無工單 → A=100%（誠實工單口徑）
    sy = next(s for s in m_real["stations"] if s["cls"] == "segY")
    ck(abs(sy["availability"] - 1.0) < 1e-9, "無工單站 A＝100%（工單口徑誠實）")
    ck(sy["real_split"] is True, "wo_down_by_cls 有該站(down=0) → 仍走 real")

    # ---------- (G) 部分 cls 有 wo、部分無 → 混合（無 wo 的站退回 demo）----------
    m_mixed = production_model(sb, yb, W, demo=True, wo_down_by_cls={"segX": wo_by_cls["segX"]})
    sx = next(s for s in m_mixed["stations"] if s["cls"] == "segX")
    sy2 = next(s for s in m_mixed["stations"] if s["cls"] == "segY")
    ck(sx["real_split"] is True and sy2["real_split"] is False,
       "混合：有 wo 的站 real、無 wo 的站 demo（graceful）")

    # ---------- (H) 零工期站（util_unmeasurable）：A/U/OEE None、不入線級、Layer-1 保留 ----------
    uz = {"cls": "segZeroLen", "fleet_size": 2, "producing_sec": 0.0,
          "active_span_sec": 0.0, "n_seg": 40, "zero_len_only": True}
    yz = {"cls": "segZeroLen", "output_units": 300, "has_yield_signal": False}
    sz = station_model(uz, yz, W, demo=True)
    ck(sz["util_unmeasurable"] is True, "零工期站 util_unmeasurable True")
    ck(sz["availability"] is None and sz["utilization"] is None and sz["oee"] is None
       and sz["util_real"] is None, "零工期站 A/U/OEE/util_real 全 None（不顯 0）")
    ck(sz["composition"] == [] and sz["cause"] is None, "零工期站 組成/歸因撤（無可信基底）")
    ck(sz["output"] == 300, "零工期站 output 保留（Layer-1 與段時長無關）")
    # 零工期站 active=0 仍須 has_output（走 lot count、與 active 無關）＋ demo target/met 照常
    ck(sz["has_output"] is True, "零工期站 active=0+output>0 → has_output True（不被藏）")
    ck(sz["target"] is not None and sz["met"] is not None,
       "零工期站 demo target/met 有值（產出達標判定照常）")
    # 加班無法估（producing=0→無速率）→ overtime_h/coverage 留 None（非 0）
    ck(sz["overtime_h"] is None and sz["coverage"] is None,
       "零工期站 加班/覆蓋留 None（producing=0 無法估速率、非 0）")
    ck(station_model(u, y, W, demo=True).get("util_unmeasurable") is False,
       "正常站 util_unmeasurable False（加性、不影響既有）")
    # production_model：零工期站排除於線級（不拉低 A/U/OEE），仍在 stations 顯示
    sb_z = {"stations": [
        {"cls": "segX", "fleet_size": 2, "producing_sec": 0.5 * 2 * W, "active_span_sec": 1.2 * W},
        {"cls": "segZeroLen", "fleet_size": 2, "producing_sec": 0.0, "active_span_sec": 0.0,
         "n_seg": 40, "zero_len_only": True}]}
    yb_z = {"stations": [{"cls": "segX", "final_pass_pct": 90.0, "output_units": 500}],
            "rollup": {"anchor_cls": "segX"}}
    mz = production_model(sb_z, yb_z, W, demo=True)
    ck(len(mz["stations"]) == 2, "零工期站仍在 stations（顯示）")
    # demo 線級 util_real＝Σprod/Σactive；只計 segX（prod=W、active=1.2W）＝0.8333，零工期站不入分母
    ck(abs(mz["line"]["util_real"] - (1.0 / 1.2)) < 1e-9,
       "線級 util 只計可測站（segX prod/active，零工期站不拉低）")
    ck(mz["line"]["availability"] is not None, "線級 A 非 None（零工期站已排除）")
    # station_detail：零工期工位 util 顯 None + util_unmeasurable、Layer-1 output 保留
    yrec = {"cls": "segZeroLen", "machines": [{"eid": "EID-1", "output_units": 100, "good_units": 95}]}
    tlm = {"EID-1": {"producing_sec": 0.0, "total_sec": 0.0, "zero_len_only": True}}
    dz = station_detail({"cls": "segZeroLen"}, yrec, tlm, W, demo=True)
    u0 = dz["units"][0]
    ck(u0["util_unmeasurable"] is True and u0["has_util"] is False
       and u0["availability"] is None and u0["oee"] is None, "零工期工位 util None + 不可測旗")
    ck(u0["output"] == 100, "零工期工位 output 保留")

    # ---------- (I) Qlik 口徑換源（D1/D2/D3）：additive、預設路徑 byte-identical ----------
    qrow = {"ord": 32, "cls": "segX", "mcl": "assyUnit", "total": 696,
            "pass": 690, "fail": 6, "final_yield_pct": 99.1379310344,
            "fpy_pct": 98.4195, "n_endfail": 8, "absorbed_elsewhere": 2}
    s_raw = station_model(u, y, W, demo=True)                       # 預設＝raw（舊行為）
    ck(s_raw["qlik"] is False and s_raw["output"] == 500 and s_raw["quality_src"] == "final",
       "qlik_on 預設 False → output/quality 仍走 raw（舊行為不變）")
    ck(s_raw["output_withheld"] is False, "raw 路徑 output_withheld 恆 False")
    s_qk = station_model(u, y, W, demo=True, qlik_row=qrow, qlik_on=True)
    ck(s_qk["output"] == 696 and s_qk["qlik"] is True, "qlik_on → output＝Qlik TOTAL（非 raw 500）")
    ck(s_qk["quality_src"] == "qlik_final"
       and abs(s_qk["quality_pct"] - qrow["final_yield_pct"]) < 1e-9,
       "qlik_on → quality＝Qlik final_yield（first-FAIL 語意）")
    ck(abs(s_qk["oee"] - s_qk["util_real"] * s_qk["quality"]) < 1e-9, "qlik 站 oee 恆等 util×Q")
    s_miss = station_model(u, y, W, demo=True, qlik_row=None, qlik_on=True)
    ck(s_miss["output"] is None and s_miss["output_withheld"] is True
       and s_miss["has_output"] is False and s_miss["target"] is None,
       "qlik_on + 無對映列 → output/target 誠實留白（None、非 0、非 raw 頂替）")
    ck(s_miss["quality_pct"] is None and s_miss["actual_rate_uph"] is None,
       "qlik_on + 無對映列 → quality/速率 None（不回退 raw）")
    # actual_rate_uph 的 0 vs None 分野（誠實邊界）：真的 0 產出 ≠ 量不到
    q0 = dict(qrow, total=0, **{"pass": 0}, fail=0, final_yield_pct=None)
    s_zero = station_model(u, y, W, demo=True, qlik_row=q0, qlik_on=True)
    ck(s_zero["output"] == 0 and s_zero["actual_rate_uph"] == 0.0,
       "output==0 且有生產秒 → 速率 0.0（量到了就是零，不得顯「—」）")
    ck(s_miss["output"] is None and s_miss["actual_rate_uph"] is None,
       "output 被扣住(None) → 速率 None（這才是「量不到」）")
    kp = {"OVERALL": {"pct": 77.713, "n_stations": 43, "n_empty": 1, "n_allfail": 0},
          "PH": {"pct": 92.21}, "PC": {"pct": 84.279}, "PPC": {"pct": 100.0}}
    m_qk = production_model(sb, yb, W, demo=True, qlik_rows={"segX": qrow},
                            qlik_kpis=kp, qlik_on=True)
    ck(m_qk["qlik"] is True and abs(m_qk["line"]["quality"] - 0.77713) < 1e-9,
       "線級 Quality＝OVERALL RTY（非 anchor 站 final_pass）")
    ck(abs(m_qk["line"]["oee"] - m_qk["line"]["util_real"] * 0.77713) < 1e-9,
       "線級 oee＝util24×OVERALL")
    sx_qk = next(s for s in m_qk["stations"] if s["cls"] == "segX")
    sy_qk = next(s for s in m_qk["stations"] if s["cls"] == "segY")
    ck(sx_qk["output"] == 696 and sy_qk["output"] is None and sy_qk["output_withheld"] is True,
       "production_model qlik：有列站換 Qlik、無列站誠實留白")
    m_qk_bad = production_model(sb, yb, W, demo=True, qlik_rows={}, qlik_kpis={}, qlik_on=True)
    ck(m_qk_bad["line"]["quality"] is None,
       "grid 不可用（無 OVERALL）→ 線級 Quality None（絕不 100%、絕不回退 raw）")
    ck(production_model(sb, yb, W, demo=True)["qlik"] is False,
       "production_model 預設 qlik False（舊行為 byte-identical）")

    # ---------- (J) §6.7 應生產時段軸（rw=）：additive、rw 缺＝byte-identical ----------
    #   規格＝`SPEC-RUNWINDOW.md` §6.7。⚠ 本模組**不算該跑窗**：rw 是呼叫端（app）用
    #   `wem.erw_machine_windows` ＋ `wo_segments_scoped` 裁好餵進來的（§6.7 反模式 2）。
    # (J1) rw_split 恆等與除零守恆
    for (prod, planned, hd) in [(0.6 * W, W, 0.1 * W), (0.0, W, 0.0), (0.5 * W, 2 * W, 0.0),
                                (0.3 * W, W, W), (0.2 * W, W, 5 * W)]:
        rs = rw_split(prod, planned, hd)
        hd_c = min(hd, planned)
        ck(abs(rs["health_down_sec"] - hd_c) < 1e-9, f"rw 停機 clamp 到 |RW| ({hd},{planned})")
        ck(abs(rs["availability"] - (1.0 - hd_c / planned)) < 1e-9, "rw A＝up/|RW|")
        if rs["utilization"] is not None:
            ck(abs(rs["availability"] * rs["utilization"] - rs["util_rw"]) < 1e-9,
               f"rw 恆等 A×U==util_rw ({prod},{planned},{hd})")
            ck(abs(rs["util_rw"] - prod / planned) < 1e-9, "rw util_rw＝生產/|RW|")
    ck("util24" not in rw_split(1.0, W, 0.0),
       "rw_split **沒有** util24 鍵（舊名裝新定義是禁區＝A7／C-RW16 同一條紀律）")
    rz = rw_split(1000.0, 0.0, 0.0)
    ck(rz["availability"] is None and rz["utilization"] is None and rz["util_rw"] is None,
       "|RW|=0 ⇒ A/U/util_rw 全 None（C-RW10：永不 0%／100%）")
    rfull = rw_split(1000.0, W, W)
    ck(rfull["availability"] == 0.0 and rfull["utilization"] is None,
       "維修+保養蓋滿 ⇒ A=0、U None（除零守恆）")
    rover = rw_split(1.5 * W, W, 0.0)
    ck(rover["utilization"] is None and rover["util_rw"] is None,
       "生產 > 應生產時段 ⇒ U/util_rw None（矛盾不 clamp、不夾成 100%）")
    # (J2) rw_health_down_sec 是**白名單**，不是「其餘皆停機」
    _bs = {"維修": 100.0, "計畫保養": 50.0, "等料(停機)": 999.0, "等外包(停機)": 999.0,
           "調機": 999.0, "不明": 999.0, "待機": 999.0, RW_IDLE_STATE: 999.0, "生產": 999.0}
    ck(abs(rw_health_down_sec(_bs) - 150.0) < 1e-9,
       f"窄稼動只扣 維修+計畫保養（等料/等外包/legacy/不明 走 U 那側）: {rw_health_down_sec(_bs)}")
    ck(RW_HEALTH_STATES == frozenset({"維修", "計畫保養"}),
       "RW_HEALTH_STATES 是白名單（改成「其餘皆停機」會把『不知道』算成『壞了』）")
    # (J2b) ★ 2026-09-04：三軸的 A 改吃**妥善率白名單**（`SPEC-CAPACITY.md` §3.1）
    ck(abs(rw_avail_down_sec(_bs) - (100.0 + 50.0 + 999.0 * 3)) < 1e-9,
       f"妥善率扣 維修+計畫保養+等料+等外包+legacy(調機): {rw_avail_down_sec(_bs)}")
    for _nd in ("不明", "待機", "流程等待", "非排定", "生產", RW_IDLE_STATE):
        ck(_nd not in RW_AVAIL_DOWN_STATES,
           f"『{_nd}』**不得**進妥善率白名單——把「不知道」畫成「壞了」是最貴的一種謊")
        ck(rw_avail_down_sec({_nd: 999.0}) == 0.0,
           f"只餵『{_nd}』⇒ 扣 0 秒（白名單、不是「其餘皆停機」）")
    ck(RW_AVAIL_DOWN_STATES >= RW_HEALTH_STATES and RW_AVAIL_DOWN_STATES != RW_HEALTH_STATES,
       "新白名單是舊窄稼動白名單的**嚴格超集**（A 只會變低、不會變高）")
    ck("其他維護(子類待定)" in RW_AVAIL_DOWN_STATES and LEGACY_WO_STATE in RW_AVAIL_DOWN_STATES,
       "eMaint legacy 兩種形（w8 暫桶字串／本檔收斂桶）都算停機")
    # (J2c) ★ 換白名單的**代數後果**：A 變低、U 變高、**A×U 與 OEE 一個位元不動**
    #   （`SPEC-CAPACITY.md` §3.2；這是「口徑統一不會改變機隊表現」的證明，也是
    #    「不必回補歷史 OEE」的依據——會動的只有 A 與 U 之間那一刀）。
    _P, _G = 100.0, 40.0
    _bs2 = {"維修": 10.0, "計畫保養": 5.0, "等料(停機)": 8.0, "調機": 2.0}
    _old = rw_split(_G, _P, rw_health_down_sec(_bs2))
    _new = rw_split(_G, _P, rw_avail_down_sec(_bs2))
    ck(_new["availability"] < _old["availability"] and _new["utilization"] > _old["utilization"],
       f"換尺 ⇒ A↓ U↑: A {_old['availability']}→{_new['availability']}")
    ck(abs(_new["util_rw"] - _old["util_rw"]) < 1e-12
       and abs(_new["util_rw"] - _G / _P) < 1e-12,
       f"換尺 ⇒ A×U 恆等不動（≡生產/planned）: {_old['util_rw']} vs {_new['util_rw']}")
    # (J2d) ★ 產能瀑布（`SPEC-CAPACITY.md` §3.4；C-CP2／C-CP3／C-CP4）
    _H = 3600.0
    #   固定例：planned 100h／停機 20h／生產 60h／r=6 ⇒ 理論 600、三層 120/120/110
    #   ⇒ 殘差 250；實測良品 200 ⇒ 重工當量 50（帳上估 30 ⇒ 差額＝速率殘差、chip 會亮）。
    _w = capacity_waterfall(100 * _H, 20 * _H, 60 * _H, rate_ph_per_h=6.0,
                            qlik_fail=110, qlik_pass=200, rework_equiv_h=5.0,
                            rework_pct=4.0)
    ck(_w["theory_ph"] - _w["down_ph"] - _w["idle_ph"] - _w["fail_ph"]
       == _w["good_ph"] + _w["equiv_ph"],
       "★ C-CP3 恆等式（2026-09-04 改形）：理論 − 停機 − 該跑沒跑 − 不良 "
       "＝ **實測良品 ＋ 重工當量**（取整後也成立）")
    ck(_w["good_ph"] == 200,
       "★ 最底列的主字就是**量到的** qlik_pass——老闆會拿它去對產出頁與月報，"
       "印一個比實測高的殘差等於給他一個對不上的頭數字（Fable 2026-09-04 自糾）")
    ck(_w["equiv_ph"] == _w["residual_ph"] - _w["good_ph"],
       "重工當量＝殘差 − 實測（＝r·rework_h ＋ 速率殘差；主項是重工）")
    ck(_w["down_ph"] + _w["idle_ph"] + _w["residual_ph"] + _w["fail_ph"] == _w["theory_ph"],
       "三層 ＋ 生產段 ≡ 理論（`_round_to_sum` 分配、無捨入破口）")
    ck(abs(_w["planned_h"] - 100.0) < 1e-9 and abs(_w["down_h"] - 20.0) < 1e-9
       and abs(_w["idle_h"] - 20.0) < 1e-9 and abs(_w["producing_h"] - 60.0) < 1e-9,
       "小時四欄＝planned／down／idle／producing（idle 是殘量）")
    ck(_w["rework_ledger_ph"] == 30 and _w["rework_share"] is not None,
       "★ C-CP4：帳上的重工估計另給一份（tooltip 要拿它跟 `equiv_ph` 對照，"
       "差太多代表當量裡有速率殘差、不全是重工）")
    _wneg = capacity_waterfall(100 * _H, 20 * _H, 60 * _H, rate_ph_per_h=6.0,
                               qlik_fail=10, qlik_pass=400)
    ck(_wneg["equiv_ph"] < 0 and _wneg["rework_share"] is None,
       "★ 當量為負（實際比當月中位速率快）**不是重工** ⇒ 不畫斜紋，顯示層改標速率殘差")
    ck(_wneg["theory_ph"] - _wneg["down_ph"] - _wneg["idle_ph"] - _wneg["fail_ph"]
       == _wneg["good_ph"] + _wneg["equiv_ph"],
       "★ 當量為負時恆等式照樣成立（它是一個加項，不是一個下限）")
    # 溢出：生產 ＋ 停機 > |RW| ⇒ **吃生產、停機不縮**（與 `_composition_rw` 同一套分配）
    _wo = capacity_waterfall(100 * _H, 40 * _H, 90 * _H, rate_ph_per_h=1.0, qlik_fail=0,
                             qlik_pass=1)
    ck(abs(_wo["down_h"] - 40.0) < 1e-9 and abs(_wo["producing_h"] - 60.0) < 1e-9
       and _wo["idle_h"] == 0.0,
       "★ 溢出吃「生產」、停機桶永不縮（與組成條同一套分配，兩者長度才對得上）")
    # 無尺 ⇒ 只回小時、PH 全 None（C-CP2：絕不外推）
    _wn = capacity_waterfall(100 * _H, 20 * _H, 60 * _H)
    ck(_wn["ph_ok"] is False and _wn["theory_ph"] is None and _wn["good_ph"] is None
       and _wn["equiv_ph"] is None and abs(_wn["planned_h"] - 100.0) < 1e-9,
       "★ C-CP2 無尺 ⇒ PH 全 None、小時照給（絕不外推一個「大概的速率」）")
    ck(capacity_waterfall(100 * _H, 0, 0, rate_ph_per_h=5.0, qlik_fail=None)["ph_ok"] is False
       and capacity_waterfall(100 * _H, 0, 0, rate_ph_per_h=5.0, qlik_fail=1,
                              qlik_pass=None)["ph_ok"] is False,
       "★ 不良**與良品**兩個計數缺一就不換算（主字是實測良品，沒有它就沒有可印的頭數字）")
    ck(capacity_waterfall(0, 0, 0) is None and capacity_waterfall(-5, 0, 0) is None,
       "★ C-RW10：沒有應生產時段 ⇒ 回 None（不畫一張全 0 的圖）")
    # 良品為負＝真矛盾，**不夾成 0**（同 U>1 不 clamp 的紀律）
    _wc = capacity_waterfall(10 * _H, 0, 10 * _H, rate_ph_per_h=1.0, qlik_fail=999,
                             qlik_pass=1)
    ck(_wc["residual_ph"] < 0 and _wc["contradiction"] is True,
       "★ 量到的不良 > 估出來的產出 ⇒ 殘差為負＋contradiction 旗（夾成 0 就藏起來了）")
    ck(RW_IDLE_STATE == "該跑沒跑" and RW_IDLE_STATE != REMAINDER_STATE,
       "rw 殘量桶＝『該跑沒跑』（一桶一名，§6.6-b），不是『未拆分』也不是『不明』")
    # (J3) station_model：rw 缺 ⇒ **逐鍵 byte-identical**（demo／snapshot 恆走這條＝QG-4.2）
    #   ⚠ **2026-08-25 對抗式驗收改寫**：原本這裡只比「傳 rw=None」vs「不傳」——那是**同一條
    #     呼叫路徑**，任何把舊分支改壞的變異都殺不掉（M13／M14 就是這樣活下來的）。
    #     ⇒ 改成**確定性 golden**：seeded demo 站的完整 model dict 逐鍵釘死。
    #     動到這組數字＝舊路徑真的被改了，**先確認那是本意**再更新 golden。
    _gu = {"cls": "segGolden", "fleet_size": 2, "producing_sec": 0.5 * 2 * W,
           "active_span_sec": 1.2 * W}
    _gy = {"cls": "segGolden", "final_pass_pct": 90.0, "output_units": 500}
    _g = station_model(_gu, _gy, W, demo=True)
    _GOLD = {"availability": 0.9297498234760289, "utilization": 0.8962984582430723,
             "oee": 0.75, "util_real": 0.8333333333333334,
             "down_sec": 4985.159633435395, "pm_sec": 2298.378668569924,
             "idle_sec": 9996.46169799468, "off_shift_sec": 69120.0,
             "target": 414, "gap": 86, "met": True, "cause": None,
             "real_split": False, "rw_axis": False, "rw_planned_sec": None}
    _bad = {k: (_g[k], v) for k, v in _GOLD.items() if _g[k] != v}
    ck(not _bad, f"舊路徑（rw 缺）golden 逐鍵不動——差異: {_bad}")
    ck(abs(sum(c["sec"] for c in _g["composition"]) - _gu["fleet_size"] * W) < 1e-6
       and [c["state"] for c in _g["composition"]]
       == ["生產", "待機", "故障停機", "計畫保養", "非排定"],
       f"舊路徑 composition golden（5 桶、Σ＝total_cap）: {[c['state'] for c in _g['composition']]}")
    _none = station_model(_gu, _gy, W, demo=True, rw=None)
    ck(_g == _none, "station_model(rw=None) 與不傳 rw 逐鍵相同（換軸 additive、舊路徑零污染）")
    ck(_g["rw_axis"] is False and _g["rw_planned_sec"] is None,
       "舊路徑 rw_axis False／rw_planned_sec None")
    # (J4) station_model rw 分支：恆等 A×U×Q==oee、組成加總＝|RW|、demo 欄位歸零
    _rw = {"planned_sec": 1.6 * W, "by_state": {"維修": 0.1 * W, "計畫保養": 0.06 * W,
                                                "等料(停機)": 0.04 * W}}
    s_rw = station_model(u, y, W, demo=True, rw=_rw)
    ck(s_rw["rw_axis"] is True and abs(s_rw["rw_planned_sec"] - 1.6 * W) < 1e-9,
       "rw 分支：rw_axis True、rw_planned_sec＝|RW|")
    ck(abs(s_rw["rw_avail_down_sec"] - 0.20 * W) < 1e-9,
       "rw 分支：妥善率扣除＝維修+計畫保養+等料（★ 2026-09-04 換尺，等料現在也算停機）")
    ck("rw_health_down_sec" not in s_rw,
       "舊鍵 `rw_health_down_sec` 已退場（舊名裝新定義是禁區＝§6.7-2／C-CP8）")
    ck(abs(s_rw["availability"] - (1.0 - 0.20 / 1.6)) < 1e-9,
       "rw 站 A＝1−(全部計停機工單)/|RW|")
    ck(abs(s_rw["availability"] * s_rw["utilization"] * s_rw["quality"] - s_rw["oee"]) < 1e-9,
       "rw 站恆等 A×U×Q==oee")
    ck(abs(s_rw["util_real"] - s_rw["producing_sec"] / (1.6 * W)) < 1e-9,
       "rw 站 util_real＝生產/|RW|（＝A×U）")
    _csum = sum(c["sec"] for c in s_rw["composition"])
    ck(abs(_csum - 1.6 * W) < 1.0, f"rw 組成加總＝|RW|（不是 24h 全時），got {_csum}")
    _cst = {c["state"] for c in s_rw["composition"]}
    ck("生產" in _cst and "維修" in _cst and "等料(停機)" in _cst and RW_IDLE_STATE in _cst
       and REMAINDER_STATE not in _cst and "非排定" not in _cst,
       f"rw 組成＝生產+cmms 各態+該跑沒跑（無『未拆分』『非排定』）: {sorted(_cst)}")
    ck(s_rw["down_sec"] == 0.0 and s_rw["pm_sec"] == 0.0 and s_rw["idle_sec"] == 0.0
       and s_rw["off_shift_sec"] == 0.0, "rw 分支：demo down/pm/idle/off-shift 欄位歸零")
    # (J4b) ★ 溢出吃「生產」、**停機桶永不縮**（2026-08-25 對抗式驗收裁決；見 `_composition_rw`）
    #   驗收給的兩個極端例——舊的「等比縮停機」寫法在這兩例都會讓條上的維修變短，
    #   而卡片上的 A 用的是**沒縮的** 50/100 ⇒ bar 與 A 當場打臉。
    _c1 = {c["state"]: c["sec"] for c in _composition_rw(90.0, {"維修": 50.0}, 100.0)}
    ck(abs(_c1["維修"] - 50.0) < 1e-9, f"溢出時維修**完整保留** 50（不縮）: {_c1}")
    ck(abs(_c1["生產"] - 50.0) < 1e-9, f"溢出由生產吸收（90 → 50）: {_c1}")
    ck(abs(sum(_c1.values()) - 100.0) < 1e-9, f"恆等 Σ≡planned: {_c1}")
    _c2 = {c["state"]: c["sec"] for c in _composition_rw(100.0, {"維修": 100.0}, 100.0)}
    _s2 = station_model({"cls": "segFull", "fleet_size": 1, "producing_sec": 100.0,
                         "active_span_sec": 100.0}, None, 100.0, demo=True,
                        rw={"planned_sec": 100.0, "by_state": {"維修": 100.0}})
    ck(abs(_c2["維修"] - 100.0) < 1e-9 and "生產" not in _c2,
       f"整段維修：條上維修不消失、生產被吃光: {_c2}")
    ck(_s2["availability"] == 0.0 and _s2["utilization"] is None,
       f"同一輸入下卡片 A=0%、U「—」（bar 與 A 同源不打臉）: "
       f"A={_s2['availability']} U={_s2['utilization']}")
    ck(abs(sum(_c2.values()) - 100.0) < 1e-9, f"恆等 Σ≡planned: {_c2}")
    # (J4c) ★ 兩個「正確但零覆蓋」的守衛（2026-08-25 驗收 M7／M12 重注入必須轉紅）
    #   M7＝零工期站在新軸上仍須全 None（`zlo` 短路不得被 rw 分支繞過）。
    _s_zlo = station_model({"cls": "segZL", "fleet_size": 2, "producing_sec": 0.0,
                            "active_span_sec": 0.0, "n_seg": 40, "zero_len_only": True},
                           {"cls": "segZL", "output_units": 300}, W, demo=True,
                           rw={"planned_sec": 1.6 * W, "by_state": {"維修": 0.1 * W}})
    ck(_s_zlo["availability"] is None and _s_zlo["utilization"] is None
       and _s_zlo["oee"] is None and _s_zlo["util_real"] is None and _s_zlo["composition"] == [],
       f"零工期站**即使有 rw 材料**仍全 None（zlo 短路優先於換軸）: A={_s_zlo['availability']}")
    #   M12＝`|RW|=0` ＋ **落後目標** ⇒ `cause` 那段會拿 A 去算 ⇒ 沒有守衛就是硬 500。
    _u_behind = {"cls": "segLate", "fleet_size": 1, "producing_sec": 0.4 * W,
                 "active_span_sec": 0.9 * W}
    _y_behind = {"cls": "segLate", "final_pass_pct": 90.0, "output_units": 500}
    _s_b = station_model(_u_behind, _y_behind, W, demo=True,
                         rw={"planned_sec": 0.0, "by_state": {}})
    ck(_s_b["gap"] is not None and _s_b["gap"] < 0,
       f"構造成立：這一站確實落後目標（gap={_s_b.get('gap')}）")
    ck(_s_b["availability"] is None and _s_b["cause"] is None,
       "|RW|=0 ＋ 落後目標 ⇒ 歸因整塊撤、**不炸**（沒有這個守衛就是 500）")
    # (J5) |RW|=0（名冊外站／整期間停線）⇒ 三軸「—」、組成與歸因整塊撤
    s_z = station_model(u, y, W, demo=True, rw={"planned_sec": 0.0, "by_state": {}})
    ck(s_z["availability"] is None and s_z["utilization"] is None and s_z["oee"] is None
       and s_z["util_real"] is None, "|RW|=0 ⇒ A/U/OEE/util_real 全 None（顯「—」）")
    ck(s_z["composition"] == [] and s_z["cause"] is None,
       "|RW|=0 ⇒ 組成/缺口歸因撤（無可信基底；歸因用到 A 會炸）")
    ck(s_z["output"] == 500 and s_z["has_output"] is True,
       "|RW|=0 仍顯產出（Layer-1 與該跑窗無關）")
    # (J6) production_model：rw_by_cls 缺 ⇒ byte-identical；有 ⇒ **每站都上新軸**（缺料的站顯「—」）
    _pm_base = production_model(sb, yb, W, demo=True)
    ck(_pm_base["rw_axis"] is False and _pm_base["line"]["rw_axis"] is False
       and _pm_base["rw_planned_sec"] is None, "production_model 預設 rw_axis False（舊行為）")
    _rwc = {"segX": {"planned_sec": 1.6 * W, "by_state": {"維修": 0.16 * W}}}   # segY 刻意缺
    m_rw = production_model(sb, yb, W, demo=True, rw_by_cls=_rwc)
    _sx = next(s for s in m_rw["stations"] if s["cls"] == "segX")
    _sy = next(s for s in m_rw["stations"] if s["cls"] == "segY")
    ck(m_rw["rw_axis"] is True and _sx["rw_axis"] is True and _sy["rw_axis"] is True,
       "rw_by_cls 在 ⇒ **每一站**都上新軸（不做「有的站新軸、有的站舊軸」）")
    ck(_sy["availability"] is None and _sy["oee"] is None,
       "rw_by_cls 沒有的站 ⇒ planned=0 ⇒ 顯「—」（C-RW9 永不拿鄰站代打、也不退回舊軸）")
    L_rw = m_rw["line"]
    ck(abs(L_rw["availability"] - (1.0 - 0.16 / 1.6)) < 1e-9, "線級 A＝1−Σ扣除/Σ|RW|")
    ck(abs(L_rw["util_real"] - _sx["producing_sec"] / (1.6 * W)) < 1e-9,
       "線級 util_real＝Σ生產/Σ|RW|，且 **|RW|=0 的 segY 兩邊都不進**（C-RW9 的兩面）")
    ck(m_rw["rw_n_stations"] == 1, "線級只數真正有應生產時段的站（segY 不算）")
    ck(abs(L_rw["availability"] * L_rw["utilization"] - L_rw["util_real"]) < 1e-9,
       "線級恆等 A×U==util_real（新軸）")
    ck(any(c["state"] == RW_IDLE_STATE for c in L_rw["composition"]),
       "線級組成含『該跑沒跑』（新軸的殘量桶）")
    # (J7) station_detail：rw_by_eid 缺 ⇒ byte-identical；有 ⇒ 逐列上新軸、缺列顯「—」
    _yrec = {"cls": "segX", "machines": [{"eid": "EID-1", "output_units": 100, "good_units": 95},
                                         {"eid": "EID-2", "output_units": 80, "good_units": 80}]}
    _tlm = {"EID-1": {"producing_sec": 0.5 * W, "total_sec": 0.9 * W},
            "EID-2": {"producing_sec": 0.2 * W, "total_sec": 0.8 * W}}
    d_old = station_detail({"cls": "segX"}, _yrec, _tlm, W, demo=True)
    ck(d_old["rw_axis"] is False and d_old["units"][0]["availability"] is not None,
       "station_detail 預設走舊 demo 拆分（byte-identical）")
    d_rw = station_detail({"cls": "segX"}, _yrec, _tlm, W, demo=True,
                          rw={"planned_sec": 1.6 * W, "by_state": {}},
                          rw_by_eid={"EID-1": {"planned_sec": 0.8 * W,
                                               "by_state": {"維修": 0.08 * W}}})
    _u1 = d_rw["units"][0]
    _u2 = d_rw["units"][1]
    ck(d_rw["rw_axis"] is True and _u1["rw_axis"] is True, "station_detail rw：逐列標新軸")
    ck(abs(_u1["availability"] - (1.0 - 0.08 / 0.8)) < 1e-9, "子工位 A＝1−維修/|RW|")
    ck(abs(_u1["availability"] * _u1["utilization"] - _u1["util"]) < 1e-9,
       "子工位恆等 A×U==util（新軸）")
    ck(_u2["availability"] is None and _u2["utilization"] is None and _u2["oee"] is None
       and _u2["has_util"] is True,
       "名冊外子工位（rw_by_eid 無此列）⇒ A/U/OEE None＝顯「—」，但仍成列（C-RW9）")

    print(f"[board_oee] selftest PASS — {n} 項斷言全過："
          f"real_split 恆等 A×U==producing/base + 除零/clamp 守恆；rollup per-EID→per-cls 相加（無 double-attribution）；"
          f"station_model real 分支 oee==util24×Q、composition 加總=total_cap、demo 欄位歸零；"
          f"demo 分支 byte-不變；production_model demo↔real 雙路徑 + 混合 graceful；"
          f"§6.7 應生產時段軸 rw_split 恆等 A×U==util_rw + |RW|=0⇒None + U>1 不 clamp、"
          f"妥善率白名單（2026-09-04 換尺：A↓ U↑ **A×U 不動**）、"
          f"rw 缺＝逐鍵 byte-identical、三支（station/production/detail）全覆蓋。"
          f"凍結核心未 import。")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="board_oee 呈現模型（OEE/達標；A↔U real/demo 拆分）")
    ap.add_argument("--selftest", action="store_true", help="離線純函式自驗（零 MES）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()


if __name__ == "__main__":
    main()
