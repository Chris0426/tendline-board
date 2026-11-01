---
name: Tendline Board UI v2
description: One neutral, colourless chrome; semantic colour only on data marks; two editions (Laptop = cards / Projector = hairline alert stack; internal codes 15 / 2) from one stylesheet; 21 pages after batch 3.
colors:
  # neutral layer (board.css :root 26-27; dark 64-67). Light value here; dark in .impeccable/design.json colorMeta.
  ground: "#f4f6fb"
  panel: "#ffffff"
  panel-2: "#eef2f8"
  line: "#d8e0ec"
  track: "#e6ebf3"
  ink: "#1b2433"
  muted: "#5c6b82"
  hover: "#b9c6db"
  # neutral data-bar fill (board.css :root 27; dark 67/83)
  bar-neutral: "#7d8ca1"
  # v2 neutral aliases (board.css 29) — no new hue: --line2 = --hover, --focus = --mut
  # verdict layer (board.css 31-32; dark 68-69)
  good: "#1e7d4f"
  warn: "#b07d12"
  bad: "#b3423a"
  unknown: "#b07d12"
  product-tag: "#536f99"
  product-tag-ink: "#ffffff"
  # downtime four (board.css 33; parts dark 70)
  st-repair: "#dc2626"
  st-pm: "#2563eb"
  st-parts: "#6b21a8"
  st-vendor: "#d97706"
  # other frozen semantics (board.css 34-36, 46, 48)
  st-legacy: "#9ca3af"
  st-hwait: "#8b98a9"
  tex-stripe: "rgba(0,0,0,.30)"
  st-dormant: "#64748b"
  rw-off: "#cbd5e1"
  rw-on: "#eef4fb"
  eq-off-tint: "rgba(15,23,42,.045)"
  src-snapshot: "#2f7fc4"
  on-color: "#ffffff"
  # tints and edges ported from the /equipment and /station literals (board.css 38-40; dark 74-76, only the two verdict tints change)
  tint-warn: "rgba(176,125,18,.10)"
  tint-warn-2: "rgba(176,125,18,.12)"
  tint-good: "rgba(30,125,79,.09)"
  tint-bad: "rgba(179,66,58,.14)"
  cell-edge: "rgba(0,0,0,.10)"
  seg-edge: "rgba(0,0,0,.14)"
  rw-stripe-ink: "rgba(255,255,255,.55)"
  rw-stripe-gap: "rgba(255,255,255,0)"
  # batch 2 root tokens (board.css 41-44): low-confidence stripe, print sheet (paper is always light)
  low-stripe: "rgba(0,0,0,.16)"
  print-paper: "#ffffff"
  print-ink: "#000000"
  print-rule: "#bbbbbb"
  # batch 3 root token (board.css 45): hairline between timeline segments (both themes)
  tl-seg-edge: "rgba(0,0,0,.18)"
  # bundle grouping bands, /manpower only (board.css 58-59; dark 86-87 — only edge0/edge1 are re-declared in dark)
  band0: "rgba(37,99,235,.045)"
  band1: "rgba(13,148,136,.05)"
  band2: "rgba(124,58,237,.05)"
  band3: "rgba(100,116,139,.07)"
  band-edge0: "#2563eb"
  band-edge1: "#0d9488"
  band-edge2: "#7c3aed"
  band-edge3: "#64748b"
  # bundle identity ramp, /dispatch + landing dispatch pane (board.css 60): aliases of --fg / --mut / --bar-neutral / --hover, resolved light values (R30)
  band-id0: "#1b2433"
  band-id1: "#5c6b82"
  band-id2: "#7d8ca1"
  band-id3: "#b9c6db"
  # landing floor plan (board.css 49-53; dark 77-78)
  plan-ink: "#c6d0e0"
  zone-za: "#2563eb"
  zone-zb: "#7c3aed"
  zone-zc: "#0d9488"
  # page-local, station only (board.css 1156 region)
  cap-q: "#d97706"
  # page-local, daily only (board.css 526)
  daily-bar-neutral: "#7d8ca1"  # re-declared, same value as root bar-neutral
  daily-b-prod: "#1e7d4f"
  daily-b-wo: "#dc2626"
  daily-b-starved: "#6b21a8"
  daily-b-blocked: "#2563eb"
  daily-b-unexp: "#8b98a9"
  daily-p-scrap: "#be185d"
  daily-p-rework: "#b07d12"
  daily-p-slow: "#0d9488"
  # page-local, monthly (board.css 1355; --acc-n/--gapfg re-declared with the same values on downtime 1635)
  acc-n: "#2563eb"
  gapfg: "#9aa7bb"
  l-down: "#dc2626"
  l-yield: "#b07d12"
  l-rework: "#2563eb"
  # page-local, availability (board.css 1458)
  av-up: "#6fae9c"
  # page-local, downtime monthly (board.css 1635)
  dt-za: "#1e293b"
  dt-zb: "#64748b"
  dt-zc: "#cbd5e1"
  dti-za: "#ffffff"
  dti-zb: "#ffffff"
  dti-zc: "#1b2433"
  # page-local, pm (board.css 1710)
  pm-age1: "#8b98a9"
  pm-age2: "#b07d12"
  pm-age3: "#c4803f"
  pm-age4: "#c4504f"
  pm-seg1: "#7c93b5"
  pm-seg2: "#41608c"
  pm-seg3: "#b8c6da"
  pm-area-za: "#3f7a8c"
  pm-area-zb: "#5a6fae"
  pm-area-zc: "#3f7f63"
  pm-area-zd: "#8a6aa8"
  pm-bar: "#b9c9e0"
  pm-bar2: "#7d95b8"
  pm-line1: "#2f5597"
  pm-line2: "#9c5fb0"
  # page-local, health (board.css 2246; no dark override) — same value as st-legacy, its own name (R28)
  health-mh-wait: "#9ca3af"
  # page-local, manpower (board.css 2482; dark 2486). --acc-n re-declared = monthly's value
  manpower-acc-o: "#0d9488"
  manpower-hatch: "rgba(0,0,0,.13)"
  manpower-mp-strip: "rgba(27,36,51,.55)"
  # page-local, cost (board.css 2600; dark 2604). --acc-n/--gapfg re-declared = monthly's values
  cost-b-one: "#2563eb"
  cost-b-part: "#1e7d4f"
  cost-b-unk: "#8a94a6"
  # page-local, budget (board.css 2744; dark 2748 — tk-overt dark = the same literal, R31)
  budget-acc-a: "#1e7d4f"
  budget-acc-p: "#b07d12"
  budget-tk-overt: "rgba(179,66,58,.13)"
  # page-local, rework (board.css 2855; dark rgb(230,170,90)) — one hue = magnitude (R32)
  rework-rw-seq: "rgb(176,108,37)"
  # machine and dispatch declare no page palette (board.css 2224, 2380)
typography:
  body:
    fontFamily: "\"Public Sans\", \"Noto Sans TC\", \"Microsoft JhengHei\", system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tabular-nums"
  title:
    fontFamily: "\"Public Sans\", \"Noto Sans TC\", \"Microsoft JhengHei\", system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.25
  small:
    fontFamily: "\"Public Sans\", \"Noto Sans TC\", \"Microsoft JhengHei\", system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
  note:
    fontFamily: "\"Public Sans\", \"Noto Sans TC\", \"Microsoft JhengHei\", system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
  label:
    fontFamily: "\"Public Sans\", \"Noto Sans TC\", \"Microsoft JhengHei\", system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "normal"
  figure:
    fontFamily: "\"Public Sans\", \"Noto Sans TC\", \"Microsoft JhengHei\", system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1
    fontFeature: "tabular-nums"
  figure-2:
    fontFamily: "\"Public Sans\", \"Noto Sans TC\", \"Microsoft JhengHei\", system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.15
    fontFeature: "tabular-nums"
rounded:
  none: "0px"
  label: "2px"
  control: "3px"
  card: "4px"
spacing:
  row: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  gap: "16px"
  pane-h: "202px"
  side-w: "344px"
  nav-h: "48px"
  main-max: "1560px"
components:
  panel-15:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.card}"
    padding: "9px 12px 8px"
  panel-2:
    backgroundColor: "transparent"
    rounded: "{rounded.none}"
    padding: "10px 2px 8px"
  nav-tab:
    textColor: "{colors.muted}"
    typography: "{typography.small}"
    padding: "13px 6px 11px"
  nav-tab-on:
    textColor: "{colors.ink}"
    typography: "{typography.small}"
    padding: "13px 6px 11px"
  seg-toggle:
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "3px 7px"
  seg-toggle-on:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.panel}"
    typography: "{typography.label}"
    padding: "3px 7px"
  source-badge:
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.label}"
    padding: "1px 6px"
  legend-item:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.small}"
    rounded: "{rounded.control}"
    padding: "3px 9px"
  lamp:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    size: "10px"
  kpi-value:
    textColor: "{colors.ink}"
    typography: "{typography.figure-2}"
  table-head:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    padding: "5px 8px"
  icon-button:
    textColor: "{colors.muted}"
    rounded: "{rounded.control}"
    width: "26px"
    height: "24px"
  zoom-button:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.note}"
    rounded: "{rounded.control}"
    padding: "3px 9px"
  verdict-dot:
    size: "8px"
  caution-lamp:
    size: "7px"
  help-glyph:
    textColor: "{colors.muted}"
    size: "14px"
  help-tip:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ground}"
    rounded: "{rounded.card}"
    padding: "8px 11px"
  loss-brick:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.card}"
    padding: "10px 14px"
  degraded-card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.card}"
    padding: "14px 16px"
  toggle-chip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    typography: "{typography.small}"
    rounded: "{rounded.control}"
    padding: "3px 10px"
  toggle-chip-on:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.panel}"
    typography: "{typography.small}"
    rounded: "{rounded.control}"
    padding: "3px 10px"
  page-tab:
    textColor: "{colors.muted}"
    padding: "7px 16px"
  page-tab-on:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    padding: "7px 16px"
  outline-badge:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.label}"
    padding: "1px 7px"
  owner-bar:
    backgroundColor: "{colors.track}"
    rounded: "{rounded.card}"
    height: "20px"
  attainment-bar:
    backgroundColor: "{colors.track}"
    fillColor: "{colors.bar-neutral}"
    rounded: "{rounded.card}"
    height: "14px"
  timeline-card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.card}"
    padding: "18px 20px"
  timeline-bar:
    backgroundColor: "{colors.track}"
    rounded: "{rounded.card}"
    height: "30px"
  timeline-stop:
    backgroundColor: "{colors.track}"
    textColor: "{colors.ink}"
    typography: "{typography.small}"
    rounded: "{rounded.card}"
    padding: "6px 10px"
  caveat-box:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.note}"
    rounded: "{rounded.card}"
    padding: "8px 13px"
  coverage-chip:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.label}"
    padding: "2px 9px"
  dispatch-lamp:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    size: "10px"
  bundle-identity-band:
    width: "4px"
  bundle-group-band:
    backgroundColor: "{colors.band0}"
    width: "4px"
  manpower-bar:
    backgroundColor: "{colors.track}"
    rounded: "{rounded.control}"
    height: "9px"
  category-badge:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.label}"
    padding: "1px 7px"
  category-badge-fact:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.panel}"
    typography: "{typography.label}"
    rounded: "{rounded.label}"
    padding: "1px 7px"
  budget-track:
    backgroundColor: "{colors.track}"
    rounded: "{rounded.card}"
    height: "16px"
  budget-track-over:
    backgroundColor: "{colors.budget-tk-overt}"
    rounded: "{rounded.card}"
    height: "16px"
  nav-segment:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    typography: "{typography.small}"
    padding: "4px 12px"
  nav-segment-current:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.panel}"
    typography: "{typography.small}"
    padding: "4px 12px"
  rework-bar:
    backgroundColor: "{colors.rework-rw-seq}"
    rounded: "{rounded.card}"
    height: "8px"
---

# Design System: Tendline Board UI v2

Scope: the v2 shell, the batch-1 pages (`/` landing, `/daily`, `/equipment` family, `/station` index + `/station/{cls}`), the batch-2 pages (`/monthly`, `/availability`, `/utilization`, `/equipment/downtime/monthly`, `/equipment/pm`, `/output` + `/yield` + `/yield/station`) and the batch-3 pages (`/machine/{eid}` + the shared `_timeline` fragment, `/equipment/health`, `/dispatch`, `/manpower`, `/cost` + `/cost/forecast` + `/cost/notes` and their fragments, `/budget`, `/rework`), as shipped in `static/v2/board.css`, `static/v2/board.js`, `templates/v2/**` (batch 1 commits `d587f2e`…`aeac4f2` plus its fixes; batch 2 `3adb7b3`…`94b1f3a`; batch 3 through `8819bd9`). Every value below is copied from a line in those files. Line numbers in the batch-1/2 sections refer to `board.css` at `94b1f3a` (the batch-2 ported blocks now sit at `:1396-1985` and the batch-2 discipline at `:1987-2170` at HEAD; earlier anchors past `:230` are offset by about +48); anchors in the batch-3 sections and in every sentence touched on 2026-09-28 (batch 3) refer to `board.css` at `8819bd9` (batch 3: pack A `:2172-2373`, pack B `:2374-2586`, pack C1 `:2588-2732`, pack C2 `:2733-2898`). Product truth lives in `PRODUCT.md`; colour law and conflicts live in `SPEC-UI-V2.md` §4 and appendix A/C; the binding rulings live in `_UI-V2-KEEP-BATCH2.md` §H (R10–R22) and `_UI-V2-KEEP-BATCH3.md` §H (R23–R42). This file records what the build actually does.

## Overview

**Creative North Star: "The Crew-Alerting Panel"**

The system borrows the grammar of an aircraft crew-alerting display and none of its costume: normal state says nothing, only abnormal state carries colour, abnormal items stack with the most severe on top, every figure sits in the same slot every visit, and the chrome around the data has no hue at all. Light and dark are both first-class (theme follows the OS; there is no JS theme switch, `board.css:24, 64-95`, `_base.html`). The ground is a cool near-white or a deep navy-black; text is ink; the only colour on a page is a data mark: a machine footprint, a bar, a 10px lamp, an 8px verdict dot, a status chip.

One codebase ships **two editions** selected by `<html data-ui>`: **1.5** keeps the current information order and draws panels as quiet 1px cards (radius 4, no shadow); **2.0** removes the cards, separates sections with hairline rules, moves the alert stack beside the map, turns the line-level figures into a strip, and lets normal tiles lose their frame and colour. They are variants of one system, not two systems: the tokens, components, macros, type ramp, and colour law are identical; only `[data-ui="15"]` / `[data-ui="2"]` branches differ (`board.css:178-181, 192, 390-392, 396-406, 1231-1259, 2088-2117`). A second axis, `data-density="laptop|projector"`, scales data type (x1.25), rows (x1.2) and headline figures (x1.4) while the nav stays pinned (`board.css:97, 122`).

Seventeen of the twenty-one pages are **ported**: their v1 inline `<style>` is scoped under `[data-page=…]` and kept value-for-value (`:520-1167, 1348-1943`), then a hand-written discipline block re-states the v2 law over it (`:1169-1275, 1945-2126`): kickers lose uppercase and tracking, static labels go square, coloured text moves to a lamp or an edge, cards become hairline sections in 2.0. Batch 3 (R23) ports its seven pages the same way but writes each page's block by hand in one pass, discipline applied in place rather than as a second block (`:2172-2898`); the one exception is the single-machine timeline fragment, whose rules carry no page prefix because it renders inside `/machine`, `/utilization` and `/availability` (`.tl`, `:2177-2222`, R34). No 2.0-specific composition was given to any ported page (R23); 2.0 differs from 1.5 by shell only. The build is dense and literal: fixed pixel steps rather than fluid type, tabular numerals everywhere, four equal panes with their own scrollbars and every row kept, explanatory prose demoted to small muted text but never deleted. Confirmed rejections: gradients, decorative shadows, capsule labels on static things, emoji or Unicode glyph icons, uppercase tracked kickers, fake trend lines, text painted in data colour.

**Key Characteristics:**
- Chrome is colourless; colour is reserved for data marks and honesty labels (source badge, draft/weak chips, outline badges).
- Two editions, one system: `15` = cards, `2` = hairline sections + severity-ordered alert stack.
- Neutral + semantic token layers frozen bit-for-bit from v1; no template may contain a hex value (smoke `ui_v2_nohex`).
- Public Sans 400/600 self-hosted with a CJK fallback stack; tabular figures on the body element.
- Fixed constellation: same slot, same place, every visit; four panes at a fixed 202px height.
- Density switch by CSS variables only; nav never grows.
- One legend per page; hollow means "no data", never red, never zero.
- A verdict lives on a dot, a symbol, a bar or an edge; the digits beside it are always ink.
- Only a down machine moves; reduced motion swaps the pulse for a static corner mark.

## Colors

A cool neutral ground with ink text, over which a small, frozen vocabulary of verdict and machine-state colours appears only on data marks. Values in the frontmatter are the light theme; dark values are in `.impeccable/design.json` (`colorMeta.<token>.dark`) and in `board.css:64-95`.

### Primary

There is no brand accent. The active state of the system is **ink**: the on-tab underline (`board.css:135`), the selected segment (`:166`), the selected legend item border (`:366`), the selected month / downtime-type chip (`:2004-2005`), the 2.0 page-tab underline (`:2108`) and the machine label all use `--fg`. Rarity of colour is the point.

### Neutral (`--bg --panel --panel2 --line --track --fg --mut --hover`, `board.css:26-27`)
- **Ground** (`--bg`): page background; also the 2.0 table header, the 2.0 sticky station column and sticky group index background (`:192, 1281-1283, 2107-2112`).
- **Panel** (`--panel`): 1.5 card face, nav bar, table headers, floor-plan stage, hover-card face, toggle-chip face.
- **Panel-2** (`--panel2`): hover fill for rows, tabs, chips and buttons; WO card face; the `/equipment/pm` selected window chip (`:2007`).
- **Line** (`--line`): 1px card border, table row rules, inner rules, bar troughs' border. **Line-2** (`--line2` = `--hover`): section rule in 2.0, drop-down border, chip border on hover, the underline colour of every in-content link (`:180, 197, 2010-2011`). **Track** (`--track`): bar troughs (owner bar, attainment bar), spinner ring.
- **Ink** (`--fg`) and **Muted** (`--mut`): the only two text colours. `--focus` (= `--mut`) paints the only focus ring (`:106`).
- **Bundle identity ramp** (`--band-id0..3`, `board.css:60`; aliases of `--fg`, `--mut`, `--bar-neutral`, `--hover`, so no new hue and no dark block): the four neutral steps that tell one dispatch bundle from the next, on the 4px left band of a `/dispatch` bundle row (`:2415-2418`) and the 3px `.dspband` in the landing dispatch pane (`:269-273`). Identity only, never a verdict (R30).
- **Timeline segment edge** (`--tl-seg-edge`, `board.css:45`, 18% black in both themes): the hairline between adjacent state segments of the single-machine timeline bar (`:2191`); the third of the black-hairline family beside `--cell-edge` and `--seg-edge`.
- **Tints and edges** (`board.css:38-40`): `--tint-warn/--tint-warn-2/--tint-good/--tint-bad` are the four translucent washes behind a source banner, a hot strip row, a demo badge or a warning box (`:818, 827, 974, 1008, 1020, 1837, 1905`); `--cell-edge` and `--seg-edge` are the 10%/14% black hairlines between heat-map cells and band segments (`:883, 936`); `--rw-stripe-ink/-gap` draw the run-window stripe on the station waterfall (`:1099`). All eight are identical in both themes except the two verdict tints.

### Semantic (verdict) layer (`board.css:31-32`, light consolidated 2026-09-28; meanings from SPEC-UI-V2 appendix A)
- **Good / Warn / Bad** (`--good/--warn/--bad`): pass / attention / fail verdicts. Used on lamps (including the `/dispatch` 10px need / lend lamps, `:2423-2424, 2471-2472`), verdict dots, verdict symbols, outline-badge frames, 3px card edges (`/dispatch` needs-help row, `/budget` over-budget card, `:2454, 2777`), the 7px caution lamp and the `/budget` petty-cash bars; never on chrome, never on running prose, and since R30 never on a bundle band (the bands are the neutral `--band-id*` ramp).
- **Unknown** (`--unk`, same value as `--warn`): unknown / demo / draft / synthetic-source honesty markers: source banner (`:229-233`), draft and weak chips (`:377, 381`), stale freshness (`:379`), the outline of every DEMO / RAW badge and the caution lamp on "not available" prose (`:1993, 2021, 2039-2044`).
- **Product tag** (`--ptag` / `--ptag-ink`): the R/A/M product-capability chip; observed = solid, default/idle = outline, no third form (`:474, 795, 1528, 1624`).

### Machine-state layer (`board.css:33-36`, frozen)
- **Downtime four**: `--st-repair` repair red, `--st-pm` planned-maintenance blue, `--st-parts` waiting-parts purple (dark `#b26ef5`), `--st-vendor` waiting-vendor amber. The only four solid downtime colours; green is banned in downtime.
- **Legacy grey** (`--st-legacy`, aliases `--st-setup/--st-maint`): eMaint legacy states; never in a legend.
- **Hold** (`--st-hwait`, alias `--st-htest`) always paired with **`--tex-stripe`**: uptime-hold, the one diagonal-stripe texture on the `/equipment` family; stripes never paint a cell body.
- **Dormant** (`--st-dormant`): long-stopped slate + the ◌ mark; neutral, never in the downtime heat map.
- **Run window** (`--rw-off` / `--rw-on`): outside / inside the scheduled run window; ground colour, not a verdict. **Off-day tint** (`--eq-off-tint`): non-working-day column wash.
- **Snapshot** (`--src-snapshot`): the third source badge colour (demo = `--unk`, live = `--good`, snapshot = this) (`:156-158, 1049, 2035`).
- **On-colour** (`--on-color`, `#fff` both themes): ink on a filled status colour (`:570, 1082, 1114`); `/yield/station`'s RAW badge declares it but the v2 override turns that badge into an outline (`:1924, 1993`).
- **Low-confidence stripe** (`--low-stripe`, batch 2): the 45° stripe over a low-confidence segment of the `/utilization` strip (`:1579`); the "don't know" family, not the uptime stripe (ruling C-1). Light `rgba(0,0,0,.16)`; dark `rgba(255,255,255,.22)` in both v2 dark token blocks (R38 2026-09-28: black .16 vanished on dark `--rw-off`). Segment colours are inline `background-color:` (never the `background:` shorthand, which erased the stripe until R38).

### Print layer (`--print-paper/--print-ink/--print-rule`, `board.css:44`)
The `/monthly` print sheet is always light: white paper, black ink, `#bbb` rules on every card and brick (`:1447-1456`). Paper never follows the OS theme.

### Landing layer (`board.css:49-53`)
- **Plan ink** (`--plan-ink`): floor-plan wall strokes and the dashed print frame.
- **Zone tints** (`--z-za/--z-zb/--z-zc` → `--ld-zone-*`) at `--ld-zone-fill` opacity (.10 light / .18 dark): zone regions and zone labels; not part of the state family.
- **Bucket colours `--ld-run/--ld-down/--ld-idle/--ld-wait/--ld-dorm`** are printed by the server (`LD_BUCKET_COLOR`) onto the page, never declared in CSS; `--ld-down` is consumed by `.envtag.t-over` (`:303-305`).
- **Seat zone** (`--seat-zone`, per machine): the hollow-seat fill is `color-mix(in srgb, var(--seat-zone) calc(var(--ld-zone-fill)*100%), var(--panel))` — the zone ground's opaque equivalent, never white, never transparent (`:332`).

### Page-local palettes (`[data-page]`, batch 1 `board.css:526, 762, 1032, 1144`; batch 2 `:1355, 1458, 1534, 1635, 1710, 1848`)
Each ported page re-declares the neutral and verdict tokens on `:root[data-page=…]` (light `--good/--bad` consolidated to the deep pair) and adds its own:
- **daily** flow/loss colours (`--b-prod --b-wo --b-starved --b-blocked --b-unexp --p-scrap --p-rework --p-slow`; `--bar-neutral` re-declared with the root value); **equipment** ink-on-status tokens (`--sti-*`); **station** `--cap-q` (quality axis / defect bar; same value as `--st-vendor`, different meaning).
- **monthly** — **Three loss classes** `--l-down` (red) / `--l-yield` (amber) / `--l-rework` (blue): the three PH-denominated losses; painted on a brick's 3px left edge, the daily strip and the drill legend, each always beside its text label (`:1386-1388`). They never share a column with the six-state composition bar, whose colours come from the server (`state_color`). **Neutral accent** `--acc-n`: the part-month badge frame and the four OEE small multiples' line and dots (`:1368, 1438-1440`) — four cuts of one quantity share one accent; its v1 uses on the selected month and the station link are ink in v2 (`:2004, 2010`). **Absence texture** `--gapfg`: no-data stripe on a composition cell (`.st.nul`), unmeasurable-day short block (`b.u`), missing-day dotted frame (`em`), zero-loss baseline (`b.z`) and the dotted frame of the partial-coverage chip — four states, four marks, never merged, because None ≠ 0 ≠ missing (`:1412-1423`).
- **availability** — `--av-up`: the muted green wash of the in-window uptime band under the strip (`:1490`); a ground, not a verdict.
- **utilization** — no new hue; the strip reuses `--rw-off` and the root `--low-stripe`.
- **downtime monthly** — `--dt-za/--dt-zb/--dt-zc`: the three area fills of the stacked chart and its legend swatches, a slate ramp (`#1e293b/#64748b/#cbd5e1`) that deliberately does not reuse the landing zone hues (`:1673-1684`); `--dti-*` is the ink on each fill, bit-for-bit from v1 (R20). `--acc-n/--gapfg` re-declared with monthly's values.
- **pm** — `--pm-age1..4` backlog age buckets (grey → amber → orange → red; `--pm-age4` is the pre-consolidation red `#c4504f`), shown only as an 8px lamp in the overdue-days cell (`:2045-2052`); `--pm-seg1..3` owner-bar segments done / overdue / pending (`:1800-1802`; `--pm-seg3` dark is the 8-digit `#33456180`); `--pm-area-za/zb/zc/zd` area-chip frame colours, label in ink (`:1765-1769, 2053-2055`); `--pm-bar/--pm-bar2` trend bars and their legend swatches (`:1820-1825`); `--pm-line1/--pm-line2` are declared (`:1710`) but referenced by no rule.
- **yield** — attainment bars use the root neutral `--bar-neutral`; verdicts are carried by `.vdot` / `.vsym` with the root verdict tokens (C-17).

Batch 3 (`board.css:2246, 2482, 2600, 2744, 2855`; `/machine` and `/dispatch` declare nothing, `:2224, 2380`):
- **health** — `--mh-wait` (`#9ca3af`, no dark variant): the 「等動工」 stage segment of the WO stage band. Same value as `--st-legacy`, its own page-local name so the two meanings never share a token (R28). Everything else on the page is the root verdict layer: `--unk` frames the caveat box and source banner, `--warn` frames the coverage chips and small-sample mark, `--bad` at .14 opacity is the Pareto share wash (`:2335`), `--tex-stripe` is the uptime stripe (the one stripe on this `/equipment`-family page, C-1).
- **manpower** — `--acc-n` / `--acc-o` blue / teal: the two side-by-side bars (normal / overtime) and their legend swatches (`:2511, 2547-2548`); category, not verdict. `--mp-strip`: the day-by-day strip block, a 55% ink wash resolved per theme (`:2559`). `--hatch`: the silent-day short hatch (`:2561`; "not 0, never a big value", C-MP8). The bundle bands use the root `--band0..3` tint + `--band-edge0..3` 4px edge (`:2529-2533`): the one place a grouping band keeps its fill (R31). All four translucent literals are whole tokens on `:root[data-page]`, bit-for-bit from v1 (R32).
- **cost** — `--b-one` blue: the single bar colour of the in-place monthly columns (`:2686`) and the `annotation` badge frame; `--b-part` green: the `cmms` badge frame; `--b-unk` grey: the `unclassified` badge frame (`:2666-2673`). `--acc-n` / `--gapfg` re-declared with monthly's values: `whitelist` badge frame, the dotted `.alloc` rule, the `est` / `awt` forecast frames, the out-of-scope row's dashed rule and the uncovered-month stripe (`:2663, 2688, 2691, 2710-2712`). Money is never colour-scaled (SPEC-RUN-COST rule 29): figures are ink or `--mut`, the only quantity channel is bar height.
- **budget** — `--acc-a` green / `--acc-p` amber: approved / pending workflow categories on the two-segment track and chart 1 (`:2785-2786, 2830`), each always beside a word; `--acc-n`: the total card's 3px edge and chart 1's second series (`:2776, 2831`); `--tk-overt`: the over-budget track tint, a whole token whose dark value is the light literal (R31, zero visible change, `:2748`). Petty-cash bars keep `--good` / `--bad` (a bar is a data mark, R25, `:2833-2834`); the negative Delta figure is ink with a 7px `--bad` lamp (R25, `:2823-2824`).
- **rework** — `--rw-seq` (`rgb(176,108,37)` light / `rgb(230,170,90)` dark): the one hue of the rework-rate px bar (`:2873`); magnitude only, no colour-to-verdict contract. The v1 RGB-triple + `rgb(var())` form becomes one whole colour token (R32).

### Open conflicts (SPEC-UI-V2 appendix C)
C-2 zone blue = PM blue (and `--acc-n` = `--l-rework` = PM blue on `/monthly`); C-7 landing state-table drift; C-13 `--cap-q` = `--st-vendor` value.

### Ruled 2026-09-28
C-1 the "stripe is uptime only" rule is scoped to the `/equipment` family and `/equipment/downtime/monthly`; on every other page a 45° stripe means the "don't know" family (no data / not measurable / low confidence) and no page carries two stripe meanings in one legend (`/utilization`'s low-confidence stripe stays unlisted in its legend: R38 ruled (c), neither edition adds the item, the v1 behaviour question goes to Chris via ROADMAP). C-3 `/monthly` keeps its values: same value with two meanings is tolerated across columns, never within one. C-4 Δ colouring stays banned on `/monthly` and `/equipment/downtime/monthly`; `/equipment` Δpp and `/output` distance-to-target keep their verdict on the symbol or dot, never on the digits (R10/R11). C-5 light `--good/--bad` is consolidated to `#1e7d4f/#b3423a`; dark is unchanged. C-6 production green `#16a34a` remains distinct from verdict `--good`. C-14 downtime red `#dc2626` and dispatch `--bad` remain distinct, with labelled lamps. C-15 station values remain ink with adjacent colour dots in both v2 editions; v1 is untouched. C-16 keeps `--warn/--unk #b07d12`, moves landing `.srcbanner b` to ink (`:232`), and leaves other small-text uses to the roadmap. C-17 (ruled; SPEC-UI-V2 appendix C-17 = R11 + R22) keeps `/output` distance-to-target verdicts on the ▲▼ symbol, and changes the attainment bar to the single neutral `--bar-neutral` fill; the same-row gap number plus the bar title supplies rule-25 redundancy. Open at HEAD: C-2, C-7, C-13 only.

### Named Rules
**The Colourless Chrome Rule.** Nav, headings, rules, buttons, tabs and toggles carry no hue in either edition; the active state is ink. Amber means attention and red means warning on *state* only, never on emphasis or decoration.
**The Lamp-Not-Text Rule.** Status colour lives on a 10px square lamp, an 8px verdict dot (`.vdot`), a 7px caution lamp (`::before`), a verdict symbol (`.vsym`), a bar or a chip beside `--fg` text; text itself is never painted in a data colour (station axes: `--ax-c` on a `::before` dot, value in ink, `board.css:1326-1335`; batch 2 `:1952-1958, 2014-2052`).
**The Edge-Not-Fill Rule.** A category or caution colour on a card lives on its 3px (loss brick, degraded card) or 4px (availability fallback) left edge; the card face stays `--panel`, and in 2.0 the edge is all that remains of the card (`:1380, 1385-1388, 1480, 1655, 2099-2100`). Scope: cards and callout boxes (batch 3: the `/machine` no-station card, the `/equipment/health` caveat box, the `/dispatch` needs-help row, the `/manpower` caliber line, the `/cost` and `/budget` degraded cards, the `/budget` total / over-budget cards and warning box, `:2237, 2350, 2454, 2492, 2703, 2775-2777, 2840, 2845`). Data-bar tracks and grouping bands (e.g. `/manpower` bundle bands, the `/budget` over-budget track tint) are data marks and keep their fill (R31).
**The Identity-Not-Verdict Rule.** A colour that only tells things apart (which bundle, which category) is never drawn from the verdict layer. Bundle identity on `/dispatch` and the landing dispatch pane is the neutral `--band-id0..3` ramp (R30, `:60, 269-273, 2415-2418`), so a red band can never sit beside a green "can lend" lamp and read as a verdict; `/manpower` keeps its own shipped colour bands because that page carries no verdict lamp at all (R30 amendment). Category colours (`/cost` `--b-*`, `/budget` `--acc-*`) always appear beside a word.
**The Hollow Rule.** No data = hollow outline in `--mut`; never red, never zero. Not-measurable = hollow + 3.6/2.6 dash; inferred production = fill + the same 3.6/2.6 dash; size-estimated footprint = its own 2.4/1.8 dash (`:315-319`). On `/monthly` the four absence marks (`--gapfg` stripe, short block, dotted frame, baseline) are the same doctrine in table form.
**The Server-Colour Rule.** Machine bucket colours, dispatch state colours and the six-state composition colours arrive from the server as inline values or `--ld-*`; templates never restate them as hex.

## Typography

**Body Font:** Public Sans 400 / 600, self-hosted woff2 (latin + latin-ext subsets, OFL-1.1, `static/v2/fonts/`), `font-display: swap`; preloaded in `_base.html`.
**Fallback:** `"Noto Sans TC", "Microsoft JhengHei", system-ui, sans-serif` — the CJK fallback is part of the stack because the same templates render `zh-Hant` (`board.css:57`).
**Mono:** none in the shell; equipment's diagnose code uses `"Consolas","SFMono-Regular",monospace` (`:983`) and station's class subtitle `ui-monospace` (`:1054`), both inherited from v1.

**Character:** a plain, wide-aperture working sans with two weights only; hierarchy is made by size and by the 400/600 step, never by a third weight, never by uppercase or tracking. Numerals are tabular on `body` (`:104`), so columns of figures align without extra classes.

### Hierarchy (fixed pixel steps × density factor; `board.css:58-60`)
- **Figure** (600, `--fs-num` 30px × `--kn`, line-height 1): the 2.0 figure-strip value (`:391`), station axis values (34px, `:1075`).
- **Figure-2** (600, `--fs-num2` 22px × `--kn`, 1.15): KPI value in 1.5 and panel head count (`:173, 202`).
- **Ported hero figures** keep their v1 pixel sizes (R21; SPEC §6 records them as px × `--k`): `/equipment/pm` hero 54px (`:1743`), `/output` anchor 42px (`:1865`), `/monthly` brick and `/utilization` headline 26px (`:1390`, `index.html:76`), all 600 tabular, line-height 1–1.2.
- **Title** (600, `--fs-title` 15px × `--k`, 1.25): panel and side titles, section headings on ported pages (`:172, 1975-1976`); ported page `h1` = 22px × `--k`, 600 (`:1178, 1973-1974`).
- **Body** (400, `--fs-body` 14px × `--k`, 1.45): default text.
- **Small** (400/600, `--fs-sm` 12.5px × `--k`): nav tabs, alert rows, side rows, legend items, toggle chips.
- **Note** (400, `--fs-note` 12px × `--k`): tables (`:188`), KPI labels, event feed, footer disclosures.
- **Label** (600, `--fs-xs` 11px × `--k`): table heads, lamps, chips, badges, sub-labels, demoted prose, the `/yield/station` month heading (`:1984`).
- **Density:** `[data-density="projector"]` sets `--k:1.25; --kr:1.2; --kn:1.4` (`:97`). The nav pins its own sizes so it never grows (`:122`). Ported pages keep v1 px sizes, so projector applies `zoom:1.25` to `.board-main` and neutralises the variables to avoid double scaling (`:1270-1273, 2119-2125`).

### Named Rules
**The Two-Weight Rule.** Only 400 and 600 exist as faces; anything declaring 700/800 (ported v1 rules) renders at 600. Do not introduce a third weight.
**The No-Kicker Rule.** No uppercase, no letter-spacing on labels or headings; the discipline blocks strip both from every ported kicker, table head and badge (`board.css:1178-1185, 1972-1984`). The only tracked text is the brand mark `TENDLINE BOARD` (.08em, `:126`) and the landing source banner's bold (.04em, `:232`).
**The Demote-Don't-Delete Rule.** Methodology, disclaimers and caveats move to `--fs-xs --mut` at the bottom of a block or into a `title` tooltip; the words are never removed (`:1247-1249, 2113-2116`). Exempt by ruling: the `/manpower` five caliber sentences (`.foot`, R42 / C-PLANT-1), the `/cost` caliber line and notes lists (SPEC-RUN-COST §8.3) stay at table-body size in both editions (`:2565, 2609, 2716-2718`).

## Layout

- **Shell:** `.board-nav` (48px min height, `--panel` face, 1px `--line` bottom rule, sticky-free, z 50) above `.board-main` (max-width 1560px, padding `12px 20px 40px`; ported pages' inner `main` and `/output`'s product switch widened to 1560 too, `:118, 123, 1968-1970`).
- **Grid rhythm:** 4px row padding (`--row-pad`), 6/8/12/16 spacing steps; pane grid gap 12px (1.5) or `0 24px` (2.0); map/side gap `--gap` 16px.
- **Landing 1.5:** four panes in a 4-column grid on top, then map column + fixed side column (`--side-w` 344px); line-level figures as a quiet card under the map.
- **Landing 2.0:** map + alert stack first (side 400px; 460px in projector), then the figure strip under the map, then the four panes as an instrument strip. The stack column is exactly as tall as the map and scrolls on its own, so strip and panes stay in the first viewport (`:397-406`); a sticky one-line group index announces groups below the clip (`:1278-1287`).
- **Panes:** every pane is `.board-scroll` at `--pane-h` 202px × `--kr`, independent scrollbar, all rows kept; projector collapses four columns to two.
- **Responsive:** ≤1100px the side column stacks under the map and panes go two-up; ≤620px single column, tighter nav padding, zoom hint hidden (`:503-517`). Wall mode (`body.wall`) hides nav, side and footer and enlarges pane type (`:495-500`).
- **Ported pages, batch 1:** keep their v1 grids (`/daily` hero 3-col, `/equipment` sticky-header grid with sticky Now/Avail columns, `/station` 4-axis row → 2 at ≤720px); 2.0 turns card groups into hairline-separated sections without moving them.
- **Ported pages, batch 2:** `/monthly` = header row (title + badges), month segment, tab bar, then one table per tab; the station table `.tw.stk` keeps a sticky first column and sticky head (`:1404`, smoke-pinned by R13). `/availability` and `/utilization` = headline, controls, then one machine row per line: a card per row in 1.5, a single `--line` hairline per row with `--panel2` hover in 2.0 (`:2102-2104`). `/equipment/downtime/monthly` = type chips + range, stacked area chart, KPI row, then the table. `/equipment/pm` = zone bar with window chips right-aligned, 54px hero, then sections in ruling order (backlog before owner load). `/output`+`/yield` = product switch top-right, anchor figure + chips, one station table with attainment bars, `/yield/station` cards under it.
- **Ported pages, batch 3:** `/machine/{eid}` = crumb, 22px title with a neutral seat chip, then the shared timeline card (`.tl`: head row, 30px bar, axis, one legend, stop list). `/equipment/health` = head, back link + two static window chips, then `.mh-sec` sections (58px hero + 24px stage band, slowest-five mini bands, scatter with coverage chips, top-5 / Pareto tables). `/dispatch` = honesty banner, meta chips, bundle-pairs card, then one sticky-head grid (`12px | station | 105 | 120 | 100 | 142 | why | 88`) inside a `min(70vh,720px)` scroller with a `<details>` station layer per bundle (`:2400-2404, 2430-2436, 2446-2449`). `/manpower` = title, caliber line, freshness, one `.seg` window switch, then one table whose rows are banded by bundle and hold side-by-side `≥` bars and a day strip. `/cost*` = title, caliber line, tab bar, head line with static CM/currency labels and a period `.seg`, then tables 1 and 2 side by side (R40), FY + per-shipment equation lines (30px / 21px), forecast A/B, ticket drill as an indented appendix. `/budget` = freshness, three `.seg` rows (period / display / month), cards with a 16px two-segment track, Classic sheet with a sticky first column, pure-CSS bar charts. `/rework` = title + DEMO tag, one card, one table with a px bar column. The landing right column's `.side` family is now landing-prefixed (`:410-429`) so `/equipment/pm` and `/equipment/health` can reuse the class name for their own side text (`:1847, 2273`).
- **Print (`/monthly` only, R17):** nav, tabs, month segment and `.noprint` leave the paper; `.board-main` goes full width with no padding; every card, brick, note and small multiple is `break-inside:avoid`; sticky columns become static; the as-of / frozen line stays (`:1447-1456, 2083-2086`).

## Elevation & Depth

Flat. There are no decorative shadows anywhere in the v2 layer: cards are 1px `--line` borders on `--panel`, floating surfaces (nav drop-down, L1 hover card, equipment how-panel, help tooltip) get a 1px `--line2` hairline edge or a solid ink panel instead of a shadow (`board.css:138, 488-489, 1218, 1735`), and the equipment sticky Now column's v1 shadow becomes a `--line2` left rule. Depth is conveyed by tone (`--bg` → `--panel` → `--panel2` on hover) and by the hairline. The remaining `box-shadow` declarations in the cascade are an inset flash used as a row highlight animation (`:839`) and a 1px zero-blur `--line` offset that draws the `/monthly` sticky column's right rule (`:1404`), neither of them elevation. Semantic *textures* (uptime stripe, low-confidence stripe, absence marks, off-day tint) are kept exactly as ported because they carry meaning. The help tooltip is the one inverted surface: ink panel, ground-coloured text, radius 4 (`:618, 1735, 1889`).

### Named Rules
**The Hairline-Not-Shadow Rule.** Anything that floats gets a 1px `--line2` edge; the only ring in the system is the focus ring (`2px solid var(--focus)`, offset 2px).

## Shapes

- **Radius scale is three steps and a zero:** 2px for static labels (source badge, draft/fresh/weak chips, product tag, DEMO / RAW / part-month / partial / AI / flag badges, area and zone chips, `:1986-1993`), 3px for interactive things (legend filter links, segmented toggles, zoom buttons, drop-down panel, panel arrow button, WO card, month segment, downtime-type chips, window chips, CSV/back buttons, product-switch links, date/number inputs, `:1994-2000`; page tabs `3px 3px 0 0`, `:2001`), 4px for 1.5 cards, bricks, bar troughs and the help tooltip, 0 in 2.0 where sections are hairline-ruled. Circles are reserved for the spinner, the 14px help glyph (`:617, 1337, 1734, 1888, 2067`) and the `/utilization` trend's 8px max/min marks (`:1613`).
- **Static vs interactive:** a static label is a square micro-label (radius ≤2, 10–11px, 600, 1px border in its semantic colour, ink text, no fill, no hover); only interactive things take the button form (radius 3, border, `--panel2` hover, `--line` active, and `aria-pressed` when they toggle).
- **Marks:** lamps and legend swatches are 10px squares; verdict dots (`.vdot`) and PM age/unassigned lamps are 8px squares; caution lamps on prose are 7px squares; none has a radius. Hollow variants are a 1px `--mut` outline, dashed when "not measurable". Floor-plan footprints are rectangles with `vector-effect: non-scaling-stroke`, 0.7px stroke at rest, 1.8 on hover, 2.4 selected/focused, with a separate `--hover` ring for selection.
- **Edges:** a 3px left edge in a loss or caution colour on a `--panel` card (bricks, degraded cards); 4px on the availability fallback card.
- **Icons:** stroked SVG at `.95em`, 1.5px stroke, round caps, `currentColor`, `aria-hidden` (`_c_icon.html`: arrow, back, globe, download, search, wrench, more, calendar). Disclosure triangles are a masked SVG chevron that rotates 90° when open, on the landing WO card, daily details, the batch-2 `<details>` and the batch-3 `/dispatch` station layer and `/budget` `details.dd` (`:454-459, 653, 2073-2080, 2430-2436, 2800-2803`); the `/budget` year `◀▶` became `_c_icon` back / arrow (`8819bd9`). Text symbols that survive are data encodings kept by ruling, not icons: `.vsym` ▲▼ on `/output`, the `/dispatch` lamp glyph ▲▼■●· beside its 10px square and ink word (v1 triple encoding, R29), the `/equipment/health` Δ arrows ↑↓→ whose footer quotes them (R39), and the `≥` lower-bound mark on `/manpower` and `/cost` (`--mut`, 600, `:2643`).

## Components

All shared components are Jinja macros under `templates/v2/_c_*.html`; each renders one markup for both editions, and the edition difference is CSS. Ported pages keep their v1 markup verbatim and take their form from the discipline block.

### Panel (`_c_panel.html` → `P.panel(title, tip='', sub='', go_href='', go_label='', count=None, count_tip='', cls='')`, call block = body)
- Renders `<section class="board-panel"><header class="board-ph"><h2 class="board-pt" title=tip>` + optional `.board-pc` count (600, `--fs-num2`) + optional `.board-psub` period subtitle (`--fs-xs --mut`, ellipsised) + optional `.board-pgo` arrow link (26×24, radius 3).
- **1.5:** `--panel` face, 1px `--line` border, radius 4, header bottom rule. **2.0:** transparent, 1px `--line2` top rule, header padding `10px 2px 8px`, no card.

### Scroll pane (`_c_scroll.html` → `S.scroll(label='', cls='')`)
- `<div class="board-scroll pnb" tabindex="0" role="region" aria-label>`; height `--pane-h`, `overflow:auto`, `overscroll-behavior:contain`, focus ring inset. Always the full row set.

### Table (`_c_table.html` → `T.table(cols, cls='')`, `cols = [{label, cls?, tip?}]`)
- `<table class="board-t pnt2">`, `--fs-note`; sticky `thead th` (`--mut`, 600, `--fs-xs`, `--panel` face; `--bg` in 2.0), cells `--row-pad 8px` with `--line` rules, last row unruled, row hover `--panel2`; `.n` = right-aligned tabular numerals; in-cell links underlined in `--line2`. Empty `tip` emits no `title`. Ported tables (`/monthly`, `/equipment/downtime/monthly`, `/equipment/pm`, `/output`) keep their own classes but take the same 2.0 head-on-`--bg` treatment (`:2107-2112`).

### KPI (`_c_kpi.html` → `K.kpi(label, value, unit='', tip='', delta=None, delta_tip='', sub='', cls='')`)
- Label `--fs-note --mut`; value 600 `--fs-num2` in ink; unit and Δ in `--fs-note --mut` (Δ printed only when a real Δ exists, always neutral); optional sub-line `--fs-xs`. In `.ld-line`: value is `--fs-title` in 1.5 and `--fs-num` in 2.0.

### Alerts (`_c_alerts.html` → `AL.group(title, count=None, tip='', cls='', gid='')` + `AL.lamp(text, color='', tok='')`)
- Group = `<section class="board-ag">` with `h3.board-ag-h` (`--fs-note`, 600, `--mut`, count in ink) and `ul.board-al`; rows `.board-ai` are flex, `--fs-sm`, `--line` rules, `calc(5px*var(--kr))` vertical padding.
- Lamp = `<span class="board-lamp">` (`--fs-xs`, 600, ink text) + 10px `.board-lsw` square: `color` → server inline background; `tok` → `t-bad|t-need` (`--bad`), `t-lend` (`--good`), `t-down` (`--mut`); neither → `.hollow` outline. Colour is never the only channel; the text is always present.
- Edition: the same markup is the right-column stack in 2.0 and the side lists in 1.5; severity order is set by the page template (down-longest → PM overdue → dispatch needs help → should-run idle).

### Verdict dot and verdict symbol (R10 / R11, rule 26; `board.css:1952-1965`)
- **Dot:** `<span class="vdot" style="background:var(--good|--warn|--bad|--unk)" aria-hidden="true"></span>` — an 8px square, 6px right margin (4px inside a 56px row-end column), placed before the label or figure it judges; the template picks the token by threshold and the figure beside it is `--fg`. Used on `/utilization` A/U chips, row-end util %, the "should run, didn't" headline; `/availability` headline and row-end %; `/output` overall attainment, fail count and yield; `/yield/station` headings and fail % (`index.html:76-234`, `availability.html:79,167`, `yield.html:75,170-171`, `_yield_station.html:55,92`).
- **Symbol:** `<span class="vsym" aria-hidden="true">▲|▼</span>` inside a `.pos` / `.neg` figure: the symbol takes `--good` / `--bad` at .8em, the digits stay ink (`yield.html:61,139`). Δ on `/monthly` and `/equipment/downtime/monthly` carries neither.
- Sibling: the `/equipment` A% < 90 warning is the same idea at 8px before the value (`:1345-1346`), and the station axis dot is 9px (`:1331`).

### Caution lamp (`::before`, 7px; `board.css:2025-2044`)
- Prose that v1 painted amber or red keeps its words in ink and gains a 7px square before the first word: `--warn` on run-window caveats (`.note.off`, `.band-cap .cov`, `.au-erwline.inert`, `.rng .capn`, `.mixed`, `.ys-warn`), `--bad` on the error card `.err` (`--panel` card, 1px `--line`, radius 4, `12px 16px`), `--unk` on "not available" prose (`.pm-off`, `.pm-unavail`). Unassigned PM owner = ink name 600 + 8px `--unk` lamp (a process hole, never a verdict colour); PM backlog age = 8px lamp in `--pm-age1..4` in the overdue-days cell only (`:2045-2052`).

### Help (`_c_help.html` → `HP.help(text, r=False)`)
- Emits `<span class="help" tabindex="0">?<span class="tip">…</span></span>`, bit-for-bit the v1 hand-written markup (`r=True` → `.tip.r` right-aligned). Since R36 there is **one shared rule** (`:2107-2116`): the text node is hidden (`font-size:0`) and the system glyph is a drawn **`i`** (`::before`, 10px, 600) in a 14px circle with a 1px `--mut` border, `cursor:help`, ink on hover / focus-visible; the `?` stays in markup only for smoke parity. Tip = ink panel with ground text, 11.5px/1.55, `8px 11px`, radius 4, 300px wide, shown on hover and on keyboard focus (R21). Page blocks add only spacing (`margin-left`, `:633, 2257, 2428`); `/equipment/health` suppresses the `i` on its two special forms, the `—` form (`.help.mh-dash`, 10px, `--line` border) and the inline one-click form (`.help.mh-oc`, borderless `--mut` text), because both carry counts and one-click literals (`:2258-2260`, R36).

### Legend swatch (`_c_legend.html` → `LG.swatch(color='', dash=False, ldf='')`)
- `.qdot` (10px, inline server colour) or `.qhollow[.dash]` (1px `--mut` outline, dashed = not measurable). Wrapped by `.lgi` filter links (button form, radius 3, `--panel` face, `.on` = ink border + 600). One legend per page; `/availability` keeps both its `.avleg` legend and its `.legend` duration list because the latter is a readout, not a legend (R18).

### Tooltip helper (`_c_tip.html` → `tip(text)`) and icons (`_c_icon.html`)
- `tip` emits ` title="…"` only when non-empty; every ellipsised cell carries the full text this way, including the PM owner name `.who` (R15) and every bar (below). Icons are the eight stroked glyphs above; there are no emoji or glyph-font icons in the v2 shell.

### Navigation (`_nav.html`)
- `.board-nav`: brand mark (600, `--fs-sm`, .08em, ink), three tab groups with a `--fs-xs --mut` group label, tabs `--fs-sm --mut` with a 2px transparent bottom border → `--line2` on hover → ink + 600 when `.on`; `<details class="board-more">` drop-downs (panel `--panel`, `--line2` border, radius 3, left-rule active state). `board.js` closes sibling menus, closes on outside click/Escape, and sets `data-motion="reduce"`.
- Right cluster: `.board-src` source badge (square micro-label; demo → ink text with `--unk` border, live → `--good`, snapshot → `--src-snapshot`; **synth** → same form as demo, ink text with `--unk` border, no new token — printed only by the synthetic-line process, text = i18n `source_label_synth` (en `synthetic line` / zh `合成產線`) instead of the raw source value; WP-4b R-A3, 2026-09-30), language link, one `.board-seg` two-state switch worded by scene, `Laptop | Projector` (zh `筆電 | 投影`; `?ui=15&density=laptop` / `?ui=2&density=projector`; group `title`/`aria-label` = `ui_density_tip`); selected segment = ink fill, `--panel` text, `aria-current="true"`. No numbers (1.0 / 1.5 / 2.0) appear on screen; v1 is not on the nav (URL escape hatch `?ui=v1` only, never written to the `board_ui` cookie) and a v1 page shows the same switch with neither segment lit (KEEP-BATCH3 §H-3, 2026-09-28 evening).

### Page tabs and toggle chips (ported; R14)
- **Page tabs** (`/monthly` `.tabs a`, `/cost` `.tabs > a`): `--mut` 13px, `7px 16px`, radius `3px 3px 0 0`; 1.5 `.on` = `--panel` face with `--line` sides joined to the table; 2.0 `.on` = transparent with a 2px ink underline (`:1374-1377, 2001, 2108`; cost `:2612-2617, 2728`). Plain links, never `hx-get`. The `/cost` ⓘ notes entry is the same tab, smaller and pushed right (`.tabs a.info`, `:2617`).
- **Navigation segments** (`.seg a` on `/manpower`, `/cost`, `/budget`, and the `/monthly` month segment; `:2503-2508, 2622-2626, 2764-2770`): links that change the URL (`hx-push-url`), drawn as one joined button row (1px `--line`, radius 3, `--panel` faces, `--panel2` hover, `--line` active); the current one = ink fill + `--panel` text + 600 and **`aria-current="true"`** in markup (R27; class literal `on` kept for the smoke). `/budget`'s year view greys its month segment to `span.off` at .45 opacity, `cursor:not-allowed` (`:2772`).
- **Toggle chips** (state that does not change the URL): `/equipment/downtime/monthly` `.chips a` and `/equipment/pm` `.pm-wchip` carry `aria-pressed="true|false"` (`downtime.html:21`, `_equipment_pm_body.html:274`); button form (radius 3, 1px `--line`, `--panel` face, `--fs-sm`), hover `--panel2` + ink, active `--line`. Selected = ink fill + `--panel` text + 600 (downtime, also the `/monthly` month segment `.seg a.on`), or `--panel2` fill + ink border + 600 (pm window chips, `:2004-2007`). `/output`'s product switch uses the same form on `a.toggle` and a static `b.toggle` for the current product (`:1917, 2000`).

### Outline badges (honesty markers, static)
- DEMO (`.demotag`, `.tag.demo`, `.au .demotag`), RAW (`.ys-raw`), part-month (`.parttag`), partial / clipped coverage (`.cov`, `.cov.zero`), AI-written (`.ai`), data flags (`.flag`), snapshot (`.badge.snapshot`): 10–11px, 600, no tracking, radius 2, `1px 6-8px`, text in ink, 1px border in the marker's colour — `--unk` for demo / RAW / clipped, `--acc-n` for part-month and AI, dotted `--gapfg` for partial coverage, `--src-snapshot` for snapshot (`:1367-1368, 1413-1414, 1695-1696, 1982-1993, 2015-2021, 2035`). Never filled: `.ys-raw`'s v1 solid amber is an outline in v2 (`:1993`). A DEMO target column on `/output` appears in live too; it is a label, not a badge of the page (R22).

### Loss brick and caution cards (`/monthly`, `/equipment/downtime/monthly`, `/availability`)
- **Brick** `.brick.down|.yield|.rework`: flex card (min 210px), `--panel`, 1px `--line`, radius 4, `10px 14px`, 3px left edge in `--l-down/--l-yield/--l-rework`; label `--mut` 12px, value 26px 600 tabular with `small` unit 13px `--mut`, sub-line 11.5px `--mut`; no Δ; the yield and rework bricks carry their "no line-level rate" `title` (`:1384-1392`). In 2.0 the face, top/right/bottom borders and radius go and only the left edge remains (`:2099-2100`).
- **Degraded card** `.deg`: same card with a 3px `--warn` edge, 13px, `14px 16px` (`:1380, 1655`); **availability fallback** `.fallback`: 4px `--warn` edge, text ink (`:1480, 2018`). **Error card** `.err`: card + 7px `--bad` lamp (`:2030-2034`).

### PM owner bar (`/equipment/pm`, SPEC-PM-BOARD guard rails)
- `.pm-ownrow` = grid `170px 1fr 200px`, 12.5px: `.who` 600 ellipsised with a full `title` (R15); `.pm-bar` = 20px trough, `--track` face, 1px `--line`, radius 4, `overflow:hidden`, segments `.s1/.s2/.s3` in `--pm-seg1..3` (done / overdue / pending) with 10.5px tabular labels in `--bg` (the light third segment's label in ink, `:2057`), and the whole bar carries a `title` with the three figures; `.side` totals `--mut` 11.5px with ink numbers (`:1795-1804`). A team-total row precedes the owners; unassigned rows take the ink-name + `--unk` lamp form. In 2.0 the total row and cells lose their card (`:2094`).

### Single-machine timeline (`_timeline.html`, shared, R34; `board.css:2177-2222`)
- `.tl` card: `--panel`, 1px `--line`, radius 4, `18px 20px`; head row with the EID and utilisation both 17px 600 tabular (the reading is ink, a `.vdot` beside it carries the verdict; "—" stays `--mut` with no dot, R39), a `--mut` 12px meta line whose coverage note is ink + 7px `--warn` lamp.
- **Bar** `.tl-bar`: 30px, `--track` trough, 1px `--line`, radius 4, `overflow:hidden`; each `.seg` fills its server-given width and colour and ends in a 1px `--tl-seg-edge` hairline; `.seg.low` overlays the root `--low-stripe` 45° stripe ("don't know", C-1). The run-window re-axis (`.tl-bar.rw`) uses `--rw-off` / `--rw-on` grounds, a dashed `--mut` tick, and `.seg.off` at .28 opacity.
- **Legend** `.tl-legend`: one legend, 10px square swatches; the uncovered swatch is a `--line` 1px hatch on `--panel` (`:2207-2209`). Since R38 (2026-09-28) it ends with two items: `legend_low` (swatch = should-run amber + `--low-stripe`; a green 10px swatch hides the stripe) and `legend_off` (swatch = production green at .28 opacity); each carries its `_tip`. The old-axis fallback legend drops `LEGACY_STATES` (R39-2).
- **Utilization strip legend** `rwleg` (`/utilization`, v1 + v2): same two trailing items as `.tl-legend` (`legend_low`, `legend_off`), so the drill legend still equals the page legend item for item (smoke `leg(v1)==leg(v2)`).
- **Stop list** `.stops` / `.stop`: 12.5px flex rows on `--track`, 1px `--line`, radius 4, 5px apart; a 9px square `.dot` in the server's state colour; the duration `.dur` is ink with no lamp (the v1 amber had no semantic source, R24); `.future` is `--mut` 11.5px. In 2.0 the card becomes a `--line2` top rule and the stops become `--line` hairline rows (`:2221-2222`).

### Health caveat box, coverage chips and small-sample mark (`/equipment/health`, `board.css:2302, 2325, 2350`)
- **Caveat** `.mh-caveat`: one class for stale-WO and truncated-window notes; a box (`--panel`, 1px `--line`, 3px `--unk` left edge, radius 4, `8px 13px`, 12px ink). In 2.0 only the edge remains (`:2367`). The `◦` bullet in the stale note is v1 text (R24).
- **Coverage chip** `.mh-covchip[.warn]`: outline badge, 11px ink, `2px 9px`, radius 2, `--line` border → `--warn` when it flags skipped or unavailable coverage. **Small-sample** `.mh-ss`: 9px 600 `n<5`, `--warn` 1px frame, ink, `cursor:help` with a `title`.
- Δ self-trend `.mh-dlt` (`:2329-2332`): the arrow glyph ↑↓→ carries `--good` / `--bad` / `--mut` (R11 on a symbol; R39 keeps the glyph). Pareto share `.mh-barc > i` (`:2335`): `--bad` at .14 opacity behind an ink figure (a bar, not a fill behind prose).

### Dispatch lamp grammar and bundle band (`/dispatch`, R29 / R30; `board.css:2415-2426, 2468-2476`)
- **Two-level lamp**, one grammar: a 10px square `::before` + an ink 600 word (`--fs-xs` on the bundle `.kd-lamp`, 11.5px on the station `.lamp`, which also keeps its 11px glyph `.g`). Bundle: `need` = `--bad`, `lend` = `--good`, `down` (no work) = `--mut` (same token as the landing pane's `t-down`), `na` = hollow 1px dashed `--mut`, `ok` = no square drawn, the 10px slot kept so words align. Station: `need` / `lend` as above, `down` (whole station on hold = repair) = `--st-repair`, pass-through `none` = hollow dashed, `ok` = blank slot. The v1 `--lamp-*` text-contrast tokens retire in v2 because the words are ink.
- **Bundle band** `.kd-band`: a 4px left border stretched to the row (`margin:-7px 0`), coloured `--band-id0..3` by the bundle's `.b0..b3` row class; the landing pane's `.dspband` is the same ramp at 3px × .85em. **Needs-help row** `.dsp-r.need`: 3px `--bad` left edge, no fill (R31, §24: the manpower cell never sits on red). Honesty banner `.dsp-banner`, over-age / FAIL-held `.chip` and `kd-chip.warn`: outline badges, `--unk` / `--warn` frame, ink text, no fill.

### Manpower bundle band, `≥` bars and hatch (`/manpower`, SPEC-MANPOWER §7; `board.css:2529-2565`)
- **Band**: rows `tr.b0..b3` take `--band0..3` as a row tint plus a 4px `--band-edge0..3` left edge on the index cell; the one grouping band that keeps its fill (R31). Group breaks are a 2px `--line` top rule (`tr.gs`).
- **Bars** `.bar .tk`: 9px `--track` trough, radius 3; fill `<i>` in `--acc-n` (normal) or `--acc-o` (overtime), side by side never stacked (C-MP7); a 9px mask fade at the right end = lower bound "at least" (C-MP11), and the value cell prints `≥` before the figure (11.5px tabular; `.z` zero = `--mut`).
- **Day strip** `.strip`: 34px, 2px gaps, blocks in `--mp-strip` with a 2px top radius; a silent day is a short `--hatch` 45° hatch (C-MP8). "No measurable signal" `.nosig b` = ink + 7px `--unk` lamp (R24). The caliber line `.cal` has a neutral 3px `--line` edge (R31); the `.foot` five sentences stay full size (R42).

### Cost badge family, `gapfill` stripe and forecast marks (`/cost*`, R26 / R32; `board.css:2666-2712`)
- **`.bdg`** outline badge: 11px ink, `1px 7px`, radius 2, 1px frame in the source or caution colour: `annotation` `--b-one`, `whitelist` `--acc-n`, `unclassified` `--b-unk`, `cmms` `--b-part`, `unpriced` / `unalloc` `--warn`, `retired` `--mut`, default `--line`. **`.bdg.fact`** ("Chris confirmed") is the one filled member: ink fill, `--panel` text, 600, the strongest source mark on the page and the same syntax as a selected segment.
- **Monthly columns** `.chart .col b`: one colour `--b-one`, 2px top radius, `min-height:2px` (never a zero-length bar); an uncovered month with a value takes `b.gapfill`, a `--b-one` / `--gapfg` 3px/2px stripe that was inline in v1 and is now a class (R32; `style=` keeps only `height`); an absent month is a dashed `--gapfg` 26px slot, `em.uncov` striped. Hover = 2px `--hover` outline. Top-of-bar value `--mut` 10px (R42).
- **Forecast marks** `.est` (dashed `--gapfg`) vs `.awt` (dotted `--gapfg`): 10.5px 600 `--mut`, radius 2; line style is the only difference and actuals carry no mark. Structure rows are grey grounds (`tr.sub`, `tr.grp` on `--panel2`; `tr.out` dashed `--gapfg`; `tr.tot` 2px ink top rule), never a colour, because money is not colour-scaled.

### Budget two-segment track and segment rows (`/budget`, R25 / R27 / R31; `board.css:2764-2790, 2823-2836`)
- **Track** `.tk`: 16px, `--track`, radius 4, `overflow:hidden`, flex of `<i>` segments `.a` (`--acc-a`, approved) and `.p` (`--acc-p`, pending); `.tk.overt` swaps the trough for the `--tk-overt` red tint when the total is over budget. No text inside the track (§G-4); the figures sit below in `.nums` (`--mut` 12.5px tabular, ink `<b>`), and the OVER BUDGET `.flag` is ink 600 + 7px `--bad` lamp (R24).
- **Segment rows** `.seg`: three `bar-row`s (period / display / month); see Navigation segments above for the form and `aria-current`. Cards `.card` (radius 4, `12px 15px`) with `.tot` 3px `--acc-n` edge and `.over` 3px `--bad` edge keep only the edge in 2.0 (`:2881-2883`). Classic sheet: `table.cls` 12px, sticky first column with a `--line2` right rule, never a shadow (§G-12); `td.neg` = ink figure + 7px `--bad` lamp before the minus sign (R25).
- **Charts**: pure-CSS columns (`.col b.s0` `--acc-a`, `.s1` `--acc-n`; petty cash `.pc` `--good`, `.pc.ov` `--bad`); the cap line is a 2px `--bad` rule, the second cap 2px dashed `--warn` (`:2835-2836`).

### Rework rate bar (`/rework`, R42; `board.css:2873`)
- `.bar`: inline 8px block, radius 4, `--rw-seq` fill, width set in markup as absolute px (1% = 1px, capped at 100px) followed by the figure in ink; no attempt prints "—" in `--mut` (R39: `0` is a real value and stays). Page DEMO tag `.demotag`: outline badge, 10px 600, `--unk` frame, ink (R33 keeps it beside the nav source badge).

### Attainment bar (`/output`, R22)
- `.obar` (14px, min 130px) and `.ybar` (12px, min 110px): `--track` trough, radius 4, inner `<i>` with server width % and the single neutral `--bar-neutral` fill; `display:block` in v2 so the bar is actually drawn (the v1 inline span rendered zero-size, `:1892-1895, 1959-1963`). The cell prints no number; the same-row distance-to-target column supplies the ink number and coloured ▲▼ verdict symbol, and the bar's `title` carries the percentage (`yield.html:135`). C-17 records this rule.

### Floor plan (signature, landing)
- `.stage` (`--panel`, 1px `--line`, radius 4 in 1.5 only) with zoom buttons (radius 3) and a `--fs-xs` hint; zone regions filled at `--ld-zone-fill`; machine bodies filled by server bucket colour; hollow bodies take pointer on the whole face; seat cells hollow → zone-equivalent opaque colour; `.dim` at .16 opacity for filtered-out machines; `.mach.pulse` = the only motion (2.5s, 1.2s at ≥24h; reduced motion → static `.rmk` corner mark).

### Buttons / inputs
- No standalone button component. Buttons are the segmented toggle, legend filter, zoom button, `.board-pgo`, toggle chips, and ported `.eq-btn` / `.ctl` / `.pbtn` / `.pm-back` controls: 1px `--line` or `--line2` border, radius 3, `--panel2` hover, `--line` active, `font:inherit`, `color:inherit`. Inputs exist on `/station`, `/availability` and `/utilization` (`date`, `number`, `select`: `--panel2` face, `--line` border, radius 3) and the `/monthly` OEE station `<select>` with a `<noscript>` submit (R17). Focus everywhere = `2px solid var(--focus)` outline, offset 2px.

## Do's and Don'ts

### Do:
- **Do** define every token once in `board.css :root` (or the page's `:root[data-page]`) and reference it with `var()`; templates contain zero hex (smoke `ui_v2_nohex`).
- **Do** keep both themes complete; theme follows `prefers-color-scheme`, `data-theme` only forces it; print is always the paper tokens.
- **Do** put status colour on a 10px lamp, an 8px `.vdot`, a 7px caution lamp, a `.vsym` symbol, a bar or a 3px card edge beside ink text; keep KPI titles, values and Δ digits in `--fg`.
- **Do** render "no data" as a hollow `--mut` outline, "not measurable" as hollow + 3.6/2.6 dash, "inferred" as fill + 3.6/2.6 dash, "size-estimated" as 2.4/1.8 dash, and print "—" for anything unmeasurable; on tables keep None, 0 and missing as three different marks.
- **Do** give every scroll pane its full row set and its own scrollbar at `--pane-h`.
- **Do** attach a full `title` to any text a CSS `ellipsis` can cut and to any bar that prints no number.
- **Do** use radius 2 for static labels, 3 for interactive controls, 4 for 1.5 cards and bar troughs, 0 for 2.0 sections.
- **Do** give a toggle chip `aria-pressed`, a button form, and an ink selected state; give a segment that changes the URL `aria-current="true"` instead (R27): `aria-pressed` = toggle, `aria-current` = navigation, never both.
- **Do** draw a two-level status lamp as a 10px square before an ink word; leave the 10px slot blank for "ok" and hollow-dashed for "n/a" (R29).
- **Do** colour bundle or category identity from a neutral ramp (`--band-id*`) or a category token beside a word, never from `--good/--warn/--bad` (R30).
- **Do** declare a translucent value as a whole token on `:root[data-page]` (`--hatch`, `--mp-strip`, `--tk-overt`), bit-for-bit, in both themes; never inline `rgba(` in a template or `color-mix()` in place of a token (R32).
- **Do** draw an honesty marker (DEMO, RAW, partial, AI, snapshot) as a square outline badge with ink text and a semantic border, never as a filled chip.
- **Do** demote explanatory prose to `--fs-xs --mut` (or a tooltip) with the words unchanged.
- **Do** scale for projector with `--k/--kr/--kn` (or `zoom:1.25` on `.board-main` for ported pages) and leave the nav at laptop size.
- **Do** draw icons and disclosure chevrons as stroked inline SVG (`.board-ic`, 1.5px, round caps, `currentColor`, `aria-hidden`).

### Don't:
- **Don't** paint chrome (nav, headings, tabs, rules, buttons, links in page chrome) with any hue; the active state is ink.
- **Don't** paint text in `--good/--warn/--bad/--unk` or any state, loss or area colour, and don't colour a Δ's digits.
- **Don't** add a fifth downtime colour, use green for downtime, or let legacy states into a legend.
- **Don't** give a stripe a second meaning inside the `/equipment` family, or list two stripe meanings in one legend anywhere.
- **Don't** use gradients, decorative box-shadows, capsule radii on static labels, emoji/Unicode glyph icons, or uppercase tracked kickers. (Text symbols that encode data and are quoted by the page — `.vsym` ▲▼, the dispatch lamp glyph, the health Δ arrows, `≥` — are not icons; they stay by ruling R29 / R39 and always sit beside an ink word or figure.)
- **Don't** fill a row, card or box with a verdict colour; the verdict goes on a 3px left edge and the face stays `--panel` (R31). Data tracks and grouping bands are the exception because they are marks.
- **Don't** put a figure inside a bar track (`/budget` `.tk`), and don't colour-scale money; the only quantity channel on `/cost*` is bar height in one colour.
- **Don't** hard-code machine bucket, dispatch state or six-state composition colours in CSS or templates; they come from the server.
- **Don't** animate anything except the down pulse; under reduced motion the pulse becomes a static mark.
- **Don't** invent a trend line or sparkline without a real series; print a real Δ or nothing.
- **Don't** add a second legend, a third product-tag form, or a fill behind an honesty badge.
- **Don't** introduce a third font weight, a different sans, or a fluid `clamp()` type step.

## Batch 3 discipline leftovers (2026-09-28, documenter; for ROADMAP — recorded, not canonised)

Things the shipped CSS / templates at `8819bd9` still carry against the Named Rules. None is a design-system rule; each is a defect or a ruled tolerance the build inherits.

1. **Sub-11px ported sizes** (R42 "as v1", same class as #66): `board.css:2325` `.mh-ss` 9px; `:2308` `.mh-scatter .axt` 9.5px; `:2315` `table.mh-tbl th` 10px; `:2258` `.help.mh-dash` 10px; `:2476` `/dispatch .chip` 10px; `:2685` `/cost .chart .col .v` 10px; `:2863` `/rework .demotag` 10px; 10.5px at `:2233, 2405, 2449, 2458, 2461, 2521, 2524, 2541, 2663, 2675, 2693, 2710, 2712, 2837`. The Label step is 11px.
2. **Unicode glyphs kept by ruling**: `templates/v2/_dispatch_body.html:12` ▲▼■●· (R29, triple encoding); `templates/v2/_equipment_health_body.html:37` ↑↓→ with the verdict colour on the glyph itself, no `.vsym` class (R39) — a second symbol grammar beside `/output`'s `.vsym`; `_equipment_health_body.html:61` `◦` text bullet in the caveat box.
3. **Element-generic rules that reach the nav / assistant include**: `board.css:2489, 2515-2516, 2572` (`[data-page="manpower"] b, th, table, td, code`); `:2608` (`[data-page="cost"] b`); `:2751, 2806-2807` (`[data-page="budget"] b, th, table, td`); `:2866-2868` (`[data-page="rework"] table, th, td`). `/cost` already shows the fix (`.board-main table/th/td`, `:2646-2647`).
4. **`.tabs a.on` literal pinned globally**: `run_smoke.py:29431-29432` (`css.count('.tabs a.on{') == 1`) forces `/cost` to write `.tabs > a.on` (`board.css:2616, 2728`); a selector shape dictated by a smoke pin, not by the system.
5. **Four copies of the `.seg` grammar**: `/monthly` (`:2045` region), `/manpower :2503-2508`, `/cost :2622-2626`, `/budget :2764-2770` are the same rules written four times; the class literal `on` is retained for `run_smoke.py:18867`. One shared `.seg` rule (like the shared `.help`) is the obvious consolidation.
6. **Dark values that are light literals**: `--tk-overt` dark = light `rgba(179,66,58,.13)` (`board.css:2748`, R31 "zero visible change"); root `--band-edge2/3` and `--band0..3`'s dark block re-declares only `edge0/edge1` (`:86-87`) so `/manpower` bundles 2 and 3 use the light edge hue in dark.
7. **Filled caution banner on `/equipment/health`**: `.mh-srcbanner` = `--tint-warn` fill + `--unk` frame (`board.css:2342-2343`), following the batch-1 `/equipment`-family precedent (`:818`), while `/dispatch`'s `.dsp-banner` (`:2387`) and the landing banner are frame-only (§G2). Two banner forms for the same honesty message; Edge-Not-Fill says box → edge.
8. **One word, two colours on `/dispatch`**: bundle `.kd-lamp.down` = `--mut` (no work) vs station `.lamp.down` = `--st-repair` (whole station on hold) (`board.css:2425, 2473`; R29). Both carry ink words, but the legend-less page relies on the reader knowing the level.
9. ✅ **Closed 2026-09-28 (R33 second ruling, `b613bbd`)**: `/budget`, `/cost*`, `/manpower` no longer print the nav source badge (v1 + v2; links keep `source=`); `/rework` keeps both marks (it has source semantics). Original entry: **Source badge on pages with no source semantics** (R33, ruled "as v1"): `/rework` prints the nav `.board-src` and a page `.demotag` (`board.css:2863`, `templates/v2/rework.html`); `/manpower` shows a source badge although SPEC-MANPOWER §4⑤ already ruled it off (not yet landed, v1/v2 same change).
10. **Per-page `.help` spacing copies** after R36 unified the glyph: `board.css:633, 2257, 2428` (`margin-left:5px`), plus the `/daily` and `/output` hand-written `?` markup instead of the `_c_help.html` macro; the shared rule owns the form, the call sites still differ.
