# -*- coding: utf-8 -*-
"""wsynth_launch.py — 合成產線（synth）進程的啟動器（SPEC-SYNTHLINE C-SY3／C-SY4；WP-4a）
==========================================================================================

順序固定（違反即作廢）
----------------------
1. 檢查兩個必要的環境變數已設：
     `BOARD_SOURCE`     必須是 `synth`（去空白、不分大小寫；＝`core_source` 的進程旗標）；
     `BOARD_STATE_DIR`  執行期產物根（`core_paths`）；必須在 repo 外、由絆線再驗一次。
2. 設定進程內環境變數（**只改本進程的 `os.environ`**，不寫任何使用者或系統環境變數）：
     `BOARD_A11_LIVE=1`（R-11：長停與 R44 座推導必須走 live 路徑、不得退回過渡橋常數；
     已被明設成別的值就不覆寫——app 端的檢查會拒絕啟動，讓設定錯誤大聲失敗）。
3. 呼叫 `core_source.startup_tripwire()`（資料庫驅動替身、MES 模組尚未載入、cmms URL 白名單、
   狀態目錄與標記檔、UTC+8）；任何一條不過 ⇒ 印摘要、非零結束。
4. 印出摘要。
5. **之後才** import app。
6. 預先建好合成世界（`wsynth_io.world()`；首建約數秒），印出耗時——之後才開始接受請求
   （否則第一個請求要替整個世界付建置時間）。
7. 啟動伺服器（uvicorn）。

參數
----
  --host   綁定位址，預設 `127.0.0.1`（只綁 loopback；要對外由主機層的反向代理處理）。
  --port   埠，預設 8050。
  --check  只跑第 1–6 步（絆線、import app、預建世界），不啟動伺服器；印結果後結束（0＝通過）。

不做的事
--------
* 不安裝排程任務、不寫登錄檔或使用者／系統環境變數、不改任何 repo 內檔案。
* 不連 MES（絆線已把驅動換成替身）；cmms 只准 loopback 或 `BOARD_SYNTH_CMMS_HOSTS` 白名單。

用法：
    set BOARD_SOURCE=synth
    set BOARD_STATE_DIR=%TEMP%\\tendline_state
    python wsynth_launch.py --check
    python wsynth_launch.py --port 8050
"""
import argparse
import os
import sys

import core_source as _wrs

ENV_STATE_DIR = "BOARD_STATE_DIR"          # 與 core_paths.ENV_STATE_DIR 同名（絆線內部會再以 core_paths 驗）
ENV_A11_LIVE = "BOARD_A11_LIVE"            # 與 board_data.A11_LIVE_ENV 同名（不 import board_data：它會拖 MES 模組）
REQUIRED_ENV = (_wrs.ENV_SOURCE, ENV_STATE_DIR)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8050


class LaunchError(RuntimeError):
    pass


def check_env(environ=None):
    """→ list[str]（問題清單；空＝通過）。純函式。"""
    env = os.environ if environ is None else environ
    bad = []
    src = str(env.get(_wrs.ENV_SOURCE) or "").strip().lower()
    if src != _wrs.SOURCE_SYNTH:
        bad.append("%s must be %r (got %r)" % (_wrs.ENV_SOURCE, _wrs.SOURCE_SYNTH, env.get(_wrs.ENV_SOURCE)))
    if not str(env.get(ENV_STATE_DIR) or "").strip():
        bad.append("%s is not set" % ENV_STATE_DIR)
    return bad


def prepare_env(environ=None):
    """進程內環境變數（R-11）。→ list[str]（印給人看的變更紀錄）。"""
    env = os.environ if environ is None else environ
    notes = []
    if not str(env.get(ENV_A11_LIVE) or "").strip():
        env[ENV_A11_LIVE] = "1"
        notes.append("%s=1 (set in-process; long-stop facts come from the synthetic world)" % ENV_A11_LIVE)
    else:
        notes.append("%s=%r (left as given)" % (ENV_A11_LIVE, env.get(ENV_A11_LIVE)))
    return notes


def boot(out=print):
    """第 1–4 步。→ 絆線摘要；不過 ⇒ `LaunchError`／`SynthStartupError`。"""
    bad = check_env()
    if bad:
        raise LaunchError("required environment not set: " + "; ".join(bad))
    for line in prepare_env():
        out("[wsynth_launch] " + line)
    if not _wrs.is_synth_process():             # 旗標已被別的值鎖定（理論上不會：本檔最先讀它）
        raise LaunchError("core_source process flag is not synth")
    summary = _wrs.startup_tripwire()
    for line in _wrs.format_summary(summary):
        out(line)
    return summary


def import_app(out=print):
    """第 5 步前半：絆線通過之後才 import app。→ app 模組。"""
    import importlib
    mod = importlib.import_module("app")
    if not getattr(mod, "_SYNTH_PROC", False):
        raise LaunchError("app did not come up as a synthetic process")
    out("[wsynth_launch] app imported (synthetic process; default source=%r)" % (mod.DEFAULT_SOURCE,))
    return mod


def prebuild_world(out=print):
    """第 6 步：預建合成世界（只在合成進程；`wsynth_io` 在非合成進程 import 會拋錯）。→ 耗時秒數。"""
    import time
    import wsynth_io
    t0 = time.perf_counter()
    wsynth_io.world()
    el = time.perf_counter() - t0
    info = wsynth_io.cache_info()
    out("[wsynth_launch] world prebuilt in %.1fs (world_id=%s, days=%s..%s, generated=%s)"
        % (el, info.get("world_id"), info.get("first_day"), info.get("last_day"), info.get("n_generated")))
    return el


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tendline Board synthetic-line launcher (BOARD_SOURCE=synth)")
    ap.add_argument("--host", default=DEFAULT_HOST, help="bind address (default %s = loopback only)" % DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default %d)" % DEFAULT_PORT)
    ap.add_argument("--check", action="store_true", help="tripwire + import app only; do not start the server")
    a = ap.parse_args(argv)
    try:
        boot()
        mod = import_app()
        prebuild_world()
    except _wrs.SynthStartupError as exc:
        for line in _wrs.format_summary(exc.summary):
            print(line)
        print("[wsynth_launch] REFUSED: %s" % exc)
        return 2
    except LaunchError as exc:
        print("[wsynth_launch] REFUSED: %s" % exc)
        return 2
    if a.check:
        print("[wsynth_launch] --check PASS (server not started)")
        return 0
    import uvicorn
    print("[wsynth_launch] serving on http://%s:%d" % (a.host, a.port), flush=True)
    uvicorn.run(mod.app, host=a.host, port=a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
