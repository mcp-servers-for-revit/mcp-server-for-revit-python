# geothermal/

Geothermal borehole field design engine, built on [pygfunction](https://pygfunction.readthedocs.io/en/stable/)
2.3.1. Same shape as `hydraulics/`: pure Python 3.12, no Revit dependency,
public functions take/return plain dicts and lists so this can be called
from `tools/geothermal_tools.py` (MCP) or from the native GUI
(`../HC.GeoBore/`, a WPF app) over a JSON contract without exposing
pygfunction objects across that boundary.

## Modules

| Module | Covers |
|---|---|
| `fields.py` | All 8 pygfunction layouts (rectangle, staggered, dense, box, U, L, circle, custom points), file import, combining fields |
| `fluids.py` | Water/MEG/MPG/MEA/MMA properties via pygfunction's SecondaryCoolantProps binding |
| `pipes.py` | Single/multiple/independent-multiple U-tube, coaxial; effective borehole resistance R_b*; `suggest_flow_rate_kg_s` for a per-borehole flow suggestion from thermal duty + pipe geometry |
| `gfunctions.py` | g-functions for UHTR, UBWT (via `Borefield.evaluate_g_function`) and MIFT (via `gFunction.from_static_params`); all 3 solvers |
| `simulation.py` | Multi-year hourly simulation via load aggregation (ClaessonJaved/MLAA/Liu) -- borehole-wall and fluid temperature history |
| `networks.py` | Boreholes wired in parallel/series/mixed circuits (`Network.from_static_params`), reversed flow direction, variable-flow-rate g-functions (Cimmino 2024) |
| `sizing.py` | Our own iterative depth-sizing loop on top of the above (pygfunction has no automatic sizing); also `field_layout_from_area` (area -> N1xN2 grid) and `synthesize_peak_only_load` (2 design peaks -> 8760-hour worst-case load) for the area+peak "quick sizing" path |
| `sizing_ghetool.py` | GHEtool-based sizing, for cross-checking `sizing.py`. **Do not import directly** -- see below |
| `sizing_ghetool_client.py` | Safe-to-import client: runs `sizing_ghetool.py` in an isolated subprocess |
| `ghetool_worker.py` | The subprocess entry point `sizing_ghetool_client` spawns |
| `hybrid.py` | Optional supplemental cooling tower/dry cooler (peak-shaving): trims an hourly load array before it reaches `sizing.py`, so a cooling-dominated field can be sized smaller; also `minimum_tower_capacity`, which finds the smallest tower that makes a depth-capped field work |
| `engine_cli.py` | Unified JSON-over-stdio entry point (`{"command": "...", "args": {...}}` in, `{"result": ...}`/`{"error": ...}` out) -- every module's public API registered as a command. This is what `../HC.GeoBore/EngineClient.cs` calls as a subprocess, one process per request. |
| `pipe_catalog.py` | Vendor pipe/grout lookups across 4 pipe manufacturers and 3 grout manufacturers (`data/<manufacturer>_pipes.json` / `_grouts.json`), so field data stays sourced to a real datasheet instead of hand-typed numbers going stale -- see "Vendor catalogs" below |

The `pipe_type_str` mapping (our pipe config dict -> pygfunction's
`from_static_params` pipe type string) lives once in
`pipes.pygfunction_pipe_type_str` and is shared by `gfunctions.evaluate_mift`
and `networks.build_network`.

**Not yet built:** hand-built `Network` construction for
`independent_multiple_u_tube` or >2 legs per borehole (only
`from_static_params`'s single/double-U-tube/coaxial set is wired up);
`networks.evaluate_variable_flow` isn't yet connected into
`simulation.py`'s hourly loop (which assumes constant flow).

**Behavior worth knowing:** pygfunction's `'equivalent'` solver is only
valid for all-parallel networks -- for series/mixed connectivity it silently
substitutes `'similarities'` and raises a Python `UserWarning` that a GUI
would never see. `networks.evaluate()` does that substitution itself up
front and reports it via `result["method_used"]` instead.

## Validation

`tests/unit/test_geothermal_report_examples.py` pins the engine to the 3
worked examples in the pygfunction getting-started report (g=17.903 at 25y
UBWT, R_b*=0.150 m.K/W, MIFT g=18.241), plus the report's own solver/boundary-
condition cross-checks. `tests/unit/test_sizing_cross_check.py` cross-checks
`sizing.py` against `sizing_ghetool.py` on a shared case (currently ~7%
apart on sized depth, R_b* within 0.6% -- the gap is the simplified
monthly-to-hourly load synthesis in `sizing.synthesize_hourly_load`, not a
g-function/simulation discrepancy).

**GHEtool cross-check pipe coverage:** `sizing_ghetool._ghetool_pipe_data`
supports `single_u_tube` (`GHEtool.SingleUTube`) and `multiple_u_tube` wired
`"parallel"` (`GHEtool.MultipleUTube`, `config="diagonal"` -- its axis-
symmetric leg placement for `number_of_pipes=2` is the same cross layout
`HC.GeoBore/MainWindow.xaml.cs`'s `BuildPipeConfig()` sends for "Double
U-tube, parallel"). A `"series"`-wired `multiple_u_tube` raises
`NotImplementedError` rather than being sized: GHEtool's `MultipleUTube` pipe
model always builds pygfunction's `MultipleUTube` with the library default
`config="parallel"` (GHEtool's own `config` constructor argument is a
*different*, geometric "diagonal"/"adjacent" leg-placement choice, never
forwarded as pygfunction's *hydraulic* `config` kwarg) -- so a series design
has no way to be represented and sizing it anyway would silently cross-check
against a different (parallel) design instead of failing loudly.
`independent_multiple_u_tube` and `coaxial` have no GHEtool equivalent wired
up here either.

## GHEtool: import in isolation only

Two real problems found while wiring this up, both worth knowing before
touching `sizing_ghetool.py`:

1. **~850 MB dependency tail.** `ghetool` hard-requires `torch` (481 MB),
   `optuna` and `scikit-learn` even though, in this codebase's usage, only a
   single specialty pipe model (`MuoviEllipse`) and an optional
   configuration optimizer ever touch them. There's no extras split to avoid
   it -- the whole package imports eagerly.
2. **Process-wide monkey-patch.** `GHEtool.VariableClasses.Cylindrical_correction`
   rewrites `pygfunction.solvers._BaseSolver.solve`/`__init__` and
   `pygfunction.solvers.Equivalent.thermal_response_factors` **at import
   time**, globally, for the whole process (its own docstring calls this
   "a temporary solution, until issue #44 of pygfunction is solved"). Once
   `GHEtool` has been imported anywhere in a process, *unrelated* direct
   pygfunction calls made afterward can break -- observed live: a plain
   `gFunction.from_static_params(..., boundary_condition="MIFT", ...)` call
   that works before `import GHEtool` raises
   `TypeError: unsupported operand type(s) for *: 'NoneType' and 'NoneType'`
   deep inside `pygfunction.networks` after it.

   Also: `GHEtool.Borefield(borefield=..., ground_data=..., ...)` --
   passing `ground_data` as a constructor keyword bypasses the `ground_data`
   property setter, so `self.custom_gfunction` is never initialized and
   `.size()` crashes with `AttributeError`. Workaround: construct a bare
   `Borefield()` and assign `ground_data`/`fluid_data`/`flow_data`/
   `pipe_data`/`load`/`borefield` via their property setters afterward (see
   `sizing_ghetool.py`).

**Consequence:** `sizing_ghetool.py` must never be imported in the same
process as `gfunctions.py`/`simulation.py`. Always go through
`sizing_ghetool_client.size_field_isolated()`, which runs it via
`python -m geothermal.ghetool_worker` in a fresh subprocess. Given both
findings, dropping GHEtool once `sizing.py`'s own sizing is trusted (per the
user's own plan) removes ~850 MB and this whole isolation requirement in one
move.

## Hybrid cooling tower

`hybrid.apply_cooling_tower(hourly_load_W, tower_capacity_kW)` models a
supplemental heat-rejection device (cooling tower / dry cooler) with a
**peak-shaving** control strategy: in every cooling hour it removes
`min(tower_capacity_kW, that hour's cooling demand)` from the load before it
reaches the ground, and never assists heating. It's a pure array transform --
call it on an hourly load array, feed the returned `ground_load_W` into
`simulation.run_hourly_simulation` or `sizing.size_field` in place of the
untouched load, same pattern as everything else in this package.

The one detail worth getting right, since an earlier draft of this module got
it backwards: **tower capacity must bound what the tower itself does, not
what the ground ends up seeing.** Clipping the *ground's* load at a threshold
(the wrong version) makes the implied tower duty unbounded -- a "5 kW tower"
could be asked to reject 50 kW in a bad hour, which no 5 kW tower can
actually do. `apply_cooling_tower` bounds duty at the tower's own rating
instead, so `result["tower_peak_kW"] <= tower_capacity_kW` always holds.

Like `sizing.synthesize_hourly_load`, this is a deliberately simple stand-in:
no wet-bulb-dependent tower performance, no approach temperature -- the tower
is an ideal, always-available, capacity-limited heat sink. Good enough to
answer "how much smaller does the field get with a tower of this size," not a
substitute for a real hybrid plant design.

**Ground-temperature deadband dispatch** (added 2026-09-28, following Yu et
al. 2026, *Buildings* 16(18) 3714, https://doi.org/10.3390/buildings16183714):
a second control strategy, alongside peak-shaving, that runs the tower only
once the borehole wall has actually drifted `tower_deadband_C` above a
setpoint (defaulting to `T_g`), off again once it falls back to the setpoint
-- their "ground-temperature-based control strategy," which held 10-year
ground temperature drift to +0.28 C in their case for noticeably fewer tower
run-hours than shaving every cooling hour. Unlike `apply_cooling_tower` (a
one-shot array transform independent of the ground's own response), this
dispatch rule needs the ground temperature the *simulation itself* computes
hour by hour, so it can't be a preprocessing step -- `simulation.
run_hourly_simulation` grew an optional `tower_control` hook (a per-hour
callable receiving the previous hour's `T_b`, returning the adjusted load) for
exactly this; passing `None` (the default) reproduces the function's prior
behavior byte for byte. `hybrid.deadband_tower_controller(T0_C, deadband_C,
tower_capacity_kW)` builds the stateful hysteresis controller (pure Python,
directly unit-testable); `hybrid.run_hourly_simulation_with_deadband_tower(...)`
is the flat, JSON-friendly wrapper engine_cli/the WPF UI call, since a Python
closure can't cross the subprocess boundary itself. Wired into the Simulation
and Sizing tabs' shared "Control strategy" dropdown next to the existing tower
capacity input; Area Sizing still uses peak-shaving only -- `minimum_tower_
capacity`'s capacity-bisection would need its own integration of this to gain
it, not yet done (see below).

**Deadband dispatch in depth SIZING** (`sizing.size_field`, added the same
day): unlike `apply_cooling_tower`, the deadband controller's behavior is
depth-dependent -- a shallower field warms up faster, so the tower would need
to run sooner/longer there -- so its output can't be precomputed once and
reused across every `H` the bisection tries, the way `apply_cooling_tower`'s
can. `size_field` grew an optional `tower_control_factory` hook: a zero-arg
callable that mints a *fresh* controller (reset on/off state) for every depth
trial, called from inside the bisection's own `_run(H)` closure -- reusing one
controller across trials would leak one (possibly rejected) depth's hysteresis
state into the next depth's result. `hybrid.size_field_with_deadband_tower(...)`
is the flat wrapper, mirroring `run_hourly_simulation_with_deadband_tower`.
Pass the raw (pre-tower) load, same as `size_field` without a tower -- there is
nothing to precompute before calling it.

Worth knowing: a cooling tower only ever helps in cooling hours, so on a
**heating-dominated** load it can size *deeper*, not shallower -- less summer
heat rejection reaching the ground each year means less "recharge," which
pushes the following winter's minimum fluid temperature down over the design
life (the same non-monotonic-in-capacity effect `minimum_tower_capacity`'s
docstring already documents). Confirmed both ways in
`tests/unit/test_geothermal_hybrid.py`: shrinks the sized depth on the
cooling-dominated fixture there, and was observed live to slightly *grow* it
on this project's own heating-dominated demo load profile (net +6700 kWh/yr
extraction) -- not a bug, the physically correct answer for that profile.

Because GHEtool sizes from monthly loads directly, there's no way to hand it
a tower-adjusted hourly profile -- the GHEtool cross-check in
`engine_cli.py`/`HC.GeoBore` always compares against the no-tower baseline on
both sides; the tower's effect is shown separately.

**Deadband dispatch in CAPACITY sizing** (`hybrid.minimum_tower_capacity`,
added the same day): a third variant of the same pattern, for the Area Sizing
tab's "field alone can't meet the limit, what's the smallest dry cooler that
fixes it" fallback. `minimum_tower_capacity` grew a `tower_control_factory`
hook too -- one argument this time (the candidate `capacity_kW`, since
capacity is what *this* bisection varies, not depth), called once per
capacity trial for the same state-leak reason as `size_field`'s.
`hybrid.minimum_deadband_tower_capacity(...)` is the flat wrapper. Wired into
Area Sizing's own "Dry cooler strategy" dropdown (separate from the shared
Hybrid cooling tower panel, which stays disabled on this tab since capacity
here is a solved-for *output*, not something the user enters).

Worth knowing: on Area Sizing's own load model
(`sizing.synthesize_peak_only_load` -- a **constant block** for the whole
cooling season, no part-load shape), the deadband strategy converges to
essentially the *same* capacity and tower run-hours as peak-shaving, not
fewer. Confirmed directly: on a `T_g=12 C`, `H=55 m` case with the default
80/120 kW peak loads, both strategies picked the same ~11 kW capacity, and
the deadband controller ended up on for ~2952 h/yr either way (out of 2952
actual cooling hours/year in that load) -- once triggered, a controller with
no "off" period to find in a months-long unbroken block just stays on for the
whole block, same as peak-shaving. The deadband strategy's real advantage
(fewer run-hours) only shows up against a load with genuine intra-season
swings -- the Simulation/Sizing tabs' `synthesize_hourly_load`, or real
monthly data -- not this tab's deliberately conservative worst-case model.

## Area + peak-load quick sizing

For an early feasibility question -- "given my building's peak heating and
cooling loads and how much land I have, what's the minimum borehole depth,
and do I need a dry cooler" -- three pieces compose into that answer without
a monthly load profile:

1. `sizing.field_layout_from_area(area_ambient_m2, area_under_building_m2,
   spacing_m, max_rows=None)` -- largest N1xN2 grid that fits the combined
   area. The two areas are summed, not modeled as separate sub-fields: this
   engine's ground model has no surface boundary condition that would
   distinguish "under a building" from "open ground" anyway. Defaults to
   the largest roughly-square grid; pass `max_rows` when the site isn't
   square or its boundary isn't perpendicular to a natural grid -- N_2
   (the "row" direction) is capped at that value and N_1 grows to keep
   roughly the same total borehole count, reshaping the field into a
   longer, narrower layout instead of a smaller one.
2. `sizing.synthesize_peak_only_load(peak_heating_kW, peak_cooling_kW,
   heating_season_months, cooling_season_months)` -- turns just two design
   peaks into an 8760-hour year by assuming each peak runs *continuously*
   for its whole season (centered on January/July). No part-load shape, so
   it's deliberately conservative (over-predicts annual ground duty) --
   good for a worst-case screening check, not a substitute for
   `synthesize_hourly_load` once real monthly data exists.
3. Feed both into `sizing.size_field` as usual, with `H_max` set to the
   maximum *practical* (drillable) depth rather than an arbitrary search
   ceiling. If that succeeds, done -- no dry cooler needed. If it raises
   (the field can't hold the limit even at max depth), fall back to
   `hybrid.minimum_tower_capacity(..., H=H_max, ...)` to find the smallest
   tower that closes the gap at that fixed depth.

`minimum_tower_capacity` bisects on the max-temperature (cooling) margin
specifically, not the combined min/max margin the way `size_field` bisects
on H. That distinction matters: more tower capacity monotonically helps the
max-temperature limit, but it also removes cooling "recharge" from the
ground each year, which pushes the *minimum* fluid temperature down over
the design life -- so the combined margin is **not** monotonic in tower
capacity, and bisecting on it directly can wrongly call a solvable case
infeasible (caught by a test with a real, if extreme, case: a tower sized
to fully eliminate cooling load solved the max-temperature limit but then
failed the min-temperature limit instead). The function finds the minimum
capacity that satisfies the max limit, then checks the min limit
separately, and raises a specific error -- not a silently wrong answer --
if a dry cooler alone can't satisfy both.

## Flow rate suggestion, and single vs. double U-tube

`pipes.suggest_flow_rate_kg_s(capacity_W, config, fluid_str, fluid_percent,
fluid_temperature_C, design_delta_T_C=5.0, min_reynolds=4000.0)` proposes a
per-borehole mass flow rate from a per-borehole thermal duty and the
selected pipe, as the **larger** of two candidates:

1. **Design-ΔT method** (the standard HVAC sizing approach):
   `m_dot = |capacity_W| / (cp * design_delta_T_C)`. `design_delta_T_C` is a
   design choice (5 C is a common ground-loop default), not a measured
   constant -- callers can pass their own.
2. **Turbulent-flow floor**: the minimum flow that keeps every individual
   pipe *leg's* Reynolds number at or above `min_reynolds` (4000 by
   default). Laminar flow inside the pipe sharply raises the convective
   film resistance component of R_b*, a real constraint, not a nice-to-have.

Both use `pipes.build_pipe`'s own per-leg-flow convention: for
`multiple_u_tube`, each parallel leg only carries `m_flow_borehole /
nPipes`; for `single_u_tube`, every leg carries the full flow. That
convention is also why **a double U-tube needs proportionally more total
flow to pay off**: at the *same* total borehole flow, a parallel double
U-tube's legs each carry half the single U-tube's per-leg flow, which
lowers Reynolds number and raises convective resistance enough to
outweigh the benefit of more conductive pipe surface -- `R_b*` can come
out *higher*, not lower, than a single U-tube at matched total flow (see
`tests/unit/test_pipes_flow_and_double_u.py`, which asserts both
directions explicitly: double U-tube wins at *matched per-leg flow*, and
can lose at *matched total flow*). This is exactly why
`suggest_flow_rate_kg_s`'s turbulence floor scales with `nPipes`.

`multiple_u_tube` (double U-tube, `nPipes=2`, `config="parallel"` or
`"series"`) was already fully wired through `pipes.build_pipe`,
`pygfunction_pipe_type_str`, and therefore `simulation.run_hourly_simulation`
/ `sizing.size_field` / `gfunctions.evaluate_mift` -- but had **zero test
coverage anywhere in this repo** before it was exposed through
`HC.GeoBore`'s "Pipe configuration" dropdown. Validated before shipping,
not just assumed to work because the code existed.

## Vendor catalogs

`pipe_catalog.py` loads one JSON file per manufacturer
(`data/<manufacturer>_pipes.json`, `data/<manufacturer>_grouts.json`),
tags each entry with that file's `"manufacturer"` name, and exposes
`list_pipes(manufacturer=None)` / `list_pipe_manufacturers()` /
`get_pipe(key)` and the equivalent `*_grouts`/`*_grout` functions. Add
another manufacturer by dropping in a similarly-shaped file and listing it
in `pipe_catalog._PIPE_FILES`/`_GROUT_FILES`.

**Pipes** (all PE100-RC or PE100/PE100-RC, all SDR11 unless noted) -- see
each file's own `_source` citation for the exact datasheet and retrieval
date:

| Manufacturer | Source | Sizes |
|---|---|---|
| REHAU | RAUGEO PE-Xa European technical manual (doc 827600 ES 10.2024) | 20x1.9 - 75x6.8mm |
| Gerodur | GEROtherm DUPLEX product page dimension table | 32x3.0 - 50x5.6mm (incl. 2 SDR9 sizes) |
| Muovitech | MuoviTech UK "PE Pipe" datasheet, SDR11 PN16 table | 20x2.0 - 75x6.8mm |
| Pipelife Bulgaria | Pipelife's group-wide PE100 SDR11 PN16 catalog (no BG-specific geothermal-branded page found) | 32x3.0 - 63x5.8mm |

None of the four publish a pipe-material thermal conductivity distinct
from generic PE for their PE100/PE100-RC/PE-Xa products -- `k_p` uses the
same industry-standard 0.40 W/m.K for all of them (REHAU's own Technical
Bulletin TB246 documents this as a negligible difference for modeling).
Pipelife's fetched catalog page listed dimensions under "PE100" rather
than "PE100-RC" explicitly; noted in that file, since RC denotes a
crack-resistance *material* grade, not a different *dimensional* standard
-- the same wall thicknesses apply to both.

**Grouts**:

| Manufacturer | Product(s) | k_g (W/m.K) |
|---|---|---|
| REHAU | RAUGEO fill rojo / azul | 2.0 / 1.2 |
| Muovitech | MuoviTeco 2.0 (cement-free bentonite-silica) | 2.0 |
| Fischer Spezialbaustoffe | GeoSolid 240-HS / GeoSolid 235 | 2.40 / 2.35 |

Gerodur's own product line only covers grouting *installation accessories*
(tremie tubes, packers) -- no grout material itself was found. No
Pipelife-branded grout was found either. Fischer's two GeoSolid variants
are included specifically as the documented fallback for those two.
GeoSolid 240-HS has a full primary-source datasheet (density, compressive
strength, water permeability, all in `fischer_grouts.json`); GeoSolid 235's
thermal conductivity is corroborated by multiple independent citations of
Fischer's own published figure, but Fischer's current site only actively
publishes a complete property table for 240-HS -- 235's other properties
are omitted rather than assumed equal to 240-HS's copied-over numbers.

Exposed via `HC.GeoBore`'s cascading "Pipe manufacturer" -> "Pipe product"
and "Grout manufacturer" -> "Grout product" dropdowns (grout manufacturer
is independent of pipe manufacturer -- pick whichever grout you're
actually using, regardless of who made the pipe). Selecting a real product
locks r_in/r_out/k_p (pipe) or k_g (grout) read-only, so a sourced catalog
value can't silently drift out of sync with a hand edit; picking "Custom"
unlocks them again for manual entry. Every calculation requires both a
pipe and grout product selected (Custom counts) -- the UI refuses to run
with an unselected product rather than calculating against whatever
happened to be left in the text fields. Grout k_g, pipe r_in/r_out/k_p,
and fluid/percent/temperature all stay **disabled and blank** until both
manufacturer+product pairs have a selection -- nothing defaults to
"Custom" on launch, so there's no arbitrary pre-filled number sitting in a
field before the user has actually decided on a pipe and grout.

## GUI

**GeoBore** (`../HC.GeoBore/`, solution `../HC.GeoBore.sln`, project/assembly
name kept as `HC.GeoBore`/`GeoBore.exe` for build continuity -- the
user-facing name everywhere it's actually seen, window title through About
dialog, is "GeoBore" only): a WPF app that calls this package's
`engine_cli.py` as a subprocess. Started 2026-09-21, now 4 tabs (g-Function /
Simulation / Sizing / Area Sizing) sharing one set of input controls -- field
layout, ground properties, pipe/fluid (cascading pipe/grout manufacturer ->
product dropdowns across 4 pipe and 3 grout manufacturers, pipe configuration
single/double U-tube), monthly load profile, and the optional hybrid cooling
tower all live in one left-hand panel instead of being duplicated per tab;
each tab only holds what's genuinely calculation-specific, and greys out
shared inputs it doesn't use (or, on Area Sizing, solves for) rather than
hiding them silently. Flow per borehole has an "Auto-calc" button next to it
(`pipes.suggest_flow_rate_kg_s`, driven off whatever peak heating/cooling
capacity is currently filled in anywhere in the UI and the currently
selected pipe). `../HC.GeoBore.Tests` (34 tests, no mocks for the engine
calls -- real subprocess) proves the bridge end-to-end against the same
report worked examples and sizing/tower/area/double-U-tube/catalog/GHEtool
tolerances this package's own tests use, plus pure-C# round-trip tests for
the project file format and the chart builders. Other layouts and `networks.py` are reachable
through `engine_cli.py` but don't have a screen yet.
`EngineClient.CreateDefault()` points at this repo's own `.venv`
interpreter by default (overridable via Settings); packaging for
distribution means swapping that default for a frozen engine build
(PyInstaller), which does not change the stdin/stdout protocol itself. App
icon: `Resources/app.ico` (multi-resolution, 16-256px).

**Result graphics** (added 2026-09-25; OxyPlot.Wpf 2.2.0 for the xy/bar charts,
plus one custom-drawn control). Every chart is a pure function of arrays the
engine already returned -- `size_field` and `minimum_tower_capacity` hand back
the full hourly `T_f_C`/`T_b_C` of the design they settled on, so the Sizing and
Area Sizing charts show *exactly the run that produced the answer* and cost no
extra engine call. Each tab's results column is a scrollable stack:

| Tab | Graphics |
|---|---|
| g-Function | g vs ln(t/ts) with a locked secondary time-in-years axis and a design-life marker; field plan view; the data table |
| Simulation | Fluid temperature over the whole period (min/max envelope + mean wall temperature); monthly net ground load; field plan view; yearly table |
| Sizing | Fluid temperature at the sized depth with the min/max limit lines drawn (the design-driving constraint is the envelope touching a limit); monthly net ground load (tower vs. no tower side by side when the tower is on); field plan view; depth comparison bars (engine / engine + tower / GHEtool L3, shown only when there is something to compare) |
| Area Sizing | To-scale plan view of the derived grid (so a `max rows` layout visibly becomes a long strip) with a ground-area-used bar; fluid temperature at the result depth with limits; monthly net ground load (with the dry cooler's effect when one was needed) |

Design notes: the 8760 x years hourly series is bucketed to ~1500 min/max pairs
so a single sharp peak still reaches the chart; monthly loads use the same
non-leap month lengths `sizing.py` lays its year out with (extraction warm/+,
injection cool/-); `FieldPlanView` takes plain borehole positions (not N1/N2)
so a future non-rectangular layout can reuse it. OxyPlot 2.2 has no
`ColumnSeries` and its `BarSeries` is horizontal-only, so vertical monthly
columns are `RectangleBarSeries` (data-unit widths). Mouse-wheel zoom is turned
off on the charts so the wheel scrolls the results page.

**Menu bar** (File / Settings / Help), added 2026-09-21:
- **File**: New/Open/Save/Save As a `.geobore` project (JSON, holding every
  input control across the shared panel and all 4 tabs -- not results,
  those are cheap to recompute); Recent Projects; Save Iteration.../
  Iteration History... (named snapshots embedded in the *same* project
  file, not separate files, so one project stays one portable file); Exit.
- **Settings**: engine path override + a Test Connection button; the flow
  auto-calc's design-delta-T/min-Reynolds defaults (previously hardcoded);
  autosave toggle + interval.
- **Help**: About, View README (opens this file), Engine Diagnostics
  (interpreter path, command count, REHAU catalog load counts).

`ProjectInputs`/`ProjectIteration`/`ProjectFile` (`HC.GeoBore/ProjectModel.cs`)
are plain DTOs serialized with `System.Text.Json`; `AppSettings.cs` persists
tool-level (not per-project) preferences to
`%APPDATA%\GeoBore\settings.json`. Combo selections save as plain
`SelectedIndex` -- for the REHAU pipe/grout dropdowns specifically, that
ties a saved project to the catalog's *current order*, not a text-matching
resolver; a documented simplification, not a robustness guarantee if the
catalog is ever reordered.
