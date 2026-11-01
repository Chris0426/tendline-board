# -*- coding: utf-8 -*-
"""
board_w8.py — W8 停機融合契約「消費端 stub」（cmms → Tendline Board）
=================================================================

定位（GOVERNANCE §3.2.2 consumer-stub-first **首例**；風險帳本 §3.3「W8 高風險」的首要動作）：
  cmms WO 讀取 API 已建**未上線**、Tendline Board 消費端原本零碼。本檔把 W8 契約（CONCEPT §19.3
  A8/W8，2026-06-29 雙邊 CLOSED）的**純語意**先寫成可測純函式——fixture 驅動、零 I/O、
  deterministic。cmms go-live 那天只換傳輸層（HTTP/MCP 讀 list/get/status-history/rollup），
  本檔函式一行不改。

契約摘要（唯一真相＝CONCEPT §19.3「W8 停機融合契約」段）：
  - cmms 只吐 raw WO 事實、**不主張停機**；Tendline Board 把 WO 活躍窗疊到 MES 偵測的停機窗上分類。
  - work_type → §23 態：PM→計畫保養、REACTIVE→維修、CONVERSION→調機；
    PART REQUEST 及其餘長尾（ON HALT/DELAY/CORRECTIVE/PROACTIVE）與未知值→
    「其他維護(子類待定)」暫桶（＝契約原文字串；is_canonical=False、待 cmms W4 重分類 push）。
    work_type 只貼標、不主張停機長度。
  - status 7 態：OPEN/IN_PROGRESS/ON_HOLD/COMPLETED 非終態；CLOSED/CANCELLED/VOIDED 終態。
    WO 活躍窗＝open → 首個 COMPLETED（無 COMPLETED→首個終態；皆無→ongoing）。
  - **status_history 條目真形狀（2026-07-04 峰會 CM-1 當場關；cmms schemas.py:42-49
    `WorkOrderStatusHistoryRead`，經 `/work-orders/{no}/detail`）**＝
    `{from_status, to_status, hold_reason, changed_at, source_actor}` →
    本檔邊界對映：`to_status`→status、`changed_at`→at、`hold_reason` 保留
    （governed 詞彙 canonical 5 值，cmms 2026-07-07 對版確認：WAITING_MACHINE_TIME=等機台
    空檔·機台仍生產／TEST_RUN=試跑／WAITING_PARTS=等料／WAITING_VENDOR=等外包／OTHER；
    **未知值＝「不知道」保留條目、不丟整筆**——additive 契約面，cmms /admin/vocab 可增值）；
    `from_status` 不需要、**`source_actor` 治理丟棄**（可含 human:<id>＝per-person，§24 D1/D2）。
    ON_HOLD＋hold_reason 的窗語意（S12）＝**已落地 board_eqmeeting 分階段口徑**
    （2026-07-07 Chris 裁決）：不計停機集歸 eqmeeting.UPTIME_HOLD_REASONS、與本檔顯示集脫鉤。
  - ★歷史 caveat：大量遷移 WO 無 status_history 中間轉移 →
    status_history 空 → 回退 opened_at→closed_at。
  - 鍵：work_order_no(BigInt)、asset_id = EID（P-1 同名同物、直 join MES）。
  - TODO（本 stub 不實作）：**站級 rollup 不在此**——站級走 cmms `rollup_asset_work_orders`
    API（self + contains_module 後代、shared 不捲＝避 U5 共用治具雷）；本檔一律單 EID 層級，
    caller 負責先按 EID 分好 gaps/wos。
  - TODO（傳輸層接通時，QG-3）：schema_id 驗證（W8 契約**尚未定 schema id 字串**，接通前
    須與 cmms 對版）、timeout + 失敗呈現（該卡 unavailable + coverage 誠實）、golden fixture
    落 `_fixtures/`。

§14.2 時間對齊 v0 聲明（誠實聲明，不做重錨魔法）：
  - **WO 時戳＝cmms 即時時鐘**（cmms 自建紀錄、A6 freshness=即時）。
  - **MES 停機窗＝MES server time**（全系統時間錨、C5），但經 ETL 推送 read-DB
    **~85 分鐘落後、~每小時批次**（§21.1 實測）——即本檔 overlap 的兩側時鐘來源不同、
    新鮮度不同。v0 立場：**不重錨**（兩者皆 server-side 時鐘、非 XP 邊緣時戳），只做
    髒時戳防護（guard_ts：1900-01-01 sentinel、未來日期，照 §21.1「timestamp <= GETDATE()」
    紀律）。ETL 落後意味最近 ~85min 的 MES gap 可能還沒現形——那是上游 mes_timeline 的
    觀測窗問題，非本檔職責。
  - **時區＝S10 CLOSED（2026-07-05 A3 對版，§19.7）**：cmms 吐 **UTC ISO-8601 帶 'Z'**
    （prod 實證；LIST/detail 同一條 pydantic 序列化路徑；微秒有則忠實保留、整秒無小數位），
    MES 窗＝台北牆鐘 naive。本檔＝**§14.2 對齊層**：tz-aware 時戳（datetime.tzinfo 非 None、
    或字串帶 'Z'/±HH:MM offset）一律 **轉台北牆鐘（UTC+8）後去 tz**，之後才與 MES 窗 overlap
    ——絕不靜默截斷 offset（那會錯位 8h）。naive＝視為台北牆鐘、原樣。
    （原「大聲拒收」立場＝對版前的 loud-fail 防線，2026-07-05 fixture-first 讀真 golden
    抓到整筆丟棄＝防線奏效；對版後改為轉換。）

治理（§24 D1/D2 硬規則）：任何 per-person 欄位（assignee/technician/confirmed_by/
  operator…）在 normalize 邊界**白名單丟棄**——不傳遞、不儲存、不進分類輸出。
  歸因單位＝機台/視窗，不是人。

用法：
  python board_w8.py --selftest     # 離線自驗（inline fixture＋若在場則對 _fixtures/
                                      #   w8.workorder.json golden 跑 QG-3.4；不碰網路/DB）
  （cmms API 未上線 → 本檔現階段沒有別的入口；go-live 後傳輸層另加。）

樣板：consume 紀律照 remote-diag `adapters/board_labels.py`（白名單+enum 健檢+壞資料
  丟棄不炸+selftest），不抄其內容。純 Python 標準庫、零新依賴。
"""
import sys
import json
import os.path
import argparse
import datetime as _dt

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 契約常數（來源＝CONCEPT §19.3 W8 段 + §23 canonical 10 態）
# ---------------------------------------------------------------------------

# §23 canonical 10 態（顯示層才翻譯；此處 key 不翻）
CANONICAL_STATES = ("生產", "計畫保養", "非排定", "維修", "故障停機",
                    "調機", "該跑沒跑", "待機", "流程等待", "不明")

# W8 暫桶（**非** §23 canonical 態；待 cmms W4 重分類 push 後才落 canonical）
# 字串＝CONCEPT §19.3 契約原文（F6 lead 裁決 2026-07-04：契約字串勝於任務指示字串，
# 與 _fixtures/w8.workorder.json `_work_type_mapping` 對齊）。
BUCKET_STATE = "其他維護(子類待定)"

CONFIDENCE = ("high", "medium", "low")   # C3 正面 only enum

# work_type → (state, confidence, is_canonical)
#   PM/REACTIVE：§23 表列 計畫保養/維修 信心=high（判定源=cmms 工單）。
#   CONVERSION→調機：契約定對映；confidence **契約未定**——§23 表的「低（有良率脈絡時中）」
#     是針對『良率 proxy』判定源；這裡是 cmms 正面工單紀錄（強於 proxy）、但又未像
#     PM/維修 被 §23 表列為 high → 保守取 medium（契約沉默點，見檔尾清單）。
#   其餘（PART REQUEST/ON HALT/DELAY/CORRECTIVE/PROACTIVE/未知）→暫桶：
#     子類未定、待 W4 → 誠實 low、is_canonical=False（不硬猜、不假裝 canonical）。
# ★ 2026-08-16 cmms 第八輪三（結構性答案，取代「live 30 天窗零 legacy 格」那個窗長證據）：
#   8 碼原樣沿用 eMaint、**W4 重分類至今未動**（匯入分佈：REACTIVE 與 PM 佔絕大多數，
#   其餘 ON HALT／PART REQUEST／CONVERSION／PROACTIVE／CORRECTIVE／DELAY 為長尾）。
#   開單通道逐條：網頁報修／助理 MCP／on-box **三條寫死 REACTIVE**、PM 生成器寫死 PM；
#   只有 `propose_open_work_order`（需 admin confirm）與 admin CLI 是自由參數、**FK 限那 8 碼**。
#   ⇒ ① **四條現場/自動化通道結構上只可能產出 REACTIVE/PM**；長尾碼要出現必須 admin 刻意為之。
#     ② **cmms 明確不保證永不出現**（W4 重分類仍是待辦，真動＝形狀變更、他們會 push）。
#     ③ 未知值走暫桶這條 fail-open **正是他們建議的紀律**（同 `entry_type`：**不得白名單拒未知值**）
#        ⇒ 本 map 的 fallback 路徑**永遠留著**，即使它今天結構上打不到。
#   ⚠ 顯示側另有一條：`調機`／暫桶在全站一律**灰色具名**（app.STATE_COLORS legacy 灰），
#     **不給獨立色**——那是防呆網，不是分類（2026-08-16 Chris 裁決）。
_WORK_TYPE_MAP = {
    "PM":         ("計畫保養", "high",   True),
    "REACTIVE":   ("維修",     "high",   True),
    "CONVERSION": ("調機",     "medium", True),
}

# status 7 態（契約原文）
STATUSES = ("OPEN", "IN_PROGRESS", "ON_HOLD", "COMPLETED",
            "CLOSED", "CANCELLED", "VOIDED")
TERMINAL_STATUSES = ("CLOSED", "CANCELLED", "VOIDED")

# hold_reason governed 詞彙（cmms 0019 起；CM-1 ②）——「認得的值」集＝**顯示保留用**；
# 未知值＝「不知道」保留。canonical 5 值＝cmms 2026-07-07 對版確認（增值 cmms 必 push）。
# ★ 與「不計停機」語意**脫鉤**：不計停機集＝board_eqmeeting.UPTIME_HOLD_REASONS
#   （永遠只有 WAITING_MACHINE_TIME/TEST_RUN；cmms 明證該兩值口徑正確且完整）。
#   本 tuple 只決定 drill 工單卡顯不顯示原字串，**不得**再身兼停機計算職。
HOLD_REASONS_KNOWN = ("WAITING_MACHINE_TIME", "TEST_RUN",
                      "WAITING_PARTS", "WAITING_VENDOR", "OTHER")
HOLD_REASON_UNKNOWN = "不知道"

# 取消／作廢＝**不進可計停機**的終態集（2026-08-11 Chris 拍板①，對齊 PM 板 C-P7）。
# 理由不是「取消一定沒停機」，而是**同一張單在 PM 板不算、設備板算＝內部矛盾**。
# ⚠ 這裡刻意**不含 CLOSED**——結案單的歷史停機是真的、必須留在板上。
CANCEL_STATUSES = ("CANCELLED", "VOIDED")

# 治理白名單：只有這 8 欄會被保留；其餘（尤其 per-person）一律丟棄（§24 D1/D2）。
# confirmed_reason_code（D6，2026-07-07 CLOSED；additive 進 contract_wo_detail.v1、不 bump）＝
#   REACTIVE 工單人工確認的 eqf 單軸故障碼（nullable、null=未確認≠無故障、退役碼不新選）；
#   B3 classifier「人工 confirm 標籤＝長期學習源」（§10.2）的 ground-truth 入口。**非人員欄**。
# status（2026-08-11 加）＝**單值判讀欄**（工單當前狀態）。實測 A3 LIST wire 上本來就有這欄
#   （`asset_id/closed_at/hold_reason/opened_at/status/status_history/work_order_no/work_type`），
#   是 normalize_wo 收到後丟掉的 ⇒ 加它是**純 additive 的欄位透傳**、cmms 零改動（QG-1 疑慮
#   已解除，存證＝`_CODE-REVIEW-FINDINGS.md` 2026-08-11 節）。
#   ⚠ 與 NOTE-32 那顆翻頁坑（S-PM8）無關——那條擋的是「把 status 當**可變過濾軸**去翻頁」；
#     這裡只當單值判讀欄收進來、不參與翻頁。transport 那條擋 status **query 參數**的結構釘
#     繼續留著，兩件事不衝突。
#   `failure_started_at`（fsa）＝2026-08-16 待辦 4 收進（SPEC-LASTPROD-PUSH §11）：cmms
#   報修/維修單上那顆「對齊 MES 停機起點」按鈕寫入的欄（值源＝我方 `board.last_production.v1`
#   推送）。**決定權在現場、我方忠實反映**（CONCEPT §19.3 第十輪 relay 四）。消費規則
#   收口在 `wo_failure_start`（單一 predicate，五條規則）→ `wo_active_window` 起點。
#   `production_resumed_at`（pra）＝2026-08-22 NOTE-60 收進（cmms 實證 push：WO-36103 我方看板
#   多算 21h＝消費缺口非 bug）：cmms 工單上「修正停機終點」按鈕寫入的欄（人工確認閘、值源
#   MES 回執）。語意＝**有值 ⇒ 它就是停機終點、優先於結單時刻**（`min(段終點, pra)` 裁剪、
#   與 cmms 引擎一致）；NULL＝沒有人對齊過 ⇒ 照舊（結單時刻）＝舊單零影響。消費規則收口在
#   `wo_production_resumed`（單一 predicate）→ `wo_active_window` 終點。欄位 2026-08-17 起
#   就在契約（NOTE-53 兩 md5、對版 PASS）——本輪只是開始消費，零契約變更。
_ALLOWED = ("work_order_no", "asset_id", "work_type", "status",
            "status_history", "opened_at", "closed_at", "confirmed_reason_code",
            "failure_started_at", "production_resumed_at")

# §21.1 髒資料 sentinel：MES/舊系統常見 1900-01-01；1901 以前一律視為髒
DIRTY_FLOOR = _dt.datetime(1901, 1, 1)


# ---------------------------------------------------------------------------
# 時間解析 + 髒時戳防護（§14.2 v0 / §21.1 紀律）
# ---------------------------------------------------------------------------

_TZ_TAIPEI = _dt.timezone(_dt.timedelta(hours=8))   # §14.2 對齊層：cmms UTC → 台北牆鐘


def _parse_dt(v):
    """容忍 'YYYY-MM-DDTHH:MM:SS[.ffffff][Z|±HH:MM]' / 'YYYY-MM-DD HH:MM:SS' /
    'YYYY-MM-DD' / datetime（naive 或 aware）。解析不了→ValueError（caller 決定丟欄位或丟整筆）。

    ★ S10 CLOSED（2026-07-05 A3 對版，§19.7）：cmms 時戳＝UTC ISO-8601 帶 'Z'（prod 實證、
    LIST/detail 同一序列化路徑）→ tz-aware（datetime.tzinfo 非 None、或字串帶 'Z'/offset）
    ＝**轉台北牆鐘（UTC+8）後去 tz**（§14.2 對齊層；MES 段＝台北 naive，轉換後才可疊、
    否則錯位 8h）。naive＝台北牆鐘、原樣。微秒一律截斷（窗運算秒級、無語意損失；
    cmms 整秒/微秒兩種序列化都容納——對版 relay 明言要容納兩種）。"""
    if isinstance(v, _dt.datetime):
        if v.tzinfo is not None:
            v = v.astimezone(_TZ_TAIPEI).replace(tzinfo=None)
        return v.replace(microsecond=0)
    s = str(v).strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"     # py<3.11 fromisoformat 不吃 'Z'；正規化為 offset
    try:
        d = _dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        raise ValueError(f"壞時間: {v!r}")
    if d.tzinfo is not None:
        d = d.astimezone(_TZ_TAIPEI).replace(tzinfo=None)
    return d.replace(microsecond=0)


def guard_ts(ts, now):
    """髒時戳 guard（§21.1 紀律、純函式）：
      - 非 datetime → None
      - 1900-01-01 sentinel（及任何 < 1901）→ None（丟棄不猜）
      - 未來日期（> now）→ None（比照 §21.1 `WHERE timestamp <= GETDATE()`）
    now 由 caller 傳入（測試 deterministic；生產傳 datetime.now()）。"""
    if not isinstance(ts, _dt.datetime):
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(_TZ_TAIPEI).replace(tzinfo=None)   # §14.2 防禦層同紀律（S10 CLOSED）
    if ts < DIRTY_FLOOR:
        return None
    if ts > now:
        return None
    return ts


# ---------------------------------------------------------------------------
# 1) normalize_wo — 邊界白名單 + 型別/enum 健檢（壞資料回 None 不炸）
# ---------------------------------------------------------------------------

def normalize_wo(rec):
    """cmms raw WO → normalized dict；不合法回 None（丟棄，不讓壞資料污染分類）。

    白名單＝(work_order_no, asset_id, work_type, status, status_history, opened_at, closed_at)
    → per-person 欄位（assignee/technician/confirmed_by…）**自動消失**（§24 D1/D2）。

    規則（含契約沉默點的自選規則，見檔尾清單）：
      - work_order_no：int 必要（容忍純數字 str；bool 拒收）。
      - asset_id：非空 str 必要（= EID，P-1 同名同物）。
      - work_type：str→strip().upper()；None 保留 None（未知值由 wo_state 落暫桶）。
      - status：str→strip().upper()（空→None）；非 str／缺席→None；**絕不因此欄拒收整筆**。
        **未知值照存、不驗 enum**——這是單值判讀欄，provider 若新增／改名狀態值，
        擋在白名單外會讓判定（`wo_is_cancelled`）**靜默失效**；照存則至少 history 那條腿
        還在，而未知值本來就不會命中 CANCEL_STATUSES＝保守側。
      - status_history：**條目形狀＝cmms 真形狀（CM-1 峰會關閉）** → 邊界對映
        `to_status`→status、`changed_at`→at、`hold_reason` 保留（未知值＝「不知道」，
        不丟整筆）、`source_actor` 治理丟棄（per-person）、`from_status` 略；
        非法條目（缺 to_status/壞時間/未知 status）逐條丟棄；status_history 本身非 list
        （型別損毀）→ 整筆拒收（None）——不能默默當成「空 history」啟動遷移回退、
        產生似是而非的窗。**非空 history 清洗後變空＝同樣整筆拒收（F1，峰會追認）**：
        遷移回退是給「本來就空」的大量舊單，不是給 enum drift 的通用降級（否則
        provider 新增 status 值時會誤造幻影 ongoing 窗）。條目按 at 排序。
      - opened_at/closed_at：解析失敗→該欄 None（有 history/另一欄可回退，不整筆丟）。
    """
    if not isinstance(rec, dict):
        return None
    out = {k: rec.get(k) for k in _ALLOWED}   # 只取白名單 → per-person 自動消失

    # work_order_no（BigInt PK）
    wno = out.get("work_order_no")
    if isinstance(wno, bool):
        return None
    if isinstance(wno, str) and wno.strip().isdigit():
        wno = int(wno.strip())
    if not isinstance(wno, int):
        return None
    out["work_order_no"] = wno

    # asset_id = EID. Seat-ID parsing is deliberately lazy: capacity imports
    # scope, which imports this module, so a module-level import would create a
    # circular import during startup.
    aid = out.get("asset_id")
    if not isinstance(aid, str) or not aid.strip():
        return None
    seat_id = aid.strip()
    from board_capacity import fold_seat_id
    parent_eid, seat_unit, seat_known = fold_seat_id(seat_id)
    out["asset_id"] = parent_eid
    # Derived local fields, not wire fields: `_ALLOWED` intentionally remains
    # the cmms contract whitelist. Plain EIDs preserve every old key/value.
    out["seat_id"] = seat_id if seat_unit is not None else None
    out["seat_unit"] = seat_unit
    out["seat_known"] = seat_known

    # work_type
    wt = out.get("work_type")
    out["work_type"] = None if wt is None else str(wt).strip().upper()

    # status（單值判讀欄；2026-08-11）——str→strip().upper()（空→None）、非 str→None、
    # **絕不因此欄拒收整筆**（見 docstring：enum drift 不得讓終態判定靜默失效 ⇒ 不驗 enum）。
    st_cur = out.get("status")
    if isinstance(st_cur, str):
        st_cur = st_cur.strip().upper()
        out["status"] = st_cur or None
    else:
        out["status"] = None

    # confirmed_reason_code（D6；nullable eqf 單軸碼）——str→strip（空→None）、非 str→None、
    # **絕不因此欄拒收整筆**（sparse 誠實選填；null=未確認≠無故障，消費端不得入「無故障」統計）。
    crc = out.get("confirmed_reason_code")
    if isinstance(crc, str):
        crc = crc.strip()
        out["confirmed_reason_code"] = crc or None
    else:
        out["confirmed_reason_code"] = None   # None / 非 str（型別髒）一律 None

    # status_history
    hist = out.get("status_history")
    if hist is None:
        hist = []
    if not isinstance(hist, list):
        return None   # 型別損毀 ≠ 空 history（空=遷移 caveat 回退；損毀=拒收）
    clean = []
    for e in hist:
        if not isinstance(e, dict):
            continue
        st = e.get("to_status")            # CM-1 欄名對映：to_status → status
        if not isinstance(st, str):
            continue
        st = st.strip().upper()
        if st not in STATUSES:
            continue   # 未知 status 值 → 丟條目（白名單紀律）
        try:
            at = _parse_dt(e.get("changed_at"))   # CM-1 欄名對映：changed_at → at
        except (ValueError, TypeError):
            continue
        hr = e.get("hold_reason")          # governed 詞彙；未知＝「不知道」保留（CM-1 ②）
        if hr is not None:
            hr = str(hr).strip().upper()
            hr = hr if hr in HOLD_REASONS_KNOWN else HOLD_REASON_UNKNOWN
        # from_status 不需要；source_actor 可含 human:<id> → 治理丟棄（§24 D1/D2）
        clean.append({"status": st, "at": at, "hold_reason": hr})
    if hist and not clean:
        return None   # F1：非空 history 清洗後掏空 → 拒收（≠遷移空 history，不得誤觸回退）
    clean.sort(key=lambda e: e["at"])
    out["status_history"] = clean

    # opened_at / closed_at / failure_started_at / production_resumed_at（壞值→None、不整筆丟）
    for k in ("opened_at", "closed_at", "failure_started_at", "production_resumed_at"):
        v = out.get(k)
        if v is None:
            continue
        try:
            out[k] = _parse_dt(v)
        except (ValueError, TypeError):
            out[k] = None

    return out


# ---------------------------------------------------------------------------
# 2) wo_state — work_type → (§23 態或暫桶, confidence, is_canonical)
# ---------------------------------------------------------------------------

def wo_state(work_type):
    """work_type → (state, confidence, is_canonical)。照 W8 契約表：
      PM→計畫保養(high)、REACTIVE→維修(high)、CONVERSION→調機(medium, 保守自選)、
      其餘長尾與未知→「其他維護(待W4)」暫桶(low, is_canonical=False)。
    貼標 only——不主張停機長度（稼動分母＝SALTMARSH 排班、非 WO）。"""
    wt = ("" if work_type is None else str(work_type)).strip().upper()
    if wt in _WORK_TYPE_MAP:
        return _WORK_TYPE_MAP[wt]
    return (BUCKET_STATE, "low", False)


def wo_is_cancelled(rec):
    """normalized WO → True＝這張單是**取消／作廢**（CANCELLED/VOIDED）。純函式、零 I/O。

    判準（兩條腿，任一命中即 True）：
      ① `status` 欄 ∈ `CANCEL_STATUSES`（大小寫容忍；normalize_wo 已 upper，這裡再防手工 dict）。
      ② `status_history` **末筆** status ∈ `CANCEL_STATUSES`（末筆說了算——前面出現過的
         IN_PROGRESS／ON_HOLD 都不改變「這張單最後被取消」這個事實）。

    ⚠ **絕不讀 `closed_at`**（規則不變，**但支撐它的理由 2026-08-16 換過一次**）：
      · 舊理由（**已失效、勿再引用**）：「prod 實測 14 張 CANCELLED/VOIDED 的 `closed_at`
        恆 NULL」——cmms 2026-08-16 把「取消報修／作廢」收斂成一顆**任何狀態皆可取消、
        且可還原**的「取消工單」⇒ **從 CLOSED 取消的單會保留原 `closed_at`**
        （取消是軟刪、結案時刻是歷史事實；且大量 eMaint 匯入單沒有 history，
        清掉就永久銷毀）。⇒ **CANCELLED 的 `closed_at` 從此可能非 NULL。**
      · 現行理由（**更強**）：`closed_at` 與「這張單是不是被取消」**根本無關**——
        它現在兩種值都可能出現在 CANCELLED 上 ⇒ 拿它判終態不只是漏判，是**沒有訊號**。
      ★ 這是「已定案的結論不要再補論據」的實例：**結論從頭到尾是對的，補上去的
        那條觀察欠了一次收回。** 存證＝`CONCEPT.md` §19.3 2026-08-16 cmms 第六輪。

    ★ 兩條腿都要的理由（不是冗餘）：
      · 只讀 history 末筆＝**今天安全但只是資料巧合**——eMaint 匯出若載進「已取消」的舊單，
        其 `status_history` 是**空的**（那批遷移單全空），規則會**靜默漏掉整批**。
      · 只讀 status 欄＝萬一 provider 某天不填該欄，history 那腿還在。

    對齊 PM 板既有 `C-P7`（PM 板本來就把 cancelled 排除在四態之外）——同一張單在 PM 板
    不算、設備板算＝內部矛盾，這支就是消除那個矛盾的單一判定（2026-08-11 Chris 拍板①）。
    消費端一律 `if w8.wo_is_cancelled(w): continue`，**不得**各自再寫一份 predicate。"""
    if not isinstance(rec, dict):
        return False
    st = rec.get("status")
    if isinstance(st, str) and st.strip().upper() in CANCEL_STATUSES:
        return True
    hist = rec.get("status_history") or []
    if hist:
        last = hist[-1]
        if isinstance(last, dict):
            lst = last.get("status")
            if isinstance(lst, str) and lst.strip().upper() in CANCEL_STATUSES:
                return True
    return False


def wo_failure_start(rec, now=None):
    """normalized WO → **生效的**停機起點對齊值（台北 naive datetime）／None（未設定）。

    ＝待辦 4 的**單一 predicate**（SPEC-LASTPROD-PUSH §11；規則與 cmms 引擎對齊、勿自創）：
      ① `failure_started_at`（fsa）為 null ⇒ None（**位元級不變**——一切照舊）。
      ② fsa 髒/未來（`guard_ts`：<1901 sentinel、> now）⇒ None（§21.1 紀律：丟棄不猜）。
      ③ `opened_at` 缺/髒 ⇒ None（**無從驗證方向**——第五條規則要能比才算數；此時窗
         起點本就走 history 回退，別再疊一個沒驗過方向的對齊值）。
      ④ ★ **`fsa >= opened_at` ⇒ None（視為未設定、絕不照裁）**——2026-08-16 追加的
         方向鐵閘。cmms 已在 domain 層拒寫這種值（存量實查 0 張）⇒ 本條今天打不到任何
         一列＝**純防呆**；它防的是上游壞掉時**不出聲地砍自家停機**（那是最糟的失敗型）。
         被本條擋下的單**不計入**「N 張單走對齊起點」揭露（它沒有走對齊，只是有個壞值）。
      ⑤ 其餘（恆 `fsa < opened_at`）⇒ 回 fsa＝停機起點提前到現場按鈕對齊的 MES 末筆生產。
    ★ **2026-09-12 cmms ADR-032（NOTE-74）：`fsa` 的語意變了、本函式一個位元不動**——
      對面不再把現場填的值夾到開單日 09:00 台北 ⇒ 新值**只會更早**（形狀／null 語意／
      方向鐵閘 `fsa < opened_at` 全不變，更早永遠撞不到那道閘）。既有值**不回溯** ⇒
      新舊混在同一欄、**唯一邊界＝對面的部署日**（⛳ 待 cmms 告知）⇒ 在知道那天以前，
      **不得**拿 `fsa` 做跨期比較並說差異來自現場行為。存證＝`SPEC-LASTPROD-PUSH.md`
      §11「第六條」。
    純函式；now 由 caller 傳（測試 deterministic）。"""
    now = now if now is not None else _dt.datetime.now()
    fsa = guard_ts((rec or {}).get("failure_started_at"), now)
    if fsa is None:
        return None
    opened = guard_ts((rec or {}).get("opened_at"), now)
    if opened is None or fsa >= opened:
        return None
    return fsa


def wo_production_resumed(rec, now=None):
    """normalized WO → **生效的**停機終點對齊值（台北 naive datetime）／None（未設定）。

    ＝NOTE-60 消費的**單一 predicate**（`wo_failure_start` 的鏡像；規則與 cmms 引擎對齊、勿自創）：
      ① `production_resumed_at`（pra）為 null ⇒ None（**位元級不變**——一切照舊走結單時刻；
         cmms 明講 NULL＝「沒有人對齊過」⇒ 舊單零影響）。
      ② pra 髒/未來（`guard_ts`：<1901 sentinel、> now）⇒ None（§21.1 紀律：丟棄不猜）。
      ③ `opened_at` 缺/髒 ⇒ None（**無從驗證方向**——與 fsa 第三條同理；此時窗終點本就走
         history/結單，別再疊一個沒驗過方向的對齊值）。
      ④ ★★ **`pra <= 停機起點參考值` ⇒ None（視為未設定、絕不照裁）**——方向鐵閘。
         **★ 2026-08-21 修正（cmms 第二十五輪 §一-2 實證要求；原本這裡寫 `opened_at`＝抓錯下界）**：
         下界必須是**我方自己那條停機的起點**、也就是 cmms `downtime_end_reference` 的同一點：
             · 非 PM ⇒ **fsa（有就優先）否則 opened_at**
             · PM   ⇒ **status_history 首次轉入 IN_PROGRESS**（缺 ⇒ 退回 opened_at，
                        見下方 ⚠；PM 的 OPEN 前綴本就不計停機 ⇒ 退化不致造假）
         **為什麼原本的 `opened_at` 是錯的**：cmms 另有鐵閘要求 `fsa` **嚴格早於** `opened_at`
         ⇒ 只要該單對齊過 fsa，**合法的 pra 就有一整段落在 `(fsa, opened_at]`**——那些值 cmms
         寫得進來、我方卻判成「未設定」而退回結單時刻。**那不是邊角，正是「延遲開單」**
         （＝cmms `failure_start_gap` 補算 `[fsa, opened_at)` 存在的理由）。cmms 給的實例：
             故障 08:00(fsa) → 09:30 復機(pra) → **14:00 才補開單** → 18:00 結單
             正確 = [08:00, 09:30) = 90 分；原本的我方 = 08:00→18:00 = 600 分 ⇒ **多算 8.5h**
         ——與 NOTE-60 那 21h **同一個病從防呆這道門走回來**，且方向同為危險側（多報停機
         沒有人會抱怨）。規則語意一字未變（「終點必須嚴格晚於起點」），只是把下界換對。
      ⑤ 其餘（恆 `pra > 參考值`）⇒ 回 pra＝停機終點裁到現場按鈕對齊的 MES 復機時刻。
    ⚠ **被 ④ 擋下的單必須計數揭露、不得靜默**（cmms 第二十五輪 §一-3 ask）：cmms domain 結構上
      **不可能**寫出「pra <= 其參考點」的值 ⇒ 這道閘真的擋到東西時，唯一可能的成因是
      **兩邊參考點不一致**、不是髒資料 ⇒ 那個計數是**裁決訊號**（非零＝該改的是門檻本身）。
      計數用 `pra_block_reason()`（見下）→ `board_eqmeeting.build_board` 的 `pra_blocked` 鍵。
    ⚠ 與段終點的關係在 `wo_active_window`：**`min(既有終點, pra)`**——先對齊、後結單
    （WO-36103 型：OPEN 期間已對齊 ⇒ 窗在 pra 收口，晚一天補結單不再多算 21h）；
    已有更早的 COMPLETED/終態 ⇒ 保留較早者（min 語意，與 cmms 引擎逐字同）。純函式。"""
    now = now if now is not None else _dt.datetime.now()
    pra = guard_ts((rec or {}).get("production_resumed_at"), now)
    if pra is None:
        return None
    ref, _kind = pra_reference(rec, now)
    if ref is None or pra <= ref:
        return None
    return pra


def pra_reference(rec, now=None):
    """normalized WO → (停機起點參考值, 種類)／(None, None)。**與 cmms `downtime_end_reference`
    同一個點**（第二十五輪 §一-2）：非 PM＝fsa or opened_at；PM＝首次 IN_PROGRESS。

    ★ **PM 缺 IN_PROGRESS ⇒ 回 `(None, None)`＝誠實拒，不退回 `opened_at`**
      （2026-08-22 第二十六輪 §一：我方原本 fallback 到 opened_at，cmms 指出該改）。
      cmms 的 PM 分支查無 IN_PROGRESS 就回 None、**不拿別的欄位當替身**（「維修根本還沒
      開始，沒有終點可修」），且其三個呼叫端全部停手 ⇒ **那種單在 cmms 結構上不可能有
      `production_resumed_at` 值**（欄位 cmms-native、無匯入路徑、唯一寫入口是那支 domain
      method）。⇒ 改判的理由**不是正確性而是訊號**：fallback 會**接受**一個對方產不出來的值，
      真出現就代表「要嘛 cmms 有 bug、要嘛有值繞過了唯一寫入路徑」——**兩種都該亮**。
      改回 None 後它自然落進 `pra_block_reason()` 的 **`no_reference`** 桶（零新機制）。
    ⚠ **非 PM 那一臂必須是「PM 的補集」，不得寫成 `== "REACTIVE"`**（第二十六輪 §二 主動示警）：
      eMaint 匯入的 `work_type` 有長尾——REACTIVE 與 PM 佔絕大多數、**ON HALT／
      PART REQUEST／CONVERSION／PROACTIVE／CORRECTIVE／DELAY 為長尾**——
      cmms 引擎對長尾與 REACTIVE **同判**（其護欄 #8 不替 work_type 硬編特例）。寫成 `== REACTIVE`
      會讓 ~750 張長尾單掉進 PM 分支、去找一個永不存在的 IN_PROGRESS ⇒ **不炸但靜默走錯分支**。
      回歸釘見 selftest「長尾 work_type」那組。純函式。"""
    now = now if now is not None else _dt.datetime.now()
    rec = rec or {}
    if (rec.get("work_type") or "").strip().upper() == "PM":
        for e in (rec.get("status_history") or []):
            if isinstance(e, dict) and e.get("status") == "IN_PROGRESS":
                t = guard_ts(e.get("at"), now)
                if t is not None:
                    return t, "pm_in_progress"
        return None, None          # ★ 誠實拒（見 docstring）：不臆造替身、路由進 no_reference
    fsa = wo_failure_start(rec, now)
    if fsa is not None:
        return fsa, "fsa"
    opened = guard_ts(rec.get("opened_at"), now)
    return (opened, "opened_at") if opened is not None else (None, None)


def pra_block_reason(rec, now=None):
    """normalized WO → 這張單的 pra **為什麼沒生效**／None（生效或本來就沒 pra）。

    值域：`None`｜`"dirty"`（髒/未來，§21.1 丟棄不猜）｜`"no_reference"`（連停機起點都定不出來）
    ｜**`"not_after_reference"`（★ 唯一的裁決訊號——見 `wo_production_resumed` ④ 的 ⚠）**。
    ⚠ pra 本來就是 null ⇒ 回 `None`（**那不是「被擋下」**，是沒有人對齊過；混進計數會把
      裁決訊號淹掉，正是 cmms 要的反面）。純函式、與 `wo_production_resumed` 共用同一組規則。"""
    now = now if now is not None else _dt.datetime.now()
    raw = (rec or {}).get("production_resumed_at")
    if raw is None or raw == "":
        return None
    if guard_ts(raw, now) is None:
        return "dirty"
    ref, _kind = pra_reference(rec, now)
    if ref is None:
        return "no_reference"
    return "not_after_reference" if guard_ts(raw, now) <= ref else None


# ---------------------------------------------------------------------------
# 3) wo_active_window — status/caveat 規則 + 髒資料防護
# ---------------------------------------------------------------------------

def wo_active_window(rec, now=None):
    """normalized WO → (start, end|None)；無有效窗 → None。end=None ＝ ongoing。

    契約規則：
      - 活躍窗＝ open → 首個 COMPLETED；無 COMPLETED → 首個終態(CLOSED/CANCELLED/VOIDED)；
        皆無 → ongoing（end=None）。
      - ★遷移 caveat：status_history 空 → 回退 opened_at→closed_at。

    自選規則（契約沉默點，見檔尾清單）：
      - 「open」時刻＝opened_at 欄優先；缺/髒 → status_history 首筆時戳。
      - ★ 待辦 4（2026-08-16）：`wo_failure_start` 生效（恆嚴格 `< opened_at`）⇒
        **窗起點提前到 fsa**＝`[fsa, opened_at)` 以停機計入（REACTIVE 的 OPEN 前綴
        `_phase_kind=='count'` ⇒ 自動落維修；PM 的 OPEN 本就不計＝結構相容）。
        fsa 為 null／被第五條擋下 ⇒ 本函式**位元級不變**。
      - ★ NOTE-60（2026-08-22）：`wo_production_resumed` 生效（恆嚴格 `> opened_at`）⇒
        **窗終點裁到 min(既有終點, pra)**——「對齊」與「結單」是兩個獨立事實；OPEN 且已
        對齊的單不再 ongoing（WO-36103 那 21h 就是這條缺口）。窗終點的變動經 lifecycle
        （`board_maint.wo_lifecycle`）自然穿透到 lead/MTTR＝Chris 2026-08-22 拍板
        「MTTR 一起裁到 pra」（復機才是修好；亦是一鍵結單 MTTR=0 之外的第二條真實時刻）。
        pra 為 null／被方向鐵閘擋下 ⇒ 本函式**位元級不變**。
      - 髒資料（§21.1 紀律）：start 髒/未來 → 整窗丟棄（None）；end 髒/未來 → 該候選
        跳過（F4：history 路徑取「首個**有效** COMPLETED、無則首個**有效**終態」——
        髒 COMPLETED 不得遮蔽同單有效終態；全部候選無效才 end=None ongoing）；
        end < start → 整窗丟棄（無從知道哪端錯，clamp 會造假、誠實=丟）。
    """
    now = now if now is not None else _dt.datetime.now()
    hist = rec.get("status_history") or []

    start = guard_ts(rec.get("opened_at"), now)
    if start is None and hist:
        start = guard_ts(hist[0]["at"], now)
    if start is None:
        return None
    fsa = wo_failure_start(rec, now)         # 恆 < opened_at；opened_at 髒時恆 None
    if fsa is not None and fsa < start:
        start = fsa

    if hist:
        end = None
        for e in hist:                      # 首個「有效」COMPLETED 優先（契約字面 + F4）
            if e["status"] == "COMPLETED" and guard_ts(e["at"], now) is not None:
                end = e["at"]
                break
        if end is None:
            for e in hist:                  # 無有效 COMPLETED → 首個「有效」終態（F4：
                if (e["status"] in TERMINAL_STATUSES        # 髒 COMPLETED 不得遮蔽
                        and guard_ts(e["at"], now) is not None):  # 同單有效終態）
                    end = e["at"]
                    break
    else:
        end = rec.get("closed_at")          # ★遷移 caveat 回退

    end = guard_ts(end, now)                # 髒/未來 end → ongoing
    # ★ NOTE-60（2026-08-22）：pra 生效（恆嚴格 > opened_at ≥ 走 fsa 前的 start）⇒
    #   **停機終點＝min(既有終點, pra)**——「對齊」與「結單」是兩個獨立事實，先對齊後結單
    #   合法（WO-36103 型：OPEN 且已對齊 ⇒ 窗在 pra 收口、不再 ongoing 到結單）。
    #   pra 為 null／被方向鐵閘擋下 ⇒ 本函式**位元級不變**。
    #   ⚠ pra 恆 > opened_at 但 start 可能＝fsa < opened_at ⇒ end=pra 恆 > start、
    #   不觸發下方整窗丟棄（方向鐵閘在 predicate 裡就保證了這一點）。
    pra = wo_production_resumed(rec, now)
    if pra is not None and (end is None or pra < end):
        end = pra
    if end is not None and end < start:
        return None                         # 損毀窗 → 整窗丟棄
    return (start, end)


def migration_fallback_rate(wos):
    """NOTE-6 ① 要求「全量回退要看得見」：回 (走遷移回退的筆數, 總筆數)。
    回退＝status_history 空（遷移 caveat 路徑）。caller（儀表板/log）應呈現比率——
    **比率 ≈100% 且樣本含新單＝形狀錯位訊號（FP-6），不是「全是遷移單」**。"""
    tot = fb = 0
    for w in wos:
        if not isinstance(w, dict):
            continue
        tot += 1
        if not w.get("status_history"):
            fb += 1
    return (fb, tot)


# ---------------------------------------------------------------------------
# 4) classify_gaps — MES 停機窗 × WO 活躍窗 overlap 切分
# ---------------------------------------------------------------------------

def classify_gaps(gaps, wos, now=None):
    """單一 EID 的 MES 停機窗列表 × 該 EID 的 normalized WO 列表 → 分類 segment 列表。

    輸入：
      gaps：[(start, end), …]（MES 偵測的停機窗；datetime；上游 mes_timeline 已做
            `<= GETDATE()` 過濾，本函式只 skip 形狀損毀者 end<=start）。
            **前置條件（F7）**：gaps 應互不重疊（mes_timeline 產出天然如此）；本函式
            防禦性依 start 排序（輸出保證時間序），但**重疊的 gaps 屬 caller 錯誤**
            ——重疊區會在兩個 gap 各出一段（雙計），本函式不偵測不修復。
      wos ：normalize_wo() 輸出列表；**允許含 None**（normalize_wo 對壞資料合法回
            None，自然組合 [normalize_wo(r) for r in raw] 直接可餵——F3 skip 之）。
            **caller 責任＝已按 EID 分好**（本 stub 不做站級 rollup，見模組 docstring
            TODO）。**dup work_order_no 防護（F5）**：同 wo_no 多筆若內容一致→去重取一；
            若內容衝突→該 wo_no 全丟（BigInt PK 出現衝突複本＝髒資料，不猜哪筆真）。

    輸出：每 gap 依 WO 活躍窗重疊切分後的 segment（時間排序、gap 內完整鋪滿）：
      {start, end, state, confidence, is_canonical, source, work_order_no}
      - WO 覆蓋段：source="cmms_workorder"、state/confidence 依 wo_state。
      - 未被任何 WO 覆蓋的殘餘：state="不明"（§23 唯一兜底、confidence=low）、
        source=None、work_order_no=None。
      - ongoing WO（end=None）：覆蓋到該 gap 末端（觀測窗內全蓋）。

    多 WO 重疊同一時段＝**契約未定、待 §19 對版**（沉默點）——本實作 deterministic 規則：
      **活躍窗 start 較早者優先**（opened_at 有效時 start≡opened_at，同義；S1 死邏輯
      已簡化）；平手取 work_order_no 較小者。單段只貼一單（每時間窗恰好一態，§23.1）。

    區間語意（自選）：半開 [start, end)；端點相接不算重疊。相鄰同分類 segment 於
    gap 內合併；**不跨 gap 合併**（保留 MES 窗邊界）。
    """
    now = now if now is not None else _dt.datetime.now()

    covers = []
    for w in wos:
        if not isinstance(w, dict):
            continue   # F3：normalize_wo 合法回 None → skip，不炸
        win = wo_active_window(w, now=now)
        if win is None:
            continue
        ws, we = win
        state, conf, canon = wo_state(w.get("work_type"))
        covers.append({"start": ws, "end": we, "state": state, "confidence": conf,
                       "is_canonical": canon, "wno": w["work_order_no"],
                       "prio": (ws, w["work_order_no"])})

    # F5：dup work_order_no 防護（PK 不可重複；重複＝髒）——內容一致去重取一、衝突全丟。
    _by_wno = {}
    for c in covers:
        _by_wno.setdefault(c["wno"], []).append(c)
    covers = []
    for wno in sorted(_by_wno):
        group = _by_wno[wno]
        if len({(g["start"], g["end"], g["state"]) for g in group}) == 1:
            covers.append(group[0])
        # else：衝突複本 → 全丟（該時段自然殘留「不明」→ 誠實兜底）

    # F7：防禦性排序（形狀損毀先濾再排；輸出因此保證時間序——前提 gaps 互不重疊）
    valid_gaps = []
    for gap in gaps:
        try:
            gs, ge = gap
        except (TypeError, ValueError):
            continue
        if not isinstance(gs, _dt.datetime) or not isinstance(ge, _dt.datetime):
            continue
        if gs.tzinfo is not None or ge.tzinfo is not None:
            continue   # MES 段本就台北 naive；aware gap＝上游 bug → 防禦跳過（不代轉、不猜）
        if ge <= gs:
            continue   # 零長度/損毀 gap → skip
        valid_gaps.append((gs, ge))

    segments = []
    for gs, ge in sorted(valid_gaps):

        # 邊界點：gap 端點 + 每個 WO 窗與 gap 的交集端點
        pts = {gs, ge}
        for c in covers:
            ce = c["end"] if c["end"] is not None else ge   # ongoing → 蓋到 gap 末端
            s, e = max(c["start"], gs), min(ce, ge)
            if s < e:
                pts.add(s)
                pts.add(e)
        spts = sorted(pts)

        gap_segs = []
        for a, b in zip(spts, spts[1:]):
            best = None
            for c in covers:
                ce = c["end"] if c["end"] is not None else ge
                if c["start"] <= a and ce >= b:            # 完整覆蓋 [a,b)
                    if best is None or c["prio"] < best["prio"]:
                        best = c
            if best is None:
                seg = {"start": a, "end": b, "state": "不明", "confidence": "low",
                       "is_canonical": True, "source": None, "work_order_no": None}
            else:
                seg = {"start": a, "end": b, "state": best["state"],
                       "confidence": best["confidence"],
                       "is_canonical": best["is_canonical"],
                       "source": "cmms_workorder", "work_order_no": best["wno"]}
            # 相鄰同分類合併（gap 內）
            if gap_segs and gap_segs[-1]["end"] == seg["start"] and all(
                    gap_segs[-1][k] == seg[k]
                    for k in ("state", "confidence", "is_canonical",
                              "source", "work_order_no")):
                gap_segs[-1]["end"] = seg["end"]
            else:
                gap_segs.append(seg)
        segments.extend(gap_segs)

    return segments


# ---------------------------------------------------------------------------
# 5) normalize_notes — WO 明細事件流（cmms detail v1 additive 擴充；2026-07-07）
# ---------------------------------------------------------------------------
#
# cmms 於 contract_wo_detail.v1 ADDITIVE 擴充（master、可能尚未部署）：
#   · notes: [{occurred_at, entry_type, body, source_actor}]——升冪 by occurred_at（平手 by id）、
#     只保留最新 200 筆＋notes_truncated:bool；occurred_at 序列化＝同 status_history.changed_at
#     （UTC ISO-8601 'Z'）。
#   · action_taken: nullable——結單處置摘要（Chris 2026-07-07 從結單 UI 移除→日後多為 null、
#     有值才顯、絕不倚賴）。
# ★ 部署時序：live 端點「一段時間內」可能仍不吐這兩欄 → **一切消費必須容忍其缺席**
#   （缺席＝行為與今日相同；不得因缺欄崩潰）。
# ★ entry_type vocab（additive、cmms 會長）：report/progress/diagnosis/hold/resume/part/
#   note/ai_candidate——消費端 **禁止白名單拒斥未知值**（保留、原碼 raw 顯示）。
# ★ 語意裁決（DISJOINT，無需去重）：靜態 `diagnosis` 欄＝eMaint legacy 匯入的最終診斷快照
#   （僅歷史 WO 有、新 WO 恆 null）；`entry_type='diagnosis'` 的 note＝live 流程紀錄——兩來源
#   互斥，顯示層各自呈現（靜態者標「eMaint 最終診斷」）。
# ★ 治理（§24 D1/D2，同 status_history precedent）：`source_actor` 邊界丟棄（可含 human:<id>）。
#
# 本組函式＝**additive 純函式**（不改 normalize_wo；notes 不在其 6 欄白名單、normalize_wo 天然
# 丟棄之——notes 處置是獨立 helper）。QG-1 紀律：既有函式行為零改動。

# entry_type 已知詞彙（additive；**僅供文件/顯示對映參考，消費端不得用來拒斥未知值**）
NOTE_ENTRY_TYPES_KNOWN = ("report", "progress", "diagnosis", "hold",
                          "resume", "part", "note", "ai_candidate")


def normalize_notes(detail):
    """cmms WO detail dict → 清洗後 notes 列表（時間序、UTC-Z→台北牆鐘、去 source_actor）。

    輸入＝raw detail dict（可能**完全無 `notes`**＝pre-deploy／舊 WO → 回 []）。逐筆：
      - `occurred_at` 經既有 `_parse_dt`（UTC-Z → 台北牆鐘、同 status_history 紀律）；
        解析失敗＝丟該筆（大聲少一筆、不炸整包）。
      - `entry_type` **原樣保留**（含未知值；不白名單拒斥＝契約指示）；None→""。
      - `body` 逐字保留（strip）；空 body＝丟該筆。
      - `source_actor` 治理丟棄（§24 D1/D2，同 status_history precedent）。
    輸出＝[{"at": <naive 台北 datetime>, "entry_type": <str>, "body": <str>}]，**升冪 by at**
      （信任輸入升冪、仍防禦性再排；平手保持穩定＝Python sort 穩定、等同 by id 次序）。

    純函式、零 I/O；notes_truncated 由 caller 讀 `notes_truncated(detail)`（本層不合併旗標）。"""
    if not isinstance(detail, dict):
        return []
    raw = detail.get("notes")
    if not isinstance(raw, list):
        return []                       # 缺 notes / 型別非 list（pre-deploy／髒）→ [] 容忍
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        body = e.get("body")
        if not isinstance(body, str) or not body.strip():
            continue                    # 空/非字串 body → 丟該筆（不炸）
        try:
            at = _parse_dt(e.get("occurred_at"))    # UTC-Z → 台北牆鐘（同 changed_at 紀律）
        except (ValueError, TypeError):
            continue                    # 壞時戳 → 丟該筆（loud in count, not crash）
        et = e.get("entry_type")
        et = str(et).strip() if et is not None else ""   # 原樣保留（未知值不拒斥）
        out.append({"at": at, "entry_type": et, "body": body.strip()})
    out.sort(key=lambda x: x["at"])     # 防禦性升冪（穩定排序＝平手保留輸入序 by id）
    return out


def notes_truncated(detail):
    """cmms detail 的 `notes_truncated` 旗標 passthrough（缺／非 bool→False；pre-deploy 容忍）。"""
    return bool(detail.get("notes_truncated")) if isinstance(detail, dict) else False


# ---------------------------------------------------------------------------
# 5) normalize_scope_events — A10 停機範圍事件（additive；CONCEPT §19.3 A10 / cmms NOTE-18）
# ---------------------------------------------------------------------------
#   ⚠ 這支**刻意不進 `_ALLOWED`**：`scope_events` 只在 **detail** 路徑出現（LIST 沒有這欄），
#     把它塞進 normalize_wo 的白名單＝對 LIST 列宣告一個永遠是 None 的欄、且會讓
#     「欄位恆在」pin (a) 的語意在兩條路徑上不一致。正解＝app 層取回 detail 後呼叫本函式，
#     把結果 attach 成 normalized wo 的 additive key（`board_scope` 只讀那個 key）。

def normalize_scope_events(events):
    """cmms detail 的 `scope_events` → `([{"ts": 台北 naive datetime, "scope": [str,…]}, …], n_dropped)`。

    契約 pin（NOTE-18；全文＝`CONCEPT.md` §19.3 A10）：
      (a) 欄位**恆在**、`[]`＝legacy／整機全程（**勿防 absent、勿當缺資料**）→ 回 `([], 0)`。
      (b) 開單**不**落初始整機事件 ⇒ 本層不補；初始範圍由 `asset_id`＋`opened_at` 推導
          （在 `board_scope.scope_timeline`）。
      (c) `ts` 升冪（回填時戳插入後重排）——本層**防禦性再排**（穩定排序＝同刻保留輸入序）。
      (d) 只曝 effective 事件（更正＝cmms 內部 supersede，消費端看不到）。

    逐筆清洗（丟棄並計數，**絕不炸整包**）：
      · `ts` 走既有 `_parse_dt`（UTC-Z → 台北牆鐘 +8，與 `occurred_at`／`changed_at` 同一支）。
      · `scope` 須為 list/tuple 且**清洗後非空**——**空 scope＝bad data**（cmms UI 結構上
        不產生空快照、非空是 domain 硬驗證）。放它過去＝製造一個「全部工位都恢復了」的
        幽靈事件，比丟掉嚴重得多。
      · 成員取非空 str（strip、**保序去重**）；`asset_id` 是 opaque string ⇒ 不 trim 內部、
        不正規化大小寫（legacy 髒值如 `EID-38173.` 照樣原樣傳下去）。
      · 其餘欄（若日後 additive）一律不取＝白名單紀律；per-person 欄天然消失（§24）。

    ★★ **2026-09-06（`SPEC-CAPACITY-FORECAST.md` W4）：`sub_units` 開始被帶出來。**
      每一筆輸出多一個 **`sub_units`（list、欄位恆在、`[]`＝整機）**——`[]` 與**欄位缺席**
      是同一個意思（A10 半 wire 的 pin：`[]`＝無；舊 cmms 版本根本沒這欄）⇒ 兩者都投影成 `[]`。
      清洗與 `scope` 同一套（非空 str、strip、保序去重）；**空 `sub_units` 永遠不算 bad data**
      （那是絕大多數工單的正常樣子，與空 `scope` 完全不同）。
      ⚠ **消費紀律**：`sub_units` 只給**停機當量係數**（`board_capacity.equiv_units_weighted`）用。
        `board_scope` 的 clip／機器視角**仍然照整機停算**（cmms NOTE-70 ack 的那條紀律不變）
        ——A／U 不因座而變（C-CF8）。

    `n_dropped > 0` ⇒ 呼叫端應自揭（頁面/log），不得靜默。純函式、零 I/O。"""
    if not isinstance(events, (list, tuple)):
        return ([], 0)
    rows, dropped = [], 0
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            dropped += 1
            continue
        sc = e.get("scope")
        if not isinstance(sc, (list, tuple)):
            dropped += 1
            continue
        members = _clean_id_list(sc)
        if not members:                     # 空 scope＝bad data（見 docstring）
            dropped += 1
            continue
        try:
            ts = _parse_dt(e.get("ts"))
        except (ValueError, TypeError):
            dropped += 1
            continue
        su = e.get("sub_units")
        # 空／缺席／壞形一律 `[]`＝整機（**不丟棄整筆**：空 sub_units 是正常樣子，不是 bad data）
        subs = _clean_id_list(su) if isinstance(su, (list, tuple)) else []
        rows.append((ts, i, members, subs))
    rows.sort(key=lambda t: (t[0], t[1]))   # pin (c) 防禦性升冪（穩定＝同刻保留輸入序）
    return ([{"ts": ts, "scope": ms, "sub_units": su} for ts, _i, ms, su in rows], dropped)


def _clean_id_list(seq):
    """opaque id 序列 → 非空 str、strip、**保序去重**（`scope` 與 `sub_units` 共用一套）。"""
    out, seen = [], set()
    for m in seq or ():
        if not isinstance(m, str):
            continue
        m = m.strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def effective_seat_units(wo, at=None):
    """一張工單此刻指名的座（C-RW28）→ `[str…]`。

    先用與既有 `effective_sub_units` 相同的 effective-event 選擇，再把 A10
    `sub_units` 與 NOTE-79 `scope` 裡屬於同母機的座 ID 委派給
    `board_capacity.event_seat_units` 聯集。空 list 仍是整機；list 回傳形狀保留
    W4 既有消費端的逐位元行為。
    """
    if not isinstance(wo, dict):
        return []
    evs, _n = normalize_scope_events(wo.get("scope_events"))
    if at is not None:
        evs = [e for e in evs if e["ts"] <= at]
    if not evs:
        return []
    # `board_capacity` imports scope, which imports this module; follow the
    # existing lazy fold_seat_id import at normalize_wo rather than creating a
    # module-initialization cycle.
    from board_capacity import event_seat_units, fold_seat_id
    event = evs[-1]
    parent = fold_seat_id(wo.get("asset_id"))[0]
    return list(event_seat_units(parent, event.get("scope") or (),
                                 event.get("sub_units") or ()))


def effective_sub_units(wo, at=None):
    """Backward-compatible thin wrapper for :func:`effective_seat_units`."""
    return effective_seat_units(wo, at=at)


# ---------------------------------------------------------------------------
# selftest（inline fixture、零 I/O、deterministic now）
# ---------------------------------------------------------------------------

def _selftest():
    NOW = _dt.datetime(2026, 7, 1, 12, 0, 0)   # 固定 now → 未來日期測試 deterministic
    n_checked = 0

    def ck(cond, msg):
        nonlocal n_checked
        assert cond, msg
        n_checked += 1

    # ---- fixture：raw WO（含各種故意的壞/髒/per-person） ----
    raw_pm = {   # 1) 正常 PM：完整 history（cmms 真形狀）；首個 COMPLETED（非 CLOSED）收窗
        "work_order_no": 1001, "asset_id": "EID-24742", "work_type": "PM",
        "opened_at": "2026-06-10T08:00:00", "closed_at": "2026-06-10T16:00:00",
        "status_history": [
            {"from_status": None, "to_status": "OPEN", "changed_at": "2026-06-10T08:00:00",
             "hold_reason": None, "source_actor": "human:wang123"},   # source_actor → 必丟(D1/D2)
            {"to_status": "IN_PROGRESS", "changed_at": "2026-06-10T08:30:00"},
            {"to_status": "COMPLETED", "changed_at": "2026-06-10T10:00:00"},
            {"to_status": "CLOSED", "changed_at": "2026-06-10T16:00:00"},
        ],
        "technician": "小王", "assignee": "小李",   # per-person → 必須被丟棄(D1/D2)
    }
    raw_reactive = {   # 2) REACTIVE：完整 history 含 ON_HOLD 往返（帶 hold_reason）
        "work_order_no": 1002, "asset_id": "EID-24742", "work_type": "REACTIVE",
        "opened_at": "2026-06-11 09:00:00",          # 空格式也要吃
        "status_history": [
            {"to_status": "OPEN", "changed_at": "2026-06-11 09:00:00"},
            {"to_status": "IN_PROGRESS", "changed_at": "2026-06-11 09:10:00"},
            {"to_status": "ON_HOLD", "changed_at": "2026-06-11 11:00:00",
             "hold_reason": "WAITING_MACHINE_TIME"},                  # 已知詞彙 → 保留
            {"to_status": "IN_PROGRESS", "changed_at": "2026-06-11 13:00:00"},
            {"to_status": "COMPLETED", "changed_at": "2026-06-11 15:00:00"},
            {"to_status": "REOPENED", "changed_at": "2026-06-11 15:30:00"},  # 未知 status → 丟條目
        ],
        "confirmed_by": "someone",                    # per-person → 丟
    }
    raw_migrated = {   # 3) ★遷移 caveat：history 空 → opened_at→closed_at
        "work_order_no": "1003", "asset_id": "EID-00884",   # 純數字 str wo_no → 容忍轉 int
        "work_type": "REACTIVE",
        "opened_at": "2026-06-12T00:00:00", "closed_at": "2026-06-12T06:00:00",
        "status_history": [],
    }
    raw_ongoing = {   # 4) ongoing：無 COMPLETED、無終態 → end=None
        "work_order_no": 1004, "asset_id": "EID-00884", "work_type": "REACTIVE",
        "opened_at": "2026-06-14T06:00:00",
        "status_history": [
            {"to_status": "OPEN", "changed_at": "2026-06-14T06:00:00"},
            {"to_status": "IN_PROGRESS", "changed_at": "2026-06-14T06:30:00"},
        ],
    }
    raw_partreq = {   # 5) 長尾 work_type → 暫桶
        "work_order_no": 1005, "asset_id": "EID-00950", "work_type": "PART REQUEST",
        "opened_at": "2026-06-13T08:00:00", "closed_at": "2026-06-13T09:00:00",
        "status_history": [],
    }

    # ---- normalize：白名單 + per-person 丟棄 + 健檢 ----
    pm = normalize_wo(raw_pm)
    ck(pm is not None, "正常 PM 應通過 normalize")
    # Seat metadata is derived locally, not admitted to the wire whitelist.
    ck(set(_ALLOWED) <= set(pm.keys())
       and set(pm.keys()) - set(_ALLOWED) == {"seat_id", "seat_unit", "seat_known"},
       f"欄位外洩: {set(pm.keys()) - set(_ALLOWED)}")
    ck("technician" not in pm and "assignee" not in pm, "per-person 欄位必須消失")

    rx = normalize_wo(raw_reactive)
    ck(rx is not None and "confirmed_by" not in rx, "REACTIVE normalize + per-person 丟棄")
    ck(len(rx["status_history"]) == 5, "未知 status 條目(REOPENED)應被丟棄")
    # CM-1：條目層欄名對映 + hold_reason + source_actor 治理
    for e in pm["status_history"] + rx["status_history"]:
        ck(set(e.keys()) == {"status", "at", "hold_reason"},
           f"條目欄位應為 status/at/hold_reason（source_actor/from_status 必丟）: {e}")
    ck(rx["status_history"][2]["hold_reason"] == "WAITING_MACHINE_TIME",
       "已知 hold_reason 應保留")
    hr_unknown = normalize_wo({"work_order_no": 1011, "asset_id": "EID-1",
                               "work_type": "PM", "opened_at": "2026-06-10T08:00:00",
                               "status_history": [
                                   {"to_status": "ON_HOLD",
                                    "changed_at": "2026-06-10T09:00:00",
                                    "hold_reason": "BRAND_NEW_VOCAB"}]})
    ck(hr_unknown is not None
       and hr_unknown["status_history"][0]["hold_reason"] == HOLD_REASON_UNKNOWN,
       "未知 hold_reason 應＝「不知道」保留、不丟整筆（CM-1 ②）")
    # canonical 5 值（cmms 2026-07-07 對版）全數原樣保留——顯示集擴 2→5、不再顯成「不知道」
    for hr_v in ("WAITING_PARTS", "WAITING_VENDOR", "OTHER"):
        hr_c = normalize_wo({"work_order_no": 1013, "asset_id": "EID-1",
                             "work_type": "REACTIVE",
                             "opened_at": "2026-06-10T08:00:00",
                             "status_history": [
                                 {"to_status": "ON_HOLD",
                                  "changed_at": "2026-06-10T09:00:00",
                                  "hold_reason": hr_v}]})
        ck(hr_c is not None
           and hr_c["status_history"][0]["hold_reason"] == hr_v,
           f"canonical hold_reason {hr_v} 應原樣保留（非「不知道」）")
    ck(len(HOLD_REASONS_KNOWN) == 5, "HOLD_REASONS_KNOWN＝canonical 5 值（顯示集）")
    # 舊形狀（{status,at}）條目＝缺 to_status → 丟；全舊形狀 → 掏空拒收（大聲、非靜默 fallback）
    ck(normalize_wo({"work_order_no": 1012, "asset_id": "EID-1", "work_type": "PM",
                     "opened_at": "2026-06-10T08:00:00",
                     "status_history": [
                         {"status": "OPEN", "at": "2026-06-10T08:00:00"}]}) is None,
       "舊形狀 history 應掏空拒收（NOTE-6 ① 反例：不得靜默觸發遷移回退）")
    # NOTE-6 ①：回退率可見
    ck(migration_fallback_rate([pm, rx, normalize_wo(raw_migrated), None])
       == (1, 3), "migration_fallback_rate 應數對（含 skip None）")

    mig = normalize_wo(raw_migrated)
    ck(mig is not None and mig["work_order_no"] == 1003, "數字 str wo_no 容忍轉 int")

    ong = normalize_wo(raw_ongoing)
    pr = normalize_wo(raw_partreq)
    ck(ong is not None and pr is not None, "ongoing/長尾 normalize")

    # 壞資料 → None 不炸（優雅降級 case，比照 QG-3.4「端點缺/壞」精神）
    ck(normalize_wo(None) is None, "非 dict → None")
    ck(normalize_wo("garbage") is None, "str → None")
    ck(normalize_wo({"work_order_no": "abc", "asset_id": "EID-1"}) is None,
       "壞 wo_no → None")
    ck(normalize_wo({"work_order_no": True, "asset_id": "EID-1"}) is None,
       "bool wo_no → None")
    ck(normalize_wo({"work_order_no": 9, "asset_id": ""}) is None, "空 asset_id → None")
    ck(normalize_wo({"work_order_no": 9, "asset_id": "EID-1",
                     "status_history": "corrupt"}) is None,
       "history 型別損毀 → 整筆拒收（不得偽裝成遷移空 history）")

    # ---- confirmed_reason_code（D6；additive 白名單、nullable、僅 REACTIVE 有意義、eqf 單軸）----
    ck("confirmed_reason_code" in set(_ALLOWED), "confirmed_reason_code 進白名單（D6）")
    ck(set(_ALLOWED) <= set(pm.keys())
       and set(pm.keys()) - set(_ALLOWED) == {"seat_id", "seat_unit", "seat_known"},
       "座資訊是三個 derived 欄；wire 白名單本身一字未動")
    _plain = normalize_wo({"work_order_no": 1599, "asset_id": "EID-00425",
                           "work_type": "REACTIVE", "opened_at": "2026-06-16T08:00:00",
                           "closed_at": "2026-06-16T10:00:00", "status_history": []})
    _plain_old = {"work_order_no": 1599, "asset_id": "EID-00425", "work_type": "REACTIVE",
                  "status": None, "status_history": [],
                  "opened_at": _dt.datetime(2026, 6, 16, 8, 0),
                  "closed_at": _dt.datetime(2026, 6, 16, 10, 0),
                  "confirmed_reason_code": None, "failure_started_at": None,
                  "production_resumed_at": None}
    ck({key: _plain[key] for key in _ALLOWED} == _plain_old
       and (_plain["seat_id"], _plain["seat_unit"], _plain["seat_known"]) == (None, None, True),
       "pure EID keeps every pre-seat key bit-identical and adds empty seat metadata")
    import board_capacity as _wcap
    _spc = list(_wcap.NEST_EAV_UNITS)[1]       # WP-10d（R-P11）：登記座所屬機台由名冊推導（私有樹＝EID-26596）
    _seat = normalize_wo({"work_order_no": 1600, "asset_id": f" {_spc}-N2 ",
                          "work_type": "REACTIVE", "opened_at": "2026-06-16T08:00:00",
                          "status_history": []})
    ck((_seat["asset_id"], _seat["seat_id"], _seat["seat_unit"], _seat["seat_known"])
       == (_spc, f"{_spc}-N2", "nest:2", True),
       "seat WO folds into its parent and keeps additive seat metadata")
    ck(pm.get("confirmed_reason_code") is None, "缺欄 → confirmed_reason_code None（不拒收）")
    d6a = normalize_wo({"work_order_no": 1601, "asset_id": "EID-1", "work_type": "REACTIVE",
                        "opened_at": "2026-06-10T08:00:00", "status_history": [],
                        "confirmed_reason_code": "eqfPickupVacuumFault"})
    ck(d6a is not None and d6a["confirmed_reason_code"] == "eqfPickupVacuumFault",
       "confirmed_reason_code 非空 str → 保留（strip）")
    d6b = normalize_wo({"work_order_no": 1602, "asset_id": "EID-1", "work_type": "PM",
                        "opened_at": "2026-06-10T08:00:00", "status_history": [],
                        "confirmed_reason_code": None})
    ck(d6b is not None and d6b["confirmed_reason_code"] is None,
       "null confirmed_reason_code → None（未確認≠無故障）")
    d6c = normalize_wo({"work_order_no": 1603, "asset_id": "EID-1", "work_type": "REACTIVE",
                        "opened_at": "2026-06-10T08:00:00", "status_history": [],
                        "confirmed_reason_code": "  "})
    ck(d6c is not None and d6c["confirmed_reason_code"] is None, "空白 → None")
    d6d = normalize_wo({"work_order_no": 1604, "asset_id": "EID-1", "work_type": "REACTIVE",
                        "opened_at": "2026-06-10T08:00:00", "status_history": [],
                        "confirmed_reason_code": 12345})
    ck(d6d is not None and d6d["confirmed_reason_code"] is None,
       "非 str confirmed_reason_code → None、不因此欄拒收整筆")

    # ---- status 欄透傳（2026-08-11；純 additive、wire 上本來就有）----
    ck("status" in set(_ALLOWED), "status 進白名單（2026-08-11 拍板②）")
    ck(pm.get("status") is None, "缺 status 欄 → None（不拒收整筆）")
    for raw_v, exp in ((" cancelled ", "CANCELLED"), ("Voided", "VOIDED"),
                       ("OPEN", "OPEN"), ("", None), ("   ", None),
                       (12345, None), (None, None),
                       ("SOME_FUTURE_STATE", "SOME_FUTURE_STATE")):
        _r = normalize_wo({"work_order_no": 1701, "asset_id": "EID-1", "work_type": "REACTIVE",
                           "opened_at": "2026-06-10T08:00:00", "status_history": [],
                           "status": raw_v})
        ck(_r is not None and _r["status"] == exp,
           f"status {raw_v!r} → {exp!r}（strip+upper、空/非 str→None、**未知值照存不驗 enum**）")

    # ---- wo_is_cancelled：四型 + 末筆說了算（2026-08-11 拍板①；絕不讀 closed_at）----
    def _mk_c(wno, status=None, hist=None, closed=None):
        return normalize_wo({"work_order_no": wno, "asset_id": "EID-1", "work_type": "REACTIVE",
                             "opened_at": "2026-06-24T10:39:00", "closed_at": closed,
                             "status": status,
                             "status_history": [{"to_status": s, "changed_at": t}
                                                for s, t in (hist or [])]})
    # (a) status=CANCELLED ＋ history **空**＝eMaint 匯出殭屍型（＝白名單存在的唯一理由）
    ck(wo_is_cancelled(_mk_c(2001, status="CANCELLED")) is True,
       "(a) status=CANCELLED + 空 history → True（只讀 history 末筆會靜默漏掉整批遷移單）")
    # (b) status 欄缺 ＋ history 末筆 CANCELLED
    ck(wo_is_cancelled(_mk_c(2002, hist=[("CANCELLED", "2026-08-10T08:15:00")])) is True,
       "(b) status None + history 末筆 CANCELLED → True")
    # (c) VOIDED 兩腿皆算
    ck(wo_is_cancelled(_mk_c(2003, status="VOIDED")) is True
       and wo_is_cancelled(_mk_c(2004, hist=[("VOIDED", "2026-08-10T08:15:00")])) is True,
       "(c) VOIDED → True（兩條腿）")
    # (d) 正常終態／進行中 → False（結案單的歷史停機不得消失）
    for _st, _h in (("CLOSED", [("CLOSED", "2026-07-01T08:00:00")]),
                    ("COMPLETED", [("COMPLETED", "2026-07-01T08:00:00")]),
                    ("OPEN", []), (None, []),
                    ("IN_PROGRESS", [("OPEN", "2026-06-24T10:39:00"),
                                     ("IN_PROGRESS", "2026-06-24T11:00:00")])):
        ck(wo_is_cancelled(_mk_c(2005, status=_st, hist=_h)) is False,
           f"(d) status={_st!r} → False（結案／進行中的停機是真的）")
    # (e) 末筆說了算：前面有 IN_PROGRESS、末筆 CANCELLED
    ck(wo_is_cancelled(_mk_c(2006, hist=[("OPEN", "2026-06-24T10:39:00"),
                                         ("IN_PROGRESS", "2026-06-25T09:00:00"),
                                         ("CANCELLED", "2026-08-10T08:15:00")])) is True,
       "(e) history 末筆 CANCELLED（前面有 IN_PROGRESS）→ True")
    # 反向：末筆非終態、中途曾 CANCELLED（理論髒資料）→ False（只認末筆）
    ck(wo_is_cancelled(_mk_c(2007, hist=[("CANCELLED", "2026-06-25T09:00:00"),
                                         ("IN_PROGRESS", "2026-06-26T09:00:00")])) is False,
       "中途 CANCELLED 但末筆 IN_PROGRESS → False（只認末筆）")
    # ★ 絕不讀 closed_at：CANCELLED 的 closed_at 恆 NULL（prod 14/14）⇒ 判定不得依賴它
    ck(wo_is_cancelled(_mk_c(2008, status="CANCELLED", closed=None)) is True,
       "closed_at=None 的 CANCELLED 仍判 True")
    ck(wo_is_cancelled(_mk_c(2009, status="CLOSED", closed="2026-07-01T08:00:00")) is False,
       "有 closed_at 的 CLOSED → False（closed_at 非判準、只是恰好有值）")
    # ★ 2026-08-16 cmms 新 wire 形（取消收斂：任何狀態皆可取消、且保留原值）——
    #   「CANCELLED 的 closed_at 恆 NULL」**已失效**，這兩條釘的就是新形狀。
    ck(wo_is_cancelled(_mk_c(2010, status="CANCELLED", closed="2026-07-01T08:00:00")) is True,
       "★ 從 CLOSED 取消的單：CANCELLED **帶非 null closed_at** 仍判 True"
       "（closed_at 兩種值都可能，本判定結構上不看它）")
    ck(wo_is_cancelled(_mk_c(2011, status="CLOSED", closed="2026-07-01T08:00:00",
                             hist=[("CLOSED", "2026-06-30T08:00:00"),
                                   ("CANCELLED", "2026-07-02T08:00:00"),
                                   ("CLOSED", "2026-07-03T08:00:00")])) is False,
       "★ 還原（CANCELLED→CLOSED）：末筆非 CANCELLED ⇒ 該單重新計入＝還原的語意")
    ck(wo_is_cancelled(_mk_c(2012, status="CANCELLED", closed="2026-07-01T08:00:00",
                             hist=[])) is True,
       "★ eMaint 匯入單（零 history）被取消：只有 status 那條腿救得了它")
    ck(wo_is_cancelled(None) is False and wo_is_cancelled("x") is False,
       "非 dict → False（壞資料不得被當成取消而靜默消失）")
    ck(set(CANCEL_STATUSES) == {"CANCELLED", "VOIDED"}
       and "CLOSED" not in CANCEL_STATUSES,
       "CANCEL_STATUSES 恰兩值、且**不含 CLOSED**（結案單停機是真的）")

    # ---- wo_state 對映表 ----
    ck(wo_state("PM") == ("計畫保養", "high", True), "PM 對映")
    ck(wo_state("REACTIVE") == ("維修", "high", True), "REACTIVE 對映")
    ck(wo_state("CONVERSION") == ("調機", "medium", True), "CONVERSION 對映")
    for wt in ("PART REQUEST", "ON HALT", "DELAY", "CORRECTIVE", "PROACTIVE",
               "WHATEVER_NEW", None):
        st, conf, canon = wo_state(wt)
        ck(st == BUCKET_STATE and conf == "low" and canon is False,
           f"{wt!r} 應落暫桶(low, 非canonical)")
    ck("計畫保養" in CANONICAL_STATES and BUCKET_STATE not in CANONICAL_STATES,
       "暫桶不得混入 canonical 10 態")

    # ---- wo_active_window：契約規則 + 遷移 caveat + ongoing ----
    w = wo_active_window(pm, now=NOW)
    ck(w == (_dt.datetime(2026, 6, 10, 8, 0), _dt.datetime(2026, 6, 10, 10, 0)),
       f"PM 窗應收在首個 COMPLETED（非 CLOSED）: {w}")
    w = wo_active_window(rx, now=NOW)
    ck(w == (_dt.datetime(2026, 6, 11, 9, 0), _dt.datetime(2026, 6, 11, 15, 0)),
       f"REACTIVE 窗 open→COMPLETED: {w}")
    w = wo_active_window(mig, now=NOW)
    ck(w == (_dt.datetime(2026, 6, 12, 0, 0), _dt.datetime(2026, 6, 12, 6, 0)),
       f"遷移 caveat 應回退 opened_at→closed_at: {w}")
    w = wo_active_window(ong, now=NOW)
    ck(w == (_dt.datetime(2026, 6, 14, 6, 0), None), f"ongoing 應 end=None: {w}")
    # 無 COMPLETED → 首個終態（CANCELLED）
    w = wo_active_window(normalize_wo({
        "work_order_no": 1006, "asset_id": "EID-1", "work_type": "PM",
        "opened_at": "2026-06-15T08:00:00",
        "status_history": [{"to_status": "OPEN", "changed_at": "2026-06-15T08:00:00"},
                           {"to_status": "CANCELLED", "changed_at": "2026-06-15T09:00:00"}]}),
        now=NOW)
    ck(w == (_dt.datetime(2026, 6, 15, 8, 0), _dt.datetime(2026, 6, 15, 9, 0)),
       f"無 COMPLETED → 首個終態收窗: {w}")

    # ---- 髒日期防護（§21.1 紀律） ----
    ck(guard_ts(_dt.datetime(1900, 1, 1), NOW) is None, "1900 sentinel → None")
    ck(guard_ts(_dt.datetime(2026, 8, 1), NOW) is None, "未來日期 → None")
    ck(guard_ts("not-a-dt", NOW) is None, "非 datetime → None")
    ck(guard_ts(_dt.datetime(2026, 6, 1), NOW) == _dt.datetime(2026, 6, 1), "正常值放行")
    # closed_at=1900 sentinel → 視為 ongoing（丟壞值不猜值）
    w = wo_active_window(normalize_wo({
        "work_order_no": 1007, "asset_id": "EID-1", "work_type": "PM",
        "opened_at": "2026-06-16T08:00:00", "closed_at": "1900-01-01T00:00:00",
        "status_history": []}), now=NOW)
    ck(w == (_dt.datetime(2026, 6, 16, 8, 0), None), f"髒 closed_at → ongoing: {w}")
    # opened_at 在未來 → 整窗丟棄
    ck(wo_active_window(normalize_wo({
        "work_order_no": 1008, "asset_id": "EID-1", "work_type": "PM",
        "opened_at": "2026-08-01T00:00:00", "status_history": []}), now=NOW) is None,
       "未來 opened_at → 整窗丟棄")
    # end < start → 整窗丟棄
    ck(wo_active_window(normalize_wo({
        "work_order_no": 1009, "asset_id": "EID-1", "work_type": "PM",
        "opened_at": "2026-06-13T10:00:00", "closed_at": "2026-06-13T08:00:00",
        "status_history": []}), now=NOW) is None, "end<start → 整窗丟棄")
    # 完全無時間資訊 → 無窗
    ck(wo_active_window(normalize_wo({
        "work_order_no": 1010, "asset_id": "EID-1", "work_type": "PM",
        "status_history": []}), now=NOW) is None, "無任何時戳 → None")

    # ---- 待辦 4：failure_started_at（fsa）五條規則（SPEC-LASTPROD-PUSH §11）----
    def _fsa_wo(fsa, opened="2026-06-16T08:00:00", closed="2026-06-16T12:00:00"):
        return normalize_wo({"work_order_no": 1011, "asset_id": "EID-1",
                             "work_type": "REACTIVE", "opened_at": opened,
                             "closed_at": closed, "failure_started_at": fsa,
                             "status_history": []})
    # 規則⑤（常態）：fsa < opened_at ⇒ 窗起點提前到 fsa＝[fsa, opened) 以停機計入
    _wf = _fsa_wo("2026-06-16T06:00:00")
    ck(_wf["failure_started_at"] == _dt.datetime(2026, 6, 16, 6, 0),
       "fsa 進白名單、naive 台北原樣")
    ck(wo_failure_start(_wf, NOW) == _dt.datetime(2026, 6, 16, 6, 0), "fsa<opened ⇒ 生效")
    ck(wo_active_window(_wf, now=NOW) == (_dt.datetime(2026, 6, 16, 6, 0),
                                          _dt.datetime(2026, 6, 16, 12, 0)),
       "窗起點＝fsa（[fsa, opened) 以停機計入）")
    # 規則①：fsa=null ⇒ 位元級不變
    ck(wo_active_window(_fsa_wo(None), now=NOW)
       == (_dt.datetime(2026, 6, 16, 8, 0), _dt.datetime(2026, 6, 16, 12, 0)),
       "fsa=null ⇒ 窗與加欄前位元級相同")
    # 規則④（★ 方向鐵閘、2026-08-16 追加）：fsa >= opened_at ⇒ 視為未設定、絕不照裁
    for bad in ("2026-06-16T08:00:00", "2026-06-16T11:00:00"):    # ==／> 兩形
        ck(wo_failure_start(_fsa_wo(bad), NOW) is None
           and wo_active_window(_fsa_wo(bad), now=NOW)
           == (_dt.datetime(2026, 6, 16, 8, 0), _dt.datetime(2026, 6, 16, 12, 0)),
           f"fsa>=opened_at ⇒ 未設定（上游壞掉防呆、不出聲砍停機是最糟失敗型）: {bad}")
    # 規則②：fsa 髒（1900 sentinel）/未來 ⇒ 未設定
    ck(wo_failure_start(_fsa_wo("1900-01-01T00:00:00"), NOW) is None, "髒 fsa（sentinel）⇒ None")
    ck(wo_failure_start(_fsa_wo("2026-07-30T00:00:00", closed=None), NOW) is None,
       "未來 fsa ⇒ None（guard_ts）")
    # 規則③：opened_at 缺/髒 ⇒ 無從驗證方向 ⇒ 未設定（窗照走 history 回退、不疊對齊值）
    _wnop = normalize_wo({"work_order_no": 1012, "asset_id": "EID-1", "work_type": "REACTIVE",
                          "failure_started_at": "2026-06-16T06:00:00",
                          "status_history": [{"to_status": "OPEN",
                                              "changed_at": "2026-06-16T08:00:00"}]})
    ck(wo_failure_start(_wnop, NOW) is None
       and wo_active_window(_wnop, now=NOW)[0] == _dt.datetime(2026, 6, 16, 8, 0),
       "opened_at 缺 ⇒ fsa 不生效、窗起點＝history 首筆")
    # UTC-Z wire 形：fsa 帶 Z ⇒ +8 轉台北（與 opened/closed 同一條解析）
    ck(wo_failure_start(_fsa_wo("2026-06-15T22:00:00Z", opened="2026-06-16T08:00:00"), NOW)
       == _dt.datetime(2026, 6, 16, 6, 0), "fsa UTC-Z → 台北 +8")

    # ---- NOTE-60：production_resumed_at（pra）五條規則（wo_failure_start 的鏡像）----
    def _pra_wo(pra, opened="2026-06-16T08:00:00", closed="2026-06-16T12:00:00",
                hist=()):
        return normalize_wo({"work_order_no": 1013, "asset_id": "EID-1",
                             "work_type": "REACTIVE", "opened_at": opened,
                             "closed_at": closed, "production_resumed_at": pra,
                             "status_history": list(hist)})
    # 規則⑤（常態）：pra > opened_at 且 < 既有終點 ⇒ 窗終點裁到 pra（對齊優先於結單）
    _wp = _pra_wo("2026-06-16T10:30:00")
    ck(_wp["production_resumed_at"] == _dt.datetime(2026, 6, 16, 10, 30),
       "pra 進白名單、naive 台北原樣")
    ck(wo_production_resumed(_wp, NOW) == _dt.datetime(2026, 6, 16, 10, 30),
       "pra>opened ⇒ 生效")
    ck(wo_active_window(_wp, now=NOW) == (_dt.datetime(2026, 6, 16, 8, 0),
                                          _dt.datetime(2026, 6, 16, 10, 30)),
       "窗終點＝pra（min(結單 12:00, pra 10:30)＝停機不再算到結單時刻）")
    # ★ WO-36103 型：OPEN 未結單但已對齊 ⇒ 窗在 pra 收口、不再 ongoing（那 21h 的修法本體）
    _wopen = _pra_wo("2026-06-16T10:30:00", closed=None)
    ck(wo_active_window(_wopen, now=NOW) == (_dt.datetime(2026, 6, 16, 8, 0),
                                             _dt.datetime(2026, 6, 16, 10, 30)),
       "★ 先對齊後結單（WO-36103 型）：OPEN＋pra ⇒ 終點＝pra、不 ongoing 到結單")
    # min 語意：既有終點早於 pra ⇒ 保留較早者（cmms 引擎逐字同）
    ck(wo_active_window(_pra_wo("2026-06-16T13:00:00"), now=NOW)
       == (_dt.datetime(2026, 6, 16, 8, 0), _dt.datetime(2026, 6, 16, 12, 0)),
       "pra 晚於既有終點 ⇒ min 保留較早的結單時刻（pra 不延長停機）")
    # 規則①：pra=null ⇒ 位元級不變（NULL＝沒有人對齊過＝照舊）
    ck(wo_active_window(_pra_wo(None), now=NOW)
       == (_dt.datetime(2026, 6, 16, 8, 0), _dt.datetime(2026, 6, 16, 12, 0)),
       "pra=null ⇒ 窗與加欄前位元級相同（舊單零影響）")
    # 規則④（★ 方向鐵閘）：pra <= opened_at ⇒ 視為未設定、絕不照裁
    #   ——壞值把終點拉到窗前會經 end<start 整窗丟棄**靜默滅掉整段真停機**（最糟失敗型）
    for bad in ("2026-06-16T08:00:00", "2026-06-16T05:00:00"):    # ==／< 兩形
        ck(wo_production_resumed(_pra_wo(bad), NOW) is None
           and wo_active_window(_pra_wo(bad), now=NOW)
           == (_dt.datetime(2026, 6, 16, 8, 0), _dt.datetime(2026, 6, 16, 12, 0)),
           f"pra<=opened_at ⇒ 未設定（防呆：不出聲砍掉整段停機）: {bad}")
    # 規則②：pra 髒（1900 sentinel）/未來 ⇒ 未設定
    ck(wo_production_resumed(_pra_wo("1900-01-01T00:00:00"), NOW) is None,
       "髒 pra（sentinel）⇒ None")
    ck(wo_production_resumed(_pra_wo("2026-07-30T00:00:00", closed=None), NOW) is None,
       "未來 pra ⇒ None（guard_ts）")
    # 規則③：opened_at 缺/髒 ⇒ 無從驗證方向 ⇒ 未設定
    _wnop2 = normalize_wo({"work_order_no": 1014, "asset_id": "EID-1", "work_type": "REACTIVE",
                           "production_resumed_at": "2026-06-16T10:30:00",
                           "status_history": [{"to_status": "OPEN",
                                               "changed_at": "2026-06-16T08:00:00"}]})
    ck(wo_production_resumed(_wnop2, NOW) is None
       and wo_active_window(_wnop2, now=NOW)[1] is None,
       "opened_at 缺 ⇒ pra 不生效、窗照舊 ongoing")
    # fsa＋pra 同單：起點＝fsa、終點＝pra（兩錨各自獨立、互不干擾）
    _wboth = normalize_wo({"work_order_no": 1015, "asset_id": "EID-1",
                           "work_type": "REACTIVE", "opened_at": "2026-06-16T08:00:00",
                           "closed_at": "2026-06-16T12:00:00",
                           "failure_started_at": "2026-06-16T06:00:00",
                           "production_resumed_at": "2026-06-16T10:30:00",
                           "status_history": []})
    ck(wo_active_window(_wboth, now=NOW) == (_dt.datetime(2026, 6, 16, 6, 0),
                                             _dt.datetime(2026, 6, 16, 10, 30)),
       "fsa＋pra 同單：窗＝[fsa, pra]（停機的兩端都錨在現場對齊值）")
    # UTC-Z wire 形：pra 帶 Z ⇒ +8 轉台北（與 fsa 同一條解析）
    ck(wo_production_resumed(_pra_wo("2026-06-16T02:30:00Z"), NOW)
       == _dt.datetime(2026, 6, 16, 10, 30), "pra UTC-Z → 台北 +8")

    # ---- ★ 第二十五輪 §一-2（2026-08-21）：方向鐵閘下界改成「我方的停機起點」----
    #   **回歸釘**：改回 opened_at 會讓下面這條在延遲開單上多算 8.5 小時（cmms 實證數字）。
    _late = normalize_wo({"work_order_no": 1016, "asset_id": "EID-1", "work_type": "REACTIVE",
                          "failure_started_at": "2026-06-16T08:00:00",   # 故障
                          "production_resumed_at": "2026-06-16T09:30:00",  # 復機
                          "opened_at": "2026-06-16T14:00:00",            # ★ 延遲開單
                          "closed_at": "2026-06-16T18:00:00", "status_history": []})
    ck(pra_reference(_late, NOW) == (_dt.datetime(2026, 6, 16, 8, 0), "fsa"),
       "非 PM 且有 fsa ⇒ 參考點＝fsa（不是 opened_at）")
    ck(wo_production_resumed(_late, NOW) == _dt.datetime(2026, 6, 16, 9, 30),
       "★ 延遲開單：pra 落在 (fsa, opened_at] ⇒ **生效**（原本被誤判為未設定）")
    ck(wo_active_window(_late, now=NOW) == (_dt.datetime(2026, 6, 16, 8, 0),
                                            _dt.datetime(2026, 6, 16, 9, 30)),
       "★ 窗＝[fsa 08:00, pra 09:30]＝90 分（原本 08:00→18:00＝600 分、多算 8.5h）")
    ck(pra_block_reason(_late, NOW) is None, "生效的 pra 不進被擋計數")
    # pra <= fsa ⇒ 仍擋（方向語意一字未變），且**這才是要計數揭露的那一種**
    _bad_ref = normalize_wo({"work_order_no": 1017, "asset_id": "EID-1", "work_type": "REACTIVE",
                             "failure_started_at": "2026-06-16T08:00:00",
                             "production_resumed_at": "2026-06-16T07:00:00",
                             "opened_at": "2026-06-16T14:00:00",
                             "closed_at": "2026-06-16T18:00:00", "status_history": []})
    ck(wo_production_resumed(_bad_ref, NOW) is None
       and pra_block_reason(_bad_ref, NOW) == "not_after_reference",
       "pra <= fsa ⇒ 擋下且回報 not_after_reference（＝參考點不一致的裁決訊號）")
    # pra 本來就 null ⇒ **不是被擋**（混進計數會把裁決訊號淹掉）
    ck(pra_block_reason(_pra_wo(None), NOW) is None, "pra=null 不算被擋（訊號不得被稀釋）")
    ck(pra_block_reason(_pra_wo("1900-01-01T00:00:00"), NOW) == "dirty", "髒值歸 dirty 桶")
    # PM ⇒ 參考點＝首次 IN_PROGRESS（PM 的 OPEN 前綴不計停機）
    _pm_wo = normalize_wo({"work_order_no": 1018, "asset_id": "EID-1", "work_type": "PM",
                           "opened_at": "2026-06-16T08:00:00",
                           "closed_at": "2026-06-16T18:00:00",
                           "production_resumed_at": "2026-06-16T09:00:00",
                           "status_history": [{"to_status": "OPEN", "changed_at": "2026-06-16T08:00:00"},
                                              {"to_status": "IN_PROGRESS", "changed_at": "2026-06-16T10:00:00"}]})
    ck(pra_reference(_pm_wo, NOW) == (_dt.datetime(2026, 6, 16, 10, 0), "pm_in_progress"),
       "PM ⇒ 參考點＝首次 IN_PROGRESS")
    ck(wo_production_resumed(_pm_wo, NOW) is None
       and pra_block_reason(_pm_wo, NOW) == "not_after_reference",
       "PM 的 pra 早於首次 IN_PROGRESS ⇒ 擋下並計數")
    # ★ 第二十六輪 §一：PM 缺 IN_PROGRESS ⇒ **誠實拒**（不臆造替身），落 no_reference 桶
    _pm_noip = normalize_wo({"work_order_no": 1019, "asset_id": "EID-1", "work_type": "PM",
                             "opened_at": "2026-06-16T08:00:00",
                             "closed_at": "2026-06-16T18:00:00",
                             "production_resumed_at": "2026-06-16T09:00:00",
                             "status_history": []})
    ck(pra_reference(_pm_noip, NOW) == (None, None)
       and wo_production_resumed(_pm_noip, NOW) is None,
       "★ PM 缺 IN_PROGRESS ⇒ 無參考點、pra 不生效（不退回 opened_at＝不臆造）")
    ck(pra_block_reason(_pm_noip, NOW) == "no_reference",
       "★ 且落 no_reference 桶——cmms 結構上產不出這種值，真出現要亮不要吞")
    # ★ 第二十六輪 §二：非 PM 那一臂必須是「PM 的補集」，**不得**寫成 == "REACTIVE"
    #   （eMaint work_type 長尾 ~750 張；寫成 == REACTIVE 會讓它們靜默走錯分支）
    for _wt in ("ON HALT", "PART REQUEST", "CONVERSION", "PROACTIVE", "CORRECTIVE",
                "DELAY", "", None, "reactive"):
        _tail = normalize_wo({"work_order_no": 1020, "asset_id": "EID-1", "work_type": _wt,
                              "failure_started_at": "2026-06-16T08:00:00",
                              "production_resumed_at": "2026-06-16T09:30:00",
                              "opened_at": "2026-06-16T14:00:00",
                              "closed_at": "2026-06-16T18:00:00", "status_history": []})
        ck(pra_reference(_tail, NOW) == (_dt.datetime(2026, 6, 16, 8, 0), "fsa"),
           f"長尾 work_type 走非 PM 臂（PM 的補集、非 ==REACTIVE）: {_wt!r}")
    # 大小寫／空白不得讓 PM 溜進非 PM 臂
    for _pmv in ("pm", " PM ", "Pm"):
        _pmc = normalize_wo({"work_order_no": 1021, "asset_id": "EID-1", "work_type": _pmv,
                             "opened_at": "2026-06-16T08:00:00",
                             "production_resumed_at": "2026-06-16T09:00:00",
                             "status_history": []})
        ck(pra_reference(_pmc, NOW) == (None, None), f"PM 大小寫/空白仍走 PM 臂: {_pmv!r}")

    # ---- classify_gaps ----
    D = _dt.datetime
    # (i) gap 與 WO 部分重疊 → 三段切分：不明 / 計畫保養 / 不明
    segs = classify_gaps([(D(2026, 6, 10, 7, 0), D(2026, 6, 10, 11, 0))], [pm], now=NOW)
    ck([s["state"] for s in segs] == ["不明", "計畫保養", "不明"],
       f"部分重疊切分錯: {[s['state'] for s in segs]}")
    ck(segs[1]["start"] == D(2026, 6, 10, 8, 0) and segs[1]["end"] == D(2026, 6, 10, 10, 0)
       and segs[1]["source"] == "cmms_workorder" and segs[1]["work_order_no"] == 1001
       and segs[1]["confidence"] == "high" and segs[1]["is_canonical"] is True,
       f"WO 覆蓋段欄位錯: {segs[1]}")
    ck(segs[0]["source"] is None and segs[0]["work_order_no"] is None
       and segs[0]["confidence"] == "low" and segs[0]["is_canonical"] is True,
       f"殘餘不明段欄位錯: {segs[0]}")
    # 鋪滿不變式：gap 內 segment 首尾相接、覆蓋整個 gap
    ck(segs[0]["start"] == D(2026, 6, 10, 7, 0) and segs[-1]["end"] == D(2026, 6, 10, 11, 0)
       and all(a["end"] == b["start"] for a, b in zip(segs, segs[1:])),
       "segment 未鋪滿 gap")

    # (ii) 完全無重疊 → 全不明（單段）
    segs = classify_gaps([(D(2026, 6, 20, 0, 0), D(2026, 6, 20, 2, 0))], [pm, rx], now=NOW)
    ck(len(segs) == 1 and segs[0]["state"] == "不明", f"無重疊應全不明: {segs}")
    # 零 WO → 全不明（優雅降級：源沒資料≠沒問題，殘餘誠實標不明）
    segs = classify_gaps([(D(2026, 6, 20, 0, 0), D(2026, 6, 20, 2, 0))], [], now=NOW)
    ck(len(segs) == 1 and segs[0]["state"] == "不明", "零 WO 應全不明")

    # (iii) 多 WO 重疊 → deterministic：opened_at 較早者優先
    wo_a = normalize_wo({"work_order_no": 2001, "asset_id": "EID-2",
                         "work_type": "REACTIVE", "opened_at": "2026-06-16T08:00:00",
                         "closed_at": "2026-06-16T11:00:00", "status_history": []})
    wo_b = normalize_wo({"work_order_no": 2002, "asset_id": "EID-2",
                         "work_type": "PM", "opened_at": "2026-06-16T09:00:00",
                         "closed_at": "2026-06-16T12:00:00", "status_history": []})
    for wos in ([wo_a, wo_b], [wo_b, wo_a]):   # 輸入順序不得影響結果（deterministic）
        segs = classify_gaps([(D(2026, 6, 16, 8, 0), D(2026, 6, 16, 12, 0))], wos, now=NOW)
        ck([(s["state"], s["work_order_no"]) for s in segs]
           == [("維修", 2001), ("計畫保養", 2002)],
           f"多 WO 重疊 deterministic 規則錯: {segs}")
        ck(segs[0]["end"] == D(2026, 6, 16, 11, 0), "重疊段應歸 opened_at 較早者(2001)")
    # tie-break：opened_at 相同 → work_order_no 較小者
    wo_c = normalize_wo({"work_order_no": 2003, "asset_id": "EID-2",
                         "work_type": "PM", "opened_at": "2026-06-16T08:00:00",
                         "closed_at": "2026-06-16T11:00:00", "status_history": []})
    segs = classify_gaps([(D(2026, 6, 16, 8, 0), D(2026, 6, 16, 11, 0))],
                         [wo_c, wo_a], now=NOW)
    ck(len(segs) == 1 and segs[0]["work_order_no"] == 2001,
       f"tie-break 應取 wo_no 較小: {segs}")

    # (iv) ongoing WO 覆蓋到 gap 末端
    segs = classify_gaps([(D(2026, 6, 14, 5, 0), D(2026, 6, 14, 9, 0))], [ong], now=NOW)
    ck([s["state"] for s in segs] == ["不明", "維修"]
       and segs[1]["end"] == D(2026, 6, 14, 9, 0),
       f"ongoing 應蓋到 gap 末端: {segs}")

    # (v) 暫桶 WO 上 gap：is_canonical=False 傳遞到 segment
    segs = classify_gaps([(D(2026, 6, 13, 8, 0), D(2026, 6, 13, 9, 0))], [pr], now=NOW)
    ck(len(segs) == 1 and segs[0]["state"] == BUCKET_STATE
       and segs[0]["is_canonical"] is False and segs[0]["confidence"] == "low",
       f"暫桶段錯: {segs}")

    # (vi) 損毀 gap（end<=start / 非 datetime）→ skip 不炸
    ck(classify_gaps([(D(2026, 6, 1), D(2026, 6, 1)), ("x", "y")], [pm], now=NOW) == [],
       "損毀 gap 應 skip")

    # ---- 2026-07-04 verify findings 修正案（F1–F5、S10） ----
    # F1：非空 history 全是未知 status → 掏空 → 整筆拒收（不得誤觸遷移回退成幻影 ongoing）
    ck(normalize_wo({"work_order_no": 3001, "asset_id": "EID-3", "work_type": "REACTIVE",
                     "opened_at": "2026-06-22T08:35:00", "closed_at": None,
                     "status_history": [{"to_status": "PENDING",
                                         "changed_at": "2026-06-22T08:35:00"}]}) is None,
       "F1：history 清洗後掏空應整筆拒收")
    # F2/S10 CLOSED（2026-07-05 A3 對版）：tz-aware datetime / 'Z'/offset 字串
    # → **轉台北牆鐘後去 tz**（§14.2 對齊層），不再拒收
    _tz = _dt.timezone(_dt.timedelta(hours=8))
    aware = _dt.datetime(2026, 6, 22, 8, 35, tzinfo=_tz)          # +08:00＝台北同牆鐘
    r = normalize_wo({"work_order_no": 3002, "asset_id": "EID-3", "work_type": "PM",
                      "opened_at": aware, "status_history": []})
    ck(r is not None and r["opened_at"] == D(2026, 6, 22, 8, 35),
       "S10：aware(+08:00) datetime → 同牆鐘 naive")
    ck(wo_active_window(r, now=NOW) == (D(2026, 6, 22, 8, 35), None),
       "S10：轉換後正常成窗（遷移回退 ongoing）")
    ck(normalize_wo({"work_order_no": 3003, "asset_id": "EID-3", "work_type": "PM",
                     "opened_at": "2026-06-22T00:35:00Z",
                     "status_history": []})["opened_at"] == D(2026, 6, 22, 8, 35),
       "S10：UTC 'Z' 字串（cmms prod 形）→ +8h 台北牆鐘")
    ck(normalize_wo({"work_order_no": 3003, "asset_id": "EID-3", "work_type": "PM",
                     "opened_at": "2026-06-22T08:35:00+08:00",
                     "status_history": []})["opened_at"] == D(2026, 6, 22, 8, 35),
       "S10：+08:00 offset 字串 → 同牆鐘 naive")
    ck(normalize_wo({"work_order_no": 3004, "asset_id": "EID-3", "work_type": "PM",
                     "opened_at": "2026-07-01T06:51:25.886468Z",
                     "status_history": []})["opened_at"] == D(2026, 7, 1, 14, 51, 25),
       "S10：微秒 UTC-Z（cmms prod 實測樣本形）→ 截微秒+轉台北")
    ck(normalize_wo({"work_order_no": 3004, "asset_id": "EID-3", "work_type": "PM",
                     "opened_at": "2026-06-22 08:35:00.123",
                     "status_history": []})["opened_at"] == D(2026, 6, 22, 8, 35),
       "小數秒尾綴允許截斷（無語意損失）")
    r5 = normalize_wo({"work_order_no": 3005, "asset_id": "EID-3", "work_type": "PM",
                       "opened_at": "2026-06-01T00:00:00",
                       "status_history": [{"to_status": "OPEN", "changed_at": aware},
                                          {"to_status": "OPEN",
                                           "changed_at": "2026-06-01T00:00:00"}]})
    ck(r5 is not None and [e["at"] for e in r5["status_history"]]
       == [D(2026, 6, 1), D(2026, 6, 22, 8, 35)],
       "history 混 aware/naive → aware 條目轉台北保留、時間序正確")
    ck(guard_ts(aware, NOW) == D(2026, 6, 22, 8, 35),
       "guard_ts 防禦層對 aware 同轉台北（S10 CLOSED）")
    # F3：wos 含 None（normalize_wo 合法輸出）→ skip 不炸
    segs = classify_gaps([(D(2026, 6, 10, 7, 0), D(2026, 6, 10, 11, 0))],
                         [None, pm, None], now=NOW)
    ck([s["state"] for s in segs] == ["不明", "計畫保養", "不明"],
       "F3：wos 含 None 應 skip 不炸")
    # F4：髒（未來）COMPLETED 不得遮蔽同單有效終態
    w = wo_active_window(normalize_wo({
        "work_order_no": 3006, "asset_id": "EID-3", "work_type": "REACTIVE",
        "opened_at": "2026-06-01T08:00:00",
        "status_history": [{"to_status": "OPEN", "changed_at": "2026-06-01T08:00:00"},
                           {"to_status": "COMPLETED", "changed_at": "2027-01-01T00:00:00"},
                           {"to_status": "CLOSED", "changed_at": "2026-06-02T08:00:00"}]}),
        now=NOW)
    ck(w == (D(2026, 6, 1, 8, 0), D(2026, 6, 2, 8, 0)),
       f"F4：髒 COMPLETED 應回退有效終態收窗: {w}")
    # F5：dup work_order_no——內容衝突全丟（殘留不明）、內容一致去重取一
    dup1 = normalize_wo({"work_order_no": 4001, "asset_id": "EID-4", "work_type": "PM",
                         "opened_at": "2026-06-16T08:00:00",
                         "closed_at": "2026-06-16T10:00:00", "status_history": []})
    dup2 = normalize_wo({"work_order_no": 4001, "asset_id": "EID-4",
                         "work_type": "REACTIVE", "opened_at": "2026-06-16T08:00:00",
                         "closed_at": "2026-06-16T10:00:00", "status_history": []})
    for order in ([dup1, dup2], [dup2, dup1]):
        segs = classify_gaps([(D(2026, 6, 16, 8, 0), D(2026, 6, 16, 10, 0))],
                             order, now=NOW)
        ck(len(segs) == 1 and segs[0]["state"] == "不明",
           f"F5：衝突 dup wo_no 應全丟→不明（輸入順序無關）: {segs}")
    segs = classify_gaps([(D(2026, 6, 16, 8, 0), D(2026, 6, 16, 10, 0))],
                         [dup1, dict(dup1)], now=NOW)
    ck(len(segs) == 1 and segs[0]["state"] == "計畫保養",
       f"F5：一致 dup wo_no 應去重取一: {segs}")
    # F7：gaps 亂序輸入 → 輸出仍時間序
    segs = classify_gaps([(D(2026, 6, 20, 4, 0), D(2026, 6, 20, 5, 0)),
                          (D(2026, 6, 20, 1, 0), D(2026, 6, 20, 2, 0))], [], now=NOW)
    ck([s["start"] for s in segs] == [D(2026, 6, 20, 1, 0), D(2026, 6, 20, 4, 0)],
       "F7：gaps 亂序應防禦性排序")

    # ---- normalize_notes（cmms detail v1 additive 擴充；2026-07-07）----
    # 缺 notes（pre-deploy／舊 WO）→ []（容忍、行為同今日）
    ck(normalize_notes({}) == [], "缺 notes → []")
    ck(normalize_notes({"work_order_no": 1}) == [], "無 notes 欄的 detail → []")
    ck(normalize_notes(None) == [] and normalize_notes("x") == [], "非 dict → []")
    ck(normalize_notes({"notes": "corrupt"}) == [], "notes 型別非 list → []（容忍不炸）")
    ck(notes_truncated({}) is False and notes_truncated({"notes_truncated": True}) is True
       and notes_truncated(None) is False, "notes_truncated passthrough（缺→False）")
    # 3 筆 UTC-Z 事件流 → 台北牆鐘（+8）、升冪、entry_type 原樣、source_actor 丟棄
    nd_notes = normalize_notes({"notes": [
        {"occurred_at": "2026-07-01T11:40:00Z", "entry_type": "diagnosis",
         "body": "真空度不足,疑真空泵老化。", "source_actor": "human:alice.fang"},
        {"occurred_at": "2026-07-01T09:15:00Z", "entry_type": "report",
         "body": "吸嘴堵塞、取料失敗連續報警", "source_actor": "human:ccchang"},
        {"occurred_at": "2026-07-02T16:45:00Z", "entry_type": "progress",
         "body": "更換真空泵完成,試機 OK。", "source_actor": "human:alice.fang"}]})
    ck([e["at"] for e in nd_notes] == [_dt.datetime(2026, 7, 1, 17, 15),
                                       _dt.datetime(2026, 7, 1, 19, 40),
                                       _dt.datetime(2026, 7, 3, 0, 45)],
       f"notes 升冪＋UTC-Z→台北+8: {[e['at'] for e in nd_notes]}")
    ck([e["entry_type"] for e in nd_notes] == ["report", "diagnosis", "progress"],
       "notes 依 at 升冪重排、entry_type 原樣")
    ck(all("source_actor" not in e and set(e.keys()) == {"at", "entry_type", "body"}
           for e in nd_notes), "notes 條目欄＝{at,entry_type,body}（source_actor 治理丟棄）")
    # 未知 entry_type 保留（不白名單拒斥＝契約指示）
    nd_unknown = normalize_notes({"notes": [
        {"occurred_at": "2026-07-01T00:00:00Z", "entry_type": "brand_new_kind",
         "body": "未來新增的事件類型"}]})
    ck(len(nd_unknown) == 1 and nd_unknown[0]["entry_type"] == "brand_new_kind",
       "未知 entry_type 應原樣保留（禁白名單拒斥）")
    # 壞筆逐條丟、不炸整包（空 body / 壞時戳 / 非 dict）
    nd_bad = normalize_notes({"notes": [
        {"occurred_at": "2026-07-01T00:00:00Z", "entry_type": "note", "body": "  "},
        {"occurred_at": "not-a-date", "entry_type": "note", "body": "壞時戳"},
        "garbage",
        {"occurred_at": "2026-07-01T02:00:00Z", "entry_type": "note", "body": "唯一好筆"}]})
    ck(len(nd_bad) == 1 and nd_bad[0]["body"] == "唯一好筆",
       f"壞筆（空body/壞時戳/非dict）逐條丟、留好筆: {nd_bad}")

    # ---- QG-3.4：對 _fixtures/w8.workorder.json golden 跑（檔缺→SKIP 註記，不 FAIL） ----
    fx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_fixtures", "w8.workorder.json")
    fx_note = "SKIP（_fixtures/w8.workorder.json 不在場）"
    if os.path.exists(fx_path):
        with open(fx_path, encoding="utf-8") as f:
            fx = json.load(f)
        for g in fx.get("golden", []):
            n = normalize_wo(g)
            ck(n is not None, f"fixture golden 應過 normalize: wo={g.get('work_order_no')}")
            exp = g.get("_active_window_expected") or {}
            w = wo_active_window(n, now=NOW)
            ck(w is not None, f"fixture golden 應有活躍窗: wo={g['work_order_no']}")
            ck(w[0] == _parse_dt(exp["start"]),
               f"fixture golden 窗 start 不符: wo={g['work_order_no']} got={w[0]}")
            expected_end = None if exp.get("end") is None else _parse_dt(exp["end"])
            ck(w[1] == expected_end,
               f"fixture golden 窗 end 不符: wo={g['work_order_no']} got={w[1]}")
        # invalid：0=PENDING 掏空(F1)→拒收；1=缺 asset_id→拒收；2=無時戳→無窗；
        #          3=downtime_minutes→白名單剝除（reject_by=provider_governance、記錄本身合法）
        inv = [c["record"] for c in fx.get("invalid", [])]
        ck(normalize_wo(inv[0]) is None, "fixture invalid[0]（PENDING 掏空）應拒收（F1）")
        ck(normalize_wo(inv[1]) is None, "fixture invalid[1]（缺 asset_id）應拒收")
        n2 = normalize_wo(inv[2])
        ck(n2 is not None and wo_active_window(n2, now=NOW) is None,
           "fixture invalid[2]（無任何時戳）應無活躍窗")
        n3 = normalize_wo(inv[3])
        ck(n3 is not None and "downtime_minutes" not in n3,
           "fixture invalid[3] downtime_minutes 應被白名單剝除（cmms 不主張停機）")
        fx_note = (f"fixture golden {len(fx.get('golden', []))} 筆窗全 match、"
                   f"invalid {len(inv)} 筆照 reject_by 處置")

    # ---- QG-3.7：cmms **真 golden** ×2（2026-07-05 A3 對版交付；UTC-Z→台北全鏈）----
    # contract_wo_detail.v1.json＝平鋪 WO；contract_wo_active_in.v1.json＝{items,truncated}。
    # 原樣位元拷貝自 cmms repo tests/fixtures/（golden 的意義＝wire 形狀一字不差）。
    NOW2 = _dt.datetime(2026, 7, 10, 12, 0, 0)   # golden 時戳落 7/1–7/3 台北 → now 需在其後
    cg_note = "SKIP（cmms golden 不在場）"
    _fxdir = os.path.dirname(os.path.abspath(__file__))
    dpath = os.path.join(_fxdir, "_fixtures", "contract_wo_detail.v1.json")
    apath = os.path.join(_fxdir, "_fixtures", "contract_wo_active_in.v1.json")
    if os.path.exists(dpath) and os.path.exists(apath):
        with open(dpath, encoding="utf-8") as f:
            det = json.load(f)
        nd = normalize_wo(det)
        ck(nd is not None, "cmms detail golden 應過 normalize（2026-07-05 前被整筆丟＝S10 觸雷）")
        ck(nd["opened_at"] == D(2026, 7, 1, 17, 12),
           f"detail golden opened_at 09:12Z→17:12 台北, got {nd['opened_at']}")
        for k in ("downtime_minutes", "downtime_estimated", "closed_by",
                  "assigned_person", "diagnosis", "parts",
                  "notes", "notes_truncated", "action_taken"):   # v1 additive 欄亦剝
            ck(k not in nd, f"detail golden 白名單應剝除 {k}")
        # v1 additive：notes 走獨立 helper（非 normalize_wo）——升冪、UTC-Z→台北、entry_type 原樣
        dn = normalize_notes(det)
        ck([e["at"] for e in dn] == [D(2026, 7, 1, 17, 15), D(2026, 7, 1, 19, 40),
                                     D(2026, 7, 3, 0, 45)]
           and [e["entry_type"] for e in dn] == ["report", "diagnosis", "progress"]
           and all("source_actor" not in e for e in dn),
           f"detail golden notes：3 筆升冪 UTC-Z→台北、entry_type 原樣、去 source_actor: {dn}")
        ck(notes_truncated(det) is False and det.get("action_taken"),
           "detail golden notes_truncated=False、action_taken 有值（結單處置）")
        ck(len(nd["status_history"]) == 3
           and nd["status_history"][1]["hold_reason"] == "WAITING_MACHINE_TIME",
           "detail golden history 3 條、governed hold_reason 保留")
        # ★ 待辦 4（2026-08-16）：detail golden 自帶 `failure_started_at` 07:30Z＝15:30 台北
        #   （NOTE-40 交付時就是 fsa 的示範樣本）⇒ 窗起點自此＝fsa（原 17:12＝opened_at）。
        ck(nd["failure_started_at"] == D(2026, 7, 1, 15, 30),
           f"detail golden fsa 07:30Z→15:30 台北（已入白名單、不再剝除）, got "
           f"{nd.get('failure_started_at')}")
        ck(wo_failure_start(nd, now=NOW2) == D(2026, 7, 1, 15, 30),
           "detail golden fsa < opened_at ⇒ predicate 生效")
        # ★ NOTE-60（2026-08-22）：detail golden 自帶 `production_resumed_at` 15:20Z＝23:20 台北
        #   （NOTE-53 交付時就帶的示範樣本）⇒ 窗終點自此＝min(CLOSED 次日 00:50, pra 23:20)
        #   ＝pra（原 00:50＝首個終態）。兩個釘子（此處與 active-in[0]）同輪改＝語意變更的存證。
        ck(nd["production_resumed_at"] == D(2026, 7, 2, 23, 20),
           f"detail golden pra 15:20Z→23:20 台北（已入白名單、不再剝除）, got "
           f"{nd.get('production_resumed_at')}")
        ck(wo_production_resumed(nd, now=NOW2) == D(2026, 7, 2, 23, 20),
           "detail golden pra > opened_at ⇒ predicate 生效")
        ck(wo_active_window(nd, now=NOW2)
           == (D(2026, 7, 1, 15, 30), D(2026, 7, 2, 23, 20)),
           "detail golden 窗：起點＝fsa 15:30（待辦 4）；終點＝pra 23:20（NOTE-60：對齊優先於"
           "結單時刻，min(終態 00:50, pra 23:20)）")
        with open(apath, encoding="utf-8") as f:
            act = json.load(f)
        ck(isinstance(act.get("items"), list) and len(act["items"]) == 2
           and act.get("truncated") is False,
           "active-in golden envelope＝{items×2, truncated:false}")
        na = [normalize_wo(r) for r in act["items"]]
        ck(all(x is not None for x in na), "active-in golden 兩筆皆應過 normalize")
        ck(wo_active_window(na[0], now=NOW2)
           == (D(2026, 7, 1, 15, 30), D(2026, 7, 2, 23, 20)),
           "active-in[0]（inline history）窗＝detail 同單同窗（起點＝fsa、終點＝pra）")
        ck(na[1]["failure_started_at"] is None and wo_failure_start(na[1], NOW2) is None,
           "active-in[1] fsa=null ⇒ predicate None（第一條規則）")
        ck(na[1]["production_resumed_at"] is None
           and wo_production_resumed(na[1], NOW2) is None,
           "active-in[1] pra=null ⇒ predicate None（NULL＝沒有人對齊過＝照舊）")
        ck(na[1]["status_history"] == [] and wo_active_window(na[1], now=NOW2)
           == (D(2026, 7, 3, 9, 0), None),
           "active-in[1]（still-open 遷移單、fsa=null）＝01:00Z→09:00 台北、ongoing＝位元級不變")
        ck(migration_fallback_rate(na) == (1, 2), "active-in 回退率＝1/2 可見")
        ck(wo_state(na[0]["work_type"])[0] == "維修"
           and wo_state(na[1]["work_type"])[0] == "計畫保養",
           "REACTIVE→維修 / PM→計畫保養（契約表）")
        cg_note = "cmms 真 golden ×2 全鏈 PASS（UTC-Z→台北+8、白名單、窗、回退率）"

    # ---- (SE) normalize_scope_events（A10 additive；CONCEPT §19.3 A10 / cmms NOTE-18）----
    #   四 pin + 空 scope＝bad data。**不進 _ALLOWED**（LIST 無此欄）＝結構釘在下面。
    ck("scope_events" not in _ALLOWED,
       "SE0 scope_events 不得進 _ALLOWED（只在 detail 路徑；進白名單＝對 LIST 列宣告永遠 None 的欄）")
    se_raw = [{"ts": "2026-08-20T02:30:00Z", "scope": ["EID-24790"]},
              {"ts": "2026-08-20T05:00:00Z", "scope": ["EID-24790", "EID-26841"]},
              {"ts": "2026-08-20T05:30:00Z", "scope": ["EID-24790"]}]
    se, nd_ = normalize_scope_events(se_raw)
    ck(nd_ == 0 and len(se) == 3, "SE1 golden 三筆全收、零丟棄")
    ck([e["ts"] for e in se] == [D(2026, 8, 20, 10, 30), D(2026, 8, 20, 13, 0),
                                 D(2026, 8, 20, 13, 30)],
       f"SE2 UTC-Z → 台北 +8（02:30Z→10:30）: {[e['ts'] for e in se]}")
    ck([e["scope"] for e in se] == [["EID-24790"], ["EID-24790", "EID-26841"], ["EID-24790"]],
       "SE3 scope 逐字保序（opaque string、不正規化）")
    ck(all(set(e) == {"ts", "scope", "sub_units"} for e in se),
       "SE4 輸出欄恰 ts/scope/sub_units（白名單；per-person 天然消失）")
    ck(all(e["sub_units"] == [] for e in se), "SE4b 缺席 sub_units 一律投影成 []＝整機")
    # pin (a)：`[]`＝legacy 整機全程；缺席/型別髒也回 ([],0)（勿防 absent、勿炸）
    ck(normalize_scope_events([]) == ([], 0), "SE5 `[]`＝legacy 整機全程（pin (a)）")
    for bad_in in (None, "x", 3, {}):
        ck(normalize_scope_events(bad_in) == ([], 0), f"SE6 非 list 輸入容忍: {bad_in!r}")
    # pin (c)：亂序 → 防禦性重排；同刻保留輸入序（穩定排序）
    ck(normalize_scope_events([se_raw[2], se_raw[0], se_raw[1]])[0] == se,
       "SE7 亂序輸入 → 升冪輸出（pin (c) 回填時戳重排）")
    same_ts = [{"ts": "2026-08-20T05:00:00Z", "scope": ["A"]},
               {"ts": "2026-08-20T05:00:00Z", "scope": ["B"]}]
    ck([e["scope"] for e in normalize_scope_events(same_ts)[0]] == [["A"], ["B"]],
       "SE8 同刻保留輸入序（穩定排序＝後到者定調由消費端處理）")
    # 空 scope＝bad data（cmms domain 硬驗證）——丟棄並計數，絕不當「全部恢復」
    ck(normalize_scope_events([{"ts": "2026-08-20T05:00:00Z", "scope": []}]) == ([], 1),
       "SE9 空 scope＝bad data → 丟棄＋計數 1（放過去＝製造幽靈全恢復事件）")
    ck(normalize_scope_events([{"ts": "2026-08-20T05:00:00Z", "scope": ["", "  "]}]) == ([], 1),
       "SE10 清洗後空 scope 同樣丟棄")
    mixed = [se_raw[0], {"ts": "bad", "scope": ["X"]}, {"ts": "2026-08-20T06:00:00Z"},
             "junk", {"ts": "2026-08-20T06:00:00Z", "scope": "EID-1"}]
    mse, mnd = normalize_scope_events(mixed)
    ck(len(mse) == 1 and mnd == 4, f"SE11 壞 ts／缺 scope／非 dict／scope 非 list 各丟一筆: {mnd}")
    # 成員保序去重、非 str 成員丟（不丟整筆）
    dedup = [{"ts": "2026-08-20T05:00:00Z", "scope": ["EID-B", "EID-A", "EID-B", 7, None]}]
    ck(normalize_scope_events(dedup)[0][0]["scope"] == ["EID-B", "EID-A"],
       "SE12 成員保序去重、非 str 成員丟棄（不丟整筆）")
    # naive 台北 datetime 直傳（demo／內部合成路徑）與 wire 形同義
    ck(normalize_scope_events([{"ts": D(2026, 8, 20, 10, 30), "scope": ["EID-24790"]}])[0]
       == [{"ts": D(2026, 8, 20, 10, 30), "scope": ["EID-24790"], "sub_units": []}],
       "SE13 naive datetime 直傳同義（demo 合成走同一支）")
    # ★ W4（`SPEC-CAPACITY-FORECAST.md` §2）：`sub_units` 帶出來、空與非空兩形
    su_raw = [{"ts": "2026-08-20T02:30:00Z", "scope": ["EID-26596"],
               "sub_units": ["nest:2", "nest:2", "", 7, "nest:4"]},
              {"ts": "2026-08-20T05:00:00Z", "scope": ["EID-26596"], "sub_units": []}]
    su, su_nd = normalize_scope_events(su_raw)
    ck(su_nd == 0 and [e["sub_units"] for e in su] == [["nest:2", "nest:4"], []],
       "SE14 sub_units 保序去重、非 str 丟成員；`[]` 照留（**不是** bad data、不丟整筆）")
    ck(normalize_scope_events([{"ts": "2026-08-20T05:00:00Z", "scope": ["E"],
                               "sub_units": "nest:2"}])[0][0]["sub_units"] == [],
       "SE15 sub_units 型別髒 → []＝整機（絕不炸、絕不丟整筆）")
    ck(effective_sub_units({"scope_events": su_raw}) == [],
       "SE16 effective＝最後一筆（折返整機 ⇒ []）")
    ck(effective_sub_units({"scope_events": su_raw}, at=D(2026, 8, 20, 12, 0))
       == ["nest:2", "nest:4"], "SE17 effective as-of 只看該時刻以前的快照")
    for empty in ({}, {"scope_events": []}, {"scope_events": None}, None, "x"):
        ck(effective_sub_units(empty) == [], f"SE18 無事件＝整機: {empty!r}")
    _old_seat_shape = {"asset_id": "EID-26718", "scope_events": [
        {"ts": D(2026, 8, 20, 10, 0), "scope": ["EID-26718"],
         "sub_units": ["nest:2"]}]}
    ck(effective_sub_units(_old_seat_shape) == ["nest:2"]
       and effective_sub_units(_old_seat_shape) == effective_seat_units(_old_seat_shape),
       "SE19 C-RW28 old sub_units form remains byte-identical through the thin wrapper")
    _note79_live_shape = {"asset_id": "EID-26718", "scope_events": [
        {"ts": D(2026, 8, 20, 10, 0), "scope": ["EID-26718-N2"], "sub_units": []}]}
    ck(effective_sub_units(_note79_live_shape) == ["nest:2"]
       and effective_seat_units(_note79_live_shape) == ["nest:2"],
       "SE20 C-RW28 NOTE-79 live scope seat ID reaches the shared effective answer")

    print(f"[board_w8] selftest PASS — {n_checked} 項斷言全過："
          f"normalize 白名單/per-person 丟棄/壞資料→None；"
          f"work_type 對映(PM/REACTIVE/CONVERSION/長尾→暫桶)；"
          f"活躍窗(首個有效COMPLETED/有效終態/遷移caveat/ongoing)；"
          f"髒日期防護(1900/未來/end<start)；UTC-Z/offset→台北對齊(S10 CLOSED)；"
          f"overlap 切分(部分重疊/全不明/多WO deterministic/dup防護/ongoing/鋪滿不變式)；"
          f"normalize_notes(缺→[]/UTC-Z→台北升冪/entry_type原樣/去source_actor/壞筆逐條丟)；"
          f"normalize_scope_events(A10 四 pin：[]＝legacy 整機/不進 _ALLOWED/亂序重排/"
          f"空 scope＝bad data 丟棄計數/UTC-Z→台北/成員保序去重)＋W4 sub_units 帶出"
          f"(欄位恆在/[]＝整機/型別髒不炸/effective as-of)＋C-RW28 NOTE-79 scope 座聯集；"
          f"status 欄透傳(strip+upper/空→None/非 str→None/未知值照存不驗 enum)＋"
          f"wo_is_cancelled(status 欄腿＝空 history 的 eMaint 殭屍型/history 末筆腿/"
          f"VOIDED/CLOSED 不算/末筆說了算/**絕不讀 closed_at**)；"
          f"verify 修正案 F1–F5/F7 全鎖；QG-3.4={fx_note}；QG-3.7={cg_note}。"
          f"純函式零 I/O（selftest 讀本地 fixture 除外）。")


# ---------------------------------------------------------------------------
# 契約沉默點清單（實作時被迫自選規則之處；待 §19 對版，對版後同步修訂）
# ---------------------------------------------------------------------------
# ★ 2026-07-04 三方峰會裁決回填（W1/CM-1；權威表＝remote-diag contracts/SUMMIT_INPUT.md 頂）：
#   S2/CM-1① CLOSED＝cmms 真形狀 {from_status,to_status,hold_reason,changed_at,source_actor}
#   → 對映 to_status/changed_at、hold_reason 保留（未知=「不知道」）、source_actor 治理丟棄；
#   掏空拒收（F1）獲追認。追認類（S1/S3/S4/S11 含 S2 拒收策略）照 Board 自選過。
#   S5/S12 登記 SME。S7/S10 已於 2026-07-05 A3 對版 CLOSED（見下）；cmms golden ×2 已收進
#   selftest（QG-3.7 節）。
# S1  多 WO 重疊：自選「活躍窗 start 較早者優先、tie-break wo_no 較小」——**峰會追認 ✓**。
# S2  條目形狀＝**CLOSED（CM-1 ①，cmms schemas.py:42-49 真形狀，欄名對映已實作）**。
# S3  「open」時刻＝opened_at 優先、缺則 history 首筆——**峰會追認 ✓**。
# S4  髒資料細則（start 髒=整窗丟/end 候選髒=跳過 F4/end<start=丟）——**峰會追認 ✓**。
# S5  CONVERSION→調機 confidence（現 medium）＝**登記 SME 級**（W1 裁決），暫維持。
# S6  暫桶字串＝已裁決非沉默（契約原文「其他維護(子類待定)」）；confidence=low 維持自選。
# S7  W8 schema_id＝**CLOSED（2026-07-05 A3 對版）**：detail=contract_wo_detail.v1、
#     LIST=contract_wo_active_in.v1；**契約級識別（fixture 檔名/cmms 契約測試載體）、非 in-band**
#     ——真回應 body 無 schema_id 欄；形狀 pin 由 golden fixture＋cmms 契約測試（形變即紅
#     ＝機械 push）承擔。破壞性變更必 bump v2、不原地覆寫。
# S8  殘餘不明段 source=None；is_canonical additive 欄（消費端內部設計，非跨 repo）。
# S9  ongoing 蓋到 gap 末端；半開 [start,end)——**峰會追認 ✓**。
# S10 **WO 時戳時鐘＝CLOSED（2026-07-05 A3 對版，§19.7）**：cmms＝UTC ISO-8601 'Z'
#     （LIST/detail 同序列化路徑、prod 實證；微秒有則保留整秒則無——兩種都容納）；
#     Board §14.2 對齊層 UTC→台北(+8) 轉 naive 後才疊 MES。_parse_dt/guard_ts 已落地轉換。
#     注意方向性：**cmms 入站 aware→轉台北**；C6 出站（Board 為 provider）維持 D1 pin
#     「naive 台北牆鐘」不變——兩者不衝突（邊界各自聲明）。
# S11 dup wo_no（一致去重、衝突全丟）——**峰會追認 ✓**。
# S12 ON_HOLD 窗語意＝**CLOSED（2026-07-07 Chris 裁決、落地 board_eqmeeting 分階段口徑）**：
#     WAITING_MACHINE_TIME/TEST_RUN＝uptime 不計停機（集合歸 eqmeeting.UPTIME_HOLD_REASONS）；
#     WAITING_PARTS/WAITING_VENDOR/OTHER/未知＝計停機（保守）。cmms 2026-07-07 明證該
#     兩值不計集正確且完整；hold_reason 詞彙+is_downtime 已登記為 Board 消費契約、增值必 push。
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="W8 停機融合契約消費端 stub（純函式；cmms API 未上線、現階段僅 --selftest）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（inline fixture、零 I/O）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    ap.error("cmms WO API 未上線（W8 go-live 前無傳輸層）；現階段只有 --selftest")


if __name__ == "__main__":
    main()
