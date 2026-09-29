"""Tests for geothermal.economics -- the "financial plan" feature added so
GeoBore can itself produce a capital-cost/payback report from real drilling
and energy pricing inputs, instead of a user pasting engine results into a
spreadsheet by hand.
"""
import pytest

from geothermal import economics


def test_baseline_operating_cost_splits_heating_and_cooling_correctly():
    # +ve = heating (2 hours, 5000 W each = 10 kWh), -ve = cooling (1 hour, 3000 W = 3 kWh).
    load = [5000.0, 5000.0, -3000.0]
    result = economics.baseline_operating_cost(load, baseline_heating_cost_per_kWh_delivered=0.08, baseline_cooling_cost_per_kWh_delivered=0.15)
    assert result["heating_delivered_kWh"] == pytest.approx(10.0)
    assert result["cooling_delivered_kWh"] == pytest.approx(3.0)
    assert result["heating_cost"] == pytest.approx(0.8)
    assert result["cooling_cost"] == pytest.approx(0.45)
    assert result["total_annual_cost"] == pytest.approx(1.25)


def test_baseline_operating_cost_rejects_negative_prices_and_empty_load():
    with pytest.raises(ValueError, match="must be >= 0"):
        economics.baseline_operating_cost([1000.0], -0.1, 0.1)
    with pytest.raises(ValueError, match="is empty"):
        economics.baseline_operating_cost([], 0.1, 0.1)


def test_compare_generators_runs_one_baseline_cost_calc_per_named_profile():
    load = [5000.0, 5000.0, -3000.0]
    generators = [
        {"name": "Gas boiler + split AC", "heating_cost_per_kWh_delivered": 0.08, "cooling_cost_per_kWh_delivered": 0.15},
        {"name": "Air-source heat pump", "heating_cost_per_kWh_delivered": 0.05, "cooling_cost_per_kWh_delivered": 0.05},
    ]
    results = economics.compare_generators(load, generators)
    assert [r["name"] for r in results] == ["Gas boiler + split AC", "Air-source heat pump"]
    # First profile matches the standalone baseline_operating_cost call with the same numbers.
    direct = economics.baseline_operating_cost(load, 0.08, 0.15)
    assert results[0]["total_annual_cost"] == pytest.approx(direct["total_annual_cost"])
    # A more efficient/cheaper-to-run profile costs less for the SAME load.
    assert results[1]["total_annual_cost"] < results[0]["total_annual_cost"]


def test_compare_generators_rejects_empty_list_and_missing_name():
    with pytest.raises(ValueError, match="must not be empty"):
        economics.compare_generators([1000.0], [])
    with pytest.raises(ValueError, match="needs a non-empty 'name'"):
        economics.compare_generators([1000.0], [{"heating_cost_per_kWh_delivered": 0.1, "cooling_cost_per_kWh_delivered": 0.1}])


def test_financial_summary_matches_hand_computed_capital_cost_breakdown():
    result = economics.financial_summary(
        total_length_m=1000.0, cost_per_meter_drilled=60.0,
        n_boreholes=8, fixed_cost_per_borehole=5000.0,
        heat_pump_equipment_cost=20000.0, other_fixed_costs=5000.0,
        gshp_annual_electrical_energy_kWh=15000.0, electricity_price_per_kWh=0.15,
        baseline_annual_operating_cost=15000.0,
        analysis_period_years=25, incentive_fraction=0.30,
    )
    assert result["drilling_cost"] == pytest.approx(60000.0)
    assert result["borehole_fixed_cost"] == pytest.approx(40000.0)
    assert result["gross_capital_cost"] == pytest.approx(125000.0)
    assert result["incentive_amount"] == pytest.approx(37500.0)
    assert result["net_capital_cost"] == pytest.approx(87500.0)
    assert result["gshp_annual_operating_cost"] == pytest.approx(2250.0)
    assert result["first_year_savings"] == pytest.approx(12750.0)


def test_flat_savings_produces_an_exact_linear_payback():
    # net capital 100000, flat 10000/yr savings -> exactly 10 years, no escalation/discount.
    result = economics.financial_summary(
        total_length_m=1000.0, cost_per_meter_drilled=60.0,
        n_boreholes=8, fixed_cost_per_borehole=5000.0,
        heat_pump_equipment_cost=0.0, other_fixed_costs=0.0,
        gshp_annual_electrical_energy_kWh=10000.0, electricity_price_per_kWh=0.0,
        baseline_annual_operating_cost=10000.0,
        analysis_period_years=20,
    )
    assert result["simple_payback_years"] == pytest.approx(10.0)
    assert result["cumulative_cash_flow"][9] == pytest.approx(0.0, abs=1e-6)  # index 9 = year 10
    assert result["cumulative_cash_flow"][-1] == pytest.approx(100000.0)  # year 20: 2x capital recovered
    assert result["net_present_value"] is None
    assert result["discounted_payback_years"] is None
    assert result["discounted_cumulative_cash_flow"] is None


def test_escalation_pulls_payback_earlier_than_the_flat_case():
    base_kwargs = dict(
        total_length_m=1000.0, cost_per_meter_drilled=60.0,
        n_boreholes=8, fixed_cost_per_borehole=5000.0,
        heat_pump_equipment_cost=0.0, other_fixed_costs=0.0,
        gshp_annual_electrical_energy_kWh=10000.0, electricity_price_per_kWh=0.0,
        baseline_annual_operating_cost=10000.0,
        analysis_period_years=20,
    )
    flat = economics.financial_summary(**base_kwargs)
    escalated = economics.financial_summary(**base_kwargs, energy_price_escalation_rate=0.03)
    assert escalated["simple_payback_years"] < flat["simple_payback_years"]


def test_discounting_pushes_payback_later_and_reports_a_present_value():
    result = economics.financial_summary(
        total_length_m=1000.0, cost_per_meter_drilled=60.0,
        n_boreholes=8, fixed_cost_per_borehole=5000.0,
        heat_pump_equipment_cost=20000.0, other_fixed_costs=5000.0,
        gshp_annual_electrical_energy_kWh=15000.0, electricity_price_per_kWh=0.15,
        baseline_annual_operating_cost=15000.0,
        analysis_period_years=25, incentive_fraction=0.30,
        energy_price_escalation_rate=0.03, discount_rate=0.05,
    )
    assert result["discounted_payback_years"] > result["simple_payback_years"]
    assert result["net_present_value"] is not None
    assert result["discounted_cumulative_cash_flow"] is not None
    assert len(result["discounted_cumulative_cash_flow"]) == 25
    assert result["discounted_cumulative_cash_flow"][-1] == pytest.approx(result["net_present_value"])
    # Discounting only ever reduces the present value of future savings relative to the
    # undiscounted running balance at the same year.
    assert all(d <= u for d, u in zip(result["discounted_cumulative_cash_flow"], result["cumulative_cash_flow"]))


def test_a_system_that_never_saves_money_reports_no_payback():
    result = economics.financial_summary(
        total_length_m=1000.0, cost_per_meter_drilled=60.0,
        n_boreholes=8, fixed_cost_per_borehole=5000.0,
        heat_pump_equipment_cost=0.0, other_fixed_costs=0.0,
        gshp_annual_electrical_energy_kWh=20000.0, electricity_price_per_kWh=0.20,  # 4000/yr opex
        baseline_annual_operating_cost=3000.0,  # baseline is CHEAPER than the GSHP here
        analysis_period_years=20,
    )
    assert result["first_year_savings"] < 0
    assert result["simple_payback_years"] is None
    assert all(v < 0 for v in result["cumulative_cash_flow"])  # keeps getting worse, never recovers


def test_rejects_invalid_inputs():
    good = dict(
        total_length_m=1000.0, cost_per_meter_drilled=60.0,
        n_boreholes=8, fixed_cost_per_borehole=5000.0,
        heat_pump_equipment_cost=0.0, other_fixed_costs=0.0,
        gshp_annual_electrical_energy_kWh=10000.0, electricity_price_per_kWh=0.1,
        baseline_annual_operating_cost=10000.0,
        analysis_period_years=20,
    )
    with pytest.raises(ValueError, match="total_length_m and n_boreholes"):
        economics.financial_summary(**{**good, "total_length_m": 0.0})
    with pytest.raises(ValueError, match="drilling cost inputs"):
        economics.financial_summary(**{**good, "cost_per_meter_drilled": -1.0})
    with pytest.raises(ValueError, match="analysis_period_years must be > 0"):
        economics.financial_summary(**{**good, "analysis_period_years": 0})
    with pytest.raises(ValueError, match="incentive_fraction must be between"):
        economics.financial_summary(**{**good, "incentive_fraction": 1.5})


def test_incentive_fraction_of_zero_reproduces_gross_as_net():
    result = economics.financial_summary(
        total_length_m=500.0, cost_per_meter_drilled=50.0,
        n_boreholes=4, fixed_cost_per_borehole=1000.0,
        heat_pump_equipment_cost=10000.0, other_fixed_costs=0.0,
        gshp_annual_electrical_energy_kWh=8000.0, electricity_price_per_kWh=0.12,
        baseline_annual_operating_cost=9000.0,
        analysis_period_years=15,
    )
    assert result["net_capital_cost"] == pytest.approx(result["gross_capital_cost"])
    assert result["incentive_amount"] == pytest.approx(0.0)
