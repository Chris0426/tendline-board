# -*- coding: utf-8 -*-
"""
board_scope.py — A10 母機工單「停機範圍事件」展開到子工位（純函式、零 I/O、selftest）
=====================================================================================

規格權威＝`CONCEPT.md` §19.3 **A10**（契約四 pin＋golden 劇本＋**Board 側拍板全表九條**）
＋`SPEC-DORMANT-CAPACITY.md` §5（推論 vs 事實的介面）
＋`_fixtures/wo_scope_events_golden.v1.json`（＋同名 `.README.md`＝期望帳）。

═══ 這在答什麼問題 ═══════════════════════════════════════════════════════════
  MES 有子工位獨立生產紀錄的機台（CAL Gen2 四座、CLN-2 兩組各四座），Board 的
  downtime/availability **以子工位 EID 計算**；但工單開在**母 EID**，其停機時數掛母機。
  而三個母機（`EID-24742`／`EID-16077`／`EID-27600`）**都不在 `EQMT_CATALOG`**
  ⇒ `build_board` 只走名冊 `wos_by_eid.get(eid)`，母單今天在設備頁**連查都不會查**
  ＝**完全消失**（不是「掛錯列」）。這是 A10 要修的唯一硬錯。

  且真實維修的影響範圍**隨時間變動**：開單整機停 → 隔離故障後放行三座 → 中途臨時
  陪停一座半小時再放回 —— 全在同一張工單裡。cmms 因此在 WO detail 加 append-only 的
  **完整範圍快照事件流** `scope_events: [{ts, scope}]`（NOTE-18／NOTE-25）。

═══ 兩軸交集（cmms A10 ④ 明講的消費鐵則；違反即錯）═════════════════════════
  `scope` 答「**哪些工位被牽連**」、`is_downtime` 答「**現在是否停機**」＝**正交兩軸**。
  正確合成＝該段 **算停機** AND 工位 **∈ effective scope**。
  · 「算停機」這一軸 Board 用自家 `board_eqmeeting._phase_kind(...)=='count'`
    ——NOTE-13 的 fixture 已釘死「`is_downtime` ≡ `_phase_kind=='count'`」逐段等價
    （`_fixtures/selftest_fixtures._check_wo_downtime_flag`）⇒ **不新收 wire 欄**、
    不在 runtime defer 別人的判定（比照 Diag nuisance-seed 前例）。
  · 本模組只管 scope 那一軸，**只回時間區間**；「這段算不算停機」由呼叫端
    （`board_eqmeeting.build_row`）拿 `wo_segments` 的 countable 段來交集。

═══ 契約四 pin（NOTE-18；parser 承重約束）════════════════════════════════════
  (a) `scope_events` 欄位**恆在**、`[]`＝legacy／整機全程（**勿防 absent**、勿當缺資料）。
  (b) 開單**不**落初始整機事件 ⇒ **初始範圍一律由 `asset_id`＋`opened_at` 推導**。
  (c) `ts` 升冪（回填時戳插入後重排）——本模組仍**防禦性重排**（便宜、且 fixture 對版要驗）。
  (d) 只曝 effective 事件（更正＝cmms 內部 supersede，消費端看不到更正語意）。
  值域：scope **非空**（空＝bad data，cmms 有 domain 硬驗證）・成員 ∈ {標的 EID} ∪
  direct `contains_module` 子工位・**限 REACTIVE**（PM 不出卡、不吃 scope）・終態拒新增。
  決策 9＝不設筆數上限、不出 truncated 旗標 ⇒ 拿到的恆為完整陣列。

═══ 展開規則（Board 側拍板全表；勿重議）════════════════════════════════════════
  ① ⚠ **原拍板「作用面只有 `/equipment`」已於 2026-08-14 被 Chris 裁決取代**（存證＝
     `CONCEPT.md` §19.3 A10 拍板表 ① 的取代註記）：**凡指標涉及工單停機的頁都要反映**。
     ⇒ R1 輪擴散到 `/utilization`（A%/U% chip、機台帶著色、子工位附掛列）與 landing
     real_split；`/daily`（`board_attrib`）／`/updown`／`/dispatch`／`/equipment/health`
     留 R2。
     ★ **母子雙計的顧慮由「機器視角投影」解掉、不是靠不做**：父機級消費者吃
     `machine_scope_windows(...)`（scope ∋ 母機 EID 的那些段＝整機真的停），子工位級消費者
     吃 `child_clips(...)`——**兩者永不相加**，同一張單在父機軸與子工位軸各自回答自己的問題。
  ② 展開名冊＝**顯式子女常數**（`PARENT_EXPANSION`）。**自動展開已被資料證否**
     ——`MES-dependent-equipment-export.csv` 的邊方向不可信（配件當 parent、三層鏈、
     模組當 parent），**不得作為來源**。
  ④ 展開目標＝母機子工位 ∩ `EQMT_CATALOG`（頁面上要有列可放）。`EID-27600` 的第五個
     子女 E-Test `EID-27617` **不在名冊、永不入列**（cmms 亦已排除出範圍卡選單）。
  ⑤ **PM＝全量繼承**：每個子工位各記**完整活躍區間、不平均切分**（母機保養 10h ⇒ 四個
     工位那 10h 都不能生產、各記 10h 而非各 2.5h）。**PM 不吃 scope_events。**
     配套鐵則：**子工位停機時數不可相加當機器停機時數**（10h×4 ≠ 機器停 40h）。
  ⑦ **不加母機列**（母機無自身稼動數據、加列會製造「該不該相加」的困惑）。
  ⑨ 母單與子工位自身工單重疊 ⇒ 既有 interval-union 天然不重複計。

═══ 與 dormant 的介面（`SPEC-DORMANT-CAPACITY.md` §5；本模組在 clip 構造期實現）════
  ★★ **2026-08-14 Chris 裁決翻面（舊 D1 規則作廢、勿重撿）**：
     **登記長停的工位不得再計任何 downtime——explicit（直接指名）也一樣扣。**
  · 舊規則是「繼承（推論）扣、直接指名（事實）照留」。它與**產能分母**內部矛盾：
    長停工位**不在分母裡**（`SPEC-DORMANT-CAPACITY.md` §3.1 把它排除了），停機卻進分子
    ⇒ 同一台機在兩張表上各講一句話——**與 0 號取消單「PM 板不算、設備板算」同一個形**。
  · 「修停用機隱形」那個顧慮**改由揭露吸收**（不是靠把時數算回去）：被扣掉的段逐筆記帳、
    `expand_for_children` 的 entry 帶 `dropped_dormant`，頁面出一行
    「N 張工單的停機段落在長停工位的長停日（Xh 已排除於停機統計）」。**只揭露、不改數字。**
  · **復機的正確路徑＝先在 cmms 登記 A11 復機事件**，登記完當天起停機自然照計
    （C-D1：登記才是事實）。用「有人在修它」去推翻登記＝拿推論覆蓋登記、方向反了。
  · 實作＝`inherited` 與 `explicit` **走同一支** `_subtract_dormant`（逐台北日曆日）；
    `kind` 標記**保留**（drill 出處不變、`explicit` 仍顯示為事實來源）。
  ⇒ 因為扣在 **clip 構造期**，heatmap 與 Now 欄共用同一份 clip、**結構上自動一致**。
  ⚠ CLN 的 `EID-27639`／`EID-27706`（MES 標 Out Of Service）**不在橋上、且永遠不會登記**
    （§4：Chris 確認正在正常生產）⇒ 繼承照落＝**永久正確**（C-D1：登記才是事實，
    MES `astStatus` 只是佐證）。**不得為此把它們塞進 `DORMANT_BRIDGE`。**

═══ 消費紀律 ═════════════════════════════════════════════════════════════════
  只 import `board_w8`（活躍窗）與 `board_dormant`（as-of 投影）——**不 import
  `board_eqmeeting`**（那邊 import 本檔，避免循環）；凍結核心零接觸。
"""
import sys
import inspect
import argparse
import datetime as _dt

import board_w8 as w8
import board_dormant as wdorm

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# 展開名冊（人工審定常數；拍板②④）
# ---------------------------------------------------------------------------
# 出處：cmms **顯式子女常數**實查（NOTE-25 ④：三母機逐台子女數、27600 扣 27617 後
#       與其餘兩台同數、雙邊收斂一字不差）＋ Board `EQMT_CATALOG` 逐一核對（全部子工位
#       都在名冊、順序即名冊順序）。
# ⚠ **`EID-27617`（CLN-2 SF E-Test 模組）永不入列**：不在 `EQMT_CATALOG`（頁面無列可放）、
#   且其邊型不對稱＝MES 登錄錯（cmms 2026-08-07 獨立得同結論、Board U5/U6 census 為第三方佐證）。
# ⚠ **絕不改成「讀 CSV 自動展開」**：`MES-dependent-equipment-export.csv` 的邊方向不可信
#   （配件當 parent：37432 掃碼器→04284 真機／09274 電測盒→05303 真機；模組當 parent：
#   24790 帶 3 子；三層鏈 Aligner37→Curer37→AttachRig3）——自動展開已被這批資料證否。
PARENT_EXPANSION = {'EID-55232': ('EID-54912', 'EID-54681', 'EID-54775', 'EID-54457'),
                    'EID-55546': ('EID-54358', 'EID-54395', 'EID-55074', 'EID-55513', 'EID-55177'),
                    'EID-54346': ('EID-55196', 'EID-54760', 'EID-54380')}

# clip 的兩種出處（drill 揭露用；Board 側承諾②「繼承/推定區間帶揭露旗」）
KIND_INHERITED = "inherited"   # 推論：母機停 ⇒ 子工位停（dormant 日扣除）
KIND_EXPLICIT = "explicit"     # 事實：scope event 明確含此工位（dormant 日照留）


# ---------------------------------------------------------------------------
# 0) 型別強制（與 board_eqmeeting._coerce_dt 同紀律：台北 naive、截微秒）
# ---------------------------------------------------------------------------

def _as_dt(v):
    """datetime（naive/aware）／ISO 字串 → 台北 naive datetime（截微秒）；解析不了→None。"""
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        d = v
        if d.tzinfo is not None:
            d = d.astimezone(_dt.timezone(_dt.timedelta(hours=8))).replace(tzinfo=None)
        return d.replace(microsecond=0)
    try:
        return w8._parse_dt(v)
    except (ValueError, TypeError):
        return None


def is_reactive(wo):
    """該單是否吃 scope_events（值域鐵則：**限 REACTIVE**；其餘 work_type 一律全量繼承）。"""
    return (((wo or {}).get("work_type") or "").strip().upper() == "REACTIVE")


# ---------------------------------------------------------------------------
# 1) 活躍窗 + 事件清洗
# ---------------------------------------------------------------------------

def active_window(wo, win_end):
    """normalized WO → (a_start, a_end_eff)（ongoing ⇒ a_end_eff＝win_end）；無有效窗→None。
       走 `w8.wo_active_window`（**不重寫一份窗語意**；髒/未來時戳 guard 全在那邊）。"""
    win_end = _as_dt(win_end)
    if win_end is None:
        return None
    win = w8.wo_active_window(wo, now=win_end)
    if win is None:
        return None
    a_start, a_end = win
    a_start = _as_dt(a_start)
    a_end_eff = win_end if a_end is None else _as_dt(a_end)
    if a_start is None or a_end_eff is None or a_end_eff <= a_start:
        return None
    return (a_start, a_end_eff)


def clean_scope_events(wo):
    """WO 上的 `scope_events` → ([{ts, scope, seat_units}, …] 升冪, n_dropped)。

       容忍兩形：已過 `w8.normalize_scope_events` 的（ts＝datetime）與 raw wire 形
       （ts＝UTC `Z` 字串）——**同一支解析**，讓 fixture 對版與 live 走同一條路。
       丟棄並計數：非 dict／壞 ts／`scope` 非 list／**空 scope（＝bad data，cmms 有 domain
       硬驗證，UI 結構上不產生空快照）**。
       ★ pin (c) 防禦性重排（同刻保留輸入順序＝穩定排序，後到者定調）。
       ★ pin (a)：欄位恆在、`[]`＝legacy 整機全程 ⇒ 回 ([], 0)、**不是缺資料**。"""
    # `board_capacity` imports this module for PARENT_EXPANSION. Importing
    # its approved cmms seat parser only while consuming events avoids a
    # module-initialization cycle without adding a second parser here.
    from board_capacity import fold_seat_id, fold_seat_members

    raw = (wo or {}).get("scope_events")
    if not isinstance(raw, list):
        return ([], 0)
    out, dropped = [], 0
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            dropped += 1
            continue
        ts = _as_dt(e.get("ts"))
        sc = e.get("scope")
        if ts is None or not isinstance(sc, (list, tuple)):
            dropped += 1
            continue
        raw_members = frozenset(s.strip() for s in sc if isinstance(s, str) and s.strip())
        if not raw_members:                  # 空 scope＝bad data，丟棄＋計數（絕不當「全恢復」）
            dropped += 1
            continue
        seat_units = tuple(sorted({unit for _parent, unit, _known in
                                   (fold_seat_id(member) for member in raw_members)
                                   if unit is not None}))
        members = fold_seat_members(raw_members)
        out.append((ts, i, members, seat_units))
    out.sort(key=lambda t: (t[0], t[1]))
    return ([{"ts": ts, "scope": sc, "seat_units": seat_units}
             for ts, _i, sc, seat_units in out], dropped)


def bad_scope_event_count(wos):
    """一批 WO 的壞 scope 事件總數（呼叫端自揭用；0＝乾淨）。"""
    return sum(clean_scope_events(w)[1] for w in (wos or []) if isinstance(w, dict))


# ---------------------------------------------------------------------------
# 2) scope_timeline — 活躍窗內的 effective scope 分段
# ---------------------------------------------------------------------------

def scope_timeline(wo, win_end):
    """normalized WO（含 additive `scope_events`）→ `[(start, end, frozenset(scope)), …]`。

       · **初始段**＝自活躍窗 start、scope＝`{asset_id}`（pin (b)：開單不落初始事件，
         初始整機態一律由 `asset_id`＋`opened_at` 推導）。
       · 每筆事件開新段（scope＝該筆**完整快照**，非增量）。
       · 末段止於活躍窗 end（ongoing ⇒ win_end；結單自動結束範圍＝讀活躍窗，無終止事件）。
       · **事件裁到活躍窗**：`ts < a_start` → 夾到 a_start（覆寫初始 scope）；
         `ts > a_end_eff` → 丟棄（結單後不得有 effective 事件；防禦）。
       · 相鄰同 scope 段合併（折返整機＝快照含母機 EID，自然回到「全機」語意）。
       非 REACTIVE／無事件（legacy `[]`）→ 單段整窗、scope＝`{asset_id}`＝整機全程。"""
    parent = (wo or {}).get("asset_id")
    if not isinstance(parent, str) or not parent:
        return []
    win = active_window(wo, win_end)
    if win is None:
        return []
    a_start, a_end_eff = win
    evs = clean_scope_events(wo)[0] if is_reactive(wo) else []

    cur = frozenset({parent})
    cursor = a_start
    out = []
    for e in evs:
        ts = e["ts"]
        if ts > a_end_eff:
            continue                                  # 活躍窗外（防禦；契約不該產生）
        if ts < cursor:
            ts = cursor                               # 夾到窗首＝覆寫初始整機 scope
        if ts > cursor:
            out.append((cursor, ts, cur))
            cursor = ts
        cur = e["scope"]
    if a_end_eff > cursor:
        out.append((cursor, a_end_eff, cur))

    merged = []
    for s, e_, sc in out:
        if merged and merged[-1][1] == s and merged[-1][2] == sc:
            merged[-1] = (merged[-1][0], e_, sc)
        else:
            merged.append((s, e_, sc))
    return [tuple(x) for x in merged]


# ---------------------------------------------------------------------------
# 3) child_clips — 單一子工位在單張母單下的區間（含 dormant 扣除）
# ---------------------------------------------------------------------------

def _subtract_dormant(a, b, child_eid, dormant_events):
    """把 [a,b) **逐台北日曆日**切開 → `(kept, dropped)`（各為 `[(s,e), …]`、升冪、不合併）。
       `kept`＝該工位**非**長停的那些日；`dropped`＝長停的那些日（＝要揭露的帳）。
       走 `wdorm.split_day_windows` + `wdorm.is_dormant`（**不自訂第二套日界／第二套判定**）。"""
    kept, dropped = [], []
    for d_s, d_e in wdorm.split_day_windows(a, b):
        if wdorm.is_dormant(child_eid, d_s.date(), dormant_events):
            dropped.append((d_s, d_e))
        else:
            kept.append((d_s, d_e))
    return (kept, dropped)


def _merge_same_kind(segs):
    """`[(s,e,kind), …]` → 相鄰**同 kind** 且端點相接者合併（逐日切開後接回去）；
       不同 kind 不合併＝drill 分得出出處。零/負長段丟棄。"""
    out = []
    for s, e, kind in segs:
        if e <= s:
            continue
        if out and out[-1][1] == s and out[-1][2] == kind:
            out[-1] = (out[-1][0], e, kind)
        else:
            out.append((s, e, kind))
    return [tuple(x) for x in out]


def child_clips_ex(wo, parent_eid, child_eid, win_end, dormant_events=None):
    """`child_clips` 的完整形 → `(clips, dropped_dormant)`，兩者皆 `[(start, end, kind), …]`。

       · **非 REACTIVE（PM／調機／暫桶／未知）＝全量繼承**（拍板⑤）：整個活躍窗一段
         `inherited`、**不吃 scope**、**不平均切分**。
       · **REACTIVE**：走 `scope_timeline`，逐段判
         `child ∈ scope` → **explicit（事實）**；否則 `parent ∈ scope` → **inherited（推論）**；
         兩者皆非 → 該段此工位不受影響、跳過。
         ★ 兩者同時成立時 **explicit 勝**（事實優先於推論；影響的是 drill 出處標記）。
       · **dormant（2026-08-14 翻面）**：**兩種 kind 一律**逐日扣掉該工位的長停日
         （`SPEC-DORMANT-CAPACITY.md` §5）——扣掉的段不消失，落到 `dropped_dormant`
         供呼叫端揭露（「工單 #N 指名長停工位、Xh 已排除」）。
         `dormant_events=None`＝用過渡橋常數；傳 `[]`＝關掉（測試用／負對照）。
       · 兩份清單各自做相鄰同 kind 合併。純函式；**不 mutate 輸入**。"""
    segs = scope_timeline(wo, win_end)
    if not segs:
        return ([], [])
    raw = []
    if not is_reactive(wo):                      # 全量繼承（PM 等）：整窗一段、不吃 scope
        s0, e0, _sc = segs[0]
        raw.append((s0, segs[-1][1], KIND_INHERITED))
    else:
        for s, e, sc in segs:
            if child_eid in sc:
                raw.append((s, e, KIND_EXPLICIT))
            elif parent_eid in sc:
                raw.append((s, e, KIND_INHERITED))

    kept_raw, drop_raw = [], []
    for s, e, kind in raw:
        keep, drop = _subtract_dormant(s, e, child_eid, dormant_events)
        kept_raw.extend((a, b, kind) for a, b in keep)
        drop_raw.extend((a, b, kind) for a, b in drop)
    return (_merge_same_kind(kept_raw), _merge_same_kind(drop_raw))


def child_clips(wo, parent_eid, child_eid, win_end, dormant_events=None):
    """單張母單 → 該子工位的區間清單 `[(start, end, kind), …]`（kind ∈ inherited/explicit）。
       ＝`child_clips_ex(...)[0]`（薄殼；被扣掉的帳走 `child_clips_ex` 的第二個回傳值）。"""
    return child_clips_ex(wo, parent_eid, child_eid, win_end,
                          dormant_events=dormant_events)[0]


# ---------------------------------------------------------------------------
# 3b) machine_scope_windows — **機器視角**投影（2026-08-14 A10 擴散 R1）
# ---------------------------------------------------------------------------
#   問題：`/utilization`／landing 的機隊成員是**父機級那一台**（`fetch_fleet` 排模組），
#   而母單的影響範圍會縮小到某幾個工位。範圍一縮小，**「機器」就不是停機的**
#   ——其餘工位還在生產，那台機器的稼動並沒有整段歸零。
#   ⇒ 父機級消費者要問的問題是「這台**機器**整台停了多久」＝`scope ∋ 母機 EID` 的那些段。
#
#   ★ 與 `child_clips` 是**同一條時間軸的兩個投影、永不相加**：
#     父機軸答「機器停多久」、子工位軸答「這個工位停多久」。母子雙計的顧慮由此解掉。
#   ⚠ **誠實邊界**：判準是字面的「母機 EID 在不在該段快照裡」。若某快照列出了**全部**
#     子工位但**沒有**列母機 EID（cmms 值域允許、UI 實務上不產生——縮到全部＝沒縮），
#     本函式判「機器不算停」＝**低估側**。刻意不加「⊇ 全部子工位就當整機」那條推論規則：
#     它要 Board 自己維護一份「全部子工位是誰」的判定，而那正是 A10 拒絕 runtime 推導的東西。


def machine_scope_windows(wo, win_end):
    """normalized WO → **機器（母機那一台）**真的整台停機的時間窗 `[(start, end), …]`。

       · REACTIVE：`scope_timeline` 中 **scope ∋ `asset_id`** 的段（初始整機段恆命中
         ＝pin (b) 由 `asset_id` 推導；縮小到工位後不再命中；折返整機再度命中）。
       · 非 REACTIVE（PM 等）：`scope_timeline` 回單段整窗、scope＝`{asset_id}`
         ⇒ 自動＝整個活躍窗（PM 不吃 scope＝拍板⑤）。
       · 無窗／壞資料 → `[]`。相鄰相接段合併。純函式、不 mutate 輸入。
       ⚠ 本函式**不做 dormant 扣除**——長停是**工位**的事實，父機那一台不長停
         （橋上／A11 登記的都是子工位 EID）。父機級的 dormant 由消費端自己判。"""
    parent = (wo or {}).get("asset_id")
    if not isinstance(parent, str) or not parent:
        return []
    out = []
    for s, e, sc in scope_timeline(wo, win_end):
        if parent not in sc:
            continue
        if out and out[-1][1] == s:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return [tuple(x) for x in out]


def machine_in_scope_now(wo, now):
    """此刻（`now`）**機器（母機軸）**是否被本單圈在停機範圍內 → bool（A10 擴散 R2、
       `/updown` 的 Now 判定用；2026-08-16）。

       · 非 REACTIVE／legacy `[]`＝整機 ⇒ True（pin (a)；PM 不吃 scope＝拍板⑤）。
       · REACTIVE ongoing 且此刻的 effective scope **不含** `asset_id` ⇒ False
         （範圍縮到某幾個工位＝其餘工位還在生產、「這台機器」此刻不算停）。
       · 活躍窗不涵蓋 `now`（已結單／壞資料無窗）⇒ True＝**不做主張**——呼叫端的
         「此刻」判定（`wem._now_block`）對非活躍單本來就不會判 DOWN，這裡回 False
         反而會把一張已結單從歷史統計的輸入裡踢掉（本函式只答「此刻」，不碰歷史）。
       ⚠ 與 `machine_scope_windows` 同一條誠實邊界（快照列全部工位但沒列母機＝低估側）；
         同樣**不做 dormant**——Now 答「此刻狀態」（SPEC-DORMANT §5.1 ②）。純函式。"""
    now = _as_dt(now)
    if now is None:
        return True
    segs = scope_timeline(wo, now)
    if not segs:
        return True
    s, e, sc = segs[-1]
    if e != now:                       # 末段不到 now＝此單此刻不活躍 ⇒ 不做主張
        return True
    return (wo or {}).get("asset_id") in sc


# ---------------------------------------------------------------------------
# 4) expand_for_children — 板級編排
# ---------------------------------------------------------------------------

def _clip_to_window(segs, ws, we):
    """`[(s,e,kind), …]` → 裁到 `[ws, we]`（None＝不裁）；裁空者丟棄。"""
    out = []
    for s, e, kind in segs:
        a = s if (ws is None or s > ws) else ws
        b = e if (we is None or e < we) else we
        if b > a:
            out.append((a, b, kind))
    return out


def expand_for_children(wos_by_parent, win_start, win_end, dormant_events=None):
    """`{母 EID: [normalized wo, …]}` →
       `{子 EID: [{"wo", "clips", "parent_eid", "dropped_dormant"}, …]}`。

       · 只展開 `PARENT_EXPANSION` 認得的母機（其餘 EID 一律忽略＝名冊外不猜拓撲）。
       · `clips`／`dropped_dormant` 皆額外裁到 `[win_start, win_end]`（板窗）。
       · **entry 的成立條件＝`clips` 或 `dropped_dormant` 任一非空**（2026-08-14 起）
         ——長停工位被整張單扣光時 `clips == []`，但那張單**正是**揭露行要講的東西，
         沒有 entry 就沒有帳可算。⚠ 下游的兩個消費點（`board_eqmeeting._inherited_segments`
         與 `_now_inherited`）都以 `if not clips: continue` 開頭 ⇒ **只有 dropped 的 entry
         對停機數字與 Now 欄逐位元無影響**（selftest 有釘）。
       · 順序＝母機常數順序 × 該母機 WO 的輸入順序（確定性）。
       呼叫端（`board_eqmeeting.build_row(inherited=…)`）再與 `wo_segments` 的
       **countable** 段交集 —— 兩軸交集在那裡完成，本函式不碰「算不算停機」。"""
    ws, we = _as_dt(win_start), _as_dt(win_end)
    out = {}
    for parent, kids in PARENT_EXPANSION.items():
        for wo in (wos_by_parent or {}).get(parent) or []:
            if not isinstance(wo, dict):
                continue
            for child in kids:
                raw_clips, raw_drop = child_clips_ex(wo, parent, child, we,
                                                     dormant_events=dormant_events)
                clips = _clip_to_window(raw_clips, ws, we)
                dropped = _clip_to_window(raw_drop, ws, we)
                if clips or dropped:
                    out.setdefault(child, []).append(
                        {"wo": wo, "clips": clips, "parent_eid": parent,
                         "dropped_dormant": dropped})
    return out


# ---------------------------------------------------------------------------
# --selftest（純函式、零 I/O、零網路、deterministic）
# ---------------------------------------------------------------------------

def _selftest():
    D = _dt.datetime
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    def secs(clips):
        return sum((b - a).total_seconds() for a, b, _k in clips)

    # ── A. PARENT_EXPANSION 名冊完整性（對 EQMT_CATALOG 交叉核對）────────
    # WP-10d（R-P11）：公開樹的名冊是合成的（母機各有 3／5／3 座、與私有名冊不同形）⇒ 具名母機與逐組座數只在私有樹釘死；
    #   公開樹由名冊常數推導（母機＝名冊第一／第二把鍵；第一組要有 ≥3 座且第 2、3 座＝橋上兩台）。
    import core_paths as _wp
    _pub = _wp.is_public_tree()
    if not _pub:
        import board_private_pins as _pp
    ck(len(PARENT_EXPANSION) == 3, "A1 展開名冊恰三組母機")
    ck(_pub or set(PARENT_EXPANSION) == {"EID-24742", "EID-16077", "EID-27600"}, "A2 三個母 EID")
    ck(all(len(v) >= 3 for v in PARENT_EXPANSION.values()) if _pub
       else all(len(v) == _pp.PINS["scope.parent_children_each_n"] for v in PARENT_EXPANSION.values()),
       "A3 每組子工位數應與私有釘值相同（27617 已排除）")
    allkids = [k for v in PARENT_EXPANSION.values() for k in v]
    ck(len(allkids) == len(set(allkids)) and (_pub or len(allkids) == _pp.PINS["scope.parent_children_total_n"]),
       "A4 子工位互不重複且總數應與私有釘值相同")
    ck("EID-27617" not in allkids, "A5 EID-27617（E-Test）永不入列")
    ck(not (set(PARENT_EXPANSION) & set(allkids)), "A6 母機不得同時是子工位（不加母機列、無自繼承）")
    # 交叉核對 EQMT_CATALOG：子工位皆在名冊、母機皆**不在**（＝A10 的硬錯本身）
    import board_eqmeeting as _wem      # 只在 selftest import（避免模組層循環）
    cat = {e for _a, _n, e in _wem.EQMT_CATALOG}
    for k in allkids:
        ck(k in cat, "A7 子工位須在 EQMT_CATALOG（有列可放）: %s" % k)
    for p in PARENT_EXPANSION:
        ck(p not in cat, "A8 母機不在 EQMT_CATALOG（拍板⑦不加母機列）: %s" % p)

    # ── golden 劇本（`_fixtures/wo_scope_events_golden.v1.json` 同形，手工內嵌＝零 I/O）──
    #    2026-08-20（台北）：08:50 開單於母機 → 10:30 放行只留子1 → 13:00 加停子2 →
    #    13:30 子2 恢復 → 15:40 結單。期望帳＝README「期望帳」那張表。
    _parents = list(PARENT_EXPANSION)
    P, P2 = _parents[0], _parents[1]       # 私有樹＝EID-24742／EID-16077（PM 全量繼承那組）
    _kids = PARENT_EXPANSION[P]
    K1, VX5, K3 = _kids[:3]
    # 第四座＝「從未被指名、只吃繼承」的座位；合成名冊的第一組只有 3 座 ⇒ 用占位字串（child_clips 不查名冊）
    K4 = _kids[3] if len(_kids) > 3 else P + "-K4"
    DAY = D(2026, 8, 20)

    def at(h, m):
        return DAY + _dt.timedelta(hours=h, minutes=m)

    WEND = D(2026, 8, 20, 23, 0)         # 板窗末（晚於結單；guard_ts 不得判未來）

    def mk_golden(events=None, closed=at(15, 40), status="CLOSED"):
        rec = {"work_order_no": 99999901, "asset_id": P, "work_type": "REACTIVE",
               "opened_at": at(8, 50), "closed_at": closed,
               "status_history": [
                   {"to_status": "OPEN", "changed_at": at(8, 50)},
                   {"to_status": "IN_PROGRESS", "changed_at": at(10, 25)}]
               + ([{"to_status": status, "changed_at": closed}] if closed else [])}
        w = w8.normalize_wo(rec)
        w["scope_events"] = [] if events is None else events
        return w

    GOLD_EVENTS = [{"ts": at(10, 30), "scope": [K1]},
                   {"ts": at(13, 0), "scope": [K1, VX5]},
                   {"ts": at(13, 30), "scope": [K1]}]
    wo = mk_golden(GOLD_EVENTS)

    # cmms NOTE-71: a seat asset in a scope snapshot belongs to its parent
    # machine for all existing scope consumers; the nest remains additive.
    seat_wo = w8.normalize_wo({"work_order_no": 99999900, "asset_id": "EID-26596",
                               "work_type": "REACTIVE", "opened_at": at(8, 50),
                               "closed_at": at(15, 40), "status_history": []})
    seat_wo["scope_events"] = [{"ts": at(10, 30), "scope": ["EID-26596-N2"]}]
    seat_events, seat_dropped = clean_scope_events(seat_wo)
    ck(seat_dropped == 0 and seat_events[0]["scope"] == frozenset({"EID-26596"})
       and seat_events[0]["seat_units"] == ("nest:2",),
       "A9 NOTE-71 seat scope folds to its parent and preserves additive nest metadata")
    seat_timeline = scope_timeline(seat_wo, WEND)
    ck(seat_timeline[-1][2] == frozenset({"EID-26596"}),
       "A10 folded NOTE-71 scope still covers the parent machine")

    # ── B. scope_timeline（四段、母機只在首段）─────────────────────────
    tl = scope_timeline(wo, WEND)
    ck(len(tl) == 4, "B1 scope_timeline 四段（初始＋三事件）: %d" % len(tl))
    ck(tl[0] == (at(8, 50), at(10, 30), frozenset({P})), "B2 初始段＝整機（pin (b) 由 asset_id 推導）")
    ck(tl[1] == (at(10, 30), at(13, 0), frozenset({K1})), "B3 放行後只剩子1")
    ck(tl[2] == (at(13, 0), at(13, 30), frozenset({K1, VX5})), "B4 加停子2")
    ck(tl[3] == (at(13, 30), at(15, 40), frozenset({K1})), "B5 末段止於結單（活躍窗尾）")
    ck(all(P not in sc for _s, _e, sc in tl[1:]), "B6 母機 10:30 後不在任何 scope（母子不雙計）")
    ck(sum((e - s).total_seconds() for s, e, _sc in tl) == 6 * 3600 + 50 * 60,
       "B7 Σ 分段 ≡ 活躍窗長 6h50m（切分不多算不少算）")

    # ── C. 期望帳逐值（dormant 關閉＝README 那張表）──────────────────────
    EXP = {K1: 6 * 3600 + 50 * 60, VX5: 2 * 3600 + 10 * 60,
           K3: 1 * 3600 + 40 * 60, K4: 1 * 3600 + 40 * 60}
    clips = {k: child_clips(wo, P, k, WEND, dormant_events=[]) for k in (K1, VX5, K3, K4)}
    for k in (K1, VX5, K3, K4):
        ck(abs(secs(clips[k]) - EXP[k]) < 1e-6,
           "C 期望帳 %s 應 %ds，實 %ds" % (k, EXP[k], secs(clips[k])))
    ck([(a, b, kd) for a, b, kd in clips[K1]]
       == [(at(8, 50), at(10, 30), KIND_INHERITED), (at(10, 30), at(15, 40), KIND_EXPLICIT)],
       "C5 子1＝繼承段＋指名段（不同 kind 不合併、drill 分得出出處）")
    ck([(a, b, kd) for a, b, kd in clips[VX5]]
       == [(at(8, 50), at(10, 30), KIND_INHERITED), (at(13, 0), at(13, 30), KIND_EXPLICIT)],
       "C6 子2＝繼承 1h40m ＋ 指名 30m（中間放行段沒有 clip）")
    ck(all(kd == KIND_INHERITED for _a, _b, kd in clips[K3] + clips[K4]),
       "C7 子3/子4 全是繼承（從未被指名）")
    ck(secs(clips[K3]) == secs(clips[K4]) == 6000.0, "C8 子3/子4 各 1h40m")

    # ── D. dormant 介面（SPEC §5，**2026-08-14 翻面**：推論扣、事實**也扣**）────
    #    ⚠⚠ **同一份判定在 repo 裡有兩處斷言，改一處必改另一處**：
    #        這裡的 D1／D2（＋D5–D9）↔ `_fixtures/selftest_fixtures._check_scope_events_golden`
    #        的 dormant 介面那段。2026-08-14 翻面時只改了這一半 ⇒ 對方那條紅了才被抓到。
    #    橋常數：26841（＝VX5）與 26887（＝K3）皆自 2023 年起長停（§9b 對帳後的生效日）。
    ck((VX5, K3) == tuple(r[0] for r in wdorm.DORMANT_BRIDGE)
       and (_pub or (VX5, K3) == ("EID-26841", "EID-26887")), "D0 子2/子3 恰為橋上那兩台（劇本與橋對得上）")
    dz = {k: child_clips(wo, P, k, WEND) for k in (K1, VX5, K3, K4)}     # 預設＝橋常數
    ck(dz[VX5] == [], "D1 長停工位：**直接指名（事實）也扣**（2026-08-14 裁決；舊規則保留 30m 已作廢）")
    ck(dz[K3] == [], "D2 長停工位只有繼承 ⇒ 全部跳過（不讓拔線機器因母機停而掉可用度）")
    ck(dz[K1] == clips[K1] and dz[K4] == clips[K4], "D3 非長停工位不受影響（逐位相同）")
    ck(wdorm.is_dormant("EID-27639", DAY.date()) is False
       and wdorm.is_dormant("EID-27706", DAY.date()) is False,
       "D4 CLN 的 27639/27706 **不在橋上** ⇒ 吃繼承＝永久正確（C-D1 登記才是事實）")
    # D5–D8 被扣掉的段要**可揭露**（不是消失）：dropped 逐值 + 秒數守恆
    _vx5, _d2 = child_clips_ex(wo, P, VX5, WEND)
    ck(_vx5 == [] and _d2 == [(at(8, 50), at(10, 30), KIND_INHERITED),
                             (at(13, 0), at(13, 30), KIND_EXPLICIT)],
       "D5 VX5 被扣掉的兩段（含 kind）逐值可揭露——扣掉≠消失，帳要記得出來")
    ck(abs(secs(_vx5) + secs(_d2) - EXP[VX5]) < 1e-6,
       "D6 秒數守恆：kept + dropped ≡ 關掉 dormant 時的期望帳（結構上不可能漏帳）")
    _k3, _d3 = child_clips_ex(wo, P, K3, WEND)
    ck(_k3 == [] and abs(secs(_d3) - EXP[K3]) < 1e-6, "D7 K3 全數落入 dropped（1h40m）")
    for _k in (K1, K4):
        ck(child_clips_ex(wo, P, _k, WEND)[1] == [],
           "D8 非長停工位 dropped 恆空（不是每台都在扣）: %s" % _k)
    # D9 負對照（承重）：**沒有 dormant 事實時，翻面前後逐位相同**
    for _k in (K1, VX5, K3, K4):
        ck(child_clips(wo, P, _k, WEND, dormant_events=[]) == clips[_k]
           and child_clips_ex(wo, P, _k, WEND, dormant_events=[])[1] == [],
           "D9 dormant_events=[] ⇒ clips 逐位同期望帳、dropped 空: %s" % _k)

    # ── E. PM 全量繼承（拍板⑤：各記完整區間、不平均切分、不吃 scope）────
    pm = w8.normalize_wo({"work_order_no": 99999902, "asset_id": P2, "work_type": "PM",
                          "opened_at": at(6, 0), "closed_at": at(16, 0),
                          "status_history": [
                              {"to_status": "OPEN", "changed_at": at(6, 0)},
                              {"to_status": "IN_PROGRESS", "changed_at": at(6, 0)},
                              {"to_status": "COMPLETED", "changed_at": at(16, 0)}]})
    pm["scope_events"] = []
    PK = PARENT_EXPANSION[P2]
    pmc = {k: child_clips(pm, P2, k, WEND, dormant_events=[]) for k in PK}
    for k in PK:
        ck(pmc[k] == [(at(6, 0), at(16, 0), KIND_INHERITED)],
           "E1 PM 每個子工位各記完整 10h（不是各 2.5h）: %s" % k)
    ck(sum(secs(pmc[k]) for k in PK) == len(PK) * 10 * 3600,
       "E2 Σ 子工位時數＝40h ⇒ **不可相加當機器停機時數**（配套鐵則，頁面/CSV 要講）")
    # PM 帶 scope_events 也**不得**被吃（值域鐵則：限 REACTIVE）
    pm2 = dict(pm)
    pm2["scope_events"] = [{"ts": at(9, 0), "scope": [PK[0]]}]
    ck(child_clips(pm2, P2, PK[1], WEND, dormant_events=[])
       == [(at(6, 0), at(16, 0), KIND_INHERITED)], "E3 PM 不吃 scope_events（即使誤帶）")

    # ── F. legacy `[]` ＝整機全程（pin (a)：勿防 absent、勿當缺資料）──────
    leg = mk_golden(None)
    for k in (K1, K4):
        ck(child_clips(leg, P, k, WEND, dormant_events=[])
           == [(at(8, 50), at(15, 40), KIND_INHERITED)], "F1 `[]` ⇒ 整機全程繼承: %s" % k)
    noattr = mk_golden(None)
    del noattr["scope_events"]                     # 防禦：欄位真的缺席也不得炸
    ck(len(child_clips(noattr, P, K1, WEND, dormant_events=[])) == 1, "F2 欄位缺席仍整機繼承（不炸）")

    # ── G. 折返整機（快照＝[母機]）＝ inference ──────────────────────────
    back = mk_golden([{"ts": at(10, 30), "scope": [K1]},
                      {"ts": at(12, 0), "scope": [P]}])          # 12:00 折返整機
    gb = child_clips(back, P, K3, WEND, dormant_events=[])
    ck(gb == [(at(8, 50), at(10, 30), KIND_INHERITED), (at(12, 0), at(15, 40), KIND_INHERITED)],
       "G1 折返整機 ⇒ 子3 兩段繼承（快照自癒；中間放行段無 clip）")
    ck(all(kd == KIND_INHERITED for _a, _b, kd in gb), "G2 折返整機是**推論**不是指名")
    gb1 = child_clips(back, P, K1, WEND, dormant_events=[])
    ck(secs(gb1) == 6 * 3600 + 50 * 60 and [kd for _a, _b, kd in gb1]
       == [KIND_INHERITED, KIND_EXPLICIT, KIND_INHERITED],
       "G3 子1 全程受影響、但 10:30–12:00 是指名、12:00 後回到推論")
    ck(child_clips(back, P, K3, WEND) == [], "G4 折返整機對長停工位仍是推論 ⇒ 跳過")

    # ── H. 空 scope＝bad data（丟棄＋計數，絕不當「全部恢復」）────────────
    bad = mk_golden([{"ts": at(10, 30), "scope": [K1]},
                     {"ts": at(12, 0), "scope": []},
                     {"ts": at(14, 0), "scope": "EID-24790"}])
    ev, nd = clean_scope_events(bad)
    ck(nd == 2 and len(ev) == 1, "H1 空 scope + 非 list scope 各丟一筆、計數 2")
    ck(child_clips(bad, P, K1, WEND, dormant_events=[])
       == clips[K1][:1] + [(at(10, 30), at(15, 40), KIND_EXPLICIT)],
       "H2 丟棄壞事件後＝只剩放行那一筆生效（不製造幽靈恢復）")
    ck(bad_scope_event_count([bad, wo, None, "x"]) == 2, "H3 bad_scope_event_count 加總（容忍雜質）")
    ck(clean_scope_events({"scope_events": None}) == ([], 0), "H4 非 list 欄位 → ([],0)")

    # ── I. ts 亂序 → 防禦性重排（pin (c)）────────────────────────────────
    shuffled = mk_golden([GOLD_EVENTS[2], GOLD_EVENTS[0], GOLD_EVENTS[1]])
    ck(scope_timeline(shuffled, WEND) == tl, "I1 事件亂序 → 重排後與升冪輸入逐位相同")
    ck([e["ts"] for e in clean_scope_events(shuffled)[0]]
       == [at(10, 30), at(13, 0), at(13, 30)], "I2 clean_scope_events 輸出升冪")
    # 同刻兩筆 → 後到者定調（穩定排序）
    same = mk_golden([{"ts": at(13, 0), "scope": [K1]}, {"ts": at(13, 0), "scope": [K1, VX5]}])
    ck(child_clips(same, P, VX5, WEND, dormant_events=[])[-1][2] == KIND_EXPLICIT,
       "I3 同刻兩筆＝後到者定調")

    # ── J. ongoing 單（未結單）＝活躍窗延伸到 win_end ─────────────────────
    ong = mk_golden(GOLD_EVENTS, closed=None)
    oc = child_clips(ong, P, K1, WEND, dormant_events=[])
    ck(oc[-1][1] == WEND, "J1 ongoing ⇒ 末段止於 win_end")
    ck(secs(oc) == (WEND - at(8, 50)).total_seconds(), "J2 ongoing 總長＝開單→win_end")
    ck(child_clips(ong, P, K4, WEND, dormant_events=[])
       == [(at(8, 50), at(10, 30), KIND_INHERITED)], "J3 ongoing 不改已放行工位的帳")

    # ── K. 活躍窗外事件裁掉 / 夾到窗首 ───────────────────────────────────
    late = mk_golden(GOLD_EVENTS + [{"ts": at(18, 0), "scope": [K4]}])   # 結單後（防禦）
    ck(scope_timeline(late, WEND) == tl, "K1 結單後事件丟棄（終態拒新增；防禦仍要對）")
    early = mk_golden([{"ts": at(7, 0), "scope": [K1]}] + GOLD_EVENTS)   # 開單前（防禦）
    et = scope_timeline(early, WEND)
    ck(et[0] == (at(8, 50), at(13, 0), frozenset({K1})),
       "VX5 開單前事件夾到窗首＝覆寫初始整機 scope（且與其後同 scope 段合併、不產生負長段）")
    ck(child_clips(early, P, K3, WEND, dormant_events=[]) == [],
       "K3 該情境下子3 從未受影響（初始整機態被覆寫）")

    # ── L. expand_for_children（板級編排）───────────────────────────────
    WS = D(2026, 8, 20, 0, 0)
    exp = expand_for_children({P: [wo], P2: [pm], "EID-99999": [wo]}, WS, WEND,
                              dormant_events=[])
    ck(set(exp) == set(PARENT_EXPANSION[P]) | set(PK), "L1 只展開名冊上的母機（99999 忽略）")
    ck(all(len(v) == 1 for v in exp.values()), "L2 每個子工位一筆 entry")
    ck(exp[K1][0]["parent_eid"] == P and exp[PK[0]][0]["parent_eid"] == P2, "L3 帶 parent_eid")
    ck(exp[K1][0]["wo"] is wo, "L4 entry 帶原 WO 物件（呼叫端據以跑 wo_segments）")
    ck(abs(sum((b - a).total_seconds() for a, b, _k in exp[VX5][0]["clips"]) - EXP[VX5]) < 1e-6,
       "L5 clips 與 child_clips 一致")
    ck(all(v[0]["dropped_dormant"] == [] for v in exp.values()),
       "L5b dormant 關掉 ⇒ 每個 entry 的 dropped_dormant 皆空（additive 鍵恆在、不是有時才有）")
    # dormant 預設（橋）：VX5/K3 clips 全被扣光，但**entry 仍在**＝揭露帳的來源（2026-08-14）
    exp_d = expand_for_children({P: [wo]}, WS, WEND)
    ck(VX5 in exp_d and K3 in exp_d, "L6 長停工位仍成 entry（否則揭露行沒有帳可算）")
    ck(exp_d[VX5][0]["clips"] == [] and exp_d[K3][0]["clips"] == [],
       "L6b 長停工位 clips 全空（explicit 也扣＝2026-08-14 翻面）")
    ck(exp_d[VX5][0]["dropped_dormant"] and exp_d[K3][0]["dropped_dormant"],
       "L6c 長停工位 dropped_dormant 非空＝可揭露")
    ck(all(exp_d[_k][0] == exp[_k][0] for _k in (K1, K4) if _k in exp),
       "L6d 非長停工位的 entry 逐位相同（橋開不開都一樣）")
    # 板窗裁切：窗只到 12:00 ⇒ clips 全裁
    exp_c = expand_for_children({P: [wo]}, WS, D(2026, 8, 20, 12, 0), dormant_events=[])
    ck(all(b <= D(2026, 8, 20, 12, 0) for v in exp_c.values() for _a, b, _k in v[0]["clips"]),
       "L7 clips 裁到板窗末")
    ck(expand_for_children({}, WS, WEND) == {} and expand_for_children(None, WS, WEND) == {},
       "L8 空/None → {}")
    ck(expand_for_children({P: [None, "x"]}, WS, WEND) == {}, "L9 雜質容忍")

    # ── M. 壞資料容忍 ────────────────────────────────────────────────────
    ck(scope_timeline({}, WEND) == [] and scope_timeline(None, WEND) == [], "M1 空 WO → []")
    ck(scope_timeline({"asset_id": P}, WEND) == [], "M2 無窗 → []")
    ck(child_clips({"asset_id": P, "work_type": "REACTIVE"}, P, K1, WEND) == [], "M3 無窗 → []")
    ck(is_reactive({"work_type": "reactive"}) and not is_reactive({"work_type": "PM"})
       and not is_reactive({}), "M4 is_reactive 大小寫/缺欄")
    # UTC 'Z' wire 形 ts 與 datetime 形同義（fixture 對版與 live 走同一支解析）
    wire = mk_golden([{"ts": "2026-08-20T02:30:00Z", "scope": [K1]},
                      {"ts": "2026-08-20T05:00:00Z", "scope": [K1, VX5]},
                      {"ts": "2026-08-20T05:30:00Z", "scope": [K1]}])
    ck(scope_timeline(wire, WEND) == tl, "M5 wire UTC-Z 字串 ts ≡ 台北 naive datetime（+8 還原）")
    # 不 mutate 輸入
    before = [dict(e) for e in wo["scope_events"]]
    child_clips(wo, P, K1, WEND)
    ck([dict(e) for e in wo["scope_events"]] == before, "M6 child_clips 不 mutate 輸入")

    # ── P. machine_scope_windows（機器視角；2026-08-14 A10 擴散 R1）─────────
    #    golden 劇本的機器視角＝**只有初始整機段 1h40m**（08:50–10:30）——10:30 之後範圍
    #    縮到工位，其餘工位還在生產 ⇒ 「這台機器」不算整台停。**不是** 6h50m。
    msw = machine_scope_windows(wo, WEND)
    ck(msw == [(at(8, 50), at(10, 30))], "P1 機器視角＝只有初始整機段（縮小後不算機器停）")
    ck(sum((b - a).total_seconds() for a, b in msw) == 1 * 3600 + 40 * 60,
       "P2 機器視角恰 1h40m（≠ 子工位 K1 的 6h50m；兩軸永不相加）")
    ck(machine_scope_windows(back, WEND)
       == [(at(8, 50), at(10, 30)), (at(12, 0), at(15, 40))],
       "P3 折返整機 ⇒ 機器再度整台停（兩段）")
    ck(machine_scope_windows(pm, WEND) == [(at(6, 0), at(16, 0))],
       "P4 非 REACTIVE（PM）＝整個活躍窗（不吃 scope）")
    ck(machine_scope_windows(leg, WEND) == [(at(8, 50), at(15, 40))],
       "P5 legacy `[]` ⇒ 整機全程（pin (a)）")
    ck(machine_scope_windows({}, WEND) == [] and machine_scope_windows(None, WEND) == [],
       "P6 空/壞 WO → []")
    ck(machine_scope_windows(mk_golden([{"ts": at(10, 30), "scope": [K1, VX5, K3, K4]}]), WEND)
       == [(at(8, 50), at(10, 30))],
       "P7 誠實邊界：快照列全部四個工位但沒列母機 EID ⇒ 判機器不停（低估側、見函式註解）")

    # ── P8+ machine_in_scope_now（R2、/updown Now 判定；2026-08-16）────────────
    ong2 = mk_golden(GOLD_EVENTS, closed=None)         # ongoing、10:30 起縮到 K1（未折返）
    ck(machine_in_scope_now(ong2, at(9, 0)) is True, "P8 縮範圍前＝整機 ⇒ 此刻機器在範圍內")
    ck(machine_in_scope_now(ong2, at(14, 0)) is False,
       "P9 ongoing 且此刻 scope 只剩工位 ⇒ 機器此刻不算停（/updown 不亮 DOWN）")
    back2 = mk_golden([{"ts": at(10, 30), "scope": [K1]},
                       {"ts": at(12, 0), "scope": [P]}], closed=None)
    ck(machine_in_scope_now(back2, at(13, 0)) is True, "P10 折返整機 ⇒ 此刻再度在範圍內")
    ck(machine_in_scope_now(wo, at(20, 0)) is True,
       "P11 已結單（活躍窗不涵蓋 now）⇒ 不做主張回 True（Now 判定自然不會 DOWN）")
    ck(machine_in_scope_now(mk_golden(None, closed=None), at(14, 0)) is True,
       "P12 legacy [] ongoing ⇒ 整機、此刻在範圍內")
    ck(machine_in_scope_now(pm, at(9, 0)) is True and machine_in_scope_now(None, at(9, 0)) is True,
       "P13 PM（非 REACTIVE）恆整機；壞 WO 不做主張")

    # ── N. 結構釘：本模組不得自取 now（投影一律吃參數；比照 wdorm C-D6）──
    for fn in (scope_timeline, child_clips, child_clips_ex, machine_scope_windows,
               machine_in_scope_now, expand_for_children, active_window,
               clean_scope_events, is_reactive, bad_scope_event_count):
        sig = inspect.signature(fn)
        for pname, p in sig.parameters.items():
            ck(not isinstance(p.default, (_dt.date, _dt.datetime)),
               "N %s(%s) 不得有日期預設值" % (fn.__name__, pname))
    src = inspect.getsource(sys.modules[__name__])
    body = src.split("def _selftest")[0]
    ck("datetime.now" not in body and ".today()" not in body and "utcnow" not in body,
       "N9 模組本體零 now/today/utcnow（窗一律由呼叫端傳）")
    # N10 零 I/O 鐵則：名冊是**人工審定常數**，不得改成 runtime 讀 CSV/JSON
    #     （`MES-dependent-equipment-export.csv` 的邊方向不可信；M4 runtime-discovery 紅線）。
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())   # 去註解後掃程式碼本體
    for forbidden in ("open(", "import csv", "json.load", "os.environ", "requests"):
        ck(forbidden not in code, "N10 模組本體不得出現 %r（純函式零 I/O、名冊是常數）" % forbidden)

    print("[board_scope] selftest PASS — %d 項斷言全過（A10 母單展開；規格＝CONCEPT §19.3 A10）："
          "展開名冊（27617 排除、子工位皆在 EQMT_CATALOG、母機皆不在＝A10 的硬錯）；"
          "scope_timeline（初始由 asset_id 推導 pin(b)／完整快照／折返整機／相鄰同 scope 合併／"
          "Σ 分段 ≡ 活躍窗）；golden 劇本期望帳逐值（子1 6h50m／子2 2h10m／子3·4 各 1h40m／"
          "母機 10:30 後不在任何 scope）；**dormant 翻面（繼承與指名皆扣＋dropped 逐值可揭露"
          "＋秒數守恆＋dormant_events=[] 負對照逐位相同）＝SPEC §5 的 2026-08-14 裁決**；"
          "機器視角 machine_scope_windows（golden＝1h40m 而非 6h50m、折返兩段、PM 整窗、"
          "全工位快照的低估側邊界）；"
          "PM 全量繼承（各記完整區間、不吃 scope、Σ 不可相加）；legacy [] 整機全程 pin(a)；"
          "空 scope 丟棄計數；ts 亂序重排 pin(c)；ongoing／窗外事件裁切；wire UTC-Z ≡ naive；"
          "不 mutate；結構釘零 now。純函式零 I/O。" % n)
    return n


def main():
    ap = argparse.ArgumentParser(
        description="A10 母機工單停機範圍展開（純函式；app 由 board_eqmeeting 消費）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（零 I/O、零網路）")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
