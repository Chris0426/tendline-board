#!/usr/bin/env python3
"""board_translate.py — /equipment cell drill 「整格一鍵翻譯」的純函式核心。

定位
----
設備會議頁（/equipment）的 cell 下鑽（`_equipment_cell.html`）把 cmms free-text 落到 segment
列上（故障簡述 desc / 事件流 notes[].body / eMaint 最終診斷 worklog / 結單處置 closeout）。
SALTMARSH 作業員可能以中文（或越南文）填單；跨部門看板/老闆要英文。此模組提供**整格一次**把該格
所有 free-text 批次翻成 UI 語言的確定性支撐：語言偵測（免 LLM 便宜 gating）、收集/回填、
prompt 組裝、回覆解析、模組級快取。真正打 Hermes gateway 的 I/O 由 app.py 注入（本檔零 I/O）。

鐵則（與 D9 衛生 / 安全對齊）
--------------------------
- **只翻四種 free-text 欄位**：desc、notes[].body（＝渲染的 body_full 全文）、worklog（診斷全文
  worklog_full）、closeout。時間戳／工單號／零件號／EID／狀態名／結構欄**永不送 LLM**——
  只把四種 free-text 字串本身編號送出，並在 prompt 指示保留內嵌 token（part no/code/機台識別）。
- **翻譯結果＝不可信**：回填後由模板正常 Jinja autoescape 當純文字渲染（永不 |safe、永不過
  board_render 的 HTML 管線）。
- **回填為 additive 複本**：apply_translations 回新 list、不 mutate 原 segment（原文永遠一鍵可回）。
- **gating 便宜、無訊號即隱藏**：整格 free-text 若全已是 UI 語言（或無 free-text）→ 呼叫端不顯按鈕。

離線可跑：`python board_translate.py --selftest`（純函式、零網路 DB、Windows 亦可）。
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict

# ── 語言偵測（便宜、非 LLM；只為 UI 是否顯「翻譯」按鈕的 gating）─────────────────
# CJK 表意文字（含擴充 A、相容表意）→ 判為 "zh"。
def _is_cjk(o: int) -> bool:
    return (0x4E00 <= o <= 0x9FFF) or (0x3400 <= o <= 0x4DBF) or (0xF900 <= o <= 0xFAFF)


# 越南文專有帶調字母（含大小寫）——出現即判 "vi"（對 en/zh 皆為外語 → 顯按鈕）。
_VI_CHARS = set(
    "àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệ"
    "ìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵ"
)
_VI_CHARS |= {c.upper() for c in _VI_CHARS}


def detect_lang(text) -> str:
    """便宜啟發式：任一 CJK 字 → "zh"；否則任一越南文帶調字母 → "vi"；否則 "en"。
       空字串／None → "en"（無可翻＝視同已是任何語言、不觸發翻譯）。"""
    if not text:
        return "en"
    s = str(text)
    for ch in s:
        if _is_cjk(ord(ch)):
            return "zh"
    for ch in s:
        if ch in _VI_CHARS:
            return "vi"
    return "en"


def _ui(ui_lang) -> str:
    return "zh" if str(ui_lang).lower().startswith("zh") else "en"


def needs_translation(texts, ui_lang) -> bool:
    """整格是否需要翻譯：任一 free-text 之偵測語言不等於 UI 語言 → True。
       規則：偵測 "en" 才 match UI "en"；偵測 "zh" 才 match UI "zh"；"vi" 對兩者皆外語。
       無 free-text（空 list / 全空白）→ False（呼叫端不顯按鈕）。"""
    target = _ui(ui_lang)
    for tx in texts or []:
        if tx is None or not str(tx).strip():
            continue
        if detect_lang(tx) != target:
            return True
    return False


# ── 收集 / 回填（operate on route 建的 ctx segment dicts）───────────────────────
# path_id 形：("desc", seg_i) | ("note", seg_i, note_j) | ("worklog", seg_i) | ("closeout", seg_i)
# 只收非空 free-text；notes 取 body_full（全文，回填後再截 body 顯示）；worklog 取 worklog_full。
_NOTE_BODY_TRUNC = 160     # 與 app._note_line 顯示截斷一致
_WORKLOG_TRUNC = 240       # 與 /equipment/cell route worklog/closeout 顯示截斷一致


def collect_texts(segments):
    """回 [(path_id, text), ...]（穩定順序＝編號順序）。只含四種 free-text 欄位、非空。
       不含任何 EID / 工單號 / 時間戳 / 狀態名（D9 衛生：僅 free-text 本身進 LLM）。"""
    out = []
    for i, seg in enumerate(segments or []):
        if seg.get("show_desc") and (seg.get("desc") or "").strip():
            out.append((("desc", i), str(seg["desc"])))
        for j, nt in enumerate(seg.get("notes") or []):
            body = (nt.get("body_full") or nt.get("body") or "").strip()
            if body:
                out.append((("note", i, j), str(nt.get("body_full") or nt.get("body"))))
        wl = (seg.get("worklog_full") or seg.get("worklog") or "").strip()
        if wl:
            out.append((("worklog", i), str(seg.get("worklog_full") or seg.get("worklog"))))
        co = (seg.get("closeout_full") or seg.get("closeout") or "").strip()
        if co:
            # 取 `_full`＝送去翻譯的是**全文**（與 note/worklog 一致）；沒有 _full 的舊 ctx 退截斷版。
            out.append((("closeout", i), str(seg.get("closeout_full") or seg.get("closeout"))))
    return out


def _trunc(s, n):
    s = str(s)
    return (s[:n] + "…") if len(s) > n else s


def apply_translations(segments, id_map, translations):
    """回填翻譯到 segment 的**新複本**（不 mutate 原 list/dict）。
       id_map＝collect 出的 path_id 依序（index i → 編號 i+1）；translations＝{編號:譯文}。
       缺／未解析的編號 → 保留原文（誠實部分翻譯）。notes/worklog/closeout 回填後重新截斷顯示欄。"""
    new = []
    for seg in segments or []:
        c = dict(seg)
        if seg.get("notes"):
            c["notes"] = [dict(n) for n in seg["notes"]]
        new.append(c)
    for idx, pid in enumerate(id_map or []):
        tr = (translations or {}).get(idx + 1)
        if tr is None:
            continue
        tr = str(tr)
        kind, si = pid[0], pid[1]
        if si < 0 or si >= len(new):
            continue
        seg = new[si]
        if kind == "desc":
            seg["desc"] = tr
        elif kind == "note":
            j = pid[2]
            if 0 <= j < len(seg.get("notes") or []):
                seg["notes"][j]["body_full"] = tr
                seg["notes"][j]["body"] = _trunc(tr, _NOTE_BODY_TRUNC)
        elif kind == "worklog":
            seg["worklog_full"] = tr
            seg["worklog"] = _trunc(tr, _WORKLOG_TRUNC)
        elif kind == "closeout":
            # `_full`＝譯文全文（#26 起 drill 直接顯示它）；截斷版同步回填＝與 worklog 對稱。
            seg["closeout_full"] = tr
            seg["closeout"] = _trunc(tr, _WORKLOG_TRUNC)
    return new


# ── prompt 組裝 / 回覆解析 ─────────────────────────────────────────────────────
# 單筆譯文值長度上限：free-text 欄本身有顯示截斷（160/240）、正常譯文遠不及此；
# 超限＝疑似 LLM 失控輸出（重複/夾帶），視為缺項留原文，避免撐爆 ctx/DOM。
MAX_TRANSLATION_LEN = 20000

_PROMPT_HEAD = (
    "You are a translation function. Translate each numbered item below into {target}. "
    "Rules: translate free text only; preserve unchanged any part numbers, model names, "
    "codes, technical tokens (e.g. degas, dummy, DI, RTI), numbers, and machine/station "
    "identifiers embedded in the text; do not add commentary; do not use any tools. "
    "Respond with ONLY a JSON object mapping item number strings to translated strings, "
    'e.g. {{"1":"..."}}.'
)


def target_name(target_lang) -> str:
    return "Traditional Chinese" if _ui(target_lang) == "zh" else "English"


def build_prompt(items, target_lang) -> str:
    """組單一嚴格指令 prompt：說明 + 編號 free-text 清單。items＝純字串序列（不含任何欄位名/EID）。"""
    head = _PROMPT_HEAD.format(target=target_name(target_lang))
    lines = [f"{i}. {str(t)}" for i, t in enumerate(items or [], 1)]
    return head + "\n" + "\n".join(lines)


def _try_json(s):
    try:
        return json.loads(s)
    except Exception:
        return None


def _first_json_object(s):
    """從字串取第一個平衡的 {...} 區塊（忽略字串內的大括號）；無 → None。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    return None


def parse_reply(reply, n_items):
    """解析 gateway 回覆 → {編號:譯文}（只留 1..n_items 的整數鍵、值轉字串）。
       先整段 json.loads；失敗退回抽第一個 {...} 區塊再 loads。非 dict／無有效鍵 → None（呼叫端當失敗）。
       部分鍵＝誠實部分翻譯（回傳有幾筆算幾筆，缺的由 apply 保留原文）。
       超過 MAX_TRANSLATION_LEN 的譯文值＝視為缺項（不入 map、由 apply 保留原文）＝防超長回覆撐爆。"""
    if not reply or not str(reply).strip():
        return None
    obj = _try_json(str(reply).strip())
    if not isinstance(obj, dict):
        blk = _first_json_object(str(reply))
        obj = _try_json(blk) if blk is not None else None
    if not isinstance(obj, dict):
        return None
    out = {}
    for k, v in obj.items():
        try:
            ki = int(str(k).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= ki <= n_items and isinstance(v, (str, int, float)) and not isinstance(v, bool):
            sv = str(v)
            if len(sv) > MAX_TRANSLATION_LEN:
                continue                          # 超長＝視為缺項（誠實：apply 保留原文）
            out[ki] = sv
    return out or None


# ── 整頁批次（2026-08-17「一鍵翻譯整頁 drill」）────────────────────────────────
# 多個 cell 的 free-text 併成一個 prompt（全域編號 1..Σn）打一次 gateway，回來再切回
# 逐 cell 的 {1..ni:譯文}——每個 cell 的 mapping 與單格翻譯**同形** ⇒ 直接 cache_put 進
# 同一顆快取、被 GET ?tr=1 / 單格 POST 以同一把 cache_key 命中（一份快取、兩個入口）。
def split_batch(counts, mapping):
    """全批次 `{1..Σcounts: 譯文}` → 依 counts 切回逐 cell 的 `{1..ni: 譯文}`（重新編號）。

    `mapping=None`（整批解析失敗）→ 每格 None。某格一筆譯文都分不到 → 該格 None
    （呼叫端**不得**快取 None＝失敗不毒化快取，與單格語意一致）；部分譯文＝誠實部分翻譯。
    """
    out, base = [], 0
    for n in counts or []:
        sub = {}
        for i in range(1, int(n) + 1):
            v = (mapping or {}).get(base + i)
            if v is not None:
                sub[i] = v
        out.append(sub or None)
        base += int(n)
    return out


# ── 模組級快取（(源文雜湊, 目標語) → {編號:譯文}）；FIFO 上限 ─────────────────────
CACHE_CAP = 500
_CACHE: "OrderedDict[str, dict]" = OrderedDict()


def cache_key(texts, target) -> str:
    """source free-text 串接（NUL 分隔）+ 目標語 → sha256 hex。新 notes 改動 → 換 hash（無需 TTL）。"""
    joined = "\x00".join(str(t) for t in (texts or []))
    h = hashlib.sha256((joined + "\x00" + _ui(target)).encode("utf-8")).hexdigest()
    return f"{_ui(target)}:{h}"


def cache_get(key):
    return _CACHE.get(key)


def cache_put(key, mapping):
    """存入並維持 FIFO 上限（超過 → 丟最舊）。"""
    if key in _CACHE:
        _CACHE.pop(key)
    _CACHE[key] = mapping
    while len(_CACHE) > CACHE_CAP:
        _CACHE.popitem(last=False)


def _cache_clear():
    _CACHE.clear()


# ── selftest ───────────────────────────────────────────────────────────────────
def _selftest() -> int:
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # ── detect_lang ──────────────────────────────────────────────────────────
    ck(detect_lang("真空度不足取料失敗") == "zh", "zh detect")
    ck(detect_lang("Vacuum pickup failed") == "en", "en detect")
    ck(detect_lang("Bơm chân không hỏng, thay thế") == "vi", "vi detect")
    ck(detect_lang("Máy đo lệch vị trí") == "vi", "vi detect 2")
    ck(detect_lang("") == "en", "empty → en")
    ck(detect_lang(None) == "en", "None → en")
    ck(detect_lang("12345 mm 3.2bar") == "en", "numbers-only → en")
    ck(detect_lang("更換 O-ring RTI degas") == "zh", "mixed cjk+ascii → zh (cjk wins)")

    # ── needs_translation ────────────────────────────────────────────────────
    ck(needs_translation(["hello", "中文"], "en") is True, "en ui, has zh → True")
    ck(needs_translation(["hello", "world"], "en") is False, "en ui, all en → False")
    ck(needs_translation(["中文", "測試"], "zh") is False, "zh ui, all zh → False")
    ck(needs_translation(["中文", "hello"], "zh") is True, "zh ui, has en → True (mixed)")
    ck(needs_translation([], "en") is False, "empty list → False")
    ck(needs_translation(["   ", ""], "en") is False, "all blank → False")
    ck(needs_translation(["Bơm chân không"], "en") is True, "vi under en → True")
    ck(needs_translation(["Bơm chân không"], "zh") is True, "vi under zh → True")

    # ── collect_texts fixture（desc + notes + worklog + closeout）───────────────
    segs = [
        {"state": "維修", "work_order_no": 8801, "show_desc": True,
         "desc": "真空度不足取料失敗",
         "notes": [
             {"tm": "07-05 09:00", "label": "Report", "body": "操作員回報…",
              "body_full": "操作員回報：真空度不足取料失敗，觸發連續報警"},
             {"tm": "07-05 11:00", "label": "Progress", "body": "已完成更換",
              "body_full": "已完成更換與復歸、試機三片確認 OK"},
         ],
         "worklog": None, "worklog_full": None, "closeout": None},
        {"state": "計畫保養", "work_order_no": 8802, "show_desc": False, "desc": None,
         "notes": [], "worklog": "更換濾網",
         "worklog_full": "完成例行清潔潤滑、更換濾網",
         "closeout": "回復標準製程參數,試跑確認穩定。"},
        {"state": "生產", "work_order_no": None, "show_desc": False, "desc": None,
         "notes": [], "worklog": None, "worklog_full": None, "closeout": None},
    ]
    import copy as _copy
    orig = _copy.deepcopy(segs)
    collected = collect_texts(segs)
    pids = [pid for pid, _ in collected]
    texts = [tx for _, tx in collected]
    ck(len(collected) == 5, "collect → 5 items, got %d" % len(collected))
    ck(pids == [("desc", 0), ("note", 0, 0), ("note", 0, 1), ("worklog", 1), ("closeout", 1)],
       "collect path_ids order wrong: %r" % pids)
    ck(texts[0] == "真空度不足取料失敗", "desc text")
    ck(texts[1].startswith("操作員回報"), "note0 uses body_full full text")
    ck(texts[3] == "完成例行清潔潤滑、更換濾網", "worklog uses worklog_full")
    ck(texts[4].startswith("回復標準製程參數"), "closeout text")
    # 生產段（無 free-text、無工單）不進 collect
    ck(all(p[1] != 2 for p in pids), "producing seg must not be collected")
    # 無 EID / 工單號 混入 free-text 清單
    ck(all("8801" not in tx and "8802" not in tx for tx in texts), "no WO number in collected text")

    # ── build_prompt ─────────────────────────────────────────────────────────
    pr = build_prompt(texts, "en")
    ck("Translate each numbered item below into English" in pr, "prompt target=English")
    ck("do not use any tools" in pr, "prompt forbids tools")
    ck("degas" in pr and "RTI" in pr, "prompt keeps token-preservation examples")
    ck('{"1":"..."}' in pr, "prompt has JSON example")
    ck("\n1. " in pr and "\n2. " in pr and "\n5. " in pr, "prompt numbers all 5 items")
    ck("asset_id" not in pr and "work_order_no" not in pr and "\"eid\"" not in pr,
       "prompt must not carry structural field names")
    ck("Traditional Chinese" in build_prompt(texts, "zh"), "prompt target=zh name")

    # ── parse_reply ──────────────────────────────────────────────────────────
    good = '{"1":"a","2":"b","3":"c","4":"d","5":"e"}'
    ck(parse_reply(good, 5) == {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}, "clean JSON parse")
    prose = 'Sure! Here is the JSON: {"1":"x","2":"y"} — hope it helps.'
    ck(parse_reply(prose, 5) == {1: "x", 2: "y"}, "JSON extracted from prose")
    ck(parse_reply("total garbage no json", 5) is None, "garbage → None")
    ck(parse_reply("", 5) is None, "empty → None")
    ck(parse_reply("[1,2,3]", 5) is None, "non-dict JSON → None")
    ck(parse_reply('{"1":"only one"}', 5) == {1: "only one"}, "partial keys ok")
    ck(parse_reply('{"1":"a","9":"out","0":"lo"}', 5) == {1: "a"}, "out-of-range keys dropped")
    ck(parse_reply('{"1":true,"2":"ok"}', 5) == {2: "ok"}, "bool value dropped, str kept")
    ck(parse_reply('{"1":42}', 5) == {1: "42"}, "numeric value coerced to str")
    # FIX-3f：譯文值長度上限——超限當缺項、正常值保留
    _huge = '{"1":"' + "Y" * (MAX_TRANSLATION_LEN + 1) + '","2":"ok"}'
    ck(parse_reply(_huge, 5) == {2: "ok"}, "over-length value dropped, normal kept")
    _atcap = '{"1":"' + "Z" * MAX_TRANSLATION_LEN + '"}'
    ck(parse_reply(_atcap, 5) == {1: "Z" * MAX_TRANSLATION_LEN}, "at-cap value retained")

    # ── apply_translations（保留未翻 + 不 mutate）─────────────────────────────
    trans = {1: "EN-desc", 2: "EN-note0", 3: "EN-note1", 4: "EN-worklog", 5: "EN-closeout"}
    new = apply_translations(segs, pids, trans)
    ck(new is not segs, "apply returns new list")
    ck(new[0] is not segs[0], "apply copies seg dict")
    ck(new[0]["notes"] is not segs[0]["notes"], "apply copies notes list")
    ck(new[0]["desc"] == "EN-desc", "desc translated")
    ck(new[0]["notes"][0]["body_full"] == "EN-note0", "note0 body_full translated")
    ck(new[0]["notes"][0]["body"] == "EN-note0", "note0 body re-derived")
    ck(new[0]["notes"][1]["body_full"] == "EN-note1", "note1 translated")
    ck(new[1]["worklog_full"] == "EN-worklog", "worklog_full translated")
    ck(new[1]["worklog"] == "EN-worklog", "worklog re-derived")
    ck(new[1]["closeout"] == "EN-closeout", "closeout translated")
    ck(new[2]["state"] == "生產", "producing seg preserved")
    # 原 segs 不變
    ck(segs == orig, "apply must NOT mutate original segments")

    # 部分翻譯：缺的保留原文
    partial = apply_translations(segs, pids, {1: "ONLY-desc", 3: "ONLY-note1"})
    ck(partial[0]["desc"] == "ONLY-desc", "partial desc translated")
    ck(partial[0]["notes"][0]["body_full"].startswith("操作員回報"), "missing note0 keeps original")
    ck(partial[0]["notes"][1]["body_full"] == "ONLY-note1", "partial note1 translated")
    ck(partial[1]["worklog_full"] == "完成例行清潔潤滑、更換濾網", "missing worklog keeps original")

    # 截斷：長譯文 → body/worklog 顯示欄截斷 + …
    long_tr = "X" * 500
    lnew = apply_translations(segs, pids, {2: long_tr, 4: long_tr})
    ck(lnew[0]["notes"][0]["body_full"] == long_tr, "long note body_full full")
    ck(len(lnew[0]["notes"][0]["body"]) == _NOTE_BODY_TRUNC + 1 and lnew[0]["notes"][0]["body"].endswith("…"),
       "long note body truncated")
    ck(len(lnew[1]["worklog"]) == _WORKLOG_TRUNC + 1 and lnew[1]["worklog"].endswith("…"),
       "long worklog truncated")

    # ── split_batch（整頁批次 → 逐 cell mapping；單格同形）──────────────────────
    full = {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}
    ck(split_batch([2, 3], full) == [{1: "a", 2: "b"}, {1: "c", 2: "d", 3: "e"}],
       "split_batch renumbers per cell")
    ck(split_batch([2, 3], {1: "a", 4: "d"}) == [{1: "a"}, {2: "d"}],
       "split_batch partial → honest partial per cell (position preserved)")
    ck(split_batch([2, 3], {3: "c"}) == [None, {1: "c"}],
       "split_batch cell with zero translations → None (caller must not cache)")
    ck(split_batch([2, 3], None) == [None, None], "split_batch mapping=None → all None")
    ck(split_batch([], full) == [], "split_batch empty counts")
    ck(split_batch([5], full) == [full], "split_batch single cell identity")
    # 切回的每格 mapping 與單格 parse_reply 輸出同形 ⇒ apply_translations 可直接吃
    one = split_batch([len(texts)], {i + 1: f"B-{i + 1}" for i in range(len(texts))})[0]
    bnew = apply_translations(segs, pids, one)
    ck(bnew[0]["desc"] == "B-1", "split_batch output feeds apply_translations")

    # ── cache ────────────────────────────────────────────────────────────────
    _cache_clear()
    k1 = cache_key(texts, "en")
    vx5 = cache_key(texts, "zh")
    k3 = cache_key(texts + ["extra"], "en")
    ck(k1 != vx5, "cache_key differs by target")
    ck(k1 != k3, "cache_key differs by texts")
    ck(cache_get(k1) is None, "cache miss → None")
    cache_put(k1, trans)
    ck(cache_get(k1) == trans, "cache put/get roundtrip")
    # FIFO 上限
    _cache_clear()
    for i in range(CACHE_CAP + 1):
        cache_put("k%d" % i, {1: str(i)})
    ck(len(_CACHE) == CACHE_CAP, "cache capped at %d, got %d" % (CACHE_CAP, len(_CACHE)))
    ck(cache_get("k0") is None, "oldest evicted (FIFO)")
    ck(cache_get("k%d" % CACHE_CAP) == {1: str(CACHE_CAP)}, "newest retained")
    _cache_clear()

    print("selftest OK — %d assertions passed" % n)
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("board_translate: pure module; run --selftest")
