# -*- coding: utf-8 -*-
"""
_gen_product_tags.py — 產品能力標籤對照檔產生器（`board_tags.py` 的唯一寫者）。

規格＝`SPEC-PRODUCT-TAGS.md`。**規則＝Chris 2026-08-31 裁決**（記憶 [[product-tags-rule]]）：
**標籤全由 MES 決定、不人工裁**；**套用面＝2026-09-01 對話同意的方向**（SPEC §6）。
本檔是「MES 普查結果 → 宣告式對照模組」的唯一轉譯路徑；`board_tags.py` 是**產出物**，
任何人不得手改（C-PT1）。

一句話：
    讀 730 天產品標籤普查（`_PRODUCT-TAGS-CENSUS-730D-*.txt` 的**主表**）
    → 以 (EID, station_class) 為線套門檻（`n_segments >= 50` **或** 佔該線總段數 `>= 1%`）
    → **按 EID 取跨站聯集** → 決定性地寫出 `board_tags.py`（同輸入同輸出、位元相同）。

用法：
    python _gen_product_tags.py                       # 用預設普查檔重生 board_tags.py
    python _gen_product_tags.py <census.txt>           # 指定來源檔
    python _gen_product_tags.py [census.txt] --check   # 只比對、不寫檔（一致 0／不一致 1）

來源檔身分（SPEC §3）
    · 產生它的 SQL ＝ **`_probe_product_tags_730d_v3.sql`**（v2 逐字元 ＋ 兩個補站
      `segSealDispenseDamAndFill`／`segGrout`；v2 ＝ v1 ＋ 兩行
      `CONVERT(varchar(19), …, 120)`，讓日期以**文字**出庫、Excel 轉不壞；秒精度、無毫秒）。
    · 編碼＝**UTF-8 無 BOM**、Tab 分隔、CRLF。解碼仍用 `utf-8-sig`（有 BOM 也吃得下）。

行尾（★ F1，2026-09-02）
    · 本 repo `core.autocrlf=true`。普查 txt 的 **md5 是身分**（釘在 `board_tags.CENSUS_MD5`）
      ⇒ `.gitattributes` 以 `-text` 把它釘成「完全不正規化」，否則 `git add` 會把 CRLF 換成 LF、
      md5 當場換值。
    · 產出物 `board_tags.py` 反向釘 `text eol=lf`；**本產生器永遠只寫 LF**。
    · `--check` 比對前把現檔的 CRLF 正規化成 LF——fresh checkout 若被寫成 CRLF，
      那是行尾不是內容，不該報成「有人手改」。

⚠ 鐵則（動本檔前先讀 `SPEC-PRODUCT-TAGS.md` §5）
    · **UNKNOWN 永不成為標籤**（C-PT2），但**進分母**——它是「substrate/非產品段」的誠實佔位，
      拿掉它會把低佔比產品訊號灌成高佔比。
    · **門檻是 OR 不是 AND**：大機台跑得多 ⇒ 絕對段數救得起小佔比；小站總量少 ⇒ 佔比救得起小段數。
    · **聯集在 EID 級、不在線級**：一台機在 A 線全 UNKNOWN、在 B 線是 Plover ⇒ 該機 observed=Plover，
      **不是 default**（`EID-11860` 就是這型；selftest 釘死）。
    · **解析嚴格、寧可炸不要猜**：block 數、表頭、family 值域、時戳形、非負整數逐項驗；
      y1／y2 QA 表對主表**逐站守恆**當輸入完整性閘（**不進計算**，§7 反模式 A9）。
    · 產出模組的 selftest 釘子（89 台／84 observed／5 default／線級分布…）是**寫死在本檔
      `_PINS` 區塊的人工承認值**。換普查檔重生後 selftest 會紅——那是**設計**：新普查要人看過
      才算數，看過再來改 `_PINS`，**不准為了讓測試綠而反過來改釘子**。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CENSUS = "_PRODUCT-TAGS-CENSUS-730D-V3-20260903.txt"
OUT_MODULE = "board_tags.py"

# ── 規則常數（唯一出處；產出模組原樣鏡射）─────────────────────────────────────
FAMILIES = ("Plover", "Kestrel", "Heron")
LETTER = {"Plover": "R", "Kestrel": "A", "Heron": "M"}
UNKNOWN_FAMILY = "UNKNOWN (NULL -- substrate/non-product segment)"
WINDOW_DAYS = 730
TH_SEGMENTS = 50
TH_SHARE = 0.01

# ── 來源檔形狀（三個結果集、空行分隔）────────────────────────────────────────
MAIN_HEADER = ("EID", "station_class", "family", "n_segments",
               "first_seen", "last_seen", "lot_sample_min", "lot_sample_max")
Y1_HEADER = ("station_class", "y1_raw_rows", "y1_distinct_eids")
Y2_HEADER = ("station_class", "y2_raw_rows_matched")
N_BLOCKS = 3

_EID_RE = re.compile(r"^EID-\d{5}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")   # v2 SQL：秒精度、無毫秒
_INT_RE = re.compile(r"^\d+$")                                   # 非負整數，不吃空白/符號/小數


def _utf8_stdout():
    """主控台若不是 UTF-8（Windows cp1252/cp950）就轉過去；轉不動就算了，`_say` 還有第二層。"""
    for s in (sys.stdout, sys.stderr):
        try:
            if s is not None and (getattr(s, "encoding", "") or "").lower().replace("-", "") != "utf8":
                s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


def _say(msg, stream=None):
    """print 的安全版：主控台編碼吃不下中文時退成 backslashreplace，不讓 CLI 因 cp1252 炸掉。"""
    stream = stream or sys.stdout
    try:
        print(msg, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        print(msg.encode(enc, "backslashreplace").decode(enc, "replace"), file=stream)


# ══════════════════════════════════════════════════════════════════════════════
# 純函式層（零 I/O；`board_tags.py` 的 selftest 會 import 這幾支重算比對）
# ══════════════════════════════════════════════════════════════════════════════

def split_blocks(text):
    """普查全文 → **恰好三個** block（各自 list[str] 非空行）：主表／y1 QA／y2 QA。

    block 數不是 3 ⇒ raise。這道閘擋的是「只貼回一半」「多貼了一個結果集」這種
    看起來像資料、其實是殘檔的輸入。
    """
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [[ln for ln in b.split("\n") if ln.strip()] for b in body.split("\n\n")]
    blocks = [b for b in blocks if b]
    if len(blocks) != N_BLOCKS:
        raise ValueError("普查應有恰好 %d 個結果集（主表／y1／y2），實得 %d 個"
                         % (N_BLOCKS, len(blocks)))
    return blocks


def _check_header(lines, expect, what):
    hdr = tuple(lines[0].split("\t"))
    if hdr != expect:
        raise ValueError("%s 表頭不符：%r != %r" % (what, hdr, expect))


def parse_census(text):
    """普查全文 → **主表** rows（list[dict]）。**逐欄驗，寧可炸不要猜。**

    來源檔三個結果集以空行分隔：主表／y1 QA／y2 QA。**只吃第一個 block**——
    y1（每站 raw 列數與 distinct EID 數）與 y2（每站 matched 列數）是普查自身的守恆 QA，
    **不參與標籤計算**（分母只算主表；§7 反模式 A9），只在 `check_integrity()` 當輸入閘。

    驗什麼：欄數／EID 形（`EID-` ＋ 五碼數字）／`family` 落在 `FAMILIES ∪ {UNKNOWN_FAMILY}`／
    `n_segments` 非負整數／`first_seen`、`last_seen` 合 `YYYY-MM-DD HH:MM:SS`／
    `first_seen <= last_seen`。任一不合 ⇒ raise。
    """
    lines = split_blocks(text)[0]
    _check_header(lines, MAIN_HEADER, "主表")
    allowed = set(FAMILIES) | {UNKNOWN_FAMILY}
    rows = []
    for i, ln in enumerate(lines[1:], start=2):
        f = ln.split("\t")
        if len(f) != len(MAIN_HEADER):
            raise ValueError("主表第 %d 行欄數不符（%d 欄）：%r" % (i, len(f), ln[:120]))
        eid, station, family, n_s, first, last = (x.strip() for x in f[:6])
        if not _EID_RE.match(eid):
            raise ValueError("主表第 %d 行 EID 形狀不合（應 EID- ＋ 五碼數字）：%r" % (i, eid))
        if not station:
            raise ValueError("主表第 %d 行 station_class 是空的" % i)
        if family not in allowed:
            raise ValueError("主表第 %d 行 family 不在值域：%r（合法＝%s ＋ UNKNOWN 全字串）"
                             % (i, family, "/".join(FAMILIES)))
        if not _INT_RE.match(n_s):
            raise ValueError("主表第 %d 行 n_segments 不是非負整數：%r" % (i, n_s))
        for label, ts in (("first_seen", first), ("last_seen", last)):
            if not _TS_RE.match(ts):
                raise ValueError("主表第 %d 行 %s 不合 YYYY-MM-DD HH:MM:SS：%r（v2 SQL 應以 "
                                 "CONVERT(varchar(19), …, 120) 出庫；若含毫秒或地區日期形，"
                                 "多半是被 Excel 轉過的壞版）" % (i, label, ts))
        if first > last:
            raise ValueError("主表第 %d 行 first_seen > last_seen：%r > %r" % (i, first, last))
        rows.append({"eid": eid, "station": station, "family": family,
                     "n": int(n_s), "first_seen": first, "last_seen": last})
    if not rows:
        raise ValueError("普查主表是空的")
    return rows


def parse_qa(text):
    """普查全文 → `(y1, y2)`＝`{station: (raw_rows, distinct_eids)}`／`{station: raw_rows_matched}`。

    **QA 表不進標籤計算**，只餵 `check_integrity()`。
    """
    blocks = split_blocks(text)
    _check_header(blocks[1], Y1_HEADER, "y1 QA")
    _check_header(blocks[2], Y2_HEADER, "y2 QA")

    def _rows(lines, width, what):
        out = {}
        for i, ln in enumerate(lines[1:], start=2):
            f = [x.strip() for x in ln.split("\t")]
            if len(f) != width:
                raise ValueError("%s 第 %d 行欄數不符（%d 欄）" % (what, i, len(f)))
            if not f[0]:
                raise ValueError("%s 第 %d 行 station_class 是空的" % (what, i))
            for v in f[1:]:
                if not _INT_RE.match(v):
                    raise ValueError("%s 第 %d 行 %r 不是非負整數" % (what, i, v))
            if f[0] in out:
                raise ValueError("%s 有重複站別：%s" % (what, f[0]))
            out[f[0]] = tuple(int(v) for v in f[1:])
        return out

    y1 = _rows(blocks[1], 3, "y1 QA")
    y2 = {k: v[0] for k, v in _rows(blocks[2], 2, "y2 QA").items()}
    return y1, y2


def check_integrity(rows, y1, y2):
    """用 y1／y2 對主表**逐站守恆**當輸入完整性閘。違反 ⇒ raise。回 stats dict。

    兩條**實測成立**的守恆律（2026-09-03 V3 普查：41/41 站零差；V2 為 39/39）：
        ① `y1.distinct_eids[站]` ≡ 主表該站的 distinct EID 數
        ② `y2.raw_rows_matched[站]` ≡ 主表該站的 `SUM(n_segments)`
    第三條**刻意不當守恆律**：`y1.raw_rows` 與 `y2.raw_rows_matched` **本來就不相等**——
    join 到設備後有極小幅放大（本次 4 站、合計 **+16 列 / 6,227,290 ＝ 0.00026%**、單站最大 +12，
    且**恆 y2 >= y1**）。這是普查設計要暴露的量，不是錯誤；只斷言**單調**。

    站別集合：`set(y1) == set(y2)`；主表站別 ⊆ y1（某站若整站零列，主表可以沒有它，
    但 y1 該站的 distinct_eids 必須是 0——由 ① 自動涵蓋）。
    """
    if set(y1) != set(y2):
        raise ValueError("y1／y2 站別集合不一致（y1 %d 站、y2 %d 站、對稱差 %d）"
                         % (len(y1), len(y2), len(set(y1) ^ set(y2))))
    main_stations = set(r["station"] for r in rows)
    extra = main_stations - set(y1)
    if extra:
        raise ValueError("主表出現 QA 表沒有的站別：%s" % ", ".join(sorted(extra)))

    m_eids, m_sum = {}, {}
    for r in rows:
        m_eids.setdefault(r["station"], set()).add(r["eid"])
        m_sum[r["station"]] = m_sum.get(r["station"], 0) + r["n"]

    bad = []
    for st in sorted(y1):
        got_e, want_e = len(m_eids.get(st, ())), y1[st][1]
        if got_e != want_e:
            bad.append("站 %s：主表 distinct EID %d ≠ y1_distinct_eids %d" % (st, got_e, want_e))
        got_n, want_n = m_sum.get(st, 0), y2[st]
        if got_n != want_n:
            bad.append("站 %s：主表 SUM(n_segments) %d ≠ y2_raw_rows_matched %d"
                       % (st, got_n, want_n))
    if bad:
        raise ValueError("普查逐站守恆失敗（%d 條）：\n  " % len(bad) + "\n  ".join(bad[:10]))

    fan = {st: y2[st] - y1[st][0] for st in y1}
    neg = [st for st, d in fan.items() if d < 0]
    if neg:
        raise ValueError("y2 < y1（join 不該掉列）：%s" % ", ".join(sorted(neg)[:10]))
    return {
        "stations": len(y1),
        "main_rows": len(rows),
        "eids": len(set(r["eid"] for r in rows)),
        "y1_total": sum(v[0] for v in y1.values()),
        "y2_total": sum(y2.values()),
        "fanout_total": sum(fan.values()),
        "fanout_stations": sum(1 for d in fan.values() if d),
        "fanout_max": max(fan.values()) if fan else 0,
    }


def build_lines(rows):
    """rows → {(eid, station): {family: n_segments}}。同 (eid, station, family) 重複＝來源錯，炸。"""
    out = {}
    for r in rows:
        d = out.setdefault((r["eid"], r["station"]), {})
        if r["family"] in d:
            raise ValueError("普查有重複列：%s / %s / %s" % (r["eid"], r["station"], r["family"]))
        d[r["family"]] = r["n"]
    return out


def counted_families(counts):
    """一條線的 {family: n}（**含 UNKNOWN、UNKNOWN 進分母**）→ 計入的 family tuple（FAMILIES 序）。

    計入條件＝`n >= TH_SEGMENTS` **或** `n / 該線總段數 >= TH_SHARE`（OR）。
    UNKNOWN 永不進結果（C-PT2）。總段數 0 ⇒ 空 tuple。
    """
    total = sum(counts.values())
    if total <= 0:
        return ()
    out = []
    for fam in FAMILIES:
        n = counts.get(fam, 0)
        if n <= 0:
            continue
        if n >= TH_SEGMENTS or (n / total) >= TH_SHARE:
            out.append(fam)
    return tuple(out)


def line_share(counts, family):
    """該 family 在該線的佔比（分母含 UNKNOWN），六位小數。總段數 0 ⇒ 0.0。"""
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    return round(counts.get(family, 0) / total, 6)


def build_tags(rows):
    """rows → {eid: record}（record 形狀見 `board_tags.PRODUCT_TAGS` docstring）。

    · `tags` ＝各線 `counted_families` 的**跨站聯集**（FAMILIES 序）；空 ⇒ `origin="default"`
      且 `tags = FAMILIES`（零訊號＝全能力預設）。
    · `families` ＝**產品 family 跨線合計原始段數**（不套門檻）——保留「看過但沒過門檻」的證據。
    · `first_seen`/`last_seen` ＝**只看產品 family 列**的日期 min/max（`YYYY-MM-DD`）；
      一台機若一列產品 family 都沒有 ⇒ `None`。
    · `lines` ＝逐 (station, family) 列，含 UNKNOWN；`counted` 標該列有沒有進聯集。
    """
    lines = build_lines(rows)
    by_eid = {}
    for (eid, station), counts in lines.items():
        by_eid.setdefault(eid, {})[station] = counts

    out = {}
    for eid in sorted(by_eid):
        stations = sorted(by_eid[eid])
        tagset = set()
        for st in stations:
            tagset |= set(counted_families(by_eid[eid][st]))
        tags = tuple(f for f in FAMILIES if f in tagset)
        origin = "observed" if tags else "default"
        if not tags:
            tags = FAMILIES

        fam_total = {}
        for st in stations:
            for fam in FAMILIES:
                n = by_eid[eid][st].get(fam, 0)
                if n > 0:
                    fam_total[fam] = fam_total.get(fam, 0) + n
        families = {f: fam_total[f] for f in FAMILIES if f in fam_total}

        prod_rows = [r for r in rows if r["eid"] == eid and r["family"] in FAMILIES]
        first_seen = min(r["first_seen"] for r in prod_rows)[:10] if prod_rows else None
        last_seen = max(r["last_seen"] for r in prod_rows)[:10] if prod_rows else None

        rec_lines = []
        for st in stations:
            counts = by_eid[eid][st]
            counted = set(counted_families(counts))
            order = [f for f in FAMILIES if f in counts]
            if UNKNOWN_FAMILY in counts:
                order.append(UNKNOWN_FAMILY)
            for fam in order:
                rec_lines.append((st, fam, counts[fam], line_share(counts, fam), fam in counted))

        out[eid] = {
            "tags": tags,
            "origin": origin,
            "families": families,
            "stations": tuple(stations),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "lines": tuple(rec_lines),
        }
    return out


def window_end(rows):
    """普查主表最大 `last_seen` 的日期部分（`YYYY-MM-DD`）＝這份普查的窗末。"""
    return max(r["last_seen"] for r in rows)[:10]


def load_census(path=None):
    """讀普查檔 → `(rows, y1, y2, md5, window_end, stats)`。**唯一的 I/O 點**（不碰 MES）。"""
    path = path or os.path.join(HERE, DEFAULT_CENSUS)
    raw = open(path, "rb").read()
    md5 = hashlib.md5(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    rows = parse_census(text)
    y1, y2 = parse_qa(text)
    stats = check_integrity(rows, y1, y2)
    return rows, y1, y2, md5, window_end(rows), stats


# ══════════════════════════════════════════════════════════════════════════════
# 產出模組模板（★ 人工承認的釘子住 _PINS；換普查檔要人看過才改）
# ══════════════════════════════════════════════════════════════════════════════

_HEADER = '''# -*- coding: utf-8 -*-
"""
board_tags.py — 產品能力標籤對照表（`{Plover, Kestrel, Heron}`）。規格＝`SPEC-PRODUCT-TAGS.md`。

★★★ GENERATED FILE — 由 `_gen_product_tags.py` 從 `@@CENSUS@@`
（md5 `@@MD5@@`、窗末 `@@WEND@@`）產生，勿手改。★★★

**規則＝Chris 2026-08-31 裁決**（記憶 [[product-tags-rule]]）；**套用面＝2026-09-01 對話同意**
（SPEC §6）。要改標籤＝**重跑 730 天普查換來源檔 → 跑 `python _gen_product_tags.py` 重生
→ 人看過釘子**（C-PT1）。手改本檔＝下一次重生就被覆蓋，而且會讓「MES 決定、不人工裁」失效。

語意（SPEC §1）：
    標籤＝**能力**，不是排程也不是產出歸屬——「@@WDAYS@@ 天內 MES 觀察到這台機跑過的 family」，
    跑過＝實務上被指派過。零訊號 ⇒ `origin="default"`＝R+A+M（全能力預設，**不是「沒有能力」**）。
    頁面**顯示相同**（default 與 observed 長一樣）、只有 tooltip／對照檔區分兩者。

規則（SPEC §2）：
    以 `(EID, station_class)` 為線；某 family 計入 observed ⇔
        `n_segments >= @@THSEG@@` **或** `n_segments / 該線總段數 >= @@THSHARE@@`（**OR**；分母含 UNKNOWN）。
    再**按 EID 取跨站聯集**。UNKNOWN 永不成為標籤（C-PT2）。
    ⚠ 裁決原文寫「該機總段數」；本檔以**線級分母＋EID 聯集**實作——兩讀法在本次普查
    **等價（0 台變）**，立論與存證＝SPEC §2.1。

⚠ 鐵則（出處：〔裁決〕＝Chris 08-31；〔對話同意〕＝09-01）
    · C-PT1 不人工改本檔；改標籤＝重跑普查＋產生器。          〔裁決〕
    · C-PT2 UNKNOWN 永不成標籤（但進分母）。                  〔裁決〕
    · C-PT3 default 顯示與 observed 相同，只有 tooltip 區分。 〔裁決〕
    · C-PT4 標籤**只描述能力，不得用來過濾產出／良率**——那要用**段的 family**。〔對話同意〕
    · C-PT5 過濾器只在「列＝機台」的頁；狀態只在 URL 參數、預設全部、不跨頁記憶。〔對話同意〕

本檔＝**純資料＋純函式、零 I/O**。唯一的專案 import ＝ `mes_timeline` 的**兩張別名表**
（`EID_RENAME`／`EID_ALIAS_DROP`＝形④ 別名折疊的**唯一出處**，SPEC §2.5.3；純資料、
離線可用、**不碰 MES**）——**絕不在本檔另抄一份**，抄一份就是第二個會漂移的事實。
（`--selftest` 另會 import `_gen_product_tags` 的純函式重算比對，**不讀 MES**。）
行尾**永遠 LF**（`.gitattributes` 釘死）。

    python board_tags.py --selftest
"""

from __future__ import annotations

import re
import sys

from mes_timeline import EID_ALIAS_DROP, EID_RENAME

'''

_CONSTS = '''# ── 來源檔身分（SPEC §3）──────────────────────────────────────────────────────
CENSUS_FILE = "@@CENSUS@@"
CENSUS_MD5 = "@@MD5@@"
CENSUS_WINDOW_END = "@@WEND@@"          # 主表最大 last_seen 的日期部分

# ── 規則常數（SPEC §2；改這裡＝改口徑，要走 SPEC 修訂）───────────────────────
WINDOW_DAYS = @@WDAYS@@
TH_SEGMENTS = @@THSEG@@
TH_SHARE = @@THSHARE@@
FAMILIES = ("Plover", "Kestrel", "Heron")
LETTER = {"Plover": "R", "Kestrel": "A", "Heron": "M"}
UNKNOWN_FAMILY = "UNKNOWN (NULL -- substrate/non-product segment)"

'''

_DATA_DOC = '''# ── 對照表 ────────────────────────────────────────────────────────────────────
# PRODUCT_TAGS: dict[str, dict]，鍵＝EID（形 `EID-` ＋ 五碼數字，補零）。每台：
#   tags       tuple[str]      能力標籤（FAMILIES 序）；origin="default" 時＝FAMILIES 全三個
#   origin     "observed"|"default"
#   families   dict[str, int]  產品 family **跨線合計原始段數**（不套門檻；沒看過的 family 不列）
#   stations   tuple[str]      這台機在普查窗內出現過的 station_class（排序）
#   first_seen str|None        只看產品 family 列的日期 min（YYYY-MM-DD）；一列都沒有 ⇒ None
#   last_seen  str|None        同上取 max
#   lines      tuple[tuple]    ((station, family, n_segments, share, counted), ...)
#                              share＝該 family 佔**該線**總段數（分母含 UNKNOWN），六位小數
#                              counted＝這一列有沒有通過門檻進聯集（UNKNOWN 恆 False）
'''

_BODY = '''

# ══════════════════════════════════════════════════════════════════════════════
# 形④ 別名折疊（SPEC §2.5.3；Chris 2026-09-02 裁）
# ══════════════════════════════════════════════════════════════════════════════
# **改名型（形④機制 (a)）的主 EID，標籤取其別名的 `observed` 聯集。**
# 理由：改名主 EID 在 MES 上**沒有自己的身分**——段全記在別名上（`EID-38106` 的段記在
# `38041`／`38072`）⇒ 拿 §2.5 的 default 代表它，是把一個**已知的觀測**講成**無訊號**。
#
# ⚠ 別名表**只 import `mes_timeline` 的同一個物件**（`--selftest` 釘 `is`）——
#   在本檔另抄一張表＝第二個會漂移的事實（同 `_probe_cost_rollup`／`board_capacity` 紀律）。
# ⚠ **只限機制 (a)**：`ALIAS_OF` 的鍵恆 ⊆ `set(EID_RENAME.values())`；機制 (b) 的主 EID
#   （`07940`／`07905`／`26554`）**兩張表都沒有它們**、本來就有自己的超集段
#   ⇒ **一個位元不動**（SPEC §2.5.2／§2.5.3 的警告）。
# ⚠ **不吃吸收元（§7 A11）**：只聯集 `origin == "observed"` 的別名。若一顆 observed 別名都沒有，
#   折疊回 `None` ⇒ 照走 §2.5 的 default——**寧可誠實地說「沒量到」，也不假裝有根據**。
# ⚠ `PRODUCT_TAGS` **仍是純普查**（鍵＝普查窗內出現過的 EID）；折疊結果住 `FOLDED_TAGS`。
#   要拿一台機的記錄（tooltip 段數等）請用 `record_of()`，**不要直接 `PRODUCT_TAGS.get()`**。
# ⚠⚠ **標籤取聯集、段數不取和**：機制 (b) 的別名（`EID_ALIAS_DROP`，如 `38072`）段是主 EID 段的
#   **子集** ⇒ 只貢獻標籤／站別／first-last，**不貢獻段數與線**（`SPEC-RUNWINDOW.md` §10.8-8
#   「不是加總。加總＝雙算」）。段數只由**計數主**（`main` 自己＋`EID_RENAME` 來源）貢獻。


def _alias_of():
    """`{改名型主 EID: (別名, ...)}`。別名＝兩張表中 value 指向該主 EID 的 key。"""
    mains = set(EID_RENAME.values())
    out = {}
    for table in (EID_RENAME, EID_ALIAS_DROP):
        for alias, main in table.items():
            if main in mains:
                out.setdefault(main, set()).add(alias)
    return {m: tuple(sorted(a)) for m, a in out.items()}


ALIAS_OF = _alias_of()


def _fold_alias(main, aliases):
    """把 `main` 自己（若有）＋ `aliases` 中**observed** 的普查列聯集成一筆記錄。

    ★★ **段數絕不相加**（`SPEC-RUNWINDOW.md` §10.8-8 的兩機制表；§10.8-5 反模式 2）：

      · **計數主**＝`main` 自己 ＋ **`EID_RENAME` 來源**的別名（機制 (a)「**搬**」——
        主 EID 本來零段、段被 fixture 領走）⇒ 貢獻 `families` 段數與 `lines`。
      · **`EID_ALIAS_DROP` 來源**的別名（機制 (b)「**忽略**」——別名段是主 EID 段的
        **子集**、每段同掛）⇒ **只貢獻 `tags`／`stations`／`first_seen`／`last_seen`，
        不貢獻段數、不貢獻線**。相加＝**雙算**（`38041` 155 ＋ `38072` 141 ＝ 296
        ≈ 2× 真值；普查兩列 first/last 與 lot 樣本逐位相同＝同一批段被記兩次）。

    站集合聯集、`first_seen`／`last_seen` 取 min/max、`tags` 取聯集（FAMILIES 序）、
    `origin` 恆 `"observed"`。零貢獻者 ⇒ 回 `None`（呼叫端照走 default）。
    ⚠ Chris 的裁決（SPEC §2.5.3）是「標籤取別名 observed 的**聯集**」——**聯集講的是標籤**，
      段數的口徑住 §10.8-8，兩者別混。
    """
    parts = []
    for eid in (main,) + tuple(aliases):
        rec = PRODUCT_TAGS.get(eid)
        if rec is not None and rec["origin"] == "observed":
            parts.append((eid, rec))
    if not parts:
        return None
    fam, lines, stations, tagset, counted = {}, [], set(), set(), []
    for eid, rec in parts:
        stations.update(rec["stations"])
        tagset.update(rec["tags"])
        if eid != main and eid not in EID_RENAME:
            continue                      # 機制 (b)：別名段不另計（絕不相加）
        counted.append(eid)
        for f, n in rec["families"].items():
            fam[f] = fam.get(f, 0) + n
        lines.extend(rec["lines"])
    firsts = [rec["first_seen"] for _e, rec in parts if rec["first_seen"]]
    lasts = [rec["last_seen"] for _e, rec in parts if rec["last_seen"]]
    src = tuple(e for e, _r in parts)
    cnt = tuple(counted)
    return {
        "tags": tuple(f for f in FAMILIES if f in tagset),
        "origin": "observed",
        "families": {f: fam[f] for f in FAMILIES if f in fam},
        "stations": tuple(sorted(stations)),
        "first_seen": min(firsts) if firsts else None,
        "last_seen": max(lasts) if lasts else None,
        "lines": tuple(sorted(lines)),
        # 折疊來源＝哪幾顆別名（含主 EID 自身，若它也有普查列）。只供人看／log。
        "folded_from": src,
        # 其中**貢獻段數**的是哪幾顆（機制 (a)）；其餘只貢獻標籤。
        "counted_from": cnt,
        "facts": ("folded from " + ", ".join(src)
                  + "; segments counted from " + (", ".join(cnt) or "(none)")),
    }


FOLDED_TAGS = {m: r for m, r in
               ((m, _fold_alias(m, a)) for m, a in ALIAS_OF.items()) if r is not None}


def record_of(eid):
    """`eid` → 對照記錄（**含形④ 折疊**）；不在表內 ⇒ `None`。

    折疊主 EID 走 `FOLDED_TAGS`，其餘走普查表 `PRODUCT_TAGS`。
    ⚠ 呈現層要段數（chip tooltip）**一律走本函式**——直接讀 `PRODUCT_TAGS` 會讓折疊主 EID
    的 chip 是 observed、tooltip 卻空白。
    """
    rec = FOLDED_TAGS.get(eid)
    return rec if rec is not None else PRODUCT_TAGS.get(eid)


# ══════════════════════════════════════════════════════════════════════════════
# 查詢層（純函式）
# ══════════════════════════════════════════════════════════════════════════════

def normalize_families(families):
    """任意 family 寫法 → canonical tuple（**FAMILIES 序、去重**）。

    吃字母（`R`/`a`/`m`）與全名（`kestrel`／`Plover`），**大小寫不敏感**；
    **不認得的一律忽略**（不炸——URL 參數是使用者可以亂打的東西）。
    `None`／空 ⇒ `()`。

    ⚠ 傳 `str` 會 raise（腳槍：`"Plover"` 逐字元迭代會意外「剛好」只中 R）。
    要吃字串請走 `parse_tag_param()`。
    """
    if families is None:
        return ()
    if isinstance(families, str):
        raise TypeError("normalize_families 不吃 str（會逐字元迭代）；"
                        "要解析 URL 參數請用 parse_tag_param()")
    lookup = {}
    for fam in FAMILIES:
        lookup[fam.lower()] = fam
        lookup[LETTER[fam].lower()] = fam
    got = set()
    for x in families:
        if not isinstance(x, str):
            continue
        fam = lookup.get(x.strip().lower())
        if fam:
            got.add(fam)
    return tuple(f for f in FAMILIES if f in got)


def parse_tag_param(s):
    """URL 參數 `?tag=` → canonical family tuple（SPEC §6.2）。

        parse_tag_param("R,a")     -> ("Plover", "Kestrel")
        parse_tag_param("kestrel") -> ("Kestrel",)
        parse_tag_param("m , R")   -> ("Plover", "Heron")     # 回傳恆 FAMILIES 序
        parse_tag_param("R,zzz")   -> ("Plover",)             # 不認得的忽略
        parse_tag_param("")        -> ()                      # 空＝全部（呼叫端語意）
        parse_tag_param(None)      -> ()

    分隔符：逗號、加號、空白（三者混用皆可）。**空 tuple ＝「沒選」＝ 全部**——
    `filter_eids` 對空選回全部，兩支的語意在此對齊（C-PT5「預設全部」）。
    """
    if s is None:
        return ()
    if not isinstance(s, str):
        return normalize_families(s)
    return normalize_families([t for t in re.split(r"[,+\\s]+", s) if t])


def tags_of(eid):
    """`eid`（形 `EID-` ＋ 五碼數字，補零）→ `(tags_tuple, origin)`。

    不在表內（名冊有、普查沒有的結構性零訊號機：治具／烤箱／顯微鏡…）
    ⇒ `(FAMILIES, "default")`——與表內 default 台同形，呼叫端不必分兩條路。
    鍵**大小寫敏感、不做正規化**：`"eid-00425"`／`"EID-425"` 一律視為表外 ⇒ default。
    ★ 形④ 改名型主 EID（如 `EID-38106`）走 `FOLDED_TAGS`＝其別名 `observed` 的聯集
    （SPEC §2.5.3）——它的段記在別名編號上，不是「沒量到」。
    """
    rec = record_of(eid)
    if rec is None:
        return FAMILIES, "default"
    return rec["tags"], rec["origin"]


def letters_of(eid):
    """`eid` → 縮寫字母串（FAMILIES 序）。例：Plover+Kestrel ⇒ `"RA"`；default ⇒ `"RAM"`。"""
    tags, _origin = tags_of(eid)
    return "".join(LETTER[f] for f in FAMILIES if f in tags)


def has_tag(eid, family):
    """這台機有沒有 `family` 能力（default 台恆 True——零訊號＝全能力預設）。

    `family` 吃字母或全名、大小寫不敏感；**不認得或不是單一 family ⇒ False**（不炸）。
    """
    want = normalize_families([family]) if isinstance(family, str) else ()
    if len(want) != 1:
        return False
    tags, _origin = tags_of(eid)
    return want[0] in tags


def filter_eids(eids, families):
    """`eids` 依 `families` 過濾（**OR** 語意；空選＝全部）。回 list，**保留輸入順序**。

    · `families` 吃字母或全名（`["R", "kestrel"]` 可）；不認得的忽略；
      濾掉之後空掉 ⇒ 視同空選＝**全部**（不會回空表）。
    · default 台（含不在表內的）帶全三個標籤 ⇒ **任何選擇都會中**（C-PT3 的必然結果：
      「不知道」不等於「不能跑」，不該因為沒訊號就從清單消失）。
    · `eids`／`families` 傳 `str` 會 raise（逐字元迭代的腳槍）；generator 可以，
      family 集合只算一次。
    """
    if isinstance(eids, str):
        raise TypeError("filter_eids 的 eids 不吃 str（會逐字元迭代）；請傳 list/tuple/generator")
    want = normalize_families(families)
    if not want:
        return list(eids)
    fs = set(want)
    return [e for e in eids if fs & set(tags_of(e)[0])]


# ══════════════════════════════════════════════════════════════════════════════
# selftest
# ══════════════════════════════════════════════════════════════════════════════

def selftest():
    n = [0]

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            raise AssertionError(f"[{n[0]}] {msg}")

    # ── 形狀 ──────────────────────────────────────────────────────────────────
    ck(FAMILIES == ("Plover", "Kestrel", "Heron"), "FAMILIES 順序＝R/A/M（tuple 順序全檔依此）")
    ck(WINDOW_DAYS == @@WDAYS@@ and TH_SEGMENTS == @@THSEG@@ and TH_SHARE == @@THSHARE@@,
       "規則常數＝730 天／50 段／1%（改它要走 SPEC 修訂）")
    ck(set(LETTER) == set(FAMILIES) and "".join(LETTER[f] for f in FAMILIES) == "RAM",
       "LETTER 覆蓋三個 family、序＝RAM")
    ck(CENSUS_FILE == "@@CENSUS@@" and CENSUS_MD5 == "@@MD5@@",
       "來源檔身分（SPEC §3）")
    ck(CENSUS_WINDOW_END == "@@WEND@@", "窗末＝主表最大 last_seen 日期")

    for eid, rec in PRODUCT_TAGS.items():
        ck(isinstance(eid, str) and eid.startswith("EID-") and len(eid) == 9,
           f"EID 形狀 {eid}")
        ck(rec["origin"] in ("observed", "default"), f"{eid} origin enum")
        ck(rec["tags"] == tuple(f for f in FAMILIES if f in set(rec["tags"])),
           f"{eid} tags 依 FAMILIES 序")
        ck(all(f in FAMILIES for f in rec["tags"]), f"{eid} tags 只含 FAMILIES")
        ck(bool(rec["tags"]), f"{eid} tags 非空（default 台＝全三個）")
        if rec["origin"] == "default":
            ck(rec["tags"] == FAMILIES, f"{eid} default ⇒ R+A+M")
        ck(all(f in FAMILIES for f in rec["families"]), f"{eid} families 只含產品 family")
        ck(UNKNOWN_FAMILY not in rec["families"], f"{eid} UNKNOWN 不進 families（C-PT2）")
        ck(all(not c for (_st, f, _n, _s, c) in rec["lines"] if f == UNKNOWN_FAMILY),
           f"{eid} UNKNOWN 列 counted 恆 False（C-PT2）")
        ck(tuple(sorted(set(st for (st, _f, _n, _s, _c) in rec["lines"]))) == rec["stations"],
           f"{eid} stations ≡ lines 的站集合")

    # ── 普查規模釘子（★ 人工承認值；換普查檔就會紅＝設計）────────────────────
    # WP-10d（R-P11）：公開樹的普查表是合成的（EID 數／線數／observed:default 比例／具名樣本機都不同）⇒
    #   具名計數與具名樣本只在私有樹照釘；公開樹改驗表自身的結構不變式，並由表推導「樣本機」的角色。
    import core_paths as _wp
    _pub = _wp.is_public_tree()
    if not _pub:
        import board_private_pins as _pp
    n_eids = len(PRODUCT_TAGS)
    n_lines = sum(len(r["stations"]) for r in PRODUCT_TAGS.values())
    obs = sorted(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed")
    dft = sorted(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "default")
    ck(_pub or n_eids == _pp.PINS["tags.census_eids_n"], f"普查 EID 數應與私有釘值相同，實 {n_eids}")
    ck(_pub or n_lines == _pp.PINS["tags.census_lines_n"], f"(EID, station) 線數應與私有釘值相同，實 {n_lines}")
    ck(len(obs) + len(dft) == n_eids and len(dft) > 0
       and (_pub or (len(obs) == _pp.PINS["tags.observed_n"] and len(dft) == _pp.PINS["tags.default_n"])),
       f"observed/default 數應與私有釘值相同，實 {len(obs)}/{len(dft)}")
    ck(_pub or dft == _pp.PINS["tags.default_eids"], f"default 名單應與私有釘值相同，實 {dft}")

    # 線級 tag 集合分布（零訊號線投影成 R+A+M——它們最終落在 default 台上）
    dist = {}
    for rec in PRODUCT_TAGS.values():
        per_station = {}
        for (st, f, _nn, _s, c) in rec["lines"]:
            per_station.setdefault(st, set())
            if c:
                per_station[st].add(f)
        for st, s in per_station.items():
            key = tuple(f for f in FAMILIES if f in s) or FAMILIES
            dist[key] = dist.get(key, 0) + 1
    ck(_pub or dist == _pp.PINS["tags.line_family_distribution"], f"線級 tag 集合分布，實 {dist}")
    ck(("Heron",) not in dist, "零 Heron-only 線（Heron 永遠伴隨 Plover；SPEC §4 觀察）")

    # ── 抽查（SPEC §4 表的五個定錨）──────────────────────────────────────────
    if not _pub:   # 具名樣本機的事實（私有普查）；公開樹由下面的角色推導取代
        ck(tags_of("EID-55142") == (("Kestrel",), "observed"), "EID-16821＝Kestrel only")
        ck(PRODUCT_TAGS["EID-55142"]["families"]["Kestrel"] == _pp.PINS["tags.eid_16821_kestrel_segments_n"],
           "EID-16821 Kestrel 段數")
        ck(tags_of("EID-26596") == (("Kestrel",), "observed"), "EID-26596＝Kestrel only")
        ck(tags_of("EID-00425") == (("Plover",), "observed"), "EID-00425＝Plover only")
        ck(tags_of("EID-00643") == (("Plover", "Kestrel"), "observed"), "EID-00643＝Plover+Kestrel")
        ck(tags_of("EID-11860") == (("Plover",), "observed"),
           "EID-11860＝Plover（segPostPickInspection 線全 UNKNOWN、DiePlacement 線是 Plover "
           "⇒ **EID 級聯集後 observed**，不是 default）")
        ck(any(f == UNKNOWN_FAMILY for (_st, f, _nn, _s, _c) in PRODUCT_TAGS["EID-11860"]["lines"]),
           "EID-11860 確實有 UNKNOWN 列（上一條的前提）")

    # 樣本角色：私有樹＝具名機台；公開樹＝由合成普查表推導（R-only／R+A／A-only 各一台 observed）
    if _pub:
        _Diag = next(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed" and tuple(r["tags"]) == (FAMILIES[0],))
        _RA = next(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed" and tuple(r["tags"]) == FAMILIES[:2])
        _AO = next(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed" and tuple(r["tags"]) == (FAMILIES[1],))
    else:
        _Diag, _RA, _AO = "EID-00425", "EID-00643", "EID-55142"
    assert _pub or (_Diag, _RA, _AO) == ("EID-00425", "EID-00643", "EID-55142")      # 角色綁定（不計入斷言數）
    # ── 形④ 別名折疊（SPEC §2.5.3；Chris 2026-09-02 裁）────────────────────────
    import mes_timeline as _mt
    ck(EID_RENAME is _mt.EID_RENAME and EID_ALIAS_DROP is _mt.EID_ALIAS_DROP,
       "★★ 別名表 **import 同一個物件**（本檔不得另抄一份＝第二個會漂移的事實）")
    ck(set(ALIAS_OF) == set(EID_RENAME.values()),
       f"ALIAS_OF 只含機制 (a) 改名型主 EID，實 {sorted(ALIAS_OF)}")
    import board_capacity as _wcp
    _fm = next(iter(EID_RENAME.values()))
    _ren = next(iter(EID_RENAME))
    _fa = tuple(ALIAS_OF[_fm])
    _b_list = tuple(_wcp._ALIGNER_ALIASES.values())      # 機制 (b) 主 EID（私有樹＝07940／07905／26554）
    _exp_tags = tuple(f for f in FAMILIES
                      if any(f in PRODUCT_TAGS[a]["tags"] for a in _fa if PRODUCT_TAGS[a]["origin"] == "observed"))
    ck(ALIAS_OF.get(_fm) == _fa and (_pub or (_fm == @@PIN_FOLD_MAIN@@ and _fa == @@PIN_FOLD_ALIASES@@)),
       f"{_fm} 的別名＝rename ＋ drop 兩顆，實 {ALIAS_OF.get(_fm)}")
    ck(all(m not in PRODUCT_TAGS for m in ALIAS_OF),
       "改名主 EID 在普查表內沒有自己的列（＝本規則存在的理由；MES 段記在別名編號上）")
    ck(set(FOLDED_TAGS) == set(ALIAS_OF), f"每個改名主 EID 都折得出記錄，實 {sorted(FOLDED_TAGS)}")
    for _b in _b_list:
        ck(_b not in ALIAS_OF and _b not in FOLDED_TAGS,
           f"機制 (b) 主 EID {_b} 完全不受折疊影響（SPEC §2.5.3：它們本來就有自己的超集段）")
    ck(tags_of(_b_list[-1]) == ((tuple(PRODUCT_TAGS[_b_list[-1]]["tags"]), PRODUCT_TAGS[_b_list[-1]]["origin"])
                               if _b_list[-1] in PRODUCT_TAGS else (FAMILIES, "default"))
       and (_pub or tags_of(_b_list[-1]) == (FAMILIES, "default")),
       "26554 照舊 default（機制 (b) 一個位元不動）")
    _fold = record_of(_fm)
    ck(_fold is not None and _fold["origin"] == "observed",
       f"{_fm} 折疊後＝observed（不是「沒量到」，是「記在別的編號上」）")
    ck(tags_of(_fm) == (_exp_tags, "observed"),
       f"{_fm} 標籤＝別名 observed 聯集，實 {tags_of(_fm)}")
    ck(_fold["families"] == PRODUCT_TAGS[_ren]["families"]
       and (_pub or _fold["families"] == {"Kestrel": _pp.PINS["tags.folded_kestrel_segments_n"]}),
       "★★ 折疊段數＝**取計數主**（@@PIN_FOLD_COUNTED@@）、別名段不另計"
       f"（SPEC-RUNWINDOW §10.8-8 機制 (b)：加總＝雙算），實 {_fold['families']}")
    ck(_fold["counted_from"] == (_ren,) and (_pub or _fold["counted_from"] == @@PIN_FOLD_COUNTED_T@@),
       f"counted_from＝只有 EID_RENAME 來源貢獻段數，實 {_fold['counted_from']}")
    ck(all(a not in EID_RENAME or a in _fold["counted_from"] for a in _fa)
       and all(a not in _fold["counted_from"] for a in _fa if a in EID_ALIAS_DROP),
       "EID_ALIAS_DROP 的別名一顆都不進計數（子集、不另計）")
    ck(_fold["folded_from"] == _fa and all(a in _fold["facts"] for a in _fa),
       f"facts／folded_from 註明來自哪些別名，實 {_fold['facts']!r}")
    _al = [PRODUCT_TAGS[a] for a in _fa]
    _cm = [PRODUCT_TAGS[a] for a in _fold["counted_from"]]
    ck(_fold["families"]["Kestrel"] < sum(r["families"]["Kestrel"] for r in _al),
       "★ 反雙算釘：折疊段數**嚴格小於**兩顆別名相加（相加曾是本檔的 bug）")
    ck(_fold["stations"] == tuple(sorted(set(s for r in _al for s in r["stations"])))
       and _fold["first_seen"] == min(r["first_seen"] for r in _al)
       and _fold["last_seen"] == max(r["last_seen"] for r in _al),
       "站集合／標籤聯集、first/last 取 min/max（這三項吃全部 observed 別名）")
    ck(len(_fold["lines"]) == sum(len(r["lines"]) for r in _cm),
       "lines＝只有計數主的列（機制 (b) 別名的線不接上來）")
    _nf = next((f for f in FAMILIES if f not in _exp_tags), None)
    ck(letters_of(_fm) == "".join(LETTER[f] for f in _exp_tags) and has_tag(_fm, LETTER[_exp_tags[0]])
       and (_nf is None or not has_tag(_fm, _nf)) and (_pub or (letters_of(_fm) == "A" and not has_tag(_fm, "Plover"))),
       "letters_of／has_tag 一併吃到折疊結果（都走 tags_of）")
    ck(_fm not in PRODUCT_TAGS,
       "PRODUCT_TAGS 仍是**純普查**（折疊結果只住 FOLDED_TAGS）")
    for _a in _fa:
        ck(record_of(_a) is PRODUCT_TAGS[_a], f"別名 {_a} 自身仍回它自己的普查列、不被折疊改寫")
    ck(record_of("EID-99999") is None, "表外 ⇒ record_of 回 None")
    ck(_fold_alias("EID-99999", ()) is None,
       "零貢獻者 ⇒ None ⇒ 照走 §2.5 的 default（不假裝有根據）")
    ck(_fold_alias("EID-99999", (dft[0],)) is None,
       "★ 只吃 observed 別名：default 別名不進聯集（§7 A11 吸收元陷阱）")

    # ── 查詢層 ────────────────────────────────────────────────────────────────
    ck(tags_of("EID-99999") == (FAMILIES, "default"), "不在表內 ⇒ (FAMILIES, default)")
    ck(tags_of("") == (FAMILIES, "default"), "空字串一樣走 default，不炸")
    ck(tags_of(_Diag.lower()) == (FAMILIES, "default"), "鍵大小寫敏感、不正規化 ⇒ 走 default")
    ck(letters_of(_RA) == "RA", "letters_of＝FAMILIES 序縮寫")
    ck(letters_of("EID-99999") == "RAM", "不在表內 ⇒ RAM")
    ck(letters_of(dft[0]) == "RAM", "表內 default 台 ⇒ RAM（與表外同形）")
    ck(has_tag(_Diag, "Plover") and not has_tag(_Diag, "Heron"),
       "has_tag 正負各一")
    ck(has_tag(_Diag, "R") and has_tag(_Diag, "plover"),
       "has_tag 吃字母與小寫全名")
    ck(has_tag("EID-99999", "Heron"), "不在表內 ⇒ 三個 family 都 True")
    ck(not has_tag(_Diag, "Nessie"), "不認得的 family ⇒ False（不炸）")
    ck(not has_tag(_Diag, "RA") and not has_tag(_Diag, None),
       "has_tag 只吃單一 family；連寫或 None ⇒ False")

    # normalize_families / parse_tag_param（SPEC §6.2）
    ck(normalize_families(["R", "a"]) == ("Plover", "Kestrel"), "字母、大小寫不敏感")
    ck(normalize_families(["kestrel", "HERON"]) == ("Kestrel", "Heron"), "全名、大小寫不敏感")
    ck(normalize_families(["m", "R", "Plover"]) == ("Plover", "Heron"), "去重＋恆 FAMILIES 序")
    ck(normalize_families(["zzz", 7, None]) == (), "不認得／非字串一律忽略")
    ck(normalize_families(None) == () and normalize_families([]) == (), "None／空 ⇒ ()")
    ck(normalize_families(f for f in ["R"]) == ("Plover",), "吃 generator")
    try:
        normalize_families("Plover")
        _raised = False
    except TypeError:
        _raised = True
    ck(_raised, "normalize_families 傳 str ⇒ TypeError（逐字元迭代腳槍）")
    ck(parse_tag_param("R,a") == ("Plover", "Kestrel"), "?tag=R,a")
    ck(parse_tag_param("kestrel") == ("Kestrel",), "?tag=kestrel")
    ck(parse_tag_param("m , R") == ("Plover", "Heron"), "空白與逗號混用、回傳恆 FAMILIES 序")
    ck(parse_tag_param("R+M") == ("Plover", "Heron"), "加號也是分隔符")
    ck(parse_tag_param("R,zzz") == ("Plover",), "不認得的字樣忽略")
    ck(parse_tag_param("") == () and parse_tag_param("   ") == () and parse_tag_param(None) == (),
       "空／空白／None ⇒ ()＝沒選＝全部")
    ck(parse_tag_param("zzz") == (), "全不認得 ⇒ ()（呼叫端據此回全部）")
    ck(parse_tag_param(["R"]) == ("Plover",), "非字串就退回 normalize_families")

    pool = [_Diag, _RA, _AO, "EID-99999", dft[0]]
    ck(filter_eids(pool, []) == pool, "空選＝全部、且保留輸入順序")
    ck(filter_eids(pool, None) == pool, "None 一樣＝全部")
    ck(filter_eids(pool, ["Nessie"]) == pool, "全不認得 ⇒ 視同空選＝全部（不回空表）")
    ck(filter_eids(pool, ["Kestrel"]) == [_RA, _AO, "EID-99999", dft[0]],
       "Kestrel 過濾：Plover-only 掉、default 台留（C-PT3）")
    ck(filter_eids(pool, ["A"]) == filter_eids(pool, ["kestrel"]) == filter_eids(pool, ["Kestrel"]),
       "字母／小寫全名／全名三種寫法同結果")
    ck(filter_eids(pool, parse_tag_param("A")) == filter_eids(pool, ["Kestrel"]),
       "parse_tag_param 的輸出可直餵 filter_eids（§6.2 動線）")
    ck(filter_eids(pool, ["Plover", "Heron"]) == [_Diag, _RA, "EID-99999", dft[0]],
       "多選＝OR")
    ck(filter_eids([], ["Plover"]) == [], "空輸入 ⇒ 空輸出")
    ck(filter_eids((e for e in pool), ["Kestrel"]) == filter_eids(pool, ["Kestrel"]),
       "eids 吃 generator（family 集合只算一次）")
    for _bad in (_Diag, "R"):
        try:
            filter_eids(_bad, ["Plover"])
            _raised = False
        except TypeError:
            _raised = True
        ck(_raised, f"filter_eids 的 eids 傳 str ⇒ TypeError（{_bad!r}）")

    # ── 規則邊界（門檻是 OR；分母含 UNKNOWN）──────────────────────────────────
    # ⚠ 只 import 產生器的**純函式**重算（零 MES、零網路）；路徑自解析，不依賴 cwd。
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import _gen_product_tags as _gen
    _cf = _gen.counted_families
    U = UNKNOWN_FAMILY
    ck(_cf({"Plover": 49, U: 9951}) == (), "49 段且 0.49% ⇒ 不計（兩條都沒過）")
    ck(_cf({"Plover": 50, U: 9950}) == ("Plover",), "50 段 ⇒ 計（絕對段數腿）")
    ck(_cf({"Plover": 1, U: 100}) == (), "1/101＝0.99% 且 <50 段 ⇒ 不計（佔比腿差一點）")
    ck(_cf({"Plover": 1, U: 99}) == ("Plover",), "1/100＝1% 且 <50 段 ⇒ 計（佔比腿剛好過）")
    ck(_cf({"Plover": 49, "Kestrel": 51, U: 9900}) == ("Kestrel",), "同線兩 family 各自判")
    ck(_cf({"Kestrel": 60, "Plover": 60}) == ("Plover", "Kestrel"), "回傳依 FAMILIES 序、非輸入序")
    ck(_cf({U: 8000}) == (), "整線只有 UNKNOWN ⇒ 空（C-PT2）")
    ck(_cf({}) == () and _cf({"Plover": 0}) == (), "空線／零段 ⇒ 空、不除零")
    ck(_cf({"Plover": 3, "Kestrel": 1}) == ("Plover", "Kestrel"),
       "小總量線：3/4 與 1/4 都過 1% ⇒ 佔比腿救得起小段數")
    ck(_gen.line_share({"Plover": 1, U: 3}, "Plover") == 0.25
       and _gen.line_share({}, "Plover") == 0.0, "line_share 分母含 UNKNOWN、零段不除零")

    # ── 來源檔完整性（y1／y2 逐站守恆；QA 表不進計算）─────────────────────────
    # 普查檔不存在（例：脫敏公開樹不帶原始普查）⇒ 標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    if not _os.path.exists(_os.path.join(_here, CENSUS_FILE)):
        print("board_tags selftest: skipped: census file absent (%s)" % CENSUS_FILE)
    else:
        _rows, _y1, _y2, _md5, _wend, _stats = _gen.load_census()
        ck(_md5 == CENSUS_MD5, f"普查檔 md5 與本檔釘的一致，實 {_md5}")
        ck(_wend == CENSUS_WINDOW_END, "窗末一致")
        ck(_stats["main_rows"] == _pp.PINS["tags.census_main_rows_n"],
           f"主表列數應與私有釘值相同，實 {_stats['main_rows']}")
        ck(_stats["stations"] == _pp.PINS["tags.census_stations_n"],
           f"QA 表站數應與私有釘值相同，實 {_stats['stations']}")
        ck(_stats["eids"] == _pp.PINS["tags.census_eids_n"], "主表 distinct EID ≡ 私有釘值")
        ck(_stats["fanout_total"] == _pp.PINS["tags.fanout_total_n"]
           and _stats["fanout_stations"] == _pp.PINS["tags.fanout_stations_n"]
           and _stats["fanout_max"] == _pp.PINS["tags.fanout_max_n"],
           f"y2−y1 join 放大形狀應與私有釘值相同，實 {_stats}")
        ck(_gen.build_tags(_rows) == PRODUCT_TAGS, "從普查重算 ≡ 本檔 PRODUCT_TAGS（逐台逐欄）")
        try:
            _gen.check_integrity(_rows[:-1], _y1, _y2)
            _raised = False
        except ValueError:
            _raised = True
        ck(_raised, "抽掉主表一列 ⇒ 逐站守恆閘應 raise（閘是活的、不是裝飾）")
        try:
            _gen.split_blocks("a\\tb\\n\\nc\\td")
            _raised = False
        except ValueError:
            _raised = True
        ck(_raised, "block 數不是 3 ⇒ raise")

        # ── 產生器 --check（本檔確實是那份普查生出來的、不是手改過的）─────────────
        ck(_gen.main(["--check"]) == 0, "產生器 --check 應回 0（本檔＝普查的決定性產物、無手改）")

    print(f"board_tags selftest OK ({n[0]} checks)")
    return n[0]


if __name__ == "__main__":
    import argparse

    try:
        for _s in (sys.stdout, sys.stderr):
            if (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
                _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        _obs = sum(1 for r in PRODUCT_TAGS.values() if r["origin"] == "observed")
        print(f"board_tags: {len(PRODUCT_TAGS)} EIDs "
              f"({_obs} observed / {len(PRODUCT_TAGS) - _obs} default) "
              f"from {CENSUS_FILE} (md5 {CENSUS_MD5[:8]}..., window end {CENSUS_WINDOW_END})")
'''

# ── 人工承認的釘子（★ 換普查檔＝人看過再改這裡；不准為了讓測試綠反改釘子）──────
# 普查計數類釘值（EID 數、線數、observed/default、線級分布、具名機段數、主表形狀）住私有專用的
# `board_private_pins.PINS["tags.*"]`（不進公開樹）；模板的私有分支在執行時讀它、這裡只留 EID 類釘子。
_PINS = {
    # 形④ 別名折疊（SPEC §2.5.3）：主 EID `38106` 的標籤＝`38041`＋`38072` 的 observed **聯集**；
    # 段數**只取計數主** `38041`（V3＝155；V2 為 156——窗右移一天、非口徑變）——
    # `38072`（141）是機制 (b)、段是子集、**不另計**
    # （`SPEC-RUNWINDOW.md` §10.8-8：「不是加總。加總＝雙算」）。曾誤寫兩者相加 ≈ 2× 真值。
    "PIN_FOLD_MAIN": '"EID-55376"',
    "PIN_FOLD_ALIASES": '("EID-55272", "EID-54321")',
    "PIN_FOLD_COUNTED": "EID-55272",
    "PIN_FOLD_COUNTED_T": '("EID-55272",)',
}


# ══════════════════════════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════════════════════════

def _tup(items):
    if not items:
        return "()"
    if len(items) == 1:
        return "(%s,)" % items[0]
    return "(" + ", ".join(items) + ")"


def _s(x):
    """字串 → Python literal（普查值全是 ASCII 識別字/日期，用雙引號足矣）。"""
    if x is None:
        return "None"
    if '"' in x or "\\" in x:
        raise ValueError("來源值含引號或反斜線，渲染器不處理：%r" % x)
    return '"%s"' % x


def render_data(tags):
    out = ["PRODUCT_TAGS = {"]
    for eid in sorted(tags):
        rec = tags[eid]
        out.append('    %s: {' % _s(eid))
        out.append('        "tags": %s,' % _tup([_s(f) for f in rec["tags"]]))
        out.append('        "origin": %s,' % _s(rec["origin"]))
        out.append('        "families": {%s},'
                   % ", ".join("%s: %d" % (_s(f), rec["families"][f])
                               for f in FAMILIES if f in rec["families"]))
        out.append('        "stations": %s,' % _tup([_s(s) for s in rec["stations"]]))
        out.append('        "first_seen": %s,' % _s(rec["first_seen"]))
        out.append('        "last_seen": %s,' % _s(rec["last_seen"]))
        out.append('        "lines": (')
        for (st, fam, n, share, counted) in rec["lines"]:
            out.append('            (%s, %s, %d, %s, %s),'
                       % (_s(st), _s(fam), n, repr(share), "True" if counted else "False"))
        out.append('        ),')
        out.append('    },')
    out.append("}")
    return "\n".join(out) + "\n"


def render_module(tags, census_name, md5, wend):
    subs = {
        "CENSUS": census_name,
        "MD5": md5,
        "WEND": wend,
        "WDAYS": str(WINDOW_DAYS),
        "THSEG": str(TH_SEGMENTS),
        "THSHARE": repr(TH_SHARE),
    }
    subs.update(_PINS)
    text = _HEADER + _CONSTS + _DATA_DOC + render_data(tags) + _BODY
    for k, v in subs.items():
        text = text.replace("@@%s@@" % k, v)
    if "@@" in text:
        raise ValueError("模板還有未替換的佔位符")
    return text


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main(argv=None):
    ap = argparse.ArgumentParser(description="產品能力標籤對照檔產生器（寫 board_tags.py）")
    ap.add_argument("census", nargs="?", default=None,
                    help="普查來源檔（預設 %s）" % DEFAULT_CENSUS)
    ap.add_argument("--check", action="store_true",
                    help="只比對現有 board_tags.py 是否與重生內容一致、不寫檔（0/1）")
    a = ap.parse_args(argv)

    census_path = a.census or os.path.join(HERE, DEFAULT_CENSUS)
    if not os.path.isabs(census_path):
        cand = os.path.join(HERE, census_path)
        census_path = cand if os.path.exists(cand) else os.path.abspath(census_path)
    if not os.path.exists(census_path):
        _say("找不到普查檔：%s" % census_path, sys.stderr)
        return 2

    rows, _y1, _y2, md5, wend, stats = load_census(census_path)
    tags = build_tags(rows)
    module = render_module(tags, os.path.basename(census_path), md5, wend)
    blob = module.encode("utf-8")            # ★ 永遠 LF（模板全是 \n、不經 os.linesep）

    out_path = os.path.join(HERE, OUT_MODULE)
    if a.check:
        if not os.path.exists(out_path):
            _say("CHECK FAIL：%s 不存在" % OUT_MODULE, sys.stderr)
            return 1
        cur = open(out_path, "rb").read()
        # ★ F1：行尾正規化後再比。fresh checkout 在 core.autocrlf=true 下會被寫成 CRLF，
        #    那是行尾不是內容，不該報成「有人手改」。
        norm = cur.replace(b"\r\n", b"\n")
        if norm == blob:
            note = "" if norm == cur else "（工作樹是 CRLF、已正規化後比對；.gitattributes 已釘 eol=lf）"
            _say("CHECK OK：%s ≡ %s 的決定性產物（md5 %s）%s"
                 % (OUT_MODULE, os.path.basename(census_path), md5, note))
            return 0
        _say("CHECK FAIL：%s 與重生內容不同（正規化後 %d vs %d bytes）——有人手改，或普查檔換了。"
             "修法＝跑 `python _gen_product_tags.py` 重生（C-PT1）。"
             % (OUT_MODULE, len(norm), len(blob)), sys.stderr)
        return 1

    open(out_path, "wb").write(blob)
    obs = sum(1 for r in tags.values() if r["origin"] == "observed")
    n_lines = sum(len(r["stations"]) for r in tags.values())
    _say("寫出 %s：%d 台（%d observed / %d default）、%d 線、窗末 %s、來源 %s（md5 %s）"
         % (OUT_MODULE, len(tags), obs, len(tags) - obs, n_lines, wend,
            os.path.basename(census_path), md5))
    _say("  輸入閘：主表 %d 列／%d 站；y1 distinct_eids 與 y2 raw_rows 逐站守恆 ✓；"
         "y2−y1 join 放大 %d 列（%d 站、單站最大 %d、%.6f%%）"
         % (stats["main_rows"], stats["stations"], stats["fanout_total"],
            stats["fanout_stations"], stats["fanout_max"],
            100.0 * stats["fanout_total"] / max(stats["y1_total"], 1)))
    return 0


if __name__ == "__main__":
    _utf8_stdout()
    sys.exit(main())
