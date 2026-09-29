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
    public async Task ApplyWetBulbTower_LetsASmallerFieldSatisfyTheSameTemperatureLimit()
    {
        // Same shape MainWindow.SimulateButton_Click/SizeFieldButton_Click send for the
        // "Wet-bulb threshold" strategy -- unlike deadband, this is a stateless per-hour
        // transform (see hybrid.py's module docstring), so it's two calls
        // (synthetic_wet_bulb_series then apply_wet_bulb_tower) feeding the SAME
        // size_field command peak-shaving already uses, not a dedicated *_with_*_tower one.
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

        var wetBulb = (await engine.CallAsync("synthetic_wet_bulb_series", new Dictionary<string, object?>
        {
            ["min_wet_bulb_C"] = 0.0, ["max_wet_bulb_C"] = 28.0, ["n_hours"] = 8760,
        })).EnumerateArray().Select(x => (object?)x.GetDouble()).ToList();

        var commonArgs = new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 18.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["simulation_period_years"] = 5,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 40.0, ["H_min"] = 20.0, ["H_max"] = 400.0,
        };

        var withoutTower = await engine.CallAsync("size_field", new Dictionary<string, object?>(commonArgs) { ["hourly_load_W"] = hourly });
        var towerResult = await engine.CallAsync("apply_wet_bulb_tower", new Dictionary<string, object?>
        {
            ["hourly_load_W"] = hourly, ["wet_bulb_C"] = wetBulb, ["threshold_C"] = 20.0, ["tower_capacity_kW"] = 60.0,
        });
        var groundLoad = towerResult.GetProperty("ground_load_W").EnumerateArray().Select(x => (object?)x.GetDouble()).ToList();
        var withTower = await engine.CallAsync("size_field", new Dictionary<string, object?>(commonArgs) { ["hourly_load_W"] = groundLoad });

        Assert.True(towerResult.GetProperty("tower_hours").GetInt32() > 0);
        Assert.True(withTower.GetProperty("H_m").GetDouble() <= withoutTower.GetProperty("H_m").GetDouble());
    }

    [Fact]
    public async Task MinimumWetBulbTowerCapacity_FindsAWorkingCapacityAtACappedDepth()
    {
        // Same shape MainWindow.AreaSizeButton_Click sends when the field alone can't meet
        // the limit at max practical depth and the Area Sizing tab's "Dry cooler strategy"
        // dropdown is set to "Wet-bulb threshold". threshold_C is set above the synthetic
        // series' own max so the tower is never gated off -- see the matching Python test's
        // comment (test_minimum_wet_bulb_tower_capacity_finds_a_working_capacity_at_a_capped_depth)
        // for why a low threshold here would make this specific case genuinely infeasible,
        // not a bug.
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
            hourly.Add(month is >= 4 and <= 8 ? -20000.0 : 3000.0);
        }

        var wetBulb = (await engine.CallAsync("synthetic_wet_bulb_series", new Dictionary<string, object?>
        {
            ["min_wet_bulb_C"] = 0.0, ["max_wet_bulb_C"] = 28.0, ["n_hours"] = 8760,
        })).EnumerateArray().Select(x => (object?)x.GetDouble()).ToList();

        var result = await engine.CallAsync("minimum_wet_bulb_tower_capacity", new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["H"] = 20.0, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 18.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["hourly_load_W"] = hourly, ["simulation_period_years"] = 5,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 35.0,
            ["capacity_max_kW"] = 300.0, ["wet_bulb_C"] = wetBulb, ["threshold_C"] = 35.0,
        });

        Assert.True(result.GetProperty("tower_capacity_kW").GetDouble() > 0, "a 20 m field alone should not meet a 35 C limit on this heavy-cooling profile");
        Assert.True(result.GetProperty("T_f_max_C").GetDouble() <= 35.0 + 1e-6);
        Assert.True(result.GetProperty("tower").GetProperty("tower_hours").GetInt32() > 0);
    }

    [Fact]
    public async Task MinimumWetBulbTowerCapacity_ThrowsDistinctErrorWhenTowerProvidesNoBenefit()
    {
        // Same shape MainWindow.AreaSizeButton_Click sends -- and the exact live scenario that
        // motivated adding this distinction: a threshold low enough that the tower is gated off
        // for this load's entire cooling season, so no capacity, however large, would help. The
        // app catches this specific message to report the field-alone shortfall plainly instead
        // of a raw traceback (see AreaSizeButton_Click's inner try/catch).
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
            hourly.Add(month is >= 4 and <= 8 ? -20000.0 : 3000.0);
        }

        var wetBulb = (await engine.CallAsync("synthetic_wet_bulb_series", new Dictionary<string, object?>
        {
            ["min_wet_bulb_C"] = 0.0, ["max_wet_bulb_C"] = 28.0, ["n_hours"] = 8760,
        })).EnumerateArray().Select(x => (object?)x.GetDouble()).ToList();

        var ex = await Assert.ThrowsAsync<EngineException>(() => engine.CallAsync("minimum_wet_bulb_tower_capacity", new Dictionary<string, object?>
        {
            ["field_template"] = fieldTemplate, ["H"] = 20.0, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 18.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
            ["hourly_load_W"] = hourly, ["simulation_period_years"] = 5,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 35.0,
            ["capacity_max_kW"] = 300.0, ["wet_bulb_C"] = wetBulb, ["threshold_C"] = 15.0,
        }));

        Assert.Contains("provides NO benefit", ex.Message);
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

    [Fact]
    public async Task EstimateGroundPropertiesFromTrt_RecoversKnownValuesFromSyntheticData()
    {
        // Same shape TrtRunButton_Click sends: generate a synthetic TRT curve via
        // run_hourly_simulation at a KNOWN k_s/R_b (mirrors tests/unit/test_trt.py's own
        // validation), then check the fit recovers them through the real subprocess bridge.
        var engine = EngineClient.CreateDefault();
        var singleBorehole = await engine.CallAsync("build_custom_field", new Dictionary<string, object?>
        {
            ["x"] = new object[] { 0.0 }, ["y"] = new object[] { 0.0 },
            ["H"] = 150.0, ["D"] = 2.0, ["r_b"] = 0.075,
        });
        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        const double kSTrue = 2.0;
        const double rhoCp = 2.4e6;
        var hourlyQ = Enumerable.Repeat((object?)(-8000.0), 72).ToList();

        var sim = await engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>
        {
            ["field"] = singleBorehole, ["alpha"] = kSTrue / rhoCp, ["k_s"] = kSTrue, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["hourly_load_W"] = hourlyQ,
        });
        var tF = sim.GetProperty("T_f_C").EnumerateArray().Select(x => (object?)x.GetDouble()).ToList();
        var timeS = Enumerable.Range(0, 72).Select(i => (object?)((i + 1) * 3600.0)).ToList();

        var result = await engine.CallAsync("estimate_ground_properties_from_trt", new Dictionary<string, object?>
        {
            ["H"] = 150.0, ["D"] = 2.0, ["r_b"] = 0.075, ["time_s"] = timeS, ["T_f_C"] = tF, ["Q_W"] = hourlyQ,
            ["T_g"] = 12.0, ["rho_cp_J_m3K"] = rhoCp,
            ["k_s_guess"] = 1.5, ["R_b_guess"] = 0.10, ["t_min_s"] = 12 * 3600.0,
        });

        Assert.InRange(result.GetProperty("k_s_W_mK").GetDouble(), kSTrue * 0.9, kSTrue * 1.1);
        Assert.False(result.GetProperty("R_b_was_fixed").GetBoolean());
        Assert.Equal(72, result.GetProperty("n_points_total").GetInt32());
    }

    [Fact]
    public async Task SizeFieldMonteCarlo_ReportsP50P90ThroughTheRealSubprocessContract()
    {
        // Same shape SizeFieldButton_Click's Monte Carlo branch sends: base_kwargs is exactly
        // what a plain size_field call already builds, plus uncertain_inputs/n_samples.
        var engine = EngineClient.CreateDefault();
        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        var hourly = (await engine.CallAsync("synthesize_hourly_load", new Dictionary<string, object?>
        {
            ["baseload_heating_kWh"] = new object[] { 3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800 },
            ["baseload_cooling_kWh"] = new object[] { 0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0 },
            ["peak_heating_kW"] = new object[] { 25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24 },
            ["peak_cooling_kW"] = new object[] { 0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0 },
        })).EnumerateArray().Select(x => (object?)x.GetDouble()).ToList();

        var baseKwargs = new Dictionary<string, object?>
        {
            ["field_template"] = new Dictionary<string, object?>
            {
                ["layout"] = "rectangle", ["N_1"] = 4, ["N_2"] = 3, ["B_1"] = 6.0, ["B_2"] = 6.0, ["D"] = 2.0, ["r_b"] = 0.075,
            },
            ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0, ["pipe_config"] = pipe,
            ["m_flow_borehole"] = 0.30, ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["hourly_load_W"] = hourly, ["simulation_period_years"] = 1,
            ["T_f_min_limit_C"] = -2.0, ["T_f_max_limit_C"] = 20.0, ["H_min"] = 20.0, ["H_max"] = 150.0,
            ["tol_m"] = 3.0, ["max_iter"] = 10,
        };
        var uncertainInputs = new Dictionary<string, object?>
        {
            ["k_s"] = new Dictionary<string, object?> { ["dist"] = "normal", ["mean"] = 2.0, ["stddev"] = 0.2 },
        };

        var result = await engine.CallAsync("size_field_monte_carlo", new Dictionary<string, object?>
        {
            ["base_kwargs"] = baseKwargs, ["uncertain_inputs"] = uncertainInputs, ["n_samples"] = 10, ["random_seed"] = 1,
        });

        Assert.Equal(10, result.GetProperty("n_samples").GetInt32());
        var percentiles = result.GetProperty("percentiles").EnumerateArray().ToList();
        Assert.Equal(2, percentiles.Count);
        var p50 = percentiles.First(p => p.GetProperty("p").GetDouble() == 50.0).GetProperty("H_m").GetDouble();
        var p90 = percentiles.First(p => p.GetProperty("p").GetDouble() == 90.0).GetProperty("H_m").GetDouble();
        Assert.True(p90 >= p50);
    }

    [Fact]
    public async Task RunHourlySimulation_MiftOption_ReportsWhichBoundaryConditionWasUsed()
    {
        // Same shape SimulateButton_Click sends once "MIFT" is selected -- proves the new
        // gfunc_boundary_condition parameter round-trips through the real subprocess, and that
        // the default (omitted) still reproduces plain UBWT behavior unchanged.
        var engine = EngineClient.CreateDefault();
        var field = await engine.CallAsync("build_rectangle_field", ReportField);
        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        var hourlyQ = Enumerable.Range(1, 8760).Select(h => (object?)(25e3 * Math.Cos(2 * Math.PI * h / 8760) + 8e3)).ToList();

        var commonArgs = new Dictionary<string, object?>
        {
            ["field"] = field, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["hourly_load_W"] = hourlyQ,
        };

        var ubwtDefault = await engine.CallAsync("run_hourly_simulation", commonArgs);
        Assert.Equal("UBWT", ubwtDefault.GetProperty("gfunc_boundary_condition").GetString());

        var mift = await engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>(commonArgs)
        {
            ["gfunc_boundary_condition"] = "MIFT",
        });
        Assert.Equal("MIFT", mift.GetProperty("gfunc_boundary_condition").GetString());
    }

    [Fact]
    public async Task RunHourlySimulation_DriftAndMultipoleOrder_RoundTripThroughTheRealSubprocess()
    {
        // Same shape MainWindow's commonSimArgs sends once the "Advanced physics" group's
        // Ground temp. drift / Multipole order boxes are non-default -- proves both new
        // simulation.run_hourly_simulation parameters actually reach the engine and change
        // the result (not silently ignored), and that T_g_drift_C_per_year=0.0 with
        // multipole_order=2 (the boxes' own XAML defaults) reproduces the original result.
        var engine = EngineClient.CreateDefault();
        var field = await engine.CallAsync("build_rectangle_field", ReportField);
        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        var commonArgs = new Dictionary<string, object?>
        {
            ["field"] = field, ["alpha"] = 1.0e-6, ["k_s"] = 2.0, ["k_g"] = 1.5, ["T_g"] = 12.0,
            ["pipe_config"] = pipe, ["m_flow_borehole"] = 0.30,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 0.0,
            ["hourly_load_W"] = Enumerable.Repeat((object?)4000.0, 8760).ToList(),
        };

        var baseline = await engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>(commonArgs)
        {
            ["T_g_drift_C_per_year"] = 0.0, ["multipole_order"] = 2,
        });
        var defaultOmitted = await engine.CallAsync("run_hourly_simulation", commonArgs);
        Assert.Equal(
            baseline.GetProperty("T_f_C").EnumerateArray().Last().GetDouble(),
            defaultOmitted.GetProperty("T_f_C").EnumerateArray().Last().GetDouble());

        var drifted = await engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>(commonArgs)
        {
            ["T_g_drift_C_per_year"] = 0.5,
        });
        Assert.True(
            drifted.GetProperty("T_f_C").EnumerateArray().Last().GetDouble() >
            baseline.GetProperty("T_f_C").EnumerateArray().Last().GetDouble());
    }

    [Fact]
    public async Task WeightedAverageGroundProperties_MatchesHandComputation()
    {
        // Same shape ComputeLayeredGroundButton_Click sends: 2 layers, borehole spans [2, 152] m.
        var engine = EngineClient.CreateDefault();
        var layers = new List<object?>
        {
            new Dictionary<string, object?> { ["top_m"] = 0.0, ["bottom_m"] = 20.0, ["k_s"] = 1.5, ["rho_cp_J_m3K"] = 2.2e6, ["T_g_C"] = 10.0 },
            new Dictionary<string, object?> { ["top_m"] = 20.0, ["bottom_m"] = 200.0, ["k_s"] = 3.0, ["rho_cp_J_m3K"] = 2.5e6, ["T_g_C"] = 13.0 },
        };
        var result = await engine.CallAsync("weighted_average_ground_properties", new Dictionary<string, object?>
        {
            ["layers"] = layers, ["D"] = 2.0, ["H"] = 150.0,
        });
        var kExpected = (1.5 * 18.0 + 3.0 * 132.0) / 150.0;
        Assert.Equal(kExpected, result.GetProperty("k_s_eff").GetDouble(), 1e-6);
        Assert.True(result.TryGetProperty("T_g_eff_C", out _));
    }

    [Fact]
    public async Task BoreholeThermalCapacitance_ReportsAPositiveCapacitanceFromRealGeometry()
    {
        // Same shape BuildBoreholeCapacitanceJmKAsync sends.
        var engine = EngineClient.CreateDefault();
        var pipe = new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -0.03, 0.0 }, new object[] { 0.03, 0.0 } },
            ["r_in"] = 0.0131, ["r_out"] = 0.0160, ["k_p"] = 0.4,
        };
        var result = await engine.CallAsync("borehole_thermal_capacitance", new Dictionary<string, object?>
        {
            ["config"] = pipe, ["r_b"] = 0.075,
            ["rho_cp_grout_J_m3K"] = 3.8e6, ["rho_cp_pipe_J_m3K"] = 1.5e6,
            ["fluid_str"] = "MPG", ["fluid_percent"] = 25.0, ["fluid_temperature_C"] = 20.0,
        });
        Assert.True(result.GetProperty("C_b_J_mK").GetDouble() > 0);
    }

    [Fact]
    public async Task GroundwaterSteadyStateEffect_DownstreamColderThanUpstreamUnderExtraction()
    {
        // Same shape GroundwaterCheckButton_Click sends.
        var engine = EngineClient.CreateDefault();
        var result = await engine.CallAsync("groundwater_steady_state_effect", new Dictionary<string, object?>
        {
            ["q_prime_W_m"] = 25.0, ["r_b"] = 0.075, ["k_s"] = 2.0, ["alpha"] = 1.0e-6,
            ["darcy_velocity_m_s"] = 5.0e-6, ["rho_cp_water_J_m3K"] = 4.18e6, ["rho_cp_soil_J_m3K"] = 2.3e6,
        });
        var down = result.GetProperty("delta_T_downstream_C").GetDouble();
        var up = result.GetProperty("delta_T_upstream_C").GetDouble();
        Assert.True(down < 0 && up < 0 && Math.Abs(down) > Math.Abs(up));
    }

    [Fact]
    public async Task ApplyHeatPump_HeatingEnergyBalanceHoldsThroughTheRealSubprocess()
    {
        // Same shape SimulateButton_Click's heat-pump refinement loop sends.
        var engine = EngineClient.CreateDefault();
        var heatingCurve = new object?[] { new[] { -5.0, 3.0 }, new[] { 0.0, 3.5 }, new[] { 10.0, 4.5 }, new[] { 20.0, 5.5 } };
        var coolingCurve = new object?[] { new[] { 10.0, 4.0 }, new[] { 20.0, 5.0 }, new[] { 30.0, 6.0 }, new[] { 40.0, 4.5 } };
        var result = await engine.CallAsync("apply_heat_pump", new Dictionary<string, object?>
        {
            ["building_load_W"] = new object?[] { 10000.0 }, ["eft_C"] = new object?[] { 0.0 },
            ["cop_heating_curve"] = heatingCurve, ["cop_cooling_curve"] = coolingCurve,
        });
        var groundLoad = result.GetProperty("ground_load_W").EnumerateArray().First().GetDouble();
        var electricalPower = result.GetProperty("electrical_power_W").EnumerateArray().First().GetDouble();
        Assert.Equal(10000.0, groundLoad + electricalPower, 1e-6);
    }
}
