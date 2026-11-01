"""core_paths.py — 執行期產物路徑的**唯一出處**（合成產線 Q-F；`_SYNTHLINE-RULINGS.md` 第二批）。

為什麼有這支
------------
repo 內有 24 處（`_SYNTHLINE-WP0-CALLSITES.md` §4）各自拼 `<repo>/_state/...`，另有三個執行期產物
（生產日曆、兩份綑級 raw）直接落在 repo 根（§3、R-5）。合成產線（synth）要在 CI／本機把它們
**整批改向**到一個暫存根，而 live 進程的路徑必須**逐位元不變**。所以：

* **未設 `BOARD_STATE_DIR`**：每個 helper 回傳的字串與改動前的字面**逐字相同**——呼叫端把
  **自己原本的 repo 根運算式**當 `repo=` 傳進來（`os.path.dirname(os.path.abspath(__file__))`、
  `Path(__file__).parent`、`Path(__file__).resolve().parent` 各模組不一，刻意不統一：
  `resolve()` 與 `abspath()` 在 junction／大小寫／8.3 短名下可能不同字串）。`repo` 是
  `pathlib` 物件就回 `pathlib` 物件（`BASE / "_state" / x` 的原形），是字串就回字串。
* **有設 `BOARD_STATE_DIR=<root>`**：
    - `_state/<parts>`       → `<root>/<parts>`（`<root>` 取代 `_state/` 本身）；
    - repo 根產物 `<name>`    → `<root>/_repo_root/<name>`（固定子目錄）。
  既有的專屬 env（`BOARD_EQM_LP_STATE`、`BOARD_WO_LEDGER`、`LLM_CLASS_LEDGER`、
  `LLM_SERVICE_LEDGER`、`BOARD_ASSISTANT_DB`）**優先權高於**本變數——那些判斷留在各呼叫端、
  語意一個字不動。

時機
----
env 在**函式被呼叫時**才讀，不在 import 時定值。但有些既有模組本來就把路徑存成模組常數
（例：`app._MR_SNAP_DIR`、`board_diag._CONFIRMED_PATH`，R-17）——那些地方**保持原本的
import 時機**，所以 `BOARD_STATE_DIR` 必須在 import 之前設好（synth 在進程啟動時設，成立）。
清單＝`IMPORT_TIME_SITES`。

紀律
----
* **零相依**：只用標準函式庫，不 import repo 內任何模組（被 `core_lastprod_push`、`board_data`
  等排程／核心模組 import，自己不能拖進任何東西）。
* 本檔**不建目錄、不寫檔**；純路徑運算。

自驗：`python core_paths.py --selftest`（預設字面對照表、設 env 後全落根下、原始碼掃描、
實際 import 各模組比對；測試用 env 只在子進程內設，寫檔只寫系統暫存目錄）。
"""
import os
from pathlib import PurePath, Path

ENV_STATE_DIR = "BOARD_STATE_DIR"
STATE_DIRNAME = "_state"
REPO_ROOT_SUBDIR = "_repo_root"          # env 設定時，repo 根產物掛到 <root>/_repo_root/

LEDGER_SUBDIR = "daily_ledger"           # 日帳（board_data.ledger_dir、_backfill_ledger）
MONTHLY_SUBDIR = "monthly_ledger"        # 月快照（app._MR_SNAP_DIR）

PROD_CALENDAR_FILE = "_PROD-CALENDAR-LATEST.json"
KUN_ACTIVITY_FILE = "_KUN-ACTIVITY-RAW.json"
KUN_MANPOWER_FILE = "_KUN-MANPOWER-RAW.json"
ASSISTANT_DB_FILE = "board_assistant.sqlite3"
REPO_ROOT_ARTIFACTS = (PROD_CALENDAR_FILE, KUN_ACTIVITY_FILE, KUN_MANPOWER_FILE, ASSISTANT_DB_FILE)

#: 本來就在 import 時定值、刻意保持原時機的地方（R-17）⇒ env 必須在 import 之前設。
IMPORT_TIME_SITES = (
    "app.EQM_LP_STATE",
    "app._MR_SNAP_DIR",
    "board_data.PROD_CALENDAR_PATH",
    "board_data.KUN_MANPOWER_PATH",
    "board_data.KUN_SLOTS_PATH",
    "board_diag._CONFIRMED_PATH (+ EQF_CONFIRMED read at import)",
    "_wo_ledger.DEFAULT_LEDGER",
    "_probe_cost_rollup.STATE_PATH",
    "_probe_llm_classify.STATE",
    "_probe_rw_denominator.LEDGER_DIR",
    "_backfill_ledger.LEDGER_DIR (legacy alias; internal reads are call-time)",
    "_probe_kun_activity.RAW_OUT",
    "_probe_kun_manpower.RAW_OUT",
)


def repo_dir():
    """本檔所在目錄（＝repo 根）。只在呼叫端沒給 `repo=` 時使用。"""
    return os.path.dirname(os.path.abspath(__file__))


def env_state_dir(environ=None):
    """`BOARD_STATE_DIR` 的值（絕對路徑字串）；未設或空白 → None。**呼叫時才讀**。"""
    env = os.environ if environ is None else environ
    v = str(env.get(ENV_STATE_DIR, "") or "").strip()
    return os.path.abspath(v) if v else None


def is_redirected(environ=None):
    return env_state_dir(environ) is not None


#: 脫敏公開樹的標記檔（建置引擎在輸出樹根寫入）。存在＝目前跑在公開樹；自測據此把「需要私有資料／模組」的段落標 skipped。
PUBLIC_TREE_MARKER = "PUBLIC-TREE.md"


def is_public_tree(repo=None):
    """repo 根有 `PUBLIC-TREE.md` ⇒ True（公開樹）。零其他副作用、只判存在。"""
    return os.path.exists(os.path.join(repo or repo_dir(), PUBLIC_TREE_MARKER))


def _join(base, parts):
    if isinstance(base, PurePath):
        return base.joinpath(*parts)
    return os.path.join(base, *parts)


def _root_like(repo, root):
    """env 根要回同型別：呼叫端給 pathlib ⇒ 回 pathlib。"""
    return Path(root) if isinstance(repo, PurePath) else root


def state_path(*parts, repo=None, environ=None):
    """`<repo>/_state/<parts…>`；env 設定時 `<BOARD_STATE_DIR>/<parts…>`。"""
    root = env_state_dir(environ)
    if root is not None:
        return _join(_root_like(repo, root), parts)
    base = repo if repo is not None else repo_dir()
    return _join(base, (STATE_DIRNAME,) + tuple(parts))


def state_dir(repo=None, environ=None):
    """`<repo>/_state`；env 設定時 `<BOARD_STATE_DIR>`。"""
    root = env_state_dir(environ)
    if root is not None:
        return _root_like(repo, root)
    base = repo if repo is not None else repo_dir()
    return _join(base, (STATE_DIRNAME,))


def ledger_dir(repo=None, environ=None):
    """日帳目錄 `<state>/daily_ledger`（board_data 與 _backfill_ledger 共用＝R-18 單一出處）。"""
    return state_path(LEDGER_SUBDIR, repo=repo, environ=environ)


def monthly_ledger_dir(repo=None, environ=None):
    """月快照目錄 `<state>/monthly_ledger`。"""
    return state_path(MONTHLY_SUBDIR, repo=repo, environ=environ)


def repo_root_path(name, repo=None, environ=None):
    """repo 根產物 `<repo>/<name>`；env 設定時 `<BOARD_STATE_DIR>/_repo_root/<name>`。"""
    root = env_state_dir(environ)
    if root is not None:
        return _join(_root_like(repo, root), (REPO_ROOT_SUBDIR, name))
    base = repo if repo is not None else repo_dir()
    return _join(base, (name,))


def prod_calendar_path(repo=None, environ=None):
    return repo_root_path(PROD_CALENDAR_FILE, repo=repo, environ=environ)


def kun_activity_path(repo=None, environ=None):
    return repo_root_path(KUN_ACTIVITY_FILE, repo=repo, environ=environ)


def kun_manpower_path(repo=None, environ=None):
    return repo_root_path(KUN_MANPOWER_FILE, repo=repo, environ=environ)


# =============================================================================
# selftest
# =============================================================================
# 原始碼掃描：非凍結模組內不得再有直接拼 `_state` 的字面（docstring／註解不算）。
FROZEN_MODULES = ("mes_timeline.py", "mes_yield.py", "mes_operator.py", "board_w8.py",
                  # `core_prod_calendar.py`＝QG 凍結產出端（FILES.md「不得為排程改它」）⇒ 不動；
                  # 它寫 repo 根的 `_PROD-CALENDAR-LATEST.json`（OUT_LATEST），見白名單。
                  "core_prod_calendar.py")
# `.claude`（含 `.claude/worktrees/` 別人的工作樹）任何層都不進；`_publish/` 只掃它最上層的
# `.py`（工具碼），**不進它的子目錄**（產出／暫存樹）。
_SCAN_SKIP_DIRS = {".git", ".claude", "worktrees", "__pycache__", "node_modules", ".venv", "venv",
                   ".impeccable"}
_SCAN_TOP_ONLY_DIRS = {"_publish"}
#: (檔名, 字面值) → 理由。字面值＝完全相等或以 `_state/`、`_state\\` 開頭的字串常數。
SCAN_WHITELIST = {
    ("core_paths.py", "*"): "本檔即唯一出處",
    ("run_smoke.py", "*"): "測試檔：暫存目錄改向與負斷言，不是執行期路徑",
    ("wsynth_cmms_selftest.py", "_state"):
        "selftest 對 check_seed_dir 的拒收輸入：`os.path.join(tmp, \"_state\")`，tmp＝tempfile 暫存目錄；"
        "是測試輸入不是執行期狀態路徑（規則分不出基底是暫存目錄，故列白名單）",
    ("board_forecast.py", "_state/fleet_map.json"):
        "selftest 內 str.replace 的比對字面（把 docstring 提到的舊路徑剔除後再掃），不組路徑",
    ("board_llmclass.py", "_state"):
        "selftest 1524：驗 `service_ledger_path(base=_tmp)` 的期望值（顯式 base 參數語意、暫存目錄），不是執行期路徑",
    ("board_diag.py", "_state"):
        "CONFIRMED_OVERLAY_FILE 公開常數（相對檔名、保留相容）；執行期路徑 `_CONFIRMED_PATH` 已改走 core_paths",
}
SCAN_ARTIFACT_WHITELIST = {
    ("core_paths.py", "*"): "本檔即唯一出處",
    ("run_smoke.py", "*"): "測試檔",
    ("core_prod_calendar.py", PROD_CALENDAR_FILE): "凍結產出端（不得為此改動）；synth 依 §7.1 自寫日曆到改向根，不跑本檔",
}


def _is_state_literal(s):
    return s == STATE_DIRNAME or s.startswith(STATE_DIRNAME + "/") or s.startswith(STATE_DIRNAME + "\\")


def _match_list_ids(tree):
    """「只是出現在比對清單裡」的字串常數：tuple／list／set 字面的元素（例：
    `_FORBIDDEN_PARTS = ("_fixtures", "_state")`、`x in ("_state", …)`）。這種清單是負斷言／
    比對用的閉集，不是在組路徑。例外：清單被直接展開（`*`）或整個丟進路徑建構呼叫
    （`join`／`joinpath`／`Path`／`PurePath`）時仍算組路徑。"""
    import ast
    path_calls = {"join", "joinpath", "Path", "PurePath", "PureWindowsPath", "PurePosixPath"}
    tainted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name in path_calls:
                for a in node.args:
                    tainted.add(id(a.value) if isinstance(a, ast.Starred) else id(a))
        elif isinstance(node, ast.Starred):
            tainted.add(id(node.value))
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and id(node) not in tainted:
            for e in node.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    ids.add(id(e))
    return ids


def scan_sources(root=None):
    """→ `(state_hits, artifact_hits)`；每筆 `(relpath, lineno, value)`，已扣白名單、凍結模組與
    比對清單元素（`_match_list_ids`）。"""
    import ast
    root = root or repo_dir()
    state_hits, art_hits = [], []
    for dp, dns, fns in os.walk(root):
        if os.path.basename(dp) in _SCAN_TOP_ONLY_DIRS:
            dns[:] = []
        else:
            dns[:] = [d for d in dns if d not in _SCAN_SKIP_DIRS]
        for fn in fns:
            if not fn.endswith(".py") or fn in FROZEN_MODULES:
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=p)
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                continue
            doc_ids = _match_list_ids(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    doc_ids.add(id(node.value))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                if id(node) in doc_ids:
                    continue
                v = node.value
                if _is_state_literal(v):
                    if (fn, "*") in SCAN_WHITELIST or (fn, v) in SCAN_WHITELIST:
                        continue
                    state_hits.append((rel, node.lineno, v))
                elif v in REPO_ROOT_ARTIFACTS:
                    if (fn, "*") in SCAN_ARTIFACT_WHITELIST or (fn, v) in SCAN_ARTIFACT_WHITELIST:
                        continue
                    art_hits.append((rel, node.lineno, v))
    return state_hits, art_hits


# 每一處呼叫點的「改動前字面」與「改動後呼叫」——**改動前的運算式逐字抄在這裡釘住**。
# base 型別照各模組原本的 repo 根運算式：S＝abspath 字串、P＝Path(__file__).parent、
# R＝Path(__file__).resolve().parent。
def _site_table(S, P, R, svc_name="opswiki_llm_service.json"):
    j = os.path.join
    return [
        # id, 改動前（逐字）, 改動後（呼叫端現在的寫法）
        ("S2 app.EQM_LP_STATE", j(S, "_state", "last_production.json"),
         state_path("last_production.json", repo=S)),
        ("S3 board_data.ledger_dir", j(S, "_state", "daily_ledger"), ledger_dir(repo=S)),
        ("S4 app wip_snapshots (via S3)", j(os.path.dirname(j(S, "_state", "daily_ledger")), "wip_snapshots"),
         j(os.path.dirname(ledger_dir(repo=S)), "wip_snapshots")),
        ("S5 app._MR_SNAP_DIR", str(P / "_state" / "monthly_ledger"), str(monthly_ledger_dir(repo=P))),
        ("S6 app._ld_lastgood_dir", P / "_state" / "landing_lastgood", state_path("landing_lastgood", repo=P)),
        ("S7 app._env_state_path", P / "_state" / "env_sensors" / "latest.json",
         state_path("env_sensors", "latest.json", repo=P)),
        ("S8 board_data.wo_ledger_path", j(S, "_state", "wo_ledger.sqlite"),
         state_path("wo_ledger.sqlite", repo=S)),
        ("S9 _wo_ledger.DEFAULT_LEDGER", R / "_state" / "wo_ledger.sqlite", state_path("wo_ledger.sqlite", repo=R)),
        ("S10 board_data.fleet_map_path", j(S, "_state", "fleet_map.json"), state_path("fleet_map.json", repo=S)),
        ("S11 board_data.cost_parts_cache_path", j(S, "_state", "cmms_parts_reference.json"),
         state_path("cmms_parts_reference.json", repo=S)),
        ("S12 board_diag._CONFIRMED_PATH", j(S, j("_state", "eqf_confirmed_overlay.json")),
         state_path("eqf_confirmed_overlay.json", repo=S)),
        ("S13a board_forecast.inputs_path", j(S, "_state", "forecast_inputs.json"),
         state_path("forecast_inputs.json", repo=S)),
        ("S13b board_forecast.rollup_path", j(S, "_state", "cost_rollup.json"),
         state_path("cost_rollup.json", repo=S)),
        ("S14 board_llmclass.ledger_path", R / "_state" / "opswiki_llm_verdicts.json",
         state_path("opswiki_llm_verdicts.json", repo=R)),
        ("S15 board_llmclass.service_ledger_path", R / "_state" / svc_name,
         state_path(svc_name, repo=R)),
        ("S16 board_llmclass._state_fingerprint", R / "_state", state_dir(repo=R)),
        ("S17 board_assistant._db_path", j(S, "board_assistant.sqlite3"),
         repo_root_path(ASSISTANT_DB_FILE, repo=S)),
        ("J1 _jev_shadow_report.default_db", j(S, "board_assistant.sqlite3"),
         repo_root_path(ASSISTANT_DB_FILE, repo=S)),
        ("T1 core_lastprod_push state_path", j(S, j("_state", "last_production.json")),
         state_path("last_production.json", repo=S)),
        ("T2 _probe_cost_rollup.STATE_PATH", j(S, j("_state", "cost_rollup.json")),
         state_path("cost_rollup.json", repo=S)),
        ("T3 _probe_llm_classify.STATE", R / "_state", state_dir(repo=R)),
        ("T4 _probe_rw_denominator.LEDGER_DIR", R / "_state" / "daily_ledger", ledger_dir(repo=R)),
        ("T5 _backfill_ledger.LEDGER_DIR", j(S, "_state", "daily_ledger"), ledger_dir(repo=S)),
        ("T7 board_eqf_learn.overlay_path", j(S, "_state", "eqf_confirmed_overlay.json"),
         state_path("eqf_confirmed_overlay.json", repo=S)),
        ("P1 board_data.PROD_CALENDAR_PATH", j(S, "_PROD-CALENDAR-LATEST.json"), prod_calendar_path(repo=S)),
        ("P2 board_data.KUN_MANPOWER_PATH", j(S, "_KUN-MANPOWER-RAW.json"), kun_manpower_path(repo=S)),
        ("P3 board_data.KUN_SLOTS_PATH", j(S, "_KUN-ACTIVITY-RAW.json"), kun_activity_path(repo=S)),
        ("W2 _probe_kun_manpower.RAW_OUT", j(S, "_KUN-MANPOWER-RAW.json"), kun_manpower_path(repo=S)),
        ("W3 _probe_kun_activity.RAW_OUT", j(S, "_KUN-ACTIVITY-RAW.json"), kun_activity_path(repo=S)),
    ]


# 子進程內 import 真模組、逐處取值（env 由父進程決定）。pyodbc 換成 connect 即 raise 的 stub。
_PROBE_SRC = r'''
import json, os, sys, types
from pathlib import Path
class _NoMES(Exception): pass
_stub = types.ModuleType("pyodbc")
def _no(*a, **k): raise _NoMES("core_paths selftest: pyodbc is stubbed (zero MES)")
_stub.connect = _no
_stub.Error = _NoMES
_stub.OperationalError = _NoMES
_stub.InterfaceError = _NoMES
_stub.ProgrammingError = _NoMES
sys.modules["pyodbc"] = _stub
sys.path.insert(0, sys.argv[1])
os.chdir(sys.argv[1])
out, err = {}, {}
import importlib
PUBLIC = "--public" in sys.argv
absent = {}
def imp(name, *keys):
    # 公開樹沒有的私有模組（或被砍成子集的模組）⇒ 記入 absent、該模組的取值點 skipped；私有樹一律嚴格（缺就拋）。
    try:
        return importlib.import_module(name)
    except ImportError:
        if not PUBLIC:
            raise
        for k in keys:
            absent[k] = name
        return None
def grab(k, f):
    try:
        out[k] = str(f())
    except Exception as e:
        if PUBLIC and isinstance(e, (AttributeError, TypeError)) and k not in absent:
            absent[k] = "(stub)"
        elif k not in absent:
            err[k] = "%s: %s" % (type(e).__name__, e)
wd = imp("board_data", "S3", "S8", "S10", "S11", "P1", "P2", "P3", "repo_S_wd")
grab("S3", lambda: wd.ledger_dir()); grab("S8", lambda: wd.wo_ledger_path()); grab("S10", lambda: wd.fleet_map_path())
grab("S11", lambda: wd.cost_parts_cache_path())
grab("P1", lambda: wd.PROD_CALENDAR_PATH); grab("P2", lambda: wd.KUN_MANPOWER_PATH)
grab("P3", lambda: wd.KUN_SLOTS_PATH)
wl = imp("_wo_ledger", "S9", "S9c")
grab("S9", lambda: wl.DEFAULT_LEDGER); grab("S9c", lambda: wl.ledger_path())
diag = imp("board_diag", "S12")
grab("S12", lambda: diag._CONFIRMED_PATH)
fc = imp("board_forecast", "S13a", "S13b")
grab("S13a", lambda: fc.inputs_path()); grab("S13b", lambda: fc.rollup_path())
lc = imp("board_llmclass", "S14", "S15", "S16", "S15name", "repo_R")
grab("S14", lambda: lc.ledger_path(environ={k: v for k, v in os.environ.items() if k != "LLM_CLASS_LEDGER"}))
grab("S15", lambda: lc.service_ledger_path(environ={k: v for k, v in os.environ.items() if k != "LLM_SERVICE_LEDGER"}))
grab("S16", lambda: lc._state_fingerprint_dir())
grab("S15name", lambda: lc.service_ledger_name(None))
ef = imp("board_eqf_learn", "T7")
grab("T7", lambda: ef.overlay_path())
wa = imp("board_assistant", "S17")
grab("S17", lambda: wa._db_path())
jsr = imp("_jev_shadow_report", "J1")
grab("J1", lambda: jsr.default_db())
lp = imp("core_lastprod_push", "T1")
grab("T1", lambda: lp.state_file_path())
cr = imp("_probe_cost_rollup", "T2")
grab("T2", lambda: cr.STATE_PATH)
plc = imp("_probe_llm_classify", "T3")
grab("T3", lambda: plc.STATE)
rwd = imp("_probe_rw_denominator", "T4")
grab("T4", lambda: rwd.LEDGER_DIR)
bf = imp("_backfill_ledger", "T5", "T5c")
grab("T5", lambda: bf.LEDGER_DIR); grab("T5c", lambda: bf._ledger_dir())
ka = imp("_probe_kun_activity", "W3")
grab("W3", lambda: ka.RAW_OUT)
km = imp("_probe_kun_manpower", "W2")
grab("W2", lambda: km.RAW_OUT)
if "--with-app" in sys.argv:
    app = imp("app", "S2", "S4", "S5", "S6", "S7", "repo_S", "repo_P")
    grab("S2", lambda: app.EQM_LP_STATE); grab("S5", lambda: app._MR_SNAP_DIR)
    grab("S6", lambda: app._ld_lastgood_dir()); grab("S7", lambda: app._env_state_path())
    grab("S4", lambda: os.path.join(os.path.dirname(wd.ledger_dir()), "wip_snapshots"))
    grab("repo_S", lambda: os.path.dirname(os.path.abspath(app.__file__)))
    grab("repo_P", lambda: app.BASE)
grab("repo_S_wd", lambda: os.path.dirname(os.path.abspath(wd.__file__)))
grab("repo_R", lambda: Path((lc or wd).__file__).resolve().parent)
print(json.dumps({"out": out, "err": err, "absent": absent}))
'''


def _probe_modules(env_over, with_app, py=None):
    import json
    import subprocess
    import sys
    import tempfile
    env = {k: v for k, v in os.environ.items()
           if k not in (ENV_STATE_DIR, "BOARD_EQM_LP_STATE", "BOARD_WO_LEDGER",
                        "LLM_CLASS_LEDGER", "LLM_SERVICE_LEDGER", "BOARD_ASSISTANT_DB")}
    env.update(env_over)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("BOARD_NO_AUTOREBUILD", "1")
    with tempfile.TemporaryDirectory(prefix="core_paths_probe_") as td:
        script = os.path.join(td, "probe.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(_PROBE_SRC)
        args = ([py or sys.executable, script, repo_dir()] + (["--with-app"] if with_app else [])
                + (["--public"] if is_public_tree() else []))
        r = subprocess.run(args, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    last = (r.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        return json.loads(last[0])
    except ValueError:
        raise AssertionError("probe subprocess failed rc=%s\n%s\n%s"
                             % (r.returncode, (r.stdout or "")[-1500:], (r.stderr or "")[-3000:]))


def _selftest(argv=()):
    import sys
    import tempfile
    n = [0]
    fails = []

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            fails.append(msg)
            print("FAIL [%d] %s" % (n[0], msg))

    here = repo_dir()
    S = here
    P = Path(here)
    R = Path(here)
    # ── (1) 預設（env 未設）：helper 回傳＝改動前字面，逐字（含型別） ────────────────────
    clean = {k: v for k, v in os.environ.items() if k != ENV_STATE_DIR}
    saved = os.environ.get(ENV_STATE_DIR)
    try:
        if saved is not None:
            del os.environ[ENV_STATE_DIR]          # 只影響本 selftest 進程（`--selftest` 自成一個）
        for sid, old, new in _site_table(S, P, R):
            ck(type(old) is type(new) and str(old) == str(new),
               "(1) default byte-identical %s: old=%r new=%r" % (sid, old, new))
        # 非本機 base（含 Windows 形、POSIX 形、Path）也逐字
        for base in (r"C:\x\repo", "/srv/repo", Path(r"C:\x\repo")):
            for sid, old, new in _site_table(base if isinstance(base, str) else str(base),
                                             Path(base), Path(base)):
                ck(str(old) == str(new), "(1b) default formula %s base=%r" % (sid, base))
        ck(env_state_dir(clean) is None and env_state_dir({ENV_STATE_DIR: "  "}) is None,
           "(1c) 未設／空白 ⇒ 不改向")
    finally:
        if saved is not None:
            os.environ[ENV_STATE_DIR] = saved

    # ── (2) env 設定：所有路徑都落在根下，沒有一個漏在 repo 內 ──────────────────────────
    with tempfile.TemporaryDirectory(prefix="core_paths_root_") as td:
        envd = {ENV_STATE_DIR: td}
        for base in (S, P):
            vals = [state_path("a", "b.json", repo=base, environ=envd), state_dir(repo=base, environ=envd),
                    ledger_dir(repo=base, environ=envd), monthly_ledger_dir(repo=base, environ=envd),
                    prod_calendar_path(repo=base, environ=envd), kun_activity_path(repo=base, environ=envd),
                    kun_manpower_path(repo=base, environ=envd),
                    repo_root_path(ASSISTANT_DB_FILE, repo=base, environ=envd)]
            for v in vals:
                sv = os.path.normcase(os.path.abspath(str(v)))
                ck(sv == os.path.normcase(td) or sv.startswith(os.path.normcase(td) + os.sep),
                   "(2) redirected under root: %r" % (v,))
                ck(not sv.startswith(os.path.normcase(here) + os.sep), "(2b) not inside repo: %r" % (v,))
                ck(isinstance(v, PurePath) == isinstance(base, PurePath), "(2c) type kept: %r" % (v,))
        ck(str(prod_calendar_path(repo=S, environ=envd)) ==
           os.path.join(os.path.abspath(td), REPO_ROOT_SUBDIR, PROD_CALENDAR_FILE),
           "(2d) repo-root artifacts go under <root>/_repo_root/")
        ck(str(ledger_dir(repo=S, environ=envd)) == os.path.join(os.path.abspath(td), LEDGER_SUBDIR),
           "(2e) _state/x ⇒ <root>/x")

    # ── (3) 原始碼掃描：非凍結模組不再直接拼 `_state` 字面／repo 根產物檔名 ─────────────
    sh, ah = scan_sources(here)
    ck(not sh, "(3) no direct `_state` literal outside core_paths (non-frozen): %r" % (sh,))
    ck(not ah, "(3b) no repo-root artifact literal outside core_paths (non-frozen): %r" % (ah,))
    # (3c) 掃描規則本身：比對清單不算、組路徑要抓；`_publish/` 只掃最上層、`.claude/worktrees/` 不進。
    with tempfile.TemporaryDirectory(prefix="core_paths_scan_") as td:
        def _w(rel, body):
            fp = os.path.join(td, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(body)
        _w("list_only.py", 'import os\nBAD = ("_fixtures", "_state")\n'
                           'def f(x):\n    return x in ["_state", "y"] or x in {"_state"}\n')
        _w("join.py", 'import os\nP = os.path.join("r", "_state", "a.json")\n')
        _w("star.py", 'import os\nP = os.path.join("r", *("_state", "a.json"))\n')
        _w("path_div.py", 'from pathlib import Path\nP = Path("r") / "_state"\n')
        _w("tuple_to_path.py", 'from pathlib import Path\nP = Path(*("r", "_state"))\n')
        _w("_publish/top.py", 'P = "_state/x"\n')
        _w("_publish/out/deep.py", 'P = "_state/x"\n')
        _w(".claude/worktrees/w/other.py", 'P = "_state/x"\n')
        _w("worktrees/x/other.py", 'P = "_state/x"\n')
        hits = sorted(os.path.normcase(r) for r, _l, _v in scan_sources(td)[0])
        want = sorted(os.path.normcase(x) for x in
                      ("join.py", "star.py", "path_div.py", "tuple_to_path.py",
                       os.path.join("_publish", "top.py")))
        ck(hits == want, "(3c) scan rule: match lists skipped, path building caught, "
                         "_publish top-only, worktrees skipped: got=%r" % (hits,))

    # ── (4) 實際 import 各模組（子進程；pyodbc stub）：預設值＝改動前字面 ─────────────────
    with_app = "--no-app" not in argv
    got = _probe_modules({}, with_app)
    o, e = got["out"], got["err"]
    absent = got.get("absent") or {}
    if absent:      # 公開樹缺的私有模組／被裁成子集的模組：該取值點標 skipped、不算失敗；私有樹 absent 恆空（缺即拋）
        print("core_paths selftest: skipped: module absent in public tree: %s" % sorted(set(absent.values())))
    ck(not e, "(4) probe errors: %r" % (e,))
    rs, rr = o.get("repo_S_wd"), o.get("repo_R")
    if rs and rr:
        table = {sid.split()[0]: old for sid, old, _new in
                 _site_table(rs, Path(o.get("repo_P") or rs), Path(rr),
                             o.get("S15name") or "opswiki_llm_service.json")}
        for key in ("S3", "S8", "S9", "S10", "S11", "S12", "S13a", "S13b", "S14", "S15", "S16",
                    "S17", "J1", "T1", "T2", "T3", "T4", "T5", "T7", "P1", "P2", "P3", "W2", "W3"):
            if key in absent:
                continue
            ck(o.get(key) == str(table[key]), "(4) module default %s: got=%r want=%r"
               % (key, o.get(key), str(table[key])))
        for a, b in (("S9c", "S9"), ("T5c", "T5")):
            if a in absent or b in absent:
                continue
            ck(o.get(a) == o.get(b), "(4b) call-time == import-time default %s/%s" % (a, b))
        if with_app:
            ta = {sid.split()[0]: old for sid, old, _new in
                  _site_table(o["repo_S"], Path(o["repo_P"]), Path(rr))}
            for key in ("S2", "S4", "S5", "S6", "S7"):
                if key in absent:
                    continue
                ck(o.get(key) == str(ta[key]), "(4c) app default %s: got=%r want=%r"
                   % (key, o.get(key), str(ta[key])))

    # ── (5) 實際 import 各模組（子進程）＋ env 設定：每一處都落在根下 ──────────────────────
    with tempfile.TemporaryDirectory(prefix="core_paths_env_") as td:
        got = _probe_modules({ENV_STATE_DIR: td}, with_app)
        o, e = got["out"], got["err"]
        ck(not e, "(5) probe errors (env): %r" % (e,))
        tdn = os.path.normcase(os.path.abspath(td))
        for k, v in sorted(o.items()):
            if k.startswith("repo_") or k == "S15name":
                continue
            sv = os.path.normcase(os.path.abspath(v))
            ck(sv == tdn or sv.startswith(tdn + os.sep), "(5) %s under root: %r" % (k, v))
    # 主線／worktree 狀態目錄沒有被本 selftest 建出任何東西：本檔零寫檔（上面全是暫存目錄）。

    if fails:
        print("core_paths selftest FAIL (%d/%d)" % (len(fails), n[0]))
        return 1
    print("core_paths selftest PASS (%d checks)" % n[0])
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest(sys.argv[1:]))
    print(__doc__)
