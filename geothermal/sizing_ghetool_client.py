"""Safe-to-import client for GHEtool-based sizing: runs it in a subprocess
via geothermal.ghetool_worker so GHEtool's process-wide pygfunction
monkey-patch (see ghetool_worker's docstring) never touches this process.

This module itself must never import GHEtool or geothermal.sizing_ghetool.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


def size_field_isolated(**kwargs: Any) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "geothermal.ghetool_worker"],
        input=json.dumps(kwargs), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ghetool_worker exited {proc.returncode}: {proc.stderr[-4000:]}")
    result = json.loads(proc.stdout)
    if "error" in result and len(result) == 1:
        raise RuntimeError(f"GHEtool sizing failed: {result['error']}")
    return result
