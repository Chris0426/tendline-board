#!/usr/bin/env python3
"""board_envsensor.py — iMonnit 溫溼度感測（`SPEC-ENV-SENSOR.md` C-ES1–C-ES6）。

定位
----
**只顯示＋一鍵跳轉**：把 iMonnit 已經在監測的溫溼度值疊成 landing layout 上的一枚貼紙。
Board **不重做警報、不存歷史、不發明門檻**——警報與歷史都在 iMonnit，這裡只轉述「此刻」
再給一條連到那顆 sensor 自己的監控頁的連結（C-ES5）。

本檔的鐵則（違反即錯）
--------------------
- **純函式為主、一支取數函式**：除了 `fetch_sensor_list()` 之外，本檔任何函式都不碰網路、
  不碰檔案、不看時鐘以外的環境（`now` 一律由呼叫端傳）。
- **零 MES／零 cmms／零新分母**（C-ES1）：sensor 本體的 `EID-29xxx` 永遠不進 `EQMT_CATALOG`，
  這裡也不認得任何 MES 概念。對照表是**人工**的（C-ES6），名稱只用來起草、不當程式判準。
- **金鑰只在 `fetch_sensor_list()` 的 header 裡出現一次**（C-ES4）：不落任何回傳值、
  不進例外訊息、不進 `repr`。本檔沒有任何 `print(headers)` 類的東西，也不准加。
- **序列、單執行緒、10 分鐘一次**：iMonnit 明文限流「one API call at a time」（§2 逐字引文）。
  本檔一次呼叫拿回全部 sensor（`SensorList`），**永遠不逐顆打**。

⚠ **base URL 待第一次真呼叫確認**（`SPEC-ENV-SENSOR.md` §2 末段）——文件頁的 Request URL
  欄要按「Try this API」才會填，勘查時依鐵則沒按 ⇒ 這裡先照 iMonnit 慣例寫死一處常數，
  第一次真呼叫回 404／405 時**只改這一行**並回填 SPEC §2。
"""
import json
import re
import sys

__all__ = [
    "IMONNIT_SENSOR_LIST_URL", "SENSOR_CHART_BASE", "DEFAULT_TIMEOUT_S",
    "STALE_INTERVAL_MULT", "STALE_FALLBACK_S", "SCHEMA_ID",
    "chart_url", "norm_key", "short_reading", "parse_last_comm", "sensor_flags",
    "load_map", "confirmed_sensors", "match_machines", "join_rows", "count_alerts",
    "fetch_sensor_list",
]

# ---------------------------------------------------------------------------
# 常數（**一個出處**；改這裡就好）
# ---------------------------------------------------------------------------
# ⚠ 待第一次真呼叫確認（`SPEC-ENV-SENSOR.md` §2）：iMonnit 文件頁沒有印出 Request URL，
#   這是照該站慣例推的形。第一次真呼叫若回 404／405，**只改這一行**、順手回填 SPEC §2。
IMONNIT_SENSOR_LIST_URL = "https://www.imonnit.com/json/SensorList/"
# 單顆監控頁（sonnet 實點確認過的路徑；C-ES5 一律新分頁、Board 不代理不 iframe）
SENSOR_CHART_BASE = "https://www.imonnit.com/Overview/SensorChart/"
DEFAULT_TIMEOUT_S = 15.0
# C-ES3：通訊過期＝距今 > 3×ReportInterval；`SensorList` 沒帶 interval 時退回 2 小時。
STALE_INTERVAL_MULT = 3
STALE_FALLBACK_S = 7200.0
SCHEMA_ID = "board.envmap.v1"

# `SensorList` 回傳欄位（§2 逐字）。這裡只列**本檔真的讀**的那幾個，其餘欄位一律原樣忽略
# ——多一個欄位不該讓整組壞掉（iMonnit 隨時可能加欄）。
F_ID = "SensorID"
F_NAME = "SensorName"
F_LAST = "LastCommunicationDate"
F_STATUS = "Status"
F_READING = "CurrentReading"
# ⚠⚠ `AlertsActive` **不是旗標**（`SPEC-ENV-SENSOR.md` §7，2026-09-06 第一次真呼叫）：
#     實測**全部感測器** `True` ＝「這顆有設警報規則」而不是「它正在警報」。原稿拿它當琥珀
#     判準 ⇒ live 一次畫出整排全琥珀。留這個常數只為了**讀得到、印得出來**，
#     `sensor_flags` 一個位元都不看它。改回去之前先讀 §7。
F_ALERTS = "AlertsActive"
# ⚠ `ReportInterval` **`SensorList` 不回**（§7：`SensorListFull` 才有）⇒ 實務上過期判準
#   **永遠**走 2 小時退路。讀得到就用是為了將來換端點時不必再改一次判準。
F_INTERVAL = "ReportInterval"


def chart_url(sensor_id):
    """sensorID → iMonnit 單顆監控頁（C-ES5）。"""
    return SENSOR_CHART_BASE + str(sensor_id)


def norm_key(text):
    """名稱正規化：小寫、只留英數。

       站名是**人手打的自由文字**（`@`／`-`／`in`／括號四種寫法並存、大小寫不一、
       `FlexBonding` 與 `Flex bonder#1` 同一件事）⇒ 比對前一律先過這一支。
       ⚠ 這是**顯示層的對照輔助**，不是判準：`confirmed` 仍由人翻（C-ES6）。"""
    return re.sub(r"[^0-9a-z]+", "", str(text or "").lower())


_READING_PCT = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_READING_DEG = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°|deg)", re.I)


def parse_reading(raw):
    """`CurrentReading` 原字串 → `{"rh": float|None, "temp_c": float|None}`。**純函式**。

       例：`'52.17% @ 22.4° C'` ⇒ `{"rh": 52.17, "temp_c": 22.4}`。
       ★ §8 起這一支有**兩個**消費者（貼紙短形與超標判定）⇒ 抽成一支，
         regex 只有一份：兩邊對「這個字串裡的數字是多少」永遠不可能有兩種答案。
       解析不到的那一側回 `None`（＝**不知道**）⇒ 判定端據此永不 `over`。"""
    text = str(raw or "").strip()
    pct = _READING_PCT.search(text)
    deg = _READING_DEG.search(text)
    def _f(m):
        try:
            return float(m.group(1))
        except (TypeError, ValueError):                      # pragma: no cover - regex 已限定
            return None
    return {"rh": _f(pct) if pct else None,
            "temp_c": _f(deg) if deg else None}


# ── §8 規則 ID 索引（**不是門檻的第二份**）────────────────────────────────────
#   門檻的唯一出處仍是 `_ENV-SENSOR-MAP.json` 的 `limits`；這張表只回答
#   「map 給的這個界線，在 iMonnit 上是哪一條規則的號碼」，用來把規則 ID 印進 tooltip。
#   出處＝`SPEC-ENV-SENSOR.md` §8 的規則表（sonnet 唯讀抄 `/Rule/Index`）。
#   ⚠ **查不到就不印號碼**（句子照印）——寧可少一個號碼，也不要猜一個號碼；
#     而且只有當那個號碼**也列在該顆的 `limits.rules`** 裡才印（兩邊對得起來才算數）。
RULE_IDS = {
    ("rh", "hi", 58.0): 211600,
    ("rh", "lo", 42.0): 212387,
    ("rh", "hi", 60.0): 328397,
    ("rh", "lo", 40.0): 363329,
    ("rh", "hi", 65.0): 661389,
    ("temp_c", "hi", 25.0): 232247,
}


def limit_breaches(reading, limits):
    """讀值 × `limits` → 越線清單（**純函式、零 I/O、零文案**）。

       `limits`＝`{"rh":[lo,hi], "temp_c":[lo,hi], "rules":[…]}`，`null`／缺鍵＝**該側無規則**。
       回 `[{side, value, bound, dir, rule}]`（`dir` ∈ `hi`／`lo`；`rule` 查不到就是 `None`）。
       ★ **判定權仍是 iMonnit 的**（C-ES3 精神未變）：門檻是抄來的，讀值是 iMonnit 回的，
         Board 只做「大於／小於」這一個算術。缺 `limits`、缺讀值 ⇒ 回空列 ⇒ 永不 `over`。
       ★ 邊界＝**嚴格**大於／小於（規則本身寫的就是 `Greater Than 58` ⇒ 58.0 不算越線）。"""
    out = []
    if not isinstance(limits, dict):
        return out
    rules = {int(r) for r in (limits.get("rules") or ()) if isinstance(r, (int, float))}
    for side in ("rh", "temp_c"):
        value = (reading or {}).get(side)
        if value is None:
            continue
        pair = limits.get(side)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        lo, hi = pair
        for bound, direction, hit in ((lo, "lo", lambda v, b: v < b),
                                      (hi, "hi", lambda v, b: v > b)):
            if bound is None:
                continue
            try:
                bound_f = float(bound)
            except (TypeError, ValueError):
                continue
            if not hit(float(value), bound_f):
                continue
            rule = RULE_IDS.get((side, direction, bound_f))
            out.append({"side": side, "value": float(value), "bound": bound_f,
                        "dir": direction, "rule": rule if rule in rules else None})
    return out


def short_reading(raw):
    """`CurrentReading` 原字串 → 貼紙上的短形。

       例：`'52.17% @ 22.4° C'` → `'52% 22°'`。**解析只為了縮短**（著色走 `sensor_flags`）
       ⇒ 解析不出來一點都不嚴重：退回原字串前 8 字，
       完整原文仍在 `title` 裡（`join_rows` 的 `reading_raw`）。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    got = parse_reading(s)
    parts = []
    if got["rh"] is not None:
        parts.append("{:.0f}%".format(got["rh"]))
    if got["temp_c"] is not None:
        parts.append("{:.0f}°".format(got["temp_c"]))
    if parts:
        return " ".join(parts)
    return s[:8]


_MS_DATE = re.compile(r"/Date\((-?\d+)")


def parse_last_comm(value, tz=None):
    """`LastCommunicationDate` → aware `datetime`（UTC）；認不得就回 `None`。

       ★ **`SensorList` 實際回的是 ASP.NET `/Date(ms)/`**（§7 第一次真呼叫）＝ epoch 毫秒、
         UTC，**零時區歧義**——原本「無時區字串當 UTC」那個假設在這個端點上根本用不到。
         另外兩種形（ISO、`'YYYY-MM-DD HH:MM:SS'`）留著認，因為 iMonnit 別的端點回過。
       **認不得不是錯**：呼叫端把 `None` 當成「不知道最後通訊時間」⇒ 不判過期
       （C-ES3 只轉述、不發明），`title` 仍印原字串。
       ⚠ 停用的 sensor 會回 `/Date(-62135596800000)/`（.NET `DateTime.MinValue`）
         ⇒ 溢位／超出平台範圍，這裡回 `None`＝「不知道」，不是 1 年 1 月 1 日。"""
    import datetime as _dt
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    m = _MS_DATE.search(s)
    if m:
        try:
            return _dt.datetime.fromtimestamp(int(m.group(1)) / 1000.0, _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    txt = s.replace("Z", "+00:00").replace("T", " ")
    txt = re.sub(r"\.\d+", "", txt)
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            out = _dt.datetime.strptime(txt, fmt)
        except ValueError:
            continue
        return out if out.tzinfo else out.replace(tzinfo=_dt.timezone.utc)
    return None


def _as_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def sensor_flags(sensor, now=None, limits=None):
    """C-ES3 三旗標（**門檻一個都不是 Board 發明的**）＋顯示桶。

       · `alert`   ＝ **`Status != 0`**（iMonnit 自己的狀態碼；`title` 印碼）
       · `stale`   ＝ `LastCommunicationDate` 距今 > 3×`ReportInterval`（`SensorList` 不回
                     interval ⇒ 實務上恆為 2h 退路，§7）
       · `offline` ＝ `Status` 的**字面**含 OFFLINE（`SensorList` 回的是數字碼 ⇒ 這條在本端點
                     不會觸發；留著是因為 iMonnit 別的端點回過字串，而多認一種形不花錢）

       ★ **`AlertsActive` 不看**（§7）：全部感測器皆 `True` ＝規則已啟用，不是正在警報。
         那是 09-06 第一次真呼叫才知道的事，原稿拿它當旗標 ⇒ live 整排全琥珀。

       · `over`    ＝ **§8**：讀值越過 `limits`（`_ENV-SENSOR-MAP.json` 逐字抄的 iMonnit
                     帳號層 Rules）任一側。`limits` 不給／讀值解析不到 ⇒ **永遠 False**。

       `tone` 是**顯示桶**，四選一：`ok`／`alert`／`over`／`unknown`。
       ⚠ **優先序＝`unknown` ＞ `over` ＞ `alert` ＞ `ok`**（§8）：
         · 連不上／過期的讀值**不准畫紅**——那個數字是它最後一次講話時的事，
           拿去跟門檻比就是拿舊聞當現況（C-LD2「不知道 ≠ 壞了」）；
         · 超標（紅）比狀態碼（琥珀）急：`Status≠0` 在 §7 實測全是「沒通訊」那一類。
         四個旗標本身照樣全部回傳、`title` 一句都不吞。"""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    raw_status = (sensor or {}).get(F_STATUS)
    # ⚠ `0` 是**合法且最常見**的狀態碼 ⇒ 不能用 `or ""` 把它吃掉（那會讓狀態正常的機台
    #   在 `title` 裡印成「—」）。只有 `None`／空字串才是「沒有這個欄位」。
    status = "" if raw_status is None else str(raw_status)
    code = _as_int(raw_status, default=0)
    alert = code != 0
    offline = "offline" in status.lower()
    last = parse_last_comm((sensor or {}).get(F_LAST))
    interval_s = _as_int((sensor or {}).get(F_INTERVAL), 0) * 60.0
    limit = (STALE_INTERVAL_MULT * interval_s) if interval_s > 0 else STALE_FALLBACK_S
    age_s = None
    if last is not None:
        age_s = max(0.0, (now - last).total_seconds())
    stale = bool(age_s is not None and age_s > limit)
    breaches = limit_breaches(parse_reading((sensor or {}).get(F_READING)), limits)
    over = bool(breaches)
    if offline or stale:
        tone = "unknown"
    elif over:
        tone = "over"
    elif alert:
        tone = "alert"
    else:
        tone = "ok"
    return {"alert": alert, "offline": offline, "stale": stale, "over": over,
            "breaches": breaches, "tone": tone,
            "status": status, "code": code, "last": last, "age_s": age_s,
            "limit_s": limit, "alerts_active": bool((sensor or {}).get(F_ALERTS))}


# ---------------------------------------------------------------------------
# 對照表（`_ENV-SENSOR-MAP.json`；C-ES6 人工一份、進版控）
# ---------------------------------------------------------------------------
def load_map(path):
    """讀 `_ENV-SENSOR-MAP.json`。缺檔／壞檔／`schema_id` 不對 ⇒ 回空表（功能靜默關）。

       ⚠ 這是**唯一**會碰檔案的讀取路徑，而且只在暖機／渲染時讀一次由呼叫端快取；
         本檔不自己記憶化（快取策略屬於 app 層，寫兩份就是下一次會漂的事實）。"""
    try:
        doc = json.loads(open(str(path), encoding="utf-8").read())
    except Exception:                                          # noqa: BLE001
        return {"schema_id": "", "sensors": []}
    if not isinstance(doc, dict) or doc.get("schema_id") != SCHEMA_ID:
        return {"schema_id": "", "sensors": []}
    sensors = [s for s in (doc.get("sensors") or []) if isinstance(s, dict)]
    return {"schema_id": doc.get("schema_id"), "source": doc.get("source") or "",
            "sensors": sensors}


def confirmed_sensors(map_doc):
    """`confirmed:true` 且有 anchor 的那幾顆（C-ES6：Chris 沒翻 true 的一律不畫）。"""
    out = []
    for s in (map_doc or {}).get("sensors") or ():
        if not s.get("confirmed"):
            continue
        if not isinstance(s.get("anchor"), dict) or not s["anchor"]:
            continue
        out.append(s)
    return out


def match_machines(anchor, catalog, cls_names=None):
    """anchor → `(eids, reason)`；**對不到就對不到**，永不猜一台。

       `catalog`＝`[(area, name, eid), …]`（`board_eqmeeting.EQMT_CATALOG` 的形）；
       `cls_names`＝`{eid: 站別顯示名}`（離線只認得幾台＝`app._ld_station_map()`，
       認不得的那幾台就只靠機台名比對）。

       三形（`SPEC-ENV-SENSOR.md` §3）：
         · `{"eid": …}`      ⇒ 名冊裡有這台就是它；沒有 ⇒ `eid_not_in_catalog`
         · `{"cls_hint": …}` ⇒ 正規化子字串比對（機台名 **或** 站別顯示名）＋ `zone` 過濾
                               ＋ 選配 `instance_hint`（Plover／Kestrel 之類的實例字樣）
         · `{"zone": …}`     ⇒ 不對機台，貼在區標旁（由呼叫端處理）

       `reason` 非空＝**這一顆畫不出來**，呼叫端要把它列進揭露清單（不是靜靜消失）。
       ⚠ `zone_mismatch` 是**最有用的一種**：名字對得到、但那些機台不在對照表寫的區
         ⇒ 對照表與名冊有一個要改，人來決定（碼不准自己放寬）。"""
    anchor = anchor or {}
    cat = [(str(a or ""), str(n or ""), str(e or "")) for a, n, e in (catalog or ())]
    if anchor.get("eid"):
        eid = str(anchor["eid"])
        return ([eid], "") if any(e == eid for _a, _n, e in cat) else ([], "eid_not_in_catalog")
    if anchor.get("cls_hint"):
        hint = norm_key(anchor["cls_hint"])
        names = cls_names or {}
        pool = [(a, n, e) for a, n, e in cat
                if hint and (hint in norm_key(n) or hint in norm_key(names.get(e, "")))]
        if not pool:
            return [], "no_name_match"
        zone = str(anchor.get("zone") or "")
        if zone:
            in_zone = [x for x in pool if x[0] == zone]
            if not in_zone:
                seen = sorted({a for a, _n, _e in pool})
                return [], "zone_mismatch:" + ",".join(seen)
            pool = in_zone
        inst = norm_key(anchor.get("instance_hint") or "")
        if inst:
            narrowed = [x for x in pool if inst in norm_key(x[1])]
            if not narrowed:
                return [], "instance_no_match"
            pool = narrowed
        return [e for _a, _n, e in pool], ""
    if anchor.get("zone"):
        return [], "zone_anchor"
    return [], "anchor_unknown"


def join_rows(map_doc, sensors, now=None, catalog=None, cls_names=None):
    """對照表 × `SensorList` 回傳 → 每顆一筆「畫得出來的事實」＋一份揭露清單。

       回 `(rows, unmatched)`：
         · `rows`      ＝ confirmed 且對得到 anchor 的（含**沒有讀值**的那幾顆——
                          C-ES3「照誠實顯示」，缺讀值畫空心、不是不畫）
         · `unmatched` ＝ `[{sensor_id, name, reason}]`，呼叫端負責讓它被看見
       ⚠ 本函式**不排版、不著色成 CSS class**：那是 app 的事。這裡只回事實。"""
    by_id = {}
    for s in sensors or ():
        if isinstance(s, dict) and s.get(F_ID) is not None:
            by_id[str(s.get(F_ID))] = s
    rows, unmatched = [], []
    for rec in confirmed_sensors(map_doc):
        sid = str(rec.get("sensor_id"))
        eids, reason = match_machines(rec.get("anchor"), catalog or (), cls_names)
        if reason and reason != "zone_anchor":
            unmatched.append({"sensor_id": rec.get("sensor_id"),
                              "name": rec.get("name") or "", "reason": reason})
            continue
        live = by_id.get(sid)
        # §8：門檻跟著這顆 sensor 的 map 紀錄走（`limits` 缺席＝該顆沒有規則 ⇒ 永不 over）。
        flags = sensor_flags(live or {}, now, rec.get("limits")) if live else {
            "alert": False, "offline": False, "stale": False, "over": False,
            "breaches": [], "tone": "unknown",
            "status": "", "code": 0, "last": None, "age_s": None,
            "limit_s": STALE_FALLBACK_S, "alerts_active": False}
        raw = str((live or {}).get(F_READING) or "")
        rows.append({
            "sensor_id": rec.get("sensor_id"),
            "name": str((live or {}).get(F_NAME) or rec.get("name") or ""),
            "label": rec.get("label") or "",
            "anchor": rec.get("anchor") or {},
            "eids": eids,
            "url": rec.get("url") or chart_url(rec.get("sensor_id")),
            "reading_raw": raw,
            "reading": short_reading(raw),
            "have_reading": bool(live is not None and raw),
            "in_payload": live is not None,
            "flags": flags,
            "tone": flags["tone"],
            # §8：越線清單是**結構化事實**，句子由 app 用 i18n 組（本模組不排版不翻譯）。
            "breaches": flags.get("breaches") or [],
            "limits": rec.get("limits") or None,
            "last_raw": str((live or {}).get(F_LAST) or ""),
            # ★ §7：真回傳是 `/Date(1767345240000)/` ⇒ **原字串印在 tooltip 裡是給機器看的**。
            #   解得開就印解出來的 UTC 時刻，解不開才退回原字串（誠實：不假裝解得開）。
            "last_txt": (flags["last"].strftime("%Y-%m-%d %H:%M UTC")
                         if flags.get("last") is not None
                         else str((live or {}).get(F_LAST) or "")),
            "status": flags["status"],
        })
    return rows, unmatched


def count_alerts(rows):
    """圖例 chip 的兩個數：`(N 顯示中, k 非正常)`。純計數、零判斷。"""
    rows = list(rows or ())
    return len(rows), sum(1 for r in rows if (r.get("tone") or "ok") != "ok")


def count_over(rows):
    """§8：`over` 的顆數（圖例 chip 換文案的門檻＝**例外才出現**）。純計數。"""
    return sum(1 for r in (rows or ()) if (r.get("tone") or "ok") == "over")


# ---------------------------------------------------------------------------
# 唯一的取數函式（**只准在背景執行緒裡呼叫**；C-ES2 請求路徑零外呼）
# ---------------------------------------------------------------------------
def fetch_sensor_list(key_id, secret, url=None, timeout=DEFAULT_TIMEOUT_S, opener=None):
    """一次呼叫拿回全部 sensor。回 `(sensors, err)`——**永不 raise**。

       · 認證＝兩個 header（§2 逐字：「The APIKeyID and the APISecretKey both need to be
         included as headers」）。⚠ 金鑰只在這裡出現，**不進回傳值、不進 `err`、不進 log**（C-ES4）。
       · 限流＝序列、一次一支（§2）；節奏由呼叫端的 10 分鐘迴圈負責，本函式不 sleep、不重試
         ——失敗就回錯誤字串，下一輪再說（重試＝把一次呼叫變兩次，正是限流最恨的形）。
       · `err` 是**分類字串**（`http:404`／`urlerror`／`badjson`／`shape`），不是例外原文
         ⇒ 不可能夾帶到 URL 裡的任何憑證。"""
    if not key_id or not secret:
        return [], "nokey"
    ctype = ""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        str(url or IMONNIT_SENSOR_LIST_URL),
        headers={"APIKeyID": str(key_id), "APISecretKey": str(secret),
                 "Accept": "application/json"})
    try:
        _open = opener or urllib.request.urlopen
        with _open(req, timeout=float(timeout)) as resp:
            body = resp.read()
            # 字集由回應自己說了算（§7）；拿不到 header 的樁物件 ⇒ 空字串 ⇒ 退 UTF-8。
            try:
                ctype = resp.headers.get("Content-Type", "")
            except Exception:                                  # noqa: BLE001
                ctype = ""
    except urllib.error.HTTPError as e:
        return [], "http:" + str(getattr(e, "code", "?"))
    except urllib.error.URLError:
        return [], "urlerror"
    except Exception as e:                                     # noqa: BLE001
        return [], "error:" + type(e).__name__
    try:
        doc = json.loads(_decode_body(body, ctype))
    except Exception:                                          # noqa: BLE001
        return [], "badjson"
    return _sensors_from(doc)


def _decode_body(body, content_type=""):
    """回應位元組 → 字串：**先看 `Content-Type` 的 charset、沒有就 UTF-8**，一律 `replace`。

       ⚠ 這一支是 09-06 第一次真呼叫買來的（§7）：`CurrentReading` 的形是
         `52.12% @ 22.5° C`，度號是 **U+00B0**；用錯字集解出來就是一枚 `�` 貼在圖上
         ——而它**不會報錯**，只會安靜地變成一個看不懂的字。
       `errors="replace"` 是刻意的：一個解不開的位元組不該讓整組感測器消失。"""
    charset = ""
    for part in str(content_type or "").split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip("\"'").strip()
    if isinstance(body, str):
        return body
    for enc in ([charset] if charset else []) + ["utf-8"]:
        try:
            return body.decode(enc, "replace")
        except (LookupError, TypeError):
            continue                                           # 認不得的 charset ⇒ 退 UTF-8
    return body.decode("utf-8", "replace")


def _sensors_from(doc):
    """回傳體 → sensor list。iMonnit 有回過裸 list 也回過 `{"Result": […]}` ⇒ 兩種都認。"""
    if isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict):
        items = None
        for key in ("Result", "result", "Data", "data", "SensorList", "Sensors"):
            if isinstance(doc.get(key), list):
                items = doc[key]
                break
        if items is None:
            return [], "shape"
    else:
        return [], "shape"
    out = [s for s in items if isinstance(s, dict) and s.get(F_ID) is not None]
    return out, ("" if out else "empty")


# ---------------------------------------------------------------------------
# 自驗（`python board_envsensor.py --selftest`；零網路、零檔案）
# ---------------------------------------------------------------------------
def _selftest():
    import datetime as _dt
    ok = fail = 0

    def chk(label, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print("PASS  " + label)
        else:
            fail += 1
            print("FAIL  " + label + ("  " + str(detail) if detail else ""))

    now = _dt.datetime(2026, 9, 6, 0, 0, tzinfo=_dt.timezone.utc)

    # ---- 縮讀值（§4）----
    chk("short_reading trims the iMonnit string to the sticker form",
        short_reading("52.17% @ 22.4° C") == "52% 22°",
        short_reading("52.17% @ 22.4° C"))
    chk("short_reading keeps a humidity-only string readable",
        short_reading("48.9%") == "49%", short_reading("48.9%"))
    chk("short_reading falls back to the first 8 characters, never to a guess",
        short_reading("no reading available") == "no readi")
    chk("short_reading is empty for an empty reading (a sticker with no value)",
        short_reading(None) == "" and short_reading("") == "")

    # ---- 兩旗標（C-ES3；★ 09-06 §7 真呼叫後改寫）----
    # 真資料的形：`Status` 是**數字碼**（0×22／2×2／5×2）、`AlertsActive` **全 True**、
    # `SensorList` 不回 `ReportInterval` ⇒ 過期恆走 2h 退路。
    fresh = {F_ID: 1, F_STATUS: 0, F_ALERTS: True, F_INTERVAL: 10,
             F_LAST: "2026-09-05 23:55:00"}
    chk("Status 0 with a recent report is plain normal",
        sensor_flags(fresh, now)["tone"] == "ok")
    chk("★ AlertsActive is NOT a flag: all 26 live sensors carry True (a rule exists), so a "
        "Status-0 sensor stays normal however loudly AlertsActive shouts",
        sensor_flags(dict(fresh, **{F_ALERTS: True}), now)["tone"] == "ok"
        and sensor_flags(dict(fresh, **{F_ALERTS: 9}), now)["tone"] == "ok"
        and sensor_flags(dict(fresh, **{F_ALERTS: True}), now)["alerts_active"] is True,
        sensor_flags(dict(fresh, **{F_ALERTS: True}), now))
    chk("★ any non-zero iMonnit Status code is amber - the code is transcribed, never "
        "reinterpreted (2 and 5 are the two the fleet actually returns)",
        sensor_flags(dict(fresh, **{F_STATUS: 2}), now)["tone"] == "alert"
        and sensor_flags(dict(fresh, **{F_STATUS: 5}), now)["tone"] == "alert"
        and sensor_flags(dict(fresh, **{F_STATUS: 5}), now)["code"] == 5)
    chk("Status 0 is a real code, not a missing field: it survives into the title as '0' "
        "instead of collapsing to a dash",
        sensor_flags(fresh, now)["status"] == "0"
        and sensor_flags({F_ID: 9}, now)["status"] == "")
    chk("a textual OFFLINE status is still recognised (another endpoint returns words), and "
        "hollow beats amber there",
        sensor_flags(dict(fresh, **{F_STATUS: "Offline"}), now)["tone"] == "unknown")
    stale = dict(fresh, **{F_LAST: "2026-09-05 20:00:00"})       # 4h > 3x10min
    chk("communication older than 3x ReportInterval is stale (hollow), by the interval "
        "the sensor itself reports",
        sensor_flags(stale, now)["stale"] and sensor_flags(stale, now)["tone"] == "unknown")
    nointv = {F_ID: 2, F_STATUS: 0, F_ALERTS: True, F_LAST: "2026-09-05 21:30:00"}
    chk("with no ReportInterval - which is what SensorList actually returns - the fallback "
        "window is exactly 2 hours",
        sensor_flags(nointv, now)["stale"] is True
        and sensor_flags(nointv, now)["limit_s"] == STALE_FALLBACK_S)
    chk("2h minus a minute is not yet stale under the fallback window",
        sensor_flags(dict(nointv, **{F_LAST: "2026-09-05 22:01:00"}), now)["stale"] is False)
    chk("a silent sensor whose status code is non-zero stays hollow: that code is from the "
        "last time it spoke, so amber would claim a live alarm",
        sensor_flags(dict(fresh, **{F_STATUS: 5, F_LAST: "2026-01-02 09:14:00"}),
                     now)["tone"] == "unknown"
        and sensor_flags(dict(fresh, **{F_STATUS: 5, F_LAST: "2026-01-02 09:14:00"}),
                         now)["alert"] is True)
    chk("an unparsable LastCommunicationDate is not evidence of staleness",
        sensor_flags({F_ID: 3, F_LAST: "who knows", F_STATUS: 0}, now)["stale"] is False)
    chk("a disabled sensor's .NET DateTime.MinValue is 'unknown', not year 1",
        parse_last_comm("/Date(-62135596800000)/") is None)

    # ---- 日期三形 ----
    chk("LastCommunicationDate parses the three shapes iMonnit has returned",
        parse_last_comm("2026-09-05T23:55:00Z") is not None
        and parse_last_comm("2026-09-05 23:55:00") is not None
        and parse_last_comm("/Date(1788652500000)/") is not None
        and parse_last_comm("nonsense") is None)

    # ---- 跳轉 URL（C-ES5）----
    chk("the jump URL is iMonnit's own sensor chart page",
        chart_url(547114) == "https://www.imonnit.com/Overview/SensorChart/547114")

    # ---- 對照表（C-ES6）----
    cat = [("za", "Die Placement Cure Oven", "EID-25335"),
           ("za", "Die Placement Cure Oven", "EID-25366"),
           ("za", "Frame#2", "EID-12724"),
           ("za", "Frame#3 Kestrel", "EID-13219"),
           ("zb", "Flex bonder#1", "EID-00643"),
           ("zc", "CAL#4", "EID-16821")]
    cls_names = {"EID-12724": "FrameBonding", "EID-13219": "FrameBonding",
                 "EID-00643": "Flex Bonding"}
    chk("an eid anchor resolves to that machine and to nothing else",
        match_machines({"eid": "EID-16821"}, cat) == (["EID-16821"], ""))
    chk("an eid anchor outside the roster is disclosed, never silently dropped",
        match_machines({"eid": "EID-99999"}, cat) == ([], "eid_not_in_catalog"))
    chk("a cls_hint matches on the machine name, normalised (case, spaces and '#' free)",
        match_machines({"cls_hint": "Die Placement", "zone": "za"}, cat)[0]
        == ["EID-25335", "EID-25366"])
    chk("a cls_hint also matches on the station class name for the machines whose class "
        "is known offline ('Frame Bonding' is nobody's machine name)",
        match_machines({"cls_hint": "Frame Bonding", "zone": "za"}, cat, cls_names)[0]
        == ["EID-12724", "EID-13219"])
    chk("instance_hint narrows a station to the named instance",
        match_machines({"cls_hint": "Frame Bonding", "zone": "za",
                        "instance_hint": "Kestrel"}, cat, cls_names)[0] == ["EID-13219"])
    chk("an instance_hint that matches nobody is disclosed rather than widened back",
        match_machines({"cls_hint": "Frame Bonding", "zone": "za",
                        "instance_hint": "Plover"}, cat, cls_names)
        == ([], "instance_no_match"))
    chk("a zone that disagrees with the roster is its own disclosure, and the code never "
        "quietly ignores the zone to make a sticker appear",
        match_machines({"cls_hint": "Flex Bonding", "zone": "za"}, cat, cls_names)
        == ([], "zone_mismatch:zb"))
    chk("a name nobody carries is disclosed as such",
        match_machines({"cls_hint": "Substrate MOI", "zone": "zc"}, cat, cls_names)
        == ([], "no_name_match"))

    # ---- map 讀取 ----
    chk("a map with the wrong schema_id is an empty map, not a half-trusted one",
        load_map(__file__)["sensors"] == [])
    doc = {"schema_id": SCHEMA_ID, "sensors": [
        {"sensor_id": 1, "anchor": {"eid": "EID-16821"}, "confirmed": True, "name": "a"},
        {"sensor_id": 2, "anchor": None, "confirmed": False, "name": "b",
         "skip_reason": "not a line station"},
        {"sensor_id": 3, "anchor": {"eid": "EID-16821"}, "confirmed": False, "name": "c"}]}
    chk("confirmed=false is never drawn, whatever its anchor says (C-ES6)",
        [s["sensor_id"] for s in confirmed_sensors(doc)] == [1])

    # ---- join ----
    payload = [{F_ID: 1, F_NAME: "010.Humidity", F_STATUS: "Good", F_ALERTS: 0,
                F_INTERVAL: 10, F_LAST: "2026-09-05 23:55:00",
                F_READING: "52.17% @ 22.4° C"}]
    rows, unmatched = join_rows(doc, payload, now, cat)
    chk("★ the tooltip's 'last communication' is the decoded instant, not the /Date(ms)/ "
        "wire form - and an unparsable one falls back to the raw string rather than to a "
        "made-up time",
        join_rows(doc, [dict(payload[0], **{F_LAST: "/Date(1767345240000)/"})],
                  now, cat)[0][0]["last_txt"] == "2026-01-02 09:14 UTC"
        and join_rows(doc, [dict(payload[0], **{F_LAST: "who knows"})],
                      now, cat)[0][0]["last_txt"] == "who knows")
    chk("join keeps the confirmed sensor, its raw reading and its short form",
        len(rows) == 1 and rows[0]["reading"] == "52% 22°"
        and rows[0]["reading_raw"] == "52.17% @ 22.4° C" and rows[0]["tone"] == "ok"
        and not unmatched, rows)
    rows2, _ = join_rows(doc, [], now, cat)
    chk("a confirmed sensor missing from the payload is still drawn - hollow, with no "
        "invented value (C-ES3 honest display)",
        len(rows2) == 1 and rows2[0]["tone"] == "unknown"
        and rows2[0]["reading"] == "" and rows2[0]["in_payload"] is False)
    chk("the legend counts are drawn-count and not-normal-count",
        count_alerts(rows) == (1, 0) and count_alerts(rows2) == (1, 1))

    # ---- 取數（零網路：注入 opener）----
    class _Resp:
        def __init__(self, body, ctype=""):
            self._b = body
            self.headers = {"Content-Type": ctype}

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = {}

    def _fake(req, timeout=None):
        seen["headers"] = dict(req.headers)
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _Resp(json.dumps({"Result": payload}).encode("utf-8"))

    got, err = fetch_sensor_list("kid", "sec", opener=_fake)
    chk("fetch sends both key headers, one serial call, 15s timeout, to the one URL constant",
        err == "" and len(got) == 1 and seen["timeout"] == DEFAULT_TIMEOUT_S
        and seen["url"] == IMONNIT_SENSOR_LIST_URL
        and any(k.lower() == "apikeyid" for k in seen["headers"])
        and any(k.lower() == "apisecretkey" for k in seen["headers"]), seen.get("url"))
    chk("missing credentials never reach the network at all",
        fetch_sensor_list("", "", opener=_fake) == ([], "nokey"))

    def _boom(req, timeout=None):
        raise RuntimeError("secret-bearing boom")

    got3, err3 = fetch_sensor_list("kid", "sec", opener=_boom)
    chk("a transport failure is a short classified string, never an exception and never "
        "a message that could carry a credential",
        got3 == [] and err3 == "error:RuntimeError")

    def _html(req, timeout=None):
        return _Resp(b"<html>login</html>")

    chk("a non-JSON body degrades to 'badjson' rather than half-parsing",
        fetch_sensor_list("kid", "sec", opener=_html) == ([], "badjson"))
    chk("a bare list body is accepted too (the API has returned both shapes)",
        _sensors_from(payload)[0] == payload and _sensors_from({"nope": 1}) == ([], "shape"))

    # ---- ★ 回應解碼（§7：度號 U+00B0）----
    _deg = "52.12% @ 22.5° C"
    chk("the degree sign survives when the response declares its charset - a wrong codec does "
        "not raise, it silently paints a replacement character onto the map",
        _decode_body(_deg.encode("latin-1"), "application/json; charset=iso-8859-1") == _deg
        and _decode_body(_deg.encode("utf-8"), "application/json; charset=utf-8") == _deg)
    chk("no charset, an unknown charset and a quoted one all fall back to UTF-8 rather than "
        "failing the whole poll",
        _decode_body(_deg.encode("utf-8"), "") == _deg
        and _decode_body(_deg.encode("utf-8"), "application/json; charset=x-nope") == _deg
        and _decode_body(_deg.encode("utf-8"), 'application/json; charset="utf-8"') == _deg
        and "�" in _decode_body(_deg.encode("latin-1"), ""))

    def _degfake(req, timeout=None):
        return _Resp(json.dumps({"Result": [dict(payload[0], **{F_READING: _deg})]},
                                ensure_ascii=False).encode("latin-1"),
                     "application/json; charset=iso-8859-1")

    _gotd, _errd = fetch_sensor_list("kid", "sec", opener=_degfake)
    chk("end to end: a latin-1 response reaches the sticker with its degree sign intact",
        _errd == "" and _gotd[0][F_READING] == _deg
        and short_reading(_gotd[0][F_READING]) == "52% 22°", _gotd)

    # ---- §8 超標（門檻＝map 逐字抄的 iMonnit Rules；Board 只做大於／小於）----------
    _A = {"rh": [42, 58], "temp_c": [None, 25],
          "rules": [212387, 211600, 328397, 363329, 232247, 661389]}
    _B = {"rh": [40, 60], "temp_c": [None, 25],
          "rules": [363329, 328397, 232247, 661389]}
    _G = {"rh": [None, 65], "temp_c": [None, None], "rules": [661389]}
    chk("parse_reading is the one place a reading becomes numbers (the sticker's short form "
        "reads the same two figures the limit check does)",
        parse_reading("52.17% @ 22.4° C") == {"rh": 52.17, "temp_c": 22.4}
        and parse_reading("48.9%") == {"rh": 48.9, "temp_c": None}
        and parse_reading("no reading") == {"rh": None, "temp_c": None},
        parse_reading("52.17% @ 22.4° C"))
    chk("RH 59% breaches the narrow A window and stays inside the wider B one - the threshold "
        "is the sensor's own copied rule, not one number for the whole fleet",
        [b["side"] for b in limit_breaches({"rh": 59.0}, _A)] == ["rh"]
        and limit_breaches({"rh": 59.0}, _B) == [],
        limit_breaches({"rh": 59.0}, _A))
    chk("RH 66% is over for all three groups (the account-wide 65% ceiling applies to "
        "everyone, including the sensors that have no other rule)",
        all(limit_breaches({"rh": 66.0}, lim) for lim in (_A, _B, _G)))
    chk("25.1 degrees is over and 25.0 is not: the rule reads 'Greater Than 25', so the bound "
        "itself is inside",
        [b["side"] for b in limit_breaches({"temp_c": 25.1}, _B)] == ["temp_c"]
        and limit_breaches({"temp_c": 25.0}, _B) == []
        and limit_breaches({"temp_c": 26.0}, _G) == [],
        limit_breaches({"temp_c": 25.1}, _B))
    chk("a low-side rule still fires (RH 39% is under both windows) and a null side is simply "
        "no rule, never an implicit zero",
        [b["dir"] for b in limit_breaches({"rh": 39.0}, _B)] == ["lo"]
        and limit_breaches({"rh": 10.0}, _G) == [],
        limit_breaches({"rh": 39.0}, _B))
    chk("no limits and no parsable reading can never produce an over: the flag needs both a "
        "copied threshold and a number iMonnit actually sent",
        limit_breaches({"rh": 99.0}, None) == []
        and limit_breaches({"rh": 99.0}, {}) == []
        and limit_breaches({"rh": None, "temp_c": None}, _A) == []
        and limit_breaches({}, _A) == [])
    chk("the rule id printed in the tooltip is looked up from the bound the map supplied and "
        "cross-checked against that sensor's own rule list - an unknown bound prints the "
        "sentence without a number rather than inventing one",
        limit_breaches({"rh": 59.0}, _A)[0]["rule"] == 211600
        and limit_breaches({"rh": 39.0}, _B)[0]["rule"] == 363329
        and limit_breaches({"temp_c": 25.1}, _B)[0]["rule"] == 232247
        and limit_breaches({"rh": 66.0}, _G)[0]["rule"] == 661389
        and limit_breaches({"rh": 63.0}, {"rh": [None, 62], "rules": [661389]})[0]["rule"]
        is None,
        [b["rule"] for b in limit_breaches({"rh": 59.0}, _A)])
    _over_s = {F_ID: 9, F_STATUS: 0, F_ALERTS: True, F_INTERVAL: 10,
               F_LAST: "2026-09-05 23:55:00", F_READING: "59.0% @ 22.4° C"}
    _over_f = sensor_flags(_over_s, now, _A)
    _amber_f = sensor_flags(dict(_over_s, **{F_STATUS: 2}), now, None)
    _both_f = sensor_flags(dict(_over_s, **{F_STATUS: 2}), now, _A)
    _stale_over = sensor_flags(dict(_over_s, **{F_LAST: "2026-09-01 00:00:00"}), now, _A)
    chk("§8 tone order is unknown > over > alert > ok: a stale sensor is never painted red "
        "(its reading is old news, and 'we do not know' is not 'it is broken'), while a live "
        "over beats a status code",
        _over_f["tone"] == "over" and _over_f["over"] is True
        and _amber_f["tone"] == "alert" and _amber_f["over"] is False
        and _both_f["tone"] == "over" and _both_f["alert"] is True
        and _stale_over["tone"] == "unknown" and _stale_over["over"] is True
        and sensor_flags(_over_s, now, None)["tone"] == "ok",
        f"over={_over_f['tone']} amber={_amber_f['tone']} both={_both_f['tone']} "
        f"stale={_stale_over['tone']}")
    import os.path as _op                      # selftest 只在這裡碰路徑（模組本體零 I/O）

    _map8_path = _op.join(_op.dirname(_op.abspath(__file__)), "_ENV-SENSOR-MAP.json")
    if not _op.exists(_map8_path):
        # 私有對照表不存在（例：脫敏公開樹不帶真實感測器對照）⇒ 標 skipped、不算失敗；存在 ⇒ 照舊全比對。
        print("board_envsensor selftest: skipped: _ENV-SENSOR-MAP.json absent")
    else:
        _map8 = load_map(_map8_path)
        _lim8 = [s8.get("limits") for s8 in confirmed_sensors(_map8)]
        chk("every confirmed sensor carries a copied limit block with its rule ids, and each of "
            "those bounds is one this module can name (so the tooltip can always cite a rule)",
            bool(_lim8) and all(isinstance(l8, dict) and l8.get("rules") for l8 in _lim8)
            and all(RULE_IDS.get((side, dirn, float(bound))) in set(l8["rules"])
                    for l8 in _lim8 for side in ("rh", "temp_c")
                    for bound, dirn in zip(l8.get(side) or [None, None], ("lo", "hi"))
                    if bound is not None),
            f"limits={len(_lim8)}")
    chk("count_over counts only the red ones (the legend chip is an exception, not a fixture)",
        count_over([{"tone": "over"}, {"tone": "alert"}, {"tone": "ok"},
                    {"tone": "unknown"}, {"tone": "over"}]) == 2
        and count_over([]) == 0)

    print("---- {} PASS / {} FAIL ----".format(ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(_selftest())
    print(__doc__)
