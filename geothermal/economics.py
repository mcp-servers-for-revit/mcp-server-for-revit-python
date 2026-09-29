"""Project financial analysis: turns the engine's own energy results
(drilled length from fields.py/sizing.py, GSHP compressor electrical energy
from heat_pump.py) plus real, user-supplied cost/pricing inputs into a
capital cost breakdown, an annual operating cost comparison against a
baseline (non-geothermal) system, and a payback/cash-flow projection over
the design life.

This module is currency-agnostic (the WPF shell labels its own inputs in
euro; every function here just takes a plain price-per-unit number). Every
monetary figure comes from a REQUIRED input, never a
built-in default or an assumed regional price -- drilling cost, equipment
cost, electricity price, and baseline system cost all vary enormously by
region, contractor, and utility, and guessing any of them would misrepresent
a specific project's actual economics (same "never fabricate" precedent as
trt.py's rho_cp_J_m3K and thermal_mass.py's grout/pipe rho_cp, both
required, never-guessed inputs).

incentive_fraction is a plain caller-supplied percentage-off the gross
capital cost, NOT a specific jurisdiction's tax-credit rule (e.g. the US
commercial Section 48 ITC's own base/enhanced rates and phase-down
schedule) -- this engine has no way to track legislation as it changes, so
the caller supplies whatever rate applies to their own project and
jurisdiction, sourced from their own tax/legal advice.
"""
from __future__ import annotations

from typing import Any, Optional


def baseline_operating_cost(
    hourly_load_W: list[float],
    baseline_heating_cost_per_kWh_delivered: float,
    baseline_cooling_cost_per_kWh_delivered: float,
) -> dict[str, Any]:
    """What a NON-geothermal baseline system (e.g. a gas furnace + a
    conventional AC) would cost per year to deliver the SAME building load
    hourly_load_W already represents (+ve = heating demand, -ve = cooling
    demand -- same convention as sizing.synthesize_hourly_load and
    heat_pump.apply_heat_pump's building_load_W).

    The two cost-per-kWh-delivered inputs are each the caller's OWN derived
    number (fuel price / baseline system efficiency, or electricity price /
    baseline EER-as-COP) -- this function makes no assumption about the
    baseline system's fuel type or efficiency curve, so it works for a gas
    furnace, a resistance heater, or a conventional split AC alike; the
    caller does that one division themselves with numbers only they know
    (their own utility rate, their own baseline equipment's rated
    efficiency).
    """
    if baseline_heating_cost_per_kWh_delivered < 0 or baseline_cooling_cost_per_kWh_delivered < 0:
        raise ValueError("baseline cost-per-kWh-delivered values must be >= 0")
    if not hourly_load_W:
        raise ValueError("hourly_load_W is empty")

    heating_kWh = sum(q for q in hourly_load_W if q > 0.0) / 1000.0
    cooling_kWh = sum(-q for q in hourly_load_W if q < 0.0) / 1000.0
    heating_cost = heating_kWh * baseline_heating_cost_per_kWh_delivered
    cooling_cost = cooling_kWh * baseline_cooling_cost_per_kWh_delivered
    return {
        "heating_delivered_kWh": heating_kWh,
        "cooling_delivered_kWh": cooling_kWh,
        "heating_cost": heating_cost,
        "cooling_cost": cooling_cost,
        "total_annual_cost": heating_cost + cooling_cost,
    }


def compare_generators(hourly_load_W: list[float], generators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """baseline_operating_cost, run once per named generator profile, for a
    side-by-side "what would each alternative heating/cooling generator cost
    to run" comparison -- e.g. a gas boiler + split AC, an air-source heat
    pump, and electric resistance heating, alongside the GSHP design's own
    operating cost (computed separately by financial_summary).

    generators: a list of {"name": str, "heating_cost_per_kWh_delivered":
    float, "cooling_cost_per_kWh_delivered": float} -- the SAME two
    caller-derived numbers baseline_operating_cost itself takes, one profile
    per generator type being compared. No generator type is built in or
    assumed here: this engine has no database of typical AFUE/COP/EER
    figures by equipment type, since a fabricated one would misrepresent a
    specific product's real rated performance -- the caller supplies each
    profile's own numbers, sourced from that equipment's own datasheet and
    the fuel price it actually pays.
    """
    if not generators:
        raise ValueError("generators must not be empty")
    results = []
    for g in generators:
        if "name" not in g or not g["name"]:
            raise ValueError(f"each generator profile needs a non-empty 'name': {g}")
        cost = baseline_operating_cost(
            hourly_load_W, g["heating_cost_per_kWh_delivered"], g["cooling_cost_per_kWh_delivered"],
        )
        results.append({"name": g["name"], **cost})
    return results


def financial_summary(
    total_length_m: float, cost_per_meter_drilled: float,
    n_boreholes: int, fixed_cost_per_borehole: float,
    heat_pump_equipment_cost: float, other_fixed_costs: float,
    gshp_annual_electrical_energy_kWh: float, electricity_price_per_kWh: float,
    baseline_annual_operating_cost: float,
    analysis_period_years: int,
    incentive_fraction: float = 0.0,
    energy_price_escalation_rate: float = 0.0,
    discount_rate: Optional[float] = None,
) -> dict[str, Any]:
    """Capital cost breakdown, annual operating cost comparison against a
    baseline system, simple payback, and a year-by-year cumulative cash
    flow over analysis_period_years.

    energy_price_escalation_rate: annual fractional increase applied to the
    GSHP-vs-baseline SAVINGS gap every year of the projection (0.0 = flat
    prices, the default). Applied equally to both sides -- it does not
    change first-year savings or simple payback, only how projected savings
    grow later in the period. Does NOT model differential escalation (e.g.
    gas prices rising faster than electricity) -- a documented
    simplification, not a hidden assumption.

    discount_rate (optional): if given, the cash flow is also discounted
    (net_present_value = the final discounted cumulative value) and a
    discounted payback year is reported alongside the simple (undiscounted)
    one. Left out of the result entirely (not defaulted to 0) when not given.
    """
    if total_length_m <= 0 or n_boreholes <= 0:
        raise ValueError("total_length_m and n_boreholes must both be > 0")
    if cost_per_meter_drilled < 0 or fixed_cost_per_borehole < 0:
        raise ValueError("drilling cost inputs must be >= 0")
    if heat_pump_equipment_cost < 0 or other_fixed_costs < 0:
        raise ValueError("equipment/other cost inputs must be >= 0")
    if electricity_price_per_kWh < 0:
        raise ValueError("electricity_price_per_kWh must be >= 0")
    if baseline_annual_operating_cost < 0:
        raise ValueError("baseline_annual_operating_cost must be >= 0")
    if analysis_period_years <= 0:
        raise ValueError("analysis_period_years must be > 0")
    if not (0.0 <= incentive_fraction <= 1.0):
        raise ValueError("incentive_fraction must be between 0.0 and 1.0")

    drilling_cost = total_length_m * cost_per_meter_drilled
    borehole_fixed_cost = n_boreholes * fixed_cost_per_borehole
    gross_capital_cost = drilling_cost + borehole_fixed_cost + heat_pump_equipment_cost + other_fixed_costs
    incentive_amount = gross_capital_cost * incentive_fraction
    net_capital_cost = gross_capital_cost - incentive_amount

    gshp_annual_operating_cost = gshp_annual_electrical_energy_kWh * electricity_price_per_kWh
    first_year_savings = baseline_annual_operating_cost - gshp_annual_operating_cost

    cumulative_cash_flow: list[float] = []
    discounted_cumulative_cash_flow: Optional[list[float]] = [] if discount_rate is not None else None
    running = -net_capital_cost
    discounted_running = -net_capital_cost
    simple_payback_years: Optional[float] = None
    discounted_payback_years: Optional[float] = None
    for year in range(1, analysis_period_years + 1):
        escalation = (1.0 + energy_price_escalation_rate) ** (year - 1)
        year_savings = first_year_savings * escalation
        running += year_savings
        cumulative_cash_flow.append(running)
        if simple_payback_years is None and running >= 0:
            # Linear interpolation within the year the balance crosses zero.
            prev = cumulative_cash_flow[-2] if len(cumulative_cash_flow) > 1 else -net_capital_cost
            simple_payback_years = (year - 1) + (-prev) / (running - prev) if running != prev else float(year)
        if discount_rate is not None:
            discounted_savings = year_savings / (1.0 + discount_rate) ** year
            discounted_running += discounted_savings
            discounted_cumulative_cash_flow.append(discounted_running)
            if discounted_payback_years is None and discounted_running >= 0:
                prev_d = discounted_cumulative_cash_flow[-2] if len(discounted_cumulative_cash_flow) > 1 else -net_capital_cost
                discounted_payback_years = (year - 1) + (-prev_d) / (discounted_running - prev_d) if discounted_running != prev_d else float(year)

    return {
        "drilling_cost": drilling_cost,
        "borehole_fixed_cost": borehole_fixed_cost,
        "heat_pump_equipment_cost": heat_pump_equipment_cost,
        "other_fixed_costs": other_fixed_costs,
        "gross_capital_cost": gross_capital_cost,
        "incentive_amount": incentive_amount,
        "net_capital_cost": net_capital_cost,
        "gshp_annual_operating_cost": gshp_annual_operating_cost,
        "baseline_annual_operating_cost": baseline_annual_operating_cost,
        "first_year_savings": first_year_savings,
        "simple_payback_years": simple_payback_years,
        "cumulative_cash_flow": cumulative_cash_flow,
        "discounted_cumulative_cash_flow": discounted_cumulative_cash_flow,
        "net_present_value": discounted_cumulative_cash_flow[-1] if discounted_cumulative_cash_flow else None,
        "discounted_payback_years": discounted_payback_years,
    }
