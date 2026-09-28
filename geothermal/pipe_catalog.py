"""Vendor pipe and grout catalogs, so field data stays sourced to a real
datasheet instead of hand-typed numbers going stale. Each manufacturer is
one JSON file in geothermal/data/ (<manufacturer>_pipes.json or
_grouts.json), with a top-level "manufacturer" name and a "_source"
citation. Add another manufacturer by dropping in a similarly-shaped JSON
file and listing it in _PIPE_FILES/_GROUT_FILES below.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent / "data"

_PIPE_FILES = ["rehau_pipes.json", "gerodur_pipes.json", "muovitech_pipes.json", "pipelife_pipes.json"]
_GROUT_FILES = ["rehau_grouts.json", "muovitech_grouts.json", "fischer_grouts.json"]


def _load(filename: str) -> dict[str, Any]:
    with open(_DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def _load_entries(files: list[str], entries_key: str) -> list[dict[str, Any]]:
    entries = []
    for filename in files:
        data = _load(filename)
        for entry in data[entries_key]:
            tagged = dict(entry)
            tagged.setdefault("manufacturer", data["manufacturer"])
            entries.append(tagged)
    return entries


def list_pipes(manufacturer: str | None = None) -> list[dict[str, Any]]:
    """Each entry: key, manufacturer, name, d_mm, s_mm, r_in_m, r_out_m, k_p_W_mK."""
    pipes = _load_entries(_PIPE_FILES, "pipes")
    if manufacturer is not None:
        pipes = [p for p in pipes if p["manufacturer"] == manufacturer]
    return pipes


def list_pipe_manufacturers() -> list[str]:
    return sorted({p["manufacturer"] for p in list_pipes()})


def get_pipe(key: str) -> dict[str, Any]:
    for pipe in list_pipes():
        if pipe["key"] == key:
            return pipe
    raise KeyError(f"unknown pipe key {key!r}")


def list_grouts(manufacturer: str | None = None) -> list[dict[str, Any]]:
    """Each entry: key, manufacturer, name, k_g_W_mK, ..."""
    grouts = _load_entries(_GROUT_FILES, "grouts")
    if manufacturer is not None:
        grouts = [g for g in grouts if g["manufacturer"] == manufacturer]
    return grouts


def list_grout_manufacturers() -> list[str]:
    return sorted({g["manufacturer"] for g in list_grouts()})


def get_grout(key: str) -> dict[str, Any]:
    for grout in list_grouts():
        if grout["key"] == key:
            return grout
    raise KeyError(f"unknown grout key {key!r}")
