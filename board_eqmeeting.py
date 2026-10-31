# -*- coding: utf-8 -*-
"""
board_eqmeeting.py — 設備部門例行會議頁『呈現模型』（純函式 + demo 合成 + selftest）
=====================================================================================

定位（F4 設備工程日常視角；與 board_daily.py「老闆昨日戰報」平行、但受眾不同）：
  設備部門每週例行會議要看的是**每一台設備最近幾天、有紀錄的停機分布**——哪台停最久、
  停在哪一天、是維修還是保養、現在到底能不能生產。本模組把 cmms 停機工單（board_w8
  契約消費端）攤成「設備 × 天」的停機格盤 + 每台『現在』Up/Down 徽章 + 會議 headline。

═══ 口徑聲明（2026-07-06 Chris 正式裁決；純工單導向，取代原「MES 停機窗疊工單」）═══
  本頁**只 review 有紀錄的 downtime＝cmms 工單**。逐條出處：
  1. 只顯示 cmms 工單時數（維修 / 計畫保養 / 調機 / 其他維護 4 態）。MES gap、「不明」態、
     「無 MES 灰格」全部移除；**沒開工單的停機此頁不顯示**（與被取代的 Excel 日報同口徑）。
  2. 「故障停機」不得出現在此頁——§23 裡它的判定源是 remote-diag 訊號（非工單），此頁產不出它。
  3. 「現在」徽章＝**多源裁決、MES 是加分不是門檻**（2026-07-10 Chris 拍板，修正 07-09 首版）：
     ① 有 open 工單（活躍窗涵蓋現在且此刻階段算停機）→ DOWN；
     ② 否則預設 **UP**——沒維修工單就當機台可用；MES 只負責**升級標籤**：距上次生產
        ≤ MES_RECENT_H → UP·生產中（MES 確認在跑）、否則 UP·待命（附「最後生產 Xh 前」小字）；
     ③ MES 證據未取得（探測失敗）→ 仍 UP（純工單二值）＋頁面 caveat。
     為何不留 UNKNOWN 第三態：07-09 首版把「沒工單也沒近 2h 生產」判 UNKNOWN，結果**滿屏灰**
     ——機台在任一時刻多半不是「剛收完一段」。板子的價值是「一眼掃紅色」，灰海把它淹了。
     **已知取捨（Chris 知情拍板）**：「壞了但還沒開工單」會顯 UP·待命。沒有心跳源本來就分不出
     「待命」與「壞了沒開單」；『沒工單＝預設 UP』是務實預設，等心跳（jumpbox O11）接上才真能
     確定——屆時 UP·待命 升級為「確認待命」。**沉默永遠不推 DOWN**（gap≠fault §10.2/§23）。
  3b.⚠ 久靜提示（同輪拍板）：有 MES age 且 > QUIET_H（見該常數；R45 起 QUIET_DAYS 天）→ 掛 ⚠、**狀態仍是 UP**，
     只把「值得問一句」的少數浮出來。無 age（本就不寫 MES／超出探測窗）→ 不掛 ⚠（分不出＝不猜）。
     **停機 heatmap 仍純工單口徑（3/3b 只改 Now 欄）**；MES_RECENT_H 與 /updown 同門檻。
  4. A%＝1 − 工單停機時數 / 窗時數（24h 基準；排班源未接前下班/週末計為可用——tooltip 講明）。
     昨日Δ照舊。**停機時數＝工單活躍時數（開單→完工）；≠§14.4 融合稼動**；重疊工單不重複計入
     總時數（interval-union；跨態重疊時 Σby_state 可 > union stop_h＝口徑，footer 講明）。
  5. legend 顏色高區分度（dataviz 驗證：4 態 categorical、CVD ΔE 82，light+dark 皆 PASS）。
  6. 工單號→cmms 工單深連結、新分頁開。
  MES gap 面（融合稼動、不明態、無源灰格）歸 /utilization 與 /updown，**不在本頁**。

═══ 消費紀律（本檔只 import board_w8、絕不改姊妹）═══════════════════════════════
  - board_w8（W8 停機融合契約消費端 stub）：normalize_wo / wo_active_window /
    wo_state / migration_fallback_rate / CANONICAL_STATES —— 純消費。
  - 凍結核心 mes_timeline / mes_yield：**完全不 import**（本頁不連 MES、不碰 SQL、不吃 gap）。
  - board_updown：import `MES_RECENT_H`（『現在』徽章多源、與 /updown 同門檻；MES 證據值由
    app 層探測後傳入 build_board，本模組仍不連 MES）。

═══ 誠實鐵則 ═════════════════════════════════════════════════════════════════
  『真』（live）：cmms 工單分類、停機時數、Up/Down 現況（皆源自工單）。
  『DEMO』（demo_board 全合成、頁面須掛「全模擬」大牌）：per-EID 確定性合成契約形工單。
  無工單的設備＝stop_h 0、A% 100.0（工單口徑誠實值——此頁只認有紀錄的停機）。

出處：EQMT_CATALOG 設備目錄＝Chris 2026-07-06 由 Excel 轉錄（順序保留、EID 補零成 5 碼）。
"""
import sys
import json
import argparse
import datetime as _dt
import random

import board_w8 as w8
import board_updown as ud   # 只取 MES_RECENT_H（『現在』多源裁決與 /updown 同門檻；不連 MES）
import board_dormant as wdorm   # 只在 demo_board 取橋常數投影；live 的 dormant_by_eid 由 app 層傳入
import board_scope as wscope    # A10 母單展開（PARENT_EXPANSION／expand_for_children）；demo 自算、live 由 app 層傳入
import board_runwindow as wrw   # #39 該跑窗核心（`board.runwindow.v1` 純函式；SPEC-RUNWINDOW v1）
import board_tags as wtags        # WP-D：730 天普查的 EID 集合＝「MES 量得到嗎」的靜態權威（純資料、零 I/O）
import board_capacity as _wcap    # 子工位名冊（形① nest／形② co-tag）＋排列軸的唯一出處；純常數、零 I/O

# ⚠ 久靜門檻：有 MES age 且超過此值 → 掛 ⚠「值得問一句」、**狀態仍 UP**（不改判斷）。
# ★ R45（Chris 2026-09-29 拍板）：**7 天＝168h，仍是牆鐘時數**（不讀生產日曆）。
#   為什麼改：09-02 WP-D 起「久靜」同時決定**摺疊**（沉默機絕不摺，`row_visibility`）⇒ 門檻太短
#   的代價從「多一個 ⚠」變成「多一整列擠上表」。09-29 實測：09-25～09-28 四天連休後，7 天期間
#   顯示 60 列、其中 35 列是無工單的沉默機，約 25 列只是最後生產落在 4.3–4.9 天前（連假）。
#   Chris 明示不改 UI、改門檻；取 7 天＝與頁面預設期間同長，語意＝「整個預設期間都沒生產」。
#   **沒改的**：沉默機絕不摺（D1）、座列一律顯示（C-RW26）、斜紋列算有紀錄、`age is None`
#   仍歸 silent、摘要列文案、計法不讀生產日曆。已知殘留：農曆年九天連假仍會誤閃（ROADMAP）。
#   沿革：07-10 首定 3 天＝72h（撐過正常週末 ≈63h；當時全機隊只 1 個久靜警示，連假會閃＝可接受、只是提示）。
# **單一出處**：app 層的 `EQM_QUIET_DAYS` 預設值由 `QUIET_DAYS` 帶出（env `BOARD_EQM_QUIET_DAYS`
# 仍可覆寫後傳 build_board(quiet_h=…)）；本模組純函式、不讀 env。所有文案一律由此值插值、不寫死。
QUIET_DAYS = 7
QUIET_H = QUIET_DAYS * 24.0

# /equipment 的 MES **備援探測窗**（天）：lastprod 狀態檔拒收時退回 live 探測
# （`app._eqm_mes_evidence` → `get_updown_evidence(fleet_days=lookback_days=此值)`）。
# ★ 不變量：**探測窗必須 > 久靜門檻**——否則久靜的機台會直接掉出探測目錄（age None）、
#   ⚠ 永不觸發、還被歸成「整窗無生產」。R45 門檻 3→7 天時同步 7→10 天（留 3 天餘裕）。
#   app 層預設值由此常數帶出（env `BOARD_EQM_MES_DAYS` 可覆寫）；下方 import 期檢查＋selftest 釘住。
MES_PROBE_DAYS = 10
if not MES_PROBE_DAYS * 24.0 > QUIET_H:      # 非 assert（-O 也要擋）：改數字的下一手在 import 期就撞牆
    raise ValueError(f"MES_PROBE_DAYS={MES_PROBE_DAYS}d must exceed QUIET_H={QUIET_H}h "
                     "(probe window <= quiet threshold ⇒ quiet machines fall out of the probe)")

# (c) MES 交叉揭露的護欄（小時）——「報修當下正把手上那顆跑完」是常態，工單起點附近有生產
# 訊號**不算矛盾**；再加上 MES 探測與 cmms 時戳的時鐘 skew。只有生產訊號落在停機段起點
# **guard 小時之後**才算真矛盾。app 層以 BOARD_EQM_WO_MES_GUARD_H 覆寫後傳入；本模組不讀 env。
WO_MES_GUARD_H = 2.0

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# 頁面局部呈現 key（**非** §23 canonical 態）——會議「一眼看色帶就分辨」原則
# （2026-07-07 Chris 裁決）：把 ON_HOLD 各子類從母態拆出成獨立色帶，色帶本身即語意。
#
# ── 計停機的 ON_HOLD 子類（countable=True、正常進 stop_h/by_state）：不論母 work_type，
#    等料 / 等外包各給獨立色，讓觀眾在 heatmap 上分辨「這台是卡料不是純維修」。
#    OTHER / 未知 hold_reason＝保守留在母態（維修/計畫保養…）、不另開第 9 色。
#    ⚠ 色碼本身住在 templates/equipment.html 的 :root CSS 變數（亮/暗各一階）；模板 STC 只做
#      「態 → var 名」對映。本模組只定義 canonical key、不碰顏色。
#      2026-07-10 re-pick：等料曾＝#16a34a，與「生產」同綠＝讀作 pass／沒事。
HOLD_PARTS_STATE = "等料(停機)"       # WAITING_PARTS  → 計停機、獨立色（紫 --st-parts）
HOLD_VENDOR_STATE = "等外包(停機)"    # WAITING_VENDOR → 計停機、獨立色（琥珀實心 --st-vendor；2026-07-10 由紫圓點改）
# countable ON_HOLD 子類 → 專屬停機態（其餘 hold_reason→母態、保守不開新色）。
HOLD_DOWN_STATES = {"WAITING_PARTS": HOLD_PARTS_STATE, "WAITING_VENDOR": HOLD_VENDOR_STATE}

# ── 不計停機的 ON_HOLD 子類（countable=False、只活 drill 色帶、條紋樣式、機台可生產）：
#    等機台空檔 / 試跑觀察各給一個「明顯不同」的低彩度條紋色（不與計停機色競爭＝『機台沒事』）。
UPTIME_WAIT_STATE = "暫停·等機台空檔"   # WAITING_MACHINE_TIME → 不計、斜紋（淺冷灰 --st-hwait）
UPTIME_TEST_STATE = "暫停·試跑"        # TEST_RUN             → 不計、圓點（淺暖灰 --st-htest；2026-07-10 由斜紋改，兩 uptime 分辨）
_UPTIME_STATES = (UPTIME_WAIT_STATE, UPTIME_TEST_STATE)

# 「不計停機」hold_reason 集（S12 口徑、本檔擁有）——**永遠只有這兩值**；
# cmms 2026-07-07 對版明證此集正確且完整（canonical 5 值中其餘 WAITING_PARTS/
# WAITING_VENDOR/OTHER＝計停機）。與 w8.HOLD_REASONS_KNOWN（顯示保留集、已擴 5 值）
# **脫鉤**——絕不改回引用那個 tuple 當停機判準。
UPTIME_HOLD_REASONS = ("WAITING_MACHINE_TIME", "TEST_RUN")


def _uptime_state(hold_reason):
    """不計停機 ON_HOLD 子類 → 拆分後的 uptime 呈現態（只有 WAITING_MACHINE_TIME/TEST_RUN 會到此）。"""
    return UPTIME_TEST_STATE if hold_reason == "TEST_RUN" else UPTIME_WAIT_STATE


# ---------------------------------------------------------------------------
# 權威設備目錄（Chris 2026-07-06 Excel 轉錄；順序不可改；EID 原始值補零成 5 碼）
#   entry 形＝(area_key, display_name, eid)；area_key ∈ {"za","zb","zc","zd"}。
#   同名多台（AttachRig / Die Placement Cure Oven / AOI Frame Straightness / Thermal Cycle·Bake Oven /
#     Low·High Power Microscope / E-Tester）＝正常，靠 EID 區分。
# ---------------------------------------------------------------------------
# ⚠ 顯示名一桶一名（Chris 2026-08-24 裁決）：`zc` 桶畫面上一律叫 **Zone C**（與 PLOVER／人力頁同名）；
#   area_key 是 `zc`（g5 起資料鍵即中性鍵 za/zb/zc/zd）。改這行＝全站同步（/equipment、PM 板、兩份匯出、MCP 標籤）。
AREAS = [('za', 'Zone A'), ('zb', 'Zone B'), ('zc', 'Zone C'), ('zd', 'Zone D')]

# dup EID 容許清單：2026-07-06 Chris 裁決 36255＝Zone C AutoOQC（Zone D Output Checker 該列已刪）→ 現無容許 dup；
# selftest 對任何重複一律 FAIL。
KNOWN_DUP_EIDS = set()

EQMT_CATALOG = [('za', 'Frame Bonder #1', 'EID-54769'),
                ('za', 'Frame Bonder #2', 'EID-54428'),
                ('za', 'Laminator #1', 'EID-54336'),
                ('za', 'Laminator #2', 'EID-54466'),
                ('za', 'Leak Tester #1', 'EID-54519'),
                ('za', 'Leak Tester #2', 'EID-55462'),
                ('za', 'Serialiser #1', 'EID-54890'),
                ('za', 'Plasma Cell #1', 'EID-54332'),
                ('za', 'Straightness Gauge #1', 'EID-55357'),
                ('za', 'Straightness Gauge #2', 'EID-54508'),
                ('za', 'Plasma Cell #2', 'EID-55561'),
                ('za', 'Film Inspector #1', 'EID-55142'),
                ('za', 'Placer #1', 'EID-55032'),
                ('za', 'Placer #2', 'EID-55352'),
                ('za', 'Cure Oven #1', 'EID-55459'),
                ('za', 'Cure Oven #2', 'EID-54748'),
                ('za', 'Pre-cure Align Gauge #1', 'EID-54352'),
                ('za', 'Pre-cure Height Gauge #1', 'EID-55286'),
                ('za', 'Align Gauge #1', 'EID-54721'),
                ('za', 'Height Gauge #1', 'EID-54822'),
                ('za', 'Pick Inspector #1', 'EID-55052'),
                ('za', 'Bake Oven', 'EID-54383'),
                ('za', 'Bake Oven', 'EID-55507'),
                ('za', 'Inspection Scope #1', 'EID-55172'),
                ('za', 'Visual Bench #1', 'EID-55590'),
                ('zb', 'Flex Bonder #1', 'EID-55593'),
                ('zb', 'Flex Bonder #2', 'EID-54798'),
                ('zb', 'Flex Bonder #3', 'EID-54733'),
                ('zb', 'Bend Tester #1', 'EID-55321'),
                ('zb', 'Wire Bonder #1', 'EID-54655'),
                ('zb', 'Wire Bonder #2', 'EID-55109'),
                ('zb', 'Plasma Cell #3', 'EID-54618'),
                ('zb', 'Dispenser #1', 'EID-54348'),
                ('zb', 'Dispenser #2', 'EID-54597'),
                ('zb', 'Dispenser #3', 'EID-54471'),
                ('zb', 'UV Oven #1', 'EID-54977'),
                ('zb', 'UV Oven #2', 'EID-54950'),
                ('zb', 'N2 Checker #1', 'EID-54318'),
                ('zb', 'Potting Cell #1', 'EID-55252'),
                ('zb', 'Thermal Oven #1', 'EID-54959'),
                ('zb', 'Thermal Oven #2', 'EID-55219'),
                ('zb', 'Module E-Tester #1', 'EID-55058'),
                ('zb', 'Module E-Tester #2', 'EID-54573'),
                ('zb', 'Assembly Fixture #1', 'EID-54691'),
                ('zb', 'Taping Jig #1', 'EID-54585'),
                ('zb', 'Taping Jig #2', 'EID-54558'),
                ('zb', 'Pull Tester #1', 'EID-55416'),
                ('zc', 'Plasma Cell #4', 'EID-55411'),
                ('zc', 'Manifold Press #1', 'EID-54625'),
                ('zc', 'Chassis Press #1', 'EID-55564'),
                ('zc', 'Calibration Tester #1', 'EID-55040'),
                ('zc', 'Calibration Tester #2', 'EID-54306'),
                ('zc', 'Calibration Bay #1', 'EID-54912'),
                ('zc', 'Calibration Bay #2', 'EID-54681'),
                ('zc', 'Calibration Bay #3', 'EID-54775'),
                ('zc', 'Calibration Bay #4', 'EID-54457'),
                ('zc', 'Auto OQC #1', 'EID-54829'),
                ('zc', 'Auto OQC #2', 'EID-55130'),
                ('zc', 'Final OQC Bench #1', 'EID-54341'),
                ('zc', 'Clean Tester #1', 'EID-55216'),
                ('zc', 'Rinse Bay #1', 'EID-54358'),
                ('zc', 'Rinse Bay #2', 'EID-54395'),
                ('zc', 'Rinse Bay #3', 'EID-55074'),
                ('zc', 'Rinse Bay #4', 'EID-55513'),
                ('zc', 'Rinse Bay #5', 'EID-55177'),
                ('zc', 'Fill Bay #1', 'EID-55196'),
                ('zc', 'Fill Bay #2', 'EID-54760'),
                ('zc', 'Fill Bay #3', 'EID-54380'),
                ('zc', 'Assembly Leak Tester #1', 'EID-54645'),
                ('zc', 'QA Station #1', 'EID-55144'),
                ('zc', 'Pack Inspector #1', 'EID-55053'),
                ('zc', 'Pack Inspector #2', 'EID-55338'),
                ('zc', 'Seal Lube Cell #1', 'EID-55376'),
                ('zc', 'Rinse Fixture #1', 'EID-54821'),
                ('zd', 'Aux E-Tester #1', 'EID-55518'),
                ('zd', 'Aux E-Tester #2', 'EID-54791'),
                ('zd', 'Line-end Checker #1', 'EID-54375'),
                ('zd', 'Line-end Runner #1', 'EID-54506'),
                ('zd', 'Line-end Runner #2', 'EID-54411'),
                ('zd', 'Label Fixture #1', 'EID-55327')]


# ---------------------------------------------------------------------------
# 小工具（純）
# ---------------------------------------------------------------------------

def _seed(s):
    """demo 確定性 seed base（repo 慣例 sum(ord)）。"""
    return sum(ord(c) for c in (s or "x"))


def _coerce_dt(v):
    """segment / WO 窗的時戳 → naive datetime（台北牆鐘、截微秒）。
       容忍 datetime（live/demo）或 ISO 字串（snapshot＝str(datetime)）。解析不了→None。"""
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        d = v
    else:
        s = str(v).strip()
        try:
            d = _dt.datetime.fromisoformat(s)
        except ValueError:
            try:
                d = _dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if d.tzinfo is not None:
        d = d.replace(tzinfo=None)
    return d.replace(microsecond=0)


def _union_seconds(intervals):
    """互相重疊區間的聯集總秒數（sweep）。intervals＝[(start,end), …]（datetime、end>start）。"""
    ivs = sorted((s, e) for s, e in intervals if s is not None and e is not None and e > s)
    total = 0.0
    cur_s = cur_e = None
    for s, e in ivs:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        elif e > cur_e:
            cur_e = e
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


# ---- 區間代數（#36 實跑窗用；`_union_seconds` 只回秒數、疊 closure 需要「區間本體」）------
#   ★ 三支都是**純集合運算、零口徑**：口徑（誰進 closure、uncertain 怎麼算）全在
#     `SPEC-PROD-CALENDAR.md` 與 `availability_pw`／`availability_pw_window` 的 docstring。
#   ★ 輸入一律 [(start,end), …]；`_subtract`／`_intersect` 的兩邊都**必須先 `_union_intervals`**
#     （非重疊且排序是掃描前提；沒 union 過的輸入會靜默給錯答案）。

def _union_intervals(intervals):
    """[(s,e)…] → 排序且互不重疊的 union list（`_union_seconds` 的「回區間」版）。"""
    out = []
    for s, e in sorted((s, e) for s, e in intervals
                       if s is not None and e is not None and e > s):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def _subtract_intervals(base, cut):
    """base − cut（兩者皆須已 union）。回排序、互不重疊的 list。"""
    out = []
    for s, e in base:
        if e <= s:               # 零/負長度（如末欄 partial 恰好收在午夜）＝沒有東西可扣
            continue
        cur = s
        for c_s, c_e in cut:
            if c_e <= cur or c_s >= e:
                continue
            if c_s > cur:
                out.append((cur, c_s))
            if c_e > cur:
                cur = c_e
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def _intersect_intervals(a, b):
    """a ∩ b（兩者皆須已 union）。回排序、互不重疊的 list。"""
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        s = a[i][0] if a[i][0] > b[j][0] else b[j][0]
        e = a[i][1] if a[i][1] < b[j][1] else b[j][1]
        if e > s:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _ivs_seconds(ivs):
    return sum((e - s).total_seconds() for s, e in ivs)


# ---------------------------------------------------------------------------
# 1) day_windows — 以 win_end 為錨往回 n_days 台北日曆日（末欄＝今日 partial）
# ---------------------------------------------------------------------------

PROD_DAY_START_H = 8      # 生產日界 08:00（C-PC1／SPEC-RUNWINDOW A6；唯一出處＝
                          # `_probe_kun_activity.PROD_DAY_START_H`，此處鏡像數值、selftest 釘等值）


def day_windows(win_end, n_days):
    """→ [(date_str, d_start, d_end), …]（oldest→today，共 n_days 欄）。

       ★ 2026-08-23 換軸（SPEC-RUNWINDOW **A6**、Chris 拍板）：欄＝**生產日**
       `[D 08:00, D+1 08:00)`，**不再是台北日曆日**。00:00–07:59 屬前一個生產日
       （ZC night 20:00–04:40／ZAB swing 16:00–00:40 因此完整嵌在單一欄內＝跨午夜
       不再是邊界 case）。末欄（今日生產日）＝[當前生產日 08:00, win_end)（partial）。
       date_str="MM-DD"＝**生產日**日期——頁面日期欄從此是生產日（方法節已註明）。
       win_end＝datetime（台北 naive）。"""
    n = max(1, int(n_days))
    win_end = _coerce_dt(win_end)
    today = win_end.date()
    if win_end.hour < PROD_DAY_START_H:            # 清晨＝仍屬前一生產日
        today = today - _dt.timedelta(days=1)
    wins = []
    for k in range(n - 1, -1, -1):                 # k＝幾個生產日前；oldest 先、today 最後
        day = today - _dt.timedelta(days=k)
        d_start = _dt.datetime(day.year, day.month, day.day, PROD_DAY_START_H)
        d_end = win_end if k == 0 else d_start + _dt.timedelta(days=1)
        wins.append((f"{day.month:02d}-{day.day:02d}", d_start, d_end))
    return wins


# ---------------------------------------------------------------------------
# 2) wo_segments — 工單活躍窗 → **分階段** 停機 segment（S12 口徑，2026-07-07 Chris 裁決）
# ---------------------------------------------------------------------------
#   工單活躍窗內不再整窗一律算停機，改由 normalize_wo 的 status_history 重建階段時間線、
#   逐階段判定「算停機 / 機台可生產不計 / 不算」：
#     · OPEN（未開始處理）：REACTIVE→算（壞了才報修）；PM/調機/暫桶→不算（開始處理才停機）。
#     · IN_PROGRESS（處理中）：一律算。
#     · ON_HOLD：hold_reason∈{WAITING_MACHINE_TIME,TEST_RUN}→機台可生產、不計停機（uptime-hold）；
#                其他（等料 WAITING_PARTS/等外包 WAITING_VENDOR/OTHER/未知「不知道」）→算。
#     · 遷移單（history 空、非仍 open）：無階段資料→整窗照算（歷史已結案單合理近似）。
#     · 仍 open 且 history 空/只有 OPEN：按 OPEN 規則（REACTIVE 算到 win_end、PM/調機/暫桶 0）。
#   COMPLETED/終態＝止於活躍窗尾（wo_active_window 語意不變）。


def _phase_kind(status, hold_reason, work_type):
    """單一階段 → 'count'（算停機）/ 'uptime'（機台可生產、不計）/ 'none'（不算、不畫）。
       status=None＝遷移單無階段資料的整窗段（rule 5）→算。"""
    if status is None:                                   # 遷移單整窗（rule 5）
        return "count"
    if status == "IN_PROGRESS":
        return "count"
    if status == "ON_HOLD":
        if hold_reason in UPTIME_HOLD_REASONS:           # 等機台空檔 / 試跑觀察＝可生產
            return "uptime"
        return "count"                                   # 等料/等外包/OTHER/未知「不知道」→算
    if status == "OPEN":                                 # 未開始處理：只有維修算
        return "count" if (work_type or "").strip().upper() == "REACTIVE" else "none"
    return "none"                                        # COMPLETED/終態＝活躍窗外（已被裁掉）


def _phase_timeline(rec, a_start, a_end, wo_ongoing):
    """由 normalize_wo 的 status_history 重建活躍窗 [a_start, a_end] 內的階段時間線。
       → [{start, end, status, hold_reason}]（時間序、首尾鋪滿活躍窗）。
       · history 空 + 仍 open → 單段 status='OPEN'（rule 6：按 OPEN 規則）。
       · history 空 + 非 open → 單段 status=None（rule 5：遷移單整窗）。
       · history 非空：每條 changed_at 起新階段（狀態=該條 to_status），前綴補 OPEN（若首條
         晚於 a_start）；末條非終態延伸至 a_end；各階段裁到 [a_start, a_end]。"""
    hist = rec.get("status_history") or []
    if not hist:
        return [{"start": a_start, "end": a_end,
                 "status": ("OPEN" if wo_ongoing else None), "hold_reason": None}]
    bps = [(e["at"], e["status"], e.get("hold_reason")) for e in hist]
    if bps[0][0] > a_start:                              # opened_at 早於首條 history → 補 OPEN 前綴
        bps.insert(0, (a_start, "OPEN", None))
    phases = []
    for i, (t, st, hr) in enumerate(bps):
        ps = t if t > a_start else a_start
        pe = bps[i + 1][0] if i + 1 < len(bps) else a_end
        if pe > a_end:
            pe = a_end
        if pe > ps:
            phases.append({"start": ps, "end": pe, "status": st, "hold_reason": hr})
    return phases


def wo_segments(wos, win_start, win_end):
    """單一 EID 的 normalize_wo 輸出列表 → **分階段** 停機 segment 列表（S12 口徑，2026-07-07）。
       每筆 → w8.wo_active_window(w, now=win_end) 取活躍窗，再由 status_history 重建階段時間線
       （見上方口徑）。每階段依 _phase_kind：
         · 'count'  → countable=True、state=w8.wo_state(work_type)（維修/計畫保養/調機/其他維護）；
                      但 ON_HOLD 等料/等外包從母態拆出成 等料(停機)/等外包(停機) 獨立色帶（其餘 hold→母態）。
         · 'uptime' → countable=False、state=暫停·等機台空檔／暫停·試跑（機台可生產、不入 stop_h/A%；
                      仍入 segments 供 drill 色帶顯示、兩子類各一條紋色）。
         · 'none'   → 不產 segment（PM/調機/暫桶的 OPEN 未開始處理＝機台還在跑）。
       各階段裁到 [win_start, win_end]；裁後空段跳過。**故障停機此路徑產不出**（裁決②）。
       segment 形＝{start,end,state,confidence,source,work_order_no,ongoing,countable}（countable additive）。
       ongoing＝仍 open 且該 countable 段延伸至 win_end（＝目前仍停機中）；uptime 段永遠 ongoing=False。
       允許含 None（normalize_wo 對壞資料合法回 None）——skip。輸出按 (start, wo_no) 排序。
       ★ **取消／作廢單一律不產段**（2026-08-11 拍板①，判定＝`w8.wo_is_cancelled`）：對齊 PM 板
         既有 `C-P7`。理由不是「取消一定沒停機」，而是同一張單在 PM 板不算、設備板算＝內部矛盾。
         此處是**唯一**過濾點——heatmap／`downtime_by_eid`／attrib 的 `wo_segs`／oee 拆分／
         `/dispatch` 的 `down_eids` 全部消費本函式 ⇒ 一處擋、全線跟上（不得在下游各寫一份）。
         實測：`WO 35185`／`EID-00884` 開單 47 天後才被取消，過去整片塗紅的就是這型。"""
    win_start = _coerce_dt(win_start)
    win_end = _coerce_dt(win_end)
    out = []
    for w in wos or []:
        if not isinstance(w, dict):
            continue
        if w8.wo_is_cancelled(w):        # 取消／作廢＝不進可計停機（拍板①；對齊 C-P7）
            continue
        win = w8.wo_active_window(w, now=win_end)
        if win is None:
            continue
        a_start, a_end = win
        wo_ongoing = a_end is None
        a_start = _coerce_dt(a_start)
        a_end_eff = win_end if wo_ongoing else _coerce_dt(a_end)
        if a_start is None or a_end_eff is None or a_end_eff <= a_start:
            continue
        wt = w.get("work_type")
        state, conf, _canon = w8.wo_state(wt)
        wno = w["work_order_no"]
        wsegs = []                                          # 本單各階段段（相鄰同類合併前）
        for ph in _phase_timeline(w, a_start, a_end_eff, wo_ongoing):
            kind = _phase_kind(ph["status"], ph["hold_reason"], wt)
            if kind == "none":
                continue
            a = ph["start"] if ph["start"] > win_start else win_start
            b = ph["end"] if ph["end"] < win_end else win_end
            if b <= a:
                continue
            if kind == "uptime":                            # 不計停機：拆 等機台空檔 / 試跑
                seg = {"start": a, "end": b, "state": _uptime_state(ph["hold_reason"]),
                       "confidence": conf, "source": "cmms_workorder", "work_order_no": wno,
                       "ongoing": False, "countable": False}
            else:                                            # 計停機：ON_HOLD 等料/等外包拆出母態
                cstate = state
                if ph["status"] == "ON_HOLD":
                    cstate = HOLD_DOWN_STATES.get(ph["hold_reason"], state)   # 其餘 hold→母態
                og = wo_ongoing and b == win_end and ph["end"] == a_end_eff
                seg = {"start": a, "end": b, "state": cstate, "confidence": conf,
                       "source": "cmms_workorder", "work_order_no": wno,
                       "ongoing": og, "countable": True}
            # 相鄰同 state/countable 段合併（如 REACTIVE 的 OPEN→IN_PROGRESS 皆維修＝一連續塊）
            if wsegs and wsegs[-1]["end"] == seg["start"] \
                    and wsegs[-1]["state"] == seg["state"] \
                    and wsegs[-1]["countable"] == seg["countable"]:
                wsegs[-1]["end"] = seg["end"]
                wsegs[-1]["ongoing"] = wsegs[-1]["ongoing"] or seg["ongoing"]
            else:
                wsegs.append(seg)
        out.extend(wsegs)
    out.sort(key=lambda s: (s["start"], s["work_order_no"]))
    return out


# ---------------------------------------------------------------------------
# 2.4) _inherited_segments — A10 母單展開段（母 EID 工單 → 子工位 segment）
# ---------------------------------------------------------------------------
#   規格＝`CONCEPT.md` §19.3 A10「Board 側拍板全表」；區間計算＝`board_scope`。
#   ★ **兩軸交集**（cmms A10 ④ 的消費鐵則）＝「這段算不算停機」×「這個工位在不在範圍內」：
#     · 「算停機」那一軸＝本檔既有 `wo_segments`（S12 分階段、`_phase_kind=='count'`）
#       ——NOTE-13 fixture 已釘死它與 cmms `is_downtime` 逐段等價 ⇒ **不新收 wire 欄**。
#     · 「在不在範圍」那一軸＝`board_scope.child_clips`（scope 事件流 + dormant 扣除）。
#     兩者在這裡相交；**uptime（不計停機）段不繼承**——它只講「母機那時可生產」，
#     繼承到子工位不增加任何資訊，卻會讓子工位 drill 出現一條與它無關的條紋。
#   ★ 母單與子工位**自身**工單重疊 ⇒ 既有 interval-union（day_cell/downtime_by_eid）
#     天然不重複計（拍板⑨）；本函式只負責產段，不做任何去重。


def _inherited_segments(inherited, win_start, win_end):
    """`inherited`＝`board_scope.expand_for_children` 給該 EID 的 entries
       （`[{"wo", "clips", "parent_eid"}, …]`）→ 帶 additive `inherited_from` 的 segment 列表。

       每個 entry：母單跑一次 `wo_segments`（走**完全相同**的 S12 管線）→ 只取 countable 段
       → 與 clips 逐一交集（裁切、跨 clip 切開）→ 淺拷貝後覆寫 start/end、重算 ongoing、
       加 `inherited_from`（母機 EID）與 `inherited_kind`（inherited＝推論／explicit＝事實）。
       ⚠ **出處只掛在結構欄上、絕不塞進 `desc`／`brief_description` 等自由文字欄**——
         那四個欄是 `board_translate` 的翻譯管線輸入（D9 衛生），把系統文案混進去
         會被送去翻譯、也會污染 cmms 原文。selftest 有負斷言。
       None／空 → []（＝`build_row` 行為逐位元同加此參數前）。純函式、不 mutate 輸入。
       ★ **母單取消／作廢 ⇒ 子工位零繼承段**——本函式**不自己判**，而是靠下面那句
         `wo_segments([wo], …)`：它是取消過濾的唯一落點（拍板①），母單被擋掉就回 []。
         在這裡再寫一次 `wo_is_cancelled` ＝第二份 predicate ＝漂移的種子（同 `_now_inherited`
         對 dormant 的處理紀律）。selftest 有釘子守這條結構性繼承。"""
    out = []
    for ent in inherited or []:
        if not isinstance(ent, dict):
            continue
        wo = ent.get("wo")
        clips = ent.get("clips") or []
        parent = ent.get("parent_eid")
        if not isinstance(wo, dict) or not clips:
            continue
        for s in wo_segments([wo], win_start, win_end):
            if not s.get("countable", True):      # uptime-hold 不繼承（見上方註解）
                continue
            for clip in clips:
                c_s, c_e, kind = clip[0], clip[1], clip[2]
                a = s["start"] if s["start"] > c_s else c_s
                b = s["end"] if s["end"] < c_e else c_e
                if b <= a:
                    continue
                seg = dict(s)
                seg["start"], seg["end"] = a, b
                # ongoing＝「延續至今」；被 clip 裁掉尾巴就不再是「現在仍停」。
                seg["ongoing"] = bool(s.get("ongoing")) and b == s["end"]
                seg["inherited_from"] = parent
                seg["inherited_kind"] = kind
                out.append(seg)
    out.sort(key=lambda s: (s["start"], s["work_order_no"]))
    return out


# ---------------------------------------------------------------------------
# 2.5) assign_lanes — drill band 子軌道排程（時間重疊段分軌、皆可見）
# ---------------------------------------------------------------------------
#   Chris 2026-07-07 裁決：cell drill 的『當日 00:00–24:00 色帶』原本每段皆滿高、絕對定位、
#   時間重疊的段互相覆蓋（後畫的蓋住先畫的）＝同一台機同日的維修單 + 進行中 PM 只看得到一色。
#   修法＝貪婪區間分割（interval partitioning）：時間有重疊的段排入不同 lane（垂直堆疊、都看得見），
#   沒有重疊的日子維持單一 lane（＝今日外觀不變）。所有態一視同仁參與 packing（維修/保養/調機/
#   其他維護/等料/等外包 計停機段 + 兩 uptime 條紋段 + faint 生產/待機段）——PM 軌與維修軌都要現形。

def assign_lanes(segs):
    """band segment（帶 start_min/dur_min 分鐘浮點；route clip 已產好）→ 貪婪區間分割排入子軌道。
       演算法（確定性）：先按 (start_min, end_min, 原索引) 排序；逐段配到「最低索引、且該軌最後一段
       end_min <= 本段 start_min」的 lane（端點相接＝不重疊、可共用同軌），無可用軌則開新軌。
       所有態皆參與（計停機 / uptime 條紋 / faint 生產待機段）。
       → (out, n_lanes)：out＝原順序淺拷貝、每段新增 lane（int，0-based）；n_lanes＝用到的軌道數
       （無段→0、無任何重疊→1、最大同時重疊 k→k）。零長段（dur<=0）以 end==start 處理、可與任意
       前段共軌，不干擾。純函式、零 I/O、deterministic。"""
    segs = segs or []
    items = []                                          # (start, end, 原索引)
    for i, s in enumerate(segs):
        start = float(s.get("start_min") or 0.0)
        dur = float(s.get("dur_min") or 0.0)
        if dur < 0:
            dur = 0.0
        items.append((start, start + dur, i))
    order = sorted(range(len(items)), key=lambda k: (items[k][0], items[k][1], items[k][2]))
    lane_end = []                                       # 各 lane 目前最後 end_min
    lane_of = [0] * len(items)
    for k in order:
        start, end, _i = items[k]
        placed = None
        for li in range(len(lane_end)):
            if lane_end[li] <= start:                   # 端點相接＝不重疊、可共軌
                placed = li
                break
        if placed is None:
            placed = len(lane_end)
            lane_end.append(end)
        else:
            lane_end[placed] = end
        lane_of[k] = placed
    out = []
    for i, s in enumerate(segs):
        c = dict(s)
        c["lane"] = lane_of[i]
        out.append(c)
    return out, len(lane_end)


# ---------------------------------------------------------------------------
# 3) group_wos_by_eid — 按 asset_id 分組（None/壞資料容忍跳過）
# ---------------------------------------------------------------------------

def group_wos_by_eid(work_orders):
    """normalize_wo 輸出列表（允許含 None）→ {eid: [wo]}。asset_id 空/壞→跳過。"""
    out = {}
    for w in work_orders or []:
        if not isinstance(w, dict):
            continue
        eid = w.get("asset_id")
        if not isinstance(eid, str) or not eid:
            continue
        out.setdefault(eid, []).append(w)
    return out


# ---------------------------------------------------------------------------
# 3b) dormant_split_segments — 長停工位的**直接**工單段過濾（2026-08-14 Chris 裁決）
# ---------------------------------------------------------------------------
#   A10 驗收時抓到的第二個洞：`board_scope` 只管「母單展開到子工位」那條線，
#   **直接開在長停工位自身上的工單完全不過 dormant** ⇒ 那台機的停機照樣計。
#   Chris 裁決：**登記長停的工位不得再計任何 downtime**（理由與 §5 翻面同一條——
#   長停工位不在產能分母裡，停機卻進分子＝同一台機在兩張表上各講一句話）。
#
#   ★ 三條實作紀律：
#     ① **重用 `wdorm.split_day_windows` + `wdorm.is_dormant`**——as-of 台北日曆日、
#        日粒度，**不自訂第二套日界／第二套判定**（第二份 predicate＝漂移的種子）。
#     ② **只碰 countable 段**：uptime-hold 段本來就不計停機、只在 drill 顯示，
#        濾掉它只會讓 drill 少一條與判斷無關的條紋。
#     ③ **扣掉的段不消失**：回 `(kept, dropped)`，`dropped` 是揭露行的帳。
#   ⚠ 這裡**不是**取消單那條過濾的第二份——取消單擋在 `wo_segments`（唯一過濾點），
#     dormant 擋在這裡是因為它**依 EID 而定**、而 `wo_segments` 收的是「某台機的單」
#     卻不知道自己在替誰算（它沒有 eid 參數）。兩條規則的落點不同、不互相取代。


def dormant_split_segments(segs, eid, dormant_events=None):
    """該 EID 的 `wo_segments` 輸出 → `(kept, dropped)`。

       `dropped`＝countable 段落在該工位**長停日**的那些片（逐台北日曆日切開）。
       非 countable 段一律留在 `kept`（見上方紀律②）。`dormant_events=None`＝過渡橋常數、
       `[]`＝關掉（負對照）。**該 EID 全窗都不長停 ⇒ `kept` 與輸入逐位元相同、`dropped` 空。**
       純函式、零 I/O、不 mutate 輸入（切片為新 dict）。"""
    kept, dropped = [], []
    for s in segs or []:
        if not isinstance(s, dict) or not s.get("countable", True):
            kept.append(s)
            continue
        a, b = _coerce_dt(s.get("start")), _coerce_dt(s.get("end"))
        if a is None or b is None or b <= a:
            kept.append(s)
            continue
        days = wdorm.split_day_windows(a, b)
        if not any(wdorm.is_dormant(eid, d_s.date(), dormant_events) for d_s, _d_e in days):
            kept.append(s)                       # 一天都不長停 ⇒ 原物件原樣留下（逐位元相同）
            continue
        for d_s, d_e in days:
            cut = dict(s)
            cut["start"], cut["end"] = d_s, d_e
            cut["ongoing"] = bool(s.get("ongoing")) and d_e == b
            (dropped if wdorm.is_dormant(eid, d_s.date(), dormant_events) else kept).append(cut)
    kept.sort(key=lambda s: (s["start"], s["work_order_no"]))
    dropped.sort(key=lambda s: (s["start"], s["work_order_no"]))
    return (kept, dropped)


def dormant_dropped_account(direct_dropped):
    """**直接**開在長停 EID 上被扣掉的段 → `{"sec": 聯集秒, "wo_nos": [單號…]}`／`None`。

       ★ **2026-08-18 Chris 裁決（WO 36045 實例）：帳只記「直接單」、母單遺傳不記。**
         母機工單遺傳給長停子工位是**結構必然**（母機在役、單開在母機＝完全合法；
         「遺傳」這個概念只存在 Board 的 A10 展開）——每張母機工單都替它產一行「已排除」
         ＝永久性噪音，讀起來像出了什麼事。含 scope「指名」到長停工位的段也一樣不記
         （現場圈範圍是常態操作、不是異常）。⇒ 遺傳段照扣（數字逐位元不變）、**帳面靜默**。
         舊版 `_inherited_dropped_segments`（重建遺傳被扣段供帳用）已隨本裁決整支移除。
       ★ 仍記帳的唯一情形＝**工單直接開在名冊內的長停 EID 上**——「登記說停用的機台
         有人開單」才是真異常、值得被看見（C-D3 的揭露留給它）。
       ★ 用 **interval-union** 而不是逐段相加（同機多張直接單可能重疊；拍板⑨同理）。
         `wo_nos` 升冪去重、供揭露行寫「工單 #N」。"""
    segs = list(direct_dropped or [])
    ivs = [(s["start"], s["end"]) for s in segs
           if isinstance(s, dict) and s.get("start") and s.get("end")]
    sec = _union_seconds(ivs)
    if sec <= 0:
        return None
    nos = sorted({s.get("work_order_no") for s in segs if s.get("work_order_no") is not None})
    return {"sec": sec, "wo_nos": nos}


def downtime_by_eid(wos_by_eid, win_start, win_end):
    """{eid: [normalize_wo 輸出]} → {eid: {"down_sec": union_countable_sec, "by_state": {state: sec}}}。
       每 EID 的 countable 停機＝**該機 countable segment 的 interval-union**（同機重疊工單不重複計；
       重用 wo_segments〔S12 分階段〕+ _union_seconds〔本檔 union helper〕，不重寫 S12 邏輯）。
       by_state＝各態 countable 原始加總（跨態重疊時 Σby_state 可 > down_sec＝口徑；供組成攤比例）。
       ★ 供 board_oee A↔U 真拆分消費（landing OEE Tier-2）：per-EID → 再 rollup 到站。純函式、零 I/O。"""
    out = {}
    for eid, wos in (wos_by_eid or {}).items():
        segs = wo_segments(wos, win_start, win_end)
        ivs, by_state = [], {}
        for s in segs:
            if not s.get("countable", True):          # uptime-hold（機台可生產）不計停機
                continue
            a, b = _coerce_dt(s.get("start")), _coerce_dt(s.get("end"))
            if a is None or b is None or b <= a:
                continue
            ivs.append((a, b))
            by_state[s["state"]] = by_state.get(s["state"], 0.0) + (b - a).total_seconds()
        out[eid] = {"down_sec": _union_seconds(ivs), "by_state": by_state}
    return out


# ---------------------------------------------------------------------------
# 3d) downtime_by_eid_scoped — **父機級消費者**的停機（A10 擴散 R1、2026-08-14）
# ---------------------------------------------------------------------------
#   Chris 2026-08-14 裁決取代 A10 拍板①（原「作用面只有 /equipment」）：
#   **凡指標涉及工單停機的頁都要反映。** 但父機級消費者（`/utilization` 的 A%/U% chip、
#   landing 的 A↔U 真拆分）的機隊成員是**父機那一台**（`fetch_fleet` 排模組）
#   ⇒ 它要問的不是「這個工位停多久」而是「這台**機器**整台停多久」。
#
#   ★ 兩件事、一支函式：
#     ① **母機 EID**（`wscope.PARENT_EXPANSION`）：countable 段先 ∩
#        `wscope.machine_scope_windows(wo)` 再 union ——範圍縮小到某幾個工位時，
#        其餘工位還在生產、機器不算整台停（**修正的是高估**）。
#     ② **任何 EID**：套 `dormant_split_segments` ——登記長停的工位不得再計 downtime
#        （甲的同一條規則；`/utilization`／landing 也要跟上，否則同一個事實三頁三個答案）。
#   ★ **既有 `downtime_by_eid` 一個位元不動**（本函式是新增、不是改寫）：
#     `dormant_events=[]` ∧ 無母機 EID ⇒ 本函式輸出與它**逐位元相同**（selftest 有釘）。
#   ★ **永不與子工位軸相加**：這條線只回父機級的答案；`/equipment` 的子工位列走
#     `_inherited_segments`。同一張單在兩軸各自回答自己的問題（見 `board_scope` 檔頭①）。


def wo_segments_scoped(eid, wos, win_start, win_end, dormant_events=None):
    """該 EID 的 `wo_segments` **機器視角**版（A10 擴散 R2、2026-08-16 自
       `downtime_by_eid_scoped` 抽出——**行為逐位元不變**，只是讓段級消費者
       〔`/daily` attrib 的 `wo_segs`／`/dispatch` 的 `down_eids`〕能吃同一套口徑）。

       ① `eid ∈ wscope.PARENT_EXPANSION`（母機）⇒ countable 段先 ∩
          `wscope.machine_scope_windows(wo)`——範圍縮小到某幾個工位時，其餘工位還在
          生產、機器不算整台停。段被裁掉尾巴 ⇒ `ongoing` 重算（不再是「現在仍停」）。
          ⚠ 母單須先帶好 `scope_events`（app 層 `_scope_src_with_events`）；沒帶＝
          legacy 整機＝高估側降級，與 `/equipment` 同一條紀律。
       ② 任何 EID ⇒ `dormant_split_segments`（登記長停的工位不計任何 downtime）。
          `dormant_events=None`＝過渡橋常數；`[]`＝關掉（**「此刻」型消費者
          〔`_now_block`／`down_eids`〕依 SPEC-DORMANT §5.1 ② 刻意傳 `[]`**——
          Now 答「此刻狀態」、停機統計答「算多少」，是兩個問題）。
       純函式、零 I/O、不 mutate 輸入。非母機 ∧ 無 dormant ⇒ 與 `wo_segments`
       **逐位元相同**（selftest N2 釘）。"""
    win_end_d = _coerce_dt(win_end)
    segs = wo_segments(wos, win_start, win_end)
    if eid in wscope.PARENT_EXPANSION:         # ① 機器視角：段 ∩ scope∋母機 的那些窗
        mw = {}
        for w in (wos or []):
            if isinstance(w, dict):
                mw[w.get("work_order_no")] = wscope.machine_scope_windows(w, win_end_d)
        clipped = []
        for s in segs:
            if not s.get("countable", True):
                clipped.append(s)
                continue
            for c_s, c_e in mw.get(s.get("work_order_no")) or []:
                a = s["start"] if s["start"] > c_s else c_s
                b = s["end"] if s["end"] < c_e else c_e
                if b <= a:
                    continue
                cut = dict(s)
                cut["start"], cut["end"] = a, b
                cut["ongoing"] = bool(s.get("ongoing")) and b == s["end"]
                clipped.append(cut)
        segs = sorted(clipped, key=lambda s: (s["start"], s["work_order_no"]))
    return dormant_split_segments(segs, eid, dormant_events)[0]         # ② 長停日不計


def downtime_by_eid_scoped(wos_by_eid, win_start, win_end, dormant_events=None):
    """`downtime_by_eid` 的**機器視角**版（形狀完全相同：`{eid: {down_sec, by_state}}`）。

       `dormant_events=None`＝過渡橋常數（與 `wdorm`／`wscope` 同一個慣例）；`[]`＝關掉。
       段級口徑收口在 `wo_segments_scoped`（R2 抽出；本函式只做 union）。
       純函式、零 I/O、不 mutate 輸入。"""
    out = {}
    for eid, wos in (wos_by_eid or {}).items():
        segs = wo_segments_scoped(eid, wos, win_start, win_end,
                                  dormant_events=dormant_events)
        ivs, by_state = [], {}
        for s in segs:
            if not s.get("countable", True):
                continue
            a, b = _coerce_dt(s.get("start")), _coerce_dt(s.get("end"))
            if a is None or b is None or b <= a:
                continue
            ivs.append((a, b))
            by_state[s["state"]] = by_state.get(s["state"], 0.0) + (b - a).total_seconds()
        out[eid] = {"down_sec": _union_seconds(ivs), "by_state": by_state}
    return out


# ---------------------------------------------------------------------------
# 3e) ★ R44（C-AV16，Fable 2026-09-29）：形③ 多座父機的停機＝在役座推導
# ---------------------------------------------------------------------------
#   事實（09-29 live）：父機 EID-24742 印 100%，而子座 24790／26820 整窗紅 0%——父機窗內只有
#   兩張 OPEN 未開始的 PM（`_phase_kind=none`），子座的 REACTIVE 單**沒有任何路徑**進父機。
#   裁決：**機台級不變**（單位仍是機台、父機仍以 1 台計、分母仍是它的 RW，C-AV1 不動）；
#   改的是父機「自己的停機」：
#       seat_down(s) ＝ (a) 座自己的單（`wo_segments_scoped`）∪ (b) 父機單展到座
#                      （`wscope.expand_for_children`＝REACTIVE 有 scope 只展 scope 內的座、
#                       無 scope／PM／調機整機 ⇒ 全部座；長停日已扣）——同座先 union 再 ∩ RW(s)。
#       parent_down  ＝ Σ_日 Σ_{s∈在役(日)} |seat_down(s) ∩ RW(s) ∩ 日| ÷ n_在役(日)
#   · 在役＝`board_capacity.active_units(parent, 日, events)`（逐日 as-of；**不用**
#     `wdorm.capacity_split`——兩份清單語意不同）。
#   · **只作用於 `wscope.PARENT_EXPANSION`**（形③）；單座機、形① nest、形② cotag 一律走原路徑、
#     逐位元不變（selftest 有負斷言）。
#   ★ 取代：`app.au_chips` docstring 的 2026-08-15「刻意濾掉子工位直接單」與
#     `SPEC-DORMANT-CAPACITY §12.3` 09-28「已裁不縮」的**父機停機來源**部分。
#   ★ 全 Board 同尺（QG-13）：`/availability` 列尾與頁級、`/utilization` A chip、站級三軸
#     （`app.rw_station_payload`）、日帳 `rw.avail_down_sec`、下鑽 `av_head`、landing 的 A↔U
#     真拆分——**全部呼叫本節同一支 `parent_seat_rollup`**，不得各自重算。


def seat_parent(eid):
    """形③ 座 EID → 其父機 EID；不是任何 `PARENT_EXPANSION` 的座 ⇒ None。純查表。"""
    for p, kids in wscope.PARENT_EXPANSION.items():
        if eid in kids:
            return p
    return None


def _wcap_events(dormant_events):
    """`dormant_events` 的 wdorm 慣例（None＝過渡橋常數）→ `board_capacity` 吃的 dict 列。

       ⚠ `board_capacity` 的 `events=None` 語意是「**沒有事件**」（selftest G9），與
       `board_dormant` 的「None＝橋常數」不同 ⇒ 不轉就會在 demo／退橋時把 26841／26887
       當成在役座（分母多兩座、半停變四分之一停）。這裡只做形狀轉換，不做任何判定。"""
    if dormant_events is not None:
        return dormant_events
    return [{"eid": r[0], "ts_effective": r[1], "state": r[2]} for r in wdorm.bridge_rows()]   # 唯一讀口（WP-4b R-C3）


def seat_down_segments(eid, wos_by_eid, win_start, win_end, dormant_events=None,
                       _inherited=None):
    """單一列的停機段（R44 §1.1／§1.5）。

       · 形③ 座 ⇒ (a) 自己的單（`wo_segments_scoped`）＋ (b) 父機單展到本座
         （`_inherited_segments(expand_for_children(...))`，只取 countable 段）；**不 union**
         ——union 留給消費端（`board_avail.machine_split`／`rollup_model`），同座兩條來源重疊
         天然只計一次。
       · 其他 EID ⇒ 與 `wo_segments_scoped` **逐位元相同**。
       `_inherited`＝呼叫端已算好的 `expand_for_children` 結果（同一父機多座時省重算）。純函式。"""
    own = wo_segments_scoped(eid, (wos_by_eid or {}).get(eid) or [], win_start, win_end,
                             dormant_events=dormant_events)
    parent = seat_parent(eid)
    if parent is None:
        return own
    if _inherited is None:
        _inherited = wscope.expand_for_children(
            {parent: (wos_by_eid or {}).get(parent) or []}, win_start, win_end,
            dormant_events=dormant_events)
    inh = _inherited_segments((_inherited or {}).get(eid) or [], _coerce_dt(win_start),
                              _coerce_dt(win_end))
    if not inh:
        return own
    return sorted(own + inh, key=lambda s: (s["start"], s["work_order_no"]))


def parent_active_days(parent, win_start, win_end, dormant_events=None):
    """→ `[(日起, 日迄, (在役座…))]`（台北日曆日切片、`wdorm.split_day_windows`；在役＝
       `board_capacity.active_units` 逐日 as-of）。非形③ 父機 ⇒ []。"""
    if parent not in wscope.PARENT_EXPANSION:
        return []
    ev = _wcap_events(dormant_events)
    return [(d_s, d_e, tuple(_wcap.active_units(parent, d_s.date(), ev)))
            for d_s, d_e in wdorm.split_day_windows(_coerce_dt(win_start), _coerce_dt(win_end))]


def parent_seat_rollup(parent, wos_by_eid, win_start, win_end, rw_by_eid=None,
                       dormant_events=None, down_states=None, order=(), erw_ctx=None):
    """★ R44 唯一出處：形③ 父機的停機＝在役座推導 → `board_avail.rollup_model` 的輸出
       （秒座標相對 `win_start`）＋ `parent`／`seats`／`seat_segs`（座 → datetime 段，
       座列直接重用、不另算）／`seat_offsets`。非形③ 或窗不合法 ⇒ None。

       `rw_by_eid`＝`{eid: [(s0, s1)]}`（相對 `win_start` 的秒）：
         · `None` ⇒ 24h 全日軸（父機與每座都是整個窗；`au_chips` 退路／landing 用）。
         · 有值 ⇒ 父機窗取 `rw_by_eid[parent]`（缺 ⇒ None：父機不在名冊＝C-RW9 不主張）；
           座窗取 `rw_by_eid[座]`，缺而給了 `erw_ctx` ⇒ `erw_machine_windows` 補算，
           再缺 ⇒ 與父機同窗（裁決 §1.1「與父機同窗」）。
       `down_states`＝白名單（`/availability`、日帳用 `RW_AVAIL_DOWN_STATES` 系）；
       `None` ⇒ 段裡的態全算（24h／事件視角消費者）。純函式、零 I/O。"""
    if parent not in wscope.PARENT_EXPANSION:
        return None
    ws, we = _coerce_dt(win_start), _coerce_dt(win_end)
    if ws is None or we is None or we <= ws:
        return None
    import board_avail as _wav                    # 區域 import：純模組、零相依環
    seats = tuple(wscope.PARENT_EXPANSION[parent])
    inh = wscope.expand_for_children({parent: (wos_by_eid or {}).get(parent) or []}, ws, we,
                                     dormant_events=dormant_events)
    seat_segs = {s: seat_down_segments(s, wos_by_eid, ws, we, dormant_events=dormant_events,
                                       _inherited=inh) for s in seats}
    seat_off = {s: _wav.segs_to_offsets(v, ws) for s, v in seat_segs.items()}
    span = (we - ws).total_seconds()
    if rw_by_eid is None:
        rw_parent, rw_seats = [(0.0, span)], {}
    else:
        if parent not in rw_by_eid:
            return None
        rw_parent = list(rw_by_eid.get(parent) or [])
        rw_seats = {s: list(rw_by_eid[s]) for s in seats if s in rw_by_eid}
        miss = [s for s in seats if s not in rw_seats]
        if miss and erw_ctx:
            for s, w in (erw_machine_windows(miss, ws, we, erw_ctx) or {}).items():
                rw_seats[s] = list(w.get("win") or [])
    days = [((d_s - ws).total_seconds(), (d_e - ws).total_seconds(), act)
            for d_s, d_e, act in parent_active_days(parent, ws, we, dormant_events)]
    roll = _wav.rollup_model(seat_off, days, rw_parent, rw_seats, down_states, order)
    roll.update({"parent": parent, "seats": seats, "seat_segs": seat_segs,
                 "seat_offsets": seat_off})
    return roll


def parent_seat_rollups(eids, wos_by_eid, win_start, win_end, rw_by_eid=None,
                        dormant_events=None, down_states=None, order=(), erw_ctx=None):
    """`eids` 中的形③ 父機逐台 `parent_seat_rollup` → `{父機: roll}`（算不出的不列）。"""
    out = {}
    for e in sorted({x for x in (eids or ()) if x in wscope.PARENT_EXPANSION}):
        r = parent_seat_rollup(e, wos_by_eid, win_start, win_end, rw_by_eid=rw_by_eid,
                               dormant_events=dormant_events, down_states=down_states,
                               order=order, erw_ctx=erw_ctx)
        if r is not None:
            out[e] = r
    return out


def downtime_by_eid_rollup(wos_by_eid, win_start, win_end, dormant_events=None):
    """`downtime_by_eid_scoped` 的 R44 版（形狀相同 `{eid: {down_sec, by_state}}`）：
       形③ 父機的那一格改成 24h 軸的在役座推導（`down_sec`＝加權 union、`by_state`＝
       加權逐態 union），並多一個加法鍵 `excl_sec`（全座長停日自分母排除的秒數）。
       父機即使自己沒有任何單、只要任一座有單也會出現。**其餘 EID 逐位元等於
       `downtime_by_eid_scoped`**（selftest 釘）。純函式。"""
    out = downtime_by_eid_scoped(wos_by_eid, win_start, win_end, dormant_events=dormant_events)
    w = wos_by_eid or {}
    for p, kids in wscope.PARENT_EXPANSION.items():
        if p not in w and not any(k in w for k in kids):
            continue
        r = parent_seat_rollup(p, w, win_start, win_end, rw_by_eid=None,
                               dormant_events=dormant_events)
        if r is None:
            continue
        out[p] = {"down_sec": r["down_sec"], "by_state": dict(r["by_state_union"]),
                  "excl_sec": r["excluded_rw_sec"]}
    return out


# ---------------------------------------------------------------------------
# 4) bucket_segments_by_day — segment 於台北午夜拆分入日欄（秒數守恆）
# ---------------------------------------------------------------------------

def bucket_segments_by_day(segments, day_wins):
    """工單 segment → {date_str: [seg]}。跨午夜 segment 於台北午夜拆分、每段 stop_sec 按裁切後
       重算（秒數守恆＝segment 完全落在 day_wins 覆蓋範圍時，各日裁段 stop_sec 總和＝原長）。
       裁段保留原 segment 全欄（含 state/source/work_order_no/ongoing），只覆寫 start/end/stop_sec。"""
    buckets = {ds: [] for ds, _, _ in day_wins}
    for seg in segments or []:
        if not isinstance(seg, dict) or seg.get("state") == "生產":
            continue
        s = _coerce_dt(seg.get("start"))
        e = _coerce_dt(seg.get("end"))
        if s is None or e is None or e <= s:
            continue
        for ds, d_start, d_end in day_wins:
            a = s if s > d_start else d_start
            b = e if e < d_end else d_end
            if b > a:
                cut = dict(seg)
                cut["start"], cut["end"] = a, b
                cut["stop_sec"] = (b - a).total_seconds()
                buckets[ds].append(cut)
    return buckets


# ---------------------------------------------------------------------------
# 5) day_cell — 單日格：停機時數(union) + 分態原始加總 + 主導態 + 工單號 + ongoing
# ---------------------------------------------------------------------------

def day_cell(segs_of_day):
    """→ {"stop_h", "by_state":{state:h}, "dominant", "wo_nos":[…], "ongoing", "by_uptime":{state:h}}。
       **stop_h/by_state/dominant/wo_nos 只計 countable 段（S12，2026-07-07）**：uptime-hold
       （機台可生產）段不入這些——但 2026-07-10 起另累積進 `by_uptime`（各 uptime 子類時數），
       供格底組成條在第一層現形（Chris 反饋 #4：原本只在 drill 色帶現形→長官沒點就錯過）。
       by_uptime 與該日窗裁切一致（segs_of_day 已由 bucket_segments_by_day 切好、帶 stop_sec）、
       與 by_state 互斥（countable vs 不計）。純 uptime 日＝stop_h 0 但 by_uptime 非空。
       stop_h＝該日 countable segments 的 **interval-union** 長度（重疊工單不重複計；裁決④）；
       by_state＝各態原始加總（跨態重疊時 Σby_state 可 > stop_h＝口徑、footer 講明）；
       dominant＝by_state 時數最大態。ongoing＝該日是否含延續至今的 countable 工單段。"""
    by_state, by_uptime = {}, {}
    wo_nos, ongoing = [], False
    ivs, extra_sec = [], 0.0                        # union 用 (start,end)；缺時戳者退加總
    for seg in segs_of_day or []:
        st = seg.get("state")
        if st == "生產":
            continue
        s, e = _coerce_dt(seg.get("start")), _coerce_dt(seg.get("end"))
        sec = seg.get("stop_sec")
        if sec is None:
            sec = (e - s).total_seconds() if (s and e and e > s) else 0.0
        if not seg.get("countable", True):          # uptime-hold（機台可生產）：入 by_uptime、不計停機
            if st in _UPTIME_STATES:
                by_uptime[st] = by_uptime.get(st, 0.0) + sec / 3600.0
            continue
        by_state[st] = by_state.get(st, 0.0) + sec / 3600.0
        if s and e and e > s:
            ivs.append((s, e))
        else:
            extra_sec += sec
        wno = seg.get("work_order_no")
        if wno is not None and wno not in wo_nos:
            wo_nos.append(wno)
        if seg.get("ongoing"):
            ongoing = True
    stop_h = (_union_seconds(ivs) + extra_sec) / 3600.0
    dominant = max(by_state, key=by_state.get) if by_state else None
    return {"stop_h": stop_h, "by_state": by_state, "dominant": dominant,
            "wo_nos": wo_nos, "ongoing": ongoing, "by_uptime": by_uptime}


# ---------------------------------------------------------------------------
# 6) availability — 窗內工單口徑 A%（24h 基準、G1 前近似）+ 昨日Δ
# ---------------------------------------------------------------------------

def availability(cells, day_wins):
    """窗內 Availability（工單口徑；裁決④）：A_win = 1 − Σ工單停機(union) / Σ窗時數（24h 基準；
       末欄 partial 按實際時數計）。stop_h 已 union → A%∈[0,100]。排班源未接前下班/週末算「可用」
       （`?`面板 tooltip 註明）。無任何工單的機台＝Σ停機 0 → A% 100.0（誠實：此頁只認有紀錄停機）。
       A_yd＝昨日（倒數第二欄完整日）之 1 − 停機/24；delta_pp = A_yd − A_win（百分點）。
       cells 全 None（不該發生於工單導向；防禦）→ (None,None,None)。"""
    denom_h = stop_h = 0.0
    any_cell = False
    for ds, d_s, d_e in day_wins:
        denom_h += (d_e - d_s).total_seconds() / 3600.0
        c = cells.get(ds)
        if c is not None:
            any_cell = True
            stop_h += (c.get("stop_h") or 0.0)
    if not any_cell or denom_h <= 0:
        return (None, None, None)
    a_win = 100.0 * (1.0 - stop_h / denom_h)
    a_yd = None
    if len(day_wins) >= 2:
        yd = cells.get(day_wins[-2][0])
        yd_h = (day_wins[-2][2] - day_wins[-2][1]).total_seconds() / 3600.0
        a_yd = 100.0 * (1.0 - ((yd.get("stop_h") or 0.0) if yd else 0.0) / yd_h)
    delta = (a_yd - a_win) if a_yd is not None else None
    return (a_win, a_yd, delta)


# ---------------------------------------------------------------------------
# 6b) 該跑窗（expected run window, erw）—— ROADMAP #39／`SPEC-RUNWINDOW.md` v1
# ---------------------------------------------------------------------------
#   ★ 2026-08-23 換軸（A3）：本節**取代** #36 的「實跑窗」並列欄——A%（妥善率）的分母
#     從 24h 換成**該跑窗**（班次窗 ∪ 三層階梯擴窗 − closure），單一真相、不並列。
#     舊詞「實跑窗／plant run-window」退役（C-RW2）；「班表／排班／schedule」照舊全面禁止。
#   ★ 口徑與階梯的**計算本體在 `board_runwindow`**（`board.runwindow.v1`；C-RW14 三層階梯、
#     C-RW4 永不取機台自身訊號、C-RW15 機種＝PLOVER 同列）——本節只做「板的接線」：
#     coverage 篩日（C-PC8）、逐列算妥善率與 time bar 材料、degraded 彙總。
#   ★★ C-PC11 迴歸釘仍在（QG-13）：同一組綑級輸入下停機 4h→20h ⇒ 分母恆定、值變差。
#   ★ C-PC12 兜底照舊：uncertain closure 對該綑不扣（`closures_with_uncertainty` 在
#     `board_data`，本檔只消費 `uncertain_eids`）。
#   ★ 適用面：**只對綑名冊機台成立**；非名冊（Zone D／Kestrel）顯「—」（C-RW9 不外推）。
#   ★ inert（§5.3）：無日曆／coverage 缺／零涵蓋日 ⇒ `erw_prepare` 回 None ⇒ 整頁退回
#     舊 24h 軸並顯著標示（`availability()` 保留正是為了這個 fallback）。

_KUN_ROSTER_EIDS = None


def kun_roster_eids():
    """綑名冊 EID 集合（＝日曆 quorum 母體）。

       唯一出處＝`_probe_kun_activity.KUN_ROSTER`（C-PC2／A4：Chris 人工維護）——本檔
       **不另抄一份綑結構**，只做 lazy 讀取（與 `board_data._kun_maps` 同一份事實；
       本檔不 import `board_data`＝它 import 本檔，反向會成環）。
       probe 未就緒 ⇒ 空 set＝誠實降級（並列欄全「—」、既有欄不受影響）。"""
    global _KUN_ROSTER_EIDS
    if _KUN_ROSTER_EIDS is None:
        try:
            import _probe_kun_activity as _ka
            _KUN_ROSTER_EIDS = frozenset(e for _k, _a, _n, es in _ka.KUN_ROSTER for e in es)
        except Exception:
            _KUN_ROSTER_EIDS = frozenset()
    return _KUN_ROSTER_EIDS


def erw_prepare(day_wins, closures, cal_coverage, slots_raw=None, mp_raw=None):
    """該跑窗（expected run window）建構 context（`SPEC-RUNWINDOW.md` v1；#39）。

       → ctx dict 或 **None＝整框 inert**（§5.3：無日曆／coverage 缺／零涵蓋日 ⇒ 頁面退回
       舊 24h 軸並顯著標示——寧可退回，也不出「週日主張該跑 16.7h」那種數字）。
       · `closures`＝`closures_with_uncertainty` 輸出（None＝沒有日曆＝inert）。
       · `cal_coverage`＝`(from, to)` 台北牆鐘；只有**完整**落在其中的欄日被主張（C-PC8）。
       · `slots_raw`／`mp_raw`＝`board_data.load_kun_slots`／`load_kun_manpower` 的 doc
         （None＝該源缺席 ⇒ 擴窗逐日降級、**框不 inert**——降級形＝ShiftWin−Closure＋標示）。
       純函式、零 I/O。"""
    if closures is None or not cal_coverage:
        return None
    cf, ct = cal_coverage
    if cf is None or ct is None:
        return None
    days = {}
    for ds, d_s, d_e in day_wins:
        if d_e <= d_s:
            continue                                     # 零長度末欄（win_end 恰在日界上）
        if not (d_s >= cf and d_e <= ct):
            continue                                     # C-PC8：不完整涵蓋的欄日不主張
        # ★ #182-P1（2026-09-25，`_INVEST-RW-DENOMINATOR-20260925.md` §1／§3.1-2）：
        #   `day_start` 一律＝**該欄所屬生產日的 08:00**、`day_iso`＝**生產日**日期——
        #   **永不**存欄的（可能被裁過的）起點 `d_s`。`run_window` 的分鐘座標「0＝08:00」
        #   （`board_runwindow` 檔頭），下游 `erw_machine_windows`／`erw_full_seconds` 拿
        #   `day_start` 當那個 0 點、再用自己的 `lo/hi` 裁到欄緣。舊寫法把殘欄起點（例：
        #   日曆日窗的 00:00、滾動窗的 20:30）當 0 點 ⇒ 班次窗整段平移、殘欄吃到錯的
        #   slots／manpower 日 ⇒ 回補日帳 ZAB 1,440 分／台、稼動三頁首欄分母偏寬。
        #   C-PC8 涵蓋判定**仍看欄本身** `[d_s, d_e)`（只主張真正用到的那一段）。
        #   `day_end` 照舊＝欄尾（無讀者；保留鍵形）。
        _pd = d_s.date() if d_s.hour >= PROD_DAY_START_H else d_s.date() - _dt.timedelta(days=1)
        anchor = _dt.datetime(_pd.year, _pd.month, _pd.day, PROD_DAY_START_H)
        day_iso = f"{_pd.year:04d}-{_pd.month:02d}-{_pd.day:02d}"
        days[ds] = {"day_start": anchor, "day_end": d_e,
                    "slots": (wrw.decode_day_slots(slots_raw, day_iso)
                              if slots_raw is not None else None),
                    "mp": (wrw.manpower_day(mp_raw, day_iso)
                           if mp_raw is not None else None)}
    if not days:
        return None
    return {"days": days, "closures": list(closures)}


def _erw_stop_iv(bucket_day, day_start):
    """該日 countable 停機段 → 生產日分鐘 interval 清單（union 過；「生產」態不算停機）。"""
    ivs = []
    for seg in bucket_day or ():
        if not seg.get("countable", True) or seg.get("state") == "生產":
            continue
        a, b = _coerce_dt(seg.get("start")), _coerce_dt(seg.get("end"))
        if a is None or b is None or b <= a:
            continue
        ivs.append(((a - day_start).total_seconds() / 60.0,
                    (b - day_start).total_seconds() / 60.0))
    return wrw.iv_union(ivs)


def _erw_stop_pieces(bucket_day, day_start, iv):
    """該日 countable 停機段 **逐段**裁到 `iv` → [(state, lo, hi)…] 分鐘座標（未跨段合併）。
       ★ 2026-08-23 晚 Chris 裁決①③（SPEC-RUNWINDOW §6.3）：格子與 time bar 的態色都要
         「窗內值」⇒ 這支是那兩處共用的裁切器（**不是**新的一份口徑：篩選條件與
         `_erw_stop_iv` 逐字同款，差別只在「保留態、不 union」）。"""
    out = []
    for seg in bucket_day or ():
        if not seg.get("countable", True) or seg.get("state") == "生產":
            continue
        a, b = _coerce_dt(seg.get("start")), _coerce_dt(seg.get("end"))
        if a is None or b is None or b <= a:
            continue
        lo = (a - day_start).total_seconds() / 60.0
        hi = (b - day_start).total_seconds() / 60.0
        st = seg.get("state") or "維修"
        for xlo, xhi in wrw.iv_intersect([(lo, hi)], iv):
            out.append((st, xlo, xhi))
    return out


def _erw_by_state(bucket_day, day_start, iv):
    """該日**窗內**逐態停機時數 {state: h}（各態先自我 union、再 ∩ 窗 ⇒ 同態重疊不雙計）。
       ⚠ 與 `day_cell` 的 `by_state`（24h 軸、原始加總）**刻意不同源**：那是工單事實、
       這是窗內裁切值。cells 資料結構一個位元不動，格子只是改讀這一份（§6.3 ①）。"""
    per = {}
    for st, lo, hi in _erw_stop_pieces(bucket_day, day_start, [(-1e9, 1e9)]):
        per.setdefault(st, []).append((lo, hi))
    out = {}
    for st, ivs in per.items():
        m = wrw.iv_total(wrw.iv_intersect(wrw.iv_union(ivs), iv))
        if m > 0:
            out[st] = m / 60.0
    return out


def erw_row(eid, buckets, day_wins, ctx):
    """單列該跑窗計算 → dict 或 **None＝非名冊**（C-RW9 不適用 ⇒ 呼叫端顯「—」）。

       · 妥善率＝100×(1 − Σ(停機∩RW) / Σ|RW|)，只累加「有涵蓋 ∧ |RW|>0」的欄日
         （C-RW10：|RW|=0 的日子不進分母、也不進分子）。
       · Δpp＝最新**完整**生產日的單日值 − 整窗值（與舊軸同語意、兩邊都是該跑窗軸）。
       · `days[ds]`＝逐日 {h, stop_h, by_state（窗內逐態）, iv, gaps, complete}
         ——**格子（cells 顯示層）、drill、bar 三處共用這一份**（§6.3 ①②③）。
       · `bar`＝§6.2 ② 妥善率 time bar 的材料。★ **2026-08-23 晚 Chris 裁決③：整窗連排**
         （原本只畫最新完整生產日＝列尾一根 bar 只講一天、與同列七個日格對不上）：
         座標基底＝`len(day_wins) × 1440` 分鐘、逐欄日 offset 連排；
         `win`＝各涵蓋日的該跑窗段、`stop`＝(state, l, w) 窗內停機片段、
         `ticks`＝日界百分比、`uncov`＝**日曆未涵蓋日**整日區段（留白＝「無主張」，
         與「窗外沉灰＝不該跑」**必須看得出不同**）、`n_days`／`h`＝Σ窗時數。
         ⚠ 涵蓋但 |RW|=0 的日**不進 `uncov`**（那是「不該跑」＝純沉灰，主張明確）。
         **與本列妥善率同一份資料**（QG-13 有釘：bar 與 A% 永不各算各的）。
       · 逐空隙段階梯紀錄（A10 揭露）住 `days[ds]["gaps"]`（bar 不再另存一份）。"""
    if not wrw.kuns_of(eid):
        return None
    win_h = stop_h = 0.0
    days, degraded = {}, set()
    for ds, d_s, d_e in day_wins:
        info = (ctx.get("days") or {}).get(ds)
        if info is None:
            continue
        cm = wrw.closures_to_day_min(ctx.get("closures"), d_s, eid)
        rw = wrw.run_window(eid, info["slots"], info["mp"], cm)
        elapsed = (d_e - d_s).total_seconds() / 60.0
        iv = wrw.iv_intersect(rw["iv"], [(0.0, elapsed)])   # 末欄 partial 裁到 win_end
        mins = wrw.iv_total(iv)
        degraded.update(rw["degraded"])
        stop_iv = _erw_stop_iv((buckets or {}).get(ds), d_s)
        s_in = wrw.iv_total(wrw.iv_intersect(stop_iv, iv))
        days[ds] = {"h": mins / 60.0, "stop_h": s_in / 60.0, "iv": iv, "gaps": rw["gaps"],
                    # §6.3 ①：格子改讀「窗內逐態」⇒ 這一份必須與 stop_h 同窗同源
                    "by_state": _erw_by_state((buckets or {}).get(ds), d_s, iv),
                    # 逐日產出當量（§6.3 ①）＝ **`build_row` 才補得起來**（rate 在那裡）；
                    # 這裡先放 None ⇒ 鍵恆存在、模板不必分辨「沒鍵」與「不換算」。
                    "equiv_ph": None,
                    "complete": (d_e - d_s) == _dt.timedelta(days=1)}
        if mins <= 0:
            continue                                       # C-RW10：|RW|=0 不進任何和
        win_h += mins / 60.0
        stop_h += s_in / 60.0
    avail = (100.0 * (1.0 - stop_h / win_h)) if win_h > 0 else None
    # Δpp＝最新**完整**生產日單日值 − 整窗值（兩邊同軸；基準標示＝§6.3 ④ 的小字）
    delta = None
    for ds, d_s, d_e in reversed(day_wins):
        d = days.get(ds)
        if not (d and d.get("complete") and d["h"] > 0):
            continue
        if avail is not None:
            delta = 100.0 * (1.0 - d["stop_h"] / d["h"]) - avail
        break
    # §6.2 ②／§6.3 ③ time bar：**整窗連排**（座標基底＝n_days × 1440 分鐘）
    bar = None
    if day_wins:
        span = len(day_wins) * 1440.0

        def _p(x):
            return round(x / span * 100.0, 3)

        win_seg, stop_seg, uncov, ticks = [], [], [], []
        for i, (ds, d_s, _d_e) in enumerate(day_wins):
            off = i * 1440.0
            if i:
                ticks.append(_p(off))
            d = days.get(ds)
            if d is None:
                # 日曆未涵蓋 ⇒ 「無主張」，**不是**「不該跑」⇒ 模板畫成留白/點紋、非沉灰
                uncov.append((_p(off), _p(1440.0)))
                continue
            for lo, hi in d["iv"]:
                win_seg.append((_p(off + lo), _p(hi - lo)))
            for st, lo, hi in _erw_stop_pieces((buckets or {}).get(ds), d_s, d["iv"]):
                stop_seg.append((st, _p(off + lo), _p(hi - lo)))
        bar = {"n_days": len(day_wins), "h": win_h,
               "win": win_seg, "stop": stop_seg, "ticks": ticks, "uncov": uncov}
    return {"avail": avail, "delta": delta, "window_h": win_h, "stop_h": stop_h,
            "days": days, "degraded": sorted(degraded), "bar": bar}


def prod_day_windows(win_start, win_end):
    """任意窗 → 生產日欄 [(ds, d_s, d_e), …]（08:00 界；首末欄裁到窗緣）。
       `/utilization` 的窗級該跑窗用（它的窗是使用者自選的、不是 n_days 形）。"""
    ws, we = _coerce_dt(win_start), _coerce_dt(win_end)
    if ws is None or we is None or we <= ws:
        return []
    d0 = ws.date() if ws.hour >= PROD_DAY_START_H else ws.date() - _dt.timedelta(days=1)
    out, cur = [], _dt.datetime(d0.year, d0.month, d0.day, PROD_DAY_START_H)
    while cur < we:
        nxt = cur + _dt.timedelta(days=1)
        a, b = max(cur, ws), min(nxt, we)
        if b > a:
            out.append((f"{cur.month:02d}-{cur.day:02d}", a, b))
        cur = nxt
    return out


def prod_day_window(day=None, *, now=None):
    """生產日 D → `(D 08:00, D+1 08:00)`（台北 naive）＝**日帳／回補／`/daily` 的單日窗唯一出處**。

       · `day`＝`'YYYY-MM-DD'` 或 `date`＝呼叫端已決定的生產日（`_backfill_ledger` 逐日回補）。
         ⚠ 不收 `datetime`：「某時刻屬哪個生產日」與「某日期就是生產日」是兩件事，混收會
         讓 09-15 03:00 這種輸入默默錯一天——要從時刻推請傳 `now=`。
       · `day=None` ⇒ **最新一個完整生產日**（`now` 所在生產日的前一個；`now` 預設牆鐘）
         ＝`app.daily_body` 的「昨日」（A6：清晨 0–8 點仍屬前一生產日）。
       ★ #182-P1（2026-09-25）：A6（08-24）把 live 換成生產日窗時，回補 runner 自己留了一份
         日曆日窗 `[D 00:00, D+1 00:00)` 沒跟上 ⇒ 回補日帳分母≈24h（`_INVEST-RW-DENOMINATOR-
         20260925.md` §1）。兩條路徑從此都問這一支，口徑換版只能換一處。"""
    if day is None:
        n = now if now is not None else _dt.datetime.now()
        pd = n.date() if n.hour >= PROD_DAY_START_H else n.date() - _dt.timedelta(days=1)
        pd = pd - _dt.timedelta(days=1)
    elif isinstance(day, _dt.datetime):
        raise TypeError("prod_day_window(day=) 不收 datetime；從時刻推生產日請用 now=")
    elif isinstance(day, _dt.date):
        pd = day
    else:
        pd = _dt.datetime.strptime(str(day), "%Y-%m-%d").date()
    y0 = _dt.datetime(pd.year, pd.month, pd.day, PROD_DAY_START_H)
    return y0, y0 + _dt.timedelta(days=1)


def erw_full_seconds(eids, win_start, win_end, ctx):
    """窗級該跑窗分母（`/utilization`；`SPEC-RUNWINDOW` §4.2）→ (總秒數, meta)。

       每台機的分母＝窗長 − Σ(涵蓋生產日內的**非該跑窗**時間)。⚠ **coverage 外期間視為
       「該跑」照樣進分母＝刻意的低估側**（同 #36 `availability_pw_window` 的保守選擇；
       呼叫端必須隨讀數附 caveat）。ctx＝`erw_prepare` 輸出（它的 `days` 鍵是 MM-DD、
       day_start＝生產日 08:00——本函式用自己的 `prod_day_windows` 對齊同一把尺）。
       eids 應已是**本站機隊 ∩ 綑名冊**（名冊外不進、C-RW9）。"""
    ws, we = _coerce_dt(win_start), _coerce_dt(win_end)
    total = 0.0
    n_cov = 0
    dws = prod_day_windows(ws, we)
    for eid in sorted(set(eids or ())):
        sec = (we - ws).total_seconds()
        for ds, a, b in dws:
            info = (ctx.get("days") or {}).get(ds)
            if info is None:
                continue
            d_s = info["day_start"]
            cm = wrw.closures_to_day_min(ctx.get("closures"), d_s, eid)
            rw = wrw.run_window(eid, info["slots"], info["mp"], cm)
            if rw is None:
                continue                     # 防禦（呼叫端已濾名冊；C-RW9）
            lo = (a - d_s).total_seconds() / 60.0
            hi = (b - d_s).total_seconds() / 60.0
            in_rw = wrw.iv_total(wrw.iv_intersect(rw["iv"], [(lo, hi)]))
            sec -= ((hi - lo) - in_rw) * 60.0            # 扣掉「涵蓋日內的非該跑窗」
        total += max(0.0, sec)
    n_cov = sum(1 for ds, _a, _b in dws if ds in (ctx.get("days") or {}))
    return total, {"n_days": n_cov, "n_days_total": len(dws)}


# ── §6.5（2026-08-24）：`/utilization`／`/station`／`/machine` 換軸的兩支材料函式 ────────
#   規格＝`SPEC-RUNWINDOW.md` §6.5（本輪裁決存證）。`/equipment` 走 `erw_row`（逐日格），
#   稼動側的三頁走這兩支：**同一個 `erw_prepare` ctx、同一把尺、零第二份口徑**。
#   ★ QG-13「bar 與讀數永不各算各的」在本節的形＝
#     `Σ erw_machine_windows(...)[eid]["sec"] ≡ erw_full_seconds(...)`（selftest 釘死）。
#     ⇒ **未涵蓋日照 `erw_full_seconds` 的既有保守選擇「視為該跑」計入分母**（低估側），
#     但另存 `uncov` 區段讓呈現層畫成「無主張」（留白/點紋）——
#     「不知道」與「不該跑」責任完全不同，**永不同色**（§6.3 ③ 反模式 2）。
UNEXPLAINED_STATE = "該跑沒跑"          # §23 canonical：應生產時段內、沒有生產也沒有工單解釋


def erw_machine_windows(eids, win_start, win_end, ctx):
    """逐機的**絕對座標**該跑窗材料（`/utilization`／`/station`／`/machine` 的機台帶與圓餅共用）。

       → `{eid: {"win": [(s0,s1)…], "uncov": [(s0,s1)…], "marks": [s…], "sec": float,
                 "span": float}}`，座標＝**距 `win_start` 的秒數**（模板換算百分比用）。
       · 非名冊 EID **不出現在回傳裡**（C-RW9 不適用 ⇒ 呼叫端顯「—」、永不外推）。
       · `marks`＝該跑窗段的邊界（收工時間／延長端點）＝`/equipment` drill 的同一套刻度語意
         （日界與窗緣本身不畫）。
       · `uncov`＝日曆未涵蓋的生產日整段（**同時**在 `win` 裡＝計入分母的保守選擇，見上）。
       純函式、零 I/O。ctx＝`erw_prepare` 輸出；ctx None ⇒ 回 {}（呼叫端整框 inert）。"""
    ws, we = _coerce_dt(win_start), _coerce_dt(win_end)
    out = {}
    if ws is None or we is None or we <= ws or not ctx:
        return out
    dws = prod_day_windows(ws, we)
    cdays = ctx.get("days") or {}
    span = (we - ws).total_seconds()
    for eid in sorted({e for e in (eids or ()) if e}):
        if not wrw.kuns_of(eid):
            continue                                     # C-RW9：非名冊 ⇒ 不主張
        win, uncov, marks = [], [], []
        for ds, a, b in dws:
            lo_s, hi_s = (a - ws).total_seconds(), (b - ws).total_seconds()
            if hi_s <= lo_s:
                continue
            info = cdays.get(ds)
            rw = None
            if info is not None:
                cm = wrw.closures_to_day_min(ctx.get("closures"), info["day_start"], eid)
                rw = wrw.run_window(eid, info["slots"], info["mp"], cm)
            if info is None or rw is None:
                # C-PC8 未涵蓋：分母照 `erw_full_seconds` 的既有保守選擇計入，呈現層另標「無主張」
                win.append((lo_s, hi_s))
                uncov.append((lo_s, hi_s))
                continue
            base = (info["day_start"] - ws).total_seconds()
            for lo, hi in rw["iv"]:
                s0 = max(base + lo * 60.0, lo_s)
                s1 = min(base + hi * 60.0, hi_s)
                if s1 > s0:
                    win.append((s0, s1))
                    for x in (s0, s1):
                        if lo_s < x < hi_s:              # 日界與窗緣本身不是收工線
                            marks.append(x)
        win = wrw.iv_union(win)
        out[eid] = {"win": win, "uncov": wrw.iv_union(uncov), "marks": sorted(set(marks)),
                    "sec": wrw.iv_total(win), "span": span}
    return out


def erw_state_split(machines, win_start, win_end, ctx, mwin=None):
    """機隊圓餅／組成換軸（§6.5）：**應生產時段內**的逐態秒數。

       → `{"by_state": {態: 秒}, "rw_sec", "prod_sec", "unexp_sec", "n_machines",
            "n_days", "n_days_total", "eids"}`；ctx 缺／零名冊交集 ⇒ **None**（呼叫端退舊軸）。
       口徑三條（**這一節是規格的機械形，改動前先讀 SPEC-RUNWINDOW §6.5**）：
         1. 分母＝`Σ_m |RW(m)|`（＝`erw_full_seconds` 同值，selftest 釘死）。
         2. `不明`（以及 record 未覆蓋的殘量）∩ RW → **`該跑沒跑`（未解釋）**：換軸之後
            「不知道為什麼沒跑」與「本來就不該跑」不再混在同一塊——後者根本不在分母裡。
         3. **「未排生產」一秒都不進 `by_state`**：它不是一個態、是分母外的時間；
            進了圓餅就等於沒換軸（回到 24h 軸只是換了個名字）。
       `machines`＝`data["machines"]`（records 已疊工單 overlay）；records 不重疊（timeline 形）。"""
    ws, we = _coerce_dt(win_start), _coerce_dt(win_end)
    if ws is None or we is None or we <= ws or not ctx:
        return None
    if mwin is None:
        mwin = erw_machine_windows([m.get("eid") for m in (machines or ())], ws, we, ctx)
    if not mwin:
        return None
    by_state, rw_sec, eids = {}, 0.0, []
    for m in machines or ():
        w = mwin.get(m.get("eid"))
        if w is None:
            continue                                     # 名冊外：不進分母、也不進分子（C-RW9）
        eids.append(m.get("eid"))
        rw_sec += w["sec"]
        acc = 0.0
        for r in (m.get("records") or ()):
            a, b = _coerce_dt(r.get("start")), _coerce_dt(r.get("end"))
            if a is None or b is None or b <= a:
                continue
            sec = wrw.iv_total(wrw.iv_intersect(
                [((a - ws).total_seconds(), (b - ws).total_seconds())], w["win"]))
            if sec <= 0:
                continue
            st = r.get("state") or "不明"
            if st == "不明":
                st = UNEXPLAINED_STATE                   # 口徑 2
            by_state[st] = by_state.get(st, 0.0) + sec
            acc += sec
        resid = w["sec"] - acc
        if resid > 1e-6:                                 # record 未覆蓋的殘量也是「該跑沒跑」
            by_state[UNEXPLAINED_STATE] = by_state.get(UNEXPLAINED_STATE, 0.0) + resid
    if not eids:
        return None
    dws = prod_day_windows(ws, we)
    return {"by_state": by_state, "rw_sec": rw_sec, "n_machines": len(eids),
            "prod_sec": by_state.get("生產", 0.0),
            "unexp_sec": by_state.get(UNEXPLAINED_STATE, 0.0),
            "n_days": sum(1 for ds, _a, _b in dws if ds in (ctx.get("days") or {})),
            "n_days_total": len(dws), "eids": sorted(eids)}


def closures_in_window(closures, win_start, win_end):
    """closure 清單裁到 `[win_start, win_end)`（drill 疊層用；`uncertain_eids` 原樣帶過）。"""
    out = []
    for c in closures or ():
        a, b = _coerce_dt(c.get("start")), _coerce_dt(c.get("end"))
        if a is None or b is None:
            continue
        a = a if a > win_start else win_start
        b = b if b < win_end else win_end
        if b > a:
            out.append({"start": a, "end": b,
                        "uncertain_eids": tuple(c.get("uncertain_eids") or ())})
    out.sort(key=lambda c: (c["start"], c["end"]))
    return out


# ---------------------------------------------------------------------------
# 7) build_row — 單台設備列（工單停機格盤 + 現在 Up/Down 徽章）
# ---------------------------------------------------------------------------

def wo_current(rec):
    """normalized WO → (current_status, current_hold_reason)（drill 工單卡『現在暫停原因』用）。
       current＝status_history 末條（normalize_wo 已按 at 排序）；空 history（遷移單）→(None,None)。
       **hold_reason 只在末條 status==ON_HOLD 時回傳**——完工單的歷史 hold 不是『現在暫停』
       （task 2026-07-07：completed WO's historical hold is not on hold now）。純函式、零 I/O。"""
    hist = rec.get("status_history") or []
    if not hist:
        return (None, None)
    last = hist[-1]
    st = last.get("status")
    hr = last.get("hold_reason") if st == "ON_HOLD" else None
    return (st, hr)


def _now_inherited(inherited, win_end):
    """A10：此刻是否因**母機工單**而停（Now 欄的第①b 順位）→ `{"wo_no","state","parent"}`／None。

       條件三合一（缺一不可）：①母單活躍窗涵蓋 `win_end`；②母單**末階段**
       `_phase_kind=='count'`（與 ① 同一支判定，PM 只 OPEN 未處理＝機台還在跑＝非 DOWN）；
       ③`win_end` 落在該子工位的某個 clip 內（＝此刻它在 effective scope 裡）。
       ★ 因為 dormant 日已在 clip 構造期扣掉（`board_scope.child_clips`），
         **2026-08-14 翻面後長停工位的 clip 全空**（繼承與指名皆扣）⇒ **母單再也不會把一個
         長停工位釘成 DOWN** ⇒ `SPEC-DORMANT-CAPACITY.md` §5 自動成立、這裡不需要也不得
         再寫一次 dormant 判定（第二份 predicate＝漂移的種子）。
         ⚠ 對照：**直接開在長停工位上的單仍會讓 Now 顯 DOWN**（`_now_block` 未隨此裁決改動
         ——Now 欄答「此刻狀態」、停機統計答「算多少」，是兩個問題；見 `_now_block` docstring）。"""
    for ent in inherited or []:
        if not isinstance(ent, dict):
            continue
        wo = ent.get("wo")
        clips = ent.get("clips") or []
        if not isinstance(wo, dict) or not clips:
            continue
        if w8.wo_is_cancelled(wo):       # 母單取消 ⇒ 子工位不繼承 DOWN（拍板①、與 wo_segments 同判定）
            continue
        if not any(c[0] <= win_end <= c[1] for c in clips):
            continue
        win = w8.wo_active_window(wo, now=win_end)
        if win is None:
            continue
        ws, we = win
        if not (ws <= win_end and (we is None or we > win_end)):
            continue
        a_end_eff = win_end if we is None else _coerce_dt(we)
        phases = _phase_timeline(wo, _coerce_dt(ws), a_end_eff, we is None)
        if not phases:
            continue
        cur = phases[-1]
        if _phase_kind(cur["status"], cur["hold_reason"], wo.get("work_type")) != "count":
            continue
        state, _conf, _canon = w8.wo_state(wo.get("work_type"))
        if cur["status"] == "ON_HOLD" and cur["hold_reason"] in HOLD_DOWN_STATES:
            state = HOLD_DOWN_STATES[cur["hold_reason"]]
        return {"wo_no": wo["work_order_no"], "state": state, "parent": ent.get("parent_eid")}
    return None


def _now_mes_fields(mes_available, mes_any_last_h, mes_last_designation, mes_desig_available):
    """Additive evidence fields shared by every Now branch, including DOWN."""
    return {"mes_any_last_h": mes_any_last_h if mes_available else None,
            "mes_last_designation": mes_last_designation if mes_available else None,
            "mes_desig_available": bool(mes_available and mes_desig_available)}


def _sub_units_field(sub_units):
    """⑥ 子工位（2026-09-08 additive；`SPEC-LANDING.md` §18.23 / C-LD40）。

    `sub_units`＝`[{"unit": "nest:3"|"EID-05707", "last_h": float|None}, …]`（呼叫端已排好序）。
    · **空／None ⇒ 整個鍵不存在** ⇒ 沒有子工位的機台，`now` 逐位元同加此欄前。
    · 本函式**不做任何判斷**：不決定顏色、不比門檻、不補值——那是顯示層
      （`app.ld_subunit_cells`）的事，而且那條規則只有一份。板只負責把事實帶上來。
    """
    return {"sub_units": [dict(u) for u in sub_units]} if sub_units else {}


def _now_block(wos, win_end, mes_last_h=None, mes_available=False, quiet_h=QUIET_H,
               dormant=None, inherited=None, mes_covered=False, mes_any_last_h=None,
               mes_last_designation=None, mes_desig_available=False, sub_units=None):
    """『現在』多源裁決（2026-07-10 Chris：MES 是加分不是門檻）＝WO 階段化(S12) → MES 升級標籤。
       ① 任一工單活躍窗涵蓋 win_end **且此刻階段＝算停機(count)** → DOWN（PM 只 OPEN 未處理＝機台
          還在跑＝非 DOWN；ON_HOLD 等機台空檔＝非 DOWN；此階段判斷比 /updown 的『有 open 單即 DOWN』
          更精準、保留）。
       ①b **長停（dormant）＝第三態**（2026-08-09、A11 過渡橋；規格＝`SPEC-DORMANT-CAPACITY.md`
          §3.2）：`dormant`＝該 EID 的 `wdorm.dormant_info(...)`（登記的事實，非推導）→
          bucket/verdict 皆 "dormant"、中性色、A% 顯「—」。
          ★ **順序：DOWN 勝過 dormant**（①在①b 之前）——「有計停機工單直接壓在這台上」＝現在
            真的有人在修它，那是**此刻的狀態**、該看得見。
            ⚠⚠ **2026-08-14 Chris 裁決後這條的理由要重寫**：舊註解援引 SPEC §5「事實 > 推論」
              ——**那條已翻面**（登記長停的工位不計任何停機、explicit 也扣）。這裡之所以**仍然**
              保留 DOWN 勝，理由換成：**Now 欄答的是「此刻是什麼狀態」，不是「算多少停機」**。
              停機統計那一軸已在 `build_row` 走 `dormant_split_segments` 全數排除、並出揭露行；
              板上的完整故事＝「Now DOWN（有人在修）＋ heatmap 0（登記長停、不計）＋
              揭露行（Xh 已排除）」⇒「修一台停用機」不會隱形。
              **這是刻意保留的不一致面、不是漏改**（真要跟著翻＝另一次拍板，見 selftest G2-5）。
          ★ **stale 強制 False**：⚠ 久靜的意思是「這台不該這麼久沒動、值得問一句」；長停工位
            本來就不排生產，掛 ⚠ 等於每天提醒一件已經登記過的事＝噪音。
       ② 無 count 工單、非長停 → **一律 UP**（沒維修單＝預設可用）。MES 只決定顯示哪個標籤：
            mes_available 且 age ≤ ud.MES_RECENT_H → verdict "up_producing"（MES 確認在跑）；
            其餘（age 超門檻／無 age／探測失敗）  → verdict "up_idle"（待命；有 age 就附小字）。
          **絕不因 MES 沉默而判 DOWN 或 UNKNOWN**（gap≠fault；07-09 首版的 UNKNOWN 已撤，滿屏灰）。
          ⚠ **dormant ≠ 當年撤掉的 UNKNOWN**——UNKNOWN 是「MES 沉默 ⇒ 猜這台可能有事」（滿屏灰、
            零資訊、已撤）；dormant 是**登記的事實**（誰、哪天、什麼原因，SPEC §1/§6.1），
            至多兩三台。**勿當 UNKNOWN 復辟而撤除。**
       ③ stale ⚠：mes_available 且 age 有值且 > quiet_h → True（**狀態仍 UP**、只是值得問一句）。
          無 age＝分不出「本就不寫 MES」vs「久到超出探測窗」→ stale False（不猜）。
       ④ mes_nosig（2026-08-17 三形定案；只在 up_idle 分支長出）：mes_covered＝這台在證據源的
          **涵蓋範圍內**（lastprod 狀態檔源＝整份名冊都涵蓋、值 None＝「整窗掃過、無生產段」；
          探測 fallback 源＝只有拿到 age 的機台涵蓋 ⇒ nosig 結構上不可能 True）。
          covered ∧ age None ⇒ nosig True＝顯中性小字「近 N 天無生產」、**不掛 ⚠**
          （2026-08-17 實測有大量台 >60d 無訊號——這批恆 ⚠＝滿版警示＝稀釋 ⚠；
          長期不跑是穩定事實不是異常，⚠ 留給「平常有跑、最近安靜」的 QUIET_H–30d 帶；R45 前是 3d–30d）。
       ⑤ mes_age_h（2026-09-07 additive；**所有分支都帶**）＝`mes_last_h if mes_available else None`
          ＝**這次探測拿到的原始年齡**，不看 bucket、不做任何升級判斷。
          ⚠ **與 `mes_last_h` 是兩件事、不可互換**：`mes_last_h` 的語意是「規則②要用來升級 UP
            標籤的那個訊號」——DOWN 兩個分支（自身工單／A10 母單展開）刻意回 None，因為那兩形
            根本沒走規則②，講一個沒被用到的數字只會讓下游以為它參與了裁決。
            但**維修中的機台也有 MES 年齡**（`wo_mes_conflict` 吃的正是同一個入參），
            而 `SPEC-LANDING` §18.17 B3 的「MES 訊號」欄要的就是這些機台 ⇒ 另開一個鍵照實回，
            **`mes_last_h` 的語意與值一個位元不動**（`/equipment` 與 `/updown` 都在吃它）。
       → {"bucket":"down"|"up"|"dormant", "verdict":"down"|"up_producing"|"up_idle"|"dormant",
          "wo_no", "state", "mes_last_h", "mes_age_h", "stale"}；up_idle 分支另帶 "mes_nosig"
          （additive）。"""
    win_end = _coerce_dt(win_end)
    # ⑤ 原始年齡：四個 return 分支共用同一個表達式（**只算一次**，免得哪天有人只改其中一處）。
    age_raw = mes_last_h if mes_available else None
    mes_fields = _now_mes_fields(mes_available, mes_any_last_h, mes_last_designation,
                                 mes_desig_available)
    # ⑥ 子工位：**每個分支都帶**（同 `mes_fields` 的做法）——停機／長停那幾形一樣要能
    #    在 landing 上畫出子工位格（規則在顯示層，這裡只搬事實）。
    mes_fields.update(_sub_units_field(sub_units))
    for w in wos or []:
        if not isinstance(w, dict):
            continue
        # 取消／作廢單不得把 Now 釘成 DOWN（2026-08-11 拍板①）——這型最惡劣：CANCELLED 的
        # `closed_at` 可能為 NULL、若 history 也空（eMaint 匯出）⇒ 活躍窗永遠 ongoing ⇒ **永遠 DOWN**。
        # （⚠ 舊敘述寫「CANCELLED 恆 NULL」，2026-08-16 cmms 收斂取消後已失效——改不了本行的結論。）
        if w8.wo_is_cancelled(w):
            continue
        win = w8.wo_active_window(w, now=win_end)
        if win is None:
            continue
        ws, we = win
        if not (ws <= win_end and (we is None or we > win_end)):   # 活躍窗須涵蓋現在＝仍 open
            continue
        a_end_eff = win_end if we is None else _coerce_dt(we)
        phases = _phase_timeline(w, _coerce_dt(ws), a_end_eff, we is None)
        if not phases:
            continue
        cur = phases[-1]                                          # 覆蓋 win_end 者＝末階段
        if _phase_kind(cur["status"], cur["hold_reason"], w.get("work_type")) == "count":
            state, _conf, _canon = w8.wo_state(w.get("work_type"))
            # 2026-07-10 Chris UX：「現在」欄要答「此刻為什麼停」——若當前階段正卡等料/等外包，
            # 顯示 hold 態（DOWN·等料）而非母態（DOWN·維修）＝更接近事實、掃表不必點 drill。
            # OTHER/未知 hold 維持母態（保守；與 wo_segments 拆色同規則）。
            if cur["status"] == "ON_HOLD" and cur["hold_reason"] in HOLD_DOWN_STATES:
                state = HOLD_DOWN_STATES[cur["hold_reason"]]
            return {"bucket": "down", "verdict": "down", "wo_no": w["work_order_no"],
                    "state": state, "mes_last_h": None, "mes_age_h": age_raw, "stale": False,
                    **mes_fields}
    # ①b A10 母單展開：自身無計停機工單，但**母機**的工單此刻把這個工位圈在範圍內 → 一樣 DOWN。
    #    排在自身工單之後（自身單更具體）、長停之前（有人在修＝事實 > 登記）。
    inh = _now_inherited(inherited, win_end)
    if inh is not None:
        return {"bucket": "down", "verdict": "down", "wo_no": inh["wo_no"],
                "state": inh["state"], "mes_last_h": None, "mes_age_h": age_raw, "stale": False,
                "inherited_from": inh["parent"], **mes_fields}
    # ①c 長停（登記的事實）——排在 DOWN 之後、預設 UP 之前。stale 強制 False（見 docstring）。
    if dormant:
        return {"bucket": "dormant", "verdict": "dormant", "wo_no": None, "state": None,
                "mes_last_h": age_raw, "mes_age_h": age_raw, "stale": False, **mes_fields}
    # 無 count 工單 → 預設 UP；MES 僅升級標籤 / 掛 ⚠（規則②③）
    age = age_raw
    if age is not None and age <= ud.MES_RECENT_H:
        return {"bucket": "up", "verdict": "up_producing", "wo_no": None,
                "state": None, "mes_last_h": age, "mes_age_h": age_raw, "stale": False, **mes_fields}
    return {"bucket": "up", "verdict": "up_idle", "wo_no": None, "state": None,
            "mes_last_h": age, "mes_age_h": age_raw, "stale": age is not None and age > quiet_h,
            "mes_nosig": bool(mes_available and mes_covered and age is None), **mes_fields}


# ---------------------------------------------------------------------------
# 7b) wo_mes_conflict — (c) MES 交叉揭露（2026-08-11 Chris 拍板；零新查詢）
# ---------------------------------------------------------------------------
#   問題：「工單活著的每一秒都算停機」而工單可以活很久（實測存在存活極久的未結單）⇒ 板上
#   17.5% 的停機裡，齡 >30 天的單佔 83%。**Board 分不出「真的停了兩年」與「沒人結單」**
#   （`EID-17431` 那 76 天，工作日誌看得出是真的在修）。
#   ⇒ Chris 拍板 (c)：**工單說停機 ∧ MES 同時段卻有生產 ⇒ 頁面揭露矛盾、不自動改任何數字。**
#   揭露而不裁決，判斷留給看板的人。
#
#   ★★ 三條界線（明文拍板、不准擴大解釋）：
#     (甲) **不對工單齡設上限、不加常駐 UI 分層**——那 17.5% 會留在板上。若它是真的，
#          板本來就該長那樣。這裡是唯一的安全網，**且只在真矛盾時出現一行**。
#     (c1) **單向**：只抓「工單說停機 ∧ MES 有生產」；反向（機台真的停著但沒有工單）
#          **不抓、也不打算抓**＝那就是 2026-08-11 否決掉的雙向哨兵。別把它長成雙向網。
#     (c2) **零新查詢**：`mes_last_h` 是 Now 欄本來就在用的那份探測結果（同一個 request、
#          同一份 `mes_by_eid`）。判準＝**它有沒有發出新的 MES 查詢？有就是哨兵，沒有才是揭露。**
#
#   ★★★ #22 定案（2026-08-18 Chris；取代 08-17 議而未決的「排除 Engineering 段」方案）：
#     **帶類別揭露、不排除、不判斷。** dummy print 假陽性（EID-17431/WO-34497 實例）的解法
#     不是把 engineering 段從偵測裡剔掉——「排除」本身就是替讀者下判斷（我方不熟工單內容、
#     判斷錯比不判斷更添亂）——而是把該段的 MES `matDesignation` 原值標在揭露行上
#     （Production／Engineering (R&D)…），讓讀的人自己分。
#     ⇒ **本函式一個位元沒動**（偵測照舊、engineering 段照抓）；類別在 **app 層** attach
#     （`_build_eqm_live`：lpstate meta 的 `designation_by_eid` → `board.conflicts[].desig`）
#     ——配對原子性由狀態檔 v2「end+designation 同筆存」保證。探測 fallback／demo 拿不到
#     類別 ⇒ 模板省略子句（不猜）。零新查詢（c2）不變：designation 早就在狀態檔裡。


def wo_mes_conflict(segs, mes_last_h, win_end, guard_h=WO_MES_GUARD_H):
    """(c) 單向交叉揭露 → `{"wo_no", "mes_last_h"}`（有矛盾）／`None`（無、或量不到）。

       `segs`＝該 EID 的 `wo_segments` 輸出（本函式**只看 countable=True** 的段——uptime-hold
         段本來就在說「機台可生產」，它與 MES 有生產完全不矛盾）。
       `mes_last_h`＝距上次生產的小時數（Now 欄那份探測；`None`／非數＝量不到 ⇒ 回 None，
         **絕不因為量不到就宣稱有矛盾或沒矛盾**）。
       `win_end`＝探測基準時刻 ⇒ `t_last = win_end − mes_last_h 小時`＝MES 最後一段生產的收尾時刻。

       判準：∃ countable 段使 `seg.start + guard_h ≤ t_last ≤ seg.end` ⇒ 矛盾。
         · 左界加 `guard_h`＝報修當下把手上那顆跑完、＋時鐘 skew（見 `WO_MES_GUARD_H`）。
         · 命中多段時取**最早**那段的工單（＝矛盾持續最久的那張；輸出確定性）。
       回傳只帶 `wo_no` 與 `mes_last_h` 兩個顯示欄位——**本函式不改任何數字**，
       停機時數、A%、heatmap 一個位元都不動。純函式、零 I/O、不 mutate 輸入。"""
    if mes_last_h is None or isinstance(mes_last_h, bool) or not isinstance(mes_last_h, (int, float)):
        return None
    win_end = _coerce_dt(win_end)
    if win_end is None:
        return None
    try:
        t_last = win_end - _dt.timedelta(hours=float(mes_last_h))
    except (ValueError, OverflowError):
        return None
    guard = _dt.timedelta(hours=float(guard_h or 0.0))
    hit = None
    for s in segs or []:
        if not isinstance(s, dict) or not s.get("countable", True):
            continue
        a, b = s.get("start"), s.get("end")
        if a is None or b is None:
            continue
        if a + guard <= t_last <= b:
            if hit is None or a < hit[0]:
                hit = (a, s.get("work_order_no"))
    if hit is None:
        return None
    return {"wo_no": hit[1], "mes_last_h": mes_last_h}


# ---------------------------------------------------------------------------
# 7c) dormant_mes_conflict — §10 一致性揭露（2026-08-11 Chris 否決哨兵後的**唯一殘片**）
# ---------------------------------------------------------------------------
#   `SPEC-DORMANT-CAPACITY.md` §10：雙向哨兵**已被否決**（長停是組織的共決，不是系統該
#   管轄的範圍）。兩個方向的價值不對稱，只有一個方向剩下東西：
#     · MES 長期零生產 ∧ 無登記（「該登記沒登記」）→ **完全不做**（只影響回溯數字、組織已知）。
#     · 登記長停 ∧ MES 卻有生產（「該解除沒解除」）→ **數字會壞**（該機被排除在產能分母外、
#       產出卻仍計入分子 ⇒ 稼動率灌水）⇒ **揭露一行**。
#
#   ★★ 這不是哨兵，別把它當哨兵重建。判準（§10 反模式，逐字）：
#      **它有沒有發出新的 MES 查詢？有就是哨兵，沒有才是揭露。**
#      本函式吃的 `mes_last_h` 是 Now 欄本來就在用的那份探測結果（同一個 request、同一份
#      `mes_by_eid`）＝**零新查詢**；長停旗標也在同一隻手上。
#   ★ 仍守 **C-D1：只揭露、永不裁決**——登記才是事實。這行字不改任何數字、不動分母、
#      **不自動解除登記**（`dormant` 旗標與 A%「—」全都照舊）。
#   ★ 與 (c) `wo_mes_conflict` **分開兩件事**（分兩列渲染、不合併）：那條講「工單說停機」、
#      這條講「登記說不排生產」——來源不同、該找的人不同、處置也不同。


def dormant_mes_conflict(dormant, mes_last_h, win_end):
    """§10 一致性揭露 → `{"mes_last_h": …}`（有矛盾）／`None`（無、或量不到）。

       `dormant`＝該 EID 的長停資訊（`wdorm.dormant_info` 或批次現況同形 dict；含 `since`
         ＝**台北日曆日字串**）。falsy ⇒ None（沒登記就沒有這件事可講）。
       `mes_last_h`＝距上次生產的小時數（Now 欄那份探測）。`None`／非數＝**量不到 ⇒ None**
         （量不到既不能宣稱矛盾、也不能宣稱沒矛盾）。
       `win_end`＝探測基準時刻 ⇒ `t_last = win_end − mes_last_h 小時`。

       判準＝**日粒度、台北日曆日**：`date(t_last) > date(since)` 才算矛盾。
         · 與 `board_dormant` 的 as-of 語意同軸（SPEC §6.1-1：日粒度、當日以最後一筆事件
           定調）——生效日**當天**本來就可能還有生產（決策當天上午還在跑），故用**嚴格大於**、
           且不必再疊時鐘 skew 護欄：整整一個日曆日就是護欄。
         · 只讀 `since` 這個**已經是台北日曆日**的欄位，全鏈零 UTC 換算（§6.1 消費紀律②）。
       純函式、零 I/O、**零新查詢**、不 mutate 輸入。"""
    if not dormant or not isinstance(dormant, dict):
        return None
    if mes_last_h is None or isinstance(mes_last_h, bool) or not isinstance(mes_last_h, (int, float)):
        return None
    since = dormant.get("since")
    if not isinstance(since, str) or len(since.strip()) < 10:
        return None                       # 缺／壞 since ⇒ 量不到基準日，不猜
    try:
        since_d = _dt.date.fromisoformat(since.strip()[:10])
    except ValueError:
        return None
    win_end = _coerce_dt(win_end)
    if win_end is None:
        return None
    try:
        t_last = win_end - _dt.timedelta(hours=float(mes_last_h))
    except (ValueError, OverflowError):
        return None
    if t_last.date() <= since_d:
        return None                       # 生效日當天／之前的生產＝正常（決策當天還在跑）
    return {"mes_last_h": mes_last_h}


# ---------------------------------------------------------------------------
# 6c) 座係數（`#110` (2)(4)；`SPEC-RUNWINDOW.md` §6.3-b、鐵則 C-RW24）
# ---------------------------------------------------------------------------
#   Chris 2026-09-08 裁決（`ROADMAP.md` #110）：**機台＝可用度單位**（C-CF8：A％／U 的分母
#   永不按座拆）、**座＝產能單位** ⇒ 停機「當量」要按壞了幾座折算。本節只作用在 `/equipment`
#   的 ≈PH 當量那兩個位置（列尾 `eq-equiv` 與逐日格第二行），A％／Δ／`stop_h`／segments／
#   四色／組成條／Now 欄**一個位元不動**。
#
#   ★ 座名冊與投影**唯一住在 `board_capacity`**（C-CF3）——本節只是把它套在一塊板上，
#     不自己數座位、不自己判長停。
#   ★ 「指名哪幾座」有**兩條路，兩條都吃、取聯集**：
#       · A10 `board_w8.effective_sub_units(wo, at)`（`[]`＝整機）
#       · NOTE-71 `-N` 座級單的加性欄 `seat_unit`（`"nest:2"`；`board_w8.normalize_wo`）
#     兩者的值形都是 `board_capacity.installed_units()` 的 UNIT_ID ⇒ 直接聯集即可。
#   ★ 形②③ 的**子槽位各自是名冊上的一列**（CLN DI／SF、CAL Gen2 各 4 子）：那一列的停機
#     只壞了母機 n 座裡的 1 座 ⇒ 該列恆 `f = 1/n`。這正是 Chris 2026-09-12 追問的
#     「CLN Gen2 座級的當量永遠沒顯示」的另一半；另一半（速率繼承）在 `inherit_child_rates`。


def _seat_parent_children():
    """→ `[(母 EID, (子 EID…)), …]`＝形③（`PARENT_EXPANSION`）＋形②（`COTAG_CHILD_UNITS`）。

       ⚠ **名冊在 `board_capacity`、本函式只是取用順序穩定的視圖**——不要在這裡新增成員，
       也不要把形①（nest）放進來：nest 沒有自己的 EID，不存在「子列繼承母機速率」這件事。"""
    out = []
    for reg in (_wcap.PARENT_EXPANSION, _wcap.COTAG_CHILD_UNITS):
        for parent in sorted(reg):
            out.append((parent, tuple(reg[parent])))
    return out


def inherit_child_rates(rates, rate_days=None, rate_kind=None):
    """形②③ 的子槽位**沒有速率就繼承母機的**（就地改寫；回傳同一個 `rates` 便於串接）。

       ★ 病因（2026-09-12 Chris 追問「CLN Gen2 座級的當量永遠沒顯示」）：`/equipment` 的
         ≈PH 速率是**站級**事實，靠 `attrib_eid_cls_map(fleet_map)` 對映到機台；而
         `_state/fleet_map.json` 只認得**母機**（`segCleanTest` 列 `EID-16077`／`EID-27600`、
         `segCalibrationTest` 列 `EID-24742`）——子槽位 `EID-14732`／`EID-27639`／
         `EID-24790`… **不在任何站** ⇒ 永遠沒速率 ⇒ 永遠不印當量。
         Live 實看：Seal#8 印「≈113PH」、CLN DI Bay#1 停 3.8h 只印小時。
       ★ 為什麼繼承是對的：子槽位**就是**母機那一站的 1/n 座，它的產出速率就是母機的站速率；
         再乘上座係數 `1/n` 才是那一座的當量。速率繼承與座係數是**同一件事的兩半**，
         少任何一半都會得到錯的數（缺速率＝不印；缺係數＝把整站速率記到一座頭上）。
       ⚠ **只補、不覆蓋**：子槽位哪天自己在 `fleet_map` 長出站別（真的被量到），以它自己的
         速率為準——繼承是兜底、不是主張。母機沒速率 ⇒ 子也沒有（不猜）。

       ★ **WP-C2（2026-09-12）`rate_kind`（可選、同形跟著繼承）**：座‧小時尺下母機的
         `rates[parent]` ＝ `r_seat × n`（n＝母機安裝座數），而子列的座係數恆 `1/n`
         ⇒ `rate × f` **恰為 `r_seat`**＝「把 Gen2 子 EID 視為一台 Gen1 機器」那句話的算術形。
         **所以 A 尺下子列照樣直接繼承母機那個值、`seat_factors_for` 一個位元不必動**
         （寫成「子 rate＝`r_seat`、f＝1」是同一個數字的另一種拆法，但要改兩支凍結函式）。
         `rate_kind` 只是把那一列用的是哪把尺帶給 tooltip，**不參與任何計算**。"""
    if not isinstance(rates, dict):
        return rates
    days = rate_days if isinstance(rate_days, dict) else None
    kinds = rate_kind if isinstance(rate_kind, dict) else None
    for parent, children in _seat_parent_children():
        if parent not in rates:
            continue
        for child in children:
            if child in rates:
                continue                      # 只補、不覆蓋
            rates[child] = rates[parent]
            if days is not None and parent in days:
                days[child] = days[parent]
            if kinds is not None and parent in kinds:
                kinds[child] = kinds[parent]
    return rates


def _seg_countable_h(segs, a, b):
    """`wo_segments` 輸出裡 **countable** 段落在 `[a, b)` 的小時數（只當權重用、不當時數用）。"""
    tot = 0.0
    for s in segs or ():
        if not s.get("countable", True):
            continue
        x = s["start"] if s["start"] > a else a
        y = s["end"] if s["end"] < b else b
        if y > x:
            tot += (y - x).total_seconds() / 3600.0
    return tot


def seat_factors_for(catalog, wos_by_eid, day_wins, win_end, dormant_events=None):
    """一塊板的座係數 → `{eid: {"win", "days": {ds: f}, "days_k": {ds: k|None}, "k", "n", "wos"}}`。

       純函式、零 I/O、恆不 raise（算不出來就少一個條目＝該列係數 1.0＝接線前的數字）。
       **只對名冊上有座的機台與它們的子 EID 產生條目**；其他 eid 不出現 ⇒ `build_row`
       當 1.0 用 ⇒ **那些列逐位元不變**。

       · **子 EID 列**（形②③ 的子槽位，各自是名冊上的一列）：
         `f = board_capacity.equiv_units_factor(母, [子], day, events)`
         ＝子在役 ⇒ `1/n`、子自己被登記長停 ⇒ `0`（那一列本來就全「—」）。`k`＝1／0。
       · **母機／整機列**（`installed_units(eid)` 非空且自己就在名冊上）：窗內**逐工單**
         算係數，按該單自己的 countable 段小時加權（`board_capacity.equiv_units_weighted`）。
         每張單的 `affected` ＝ A10 `effective_sub_units(w, at=win_end)` ∪ NOTE-71 `seat_unit`
         （兩條指名座的路**都吃、取聯集**）；空集合＝整機 ⇒ 係數＝`active/installed`。
         沒有任何工單列（例：純 MES 停機）⇒ `win`／逐日皆 1.0。
       · `k`＝所有**有權重**的工單的 `|affected ∩ active|`：一致 ⇒ 該值；否則 `None`
         （`None` 的畫面語意＝印 `×0.xx`、不印 `k/n`——不一致的 k 印成單一數字就是說謊）。
       · `days` 逐**生產日**（`day_wins` 的窗）各算一次，日參數＝該格的生產日；`win` 用窗末日。
         `days_k` ＝**當日**的 k（同一條一致性規則）——格 tooltip 只准配當日的 k，
         拿整窗那顆去配當日的係數，同一個 tooltip 裡兩個數字會對不起來。

       ⚠ 權重是**逐單**時數、不是列級 interval-union ⇒ 與 `row["erw"]["stop_h"]` 不等；
         它在這裡只當**比例**用。這與 `app._equiv_factor_by_cls`（W1）是同一種寫法、同一支
         `wo_segments`，**不另造第二種段口徑**。"""
    win_end = _coerce_dt(win_end)
    day_wins = list(day_wins or ())
    if win_end is None or not day_wins:
        return {}
    win_start = day_wins[0][1]
    win_day = win_end.date()
    cat_eids = {e[2] for e in (catalog or ())}
    out = {}

    # (1) 子 EID 列：一列＝一座
    for parent, children in _seat_parent_children():
        n = len(_wcap.installed_units(parent))
        if not n:
            continue
        for child in children:
            if child not in cat_eids:
                continue
            try:
                active = frozenset(_wcap.active_units(parent, win_day, dormant_events))
                entry = {"win": _wcap.equiv_units_factor(parent, [child], win_day, dormant_events),
                         "days": {}, "days_k": {}, "k": (1 if child in active else 0),
                         "n": n, "wos": []}
                for ds, d0, _d1 in day_wins:
                    entry["days"][ds] = _wcap.equiv_units_factor(
                        parent, [child], d0.date(), dormant_events)
                    entry["days_k"][ds] = (
                        1 if child in frozenset(_wcap.active_units(parent, d0.date(),
                                                                   dormant_events)) else 0)
            except Exception:
                continue                       # 一顆算壞＝那一列退回 1.0（不主張、不炸頁）
            if entry["win"] is None:
                continue
            out[child] = entry

    # (2) 母機／整機列（自己在名冊上有座）
    for eid in _wcap.machines_with_units():
        if eid not in cat_eids:
            continue
        n = len(_wcap.installed_units(eid))
        if not n:
            continue
        try:
            wrows = []                         # [(affected tuple, segs, wo_no)]
            for w in (wos_by_eid or {}).get(eid) or ():
                if not isinstance(w, dict):
                    continue
                segs = wo_segments([w], win_start, win_end)
                if not segs:
                    continue
                aff = set(w8.effective_seat_units(w, at=win_end))
                su = w.get("seat_unit")
                if isinstance(su, str) and su:
                    aff.add(su)                # NOTE-71 `-N` 座級單（與 A10 取聯集）
                wrows.append((tuple(sorted(aff)), segs, w.get("work_order_no")))
            active = frozenset(_wcap.active_units(eid, win_day, dormant_events))
            entry = {"win": 1.0, "days": {}, "days_k": {}, "k": None, "n": n, "wos": []}
            rows_w, ks = [], []
            for aff, segs, wno in wrows:
                h = _seg_countable_h(segs, win_start, win_end)
                if h <= 0:
                    continue
                rows_w.append((eid, aff, h))
                kk = len(active) if not aff else len(set(aff) & active)
                ks.append(kk)
                entry["wos"].append({"wo_no": wno, "units": list(aff), "k": kk})
            if rows_w:
                f, tot = _wcap.equiv_units_weighted(rows_w, win_day, dormant_events)
                if f is not None and tot > 0:
                    entry["win"] = f
            if ks and len(set(ks)) == 1:
                entry["k"] = ks[0]
            for ds, d0, d1 in day_wins:
                drows = [(eid, aff, _seg_countable_h(segs, d0, d1)) for aff, segs, _n in wrows]
                drows = [r for r in drows if r[2] > 0]
                df, dks = None, []
                if drows:
                    df, dtot = _wcap.equiv_units_weighted(drows, d0.date(), dormant_events)
                    if dtot <= 0:
                        df = None
                    dactive = frozenset(_wcap.active_units(eid, d0.date(), dormant_events))
                    dks = [len(dactive) if not a else len(set(a) & dactive) for _e, a, _h in drows]
                entry["days"][ds] = 1.0 if df is None else df
                entry["days_k"][ds] = dks[0] if (dks and len(set(dks)) == 1) else None
        except Exception:
            continue                           # 恆不 raise（座腿壞掉不得拖垮整頁）
        out[eid] = entry
    return out


# ---------------------------------------------------------------------------
# 6d) 座列浮出（`#125` (b)(c)；`SPEC-RUNWINDOW.md` §6.3-c、鐵則 C-RW26）
# ---------------------------------------------------------------------------
#   Chris 2026-09-12 晚裁決（`ROADMAP.md` `#125`）：**Gen2（形③）不動**——槽位是目錄上的
#   獨立單位（自己的 EID／MES 段／該跑窗）＋天天被看，維持第一層。**四站（Flex Bonding／
#   Leak Test／Press／Frame）的座「例外才出現」**：窗內有工單範圍指到該座、或該座被登記長停
#   （A11 座級事件）時，那一座才浮出成**母機正下方縮排的一列**；**整機單不展開到座**
#   （避免四列複本）；沒有座級事件的座**不出現**。
#
#   ★ 浮出列的語意（Chris 09-12 逐條收下，`ROADMAP` `#125` ★★ 段）：
#     · **直接顯示不折疊**（存在理由就是有人該看的事件）⇒ `row_visibility` 對 `is_seat` 恆 shown。
#     · **停機段／`stop_h`／Now 有**＝指到這座的那張單、**從範圍改到座那一刻起算**；
#       改之前的時段**留在母機列**（母機列一個位元不動）。
#     · **當量有、但標「其中」**——母機列的當量已按 k/n 折過、含這張單 ⇒ 座列印的是
#       同一個數的子集、**不得再加**（tooltip 明講；C-RW13 的 Σ 禁令在這裡換個形出現）。
#     · **A%／Δ 恆「—」**（C-CF8：機台是可用度單位；座借母機該跑窗算 A% 會和母機列打架）。
#     · **「暫停·不計」的單也讓座浮出**（斜紋、`stop_h` 0、無當量＝誠實回答「這張單在講哪一座」）。
#   ★ **不進任何 Σ**：`collapse_summary`／`_headline`／CSV／Hermes 一律跳過 `is_seat` 列。
#     實作上靠**插入時機**：座列在 `build_board` 尾端、**所有彙總算完之後**才插進
#     `areas[].rows` ⇒ 板內每一個 Σ 都是「按機台列算的」那一份，逐位元同加座列前。


def _seat_unit_timeline(wo, win_end):
    """單張工單 → `[(start, end, frozenset(座 UNIT_ID)), …]`＝活躍窗內逐段「此刻指名哪幾座」。

       **空集合＝該段講的是整機**（不指名任何座）⇒ 整機單不展開到座（`#125` 裁決）。
       兩條指名座的路**都吃、取聯集**（＝C-RW24 的同一條，不另造第三種判定）：
         · A10：逐事件快照 `sub_units`（`w8.normalize_scope_events`）——**會隨時間變**，
           這正是「改到座之前的時段留在母機列」那句話的機關；
         · NOTE-71：`seat_unit`（單直接開在座資產 `EID-xxxxx-N<n>` 上）——**整張單恆有**。
       ⚠ **不能走 `board_scope.scope_timeline`**：它的 `scope` 已經被 `fold_seat_members`
         折成母機 EID 集合、`sub_units` 也不在裡面（形② 的子 EID 是例外，它本來就是 EID）。
         段界照它的規則走（事件開新段、早於窗首的事件夾到窗首、相鄰同值合併），但比較的是
         **座集合**——scope 相同而 `sub_units` 不同的兩筆事件在 `scope_timeline` 會被合併掉。
       純函式、零 I/O、恆不 raise（壞資料 ⇒ `[]`）。"""
    if not isinstance(wo, dict):
        return []
    try:
        win = w8.wo_active_window(wo, now=win_end)
        if win is None:
            return []
        a_start, a_end = win
        a_start = _coerce_dt(a_start)
        a_end_eff = _coerce_dt(win_end) if a_end is None else _coerce_dt(a_end)
        if a_start is None or a_end_eff is None or a_end_eff <= a_start:
            return []
        parent = _wcap.fold_seat_id(wo.get("asset_id"))[0]
        su = wo.get("seat_unit")
        base = frozenset({su}) if isinstance(su, str) and su else frozenset()
        evs = w8.normalize_scope_events(wo.get("scope_events"))[0]
    except Exception:
        return []
    cur, cursor, out = base, a_start, []
    for e in evs:
        ts = _coerce_dt(e.get("ts"))
        if ts is None or ts > a_end_eff:
            continue                                   # 活躍窗外（防禦；契約不該產生）
        if ts < cursor:
            ts = cursor                                # 夾到窗首＝覆寫初始整機態
        if ts > cursor:
            out.append((cursor, ts, cur))
            cursor = ts
        cur = base | frozenset(_wcap.event_seat_units(
            parent, e.get("scope") or (), e.get("sub_units") or ()))
    if a_end_eff > cursor:
        out.append((cursor, a_end_eff, cur))
    merged = []
    for s, e_, sc in out:
        if merged and merged[-1][1] == s and merged[-1][2] == sc:
            merged[-1] = (merged[-1][0], e_, sc)
        else:
            merged.append([s, e_, sc])
    return [(a, b, sc) for a, b, sc in merged]


def _seat_wo_clips(wo, units, win_start, win_end):
    """單張工單 → `{座: [(start, end), …]}`（裁到板窗、升冪、相鄰／重疊合併）。
       `units`＝該母機**名冊上**的座（名冊外的座號一律丟棄＝C-CF3，不在這裡長第二份名冊）。"""
    per = {}
    for s, e, sc in _seat_unit_timeline(wo, win_end):
        if not sc:
            continue                                   # 整機段：不展開到座
        a = s if s > win_start else win_start
        b = e if e < win_end else win_end
        if b <= a:
            continue
        for u in sc:
            if u in units:
                per.setdefault(u, []).append((a, b))
    out = {}
    for u, ivs in per.items():
        ivs.sort()
        merged = []
        for a, b in ivs:
            if merged and merged[-1][1] >= a:
                if b > merged[-1][1]:
                    merged[-1][1] = b
            else:
                merged.append([a, b])
        out[u] = [(a, b) for a, b in merged]
    return out


def _seat_clip_segments(wo, clips, win_start, win_end):
    """該座的 segment ＝母機那張單的 `wo_segments` **∩ clips**（裁切、跨 clip 切開）。

       ⚠ 與 `_inherited_segments` **刻意有一處不同**：那一支**只取 countable 段**（A10 母單
         繼承到子工位時，uptime-hold 段繼承過去不增加資訊）；這裡**兩種都留**——
         Chris 裁決「『暫停·不計』的單也讓座浮出（斜紋、`stop_h` 0、無當量）」，
         而那一列的存在理由就是回答「這張單在講哪一座」。
       態／顏色／countable／ongoing 全部沿用 `wo_segments` 既有規則（**不新造口徑**）。"""
    out = []
    for s in wo_segments([wo], win_start, win_end):
        for c_s, c_e in clips:
            a = s["start"] if s["start"] > c_s else c_s
            b = s["end"] if s["end"] < c_e else c_e
            if b <= a:
                continue
            seg = dict(s)
            seg["start"], seg["end"] = a, b
            # ongoing＝「延續至今」；被 clip 裁掉尾巴就不再是「現在仍停」（同 `_inherited_segments`）。
            seg["ongoing"] = bool(s.get("ongoing")) and b == s["end"]
            out.append(seg)
    return out


def _seat_dormant_event(parent, unit, kind, dormant_events):
    """該座在 A11 事件流裡**有沒有座級登記**（＝浮出條件 (ii)）→ bool。

       · 形①（nest）：`eid == 母機` 且 `sub_unit == 該座`（整機列的事件**不算**——
         那是整台的登記、不是「這一座有事」）。
       · 形②（co-tag 子 EID）：子 EID 自己就是資產 ⇒ `eid == 子 EID`。
       `dormant_events=None`（過渡橋常數）⇒ 恆 False：橋常數裡沒有座級事件，
       拿它去推「某座長停」就是憑空主張。恆不 raise。"""
    for ev in dormant_events or ():
        if not isinstance(ev, dict):
            continue
        if kind == "nest_eav":
            if ev.get("eid") == parent and ev.get("sub_unit") == unit:
                return True
        elif ev.get("eid") == unit:
            return True
    return False


def _seat_row(parent, pname, area_key, unit, units, kind, wo_hits, day_wins, win_start,
              win_end, erw_ctx, rate, dormant_events, seat_age_h=None):
    """單一座列（純函式）。鍵集合 ＝ `build_row` 的鍵集合 ＋ `is_seat`／`parent_eid`／`seat_unit`
       ——模板、`/equipment/cell` 與 `_find_eqm_row` 才走得通同一條路（smoke 有釘）。

       `seat_age_h`＝**這一座自己的** MES 末筆年齡（小時）或 None（`#128`-A／§6.9；
       材料＝lastprod push 的 `sub_unit_ages_by_eid[母機][unit]`）。**None＝逐位元同加此參數前。**"""
    n = len(units)
    segments, wos_win, seat_wos = [], [], []
    for w, clips in wo_hits:
        segments.extend(_seat_clip_segments(w, clips, win_start, win_end))
        cur_st, cur_hr = wo_current(w)
        wos_win.append({"wno": w.get("work_order_no"), "start": clips[0][0], "end": clips[-1][1],
                        "work_type": w.get("work_type"), "status": cur_st, "hold_reason": cur_hr})
        seat_wos.append({"wo_no": w.get("work_order_no"), "units": [unit], "k": 1})
    segments.sort(key=lambda s: (s["start"], s["work_order_no"]))
    buckets = bucket_segments_by_day(segments, day_wins)
    cells = {ds: day_cell(buckets[ds]) for ds, _, _ in day_wins}
    total_stop_h = sum(c["stop_h"] for c in cells.values() if c)

    # ★ **刻意偏離派工單一處（理由＝§6.3「整頁單一尺」）**：座列的 `erw` 不設 None，而是
    #   「拿**母機的**該跑窗裁切過的 `stop_h`／`days`／`by_state`」＋`avail`／`delta`／`bar`
    #   恆 None。設成 None 會讓座列變成整頁**唯一**一條 24h 軸的列——那正是 §6.3 開單要修的病
    #   （「同一頁兩把尺」）。A%／Δ 仍恆「—」＝裁決原意逐字成立；bar 不畫＝座沒有自己的該跑窗。
    erw = erw_row(parent, buckets, day_wins, erw_ctx) if erw_ctx is not None else None
    if erw is not None:
        erw["avail"] = None                 # C-CF8：座不是可用度單位（永不算 A%）
        erw["delta"] = None
        erw["bar"] = None                   # 座沒有自己的該跑窗 ⇒ 不畫 time bar

    # 座係數＝**一座**。走 `equiv_units_factor`（與 `seat_factors_for` 的子 EID 列同一支）：
    # 在役 ⇒ `1/n`＝裁決那句「`1/installed`」；被登記長停 ⇒ `0`（沒有產能可損失，
    # 也讓座列的當量恆為母機那顆的真子集）。算壞 ⇒ 退回 `1/n`（不主張、不炸頁）。
    try:
        f = _wcap.equiv_units_factor(parent, [unit], win_end.date(), dormant_events)
    except Exception:
        f = None
    if f is None:
        f = 1.0 / n
    stop_eff = ((erw["stop_h"] if erw is not None else total_stop_h) or 0.0)
    equiv_ph = None
    if rate and float(rate) > 0 and stop_eff > 0:
        _e = int(round(stop_eff * float(rate) * float(f)))
        equiv_ph = _e if _e >= 1 else None
    if erw is not None:
        for _d in (erw.get("days") or {}).values():
            _d["seat_f"], _d["seat_k"], _d["seat_n"] = float(f), 1, n
            _sh = _d.get("stop_h") or 0.0
            if rate and float(rate) > 0 and _sh >= 0.05:
                _v = int(round(_sh * float(rate) * float(f)))
                _d["equiv_ph"] = _v if _v >= 1 else None

    # Now＝那張單此刻的狀態。**沿用 `_now_inherited` 的三合一判定**（活躍窗涵蓋 now ∧ 末階段
    # 算停機 ∧ now 落在這座的 clip 內）——不新寫第二份「現在停不停」的規則。
    inh = [{"wo": w, "clips": [(a, b, "explicit") for a, b in clips], "parent_eid": parent}
           for w, clips in wo_hits]
    try:
        since = _wcap.seat_dormant_since(parent, unit, win_end.date(), dormant_events)
    except Exception:
        since = None
    dorm = {"since": since, "reason_zh": None, "reason_en": None} if since else None
    now = _now_block([], win_end, mes_last_h=None, mes_available=False,
                     dormant=dorm, inherited=inh)
    # ★ `#128`-A（`SPEC-RUNWINDOW.md` §6.9／鐵則 **C-RW27**）：Now 欄第二行「最後生產」的
    #   材料＝**這一座自己的**末筆。座這條路上 `mes_available=False` ⇒ `_now_block` 自己
    #   算出的 `mes_age_h` 恆 None ⇒ 這裡**只補那一個欄**、其餘鍵一個位元不動。
    #   ⚠ **刻意不從 `_now_block` 的入參走**：傳 `mes_available=True` 會讓座走規則②③、
    #     憑一個末筆宣稱「UP·生產中／UP·待命」——座級沒有可用度證據腿（模板那段註解的
    #     裁決：有末筆不等於量到「可用」）⇒ **座的 chip 一個位元都不能動**。
    if seat_age_h is not None:
        now["mes_age_h"] = seat_age_h
    return {
        "eid": (_wcap.seat_wire_id(parent, unit) or unit),   # 形① ⇒ `EID-26718-N2`；形② ⇒ 子 EID
        "name": pname,                     # 座的字樣由**呈現層**接（英文預設＋中文可切換＝前端鐵則）
        "area": area_key, "dup": False,
        "segments": segments, "cells": cells, "wos_win": wos_win,
        "wos_win_inh": [],          # §18.37 加性鍵：座列的單本來就已按座裁好，沒有「繼承單」這一層（S6l 鍵集合對齊）
        "total_stop_h": total_stop_h,
        # C-CF8／裁決：座列的 A%／Δ 恆「—」（模板另掛裁決句 tooltip）
        "avail_pct": None, "avail_delta_pp": None,
        "erw": erw, "equiv_ph": equiv_ph,
        "seat_f": float(f), "seat_k": 1, "seat_n": n, "seat_wos": seat_wos,
        "dormant": dorm, "wo_mes_conflict": None, "dormant_mes_conflict": None,
        "dormant_dropped": None,
        "mes_covered": False, "mes_measurable": False,
        "now": now,
        # ★ 三個加性鍵（**就這三個**；`equiv_kind` 是 `is_seat` 的同義詞 ⇒ 不另開第四鍵）
        "is_seat": True, "parent_eid": parent, "seat_unit": unit,
    }


def seat_rows_for_board(catalog, wos_by_eid, day_wins, win_end, mode,
                        dormant_events=None, rates_by_eid=None, erw_ctx=None,
                        sub_unit_ages=None):
    """一塊板的**座列** → `{母機 eid: [座列 dict, …]}`（名冊宣告順序）。

       純函式、零 I/O、恆不 raise（一台算壞＝那台沒有座列＝接線前的板）。
       **`build_row` 一個位元沒動**——座列是另一種列，不是 `build_row` 的新分支。

       · **對象**＝形①（`NEST_EAV_UNITS` 的機台）＋形②（`COTAG_CHILD_UNITS` 的機台）的座。
         **形③（`PARENT_EXPANSION`）一個位元不動**（Gen2 槽位維持第一層、各自是名冊上的一列）。
       · **何時浮出**（例外才出現）：板窗內該座 (i) 出現在某張單的**範圍**裡
         （A10 `sub_units` ∪ NOTE-71 `seat_unit`＝C-RW24 同一條），或 (ii) 有 A11 **座級**長停事件。
         整機單不展開到座；沒有座級事件的座不出現。
       · `rates_by_eid`＝**母機**那一格的速率（WP-C2 A 尺下＝`r_seat × 安裝座數`）⇒
         `rate × f` 乘開恰為 `r_seat`＝「一座值多少」（C-RW25 的算術形，這裡不另取一次）。
       · `erw_ctx`＝`erw_prepare` 輸出（`None`＝框 inert ⇒ 座列與整頁一起退回 24h 軸）。
       · `sub_unit_ages`＝`{母機 eid: {unit: 末筆年齡小時}}`＝**`build_board` 手上那一份
         `sub_unit_ages_by_eid` 原封不動**（`#128`-A／§6.9 第 5 條；形①鍵＝`nest:n`、
         形②鍵＝子 EID，與 `installed_units` 同一套 token）⇒ 每座取自己那一格塞進
         `now["mes_age_h"]`。**不傳＝逐位元同加此 kwarg 前**（座列的第二行就不出現）。
         ⚠ 這裡**不重算、不折算、不拿母機的年齡代打**：母機那一格是母機的事實，座列印
           母機的末筆就是 C-RW17「EID 級數字平均分攤給 nest」的同型錯誤。

       ⚠ 派工單原簽名另有 `seat_factors=`／`rate_kind_by_eid=`，**刻意不收**：
         座列的係數由裁決寫死＝「一座」，唯一出處是 `board_capacity`（C-CF3），從
         `seat_factors` 轉一手就是第二份來源；`rate_kind` 是 board 層的純顯示附掛
         （同機台列，`build_row` 也沒看過它）⇒ 由 `build_board` 統一附掛。"""
    win_end = _coerce_dt(win_end)
    day_wins = list(day_wins or ())
    if win_end is None or not day_wins:
        return {}
    win_start = day_wins[0][1]
    cat = {e: (a, n) for a, n, e in (catalog or ())}
    rates_by_eid = rates_by_eid or {}
    out = {}
    for parent in _wcap.machines_with_units():
        if parent in _wcap.PARENT_EXPANSION:
            continue                              # 形③：Gen2 不動（`#125` 裁決）
        if parent not in cat:
            continue
        try:
            units = _wcap.installed_units(parent)
            if not units:
                continue
            kind = _wcap.unit_kind(parent)
            unit_set = frozenset(units)
            area_key, pname = cat[parent]
            hits = {}                             # 座 → [(wo, [(s, e), …]), …]
            for w in (wos_by_eid or {}).get(parent) or ():
                if not isinstance(w, dict) or w8.wo_is_cancelled(w):
                    continue                      # 取消／作廢單不產段（拍板①、同 `wo_segments`）
                for u, clips in _seat_wo_clips(w, unit_set, win_start, win_end).items():
                    if clips:
                        hits.setdefault(u, []).append((w, clips))
            for u in units:                       # (ii) A11 座級長停：沒有工單也要浮出
                if u not in hits and _seat_dormant_event(parent, u, kind, dormant_events):
                    hits[u] = []
            if not hits:
                continue
            _ages = (sub_unit_ages or {}).get(parent) or {}
            rows = [_seat_row(parent, pname, area_key, u, units, kind, hits[u], day_wins,
                              win_start, win_end, erw_ctx, rates_by_eid.get(parent),
                              dormant_events, _ages.get(u))
                    for u in units if u in hits]
        except Exception:
            continue                              # 一台算壞＝那台沒有座列（不主張、不炸頁）
        if rows:
            out[parent] = rows
    return out


def build_row(entry, wos_for_eid, day_wins, win_end, mode, mes_last_h=None, mes_available=False,
              quiet_h=QUIET_H, dormant=None, inherited=None, wo_mes_guard_h=WO_MES_GUARD_H,
              dormant_events=None, mes_covered=False,
              erw_ctx=None, rate_per_mh=None, mes_measurable=False, mes_any_last_h=None,
              mes_last_designation=None, mes_desig_available=False, sub_units=None,
              seat_factor=None):
    """entry＝(area_key, name, eid)；wos_for_eid＝該 EID 的 normalize_wo 輸出列表。
       mode＝"demo"/"live"/"snapshot"（保留供頁面文案；本列邏輯不因 mode 分叉）。
       mes_last_h／mes_available＝該 EID 的 MES 近期生產證據（多源 Now；見 _now_block）。
       mes_covered＝該 EID 在證據源涵蓋範圍內（_now_block 規則④；預設 False＝舊行為逐位不變）。
       quiet_h＝⚠ 久靜門檻小時（見 QUIET_H）。
       dormant＝該 EID 的 `wdorm.dormant_info(eid, 窗末台北日)` 或 None（A11 過渡橋；
         **None＝行為逐位元同加此參數前**）。
       inherited＝該 EID 的 A10 母單展開 entries（`board_scope.expand_for_children` 輸出的
         該工位那一份）或 None。母單開在**不在 `EQMT_CATALOG` 的母機**上（`EID-24742`／
         `EID-16077`／`EID-27600`），今天在本頁**完全不顯示**；展開後那些時數落到子工位列。
         **None＝行為逐位元同加此參數前**（selftest 有逐位釘）。
         ⚠ 配套鐵則（拍板⑤）：母單 PM 全量繼承 ⇒ **子工位停機時數不可相加當機器停機時數**
           （母機保養 10h ⇒ 四個工位各記 10h、不是機器停 40h）。頁面 `?` 面板與 CSV meta 有講。

       → row＝{eid, name, area, dup, segments, cells:{date:cell}, total_stop_h,
               avail_pct, avail_delta_pp, wos_win, wos_win_inh, dormant, wo_mes_conflict, mes_covered,
               now:{bucket,verdict,wo_no,state,mes_last_h,stale}}。
       mes_covered＝**additive 回聲**（WP-D）：把同名參數原樣掛回 row（診斷／smoke 用；
         語意＝「這次的 `mes_by_eid` 有沒有這個 key」＝`_now_block` 規則④那一顆）。
       mes_measurable＝**additive**（WP-D；`row_visibility` 判「量不到」用的那一顆）＝
         這台在不在 **730 天普查集合**（`MES_MEASURABLE_EIDS`）。⚠ **與 `mes_covered` 不是同一件事**
         ——live 的 `mes_by_eid` 對名冊全鋪 key ⇒ `mes_covered` 恆 True（審查 HIGH-1）。
         預設 False＝「不知道量不量得到」，呼叫端沒傳就當量不到（收窄側＝安全側：摺）。
       wo_mes_conflict＝(c) 交叉揭露結果（`wo_mes_conflict()` 輸出或 None）——**additive、
         只揭露不改數字**；上面每一個數字（stop_h／A%／cells）都與沒有這個欄時逐位相同。
       wo_mes_guard_h＝(c) 的護欄小時（見 `WO_MES_GUARD_H`）。
       dormant_mes_conflict＝§10 一致性揭露（`dormant_mes_conflict()` 輸出或 None）——同樣
         **additive、只揭露不改數字**：A% 仍是「—」、dormant 旗標不動、**不自動解除登記**。
       segments＝wo_segments 輸出（全 WO-backed；drill / cell band 直接吃）。
       無工單→segments []、cells 全 stop_h 0、total_stop_h 0、A% 100.0（誠實工單口徑值）。
       **長停列 A%＝None**（顯「—」）——`SPEC-DORMANT-CAPACITY.md` §3.2：對一台登記為不排生產的
       機器印「100.0%」＝替拔線的機器背書。停機 heatmap（工單口徑）**不受影響**：真有工單就照顯，
       長停不代表沒人在修它。

       erw_ctx＝**#39 該跑窗 context**（`erw_prepare` 輸出；`SPEC-RUNWINDOW.md` v1）。
         · **None＝框 inert** ⇒ `avail_pct`／Δ 走舊 24h 軸（`availability()`）＝§5.3 fallback，
           呼叫端（board）帶顯著標示；row["erw"] 恆 None、row["equiv_ph"] 恆 None。
         · 有 ctx ⇒ `avail_pct`／Δ **換軸**為妥善率（分母＝該跑窗）；非名冊機台 A%＝None
           （C-RW9「—」）；row["erw"]＝`erw_row` 輸出（含 time bar 材料與階梯紀錄）。
       rate_per_mh＝該站實際生產速率（顆/機台小時；§6.2 ① 產出當量）。None＝不換算；
         有值 ∧ 窗內停機 > 0 ⇒ row["equiv_ph"]＝int（恆帶 ≈ 顯示、上限語意在 tooltip）。
         ⚠ 長停列 A%／Δ／erw／equiv 全 None（對登記不排生產的機器印任何指標都是替它背書）。

       seat_factor＝**座係數**（`#110` (2)(4)；`seat_factors_for()` 輸出裡屬於這一台的那一份
         或 None）。`None`＝係數 1.0 ⇒ **整列逐位元同加此參數前**（selftest 有逐位釘）。
         有值 ⇒ 只乘在**當量**上：`equiv_ph = round(stop_h × rate × f_win)`、逐日格
         `round(day_stop_h × rate × f_day)`；門檻（≥0.05h、≥1PH）一個位元不動。
         ⚠⚠ **A%／Δ／stop_h／segments／cells 永不按座**（C-CF8＝機台才是可用度單位；
           座只是產能單位）——這個參數碰得到的東西只有 `equiv_ph` 那兩個位置。
         row 另加四個 additive 欄（顯示層用）：`seat_f`（整窗係數）／`seat_k`（受影響座數，
         不一致⇒None）／`seat_n`（安裝座數）／`seat_wos`（逐工單 `{wo_no, units, k}`）。
         沒有座／長停／框 inert ⇒ 四欄皆 None（鍵恆存在＝模板不必分辨「沒鍵」與「1.0」）。"""
    area_key, name, eid = entry
    dup = eid in KNOWN_DUP_EIDS
    wos = wos_for_eid or []
    win_end = _coerce_dt(win_end)
    win_start = day_wins[0][1]

    # ★ 長停工位不得再計 downtime（2026-08-14 Chris 裁決）——**直接**開在它身上的單也一樣。
    #   母單展開那條線已在 clip 構造期扣過（`board_scope.child_clips`），這裡補的是
    #   「工單直接開在名冊內的長停 EID 上」那條腿。被扣掉的段落進 `dormant_dropped` 揭露。
    segments, drop_direct = dormant_split_segments(
        wo_segments(wos, win_start, win_end), eid, dormant_events)
    inh_segs = _inherited_segments(inherited, win_start, win_end)     # A10 母單展開（無則 []）
    if inh_segs:
        segments = sorted(segments + inh_segs, key=lambda s: (s["start"], s["work_order_no"]))
    # 帳（揭露行材料）只收**直接單**：母單遺傳被扣的段照扣但不記帳（2026-08-18 裁決，
    # 理由見 dormant_dropped_account docstring——結構必然不是異常、每張母機工單都產一行＝噪音）。
    dropped_acct = dormant_dropped_account(drop_direct)
    buckets = bucket_segments_by_day(segments, day_wins)
    cells = {ds: day_cell(buckets[ds]) for ds, _, _ in day_wins}
    total_stop_h = sum(c["stop_h"] for c in cells.values() if c)
    a_win, _a_yd, a_delta = availability(cells, day_wins)

    # WO 活躍窗投影（drill 工單卡 fallback：segment 沒對到單時仍能列出當日相交的單）。
    # ★ 取消／作廢單同樣不入（2026-08-11 拍板①的**同一條**判定）：使用者原始抱怨就是
    #   「取消的維修單還出現在板上」——只擋色塊、卻讓 drill 的「未產段的開放工單」摘要繼續
    #   把它列成一行「維修」（而那一行沒有任何『已取消』標記）＝抱怨只解了一半、而且更誤導。
    #   ⚠ 這**不是**藏資料：那張單在 cmms 仍在、drill 的深連結一直指得到；本頁只是不再把它
    #     當成「這台機器此刻/當日的維修事實」。
    wos_win = []
    for w in wos:
        if not isinstance(w, dict):
            continue
        if w8.wo_is_cancelled(w):
            continue
        win = w8.wo_active_window(w, now=win_end)
        if win is None:
            continue
        ws, we = win
        cur_st, cur_hr = wo_current(w)                # 現在狀態 + 現在 hold_reason（drill 卡用）
        wos_win.append({"wno": w["work_order_no"], "start": ws, "end": we or win_end,
                        "work_type": w.get("work_type"),
                        "status": cur_st, "hold_reason": cur_hr})
    # ★ §18.37：A10 母單的 scope 已在 `inherited` 裡展開，右窗／機台卡／維修窗也必須
    # 看得到同一張未結單。`wos_win` 是既有消費者的契約，逐位元不動；繼承單另投影到
    # additive `wos_win_inh`，形狀完全同前者、只多母機出處。材料零新取數。
    wos_win_inh = []
    for ent in inherited or ():
        if not isinstance(ent, dict):
            continue
        w, clips = ent.get("wo"), ent.get("clips") or []
        if not isinstance(w, dict) or not clips:
            continue
        if w8.wo_is_cancelled(w):
            continue
        win = w8.wo_active_window(w, now=win_end)
        if win is None:
            continue
        ws, we = win
        # 母單的活躍窗要與這個子工位真正落在 scope 裡的 clips 相交：母單已結、或 scope
        # 已在窗內縮走時，`end` 不得再被假裝成 `win_end` 而誤列為未結。
        try:
            cs, ce = clips[0][0], clips[-1][1]
            start = ws if ws >= cs else cs
            end = (we or win_end) if (we or win_end) <= ce else ce
        except Exception:
            continue
        if end <= start:
            continue
        cur_st, cur_hr = wo_current(w)
        wos_win_inh.append({"wno": w.get("work_order_no"), "start": start, "end": end,
                            "work_type": w.get("work_type"),
                            "status": cur_st, "hold_reason": cur_hr,
                            "inherited_from": ent.get("parent_eid")})

    # #39 該跑窗換軸（A3 單一真相）：ctx 在 ⇒ A%／Δ＝妥善率（分母＝該跑窗）；
    #   非名冊 ⇒ None（C-RW9「—」）。ctx 不在 ⇒ 保持上面的舊 24h 軸值（inert fallback）。
    erw = None
    equiv_ph = None
    # ★ 座係數（§6.3-b／C-RW24）：None＝1.0＝**整列逐位元同加此參數前**。
    _sf = seat_factor if isinstance(seat_factor, dict) else None
    _f_win = 1.0
    if erw_ctx is not None:
        erw = erw_row(eid, buckets, day_wins, erw_ctx)
        if erw is None:                                  # 非名冊：不適用
            a_win = a_delta = None
        else:
            a_win, a_delta = erw["avail"], erw["delta"]
            if _sf is not None:
                _v = _sf.get("win")
                _f_win = 1.0 if _v is None else float(_v)
                # 逐日係數先掛進格（**與 equiv 同一份事實**，tooltip 與數字永不各算各的）。
                for _ds, _d in (erw.get("days") or {}).items():
                    _vd = (_sf.get("days") or {}).get(_ds)
                    _d["seat_f"] = 1.0 if _vd is None else float(_vd)
                    # ⚠ 格的 k 是**當日**的 k（`days_k`），不是整窗那顆——拿窗的 k 配當日的
                    #   係數，同一個 tooltip 裡兩個數字會對不起來。
                    _d["seat_k"] = (_sf.get("days_k") or {}).get(_ds)
                    _d["seat_n"] = _sf.get("n")
            if rate_per_mh and (erw["stop_h"] or 0.0) > 0 and rate_per_mh > 0:
                equiv_ph = int(round(erw["stop_h"] * float(rate_per_mh) * _f_win))
                if equiv_ph <= 0:
                    equiv_ph = None
            # ★ 2026-08-23 晚 Chris 裁決①（SPEC-RUNWINDOW §6.3）：當量**進格**（格子第二行）。
            #   rate 只有在這裡拿得到 ⇒ 逐日換算補掛在 erw["days"]，`erw_row` 維持純窗口徑。
            #   ⚠ 逐格顯示、恆帶 ≈、**永不做跨格/跨列 Σ**（C-RW13／§7-7）。
            if rate_per_mh and rate_per_mh > 0:
                for _d in (erw.get("days") or {}).values():
                    _sh = _d.get("stop_h") or 0.0
                    _fd = 1.0
                    if _sf is not None and _d.get("seat_f") is not None:
                        _fd = float(_d["seat_f"])       # ⚠ 不可寫 `or 1.0`：0.0 是合法係數
                    _e = int(round(_sh * float(rate_per_mh) * _fd)) if _sh >= 0.05 else 0
                    _d["equiv_ph"] = _e if _e >= 1 else None

    if dormant:                       # 長停 → A%／Δ／erw／equiv 全「—」（不是 0、也不是 100）
        a_win = a_delta = None
        erw = None
        equiv_ph = None
    if erw is None:
        _sf = None                    # 框 inert／非名冊／長停 ⇒ 座係數四欄一律 None（不主張）
    # (c) MES 交叉揭露：吃**這一列已經算好的** segments 與 Now 欄那份 mes_last_h ⇒ 零新查詢。
    #     mes 探測失敗（mes_available=False）⇒ 不揭露（量不到不是矛盾）。
    conflict = (wo_mes_conflict(segments, mes_last_h, win_end, wo_mes_guard_h)
                if mes_available else None)
    # §10 一致性揭露：登記長停 ∧ MES 窗內有生產 ⇒ 揭露一行。**同一份 mes_last_h、零新查詢**
    #     （判準＝「有沒有發出新的 MES 查詢？有就是哨兵，沒有才是揭露」）。
    #     與上面 (c) 是兩件事、兩列渲染：那條講工單、這條講登記。
    dconflict = (dormant_mes_conflict(dormant, mes_last_h, win_end)
                 if mes_available else None)
    return {"eid": eid, "name": name, "area": area_key, "dup": dup,
            "segments": segments, "cells": cells, "wos_win": wos_win,
            "wos_win_inh": wos_win_inh,
            "total_stop_h": total_stop_h, "avail_pct": a_win, "avail_delta_pp": a_delta,
            # #39 該跑窗（A3 換軸後 avail_pct 本身就是妥善率；erw＝bar 材料＋階梯紀錄）。
            #   None＝框 inert（舊軸 fallback）或非名冊或長停 ⇒ bar 不渲染。
            "erw": erw,
            # §6.2 ① 產出當量（顆＝PH）；None＝不顯示（零停機／無速率／inert／長停）。
            "equiv_ph": equiv_ph,
            # §6.3-b 座係數（`#110` (2)(4)）：**四欄恆存在**、無座／長停／inert ⇒ 皆 None。
            #   ⚠ 只描述當量怎麼折的，A%／stop_h／cells 一個位元都沒看過它們（C-CF8）。
            "seat_f": (None if _sf is None else _f_win),
            "seat_k": (None if _sf is None else _sf.get("k")),
            "seat_n": (None if _sf is None else _sf.get("n")),
            "seat_wos": (None if _sf is None else list(_sf.get("wos") or ())),
            "dormant": dormant or None, "wo_mes_conflict": conflict,
            "dormant_mes_conflict": dconflict,
            # ★ 長停排除帳（additive；`None`＝這一列什麼都沒被扣 ⇒ 揭露行不渲染）。
            #   **不是 caveat 也不是矛盾**：它講的是「本頁刻意沒把這些時數算進去」，
            #   讓「修一台停用機」不會因為新規則而隱形（SPEC-DORMANT §5 的配套）。
            "dormant_dropped": dropped_acct,
            # ★ WP-D（2026-09-02）：`mes_covered` 由**參數**升格成 row 的 additive 欄位。
            #   它本來只餵給 `_now_block` 規則④；`row_visibility` 需要在**列建好之後**回答
            #   「這台在不在證據源涵蓋範圍內」，而那件事**無法從 `now` 反推**：
            #   缺 key（未涵蓋）與值 None（涵蓋、整窗無生產）在 `now` 上都是 `mes_last_h=None`，
            #   只有 up_idle 分支的 `mes_nosig` 分得開——DOWN／dormant 列連那個鍵都沒有。
            #   ⇒ 與其在別處寫第二份推導（漂移的種子），不如把已知事實直接掛上來。
            #   **純 additive：上面每一個數字一個位元不動。**
            #   ⚠ 兩顆旗不可互換：`mes_covered`＝這次探測有沒有這個 key（`_now_block` 規則④）；
            #     `mes_measurable`＝730 天普查看過這台沒有（`row_visibility` 判「量不到」）。
            "mes_covered": bool(mes_covered),
            "mes_measurable": bool(mes_measurable),
            "now": _now_block(wos, win_end, mes_last_h=mes_last_h, mes_available=mes_available,
                              mes_any_last_h=mes_any_last_h,
                              mes_last_designation=mes_last_designation,
                              mes_desig_available=mes_desig_available,
                              quiet_h=quiet_h, dormant=dormant, inherited=inherited,
                              mes_covered=mes_covered, sub_units=sub_units)}


# ---------------------------------------------------------------------------
# 7d) row_visibility / collapse_summary — 預設摺疊「沒什麼好講」的列（WP-D、2026-09-02）
# ---------------------------------------------------------------------------
#   使用者回饋（Chris 轉述）：「table 只顯示此期間有任何紀錄的機台，其餘沒紀錄的摺疊
#   （但允許用戶手動展開）」。名冊裡有相當比例的機台在任何一個 7 天窗內完全沒有工單，
#   長官要掃的紅色被它們稀釋掉。
#
#   ★★ 三條界線（Chris 2026-09-02 同意的三修正版；改這裡前逐條讀）：
#     (D1) **摺的判準不是「無工單」，是「無工單**而且**沒什麼好講」。**
#          無工單的列分四種，只有三種可以摺：
#            · MES 近期有生產（涵蓋內、有 age、不久靜）＝安靜的健康機 → 摺
#            · 無 MES 覆蓋（治具／烤箱／顯微鏡那類本就不寫 MES 的機器）→ 摺
#            · ◌ 長停（登記的事實）→ 摺，**但摘要句點名**（C-D3「排除必須看得見」的延伸：
#              摺疊也是一種排除，不得靜默）
#            · **有 MES 覆蓋卻無生產或久靜、又沒有任何工單 ＝「沉默機」→ 絕不摺。**
#              這是全表最該被看見的一列——沒有人開單、機器也沒在跑。摺掉它＝
#              **靜默排除＝獎勵不修機器**（`SPEC-DORMANT-CAPACITY.md` C-D3 的精神）。
#     (D2) **摺疊是顯示層、不是口徑。** 本節兩支函式**不改任何數字**：`cells`／`segments`／
#          `total_stop_h`／`avail_pct`／Δ／headline／CSV 全部照整份名冊算。摺疊只決定
#          模板畫不畫那一列。⇒ 兩支都是**純函式**、吃 `build_row` 產出的 row dict、零 I/O。
#     (D3) **`mes_available=False`（整板沒有 MES 證據，例如工單源在但探測失敗）⇒ 分不出
#          沉默機** ⇒ 無工單列一律摺，**但摘要句必須說出「分不出來」**（`unknown_mes`）。
#          量不到就說量不到——不得把「分不出」靜默地當成「沒事」。
#
#   ⚠ **「有工單」用的是「這一列有沒有任何 WO 段」，不是「有沒有 countable 段」。**
#     純 uptime-hold 的列（等機台空檔／試跑）在頁上是看得見的 `.eq-uptonly` 透明格＋組成條
#     ——2026-07-10 Chris 裁決「uptime 態要在第一層看得到、『只活在 drill』已被否決」
#     （記憶 [[equipment-page-encoding-rules]] 第 4 條）。摺掉它 ＝ 把那條裁決撤銷得更徹底
#     （連 drill 都點不到）。⇒ 有段就是有紀錄，**不分計不計停機**。

_VIS_SHOWN = "shown"
_VIS_COLLAPSED = "collapsed"

# ★★ 「MES 量得到這台嗎」＝**靜態普查集合**，不是 `mes_by_eid` 的 key 集合（2026-09-02 審查 HIGH-1）。
#   踩過的坑：`app._build_eqm_live` 把狀態檔的 age **對整份名冊全鋪 key**
#   （`{e: _ages.get(e) for _a,_n,e in EQMT_CATALOG}`、查無＝值 None）⇒ live 上
#   `mes_covered` 恆 True ⇒ `no_mes` 分支**永遠走不到**，那批治具／烤箱／顯微鏡
#   （本來就不寫 MES）全被判成「沉默機」、永不摺，還被 tooltip 定性成「該問一句」。
#   ⇒ 覆蓋這件事必須問一個**與當次探測無關**的來源：730 天普查看過這台機沒有。
#   `board_tags.PRODUCT_TAGS` 的鍵集合＝普查窗內出現過的 EID（`SPEC-PRODUCT-TAGS.md` §3）。
#   ⚠ 這是**能力集合的副產品**，不是標籤本身——本節**只用它的鍵**、不讀任何標籤值
#   （C-PT4：標籤只描述能力，不得用來過濾別的東西）。
#   ⚠ `mes_covered`（`_now_block` 規則④的那顆）**一個位元不動**——Now 欄的 UP 三形不受本節影響。
#
#   ★ **必須套別名折疊**（2026-09-03；`SPEC-PRODUCT-TAGS.md` §2.5.3／§2.5.3-a）。
#   `PRODUCT_TAGS` 是**純普查**表：形④改名型的鍵是**別名** `EID-38041`／`EID-38072`，
#   **主 EID `EID-38106` 不在裡面**。而 `mes_timeline` 早已把 `38041→38106` 改名、`38072` 丟棄
#   ⇒ `/equipment` 名冊第 39 站那一列（`("zc","Top O Ring Lubrication","EID-38106")`）拿純普查鍵
#   去問「量得到嗎」永遠得到 False ⇒ 無工單時被 `no_mes` 摺掉，並被定性成「MES 結構上量不到」。
#   **那是錯的**：它有大量觀測段，久靜時本該以 `silent`（沉默機）留在表上被看見。
#   ⇒ 併入**「任一別名出現在普查表裡」的折疊主 EID**、再**減去 `wtags.ALIAS_OF` 的別名**——
#   別名本來就不成列（名冊只有主 EID），減掉是為了讓集合與名冊／`mes_timeline` 同一個口徑，
#   不留兩顆永遠比不到的殭屍鍵。⚠ 別名表**只從 `board_tags` 讀**（它自己 import `mes_timeline`）、
#   **不得在本檔抄第二份**。⚠ 機制 (b)（`07940`／`07905`／`26554`）不在 `ALIAS_OF` 裡 ⇒ 恆不受影響。
#   ⚠ **判準是「鍵在不在普查表」、不是 `FOLDED_TAGS` 的成員資格**（C-PT4：只用鍵、不讀標籤值）。
#   `FOLDED_TAGS` 只收**別名 `origin=="observed"`** 的主 EID（`SPEC-PRODUCT-TAGS.md` §2.5.3-a
#   第三條線）⇒ 哪天有個主 EID 的別名**全是 `default`**（＝普查掃到了、只是沒過 §2.5 的門檻），
#   它就不在 `FOLDED_TAGS` 裡、又會被判成 `no_mes`——但那台其實**量得到**，只是產品訊號沒過門檻。
#   「量不量得到」是覆蓋問題，與標籤有沒有過門檻無關 ⇒ 這裡只問鍵。
MES_MEASURABLE_EIDS = ((frozenset(wtags.PRODUCT_TAGS)
                        | frozenset(m for m, _als in wtags.ALIAS_OF.items()
                                    if any(a in wtags.PRODUCT_TAGS for a in _als)))
                       - frozenset(a for _als in wtags.ALIAS_OF.values() for a in _als))

# ═══ #104(b)「生產（推斷）」的名冊（`SPEC-LANDING.md` §18.26.2；鐵則 **C-LD44**）════════
#   **這是什麼**：上面 `MES_MEASURABLE_EIDS` 之外的機台（治具／烤箱／顯微鏡）在 MES 上
#   沒有任何設備 co-tag ⇒ landing 只能畫「量不到」（#104(a) 的 `nomes` 桶）。但其中有幾台
#   所服務的**站**在 MES 上有 lot 段、而那一站的段**沒有任何設備掛標**（`pct_no_ast=100%`、
#   `n_distinct_eid=0`）⇒「站在動」這件事只可能是這台在動 ⇒ 可以**推斷**它此刻在生產。
#   `{站 class: (EID, …)}`；**push 與 landing 都 import 這一份、絕不各抄一份**。
#
#   ★ 出處＝`_PROBE-NOMES-STATION.sql`（Chris 2026-09-08 19:38 SSMS 實跑）的結果檔
#     `_PROBE-NOMES-STATION-RESULT.tsv`：站的三個數字讀 STEP2、機台的歸站讀 STEP4 的
#     `master_station`（＝`equipment_t.sgcPK` → `process_segment_class_t`）。
#     逐條 `# Evidence:` 抄的就是那兩個結果集的欄位值，**本檔不抄第二份數字**。
#
#   ⚠ **入選的三個條件（缺一不可，C-LD44）**：
#     ① 站是 lot-only（STEP2 `pct_no_ast = 100.00`、`n_distinct_eid = 0`）——站上有別台
#        掛得住標，就代表「站在動」是**別台**在動，不得推給量不到的這台；
#     ② 這台機不在 `MES_MEASURABLE_EIDS`（量得到的機台永遠走真訊號、不准走推斷）；
#     ③ 30 天窗內該站有**生產級**段（STEP2 `n_seg_prodgrade > 0`）——零生產級段的站在
#        規則下永遠點不亮（`segASSYMicroscopeInspection` 109 段全非生產級 ⇒ 不列）。
#   ⇒ 探針抽到的機台裡只有這幾台過關；**其餘仍是 `nomes`，那是事實不是待辦**
#     （不列的理由逐條＝`SPEC-LANDING.md` §18.26.1）。
STATION_LOT_PROXY = {'segLotOnlyTaping': ('EID-54585', 'EID-54558'),
                     'segLotOnlyVisual': ('EID-55590',),
                     'segLotOnlyPullTest': ('EID-55416',)}

# 反查表（`{EID: 站}`）＝**由上表衍生、不另抄一份**；同站多台的台數也從這裡數。
STATION_LOT_STATION_OF = {e: c for c, es in STATION_LOT_PROXY.items() for e in es}


def row_visibility(row, mes_available):
    """單列顯示決策（純函式）→ `("shown"|"collapsed", reason_key)`。

       `row`＝`build_row` 的輸出（本函式**只讀**，不 mutate）。
       `mes_available`＝整板是否有 MES 證據（`build_board` 的同名旗標）。

       reason_key（六個，穩定字面值——模板與 smoke 都吃它）：
         · `record`     ＝有紀錄 ⇒ 顯示（任一：有 WO 段〔含 uptime／繼承段〕／`total_stop_h>0`／
                          `now.bucket=="down"`／**有長停排除帳 `dormant_dropped`**）
         · `silent`     ＝沉默機 ⇒ **顯示**（MES 量得到這台、卻無生產或久靜，且無工單）
         · `dormant`    ＝◌ 長停 ⇒ 摺（摘要點名）
         · `unknown_mes`＝整板無 MES 證據、分不出沉默機 ⇒ 摺（摘要要講）
         · `no_mes`     ＝**不在 730 天普查集合裡**（MES 結構上量不到這台）⇒ 摺
         · `mes_prod`   ＝MES 近期有生產、不久靜 ⇒ 摺

       ★★ 「量得到嗎」讀 **`row["mes_measurable"]`（靜態普查集合，見 `MES_MEASURABLE_EIDS`）**，
          **不是** `row["mes_covered"]`（那是 `mes_by_eid` 有沒有這個 key ＝ `_now_block` 規則④用的）。
          兩者在 live 會分岔：app 層把 age 對名冊全鋪 key ⇒ `mes_covered` 恆 True。
          ⇒ 拿 `mes_covered` 判覆蓋，`no_mes` 分支在 live 永遠是死碼（2026-09-02 審查 HIGH-1）。

       ★ 判定順序是規格的一部分（不可重排）：紀錄 → 長停 → 無證據 → 量不到 → 沉默 → 有生產。
         長停排在 MES 之前，因為**登記是事實、量測是佐證**（C-D1）：demo 的
         `EQM_DORMANT_CONFLICT_SHOWCASE_EID` 就是「登記長停但 MES 有 age」的那一台，
         照 MES 分會被歸成「安靜的健康機」而失去點名。

       ★ `dormant_dropped` 也算「有紀錄」（2026-09-02 審查 MED-1）：長停工位窗內**有人直接開單**
         時，`build_row` 已把那些 countable 段全數移進排除帳 ⇒ `segments` 空、`total_stop_h` 0、
         單若已完工則 Now 也不是 DOWN ⇒ 只看前三條會把它摺掉。**而那正是 C-D3 要抓的真異常**
         （「登記說停用的機台有人開單」），揭露行還在頁上指名它、列卻不見了。"""
    if not isinstance(row, dict):
        return (_VIS_SHOWN, "record")
    # ★ `#125` (b)(c)（C-RW26）：**座列存在即顯示**——它只在「窗內有工單範圍指到這座／該座
    #   被登記長停」時才被合成出來，那正是 D1 講的「有東西好講」。`vis_reason=None`＝
    #   它不屬於那六個 reason 的任何一個（那六個講的是**機台**為什麼沒東西好講）。
    #   ⇒ 座列永不進 `collapse_summary` 的任何一個桶（摘要句逐位元同加座列前）。
    if row.get("is_seat"):
        return (_VIS_SHOWN, None)
    now = row.get("now") or {}
    # 有紀錄＝這一列在畫面上本來就有東西（色帶／組成條／DOWN 徽章／被揭露行點名）——一律留著。
    if (row.get("segments") or (row.get("total_stop_h") or 0.0) > 0
            or now.get("bucket") == "down" or row.get("dormant_dropped")):
        return (_VIS_SHOWN, "record")
    if row.get("dormant"):
        return (_VIS_COLLAPSED, "dormant")
    if not mes_available:
        return (_VIS_COLLAPSED, "unknown_mes")
    if not row.get("mes_measurable"):
        return (_VIS_COLLAPSED, "no_mes")
    age = now.get("mes_last_h")
    if age is None or now.get("stale"):
        return (_VIS_SHOWN, "silent")        # ★ 沉默機：全表最該被看見的一列
    return (_VIS_COLLAPSED, "mes_prod")


def collapse_summary(rows, mes_available):
    """一個區的摺疊摘要（純函式）→ dict 或 **None（這一區沒有任何列被摺）**。

       → `{"n": 摺掉幾列, "mes_prod": n, "no_mes": n, "unknown": n,
            "dormant": [顯示名, …], "unknown_mes": bool}`

       · `dormant` 存 **`顯示名 (EID)`** ＝摘要句要點名（C-D3）。**EID 是必要的**：名冊裡有
         10 組重名（`AOI Frame Straightness`／`AttachRig`／`E-Tester`／`Thermal Cycle Oven`…），只寫顯示名
         的話讀者分不出被摺的是哪一台（2026-09-02 審查 LOW-2）。名字缺 → 只寫 EID。
       · `unknown_mes`＝有列是因為「整板沒有 MES 證據」而被摺 ⇒ 摘要句要加一句
         「分不出沉默機」。它是 `unknown > 0` 的同義詞，另立一鍵只為讓模板讀起來像人話。
       · **回 None＝模板整列不渲染** ⇒ 沒有東西可摺的區（或整板）版面**逐位元不變**
         （同 `conflicts`／`dormant_drops` 那條「例外才出現」紀律，記憶 [[ui-addition-restraint]]）。
       純函式、零 I/O、不 mutate 輸入。"""
    out = {"n": 0, "mes_prod": 0, "no_mes": 0, "unknown": 0, "dormant": []}
    for r in rows or []:
        state, why = row_visibility(r, mes_available)
        if state != _VIS_COLLAPSED:
            continue
        out["n"] += 1
        if why == "dormant":
            _nm, _eid = r.get("name"), r.get("eid")
            out["dormant"].append(f"{_nm} ({_eid})" if _nm and _eid else str(_nm or _eid or "?"))
        elif why in ("mes_prod", "no_mes"):
            out[why] += 1
        else:                # `unknown_mes` → `unknown` 桶；未知 reason 也落這裡＝寧可多說一句
            out["unknown"] += 1
    if not out["n"]:
        return None
    out["unknown_mes"] = bool(out["unknown"])
    return out


# ---------------------------------------------------------------------------
# 8) build_board — 全設備會議板（分區 + headline）
# ---------------------------------------------------------------------------

def _headline(rows, day_wins):
    """會議 headline：以「昨日」（day_wins 倒數第二欄＝完整日；只有今日 partial 時退今日）
       彙整工單停機、標出停最久機台；並統計現在 Up/Down 分布。"""
    yd_label = day_wins[-2][0] if len(day_wins) >= 2 else day_wins[-1][0]
    # dup EID 聚合面只計一次（headline 是「台」的統計、不是「列」）。
    seen, uniq = set(), []
    for r in rows:
        # ★ `#125` (b)(c)（C-RW26）：**座列永不進 headline**——headline 是「台」的統計
        #   （N/M 台有停機、現在幾台 DOWN、停最久的那一台），把座算成台會同時虛增分子與分母，
        #   而且母機列已經把同一段時數算過一次了（C-RW13 的 Σ 禁令換個地方出現）。
        #   ⚠ 這顆守門在這裡是**必要的**：`build_board` 的插入時機天然擋住了，但
        #     `headline_for_rows`（`?tag=` 重算）吃的是 `areas[].rows`＝**已經含座列**。
        if r.get("is_seat"):
            continue
        if r["eid"] in seen:
            continue
        seen.add(r["eid"])
        uniq.append(r)
    yd_by_state, yd_union, top = {}, 0.0, None
    for r in uniq:
        cell = (r.get("cells") or {}).get(yd_label)
        if not cell:
            continue
        yd_union += cell["stop_h"] or 0.0                   # 全廠昨日停機＝Σ 各機 union（真時數）
        for st, h in cell["by_state"].items():
            yd_by_state[st] = yd_by_state.get(st, 0.0) + h
        if cell["stop_h"] and (top is None or cell["stop_h"] > top["h"]):
            top = {"eid": r["eid"], "name": r["name"], "h": cell["stop_h"]}
    n_down = sum(1 for r in uniq if r["now"]["bucket"] == "down")
    n_stale = sum(1 for r in uniq if r["now"]["stale"])
    # 長停台數（A11 過渡橋）：C-D3「排除必須看得見」——窄橫列據此寫「N 個工位長停不計（◌）」。
    # additive 鍵；無長停時＝0（既有消費者行為不變）。
    n_dormant = sum(1 for r in uniq if r["now"]["bucket"] == "dormant")
    n_with_downtime = sum(1 for r in uniq if r["total_stop_h"])   # 本窗有工單停機的台數（圈定 review 範圍）
    return {
        "yd_label": yd_label,
        "yd_stop_h": yd_union,
        "yd_by_state": yd_by_state,
        "top_machine": top,
        "open_wo": n_down,           # 現在 open 工單台數＝現在 DOWN 台數（工單導向：DOWN⟺open 工單）
        "n_down": n_down,
        "n_stale": n_stale,          # ⚠ 久靜台數（狀態仍 UP、只是值得問一句；非 DOWN 主張）
        "n_dormant": n_dormant,      # ◌ 長停台數（登記的事實；A% 不計、非 DOWN 非 UP）
        "n_with_downtime": n_with_downtime,   # 窄橫列①：本窗有停機台數
        "n_total": len(uniq),                 # 窄橫列①：目錄總台數（去 dup）
    }


def headline_for_rows(rows, day_labels):
    """`_headline` 的**公開純函式入口**（給 `/equipment` 的 `?tag=` 過濾後重算 headline 用）。

       ★ 2026-09-04（Chris 二度裁決：「所有掛 R/A/M 頁簽的頁，指標會被過濾影響的都要重算」）。
       `_headline` 本來就是純函式，這一支只做兩件事、**零口徑**：
         ① 把 `board["day_labels"]`（呼叫端唯一拿得到的東西）還原成 `_headline` 要的
            `day_wins` 形狀——那支**只用到 `day_wins[-1][0]`／`[-2][0]` 兩個標籤**，
            窗的起訖它一秒都沒碰 ⇒ 補 `None` 是安全的、不是偷懶。
         ② 空 `day_labels`／空 `rows` ⇒ `None`（模板的 `H` 判斷本來就吃 falsy）。
       ⚠ **`build_board` 一個位元都沒動**：它照舊直接叫 `_headline(rows, day_wins)`，
         拿的是真的窗。把 `build_board` 改成走這一支，等於為了一個過濾路徑
         讓全站的 headline 改吃一份補過 `None` 的材料——不值得，也沒有必要。"""
    if not day_labels or not rows:
        return None
    return _headline(rows, [(ds, None, None) for ds in day_labels])


def build_board(catalog, wos_by_eid, win_end, n_days, mode, wo_status="not_configured",
                mes_by_eid=None, quiet_h=QUIET_H, dormant_by_eid=None,
                inherited_by_eid=None, wo_mes_guard_h=WO_MES_GUARD_H,
                sched_unknown=None, dormant_events=None, mes_lookback_d=None,
                closures=None, cal_coverage=None, erw_slots=None, erw_manpower=None,
                rates_by_eid=None, mes_measurable_eids=None, mes_any_by_eid=None,
                mes_designation_by_eid=None, mes_desig_available_by_eid=None,
                sub_unit_ages_by_eid=None, seat_factors=None, rate_kind_by_eid=None):
    """catalog＝EQMT_CATALOG（有序 (area,name,eid)）；wos_by_eid＝{eid: [normalize_wo 輸出]}。
       mes_by_eid＝{eid: mes_last_h|None}（多源 Now：距上次生產小時數）。
         傳 dict（含空 dict）＝MES 證據在 → Now 走「UP·生產中／UP·待命」＋⚠ 久靜；
           值 float＝距上次生產小時數（照舊）；
           **值 None（2026-08-17 lastprod 狀態檔源）＝「整窗掃過、近 mes_lookback_d 天無生產」**
             → UP·待命＋中性小字「近 N 天無生產」、不掛 ⚠（_now_block 規則④）；
           缺 eid＝該機不在證據源涵蓋範圍（探測 fallback：本就不寫 MES 或久到超窗、分不出）
             → UP·待命、無小字、不掛 ⚠（＝加值語意前的舊形，逐位不變）。
         傳 None＝無 MES 證據（探測失敗）→ Now 純工單二值（DOWN／UP）＋頁面 caveat。
         **兩種情況都不會產出 UNKNOWN**——沒工單一律 UP（2026-07-10 拍板，見檔頭 §3）。
       mes_lookback_d＝值 None 那句主張的窗寬天數（呼叫端自證據源 meta 讀來、**永不寫死**
         ——C-LP5 同精神；None＝證據源不支撐該主張 ⇒ 模板 nosig 小字不渲染）。
       quiet_h＝⚠ 久靜門檻小時（app 以 BOARD_EQM_QUIET_DAYS 換算後傳入）。
       dormant_by_eid＝{eid: dormant_info dict}（A11 過渡橋；由呼叫端以**窗末台北日**投影
         `board_dormant`）。**None／空 dict＝板逐位元同加此參數前**（selftest 有逐位釘）。
       inherited_by_eid＝{子工位 eid: entries}（A10 母單展開；由呼叫端跑
         `board_scope.expand_for_children`——live 在 app 層〔要先取 detail 拿 scope_events〕、
         demo 在 `demo_board` 內）。**None／空 dict＝板逐位元同加此參數前**（selftest 有逐位釘）。
       wo_mes_guard_h＝(c) MES 交叉揭露的護欄小時（見 `WO_MES_GUARD_H`；app 以
         BOARD_EQM_WO_MES_GUARD_H 覆寫）。
       dormant_events＝A11 登記事件流（`None`＝過渡橋常數、`[]`＝關掉）。**用途只有一個**：
         把「直接開在長停工位上的工單」逐日排除出停機統計（2026-08-14 Chris 裁決）。
         ⚠ 與 `dormant_by_eid` 必須是**同一組事實**（呼叫端一次取好餵給整頁）——這裡與
         Now 欄各自取源＝同一台機「Now 說長停、heatmap 卻照畫」。
       sched_unknown＝A11 批次現況讀回來的 `unknown[]`（我方送的 EID 在 cmms 資產主檔查無
         ＝**兩邊名冊漂了**）。**None／空 list＝版面逐位元同加此參數前**；非空 ⇒ 頁上紅字
         （cmms NOTE-34【三】硬紀律：不得忽略、不得當成「這台大概沒事」）。
       → board＝{areas:[{key,label,rows}], headline, wo_status, truncated, fallback_rate,
                 mode, win_end, n_days, day_labels, mes_available, quiet_h, conflicts,
                 dormant_conflicts, sched_unknown}。
       conflicts＝`[{eid, wo_no}]`（(c) 交叉揭露；**[]＝版面逐位元同加此欄前**）。
       dormant_conflicts＝`[{eid, since}]`（§10 一致性揭露：登記長停 ∧ MES 窗內有生產；
         同樣 **[]＝版面逐位元同加此欄前**）。
       wo_status＝"live"|"not_configured"|"snapshot_off"|"demo"|"error:<r>"（工單源接通狀態，caller 傳）。
       工單源未接通（not_configured/snapshot_off/error）＝空 wos → 全機 A% 100、頁面誠實 banner。

       closures／cal_coverage／erw_slots／erw_manpower＝**#39 該跑窗**（`SPEC-RUNWINDOW.md` v1；
         口徑在「6b」節檔頭與 `board_runwindow`，這裡只講接線）：
         · `closures`＝`calendar_closures` → `closures_with_uncertainty` 輸出（已裁到本窗、
           已過 C-PC12 兜底）。**`None`＝沒有日曆 ⇒ 整框 inert**（§5.3：A% 退回舊 24h 軸、
           `board["erw"] is None`、頁面顯著標示）。
         · `cal_coverage`＝`(from, to)` 台北牆鐘（C-PC8 篩涵蓋日）。
         · `erw_slots`／`erw_manpower`＝兩個綑級 raw doc（`load_kun_slots`／`load_kun_manpower`）；
           **None＝該源缺席 ⇒ 擴窗降級（ShiftWin−Closure）＋degraded 標示，框不 inert**。
         · `rates_by_eid`＝{eid: 顆/機台小時}（§6.2 ① 產出當量；None＝整頁不換算）。
       seat_factors＝**座係數**（`#110` (2)(4)；`seat_factors_for()` 輸出＝`{eid: {...}}`）。
         `None`／空 dict＝全列 1.0 ⇒ **整塊板逐位元同加此參數前**（selftest 有逐位釘）。
         只乘在 `equiv_ph`（列尾與逐日格）上；A%／Δ／stop_h／cells／segments／Now
         **永不按座**（C-CF8＝機台才是可用度單位、座只是產能單位；C-RW24）。
       rate_kind_by_eid＝**當量速率用的是哪把尺**（WP-C2；`{eid: "seat"|"mh_blend"}`）＝
         **純顯示欄**（tooltip 選句），board 層直接附掛成 `row["rate_kind"]`、
         `build_row` 一個位元沒看過它（同 `vis`／`vis_reason` 的分層紀律）。
         `None`／缺席 ⇒ `row["rate_kind"] is None` ⇒ 模板不印尺別那一句。
         ⚠ **它不參與任何計算**——尺別已經體現在 `rates_by_eid` 的值裡了；
           讓模板拿它去乘任何東西＝第二份口徑。
       board["erw"]＝None（inert）或 {n_days, n_days_total, n_closures, n_uncertain, degraded}；
       board["closures"]＝drill 收工線／time bar 的 closure 材料（inert ⇒ None）。

       mes_measurable_eids＝**WP-D 摺疊專用**的「MES 量得到哪些 EID」集合（`row_visibility` 唯一
         消費者；**不參與任何數字**）。`None`＝用預設 `MES_MEASURABLE_EIDS`＝730 天普查集合
         ⇒ **demo 與 live 走同一條規則**。⚠ 它**不是** `mes_by_eid` 的 key 集合——那顆是
         `mes_covered`，live 恆 True（審查 HIGH-1，理由寫在 `MES_MEASURABLE_EIDS` 上方）。"""
    day_wins = day_windows(win_end, n_days)
    win_end = _coerce_dt(win_end)
    wos_by_eid = wos_by_eid or {}
    mes_available = mes_by_eid is not None
    mes_by_eid = mes_by_eid or {}
    mes_any_by_eid = mes_any_by_eid or {}
    mes_designation_by_eid = mes_designation_by_eid or {}
    mes_desig_available_by_eid = mes_desig_available_by_eid or {}
    dormant_by_eid = dormant_by_eid or {}
    inherited_by_eid = inherited_by_eid or {}

    # #39 該跑窗 context（C-PC8 篩涵蓋日在 erw_prepare 內；None＝inert ⇒ 舊 24h 軸 fallback）。
    erw_ctx = erw_prepare(day_wins, closures, cal_coverage, erw_slots, erw_manpower)
    rates_by_eid = rates_by_eid or {}
    # WP-D 摺疊專用（零數字影響）：`None`＝730 天普查集合 ⇒ demo 與 live 同一條規則。
    _meas = (MES_MEASURABLE_EIDS if mes_measurable_eids is None
             else frozenset(mes_measurable_eids))
    # ★ 2026-09-08 子工位（`SPEC-LANDING.md` §18.23）：`{eid: {unit_id: age_h}}` →
    #   逐台一份 `[{"unit", "last_h"}, …]`，**順序＝名冊 `installed_units` 的宣告順序**
    #   （nest 1、2、3…／co-tag tuple 序）＝畫面上 nest 1 在下／在左的那個「1」。
    #   · 名冊沒有子工位的機台 ⇒ 恆 `None` ⇒ `now` 逐位元同加此欄前（**名冊絕大多數機台**；
    #     2026-09-08 深夜 AOI 退出名冊後隨之變動＝C-LD46）。
    #   · 名冊有、但這一輪沒有任何一座的資料 ⇒ 仍帶 k 筆 `last_h=None`＝「k 座、都不知道」
    #     ⇒ 畫 k 個空心格。**這比不帶更誠實**：不帶等於說「這台沒有子工位」。
    _sub_ages = sub_unit_ages_by_eid or {}
    _sub_rows = {}
    for _e in _wcap.machines_with_units():
        _units = _wcap.installed_units(_e)
        if not _units or _e in _wcap.PARENT_EXPANSION:
            continue          # 形③ 的子槽位各自是名冊上的一列（已有自己的方塊），不畫格
        _ages = _sub_ages.get(_e) or {}
        _sub_rows[_e] = [{"unit": u, "last_h": _ages.get(u)} for u in _units]

    rows = [build_row(e, wos_by_eid.get(e[2], []), day_wins, win_end, mode,
                      mes_last_h=mes_by_eid.get(e[2]), mes_available=mes_available,
                      mes_any_last_h=mes_any_by_eid.get(e[2]),
                      mes_last_designation=mes_designation_by_eid.get(e[2]),
                      mes_desig_available=mes_desig_available_by_eid.get(e[2], False),
                      quiet_h=quiet_h, dormant=dormant_by_eid.get(e[2]),
                      inherited=inherited_by_eid.get(e[2]),
                      wo_mes_guard_h=wo_mes_guard_h,
                      dormant_events=dormant_events,
                      mes_covered=(e[2] in mes_by_eid),
                      erw_ctx=erw_ctx,
                      rate_per_mh=rates_by_eid.get(e[2]),
                      mes_measurable=(e[2] in _meas),
                      sub_units=_sub_rows.get(e[2]),
                      seat_factor=(seat_factors or {}).get(e[2]))
            for e in catalog]

    # ★ WP-D 顯示層摺疊（2026-09-02；口徑零影響，見 7d 節 D2）：判定＝純函式 `row_visibility`，
    #   結果**只附掛**在 row／area 上，**模板不做任何判斷邏輯**。
    #   ⚠ 順序與列組成完全不動——`areas` 仍是整份名冊、仍照 `EQMT_CATALOG` 原序；
    #     被摺的列**仍在 `area["rows"]` 裡**（CSV／headline／Hermes 摘要照舊吃得到）。
    for _r in rows:
        _r["vis"], _r["vis_reason"] = row_visibility(_r, mes_available)
        # WP-C2：速率尺別＝**純顯示附掛**（同上一行的分層：判定在別處、模板不做邏輯）。
        _r["rate_kind"] = (rate_kind_by_eid or {}).get(_r["eid"])

    areas = [{"key": key, "label": label,
              "rows": [r for r in rows if r["area"] == key]}
             for key, label in AREAS]
    for _a in areas:
        _a["collapsed"] = collapse_summary(_a["rows"], mes_available)

    # ★★ `#125` (b)(c)（C-RW26）：**座列浮出**＝母機列正下方插一列（`SPEC-RUNWINDOW.md` §6.3-c）。
    #   ⚠⚠ **插入時機就是「不進任何 Σ」的實作**：上面 `collapse_summary` 已經算完、下面的
    #     legend／conflicts／`_headline` 吃的都是 `rows`（機台列的平表，永遠不含座列）
    #     ⇒ 板上每一個彙總都逐位元同加座列前。**別把這段往上搬**。
    #   · 對象＝形①②（四站）；**形③（Gen2）一個位元不動**（判斷在 `seat_rows_for_board`）。
    #   · `vis` 恆 shown、`vis_reason` None（存在即顯示）；`rate_kind` 附掛**母機那一格**
    #     （座列的速率就是母機的速率 × 座係數＝C-RW25 的算術形，尺別當然同一把）。
    #   · dup EID 的兩列各自拿一份**淺拷貝**（同一個 dict 掛兩處＝下游一改兩處都動）。
    #   · `#128`-A（§6.9 第 5 條）：座列的 Now 第二行吃 `_sub_ages`（**與母機列的子工位格
    #     同一份材料**，`build_board` 早就拿在手上）⇒ 零新來源、零新查詢。
    _seat_rows = seat_rows_for_board(catalog, wos_by_eid, day_wins, win_end, mode,
                                     dormant_events=dormant_events,
                                     rates_by_eid=rates_by_eid, erw_ctx=erw_ctx,
                                     sub_unit_ages=_sub_ages)
    if _seat_rows:
        for _a in areas:
            _merged = []
            for _r in _a["rows"]:
                _merged.append(_r)
                for _sr in _seat_rows.get(_r["eid"]) or ():
                    _sr = dict(_sr)
                    _sr["vis"], _sr["vis_reason"] = _VIS_SHOWN, None
                    _sr["rate_kind"] = (rate_kind_by_eid or {}).get(_r["eid"])
                    _merged.append(_sr)
            _a["rows"] = _merged

    # legend 動態化（一眼看色帶原則，2026-07-07；2026-07-10 Chris 反饋 #1）：維修/計畫保養常駐；
    # 等料/等外包窗內有值才顯（各獨立色）；uptime 兩子類（等機台空檔斜紋/試跑圓點）有段才各自顯。
    # ★ 調機(CONVERSION)/其他維護(長尾)＝eMaint legacy work order type、新 cmms 已無此二 type →
    #   **永不進 legend**（連動態補顯也撤）；歷史遷移單相交時該態仍以 legacy 灰渲染 + tooltip 具名、
    #   不藏資料（STC/SNAME 保留 --st-setup/--st-maint→--st-legacy），只是不佔圖例版面。
    # BUCKET_STATE→'其他維護' 正規化（legend key；仍計 present 供未來若恢復，但 legacy 不列入 legend）。
    present = set()
    legend_uptime = []
    for r in rows:
        for c in r["cells"].values():
            for st, h in (c.get("by_state") or {}).items():
                if h:
                    present.add("其他維護" if st == w8.BUCKET_STATE else st)
        for s in r["segments"]:
            if not s.get("countable", True):
                us = s.get("state")
                if us in _UPTIME_STATES and us not in legend_uptime:
                    legend_uptime.append(us)
    legend_states = ["維修", "計畫保養"] + [
        s for s in (HOLD_PARTS_STATE, HOLD_VENDOR_STATE) if s in present]
    legend_uptime = [s for s in _UPTIME_STATES if s in legend_uptime]   # canonical 排序
    has_uptime_hold = bool(legend_uptime)

    # (c) 交叉揭露彙總（additive）：dup EID 只算一次（同 _headline 的「台」語意）。
    #     空 list ⇒ 模板整列不渲染 ⇒ **沒有矛盾的日子版面逐位元不變**（(甲) 不加常駐 UI）。
    conflicts, _seen_cf = [], set()
    for r in rows:
        cf = r.get("wo_mes_conflict")
        if cf and r["eid"] not in _seen_cf:
            _seen_cf.add(r["eid"])
            conflicts.append({"eid": r["eid"], "wo_no": cf.get("wo_no")})

    # §10 一致性揭露彙總（additive、與上面 (c) **分開兩列**）：登記長停 ∧ MES 窗內有生產。
    #     同樣「沒有矛盾就整列不渲染」＝板面逐位元同加此欄前；**不改任何數字、不解除登記**。
    dormant_conflicts, _seen_dcf = [], set()
    for r in rows:
        dcf = r.get("dormant_mes_conflict")
        if dcf and r["eid"] not in _seen_dcf:
            _seen_dcf.add(r["eid"])
            dormant_conflicts.append({"eid": r["eid"],
                                      "since": (r.get("dormant") or {}).get("since")})

    # ★ 長停排除揭露彙總（2026-08-14；additive、與上面兩列**再分開一列**）：
    #   「工單的停機段落在長停工位的長停日 ⇒ 已排除於停機統計」。
    #   · **單向、只揭露、不改任何數字**（數字已經在 build_row 排除掉了，這行只是說出來）。
    #   · 與 (c)／§10 **刻意不合併**：那兩條講「矛盾」（工單 vs MES、登記 vs MES），
    #     這條講「本頁刻意沒算」——來源不同、要找的人不同（去 cmms 登記復機的是同一群人，
    #     但這一行的動作是「確認這台真的還在長停」而不是「去解除登記」）。
    #   · 空 list ⇒ 模板整列不渲染 ⇒ **沒有東西被扣的日子版面逐位元不變**。
    dormant_drops, _seen_dd = [], set()
    for r in rows:
        dd = r.get("dormant_dropped")
        if dd and r["eid"] not in _seen_dd:
            _seen_dd.add(r["eid"])
            dormant_drops.append({"eid": r["eid"], "h": (dd.get("sec") or 0.0) / 3600.0,
                                  "wo_nos": dd.get("wo_nos") or []})

    # ★ 待辦 4 揭露（2026-08-16；SPEC-LASTPROD-PUSH §11）：「本窗有 N 張單走對齊起點」。
    #   fsa 生效（`w8.wo_failure_start` 五條規則；被方向鐵閘擋下的**不計入 N**）∧ 活躍窗
    #   與板窗相交 ⇒ 計一張。**非零才渲染**＝今天（prod fsa 全 null）版面逐位元不變。
    #   誠實標示「哪些數字含有現場的修正」——不是對資料存疑（CONCEPT §19.3 第十輪 relay 四）。
    #   ★ NOTE-60（2026-08-22）：pra（對齊終點）走同一迴圈收 `pra_aligned` 鏡像鍵——
    #   同為量測正確性用途（UI 行已於 2026-08-21 裁決退役、兩鍵皆無模板消費者）。
    #   ★ 第二十五輪 §一-3（2026-08-21）：**被方向鐵閘擋下的 pra 要計數揭露、不得靜默**
    #   ——cmms domain 結構上不可能寫出「pra <= 其參考點」⇒ 擋到東西＝**兩邊參考點不一致**
    #   的裁決訊號（非零就代表該改的是門檻本身），不是髒資料。⚠ 只收 `not_after_reference`
    #   （pra 本來就 null 不算被擋；髒值另有 §21.1 路徑）——混進去會把訊號淹掉。
    _fsa_nos, _pra_nos, _pra_blocked = set(), set(), set()
    _win_lo = day_wins[0][1] if day_wins else None
    for lst in wos_by_eid.values():
        for w in lst:
            if not isinstance(w, dict) or w8.wo_is_cancelled(w):
                continue
            has_fsa = w8.wo_failure_start(w, win_end) is not None
            has_pra = w8.wo_production_resumed(w, win_end) is not None
            _blocked = w8.pra_block_reason(w, win_end) == "not_after_reference"
            if not (has_fsa or has_pra or _blocked):
                continue
            win = w8.wo_active_window(w, now=win_end)
            if win is None:
                continue
            a, b = win[0], (win_end if win[1] is None else win[1])
            if _win_lo is not None and b > _win_lo and a < win_end:
                if has_fsa:
                    _fsa_nos.add(w.get("work_order_no"))
                if has_pra:
                    _pra_nos.add(w.get("work_order_no"))
                if _blocked:
                    _pra_blocked.add(w.get("work_order_no"))

    all_wos = [w for lst in wos_by_eid.values() for w in lst]
    return {
        "areas": areas,
        # ★ WP-D：全板被摺列數（**0＝版面逐位元同加此功能前**）。給模板決定要不要長出
        #   「只看有紀錄」的回鏈，也給 smoke 一個不必數 HTML 的釘子。
        "collapsed_n": sum((a["collapsed"] or {}).get("n", 0) for a in areas),
        "conflicts": conflicts,           # (c) MES 交叉揭露；[]＝無矛盾（模板整列不渲染）
        # 待辦 4：走 fsa 對齊起點的單號（升冪）；[]＝本窗零張（模板整列不渲染）
        "fsa_aligned": sorted(n for n in _fsa_nos if n is not None),
        # NOTE-60：走 pra 對齊終點的單號（升冪）；同 fsa_aligned＝資料鍵、無 UI 行
        "pra_aligned": sorted(n for n in _pra_nos if n is not None),
        # ★ 第二十五輪 §一-3：pra 被方向鐵閘擋下的單號（升冪）。**非零＝門檻與 cmms 參考點
        #   不一致的裁決訊號**，不是髒資料告警。常態應恆為 []（修正後 cmms 寫得進來的值
        #   都會通過）；哪天非零，要回報 cmms 而不是自己吞掉。
        "pra_blocked": sorted(n for n in _pra_blocked if n is not None),
        # 長停排除揭露；[]＝這一輪什麼都沒被扣（同上，整列不渲染）
        "dormant_drops": dormant_drops,
        "dormant_conflicts": dormant_conflicts,   # §10 一致性揭露；[]＝無矛盾（同上）
        # A11 批次現況讀的 unknown[]：兩邊名冊漂了 ⇒ 頁上紅字（cmms NOTE-34【三】）。
        "sched_unknown": [str(e) for e in (sched_unknown or []) if e],
        "headline": _headline(rows, day_wins),
        "wo_status": wo_status,
        "truncated": False,
        "fallback_rate": w8.migration_fallback_rate(all_wos),   # 遷移回退率可見（NOTE-6 ①）
        "mode": mode,
        "win_end": win_end,
        "n_days": n_days,
        "day_labels": [ds for ds, _, _ in day_wins],
        "legend_states": legend_states,
        "legend_uptime": legend_uptime,
        "has_uptime_hold": has_uptime_hold,
        "mes_available": mes_available,   # 多源 Now 是否有 MES 證據（False→Now 退工單二值＋caveat）
        "quiet_h": quiet_h,               # ⚠ 久靜門檻（頁面文案插值用）
        "mes_lookback_d": mes_lookback_d,  # nosig 主張的窗寬（None＝證據源不支撐 ⇒ nosig 小字不渲染）
        # ★ #39 該跑窗的**開關＋揭露材料**（`SPEC-RUNWINDOW.md` v1）。
        #   `None`＝框 inert（§5.3）⇒ A% 欄＝舊 24h 軸＋頁面顯著標示（模板吃這個判斷）。
        #   `n_days`/`n_days_total`＝口徑行的「基於日曆涵蓋的 N/M 天」（board 級）。
        #   `degraded`＝逐列 degraded 旗的聯集（slots_uncovered／manpower_uncovered／
        #   no_fine_bucket ⇒ 口徑行標「部分日未含窗外擴窗」）。
        "erw": ({"n_days": len(erw_ctx["days"]), "n_days_total": len(day_wins),
                 "n_closures": len(closures or ()),
                 "n_uncertain": sum(1 for c in (closures or ()) if c.get("uncertain_eids")),
                 "degraded": sorted({f for r in rows
                                     for f in ((r.get("erw") or {}).get("degraded") or ())}),
                 # ★ §6.4（2026-08-23 晚 Chris）：換軸告示**有到期日**（C-RW6 修訂）。
                 #   判斷錨在**板自己的窗尾**、不是 wall clock ⇒ demo/snapshot 版面不隨
                 #   執行日漂移（QG-4.2）。到期後常駐口徑行收版位、定義仍在欄表頭與頁尾。
                 "switch": wrw.switch_notice_on(win_end)}
                if erw_ctx is not None else None),
        # drill 收工線／time bar 的 closure 材料（`/equipment/cell` 消費）；inert ⇒ None。
        "closures": ([dict(c) for c in closures] if erw_ctx is not None else None),
    }


# ---------------------------------------------------------------------------
# 9) demo_board — 全合成（不連 MES；走真管線 normalize_wo→wo_segments→bucket→build_board）
# ---------------------------------------------------------------------------

# 固定 demo 錨（確定性：同 n_days 兩呼結構等價）。午後時刻＝末欄今日 partial。
_DEMO_ANCHOR = _dt.datetime(2026, 7, 6, 14, 30, 0)

# demo 工單 work_type 混合（2026-07-10 Chris：只留 PM/REACTIVE＝新 cmms 唯二建單型；demo 不再演
# 一個 cmms 產不出來的世界＝不合成 CONVERSION(調機)/PART REQUEST(其他維護暫桶) legacy 型）。
# ★ legacy 渲染路徑(STC/SNAME/legend 排除)＝live 防呆網保留、不動；只是 demo 不再觸發它。
# ★ w8 契約單元測試（CONVERSION→調機、長尾→暫桶對映）＝契約測試，不受此影響、保留。
_DEMO_WORK_TYPES = ["PM", "REACTIVE"]

# drill 用故障簡述小詞庫（canned；per-工單號 seed 取；純 demo 顯示、真值走 cmms detail）。
_BRIEF_DESCRIPTIONS = [
    "真空度不足取料失敗",
    "模組對位偏移校正",
    "溫控異常待工程師確認",
    "氣壓管路洩漏更換",
    "夾治具磨耗更換",
    "感測器訊號漂移重新校準",
    "例行保養與清潔",
    "控制軟體異常當機重啟",
]

_TZ8 = _dt.timedelta(hours=8)


def demo_brief(wno):
    """demo 故障簡述（per-工單號確定性）。真值上線後走 cmms detail 的 brief_description。"""
    return _BRIEF_DESCRIPTIONS[int(wno) % len(_BRIEF_DESCRIPTIONS)]


# drill 工作日誌（工程師處置紀錄）小詞庫（canned；per-工單號 seed；純 demo 顯示、真值走 cmms diagnosis）。
# 2026-07-07 T1：故障簡述＋工作日誌落到 segment 列上（設備會議一眼讀故事），demo 需可見兩行。
_DIAGNOSES = [
    "更換真空吸盤密封圈後恢復正常取放",
    "重新校正 X/Y 對位參數、複測 OK",
    "調整 PID 溫控參數、觀察 30 分鐘穩定",
    "鎖緊氣壓接頭並更換洩漏管路段",
    "更換磨耗夾治具、確認夾持力達標",
    "感測器重新歸零校準、訊號回穩",
    "完成例行清潔潤滑、更換濾網",
    "回復標準製程參數、試跑三片確認",
]


def demo_diag(wno):
    """demo 工作日誌（per-工單號確定性；索引與 demo_brief 錯開＝非固定配對）。
       真值上線後走 cmms detail 的 diagnosis。"""
    return _DIAGNOSES[(int(wno) // 3) % len(_DIAGNOSES)]


# drill 事件流（cmms detail v1 notes[]）小詞庫（canned；per-工單號 seed；純 demo）。
# 2026-07-07 additive：新工單案例＝逐筆 notes 事件流（report/diagnosis/progress）；真值走 cmms notes。
_NOTE_SEQ = (
    ("report",    "操作員回報：{brief}，觸發連續報警"),
    ("diagnosis", "初判：{brief}，研判需更換耗材、安排備品"),
    ("progress",  "已完成更換與復歸、試機三片確認 OK"),
)
# demo 事件流時間錨（台北牆鐘 naive＝normalize_notes 輸出形；per-工單號位移確定性）。
_DEMO_NOTE_ANCHOR = _dt.datetime(2026, 7, 5, 9, 0, 0)

# 結單處置摘要小詞庫（cmms detail action_taken；歷史/已結單案例才有值、日後多 null）。
_ACTIONS = (
    "更換真空泵,真空度回復正常。",
    "校正對位參數並複測合格,恢復量產。",
    "更換磨耗夾治具,夾持力達標。",
    "回復標準製程參數,試跑確認穩定。",
)


def demo_has_notes(wno):
    """demo 案例二分（確定性）：偶數工單號＝『新工單』有 notes 事件流；
       奇數＝『歷史工單』無 notes、只有靜態 diagnosis（eMaint 最終診斷）+ 結單處置。"""
    return int(wno) % 2 == 0


def demo_notes(wno):
    """demo notes 事件流（normalize_notes 輸出形＝[{at(台北 naive), entry_type, body}]、升冪）。
       偶數工單號→3 筆（新工單案例）；奇數→[]（歷史案例只有靜態 diagnosis）。真值走 cmms notes[]。"""
    if not demo_has_notes(wno):
        return []
    brief = demo_brief(wno)
    base = _DEMO_NOTE_ANCHOR + _dt.timedelta(minutes=int(wno) % 30)
    return [{"at": base + _dt.timedelta(hours=2 * i), "entry_type": et,
             "body": tmpl.format(brief=brief)}
            for i, (et, tmpl) in enumerate(_NOTE_SEQ)]


def demo_action(wno):
    """demo 結單處置（cmms action_taken）；歷史/已結單案例才給值（奇數工單號）、新工單→None。
       演示『結單處置』行；真值走 cmms detail action_taken（Chris 已從結單 UI 移除→日後多 null）。"""
    if demo_has_notes(wno):
        return None
    return _ACTIONS[(int(wno) // 2) % len(_ACTIONS)]


def _to_utc_z(dt):
    """台北牆鐘 datetime → cmms 契約形 UTC ISO-8601 'Z' 字串（刻意 UTC-Z＝行使 §14.2 轉換）。"""
    return (dt - _TZ8).strftime("%Y-%m-%dT%H:%M:%SZ")


def _demo_h(frm, to, hr, at):
    """demo status_history 條目（cmms 真形狀；changed_at＝UTC-Z、source_actor 治理丟棄用）。"""
    return {"from_status": frm, "to_status": to, "hold_reason": hr,
            "changed_at": _to_utc_z(at), "source_actor": "human:demo"}


def _demo_wo(wno, eid, s, e, rnd, ongoing=False, wt=None):
    """合成一筆 **契約形** raw WO（欄位照 cmms LIST contract_wo_active_in.v1、時戳 UTC-Z）。
       wt＝work_type 覆寫（None→由 rnd 取自 _DEMO_WORK_TYPES；供 open 單指定 canonical 態）。
       ongoing＝仍開啟（still-open / 遷移單、closed_at=None、空 history）→ 活躍至今。
       其餘＝隨機 inline history（OPEN→IN_PROGRESS→COMPLETED，S12：OPEN 前段依 work_type 分算否）
       或遷移形（空 history、opened→closed＝rule 5 整窗算）。"""
    wt = wt if wt is not None else _DEMO_WORK_TYPES[rnd.randrange(len(_DEMO_WORK_TYPES))]
    if ongoing:
        return {"work_order_no": wno, "asset_id": eid, "work_type": wt,
                "opened_at": _to_utc_z(s), "closed_at": None, "status": "OPEN",
                "hold_reason": None, "status_history": []}
    if rnd.random() < 0.5:
        mid = s + (e - s) / 2
        hist = [_demo_h(None, "OPEN", None, s),
                _demo_h("OPEN", "IN_PROGRESS", None, mid),
                _demo_h("IN_PROGRESS", "COMPLETED", None, e)]
        return {"work_order_no": wno, "asset_id": eid, "work_type": wt,
                "opened_at": _to_utc_z(s), "closed_at": _to_utc_z(e),
                "status": "COMPLETED", "hold_reason": None, "status_history": hist}
    return {"work_order_no": wno, "asset_id": eid, "work_type": wt,
            "opened_at": _to_utc_z(s), "closed_at": _to_utc_z(e),
            "status": "CLOSED", "hold_reason": None, "status_history": []}


# T3 子軌道 showcase 的目標機（EQMT_CATALOG 首列＝表格最上方、demo 最易點到；穩定 EID）。
EQM_SHOWCASE_EID = "EID-54769"


def _demo_migration_wo(wno, eid, s, e, wt):
    """合成一筆 **遷移形** 契約 WO（空 status_history、opened→closed、UTC-Z）＝rule 5『整窗照算』，
       整個 [s,e] 皆為 work_type 對應計停機態（維修/計畫保養…）＝子軌道 showcase 用確定性重疊段。"""
    return {"work_order_no": wno, "asset_id": eid, "work_type": wt,
            "opened_at": _to_utc_z(s), "closed_at": _to_utc_z(e),
            "status": "CLOSED", "hold_reason": None, "status_history": []}


def _demo_phase_wo(wno, eid, opened, closed, wt, steps):
    """合成一筆 **已結案·分階段** 契約 WO（inline status_history、UTC-Z）＝demo 精準演示 S12 拆色。
       steps＝[(to_status, hold_reason, at)…]（升冪、末條終態）→ 走真管線 normalize_wo→wo_segments。"""
    hist, prev = [], None
    for to, hr, at in steps:
        hist.append(_demo_h(prev, to, hr, at))
        prev = to
    return {"work_order_no": wno, "asset_id": eid, "work_type": wt,
            "opened_at": _to_utc_z(opened), "closed_at": _to_utc_z(closed),
            "status": steps[-1][0], "hold_reason": steps[-1][1], "status_history": hist}


# 2026-07-10 first-level uptime / hold-色 showcase 機（EQMT_CATALOG 第二列＝表格上方易點）：demo 於
# demo_board 尾覆寫此機 WO 列為手工植入的確定性案例，讓 Chris 一眼驗收 Chris 反饋落地的新視覺——
#   07-02 等外包 dominant（琥珀實心）｜07-03 維修+等料 混合（紅格 + 格底紫段）｜
#   07-04 維修+試跑（紅格 + 格底圓點 uptime 段）｜07-05 純 uptime（透明格 + 斜紋組成條）。
# 錨窗 days≥5 內四日皆現形（win_start=首日 00:00）。只覆寫 demo 合成、口徑管線一行不改。
EQM_UPT_SHOWCASE_EID = "EID-54428"

# (c) MES 交叉揭露 showcase 機（2026-08-11）：demo 為這台**覆寫 MES age**，讓「工單說停機 ∧
# MES 同時段有生產」確定性成形、揭露列在 demo 頁看得到。挑它的理由＝它在 demo 錨下掛著一張
# **仍 open 的 REACTIVE 單**（countable 段延伸到 win_end、起點約 11h 前）⇒
#   ① age 落在段內且遠超護欄；② **它的 Now 欄是 DOWN（工單優先）** ⇒ 改 age 不改 Now 欄任何
#      顯示（DOWN 分支回傳 mes_last_h=None）⇒ 既有 demo 釘子逐位不動。
# ⚠ demo 的 WO 合成是 per-EID 確定性亂數：哪天那張單不見了，這台就不再有矛盾。selftest 有
#   釘子直接驗「demo 板恰有這一台在 conflicts 裡」⇒ 走鐘時是**紅燈**、不是靜默失去 demo 案例。
EQM_CONFLICT_SHOWCASE_EID = "EID-55109"
EQM_CONFLICT_SHOWCASE_AGE_H = 1.0

# §10 一致性揭露 showcase 機（2026-08-14）：demo 為這台**覆寫 MES age**，讓「登記長停 ∧ MES
# 窗內有生產」確定性成形。挑 M2 的理由＝它是橋常數兩筆之一（生效日 2023-05-23 ⇒ demo 錨
# 2026-07-06 下的 t_last 必然晚於生效日、必成矛盾），且**它的 Now 欄是 dormant** ⇒
#   dormant 分支只印 chip＋生效日、**不印 age 小字** ⇒ 改 age 不改該列任何一個字。
# ⚠ demo 對長停工位刻意「不合成工單、不給 MES age」（見 demo_board 內註解）——這裡是**唯一**
#   的例外、且只覆寫這一台，其餘機台逐位不變。
EQM_DORMANT_CONFLICT_SHOWCASE_EID = "EID-54681"
EQM_DORMANT_CONFLICT_SHOWCASE_AGE_H = 6.0

# ★ WP-D 摺疊 showcase（2026-09-02）：demo **只覆寫一台的 MES age**（零工單改動）。
#   demo 的隨機分佈剛好把 45 個「無工單」列全部撒進「MES 近期有生產」那一桶 ⇒ 沒有動手腳的話
#   **最重要的「沉默機不摺」在 demo 上連一個端到端佐證都沒有**。
#   · SILENT＝`mes` 值改成 **None**（普查看過它、整窗卻無生產）⇒ 該列走「沉默機」⇒ **留在表上**。
#     挑 `EID-38316`：①它**在** 730 天普查集合裡（`mes_measurable` True，否則會落 `no_mes`）；
#     ②demo 錨下它無任何工單、Now 是 up_producing ⇒ 覆寫只改它自己那一列。
#   · NOMES＝**不需要合成**（2026-09-02 審查 HIGH-1 之後）：`EID-01211`（Taping Jig#1）本來就
#     **不在**普查集合裡 ⇒ 自然落 `no_mes`。名冊裡有**數十台**是這一類（治具／烤箱／
#     顯微鏡／Profiler…），**demo 與 live 走的是同一條規則、同一份集合**。
#     ⚠ 原本這裡手植過「移除 mes key」的覆寫，已拿掉——那是在用 `mes_by_eid` 的 key 當覆蓋判準，
#       而 live 的 key 是對名冊全鋪的（那正是 HIGH-1 的病灶）。
#   ⚠ SILENT 靠 demo 的確定性亂數保持「無工單」；哪天走鐘 ⇒ selftest S5e 直接紅燈
#     （不是靜默失去 demo 案例），同 `EQM_CONFLICT_SHOWCASE_EID` 的紀律。
EQM_SILENT_SHOWCASE_EID = "EID-54508"
EQM_NOMES_SHOWCASE_EID = "EID-54585"      # 示範用的「量不到」機台（**不覆寫任何東西**，普查本來就沒它）

# ★ `#125` (b)(c) 座列 showcase（2026-09-12；`SPEC-RUNWINDOW.md` §6.3-c、C-RW26）。
#   **Flex Bonding #5**＝Chris 09-12 實測那台（cmms WO 32136 把範圍改成「座 2」、Board 下鑽看不到座
#   ＝開 `#125` 的那一張單）。demo 合成**恰兩張**單、**恰兩列**座列：
#     · `_SEAT_SHOWCASE_WO` REACTIVE：08:10 開單整機停 → **10:00 範圍改到 `nest:2`** → 15:00 結單
#       ⇒ 母機列 6h50m（一個位元不變）、**座 2 那列只有 10:00 之後的 5h**＝
#       「改之前的段留在母機列」那句裁決的可視化。
#     · `_SEAT_SHOWCASE_UPT_WO` REACTIVE·等機台空檔：整張單範圍就在 `nest:3`、零可計停機
#       ⇒ **不計的單也讓座浮出**（斜紋、`stop_h` 0、無當量）。
#   ⚠ 錨日＝**窗末前兩天**（同 A10 showcase 的理由）：放在「昨天」會讓 `_headline` 的
#     `yd_stop_h`／`top_machine` 跟著漂，那是與本功能無關的既有 demo 基準。
#   ⚠ 選這一台**不是**因為它最方便——它在 demo 原本零工單，加單會讓它從「摺」變「顯」
#     （`n_with_downtime` 54→55）。那是刻意的：座列的 demo 必須長在 Chris 指名的那台機上，
#     否則驗收時對不上他看過的畫面。
EQM_SEAT_SHOWCASE_EID = "EID-55593"
EQM_SEAT_SHOWCASE_UNIT = "nest:2"
EQM_SEAT_SHOWCASE_UPT_UNIT = "nest:3"


# demo 仍 open 工單的階段 pattern（cycle＝確定性保證各類皆現，演示 S12 口徑 + canonical 5 值
# hold_reason 拆色）。pm_open/reactive_ip＝現在徽章示範；reactive_hold/reactive_test＝兩 uptime
# 條紋子類（等機台空檔/試跑）；reactive_parts/vendor/other＝計停機 hold（等料/等外包獨立色 + 其他留母態）。
_DEMO_OPEN_PATTERNS = ("pm_open", "reactive_ip", "reactive_hold", "reactive_test",
                       "reactive_parts", "reactive_vendor", "reactive_other")

# ON_HOLD 計停機 pattern → 其 hold_reason（canonical 計停機 3 值；對照 UPTIME_HOLD_REASONS 的
# WAITING_MACHINE_TIME/TEST_RUN 不計）。drill 工單卡的『暫停原因』chip 演示這些友善標籤。
_DEMO_HOLD_DOWN = {"reactive_parts": "WAITING_PARTS",
                   "reactive_vendor": "WAITING_VENDOR",
                   "reactive_other": "OTHER"}


def _demo_open_wo(wno, eid, s, win_end, pattern):
    """合成一筆 **仍 open** 契約形工單（inline history、UTC-Z），依 pattern 演示 S12 現在徽章新規：
       'pm_open'         = PM 只有 OPEN（未開始處理）→ 現在 **UP**、不計停機（截圖 Quarterly PM 案例）。
       'reactive_ip'     = REACTIVE OPEN→IN_PROGRESS（處理中）→ 現在 **DOWN·維修**、全程算停機。
       'reactive_hold'   = REACTIVE OPEN→IN_PROGRESS→ON_HOLD(WAITING_MACHINE_TIME)→ 現在 **UP**
                           （暫停·等機台空檔·機台可生產）；先前 IN_PROGRESS 算停機、hold 段 uptime 扣除。
       'reactive_test'   = REACTIVE OPEN→IN_PROGRESS→ON_HOLD(TEST_RUN)→ 現在 **UP**
                           （暫停·試跑·機台可生產）；演示第二條 uptime 條紋色（暖灰 stone）。
       'reactive_parts'  = REACTIVE ...→ON_HOLD(WAITING_PARTS 等料)   → 現在 **DOWN·維修**（等料獨立色帶）。
       'reactive_vendor' = REACTIVE ...→ON_HOLD(WAITING_VENDOR 等外包)→ 現在 **DOWN·維修**（計停機）。
       'reactive_other'  = REACTIVE ...→ON_HOLD(OTHER 其他暫停)       → 現在 **DOWN·維修**（計停機）。
       後三者演示 drill 工單卡『暫停原因』chip（等料/等外包/其他暫停＝友善標籤、皆計停機）。"""
    span = win_end - s
    if pattern == "pm_open":
        wt, status, hr = "PM", "OPEN", None
        hist = [_demo_h(None, "OPEN", None, s)]
    elif pattern in ("reactive_hold", "reactive_test"):     # 兩 uptime 條紋子類
        hr = "TEST_RUN" if pattern == "reactive_test" else "WAITING_MACHINE_TIME"
        wt, status = "REACTIVE", "ON_HOLD"
        hist = [_demo_h(None, "OPEN", None, s),
                _demo_h("OPEN", "IN_PROGRESS", None, s + span * 0.3),
                _demo_h("IN_PROGRESS", "ON_HOLD", hr, s + span * 0.6)]
    elif pattern in _DEMO_HOLD_DOWN:               # 計停機 hold（等料/等外包/其他）
        hr = _DEMO_HOLD_DOWN[pattern]
        wt, status = "REACTIVE", "ON_HOLD"
        # hold 起點提前（2026-07-10）：原本一律 0.6 → hold 段太短、day_cell 的 dominant 永遠是母態
        # 「維修」，等料/等外包的獨立色（紫實心／紫圓點）在 demo heatmap 上永遠不現形、無從目視驗收。
        # 等料 0.30 / 等外包 0.15＝讓兩者在後段日子各自成為 dominant。**只影響 demo 合成、不動口徑**
        # （wo_segments/day_cell/A% 全走原路徑；真實 live 資料的 hold 起點來自 cmms status_history）。
        _at = {"WAITING_PARTS": 0.30, "WAITING_VENDOR": 0.15}.get(hr, 0.6)
        hist = [_demo_h(None, "OPEN", None, s),
                _demo_h("OPEN", "IN_PROGRESS", None, s + span * min(0.3, _at * 0.5)),
                _demo_h("IN_PROGRESS", "ON_HOLD", hr, s + span * _at)]
    else:  # reactive_ip
        wt, status, hr = "REACTIVE", "IN_PROGRESS", None
        hist = [_demo_h(None, "OPEN", None, s),
                _demo_h("OPEN", "IN_PROGRESS", None, s + span * 0.4)]
    return {"work_order_no": wno, "asset_id": eid, "work_type": wt,
            "opened_at": _to_utc_z(s), "closed_at": None, "status": status,
            "hold_reason": hr, "status_history": hist}


# demo 實跑窗劇本（#36）：**確定性合成、絕不讀 `_PROD-CALENDAR-LATEST.json`**——demo lane
#   的鐵則是「全合成」，讀本機檔會讓 demo 隨 Chris 手上有沒有跑過日曆而改變。
#   劇本要能一眼驗收三形：①名冊機台有並列值 ②非名冊機台「—」 ③一段 uncertain（C-PC12）。
EQM_DEMO_UNCERTAIN_KUN = "ZA-2"     # 這一綑當日「有工單活躍」⇒ closure 對它不確定、不縮分母


def demo_closures(win_start, win_end):
    """demo 用確定性合成 closures（`[{start,end,uncertain_eids}]`，已裁到窗內）。

       兩段、錨在窗末日期：
         · **昨日 00:00–06:00**＝乾淨的全廠停線（`uncertain_eids` 空）⇒ 名冊機台的並列 A%
           分母少 6h、非名冊機台完全不受影響（第一眼就看得出兩者的差別）。
         · **前日 18:00–23:00**＝對 `EQM_DEMO_UNCERTAIN_KUN` 整綑不確定（C-PC12 兜底的樣子：
           那五小時到底是全廠休息、還是整綑同時在修，分不出來 ⇒ 對該綑**不扣分母**）。
       n_days 小到裝不下 ⇒ 自然被裁光＝並列欄仍在（只是沒有 closure），行為仍確定。"""
    try:
        import _probe_kun_activity as _ka
        unc = next((es for k, _a, _n, es in _ka.KUN_ROSTER
                    if k == EQM_DEMO_UNCERTAIN_KUN), _ka.KUN_ROSTER[0][3])
    except Exception:
        unc = ()
    d0 = _coerce_dt(win_end).date()

    def _at(days_ago, h):
        return (_dt.datetime(d0.year, d0.month, d0.day)
                - _dt.timedelta(days=days_ago) + _dt.timedelta(hours=h))

    raw = [{"start": _at(1, 0), "end": _at(1, 6), "uncertain_eids": ()},
           {"start": _at(2, 18), "end": _at(2, 23), "uncertain_eids": tuple(unc)}]
    return closures_in_window(raw, _coerce_dt(win_start), _coerce_dt(win_end))


def demo_erw_inputs(win_start, win_end):
    """demo 用確定性合成該跑窗 raw（slot 位圖＋人力細桶；**絕不讀本機 raw 檔**＝QG-4.2）。

       劇本讓三層階梯各現一形（目視驗收 C-RW14）：
         · ZB-2（Wirebond）：25983 每完整日在 ZAB 空隙 01:00–02:00 有 prod slot
           ⇒ 25590／25098 走 **tier 1**（同機種 envelope 擴 60 分）。
         · ZC-4（AutoOQC）：37932 每完整日在 ZC 晚段 17:00–19:30 有 prod slot
           ⇒ 36255 等走 **tier 1**；ZC-4 其他日晚段 mp 桶 0。
         · ZC-3（CAL）：slot 全靜、mp 晚段細桶＝1 ⇒ **tier 3** 兜底整段（16:40–20:00）。
       其餘綑：無窗外訊號 ⇒ 該跑窗＝班次窗（④ 形）。回 `(slots_raw, mp_raw)`。"""
    slots_days, mp_days = {}, {}
    d = _coerce_dt(win_start)
    we = _coerce_dt(win_end)
    while d < we:
        if d + _dt.timedelta(days=1) > we:
            break                                            # 末 partial 生產日不合成（真實形）
        iso = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
        slots_days[iso] = {
            "EID-25983": {"prod": _slots_hex(range(102, 108))},
            "EID-37932": {"prod": _slots_hex(range(54, 69))},
        }
        mp_days[iso] = {
            "ZC-3": {"day": 3, "night": 2, "outside": 1, "outside_ZC_1": 1, "outside_ZC_2": 0},
            "ZC-4": {"day": 3, "night": 2, "outside": 0, "outside_ZC_1": 0, "outside_ZC_2": 0},
            "ZB-2": {"day": 2, "swing": 2, "outside": 0, "outside_ZAB_1": 0},
        }
        d += _dt.timedelta(days=1)
    return ({"by_prod_day_slots": slots_days}, {"by_prod_day_shift": mp_days})


def _slots_hex(slots):
    n = 0
    for i in slots:
        n |= 1 << i
    return format(n, "036x")


def demo_rates_by_eid():
    """demo 產出當量的確定性速率（顆/機台小時）：只給名冊機台、值域 6–14（per-EID 確定性）。
       **全合成**——live 的速率來自昨日日帳的 `rate_per_mh`（app 層接）。
       ★ 2026-09-12：尾巴走**同一支** `inherit_child_rates`（形②③ 子槽位沒速率就繼承母機）
         ——demo 的綑名冊本來就含那 12 個子槽位 ⇒ 這裡實務上是 no-op，但**兩條 lane 同形**
         才驗得到 live 那條腿（live 的 `fleet_map` 只認母機＝Chris 追問的那個病）。"""
    out = {}
    for e in sorted(kun_roster_eids()):
        out[e] = 6.0 + (_seed(e) % 9)
    return inherit_child_rates(out)


def demo_board(n_days=5, quiet_h=QUIET_H):
    """全合成會議板（頁面須掛「全模擬」大牌）。per-EID 確定性 seed；合成 0–3 筆契約形工單（UTC-Z）
       ＋部分機台掛 open 單（→現在 DOWN 徽章）→ **走真管線** normalize_wo→wo_segments→bucket→
       build_board（證明管線；EID 一律取自 EQMT_CATALOG、此外不捏任何實體）。mode="demo"。"""
    win_end = _DEMO_ANCHOR
    day_wins = day_windows(win_end, n_days)
    win_start = day_wins[0][1]
    span = (win_end - win_start).total_seconds()
    wos_by_eid = {}
    open_i = 0                                                # open-WO pattern cycle 計數（確定性）
    # 長停投影（A11 過渡橋）：demo 錨是 2026-07-06、橋的生效日是 2025-10-24/28 ⇒ demo 恆現形。
    # ★ **demo 不替長停 EID 合成任何工單**——不是口徑，是可視性：demo 的 WO 合成是 per-EID
    #   確定性亂數，若剛好給長停工位撒了一張 open 單，Now 欄就會走 DOWN 勝（正確但把第三態遮掉），
    #   第三態在 demo 上時有時無＝無從目視驗收。真實 live 不做此排除（長停機台照樣可能有工單）。
    dormant_by_eid = {e: wdorm.dormant_info(e, win_end.date())
                      for _a, _n, e in EQMT_CATALOG if wdorm.is_dormant(e, win_end.date())}

    for area_key, name, eid in EQMT_CATALOG:
        rnd = random.Random(_seed(eid) * 131 + 7)
        base = 40000 + _seed(eid) * 3
        raw = []
        for i in range(rnd.randint(0, 3)):                    # 0–3 筆窗內已完工/遷移工單
            s0 = win_start + _dt.timedelta(seconds=rnd.uniform(0, span * 0.85))
            e0 = min(s0 + _dt.timedelta(hours=rnd.uniform(0.5, 6)), win_end)
            if e0 <= s0:
                continue
            raw.append(_demo_wo(base + i, eid, s0, e0, rnd, ongoing=False))
        if _seed(eid) % 7 in (0, 1):                          # 部分機台掛仍 open 工單（S12 pattern cycle）
            os_ = win_end - _dt.timedelta(hours=rnd.uniform(3, 12))
            raw.append(_demo_open_wo(base + 90, eid, os_, win_end,
                                     _DEMO_OPEN_PATTERNS[open_i % len(_DEMO_OPEN_PATTERNS)]))
            open_i += 1
        wos_by_eid[eid] = [w8.normalize_wo(r) for r in raw]

    # T3 子軌道 showcase（2026-07-07）：一台機（SHOWCASE_EID）於錨日注入兩筆**時間重疊**已結案工單
    # ——維修 09:00–12:00 ∥ 計畫保養 10:00–11:30——證明 assign_lanes 把重疊段分軌、drill 色帶兩色皆
    # 可見（自然 demo 隨機不易撞出重疊，故確定性植入）。皆 CLOSED＝不影響『現在』徽章。落錨日 partial
    # 窗內（09–12 < 14:30）＝所有 days（含 days=1）皆現形。
    sc = wos_by_eid.get(EQM_SHOWCASE_EID)
    if sc is not None:
        d = win_end.date()
        a_s = _dt.datetime(d.year, d.month, d.day, 9, 0)
        a_e = _dt.datetime(d.year, d.month, d.day, 12, 0)
        b_s = _dt.datetime(d.year, d.month, d.day, 10, 0)
        b_e = _dt.datetime(d.year, d.month, d.day, 11, 30)
        sc.append(w8.normalize_wo(_demo_migration_wo(49500, EQM_SHOWCASE_EID, a_s, a_e, "REACTIVE")))
        sc.append(w8.normalize_wo(_demo_migration_wo(49501, EQM_SHOWCASE_EID, b_s, b_e, "PM")))

    # uptime / hold-色 showcase（2026-07-10 Chris 反饋落地驗收）：覆寫 EQM_UPT_SHOWCASE_EID 的 WO 列
    # 為四個手工分階段已結案單，各落一日 → 4 種新視覺各一格（口徑管線不改，皆走 normalize_wo→wo_segments）。
    su = wos_by_eid.get(EQM_UPT_SHOWCASE_EID)
    if su is not None:
        d0 = win_end.date()

        def _dd(days_ago, h, m=0):
            base = _dt.datetime(d0.year, d0.month, d0.day) - _dt.timedelta(days=days_ago)
            return base + _dt.timedelta(hours=h, minutes=m)

        # ★ A6 生產日界（2026-08-23）：showcase 時刻整批平移到 08:00 之後——欄界換成
        #   生產日後，清晨時段屬**前一欄**；不平移的話四個視覺會各自漂欄／被劈半。
        raw_sc = [
            # ① 07-02 等外包 dominant（琥珀實心）：維修 09:00–10:30 → 等外包 10:30–16:00（5.5h > 1.5h）
            _demo_phase_wo(49600, EQM_UPT_SHOWCASE_EID, _dd(4, 9), _dd(4, 16), "REACTIVE",
                           [("OPEN", None, _dd(4, 9)), ("IN_PROGRESS", None, _dd(4, 9, 30)),
                            ("ON_HOLD", "WAITING_VENDOR", _dd(4, 10, 30)), ("COMPLETED", None, _dd(4, 16))]),
            # ② 07-03 維修+等料 混合（紅格 + 格底紫段）：維修 09:00–14:00 → 等料 14:00–16:00
            _demo_phase_wo(49601, EQM_UPT_SHOWCASE_EID, _dd(3, 9), _dd(3, 16), "REACTIVE",
                           [("OPEN", None, _dd(3, 9)), ("IN_PROGRESS", None, _dd(3, 9)),
                            ("ON_HOLD", "WAITING_PARTS", _dd(3, 14)), ("COMPLETED", None, _dd(3, 16))]),
            # ③ 07-04 維修+試跑（紅格 + 格底圓點 uptime 段）：維修 10:00–12:00 → 試跑 12:00–14:00
            _demo_phase_wo(49602, EQM_UPT_SHOWCASE_EID, _dd(2, 10), _dd(2, 14), "REACTIVE",
                           [("OPEN", None, _dd(2, 10)), ("IN_PROGRESS", None, _dd(2, 10)),
                            ("ON_HOLD", "TEST_RUN", _dd(2, 12)), ("COMPLETED", None, _dd(2, 14))]),
            # ④ 07-05 純 uptime（透明格 + 斜紋組成條）：OPEN 0 長度 → 等機台空檔 08:00–14:00（無 countable）
            _demo_phase_wo(49603, EQM_UPT_SHOWCASE_EID, _dd(1, 8), _dd(1, 14), "REACTIVE",
                           [("OPEN", None, _dd(1, 8)), ("ON_HOLD", "WAITING_MACHINE_TIME", _dd(1, 8)),
                            ("COMPLETED", None, _dd(1, 14))]),
        ]
        wos_by_eid[EQM_UPT_SHOWCASE_EID] = [w8.normalize_wo(r) for r in raw_sc]

    # 長停工位清空工單（見上方 dormant_by_eid 註解）。★ **必須在合成迴圈之後才清**——
    # 迴圈裡的 `rnd`／`open_i` 是全域確定性序列，提前 continue 會位移後續機台拿到的
    # open-WO pattern ⇒ 其他機台的 demo 板整片改變（smoke 對 EID-05303／EID-38228 的
    # 拆色釘子就是被這條位移打紅的那型）。這裡只清值、不動序列。
    for _e in dormant_by_eid:
        wos_by_eid[_e] = []

    # ★ (c) showcase 的「仍 open 的計停機 REACTIVE 單」顯式保底（2026-10-02；公開樹殘留清理包）：
    #   原本這張單**完全來自** per-EID 確定性亂數（`_seed(eid)`＝EID 字串的函數）⇒ 公開樹的 EID 是
    #   置換後的假編號、亂數不再撒到這台 ⇒ selftest (c) 只能標 skipped。這裡改成：**亂數已給了
    #   這樣一張單就一個位元都不動**（私有樹恰是此情形 ⇒ demo 板逐位元同改動前）；沒給才補一張
    #   `reactive_ip`（DOWN·維修、全程計停機、起點約 11h 前＝常數註解描述的那個形）。
    #   放在合成迴圈與長停清空之後＝不位移 `rnd`／`open_i` 序列（同 dormant 那條教訓）。
    _cf_wos = wos_by_eid.get(EQM_CONFLICT_SHOWCASE_EID)
    if (_cf_wos is not None and EQM_CONFLICT_SHOWCASE_EID not in dormant_by_eid
            and not any(_w.get("work_type") == "REACTIVE" and _w.get("closed_at") is None
                        and (_w.get("status") == "IN_PROGRESS"
                             or (_w.get("status") == "ON_HOLD"
                                 and _w.get("hold_reason") in _DEMO_HOLD_DOWN.values()))
                        for _w in _cf_wos)):
        _cf_wos.append(w8.normalize_wo(_demo_open_wo(
            49900, EQM_CONFLICT_SHOWCASE_EID, win_end - _dt.timedelta(hours=11), win_end,
            "reactive_ip")))

    # demo MES age：長停工位不放 key＝「探測窗內無生產紀錄」（真實語意；也讓 Now 欄不掛 age 小字）。
    mes = _demo_mes_by_eid(EQMT_CATALOG)
    for _e in dormant_by_eid:
        mes.pop(_e, None)
    # (c) 交叉揭露 showcase（見 EQM_CONFLICT_SHOWCASE_EID 註解）：只覆寫這一台的 MES age，
    #     不動任何工單合成 ⇒ 其餘機台逐位不變、Now 欄也不變（該台是 DOWN、age 不顯示）。
    if EQM_CONFLICT_SHOWCASE_EID in wos_by_eid:
        mes[EQM_CONFLICT_SHOWCASE_EID] = EQM_CONFLICT_SHOWCASE_AGE_H
    # §10 一致性揭露 showcase：**唯一**一台長停工位帶 MES age（見常數註解）⇒ demo 頁看得到
    #     「登記長停、MES 窗內有生產」那一列。Now 欄的 dormant 分支不印 age ⇒ 該列一個字不變。
    if EQM_DORMANT_CONFLICT_SHOWCASE_EID in dormant_by_eid:
        mes[EQM_DORMANT_CONFLICT_SHOWCASE_EID] = EQM_DORMANT_CONFLICT_SHOWCASE_AGE_H
    # ★ WP-D 摺疊 showcase（見常數註解）：只動 MES 證據、零工單改動 ⇒ 其餘機台逐位不變。
    if EQM_SILENT_SHOWCASE_EID in wos_by_eid:
        mes[EQM_SILENT_SHOWCASE_EID] = None          # 普查看過它、整窗卻無生產 ⇒ 沉默機（不摺）

    # ★ A10 母單展開 showcase（2026-08-09）：母機**不在 EQMT_CATALOG** ⇒ 這兩張單今天在本頁
    #   完全不顯示；展開後時數落到子工位列。合成在**所有既有迴圈之後**（同 dormant 那條教訓：
    #   `rnd`／`open_i` 是全域確定性序列，動它會讓其他機台的 demo 板整片改變）。
    #   兩個案例＝A10 的兩種語意各一：
    #     (a) `EID-24742` REACTIVE ＝ **golden 劇本原樣復刻**（`_fixtures/wo_scope_events_golden.v1`）：
    #         08:50 開單整機停 → 10:30 隔離故障、只留 Bay#1 → 13:00 臨時陪停 Bay#2 →
    #         13:30 放回 → 15:40 結單。期望帳（**dormant 關閉時**）＝M1 6h50m／M2 2h10m／
    #         M3·M4 各 1h40m。**但 demo 錨下 M2/M3 在橋上長停** ⇒ 實際落地＝
    #         M1 6h50m／**M2 只剩指名的 30m**／**M3 歸零**／M4 1h40m（＝SPEC §5 的可視化）。
    #     (b) `EID-16077` PM ＝ **全量繼承**：CLN DI 四個工位**各記完整 4h**（不是各 1h）。
    #   ⚠ 錨日**刻意選前兩天（07-04）而不是「昨天」（07-05）**：`_headline` 的
    #     `yd_stop_h`／`top_machine` 只看昨天那一欄，把 6h50m 的母單放在昨天會讓 demo 的
    #     「停最久機台」變成 CAL 模組、全廠昨日停機憑空多十幾小時 ⇒ 既有 demo 釘子（如
    #     `board_export` 的「摘要含停最久機台」）與 SALTMARSH demo 走查基準一起漂移。
    #     ★ 但這也照出一個**真實的口徑限制**（live 同樣存在、記在 CONCEPT §19.3 A10）：
    #       `_headline.yd_stop_h` 是「Σ 各機 union」，母單展開後會把母機那一份**按工位數重複計**
    #       ——正是「子工位時數不可相加」那條鐵則講的事。headline 是全廠概覽、不是機器停機總帳；
    #       這一輪不動它（動它＝改一個與 A10 無關的既有數字），但引用時要知道。
    _da = win_end.date() - _dt.timedelta(days=2)          # demo 錨下＝2026-07-04（非「昨天」）

    def _a10(h, m=0):
        return _dt.datetime(_da.year, _da.month, _da.day) + _dt.timedelta(hours=h, minutes=m)

    _cal_kids = wscope.PARENT_EXPANSION["EID-55232"]
    _a10_wo = w8.normalize_wo(_demo_phase_wo(
        49700, "EID-55232", _a10(8, 50), _a10(15, 40), "REACTIVE",
        [("OPEN", None, _a10(8, 50)), ("IN_PROGRESS", None, _a10(10, 25)),
         ("CLOSED", None, _a10(15, 40))]))
    _a10_wo["scope_events"] = w8.normalize_scope_events([
        {"ts": _a10(10, 30), "scope": [_cal_kids[0]]},                    # 放行其他工位
        {"ts": _a10(13, 0), "scope": [_cal_kids[0], _cal_kids[1]]},       # 臨時陪停 Bay#2
        {"ts": _a10(13, 30), "scope": [_cal_kids[0]]},                    # Bay#2 恢復
    ])[0]
    # ★ A6 生產日界：PM 母單原 06:00–11:00 跨 08:00 日界會被劈成兩欄（繼承帳 4h→3h+1h）；
    #   平移到 09:00–13:00＝同一生產日整段（全量繼承「各記完整 4h」的可視化不變）。
    _pm_wo = w8.normalize_wo(_demo_phase_wo(
        49710, "EID-16077", _a10(9, 0), _a10(13, 0), "PM",
        [("OPEN", None, _a10(9, 0)), ("IN_PROGRESS", None, _a10(9, 0)),
         ("COMPLETED", None, _a10(13, 0))]))
    _pm_wo["scope_events"] = []                            # pin (a)：欄位恆在、PM 不吃 scope
    inherited_by_eid = wscope.expand_for_children(
        {"EID-55232": [_a10_wo], "EID-16077": [_pm_wo]}, win_start, win_end)

    # ★ `#125` (b)(c) 座列 showcase（2026-09-12；常數註解＝`EQM_SEAT_SHOWCASE_EID`）。
    #   同 A10 那一段的紀律：**合成在所有既有迴圈之後**（`rnd`／`open_i` 是全域確定性序列）。
    _seat_wo = w8.normalize_wo(_demo_phase_wo(
        49800, EQM_SEAT_SHOWCASE_EID, _a10(8, 10), _a10(15, 0), "REACTIVE",
        [("OPEN", None, _a10(8, 10)), ("IN_PROGRESS", None, _a10(8, 40)),
         ("CLOSED", None, _a10(15, 0))]))
    # 10:00 把範圍從整機縮到「座 2」＝cmms WO 32136 的形（`scope` 仍是母機＝機器還是那一台，
    # 縮小的是 `sub_units`）。**改之前的 1h50m 留在母機列**、之後的 5h 才進座列。
    _seat_wo["scope_events"] = w8.normalize_scope_events([
        {"ts": _a10(10, 0), "scope": [EQM_SEAT_SHOWCASE_EID],
         "sub_units": [EQM_SEAT_SHOWCASE_UNIT]},
    ])[0]
    # 「暫停·等機台空檔」＝零可計停機的單，**照樣讓座浮出**（Chris 裁決：誠實回答
    # 「這張單在講哪一座」）。OPEN 零長度 ⇒ 無 countable 段；整段 uptime 斜紋。
    _seat_upt_wo = w8.normalize_wo(_demo_phase_wo(
        49801, EQM_SEAT_SHOWCASE_EID, _a10(9, 0), _a10(13, 0), "REACTIVE",
        [("OPEN", None, _a10(9, 0)), ("ON_HOLD", "WAITING_MACHINE_TIME", _a10(9, 0)),
         ("COMPLETED", None, _a10(13, 0))]))
    _seat_upt_wo["scope_events"] = w8.normalize_scope_events([
        {"ts": _a10(9, 0), "scope": [EQM_SEAT_SHOWCASE_EID],
         "sub_units": [EQM_SEAT_SHOWCASE_UPT_UNIT]},
    ])[0]
    wos_by_eid[EQM_SEAT_SHOWCASE_EID] = (list(wos_by_eid.get(EQM_SEAT_SHOWCASE_EID) or ())
                                         + [_seat_wo, _seat_upt_wo])

    _demo_erw = demo_erw_inputs(win_start, win_end)
    _demo_rates = demo_rates_by_eid()          # WP-C2：值與尺別同一份、不各抽一次
    return build_board(EQMT_CATALOG, wos_by_eid, win_end, n_days,
                       mode="demo", wo_status="demo",
                       mes_by_eid=mes, quiet_h=quiet_h,
                       dormant_by_eid=dormant_by_eid,
                       inherited_by_eid=inherited_by_eid,
                       mes_lookback_d=30,   # demo nosig 窗寬＝比照 live 主源（lastprod 30d）
                       # #39 該跑窗：demo 全合成（closures／slot／人力／速率皆確定性、不讀檔）
                       closures=demo_closures(win_start, win_end),
                       cal_coverage=(win_start, win_end),
                       erw_slots=_demo_erw[0], erw_manpower=_demo_erw[1],
                       rates_by_eid=_demo_rates,
                       # ★ WP-C2（2026-09-12）：demo 的速率是**合成的每機台小時**（沒有日帳、
                       #   更沒有 `rw.r_seat`）⇒ 尺別誠實標 `mh_blend`＝B 尺。demo 永遠走 B
                       #   **是對的**：合成數字不該冒充量出來的每座速率（QG-4.2 混源禁令的
                       #   同一條理由）。A 尺只在 live 有新日帳時出現。
                       rate_kind_by_eid={e: "mh_blend" for e in _demo_rates},
                       # ★ 2026-09-12（`#110` (2)(4)）：demo 也帶座係數＝**同一支純函式**
                       #   （live 在 app 層餵真 A11 事件流；demo 走橋常數＝`dormant_events=None`）。
                       seat_factors=seat_factors_for(
                           EQMT_CATALOG, wos_by_eid, day_wins, win_end),
                       # ★ 2026-09-08：demo 也帶子工位（與 landing 同一支合成）
                       sub_unit_ages_by_eid=demo_sub_unit_ages())


def demo_sub_unit_ages():
    """demo 合成的子工位年齡 `{eid: {unit_id: age_h}}`（確定性；**唯一出處**）。

    ★ 為什麼住在這裡而不是 `app`：`/equipment` 的 demo 板與 `/landing2` 的 demo 方塊
      **必須是同一份合成**，否則同一台機在兩頁會有兩套座位顏色。`app._ld_demo_state`
      import 這一支，不自己再抽一次（同 `demo_rates_by_eid` 的紀律）。
    ★ 口徑（合成、不是量測）：每座各自抽——約 55% 近期生產（≤ `ud.MES_RECENT_H`）、
      25% 較舊、20% **整個鍵缺席**＝「這一座不知道」⇒ 畫空心（C-LD2）。
    ★ **保證每台看得到混色**：抽完若整台同一形，把最後一座確定性地改成另一形。
      demo 的用途是展示這個功能長什麼樣；一台全綠的 k 格看不出它有 k 格。
    ⚠ 形③（`PARENT_EXPANSION` 的母機）不合成：它的子槽位各自是名冊上的一列、
      各有自己的方塊與狀態，不畫子工位格（`build_board` 同一條判斷）。
    """
    out = {}
    for eid in _wcap.machines_with_units():
        if eid in _wcap.PARENT_EXPANSION:
            continue
        units = _wcap.installed_units(eid)
        got, kinds = {}, []
        for i, unit in enumerate(units):
            rnd = random.Random(_seed(eid + "|" + unit) * 1009 + 7)
            p = rnd.random()
            if p < 0.55:
                got[unit] = round(rnd.uniform(0.05, max(0.1, ud.MES_RECENT_H * 0.9)), 2)
                kinds.append("run")
            elif p < 0.80:
                got[unit] = round(rnd.uniform(ud.MES_RECENT_H + 1.0, 240.0), 1)
                kinds.append("old")
            else:
                kinds.append("unknown")          # 鍵缺席＝不知道（不是 0、不是「沒跑」）
        if len(units) >= 2 and len(set(kinds)) == 1:
            last = units[-1]
            if kinds[0] == "run":
                got[last] = 99.0                 # 綠 → 灰
            elif kinds[0] == "old":
                got[last] = 0.2                  # 灰 → 綠
            else:
                got[last] = 0.3                  # 全不知道 → 補一座綠
        out[eid] = got
    return out


def _demo_mes_by_eid(catalog):
    """demo 合成每台 MES 近期生產 age（確定性）＝讓 demo 展示 Now 欄全部四形：
       ~12% 值 None（整窗無生產）          → UP·待命 ·「近 N 天無生產」中性小字、不掛 ⚠；
       ~10% 久靜（QUIET_H+8～+88h > QUIET_H）→ UP·待命 ⚠（狀態仍 UP）；
       ~18% 待命（2.5–20h）               → UP·待命 ·「最後生產 Xh 前」；
       其餘 近期生產（≤ud.MES_RECENT_H）  → UP·生產中。
       有 count 工單者 _now_block 仍先判 DOWN（工單優先）。seed 與 WO 合成不同、避免相關。
       ★ 2026-08-17 起 12% 那批**塞 None、不再缺 key**＝跟上 live 主源（lastprod 狀態檔）的
       語意：整份名冊都在涵蓋範圍、無訊號＝顯式 None（nosig 小字）。「缺 key」形只剩
       探測 fallback 會產生、demo 不再合成（rnd 序列未動 ⇒ 其餘 88% 逐位不變）。"""
    out = {}
    for _area, _name, eid in catalog:
        rnd = random.Random(_seed(eid) * 977 + 13)
        r = rnd.random()
        if r < 0.12:
            out[eid] = None                                  # 整窗無生產＝nosig（塞 None、序列未動）
            continue
        if r < 0.22:
            # 久靜 → ⚠。帶寬掛在 QUIET_H 上（R45）：QUIET_H=72 時＝原 80–160h 逐位元同；
            # 門檻改 168h 後自動平移成 176–256h ⇒ demo 的 ⚠ showcase 不因改門檻而消失。
            out[eid] = round(rnd.uniform(QUIET_H + 8.0, QUIET_H + 88.0), 2)
        elif r < 0.40:
            out[eid] = round(rnd.uniform(2.5, 20.0), 2)      # 待命（有 age 小字）
        else:
            out[eid] = round(rnd.uniform(0.1, 1.8), 2)       # UP·生產中
    return out


# ---------------------------------------------------------------------------
# --selftest（純函式、零 I/O、零 MES、零 HTTP、deterministic）
# ---------------------------------------------------------------------------

def _selftest():
    D = _dt.datetime
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    import re
    eid_re = re.compile(r"^EID-\d{5}$")
    import core_paths as _wp_pub
    # WP-10d（R-P11）：橋上兩台長停工位由橋常數推導（私有樹＝EID-26841／EID-26887；公開樹＝合成名冊的橋）
    _D2, _D3 = (r[0] for r in wdorm.DORMANT_BRIDGE)
    # WP-10d（R-P11）：具名機台的角色表——私有樹＝原本寫死的真編號（一位元不動）；公開樹＝由合成名冊／橋／折疊表推導
    import mes_timeline as _mt_r
    _bridge_r = {r[0] for r in wdorm.DORMANT_BRIDGE}
    _par_r = list(wscope.PARENT_EXPANSION)
    if _wp_pub.is_public_tree():
        _E0, _ES, _EL = EQMT_CATALOG[0][2], EQM_SEAT_SHOWCASE_EID, EQMT_CATALOG[-1][2]
        _PA, _PB = _par_r[0], _par_r[1]
        _k1 = [k for k in wscope.PARENT_EXPANSION[_PA] if k not in _bridge_r]
        _K1a, _K1b = (_k1 + [None, None])[:2]
        _I1, _I2, _I3 = wscope.PARENT_EXPANSION[_PB][:3]
        _NE = list(_wcap.NEST_EAV_UNITS)[0]
        _used_r = set(_par_r) | {k for v in wscope.PARENT_EXPANSION.values() for k in v} | set(_wcap.NEST_EAV_UNITS)             | set(_wcap.COTAG_CHILD_UNITS) | {c for v in _wcap.COTAG_CHILD_UNITS.values() for c in v}
        _zc_r = [e for a, _n, e in EQMT_CATALOG if a == "zc" and e not in _used_r and e != _E0]
        _C3, _C1, _C2 = _zc_r[0], _zc_r[1], _zc_r[2]
        _CTP = list(_wcap.COTAG_CHILD_UNITS)[0]
        _CT1, _CT2 = _wcap.COTAG_CHILD_UNITS[_CTP][:2]
        _FR, _FM = next(iter(_mt_r.EID_RENAME.items()))
        _FD = next(iter(_mt_r.EID_ALIAS_DROP))
    else:
        _E0, _ES, _EL = "EID-12724", "EID-26718", "EID-37891"
        _PA, _PB = "EID-24742", "EID-16077"
        _K1a, _K1b = "EID-24790", "EID-26820"
        _I1, _I2, _I3 = "EID-14732", "EID-14979", "EID-14556"
        _NE = "EID-00425"
        _C3, _C1, _C2 = "EID-00884", "EID-00950", "EID-17431"
        _CTP, _CT1, _CT2 = "EID-08158", "EID-05707", "EID-07757"
        _FM, _FR, _FD = "EID-38106", "EID-38041", "EID-38072"

    # ⚠ 2026-10-02：原本這裡有 `_pub_skip()`，讓 (c)／S5e／S5f2 三根 showcase 斷言在公開樹標 skipped
    #   （demo 亂數種子＝EID 字串的函數、公開樹 EID 是假編號）。現況：(c) 的 open 單改由 `demo_board`
    #   顯式保底（私有樹 demo 板逐位元不變）；S5e／S5f2 在公開樹的合成名冊（WP-10e 二版）下本來就成立
    #   ⇒ 三根兩棵樹都照釘、不再略過。哪天合成名冊換版讓它們紅＝該紅（同常數註解「走鐘＝紅燈」的紀律）。

    # ---- (A) 目錄完整性（原封保留）----
    # ⚠ Burn-in（`EID-39940`）退役後，Zone C 區與名冊總數同步調整。
    #   （Chris 當面裁；存證＝上面名冊那段註解與 `SPEC-LANDING.md` §15.4-c）。
    #   這一根**刻意釘死私有值**：它就是「名冊有幾台」這件事本身的釘子，改成
    #   `len(EQMT_CATALOG)` 等於釘「它等於它自己」＝把唯一會擋住誤刪的閘拿掉。
    # WP-10d（R-P11）：公開樹的名冊是合成的（台數／區分布／具名列都不同）⇒ 這些「釘私有名冊」的斷言只在私有樹釘死；
    #   公開樹改驗名冊常數自身的結構不變式（非空、區序、無 dup、EID 形），不放寬私有樹。
    _pub_tree = _wp_pub.is_public_tree()
    if _pub_tree:
        ck(len(EQMT_CATALOG) > 0, "目錄非空")
    else:
        import board_private_pins as _pp
        _n = _pp.PINS["eqmeeting.catalog_n"]
        ck(len(EQMT_CATALOG) == _n, f"目錄台數應與私有釘值相同，實 {len(EQMT_CATALOG)}")
    # ⚠ `EID-25846` Seal#7 退役（Seal#4 `EID-25526` 接位＝換列不刪列，
    #   總數與 Zone B 區台數皆不變；存證＝`SPEC-LANDING.md` §15.5）⇒ 加進負斷言名單。
    counts = {}
    for a, _nm, _e in EQMT_CATALOG:
        counts[a] = counts.get(a, 0) + 1
    if not _pub_tree:
        ck(not any(e in ("EID-25270", "EID-25304", "EID-39940", "EID-25846")
                   for _a, _n, e in EQMT_CATALOG),
           "退役的 AutoOQC#4／#5／Burn-in／Seal#7 不得再出現在名冊裡")
        ck(("zb", "Seal#4", "EID-25526") in EQMT_CATALOG,
           "Seal#4（EID-25526）已接 Seal#7 的位")
        ck(counts["za"] == _pp.PINS["eqmeeting.area_za_n"], f"Zone A 台數應與私有釘值相同，實 {counts.get('za')}")
        ck(counts["zb"] == _pp.PINS["eqmeeting.area_zb_n"], f"Zone B 台數應與私有釘值相同，實 {counts.get('zb')}")
        # ⚠ Burn-in 退役後，Zone C 區與名冊總數同步調整。
        ck(counts["zc"] == _pp.PINS["eqmeeting.area_zc_n"], f"Zone C 台數應與私有釘值相同，實 {counts.get('zc')}")
        ck(counts["zd"] == _pp.PINS["eqmeeting.area_zd_n"], f"Zone D 台數應與私有釘值相同，實 {counts.get('zd')}")
    else:
        ck(all(counts.get(k, 0) > 0 for k, _d in AREAS), "每個區至少一台")
        ck(sum(counts.values()) == len(EQMT_CATALOG) and set(counts) <= {k for k, _d in AREAS},
           "區鍵只能是 AREAS 的鍵")
    ck(all(eid_re.match(e) for _a, _nm, e in EQMT_CATALOG), "所有 EID 應 EID-\\d{5}")
    _area_order = [k for k, _d in AREAS]
    ck([a for a, _nm, _e in EQMT_CATALOG]
       == sorted((a for a, _nm, _e in EQMT_CATALOG), key=_area_order.index),
       "area_key 順序應依 AREAS 序且不交錯")
    if not _pub_tree:
        ck(AREAS == [("za", "Zone A"), ("zb", "Zone B"), ("zc", "Zone C"), ("zd", "Zone D")],
           "AREAS 定義")
    else:
        ck(_area_order == ["za", "zb", "zc", "zd"], "AREAS 鍵序（程式分支鍵）")
    # dup：Chris 2026-07-06 裁決後無容許 dup——任何重複＝FAIL
    eids = [e for _a, _nm, e in EQMT_CATALOG]
    dup_eids = {e for e in eids if eids.count(e) > 1}
    ck(dup_eids == KNOWN_DUP_EIDS == set(), f"目錄不得有 dup，實 {dup_eids}")
    if not _pub_tree:
        ck(("zc", "AutoOQC", "EID-36255") in EQMT_CATALOG
           and not any(a == "zd" and e == "EID-36255" for a, _nm, e in EQMT_CATALOG),
           "36255 應僅存 Zone C AutoOQC 一列")
        ck(EQMT_CATALOG[0] == ("za", "Frame#2", _E0), "首列 spot check")
        ck(("zc", "CAL#3", _C3) in EQMT_CATALOG, "CAL#3 補零 spot check")
        ck(EQMT_CATALOG[-1] == ("zd", "Low Power Microscope", _EL), "末列 spot check")
    else:
        ck(EQMT_CATALOG[0][0] == AREAS[0][0] and EQMT_CATALOG[-1][0] == AREAS[-1][0],
           "首列落第一區、末列落末區（區序）")

    # ---- (B) day_windows ----
    we = D(2026, 7, 6, 14, 30, 0)
    dw = day_windows(we, 5)
    ck(len(dw) == 5, "day_windows 應 5 欄")
    ck(dw[-1][2] == we and dw[-1][1] == D(2026, 7, 6, 8, 0),
       "末欄＝今日生產日 partial（08:00→win_end；A6 換軸）")
    ck(dw[0] == ("07-02", D(2026, 7, 2, 8), D(2026, 7, 3, 8)), f"首欄完整生產日（08:00 界）: {dw[0]}")
    # ★ A6 清晨歸屬：win_end 在 08:00 前 ⇒ 今日欄＝**前一個生產日**（00:00–07:59 屬前日）
    _dw_am = day_windows(D(2026, 7, 6, 6, 0), 2)
    ck(_dw_am[-1][0] == "07-05" and _dw_am[-1][1] == D(2026, 7, 5, 8, 0),
       f"清晨 06:00 ⇒ 仍屬 07-05 生產日: {_dw_am[-1]}")
    ck(all(dw[i][2] == dw[i + 1][1] for i in range(len(dw) - 1)), "日欄應連續（前 d_end＝後 d_start）")
    ck(all((dw[i][2] - dw[i][1]) == _dt.timedelta(days=1) for i in range(len(dw) - 1)),
       "非末欄應整日 24h")
    ck(dw[2][0] == "07-04" and re.match(r"^\d{2}-\d{2}$", dw[2][0]), "date_str 格式 MM-DD")
    ck(day_windows(we, 1)[0][2] == we, "n_days=1 → 單欄今日 partial")

    # ---- (C) wo_segments（裁切 / ongoing / 未來 guard / state 對映 / 故障停機不現）----
    NOW = we
    win_start = dw[0][1]                       # 07-02 00:00
    # 窗內已完工 REACTIVE → 維修段（裁到窗內）
    wo_rx = w8.normalize_wo({"work_order_no": 51, "asset_id": "EID-X", "work_type": "REACTIVE",
                             "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                             "closed_at": _to_utc_z(D(2026, 7, 4, 6, 0)), "status_history": []})
    segs = wo_segments([wo_rx], win_start, we)
    ck(len(segs) == 1 and segs[0]["state"] == "維修" and segs[0]["source"] == "cmms_workorder",
       f"REACTIVE→維修段: {segs}")
    ck(segs[0]["start"] == D(2026, 7, 4, 2, 0) and segs[0]["end"] == D(2026, 7, 4, 6, 0)
       and segs[0]["ongoing"] is False and segs[0]["countable"] is True,
       "窗內完工段時戳 + ongoing False + countable True（遷移單整窗）")
    ck(segs[0]["work_order_no"] == 51, "segment 帶 work_order_no")
    # ★S12：open PM 單只有 OPEN 條目（未開始處理）→ 機台還在跑 → **不產任何段**（截圖案例）
    wo_pm = w8.normalize_wo({"work_order_no": 52, "asset_id": "EID-X", "work_type": "PM",
                             "opened_at": _to_utc_z(D(2026, 7, 6, 6, 0)),
                             "status_history": [{"to_status": "OPEN",
                                                 "changed_at": _to_utc_z(D(2026, 7, 6, 6, 0))}]})
    ck(wo_segments([wo_pm], win_start, we) == [],
       "S12：open PM 只有 OPEN 條目 → 不計停機、無段（機台還在跑）")
    # ★S12：open REACTIVE OPEN→IN_PROGRESS（處理中）→ ongoing 維修段至 win_end、countable
    wo_rx_ip = w8.normalize_wo({"work_order_no": 57, "asset_id": "EID-X", "work_type": "REACTIVE",
                               "opened_at": _to_utc_z(D(2026, 7, 6, 6, 0)),
                               "status_history": [
                                   {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 6, 6, 0))},
                                   {"to_status": "IN_PROGRESS",
                                    "changed_at": _to_utc_z(D(2026, 7, 6, 8, 0))}]})
    segs_ip = wo_segments([wo_rx_ip], win_start, we)
    ck(len(segs_ip) == 1 and segs_ip[0]["state"] == "維修" and segs_ip[0]["ongoing"] is True
       and segs_ip[0]["end"] == we and segs_ip[0]["countable"] is True
       and segs_ip[0]["start"] == D(2026, 7, 6, 6, 0),
       f"S12：open REACTIVE(OPEN→IN_PROGRESS) → ongoing 維修段(含 OPEN 前段): {segs_ip}")
    # 早於窗的單 → 裁到 win_start（頭被裁）
    wo_early = w8.normalize_wo({"work_order_no": 53, "asset_id": "EID-X", "work_type": "REACTIVE",
                               "opened_at": _to_utc_z(D(2026, 6, 30, 8, 0)),
                               "closed_at": _to_utc_z(D(2026, 7, 2, 12, 0)), "status_history": []})
    segs_e = wo_segments([wo_early], win_start, we)
    ck(len(segs_e) == 1 and segs_e[0]["start"] == win_start and segs_e[0]["end"] == D(2026, 7, 2, 12, 0),
       f"早於窗 → 頭裁到 win_start（08:00 生產日界）: {segs_e}")
    # 完全在窗外（早於 win_start 就收工）→ 空
    wo_out = w8.normalize_wo({"work_order_no": 54, "asset_id": "EID-X", "work_type": "PM",
                             "opened_at": _to_utc_z(D(2026, 6, 20, 8, 0)),
                             "closed_at": _to_utc_z(D(2026, 6, 21, 8, 0)), "status_history": []})
    ck(wo_segments([wo_out], win_start, we) == [], "窗外工單 → 無段")
    # CONVERSION→調機、長尾→其他維護(子類待定)；None WO 容忍
    wo_cv = w8.normalize_wo({"work_order_no": 55, "asset_id": "EID-X", "work_type": "CONVERSION",
                             "opened_at": _to_utc_z(D(2026, 7, 5, 1, 0)),
                             "closed_at": _to_utc_z(D(2026, 7, 5, 2, 0)), "status_history": []})
    wo_pr = w8.normalize_wo({"work_order_no": 56, "asset_id": "EID-X", "work_type": "PART REQUEST",
                             "opened_at": _to_utc_z(D(2026, 7, 5, 3, 0)),
                             "closed_at": _to_utc_z(D(2026, 7, 5, 4, 0)), "status_history": []})
    segs_m = wo_segments([wo_cv, None, wo_pr], win_start, we)
    states_m = {s["state"] for s in segs_m}
    ck(states_m == {"調機", w8.BUCKET_STATE}, f"CONVERSION/長尾 態對映: {states_m}")
    ck("故障停機" not in {s["state"] for grp in
                          [wo_segments([w], win_start, we) for w in [wo_rx, wo_pm, wo_cv, wo_pr]]
                          for s in grp}, "故障停機不得由工單路徑產生（裁決②）")
    ck(wo_segments([], win_start, we) == [] and wo_segments(None, win_start, we) == [],
       "空/None 容忍")

    # ---- (C2) S12 階段口徑（_phase_kind / hold-uptime 扣除含跨日 / 遷移整窗 / _now_block 新規）----
    # _phase_kind 逐條（Chris 2026-07-07 裁決階梯）
    ck(_phase_kind(None, None, "PM") == "count", "遷移整窗段（status None）→ count")
    ck(_phase_kind("OPEN", None, "REACTIVE") == "count", "REACTIVE-OPEN → count（壞了才報修）")
    ck(_phase_kind("OPEN", None, "PM") == "none", "PM-OPEN → none（未開始處理）")
    ck(_phase_kind("OPEN", None, "CONVERSION") == "none", "調機-OPEN → none")
    ck(_phase_kind("OPEN", None, "PART REQUEST") == "none", "暫桶-OPEN → none")
    ck(_phase_kind("IN_PROGRESS", None, "PM") == "count", "IN_PROGRESS → count（處理中一律算）")
    ck(_phase_kind("ON_HOLD", "WAITING_MACHINE_TIME", "REACTIVE") == "uptime",
       "ON_HOLD 等機台空檔 → uptime（機台可生產）")
    ck(_phase_kind("ON_HOLD", "TEST_RUN", "PM") == "uptime", "ON_HOLD 試跑觀察 → uptime")
    ck(_phase_kind("ON_HOLD", w8.HOLD_REASON_UNKNOWN, "PM") == "count",
       "ON_HOLD 未知「不知道」→ count（保守計停機）")
    ck(_phase_kind("ON_HOLD", None, "REACTIVE") == "count", "ON_HOLD 無 hold_reason → count")
    # S12 脫鉤鐵則：canonical 計停機三值（w8 顯示集認得、但**不是** uptime）→ count
    for _hr in ("WAITING_PARTS", "WAITING_VENDOR", "OTHER"):
        ck(_phase_kind("ON_HOLD", _hr, "PM") == "count",
           f"ON_HOLD {_hr} → count（計停機；顯示集≠不計停機集）")
    ck(UPTIME_HOLD_REASONS == ("WAITING_MACHINE_TIME", "TEST_RUN"),
       "UPTIME_HOLD_REASONS 永遠只有兩值（cmms 2026-07-07 明證完整）")
    ck(set(UPTIME_HOLD_REASONS) < set(w8.HOLD_REASONS_KNOWN),
       "uptime 集 ⊂ w8 顯示集、且真子集（脫鉤成立）")
    # 遷移單（history 空、非 open）PM → 整窗算（rule 5，不論 work_type）
    wo_mig_pm = w8.normalize_wo({"work_order_no": 60, "asset_id": "EID-X", "work_type": "PM",
                                "opened_at": _to_utc_z(D(2026, 7, 3, 1, 0)),
                                "closed_at": _to_utc_z(D(2026, 7, 3, 5, 0)), "status_history": []})
    segs_mig = wo_segments([wo_mig_pm], win_start, we)
    ck(len(segs_mig) == 1 and segs_mig[0]["state"] == "計畫保養"
       and segs_mig[0]["countable"] is True
       and abs((segs_mig[0]["end"] - segs_mig[0]["start"]).total_seconds() - 4 * 3600) < 1,
       f"遷移 PM 整窗算 4h（rule 5）: {segs_mig}")
    # hold-uptime 扣除（含跨午夜）：REACTIVE OPEN→IN_PROGRESS→ON_HOLD(WAITING)→IN_PROGRESS→COMPLETED
    wo_hold = w8.normalize_wo({"work_order_no": 61, "asset_id": "EID-X", "work_type": "REACTIVE",
                              "opened_at": _to_utc_z(D(2026, 7, 4, 20, 0)),
                              "status_history": [
                                  {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 4, 20, 0))},
                                  {"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(D(2026, 7, 4, 21, 0))},
                                  {"to_status": "ON_HOLD", "hold_reason": "WAITING_MACHINE_TIME",
                                   "changed_at": _to_utc_z(D(2026, 7, 4, 23, 0))},
                                  {"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(D(2026, 7, 5, 1, 0))},
                                  {"to_status": "COMPLETED", "changed_at": _to_utc_z(D(2026, 7, 5, 3, 0))}]})
    segs_h = wo_segments([wo_hold], win_start, we)
    ck([s["state"] for s in segs_h] == ["維修", UPTIME_WAIT_STATE, "維修"],
       f"hold 序列＝維修 / 暫停·等機台空檔 / 維修: {[s['state'] for s in segs_h]}")
    up = [s for s in segs_h if not s["countable"]]
    ck(len(up) == 1 and up[0]["state"] == UPTIME_WAIT_STATE and up[0]["ongoing"] is False
       and abs((up[0]["end"] - up[0]["start"]).total_seconds() - 2 * 3600) < 1,
       f"uptime-hold 段 2h、countable False、ongoing False: {up}")
    ck(abs(sum((s["end"] - s["start"]).total_seconds() for s in segs_h if s["countable"])
           / 3600.0 - 5.0) < 1e-9, "countable 停機＝5h（維修 3h + 2h，扣掉 hold 2h）")
    bkh = bucket_segments_by_day(segs_h, dw)
    # ★ A6 換軸的示範案例：20:00→次日 03:00 的跨午夜修單**完整落在同一個生產日欄**
    #   （07-04 生產日＝07-04 08:00–07-05 08:00）⇒ 不再被午夜劈成兩欄。
    ck(abs(day_cell(bkh["07-04"])["stop_h"] - 5.0) < 1e-9,
       f"07-04（生產日）stop_h＝5h（3h+2h 維修；跨午夜不劈欄＝A6）: {day_cell(bkh['07-04'])['stop_h']}")
    ck(abs(day_cell(bkh["07-05"])["stop_h"] - 0.0) < 1e-9,
       f"07-05（生產日）stop_h＝0（整張單屬前一生產日）: {day_cell(bkh['07-05'])['stop_h']}")
    c704, c705 = day_cell(bkh["07-04"]), day_cell(bkh["07-05"])
    ck(abs(c704["by_uptime"].get(UPTIME_WAIT_STATE, 0) - 2.0) < 1e-9
       and not c705["by_uptime"],
       f"by_uptime 等機台空檔 2h 全在 07-04 生產日: {c704['by_uptime']}/{c705['by_uptime']}")
    ck(UPTIME_WAIT_STATE not in c704["by_state"] and "維修" not in c704["by_uptime"],
       "by_uptime 與 by_state 互斥（uptime 態不入 by_state、計停機態不入 by_uptime）")
    # 純 uptime 日（stop_h=0 但 by_uptime 非空）：OPEN 0 長度 → ON_HOLD(WAITING_MACHINE_TIME) → COMPLETED
    wo_pure = w8.normalize_wo({"work_order_no": 64, "asset_id": "EID-X", "work_type": "REACTIVE",
                              "opened_at": _to_utc_z(D(2026, 7, 4, 8, 0)),
                              "closed_at": _to_utc_z(D(2026, 7, 4, 14, 0)),
                              "status_history": [
                                  {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 4, 8, 0))},
                                  {"to_status": "ON_HOLD", "hold_reason": "WAITING_MACHINE_TIME",
                                   "changed_at": _to_utc_z(D(2026, 7, 4, 8, 0))},
                                  {"to_status": "COMPLETED", "changed_at": _to_utc_z(D(2026, 7, 4, 14, 0))}]})
    segs_pure = wo_segments([wo_pure], win_start, we)
    ck(len(segs_pure) == 1 and all(not s["countable"] for s in segs_pure)
       and segs_pure[0]["state"] == UPTIME_WAIT_STATE,
       f"純 uptime 單：只有 uptime 段、無 countable（OPEN 0 長度略過）: "
       f"{[(s['state'], s['countable']) for s in segs_pure]}")
    cell_pure = day_cell(bucket_segments_by_day(segs_pure, dw)["07-04"])
    ck(cell_pure["stop_h"] == 0 and cell_pure["dominant"] is None and cell_pure["by_state"] == {}
       and abs(cell_pure["by_uptime"].get(UPTIME_WAIT_STATE, 0) - 6.0) < 1e-9,
       f"純 uptime 日：stop_h=0、dominant None、by_state 空、by_uptime 等機台空檔 6h: {cell_pure}")

    # ---- (C3) ON_HOLD 拆色（一眼看色帶原則，2026-07-07）：等料/等外包獨立計停機態、
    #          OTHER/未知留母態、兩 uptime 子類各自態 + countable=False 不入 stop_h ----
    def _hold_seq(hr, wt="REACTIVE"):
        return w8.normalize_wo({"work_order_no": 70, "asset_id": "EID-X", "work_type": wt,
                                "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                                "status_history": [
                                    {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 4, 2, 0))},
                                    {"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(D(2026, 7, 4, 3, 0))},
                                    {"to_status": "ON_HOLD", "hold_reason": hr,
                                     "changed_at": _to_utc_z(D(2026, 7, 4, 4, 0))},
                                    {"to_status": "COMPLETED", "changed_at": _to_utc_z(D(2026, 7, 4, 7, 0))}]})
    # WAITING_PARTS → 等料(停機) 獨立態、countable=True、進 stop_h/by_state
    sp = wo_segments([_hold_seq("WAITING_PARTS")], win_start, we)
    ck([s["state"] for s in sp] == ["維修", HOLD_PARTS_STATE],
       f"WAITING_PARTS 拆出 等料(停機)（母態 REACTIVE→維修先段）: {[s['state'] for s in sp]}")
    parts_seg = [s for s in sp if s["state"] == HOLD_PARTS_STATE][0]
    ck(parts_seg["countable"] is True
       and abs((parts_seg["end"] - parts_seg["start"]).total_seconds() - 3 * 3600) < 1,
       f"等料段 countable=True、3h（04:00–07:00）: {parts_seg}")
    cell_p = day_cell(bucket_segments_by_day(sp, dw)["07-03"])   # 02:00–07:00＝07-03 生產日（A6）
    ck(abs(cell_p["by_state"].get(HOLD_PARTS_STATE, 0) - 3.0) < 1e-9
       and abs(cell_p["stop_h"] - 5.0) < 1e-9,
       f"等料進 by_state(3h) + stop_h(維修2h∪等料3h=5h): {cell_p}")
    # WAITING_VENDOR → 等外包(停機) 獨立態
    sv = wo_segments([_hold_seq("WAITING_VENDOR")], win_start, we)
    ck([s["state"] for s in sv] == ["維修", HOLD_VENDOR_STATE]
       and [s for s in sv if s["state"] == HOLD_VENDOR_STATE][0]["countable"] is True,
       f"WAITING_VENDOR 拆出 等外包(停機)、countable: {[s['state'] for s in sv]}")
    # OTHER / 未知「不知道」→ 留母態（保守、不開第 9 色）；仍 countable
    for _hr, _lbl in ((("OTHER"), "OTHER"), (w8.HOLD_REASON_UNKNOWN, "未知")):
        so = wo_segments([_hold_seq(_hr, wt="PM")], win_start, we)
        ck({s["state"] for s in so} == {"計畫保養"}
           and all(s["countable"] for s in so)
           and HOLD_PARTS_STATE not in {s["state"] for s in so},
           f"ON_HOLD {_lbl} → 留母態 計畫保養、不拆色、countable: {[s['state'] for s in so]}")
    # 兩 uptime 子類各自態、countable=False、被 stop_h 排除、且相鄰合併只合同態
    st = wo_segments([_hold_seq("TEST_RUN")], win_start, we)
    test_seg = [s for s in st if not s["countable"]]
    ck(len(test_seg) == 1 and test_seg[0]["state"] == UPTIME_TEST_STATE,
       f"TEST_RUN → 暫停·試跑（countable False）: {[s['state'] for s in st]}")
    ck(abs(day_cell(bucket_segments_by_day(st, dw)["07-03"])["stop_h"] - 2.0) < 1e-9,
       "TEST_RUN uptime 段被 stop_h 排除（只剩維修 02–04=2h；02:00 屬 07-03 生產日）")
    ck(_uptime_state("WAITING_MACHINE_TIME") == UPTIME_WAIT_STATE
       and _uptime_state("TEST_RUN") == UPTIME_TEST_STATE, "_uptime_state 對映")
    ck(HOLD_DOWN_STATES == {"WAITING_PARTS": HOLD_PARTS_STATE, "WAITING_VENDOR": HOLD_VENDOR_STATE},
       "HOLD_DOWN_STATES 只含等料/等外包")
    ck(_UPTIME_STATES == (UPTIME_WAIT_STATE, UPTIME_TEST_STATE), "_UPTIME_STATES 兩子類")
    # 相鄰合併：同 hold_reason 連兩段（同態）合併、不同態不合併（等料↔維修分色帶）
    ck(len([s for s in sp if s["state"] == HOLD_PARTS_STATE]) == 1
       and len([s for s in sp if s["state"] == "維修"]) == 1,
       "等料↔維修 不同態不合併＝各自一色帶")
    # _now_block 新規：PM-OPEN 現在 UP；REACTIVE-IP 現在 DOWN·維修；ON_HOLD 等機台空檔 現在 UP
    ck(_now_block([wo_pm], we)["bucket"] == "up", "S12：open PM 只 OPEN → 現在 UP（機台還在跑）")
    nb_ip = _now_block([wo_rx_ip], we)
    ck(nb_ip["bucket"] == "down" and nb_ip["state"] == "維修" and nb_ip["wo_no"] == 57,
       f"S12：open REACTIVE 處理中 → 現在 DOWN·維修: {nb_ip}")
    wo_hold_now = w8.normalize_wo({"work_order_no": 62, "asset_id": "EID-X", "work_type": "REACTIVE",
                                  "opened_at": _to_utc_z(D(2026, 7, 6, 6, 0)),
                                  "status_history": [
                                      {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 6, 6, 0))},
                                      {"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(D(2026, 7, 6, 7, 0))},
                                      {"to_status": "ON_HOLD", "hold_reason": "WAITING_MACHINE_TIME",
                                       "changed_at": _to_utc_z(D(2026, 7, 6, 8, 0))}]})
    ck(_now_block([wo_hold_now], we)["bucket"] == "up",
       "S12：現在 ON_HOLD 等機台空檔 → UP（機台可生產）")

    # ---- (D) group_wos_by_eid（None/壞容忍）----
    wA = w8.normalize_wo({"work_order_no": 1, "asset_id": _C3, "work_type": "PM",
                          "opened_at": "2026-07-04T08:00:00", "status_history": []})
    wB = w8.normalize_wo({"work_order_no": 2, "asset_id": _C1, "work_type": "REACTIVE",
                          "opened_at": "2026-07-04T08:00:00", "status_history": []})
    wC = w8.normalize_wo({"work_order_no": 3, "asset_id": _C3, "work_type": "PM",
                          "opened_at": "2026-07-05T08:00:00", "status_history": []})
    g = group_wos_by_eid([wA, None, wB, "garbage", wC, {"no_asset": 1}])
    ck(set(g) == {_C3, _C1}, "group_wos 分組鍵錯")
    ck(len(g[_C3]) == 2 and len(g[_C1]) == 1, "group_wos 分組計數錯")

    # ---- (E) bucket_segments_by_day（秒數守恆 / 整日 / 今日 partial）----
    # ★ A6：拆欄的邊界從午夜換成 08:00 生產日界——跨午夜（22:00→01:00）不再拆；
    #   跨 08:00（06:00→09:00）才拆。兩個都測、秒數守恆不變。
    seg_mid = {"start": D(2026, 7, 3, 22, 0), "end": D(2026, 7, 4, 1, 0),
               "state": "維修", "source": "cmms_workorder", "work_order_no": 9, "ongoing": False}
    bk_mid = bucket_segments_by_day([seg_mid], dw)
    ck(len(bk_mid["07-03"]) == 1 and not bk_mid["07-04"]
       and bk_mid["07-03"][0]["stop_sec"] == 3 * 3600,
       "A6：跨午夜段整段留在同一生產日欄（22:00–01:00 全屬 07-03）")
    seg_cm = {"start": D(2026, 7, 4, 6, 0), "end": D(2026, 7, 4, 9, 0),
              "state": "維修", "source": "cmms_workorder", "work_order_no": 9, "ongoing": False}
    bk = bucket_segments_by_day([seg_cm], dw)
    ck(len(bk["07-03"]) == 1 and len(bk["07-04"]) == 1, "跨 08:00 生產日界應拆兩欄")
    ck(bk["07-03"][0]["stop_sec"] == 2 * 3600 and bk["07-04"][0]["stop_sec"] == 1 * 3600,
       "跨日界裁段秒數應 2h/1h（06–08／08–09）")
    ck(bk["07-03"][0]["stop_sec"] + bk["07-04"][0]["stop_sec"] == 3 * 3600, "秒數守恆＝原長 3h")
    ck(bk["07-03"][0]["state"] == "維修" and bk["07-03"][0]["work_order_no"] == 9,
       "裁段應保留原 segment 全欄")
    seg_today = {"start": D(2026, 7, 6, 13, 0), "end": D(2026, 7, 6, 20, 0), "state": "維修"}
    bk3 = bucket_segments_by_day([seg_today], dw)
    ck(bk3["07-06"][0]["end"] == we and bk3["07-06"][0]["stop_sec"] == 1.5 * 3600,
       "今日 partial 應裁到 win_end（1.5h）")

    # ---- (F) day_cell（union 重疊 / by_state 原始加總 / dominant / ongoing）----
    # 同日兩張重疊工單：維修 02:00–06:00、計畫保養 04:00–08:00 → union 6h、by_state 各 4h（和 8h）
    ov = [{"start": D(2026, 7, 4, 2, 0), "end": D(2026, 7, 4, 6, 0), "state": "維修",
           "stop_sec": 4 * 3600, "work_order_no": 71, "ongoing": False},
          {"start": D(2026, 7, 4, 4, 0), "end": D(2026, 7, 4, 8, 0), "state": "計畫保養",
           "stop_sec": 4 * 3600, "work_order_no": 72, "ongoing": False}]
    cell = day_cell(ov)
    ck(abs(cell["stop_h"] - 6.0) < 1e-9, f"重疊 union stop_h 應 6h（02–08）: {cell['stop_h']}")
    ck(abs(cell["by_state"]["維修"] - 4.0) < 1e-9 and abs(cell["by_state"]["計畫保養"] - 4.0) < 1e-9,
       f"by_state 應各 4h（原始加總）: {cell['by_state']}")
    ck(abs(sum(cell["by_state"].values()) - 8.0) < 1e-9 and sum(cell["by_state"].values()) > cell["stop_h"],
       "Σby_state(8h) > union stop_h(6h)＝重疊口徑")
    ck(cell["dominant"] in ("維修", "計畫保養") and cell["wo_nos"] == [71, 72], "dominant + wo_nos")
    ck(cell["ongoing"] is False, "無 ongoing")
    # 非重疊兩段：union == 和
    disj = [{"start": D(2026, 7, 4, 2, 0), "end": D(2026, 7, 4, 4, 0), "state": "維修",
             "stop_sec": 2 * 3600, "work_order_no": 8, "ongoing": True},
            {"start": D(2026, 7, 4, 6, 0), "end": D(2026, 7, 4, 9, 0), "state": "調機",
             "stop_sec": 3 * 3600, "work_order_no": 9, "ongoing": False}]
    cd = day_cell(disj)
    ck(abs(cd["stop_h"] - 5.0) < 1e-9, f"不重疊 union == 和（5h）: {cd['stop_h']}")
    ck(cd["ongoing"] is True and cd["dominant"] == "調機", "ongoing 旗傳遞 + dominant")
    ck(day_cell([])["dominant"] is None and day_cell([])["stop_h"] == 0, "空日格")
    # 缺時戳退加總（防禦）
    ck(abs(day_cell([{"state": "維修", "stop_sec": 3600, "work_order_no": 7}])["stop_h"] - 1.0) < 1e-9,
       "缺時戳段退加總")

    # ---- (G) build_row（工單導向；有工單 / 無工單 A%100 / now UP/DOWN）----
    dwR = day_windows(we, 5)
    entry = ("zc", "CAL#3", _C3)
    # 一張窗內完工單 + 一張現在 open 單
    wo1 = w8.normalize_wo({"work_order_no": 101, "asset_id": _C3, "work_type": "REACTIVE",
                           "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                           "closed_at": _to_utc_z(D(2026, 7, 4, 6, 0)), "status_history": []})
    # wo2＝現在 open REACTIVE 處理中（S12：DOWN·維修）
    wo2 = w8.normalize_wo({"work_order_no": 102, "asset_id": _C3, "work_type": "REACTIVE",
                           "opened_at": _to_utc_z(D(2026, 7, 6, 8, 0)),
                           "status_history": [
                               {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 6, 8, 0))},
                               {"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(D(2026, 7, 6, 9, 0))}]})
    row = build_row(entry, [wo1, wo2], dwR, we, "demo")
    ck(row["eid"] == _C3, "build_row eid")
    ck(set(row) == {"eid", "name", "area", "dup", "segments", "cells",
                    "total_stop_h", "now", "wos_win", "wos_win_inh", "avail_pct", "avail_delta_pp",
                    "erw", "equiv_ph",  # #39 該跑窗（換軸；`avail_pw_pct` 已刪除＝A3 不借屍還魂）
                    # §6.3-b 座係數四欄（`#110` (2)(4)）：**恆存在**、無座 ⇒ 皆 None
                    "seat_f", "seat_k", "seat_n", "seat_wos",
                    "dormant", "wo_mes_conflict", "dormant_mes_conflict", "dormant_dropped",
                    "mes_covered", "mes_measurable"},   # WP-D 兩顆旗（`vis`/`vis_reason` 由 board 掛）
       f"row 欄位: {set(row)}")
    ck("vis" not in row and "vis_reason" not in row,
       "★ WP-D 分層釘：摺疊決策屬 board 層（要 mes_available）、build_row 不得自己長 vis")
    ck("avail_pw_pct" not in row, "★ A3 負斷言：#36 舊鍵 `avail_pw_pct` 已刪除、不改名沿用")
    ck(row["dormant"] is None, "非長停列 dormant=None")
    ck("has_mes" not in row and "cover_note" not in row, "has_mes/cover_note 已移除")
    ck(row["avail_pct"] is not None and 0 <= row["avail_pct"] <= 100, "avail_pct 0..100")
    ck(set(row["now"]) == {"bucket", "verdict", "wo_no", "state", "mes_last_h",
                           "mes_age_h", "mes_any_last_h", "mes_last_designation",
                           "mes_desig_available", "stale"},
       f"now 欄位（多源）: {set(row['now'])}")
    # ★ 規則⑤（2026-09-07）：`mes_age_h` **每個分支都在**，且 DOWN 分支照實帶年齡——
    #   `mes_last_h` 仍是 None（它的語意是「升級 UP 標籤的訊號」，DOWN 沒走那條規則）。
    #   這根同時是負釘：哪天有人「順手」把 DOWN 的 `mes_last_h` 補上，`/equipment` 的
    #   Now 欄與 `/updown` 就會靜靜改口徑。
    _nb_dn = _now_block([wo1, wo2], we, mes_last_h=27.6, mes_available=True)
    _nb_up = _now_block([], we, mes_last_h=27.6, mes_available=True)
    _nb_na = _now_block([wo1, wo2], we, mes_last_h=27.6, mes_available=False)
    ck(_nb_dn["bucket"] == "down" and _nb_dn["mes_age_h"] == 27.6
       and _nb_dn["mes_last_h"] is None
       and _nb_up["mes_age_h"] == 27.6 and _nb_up["mes_last_h"] == 27.6
       and _nb_na["mes_age_h"] is None
       and all("mes_age_h" in _now_block(*_a, **_k)
               for _a, _k in ((([wo1, wo2], we), {}), (([], we), {}),
                              (([], we), {"dormant": {"eid": "EID-X"}}),
                              (([], we), {"mes_last_h": 0.5, "mes_available": True}))),
       f"規則⑤ mes_age_h：down={_nb_dn.get('mes_age_h')}／"
       f"down.mes_last_h={_nb_dn.get('mes_last_h')}／up={_nb_up.get('mes_age_h')}")
    # ★ ⑥ 子工位（2026-09-08；`SPEC-LANDING.md` §18.23 / C-LD40）——**加性**：
    #   不傳 ⇒ `now` 沒有這個鍵（上面「now 欄位」那一根就是它的負釘）；傳了 ⇒ 每個分支都帶。
    _su = [{"unit": "nest:1", "last_h": 0.4}, {"unit": "nest:2", "last_h": None}]
    ck("sub_units" not in _nb_dn and "sub_units" not in _nb_up,
       "⑥ 負釘：沒有子工位的機台，`now` 一個新鍵都不長")
    for _args, _kw in ((([wo1, wo2], we), {}), (([], we), {}),
                       (([], we), {"dormant": {"eid": "EID-X"}}),
                       (([], we), {"mes_last_h": 0.5, "mes_available": True})):
        _nb_su = _now_block(*_args, sub_units=_su, **_kw)
        ck(_nb_su["sub_units"] == _su,
           "⑥ 子工位每個分支都帶（含 DOWN／長停——那兩形一樣要畫得出座位）")
    ck("sub_units" not in _now_block([], we, sub_units=[]),
       "⑥ 空 list 與 None 等價＝整個鍵不存在（版面逐位元不變）")
    ck(_now_block([], we, sub_units=_su)["sub_units"][0] is not _su[0],
       "⑥ 逐筆淺拷貝：板不把呼叫端的 dict 交出去給下游就地改")
    _nb_rnd = _now_block([], we, mes_last_h=8.0, mes_any_last_h=1.0,
                         mes_last_designation="Engineering (R&D)", mes_desig_available=True,
                         mes_available=True)
    ck(_nb_rnd["mes_last_h"] == 8.0 and _nb_rnd["mes_any_last_h"] == 1.0
       and _nb_rnd["mes_last_designation"] == "Engineering (R&D)"
       and _nb_rnd["mes_desig_available"] is True,
       "生產級 age 與較新的 R&D any-segment evidence 同時帶入 Now（只揭露、不混算）")
    ck(row["now"]["bucket"] == "down" and row["now"]["verdict"] == "down"
       and row["now"]["state"] == "維修" and row["now"]["wo_no"] == 102,
       "S12：現在 open REACTIVE 處理中 → DOWN·維修")
    ck(all(s.get("countable") in (True, False) for s in row["segments"]),
       "segment 皆帶 countable 旗（additive）")
    ck(row["total_stop_h"] > 0 and all(s["source"] == "cmms_workorder" for s in row["segments"]),
       "有工單 → total_stop_h>0、segments 全 WO-backed")
    # ★S12：現在 open PM 只有 OPEN（未開始處理）→ 該列現在 UP（機台還在跑，非 DOWN）
    row_pm = build_row(("zc", "CAL#2", _C2), [wo_pm], dwR, we, "demo")
    ck(row_pm["now"]["bucket"] == "up" and row_pm["total_stop_h"] == 0,
       "S12：open PM 只 OPEN → 該列 UP、無停機（截圖案例）")
    # 無工單機台：stop_h 0、A% 100.0、now UP
    row0 = build_row(("zd", "Low Power Microscope", _EL), [], dwR, we, "demo")
    ck(row0["total_stop_h"] == 0 and abs(row0["avail_pct"] - 100.0) < 1e-9, "無工單 → A% 100.0")
    ck(row0["now"]["bucket"] == "up" and row0["segments"] == [], "無工單 → 現在 UP、無段")
    ck(all(row0["cells"][ds]["stop_h"] == 0 for ds in row0["cells"]), "無工單 → 每日 cell stop_h 0")

    # ---- (G2) ◌ 長停第三態（A11 過渡橋；SPEC-DORMANT-CAPACITY.md §3.2）----
    DZ = {"since": "2025-10-24", "reason_zh": "線外查修", "reason_en": "off-line investigation"}
    rd = build_row(("zc", "Gen2 CAL M2", _D2), [], dwR, we, "demo", dormant=DZ)
    ck(rd["now"]["bucket"] == "dormant" and rd["now"]["verdict"] == "dormant", "G2-1 長停列 bucket/verdict")
    ck(rd["avail_pct"] is None and rd["avail_delta_pp"] is None,
       "G2-2 長停列 A%／Δ 皆 None（顯「—」，絕不顯 100%＝替拔線機器背書）")
    ck(rd["dormant"] == DZ, "G2-3 長停列帶 dormant info（tooltip 用）")
    ck(rd["now"]["stale"] is False, "G2-4 長停列 stale 恆 False（⚠ 久靜對長停是噪音）")
    # ★ 事實 > 登記：直接指名長停 EID 的計停機工單 ⇒ DOWN 勝（有人真的在修它、可能正要復機）
    wo_on_dorm = w8.normalize_wo({
        "work_order_no": 777, "asset_id": _D2, "work_type": "REACTIVE",
        "opened_at": _to_utc_z(D(2026, 7, 6, 8, 0)),
        "status_history": [{"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 6, 8, 0))},
                           {"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(D(2026, 7, 6, 9, 0))}]})
    rdw = build_row(("zc", "Gen2 CAL M2", _D2), [wo_on_dorm], dwR, we, "demo", dormant=DZ)
    # ★ Now 欄**仍是 DOWN**——這一條刻意不跟著翻（`_now_block` 一個位元沒動）：
    #   Now 欄答的是「此刻這台機在什麼狀態」＝有人真的在修它，那是事實、該看得見；
    #   下面 G2-7 翻掉的是**停機統計**（時數／A%／heatmap）。兩者不是同一個問題。
    #   ⇒ 板上的完整故事＝「Now DOWN（有人在修）＋ heatmap 0（登記長停、不計）＋
    #      揭露行（Xh 已排除）」，「修一台停用機」不會因為新規則而隱形。
    ck(rdw["now"]["bucket"] == "down" and rdw["now"]["wo_no"] == 777,
       "G2-5 Now 欄仍顯 DOWN（此刻狀態≠停機統計；_now_block 未隨 2026-08-14 裁決改動）")
    ck(rdw["avail_pct"] is None, "G2-6 該列 A% 仍「—」（長停工位的妥善率沒有意義）")
    # ★★ 2026-08-14 Chris 裁決（bug#2 的第二個洞）：**直接開在長停工位上的單也不計停機**。
    #    舊斷言是「長停不遮蔽 heatmap：真有工單就照顯」——**已作廢**（那正是驗收抓到的錯）。
    ck(rdw["total_stop_h"] == 0,
       "G2-7 長停工位**直接**開的工單也不計停機（2026-08-14 翻面；舊「照顯」規則作廢）")
    ck(rdw["dormant_dropped"] and abs(rdw["dormant_dropped"]["sec"] - 6.5 * 3600) < 1e-6
       and rdw["dormant_dropped"]["wo_nos"] == [777],
       "G2-7b 被扣掉的 6.5h 與單號記在 dormant_dropped＝揭露行的帳（扣掉≠消失）")
    ck(all(c["stop_h"] == 0 for c in rdw["cells"].values()) and rdw["segments"] == [],
       "G2-7c heatmap 逐格皆 0、段也不留（不是只改總數）")
    # ★ 負對照（承重）：**沒有 dormant 事實 ⇒ 與翻面前逐位相同**
    rdw0 = build_row(("zc", "Gen2 CAL M2", _D2), [wo_on_dorm], dwR, we, "demo",
                     dormant=DZ, dormant_events=[])
    ck(rdw0["total_stop_h"] > 0 and rdw0["dormant_dropped"] is None,
       "G2-7d dormant_events=[] ⇒ 照舊計停機、零扣除帳（新規則零外溢）")
    ck(abs(rdw0["total_stop_h"] - 6.5) < 1e-6, "G2-7e 負對照的 6.5h ＝ 上面被扣掉的那 6.5h")
    # ★ 負對照要釘到**段本身**、不能只釘總數：只釘總數的話「無條件逐日切開再全部收下」
    #   這種改法照樣綠（drill 色帶會多出午夜切口、而數字看起來沒變）。
    _ds_in = wo_segments([wo_on_dorm], dwR[0][1], we)
    _ds_keep, _ds_drop = dormant_split_segments(_ds_in, _D2, [])
    ck(_ds_keep == _ds_in and _ds_drop == [],
       "G2-7g dormant_events=[] ⇒ dormant_split_segments 原樣回（不切日、不換形）")
    ck(all(a is b for a, b in zip(_ds_keep, _ds_in)),
       "G2-7h 且是**原物件**（零拷貝）＝結構上不可能偷偷改到段的任何一欄")
    ck(rdw0["segments"] == _ds_in, "G2-7i 負對照的 row segments 與未過濾前逐位元相同")
    # ★ 非長停 EID 走同一條碼路徑 ⇒ 逐位相同（證明過濾是有選擇性的、不是整片關掉）
    rnd_ = build_row(("zc", "CAL#2", _C2), [wo_on_dorm], dwR, we, "demo")
    rnd0 = build_row(("zc", "CAL#2", _C2), [wo_on_dorm], dwR, we, "demo",
                     dormant_events=[])
    ck(rnd_ == rnd0 and rnd_["total_stop_h"] > 0 and rnd_["dormant_dropped"] is None,
       "G2-7f 非長停 EID：傳不傳 dormant_events 逐位相同、照計停機")
    # ★ 旁證釘：傳與不傳 dormant_by_eid，**非長停列逐位相同**（新參數零外溢）
    bA = build_board(EQMT_CATALOG, wos_map0 := {_C3: [wo1, wo2]}, we, 5, "demo")
    bB = build_board(EQMT_CATALOG, wos_map0, we, 5, "demo",
                     dormant_by_eid={_D2: DZ, _D3: dict(DZ, since="2025-10-28")})
    rowsA = {r["eid"]: r for a in bA["areas"] for r in a["rows"]}
    rowsB = {r["eid"]: r for a in bB["areas"] for r in a["rows"]}
    ck(set(rowsA) == set(rowsB), "G2-8 列集合不變（長停不隱藏任何機台）")
    ck(all(rowsA[e] == rowsB[e] for e in rowsA if e not in (_D2, _D3)),
       "G2-9 非長停列傳與不傳 dormant_by_eid **逐位相同**")
    ck(rowsA[_D2]["now"]["bucket"] == "up" and rowsB[_D2]["now"]["bucket"] == "dormant",
       "G2-10 不傳＝舊行為（UP）、傳＝第三態")
    ck(bA["headline"]["n_dormant"] == 0 and bB["headline"]["n_dormant"] == 2,
       "G2-11 headline n_dormant")
    ck(bA["headline"]["n_down"] == bB["headline"]["n_down"], "G2-12 n_down 不受長停影響（本例）")
    ck(build_board(EQMT_CATALOG, wos_map0, we, 5, "demo", dormant_by_eid={}) == bA,
       "G2-13 空 dict 與 None 逐位相同")

    # ---- (G2b) 待辦 4：fsa 對齊起點（SPEC-LASTPROD-PUSH §11；2026-08-16）----
    #      規則本體釘在 w8 selftest；這裡釘**板級**：①揭露鍵 ②停機時數真的自 fsa 起算
    #      ③方向鐵閘擋下的不計入 N ④零 fsa 的板 fsa_aligned==[]（模板整列不渲染）。
    ck(bA["fsa_aligned"] == [], "G2b-1 零 fsa 的板 ⇒ fsa_aligned 空（版面逐位元不變）")
    _fD = D(2026, 7, 4)
    _fraw = _demo_phase_wo(80001, _C3, _fD + _dt.timedelta(hours=10),
                           _fD + _dt.timedelta(hours=12), "REACTIVE",
                           [("OPEN", None, _fD + _dt.timedelta(hours=10)),
                            ("IN_PROGRESS", None, _fD + _dt.timedelta(hours=10, minutes=30)),
                            ("COMPLETED", None, _fD + _dt.timedelta(hours=12))])
    _fraw["failure_started_at"] = _to_utc_z(_fD + _dt.timedelta(hours=8))
    _fwo = w8.normalize_wo(_fraw)
    bF = build_board(EQMT_CATALOG, {_C3: [_fwo]}, we, 5, "demo")
    ck(bF["fsa_aligned"] == [80001], "G2b-2 走對齊起點的單進揭露鍵（單號升冪）")
    _frow = {r["eid"]: r for a in bF["areas"] for r in a["rows"]}[_C3]
    ck(abs(_frow["cells"]["07-04"]["stop_h"] - 4.0) < 1e-6,
       f"G2b-3 停機自 fsa 08:00 起算＝4h（非開單 10:00 起的 2h）, "
       f"got {_frow['cells']['07-04']['stop_h']}")
    _graw = dict(_fraw, work_order_no=80002)
    _graw["failure_started_at"] = _to_utc_z(_fD + _dt.timedelta(hours=11))   # >= opened ⇒ 鐵閘
    bG = build_board(EQMT_CATALOG, {_C3: [w8.normalize_wo(_graw)]}, we, 5, "demo")
    ck(bG["fsa_aligned"] == [], "G2b-4 方向鐵閘擋下的單不計入 N（沒有走對齊，只是有個壞值）")
    _grow = {r["eid"]: r for a in bG["areas"] for r in a["rows"]}[_C3]
    ck(abs(_grow["cells"]["07-04"]["stop_h"] - 2.0) < 1e-6,
       "G2b-5 壞 fsa（>= opened_at）⇒ 停機照舊自開單起算（絕不照裁）")
    # ---- (G2c) NOTE-60：pra（對齊終點）鏡像——揭露鍵＋停機收口 ----
    ck(bA["pra_aligned"] == [] and bF["pra_aligned"] == [],
       "G2c-1 零 pra 的板 ⇒ pra_aligned 空（含只有 fsa 的板——兩鍵各自獨立）")
    _praw = {"work_order_no": 80003, "asset_id": _C3, "work_type": "REACTIVE",
             "opened_at": _to_utc_z(_fD + _dt.timedelta(hours=10)), "closed_at": None,
             "status": "OPEN", "hold_reason": None,
             "status_history": [_demo_h(None, "OPEN", None, _fD + _dt.timedelta(hours=10))],
             "production_resumed_at": _to_utc_z(_fD + _dt.timedelta(hours=13))}
    bP = build_board(EQMT_CATALOG, {_C3: [w8.normalize_wo(_praw)]}, we, 5, "demo")
    ck(bP["pra_aligned"] == [80003], "G2c-2 走對齊終點的單進揭露鍵（WO-36103 型：OPEN＋pra）")
    _prow = {r["eid"]: r for a in bP["areas"] for r in a["rows"]}[_C3]
    ck(abs(_prow["cells"]["07-04"]["stop_h"] - 3.0) < 1e-6,
       f"G2c-3 ★ OPEN 未結單但已對齊 ⇒ 停機收口在 pra 13:00＝3h（修 WO-36103 那 21h 的形；"
       f"未消費前這張單會 ongoing 吃滿窗）, got {_prow['cells']['07-04']['stop_h']}")
    _qraw = dict(_praw, work_order_no=80004)
    _qraw["production_resumed_at"] = _to_utc_z(_fD + _dt.timedelta(hours=9))   # <= opened ⇒ 鐵閘
    bQ = build_board(EQMT_CATALOG, {_C3: [w8.normalize_wo(_qraw)]}, we, 5, "demo")
    ck(bQ["pra_aligned"] == [],
       "G2c-4 方向鐵閘擋下的單不計入（沒有走對齊，只是有個壞值；停機照舊 ongoing）")
    # ★ 第二十五輪 §一-3（2026-08-21）：被擋下的要**計數揭露、不得靜默**
    ck(bQ["pra_blocked"] == [80004],
       "G2c-5 ★ 被方向鐵閘擋下的單進 pra_blocked（非零＝兩邊參考點不一致的裁決訊號）")
    ck(bP["pra_blocked"] == [] and bA["pra_blocked"] == [],
       "G2c-6 生效的 pra 與零 pra 的板 ⇒ pra_blocked 空（不得把 null 當成被擋、稀釋訊號）")
    # ★ 第二十五輪 §一-2：延遲開單（pra 落在 (fsa, opened_at]）**必須生效**——回歸釘
    _lraw = dict(_praw, work_order_no=80005)
    _lraw["failure_started_at"] = _to_utc_z(_fD + _dt.timedelta(hours=8))     # 故障 08:00
    _lraw["production_resumed_at"] = _to_utc_z(_fD + _dt.timedelta(hours=9, minutes=30))
    bL = build_board(EQMT_CATALOG, {_C3: [w8.normalize_wo(_lraw)]}, we, 5, "demo")
    ck(bL["pra_aligned"] == [80005] and bL["pra_blocked"] == [],
       "G2c-7 ★ 延遲開單型：pra 在 (fsa, opened] ⇒ 生效且不算被擋（下界＝fsa 的證明）")
    _lrow = {r["eid"]: r for a in bL["areas"] for r in a["rows"]}[_C3]
    ck(abs(_lrow["cells"]["07-04"]["stop_h"] - 1.5) < 1e-6,
       f"G2c-8 ★ 停機＝[fsa 08:00, pra 09:30]＝1.5h（下界寫回 opened_at 會變 ongoing 吃滿窗）, "
       f"got {_lrow['cells']['07-04']['stop_h']}")

    # ---- (G3) A10 母單展開到子工位（CONCEPT §19.3 A10「Board 側拍板全表」；引擎＝board_scope）----
    #      golden 劇本原樣（`_fixtures/wo_scope_events_golden.v1.json`）搬到 demo 錨窗內的 07-04。
    PA10 = _PA
    _ksA = list(wscope.PARENT_EXPANSION[PA10])
    KA, KB, KC = _ksA[:3]                                 # 私有樹＝24790 / 26841 / 26887（第四座 26820）
    KD = _ksA[3] if len(_ksA) > 3 else None               # WP-10d：公開樹的合成母機只有 3 座 ⇒ 沒有第四座
    _KALL = [k for k in (KA, KB, KC, KD) if k]
    _A10D = D(2026, 7, 4)

    def _a(h, m=0):
        return _A10D + _dt.timedelta(hours=h, minutes=m)

    a10_wo = w8.normalize_wo(_demo_phase_wo(
        49700, PA10, _a(8, 50), _a(15, 40), "REACTIVE",
        [("OPEN", None, _a(8, 50)), ("IN_PROGRESS", None, _a(10, 25)),
         ("CLOSED", None, _a(15, 40))]))
    a10_wo["scope_events"] = w8.normalize_scope_events([
        {"ts": _a(10, 30), "scope": [KA]},
        {"ts": _a(13, 0), "scope": [KA, KB]},
        {"ts": _a(13, 30), "scope": [KA]}])[0]
    _wsA = dwR[0][1]
    inh_off = wscope.expand_for_children({PA10: [a10_wo]}, _wsA, we, dormant_events=[])
    EXPH = {KA: 6 + 50 / 60.0, KB: 2 + 10 / 60.0, KC: 1 + 40 / 60.0}
    if KD:
        EXPH[KD] = 1 + 40 / 60.0
    for k, h in EXPH.items():
        rk = build_row(("zc", "CAL Gen2", k), [], dwR, we, "demo", inherited=inh_off.get(k))
        ck(abs(rk["total_stop_h"] - h) < 1e-9,
           f"G3-1 golden 期望帳 {k} 應 {h:.4f}h，實 {rk['total_stop_h']:.4f}h")
        ck(abs((rk["cells"]["07-04"]["stop_h"] or 0) - h) < 1e-9, f"G3-2 {k} 全額落 07-04 格")
        ck(all(s.get("inherited_from") == PA10 for s in rk["segments"]),
           f"G3-3 {k} 的段全部標出處＝母機 {PA10}")
        ck({s["state"] for s in rk["segments"]} == {"維修"},
           f"G3-4 {k} 繼承段沿用母單的 canonical 態（S12 階段標籤隨單）")
    # 出處與 kind：子1＝繼承段＋指名段；子3/4 全繼承（SPEC §5 的分辨基礎）
    rA = build_row(("zc", "CAL Gen2 Bay#1", KA), [], dwR, we, "demo", inherited=inh_off.get(KA))
    ck([s["inherited_kind"] for s in rA["segments"]] == ["inherited", "explicit"],
       f"G3-5 子1＝推論段→事實段: {[s['inherited_kind'] for s in rA['segments']]}")
    rC = build_row(("zc", "CAL Gen2 Bay#3", KC), [], dwR, we, "demo", inherited=inh_off.get(KC))
    ck([s["inherited_kind"] for s in rC["segments"]] == ["inherited"], "G3-6 子3 全是推論")
    # ★ 拍板⑦：母機**永不成列**（board 只走 EQMT_CATALOG；母機不在名冊）
    bI = build_board(EQMT_CATALOG, {}, we, 5, "demo", inherited_by_eid=inh_off)
    eids_I = {r["eid"] for a in bI["areas"] for r in a["rows"]}
    ck(not (eids_I & set(wscope.PARENT_EXPANSION)), "G3-7 三個母機 EID 永不成列（不加母機列）")
    ck(len(eids_I) == len(EQMT_CATALOG), "G3-8 展開不新增也不刪除任何列")
    # ★ 拍板⑨：母單繼承段與子工位**自身**工單重疊 ⇒ day_cell interval-union 不雙計
    own = w8.normalize_wo(_demo_migration_wo(49800, KA, _a(9, 0), _a(12, 0), "REACTIVE"))
    rov = build_row(("zc", "CAL Gen2 Bay#1", KA), [own], dwR, we, "demo",
                    inherited=inh_off.get(KA))
    covA = rov["cells"]["07-04"]
    ck(abs(covA["stop_h"] - EXPH[KA]) < 1e-9,
       f"G3-9 重疊不雙計：union 仍 6h50m，實 {covA['stop_h']:.4f}h")
    ck(abs(covA["by_state"]["維修"] - (EXPH[KA] + 3.0)) < 1e-9,
       "G3-10 by_state 是原始加總（>union）＝既有口徑不變、footer 已講明")
    ck(sorted(covA["wo_nos"]) == [49700, 49800], "G3-11 兩張單都列進 wo_nos（drill 看得到出處）")
    # ★ 拍板⑤：PM 母單全量繼承——每個子工位各記**完整區間**、不平均切分
    PM_P = _PB
    PMK = wscope.PARENT_EXPANSION[PM_P]
    pm_wo = w8.normalize_wo(_demo_phase_wo(
        49710, PM_P, _a(6, 0), _a(11, 0), "PM",
        [("OPEN", None, _a(6, 0)), ("IN_PROGRESS", None, _a(7, 0)),
         ("COMPLETED", None, _a(11, 0))]))
    pm_wo["scope_events"] = []
    inh_pm = wscope.expand_for_children({PM_P: [pm_wo]}, _wsA, we, dormant_events=[])
    for k in PMK:
        rp = build_row(("zc", "CLN Gen2 DI", k), [], dwR, we, "demo", inherited=inh_pm.get(k))
        ck(abs(rp["total_stop_h"] - 4.0) < 1e-9,
           f"G3-12 PM 全量繼承：{k} 各記完整 4h（非 1h）＝{rp['total_stop_h']:.4f}h")
        ck({s["state"] for s in rp["segments"]} == {"計畫保養"}, f"G3-13 {k} 態＝計畫保養")
    ck(abs(sum(build_row(("zc", "CLN", k), [], dwR, we, "demo",
                         inherited=inh_pm.get(k))["total_stop_h"] for k in PMK) - 4.0 * len(PMK)) < 1e-9,
       "G3-14 Σ 子工位＝16h ⇒ **子工位停機時數不可相加當機器停機時數**（配套鐵則，頁面/CSV 有講）")
    # PM 的 OPEN 前段（06:00–07:00）不算停機＝S12 規則隨單繼承、不另開特例
    rp0 = build_row(("zc", "CLN Gen2 DI", PMK[0]), [], dwR, we, "demo", inherited=inh_pm.get(PMK[0]))
    ck(rp0["segments"][0]["start"] == _a(7, 0),
       "G3-15 PM-OPEN 未開始處理那一小時不繼承（_phase_kind 規則隨單）")
    # ★ Now 欄：母單 ongoing ⇒ 子工位 DOWN（帶出處）；長停工位只有被指名才會 DOWN
    ong = w8.normalize_wo(_demo_phase_wo(
        49720, PA10, D(2026, 7, 6, 8, 0), D(2026, 7, 6, 8, 0), "REACTIVE",
        [("OPEN", None, D(2026, 7, 6, 8, 0)), ("IN_PROGRESS", None, D(2026, 7, 6, 9, 0))]))
    ong["closed_at"] = None
    ong["scope_events"] = []
    inh_now = wscope.expand_for_children({PA10: [ong]}, _wsA, we, dormant_events=[])
    for k in _KALL:
        nb = _now_block([], we, inherited=inh_now.get(k))
        ck(nb["bucket"] == "down" and nb["wo_no"] == 49720 and nb["state"] == "維修"
           and nb.get("inherited_from") == PA10,
           f"G3-16 母單 ongoing ⇒ {k} 現在 DOWN（帶出處 {PA10}）: {nb}")
    inh_now_dz = wscope.expand_for_children({PA10: [ong]}, _wsA, we)      # 預設＝橋常數
    ck(_now_block([], we, inherited=inh_now_dz.get(KA))["bucket"] == "down"
       and _now_block([], we, inherited=inh_now_dz.get(KB), dormant=DZ)["bucket"] == "dormant"
       and _now_block([], we, inherited=inh_now_dz.get(KC), dormant=DZ)["bucket"] == "dormant",
       "G3-17 長停工位不吃「母機停⇒子工位停」的推論（Now 維持 ◌ 長停）＝SPEC §5")
    ck(_now_block([wo_rx_ip], we, inherited=inh_now.get(KA))["wo_no"] == 57,
       "G3-18 自身計停機工單優先於母單繼承（更具體者勝）")
    ck("inherited_from" not in _now_block([], we), "G3-19 無繼承時 now 不長出 inherited_from 欄")
    # ★ 翻譯管線衛生（D9）：出處只掛結構欄，**絕不**混進任何自由文字欄；且繼承段除
    #   start/end/ongoing 外與母單原段逐欄相同（＝沒有偷改態、單號、countable 等承重欄）。
    base_segs = wo_segments([a10_wo], _wsA, we)
    for s in rA["segments"]:
        b0 = next(x for x in base_segs if x["work_order_no"] == s["work_order_no"])
        ck(set(s) == set(b0) | {"inherited_from", "inherited_kind"},
           f"G3-20 繼承段欄位＝母單段 + 兩個 additive 欄: {set(s) - set(b0)}")
        for kk in set(b0) - {"start", "end", "ongoing"}:
            ck(s[kk] == b0[kk], f"G3-21 繼承段承重欄與母單段相同: {kk}")
        for kk, vv in s.items():
            ck(not (isinstance(vv, str) and kk not in ("inherited_from", "inherited_kind")
                    and PA10 in vv),
               f"G3-22 母機 EID 不得混進自由文字欄 {kk}（board_translate D9 衛生）")
    # ★ 逐位釘：不傳／傳空 dict ＝ 板逐位元同加此參數前（新參數零外溢）
    bN = build_board(EQMT_CATALOG, wos_map0, we, 5, "demo")
    ck(build_board(EQMT_CATALOG, wos_map0, we, 5, "demo", inherited_by_eid=None) == bN,
       "G3-23 inherited_by_eid=None 逐位同舊")
    ck(build_board(EQMT_CATALOG, wos_map0, we, 5, "demo", inherited_by_eid={}) == bN,
       "G3-24 空 dict 逐位同舊")
    bE = build_board(EQMT_CATALOG, wos_map0, we, 5, "demo", inherited_by_eid=inh_off)
    rN = {r["eid"]: r for a in bN["areas"] for r in a["rows"]}
    rE = {r["eid"]: r for a in bE["areas"] for r in a["rows"]}
    ck(all(rN[e] == rE[e] for e in rN if e not in set(wscope.PARENT_EXPANSION[PA10])),
       "G3-25 未被展開牽連的 98 列逐位相同（展開零外溢）")
    ck(all(rE[e]["total_stop_h"] > 0 for e in wscope.PARENT_EXPANSION[PA10]),
       "G3-26 四個 CAL 子工位都拿到時數（母單今天在本頁完全不顯示＝A10 的硬錯已修）")

    # ---- (H) build_board（結構 / headline / fallback_rate / wo_status；新簽名無 mes_map）----
    wos_map = {_C3: [wo1, wo2]}
    board = build_board(EQMT_CATALOG, wos_map, we, 5, "demo", wo_status="not_configured")
    ck([a["key"] for a in board["areas"]] == ["za", "zb", "zc", "zd"], "areas 順序")
    ck([a["label"] for a in board["areas"]] == [lab for _k, lab in AREAS]
       and (_pub_tree or [a["label"] for a in board["areas"]] == ["Zone A", "Zone B", "Zone C", "Zone D"]), "areas 標籤")
    # ⚠ 列數釘一律**動態**（名冊會因退役／新機而變；寫死就是下一個會漂的事實）。
    #   唯一寫死的是上面那根「名冊有幾台」——那根才是真正的閘。
    ck(sum(len(a["rows"]) for a in board["areas"]) == len(EQMT_CATALOG),
       "board 的列數＝名冊台數")
    ck(set(board["headline"]) == {"yd_label", "yd_stop_h", "yd_by_state", "top_machine",
                                  "open_wo", "n_down", "n_stale", "n_with_downtime", "n_total",
                                  "n_dormant"},
       f"headline 欄位（多源＋窄橫列統計）: {set(board['headline'])}")
    ck(board["headline"]["n_total"] == len(EQMT_CATALOG), "headline n_total＝目錄去 dup 台數")
    ck(board["mes_available"] is False, "build_board 無 mes_by_eid → mes_available False")
    ck(board["headline"]["yd_label"] == day_windows(we, 5)[-2][0], "headline 昨日＝倒數第二欄")
    ck(board["headline"]["n_down"] == board["headline"]["open_wo"], "n_down == open_wo（工單導向）")
    ck(isinstance(board["fallback_rate"], tuple) and len(board["fallback_rate"]) == 2,
       "fallback_rate 應 (fb,tot) tuple")
    ck(board["wo_status"] == "not_configured" and board["truncated"] is False, "wo_status/truncated")
    ck(board["mode"] == "demo" and board["n_days"] == 5, "board mode/n_days")
    # ★ 2026-09-04 `headline_for_rows`（`?tag=` 過濾後重算 headline 的公開入口）：
    #   ① 餵全量列 ⇒ 與 `build_board` 自己算的那一份**逐位相同**（補 None 的 day_wins 不影響）；
    #   ② 餵子集 ⇒ `n_total` 跟著縮；③ 空列／空 labels ⇒ None（模板走空態、不留舊頭條）。
    _all_rows = [r for a in board["areas"] for r in a["rows"]]
    ck(headline_for_rows(_all_rows, board["day_labels"]) == board["headline"],
       "headline_for_rows 全量 ≡ build_board 的 headline（逐位相同）")
    _sub = [r for r in _all_rows if r["eid"] in {_C3, _NE}]
    ck((headline_for_rows(_sub, board["day_labels"]) or {}).get("n_total") == len(_sub)
       and headline_for_rows(_sub, board["day_labels"])["yd_label"]
       == board["headline"]["yd_label"],
       "headline_for_rows 子集：n_total 跟著縮、昨日欄不變")
    ck(headline_for_rows([], board["day_labels"]) is None
       and headline_for_rows(_all_rows, []) is None,
       "headline_for_rows 空列／空 labels ⇒ None（不留舊頭條）")
    ck(board["day_labels"] == [d[0] for d in day_windows(we, 5)], "day_labels")
    # n_days=1 → headline 退今日
    b1 = build_board(EQMT_CATALOG, {}, we, 1, "demo")
    ck(b1["headline"]["yd_label"] == day_windows(we, 1)[-1][0], "n_days=1 headline 退今日")
    ck(b1["headline"]["n_down"] == 0, "空 wos → n_down 0")
    # 2026-07-10 Chris 反饋 #1：legacy 調機/其他維護 永不進 legend_states（連動態補顯也撤），
    # 但該態仍在格子渲染（STC/SNAME 保留 legacy 灰、不藏資料）。
    wo_conv = w8.normalize_wo({"work_order_no": 80, "asset_id": _C3, "work_type": "CONVERSION",
                              "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                              "closed_at": _to_utc_z(D(2026, 7, 4, 5, 0)), "status_history": []})
    wo_maint = w8.normalize_wo({"work_order_no": 81, "asset_id": _C1, "work_type": "PART REQUEST",
                               "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                               "closed_at": _to_utc_z(D(2026, 7, 4, 5, 0)), "status_history": []})
    board_lg = build_board(EQMT_CATALOG, {_C3: [wo_conv], _C1: [wo_maint]},
                           we, 5, "demo", wo_status="live")
    ck("調機" not in board_lg["legend_states"] and "其他維護" not in board_lg["legend_states"],
       f"legacy 調機/其他維護 永不進 legend_states（即使窗內有值）: {board_lg['legend_states']}")
    ck(board_lg["legend_states"] == ["維修", "計畫保養"],
       f"僅 legacy 單時 legend_states 只剩常駐兩態: {board_lg['legend_states']}")
    _rconv = next(r for a in board_lg["areas"] for r in a["rows"] if r["eid"] == _C3)
    ck(any(c.get("dominant") == "調機" for c in _rconv["cells"].values()),
       "legacy 調機 態仍在格子渲染（dominant，不藏真資料）")

    # ---- (H2) assign_lanes（drill band 子軌道排程；貪婪區間分割、確定性）----
    def _seg(a, b, st="維修"):                                # (start_min, dur_min) 造 band seg
        return {"start_min": float(a), "dur_min": float(b - a), "state": st}
    _o, _n = assign_lanes([])                                # 空
    ck(_o == [] and _n == 0, "assign_lanes 空 → ([],0)")
    _o, _n = assign_lanes([_seg(0, 60), _seg(60, 120), _seg(120, 180)])
    ck(_n == 1 and [s["lane"] for s in _o] == [0, 0, 0], "無重疊三段 → 1 軌全 lane0")
    _o, _n = assign_lanes([_seg(0, 120), _seg(60, 180)])     # 兩段重疊
    ck(_n == 2 and {s["lane"] for s in _o} == {0, 1}, "兩段重疊 → 2 軌不同 lane")
    _o, _n = assign_lanes([_seg(0, 120), _seg(30, 150), _seg(60, 180)])  # 三向同時重疊
    ck(_n == 3 and sorted(s["lane"] for s in _o) == [0, 1, 2], "三向重疊 → 3 軌")
    _o, _n = assign_lanes([_seg(0, 60), _seg(60, 120)])      # 端點相接＝不重疊
    ck(_n == 1 and [s["lane"] for s in _o] == [0, 0], "端點相接（前 end==後 start）共軌")
    _in = [_seg(0, 120, "計畫保養"), _seg(60, 180, "維修"), _seg(200, 260, "調機")]
    r1 = [s["lane"] for s in assign_lanes(_in)[0]]
    r2 = [s["lane"] for s in assign_lanes(_in)[0]]
    ck(r1 == r2, "assign_lanes 確定性（同輸入同 lane 指派）")
    ck([s["state"] for s in assign_lanes(_in)[0]] == ["計畫保養", "維修", "調機"]
       and assign_lanes(_in)[1] == 2, "回原順序保留態、重疊段(0,1) 第三段落回 lane0＝2 軌")
    _o, _n = assign_lanes([{"start_min": 30.0, "dur_min": 0.0},   # 零長段守護
                           {"start_min": 0.0, "dur_min": 60.0}])
    ck(_n >= 1 and all("lane" in s for s in _o), "零長段不炸、皆帶 lane")
    _mixed = [_seg(0, 480, "生產"), _seg(120, 180, "維修"), _seg(120, 180, "計畫保養")]
    ck(assign_lanes(_mixed)[1] == 3, "所有態(含 faint 生產)一視同仁參與 packing")

    # ---- (I) demo_board（確定性 / 走真管線 / UTC-Z 轉換 / UP+DOWN / 故障停機不現）----
    db1 = demo_board(5)
    db2 = demo_board(5)
    sig = lambda b: json.dumps(b, sort_keys=True, default=str, ensure_ascii=False)
    ck(sig(db1) == sig(db2), "demo_board 同 n_days 兩呼應結構等價（確定性）")
    ck(db1["mode"] == "demo" and db1["wo_status"] == "demo", "demo_board mode/wo_status")
    # ⚠ `#125` (b)(c)（C-RW26）：座列也住 `areas[].rows`（母機正下方）⇒ **這根釘要數機台列**。
    #   把座算成台就是這條鐵則要擋的那個錯（座是產能單位、不是可用度單位）。
    ck(sum(1 for a in db1["areas"] for r in a["rows"] if not r.get("is_seat"))
       == len(EQMT_CATALOG), "demo_board 機台列數＝名冊台數")
    all_segs = [s for a in db1["areas"] for r in a["rows"] for s in r["segments"]]
    wo_segs = [s for s in all_segs if s.get("source") == "cmms_workorder"]
    ck(len(wo_segs) > 0, "demo_board 應有 cmms_workorder 分類段（證明走真管線）")
    ck(all(s["state"] != "故障停機" for s in all_segs), "demo segments 不含故障停機（裁決②）")
    # 2026-07-10 Chris：demo 只合成 PM/REACTIVE（+canonical hold 拆色）→ 無 legacy 調機/其他維護段
    ck(all(s["state"] not in ("調機", "其他維護", w8.BUCKET_STATE) for s in all_segs),
       f"demo segments 不含 legacy 調機/其他維護（只合成 PM/REACTIVE）: "
       f"{set(s['state'] for s in all_segs)}")
    # T3 showcase：EQM_SHOWCASE_EID 錨日有兩筆時間重疊工單 → assign_lanes 該日 → 2 軌（維修∥計畫保養）
    _sc_row = next(r for a in db1["areas"] for r in a["rows"] if r["eid"] == EQM_SHOWCASE_EID)
    _anchor_ds = day_windows(_DEMO_ANCHOR, 5)[-1][0]
    _sc_day = [s for s in _sc_row["segments"]
               if s.get("start") and s["start"].strftime("%m-%d") == _anchor_ds
               and s.get("work_order_no") in (49500, 49501)]
    ck(len(_sc_day) == 2 and {s["state"] for s in _sc_day} == {"維修", "計畫保養"},
       f"showcase 錨日應有維修+計畫保養兩重疊段: {[(s['state'], s.get('work_order_no')) for s in _sc_day]}")
    _lane_in = [{"start_min": (s["start"].hour * 60 + s["start"].minute),
                 "dur_min": (s["end"] - s["start"]).total_seconds() / 60.0,
                 "state": s["state"]} for s in _sc_day]
    ck(assign_lanes(_lane_in)[1] == 2, "showcase 兩重疊段經 assign_lanes → 2 軌（drill 兩色皆可見）")
    # UTC-Z 轉換：段時戳＝naive、落在合理台北時刻（窗內）
    ws_anchor = day_windows(_DEMO_ANCHOR, 5)[0][1]
    ck(all(s["start"].tzinfo is None and ws_anchor <= s["start"] <= _DEMO_ANCHOR
           for s in wo_segs), "WO 段應 naive 台北時戳、落窗內（UTC-Z 已轉）")
    # 直測 UTC-Z round-trip：_demo_wo 建 09:00 台北 → 編碼 01:00Z → normalize 還原 09:00
    raw_wo = _demo_wo(1, "EID-X", D(2026, 7, 1, 9, 0), D(2026, 7, 1, 17, 0), random.Random(1))
    ck(raw_wo["opened_at"] == "2026-07-01T01:00:00Z", "demo WO opened_at 應 UTC-Z（09:00台北→01:00Z）")
    ck(w8.normalize_wo(raw_wo)["opened_at"] == D(2026, 7, 1, 9, 0),
       "UTC-Z 過 normalize_wo 應還原 09:00 台北（+8）")
    # demo 現在徽章＝二桶(up/down)三 verdict（2026-07-10 拍板：MES 是加分不是門檻、無 UNKNOWN）。
    # demo 合成 mes → db1.mes_available True；三 verdict + ⚠ 久靜皆應現形；DOWN 態為 canonical 維護。
    now_buckets = {r["now"]["bucket"] for a in db1["areas"] for r in a["rows"]}
    now_verdicts = {r["now"]["verdict"] for a in db1["areas"] for r in a["rows"]}
    now_states = {r["now"]["state"] for a in db1["areas"] for r in a["rows"] if r["now"]["state"]}
    ck(db1["mes_available"] is True, "demo_board 合成 mes → mes_available True")
    # 2026-08-09（A11 過渡橋）：新增第三桶 dormant＝**登記的事實**（橋常數兩筆 CAL 模組）。
    # ⚠ 這不是 UNKNOWN 復辟——UNKNOWN 是「MES 沉默 ⇒ 猜」（滿屏灰、已撤）；dormant 有登記者、
    # 生效日與原因，且恆為個位數台。下面的「≤3 台」釘就是守這條界線的（橋長出第三筆以上＝
    # SPEC §7 反模式，該問「A11 怎麼還沒排上」而不是放寬這個數字）。
    ck(now_buckets == {"up", "down", "dormant"},
       f"demo 現在徽章應為 up/down/dormant 三桶（UNKNOWN 仍已撤）: {now_buckets}")
    ck(now_verdicts == {"down", "up_producing", "up_idle", "dormant"},
       f"demo 應現形四 verdict（down/up_producing/up_idle/dormant）: {now_verdicts}")
    # 2026-07-10：demo 只合成 PM/REACTIVE → DOWN now 態為 維修/等料/等外包（無 legacy 調機/其他維護）
    ck(now_states & {"維修", "等料(停機)", "等外包(停機)"}
       and "故障停機" not in now_states
       and not (now_states & {"調機", "其他維護", w8.BUCKET_STATE}),
       f"demo DOWN 態應為 canonical 維護/hold、無故障停機、無 legacy 調機·其他維護: {now_states}")
    # demo 長停（A11 過渡橋）：恰為橋常數那兩台、每台都答得出「自哪天/為什麼」、A% 顯「—」
    d_rows = [r for a in db1["areas"] for r in a["rows"] if r["now"]["bucket"] == "dormant"]
    ck([r["eid"] for r in d_rows] == [_D2, _D3], "demo 長停恰為橋的兩台 CAL 模組")
    ck(all(r["dormant"] and r["dormant"]["since"] and r["avail_pct"] is None for r in d_rows),
       "demo 長停列帶 since 且 A% 顯「—」")
    # ★★ 2026-08-14（Chris 裁決）**再改寫**：登記長停的工位不得再計任何 downtime，explicit 也扣
    #    （`SPEC-DORMANT-CAPACITY.md` §5 翻面）⇒ 兩台長停列零段。
    # ★★★ 2026-08-18（Chris 裁決，WO 36045 實例）**帳面三改寫**：母單遺傳（含 scope 指名）
    #    被扣的段**照扣但不再記帳**——結構必然不是異常，每張母機工單都產一行「已排除」＝噪音。
    #    ⇒ demo 的 49700 全是母單遺傳 ⇒ 兩台 `dormant_dropped` 皆 None、板級 dormant_drops 空、
    #    揭露行整列不渲染。**帳只剩「直接開在長停 EID 上的單」**（G2-7b 那條腿、仍有效）。
    m2_row = next(r for r in d_rows if r["eid"] == _D2)
    m3_row = next(r for r in d_rows if r["eid"] == _D3)
    ck(not m2_row["segments"] and m2_row["total_stop_h"] == 0,
       "demo 長停列 26841 零段（explicit 30min 也扣＝2026-08-14 翻面；扣的行為本身不變）")
    ck(m2_row["dormant_dropped"] is None,
       f"demo 26841 母單遺傳被扣＝**不記帳**（2026-08-18：結構必然、不是異常）: "
       f"{m2_row['dormant_dropped']}")
    ck(not m3_row["segments"] and m3_row["total_stop_h"] == 0,
       "demo 長停列 26887 仍零段（只被推論牽連 ⇒ 跳過；不讓拔線機器因母機停而掉可用度）")
    ck(m3_row["dormant_dropped"] is None,
       "demo 26887 同樣不記帳（遺傳靜默＝兩台一致、不是漏了誰）")
    # 板級彙總：demo 全是遺傳 ⇒ 空 list ⇒ 模板整列不渲染（版面回到加揭露行前）
    ck(db1["dormant_drops"] == [],
       f"demo board dormant_drops 空（遺傳不記帳）: {db1['dormant_drops']}")
    ck(all(not any(s.get("inherited_from") is None for s in r["segments"]) for r in d_rows),
       "demo 長停列不撒自身工單（第三態確定性可見；live 不做此排除）")
    ck(db1["headline"]["n_dormant"] == 2, "demo headline n_dormant＝2")
    # ★ 上限釘（SPEC §7 反模式）：橋是橋、不是家。長到 4 台以上該問「A11 怎麼還沒排上」，
    #   而不是把這個數字改大——這條紅了就是提醒去看 SPEC §8 順序 5（換源、橋退役）。
    ck(db1["headline"]["n_dormant"] <= 3, "長停台數應是個位數少數（橋長太多＝該催 A11，勿放寬）")
    ck(all(r["now"]["stale"] is False for r in d_rows), "長停列不掛 ⚠ 久靜")
    ck(db1["headline"]["n_down"] > 0 and db1["headline"]["n_stale"] > 0,
       "demo headline 應有 DOWN 機台 + ⚠ 久靜機台")
    ck(db1["headline"]["n_stale"] < db1["headline"]["n_total"] * 0.3,
       "⚠ 久靜應是少數（否則失去『浮出異常』的意義）")
    ck(all(r["now"]["bucket"] == "up" for a in db1["areas"] for r in a["rows"]
           if r["now"]["stale"]), "⚠ 久靜機台狀態必須仍是 UP（提示≠降級成 DOWN）")
    ck(db1["quiet_h"] == QUIET_H, "board 應回傳 quiet_h（頁面文案插值）")
    # 多源 _now_block 直測（規則②③、WO 優先、門檻邊界、⚠ 久靜）
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=1.0, mes_available=True)["verdict"] == "up_producing",
       "MES age 1.0h ≤ 門檻 → UP·生產中")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=ud.MES_RECENT_H, mes_available=True)["verdict"]
       == "up_producing", "MES age == 門檻 → UP·生產中（邊界）")
    _idle = _now_block([], _DEMO_ANCHOR, mes_last_h=5.0, mes_available=True)
    ck(_idle["bucket"] == "up" and _idle["verdict"] == "up_idle" and _idle["stale"] is False,
       "MES age 5.0h > 生產門檻但 < 久靜門檻 → UP·待命、無 ⚠")
    _nosig = _now_block([], _DEMO_ANCHOR, mes_last_h=None, mes_available=True)
    ck(_nosig["bucket"] == "up" and _nosig["verdict"] == "up_idle" and _nosig["stale"] is False,
       "MES 窗內無紀錄(None) → UP·待命、不掛 ⚠（分不出不寫 MES vs 超窗＝不猜）")
    _fail = _now_block([], _DEMO_ANCHOR, mes_available=False)
    ck(_fail["bucket"] == "up" and _fail["verdict"] == "up_idle" and _fail["mes_last_h"] is None,
       "mes_available False（探測失敗）→ 退工單二值 UP、age 不外洩")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=200.0, mes_available=False)["mes_last_h"] is None,
       "mes_available False 時即使誤傳 age 也不採用（單一真相＝旗標）")
    # ⚠ 久靜門檻（含邊界：> 才 stale、== 不 stale）
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=QUIET_H + 0.1, mes_available=True)["stale"] is True,
       f"MES age > {QUIET_H}h → stale ⚠")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=QUIET_H, mes_available=True)["stale"] is False,
       f"MES age == {QUIET_H}h → 不 stale（嚴格大於才掛 ⚠）")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=100.0, mes_available=True)["bucket"] == "up",
       "⚠ 久靜仍是 UP 桶（絕不因久靜推 DOWN）")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=100.0, mes_available=True, quiet_h=200.0)["stale"]
       is False, "quiet_h 可調（100h < 200h 門檻 → 不掛 ⚠）")
    # ★ R45（Chris 2026-09-29）：門檻 3 天→7 天（牆鐘時數），備援探測窗 7→10 天。
    ck(QUIET_DAYS == 7 and QUIET_H == 168.0,
       f"R45 久靜門檻＝7 天＝168h（got QUIET_DAYS={QUIET_DAYS} QUIET_H={QUIET_H}）")
    ck(QUIET_H == QUIET_DAYS * 24.0, "QUIET_H 由 QUIET_DAYS 換算（單一出處、不是兩份各自寫死的數字）")
    ck(MES_PROBE_DAYS == 10 and MES_PROBE_DAYS * 24.0 > QUIET_H,
       f"R45 不變量：備援探測窗 {MES_PROBE_DAYS}d 必須 > 久靜門檻 {QUIET_H}h"
       "（否則久靜機台掉出探測目錄、⚠ 永不觸發）")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=167.0, mes_available=True)["stale"] is False
       and _now_block([], _DEMO_ANCHOR, mes_last_h=169.0, mes_available=True)["stale"] is True,
       "R45 刀鋒兩側：167h 不 stale、169h stale")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=4.8 * 24, mes_available=True)["stale"] is False
       and _now_block([], _DEMO_ANCHOR, mes_last_h=73.0, mes_available=True)["stale"] is False,
       "R45 情境：四天連休後最後生產 4.8 天前（115.2h）與舊刀鋒 73h 都不再掛 ⚠")
    _ip = w8.normalize_wo(_demo_open_wo(9, "EID-X", _DEMO_ANCHOR - _dt.timedelta(hours=8),
                                        _DEMO_ANCHOR, "reactive_ip"))
    ck(_now_block([_ip], _DEMO_ANCHOR, mes_last_h=0.1, mes_available=True)["bucket"] == "down",
       "有 count 工單 → DOWN（工單優先於 MES 近期）")
    # 「現在」欄顯當前階段（2026-07-10）：此刻卡等料/等外包 → 顯 hold 態；OTHER → 母態
    _hp = w8.normalize_wo(_demo_open_wo(11, "EID-X", _DEMO_ANCHOR - _dt.timedelta(hours=8),
                                        _DEMO_ANCHOR, "reactive_parts"))
    ck(_now_block([_hp], _DEMO_ANCHOR)["state"] == HOLD_PARTS_STATE,
       "現在卡 WAITING_PARTS → DOWN·等料(停機)（非母態維修）")
    _hv = w8.normalize_wo(_demo_open_wo(12, "EID-X", _DEMO_ANCHOR - _dt.timedelta(hours=8),
                                        _DEMO_ANCHOR, "reactive_vendor"))
    ck(_now_block([_hv], _DEMO_ANCHOR)["state"] == HOLD_VENDOR_STATE,
       "現在卡 WAITING_VENDOR → DOWN·等外包(停機)")
    _ho = w8.normalize_wo(_demo_open_wo(13, "EID-X", _DEMO_ANCHOR - _dt.timedelta(hours=8),
                                        _DEMO_ANCHOR, "reactive_other"))
    ck(_now_block([_ho], _DEMO_ANCHOR)["state"] == "維修",
       "現在卡 OTHER hold → 維持母態維修（保守）")
    ck(_now_block([_ip], _DEMO_ANCHOR)["state"] == "維修",
       "IN_PROGRESS → 母態維修（hold 顯示只在 ON_HOLD）")
    ck(_now_block([_ip], _DEMO_ANCHOR, mes_last_h=500.0, mes_available=True)["stale"] is False,
       "DOWN 機台不掛 ⚠（已知在修、無須『問一句』）")
    # build_board 帶 mes_by_eid → mes_available True、缺 eid → UP·待命（不再 UNKNOWN）
    _bb = build_board(EQMT_CATALOG, {}, _DEMO_ANCHOR, 5, "live", wo_status="live",
                      mes_by_eid={_C3: 0.5})
    ck(_bb["mes_available"] is True, "build_board 帶 mes_by_eid → mes_available True")
    _r884 = next(r for a in _bb["areas"] for r in a["rows"] if r["eid"] == _C3)
    ck(_r884["now"]["verdict"] == "up_producing", "mes_by_eid 有值(0.5h) → UP·生產中")
    _rmiss = next(r for a in _bb["areas"] for r in a["rows"] if r["eid"] != _C3)
    ck(_rmiss["now"]["bucket"] == "up" and _rmiss["now"]["verdict"] == "up_idle"
       and _rmiss["now"]["stale"] is False,
       "mes_by_eid 缺此 eid → UP·待命、不掛 ⚠（不猜）")
    ck(_bb["headline"]["n_stale"] == 0, "無工單 + 無久靜 age → headline n_stale 0")
    # ★ 三形定案（2026-08-17 Chris 拍板；規則④）：mes_covered ∧ 值 None ＝ nosig（中性、不 ⚠）
    _ns = _now_block([], _DEMO_ANCHOR, mes_last_h=None, mes_available=True, mes_covered=True)
    ck(_ns["verdict"] == "up_idle" and _ns["mes_nosig"] is True and _ns["stale"] is False,
       "covered ∧ age None → nosig True（中性小字）、絕不掛 ⚠")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=None, mes_available=True)["mes_nosig"] is False,
       "未涵蓋（探測 fallback 缺 eid 形）→ nosig False（無小字＝舊形逐位不變）")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=5.0, mes_available=True,
                  mes_covered=True)["mes_nosig"] is False,
       "covered 但有 age → nosig False（顯 age 小字）")
    ck(_now_block([], _DEMO_ANCHOR, mes_last_h=None, mes_available=False,
                  mes_covered=True)["mes_nosig"] is False,
       "MES 證據整體不可用 → nosig False（退 caveat、不宣稱『無生產』）")
    ck("mes_nosig" not in _now_block([_ip], _DEMO_ANCHOR, mes_available=True, mes_covered=True),
       "DOWN 分支不長 mes_nosig（additive key 只住 up_idle）")
    _bb2 = build_board(EQMT_CATALOG, {}, _DEMO_ANCHOR, 5, "live", wo_status="live",
                       mes_by_eid={_C3: 0.5, _E0: None}, mes_lookback_d=30)
    ck(_bb2["mes_lookback_d"] == 30, "build_board mes_lookback_d passthrough")
    _rn = next(r for a in _bb2["areas"] for r in a["rows"] if r["eid"] == _E0)
    ck(_rn["now"]["verdict"] == "up_idle" and _rn["now"]["mes_nosig"] is True,
       "build_board 值 None → 該列 nosig True（整窗掃過、無生產）")
    _rm = next(r for a in _bb2["areas"] for r in a["rows"]
               if r["eid"] not in (_C3, _E0))
    ck(_rm["now"].get("mes_nosig") is False, "缺 eid → nosig False（涵蓋語意由 key 在場表達）")

    # ★ 子工位（2026-09-08；`SPEC-LANDING.md` §18.23 / C-LD40）────────────────────
    _sub_eids = tuple(e for e in _wcap.machines_with_units()
                      if e not in _wcap.PARENT_EXPANSION)
    # ⚠ 子工位機台數減少（2026-09-08 深夜；C-LD46／`SPEC-LANDING.md` §18.30.4）：
    #   AOI 那幾台的 nest 是載盤 slot 追蹤代號、不是物理子工位 ⇒ 退出名冊。
    ck(len(_sub_eids) == len(_wcap.NEST_EAV_UNITS) + len(_wcap.COTAG_CHILD_UNITS)
       and (_pub_tree or len(_sub_eids) == _pp.PINS["eqmeeting.subunit_machines_n"]),
       "子工位機台數＝形① ＋ 形②（形③ 不畫格；私有樹釘值）")
    _bs = build_board(EQMT_CATALOG, {}, _DEMO_ANCHOR, 5, "live", wo_status="live",
                      mes_by_eid={}, sub_unit_ages_by_eid={_CTP: {_CT1: 0.3}})
    _by_eid = {r["eid"]: r for a in _bs["areas"] for r in a["rows"]}
    ck(_by_eid[_CTP]["now"]["sub_units"]
       == [{"unit": _u, "last_h": 0.3 if _u == _CT1 else None} for _u in _wcap.installed_units(_CTP)],
       "★ 順序＝`installed_units` 的宣告順序；沒觀測到的那一座帶 None（不是缺席、不是 0）")
    ck(_by_eid[_E0]["now"]["sub_units"]
       == [{"unit": _u, "last_h": None} for _u in _wcap.installed_units(_E0)],
       "★ 名冊有 k 座、這一輪一座資料都沒有 ⇒ 仍帶 k 筆 None（畫 k 個空心格）"
       "——不帶等於說『這台沒有子工位』")
    ck(all("sub_units" not in _by_eid[e]["now"]
           for e in _by_eid if e not in _sub_eids),
       "★ 負釘：其餘無子工位機台的 `now` 一個新鍵都沒長（版面逐位元不變）")
    ck(all(k not in _by_eid for k in _wcap.PARENT_EXPANSION),
       "形③ 母機本來就不在名冊（它的子槽位各自是列）")
    _bs0 = build_board(EQMT_CATALOG, {}, _DEMO_ANCHOR, 5, "live", wo_status="live",
                       mes_by_eid={})
    ck(all("sub_units" not in r["now"] or r["eid"] in _sub_eids
           for a in _bs0["areas"] for r in a["rows"]),
       "不傳 `sub_unit_ages_by_eid` ⇒ 子工位機台仍帶 k 筆 None、其餘零新鍵（名冊是名冊、觀測是觀測）")
    _dsu = demo_sub_unit_ages()
    ck(set(_dsu) == set(_sub_eids), "demo 合成涵蓋全部子工位機台、且不碰形③")
    ck(all(set(_dsu[e]) <= set(_wcap.installed_units(e)) for e in _dsu),
       "demo 合成的座位 id 全部來自名冊（不憑空生座）")
    ck(_dsu == demo_sub_unit_ages(), "demo 合成是確定性的（同一台每次同一組顏色）")
    for _e in _sub_eids:
        _units = _wcap.installed_units(_e)
        _kinds = {("run" if (_dsu[_e].get(u) is not None and _dsu[_e][u] <= ud.MES_RECENT_H)
                   else "old" if _dsu[_e].get(u) is not None else "unknown") for u in _units}
        ck(len(_kinds) >= 2, "demo：%s 的座位不得整台同色（看不出它有幾格）" % _e)
    ck(_bb["mes_lookback_d"] is None,
       "未傳 mes_lookback_d → None（探測 fallback ⇒ 模板 nosig 小字不渲染）")
    ck(any(r["now"].get("mes_nosig") for a in db1["areas"] for r in a["rows"]),
       "demo 應含 nosig 台（三形展示：12% 值 None）")
    ck(db1["mes_lookback_d"] == 30, "demo nosig 窗寬＝30（比照 live 主源）")
    # ★demo 演示拆色新口徑：兩 uptime 子類段 + 兩計停機 hold 態 + countable 旗 + legend
    ck(db1["has_uptime_hold"] is True, "demo 應含 uptime-hold 段（reactive_hold/reactive_test pattern）")
    ck(any(s["state"] == UPTIME_WAIT_STATE and s["countable"] is False for s in all_segs),
       "demo all_segs 應含 暫停·等機台空檔 且 countable False 段")
    ck(any(s["state"] == UPTIME_TEST_STATE and s["countable"] is False for s in all_segs),
       "demo all_segs 應含 暫停·試跑 且 countable False 段（reactive_test pattern）")
    ck(db1["legend_uptime"] == [UPTIME_WAIT_STATE, UPTIME_TEST_STATE],
       f"demo legend_uptime 應含兩 uptime 子類（canonical 序）: {db1['legend_uptime']}")
    ck(any(s["state"] == HOLD_PARTS_STATE and s["countable"] is True for s in all_segs),
       "demo all_segs 應含 等料(停機) countable 段（reactive_parts pattern）")
    ck(any(s["state"] == HOLD_VENDOR_STATE and s["countable"] is True for s in all_segs),
       "demo all_segs 應含 等外包(停機) countable 段（reactive_vendor pattern）")
    ck(HOLD_PARTS_STATE in db1["legend_states"] and HOLD_VENDOR_STATE in db1["legend_states"],
       f"demo legend_states 應含 等料/等外包（有段才顯）: {db1['legend_states']}")
    ck(all(s.get("countable") in (True, False) for s in all_segs), "demo 段皆帶 countable 旗")
    ck("維修" in db1["legend_states"] and "計畫保養" in db1["legend_states"],
       "legend_states 維修/計畫保養 常駐")
    # 等料/等外包 也進 headline by_state 或 cell by_state（countable→分態加總）
    ck(any(HOLD_PARTS_STATE in (c.get("by_state") or {})
           for a in db1["areas"] for r in a["rows"] for c in r["cells"].values()),
       "demo 某 cell by_state 應含 等料(停機)（countable 進分態）")
    # 直測 _demo_open_wo pattern 現在徽章（pm_open UP／reactive_ip DOWN／reactive_hold UP）
    _wn = _DEMO_ANCHOR
    _op = _wn - _dt.timedelta(hours=8)
    ck(_now_block([w8.normalize_wo(_demo_open_wo(1, "EID-X", _op, _wn, "pm_open"))], _wn)["bucket"]
       == "up", "demo pm_open（PM 未開始處理）→ 現在 UP")
    ck(_now_block([w8.normalize_wo(_demo_open_wo(2, "EID-X", _op, _wn, "reactive_ip"))], _wn)["bucket"]
       == "down", "demo reactive_ip（處理中）→ 現在 DOWN")
    ck(_now_block([w8.normalize_wo(_demo_open_wo(3, "EID-X", _op, _wn, "reactive_hold"))], _wn)["bucket"]
       == "up", "demo reactive_hold（等機台空檔）→ 現在 UP")
    ck(_now_block([w8.normalize_wo(_demo_open_wo(31, "EID-X", _op, _wn, "reactive_test"))], _wn)["bucket"]
       == "up", "demo reactive_test（試跑觀察）→ 現在 UP（機台可生產）")
    _seg_test = wo_segments([w8.normalize_wo(_demo_open_wo(32, "EID-X", _op, _wn, "reactive_test"))],
                            day_windows(_wn, 5)[0][1], _wn)
    ck(any(s["state"] == UPTIME_TEST_STATE and not s["countable"] for s in _seg_test),
       f"demo reactive_test → 暫停·試跑 uptime 段: {[s['state'] for s in _seg_test]}")
    # ★canonical 計停機 hold pattern（等料/等外包/其他）→ 現在 DOWN + wo_current 對版。
    #   state 顯當前階段（2026-07-10）：等料/等外包 顯 hold 態、OTHER 保守留母態維修。
    _hold_now = {"WAITING_PARTS": HOLD_PARTS_STATE, "WAITING_VENDOR": HOLD_VENDOR_STATE,
                 "OTHER": "維修"}
    for _pat, _hr in _DEMO_HOLD_DOWN.items():
        _nw = w8.normalize_wo(_demo_open_wo(4, "EID-X", _op, _wn, _pat))
        ck(_now_block([_nw], _wn)["bucket"] == "down"
           and _now_block([_nw], _wn)["state"] == _hold_now[_hr],
           f"demo {_pat}（{_hr} 計停機）→ 現在 DOWN·{_hold_now[_hr]}")
        cs, chr_ = wo_current(_nw)
        ck(cs == "ON_HOLD" and chr_ == _hr,
           f"wo_current({_pat}) 應回 (ON_HOLD, {_hr})：{(cs, chr_)}")
    ck(set(_DEMO_HOLD_DOWN.values()) == {"WAITING_PARTS", "WAITING_VENDOR", "OTHER"},
       "計停機 hold demo 涵蓋 canonical 3 值")
    # wo_current：完工單歷史 hold 不算『現在暫停』（hold_reason None）；遷移單→(None,None)
    _wo_done = w8.normalize_wo({"work_order_no": 5, "asset_id": "EID-X", "work_type": "REACTIVE",
                               "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                               "closed_at": _to_utc_z(D(2026, 7, 4, 6, 0)),
                               "status_history": [
                                   {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 7, 4, 2, 0))},
                                   {"to_status": "ON_HOLD", "hold_reason": "WAITING_PARTS",
                                    "changed_at": _to_utc_z(D(2026, 7, 4, 3, 0))},
                                   {"to_status": "COMPLETED", "changed_at": _to_utc_z(D(2026, 7, 4, 6, 0))}]})
    ck(wo_current(_wo_done) == ("COMPLETED", None),
       "wo_current：末態 COMPLETED → hold_reason None（歷史 hold 非現在暫停）")
    ck(wo_current(w8.normalize_wo({
        "work_order_no": 6, "asset_id": "EID-X", "work_type": "PM",
        "opened_at": _to_utc_z(D(2026, 7, 3, 1, 0)),
        "closed_at": _to_utc_z(D(2026, 7, 3, 5, 0)), "status_history": []})) == (None, None),
       "wo_current：遷移單（空 history）→ (None, None)")
    # build_row 的 wos_win 帶 status/hold_reason（drill 卡 demo 填充來源）
    _rowh = build_row(("zc", "CAL#3", _C3),
                      [w8.normalize_wo(_demo_open_wo(7, _C3, _op, _wn, "reactive_parts"))],
                      day_windows(_wn, 5), _wn, "demo")
    _wwin = _rowh["wos_win"][0]
    ck(_wwin.get("status") == "ON_HOLD" and _wwin.get("hold_reason") == "WAITING_PARTS",
       f"wos_win 應帶現在 status/hold_reason（drill 卡填充源）: {_wwin}")
    # demo_board 回退率可見（tuple、tot>0＝有工單）
    ck(db1["fallback_rate"][1] > 0, "demo_board fallback_rate 應有樣本")
    # demo_brief / demo_diag 確定性（T1：故障簡述＋工作日誌落 segment 列，demo 需可見兩行）
    ck(demo_brief(1) == demo_brief(1) and demo_brief(1) in _BRIEF_DESCRIPTIONS, "demo_brief 確定性")
    ck(demo_diag(1) == demo_diag(1) and demo_diag(1) in _DIAGNOSES, "demo_diag 確定性")
    ck(all(demo_diag(w) in _DIAGNOSES for w in (1, 3, 7, 40001, 40090)), "demo_diag 值域封閉")
    # demo_notes / demo_action（cmms detail v1 additive；兩案例＝新工單事件流 vs 歷史靜態診斷）
    ck(demo_has_notes(40002) and not demo_has_notes(40003), "demo_has_notes 偶/奇二分")
    nt = demo_notes(40002)
    ck(demo_notes(40002) == nt and len(nt) == 3, "demo_notes 確定性＋3 筆（新工單案例）")
    ck([e["entry_type"] for e in nt] == ["report", "diagnosis", "progress"]
       and [e["at"] for e in nt] == sorted(e["at"] for e in nt),
       "demo_notes entry_type 序＋升冪、at 為台北 naive")
    ck(all(e["at"].tzinfo is None and set(e.keys()) == {"at", "entry_type", "body"} for e in nt),
       "demo_notes 條目形＝{at(naive),entry_type,body}（normalize_notes 輸出形）")
    ck(demo_notes(40003) == [] and demo_action(40003) in _ACTIONS,
       "奇數工單＝歷史案例：無 notes、有結單處置")
    ck(demo_action(40002) is None, "新工單案例（有 notes）→ action_taken=None（DISJOINT）")

    # ---- (I2) uptime/hold-色 showcase（2026-07-10 Chris 反饋落地驗收；EQM_UPT_SHOWCASE_EID 四日四視覺）----
    _su = next(r for a in db1["areas"] for r in a["rows"] if r["eid"] == EQM_UPT_SHOWCASE_EID)
    _suc = _su["cells"]
    ck(any(c.get("dominant") == HOLD_VENDOR_STATE for c in _suc.values()),
       f"showcase ① 等外包 dominant 日（琥珀實心）: {[(k, c.get('dominant')) for k, c in _suc.items()]}")
    ck(any(c.get("dominant") == "維修" and HOLD_PARTS_STATE in (c.get("by_state") or {})
           for c in _suc.values()),
       "showcase ② 維修+等料 混合日（紅格 + 格底紫段）")
    ck(any(c.get("stop_h") and UPTIME_TEST_STATE in (c.get("by_uptime") or {})
           for c in _suc.values()),
       "showcase ③ 維修+試跑（stop_h>0 且 by_uptime 含試跑＝格底圓點 uptime 段）")
    ck(any((not c.get("stop_h")) and UPTIME_WAIT_STATE in (c.get("by_uptime") or {})
           for c in _suc.values()),
       "showcase ④ 純 uptime 日（stop_h=0 且 by_uptime 含等機台空檔＝透明格 + 斜紋組成條）")
    # 兩種 uptime 紋理都在窗內現形（斜紋等機台空檔 + 圓點試跑）＝demo 一眼驗收兩紋理
    _su_upt = set()
    for c in _suc.values():
        _su_upt |= set((c.get("by_uptime") or {}).keys())
    ck(UPTIME_WAIT_STATE in _su_upt and UPTIME_TEST_STATE in _su_upt,
       f"showcase 窗內兩 uptime 紋理皆現（斜紋+圓點）: {_su_upt}")

    # ---- (N) downtime_by_eid（landing OEE A↔U 真拆分消費：per-EID union countable + by_state）----
    ws0 = D(2026, 7, 1)
    we0 = D(2026, 7, 8)
    # 兩張同機重疊維修單（07-04 02:00–06:00 與 04:00–08:00）→ union＝02:00–08:00＝6h（非 4+4=8）
    rx1 = w8.normalize_wo({"work_order_no": 61, "asset_id": "EID-DT", "work_type": "REACTIVE",
                           "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                           "closed_at": _to_utc_z(D(2026, 7, 4, 6, 0)), "status_history": []})
    rx2 = w8.normalize_wo({"work_order_no": 62, "asset_id": "EID-DT", "work_type": "REACTIVE",
                           "opened_at": _to_utc_z(D(2026, 7, 4, 4, 0)),
                           "closed_at": _to_utc_z(D(2026, 7, 4, 8, 0)), "status_history": []})
    pm1 = w8.normalize_wo({"work_order_no": 63, "asset_id": "EID-DT", "work_type": "PM",
                           "opened_at": _to_utc_z(D(2026, 7, 5, 1, 0)),
                           "closed_at": _to_utc_z(D(2026, 7, 5, 3, 0)),
                           "status_history": [{"to_status": "IN_PROGRESS",
                                               "changed_at": _to_utc_z(D(2026, 7, 5, 1, 0))},
                                              {"to_status": "COMPLETED",
                                               "changed_at": _to_utc_z(D(2026, 7, 5, 3, 0))}]})
    dbe = downtime_by_eid({"EID-DT": [rx1, rx2, pm1]}, ws0, we0)
    d = dbe["EID-DT"]
    ck(abs(d["down_sec"] - (6 * 3600 + 2 * 3600)) < 1.0,
       f"union 重疊維修 6h + PM 2h＝8h（非 4+4+2）: {d['down_sec']/3600:.2f}h")
    ck(abs(d["by_state"]["維修"] - 8 * 3600) < 1.0, "by_state 維修＝原始加總 4+4＝8h（>union）")
    ck(abs(d["by_state"]["計畫保養"] - 2 * 3600) < 1.0, "by_state 計畫保養＝2h")
    ck(downtime_by_eid({}, ws0, we0) == {} and downtime_by_eid(None, ws0, we0) == {},
       "空/None → {}（不炸）")
    # rollup 語意（跨 board_oee 校驗此處只驗 wem 端 per-EID 正確）：無工單 EID → 不在 dbe
    ck("EID-NONE" not in downtime_by_eid({"EID-DT": [rx1]}, ws0, we0), "無列 EID 不現")

    # ---- (N2) downtime_by_eid_scoped（A10 擴散 R1，2026-08-14；/utilization + landing）----
    #      三件：① 母機 EID 走「機器視角」（scope 縮小後不算機器停）；② 任何 EID 套長停日過濾；
    #            ③ **負對照：無母機、無 dormant ⇒ 與 downtime_by_eid 逐位元相同**。
    _NS = {"EID-DT": [rx1, rx2, pm1]}
    ck(downtime_by_eid_scoped(_NS, ws0, we0, dormant_events=[]) == downtime_by_eid(_NS, ws0, we0),
       "N2-1 負對照：無母機 EID ∧ 無 dormant ⇒ 與 downtime_by_eid **逐位元相同**")
    ck(downtime_by_eid_scoped({}, ws0, we0) == {}
       and downtime_by_eid_scoped(None, ws0, we0) == {}, "N2-2 空/None → {}")
    # ① 機器視角：golden 劇本（08:50 開單整機 → 10:30 縮到 Bay#1 → … → 15:40 結單）
    #    子工位軸 K1 是 6h50m，但**機器**只停了初始那 1h40m。
    _PA = _PA
    _ms_day = D(2026, 7, 4)

    def _ma(h, m=0):
        return _ms_day + _dt.timedelta(hours=h, minutes=m)

    _mwo = w8.normalize_wo(_demo_phase_wo(
        70001, _PA, _ma(8, 50), _ma(15, 40), "REACTIVE",
        [("OPEN", None, _ma(8, 50)), ("IN_PROGRESS", None, _ma(10, 25)),
         ("CLOSED", None, _ma(15, 40))]))
    _mwo["scope_events"] = w8.normalize_scope_events(
        [{"ts": _ma(10, 30), "scope": [wscope.PARENT_EXPANSION[_PA][0]]}])[0]
    _msc = downtime_by_eid_scoped({_PA: [_mwo]}, ws0, we0, dormant_events=[])
    ck(abs(_msc[_PA]["down_sec"] - (1 * 3600 + 40 * 60)) < 1e-6,
       f"N2-3 母機列＝機器視角 1h40m（範圍縮小後其餘工位還在生產）: "
       f"{_msc[_PA]['down_sec']/3600:.3f}h")
    ck(abs(downtime_by_eid({_PA: [_mwo]}, ws0, we0)[_PA]["down_sec"] - (6 * 3600 + 50 * 60)) < 1e-6,
       "N2-4 對照組：舊函式仍回整個活躍窗 6h50m（既有兩支一個位元沒動）")
    ck(abs(sum(_msc[_PA]["by_state"].values()) - (1 * 3600 + 40 * 60)) < 1e-6,
       "N2-5 by_state 跟著裁（不是只改 down_sec）")
    # 折返整機 ⇒ 機器再度整台停（證明裁切是逐段的、不是「只取第一段」）
    _mwo2 = dict(_mwo)
    _mwo2["scope_events"] = w8.normalize_scope_events(
        [{"ts": _ma(10, 30), "scope": [wscope.PARENT_EXPANSION[_PA][0]]},
         {"ts": _ma(12, 0), "scope": [_PA]}])[0]
    ck(abs(downtime_by_eid_scoped({_PA: [_mwo2]}, ws0, we0,
                                  dormant_events=[])[_PA]["down_sec"]
           - (1 * 3600 + 40 * 60 + 3 * 3600 + 40 * 60)) < 1e-6,
       "N2-6 折返整機 ⇒ 1h40m + 3h40m（逐段裁、非只取首段）")
    # legacy `[]`（無範圍事件）⇒ 機器視角 ≡ 舊函式（絕大多數單就是這型 ⇒ live 幾乎零變動）
    _mleg = dict(_mwo)
    _mleg["scope_events"] = []
    ck(downtime_by_eid_scoped({_PA: [_mleg]}, ws0, we0, dormant_events=[])
       == downtime_by_eid({_PA: [_mleg]}, ws0, we0),
       "N2-7 legacy [] 母單 ⇒ 與舊函式逐位元相同（沒有範圍事件就沒有縮小可言）")
    # ② 長停 EID：直接開在它身上的單不計（甲的同一條規則，跨頁一致）
    _dwo = w8.normalize_wo({"work_order_no": 70002, "asset_id": _D2,
                            "work_type": "REACTIVE",
                            "opened_at": _to_utc_z(D(2026, 7, 4, 2, 0)),
                            "closed_at": _to_utc_z(D(2026, 7, 4, 6, 0)),
                            "status_history": []})
    ck(downtime_by_eid_scoped({_D2: [_dwo]}, ws0, we0)[_D2]
       == {"down_sec": 0.0, "by_state": {}},
       "N2-8 長停工位（橋常數）直接單 ⇒ 0h（/utilization·landing 與 /equipment 同一個答案）")
    ck(abs(downtime_by_eid_scoped({_D2: [_dwo]}, ws0, we0,
                                  dormant_events=[])[_D2]["down_sec"] - 4 * 3600) < 1e-6,
       "N2-9 負對照：dormant_events=[] ⇒ 照計 4h")

    # ---- (N3) wo_segments_scoped（A10 擴散 R2，2026-08-16；/daily attrib + /dispatch）----
    #      段級版與 union 版**同一支口徑**（downtime_by_eid_scoped 已改為消費它＝結構上
    #      不可能分岔）；這裡另釘段級消費者才吃得到的三件：
    #      ① 負對照：非母機 ∧ dormant_events=[] ⇒ 與 wo_segments **逐位元相同**（其餘機台不動）。
    ck(wo_segments_scoped("EID-DT", [rx1, rx2, pm1], ws0, we0, dormant_events=[])
       == wo_segments([rx1, rx2, pm1], ws0, we0),
       "N3-1 負對照：非母機 EID ⇒ 段級輸出與 wo_segments 逐位元相同")
    #      ② 母機段被 scope 裁掉尾巴 ⇒ **ongoing 重算**——/dispatch down_eids 的承重：
    #         範圍縮小後「此刻」機器不算停，靠的就是裁後段不再 ongoing。
    _mon = w8.normalize_wo({
        "work_order_no": 70003, "asset_id": _PA, "work_type": "REACTIVE",
        "opened_at": _to_utc_z(_ma(8, 50)), "closed_at": None,
        "status_history": [
            {"to_status": "OPEN", "changed_at": _to_utc_z(_ma(8, 50))},
            {"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(_ma(10, 25))}]})
    _mon["scope_events"] = w8.normalize_scope_events(
        [{"ts": _ma(10, 30), "scope": [wscope.PARENT_EXPANSION[_PA][0]]}])[0]
    _sgo = wo_segments_scoped(_PA, [_mon], _ma(10, 55), _ma(11, 0), dormant_events=[])
    ck(_sgo == [], "N3-2 ongoing 母單已縮到工位 ⇒ 此刻機器視角零段（down_eids 據此不亮）")
    _sgo2 = wo_segments_scoped(_PA, [_mon], _ma(9, 0), _ma(11, 0), dormant_events=[])
    ck(len(_sgo2) == 1 and _sgo2[0]["end"] == _ma(10, 30) and _sgo2[0]["ongoing"] is False,
       "N3-3 初始整機段被裁到 10:30 ⇒ ongoing 重算為 False（不再是「現在仍停」）")
    #      ③ 長停 EID 的段級輸出＝countable 全扣、uptime-hold 段照留（drill 資訊不消失）。
    ck(wo_segments_scoped(_D2, [_dwo], ws0, we0) == [],
       "N3-4 長停工位直接單 ⇒ 段級輸出空（與 union 版 0h 同一件事）")

    # ---- (N4) ★ R44 C-AV16：形③ 父機＝在役座推導（parent_seat_rollup）---------------
    #      橋常數（None）⇒ 26841／26887 長停 ⇒ 24742 在役座＝24790、26820（n=2）。
    # WP-10d：形③ 父機的在役座＝家族座扣掉橋上長停座（私有樹＝EID-24790、EID-26820 兩座；公開樹＝合成母機的在役座）；下面的期望值隨 n 推導
    _ACT = [k for k in wscope.PARENT_EXPANSION[_PA] if k not in _bridge_r]
    def _rx(wno, eid, a, b, wt="REACTIVE", hist=None):
        return w8.normalize_wo({"work_order_no": wno, "asset_id": eid, "work_type": wt,
                                "opened_at": _to_utc_z(a),
                                "closed_at": (_to_utc_z(b) if b else None),
                                "status_history": hist or []})
    _SPAN = (we0 - ws0).total_seconds()
    ck(parent_active_days(_PA, ws0, we0)[0][2] == tuple(_ACT),
       "N4-1 在役座＝wcap.active_units 逐日（None＝橋常數 ⇒ 26841／26887 不在役）")
    ck(parent_active_days(_PA, ws0, we0, dormant_events=[])[0][2]
       == wscope.PARENT_EXPANSION[_PA], "N4-1b dormant_events=[] ⇒ 四座全在役（負對照）")
    _s1 = {_K1a: [_rx(71001, _K1a, D(2026, 7, 4, 2), D(2026, 7, 4, 6))]}
    _r1 = parent_seat_rollup(_PA, _s1, ws0, we0)
    ck(abs(_r1["down_sec"] - 4 * 3600 / len(_ACT)) < 1e-6,
       f"N4-2 一座 4h 停機 ÷ n在役 2 ＝ 父機 2h（子座單進父機）: {_r1['down_sec']/3600:.3f}h")
    _full = {e: [_rx(71010 + i, e, D(2026, 6, 30), None)]
             for i, e in enumerate(_ACT)}
    ck(parent_seat_rollup(_PA, _full, ws0, we0)["A"] == 0.0,
       "N4-3 全部在役座整窗停 ⇒ 父機 0%（不是 100%）")
    _half = {_K1a: _full[_K1a]}
    ck(abs(parent_seat_rollup(_PA, _half, ws0, we0)["A"] - (1 - 1.0 / len(_ACT))) < 1e-9,
       "N4-4 兩座在役、一座整窗停 ⇒ 父機 50%")
    _alld = [{"eid": e, "ts_effective": "2020-01-01", "state": wdorm.STATE_NOT_SCHEDULED}
             for e in wscope.PARENT_EXPANSION[_PA]]
    _r4 = parent_seat_rollup(_PA, _full, ws0, we0, dormant_events=_alld)
    ck(_r4["A"] is None and _r4["rw_sec"] == 0 and abs(_r4["excluded_rw_sec"] - _SPAN) < 1e-6,
       "N4-5 全座長停（整機未登記）⇒ A=None、RW 自分母排除")
    _pm = _rx(71020, _PA, D(2026, 7, 5, 1), D(2026, 7, 5, 3), wt="PM",
              hist=[{"to_status": "IN_PROGRESS", "changed_at": _to_utc_z(D(2026, 7, 5, 1))},
                    {"to_status": "COMPLETED", "changed_at": _to_utc_z(D(2026, 7, 5, 3))}])
    _own = _rx(71021, _K1a, D(2026, 7, 5, 2), D(2026, 7, 5, 4))
    _r5 = parent_seat_rollup(_PA, {_PA: [_pm], _K1a: [_own]}, ws0, we0)
    ck(abs(_r5["down_sec"] - (3 + 2 * (len(_ACT) - 1)) * 3600 / len(_ACT)) < 1e-6,
       f"N4-6 父機 PM 2h 整機展到在役座 ∪ 座自己單 2h（重疊 1h 不雙算）＝(3h+2h)/2＝2.5h: "
       f"{_r5['down_sec']/3600:.3f}h")
    _r6 = parent_seat_rollup(_PA, {_PA: [_mwo]}, ws0, we0)
    ck(abs(_r6["down_sec"] - (6 * 3600 + 50 * 60 + (len(_ACT) - 1) * (1 * 3600 + 40 * 60)) / len(_ACT)) < 1e-6,
       f"N4-7 REACTIVE 縮 scope 到 Bay#1 ⇒ 只展 scope 內的座（24790 6h50m、26820 只 1h40m）"
       f": {_r6['down_sec']/3600:.3f}h")
    ck(seat_down_segments("EID-DT", {"EID-DT": [rx1, rx2, pm1]}, ws0, we0)
       == wo_segments_scoped("EID-DT", [rx1, rx2, pm1], ws0, we0),
       "N4-8 負對照：非形③ 座的列段與 wo_segments_scoped 逐位元相同")
    _neg = {"EID-DT": [rx1, rx2, pm1], _NE: [rx1]}
    ck(downtime_by_eid_rollup(_neg, ws0, we0) == downtime_by_eid_scoped(_neg, ws0, we0),
       "N4-9 負對照：單座機／形① nest 在 downtime_by_eid_rollup 逐位元不變")
    ck(parent_seat_rollup("EID-DT", _neg, ws0, we0) is None
       and parent_seat_rollup(_NE, _neg, ws0, we0) is None,
       "N4-10 非形③ 父機 ⇒ None（不套推導）")
    _d9 = downtime_by_eid_rollup(_s1, ws0, we0)
    ck(_PA in _d9 and abs(_d9[_PA]["down_sec"] - 4 * 3600 / len(_ACT)) < 1e-6,
       "N4-11 downtime_by_eid_rollup：父機自己沒單、座有單 ⇒ 父機仍出現且＝座推導")
    ck(len(seat_down_segments(_ACT[-1], {_PA: [_mwo]}, ws0, we0)) >= 1
       and all(s.get("inherited_from") == _PA
               for s in seat_down_segments(_ACT[-1], {_PA: [_mwo]}, ws0, we0)),
       "N4-12 座列段＝自己 ∪ 繼承（繼承段帶 inherited_from，與 /equipment 同）")
    ck(parent_seat_rollup(_PA, _s1, ws0, we0, rw_by_eid={}) is None,
       "N4-13 rw_by_eid 缺父機（名冊外 C-RW9）⇒ None、不主張")

    # ---- (O) 取消／作廢單不進可計停機（2026-08-11 拍板①；對齊 PM 板 C-P7）----------------
    #      劇本＝prod 真案 `WO 35185`／`EID-00884`：REACTIVE、2026-06-24 開單、2026-08-10 取消，
    #      `status_history` 只有那一筆 CANCELLED、`closed_at` NULL（CANCELLED 恆 NULL）
    #      ⇒ 改動前活躍窗＝開單→取消時刻、`_phase_timeline` 由 a_start 補 OPEN 前綴、
    #        `_phase_kind("OPEN", None, "REACTIVE")=="count"` ⇒ **47 天整片塗紅**。
    cws, cwe = D(2026, 8, 1), D(2026, 8, 11)
    _cx_raw = {"work_order_no": 35185, "asset_id": _C3, "work_type": "REACTIVE",
               "opened_at": _to_utc_z(D(2026, 6, 24, 10, 39)), "closed_at": None,
               "status": "CANCELLED",
               "status_history": [{"to_status": "CANCELLED",
                                   "changed_at": _to_utc_z(D(2026, 8, 10, 8, 15))}]}
    _cx = w8.normalize_wo(_cx_raw)
    ck(_cx is not None and _cx["status"] == "CANCELLED", "取消單 normalize 成功且帶 status 欄")
    ck(w8.wo_active_window(_cx, now=cwe) is not None,
       "★ 前提：取消單本身仍有合法活躍窗（＝過去塗紅的來源；修法不是靠丟窗）")
    ck(wo_segments([_cx], cws, cwe) == [], "取消單 → wo_segments 零段（拍板①）")
    ck(downtime_by_eid({_C3: [_cx]}, cws, cwe)[_C3]
       == {"down_sec": 0.0, "by_state": {}},
       "取消單 → downtime_by_eid 該 EID 0h、by_state 空")
    # Now 欄要在**取消生效之前那段時間**看才有鑑別力（取消時刻之後活躍窗本來就不涵蓋現在）：
    # 窗尾取 08-05（落在 06-24 開單 → 08-10 取消 之間）⇒ 修法前末階段＝OPEN·REACTIVE＝count＝DOWN。
    ck(_now_block([_cx], D(2026, 8, 5))["bucket"] == "up",
       "取消單 → Now 回 UP（改動前這裡是 DOWN；且不得因 closed_at NULL 而永遠 DOWN）")
    # ★ 負對照：同一張單改成 CLOSED（history 末筆 CLOSED）⇒ 停機照算、一秒不少
    _cl_raw = dict(_cx_raw, work_order_no=35209, status="CLOSED",
                   status_history=[{"to_status": "CLOSED",
                                    "changed_at": _to_utc_z(D(2026, 8, 10, 8, 15))}])
    _cl = w8.normalize_wo(_cl_raw)
    _cl_segs = wo_segments([_cl], cws, cwe)
    ck(_cl_segs and all(s["countable"] for s in _cl_segs),
       "負對照：同單 CLOSED → 停機照算（結案單的歷史停機不得消失）")
    ck(abs(downtime_by_eid({_C3: [_cl]}, cws, cwe)[_C3]["down_sec"]
           - (D(2026, 8, 10, 8, 15) - cws).total_seconds()) < 1.0,
       "負對照：CLOSED 單停機時數＝窗起→結案時刻（未被誤刪）")
    # drill 的「當日相交但未產段」摘要也不得列它（否則抱怨只解一半：色塊沒了、drill 仍列一行
    # 沒有任何『已取消』標記的「維修」）。負對照＝CLOSED 版本仍在 wos_win。
    _rw_cx = build_row(("zc", "CAL#3", _C3), [_cx], day_windows(cwe, 5), cwe, "demo")
    _rw_cl = build_row(("zc", "CAL#3", _C3), [_cl], day_windows(cwe, 5), cwe, "demo")
    ck(_rw_cx["wos_win"] == [] and len(_rw_cl["wos_win"]) == 1,
       "取消單不進 wos_win（drill 摘要）；CLOSED 負對照仍在")
    # ★ 兩條腿各自成立：只有 status 欄（空 history＝eMaint 匯出殭屍型）也要擋掉
    _cz = w8.normalize_wo({"work_order_no": 35267, "asset_id": _C3, "work_type": "REACTIVE",
                          "opened_at": _to_utc_z(D(2026, 6, 24, 10, 39)), "closed_at": None,
                          "status": "VOIDED", "status_history": []})
    ck(wo_segments([_cz], cws, cwe) == [] and _now_block([_cz], cwe)["bucket"] == "up",
       "VOIDED + 空 history（eMaint 匯出型）→ 零段且 Now UP（只讀 history 會漏掉整批）")
    # A10 母單路徑：母單取消 ⇒ 子工位既不繼承段、Now 也不繼承 DOWN
    _pk = wscope.PARENT_EXPANSION[_PA][0]
    _pcx = w8.normalize_wo(dict(_cx_raw, work_order_no=35300, asset_id=_PA))
    _pcx["scope_events"] = []
    _inh_c = wscope.expand_for_children({_PA: [_pcx]}, cws, cwe)
    _ent_c = _inh_c.get(_pk) or []
    ck(_ent_c and _inherited_segments(_ent_c, cws, cwe) == [],
       "A10：母單取消 ⇒ 子工位零繼承段（＊結構性繼承自 wo_segments、非第二份 predicate）")
    ck(_now_inherited(_ent_c, cwe) is None, "A10：母單取消 ⇒ 子工位 Now 不繼承 DOWN")
    # 母單負對照（CLOSED 版本仍會繼承）＝證明上面兩條不是因為 clips 本身是空的而假綠
    _pcl = w8.normalize_wo(dict(_cl_raw, work_order_no=35351, asset_id=_PA))
    _pcl["scope_events"] = []
    _ent_l = (wscope.expand_for_children({_PA: [_pcl]}, cws, cwe)).get(_pk) or []
    ck(_ent_l and _inherited_segments(_ent_l, cws, cwe),
       "A10 負對照：母單 CLOSED ⇒ 子工位照樣有繼承段（前一條不是假綠）")

    # ---- (P) (c) MES 交叉揭露（2026-08-11 拍板；單向、零新查詢、不改任何數字）--------------
    _cfw_s, _cfw_e = D(2026, 8, 1), D(2026, 8, 11)
    _cf_wo = w8.normalize_wo({"work_order_no": 35496, "asset_id": _C3,
                              "work_type": "REACTIVE", "status": "IN_PROGRESS",
                              "opened_at": _to_utc_z(D(2026, 8, 2, 0, 0)), "closed_at": None,
                              "status_history": [
                                  {"to_status": "OPEN", "changed_at": _to_utc_z(D(2026, 8, 2, 0, 0))},
                                  {"to_status": "IN_PROGRESS",
                                   "changed_at": _to_utc_z(D(2026, 8, 2, 1, 0))}]})
    _cf_segs = wo_segments([_cf_wo], _cfw_s, _cfw_e)     # countable 段＝08-02 00:00 → 窗尾
    ck(_cf_segs and _cf_segs[0]["countable"], "★ 前提：(c) 劇本的工單真的產出 countable 段")
    ck(wo_mes_conflict(_cf_segs, 24.0, _cfw_e, 2.0) == {"wo_no": 35496, "mes_last_h": 24.0},
       "(c) 生產訊號落在停機段中段 → 揭露矛盾（帶 wo_no）")
    ck(wo_mes_conflict(_cf_segs, 0.0, _cfw_e, 2.0) is not None,
       "(c) 生產訊號恰在窗尾（段尾閉區間）→ 仍算矛盾")
    # 護欄：段起點後 guard 小時內的生產訊號**不算矛盾**（報修當下把手上那顆跑完＋時鐘 skew）
    _age_at_start = (_cfw_e - D(2026, 8, 2, 0, 0)).total_seconds() / 3600.0
    ck(wo_mes_conflict(_cf_segs, _age_at_start, _cfw_e, 2.0) is None,
       "(c) 生產訊號恰在段起點 → 護欄吸收、不算矛盾")
    ck(wo_mes_conflict(_cf_segs, _age_at_start - 1.9, _cfw_e, 2.0) is None
       and wo_mes_conflict(_cf_segs, _age_at_start - 2.1, _cfw_e, 2.0) is not None,
       "(c) 護欄刀鋒：起點 +1.9h 不算、+2.1h 算（guard_h 真的在生效）")
    ck(wo_mes_conflict(_cf_segs, _age_at_start + 5.0, _cfw_e, 2.0) is None,
       "(c) 生產訊號早於停機段 → 不算矛盾（開單前在生產是常態）")
    ck(wo_mes_conflict(_cf_segs, None, _cfw_e, 2.0) is None
       and wo_mes_conflict(_cf_segs, "8", _cfw_e, 2.0) is None
       and wo_mes_conflict(_cf_segs, True, _cfw_e, 2.0) is None,
       "(c) mes_last_h 量不到／型別髒 → None（量不到不是矛盾，也不假裝沒矛盾）")
    ck(wo_mes_conflict([], 1.0, _cfw_e, 2.0) is None
       and wo_mes_conflict(None, 1.0, _cfw_e, 2.0) is None, "(c) 無段 → None")
    # uptime-hold 段不算矛盾（它本來就在說「機台可生產」）
    _upt_wo = w8.normalize_wo(_demo_open_wo(24501, _C3, D(2026, 8, 2, 0, 0),
                                            _cfw_e, "reactive_hold"))
    _upt_segs = [s for s in wo_segments([_upt_wo], _cfw_s, _cfw_e) if not s["countable"]]
    ck(_upt_segs and wo_mes_conflict(_upt_segs, 1.0, _cfw_e, 2.0) is None,
       "(c) uptime-hold 段永不算矛盾（它本來就說機台可生產）")
    # ★ 只揭露不改數字：同一列加不加 (c)，stop_h／A%／cells 逐位相同
    _dws = day_windows(_cfw_e, 5)
    _r_no = build_row(("zc", "CAL#3", _C3), [_cf_wo], _dws, _cfw_e, "demo")
    _r_cf = build_row(("zc", "CAL#3", _C3), [_cf_wo], _dws, _cfw_e, "demo",
                      mes_last_h=24.0, mes_available=True)
    ck(_r_cf["wo_mes_conflict"] is not None and _r_no["wo_mes_conflict"] is None,
       "(c) build_row 帶 additive 欄；mes 不可用時為 None")
    ck(_r_cf["total_stop_h"] == _r_no["total_stop_h"]
       and _r_cf["avail_pct"] == _r_no["avail_pct"]
       and _r_cf["cells"] == _r_no["cells"] and _r_cf["segments"] == _r_no["segments"],
       "(c) **只揭露不改數字**：stop_h／A%／cells／segments 逐位相同")
    # 取消單優先於 (c)：段都沒了就沒有矛盾可揭（兩件事的順序不可倒）
    ck(build_row(("zc", "CAL#3", _C3), [_cx], _dws, _cfw_e, "demo",
                 mes_last_h=24.0, mes_available=True)["wo_mes_conflict"] is None,
       "(c) 取消單已被 ② 擋掉 ⇒ 不會再被揭露成矛盾（不重複告狀）")
    # demo 板：showcase 機確定性現形 + board.conflicts 形狀
    _cfb = demo_board(5)
    _cf_eids = [c["eid"] for c in _cfb["conflicts"]]
    ck(EQM_CONFLICT_SHOWCASE_EID in _cf_eids,
       f"(c) demo showcase 機 {EQM_CONFLICT_SHOWCASE_EID} 必在 conflicts（demo 合成漂移時要紅燈）")
    ck(all(set(c) == {"eid", "wo_no"} for c in _cfb["conflicts"]),
       "(c) conflicts 形狀＝[{eid, wo_no}]（不外洩其他欄）")
    ck(len(_cf_eids) == len(set(_cf_eids)), "(c) conflicts 每台至多一筆（dup EID 不重複）")
    # 無 MES 證據（mes_by_eid=None）⇒ conflicts 恆空＝版面逐位元同加此功能前
    _nb = build_board(EQMT_CATALOG[:5], {}, _cfw_e, 5, "demo", mes_by_eid=None)
    ck(_nb["conflicts"] == [], "(c) 無 MES 證據 → conflicts []（不揭露、版面不變）")

    # ---- (Q) §10 一致性揭露（登記長停 ∧ MES 窗內有生產；哨兵否決後的唯一殘片）------------
    #   ★ 判準＝日粒度、嚴格晚於生效日；**零新查詢**（吃 Now 欄那份 mes_last_h）。
    _dz = {"since": "2023-05-23", "reason_zh": "線外查修", "reason_en": "off-line"}
    _dwe = D(2026, 8, 11, 12, 0)
    ck(dormant_mes_conflict(_dz, 6.0, _dwe) == {"mes_last_h": 6.0},
       "§10 登記長停 ∧ MES 窗內有生產 → 揭露（只帶 mes_last_h、不帶裁決）")
    ck(dormant_mes_conflict(None, 6.0, _dwe) is None
       and dormant_mes_conflict({}, 6.0, _dwe) is None,
       "§10 沒登記長停 ⇒ None（這條**單向**：機台停著卻沒登記＝被否決的另一個方向，不抓）")
    ck(dormant_mes_conflict(_dz, None, _dwe) is None
       and dormant_mes_conflict(_dz, "6", _dwe) is None
       and dormant_mes_conflict(_dz, True, _dwe) is None,
       "§10 mes_last_h 量不到／型別髒 → None（量不到既不宣稱矛盾、也不宣稱沒矛盾）")
    ck(dormant_mes_conflict({"since": None}, 6.0, _dwe) is None
       and dormant_mes_conflict({"since": "2023-13-99"}, 6.0, _dwe) is None
       and dormant_mes_conflict({"since": "x"}, 6.0, _dwe) is None,
       "§10 since 缺／壞 → None（沒有基準日就不猜）")
    # 日粒度刀鋒：生效日**當天**（及之前）的生產不算矛盾——決策當天上午還在跑是常態；
    #   次日 00:00 起才算。整整一個日曆日就是護欄，不必再疊時鐘 skew。
    ck(dormant_mes_conflict(_dz, 0.0, D(2023, 5, 23, 23, 59)) is None
       and dormant_mes_conflict(_dz, 0.0, D(2023, 5, 22, 23, 59)) is None
       and dormant_mes_conflict(_dz, 0.0, D(2023, 5, 24, 0, 0)) is not None,
       "§10 日粒度刀鋒：生效日當天／之前皆不算矛盾，**次日 00:00 起才算**")
    # 同一個 win_end、age 跨過生效日界 ⇒ 判定翻面（證明吃的真的是 t_last 不是 win_end）
    ck(dormant_mes_conflict(_dz, 24.0, D(2023, 5, 24, 12, 0)) is None
       and dormant_mes_conflict(_dz, 11.0, D(2023, 5, 24, 12, 0)) is not None,
       "§10 age 跨界翻面：同一 win_end 下 age=24h（落回 05-23）不算、age=11h（05-24）才算"
       "——判定吃的是 `win_end − age`，不是 win_end 本身")
    # ★ 只揭露不改數字（仿 (c) 的釘法）：同一列加不加 §10 欄，每個數字逐位相同
    _r_d0 = build_row(("zc", "CAL M2", _D2), [_cf_wo], _dws, _cfw_e, "demo",
                      dormant=_dz)
    _r_d1 = build_row(("zc", "CAL M2", _D2), [_cf_wo], _dws, _cfw_e, "demo",
                      dormant=_dz, mes_last_h=6.0, mes_available=True)
    ck(_r_d1["dormant_mes_conflict"] is not None and _r_d0["dormant_mes_conflict"] is None,
       "§10 build_row 帶 additive 欄；mes 不可用時為 None")
    ck(_r_d1["total_stop_h"] == _r_d0["total_stop_h"]
       and _r_d1["avail_pct"] is None and _r_d0["avail_pct"] is None
       and _r_d1["cells"] == _r_d0["cells"] and _r_d1["segments"] == _r_d0["segments"]
       and _r_d1["dormant"] == _r_d0["dormant"],
       "§10 **只揭露不改數字**：stop_h／A%（仍是「—」）／cells／segments／dormant 旗標逐位相同"
       "——C-D1：登記才是事實，這行字**不自動解除登記**")
    # demo 板：showcase 機確定性現形 + board.dormant_conflicts 形狀
    _dcb = demo_board(5)
    _dcf_eids = [c["eid"] for c in _dcb["dormant_conflicts"]]
    ck(_dcf_eids == [EQM_DORMANT_CONFLICT_SHOWCASE_EID],
       f"§10 demo showcase 機恰一台（合成漂移時要紅燈，不是靜默失去 demo 案例），got {_dcf_eids}")
    ck(all(set(c) == {"eid", "since"} for c in _dcb["dormant_conflicts"])
       and _dcb["dormant_conflicts"][0]["since"] == {r[0]: r[1] for r in wdorm.DORMANT_BRIDGE}[EQM_DORMANT_CONFLICT_SHOWCASE_EID],
       f"§10 dormant_conflicts 形狀＝[{{eid, since}}]，got {_dcb['dormant_conflicts']}")
    ck(_dcf_eids != [c["eid"] for c in _dcb["conflicts"]],
       "§10 與 (c) 是**兩件不同的事**（不同機台、分兩列渲染；來源不同、該找的人不同）")
    _nb10 = build_board(EQMT_CATALOG[:5], {}, _cfw_e, 5, "demo", mes_by_eid=None,
                        dormant_by_eid={EQMT_CATALOG[0][2]: _dz})
    ck(_nb10["dormant_conflicts"] == [],
       "§10 無 MES 證據 → dormant_conflicts []（不揭露、版面逐位元同加此欄前）")
    # sched_unknown（A11 批次名冊漂移）：additive、預設空
    ck(_nb10["sched_unknown"] == [] and demo_board(5)["sched_unknown"] == [],
       "A11 batch：`sched_unknown` 預設空 ⇒ 模板整列不渲染（demo 恆無名冊漂移）")
    ck(build_board(EQMT_CATALOG[:3], {}, _cfw_e, 5, "demo",
                   sched_unknown=["EID-99999", None, "EID-88888"])["sched_unknown"]
       == ["EID-99999", "EID-88888"],
       "A11 batch：`sched_unknown` 原樣透傳（空值濾掉）——它是**錯誤**不是雜訊，頁上要紅字")

    # ---- (R) #39 該跑窗（`SPEC-RUNWINDOW.md` v1；C-RW 系列＋QG-13）------------------------
    # 區間代數先自檢（datetime 版三支仍被 day_cell 等消費；分鐘版住 board_runwindow 自測）
    _iv = _union_intervals([(D(2026, 8, 1, 2), D(2026, 8, 1, 5)),
                           (D(2026, 8, 1, 4), D(2026, 8, 1, 6)),
                           (D(2026, 8, 1, 9), D(2026, 8, 1, 10)),
                           (D(2026, 8, 1, 1), D(2026, 8, 1, 1))])         # 零長度丟掉
    ck(_iv == [(D(2026, 8, 1, 2), D(2026, 8, 1, 6)), (D(2026, 8, 1, 9), D(2026, 8, 1, 10))],
       f"_union_intervals：重疊併、零長度丟、排序，got {_iv}")
    ck(_ivs_seconds(_subtract_intervals([(D(2026, 8, 1, 0), D(2026, 8, 2, 0))], _iv))
       == (24 - 4 - 1) * 3600.0, "_subtract_intervals：24h − 4h − 1h ＝ 19h")

    # ① inert 三形（§5.3）：無日曆／coverage 缺／零涵蓋日 ⇒ erw_prepare None ⇒ 舊 24h 軸
    _rw_we = D(2026, 8, 12, 8, 0)                      # 生產日界上＝末欄 0 長、前欄全完整
    _rw_dw = day_windows(_rw_we, 3)
    ck(erw_prepare(_rw_dw, None, (D(2026, 8, 1), _rw_we)) is None, "①a 無日曆 ⇒ inert")
    ck(erw_prepare(_rw_dw, [], None) is None and erw_prepare(_rw_dw, [], (None, None)) is None,
       "①b coverage 缺 ⇒ inert")
    ck(erw_prepare(_rw_dw, [], (D(2026, 1, 1), D(2026, 1, 2))) is None,
       "①c coverage 與本窗零交集 ⇒ inert（不是 0、不是 100）")

    # ② active ctx（coverage 蓋前兩個完整生產日；slot／mp 全給 None＝降級形 ShiftWin−Closure）
    _rw_ctx = erw_prepare(_rw_dw, [], (_rw_dw[0][1], _rw_dw[-2][2]))
    ck(_rw_ctx is not None and len(_rw_ctx["days"]) == 2,
       f"② C-PC8：只有完整涵蓋的欄日被主張（2/3），got {_rw_ctx and len(_rw_ctx['days'])}")

    # ZAB 該跑窗（分鐘，自生產日 08:00 起）＝各班窗**逐分鐘聯集**（獨立於被測的 interval 代數）；
    # 本檔零班表時刻字面：班表被置換成合成值時期望值跟著走。斷言其形狀＝自 0 起單一連續段。
    from _probe_kun_activity import SHIFT_WINDOWS as _SW_zab, REGION_SHIFTS as _RS_zab
    _zab_cov = sorted({m_ for s_ in _RS_zab["ZAB"] for m_ in range(*_SW_zab[s_])})
    assert _zab_cov == list(range(len(_zab_cov))), "selftest 前提：ZAB 班次窗聯集＝自生產日 08:00 起單一連續段"
    _ZABM = len(_zab_cov)                                # ZAB 該跑窗分鐘
    _ZABH = _ZABM / 60.0
    # ③ 名冊 ZAB 機（EID-25872＝ZA-1）：降級形該跑窗＝ZAB 班次窗/日
    _rw_wo = w8.normalize_wo(_demo_migration_wo(
        51000, _E0, D(2026, 8, 10, 10, 0), D(2026, 8, 10, 14, 0), "REACTIVE"))  # 窗內修 4h
    _rw_bk = bucket_segments_by_day(wo_segments([_rw_wo], _rw_dw[0][1], _rw_we), _rw_dw)
    _er = erw_row(_E0, _rw_bk, _rw_dw, _rw_ctx)
    ck(_er is not None and abs(_er["window_h"] - 2 * _ZABH) < 1e-6,
       f"③ 降級形分母＝2 日 × ZAB 班次窗（{_ZABH:.2f}h），got {_er and _er['window_h']}")
    ck(abs(_er["stop_h"] - 4.0) < 1e-6 and abs(_er["avail"] - 100.0 * (1 - 4.0 / (2 * _ZABH))) < 1e-6,
       f"③ 停機∩窗＝4h（10:00–14:00 全在窗內）⇒ 妥善率照式，got {_er['stop_h']}/{_er['avail']}")
    ck(_er["degraded"] == ["slots_uncovered"],
       f"③ slot 源缺席 ⇒ 階梯①②③全停＋degraded 標（防塌陷：③不得單獨擴整段），got {_er['degraded']}")
    ck(erw_row(_EL, _rw_bk, _rw_dw, _rw_ctx) is None, "③ 非名冊 ⇒ None（C-RW9）")

    # ④ ★★ C-PC11／C-RW4 迴歸釘（QG-13）：停機 4h→20h ⇒ 分母恆定、值變差
    _rw_wo2 = w8.normalize_wo(_demo_migration_wo(
        51001, _E0, D(2026, 8, 10, 8, 30), D(2026, 8, 11, 4, 30), "REACTIVE"))  # 20h
    _rw_bk2 = bucket_segments_by_day(wo_segments([_rw_wo2], _rw_dw[0][1], _rw_we), _rw_dw)
    _er2 = erw_row(_E0, _rw_bk2, _rw_dw, _rw_ctx)
    ck(abs(_er2["window_h"] - _er["window_h"]) < 1e-9 and _er2["avail"] < _er["avail"],
       "④ ★★ C-RW4 迴歸釘：停越久分母恆定、妥善率變差"
       "（分母若隨自身訊號縮＝停越久反而越好看，而且沒有任何測試會紅——這條就是那個測試）")
    ck(abs(_er2["stop_h"] - (_ZABH - 0.5)) < 1e-6,
       f"④ 20h 修單 ∩ ZAB 班次窗＝窗長 − 0.5h（08:30 起 ⇒ 窗頭 0.5h 沒修），got {_er2['stop_h']}")

    # ⑤ closure 扣減＋uncertain（C-RW5／C-RW7）＋C-RW10
    _cl_one = [{"start": D(2026, 8, 10, 10, 0), "end": D(2026, 8, 10, 14, 0),
                "uncertain_eids": ()}]
    _ctx_cl = erw_prepare(_rw_dw, _cl_one, (_rw_dw[0][1], _rw_dw[-2][2]))
    _er_cl = erw_row(_E0, _rw_bk, _rw_dw, _ctx_cl)
    ck(abs(_er_cl["window_h"] - (_er["window_h"] - 4.0)) < 1e-6
       and abs(_er_cl["stop_h"] - 0.0) < 1e-9,
       f"⑤ closure 10–14 扣分母 4h、同時段修單不再算窗內停機，got {_er_cl['window_h']}/{_er_cl['stop_h']}")
    _cl_unc = [{"start": D(2026, 8, 10, 10, 0), "end": D(2026, 8, 10, 14, 0),
                "uncertain_eids": (_E0,)}]
    _er_unc = erw_row(_E0, _rw_bk, _rw_dw,
                      erw_prepare(_rw_dw, _cl_unc, (_rw_dw[0][1], _rw_dw[-2][2])))
    ck(abs(_er_unc["window_h"] - _er["window_h"]) < 1e-9 and abs(_er_unc["stop_h"] - 4.0) < 1e-9,
       "⑤ C-RW7：uncertain closure 對本機不扣（分母不縮、停機照算＝保守側）")
    _cl_full = [{"start": D(2026, 8, 10, 8, 0), "end": D(2026, 8, 12, 8, 0),
                 "uncertain_eids": ()}]
    _er_full = erw_row(_E0, _rw_bk, _rw_dw,
                       erw_prepare(_rw_dw, _cl_full, (_rw_dw[0][1], _rw_dw[-2][2])))
    ck(_er_full["avail"] is None and _er_full["window_h"] == 0,
       "⑤ C-RW10：closure 蓋滿全部涵蓋日 ⇒ |RW|=0 ⇒ 妥善率 None（顯「—」、不是 0%/100%）")

    # ⑥ Δ 與 time bar（§6.2 ②／§6.3 ③ 整窗連排）：bar 與妥善率同一份資料
    _bar = _er["bar"]
    ck(_bar is not None and _bar["n_days"] == 3 and abs(_bar["h"] - _er["window_h"]) < 1e-9,
       f"⑥ bar＝整窗連排（n_days＝欄數、h＝Σ|RW| 與妥善率同分母），got {_bar and _bar['n_days']}")
    ck("date" not in _bar and "avail" not in _bar and "gaps" not in _bar,
       "⑥ 舊單日鍵（date/avail/gaps）已退場——階梯紀錄改讀 days[ds].gaps（§6.3 ③）")
    ck(_bar["ticks"] == [round(100.0 / 3, 3), round(200.0 / 3, 3)],
       f"⑥ ticks＝日界百分比（n_days−1 條），got {_bar['ticks']}")
    ck(all(0.0 <= l <= 100.0 and 0.0 < w <= 100.0 for l, w in _bar["win"])
       and all(0.0 <= l <= 100.0 for _st, l, _w in _bar["stop"]),
       "⑥ bar 座標全落 0–100（整窗基底＝n_days×1440 分鐘）")
    # 第三欄（末欄）零長度＝不在 days ⇒ 進 uncov「留白」；涵蓋日永不進 uncov
    ck([round(l, 3) for l, _w in _bar["uncov"]] == [round(200.0 / 3, 3)]
       and abs(_bar["uncov"][0][1] - round(100.0 / 3, 3)) < 1e-9,
       f"⑥ uncov＝日曆未涵蓋日整日區段（留白＝無主張、≠沉灰的『不該跑』），got {_bar['uncov']}")
    _bar_stop_w = sum(w for _st, _l, w in _bar["stop"])
    _bar_win_w = sum(w for _l, w in _bar["win"])
    ck(_bar_stop_w <= _bar_win_w + 1e-6, "⑥ bar 停機段恆 ⊂ 窗段（分子分母同窗＝C-RW11 的像素版）")
    _yd = _er["days"][_rw_dw[-2][0]]
    ck(_er["delta"] is not None
       and abs(_er["delta"] - (100.0 * (1 - _yd["stop_h"] / _yd["h"]) - _er["avail"])) < 1e-9,
       "⑥ Δpp＝最新完整日單日值 − 整窗值（兩邊同軸；§6.3 ④ 頁面標基準）")
    # ⑥b |RW|=0 的涵蓋日：days 有 entry、h=0、bar 無亮段（C-RW10 顯「—」的資料側）
    _dz0 = _er_full["days"][_rw_dw[-2][0]]
    ck(_dz0["h"] == 0 and _dz0["stop_h"] == 0 and _dz0["by_state"] == {}
       and _er_full["bar"]["win"] == [] and _er_full["bar"]["stop"] == [],
       f"⑥b closure 蓋滿 ⇒ 涵蓋日仍有 entry 但 h=0、bar 零亮段（格子顯「—」不是 0.0），got {_dz0}")
    ck(_rw_dw[0][0] in _er["days"] and _rw_dw[-1][0] not in _er["days"],
       "⑥b 未涵蓋日在 days **無 entry**（格子據此顯「—·日曆未涵蓋」，永不外推）")

    # ⑥c §6.3 ① 窗內逐態：造一段**跨窗尾邊界**的修單，驗 by_state 只留窗內那半。
    #    23:00→次日 03:00 共 4h（生產日分鐘 [900,1140)）全在生產日 08-10 內；只有落在 ZAB 該跑窗內的那半進格子主字
    #    （窗內分鐘＝min(1140, 窗長) − 900；窗外那半只該出現在 drill 的淡化段與 tooltip）。
    _rw_wo3 = w8.normalize_wo(_demo_migration_wo(
        51002, _E0, D(2026, 8, 10, 23, 0), D(2026, 8, 11, 3, 0), "REACTIVE"))
    _er3 = erw_row(_E0, bucket_segments_by_day(
        wo_segments([_rw_wo3], _rw_dw[0][1], _rw_we), _rw_dw), _rw_dw, _rw_ctx)
    _d3 = _er3["days"][_rw_dw[0][0]]
    _in3 = min(1140, _ZABM) - 900                       # 窗內分鐘（須 > 0：修單起點 900 落在窗內）
    assert _in3 > 0, "selftest 前提：跨窗邊界段的起點在 ZAB 該跑窗內"
    ck(abs(_d3["by_state"].get("維修", 0.0) - (_in3 / 60.0)) < 1e-6
       and abs(_d3["stop_h"] - (_in3 / 60.0)) < 1e-6,
       f"⑥c 跨窗邊界段：by_state 只計窗內那半（窗外不進格子主字），got {_d3['by_state']}")
    ck(len(_er3["bar"]["stop"]) == 1
       and abs(_er3["bar"]["stop"][0][1] - round(900 / 43.20, 3)) < 1e-9
       and abs(_er3["bar"]["stop"][0][2] - round(_in3 / 43.20, 3)) < 1e-9,
       f"⑥c bar 停機片段同樣只畫窗內那半（與格子同一份裁切器），got {_er3['bar']['stop']}")

    # ⑦ build_row／build_board 接線：ctx 在 ⇒ 換軸；不在 ⇒ 舊 24h 軸（inert fallback）
    # ⚠ 2026-09-04：`EID-13219`（Frame#3 Kestrel）裁進 ZA-1 綑後，`EQMT_CATALOG[:6]` 已**整段
    #   都在名冊內** ⇒ 下面 C-RW9 的「非名冊機台」負斷言會撈不到列（原本是靠目錄第 2 列碰巧
    #   不在名冊）。改成「前 6 台 ＋ 目錄裡第一台非名冊機」——**由名冊本身動態選**，
    #   名冊日後再增減都不會讓這根釘子靜默失效。
    _rw_cat = EQMT_CATALOG[:6] + [next(r for r in EQMT_CATALOG
                                       if r[2] not in kun_roster_eids())]
    _rw_wos = {_E0: [_rw_wo]}
    _b_off = build_board(_rw_cat, _rw_wos, _rw_we, 3, "demo", "demo")
    _b_on = build_board(_rw_cat, _rw_wos, _rw_we, 3, "demo", "demo",
                        closures=[], cal_coverage=(_rw_dw[0][1], _rw_dw[-2][2]))
    ck(_b_off["erw"] is None and _b_off["closures"] is None
       and all(r["erw"] is None and r["equiv_ph"] is None
               for a in _b_off["areas"] for r in a["rows"]),
       "⑦ inert：board.erw／closures None、逐列 erw／equiv None ⇒ 頁面走舊軸＋標示")
    _r13_off = next(r for a in _b_off["areas"] for r in a["rows"] if r["eid"] == _E0)
    _r13_on = next(r for a in _b_on["areas"] for r in a["rows"] if r["eid"] == _E0)
    ck(_r13_off["avail_pct"] is not None and _r13_on["avail_pct"] is not None
       and _r13_on["avail_pct"] < _r13_off["avail_pct"],
       "⑦ ★ 換軸方向：同樣 4h 停機、分母 24h→16.67h ⇒ 妥善率變低（以前被 24h 稀釋）")
    ck(all((r1["cells"] == r2["cells"] and r1["total_stop_h"] == r2["total_stop_h"]
            and r1["segments"] == r2["segments"])
           for a1, a2 in zip(_b_off["areas"], _b_on["areas"])
           for r1, r2 in zip(a1["rows"], a2["rows"]))
       and _b_off["headline"] == _b_on["headline"],
       "⑦ 換軸只動 A%/Δ/erw/equiv——cells／stop_h／segments／headline 逐位元不動"
       "（heatmap 是工單事實、不隨分母換軸）")
    _r_nros = next(r for a in _b_on["areas"] for r in a["rows"]
                   if r["eid"] not in kun_roster_eids())
    ck(_r_nros["avail_pct"] is None and _r_nros["erw"] is None,
       "⑦ C-RW9：換軸後非名冊機台 A%＝None（顯「—」、永不外推）")
    ck(_b_on["erw"]["n_days"] == 2 and _b_on["erw"]["n_days_total"] == 3
       and "slots_uncovered" in _b_on["erw"]["degraded"],
       f"⑦ board.erw 揭露材料（N/M 天＋degraded 聯集），got {_b_on['erw']}")

    # ⑧ 長停列：換軸後照樣全「—」
    _b_dz = build_board(_rw_cat, _rw_wos, _rw_we, 3, "demo", "demo",
                        closures=[], cal_coverage=(_rw_dw[0][1], _rw_dw[-2][2]),
                        dormant_by_eid={_rw_cat[0][2]: _dz})
    _r_dz = _b_dz["areas"][0]["rows"][0]
    ck(_r_dz["avail_pct"] is None and _r_dz["erw"] is None and _r_dz["equiv_ph"] is None,
       "⑧ 長停列：A%／erw／equiv 全「—」（對登記不排生產的機器印任何指標都是替它背書）")

    # ⑨ §6.2 ① 產出當量：rate ∧ 窗內停機>0 ⇒ int；無 rate／零停機 ⇒ None
    _b_rate = build_board(_rw_cat, _rw_wos, _rw_we, 3, "demo", "demo",
                          closures=[], cal_coverage=(_rw_dw[0][1], _rw_dw[-2][2]),
                          rates_by_eid={_E0: 8.0})
    _r13_r = next(r for a in _b_rate["areas"] for r in a["rows"] if r["eid"] == _E0)
    ck(_r13_r["equiv_ph"] == int(round(_r13_r["erw"]["stop_h"] * 8.0)) and _r13_r["equiv_ph"] == 32,
       f"⑨ 當量＝窗內停機 4h × 8 顆/機台小時 ＝ 32PH，got {_r13_r['equiv_ph']}")
    ck(all(r["equiv_ph"] is None for a in _b_on["areas"] for r in a["rows"]),
       "⑨ 無 rate ⇒ 全列 None（不猜速率）")
    # ⑨s §6.3-b 座係數（`#110` (2)(4)）：同輸入＋`seat_factors` ⇒ 當量按座折；不給 ⇒ 32PH 不變
    _b_seat = build_board(_rw_cat, _rw_wos, _rw_we, 3, "demo", "demo",
                          closures=[], cal_coverage=(_rw_dw[0][1], _rw_dw[-2][2]),
                          rates_by_eid={_E0: 8.0},
                          seat_factors={_E0: {
                              "win": 0.25, "days": {d[0]: 0.25 for d in _rw_dw},
                              "k": 3, "n": 12, "wos": [{"wo_no": 1, "units": ["nest:2"], "k": 3}]}})
    _r13_s = next(r for a in _b_seat["areas"] for r in a["rows"] if r["eid"] == _E0)
    ck(_r13_s["equiv_ph"] == 8 and _r13_s["seat_f"] == 0.25 and _r13_s["seat_k"] == 3
       and _r13_s["seat_n"] == 12 and _r13_s["seat_wos"] == [{"wo_no": 1, "units": ["nest:2"], "k": 3}],
       f"⑨s 座係數：4h × 8 × 0.25 ＝ 8PH ＋四個 additive 欄，got "
       f"{(_r13_s['equiv_ph'], _r13_s['seat_f'], _r13_s['seat_k'], _r13_s['seat_n'])}")
    ck(_r13_r["equiv_ph"] == 32 and _r13_r["seat_f"] is None and _r13_r["seat_k"] is None
       and _r13_r["seat_n"] is None and _r13_r["seat_wos"] is None,
       "⑨s 不給 seat_factors ⇒ 32PH 不變、四欄皆 None（逐位元同接線前）")
    ck(_r13_s["avail_pct"] == _r13_r["avail_pct"]
       and _r13_s["erw"]["stop_h"] == _r13_r["erw"]["stop_h"]
       and _r13_s["cells"] == _r13_r["cells"] and _r13_s["segments"] == _r13_r["segments"]
       and _r13_s["total_stop_h"] == _r13_r["total_stop_h"]
       and _r13_s["avail_delta_pp"] == _r13_r["avail_delta_pp"],
       "⑨s C-CF8／C-RW24：座係數只碰當量——A%／Δ／stop_h／cells／segments 逐位元不動")
    ck(_b_seat["areas"] and all(
        r["seat_f"] is None and r["seat_k"] is None and r["seat_n"] is None
        and r["seat_wos"] is None
        for a in _b_seat["areas"] for r in a["rows"] if r["eid"] != _E0),
       "⑨s 沒有條目的 eid ⇒ 四欄 None ⇒ 那些列逐位元不變")
    _dr_s = _r13_s["erw"]["days"]
    ck(_dr_s[_rw_dw[0][0]]["equiv_ph"] == 8 and _dr_s[_rw_dw[0][0]]["seat_f"] == 0.25
       and _dr_s[_rw_dw[0][0]]["seat_n"] == 12,
       f"⑨s 逐日格同樣按座（4h×8×0.25＝8PH）＋當日係數進格，got "
       f"{ {k: (v['equiv_ph'], v.get('seat_f')) for k, v in _dr_s.items()} }")
    # ⑨s2 `inherit_child_rates`：子繼承母、母無速率時子仍無、已有速率不覆蓋
    _ir = inherit_child_rates({_PB: 11.0, _I1: 3.0}, {_PB: "2026-07-05"})
    ck(_ir[_I1] == 3.0 and _ir[_I2] == 11.0 and _ir[_I3] == 11.0
       and _K1a not in _ir,
       f"⑨s2 速率繼承：子沒速率才補母機的、已有不覆蓋、母沒速率的那家一個都不補，got {_ir}")
    _ird = {_PB: "2026-07-05"}
    inherit_child_rates({_PB: 11.0}, _ird)
    ck(_ird.get(_I2) == "2026-07-05", f"⑨s2 速率日同步繼承，got {_ird}")
    # ⑨s2b WP-C2：`rate_kind` 同形跟著繼承（純顯示欄、不參與計算）；不傳＝逐位元同前
    _ik = {_PA: "seat"}
    _ir2 = inherit_child_rates({_PA: 16.0}, None, _ik)
    ck(_ir2[_K1a] == 16.0 and _ik.get(_K1a) == "seat"
       and inherit_child_rates({_PA: 16.0}) == {
           e: 16.0 for e in (_PA,) + tuple(_wcap.PARENT_EXPANSION[_PA])},
       f"⑨s2b 尺別隨速率一起繼承（A 尺下母機值＝r_seat×n、子列 f=1/n ⇒ 乘開恰為 r_seat），"
       f"got rates={_ir2} kinds={_ik}")
    # ⑨s2c WP-C2：`rate_kind_by_eid` 只是 board 層附掛，**A%／當量／座係數一個位元不動**
    _bk_off = build_board(_rw_cat, _rw_wos, _rw_we, 3, "demo", "demo",
                          closures=[], cal_coverage=(_rw_dw[0][1], _rw_dw[-2][2]),
                          rates_by_eid={_E0: 8.0})
    _bk_on = build_board(_rw_cat, _rw_wos, _rw_we, 3, "demo", "demo",
                         closures=[], cal_coverage=(_rw_dw[0][1], _rw_dw[-2][2]),
                         rates_by_eid={_E0: 8.0},
                         rate_kind_by_eid={_E0: "seat"})
    _bk_r = {r["eid"]: r for a in _bk_on["areas"] for r in a["rows"]}
    _bk_r0 = {r["eid"]: r for a in _bk_off["areas"] for r in a["rows"]}
    ck(_bk_r[_E0]["rate_kind"] == "seat"
       and _bk_r0[_E0]["rate_kind"] is None
       and all({k: v for k, v in _bk_r[e].items() if k != "rate_kind"}
               == {k: v for k, v in _bk_r0[e].items() if k != "rate_kind"} for e in _bk_r),
       "⑨s2c `rate_kind_by_eid` 只附掛顯示欄：除該欄外整塊板逐位元相同（C-RW25 的負斷言）")
    ck(all("rate_kind" in r for a in _bk_off["areas"] for r in a["rows"]),
       "⑨s2c 鍵恆存在（模板不必分辨『沒鍵』與 None）")
    ck(demo_board(3)["areas"][0]["rows"][0]["rate_kind"] in (None, "mh_blend"),
       "⑨s2c demo 恆走 B 尺（合成速率不冒充量出來的每座速率）")
    # ⑨s3 `seat_factors_for`：只對有座的機台與子槽位列產出條目；形③ 子列恆 1/4
    _sfw = seat_factors_for(_rw_cat, _rw_wos, _rw_dw, _rw_we)
    ck(set(_sfw) == {e for _a, _n, e in _rw_cat if _wcap.installed_units(e)}
       and _sfw.get(_E0, {}).get("n") == len(_wcap.installed_units(_E0)),
       f"⑨s3 只有名冊上有座的列拿得到條目（其餘＝1.0、逐位元不變），got {sorted(_sfw)}")
    _cat_cln = [("zc", "CLN Gen2 DI Bay#1", _I1)]
    _sfc = seat_factors_for(_cat_cln, {}, _rw_dw, _rw_we)[_I1]
    _nIC = len(_wcap.PARENT_EXPANSION[_PB])          # WP-10d：形③ 母機座數由名冊推導（不寫死）
    _nNE = len(_wcap.installed_units(_NE))           # 有座機台的座數（由名冊推導、不寫死）
    ck(_sfc["win"] == 1.0 / _nIC and _sfc["n"] == _nIC and _sfc["k"] == 1
       and all(v == 1.0 / _nIC for v in _sfc["days"].values()),
       f"⑨s3 形③ 子槽位列恆 1/n（母機 n 座、這一列只佔一座＝Chris 2026-09-12 追問那件），"
       f"got {_sfc}")
    # ⑨s4 兩條指名座的路（A10 `sub_units` ／ NOTE-71 `seat_unit`）同值、聯集去重
    _seat_ws = _rw_dw[0][1]
    _seat_t = lambda h: (_seat_ws + _dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")

    def _seat_wo(scope_events=None, **extra):
        # ⚠ `scope_events` 來自 detail、**不在 `normalize_wo` 的白名單內** ⇒ 必須在
        #   normalize **之後**掛上去（與 `run_smoke` (al) 同一個寫法；寫在 raw 裡會被丟掉）。
        raw = {"work_order_no": 970001, "asset_id": _NE, "work_type": "REACTIVE",
               "opened_at": _seat_t(1), "closed_at": _seat_t(3), "status_history": []}
        raw.update(extra)
        out = w8.normalize_wo(raw)
        if scope_events is not None:
            out["scope_events"] = scope_events
        return out

    _sc_ev = [{"ts": _seat_t(1.5), "scope": [_NE], "sub_units": ["nest:2"]}]
    _cat_lam = [("za", "Press#1", _NE)]
    _f_a10 = seat_factors_for(_cat_lam, {_NE: [_seat_wo(scope_events=_sc_ev)]},
                              _rw_dw, _rw_we)[_NE]
    _f_n71 = seat_factors_for(_cat_lam, {_NE: [_seat_wo(asset_id=(_NE + "-N2"))]},
                              _rw_dw, _rw_we)[_NE]
    ck(_f_a10["win"] == 1.0 / _nNE and _f_a10["k"] == 1 and _f_a10["n"] == _nNE
       and _f_n71["win"] == 1.0 / _nNE and _f_n71["k"] == 1 and _f_n71["n"] == _nNE,
       f"⑨s4 兩條指名座路各自都吃、同值＝1/n，got a10={_f_a10} n71={_f_n71}")
    _f_both = seat_factors_for(
        _cat_lam, {_NE: [_seat_wo(asset_id=(_NE + "-N2"), scope_events=_sc_ev)]},
        _rw_dw, _rw_we)[_NE]
    ck(_f_both["win"] == 1.0 / _nNE and _f_both["wos"] == [{"wo_no": 970001, "units": ["nest:2"], "k": 1}],
       f"⑨s4 兩路同時指同一座 ⇒ 聯集去重、仍是 1/n，got {_f_both}")
    _f_whole = seat_factors_for(_cat_lam, {_NE: [_seat_wo()]},
                               _rw_dw, _rw_we)[_NE]
    ck(_f_whole["win"] == 1.0 and _f_whole["k"] == _nNE and _f_whole["wos"][0]["units"] == [],
       f"⑨s4 整機單 ⇒ active/installed（n 座全在役＝1.0、不印後綴），got {_f_whole}")
    # ⑨b §6.3 ①：**逐日**當量進格（≥0.05h ∧ rate ⇒ int；否則 None）。⚠ 永不做跨格 Σ（C-RW13）
    _dr = _r13_r["erw"]["days"]
    ck(_dr[_rw_dw[0][0]]["equiv_ph"] == 32
       and all(v["equiv_ph"] is None for k, v in _dr.items() if (v["stop_h"] or 0.0) < 0.05),
       f"⑨b 逐日當量：4h 停機日＝32PH、零停機日 None，got "
       f"{ {k: v['equiv_ph'] for k, v in _dr.items()} }")
    ck(all((v.get("equiv_ph") is None)
           for a in _b_on["areas"] for r in a["rows"] if r["erw"]
           for v in r["erw"]["days"].values()),
       "⑨b 無 rate ⇒ 逐日 equiv_ph 鍵仍在但恆 None（模板不必分辨『沒鍵』與『不換算』）")

    # ⑩ closures_in_window：裁窗、帶過 uncertain_eids、排序（drill 收工線材料）
    _uw_s, _uw_e = D(2026, 8, 1, 0, 0), D(2026, 8, 2, 0, 0)
    _ciw = closures_in_window(
        [{"start": D(2026, 8, 1, 20), "end": D(2026, 8, 2, 4), "uncertain_eids": ("E1",)},
         {"start": D(2026, 7, 1), "end": D(2026, 7, 2)}], _uw_s, _uw_e)
    ck(_ciw == [{"start": D(2026, 8, 1, 20), "end": _uw_e, "uncertain_eids": ("E1",)}],
       f"closures_in_window：裁到窗內、窗外整段丟、uncertain 原樣帶過，got {_ciw}")

    # ⑪ demo lane：確定性、三層階梯可目視（tier1 兩例＋tier3 一例）、非名冊「—」
    _dpw = demo_board(5)
    ck(_dpw["erw"] and _dpw["erw"]["n_closures"] == 2 and _dpw["erw"]["n_uncertain"] == 1,
       f"⑪ demo：兩段合成 closure、其中一段 uncertain，got {_dpw.get('erw')}")
    ck(demo_closures(D(2026, 7, 1), _DEMO_ANCHOR) == demo_closures(D(2026, 7, 1), _DEMO_ANCHOR)
       and demo_erw_inputs(D(2026, 7, 1), _DEMO_ANCHOR) == demo_erw_inputs(D(2026, 7, 1), _DEMO_ANCHOR),
       "⑪ demo erw 輸入確定性（同輸入同輸出）")
    _ros = kun_roster_eids()
    _dpw_rows = [r for a in _dpw["areas"] for r in a["rows"]]
    ck(any(r["avail_pct"] is not None and r["erw"] is not None
           for r in _dpw_rows if r["eid"] in _ros)
       and all(r["avail_pct"] is None for r in _dpw_rows
               if r["eid"] not in _ros and not r.get("dormant")),
       "⑪ demo：名冊機台換軸有值、非名冊一律「—」（一眼可驗收）")
    # ⚠ 階梯紀錄自 §6.3 ③ 起讀 `erw["days"][ds]["gaps"]`（bar 換成整窗連排、不再存單日 gaps）
    def _tiers(row_):
        out = {}
        for _d in (row_["erw"]["days"] or {}).values():
            for g in _d.get("gaps") or ():
                if g.get("tier"):
                    out[g["name"]] = g["tier"]
        return out

    if not _pub_tree:   # demo_erw_inputs 的訊號劇本鎖在具名名冊機台（Wirebond 25983／CAL 綑）；公開樹的合成名冊沒有這組角色 ⇒ 該兩根 tier 釘不跑（R-P11）
        _d_wb = next(r for r in _dpw_rows if r["eid"] == "EID-25590")
        _d_wb_gaps = _tiers(_d_wb)
        ck(_d_wb_gaps.get("outside_ZAB_1") == 1,
           f"⑪ demo tier1：Wirebond 同機種 25983 訊號擴 25590 的窗，got {_d_wb_gaps}")
        _d_cal = next(r for r in _dpw_rows if r["eid"] == _C2)
        _d_cal_gaps = _tiers(_d_cal)
        ck(_d_cal_gaps.get("outside_ZC_1") == 3,
           f"⑪ demo tier3：CAL 綑 slot 全靜＋人力桶>0 ⇒ 兜底整段，got {_d_cal_gaps}")
    ck(all(r["avail_pct"] is None or 0.0 <= r["avail_pct"] <= 100.0 for r in _dpw_rows),
       "⑪ demo：妥善率恆落 0–100（結構性：停機∩窗 ≤ 窗）")
    ck(any(r["equiv_ph"] is not None and r["equiv_ph"] > 0 for r in _dpw_rows),
       "⑪ demo：至少一列有產出當量（demo_rates_by_eid 接通、可目視驗收）")
    ck(kun_roster_eids() and all(isinstance(e, str) and e.startswith("EID-")
                                 for e in kun_roster_eids()),
       "綑名冊唯一出處＝`_probe_kun_activity.KUN_ROSTER`（本檔不另抄一份）")

    # ⑫ §6.5（2026-08-24）：`/utilization`／`/station`／`/machine` 換軸的兩支材料函式
    _uw_a, _uw_b = _rw_dw[0][1], _rw_we                  # 與 ②–⑤ 同一個窗、同一個 ctx
    _mw = erw_machine_windows([_E0, _EL], _uw_a, _uw_b, _rw_ctx)
    ck(_EL not in _mw, "⑫ C-RW9：非名冊 EID 不出現在逐機材料裡（呼叫端顯「—」、不外推）")
    # ★ QG-13：bar／圓餅／chips 永不各算各的 ⇒ 逐機 sec 必須逐位對上既有 `erw_full_seconds`
    _fs, _fm = erw_full_seconds([_E0], _uw_a, _uw_b, _rw_ctx)
    ck(abs(_mw[_E0]["sec"] - _fs) < 1e-6,
       f"⑫ ★ Σ|RW| 逐位等於 erw_full_seconds（同一把尺、零第二份口徑），"
       f"got {_mw[_E0]['sec']} vs {_fs}")
    ck(not _mw[_E0]["uncov"], "⑫ 兩個欄日都被涵蓋 ⇒ uncov 空")
    # 未涵蓋日：只蓋第一個欄日 ⇒ 第二日無主張。**分母仍計入**（低估側，與 erw_full_seconds 同）、
    # 但另存 uncov 讓呈現層畫成「不知道」而非「不該跑」（§6.3 ③ 反模式 2）。
    _ctx_u = erw_prepare(_rw_dw, [], (_rw_dw[0][1], _rw_dw[0][2]))
    _mw_u = erw_machine_windows([_E0], _uw_a, _uw_b, _ctx_u)[_E0]
    _fs_u, _ = erw_full_seconds([_E0], _uw_a, _uw_b, _ctx_u)
    ck(len(_mw_u["uncov"]) == 1 and abs(_mw_u["sec"] - _fs_u) < 1e-6
       and all(wrw.iv_total(wrw.iv_intersect([u], _mw_u["win"])) == (u[1] - u[0])
               for u in _mw_u["uncov"]),
       f"⑫ 未涵蓋日：計入分母（低估側、逐位同 erw_full_seconds）但另存 uncov 供呈現層畫「無主張」，"
       f"got uncov={_mw_u['uncov']} sec={_mw_u['sec']} vs {_fs_u}")
    ck(_mw[_E0]["marks"] and all(0 < m < _mw[_E0]["span"]
                                         for m in _mw[_E0]["marks"]),
       "⑫ 收工線刻度＝該跑窗段邊界（窗緣本身不畫）")
    ck(erw_machine_windows([_E0], _uw_a, _uw_b, None) == {},
       "⑫ ctx 缺 ⇒ {}（呼叫端整框 inert、退回舊 24h 軸並標示）")

    # ⑫c ★ #182-P1（2026-09-25；`_INVEST-RW-DENOMINATOR-20260925.md` §1.3）：殘欄錨點
    #   `erw_prepare` 的 `day_start` 一律＝該欄生產日 08:00（不是被裁過的欄起點）。
    #   日期**相對 today**（[[smoke-fixture-date-anchors]]），不寫死。
    _td_c = _dt.date.today() - _dt.timedelta(days=10)
    _A_c = _dt.datetime(_td_c.year, _td_c.month, _td_c.day)            # D 00:00
    # (i) 調查檔的純函式反例：ZAB EID-12724、窗 [D 20:30, D+1 08:00)、無 slot／人力（降級形＝
    #     班次窗 [0,1000)＝08:00–00:40）。對齊答案＝[750,1000)＝**250 分**；舊錨（20:30 當 0 點）＝690。
    _ws_c = _A_c + _dt.timedelta(hours=20, minutes=30)
    _we_c = _A_c + _dt.timedelta(days=1, hours=8)
    _ctx_c = erw_prepare(prod_day_windows(_ws_c, _we_c), [], (_ws_c, _we_c))
    _ds_c = f"{_td_c.month:02d}-{_td_c.day:02d}"
    ck(_ctx_c is not None and list(_ctx_c["days"]) == [_ds_c]
       and _ctx_c["days"][_ds_c]["day_start"] == _A_c + _dt.timedelta(hours=8),
       f"⑫c ★ 殘欄的 day_start＝生產日 08:00（不是欄起點 20:30），got "
       f"{(_ctx_c or {}).get('days')}")
    _mw_c = erw_machine_windows([_E0], _ws_c, _we_c, _ctx_c)[_E0]
    _fs_c, _ = erw_full_seconds([_E0], _ws_c, _we_c, _ctx_c)
    _c250 = _ZABM - 750                                 # 對齊答案＝[750, 窗尾)（私有班表＝250 分）；舊錨（20:30 當 0 點）會多算
    ck(abs(_mw_c["sec"] - _c250 * 60.0) < 1e-6 and abs(_fs_c - _c250 * 60.0) < 1e-6,
       f"⑫c ★★ 反例 EID-12724 [20:30, 08:00)＝{_c250} 分（不是舊錨的長度），"
       f"got mwin={_mw_c['sec'] / 60:.1f} full={_fs_c / 60:.1f}")
    # (ii) 一般形（§1.3 第 3 條）：任意起點 ws 的結果 ≡「對齊超集窗算完 ∩ [ws, we)」。
    #      三源 raw 取超集窗的 demo 合成（兩邊餵同一份），含 certain／uncertain closure。
    _S_c = _A_c + _dt.timedelta(hours=8) - _dt.timedelta(days=1)       # D−1 08:00（對齊）
    _E_c = _A_c + _dt.timedelta(days=2, hours=8)                       # D+2 08:00
    _sl_c, _mp_c = demo_erw_inputs(_S_c, _E_c)
    _cl_c = demo_closures(_S_c, _E_c)
    _ctxS = erw_prepare(prod_day_windows(_S_c, _E_c), _cl_c, (_S_c, _E_c), _sl_c, _mp_c)
    _ros_c = sorted(kun_roster_eids())
    _mwS = erw_machine_windows(_ros_c, _S_c, _E_c, _ctxS)
    _bad_c = []
    for _h, _m in ((0, 0), (3, 0), (7, 59), (8, 0), (13, 15), (20, 30), (23, 59)):
        _ws = _A_c + _dt.timedelta(hours=_h, minutes=_m)
        _off = (_ws - _S_c).total_seconds()
        _span = (_E_c - _ws).total_seconds()
        _ctxW = erw_prepare(prod_day_windows(_ws, _E_c), _cl_c, (_S_c, _E_c), _sl_c, _mp_c)
        _mwW = erw_machine_windows(_ros_c, _ws, _E_c, _ctxW)
        for _e in _ros_c:
            _want = wrw.iv_intersect([(a - _off, b - _off) for a, b in _mwS[_e]["win"]],
                                     [(0.0, _span)])
            _got = _mwW[_e]["win"]
            if (len(_want) != len(_got)
                    or any(abs(a - c) > 1e-6 or abs(b - d) > 1e-6
                           for (a, b), (c, d) in zip(_want, _got))):
                _bad_c.append((f"{_h:02d}:{_m:02d}", _e, wrw.iv_total(_want) / 60,
                               wrw.iv_total(_got) / 60))
    ck(not _bad_c and len(_ros_c) > 0,
       f"⑫c ★ 任意起點（00:00／03:00／07:59／08:00／13:15／20:30／23:59）× 全綑名冊："
       f"結果 ≡ 對齊超集窗 ∩ [ws,we)（逐區間），bad={_bad_c[:4]}")
    # (iii) 既有行為釘：起點正好 08:00 的欄（`day_windows`、對齊的 `prod_day_windows`）
    #       day_start 仍＝欄起點、讀的 slot 日不變 ⇒ 對齊窗的輸出逐位元同修前（②–⑫ 的
    #       golden 全建在對齊窗上、一個數字都沒改）。
    _aligned = list(day_windows(_E_c - _dt.timedelta(hours=3), 4)) + prod_day_windows(_S_c, _E_c)
    _ctxA = erw_prepare(_aligned, _cl_c, (_S_c, _E_c), _sl_c, _mp_c)
    ck(all(_ctxA["days"][ds]["day_start"] == d_s
           for ds, d_s, d_e in _aligned if ds in _ctxA["days"])
       and len(_ctxA["days"]) >= 3,
       "⑫c 既有行為：08:00 起點的欄 day_start ≡ 欄起點（修前修後同一個值 ⇒ 逐位元不變）")
    # (iv) `prod_day_window`＝日帳單日窗唯一出處（`app.daily_body` ＋ `_backfill_ledger`）
    def _old_daily_win(now):                     # 修前 `app.daily_body` A6 的內嵌寫法（逐字）
        t0 = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now.hour < 8:
            t0 -= _dt.timedelta(days=1)
        return t0 - _dt.timedelta(days=1), t0

    _nows = [_A_c + _dt.timedelta(minutes=30 * k + 7) for k in range(96)]   # 兩天、每半小時
    ck(all(prod_day_window(now=n) == _old_daily_win(n) for n in _nows),
       "⑫c prod_day_window(now=) ≡ 修前 `app.daily_body` 內嵌算法（96 個時刻逐位元同）")
    _Dn = _A_c + _dt.timedelta(days=1)
    ck(prod_day_window(now=_Dn + _dt.timedelta(hours=8)) == prod_day_window(_td_c)
       and prod_day_window(now=_Dn + _dt.timedelta(hours=7, minutes=59))
       == prod_day_window(_td_c - _dt.timedelta(days=1))
       and prod_day_window(_td_c.strftime("%Y-%m-%d"))
       == (_A_c + _dt.timedelta(hours=8), _A_c + _dt.timedelta(days=1, hours=8)),
       "⑫c prod_day_window：D+1 08:00 ⇒ D；D+1 07:59 ⇒ D−1；'YYYY-MM-DD' ⇒ [D 08:00, D+1 08:00)")
    try:
        prod_day_window(_A_c)
        _te = False
    except TypeError:
        _te = True
    ck(_te, "⑫c prod_day_window 不收 datetime 當 day（時刻 vs 生產日會默默錯一天；要推請用 now=）")

    # ⑫b 圓餅換軸：不明→未解釋、未排生產不進 by_state、分母≡Σ|RW|
    def _mkm(eid, recs):
        return {"eid": eid, "desc": "", "records": recs}

    _d0 = _rw_dw[0][1]
    _u_recs = [{"state": "生產", "conf": "high", "start": D(2026, 8, 10, 9, 0),
                "end": D(2026, 8, 10, 11, 0), "sec": 7200.0},
               {"state": "維修", "conf": "high", "start": D(2026, 8, 10, 11, 0),
                "end": D(2026, 8, 10, 12, 0), "sec": 3600.0},
               # 這段完全落在 ZAB 班次窗外（00:40–08:00）⇒ 一秒都不得進圓餅
               {"state": "生產", "conf": "high", "start": D(2026, 8, 11, 2, 0),
                "end": D(2026, 8, 11, 4, 0), "sec": 7200.0},
               {"state": "不明", "conf": "low", "start": D(2026, 8, 10, 12, 0),
                "end": D(2026, 8, 10, 13, 0), "sec": 3600.0}]
    _sp = erw_state_split([_mkm(_E0, _u_recs), _mkm(_EL, _u_recs)],
                          _uw_a, _uw_b, _rw_ctx)
    ck(_sp["n_machines"] == 1 and _sp["eids"] == [_E0],
       f"⑫b 只算「本站機隊 ∩ 綑名冊」（§4.2），got {_sp['n_machines']}")
    ck(abs(_sp["rw_sec"] - _fs) < 1e-6,
       f"⑫b 圓餅分母 ≡ chips 分母（同一份資料），got {_sp['rw_sec']} vs {_fs}")
    ck(abs(_sp["prod_sec"] - 7200.0) < 1e-6,
       f"⑫b 未排生產時段的生產不進圓餅（窗外 2h 被裁掉），got {_sp['prod_sec']}")
    ck("不明" not in _sp["by_state"] and _sp["by_state"].get(UNEXPLAINED_STATE),
       "⑫b 不明 ∩ 應生產時段 ⇒ 併入「該跑沒跑」（未解釋）；canonical 不明不再出現在圓餅")
    ck(abs(sum(_sp["by_state"].values()) - _sp["rw_sec"]) < 1e-6,
       f"⑫b 恆等式：Σ by_state ≡ Σ|RW|（record 未覆蓋的殘量也算未解釋），"
       f"got {sum(_sp['by_state'].values())} vs {_sp['rw_sec']}")
    ck(abs(_sp["by_state"].get("維修", 0.0) - 3600.0) < 1e-6,
       "⑫b 工單四色停機照樣進圓餅（cmms 分類本來就該看得見）")
    ck(erw_state_split([_mkm(_EL, _u_recs)], _uw_a, _uw_b, _rw_ctx) is None
       and erw_state_split([_mkm(_E0, _u_recs)], _uw_a, _uw_b, None) is None,
       "⑫b 零名冊交集／ctx 缺 ⇒ None（呼叫端退回舊 24h 軸並顯著標示，不靜默）")
    # ★ C-RW4 迴歸釘的圓餅形：把生產全換成維修 ⇒ 分母恆定、未解釋不變
    _sp2 = erw_state_split([_mkm(_E0, [dict(r, state="維修") for r in _u_recs])],
                           _uw_a, _uw_b, _rw_ctx)
    ck(abs(_sp2["rw_sec"] - _sp["rw_sec"]) < 1e-9 and _sp2["prod_sec"] == 0.0,
       "⑫b ★ C-RW4：機台自身訊號變化不動分母（停越久不得越好看）")

    # ---- (S) WP-D 預設摺疊「沒什麼好講」的列（2026-09-02；規格＝本檔 7d 節）------------
    #      判定是純函式、摺疊是顯示層：這一節同時釘「摺對了」與「什麼都沒被改到」。
    _vdw = day_windows(we, 5)
    _vent = ("za", "AOI Frame Straight", _C3)

    def _vrow(**kw):
        # 預設 `mes_measurable=True`＝「普查看得到這台」；要驗 `no_mes` 的 case 自己傳 False。
        kw.setdefault("mes_measurable", True)
        return build_row(_vent, kw.pop("wos", []), _vdw, we, "demo", **kw)

    # (S0) 兩顆旗**不是同一件事**（審查 HIGH-1 的根因）：`mes_covered`＝這次探測有沒有 key、
    #      `mes_measurable`＝730 天普查看過這台沒有。live 的 `mes_by_eid` 對名冊全鋪 key
    #      ⇒ 拿 `mes_covered` 判覆蓋，`no_mes` 在 live 永遠是死碼。
    _alias_eids = frozenset(a for _als in wtags.ALIAS_OF.values() for a in _als)
    _folded_main = frozenset(m for m, _als in wtags.ALIAS_OF.items()
                             if any(a in wtags.PRODUCT_TAGS for a in _als))
    ck(MES_MEASURABLE_EIDS == ((frozenset(wtags.PRODUCT_TAGS) | _folded_main) - _alias_eids)
       and len(MES_MEASURABLE_EIDS) > 50,
       "S0a 可量測集合＝730 天普查的 EID 鍵集合（靜態、與當次探測無關）＋別名折疊")
    # ★ S0a2（2026-09-03）：形④改名型主 EID 必須在集合裡、別名必須不在。
    #   純普查表的鍵是別名（`38041`／`38072`），主 EID `38106` 不在其中；名冊第 39 站那一列
    #   卻是主 EID ⇒ 不折疊就會被 `no_mes` 摺掉並定性成「MES 結構上量不到」（它其實有大量觀測段）。
    #   ⚠ 判準是**鍵**、不是 `FOLDED_TAGS` 成員資格——後者要求別名 `origin=="observed"`，
    #     別名全是 default 的主 EID 一樣量得到（只是標籤沒過門檻），不得因此落 `no_mes`。
    ck(all(m in MES_MEASURABLE_EIDS for m in _folded_main)
       and all(m in MES_MEASURABLE_EIDS for m in wtags.FOLDED_TAGS)
       and _alias_eids and not (_alias_eids & MES_MEASURABLE_EIDS),
       f"★ S0a2 別名在普查表裡的折疊主 EID 一律可測（含 origin 全 default 的情形）、"
       f"別名不獨立成員（主 {sorted(_folded_main)}／別名 {sorted(_alias_eids)}）")
    ck(frozenset(wtags.FOLDED_TAGS) <= _folded_main,
       "S0a2b 純鍵判準是 `FOLDED_TAGS` 的超集（觀測到標籤 ⇒ 必然掃得到鍵，反之不必然）")
    ck(_FM in MES_MEASURABLE_EIDS and _FR not in MES_MEASURABLE_EIDS
       and _FD not in MES_MEASURABLE_EIDS
       and any(e == _FM for _a, _n, e in EQMT_CATALOG)
       and not any(e in (_FR, _FD) for _a, _n, e in EQMT_CATALOG),
       "★ S0a3 第 39 站具名迴歸釘：`EID-38106` 量得到（久靜⇒沉默機、不再被 no_mes 摺）、"
       "兩顆別名既不在名冊也不在集合")
    ck(_pub_tree or sum(1 for _a, _n, e in EQMT_CATALOG if e not in MES_MEASURABLE_EIDS)
       > _pp.PINS["eqmeeting.unmeasurable_floor_exclusive"],   # 公開樹的合成普查涵蓋整個合成名冊（私有名冊才有治具／烤箱那批普查看不到的機台）
       "S0b 名冊裡真的有一批機台普查看不到（治具/烤箱/顯微鏡）——否則 no_mes 分支等於死碼")
    _v_flags = _vrow(mes_available=True, mes_covered=True, mes_measurable=False, mes_last_h=None)
    ck(_v_flags["mes_covered"] is True and _v_flags["mes_measurable"] is False
       and row_visibility(_v_flags, True) == ("collapsed", "no_mes"),
       "★ S0c 兩顆旗分岔時以 `mes_measurable` 為準（live 形：covered 恆 True 也不得判成沉默機）")
    ck(_v_flags["now"].get("mes_nosig") is True,
       "S0d …而 Now 欄仍照 `mes_covered` 走（規則④不受影響、UP 三形零改動）")

    # (S1) 四種無工單列各一 —— 順序＝規格順序，reason_key 是穩定字面值
    _v_prod = _vrow(mes_available=True, mes_covered=True, mes_last_h=1.0)
    ck(row_visibility(_v_prod, True) == ("collapsed", "mes_prod"),
       "S1a 無工單＋MES 近期有生產＝安靜的健康機 ⇒ 摺")
    _v_nomes = _vrow(mes_available=True, mes_covered=True, mes_measurable=False, mes_last_h=None)
    ck(row_visibility(_v_nomes, True) == ("collapsed", "no_mes"),
       "S1b 無工單＋普查量不到（治具/烤箱/顯微鏡那類）⇒ 摺")
    _v_dorm = _vrow(mes_available=True, mes_covered=True, mes_last_h=1.0, dormant=DZ)
    ck(row_visibility(_v_dorm, True) == ("collapsed", "dormant"),
       "S1c ◌ 長停 ⇒ 摺（且**排在 MES 判定之前**：登記是事實、量測只是佐證）")
    _v_nosig = _vrow(mes_available=True, mes_covered=True, mes_last_h=None)
    ck(row_visibility(_v_nosig, True) == ("shown", "silent"),
       "★ S1d 沉默機（普查量得到卻整窗無生產、又無工單）⇒ **絕不摺**（靜默排除＝獎勵不修機器）")
    _v_stale = _vrow(mes_available=True, mes_covered=True, mes_last_h=QUIET_H + 1.0)
    ck(_v_stale["now"]["stale"] is True
       and row_visibility(_v_stale, True) == ("shown", "silent"),
       "★ S1e 久靜（>QUIET_H）且無工單 ⇒ 同屬沉默機、**絕不摺**")
    # 刀鋒：門檻上恰好不 stale ⇒ 回到「安靜的健康機」
    ck(row_visibility(_vrow(mes_available=True, mes_covered=True, mes_last_h=QUIET_H), True)
       == ("collapsed", "mes_prod"),
       "S1f 久靜門檻是刀鋒（age == QUIET_H 不算久靜 ⇒ 摺）")
    # ★ R45（2026-09-29）：新門檻兩側各釘一次＋本次情境（連休後 4.8 天前最後生產）
    ck(row_visibility(_vrow(mes_available=True, mes_covered=True, mes_last_h=167.0), True)
       == ("collapsed", "mes_prod")
       and row_visibility(_vrow(mes_available=True, mes_covered=True, mes_last_h=169.0), True)
       == ("shown", "silent"),
       "S1g R45 刀鋒兩側：167h 無工單 ⇒ 摺（mes_prod）、169h 無工單 ⇒ 沉默機顯示")
    _v_hol = _vrow(mes_available=True, mes_covered=True, mes_last_h=4.8 * 24)
    ck(_v_hol["mes_measurable"] and not _v_hol["segments"] and _v_hol["now"]["stale"] is False
       and row_visibility(_v_hol, True) == ("collapsed", "mes_prod"),
       "★ S1h R45 情境：最後生產 4.8 天前、無工單、在普查內 ⇒ 歸 mes_prod、被摺")

    # (S2) 有工單列一定顯示（五條充分條件各一）
    ck(row_visibility(_vrow(wos=[wo1, wo2], mes_available=True, mes_covered=True,
                            mes_last_h=1.0), True) == ("shown", "record"),
       "S2a 有計停機工單 ⇒ 顯示（不論 MES 說什麼）")
    ck(row_visibility({"now": {"bucket": "down"}, "segments": [], "total_stop_h": 0.0}, True)
       == ("shown", "record"), "S2b now.bucket=='down' 單獨即足以顯示")
    ck(row_visibility({"now": {"bucket": "up"}, "segments": [], "total_stop_h": 0.4}, True)
       == ("shown", "record"), "S2c total_stop_h>0 單獨即足以顯示")
    # ★ 繼承段（A10 母單展開）＝這一列自己沒單、但母機的單圈到它 ⇒ 有紀錄
    _v_inh = build_row(("zc", "Gen2 CAL M1", KA), [], dwR, we, "demo",
                       inherited=inh_off.get(KA), mes_available=True,
                       mes_covered=True, mes_last_h=1.0)
    ck(any(s.get("inherited_from") for s in _v_inh["segments"])
       and row_visibility(_v_inh, True) == ("shown", "record"),
       "S2d 只有繼承段（母單展開）的列也算有紀錄 ⇒ 顯示")
    # ★★ 純 uptime 列（等機台空檔/試跑）：countable 全 False、stop_h 0，但頁上**看得見**
    #     （`.eq-uptonly` 透明格＋組成條）⇒ 有段就是有紀錄。摺掉它＝把 2026-07-10
    #     「uptime 要在第一層看得到」那條裁決撤銷得比當年被否決的「只活在 drill」更徹底。
    _v_upt = build_row(("zc", "uptime only", EQM_UPT_SHOWCASE_EID),
                       [w8.normalize_wo(_demo_phase_wo(
                           49803, EQM_UPT_SHOWCASE_EID, D(2026, 7, 5, 8, 0), D(2026, 7, 5, 14, 0),
                           "REACTIVE",
                           [("OPEN", None, D(2026, 7, 5, 8, 0)),
                            ("ON_HOLD", "WAITING_MACHINE_TIME", D(2026, 7, 5, 8, 0)),
                            ("COMPLETED", None, D(2026, 7, 5, 14, 0))]))],
                       _vdw, we, "demo", mes_available=True, mes_covered=True, mes_last_h=1.0)
    ck(_v_upt["total_stop_h"] == 0 and _v_upt["segments"]
       and not any(s.get("countable", True) for s in _v_upt["segments"]),
       "S2e 前提：純 uptime 列＝有段、零計停機時數")
    ck(row_visibility(_v_upt, True) == ("shown", "record"),
       "★ S2f 純 uptime 列 ⇒ 顯示（[[equipment-page-encoding-rules]] 第 4 條：uptime 要在第一層看得到）")
    # ★★ S2g（審查 MED-1）：長停工位窗內有**已完工**的直接維修單 ⇒ 段全被移進 `dormant_dropped`
    #     ⇒ segments 空、stop_h 0、Now 不是 DOWN（單已結）——前三條判準全部落空。
    #     但這正是 C-D3 要抓的真異常（「登記說停用的機台有人開單」），揭露行還在頁上指名它。
    _v_dd = build_row(("zc", "Gen2 CAL M2", _D2),
                      [w8.normalize_wo(_demo_phase_wo(
                          49804, _D2, D(2026, 7, 5, 8, 0), D(2026, 7, 5, 14, 0), "REACTIVE",
                          [("OPEN", None, D(2026, 7, 5, 8, 0)),
                           ("IN_PROGRESS", None, D(2026, 7, 5, 8, 0)),
                           ("COMPLETED", None, D(2026, 7, 5, 14, 0))]))],
                      _vdw, we, "demo", dormant=DZ, mes_available=True,
                      mes_covered=True, mes_measurable=True, mes_last_h=1.0)
    ck(not _v_dd["segments"] and _v_dd["total_stop_h"] == 0
       and _v_dd["now"]["bucket"] == "dormant" and _v_dd["dormant_dropped"],
       "S2g 前提：長停 EID 上的已完工直接單 ⇒ 零段／零時數／Now 非 DOWN、但有排除帳")
    ck(row_visibility(_v_dd, True) == ("shown", "record"),
       "★ S2g2 有 `dormant_dropped` ⇒ 顯示——揭露行點名它，列不得同時消失（審查 MED-1）")

    # (S3) mes_available=False ⇒ 分不出沉默機 ⇒ 無工單列一律摺、但摘要要說「分不出」
    _v_nomes_board = _vrow(mes_available=False, mes_covered=False, mes_last_h=None)
    ck(row_visibility(_v_nomes_board, False) == ("collapsed", "unknown_mes"),
       "S3a 整板無 MES 證據 ⇒ 無工單列摺（reason=unknown_mes）")
    ck(row_visibility(_v_dorm, False) == ("collapsed", "dormant"),
       "S3b 無 MES 證據時長停仍走 dormant（點名不因量不到而消失）")
    ck(row_visibility(_vrow(wos=[wo1, wo2], mes_available=False), False) == ("shown", "record"),
       "S3c 無 MES 證據時有工單列照顯（工單口徑不依賴 MES＝『MES 是加分不是門檻』）")

    # (S4) collapse_summary：分桶、點名、None
    _v_rows = [_v_prod, _v_prod, _v_nomes, _v_dorm, _v_nosig, _v_upt]
    _cs = collapse_summary(_v_rows, True)
    ck(_cs["n"] == 4 and _cs["mes_prod"] == 2 and _cs["no_mes"] == 1
       and _cs["unknown"] == 0 and _cs["dormant"] == [f"AOI Frame Straight ({_C3})"]
       and _cs["unknown_mes"] is False,
       f"S4a 摘要分桶＋長停**點名（顯示名）**，got {_cs}")
    ck(_cs["n"] == _cs["mes_prod"] + _cs["no_mes"] + _cs["unknown"] + len(_cs["dormant"]),
       "S4b 摘要守恆：n ≡ 四桶之和（摺掉的列一台都不許在句子裡消失）")
    _cs0 = collapse_summary(_v_rows, False)
    ck(_cs0["n"] == 5 and _cs0["unknown"] == 4 and _cs0["unknown_mes"] is True
       and _cs0["dormant"] == [f"AOI Frame Straight ({_C3})"],
       f"S4c 無 MES 證據 ⇒ 沉默機也被摺進 unknown 桶＋旗標為真（誠實說『分不出』），got {_cs0}")
    ck(collapse_summary([_v_nosig, _v_upt], True) is None and collapse_summary([], True) is None,
       "★ S4d 沒有任何列被摺 ⇒ 回 None ⇒ 模板整列不渲染 ⇒ 版面逐位元不變")

    # (S5) build_board 附掛：順序不變、列不減、口徑一位元不動
    _vb = demo_board(n_days=5)
    # ⚠ `#125` C-RW26：座列插在母機列正下方 ⇒ 這根釘濾掉 `is_seat` 之後才是「機台列的組成」。
    #   下面 S5a2 另釘座列**只會插在自己母機後面**（不重排、不移位）。
    ck([r["eid"] for a in _vb["areas"] for r in a["rows"] if not r.get("is_seat")]
       == [e for _a, _n, e in EQMT_CATALOG],
       "★ S5a 摺疊後 areas 的機台列組成與順序**逐位元＝EQMT_CATALOG**（只少畫、不重排、不刪列）")
    ck(all(r.get("vis") in ("shown", "collapsed") for a in _vb["areas"] for r in a["rows"]),
       "S5b 每一列都帶 vis（模板不需要自己判斷）")
    ck(_vb["headline"]["n_total"] == len(EQMT_CATALOG) and _vb["collapsed_n"] > 0,
       f"S5c headline 仍算全 {len(EQMT_CATALOG)} 台（摺疊是顯示層）；"
       f"demo 板確實有列被摺，got {_vb['collapsed_n']}")
    ck(_vb["collapsed_n"] == sum((a["collapsed"] or {}).get("n", 0) for a in _vb["areas"])
       == sum(1 for a in _vb["areas"] for r in a["rows"] if r["vis"] == "collapsed"),
       "S5d board 級摺疊數 ≡ Σ 區摘要 ≡ Σ 逐列旗標（三處同數）")
    _vmap = {r["eid"]: r for a in _vb["areas"] for r in a["rows"]}
    ck((
       _vmap[EQM_SILENT_SHOWCASE_EID]["vis"] == "shown"
       and _vmap[EQM_SILENT_SHOWCASE_EID]["vis_reason"] == "silent"
       and [r["eid"] for a in _vb["areas"] for r in a["rows"] if r["vis_reason"] == "silent"]
       == [EQM_SILENT_SHOWCASE_EID]),
       "★ S5e demo 沉默機 showcase 恰一台且留在表上（走鐘＝紅燈，不是靜默失去 demo 案例）")
    ck((
       _vmap[EQM_NOMES_SHOWCASE_EID]["vis"] == "collapsed"
       and _vmap[EQM_NOMES_SHOWCASE_EID]["vis_reason"] == "no_mes"
       and _vmap[EQM_NOMES_SHOWCASE_EID]["mes_measurable"] is False
       and _vmap[EQM_NOMES_SHOWCASE_EID]["mes_covered"] is True),
       "★ S5f2 治具落 no_mes 桶——**而它的 `mes_covered` 是 True**（demo 也對名冊全鋪 key）"
       "＝審查 HIGH-1 的端到端形：判準必須是普查集合、不是探測的 key")
    # ⚠ 門檻下修：名冊有機台退役之後，demo 的合成狀態是**索引式**的
    #   （`i % 2`／`_slots_hex`），少列會把後面每一台的合成狀態整批位移 ⇒ 實測計數跟著變。
    #   這根釘要的是「一整批、不是手植的 1 台」這個量級，不是某個確切數字。
    ck(_pub_tree or sum((a["collapsed"] or {}).get("no_mes", 0) for a in _vb["areas"])
       >= _pp.PINS["eqmeeting.collapsed_no_mes_min"],   # 公開樹的合成普查涵蓋整個合成名冊 ⇒ 沒有「普查看不到」那批
       "S5f3 demo 的「無 MES 覆蓋」是一整批治具/烤箱/顯微鏡，不是手植的 1 台"
       "（**不寫死台數**：普查重跑或別名折疊都會讓它上下動，釘子只釘量級）")
    # ★★ S5f4（審查 HIGH-1 的 **live 形**）：把 live 的 `mes_by_eid` 形狀原樣重現——
    #    **名冊全量個 key、查不到的塞 None**（＝`app._build_eqm_live` 那一行的行為）。
    #    舊實作在這個形狀下 `mes_covered` 恆 True ⇒ 治具會被判成「沉默機」永不摺。
    _live_mes = {e: (None if i % 2 else 1.0) for i, (_a, _n, e) in enumerate(EQMT_CATALOG)}
    _lb = build_board(EQMT_CATALOG, {}, _DEMO_ANCHOR, 5, "live", wo_status="live",
                      mes_by_eid=_live_mes, mes_lookback_d=30)
    _lmap = {r["eid"]: r for a in _lb["areas"] for r in a["rows"]}
    ck(len(_live_mes) == len(EQMT_CATALOG)
       and sum(1 for v in _live_mes.values() if v is None) == len(EQMT_CATALOG) // 2
       and all(r["mes_covered"] for r in _lmap.values()),
       "S5f4 前提：live 形＝名冊全鋪 key ⇒ 每一列的 `mes_covered` 都是 True")
    _jig = [e for _a, _n, e in EQMT_CATALOG
            if e not in MES_MEASURABLE_EIDS and _live_mes[e] is None]
    ck((_pub_tree or _jig) and all(_lmap[e]["vis_reason"] == "no_mes" for e in _jig),
       "★ S5f5 live 形：普查看不到的機台一律 `no_mes`（**不是**沉默機）——HIGH-1 的迴歸釘")
    _sil = [e for _a, _n, e in EQMT_CATALOG
            if e in MES_MEASURABLE_EIDS and _live_mes[e] is None]
    ck(_sil and all(_lmap[e]["vis_reason"] == "silent" and _lmap[e]["vis"] == "shown"
                    for e in _sil),
       "★ S5f6 live 形：**普查看得到卻整窗無生產**的才是沉默機、且留在表上")
    ck(set(_jig) & set(_sil) == set()
       and len(_jig) + len(_sil) == sum(1 for v in _live_mes.values() if v is None),
       "S5f7 兩群互斥且窮盡（每一個 None 都有歸屬，沒有列掉進三不管地帶）")
    ck([a["collapsed"] for a in _vb["areas"] if a["key"] == "zc"][0]["dormant"],
       "S5f Zone C 區的摘要點到長停機台的名字（C-D3：摺疊也是排除、不得靜默）")
    # ★ 承重負斷言：摺疊**不得**動到任何數字。同一份輸入、拆掉 vis 之後與規格數字逐位相同。
    _vb_nums = [(r["eid"], r["total_stop_h"], r["avail_pct"], r["avail_delta_pp"],
                 sorted((r["cells"] or {}).items()), len(r["segments"]))
                for a in _vb["areas"] for r in a["rows"]]
    _vb2 = demo_board(n_days=5)
    ck(_vb_nums == [(r["eid"], r["total_stop_h"], r["avail_pct"], r["avail_delta_pp"],
                     sorted((r["cells"] or {}).items()), len(r["segments"]))
                    for a in _vb2["areas"] for r in a["rows"]],
       "S5g 確定性：兩次 demo_board 的數字逐位相同（摺疊沒有引入任何隨機性）")
    ck(not any("vis" in (r.get("now") or {}) for a in _vb["areas"] for r in a["rows"]),
       "S5i 摺疊旗標不得污染 now 區塊（Now 欄的契約不變）")

    # ══ (S6) `#125` (b)(c) 座列浮出（鐵則 C-RW26；`SPEC-RUNWINDOW.md` §6.3-c）══════════════
    #   Chris 2026-09-12 晚裁決：四站的座**例外才出現**（窗內有工單範圍指到該座／該座被登記
    #   長停才浮出成母機正下方一列）；**Gen2（形③）一個位元不動**；座列**不進任何 Σ**。
    _s6_end = _DEMO_ANCHOR
    _s6_dw = day_windows(_s6_end, 5)
    _s6_day = _s6_end.date() - _dt.timedelta(days=2)          # 錨前兩天（同 A10 showcase 的理由）
    _s6_cat = [("zb", "Flex bonder#5", _ES), ("za", "Leak Test#1", _CTP),
               ("za", "Frame#2", _E0)]

    def _s6_at(h, m=0):
        return (_dt.datetime(_s6_day.year, _s6_day.month, _s6_day.day)
                + _dt.timedelta(hours=h, minutes=m))

    def _s6_wo(no, eid, phases, ev=None, wt="REACTIVE", asset=None):
        """phases＝[(status, hold_reason, at), …]；ev＝[(ts, [sub_units…]), …]。"""
        w = dict(_demo_phase_wo(no, eid, phases[0][2], phases[-1][2], wt, phases))
        if asset:
            w["asset_id"] = asset                 # NOTE-71：單直接開在座資產上
        w = w8.normalize_wo(w)
        w["scope_events"] = w8.normalize_scope_events(
            [{"ts": ts, "scope": [eid], "sub_units": list(su)} for ts, su in (ev or ())])[0]
        return w

    # (a) A10：08:10 開單整機停 → **10:00 範圍改到座 2** → 15:00 結單。
    _s6_a10 = _s6_wo(70001, _ES,
                     [("OPEN", None, _s6_at(8, 10)), ("IN_PROGRESS", None, _s6_at(8, 40)),
                      ("CLOSED", None, _s6_at(15, 0))],
                     ev=[(_s6_at(10, 0), ["nest:2"])])
    # (b) 整機單（零 scope 事件）＝**不得**展開到座（避免三列複本）。
    _s6_whole = _s6_wo(70002, _ES,
                       [("OPEN", None, _s6_at(8, 0)), ("IN_PROGRESS", None, _s6_at(8, 0)),
                        ("CLOSED", None, _s6_at(9, 0))])
    # (c) NOTE-71：單直接開在 `EID-26718-N1` 上 ⇒ `seat_unit` 整張單恆有。
    _s6_n71 = _s6_wo(70003, _ES,
                     [("OPEN", None, _s6_at(11, 0)), ("IN_PROGRESS", None, _s6_at(11, 0)),
                      ("CLOSED", None, _s6_at(13, 0))], asset=(_ES + "-N1"))
    # (d) 不計的單（暫停·等機台空檔）指到座 3 ⇒ **照樣浮出**、`stop_h` 0、無當量。
    _s6_upt = _s6_wo(70004, _ES,
                     [("OPEN", None, _s6_at(16, 0)),
                      ("ON_HOLD", "WAITING_MACHINE_TIME", _s6_at(16, 0)),
                      ("COMPLETED", None, _s6_at(19, 0))],
                     ev=[(_s6_at(16, 0), ["nest:3"])])
    # C-RW28／NOTE-79 live form：整機單的 scope 直接寫 cmms seat asset ID。
    _s6_note79 = _s6_wo(32136, _ES,
                         [("OPEN", None, _s6_at(16, 0)),
                          ("ON_HOLD", "WAITING_MACHINE_TIME", _s6_at(16, 0)),
                          ("COMPLETED", None, _s6_at(19, 0))])
    _s6_note79["scope_events"] = w8.normalize_scope_events(
        [{"ts": _s6_at(16, 0), "scope": [(_ES + "-N2")], "sub_units": []}])[0]
    # (e) 形②（co-tag 子 EID）：`sub_units` 就是子 EID 本身。
    _s6_cotag = _s6_wo(70005, _CTP,
                       [("OPEN", None, _s6_at(9, 0)), ("IN_PROGRESS", None, _s6_at(9, 0)),
                        ("CLOSED", None, _s6_at(12, 0))],
                       ev=[(_s6_at(10, 0), [_CT1])])
    _s6_wos = {_ES: [_s6_a10, _s6_whole, _s6_n71, _s6_upt],
               _CTP: [_s6_cotag]}
    _s6 = seat_rows_for_board(_s6_cat, _s6_wos, _s6_dw, _s6_end, "live",
                              rates_by_eid={_ES: 9.0})
    _s6m = {r["eid"]: r for rs in _s6.values() for r in rs}
    ck("nest:2" in _seat_wo_clips(_s6_note79, set(_wcap.installed_units(_ES)),
                                   _s6_dw[0][1], _s6_end),
       "★ S6a0 C-RW28 NOTE-79 live scope seat ID reaches _seat_wo_clips as nest:2")
    ck(sorted(_s6m) == sorted([_CT1, (_ES + "-N1"), (_ES + "-N2"), (_ES + "-N3")]),
       f"★ S6a 浮出條件：A10 改範圍／NOTE-71 直開座／不計單／形② 子 EID 各一列，"
       f"**整機單不展開**、沒事件的座不出現，got {sorted(_s6m)}")
    ck(_s6m[(_ES + "-N2")]["seat_unit"] == "nest:2"
       and _s6m[(_ES + "-N2")]["parent_eid"] == _ES
       and _s6m[(_ES + "-N2")]["is_seat"] is True
       and _s6m[_CT1]["seat_unit"] == _CT1,
       "S6b 列 eid＝形① NOTE-71 wire id／形② 子 EID 本身；三個加性鍵齊備")
    # ★ 段裁範圍：改到座**之前**的 1h50m 留在母機列，座列只有 10:00 之後的 5h。
    _s6_seg2 = _s6m[(_ES + "-N2")]["segments"]
    ck(len(_s6_seg2) == 1 and _s6_seg2[0]["start"] == _s6_at(10, 0)
       and _s6_seg2[0]["end"] == _s6_at(15, 0) and _s6_seg2[0]["work_order_no"] == 70001
       and abs(_s6m[(_ES + "-N2")]["total_stop_h"] - 5.0) < 1e-9,
       f"★ S6c 段只取「範圍含這座」的時段（10:00→15:00＝5.0h）；改之前的留在母機列，"
       f"got {[(s['start'], s['end']) for s in _s6_seg2]}")
    ck(abs(_s6m[(_ES + "-N1")]["total_stop_h"] - 2.0) < 1e-9
       and {s["work_order_no"] for s in _s6m[(_ES + "-N1")]["segments"]} == {70003},
       "S6d NOTE-71 直開座上的單＝**整張**（不必等 scope 事件）")
    _s6_r3 = _s6m[(_ES + "-N3")]
    ck(_s6_r3["total_stop_h"] == 0.0 and _s6_r3["equiv_ph"] is None
       and _s6_r3["segments"] and all(s["countable"] is False for s in _s6_r3["segments"]),
       "★ S6e 不計的單也讓座浮出：有段（斜紋）、`stop_h` 0、**無當量**")
    ck(all(r["avail_pct"] is None and r["avail_delta_pp"] is None for r in _s6m.values()),
       "★ S6f A%／Δ 恆 None（C-CF8：機台是可用度單位、座是產能單位）")
    ck(_s6m[(_ES + "-N2")]["now"]["bucket"] == "up"
       and _s6m[(_ES + "-N2")]["now"]["verdict"] == "up_idle",
       "S6g Now：單已結案 ⇒ 不宣稱 DOWN（判定沿用 `_now_inherited`，不另寫一份）")
    # Now DOWN 形：同一張單改成仍 open、範圍在座 2。
    _s6_open = _s6_wo(70006, _ES,
                      [("OPEN", None, _s6_end - _dt.timedelta(hours=4)),
                       ("IN_PROGRESS", None, _s6_end - _dt.timedelta(hours=3))],
                      ev=[(_s6_end - _dt.timedelta(hours=2), ["nest:2"])])
    _s6_open["closed_at"] = None
    _s6o = seat_rows_for_board(_s6_cat, {_ES: [_s6_open]}, _s6_dw, _s6_end, "live")
    _s6o_row = _s6o[_ES][0]
    ck(_s6o_row["now"]["bucket"] == "down" and _s6o_row["now"]["wo_no"] == 70006
       and _s6o_row["now"]["state"] == "維修",
       f"★ S6h Now＝那張單的狀態（仍 open ∧ 此刻範圍含這座 ⇒ DOWN·維修），got {_s6o_row['now']}")
    # ★ 當量＝母機速率 × 一座；母機列的當量已含本列 ⇒ 座列恆是**子集**（不得再加）。
    _nES = len(_wcap.installed_units(_ES))          # WP-10d：該機座數由名冊推導（私有樹＝3）
    ck(_s6m[(_ES + "-N2")]["seat_n"] == _nES
       and abs(_s6m[(_ES + "-N2")]["seat_f"] - 1.0 / _nES) < 1e-9
       and _s6m[(_ES + "-N2")]["equiv_ph"] == int(round(5.0 * 9.0 / _nES)),
       f"★ S6i 當量＝`stop_h × 母機速率 × 一座`，got {_s6m[_ES + '-N2']['equiv_ph']}")
    ck(_s6m[_CT1]["equiv_ph"] is None,
       "S6j 母機沒有速率 ⇒ 座列不猜（同機台列：無 rate 就不換算）")
    # ★ 形③（Gen2）一個位元不動：子槽位列與母機都不得產生座列。
    _s6_g2 = seat_rows_for_board(EQMT_CATALOG, {}, _s6_dw, _s6_end, "live",
                                 dormant_events=[{"eid": _PA, "sub_unit": _I1,
                                                  "ts_effective": "2026-01-01",
                                                  "state": wdorm.STATE_NOT_SCHEDULED}])
    ck(_s6_g2 == {} and all(p not in _s6_g2 for p in _wcap.PARENT_EXPANSION),
       f"★ S6k 形③ Gen2 零座列（母機不在名冊、子槽位各自是第一層的列），got {sorted(_s6_g2)}")
    # ★ 鍵集合＝`build_row` 的鍵集合 ＋ 那三個加性鍵（模板／drill 走同一條路）。
    _s6_br = build_row(("zb", "Flex bonder#5", _ES), [], _s6_dw, _s6_end, "live")
    ck(set(_s6m[(_ES + "-N2")]) - set(_s6_br) == {"is_seat", "parent_eid", "seat_unit"}
       and set(_s6_br) - set(_s6m[(_ES + "-N2")]) == set(),
       f"★ S6l 座列鍵集合 ＝ build_row 鍵集合 ＋ 三個加性鍵，"
       f"got +{sorted(set(_s6m[_ES + '-N2']) - set(_s6_br))} "
       f"-{sorted(set(_s6_br) - set(_s6m[_ES + '-N2']))}")
    # ★ A11 **座級**長停：零工單也要浮出；整機列的事件**不算**（那是整台的登記）。
    _s6_ev = [{"eid": _ES, "sub_unit": "nest:1", "ts_effective": "2026-01-05",
               "state": wdorm.STATE_NOT_SCHEDULED, "source": "cmms"}]
    _s6_dm = seat_rows_for_board(_s6_cat, {}, _s6_dw, _s6_end, "live", dormant_events=_s6_ev)
    ck(list(_s6_dm) == [_ES] and len(_s6_dm[_ES]) == 1
       and _s6_dm[_ES][0]["eid"] == (_ES + "-N1")
       and _s6_dm[_ES][0]["now"]["bucket"] == "dormant"
       and (_s6_dm[_ES][0]["dormant"] or {}).get("since") == "2026-01-05",
       f"★ S6m A11 座級長停＝零工單也浮出、Now 走既有長停形，got {_s6_dm}")
    _s6_ev_whole = [{"eid": _ES, "ts_effective": "2026-01-05",
                     "state": wdorm.STATE_NOT_SCHEDULED, "source": "cmms"}]
    ck(seat_rows_for_board(_s6_cat, {}, _s6_dw, _s6_end, "live",
                           dormant_events=_s6_ev_whole) == {},
       "★ S6n 整機長停登記**不讓座浮出**（那是整台的事實，母機列已經在講）")
    ck(seat_rows_for_board(_s6_cat, {}, _s6_dw, _s6_end, "live") == {},
       "S6o 沒有任何座級事件 ⇒ 零座列 ⇒ 版面逐位元同加本功能前（例外才出現）")
    # ★★ 承重負斷言：座列**不進任何 Σ**、且母機列與 `build_row` 輸出逐位元不動。
    _s6_bd = demo_board(5)
    _s6_orig = globals()["seat_rows_for_board"]
    try:
        globals()["seat_rows_for_board"] = lambda *_a, **_k: {}
        _s6_bd0 = demo_board(5)
    finally:
        globals()["seat_rows_for_board"] = _s6_orig
    ck([a["collapsed"] for a in _s6_bd["areas"]] == [a["collapsed"] for a in _s6_bd0["areas"]]
       and _s6_bd["collapsed_n"] == _s6_bd0["collapsed_n"]
       and _s6_bd["headline"] == _s6_bd0["headline"]
       and _s6_bd["legend_states"] == _s6_bd0["legend_states"]
       and _s6_bd["legend_uptime"] == _s6_bd0["legend_uptime"]
       and _s6_bd["erw"] == _s6_bd0["erw"]
       and (_s6_bd["conflicts"], _s6_bd["dormant_conflicts"], _s6_bd["dormant_drops"])
       == (_s6_bd0["conflicts"], _s6_bd0["dormant_conflicts"], _s6_bd0["dormant_drops"]),
       "★★ S6p 座列不進任何 Σ：collapse／headline／legend／erw／揭露清單逐位元同無座列的板")
    ck([r for a in _s6_bd["areas"] for r in a["rows"] if not r.get("is_seat")]
       == [r for a in _s6_bd0["areas"] for r in a["rows"]],
       "★★ S6q 母機列逐位元不動（座列是**另外插進去的列**，不是 build_row 的新分支）")
    _s6_pos = [(i, r) for a in _s6_bd["areas"] for i, r in enumerate(a["rows"])
               if r.get("is_seat")]
    ck(_s6_pos and all(
        any(a["rows"][i - 1]["eid"] in (r["parent_eid"], )
            or a["rows"][i - 1].get("parent_eid") == r["parent_eid"]
            for a in _s6_bd["areas"] if len(a["rows"]) > i and a["rows"][i] is r)
        for i, r in _s6_pos),
       "★ S6r 座列緊接在自己母機（或同母機的前一座）之後——不重排、不移位")
    ck(sum(1 for a in _s6_bd["areas"] for r in a["rows"] if r.get("is_seat")) == 2
       and [r["eid"] for a in _s6_bd["areas"] for r in a["rows"] if r.get("is_seat")]
       == [(_ES + "-N2"), (_ES + "-N3")],
       "S6s demo 恰兩列座（一張計停機的座級單＋一張不計的單；確定性 showcase）")
    ck(all(r["vis"] == "shown" and r["vis_reason"] is None
           for a in _s6_bd["areas"] for r in a["rows"] if r.get("is_seat"))
       and row_visibility({"is_seat": True}, False) == ("shown", None),
       "★ S6t 座列恆 shown、`vis_reason` None（存在即顯示；永不進摺疊摘要的任何一桶）")

    # #61／§11.6：cmms 登記在板外是例外揭露，不改 EQMT_CATALOG、不改 build_board 分母。
    _outside_ev = [{"eid": "EID-99999", "ts_effective": "2026-01-06",
                    "state": wdorm.STATE_NOT_SCHEDULED, "source": "cmms"}]
    ck(wdorm.unlisted_long_stop_registrations(_dt.date(2026, 2, 1), _outside_ev,
                                               [e for _a, _n, e in EQMT_CATALOG])
       == [{"eid": "EID-99999", "since": "2026-01-06"}],
       "#61 正例：cmms 長停登記在本板名冊外 ⇒ chip 材料含 EID 與登記日")
    ck(wdorm.unlisted_long_stop_registrations(_dt.date(2026, 2, 1), _outside_ev,
                                               ["EID-99999"]) == []
       and wdorm.unlisted_long_stop_registrations(_dt.date(2026, 2, 1), None, []) == [],
       "#61 負例：N=0／cmms 未接線 ⇒ 空列（版面不得常駐 chip，bridge 不冒充 cmms）")

    # ---- (#104(b)) STATION_LOT_PROXY 名冊三條釘（`SPEC-LANDING.md` §18.26.2；C-LD44）----
    #   名冊本身沒有計算，但它是**一條會讓方塊變綠的規則的輸入** ⇒ 三個結構性條件各釘一根：
    #   在名冊上、量不到、站不是設備稼動名冊上的站。任一條破了，推斷就是在說謊。
    import mes_timeline as _mt_slp        # ⚠ 只讀常數 `EQP_STATIONS`（凍結模組；本檔零 I/O 呼叫）
    _slp_cat = {e for _a, _n, e in EQMT_CATALOG}
    _slp_eids = [e for _c, _es in STATION_LOT_PROXY.items() for e in _es]
    # WP-10d（R-P11）：STATION_LOT_PROXY 在公開樹是合成名冊（站數／台數不同、且合成普查涵蓋整個合成名冊）⇒ 具名計數與「普查量不到」只在私有樹釘
    ck(len(_slp_eids) == len(set(_slp_eids))
       and (_pub_tree or (len(STATION_LOT_PROXY) == _pp.PINS["eqmeeting.station_lot_proxy_stations_n"]
                           and len(_slp_eids) == _pp.PINS["eqmeeting.station_lot_proxy_eids_n"])),
       f"#104(b) 名冊站數／台數＝探針結論（私有樹釘值），實 {len(STATION_LOT_PROXY)} 站 {len(_slp_eids)} 台")
    ck(all(e in _slp_cat for e in _slp_eids),
       "#104(b) 每一台都必須在 EQMT_CATALOG 上（不在名冊的機台畫不到 landing 上）")
    ck(_pub_tree or all(e not in MES_MEASURABLE_EIDS for e in _slp_eids),
       "#104(b) ★ 量得到的機台不准走推斷（C-LD44）：走推斷就是拿 low 信心蓋掉真訊號")
    ck(all(c not in _mt_slp.EQP_STATIONS for c in STATION_LOT_PROXY),
       "#104(b) ★ 這些站不得出現在 `mes_timeline.EQP_STATIONS`（那是**設備稼動**名冊；"
       "站若在那份名冊裡就代表它掛得住設備 ⇒ lot-only 的前提不成立）")
    ck(STATION_LOT_STATION_OF == {e: c for c, es in STATION_LOT_PROXY.items() for e in es}
       and len(STATION_LOT_STATION_OF) == len(_slp_eids),
       "#104(b) 反查表由名冊衍生、不是第二份手抄")
    ck(_pub_tree or (sorted(STATION_LOT_PROXY["segDieArrayTaping"]) == sorted(["EID-01352", "EID-26079"])
                     and "EID-24967" not in STATION_LOT_STATION_OF),
       "#104(b) 同站兩支治具一起列；Flex Jig#2（EID-24967）的 master_station 是別站 ⇒ 不列")

    print(f"[board_eqmeeting] selftest PASS — {n} 項斷言全過（S12 分階段停機口徑 2026-07-07 裁決）："
          f"目錄 {len(EQMT_CATALOG)} 台(四區、EID 補零、無 dup、順序)；"
          f"day_windows(生產日界 08:00＝A6/末欄 partial)；"
          f"S12 階段(_phase_kind：REACTIVE-OPEN 算/PM-OPEN 不算/IN_PROGRESS 算/hold-waiting uptime/遷移整窗)；"
          f"ON_HOLD 拆色(等料/等外包獨立計停機態·OTHER+未知留母態·兩 uptime 子類 countable=False)；"
          f"wo_segments(分階段/相鄰合併/countable 旗/hold-uptime 扣除跨日/故障停機不現)；"
          f"group_wos_by_eid(None 容忍)；bucket(跨午夜秒守恆/兩類都切)；"
          f"assign_lanes(貪婪區間分割/無重疊 1 軌/重疊分軌/端點相接共軌/確定性/showcase 錨日 2 軌)；"
          f"day_cell(只計 countable/union 重疊/等料等外包進 by_state/by_uptime 第一層現形·跨午夜切分·與 by_state 互斥/純 uptime 日 stop_h=0)；"
          f"build_row(now PM-OPEN=UP/REACTIVE-IP=DOWN)；build_board(分區/legend_states legacy 調機·其他維護 排除/legend_uptime 兩子類)；"
          f"**A10 母單展開**(golden 期望帳逐台 6h50m/2h10m/1h40m×2·出處欄 inherited_from+kind·"
          f"母機永不成列·重疊 union 不雙計·PM 全量繼承各記完整區間·Now 帶出處且自身單優先·"
          f"**長停工位繼承與指名皆扣（2026-08-14 翻面）**·不傳＝逐位同舊·翻譯管線衛生負斷言)；"
          f"**長停不計 downtime**(直接單也扣＋dormant_dropped 揭露帳逐值＋board dormant_drops 彙總"
          f"＋dormant_events=[] 負對照逐位相同＋非長停 EID 逐位相同)；"
          f"**downtime_by_eid_scoped**(母機機器視角 1h40m≠子工位 6h50m·折返兩段·legacy [] 與舊函式"
          f"逐位相同·長停 EID 0h·負對照與 downtime_by_eid 逐位相同)；"
          f"demo_board(只合成 PM/REACTIVE·無 legacy 調機/其他維護·確定性/走真管線/UTC-Z/pattern UP·DOWN/"
          f"uptime showcase 四視覺:等外包琥珀 dominant·維修+等料·維修+試跑·純 uptime；uptime 紋理三輪合併＝單一斜紋)；"
          f"downtime_by_eid(per-EID union countable 停機 + by_state 原始加總＝landing OEE A↔U 真拆分供給)；"
          f"**取消/作廢不進可計停機**(拍板①·WO 35185 劇本：零段/0h/Now UP·"
          f"CLOSED 負對照照算·空 history 的 VOIDED 也擋·A10 母單取消不繼承＋CLOSED 負對照)；"
          f"**(c) MES 交叉揭露**(段中段揭露/護欄刀鋒 ±0.1h/段前不算/uptime 段不算/量不到回 None/"
          f"**只揭露不改數字逐位相同**/取消單不重複告狀/demo showcase 確定性/無 MES ⇒ conflicts [])；"
          f"**§10 一致性揭露**(登記長停 ∧ MES 窗內有生產：日粒度刀鋒次日起才算/age 跨界翻面/"
          f"沒登記不抓＝單向/量不到回 None/since 壞值不猜/**只揭露不改數字且不解除登記**/"
          f"demo showcase 恰一台/無 MES ⇒ dormant_conflicts [])；"
          f"**A11 批次 sched_unknown**(名冊漂移原樣透傳、預設空＝整列不渲染)。"
          f"**#39 該跑窗換軸**(inert 三形退舊軸·C-PC8 涵蓋日篩選·降級形 ShiftWin−Closure＋degraded 旗·"
          f"**C-RW4 迴歸釘：停 4h→20h 分母恆定值變差**·C-RW7 uncertain 不縮分母·C-RW10 |RW|=0 顯「—」·"
          f"C-RW9 非名冊 None·長停 None·Δpp 同軸·time bar 與妥善率同資料且停⊂窗·"
          f"**§6.3 五條裁決**(days.by_state 窗內逐態＋跨窗邊界段只留窗內半·|RW|=0 涵蓋日有 entry"
          f"但 h=0·未涵蓋日無 entry·逐日 equiv_ph 規則·bar 整窗連排 n_days/ticks/uncov 且舊單日鍵退場)·"
          f"§6.2① 當量＝窗內停機×rate 無 rate 不猜·換軸只動 A%/Δ/erw/equiv 其餘逐位不動·"
          f"demo 三層階梯 tier1/tier3 可目視＋確定性·avail_pw_pct 已刪除負斷言)。"
          f"**§6.5 稼動三頁換軸**(erw_machine_windows：C-RW9 非名冊不出現·"
          f"★Σ|RW| 逐位≡erw_full_seconds·未涵蓋日計入分母但另存 uncov·收工線刻度不含窗緣·"
          f"ctx 缺⇒{{}}；erw_state_split：只算機隊∩名冊·分母≡chips·未排生產不進圓餅·"
          f"不明→該跑沒跑·Σby_state≡Σ|RW|·四色停機照進·零交集/ctx 缺⇒None·C-RW4 圓餅形迴歸釘)。"
          f"**WP-D 預設摺疊**(row_visibility 六個 reason_key·四種無工單列各一＋久靜刀鋒·"
          f"★沉默機與久靜機絕不摺·★純 uptime/繼承段/長停排除帳列算有紀錄·"
          f"無 MES 證據⇒全摺但誠實標 unknown_mes·"
          f"★★覆蓋判準＝730 天普查靜態集合非探測 key（**live 形迴歸釘**：名冊 key 全鋪、"
          f"covered 恆 True 之下治具仍落 no_mes、普查內的 None 才是 silent、兩群互斥窮盡）·"
          f"collapse_summary 分桶守恆＋長停點名帶 EID＋零摺回 None·demo silent showcase 恰一台·"
          f"board 三處同數且順序/列數/口徑逐位不動)。"
          f"**#104(b) STATION_LOT_PROXY**(站數台數釘·全在名冊·全不在普查集合·"
          f"五站皆不在 mes_timeline.EQP_STATIONS·反查表由名冊衍生·Flex Jig#2 歸別站不列)。"
          f"**#125 (b)(c) 座列浮出（C-RW26）**(四種浮出條件各一：A10 改範圍／NOTE-71 直開座／"
          f"不計單／形② 子 EID·**整機單不展開**·沒事件的座不出現·段只取「範圍含這座」的時段"
          f"（改之前留母機）·當量＝母機速率×一座·A%／Δ 恆 None·Now 走 _now_inherited 三合一·"
          f"A11 **座級**長停零工單也浮出而整機登記不浮出·**形③ Gen2 零座列**·鍵集合＝build_row＋三·"
          f"★★ 座列不進任何 Σ（collapse／headline／legend／erw／揭露清單逐位元同無座列的板）·"
          f"母機列逐位元不動·位置緊接母機·demo 恰兩列·恆 shown 且 vis_reason None)。"
          f"純函式零 I/O、不連 MES。")
    return n


def main():
    ap = argparse.ArgumentParser(
        description="設備部門例行會議頁呈現模型（純工單導向純函式；--selftest 離線自驗）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（零 I/O、零 MES）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    ap.error("本檔＝呈現模型純函式庫；app 路由消費 build_board/demo_board。現階段僅 --selftest。")


if __name__ == "__main__":
    main()
