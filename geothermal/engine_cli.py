"""Unified JSON-over-stdio entry point for the geothermal engine.

For the WPF shell (or anything else) to call as a subprocess per request --
one process per call, matching geothermal/ghetool_worker.py's pattern, so a
crashed calculation never takes down a long-running server and GHEtool-backed
commands stay isolated (see geothermal/README.md).

Protocol: a single JSON object on stdin, the whole stream (not
line-delimited): {"command": "<name>", "args": {...}}. A single JSON object
on stdout: {"result": ...} on success, {"error": "...", "traceback": "..."}
on failure. Exit code is always 0 when a response was written -- callers
check for the "error" key, not the process exit code.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

COMMANDS: dict[str, Callable[[dict[str, Any]], Any]] = {}


def command(name: str):
    def decorator(fn: Callable[[dict[str, Any]], Any]):
        COMMANDS[name] = fn
        return fn
    return decorator


@command("list_commands")
def _list_commands(args: dict[str, Any]) -> list[str]:
    return sorted(COMMANDS)


# ---- fields ----------------------------------------------------------

_FIELD_LAYOUT_COMMANDS = {
    "build_rectangle_field": "rectangle_field",
    "build_staggered_rectangle_field": "staggered_rectangle_field",
    "build_dense_rectangle_field": "dense_rectangle_field",
    "build_box_shaped_field": "box_shaped_field",
    "build_U_shaped_field": "U_shaped_field",
    "build_L_shaped_field": "L_shaped_field",
    "build_circle_field": "circle_field",
    "build_custom_field": "custom_field",
}


def _make_field_command(fn_name: str):
    def handler(args: dict[str, Any]) -> Any:
        from geothermal import fields
        return getattr(fields, fn_name)(**args)
    return handler


for _cmd_name, _fn_name in _FIELD_LAYOUT_COMMANDS.items():
    COMMANDS[_cmd_name] = _make_field_command(_fn_name)


@command("field_from_file")
def _field_from_file(args: dict[str, Any]) -> Any:
    from geothermal import fields
    return fields.field_from_file(**args)


@command("combine_fields")
def _combine_fields(args: dict[str, Any]) -> Any:
    from geothermal import fields
    return fields.combine_fields(*args["fields"])


@command("field_summary")
def _field_summary(args: dict[str, Any]) -> Any:
    from geothermal import fields
    return fields.field_summary(args["field"])


@command("characteristic_time")
def _characteristic_time(args: dict[str, Any]) -> Any:
    from geothermal import fields
    return fields.characteristic_time(args["field"], args["alpha"])


# ---- fluids ------------------------------------------------------------

@command("fluid_properties")
def _fluid_properties(args: dict[str, Any]) -> Any:
    from geothermal import fluids
    return fluids.properties(**args)


# ---- pipes ---------------------------------------------------------------

@command("effective_borehole_resistance")
def _effective_borehole_resistance(args: dict[str, Any]) -> Any:
    from geothermal import pipes
    return pipes.effective_resistance(**args)


@command("suggest_flow_rate")
def _suggest_flow_rate(args: dict[str, Any]) -> Any:
    from geothermal import pipes
    return pipes.suggest_flow_rate_kg_s(**args)


# ---- vendor catalogs -------------------------------------------------

@command("list_pipes")
def _list_pipes(args: dict[str, Any]) -> Any:
    from geothermal import pipe_catalog
    return pipe_catalog.list_pipes(**args)


@command("list_pipe_manufacturers")
def _list_pipe_manufacturers(args: dict[str, Any]) -> Any:
    from geothermal import pipe_catalog
    return pipe_catalog.list_pipe_manufacturers()


@command("list_grouts")
def _list_grouts(args: dict[str, Any]) -> Any:
    from geothermal import pipe_catalog
    return pipe_catalog.list_grouts(**args)


@command("list_grout_manufacturers")
def _list_grout_manufacturers(args: dict[str, Any]) -> Any:
    from geothermal import pipe_catalog
    return pipe_catalog.list_grout_manufacturers()


# ---- g-functions -----------------------------------------------------

@command("time_grid")
def _time_grid(args: dict[str, Any]) -> Any:
    from geothermal import gfunctions
    return gfunctions.time_grid(**args)


@command("evaluate_gfunction")
def _evaluate_gfunction(args: dict[str, Any]) -> Any:
    from geothermal import gfunctions
    return gfunctions.evaluate(**args)


@command("evaluate_mift_gfunction")
def _evaluate_mift_gfunction(args: dict[str, Any]) -> Any:
    from geothermal import gfunctions
    return gfunctions.evaluate_mift(**args)


# ---- networks ------------------------------------------------------------

@command("network_gfunction")
def _network_gfunction(args: dict[str, Any]) -> Any:
    from geothermal import networks
    return networks.evaluate(**args)


@command("network_variable_flow_gfunction")
def _network_variable_flow_gfunction(args: dict[str, Any]) -> Any:
    from geothermal import networks
    return networks.evaluate_variable_flow(**args)


@command("network_effective_resistance")
def _network_effective_resistance(args: dict[str, Any]) -> Any:
    from geothermal import networks
    return networks.effective_network_resistance(**args)


# ---- simulation ------------------------------------------------------

@command("run_hourly_simulation")
def _run_hourly_simulation(args: dict[str, Any]) -> Any:
    from geothermal import simulation
    return simulation.run_hourly_simulation(**args)


# ---- sizing ------------------------------------------------------------

@command("synthesize_hourly_load")
def _synthesize_hourly_load(args: dict[str, Any]) -> Any:
    from geothermal import sizing
    return sizing.synthesize_hourly_load(**args)


@command("field_layout_from_area")
def _field_layout_from_area(args: dict[str, Any]) -> Any:
    from geothermal import sizing
    return sizing.field_layout_from_area(**args)


@command("synthesize_peak_only_load")
def _synthesize_peak_only_load(args: dict[str, Any]) -> Any:
    from geothermal import sizing
    return sizing.synthesize_peak_only_load(**args)


@command("size_field")
def _size_field(args: dict[str, Any]) -> Any:
    from geothermal import sizing
    return sizing.size_field(**args)


@command("size_field_ghetool")
def _size_field_ghetool(args: dict[str, Any]) -> Any:
    # sizing_ghetool_client spawns its own isolated subprocess; engine_cli.py
    # itself never imports GHEtool (see geothermal/README.md).
    from geothermal import sizing_ghetool_client
    return sizing_ghetool_client.size_field_isolated(**args)


# ---- hybrid (supplemental cooling tower) ------------------------------

@command("apply_cooling_tower")
def _apply_cooling_tower(args: dict[str, Any]) -> Any:
    from geothermal import hybrid
    return hybrid.apply_cooling_tower(**args)


@command("run_hourly_simulation_with_deadband_tower")
def _run_hourly_simulation_with_deadband_tower(args: dict[str, Any]) -> Any:
    from geothermal import hybrid
    return hybrid.run_hourly_simulation_with_deadband_tower(**args)


@command("size_field_with_deadband_tower")
def _size_field_with_deadband_tower(args: dict[str, Any]) -> Any:
    from geothermal import hybrid
    return hybrid.size_field_with_deadband_tower(**args)


@command("minimum_tower_capacity")
def _minimum_tower_capacity(args: dict[str, Any]) -> Any:
    from geothermal import hybrid
    return hybrid.minimum_tower_capacity(**args)


@command("minimum_deadband_tower_capacity")
def _minimum_deadband_tower_capacity(args: dict[str, Any]) -> Any:
    from geothermal import hybrid
    return hybrid.minimum_deadband_tower_capacity(**args)


def main() -> None:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
        command_name = request["command"]
        args = request.get("args", {})
    except Exception as exc:
        json.dump({"error": f"invalid request: {type(exc).__name__}: {exc}"}, sys.stdout)
        return

    handler = COMMANDS.get(command_name)
    if handler is None:
        json.dump({"error": f"unknown command {command_name!r}; see list_commands"}, sys.stdout)
        return

    try:
        result = handler(args)
    except Exception as exc:
        json.dump({"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}, sys.stdout)
        return

    json.dump({"result": result}, sys.stdout)


if __name__ == "__main__":
    main()
