# -*- coding: utf-8 -*-
"""
board_kb.py — 製程／設備**知識卡**的唯讀 loader＋檢索（#175；純標準庫、零 I/O 以外的相依）。

## 為什麼有這一支（2026-09-26）

09-24 已把官方製程規範（Plover `PR*`＋Kestrel `PS*`／`MP*`）與設備手冊（`EQ*`）整理成
97 張英文知識卡（`_kb_a/cards/`＝54、`_kb_b/cards/`＝43）＋各組 `_glossary-*.tsv`，
但助理讀不到。09-26 另加 Board 自編卡各 1（`Board-` 卡號＋`provenance` 標頭；56／51）。本支＝MCP 工具 `knowledge_card` 的**唯一讀端**：把卡片載入、切節、建索引、
依名詞／主題檢索。`board_mcp.knowledge_card` 只是薄封裝（出處信封＋參數驗證）。

## 這支做什麼

- `load_index(root=None)`：讀 `<root>/_kb_a/cards/*.md`、`<root>/_kb_b/cards/*.md`
  與兩處 `_glossary-*.tsv`；`root` 預設＝env `BOARD_KB_ROOT`，再退 repo 根。
  以（檔數＋最大 mtime）當簽章快取——卡片改了下一次呼叫自動重讀，不必重啟 `:8000`。
- `search(query, section=None, root=None)`：回 `board.knowledge_card.v1` 形 dict（見 `KEYS`）。
  評分＝卡號完全比對 ≫ MES 站 class 完全比對 ≫ 標題／別名 ≫ 內文（idf 加權）；
  另附詞彙表命中（`glossary`）與設備卡 §5 警報表命中列（`alarm_rows`）。

## 這支**不做**什麼（鐵則）

- **唯讀、引用不複製**：卡片是文件摘要，本支不改卡、不寫任何檔；回傳只帶卡號與文件出處
  （`sources`），**不回檔案系統路徑**（QG-6：agent 層無檔案路徑）。
- **不碰 MES、不帶 SQL**：`_mes_sql_kb/` 的 SQL 範本目錄**刻意不接**（QG-6 agent 層無 SQL；
  「給我 SQL」在 Jev criteria 是 refuse）。
- **文件值不是量測值**：卡上的規格／參數是文件版次當時的值，與 MES 不同時**以 MES 為準**
  （caveat 恆附）；即時限值走 `station_spec_limits`、機台事件走 `equipment_status_events`。
- **路徑失效優雅降級**：卡片目錄不在／讀不到 ⇒ `found=false`＋`unavailable_reason="unavailable"`
  （助理白話＝「這次拿不到資料」），**永不 raise、永不回 traceback**。
- **查無如實說**：最佳分數不過門檻 ⇒ `found=false`＋`unavailable_reason="no_matching_card"`，
  不硬湊最像的一張。

## 用法

```
python board_kb.py --selftest              # 離線自驗（讀 repo 內真卡片＋暫存目錄降級）
python board_kb.py "die placement"            # 手動檢索（印 JSON 摘要）
```
"""

from __future__ import annotations

import csv
import glob
import io
import json
import math
import os
import re
import threading

SCHEMA_ID = "board.knowledge_card.v1"
ENV_ROOT = "BOARD_KB_ROOT"

#: 兩個卡片組（相對 root）。kind＝process／equipment。
GROUPS = (("process", os.path.join("_kb_a", "cards")),
          ("equipment", os.path.join("_kb_b", "cards")))

#: 回傳鍵（順序＝契約；smoke／selftest 逐鍵比對）。
KEYS = ("schema_id", "found", "unavailable_reason", "query", "section", "card", "other_matches",
        "glossary", "alarm_rows", "n_cards_indexed", "caveats")
CARD_KEYS = ("card_id", "kind", "title", "also_known_as", "mes_stations", "product_line",
             "sources", "sections_available", "content", "truncated")

MAX_QUERY_LEN = 200
CONTENT_BUDGET = 6000        # 主卡內文字元上限（4 萬字元的整本手冊不該灌進一輪對話）
MAX_OTHER = 4
MAX_GLOSSARY = 8
MAX_PER_TERM = 3             # 同一詞多張卡各自定義（CAL 有 8 條）⇒ 每詞最多 3 條
MAX_ALARM_ROWS = 10
MIN_SCORE = 2.0              # 最佳卡低於此 ⇒ 查無（見 `_accept`）

CAVEATS = (
    "Knowledge cards summarise official process specifications (Plover PR / Kestrel PS, MP) and "
    "equipment manuals (EQ) at the document revisions listed in `sources`. They are DOCUMENT "
    "values, not measurements: when a card value and MES differ, MES is authoritative (live spec "
    "limits: station_spec_limits; a machine's actual error events: equipment_status_events).",
    "Cite the card id and the source document when you use a card; never present a card value "
    "as this week's data, and never invent values a card does not contain.",
    "Cards are written in English; coverage is partial (56 process cards, 51 equipment cards; "
    "some machines have no card). Cards whose id starts with Board- are Tendline Board compiled summaries of "
    "Tendline Board's own records (see `provenance`), not official Aldergrove Precision documents. An EQ number identifies a machine TYPE's document set, not one "
    "machine (EID); there is no EID-to-EQ table yet.",
    "MES station mappings on process cards carry a confidence (high / medium / low) inferred "
    "from the document text; a low mapping is a hint, not a fact.",
)

#: 中文常用詞 → 英文檢索詞（卡片是英文；LLM 多半會自己翻，這層只是保險）。
ZH_ALIASES = (
    ("模組", "module"), ("模組", "module"), ("晶片", "die"), ("晶粒", "die"),
    ("基板", "substrate"), ("單元", "unit"), ("貼片", "die placement"), ("黏晶", "die placement"),
    ("打線", "wirebond"), ("焊線", "wirebond"), ("封膠", "sealdispense"), ("點膠", "dispense"),
    ("貼膜", "lamination"), ("壓合", "lamination"), ("電測", "electrical test"),
    ("校正", "calibration"), ("保養", "preventive maintenance"), ("警報", "alarm"),
    ("錯誤碼", "error code"), ("故障", "fault"), ("測漏", "leak test"), ("漏氣", "leak"),
    ("清洗", "clean"), ("包裝", "packaging"), ("真空", "vacuum"), ("膠", "adhesive"),
    ("規格", "spec"), ("參數", "parameter"), ("重工", "rework"), ("報廢", "scrap"),
    ("不良", "defect"), ("檢驗", "inspection"), ("目檢", "visual inspection"),
    ("綽號", "nickname"), ("編號", "numbering"),
)

#: section 參數的關鍵字 → 節標題關鍵字（大小寫不拘、比對節標題）。
SECTION_ALIASES = {
    "alarm": ("alarm", "error"), "alarms": ("alarm", "error"), "error": ("alarm", "error"),
    "警報": ("alarm", "error"), "錯誤碼": ("alarm", "error"),
    "pm": ("preventive",), "maintenance": ("preventive",), "保養": ("preventive",),
    "calibration": ("calibration",), "校正": ("calibration",),
    "parameter": ("parameter",), "parameters": ("parameter",), "spec": ("parameter", "spec"),
    "specs": ("parameter", "spec"), "limits": ("parameter", "spec"), "規格": ("parameter", "spec"),
    "troubleshooting": ("troubleshoot", "failure"), "failure": ("failure",),
    "defect": ("failure", "defect"), "rework": ("rework",),
    "glossary": ("glossary",), "overview": ("what this",), "modules": ("module",),
    "cycle": ("cycle", "sequence"), "sequence": ("sequence", "cycle"), "station": ("mes station",),
    "safety": ("safety",), "consumables": ("consumable",), "inspection": ("inspection", "measurement"),
}

_STOP = frozenset((
    "a", "an", "the", "of", "and", "or", "is", "are", "was", "be", "what", "whats", "what's",
    "does", "do", "did", "mean", "means", "meaning", "how", "why", "which", "who", "to", "in",
    "on", "for", "at", "by", "with", "from", "it", "its", "this", "that", "these", "those",
    "between", "difference", "different", "vs", "versus", "tell", "me", "about", "explain",
    "card", "please", "can", "you", "i", "we", "our", "my", "there", "any", "some", "used",
    "use", "step", "process", "machine", "station", "equipment",
))

_ID_RE = re.compile(r"^(?:eq|pr|ps|mp)[0-9]{6}$")
_SEG_RE = re.compile(r"`(seg[A-Za-z0-9_]+)`")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9#]*")

_LOCK = threading.Lock()
_CACHE: dict = {}


def repo_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def kb_root(root: str | None = None) -> str:
    return root or (os.environ.get(ENV_ROOT) or "").strip() or repo_root()


# ── 解析 ───────────────────────────────────────────────────────────────────────
def _parse_list(v: str) -> list:
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        try:
            return [str(x) for x in next(csv.reader([inner], skipinitialspace=True))
                    if str(x).strip()]
        except Exception:                                   # noqa: BLE001
            return [x.strip().strip('"') for x in inner.split(",") if x.strip()]
    return [v.strip('"')] if v else []


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """極簡 YAML 標頭（只認本批卡片用到的 `key: value` 與 `key: [a, b]`）；沒有就回空 dict。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        meta[k] = _parse_list(v) if v.startswith("[") else v.strip('"')
    return meta, text[end + 4:].lstrip("\n")


def _split_sections(body: str) -> tuple[str, list]:
    """body → (標題, [(節標題, 節內文)])。節＝`## ` 起頭；重複的節標題只留第一個（卡片偶有重列）。"""
    title = ""
    head_lines, sections, cur, buf = [], [], None, []
    seen = set()
    for line in body.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            if cur is not None and cur not in seen:
                seen.add(cur)
                sections.append((cur, "\n".join(buf).strip()))
            elif cur is None:
                head_lines = buf
            cur, buf = line[3:].strip(), []
            continue
        buf.append(line)
    if cur is not None and cur not in seen:
        sections.append((cur, "\n".join(buf).strip()))
    elif cur is None:
        head_lines = buf
    preamble = "\n".join(head_lines).strip()
    if preamble:
        sections.insert(0, ("Card header", preamble))
    return title, sections


def _tokens(s: str) -> list:
    return _TOKEN_RE.findall((s or "").lower())


def _norm_title(title: str) -> tuple[str, str]:
    """`QE910001 — Frame Bonding Machine` → ("QE910001", "Frame Bonding Machine")。"""
    m = re.match(r"^\s*([A-Z]{2}[0-9]{6})\s*[—–-]+\s*(.*)$", title or "")
    if m:
        return m.group(1), m.group(2).strip()
    return "", (title or "").strip()


def _alarm_rows(sections: list) -> list:
    """設備卡 §5（Alarms / error codes）的表格列 → [{code, meaning, cause, action, source}]。"""
    out = []
    for name, text in sections:
        low = name.lower()
        if "alarm" not in low and "error code" not in low:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("|") or set(line) <= set("|-: "):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or cells[0].lower() in ("code or message", "code", "message"):
                continue
            cells += [""] * (5 - len(cells))
            out.append({"code": cells[0], "meaning": cells[1], "likely_cause": cells[2],
                        "action": cells[3], "source": " | ".join(c for c in cells[4:] if c)})
    return out


def _load_card(path: str, kind: str) -> dict:
    with io.open(path, encoding="utf-8") as fh:
        text = fh.read()
    meta, body = _parse_front_matter(text)
    title, sections = _split_sections(body)
    doc_id, name = _norm_title(title)
    stem = os.path.splitext(os.path.basename(path))[0]
    stations = list(meta.get("mes_stations") or [])
    if not stations:
        for sname, stext in sections:
            if "mes station" in sname.lower():
                stations = list(dict.fromkeys(_SEG_RE.findall(stext)))
    aka = [a for a in (meta.get("also_known_as") or []) if a]
    sources = list(meta.get("sources") or [])
    if not sources:
        for sname, stext in sections:
            if sname == "Card header":
                for ln in stext.splitlines():
                    if ln.lower().startswith("sources read:"):
                        sources = [s.strip() for s in ln.split(":", 1)[1].split("|")[0].split(";")
                                   if s.strip()]
    ids = {stem.lower()}
    if doc_id:
        ids.add(doc_id.lower())
    if meta.get("eq"):
        ids.add(str(meta["eq"]).lower())
    full = "\n".join(t for _, t in sections)
    head_text = " ".join([name, " ".join(aka), " ".join(stations)])
    return {
        "card_id": stem, "kind": kind, "doc_id": doc_id or stem,
        "title": name or title or stem, "also_known_as": aka, "mes_stations": stations,
        "product_line": meta.get("product_line") or None, "sources": sources,
        "provenance": meta.get("provenance") or None,
        "sections": sections, "alarms": _alarm_rows(sections) if kind == "equipment" else [],
        "ids": ids,
        "stations_l": {s.lower() for s in stations},
        "head_tokens": set(_tokens(head_text)),
        "head_text_l": head_text.lower(),
        "body_tf": _tf(_tokens(full)),
    }


def _tf(tokens: list) -> dict:
    d: dict = {}
    for t in tokens:
        d[t] = d.get(t, 0) + 1
    return d


def _load_glossary(path: str, kind: str) -> list:
    rows = []
    with io.open(path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh, delimiter="\t")
        header = next(rd, None) or []
        for r in rd:
            if not r or not r[0].strip():
                continue
            r = r + [""] * (5 - len(r))
            rows.append({"term": r[0].strip(), "expansion": r[1].strip(),
                         "definition": r[2].strip(), "card_ref": r[3].strip(),
                         "source": r[4].strip(), "kind": kind})
    return rows


def _signature(root: str) -> tuple | None:
    files = []
    for _kind, rel in GROUPS:
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            return None
        files += glob.glob(os.path.join(d, "*.md")) + glob.glob(os.path.join(d, "_glossary-*.tsv"))
    if not files:
        return None
    try:
        return (len(files), max(os.path.getmtime(f) for f in files))
    except OSError:
        return None


def load_index(root: str | None = None) -> dict | None:
    """回索引 dict；卡片目錄不在／讀壞 ⇒ **None**（呼叫端回 unavailable，不 raise）。"""
    r = kb_root(root)
    try:
        sig = _signature(r)
        if sig is None:
            return None
        with _LOCK:
            hit = _CACHE.get(r)
            if hit and hit["sig"] == sig:
                return hit
        cards, glossary = [], []
        for kind, rel in GROUPS:
            d = os.path.join(r, rel)
            for p in sorted(glob.glob(os.path.join(d, "*.md"))):
                cards.append(_load_card(p, kind))
            for p in sorted(glob.glob(os.path.join(d, "_glossary-*.tsv"))):
                glossary += _load_glossary(p, kind)
        if not cards:
            return None
        df: dict = {}
        for c in cards:
            for t in c["body_tf"]:
                df[t] = df.get(t, 0) + 1
        idx = {"sig": sig, "cards": cards, "glossary": glossary, "df": df,
               "n": {"process": sum(1 for c in cards if c["kind"] == "process"),
                     "equipment": sum(1 for c in cards if c["kind"] == "equipment")}}
        with _LOCK:
            _CACHE[r] = idx
        return idx
    except Exception:                                       # noqa: BLE001 —— 降級、不 raise
        return None


# ── 檢索 ───────────────────────────────────────────────────────────────────────
def _expand_query(q: str) -> tuple[list, str]:
    """回 (資訊詞 tokens, 正規化片語)。中文別名先換成英文詞。"""
    low = q.lower()
    extra = []
    for zh, en in ZH_ALIASES:
        if zh in q:
            extra.append(en)
            low = low.replace(zh, " ")
    phrase = " ".join(_tokens(" ".join([low] + extra)))
    toks = [t for t in _tokens(phrase) if t not in _STOP and (len(t) > 1 or t.isdigit())]
    return list(dict.fromkeys(toks)), phrase


def _score(card: dict, toks: list, phrase: str, raw_l: str, idx: dict) -> tuple[float, list]:
    n = len(idx["cards"])
    s, why = 0.0, []
    if raw_l in card["ids"] and raw_l not in toks:                             # 帶連字號的卡號（Board-EQ001）不會被 tokens 切中
        s += 100.0
        why.append("card_id")
    for t in toks:
        if t in card["ids"]:
            s += 100.0
            why.append("card_id")
    if raw_l in card["stations_l"] or any(t in card["stations_l"] for t in toks):
        s += 40.0
        why.append("mes_station")
    content_phrase = " ".join(toks)
    if len(toks) >= 2 and content_phrase and content_phrase in card["head_text_l"]:
        s += 12.0
        why.append("title_phrase")
    matched = 0
    for t in toks:
        idf = math.log(1.0 + n / (1 + idx["df"].get(t, 0)))
        hit = False
        if t in card["head_tokens"] or (len(t) >= 5 and any(
                len(h) >= 5 and (h.startswith(t) or t.startswith(h)) for h in card["head_tokens"])):
            # 前綴容忍：wirebond ↔ wirebonder、laminat ↔ lamination（標題／別名常是另一個詞形）
            s += 3.0 * idf
            hit = True
        tf = card["body_tf"].get(t, 0)
        if tf:
            s += min(tf, 6) / 6.0 * idf
            hit = True
        matched += hit
    if toks and matched:
        why.append("terms %d/%d" % (matched, len(toks)))
    return s, why, matched


def _pick_sections(card: dict, section: str | None) -> tuple[list, bool]:
    secs = card["sections"]
    if not section:
        return secs, False
    key = section.strip().lower()
    needles = SECTION_ALIASES.get(key) or (key,)
    picked = [(n, t) for n, t in secs if any(nd in n.lower() for nd in needles)]
    return (picked or secs), not picked


def _render(card: dict, section: str | None) -> dict:
    secs, section_missing = _pick_sections(card, section)
    parts, used, truncated = [], 0, False
    for name, text in secs:
        block = "## %s\n%s" % (name, text)
        if used + len(block) > CONTENT_BUDGET:
            room = CONTENT_BUDGET - used
            if room > 200:
                parts.append(block[:room].rstrip() + "\n…")
            truncated = True
            break
        parts.append(block)
        used += len(block) + 2
    out = {"card_id": card["card_id"], "kind": card["kind"], "title": card["title"],
           "also_known_as": list(card["also_known_as"]), "mes_stations": list(card["mes_stations"]),
           "product_line": card["product_line"], "sources": list(card["sources"]),
           "sections_available": [n for n, _ in card["sections"]],
           "content": "\n\n".join(parts), "truncated": truncated}
    if card.get("provenance"):                             # Board 自編卡（Fable 裁 2026-09-26）：出處標示隨卡回傳
        out["provenance"] = card["provenance"]
    if section_missing:
        out["section_note"] = "no section matched %r; the whole card is shown" % section
    return out


def _glossary_hits(idx: dict, toks: list, raw: str) -> list:
    raw_l = raw.strip().lower()
    want = set(toks) | ({raw_l} if raw_l else set())
    hits, seen, per_term = [], set(), {}
    for g in idx["glossary"]:
        term_l = g["term"].lower()
        if term_l in want or g["expansion"].lower() == raw_l:
            k = (term_l, g["definition"].lower())
            if k in seen or per_term.get(term_l, 0) >= MAX_PER_TERM:
                continue
            seen.add(k)
            per_term[term_l] = per_term.get(term_l, 0) + 1
            hits.append({kk: g[kk] for kk in ("term", "expansion", "definition", "card_ref", "source")})
            if len(hits) >= MAX_GLOSSARY:
                break
    return hits


def _alarm_hits(idx: dict, toks: list, content_phrase: str) -> list:
    codes = [t for t in toks if any(ch.isdigit() for ch in t)]
    hits = []
    for c in idx["cards"]:
        for row in c["alarms"]:
            code_l = row["code"].lower()
            row_l = (row["code"] + " " + row["meaning"]).lower()
            ok = any(re.search(r"(?<![a-z0-9])" + re.escape(cd) + r"(?![a-z0-9])", code_l)
                     for cd in codes)
            if not ok and len(toks) >= 2 and content_phrase in row_l:
                ok = True
            if ok:
                hits.append(dict(row, card_id=c["card_id"], machine=c["title"]))
                if len(hits) >= MAX_ALARM_ROWS:
                    return hits
    return hits


def blank(query, section, n=None, reason=None) -> dict:
    return {"schema_id": SCHEMA_ID, "found": False, "unavailable_reason": reason,
            "query": query, "section": section, "card": None, "other_matches": [],
            "glossary": [], "alarm_rows": [], "n_cards_indexed": n,
            "caveats": list(CAVEATS)}


def search(query: str, section: str | None = None, root: str | None = None) -> dict:
    """檢索入口（**永不 raise**）。`query` 必須已過 `normalize_query`。"""
    idx = load_index(root)
    if idx is None:
        out = blank(query, section, reason="unavailable")
        out["hint"] = "the knowledge base is not available on this server"
        return out
    try:
        toks, phrase = _expand_query(query)
        raw_l = query.strip().lower()
        scored = []
        for c in idx["cards"]:
            s, why, matched = _score(c, toks, phrase, raw_l, idx)
            if s > 0:
                scored.append((s, matched, c, why))
        scored.sort(key=lambda x: (-x[0], x[2]["card_id"]))
        content_phrase = " ".join(toks)
        gl = _glossary_hits(idx, toks, query)
        al = _alarm_hits(idx, toks, content_phrase)
        out = blank(query, section, n=dict(idx["n"]))
        best = scored[0] if scored else None
        if best and _accept(best, toks):
            out["card"] = _render(best[2], section)
            out["card"]["match"] = best[3]
            out["other_matches"] = [
                {"card_id": c["card_id"], "kind": c["kind"], "title": c["title"],
                 "mes_stations": list(c["mes_stations"]), "score": round(s, 1)}
                for s, m, c, _w in scored[1:1 + MAX_OTHER] if _accept((s, m, c, _w), toks)]
        out["glossary"], out["alarm_rows"] = gl, al
        out["found"] = bool(out["card"] or gl or al)
        if not out["found"]:
            out["unavailable_reason"] = "no_matching_card"
            out["hint"] = ("no knowledge card covers this topic; say so plainly and do not invent "
                           "document values")
        return out
    except Exception:                                       # noqa: BLE001
        out = blank(query, section, n=dict(idx["n"]), reason="unavailable")
        return out


def _accept(entry, toks) -> bool:
    """最佳卡夠像才算命中：卡號／站 class 完全比對，或分數過門檻且至少命中一半資訊詞。"""
    s, matched, _c, why = entry
    if any(w in ("card_id", "mes_station") for w in why):
        return True
    if not toks:
        return False
    return s >= MIN_SCORE and matched * 2 >= len(toks)


def normalize_query(query, section=None) -> tuple[tuple | None, dict | None]:
    """參數 → `((query, section), None)` 或 `(None, 400 形 err)`。"""
    if not isinstance(query, str) or not query.strip():
        return None, {"error": "bad_query", "given": query,
                      "hint": "query must be a non-empty term or topic, e.g. 'DAF', 'die placement', "
                              "'QE910002', 'segWireBonding', 'E102'"}
    q = query.strip()
    if len(q) > MAX_QUERY_LEN:
        return None, {"error": "bad_query", "given": q[:40] + "…",
                      "hint": "query too long (max %d characters); use a short term" % MAX_QUERY_LEN}
    sec = None
    if section is not None:
        if not isinstance(section, str) or len(section) > 40:
            return None, {"error": "bad_section", "given": section,
                          "hint": "section is a short keyword such as 'alarms', 'pm', "
                                  "'calibration', 'parameters', 'troubleshooting', 'glossary'"}
        sec = section.strip() or None
    return (q, sec), None


def ok_payload(out: dict) -> bool:
    """形狀驗證（鍵集合＋型別）：selftest／smoke 共用。"""
    if not isinstance(out, dict) or tuple(k for k in KEYS if k in out) != KEYS:
        return False
    if out["schema_id"] != SCHEMA_ID or not isinstance(out["found"], bool):
        return False
    if out["found"] and out["unavailable_reason"] is not None:
        return False
    if not out["found"] and not out["unavailable_reason"]:
        return False
    c = out["card"]
    if c is not None and not all(k in c for k in CARD_KEYS):
        return False
    return all(isinstance(out[k], list) for k in ("other_matches", "glossary", "alarm_rows",
                                                   "caveats"))


# ── selftest ──────────────────────────────────────────────────────────────────
def selftest() -> int:
    import tempfile
    passed = 0

    def ck(cond, what):
        nonlocal passed
        if not cond:
            raise AssertionError(what)
        passed += 1

    idx = load_index()
    # 私有知識卡目錄不在（例：脫敏公開樹不帶原始卡片）⇒ 依賴卡片內容的斷言標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    # 目錄不在的降級、參數正規化不依賴卡片內容，一律照跑。
    if idx is None:
        print("board_kb selftest: skipped: knowledge cards absent")
    else:
        ck(idx is not None, "repo 內卡片應載得起來")
        ck(idx["n"] == {"process": 56, "equipment": 51}, "卡數應 56／51（含 Board 自編各 1），實得 %r" % (idx["n"],))
        ck(len(idx["glossary"]) > 100, "詞彙表應 >100 列")
        ck(load_index() is idx, "同簽章應回快取")
        eq2 = next(c for c in idx["cards"] if c["card_id"] == "QE910001")
        ck(eq2["mes_stations"] == ["segFrameBonding"] and eq2["title"] == "Frame Bonding Machine",
           "QE910001 YAML 解析錯：%r" % (eq2["mes_stations"],))
        ck(len(eq2["alarms"]) >= 5, "QE910001 §5 警報列應 ≥5")
        pr = next(c for c in idx["cards"] if c["card_id"] == "QR910010")
        ck("segDiePlacement" in pr["mes_stations"], "QR910010 應由 §2 解析出 segDiePlacement")
        ars = next(c for c in idx["cards"] if c["card_id"] == "KESTREL-QM910001")
        ck(ars["doc_id"] == "QM910001" and "qm877059" in ars["ids"], "Kestrel 卡號應可用文件號比對")

        # 卡號完全比對
        r = search("QE910002")
        ck(ok_payload(r) and r["found"] and r["card"]["card_id"] == "QE910002", "卡號應直達")
        ck("card_id" in r["card"]["match"], "match 應標 card_id")
        r = search("QM910001")
        ck(r["card"]["card_id"] == "KESTREL-QM910001", "Kestrel 文件號應直達")
        # 站 class
        r = search("segWireBonding")
        ck(r["found"] and "segWireBonding" in r["card"]["mes_stations"], "站 class 應命中對應卡")
        # 名詞
        r = search("DAF")
        ck(r["found"] and any(g["term"] == "DAF" for g in r["glossary"]), "DAF 應命中詞彙表")
        r = search("die module")
        ck(r["found"] and r["card"] is not None, "die／module 應命中卡")
        r = search("晶片跟模組差在哪")
        ck(r["found"] and r["card"] is not None, "中文別名應轉英文檢索")
        r = search("Frame bonding")
        ck(r["card"]["card_id"] in ("QE910001", "QR910009") or "Frame" in r["card"]["title"],
           "Frame bonding 應命中 Frame 卡，實得 %s" % r["card"]["card_id"])
        # 警報碼
        r = search("E102")
        ck(r["found"] and any(a["code"] == "E102" for a in r["alarm_rows"]), "E102 應命中警報列")
        ck(all("card_id" in a and "machine" in a for a in r["alarm_rows"]), "警報列帶卡號與機名")
        r = search("Low Vacuum Error")
        ck(any(a["card_id"] == "QE910001" for a in r["alarm_rows"]), "警報訊息片語應命中 QE910001")
        r = search("wirebonder")
        ck(r["card"]["card_id"] == "QE910002", "別名 Wirebonder 應直達 QE910002，實得 %s"
           % r["card"]["card_id"])
        r = search("CAL")
        ck(sum(1 for g in r["glossary"] if g["term"] == "CAL") <= MAX_PER_TERM, "同詞詞彙列應封頂")
        # section
        r = search("QE910001", "alarms")
        ck(r["card"]["content"].startswith("## 5.") and "section_note" not in r["card"],
           "section=alarms 應只回 §5")
        r = search("QE910001", "nonexistent-section")
        ck("section_note" in r["card"], "找不到節應註記並回整張")
        # 預算
        r = search("QE910003")
        ck(len(r["card"]["content"]) <= CONTENT_BUDGET + 10, "內文應受預算限制")
        # 查無
        r = search("weekend weather forecast")
        ck(ok_payload(r) and r["found"] is False and r["unavailable_reason"] == "no_matching_card",
           "無關主題應如實查無")
        ck(r["card"] is None and r["glossary"] == [] and r["alarm_rows"] == [], "查無時不湊卡")
    # 降級：目錄不在
    with tempfile.TemporaryDirectory() as td:
        r = search("DAF", root=td)
        ck(ok_payload(r) and r["found"] is False and r["unavailable_reason"] == "unavailable",
           "目錄不在應 unavailable、不 raise")
        ck("traceback" not in json.dumps(r).lower(), "降級不得帶 traceback")
        # 壞卡（空目錄＋一張壞編碼卡）仍不 raise
        for _k, rel in GROUPS:
            os.makedirs(os.path.join(td, rel))
        with io.open(os.path.join(td, GROUPS[0][1], "QR910011.md"), "wb") as fh:
            fh.write(b"\xff\xfe\x00bad")
        r = search("DAF", root=td)
        ck(r["found"] is False and r["unavailable_reason"] == "unavailable", "壞卡應降級")
    # 參數
    ck(normalize_query("")[1]["error"] == "bad_query", "空 query 應 bad_query")
    ck(normalize_query("x" * 201)[1]["error"] == "bad_query", "超長 query 應 bad_query")
    ck(normalize_query("DAF", 5)[1]["error"] == "bad_section", "非字串 section 應 bad_section")
    ck(normalize_query(" DAF ", " ")[0] == ("DAF", None), "空白 section 應正規化成 None")
    if idx is not None:
        # Board 自編卡（Fable 裁 2026-09-26）：Q1／Q5 缺陷根——編號制度、die↔module
        r = search("seal4 跟 EID 是同一種編號嗎")
        ck(r["found"] and r["card"]["card_id"] == "Board-EQ001", "seal4↔EID 應命中 Board-EQ001，實得 %s"
           % (r["card"] or {}).get("card_id"))
        ck(r["card"].get("provenance", "").startswith("Board 自編"), "Board 卡應帶 provenance 標示")
        r = search("die 跟 module 差在哪")
        ck(r["found"] and r["card"]["card_id"] == "Board-PR001", "die↔module 應命中 Board-PR001，實得 %s"
           % (r["card"] or {}).get("card_id"))
        r = search("Board-EQ001")
        ck(r["card"]["card_id"] == "Board-EQ001" and "card_id" in r["card"]["match"], "Board 卡號應直達")
        ck("provenance" not in search("QE910002")["card"], "官方卡不帶 provenance 鍵")
        ck(all(c["card_id"].startswith("Board-") == bool(c["provenance"]) for c in idx["cards"]),
           "provenance 標頭⇔Board- 卡號（自編卡慣例）")
        # 回傳不含檔案系統路徑
        blob = json.dumps(search("QE910001"), ensure_ascii=False)
        ck(repo_root() not in blob and "_kb_b" not in blob, "回傳不得帶檔案路徑")
    print("%d passed" % passed)
    return 0


def main(argv=None) -> int:
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="知識卡唯讀 loader＋檢索（#175）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--section", default=None)
    ap.add_argument("query", nargs="?")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.query:
        ap.error("給一個 query 或 --selftest")
    key, err = normalize_query(a.query, a.section)
    if err:
        print(json.dumps(err, ensure_ascii=False))
        return 2
    out = search(*key)
    if out.get("card"):
        out["card"]["content"] = out["card"]["content"][:400] + "…"
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
