# -*- coding: utf-8 -*-
"""wsynth_wiring_selftest.py — 合成產線 WP-4a（app 接線第一包）自測
=====================================================================

釘什麼
------
(E) **等價性（非合成進程）**：開工時的原始碼（`--base`，預設 `66e5d97`＝WP-4a 開工 HEAD）以
    `git archive` 解到系統暫存目錄，與目前的 worktree **各起一個子進程**（非合成、demo 預設源），
    對清冊 §5 本包改過的每一種判斷逐輸入取值，兩棵樹的真值表逐項相等；另對「只能以字面比對」的
    行內判斷（`source == "live"` 等），以改動前的字面運算式當基準、逐輸入比對新 helper。
(P) **頁面位元組（非合成進程、demo）**：兩棵樹各起一個子進程、同一個凍結時鐘，以 in-process
    TestClient 請求導覽列上每一頁與它們的 body 端點（en／zh／`?ui=v1`，以及 `source=synth`、
    `source=bogus` 兩種未知源），回應碼與 md5 逐一相等；且非合成子進程沒有載入任何 `wsynth_` 模組。
    時間處理：子進程在 import 任何 app 模組**之前**把 `datetime.datetime` 換成 `now()`／`today()`／
    `utcnow()` 回同一個錨的子類別（錨＝父進程取一次真的現在、兩棵樹共用；`astimezone` 真換算，
    同 run_smoke `_STATION_MD5` 的凍結手法）；`date.today()` 不換（換了會破 `isinstance(x, date)`），
    所以錨取自真的現在＝同一天。兩棵樹各自的狀態目錄都改向到暫存目錄（`BOARD_STATE_DIR`）。
(S) **合成進程**：子進程設 `BOARD_SOURCE=synth`＋狀態目錄改向到系統暫存目錄，經
    `wsynth_launch` 的 `boot()`／`import_app()`（＝`--check` 路徑）啟動；絆線全過；第一期各頁 200
    （或明確降級），第一期外的路由 404；回應不含例外文字、沒有 5xx；請求期間沒有開任何 fixture／
    私機設定檔／真平面圖；沒有寫 repo 內任何路徑；沒有起外部進程、沒有 iMonnit／Jev 執行緒；
    私機功能函式全部回「關」；MCP 的 MES 工具族與知識卡回「未提供」且帶合成揭露；
    `core_paths.IMPORT_TIME_SITES` 每一處都落在改向後的根下；v1 介面關閉；月快照輪替。
(N) **非合成進程拒用合成日帳**（Q-C）：狀態目錄裡同時有 `source=live` 與 `source=synth` 的日帳，
    app 的讀口、`/equipment` 速率讀端、月帳文件都只看得到 live 那份；`should_persist("synth")`＝False。
(L) **啟動器**：`--check` 通過；缺環境變數、來源不是 synth、`BOARD_A11_LIVE=0`、非 loopback 的
    cmms URL 各自拒絕啟動（非零結束）。
(R) **repo 未被寫入**：全程前後對 repo 內所有檔案（排除 `.git`／`.claude`）的大小＋mtime 做快照比對。
(T) **WP-6 tick 接線**（`wsynth_tick`；2026-10-01）：合成子進程（凍結時鐘、狀態目錄改向）跑一輪
    calendar＋lastprod＋freeze，再以同一進程的 app 讀：tick 寫的日曆／last_production 狀態檔路徑＝app 讀的
    路徑（`board_data.PROD_CALENDAR_PATH`、`app.EQM_LP_STATE`）；app 的 last_production 讀口收下 tick 的狀態檔；
    日曆必須驗得過並落檔、`load_prod_calendar()` 回 ok（WP-6b g3 起；g2 的「段恰好結束在 08:00:00 生產日界」
    缺口已在產生器修掉，驗不過＝回歸、直接紅）；
    月帳凍結走 app 單一出處、不出錯；`wsynth_tick.WARM_PATHS` 每條都在第一期白名單內且回 200；寫入全在狀態目錄。
(I…／S27–S31／A2) **WP-4b A 包（來源標示、導覽列、死連結；2026-09-30 R-A1～R-A10）**，全部在 (I) 的整合子進程
    （合成進程＋WP-3 資料層＋loopback 假 cmms、凍結時鐘）裡以真路由渲染、看 HTML：
    (S27) 第一期每一個整頁 en／zh 都有合成徽章（`.board-src.synth`＋`source_label_synth` 譯文）；有來源牌的
          三處（首頁外殼、機況 body、設備健康 body）印合成來源牌字樣。
    (S28) 第一期每一頁（整頁＋片段＋測試注入平面圖後的首頁 body）en／zh 對整字 `LIVE`、`REAL`、`real`
          與「真值」零命中（C-SY11；樣式見 `WP4B_FORBIDDEN`）。
    (S29) 上述頁面抽出的所有站內 `href`／`hx-get`／`hx-post` 逐一請求都不是 404、不是 5xx，且沒有一個
          指向第一期外的路徑（`app._synth_path_allowed`）。
    (S31) 首頁調度窗（測試注入平面圖，因為合成進程不讀真平面圖、首頁主體平常是降級卡）不印「暖機中」
          句、印「合成產線未提供」句。
    (A2-1…6) R-A2 六處「字面 `"live"` 在合成下為假而行為正確」的行為負斷言（不加分支、只釘結果）。
    `/updown`、`/updown/body` 移出第一期（R-A1）⇒ 列入 (S4) 的 404 組。
    首頁平面圖：SL2-A（2026-10-02）起合成進程有自己的示意平面圖（`wsynth_layout`），本自測不再注入版面
    （原「測試注入格子」退役；上列「注入平面圖」的各項現在量的是自然渲染的合成平面圖）。
(H1–H9) **SL2-A 合成平面圖**（SPEC-SYNTHLINE-PHASE2 §2.1；獨立子進程、凍結時鐘、loopback 假 cmms）：首頁 `/`＋
    `/landing2/body`（en／zh × Laptop／Projector）渲染平面圖本體（每台名冊機一個方塊、無錯誤卡、無 Draft chip）、
    四窗在、沒有資料腿的窗印「合成產線未提供」；點機台開右欄機況卡；`/landing/state` 與首頁同機同桶；方塊色＝桶色；
    渲染後幾何不重疊、四區各一標且不壓機台；調度距離讀口（`wd.load_layout_doc`／`layout_doc_stamp`）與首頁同一份
    記憶體文件；開機到渲染期間零真平面圖／線稿／DXF／產生器讀取（open 攔截）。(H6b／H6c) 從合成世界**挑**一則
    「停機→修復→恢復生產」事件（只挑不造），兩個凍結時鐘子進程各看一次：停機中＝紅（down）、恢復後＝綠（run）。
    (H10／H11) **SL2-A 視覺修整（2026-10-02）**：合成人力未提供期間首頁零綑框、零人數 chip、零「未畫」句
    （§2.5-8）；版面 `sl2` 的右上角縮放控制區（鏡頭寬 0.20 × 高 0.09）內沒有任何**渲染後**方塊（用首頁真正送出的鏡頭）。
(S30／S30b／S32–S35) **WP-4b B 包（2026-09-30 R-B1～R-B6）**：
    (S30) 合成 `/station/{cls}/units`（CAL＝Gen2 取代父機、CLN＝保留父機的 Gen2 母機）渲染機台表、列＝時間軸名冊
          ＋Gen2 展開（預期由本檔獨立寫法算出）、產出／良品／品質／OEE 全「—」、妥善率與稼動有值、來源句在。
    (S30b) 兩棵樹（非合成）對 `_demo_expand_fleet`／`_expand_gen2_yield_live`（fetch／build 以假函式替代、
          含例外與空列兩條退路）逐位元等價＝共用函式 `gen2_unit_split` 抽出不改既有輸出。
    (S32) MCP：每則回覆帶合成揭露；`explain_metric` 附 `synthetic_line_note`（＝`board_metrics.SYNTH_NOTE`）；
          回答來源的部分（揭露、退階句、provenance）對 C-SY11 禁字零命中。非合成不加鍵（E2 釘）。
    (S33) 設備健康頁：合成世界起日晚於 Δ 前期窗起點 ⇒ 掛「展示世界不足 180 天」chip；把起日假設成夠早 ⇒ 不掛。
    (S34) 合成匯出（/equipment、/equipment/health 兩節）meta 帶合成浮水印、檔名 `SYNTH_` 前綴；非合成匯出
          已併入 (P) 逐位元比對（內容＋content-disposition）。
    (S35) 首頁本體（注入平面圖＋選機）：調度暖機句、月報暖機句、作業員快取暖機 tooltip、調度窗標題 tooltip
          的夜間／週末暖機句在合成下全不出現；`/landing/state` 同。
(S36–S39) **WP-4b C 包（2026-09-30 R-C1～R-C6）：合成輸出面不得含從工廠資料量出來的數字或日期**：
    (S36) 守門員：禁值集合**只在整合子進程記憶體內**由 `board_tags.PRODUCT_TAGS`（families／lines 段數取四位數
          以上，含／不含千分位兩種寫法；`first_seen`／`last_seen`）與 `board_dormant.DORMANT_BRIDGE` 的日期導出，
          對第一期每一頁、片段、匯出、JSON 端點（en／zh；每個合成站別三頁、每台合成機 /machine 頁）與 MCP 回應全文
          比對，零命中；日期巧合命中只准出現在 `RC5_KNOWN_COINCIDENCES` 逐條列出的前後文。輸出只印頁面、命中數與
          遮罩後前後文，**不印禁值**。chip 仍在、且沒有任何 tooltip 帶普查段（S36d）。
    (S37) 覆蓋率註記（`cln_cov_note`，真實普查的模組級打標覆蓋率）在合成下不渲染。
    (S38) 長停橋常數換成「被讀就記一筆」的絆線（import app 之前裝）：一般請求與**強制 A11 取回失敗**後重畫
          相關頁面／MCP，讀取次數皆 0；頁上沒有橋的原因、出處與「Board 橋常數」來源句；來源句改說合成源不可達。
    (S39) 成本預估的普查讀腿、PM 排程板、人力資料讀腿在合成第一期零呼叫。
(WP-7，2026-10-02) **整合子進程跑兩次**：日曆缺席（既有狀態）與「渲染前先跑 tick calendar」（I10；上面
    S27–S40 全部在日曆已寫的狀態下再判一次，訊息前綴 `[cal] `）。(I3) 的 demo-only 交集改成對**所有**合成渲染
    （兩個整合子進程的每一個回應＋R-C5 掃到的 MCP 回應＋synth_round 三個子進程）取 EID 聯集；R-C5 ② 另加機台頁
    的 `page=availability` 與 htmx 片段身分。
(G0–G8) **synth_round**（SPEC-SYNTHLINE WP-7；放本檔的理由見該節註解）：絆線、確定性（兩進程逐位元）、前綴穩定
    （晚一天的世界重送固定過去窗）、大牌＋橫幅＋禁字（兩種日曆狀態）、**G1 跨頁同尺**（兩種日曆狀態；未達成的
    指標必須逐項落在 `G1_KNOWN_UNMET` 且原因證據在場）、R44 在合成座級工單上成立、著陸頁真拆分 N/A、日曆真的換尺。

紀律
----
* 不連 MES（每個子進程的資料庫驅動都是替身）、不打任何網路端點、**任何請求都不帶 `source=live`**。
* 測試用環境變數只在子進程內設；寫檔只寫系統暫存目錄。
* WP-3（資料層轉接）尚未併入：合成進程裡 app 直接呼叫的 live 取數函式會撞到驅動替身而被既有
  降級接住——本自測**計數並列出**這些呼叫點（WP-3 的工作清單），不當成失敗；它釘的是「app 沒有
  走錯路」（沒有塌成 demo、沒有讀真資料、沒有寫 repo）。

用法：`python wsynth_wiring_selftest.py [--base <commit>] [-v] [--skip-pages]`
      開發捷徑（完整驗收不得用）：`--only-integ [--calendar]`、`--only-synth-round`
"""
import argparse
import datetime
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time

REPO = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE = "c4b6cdf"   # SL2-A 視覺修整開工時的主線 HEAD（2026-10-02）＝本包改動的前一個 commit；
#                            SL2-A＝185e1af；
#                            WP-7＝0025fa5、小修包＝4e6854a、WP-4b C 包＝a4bbd06、B 包＝22a6869、A 包＝cabb50d、WP-4a 整合輪＝dfdb559、第一輪＝66e5d97
#                            ⚠ 基準比對要在**乾淨 worktree** 跑（主線有 `_state/` 等未追蹤狀態，必假紅）。

#: (P) **明確列名的預期差異**（只列這些網址族；其餘一律逐位元同）。鍵＝路徑（完全相等，不含子路徑）；
#: 值＝(原因, 每個回應應少掉的位元組數, 不受影響的查詢片段)。基準若已越過該改動，該族照樣須逐位元同
#: （判準＝「相同」或「狀態碼同且恰少掉該位元組數」），基準移過之後把該列刪掉。
#: WP-7（2026-10-02）：基準移到 0025fa5、已越過小修包項目三（`/equipment` v2 外殼 295 位元組註解）⇒ 該列刪除；
#:   本表現為空（機制保留：日後真有核准的非合成位元差異照格式逐條加）。
_EXPECTED_PAGE_DIFF = {}


def _expected_page_diff(url, base_rec, new_rec):
    """`url` 屬預期差異族且差異恰為所列位元組數（或完全相同）⇒ True；不在清單 ⇒ False。"""
    path, _, query = url.partition("?")
    spec = _EXPECTED_PAGE_DIFF.get(path)
    if not spec or spec[2] in query:
        return False
    if base_rec[:2] == new_rec[:2]:
        return True
    return base_rec[0] == new_rec[0] and base_rec[2] - new_rec[2] == spec[1]
CAL = "segCalibrationTest"
MARK = "@@WP4A@@"
_TAIL = 4000

# ─────────────────────────────────────────────────────────────────────────────
# 共用：子進程
# ─────────────────────────────────────────────────────────────────────────────
_POISON = {  # 同 run_smoke 在 import app 之前設的毒丸（非合成子進程用）
    "BOARD_ASSISTANT_UI": "1", "BOARD_TUNNEL_AUTH": "0", "BOARD_OPEN_ACCESS": "0",
    "LLM_CLASS_ENABLED": "0", "BOARD_ENV_SENSOR_POLL": "0", "BOARD_ENV_SENSOR": "0",
    "BOARD_JEV_SHADOW": "0", "BOARD_JEV_ROUTER": "off", "BOARD_NO_AUTOFREEZE": "1",
}


def _base_env(over=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("BOARD_") and not k.startswith("LLM_") and not k.startswith("IMONNIT_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(over or {})
    return env


_PRELUDE = r'''
import sys, os, io, json, types, builtins, threading, subprocess, traceback, time, datetime as _d
TREE = sys.argv[1]
TD = sys.argv[2]
ARG = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
sys.path.insert(0, TREE)
R = {}
def _stub_pyodbc():
    class _NoMES(Exception):
        pass
    m = types.ModuleType("pyodbc")
    def _no(*a, **k):
        raise _NoMES("selftest: pyodbc stubbed (zero MES)")
    m.connect = _no
    for n in ("Error", "OperationalError", "InterfaceError", "ProgrammingError", "DatabaseError"):
        setattr(m, n, _NoMES)
    sys.modules["pyodbc"] = m
def _freeze_clock(iso):
    anchor = _d.datetime.fromisoformat(iso)
    class FrozenDT(_d.datetime):
        @classmethod
        def now(cls, tz=None):
            a = cls(anchor.year, anchor.month, anchor.day, anchor.hour, anchor.minute, anchor.second)
            return a if tz is None else a.astimezone(tz)
        @classmethod
        def today(cls):
            return cls.now()
        @classmethod
        def utcnow(cls):
            return cls.now().astimezone(_d.timezone.utc).replace(tzinfo=None)
    _d.datetime = FrozenDT
def _jsonable(x):
    try:
        json.dumps(x)
        return x
    except Exception:
        return repr(x)
'''

_EPILOGUE = r'''
print("@@WP4A@@" + json.dumps(R, default=repr))
'''


def _run_child(code, tree, env, arg=None, timeout=1200, cwd=None):
    with tempfile.TemporaryDirectory(prefix="wp4a_child_") as td:
        # WP-7：子進程碼改寫進暫存檔再跑（原本 `-c`）——整合子進程加上 WP-7 的段落後超過 Windows 命令列上限
        #   （WinError 206）。檔案放在子進程自己的暫存目錄（＝cwd＝`-c` 時的 sys.path[0]），argv[1:] 不變。
        src = os.path.join(td, "_wp4a_child.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write(_PRELUDE + code + _EPILOGUE)
        r = subprocess.run([sys.executable, src, tree, td,
                            json.dumps(arg or {})],
                           env=env, cwd=cwd or td, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    for line in reversed((r.stdout or "").splitlines()):
        if line.startswith(MARK):
            return json.loads(line[len(MARK):]), r
    raise AssertionError("child failed rc=%s\nstdout=%s\nstderr=%s"
                         % (r.returncode, (r.stdout or "")[-_TAIL:], (r.stderr or "")[-_TAIL:]))


def _extract_base(commit, dest):
    data = subprocess.run(["git", "-C", REPO, "archive", "--format=tar", commit],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


def _repo_fingerprint():
    out = {}
    for dp, dns, fns in os.walk(REPO):
        dns[:] = [d for d in dns if d not in (".git", ".claude")]
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, REPO)] = (st.st_size, st.st_mtime_ns)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# (E) 真值表：兩棵樹同一段程式
# ─────────────────────────────────────────────────────────────────────────────
_INPUTS = ["demo", "snapshot", "live", "synth", "", None, "LIVE", "Live", " live", "live ", "SYNTH",
           "Synth", " synth", "synthetic", "bogus", "sim", 0, 1, True, ("live",), b"live"]

_TRUTH_CHILD = r'''
_stub_pyodbc()
os.chdir(TREE)
ENV0 = dict(os.environ)
import app as A
import board_attrib as WA
import board_export as WX
import board_mcp as WM
INP = ARG["inputs"]
def J(v):
    return [v, type(v).__name__]
def run(key, fn, *args):
    try:
        R[key] = _jsonable(fn(*args))
    except Exception as e:
        R[key] = "EXC:%s" % type(e).__name__
def withenv(over, fn):
    saved = {k: os.environ.get(k) for k in over}
    try:
        for k, v in over.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
inputs = [tuple(x) if isinstance(x, list) else x for x in INP]
inputs = [b"live" if x == "__bytes__" else x for x in inputs]
# 1. 快取鍵塌縮
for i, v in enumerate(inputs):
    run("source_key[%d]" % i, A._source_key, v)
# 2. 落地閘
for i, v in enumerate(inputs):
    run("should_persist[%d]" % i, WA.should_persist, v)
# 3. MCP 來源（env）
for i, v in enumerate(inputs):
    if isinstance(v, str):
        run("mcp_source[%d]" % i, lambda v=v: withenv({"BOARD_SOURCE": v}, WM._source))
run("mcp_source[unset]", lambda: withenv({"BOARD_SOURCE": None}, WM._source))
# 4. export page_ctx／_norm_params
for i, v in enumerate(inputs):
    if isinstance(v, str):
        raw = json.dumps({"path": "/equipment", "params": {"source": v}})
        run("page_ctx_source[%d]" % i, WX.page_ctx_source, raw)
        run("norm_params[%d]" % i, WX._norm_params, {"source": [v]})
# 5. UI 版本解析
class _Req:
    def __init__(self, qp, ck):
        self.query_params = qp
        self.cookies = ck
for q in (None, "v1", "15", "2", "bogus"):
    for c in (None, "v1", "15", "2"):
        run("ui_resolve[%s|%s]" % (q, c), A._ui_resolve,
            _Req({} if q is None else {"ui": q}, {} if c is None else {"board_ui": c}))
# 6. 私機功能開關（env 變化）
for v in (None, "0", "1"):
    run("assistant_ui[%s]" % v, lambda v=v: withenv({"BOARD_ASSISTANT_UI": v}, A._assistant_ui_enabled))
for u, s in ((None, None), ("http://127.0.0.1:9", None), ("http://127.0.0.1:9", "x")):
    run("hermes_cfg[%s|%s]" % (u, s), lambda u=u, s=s: withenv(
        {"BOARD_HERMES_URL": u, "BOARD_HERMES_SECRET": s}, A._hermes_configured))
for v in (None, "on", "off", "0"):
    run("jev_router[%s]" % v, lambda v=v: withenv({"BOARD_JEV_ROUTER": v}, A._jev_router_on))
for v in (None, "0", "1"):
    run("jev_shadow[%s]" % v, lambda v=v: withenv({"BOARD_JEV_SHADOW": v}, A._jev_shadow_off_reason))
for k, s, en in ((None, None, None), ("a", "b", None), ("a", "b", "0"), ("a", None, None)):
    run("env_enabled[%s|%s|%s]" % (k, s, en), lambda k=k, s=s, en=en: withenv(
        {"IMONNIT_API_KEY_ID": k, "IMONNIT_API_SECRET": s, "BOARD_ENV_SENSOR": en}, A._env_enabled))
run("dx_run_live[unset]", lambda: withenv({"BOARD_Diag_SCRIPT": None}, lambda: A._dx_run_live("EID-00001", 3)))
run("dx_confirmed[empty]", lambda: A._dx_confirmed_live({}, "EID-00001"))
# 7. 工單深連結閘（mode／source；cmms 設定有／無）
for cfg in ("off", "on"):
    over = ({"BOARD_CMMS_BASE_URL": None, "BOARD_CMMS_TOKEN": None} if cfg == "off"
            else {"BOARD_CMMS_BASE_URL": "http://127.0.0.1:9", "BOARD_CMMS_TOKEN": "x"})
    for i, v in enumerate(inputs):
        if not isinstance(v, (str, type(None))):
            continue
        run("eqm_wo_base[%s|%d]" % (cfg, i), lambda v=v, over=over: withenv(over, lambda: A._eqm_cmms_wo_base({"mode": v})))
        run("maint_wo_base[%s|%d]" % (cfg, i), lambda v=v, over=over: withenv(over, lambda: A._maint_cmms_wo_base({"mode": v})))
        run("ud_wo_base[%s|%d]" % (cfg, i), lambda v=v, over=over: withenv(over, lambda: A._ud_cmms_wo_base(v)))
    run("eqm_wo_base[%s|none]" % cfg, lambda over=over: withenv(over, lambda: A._eqm_cmms_wo_base(None)))
# 8. 非 live 早退（零 I/O 分支）
for i, v in enumerate(inputs):
    if not isinstance(v, (str, type(None))) or v == "live":
        continue
    run("window_wos[%d]" % i, A.load_window_wos, v, 7)
    run("util_wos[%d]" % i, A.load_util_wos, v, 4, "", "", None, None)
    run("fleet_by_cls[%d]" % i, A.load_fleet_by_cls, v, 7, 15)
    run("mr_freeze_due[%d]" % i, A._mr_freeze_due, v)
    run("autorebuild_due[%d]" % i, lambda v=v: A._ledger_autorebuild_due(v, sync=True))
    run("lastgood_save[%d]" % i, A._ld_lastgood_save, "period", v, {"k": 1})
    run("lastgood_load[%d]" % i, lambda v=v: A._ld_lastgood_load("period", v) is not None)
    run("peek_eqm_none[%d]" % i, lambda v=v: A._peek_eqm(v, 7, 15) is None if v != "demo" else "demo")
    run("station_rate[%d]" % i, lambda v=v: A._station_rate(v, "segCalibrationTest", None))
R["_n_keys"] = len(R)
'''

_PURE_CHILD = r'''
_stub_pyodbc()
os.chdir(TREE)
import app as A
import board_mcp as WM
INP = [b"live" if x == "__bytes__" else (tuple(x) if isinstance(x, list) else x) for x in ARG["inputs"]]
INP = INP + [[], {}]
bad = []
n = 0
def ck(cond, msg):
    global n
    n += 1
    if not cond:
        bad.append(msg)
for v in INP:
    try:
        old_eq = (v == "live")
    except Exception:
        old_eq = "EXC"
    ck(A._is_live(v) == old_eq, "_is_live(%r) vs == 'live'" % (v,))
    ck((not A._is_live(v)) == (v != "live"), "not _is_live(%r) vs != 'live'" % (v,))
    try:
        old_in = v in ("live", "snapshot")
    except TypeError:
        old_in = "EXC"
    if old_in != "EXC":
        ck((A._is_live(v) or v == "snapshot") == old_in, "live|snapshot(%r)" % (v,))
        ck((not (A._is_live(v) or v == "snapshot")) == (v not in ("live", "snapshot")), "not live|snapshot(%r)" % (v,))
        old_route = v if v in ("demo", "live", "snapshot") else "demo"
        ck(A._route_source(v) == old_route, "_route_source(%r)=%r want %r" % (v, A._route_source(v), old_route))
        ck(A._source_key(v) == old_route, "_source_key(%r)" % (v,))
for s in ("demo", "snapshot", "live"):
    ck(A._ud_mode(s) == ("sim" if s == "demo" else "live"), "_ud_mode shell %s" % s)
    for bm in ("sim", "live"):
        ck(A._ud_mode(s, bm) == bm, "_ud_mode body %s/%s" % (s, bm))
ck(A._LIVE_SRC == "live", "_LIVE_SRC == live")
ck(A._SYNTH_PROC is False, "_SYNTH_PROC False")
ck(A._SYNTH_ASSIST is False and os.environ.get("BOARD_SYNTH_ASSISTANT") == "1",
   "filming assistant flag set in a non-synth process stays off")
# PA2（`_handoff/PLAN-board-assistant-phase2-20261030.md`）：公開旗標在非合成進程同樣無效；共用 gateway 的
#   quota／budget 分類只在合成進程開（非合成進程 429／503-budget 照舊 ai_fail）。
ck(A._PUBLIC_ASSIST is False and A._SYNTH_ASSIST is False and os.environ.get("BOARD_PUBLIC_ASSISTANT") == "1",
   "PA2 public assistant flag set in a non-synth process stays off")
ck(A._gw_error_kind("HTTPError", 429, "daily question limit reached") == "ai_fail"
   and A._gw_error_kind("HTTPError", 503, "daily budget exhausted") == "ai_fail"
   and A._gw_error_kind("HTTPError", 502, "timed out") == "ai_timeout",
   "PA2 _gw_error_kind non-synth: 429 / 503-budget stay ai_fail (classification unchanged)")
ck(A._assist_configured() == A._hermes_configured(), "_assist_configured == _hermes_configured (non-synth)")
ck(A.DEFAULT_SOURCE == os.environ.get("BOARD_SOURCE", "demo"), "DEFAULT_SOURCE = env")
ck(A._SOURCES == ("demo", "snapshot", "live"), "_SOURCES value/order")
ck(A._UI_VALUES_PROC is A.UI_VALUES, "_UI_VALUES_PROC is UI_VALUES")
ck(not any(getattr(m, "cls", None) is A._SynthSourceMiddleware for m in A.app.user_middleware),
   "synth middleware not installed")
for src in ("live", "demo", "snapshot", "", None, "synthetic", "LIVE"):
    ck(A._ledger_doc_ok({"source": src}) is True, "_ledger_doc_ok keeps source=%r" % (src,))
ck(A._ledger_doc_ok({"source": "synth"}) is False and A._ledger_doc_ok({"source": " Synth "}) is False,
   "_ledger_doc_ok rejects synth docs in non-synth process")
ck(A._ledger_doc_ok("x") is True, "_ledger_doc_ok non-dict passthrough")
ck(A._mr_synth_rotate() == [], "_mr_synth_rotate no-op in non-synth")
for name in ("wo_serials_status", "station_variables", "serial_genealogy", "wo_materials", "lot_where_used",
             "shipment_lookup", "station_failure_pareto", "module_die_etest", "equipment_status_events",
             "equipment_modules", "equipment_calibration", "segment_data", "station_spec_limits",
             "station_retest", "wo_route", "knowledge_card"):
    f = getattr(WM, name)
    ck(not hasattr(f, "__wrapped__"), "mcp %s is the original function (decorator identity)" % name)
ck("synthetic_line" not in WM.ping(), "mcp payload has no synthetic_line key in non-synth process")
_em = WM.explain_metric("util.availability", "en")
ck("synthetic_line_note" not in _em and "synthetic_line" not in _em, "WP-4b R-B4: explain_metric adds no synth keys in non-synth")
ck(A._synth_query_string(b"source=live&lang=en") == b"source=synth&lang=en", "query rewrite pure fn")
R["n"] = n
R["bad"] = bad
R["wsynth_loaded"] = sorted(m for m in sys.modules if m.startswith("wsynth_"))
'''


def _truth_tables(base_tree, verbose):
    inputs = [list(x) if isinstance(x, tuple) else ("__bytes__" if isinstance(x, bytes) else x)
              for x in _INPUTS]
    out = {}
    for tag, tree in (("base", base_tree), ("new", REPO)):
        sd = tempfile.mkdtemp(prefix="wp4a_tt_%s_" % tag)
        env = _base_env(dict(_POISON, BOARD_SOURCE="demo", BOARD_STATE_DIR=sd,
                             BOARD_ASSISTANT_DB=os.path.join(sd, "a.sqlite3")))
        env.pop("BOARD_ASSISTANT_UI", None)          # 真值表自己控制這把
        res, _r = _run_child(_TRUTH_CHILD, tree, env, {"inputs": inputs})
        out[tag] = res
    return out


# ─────────────────────────────────────────────────────────────────────────────
# (P) 頁面位元組
# ─────────────────────────────────────────────────────────────────────────────
_PAGE_PATHS = ("/", "/landing/v1", "/landing/body", "/landing2/body", "/landing/state", "/availability",
               "/utilization", "/equipment", "/equipment/body", "/equipment/health", "/equipment/health/body",
               "/equipment/pm", "/equipment/pm/body", "/equipment/downtime/monthly",
               "/equipment/downtime/monthly/body", "/station/" + CAL, "/station/%s/overview" % CAL,
               "/station/%s/units" % CAL, "/station", "/daily", "/daily/body", "/dispatch", "/dispatch/body",
               "/manpower", "/monthly", "/monthly/body", "/output", "/yield", "/rework", "/saltmarsh/weekly",
               "/saltmarsh/weekly/body", "/budget", "/budget/body", "/cost", "/cost/body", "/updown", "/updown/body",
               "/machine/EID-24742",
               # WP-4b R-B3：匯出（非合成 demo 的 CSV 與檔名逐位元同前；檔名經 content-disposition 併入 md5）
               "/equipment/export.csv", "/equipment/health/export.csv")
_PAGE_QUERIES = ("source=demo&lang=en", "source=demo&lang=zh", "source=demo&lang=en&ui=v1",
                 "source=synth&lang=en", "source=bogus&lang=en", "lang=en")

_PAGES_CHILD = r'''
_stub_pyodbc()
_freeze_clock(ARG["anchor"])
os.chdir(TREE)
import hashlib
from fastapi.testclient import TestClient
import app as A
c = TestClient(A.app, raise_server_exceptions=False)
pages = {}
for u in ARG["urls"]:
    assert "source=live" not in u
    r = c.get(u)
    pages[u] = [r.status_code, hashlib.md5(r.content + r.headers.get("content-disposition", "").encode()).hexdigest(),
                len(r.content)]
R["pages"] = pages
R["wsynth_loaded"] = sorted(m for m in sys.modules if m.startswith("wsynth_"))
'''


def _pages(base_tree):
    urls = [p + "?" + q for q in _PAGE_QUERIES for p in _PAGE_PATHS]
    anchor = datetime.datetime.now().replace(microsecond=0).isoformat()
    out = {}
    for tag, tree in (("base", base_tree), ("new", REPO)):
        sd = tempfile.mkdtemp(prefix="wp4a_pg_%s_" % tag)
        # 錄影助理開關（2026-10-02）：兩棵樹都帶 `BOARD_SYNTH_ASSISTANT=1`＝非合成進程必須無視它（逐位元同）
        #   PA2：公開旗標 `BOARD_PUBLIC_ASSISTANT=1`／`BOARD_ASSIST_TIER_DEEP=1` 同理（舊樹不認得＝本來就無視）
        env = _base_env(dict(_POISON, BOARD_SOURCE="demo", BOARD_STATE_DIR=sd,
                             BOARD_ASSISTANT_DB=os.path.join(sd, "a.sqlite3"), BOARD_SYNTH_ASSISTANT="1",
                             BOARD_PUBLIC_ASSISTANT="1", BOARD_ASSIST_TIER_DEEP="1"))
        res, _r = _run_child(_PAGES_CHILD, tree, env, {"urls": urls, "anchor": anchor})
        out[tag] = res
    return urls, out


# ─────────────────────────────────────────────────────────────────────────────
# (S) 合成進程
# ─────────────────────────────────────────────────────────────────────────────
PHASE1 = ("/", "/landing2/body", "/landing/state", "/utilization", "/availability", "/equipment",
          "/equipment/body", "/equipment/health", "/equipment/health/body", "/station",
          "/station/" + CAL, "/station/%s/overview" % CAL, "/station/%s/units" % CAL,
          "/machine/EID-24742",
          "/equipment/export.csv", "/equipment/health/export.csv", "/api/scoreboard")
NOT_PHASE1 = ("/daily", "/daily/body", "/dispatch", "/dispatch/body", "/manpower", "/monthly", "/rework",
              "/output", "/yield", "/budget", "/cost", "/saltmarsh/weekly", "/equipment/pm",
              "/equipment/downtime/monthly", "/assistant", "/assistant/panel", "/landing/v1", "/landing/body",
              "/landing/opr/warm", "/equipment/translate/all", "/api/qlik", "/api/yield",
              "/api/confirmed_reasons", "/.well-known/onbox-jwks.json", "/equipment/diagnose",
              "/updown", "/updown/body")     # WP-4b R-A1：/updown 移出第一期
#: 請求期間不得開啟的 repo 內路徑片段（C-SY1、清冊 §8）
#: （`_state`／repo 根產物名取自 `core_paths` 常數：本檔不寫這些路徑字面，免得 core_paths 的原始碼掃描誤判。）
sys.path.insert(0, REPO)
import core_paths as _wp  # noqa: E402  零相依
FORBIDDEN_PARTS = ("_fixtures", "_kb_a", "_kb_b", _wp.STATE_DIRNAME, "layout_eid_xy.json",
                   "_hermes.env", "_imonnit.env", "internal-file", "_KUN-", _wp.PROD_CALENDAR_FILE,
                   _wp.ASSISTANT_DB_FILE)
#: import 期間的例外白名單：整合輪起**必須為空**（board_diag／board_downpanel 在合成進程延遲載入、
#: Jev 的 key 與題庫在合成進程不讀；Fable 2026-09-29 裁「合成進程不得在 import 期讀任何 fixture」）。
KNOWN_IMPORT_TIME = ()

_SYNTH_CHILD = r'''
REPO = TREE
OPENED, WRITES, POPEN, THREADS, LEAKS, FSOPS = [], [], [], [], [], []
PHASE = {"v": "boot"}
_bopen = builtins.open
def _rec(file, mode="r", *a, **k):
    try:
        p = None if isinstance(file, int) else os.path.abspath(os.fspath(file))
    except Exception:
        p = None
    if p:
        OPENED.append((PHASE["v"], p))
        if any(ch in str(mode) for ch in "wax+"):
            WRITES.append(p)
    return _bopen(file, mode, *a, **k)
builtins.open = _rec
io.open = _rec
for _nm in ("replace", "rename", "remove", "unlink", "mkdir", "makedirs", "rmdir"):
    _orig = getattr(os, _nm)
    def _w(*a, _o=_orig, _n=_nm, **k):
        try:
            FSOPS.append((_n, os.path.abspath(os.fspath(a[-1] if _n in ("replace", "rename") else a[0]))))
        except Exception:
            pass
        return _o(*a, **k)
    setattr(os, _nm, _w)
_ts = threading.Thread.start
def _tstart(self):
    THREADS.append(self.name)
    return _ts(self)
threading.Thread.start = _tstart
class _NoPopen(subprocess.Popen):
    def __init__(self, *a, **k):
        POPEN.append(repr(a)[:200])
        raise OSError("external process blocked by selftest")
subprocess.Popen = _NoPopen
os.chdir(TD)
import wsynth_launch as L
summ = L.boot(out=lambda s: None)
R["boot_ok"] = summ.get("ok")
R["boot_checks"] = [[c["name"], c["ok"]] for c in summ.get("checks", [])]
drv = sys.modules["pyodbc"]
R["driver_is_stub"] = bool(drv.__dict__.get("__core_source_stub__"))
_c = drv.connect
def _leak(*a, **k):
    st = [f.filename for f in traceback.extract_stack()[:-1]]
    LEAKS.append([os.path.basename(x) for x in st if x.startswith(REPO)][-4:])
    return _c(*a, **k)
drv.connect = _leak
app = L.import_app(out=lambda s: None)
PHASE["v"] = "request"
import core_source as S
import core_paths as P
import board_data as wd
import board_attrib as WA
import board_mcp as WM
from fastapi.testclient import TestClient
c = TestClient(app.app, raise_server_exceptions=False)
pages = {}
for u in ARG["phase1"] + ARG["not_phase1"]:
    assert "source=live" not in u
    uu = u + ("&" if "?" in u else "?") + "lang=en"
    r = c.get(uu)
    pages[u] = [r.status_code, len(r.content), ("Traceback" in r.text) or ("SynthLeakError" in r.text)]
# Q-B：帶 demo／snapshot 仍是合成（不帶 live：selftest 紀律）
for q in ("source=demo", "source=snapshot", "source=bogus"):
    r = c.get("/utilization?" + q)
    pages["/utilization?" + q] = [r.status_code, len(r.content), 'source=synth' in r.text and 'source=demo' not in r.text]
# Q18：v1 關閉（查詢參數與 cookie 都無效）
r1 = c.get("/utilization?ui=v1&lang=en")
r2 = c.get("/utilization?lang=en", cookies={"board_ui": "v1"})
R["v1_closed"] = ["/static/v2/" in r1.text, "/static/v2/" in r2.text]
# 私機功能
R["priv"] = {
    "hermes_configured": app._hermes_configured(),
    "assistant_ui": app._assistant_ui_enabled(),
    "jev_router_on": app._jev_router_on(),
    "jev_shadow_enabled": app._jev_shadow_enabled(),
    "gw_kick": app._gw_kick_task(),
    "dx_run_live": list(app._dx_run_live("EID-24742", 3)),
    "dx_confirmed": app._dx_confirmed_live({}, "EID-24742"),
    "env_enabled": app._env_enabled(),
    "env_poller_on": bool(app._ENV_POLLER.get("on")),
    "hermes_post": app._hermes_post("x"),
    "synth_assist": app._SYNTH_ASSIST,                 # 錄影助理開關（未設 env ⇒ 關）
    "assist_configured": app._assist_configured(),
}
R["dock_off"] = "assistant-dock" in c.get("/utilization?lang=en").text
# 來源值／判斷
R["src"] = {
    "DEFAULT_SOURCE": app.DEFAULT_SOURCE, "_LIVE_SRC": app._LIVE_SRC, "_SYNTH_PROC": app._SYNTH_PROC,
    "route": [app._route_source(v) for v in ("live", "demo", "snapshot", "bogus", "", None)],
    "key": [app._source_key(v) for v in ("live", "demo", "snapshot", "bogus", "", None)],
    "is_live_synth": app._is_live("synth"), "is_live_demo": app._is_live("demo"),
    "ud_mode": [app._ud_mode("synth"), app._ud_mode("synth", "live")],
    "should_persist": [WA.should_persist("synth"), WA.should_persist("live"), WA.should_persist("demo")],
    "qs": [app._synth_query_string(b"source=live&lang=en").decode(),
           app._synth_query_string(b"a=1&source=demo&source=x&b=%20").decode(),
           app._synth_query_string(b"lang=en").decode()],
    "a11": wd.a11_live_enabled(),
    "ui_values": list(app._UI_VALUES_PROC),
    "export_known": [__import__("board_export")._known_source(v) for v in ("synth", "live", "bogus")],
}
# MCP（Q-J）
sv = WM.station_variables("segCalibrationTest")
kc = WM.knowledge_card("DAF")
pg = WM.ping()
R["mcp"] = {"source": WM._source(), "sv_error": sv.get("error"), "sv_reason": sv.get("unavailable_reason"),
            "sv_disc": (sv.get("synthetic_line") or {}).get("source"),
            "sv_conf": (sv.get("provenance") or {}).get("confidence"),
            "kc_error": kc.get("error"), "kc_disc": (kc.get("synthetic_line") or {}).get("source"),
            "ping_disc": (pg.get("synthetic_line") or {}).get("source"),
            "wrapped": [hasattr(getattr(WM, n), "__wrapped__") for n in ("station_variables", "knowledge_card", "wo_route")]}
# 日帳（Q-C）：狀態目錄裡父進程放了 live／synth 各一份
days = ARG["ledger_days"]
docs = app._ledger_docs(days=5, end_day=days["synth"])
R["ledger"] = {"docs_sources": sorted(str(d.get("source")) for d in docs if isinstance(d, dict)),
               "doc_synth": (app._ledger_doc(days["synth"]) or {}).get("source"),
               "doc_live": app._ledger_doc(days["live"]),
               "rates_days": [d for d, _r in app._eqm_rates_dated_from_ledger()]}
# Q11：月快照輪替（15 份 → 13 份）
snap = app._MR_SNAP_DIR
os.makedirs(snap, exist_ok=True)
months = ["2025-%02d" % m for m in range(1, 13)] + ["2026-01", "2026-02", "2026-03"]
for m in months:
    with open(os.path.join(snap, m + ".json"), "w", encoding="utf-8") as f:
        f.write('{"source": "synth"}')          # 合成進程凍的快照帶 source=synth（Q-C 來源隔離）
with open(os.path.join(snap, "2024-12.json"), "w", encoding="utf-8") as f:
    f.write("{}")                               # 非合成快照（無 source 欄）：合成進程視同不存在、不輪替
gone = app._mr_synth_rotate()
R["rotate"] = {"gone": gone, "left": sorted(n[:-5] for n in os.listdir(snap) if n.endswith(".json"))}
# core_paths 的 import 時定值清單
R["lazy"] = {"diag_loaded": "board_diag" in sys.modules, "dp_loaded": "board_downpanel" in sys.modules}
root = P.env_state_dir()
sites = {}
def site(name, fn):
    try:
        v = os.path.abspath(os.fspath(fn()))
        sites[name] = [v, os.path.normcase(v).startswith(os.path.normcase(root) + os.sep) or
                       os.path.normcase(v) == os.path.normcase(root)]
    except Exception as e:
        sites[name] = ["EXC %s: %s" % (type(e).__name__, str(e)[:120]), False]
PHASE["v"] = "sites"      # 以下是**自測自己** import 模組取路徑常數（不是 app 的行為）
import board_diag as diag
import _wo_ledger as wl
site("app.EQM_LP_STATE", lambda: app.EQM_LP_STATE)
site("app._MR_SNAP_DIR", lambda: app._MR_SNAP_DIR)
site("board_data.PROD_CALENDAR_PATH", lambda: wd.PROD_CALENDAR_PATH)
site("board_data.KUN_MANPOWER_PATH", lambda: wd.KUN_MANPOWER_PATH)
site("board_data.KUN_SLOTS_PATH", lambda: wd.KUN_SLOTS_PATH)
site("board_diag._CONFIRMED_PATH", lambda: diag._CONFIRMED_PATH)
site("_wo_ledger.DEFAULT_LEDGER", lambda: wl.DEFAULT_LEDGER)
for mod, attr in (("_probe_cost_rollup", "STATE_PATH"), ("_probe_llm_classify", "STATE"),
                  ("_probe_rw_denominator", "LEDGER_DIR"), ("_backfill_ledger", "LEDGER_DIR"),
                  ("_probe_kun_activity", "RAW_OUT"), ("_probe_kun_manpower", "RAW_OUT")):
    site(mod + "." + attr, lambda mod=mod, attr=attr: getattr(__import__(mod), attr))
R["sites"] = sites
R["import_time_sites"] = list(P.IMPORT_TIME_SITES)
R["opened"] = sorted(set((ph, p) for ph, p in OPENED if p.startswith(REPO)))
R["writes"] = sorted(set(WRITES))
R["fsops"] = sorted(set(FSOPS))
R["popen"] = POPEN
R["threads"] = THREADS
R["leaks"] = LEAKS
R["pages"] = pages
R["state_root"] = root
'''


def _synth(ledger_sample):
    td = tempfile.mkdtemp(prefix="wp4a_synth_")
    sd = os.path.join(td, "state")
    # 父進程預先放標記檔＋兩份日帳（synth／live）——子進程只讀狀態目錄、不碰 fixture。
    sys.path.insert(0, REPO)
    import core_source as S
    os.makedirs(os.path.join(sd, "daily_ledger"))
    with open(os.path.join(sd, S.MARKER_FILE), "w", encoding="utf-8") as f:
        json.dump({"kind": S.MARKER_KIND, "version": S.MARKER_VERSION, "created_by": "wsynth_wiring_selftest"}, f)
    today = datetime.date.today()
    days = {"synth": (today - datetime.timedelta(days=1)).isoformat(),
            "live": (today - datetime.timedelta(days=2)).isoformat()}
    for src, day in days.items():
        doc = dict(ledger_sample, source=src, day=day)
        with open(os.path.join(sd, "daily_ledger", day + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    env = _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": sd})
    res, r = _run_child(_SYNTH_CHILD, REPO, env, {"phase1": list(PHASE1), "not_phase1": list(NOT_PHASE1),
                                                  "ledger_days": days})
    return res, days, r


_NONSYNTH_LEDGER_CHILD = r'''
_stub_pyodbc()
os.chdir(TREE)
import app as A
import board_attrib as WA
import _freeze_monthly as FM
days = ARG["ledger_days"]
docs = A._ledger_docs(days=5, end_day=days["synth"])
mr_docs, _deg = A._mr_docs("live")
R["docs_sources"] = sorted(str(d.get("source")) for d in docs if isinstance(d, dict))
R["mr_sources"] = sorted(str(d.get("source")) for d in mr_docs if isinstance(d, dict))
R["doc_synth"] = A._ledger_doc(days["synth"])
R["doc_live"] = (A._ledger_doc(days["live"]) or {}).get("source")
R["rates_days"] = [d for d, _r in A._eqm_rates_dated_from_ledger()]
R["persist"] = [WA.should_persist("synth"), WA.should_persist("live"), WA.should_persist(" LIVE ")]
lines = []
FM.run(days["synth"][:7], dry_run=True, app_mod=A, out=lines.append)
R["fm_ran"] = bool(lines)
R["wsynth_loaded"] = sorted(m for m in sys.modules if m.startswith("wsynth_"))
'''


def _nonsynth_ledger(ledger_sample):
    td = tempfile.mkdtemp(prefix="wp4a_ns_")
    sd = os.path.join(td, "state")
    os.makedirs(os.path.join(sd, "daily_ledger"))
    today = datetime.date.today()
    days = {"synth": (today - datetime.timedelta(days=1)).isoformat(),
            "live": (today - datetime.timedelta(days=2)).isoformat()}
    for src, day in days.items():
        doc = dict(ledger_sample, source=src, day=day)
        with open(os.path.join(sd, "daily_ledger", day + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    env = _base_env(dict(_POISON, BOARD_SOURCE="demo", BOARD_STATE_DIR=sd,
                         BOARD_ASSISTANT_DB=os.path.join(sd, "a.sqlite3")))
    res, _r = _run_child(_NONSYNTH_LEDGER_CHILD, REPO, env, {"ledger_days": days})
    return res, days


_TICK_CHILD = r'''
_freeze_clock(ARG["anchor"])
os.chdir(TD)
WRITES, FSOPS = [], []
_bopen = builtins.open
def _rec(file, mode="r", *a, **k):
    try:
        p = None if isinstance(file, int) else os.path.abspath(os.fspath(file))
    except Exception:
        p = None
    if p and any(ch in str(mode) for ch in "wax+"):
        WRITES.append(p)
    return _bopen(file, mode, *a, **k)
builtins.open = _rec
io.open = _rec
for _nm in ("replace", "rename", "remove", "unlink", "mkdir", "makedirs", "rmdir"):
    _orig = getattr(os, _nm)
    def _w(*a, _o=_orig, _n=_nm, **k):
        try:
            FSOPS.append([_n, os.path.abspath(os.fspath(a[-1] if _n in ("replace", "rename") else a[0]))])
        except Exception:
            pass
        return _o(*a, **k)
    setattr(os, _nm, _w)
import wsynth_tick as K
K.boot(out=lambda s: None)
summ = K.run_tick(K.parse_steps("calendar,lastprod,freeze"), out=lambda s: None)
R["summary"] = summ
import app
import board_data as wd
import core_lastprod_push as LP
import core_paths as P
nc = lambda p: os.path.normcase(os.path.abspath(os.fspath(p)))
R["cal_path_same"] = nc(wd.PROD_CALENDAR_PATH) == nc(P.prod_calendar_path(repo=TREE))
R["lp_path_same"] = nc(app.EQM_LP_STATE) == nc(LP.state_file_path())
R["cal_status"] = wd.load_prod_calendar()[1]
ages, meta = wd.lastprod_state_ages(app.EQM_LP_STATE)
R["lp_ages_n"] = len(ages or {})
R["lp_meta"] = repr(meta)[:200]
from fastapi.testclient import TestClient
c = TestClient(app.app, raise_server_exceptions=False)
R["warm_allowed"] = [bool(app._synth_path_allowed(p.partition("?")[0])) for p in K.WARM_PATHS]
st = {}
for p in K.WARM_PATHS:
    assert "source=live" not in p
    r = c.get(p)
    st[p] = [r.status_code, ("Traceback" in r.text) or ("SynthLeakError" in r.text)]
R["warm_status"] = st
R["state_root"] = P.env_state_dir()
R["writes"] = sorted(set(WRITES))
R["fsops"] = sorted(set(tuple(x) for x in FSOPS))
'''


def _tick_case():
    """(T) WP-6：合成子進程跑 tick，再以同一進程的 app 讀回（凍結時鐘、狀態目錄改向、無 cmms）。"""
    td = tempfile.mkdtemp(prefix="wp6_tick_")
    anchor = datetime.datetime.now().replace(second=0, microsecond=0)
    env = _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": os.path.join(td, "state"),
                     "BOARD_SYNTH_NOW": anchor.isoformat()})
    res, _r = _run_child(_TICK_CHILD, REPO, env, {"anchor": anchor.isoformat()})
    return res


def _launcher_case(over, extra_args=()):
    td = tempfile.mkdtemp(prefix="wp4a_launch_")
    env = _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": os.path.join(td, "state")})
    for k, v in over.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    r = subprocess.run([sys.executable, os.path.join(REPO, "wsynth_launch.py"), "--check"] + list(extra_args),
                       env=env, cwd=td, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    return r.returncode, (r.stdout or "") + (r.stderr or ""), os.path.join(td, "state")


# ─────────────────────────────────────────────────────────────────────────────
# (SA) 錄影助理開關（Fable 2026-10-02；SPEC-SYNTHLINE §8.4；預設關）
# ─────────────────────────────────────────────────────────────────────────────
#: 開關開時要看 dock 的第一期整頁（en／zh 各一次）
SA_FULL = ("/", "/utilization?days=7", "/availability?days=7", "/equipment", "/equipment/health", "/station",
           "/station/%s?days=7" % CAL, "/machine/EID-24742?days=7")
#: gateway `/health` 宣告的 MCP 位址：必須被擋的（埠 80＝TestClient 的 scope["server"] 埠＝本進程埠）
SA_BLOCKED_MCP = ("http://127.0.0.1:18000/mcp", "http://localhost:8000/mcp", "http://127.0.0.1:8787/mcp",
                  "http://192.0.2.5:10080/mcp", "http://127.0.0.1:10081/mcp", "https://127.0.0.1:10080/mcp",
                  "http://127.0.0.1:10080/other", "http://127.0.0.1/mcp", "http://u:p@127.0.0.1:10080/mcp",
                  "__missing__", "__unreadable__")
SA_OK_MCP = ("http://127.0.0.1:10080/mcp", "http://localhost:80/mcp")

_SA_CHILD = r'''
import re, socket, http.server
from markupsafe import escape as _mesc
os.chdir(TD)
POPEN = []
class _NoPopen(subprocess.Popen):
    def __init__(self, *a, **k):
        POPEN.append(repr(a)[:200])
        raise OSError("external process blocked by selftest")
subprocess.Popen = _NoPopen
# ── 假 gateway（loopback；只記錄、不跑任何模型）──────────────────────────────
GW = {"mcp_url": None, "mode": "ok", "chat": [], "health": 0, "post": "ok"}
class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _send(self, code, raw):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if self.path != "/health":
            return self._send(404, b"{}")
        GW["health"] += 1
        if GW["mode"] == "__unreadable__":
            return self._send(200, b"not json")
        doc = {"status": "ok", "persona_rules_sha": "x"}
        # PA2：共用 gateway 的 `/health` 形（cmms `infra/hermes/CONTRACT.md` §5）＝
        #   `mcp_targets.board = {"configured": bool, "url": str|null}`；只有 mcp_targets 整個不在才退回 `mcp_url`
        _cm = {"configured": True, "url": "http://app:8080/mcp"}
        if GW["mode"] == "targets":                       # 只有 mcp_targets.board（無頂層 mcp_url）
            doc["mcp_targets"] = {"cmms": _cm, "board": {"configured": True, "url": GW["mcp_url"]}}
        elif GW["mode"] == "targets_bad":                 # board 指錯、頂層 mcp_url 是對的 ⇒ 不得退回、要擋
            doc["mcp_targets"] = {"cmms": _cm, "board": {"configured": True, "url": "http://127.0.0.1:18000/mcp"}}
            doc["mcp_url"] = GW["mcp_url"]
        elif GW["mode"] == "targets_noboard":             # mcp_targets 在、沒有 board ⇒ 擋（不退回 mcp_url）
            doc["mcp_targets"] = {"cmms": _cm}
            doc["mcp_url"] = GW["mcp_url"]
        elif GW["mode"] == "targets_unconfigured":        # configured=false（url 即使對）⇒ 擋
            doc["mcp_targets"] = {"cmms": _cm, "board": {"configured": False, "url": GW["mcp_url"]}}
        elif GW["mode"] == "targets_nullurl":             # url=null ⇒ 擋
            doc["mcp_targets"] = {"cmms": _cm, "board": {"configured": True, "url": None}}
        elif GW["mode"] == "targets_string":              # board 是字串（非合約形）⇒ 擋
            doc["mcp_targets"] = {"cmms": _cm, "board": GW["mcp_url"]}
        elif GW["mode"] != "__missing__":
            doc["mcp_url"] = GW["mcp_url"]
        self._send(200, json.dumps(doc).encode())
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        GW["chat"].append({"path": self.path, "secret": self.headers.get("X-Hermes-Secret"),
                           "message": body.get("message", ""), "turn_id": body.get("turn_id"),
                           # PA2 合約鍵（值原樣記；token 只比對、不印）
                           "keys": sorted(body), "scoped_token": body.get("scoped_token"),
                           "target": body.get("target"), "client_id": body.get("client_id"),
                           "tier": body.get("tier", "__absent__")})
        if GW["post"] == "quota":                         # PA2：每來源每日上限
            return self._send(429, json.dumps({"detail": "daily question limit reached"}).encode())
        if GW["post"] in ("budget", "budget_monthly"):     # PA2：預算用罄（合約兩種 detail）
            return self._send(503, json.dumps({"detail": "daily budget exhausted" if GW["post"] == "budget"
                                               else "monthly budget exhausted"}).encode())
        if GW["post"] == "unconfigured":                  # PA2：503 其他 detail＝後端未設定 ⇒ 照舊 ai_fail
            return self._send(503, json.dumps({"detail": "agent backend not configured: anthropic credentials rejected"}).encode())
        self._send(200, json.dumps({"reply": ARG["reply"]}).encode())
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
GW_URL = "http://127.0.0.1:%d" % srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True, name="sa-fake-gw").start()
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
DEAD_URL = "http://127.0.0.1:%d" % _s.getsockname()[1]
_s.close()
ENV_OK = {"BOARD_HERMES_URL": GW_URL, "BOARD_HERMES_SECRET": "sa-secret", "BOARD_MCP_TOKEN": "sa-token"}
os.environ.update(ENV_OK)
import wsynth_launch as L
summ = L.boot(out=lambda s: None)
R["boot_ok"] = summ.get("ok")
app = L.import_app(out=lambda s: None)
app._GW_HEAL_WAIT_S = 0
import board_assistant as WA
from fastapi.testclient import TestClient
I18 = app.I18N
def esc(x):
    return str(_mesc(x))
def cl():
    return TestClient(app.app, raise_server_exceptions=False)
def forbidden(h):
    hits = []
    for pat in ARG["forbidden"]:
        for mm in re.finditer(pat, h):
            hits.append(h[max(0, mm.start() - 60):mm.end() + 30])
    return hits
c = cl()
R["flags"] = {"SYNTH_ASSIST": app._SYNTH_ASSIST, "assistant_ui": app._assistant_ui_enabled(),
              "hermes_configured": app._hermes_configured(), "assist_configured": app._assist_configured(),
              "hermes_post": app._hermes_post("x"), "gw_kick": app._gw_kick_task(),
              "PUBLIC_ASSIST": app._PUBLIC_ASSIST}                # PA2：本案只開錄影旗標 ⇒ 公開旗標 False
# ── 純函式表 ─────────────────────────────────────────────────────────────────
R["flag_parse"] = {repr(v): app._synth_assist_flag({} if v is None else {app.ENV_SYNTH_ASSISTANT: v})
                   for v in (None, "1", " 1 ", "0", "", "true", "yes", "01", "2")}
# PA2：公開旗標／deep tier 旗標同一口徑（去空白後恰為 "1"）；兩旗標互不串（錄影鍵不開公開、反之亦然）
R["pub_flag_parse"] = {repr(v): app._public_assist_flag({} if v is None else {app.ENV_PUBLIC_ASSISTANT: v})
                       for v in (None, "1", " 1 ", "0", "", "true", "yes", "01", "2")}
R["tier_flag_parse"] = {repr(v): app._assist_tier_deep_flag({} if v is None else {app.ENV_ASSIST_TIER_DEEP: v})
                        for v in (None, "1", " 1 ", "0", "", "true", "deep")}
R["flag_cross"] = [app._public_assist_flag({app.ENV_SYNTH_ASSISTANT: "1"}),
                   app._synth_assist_flag({app.ENV_PUBLIC_ASSISTANT: "1"})]
R["env_names"] = [app.ENV_PUBLIC_ASSISTANT, app.ENV_ASSIST_TIER_DEEP, app.ENV_GW_MCP_URL_EXPECTED]
# PA2：`_synth_mcp_target_ok(expected=)` 可稽核放行模式（Docker 內 gateway 經 host.docker.internal 回打隧道）
R["mcp_table_exp"] = {"%s|%s|%s" % (u, p, e): app._synth_mcp_target_ok(u, p, e)[0] for u, p, e in ARG["mcp_exp"]}
R["gw_url"] = {u: app._synth_gateway_url_ok(u)[0] for u in ARG["gw_urls"]}
R["mcp_tbl"] = {"%s|%s" % (u, p): app._synth_mcp_target_ok(u, p)[0] for u, p in ARG["mcp_pairs"]}
# ── 頁面（dock）──────────────────────────────────────────────────────────────
PG = {"status": {}, "dock": {}, "badge": {}, "fullpage_link": {}, "trall": {}, "forbidden": {}, "links": {},
      "neutral": {}}
for lg in ("en", "zh"):
    for u in ARG["full"]:
        uu = u + ("&" if "?" in u else "?") + "lang=" + lg
        assert "source=live" not in uu
        r = c.get(uu)
        h = r.text
        PG["status"][uu] = r.status_code
        m = re.search(r'<aside class="board-dock".*?</aside>', h, re.S)
        dock = m.group(0) if m else ""
        PG["dock"][uu] = bool(dock)
        PG["badge"][uu] = ('<span class="board-src synth" title="%s">%s</span>'
                           % (esc(I18[lg]["as_synth_note"]), esc(I18[lg]["source_label_synth"]))) in dock
        PG["fullpage_link"][uu] = "/assistant?" in h
        mfab = re.search(r'<button class="board-fab".*?</button>', h, re.S)
        fab = mfab.group(0) if mfab else ""
        mttl = re.search(r'<span class="ttl">(.*?)</span>', dock, re.S)
        PG["neutral"][uu] = {"hermes": bool(re.search("hermes", fab + dock, re.I)),
                             "ttl": mttl.group(1) if mttl else None,
                             "fab_text": re.sub(r"<[^>]+>", "", fab).strip(),
                             "fab_aria": (re.search(r'aria-label="([^"]*)"', fab) or [None, None])[1],
                             "dock_aria": (re.search(r'<aside[^>]*aria-label="([^"]*)"', dock) or [None, None])[1]}
        PG["trall"][uu] = 'id="eq-trall-btn"' in h
        fh = forbidden(h)
        if fh:
            PG["forbidden"][uu] = fh[:3]
        for mm in re.finditer(r'\b(?:href|hx-get|hx-post)="([^"]*)"', dock):
            PG["links"].setdefault(mm.group(1).replace("&amp;", "&"), uu)
R["pages"] = PG
R["self_port"] = app._SYNTH_SELF_PORT["port"]
LK = {}
for v in sorted(PG["links"]):
    p = v.split("?", 1)[0]
    LK[v] = [app._synth_path_allowed(p), c.get(v).status_code if "{" not in v else None]
R["dock_links"] = LK
PN = {}
for lg in ("en", "zh"):
    r = c.get("/assistant/panel?lang=" + lg)
    h = r.text
    PN[lg] = {"status": r.status_code, "form": 'id="assistant-form"' in h,
              "note": ('<span class="board-src synth">%s</span> %s' % (esc(I18[lg]["source_label_synth"]),
                                                                   esc(I18[lg]["as_synth_note"]))) in h,
              "disabled": esc(I18[lg]["as_disabled"]) in h, "forbidden": forbidden(h)[:3],
              "hermes": bool(re.search("hermes", h, re.I))}
R["panel"] = PN
R["routes"] = {u: c.get(u).status_code for u in ("/assistant", "/assistant?lang=zh", "/daily", "/updown",
                                                   "/equipment/translate/all")}
R["note_forbidden"] = {"prompt": forbidden(app._SYNTH_ASSIST_PROMPT_NOTE),
                       "en": forbidden(I18["en"]["as_synth_note"]), "zh": forbidden(I18["zh"]["as_synth_note"])}
# ── 一輪問答：送出 → 背景 worker → 著地 ──────────────────────────────────────
def ask(msg, client=None, xff=None):
    cc = TestClient(app.app, raise_server_exceptions=False, **({"client": client} if client else {}))
    if xff is not None:
        cc.headers["X-Forwarded-For"] = xff
    cc.get("/utilization?days=7&lang=en")                 # 記下本進程埠（middleware）
    n0, h0 = len(GW["chat"]), GW["health"]
    r = cc.post("/assistant", data={"message": msg, "lang": "en", "page_ctx": "/utilization?days=7"})
    m = re.search(r"/assistant/turn/(\d+)", r.text)
    aid = cc.cookies.get(app._AID_COOKIE)
    if not m:
        return {"http": r.status_code, "turn": None, "disabled": esc(I18["en"]["as_disabled"]) in r.text,
                "chat": len(GW["chat"]) - n0, "health": GW["health"] - h0}, cc
    tid = int(m.group(1))
    t_end = time.time() + 60
    t = WA.get_turn(aid, tid)
    while t and t["status"] == "pending" and time.time() < t_end:
        time.sleep(0.1)
        t = WA.get_turn(aid, tid)
    return {"http": r.status_code, "turn": tid, "status": t and t["status"], "kind": t and t.get("error_kind"),
            "chat": len(GW["chat"]) - n0, "health": GW["health"] - h0, "aid": aid,
            "client_key": t and t.get("client_key")}, cc
TURNS = {}
for u in ARG["ok_mcp"]:
    GW["mode"], GW["mcp_url"] = "ok", u
    TURNS["ok|" + u], _cc = ask("why did this machine stop, and for how long?")
    if u == ARG["ok_mcp"][0]:
        OKC, OKT = _cc, TURNS["ok|" + u]
        last = GW["chat"][-1] if GW["chat"] else {}
        R["chat_payload"] = {"path": last.get("path"), "secret_ok": last.get("secret") == "sa-secret",
                             "note_first": str(last.get("message", "")).startswith(app._SYNTH_ASSIST_PROMPT_NOTE),
                             "turn_id": last.get("turn_id") == str(OKT["turn"])}   # PA2：合約 string
        # PA2 合約鍵：TestClient 預設對端 "testclient"（非 IP）⇒ _client_ip 回 "" ⇒ client_key "unknown"
        R["pa2_payload"] = {"keys": last.get("keys"), "token_ok": last.get("scoped_token") == "sa-token",
                            "target": last.get("target"), "client_id": last.get("client_id"),
                            "stored": OKT.get("client_key"), "tier": last.get("tier")}
for u in ARG["blocked_mcp"]:
    if u in ("__missing__", "__unreadable__"):
        GW["mode"], GW["mcp_url"] = u, None
    else:
        GW["mode"], GW["mcp_url"] = "ok", u
    TURNS["blocked|" + u], _cc = ask("is this plant data?")
GW["mode"], GW["mcp_url"] = "ok", ARG["ok_mcp"][0]
# ── PA2：/health 兩種形、client_id（XFF 只在對端 loopback 時信）、tier、quota／budget 分類 ──────────────
PA2 = {}
for mode in ("targets", "targets_noboard", "targets_bad", "targets_unconfigured", "targets_nullurl", "targets_string"):
    GW["mode"], GW["mcp_url"] = mode, ARG["ok_mcp"][0]
    PA2["health|" + mode], _cc = ask("which machine stopped longest?")
# 非 loopback 目標（埠 80＝TestClient 本進程埠 ⇒ 隧道 10080）：只有 BOARD_GW_MCP_URL_EXPECTED 逐字相等才放行
_DK = "http://host.docker.internal:10080/mcp"
for k, exp in (("docker|expected_eq", _DK), ("docker|expected_ne", "http://host.docker.internal:10080/mcp/"),
               ("docker|unset", None)):
    GW["mode"], GW["mcp_url"] = "targets", _DK
    if exp is None:
        os.environ.pop(app.ENV_GW_MCP_URL_EXPECTED, None)
    else:
        os.environ[app.ENV_GW_MCP_URL_EXPECTED] = exp
    PA2[k], _cc = ask("which machine stopped longest?")
os.environ.pop(app.ENV_GW_MCP_URL_EXPECTED, None)
GW["mode"], GW["mcp_url"] = "ok", ARG["ok_mcp"][0]
_t, _cc = ask("who am i (loopback proxy)?", client=("127.0.0.1", 50001), xff="198.18.0.66 , 203.0.113.9 ")
_t["client_id"] = GW["chat"][-1].get("client_id") if _t.get("chat") else None
_t["want"] = WA.client_key("203.0.113.9", "sa-secret")
PA2["xff_loopback"] = _t
_t, _cc = ask("who am i (direct)?", client=("198.51.100.4", 50002), xff="203.0.113.9")
_t["client_id"] = GW["chat"][-1].get("client_id") if _t.get("chat") else None
_t["want"] = WA.client_key("198.51.100.4", "sa-secret")
PA2["xff_direct"] = _t
import sqlite3
_raw = sqlite3.connect(WA._db_path())                    # 原始 IP 不落庫：整張 message 表任何欄都不得出現
PA2["raw_ip_rows"] = sum(1 for _row in _raw.execute("SELECT * FROM message").fetchall()
                         if any(isinstance(_v, str) and ("203.0.113.9" in _v or "198.51.100.4" in _v) for _v in _row))
_raw.close()
os.environ[app.ENV_ASSIST_TIER_DEEP] = "1"
_t, _cc = ask("deep tier please")
_t["tier"] = GW["chat"][-1].get("tier") if _t.get("chat") else None
PA2["tier_deep"] = _t
os.environ.pop(app.ENV_ASSIST_TIER_DEEP, None)
for pm in ("quota", "budget", "budget_monthly", "unconfigured"):
    GW["post"] = pm
    PA2["post|" + pm], _cc = ask("limit me")
    _r = _cc.get("/assistant/turn/%d?lang=en" % PA2["post|" + pm]["turn"])   # 已著地 ⇒ 286（停輪）＋該輪泡泡
    PA2["post|" + pm]["html_en"] = [_r.status_code, ("assist-turn-%d" % PA2["post|" + pm]["turn"]) in _r.text]
GW["post"] = "ok"
R["pa2"] = PA2
os.environ["BOARD_HERMES_URL"] = DEAD_URL
TURNS["down"], _cc = ask("gateway is down")
R["hermes_fail"] = {lg: bool(re.search("hermes", _cc.get("/assistant/turn/%d?lang=%s" % (TURNS["down"]["turn"], lg)).text, re.I))
                    or bool(re.search("hermes", _cc.get("/assistant/panel?lang=" + lg).text, re.I))
                    for lg in ("en", "zh")}
for k, over in (("cfg|prod_gateway", {"BOARD_HERMES_URL": "http://127.0.0.1:8787"}),
                ("cfg|tunnel_port", {"BOARD_HERMES_URL": "http://127.0.0.1:18000"}),
                ("cfg|dashboard", {"BOARD_HERMES_URL": "http://localhost:8000"}),
                ("cfg|non_loopback", {"BOARD_HERMES_URL": "http://198.51.100.10:8797"}),
                ("cfg|no_token", {"BOARD_MCP_TOKEN": None}),
                ("cfg|no_secret", {"BOARD_HERMES_SECRET": None})):
    os.environ.update(ENV_OK)
    for kk, vv in over.items():
        if vv is None:
            os.environ.pop(kk, None)
        else:
            os.environ[kk] = vv
    TURNS[k], _cc = ask("refused config")
    TURNS[k]["panel_disabled"] = esc(I18["en"]["as_disabled"]) in _cc.get("/assistant/panel?lang=en").text
os.environ.update(ENV_OK)
R["turns"] = TURNS
R["popen"] = POPEN
# ── 回覆渲染與「依據」chip（第一期外連結不掛）─────────────────────────────────
tid = OKT["turn"]
WA.tool_call_record(tid, "get_downtime_monthly", "aaaaaaaa",
                    {"page": "/equipment/downtime/monthly", "freshness": "fixture"}, {})
WA.tool_call_record(tid, "get_utilization", "bbbbbbbb", {"page": "/utilization?days=7", "freshness": "fixture"}, {})
WA.tool_call_record(tid, "explain_metric", "cccccccc", {"freshness": "static"}, {})   # fixture 以外的值不改顯示
h1 = OKC.get("/assistant/turn/%d?lang=en" % tid).text
h2 = OKC.get("/assistant/panel?lang=en").text
def _chips(h):
    m = re.search(r'<details class="board-basis">.*?</details>', h, re.S)
    out = []
    for li in re.findall(r"<li>(.*?)</li>", m.group(0) if m else "", re.S):
        out.append({"text": re.sub(r"<[^>]+>", "", li).strip(),
                    "title": (re.search(r'title="([^"]*)"', li) or [None, ""])[1]})
    return out
R["chips"] = {"turn_en": _chips(h1), "panel_en": _chips(h2),
              "turn_zh": _chips(OKC.get("/assistant/turn/%d?lang=zh" % tid).text),
              "panel_zh": _chips(OKC.get("/assistant/panel?lang=zh").text)}
R["envelope"] = [c["freshness"] for c in WA.tool_basis_for_turn(tid)]            # 資料層（信封 enum）不動
_csv = OKC.get("/assistant/turn/%d/tools.csv" % tid)
R["csv"] = {"status": _csv.status_code, "fixture": "fixture" in _csv.text, "static": "static" in _csv.text,
            "synthetic": "synthetic" in _csv.text}
R["hermes_turn"] = {"turn": bool(re.search("hermes", h1, re.I)), "panel": bool(re.search("hermes", h2, re.I))}
def _basis(h):
    m = re.search(r'<details class="board-basis">.*?</details>', h, re.S)
    d = m.group(0) if m else ""
    return {"found": bool(d), "util_link": 'href="/utilization?days=7"' in d,
            "monthly_link": 'href="/equipment/downtime/monthly"' in d, "chips": d.count("board-basis-chip")}
R["basis"] = {"turn": _basis(h1), "panel": _basis(h2)}
R["reply_html"] = {"turn": re.findall(r'<a href="[^"]*">[^<]*</a>', h1.split('<details', 1)[0])[:12]}
R["render_fn"] = app._synth_render_reply(ARG["reply"])
'''


def _assist_case():
    """(SA) 合成子進程、錄影助理開關開（env 由父進程給）、loopback 假 gateway（子進程自己起）。"""
    td = tempfile.mkdtemp(prefix="wsa_")
    env = _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": os.path.join(td, "state"),
                     "BOARD_SYNTH_ASSISTANT": "1"})
    gw_urls = ["http://127.0.0.1:8797", "http://localhost:8797", "http://[::1]:8797", "http://127.0.0.1:8787",
               "http://127.0.0.1:8000", "http://127.0.0.1:18000", "http://192.0.2.5:8797", "https://127.0.0.1:8797",
               "http://127.0.0.1", "http://127.0.0.1:8797/chat", "http://u:p@127.0.0.1:8797",
               "http://127.0.0.1:8797?x=1", "http://127.0.0.1:8797#f", "http://evil.example:8797", "", None]
    mcp_pairs = [["http://127.0.0.1:18061/mcp", 8061], ["http://127.0.0.1:8061/mcp", 8061],
                 ["http://localhost:18061/mcp", 8061], ["http://127.0.0.1:18000/mcp", 8000],
                 ["http://127.0.0.1:18000/mcp", 8061], ["http://127.0.0.1:8000/mcp", 8061],
                 ["http://127.0.0.1:18062/mcp", 8061], ["http://127.0.0.1:18061/mcp", None],
                 ["http://127.0.0.1:18061/mcp", True], ["http://127.0.0.1:18061/x", 8061],
                 ["http://192.0.2.5:18061/mcp", 8061], [None, 8061]]
    dk = "http://host.docker.internal:18061/mcp"
    mcp_exp = [[dk, 8061, dk], [dk, 8061, " %s " % dk], [dk, 8061, dk + "/"], [dk, 8061, None], [dk, 8061, ""],
               ["http://host.docker.internal:18062/mcp", 8061, "http://host.docker.internal:18062/mcp"],
               ["http://host.docker.internal:18000/mcp", 8000, "http://host.docker.internal:18000/mcp"],
               ["http://host.docker.internal:18061/x", 8061, "http://host.docker.internal:18061/x"],
               ["https://host.docker.internal:18061/mcp", 8061, "https://host.docker.internal:18061/mcp"],
               ["http://127.0.0.1:18061/mcp", 8061, dk], ["http://127.0.0.1:18061/mcp", 8061, None]]
    reply = ("EID-24742 stopped for a work order. See [utilization](/utilization?days=7), "
             "[daily](/daily) and [station](/station/%s)." % CAL)
    res, _r = _run_child(_SA_CHILD, REPO, env, {"full": list(SA_FULL), "forbidden": list(WP4B_FORBIDDEN),
                                                "ok_mcp": list(SA_OK_MCP), "blocked_mcp": list(SA_BLOCKED_MCP),
                                                "gw_urls": gw_urls, "mcp_pairs": mcp_pairs, "reply": reply,
                                                "mcp_exp": mcp_exp})
    return res


def _assist_report(sa, ck):
    """(SA1–SA15) 判定；PA2 的 (SA16–SA21) 在 `_pa2_report`。"""
    ck(sa.get("boot_ok") is True, "(SA0) synthetic process with the filming assistant flag boots: %s" % sa.get("boot_ok"))
    fl = sa["flags"]
    ck(fl == {"SYNTH_ASSIST": True, "assistant_ui": True, "hermes_configured": False, "assist_configured": True,
              "hermes_post": None, "gw_kick": False, "PUBLIC_ASSIST": False},
       "(SA1) flag on: assistant UI + chat configured; translation gate (_hermes_configured), _hermes_post and "
       "the wsl gateway kick stay off: %s" % fl)
    ck(sa["flag_parse"] == {"None": False, "'1'": True, "' 1 '": True, "'0'": False, "''": False, "'true'": False,
                            "'yes'": False, "'01'": False, "'2'": False},
       "(SA2) flag parse: only '1' turns it on: %s" % sa["flag_parse"])
    want_gw = {"http://127.0.0.1:8797": True, "http://localhost:8797": True, "http://[::1]:8797": True}
    bad_gw = {u: v for u, v in sa["gw_url"].items() if v != want_gw.get(u, False)}
    ck(not bad_gw, "(SA3) gateway URL gate: loopback http with explicit non-production port only (8787/8000/18000, "
       "non-loopback, https, no port, path/query/fragment/userinfo all refused): %s" % bad_gw)
    want_mcp = {"http://127.0.0.1:18061/mcp|8061": True, "http://127.0.0.1:8061/mcp|8061": True,
                "http://localhost:18061/mcp|8061": True}
    bad_mcp = {k: v for k, v in sa["mcp_tbl"].items() if v != want_mcp.get(k, False)}
    ck(not bad_mcp, "(SA4) MCP target gate: only this process's port or its +10000 tunnel; 18000/8000, other ports, "
       "non-loopback, non-/mcp, unknown own port all refused: %s" % bad_mcp)
    pg = sa["pages"]
    ck(all(v == 200 for v in pg["status"].values()) and all(pg["dock"].values()) and all(pg["badge"].values()),
       "(SA5) every phase-1 full page (en/zh, %d) renders the dock with the SYNTHETIC badge + note tooltip: "
       "status=%s dock=%s badge=%s" % (len(pg["status"]), {k: v for k, v in pg["status"].items() if v != 200},
                                       [k for k, v in pg["dock"].items() if not v],
                                       [k for k, v in pg["badge"].items() if not v]))
    ck(not any(pg["fullpage_link"].values()) and not any(pg["trall"].values()),
       "(SA6) no full-page /assistant link (404 in synth) and no translate-all button (translation stays off): "
       "%s %s" % ([k for k, v in pg["fullpage_link"].items() if v], [k for k, v in pg["trall"].items() if v]))
    pn = sa["panel"]
    nf = sa["note_forbidden"]
    ck(not pg["forbidden"] and not pn["en"]["forbidden"] and not pn["zh"]["forbidden"] and not any(nf.values()),
       "(SA7) C-SY11: zero LIVE/REAL/real/真值 on dock pages, the panel (en/zh), the synth note and the prompt "
       "disclosure: %s %s %s" % (pg["forbidden"], [pn[l]["forbidden"] for l in ("en", "zh")], nf))
    ck(all(pn[l]["status"] == 200 and pn[l]["form"] and pn[l]["note"] and not pn[l]["disabled"] for l in ("en", "zh")),
       "(SA8) /assistant/panel en/zh: 200, form present, SYNTHETIC note line on top: %s" % pn)
    bad_lk = {v: x for v, x in sa["dock_links"].items() if not x[0] or x[1] == 404 or (x[1] or 0) >= 500}
    rt = sa["routes"]
    ck(sa["dock_links"] and not bad_lk and rt["/assistant"] == 404 and rt["/assistant?lang=zh"] == 404
       and rt["/daily"] == 404 and rt["/updown"] == 404 and rt["/equipment/translate/all"] == 404,
       "(SA9) dock links all phase-1 and not 404 (%d); full page /assistant and non-phase-1 routes still 404: %s %s"
       % (len(sa["dock_links"]), bad_lk, rt))
    tu = sa["turns"]
    ok = [tu["ok|" + u] for u in SA_OK_MCP]
    cp = sa.get("chat_payload") or {}
    ck(all(t["status"] == "done" and t["chat"] == 1 for t in ok)
       and cp == {"path": "/chat", "secret_ok": True, "note_first": True, "turn_id": True} and sa["self_port"] == 80,
       "(SA10) a turn reaches /chat exactly once when the gateway's MCP target is this process (own port %s or "
       "+10000 tunnel); prompt starts with the synthetic disclosure; secret header sent: %s %s"
       % (sa["self_port"], [{k: t[k] for k in ("status", "chat", "kind")} for t in ok], cp))
    bl = {u: tu["blocked|" + u] for u in SA_BLOCKED_MCP}
    bad_bl = {u: {k: t.get(k) for k in ("status", "kind", "chat", "health")} for u, t in bl.items()
              if not (t["status"] == "failed" and t["kind"] == "ai_fail" and t["chat"] == 0 and t["health"] >= 1)}
    dn = tu["down"]
    ck(not bad_bl and dn["status"] == "failed" and dn["kind"] == "gw_down" and dn["chat"] == 0 and not sa["popen"],
       "(SA11) tripwire: a gateway whose MCP target is not this process (%d cases incl. :18000 / :8000 / :8787, "
       "old gateway without mcp_url, unreadable /health) lands ai_fail with /chat never called; unreachable "
       "gateway lands gw_down without spawning wsl: %s down=%s popen=%s"
       % (len(bl), bad_bl, {k: dn.get(k) for k in ("status", "kind", "chat")}, sa["popen"]))
    cfg = {k: v for k, v in tu.items() if k.startswith("cfg|")}
    bad_cfg = {k: v for k, v in cfg.items() if not (v["turn"] is None and v["disabled"] and v["chat"] == 0
                                                   and v["health"] == 0 and v["panel_disabled"])}
    ck(len(cfg) == 6 and not bad_cfg,
       "(SA12) refused config (gateway URL 8787/18000/8000/non-loopback, missing MCP token or secret): panel and "
       "POST /assistant say 'not enabled', no turn, gateway never contacted: %s" % bad_cfg)
    bs = sa["basis"]
    rf = sa["render_fn"]
    ck(all(b["found"] and b["chips"] == 3 and b["util_link"] and not b["monthly_link"] for b in bs.values())
       and '<a href="/machine/EID-24742">EID-24742</a>' in rf and "/updown" not in rf
       and 'href="/daily"' not in rf and 'href="/utilization?days=7"' in rf and 'href="/station/%s"' % CAL in rf,
       "(SA13) basis fold renders (3 chips) with the phase-1 link kept and the out-of-phase link dropped (turn and "
       "panel); reply links: bare EID -> /machine/EID, /daily unlinked, phase-1 links kept: %s %s" % (bs, rf))
    # (SA14) 中性字樣（Fable 2026-10-02，R-A9 模式）：合成進程助理面板不印產品名
    want = {"en": ("Assistant", "Ask the assistant"), "zh": ("助理", "問助理")}
    bad_nt = {}
    for uu, nt in pg["neutral"].items():
        lg = "zh" if uu.endswith("lang=zh") else "en"
        w_ttl, w_fab = want[lg]
        if not (not nt["hermes"] and nt["ttl"] == "✦ " + w_ttl and nt["fab_text"] == "✦ " + w_fab
                and nt["fab_aria"] == w_fab and nt["dock_aria"] == w_ttl):
            bad_nt[uu] = nt
    ht = sa["hermes_turn"]
    ck(len(pg["neutral"]) == 2 * len(SA_FULL) and not bad_nt and not pn["en"]["hermes"] and not pn["zh"]["hermes"]
       and not ht["turn"] and not ht["panel"] and not any(sa["hermes_fail"].values()),
       "(SA14) neutral wording: with the flag on, the dock title/aria-label and the FAB read 'Assistant'/'Ask the "
       "assistant' (zh 助理/問助理) and the dock, FAB, panel (en/zh), turn partial and a failed turn carry no "
       "'Hermes' on any phase-1 page (%d): %s panel=%s turn=%s fail=%s"
       % (len(pg["neutral"]), dict(list(bad_nt.items())[:2]), [pn[l]["hermes"] for l in ("en", "zh")], ht,
          sa["hermes_fail"]))
    # (SA15) 「依據」chip 新鮮度欄：合成進程 fixture 只改顯示成 synthetic（信封 enum／CSV 不動）
    ch = sa["chips"]
    syn = {"en": "synthetic", "zh": "合成"}
    bad_ch = {}
    for k, cs in ch.items():
        lg = k.rsplit("_", 1)[1]
        by_tool = {c["text"].split(" · ")[0]: c for c in cs}
        a, b, s = by_tool.get("get_downtime_monthly"), by_tool.get("get_utilization"), by_tool.get("explain_metric")
        ok_ = (len(cs) == 3 and a and b and s
               and all(x["text"].split(" · ")[2] == syn[lg] and "fixture" not in x["text"]
                       and (" · %s (fixture) · " % syn[lg]) in x["title"] for x in (a, b))
               and s["text"].split(" · ")[2] == "static" and " · static · " in s["title"] and "fixture" not in s["title"])
        if not ok_:
            bad_ch[k] = cs
    ck(not bad_ch and sa["envelope"] == ["fixture", "fixture", "static"]
       and sa["csv"] == {"status": 200, "fixture": True, "static": True, "synthetic": False},
       "(SA15) basis chip freshness cell: 'fixture' shows as synthetic (zh 合成) in the chip text, the title keeps the "
       "original value ('synthetic (fixture)'), other values (static) unchanged; the stored envelope enum and the "
       "CSV export still say fixture (turn en/zh, panel en/zh): %s envelope=%s csv=%s"
       % (bad_ch, sa["envelope"], sa["csv"]))
    _pa2_report(sa, ck)


def _pa2_report(sa, ck):
    """(SA16–SA21) PA2 公開助理第二期（`_handoff/PLAN-board-assistant-phase2-20261030.md`）：共用 gateway 合約
       （以 cmms `infra/hermes/CONTRACT.md` 為準：`/health` 物件形、`turn_id` string、503 detail 字串）。"""
    want_flag = {"None": False, "'1'": True, "' 1 '": True, "'0'": False, "''": False, "'true'": False,
                 "'yes'": False, "'01'": False, "'2'": False}
    ck(sa["pub_flag_parse"] == want_flag and sa["flag_cross"] == [False, False]
       and sa["env_names"] == ["BOARD_PUBLIC_ASSISTANT", "BOARD_ASSIST_TIER_DEEP", "BOARD_GW_MCP_URL_EXPECTED"]
       and sa["tier_flag_parse"] == {"None": False, "'1'": True, "' 1 '": True, "'0'": False, "''": False,
                                     "'true'": False, "'deep'": False},
       "(SA16) PA2 flags: BOARD_PUBLIC_ASSISTANT / BOARD_ASSIST_TIER_DEEP parse like the filming flag (only '1'); "
       "the filming and public keys do not cross: %s tier=%s cross=%s"
       % (sa["pub_flag_parse"], sa["tier_flag_parse"], sa["flag_cross"]))
    pp = sa.get("pa2_payload") or {}
    ck(pp == {"keys": sorted(["message", "history", "locale", "layout", "turn_id", "scoped_token", "target",
                              "client_id"]),
              "token_ok": True, "target": "board", "client_id": "unknown", "stored": "unknown", "tier": "__absent__"},
       "(SA17) PA2 /chat payload: legacy keys + scoped_token (= BOARD_MCP_TOKEN) + target=board + client_id "
       "(= the turn's stored client_key; non-IP peer -> 'unknown'); no tier key unless asked: %s"
       % {k: v for k, v in pp.items()})
    p2 = sa["pa2"]
    _refused = ("targets_noboard", "targets_bad", "targets_unconfigured", "targets_nullurl", "targets_string")
    hb = {m: {k: p2["health|" + m].get(k) for k in ("status", "kind", "chat", "health")}
          for m in ("targets",) + _refused}
    ck(hb["targets"]["status"] == "done" and hb["targets"]["chat"] == 1
       and all(hb[m]["status"] == "failed" and hb[m]["kind"] == "ai_fail" and hb[m]["chat"] == 0 for m in _refused)
       and all(v["health"] >= 1 for v in hb.values()),
       "(SA18) PA2 preflight reads /health mcp_targets.board.url (contract object shape passes); with mcp_targets "
       "present, a missing board, configured=false, url=null, a non-object board or a wrong board url are refused "
       "even if the legacy mcp_url is right (/chat never called): %s" % hb)
    want_exp = {"http://host.docker.internal:18061/mcp|8061|http://host.docker.internal:18061/mcp": True,
                "http://host.docker.internal:18061/mcp|8061| http://host.docker.internal:18061/mcp ": True,
                "http://127.0.0.1:18061/mcp|8061|None": True}
    bad_exp = {k: v for k, v in sa["mcp_table_exp"].items() if v != want_exp.get(k, False)}
    dk = {k: {kk: p2[k].get(kk) for kk in ("status", "kind", "chat")}
          for k in ("docker|expected_eq", "docker|expected_ne", "docker|unset")}
    ck(len(sa["mcp_table_exp"]) == 11 and not bad_exp
       and dk == {"docker|expected_eq": {"status": "done", "kind": None, "chat": 1},
                  "docker|expected_ne": {"status": "failed", "kind": "ai_fail", "chat": 0},
                  "docker|unset": {"status": "failed", "kind": "ai_fail", "chat": 0}},
       "(SA18b) PA2 BOARD_GW_MCP_URL_EXPECTED: a non-loopback MCP target (host.docker.internal) passes only when it "
       "equals the expected value verbatim AND still has path /mcp, an own/+10000 port, no production port, http; "
       "unset keeps the loopback rule (non-loopback refused): table=%s turns=%s" % (bad_exp, dk))
    xl, xd = p2["xff_loopback"], p2["xff_direct"]
    ck(xl["status"] == "done" and xl["client_id"] == xl["want"] == xl["client_key"] and len(xl["want"]) == 32
       and xd["status"] == "done" and xd["client_id"] == xd["want"] == xd["client_key"] and xd["want"] != xl["want"]
       and p2["raw_ip_rows"] == 0,
       "(SA19) PA2 client_id: loopback peer -> last X-Forwarded-For hop (the one our proxy appended); direct peer -> XFF ignored; the HMAC is "
       "stored on the turn and sent as client_id; the raw IP is in no message column: loopback=%s direct=%s raw=%s"
       % ({k: xl.get(k) for k in ("status", "client_id", "want")}, {k: xd.get(k) for k in ("status", "client_id", "want")},
          p2["raw_ip_rows"]))
    td = p2["tier_deep"]
    ck(td["status"] == "done" and td["chat"] == 1 and td["tier"] == "deep",
       "(SA20) PA2 BOARD_ASSIST_TIER_DEEP=1 adds tier=deep to /chat: %s" % {k: td.get(k) for k in ("status", "chat", "tier")})
    pq = {m: {k: p2["post|" + m].get(k) for k in ("status", "kind", "chat", "html_en")}
          for m in ("quota", "budget", "budget_monthly", "unconfigured")}
    ck(pq == {"quota": {"status": "failed", "kind": "quota", "chat": 1, "html_en": [286, True]},
              "budget": {"status": "failed", "kind": "budget", "chat": 1, "html_en": [286, True]},
              "budget_monthly": {"status": "failed", "kind": "budget", "chat": 1, "html_en": [286, True]},
              "unconfigured": {"status": "failed", "kind": "ai_fail", "chat": 1, "html_en": [286, True]}}
       and not sa["popen"],
       "(SA21) PA2 gateway 429 -> quota, 503 daily/monthly budget exhausted -> budget, 503 agent backend not configured -> ai_fail; each lands after exactly one "
       "/chat (no kick, no retry, no wsl spawn): %s popen=%s" % (pq, sa["popen"]))


# ─────────────────────────────────────────────────────────────────────────────
# (I) 整合：合成進程 × WP-3 資料層 × loopback 假 cmms（凍結時鐘）
# ─────────────────────────────────────────────────────────────────────────────
INTEG_URLS = ("/equipment/body", "/", "/landing2/body", "/landing/state", "/utilization?days=7",
              "/availability?days=7", "/equipment", "/equipment/health", "/equipment/health/body", "/station",
              "/station/%s?days=7" % CAL, "/station/%s/overview?days=7" % CAL, "/station/%s/units?days=7" % CAL,
              "/machine/EID-24742?days=7", "/landing2/wo")
#: WP-4b A 包：第一期的整頁（有導覽列 ⇒ 有來源徽章）與片段；逐頁 en／zh 看渲染後的 HTML
WP4B_FULL = ("/", "/utilization?days=7", "/availability?days=7", "/equipment", "/equipment/health", "/station",
             "/station/%s?days=7" % CAL, "/machine/EID-24742?days=7")
WP4B_FRAG = ("/landing2/body", "/landing2/wo", "/equipment/body", "/equipment/health/body",
             "/station/%s/overview?days=7" % CAL, "/station/%s/units?days=7" % CAL)
#: 三個舊頁級 synth 通知（R-A3）已於 WP-P5b 併入全站橫幅：頁 → 舊通知 class（合成下整頁與片段都不得再印）
WP4B_BANNER = {"/": "srcbanner", "/equipment": "eq-srcbanner", "/equipment/body": "eq-srcbanner",
               "/equipment/health": "mh-srcbanner", "/equipment/health/body": "mh-srcbanner"}
#: C-SY11 禁字（R-A9）
WP4B_FORBIDDEN = (r"\bLIVE\b", r"\bREAL\b", r"\breal\b", "真值")

#: (S36b) R-C5 守門員的**共用判定碼**（純函式、只依賴 `re`）。同一段原始碼被兩處使用：①整合子進程（`_INTEG_CHILD`
#:   開頭，掃合成輸出面）②父進程的確定性迴歸斷言（`_rc5_mask_regression`，`exec` 同一份字串）⇒ 測的就是實際跑的碼。
#:   S36b 間歇紅的真因：機台頁（`templates/_timeline.html`、`templates/v2/_timeline.html`）的 `<div class="meta">`
#:   第一格印的是**頁面自己算出的** `segs N`（N＝`m.n_seg`，該機該觀測窗內合成時間軸的段數，隨錨點分鐘漂移）；
#:   N 是四位數以上時偶爾恰等於某個普查段數（禁值），整頁假紅。處理＝釘結構不釘數值：只把「`<div class="meta">`
#:   緊接 `LABEL N ·` 再接 `OBSERVED` 標籤」這個**位置**上的 N 遮成 `<S>` 後再比對；同一個數字出現在任何其他位置
#:   （tooltip、別的 `<div>`、標籤後面接別的字）一律照舊比對、照舊紅，守門本意（R-C1～R-C6）不變。
_RC5_SHARED_SRC = r"""
import re as _re_rc5
def rc5_num_re(num_forms):
    # 數字邊界＝前後不緊貼英數字／底線／千分位／小數點（原 `_num_re` 逐字搬出，語意不變）
    return _re_rc5.compile(r"(?<![0-9A-Za-z_,.])(?:%s)(?![0-9A-Za-z_]|[.,]\d)" % "|".join(
        _re_rc5.escape(x) for x in sorted(num_forms, key=len, reverse=True)))
def rc5_segs_mask_res(label_pairs):
    # label_pairs＝[(segs 標籤, observed 標籤), ...]（已 HTML 跳脫；en／zh 各一對）
    return [_re_rc5.compile(r'(<div class="meta">\s*' + _re_rc5.escape(sg) + r'\s+)\d[\d,]*(?=\s+·\s+' + _re_rc5.escape(ob) + ')')
            for sg, ob in label_pairs]
def rc5_mask_segs(text, mask_res):
    # 回 (遮罩後文字, 遮掉的個數)；遮罩只動 N 的位元，不改其餘任何前後文
    total = 0
    for rx in mask_res:
        text, k = rx.subn(lambda m: m.group(1) + "<S>", text)
        total += k
    return text, total
"""

_INTEG_CHILD = _RC5_SHARED_SRC + r"""
import re
_freeze_clock(ARG["anchor"])
os.chdir(TD)
import wsynth_launch as L
L.boot(out=lambda s: None)
# WP-4b C 包（R-C3）：長停橋常數換成「被讀就記一筆」的絆線（在 import app 之前；app／eqmeeting／dormant 都在
#   呼叫當下以模組屬性取它）。原值只留在本子進程記憶體內，供 R-C5 的日期禁值與負斷言使用。
import board_dormant as _WDM0
_BRIDGE_ORIG = [tuple(r) for r in list.__iter__(_WDM0.DORMANT_BRIDGE)]
BRIDGE_READS = []
class _BridgeTrip(list):
    def _hit(self):
        BRIDGE_READS.append(["%s:%s" % (os.path.basename(f.filename), f.name) for f in traceback.extract_stack()[:-2]
                             if f.filename.startswith(TREE)][-4:])
    def __iter__(self):
        self._hit()
        return list.__iter__(self)
    def __len__(self):
        self._hit()
        return list.__len__(self)
    def __getitem__(self, i):
        self._hit()
        return list.__getitem__(self, i)
_WDM0.DORMANT_BRIDGE = _BridgeTrip(_BRIDGE_ORIG)
drv = sys.modules["pyodbc"]
_c = drv.connect
CALLS = []
def _leak(*a, **k):
    CALLS.append(["%s:%s" % (os.path.basename(f.filename), f.name) for f in traceback.extract_stack()[:-1]
                  if f.filename.startswith(TREE)][-5:])
    return _c(*a, **k)
drv.connect = _leak
app = L.import_app(out=lambda s: None)
R["prebuild_s"] = L.prebuild_world(out=lambda s: None)
import wsynth_io as IO
import board_data as wd
# WP-7 ③（2026-10-02）：`calendar` 模式＝渲染任何頁面**之前**先跑一次 tick 的 calendar 步驟（同一合成時鐘、
#   寫進改向後的狀態目錄）⇒ 下面所有守門員（S27–S40）在「日曆已寫、/equipment 等頁改走應生產時段基準」的
#   狀態下再渲染一次。非 calendar 模式＝既有狀態（日曆缺席）逐位元不變。
if ARG.get("calendar"):
    import wsynth_tick as _K
    _ks = _K.run_tick(_K.parse_steps("calendar"), out=lambda s: None)
    R["cal_tick"] = {"rc": _ks.get("rc"), "results": _ks.get("results")}
R["cal_status"] = wd.load_prod_calendar()[1]
# WP-4b C 包（R-C4）：讀真實常數／普查檔的資料腿在合成第一期**一次都不該被呼叫**（計數，請求全跑完再判）
RC4_CALLS = {}
def _count_calls(mod, name, tag):
    _f = getattr(mod, name, None)
    if _f is None:
        RC4_CALLS[tag] = "missing"
        return
    RC4_CALLS[tag] = 0
    def _w(*a, **k):
        RC4_CALLS[tag] += 1
        return _f(*a, **k)
    setattr(mod, name, _w)
for _nm in ("load_census", "load_inputs", "load_rollup"):
    _count_calls(app._wfc, _nm, "board_forecast." + _nm)
for _nm in ("get_pm_board", "get_pm_item"):
    _count_calls(wd, _nm, "board_data." + _nm)
for _nm in ("_forecast_census", "_forecast_inputs", "_forecast_rollup", "_load_forecast_board", "load_pm",
            "load_pm_item", "_load_manpower_board"):
    if hasattr(app, _nm):
        _count_calls(app, _nm, "app." + _nm)
fleet = sorted(a["asset_id"] for a in IO.world().ctx.fleet)
R["fleet"] = fleet
from fastapi.testclient import TestClient
c = TestClient(app.app, raise_server_exceptions=False)
# (I3) WP-7 ①：**每一個**合成渲染（本子進程經測試客戶端發出的所有請求的回應——en／zh、整頁與片段、所有
#   站頁／機台頁變體、匯出、JSON 端點，以及下面 R-C5 掃到的 MCP 回應）印出的 EID 取聯集；父進程拿它與
#   demo-only 集合交集（原本只掃 `INTEG_URLS` 的英文頁）。只記 EID、不改任何請求。
ALL_EIDS = set()
N_RENDERS = [0]
_c_get0 = c.get
def _c_get(url, *a, **k):
    r = _c_get0(url, *a, **k)
    N_RENDERS[0] += 1
    ALL_EIDS.update(re.findall(r"EID-\d{5}", r.content.decode("utf-8", "replace")
                               + " " + r.headers.get("content-disposition", "")))
    return r
c.get = _c_get
def text_of(h):
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S)
    h = re.sub(r"<!--.*?-->", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
pages, all_eids, bodies = {}, set(), {}
fs = set(fleet)
for u in ARG["urls"]:
    assert "source=live" not in u
    r = c.get(u + ("&" if "?" in u else "?") + "lang=en")
    eids = set(re.findall(r"EID-\d{5}", r.text))
    all_eids |= eids
    bodies[u] = r.text
    pages[u] = [r.status_code, ("Traceback" in r.text) or ("SynthLeakError" in r.text), sorted(eids - fs)[:5]]
R["pages"] = pages
R["all_eids"] = sorted(all_eids)
R["demo_badge"] = [u for u in ("/utilization?days=7", "/availability?days=7") if ">DEMO<" in bodies[u]
                   or " DEMO " in text_of(bodies[u])]
wos = re.findall(r"WO (\d{5,6})(?!\d)", bodies["/equipment/body"])
R["synth_wo"] = len(wos)
R["wo_out_of_range"] = sorted(set(w for w in wos if not (710001 <= int(w) <= 799999)))
m = re.search(r"(\d+)h(\d{2})m of work-order downtime", text_of(bodies["/availability?days=7"]))
R["avail_down_min"] = (int(m.group(1)) * 60 + int(m.group(2))) if m else None
ev, st, _age = wd.get_dormant_events()
R["a11"] = [st, len(ev or [])]
stj = json.loads(bodies["/landing/state"])
R["state_warming"] = stj.get("warming")
R["state_source"] = stj.get("source")
R["units_note"] = "unavailable" if "sec-unavail" in bodies["/station/%s/units?days=7" % ARG["cal"]] else "rendered"
# G1：同一條線（CAL、days=7）在各頁印出的數字
ut = text_of(bodies["/utilization?days=7"])
so = text_of(bodies["/station/%s/overview?days=7" % ARG["cal"]])
home = text_of(bodies["/landing2/body"])
def first(pat, t):
    mm = re.search(pat, t)
    return mm.group(1) if mm else None
g1 = {"utilization": {"A": first(r"Availability \(A\) (\d+%)", ut), "U": first(r"Utilization \(U\) (\d+%)", ut),
                      "fleet_util": first(r"(\d+%) Fleet util", ut)},
      "station_overview": {"A": first(r"Availability · F4 EQUIP (\d+%)", so), "U": first(r"Utilization · F3 PROD (\d+%)", so)},
      "home": {"A": first(r"Availability[^%]{0,40}?(\d+%)", home), "U": first(r"Utilization[^%]{0,40}?(\d+%)", home),
               "note": "synthetic floor plan (SL2-A); capacity/output panes 'not provided' on the synthetic line ⇒ no A/U printed"}}
mism = []
for k in ("A", "U"):
    vals = {pg: g1[pg][k] for pg in ("utilization", "station_overview", "home") if g1[pg][k] is not None}
    if len(set(vals.values())) > 1:
        mism.append({k: vals})
g1["mismatch"] = mism
g1["functions"] = {"utilization": "app.utilization -> au_chips(data_v, wos_by_eid, ...) (work-order hours, /equipment basis)",
                   "station_overview": "app.station_axis_payload -> oee.station_model(u_station, ..., rw=_station_rw_payload)"}
R["g1"] = g1
R["driver_calls"] = CALLS
# ── WP-4b A 包（R-A1～R-A10）：來源標示、導覽列、死連結、C-SY11 字樣 ─────────────────────────────
import html as _html
from markupsafe import escape as _mesc
def _esc(x):
    return str(_mesc(x))
from urllib.parse import urlsplit
I18 = app.I18N
W4 = {"badge": {}, "banner": {}, "forbidden": {}, "status": {}, "synthbar": {}}
wp_pages = {}
def _get(u, lg):
    uu = u + ("&" if "?" in u else "?") + "lang=" + lg
    assert "source=live" not in uu
    r = c.get(uu)
    W4["status"][uu] = r.status_code
    wp_pages[uu] = r.text
    return r.text
for lg in ("en", "zh"):
    lab = I18[lg]["source_label_synth"]
    for u in ARG["wp_full"]:
        h = _get(u, lg)
        m = re.search(r'<span class="board-src ([^"]*)">([^<]*)</span>', h)
        W4["badge"][u + "|" + lg] = [m.group(1), m.group(2)] if m else None
    _bar = '<div class="board-synthbar" role="note">%s</div>' % _esc(I18[lg]["synth_bar"])
    for u in ARG["wp_full"]:   # R-P10：整頁外殼都有全站橫幅
        W4["synthbar"][u + "|" + lg] = _bar in wp_pages[u + ("&" if "?" in u else "?") + "lang=" + lg]
    for u in ARG["wp_frag"]:
        _get(u, lg)
        W4["synthbar"]["frag:" + u + "|" + lg] = "board-synthbar" not in wp_pages[u + ("&" if "?" in u else "?") + "lang=" + lg]
    for u, cls in ARG["wp_banner"].items():
        h = wp_pages[u + ("&" if "?" in u else "?") + "lang=" + lg]
        # WP-P5b：頁級通知不得出現（含 demo／其他源的同 class 牌——合成下它們本就不該渲染）；整頁另須含全站橫幅
        W4["banner"][u + "|" + lg] = (not re.search(r'class="%s[ "]' % re.escape(cls), h)) and (
            u in ARG["wp_frag"] or "board-synthbar" in h)
# 首頁主體：SL2-A（2026-10-02）起合成進程有自己的示意平面圖（`wsynth_layout`，由合成機隊在記憶體內排出、
#   不讀任何檔）⇒ 首頁 body 的合成分支**自然**被渲染；本子進程不再注入版面（原「測試注入格子」已退役）。
#   網址的 `layout=synth` 只是讓這幾個回應在 `wp_pages` 裡有自己的鍵（app 不認得、忽略）。
_lay_nat = app.ld_layout()
W4["home_layout"] = {"ok": bool(_lay_nat.get("ok")), "err": _lay_nat.get("err"),
                     "n_boxes": len(_lay_nat.get("boxes") or ())}
_fl = [b["eid"] for b in (_lay_nat.get("boxes") or ())]
_sel = _fl[0] if _fl else "EID-00000"
W4["disp"] = {}
for lg in ("en", "zh"):
    for u in ("/?layout=synth", "/landing2/body?layout=synth", "/landing2/body?eid=%s&layout=synth" % _sel):
        h = _get(u, lg)
    _get("/landing/state?layout=synth", lg)          # SL2-A ④⑤：輪詢端點也進 S28／S36 掃描面
    body = wp_pages["/landing2/body?layout=synth&lang=" + lg]
    secs = [s for s in body.split('<section class="board-panel') if '>%s</h2>' % _esc(I18[lg]["ld_q4_head"]) in s]
    sec = secs[0] if secs else ""
    W4["disp"][lg] = {"found": bool(sec), "n_sections": len(secs),
                      "warming": _esc(I18[lg]["ld_q4_warming"]) in sec,
                      "synth_na": _esc(I18[lg]["ld_q4_synth_na"]) in sec,
                      "ld_err_card": 'class="errcard"' in body}
# (S35) WP-4b R-B6：首頁本體（注入平面圖＋選一台機）其餘「永遠暖機中」句在合成下改印「合成產線未提供」
_warm_keys = ("ld_m_why_disp_warming", "ld_m_why_period_warm")
S35 = {"hits": {}, "card": {}, "fn": {}, "tip": {}}
for lg in ("en", "zh"):
    T = I18[lg]
    _chipw = T["ld_kun_chip_warming"].split("{")[0]
    for u in ("/?layout=synth", "/landing2/body?layout=synth", "/landing2/body?eid=%s&layout=synth" % _sel):
        h = wp_pages[u + "&lang=" + lg]
        bad = [k for k in _warm_keys if _esc(T[k]) in h]
        if _esc(_chipw) in h:
            bad.append("ld_kun_chip_warming")
        if _esc(T["ld_q4_tip"]) in h:
            bad.append("ld_q4_tip")
        if lg == "zh" and "暖機" in text_of(h):
            bad.append("暖機@" + text_of(h)[max(0, text_of(h).find("暖機") - 40):text_of(h).find("暖機") + 20])
        if bad:
            S35["hits"][u + "|" + lg] = bad
        if u.startswith("/landing2/body"):     # 調度窗在 body 片段裡（外殼頁 `/` 以 hx 載入 body、不含調度窗）
            S35["tip"][u + "|" + lg] = _esc(T["ld_q4_tip_synth"]) in h
    h = wp_pages["/landing2/body?eid=%s&layout=synth&lang=%s" % (_sel, lg)]
    m1 = re.search(r'data-ldf="recent">([^<]*)<', h)
    m2 = re.search(r'data-ldf="stationmonth">([^<]*)<', h)
    S35["card"][lg] = [m1.group(1) if m1 else None, m2.group(1) if m2 else None]
    _na = T["ld_q4_synth_na"]
    S35["fn"][lg] = [app._ld_recent_txt("EID-00000", {}, False, T) == _na,
                     app._ld_station_month_txt(None, T, False) == _na,
                     app._ld_kun_operator_text("k", T, {})["chip_title"] == _na,
                     app._ld_kun_operator_text("k", T, {"reason": "x"})["chip_title"] == "x"]
_stj = c.get("/landing/state?lang=zh").json()
_ko = _stj.get("kun_operators") or {}
S35["state_kun"] = sorted(set(str(v.get("chip_title")) for v in _ko.values() if isinstance(v, dict)))
S35["state_warm"] = [k for k in ("recent_txt", "station_month_txt") for mm in (_stj.get("machines") or [])
                     if isinstance(mm, dict) and "暖機" in str(mm.get(k) or "")][:3]
R["s35"] = S35
# (S28b) WP-7 ②：長停排除揭露列 tooltip 的合成專用鍵（R-A9 同法）——鍵在、en／zh 零禁字；渲染出來的次數另記
W4["dorm_tip"] = {lg: ([] if "eq_dorm_drop_tip_synth" in I18[lg] else ["missing"])
                      + [p for p in ARG["forbidden"] if re.search(p, I18[lg].get("eq_dorm_drop_tip_synth", ""))]
                  for lg in ("en", "zh")}
W4["dorm_tip_rendered"] = sum(h.count(_esc(I18[lg]["eq_dorm_drop_tip_synth"])) for h in wp_pages.values()
                              for lg in ("en", "zh") if "eq_dorm_drop_tip_synth" in I18[lg])
# (S28) 禁字
for uu, h in wp_pages.items():
    hits = []
    for pat in ARG["forbidden"]:
        for mm in re.finditer(pat, h):
            hits.append(h[max(0, mm.start() - 60):mm.end() + 30])
    if hits:
        W4["forbidden"][uu] = hits[:3]
# (S29) 站內連結逐一請求
links = {}
for uu, h in wp_pages.items():
    base_path = urlsplit(uu).path
    for mm in re.finditer(r'\b(?:href|hx-get|hx-post)="([^"]*)"', h):
        v = _html.unescape(mm.group(1)).strip()
        if not v or v.startswith("#") or v.startswith("//") or "://" in v or v.startswith("mailto:") or v.startswith("javascript:"):
            continue
        if v.startswith("?"):
            v = base_path + v
        if not v.startswith("/") or v.startswith("/static/"):
            continue
        links.setdefault(v, uu)
W4["n_links"] = len(links)
W4["link_bad"], W4["link_out"], W4["link_live"] = {}, [], []
for v, src in sorted(links.items()):
    p = urlsplit(v).path
    if not app._synth_path_allowed(p):
        W4["link_out"].append([v, src])
        continue
    if "source=live" in v:
        W4["link_live"].append([v, src])
        continue
    r = c.get(v)
    if r.status_code == 404 or r.status_code >= 500:
        W4["link_bad"][v] = [r.status_code, src]
R["wp4b"] = W4
# ── R-A2：六處「字面 "live" 在合成下為假」的行為負斷言（不加分支；只釘結果）────────────────
A2 = {}
# A2-1 diag_live_hint：即使設了 Diag 腳本路徑，合成 /equipment 的 Diag 列仍是「未接通」且不載入 board_diag
os.environ["BOARD_Diag_SCRIPT"] = os.path.join(TD, "no_such_diagnose.py")
try:
    h = c.get("/equipment?lang=en").text
finally:
    os.environ.pop("BOARD_Diag_SCRIPT", None)
A2["diag"] = [('<td>%s</td><td><span class="eq-stag">%s</span>' % (_esc(I18["en"]["eq_dx_srcrow"]),
                                                              _esc(I18["en"]["eq_st_pend"]))) in h,
            "board_diag" in sys.modules]
# A2-2 _station_rework：合成源回 (None, None)（live-only）
_now = _d.datetime.now()
A2["rework"] = list(app._station_rework("synth", ARG["cal"], _now - _d.timedelta(days=7), _now))
# A2-3／A2-4 station_units：合成下不走「CAL live 由 fleet 直算機台良率」，timeline 失敗也不走 live 快速失敗支
_calls = {"fleet_yield": 0, "yield_station": 0}
_o_fy, _o_ys, _o_load = wd.get_yield_machines_from_fleet, app.load_yield_station, app.load
def _fy(*a, **k):
    _calls["fleet_yield"] += 1
    return _o_fy(*a, **k)
def _ys(*a, **k):
    _calls["yield_station"] += 1
    return _o_ys(*a, **k)
wd.get_yield_machines_from_fleet, app.load_yield_station = _fy, _ys
try:
    r1 = c.get("/station/%s/units?days=7&lang=en" % ARG["cal"])
    n_fy_ok, n_ys_ok = _calls["fleet_yield"], _calls["yield_station"]
    def _boom(*a, **k):
        raise RuntimeError("selftest: timeline forced to fail")
    app.load = _boom
    r2 = c.get("/station/%s/units?days=6&lang=en" % ARG["cal"])
finally:
    wd.get_yield_machines_from_fleet, app.load_yield_station, app.load = _o_fy, _o_ys, _o_load
A2["units"] = {"ok_status": r1.status_code, "ok_fleet_yield": n_fy_ok,
               "fail_status": r2.status_code, "fail_fleet_yield": _calls["fleet_yield"] - n_fy_ok,
               "fail_yield_station": _calls["yield_station"] - n_ys_ok,
               "exc_text": ("Traceback" in r1.text) or ("Traceback" in r2.text)}
# A2-5／A2-6 _ld_warm_opr_kun：合成鍵即使快取裡有一份 ok 的也不回（第一處）；一律回 live_only 降級（第二處）
app._OPR_KUN_CACHE["synth"] = (time.monotonic(), {"ok": True, "selftest_seed": 1})
p1 = app._ld_warm_opr_kun("synth")
p2 = app._ld_warm_opr_kun("synth")
A2["opr"] = {"seed_returned": bool(p1.get("selftest_seed")), "ok": [p1.get("ok"), p2.get("ok")],
             "reason": [str(p1.get("unavailable") or p1.get("reason") or ""), str(p2.get("unavailable") or p2.get("reason") or "")],
             "mes_operator_loaded": "mes_operator" in sys.modules}
# A2-7 load_dispatch：合成下本次重算成功也不寫 WIP 日帳、不預建綑板
_seen = {"append": 0, "kun": 0}
_o_bd, _o_ap, _o_kb, _o_ok = app._build_dispatch, app._append_wip_snapshot, app._build_kun_from_dispatch, wd.wip_ok
def _bd(src, sm):
    return ({"n_rows": 0, "n_need": 0, "n_lend": 0, "n_down": 0, "skipped_n": 0, "wo_gate": "selftest"},
            {"selftest": True})
def _ap(*a, **k):
    _seen["append"] += 1
def _kb(*a, **k):
    _seen["kun"] += 1
    return None, {}
app._build_dispatch, app._append_wip_snapshot, app._build_kun_from_dispatch = _bd, _ap, _kb
wd.wip_ok = lambda w: True
try:
    for _k in [k for k in list(app._DISP_CACHE) if k and k[0] == "synth"]:
        app._DISP_CACHE.pop(_k, None)
    _bw = app.load_dispatch("synth", 15)
finally:
    app._build_dispatch, app._append_wip_snapshot, app._build_kun_from_dispatch = _o_bd, _o_ap, _o_kb
    wd.wip_ok = _o_ok
A2["dispatch"] = {"board": _bw[0] is not None, "append": _seen["append"], "kun": _seen["kun"]}
R["a2"] = A2
# ── WP-4b B 包（R-B1～R-B6）───────────────────────────────────────────────────────────────
B = {}
import board_mcp as WM
# (S30) /station/{cls}/units：名冊骨架（一般站 CAL 含 Gen2 取代父機；CLN 含保留父機的 Gen2 母機）
_cells_re = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
B["units"] = {}
for cls in ARG["units_cls"]:
    r = c.get("/station/%s/units?days=7&lang=en" % cls)
    h = r.text
    tl = app.load("synth", 7, 15, "", "", cls)
    tl, _ = app.apply_roster_fill("synth", 7, 15, "", "", cls, tl)
    exp = []                                   # 測試自己的預期（獨立寫法，不呼叫被測的共用函式）
    for m in tl.get("machines") or []:
        e = m["eid"]
        pos = wd.GEN2_BAY_POSITIONS.get(e)
        if pos:
            if wd.MODULE_COVERAGE_NOTES.get(e):
                exp.append(e)
            exp.extend(x for x, _d, _r in pos)
        else:
            exp.append(e)
    rows, bad_cells, n_a, n_u = [], [], 0, 0
    tb = h.split("<tbody>", 1)[1].split("</tbody>", 1)[0] if "<tbody>" in h else ""
    for tr in re.findall(r"<tr(?![^>]*data-seat)[^>]*>(.*?)</tr>", tb, re.S):
        cells = _cells_re.findall(tr)
        if len(cells) != 8:
            continue
        mm = re.search(r"(EID-\d{5})", cells[0])
        rows.append(mm.group(1) if mm else None)
        for i in (1, 2, 6, 7):                 # 產出／良品／品質／OEE
            if re.sub(r"<[^>]+>", "", cells[i]).strip() != "—":
                bad_cells.append([rows[-1], i, cells[i][:60]])
        n_a += "%" in cells[4]
        n_u += "%" in cells[5]
    B["units"][cls] = {"status": r.status_code, "unavail": "sec-unavail" in h, "rows": rows, "exp": exp,
                       "bad_cells": bad_cells[:5], "n_avail": n_a, "n_util": n_u,
                       "note": _esc(I18["en"]["sx_yield_synth_na"]) in h,
                       "has_gen2": any(e in wd.GEN2_BAY_POSITIONS for e in exp) or
                                   any(e in wd._BAY_PARENT for e in exp)}
# (S32) MCP：合成揭露＋退階語意；回答來源的部分不含禁字
B["mcp"] = {}
for lg in ("en", "zh"):
    em = WM.explain_metric("util.availability", lg)
    src_part = json.dumps({"synthetic_line": em.get("synthetic_line"), "note": em.get("synthetic_line_note"),
                           "provenance": em.get("provenance"), "freshness": em.get("freshness")},
                          ensure_ascii=False)
    B["mcp"][lg] = {"disc": (em.get("synthetic_line") or {}).get("source"),
                    "note_ok": em.get("synthetic_line_note") == __import__("board_metrics").SYNTH_NOTE[lg],
                    "forbidden": [p for p in ARG["forbidden"] if re.search(p, src_part)],
                    "prov_fresh": (em.get("provenance") or {}).get("freshness")}
B["mcp_other"] = {n: ((f() or {}).get("synthetic_line") or {}).get("source")
                  for n, f in (("ping", WM.ping), ("list_metrics", lambda: WM.list_metrics("/utilization")))}
B["mcp_disc_forbidden"] = [p for p in ARG["forbidden"] if re.search(p, json.dumps(WM.ping().get("synthetic_line")))]
# (S33) 設備健康頁：世界不足 180 天 ⇒ 掛 chip；把世界起日假設成足夠早 ⇒ 不掛
B["health"] = {}
_hs = IO.history_start
for lg in ("en", "zh"):
    chip_head = _esc(I18[lg]["mh_synth_prev_short"].split("{")[0])
    h1 = c.get("/equipment/health/body?lang=" + lg).text
    try:
        IO.history_start = lambda: _d.date(2000, 1, 1)
        h2 = c.get("/equipment/health/body?lang=" + lg).text
    finally:
        IO.history_start = _hs
    B["health"][lg] = {"short_chip": chip_head in h1, "start_in_chip": str(_hs()) in h1,
                       "long_chip": chip_head in h2}
B["hist_start"] = str(_hs())
# (S34) 合成匯出浮水印
import board_export as WX
B["export"] = {}
for u in ("/equipment/export.csv?lang=en", "/equipment/health/export.csv?section=machines&lang=en",
          "/equipment/health/export.csv?section=pareto&lang=zh"):
    r = c.get(u)
    txt = r.content.decode("utf-8-sig", "replace")
    lines = txt.splitlines()
    srcl = [ln for ln in lines[:6] if ln.startswith("# source") or ln.startswith("# 源") or WX._SYNTH_WATERMARK in ln]
    B["export"][u] = {"status": r.status_code, "mark": any(WX._SYNTH_WATERMARK in ln for ln in lines[:6]),
                      "fn": r.headers.get("content-disposition", ""),
                      "forbidden": [p for p in ARG["forbidden"] if re.search(p, WX._SYNTH_WATERMARK)],
                      "srcline": srcl[:1]}
R["b"] = B
# ── WP-4b C 包（R-C5，2026-09-30）：合成輸出面不得含真實量測值（守門員）────────────────────────────
#   禁值集合**只在本子進程記憶體內**導出（`board_tags.PRODUCT_TAGS` 的 families／lines 段數，取四位數
#   以上、含千分位與不含千分位兩種寫法；`first_seen`／`last_seen` 與 `DORMANT_BRIDGE` 各欄裡的日期），
#   **不寫檔、不印值**：回傳只有頁面、命中數、以及把命中值遮成 `<N>`／`<D>` 的前後文。
import board_tags as _WT
_vals = set()
for _rec in _WT.PRODUCT_TAGS.values():
    for _v in (_rec.get("families") or {}).values():
        _vals.add(int(_v))
    for _ln in _rec.get("lines") or ():
        _vals.add(int(_ln[2]))
_vals = sorted(v for v in _vals if v >= 1000)
_num_forms = set()
for _v in _vals:
    _num_forms.add(str(_v))
    _num_forms.add("{:,}".format(_v))
_dates = set()
for _rec in _WT.PRODUCT_TAGS.values():
    for _k in ("first_seen", "last_seen"):
        if _rec.get(_k):
            _dates.add(str(_rec[_k])[:10])
for _row in _BRIDGE_ORIG:
    for _cell in _row:
        _dates.update(re.findall(r"\d{4}-\d{2}-\d{2}", str(_cell)))
# 即時時鐘導出的日期（觀測窗起點、日期輸入框值：錨點日 − 預設窗天數）不是普查值：每逢「今天 − N 天」恰等於某個普查日
#   的那一天（例：2026-10-01 的 30 天窗起點 2026-09-01）整天假紅（只比 ISO 全形、命中的是時鐘字樣）。
#   ⇒ 把錨點日 − {0,1,7,14,30,60,90} 天從禁值集合剔除、剔除個數另列於輸出（`n_clock_excl`）；其餘日期仍逐字比對。
import datetime as _dtm
_clock_days = set((_now.date() - _dtm.timedelta(days=_k)).isoformat() for _k in (0, 1, 7, 14, 30, 60, 90))
_n_clock_excl = len(_dates & _clock_days)
_dates -= _clock_days
# 數字：一個「數」的邊界＝前後不緊貼英數字／底線／千分位／小數點（頁上印的段數是獨立的數；雜湊、識別碼
#   裡剛好出現同一串數字不是「印出統計」——例：MCP `evidence_ref` 的十六進位雜湊每次都不同，不設這條邊界
#   守門員會隨機紅）；日期：ISO 形、前後不緊貼數字。**日期不設任何例外規則**：巧合命中一律逐條列在
#   `RC5_KNOWN_COINCIDENCES`（頁面＋遮罩後前後文＋理由），不放寬比對。
_num_re = rc5_num_re(_num_forms)
_segs_mask_res = rc5_segs_mask_res([(_esc(I18[_lg]["segs"]), _esc(I18[_lg]["observed"])) for _lg in ("en", "zh")])
_date_re = re.compile(r"(?<!\d)(?:%s)(?!\d)" % "|".join(re.escape(x) for x in sorted(_dates)))
RC5 = {"n_values": len(_vals), "n_forms": len(_num_forms), "n_dates": len(_dates), "hits": {}, "n_scanned": 0,
       "n_clock_excl": _n_clock_excl, "errors": {}, "cov": {}, "bridge_txt": {}, "tagtip": {}, "n_chips": 0,
       "n_segs_masked": 0, "n_segs_masked_census": 0}
# R-C1：既有 chip tooltip 的普查段（「MES 730 天觀察：…」）前綴；合成下一個都不得出現（含「observed: 」殘句）
_tagtip_txt = sorted(set(I18[lg][k].split("{families}")[0].split("·")[-1].strip()
                         for lg in ("en", "zh") for k in ("tag_tip_observed", "tag_tip_recent", "tag_tip_idle",
                                                          "tag_tip_unknown")))
# R-C2：覆蓋率註記（真實普查的模組級打標覆蓋率）整句；R-C3：橋常數的原因／出處字樣＋「Board 橋常數」來源句
_cov_txt = [x for lg in ("en", "zh") for x in (I18[lg]["cln_cov_note"], _esc(I18[lg]["cln_cov_note"]))]
_br_txt = sorted(set([str(x) for r in _BRIDGE_ORIG for x in r[3:6] if str(x).strip()]
                     + [str(_esc(x)) for r in _BRIDGE_ORIG for x in r[3:6] if str(x).strip()]
                     + [I18[lg]["a11_src_bridge"] for lg in ("en", "zh")]
                     + [_esc(I18[lg]["a11_src_bridge"]) for lg in ("en", "zh")]))
def _rc5_scan(key, text):
    RC5["n_scanned"] += 1
    ALL_EIDS.update(re.findall(r"EID-\d{5}", text))     # (I3) WP-7：MCP 回應也算合成輸出面
    _nc = sum(text.count(x) for x in _cov_txt)
    if _nc:
        RC5["cov"][key] = _nc
    _nb = sum(text.count(x) for x in _br_txt)
    if _nb:
        RC5["bridge_txt"][key] = _nb
    _nt = sum(text.count(x) for x in _tagtip_txt)
    if _nt:
        RC5["tagtip"][key] = _nt
    RC5["n_chips"] += text.count('class="ptag ')
    _n0 = len(_num_re.findall(text))
    text, _nm = rc5_mask_segs(text, _segs_mask_res)   # (S36b) 機台頁自己算的 `segs N` 位置（結構）不比數值
    RC5["n_segs_masked"] += _nm
    RC5["n_segs_masked_census"] += _n0 - len(_num_re.findall(text))   # 其中「若不遮就會紅」的個數（診斷用）
    nh = [m for m in _num_re.finditer(text)]
    dh = [m for m in _date_re.finditer(text)]
    if not (nh or dh):
        return
    def _ctx(m, tag):
        # 前後文裡的其他數字一律遮成 `<#>`（截斷在視窗邊緣的禁值也不會以片段形式印出）
        return (re.sub(r"\d{3,}", "<#>", text[max(0, m.start() - 70):m.start()]) + tag
                + re.sub(r"\d{3,}", "<#>", text[m.end():m.end() + 40]))
    RC5["hits"][key] = {"num": len(nh), "date": len(dh),
                        "ctx": [_ctx(m, "<N>") for m in nh[:3]] + [_ctx(m, "<D>") for m in dh[:3]],
                        "dctx": [_ctx(m, "<D>") for m in dh[:40]]}
# ① 已渲染的第一期整頁與片段（含注入平面圖的首頁 body）
for _u, _h in wp_pages.items():
    _rc5_scan(_u, _h)
# ② 各站頁（整頁、overview、units）＋每台合成機的 /machine 頁（en／zh）＋匯出＋JSON 端點
_st_classes = sorted(set(str(a.get("station_cls")) for a in IO.world().ctx.fleet if a.get("station_cls")))
RC5["n_stations"] = len(_st_classes)
_more = []
for _cls in _st_classes:
    _more += ["/station/%s?days=7" % _cls, "/station/%s/overview?days=7" % _cls, "/station/%s/units?days=7" % _cls]
_more += ["/machine/%s?days=7" % _e for _e in fleet if re.fullmatch(r"EID-\d{5}", _e)]
_more += ["/equipment/export.csv", "/equipment/health/export.csv?section=machines",
          "/equipment/health/export.csv?section=pareto", "/landing/state", "/api/scoreboard",
          "/equipment?days=30", "/equipment/body?days=30", "/utilization?days=30", "/availability?days=30",
          "/equipment/health?days=180", "/equipment/health/body?days=180"]
#   WP-7 ①：機台頁的其餘支援變體——妥善率下鑽身分（`page=availability`，整頁）與 htmx 片段身分
#   （`HX-Request: true` ⇒ `_timeline.html`；稼動與妥善率兩種），每台合成機各一次（en／zh）。
_more += ["/machine/%s?days=7&page=availability" % _e for _e in fleet if re.fullmatch(r"EID-\d{5}", _e)]
_more_hx = []
for _e in fleet:
    if re.fullmatch(r"EID-\d{5}", _e):
        _more_hx += ["/machine/%s?days=7" % _e, "/machine/%s?days=7&page=availability" % _e]
RC5["n_hx"] = 0
for _lg in ("en", "zh"):
    for _u, _hdr in [(x, None) for x in _more] + [(x, {"HX-Request": "true"}) for x in _more_hx]:
        _uu = _u + ("&" if "?" in _u else "?") + "lang=" + _lg
        assert "source=live" not in _uu
        try:
            _r = c.get(_uu, headers=_hdr) if _hdr else c.get(_uu)
            if _hdr:
                RC5["n_hx"] += 1
                if _r.status_code != 200:
                    RC5["errors"]["hx:" + _uu] = _r.status_code
            _rc5_scan(("hx:" if _hdr else "") + _uu,
                      _r.content.decode("utf-8", "replace") + " " + _r.headers.get("content-disposition", ""))
        except Exception as _e2:
            RC5["errors"][_uu] = type(_e2).__name__
# ③ MCP 回應（第一期可用的工具；JSON 全文）
import board_mcp as _WM
_calls = [("fleet_scoreboard", lambda: _WM.fleet_scoreboard(7)), ("updown_now", lambda: _WM.updown_now()),
          ("equipment_downtime", lambda: _WM.equipment_downtime(None, 7)), ("list_stations", _WM.list_stations)]
for _cls in _st_classes:
    _calls += [("station_overview:" + _cls, lambda _c=_cls: _WM.station_overview(_c, 7)),
               ("station_units:" + _cls, lambda _c=_cls: _WM.station_units(_c, 7))]
for _e in fleet:
    if re.fullmatch(r"EID-\d{5}", _e):
        _calls += [("machine_lookup:" + _e, lambda _x=_e: _WM.machine_lookup(_x)),
                   ("updown_now:" + _e, lambda _x=_e: _WM.updown_now(_x))]
for _nm, _fn in _calls:
    try:
        _rc5_scan("mcp:" + _nm, json.dumps(_fn(), ensure_ascii=False, default=str))
    except Exception as _e2:
        RC5["errors"]["mcp:" + _nm] = type(_e2).__name__
# ④ R-C3：強制長停事實取回失敗（假 cmms 那條腿回失敗、清掉相關快取）後重畫會用到長停投影的頁面與 MCP——
#   合成下不得退回橋常數（日期、原因、「Board 橋常數」來源句都不得出現），來源句改說「合成工單源不可達」。
_o_fetch = wd._a11_fetch_all
_CLEAR = ("_CACHE", "_EQM_CACHE", "_EQM_MES_CACHE", "_SCOPE_EV_CACHE", "_WOW_CACHE", "_UTIL_WOW_CACHE",
          "_MAINT_CACHE", "_MAINT_WOW_CACHE", "_SEAT_CACHE", "_UD_CACHE", "_FBC_CACHE", "_YS_CACHE")
def _clear_caches():
    for _nm in _CLEAR:
        _d0 = getattr(app, _nm, None)
        if isinstance(_d0, dict):
            _d0.clear()
    wd._A11_EVENTS, wd._A11_FAIL_TS = None, None
_n_reads_before = len(BRIDGE_READS)
wd._a11_fetch_all = lambda *a, **k: (None, "selftest_forced_failure")
_clear_caches()
RC5["forced"] = {"status": wd.get_dormant_events()[1]}
_clear_caches()
_fu = ["/equipment/body?days=7", "/equipment?days=7", "/utilization?days=7", "/availability?days=7",
       "/station/%s?days=7" % ARG["cal"], "/station/%s/overview?days=7" % ARG["cal"],
       "/station/%s/units?days=7" % ARG["cal"], "/landing2/body", "/landing/state", "/equipment/export.csv"]
_fu += ["/machine/%s?days=7" % r[0] for r in _BRIDGE_ORIG]
try:
    for _lg in ("en", "zh"):
        for _u in _fu:
            _uu = _u + ("&" if "?" in _u else "?") + "lang=" + _lg
            try:
                _r = c.get(_uu)
                _t = _r.content.decode("utf-8", "replace")
                _rc5_scan("a11-fail:" + _uu, _t)
                if _u.startswith("/equipment/body"):
                    RC5["forced"]["note_" + _lg] = _esc(I18[_lg].get("a11_src_synth_na", "@@no-such-key@@")) in _t
            except Exception as _e2:
                RC5["errors"]["a11-fail:" + _uu] = type(_e2).__name__
    try:
        _rc5_scan("a11-fail:mcp:updown_now", json.dumps(_WM.updown_now(), ensure_ascii=False, default=str))
    except Exception as _e2:
        RC5["errors"]["a11-fail:mcp:updown_now"] = type(_e2).__name__
finally:
    wd._a11_fetch_all = _o_fetch
    _clear_caches()
RC5["forced"]["reads_during"] = len(BRIDGE_READS) - _n_reads_before
RC5["bridge_in_fleet"] = sum(1 for r in _BRIDGE_ORIG if r[0] in set(fleet))
RC5["bridge_reads"] = len(BRIDGE_READS)
RC5["bridge_read_sites"] = sorted(set(" > ".join(x) for x in BRIDGE_READS))[:12]
RC5["tagtip_prefixes"] = len(_tagtip_txt)
R["rc5"] = RC5
R["all_eids_every"] = sorted(ALL_EIDS)
R["n_renders"] = N_RENDERS[0]
R["rc4"] = {"calls": RC4_CALLS}
"""

#: WP-4b B 包 (S30b)：Gen2 展開共用函式抽出前後逐位元等價（兩棵樹、非合成子進程、fetch/build 以假函式替代）
_EXPAND_CHILD = r'''
_stub_pyodbc()
os.chdir(TREE)
import board_data as wd
FLEETS = [[], [["EID-00884", "a"]], [["EID-24742", "gen2"]], [["EID-16077", "di"], ["EID-27600", "sf"]],
          [["EID-00950", "x"], ["EID-24742", "g"], ["EID-01066", "cln4"], ["EID-16077", "di"]],
          [["EID-24742", "g"], ["EID-24742", "dup"]]]
R["demo"] = [[list(t) for t in wd._demo_expand_fleet([tuple(f) for f in fl])] for fl in FLEETS]
R["demo_real"] = [list(t) for t in wd._demo_expand_fleet([(e, d) for e, d, _du in wd.DEMO_CAL_FLEET])] + \
                 [list(t) for t in wd._demo_expand_fleet([(e, d) for e, d, _du in wd.DEMO_TL_FLEETS[wd.CLN_CLASS]])]
calls = []
def _fy(cur, eids, days, cls):
    calls.append([list(eids), days, cls])
    if ARG.get("boom") and any(e in ARG["boom"] for e in eids):
        raise RuntimeError("selftest boom")
    return {e: {"n": i} for i, e in enumerate(eids)}
def _bmr(fleet, myield):
    if ARG.get("empty") and any(e in ARG["empty"] for e, _ in fleet):
        return []
    return [{"eid": e, "desc": d, "output_units": (myield.get(e) or {}).get("n", 0)} for e, d in fleet]
wd.my.fetch_machine_yield, wd.my.build_machine_records = _fy, _bmr
out = []
for fl in FLEETS + [[["EID-24742", "g"], ["EID-27600", "sf"]]]:
    for mode in ("ok", "boom", "empty"):
        ARG["boom"] = ["EID-26820", "EID-27661"] if mode == "boom" else None
        ARG["empty"] = ["EID-24790", "EID-27639"] if mode == "empty" else None
        rec = {"cls": "c", "machines": [{"eid": e, "desc": d, "output_units": 7} for e, d in fl]}
        wd._expand_gen2_yield_live("CUR", "segX", 7, rec)
        out.append(rec["machines"])
wd._expand_gen2_yield_live("CUR", "segX", 7, None)
wd._expand_gen2_yield_live("CUR", "segX", 7, {"machines": []})
R["live"] = out
R["calls"] = calls
'''

_DEMO_EID_CHILD = r"""
import re
_stub_pyodbc()
os.chdir(TREE)
import app as A
from fastapi.testclient import TestClient
c = TestClient(A.app, raise_server_exceptions=False)
eids = set()
for u in ARG["urls"]:
    r = c.get(u + ("&" if "?" in u else "?") + "lang=en&source=demo")
    eids |= set(re.findall(r"EID-\d{5}", r.text))
R["eids"] = sorted(eids)
# (I3) 正向對照組的樣本改取 demo 名冊（WP-6b；R-C6 後頁面文字已無 demo-only EID）：demo 源每站的機台名冊
#   ＝`board_data._demo_yield_station(cls)`（/station 單機表的同一支，`mt.EQP_STATIONS` 全站），不靠頁面文字。
import mes_timeline as _mt
import board_data as _wd
roster = set()
for _cls in _mt.EQP_STATIONS:
    for _m in (_wd._demo_yield_station(_cls)["station"].get("machines") or ()):
        if _m.get("eid"):
            roster.add(_m["eid"])
R["roster"] = sorted(roster)
"""


def _integrated(calendar=False):
    """(I) 整合子進程。`calendar=True`（WP-7 ③）＝渲染前先跑 tick 的 calendar 步驟，其餘逐行同。"""
    sys.path.insert(0, REPO)
    import wsynth_cmms as WC                       # 自測父進程內起 loopback 假 cmms（只綁 127.0.0.1）
    anchor = datetime.datetime.now().replace(second=0, microsecond=0)
    td = tempfile.mkdtemp(prefix="wp4a_integ_")
    with WC.serve(WC.GeneratorSource(), now_fn=lambda: anchor) as base:
        env = _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": os.path.join(td, "state"),
                         "BOARD_SYNTH_NOW": anchor.isoformat(), "BOARD_CMMS_BASE_URL": base,
                         "BOARD_CMMS_TOKEN": WC.DEFAULT_TOKEN})
        res, _r = _run_child(_INTEG_CHILD, REPO, env, {"urls": list(INTEG_URLS), "anchor": anchor.isoformat(),
                                                       "cal": CAL, "wp_full": list(WP4B_FULL),
                                                       "wp_frag": list(WP4B_FRAG), "wp_banner": WP4B_BANNER,
                                                       "forbidden": list(WP4B_FORBIDDEN),
                                                       "units_cls": [CAL, "segCleanTest"],
                                                       "calendar": bool(calendar)})
    return res


def _demo_marks():
    """(I3) 正向對照組：非合成 demo 子進程的頁面 EID 與 demo 名冊。"""
    sd = tempfile.mkdtemp(prefix="wp4a_demoeid_")
    demo, _r = _run_child(_DEMO_EID_CHILD, REPO,
                          _base_env(dict(_POISON, BOARD_SOURCE="demo", BOARD_STATE_DIR=sd)),
                          {"urls": [u for u in INTEG_URLS if u != "/landing2/wo"]})
    return demo


#: R-C5 日期的**逐條**巧合清單（不是放寬規則）：（遮罩後前後文裡必須出現的片段, 理由）。
#:   只有前後文逐字含該片段的命中才算已說明；其餘日期命中一律 FAIL。
#:   WP-6b（2026-10-02）：唯一一條（`templates/v2/equipment.html` JS 註解的開發日期）已由 WP-RC6 改成 Jinja 註解、
#:   不再送出 ⇒ 死條目刪除；清單現為空（機制保留：日後真有巧合照格式逐條加，不放寬比對）。
RC5_KNOWN_COINCIDENCES = ()


def _rc5_mask_regression(ck):
    """(S36f) 確定性迴歸：人工構造「頁面自己的 segs 值＝某普查值」→ 不假紅；同值出現在別的位置 → 仍紅。
    與時鐘無關（不跑 app、不看錨點分鐘）；用的是子進程實際執行的同一份 `_RC5_SHARED_SRC`。"""
    ns = {}
    exec(_RC5_SHARED_SRC, ns)
    census = {"1234", "1,234", "20000", "20,000"}           # 人工禁值（不是真普查值）
    num_re = ns["rc5_num_re"](census)
    masks = ns["rc5_segs_mask_res"]([("segs", "observed"), ("段", "觀測")])

    def hits(page):
        t, k = ns["rc5_mask_segs"](page, masks)
        return len(num_re.findall(t)), k

    sg = '<div class="meta">\n      segs %s ·\n      observed 1:02:03 · producing 0:10:00'
    zh = '<div class="meta">\n      段 %s ·\n      觀測 1:02:03 · 生產 0:10:00'
    # 結構位置上的 segs 值＝普查值：en／zh、含千分位與不含千分位 ⇒ 不紅，且確實被遮掉一處
    for v in ("1234", "1,234", "20000"):
        for tpl in (sg, zh):
            h, k = hits(tpl % v)
            ck(h == 0 and k == 1, "(S36f) structural segs=%s (census value) is not a leak and is masked once: hits=%d masked=%d"
               % (v, h, k))
    # 真外露仍紅：同值在 tooltip、別的 div、segs 標籤但後接別的字、meta 區第二個數字、遮罩之外的重複出現、良率區 segs
    leaks = {
        "tooltip": '<span title="MES 730d: 1234 segments">x</span>',
        "other-div": '<div class="other">\n      segs 1234 ·\n      observed 1:02:03',
        "label-no-observed": '<div class="meta">\n      segs 1234 ·\n      PH 5',
        "second-number": (sg % "57") + ' · extra 1234',
        "same-value-twice": (sg % "1234") + ' <i>1234</i>',
        "yield-kv": '<div class="ys-kv">good <b>5</b> ·\n    segs 1,234 ·\n    fail <b>2%</b></div>',
    }
    for nm, page in leaks.items():
        h, _k = hits(page)
        ck(h >= 1, "(S36f) real leak still red (%s): hits=%d" % (nm, h))
    # 遮罩只改 N 那一格，其餘前後文不動
    t, k = ns["rc5_mask_segs"](sg % "57" + " tail", masks)
    ck(k == 1 and t.endswith(" tail") and "segs <S> ·" in t, "(S36f) mask rewrites only the N position: %r" % t[-60:])


def _rc5_report(rc5, ck):
    """(S36) R-C5 守門員的判定與輸出（只印頁面、命中數與遮罩後前後文；禁值本身從不離開子進程）。"""
    _rc5_mask_regression(ck)
    num_hits = {k: v for k, v in rc5["hits"].items() if v["num"]}
    date_hits, explained = {}, []
    for k, v in rc5["hits"].items():
        if not v["date"]:
            continue
        unexpl = []
        for cx in v.get("dctx") or []:
            why = next((r for snip, r in RC5_KNOWN_COINCIDENCES if snip in cx), None)
            if why:
                explained.append((k, why))
            else:
                unexpl.append(cx)
        if unexpl or len(v.get("dctx") or []) < v["date"]:
            date_hits[k] = dict(v, unexplained=unexpl)
    for k, why in explained:
        print("[wiring] (S36) date coincidence (explained, not a census leak): %s — %s" % (k, why), flush=True)
    print("[wiring] (S36) forbidden census set: %d values (%d spellings) + %d dates (%d clock-derived dates excluded); "
          "%d outputs scanned (%d station classes); errors=%s" % (rc5["n_values"], rc5["n_forms"], rc5["n_dates"],
                                                                  rc5.get("n_clock_excl", 0), rc5["n_scanned"],
                                                                  rc5.get("n_stations", 0), rc5["errors"]), flush=True)
    for k, v in sorted(rc5["hits"].items()):
        print("[wiring] (S36) hit %s num=%d date=%d ctx=%s" % (k, v["num"], v["date"], v["ctx"]), flush=True)
    ck(rc5["n_values"] > 0 and rc5["n_dates"] > 0 and rc5["n_scanned"] > 100 and not rc5["errors"],
       "(S36a) R-C5 guard armed: forbidden set non-empty, >100 synth outputs scanned, no request errors (%d scanned)"
       % rc5["n_scanned"])
    print("[wiring] (S36b) machine-page `segs N` positions masked (structure pinned, value not compared): %d "
          "(of which would-have-been census collisions: %d)" % (rc5.get("n_segs_masked", 0),
                                                              rc5.get("n_segs_masked_census", 0)), flush=True)
    ck(rc5.get("n_segs_masked", 0) > 0,
       "(S36e) R-C5: the structural `segs N` mask matches on the real machine pages (template drift would make the "
       "S36b flake return silently): %d positions" % rc5.get("n_segs_masked", 0))
    ck(not num_hits, "(S36b) R-C5: zero census segment counts on synth pages/fragments/exports/MCP: %d outputs hit "
       "(%d hits)" % (len(num_hits), sum(v["num"] for v in num_hits.values())))
    ck(not date_hits, "(S36c) R-C5: zero census first/last-seen or long-stop bridge dates on synth outputs: %d outputs "
       "hit (%d hits)" % (len(date_hits), sum(v["date"] for v in date_hits.values())))
    ck(not rc5["cov"], "(S37) R-C2: the module-coverage note (a census coverage figure) is never rendered in synth: %s"
       % rc5["cov"])
    ck(rc5["n_chips"] > 0 and rc5.get("tagtip_prefixes") and not rc5["tagtip"],
       "(S36d) R-C1: product chips still render (%d chips, letters kept) and none carries the census tooltip part "
       "(%d prefixes checked): %s" % (rc5["n_chips"], rc5.get("tagtip_prefixes") or 0, rc5["tagtip"]))
    fo = rc5.get("forced") or {}
    print("[wiring] (S38) long-stop bridge: %d of its machines are in the synthetic fleet; forced A11 failure status=%s; "
          "bridge reads total=%d (during forced failure %s); sites=%s"
          % (rc5.get("bridge_in_fleet", 0), fo.get("status"), rc5.get("bridge_reads", -1), fo.get("reads_during"),
             rc5.get("bridge_read_sites")), flush=True)
    ck(str(fo.get("status") or "").startswith("error:"), "(S38a) R-C3 forced failure took the error path: %s" % fo)
    ck(rc5.get("bridge_reads") == 0, "(S38b) R-C3: DORMANT_BRIDGE never read in the synth process (normal requests "
       "and forced A11 failure): %d reads at %s" % (rc5.get("bridge_reads", -1), rc5.get("bridge_read_sites")))
    ck(not rc5["bridge_txt"], "(S38c) R-C3: no bridge reason/provenance text and no 'Board bridge constants' source line "
       "on synth outputs: %s" % rc5["bridge_txt"])
    ck(fo.get("note_en") is True and fo.get("note_zh") is True,
       "(S38d) R-C3: /equipment body says the synthetic long-stop source is unreachable (en/zh): %s" % fo)


def _rc4_report(rc4, ck):
    """(S39) R-C4：讀真實常數／普查檔的資料腿（成本預估的普查、PM 排程板、人力）在合成第一期零呼叫。"""
    calls = rc4.get("calls") or {}
    # （`board_manpower` 模組本身會被 `board_kundispatch` 載入以取站列結構 `PLOVER_BLOCKS`＝結構、不是量測值；
    #   這裡釘的是人力**資料**讀腿 `_load_manpower_board`。）
    ck(calls and all(v == 0 for v in calls.values()),
       "(S39) R-C4: census/forecast/PM/manpower data legs never called while serving phase 1 in synth: %s" % calls)


def _main_integ_only(a, ck, n, fails, t_all):
    """開發用捷徑（`--only-integ [--calendar]`）：只跑一個整合子進程與它的判定（含 R-C5 守門員）。完整驗收一律跑全檔。"""
    cal = bool(getattr(a, "calendar", False))
    integ = _integrated(calendar=cal)
    _integ_report(integ, ck, "[cal] " if cal else "")
    print("[wiring] --only-integ: %d checks, %d failed, %.0fs" % (n[0], len(fails), time.time() - t_all))
    return 0 if not fails else 1


def _integ_report(integ, ck0, sfx=""):
    """(I…／S27–S40／A2／S30–S39) 整合子進程結果的判定（WP-7 ③ 起抽成函式：日曆缺席與「tick 已寫日曆」兩種狀態
       各判一次；`sfx`＝訊息前綴）。判定與門檻逐行沿用、一個字不放寬。"""

    def ck(cond, msg):
        ck0(cond, sfx + msg)

    for u in INTEG_URLS:
        v = integ["pages"].get(u)
        ck(v is not None and v[0] == 200 and not v[1], "(I1) %s -> 200 without exception text: %s" % (u, v))
    fleet = set(integ["fleet"])
    off_fleet = {u: v[2] for u, v in integ["pages"].items() if v[2]}
    ck(fleet and not off_fleet, "(I2) every EID printed on phase-1 pages belongs to the synthetic fleet (%d assets): %s"
       % (len(fleet), off_fleet))
    ck(integ["demo_badge"] == [], "(I4) no DEMO badge on synth /utilization, /availability: %s" % integ["demo_badge"])
    ck(integ["driver_calls"] == [], "(I5) database driver stub called zero times: %s" % integ["driver_calls"][:3])
    ck(integ["synth_wo"] > 0 and integ["wo_out_of_range"] == [],
       "(I6) /equipment/body shows synthetic work orders (%d refs, all in 710001-799999): off=%s"
       % (integ["synth_wo"], integ["wo_out_of_range"][:5]))
    ck((integ["avail_down_min"] or 0) > 0, "(I7) /availability work-order downtime > 0: %s min" % integ["avail_down_min"])
    ck(integ["a11"][0] == "live" and integ["a11"][1] > 0,
       "(I8) long-stop facts come from the live path (status=%s, n=%s), not the bridge constants" % tuple(integ["a11"]))
    ck(integ["state_warming"] is False and integ["state_source"] == "synth",
       "(I9) /landing/state served from the warmed board (warming=%s, source=%s)"
       % (integ["state_warming"], integ["state_source"]))
    g1 = integ["g1"]
    print("[wiring] %s(I) G1 cross-page CAL days=7 text scrape (frozen clock; diagnostic only — the pinned G1 "
          "verdict is synth_round (G5)): %s" % (sfx, json.dumps(g1, ensure_ascii=False)), flush=True)
    print("[wiring] %s(I) world prebuilt in %.1fs; %d synth WO refs on /equipment/body; station/units=%s; "
          "%d renders" % (sfx, integ["prebuild_s"], integ["synth_wo"], integ["units_note"], integ.get("n_renders", 0)),
          flush=True)

    # WP-4b A 包（R-A1～R-A10）
    w4 = integ["wp4b"]
    bad_st = {u: s for u, s in w4["status"].items() if s != 200}
    ck(not bad_st, "(S27a) every WP-4b page request answers 200 in synth: %s" % bad_st)
    bad_badge = {k: v for k, v in w4["badge"].items()
                 if not (v and v[0] == "synth" and v[1] == ("synthetic line" if k.endswith("|en") else "合成產線"))}
    ck(len(w4["badge"]) == 2 * len(WP4B_FULL) and not bad_badge,
       "(S27b) every phase-1 page (en/zh, %d) carries the synthetic badge (.board-src.synth + source_label_synth): %s"
       % (len(w4["badge"]), bad_badge))
    bad_ban = {k: v for k, v in w4["banner"].items() if not v}
    ck(len(w4["banner"]) == 2 * len(WP4B_BANNER) and not bad_ban,
       "(S27c) WP-P5b: the three page-level synthetic notices (.srcbanner on /, .eq-srcbanner on /equipment, "
       ".mh-srcbanner on /equipment/health; full pages and body fragments, en/zh) are gone; full pages carry the "
       ".board-synthbar instead: %s" % bad_ban)
    bad_bar = {k: v for k, v in w4["synthbar"].items() if not v}
    ck(len(w4["synthbar"]) == 2 * (len(WP4B_FULL) + len(WP4B_FRAG)) and not bad_bar,
       "(S40) R-P10 site-wide synthetic bar: every phase-1 full page (en/zh, %d incl. / and /utilization) carries "
       "the one-line bar; the htmx body fragments (%d, shell-level bar) do not: %s"
       % (2 * len(WP4B_FULL), 2 * len(WP4B_FRAG), bad_bar))
    hl = w4.get("home_layout") or {}
    ck(hl.get("ok") is True and hl.get("n_boxes", 0) > 0,
       "(S31a) SL2-A: the synth home renders its own synthetic floor plan (no layout injection): %s" % hl)
    ck(not w4["forbidden"], "(S28) zero LIVE/REAL/real/真值 on %d phase-1 renders (en/zh, incl. synthetic-floor-plan home "
       "and /landing/state): %s"
       % (len(w4["status"]), {k: v for k, v in list(w4["forbidden"].items())[:4]}))
    _tpl_sel = "t.eq_dorm_drop_tip_synth if is_synth else t.eq_dorm_drop_tip"
    _tpl_ok = []
    for _tp in ("templates/_equipment_body.html", "templates/v2/_equipment_body.html"):
        with open(os.path.join(REPO, _tp), encoding="utf-8") as _f:
            _tpl_ok.append(_f.read().count(_tpl_sel) == 1)
    ck(w4.get("dorm_tip") == {"en": [], "zh": []} and all(_tpl_ok),
       "(S28b) WP-7 R-A9: the long-stop exclusion tooltip has a synth-only key with no LIVE/REAL/real/真值 (en/zh) and "
       "both equipment-body templates (v1/v2) select it under is_synth (rendered %s times on these pages): %s %s"
       % (w4.get("dorm_tip_rendered"), w4.get("dorm_tip"), _tpl_ok))
    ck(w4["n_links"] > 0 and not w4["link_out"] and not w4["link_live"],
       "(S29a) %d in-site links; none points outside phase 1 or carries source=live: out=%s live=%s"
       % (w4["n_links"], w4["link_out"][:6], w4["link_live"][:3]))
    ck(not w4["link_bad"], "(S29b) every in-site link requested: no 404 / 5xx: %s" % dict(list(w4["link_bad"].items())[:6]))
    print("[wiring] %s(WP-4b) %d synth renders scanned (en/zh), %d distinct in-site links requested"
          % (sfx, len(w4["status"]), w4["n_links"]), flush=True)
    dsp = w4["disp"]
    ck(all(dsp[lg]["found"] and not dsp[lg]["ld_err_card"] and not dsp[lg]["warming"] and dsp[lg]["synth_na"]
           for lg in ("en", "zh")),
       "(S31) home dispatch pane (synthetic floor plan) says 'not provided on the synthetic line', never 'warming up': %s" % dsp)
    a2 = integ["a2"]
    ck(a2["diag"] == [True, False], "(A2-1) diag_live_hint: Diag row stays 'not connected' and board_diag is not imported "
       "even with BOARD_Diag_SCRIPT set: %s" % a2["diag"])
    ck(a2["rework"] == [None, None], "(A2-2) _station_rework('synth') -> (None, None): %s" % a2["rework"])
    u = a2["units"]
    ck(u["ok_status"] == 200 and u["ok_fleet_yield"] == 0 and not u["exc_text"],
       "(A2-3) /station/{cls}/units in synth never takes the CAL-live fleet-yield branch: %s" % u)
    ck(u["fail_status"] == 200 and u["fail_fleet_yield"] == 0 and u["fail_yield_station"] >= 1,
       "(A2-4) timeline failure in synth skips the live fast-fail branch (falls to load_yield_station): %s" % u)
    op = a2["opr"]
    ck(op["seed_returned"] is False and op["ok"] == [False, False] and all("live_only" in x for x in op["reason"])
       and op["mes_operator_loaded"] is False,
       "(A2-5) _ld_warm_opr_kun('synth'): never serves a cached payload, always live_only, mes_operator never loaded: %s" % op)
    dp_ = a2["dispatch"]
    ck(dp_ == {"board": True, "append": 0, "kun": 0},
       "(A2-6) load_dispatch('synth'): a successful rebuild writes no WIP ledger line and prebuilds no kun board: %s" % dp_)

    # WP-4b B 包（R-B1～R-B6）
    b = integ["b"]
    for cls, uu in b["units"].items():
        ck(uu["status"] == 200 and not uu["unavail"] and uu["rows"] and uu["rows"] == uu["exp"],
           "(S30a) synth /station/%s/units renders the machine table; rows == timeline roster with Gen2 expansion: "
           "rows=%s exp=%s" % (cls, uu["rows"], uu["exp"]))
        ck(not uu["bad_cells"], "(S30c) %s output/good/quality/OEE cells are all '—' (no 0 printed): %s"
           % (cls, uu["bad_cells"]))
        ck(uu["n_avail"] > 0 and uu["n_util"] > 0 and uu["note"],
           "(S30d) %s availability/utilization computed (%d/%d rows with %%) and the synthetic yield note is shown"
           % (cls, uu["n_avail"], uu["n_util"]))
    ck(all(uu["has_gen2"] for uu in b["units"].values()), "(S30e) both tested stations carry Gen2 units")
    mc = b["mcp"]
    ck(all(mc[lg]["disc"] == "synth" and mc[lg]["note_ok"] and not mc[lg]["forbidden"] for lg in ("en", "zh"))
       and b["mcp_other"] == {"ping": "synth", "list_metrics": "synth"} and not b["mcp_disc_forbidden"],
       "(S32) MCP in synth: disclosure block on every reply, explain_metric carries the synthetic-line note, no "
       "LIVE/REAL/real/真值 in the source part: %s %s" % (mc, b["mcp_other"]))
    hh = b["health"]
    ck(all(hh[lg]["short_chip"] and hh[lg]["start_in_chip"] and not hh[lg]["long_chip"] for lg in ("en", "zh")),
       "(S33) /equipment/health: 'synthetic world shorter than 180 days' chip when the world starts after the prior "
       "window (world starts %s), none when it is long enough: %s" % (b["hist_start"], hh))
    ex = b["export"]
    ck(all(v["status"] == 200 and v["mark"] and "SYNTH_" in v["fn"] and not v["forbidden"] for v in ex.values()),
       "(S34) synth CSV exports carry the synthetic watermark in the meta header and a SYNTH_ file name: %s" % ex)
    s35 = integ["s35"]
    ck(not s35["hits"] and all(s35["tip"].values())
       and all(s35["card"][lg][0] and s35["card"][lg][1] for lg in ("en", "zh"))
       and all(all(v) for v in s35["fn"].values()) and not s35["state_warm"]
       and not any("暖機" in x for x in s35["state_kun"]),
       "(S35) home body (synthetic floor plan, selected machine): no 'warming up' sentence in synth; dispatch tooltip "
       "uses the synthetic variant: %s" % s35)

    # WP-4b C 包（R-C5）：合成輸出面零真實量測值（禁值只在子進程內；這裡只看頁面、命中數、遮罩前後文）
    _rc5_report(integ["rc5"], ck)
    _rc4_report(integ["rc4"], ck)


def _i3_report(ck, demo_marks, fleet, seen_by_src):
    """(I3) demo-only EID 不得出現在任何合成渲染（WP-7 ①：取所有來源的聯集再與 demo-only 交集）。
       demo-only 集合＝（demo 名冊 ∪ demo 頁面印出的 EID）－ 合成機隊（WP-6b：R-C6 之後頁面文字裡的唯一樣本
       消失、N=0 假紅 ⇒ 改由 demo 源每站機台名冊導出；頁面抽取保留為加法來源）。
       `seen_by_src`＝{來源標籤: (EID 清單, 渲染數)}。只印命中的 EID 與來源，不印頁面內容。"""
    demo_roster = set(demo_marks.get("roster") or ())
    demo_only = set((demo_roster | set(demo_marks["eids"])) - set(fleet))
    seen = set()
    hits = {}
    n_r = 0
    for src, (eids, nr) in seen_by_src.items():
        seen |= set(eids)
        n_r += nr or 0
        h = sorted(set(eids) & demo_only)
        if h:
            hits[src] = h[:6]
    ck(demo_roster and demo_only and seen and not (seen & demo_only),
       "(I3) demo-only EIDs exist (%d = demo roster %d + demo page text %d, minus the synthetic fleet; e.g. %s) and "
       "none appears in ANY synth render (union over %d renders from %s: %d distinct EIDs): hits=%s"
       % (len(demo_only), len(demo_roster), len(demo_marks["eids"]), sorted(demo_only)[:3], n_r,
          sorted(seen_by_src), len(seen), hits))


# ─────────────────────────────────────────────────────────────────────────────
# (G) WP-7 synth_round（2026-10-02；SPEC-SYNTHLINE §1.3 G1／WP-7 列）
# ─────────────────────────────────────────────────────────────────────────────
#   放這支而不放 `run_smoke.py` 的理由：合成進程是**進程獨占**（C-SY3；`BOARD_SOURCE=synth` 啟動的進程只服務
#   合成、非合成進程不得載入任何 `wsynth_` 模組＝本檔 (E3)(P4)(N7) 的釘），而 run_smoke 的主進程是 demo 母體
#   （import app 之前就設了毒丸、全程 in-process）。合成輪必須在獨立子進程裡跑（凍結時鐘、改向狀態目錄、
#   loopback 假 cmms），那套樁（`_PRELUDE`／`_run_child`／`wsynth_cmms.serve`）本來就住在這裡；搬一份去
#   run_smoke 就是第二份會漂移的樁。demo 釘不動由本檔 (P) 逐位元比對守。
#
#   本輪釘的事：
#   (G0) 絆線：開機檢查全過、驅動是替身、直呼替身拋 SynthLeakError、整輪（兩種日曆狀態）替身零呼叫。
#   (G2) 確定性：兩個獨立合成子進程（各自的狀態目錄、各自的假 cmms、同一個凍結時鐘）對同一組第一期頁面逐位元同。
#   (G3) 前綴穩定：時鐘晚一天的世界，對「早一天那個時鐘之前」的固定過去窗，/availability 逐機與 /utilization
#        A／U 讀數（分子、分母）逐值同（延長時間軸不改寫過去；產生器層的前綴釘在 wsynth_selftest）。
#        窗＝`g3_window`：早一天時鐘之前**已定案**的過去（扣掉最長晚到再向下取台北 00:00；理由見該函式上方註解）。
#        (G3a) 產生器層：3 天 × 24 整點的時鐘，窗都已定案、舊窗（−1h）至少一次不定案（反向對照）。
#        (G3b) 真頁面：00／04／08／12／16／20 時各一對子進程，同一個已定案窗逐值同（不隨跑測時刻變）。
#   (G4) SYNTHETIC 大牌 en／zh＋全站橫幅＋零 LIVE／REAL／real／真值：日曆缺席與 tick 已寫日曆兩種狀態各一次。
#   (G5) G1 跨頁同尺（凍結時鐘、同窗同參數）：兩種日曆狀態各量一次，逐指標比
#        /utilization（`au_chips`）／/availability（`avail_model`）／站頁頭卡（`station_model`）／
#        /equipment 逐機 A%（同一個窗的 /availability 逐機 A）；量的是**頁面真正用的那份值**（攔截回傳值，不重算），
#        再加畫面印出的百分比。未達成的指標必須逐項落在 `G1_KNOWN_UNMET` 且原因證據在場，否則紅。
#   (G6) R44：形③ 父機的停機＝在役座推導，在合成**座級工單**上成立（兩種日曆狀態）。
#   (G7) 著陸頁真拆分：合成第一期首頁（landing2）不印任何 A／U 數字、/landing/body 不在第一期（404）⇒ N/A。
#   (G8) 「有日曆」狀態真的換了尺：/utilization A chip、站頁頭卡、/equipment 板都在應生產時段軸；無日曆＝都不在。
ROUND_DET_URLS = ("/", "/landing2/body", "/utilization?days=7", "/availability?days=7", "/equipment?days=7",
                  "/equipment/body?days=7", "/equipment/health/body", "/station", "/station/%s?days=7" % CAL,
                  "/station/%s/overview?days=7" % CAL, "/station/%s/units?days=7" % CAL,
                  "/machine/EID-24742?days=7", "/equipment/export.csv", "/api/scoreboard")

#: G1 已知未達成（逐項列出；**不是放寬**）。鍵＝(日曆狀態, 指標)；值＝原因。判定：
#:   · 量到未達成、不在本表 ⇒ 紅（新的跨頁不一致）；
#:   · 量到未達成、在本表、但子進程同時量的「原因證據」不成立 ⇒ 紅（原因換了＝要重新裁）；
#:   · 本表的項目在某一輪達成 ⇒ 印 NOTE（綠；資料相依的項目——如 C-PC12——在某些時鐘下可能剛好不觸發）。
#:   指標：A／U＝比值；down＝A 的分子（工單停機秒）；planned＝A 的分母（應生產時段秒／24h 全時秒）；
#:   prod＝U 的分子（生產秒）；A%／U%＝畫面印出的百分比；A_eq_machine＝/equipment 逐機 A% 對同窗 /availability 逐機 A。
G1_KNOWN_UNMET = {
    ("nocal", "A"): "日曆缺席 ⇒ 站頁頭卡的應生產時段材料 inert（`_station_rw_payload`→None）、`station_model` 退回舊 24h 軸，"
                    "其 A 是 MES 時間軸狀態拆分（down＋PM），不是工單停機；/utilization 與 /availability 走工單口徑 24h 退路。"
                    "證據：st.rw_axis=False ∧ st.real_split=False（站頁無工單分子）。",
    ("nocal", "A%"): "同 (nocal, A)：畫面百分比跟著比值走。",
    ("nocal", "U"): "同 (nocal, A)：站頁頭卡舊軸的 U 分母是活躍期間／全時、分子是時間軸 analyze 的生產秒。",
    ("nocal", "U%"): "同 (nocal, U)。",
    ("nocal", "prod"): "U 的分子兩支：/utilization `au_chips` 取 Σ 各機 `agg.producing_sec`（records 彙總）；站頁頭卡取凍結的 "
                       "`mt.fleet_scoreboard`（analyze）的 `producing_sec`。兩支在同一份時間軸上給不同的生產秒。"
                       "證據：兩值皆非空且不等。",
    ("cal", "prod"): "同 (nocal, prod)。",
    ("cal", "down"): "有日曆時站頁頭卡的停機＝`app.rw_station_payload` 的逐態秒（形③ 父機取 `parent_seat_rollup` 的 "
                     "`by_state_union`＝事件視角；其餘機台逐態先 union 再 ∩ RW）**跨態相加**；/availability 與 /utilization "
                     "A chip＝跨態 union 後優先序切分（每秒只扣一次，SPEC-AVAILABILITY §2.1–2.3）。⇒ 同一台機在同一秒有兩種態的"
                     "工單（例：等料與等外包重疊、或形③ 兩座各一種態）時，站頁頭卡多扣一次。"
                     "證據：分母三頁同；每一台不一致的機台站頁值較大、逐態都 ≥、至少兩個態；總差＝這些機台差之和。",
    ("cal", "A"): "同 (cal, down)：分母同、分子（down）站頁頭卡較大 ⇒ 站頁 A 較低。",
    ("cal", "A%"): "同 (cal, A)。",
    ("cal", "U"): "U 的差異＝prod 兩支（見 prod）＋（有跨態重疊的站）站頁 down 的事件視角差（見 down）；兩頁各自"
                  "滿足 U＝prod÷(planned−down)。證據：分母同、兩頁 U 各自等於自己的 prod÷(planned−down)、prod 不同。",
    ("cal", "U%"): "同 (cal, U)。",
    ("cal", "A_eq_machine"): "/equipment 的應生產時段 ctx 經 `closures_with_uncertainty`（C-PC12：停線時段內有工單的綑全體標"
                             "不確定 ⇒ 對那些機台不扣停線、停機照算）；/utilization／/availability 的 `_util_erw_ctx` 刻意"
                             "不標不確定（app.py 註解：保守形）。⇒ 只有落在不確定綑上的機台兩頁 A 不同。"
                             "證據：每一台不一致的機台都在 /equipment 板某個與窗相交的 closure 的 uncertain_eids 裡。",
}

_ROUND_CHILD = r'''
import re, hashlib
_freeze_clock(ARG["anchor"])
os.chdir(TD)
MODE = ARG["mode"]
import wsynth_tick as K
_summ = K.boot(out=lambda s: None)                 # check_env → A11 開 → 絆線（含狀態標記）
R["boot_ok"] = bool(_summ.get("ok"))
R["boot_checks"] = [[c0["name"], c0["ok"]] for c0 in _summ.get("checks", [])]
drv = sys.modules["pyodbc"]
R["driver_is_stub"] = bool(drv.__dict__.get("__core_source_stub__"))
_drv_connect = drv.connect
try:
    _drv_connect("DRIVER={x};SERVER=y")
    R["trip_raise"] = "no exception"
except Exception as _e0:
    R["trip_raise"] = type(_e0).__name__
CALLS = []
def _leak(*a, **k):
    CALLS.append(["%s:%s" % (os.path.basename(f.filename), f.name) for f in traceback.extract_stack()[:-1]
                  if f.filename.startswith(TREE)][-5:])
    return _drv_connect(*a, **k)
drv.connect = _leak
import wsynth_launch as L
app = L.import_app(out=lambda s: None)
L.prebuild_world(out=lambda s: None)
import wsynth_io as IO
import board_data as wd
import board_scope as wscope
wem = app.wem
FLEET = [a0 for a0 in IO.world().ctx.fleet]
CLS_OF = {str(a0["asset_id"]): str(a0.get("station_cls")) for a0 in FLEET if a0.get("station_cls")}
G1_CLASSES = [ARG["cal"], wd.CLN_CLASS]
# 攔截頁面真正用的那份值（只記回傳、不改）：模板 ctx、`au_chips`、`station_model`
CAP = {}
_tr0 = app.templates.TemplateResponse
def _tr(request, name, ctx, *a, **k):
    CAP[name.rsplit("/", 1)[-1]] = ctx
    return _tr0(request, name, ctx, *a, **k)
app.templates.TemplateResponse = _tr
SPY = {"au": None, "st": None, "rws": None}
_au0 = app.au_chips
def _au(*a, **k):
    r0 = _au0(*a, **k)
    SPY["au"] = r0
    return r0
app.au_chips = _au
_rws0 = app.rw_station_payload
def _rws(*a, **k):
    r0 = _rws0(*a, **k)
    if SPY["rws"] is None:          # 頭卡材料是一頁裡的**第一次**呼叫（之後那次是 Kestrel 專機 chip 的子集）
        SPY["rws"] = r0
    return r0
app.rw_station_payload = _rws
_st0 = app.oee.station_model
def _st(*a, **k):
    r0 = _st0(*a, **k)
    SPY["st"] = r0
    return r0
app.oee.station_model = _st
from fastapi.testclient import TestClient
c = TestClient(app.app, raise_server_exceptions=False)
EIDS = set()
NR = [0]
_cg0 = c.get
def _cg(url, *a, **k):
    assert "source=live" not in url
    r0 = _cg0(url, *a, **k)
    NR[0] += 1
    EIDS.update(re.findall(r"EID-\d{5}", r0.content.decode("utf-8", "replace")))
    return r0
c.get = _cg
def text_of(h):
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S)
    h = re.sub(r"<!--.*?-->", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
def first(pat, t):
    mm = re.search(pat, t)
    return mm.group(1) if mm else None
def _f(x):
    return None if x is None else float(x)
_CLEAR = ("_CACHE", "_EQM_CACHE", "_EQM_MES_CACHE", "_SCOPE_EV_CACHE", "_WOW_CACHE", "_UTIL_WOW_CACHE",
          "_MAINT_CACHE", "_MAINT_WOW_CACHE", "_SEAT_CACHE", "_UD_CACHE", "_FBC_CACHE", "_YS_CACHE")
def clear_caches():
    for nm in _CLEAR:
        d0 = getattr(app, nm, None)
        if isinstance(d0, dict):
            d0.clear()

# ── (G2) 確定性：第一組請求（兩個子進程同序）──────────────────────────────────────────────
if MODE in ("full", "twin"):
    det = {}
    for u in ARG["det_urls"]:
        r0 = c.get(u + ("&" if "?" in u else "?") + "lang=en")
        det[u] = [r0.status_code, hashlib.md5(r0.content + r0.headers.get("content-disposition", "").encode()).hexdigest(),
                  len(r0.content)]
    R["det"] = det

# ── (G3) 前綴穩定：固定過去窗的讀數 ────────────────────────────────────────────────────────
pw_s, pw_e = ARG["pw"]
pref = {}
for cls in G1_CLASSES:
    q = "cls=%s&start=%s&end=%s&lang=en" % (cls, pw_s, pw_e)
    CAP.clear()
    r0 = c.get("/availability?" + q)
    av = (CAP.get("availability.html") or {}).get("av") or {}
    SPY["au"] = None
    r1 = c.get("/utilization?" + q)
    au = SPY["au"] or {}
    pref[cls] = {"status": [r0.status_code, r1.status_code],
                 "av": {e: [_f(v.get("A")), _f(v.get("rw_sec")), _f(v.get("down_sec"))]
                        for e, v in sorted((av.get("by_eid") or {}).items())},
                 "av_fleet": [_f(av.get("A")), _f(av.get("rw_sec")), _f(av.get("down_sec"))],
                 "au": [_f(au.get(k0)) for k0 in ("A", "U", "wo_down", "full", "prod")]}
R["prefix"] = pref

if MODE == "full":
    def g1(state):
        clear_caches()
        out = {}
        for cls in G1_CLASSES:
            one = {}
            SPY["au"] = None
            hu = text_of(c.get("/utilization?days=7&lang=en&cls=%s" % cls).text)
            au = SPY["au"] or {}
            CAP.clear()
            hv = text_of(c.get("/availability?days=7&lang=en&cls=%s" % cls).text)
            avc = CAP.get("availability.html") or {}
            av = avc.get("av") or {}
            SPY["st"] = SPY["rws"] = None
            hs = text_of(c.get("/station/%s/overview?days=7&lang=en" % cls).text)
            st = SPY["st"] or {}
            rw = bool(st.get("rw_axis"))
            # 站頁頭卡逐機（事件視角）停機 vs /availability 逐機（優先序切分）停機：只記不一致的機台（原因證據用）
            _avb = av.get("by_eid") or {}
            _seb = {}
            for e, v in ((SPY["rws"] or {}).get("by_eid") or {}).items():
                _ev = _f(app.oee.rw_avail_down_sec(v.get("by_state") or {}))
                _ad = _f((_avb.get(e) or {}).get("down_sec"))
                if _ad is None or abs((_ev or 0.0) - _ad) > 1e-6:
                    _seb[e] = {"station_by_state": {k0: round(x0, 3) for k0, x0 in (v.get("by_state") or {}).items()},
                               "station_down": _ev, "avail_down": _ad,
                               "avail_by_state": {k0: round(x0, 3) for k0, x0 in ((_avb.get(e) or {}).get("by_state") or {}).items()},
                               "rollup": bool((_avb.get(e) or {}).get("rollup"))}
            one["st_vs_av_eid"] = _seb
            one["axis"] = {"utilization": "rw" if au.get("erw") else "24h",
                           "availability": "rw" if avc.get("av_rw") else "24h",
                           "station": "rw" if rw else ("real_split" if st.get("real_split") else "old")}
            one["vals"] = {
                "A": {"utilization": _f(au.get("A")), "availability": _f(av.get("A")),
                      "station": _f(st.get("availability"))},
                "U": {"utilization": _f(au.get("U")), "station": _f(st.get("utilization"))},
                "down": {"utilization": _f(au.get("wo_down")), "availability": _f(av.get("down_sec")),
                         "station": _f(st.get("rw_avail_down_sec") if rw else st.get("wo_down_sec"))},
                "planned": {"utilization": _f(au.get("full")), "availability": _f(av.get("rw_sec")),
                            "station": _f(st.get("rw_planned_sec") if rw else st.get("total_cap_sec"))},
                "prod": {"utilization": _f(au.get("prod")), "station": _f(st.get("producing_sec"))},
                "A%": {"utilization": first(r"Availability \(A\) (\d+%)", hu),
                       "availability": first(r"(\d+%) Availability [0-9hms ]+ of work-order downtime", hv),
                       "station": first(r"Availability · F4 EQUIP (\d+%)", hs)},
                "U%": {"utilization": first(r"Utilization \(U\) (\d+%)", hu),
                       "station": first(r"Utilization · F3 PROD (\d+%)", hs)}}
            one["st_flags"] = {"rw_axis": rw, "real_split": bool(st.get("real_split"))}
            # ── (G6) R44：形③ 父機＝在役座推導（本站的 /availability 同一份 ctx）
            be = av.get("by_eid") or {}
            M = app._util_page_materials("synth", 7, 15, "", "", cls, "")
            wos_keys = set((M.get("wos_by_eid") or {}).keys())
            r44 = {}
            for p, seats in wscope.PARENT_EXPANSION.items():
                if p not in be:
                    continue
                pv = be[p]
                roll = pv.get("rollup") or {}
                band = pv.get("band") or []
                r44[p] = {"rollup": roll, "down": _f(pv.get("down_sec")), "rw": _f(pv.get("rw_sec")),
                          "A": _f(pv.get("A")),
                          "band_sum": sum((b0 - a0) * k0 / n0 for _s0, a0, b0, k0, n0, _x0 in band),
                          "n_band": len(band),
                          "seat_rows": sorted(s for s in seats if s in be),
                          "seat_wos": sorted(s for s in seats if s in wos_keys),
                          "parent_wos": p in wos_keys}
            one["r44"] = r44
            one["c_av5"] = [_f(av.get("up_sec")), sum((av.get("by_state") or {}).values()), _f(av.get("rw_sec"))]
            out[cls] = one
        # ── /equipment 逐機 A%（應生產時段基準／24h 退路）對同一個窗的 /availability 逐機 A
        c.get("/equipment/body?days=7&lang=en")
        b = app.load_eqm("synth", 7, 15)                 # 同頁快取鍵（days=7、stop_min=15）＝頁面那一份板
        dw = wem.day_windows(b["win_end"], b["n_days"])
        ws, we = dw[0][1], wem._coerce_dt(b["win_end"])
        unc = set()
        for cl0 in (b.get("closures") or ()):
            a0, b0 = wem._coerce_dt(cl0.get("start")), wem._coerce_dt(cl0.get("end"))
            if a0 is not None and b0 is not None and a0 < we and b0 > ws:
                unc.update(cl0.get("uncertain_eids") or ())
        rows = {}
        for ar in b.get("areas") or ():
            for rr in ar.get("rows") or ():
                if rr.get("is_seat") or not rr.get("eid") or rr.get("avail_pct") is None:
                    continue
                rows[rr["eid"]] = float(rr["avail_pct"])
        by_cls = {}
        for e in rows:
            if CLS_OF.get(e):
                by_cls.setdefault(CLS_OF[e], []).append(e)
        cmp_, miss = {}, []
        for cls in sorted(by_cls):
            CAP.clear()
            c.get("/availability?cls=%s&start=%s&end=%s&lang=en" % (cls, ws.isoformat(), we.isoformat()))
            be2 = ((CAP.get("availability.html") or {}).get("av") or {}).get("by_eid") or {}
            for e in by_cls[cls]:
                if e in be2 and be2[e].get("A") is not None:
                    cmp_[e] = [rows[e], 100.0 * float(be2[e]["A"]), e in unc]
                else:
                    miss.append(e)
        out["eq_machine"] = {"cmp": cmp_, "not_on_availability": sorted(miss)[:10], "n_rows": len(rows),
                             "n_unc": len(unc), "board_erw": b.get("erw") is not None,
                             "window": [str(ws), str(we)]}
        # ── (G7) 首頁（landing2）不印 A／U 數字
        hh = {}
        for lg in ("en", "zh"):
            t0 = text_of(c.get("/landing2/body?lang=" + lg).text)
            hh[lg] = first(r"((?:Availability|Utilization|妥善率|稼動)[^%]{0,40}\d+%)", t0)
        out["home_au"] = hh
        out["landing_body_status"] = c.get("/landing/body?days=7&lang=en").status_code
        R["g1_" + state] = out

    def badge_scan(state):
        I18 = app.I18N
        from markupsafe import escape as _mesc
        res = {"badge": {}, "bar": {}, "forbidden": {}, "status": {}}
        for lg in ("en", "zh"):
            for u in ARG["wp_full"] + ARG["wp_frag"]:
                uu = u + ("&" if "?" in u else "?") + "lang=" + lg
                r0 = c.get(uu)
                h = r0.text
                res["status"][uu] = r0.status_code
                if u in ARG["wp_full"]:
                    m0 = re.search(r'<span class="board-src ([^"]*)">([^<]*)</span>', h)
                    res["badge"][uu] = [m0.group(1), m0.group(2)] if m0 else None
                    res["bar"][uu] = ('<div class="board-synthbar" role="note">%s</div>' % _mesc(I18[lg]["synth_bar"])) in h
                else:
                    res["bar"][uu] = "board-synthbar" not in h
                hits = []
                for pat in ARG["forbidden"]:
                    for mm in re.finditer(pat, h):
                        hits.append(h[max(0, mm.start() - 60):mm.end() + 30])
                if hits:
                    res["forbidden"][uu] = hits[:3]
        R["badge_" + state] = res

    g1("nocal")
    badge_scan("nocal")
    R["cal_before"] = wd.load_prod_calendar()[1]
    _ks = K.run_tick(K.parse_steps("calendar"), out=lambda s: None)
    R["tick"] = {"rc": _ks.get("rc"), "results": _ks.get("results")}
    R["cal_after"] = wd.load_prod_calendar()[1]
    g1("cal")
    badge_scan("cal")
R["calls"] = CALLS
R["eids"] = sorted(EIDS)
R["n_renders"] = NR[0]
'''


def _round_child(mode, anchor, pw, base, token):
    """一個 synth_round 子進程（自己的改向狀態目錄、凍結時鐘＝`anchor`、cmms＝呼叫端起的 loopback `base`）。"""
    td = tempfile.mkdtemp(prefix="wp7_round_%s_" % mode)
    env = _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": os.path.join(td, "state"),
                     "BOARD_SYNTH_NOW": anchor.isoformat(), "BOARD_CMMS_BASE_URL": base,
                     "BOARD_CMMS_TOKEN": token})
    res, _r = _run_child(_ROUND_CHILD, REPO, env, {
        "mode": mode, "anchor": anchor.isoformat(), "cal": CAL, "pw": pw,
        "det_urls": list(ROUND_DET_URLS), "wp_full": list(WP4B_FULL), "wp_frag": list(WP4B_FRAG),
        "forbidden": list(WP4B_FORBIDDEN)})
    return res


def _g1_compare(vals, tol):
    """一個指標的各頁值 → (狀態, 參與比較的頁)。None 的頁不參與；少於兩頁＝"n/a"。"""
    have = {pg: v for pg, v in vals.items() if v is not None}
    if len(have) < 2:
        return "n/a", sorted(have)
    xs = list(have.values())
    if isinstance(xs[0], str):
        ok = len(set(xs)) == 1
    else:
        ok = all(abs(x - xs[0]) <= tol for x in xs)
    return ("met" if ok else "unmet"), sorted(have)


def _g1_evidence(state, metric, one_by_cls, eqm):
    """`G1_KNOWN_UNMET` 每一項的原因證據（子進程量出的值；只對**該指標未達成**的站判；不成立 ⇒ False）。"""
    if metric == "A_eq_machine":
        bad = [e for e, (x, y, u) in eqm["cmp"].items() if abs(x - y) > 1e-6]
        return all(eqm["cmp"][e][2] for e in bad)
    tol = 1e-9 if metric in ("A", "U") else 1e-6
    ok, n = True, 0
    for cls, one in one_by_cls.items():
        v, fl = one["vals"], one["st_flags"]
        if _g1_compare(v[metric], tol)[0] != "unmet":
            continue
        n += 1
        if state == "nocal" and metric in ("A", "A%", "U", "U%"):
            ok &= (fl["rw_axis"] is False and fl["real_split"] is False)
        elif metric == "prod":
            ok &= v["prod"]["utilization"] is not None and v["prod"]["station"] is not None
        elif state == "cal" and metric in ("A", "A%", "down"):
            # 站頁頭卡＝逐態 union 再跨態相加（事件視角）；/availability＝跨態 union 後優先序切分（每秒一次）。
            # 證據：①分母同 ②每一台不一致的機台：站頁值 > /availability 值、逐態都 ≥、且至少兩個態（跨態重疊才可能）
            #       ③站頁與 /availability 的總差＝這些機台差之和（差異全部由它們解釋）。
            sv = one.get("st_vs_av_eid") or {}
            per_ok = bool(sv) and all(
                x["avail_down"] is not None and x["station_down"] is not None
                and x["station_down"] > x["avail_down"] + 1e-6
                and sum(1 for s0, y in x["station_by_state"].items() if y > 0) >= 2
                and all(y >= (x["avail_by_state"].get(s0) or 0.0) - 1e-3 for s0, y in x["station_by_state"].items())
                for x in sv.values())
            tot = (v["down"]["station"] or 0.0) - (v["down"]["availability"] or 0.0)
            ok &= bool(_g1_compare(v["planned"], 1e-6)[0] == "met" and per_ok
                       and abs(tot - sum(x["station_down"] - x["avail_down"] for x in sv.values())) < 1e-3)
        elif state == "cal" and metric in ("U", "U%"):
            # U 的差＝prod 兩支（見 prod）＋（若有）站頁 down 的事件視角差（見 down）；兩頁各自滿足 U＝prod÷(planned−down)。
            pl, dn, pr = v["planned"], v["down"], v["prod"]
            u_u, u_s = v["U"]["utilization"], v["U"]["station"]
            den_u = (pl["utilization"] or 0) - (dn["utilization"] or 0)
            den_s = (pl["station"] or 0) - (dn["station"] or 0)
            ok &= bool(_g1_compare(pl, 1e-6)[0] == "met" and den_u > 0 and den_s > 0
                       and u_u is not None and u_s is not None
                       and abs(u_u - pr["utilization"] / den_u) < 1e-9 and abs(u_s - pr["station"] / den_s) < 1e-9
                       and _g1_compare(pr, 1e-6)[0] == "unmet")
        else:
            ok = False
    return bool(ok and n)


# ── (G3) 前綴穩定比的「已定案過去窗」（2026-10-02 修；原窗＝早一天時鐘往回 1 小時，白天穩定紅）────────────
#   前綴穩定（SPEC-SYNTHLINE C-SY5）釘的是「延長時間軸不得改寫任何**已曝光**事件」。但合成世界（照真實 MES／
#   cmms 的樣子）有幾種事實會**晚到**：曝光時刻晚於它所描述的時間點。窗尾若離早一天時鐘太近，晚到的事實在早一天
#   的世界還沒曝光、晚一天的世界已曝光 ⇒ 同一個窗讀數不同，但兩個世界都沒有改寫任何已曝光的東西。
#   2026-10-02 16:43 實測：窗尾 10-01 15:43、早一天時鐘 16:43；CAL 段 EID-00950 15:27→16:55（end > 16:43 未曝光）
#   ⇒ /utilization prod 差 960 秒＝(15:43−15:27)；CLN 段 EID-27789 15:38→16:53 ⇒ 差 360＝300（段內）＋60（與
#   15:37 結束的前段之間 1 分空檔被 analyze 併入 run，STOP_MIN 15）。凌晨窗尾沒有跨窗尾的生產段 ⇒ 綠。
#   晚到的事實（皆取自參數，改參數這裡自動跟）：
#     · MES 段只在 end <= now 曝光、段長上限 SEG_MINUTES[1]；窗尾後 STOP_MIN 內開始的段會把空檔併進 run；
#     · 反應單 failure_started_at＝開單前 FSA_LEAD_MIN；production_resumed_at 落後其值 PRA_WRITE_LAG_MIN 才寫；
#     · A11 schedule_state 的 effective_date＝台北**日曆日**（自 00:00 生效），記錄於當日 A11_RECORD_HOUR。
#   ⇒ 窗尾＝(早一天時鐘 − 最長晚到 − 餘裕) **向下取到台北 00:00**（日粒度的 A11 只在整日界上才不跨）。
G3_SETTLE_PAD_MIN = 15


def _g3_settle():
    """最長晚到（分）＋餘裕 → timedelta。只讀參數，不含任何時刻。"""
    import wsynth_params as WP
    import mes_timeline as MT
    late = max(WP.SEG_MINUTES[1] + MT.STOP_MIN_DEFAULT, WP.FSA_LEAD_MIN[1], WP.PRA_WRITE_LAG_MIN[1])
    return datetime.timedelta(minutes=late + G3_SETTLE_PAD_MIN)


def g3_window(anchor):
    """`anchor`（晚一天世界的凍結時鐘）→ (早一天時鐘, [窗起, 窗尾])。窗尾＝早一天時鐘之前已完全定案的台北 00:00。"""
    early = anchor - datetime.timedelta(days=1)
    end = (early - _g3_settle()).replace(hour=0, minute=0, second=0, microsecond=0)
    return early, [(end - datetime.timedelta(days=6)).isoformat(), end.isoformat()]


def _g3_window_legacy(anchor):
    """修正前的窗（早一天時鐘往回 1 小時）；只給 (G3a) 的反向對照（證明檢查有牙）用。"""
    early = anchor - datetime.timedelta(days=1)
    return early, [(early - datetime.timedelta(days=6)).isoformat(), (early - datetime.timedelta(hours=1)).isoformat()]


def _g3_late_facts(w, early, pw_e):
    """世界 `w`（`wsynth_world.build_world` 的結果，曝光到比 `early` 晚的時刻）裡，在 `early` 還沒曝光、卻描述
       窗尾 `pw_e` 之前（含窗尾後 STOP_MIN 內開始、會被併進 run 的段）的事實 → list。空＝窗在 `early` 已定案。"""
    import mes_timeline as MT
    import wsynth_clock as WCK
    fi = datetime.datetime.fromisoformat
    lim = pw_e + datetime.timedelta(minutes=MT.STOP_MIN_DEFAULT)
    out = []
    for s in w["segments"]:
        if fi(s["start"]) <= lim and fi(s["end"]) > early:
            out.append(("segment", s["eid"], s["start"], s["end"]))
    for e in w["events"]:
        at = WCK.from_utc_z(e["at"])
        if at <= early:
            continue
        refs = []
        if e.get("failure_started_at"):
            refs.append(WCK.from_utc_z(e["failure_started_at"]))
        if e.get("kind") == "wo.field" and e.get("field") == "production_resumed_at":
            refs.append(WCK.from_utc_z(e["value"]))
        for k0 in ("effective_date", "in_service_from"):
            if e.get(k0):
                refs.append(fi(e[k0]))                      # 台北日曆日 ⇒ 自 00:00 生效
        if any(r < pw_e for r in refs):
            out.append((e["kind"], e.get("asset_id"), e["at"], [r.isoformat() for r in refs]))
    return out


def _g3_diff(a_, b_):
    """兩個子進程的 (G3) 讀數 → 差異 list（空＝逐值同）。比全部機台、全部欄位，不挑。"""
    pd = []
    for cls in a_:
        x, y = a_[cls], b_.get(cls) or {}
        if x["status"] != [200, 200] or y.get("status") != [200, 200]:
            pd.append((cls, "status", x["status"], y.get("status")))
            continue
        for k in ("av_fleet", "au"):
            if not _num_eq(x[k], y.get(k)):
                pd.append((cls, k, x[k], y.get(k)))
        if sorted(x["av"]) != sorted(y.get("av") or {}):
            pd.append((cls, "av_eids", sorted(x["av"]), sorted(y.get("av") or {})))
        for e in x["av"]:
            if not _num_eq(x["av"][e], (y.get("av") or {}).get(e)):
                pd.append((cls, e, x["av"][e], (y.get("av") or {}).get(e)))
    return pd


#: (G3b) 真頁面逐時刻重跑的整點（台北；晚一天世界的凍結時鐘＝anchor 當日這些整點）
G3_HOURS = (0, 4, 8, 12, 16, 20)
#: (G3a) 產生器層的定案檢查掃的天數（anchor 當日往前；每天 24 個整點）
G3_SWEEP_DAYS = 3


def g3_any_clock(ck, anchor, WC):
    """(G3a) 產生器層：anchor 前 `G3_SWEEP_DAYS` 天 × 24 整點，`g3_window` 的窗在早一天時鐘都已定案（零晚到事實）；
       反向對照：舊窗（早一天時鐘 −1h）在同一批時鐘至少一次不定案（檢查有牙）。
       (G3b) 真頁面：`G3_HOURS` 各一對（晚一天／早一天）合成子進程，固定過去窗讀數逐值同。"""
    import wsynth_world as WW
    from concurrent.futures import ThreadPoolExecutor
    d0 = anchor.date()
    last = datetime.datetime(d0.year, d0.month, d0.day, 23, 0)
    w = WW.build_world(last)
    clocks = [last - datetime.timedelta(hours=h) for h in range(24 * G3_SWEEP_DAYS)]
    bad_new, bad_old = {}, {}
    for t in clocks:
        early, pw = g3_window(t)
        lf = _g3_late_facts(w, early, datetime.datetime.fromisoformat(pw[1]))
        if lf:
            bad_new[t.isoformat()] = lf[:3]
        early, pw = _g3_window_legacy(t)
        lf = _g3_late_facts(w, early, datetime.datetime.fromisoformat(pw[1]))
        if lf:
            bad_old[t.isoformat()] = len(lf)
    ck(not bad_new and bad_old,
       "(G3a) the prefix window is settled at every hourly clock (%d clocks over %d days: no fact describing time before "
       "the window end is exposed only after the earlier world's clock); control: the pre-fix window (early clock -1h) "
       "is unsettled on %d of them: unsettled=%s" % (len(clocks), G3_SWEEP_DAYS, len(bad_old),
                                                     dict(list(bad_new.items())[:3])))
    print("[wiring] (G3a) pre-fix window unsettled at %d/%d hourly clocks (hours of day: %s)"
          % (len(bad_old), len(clocks), sorted({datetime.datetime.fromisoformat(k).hour for k in bad_old})), flush=True)

    def pair(h):
        t = datetime.datetime(d0.year, d0.month, d0.day, h, 0)
        early, pw = g3_window(t)
        with WC.serve(WC.GeneratorSource(), now_fn=lambda: t) as b1:
            x = _round_child("early", t, pw, b1, WC.DEFAULT_TOKEN)
        with WC.serve(WC.GeneratorSource(), now_fn=lambda: early) as b2:
            y = _round_child("early", early, pw, b2, WC.DEFAULT_TOKEN)
        n_m = sum(len(v["av"]) for v in x["prefix"].values())
        nz = sum(1 for v in x["prefix"].values() for r in v["av"].values() if (r[2] or 0) > 0)
        return h, pw, _g3_diff(x["prefix"], y["prefix"]), n_m, nz

    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(pair, G3_HOURS))
    for h, pw, pd, n_m, nz in res:
        ck(not pd and n_m > 0 and nz > 0,
           "(G3b) %02d:00 clock: a world one day later re-serves the settled past window %s..%s identically "
           "(%d machines, %d with downtime): diff=%s" % (h, pw[0], pw[1], n_m, nz, pd[:4]))


def synth_round(ck, anchor=None):
    """WP-7 synth_round：三個合成子進程（full＝兩種日曆狀態、twin＝同時鐘確定性、early＝早一天的前綴）。
       → dict（`eids`／`n_renders`／`g1`）供 (I3) 與報告用。"""
    t0 = time.time()
    anchor = anchor or datetime.datetime.now().replace(second=0, microsecond=0)
    sys.path.insert(0, REPO)
    early, pw = g3_window(anchor)
    import wsynth_cmms as WC                       # loopback 假 cmms（只綁 127.0.0.1）
    # full 與 twin 共用**同一個**假 cmms（同一個凍結時鐘）：工單深連結印的是 cmms base URL（含埠），各起一個
    #   就是兩個隨機埠＝位元差異來自測試樁、不是合成世界（2026-10-02 實測 /equipment/health/body 只差這一處）。
    with WC.serve(WC.GeneratorSource(), now_fn=lambda: anchor) as base:
        full = _round_child("full", anchor, pw, base, WC.DEFAULT_TOKEN)
        twin = _round_child("twin", anchor, pw, base, WC.DEFAULT_TOKEN)
    with WC.serve(WC.GeneratorSource(), now_fn=lambda: early) as base_e:
        prev = _round_child("early", early, pw, base_e, WC.DEFAULT_TOKEN)
    print("[wiring] (G) synth_round children done in %.0fs (anchor %s, early %s, past window %s..%s)"
          % (time.time() - t0, anchor, early, pw[0], pw[1]), flush=True)

    # (G0) 絆線
    for tag, res in (("full", full), ("twin", twin), ("early", prev)):
        ck(res["boot_ok"] and all(ok for _n, ok in res["boot_checks"]) and res["driver_is_stub"]
           and res["trip_raise"] == "SynthLeakError" and res["calls"] == [],
           "(G0) %s: tripwire armed (boot checks %s, driver stub, direct connect raises %s) and the driver stub was "
           "called zero times over %d renders: %s" % (tag, res["boot_checks"], res["trip_raise"], res["n_renders"],
                                                       res["calls"][:3]))
    # (G2) 確定性
    dd = {u: (full["det"].get(u), twin["det"].get(u)) for u in ROUND_DET_URLS}
    bad = {u: v for u, v in dd.items() if not (v[0] and v[1] and v[0] == v[1] and v[0][0] == 200)}
    ck(not bad, "(G2) determinism: two independent synthetic processes (own state dirs and caches, one shared loopback "
       "cmms, same frozen clock) serve %d phase-1 URLs bit-identically: diff=%s" % (len(dd), dict(list(bad.items())[:4])))
    # (G3) 前綴穩定
    pd = _g3_diff(full["prefix"], prev["prefix"])
    n_m = sum(len(v["av"]) for v in full["prefix"].values())
    nz = sum(1 for v in full["prefix"].values() for x in v["av"].values() if (x[2] or 0) > 0)
    ck(not pd and n_m > 0 and nz > 0,
       "(G3) prefix stability: a world one day later re-serves the settled past window %s..%s identically "
       "(/availability per machine A/RW/down for %d machines, %d with downtime; fleet A; /utilization A/U/down/"
       "full/prod): diff=%s" % (pw[0], pw[1], n_m, nz, pd[:4]))
    # (G3a)(G3b) 窗的取法在一天中任何時刻都定案（不隨跑測的時刻變）
    g3_any_clock(ck, anchor, WC)
    # (G4) 大牌／橫幅／禁字（兩種日曆狀態）
    for state in ("nocal", "cal"):
        bs = full["badge_" + state]
        bad_st = {u: s for u, s in bs["status"].items() if s != 200}
        bad_b = {u: v for u, v in bs["badge"].items()
                 if not (v and v[0] == "synth" and v[1] == ("synthetic line" if u.endswith("lang=en") else "合成產線"))}
        bad_bar = [u for u, v in bs["bar"].items() if not v]
        ck(not bad_st and len(bs["badge"]) == 2 * len(WP4B_FULL) and not bad_b and not bad_bar and not bs["forbidden"],
           "(G4) %s: SYNTHETIC badge en/zh on every phase-1 full page, the site-wide bar on full pages only, zero "
           "LIVE/REAL/real/真值 on %d renders: status=%s badge=%s bar=%s forbidden=%s"
           % (state, len(bs["status"]), bad_st, bad_b, bad_bar[:4], dict(list(bs["forbidden"].items())[:3])))
    # (G8) 日曆狀態真的換了尺
    ck(full["cal_before"] == "absent" and full["cal_after"] == "ok" and (full["tick"] or {}).get("rc") == 0,
       "(G8a) the round ran with no calendar, then the tick wrote one (status %s -> %s, tick rc=%s)"
       % (full["cal_before"], full["cal_after"], (full["tick"] or {}).get("rc")))
    ax = {s: {cls: one["axis"] for cls, one in full["g1_" + s].items() if isinstance(one, dict) and "axis" in one}
          for s in ("nocal", "cal")}
    ck(all(a_ == {"utilization": "24h", "availability": "24h", "station": "old"} for a_ in ax["nocal"].values())
       and all(a_ == {"utilization": "rw", "availability": "rw", "station": "rw"} for a_ in ax["cal"].values())
       and full["g1_nocal"]["eq_machine"]["board_erw"] is False and full["g1_cal"]["eq_machine"]["board_erw"] is True,
       "(G8b) no calendar ⇒ every page on its 24h / old axis; calendar ⇒ /utilization, /availability, the station "
       "header and the /equipment board all on the expected-run-window axis: %s; /equipment erw %s -> %s"
       % (ax, full["g1_nocal"]["eq_machine"]["board_erw"], full["g1_cal"]["eq_machine"]["board_erw"]))

    # (G5) G1
    report = {}
    for state in ("nocal", "cal"):
        g = full["g1_" + state]
        per_cls = {cls: one for cls, one in g.items() if isinstance(one, dict) and "vals" in one}
        unmet = {}
        for metric in ("A", "U", "down", "planned", "prod", "A%", "U%"):
            for cls, one in per_cls.items():
                stt, pgs = _g1_compare(one["vals"][metric], 1e-9 if metric in ("A", "U") else 1e-6)
                report.setdefault((state, metric), {})[cls] = (stt, one["vals"][metric])
                if stt == "unmet":
                    unmet.setdefault(metric, []).append(cls)
        eqm = g["eq_machine"]
        bad_eq = sorted(e for e, (x, y, _u) in eqm["cmp"].items() if abs(x - y) > 1e-6)
        report[(state, "A_eq_machine")] = {"n": len(eqm["cmp"]), "unmet": bad_eq,
                                           "explained": [e for e in bad_eq if eqm["cmp"][e][2]]}
        if bad_eq:
            unmet["A_eq_machine"] = bad_eq
        for metric, where in sorted(unmet.items()):
            known = (state, metric) in G1_KNOWN_UNMET
            ev = known and _g1_evidence(state, metric, per_cls, eqm)
            detail = ({e: [round(eqm["cmp"][e][0], 4), round(eqm["cmp"][e][1], 4), eqm["cmp"][e][2]] for e in where[:6]}
                      if metric == "A_eq_machine"
                      else {cls: per_cls[cls]["vals"][metric] for cls in where})
            ck(known and ev, "(G5) G1 %s/%s unmet on %s — %s: %s"
               % (state, metric, where[:6], ("known, cause evidenced" if known and ev else
                                             "known but the recorded cause is NOT evidenced" if known else
                                             "NOT in G1_KNOWN_UNMET (new cross-page inconsistency)"), detail))
        for (s0, m0) in G1_KNOWN_UNMET:
            if s0 == state and m0 not in unmet:
                print("[wiring] (G5) NOTE %s/%s is listed in G1_KNOWN_UNMET but was met on this clock" % (s0, m0),
                      flush=True)
        met = [m for m in ("A", "U", "down", "planned", "prod", "A%", "U%", "A_eq_machine") if m not in unmet]
        na = [(cls, m) for cls, one in per_cls.items() for m in ("A", "U", "down", "planned", "prod", "A%", "U%")
              if _g1_compare(one["vals"][m], 1e-6)[0] == "n/a"]
        ck(len(per_cls) == 2 and len(eqm["cmp"]) > 0 and not na,
           "(G5) %s: every G1 metric had at least two pages to compare on both tested stations (%s), and %d "
           "/equipment machines were compared on window %s: n/a=%s"
           % (state, sorted(per_cls), len(eqm["cmp"]), eqm["window"], na))
        print("[wiring] (G5) G1 %s: met=%s unmet=%s" % (state, met, sorted(unmet)), flush=True)
        for cls, one in per_cls.items():
            print("[wiring] (G5)   %s %s axis=%s %s" % (state, cls, one["axis"],
                                                         json.dumps(one["vals"], ensure_ascii=False)), flush=True)
            if one.get("st_vs_av_eid"):
                print("[wiring] (G5)   %s %s station-header vs /availability per-machine downtime differs on: %s"
                      % (state, cls, json.dumps(one["st_vs_av_eid"], ensure_ascii=False)), flush=True)
        print("[wiring] (G5)   %s /equipment vs /availability per machine: %d compared, unmet=%s (explained by C-PC12 "
              "uncertain closures: %s; %d uncertain EIDs on the board)" % (state, len(eqm["cmp"]), bad_eq,
                                                                           report[(state, "A_eq_machine")]["explained"],
                                                                           eqm["n_unc"]), flush=True)

    # (G6) R44
    for state in ("nocal", "cal"):
        g = full["g1_" + state]
        r44 = {cls: one["r44"] for cls, one in g.items() if isinstance(one, dict) and "r44" in one}
        parents = {p: v for d in r44.values() for p, v in d.items()}
        bad = {p: v for p, v in parents.items()
               if not ((v["rollup"] or {}).get("n_active", 0) >= 1 and abs(v["band_sum"] - (v["down"] or 0)) < 1e-6
                       and v["seat_rows"])}
        seat_wo = sorted(s for v in parents.values() for s in v["seat_wos"])
        ck(parents and not bad and seat_wo,
           "(G6) %s R44: form-3 parents %s derive their downtime from in-service seats (rollup n_active>=1, "
           "Σ band (b-a)·k/n ≡ down_sec, seat rows present) and the synthetic world issues seat-level work orders "
           "(%s): bad=%s" % (state, sorted(parents), seat_wo, bad))
        c5 = {cls: one["c_av5"] for cls, one in g.items() if isinstance(one, dict) and "c_av5" in one}
        ck(all(v[0] is not None and v[2] is not None and abs(v[0] + v[1] - v[2]) < 1e-6 for v in c5.values()),
           "(G6) %s C-AV5: up + Σ by_state ≡ Σ|RW| at fleet level on the tested stations: %s" % (state, c5))
    # (G7) 著陸頁
    for state in ("nocal", "cal"):
        g = full["g1_" + state]
        ck(g["home_au"] == {"en": None, "zh": None} and g["landing_body_status"] == 404,
           "(G7) %s: the synthetic home (landing2 body, en/zh) prints no A/U figure and /landing/body (v1 real split) "
           "is outside phase 1 (%s) ⇒ 'landing real split' is N/A on the synthetic line: %s"
           % (state, g["landing_body_status"], g["home_au"]))
    eids = sorted(set(full["eids"]) | set(twin["eids"]) | set(prev["eids"]))
    return {"eids": eids, "n_renders": full["n_renders"] + twin["n_renders"] + prev["n_renders"], "report": report}


def _num_eq(a, b, tol=1e-9):
    if a is None or b is None or len(a) != len(b):
        return a == b
    return all((x is None and y is None) or (x is not None and y is not None and abs(x - y) <= tol)
               for x, y in zip(a, b))


def _main_round_only(a, ck, n, fails, t_all):
    """開發用捷徑（`--only-synth-round`）：只跑 (G) synth_round 與 (I3)（整合子進程略過）。完整驗收一律跑全檔。"""
    rnd = synth_round(ck)
    _i3_report(ck, _demo_marks(), _round_fleet(), {"synth_round": (rnd["eids"], rnd["n_renders"])})
    print("[wiring] --only-synth-round: %d checks, %d failed, %.0fs" % (n[0], len(fails), time.time() - t_all))
    return 0 if not fails else 1


def _round_fleet():
    """合成機隊（只在開發捷徑用；完整驗收取整合子進程回報的機隊）。"""
    code = ("_freeze_clock(ARG['anchor'])\nos.chdir(TD)\nimport wsynth_launch as L\nL.boot(out=lambda s: None)\n"
            "import wsynth_io as IO\nR['fleet'] = sorted(a['asset_id'] for a in IO.world().ctx.fleet)\n")
    td = tempfile.mkdtemp(prefix="wp7_fleet_")
    anchor = datetime.datetime.now().replace(second=0, microsecond=0)
    res, _r = _run_child(code, REPO, _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": os.path.join(td, "state"),
                                                "BOARD_SYNTH_NOW": anchor.isoformat()}), {"anchor": anchor.isoformat()})
    return res["fleet"]


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# (H) SL2-A 合成平面圖（SPEC-SYNTHLINE-PHASE2 §2.1；2026-10-02）
# ─────────────────────────────────────────────────────────────────────────────
#: 合成進程裡任何「讀真平面圖／線稿／產生器／DXF」的路徑都必須被擋（C-SY1／C-SY15）。比對的是被 open 的路徑字串。
HOME_FORBIDDEN_OPEN = ("layout_eid_xy", "layout.svg", "_gen_layout", ".dxf", ".DXF", "_LAYOUT-")
HOME_FORBIDDEN_MODS = ("_gen_layout",)

_HOME_CHILD = r'''
import re, hashlib, builtins
OPENED = []
_bo, _io0, _oo = builtins.open, io.open, os.open
def _recw(fn):
    def w(file, *a, **k):
        OPENED.append(str(file))
        return fn(file, *a, **k)
    return w
builtins.open = _recw(_bo); io.open = _recw(_io0); os.open = _recw(_oo)   # 開機之前就裝（import 期也算）
_freeze_clock(ARG["anchor"])
os.chdir(TD)
import wsynth_launch as L
L.boot(out=lambda s: None)
app = L.import_app(out=lambda s: None)
L.prebuild_world(out=lambda s: None)
import board_data as wd
import wsynth_layout as WL
from fastapi.testclient import TestClient
from markupsafe import escape as _mesc
c = TestClient(app.app, raise_server_exceptions=False)
def get(u):
    assert "source=live" not in u
    c.cookies.clear()                       # `?ui=` 會寫 cookie：每個請求獨立、不互相污染
    return c.get(u)
I18 = app.I18N
MODE = ARG["mode"]
eqb = get("/equipment/body?lang=en")        # 暖設備板 ⇒ 狀態層（/landing/state）有材料
R["eq_status"] = eqb.status_code
def boxes_of(h):
    # 平面圖上的機台方塊：<g class="mach …" data-eid=… data-b=…> 緊接 <rect class="body" … fill="…">
    out = {}
    for m in re.finditer(r'<g class="mach[^"]*"\s+data-eid="([^"]+)" data-b="([^"]*)".*?<rect class="body" x="([^"]+)" '
                         r'y="([^"]+)"\s+width="([^"]+)" height="([^"]+)"[^>]*?fill="([^"]*)"', h, re.S):
        out[m.group(1)] = {"b": m.group(2), "x": float(m.group(3)), "y": float(m.group(4)),
                           "w": float(m.group(5)), "h": float(m.group(6)), "fill": m.group(7)}
    return out
if MODE == "full":
    lay = app.ld_layout()
    doc = WL.cached_doc()
    R["lay"] = {"ok": lay.get("ok"), "err": lay.get("err"), "n_boxes": len(lay.get("boxes") or ()),
                "unplaced": len(lay.get("unplaced") or ()), "excluded": len(lay.get("excluded") or ()),
                "reviewed": lay.get("reviewed"), "status": lay.get("status"), "plan": lay.get("plan"),
                "frames": len(lay.get("frames") or ()), "vw": lay.get("vw"), "vh": lay.get("vh")}
    R["catalog"] = sorted(e for _a, _n, e in app.wem.EQMT_CATALOG)
    R["doc_md5"] = WL.doc_md5(doc)
    R["doc_n"] = len(doc["machines"])
    # D-LY3：調度距離讀口與首頁取同一份文件（副本）；stamp＝合成鍵
    wdoc, wst = wd.load_layout_doc()
    R["wd"] = {"status": wst, "md5": WL.doc_md5(wdoc) if wdoc else None, "stamp": list(wd.layout_doc_stamp() or ()),
               "copy": wdoc is not doc, "path_ignored": wd.load_layout_doc(wd.LAYOUT_DOC_PATH)[1]}
    R["wl_stamp"] = list(WL.doc_stamp())
    # 一次就地改動副本不得影響共用物件
    if wdoc:
        wdoc["machines"].clear()
    R["shared_intact"] = len(WL.cached_doc()["machines"]) == R["doc_n"]
    # ①：首頁 en／zh × Laptop(15)／Projector(2)：整頁與 body 片段
    H = {}
    for lg in ("en", "zh"):
        for ui in ("15", "2"):
            for u in ("/", "/landing2/body"):
                r = get("%s?lang=%s&ui=%s" % (u, lg, ui))
                h = r.text
                bx = boxes_of(h)
                H["%s|%s|%s" % (u, lg, ui)] = {
                    "status": r.status_code, "errcard": 'class="errcard"' in h, "draft": 'class="draftchip"' in h,
                    "traceback": "Traceback" in h, "n_mach": len(bx),
                    "na": h.count(str(_mesc(I18[lg]["ld_q4_synth_na"]))), "zreg": h.count('<path class="zreg '),
                    "frames": h.count('<rect class="pframe"'), "panes": h.count('<section class="board-panel'),
                    "kreg": h.count('<path class="kreg'), "kchip": h.count('<g class="kchip"'),
                    "bytes": len(r.content)}
                if u == "/landing2/body" and lg == "en" and ui == "15":
                    BODY = h
                    BOX = bx
    R["home"] = H
    # 選機：右欄機況卡（mcard 未隱藏、摘要隱藏）
    sel = sorted(BOX)[0] if BOX else "EID-00000"
    hs = get("/landing2/body?lang=en&eid=%s" % sel).text
    mm = re.search(r'<div class="mcard" id="mc-%s"[^>]*>' % re.escape(sel), hs)
    R["sel"] = {"eid": sel, "card": mm.group(0) if mm else None,
                "summary_hidden": bool(re.search(r'<div id="ld-summary"\s+hidden', hs)),
                "sel_cls": bool(re.search(r'<g class="mach[^"]*\bsel\b[^"]*"\s+data-eid="%s"' % re.escape(sel), hs))}
    # ⑤：/landing/state 與首頁一致（同一組機台、同一個桶）
    st = get("/landing/state?lang=en").json()
    smap = {m["eid"]: m["bucket"] for m in (st.get("machines") or ())}
    R["state"] = {"warming": st.get("warming"), "n": len(smap),
                  "missing_on_map": sorted(set(smap) - set(BOX))[:5], "extra_on_map": sorted(set(BOX) - set(smap))[:5],
                  "bucket_diff": sorted(e for e in BOX if smap.get(e) != BOX[e]["b"])[:5],
                  "counts": st.get("counts"), "zh_status": get("/landing/state?lang=zh").status_code}
    # 顏色＝桶色（DOWN 紅、生產綠……；無色桶＝空心）
    col = app.LD_BUCKET_COLOR
    R["fill_bad"] = sorted(e for e, v in BOX.items() if v["fill"] != (col.get(v["b"]) or "none"))[:5]
    R["buckets"] = sorted(set(v["b"] for v in BOX.values()))
    # ②：渲染後（grow／parity 之後）不重疊；區標不壓機台、每區一個標
    ctx = app._ld_ctx(None, "synth", "en", "", "", 0)
    rows = ctx["rows"]
    rb = [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])) for r in rows]
    # 相接（同一站的機台、工位組）不是重疊：座標 6 位捨入 × 畫布 1000 ⇒ 相接邊可有 ≤1e-3 單位的假交疊；
    #   門檻 0.01 viewBox 單位（≈0.01 px），任何真重疊都遠大於它。
    def _ov(a, b, tol=0.01):
        return (min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]) > tol and
                min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > tol)
    R["rendered_overlap"] = [[rows[i]["eid"], rows[j]["eid"]] for i in range(len(rb)) for j in range(i + 1, len(rb))
                             if _ov(rb[i], rb[j])][:5]
    zl = ctx["zlabels"]
    R["zlabels"] = sorted(z["key"] for z in zl)
    R["zlabel_hits"] = [[z["key"], rows[i]["eid"]] for z in zl for i in range(len(rb))
                        if app._ld_boxes_overlap(app._ld_label_box(z["label"], z["x"], z["y"]), rb[i])][:5]
    R["zones"] = sorted(set(z["key"] for z in ctx["zregions"]))
    # (H11) sl2 控制區：用**渲染後**的方塊（grow／parity 之後）與首頁真正送出的鏡頭（`ld_viewbox`，含區框點）
    _vb = ctx["ld_viewbox"]
    _cam = (float(_vb["x"]), float(_vb["y"]), float(_vb["w"]), float(_vb["h"]))
    R["ctrl"] = {"zone": [round(v, 2) for v in WL.control_zone(_cam)], "cam": [round(v, 2) for v in _cam],
                 "hits": [rows[i]["eid"] for i in WL.control_zone_hits(rb, cam=_cam)][:5],
                 "version": WL.LAYOUT_VERSION, "n_kun_hulls": len(ctx["kun_hulls"]),
                 "n_kun_notes": len(ctx["kun_notes"])}
    R["mods"] = sorted(m for m in sys.modules if m in ARG["forbidden_mods"])
else:
    st = get("/landing/state?lang=en").json()
    sm = {m["eid"]: m for m in (st.get("machines") or ())}
    h = get("/landing2/body?lang=en").text
    bx = boxes_of(h)
    e = ARG["eid"]
    R["probe"] = {"warming": st.get("warming"), "state_bucket": (sm.get(e) or {}).get("bucket"),
                  "state_label": (sm.get(e) or {}).get("state_label"), "map_bucket": (bx.get(e) or {}).get("b"),
                  "map_fill": (bx.get(e) or {}).get("fill"), "down_color": app.LD_BUCKET_COLOR["down"],
                  "run_color": app.LD_BUCKET_COLOR["run"], "on_map": e in bx}
builtins.open, io.open, os.open = _bo, _io0, _oo
R["opened_bad"] = sorted(set(p for p in OPENED if any(s in p for s in ARG["forbidden_open"])))[:8]
R["n_opened"] = len(OPENED)
'''


def _home_event(anchor, days=14):
    """合成世界裡一則「停機→修復→恢復生產」事件（只挑、不造；同 `_showcase/video/find_event.py` 的條件）。
       回 `(eid, wo, t_down, t_back)`；`t_down`＝開單後 5 分鐘（未完工）、`t_back`＝修復後第一段生產結束後 5 分鐘。"""
    import collections
    sys.path.insert(0, REPO)
    import board_eqmeeting as _wem
    import wsynth_clock as _C
    import wsynth_world as _W
    w = _W.build_world(anchor)
    ctx = w["ctx"] if "ctx" in w else _W.make_context()
    by_id = {a["asset_id"]: a for a in ctx.fleet}
    cat = {e for _a, _n, e in _wem.EQMT_CATALOG}
    wos = collections.OrderedDict()
    for e in w["events"]:
        if e["kind"].startswith("wo."):
            wos.setdefault(e["work_order_no"], []).append(e)
    prod = collections.defaultdict(list)
    for s in w["segments"]:
        if s["designation"] == "Production":
            prod[s["eid"]].append((_W._dtp(s["start"]), _W._dtp(s["end"])))
    spans = collections.defaultdict(list)            # eid -> [(open, terminal or None)]
    for no, ev in wos.items():
        if ev[0]["kind"] != "wo.open":
            continue
        term = [_C.from_utc_z(x["at"]) for x in ev if x["kind"] == "wo.status"
                and x["to_status"] in ("COMPLETED", "CANCELLED", "VOIDED", "CLOSED")]
        spans[ev[0]["asset_id"]].append((no, _C.from_utc_z(ev[0]["at"]), min(term) if term else None))
    best = None
    for no, ev in wos.items():
        op = ev[0]
        if op["kind"] != "wo.open" or op["work_type"] != "REACTIVE":
            continue
        aid = op["asset_id"]
        a = by_id.get(aid)
        if aid not in cat or not a or a["nests"] or a["children"] or a["parent_asset_id"] or op["scope"]:
            continue
        ts = {}
        for x in ev:
            if x["kind"] == "wo.status":
                ts.setdefault(x["to_status"], _C.from_utc_z(x["at"]))
        if "COMPLETED" not in ts:
            continue
        t_open = _C.from_utc_z(op["at"])
        if t_open < anchor - datetime.timedelta(days=days):
            continue
        pra = [x["value"] for x in ev if x["kind"] == "wo.field" and x["field"] == "production_resumed_at"]
        t1 = _C.from_utc_z(pra[0]) if pra else ts["COMPLETED"]
        t_down = t_open + datetime.timedelta(minutes=5)
        if t_down >= ts["COMPLETED"]:
            continue
        after = sorted(e2 for s2, e2 in prod[aid] if s2 >= t1)
        if not after:
            continue
        t_back = after[0] + datetime.timedelta(minutes=5)
        if t_back > anchor - datetime.timedelta(hours=1):
            continue
        clash = [n for n, o, tt in spans[aid] if n != no and o < t_back and (tt is None or tt > t_down - datetime.timedelta(days=1))]
        if clash:
            continue
        if best is None or t_open > best[1]:
            best = (aid, t_open, no, t_down.replace(microsecond=0), t_back.replace(second=0, microsecond=0)
                    + datetime.timedelta(minutes=1))
    if best is None:
        return None
    return best[0], best[2], best[3], best[4]


def _home_child(mode, anchor, extra=None):
    sys.path.insert(0, REPO)
    import wsynth_cmms as WC
    td = tempfile.mkdtemp(prefix="sl2a_home_%s_" % mode)
    with WC.serve(WC.GeneratorSource(), now_fn=lambda: anchor) as base:
        env = _base_env({"BOARD_SOURCE": "synth", "BOARD_STATE_DIR": os.path.join(td, "state"),
                         "BOARD_SYNTH_NOW": anchor.isoformat(), "BOARD_CMMS_BASE_URL": base,
                         "BOARD_CMMS_TOKEN": WC.DEFAULT_TOKEN})
        arg = {"mode": mode, "anchor": anchor.isoformat(), "forbidden_open": list(HOME_FORBIDDEN_OPEN),
               "forbidden_mods": list(HOME_FORBIDDEN_MODS)}
        arg.update(extra or {})
        res, _r = _run_child(_HOME_CHILD, REPO, env, arg)
    return res


def _home_case(ck):
    """(H1–H11) SL2-A：合成首頁平面圖（本體、狀態燈、右欄卡、輪詢一致、版面不重疊、區標不壓機、零真檔讀取、零綑框、控制區淨空）。"""
    anchor = datetime.datetime.now().replace(second=0, microsecond=0)
    h = _home_child("full", anchor)
    lay = h["lay"]
    ncat = len(h["catalog"])
    ck(lay["ok"] is True and lay["n_boxes"] == ncat and lay["unplaced"] == 0 and lay["excluded"] == 0
       and lay["reviewed"] is True and lay["status"] == "synthetic" and lay["plan"] == "" and lay["frames"] >= 1,
       "(H1) synth ld_layout(): synthetic floor plan ok, one block per roster machine (%d/%d), nothing unplaced, "
       "reviewed (no Draft chip), no building line art, parent frames=%s: %s"
       % (lay["n_boxes"], ncat, lay["frames"], {k: lay[k] for k in ("err", "status", "vw", "vh")}))
    bad = {k: v for k, v in h["home"].items()
           if not (v["status"] == 200 and not v["errcard"] and not v["draft"] and not v["traceback"]
                   and (v["n_mach"] == ncat if k.startswith("/landing2/body") else True))}
    ck(len(h["home"]) == 8 and not bad,
       "(H2) / and /landing2/body (en/zh x Laptop/Projector) answer 200 with the floor plan (no error card, no Draft "
       "chip, %d machine blocks on every body): %s" % (ncat, bad))
    body = {k: v for k, v in h["home"].items() if k.startswith("/landing2/body")}
    ck(all(v["na"] >= 1 and v["zreg"] >= 4 and v["panes"] >= 4 for v in body.values()),
       "(H3) home body: four area regions, the four split panes render, and panes without a phase-1 data leg print "
       "the existing 'not provided on the synthetic line' sentence: %s"
       % {k: (v["na"], v["zreg"], v["panes"]) for k, v in body.items()})
    sel = h["sel"]
    ck(sel["card"] and "hidden" not in sel["card"] and sel["summary_hidden"] and sel["sel_cls"],
       "(H4) selecting a machine (%s) opens its right-hand status card (card shown, summary hidden, block marked "
       "selected): %s" % (sel["eid"], sel))
    st = h["state"]
    ck(st["warming"] is False and st["n"] == ncat and not st["missing_on_map"] and not st["extra_on_map"]
       and not st["bucket_diff"] and st["zh_status"] == 200,
       "(H5) /landing/state (5-min poll) agrees with the home body: same machines, same bucket per machine "
       "(warming=%s, n=%s, counts=%s): %s" % (st["warming"], st["n"], st["counts"],
                                              {k: st[k] for k in ("missing_on_map", "extra_on_map", "bucket_diff")}))
    ck(not h["fill_bad"] and len(h["buckets"]) >= 2,
       "(H6a) every block's fill is its bucket colour (buckets on the map: %s): %s" % (h["buckets"], h["fill_bad"]))
    ck(not h["rendered_overlap"] and h["zones"] == sorted(h["zlabels"]) and len(h["zones"]) == 4
       and not h["zlabel_hits"],
       "(H7) rendered geometry (after grow/size-parity): no two blocks overlap; four areas, one label each, no label "
       "on a block: overlap=%s zones=%s labels=%s hits=%s"
       % (h["rendered_overlap"], h["zones"], h["zlabels"], h["zlabel_hits"]))
    wdr = h["wd"]
    ck(wdr["status"] == "ok" and wdr["md5"] == h["doc_md5"] and wdr["stamp"] == h["wl_stamp"]
       and wdr["stamp"][:2] == ["synth-layout", wdr["stamp"][1]] and wdr["copy"] and h["shared_intact"]
       and wdr["path_ignored"] == "ok",
       "(H8) D-LY3: dispatch-distance reader (wd.load_layout_doc / layout_doc_stamp) serves a copy of the same "
       "in-memory synthetic doc as the home page (stamp %s), even when handed the real path: %s"
       % (wdr["stamp"], wdr))
    ck(not h["opened_bad"] and not h["mods"],
       "(H9) C-SY1/C-SY15 tripwire: while booting the synth process and serving the home (%d opens), no real floor-plan "
       "/ line-art / DXF / layout-generator path was opened and _gen_layout was never imported: %s %s"
       % (h["n_opened"], h["opened_bad"], h["mods"]))
    # (H10) SPEC-SYNTHLINE-PHASE2 §2.5-8：合成人力未提供期間，首頁零綑框、零人數 chip、零「未畫」句
    kk = {k: (v["kreg"], v["kchip"]) for k, v in h["home"].items()}
    ck(all(a == 0 and b == 0 for a, b in kk.values()) and h["ctrl"]["n_kun_hulls"] == 0
       and h["ctrl"]["n_kun_notes"] == 0,
       "(H10) synthetic staffing not provided => the home draws no bundle frame and no head-count chip "
       "(kreg, kchip per page): %s; ctx hulls=%s notes=%s" % (kk, h["ctrl"]["n_kun_hulls"], h["ctrl"]["n_kun_notes"]))
    # (H11) sl2：首頁右上角的縮放控制區（鏡頭寬 CTRL_W_FRAC × 高 CTRL_H_FRAC）內沒有任何渲染後的方塊
    ck(not h["ctrl"]["hits"],
       "(H11) %s control zone %s of the served camera %s holds no rendered block: %s"
       % (h["ctrl"]["version"], h["ctrl"]["zone"], h["ctrl"]["cam"], h["ctrl"]["hits"]))
    # (H6b) 狀態燈跟著世界走：同一台機在停機中＝紅、修復並恢復生產後＝綠（事件只挑、不造）
    evt = _home_event(anchor)
    ck(evt is not None, "(H6b0) the synthetic world has a down-then-back REACTIVE event on a plain roster machine in the "
       "last 14 days: %s" % (evt,))
    if evt:
        eid, wo, t_down, t_back = evt
        pd = _home_child("probe", t_down, {"eid": eid})["probe"]
        pb = _home_child("probe", t_back, {"eid": eid})["probe"]
        print("[wiring] (H6b) WO %s on %s: down at %s -> %s; back at %s -> %s"
              % (wo, eid, t_down, {k: pd[k] for k in ("state_bucket", "map_bucket", "map_fill")},
                 t_back, {k: pb[k] for k in ("state_bucket", "map_bucket", "map_fill")}), flush=True)
        ck(pd["on_map"] and pd["warming"] is False and pd["state_bucket"] == "down" and pd["map_bucket"] == "down"
           and pd["map_fill"] == pd["down_color"],
           "(H6b) world time %s (WO %s open): %s is DOWN, red on the map and in /landing/state: %s"
           % (t_down, wo, eid, pd))
        ck(pb["on_map"] and pb["state_bucket"] == "run" and pb["map_bucket"] == "run"
           and pb["map_fill"] == pb["run_color"],
           "(H6c) world time %s (repaired, producing again): %s is back to green (run): %s" % (t_back, eid, pb))
    return h


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE, help="改動前基準 commit（預設＝DEFAULT_BASE；見其註解）")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="no-op（本檔本身就是自測；讓 `--selftest` 統一呼叫形可用）")
    ap.add_argument("--skip-pages", action="store_true", help="略過 (P) 頁面位元組比對（最慢的一段）")
    ap.add_argument("--only-integ", action="store_true",
                    help="開發用：只跑 (I) 整合子進程（S27–S36）；略過 E／P／N／S／L（完整驗收不得用）")
    ap.add_argument("--calendar", action="store_true", help="開發用：配 --only-integ，渲染前先跑 tick calendar")
    ap.add_argument("--only-assist", action="store_true",
                    help="開發用：只跑 (SA) 錄影助理開關；略過其餘（完整驗收不得用）")
    ap.add_argument("--only-synth-round", action="store_true",
                    help="開發用：只跑 (G) synth_round＋(I3)；略過其餘（完整驗收不得用）")
    ap.add_argument("--only-home", action="store_true",
                    help="開發用：只跑 (H) SL2-A 合成平面圖；略過其餘（完整驗收不得用）")
    a = ap.parse_args(argv)
    import core_paths as _wp_pub
    if _wp_pub.is_public_tree(REPO):
        # 本自測要對「來源 repo」做 git archive 取基準 commit 逐位元比對；公開樹沒有來源 repo 的歷史 => 整支跳過、不算失敗。
        print("wsynth_wiring_selftest: skipped: needs git history of the private source repo (public tree)")
        return 0
    n, fails = [0], []

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            fails.append(msg)
            print("FAIL [%d] %s" % (n[0], msg), flush=True)
        elif a.v:
            print("ok   [%d] %s" % (n[0], msg), flush=True)

    t_all = time.time()
    fp0 = _repo_fingerprint()
    with open(os.path.join(REPO, "_fixtures", "monthly_daily_docs_sample.json"), encoding="utf-8") as f:
        ledger_sample = json.load(f)["docs"][0]            # 合成日帳（fixture 內 `_synthetic`）；只在父進程讀

    if a.only_integ:
        return _main_integ_only(a, ck, n, fails, t_all)
    if a.only_synth_round:
        return _main_round_only(a, ck, n, fails, t_all)
    if a.only_home:
        _home_case(ck)
        print("[wiring] %d checks, %d failed, %.0fs (--only-home)" % (n[0], len(fails), time.time() - t_all))
        print("wsynth_wiring_selftest: %s" % ("ALL PASS" if not fails else "FAIL"))
        return 0 if not fails else 1
    if a.only_assist:
        _assist_report(_assist_case(), ck)
        print("[wiring] %d checks, %d failed, %.0fs (--only-assist)" % (n[0], len(fails), time.time() - t_all))
        print("wsynth_wiring_selftest: %s" % ("ALL PASS" if not fails else "FAIL"))
        return 0 if not fails else 1
    base_tree = tempfile.mkdtemp(prefix="wp4a_base_")
    _extract_base(a.base, base_tree)
    print("[wiring] base tree %s extracted" % a.base, flush=True)

    # (E) 真值表：兩棵樹
    tt = _truth_tables(base_tree, a.v)
    b, w = tt["base"], tt["new"]
    ck(b.get("_n_keys") == w.get("_n_keys"), "(E0) same number of truth-table keys (%s vs %s)"
       % (b.get("_n_keys"), w.get("_n_keys")))
    diff = sorted(k for k in set(b) | set(w) if b.get(k) != w.get(k))
    ck(not diff, "(E1) truth tables identical base vs new: %d keys compared; diff=%s"
       % (len(b), [(k, b.get(k), w.get(k)) for k in diff[:8]]))
    n_eq_tree = len(b) - 1
    print("[wiring] (E) two-tree truth table: %d judgements compared" % n_eq_tree, flush=True)

    # (E') 行內字面運算式 vs 新 helper（新樹、非合成）
    sd = tempfile.mkdtemp(prefix="wp4a_pure_")
    inputs = [list(x) if isinstance(x, tuple) else ("__bytes__" if isinstance(x, bytes) else x) for x in _INPUTS]
    pure, _r = _run_child(_PURE_CHILD, REPO, _base_env(dict(_POISON, BOARD_SOURCE="demo", BOARD_STATE_DIR=sd,
                                                            BOARD_SYNTH_ASSISTANT="1", BOARD_PUBLIC_ASSISTANT="1")),
                          {"inputs": inputs})
    ck(not pure.get("bad"), "(E2) inline-expression equivalence (%s checks): %s" % (pure.get("n"), pure.get("bad")))
    ck(pure.get("wsynth_loaded") == [], "(E3) non-synth app import loads no wsynth_ module: %s" % pure.get("wsynth_loaded"))
    n_eq_pure = pure.get("n") or 0

    # (S30b) WP-4b R-B1：Gen2 展開共用函式抽出前後，既有兩處（_demo_expand_fleet／_expand_gen2_yield_live）逐位元等價
    xo = {}
    for tag, tree in (("base", base_tree), ("new", REPO)):
        sd = tempfile.mkdtemp(prefix="wp4b_x_%s_" % tag)
        xo[tag], _r = _run_child(_EXPAND_CHILD, tree, _base_env(dict(_POISON, BOARD_SOURCE="demo",
                                                                     BOARD_STATE_DIR=sd)), {})
    ck(json.dumps(xo["base"], sort_keys=True) == json.dumps(xo["new"], sort_keys=True)
       and len(xo["new"].get("live") or []) == 21 and xo["new"].get("calls"),
       "(S30b) Gen2 expansion refactor: _demo_expand_fleet + _expand_gen2_yield_live outputs (and the yield "
       "fetch calls they make) identical base vs new over %d fleets x 3 modes: diff=%s"
       % (len(xo["new"].get("live") or []) // 3,
          [k for k in set(xo["base"]) | set(xo["new"]) if xo["base"].get(k) != xo["new"].get(k)]))

    # (P) 頁面位元組
    n_pages = 0
    if not a.skip_pages:
        urls, pg = _pages(base_tree)
        pb, pw = pg["base"]["pages"], pg["new"]["pages"]
        pdiff_all = [u for u in urls if pb.get(u)[:2] != pw.get(u)[:2]]
        pexp = [u for u in pdiff_all if _expected_page_diff(u, pb.get(u), pw.get(u))]
        pdiff = [u for u in pdiff_all if u not in pexp]
        n_pages = len(urls)
        ck(not pdiff, "(P1) demo page bytes identical base vs new: %d URLs (%d listed expected diffs: %s); diff=%s"
           % (len(urls), len(pexp), sorted({u.partition("?")[0] for u in pexp}),
              [(u, pb.get(u), pw.get(u)) for u in pdiff[:6]]))
        ck(all(v[0] == 200 for v in pw.values()), "(P2) all %d page requests answer 200 (non-synth)" % len(urls))
        synth_urls = [u for u in urls if "source=synth" in u]
        bogus = {u.replace("source=synth", "source=bogus"): u for u in synth_urls}
        ck(all(pw[u][:2] == pb[u][:2] or u in pexp for u in synth_urls),
           "(P3) non-synth + ?source=synth: responses identical to pre-change handling (%d URLs)" % len(synth_urls))
        ck(pg["new"]["wsynth_loaded"] == [], "(P4) no wsynth_ module loaded in the non-synth page process: %s"
           % pg["new"]["wsynth_loaded"])
        print("[wiring] (P) %d URLs compared (%d with source=synth, %d unknown-source twins)"
              % (len(urls), len(synth_urls), len(bogus)), flush=True)

    # (N) 非合成進程拒用合成日帳
    ns, ndays = _nonsynth_ledger(ledger_sample)
    ck(ns["docs_sources"] == ["live"], "(N1) non-synth _ledger_docs drops synth docs: %s" % ns["docs_sources"])
    ck(ns["mr_sources"] == ["live"], "(N2) non-synth _mr_docs('live') drops synth docs: %s" % ns["mr_sources"])
    ck(ns["doc_synth"] is None and ns["doc_live"] == "live", "(N3) non-synth _ledger_doc: synth=None, live kept")
    ck(ndays["synth"] not in ns["rates_days"], "(N4) non-synth /equipment rate reader ignores synth doc: %s"
       % ns["rates_days"])
    ck(ns["persist"] == [False, True, True], "(N5) non-synth should_persist synth/live/' LIVE ': %s" % ns["persist"])
    ck(ns["fm_ran"], "(N6) _freeze_monthly.run(dry_run) runs against the app reader")
    ck(ns["wsynth_loaded"] == [], "(N7) no wsynth_ module loaded: %s" % ns["wsynth_loaded"])

    # (S) 合成進程
    s, sdays, sr = _synth(ledger_sample)
    ck(s["boot_ok"] is True and all(ok for _n, ok in s["boot_checks"]), "(S1) tripwire all pass: %s" % s["boot_checks"])
    ck(s["driver_is_stub"], "(S2) database driver is the tripwire stub")
    bad_p1 = {u: v for u, v in s["pages"].items() if u in PHASE1 and not (v[0] == 200 and not v[2])}
    ck(not bad_p1, "(S3) phase-1 pages 200 without exception text: %s" % bad_p1)
    bad_np = {u: v for u, v in s["pages"].items() if u in NOT_PHASE1 and v[0] != 404}
    ck(not bad_np, "(S4) non-phase-1 routes 404 in synth: %s" % bad_np)
    ck(all(s["pages"]["/utilization?" + q][0] == 200 and s["pages"]["/utilization?" + q][2]
           for q in ("source=demo", "source=snapshot", "source=bogus")),
       "(S5) ?source=demo/snapshot/bogus collapse to synth (Q-B)")
    ck(s["v1_closed"] == [True, True], "(S6) v1 UI closed (?ui=v1 and cookie render v2): %s" % s["v1_closed"])
    pv = s["priv"]
    ck(pv == {"hermes_configured": False, "assistant_ui": False, "jev_router_on": False,
              "jev_shadow_enabled": False, "gw_kick": False, "dx_run_live": [None, "not_configured"],
              "dx_confirmed": [], "env_enabled": False, "env_poller_on": False, "hermes_post": None,
              "synth_assist": False, "assist_configured": False} and s["dock_off"] is False,
       "(S7) private-machine features all off (filming assistant flag unset => off, no dock): %s dock=%s"
       % (pv, s["dock_off"]))
    ck(not s["popen"], "(S8) no external process started: %s" % s["popen"])
    bad_thr = [t for t in s["threads"] if t == "board-env-sensor" or t.startswith("jev-") or "hermes" in t.lower()]
    ck(not bad_thr, "(S9) no iMonnit/Jev/Hermes threads: %s" % bad_thr)
    src = s["src"]
    ck(src["DEFAULT_SOURCE"] == "synth" and src["_LIVE_SRC"] == "synth" and src["_SYNTH_PROC"] is True,
       "(S10) synth process source constants: %s" % {k: src[k] for k in ("DEFAULT_SOURCE", "_LIVE_SRC", "_SYNTH_PROC")})
    ck(src["route"] == ["synth"] * 6 and src["key"] == ["synth"] * 6, "(S11) every source collapses to synth")
    ck(src["is_live_synth"] is True and src["is_live_demo"] is False, "(S12) _is_live in synth")
    ck(src["ud_mode"] == ["synth", "synth"], "(S13) /updown mode is synth (never live): %s" % src["ud_mode"])
    ck(src["should_persist"] == [True, False, False], "(S14) synth should_persist synth/live/demo: %s" % src["should_persist"])
    ck(src["qs"] == ["source=synth&lang=en", "a=1&source=synth&source=synth&b=%20", "lang=en"],
       "(S15) query rewrite: %s" % src["qs"])
    ck(src["a11"] is True, "(S16) A11 live path enabled in synth (R-11)")
    ck(src["ui_values"] == ["15", "2"], "(S17) UI values without v1: %s" % src["ui_values"])
    ck(src["export_known"] == [True, True, False], "(S18) export accepts synth only in synth: %s" % src["export_known"])
    mc = s["mcp"]
    ck(mc["source"] == "synth" and mc["sv_error"] == "unavailable" and mc["sv_reason"] == "synth_not_provided"
       and mc["sv_disc"] == "synth" and mc["sv_conf"] == "low" and mc["kc_error"] == "unavailable"
       and mc["kc_disc"] == "synth" and mc["ping_disc"] == "synth" and all(mc["wrapped"]),
       "(S19) MCP: source synth, MES tools + knowledge card unavailable with disclosure: %s" % mc)
    lg = s["ledger"]
    ck(lg["docs_sources"] == ["synth"] and lg["doc_synth"] == "synth" and lg["doc_live"] is None
       and sdays["live"] not in lg["rates_days"],
       "(S20) synth ledger readers accept only synth docs: %s" % lg)
    ck(s["rotate"]["gone"] == ["2025-01", "2025-02"] and len(s["rotate"]["left"]) == 14
       and "2024-12" in s["rotate"]["left"],
       "(S21) monthly snapshots rotate to 13 synth snapshots in synth; a non-synth snapshot is "
       "neither listed nor rotated (Q-C): %s" % s["rotate"])
    sites = s["sites"]
    ck(len(sites) == len(s["import_time_sites"]) and all(v[1] for v in sites.values()),
       "(S22) core_paths IMPORT_TIME_SITES (%d) all under the redirected root: %s"
       % (len(s["import_time_sites"]), {k: v for k, v in sites.items() if not v[1]}))
    req_open = [p for ph, p in s["opened"] if ph == "request"]
    bad_open = [os.path.relpath(p, REPO) for p in req_open
                if any(part in os.path.relpath(p, REPO) for part in FORBIDDEN_PARTS)]
    ck(not bad_open, "(S23) no fixture/private/real-layout file opened while serving: %s" % bad_open)
    R_LAZY = s.get("lazy") or {}
    ck(R_LAZY.get("diag_loaded") is False and R_LAZY.get("dp_loaded") is False,
       "(S26) board_diag / board_downpanel never imported while serving in synth: %s" % R_LAZY)
    boot_open = [os.path.relpath(p, REPO) for ph, p in s["opened"] if ph == "boot"
                 and any(part in os.path.relpath(p, REPO) for part in FORBIDDEN_PARTS)]
    unknown_boot = [p for p in boot_open if not any(k in p for k in KNOWN_IMPORT_TIME)]
    ck(not unknown_boot, "(S24) import-time opens limited to the reported known list: %s" % unknown_boot)
    in_repo_writes = [p for p in s["writes"] if os.path.normcase(p).startswith(os.path.normcase(REPO))]
    in_repo_fs = [x for x in s["fsops"] if os.path.normcase(x[1]).startswith(os.path.normcase(REPO))]
    ck(not in_repo_writes and not in_repo_fs, "(S25) no write/rename/mkdir inside the repo: %s %s"
       % (in_repo_writes, in_repo_fs))
    leaks = s["leaks"]
    print("[wiring] (S) unadapted real-source calls caught by the driver stub (WP-3 scope): %d %s"
          % (len(leaks), sorted(set(tuple(x) for x in leaks))), flush=True)
    print("[wiring] (S) import-time opens by modules outside WP-4a (reported): %s" % sorted(set(boot_open)), flush=True)

    # (SA) 錄影助理開關開（Fable 2026-10-02）：合成進程＋loopback 假 gateway
    _assist_report(_assist_case(), ck)

    # (I) 整合：合成進程＋WP-3 資料層＋loopback 假 cmms（凍結時鐘）
    #   WP-7 ③：同一套判定跑兩次——日曆缺席（既有狀態）與「渲染前先跑 tick calendar」（日曆已寫）。
    integ = _integrated(calendar=False)
    _integ_report(integ, ck)
    integ_cal = _integrated(calendar=True)
    ck(integ["cal_status"] == "absent" and integ_cal["cal_status"] == "ok"
       and (integ_cal.get("cal_tick") or {}).get("rc") == 0,
       "(I10) WP-7: the integration child ran once with no calendar (status=%s) and once after the tick wrote "
       "one (status=%s, tick=%s)" % (integ["cal_status"], integ_cal["cal_status"], integ_cal.get("cal_tick")))
    _integ_report(integ_cal, ck, "[cal] ")

    # (H) SL2-A 合成平面圖（首頁本體、狀態燈跟世界走、右欄卡、輪詢一致、幾何、零真檔讀取）
    _home_case(ck)

    # (G) WP-7 synth_round：確定性／前綴穩定／絆線／大牌／G1 兩種日曆狀態／R44／著陸頁
    rnd = synth_round(ck)
    # (I3) WP-7 ①：demo-only EID 對**所有**合成渲染（兩個整合子進程＋synth_round 三個子進程）的聯集
    _i3_report(ck, _demo_marks(), integ["fleet"],
               {"integ": (integ["all_eids_every"], integ["n_renders"]),
                "integ[cal]": (integ_cal["all_eids_every"], integ_cal["n_renders"]),
                "synth_round": (rnd["eids"], rnd["n_renders"])})

    # (T) WP-6 tick 接線
    tk = _tick_case()
    trs = {r["step"]: r for r in (tk.get("summary") or {}).get("results", [])}
    tcal = trs.get("calendar", {})
    ck(trs.get("lastprod", {}).get("ok") is True and trs.get("lastprod", {}).get("status") == "written"
       and tcal.get("status") == "written" and tk.get("cal_status") == "ok"
       and (tk.get("summary") or {}).get("rc") == 0,
       "(T1) tick round in a synthetic process: last_production written; calendar written and accepted by "
       "load_prod_calendar (g3 world: no segment ends on the 08:00 production-day boundary); round rc 0: "
       "calendar=%s app=%s rc=%s" % (tcal.get("status"), tk.get("cal_status"), (tk.get("summary") or {}).get("rc")))
    ck(tk.get("cal_path_same") is True and tk.get("lp_path_same") is True,
       "(T2) the tick writes exactly the calendar / last_production paths the app reads (redirected state dir)")
    ck((tk.get("lp_ages_n") or 0) > 0, "(T3) app's last_production reader accepts the tick's state file: %s EIDs (%s)"
       % (tk.get("lp_ages_n"), tk.get("lp_meta")))
    tfz = trs.get("freeze", {})
    ck(tfz.get("ok") is True and not str(tfz.get("why")).startswith("error"),
       "(T4) monthly freeze/rotation via the app's single source runs without error: %s" % tfz)
    ck(all(tk.get("warm_allowed") or [False])
       and all(v[0] == 200 and not v[1] for v in (tk.get("warm_status") or {}).values()),
       "(T5) every wsynth_tick.WARM_PATHS entry is a phase-1 synth path answering 200: %s" % tk.get("warm_status"))
    troot = os.path.normcase(os.path.realpath(tk.get("state_root") or "?"))   # realpath：8.3 短名與長名同一
    tout = [p for p in tk.get("writes", []) + [x[1] for x in tk.get("fsops", [])]
            if not os.path.normcase(os.path.realpath(p)).startswith(troot)]
    ck(not tout, "(T6) every tick/app write lands under the synthetic state dir: %s" % tout[:4])

    # (L) 啟動器
    rc, txt, st = _launcher_case({})
    ck(rc == 0 and "--check PASS" in txt and os.path.isfile(os.path.join(st, "SYNTH_WORLD.json")),
       "(L1) launcher --check passes and creates the marker (rc=%s)" % rc)
    rc, txt, _st = _launcher_case({"BOARD_STATE_DIR": None})
    ck(rc == 2 and "BOARD_STATE_DIR is not set" in txt, "(L2) missing BOARD_STATE_DIR refused (rc=%s)" % rc)
    rc, txt, _st = _launcher_case({"BOARD_SOURCE": "demo"})
    ck(rc == 2 and "BOARD_SOURCE must be" in txt, "(L3) non-synth BOARD_SOURCE refused (rc=%s)" % rc)
    rc, txt, _st = _launcher_case({"BOARD_A11_LIVE": "0"})
    ck(rc == 2 and "a11_live" in txt, "(L4) BOARD_A11_LIVE=0 refused by app check (rc=%s)" % rc)
    rc, txt, _st = _launcher_case({"BOARD_CMMS_BASE_URL": "https://work.example.com"})
    ck(rc == 2 and "cmms_base_url" in txt, "(L5) non-loopback cmms URL refused by tripwire (rc=%s)" % rc)

    # (R) repo 未被寫入
    fp1 = _repo_fingerprint()
    changed = sorted(k for k in set(fp0) | set(fp1) if fp0.get(k) != fp1.get(k))
    ck(not changed, "(R1) repo files unchanged by the whole run: %s" % changed[:10])

    print("[wiring] equivalence comparisons: two-tree truth table %d + inline expressions %d + pages %d"
          % (n_eq_tree, n_eq_pure, n_pages))
    print("[wiring] %d checks, %d failed, %.0fs" % (n[0], len(fails), time.time() - t_all))
    print("wsynth_wiring_selftest: %s" % ("ALL PASS" if not fails else "FAIL"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
