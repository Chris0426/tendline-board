# -*- coding: utf-8 -*-
"""core_source.py — 來源值（source）的唯一出處＋合成進程旗標＋啟動絆線（合成產線地基）
======================================================================================

依據：SPEC-SYNTHLINE §2.1、C-SY3／C-SY4／C-SY11、§4 反模式；`_SYNTHLINE-RULINGS.md`
Q3、Q-A、Q-B、Q-C、Q-K；`_SYNTHLINE-WP0-CALLSITES.md` §5、§10 R-1／R-2／R-3／R-17。

這支提供五件事
--------------
1. **來源值常數**：`SOURCE_*`、`CURRENT_SOURCES`（＝app 現行 `_SOURCES`，順序相同）、
   `ALL_SOURCES`（多一個 `synth`）、`LIVE_LIKE`（走 live 資料路徑的來源值）。
   判斷：`is_live_like()`、`is_synth_source()`、`is_known_source()`。
2. **進程旗標**（Q-A：I/O 注入看進程旗標，不看請求的 source 參數）：`is_synth_process()`
   在**第一次被呼叫時**讀環境變數 `BOARD_SOURCE` 並鎖定；之後環境變數再變也不改結果。
   `BOARD_SOURCE` 本來就被 live／demo 服務與測試拿來設預設 source（值可能是 live、demo、
   或任意字串）⇒ 只有「去空白、轉小寫後恰為 synth」才算合成進程；其餘一律不是、不報錯。
3. **正規化**（Q-B）：`normalize_source()`。合成進程收到任何值 ⇒ `synth`；非合成進程收到
   `synth` ⇒ 回 `None`（拒絕訊號，呼叫端自己決定 404 或塌成預設）；其他值＝app 現行規則
   （在 `CURRENT_SOURCES` 內原樣回傳，否則 `demo`）。`source_key()`＝不會回 None 的版本，
   非合成進程下與 app 現行 `_source_key` 對**所有輸入**等價（自測釘住）。
4. **啟動絆線**（C-SY3／C-SY4）：`startup_tripwire()`。合成進程啟動時呼叫一次；任何一條不過
   ⇒ `SynthStartupError`（附完整摘要）。**非合成進程呼叫時什麼都不做**：不 import、不讀檔、
   不寫檔、不裝替身，直接回 `{"synth": False, ...}`。
5. **標示 helper**（C-SY11）：`source_label_key()` 只回 i18n 鍵、不回文字。

零相依
------
只用標準函式庫。**不 import repo 內任何模組**；唯一例外是在絆線函式內部延遲 import
`wsynth_clock`（UTC 偏移、標記檔時間）與 `core_paths`（狀態目錄改向的唯一出處）。
live 進程 import 本檔不會載入任何 `wsynth_` 開頭的模組（自測以已載入模組清單釘住）。

自測：`python core_source.py --selftest`（測試用環境變數只在子進程內設；寫檔只寫系統暫存目錄）。
"""
import os
import sys
import threading
import types

# =============================================================================
# 1. 來源值常數（唯一出處）
# =============================================================================
SOURCE_DEMO = "demo"
SOURCE_SNAPSHOT = "snapshot"
SOURCE_LIVE = "live"
SOURCE_SYNTH = "synth"

#: app 現行合法值（與 app `_SOURCES` 同值同序；自測釘住）。
CURRENT_SOURCES = (SOURCE_DEMO, SOURCE_SNAPSHOT, SOURCE_LIVE)
#: 含合成來源的全部合法值。
ALL_SOURCES = CURRENT_SOURCES + (SOURCE_SYNTH,)
#: 走 live 資料路徑的來源值（SPEC §2.1 第 5 點）。只管路由、快取、寫入閘、標示（Q-A）。
LIVE_LIKE = (SOURCE_LIVE, SOURCE_SYNTH)
#: 未知值的塌縮目標（app 現行行為）。
DEFAULT_SOURCE = SOURCE_DEMO

#: 拒絕訊號：非合成進程收到合成來源值時 `normalize_source()` 回這個。
REJECTED = None

# 環境變數名
ENV_SOURCE = "BOARD_SOURCE"                  # 進程旗標（啟動時設定）
ENV_CMMS_BASE_URL = "BOARD_CMMS_BASE_URL"    # 與 board_w8_transport／app 同名
ENV_SANDBOX_HOSTS = "BOARD_SYNTH_CMMS_HOSTS"  # 合成進程允許的 sandbox cmms 主機（逗號分隔）

# 標記檔
MARKER_FILE = "SYNTH_WORLD.json"
MARKER_KIND = "tendline.synth_world_marker"
MARKER_VERSION = 1

# 絆線對象
DB_DRIVER_MODULES = ("pyodbc",)
MES_MODULE_PREFIX = "mes_"
SYNTH_MODULE_PREFIX = "wsynth_"

# 標示鍵（C-SY11）：只回鍵；文字由 i18n 提供。
LABEL_KEY_PREFIX = "source_label_"
LABEL_KEYS = {s: LABEL_KEY_PREFIX + s for s in ALL_SOURCES}

# 絆線檢查名（摘要與例外內用）
CHECK_UTC_OFFSET = "utc_offset"
CHECK_DB_DRIVER = "db_driver"
CHECK_MES_IMPORT_ORDER = "mes_import_order"
CHECK_CMMS_BASE_URL = "cmms_base_url"
CHECK_STATE_DIR = "state_dir"
CHECK_NAMES = (CHECK_UTC_OFFSET, CHECK_DB_DRIVER, CHECK_MES_IMPORT_ORDER,
               CHECK_CMMS_BASE_URL, CHECK_STATE_DIR)


class SynthLeakError(RuntimeError):
    """合成進程內有程式碼嘗試使用資料庫驅動（未轉接的真源路徑）。

    替身模組把 DB-API 的例外名（`Error`、`OperationalError`…）都指向本類別，所以既有的
    `except pyodbc.Error` 會把它當成「連線失敗」處理＝頁面誠實降級，而不是靜默打到真源。"""


class SynthStartupError(RuntimeError):
    """合成進程啟動絆線不過。`.summary`＝完整摘要（每一條的結果）。"""

    def __init__(self, message, summary):
        RuntimeError.__init__(self, message)
        self.summary = summary


class SynthStateError(RuntimeError):
    """合成進程的寫入點發現狀態目錄沒有標記檔（`assert_state_marker`）。"""


# =============================================================================
# 1b. 來源值判斷
# =============================================================================
def is_live_like(source):
    """source 是否走 live 資料路徑（`live` 或 `synth`，逐字相等、大小寫敏感）。"""
    return isinstance(source, str) and source in LIVE_LIKE


def is_synth_source(source):
    """source 是否為合成來源值（逐字 `synth`）。"""
    return isinstance(source, str) and source == SOURCE_SYNTH


def is_known_source(source):
    """source 是否為 `ALL_SOURCES` 之一。"""
    return isinstance(source, str) and source in ALL_SOURCES


# =============================================================================
# 2. 進程旗標（第一次呼叫時讀 BOARD_SOURCE 並鎖定）
# =============================================================================
_FLAG_LOCK = threading.Lock()
_FLAG = None          # (is_synth: bool, env_raw: str|None)


def _parse_env_source(raw):
    return str(raw if raw is not None else "").strip().lower() == SOURCE_SYNTH


def is_synth_process():
    """本進程是不是合成進程。第一次呼叫時讀 `BOARD_SOURCE` 並鎖定（之後 env 再變也不改）。"""
    global _FLAG
    f = _FLAG
    if f is None:
        with _FLAG_LOCK:
            if _FLAG is None:
                raw = os.environ.get(ENV_SOURCE)
                _FLAG = (_parse_env_source(raw), raw)
            f = _FLAG
    return f[0]


#: 標示用別名（SPEC／裁決文字稱 `is_synth`）。
is_synth = is_synth_process


def process_flag_info():
    """→ `{"synth": bool, "env_raw": str|None}`（會觸發鎖定）。供啟動日誌列印。"""
    is_synth_process()
    return {"synth": _FLAG[0], "env_raw": _FLAG[1]}


# =============================================================================
# 3. 來源值正規化（Q-B）
# =============================================================================
def _current_rule(source):
    """app 現行 `_source_key` 的規則，逐字等價：在合法值內原樣回傳，否則 demo。"""
    return source if source in CURRENT_SOURCES else DEFAULT_SOURCE


def _resolve_proc(synth_process):
    return is_synth_process() if synth_process is None else bool(synth_process)


def normalize_source(source, synth_process=None):
    """請求帶進來的 source → 本進程要用的 source。

    * 合成進程：任何值（含 live／demo／snapshot／垃圾／None）一律回 `"synth"`（Q-B）。
    * 非合成進程：
        - `"synth"` ⇒ 回 `REJECTED`（None）。呼叫端決定回 404 或塌成預設；**永不**進合成路徑。
        - 其他值 ⇒ app 現行規則：在 `CURRENT_SOURCES` 內原樣回傳，否則 `"demo"`。
    `synth_process` 只供測試注入；正式呼叫一律省略（讀鎖定的進程旗標）。"""
    if _resolve_proc(synth_process):
        return SOURCE_SYNTH
    if is_synth_source(source):
        return REJECTED
    return _current_rule(source)


def source_key(source, synth_process=None):
    """同 `normalize_source`，但拒絕訊號塌成 `DEFAULT_SOURCE`（給快取鍵等不能回 None 的地方）。
    非合成進程下與 app 現行 `_source_key` 對所有輸入等價。"""
    r = normalize_source(source, synth_process=synth_process)
    return DEFAULT_SOURCE if r is REJECTED else r


# =============================================================================
# 5. 標示 helper（C-SY11）
# =============================================================================
def source_label_key(source=None, synth_process=None):
    """本進程應顯示的來源標籤 **i18n 鍵**（不回文字）。

    合成進程 ⇒ 恆為 `source_label_synth`（不論請求帶什麼）。
    非合成進程 ⇒ 依 `source_key(source)`：live／snapshot／demo 各自的鍵；`synth` 與未知值塌成 demo 鍵。"""
    if _resolve_proc(synth_process):
        return LABEL_KEYS[SOURCE_SYNTH]
    return LABEL_KEYS[source_key(source, synth_process=False)]


# =============================================================================
# 5b. 日帳／月快照的來源隔離（Q-C／C-SY3；落地帳讀端的**唯一判斷**）
# =============================================================================
def ledger_source_admitted(value, synth_process=None):
    """落地帳（日帳 doc、月快照 doc）的 `source` 欄值 → 本進程收不收。

    * 正規化＝`str(value or "").strip().lower()`（與寫端 `board_attrib.should_persist` 同一正規化）。
    * 合成進程：**只收合成值**；`live`、其他值、**欄位缺席的舊帳**一律拒。
    * 非合成進程：**只拒合成值**；`live`、其他值、欄位缺席的舊帳照舊收（＝改動前行為）。
    被拒的帳由讀端視同不存在並計數（`board_data.read_ledger_doc`、`board_monthly.read_snapshot`）。
    `synth_process` 只供測試注入；正式呼叫一律省略（讀鎖定的進程旗標）。"""
    synth_doc = is_synth_source(str(value or "").strip().lower())
    return synth_doc if _resolve_proc(synth_process) else not synth_doc


def ledger_doc_admitted(doc, synth_process=None):
    """落地帳 doc（json 讀出來的原物件）→ 本進程收不收（判準＝`ledger_source_admitted`）。
    非 dict 原樣放行（壞檔／形狀不符由各讀端既有的 parse／validate 照舊處理）。"""
    if not isinstance(doc, dict):
        return True
    return ledger_source_admitted(doc.get("source"), synth_process=synth_process)


# =============================================================================
# 4. 啟動絆線（C-SY3／C-SY4）
# =============================================================================
_DBAPI_EXC_NAMES = ("Warning", "Error", "InterfaceError", "DatabaseError", "DataError",
                    "OperationalError", "IntegrityError", "InternalError", "ProgrammingError",
                    "NotSupportedError")

_TRIP_LOCK = threading.Lock()
_TRIP_RESULT = None


def make_driver_stub(name):
    """資料庫驅動替身：`connect` 一呼叫就拋 `SynthLeakError`；DB-API 例外名都指向它；
    其他非 dunder 屬性一讀就拋 `SynthLeakError`（大聲失敗）。"""
    m = types.ModuleType(name)
    m.__doc__ = "synthetic-process stand-in for the database driver (core_source tripwire)"
    m.__core_source_stub__ = True

    def connect(*_a, **_k):
        raise SynthLeakError("%s.connect() called inside a synthetic process "
                             "(unadapted real-source path)" % name)

    m.connect = connect
    for exc in _DBAPI_EXC_NAMES:
        setattr(m, exc, SynthLeakError)

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        raise SynthLeakError("%s.%s used inside a synthetic process" % (name, attr))

    m.__getattr__ = __getattr__
    return m


def is_driver_stub(mod):
    """mod 是否為本檔裝上的替身。"""
    try:
        return mod is not None and mod.__dict__.get("__core_source_stub__") is True
    except Exception:
        return False


def _norm_host(h):
    return str(h or "").strip().lower().rstrip(".")


def is_loopback_host(host):
    """`localhost`、127.0.0.0/8、`::1`（含 IPv4 映射的 loopback）⇒ True。看起來像的主機名一律 False。"""
    import ipaddress
    h = _norm_host(host)
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped is not None and mapped.is_loopback)


def parse_sandbox_hosts(raw):
    """`BOARD_SYNTH_CMMS_HOSTS` 的值 → `((host, port|None), ...)`。
    分隔＝逗號或空白；每項是 `host` 或 `host:port`（IPv6 用 `[addr]:port`）。
    帶 scheme、路徑、`@`、反斜線的項目 ⇒ `ValueError`（設定錯＝拒絕啟動）。"""
    from urllib.parse import urlsplit
    out = []
    for item in str(raw or "").replace(",", " ").split():
        if any(c in item for c in "/@\\?#") or "://" in item:
            raise ValueError("sandbox host entry must be host or host:port, got %r" % (item,))
        try:
            parts = urlsplit("//" + item)
            host, port = parts.hostname, parts.port
        except ValueError as exc:
            raise ValueError("unparseable sandbox host entry %r (%s)" % (item, exc))
        if not host:
            raise ValueError("sandbox host entry without host: %r" % (item,))
        out.append((_norm_host(host), port))
    return tuple(out)


def cmms_url_allowed(url, allowed_hosts=()):
    """cmms base URL 是否可用於合成進程 → `(ok: bool, detail: str)`。純函式。

    * 空值 ⇒ ok（cmms 未設定＝Board 端既有降級；不外連）。
    * scheme 只准 http／https；不准 userinfo、query、fragment、反斜線、空白字元。
    * 主機是 loopback ⇒ ok（任何埠）。
    * 否則必須在 `allowed_hosts`（`parse_sandbox_hosts` 的輸出）內；項目帶埠時埠也要相符
      （URL 未寫埠時以 scheme 預設埠比）。白名單為空 ⇒ 拒絕。"""
    from urllib.parse import urlsplit
    s = str(url or "").strip()
    if not s:
        return True, "unset (cmms disabled; no outbound)"
    if any(c in s for c in "\\ \t\r\n"):
        return False, "backslash/whitespace in URL"
    try:
        parts = urlsplit(s)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        return False, "unparseable URL (%s)" % (exc,)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return False, "scheme %r not allowed" % (parts.scheme,)
    if not host:
        return False, "no host"
    if parts.username is not None or parts.password is not None:
        return False, "userinfo not allowed"
    if parts.query or parts.fragment:
        return False, "query/fragment not allowed in base URL"
    if is_loopback_host(host):
        return True, "loopback"
    eff_port = port if port is not None else (443 if scheme == "https" else 80)
    h = _norm_host(host)
    for ah, ap in allowed_hosts or ():
        if h == ah and (ap is None or ap == eff_port):
            return True, "sandbox allowlist"
    if not allowed_hosts:
        return False, "host is not loopback and the sandbox allowlist is empty"
    return False, "host not in sandbox allowlist"


def _default_repo_root():
    return os.path.dirname(os.path.abspath(__file__))


def _canon(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _is_within(child, parent):
    c, p = _canon(child), _canon(parent)
    if c == p:
        return True
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:            # 不同磁碟機
        return False


def _git_ancestor(path):
    """path（或其最近的既存祖先）往上任一層有 `.git` ⇒ 回那一層；否則 None。"""
    p = _canon(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def _read_marker(path):
    import json
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or doc.get("kind") != MARKER_KIND:
        raise ValueError("marker kind mismatch")
    return doc


def _check_state_dir(repo_root, environ):
    """→ (ok, detail, state_dir|None, action)；action ∈ {"present", "create", None}。只讀不寫。"""
    import core_paths
    sd = core_paths.env_state_dir(environ)
    if sd is None:
        return False, "%s is not set (state must be redirected)" % core_paths.ENV_STATE_DIR, None, None
    if _is_within(sd, repo_root):
        return False, "state dir is inside the repo (%s)" % sd, sd, None
    g = _git_ancestor(sd)
    if g is not None:
        return False, "state dir is inside a git work tree (%s)" % g, sd, None
    if os.path.exists(sd) and not os.path.isdir(sd):
        return False, "state dir path exists and is not a directory (%s)" % sd, sd, None
    entries = os.listdir(sd) if os.path.isdir(sd) else []
    if MARKER_FILE in entries:
        try:
            _read_marker(os.path.join(sd, MARKER_FILE))
        except Exception as exc:
            return False, "marker %s is invalid (%s: %s)" % (MARKER_FILE, type(exc).__name__, exc), sd, None
        return True, "marker present", sd, "present"
    if entries:
        return False, "state dir is not empty and has no %s (refusing to adopt it)" % MARKER_FILE, sd, None
    return True, "empty; marker will be created", sd, "create"


def _create_marker(sd):
    import json
    import wsynth_clock
    os.makedirs(sd, exist_ok=True)
    doc = {"kind": MARKER_KIND, "version": MARKER_VERSION,
           "created_at": wsynth_clock.now().isoformat(sep=" "),
           "created_by": "core_source.startup_tripwire"}
    path = os.path.join(sd, MARKER_FILE)
    with open(path, "x", encoding="utf-8") as f:     # 排他建立：並行啟動只會有一個成功
        json.dump(doc, f, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    return path


def _install_driver_stubs():
    for name in DB_DRIVER_MODULES:
        if not is_driver_stub(sys.modules.get(name)):
            sys.modules[name] = make_driver_stub(name)


def startup_tripwire(repo_root=None, environ=None, utc_offset=None):
    """合成進程啟動絆線。**非合成進程：什麼都不做**，回 `{"synth": False, "ok": True, "checks": []}`。

    合成進程依序檢查（全部跑完才判；任何一條不過 ⇒ `SynthStartupError`，`.summary` 附全部結果）：
      utc_offset        本機 UTC 偏移＝+8（委派 `wsynth_clock.check_utc_offset`）。
      db_driver         真驅動（`DB_DRIVER_MODULES`）尚未被載入；把它換成替身（不論成敗都裝）。
      mes_import_order  沒有任何 `mes_` 開頭的模組已被載入（替身必須先於 MES 模組）。
      cmms_base_url     `BOARD_CMMS_BASE_URL` 空、loopback、或在 `BOARD_SYNTH_CMMS_HOSTS` 白名單內。
      state_dir         `BOARD_STATE_DIR` 已設、不在 repo 內（也不在任何 git 工作樹內）、
                        是目錄或不存在；有合法標記檔，或為空（全部通過後才建標記檔）；
                        非空無標記 ⇒ 拒絕。
    通過 ⇒ 回摘要並快取；再次呼叫直接回同一份摘要（冪等）。

    參數只供測試注入：`repo_root`（預設＝本檔所在目錄）、`environ`（預設 `os.environ`；
    進程旗標不受它影響）、`utc_offset`（`datetime.timedelta`；預設讀本機）。"""
    global _TRIP_RESULT
    if not is_synth_process():
        return {"synth": False, "ok": True, "checks": []}
    with _TRIP_LOCK:
        if _TRIP_RESULT is not None:
            return _TRIP_RESULT
        env = os.environ if environ is None else environ
        repo = _default_repo_root() if repo_root is None else repo_root
        checks = []

        def add(name, ok, detail):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        # utc_offset
        import wsynth_clock
        try:
            off = wsynth_clock.check_utc_offset(utc_offset)
            add(CHECK_UTC_OFFSET, True, "UTC%s" % _fmt_offset(off))
        except wsynth_clock.ClockOffsetError as exc:
            add(CHECK_UTC_OFFSET, False, str(exc))

        # db_driver ＋ mes_import_order（先看、再裝替身）
        real = [n for n in DB_DRIVER_MODULES
                if sys.modules.get(n) is not None and not is_driver_stub(sys.modules.get(n))]
        mes_loaded = sorted(n for n in list(sys.modules) if n.startswith(MES_MODULE_PREFIX))
        _install_driver_stubs()
        add(CHECK_DB_DRIVER, not real,
            ("stub installed for %s" % ", ".join(DB_DRIVER_MODULES)) if not real
            else "real driver already imported before tripwire: %s" % ", ".join(real))
        add(CHECK_MES_IMPORT_ORDER, not mes_loaded,
            "no MES module loaded yet" if not mes_loaded
            else "MES modules imported before tripwire: %s" % ", ".join(mes_loaded))

        # cmms_base_url
        try:
            hosts = parse_sandbox_hosts(env.get(ENV_SANDBOX_HOSTS, ""))
            ok, detail = cmms_url_allowed(env.get(ENV_CMMS_BASE_URL, ""), hosts)
        except ValueError as exc:
            ok, detail = False, "%s malformed: %s" % (ENV_SANDBOX_HOSTS, exc)
        add(CHECK_CMMS_BASE_URL, ok, detail)

        # state_dir（只讀）
        try:
            ok, detail, sd, action = _check_state_dir(repo, env)
        except Exception as exc:
            ok, detail, sd, action = False, "%s: %s" % (type(exc).__name__, exc), None, None
        add(CHECK_STATE_DIR, ok, detail)

        all_ok = all(c["ok"] for c in checks)
        marker = action if all_ok else None
        if all_ok and action == "create":
            try:
                _create_marker(sd)
                checks[-1]["detail"] = "marker created"
                marker = "created"
            except Exception as exc:
                checks[-1]["ok"] = False
                checks[-1]["detail"] = "marker creation failed (%s: %s)" % (type(exc).__name__, exc)
                all_ok = False
                marker = None
        summary = {"synth": True, "ok": all_ok, "env_source": _FLAG[1] if _FLAG else None,
                   "state_dir": sd, "marker": marker,
                   "clock_frozen": wsynth_clock.is_frozen(env), "checks": checks}
        if not all_ok:
            bad = ["%s: %s" % (c["name"], c["detail"]) for c in checks if not c["ok"]]
            raise SynthStartupError("synthetic process refused to start: " + " | ".join(bad), summary)
        _TRIP_RESULT = summary
        return summary


def _fmt_offset(td):
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, minutes = divmod(total // 60, 60)
    return "%s%02d:%02d" % (sign, hours, minutes)


def format_summary(summary):
    """摘要 → 啟動日誌行（list[str]）。"""
    if not summary.get("synth"):
        return ["[core_source] not a synthetic process; tripwire skipped"]
    lines = ["[core_source] synthetic process tripwire: %s (state_dir=%s, marker=%s, clock_frozen=%s)"
             % ("PASS" if summary.get("ok") else "REFUSED", summary.get("state_dir"),
                summary.get("marker"), summary.get("clock_frozen"))]
    for c in summary.get("checks", []):
        lines.append("[core_source]   %-16s %s  %s" % (c["name"], "ok  " if c["ok"] else "FAIL", c["detail"]))
    return lines


def tripwire_summary():
    """已通過的絆線摘要；尚未通過（或非合成進程）⇒ None。"""
    return _TRIP_RESULT


def assert_state_marker(environ=None):
    """寫入點用（WP0 §5-7 第 4 點）：合成進程下斷言狀態目錄有標記檔，否則 `SynthStateError`。
    非合成進程：什麼都不做（不 import、不讀檔）、回 None。"""
    if not is_synth_process():
        return None
    import core_paths
    sd = core_paths.env_state_dir(environ)
    if sd is None:
        raise SynthStateError("synthetic process without %s" % core_paths.ENV_STATE_DIR)
    path = os.path.join(sd, MARKER_FILE)
    try:
        _read_marker(path)
    except Exception as exc:
        raise SynthStateError("synthetic state marker missing or invalid at %s (%s)" % (path, exc))
    return path


def _reset_for_tests(uninstall_stub=False):
    """**只給測試用**：清掉進程旗標與絆線快取（下一次呼叫重新讀 env）。可選擇拆掉替身。"""
    global _FLAG, _TRIP_RESULT
    with _FLAG_LOCK:
        _FLAG = None
    with _TRIP_LOCK:
        _TRIP_RESULT = None
    if uninstall_stub:
        for name in DB_DRIVER_MODULES:
            if is_driver_stub(sys.modules.get(name)):
                del sys.modules[name]


__all__ = [
    "SOURCE_DEMO", "SOURCE_SNAPSHOT", "SOURCE_LIVE", "SOURCE_SYNTH", "CURRENT_SOURCES",
    "ALL_SOURCES", "LIVE_LIKE", "DEFAULT_SOURCE", "REJECTED", "ENV_SOURCE", "ENV_CMMS_BASE_URL",
    "ENV_SANDBOX_HOSTS", "MARKER_FILE", "MARKER_KIND", "DB_DRIVER_MODULES", "LABEL_KEYS",
    "CHECK_NAMES", "SynthLeakError", "SynthStartupError", "SynthStateError",
    "is_live_like", "is_synth_source", "is_known_source", "is_synth_process", "is_synth",
    "process_flag_info", "normalize_source", "source_key", "source_label_key",
    "ledger_source_admitted", "ledger_doc_admitted",
    "startup_tripwire", "format_summary", "tripwire_summary", "assert_state_marker",
    "make_driver_stub", "is_driver_stub", "is_loopback_host", "parse_sandbox_hosts",
    "cmms_url_allowed",
]


# =============================================================================
# selftest
# =============================================================================
class _StrSub(str):
    pass


def _norm_inputs():
    return ["demo", "snapshot", "live", "synth", "", None, "LIVE", "Live", " live", "live ",
            "SYNTH", "Synth", " synth", "synthetic", "bogus", "demo\x00", "snapshot\n",
            0, 1, 1.5, True, float("nan"), ("live",), b"live", [], {},
            _StrSub("live"), _StrSub("synth"), _StrSub("demo")]


def _app_literal_collapses(repo):
    """讀 app.py（只讀、不 import、不 exec）：列出**還留著字面**的路由入口塌縮
    `if X not in (<三個合法值>|_SOURCES): X = "demo"` → [行號]。WP-4a 之後應為空
    （全部改走 `core_source`），留著字面＝有一處沒接上唯一出處。"""
    import ast
    path = os.path.join(repo, "app.py")
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
            continue
        t = node.test
        if not (isinstance(t.left, ast.Name) and len(t.ops) == 1 and isinstance(t.ops[0], ast.NotIn)):
            continue
        comp = t.comparators[0]
        lit = (isinstance(comp, ast.Tuple) and all(isinstance(e, ast.Constant) for e in comp.elts)
               and set(e.value for e in comp.elts) == set(CURRENT_SOURCES))
        if lit or (isinstance(comp, ast.Name) and comp.id == "_SOURCES"):
            out.append(node.lineno)
    return out


#: 子進程內 import app，逐輸入比對 app 對外的來源值行為（E 段）。
_APP_EQ_CHILD = r"""
import core_source as S
if ARG_STUB:
    m = types.ModuleType("pyodbc")
    def _no(*a, **k):
        raise RuntimeError("core_source selftest: pyodbc stubbed (zero MES)")
    m.connect = _no
    m.Error = RuntimeError
    sys.modules["pyodbc"] = m
os.chdir(sys.argv[1])
import app as A
bad = []
n = 0
for i, v in enumerate(S._norm_inputs()):
    n += 1
    a, r = A._source_key(v), A._route_source(v)
    if ARG_SYNTH:
        ok = a == S.SOURCE_SYNTH and r == S.SOURCE_SYNTH
    else:
        old = S._current_rule(v)
        ok = (a == old and r == old and type(a) is type(old)
              and a == S.source_key(v) and (S.normalize_source(v) == a or S.is_synth_source(v)))
    if not ok:
        bad.append("%d:%r -> key=%r route=%r" % (i, v, a, r))
R["n"] = n
R["bad"] = bad
R["sources"] = list(A._SOURCES)
R["synth_proc"] = A._SYNTH_PROC
R["default"] = A.DEFAULT_SOURCE
R["wsynth"] = sorted(k for k in sys.modules if k.startswith("wsynth_"))
"""


_TAIL = 3 * 10 ** 3          # 子進程失敗時保留的輸出尾長
_ID_LIMIT = 10 ** 3          # 原始碼內數值字面的上限（防編號字面值）


def _child_env(over=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(over or {})
    return env


_CHILD_PRELUDE = r'''
import json, os, sys, types, datetime
sys.path.insert(0, sys.argv[1])
TD = sys.argv[2]
R = {}
'''


def _run_child(code, env_over=None, cwd=None):
    import json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="core_source_child_") as td:
        r = subprocess.run([sys.executable, "-c", _CHILD_PRELUDE + code + "\nprint(json.dumps(R))\n",
                            _default_repo_root(), td],
                           env=_child_env(env_over), cwd=cwd or td, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    last = (r.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        return json.loads(last[0])
    except ValueError:
        raise AssertionError("child failed rc=%s\nstdout=%s\nstderr=%s"
                             % (r.returncode, (r.stdout or "")[-_TAIL:], (r.stderr or "")[-_TAIL:]))


def _selftest(argv=()):
    import datetime
    import json
    import tempfile
    n = [0]
    fails = []
    verbose = "-v" in argv

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            fails.append(msg)
            print("FAIL [%d] %s" % (n[0], msg))
        elif verbose:
            print("ok   [%d] %s" % (n[0], msg))

    repo = _default_repo_root()
    H8 = datetime.timedelta(hours=8)

    # ── (A) 來源判斷真值表 ─────────────────────────────────────────────────────────
    ck(LIVE_LIKE == ("live", "synth") and ALL_SOURCES == ("demo", "snapshot", "live", "synth"),
       "(A0) LIVE_LIKE / ALL_SOURCES values")
    table = {"live": (True, False, True), "synth": (True, True, True), "demo": (False, False, True),
             "snapshot": (False, False, True), "LIVE": (False, False, False), "Synth": (False, False, False),
             " synth": (False, False, False), "": (False, False, False), None: (False, False, False),
             "synthetic": (False, False, False), b"live": (False, False, False), 1: (False, False, False),
             ("live",): (False, False, False)}
    for v, (ll, sy, kn) in table.items():
        ck((is_live_like(v), is_synth_source(v), is_known_source(v)) == (ll, sy, kn),
           "(A1) truth table %r -> live_like=%s synth=%s known=%s" % (v, ll, sy, kn))
    ck(is_live_like([]) is False and is_synth_source({}) is False, "(A2) unhashable inputs are not sources")

    # ── (A3) 落地帳來源隔離（Q-C）：非合成進程只拒合成帳；合成進程只收合成帳 ─────────────
    _absent = object()
    ledger_table = [("live", True, False), ("synth", False, True), (" Synth ", False, True),
                    ("SYNTH", False, True), ("synthetic", True, False), ("demo", True, False),
                    ("", True, False), (None, True, False), (_absent, True, False)]
    for v, keep_ns, keep_sy in ledger_table:
        doc = {} if v is _absent else {"source": v}
        ck(ledger_doc_admitted(doc, synth_process=False) is keep_ns
           and ledger_doc_admitted(doc, synth_process=True) is keep_sy
           and (v is _absent or ledger_source_admitted(v, synth_process=False) is keep_ns),
           "(A3) ledger source %r -> non-synth keep=%s, synth keep=%s"
           % ("<absent>" if v is _absent else v, keep_ns, keep_sy))
    ck(ledger_doc_admitted("x", synth_process=False) and ledger_doc_admitted(None, synth_process=True),
       "(A3b) non-dict docs pass through (parse/validate decide)")

    # ── (N) 正規化：對照表（注入旗標，進程內） ─────────────────────────────────────
    for v in _norm_inputs():
        ck(normalize_source(v, synth_process=True) == "synth", "(N1) synth proc: %r -> synth" % (v,))
        ck(source_key(v, synth_process=True) == "synth", "(N1b) synth proc key: %r -> synth" % (v,))
        ck(source_label_key(v, synth_process=True) == "source_label_synth", "(N1c) synth label %r" % (v,))
    expect_ns = {"demo": "demo", "snapshot": "snapshot", "live": "live", "synth": None}
    for v in _norm_inputs():
        got = normalize_source(v, synth_process=False)
        want = expect_ns.get(v, "demo") if isinstance(v, str) and not isinstance(v, _StrSub) else None
        if isinstance(v, _StrSub):
            want = None if str(v) == "synth" else (str(v) if str(v) in CURRENT_SOURCES else "demo")
        elif not isinstance(v, str):
            want = "demo"
        ck(got == want and (got is None or got in CURRENT_SOURCES),
           "(N2) non-synth proc: %r -> %r (want %r)" % (v, got, want))
        ck(normalize_source(v, synth_process=False) != "synth", "(N2b) non-synth never yields synth: %r" % (v,))
        ck(source_key(v, synth_process=False) == ("demo" if want is None else want),
           "(N2c) non-synth key %r" % (v,))
        lk = source_label_key(v, synth_process=False)
        ck(lk == "source_label_" + ("demo" if want is None else want), "(N2d) non-synth label %r -> %s" % (v, lk))

    # ── (E) 與 app 對外行為等價（子進程 import app；WP-4a 起 app 經本檔塌縮） ────────
    #   WP-4a 整合輪（Fable 2026-09-29 准許只改本檔自測）：舊 E 段以語法樹把 app 的常數與函式抽出來
    #   單獨 exec，app 改接本檔後必然 NameError ⇒ 改成子進程真的 import app、比對對外行為：
    #   非合成進程＝改動前的規則（`_current_rule`）逐輸入等價；合成進程＝一律 synth。
    lit = _app_literal_collapses(repo)
    ck(not lit, "(E0) no literal route-entry collapse left in app.py (all via core_source): %s" % lit)
    for synth in (False, True):
        tag = "synth" if synth else "non-synth"
        with tempfile.TemporaryDirectory(prefix="core_source_app_") as sd:
            over = {"BOARD_SOURCE": "synth" if synth else "demo", "BOARD_ENV_SENSOR": "0",
                    "BOARD_ENV_SENSOR_POLL": "0", "BOARD_JEV_SHADOW": "0", "BOARD_JEV_ROUTER": "off",
                    "BOARD_TUNNEL_AUTH": "0", "BOARD_STATE_DIR": os.path.join(sd, "state"),
                    "BOARD_ASSISTANT_DB": os.path.join(sd, "a.sqlite3")}
            if synth:
                over["BOARD_A11_LIVE"] = "1"
            code = ("ARG_STUB = %r\nARG_SYNTH = %r\n" % (not synth, synth)) + _APP_EQ_CHILD
            try:
                res = _run_child(code, over)
            except AssertionError as exc:
                ck(False, "(E1) %s child import app failed: %s" % (tag, str(exc)[-600:]))
                continue
        ck(not res["bad"] and res["n"] == len(_norm_inputs()),
           "(E1) %s: app._source_key/_route_source vs pre-change rule over %d inputs: %s"
           % (tag, res["n"], res["bad"][:5]))
        ck(tuple(res["sources"]) == CURRENT_SOURCES, "(E2) %s: app._SOURCES == CURRENT_SOURCES" % tag)
        ck(res["synth_proc"] is synth and res["default"] == ("synth" if synth else "demo"),
           "(E3) %s: app process flag / default source (%r, %r)" % (tag, res["synth_proc"], res["default"]))
        if not synth:
            ck(res["wsynth"] == [], "(E4) non-synth app import loads no wsynth_ module: %s" % res["wsynth"])
    print("  app equivalence: child-process import of app (non-synth + synth), %d inputs each"
          % len(_norm_inputs()))

    # ── (U) cmms URL 白名單（純函式） ─────────────────────────────────────────────
    sb = parse_sandbox_hosts("work.sandbox.example, [fd00::5]:81 Api.Example.Test:443")
    ck(sb == (("work.sandbox.example", None), ("fd00::5", 81), ("api.example.test", 443)),
       "(U0) parse_sandbox_hosts %r" % (sb,))
    for bad in ("http://x.example", "a/b", "u@x.example", "x.example?q", "x\\y", "[::1"):
        try:
            parse_sandbox_hosts(bad)
            ck(False, "(U0b) malformed sandbox entry accepted: %r" % bad)
        except ValueError:
            ck(True, "(U0b) malformed sandbox entry rejected: %r" % bad)
    url_cases = [
        ("", (), True), (None, (), True), ("   ", (), True),
        ("http://127.0.0.1", (), True), ("http://127.0.0.1:81/", (), True),
        ("http://127.255.255.254:9", (), True), ("https://LOCALHOST:81", (), True),
        ("http://localhost.", (), True), ("HTTP://LocalHost", (), True),
        ("http://[::1]:81", (), True), ("http://[::1]", (), True), ("http://[::ffff:127.0.0.1]:81", (), True),
        ("http://localhost.evil.example", (), False), ("http://127.0.0.1.evil.example", (), False),
        ("http://evil.example/127.0.0.1", (), False), ("http://127.0.0.1@evil.example", (), False),
        ("http://user:pw@127.0.0.1", (), False), ("http://0.0.0.0:81", (), False),
        ("http://127.1", (), False), ("http://localhost-evil.example", (), False),
        ("http://[::2]", (), False), ("http://loca1host", (), False),
        ("ftp://127.0.0.1", (), False), ("127.0.0.1:81", (), False), ("//127.0.0.1", (), False),
        ("http://127.0.0.1\\@evil.example", (), False), ("http://127.0.0.1/?x=1", (), False),
        ("http://127.0.0.1/#f", (), False), ("http://[::1", (), False),
        ("http://work.sandbox.example", (), False),
        ("http://work.sandbox.example", sb, True), ("https://WORK.Sandbox.Example./base", sb, True),
        ("http://api.example.test", sb, False), ("https://api.example.test", sb, True),
        ("https://api.example.test:444", sb, False), ("http://[fd00::5]:81", sb, True),
        ("http://[fd00::5]:82", sb, False), ("http://sandbox.example", sb, False),
        ("http://evil-work.sandbox.example", sb, False),
    ]
    for url, hosts, want in url_cases:
        got, detail = cmms_url_allowed(url, hosts)
        ck(got is want, "(U1) cmms_url_allowed(%r, allow=%d) -> %s (%s)" % (url, len(hosts), got, detail))

    # ── (P) 進程旗標：鎖定（子進程） ─────────────────────────────────────────────
    r = _run_child(r'''
import core_source as s
R["a"] = s.is_synth_process(); os.environ["BOARD_SOURCE"] = "live"; R["b"] = s.is_synth_process()
del os.environ["BOARD_SOURCE"]; R["c"] = s.is_synth_process(); R["info"] = s.process_flag_info()
s._reset_for_tests(); R["d"] = s.is_synth_process()
''', {"BOARD_SOURCE": "synth"})
    ck([r["a"], r["b"], r["c"], r["d"]] == [True, True, True, False],
       "(P1) synth locked; env flips ignored until reset: %r" % (r,))
    ck(r["info"] == {"synth": True, "env_raw": "synth"}, "(P1b) process_flag_info %r" % (r.get("info"),))
    r = _run_child(r'''
import core_source as s
R["a"] = s.is_synth_process(); os.environ["BOARD_SOURCE"] = "synth"; R["b"] = s.is_synth_process()
s._reset_for_tests(); R["c"] = s.is_synth_process()
''', {"BOARD_SOURCE": "live"})
    ck([r["a"], r["b"], r["c"]] == [False, False, True], "(P2) non-synth locked: %r" % (r,))
    r = _run_child(r'''
import core_source as s
R["unset"] = s.is_synth_process()
for raw in ["synth", "SYNTH", " synth ", "Synth", "live", "demo", "snapshot", "bogus", "", "synthetic", "synth2"]:
    os.environ["BOARD_SOURCE"] = raw; s._reset_for_tests(); R[raw] = s.is_synth_process()
''')
    want_env = {"unset": False, "synth": True, "SYNTH": True, " synth ": True, "Synth": True, "live": False,
                "demo": False, "snapshot": False, "bogus": False, "": False, "synthetic": False, "synth2": False}
    ck(r == want_env, "(P3) env value table %r" % (r,))

    # 正規化：真進程旗標下的對照
    norm_code = r'''
import core_source as s
vals = ["demo", "snapshot", "live", "synth", "", "LIVE", "SYNTH", "bogus", None]
R["norm"] = [s.normalize_source(v) for v in vals]
R["key"] = [s.source_key(v) for v in vals]
R["label"] = [s.source_label_key(v) for v in vals]
R["mods"] = sorted(m for m in sys.modules if m.startswith("wsynth_") or m == "core_paths")
'''
    r = _run_child(norm_code, {"BOARD_SOURCE": "synth"})
    ck(r["norm"] == ["synth"] * 9 and r["key"] == ["synth"] * 9 and r["label"] == ["source_label_synth"] * 9,
       "(P4) synth process: every value -> synth %r" % (r,))
    r = _run_child(norm_code, {"BOARD_SOURCE": "live"})
    ck(r["norm"] == ["demo", "snapshot", "live", None, "demo", "demo", "demo", "demo", "demo"],
       "(P5) live process normalize %r" % (r["norm"],))
    ck(r["key"] == ["demo", "snapshot", "live", "demo", "demo", "demo", "demo", "demo", "demo"],
       "(P5b) live process source_key %r" % (r["key"],))
    ck(r["label"][:4] == ["source_label_demo", "source_label_snapshot", "source_label_live", "source_label_demo"],
       "(P5c) live process labels %r" % (r["label"],))
    ck(r["mods"] == [], "(P5d) normalize/label in live process loads no wsynth_/core_paths: %r" % (r["mods"],))

    # ── (I) import 純度 ───────────────────────────────────────────────────────────
    imp_code = r'''
before = set(sys.modules)
import core_source
new = sorted(set(sys.modules) - before)
repo = os.path.normcase(os.path.abspath(sys.argv[1]))
R["repo_mods"] = [m for m in new if m != "core_source" and getattr(sys.modules.get(m), "__file__", None)
                  and os.path.normcase(os.path.abspath(sys.modules[m].__file__)).startswith(repo + os.sep)]
R["synth_mods"] = [m for m in sys.modules if m.startswith("wsynth_") or m == "core_paths"]
R["driver"] = "pyodbc" in sys.modules
'''
    for src_env in ("live", "synth", None):
        r = _run_child(imp_code, {"BOARD_SOURCE": src_env} if src_env else {})
        ck(r["repo_mods"] == [] and r["synth_mods"] == [] and r["driver"] is False,
           "(I1) import core_source (BOARD_SOURCE=%s) loads no repo module / no wsynth_ / no driver: %r"
           % (src_env, r))
    import ast
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
        src_text = f.read()
    tree = ast.parse(src_text)
    top_imports, fn_imports = set(), set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_imports.add((node.module or "").split(".")[0])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            fn_imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            fn_imports.add((node.module or "").split(".")[0])
    ck(top_imports == {"os", "sys", "threading", "types"}, "(I2) top-level imports stdlib only: %r" % (top_imports,))
    repo_named = sorted(m for m in fn_imports if os.path.exists(os.path.join(repo, m + ".py")))
    ck(repo_named == ["core_paths", "wsynth_clock"], "(I3) only lazy repo imports are wsynth_clock/core_paths: %r"
       % (repo_named,))

    # ── (T) 絆線（子進程＋暫存目錄） ─────────────────────────────────────────────
    trip_env = {"BOARD_SOURCE": "synth", "BOARD_CMMS_BASE_URL": "http://127.0.0.1:81"}

    # T1 空（不存在）目錄＋loopback：通過、建標記、替身生效、冪等
    r = _run_child(r'''
import core_source as s
sd = os.path.join(TD, "st"); os.environ["BOARD_STATE_DIR"] = sd
sm = s.startup_tripwire(utc_offset=datetime.timedelta(hours=8))
R["ok"] = sm["ok"]; R["marker"] = sm["marker"]; R["checks"] = {c["name"]: c["ok"] for c in sm["checks"]}
R["files"] = sorted(os.listdir(sd))
R["mk"] = json.load(open(os.path.join(sd, s.MARKER_FILE), encoding="utf-8"))["kind"]
import pyodbc
R["stub"] = s.is_driver_stub(pyodbc)
try:
    pyodbc.connect("x"); R["connect"] = "no-raise"
except s.SynthLeakError: R["connect"] = "SynthLeakError"
try:
    pyodbc.connect("x")
except pyodbc.Error: R["caught_as_driver_error"] = True
try:
    pyodbc.drivers(); R["other_attr"] = "no-raise"
except s.SynthLeakError: R["other_attr"] = "SynthLeakError"
def lazy():
    import pyodbc as p
    return p.connect("dsn")
try:
    lazy(); R["lazy"] = "no-raise"
except s.SynthLeakError: R["lazy"] = "SynthLeakError"
sys.modules["mes_selftest_dummy"] = types.ModuleType("mes_selftest_dummy")
R["idem"] = s.startup_tripwire() is sm and s.tripwire_summary() is sm
R["assert_marker"] = bool(s.assert_state_marker())
R["log"] = s.format_summary(sm)
R["frozen"] = sm["clock_frozen"]
''', trip_env)
    ck(r["ok"] is True and r["marker"] == "created" and all(r["checks"].values())
       and set(r["checks"]) == set(CHECK_NAMES), "(T1) empty dir + loopback passes: %r" % (r.get("checks"),))
    ck(r["files"] == [MARKER_FILE] and r["mk"] == MARKER_KIND, "(T1b) marker created, nothing else: %r" % (r["files"],))
    ck(r["stub"] is True and r["connect"] == "SynthLeakError", "(T1c) stub installed; connect raises SynthLeakError")
    ck(r.get("caught_as_driver_error") is True, "(T1d) `except driver.Error` catches it (honest degrade)")
    ck(r["other_attr"] == "SynthLeakError" and r["lazy"] == "SynthLeakError", "(T1e) other attrs / lazy import hit stub")
    ck(r["idem"] is True, "(T1f) second call returns cached summary (idempotent, even with MES module loaded later)")
    ck(r["assert_marker"] is True, "(T1g) assert_state_marker passes after creation")
    ck(len(r["log"]) == 1 + len(CHECK_NAMES) and "PASS" in r["log"][0], "(T1h) format_summary lines")
    ck(r["frozen"] is False, "(T1i) clock not frozen in this run")

    # T2 標記已在＋其他檔 ⇒ 通過（present）；T3 非空無標記 ⇒ 拒絕、不動任何檔
    for case, prep, want_ok, want_marker in (
            ("T2", "marker", True, "present"), ("T3", "nomarker", False, None),
            ("T3b", "badmarker", False, None), ("T3c", "file", False, None),
            ("T3d", "unset", False, None), ("T3e", "insiderepo", False, None),
            ("T3f", "gittree", False, None)):
        r = _run_child(r'''
import core_source as s
prep = %r
sd = os.path.join(TD, "st")
repo_root = None
if prep in ("marker", "nomarker", "badmarker"):
    os.makedirs(sd)
    open(os.path.join(sd, "other.json"), "w").write("{}")
if prep == "marker":
    json.dump({"kind": s.MARKER_KIND, "version": 1}, open(os.path.join(sd, s.MARKER_FILE), "w"))
if prep == "badmarker":
    open(os.path.join(sd, s.MARKER_FILE), "w").write("not json")
if prep == "file":
    open(sd, "w").write("x")
if prep == "insiderepo":
    repo_root = os.path.join(TD, "fakerepo"); os.makedirs(repo_root); sd = os.path.join(repo_root, "_state")
if prep == "gittree":
    g = os.path.join(TD, "wt"); os.makedirs(os.path.join(g, ".git")); sd = os.path.join(g, "sub", "state")
if prep != "unset":
    os.environ["BOARD_STATE_DIR"] = sd
before = sorted(os.listdir(sd)) if os.path.isdir(sd) else None
try:
    sm = s.startup_tripwire(repo_root=repo_root, utc_offset=datetime.timedelta(hours=8)); R["ok"] = sm["ok"]
    R["marker"] = sm["marker"]
except s.SynthStartupError as e:
    R["ok"] = False; R["marker"] = e.summary["marker"]
    R["failed"] = [c["name"] for c in e.summary["checks"] if not c["ok"]]
    R["msg"] = str(e)
R["after"] = sorted(os.listdir(sd)) if os.path.isdir(sd) else None
R["before"] = before
R["exists"] = os.path.exists(sd)
R["cached"] = s.tripwire_summary() is not None
''' % prep, trip_env)
        ck(r["ok"] is want_ok and r["marker"] == want_marker,
           "(%s) state dir case %s -> ok=%s marker=%s %r" % (case, prep, r["ok"], r["marker"], r.get("msg")))
        if not want_ok:
            ck(r.get("failed") == [CHECK_STATE_DIR], "(%s b) only state_dir failed: %r" % (case, r.get("failed")))
            ck(r["after"] == r["before"], "(%s c) refused run did not touch the dir" % case)
            ck(r["cached"] is False, "(%s d) refused run is not cached" % case)
            if prep in ("insiderepo", "gittree", "unset"):
                ck(r["exists"] is False, "(%s e) nothing was created" % case)
        else:
            ck(r["after"] == r["before"], "(%s b) present marker: dir untouched" % case)

    # T4 真驅動已載入／MES 模組先載入 ⇒ 拒絕（替身照裝）
    r = _run_child(r'''
import core_source as s
os.environ["BOARD_STATE_DIR"] = os.path.join(TD, "st")
res = {}
def run(tag):
    s._reset_for_tests(uninstall_stub=True)
    try:
        s.startup_tripwire(utc_offset=datetime.timedelta(hours=8)); res[tag] = ["passed"]
    except s.SynthStartupError as e:
        res[tag] = [c["name"] for c in e.summary["checks"] if not c["ok"]]
    res[tag + "_stub"] = s.is_driver_stub(sys.modules.get("pyodbc"))
sys.modules["pyodbc"] = types.ModuleType("pyodbc")
run("fake_driver")
del sys.modules["pyodbc"]
sys.modules["mes_selftest_dummy"] = types.ModuleType("mes_selftest_dummy")
run("mes_first")
del sys.modules["mes_selftest_dummy"]
real = None
s._reset_for_tests(uninstall_stub=True)
try:
    import importlib; real = importlib.import_module("pyodbc")
except ImportError:
    pass
if real is not None and not s.is_driver_stub(real):
    run("real_driver")
else:
    res["real_driver"] = "unavailable"
R.update(res)
R["created"] = os.path.exists(os.path.join(TD, "st"))
''', trip_env)
    ck(r["fake_driver"] == [CHECK_DB_DRIVER] and r["fake_driver_stub"] is True,
       "(T4) preloaded driver module -> refuse (db_driver), stub still installed: %r" % (r,))
    ck(r["mes_first"] == [CHECK_MES_IMPORT_ORDER] and r["mes_first_stub"] is True,
       "(T4b) MES module imported before tripwire -> refuse: %r" % (r.get("mes_first"),))
    if r["real_driver"] == "unavailable":
        print("  (T4c) real driver not installed here; simulated case (T4) covers it")
    else:
        ck(r["real_driver"] == [CHECK_DB_DRIVER], "(T4c) real imported driver -> refuse: %r" % (r["real_driver"],))
    ck(r["created"] is False, "(T4d) refused runs created no state dir")

    # T5 UTC 偏移
    r = _run_child(r'''
import core_source as s, wsynth_clock
os.environ["BOARD_STATE_DIR"] = os.path.join(TD, "st")
for tag, h in (("h0", 0), ("h9", 9), ("hm8", -8), ("h8", 8), ("host", None)):
    s._reset_for_tests()
    try:
        s.startup_tripwire(utc_offset=None if h is None else datetime.timedelta(hours=h)); R[tag] = []
    except s.SynthStartupError as e:
        R[tag] = [c["name"] for c in e.summary["checks"] if not c["ok"]]
R["host_off_h"] = wsynth_clock.local_utc_offset().total_seconds() / (60 * 60)
''', trip_env)
    ck(r["h0"] == [CHECK_UTC_OFFSET] and r["h9"] == [CHECK_UTC_OFFSET] and r["hm8"] == [CHECK_UTC_OFFSET],
       "(T5) offset != +8 refused: %r" % (r,))
    ck(r["h8"] == [], "(T5b) +8 passes")
    ck(r["host"] == ([] if r["host_off_h"] == 8 else [CHECK_UTC_OFFSET]),
       "(T5c) real host offset (%sh) handled consistently: %r" % (r["host_off_h"], r["host"]))

    # T6 cmms URL 經 env 串接（白名單 env、格式錯誤、未設）
    r = _run_child(r'''
import core_source as s
os.environ["BOARD_STATE_DIR"] = os.path.join(TD, "st")
cases = [("unset", None, None), ("loop", "http://[::1]:81", None), ("lookalike", "http://127.0.0.1.evil.example", None),
         ("sandbox_no_list", "https://work.sandbox.example", None),
         ("sandbox_listed", "https://work.sandbox.example", "work.sandbox.example"),
         ("sandbox_wrong_port", "https://work.sandbox.example:81", "work.sandbox.example:443"),
         ("bad_list", "http://127.0.0.1:81", "http://work.sandbox.example")]
for tag, url, hosts in cases:
    for k, v in (("BOARD_CMMS_BASE_URL", url), ("BOARD_SYNTH_CMMS_HOSTS", hosts)):
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v
    s._reset_for_tests()
    try:
        sm = s.startup_tripwire(utc_offset=datetime.timedelta(hours=8))
        R[tag] = ["pass", [c["detail"] for c in sm["checks"] if c["name"] == "cmms_base_url"][0]]
    except s.SynthStartupError as e:
        R[tag] = [c["name"] for c in e.summary["checks"] if not c["ok"]]
''', {"BOARD_SOURCE": "synth"})
    ck(r["unset"][0] == "pass" and r["unset"][1].startswith("unset"), "(T6) unset URL passes as disabled: %r" % (r["unset"],))
    ck(r["loop"][0] == "pass", "(T6b) IPv6 loopback passes")
    ck(r["lookalike"] == [CHECK_CMMS_BASE_URL], "(T6c) look-alike loopback refused")
    ck(r["sandbox_no_list"] == [CHECK_CMMS_BASE_URL], "(T6d) sandbox host with empty allowlist refused")
    ck(r["sandbox_listed"][0] == "pass", "(T6e) sandbox host listed in env passes")
    ck(r["sandbox_wrong_port"] == [CHECK_CMMS_BASE_URL], "(T6f) sandbox host wrong port refused")
    ck(r["bad_list"] == [CHECK_CMMS_BASE_URL], "(T6g) malformed allowlist env refused")

    # T7 多條同時失敗 ⇒ 摘要逐條列出、例外訊息含全部
    r = _run_child(r'''
import core_source as s
try:
    s.startup_tripwire(utc_offset=datetime.timedelta(hours=0)); R["failed"] = []
except s.SynthStartupError as e:
    R["failed"] = [c["name"] for c in e.summary["checks"] if not c["ok"]]; R["msg"] = str(e)
    R["n"] = len(e.summary["checks"]); R["log"] = s.format_summary(e.summary)
''', {"BOARD_SOURCE": "synth", "BOARD_CMMS_BASE_URL": "http://evil.example"})
    ck(r["failed"] == [CHECK_UTC_OFFSET, CHECK_CMMS_BASE_URL, CHECK_STATE_DIR] and r["n"] == len(CHECK_NAMES),
       "(T7) all failures reported together: %r" % (r.get("failed"),))
    ck(all(x in r["msg"] for x in r["failed"]) and "REFUSED" in r["log"][0], "(T7b) message/log list every failure")

    # ── (Q) 非合成進程呼叫絆線：什麼都不做 ───────────────────────────────────────
    nonsynth_code = r'''
import core_source as s
sd = os.path.join(TD, "st"); os.makedirs(sd)
os.environ["BOARD_STATE_DIR"] = sd
os.environ["BOARD_CMMS_BASE_URL"] = "http://evil.example"
EV = []
ARM = [False]
WATCH = ("open", "os.listdir", "os.scandir", "os.mkdir", "os.rename", "os.remove", "os.rmdir", "import")
def hook(ev, args):
    if ARM[0] and ev in WATCH:
        EV.append(ev)
sys.addaudithook(hook)
before = set(sys.modules)
ARM[0] = True
sm = s.startup_tripwire(utc_offset=datetime.timedelta(hours=0))
am = s.assert_state_marker()
ARM[0] = False
R["sm"] = sm; R["am"] = am; R["events"] = EV
R["new_mods"] = sorted(set(sys.modules) - before)
R["synth_mods"] = sorted(m for m in sys.modules if m.startswith("wsynth_") or m == "core_paths")
R["driver_in_modules"] = "pyodbc" in sys.modules
R["dir"] = os.listdir(sd)
R["cached"] = s.tripwire_summary()
'''
    for src_env in ("live", "demo", None):
        r = _run_child(nonsynth_code, {"BOARD_SOURCE": src_env} if src_env else {})
        tag = "(Q %s)" % (src_env or "unset")
        ck(r["sm"] == {"synth": False, "ok": True, "checks": []}, "%s tripwire returns non-synth summary" % tag)
        ck(r["am"] is None, "%s assert_state_marker is a no-op" % tag)
        ck(r["events"] == [], "%s no file/dir/import audit events during the call: %r" % (tag, r["events"]))
        ck(r["new_mods"] == [] and r["synth_mods"] == [], "%s no module loaded (no wsynth_/core_paths): %r"
           % (tag, r["new_mods"] + r["synth_mods"]))
        ck(r["driver_in_modules"] is False, "%s no driver stub installed" % tag)
        ck(r["dir"] == [] and r["cached"] is None, "%s no marker written, nothing cached" % tag)

    # ── (L) 原始碼字面值掃描 ───────────────────────────────────────────────────────
    import io
    import re
    import tokenize
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts.append((node.lineno, node.value))
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            ck(abs(node.value) < _ID_LIMIT, "(L1) no id-like numeric literal (line %d: %r)" % (node.lineno, node.value))
    for tok in tokenize.generate_tokens(io.StringIO(src_text).readline):
        if tok.type == tokenize.COMMENT:
            texts.append((tok.start[0], tok.string))
    date_re = re.compile(r"\d{4}-\d{2}-\d{2}")
    digit_re = re.compile(r"\d{4,}")
    tld_re = re.compile(r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
                        r"(com|net|org|io|app|cloud|dev|local|lan|corp|internal|intra|tw|cn|au|us|uk|co|biz|info)\b")
    host_style_re = re.compile(r"\b[a-z]{2,}\d+-[a-z][a-z0-9-]*\b")      # 內網主機名常見形（小寫）
    domain_acct_re = re.compile(r"\b[A-Z]{3,}\\[a-z]")
    ipv4_re = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
    for ln, t in texts:
        for m in digit_re.finditer(date_re.sub("", t)):
            ck(False, "(L2) digit run (id-like) at line %d: %r" % (ln, m.group(0)))
        for m in tld_re.finditer(t):
            host = m.group(0).lower()
            ck(host == "example.com" or host.endswith(".example.com"),"(L3) hostname literal at line %d: %r" % (ln, host))
        for m in host_style_re.finditer(t):
            ck(False, "(L4) host-style token at line %d: %r" % (ln, m.group(0)))
        for m in domain_acct_re.finditer(t):
            ck(False, "(L5) domain account at line %d: %r" % (ln, m.group(0)))
        for m in ipv4_re.finditer(t):
            ck(m.group(1) in ("127", "0"), "(L6) non-loopback IPv4 literal at line %d: %r" % (ln, m.group(0)))
    # 站名：由子進程 import 名冊常數（驅動換替身），逐一比對本檔原始碼（大小寫敏感、整詞）
    r = _run_child(r'''
stub = types.ModuleType("pyodbc")
def _no(*a, **k): raise RuntimeError("no db in selftest")
stub.connect = _no; stub.Error = RuntimeError; stub.OperationalError = RuntimeError
sys.modules["pyodbc"] = stub
out = set()
def walk(v, depth=0):
    if depth > 6: return
    if isinstance(v, str):
        if 2 <= len(v) <= 40 and any(c.isalpha() for c in v): out.add(v)
    elif isinstance(v, dict):
        for k, x in v.items(): walk(k, depth + 1); walk(x, depth + 1)
    elif isinstance(v, (list, tuple, set, frozenset)):
        for x in v: walk(x, depth + 1)
try:
    import mes_timeline as mt
    for name in ("EQP_STATIONS", "CAL_CLASS"):
        walk(getattr(mt, name, None))
    R["names"] = sorted(out)
except Exception as e:
    R["err"] = "%s: %s" % (type(e).__name__, e)
''')
    ck("err" not in r and len(r.get("names", [])) > 0, "(L7) station roster loaded for scan (%d names; %s)"
       % (len(r.get("names", [])), r.get("err", "")))
    hits = [nm for nm in r.get("names", [])
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(nm) + r"(?![A-Za-z0-9_])", src_text)]
    ck(not hits, "(L7b) no station-roster name appears in core_source.py: %d hit(s)" % len(hits))

    if fails:
        print("core_source selftest FAIL (%d/%d)" % (len(fails), n[0]))
        return 1
    print("core_source selftest PASS (%d checks)" % n[0])
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(_selftest(sys.argv[1:]))
    print(__doc__)
