"""Real, sourced jurisdiction tax-incentive presets for the Financial Plan
tab's incentive_fraction input -- same "keep field data sourced to a real
citation instead of a hand-typed number going stale" pattern as
pipe_catalog.py, applied to tax policy instead of pipe/grout datasheets.

Deliberately NOT a general "incentive database": only a standing, ongoing,
percentage-of-capital-cost RATE belongs here (e.g. a tax credit with a
known statutory rate), never a one-off fixed-amount grant program with an
application deadline or a limited fund -- those aren't reducible to a
capital-cost fraction and representing one as if it were would misrepresent
it. See geothermal/data/incentive_presets.json's own "_note" for what was
researched and deliberately left out (EU/Bulgaria, as of 2026-09-29 -- no
comparably stable standing rate was found).

Every preset is a REAL rate from REAL tax-policy research, cited to its own
source and retrieval date, not invented or estimated -- same "never
fabricate" precedent as trt.py's rho_cp_J_m3K and thermal_mass.py's grout/
pipe rho_cp. Tax law changes; check base_fraction against the cited source
before relying on an old preset for a live decision, and treat this as a
starting point for the SOFTWARE user's own tax/legal advice, never a
substitute for it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_FILE = Path(__file__).parent / "data" / "incentive_presets.json"


def list_incentive_presets() -> list[dict[str, Any]]:
    """Each entry: id, jurisdiction, base_fraction (the rate this preset
    actually fills in), enhanced_fraction/enhanced_condition and
    bonus_fraction/bonus_condition (informational only -- not auto-applied,
    since their eligibility conditions can't be verified here), source,
    as_of, notes.
    """
    with open(_DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data["presets"]
