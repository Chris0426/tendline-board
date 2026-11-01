#!/usr/bin/env python3
"""Rectilinear, machine-owned regions for the landing plan.

This module deliberately has no application imports and performs no I/O.  The
input coordinates are normalised to a viewBox; the returned points are in that
viewBox's coordinate system so callers can use them directly in SVG paths.

★ The expensive half (`grid_ownership`) and the cheap half (`regions_from_owners`)
are split on purpose: the caller caches the first — it is a pure function of the
coordinate file — and re-runs the second per request, because the mask and the
label material change with what is being drawn.
"""
from __future__ import annotations

import math


# Keep all of the region-engine knobs here.  Consumers must not duplicate them.
# §18.10-c: 1/160 left visible single-cell teeth on the boundary; 1/240 plus the
# open+close pass below is what removes them.
GRID_WIDTH_RATIO = 1.0 / 240.0
RADIUS_WIDTH_RATIO = 0.03            # zones; bundles pass their own (§18.10-c ④)
EROSION_CELLS = 1
CHAIKIN_ROUNDS = 1
LABEL_SPOT_LIMIT = 300
# 區標候選格的「離島邊界至少幾格」門檻：先要 3，島太瘦退 2，再瘦就不挑（見 `_label_spots`）。
LABEL_CLEAR_MIN = 3
LABEL_CLEAR_FALLBACK = 2
LABEL_CLEAR_CAP = 4                  # 只問「有沒有 ≥3」⇒ 量到 4 就夠，不必跑到收斂


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalise_items(items, view_width, view_height, group_field):
    """Validate normalised records and convert them to viewBox coordinates."""
    out = []
    for raw in items or ():
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "")
        group = str(raw.get(group_field) or raw.get("group") or raw.get("zone") or "")
        if not key or not group:
            continue
        x, y = _number(raw.get("x")), _number(raw.get("y"))
        w, h = _number(raw.get("w")), _number(raw.get("h"))
        if w <= 0 or h <= 0:
            continue
        out.append({"key": key, "group": group,
                    "x0": x * view_width, "y0": y * view_height,
                    "x1": (x + w) * view_width, "y1": (y + h) * view_height,
                    "cx": (x + w / 2.0) * view_width,
                    "cy": (y + h / 2.0) * view_height})
    return out


def _rect_distance(px, py, rect):
    """Euclidean distance from a point to a closed rectangle (zero inside)."""
    dx = max(rect["x0"] - px, 0.0, px - rect["x1"])
    dy = max(rect["y0"] - py, 0.0, py - rect["y1"])
    return math.hypot(dx, dy)


def grid_ownership(items, view_width, view_height, group_field="group",
                   radius_ratio=None):
    """Return `(owners, seeded, records, cell)` — the expensive, cacheable half.

    A cell belongs to the closest machine *rectangle*, not its centre.  Centre
    cells are seeded back to their own record after the nearest calculation;
    this makes the primary ownership invariant explicit rather than accidental.

    ⚠ `radius_ratio` is per-call because zones and bundles want different reach
    (§18.10-c ④: 3 % vs 1.5 %) — a module constant would make one of them wrong.
    """
    vw, vh = _number(view_width), _number(view_height)
    if vw <= 0 or vh <= 0:
        return {}, {}, [], 0.0
    records = _normalise_items(items, vw, vh, group_field)
    cell = vw * GRID_WIDTH_RATIO
    cols = max(1, int(math.ceil(vw / cell)))
    rows = max(1, int(math.ceil(vh / cell)))
    radius = vw * (RADIUS_WIDTH_RATIO if radius_ratio is None else _number(radius_ratio))
    owners, seeded = {}, {}
    ordered = sorted(records, key=lambda r: r["key"])
    for gy in range(rows):
        py = min(vh, (gy + 0.5) * cell)
        for gx in range(cols):
            px = min(vw, (gx + 0.5) * cell)
            best = None
            for rec in ordered:
                d = _rect_distance(px, py, rec)
                candidate = (d, rec["key"], rec)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
            if best is not None and best[0] <= radius:
                # A cell that even partly covers another group's machine box is
                # intentionally left unowned.  The resulting gap is what makes
                # a region incapable of intersecting a foreign machine rectangle.
                x0, y0 = gx * cell, gy * cell
                x1, y1 = min(vw, (gx + 1) * cell), min(vh, (gy + 1) * cell)
                foreign = any(rec["group"] != best[2]["group"]
                              and x0 < rec["x1"] and x1 > rec["x0"]
                              and y0 < rec["y1"] and y1 > rec["y0"]
                              for rec in ordered)
                if not foreign:
                    owners[(gx, gy)] = best[2]["group"]
    for rec in ordered:
        gx = min(cols - 1, max(0, int(rec["cx"] // cell)))
        gy = min(rows - 1, max(0, int(rec["cy"] // cell)))
        # A collision would mean the source layout itself put two centres in one
        # fine cell.  Preserve deterministic ownership; normal layouts have none.
        if (gx, gy) not in seeded:
            owners[(gx, gy)] = rec["group"]
            seeded[(gx, gy)] = rec["key"]
    return owners, seeded, records, cell


def _grid_ownership(items, view_width, view_height, group_field="group"):
    """Legacy 6-tuple shape kept for the selftest and the smoke round."""
    owners, seeded, records, cell = grid_ownership(
        items, view_width, view_height, group_field)
    vw, vh = _number(view_width), _number(view_height)
    cols = max(1, int(math.ceil(vw / cell))) if cell else 0
    rows = max(1, int(math.ceil(vh / cell))) if cell else 0
    return owners, seeded, records, cell, cols, rows


def _components(cells):
    """4-connected components of integer grid cells, in stable order."""
    left = set(cells)
    out = []
    while left:
        start = min(left)
        left.remove(start)
        stack, group = [start], {start}
        while stack:
            x, y = stack.pop()
            for nxt in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nxt in left:
                    left.remove(nxt)
                    group.add(nxt)
                    stack.append(nxt)
        out.append(group)
    return out


def _neighbours(pos):
    x, y = pos
    return ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))


def _erode(cells, steps=1):
    """Erode by 4-neighbour cells so different groups never share an edge."""
    kept = set(cells)
    for _ in range(max(0, int(steps))):
        kept = {p for p in kept if all(n in kept for n in _neighbours(p))}
        if not kept:
            break
    return kept


def _dilate(cells, steps=1):
    grown = set(cells)
    for _ in range(max(0, int(steps))):
        grown |= {n for p in grown for n in _neighbours(p)}
    return grown


def _morph_open(cells):
    """Erode→dilate: bites off single-cell teeth without shrinking the body."""
    return _dilate(_erode(cells, 1), 1)


def _morph_close(cells):
    """Dilate→erode: fills single-cell nicks without growing the body."""
    return _erode(_dilate(cells, 1), 1)


def _point_in_ring(ring, point):
    """Ray casting; the ring is a closed, non self-intersecting loop."""
    px, py = point
    inside = False
    for i in range(len(ring)):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % len(ring)]
        if (y0 > py) != (y1 > py) and px < x0 + (py - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _filled(outer, holes, point):
    """Inside the painted area = inside the outer ring and in none of the holes."""
    return _point_in_ring(outer, point) and not any(
        _point_in_ring(h, point) for h in holes or ())


def _outline(cells, cell):
    """Trace **every** boundary loop of a polyomino, largest (the outer ring) first.

    ⚠ Returning only the biggest loop would silently *fill* the holes, and a hole
    is exactly where a foreign machine can sit: the ownership pass leaves the
    cells over a foreign rectangle unowned, so its neighbours form a ring around
    it.  A filled ring is the bbox mistake wearing a rectilinear costume, which is
    the whole thing C-LD25 exists to forbid — so the caller gets the holes too and
    decides which ones must stay punched out.
    """
    edges = {}
    for gx, gy in cells:
        x0, y0, x1, y1 = gx * cell, gy * cell, (gx + 1) * cell, (gy + 1) * cell
        for neighbour, edge in (
            ((gx, gy - 1), ((x0, y0), (x1, y0))),
            ((gx + 1, gy), ((x1, y0), (x1, y1))),
            ((gx, gy + 1), ((x1, y1), (x0, y1))),
            ((gx - 1, gy), ((x0, y1), (x0, y0))),
        ):
            if neighbour not in cells:
                edges.setdefault(edge[0], []).append(edge[1])
    loops = []
    while edges:
        start = min(edges)
        point, loop = start, [start]
        while True:
            ends = edges.get(point) or []
            if not ends:
                break
            nxt = min(ends)
            ends.remove(nxt)
            if not ends:
                edges.pop(point, None)
            point = nxt
            if point == start:
                break
            loop.append(point)
        if len(loop) >= 3 and point == start:
            loops.append(loop)
    if not loops:
        return []

    def area(poly):
        return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                       - poly[(i + 1) % len(poly)][0] * poly[i][1]
                       for i in range(len(poly))) / 2.0)

    def simplify(poly):
        changed = True
        while changed and len(poly) >= 3:
            changed, compact = False, []
            for i, cur in enumerate(poly):
                prev, nxt = poly[i - 1], poly[(i + 1) % len(poly)]
                if ((cur[0] - prev[0]) * (nxt[1] - cur[1])
                        == (cur[1] - prev[1]) * (nxt[0] - cur[0])):
                    changed = True
                    continue
                compact.append(cur)
            poly = compact
        return [(round(x, 6), round(y, 6)) for x, y in poly]

    out = [simplify(poly) for poly in sorted(loops, key=area, reverse=True)]
    return [poly for poly in out if len(poly) >= 3]


def _chaikin(ring, rounds=CHAIKIN_ROUNDS):
    """Corner cutting on a closed ring: each edge keeps its middle half.

    One round is enough to turn the staircase into a rounded outline while long
    straight runs stay straight (only their last quarter bends).  More rounds
    would start pulling the boundary away from the cells it describes, and the
    invariant check would then reject the whole island rather than a corner.
    """
    pts = list(ring or ())
    for _ in range(max(0, int(rounds))):
        if len(pts) < 3:
            break
        nxt = []
        for i in range(len(pts)):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % len(pts)]
            nxt.append((ax + (bx - ax) * 0.25, ay + (by - ay) * 0.25))
            nxt.append((ax + (bx - ax) * 0.75, ay + (by - ay) * 0.75))
        pts = nxt
    return [(round(x, 4), round(y, 4)) for x, y in pts]


def _occupied_cells(records, cell):
    """Every grid cell any machine rectangle touches (rasterised once)."""
    out = set()
    if cell <= 0:
        return out
    for rec in records:
        for gx in range(int(math.floor(rec["x0"] / cell)),
                        int(math.floor((rec["x1"] - 1e-9) / cell)) + 1):
            for gy in range(int(math.floor(rec["y0"] / cell)),
                            int(math.floor((rec["y1"] - 1e-9) / cell)) + 1):
                out.add((gx, gy))
    return out


def cell_clearance(cells, cap=LABEL_CLEAR_CAP):
    """How many cells deep each cell sits inside ``cells`` (8-connected erosion).

    ``0`` means the cell touches the island's boundary; ``3`` means a 7×7 block of
    island cells fits around it.  Capped because the callers only ever ask
    "at least N?" — running it to convergence on a 5 000-cell island is waste.
    """
    out = dict.fromkeys(cells, 0)
    cur = set(cells)
    for level in range(1, max(0, int(cap)) + 1):
        cur = {p for p in cur
               if all((p[0] + dx, p[1] + dy) in cur
                      for dx in (-1, 0, 1) for dy in (-1, 0, 1))}
        if not cur:
            break
        for p in cur:
            out[p] = level
    return out


def _label_spots(cells, occupied, cell, limit=LABEL_SPOT_LIMIT):
    """§18.10-c ①: candidate label anchors — cells **no machine sits on**, nearest
    the island's own bbox top-left first.  The caller still has to check that the
    label's box clears the machines and the other labels; this only guarantees the
    anchor itself is inside the island and on empty floor.

    ⚠ **Cell centres, not cell corners.**  Chaikin rounds the corners off, so a
    boundary cell's top-left corner can land just *outside* the smoothed ring even
    though the cell is in the island — an anchor that fails its own invariant.
    The centre is always comfortably inside.

    ★ **Two-stage ordering** (§18.10-d follow-up): sorting by distance-to-top-left
    alone always lands on the rim — measured clearance was 0 cells for all three
    zone labels, against island maxima of 13–14.  So first keep only candidates
    that are at least ``LABEL_CLEAR_MIN`` cells deep inside the island (stepping
    down to ``LABEL_CLEAR_FALLBACK`` and then to "anywhere" for islands too thin
    to offer any), and *then* take the one nearest the bbox top-left.  Depth first
    is what keeps a label out of a narrow spur; nearest-top-left second is what
    keeps it where a reader looks for it.
    """
    free = [pos for pos in cells if pos not in occupied]
    if not free:
        return []
    deep = cell_clearance(cells)
    pool = free
    for floor in (LABEL_CLEAR_MIN, LABEL_CLEAR_FALLBACK):
        picked = [pos for pos in free if deep.get(pos, 0) >= floor]
        if picked:
            pool = picked
            break
    ox = min(gx for gx, _ in cells) * cell
    oy = min(gy for _, gy in cells) * cell
    scored = sorted((math.hypot(gx * cell - ox, gy * cell - oy), gy, gx)
                    for gx, gy in pool)
    return [(round((gx + 0.5) * cell, 4), round((gy + 0.5) * cell, 4))
            for _d, gy, gx in scored[:limit]]


def regions_from_owners(owners, seeded, records, cell, allow_cells=None, min_members=2):
    """Cheap half: mask → islands → erode → open/close → outline → Chaikin.

    ``allow_cells`` is ``{group: set_of_cells}``; a group present there keeps only
    those cells (§18.10-c ④ uses it to force bundle cells ⊆ their zone's cells),
    a group absent from it is unrestricted.

    ``min_members`` is how many machines an island must own to be drawn.  Zones
    keep the original **2** (a lone machine's own coloured edge says it better
    than a loop around it), while bundles pass **1** since §18.16 ruling 2: a
    roster member that ends up alone still has to be visibly in its bundle, so it
    gets a satellite frame.  The knob is a parameter and not a second engine —
    one geometry, one set of invariants (C-LD25 is unaffected either way: the
    island's cells are still only cells this group owns).
    """
    by_group = {}
    for pos, group in owners.items():
        if allow_cells is not None:
            ok = allow_cells.get(group)
            if ok is not None and pos not in ok:
                continue
        by_group.setdefault(group, set()).add(pos)
    source_by_cell = {}
    for pos, key in seeded.items():
        source_by_cell.setdefault(pos, []).append(key)
    # ★ 平滑（尤其 close）會**加格**，加到別區頭上就毀了 C-LD25。這張表記「每個格的
    #   4 鄰裡出現過哪些 group」⇒ 一個群只准站在「halo 只有自己」的格上，
    #   於是兩群平滑後的格永遠不可能 4 相鄰，也就永遠不共邊。一次 O(格數) 建好。
    halo = {}
    for pos, group in owners.items():
        halo.setdefault(pos, set()).add(group)
        for n in _neighbours(pos):
            halo.setdefault(n, set()).add(group)
    occupied = _occupied_cells(records, cell)
    out = {}
    for group, cells in sorted(by_group.items()):
        safe = {pos for pos, owned in halo.items() if owned == {group}}
        islands = []
        for raw_component in _components(cells):
            # Membership is decided before erosion.  Erosion is visual spacing,
            # never a change in which machine owns the underlying region.
            members = sorted(key for pos, keys in source_by_cell.items()
                             if pos in raw_component for key in keys)
            if len(members) < min_members:
                continue
            shaped = _morph_close(_morph_open(_erode(raw_component, EROSION_CELLS))) & safe
            for component in _components(shaped):
                rings = _outline(component, cell)
                if not rings:
                    continue
                outer, inner = rings[0], rings[1:]
                holes = [ring for ring in inner
                         if any(rec["group"] != group
                                and _point_in_ring(ring, (rec["cx"], rec["cy"]))
                                for rec in records)]
                soft_outer = _chaikin(outer)
                soft_holes = [_chaikin(h) for h in holes]
                # §18.10-c ②: smoothing moves the boundary, so re-check the one
                # invariant it could break — and fall back **for this island only**
                # rather than dropping the whole zone's rounding.
                if any(rec["group"] != group
                       and _filled(soft_outer, soft_holes, (rec["cx"], rec["cy"]))
                       for rec in records):
                    soft_outer, soft_holes = outer, holes
                islands.append({"points": soft_outer, "holes": soft_holes,
                                "members": members, "cells": frozenset(component),
                                "label_spots": _label_spots(component, occupied, cell)})
        if islands:
            out[group] = islands
    return out


def build_regions(items, view_width, view_height, *, group_field="group",
                  radius_ratio=None, allow_cells=None, min_members=2):
    """Return ``{group: [{points, holes, members, cells, label_spots}, …]}``.

    ``items`` uses normalised ``x/y/w/h`` coordinates.  ``points`` is the outer
    ring and ``holes`` the rings that must stay punched out, both closed-by-the-
    caller, in viewBox units.  One-machine islands are purposefully omitted at
    the default ``min_members=2``: their coloured machine edge is the less
    ambiguous representation.  Pass ``min_members=1`` to keep them (bundles do,
    §18.16 ruling 2 — a satellite frame, drawn one step fainter and chip-less).

    ★ **A hole is kept only when a foreign machine's centre sits in it** (C-LD25's
    invariant, made structural rather than incidental).  Empty pockets — aisles
    further than R from every machine — are filled, because a dashed loop around
    an empty aisle is noise, not information.
    """
    owners, seeded, records, cell = grid_ownership(
        items, view_width, view_height, group_field, radius_ratio)
    if not owners:
        return {}
    return regions_from_owners(owners, seeded, records, cell, allow_cells, min_members)


def _selftest():
    """Pure-Python invariants used by the landing smoke round."""
    items = [
        {"key": "A", "group": "alpha", "x": .12, "y": .20, "w": .05, "h": .06},
        {"key": "B", "group": "alpha", "x": .20, "y": .20, "w": .05, "h": .06},
        {"key": "C", "group": "beta",  "x": .55, "y": .20, "w": .05, "h": .06},
        {"key": "D", "group": "beta",  "x": .63, "y": .20, "w": .05, "h": .06},
        {"key": "E", "group": "single", "x": .84, "y": .74, "w": .04, "h": .05},
    ]
    owners, seeded, records, _cell, _cols, _rows = _grid_ownership(items, 1000, 600)
    record_by_key = {r["key"]: r for r in records}
    # ① Every machine's centre cell belongs to itself; ② each cell stores one owner.
    assert all(owners[pos] == record_by_key[key]["group"] for pos, key in seeded.items())
    assert all(isinstance(owner, str) and owner for owner in owners.values())
    # ③ no owned cell intersects a different group's machine rectangle.
    assert all(not (owner != rec["group"]
                    and gx * _cell < rec["x1"] and (gx + 1) * _cell > rec["x0"]
                    and gy * _cell < rec["y1"] and (gy + 1) * _cell > rec["y0"])
               for (gx, gy), owner in owners.items() for rec in records)
    regions = build_regions(items, 1000, 600)
    # Single-machine islands are not emitted at the default threshold …
    assert "single" not in regions and set(regions) == {"alpha", "beta"}
    # … and are emitted at min_members=1 (§18.16 ruling 2, the bundle satellite
    # frame).  Nothing else about the shape changes: same invariants, same rings.
    sat = build_regions(items, 1000, 600, min_members=1)
    assert set(sat) == {"alpha", "beta", "single"}
    assert len(sat["single"]) == 1 and len(sat["single"][0]["members"]) == 1
    assert [i["points"] for i in sat["alpha"]] == [i["points"] for i in regions["alpha"]]
    assert all(not _filled(island["points"], island["holes"], (rec["cx"], rec["cy"]))
               for group, islands in sat.items() for island in islands
               for rec in records if rec["group"] != group)
    # ★ C-LD25 on the drawn shape: no foreign machine centre in any painted area,
    #   and each island really does hold its own members.
    assert all(not _filled(island["points"], island["holes"], (rec["cx"], rec["cy"]))
               for group, islands in regions.items() for island in islands
               for rec in records if rec["group"] != group)
    assert all(_filled(islands[0]["points"], islands[0]["holes"],
                       (record_by_key[m]["cx"], record_by_key[m]["cy"]))
               for group, islands in regions.items() for m in islands[0]["members"])
    for group, islands in regions.items():
        assert all(len(island["members"]) >= 2 and len(island["points"]) >= 3
                   for island in islands)
        # §18.10-c ①: every label anchor is on empty floor inside its own island.
        for island in islands:
            occupied = _occupied_cells(records, _cell)
            assert island["label_spots"], "an island with free floor must offer an anchor"
            # spot＝格**中心** ⇒ `//` 就是它所屬的格，且該點必在平滑後的環內。
            assert all((int(x // _cell), int(y // _cell)) in island["cells"]
                       and (int(x // _cell), int(y // _cell)) not in occupied
                       and _filled(island["points"], island["holes"], (x, y))
                       for x, y in island["label_spots"])
    # ★ 平滑後仍不共邊：兩群的格集合連 4 相鄰都不會有（`safe` 遮罩的保證）。
    cellsets = {g: set().union(*[i["cells"] for i in isl]) for g, isl in regions.items()}
    for ga, sa in cellsets.items():
        for gb, sb in cellsets.items():
            if ga >= gb:
                continue
            assert not (sa & sb)
            assert not any(n in sb for p in sa for n in _neighbours(p))

    # ★ 洞的形（別區機台被本區機台包起來）：外環把洞填掉的話 C-LD25 就只是運氣好。
    ring = [{"key": "R%d" % i, "group": "ring",
             "x": (500 + 60 * math.cos(i * math.pi / 4) - 10) / 1000.0,
             "y": (300 + 60 * math.sin(i * math.pi / 4) - 10) / 600.0,
             "w": 20 / 1000.0, "h": 20 / 600.0} for i in range(8)]
    ring.append({"key": "IN", "group": "intruder",
                 "x": 490 / 1000.0, "y": 290 / 600.0, "w": 20 / 1000.0, "h": 20 / 600.0})
    donut = build_regions(ring, 1000, 600)
    assert "intruder" not in donut and len(donut.get("ring") or ()) == 1
    island = donut["ring"][0]
    assert island["holes"], "a machine ringed by another zone must punch a hole"
    assert _point_in_ring(island["points"], (500.0, 300.0))          # 外環確實包住它
    assert not _filled(island["points"], island["holes"], (500.0, 300.0))
    assert len(island["members"]) == 8

    # ★ §18.10-c ④：`allow_cells` 把一群的格夾進另一群的格裡（綑格 ⊆ 區格）。
    owners2, seeded2, records2, cell2 = grid_ownership(items, 1000, 600)
    alpha_cells = {p for p, g in owners2.items() if g == "alpha"}
    trimmed = regions_from_owners(owners2, seeded2, records2, cell2,
                                  allow_cells={"beta": alpha_cells})
    assert "beta" not in trimmed and "alpha" in trimmed
    return True


if __name__ == "__main__":
    _selftest()
    print("board_layout_regions: PASS")
