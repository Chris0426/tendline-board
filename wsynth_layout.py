# -*- coding: utf-8 -*-
"""
wsynth_layout.py — 合成產線的示意平面圖（SPEC-SYNTHLINE-PHASE2 §2.1 SL2-A；純函式、零 I/O）
==========================================================================================

一句話：`(合成機隊定義, 名冊常數的區序／站序／綑序, 本檔手寫版面參數)` → 一份 `board.layout.v1` 形的文件，
**只在記憶體內**產出、不落檔、不進 repo（D-LY1／C-SY14）。首頁（`app._ld_read_layout` 的合成分支）與
調度距離讀口（`board_data.load_layout_doc`／`layout_doc_stamp` 的合成分支）取**同一個**記憶體物件
（`cached_doc()`；D-LY3「兩個注入點不得各算各的」）。

鐵則對照
--------
* **C-SY15／AP2-1：與任何真實座標零函數關係。** 輸入白名單＝`wsynth_world.fleet_from_roster()` 的機隊定義
  （`asset_id`、`area`、`display_name`、`station_cls`、`parent_asset_id`、`gen`）＋名冊常數的**順序**
  （`board_eqmeeting.AREAS`／`EQMT_CATALOG` 的列序、`_probe_kun_activity.KUN_ROSTER` 的綑序、
  `mes_timeline.EQP_STATIONS` 的路由序）＋本檔的手寫參數。本檔不 import、不 open 真平面圖座標檔、
  線稿、平面圖產生器、任何 DXF／PNG、調度距離存證表；selftest 以 `builtins.open`／`io.open`／`os.open`
  攔截與 `sys.modules`、AST import 掃描做負斷言（D-LY1 末條）。
* **確定性**：不用亂數；排版是「名冊順序 × 手寫參數」的函數 ⇒ 同一機隊永遠同一份文件（逐位元）。
* **D-LY4（C-LD7 的合成版類比）**：`GOLDEN_DOC_MD5` 釘「`(LAYOUT_VERSION, fleet_digest)` → 文件 md5」，
  另以本檔手寫的夾具機隊釘一個與名冊無關的演算法黃金值（`FIXTURE_GOLDEN_MD5`；兩棵樹都量得到）。
  **改演算法或參數必須升 `LAYOUT_VERSION`、重釘，並由主持人親看一張合成首頁截圖後才收件。**
  機隊不在表內（例：公開樹的合成名冊）＝selftest 印 `unpinned`（不是失敗；請發佈線以
  `python wsynth_layout.py --golden` 取值後補一列）。
* **D-LY6**：私有樹的合成進程機隊＝真名冊 ⇒ 圖上是真 EID／真站名、**假幾何**；私有樹的合成首頁不得截圖對外。

版面演算法（D-LY2；手寫、與真實無關的示意形）
------------------------------------------
1. 畫布 `0 0 1000 600`（5:3）；`meta.dxf_insunits=4`（mm）、`dxf_units_per_norm_x/y`＝60 m × 36 m（只給調度距離換公尺）。
2. 區：名冊 `AREAS` 的四個區鍵依區序排成**順時針 U 形四格**（左上→右上→右下→左下）；上下兩列各佔一半高，
   同列兩格的寬依該區機台數的 `sqrt` 比例分配；每格內留 `AISLE_MARGIN`（4%）走道邊距。
   格邊長全圖共用一個（各區最佳值的最小者）⇒ 每台機在每一區都同一個大小；各區再挑最貼合格長寬比的每列容量。
3. 站：區內依「綑序 → 站的路由序 → 名冊列序」排成簇，簇內機台間留 `MACHINE_GAP`（0.2 格，機名不互撞）；簇在列內由左而右、下一列由右而左
   （**蛇形**）；簇與簇之間留 `STATION_GAP`（1.5 格），列與列之間留 `ROW_GAP`（1.5 格）。同一綑的站在名冊裡
   連號 ⇒ 自然相鄰。簇比一列長時在列尾斷開續到下一列（形③ 父機的工位組永不斷開）。
4. 方塊：一般機 1×1 格；形③ 父機的工位 ⇒ 各一格、緊貼成一列，`parent_eid` 指父機（`source="parent-split"`，
   母機框由 `_ld_read_layout` 既有邏輯算）；無稼動站的被動件 `PASSIVE_SIDE`（0.6）見方、列內垂直置中；
   巢座、co-tag 子機不另畫（名冊外的資產本來就不上首頁）。
5. 每列：`eid`、`name`、`area`、`x/y/w/h`（正規化 0–1、6 位）、`rot=0`、`source`、`confidence="high"`、
   `size_real=True`、`parent_eid`；`parents=[{parent_eid, axis, slots, children}]`；`unplaced=[]`、`excluded=[]`。
6. 不畫建築線稿（呼叫端 `plan=""`）。
7. **sl2 控制區**（2026-10-02）：首頁縮放控制（＋／－／Wall mode）固定疊在圖的右上角。右上區（U 形格 (1,0)）
   的可用高度上緣扣掉 `_ctrl_reserve_h(u)`、左上區改貼格頂（鏡頭上緣的錨）⇒ 鏡頭右上角
   `CTRL_W_FRAC × CTRL_H_FRAC` 的控制區內沒有機台（含渲染期最大放大）。selftest (L14) 釘、(L14b) 證明不空轉、
   (L1c) 證明關掉保留就逐位元回到 sl1。正式頁的控制元件位置與真平面圖座標一個位元都不動（C-LD7）。

自測：`python wsynth_layout.py --selftest [-v]`；印黃金值：`python wsynth_layout.py --golden`。
"""
from __future__ import annotations

import hashlib
import json
import math
import threading

LAYOUT_VERSION = "sl2"
SCHEMA_ID = "board.layout.v1"

# ── 手寫版面參數（D-LY2；改任何一個都要升 LAYOUT_VERSION、重釘黃金值、主持人親看截圖）────────────────
VIEW_W, VIEW_H = 1000.0, 600.0            # 5:3；刻意不取任何真實圖面的長寬比
CANVAS_MM_X, CANVAS_MM_Y = 60000.0, 36000.0   # 畫布＝60 m × 36 m（只給調度距離換公尺用）
DXF_INSUNITS_MM = 4
AISLE_MARGIN = 0.04                        # 每格內四邊的走道邊距（佔該格邊長比例）
STATION_GAP = 1.5                          # 簇與簇之間（格）
ROW_GAP = 1.5                              # 列與列之間（格）
MACHINE_GAP = 0.2                          # 同簇機台之間（格）；形③ 工位組不留（緊貼）
PASSIVE_SIDE = 0.6                         # 無稼動站的被動件邊長（格）
U_SLOTS = ((0, 0), (1, 0), (1, 1), (0, 1))  # 區序 → (欄, 列)：左上、右上、右下、左下（順時針 U 形）
ROUND = 6
# ── sl2 控制區（主持人 2026-10-02 親看 SL2-A 截圖：右上角的縮放鈕／Wall mode 蓋住一台 DOWN 機台）────────
#   首頁的縮放控制固定疊在平面圖**右上角**（`.zctl{top:9px;right:9px}`；正式頁的控制元件位置不動）。
#   實量（公開樹、凍結時鐘、Laptop 1440 與 Projector 1920）：控制列約 145×30 px、距圖頂 8 px、
#   圖寬約 1036–1042 px ⇒ 約佔鏡頭寬的 0.14、高的 0.063。下面兩個比例留了餘裕（圖寬縮到約 730 px 仍蓋得住）。
#   ⚠ 正式平面圖的座標檔是 reviewed（C-LD7），不因此改；本區只存在於合成版面。
CTRL_W_FRAC = 0.20                         # 控制區寬（佔鏡頭寬）
CTRL_H_FRAC = 0.09                         # 控制區高（佔鏡頭高）
CTRL_SLOT = (1, 0)                         # 控制區所在的 U 形格（右上）——該區上緣扣掉保留高度
CTRL_ANCHOR_SLOT = (0, 0)                  # 左上區貼格頂 ⇒ 鏡頭上緣由它決定
#: 兩個渲染期常數的**鏡像**（本檔不得 import app；selftest (L14c) 讀 app 原始碼比對，漂了就紅）：
CAMERA_PAD = 0.015                         # = app.LD_VIEWBOX_PAD_RATIO（鏡頭＝方塊外框各邊外擴 1.5%）
GROW_MAX = 1.6                             # = app.LD_BOX_GROW_MAX（簇放大倍率上限 ⇒ 每邊最多外長 0.3 格）

#: D-LY4：`LAYOUT_VERSION → {fleet_digest 前 16 hex: 文件 md5}`。只存摘要前綴（不在原始碼放整份機隊摘要）。
#:   私有樹（真名冊機隊）一列；公開樹的合成名冊機隊由發佈線以 `--golden` 取值、主持人看過截圖後補列。
#:   sl1 列留作存證（L10 只查現行版號）。
GOLDEN_DOC_MD5 = {
    "sl1": {
        "72d985bde5d90288": "bd3fe82e83bbc4951322bb78cacb6011",
    },
    "sl2": {
        "72d985bde5d90288": "62579f789f54bdeaf84aa91195191ed9",
        "7f7c5e048410c5f2": "68389a8001fb6d52d3ff04fb4bf79a17",   # 公開樹合成名冊機隊（公開樹 `--golden` 取值）
    },
}
#: 本檔手寫夾具機隊（`_fixture_inputs()`；與任何名冊無關）的**幾何** md5（`geometry_md5`；不含名稱字串）⇒ 兩棵樹都釘得到演算法本身。
#:   sl1 值仍被 (L1c) 用：關掉控制區保留＝逐位元回到 sl1 的排法。
FIXTURE_GOLDEN_MD5 = {"sl1": "0502d7ce6290e627061f3570c575d5f9", "sl2": "6b30a825c320f5ab65972e16281ea99b"}


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def doc_md5(doc) -> str:
    return hashlib.md5(_canon(doc).encode("utf-8")).hexdigest()


def geometry_md5(doc) -> str:
    """只看幾何（每台的 eid／區／x／y／w／h／父機、`parents[]`）的 md5——**與文件裡的名稱字串無關**
    ⇒ 公開樹經名稱置換（schema 名、模組名）後，夾具黃金值仍是同一個（演算法釘）。"""
    geo = [[m["eid"], m["area"], m["x"], m["y"], m["w"], m["h"], m["parent_eid"]] for m in doc["machines"]]
    par = [[p["parent_eid"], p["children"]] for p in doc["parents"]]
    return hashlib.md5(_canon([geo, par, doc["meta"]["svg_viewbox"]]).encode("utf-8")).hexdigest()


# ===========================================================================
# 輸入（白名單）
# ===========================================================================

def roster_inputs():
    """名冊常數的**順序**（純常數模組；import 零 I/O，selftest 以攔截驗證）。"""
    import mes_timeline as _mt                 # 凍結模組：只取常數
    import board_eqmeeting as _wem
    import _probe_kun_activity as _pka
    return {
        "areas": tuple(k for k, _lbl in _wem.AREAS),
        "catalog": tuple((a, n, e) for a, n, e in _wem.EQMT_CATALOG),
        "kuns": tuple((k[0], tuple(k[3])) for k in _pka.KUN_ROSTER),
        "station_order": tuple(_mt.EQP_STATIONS),
    }


def default_fleet():
    import wsynth_world as _ww
    return _ww.fleet_from_roster()


def fleet_digest(fleet) -> str:
    import wsynth_world as _ww
    return _ww.fleet_digest(fleet)


# ===========================================================================
# 排版（純函式）
# ===========================================================================

def _kun_index(roster):
    """EID → 綑序。一台列在多個綑（共用父機與其工位）時取**成員最多**的那一綑（同數取先列者）
    ⇒ 共用工位組排在大綑裡、小綑（若只剩共用組）自然也是連續一段。"""
    kuns = list(roster["kuns"])
    out = {}
    for i, (_k, eids) in sorted(enumerate(kuns), key=lambda t: (-len(t[1][1]), t[0])):
        for e in eids:
            out.setdefault(e, i)
    return out


def _area_items(area, fleet_by_id, roster):
    """一個區的簇序列：`[[(eid, kind, width), ...], ...]`。kind ∈ {box, seat, passive}。"""
    cat_idx = {}
    for i, (a, _n, e) in enumerate(roster["catalog"]):
        cat_idx.setdefault(e, i)
    kun_idx = _kun_index(roster)
    st_idx = {s: i for i, s in enumerate(roster["station_order"])}
    big = 10 ** 6
    members = [e for (a, _n, e) in roster["catalog"] if a == area and e in fleet_by_id]
    members = sorted(dict.fromkeys(members), key=lambda e: cat_idx[e])

    def st_of(e):
        s = fleet_by_id[e].get("station_cls")
        return st_idx.get(s, big - 1) if s else big

    groups = {}
    for e in members:
        asset = fleet_by_id[e]
        parent = asset.get("parent_asset_id")
        # 形③：父機（不在名冊列）宣告了這台是它的工位 ⇒ 工位組（co-tag 子機的 parent 不宣告 children，不算）
        parent_split = bool(parent) and e in tuple((fleet_by_id.get(parent) or {}).get("children") or ())
        if parent_split:
            key = ("P", parent)
        elif asset.get("gen") == "none" and not asset.get("station_cls"):
            key = ("N", kun_idx.get(e, big))
        else:
            key = ("S", kun_idx.get(e, big), st_of(e))
        groups.setdefault(key, []).append(e)

    def gkey(item):
        key, eids = item
        k = min(kun_idx.get(e, big) for e in eids)
        s = min(st_of(e) for e in eids)
        c = min(cat_idx[e] for e in eids)
        return (k, s, c)

    out = []
    for key, eids in sorted(groups.items(), key=gkey):
        eids = sorted(eids, key=lambda e: cat_idx[e])
        if key[0] == "P":
            out.append({"unbreakable": True,
                        "items": [(e, "seat", 1.0) for e in eids]})
        elif key[0] == "N":
            out.append({"unbreakable": False,
                        "items": [(e, "passive", PASSIVE_SIDE) for e in eids]})
        else:
            out.append({"unbreakable": False, "items": [(e, "box", 1.0) for e in eids]})
    return out


def _pack(groups, cap):
    """蛇形列裝箱（格單位）。回 `(rows, used_width)`；rows＝`[[(eid, kind, x, w), ...], ...]`（列內由左而右的 x）。"""
    rows, cur, x = [], [], 0.0
    for g in groups:
        items = list(g["items"])
        need = sum(w for _e, _k, w in items)
        if g["unbreakable"] and need > cap + 1e-9:
            return None, None
        if cur:
            if g["unbreakable"] and x + STATION_GAP + need > cap + 1e-9:
                rows.append(cur)
                cur, x = [], 0.0
            elif x + STATION_GAP + items[0][2] > cap + 1e-9:
                rows.append(cur)
                cur, x = [], 0.0
            else:
                x += STATION_GAP
        for i, (e, k, w) in enumerate(items):
            # 簇內機台之間留 MACHINE_GAP（機名不互撞）；形③ 工位組緊貼（母機框圍成一台）
            if i and k != "seat":
                x += MACHINE_GAP
            if x + w > cap + 1e-9:
                rows.append(cur)
                cur, x = [], 0.0
            cur.append((e, k, x, w))
            x += w
    if cur:
        rows.append(cur)
    used = max((r[-1][2] + r[-1][3]) for r in rows) if rows else 0.0
    return rows, used


def _fit(groups, iw, ih):
    """挑「每列容量」使格邊長 `u` 最大（同值取較小容量）。回 `(rows, used, u)`。"""
    total = sum(w for g in groups for _e, _k, w in g["items"]) + STATION_GAP * max(0, len(groups) - 1)
    lo = max([sum(w for _e, _k, w in g["items"]) for g in groups if g["unbreakable"]] + [1.0])
    best = None
    cap = lo
    while cap <= max(lo, total) + 1e-9:
        rows, used = _pack(groups, cap)
        if rows is not None:
            n = len(rows)
            hu = n + (n - 1) * ROW_GAP
            u = min(iw / used, ih / hu)
            if best is None or u > best[2] + 1e-12:
                best = (rows, used, u)
        cap += 0.5
    return best


def _fit_at(groups, iw, ih, u):
    """全圖共用格邊長 `u` 時，挑「每列容量」使區塊長寬比最接近格的長寬比（放得下為前提；同值取較小容量）。"""
    total = sum(w for g in groups for _e, _k, w in g["items"]) + STATION_GAP * max(0, len(groups) - 1)
    lo = max([sum(w for _e, _k, w in g["items"]) for g in groups if g["unbreakable"]] + [1.0])
    target = math.log(iw / ih)
    best = None
    cap = lo
    while cap <= max(lo, total) + 1e-9:
        rows, used = _pack(groups, cap)
        if rows is not None:
            n = len(rows)
            hu = n + (n - 1) * ROW_GAP
            if used * u <= iw + 1e-9 and hu * u <= ih + 1e-9:
                score = abs(math.log(used / hu) - target)
                if best is None or score < best[2] - 1e-12:
                    best = (rows, used, score)
        cap += 0.5
    return best[0], best[1]


def _cells(areas, counts):
    """區 → 格的畫布矩形 `(x, y, w, h)`（viewBox 單位）。U 形四格；同列寬依 sqrt(台數) 比例。"""
    by_slot = {}
    for i, a in enumerate(areas[:len(U_SLOTS)]):
        by_slot[U_SLOTS[i]] = a
    out = {}
    for row in (0, 1):
        left, right = by_slot.get((0, row)), by_slot.get((1, row))
        wl = math.sqrt(max(1, counts.get(left, 0))) if left else 0.0
        board = math.sqrt(max(1, counts.get(right, 0))) if right else 0.0
        tot = (wl + board) or 1.0
        y0, h = row * VIEW_H / 2.0, VIEW_H / 2.0
        if left:
            out[left] = (0.0, y0, VIEW_W * wl / tot, h)
        if right:
            out[right] = (VIEW_W * wl / tot, y0, VIEW_W * board / tot, h)
    return out


def _r(v):
    return round(float(v), ROUND) + 0.0


def _ctrl_reserve_h(u):
    """右上區上緣要扣掉的高度（viewBox 單位）。鏡頭上緣 ≤ 左上區第一列頂（貼格頂；被動件在列內下沉 0.2 格），
    控制區高 ≤ `CTRL_H_FRAC × 畫布高 × (1 + 2×CAMERA_PAD)`；右上區的方塊放大後最多往上長 `(GROW_MAX−1)/2` 格。"""
    return (CTRL_H_FRAC * VIEW_H * (1.0 + 2.0 * CAMERA_PAD)
            + ((GROW_MAX - 1.0) / 2.0 + (1.0 - PASSIVE_SIDE) / 2.0) * u)


def camera_of(boxes, pad=CAMERA_PAD):
    """`[(x, y, w, h), ...]`（viewBox 單位）→ 鏡頭 `(x, y, w, h)`＝外框各邊外擴 `pad`（同 app `_ld_content_viewbox`
    的方塊部分；app 另併區框點，只會讓鏡頭更大 ⇒ 控制區更往外、更空）。"""
    if not boxes:
        return (0.0, 0.0, VIEW_W, VIEW_H)
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    return (x0 - w * pad, y0 - h * pad, w * (1.0 + 2.0 * pad), h * (1.0 + 2.0 * pad))


def control_zone(cam):
    """鏡頭 `(x, y, w, h)` → 右上角控制區 `(x, y, w, h)`（同單位）。"""
    cx, cy, cw, chh = cam
    return (cx + cw * (1.0 - CTRL_W_FRAC), cy, cw * CTRL_W_FRAC, chh * CTRL_H_FRAC)


def control_zone_hits(boxes, cam=None, grow=0.0):
    """壓到控制區的方塊索引（`grow`＝每台方塊各邊先外擴「短邊 × grow」再判；0＝照原尺寸）。"""
    zx, zy, zw, zh = control_zone(cam if cam is not None else camera_of(boxes))
    out = []
    for i, (x, y, w, h) in enumerate(boxes):
        g = min(w, h) * grow
        if min(x + w + g, zx + zw) - max(x - g, zx) > 1e-9 and min(y + h + g, zy + zh) - max(y - g, zy) > 1e-9:
            out.append(i)
    return out


def doc_boxes(doc):
    """文件 → 方塊（viewBox 單位）。"""
    return [(m["x"] * VIEW_W, m["y"] * VIEW_H, m["w"] * VIEW_W, m["h"] * VIEW_H) for m in doc["machines"]]


def layout_doc(fleet=None, *, roster=None, version=LAYOUT_VERSION, _reserve_ctrl=True):
    """合成機隊 → `board.layout.v1` 文件（新 dict；純函式）。`_reserve_ctrl=False` 只給 selftest (L14b) 證明
    控制區檢查不是空轉（不保留右上控制區、左上區照舊置中＝sl1 的排法）。"""
    if version != LAYOUT_VERSION:
        raise ValueError("unknown layout version %r (only %r)" % (version, LAYOUT_VERSION))
    fleet = list(default_fleet() if fleet is None else fleet)
    roster = roster or roster_inputs()
    by_id = {}
    for a in fleet:
        by_id.setdefault(str(a["asset_id"]), a)
    areas = list(roster["areas"])
    groups_by_area = {a: _area_items(a, by_id, roster) for a in areas}
    counts = {a: sum(len(g["items"]) for g in gs) for a, gs in groups_by_area.items()}
    cells = _cells(areas, counts)
    name_of = {}
    for _a, n, e in roster["catalog"]:
        name_of.setdefault(e, n)
    machines = []
    parents = {}
    inner = {}
    for area in areas:
        if groups_by_area[area] and area in cells:
            cx, cy, cw, ch = cells[area]
            mx, my = cw * AISLE_MARGIN, ch * AISLE_MARGIN
            inner[area] = (cx + mx, cy + my, cw - 2 * mx, ch - 2 * my)
    slot_of = {a: U_SLOTS[i] for i, a in enumerate(areas[:len(U_SLOTS)])}
    top_left = next((a for a in inner if slot_of.get(a) == CTRL_ANCHOR_SLOT), None)
    ctrl_area = next((a for a in inner if slot_of.get(a) == CTRL_SLOT), None) if _reserve_ctrl else None

    def _global_u(inn):
        return min(_fit(groups_by_area[a], inn[a][2], inn[a][3])[2] for a in inn) if inn else 1.0

    # 全圖一個格邊長（＝各區最佳格邊長的最小值）⇒ 每台機在每一區都是同一個大小（示意平面圖的「格」是全圖共用的）
    u = _global_u(inner)
    # sl2 控制區：右上區的可用高度扣掉 `_ctrl_reserve_h(u)`（以未扣前的 u 算＝上界；扣完 u 只會變小 ⇒
    #   實際需要的保留高度 ≤ 已扣的高度）。左上區貼齊格頂 ⇒ 鏡頭（內容外框）上緣由它決定、控制區落在右上區的留白裡。
    if ctrl_area is not None and top_left is not None:
        rh = _ctrl_reserve_h(u)
        ix, iy, iw, ih = inner[ctrl_area]
        if ih - rh > 0:
            inner[ctrl_area] = (ix, iy + rh, iw, ih - rh)
            u = _global_u(inner)
    for area in areas:
        if area not in inner:
            continue
        groups = groups_by_area[area]
        ix, iy, iw, ih = inner[area]
        rows, used = _fit_at(groups, iw, ih, u)
        n = len(rows)
        bw, bh = used * u, (n + (n - 1) * ROW_GAP) * u
        ox, oy = ix + (iw - bw) / 2.0, iy + (ih - bh) / 2.0
        if area == top_left and ctrl_area is not None:
            oy = iy                                            # sl2：左上區貼格頂（鏡頭上緣的錨）
        for ri, row in enumerate(rows):
            ry = oy + ri * (1.0 + ROW_GAP) * u
            for e, kind, x, w in row:
                xx = x if ri % 2 == 0 else (used - x - w)       # 蛇形：奇數列由右而左
                side = w * u
                bx = ox + xx * u
                by = ry + (u - side) / 2.0                     # 被動件列內垂直置中
                asset = by_id[e]
                parent = asset.get("parent_asset_id") if kind == "seat" else None
                if parent:
                    parents.setdefault(parent, []).append(e)
                machines.append({
                    "eid": e, "name": name_of.get(e) or asset.get("display_name") or e, "area": area,
                    "x": _r(bx / VIEW_W), "y": _r(by / VIEW_H), "w": _r(side / VIEW_W), "h": _r(side / VIEW_H),
                    "rot": 0.0, "source": "parent-split" if parent else "synthetic", "confidence": "high",
                    "size_real": True, "parent_eid": parent or None,
                })
    machines.sort(key=lambda m: m["eid"])
    return {
        "schema_id": SCHEMA_ID, "status": "synthetic", "reviewed": True,
        "meta": {"origin": "synthetic", "generator": "wsynth_layout", "layout_version": version,
                 "fleet_digest": fleet_digest(fleet), "reviewed_by": "golden-pin",
                 "coord_origin": "top-left", "y_axis": "down",
                 "svg_viewbox": "0 0 %d %d" % (int(VIEW_W), int(VIEW_H)),
                 "dxf_insunits": DXF_INSUNITS_MM,
                 "dxf_units_per_norm_x": CANVAS_MM_X, "dxf_units_per_norm_y": CANVAS_MM_Y},
        "machines": machines, "extras": [], "unplaced": [], "excluded": [],
        "parents": [{"parent_eid": p, "axis": "x", "slots": len(ch), "children": list(ch)}
                    for p, ch in sorted(parents.items())],
    }


# ===========================================================================
# 記憶體內單一物件（D-LY3：兩個注入點取同一個）
# ===========================================================================

_CACHE = {"doc": None, "stamp": None}
_LOCK = threading.Lock()


def cached_doc():
    """本進程唯一的一份合成平面圖（首次呼叫才算；之後回**同一個物件**——呼叫端不得就地改它）。"""
    with _LOCK:
        if _CACHE["doc"] is None:
            doc = layout_doc()
            _CACHE["doc"] = doc
            _CACHE["stamp"] = ("synth-layout", LAYOUT_VERSION, doc["meta"]["fleet_digest"])
        return _CACHE["doc"]


def doc_stamp():
    """快取鍵 `("synth-layout", LAYOUT_VERSION, fleet_digest)`（D-LY3）。"""
    cached_doc()
    return _CACHE["stamp"]


def reset_cache():
    with _LOCK:
        _CACHE["doc"] = None
        _CACHE["stamp"] = None


# ============================================================================ selftest ===

def _fixture_inputs():
    """手寫夾具（與任何名冊無關）：四區、兩綑、一組三工位父機、被動件。"""
    areas = ("qa", "qb", "qc", "qd")
    cat = []
    fleet = []

    def add(area, eid, cls, gen="own", parent=None):
        cat.append((area, "M-" + eid, eid))
        fleet.append({"asset_id": eid, "area": area, "display_name": "M-" + eid, "station_cls": cls,
                      "parent_asset_id": parent, "gen": gen, "children": ()})

    for i in range(5):
        add("qa", "F%03d" % i, "s1" if i < 3 else "s2")
    for i in range(3):
        add("qb", "F1%02d" % i, "s3", parent="FP1")
    fleet.append({"asset_id": "FP1", "area": "qb", "display_name": "FP1", "station_cls": "s3",
                  "parent_asset_id": None, "gen": "derived", "children": ("F100", "F101", "F102")})
    for i in range(4):
        add("qb", "F2%02d" % i, "s4")
    for i in range(9):
        add("qc", "F3%02d" % i, "s5" if i % 3 else "s6")
    for i in range(6):
        add("qd", "F4%02d" % i, None, gen="none")
    add("qd", "F500", "s1")
    roster = {"areas": areas, "catalog": tuple(cat),
              "kuns": (("k1", ("F000", "F001", "F002")), ("vx5", ("F200", "F201"))),
              "station_order": ("s1", "s2", "s3", "s4", "s5", "s6")}
    return fleet, roster


def _overlap(a, b, eps=2e-6):
    # eps：座標 6 位捨入後，相接的兩台可能有 ≤1e-6 的假重疊（相接不是重疊）
    return (min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) > eps and
            min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) > eps)


def _order_in_area(fleet, roster, area):
    by_id = {str(a["asset_id"]): a for a in fleet}
    return [e for g in _area_items(area, by_id, roster) for e, _k, _w in g["items"]]


_FORBIDDEN_PATH_PARTS = ("layout_eid_xy", "layout.svg", "_gen_layout", ".dxf", ".DXF", ".png", ".PNG",
                         "_KUN-", "_fixtures", "_state")
_FORBIDDEN_IMPORTS = ("_gen_layout", "board_kundispatch", "board_layout_regions")

_CHILD = r'''
import builtins, io, os, sys, json
sys.path.insert(0, sys.argv[1])
OPENED = []
_bo, _io, _oo = builtins.open, io.open, os.open
def _rec(fn):
    def w(file, *a, **k):
        OPENED.append(str(file))
        return fn(file, *a, **k)
    return w
builtins.open = _rec(_bo); io.open = _rec(_io); os.open = _rec(_oo)
import wsynth_layout as WL
d = WL.cached_doc()
d2 = WL.layout_doc()
builtins.open, io.open, os.open = _bo, _io, _oo
print("@@WL@@" + json.dumps({"opened": OPENED, "md5": WL.doc_md5(d), "md5b": WL.doc_md5(d2),
                             "mods": sorted(m for m in sys.modules if m in %r)}))
''' % (_FORBIDDEN_IMPORTS,)


def _selftest(verbose=False):
    import ast
    import os
    import subprocess
    import sys
    res = []

    def ck(cond, msg):
        res.append(bool(cond))
        if verbose or not cond:
            print(("PASS " if cond else "FAIL ") + msg, flush=True)

    # (L1) 夾具：演算法黃金值（與名冊無關）＋結構
    ffleet, froster = _fixture_inputs()
    fd = layout_doc(ffleet, roster=froster)
    fmd5 = geometry_md5(fd)
    want = FIXTURE_GOLDEN_MD5.get(LAYOUT_VERSION)
    ck(fmd5 == want, "(L1) fixture golden geometry md5 %s == pinned %s (LAYOUT_VERSION=%s)" % (fmd5, want, LAYOUT_VERSION))
    ck(doc_md5(layout_doc(ffleet, roster=froster)) == doc_md5(fd), "(L1b) fixture: same input -> bit-identical doc")
    for nm, (fleet, roster) in (("fixture", (ffleet, froster)), ("roster", (None, None))):
        if fleet is None:
            fleet, roster = default_fleet(), roster_inputs()
        d = layout_doc(fleet, roster=roster)
        ms = d["machines"]
        cat_eids = [e for _a, _n, e in roster["catalog"]]
        fleet_ids = {str(a["asset_id"]) for a in fleet}
        expect = sorted(set(e for e in cat_eids if e in fleet_ids))
        got = [m["eid"] for m in ms]
        ck(sorted(got) == expect and len(got) == len(set(got)),
           "(L2:%s) every roster machine exactly one block (%d blocks / %d expected)" % (nm, len(got), len(expect)))
        inb = [m["eid"] for m in ms if not (0 <= m["x"] and 0 <= m["y"] and m["x"] + m["w"] <= 1 + 1e-9
                                            and m["y"] + m["h"] <= 1 + 1e-9 and m["w"] > 0 and m["h"] > 0)]
        ck(not inb, "(L3:%s) all blocks inside the canvas [0,1]: %s" % (nm, inb[:5]))
        ov = [(a["eid"], b["eid"]) for i, a in enumerate(ms) for b in ms[i + 1:] if _overlap(a, b)]
        ck(not ov, "(L4:%s) no two blocks overlap: %s" % (nm, ov[:5]))
        # 區格互不越界：每台都在自己區的格內
        counts = {}
        for m in ms:
            counts[m["area"]] = counts.get(m["area"], 0) + 1
        cells = _cells(list(roster["areas"]), counts)
        oc = []
        for m in ms:
            cx, cy, cw, chh = cells[m["area"]]
            if not (cx / VIEW_W - 1e-9 <= m["x"] and m["x"] + m["w"] <= (cx + cw) / VIEW_W + 1e-9 and
                    cy / VIEW_H - 1e-9 <= m["y"] and m["y"] + m["h"] <= (cy + chh) / VIEW_H + 1e-9):
                oc.append(m["eid"])
        ck(not oc, "(L5:%s) every block lies inside its own area cell: %s" % (nm, oc[:5]))
        # 綑內相鄰：每個綑在每個區的排列序裡是一段連續區間
        bad = []
        for area in roster["areas"]:
            order = _order_in_area(fleet, roster, area)
            for k, eids in roster["kuns"]:
                pos = [order.index(e) for e in eids if e in order]
                if pos and max(pos) - min(pos) + 1 != len(pos):
                    bad.append((area, k))
        ck(not bad, "(L6:%s) each bundle's machines are contiguous in its area's serpentine order: %s" % (nm, bad))
        # 站相鄰：同一（綑、站）的機台在排列序裡連續
        badst = []
        by_id = {str(a["asset_id"]): a for a in fleet}
        for area in roster["areas"]:
            order = _order_in_area(fleet, roster, area)
            # 只檢查有站別者：同站可能跨綑（綑序優先），所以以「同站同綑」為單位
            kun_of = _kun_index(roster)
            grp = {}
            seats = {e for p in d["parents"] for e in p["children"]}   # 工位組是一個不可拆的單位（內含被動工位）
            order = [e for e in order if e not in seats]
            for e in order:
                if by_id[e].get("station_cls"):
                    grp.setdefault((kun_of.get(e), by_id[e]["station_cls"]), []).append(order.index(e))
            for kk, pos in grp.items():
                if max(pos) - min(pos) + 1 != len(pos):
                    badst.append((area, kk))
        ck(not badst, "(L7:%s) machines of one station (within a bundle) are adjacent: %s" % (nm, badst[:5]))
        # 父機工位：同一列、緊貼、parents[] 與 parent_eid 一致
        pbad = []
        for p in d["parents"]:
            kids = [m for m in ms if m["eid"] in p["children"]]
            if len(kids) != len(p["children"]) or any(m["parent_eid"] != p["parent_eid"] for m in kids):
                pbad.append(p["parent_eid"])
                continue
            ys = {m["y"] for m in kids}
            kids.sort(key=lambda m: m["x"])
            gaps = [round(b["x"] - (a["x"] + a["w"]), 6) for a, b in zip(kids, kids[1:])]
            if len(ys) != 1 or any(abs(g) > 1e-5 for g in gaps):
                pbad.append(p["parent_eid"])
        ck(not pbad, "(L8:%s) parent-split seats share one row and touch; parents[] agrees: %s (%d parents)"
           % (nm, pbad, len(d["parents"])))
        ck(d["schema_id"] == SCHEMA_ID and d["reviewed"] is True and d["status"] == "synthetic"
           and d["meta"]["origin"] == "synthetic" and d["meta"]["reviewed_by"] == "golden-pin"
           and d["unplaced"] == [] and d["excluded"] == [],
           "(L9:%s) doc shape: board.layout.v1, reviewed, synthetic origin, nothing unplaced/excluded" % nm)
        # (L14) sl2 控制區：鏡頭（方塊外框＋1.5%）右上角 `CTRL_W_FRAC × CTRL_H_FRAC` 內沒有任何機台——
        #   方塊先照渲染期最大放大倍率各邊外擴 `(GROW_MAX−1)/2` 再判（＝渲染後也不會長進去）
        bx = doc_boxes(d)
        cam = camera_of(bx)
        hits = [ms[i]["eid"] for i in control_zone_hits(bx, cam, grow=(GROW_MAX - 1.0) / 2.0)]
        ck(not hits, "(L14:%s) no machine (grown to the render-time max) inside the top-right control zone %s of "
           "camera %s: %s" % (nm, tuple(round(v, 1) for v in control_zone(cam)), tuple(round(v, 1) for v in cam),
                              hits[:5]))
        # (L14b) 反向：拿掉保留（夾具／名冊同一份輸入、不扣右上區高度）時控制區確實會壓到機台——證明 L14 不是空轉
        if nm == "fixture":
            b0 = doc_boxes(layout_doc(fleet, roster=roster, _reserve_ctrl=False))
            ck(control_zone_hits(b0, camera_of(b0)),
               "(L14b) without the reserve the fixture's top-right control zone would cover a machine (L14 is not vacuous)")
    # (L14c) 兩個鏡像常數＝app 的渲染期常數（本檔不 import app；讀原始碼文字比對）
    import re
    app_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    if os.path.exists(app_py):
        asrc = open(app_py, encoding="utf-8").read()
        got = {}
        for nm_c, mine in (("LD_VIEWBOX_PAD_RATIO", CAMERA_PAD), ("LD_BOX_GROW_MAX", GROW_MAX)):
            mm = re.search(r"^%s\s*=\s*([0-9.]+)" % nm_c, asrc, re.M)
            got[nm_c] = (float(mm.group(1)) if mm else None, mine)
        ck(all(a == b for a, b in got.values()),
           "(L14c) mirrored render constants equal app.py's (CAMERA_PAD, GROW_MAX): %s" % got)
    else:
        print("NOTE (L14c) app.py not next to this file — mirrored-constant check skipped", flush=True)
    # (L1c) 拿掉控制區保留＝逐位元回到 sl1 的夾具幾何（sl2 只多了「右上保留＋左上貼頂」這一件事）
    ck(geometry_md5(layout_doc(ffleet, roster=froster, _reserve_ctrl=False)) == FIXTURE_GOLDEN_MD5.get("sl1"),
       "(L1c) with the control-zone reserve switched off the fixture geometry is sl1's pinned value %s"
       % FIXTURE_GOLDEN_MD5.get("sl1"))
    # (L10) 實際機隊黃金值（D-LY4）
    fl = default_fleet()
    dg = fleet_digest(fl)
    md = doc_md5(layout_doc(fl))
    pin = (GOLDEN_DOC_MD5.get(LAYOUT_VERSION) or {}).get(dg[:16])
    if pin is None:
        print("NOTE (L10) fleet %s… unpinned for %s (doc md5 %s) — add via --golden after review"
              % (dg[:12], LAYOUT_VERSION, md), flush=True)
    else:
        ck(md == pin, "(L10) roster fleet golden md5 %s == pinned %s" % (md, pin))
    # (L11) 機隊變 ⇒ stamp 變；cached_doc 回同一物件
    reset_cache()
    a, b = cached_doc(), cached_doc()
    ck(a is b and doc_stamp() == ("synth-layout", LAYOUT_VERSION, dg),
       "(L11) cached_doc returns one shared object; stamp = (synth-layout, version, fleet_digest)")
    fl2 = [dict(x) for x in fl]
    fl2[0]["display_name"] = str(fl2[0].get("display_name")) + "x"
    ck(fleet_digest(fl2) != dg, "(L11b) a changed fleet changes the digest (and therefore the stamp)")
    # (L12) C-SY1／C-SY15：子進程攔截 open；sys.modules 不含禁用模組；跨進程逐位元同
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", _CHILD, here], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=here, timeout=300)
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("@@WL@@")), None)
    ck(line is not None, "(L12) child ran (rc=%s) %s" % (r.returncode, (r.stderr or "")[-400:]))
    if line:
        out = json.loads(line[6:])
        bad = [p for p in out["opened"] if any(s in p for s in _FORBIDDEN_PATH_PARTS)]
        ck(not bad, "(L12a) no real floor-plan / DXF / staffing / fixture / state file opened while importing and "
           "building the synthetic layout (%d opens in total): %s" % (len(out["opened"]), bad[:5]))
        ck(not out["mods"], "(L12b) forbidden modules never imported: %s" % out["mods"])
        ck(out["md5"] == out["md5b"] == md, "(L12c) cross-process bit-identical: %s %s %s"
           % (out["md5"], out["md5b"], md))
    # (L13) 原始碼：不 import 禁用模組、不呼叫 open
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src.split("# ============================================================================ selftest ===")[0]
    tree = ast.parse(body)
    imps, opens = set(), 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imps.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imps.add(node.module or "")
        elif isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "open":
            opens += 1
    ck(not (imps & set(_FORBIDDEN_IMPORTS)) and opens == 0,
       "(L13) module code imports only the whitelist and never calls open(): imports=%s" % sorted(imps))
    n, f = len(res), res.count(False)
    print("wsynth_layout selftest: %d checks, %d failed" % (n, f), flush=True)
    return 0 if f == 0 else 1


def _main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--golden", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args(argv)
    if a.golden:
        fl = default_fleet()
        ff, fr = _fixture_inputs()
        print(json.dumps({"layout_version": LAYOUT_VERSION, "fleet_key": fleet_digest(fl)[:16],
                          "doc_md5": doc_md5(layout_doc(fl)),
                          "fixture_geometry_md5": geometry_md5(layout_doc(ff, roster=fr))}))
        return 0
    if a.selftest:
        return _selftest(a.v)
    ap.print_help()
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_main())
