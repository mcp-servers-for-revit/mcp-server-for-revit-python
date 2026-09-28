using System.Linq;
using System.Text.Json;

namespace HC.GeoBore.Tests;

/// <summary>
/// Exercises the exact command sequences the Simulation and Sizing tabs use
/// (MainWindow.xaml.cs SimulateButton_Click / SizeFieldButton_Click), through
/// the real subprocess bridge -- no mocks. Cross-checks against the same
/// known-good values the Python-side tests use
/// (tests/unit/test_geothermal_report_examples.py::test_step2,
/// tests/unit/test_sizing_cross_check.py), so a broken C#<->Python contract
/// for these two newer commands shows up here.
/// </summary>
public class SimulationAndSizingTests
{
    private static readonly Dictionary<string, object?> ReportField = new()
    {
        ["N_1"] = 4, ["N_2"] = 3, ["B_1"] = 6.0, ["B_2"] = 6.0, ["H"] = 150.0, ["D"] = 2.0, ["r_b"] = 0.075,
    };

    private static readonly Dictionary<string, object?> SingleUTube = new()
    {
        ["type"] = "single_u_tube",
        ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
        ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
    };

    [Fact]
    public async Task HourlySimulation_MatchesReportStep2WorkedExample()
    {
        // Same case as the pygfunction report's step 2 script and
        // tests/unit/test_geothermal_report_examples.py::test_step2_ten_year_hourly_simulation --
        // reproduced here through run_hourly_simulation directly, the same command
        // SimulateButton_Click calls (the UI additionally synthesizes the load from
        // monthly values first; that step is covered separately below).
        var engine = EngineClient.CreateDefault();
        var field = await engine.CallAsync("build_rectangle_field", ReportField);

        const int years = 10;
        var hours = Enumerable.Range(1, years * 8760).ToArray();
        var load = hours.Select(h =>
        {
            var q = 25e3 * Math.Cos(2 * Math.PI * h / 8760.0) + 8e3;
            if (q > 0)
                q += 6e3 * Math.Cos(2 * Math.PI * h / 24.0);
            return q;
        }).ToList();

        var result = await engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>
        {
            ["field"] = field, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = SingleUTube, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["hourly_load_W"] = load, ["algorithm"] = "ClaessonJaved",
        });

        Assert.Equal(0.150, result.GetProperty("R_b_star_mK_W").GetDouble(), 0.001);
        Assert.Equal(-0.1, result.GetProperty("T_f_min_C").GetDouble(), 0.1);
        Assert.Equal(15.3, result.GetProperty("T_f_max_C").GetDouble(), 0.1);
    }

    [Fact]
    public async Task SynthesizeHourlyLoad_ThenSimulate_RunsEndToEndWithoutError()
    {
        // The actual sequence SimulateButton_Click performs: monthly grid -> synthesize_hourly_load
        // -> repeat for the design life -> run_hourly_simulation.
        var engine = EngineClient.CreateDefault();
        var field = await engine.CallAsync("build_rectangle_field", ReportField);

        var hourlyResult = await engine.CallAsync("synthesize_hourly_load", new Dictionary<string, object?>
        {
            ["baseload_heating_kWh"] = new[] { 3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800 },
            ["baseload_cooling_kWh"] = new[] { 0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0 },
            ["peak_heating_kW"] = new[] { 25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24 },
            ["peak_cooling_kW"] = new[] { 0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0 },
        });
        var oneYear = hourlyResult.EnumerateArray().Select(x => x.GetDouble()).ToList();
        Assert.Equal(8760, oneYear.Count);

        var repeated = new List<double>();
        for (var y = 0; y < 5; y++)
            repeated.AddRange(oneYear);

        var result = await engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>
        {
            ["field"] = field, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = SingleUTube, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["hourly_load_W"] = repeated, ["algorithm"] = "ClaessonJaved",
        });

        Assert.True(result.GetProperty("R_b_star_mK_W").GetDouble() > 0);
        Assert.True(result.GetProperty("T_f_min_C").GetDouble() < result.GetProperty("T_f_max_C").GetDouble());
    }

    [Fact]
    public async Task SizeField_AndGHEtoolCrossCheck_AgreeWithinDocumentedTolerance()
    {
        // Same case as tests/unit/test_sizing_cross_check.py, run through the exact
        // command sequence SizeFieldButton_Click uses with "Cross-check with GHEtool" on.
        var engine = EngineClient.CreateDefault();
        var fieldTemplate = new Dictionary<string, object?>
        {
            ["layout"] = "rectangle", ["N_1"] = 4, ["N_2"] = 3, ["B_1"] = 6.0, ["B_2"] = 6.0, ["D"] = 2.0, ["r_b"] = 0.075,
        };
        var baseHeating = new[] { 3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800 };
        var baseCooling = new[] { 0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0 };
        var peakHeating = new[] { 25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24 };
        var peakCooling = new[] { 0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0 };

        var hourlyResult = await engine.CallAsync("synthesize_hourly_load", new Dictionary<string, object?>
        {
            ["baseload_heating_kWh"] = baseHeating, ["baseload_cooling_kWh"] = baseCooling,
            ["peak_heating_kW"] = peakHeating, ["peak_cooling_kW"] = peakCooling,
        });
        var oneYearLoad = hourlyResult.EnumerateArray().Select(x => x.GetDouble()).ToList();

        var ours = await engine.CallAsync("size_field", new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = SingleUTube, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["hourly_load_W"] = oneYearLoad, ["simulation_period_years"] = 10,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 16.0, ["H_min"] = 20.0, ["H_max"] = 400.0,
        });

        var ghetool = await engine.CallAsync("size_field_ghetool", new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = SingleUTube, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["baseload_heating_kWh"] = baseHeating, ["baseload_cooling_kWh"] = baseCooling,
            ["peak_heating_kW"] = peakHeating, ["peak_cooling_kW"] = peakCooling,
            ["simulation_period_years"] = 10, ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 16.0, ["method"] = "L3",
        });

        var oursH = ours.GetProperty("H_m").GetDouble();
        var ghetoolH = ghetool.GetProperty("H_m").GetDouble();
        var pctDiff = 100.0 * (oursH - ghetoolH) / ghetoolH;

        Assert.True(Math.Abs(pctDiff) < 15.0, $"sized depths {oursH:N1}m vs {ghetoolH:N1}m differ by {pctDiff:N1}%, expected <15%");
        Assert.Equal(
            ghetool.GetProperty("R_b_star_mK_W").GetDouble(),
            ours.GetProperty("R_b_star_mK_W").GetDouble(),
            ghetool.GetProperty("R_b_star_mK_W").GetDouble() * 0.05);
    }

    [Fact]
    public async Task HybridCoolingTower_LetsASmallerFieldSatisfyTheSameLimit()
    {
        // Same command sequence SizeFieldButton_Click uses with the tower checkbox on:
        // synthesize_hourly_load -> size_field (baseline) -> apply_cooling_tower ->
        // size_field again on the tower-adjusted load. Uses a strongly cooling-dominated
        // profile (the scenario the tower exists for) so the tower actually engages.
        var engine = EngineClient.CreateDefault();
        var fieldTemplate = new Dictionary<string, object?>
        {
            ["layout"] = "rectangle", ["N_1"] = 4, ["N_2"] = 3, ["B_1"] = 6.0, ["B_2"] = 6.0, ["D"] = 2.0, ["r_b"] = 0.075,
        };

        var hourlyResult = await engine.CallAsync("synthesize_hourly_load", new Dictionary<string, object?>
        {
            ["baseload_heating_kWh"] = new[] { 500, 400, 300, 100, 0, 0, 0, 0, 0, 100, 300, 500 },
            ["baseload_cooling_kWh"] = new[] { 0, 0, 500, 2000, 5000, 9000, 12000, 11000, 6000, 2000, 200, 0 },
            ["peak_heating_kW"] = new[] { 8, 7, 5, 2, 0, 0, 0, 0, 0, 2, 5, 8 },
            ["peak_cooling_kW"] = new[] { 0, 0, 10, 30, 60, 90, 110, 105, 70, 30, 5, 0 },
        });
        var oneYearLoad = hourlyResult.EnumerateArray().Select(x => x.GetDouble()).ToList();

        var sizeArgs = new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 18.0,
            ["pipe_config"] = SingleUTube, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["simulation_period_years"] = 10, ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 40.0,
            ["H_min"] = 20.0, ["H_max"] = 400.0,
        };

        sizeArgs["hourly_load_W"] = oneYearLoad;
        var withoutTower = await engine.CallAsync("size_field", sizeArgs);

        var towerResult = await engine.CallAsync("apply_cooling_tower", new Dictionary<string, object?>
        {
            ["hourly_load_W"] = oneYearLoad, ["tower_capacity_kW"] = 60.0,
        });
        Assert.True(towerResult.GetProperty("tower_hours").GetInt32() > 0);
        Assert.True(towerResult.GetProperty("tower_peak_kW").GetDouble() <= 60.0 + 1e-6);

        var groundLoad = towerResult.GetProperty("ground_load_W").EnumerateArray().Select(x => x.GetDouble()).ToList();
        sizeArgs["hourly_load_W"] = groundLoad;
        var withTower = await engine.CallAsync("size_field", sizeArgs);

        Assert.True(withTower.GetProperty("H_m").GetDouble() < withoutTower.GetProperty("H_m").GetDouble());
    }

    [Fact]
    public async Task AreaSizing_MaxRows_ReshapesANarrowSiteInsteadOfShrinkingIt()
    {
        // Same command AreaSizeButton_Click sends when "Max rows" is set: field_layout_from_area
        // with max_rows caps N_2 and grows N_1 to keep roughly the same borehole count.
        var engine = EngineClient.CreateDefault();

        var square = await engine.CallAsync("field_layout_from_area", new Dictionary<string, object?>
        {
            ["area_ambient_m2"] = 1000.0, ["area_under_building_m2"] = 296.0, ["spacing_m"] = 6.0,
        });
        var narrow = await engine.CallAsync("field_layout_from_area", new Dictionary<string, object?>
        {
            ["area_ambient_m2"] = 1000.0, ["area_under_building_m2"] = 296.0, ["spacing_m"] = 6.0, ["max_rows"] = 3,
        });

        Assert.Equal(3, narrow.GetProperty("N_2").GetInt32());
        Assert.True(narrow.GetProperty("N_1").GetInt32() > square.GetProperty("N_1").GetInt32());
        // Roughly the same total borehole count, reshaped rather than shrunk.
        var squareCount = square.GetProperty("n_boreholes").GetInt32();
        var narrowCount = narrow.GetProperty("n_boreholes").GetInt32();
        Assert.True(Math.Abs(narrowCount - squareCount) <= 3);
    }

    [Fact]
    public async Task AreaSizing_LargeAreaModestLoad_SizesWithoutADryCooler()
    {
        // Same command sequence AreaSizeButton_Click uses when size_field succeeds directly:
        // field_layout_from_area -> synthesize_peak_only_load -> size_field.
        var engine = EngineClient.CreateDefault();

        var layout = await engine.CallAsync("field_layout_from_area", new Dictionary<string, object?>
        {
            ["area_ambient_m2"] = 2000.0, ["area_under_building_m2"] = 0.0, ["spacing_m"] = 6.0,
        });
        var n1 = layout.GetProperty("N_1").GetInt32();
        var n2 = layout.GetProperty("N_2").GetInt32();
        Assert.True(n1 * n2 >= 25); // a generous area should yield a sizeable field

        var fieldTemplate = new Dictionary<string, object?>
        {
            ["layout"] = "rectangle", ["N_1"] = n1, ["N_2"] = n2, ["B_1"] = 6.0, ["B_2"] = 6.0, ["D"] = 2.0, ["r_b"] = 0.075,
        };

        var oneYearLoad = (await engine.CallAsync("synthesize_peak_only_load", new Dictionary<string, object?>
        {
            ["peak_heating_kW"] = 30.0, ["peak_cooling_kW"] = 25.0,
            ["heating_season_months"] = 4, ["cooling_season_months"] = 4,
        })).EnumerateArray().Select(x => x.GetDouble()).ToList();

        var result = await engine.CallAsync("size_field", new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = SingleUTube, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["hourly_load_W"] = oneYearLoad, ["simulation_period_years"] = 10,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 35.0, ["H_min"] = 20.0, ["H_max"] = 300.0,
        });

        Assert.True(result.GetProperty("H_m").GetDouble() > 0);
        Assert.True(result.GetProperty("T_f_max_C").GetDouble() <= 35.0 + 1e-6);
    }

    [Fact]
    public async Task AreaSizing_SmallAreaHeavyCooling_NeedsADryCooler()
    {
        // Same command sequence AreaSizeButton_Click uses when size_field fails at H_max:
        // catches the "fails the fluid temperature limit" error and falls back to
        // minimum_tower_capacity at the fixed max practical depth.
        var engine = EngineClient.CreateDefault();

        var layout = await engine.CallAsync("field_layout_from_area", new Dictionary<string, object?>
        {
            ["area_ambient_m2"] = 100.0, ["area_under_building_m2"] = 0.0, ["spacing_m"] = 6.0,
        });
        var n1 = layout.GetProperty("N_1").GetInt32();
        var n2 = layout.GetProperty("N_2").GetInt32();
        Assert.True(n1 * n2 <= 9); // a tight area should yield a small field

        var fieldTemplate = new Dictionary<string, object?>
        {
            ["layout"] = "rectangle", ["N_1"] = n1, ["N_2"] = n2, ["B_1"] = 6.0, ["B_2"] = 6.0, ["D"] = 2.0, ["r_b"] = 0.075,
        };
        const double hMax = 60.0;

        var oneYearLoad = (await engine.CallAsync("synthesize_peak_only_load", new Dictionary<string, object?>
        {
            ["peak_heating_kW"] = 10.0, ["peak_cooling_kW"] = 150.0,
            ["heating_season_months"] = 3, ["cooling_season_months"] = 5,
        })).EnumerateArray().Select(x => x.GetDouble()).ToList();

        var commonArgs = new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 18.0,
            ["pipe_config"] = SingleUTube, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["hourly_load_W"] = oneYearLoad, ["simulation_period_years"] = 10,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 35.0,
        };

        var sizeArgs = new Dictionary<string, object?>(commonArgs) { ["H_min"] = 20.0, ["H_max"] = hMax };
        EngineException? caught = null;
        try
        {
            await engine.CallAsync("size_field", sizeArgs);
        }
        catch (EngineException ex)
        {
            caught = ex;
        }
        Assert.NotNull(caught);
        Assert.Contains("fails the fluid temperature limit", caught!.Message);

        var towerArgs = new Dictionary<string, object?>(commonArgs) { ["H"] = hMax };
        var towerResult = await engine.CallAsync("minimum_tower_capacity", towerArgs);

        Assert.True(towerResult.GetProperty("tower_capacity_kW").GetDouble() > 0);
        Assert.True(towerResult.GetProperty("T_f_max_C").GetDouble() <= 35.0 + 1e-6);
    }
}
