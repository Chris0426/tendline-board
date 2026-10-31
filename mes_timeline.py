#!/usr/bin/env python3
"""Tendline Board Phase 1 — 生產 vs 停機 時間軸 + 機隊彙總（凍結 v1, §21.2）。

設計（探索 + ground-truth 已驗，見 MES-PHASE1-FINDINGS.md）：
  - 機台級 fleet = 產 CAL 段、且非任何 CAL 機台之 dependent 的 EID
    （`dependent-equipment export` NOT EXISTS 排除模組；勿用 NOT IN——dependent 有 NULL 會全滅）。
  - 生產時間 = 各機台 segment 的 interval-union（區間聯集）→ 真實 wall-clock。
  - ★ 設備視角（§14.4 / remote-diag B3 反向需求#2）：聯集計入**所有** segment（含真實重測），
    **NO 產品視角去重**。沿用 mes_cal.py 連線，但**不沿用其重測去重**（那是良率口徑，
    用在稼動會把『有在跑』誤判『沒跑』、假性抹掉稼動）。
  - 狀態（§23 v1）：生產=被段覆蓋（信心 high）；停機=未覆蓋（v1 全→『不明』，信心 low，
    C3 正面 only）。相隔 < stop 門檻的聯集併成『生產 run』（吸收換單元 micro-idle）；
    >= 門檻＝『停機』窗，送分類（v1→不明）。
  - 時間錨＝MES server GETDATE()（§14.2 / C5）。髒值防線 `<= GETDATE()`（§21.1）。
ground-truth（2026-06-20）：對 rep.fact_operator_equipment_segment_time_coverage（30 分桶）
  Gen1 三台差 <35 秒/4 天；Gen2 證 Fact 槽位級盲 SUM 膨脹 ~44%、機台級 union 才對。
凍結原則：參數化 SQL 拉 raw 機台級段 + Python 確定性 union/分類 + _selftest。runtime 無 LLM（§15）。唯讀。
"""
import argparse
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SERVER = "mes-sql.example.com"
DATABASE = "MES_DB"
DRIVER = "{ODBC Driver 17 for SQL Server}"
CAL_CLASS = "segCalibrationTest"
STOP_MIN_DEFAULT = 15          # gap >= 此分鐘數＝『停機』窗（否則算 run 內 micro-idle）。SME 定，§25。
CONF_PROD = "high"             # C3：MES segment 直接給＝high
CONF_DOWN = "low"             # C3：無 reason 訊號＝low（正面 only）→ §10.2 補問

# 固定審過的參數化站清單（§14.2 / §26.9(b)）：全線「有設備」(ast>0) 的 segment class，
# 路由序排列。**非 runtime discovery**——`_explore_stations.py` 14 天全線盤點後人工審定
# （39 ast>0 class，扣 segMESReserved＝系統/借用桶，得 38 製程站）。`--all-stations`
# 逐站跑『同一套』凍結 CAL 形 query（每站 SegmentClassID=? 點查），絕不全線掃描。
# 5 站需 CAL-Gen2 式 dependent-equipment export NOT EXISTS 模組排除（CAL/FluidCleanE/OtherElectrical/
# LaminationLeak/ModuleElectrical）；其餘 33 站排除為 no-op（對空集恆真）＝一般化安全。
# ★ 2026-09-02（#41 裁決 (c)，`SPEC-RUNWINDOW.md` §10.8-4）：**追加第 39 站
#   `segTopSealLube`**（38 → 39）。原 14 天盤點沒收它（那時窗內訊號不足），
#   本輪 730 天探針證實它是有設備的製程站（計數主 `EID-38041` 730d 有觀測段）。
#   ⚠ 仍是**人工審定常數、非 runtime discovery**（M4 教訓照舊）。
EQP_STATIONS = ['segCalibrationTest',
                'segCleanTest',
                'segAuxElectricalTest',
                'segLamLeakTest',
                'segModuleElectricalTest',
                'segSealDispense',
                'segWireBonding',
                'segAttachCureOven',
                'segPostPickInspection',
                'segAutomatedOQC',
                'segDiePlacement',
                'segUVCure',
                'segUVNitrogenCheck',
                'segFlexBonding',
                'segBendTest',
                'segPotting',
                'segFrameBonding',
                'segStraightnessCheck',
                'segPostCureAlignCheck',
                'segPostCureHeightCheck',
                'segEOLChecker',
                'segEOLRunner',
                'segLamination',
                'segThermalCure',
                'segChassisAttach',
                'segManifoldAttach',
                'segFinalOQC',
                'segPostLamInspection',
                'segPreCureAlignCheck',
                'segPreCureHeightCheck',
                'segPreEncapPlasma',
                'segPreLamPlasma',
                'segAssemblyLeakTest',
                'segSerialisation',
                'segQASetup',
                'segPlasmaActivation',
                'segFramePlasma',
                'segVacuumPackInspect',
                'segTopSealLube']

# ---- 形④ 別名折疊（`SPEC-RUNWINDOW.md` §10.8-4／§10.8-8；2026-09-02 Chris 裁）-----------
# 形④＝「非生產子女」：控制電腦／點膠治具與主 EID **每段同掛**，段數是主 EID 的**子集**
# （永遠 ≤）。它不是一台機，另計就是憑空多出一台的稼動/停機帳（＝雙算）。
#   · **rename（改名）**：`EID-38041`（MES 計數主，Dispense Lubrication Fixture）→
#     `EID-38106`（名冊列「Top O Ring Lubrication」）。38106 在 MES 沒有自己的段
#     （12 個月窗零產品段訊號）⇒ 不改名，第 39 站在 /equipment・/utilization・/station
#     上就沒有機台列可長。**只改 EID 標籤、不動任何一條 SQL、不動段本身。**
#   · **drop（別名段不另計）**：`EID-38072`（Vantor Dispensing 點膠機）——730d 有觀測段
#     ⊂ 主 EID 的段 ⇒ 丟掉它零損失（**絕不相加**）。
#   ⚠ Aligner×3（`01450`→`07940`、`24821`→`07905`、`01529`→`26554`）**不在此表**：
#     它們在 `dependent-equipment export` 有邊、主 EID 也一定是同窗候選（子集性質保證）
#     ⇒ 早已被 `fetch_fleet` 的模組排除擋掉。再列一次＝第二個機制、第二份會漂移的事實。
#     它們的「摺回主機」語意住 `board_capacity.ALIASES`（WO 面用，與本層不同用途）。
#   ⚠ 這兩張表是**別名折疊的唯一出處**（`board_capacity` import 同一個物件、selftest 互釘）。
EID_RENAME = {'EID-55272': 'EID-55376'}
EID_ALIAS_DROP = {'EID-54321': 'EID-55376'}


def fold_eid(eid):
    """MES 讀取層的形④ 折疊：drop → `None`；rename → 主 EID；其餘**原樣返回**（恆等）。

    純函式、零 I/O。折疊只作用於上面兩張表列出的 EID，對其餘每一顆 EID 逐位元恆等。"""
    if eid in EID_ALIAS_DROP:
        return None
    return EID_RENAME.get(eid, eid)


# ---- #123 站別折疊：Kestrel 獨有站 → 同一台機的 Plover 站（2026-09-10 Chris 裁 #122(b)）----
# 事實（`_PROBE-PUSH-COVERAGE-RESULT.tsv` STEP3 逐列直證、判讀＝
# `_AUDIT-PUSH-COVERAGE-20260909.md` §2.1／§8.1）：Kestrel 的 Seal／UV cure 製程在 MES
# 記在**另一組站別**——
#   · Seal 設備（`EID-25749`／`EID-25846`／`EID-26190`）：Plover 段在 `segSealDispense`、
#     Kestrel 段在 `segSealDispenseDamAndFill` ＋ `segGrout`。
#   · UV cure oven（`EID-09731`／`EID-38228`）：Plover 段在 `segUVCure`、
#     Kestrel 段在 `segUVCurePostDamAndFill` ＋ `segUVCurePostGrout`。
# 稼動口徑＝對 `EQP_STATIONS` 39 站逐站點查 ⇒ 上述機台跑 Kestrel 的整段時間在 /utilization、
# OEE、日帳／月帳裡被讀成「閒置」＝**口徑錯**（實測落差見 §8.1 的逐台記錄）。
# ★ Chris 2026-09-10 裁：**折疊、不把 39 站擴成 43 站**。稼動是**設備視角**、按 EID 區間
#   聯集，站名只作分組 ⇒ 把 Kestrel 段折回**同一台機的 Plover 站**：站頁不增、口徑不變、
#   名冊零改動。存證＝`SPEC-RUNWINDOW.md` §10.8-10、鐵則 **C-RW21**。
# ⚠ **只作用於稼動路徑**（`fetch_fleet`／`fetch_segments`／`board_data.fetch_segments_desig`）。
#   **產品視角不折**：`mes_yield` 的 Y1／Y2 是站級良率（產品視角、按 `ResultSuccess`），
#   Kestrel 有自己的 45 站帳（`mes_qlik`）——折過去＝把兩本帳混在一起。
# ⚠ 別名站**永遠不進 `EQP_STATIONS`**（進去就變成第 40–43 站＝Chris 否掉的那個方案）。
#   `classes_for` 是**唯一的展開點**；此表是站別折疊的**唯一出處**（消費者 import 同一個物件）。
# ⚠ 與 `core_lastprod_push.LASTPROD_EXTRA_CLASSES`（#122(a)）＝**同一份事實、兩個消費者**：
#   那邊是「推送要掃到這四站的末段」、這邊是「稼動要把這四站的時間算回主站」。用途與生命
#   週期不同 ⇒ **刻意各自持有、不互相 import**（兩邊的 SPEC 交叉引用）。
CLASS_ALIASES = {'segSealDispense': ('segSealDispenseDamAndFill', 'segGrout'),
                 'segUVCure': ('segUVCurePostDamAndFill', 'segUVCurePostGrout')}


def classes_for(cls):
    """稼動口徑要為站 `cls` 點查的 MES 站別：`cls` 本身 ＋ 其 Kestrel 別名（順序固定、去重）。

    純函式、零 I/O。對 `CLASS_ALIASES` 以外的每一個站**恆等回 `(cls,)`** ⇒ 其餘 37 站
    逐位元與 2026-09-10 之前相同（迴圈只跑一次、只送同一條 query）。"""
    out = [cls]
    for a in CLASS_ALIASES.get(cls, ()):
        if a not in out:
            out.append(a)
    return tuple(out)


def _conn_str():
    return (f"Driver={DRIVER};Server={SERVER};Database={DATABASE};"
            "Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;")


def _connect(login_timeout=None):
    # login_timeout（#23(b)，2026-08-18 additive）：秒＝SQL_ATTR_LOGIN_TIMEOUT，只約束**連線期**
    # （08001 那型失敗的白等來源）；查詢逾時仍是下面的 conn.timeout。None＝逐位元原行為（driver 預設）。
    import pyodbc
    kw = {"timeout": int(login_timeout)} if login_timeout else {}
    conn = pyodbc.connect(_conn_str(), autocommit=True, **kw)
    conn.timeout = 90
    cur = conn.cursor()
    # ★ SET ARITHABORT ON（2026-06-29 session B2）：ODBC Driver 預設 ARITHABORT OFF，SSMS 預設 ON。
    # 兩者讓**同一條 SQL** 走不同計劃＝經典「SSMS 快、app 慢」根因。固定為 ON＝app 拿到 Chris 在
    # SSMS 驗過的那個（快）計劃。唯讀 SELECT 無算術 overflow/除零，ON 純正面、零行為風險。
    cur.execute("SET ARITHABORT ON; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
    return conn, cur


# ---- 參數化 SQL（凍結） -------------------------------------------------------
def fetch_fleet(cur, days, cls=CAL_CLASS):
    """機台級 EID（排除模組）。cls 預設 CAL；任一 ast>0 站同形（§26.9 一般化）。
    模組排除對無巢狀站為 no-op（候選無父子邊＝全留），一般化安全。

    ★ 大窗 perf（2026-06-29 session B2 修正）：**只掃 dbo.segment_equipment_v 一次**取候選機台
    （class+窗 的 distinct EID），模組排除改抓小表 dbo.dependent-equipment export(~500 列)的父子邊、
    **在 Python 做**。原版 `WITH cal … WHERE NOT EXISTS(… JOIN cal …)` 把 cal CTE 對那個 5 表大 view
    （含 material_v/production_request_v）引用兩次——SQL Server 不物化 CTE→重評估兩次，大窗時計劃
    退化→HYT00。現法回**同一組父機 EID**（與 P3 等價），但只一次 view 掃描＝穩定快。
    模組＝候選中「其 parent 也是候選」者；忽略自參照 self-loop（如 EID-05303 astPK 334->334，
    防真機台被誤排；segAuxElectricalTest fleet 3→4 修正、wf_7fd5c016 抓到的真 bug）。
    RECOMPILE：免參數嗅探爛計劃（38 站大小差異極大、單站亦曾間歇中招）。"""
    # ★ 大窗 perf（2026-06-30 session B4 修正＝真根因，P10/P11 實證）：原 `SELECT DISTINCT … astDescription`
    # 對 5 表大 view 做去重＝blocking aggregate，計劃在 7→14 天間斷崖（DISTINCT @7d 0.4s ↔ @14d 跑 >15min
    # 跑不完 → app 90s HYT00；ARITHABORT/型別/SET options 皆已證偽）。同窗「不 DISTINCT 掃描」只 2.8s。
    # 改成不 DISTINCT 拉列、Python 去重成 distinct (astPK,eid,desc)＝與舊 SQL DISTINCT 逐列等價
    # （候選列數小、dedup 成本可忽略；astPK→eid/desc 為 1:1）。
    # ★ d=30 斷崖續修（2026-06-30 B4，P12a 實證 3.5s）：補 `stpEndTime` 過濾（同 fetch_segments）——
    # 無此過濾時 segment_equipment_v 掃描在 ~30 天再度斷崖（14d 1.1s ↔ 30d 20s；著陸頁 ×38 站更慘）。
    # 斷崖防護＝`IS NOT NULL`（P12a 主體）；「純開放段（無 end）」機台被排除＝無封閉區間→本就不顯示。
    # ★ M4 修正（2026-07-10，04-mes-text2sql 帶話＋Chris Q14 實證）：原 B4 版多帶嚴格 `> stpStartTime`，
    # 把**零工期段**（stpEndTime=stpStartTime、合法 MES 瞬時事件標記＝進料檢/序號寫入/烘烤打點）整批刪除
    # ——B4 當時的「零口徑影響」評估只涵蓋 IS NOT NULL、漏了這第二個條件。實測（Q14b @30d）：
    # GroutThermalCure/Potting/UVNitrogenMeasurement 三站 100% 零工期→fleet 回 0 台＝整站隱形；
    # Serialisation 4099 段只剩 8。改 `>=`（Q14a：perf 同量級、無斷崖；負工期髒列仍被 >= 擋）。
    # ★ #123 站別折疊（2026-09-10、§10.8-10）：**SQL 文本一個位元不動**，只把「送幾次」
    #   從一次改成 `classes_for(cls)` 次（仍是單站 `= ?` 點查、零 IN-list、零 GROUP BY
    #   ＝B2／B4 紀律不變）。候選列**聯集後**再做模組排除與 `fold_eid` ⇒ 別名站上的機台
    #   進的是**主站的 fleet**。無別名的 37 站迴圈只跑一次＝逐位元原行為。
    _rows = []
    for _c in classes_for(cls):
        cur.execute("""
    SELECT se.astPK, se.astEquipmentID, se.astDescription
    FROM dbo.segment_equipment_v se
    WHERE se.SegmentClassID = ?
      AND se.stpStartTime >= DATEADD(day, -?, GETDATE())
      AND se.stpStartTime <= GETDATE()
      AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime
    OPTION (RECOMPILE)
    """, _c, days)
        _rows.extend(cur.fetchall())
    cand = list({(r[0], r[1], r[2]) for r in _rows})            # distinct (astPK,eid,desc)＝取代 SQL DISTINCT
    if not cand:
        return []
    cand_pks = {pk for pk, _, _ in cand}
    cur.execute("""
    SELECT d.astPK_parent, d.astPK_dependent
    FROM dbo.dependent-equipment export d
    WHERE d.astPK_parent <> d.astPK_dependent
    """)
    # 模組＝候選中、其 parent 也是候選者（= 原 NOT EXISTS … JOIN cal p 的語意；self-ref 已於 SQL 濾）。
    module_pks = {dep for par, dep in cur.fetchall() if par in cand_pks}
    # ★ 形④ 別名折疊（2026-09-02、§10.8-4）：**query 一個位元不動**，只在回列時把
    #   `EID_RENAME` 改名、`EID_ALIAS_DROP` 丟掉。折疊後同一顆主 EID 可能來自兩列
    #   （主 EID 自己 + 被改名者）⇒ 以折疊後 EID 去重、**保留原 EID 排序的第一筆**
    #   （確定性；desc 只是 MES 描述，頁面顯示名一律取 `EQMT_CATALOG`）。
    #   對兩張表以外的每一顆 EID，本段與舊版逐位元等價（fold_eid 恆等 + 無同名列）。
    folded = {}
    for eid, desc in sorted(((eid, desc) for pk, eid, desc in cand if pk not in module_pks),
                            key=lambda x: x[0]):
        e = fold_eid(eid)
        if e is not None and e not in folded:
            folded[e] = desc
    return sorted(folded.items(), key=lambda x: x[0])


def fetch_segments(cur, eids, days, cls=CAL_CLASS):
    """機台級 segments（設備視角，全段計入、NO 去重）。回 {eid: [(start,end)]}。cls 預設 CAL。

    ★ 大窗 perf（2026-06-29 session B2 修正＝真根因）：查詢**不帶** `astEquipmentID IN (…)` 過濾——
    只按 class+窗 拉全段（這條 = _mes-confirm-queries P2，Chris SSMS 實證 14/30 天秒回不逾時），
    回來在 Python 過濾到 fleet EID。**上一輪（session B）已改單表但仍逾時，根因就是這條多帶的
    IN-list**：它是設備側欄位、完全參數化，大窗時翻轉計劃→bad plan cliff（7 天 4s↔14 天 90s+ HYT00）；
    而 P2『治本驗證』從沒測過帶 IN-list 的版本＝驗錯了查詢。去掉 IN-list＝app 跑的就是 P2 那條已驗快的。
    模組 EID 多回的幾列在 Python 丟棄＝結果與舊 IN-list 版逐 eid 等價（只 fleet EID 進 out）。

    ★ M4 修正（2026-07-10）：end-filter `>` → `>=`（同 fetch_fleet 註解）。零工期段貢獻 0 秒＝
    稼動總量不變；會變的是段的存在性——recency（最後封閉段 age、/equipment ⚠ 與 /updown 吃它）
    對零工期-only 機台從「永久靜默」變成有訊號，且 gap 中的零工期打點會把長 gap 切開（該時刻
    確有 MES 活動＝更準）。"""
    if not eids:
        return {}
    eidset = set(eids)
    # ★ #123 站別折疊（2026-09-10、§10.8-10）：**SQL 文本一個位元不動**、逐別名各送一次
    #   同一條點查，回列**直接串接**（稼動＝區間聯集，`analyze` 自己排序 ⇒ 串接順序不影響
    #   任何數字）。無別名的 37 站迴圈只跑一次＝逐位元原行為。
    _rows = []
    for _c in classes_for(cls):
        cur.execute("""
    SELECT se.astEquipmentID, se.stpStartTime, se.stpEndTime
    FROM dbo.segment_equipment_v se
    WHERE se.SegmentClassID = ?
      AND se.stpStartTime >= DATEADD(day, -?, GETDATE())
      AND se.stpStartTime <= GETDATE()
      AND se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime
    OPTION (RECOMPILE)
    """, _c, days)
        _rows.extend(cur.fetchall())
    # ★ 形④ 別名折疊（2026-09-02、§10.8-4）：**逐列先折疊再比對** `eidset`——
    #   `eids` 來自已折疊的 `fetch_fleet`（裡面是 `EID-38106`），MES 列上卻寫著
    #   `EID-38041`；不在這裡折，改名機的段會全數落空＝那台機「窗內無段」。
    #   drop 表的段直接丟（其段是主 EID 的子集，丟＝零損失、加＝雙算）。
    #   對兩張表以外的 EID 逐位元等價（fold_eid 恆等）。
    out = {}
    for eid, st, en in _rows:
        e = fold_eid(eid)
        if e is not None and e in eidset:
            out.setdefault(e, []).append((st, en))
    return out


# ---- 確定性核心（凍結、可單測） ----------------------------------------------
def union_intervals(intervals):
    """合併重疊/相接區間 → 不重疊聯集（interval-union；解 §5 勿相加）。"""
    if not intervals:
        return []
    s = sorted(intervals)
    merged = [list(s[0])]
    for st, en in s[1:]:
        if st <= merged[-1][1]:
            if en > merged[-1][1]:
                merged[-1][1] = en
        else:
            merged.append([st, en])
    return [(a, b) for a, b in merged]


def merge_runs(union, thr_sec):
    """把相隔 < thr 的聯集區間併成『生產 run』。回 (runs, stops)；stops=(start,end,gap_sec)。"""
    if not union:
        return [], []
    runs = [list(union[0])]
    stops = []
    for a, b in union[1:]:
        gap = (a - runs[-1][1]).total_seconds()
        if gap < thr_sec:
            runs[-1][1] = b
        else:
            stops.append((runs[-1][1], a, gap))
            runs.append([a, b])
    return [(a, b) for a, b in runs], stops


def analyze(intervals, now, thr_sec):
    """單機台分析。producing_sec=strict 聯集（驗證過的稼動分子）；run_span 含 micro-idle。"""
    u = union_intervals(intervals)
    if not u:
        return None
    producing = sum((b - a).total_seconds() for a, b in u)
    runs, stops = merge_runs(u, thr_sec)
    first, last = u[0][0], u[-1][1]
    active = (last - first).total_seconds()
    run_span = sum((b - a).total_seconds() for a, b in runs)
    return {
        "n_seg": len(intervals),
        "producing_sec": producing,
        "active_span_sec": active,
        "run_span_sec": run_span,
        "micro_idle_sec": run_span - producing,
        "stop_sec": sum(g for _, _, g in stops),
        "stops": stops,
        "runs": runs,
        "first": first, "last": last,
        "trailing_sec": max(0.0, (now - last).total_seconds()),  # L1: clamp clock-skew (now<last) so trailing never negative
        "util_strict": (producing / active if active else 0.0),
    }


def timeline_records(eid, a, now):
    """狀態時間軸記錄（§23 v1）：生產 run + 停機窗 + 尾段停機。供 fusion/JSON。"""
    rec = [{"eid": eid, "state": "生產", "conf": CONF_PROD, "start": s, "end": e,
            "sec": (e - s).total_seconds()} for s, e in a["runs"]]
    rec += [{"eid": eid, "state": "不明", "conf": CONF_DOWN, "start": s, "end": e, "sec": g}
            for s, e, g in a["stops"]]
    if a["trailing_sec"] > 0:
        rec.append({"eid": eid, "state": "不明", "conf": CONF_DOWN, "start": a["last"],
                    "end": now, "sec": a["trailing_sec"], "note": "尾段停機(至今)"})
    rec.sort(key=lambda r: r["start"])
    return rec


def hms(sec):
    sec = max(0, int(sec))  # L1: defensive clamp; negative input previously rendered garbled durations
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


# ---- 全線多站機隊級計分板（§26.9(b)，additive；單機核心不動） -------------------
def fleet_scoreboard(cls, fleet, results):
    """單站機隊級彙總（rollup 各機台 analyze）。spans 跨機台相加（每台已自聯集、無機內雙計）；
       util_strict_pct ＝ Σ生產 / Σ活躍窗（同 render() FLEET 列口徑）。"""
    f_prod = f_active = f_run = f_stop = f_trail = 0.0
    f_seg = 0
    for eid, _ in fleet:
        a = results.get(eid)
        if not a:
            continue
        f_seg += a["n_seg"]
        f_prod += a["producing_sec"]; f_active += a["active_span_sec"]
        f_run += a["run_span_sec"]; f_stop += a["stop_sec"]; f_trail += a["trailing_sec"]
    return {
        "cls": cls,
        "fleet_size": len(fleet),          # 機台級 EID 數（模組排除後；含窗內無有效段者）
        "n_seg": f_seg,
        "producing_sec": f_prod,
        "active_span_sec": f_active,
        "run_span_sec": f_run,
        "stop_sec": f_stop,
        "util_strict_pct": (100 * f_prod / f_active if f_active else 0.0),
        "trailing_sec": f_trail,
    }


def scoreboard_all_stations(days=4, stop_min=STOP_MIN_DEFAULT, stations=None):
    """全線設備站機隊級稼動計分板（§26.9(b) 一般化；唯讀）。

    對 curated EQP_STATIONS **逐站**跑『同一套』凍結 CAL 形 query（fetch_fleet/fetch_segments，
    每站 `SegmentClassID = ?` 點查）+ 凍結 analyze + fleet_scoreboard。
    **絕不**全線掃描／discovery／跨 class GROUP BY／跨 class JOIN（前兩次 HYT00 逾時根因）。
    逐站 try/except：失敗（含逾時）→ 記 {cls, reason} 跳過、續跑（並重連，避免單站逾時毒化連線而其餘全跳）。
    回 (mes_now, board, skipped)：board＝各站 fleet_scoreboard（依 EQP_STATIONS 路由序）。"""
    stations = EQP_STATIONS if stations is None else stations
    thr = stop_min * 60
    conn, cur = _connect()
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
    except Exception:
        conn.close()
        raise
    board, skipped = [], []
    for cls in stations:
        try:
            fleet = fetch_fleet(cur, days, cls)
            segs = fetch_segments(cur, [e for e, _ in fleet], days, cls)
            results = {eid: analyze(segs.get(eid, []), now, thr) for eid, _ in fleet}
            board.append(fleet_scoreboard(cls, fleet, results))
        except Exception as e:
            skipped.append({"cls": cls, "reason": f"{type(e).__name__}: {str(e)[:200]}"})
            try:
                conn.close()
            except Exception:
                pass
            try:                       # 重連：單站逾時(HYT00)恐毒化連線 → 避免其餘 37 站連帶跳過
                conn, cur = _connect()
            except Exception:
                pass                    # 連不上：下一站 query 會 raise→記 skip→再試重連（VPN 回即自癒）
    try:
        conn.close()
    except Exception:
        pass
    return now, board, skipped


def render_scoreboard(now, board, skipped, days, stop_min):
    print(f"=== 全線設備站機隊稼動計分板　MES now={now}　近{days}天　停機門檻={stop_min}min　"
          f"站 {len(board)}（跳過 {len(skipped)}）===")
    print(f"\n  {'站(segmentClass)':<40}{'機隊':>5}{'段':>7}{'生產':>9}{'活躍窗':>9}"
          f"{'run窗':>9}{'停機':>9}{'稼動%':>7}{'尾段停機':>10}")
    print("  " + "-" * 104)
    for s in sorted(board, key=lambda x: -x["producing_sec"]):
        print(f"  {s['cls']:<40}{s['fleet_size']:>5}{s['n_seg']:>7}{hms(s['producing_sec']):>9}"
              f"{hms(s['active_span_sec']):>9}{hms(s['run_span_sec']):>9}{hms(s['stop_sec']):>9}"
              f"{s['util_strict_pct']:>6.0f}%{hms(s['trailing_sec']):>10}")
    if skipped:
        print(f"\n  跳過 {len(skipped)} 站（try/except 續跑）：")
        for sk in skipped:
            print(f"    {sk['cls']}: {sk['reason']}")
    print("\n  稼動%=Σ strict 聯集生產/Σ活躍窗（同單站 FLEET 列）；§23 v1 停機全→不明（待 G1 裂解）。")


# ---- 呈現 -------------------------------------------------------------------
def render(machines, results, now, thr_sec):
    print(f"=== Phase 1 時間軸　MES now={now}　停機門檻={int(thr_sec/60)}min　機台級 {len(machines)} 台 ===")
    f_prod = f_active = f_stop = f_micro = f_seg = f_trail = 0
    print(f"\n{'機台':<12}{'段':>5}{'生產':>9}{'活躍窗':>9}{'micro':>8}{'窗內停機':>9}{'稼動%':>7}{'尾段停機':>10}")
    print("  " + "-" * 80)
    for eid, desc in machines:
        a = results.get(eid)
        if not a:
            print(f"  {eid:<12}{'0':>5}{'(窗內無段)':>9}")
            continue
        f_seg += a["n_seg"]; f_prod += a["producing_sec"]; f_active += a["active_span_sec"]
        f_stop += a["stop_sec"]; f_micro += a["micro_idle_sec"]; f_trail += a["trailing_sec"]
        print(f"  {eid:<12}{a['n_seg']:>5}{hms(a['producing_sec']):>9}{hms(a['active_span_sec']):>9}"
              f"{hms(a['micro_idle_sec']):>8}{hms(a['stop_sec']):>9}{a['util_strict']*100:>6.0f}%"
              f"{hms(a['trailing_sec']):>10}")
        for gs, ge, g in sorted(a["stops"], key=lambda x: -x[2])[:3]:
            print(f"        停機 {str(gs)[5:16]} → {str(ge)[5:16]} = {hms(g)} (→不明)")
    print("  " + "-" * 80)
    print(f"  {'FLEET':<12}{f_seg:>5}{hms(f_prod):>9}{hms(f_active):>9}{hms(f_micro):>8}{hms(f_stop):>9}"
          f"{(f_prod/f_active*100 if f_active else 0):>6.0f}%")
    print("\n  生產=信心 high；停機 v1 全→『不明』(信心 low，無 cmms/排程源前不分 保養/維修/待機/該跑沒跑)。")
    print("  稼動%=strict 聯集生產/活躍窗(首段→末段)；真 OEE 待 SALTMARSH 排程源(扣非排定)。")
    observed = f_active + f_trail  # 各機台 [首段..now] 觀測總時
    if observed:
        unknown = f_stop + f_trail
        print(f"\n  ── 機隊狀態圓餅（§25 baseline，over 觀測 {hms(observed)}）──")
        print(f"    生產 {100*f_prod/observed:.0f}%　｜　run內 micro-idle {100*f_micro/observed:.0f}%"
              f"　｜　停機/不明 {100*unknown/observed:.0f}%")
        print(f"    ★ 不明% = {100*unknown/observed:.0f}%　(§25 健康訊號：隨 cmms/SALTMARSH 排程/補問上線應單調下降)")


def main():
    ap = argparse.ArgumentParser(description="Tendline Board Phase 1 時間軸（唯讀，凍結 v1）")
    ap.add_argument("--days", type=int, default=4, help="回看天數")
    ap.add_argument("--stop-min", type=int, default=STOP_MIN_DEFAULT, help="停機門檻(分)")
    ap.add_argument("--json", action="store_true", help="輸出狀態時間軸記錄 JSON")
    ap.add_argument("--station", default=CAL_CLASS,
                    help="單站 segmentClass（預設 CAL＝保留原行為；任一 ast>0 站皆可）")
    ap.add_argument("--all-stations", action="store_true",
                    help="跑 curated EQP_STATIONS 全線機隊級計分板（配 --json/--days/--stop-min）")
    ap.add_argument("--selftest", action="store_true", help="離線單測 union/run/stop 數學")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return

    if args.all_stations:
        try:
            now, board, skipped = scoreboard_all_stations(days=args.days, stop_min=args.stop_min)
        except Exception as e:
            low = str(e).lower()
            hint = " → 連不到 MES。確認已連 VPN/內網。" if any(
                k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")) else ""
            print(f"[mes_timeline] 全線計分板查詢失敗: {type(e).__name__}{hint}\n  {str(e)[:300]}")
            sys.exit(2)
        if args.json:
            import json
            print(json.dumps({"mes_now": str(now), "days": args.days, "stop_min": args.stop_min,
                              "stations": board, "skipped": skipped},
                             ensure_ascii=False, default=str))
        else:
            render_scoreboard(now, board, skipped, args.days, args.stop_min)
        return

    thr = args.stop_min * 60
    conn, cur = _connect()
    try:
        cur.execute("SELECT GETDATE()")
        now = cur.fetchone()[0]
        machines = fetch_fleet(cur, args.days, args.station)
        segs = fetch_segments(cur, [m[0] for m in machines], args.days, args.station)
    except Exception as e:
        low = str(e).lower()
        hint = ""
        if any(k in low for k in ("not found", "08001", "(53)", "timeout", "server is not")):
            hint = " → 連不到 MES。確認已連 VPN/內網。"
        elif "login failed" in low or "28000" in str(e):
            hint = " → 登入被拒。確認 AD 帳號對 MES_DB 有讀權。"
        print(f"[mes_timeline] 查詢失敗: {type(e).__name__}{hint}\n  {str(e)[:300]}")
        conn.close()
        sys.exit(2)
    conn.close()

    results = {eid: analyze(segs.get(eid, []), now, thr) for eid, _ in machines}
    if args.json:
        import json
        recs = []
        for eid, _ in machines:
            if results.get(eid):
                recs += timeline_records(eid, results[eid], now)
        print(json.dumps({"mes_now": str(now), "stop_min": args.stop_min,
                          "records": recs}, ensure_ascii=False, default=str))
        return
    render(machines, results, now, thr)


def _selftest():
    """離線驗 union/run/stop（合成重疊段 + micro-idle + 停機）。不連 MES。"""
    base = datetime(2026, 6, 1, 8, 0, 0)

    def seg(m0, dur):
        return (base + timedelta(minutes=m0), base + timedelta(minutes=m0 + dur))

    # 4 段：[0,5][1,7] 重疊→[0,7]；[12,15] 與前隔 5min(<15→併入 run, micro-idle)；
    #       [60,70] 與前隔 45min(>=15→停機)。
    ivs = [seg(0, 5), seg(1, 6), seg(12, 3), seg(60, 10)]
    now = base + timedelta(minutes=100)
    a = analyze(ivs, now, 15 * 60)
    print("=== mes_timeline --selftest ===")
    print(f"  producing={a['producing_sec']} micro_idle={a['micro_idle_sec']} "
          f"stop={a['stop_sec']} runs={len(a['runs'])} stops={len(a['stops'])} "
          f"trailing={a['trailing_sec']}")
    exp = dict(producing_sec=1200.0,   # 聯集 [0,7]+[12,15]+[60,70]=7+3+10=20min
               micro_idle_sec=300.0,   # run[0,15] 含 [7,12] 5min 空檔
               stop_sec=2700.0,        # [15,60]=45min
               runs=2, stops=1,
               trailing_sec=1800.0)    # [70,100]=30min
    got = dict(producing_sec=a["producing_sec"], micro_idle_sec=a["micro_idle_sec"],
               stop_sec=a["stop_sec"], runs=len(a["runs"]), stops=len(a["stops"]),
               trailing_sec=a["trailing_sec"])
    ok = got == exp
    print(f"  期望 {exp}\n  實得 {got}\n  → {'PASS ✅' if ok else 'FAIL ❌'}")
    if not ok:
        sys.exit(1)

    # ---- M4 防回歸：零工期段（合法瞬時事件 marker；end-filter `>`→`>=` 後為合法輸入）----
    # 零長度區間（stpEndTime == stpStartTime）＝進料檢/序號寫入/烘烤打點等瞬時事件標記。
    #   正時長段 [0,10] + 窗中零長段 (30,30) + 窗末零長 marker (90,90)。
    #   斷言：零長段計入 n_seg；producing 不因零長段增加（union 貢獻 0 秒）；末尾零長 marker
    #   成為 last → active_span 擴大、trailing 相應縮小（同時把 M4 的 util 分母副作用釘住為已知）。
    z_ivs = [seg(0, 10), seg(30, 0), seg(90, 0)]
    z_now = base + timedelta(minutes=100)
    za = analyze(z_ivs, z_now, 15 * 60)   # 不觸發除零/例外（active>0）
    z_exp = dict(n_seg=3,                  # 三段全計入（含兩個零長段）
                 producing_sec=600.0,      # 只有 [0,10]=10min；零長段 union 貢獻 0 秒
                 active_span_sec=5400.0,   # first=0 → last=90（marker 擴大 active＝util 分母）
                 trailing_sec=600.0)       # now=100、last=90 → 100-90=10min（marker 使 trailing 縮小）
    z_got = dict(n_seg=za["n_seg"], producing_sec=za["producing_sec"],
                 active_span_sec=za["active_span_sec"], trailing_sec=za["trailing_sec"])
    z_last_ok = za["last"] == base + timedelta(minutes=90)   # 末尾零長 marker 成為 last
    z_ok = z_got == z_exp and z_last_ok
    print("=== mes_timeline --selftest 零工期段（M4 防回歸）===")
    print(f"  期望 {z_exp} last=+90m\n  實得 {z_got} last={za['last']}\n"
          f"  → {'PASS ✅' if z_ok else 'FAIL ❌'}")
    if not z_ok:
        sys.exit(1)

    fails = station_fold_pins()
    print("=== mes_timeline --selftest 站清單 + 形④ 別名折疊 ===")
    print(f"  站 {len(EQP_STATIONS)}　rename {EID_RENAME}　drop {sorted(EID_ALIAS_DROP)}")
    print(f"  → {'PASS ✅' if not fails else 'FAIL ❌ ' + '；'.join(fails)}")
    if fails:
        sys.exit(1)

    cfails = class_fold_pins()
    print("=== mes_timeline --selftest #123 站別折疊（CLASS_ALIASES）===")
    for _m, _v in sorted(CLASS_ALIASES.items()):
        print(f"  {_m} ← {'／'.join(_v)}")
    print(f"  → {'PASS ✅' if not cfails else 'FAIL ❌ ' + '；'.join(cfails)}")
    if cfails:
        sys.exit(1)


def station_fold_pins():
    """站清單 + 形④ 別名折疊的結構釘（2026-09-02 #41 裁決 (c)；§10.8-4）。

    回**失敗訊息 list**（空＝全過）。純函式、零 I/O、**不連 MES**——寫成獨立函式是為了
    讓 `run_smoke.py` 能跑同一組斷言（Claude 側依 `.claude/settings.json` 不得直接執行
    `mes_timeline.py`）⇒ 斷言只有一份、兩個入口共用。"""
    import inspect
    fails = []

    def ck(cond, msg):
        if not cond:
            fails.append(msg)

    ck(len(EQP_STATIONS) == 39, "EQP_STATIONS 應 39 站（2026-09-02 加第 39 站）")
    ck(EQP_STATIONS[0] == "segCalibrationTest", "第 1 站仍是 CAL（原序不動）")
    ck(EQP_STATIONS[38] == "segTopSealLube", "第 39 站＝segTopSealLube（末位）")
    ck(len(set(EQP_STATIONS)) == 39, "站清單無重複")
    ck(EQP_STATIONS[:38] == [
        "segCalibrationTest", "segCleanTest", "segAuxElectricalTest",
        "segLamLeakTest", "segModuleElectricalTest", "segSealDispense",
        "segWireBonding", "segAttachCureOven", "segPostPickInspection",
        "segAutomatedOQC", "segDiePlacement", "segUVCure",
        "segUVNitrogenCheck", "segFlexBonding", "segBendTest",
        "segPotting", "segFrameBonding", "segStraightnessCheck",
        "segPostCureAlignCheck", "segPostCureHeightCheck",
        "segEOLChecker", "segEOLRunner", "segLamination",
        "segThermalCure", "segChassisAttach",
        "segManifoldAttach", "segFinalOQC",
        "segPostLamInspection", "segPreCureAlignCheck",
        "segPreCureHeightCheck", "segPreEncapPlasma",
        "segPreLamPlasma", "segAssemblyLeakTest",
        "segSerialisation", "segQASetup", "segPlasmaActivation",
        "segFramePlasma", "segVacuumPackInspect",
    ], "原 38 站逐位元未動（新站只在末位 append）")

    ck(EID_RENAME == {"EID-55272": "EID-55376"}, "rename 表＝38041→38106（§10.8-8）")
    ck(EID_ALIAS_DROP == {"EID-54321": "EID-55376"}, "drop 表＝38072（Vantor，段是主的子集）")
    ck(not set(EID_RENAME) & set(EID_ALIAS_DROP), "同一顆 EID 不得同時改名又丟棄")
    ck(set(EID_RENAME.values()) | set(EID_ALIAS_DROP.values()) == {"EID-55376"},
       "兩張表都折到同一顆主 EID")
    ck(fold_eid("EID-55272") == "EID-55376", "fold: 改名")
    ck(fold_eid("EID-54321") is None, "fold: 丟棄（絕不相加＝防雙算）")
    ck(fold_eid("EID-55376") == "EID-55376", "fold: 主 EID 自身恆等")
    ck(all(fold_eid(e) == e for e in
           ("EID-00425", "EID-01450", "EID-24821", "EID-01529", "EID-07940", "EID-05303")),
       "fold: 表外 EID 逐位元恆等（Aligner×3 不在本層，靠 fetch_fleet 模組排除）")

    # `>=` end-filter 未動（記憶 [[mes-zero-length-segments]]：`>` 會讓八個站整站隱形）
    for fn in (fetch_fleet, fetch_segments):
        src = inspect.getsource(fn)
        ck("se.stpEndTime IS NOT NULL AND se.stpEndTime >= se.stpStartTime" in src,
           f"{fn.__name__} 保留 `>=` end-filter")
        ck("stpEndTime > se.stpStartTime" not in src,
           f"{fn.__name__} 絕不退回嚴格 `>`（M4 事故）")
    return fails


class _FoldFakeCur:
    """`class_fold_pins` 專用的假 cursor（**零 MES、零網路**）。

    依 `execute` 的第一個位置參數（＝站別）回該站的列；認得 `dependent-equipment export`
    那條（`fetch_fleet` 的模組排除）並回依賴邊。`calls` 記下逐次呼叫＝執行次數的證據。"""

    def __init__(self, by_cls, dep=()):
        self.by_cls, self.dep, self.calls, self._last = dict(by_cls), list(dep), [], []

    def execute(self, sql, *args):
        if "dependent-equipment export" in sql:
            self.calls.append(("dep", None))
            self._last = list(self.dep)
        else:
            cls = args[0] if args else None
            self.calls.append(("scan", cls))
            self._last = list(self.by_cls.get(cls, []))

    def fetchall(self):
        return self._last

    def scans(self):
        return [c for k, c in self.calls if k == "scan"]


def class_fold_pins():
    """#123 站別折疊的結構釘 ＋ 假 cursor 端到端（2026-09-10；§10.8-10、鐵則 C-RW21）。

    回**失敗訊息 list**（空＝全過）。純函式、零 I/O、**不連 MES**——與
    `station_fold_pins()` 同樣是「斷言只有一份、兩個入口共用」（`--selftest` 與
    `run_smoke.class_fold_round`；Claude 側依 `.claude/settings.json` 不得直接跑本檔 CLI）。"""
    fails = []

    def ck(cond, msg):
        if not cond:
            fails.append(msg)

    # ---- 不變式 ----------------------------------------------------------
    aliases = [a for v in CLASS_ALIASES.values() for a in v]
    ck(set(CLASS_ALIASES) == {"segSealDispense", "segUVCure"},
       "折疊主站＝Seal 與 UV cure oven 兩站（§10.8-10 的折疊表）")
    ck(sorted(aliases) == ["segGrout", "segSealDispenseDamAndFill",
                           "segUVCurePostDamAndFill", "segUVCurePostGrout"],
       "別名＝四個 Kestrel 獨有站（探針 STEP3 直證的那四個）")
    ck(all(m in EQP_STATIONS for m in CLASS_ALIASES),
       "★ 每個主站都在 EQP_STATIONS（折回去的那一站必須是真的稼動站）")
    ck(not (set(aliases) & set(EQP_STATIONS)),
       "★★ 別名站**永不進 EQP_STATIONS**（進去＝Chris 否掉的 39→43 方案）")
    ck(len(aliases) == len(set(aliases)), "★ 每個別名只屬一個主站（重複＝時間被算兩次）")
    ck(all(m not in v for m, v in CLASS_ALIASES.items()), "主站不得列在自己的別名裡")
    ck(len(EQP_STATIONS) == 39 and EQP_STATIONS[38] == "segTopSealLube",
       "★ EQP_STATIONS 仍 39 站、末位未動（本包不加站）")
    ck(classes_for("segSealDispense")
       == ("segSealDispense", "segSealDispenseDamAndFill", "segGrout"),
       "classes_for: Seal → 主站 + 兩個 Kestrel 別名（順序固定、主站在前）")
    ck(classes_for("segUVCure")
       == ("segUVCure", "segUVCurePostDamAndFill", "segUVCurePostGrout"),
       "classes_for: UV cure → 主站 + 兩個 Kestrel 別名")
    ck([c for c in EQP_STATIONS if classes_for(c) != (c,)]
       == ["segSealDispense", "segUVCure"],
       "★★ 39 站裡只有這兩站會展開；其餘 37 站 `classes_for` 恆等（逐位元原行為）")
    ck(all(classes_for(a) == (a,) for a in aliases)
       and classes_for("segNotAStation") == ("segNotAStation",),
       "classes_for: 非主站（含別名自己、未知站）恆等回 (cls,)")

    # ---- 假 cursor 端到端：執行次數 + 合併 --------------------------------
    t0, t1 = datetime(2026, 9, 1, 8, 0, 0), datetime(2026, 9, 1, 9, 0, 0)
    t2, t3 = datetime(2026, 9, 1, 10, 0, 0), datetime(2026, 9, 1, 11, 30, 0)
    cur = _FoldFakeCur({
        "segSealDispense": [(11, "EID-25749", "Seal#5")],
        "segSealDispenseDamAndFill": [(11, "EID-25749", "Seal#5"),
                                       (13, "EID-25846", "Seal#7")],
        "segGrout": [(12, "EID-26190", "Seal#8")],
    })
    fleet = fetch_fleet(cur, 30, "segSealDispense")
    ck(cur.scans() == ["segSealDispense", "segSealDispenseDamAndFill", "segGrout"],
       f"★ fetch_fleet 對主站逐別名各點查一次、順序固定（實得 {cur.scans()}）")
    ck(cur.calls.count(("dep", None)) == 1,
       "★ dependent-equipment export 只查一次（不隨別名重複＝別名不該加倍小表往返）")
    ck([e for e, _ in fleet] == ["EID-25749", "EID-25846", "EID-26190"],
       f"★★ 候選聯集：只在 Kestrel 站上有段的機台**也進主站 fleet**（實得 {fleet!r}）")

    cur1 = _FoldFakeCur({"segDiePlacement": [(1, "EID-00425", "DA#1")]})
    fleet1 = fetch_fleet(cur1, 30, "segDiePlacement")
    ck(cur1.scans() == ["segDiePlacement"] and fleet1 == [("EID-00425", "DA#1")],
       f"★ 無別名站：只送一次、結果逐位元原行為（實得 {cur1.scans()}／{fleet1!r}）")

    curs = _FoldFakeCur({
        "segSealDispense": [("EID-25749", t0, t1)],
        "segSealDispenseDamAndFill": [("EID-25749", t2, t3), ("EID-99999", t0, t1)],
        "segGrout": [("EID-26190", t0, t1)],
    })
    segs = fetch_segments(curs, ["EID-25749", "EID-26190"], 30, "segSealDispense")
    ck(curs.scans() == ["segSealDispense", "segSealDispenseDamAndFill", "segGrout"],
       f"★ fetch_segments 對主站逐別名各點查一次（實得 {curs.scans()}）")
    ck(segs.get("EID-25749") == [(t0, t1), (t2, t3)],
       f"★★ Kestrel 段掛回**同一台機**（不折＝那段時間被讀成閒置；實得 {segs!r}）")
    ck(segs.get("EID-26190") == [(t0, t1)] and "EID-99999" not in segs,
       "★ 只有 fleet 內的 EID 進來（別名站的 fleet 外機台照舊丟棄）")

    curs1 = _FoldFakeCur({"segDiePlacement": [("EID-00425", t0, t1)]})
    segs1 = fetch_segments(curs1, ["EID-00425"], 30, "segDiePlacement")
    ck(curs1.scans() == ["segDiePlacement"] and segs1 == {"EID-00425": [(t0, t1)]},
       f"★ 無別名站：只送一次、結果逐位元原行為（實得 {curs1.scans()}／{segs1!r}）")

    # 形④（EID）折疊與站別折疊是兩個機制、互不干擾
    curf = _FoldFakeCur({"segSealDispense": [],
                         "segSealDispenseDamAndFill": [("EID-55272", t0, t1)],
                         "segGrout": [("EID-54321", t0, t1)]})
    segf = fetch_segments(curf, ["EID-55376"], 30, "segSealDispense")
    ck(segf == {"EID-55376": [(t0, t1)]},
       f"★ 站別折疊之後 `fold_eid` 照樣生效（rename 收、drop 丟；實得 {segf!r}）")
    return fails


if __name__ == "__main__":
    main()
