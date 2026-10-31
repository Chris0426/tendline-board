# -*- coding: utf-8 -*-
"""
wsynth_clock.py — 合成產線的時鐘單一出處（SPEC-SYNTHLINE C-SY4；裁決 Q10／Q16／Q17）
====================================================================================

規則（違反即作廢）
  1. 正式運行：`now()` **就是** `datetime.datetime.now()`（台北牆鐘 naive）。沒有虛擬時鐘、
     沒有加速時鐘、沒有第二個游標（AP-6）。app 內數十處直呼 `datetime.now()`，兩者必須同源。
  2. 只有測試可經環境變數 `BOARD_SYNTH_NOW` 凍結（值＝台北 naive ISO，例
     `2026-09-29T14:30:00`）。凍結值解析失敗 ⇒ 大聲失敗（`ClockError`），不退回牆鐘。
  3. 啟動檢查：本機 UTC 偏移必須是 +8（台北）。Linux 主機必設 `TZ=Asia/Taipei`；
     偏移不符 ⇒ `ClockOffsetError`，呼叫端拒絕啟動。MES 語意＝台北牆鐘，UTC 主機會整體錯位 8h。
  4. 事件曝光規則＝`at <= now()`。本檔只提供時間，不做曝光判斷（那在 wsynth_world）。
  5. 產生器（wsynth_world）**永不**呼叫本檔的 `now()`；它只收呼叫端傳入的 `until`。

換算
  · 內部一律台北 naive `datetime`（秒精度）。
  · 種子帳 wire＝UTC ISO 帶 `Z`、秒精度（Q17）：`to_utc_z()`／`from_utc_z()`。
  · 種子檔日界＝台北 00:00（Q16）：`taipei_date()`。

純標準函式庫；零 I/O（只讀環境變數）。
"""
from __future__ import annotations

import datetime as _dt
import os

ENV_FREEZE = "BOARD_SYNTH_NOW"
TAIPEI_OFFSET = _dt.timedelta(hours=8)
_UTC_Z_FMT = "%Y-%m-%dT%H:%M:%SZ"


class ClockError(RuntimeError):
    """凍結值無法解析。"""


class ClockOffsetError(RuntimeError):
    """本機 UTC 偏移不是 +8。"""


def _parse_frozen(raw: str) -> _dt.datetime:
    s = raw.strip()
    if not s:
        raise ClockError("%s is set but empty" % ENV_FREEZE)
    if s.endswith(("Z", "z")) or "+" in s[10:] or (len(s) > 19 and "-" in s[19:]):
        raise ClockError("%s must be Taipei naive (no Z / offset): %r" % (ENV_FREEZE, raw))
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError as exc:
        raise ClockError("%s unparseable: %r" % (ENV_FREEZE, raw)) from exc
    if d.tzinfo is not None:
        raise ClockError("%s must be naive: %r" % (ENV_FREEZE, raw))
    return d.replace(microsecond=0)


def is_frozen(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return bool(env.get(ENV_FREEZE))


def now(environ=None) -> _dt.datetime:
    """台北牆鐘 naive。正式運行恆等於 `datetime.now()`；測試可經 `BOARD_SYNTH_NOW` 凍結。"""
    env = os.environ if environ is None else environ
    raw = env.get(ENV_FREEZE)
    if raw:
        return _parse_frozen(raw)
    return _dt.datetime.now()


def local_utc_offset() -> _dt.timedelta:
    """本機目前的 UTC 偏移（由 OS 時區設定決定）。"""
    off = _dt.datetime.now().astimezone().utcoffset()
    return off if off is not None else _dt.timedelta(0)


def check_utc_offset(offset=None) -> _dt.timedelta:
    """啟動檢查：偏移必須是 +8。`offset` 只供測試注入；正式運行傳 None。"""
    off = local_utc_offset() if offset is None else offset
    if off != TAIPEI_OFFSET:
        raise ClockOffsetError(
            "host UTC offset is %s, synthetic line requires +08:00 (set TZ=Asia/Taipei)" % (off,))
    return off


def floor_second(d: _dt.datetime) -> _dt.datetime:
    return d.replace(microsecond=0)


def to_utc_z(taipei_naive: _dt.datetime) -> str:
    """台北 naive → UTC ISO `Z`（秒精度）。aware 輸入拒絕。"""
    if not isinstance(taipei_naive, _dt.datetime) or taipei_naive.tzinfo is not None:
        raise ValueError("to_utc_z expects a naive Taipei datetime: %r" % (taipei_naive,))
    return (taipei_naive.replace(microsecond=0) - TAIPEI_OFFSET).strftime(_UTC_Z_FMT)


def from_utc_z(s: str) -> _dt.datetime:
    """UTC ISO `Z`（秒精度）→ 台北 naive。其他形一律 ValueError。"""
    if not isinstance(s, str):
        raise ValueError("from_utc_z expects str: %r" % (s,))
    d = _dt.datetime.strptime(s, _UTC_Z_FMT)
    return d + TAIPEI_OFFSET


def is_utc_z(s) -> bool:
    try:
        from_utc_z(s)
        return True
    except (ValueError, TypeError):
        return False


def taipei_date(d: _dt.datetime) -> _dt.date:
    """台北日曆日（種子檔日界＝台北 00:00，Q16）。"""
    return d.date()


def day_start(day: _dt.date) -> _dt.datetime:
    return _dt.datetime(day.year, day.month, day.day)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv[1:]:
        import wsynth_selftest
        sys.exit(wsynth_selftest.main(["--only", "clock"]))
    print(now().isoformat(sep=" "), "offset", local_utc_offset())
