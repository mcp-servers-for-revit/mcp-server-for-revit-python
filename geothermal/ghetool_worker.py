"""Isolated subprocess entry point for geothermal.sizing_ghetool.

GHEtool monkey-patches pygfunction's solver internals at import time
(GHEtool.VariableClasses.Cylindrical_correction.update_pygfunction(), invoked
on import -- see that file's own docstring: "a temporary solution, until
issue #44 of pygfunction is solved") by overwriting gt.solvers._BaseSolver
.solve/__init__ and gt.solvers.Equivalent.thermal_response_factors
process-wide. That corrupts *unrelated* direct pygfunction calls made
afterward in the same process -- observed live: a plain
gt.gfunction.gFunction.from_static_params(..., boundary_condition="MIFT", ...)
call that works before `import GHEtool`, raises
TypeError: unsupported operand type(s) for *: 'NoneType' and 'NoneType'
deep inside pygfunction.networks after it.

So: never import geothermal.sizing_ghetool (or GHEtool itself) in the same
process as geothermal.gfunctions / geothermal.simulation. This worker is the
only supported way to run GHEtool-based sizing; call it via
geothermal.sizing_ghetool_client.size_field_isolated(), which spawns it as a
subprocess and never imports GHEtool itself.

Protocol: JSON kwargs for sizing_ghetool.size_field on stdin; JSON result
(or {"error": "..."}) on stdout.
"""
import json
import sys


def main() -> None:
    kwargs = json.loads(sys.stdin.read())
    from geothermal import sizing_ghetool  # imported only inside this isolated process
    try:
        result = sizing_ghetool.size_field(**kwargs)
    except Exception as exc:
        json.dump({"error": f"{type(exc).__name__}: {exc}"}, sys.stdout)
        return
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
