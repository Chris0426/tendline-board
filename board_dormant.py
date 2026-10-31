# -*- coding: utf-8 -*-
"""
board_dormant.py — 資產排程狀態（長停 / 復機）as-of 投影（純函式、零 I/O、selftest）
=====================================================================================

規格權威＝`SPEC-DORMANT-CAPACITY.md`（v0.2）。本檔實作 §8 開工順序的第 1 項：
**過渡橋常數 `DORMANT_BRIDGE` ＋ as-of 投影純函式**。

═══ 這在答什麼問題 ═══════════════════════════════════════════════════════════
  「某台機器從某天起不排生產」是**有人做出的決策**（有決策者、生效日、原因、反向操作），
  不是可以從 MES 沉默推導出來的東西（§9 翻案存證：v0.1 的「自動偵測為主」已被駁倒）。
  正解＝把它記成 append-only 的排程狀態事件流，Board 端只做 **as-of 投影**。

  背景事實（Q16 實測）：CAL Gen2 Bay 2（EID-26841）末筆生產 2025-10-23、
  Bay 3（EID-26887）末筆 2025-10-27（相隔 4 天＝同一事件）；兩者歷史段數與在役的
  Bay 1/4 同量級＝曾是主力工位。**產能只剩名目一半已 9.5 個月**，而頁面至今顯
  「● UP・可用度 100.0%」＝替一台拔線的機器背書。Chris 確認：預期性長期停機、
  **不排生產、不算 down**（M2＝線外查修、M3＝斷聯防訊號干擾）。

═══ 橋的形狀＝事件流的形狀（換源零改動）═══════════════════════════════════════
  SoR 最終是 cmms（需求＝`CONCEPT.md` §19.3 **A11**；契約
  `GET /assets/{asset_id}/schedule-state-events` → 裸 list、只曝 effective、`ts_effective` 升冪）。
  cmms 已 accept 並**明確背書「過渡橋形狀即事件形」**（NOTE-26/27，SPEC §6/§6.1）
  ⇒ A11 落地時只換 `events=` 的來源（把 cmms 回來的列餵進同一組函式），
  **本檔的投影邏輯一個位元都不必動**、`DORMANT_BRIDGE` 退役。

═══ as-of 語意（SPEC §6.1；cmms 逐條釘死，照抄不自訂）═══════════════════════════
  1. 軸＝**Asia/Taipei 日曆日**。「D 日長停」＝該資產在 **D 日台北日終**之前最後一筆
     effective 事件的 `state` 為 `not_scheduled`。⇒ 日粒度、當日以最後一筆事件定調，
     **不是「事件時刻之後才生效」的瞬時語意**。
  2. 我方全鏈時戳都是台北 naive（MES 慣例）⇒ naive 午夜切日即台北日界，不轉 UTC、
     不做 instant 比較（C-D4 的歷史分母切換就靠這條對齊）。
  3. **不做母子傳遞**：長停只作用在事件所指的 EID 本身，母機長停不連帶子工位、反之亦然。
  4. ★ **兩條 lane（A13 `sub_unit`，2026-09-03 接線）**：事件列的 `sub_unit is None`＝整台機、
     非 None（`nest:2`…）＝單一工位。**本模組只認整機 lane**——`events_for` 在匯流口把帶
     `sub_unit` 的列整列濾掉（結構性防呆），工位軸由 `board_capacity._nest_effective_states`
     專責。與 cmms 語意逐字同向（A13 ⑤-2：工位 lane 不影響整機謂詞）。

═══ 鐵則（SPEC §2；違反即錯）═══════════════════════════════════════════════════
  · **C-D1** 狀態是登記的事實、不是推導——本檔**永不**看 MES 沉默、`astStatus` 或任何門檻。
  · **C-D2** 旗名叫 `dormant`，刻意不叫 out_of_service（MES `astStatus` 有同名值但語意不同）。
  · **C-D3** 排除必須看得見：呼叫端拿 `capacity_split(...)["dormant"]` 去頁面上寫
    「N 個工位不計產能（自 YYYY-MM-DD）」。靜默排除＝獎勵不修機器。
  · **C-D4** 判定 as-of 該窗——`capacity_split` **逐日**算在役工位數（歷史窗會在正確的
    那一天自己換分母），不是拿今天的結論回頭套整窗。
  · **C-D5** 產能分母改法**只作用 CAL Gen2**；CLN-2 的 dormant 只作顯示態。
    （本檔不認得站別＝呼叫端負責閘；橋常數本身也只有 CAL 兩筆。）
  · **C-D6** dormant 不落盤、不入日帳——**永遠現算**。因此本檔所有投影函式
    **一律吃日期參數、函式內絕不取 now**（selftest 有結構釘）。

═══ 消費紀律 ═════════════════════════════════════════════════════════════════
  本檔零 import 專案模組（連 board_data 都不 import＝避免把 pyodbc 依賴拖進純函式層）。
  `PRODUCING_STATE` 是 §23 canonical 中文 key 的字面複本，與 `board_data.STATE_ORDER[0]`
  同值；selftest 不 import 它（保持零相依），改由呼叫端的 selftest 交叉驗。
"""
import sys
import inspect
import argparse
import datetime as _dt

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 事件 state 值域（cmms A11 契約；只有這兩值，v1 無 retract）。
STATE_NOT_SCHEDULED = "not_scheduled"
STATE_IN_PRODUCTION = "in_production"

# §23 canonical 生產態中文 key（＝board_data.STATE_ORDER[0]）。刻意寫死字面值而非 import：
# 本模組保持「零專案相依的純函式層」。改名不可能發生（canonical key 不翻、不改）。
PRODUCING_STATE = "生產"


# ---------------------------------------------------------------------------
# 過渡橋（SPEC §1）：cmms A11 排上線前，Board 人工審定常數先撐
# ---------------------------------------------------------------------------
# 形＝(eid, ts_effective(台北日曆日 YYYY-MM-DD), state, reason_zh, reason_en, source)
#   ★★ 生效日＝**cmms A11 登記值**（NOTE-30；末筆真工單開單日、人工判讀），
#     **2026-08-10 由 Board 的 MES 逐月 segment 對帳確認為正確**（探針
#     `_A11-effective-date-recon.sql`、結論全文＝SPEC §9b）。
#   ⚠⚠ **舊推導法「生效日＝末筆 segment 次日」已作廢，勿復活**（原值 2025-10-24／2025-10-28
#     錯了 2 年 3 個月／2 年 6 個月）。它被 2025 年的**工程試跑段**騙了：兩台在
#     2023 斷點之後到 2025-10 之間各只剩 **246 段/38 天**（M2）與 **147 段/31 天**（M3），
#     ＝同期在役對照機 M4 的 **0.1–0.2%**；而 2025-08~10 那幾叢零星段發生在 **M1 完全零段
#     的全機低產期**＝工程活動不是生產。**「最後一筆 segment」≠「最後一天生產」**——
#     長停機器仍會因為試跑／校機／維護留下稀疏訊號，拿 MAX(時戳) 當生效日必然嚴重低估停期。
#     這正是 SPEC §9 翻案的同一課：**停產是決策、要讀登記，不要從沉默反推。**
#   ★ reason 在 cmms A11 是**自由文字、不鑄 vocab**（SPEC §6）⇒ 顯示層當不可信任自由文字
#     處理：逐字顯示、不做語意分類、不據以分色。橋常數這裡雙語只是因為橋自己寫得出來。
#   ⚠ 橋只收 Chris 明示的 CAL 兩筆。`EID-09670`／`EID-27639`／`EID-27706` 三台
#     Out Of Service（SPEC §4）**刻意不收**——它們不影響任何分母（C-D5）、等 A11 登記。
#   ★ 2026-09-03 加註（**橋常數本身不動**）：cmms 已把 `EID-27639`／`EID-27706` 登成長停
#     （生效日 `2024-08-17`、reason「實際停用日不可考」；relay
#     `internal-relay-note` ■二、SPEC §11.6 的 ✅ 表）。
#     **橋刻意仍不收這兩顆**——A11 已是它們的權威來源，往橋裡補＝第二份會漂移的事實
#     （SPEC §7 反模式「橋是橋、不是家」）。`EID-09670` 不在那 11 筆內、狀態未變。
#   ⚠ SPEC §7 反模式：「橋常數長出第三筆以上卻不問『A11 怎麼還沒排上』」——橋是橋，不是家。
DORMANT_BRIDGE = [('EID-54681',
                   '2024-03-19',
                   'not_scheduled',
                   '線外檢修（合成）',
                   'off-line check (synthetic)',
                   'synthetic roster (hand-written)'),
                  ('EID-54775',
                   '2023-11-28',
                   'not_scheduled',
                   '暫停排產（合成）',
                   'paused (synthetic)',
                   'synthetic roster (hand-written)')]


# ---------------------------------------------------------------------------
# A11 基線播種名冊（**碼側鏡射**；正本＝`SPEC-DORMANT-CAPACITY.md` §11.4.1）
# ---------------------------------------------------------------------------
# ★ 這**不是**第二份事實、也**不是**橋常數的延伸：它只回答「換源時要向 cmms 問哪些 EID
#   的事件流」這一個問題（覆蓋面名冊），**不帶生效日、不帶原因、不做任何投影**。
#   長停與否一律以 cmms 回來的事件流為準；這裡列到的機台若 cmms 沒登，事件流就是空 `[]`
#   ＝全程排產中（SPEC §6.1「空 [] 不分兩路」）。
# ★ 為什麼要有碼側常數：`board_data.a11_eids()` 的覆蓋面必須涵蓋「已登長停但沒有子單元
#   名冊可依附」的整機（形①／形② 的母機清單答不出它們）。**唯一出處原則**照舊——
#   本常數的正本是 SPEC §11.4.1 的 ✅ 表，SPEC 該節已註明「碼側鏡射＝本常數」；
#   ⚠ 改這裡等於改登記名單的覆蓋面，**必須同時改 SPEC §11.4.1、否則兩邊會漂移**。
# ★ 逐顆出處（Chris 2026-09-02 親裁、cmms 2026-09-03 已逐筆登記＝§11.6 的 ✅ 表 EID 級 7 筆）：
#     EID-25270  print quality check 長期停用（生效日 2025-07-16）
#     EID-26554  壞了、目前沒有要修復（2025-12-22）
#     EID-27163  硬體升級後未過 pilot run、無上線計畫（2025-07-14；★ 三顆一起、不可拆）
#     EID-27208  同上一組（2025-07-14）
#     EID-25421  同上一組（2025-07-14）
#     EID-27639  CLN Gen2 SF M1，停用日不可考（登 2024-08-17＝探針窗起點）
#     EID-27706  CLN Gen2 SF M3，同上
# ⚠ **不得把「不登」的那幾顆補進來**（`27854`／`26379`／`34624`／`35641`／`37457`／`37549`）——
#   §11.4.1 明文裁決不登；`27854` 是**待命不是長停**（這條語意每隔幾輪就有人想推翻一次）。
#     ⚠ **`EID-25270` 已移出本常數**（Chris 當面裁：該機已退役、實體移出產線
#       ⇒ 同時移出 `EQMT_CATALOG`）。**退役 ≠ 長停**：長停講的是「還在線上、只是不排產」，
#       退役講的是「不在線上了」——把退役機留在長停名冊會讓 `/equipment` 每天提醒一台
#       已經不存在的機器。連帶：長停 chip 同步調整。存證＝`SPEC-DORMANT-CAPACITY.md`
#       §11.4.1 #1 那一列的註記與 `SPEC-LANDING.md` §15.5。
A11_BASELINE_EIDS = ('EID-55196', 'EID-54380', 'EID-54791', 'EID-55252', 'EID-55338')


# ---------------------------------------------------------------------------
# 0) 型別強制
# ---------------------------------------------------------------------------

def _as_date(x):
    """date / datetime（台北 naive）/ 'YYYY-MM-DD' 字串 → `datetime.date`。
       ★ 一律走日曆日（SPEC §6.1-1）——datetime 只取 `.date()`，**不做瞬時比較**。
       壞值 → ValueError（寧可炸也不要靜默算錯一整條分母）。"""
    if isinstance(x, _dt.datetime):
        return x.date()
    if isinstance(x, _dt.date):
        return x
    if isinstance(x, str):
        s = x.strip()
        if len(s) >= 10:
            return _dt.date.fromisoformat(s[:10])
    raise ValueError("not a date: %r" % (x,))


def _as_dt(x):
    """datetime / 'ISO 字串' → datetime（台北 naive）；其他 → None（呈現層資料可能是快照字串）。"""
    if isinstance(x, _dt.datetime):
        return x
    if isinstance(x, str):
        try:
            return _dt.datetime.fromisoformat(x.strip())
        except ValueError:
            return None
    return None


def _synth_process():
    """合成進程？（合成產線 WP-4b R-C3）＝**已載入的** `core_source` 鎖定的進程旗標；沒載入 ⇒ False。

    ★ 刻意**不 import**（本檔零 import 專案模組的消費紀律照舊）：app／board_data／合成啟動器都在本檔被
      呼叫之前 import 了 `core_source`；獨立跑本檔 selftest 時它不在 ⇒ 恆 False ⇒ 行為與改動前逐位元同。"""
    m = sys.modules.get("core_source")
    try:
        return bool(m is not None and m.is_synth_process())
    except Exception:                          # noqa: BLE001（旗標讀不到＝當非合成；不因此炸投影）
        return False


def bridge_rows():
    """過渡橋常數的**唯一讀口**（WP-4b R-C3）。合成進程 ⇒ `[]`（橋上是工廠登記的真實停產日期與原因，
    合成世界不得讀它；取回失敗時長停事實就是「不顯示」，不是退回真實常數）；其餘進程 ⇒ `DORMANT_BRIDGE` 本體。"""
    return [] if _synth_process() else DORMANT_BRIDGE


def _rows(events):
    """events=None → 過渡橋常數（合成進程＝空，見 `bridge_rows`）；否則照傳（A11 換源時餵 cmms 事件列＝同形）。"""
    return bridge_rows() if events is None else (events or [])


# ---------------------------------------------------------------------------
# 1) events_for — 單一 EID 的 effective 事件流（升冪）
# ---------------------------------------------------------------------------

def events_for(eid, events=None):
    """→ [{eid, ts_effective(date), state, reason_zh, reason_en, source}]，**ts_effective 升冪**。
       同日多筆＝保留輸入順序（穩定排序）→ 後到者為當日定調（SPEC §6.1-1「當日以最後一筆定調」）。
       未知 EID → []（＝該資產全程排產中；**不是查無資料**，勿分兩路，SPEC §6.1）。

       ★★ **結構性防呆（2026-09-03 加）：帶 `sub_unit` 的列一律忽略。**
         A13 的 `sub_unit` 欄把事件分成兩條 lane：`sub_unit is None`＝整台機、
         非 None＝**單一工位**（`nest:2`…）。本模組的謂詞是**整機**謂詞
         （`is_dormant`／`dormant_info`／`dormant_set`／`capacity_split` 全走這裡），
         工位 lane 進來就會把「Press#2 的第 2 座停了」誤讀成「Press#2 整台停了」——
         分母、著色、A10 clip 三處同時錯，而畫面完全正常。
         這條與 cmms 的語意**逐字同向**（A13 ⑤-2：批次讀 `26596`／`26718`／`12724`
         三台整機仍 `in_production`＝工位 lane 不影響整機謂詞）。
       ⚠ 這個過濾**放在這裡而不是放在呼叫端**，是因為呼叫端有五處以上（`app.py`
         `/equipment`、`board_scope` clip、`board_eqmeeting`、`board_export`…）
         都是直接把整份 A11 事件流餵進來；防呆要放在**唯一的匯流口**才擋得住。
         工位軸的正確消費者是 `board_capacity._nest_effective_states`（它自己讀
         `event["sub_unit"]`），那條路徑**不經過本函式**。"""
    out = []
    for i, row in enumerate(_rows(events)):
        if isinstance(row, dict):
            e = row.get("eid")
            if e != eid:
                continue
            if row.get("sub_unit") is not None:
                continue                      # ★ 工位 lane：整機謂詞永不消費（見 docstring）
            out.append((_as_date(row["ts_effective"]), i, {
                "eid": e, "ts_effective": _as_date(row["ts_effective"]),
                "state": row.get("state"),
                "reason_zh": row.get("reason_zh"), "reason_en": row.get("reason_en"),
                "source": row.get("source")}))
            continue
        e, ts, state = row[0], row[1], row[2]
        if e != eid:
            continue
        rz = row[3] if len(row) > 3 else None
        re_ = row[4] if len(row) > 4 else None
        src = row[5] if len(row) > 5 else None
        out.append((_as_date(ts), i, {"eid": e, "ts_effective": _as_date(ts), "state": state,
                                      "reason_zh": rz, "reason_en": re_, "source": src}))
    out.sort(key=lambda t: (t[0], t[1]))
    return [d for _ts, _i, d in out]


# ---------------------------------------------------------------------------
# 2) is_dormant / dormant_info / dormant_set — as-of 投影（SPEC §6.1）
# ---------------------------------------------------------------------------

def is_dormant(eid, day, events=None):
    """「D 日長停」＝生效日 ≤ D 的**最後一筆** effective 事件 state 為 `not_scheduled`。
       `day`＝台北日曆日（date / datetime / 'YYYY-MM-DD'）。
       ★ 生效日當天就算長停（邊界釘死：26841 在 2025-10-23 False、2025-10-24 True）。
       ★ 無事件 → False（全程排產中）。★ 函式內**不取 now**（C-D6）。"""
    d = _as_date(day)
    last = None
    for ev in events_for(eid, events):
        if ev["ts_effective"] <= d:
            last = ev
        else:
            break
    return bool(last and last["state"] == STATE_NOT_SCHEDULED)


def dormant_info(eid, day, events=None):
    """dormant → {"since": 'YYYY-MM-DD', "reason_zh", "reason_en"}；否則 None。
       `since`＝**本段長停的起點**（往回走連續的 not_scheduled 事件；更正／補登記造成的
       連續同態事件不會讓「自 X 日」跳到更晚的那一筆）；reason 取 effective（最後一筆）
       ＝現在的原因。復機後再度長停 ⇒ since 是**新那一段**的起點，不是最早那次。"""
    d = _as_date(day)
    evs = [e for e in events_for(eid, events) if e["ts_effective"] <= d]
    if not evs or evs[-1]["state"] != STATE_NOT_SCHEDULED:
        return None
    i = len(evs) - 1
    while i > 0 and evs[i - 1]["state"] == STATE_NOT_SCHEDULED:
        i -= 1
    return {"since": evs[i]["ts_effective"].isoformat(),
            "reason_zh": evs[-1]["reason_zh"], "reason_en": evs[-1]["reason_en"]}


def unlisted_long_stop_registrations(day, events, board_eids):
    """cmms A11 事件流中「長停、但不在本板名冊」的 EID → `[{eid, since}]`。

    這是 §11.6 末段的查明揭露，不改任何設備板分母或長停裁決。`events is None`
    表示 cmms A11 未接通／失敗：**不退回 bridge 常數**，因為 chip 的字面是 cmms
    registration。每筆狀態都經 `events_for()` / `dormant_info()`，因此沿用唯一的
    同日排序、sub_unit lane 排除與復機後失效語意；不是另一支 raw-event 解讀器。
    """
    if events is None:
        return []
    known = {str(e) for e in (board_eids or ()) if str(e).strip()}
    candidates = set()
    for row in _rows(events):
        eid = row.get("eid") if isinstance(row, dict) else (row[0] if row else None)
        if eid and str(eid) not in known and events_for(eid, events):
            candidates.add(str(eid))
    out = []
    for eid in sorted(candidates):
        info = dormant_info(eid, day, events)
        if info is not None:
            out.append({"eid": eid, "since": info["since"]})
    return out


def dormant_set(day, events=None):
    """該日全部長停 EID（frozenset）。板面著色用：一次算好、逐列查。"""
    d = _as_date(day)
    eids = set()
    for row in _rows(events):
        eids.add(row.get("eid") if isinstance(row, dict) else row[0])
    return frozenset(e for e in eids if e and is_dormant(e, d, events))


# ---------------------------------------------------------------------------
# 3) 日窗切分 — [win_start, win_end] → 逐台北日曆日（首尾可為部分日）
# ---------------------------------------------------------------------------

def split_day_windows(win_start, win_end):
    """→ [(day_start_dt, day_end_dt), …]（升冪、首尾按實際窗裁切、相加恰＝窗長）。
       與 `board_eqmeeting.day_windows` 的差別：那支以 win_end 為錨往回數 n_days 個
       **完整**日曆日（會超出 win_start）；本支是**把既有窗逐日切**，Σ 日窗 ≡ 窗長
       ⇒ 產能分母不會因切日而多算或少算。win_end ≤ win_start → []。"""
    s, e = _as_dt(win_start), _as_dt(win_end)
    if not s or not e or e <= s:
        return []
    out = []
    cur = s
    while cur < e:
        nxt = _dt.datetime(cur.year, cur.month, cur.day) + _dt.timedelta(days=1)
        seg_end = nxt if nxt < e else e
        out.append((cur, seg_end))
        cur = seg_end
    return out


# ---------------------------------------------------------------------------
# 4) 產能分母（SPEC §3.1）：在役工位數 × 窗長，**逐日 as-of**（C-D4）
# ---------------------------------------------------------------------------

def active_days_count(eid, day_list, events=None):
    """該 EID 在 day_list 裡有幾天是「在役（非長停）」。day_list＝date/datetime/字串皆可。"""
    return sum(1 for d in (day_list or []) if not is_dormant(eid, d, events))


def capacity_split(child_eids, day_wins, events=None):
    """並行工位的**產能分母**（SPEC §3.1）：`產能 = Σ_日 Σ_當日在役工位 該日窗長`。
       child_eids＝該父機配置的全部工位 EID；day_wins＝`split_day_windows` 輸出。

       → {"denom_sec": float,                      # 分母（秒）；逐日 as-of，歷史窗會自己換分母
          "active_by_day": {'YYYY-MM-DD': [eid,…]},# 每天在役的是哪幾台（可稽核、可畫）
          "n_active_end": int,                     # **窗末日**在役數 → 頁面「2/4 工位在役」
          "n_total": int,
          "dormant": [{"eid","since","reason_zh","reason_en"}, …]}  # 窗末日長停者（C-D3 揭露用）

       ★ 為何逐日不是「拿今天的狀態乘窗長」：C-D4——12 個月趨勢要在 2025-10-23/27 **當天**
         換分母，不是拿今天的結論回頭套。此處逐日算 ⇒ 歷史窗自動正確、日後建月趨勢直接重用。
       ★ 空 children（快照無子工位）→ denom_sec 0．0、n_total 0 ⇒ 呼叫端顯「—」，
         **絕不退回舊的父機聯集口徑**（O-1：兩口徑不得並存）。"""
    eids = list(child_eids or [])
    wins = list(day_wins or [])
    denom = 0.0
    by_day = {}
    last_active = []
    for d_s, d_e in wins:
        day = _as_date(d_s)
        sec = (d_e - d_s).total_seconds()
        active = [e for e in eids if not is_dormant(e, day, events)]
        by_day[day.isoformat()] = active
        denom += len(active) * max(0.0, sec)
        last_active = active
    dormant = []
    if wins:
        end_day = _as_date(wins[-1][0])
        for e in eids:
            info = dormant_info(e, end_day, events)
            if info:
                dormant.append({"eid": e, "since": info["since"],
                                "reason_zh": info["reason_zh"], "reason_en": info["reason_en"]})
    return {"denom_sec": denom, "active_by_day": by_day,
            "n_active_end": len(last_active), "n_total": len(eids),
            "dormant": dormant}


def producing_within(records, day_wins, eid, events=None):
    """產能利用率的**分子**：該工位在「它自己在役的那些日子」裡的生產秒數。
       records＝canonical §23 records（`{start,end,state,…}`；datetime 或 ISO 字串皆收）。
       長停日的生產**不計入分子**——理論上長停的機器不會有生產段（真有就是哨兵該叫的
       「在跑了、登記沒解除」，SPEC §1-3），但分子分母的日集合必須是同一組，否則
       利用率會 >100% 而沒有人看得出為什麼。"""
    total = 0.0
    for d_s, d_e in (day_wins or []):
        if is_dormant(eid, _as_date(d_s), events):
            continue
        for r in (records or []):
            if (r or {}).get("state") != PRODUCING_STATE:
                continue
            s, e = _as_dt(r.get("start")), _as_dt(r.get("end"))
            if not s or not e or e <= s:
                continue
            lo = s if s > d_s else d_s
            hi = e if e < d_e else d_e
            if hi > lo:
                total += (hi - lo).total_seconds()
    return total


# ---------------------------------------------------------------------------
# --selftest（純函式、零 I/O、deterministic）
# ---------------------------------------------------------------------------

def _selftest():
    D = _dt.date
    DT = _dt.datetime
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # ── A. 橋常數本身 ──────────────────────────────────────────────────
    # WP-10d（R-P11）：公開樹的橋是合成名冊（EID／生效日／原因都不同）⇒ 具名機台與生效日一律**由橋常數推導**
    #   （M3＝橋內較早停的一筆、M2＝較晚停的一筆；私有樹推出來恰為 26887／26841，A2／A5 仍釘死事實）。
    import core_paths as _wp
    _by_date = sorted(DORMANT_BRIDGE, key=lambda r: r[1])
    E3, E2 = _by_date[0][0], _by_date[-1][0]
    _row2 = _by_date[-1]
    d3, d2 = _as_date(_by_date[0][1]), _as_date(_by_date[-1][1])
    ONE = _dt.timedelta(days=1)
    ck(len(DORMANT_BRIDGE) == 2, "A1 橋只收 Chris 明示的 CAL 兩筆")
    if not _wp.is_public_tree():
        ck({r[0] for r in DORMANT_BRIDGE} == {"EID-26841", "EID-26887"}, "A2 橋的 EID")
        assert E2 == "EID-26841" and E3 == "EID-26887", "A2b 推導的 M2／M3＝真名冊的兩台"   # 不計入斷言數
    ck(all(r[2] == STATE_NOT_SCHEDULED for r in DORMANT_BRIDGE), "A3 橋全為 not_scheduled")
    ck(all(len(r) == 6 and all(r[3:6]) for r in DORMANT_BRIDGE), "A4 橋六欄、原因與出處必填")
    # ★ 生效日＝cmms A11 登記值（NOTE-30），2026-08-10 MES 逐月對帳確認（SPEC §9b）。
    #   **舊值 2025-10-24／2025-10-28（末筆 segment 次日）已作廢**——負斷言釘死不得復活。
    if not _wp.is_public_tree():
        ck(dict((r[0], r[1]) for r in DORMANT_BRIDGE) ==
           {"EID-26841": "2023-05-23", "EID-26887": "2023-05-11"},
           "A5 生效日＝cmms 登記值（末次正常生產日；MES 對帳確認）")
    ck(not any(r[1].startswith("2025-10") for r in DORMANT_BRIDGE),
       "A6 負斷言：作廢的「末筆 segment 次日」推導值（2025-10-*）不得復活")
    # M3 比 M2 早三個月停＝兩個獨立事件（舊值誤判成「相隔 4 天的同一事件」）
    ck(d3 < d2, "A7 M3 先於 M2 停（非同一事件）")

    # ── B. as-of 邊界（SPEC §6.1-1；兩筆 × 前後日）────────────────────
    ck(is_dormant(E2, (d2 - ONE)) is False, "B1 M2 生效日前一日＝在役")
    ck(is_dormant(E2, d2) is True, "B2 M2 生效日當天即長停")
    ck(is_dormant(E3, (d3 - ONE)) is False, "B3 M3 生效日前一日＝在役")
    ck(is_dormant(E3, d3) is True, "B4 M3 生效日當天即長停")
    ck(is_dormant(E2, D(2026, 8, 9)) is True, "B5 長停延續至今（無復機事件）")
    # 型別三形同義（date / datetime / 字串）＝日粒度、不做瞬時比較
    ck(is_dormant(E2, DT(d2.year, d2.month, d2.day)) is True, "B6 生效日 00:00 即長停（非瞬時語意）")
    ck(is_dormant(E2, (DT(d2.year, d2.month, d2.day) - _dt.timedelta(minutes=1))) is False, "B7 前一日日終仍在役")
    ck(is_dormant(E2, d2.isoformat()) is True, "B8 字串日期同義")
    ck(is_dormant(E2, d2.isoformat() + "T18:30:00") is True, "B9 ISO datetime 字串取日")
    # ★ 作廢舊值的行為釘：2025-10-23（舊 M2 生效日前一日）現在**已經是長停**
    #   ——證明換值真的往前推了兩年多，不是只改了字串。
    ck(is_dormant(E2, D(2025, 10, 23)) is True,
       "B10 舊生效日前一日現已長停（換值生效、非僅改字面）")
    ck(is_dormant(E3, D(2025, 10, 27)) is True, "B11 同上（M3）")

    # ── C. dormant_info ───────────────────────────────────────────────
    inf = dormant_info(E2, D(2026, 8, 9))
    ck(inf == {"since": d2.isoformat(), "reason_zh": _row2[3],
               "reason_en": _row2[4]}, "C1 dormant_info 內容")
    ck(dormant_info(E2, (d2 - ONE)) is None, "C2 非長停日 → None")
    ck(dormant_info("EID-99999", D(2026, 8, 9)) is None, "C3 未知 EID → None")
    # ⚠ 順序＝M3 先停（05-11）、M2 後停（05-23）
    ck(dormant_set((d3 - ONE)) == frozenset(), "C4 05-10 兩台全在役")
    ck(dormant_set(d3) == frozenset({E3}), "C5 05-11 只有 M3")
    ck(dormant_set(d2) == frozenset({E2, E3}), "C6 05-23 兩台")

    # ── D. 復機事件（合成第三筆 in_production）＝翻回 False ─────────────
    EV = list(DORMANT_BRIDGE) + [
        (E2, "2026-03-02", STATE_IN_PRODUCTION, None, None, "selftest"),
    ]
    ck(is_dormant(E2, D(2026, 3, 1), EV) is True, "D1 復機前一日仍長停")
    ck(is_dormant(E2, D(2026, 3, 2), EV) is False, "D2 復機當日即在役")
    ck(dormant_info(E2, D(2026, 3, 2), EV) is None, "D3 復機後無 info")
    ck(is_dormant(E3, D(2026, 3, 2), EV) is True, "D4 復機不外溢到別台")
    # 再度長停 → since 是新那一段的起點、不是最早那次
    EV2 = EV + [(E2, "2026-05-10", STATE_NOT_SCHEDULED, "二次停", "second stop", "selftest")]
    ck(dormant_info(E2, D(2026, 6, 1), EV2)["since"] == "2026-05-10", "D5 二次長停 since")
    # 連續同態（更正/補登記）→ since 取該段起點、reason 取 effective
    EV3 = list(DORMANT_BRIDGE) + [
        (E2, "2025-12-01", STATE_NOT_SCHEDULED, "改原因", "reason fixed", "selftest")]
    i3 = dormant_info(E2, D(2026, 1, 1), EV3)
    ck(i3["since"] == d2.isoformat() and i3["reason_zh"] == "改原因", "D6 連續同態：since 段起點、reason 取最後")
    # 同日兩筆 → 後到者定調（SPEC §6.1-1）
    EV4 = list(DORMANT_BRIDGE) + [
        (E2, "2026-04-01", STATE_IN_PRODUCTION, None, None, "selftest"),
        (E2, "2026-04-01", STATE_NOT_SCHEDULED, "又停", "stopped again", "selftest")]
    ck(is_dormant(E2, D(2026, 4, 1), EV4) is True, "D7 同日兩筆＝後到者定調")

    # ── E. C-D5／§4 存證釘：橋外的 EID 永不 dormant ───────────────────
    #    CLN-2 的 SF M1/M3（MES 標 Out Of Service）與 Manifold Wash Jig 刻意不進橋（SPEC §4）
    #    ⇒ 它們**不影響任何分母、也不會被誤標長停**；等 A11 登記。
    for e in ("EID-27639", "EID-27706", "EID-09670", "EID-24742", "EID-24790", "EID-26820",
              "EID-16077", "EID-27600", "EID-00884"):
        ck(is_dormant(e, D(2026, 8, 9)) is False, "E 橋外 EID 永不 dormant: %s" % e)
    ck(len(dormant_set(D(2026, 8, 9))) == 2, "E9 今日長停恰兩台（不多不少）")

    # ── F. split_day_windows ─────────────────────────────────────────
    w = split_day_windows(DT(2025, 10, 20, 6, 0), DT(2025, 10, 23, 18, 0))
    ck(len(w) == 4, "F1 首尾部分日 → 4 段")
    ck(w[0] == (DT(2025, 10, 20, 6, 0), DT(2025, 10, 21)), "F2 首段自 win_start")
    ck(w[-1] == (DT(2025, 10, 23), DT(2025, 10, 23, 18, 0)), "F3 末段止於 win_end")
    ck(abs(sum((b - a).total_seconds() for a, b in w)
           - (DT(2025, 10, 23, 18, 0) - DT(2025, 10, 20, 6, 0)).total_seconds()) < 1e-6,
       "F4 Σ 日窗 ≡ 窗長（切日不多算不少算）")
    ck(split_day_windows(DT(2025, 1, 2), DT(2025, 1, 1)) == [], "F5 逆序窗 → []")
    ck(split_day_windows(None, DT(2025, 1, 1)) == [], "F6 None → []")
    w1 = split_day_windows(DT(2025, 10, 20), DT(2025, 10, 21))
    ck(w1 == [(DT(2025, 10, 20), DT(2025, 10, 21))], "F7 整日單段")

    # ── G. capacity_split：歷史窗跨生效日（手算精確值）────────────────
    #    窗＝2025-10-20 00:00 → 2025-11-01 00:00（12 個完整日）、四工位。
    #    10-20..10-23（4 天）：4 台在役 ／ 10-24..10-27（4 天）：3 台 ／ 10-28..10-31（4 天）：2 台
    #    ★ 本段用**合成事件**（`EV_CAP`）而非橋常數——測的是「逐日 as-of 分母」這個**結構**，
    #      不是某兩台機器的真實生效日（那在 A5/B/C 段測）。分離之後，A11 換源（橋退役）
    #      或生效日被 supersede 更正，都不會讓這組手算釘失效。
    #      **2026-08-10 生效日換值時就是靠這個分離，讓結構釘零改動、只改事實釘。**
    KIDS = ["EID-24790", E2, E3, "EID-26820"]
    EV_CAP = [(E2, "2025-10-24", STATE_NOT_SCHEDULED, "合成", "synthetic", "selftest"),
              (E3, "2025-10-28", STATE_NOT_SCHEDULED, "合成", "synthetic", "selftest")]
    dw = split_day_windows(DT(2025, 10, 20), DT(2025, 11, 1))
    ck(len(dw) == 12, "G0 12 個完整日")
    cap = capacity_split(KIDS, dw, EV_CAP)
    DAY = 86400.0
    ck(abs(cap["denom_sec"] - (4 * 4 + 3 * 4 + 2 * 4) * DAY) < 1e-6,
       "G1 逐日 as-of 分母＝(4×4＋3×4＋2×4) 台日")
    ck(cap["denom_sec"] != 4 * 12 * DAY, "G2 分母不等於「四台整窗」（沒有靜默沿用舊口徑）")
    ck(cap["denom_sec"] != 2 * 12 * DAY, "G3 分母不等於「拿今天的狀態回頭套整窗」（C-D4）")
    ck(cap["n_active_end"] == 2 and cap["n_total"] == 4, "G4 窗末 2/4 在役")
    ck(len(cap["active_by_day"]) == 12, "G5 逐日在役表 12 天")
    ck(cap["active_by_day"]["2025-10-23"] == KIDS, "G6 10-23 四台全在役")
    ck(cap["active_by_day"]["2025-10-24"] == ["EID-24790", E3, "EID-26820"], "G7 10-24 剩三台")
    ck(cap["active_by_day"]["2025-10-28"] == ["EID-24790", "EID-26820"], "G8 10-28 剩兩台")
    ck([d["eid"] for d in cap["dormant"]] == [E2, E3], "G9 揭露兩台（C-D3）")
    ck(cap["dormant"][0]["since"] == "2025-10-24" and cap["dormant"][1]["since"] == "2025-10-28",
       "G10 揭露帶生效日")
    # active_days_count 與 capacity_split 互導（同一組合成事件）
    ck(active_days_count(E2, [a for a, _b in dw], EV_CAP) == 4, "G11 M2 在役 4 天")
    ck(active_days_count(E3, [a for a, _b in dw], EV_CAP) == 8, "G12 M3 在役 8 天")
    ck(active_days_count("EID-24790", [a for a, _b in dw], EV_CAP) == 12, "G13 M1 全窗在役")
    ck(abs(sum(active_days_count(e, [a for a, _b in dw], EV_CAP) for e in KIDS) * DAY
           - cap["denom_sec"]) < 1e-6, "G14 Σ 在役台日 × 24h ≡ denom_sec")
    # ★ 同一個窗改用**真橋常數** ⇒ 兩台全窗長停（2023 就停了）⇒ 分母恰 2 台 × 12 天。
    #   這條同時證明：G 段的結構釘與事實釘確實分離、且換值真的生效。
    cap_real = capacity_split(KIDS, dw)
    ck(abs(cap_real["denom_sec"] - 2 * 12 * DAY) < 1e-6,
       "G15 真橋常數下同窗分母＝2×12 台日（2025-10 窗已在長停期內）")
    ck(cap_real["n_active_end"] == 2 and len(cap_real["dormant"]) == 2, "G16 真橋常數：窗末 2/4")

    # ── H. capacity_split 三個退化形 ───────────────────────────────────
    dw2 = split_day_windows(DT(2026, 8, 1), DT(2026, 8, 8))
    allc = capacity_split([E2, E3], dw2)
    ck(allc["denom_sec"] == 0.0 and allc["n_active_end"] == 0 and allc["n_total"] == 2,
       "H1 全長停 → 分母 0（呼叫端顯「—」，不得除以 0）")
    ck(len(allc["dormant"]) == 2, "H2 全長停 → 兩筆揭露")
    act = capacity_split(["EID-24790", "EID-26820"], dw2)
    ck(abs(act["denom_sec"] - 2 * 7 * DAY) < 1e-6 and act["dormant"] == [],
       "H3 全在役 → 分母＝台數×窗長、零揭露")
    emp = capacity_split([], dw2)
    ck(emp["denom_sec"] == 0.0 and emp["n_total"] == 0 and emp["n_active_end"] == 0
       and emp["dormant"] == [], "H4 空 children → 全零（呼叫端顯「—」不退回舊口徑）")
    ck(capacity_split(KIDS, [])["denom_sec"] == 0.0, "H5 空 day_wins → 分母 0")
    ck(capacity_split(KIDS, [])["dormant"] == [], "H6 空 day_wins → 無窗末日可判 → 零揭露")

    # ── I. producing_within ──────────────────────────────────────────
    recs_ok = [{"start": DT(2025, 10, 20, 8), "end": DT(2025, 10, 20, 12), "state": PRODUCING_STATE},
               {"start": DT(2025, 10, 21, 0), "end": DT(2025, 10, 21, 6), "state": PRODUCING_STATE},
               {"start": DT(2025, 10, 21, 6), "end": DT(2025, 10, 21, 9), "state": "維修"}]
    dw3 = split_day_windows(DT(2025, 10, 20), DT(2025, 10, 22))
    #    ★ 與 G 段同理：涉及生效日的兩案（I2/I4）走**合成事件 `EV_CAP`**＝測結構不測事實；
    #      真橋常數的事實由 I2b 釘（2025-10 對真常數而言早已在長停期內）。
    ck(producing_within(recs_ok, dw3, "EID-24790") == (4 + 6) * 3600.0, "I1 只算生產態、跨日各自歸日")
    ck(producing_within(recs_ok, dw3, E2, EV_CAP) == (4 + 6) * 3600.0,
       "I2 生效日前的長停機台照算（合成事件）")
    ck(producing_within(recs_ok, dw3, E2) == 0.0,
       "I2b 真橋常數：2025-10 已在長停期內 ⇒ 該工位生產不進分子（換值行為釘）")
    # 生效日之後的生產不進分子（分子分母日集合一致）
    recs_late = [{"start": DT(2025, 10, 25, 1), "end": DT(2025, 10, 25, 5), "state": PRODUCING_STATE}]
    dw4 = split_day_windows(DT(2025, 10, 25), DT(2025, 10, 26))
    ck(producing_within(recs_late, dw4, "EID-24790") == 4 * 3600.0, "I3 在役機台照算")
    ck(producing_within(recs_late, dw4, E2, EV_CAP) == 0.0,
       "I4 長停日的生產不進分子（合成事件）")
    # 窗外段、零/負長段、字串時戳
    ck(producing_within([{"start": DT(2025, 9, 1), "end": DT(2025, 9, 2), "state": PRODUCING_STATE}],
                        dw4, "EID-24790") == 0.0, "I5 窗外段不計")
    ck(producing_within([{"start": DT(2025, 10, 25, 3), "end": DT(2025, 10, 25, 3),
                          "state": PRODUCING_STATE}], dw4, "EID-24790") == 0.0, "I6 零長段")
    ck(producing_within([{"start": "2025-10-25T01:00:00", "end": "2025-10-25T05:00:00",
                          "state": PRODUCING_STATE}], dw4, "EID-24790") == 4 * 3600.0,
       "I7 ISO 字串時戳（snapshot 形）")
    ck(producing_within([], dw4, "EID-24790") == 0.0, "I8 空 records")
    ck(producing_within(None, dw4, "EID-24790") == 0.0, "I9 None records")
    # 跨窗界段只算窗內那部分
    ck(producing_within([{"start": DT(2025, 10, 24, 22), "end": DT(2025, 10, 25, 2),
                          "state": PRODUCING_STATE}], dw4, "EID-24790") == 2 * 3600.0,
       "I10 跨窗界只計窗內")

    # ── J. 結構釘：投影函式**不得**在簽名裡帶「now」預設（C-D6 現算鐵則）──
    #    這條擋的是最容易犯的退化：有人為了「呼叫方便」給 day 一個 datetime.now() 預設值，
    #    從此歷史窗全部被今天的狀態污染，而且沒有任何測試會紅。
    for fn in (is_dormant, dormant_info, dormant_set, active_days_count,
               capacity_split, producing_within, split_day_windows, events_for):
        sig = inspect.signature(fn)
        for pname, p in sig.parameters.items():
            ck(not isinstance(p.default, (_dt.date, _dt.datetime)),
               "J %s(%s) 不得有日期預設值" % (fn.__name__, pname))
        ck("now" not in sig.parameters, "J %s 簽名不得有 now 參數" % fn.__name__)
    src = inspect.getsource(sys.modules[__name__])
    body = src.split("def _selftest")[0]
    ck("datetime.now" not in body and ".today()" not in body and "utcnow" not in body,
       "J9 模組本體零 now/today/utcnow（投影永遠吃參數）")

    # ── K. 型別強制 ──────────────────────────────────────────────────
    for bad in (None, 123, "", "20251024"):
        try:
            _as_date(bad)
            ck(False, "K 壞日期應 raise: %r" % (bad,))
        except (ValueError, TypeError):
            ck(True, "K 壞日期 raise: %r" % (bad,))
    ck(_as_date("2025-10-24T18:00:00") == D(2025, 10, 24), "K5 ISO datetime 字串取日")

    # ── L. events_for ────────────────────────────────────────────────
    ck(events_for("EID-99999") == [], "L1 未知 EID → []（＝全程排產中、不是查無資料）")
    ev = events_for(E2)
    ck(len(ev) == 1 and ev[0]["ts_effective"] == d2, "L2 單筆")
    unsorted = [("EID-X", "2026-02-01", STATE_IN_PRODUCTION, None, None, "t"),
                ("EID-X", "2025-01-01", STATE_NOT_SCHEDULED, "早", "early", "t")]
    ck([e["ts_effective"] for e in events_for("EID-X", unsorted)]
       == [D(2025, 1, 1), D(2026, 2, 1)], "L3 輸入亂序 → 輸出升冪")
    # dict 形（A11 換源時 cmms 回的就是 dict）與 tuple 形同義
    as_dicts = [{"eid": E2, "ts_effective": "2025-10-24", "state": STATE_NOT_SCHEDULED,
                 "reason_zh": "線外查修", "reason_en": "off-line investigation", "source": "cmms"}]
    ck(is_dormant(E2, D(2026, 1, 1), as_dicts) is True, "L4 dict 形事件同義（換源零改動）")
    ck(dormant_info(E2, D(2026, 1, 1), as_dicts)["since"] == "2025-10-24", "L5 dict 形 info")
    ck(is_dormant(E2, D(2026, 1, 1), []) is False, "L6 空事件流 → 全在役")
    lane_only = [{"eid": "EID-26596", "sub_unit": "nest:2", "ts_effective": "2026-01-06",
                  "state": STATE_NOT_SCHEDULED}]
    ck(events_for("EID-26596", lane_only) == []
       and is_dormant(E2, D(2026, 1, 1), []) is False
       and events_for(E2, []) == [],
       "L7 結構防呆／不雙算：sub_unit lane 不進整機謂詞，空 live 流不合併 DORMANT_BRIDGE")

    # ── M. §11.6：cmms 長停登記不在設備板名冊的例外 chip 材料 ------------------
    outsider = [{"eid": "EID-99999", "ts_effective": "2026-01-06",
                 "state": STATE_NOT_SCHEDULED, "source": "cmms"},
                {"eid": E2, "ts_effective": "2026-01-06",
                 "state": STATE_NOT_SCHEDULED, "source": "cmms"}]
    ck(unlisted_long_stop_registrations(D(2026, 2, 1), outsider, {E2})
       == [{"eid": "EID-99999", "since": "2026-01-06"}],
       "M1 cmms 長停外來 EID ⇒ 一筆揭露，帶 EID＋登記日（不改板名冊）")
    ck(unlisted_long_stop_registrations(D(2026, 2, 1), outsider,
                                         {E2, "EID-99999"}) == []
       and unlisted_long_stop_registrations(D(2026, 2, 1), None, {E2}) == [],
       "M2 外來數 N=0／cmms 未接線 ⇒ 空列（模板不得常駐 chip，也不把 bridge 當 cmms）")

    # ── N. 合成產線 WP-4b R-C3：橋常數的唯一讀口；合成進程＝空（不 import core_source，只看已載入的旗標）──
    import types as _types
    _saved = sys.modules.get("core_source")
    try:
        sys.modules.pop("core_source", None)
        ck(bridge_rows() is DORMANT_BRIDGE and _rows(None) is DORMANT_BRIDGE,
           "N1 core_source 未載入 ⇒ 橋常數本體（獨立跑本檔＝行為同改動前）")
        for flag, want_empty in ((False, False), (True, True)):
            fake = _types.ModuleType("core_source")
            fake.is_synth_process = (lambda f=flag: f)
            sys.modules["core_source"] = fake
            got = bridge_rows()
            ck((got == [] and _rows(None) == [] and dormant_set(D(2026, 1, 1)) == frozenset()
                and dormant_info(E2, D(2026, 1, 1)) is None) if want_empty
               else (got is DORMANT_BRIDGE and E2 in dormant_set(D(2026, 1, 1))),
               "N2 進程旗標=%s ⇒ %s" % (flag, "空（合成不讀橋）" if want_empty else "橋常數本體"))
        boom = _types.ModuleType("core_source")
        boom.is_synth_process = lambda: 1 / 0
        sys.modules["core_source"] = boom
        ck(bridge_rows() is DORMANT_BRIDGE, "N3 旗標讀取例外 ⇒ 當非合成（不炸投影）")
        fake = _types.ModuleType("core_source")
        fake.is_synth_process = lambda: True
        sys.modules["core_source"] = fake
        ev = [{"eid": E2, "ts_effective": "2025-10-24", "state": STATE_NOT_SCHEDULED}]
        ck(is_dormant(E2, D(2026, 1, 1), ev) is True,
           "N4 合成進程：傳進來的事件流照常投影（只擋 events=None 的退橋）")
    finally:
        if _saved is None:
            sys.modules.pop("core_source", None)
        else:
            sys.modules["core_source"] = _saved

    print("board_dormant selftest: %d checks PASS" % n)
    return 0


def main():
    ap = argparse.ArgumentParser(description="dormant（長停/復機）as-of 投影")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="印出該台北日曆日的長停清單（YYYY-MM-DD）")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if a.as_of:
        for e in sorted(dormant_set(a.as_of)):
            info = dormant_info(e, a.as_of)
            print("%s  since %s  %s" % (e, info["since"], info["reason_zh"]))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
