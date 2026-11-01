# -*- coding: utf-8 -*-
"""
board_jev.py — Jev（TypeSafe）請求組裝與 shadow 分類的**單一出處**（純標準庫、零相依）。

規格＝`SPEC-HERMES-TRUST.md` **§7.7／§7.7.2**（Jev 只當 router／pre-gate＋guardrail、
**先 shadow mode**：只記錄不決策、逾時／無 key 一律靜默退回現行路徑、不改路由）。

## 為什麼有這一支（2026-09-21）

`_jev_calibrate.py`（離線批次校準）與 app 端的 shadow mode 送的**必須是同一形 request**，
否則校準表量的不是線上真正在跑的東西。⇒ 把「怎麼組 request、怎麼讀 key／criteria、
怎麼遮罩」收斂成這一支，兩個消費端都 import 它，**不各自持一份副本**。

## 這支做什麼

- 常數（端點／模型／questions 鍵名／instructions／criteria 來源）＝**唯一出處**。
- `load_key()`／`load_criteria()`／`build_state()`／`build_payload()`／`_redact()`。
- `classify()`＝shadow mode 那一次呼叫：**永不 raise**、錯誤收斂成短 kind、
  只回 `{predicted, confidence}` 兩個決策欄（＋模型／criteria 版本／耗時／錯誤 kind）。

## 這支**不做**什麼（鐵則）

- **不定門檻**（§7.7.2 已裁 router 門檻＝0.9，但那是 SPEC 的裁決，不是碼裡的常數）。
- **本支不決策**：`classify()` 的回傳進 turn 日誌（`board_assistant.record_jev`）；#172 起 app 端 router 決策面
  （§7.7.5）用 `router_directive()` 把它翻成 prompt 前的 `[router]` 指令——**決策點在 app、指令文字在這裡**，
  本支仍零 I/O、不讀門檻。
- **不連 MES、不讀 SQLite、不碰 live 服務**。
- **key 永不出現在 stdout／stderr／argv／例外訊息**：`classify()` 的 `error` 只可能是
  下面那組**短 kind 字面值**，絕不回放伺服器 body 或 traceback。
- **state 只放三個具名欄**（`question`／`page`／`language`）。§7.7 ④：state 塞無關欄位
  會被干擾（官方 jaggedness：「Accuracy falls as the state grows with content unrelated
  to the decision.」）。

## 錯誤 kind（`JevResult.error`；shadow 只記錄，呼叫端不重試）

| kind | 意思 |
|---|---|
| `no_key` | 沒有 `TYPESAFE_API_KEY`／`_jev.env`（＝功能本來就沒開） |
| `no_criteria` | fixture 讀不到／criteria 空（呼叫端應視同沒開） |
| `timeout` | socket 逾時（`SHADOW_TIMEOUT`） |
| `http_<code>` | HTTP 非 2xx；**429／529 不重試**（shadow 不值得等） |
| `net` | 連線／DNS／其他未預期例外 |
| `bad_response` | 200 但 JSON 壞或缺 `answers.route.choice` |

## 用法

```
python board_jev.py --selftest   # 離線自驗（零網路、不讀真 key 也能跑）
```
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# ── 常數（照官方文件 docs.typesafe.ai；**不要**在這裡放門檻）─────────────────────
API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

Q_CHOICE = "route"          # questions map 的鍵名
Q_NOUL = "refuse_check"

# §7.7 實測：「題目寫得越『嚴謹多條件』校準越差（0.040→0.116，Jev 照字面讀）」
# ⇒ instructions 一律**短句、英文**，邊界情形寫在 criteria（官方 jaggedness：
#   "State the exact condition in the instructions. Be specific. Put boundary cases in the criteria."）
CHOICE_INSTRUCTIONS = (
    "Which Tendline Board tool should answer this question? "
    "Pick ask if required information is missing. "
    "Pick refuse if the tools cannot or should not do it."
)
NOUL_INSTRUCTIONS = "Should this question be refused?"
NOUL_CRITERIA = {
    "true": "The question must be refused: the tools cannot do it, or it asks to change data or caliber.",
    "false": "The question can be answered by a tool, or answered after one clarifying question.",
}

FIXTURE_PATH = os.path.join("_fixtures", "hermes_golden.v1.json")
FIXTURE_REL = FIXTURE_PATH          # 別名（`_jev_calibrate` 報表 header 用的舊名）
ENV_FILE = "_jev.env"
ENV_VAR = "TYPESAFE_API_KEY"

# shadow 一次呼叫的逾時（秒）。線上是**背景執行緒**、不擋使用者回覆 ⇒ 給得比校準短：
# 逾時就是逾時，記一筆 `timeout` 比讓執行緒掛著久有意義。
_DEFAULT_SHADOW_TIMEOUT = 20.0
try:
    SHADOW_TIMEOUT = float(os.environ.get("BOARD_JEV_TIMEOUT") or _DEFAULT_SHADOW_TIMEOUT)
except (TypeError, ValueError):
    SHADOW_TIMEOUT = _DEFAULT_SHADOW_TIMEOUT
if SHADOW_TIMEOUT <= 0:
    SHADOW_TIMEOUT = _DEFAULT_SHADOW_TIMEOUT

# 回應讀取上限（防失控大回覆；同 app `_HERMES_MAX_BYTES` 紀律）。choice 回應是幾百位元組。
MAX_RESP_BYTES = 256 * 1024


def repo_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


# ── key 與遮罩 ────────────────────────────────────────────────────────────────
def load_key(env_path: str | None = None) -> str | None:
    """環境變數 `TYPESAFE_API_KEY` 優先，否則讀 `_jev.env` **第一個** `TYPESAFE_API_KEY=` 行。

    `env_path`＝那個檔的路徑（預設 repo 根的 `_jev.env`）。
    **找不到一律回 `None`、不 raise**——shadow mode 的「沒開」就是一個正常狀態；
    批次校準要的「大聲死掉」由 `_jev_calibrate.load_key` 自己包成 SystemExit（訊息不變）。

    ⚠ 回傳值只往 HTTP header 走；**永不** print、永不入輸出檔、永不進例外訊息。
    """
    env_key = (os.environ.get(ENV_VAR) or "").strip()
    if env_key:
        return env_key
    path = env_path or os.path.join(repo_root(), ENV_FILE)
    try:
        if not os.path.exists(path):
            return None
        with io.open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if line.startswith(ENV_VAR + "="):
                    val = line[len(ENV_VAR) + 1:].strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        return None
    return None


def _redact(text: object, key: str | None) -> str:
    """把 key（以及任何 `Bearer <…>` 形）遮成 `***`。任何要外流的字串都先過這裡。"""
    s = "" if text is None else str(text)
    if key:
        s = s.replace(key, "***")
        # 有些服務回 body 時會截尾／加引號，長 key 的前綴也一併遮（避免部分外洩）
        if len(key) > 12:
            s = s.replace(key[:12], "***")
    out = []
    for chunk in s.split("Bearer "):
        out.append(chunk)
    if len(out) > 1:
        rebuilt = out[0]
        for chunk in out[1:]:
            # 「Bearer 」之後的第一個 token 一律遮掉
            i = 0
            while i < len(chunk) and not chunk[i].isspace():
                i += 1
            rebuilt += "Bearer ***" + chunk[i:]
        s = rebuilt
    return s


# ── fixture／criteria ─────────────────────────────────────────────────────────
def load_fixture(path: str | None = None) -> dict:
    p = path or os.path.join(repo_root(), FIXTURE_PATH)
    with io.open(p, encoding="utf-8") as fh:
        return json.load(fh)


def choice_criteria(fixture: dict) -> dict:
    """fixture `criteria` 去掉 `_note`；描述**照抄、不改寫**（§7.7：領域詞要在 criteria 講白）。"""
    return {k: v for k, v in (fixture.get("criteria") or {}).items() if k != "_note"}


def load_criteria(path: str | None = None) -> tuple[dict, int]:
    """讀 fixture → `(criteria, criteria_version)`。

    `criteria_version`＝fixture 的 `criteria_version`（v2＝2026-09-21 補例句那版，§7.7.2；
    **v3＝同日 A2 上線後加第 14 個選項 `wo_serials_status`＋`refuse` 刪「工單序號」那句、
    `R06` 改標 tool，§7.7.4——v3 尚未線上重跑分箱，v2 的 49/50 對 v3 不成立**）；
    讀不到就 0。**版本要跟著每一筆 shadow 紀錄落庫**，否則一週後對帳時
    分不出「哪些題是哪一版 criteria 打的」（換版就得重看分箱＝§7.7 門檻隨版本重校）。
    """
    fx = load_fixture(path)
    try:
        ver = int(fx.get("criteria_version") or 0)
    except (TypeError, ValueError):
        ver = 0
    return choice_criteria(fx), ver


# ── request 組裝（欄位名照 api.md）───────────────────────────────────────────
def build_state(question: object, page: object, language: object) -> dict:
    """**只放三個具名欄**（§7.7 ④）。`page`／`language` 沒有就 null，**不省略**、不塞別的。"""
    return {
        "question": question,
        "page": page if page else None,
        "language": language if language else None,
    }


def build_payload(model: str, state: dict, criteria: dict, *, include_noul: bool) -> dict:
    """`include_noul=True`＝兩題形（choice＋noul，與 2026-09-21 兩次線上校準
    **逐位元相同**，selftest 有釘）；`False`＝現行呼叫端用的一題形。

    §7.7.2 已裁 **noul「該拒答嗎」不可用**（判拒 26 vs 期望 10）⇒ shadow 不送它：
    少一題就少一份可被誤讀的數字，也少一份 input token。
    ★ **criteria v3（2026-09-21，§7.7.4）起 `_jev_calibrate.py` 也不再送 noul**
    ⇒ `include_noul=True` **今天的唯一消費者是本檔 selftest 的 `_FROZEN_PAYLOAD_V1` 釘**。
    **不得因此刪掉這個分支**：刪了就毀掉兩次線上校準送出形的位元存證（那是量尺的同一性，
    不是死碼）。
    """
    questions = {
        Q_CHOICE: {
            "type": "choice",
            "instructions": CHOICE_INSTRUCTIONS,
            "criteria": criteria,
        },
    }
    if include_noul:
        questions[Q_NOUL] = {
            "type": "noul",
            "instructions": NOUL_INSTRUCTIONS,
            "criteria": dict(NOUL_CRITERIA),
        }
    return {"model": model, "state": state, "questions": questions}


# ── shadow 分類（**永不 raise**）──────────────────────────────────────────────
@dataclass
class JevResult:
    """一次 shadow 呼叫的結果。`error` 非 None 時 `predicted`／`confidence` 必為 None。"""
    predicted: str | None = None
    confidence: float | None = None
    model: str | None = None
    criteria_version: int = 0
    error: str | None = None
    elapsed_ms: int = 0


def _timeout_like(exc: BaseException) -> bool:
    """socket 逾時（py3.10+ `socket.timeout is TimeoutError`），含被 URLError 包起來那層。"""
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, TimeoutError):
        return True
    return "timed out" in str(reason if reason is not None else exc).lower()


def classify(question: str, page: object = None, language: object = None, *,
             key: str | None, criteria: dict, criteria_version: int = 0,
             model: str = DEFAULT_MODEL, timeout: float | None = None,
             urlopen=None) -> JevResult:
    """打一次 `/v1/systemone` 的 `choice` 題 → `JevResult`。**永不 raise。**

    - `urlopen`＝注入點（測試用；預設 `urllib.request.urlopen`）⇒ selftest／smoke 零網路。
    - **429／529 不重試**：shadow 是加分動作，等待沒有價值，直接記 `http_429`／`http_529`。
    - 錯誤一律收斂成短 kind（見模組 docstring 的表）——**伺服器 body 與 traceback 永不回傳**，
      這是 key 不外洩的結構保證（不是靠記得呼叫 `_redact`）。
    """
    t0 = time.perf_counter()

    def _ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    def _err(kind: str) -> JevResult:
        return JevResult(criteria_version=criteria_version, error=kind, elapsed_ms=_ms())

    if not key:
        return _err("no_key")
    if not criteria:
        return _err("no_criteria")
    try:
        payload = build_payload(model, build_state(question, page, language),
                                criteria, include_noul=False)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        opener = urlopen or urllib.request.urlopen
        try:
            with opener(req, timeout=(timeout or SHADOW_TIMEOUT)) as resp:
                raw = resp.read(MAX_RESP_BYTES)
        except urllib.error.HTTPError as exc:
            return _err("http_%s" % exc.code)
        except Exception as exc:                     # 連線／DNS／逾時
            return _err("timeout" if _timeout_like(exc) else "net")
        try:
            data = json.loads(raw.decode("utf-8")) or {}
            ans = (data.get("answers") or {}).get(Q_CHOICE) or {}
            choice = ans.get("choice")
            if not isinstance(choice, str) or not choice:
                return _err("bad_response")
            conf = ans.get("confidence")
            conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
            resp_model = data.get("model")
            return JevResult(predicted=choice, confidence=conf,
                             model=resp_model if isinstance(resp_model, str) else None,
                             criteria_version=criteria_version, error=None, elapsed_ms=_ms())
        except Exception:
            return _err("bad_response")
    except Exception:                                # 任何未預期例外都不得往上冒（shadow 不擋人）
        return _err("net")


# ── router 決策面（#172 第三步，2026-09-23；`SPEC-HERMES-TRUST.md` §7.7.5）──────────
# ★ 形＝**指令、不是短路**：命中時在 prompt 前面加一段 `[router]` 指令，LLM 仍持有工具結果與
#   口徑、仍自己寫答案；指令錯最多退化成「今天的路徑」，短路錯＝零證據的拒答。
# ★ **門檻不在這裡**（§7.7.2 裁決 0.9 住 SPEC、預設值與 env 讀取住 app）——本函式只收參數。
# ★ 純函式、零 I/O：同一個 (result, threshold, choices) 永遠回同一個字串（selftest 逐字釘）。
ROUTER_APPLIED = ("tool", "ask", "refuse", "none", "skip")   # U6 `jev_applied` 五態（閉集）

ROUTER_TOOL_FMT = ('[router] This question was classified as needing the tool "%s" '
                   '(confidence %.2f). Call that tool first; do not call other tools '
                   'unless it returns found=false or unavailable.')
ROUTER_NO_TOOL = ('[router] This question was classified as needing no tool '
                  '(confidence %.2f). Answer without calling any tool.')
ROUTER_ASK = ('[router] This question is missing a required identifier (machine EID / '
              'station / time window / work-order or lot number). Ask ONE clarifying '
              'question in the reply language and do not call any tool.')
ROUTER_REFUSE = ('[router] The tools cannot or must not do this (write / prediction / '
                 'operator ranking / aggregation / cost / SQL / external sharing). Say plainly '
                 'it cannot be done, give the closest fact a read-only tool can provide if one '
                 'obviously applies, and do not attempt the request.')


def router_kind(result, threshold: float, choices) -> str:
    """`JevResult` → `ROUTER_APPLIED` 五態之一（`skip` 只看 error；其餘看門檻）。

    - `error` 非 None／`predicted` 空 ⇒ `skip`（呼叫端的逾時也記 `skip`，不經過這裡）。
    - `predicted` 不在 `choices`（這一版 criteria 的選項）／`confidence` 不是數字／
      `confidence < threshold` ⇒ `none`（**不加指令**、交回現行路徑）。
    - 其餘：`ask`／`refuse`／`tool`（`no_tool_context` 也算 `tool`——它是「不用工具」這個選項）。
    """
    if result is None or getattr(result, "error", None) is not None:
        return "skip"
    pred = getattr(result, "predicted", None)
    if not isinstance(pred, str) or not pred:
        return "skip"
    conf = getattr(result, "confidence", None)
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return "none"
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        return "none"
    if not choices or pred not in choices or not (conf >= thr):
        return "none"
    if pred in ("ask", "refuse"):
        return pred
    return "tool"


def router_directive(result, threshold: float, *, choices) -> str | None:
    """命中門檻 ⇒ 回 `[router]` 指令字串；否則 None（＝prompt 一位元不動）。

    `choices`＝這一版 criteria 的選項 key（app 傳 `_JEV_CRITERIA`）；**必填**：
    預測出一個不在選項裡的名字（伺服器怪回應）時絕不能叫 LLM 去呼叫一支不存在的工具。
    """
    kind = router_kind(result, threshold, choices)
    if kind == "ask":
        return ROUTER_ASK
    if kind == "refuse":
        return ROUTER_REFUSE
    if kind == "tool":
        conf = float(result.confidence)
        if result.predicted == "no_tool_context":
            return ROUTER_NO_TOOL % conf
        return ROUTER_TOOL_FMT % (result.predicted, conf)
    return None


# ── selftest（純離線；不碰網路、不需要真 key）────────────────────────────────
# ★ 舊 payload 的**位元存證**：2026-09-21 兩次線上校準（`jev-1.13.0`、v1／v2 criteria）送出的
#   就是這一形。改 `CHOICE_INSTRUCTIONS`／`NOUL_*`／鍵名／欄序 ⇒ 這根當場紅 ⇒ 表示**分箱校準
#   結果作廢、必須重跑**（§7.7：門檻隨版本重校）。不是格式潔癖，是量尺的同一性。
_FROZEN_PAYLOAD_V1 = (
    '{"model": "jev-latest", "state": {"question": "EID-13219 昨天停多久？", '
    '"page": "/equipment", "language": "zh"}, "questions": {"route": {"type": "choice", '
    '"instructions": "Which Tendline Board tool should answer this question? Pick ask if '
    'required information is missing. Pick refuse if the tools cannot or should not do '
    'it.", "criteria": {"ask": "A 描述", "refuse": "B 描述"}}, "refuse_check": {"type": '
    '"noul", "instructions": "Should this question be refused?", "criteria": {"true": '
    '"The question must be refused: the tools cannot do it, or it asks to change data or '
    'caliber.", "false": "The question can be answered by a tool, or answered after one '
    'clarifying question."}}}}'
)


def _fake_ok(choice="updown_now", confidence=0.93, model="jev-1.13.0"):
    """假 urlopen：回一個形狀照 api.md 的 200 回應（context manager + read(n)）。"""
    blob = json.dumps({
        "model": model,
        "answers": {Q_CHOICE: {"type": "choice", "choice": choice,
                               "probabilities": {choice: confidence},
                               "confidence": confidence}},
        "usage": {"input_tokens": 300, "output_tokens": 0},
    }).encode("utf-8")

    class _R:
        def read(self, n=-1):
            return blob

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        return _R()
    return _open


def _fake_raw(blob: bytes):
    class _R:
        def read(self, n=-1):
            return blob

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        return _R()
    return _open


def selftest() -> int:
    import tempfile
    passed = 0

    def ck(cond: bool, what: str) -> None:
        nonlocal passed
        if not cond:
            raise AssertionError(what)
        passed += 1

    root = repo_root()

    # 1) payload 與舊形逐位元相等（校準量尺的同一性）
    crit_demo = {"ask": "A 描述", "refuse": "B 描述"}
    st = build_state("EID-13219 昨天停多久？", "/equipment", "zh")
    p_old = build_payload(DEFAULT_MODEL, st, crit_demo, include_noul=True)
    ck(json.dumps(p_old, ensure_ascii=False) == _FROZEN_PAYLOAD_V1,
       "payload 與 2026-09-21 校準送出的舊形不再逐位元相同（量尺變了＝校準作廢）")
    ck(list(p_old) == ["model", "state", "questions"], "request 頂層欄序不符 api.md")
    ck(list(p_old["questions"]) == [Q_CHOICE, Q_NOUL], "questions 欄序：choice 先 noul 後")
    ck(set(p_old["questions"][Q_NOUL]["criteria"]) == {"true", "false"},
       "noul criteria 必須是 true／false（api.md）")
    for qn, q in p_old["questions"].items():
        ck(set(q) == {"type", "instructions", "criteria"}, "question %s 欄位不符 api.md" % qn)
    ck(len(CHOICE_INSTRUCTIONS) < 220, "instructions 要短（§7.7：越嚴謹校準越差）")

    # 2) shadow 形＝只有 choice 一題（§7.7.2：noul 不可用）
    p_shadow = build_payload(DEFAULT_MODEL, st, crit_demo, include_noul=False)
    ck(set(p_shadow["questions"]) == {Q_CHOICE}, "shadow payload 不得帶 noul 題")
    ck(p_shadow["questions"][Q_CHOICE] == p_old["questions"][Q_CHOICE],
       "shadow 的 choice 題必須與校準那題逐位元相同")
    ck(p_shadow["state"] == p_old["state"], "shadow 與校準的 state 必須同形")

    # 3) state 三欄含 null（不省略）
    s2 = build_state("q", None, None)
    ck(list(s2) == ["question", "page", "language"], "state 欄序／欄名不符")
    ck(s2["page"] is None and s2["language"] is None, "缺值要是 null、不得省略鍵")
    ck(build_state("q", "", "")["page"] is None, "空字串 page 也要正規化成 null")
    ck(json.dumps(s2) and True, "state 必須可序列化")

    # 4) load_criteria 讀真 fixture
    crit, ver = load_criteria(os.path.join(root, FIXTURE_PATH))
    ck(ver == 10, "fixture criteria_version 應為 10（§7.7.4 v8、2026-09-26 #175：+knowledge_card、編號制度定義→explain_metric、工單良率→wo_serials_status、碼義→knowledge_card；refuse 一字不動。v9 2026-10-31：例句識別碼換合成號段、語意零改動；v10 2026-10-31：例句站綽號與站別碼改合成詞（Seal#、segSealDispense）、語意零改動），實得 %r" % ver)
    ck(len(crit) == 29, "criteria 應 29 個選項（26 工具＋no_tool_context＋ask＋refuse），實得 %d" % len(crit))
    ck("_note" not in crit, "criteria 不該帶 _note 進 request")
    ck({"ask", "refuse"} <= set(crit), "criteria 缺 ask／refuse")
    ck(all(isinstance(v, str) and v for v in crit.values()), "criteria 描述不得為空")

    # 5) load_key 三分支（env／檔／都沒有）
    saved = os.environ.get(ENV_VAR)
    try:
        os.environ[ENV_VAR] = "  env-wins-key  "
        ck(load_key("/definitely/not/here/_jev.env") == "env-wins-key", "env 應優先且去空白")
        os.environ.pop(ENV_VAR, None)
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, ENV_FILE)
            io.open(fp, "w", encoding="utf-8").write(
                "# comment\n\nOTHER=1\nTYPESAFE_API_KEY=\"file-key-123\"\nTYPESAFE_API_KEY=second\n")
            ck(load_key(fp) == "file-key-123", "應取檔內**第一個**非空行、去引號")
            io.open(fp, "w", encoding="utf-8").write("TYPESAFE_API_KEY=\n")
            ck(load_key(fp) is None, "空值行應視同沒有")
            ck(load_key(os.path.join(td, "nope.env")) is None, "檔不存在應回 None、不 raise")
            ck(load_key(td) is None, "路徑是目錄時也不得 raise")
    finally:
        if saved is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = saved

    # 6) 遮罩
    fake_key = "ts_live_ABCDEFGHIJKLMNOP1234567890"
    for raw in ("HTTP 401 {\"key\": \"%s\"}" % fake_key,
                "Authorization: Bearer %s\n" % fake_key,
                RuntimeError("boom %s boom" % fake_key)):
        red = _redact(raw, fake_key)
        ck(fake_key not in red, "遮罩沒遮乾淨：%r" % red[:40])
        ck("***" in red, "遮罩應留 *** 標記")
    ck(fake_key not in _redact("Bearer " + fake_key, None), "Bearer 形應無條件遮")
    ck(_redact(None, fake_key) == "", "None 應遮成空字串")

    # 7) classify 成功路徑
    r = classify("EID-12724 現在 up 還是 down", "/updown", "zh",
                 key="k", criteria=crit_demo, criteria_version=2,
                 urlopen=_fake_ok("updown_now", 0.93))
    ck(r.predicted == "updown_now" and abs(r.confidence - 0.93) < 1e-9, "成功路徑解析錯")
    ck(r.error is None and r.model == "jev-1.13.0" and r.criteria_version == 2, "成功路徑欄位錯")
    ck(isinstance(r.elapsed_ms, int) and r.elapsed_ms >= 0, "elapsed_ms 應為非負整數")

    # classify 真的送出 shadow 形（一題、Bearer 在 header 不在 body）
    seen = {}

    def _spy(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = req.data.decode("utf-8")
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        return _fake_ok()(req, timeout)
    classify("q", None, None, key="k-secret", criteria=crit_demo, timeout=7.5, urlopen=_spy)
    ck(seen["url"] == API_URL, "端點錯")
    ck(seen["auth"] == "Bearer k-secret", "key 必須走 Authorization header")
    ck("k-secret" not in seen["body"], "key 絕不進 request body")
    ck(json.loads(seen["body"])["questions"].keys() == {Q_CHOICE}, "shadow 只送 choice 一題")
    ck(json.loads(seen["body"])["state"]["page"] is None, "state.page 缺值送 null")
    ck(seen["timeout"] == 7.5, "timeout 未傳到 urlopen")
    classify("q", None, None, key="k", criteria=crit_demo, urlopen=_spy)
    ck(seen["timeout"] == SHADOW_TIMEOUT, "未指定 timeout 應退回 SHADOW_TIMEOUT")

    # 8) 每一種 error kind（零網路；**永不 raise**）
    ck(classify("q", None, None, key=None, criteria=crit_demo).error == "no_key",
       "無 key 應回 no_key（且不得打網路）")
    ck(classify("q", None, None, key="k", criteria={}).error == "no_criteria",
       "criteria 空應回 no_criteria")

    def _boom(exc):
        def _open(req, timeout=None):
            raise exc
        return _open

    ck(classify("q", None, None, key="k", criteria=crit_demo,
                urlopen=_boom(TimeoutError("timed out"))).error == "timeout", "逾時 kind 錯")
    ck(classify("q", None, None, key="k", criteria=crit_demo,
                urlopen=_boom(urllib.error.URLError(TimeoutError("timed out")))).error == "timeout",
       "URLError 包逾時也算 timeout")
    ck(classify("q", None, None, key="k", criteria=crit_demo,
                urlopen=_boom(urllib.error.URLError(ConnectionRefusedError(111, "refused")))
                ).error == "net", "連線拒絕應為 net")
    for code in (401, 422, 429, 529):
        e = urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b"{}"))
        rr = classify("q", None, None, key="k", criteria=crit_demo, urlopen=_boom(e))
        ck(rr.error == "http_%d" % code, "HTTP %d kind 錯：%r" % (code, rr.error))
        ck(rr.predicted is None and rr.confidence is None, "錯誤時不得帶預測值")
    n_calls = []

    def _count_429(req, timeout=None):
        n_calls.append(1)
        raise urllib.error.HTTPError("http://x", 429, "err", {}, io.BytesIO(b"{}"))
    classify("q", None, None, key="k", criteria=crit_demo, urlopen=_count_429)
    ck(len(n_calls) == 1, "429 不得重試（shadow 不值得等），實打 %d 次" % len(n_calls))
    ck(classify("q", None, None, key="k", criteria=crit_demo,
                urlopen=_fake_raw(b"not json")).error == "bad_response", "壞 JSON kind 錯")
    ck(classify("q", None, None, key="k", criteria=crit_demo,
                urlopen=_fake_raw(b'{"answers": {}}')).error == "bad_response", "缺 answers.route 應 bad_response")
    ck(classify("q", None, None, key="k", criteria=crit_demo,
                urlopen=_fake_raw(b'{"answers": {"route": {"choice": ""}}}')).error == "bad_response",
       "空 choice 應 bad_response")
    r_noconf = classify("q", None, None, key="k", criteria=crit_demo,
                        urlopen=_fake_raw(b'{"answers": {"route": {"choice": "ask"}}}'))
    ck(r_noconf.predicted == "ask" and r_noconf.confidence is None and r_noconf.error is None,
       "缺 confidence 仍算成功、confidence 為 None")
    r_badconf = classify("q", None, None, key="k", criteria=crit_demo,
                         urlopen=_fake_raw(b'{"answers": {"route": {"choice": "ask", "confidence": true}}}'))
    ck(r_badconf.confidence is None, "布林 confidence 不得被當成 1.0")

    # 9) 不管怎樣都不 raise（連 urlopen 自己爆都一樣）
    def _explode(req, timeout=None):
        raise RuntimeError("unexpected")
    ck(classify("q", None, None, key="k", criteria=crit_demo, urlopen=_explode).error == "net",
       "未預期例外應收斂成 net、不得往上冒")
    ck(classify(None, None, None, key="k", criteria=crit_demo,
                urlopen=_fake_ok()).error is None, "question=None 也不得 raise")

    # 10) 錯誤訊息裡絕不出現 key（結構保證：error 只可能是短 kind）
    KINDS_OK = {"no_key", "no_criteria", "timeout", "net", "bad_response"}
    for bad in (_boom(RuntimeError("boom ts_live_SECRET")),
                _fake_raw(b'{"oops": "ts_live_SECRET"}')):
        rr = classify("q", None, None, key="ts_live_SECRET", criteria=crit_demo, urlopen=bad)
        ck(rr.error in KINDS_OK or rr.error.startswith("http_"), "error 不是短 kind：%r" % rr.error)
        ck("ts_live_SECRET" not in (rr.error or ""), "key 滲進 error")

    # 11) router_directive（#172 第三步；§7.7.5）：每一形逐字＋邊界
    CH = {"equipment_downtime": "d", "no_tool_context": "d", "ask": "d", "refuse": "d"}

    def _jr(pred, conf, err=None):
        return JevResult(predicted=pred, confidence=conf, error=err)

    d_tool = router_directive(_jr("equipment_downtime", 0.95), 0.9, choices=CH)
    ck(d_tool == ('[router] This question was classified as needing the tool '
                  '"equipment_downtime" (confidence 0.95). Call that tool first; do not call '
                  'other tools unless it returns found=false or unavailable.'),
       "tool 形指令逐字不符：%r" % d_tool)
    d_none = router_directive(_jr("no_tool_context", 0.97), 0.9, choices=CH)
    ck(d_none == ('[router] This question was classified as needing no tool '
                  '(confidence 0.97). Answer without calling any tool.'),
       "no_tool_context 形逐字不符：%r" % d_none)
    ck(router_directive(_jr("ask", 0.93), 0.9, choices=CH) == ROUTER_ASK, "ask 形應回 ROUTER_ASK")
    ck(ROUTER_ASK.startswith("[router] This question is missing a required identifier")
       and "Ask ONE clarifying question" in ROUTER_ASK and "do not call any tool" in ROUTER_ASK,
       "ask 指令文字不符")
    ck(router_directive(_jr("refuse", 0.91), 0.9, choices=CH) == ROUTER_REFUSE,
       "refuse 形應回 ROUTER_REFUSE")
    ck(ROUTER_REFUSE.startswith("[router] The tools cannot or must not do this")
       and "do not attempt the request" in ROUTER_REFUSE, "refuse 指令文字不符")
    ck(router_directive(_jr("equipment_downtime", 0.9), 0.9, choices=CH) is not None,
       "邊界：confidence == threshold（0.9）必須含")
    ck(router_directive(_jr("equipment_downtime", 0.8999), 0.9, choices=CH) is None,
       "邊界：0.8999 < 0.9 不得含")
    ck(router_directive(_jr("ask", 0.8999), 0.9, choices=CH) is None, "ask 低於門檻不得含")
    ck(router_directive(_jr("equipment_downtime", None), 0.9, choices=CH) is None,
       "confidence=None 不得含")
    ck(router_directive(_jr("equipment_downtime", True), 0.9, choices=CH) is None,
       "confidence=bool 不得當數字")
    ck(router_directive(_jr(None, None, "timeout"), 0.9, choices=CH) is None, "error 不得含")
    ck(router_directive(_jr("equipment_downtime", 0.99, "net"), 0.9, choices=CH) is None,
       "error 非 None 時即使 predicted 有值也不得含")
    ck(router_directive(_jr("drop_table", 0.99), 0.9, choices=CH) is None,
       "predicted 不在 criteria 內不得含（絕不叫 LLM 呼叫不存在的工具）")
    ck(router_directive(_jr("equipment_downtime", 0.99), 0.9, choices={}) is None,
       "choices 空＝沒有量尺 ⇒ 不得含")
    ck(router_directive(None, 0.9, choices=CH) is None, "result=None 不得含")
    ck(router_directive(_jr("equipment_downtime", 0.99), "bad", choices=CH) is None,
       "壞 threshold 不得含（不 raise）")
    ck([router_kind(x, 0.9, CH) for x in (
        _jr("equipment_downtime", 0.95), _jr("ask", 0.93), _jr("refuse", 0.91),
        _jr("equipment_downtime", 0.89), _jr(None, None, "timeout"), _jr("no_tool_context", 0.9))]
       == ["tool", "ask", "refuse", "none", "skip", "tool"], "router_kind 五態對映錯")
    ck(set(ROUTER_APPLIED) == {"tool", "ask", "refuse", "none", "skip"}, "ROUTER_APPLIED 五態閉集")
    ck(all(len(s) < 400 for s in (d_tool, d_none, ROUTER_ASK, ROUTER_REFUSE)),
       "指令要短（每形 < 400 字元）")

    print("%d passed" % passed)
    return 0


def main(argv: list | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Jev（TypeSafe）請求組裝與 shadow 分類的單一出處；規格＝SPEC-HERMES-TRUST §7.7。")
    ap.add_argument("--selftest", action="store_true", help="離線自驗（零網路）")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    ap.error("函式庫模組；請用 --selftest 自驗，或於程式中 import 使用其 API。")
    return 2


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
