# -*- coding: utf-8 -*-
"""
_gen_monthly_fixtures.py — 產生 WP-M1 的兩份 fixture（**全合成、決定性**；零 MES、零真日帳）。

    `_fixtures/monthly_daily_docs_sample.json`  仿真日帳 docs（2026-08、5 站 × 28 天）
    `_fixtures/board.monthly_ledger.v1.json`       月快照 golden ＋ 4 份非法變體

★ 為什麼要有這支（慣例＝`_gen_product_tags.py`／`_fixtures/board.last_production.v1.json`）：
  **手打的 fixture ＝ repo 裡多出第二份形狀定義**，然後兩份會悄悄長歪、沒有任何測試會紅。
  這兩份 fixture 是本檔的**決定性產物**（無亂數、無時鐘、無 I/O 輸入）⇒ 要換樣本＝改本檔重跑。
  `board_monthly --selftest` 會跑 `--check`：fixture 被手改就當場紅。

★ 全合成鐵則：數字**沒有一個**來自 MES 或真日帳——真日帳含真實產出／良率，
  `_state/` 不進版控就是這個理由；把它去識別後當 fixture 等於把產線數字 commit 進 repo。
  站別 `cls` 用真 canonical id（好讓 demo 的站別路由走得通），數字全部是 day index 的算式。

    python _gen_monthly_fixtures.py            # 重產兩份 fixture
    python _gen_monthly_fixtures.py --check    # 只驗：檔案 ≡ 本檔的產物（回 0/1）
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)


STATES = ("維修", "計畫保養", "調機", "其他維護(子類待定)", "等料(停機)", "等外包(停機)")
MONTH = "2026-08"
MISSING = {"2026-08-07", "2026-08-29", "2026-08-30"}
NOSIG_DAYS = {"2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24"}

# ── 日帳 v5 的應生產時段軸（`rw`；WP-M3 C4 ＋ 2026-09-04 換尺）───────────────────
#   ⚠ **兩把尺刻意不同源**：`rw` 的分母是跑窗（生產日曆 ∩ 班表 ∩ 名冊），不是 24h；
#     所以 fixture 的 `planned_sec` **不等於** `total_mh`，而且必然 `producing_sec < planned`。
#   三個刻意的洞（沒有它們，月報那三欄的「—」路徑一根釘子都測不到）：
#     ① `RW_OFF_DAYS`＝doc 級 `rw_axis=False`（日曆未涵蓋）⇒ 那天**每一站** `rw=null`。
#     ② `NO_RW_CLS`＝整月都沒有 rw 的站（名冊外／跑窗算不出）⇒ A／U／OEE 整列「—」，
#        但它的 24h 軸停機小時**照樣有數字**（兩把尺互不頂替）。
#     ③ 結構性不可測站（`unmeasurable`）一律 `rw=null`：量不到 producing 就給不出可信的 U。
RW_OFF_DAYS = {"2026-08-09", "2026-08-18", "2026-08-27"}
NO_RW_CLS = "segAutomatedOQC"


# ── #103（`_SPEC-QLIK-YIELD.md` §12）：Kestrel 產品子樹（日帳 v7 的 `products.kestrel`）──
#   四站＝**兩個共同站**（`segFrameBonding`／`segLamination`，證明兩棵樹的同名站
#   各自成帳、永不相加）＋**兩個 Kestrel 獨有站**（`segGrout`／`segUVCurePostGrout`，
#   §18.20.5 那 12 站之二；顯示名待抄錄 ⇒ `display=None`／`display_pending=True`）。
#   ⚠ 數字全合成、與 Plover 那棵樹刻意**完全不同的量級**（一眼看得出沒有互相污染）。
KESTREL_STATIONS_FIX = (
    (3, "segFrameBonding", "Frame Bonding", False),
    (18, "segGrout", None, True),
    (19, "segUVCurePostGrout", None, True),
    (25, "segLamination", "Lamination", False),
)


def r6(x):
    return round(float(x), 6)


def kestrel_block(i):
    """`products.kestrel` 子樹（形＝`board_unit._DOC_PRODUCT_STATION_KEYS`；數字全合成）。"""
    rows = []
    for ordn, cls, disp, pending in KESTREL_STATIONS_FIX:
        pas = 40 + (i * ordn) % 25
        fail = (i + ordn) % 4
        rows.append({
            "cls": cls, "ord": ordn, "display": disp, "display_pending": pending,
            "qlik_pass": pas, "qlik_fail": fail, "qlik_total": pas + fail,
            "n_null": 0,
            # 恆等式 `Σ units_by_family ≡ qlik_pass` 由構造保證（Kestrel 桶吃全部）
            "units_by_family": {"Kestrel": pas},
        })
    return {"product": "kestrel", "n_stations": len(rows), "degraded": False,
            "note": None, "stations": rows}


def units(attempts, rework_n, qlik_pass, qlik_fail, producing_h, r_good):
    """單位子塊（形狀照真日帳 v3；數字全合成）。"""
    if producing_h is None:
        return {"attempts": attempts, "first_attempts": attempts - rework_n,
                "rework_n": rework_n, "qlik_pass": qlik_pass, "qlik_fail": qlik_fail,
                "r_y": None, "r_base": None, "base_n_days": 0, "speed_net": None,
                "unit_bridge": 0, "identity_ok": None, "scrap_src_degraded": False,
                "note": "nosig", "good_h": None, "rework_h": None, "scrap_h": None,
                "r_good": None, "r_good_base": None, "base_good_n_days": 0,
                "slow_good": None,
                "units_by_family": ({"Plover": qlik_pass} if qlik_pass else {})}
    rework_h = r6(producing_h * rework_n / attempts) if attempts else 0.0
    first_h = producing_h - rework_h
    q = qlik_fail / (qlik_pass + qlik_fail) if (qlik_pass + qlik_fail) else 0.0
    scrap_h = r6(first_h * q)
    good_h = r6(first_h - scrap_h)
    return {"attempts": attempts, "first_attempts": attempts - rework_n,
            "rework_n": rework_n, "qlik_pass": qlik_pass, "qlik_fail": qlik_fail,
            "r_y": r6(qlik_pass / producing_h), "r_base": r6(r_good * 1.02),
            "base_n_days": 6, "speed_net": 1.5, "unit_bridge": 0, "identity_ok": True,
            "scrap_src_degraded": False, "note": None, "good_h": good_h,
            "rework_h": rework_h, "scrap_h": scrap_h, "r_good": r6(r_good),
            "r_good_base": r6(r_good * 1.02), "base_good_n_days": 6, "slow_good": 1.2,
            # ★ #98（日帳 v6）：`qlik_pass` 的家族拆分（**恆等式由構造保證**：
            #   `Σ ≡ qlik_pass`）。Plover 那棵樹在合成資料裡全歸 Plover ⇒ landing 產出格
            #   印 `R …`；Kestrel 的 `A …` 來自另一棵樹（`products.kestrel`），
            #   兩者並列顯示、**永不相加**（#103 D1）。
            "units_by_family": ({"Plover": qlik_pass} if qlik_pass else {})}


def rw_block(fleet_n, producing_h, wo_state_h, i):
    """`rw` 區塊（日帳 **v5**；形＝`{planned_sec, health_down_sec, producing_sec, n_eid, src,
    avail_down_sec, down_by_state, eids}`）。

    · `planned_sec` ＝跑窗長度 × 名冊台數（**不是** 24h × fleet_n）。
    · **`avail_down_sec`（2026-09-04 起 A 的分子）** ＝**白名單全部計停機工單** ∩ 跑窗
      （＝`board_oee.RW_AVAIL_DOWN_STATES`；等料／等外包／legacy 現在也算停機）。
    · `health_down_sec` ＝**舊尺相容欄**（`維修 + 計畫保養`）＝`RW_HEALTH_STATES`。
      兩欄並存、值不同，正是 `SPEC-CAPACITY.md` §3.1「v5 走加法、不是改名」的樣子。
    · `down_by_state` ＝逐態（事件視角）；此處合成成**與 `avail_down_sec` 恰好相等**
      （不製造跨態重疊；真資料裡 Σ 可以更大，那條路徑由 `board_attrib --selftest` 釘）。
    · `producing_sec` ＝生產 ∩ 跑窗，且**必定 ≤ up**（合成資料不製造 U>1 的矛盾——
      那條路徑由 `board_monthly --selftest` 的合成日帳單獨釘）。**up 用新尺算**，
      否則換尺之後 fixture 自己會冒出 U>1。
    """
    planned_h = fleet_n * (11.0 + (i % 3) * 0.5)
    health_h = min((wo_state_h.get("維修", 0.0) + wo_state_h.get("計畫保養", 0.0)) * 0.9,
                   planned_h)
    by_state, avail_h = {}, 0.0
    for st in STATES:                                  # STATES 全在停機白名單上
        h = r6(wo_state_h.get(st, 0.0) * 0.9)
        if h > 0:
            by_state[st] = r6(h * 3600.0)
            avail_h += h
    avail_h = min(r6(avail_h), planned_h)
    if sum(by_state.values()) > avail_h * 3600.0:      # planned 夾住之後等比縮，維持相等
        _k = (avail_h * 3600.0) / sum(by_state.values())
        by_state = {st: r6(v * _k) for st, v in by_state.items()}
    up_h = planned_h - avail_h
    prod_rw_h = min(producing_h * 0.95, up_h * 0.98)
    return {"planned_sec": r6(planned_h * 3600.0), "health_down_sec": r6(health_h * 3600.0),
            "producing_sec": r6(max(0.0, prod_rw_h) * 3600.0), "n_eid": fleet_n,
            "src": "erw",
            "avail_down_sec": r6(avail_h * 3600.0), "down_by_state": by_state,
            "eids": [f"E{fleet_n}-{k + 1}" for k in range(fleet_n)]}


def meas_row(cls, fleet_n, producing_h, wo_state_h, starved_h, blocked_h,
             u, total_mh=None, rw=None):
    total = total_mh if total_mh is not None else fleet_n * 24.0
    wo_union = r6(sum(wo_state_h.values()) * 0.92)      # union < Σ 分態（重疊）
    unexplained = r6(total - producing_h - wo_union - starved_h - blocked_h)
    return {
        "cls": cls, "fleet_n": fleet_n, "total_mh": r6(total),
        "producing_h": r6(producing_h), "wo_union_h": wo_union,
        "wo_h": {k: r6(wo_state_h.get(k, 0.0)) for k in STATES},
        "wo_union_pre_h": wo_union, "wo_overlap_prod_h": 0.0,
        "starved_h": r6(starved_h), "blocked_h": r6(blocked_h),
        "unexplained_h": unexplained,
        "lost_h": r6(total - producing_h), "unmeasurable": False, "note": None,
        "closure_h": 0.0, "rate_per_mh": u["r_y"], "rate_src": "y24_raw", "units": u,
        "rw": rw, "families": None,
    }


def unmeas_row(cls, fleet_n, note, u):
    return {
        "cls": cls, "fleet_n": fleet_n, "total_mh": None, "producing_h": None,
        "wo_union_h": None, "wo_h": None, "wo_union_pre_h": None,
        "wo_overlap_prod_h": None, "starved_h": None, "blocked_h": None,
        "unexplained_h": None, "lost_h": None, "unmeasurable": True, "note": note,
        "closure_h": None, "rate_per_mh": None, "rate_src": None, "units": u,
        "rw": None, "families": None,      # 結構性不可測 ⇒ RW 軸也給不出可信的 producing
    }


def build_docs():
    docs = []
    for dnum in range(1, 32):
        day = f"{MONTH}-{dnum:02d}"
        if day in MISSING:
            continue
        i = dnum
        stations = []
        rw_axis = day not in RW_OFF_DAYS          # doc 級：那天有沒有應生產時段軸

        def rw_of(fleet_n, producing_h, wo_state_h, cls):
            """該站該日的 `rw`；`rw_axis` 為假或該站在 `NO_RW_CLS` ⇒ None。"""
            if not rw_axis or cls == NO_RW_CLS:
                return None
            return rw_block(fleet_n, producing_h, wo_state_h, i)

        # ① segFrameBonding — 全月可測、有尺、損失中等
        wo = {"維修": 0.4 + (i % 5) * 0.35, "計畫保養": 1.2 if i % 7 == 0 else 0.0,
              "調機": 0.25 * (i % 3), "其他維護(子類待定)": 0.1 * (i % 4),
              "等料(停機)": 0.5 if i % 6 == 0 else 0.0,
              "等外包(停機)": 0.8 if i % 11 == 0 else 0.0}
        prod = 6.0 + (i % 9) * 0.4
        att = 150 + (i * 7) % 60
        rew = (i * 3) % 9
        fail = (i * 2) % 7
        stations.append(meas_row(
            "segFrameBonding", 1, prod, wo, 1.5 + (i % 4) * 0.3, 0.4 * (i % 3),
            units(att, rew, att - rew - fail, fail, prod, 26.0 + (i % 11) * 1.3),
            rw=rw_of(1, prod, wo, "segFrameBonding")))

        # ② segLamination — 全月可測、有尺、停機最重（＝損失頭一名）
        wo2 = {"維修": 2.5 + (i % 6) * 0.9, "計畫保養": 3.0 if i % 5 == 0 else 0.0,
               "調機": 0.4 * (i % 4), "其他維護(子類待定)": 0.2,
               "等料(停機)": 1.1 if i % 4 == 0 else 0.0,
               "等外包(停機)": 0.0}
        prod2 = 4.5 + (i % 6) * 0.5
        att2 = 190 + (i * 11) % 80
        rew2 = (i * 5) % 14
        fail2 = (i * 3) % 11
        stations.append(meas_row(
            "segLamination", 2, prod2, wo2, 2.0 + (i % 5) * 0.4, 0.6 * (i % 4),
            units(att2, rew2, att2 - rew2 - fail2, fail2, prod2,
                  52.0 + (i % 9) * 2.1),
            rw=rw_of(2, prod2, wo2, "segLamination")))

        # ③ segAutomatedOQC — 可測但幾乎沒生產 ⇒ 合格日 3 天 ⇒ 無尺
        prod3 = {5: 2.6, 14: 3.1, 23: 2.2}.get(i, 0.2 + (i % 5) * 0.28)
        wo3 = {"維修": 1.0 + (i % 3) * 0.6, "計畫保養": 0.0, "調機": 0.3,
               "其他維護(子類待定)": 0.0, "等料(停機)": 2.0 if i % 3 == 0 else 0.0,
               "等外包(停機)": 0.0}
        att3 = 60 + (i * 13) % 40
        rew3 = (i * 2) % 5
        fail3 = (i * 4) % 9
        stations.append(meas_row(
            "segAutomatedOQC", 1, prod3, wo3,
            3.0 + (i % 3) * 0.5, 0.2, units(att3, rew3, att3 - rew3 - fail3, fail3,
                                            prod3, 18.0 + (i % 5) * 0.7),
            rw=rw_of(1, prod3, wo3, "segAutomatedOQC")))

        # ④ segAttachCureOven — 零工期站：結構性不可測（時間全 None、產出照常）
        att4 = 200 + (i * 17) % 50
        rew4 = 0
        fail4 = (i * 2) % 5
        u4 = units(att4, rew4, att4 - fail4, fail4, None, None)
        u4["note"] = "zero_len"
        stations.append(unmeas_row("segAttachCureOven", 2, "zero_len", u4))

        # ⑤ segVacuumPackInspect — 線尾出貨站；四天 nosig、其餘可測有尺
        if day in NOSIG_DAYS:
            u5 = units(0, 0, 0, 0, None, None)
            stations.append(unmeas_row("segVacuumPackInspect", 1, "nosig", u5))
        else:
            wo5 = {"維修": 0.6 + (i % 4) * 0.4, "計畫保養": 2.0 if i % 9 == 0 else 0.0,
                   "調機": 0.15 * (i % 5), "其他維護(子類待定)": 0.0,
                   "等料(停機)": 0.9 if i % 5 == 0 else 0.0, "等外包(停機)": 0.0}
            prod5 = 5.2 + (i % 7) * 0.45
            att5 = 175 + (i * 9) % 55
            rew5 = (i * 4) % 11
            fail5 = (i * 5) % 13
            stations.append(meas_row(
                "segVacuumPackInspect", 1, prod5, wo5,
                1.8 + (i % 6) * 0.35, 0.3 * (i % 2),
                units(att5, rew5, att5 - rew5 - fail5, fail5, prod5,
                      31.0 + (i % 8) * 1.6),
                rw=rw_of(1, prod5, wo5, "segVacuumPackInspect")))

        docs.append({
            # ★ #103：demo 月要看得到 Kestrel ⇒ 每一天都帶子樹 ⇒ 這批 fixture 升 **v7**。
            #   （真 `_state/` 的八月是**另一回事**：那邊一天都沒有 Kestrel 子樹 ⇒ 整片「—」，
            #    v8 月帳已凍、一個位元不動＝D2。）
            "schema_ver": "board.daily_ledger.v7",
            "day": day,
            "products": {"kestrel": kestrel_block(i)},
            "products_status": "ok",
            "source": "synthetic",
            "wo_source_status": "ok" if i % 13 else "degraded",
            # doc 級（v4 起）：那天有沒有 RW 軸、為什麼沒有、標籤活動層抓到了沒
            "rw_axis": rw_axis,
            "rw_reason": "ok" if rw_axis else "calendar_uncovered",
            "families_status": None,
            "computed_at": f"{MONTH}-{min(dnum + 1, 31):02d}T07:12:00",
            "grid_min": 30,
            "thresholds": {"starved_cover_h": 0.5, "blocked_cover_h": 4.0,
                           "active_near_h": 2.0, "chip_min_lost_h": 1.0},
            "line": {"n_stations": len(stations),
                     "n_measurable": sum(1 for s in stations if not s["unmeasurable"]),
                     "n_unmeasurable": sum(1 for s in stations if s["unmeasurable"])},
            "stations": stations,
        })
    return docs


def _dump(obj):
    """序列化＝**唯一一份**（寫檔與 `--check` 共用；差一個換行都會讓 check 假紅）。"""
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def build_all():
    """→ `[(絕對路徑, 檔案內容字串)…]`（決定性：無亂數、無時鐘、無外部輸入）。"""
    docs = build_docs()
    out = {
        "_what": ("仿真日帳（`board.daily_ledger.v5` 形）：2026-08、5 站 × 28 天。"
                  "`board_monthly --selftest` 與月報 demo 源共用。"),
        "_synthetic": ("★★ 全合成：**數字沒有一個來自 MES 或真日帳**（真日帳含真實產出／良率，"
                       "`_state/` 不進版控就是這個理由）。站別 `cls` 用真 canonical id，"
                       "好讓 demo 的站別路由走得通；每日數字＝ day index 的決定性算式。"),
        "_generated_by": ("`_gen_monthly_fixtures.py`（決定性、無亂數）——要換樣本＝改產生器重跑，"
                          "**不要手改本檔**（手改＝repo 裡多出第二份形狀定義；"
                          "`board_monthly --selftest` 會跑 `--check` 當場抓）。"),
        "_shape": ["① segFrameBonding：全月可測、有尺",
                   "② segLamination：全月可測、有尺、停機最重（損失第一名）",
                   "③ segAutomatedOQC：可測但合格日只有 3 天 ⇒ **無尺**",
                   "④ segAttachCureOven：`zero_len` 結構性不可測（時間 None、產出照常）",
                   "⑤ segVacuumPackInspect：線尾出貨站；4 天 `nosig`、其餘可測",
                   "缺日 3 天（08-07／08-29／08-30）＝涵蓋欄要看得見的洞",
                   "v5 `rw`（應生產時段軸、妥善率尺）：③ 整月無 rw ⇒ A／U／OEE 整列「—」；"
                   "④ 不可測站無 rw；08-09／08-18／08-27 三天 `rw_axis=false` ⇒ 全站無 rw"],
        "docs": docs,
    }
    p1 = os.path.join(REPO, "_fixtures", "monthly_daily_docs_sample.json")

    import board_monthly as wm
    model = wm.month_model(docs, MONTH, today="2026-09-02")
    model_a = wm.month_model(docs, MONTH, today="2026-09-02", product="kestrel")
    golden = wm.snapshot_doc(model, "2026-09-02T07:12:00",
                             products={"kestrel": model_a})

    def variant(mut):
        d = json.loads(json.dumps(golden))
        mut(d)
        return d

    def _drop_line(d):
        d.pop("line")

    def _bad_month(d):
        d["month"] = "2026-8"

    def _partial(d):
        d["partial"] = True

    def _bad_id(d):
        d["schema_id"] = "board.monthly_ledger.v0"

    fixture = {
        "schema_id": wm.SCHEMA_ID,
        "_what": ("月快照 `_state/monthly_ledger/YYYY-MM.json` 的 golden ＋非法變體"
                  "（SPEC-MONTHLY §3.5；QG-3.1 至少兩份非法）。"),
        "_synthetic": ("★★ 全合成：golden＝`board_monthly.month_model()` 吃"
                       "`monthly_daily_docs_sample.json` 的實跑產出（today=2026-09-02），"
                       "**零 MES、零真日帳**。要換樣本＝重跑產生器，勿手改。"),
        "_iron_rule": ("**`partial=True` 的月永遠不得出現在快照區**（C-MR4）——"
                       "部分月凍結進快照＝那個月永遠少幾天，而且沒有任何測試會紅。"
                       "**已存在的快照不重寫**（C-MR8）；口徑改版＝換 `schema_id` 重產全部。"),
        "provenance": [
            {"field": "schema_id", "source": "SPEC-MONTHLY.md §3.5"},
            {"field": "stations[].*", "source": "SPEC-MONTHLY.md §3.1（日帳站列欄位的月聚合）"},
            {"field": "stations[].rate_ph_per_h", "source": "SPEC-MONTHLY.md §3.2（月尺）"},
            {"field": "stations[].qlik_fail", "source": "SPEC-MONTHLY.md §3.3（as-of 次日）"},
            {"field": "line.rty_pct", "source": "CONCEPT.md §26.10／C-MR2（跨站不 Σ 良率）"},
            {"field": "stations[].a_pct / u_pct / oee_pct",
             "source": "SPEC-MONTHLY.md §3.4（日帳 v5 `rw` 區塊）＋"
                       "`board_oee.rw_split`（A↔U 三行口徑的單一出處）"},
            {"field": "unit", "source": "Chris 2026-09-02：數量單位一律 PH"},
        ],
        "golden": [golden],
        "invalid": [
            {"case": "schema_id 不是 board.monthly_ledger.v1（口徑改版沒換讀端）",
             "reject_by": "consumer", "expect_reason": "schema_id",
             "record": variant(_bad_id)},
            {"case": "缺 line 區塊（半寫檔／舊版產出）",
             "reject_by": "consumer", "expect_reason": "missing:line",
             "record": variant(_drop_line)},
            {"case": "月鍵格式錯（2026-8，少補零 ⇒ 檔名與排序都會歪）",
             "reject_by": "consumer", "expect_reason": "bad_month",
             "record": variant(_bad_month)},
            {"case": "partial=True 的月被寫進快照區（違反 C-MR4，凍結後永遠缺幾天）",
             "reject_by": "provider_governance", "expect_reason": "partial",
             "record": variant(_partial)},
        ],
        "_note": ["`sort_key` 經 JSON 往返會變成 list（tuple 不存在於 JSON）——"
                  "讀端照樣可以拿它排序，形狀相依的釘子在 board_monthly selftest (14)。",
                  "`down_by_state` 的 key＝§23 canonical 中文態名，**不翻只翻顯示**。"],
    }
    p2 = os.path.join(REPO, "_fixtures", "board.monthly_ledger.v1.json")

    # ── #103：日帳 v7 契約 fixture（GOVERNANCE fixture-first；**兩份、一好一舊**）────────
    #   `v7_with_products`＝帶 `products.kestrel` 的一天；`v6_without_products`＝**同一天、
    #   同樣的 Plover 帳、只是沒有那一腿**（v6 舊檔）。兩份**逐鍵可比**，正是要證明
    #   「加性升版」：v6 檔照讀（不是壞檔）、Kestrel 那一側回 None＝顯「—」而不是 0。
    d0 = json.loads(json.dumps(docs[0]))
    d6 = json.loads(json.dumps(docs[0]))
    d6["schema_ver"] = "board.daily_ledger.v6"
    d6.pop("products", None)
    d6.pop("products_status", None)
    daily_fx = {
        "schema_id": "board.daily_ledger.v7",
        "_what": ("日帳 **v7**（#103）的契約 fixture：`products.kestrel` 產品子樹的形，"
                  "＋一份**沒有那一腿的 v6 檔**（讀端必須照收＝加性升版）。"),
        "_synthetic": ("★★ 全合成：與 `monthly_daily_docs_sample.json` 同一支產生器的第一天；"
                       "**零 MES、零真日帳**。要換樣本＝重跑產生器，勿手改。"),
        "_iron_rule": [
            "D1：`products.kestrel` 與站列 `units` **永不相加**（Qlik 沒有「全線」那張表）。",
            "v6 檔缺 `products` ⇒ 讀端回 **None＝Kestrel 無帳（顯「—」）**，絕不是 0。",
            "子樹站列只有**由 Qlik grid 得出**的量（無 `attempts`／`r_y`／`r_good`——"
            "那些的分母是設備視角的時間腿，不分產品）。",
        ],
        "provenance": [
            {"field": "products.kestrel.stations[]",
             "source": "_SPEC-QLIK-YIELD.md §12（#103 D1）"
                       "＋`board_unit._DOC_PRODUCT_STATION_KEYS`"},
            {"field": "products.kestrel.stations[].units_by_family",
             "source": "SPEC-LANDING.md §18.18（#98；`Σ ≡ qlik_pass` 恆等式守衛）"},
            {"field": "schema_ver", "source": "board_unit.LEDGER_SCHEMA_VER"},
        ],
        "golden": [{"case": "v7：帶 products.kestrel（4 站，含 2 個 Kestrel 獨有站）",
                    "record": d0},
                   {"case": "v6：同一天、沒有 products 那一腿（讀端照收＝Kestrel 顯「—」）",
                    "record": d6}],
        "_note": ["`display=null` ＋ `display_pending=true` ＝ §11.1-4「顯示名待抄錄」"
                  "（12 個 Kestrel 獨有站的公司慣用名尚未從 `Product Flow List.xlsx` 抄回）。"],
    }
    p3 = os.path.join(REPO, "_fixtures", "board.daily_ledger.v7.json")
    return [(p1, _dump(out)), (p2, _dump(fixture)), (p3, _dump(daily_fx))]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    n_bad = 0
    for path, text in build_all():
        if check:
            try:
                with open(path, "r", encoding="utf-8", newline="") as fh:
                    cur = fh.read()
            except OSError:
                cur = None
            ok = (cur == text)
            n_bad += (not ok)
            print(("OK   " if ok else "DIFF ") + os.path.basename(path))
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print("wrote", path)
    if check and n_bad:
        print(f"{n_bad} fixture(s) differ from the generator's output "
              f"(手改過？重跑 `python _gen_monthly_fixtures.py`)")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
