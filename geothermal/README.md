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
| `trt.py` | Thermal response test (TRT) parameter estimation: fits k_s (and, optionally, R_b) from a measured constant-injection fluid-temperature history via nonlinear regression against the same finite-line-source physics `simulation.py` uses -- see "TRT parameter estimation" below |
| `uncertainty.py` | Monte Carlo wrapper around `sizing.size_field`: resamples uncertain inputs (e.g. a TRT's own k_s +/- stderr) and reports sized borehole length as P50/P90 percentiles instead of one deterministic number -- see "Monte Carlo sizing uncertainty" below |
| `heat_pump.py` | Converts a BUILDING-side load into the GROUND-side load via a COP(EFT) curve (not a fixed COP), reporting compressor electrical energy too -- see "Heat pump coupling" below |
| `ground.py` | Turns a layered geological log (k_s/rho_cp/T_g per stratum) into thickness-weighted effective properties over the borehole's own buried span -- see "Layered ground" below |
| `thermal_mass.py` | Borehole internal thermal capacitance (grout+pipe+fluid) and the short-term fluid-temperature lag it causes -- see "Short-term borehole thermal capacitance" below |
| `groundwater.py` | Steady-state 2-D moving-line-source screening check for groundwater/Darcy advection past a borehole -- see "Groundwater/Darcy advection" below |

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

**Wet-bulb threshold dispatch** (added 2026-09-28, same day as the deadband
rollout, third and last of the strategies from Yu et al. 2026 -- see this
module's docstring): the "outdoor-temperature-based control strategy"
(WBT-GSHP) -- the tower runs whenever the OUTDOOR wet-bulb temperature is
below a threshold, regardless of what the ground is doing, since a lower
wet-bulb air stream lets the tower reject heat more efficiently.
`hybrid.synthetic_wet_bulb_series(min_wet_bulb_C, max_wet_bulb_C, n_hours,
coldest_day_of_year)` stands in for a real TMY/weather-file import (there
isn't one in this codebase yet) with a smooth annual sinusoid -- coldest in
January, hottest in July, the same seasonal convention
`sizing.synthesize_peak_only_load` uses. `hybrid.apply_wet_bulb_tower(
hourly_load_W, wet_bulb_C, threshold_C, tower_capacity_kW)` is the dispatch
itself.

Architecturally the simplest of the three strategies: unlike the deadband
controller, whether hour *i*'s tower runs depends only on that hour's
(precomputed, fixed) outdoor wet-bulb reading, not on anything the
simulation computes -- so it has no ground-temperature state to carry
between trials, and (like `apply_cooling_tower`) it's a plain,
depth/capacity-independent one-shot array transform. No `tower_control_
factory` wiring needed for depth sizing -- the Sizing tab just calls
`apply_wet_bulb_tower` once, then feeds the result into the *same*,
unmodified `size_field` peak-shaving already uses. Capacity sizing
(`minimum_tower_capacity`, the Area Sizing fallback) needed one new hook
though, since the transform still depends on the CANDIDATE capacity being
tried (how much duty each qualifying hour gets): `array_transform` -- a
two-arg `(hourly_load_W, capacity_kW) -> ...` callable, precomputed once per
capacity trial same as `apply_cooling_tower` always was, just swappable for
a different stateless strategy. `hybrid.minimum_wet_bulb_tower_capacity(...)`
is the flat wrapper. All three tabs (Simulation, Sizing, Area Sizing) expose
this as a third "Wet-bulb threshold" option in their existing tower-strategy
dropdowns.

Worth knowing, found directly while testing this: if a load's own peak
cooling hours land in the same season as the (synthetic or real) wet-bulb
maximum -- a common and realistic pattern, both being driven by the same hot
summer weather -- a wet-bulb-gated tower provides **zero** help at exactly
the hour that sets a hard temperature limit, no matter how large its rated
capacity. For depth sizing (`size_field`) this just means less benefit than
peak-shaving, not failure, since the bisection can still go deeper. For
CAPACITY sizing at a fixed depth (`minimum_tower_capacity`/`minimum_wet_bulb_
tower_capacity`) it can make the search genuinely infeasible -- correctly
reported as a `ValueError`, not silently returned as a capacity that doesn't
actually work. Pick a threshold high enough to cover the load's own peak
season, or accept that a wet-bulb-gated tower may not be able to fix a
capacity-limited peak the way peak-shaving or deadband dispatch could.

**Reporting a tower/dry cooler that provides zero real benefit** (added
2026-09-28, same day, in direct response to hitting the "zero help" case
above live): the engine already computed the right numbers when a tower
never actually engages -- `ground_load_W` comes back byte-identical to the
untouched load either way -- but the app was presenting that as if the tower
were part of the design (e.g. "ran 0 h/yr, 0 kWh/yr, peak duty 0.0 kW"),
which is misleading, and Area Sizing's capacity search raised a raw
`ValueError` traceback for the fully-infeasible case with no way to tell it
apart from "just needs more capacity." Two distinct fixes:

- `hybrid.minimum_tower_capacity` now checks the *baseline* (no-tower,
  `capacity=0`) result BEFORE deciding the max-capacity trial has failed, so
  it can tell "the largest tower tried made literally no difference to the
  worst hour" apart from "it helped some, just not enough." The former now
  raises a distinctly-worded error (`"...provides NO benefit at H=... m for
  this load, even at ... kW..."`) instead of the generic `"...doesn't bring
  the max fluid temperature under the limit..."` message, since the fix for
  each is different: raising `capacity_max_kW` truly cannot help the first
  case (only a deeper/larger field can), while it might for the second.
- On the WPF side, `MainWindow.xaml.cs` gained a `TowerHadNoEffect(JsonElement
  towerSummary)` helper (`tower_hours == 0`), checked by every tower-reporting
  call site. Simulation and Sizing suppress the tower-vs-no-tower comparison
  series/depth-bar and swap the per-year stats line for a one-line note
  ("never engaged over this period ... results above are the same as running
  with no tower at all") when it fires. Area Sizing catches the new
  `"provides NO benefit"` message specifically and replaces the traceback
  with a plain-language explanation plus concrete next steps (deeper max
  practical depth, larger field, or a different control strategy), instead of
  surfacing the underlying `EngineException` text.

Area Sizing's success path deliberately has no `TowerHadNoEffect` check: a
non-exception return from `minimum_tower_capacity` is now structurally
guaranteed to have `tower_hours > 0` at the winning capacity, since the
`capacity=0` baseline is checked first and would already have short-circuited
the whole call if the field didn't need a tower at all.

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
selected pipe). `../HC.GeoBore.Tests` (36 tests, no mocks for the engine
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

## TRT parameter estimation

Added 2026-09-28, from the accuracy-focused Phase 1/2 audit earlier this
session (see that audit's items 2 and 12) -- Engine only so far, **no WPF
screen yet**.

`trt.estimate_ground_properties_from_trt(H, D, r_b, time_s, T_f_C, Q_W, T_g,
rho_cp_J_m3K, k_s_guess, t_min_s, R_b_mK_W=None, R_b_guess=None,
gfunc_method="equivalent")` fits ground conductivity k_s (and, unless
`R_b_mK_W` is given to hold it fixed, borehole resistance R_b too) against a
measured single-borehole TRT fluid-temperature history, via
`scipy.optimize.curve_fit` (already an installed dependency -- `pygfunction`
itself requires `scipy>=1.13.1` -- so this added **zero** new dependencies).

Unlike the classical infinite-line-source semi-log method most TRT tools
use, the forward model here is the SAME finite-line-source g-function
`simulation.run_hourly_simulation` sizes with, so it correctly accounts for
borehole length/buried depth rather than approximating the borehole as
infinite. For a single borehole, UBWT and UHTR g-functions are numerically
identical (confirmed directly, `tests/unit/test_trt.py::
test_uhtr_and_ubwt_agree_for_a_single_borehole`, max abs diff ~1e-4), so the
same UBWT machinery is reused rather than adding a separate UHTR code path.
Because a TRT is a short, few-hundred-point measurement (not a multi-year
run), the module does an EXACT discrete temporal superposition (a causal
convolution against g(t)) rather than pygfunction's approximate
ClaessonJaved/MLAA/Liu load-aggregation algorithms -- those trade accuracy
for speed on long simulations, a trade not needed here.

Ground volumetric heat capacity (`rho_cp_J_m3K`) is a required, NOT-fit
input -- a single TRT is known to poorly constrain k_s and alpha
independently, so, matching standard practice, only k_s (which has good
sensitivity) is estimated. `t_min_s` (excluding early-time data from the
fit residuals, not from the heat-injection history the physics is computed
from -- excluding it from the latter would silently change the answer) is
also required, not guessed, since a wrong guess would silently bias the
fit; pick it from the standard Fourier-number rule of thumb (Fo = alpha *
t / r_b^2 > ~5) for your own expected ground type.

**Validation** (`tests/unit/test_trt.py`, 6 tests): the main test generates
a synthetic TRT curve via `simulation.run_hourly_simulation` (pygfunction's
APPROXIMATE load-aggregation convolution) at a KNOWN k_s=2.0 W/m.K,
R_b=0.1666 m.K/W, then checks `estimate_ground_properties_from_trt` (its
OWN EXACT convolution -- a genuinely different algorithm for the same
physics) recovers them. **Error before/after**: before writing this module,
there was no way to recover k_s/R_b from measured data at all (Phase 1
audit item 2: Missing). After: joint fit recovers k_s within 3.5% and R_b
within 1.5% of the known values (RMSE 0.026 C over the fitted window); the
small residual gap is the known, real difference between the two
convolution methods, not measurement noise (there is none in the synthetic
case) or a bug -- traced by hand during development (an initial version had
a real bug, a missing per-unit-length normalization on the heat rate before
convolution, caught immediately by this same test returning k_s~333 instead
of ~2). Holding R_b fixed at its true value (the "pipe/grout specs already
known" mode) tightens the k_s estimate further (stderr ~0.0013 vs ~0.0071
W/m.K for the joint fit, same synthetic data).

## Monte Carlo sizing uncertainty

Added 2026-09-28, same session, same audit (item 12). Engine only, **no WPF
screen yet**.

`uncertainty.size_field_monte_carlo(base_kwargs, uncertain_inputs,
n_samples=200, percentiles=None, random_seed=None)` resamples
`sizing.size_field` many times, drawing a fresh value for each declared
uncertain input on every trial, and reports the sized depth (`H_m`) as
percentiles (P50/P90 by default) instead of one deterministic number.
`base_kwargs` is the exact kwargs dict a normal `size_field` call already
takes -- deliberately not re-declared as its own giant parameter list, the
way most other `size_field` wrappers in this codebase are, since a generic
Monte Carlo wrapper's whole point is passing whatever `size_field` accepts
straight through unmodified (including future kwargs, e.g. a
`tower_control_factory`, this module has no reason to know about
individually). `uncertain_inputs` maps a `size_field` parameter name (
validated via `inspect.signature` against the real function, so a typo is
caught immediately rather than silently ignored) to a distribution spec --
`{"dist": "normal", "mean":, "stddev":}` or `{"dist": "uniform", "low":,
"high":}`, the two shapes an input like a TRT's own reported stderr, or a
stated ground-temperature range, actually gives you.

**Failure handling, the one real design decision here**: `size_field`
raises when even `H_max` fails the fluid-temperature limit. A trial that
fails this way is NOT dropped from the percentile calculation -- dropping
it would silently understate how bad the worst realistic outcomes are.
Instead its `H_m` is recorded as `H_max` itself (a censored lower bound on
what would actually be needed) and counted in `n_failed`/`fraction_failed`.
A "P90 == H_max" result together with a non-trivial `fraction_failed` is
itself a real finding (H_max is too tight for the stated uncertainty), and
is surfaced in the output, not hidden by quietly excluding those trials.

**Validation** (`tests/unit/test_uncertainty.py`, 8 tests): a zero-stddev
distribution exactly reproduces the deterministic `size_field` result
across all samples (regression-style sanity check); P90 >= P50 always;
the P50 of a +/-0.2 W/m.K k_s spread around 2.0 stays within 15% of the
single-point k_s=2.0 answer (H is smooth and monotonic in k_s over that
range, so this is a real, not tautological, check); a deliberately
infeasible-leaning k_s range produces `n_failed > 0` with every failed
`H_m` correctly censored at `H_max`. **Error before/after**: before, there
was no way to size a field under stated input uncertainty at all (Phase 1
audit item 12: Missing) -- a design either used one "best guess" k_s/T_g
silently, or a user had to hand-run `size_field` repeatedly and eyeball the
spread. After: an N-trial P50/P90 is directly computed and reported, with
infeasible outcomes honestly reflected rather than silently dropped.

**WPF screens added 2026-09-28, later the same day** (user: "wire up WPF toggles for the three built features") -- see "WPF toggles for TRT / Monte Carlo / MIFT" near the end of this file for what each screen does and its own scope decisions. DTRT (distributed, depth-resolved T(z) TRT analysis) is still not attempted, since it would need Phase 1 audit item 1 (layered ground) to interpret against and that has not been built; Monte Carlo sampling only covers `size_field`'s scalar inputs (k_s, T_g, ...), not a full load-profile-shape uncertainty.

## True MIFT ("UIFT") sizing option

Added 2026-09-28, same session, same audit -- this closes out the 3rd of
the audit's recommended top-3 items (the other two, TRT and Monte Carlo
uncertainty, are documented above). Engine only, **no WPF toggle yet**.

`simulation.run_hourly_simulation` and `sizing.size_field` both gained an
optional `gfunc_boundary_condition: str = "UBWT"` parameter. The default
("UBWT") reproduces every existing result exactly, byte for byte -- this
was a hard constraint, not just a preference, since the whole app's
existing sized designs must stay reproducible. Passing `"MIFT"` sizes
against the true mixed-inlet-fluid-temperature response (Cimmino 2015/2019
-- the audit's "UIFT" pick) instead of the UBWT-plus-flat-R_b*-offset
approximation every version of this engine used before today.

**What actually differs, precisely** (the first draft of this feature got
this wrong and was corrected before shipping -- see below): UBWT convolves
a g-function built on the idealized assumption that every borehole sits at
the same wall temperature, then adds a flat R_b* offset (from
`pipes.effective_resistance`, computed for ONE representative borehole) to
reach fluid temperature. MIFT instead builds the field's actual pipe
NETWORK (`networks.build_network`) and solves for how each borehole's heat
extraction really differs given a shared inlet fluid temperature and real
inter-borehole thermal interference. Per pygfunction's own solver
internals, MIFT's g-function is ALSO an "effective borehole wall
temperature" response needing an R_b*-shaped offset --
`networks.effective_network_resistance` -- but for an all-parallel field of
identical boreholes that network resistance turns out to be **numerically
identical** to the single-borehole R_b* UBWT already uses (verified
directly to full float precision; pinned as a regression test). So the
offset step is NOT where the two disagree -- the entire difference is in
the g-function's own treatment of field-wide borehole coupling. MIFT is
scoped to all-parallel connectivity here (matching every other caller of
these functions); series/mixed wiring exists in `networks.py` but isn't
threaded through this option.

**Validation** (`tests/unit/test_mift_boundary_condition.py`, 6 tests):
confirms the default-unchanged guarantee byte-for-byte; confirms the
R_b*-equals-network-resistance finding above; then demonstrates the actual
accuracy difference at two ends of a realistic range on the SAME load and
field family -- a single borehole or a modest 12-borehole field at a normal
0.30 kg/s per-borehole flow agree closely (max fluid-temperature difference
under 0.1 C over an aggressive stress-test load), while a larger 36-borehole
field at a low 0.03 kg/s per-borehole flow diverge measurably (max
difference over 0.5 C, mean over 0.1 C) -- exactly the "more boreholes,
lower flow" regime where UBWT's uniform-wall-temperature idealization
should be expected to break down, and where MIFT's own literature (Cimmino)
motivates its use. **Error before/after**: before, every sized result used
only the UBWT approximation (Phase 1 audit item 5: Partial, this was the
specific gap). After: a caller who needs the more rigorous UIFT/MIFT
response for a low-flow or large-field design can opt into it, at
meaningfully higher per-call cost (rebuilding the pipe network, not just one
borehole's resistance) -- consistent with this project's stated
accuracy-over-speed priority.

**A real mistake caught before shipping**: the first version of this
feature's docstring claimed the network resistance "can differ meaningfully"
from R_b* for multi-borehole fields, reasoning that network-level flow
splitting should matter. Direct empirical testing (several field sizes and
flow rates) showed they are IDENTICAL for the all-parallel-identical case --
the claim was wrong, caught before committing it to the docstring or tests,
and corrected to attribute the real difference to the g-function's
coupling treatment instead. Worth remembering: the mathematically "obvious"
place a difference should live is not always where it actually lives --
check empirically before writing the explanation into the code.

**Not done**: series/mixed network connectivity is not exposed through this
option (see "Field realism" in the Phase 1/2 audit for that separate,
still-open item). `hybrid.py`'s tower functions DID later gain
`gfunc_boundary_condition` passthrough (same day, see "WPF toggles for TRT /
Monte Carlo / MIFT" below) so the WPF toggle composes correctly with every
tower strategy, not just peak-shaving.

## WPF toggles for TRT / Monte Carlo / MIFT

Added 2026-09-28, later the same session (user: "wire up WPF toggles for the
three built features"). All three of that day's engine-only additions now
have a `HC.GeoBore` screen; `hybrid.py`'s tower functions also gained the
same `gfunc_boundary_condition` passthrough `simulation.py`/`sizing.py`
already had, purely so the new MIFT toggle composes with every existing
tower strategy instead of only peak-shaving.

**MIFT toggle** -- new shared-panel "g-function accuracy" group
(`SimSizeBoundaryConditionCombo`: "UBWT + R_b*" / "MIFT"), enabled only on
Simulation and Sizing (same gating as the existing Hybrid cooling tower
group -- not wired into Area Sizing yet). Feeds `gfunc_boundary_condition`
into every `run_hourly_simulation`/`size_field` call those two tabs make,
including every tower-strategy branch. Verified live at two ends of the
regime the accuracy difference actually lives in: a 12-borehole field at a
normal 0.30 kg/s flow gave visually identical results either way (as
expected, and as the Python-level test already predicted); a 36-borehole
field at a low 0.03 kg/s flow showed a real, visible difference (7.9-15.3 C
under MIFT vs. 8.1-15.1 C under UBWT).

**Monte Carlo panel** -- new "Uncertainty (Monte Carlo)" group on the Sizing
tab. `base_kwargs` for `size_field_monte_carlo` is exactly what the tab's
own deterministic `Size Field` call already builds (`BuildSizeFieldArgs`),
reused unmodified -- the whole point of the Python side's `base_kwargs`
design (see "Monte Carlo sizing uncertainty" above) is that a caller
shouldn't have to re-derive it. Supports varying k_s and/or T_g, each
normal-distributed; the "mean" field is a read-only mirror of the shared
panel's own Conductivity/Ground-temperature boxes (never a second, possibly
inconsistent value) and only the std dev is entered. Checking the box
replaces the deterministic result panel with the P50/P90 numbers and a new
histogram chart (`Charts.Histogram`, a `RectangleBarSeries` over equal-width
bins -- OxyPlot 2.2 has no native histogram type). **Scope decision**: MC
mode only wraps the plain (no-tower) `size_field` call -- there is no Python
equivalent of "Monte Carlo sizing with a deadband/wet-bulb tower," so the
UI blocks the combination with a clear message (`Size_McTowerHintText`)
rather than silently ignoring the tower or crashing. GHEtool cross-check is
also skipped in MC mode for the same reason (no MC-aware GHEtool path
exists). Live-verified with a deliberately small sample count (8, vs. the
200 default -- `size_field` costs several seconds per call, so a full run
is a multi-minute operation by design, matching this project's
accuracy-over-speed priority) -- P50/P90/mean/stddev all reported correctly,
histogram rendered, and unchecking the box correctly restored the normal
deterministic view on the next run.

**TRT Analysis tab** -- new 5th tab. Reuses the shared panel's Borehole
depth H / Buried depth D / Borehole radius r_b / Ground temperature T_g
(all correctly gated: enabled where TRT needs them, the load-profile grid /
tower group / MIFT toggle / N1-N2 field-layout boxes disabled where it
doesn't, via a new `TrtTabIndex` case in `UpdateSharedInputAvailability`).
**Deliberately scoped to the joint-fit mode only** -- `R_b_mK_W` (the
"hold R_b fixed from known pipe/grout specs" mode `trt.py` also supports)
is not exposed in this first pass, so the tab needs no pipe/fluid inputs at
all, which is why its own hint text says so explicitly rather than leaving
that ambiguous. A new `TrtRow`/`_trtRows`-backed `DataGrid` holds the
log data (Hour/Q kW/T_f measured C), seeded with 48 hours of clearly-marked
placeholder data (a simple log-time curve, not a real TRT) so the tab opens
in a runnable state rather than an empty grid -- the hint text tells the
user to replace it before relying on the result. Results include a new
`Charts.TrtFit` chart (measured vs. predicted fluid temperature, with a
vertical marker at the excluded-early-data cutoff) built the same way
`TemperatureHistory`/`MonthlyLoad` already are. **Not done**: no "apply
fitted k_s/R_b back to the shared panel" button yet (Area Sizing has this
kind of write-back for its own solved outputs; TRT's result text says to do
it manually for now); no held-fixed-R_b mode; no DTRT.

**Verification, same discipline as every other WPF turn this project has
used**: `dotnet build` clean (0 errors) via the SDK path in
[[machine-dotnet-toolchain]]; the built DLL run directly (not the apphost
`.exe`) and rechecked after a delay for a silent startup crash -- none;
`dotnet test` 40/40 passing (was 37 -- 3 new integration tests through the
real subprocess bridge: TRT recovery, Monte Carlo percentiles, and the
MIFT/UBWT round-trip, one each mirroring the corresponding Python test);
live UI-Automation pass through all three features with screenshots,
including the MIFT-agrees-then-diverges pair, the Monte Carlo panel
expand/run/collapse-back-to-deterministic cycle, and the TRT tab's fit
chart -- all matching what the Python-level tests already predicted, not
just "ran without an exception."

## Four more Phase 2 audit items, 2026-09-28, later the same session

Engine + tests only (per the user's own "build all one by one" answer to
"which remaining Phase 2 item next" -- no WPF screens for these four yet,
a deliberate scope decision, not an oversight).

**Multipole order + internal (R_a) resistance reporting** (audit item 7,
partial/missing pieces): `pipes.build_pipe`/`pipes.effective_resistance`
gained `multipole_order: int = 2` (pygfunction's own `J` parameter,
previously hardcoded to its default with no way to raise it). `effective_
resistance` also now reports `R_delta_mK_W` (the full delta-circuit
resistance matrix, via pygfunction's `thermal_resistances()`) and
`R_a_mK_W` (the single scalar most TRT/GSHP literature calls "R_a" or
R_12 -- `R_delta[0][1]`, meaningful only for a 2-leg single U-tube; `None`
for anything else). Both `None` for coaxial, whose internal resistance
network is radial rather than a multipole leg network.

**A real, previously-uncaught bug found while wiring this**: `build_pipe`'s
coaxial branch passed `r_in`/`r_out` to `gt.pipes.Coaxial` as plain Python
tuples; pygfunction's `Coaxial.__init__` calls `r_out.argmin()`/`.argmax()`
to tell the inner pipe from the outer one, which a tuple has no such method
for -- `AttributeError: 'tuple' object has no attribute 'argmin'` on ANY
coaxial construction. No prior test in this repo had ever exercised
`build_pipe`/`effective_resistance` with a coaxial config (the one existing
coaxial test only exercises `suggest_flow_rate_kg_s`, which raises before
reaching `build_pipe`) -- caught the moment a new test tried it. Fixed by
passing `np.array([...])` instead. 15 tests in
`tests/unit/test_pipes_flow_and_double_u.py` (10 pre-existing + 5 new),
covering the default-J=2 regression, R_a/R_delta shape and symmetry for
single and double U-tube, coaxial's `None`s, and J=0 vs J=2/3 convergence.

**Long-term ground temperature drift** (audit item 4, previously Missing):
`simulation.run_hourly_simulation`/`sizing.size_field` gained
`T_g_drift_C_per_year: float = 0.0`. 0.0 (default) reproduces every
existing result byte-for-byte. Modeled as a simple additive linear trend on
the far-field reference temperature, decoupled from the borehole's own
Q-driven response -- a documented simplification (a fully rigorous
treatment would need a second surface-boundary Green's function
superimposed on the first), reasonable for a slow, spatially broad forcing
like decadal climate/UHI warming. Validated against the exact analytical
prediction (drift_rate x elapsed_years), not just "warmer with drift on" --
see `tests/unit/test_ground_drift.py`. Threaded through all 5 of `hybrid.
py`'s tower functions too, same passthrough pattern as `gfunc_boundary_
condition`; the deadband controller's own setpoint is deliberately NOT
itself drifted (it represents warming from the ORIGINAL undisturbed state,
so a tower engaging more as climate drift alone pushes the field warmer
over the design life is the intended behavior, not a bug).

**Field realism: mixed depths + series/mixed networks wired into sizing**
(audit items 10a/10b, previously Partial -- the underlying primitives
existed but had no route into the actual sizing/simulation loop):
- `sizing.build_field_at_depth`/`size_field` gained `depth_scale: Optional[
  list[float]]` -- one relative depth multiplier per borehole (same order
  the layout builder produces). `size_field` still bisects on a single
  scalar H (its well-tested convergence behavior, unchanged), but each
  trial's field has per-borehole depth `H * depth_scale[i]` -- e.g.
  boreholes forced shallower near a property line, kept at a fixed
  proportion of whatever depth the rest of the field needs. Verified the
  resulting `total_length_m` matches `H_m * sum(depth_scale)` exactly, and
  that constraining some boreholes shallower correctly makes the bisection
  solve a LARGER nominal H to compensate.
- `simulation.run_hourly_simulation`/`sizing.size_field` gained
  `bore_connectivity: Optional[list[int]]`, threaded into the MIFT path's
  `networks.evaluate`/`networks.effective_network_resistance` calls (only
  meaningful under `gfunc_boundary_condition="MIFT"` -- given under UBWT,
  raises rather than silently doing nothing, since UBWT's g-function has no
  concept of hydraulic connectivity at all). `m_flow_borehole` generalizes
  to "flow per parallel CIRCUIT at the network inlet" -- identical to
  flow-per-borehole for the default all-parallel case, and to
  flow-per-STRING for series/mixed connectivity, documented explicitly
  since the parameter name becomes a little less literal there. Verified
  series connectivity produces a genuinely different R_b*/T_f than parallel
  at the same per-circuit flow (not silently ignored). 8 tests in
  `tests/unit/test_field_realism.py`.

**Heat pump coupling: COP(EFT) and compressor energy** (audit item 9,
previously Missing): new `heat_pump.apply_heat_pump(building_load_W,
eft_C, cop_heating_curve, cop_cooling_curve)` converts a BUILDING-side load
into the GROUND-side load `simulation.py`/`sizing.py` consume, via
piecewise-linear COP(EFT) curves (the heat pump's own published
performance points, clamped rather than extrapolated outside the tested
range) instead of one fixed COP -- and reports the compressor's own
electrical energy, needed for a real energy/cost comparison rather than
just a ground-temperature-stability proxy.

**Architectural decision worth explaining**: COP(EFT) genuinely depends on
the entering fluid temperature, which is an OUTPUT of running the
simulation with the very ground load this function computes -- the same
class of circular-dependency problem `hybrid.deadband_tower_controller`
solves by running INSIDE `run_hourly_simulation`'s hourly loop via the
`tower_control` hook. Building an equivalent in-loop hook for heat-pump
coupling would be a materially bigger change (a new hook type threaded
through the whole simulation/sizing/hybrid stack) than this pass makes.
Instead, `apply_heat_pump` takes `eft_C` as a plain input array and stays
fully stateless, supporting two honest workflows documented in the
module's own docstring: a one-pass estimate (`eft_C = [T_g] * n_hours`,
reasonable before the field is even sized) or iterative refinement (run
`apply_heat_pump` -> `run_hourly_simulation` -> feed the returned `T_f_C`
back as the new `eft_C` -> repeat, typically converging in 2-3 passes) --
both compose with the EXISTING `run_hourly_simulation`/`size_field` API
with zero new hook machinery. Verified the iterative workflow actually
converges (each pass's mean-T_f change shrinking, not growing or
oscillating), not just that it runs. 8 tests in
`tests/unit/test_heat_pump.py`, including the core energy-balance
identities both directions rest on (ground_load + electrical_power ==
building_load for heating; -ground_load == building_load_magnitude +
electrical_power for cooling).

**Not done for any of these four**: no WPF screen/toggle; heat pump
coupling's in-loop, fully self-consistent EFT-COP coupling (the
`tower_control`-style hook variant, not attempted this pass); `depth_scale`/
`bore_connectivity` are not threaded through `hybrid.py`'s tower functions
(narrower scope than the drift/MIFT passthroughs, a deliberate decision
given the added complexity of combining per-hour tower dispatch with
non-uniform-field or series-network geometry).

## Validation, layered ground, short-term capacitance, groundwater -- 2026-09-28, later the same session

User instruction: "Do all that next" (the 4 items still open from the Phase 1/2
audit after the four above), plus WPF screens for everything built today.
Engine + tests done first (this section); WPF screens follow in the next
section. Order chosen by risk, not by the order requested: validation first
(lowest risk, and produces machinery the others lean on), then layered ground
and short-term capacitance (both bounded, standard engineering
approximations), groundwater/Darcy last (the hardest, most novel physics --
see its own scope discussion below).

### Independent analytical validation (audit category 5: Validation & design risk)

`tests/unit/test_validation_analytical.py` (3 tests) -- every prior
validation test in this repo (`test_geothermal_report_examples.py`) pins
numbers pygfunction's OWN documentation already printed; useful, but not
independent of the library under test. This file instead derives the
classical infinite line source solution (Ingersoll & Plass, 1948) from its
own textbook formula (`g_ILS(t) = 0.5 * E1(r_b^2/(4*alpha*t))`, via
`scipy.special.exp1`) and checks `simulation.run_hourly_simulation`'s
single-borehole response against it in BOTH directions: agreement within
2.5% at moderate times (Fourier number > 5, t well under the borehole's own
steady-state time `ts = H^2/(9*alpha)`), and genuine, growing divergence
(>3% by 20 years) at long times, where the finite line source's own
finite-length end effects correctly make it level off while the ILS keeps
growing as ln(t) forever. Checking both directions (not just "some
agreement") is what makes this a real test of the finite-length physics
rather than a coincidence -- confirmed directly: the moderate-time error really
is smaller than the long-time error, not the same number written twice.

### Layered ground (audit category 1, item "layered ground λ(z)/T(z)": previously Missing)

`ground.weighted_average_ground_properties(layers, D, H)`. pygfunction's
g-function solvers all take one scalar `alpha` -- there is no layered-medium
finite line source in the library (grepped the installed 2.3.1 source: zero
hits for "layer" anywhere in it), and building one is a fundamentally
different Green's function, not an extra parameter, far beyond this pass's
scope. Implements the standard engineering approximation instead: a
THICKNESS-WEIGHTED ARITHMETIC average of each stratum's k_s/rho_cp (and,
optionally, undisturbed T_g) over exactly the borehole's own buried span
`[D, D+H]`, trimming partial layers at both ends. Arithmetic (not harmonic)
weighting is the physically correct choice here specifically because this
package's own g-function/load-aggregation machinery already treats heat
transfer as uniform per-unit-BOREHOLE-LENGTH (the same idealization
Eskilson's g-function itself rests on) -- layers are structurally in
PARALLEL along the borehole's axis (each conducts radially outward
independently at its own depth), not in series the way conduction through a
wall's stacked materials would be, so harmonic weighting would be the wrong
physical model here even though it is standard elsewhere.

A pure input-preparation function -- returns `k_s_eff`/`alpha_eff_m2_s`/
`rho_cp_eff_J_m3K`/`T_g_eff_C`, which plug directly into any existing
`simulation.run_hourly_simulation`/`sizing.size_field` call exactly like a
hand-picked scalar would. Zero changes needed anywhere else in the package.
Documented as an approximation, not a rigorous layered solution: it cannot
show a different response for, say, a thin high-conductivity seam sandwiched
between insulating layers -- only ever returns one bulk number. 9 tests in
`tests/unit/test_ground_layers.py`: the main one checks the weighted average
against a hand-computed expectation (not just "a number came out"), plus the
edge cases a real geological log actually produces (partial overlap at the
borehole's own top/bottom, gaps, overlaps, mixed T_g_C presence across
layers) and one end-to-end check that the effective properties really do
feed an ordinary simulation run unmodified.

### Short-term borehole thermal capacitance (audit category 2: Loads & thermal response, previously Missing)

`thermal_mass.borehole_thermal_capacitance` + a new optional
`borehole_capacitance_J_mK` parameter on `simulation.run_hourly_simulation`/
`sizing.size_field`. `pipes.effective_resistance`'s R_b* is QUASI-STEADY --
it relates fluid to borehole-wall temperature with no time lag, as if the
grout/pipe/fluid inside the borehole had zero thermal mass, when in reality
that mass takes real time (minutes to a couple hours) to warm up after a
load step. Standard "lumped capacitance" simplification of the borehole
cross-section (Bauer et al. 2011, *Thermal resistance and capacity models
for borehole heat exchangers*, describe the fuller multi-node version of
this idea; this is the coarser single-lump version of the same physics --
right order of magnitude and qualitative shape, not a detailed multi-node
internal temperature distribution).

`borehole_thermal_capacitance(config, r_b, rho_cp_grout_J_m3K,
rho_cp_pipe_J_m3K, fluid_str, fluid_percent, fluid_temperature_C)` computes
C_b (J/m.K) from real cross-sectional geometry (fluid/pipe-wall/grout areas,
derived the same way `pipes.build_pipe` already does for single/multiple/
independent-multiple U-tube and coaxial) times each material's volumetric
heat capacity -- grout/pipe values are REQUIRED inputs, not guessed (same
precedent as `trt.py`'s `rho_cp_J_m3K`), fluid's own is computed from
`fluids.py`'s existing pygfunction-backed density/specific-heat lookup
instead of asking for it separately. `borehole_capacitance_J_mK=None` (the
default) reproduces every existing simulation byte-for-byte; when given, the
fluid-to-wall offset relaxes toward its quasi-steady R_b* value with time
constant `tau_b = R_b* x C_b` via an exact first-order (RC) step response,
starting from zero lag at hour 0 (the borehole's internal mass starts at
rest) -- `T_b` itself is untouched either way, since it is specifically the
borehole's OWN internal mass, not the ground's (already correctly transient
through the g-function convolution), that this approximates.

9 tests in `tests/unit/test_thermal_mass.py`: geometry breakdown against a
hand calculation for both single U-tube and coaxial; `None` reproduces the
original result exactly; a load step's first-hour fluid temperature sits
STRICTLY BETWEEN the wall temperature and the no-lag prediction (proving a
partial, not full or zero, step response); convergence to the no-lag value
after 10 time constants (within 0.01 C); a larger capacitance produces a
demonstrably slower first-hour response. Live-computed example (single
U-tube, r_b=0.075 m, typical grout/pipe capacities): tau_b ~= 165 minutes --
comfortably under the 1-hour timestep for ordinary building-load
simulation, but real enough to matter for sub-hourly duty swings (a heat
pump cycling within an hour) or the first few hours of a TRT-style step
load, which is exactly the regime this was built for.

### Groundwater/Darcy advection (audit category 1, previously Missing) -- scope deliberately narrowed, read this before using it

`groundwater.py`. pygfunction 2.3.1 has ZERO moving-medium heat source of
any kind (grepped the installed source directly: no hits for "darcy",
"advection", or "moving" anywhere in it) -- every g-function it computes
assumes stagnant ground. A full moving FINITE line source (the transient
solution that would plug directly into `simulation.py`'s per-hour
convolution, alongside the existing stationary FLS) is a genuinely different
Green's function, not an extra parameter on the existing one, and rigorously
combining a transient moving-source solution with pygfunction's existing
load-aggregation scheme is an open question this module does not attempt --
substantially bigger than the other three items in this section.

**What was built instead**: the classical STEADY-STATE 2-D moving line
source (Diao, Li & Fang, 2004, *Improvement in modeling of heat transfer in
vertical ground heat exchangers*, HVAC&R Research 10(4); the same building
block underlies later transient treatments, e.g. Molina-Giraldo et al. 2011,
and is cited again in Piipponen et al. 2024 -- one of this project's own
stated literature-basis papers from the start of this audit) as a SCREENING
check run alongside, not blended into, the existing transient no-advection
simulation: "once groundwater flow reaches its own steady state around this
borehole, how much does that change things, and is my site's flow even fast
enough for it to matter." Estimating exactly when that steady state is
reached, and combining it with the transient conduction-only response, is
explicitly NOT attempted -- doing that credibly needs the fuller moving-FLS
treatment above, not a guessed blending rule.

**Checked, not just cited, before shipping**: the closed form `DeltaT(x,y) =
q'/(2*pi*k_s) * exp(u_T*x/(2*alpha)) * K0(u_T*r/(2*alpha))` was substituted
into its own governing PDE (`(Txx+Tyy) - (u_T/alpha)*Tx = 0`) and checked by
finite differences at several points during development -- residual at
machine/truncation precision (relative error ~1e-6), not just trusted from
the papers. Kept as an automated regression,
`test_groundwater.py::test_closed_form_satisfies_the_steady_advection_diffusion_pde`.

**A real mistake caught by the tests, not assumed away**: the first version
of `thermally_retarded_velocity`'s docstring claimed `u_T` is always SLOWER
than the Darcy velocity ("thermal retardation"). `test_groundwater.py::
test_thermally_retarded_velocity_scales_by_the_heat_capacity_ratio`, using
REAL water/saturated-formation volumetric heat capacities, showed the
opposite: u_T came out FASTER than u_darcy, since water's own volumetric
heat capacity (~4.18 MJ/m3.K) usually exceeds a saturated formation's bulk
value. Corrected before shipping -- same "check empirically before writing
the explanation into the code" lesson `gfunc_boundary_condition`'s R_b*
finding already taught this project once. A second, genuinely
counterintuitive finding survived the same scrutiny (derived, not guessed --
see `groundwater_steady_state_effect`'s own docstring for the one-line
derivative proof): the LOCAL steady-state temperature change at the borehole
wall gets SMALLER as Darcy velocity increases, not larger, because faster
flow removes heat more efficiently even though it also reaches that (lower)
steady value sooner -- confirmed directly,
`test_local_steady_temperature_change_shrinks_as_darcy_velocity_increases`.

7 tests total in `tests/unit/test_groundwater.py`. **Error before/after**:
before, this engine had no representation of groundwater at all (Phase 1
audit: Missing) -- a design in a high-flow aquifer setting would silently
use the same stagnant-ground physics as a dry site. After: a caller with a
real Darcy velocity (from a site investigation, not guessed) can get an
honest, math-verified order-of-magnitude screening check of whether
groundwater flow is likely to matter at their site, with an explicit,
documented boundary around what it does NOT yet do (no transient coupling,
no automatic pass/fail threshold).

**Not done for any of these three**: no WPF screen yet (see the next
section, "WPF for the seven Phase 2 engine items," for what that section
does and does not cover); `groundwater.py` is not wired into
`simulation.py`/`sizing.py` at all (deliberately -- see its scope discussion
above); `ground.py`'s effective properties are not automatically recomputed
inside `size_field`'s own bisection (the caller computes them once, up
front, same pattern as a TRT-fitted k_s already works).

## WPF for the seven Phase 2 engine items

Added 2026-09-28, later the same session, in direct response to the user's
"Do all that next" -- the 4 items above, plus WPF screens for everything
built today (which also included the 4 engine-only items from the prior
"Four more Phase 2 audit items" section: multipole order/R_a, ground drift,
field realism, heat pump). This section covers all 7 that got WPF; field
realism (`depth_scale`/`bore_connectivity`) is the one deliberately left out
-- see "Not done" at the end.

**A real gap found and fixed before any UI was built on top of it**:
`simulation.run_hourly_simulation`'s own internal call to `pipes.
effective_resistance` never forwarded `multipole_order` -- it always used
the hardcoded default (J=2), even after that parameter was added to `pipes.
build_pipe`/`effective_resistance` themselves in the earlier "multipole
order" work. Raising J from the standalone `effective_borehole_resistance`/
`build_pipe` QA calls had **zero effect** on an actual Simulation/Sizing run's
own R_b* -- exactly the kind of misleading-if-shipped-as-is UI gap this
project's own precedent (the tower-with-no-benefit fix, the coaxial pipe
bug) says to catch before building on top of it, not after. Fixed by adding
`multipole_order: int = 2` to `simulation.run_hourly_simulation` and
`sizing.size_field` (threaded to both the UBWT `effective_resistance` call
and the MIFT `networks.evaluate`/`effective_network_resistance` calls, which
needed the same fix -- `networks.build_network` never forwarded pygfunction's
own `J` parameter to `Network.from_static_params` either), and through all 5
of `hybrid.py`'s tower functions for consistency with the existing
`gfunc_boundary_condition`/`T_g_drift_C_per_year` passthrough pattern.
`borehole_capacitance_J_mK` (new, see below) got the same 5-function
passthrough treatment at the same time -- unlike `depth_scale`/
`bore_connectivity`, it only ever changes the fluid-side offset, never `T_b`
(what every tower controller actually reads), so there is no interaction
risk that would justify leaving it out the way the field-realism params were.
2 (default) and `None` (default) both reproduce every existing result
byte-for-byte -- verified via the full 348-test Python suite before any XAML
was touched.

**"Advanced physics (optional)" group** (shared panel, gated to Simulation/
Sizing only, same pattern as "g-function accuracy"): a "Ground temp. drift
(C/year)" box (`T_g_drift_C_per_year`, default 0.0) and a "Multipole order J"
box (default 2) feed straight into `commonSimArgs`/`BuildSizeFieldArgs`
(the Simulation/Sizing tabs' own shared-args builders -- see "WPF toggles for
TRT / Monte Carlo / MIFT" above for why those exist), so Monte Carlo sizing
picks them up automatically too (its `base_kwargs` is `BuildSizeFieldArgs`'s
own output, unmodified). A "Short-term borehole thermal capacitance"
checkbox reveals Grout/Pipe rho.cp inputs (both required, no default value
guessed -- same precedent as the vendor catalog's own required-selection
rule); when checked, `BuildBoreholeCapacitanceJmKAsync` calls the new
`borehole_thermal_capacitance` command (using the shared panel's own pipe
config/r_b/fluid) and passes the resulting `C_b_J_mK` as
`borehole_capacitance_J_mK`; unchecked, `null` is sent, reproducing the
original behavior exactly.

**"Groundwater screening (optional, standalone)" group** (shared panel,
NOT tab-gated -- it is an independent calculator, not part of Simulation/
Sizing's own result): Darcy velocity / heat rate q' / water and saturated-
formation rho.cp inputs, a "Check groundwater effect" button that calls
`groundwater_steady_state_effect` and prints the Peclet number and downstream/
upstream steady-state delta-T directly -- reads r_b/k_s/alpha from the
shared "Field layout"/"Ground properties" groups above it, exactly as
documented on the panel itself, so there's no duplicate/possibly-inconsistent
copy of those three numbers. Deliberately a one-shot calculator with no
chart -- matches the module's own "screening check, not simulation feature"
scope.

**"Layered ground (optional)" group** (shared panel, always available): a
`GroundLayerGrid` (Top/Bottom/k_s/rho.cp/T_g per row, seeded with the same
2-layer example `test_ground_layers.py` uses) plus its own D/H fields (NOT
the shared panel's DepthBox/BuriedDepthBox, since Sizing/Area Sizing solve
for H -- the hint text tells the user to enter their best estimate, e.g.
H_max, there instead) and a "Compute effective properties" button. Calls
`weighted_average_ground_properties` and WRITES the result into the shared
panel's own Alpha/Conductivity/[Ground temp, if every layer had one] boxes
-- a one-shot write-back, same pattern as Area Sizing's existing "apply
solved output" buttons, not a live-linked model that recomputes automatically
whenever a layer or D/H changes.

**"Heat pump coupling (optional)" group** (Simulation tab only -- see "Not
done" below for why not Sizing too): a checkbox plus two small COP-curve
grids (Heating/Cooling, EFT/COP pairs, seeded with the same example curves
`test_heat_pump.py` uses). When checked, `SimulateButton_Click` reinterprets
the shared monthly-load-profile grid as the BUILDING load instead of the
ground load, and runs the exact 3-pass iterative-refinement workflow
`heat_pump.py`'s own docstring documents and `test_heat_pump.py::
test_iterative_refinement_with_run_hourly_simulation_converges` proves
converges: seed EFT = T_g, call `apply_heat_pump` -> `run_hourly_simulation`
-> feed the returned `T_f_C` back as the next pass's EFT, repeat. The final
pass's ground load and heat-pump summary (mean COP, compressor electrical
energy) both feed the existing chart/summary-text code paths unchanged.
Blocked (a clear `InvalidOperationException`, not a silent no-op or crash)
from combining with the hybrid cooling tower in the same run -- no
Python-side support exists for running an in-loop tower controller and the
heat-pump refinement loop together, and building that is a materially
bigger change than this pass makes (same class of scope decision as MC
sizing blocking the tower combination, "WPF toggles..." above).

**Verification**: `dotnet build` clean (0 errors); `dotnet test` 45/45
passing (was 40 -- 5 new integration tests through the real subprocess
bridge, one each for the drift/multipole-order round-trip,
`weighted_average_ground_properties`, `borehole_thermal_capacitance`,
`groundwater_steady_state_effect`, and `apply_heat_pump`'s energy-balance
identity, mirroring this session's Python-level tests for the same
functions); full Python suite still at 348/348 after the `simulation.py`/
`sizing.py`/`networks.py`/`hybrid.py` passthrough edits (0 regressions).

Live UI-Automation pass through all four new panels (the built DLL run
directly, driven via `System.Windows.Automation`, same discipline as every
prior WPF turn this project has used -- PrintWindow screenshots turned out
not to capture this machine's WPF content this time, so this pass leaned on
reading live control state/values instead, which is at least as strong a
check): tab-gating confirmed correct on launch (`AdvancedPhysicsGroup`
disabled on g-Function, enabled on Simulation); both new checkboxes' reveal
behavior confirmed (`HeatPumpEnabledCheckBox` -> both COP-curve grids enable,
`CapacitanceEnabledCheckBox` -> both rho.cp boxes enable); the groundwater
"Check" button clicked live and returned real engine output (Peclet 0.068,
downstream -5.98 C, upstream -5.22 C -- matching the "downstream colder,
Pe<<1" physics this session derived and tested at the Python level); the
layered-ground "Compute effective properties" button clicked live and wrote
back k_s=2.820, alpha=1.144e-6, T_g=12.64 into the shared panel's own
boxes -- an EXACT match to the hand-computed values from `test_ground_
layers.py`'s own fixture; and a full live "Run Simulation" with heat pump
coupling checked (Custom pipe/grout entered, MPG fluid, 3-year period for a
faster check) completed with no error and printed "Heat pump (converged
after 3 refinement passes): mean COP 4.55, 4832 kWh/yr avg compressor
electrical energy" -- the complete iterative-refinement workflow, exercised
end to end through the real subprocess, not just unit-tested in isolation.

**Not done**: `depth_scale`/`bore_connectivity` (field realism) got no WPF
this round -- both need a genuine per-borehole array editor tied to the
current N1xN2 layout (not a single scalar box), a materially bigger UI
surface than the other six items, and a deliberate scope cut given
everything else in this batch; heat pump coupling is Simulation-tab only --
wiring it into Sizing's own bisection would need the EFT refinement loop to
run INSIDE every depth trial (a nested iteration, not the single top-level
loop Simulation's fixed-field case allows), a bigger change left for a
follow-up; no chart comparing building load vs. ground load when heat pump
coupling is on (the load chart still shows ground load only, same as
before); groundwater screening's Peclet-number interpretation text uses a
single Pe<0.1 cutoff for its "little effect" wording -- a readability
shortcut, not a claimed precision the underlying physics doesn't have (see
`groundwater_steady_state_effect`'s own docstring: no hard pass/fail
threshold is asserted at the engine level).
