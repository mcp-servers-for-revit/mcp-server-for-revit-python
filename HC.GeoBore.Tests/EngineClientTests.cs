using System.Linq;
using System.Text.Json;

namespace HC.GeoBore.Tests;

/// <summary>
/// Exercises the real subprocess bridge (EngineClient -> python.exe -m
/// geothermal.engine_cli), not mocks -- these are the same calculations
/// tests/unit/test_geothermal_report_examples.py validates on the Python
/// side, run here through the actual channel the WPF UI uses, so a broken
/// C#<->Python contract shows up here rather than only at runtime in the app.
/// </summary>
public class EngineClientTests
{
    private static readonly Dictionary<string, object?> ReportField = new()
    {
        ["N_1"] = 4,
        ["N_2"] = 3,
        ["B_1"] = 6.0,
        ["B_2"] = 6.0,
        ["H"] = 150.0,
        ["D"] = 2.0,
        ["r_b"] = 0.075,
    };

    [Fact]
    public async Task UnknownCommand_ThrowsEngineException()
    {
        var engine = EngineClient.CreateDefault();
        var ex = await Assert.ThrowsAsync<EngineException>(
            () => engine.CallAsync("not_a_real_command"));
        Assert.Contains("list_commands", ex.Message);
    }

    [Fact]
    public async Task ListCommands_IncludesCoreCommands()
    {
        var engine = EngineClient.CreateDefault();
        var result = await engine.CallAsync("list_commands");
        var names = result.EnumerateArray().Select(e => e.GetString()).ToList();
        Assert.Contains("build_rectangle_field", names);
        Assert.Contains("evaluate_gfunction", names);
        Assert.Contains("evaluate_mift_gfunction", names);
        Assert.Contains("run_hourly_simulation", names);
        Assert.Contains("network_gfunction", names);
    }

    [Fact]
    public async Task Step1_UbwtGfunctionAt25Years_MatchesTheReport()
    {
        var engine = EngineClient.CreateDefault();

        var field = await engine.CallAsync("build_rectangle_field", ReportField);

        var time = await engine.CallAsync("time_grid", new Dictionary<string, object?>
        {
            ["t_min_s"] = 3600.0,
            ["t_max_s"] = 25 * 8760.0 * 3600.0,
            ["num"] = 50,
        });

        var result = await engine.CallAsync("evaluate_gfunction", new Dictionary<string, object?>
        {
            ["field"] = field,
            ["alpha"] = 1.0e-6,
            ["time"] = time,
            ["boundary_condition"] = "UBWT",
        });

        var g = result.GetProperty("g").EnumerateArray().Select(e => e.GetDouble()).ToArray();
        Assert.Equal(17.903, g[^1], 0.001);

        var summary = await engine.CallAsync("field_summary", new Dictionary<string, object?> { ["field"] = field });
        Assert.Equal(12, summary.GetProperty("n_boreholes").GetInt32());
        Assert.Equal(1800.0, summary.GetProperty("total_length_m").GetDouble(), 0.1);
    }

    [Fact]
    public async Task Step3_MiftGfunctionAt25Years_MatchesTheReport()
    {
        var engine = EngineClient.CreateDefault();

        var field = await engine.CallAsync("build_rectangle_field", ReportField);
        var time = await engine.CallAsync("time_grid", new Dictionary<string, object?>
        {
            ["t_min_s"] = 3600.0,
            ["t_max_s"] = 25 * 8760.0 * 3600.0,
            ["num"] = 30,
        });

        var pipeConfig = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131,
            ["r_out"] = 0.0160,
            ["k_p"] = 0.4,
        };

        var result = await engine.CallAsync("evaluate_mift_gfunction", new Dictionary<string, object?>
        {
            ["field"] = field,
            ["alpha"] = 1.0e-6,
            ["time"] = time,
            ["pipe_config"] = pipeConfig,
            ["m_flow_network"] = 12 * 0.30,
            ["k_s"] = 2.0,
            ["k_g"] = 1.5,
            ["fluid_str"] = "MPG",
            ["fluid_percent"] = 25.0,
            ["fluid_temperature_C"] = 5.0,
        });

        var g = result.GetProperty("g").EnumerateArray().Select(e => e.GetDouble()).ToArray();
        Assert.Equal(18.241, g[^1], 0.001);
    }

    [Fact]
    public async Task RehauPipeCatalog_Contains32x2_9MatchingTheReportsOwnDimensions()
    {
        var engine = EngineClient.CreateDefault();
        var pipes = await engine.CallAsync("list_pipes", new Dictionary<string, object?> { ["manufacturer"] = "REHAU" });
        var match = pipes.EnumerateArray()
            .First(p => p.GetProperty("key").GetString() == "rehau_raugeo_32x2.9");
        Assert.Equal(0.0131, match.GetProperty("r_in_m").GetDouble(), 0.0001);
        Assert.Equal(0.0160, match.GetProperty("r_out_m").GetDouble(), 0.0001);
    }

    [Fact]
    public async Task RehauGroutCatalog_RedIsHigherConductivityThanBlue()
    {
        var engine = EngineClient.CreateDefault();
        var grouts = await engine.CallAsync("list_grouts", new Dictionary<string, object?> { ["manufacturer"] = "REHAU" });
        var rojo = grouts.EnumerateArray().First(g => g.GetProperty("key").GetString() == "rehau_raugeo_fill_rojo");
        var azul = grouts.EnumerateArray().First(g => g.GetProperty("key").GetString() == "rehau_raugeo_fill_azul");
        Assert.True(rojo.GetProperty("k_g_W_mK").GetDouble() > azul.GetProperty("k_g_W_mK").GetDouble());
    }

    [Fact]
    public async Task PipeManufacturerCatalog_ListsAllFourAndEachHasProducts()
    {
        var engine = EngineClient.CreateDefault();
        var manufacturers = (await engine.CallAsync("list_pipe_manufacturers"))
            .EnumerateArray().Select(m => m.GetString()).ToList();
        Assert.Equal(new[] { "Gerodur", "Muovitech", "Pipelife Bulgaria", "REHAU" }, manufacturers);

        foreach (var manufacturer in manufacturers)
        {
            var pipes = await engine.CallAsync("list_pipes", new Dictionary<string, object?> { ["manufacturer"] = manufacturer });
            Assert.True(pipes.GetArrayLength() > 0, $"{manufacturer} should have at least one pipe");
        }
    }

    [Fact]
    public async Task GroutManufacturerCatalog_IncludesFischerFallbackVariants()
    {
        var engine = EngineClient.CreateDefault();
        var manufacturers = (await engine.CallAsync("list_grout_manufacturers"))
            .EnumerateArray().Select(m => m.GetString()).ToList();
        Assert.Contains("Fischer Spezialbaustoffe", manufacturers);

        var fischerGrouts = await engine.CallAsync("list_grouts", new Dictionary<string, object?> { ["manufacturer"] = "Fischer Spezialbaustoffe" });
        var keys = fischerGrouts.EnumerateArray().Select(g => g.GetProperty("key").GetString()).ToList();
        Assert.Contains("fischer_geosolid_240hs", keys);
        Assert.Contains("fischer_geosolid_235", keys);
    }

    [Fact]
    public async Task DoubleUTubeParallel_LowersEffectiveResistanceVsSingleAtMatchedPerLegFlow()
    {
        // Same pos-array shape MainWindow.BuildPipeConfig() sends for "Double U-tube,
        // parallel" (a cross pattern reusing the shank-spacing value for both loops) --
        // proves the C# nested object[] pos array round-trips through System.Text.Json
        // correctly and the engine accepts multiple_u_tube, not just single_u_tube.
        var engine = EngineClient.CreateDefault();

        var single = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        var doubleParallel = new Dictionary<string, object?>
        {
            ["type"] = "multiple_u_tube",
            ["pos"] = new object[]
            {
                new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 },
                new object[] { 0.0, -0.03 }, new object[] { 0.0, 0.03 },
            },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
            ["nPipes"] = 2, ["config"] = "parallel",
        };

        var commonArgs = new Dictionary<string, object?>
        {
            ["H"] = 150.0, ["D"] = 2.0, ["r_b"] = 0.075, ["k_s"] = 2.0, ["k_g"] = 1.5,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 5.0,
        };

        var singleResult = await engine.CallAsync("effective_borehole_resistance", new Dictionary<string, object?>(commonArgs)
        {
            ["config"] = single, ["m_flow_borehole"] = 0.30,
        });
        // Matched per-leg flow: 0.60 total / 2 parallel legs = 0.30/leg, same as single U-tube.
        var doubleResult = await engine.CallAsync("effective_borehole_resistance", new Dictionary<string, object?>(commonArgs)
        {
            ["config"] = doubleParallel, ["m_flow_borehole"] = 0.60,
        });

        Assert.True(doubleResult.GetProperty("R_b_star_mK_W").GetDouble() < singleResult.GetProperty("R_b_star_mK_W").GetDouble());
    }

    [Fact]
    public async Task DeadbandTower_RunsOnlyOnceGroundRisesAboveSetpoint()
    {
        // Same shape MainWindow.SimulateButton_Click sends for the "Ground-temperature
        // deadband" tower strategy -- one call, no separate apply_cooling_tower step,
        // since the dispatch depends on the ground's own simulated response.
        var engine = EngineClient.CreateDefault();
        var field = await engine.CallAsync("build_rectangle_field", ReportField);

        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };

        // 3 years of heavy, unrelenting cooling load -- the ground has to drift for the
        // deadband controller to ever have a reason to turn the tower on.
        var hourly = new List<object?>();
        for (var h = 0; h < 3 * 8760; h++) hourly.Add(-8000.0);

        var result = await engine.CallAsync("run_hourly_simulation_with_deadband_tower", new Dictionary<string, object?>
        {
            ["field"] = field, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 5.0,
            ["hourly_load_W"] = hourly, ["tower_capacity_kW"] = 20.0, ["tower_deadband_C"] = 0.5,
        });

        var towerLoad = result.GetProperty("tower_load_W").EnumerateArray().Select(x => x.GetDouble()).ToArray();
        Assert.True(result.GetProperty("tower_hours").GetInt32() > 0, "the tower should have turned on at some point over 3 years of relentless cooling");
        Assert.True(towerLoad[0] == 0.0, "hour 0 starts at the undisturbed ground temperature -- the tower has no reason to be on yet");
        Assert.True(result.GetProperty("tower_peak_kW").GetDouble() <= 20.0 + 1e-6);
    }

    [Fact]
    public async Task SizeFieldWithDeadbandTower_SizesShallowerThanWithoutATower()
    {
        // Same shape MainWindow.SizeFieldButton_Click sends for the Sizing tab's
        // "Ground-temperature deadband" strategy -- one call does the whole tower-aware
        // depth bisection, since the controller's behavior depends on each candidate
        // depth's own simulated ground temperature (see hybrid.py's module docstring).
        var engine = EngineClient.CreateDefault();

        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        var fieldTemplate = new Dictionary<string, object?>
        {
            ["layout"] = "rectangle", ["N_1"] = 4, ["N_2"] = 3, ["B_1"] = 6.0, ["B_2"] = 6.0, ["D"] = 2.0, ["r_b"] = 0.075,
        };

        var hourly = new List<object?>();
        for (var h = 0; h < 8760; h++)
        {
            var month = h / 730; // ~one "month" per 730 hours, close enough for a synthetic profile
            hourly.Add(month is >= 4 and <= 8 ? -20000.0 : 3000.0); // heavy summer cooling, light winter heating
        }

        var commonArgs = new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 18.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["hourly_load_W"] = hourly, ["simulation_period_years"] = 5,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 40.0, ["H_min"] = 20.0, ["H_max"] = 400.0,
        };

        var withoutTower = await engine.CallAsync("size_field", commonArgs);
        var withTower = await engine.CallAsync("size_field_with_deadband_tower", new Dictionary<string, object?>(commonArgs)
        {
            ["tower_capacity_kW"] = 60.0, ["tower_deadband_C"] = 0.5,
        });

        Assert.True(withTower.GetProperty("H_m").GetDouble() <= withoutTower.GetProperty("H_m").GetDouble());
        Assert.True(withTower.GetProperty("tower_hours").GetInt32() > 0);
        Assert.Equal(60.0, withTower.GetProperty("tower_capacity_kW").GetDouble());
    }

    [Fact]
    public async Task MinimumDeadbandTowerCapacity_FindsAWorkingCapacityAtACappedDepth()
    {
        // Same shape MainWindow.AreaSizeButton_Click sends when the field alone can't meet
        // the limit at max practical depth and the Area Sizing tab's "Dry cooler strategy"
        // dropdown is set to "Ground-temperature deadband".
        var engine = EngineClient.CreateDefault();

        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        var fieldTemplate = new Dictionary<string, object?>
        {
            ["layout"] = "rectangle", ["N_1"] = 4, ["N_2"] = 3, ["B_1"] = 6.0, ["B_2"] = 6.0, ["D"] = 2.0, ["r_b"] = 0.075,
        };

        var hourly = new List<object?>();
        for (var h = 0; h < 8760; h++)
        {
            var month = h / 730;
            hourly.Add(month is >= 4 and <= 8 ? -20000.0 : 3000.0); // heavy summer cooling, light winter heating
        }

        var result = await engine.CallAsync("minimum_deadband_tower_capacity", new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["H"] = 20.0, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 18.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["hourly_load_W"] = hourly, ["simulation_period_years"] = 5,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 35.0,
            ["capacity_max_kW"] = 300.0, ["tower_deadband_C"] = 0.5,
        });

        Assert.True(result.GetProperty("tower_capacity_kW").GetDouble() > 0, "a 20 m field alone should not meet a 35 C limit on this heavy-cooling profile");
        Assert.True(result.GetProperty("T_f_max_C").GetDouble() <= 35.0 + 1e-6);
        Assert.True(result.GetProperty("tower").GetProperty("tower_hours").GetInt32() > 0);
    }

    [Fact]
    public async Task InvalidBoundaryCondition_SurfacesPythonValueErrorMessage()
    {
        var engine = EngineClient.CreateDefault();
        var field = await engine.CallAsync("build_rectangle_field", ReportField);
        var time = await engine.CallAsync("time_grid", new Dictionary<string, object?>
        {
            ["t_min_s"] = 3600.0,
            ["t_max_s"] = 8760.0 * 3600.0,
            ["num"] = 5,
        });

        var ex = await Assert.ThrowsAsync<EngineException>(() => engine.CallAsync(
            "evaluate_gfunction", new Dictionary<string, object?>
            {
                ["field"] = field,
                ["alpha"] = 1.0e-6,
                ["time"] = time,
                ["boundary_condition"] = "NOT_A_REAL_BC",
            }));
        Assert.Contains("ValueError", ex.Message);
    }
}
