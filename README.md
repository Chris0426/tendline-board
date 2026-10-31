# Tendline Board

**A maintenance board for a production line: it fuses machine production records and maintenance
work orders into one state timeline per machine, and runs end to end on a synthetic production line.**

> **Everything in this repository is synthetic.** The local quick start runs a generated production
> line on a live clock. There is no real factory data, machine, person, name or number in this
> repository, and every page of the synthetic process carries a "synthetic line" banner.

## What it does

- **One screen for the whole line.** Every machine, what it is doing now and its last seven days
  (`/equipment`). Open a machine for its timeline (`/machine/{eid}`); open a station for its machines
  (`/station/{cls}`).
- **Quiet is not broken.** A machine that stops with no explanation is *unknown* until a work order
  says why.
- **Confidence, not guesses.** Every state segment carries a confidence of high, medium or low. No
  signal means low. Anything that cannot be measured is printed as "—" with a reason.
- **One record, one number.** Pages read the same fused records, so a repair on a machine's timeline
  shows the same duration on its station page.

## Run it locally (about five minutes)

Tested with Python 3.14 on Windows. Linux and macOS have not been tried yet.

**Time zone:** the synthetic world runs on UTC+8 wall-clock time, and the launcher refuses to start
unless the process is at UTC+8. On Linux or macOS, set `TZ=Asia/Taipei` as shown below. On Windows
the machine's own time zone must be UTC+8; there is no per-process override today.

The board runs on its own. A second, optional process serves a read-only synthetic stand-in for the
work-order API, so the pages that show work orders have something to read.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Optional, terminal 1: synthetic stand-in for the work-order API (read-only, loopback)
python wsynth_cmms.py --port 8062

# Terminal 2: the board on the synthetic line
$env:BOARD_SOURCE = "synth"
$env:BOARD_STATE_DIR = "$env:TEMP\tendline_state"     # runtime state; must be outside any git work tree
$env:BOARD_CMMS_BASE_URL = "http://127.0.0.1:8062"    # only if terminal 1 is running
$env:BOARD_CMMS_TOKEN = "wsynth-loopback-token"       # only if terminal 1 is running
python wsynth_launch.py --check                       # startup checks + build the world, then exit (0 = ok)
python wsynth_launch.py --port 8050
```

**Linux / macOS (bash, untested):**

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export TZ=Asia/Taipei

# Optional, terminal 1
python wsynth_cmms.py --port 8062

# Terminal 2
export BOARD_SOURCE=synth
export BOARD_STATE_DIR="$(mktemp -d)"
export BOARD_CMMS_BASE_URL=http://127.0.0.1:8062      # only if terminal 1 is running
export BOARD_CMMS_TOKEN=wsynth-loopback-token         # only if terminal 1 is running
python wsynth_launch.py --check
python wsynth_launch.py --port 8050
```

The first start builds the synthetic world (a few seconds). Then open:

| Page | URL |
|---|---|
| Line overview (start here) | http://127.0.0.1:8050/ |
| Equipment standup | http://127.0.0.1:8050/equipment |
| Maintenance health | http://127.0.0.1:8050/equipment/health |
| Availability | http://127.0.0.1:8050/availability |
| Utilization | http://127.0.0.1:8050/utilization |
| Stations | http://127.0.0.1:8050/station |
| One station / one machine | click through from the pages above (`/station/{cls}`, `/machine/{eid}`) |

Add `?lang=zh` to any page for Traditional Chinese. The theme follows your system light/dark setting.
Other pages of the original application (cost, manpower, assistant and so on) return 404 in the
synthetic process.

The launcher binds to `127.0.0.1` by default. It never connects to a database: at startup the database
driver is replaced by a stub that raises, so a synthetic process cannot reach a real data source.
Without the optional work-order process, pages that need work orders say the source is not connected.

## How it works

```
         seed + wall clock (UTC+8)
                     |
           +---------v----------+
           |  wsynth_world.py   |  one deterministic world: incidents first, then machine
           |  pure, no I/O      |  segments and work-order events derived from the same incidents
           +----+----------+----+
                |          |
   machine segments      work-order events
                |          |
      +---------v-----+  +-v--------------------------+
      | wsynth_io.py  |  | wsynth_cmms.py             |  read-only stand-in for
      | I/O boundary  |  | (work-order API stand-in)  |  the work-order API
      +---------+-----+  +-+--------------------------+
                |          |  HTTP -> board_w8_transport.py -> board_w8.py (normalise)
                v          v
      board_data.py -- fusion: one state timeline per machine,
                       confidence high / medium / low
                |
      app.py -- FastAPI + Jinja + HTMX pages
```

- **The engine is not special-cased for the synthetic line.** Only the input boundary is swapped
  (`wsynth_io.py`, `wsynth_cmms.py`); fusion, ledgers and pages run the same code path they would run
  on real inputs.
- **Deterministic.** The same seed and generator version give the same world at the same moment;
  moving the clock forward never rewrites events that have already appeared.
- **Startup tripwire** (`core_source.py`, `startup_tripwire()`): database driver stubbed, UTC+8
  checked, state directory outside the repository and marked, and the work-order API allowed only on
  loopback or an allow-listed host.
- **The language model sits at the edges only.** `board_mcp.py` exposes read-only tools and
  `board_assistant.py` is a chat panel that shows what it read. Neither decides a machine's state.
  The assistant is switched off in the synthetic process.

## Repository map

| Files | What they are |
|---|---|
| `app.py` | FastAPI app: routes, page assembly, UI strings (one large file) |
| `board_data.py`, `board_w8*.py`, `mes_timeline.py` | Fusion core: state timeline, work-order normalisation, segment model |
| `board_*.py` | Page-level computations (availability, ledgers, station views, exports, rendering) |
| `wsynth_*.py` | The synthetic production line: world generator, clock, I/O boundary, work-order API stand-in, launcher |
| `core_source.py`, `core_paths.py` | Process-wide source flag, startup tripwire, runtime paths |
| `mes_*.py` | SQL fetchers used against a real production database in the original deployment; inactive in synthetic mode |
| `board_mcp.py`, `board_assistant.py` | Read-only tool surface and chat panel (off in synthetic mode) |
| `templates/`, `static/` | Jinja/HTMX templates and the design system (`DESIGN.md`, `SPEC-UI-V2.md`) |
| `_fixtures/` | Hand-written or synthetic fixtures used by self-tests |
| `_publish/verify_independent.py` | The independent verifier from the publishing pipeline (see below) |

## Tests

Every module carries its own self-test:

```bash
python wsynth_selftest.py               # synthetic world: determinism, prefix stability, exposure rule
python core_source.py --selftest        # source flag and startup tripwire
python board_w8.py --selftest           # work-order normalisation
python board_data.py --selftest         # fusion
```

A test that needs something only the private source repository has prints `skipped: <reason>`
instead of failing.

## How this public version is produced

This repository is generated from a private one by a publishing engine and is not edited by hand.
It contains a curated set of modules: the pages the synthetic line can serve and what they import.
Before it is published it must pass five gates: two independent zero-hit scans for real names and
values, module self-tests on the public tree, a secret scan, a human review of every sandbox page and
of the private mapping table, and a synthetic roster in place of the real fleet layout.
`_publish/verify_independent.py` is the second scanner, written from the specification only, without
reading the main engine.

## Scope and known limits

- Pages in this version: line overview, equipment standup, single-machine timeline, availability,
  utilization, maintenance health, stations. Cost, budget, manpower and yield pages are not included;
  small placeholder modules keep imports working.
- On the synthetic line, output, yield, quality and OEE are not modelled and show "—".
- Code comments, docstrings and self-test messages are mostly in Traditional Chinese.
- Comments sometimes mention internal design documents (`SPEC-*.md`, `CONCEPT.md` and others) that are
  not part of this repository.
- `requirements.txt` lists `pyodbc` for the original database fetchers; the synthetic process does not
  use the real driver.

## License

Not decided yet. Until a license file is added, all rights are reserved: you may read the code here,
but no permission to copy, modify or redistribute it is granted.

## Contact

See the author's GitHub profile.
