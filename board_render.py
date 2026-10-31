# -*- coding: utf-8 -*-
r"""
board_render.py — 助理回覆安全渲染器（Hermes sidebar T1 軌；防注入 —— 不信任 agent 輸出）
==============================================================================================

母本＝cmms `assistant_render.py`（ADR-020，跨 repo 讀合法、整個抄形），Board 適配：連結白名單
換成 Tendline Board 前門路徑集、裸 EID 自動連到 `/updown`（逐機台現況頁）。純函式、零依賴、
標準庫 only。

`render_reply(text)` 把 Hermes 回覆的**受限 markdown 子集**轉成安全 HTML，窄聊天欄可讀：
  1. 全段 HTML-escape（**先**），故任何 `<script>` / 屬性逃逸都失效。
  2. `[label](/path)` → `<a>`，**href 白名單＝Board 前門路徑集**（見 ALLOWED_PATH_PREFIXES /
     EXACT_PATHS）；拒 `javascript:` / `//host` / 外部 http / 協定相對 / 非白名單路徑
     （`/app/…`、`/assistant`、`/api`、`/mcp` …）→ 整段保留為純文字（escape 後）。
  3. 裸 `EID-\d{5}`（不在既有連結內）→ 自動連到 `/updown`。
  4. `**粗體**` → `<strong>`。
  5. 換行**不**轉 `<br>`（泡泡沿用 CSS white-space: pre-wrap）。
  6. **markdown 表格（GFM 子集）→ `<table class="board-tbl">`**（2026-09-22 #154；裁決存證＝
     `SPEC-HERMES-TRUST.md` **§12.8**）。寬版 persona 准表格（☐10），渲染器不補這一類
     白名單的話使用者看到的就是原始 pipe 字元。逐條規則：
       · 塊＝**header 列 ＋ 分隔列 ＋ 0..n 資料列**，三者連續行；**分隔列必要**、且欄數
         必須等於 header 欄數，否則整段**照舊走純文字**（不是表格就什麼都不做）。
       · 塊在第一個「不含 `|`」的行（空行亦然）結束；塊**吃掉緊鄰的一個前導 `\n` 與一個
         尾隨 `\n`**（宿主 `.chat-msg` 是 pre-wrap，不吃會多出可見空行）；其它換行原樣保留。
       · cell 以**未跳脫**的 `|` 切、`\|` 還原成字面 `|`；頭尾 pipe 可有可無；資料列**短補
         空、長截掉**（GFM 行為）。對齊記號（`:---:`）**容忍但不執行**（不出 style／align／class）。
       · **每個 cell 的內容都走上面 1–4 的完整管線**（escape-first、連結白名單、裸 EID、粗體）
         ⇒ 表格裡的連結照樣是連結、`<script>` 照樣被 escape。
       · **不執行 persona 的 8 列／4 欄上限、不截斷**（截列＝改事實，C-HT1）；溢出交 CSS。
       · **不包 wrapper `<div>`**、輸出零 `<div`（`run_smoke` 三根 bot 泡泡釘用非貪婪
         `(.*?)</div>`，出現 `</div>` 會抓到半截）；表格 HTML 內部**零換行**。
       · 渲染器**不知道 layout、也不收 layout 參數**：窄寬同一份 HTML（dock 收到表格＝模型
         違反窄版 persona，捲得動的表格仍比原始 pipe 好）。

輸出已由本模組 escape，模板以 `|safe` 輸出。**使用者訊息泡泡不走本渲染器**（維持純
autoescape）—— 只有 agent 回覆需要這些連結能力。
"""

from __future__ import annotations

import html
import re
import sys

# Windows 主控台預設 cp1252 → 繁中 print 會炸；比照 repo 慣例把 stdout 轉 utf-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 連結白名單（Board 前門路徑集；做成模組頂層常數＝好擴充）
# ---------------------------------------------------------------------------

# 精確集合：整段 href 必須完全相等（不接後綴）
EXACT_PATHS: frozenset[str] = frozenset({"/"})

# 前綴集合：href（去 query 後的路徑主體）以此開頭即算站內白名單。
#   - 以 '/' 結尾者（如 '/station/'）＝必須有後續段（cls）。
#   - 不以 '/' 結尾者（如 '/output'）＝精確相等，或後接 '/' 子路徑。
#   query（'?…'）一律允許，字元白名單另外把關。'/daily' 為預留新 landing。
ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "/output",
    "/yield",
    "/utilization",
    "/updown",
    "/station/",
    "/daily",
)

# [label](href)：label 不含 ']'；href 不含 ')' 與空白
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# 粗體（非貪婪、單行內）；於已 escape 文字上操作（'*' 不受 escape 影響）
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# 裸 EID：前後不接 word char / '/' / '-'，避免已在連結內或誤切（於已 escape 文字上操作）
_EID_RE = re.compile(r"(?<![\w/-])(EID-\d{5})(?![\w-])")
# 站內路徑合法字元白名單（不含 ':'、空白 → 天然排除 scheme）
_PATH_RE = re.compile(r"/[A-Za-z0-9/_\-.?=&]*")
# 表格分隔列（#154）：容忍頭尾 pipe 有無、`:---`／`---:`／`:---:`、任意長度 '-'（≥1）、空白。
#   ⚠ 它**不**負責欄數比對——欄數由 `_split_cells` 數，與 header 不等即整塊不是表格。
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")

# 需剝除的不可見/危險控制字元（html.escape 不處理＝視覺欺騙面）：
#   C0 控制碼（保留 \t \n）、DEL+C1、bidi 覆寫（LRM/RLM/LRE…RLO/PDF/LRI…PDI）。
_STRIP_ORDS: frozenset[int] = frozenset(
    (set(range(0x00, 0x20)) - {0x09, 0x0A})      # C0 控制碼（保留 tab/newline）
    | set(range(0x7F, 0xA0))                     # DEL + C1 控制碼
    | {0x200E, 0x200F}                           # LRM / RLM
    | set(range(0x202A, 0x202F))                 # LRE/RLE/PDF/LRO/RLO
    | set(range(0x2066, 0x206A))                 # LRI/RLI/FSI/PDI
)


def _strip_unsafe(s: str) -> str:
    """剝除不可見/雙向覆寫控制字元（防視覺欺騙）；保留 \\n \\t。純函式、無訊號時零成本。"""
    if not any(ord(c) in _STRIP_ORDS for c in s):
        return s
    return "".join(c for c in s if ord(c) not in _STRIP_ORDS)


def _safe_board_path(href: str) -> str | None:
    """回站內相對路徑（通過 Board 白名單）否則 None。純函式、易擴充。"""
    href = href.strip()
    if not href:
        return None
    # 必須是站內絕對路徑，且不得為協定相對 '//host'
    if not href.startswith("/") or href.startswith("//"):
        return None
    # 拒絕任何含 '..' 區段的路徑（防目錄穿越；_PATH_RE 白名單含 '.'＝前綴比對擋不住 '/station/../api'）
    if ".." in href.split("?", 1)[0].split("/"):
        return None
    # 字元白名單（':'/空白 不在集內 → 天然擋掉 scheme 與 'javascript:'）
    if not _PATH_RE.fullmatch(href):
        return None
    # 精確集合（如 '/'）
    if href in EXACT_PATHS:
        return href
    # 路徑主體（切掉 query 再比對前綴）
    path = href.split("?", 1)[0]
    for pref in ALLOWED_PATH_PREFIXES:
        if pref.endswith("/"):
            # 如 '/station/'：需有後續 cls 段
            if path.startswith(pref) and len(path) > len(pref):
                return href
        else:
            # 如 '/output'：精確相等或後接 '/' 子路徑
            if path == pref or path.startswith(pref + "/"):
                return href
    return None


def _render_inline(segment: str) -> str:
    """非連結文字段：剝控制/bidi → escape → 粗體 → 裸 EID 自動連結。輸入為原文，輸出安全 HTML。"""
    escaped = html.escape(_strip_unsafe(segment))
    escaped = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    # 裸 EID → /updown（逐機台現況頁）；label 顯示原 EID 字串
    escaped = _EID_RE.sub(lambda m: f'<a href="/updown">{m.group(1)}</a>', escaped)
    return escaped


def _render_flow(text: str) -> str:
    """**非表格**的一般文字流：連結白名單切段 → 每段走 `_render_inline`。

    ⚠ 這就是 #154 之前的 `render_reply` 本體（**行為逐位元不變**，只改名）。表格的每個
    cell 也呼叫它 ⇒ 表格裡的連結／裸 EID／粗體／escape 與泡泡其餘地方**同一套規則、同一份碼**。
    """
    if not text:
        return ""
    out: list[str] = []
    pos = 0
    for m in _LINK_RE.finditer(text):
        out.append(_render_inline(text[pos : m.start()]))  # 連結前的一般文字
        label, href = m.group(1), m.group(2)
        safe = _safe_board_path(href)
        if safe is not None:
            out.append(f'<a href="{html.escape(safe, quote=True)}">{html.escape(label)}</a>')
        else:
            out.append(html.escape(m.group(0)))  # href 不合白名單 → 整段當純文字
        pos = m.end()
    out.append(_render_inline(text[pos:]))  # 尾段
    return "".join(out)


# ---------------------------------------------------------------------------
# markdown 表格（GFM 子集；#154／`SPEC-HERMES-TRUST.md` §12.8）
#   逐行、在 `_LINK_RE` 切段**之前**於原文層辨識 ⇒ cell 邊界不會被連結語法弄亂。
# ---------------------------------------------------------------------------

def _split_cells(line: str) -> list[str]:
    """以**未被反斜線跳脫**的 `|` 切 cell；`\\|` 還原成字面 `|`；頭尾 pipe 可有可無。

    回傳已 strip 的 cell 原文（**尚未 escape**——交給 `_render_flow`）。
    """
    s = line.strip()
    cells: list[str] = []
    buf: list[str] = []
    ended_delim = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")            # 跳脫的 pipe＝字面內容、不切
            i += 2
            ended_delim = False
            continue
        if c == "|":
            cells.append("".join(buf))
            buf = []
            i += 1
            ended_delim = True
            continue
        buf.append(c)
        i += 1
        ended_delim = False
    cells.append("".join(buf))
    if s.startswith("|") and cells and cells[0] == "":
        cells.pop(0)                   # 頭 pipe 產生的空 cell
    if ended_delim and cells and cells[-1] == "":
        cells.pop()                    # 尾 pipe 產生的空 cell
    return [c.strip() for c in cells]


def _match_table(lines: list[str], i: int) -> tuple[str, int] | None:
    """`lines[i]` 起是不是一個表格塊？是 → (表格 HTML, 塊結束後的行號)；否 → None。"""
    n = len(lines)
    if i + 1 >= n:
        return None
    head_line = lines[i]
    if "|" not in head_line:
        return None                    # header 列至少要有一個 '|'
    sep_line = lines[i + 1]
    if not _TABLE_SEP_RE.match(sep_line):
        return None                    # **分隔列必要**：沒有＝不是表格
    head_cells = _split_cells(head_line)
    if not head_cells:
        return None
    if len(_split_cells(sep_line)) != len(head_cells):
        return None                    # 欄數不符＝整塊不是表格（照舊純文字）
    j = i + 2
    rows: list[list[str]] = []
    while j < n and "|" in lines[j]:   # 第一個「不含 '|'」的行（空行亦然）結束
        rows.append(_split_cells(lines[j]))
        j += 1
    return _render_table(head_cells, rows), j


def _render_table(head_cells: list[str], rows: list[list[str]]) -> str:
    """表格 HTML（**零 wrapper div、零換行、零屬性**，只有一個 `class="board-tbl"`）。"""
    ncol = len(head_cells)
    out = ['<table class="board-tbl"><thead><tr>']
    out += [f"<th>{_render_flow(c)}</th>" for c in head_cells]
    out.append("</tr></thead>")
    if rows:                           # 零資料列 → 只有 thead（仍渲染）
        out.append("<tbody>")
        for row in rows:
            cells = (row + [""] * ncol)[:ncol]   # 短補空、長截掉多的（GFM）
            out.append("<tr>")
            out += [f"<td>{_render_flow(c)}</td>" for c in cells]
            out.append("</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out)


def render_reply(text: str) -> str:
    """把受限 markdown 子集轉安全 HTML（見模組 docstring）。空輸入 → 空字串。

    形＝**先掃表格塊**（原文層、逐行），非表格區段與每個 cell 都交給 `_render_flow`
    ⇒ 沒有表格時輸出與 #154 之前**逐位元相同**。
    """
    if not text:
        return ""
    lines = text.split("\n")
    parts: list[str] = []
    pending: list[str] = []            # 尚未輸出的純文字行
    i, n = 0, len(lines)
    while i < n:
        hit = _match_table(lines, i)
        if hit is None:
            pending.append(lines[i])
            i += 1
            continue
        table_html, j = hit
        if pending:
            # 這裡**不補 "\n"** ＝吃掉塊的前導換行（pre-wrap 下否則多一條可見空行）
            parts.append(_render_flow("\n".join(pending)))
            pending = []
        parts.append(table_html)
        i = j                          # 下一段純文字直接接上＝尾隨換行同樣被吃掉
    if pending:
        parts.append(_render_flow("\n".join(pending)))
    return "".join(parts)


# ---------------------------------------------------------------------------
# selftest（純函式、零 I/O、deterministic）
# ---------------------------------------------------------------------------

def _selftest() -> None:
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # ---- 移植 cmms 13 case（路徑換 Board） ----

    # 1) 空輸入 → 空字串（含 None）
    ck(render_reply("") == "", "空字串應回空")
    ck(render_reply(None) == "", "None 應回空")  # type: ignore[arg-type]

    # 2) 純文字全 escape（XSS）
    o = render_reply("<script>alert(1)</script>")
    ck("<script>" not in o, "script 標籤不得漏出")
    ck("&lt;script&gt;alert(1)&lt;/script&gt;" in o, "script 應被 escape")

    # 3) label 內屬性字元被 escape（href 合法）
    o = render_reply('["onmouseover=x](/station/CAL)')
    ck('<a href="/station/CAL">' in o, "合法 station 連結應成 <a>")
    ck("onmouseover=x" in o, "label 純文字保留、不成屬性")

    # 4) 合法站內連結
    o = render_reply("see [Output](/output) now")
    ck('<a href="/output">Output</a>' in o, "合法 /output 連結")

    # 5) javascript: href 拒絕（整段當純文字）
    o = render_reply("[click](javascript:alert(1))")
    ck("<a" not in o, "javascript: 不得成連結")
    ck("javascript" in o, "javascript: 整段當純文字")

    # 6) 外部 http 拒絕
    o = render_reply("[evil](https://evil.example/output)")
    ck("<a" not in o, "外部 http 不得成連結")
    ck("evil.example" in o, "外部 host 保留為純文字")

    # 7) 協定相對 //host 拒絕
    o = render_reply("[x](//evil.example)")
    ck("<a" not in o, "協定相對 //host 不得成連結")

    # 8) 非白名單路徑拒絕
    o = render_reply("[home](/admin/secrets)")
    ck("<a" not in o, "非白名單路徑不得成連結")
    ck("/admin/secrets" in o, "非白名單路徑保留為純文字")

    # 9) 裸 EID 自動連結 → /updown
    o = render_reply("check EID-24821 please")
    ck('<a href="/updown">EID-24821</a>' in o, "裸 EID 應連到 /updown")

    # 10) 連結內 EID 不重複連（label 內的 EID 不被裸規則二次包）
    o = render_reply("[EID-24821](/updown)")
    ck(o.count("<a ") == 1, "既有連結內的 EID 不得二次連結")

    # 11) 粗體
    o = render_reply("this is **important** ok")
    ck("<strong>important</strong>" in o, "粗體應轉 <strong>")

    # 12) 換行不轉 <br>
    o = render_reply("line1\nline2")
    ck("<br" not in o, "換行不得轉 <br>")
    ck("line1\nline2" in o, "換行原樣保留（CSS pre-wrap）")

    # 13) 混合案例
    text = "Found **2** work orders:\n[Yield](/yield) on EID-24821\n<b>x</b>"
    o = render_reply(text)
    ck("<strong>2</strong>" in o, "混合：粗體")
    ck('<a href="/yield">Yield</a>' in o, "混合：合法連結")
    ck('<a href="/updown">EID-24821</a>' in o, "混合：裸 EID")
    ck("&lt;b&gt;x&lt;/b&gt;" in o and "<b>x</b>" not in o, "混合：raw HTML 被 escape")

    # ---- Board 新增 case ----

    # 14) /station/<cls> 通過
    o = render_reply("[CAL](/station/segCalibrationTest)")
    ck('<a href="/station/segCalibrationTest">CAL</a>' in o, "station/<cls> 應通過")

    # 15) /app/... 被拒（降純文字）
    o = render_reply("[x](/app/equipment/EID-00884)")
    ck("<a" not in o, "/app/ 路徑應被拒")
    ck("/app/equipment/EID-00884" in o, "/app/ 路徑保留為純文字")

    # 16) /assistant 拒
    o = render_reply("[x](/assistant)")
    ck("<a" not in o, "/assistant 應被拒")

    # 17) javascript:alert(1) 拒（Board 明列）
    o = render_reply("[x](javascript:alert(1))")
    ck("<a" not in o, "javascript:alert(1) 應被拒")

    # 18) //evil.com/updown 拒（協定相對即使含白名單字樣也拒）
    o = render_reply("[x](//evil.com/updown)")
    ck("<a" not in o, "//evil.com/updown 應被拒")
    ck("evil.com" in o, "協定相對 host 保留為純文字")

    # 19) https://evil.com 拒
    o = render_reply("[x](https://evil.com)")
    ck("<a" not in o, "https://evil.com 應被拒")

    # 20) 裸 EID-00884 → /updown
    o = render_reply("EID-00884")
    ck('<a href="/updown">EID-00884</a>' in o, "裸 EID-00884 應連 /updown")

    # 21) EID-1234（4 碼）不連
    o = render_reply("EID-1234 ok")
    ck("<a" not in o, "4 碼 EID 不得自動連結")
    ck("EID-1234" in o, "4 碼 EID 保留為純文字")

    # 22) /utilization?cls=segCLN&days=7 通過（含 query）
    o = render_reply("[U](/utilization?cls=segCLN&days=7)")
    ck('<a href="/utilization?cls=segCLN&amp;days=7">U</a>' in o,
       "帶 query 的 /utilization 應通過（'&' 於屬性內 escape 為 &amp;）")

    # 23) 精確 '/' 通過、'/api' 拒
    ck('<a href="/">Home</a>' in render_reply("[Home](/)"), "精確 '/' 應通過")
    ck("<a" not in render_reply("[x](/api/data)"), "/api 應被拒")
    ck("<a" not in render_reply("[x](/mcp)"), "/mcp 應被拒")

    # 24) /daily 預留路徑通過
    ck('<a href="/daily">D</a>' in render_reply("[D](/daily)"), "/daily 預留路徑應通過")

    # 25) FIX-3d：含 '..' 區段的路徑被拒（防目錄穿越；_PATH_RE 白名單含 '.'）
    o = render_reply("[x](/station/../api/data)")
    ck("<a" not in o, "含 '..' 的路徑不得成連結（防穿越）")
    ck("/station/../api/data" in o, "穿越路徑保留為純文字")
    ck("<a" not in render_reply("[x](/output/..)"), "尾端 '..' 亦拒")
    # 正常 /station/<cls>?days=7 不受影響
    o = render_reply("[x](/station/segX?days=7)")
    ck('<a href="/station/segX?days=7">x</a>' in o, "正常 station 路徑仍通過")

    # 26) FIX-3e：bidi 雙向覆寫字元被剝除（視覺欺騙面；html.escape 不處理）
    #     用 chr() 構造、不在 source 內嵌真 bidi 字元（自身即 trojan-source 面）。
    _RLO, _LRM = chr(0x202E), chr(0x200E)         # RLO / LRM
    _LRI, _PDI = chr(0x2066), chr(0x2069)         # LRI / PDI
    o = render_reply("abc" + _RLO + "def")
    ck(_RLO not in o, "RLO(U+202E) 覆寫字元應被剝除")
    ck("abcdef" in o, "剝除覆寫字元後文字相接")
    o = render_reply("a" + _LRM + "b")
    ck(_LRM not in o and "ab" in o, "LRM(U+200E) 應被剝除")
    o = render_reply("a" + _LRI + "b" + _PDI + "c")
    ck(_LRI not in o and _PDI not in o, "LRI/PDI(U+2066/2069) 應被剝除")
    # C0 控制字元剝除、但保留 \t 與 \n
    o = render_reply("a\x00b\tc\nd")
    ck("\x00" not in o, "NUL(C0) 控制字元應被剝除")
    ck("\t" in o and "\n" in o, "\\t 與 \\n 應保留（CSS pre-wrap）")

    # ---- #154 markdown 表格白名單（`SPEC-HERMES-TRUST.md` §12.8） ----
    #   ⚠ 以下每一根守的都是一條裁決；改任何一根之前先讀 §12.8 該條。

    T = "| 站別 | 稼動 |\n|---|---|\n| CAL | 88.4% |"

    # 27) 基本表格：thead/tbody 各一、欄數對、**零 wrapper div**、HTML 內零換行
    o = render_reply(T)
    ck(o.startswith('<table class="board-tbl">'), "表格應以 <table class=\"board-tbl\"> 起手")
    ck(o.endswith("</table>"), "表格應以 </table> 收尾")
    ck("<div" not in o, "★ 表格輸出不得含 '<div'（bot 泡泡釘用非貪婪 (.*?)</div>）")
    ck("\n" not in o, "★ 表格 HTML 內部不得有換行（宿主是 white-space:pre-wrap）")
    ck(o.count("<thead>") == 1 and o.count("<tbody>") == 1, "thead／tbody 各一")
    ck(o.count("<th>") == 2 and o.count("<td>") == 2, "2 欄 header＋1 列資料")
    ck("<th>站別</th>" in o and "<td>CAL</td>" in o, "cell 內容逐字進表格")
    ck("style=" not in o and "align=" not in o, "★ 屬性零：不出 style／align")

    # 28) **沒有分隔列＝不是表格**（整段照舊純文字；pipe 原樣留著）
    o = render_reply("| 站別 | 稼動 |\n| CAL | 88.4% |")
    ck("<table" not in o, "沒有分隔列不得成表格")
    ck("| 站別 | 稼動 |" in o, "非表格段落照舊是純文字（pipe 原樣）")

    # 29) **分隔列欄數與 header 不符＝不是表格**
    o = render_reply("| a | b |\n|---|\n| 1 | 2 |")
    ck("<table" not in o, "分隔列欄數不符不得成表格")
    ck("|---|" in o, "欄數不符時整塊留純文字")

    # 30) 頭尾 pipe 有無皆可（三種寫法同一張表）
    o1 = render_reply("| a | b |\n| --- | --- |\n| 1 | 2 |")
    o2 = render_reply("a | b\n--- | ---\n1 | 2")
    o3 = render_reply("a | b |\n--- | --- |\n1 | 2 |")
    ck(o1 == o2 == o3, "頭尾 pipe 有無不影響輸出")
    ck(o1.count("<th>") == 2 and o1.count("<td>") == 2, "三種寫法都是 2 欄 1 列")

    # 31) 對齊記號**容忍但不執行**（不出 style／align／class；輸出與無對齊記號版相同）
    o = render_reply("| a | b | c |\n| :--- | ---: | :---: |\n| 1 | 2 | 3 |")
    ck("<table" in o, ":---: 分隔列應被接受")
    ck("style=" not in o and "align=" not in o and "text-align" not in o,
       "★ 對齊記號容忍但不執行（不出任何對齊屬性）")
    ck(o == render_reply("| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |"),
       "有無對齊記號輸出逐位元相同")

    # 32) `\|` 跳脫＝字面 pipe，不切 cell
    o = render_reply("| a | b |\n|---|---|\n| x \\| y | 2 |")
    ck(o.count("<td>") == 2, "跳脫的 pipe 不得切出第三個 cell")
    ck("<td>x | y</td>" in o, "\\| 還原成字面 '|'")

    # 33) 資料列短補空 cell、長截掉多的（GFM 行為）
    o = render_reply("| a | b | c |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |")
    ck(o.count("<tr>") == 3, "1 列 header ＋ 2 列資料")
    ck(o.count("<td>") == 6, "★ 短補空、長截掉 ⇒ 每列恰 3 個 td")
    ck("<td></td>" in o, "短列補出來的是空 cell")
    ck(">4<" not in o, "長列多出來的第 4 欄被截掉")

    # 34) 零資料列＝只有 thead（仍渲染）
    o = render_reply("| a | b |\n|---|---|")
    ck("<table" in o and "<thead>" in o, "零資料列仍渲染")
    ck("<tbody>" not in o and "<td>" not in o, "零資料列不得生出空 tbody")

    # 35) ★ cell 內容走**完整管線**：escape、連結白名單、裸 EID、粗體
    o = render_reply("| x | y |\n|---|---|\n| <script>alert(1)</script> | ok |")
    ck("<script>" not in o, "★ cell 內 raw HTML 不得漏出")
    ck("&lt;script&gt;alert(1)&lt;/script&gt;" in o, "cell 內 <script> 應被 escape")
    o = render_reply("| 頁 | 備註 |\n|---|---|\n| [Yield](/yield) | [x](javascript:alert(1)) |")
    ck('<a href="/yield">Yield</a>' in o, "★ cell 內白名單連結應成 <a>")
    ck("javascript" in o and o.count("<a ") == 1,
       "★ cell 內 javascript: 連結整段降純文字（表格路徑不放寬白名單）")
    o = render_reply("| 機台 | 狀態 |\n|---|---|\n| EID-24821 | **停機** |")
    ck('<a href="/updown">EID-24821</a>' in o, "★ cell 內裸 EID 自動連 /updown")
    ck("<strong>停機</strong>" in o, "★ cell 內粗體照舊轉 <strong>")

    # 36) 前後各吃掉**一個** `\n`、其它換行原樣保留
    o = render_reply("前言\n" + T + "\n後語")
    ck(o == "前言" + render_reply(T) + "後語",
       "★ 塊吃掉緊鄰的一個前導與一個尾隨 \\n（pre-wrap 下否則多空行）")
    o = render_reply("前言\n\n" + T + "\n\n後語")
    ck(o == "前言\n" + render_reply(T) + "\n後語", "多出來的換行原樣保留（只吃一個）")

    # 37) 表格前後的一般文字照舊渲染（連結／EID／粗體都還在）
    o = render_reply("**摘要** 見 [Output](/output)：\n" + T + "\n細節問 EID-00884")
    ck("<strong>摘要</strong>" in o and '<a href="/output">Output</a>' in o,
       "表格前的一般文字照舊渲染")
    ck('<a href="/updown">EID-00884</a>' in o, "表格後的一般文字照舊渲染")
    ck(o.count("<table") == 1, "只有一張表格")

    # 38) 兩張表格（中間隔一行純文字）
    o = render_reply(T + "\n\n中間說明\n\n" + T)
    ck(o.count('<table class="board-tbl">') == 2 and o.count("</table>") == 2, "兩張表格各自成塊")
    ck("中間說明" in o, "兩表之間的文字保留")
    ck("<div" not in o, "兩表版本同樣零 '<div'")

    # 39) 無表格時輸出與舊路徑（`_render_flow`）**逐位元相同**＝零回歸面
    for _s in ("line1\nline2", "see [Output](/output) now", "a | b 只是一句話",
               "**b** EID-24821", "<b>x</b>"):
        ck(render_reply(_s) == _render_flow(_s),
           f"★ 無表格時 render_reply 必須等同 _render_flow：{_s!r}")

    # 40) 渲染器**不收 layout 參數**（窄寬同一份 HTML；layout 是 gateway persona 的事）
    import inspect as _inspect
    ck(list(_inspect.signature(render_reply).parameters) == ["text"],
       "★ render_reply 只有一個參數（渲染器不知道 layout）")

    # 41) **不執行 persona 的 8 列／4 欄上限、不截斷**（截列＝改事實，C-HT1）
    _big = ("| " + " | ".join(f"c{k}" for k in range(6)) + " |\n"
            + "|" + "---|" * 6 + "\n"
            + "".join("| " + " | ".join(f"v{r}{k}" for k in range(6)) + " |\n"
                      for r in range(12)).rstrip("\n"))
    o = render_reply(_big)
    ck(o.count("<th>") == 6, "6 欄全渲染（不套 4 欄上限）")
    ck(o.count("<tr>") == 13, "12 列資料全渲染（不套 8 列上限、不截斷）")

    print(f"[board_render] selftest PASS — {n} 項斷言全過："
          f"先 escape→Board 白名單連結→裸 EID→/updown、粗體、換行不轉 br、空輸入回空、"
          f"markdown 表格白名單（GFM 子集、cell 走完整管線、零 wrapper div）。")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="助理回覆安全渲染器（純函式；--selftest 離線自驗）")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（零 I/O、deterministic）")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
    else:
        ap.error("純函式模組；請用 --selftest 自驗，或於程式中 import render_reply。")
