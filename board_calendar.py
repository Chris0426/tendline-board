# -*- coding: utf-8 -*-
"""台灣工作日行事曆（#25，2026-08-18 Chris ask①）——`/equipment` heatmap 的非工作日淡染。

**這支只回答一個問題：某個日曆日是不是「非工作日」，如果是，因為什麼。**
它不碰任何量測、不進任何分母；純呈現層的背景說明。

━━ 三條鐵則（改這支前先讀）━━
① **週末是算出來的、假日是查出來的。**
   週六日由 `date.weekday()` 決定＝零猜測、永遠可用、跨年不必維護。
   國定假日**只能**來自 `tw_calendar.json`——碼裡永遠不寫死任何一個假日日期。
② **`verified=false` ⇒ 假日一律不吐**（只剩週末）。
   管理看板上標錯國定假日＝散布錯誤事實，比不標更糟。檔案沒被人核對過就當它不存在，
   這是安全預設、不是保守；要打開只需 Chris 核對後把旗標改 true（不必動碼）。
③ **補班日覆蓋週末。**（台灣特有：週六調整上班）漏掉補班日 ⇒ 那天被畫成休息，
   看板的人會把「有排班卻低產」誤讀成「本來就沒排」。故 workdays 的優先序**高於**週末。

★ 語意（別在別處誤用）：這是**背景說明**，不是「這天不該有生產」的斷言。SALTMARSH 週末排班是
  常態 ⇒ 被染色的欄位照樣可能有工單/停機/產出，那不是矛盾。A% 分母一個位元沒改。

★ 讀檔紀律：檔案讀**一次**進模組級快取（heatmap 每列每格都要問、不能每次開檔）；
  任何失敗（缺檔／壞 JSON／型別不對）⇒ **靜默退回「只有週末」**、絕不炸頁面——
  行事曆是加分項、不是承重件（同 `/equipment` MES「加分不是門檻」的既有紀律）。

用法：
    python board_calendar.py --selftest    # 離線零 I/O 自驗（注入 dict、不碰真檔）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                   # noqa: BLE001
        pass

CALENDAR_FILE = os.environ.get("BOARD_TW_CALENDAR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tw_calendar.json")

# 非工作日種類（模板據此分辨要不要顯名稱；週末沒有名稱可顯）
KIND_WEEKEND = "weekend"
KIND_HOLIDAY = "holiday"

_CAL = None          # (verified, {date_str: name}, {date_str}) —— 模組級快取（讀檔一次）


def _coerce(raw):
    """raw dict → `(verified, holidays, workdays)`；任何形狀不對的部分**丟掉那部分**、不炸。
       ★ 部分壞掉不毒化其餘：holidays 壞了不影響 workdays（反之亦然）——但 verified 不是
         True 就一律當 False（界線②：核對過才算數，含「檔案自己說不清楚」的情形）。"""
    if not isinstance(raw, dict):
        return (False, {}, set())
    verified = raw.get("verified") is True
    hol = {}
    for k, v in (raw.get("holidays") or {}).items() if isinstance(
            raw.get("holidays"), dict) else []:
        if _valid_date_str(k):
            hol[k] = str(v or "").strip() or None
    wd = {k for k in (raw.get("workdays") or [])
          if isinstance(raw.get("workdays"), list) and _valid_date_str(k)}
    return (verified, hol, wd)


def _valid_date_str(s):
    """只收 `YYYY-MM-DD` 的**合法日**（"2026-13-01"/"2026-2-3" 皆拒）——寬鬆解析會讓
       一個 typo 靜默變成某個真日期，那比整筆丟掉更難查。"""
    if not isinstance(s, str) or len(s) != 10:
        return False
    try:
        _dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def load_calendar(path=None, force=False):
    """讀行事曆 → `(verified, holidays, workdays)`。**恆不 raise**；失敗＝`(False, {}, set())`。"""
    global _CAL
    if _CAL is not None and not force and path is None:
        return _CAL
    try:
        with open(path or CALENDAR_FILE, encoding="utf-8") as f:
            out = _coerce(json.load(f))
    except Exception as e:                              # noqa: BLE001（行事曆是加分項）
        print(f"[board_calendar] 讀取失敗（{type(e).__name__}）→ 只染週末", flush=True)
        out = (False, {}, set())
    if path is None:
        _CAL = out
    return out


def coverage_gap(days, cal=None):
    """這批日子裡有哪些**年份沒有假日資料** → `[年, …]`（升冪）／`[]`＝都涵蓋到了。

    ★ 為什麼需要它（#30 的另一半）：行事曆是**逐年**資料。抓取排程萬一被移除、機器長期
      離線、或官方改了格式，頁面會在某年 1/1 悄悄只剩週末淡染——**而且不會有人被告知**。
      靜默降級正是本 repo 最不能接受的失敗形；這支讓 `/equipment` 講一句話。
    · `verified=False` ⇒ 回 `[]`：那是另一種狀態（「有資料但還沒核對」），已由 verified
      自己的語意管；在這裡再吼一次只會兩條訊息互相稀釋。
    · 判準＝該年**有沒有任何一筆 holidays**。台灣每年都有國定假日 ⇒ 一筆都沒有＝沒匯入。
    """
    verified, hol, _wd = cal if cal is not None else load_calendar()
    if not verified:
        return []
    have = {k[:4] for k in hol if isinstance(k, str) and len(k) == 10}
    want = set()
    for d in days or []:
        if isinstance(d, _dt.datetime):
            d = d.date()
        if isinstance(d, _dt.date):
            want.add(f"{d.year:04d}")
    return sorted(int(y) for y in (want - have))


def day_off(day, cal=None):
    """某日是否非工作日 → `(kind, label) | (None, None)`。

    · `cal`＝`(verified, holidays, workdays)`（測試注入用）；None＝走模組快取。
    · 優先序＝**補班日 > 假日 > 週末**（鐵則③：補班覆蓋週末；假日覆蓋週末＝同一天兩個理由取具名者）。
    · `verified` 為 False ⇒ holidays/workdays 整組不吃（鐵則②）⇒ 只剩週末判定。
    """
    if not isinstance(day, (_dt.date, _dt.datetime)):
        return (None, None)
    if isinstance(day, _dt.datetime):
        day = day.date()
    verified, hol, wd = cal if cal is not None else load_calendar()
    ds = day.isoformat()
    if verified:
        if ds in wd:
            return (None, None)                 # 補班日＝上班（即使是週六）
        if ds in hol:
            return (KIND_HOLIDAY, hol[ds])
    if day.weekday() >= 5:                      # 5=六 6=日
        return (KIND_WEEKEND, None)
    return (None, None)


# ---------------------------------------------------------------------------
# selftest（離線、零真檔 I/O：全部注入 cal tuple）
# ---------------------------------------------------------------------------

def _selftest():
    n_pass = n_fail = 0

    def ck(cond, msg):
        nonlocal n_pass, n_fail
        if cond:
            n_pass += 1
        else:
            n_fail += 1
            print(f"  FAIL: {msg}")

    D = _dt.date
    OFF = (True, {"2026-01-01": "元旦", "2026-02-16": "春節"}, {"2026-02-21"})
    UNV = (False, {"2026-01-01": "元旦"}, {"2026-02-21"})

    # ---- 週末＝算出來的（任何 cal 都成立）----
    ck(day_off(D(2026, 8, 15), OFF)[0] == KIND_WEEKEND, "週六＝weekend")
    ck(day_off(D(2026, 8, 16), OFF)[0] == KIND_WEEKEND, "週日＝weekend")
    ck(day_off(D(2026, 8, 14), OFF) == (None, None), "週五＝工作日")
    ck(day_off(D(2026, 8, 15), UNV)[0] == KIND_WEEKEND,
       "★ verified=False 仍照染週末（週末是算的、不是查的）")

    # ---- 假日（鐵則②：verified 才吃）----
    ck(day_off(D(2026, 1, 1), OFF) == (KIND_HOLIDAY, "元旦"), "平日假日＝holiday+名稱")
    ck(day_off(D(2026, 1, 1), UNV) == (None, None),
       "★ verified=False ⇒ 假日一律不吐（未經核對＝當它不存在，絕不上頁）")

    # ---- 補班日（鐵則③：覆蓋週末）----
    ck(D(2026, 2, 21).weekday() == 5, "2026-02-21 確實是週六（前提）")
    ck(day_off(D(2026, 2, 21), OFF) == (None, None),
       "★ 補班日覆蓋週末＝那天要上班、不得畫成休息")
    ck(day_off(D(2026, 2, 21), UNV)[0] == KIND_WEEKEND,
       "verified=False ⇒ 補班日也不吃 ⇒ 該週六退回一般週末（安全側：不宣稱任何未核對的事）")

    # ---- 假日落在週末：取具名者（同一天兩個理由）----
    ck(D(2026, 2, 16).weekday() == 0, "2026-02-16 是週一（前提）")
    _sat_hol = (True, {"2026-08-15": "測試假日"}, set())
    ck(day_off(D(2026, 8, 15), _sat_hol) == (KIND_HOLIDAY, "測試假日"),
       "假日落在週六 ⇒ 取 holiday（有名字的說法資訊量較高）")

    # ---- 髒資料一律不炸、且不靜默變成別的日期 ----
    ck(_coerce({"verified": True, "holidays": {"2026-13-01": "壞月份"}})[1] == {},
       "非法日期整筆丟掉（寬鬆解析會讓 typo 靜默變成真日期）")
    ck(_coerce({"verified": True, "holidays": {"2026-2-3": "缺零"}})[1] == {},
       "非 YYYY-MM-DD 嚴格拒收")
    ck(_coerce({"verified": "yes"})[0] is False,
       "verified 非 True 一律 False（含字串 'yes'）")
    ck(_coerce("nope") == (False, {}, set()), "整份非 dict → 全空、不炸")
    ck(_coerce({"verified": True, "holidays": "壞掉", "workdays": ["2026-02-21"]})[2]
       == {"2026-02-21"}, "holidays 壞掉不毒化 workdays")
    ck(day_off(None, OFF) == (None, None), "非日期型別 → (None,None)、不炸")
    ck(day_off(_dt.datetime(2026, 8, 15, 13, 0), OFF)[0] == KIND_WEEKEND,
       "吃 datetime（自動取 .date()）")

    # ---- coverage_gap（#30 靜默降級的守門）----
    CAL = (True, {"2026-01-01": "元旦"}, set())
    ck(coverage_gap([D(2026, 5, 1), D(2026, 12, 31)], CAL) == [], "涵蓋到的年份 ⇒ 無缺口")
    ck(coverage_gap([D(2026, 12, 30), D(2027, 1, 2)], CAL) == [2027],
       "★ 跨年窗踩到沒匯入的年份 ⇒ 報出來（否則 1/1 起靜默只剩週末）")
    ck(coverage_gap([D(2027, 3, 1), D(2028, 3, 1)], CAL) == [2027, 2028], "多年缺口全報、升冪")
    ck(coverage_gap([D(2027, 1, 1)], (False, {"2026-01-01": "x"}, set())) == [],
       "verified=False ⇒ 不報缺口（那是另一種狀態、兩條訊息互相稀釋）")
    ck(coverage_gap([], CAL) == [] and coverage_gap(None, CAL) == [], "空輸入不炸")

    # ---- 真檔可讀且形狀合法（內容由官方開放資料負責、見 core_calendar_fetch.py）----
    v, h, w = load_calendar(force=True)
    ck(isinstance(v, bool) and isinstance(h, dict) and isinstance(w, set),
       "真檔 load_calendar 回三元組")
    ck(coverage_gap([_dt.date.today()], (v, h, w)) == [],
       "★ 真檔涵蓋『今天』——紅了就是行事曆過期了（去看 TendlineBoardCalendarFetch）")

    print(f"board_calendar selftest: {n_pass} passed, {n_fail} failed")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    v, h, w = load_calendar(force=True)
    print(f"verified={v} holidays={len(h)} workdays={len(w)} file={CALENDAR_FILE}")
