using System.Collections.ObjectModel;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace HC.GeoBore;

public sealed class GfunctionRow
{
    public double TimeYears { get; init; }
    public double LnTTs { get; init; }
    public double G { get; init; }
}

public sealed class MonthlyLoadRow
{
    public string Month { get; set; } = "";
    public double BaseHeatingKWh { get; set; }
    public double BaseCoolingKWh { get; set; }
    public double PeakHeatingKW { get; set; }
    public double PeakCoolingKW { get; set; }
}

public sealed class YearlySummaryRow
{
    public int Year { get; init; }
    public double TFluidMinC { get; init; }
    public double TFluidMaxC { get; init; }
    public double TWallMinC { get; init; }
    public double TWallMaxC { get; init; }
}

public sealed class TrtRow
{
    public int Hour { get; init; }
    public double QKW { get; set; }
    public double TFMeasuredC { get; set; }
}

public sealed class GroundLayerRow
{
    public double TopM { get; set; }
    public double BottomM { get; set; }
    public double KS { get; set; }
    public double RhoCp { get; set; }
    // Null (blank cell) means "not supplied" -- geothermal.ground.weighted_average_ground_properties
    // requires EITHER every layer to have one or none of them, never a partial mix.
    public double? TgC { get; set; }
}

public sealed class CopCurveRow
{
    public double EftC { get; set; }
    public double Cop { get; set; }
}

public partial class MainWindow : Window
{
    private const double SecondsPerYear = 8760.0 * 3600.0;
    private const int HoursPerYear = 8760;
    private const int GfunctionTabIndex = 0;
    private const int SizingTabIndex = 2;
    private const int AreaSizingTabIndex = 3;
    private const int TrtTabIndex = 4;

    private static readonly string[] MonthNames =
        { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
    // A made-up but plausible heating-dominated profile, just so the load-profile grid is
    // runnable out of the box -- same defaults used in tests/unit/test_sizing_cross_check.py.
    private static readonly double[] DefaultBaseHeating = { 3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800 };
    private static readonly double[] DefaultBaseCooling = { 0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0 };
    private static readonly double[] DefaultPeakHeating = { 25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24 };
    private static readonly double[] DefaultPeakCooling = { 0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0 };

    private readonly EngineClient? _engine;
    private readonly ObservableCollection<GfunctionRow> _rows = new();
    private readonly ObservableCollection<MonthlyLoadRow> _loadRows = new();
    private readonly ObservableCollection<YearlySummaryRow> _simYearlyRows = new();
    private readonly ObservableCollection<TrtRow> _trtRows = new();
    private readonly ObservableCollection<GroundLayerRow> _groundLayerRows = new();
    private readonly ObservableCollection<CopCurveRow> _heatPumpHeatingCurveRows = new();
    private readonly ObservableCollection<CopCurveRow> _heatPumpCoolingCurveRows = new();
    private List<JsonElement> _pipeCatalog = new();
    private List<JsonElement> _groutCatalog = new();
    private List<JsonElement> _currentPipeProducts = new();
    private List<JsonElement> _currentGroutProducts = new();

    private string? _currentProjectPath;
    private List<ProjectIteration> _openIterations = new();
    private DispatcherTimer? _autosaveTimer;

    public MainWindow()
    {
        InitializeComponent();
        ResultsGrid.ItemsSource = _rows;
        LoadGrid.ItemsSource = _loadRows;
        Sim_YearlyGrid.ItemsSource = _simYearlyRows;
        TrtGrid.ItemsSource = _trtRows;
        GroundLayerGrid.ItemsSource = _groundLayerRows;
        HeatPumpHeatingCurveGrid.ItemsSource = _heatPumpHeatingCurveRows;
        HeatPumpCoolingCurveGrid.ItemsSource = _heatPumpCoolingCurveRows;
        InitializeCharts();

        foreach (var row in BuildDefaultMonthlyLoadRows())
            _loadRows.Add(row);
        foreach (var row in BuildExampleTrtRows())
            _trtRows.Add(row);
        // Clearly-marked example log (2 layers), just so the grid opens runnable -- same
        // precedent as BuildExampleTrtRows. Replace with a real geological log before relying
        // on this.
        _groundLayerRows.Add(new GroundLayerRow { TopM = 0.0, BottomM = 20.0, KS = 1.5, RhoCp = 2.2e6, TgC = 10.0 });
        _groundLayerRows.Add(new GroundLayerRow { TopM = 20.0, BottomM = 200.0, KS = 3.0, RhoCp = 2.5e6, TgC = 13.0 });
        // Clearly-made-up but plausible example COP curves, same "runnable out of the box,
        // replace before relying on it" precedent as the other example data grids above.
        foreach (var (eft, cop) in new[] { (-5.0, 3.0), (0.0, 3.5), (10.0, 4.5), (20.0, 5.5) })
            _heatPumpHeatingCurveRows.Add(new CopCurveRow { EftC = eft, Cop = cop });
        foreach (var (eft, cop) in new[] { (10.0, 4.0), (20.0, 5.0), (30.0, 6.0), (40.0, 4.5) })
            _heatPumpCoolingCurveRows.Add(new CopCurveRow { EftC = eft, Cop = cop });

        try
        {
            _engine = EngineClient.CreateDefault();
        }
        catch (Exception ex)
        {
            _engine = null;
            StatusText.Text = $"Engine not available: {ex.Message}";
        }

        RebuildRecentProjectsMenu();
        SetupAutosaveTimer();
        UpdateWindowTitle();
    }

    private static List<MonthlyLoadRow> BuildDefaultMonthlyLoadRows()
    {
        var rows = new List<MonthlyLoadRow>();
        for (var i = 0; i < 12; i++)
        {
            rows.Add(new MonthlyLoadRow
            {
                Month = MonthNames[i],
                BaseHeatingKWh = DefaultBaseHeating[i],
                BaseCoolingKWh = DefaultBaseCooling[i],
                PeakHeatingKW = DefaultPeakHeating[i],
                PeakCoolingKW = DefaultPeakCooling[i],
            });
        }
        return rows;
    }

    // Placeholder shape for a 48-hour constant-injection TRT (asymptotic log-time rise, as
    // line-source theory predicts) -- clearly example data, not a real test, per the tab's own
    // hint text; replace before running on a real project.
    private static List<TrtRow> BuildExampleTrtRows()
    {
        const double exampleTG = 12.0;
        const double exampleQKW = -8.0;
        var rows = new List<TrtRow>();
        for (var h = 1; h <= 48; h++)
            rows.Add(new TrtRow { Hour = h, QKW = exampleQKW, TFMeasuredC = Math.Round(exampleTG + 3.2 * Math.Log(h + 1), 2) });
        return rows;
    }

    private async void Window_Loaded(object sender, RoutedEventArgs e)
    {
        UpdateSharedInputAvailability();

        if (_engine is null)
            return;

        try
        {
            var pipesResult = await _engine.CallAsync("list_pipes");
            _pipeCatalog = pipesResult.EnumerateArray().ToList();
            var pipeManufacturers = await _engine.CallAsync("list_pipe_manufacturers");
            foreach (var m in pipeManufacturers.EnumerateArray())
                PipeManufacturerCombo.Items.Add(new ComboBoxItem { Content = m.GetString() });

            var groutsResult = await _engine.CallAsync("list_grouts");
            _groutCatalog = groutsResult.EnumerateArray().ToList();
            var groutManufacturers = await _engine.CallAsync("list_grout_manufacturers");
            foreach (var m in groutManufacturers.EnumerateArray())
                GroutManufacturerCombo.Items.Add(new ComboBoxItem { Content = m.GetString() });
        }
        catch (EngineException ex)
        {
            StatusText.Text = $"Could not load vendor catalog: {ex.Message}";
        }

        // Deliberately left unselected (no default "Custom") -- the dependent fields below
        // (grout k_g, pipe r_in/r_out/k_p, fluid/percent/temperature) start disabled and blank
        // until the user actively picks something from both dropdowns, so nothing gets
        // calculated against a stale or accidental default value.
        UpdatePipeGroutDependentFieldsAvailability();
    }

    private void ModeTabControl_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        // TabControl.SelectionChanged is a routed event that also bubbles up from any
        // ComboBox inside the active tab (BoundaryConditionCombo, Sim_AlgorithmCombo, etc,
        // since ComboBox is a Selector too) -- ignore anything that isn't the tab switch itself.
        if (!ReferenceEquals(e.OriginalSource, ModeTabControl))
            return;

        UpdateSharedInputAvailability();
    }

    private void UpdateSharedInputAvailability()
    {
        var index = ModeTabControl.SelectedIndex;

        var isSizing = index == SizingTabIndex;
        var isAreaSizing = index == AreaSizingTabIndex;

        // H is solved for, not entered, on both Sizing (via H_min/H_max) and Area Sizing
        // (H_max is the "max practical depth" input there).
        DepthBox.IsEnabled = !isSizing && !isAreaSizing;
        DepthHintText.Visibility = (isSizing || isAreaSizing) ? System.Windows.Visibility.Visible : System.Windows.Visibility.Collapsed;

        var isTrt = index == TrtTabIndex;
        // N1/N2 are derived from the available area on Area Sizing, not entered directly; TRT is a
        // single test borehole, so a field layout doesn't apply there either.
        N1Box.IsEnabled = !isAreaSizing && !isTrt;
        N2Box.IsEnabled = !isAreaSizing && !isTrt;
        N1N2HintText.Text = isTrt
            ? "(ignored on TRT Analysis -- a TRT is run on one test borehole, not a field)"
            : "(ignored on Area Sizing -- derived from area there instead)";
        N1N2HintText.Visibility = (isAreaSizing || isTrt) ? System.Windows.Visibility.Visible : System.Windows.Visibility.Collapsed;

        var isGfunction = index == GfunctionTabIndex;
        // The monthly load grid has no role on g-Function (no load concept), Area Sizing (which
        // synthesizes its own load from the two peak values instead), or TRT (its own log-data
        // grid replaces it).
        LoadProfileGroup.IsEnabled = !isGfunction && !isAreaSizing && !isTrt;
        // The tower group is a manual "what if" input on Simulation/Sizing; on Area Sizing the
        // tower capacity (if any) is instead a solved-for output, shown in that tab's results; TRT
        // doesn't run a tower at all.
        TowerGroup.IsEnabled = !isGfunction && !isAreaSizing && !isTrt;
        // MIFT/UBWT choice only applies where this engine actually sizes/simulates against a
        // g-function -- not on g-Function (which has its own BoundaryConditionCombo already), Area
        // Sizing (not wired through that tab's own sizing calls yet), or TRT (a different fit
        // entirely, always solved via UBWT machinery -- see geothermal/trt.py's own docstring).
        SimSizeBoundaryConditionGroup.IsEnabled = !isGfunction && !isAreaSizing && !isTrt;
        // Ground drift / multipole order / short-term capacitance: same gating as the g-function
        // accuracy group -- only meaningful where this engine actually sizes/simulates.
        AdvancedPhysicsGroup.IsEnabled = !isGfunction && !isAreaSizing && !isTrt;
    }

    // "UBWT" (default, fast) or "MIFT" (true fluid temperature, slower) -- see
    // geothermal.simulation.run_hourly_simulation's own gfunc_boundary_condition docstring.
    private string GetGfuncBoundaryCondition() =>
        SelectedText(SimSizeBoundaryConditionCombo).StartsWith("MIFT", StringComparison.OrdinalIgnoreCase) ? "MIFT" : "UBWT";

    // The three control strategies geothermal/hybrid.py implements -- kept as one enum (rather
    // than repeating .Contains("deadband")/.Contains("wet-bulb") checks at every call site) since
    // both TowerStrategyCombo (shared panel) and Area_TowerStrategyCombo (Area Sizing's own picker)
    // need the same 3-way classification in several places.
    private enum TowerStrategy { PeakShaving, Deadband, WetBulbThreshold }

    private static TowerStrategy GetTowerStrategy(ComboBox combo)
    {
        var text = SelectedText(combo);
        if (text.Contains("deadband", StringComparison.OrdinalIgnoreCase)) return TowerStrategy.Deadband;
        if (text.Contains("wet-bulb", StringComparison.OrdinalIgnoreCase)) return TowerStrategy.WetBulbThreshold;
        return TowerStrategy.PeakShaving;
    }

    // A tower/dry cooler that never actually engages (0 hours, e.g. a wet-bulb threshold that
    // never dips below the load's own cooling season) already has ZERO numeric effect --
    // ground_load_W comes back identical to the untouched load either way. But leaving its
    // "ran 0 h/yr, 0 kWh/yr, peak duty 0.0 kW" line in the results implies it's part of the
    // design when it isn't, so every tower-reporting call site checks this first and reports
    // the plain no-tower result instead, with a short note explaining why.
    private static bool TowerHadNoEffect(JsonElement towerSummary) =>
        towerSummary.GetProperty("tower_hours").GetInt32() == 0;

    private void TowerEnabledCheckBox_Changed(object sender, RoutedEventArgs e) => UpdateTowerControlsAvailability();

    private void TowerStrategyCombo_SelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateTowerControlsAvailability();

    private void UpdateTowerControlsAvailability()
    {
        // TowerStrategyCombo's own XAML-set SelectedIndex="0" fires TowerStrategyCombo_SelectionChanged during
        // InitializeComponent(), before TowerDeadbandPanel/TowerWbtPanel/TowerStrategyHintText (declared after
        // it in the same GroupBox) exist -- guard on the LAST control this method touches, not the one whose
        // own event fired.
        if (TowerStrategyHintText == null) return;
        var enabled = TowerEnabledCheckBox.IsChecked == true;
        // Monte Carlo sizing (Sizing tab) only wraps the plain size_field call -- flag the
        // conflict here too, at the same place tower state is otherwise tracked, so it's visible
        // as soon as the tower is turned on rather than only when Size Field is clicked.
        Size_McTowerHintText.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
        TowerCapacityBox.IsEnabled = enabled;
        TowerStrategyCombo.IsEnabled = enabled;
        var strategy = enabled ? GetTowerStrategy(TowerStrategyCombo) : TowerStrategy.PeakShaving;
        TowerDeadbandPanel.Visibility = strategy == TowerStrategy.Deadband ? Visibility.Visible : Visibility.Collapsed;
        TowerWbtPanel.Visibility = strategy == TowerStrategy.WetBulbThreshold ? Visibility.Visible : Visibility.Collapsed;
        TowerStrategyHintText.Text = strategy switch
        {
            TowerStrategy.Deadband =>
                "Ground-temperature deadband: the tower turns on once the borehole wall rises Deadband (C) above the undisturbed ground temperature, and stays on until it falls back to that temperature -- fewer tower run-hours than peak-shaving for a similar effect on long-term ground temperature. See geothermal/hybrid.py's deadband_tower_controller (Yu et al. 2026, https://doi.org/10.3390/buildings16183714).",
            TowerStrategy.WetBulbThreshold =>
                "Wet-bulb threshold: the tower runs whenever a synthetic outdoor wet-bulb temperature (annual sine cycle between Min/Max outdoor WBT -- no real weather-file import yet) is below the threshold, regardless of ground temperature. Yu et al. 2026's outdoor-temperature-based control strategy -- see geothermal/hybrid.py's apply_wet_bulb_tower.",
            _ =>
                "Peak-shaving: in every cooling hour the tower removes up to this much heat rejection from what the ground sees, never more than its own rating. Ignores wet-bulb performance -- see geothermal/hybrid.py.",
        };
    }

    private void CapacitanceEnabledCheckBox_Changed(object sender, RoutedEventArgs e)
    {
        var enabled = CapacitanceEnabledCheckBox.IsChecked == true;
        GroutRhoCpBox.IsEnabled = enabled;
        PipeRhoCpBox.IsEnabled = enabled;
    }

    private void HeatPumpEnabledCheckBox_Changed(object sender, RoutedEventArgs e)
    {
        if (HeatPumpCurvesPanel == null) return; // guard against the early-firing routed event during InitializeComponent()
        HeatPumpCurvesPanel.IsEnabled = HeatPumpEnabledCheckBox.IsChecked == true;
    }

    // Computes C_b (J/m.K) from the shared panel's own pipe config/borehole radius/fluid,
    // for the "short-term borehole thermal capacitance" toggle -- returns null when the
    // checkbox is unchecked, so callers can pass it straight through as
    // borehole_capacitance_J_mK without a separate branch.
    private async Task<double?> BuildBoreholeCapacitanceJmKAsync(Dictionary<string, object?> pipeConfig, double boreholeRadius)
    {
        if (_engine is null || CapacitanceEnabledCheckBox.IsChecked != true)
            return null;
        var result = await _engine.CallAsync("borehole_thermal_capacitance", new Dictionary<string, object?>
        {
            ["config"] = pipeConfig, ["r_b"] = boreholeRadius,
            ["rho_cp_grout_J_m3K"] = ParseDouble(GroutRhoCpBox, "Grout rho.cp"),
            ["rho_cp_pipe_J_m3K"] = ParseDouble(PipeRhoCpBox, "Pipe rho.cp"),
            ["fluid_str"] = SelectedText(FluidCombo),
            ["fluid_percent"] = ParseDouble(FluidPercentBox, "Fluid percent"),
            ["fluid_temperature_C"] = ParseDouble(FluidTemperatureBox, "Fluid temperature"),
        });
        return result.GetProperty("C_b_J_mK").GetDouble();
    }

    private async void GroundwaterCheckButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            GroundwaterResultText.Text = "Engine not available.";
            return;
        }
        try
        {
            var result = await _engine.CallAsync("groundwater_steady_state_effect", new Dictionary<string, object?>
            {
                ["q_prime_W_m"] = ParseDouble(GroundwaterQPrimeBox, "Heat rate q'"),
                ["r_b"] = ParseDouble(BoreholeRadiusBox, "Borehole radius"),
                ["k_s"] = ParseDouble(ConductivityBox, "Ground conductivity"),
                ["alpha"] = ParseDouble(AlphaBox, "Ground diffusivity"),
                ["darcy_velocity_m_s"] = ParseDouble(DarcyVelocityBox, "Darcy velocity"),
                ["rho_cp_water_J_m3K"] = ParseDouble(WaterRhoCpBox, "Water rho.cp"),
                ["rho_cp_soil_J_m3K"] = ParseDouble(FormationRhoCpBox, "Saturated formation rho.cp"),
            });
            var peclet = result.GetProperty("peclet_number").GetDouble();
            var dtDown = result.GetProperty("delta_T_downstream_C").GetDouble();
            var dtUp = result.GetProperty("delta_T_upstream_C").GetDouble();
            GroundwaterResultText.Text =
                $"Peclet number: {peclet:F3}. Steady-state change at borehole wall -- downstream: {dtDown:+0.00;-0.00} C, upstream: {dtUp:+0.00;-0.00} C. " +
                (peclet < 0.1
                    ? "Pe << 1: groundwater flow has little effect at the borehole itself."
                    : "Pe not negligible: the downstream/upstream asymmetry above may be design-relevant -- see geothermal/README.md's 'Groundwater/Darcy advection'.");
        }
        catch (EngineException ex)
        {
            GroundwaterResultText.Text = $"Error: {ex.Message}";
        }
    }

    private async void ComputeLayeredGroundButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            LayeredGroundResultText.Text = "Engine not available.";
            return;
        }
        try
        {
            var layers = _groundLayerRows.Select(r => new Dictionary<string, object?>
            {
                ["top_m"] = r.TopM, ["bottom_m"] = r.BottomM, ["k_s"] = r.KS, ["rho_cp_J_m3K"] = r.RhoCp,
                ["T_g_C"] = r.TgC,
            }).ToList();
            var result = await _engine.CallAsync("weighted_average_ground_properties", new Dictionary<string, object?>
            {
                ["layers"] = layers,
                ["D"] = ParseDouble(LayerDBox, "Buried depth D"),
                ["H"] = ParseDouble(LayerHBox, "Borehole length H"),
            });
            var kEff = result.GetProperty("k_s_eff").GetDouble();
            var alphaEff = result.GetProperty("alpha_eff_m2_s").GetDouble();
            AlphaBox.Text = alphaEff.ToString("G6", CultureInfo.InvariantCulture);
            ConductivityBox.Text = kEff.ToString("G6", CultureInfo.InvariantCulture);
            var tgText = "";
            if (result.TryGetProperty("T_g_eff_C", out var tgEffProp))
            {
                var tgEff = tgEffProp.GetDouble();
                GroundTempBox.Text = tgEff.ToString("G6", CultureInfo.InvariantCulture);
                tgText = $", T_g={tgEff:F2} C";
            }
            LayeredGroundResultText.Text =
                $"Written to Ground properties above: k_s={kEff:F3} W/m.K, alpha={alphaEff:E3} m2/s{tgText}.";
        }
        catch (EngineException ex)
        {
            LayeredGroundResultText.Text = $"Error: {ex.Message}";
        }
    }

    // Area Sizing's own dry-cooler strategy picker (separate from the shared TowerStrategyCombo
    // above, since the shared Hybrid cooling tower panel is disabled on this tab -- capacity here
    // is a solved-for OUTPUT, not something the user enters, so there is nothing to enable/disable,
    // only which strategy panel is visible).
    private void Area_TowerStrategyCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        // Same early-firing gotcha as TowerStrategyCombo above: guard on the LAST-declared named
        // sibling (Area_TowerWbtPanel), not the combo whose own SelectedIndex="0" fired this.
        if (Area_TowerWbtPanel == null) return;
        var strategy = GetTowerStrategy(Area_TowerStrategyCombo);
        Area_TowerDeadbandPanel.Visibility = strategy == TowerStrategy.Deadband ? Visibility.Visible : Visibility.Collapsed;
        Area_TowerWbtPanel.Visibility = strategy == TowerStrategy.WetBulbThreshold ? Visibility.Visible : Visibility.Collapsed;
    }

    private void Area_UseAmbientCheckBox_Changed(object sender, RoutedEventArgs e)
    {
        // IsChecked="True" in XAML fires this Checked event during InitializeComponent(),
        // before the sibling TextBox declared after this CheckBox has been wired up yet.
        if (Area_AmbientAreaBox == null) return;
        Area_AmbientAreaBox.IsEnabled = Area_UseAmbientCheckBox.IsChecked == true;
    }

    private void Area_UseUnderBuildingCheckBox_Changed(object sender, RoutedEventArgs e)
    {
        if (Area_UnderBuildingAreaBox == null) return;
        Area_UnderBuildingAreaBox.IsEnabled = Area_UseUnderBuildingCheckBox.IsChecked == true;
    }

    private void RepopulatePipeProductCombo()
    {
        PipeProductCombo.Items.Clear();
        PipeProductCombo.Items.Add(new ComboBoxItem { Content = "Custom (enter manually below)" });
        _currentPipeProducts = PipeManufacturerCombo.SelectedIndex > 0
            ? _pipeCatalog.Where(p => p.GetProperty("manufacturer").GetString() == SelectedText(PipeManufacturerCombo)).ToList()
            : new List<JsonElement>();
        foreach (var pipe in _currentPipeProducts)
            PipeProductCombo.Items.Add(new ComboBoxItem { Content = pipe.GetProperty("name").GetString() });
        PipeProductCombo.SelectedIndex = -1;
    }

    private void RepopulateGroutProductCombo()
    {
        GroutProductCombo.Items.Clear();
        GroutProductCombo.Items.Add(new ComboBoxItem { Content = "Custom (enter manually below)" });
        _currentGroutProducts = GroutManufacturerCombo.SelectedIndex > 0
            ? _groutCatalog.Where(g => g.GetProperty("manufacturer").GetString() == SelectedText(GroutManufacturerCombo)).ToList()
            : new List<JsonElement>();
        foreach (var grout in _currentGroutProducts)
            GroutProductCombo.Items.Add(new ComboBoxItem { Content = grout.GetProperty("name").GetString() });
        GroutProductCombo.SelectedIndex = -1;
    }

    private void PipeManufacturerCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (PipeProductCombo == null) return; // guard: XAML doesn't preselect this combo, kept for safety
        RepopulatePipeProductCombo();
    }

    private void GroutManufacturerCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (GroutProductCombo == null) return;
        RepopulateGroutProductCombo();
    }

    // GHEtool's MultipleUTube pipe model always uses pygfunction's default *parallel* hydraulic
    // wiring (see geothermal/sizing_ghetool.py's _ghetool_pipe_data), so it has no way to
    // represent a "series"-wired double U-tube -- sizing_ghetool_client would raise
    // NotImplementedError. Disabling the checkbox up front avoids that surfacing as a
    // traceback the user has to hit before they learn it's not a supported combination.
    private void Size_McEnabledCheckBox_Changed(object sender, RoutedEventArgs e)
    {
        // No XAML-set IsChecked on this box, so this can't fire during InitializeComponent --
        // consistent with TowerEnabledCheckBox's own (unguarded) Changed handler.
        var enabled = Size_McEnabledCheckBox.IsChecked == true;
        Size_McPanel.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
        if (enabled)
        {
            // Read-only "mean" fields always mirror the left panel's current k_s/T_g -- there is
            // deliberately no separate editable mean here, so the Monte Carlo run can never size
            // around a different k_s/T_g than what the rest of the app is showing.
            Size_McKsMeanText.Text = ConductivityBox.Text;
            Size_McTgMeanText.Text = GroundTempBox.Text;
        }
    }

    private void PipeConfigCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (Size_CrossCheckCheckBox == null) return; // early-firing guard: PipeConfigCombo pre-selects index 0 in XAML
        var isSeries = SelectedText(PipeConfigCombo).Contains("series", StringComparison.OrdinalIgnoreCase);
        if (isSeries) Size_CrossCheckCheckBox.IsChecked = false;
        Size_CrossCheckCheckBox.IsEnabled = !isSeries;
        Size_CrossCheckHintText.Visibility = isSeries ? Visibility.Visible : Visibility.Collapsed;
    }

    private void ApplyPipeCatalogSelection(ComboBox combo, TextBox rInBox, TextBox rOutBox, TextBox kPBox)
    {
        var index = combo.SelectedIndex;
        if (index <= 0 || index - 1 >= _currentPipeProducts.Count)
            return; // "Custom" or nothing selected yet: leave the manual fields as they are

        var pipe = _currentPipeProducts[index - 1];
        rInBox.Text = (pipe.GetProperty("r_in_m").GetDouble() * 1000.0).ToString("0.####", CultureInfo.InvariantCulture);
        rOutBox.Text = (pipe.GetProperty("r_out_m").GetDouble() * 1000.0).ToString("0.####", CultureInfo.InvariantCulture);
        kPBox.Text = pipe.GetProperty("k_p_W_mK").GetDouble().ToString("0.####", CultureInfo.InvariantCulture);
    }

    private void ApplyGroutCatalogSelection(ComboBox combo, TextBox kGBox)
    {
        var index = combo.SelectedIndex;
        if (index <= 0 || index - 1 >= _currentGroutProducts.Count)
            return;

        var grout = _currentGroutProducts[index - 1];
        kGBox.Text = grout.GetProperty("k_g_W_mK").GetDouble().ToString("0.####", CultureInfo.InvariantCulture);
    }

    private void PipeProductCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        ApplyPipeCatalogSelection(PipeProductCombo, PipeRInBox, PipeROutBox, PipeConductivityBox);
        // Lock the sourced catalog values so they can't drift out of sync with the selected
        // product by accident; "Custom" (index 0) leaves them editable, as before.
        var isCatalogPipe = PipeProductCombo.SelectedIndex > 0;
        PipeRInBox.IsReadOnly = isCatalogPipe;
        PipeROutBox.IsReadOnly = isCatalogPipe;
        PipeConductivityBox.IsReadOnly = isCatalogPipe;
        UpdatePipeGroutDependentFieldsAvailability();
    }

    private void GroutProductCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        ApplyGroutCatalogSelection(GroutProductCombo, GroutConductivityBox);
        GroutConductivityBox.IsReadOnly = GroutProductCombo.SelectedIndex > 0;
        UpdatePipeGroutDependentFieldsAvailability();
    }

    private void UpdatePipeGroutDependentFieldsAvailability()
    {
        if (GroutConductivityBox == null) return; // defensive: no early-firing path today, but cheap to guard
        var bothSelected = PipeProductCombo.SelectedIndex >= 0 && GroutProductCombo.SelectedIndex >= 0;
        GroutConductivityBox.IsEnabled = bothSelected;
        PipeRInBox.IsEnabled = bothSelected;
        PipeROutBox.IsEnabled = bothSelected;
        PipeConductivityBox.IsEnabled = bothSelected;
        FluidCombo.IsEnabled = bothSelected;
        FluidPercentBox.IsEnabled = bothSelected;
        FluidTemperatureBox.IsEnabled = bothSelected;
    }

    private bool ValidatePipeAndGroutSelected(TextBlock statusText)
    {
        if (PipeProductCombo.SelectedIndex < 0)
        {
            statusText.Text = "Select a pipe product (or \"Custom\") before calculating.";
            return false;
        }
        if (GroutProductCombo.SelectedIndex < 0)
        {
            statusText.Text = "Select a grout product (or \"Custom\") before calculating.";
            return false;
        }
        // Fluid only unlocks once both of the above are picked, but still needs its own
        // explicit choice -- there's no catalog default for it.
        if (FluidCombo.SelectedIndex < 0)
        {
            statusText.Text = "Select a fluid before calculating.";
            return false;
        }
        return true;
    }

    private static double ParseDouble(TextBox box, string fieldName)
    {
        if (!double.TryParse(box.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var value))
            throw new FormatException($"'{fieldName}' is not a valid number: '{box.Text}'");
        return value;
    }

    private static int ParseInt(TextBox box, string fieldName)
    {
        if (!int.TryParse(box.Text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
            throw new FormatException($"'{fieldName}' is not a valid whole number: '{box.Text}'");
        return value;
    }

    private static string SelectedText(ComboBox combo) =>
        ((ComboBoxItem)combo.SelectedItem).Content?.ToString() ?? string.Empty;

    // Builds the synthetic annual wet-bulb-temperature series (no real weather-file import
    // yet -- see geothermal/hybrid.py's synthetic_wet_bulb_series). minBox/maxBox let the two
    // "Wet-bulb threshold" panels (shared panel, Area Sizing's own) each read their own inputs.
    private static async Task<List<double>> BuildWetBulbSeriesAsync(EngineClient engine, int nHours, TextBox minBox, TextBox maxBox)
    {
        var result = await engine.CallAsync("synthetic_wet_bulb_series", new Dictionary<string, object?>
        {
            ["min_wet_bulb_C"] = ParseDouble(minBox, "Min outdoor WBT"),
            ["max_wet_bulb_C"] = ParseDouble(maxBox, "Max outdoor WBT"),
            ["n_hours"] = nHours,
        });
        return result.EnumerateArray().Select(x => x.GetDouble()).ToList();
    }

    private Dictionary<string, object?> BuildPipeConfig()
    {
        var shankSpacingM = ParseDouble(ShankSpacingBox, "Shank spacing") / 1000.0;
        var rIn = ParseDouble(PipeRInBox, "Pipe r_in") / 1000.0;
        var rOut = ParseDouble(PipeROutBox, "Pipe r_out") / 1000.0;
        var kP = ParseDouble(PipeConductivityBox, "Pipe conductivity");

        var configLabel = SelectedText(PipeConfigCombo);
        if (configLabel.StartsWith("Double", StringComparison.OrdinalIgnoreCase))
        {
            return new Dictionary<string, object?>
            {
                ["type"] = "multiple_u_tube",
                ["pos"] = new object[]
                {
                    new object[] { -shankSpacingM, 0.0 },
                    new object[] { shankSpacingM, 0.0 },
                    new object[] { 0.0, -shankSpacingM },
                    new object[] { 0.0, shankSpacingM },
                },
                ["r_in"] = rIn, ["r_out"] = rOut, ["k_p"] = kP,
                ["nPipes"] = 2,
                ["config"] = configLabel.Contains("series", StringComparison.OrdinalIgnoreCase) ? "series" : "parallel",
            };
        }

        return new Dictionary<string, object?>
        {
            ["type"] = "single_u_tube",
            ["pos"] = new object[] { new object[] { -shankSpacingM, 0.0 }, new object[] { shankSpacingM, 0.0 } },
            ["r_in"] = rIn, ["r_out"] = rOut, ["k_p"] = kP,
        };
    }

    private static (List<double> heating, List<double> cooling, List<double> peakHeating, List<double> peakCooling)
        ExtractMonthlyLoads(IEnumerable<MonthlyLoadRow> rows)
    {
        var list = rows.ToList();
        if (list.Count != 12)
            throw new InvalidOperationException($"expected 12 monthly rows, found {list.Count}");
        return (
            list.Select(r => r.BaseHeatingKWh).ToList(),
            list.Select(r => r.BaseCoolingKWh).ToList(),
            list.Select(r => r.PeakHeatingKW).ToList(),
            list.Select(r => r.PeakCoolingKW).ToList());
    }

    private async void CalculateButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            StatusText.Text = "Engine not available -- see message above.";
            return;
        }
        if (!ValidatePipeAndGroutSelected(StatusText)) return;

        CalculateButton.IsEnabled = false;
        StatusText.Foreground = Brushes.DarkRed;
        StatusText.Text = string.Empty;
        _rows.Clear();
        FieldSummaryText.Text = string.Empty;
        ResetGfunctionCharts();

        try
        {
            var n1 = ParseInt(N1Box, "Boreholes X");
            var n2 = ParseInt(N2Box, "Boreholes Y");
            var spacing = ParseDouble(SpacingBox, "Spacing");
            var depth = ParseDouble(DepthBox, "Borehole depth H");
            var buriedDepth = ParseDouble(BuriedDepthBox, "Buried depth D");
            var boreholeRadius = ParseDouble(BoreholeRadiusBox, "Borehole radius");
            var alpha = ParseDouble(AlphaBox, "Ground diffusivity");
            var conductivity = ParseDouble(ConductivityBox, "Ground conductivity");
            var designLifeYears = ParseDouble(DesignLifeBox, "Design life");

            var boundaryCondition = SelectedText(BoundaryConditionCombo).Split(' ')[0];
            var solver = SelectedText(SolverCombo).Split(' ')[0];

            var field = await _engine.CallAsync("build_rectangle_field", new Dictionary<string, object?>
            {
                ["N_1"] = n1,
                ["N_2"] = n2,
                ["B_1"] = spacing,
                ["B_2"] = spacing,
                ["H"] = depth,
                ["D"] = buriedDepth,
                ["r_b"] = boreholeRadius,
            });

            var time = await _engine.CallAsync("time_grid", new Dictionary<string, object?>
            {
                ["t_min_s"] = 3600.0,
                ["t_max_s"] = designLifeYears * SecondsPerYear,
                ["num"] = 40,
            });

            JsonElement gfuncResult;
            if (boundaryCondition == "MIFT")
            {
                var pipeConfig = BuildPipeConfig();
                var flowPerBorehole = ParseDouble(FlowPerBoreholeBox, "Flow per borehole");

                gfuncResult = await _engine.CallAsync("evaluate_mift_gfunction", new Dictionary<string, object?>
                {
                    ["field"] = field,
                    ["alpha"] = alpha,
                    ["time"] = time,
                    ["pipe_config"] = pipeConfig,
                    ["m_flow_network"] = n1 * n2 * flowPerBorehole,
                    ["k_s"] = conductivity,
                    ["k_g"] = ParseDouble(GroutConductivityBox, "Grout conductivity"),
                    ["fluid_str"] = SelectedText(FluidCombo),
                    ["fluid_percent"] = ParseDouble(FluidPercentBox, "Fluid percent"),
                    ["fluid_temperature_C"] = ParseDouble(FluidTemperatureBox, "Fluid temperature"),
                    ["method"] = solver,
                });
            }
            else
            {
                gfuncResult = await _engine.CallAsync("evaluate_gfunction", new Dictionary<string, object?>
                {
                    ["field"] = field,
                    ["alpha"] = alpha,
                    ["time"] = time,
                    ["method"] = solver,
                    ["boundary_condition"] = boundaryCondition,
                });
            }

            var summary = await _engine.CallAsync("field_summary", new Dictionary<string, object?> { ["field"] = field });
            var ts = (await _engine.CallAsync("characteristic_time", new Dictionary<string, object?>
            {
                ["field"] = field,
                ["alpha"] = alpha,
            })).GetDouble();

            var timeArray = gfuncResult.GetProperty("time_s").EnumerateArray().Select(x => x.GetDouble()).ToArray();
            var gArray = gfuncResult.GetProperty("g").EnumerateArray().Select(x => x.GetDouble()).ToArray();
            for (var i = 0; i < timeArray.Length; i++)
            {
                _rows.Add(new GfunctionRow
                {
                    TimeYears = timeArray[i] / SecondsPerYear,
                    LnTTs = Math.Log(timeArray[i] / ts),
                    G = gArray[i],
                });
            }

            G_Chart.Model = Charts.GFunction(_rows, designLifeYears, boundaryCondition);
            G_PlanView.SetRectangle(n1, n2, spacing, spacing, boreholeRadius);

            var nBoreholes = summary.GetProperty("n_boreholes").GetInt32();
            var totalLength = summary.GetProperty("total_length_m").GetDouble();
            var solverNote = gfuncResult.TryGetProperty("method_used", out var methodUsed)
                ? $" (solver used: {methodUsed.GetString()})" : string.Empty;
            FieldSummaryText.Text =
                $"{n1} x {n2} = {nBoreholes} boreholes, {totalLength:N0} m total drilled length. " +
                $"{boundaryCondition} boundary condition, {solver} solver{solverNote}. " +
                $"g at {designLifeYears:N0} years = {gArray[^1]:N3}.";
        }
        catch (EngineException ex)
        {
            StatusText.Text = ex.PythonTraceback is null
                ? $"Engine error: {ex.Message}"
                : $"Engine error: {ex.Message}\n\n{ex.PythonTraceback}";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            CalculateButton.IsEnabled = true;
        }
    }

    private async void SimulateButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            Sim_StatusText.Text = "Engine not available -- see message above.";
            return;
        }
        if (!ValidatePipeAndGroutSelected(Sim_StatusText)) return;

        SimulateButton.IsEnabled = false;
        Sim_StatusText.Foreground = Brushes.DarkRed;
        Sim_StatusText.Text = "Running...";
        _simYearlyRows.Clear();
        Sim_SummaryText.Text = string.Empty;
        ResetSimulationCharts();

        try
        {
            var n1 = ParseInt(N1Box, "Boreholes X");
            var n2 = ParseInt(N2Box, "Boreholes Y");
            var spacing = ParseDouble(SpacingBox, "Spacing");
            var depth = ParseDouble(DepthBox, "Borehole depth H");
            var buriedDepth = ParseDouble(BuriedDepthBox, "Buried depth D");
            var boreholeRadius = ParseDouble(BoreholeRadiusBox, "Borehole radius");
            var alpha = ParseDouble(AlphaBox, "Ground diffusivity");
            var conductivity = ParseDouble(ConductivityBox, "Ground conductivity");
            var groundTemp = ParseDouble(GroundTempBox, "Ground temperature");
            var periodYears = ParseInt(Sim_PeriodYearsBox, "Simulation period");
            var algorithm = SelectedText(Sim_AlgorithmCombo);

            var pipeConfig = BuildPipeConfig();
            var flowPerBorehole = ParseDouble(FlowPerBoreholeBox, "Flow per borehole");
            var fluidStr = SelectedText(FluidCombo);
            var fluidPercent = ParseDouble(FluidPercentBox, "Fluid percent");
            var fluidTemp = ParseDouble(FluidTemperatureBox, "Fluid temperature");

            var field = await _engine.CallAsync("build_rectangle_field", new Dictionary<string, object?>
            {
                ["N_1"] = n1, ["N_2"] = n2, ["B_1"] = spacing, ["B_2"] = spacing,
                ["H"] = depth, ["D"] = buriedDepth, ["r_b"] = boreholeRadius,
            });

            var (baseHeating, baseCooling, peakHeating, peakCooling) = ExtractMonthlyLoads(_loadRows);
            var hourlyResult = await _engine.CallAsync("synthesize_hourly_load", new Dictionary<string, object?>
            {
                ["baseload_heating_kWh"] = baseHeating,
                ["baseload_cooling_kWh"] = baseCooling,
                ["peak_heating_kW"] = peakHeating,
                ["peak_cooling_kW"] = peakCooling,
            });
            var oneYearWithoutTower = hourlyResult.EnumerateArray().Select(x => x.GetDouble()).ToList();
            // Peak-shaving has no memory (each hour's duty depends only on that hour's own load), so it
            // commutes with repetition -- shaving one year then repeating it, or repeating first and
            // shaving the whole multi-year array, give identical results. The deadband strategy is NOT
            // commutative like that (its on/off state carries across every hour of every year), so it
            // must dispatch against the full repeated series in one pass -- building repeatedLoad from the
            // raw (pre-tower) year up front, before branching, is what makes both paths share this code.
            var repeatedLoad = new List<double>(oneYearWithoutTower.Count * periodYears);
            for (var y = 0; y < periodYears; y++)
                repeatedLoad.AddRange(oneYearWithoutTower);

            var boreholeCapacitanceJmK = await BuildBoreholeCapacitanceJmKAsync(pipeConfig, boreholeRadius);
            var commonSimArgs = new Dictionary<string, object?>
            {
                ["field"] = field, ["alpha"] = alpha, ["k_s"] = conductivity, ["k_g"] = ParseDouble(GroutConductivityBox, "Grout conductivity"),
                ["T_g"] = groundTemp, ["pipe_config"] = pipeConfig, ["m_flow_borehole"] = flowPerBorehole,
                ["fluid_str"] = fluidStr, ["fluid_percent"] = fluidPercent, ["fluid_temperature_C"] = fluidTemp,
                ["algorithm"] = algorithm, ["gfunc_boundary_condition"] = GetGfuncBoundaryCondition(),
                ["T_g_drift_C_per_year"] = ParseDouble(GroundDriftBox, "Ground temperature drift"),
                ["multipole_order"] = ParseInt(MultipoleOrderBox, "Multipole order"),
                ["borehole_capacitance_J_mK"] = boreholeCapacitanceJmK,
            };

            var towerEnabled = TowerEnabledCheckBox.IsChecked == true;
            var heatPumpEnabled = HeatPumpEnabledCheckBox.IsChecked == true;
            if (heatPumpEnabled && towerEnabled)
            {
                throw new InvalidOperationException(
                    "Heat pump coupling and the hybrid cooling tower can't be combined yet -- " +
                    "uncheck one of them. (No Python-side support exists for running both together.)");
            }
            var towerStrategy = towerEnabled ? GetTowerStrategy(TowerStrategyCombo) : TowerStrategy.PeakShaving;
            var towerCapacityKW = 0.0;
            JsonElement? towerSummary = null;
            JsonElement? heatPumpSummary = null;
            List<double> oneYearWithTower = oneYearWithoutTower;
            JsonElement result = default;

            if (heatPumpEnabled)
            {
                // Iterative refinement (see geothermal/heat_pump.py's own docstring): the monthly load
                // profile is read as the BUILDING load here, not the ground load directly. Start with
                // EFT = T_g (a reasonable first guess before the field's own response is known), convert
                // to ground load via the COP(EFT) curves, simulate, feed the simulated T_f_C back as the
                // next pass's EFT, repeat -- 3 passes matches heat_pump.py's own documented/tested
                // convergence behavior (test_heat_pump.py's
                // test_iterative_refinement_with_run_hourly_simulation_converges uses the same count).
                var heatingCurve = _heatPumpHeatingCurveRows.Select(r => new[] { r.EftC, r.Cop }).ToList();
                var coolingCurve = _heatPumpCoolingCurveRows.Select(r => new[] { r.EftC, r.Cop }).ToList();
                if (heatingCurve.Count < 2 || coolingCurve.Count < 2)
                    throw new InvalidOperationException("Both heat pump COP curves need at least 2 rows.");

                var eftGuess = Enumerable.Repeat(groundTemp, repeatedLoad.Count).ToList();
                JsonElement hpResult = default;
                for (var pass = 0; pass < 3; pass++)
                {
                    hpResult = await _engine.CallAsync("apply_heat_pump", new Dictionary<string, object?>
                    {
                        ["building_load_W"] = repeatedLoad, ["eft_C"] = eftGuess,
                        ["cop_heating_curve"] = heatingCurve, ["cop_cooling_curve"] = coolingCurve,
                    });
                    var groundLoadHp = hpResult.GetProperty("ground_load_W").EnumerateArray().Select(x => x.GetDouble()).ToList();
                    result = await _engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>(commonSimArgs)
                    {
                        ["hourly_load_W"] = groundLoadHp,
                    });
                    eftGuess = result.GetProperty("T_f_C").EnumerateArray().Select(x => x.GetDouble()).ToList();
                    if (pass == 2)
                    {
                        heatPumpSummary = hpResult;
                        oneYearWithTower = groundLoadHp.Take(HoursPerYear).ToList();
                    }
                }
            }
            else if (towerStrategy == TowerStrategy.Deadband)
            {
                towerCapacityKW = ParseDouble(TowerCapacityBox, "Tower capacity");
                var deadbandC = ParseDouble(TowerDeadbandBox, "Tower deadband");
                result = await _engine.CallAsync("run_hourly_simulation_with_deadband_tower", new Dictionary<string, object?>(commonSimArgs)
                {
                    ["hourly_load_W"] = repeatedLoad, ["tower_capacity_kW"] = towerCapacityKW, ["tower_deadband_C"] = deadbandC,
                });
                towerSummary = result;
                oneYearWithTower = result.GetProperty("ground_load_W").EnumerateArray().Take(HoursPerYear).Select(x => x.GetDouble()).ToList();
            }
            else
            {
                // Peak-shaving and wet-bulb threshold are both stateless per-hour array transforms (see
                // hybrid.py's module docstring) -- precompute groundLoad once, on the full repeated
                // series, then hand it to the same run_hourly_simulation call either way.
                var groundLoad = repeatedLoad;
                if (towerStrategy == TowerStrategy.WetBulbThreshold)
                {
                    towerCapacityKW = ParseDouble(TowerCapacityBox, "Tower capacity");
                    var wetBulb = await BuildWetBulbSeriesAsync(_engine, repeatedLoad.Count, TowerWbtMinBox, TowerWbtMaxBox);
                    var towerResult = await _engine.CallAsync("apply_wet_bulb_tower", new Dictionary<string, object?>
                    {
                        ["hourly_load_W"] = repeatedLoad, ["wet_bulb_C"] = wetBulb,
                        ["threshold_C"] = ParseDouble(TowerWbtThresholdBox, "Wet-bulb threshold"), ["tower_capacity_kW"] = towerCapacityKW,
                    });
                    towerSummary = towerResult;
                    groundLoad = towerResult.GetProperty("ground_load_W").EnumerateArray().Select(x => x.GetDouble()).ToList();
                    oneYearWithTower = groundLoad.Take(HoursPerYear).ToList();
                }
                else if (towerEnabled)
                {
                    towerCapacityKW = ParseDouble(TowerCapacityBox, "Tower capacity");
                    var towerResult = await _engine.CallAsync("apply_cooling_tower", new Dictionary<string, object?>
                    {
                        ["hourly_load_W"] = repeatedLoad, ["tower_capacity_kW"] = towerCapacityKW,
                    });
                    towerSummary = towerResult;
                    groundLoad = towerResult.GetProperty("ground_load_W").EnumerateArray().Select(x => x.GetDouble()).ToList();
                    oneYearWithTower = groundLoad.Take(HoursPerYear).ToList();
                }
                result = await _engine.CallAsync("run_hourly_simulation", new Dictionary<string, object?>(commonSimArgs)
                {
                    ["hourly_load_W"] = groundLoad,
                });
            }

            var tF = result.GetProperty("T_f_C").EnumerateArray().Select(x => x.GetDouble()).ToArray();
            var tB = result.GetProperty("T_b_C").EnumerateArray().Select(x => x.GetDouble()).ToArray();
            var rbStar = result.GetProperty("R_b_star_mK_W").GetDouble();
            var totalLength = result.GetProperty("total_length_m").GetDouble();

            for (var y = 0; y < periodYears; y++)
            {
                var sliceF = tF.Skip(y * HoursPerYear).Take(HoursPerYear).ToArray();
                var sliceB = tB.Skip(y * HoursPerYear).Take(HoursPerYear).ToArray();
                _simYearlyRows.Add(new YearlySummaryRow
                {
                    Year = y + 1,
                    TFluidMinC = sliceF.Min(), TFluidMaxC = sliceF.Max(),
                    TWallMinC = sliceB.Min(), TWallMaxC = sliceB.Max(),
                });
            }

            var towerHadNoEffect = towerSummary is JsonElement summaryCheck && TowerHadNoEffect(summaryCheck);
            var showTowerComparison = towerEnabled && !towerHadNoEffect;

            Sim_TempChart.Model = Charts.TemperatureHistory(tF, tB, null, null, $"Fluid temperature over {periodYears} years");
            Sim_LoadChart.Model = Charts.MonthlyLoad(oneYearWithTower, showTowerComparison ? oneYearWithoutTower : null);
            Sim_PlanView.SetRectangle(n1, n2, spacing, spacing, boreholeRadius);

            Sim_SummaryText.Text =
                $"{n1} x {n2} = {n1 * n2} boreholes, {totalLength:N0} m total drilled length. " +
                $"R_b* = {rbStar:N4} m.K/W. " +
                $"Fluid temperature over {periodYears} years: {tF.Min():N1} C .. {tF.Max():N1} C.";

            var strategyLabel = towerStrategy switch
            {
                TowerStrategy.Deadband => $"ground-temp deadband, {ParseDouble(TowerDeadbandBox, "Tower deadband"):N1} C band",
                TowerStrategy.WetBulbThreshold => $"wet-bulb threshold, {ParseDouble(TowerWbtThresholdBox, "Wet-bulb threshold"):N1} C",
                _ => "peak-shaving",
            };
            if (towerHadNoEffect)
            {
                Sim_SummaryText.Text +=
                    $"\nCooling tower ({towerCapacityKW:N0} kW rated, {strategyLabel}): never engaged over this period, " +
                    "so it made no difference -- results above are the same as running with no tower at all.";
            }
            else if (towerSummary is JsonElement tower)
            {
                // apply_cooling_tower/run_hourly_simulation_with_deadband_tower were both called on the
                // FULL periodYears-long series above, so their hours/energy totals cover the whole period
                // -- divide by periodYears for an average-per-year figure (exact for peak-shaving, since
                // its per-hour duty never depends on prior years; an honest average for deadband, whose
                // year-to-year duty can vary slightly before the ground settles into equilibrium).
                var towerPeak = tower.GetProperty("tower_peak_kW").GetDouble();
                var towerHoursPerYear = tower.GetProperty("tower_hours").GetInt32() / (double)periodYears;
                var towerEnergyPerYear = tower.GetProperty("tower_energy_kWh").GetDouble() / periodYears;
                Sim_SummaryText.Text +=
                    $"\nCooling tower ({towerCapacityKW:N0} kW rated, {strategyLabel}): ran {towerHoursPerYear:N0} h/yr avg, " +
                    $"{towerEnergyPerYear:N0} kWh/yr avg rejected, peak duty {towerPeak:N1} kW.";
            }
            if (heatPumpSummary is JsonElement hp)
            {
                // The 3rd (final, converged) pass's own apply_heat_pump result, run against the FULL
                // repeatedLoad series -- total_electrical_energy_kWh covers the whole period, so divide
                // by periodYears for a per-year figure, same convention as the tower summary above.
                var electricalEnergyPerYear = hp.GetProperty("total_electrical_energy_kWh").GetDouble() / periodYears;
                var meanCop = hp.GetProperty("mean_cop").GetDouble();
                Sim_SummaryText.Text +=
                    $"\nHeat pump (converged after 3 refinement passes): mean COP {meanCop:N2}, " +
                    $"{electricalEnergyPerYear:N0} kWh/yr avg compressor electrical energy.";
            }

            Sim_StatusText.Text = string.Empty;
        }
        catch (EngineException ex)
        {
            Sim_StatusText.Text = ex.PythonTraceback is null
                ? $"Engine error: {ex.Message}"
                : $"Engine error: {ex.Message}\n\n{ex.PythonTraceback}";
        }
        catch (Exception ex)
        {
            Sim_StatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            SimulateButton.IsEnabled = true;
        }
    }

    private async void SizeFieldButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            Size_StatusText.Text = "Engine not available -- see message above.";
            return;
        }
        if (!ValidatePipeAndGroutSelected(Size_StatusText)) return;

        SizeFieldButton.IsEnabled = false;
        Size_StatusText.Foreground = Brushes.DarkRed;
        var crossCheck = Size_CrossCheckCheckBox.IsChecked == true;
        var towerEnabled = TowerEnabledCheckBox.IsChecked == true;
        var towerStrategy = towerEnabled ? GetTowerStrategy(TowerStrategyCombo) : TowerStrategy.PeakShaving;
        Size_StatusText.Text = crossCheck || towerEnabled
            ? "Sizing (this can take longer -- tower and/or GHEtool comparison runs extra sizing passes)..."
            : "Sizing...";
        Size_ResultText.Text = string.Empty;
        Size_TowerText.Text = string.Empty;
        Size_CrossCheckText.Text = string.Empty;
        ResetSizingCharts();

        try
        {
            var n1 = ParseInt(N1Box, "Boreholes X");
            var n2 = ParseInt(N2Box, "Boreholes Y");
            var spacing = ParseDouble(SpacingBox, "Spacing");
            var buriedDepth = ParseDouble(BuriedDepthBox, "Buried depth D");
            var boreholeRadius = ParseDouble(BoreholeRadiusBox, "Borehole radius");
            var alpha = ParseDouble(AlphaBox, "Ground diffusivity");
            var conductivity = ParseDouble(ConductivityBox, "Ground conductivity");
            var groutConductivity = ParseDouble(GroutConductivityBox, "Grout conductivity");
            var groundTemp = ParseDouble(GroundTempBox, "Ground temperature");
            var minFluidTemp = ParseDouble(Size_MinFluidTempBox, "Min fluid temperature");
            var maxFluidTemp = ParseDouble(Size_MaxFluidTempBox, "Max fluid temperature");
            var periodYears = ParseInt(Size_PeriodYearsBox, "Design life");
            var hMin = ParseDouble(Size_HMinBox, "Search H min");
            var hMax = ParseDouble(Size_HMaxBox, "Search H max");

            var pipeConfig = BuildPipeConfig();
            var flowPerBorehole = ParseDouble(FlowPerBoreholeBox, "Flow per borehole");
            var fluidStr = SelectedText(FluidCombo);
            var fluidPercent = ParseDouble(FluidPercentBox, "Fluid percent");
            var fluidTemp = ParseDouble(FluidTemperatureBox, "Fluid temperature");

            var fieldTemplate = new Dictionary<string, object?>
            {
                ["layout"] = "rectangle", ["N_1"] = n1, ["N_2"] = n2,
                ["B_1"] = spacing, ["B_2"] = spacing, ["D"] = buriedDepth, ["r_b"] = boreholeRadius,
            };

            var (baseHeating, baseCooling, peakHeating, peakCooling) = ExtractMonthlyLoads(_loadRows);
            var hourlyResult = await _engine.CallAsync("synthesize_hourly_load", new Dictionary<string, object?>
            {
                ["baseload_heating_kWh"] = baseHeating,
                ["baseload_cooling_kWh"] = baseCooling,
                ["peak_heating_kW"] = peakHeating,
                ["peak_cooling_kW"] = peakCooling,
            });
            var oneYearLoad = hourlyResult.EnumerateArray().Select(x => x.GetDouble()).ToList();
            var boreholeCapacitanceJmK = await BuildBoreholeCapacitanceJmKAsync(pipeConfig, boreholeRadius);

            Dictionary<string, object?> BuildSizeFieldArgs(List<double> load) => new()
            {
                ["field_template"] = fieldTemplate, ["alpha"] = alpha, ["k_s"] = conductivity, ["k_g"] = groutConductivity,
                ["T_g"] = groundTemp, ["pipe_config"] = pipeConfig, ["m_flow_borehole"] = flowPerBorehole,
                ["fluid_str"] = fluidStr, ["fluid_percent"] = fluidPercent, ["fluid_temperature_C"] = fluidTemp,
                ["hourly_load_W"] = load, ["simulation_period_years"] = periodYears,
                ["T_f_min_limit_C"] = minFluidTemp, ["T_f_max_limit_C"] = maxFluidTemp,
                ["H_min"] = hMin, ["H_max"] = hMax, ["gfunc_boundary_condition"] = GetGfuncBoundaryCondition(),
                ["T_g_drift_C_per_year"] = ParseDouble(GroundDriftBox, "Ground temperature drift"),
                ["multipole_order"] = ParseInt(MultipoleOrderBox, "Multipole order"),
                ["borehole_capacitance_J_mK"] = boreholeCapacitanceJmK,
            };

            if (Size_McEnabledCheckBox.IsChecked == true)
            {
                if (towerEnabled)
                {
                    Size_StatusText.Text = "Uncertainty (Monte Carlo) can't run with a cooling tower enabled -- disable one or the other.";
                    return;
                }

                var uncertainInputs = new Dictionary<string, object?>();
                if (Size_McKsCheckBox.IsChecked == true)
                {
                    uncertainInputs["k_s"] = new Dictionary<string, object?>
                    {
                        ["dist"] = "normal", ["mean"] = conductivity, ["stddev"] = ParseDouble(Size_McKsStdDevBox, "k_s std dev"),
                    };
                }
                if (Size_McTgCheckBox.IsChecked == true)
                {
                    uncertainInputs["T_g"] = new Dictionary<string, object?>
                    {
                        ["dist"] = "normal", ["mean"] = groundTemp, ["stddev"] = ParseDouble(Size_McTgStdDevBox, "T_g std dev"),
                    };
                }
                if (uncertainInputs.Count == 0)
                {
                    Size_StatusText.Text = "Check at least one of k_s / T_g to vary for Monte Carlo sizing.";
                    return;
                }

                var nSamples = ParseInt(Size_McSamplesBox, "Number of samples");
                Size_StatusText.Text = $"Running Monte Carlo ({nSamples} samples of Size Field -- this can take a while)...";

                var mcResult = await _engine.CallAsync("size_field_monte_carlo", new Dictionary<string, object?>
                {
                    ["base_kwargs"] = BuildSizeFieldArgs(oneYearLoad),
                    ["uncertain_inputs"] = uncertainInputs,
                    ["n_samples"] = nSamples,
                });

                var nFailed = mcResult.GetProperty("n_failed").GetInt32();
                var fractionFailed = mcResult.GetProperty("fraction_failed").GetDouble();
                var hMean = mcResult.GetProperty("H_m_mean").GetDouble();
                var hStdDev = mcResult.GetProperty("H_m_stddev").GetDouble();
                var percentileLines = string.Join("\n", mcResult.GetProperty("percentiles").EnumerateArray()
                    .Select(p => $"P{p.GetProperty("p").GetDouble():N0}: H = {p.GetProperty("H_m").GetDouble():N1} m"));
                var hSamples = mcResult.GetProperty("H_m_samples").EnumerateArray().Select(x => x.GetDouble()).ToArray();

                Size_McResultText.Text =
                    $"{nSamples} samples, {n1 * n2} boreholes:\n{percentileLines}\n" +
                    $"Mean H = {hMean:N1} m, std dev = {hStdDev:N2} m\n" +
                    (nFailed > 0
                        ? $"{nFailed} of {nSamples} samples ({fractionFailed:P0}) could not meet the limits even at " +
                          $"H_max = {hMax:N0} m -- censored at H_max for the percentiles above. A high fraction here " +
                          "means H_max itself likely needs raising, not just the reported depth."
                        : "All samples met the fluid-temperature limits within the searched H range.");
                Size_McHistChart.Model = Charts.Histogram(hSamples, "Sized depth H (m)");

                Size_ResultText.Text = string.Empty;
                Size_McResultPanel.Visibility = Visibility.Visible;
                Size_DeterministicHost.Visibility = Visibility.Collapsed;
                Size_LoadPlanHost.Visibility = Visibility.Collapsed;
                Size_DepthHost.Visibility = Visibility.Collapsed;
                Size_TowerCrossCheckHost.Visibility = Visibility.Collapsed;
                Size_StatusText.Text = string.Empty;
                return;
            }

            Size_McResultPanel.Visibility = Visibility.Collapsed;
            Size_DeterministicHost.Visibility = Visibility.Visible;
            Size_LoadPlanHost.Visibility = Visibility.Visible;
            Size_TowerCrossCheckHost.Visibility = Visibility.Visible;

            // Baseline (no tower) sizing -- always the result compared against GHEtool below,
            // since GHEtool has no way to see the tower-adjusted load (it sizes from monthly
            // loads directly). With the tower off, this is also the only sizing run and the
            // headline result.
            JsonElement baselineResult = await _engine.CallAsync("size_field", BuildSizeFieldArgs(oneYearLoad));
            var headlineResult = baselineResult;
            double towerCapacityKW = 0.0;
            JsonElement? towerLoadResult = null;
            var headlineLoad = oneYearLoad;

            if (towerStrategy == TowerStrategy.Deadband)
            {
                // The deadband controller's behavior depends on the simulated ground temperature at
                // whatever depth is being tried, so it can't be precomputed like apply_cooling_tower's
                // output -- one call does the whole tower-aware bisection (see hybrid.py's docstring).
                towerCapacityKW = ParseDouble(TowerCapacityBox, "Tower capacity");
                var deadbandC = ParseDouble(TowerDeadbandBox, "Tower deadband");
                Size_StatusText.Text = "Sizing with ground-temperature deadband tower...";
                headlineResult = await _engine.CallAsync("size_field_with_deadband_tower", new Dictionary<string, object?>(BuildSizeFieldArgs(oneYearLoad))
                {
                    ["tower_capacity_kW"] = towerCapacityKW, ["tower_deadband_C"] = deadbandC,
                });
                towerLoadResult = headlineResult;
                headlineLoad = headlineResult.GetProperty("ground_load_W").EnumerateArray().Take(HoursPerYear).Select(x => x.GetDouble()).ToList();
            }
            else if (towerStrategy == TowerStrategy.WetBulbThreshold)
            {
                // Stateless per-hour transform (see hybrid.py's module docstring), so -- like
                // peak-shaving -- it can be precomputed once, on the one representative year, before
                // size_field's own internal repetition/bisection.
                towerCapacityKW = ParseDouble(TowerCapacityBox, "Tower capacity");
                var wetBulb = await BuildWetBulbSeriesAsync(_engine, oneYearLoad.Count, TowerWbtMinBox, TowerWbtMaxBox);
                towerLoadResult = await _engine.CallAsync("apply_wet_bulb_tower", new Dictionary<string, object?>
                {
                    ["hourly_load_W"] = oneYearLoad, ["wet_bulb_C"] = wetBulb,
                    ["threshold_C"] = ParseDouble(TowerWbtThresholdBox, "Wet-bulb threshold"), ["tower_capacity_kW"] = towerCapacityKW,
                });
                var groundLoad = towerLoadResult.Value.GetProperty("ground_load_W").EnumerateArray().Select(x => x.GetDouble()).ToList();
                headlineLoad = groundLoad;

                Size_StatusText.Text = "Sizing with tower...";
                headlineResult = await _engine.CallAsync("size_field", BuildSizeFieldArgs(groundLoad));
            }
            else if (towerEnabled)
            {
                towerCapacityKW = ParseDouble(TowerCapacityBox, "Tower capacity");
                towerLoadResult = await _engine.CallAsync("apply_cooling_tower", new Dictionary<string, object?>
                {
                    ["hourly_load_W"] = oneYearLoad, ["tower_capacity_kW"] = towerCapacityKW,
                });
                var groundLoad = towerLoadResult.Value.GetProperty("ground_load_W").EnumerateArray().Select(x => x.GetDouble()).ToList();
                headlineLoad = groundLoad;

                Size_StatusText.Text = "Sizing with tower...";
                headlineResult = await _engine.CallAsync("size_field", BuildSizeFieldArgs(groundLoad));
            }

            var sizedH = headlineResult.GetProperty("H_m").GetDouble();
            var rbStar = headlineResult.GetProperty("R_b_star_mK_W").GetDouble();
            var tFMin = headlineResult.GetProperty("T_f_min_C").GetDouble();
            var tFMax = headlineResult.GetProperty("T_f_max_C").GetDouble();
            var iterations = headlineResult.GetProperty("iterations").GetInt32();
            var nBoreholes = n1 * n2;

            var towerHadNoEffect = towerLoadResult is JsonElement loadCheck && TowerHadNoEffect(loadCheck);
            var showTowerComparison = towerEnabled && !towerHadNoEffect;

            // size_field returns the full hourly series of the design it settled on -- chart it as-is.
            Size_TempChart.Model = Charts.TemperatureHistory(
                ReadDoubleArray(headlineResult, "T_f_C"), ReadDoubleArray(headlineResult, "T_b_C"),
                minFluidTemp, maxFluidTemp, $"Fluid temperature at H = {sizedH:N1} m over {periodYears} years");
            Size_LoadChart.Model = Charts.MonthlyLoad(headlineLoad, showTowerComparison ? oneYearLoad : null);
            Size_PlanView.SetRectangle(n1, n2, spacing, spacing, boreholeRadius);

            var depthBars = new List<(string Label, double DepthM)>
            {
                (towerEnabled ? "Engine, no tower" : "Engine", baselineResult.GetProperty("H_m").GetDouble()),
            };
            if (showTowerComparison)
            {
                var barSuffix = towerStrategy switch
                {
                    TowerStrategy.Deadband => " (deadband)",
                    TowerStrategy.WetBulbThreshold => " (wet-bulb)",
                    _ => "",
                };
                depthBars.Add(($"Engine + {towerCapacityKW:N0} kW tower{barSuffix}", sizedH));
            }

            Size_ResultText.Text =
                $"Sized depth: H = {sizedH:N1} m\n" +
                $"{nBoreholes} boreholes x {sizedH:N1} m = {sizedH * nBoreholes:N0} m total drilled length\n" +
                $"R_b* = {rbStar:N4} m.K/W\n" +
                $"Fluid temperature achieved: {tFMin:N1} C .. {tFMax:N1} C (limits: {minFluidTemp:N1} .. {maxFluidTemp:N1} C)\n" +
                $"Bisection iterations: {iterations}";

            var sizeStrategyLabel = towerStrategy switch
            {
                TowerStrategy.Deadband => $", ground-temp deadband ({ParseDouble(TowerDeadbandBox, "Tower deadband"):N1} C band)",
                TowerStrategy.WetBulbThreshold => $", wet-bulb threshold ({ParseDouble(TowerWbtThresholdBox, "Wet-bulb threshold"):N1} C)",
                _ => "",
            };
            if (towerHadNoEffect)
            {
                Size_TowerText.Text =
                    $"{towerCapacityKW:N0} kW tower{sizeStrategyLabel}: never engaged over this period, so it made no " +
                    $"difference -- sized depth above (H = {sizedH:N1} m) is the same as sizing with no tower at all.";
            }
            else if (towerEnabled && towerLoadResult is JsonElement towerLoad)
            {
                var baselineH = baselineResult.GetProperty("H_m").GetDouble();
                var reductionPct = 100.0 * (baselineH - sizedH) / baselineH;
                var towerPeak = towerLoad.GetProperty("tower_peak_kW").GetDouble();
                // apply_cooling_tower/apply_wet_bulb_tower both ran on a single representative year, so
                // their totals are already per-year; size_field_with_deadband_tower ran on the full
                // periodYears-long repeated series internally, so its totals need dividing for the same
                // "h/yr" framing.
                var isDeadband = towerStrategy == TowerStrategy.Deadband;
                var towerHoursPerYear = isDeadband
                    ? towerLoad.GetProperty("tower_hours").GetInt32() / (double)periodYears
                    : towerLoad.GetProperty("tower_hours").GetInt32();
                var towerEnergyPerYear = isDeadband
                    ? towerLoad.GetProperty("tower_energy_kWh").GetDouble() / periodYears
                    : towerLoad.GetProperty("tower_energy_kWh").GetDouble();
                Size_TowerText.Text =
                    $"Without tower: H = {baselineH:N1} m\n" +
                    $"With {towerCapacityKW:N0} kW tower{sizeStrategyLabel}: H = {sizedH:N1} m ({reductionPct:N0}% smaller)\n" +
                    $"Tower duty: peak {towerPeak:N1} kW, runs {towerHoursPerYear:N0} h/yr avg, rejects {towerEnergyPerYear:N0} kWh/yr avg";
            }
            else
            {
                Size_TowerText.Text = "(not run -- check \"Add supplemental cooling tower\" to compare)";
            }

            if (crossCheck)
            {
                Size_StatusText.Text = "Sizing done, running GHEtool cross-check...";
                var baselineH = baselineResult.GetProperty("H_m").GetDouble();
                var ghetoolResult = await _engine.CallAsync("size_field_ghetool", new Dictionary<string, object?>
                {
                    ["field_template"] = fieldTemplate, ["k_s"] = conductivity, ["k_g"] = groutConductivity,
                    ["T_g"] = groundTemp, ["pipe_config"] = pipeConfig, ["m_flow_borehole"] = flowPerBorehole,
                    ["fluid_str"] = fluidStr, ["fluid_percent"] = fluidPercent, ["fluid_temperature_C"] = fluidTemp,
                    ["baseload_heating_kWh"] = baseHeating, ["baseload_cooling_kWh"] = baseCooling,
                    ["peak_heating_kW"] = peakHeating, ["peak_cooling_kW"] = peakCooling,
                    ["simulation_period_years"] = periodYears,
                    ["T_f_min_limit_C"] = minFluidTemp, ["T_f_max_limit_C"] = maxFluidTemp,
                    ["method"] = "L3",
                });
                var ghetoolH = ghetoolResult.GetProperty("H_m").GetDouble();
                depthBars.Add(("GHEtool L3", ghetoolH));
                var ghetoolRb = ghetoolResult.GetProperty("R_b_star_mK_W").GetDouble();
                var pctDiff = 100.0 * (baselineH - ghetoolH) / ghetoolH;
                var towerNote = towerEnabled
                    ? "\n\nNote: this comparison is against the no-tower baseline on both sides -- " +
                      "GHEtool sizes from monthly loads directly, so there's no way to feed it the " +
                      "tower-adjusted hourly profile. See the Hybrid cooling tower section above for " +
                      "the tower's effect on this engine's own sizing."
                    : string.Empty;
                Size_CrossCheckText.Text =
                    $"GHEtool L3 sized depth: H = {ghetoolH:N1} m ({pctDiff:+0.0;-0.0}% vs. this engine's no-tower baseline)\n" +
                    $"GHEtool R_b* = {ghetoolRb:N4} m.K/W\n\n" +
                    "Note: methods differ (this engine uses a simplified monthly-to-hourly load " +
                    "synthesis; GHEtool L3 uses its own convolution method) -- expect close agreement, not an exact match." +
                    towerNote;
            }
            else
            {
                Size_CrossCheckText.Text = "(not run -- check \"Cross-check with GHEtool\" to compare)";
            }

            // A one-bar comparison says nothing, so the chart only appears once there is something to compare.
            if (depthBars.Count > 1)
            {
                Size_DepthChart.Model = Charts.DepthComparison(depthBars);
                Size_DepthHost.Visibility = System.Windows.Visibility.Visible;
            }

            Size_StatusText.Text = string.Empty;
        }
        catch (EngineException ex)
        {
            Size_StatusText.Text = ex.PythonTraceback is null
                ? $"Engine error: {ex.Message}"
                : $"Engine error: {ex.Message}\n\n{ex.PythonTraceback}";
        }
        catch (Exception ex)
        {
            Size_StatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            SizeFieldButton.IsEnabled = true;
        }
    }

    private async void AreaSizeButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            Area_StatusText.Text = "Engine not available -- see message above.";
            return;
        }
        if (!ValidatePipeAndGroutSelected(Area_StatusText)) return;

        AreaSizeButton.IsEnabled = false;
        Area_StatusText.Foreground = Brushes.DarkRed;
        Area_StatusText.Text = "Calculating (can take 20-40s if a dry cooler search is needed)...";
        Area_FieldText.Text = string.Empty;
        Area_ResultText.Text = string.Empty;
        ResetAreaCharts();

        try
        {
            var peakHeating = ParseDouble(Area_PeakHeatingBox, "Peak heating capacity");
            var peakCooling = ParseDouble(Area_PeakCoolingBox, "Peak cooling capacity");
            var heatingSeasonMonths = ParseInt(Area_HeatingSeasonBox, "Heating season");
            var coolingSeasonMonths = ParseInt(Area_CoolingSeasonBox, "Cooling season");
            var areaAmbient = Area_UseAmbientCheckBox.IsChecked == true
                ? ParseDouble(Area_AmbientAreaBox, "Ambient ground area") : 0.0;
            var areaUnderBuilding = Area_UseUnderBuildingCheckBox.IsChecked == true
                ? ParseDouble(Area_UnderBuildingAreaBox, "Area under building") : 0.0;
            var minFluidTemp = ParseDouble(Area_MinFluidTempBox, "Min fluid temperature");
            var maxFluidTemp = ParseDouble(Area_MaxFluidTempBox, "Max fluid temperature");
            var periodYears = ParseInt(Area_PeriodYearsBox, "Design life");
            var hMin = ParseDouble(Area_HMinBox, "Search H min");
            var hMax = ParseDouble(Area_HMaxBox, "Max practical depth");

            var spacing = ParseDouble(SpacingBox, "Spacing");
            var buriedDepth = ParseDouble(BuriedDepthBox, "Buried depth D");
            var boreholeRadius = ParseDouble(BoreholeRadiusBox, "Borehole radius");
            var alpha = ParseDouble(AlphaBox, "Ground diffusivity");
            var conductivity = ParseDouble(ConductivityBox, "Ground conductivity");
            var groutConductivity = ParseDouble(GroutConductivityBox, "Grout conductivity");
            var groundTemp = ParseDouble(GroundTempBox, "Ground temperature");
            var pipeConfig = BuildPipeConfig();
            var flowPerBorehole = ParseDouble(FlowPerBoreholeBox, "Flow per borehole");
            var fluidStr = SelectedText(FluidCombo);
            var fluidPercent = ParseDouble(FluidPercentBox, "Fluid percent");
            var fluidTemp = ParseDouble(FluidTemperatureBox, "Fluid temperature");

            var layoutArgs = new Dictionary<string, object?>
            {
                ["area_ambient_m2"] = areaAmbient, ["area_under_building_m2"] = areaUnderBuilding, ["spacing_m"] = spacing,
            };
            var maxRows = ParseInt(Area_MaxRowsBox, "Max rows");
            if (maxRows > 0)
                layoutArgs["max_rows"] = maxRows;

            var layoutResult = await _engine.CallAsync("field_layout_from_area", layoutArgs);
            var n1 = layoutResult.GetProperty("N_1").GetInt32();
            var n2 = layoutResult.GetProperty("N_2").GetInt32();
            var nBoreholes = n1 * n2;
            var footprintArea = layoutResult.GetProperty("footprint_area_m2").GetDouble();
            var totalAreaAvailable = layoutResult.GetProperty("total_area_available_m2").GetDouble();

            Area_FieldText.Text =
                $"{n1} x {n2} = {nBoreholes} boreholes at {spacing:N1} m spacing\n" +
                $"Footprint used: {footprintArea:N0} m2 of {totalAreaAvailable:N0} m2 available";

            Area_PlanView.SetRectangle(n1, n2, spacing, spacing, boreholeRadius);
            Area_FootprintBar.Maximum = Math.Max(totalAreaAvailable, footprintArea);
            Area_FootprintBar.Value = footprintArea;
            Area_FootprintText.Text =
                $"{footprintArea:N0} m² of {totalAreaAvailable:N0} m² ({100.0 * footprintArea / totalAreaAvailable:N0}%). " +
                "The footprint is the centre-to-centre span of the outer boreholes; the grid is derived from a square-equivalent of the combined area.";

            var fieldTemplate = new Dictionary<string, object?>
            {
                ["layout"] = "rectangle", ["N_1"] = n1, ["N_2"] = n2,
                ["B_1"] = spacing, ["B_2"] = spacing, ["D"] = buriedDepth, ["r_b"] = boreholeRadius,
            };

            var oneYearLoad = (await _engine.CallAsync("synthesize_peak_only_load", new Dictionary<string, object?>
            {
                ["peak_heating_kW"] = peakHeating, ["peak_cooling_kW"] = peakCooling,
                ["heating_season_months"] = heatingSeasonMonths, ["cooling_season_months"] = coolingSeasonMonths,
            })).EnumerateArray().Select(x => x.GetDouble()).ToList();

            var sizeArgs = new Dictionary<string, object?>
            {
                ["field_template"] = fieldTemplate, ["alpha"] = alpha, ["k_s"] = conductivity, ["k_g"] = groutConductivity,
                ["T_g"] = groundTemp, ["pipe_config"] = pipeConfig, ["m_flow_borehole"] = flowPerBorehole,
                ["fluid_str"] = fluidStr, ["fluid_percent"] = fluidPercent, ["fluid_temperature_C"] = fluidTemp,
                ["hourly_load_W"] = oneYearLoad, ["simulation_period_years"] = periodYears,
                ["T_f_min_limit_C"] = minFluidTemp, ["T_f_max_limit_C"] = maxFluidTemp,
                ["H_min"] = hMin, ["H_max"] = hMax,
            };

            JsonElement sizingResult;
            var towerNeeded = false;
            var areaTowerStrategy = TowerStrategy.PeakShaving;
            try
            {
                sizingResult = await _engine.CallAsync("size_field", sizeArgs);
            }
            catch (EngineException ex) when (ex.Message.Contains("fails the fluid temperature limit"))
            {
                towerNeeded = true;
                areaTowerStrategy = GetTowerStrategy(Area_TowerStrategyCombo);
                var dryCoolerCommand = areaTowerStrategy switch
                {
                    TowerStrategy.Deadband => "minimum_deadband_tower_capacity",
                    TowerStrategy.WetBulbThreshold => "minimum_wet_bulb_tower_capacity",
                    _ => "minimum_tower_capacity",
                };
                Area_StatusText.Text = "Field alone insufficient at max practical depth -- solving for minimum dry cooler capacity...";
                var dryCoolerArgs = new Dictionary<string, object?>
                {
                    ["field_template"] = fieldTemplate, ["H"] = hMax, ["alpha"] = alpha, ["k_s"] = conductivity, ["k_g"] = groutConductivity,
                    ["T_g"] = groundTemp, ["pipe_config"] = pipeConfig, ["m_flow_borehole"] = flowPerBorehole,
                    ["fluid_str"] = fluidStr, ["fluid_percent"] = fluidPercent, ["fluid_temperature_C"] = fluidTemp,
                    ["hourly_load_W"] = oneYearLoad, ["simulation_period_years"] = periodYears,
                    ["T_f_min_limit_C"] = minFluidTemp, ["T_f_max_limit_C"] = maxFluidTemp,
                };
                if (areaTowerStrategy == TowerStrategy.Deadband)
                {
                    dryCoolerArgs["tower_deadband_C"] = ParseDouble(Area_TowerDeadbandBox, "Dry cooler deadband");
                }
                else if (areaTowerStrategy == TowerStrategy.WetBulbThreshold)
                {
                    dryCoolerArgs["wet_bulb_C"] = await BuildWetBulbSeriesAsync(_engine, oneYearLoad.Count, Area_TowerWbtMinBox, Area_TowerWbtMaxBox);
                    dryCoolerArgs["threshold_C"] = ParseDouble(Area_TowerWbtThresholdBox, "Dry cooler wet-bulb threshold");
                }

                try
                {
                    sizingResult = await _engine.CallAsync(dryCoolerCommand, dryCoolerArgs);
                }
                catch (EngineException innerEx) when (innerEx.Message.Contains("provides NO benefit"))
                {
                    // The dry cooler genuinely cannot help here, no matter its rated capacity -- this
                    // control strategy never assists during the hour that sets the limit (see
                    // hybrid.minimum_tower_capacity's own distinction between "zero benefit" and
                    // "not enough capacity"). Report the field-alone shortfall plainly instead of a
                    // traceback: there is nothing a tower of any size would change here, so nothing to
                    // add to the calculation.
                    var strategyName = areaTowerStrategy switch
                    {
                        TowerStrategy.Deadband => "ground-temperature deadband",
                        TowerStrategy.WetBulbThreshold => "wet-bulb threshold",
                        _ => "peak-shaving",
                    };
                    Area_ResultText.Text =
                        $"Field alone cannot meet the peak loads within {hMax:N0} m (max practical depth), " +
                        $"and a {strategyName} dry cooler cannot help at all here -- this control strategy " +
                        "never assists during the hour that sets the limit, so no rated capacity would " +
                        "change the result.\nTry a deeper max practical depth, a larger field, or a " +
                        "different control strategy (e.g. peak-shaving, which assists every cooling hour).";
                    Area_StatusText.Text = string.Empty;
                    return;
                }
            }

            var rbStar = sizingResult.GetProperty("R_b_star_mK_W").GetDouble();
            var tFMin = sizingResult.GetProperty("T_f_min_C").GetDouble();
            var tFMax = sizingResult.GetProperty("T_f_max_C").GetDouble();

            // Both size_field and minimum_tower_capacity hand back the hourly series of the design they
            // settled on; in the tower case the ground load is the tower-adjusted one it reports.
            // minimum_tower_capacity's (peak-shaving) tower.ground_load_W is one representative year;
            // minimum_deadband_tower_capacity's spans the full periodYears-repeated series (the deadband
            // dispatch ran inside that whole series, not on one year precomputed beforehand) -- take
            // just the first year so the monthly chart below compares like with like either way.
            var resultDepth = towerNeeded ? hMax : sizingResult.GetProperty("H_m").GetDouble();
            var groundLoad = towerNeeded
                ? ReadDoubleArray(sizingResult.GetProperty("tower"), "ground_load_W").Take(HoursPerYear).ToList()
                : oneYearLoad;
            Area_TempChart.Model = Charts.TemperatureHistory(
                ReadDoubleArray(sizingResult, "T_f_C"), ReadDoubleArray(sizingResult, "T_b_C"),
                minFluidTemp, maxFluidTemp, $"Fluid temperature at H = {resultDepth:N1} m over {periodYears} years");
            Area_LoadChart.Model = Charts.MonthlyLoad(groundLoad, towerNeeded ? oneYearLoad : null);

            if (!towerNeeded)
            {
                var sizedH = sizingResult.GetProperty("H_m").GetDouble();
                Area_ResultText.Text =
                    "No dry cooler needed.\n" +
                    $"Minimum depth: H = {sizedH:N1} m\n" +
                    $"{nBoreholes} boreholes x {sizedH:N1} m = {sizedH * nBoreholes:N0} m total drilled length\n" +
                    $"R_b* = {rbStar:N4} m.K/W\n" +
                    $"Fluid temperature: {tFMin:N1} C .. {tFMax:N1} C (limits: {minFluidTemp:N1} .. {maxFluidTemp:N1} C)";

                N1Box.Text = n1.ToString(CultureInfo.InvariantCulture);
                N2Box.Text = n2.ToString(CultureInfo.InvariantCulture);
                DepthBox.Text = sizedH.ToString("0.0", CultureInfo.InvariantCulture);
                TowerEnabledCheckBox.IsChecked = false;
            }
            else
            {
                var towerCapacity = sizingResult.GetProperty("tower_capacity_kW").GetDouble();
                var towerStats = sizingResult.GetProperty("tower");
                var towerPeak = towerStats.GetProperty("tower_peak_kW").GetDouble();
                // minimum_tower_capacity's (peak-shaving) and minimum_wet_bulb_tower_capacity's tower
                // stats were both computed on a single representative year, already per-year;
                // minimum_deadband_tower_capacity ran on the full periodYears-repeated series
                // internally, so its totals need dividing -- same asymmetry as the Sizing tab (see
                // SizeFieldButton_Click).
                var areaIsDeadband = areaTowerStrategy == TowerStrategy.Deadband;
                var towerHoursPerYear = areaIsDeadband
                    ? towerStats.GetProperty("tower_hours").GetInt32() / (double)periodYears
                    : towerStats.GetProperty("tower_hours").GetInt32();
                var towerEnergyPerYear = areaIsDeadband
                    ? towerStats.GetProperty("tower_energy_kWh").GetDouble() / periodYears
                    : towerStats.GetProperty("tower_energy_kWh").GetDouble();
                var areaStrategyLabel = areaTowerStrategy switch
                {
                    TowerStrategy.Deadband => $", ground-temp deadband ({ParseDouble(Area_TowerDeadbandBox, "Dry cooler deadband"):N1} C band)",
                    TowerStrategy.WetBulbThreshold => $", wet-bulb threshold ({ParseDouble(Area_TowerWbtThresholdBox, "Dry cooler wet-bulb threshold"):N1} C)",
                    _ => "",
                };

                Area_ResultText.Text =
                    $"Field alone cannot meet the peak loads within {hMax:N0} m (max practical depth).\n" +
                    $"Dry cooler required: {towerCapacity:N1} kW rated capacity{areaStrategyLabel}\n" +
                    $"At H = {hMax:N0} m: {nBoreholes} boreholes x {hMax:N0} m = {hMax * nBoreholes:N0} m total drilled length\n" +
                    $"R_b* = {rbStar:N4} m.K/W\n" +
                    $"Fluid temperature achieved: {tFMin:N1} C .. {tFMax:N1} C (limits: {minFluidTemp:N1} .. {maxFluidTemp:N1} C)\n" +
                    $"Dry cooler duty: peak {towerPeak:N1} kW, runs {towerHoursPerYear:N0} h/yr avg, rejects {towerEnergyPerYear:N0} kWh/yr avg";

                N1Box.Text = n1.ToString(CultureInfo.InvariantCulture);
                N2Box.Text = n2.ToString(CultureInfo.InvariantCulture);
                DepthBox.Text = hMax.ToString("0.0", CultureInfo.InvariantCulture);
                TowerCapacityBox.Text = towerCapacity.ToString("0.0", CultureInfo.InvariantCulture);
                TowerEnabledCheckBox.IsChecked = true;
                // Mirror the strategy that was actually used into the shared panel too, so it's
                // consistent if the user switches to Simulation/Sizing afterward.
                TowerStrategyCombo.SelectedIndex = areaTowerStrategy switch
                {
                    TowerStrategy.Deadband => 1,
                    TowerStrategy.WetBulbThreshold => 2,
                    _ => 0,
                };
                if (areaTowerStrategy == TowerStrategy.Deadband)
                {
                    TowerDeadbandBox.Text = Area_TowerDeadbandBox.Text;
                }
                else if (areaTowerStrategy == TowerStrategy.WetBulbThreshold)
                {
                    TowerWbtThresholdBox.Text = Area_TowerWbtThresholdBox.Text;
                    TowerWbtMinBox.Text = Area_TowerWbtMinBox.Text;
                    TowerWbtMaxBox.Text = Area_TowerWbtMaxBox.Text;
                }
            }

            Area_StatusText.Text = string.Empty;
        }
        catch (EngineException ex)
        {
            Area_StatusText.Text = ex.PythonTraceback is null
                ? $"Engine error: {ex.Message}"
                : $"Engine error: {ex.Message}\n\n{ex.PythonTraceback}";
        }
        catch (Exception ex)
        {
            Area_StatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            AreaSizeButton.IsEnabled = true;
        }
    }

    private async void TrtRunButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            Trt_StatusText.Text = "Engine not available -- see message above.";
            return;
        }

        TrtRunButton.IsEnabled = false;
        Trt_StatusText.Foreground = Brushes.DarkRed;
        Trt_StatusText.Text = "Fitting...";
        Trt_ResultText.Text = string.Empty;
        Trt_Chart.Model = Charts.Empty("TRT result");

        try
        {
            var depth = ParseDouble(DepthBox, "Borehole depth H");
            var buriedDepth = ParseDouble(BuriedDepthBox, "Buried depth D");
            var boreholeRadius = ParseDouble(BoreholeRadiusBox, "Borehole radius");
            var groundTemp = ParseDouble(GroundTempBox, "Ground temperature");
            var rhoCp = ParseDouble(Trt_RhoCpBox, "Ground volumetric heat capacity");
            var ksGuess = ParseDouble(Trt_KsGuessBox, "k_s initial guess");
            var rbGuess = ParseDouble(Trt_RbGuessBox, "R_b initial guess");
            var tMinHours = ParseDouble(Trt_TMinHoursBox, "Exclude data before (hours)");

            if (_trtRows.Count < 3)
                throw new InvalidOperationException("Need at least 3 rows of TRT log data.");

            var timeS = _trtRows.Select(r => r.Hour * 3600.0).ToList();
            var tFMeasured = _trtRows.Select(r => r.TFMeasuredC).ToList();
            var qW = _trtRows.Select(r => r.QKW * 1000.0).ToList();

            var result = await _engine.CallAsync("estimate_ground_properties_from_trt", new Dictionary<string, object?>
            {
                ["H"] = depth, ["D"] = buriedDepth, ["r_b"] = boreholeRadius,
                ["time_s"] = timeS, ["T_f_C"] = tFMeasured, ["Q_W"] = qW,
                ["T_g"] = groundTemp, ["rho_cp_J_m3K"] = rhoCp,
                ["k_s_guess"] = ksGuess, ["R_b_guess"] = rbGuess, ["t_min_s"] = tMinHours * 3600.0,
            });

            var ksFit = result.GetProperty("k_s_W_mK").GetDouble();
            var ksStdErr = result.GetProperty("k_s_stderr_W_mK").GetDouble();
            var rbFit = result.GetProperty("R_b_mK_W").GetDouble();
            var rbStdErr = result.GetProperty("R_b_stderr_mK_W").GetDouble();
            var alpha = result.GetProperty("alpha_m2_s").GetDouble();
            var nUsed = result.GetProperty("n_points_used").GetInt32();
            var nTotal = result.GetProperty("n_points_total").GetInt32();
            var rmse = result.GetProperty("rmse_C").GetDouble();
            var predicted = ReadDoubleArray(result, "T_f_predicted_C");

            Trt_ResultText.Text =
                $"k_s = {ksFit:N3} ± {ksStdErr:N3} W/m.K\n" +
                $"R_b = {rbFit:N4} ± {rbStdErr:N4} m.K/W (fit jointly with k_s)\n" +
                $"Implied alpha = {alpha:0.###e+0} m2/s\n" +
                $"Used {nUsed} of {nTotal} logged points (after excluding the first {tMinHours:N0} h)\n" +
                $"Fit RMSE = {rmse:N3} C\n\n" +
                "Apply k_s to the left panel's Conductivity box, and R_b to your pipe/grout choice there, " +
                "once you're confident in this result -- not done automatically.";

            Trt_Chart.Model = Charts.TrtFit(timeS.Select(t => t / 3600.0).ToList(), tFMeasured, predicted, tMinHours);

            Trt_StatusText.Text = string.Empty;
        }
        catch (EngineException ex)
        {
            Trt_StatusText.Text = ex.PythonTraceback is null
                ? $"Engine error: {ex.Message}"
                : $"Engine error: {ex.Message}\n\n{ex.PythonTraceback}";
        }
        catch (Exception ex)
        {
            Trt_StatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            TrtRunButton.IsEnabled = true;
        }
    }

    // The "higher required capacity" the flow suggestion is based on: the largest peak
    // heating/cooling value found anywhere in the shared UI right now -- the monthly load
    // grid's per-month peak columns (Simulation/Sizing) and Area Sizing's explicit peak
    // fields. Returns null if nothing usable is filled in yet.
    private double? FindHighestRequiredCapacityKW()
    {
        double? best = null;
        void Consider(double value)
        {
            if (value > 0 && (best is null || value > best))
                best = value;
        }

        foreach (var row in _loadRows)
        {
            Consider(row.PeakHeatingKW);
            Consider(row.PeakCoolingKW);
        }
        if (double.TryParse(Area_PeakHeatingBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var peakHeating))
            Consider(peakHeating);
        if (double.TryParse(Area_PeakCoolingBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var peakCooling))
            Consider(peakCooling);

        return best;
    }

    private async void AutoCalcFlowButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            FlowAutoCalcStatusText.Text = "Engine not available.";
            return;
        }
        if (!ValidatePipeAndGroutSelected(FlowAutoCalcStatusText)) return;

        var capacityKW = FindHighestRequiredCapacityKW();
        if (capacityKW is null)
        {
            FlowAutoCalcStatusText.Text =
                "No peak heating/cooling capacity found yet -- fill in the monthly load grid or Area Sizing's peak loads first.";
            return;
        }

        AutoCalcFlowButton.IsEnabled = false;
        try
        {
            var n1 = ParseInt(N1Box, "Boreholes X");
            var n2 = ParseInt(N2Box, "Boreholes Y");
            var nBoreholes = n1 * n2;
            var capacityPerBoreholeW = capacityKW.Value * 1000.0 / nBoreholes;

            var pipeConfig = BuildPipeConfig();
            var fluidStr = SelectedText(FluidCombo);
            var fluidPercent = ParseDouble(FluidPercentBox, "Fluid percent");
            var fluidTemp = ParseDouble(FluidTemperatureBox, "Fluid temperature");

            var result = await _engine.CallAsync("suggest_flow_rate", new Dictionary<string, object?>
            {
                ["capacity_W"] = capacityPerBoreholeW, ["config"] = pipeConfig,
                ["fluid_str"] = fluidStr, ["fluid_percent"] = fluidPercent, ["fluid_temperature_C"] = fluidTemp,
                ["design_delta_T_C"] = AppSettings.Current.FlowDesignDeltaT,
                ["min_reynolds"] = AppSettings.Current.FlowMinReynolds,
            });

            var flow = result.GetProperty("flow_rate_kg_s").GetDouble();
            var governing = result.GetProperty("governing").GetString();
            FlowPerBoreholeBox.Text = flow.ToString("0.###", CultureInfo.InvariantCulture);
            FlowAutoCalcStatusText.Text =
                $"{flow:0.###} kg/s from {capacityKW:N1} kW over {nBoreholes} boreholes, {governing}-governed " +
                $"(uses the field layout and pipe config currently set above).";
        }
        catch (EngineException ex)
        {
            FlowAutoCalcStatusText.Text = ex.PythonTraceback is null
                ? $"Engine error: {ex.Message}"
                : $"Engine error: {ex.Message}\n\n{ex.PythonTraceback}";
        }
        catch (Exception ex)
        {
            FlowAutoCalcStatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            AutoCalcFlowButton.IsEnabled = true;
        }
    }

    // ====================== Project save/load, iterations, settings, help ======================

    private static double ParseDoubleOrDefault(TextBox box, double fallback) =>
        double.TryParse(box.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) ? value : fallback;

    private static int ParseIntOrDefault(TextBox box, int fallback) =>
        int.TryParse(box.Text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value) ? value : fallback;

    private static void SetComboIndexSafe(ComboBox combo, int index) =>
        combo.SelectedIndex = index >= -1 && index < combo.Items.Count ? index : -1;

    private ProjectInputs CaptureCurrentInputs() => new()
    {
        N1 = ParseIntOrDefault(N1Box, 4),
        N2 = ParseIntOrDefault(N2Box, 3),
        Spacing = ParseDoubleOrDefault(SpacingBox, 6.0),
        Depth = ParseDoubleOrDefault(DepthBox, 150.0),
        BuriedDepth = ParseDoubleOrDefault(BuriedDepthBox, 2.0),
        BoreholeRadius = ParseDoubleOrDefault(BoreholeRadiusBox, 0.075),

        Alpha = ParseDoubleOrDefault(AlphaBox, 1.0e-6),
        Conductivity = ParseDoubleOrDefault(ConductivityBox, 2.0),
        GroundTemp = ParseDoubleOrDefault(GroundTempBox, 12.0),

        PipeManufacturerIndex = PipeManufacturerCombo.SelectedIndex,
        PipeProductIndex = PipeProductCombo.SelectedIndex,
        PipeConfigIndex = PipeConfigCombo.SelectedIndex,
        GroutManufacturerIndex = GroutManufacturerCombo.SelectedIndex,
        GroutProductIndex = GroutProductCombo.SelectedIndex,
        GroutConductivity = ParseDoubleOrDefault(GroutConductivityBox, 0.0),
        ShankSpacing = ParseDoubleOrDefault(ShankSpacingBox, 30.0),
        PipeRIn = ParseDoubleOrDefault(PipeRInBox, 0.0),
        PipeROut = ParseDoubleOrDefault(PipeROutBox, 0.0),
        PipeConductivity = ParseDoubleOrDefault(PipeConductivityBox, 0.0),
        FlowPerBorehole = ParseDoubleOrDefault(FlowPerBoreholeBox, 0.30),
        FluidIndex = FluidCombo.SelectedIndex,
        FluidPercent = ParseDoubleOrDefault(FluidPercentBox, 0.0),
        FluidTemperature = ParseDoubleOrDefault(FluidTemperatureBox, 0.0),

        LoadRows = _loadRows.Select(r => new MonthlyLoadRow
        {
            Month = r.Month, BaseHeatingKWh = r.BaseHeatingKWh, BaseCoolingKWh = r.BaseCoolingKWh,
            PeakHeatingKW = r.PeakHeatingKW, PeakCoolingKW = r.PeakCoolingKW,
        }).ToList(),

        TowerEnabled = TowerEnabledCheckBox.IsChecked == true,
        TowerCapacity = ParseDoubleOrDefault(TowerCapacityBox, 50.0),

        BoundaryConditionIndex = BoundaryConditionCombo.SelectedIndex,
        SolverIndex = SolverCombo.SelectedIndex,
        DesignLife = ParseDoubleOrDefault(DesignLifeBox, 25.0),

        SimPeriodYears = ParseIntOrDefault(Sim_PeriodYearsBox, 20),
        SimAlgorithmIndex = Sim_AlgorithmCombo.SelectedIndex,

        SizeMinFluidTemp = ParseDoubleOrDefault(Size_MinFluidTempBox, -2.0),
        SizeMaxFluidTemp = ParseDoubleOrDefault(Size_MaxFluidTempBox, 20.0),
        SizePeriodYears = ParseIntOrDefault(Size_PeriodYearsBox, 20),
        SizeHMin = ParseDoubleOrDefault(Size_HMinBox, 20.0),
        SizeHMax = ParseDoubleOrDefault(Size_HMaxBox, 400.0),
        SizeCrossCheck = Size_CrossCheckCheckBox.IsChecked == true,

        AreaPeakHeating = ParseDoubleOrDefault(Area_PeakHeatingBox, 80.0),
        AreaPeakCooling = ParseDoubleOrDefault(Area_PeakCoolingBox, 120.0),
        AreaHeatingSeasonMonths = ParseIntOrDefault(Area_HeatingSeasonBox, 4),
        AreaCoolingSeasonMonths = ParseIntOrDefault(Area_CoolingSeasonBox, 4),
        AreaUseAmbient = Area_UseAmbientCheckBox.IsChecked == true,
        AreaAmbientArea = ParseDoubleOrDefault(Area_AmbientAreaBox, 1000.0),
        AreaUseUnderBuilding = Area_UseUnderBuildingCheckBox.IsChecked == true,
        AreaUnderBuildingArea = ParseDoubleOrDefault(Area_UnderBuildingAreaBox, 300.0),
        AreaMaxRows = ParseIntOrDefault(Area_MaxRowsBox, 0),
        AreaMinFluidTemp = ParseDoubleOrDefault(Area_MinFluidTempBox, -2.0),
        AreaMaxFluidTemp = ParseDoubleOrDefault(Area_MaxFluidTempBox, 35.0),
        AreaPeriodYears = ParseIntOrDefault(Area_PeriodYearsBox, 10),
        AreaHMin = ParseDoubleOrDefault(Area_HMinBox, 20.0),
        AreaHMax = ParseDoubleOrDefault(Area_HMaxBox, 200.0),
    };

    private void ApplyInputs(ProjectInputs p)
    {
        N1Box.Text = p.N1.ToString(CultureInfo.InvariantCulture);
        N2Box.Text = p.N2.ToString(CultureInfo.InvariantCulture);
        SpacingBox.Text = p.Spacing.ToString(CultureInfo.InvariantCulture);
        DepthBox.Text = p.Depth.ToString(CultureInfo.InvariantCulture);
        BuriedDepthBox.Text = p.BuriedDepth.ToString(CultureInfo.InvariantCulture);
        BoreholeRadiusBox.Text = p.BoreholeRadius.ToString(CultureInfo.InvariantCulture);

        AlphaBox.Text = p.Alpha.ToString(CultureInfo.InvariantCulture);
        ConductivityBox.Text = p.Conductivity.ToString(CultureInfo.InvariantCulture);
        GroundTempBox.Text = p.GroundTemp.ToString(CultureInfo.InvariantCulture);

        // Raw values first, THEN the combo selections -- if a saved index points at a real
        // catalog product, PipeProductCombo_SelectionChanged/GroutProductCombo_SelectionChanged
        // will auto-fill and lock these from the (possibly-updated-since-save) live catalog,
        // which should win; for "Custom"/unselected they're left as set here.
        GroutConductivityBox.Text = p.GroutConductivity.ToString(CultureInfo.InvariantCulture);
        ShankSpacingBox.Text = p.ShankSpacing.ToString(CultureInfo.InvariantCulture);
        PipeRInBox.Text = p.PipeRIn.ToString(CultureInfo.InvariantCulture);
        PipeROutBox.Text = p.PipeROut.ToString(CultureInfo.InvariantCulture);
        PipeConductivityBox.Text = p.PipeConductivity.ToString(CultureInfo.InvariantCulture);
        FlowPerBoreholeBox.Text = p.FlowPerBorehole.ToString(CultureInfo.InvariantCulture);
        FluidPercentBox.Text = p.FluidPercent.ToString(CultureInfo.InvariantCulture);
        FluidTemperatureBox.Text = p.FluidTemperature.ToString(CultureInfo.InvariantCulture);

        // Manufacturer first -- it repopulates the product combo (resetting it to -1), so the
        // product index below must be applied after, against the now-correct product list.
        SetComboIndexSafe(PipeManufacturerCombo, p.PipeManufacturerIndex);
        SetComboIndexSafe(PipeProductCombo, p.PipeProductIndex);
        SetComboIndexSafe(PipeConfigCombo, p.PipeConfigIndex);
        SetComboIndexSafe(GroutManufacturerCombo, p.GroutManufacturerIndex);
        SetComboIndexSafe(GroutProductCombo, p.GroutProductIndex);
        SetComboIndexSafe(FluidCombo, p.FluidIndex);

        _loadRows.Clear();
        foreach (var row in p.LoadRows)
            _loadRows.Add(row);

        TowerEnabledCheckBox.IsChecked = p.TowerEnabled;
        TowerCapacityBox.Text = p.TowerCapacity.ToString(CultureInfo.InvariantCulture);
        TowerCapacityBox.IsEnabled = p.TowerEnabled;

        SetComboIndexSafe(BoundaryConditionCombo, p.BoundaryConditionIndex);
        SetComboIndexSafe(SolverCombo, p.SolverIndex);
        DesignLifeBox.Text = p.DesignLife.ToString(CultureInfo.InvariantCulture);

        Sim_PeriodYearsBox.Text = p.SimPeriodYears.ToString(CultureInfo.InvariantCulture);
        SetComboIndexSafe(Sim_AlgorithmCombo, p.SimAlgorithmIndex);

        Size_MinFluidTempBox.Text = p.SizeMinFluidTemp.ToString(CultureInfo.InvariantCulture);
        Size_MaxFluidTempBox.Text = p.SizeMaxFluidTemp.ToString(CultureInfo.InvariantCulture);
        Size_PeriodYearsBox.Text = p.SizePeriodYears.ToString(CultureInfo.InvariantCulture);
        Size_HMinBox.Text = p.SizeHMin.ToString(CultureInfo.InvariantCulture);
        Size_HMaxBox.Text = p.SizeHMax.ToString(CultureInfo.InvariantCulture);
        // Set BEFORE PipeConfigCombo is applied above would seem simpler, but Size_CrossCheckCheckBox
        // doesn't exist yet the very first time PipeConfigCombo_SelectionChanged fires (during
        // InitializeComponent()); applying it here, after, means a saved series config's disable/
        // uncheck (already applied by that handler above) is never overwritten by an old saved
        // "checked" value for a project saved before that combination was disallowed.
        Size_CrossCheckCheckBox.IsChecked = p.SizeCrossCheck
            && !SelectedText(PipeConfigCombo).Contains("series", StringComparison.OrdinalIgnoreCase);

        Area_PeakHeatingBox.Text = p.AreaPeakHeating.ToString(CultureInfo.InvariantCulture);
        Area_PeakCoolingBox.Text = p.AreaPeakCooling.ToString(CultureInfo.InvariantCulture);
        Area_HeatingSeasonBox.Text = p.AreaHeatingSeasonMonths.ToString(CultureInfo.InvariantCulture);
        Area_CoolingSeasonBox.Text = p.AreaCoolingSeasonMonths.ToString(CultureInfo.InvariantCulture);
        Area_UseAmbientCheckBox.IsChecked = p.AreaUseAmbient;
        Area_AmbientAreaBox.Text = p.AreaAmbientArea.ToString(CultureInfo.InvariantCulture);
        Area_AmbientAreaBox.IsEnabled = p.AreaUseAmbient;
        Area_UseUnderBuildingCheckBox.IsChecked = p.AreaUseUnderBuilding;
        Area_UnderBuildingAreaBox.Text = p.AreaUnderBuildingArea.ToString(CultureInfo.InvariantCulture);
        Area_UnderBuildingAreaBox.IsEnabled = p.AreaUseUnderBuilding;
        Area_MaxRowsBox.Text = p.AreaMaxRows.ToString(CultureInfo.InvariantCulture);
        Area_MinFluidTempBox.Text = p.AreaMinFluidTemp.ToString(CultureInfo.InvariantCulture);
        Area_MaxFluidTempBox.Text = p.AreaMaxFluidTemp.ToString(CultureInfo.InvariantCulture);
        Area_PeriodYearsBox.Text = p.AreaPeriodYears.ToString(CultureInfo.InvariantCulture);
        Area_HMinBox.Text = p.AreaHMin.ToString(CultureInfo.InvariantCulture);
        Area_HMaxBox.Text = p.AreaHMax.ToString(CultureInfo.InvariantCulture);

        UpdatePipeGroutDependentFieldsAvailability();
        UpdateSharedInputAvailability();
    }

    private bool ConfirmDiscardCurrentWork(string action) =>
        MessageBox.Show($"{action} will replace all current inputs. Continue?", "GeoBore",
            MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes;

    private void UpdateWindowTitle()
    {
        var name = _currentProjectPath is null ? "Untitled" : Path.GetFileNameWithoutExtension(_currentProjectPath);
        Title = $"GeoBore - {name}";
    }

    private void RebuildRecentProjectsMenu()
    {
        RecentProjectsMenu.Items.Clear();
        var recents = AppSettings.Current.RecentProjects;
        if (recents.Count == 0)
        {
            RecentProjectsMenu.Items.Add(new MenuItem { Header = "(none)", IsEnabled = false });
            return;
        }
        foreach (var path in recents)
        {
            var item = new MenuItem { Header = path };
            item.Click += (_, _) => LoadProjectFromFile(path);
            RecentProjectsMenu.Items.Add(item);
        }
    }

    private void SetupAutosaveTimer()
    {
        _autosaveTimer?.Stop();
        var settings = AppSettings.Current;
        if (!settings.AutosaveEnabled)
            return;

        _autosaveTimer = new DispatcherTimer { Interval = TimeSpan.FromMinutes(Math.Max(1, settings.AutosaveIntervalMinutes)) };
        _autosaveTimer.Tick += (_, _) =>
        {
            if (_currentProjectPath is not null)
                SaveProjectToFile(_currentProjectPath, silent: true);
        };
        _autosaveTimer.Start();
    }

    private void NewProject_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmDiscardCurrentWork("Starting a new project"))
            return;
        ApplyInputs(new ProjectInputs());
        _openIterations = new List<ProjectIteration>();
        _currentProjectPath = null;
        UpdateWindowTitle();
    }

    private void OpenProject_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmDiscardCurrentWork("Opening a project"))
            return;
        var dialog = new OpenFileDialog { Filter = "GeoBore Project (*.geobore)|*.geobore|All files (*.*)|*.*", Title = "Open GeoBore Project" };
        if (dialog.ShowDialog() == true)
            LoadProjectFromFile(dialog.FileName);
    }

    private void LoadProjectFromFile(string path)
    {
        try
        {
            var project = JsonSerializer.Deserialize<ProjectFile>(File.ReadAllText(path))
                ?? throw new InvalidOperationException("the file is empty");
            ApplyInputs(project.CurrentInputs);
            _openIterations = project.Iterations;
            _currentProjectPath = path;
            AppSettings.Current.AddRecentProject(path);
            RebuildRecentProjectsMenu();
            UpdateWindowTitle();
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Could not open project:\n{ex.Message}", "GeoBore", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void SaveProject_Click(object sender, RoutedEventArgs e)
    {
        if (_currentProjectPath is null)
            SaveProjectAs_Click(sender, e);
        else
            SaveProjectToFile(_currentProjectPath);
    }

    private void SaveProjectAs_Click(object sender, RoutedEventArgs e)
    {
        var suggestedName = _currentProjectPath is null ? "Untitled.geobore" : Path.GetFileName(_currentProjectPath);
        var dialog = new SaveFileDialog { Filter = "GeoBore Project (*.geobore)|*.geobore", Title = "Save GeoBore Project", FileName = suggestedName };
        if (dialog.ShowDialog() != true)
            return;
        _currentProjectPath = dialog.FileName;
        SaveProjectToFile(_currentProjectPath);
    }

    private void SaveProjectToFile(string path, bool silent = false)
    {
        try
        {
            var project = new ProjectFile
            {
                ProjectName = Path.GetFileNameWithoutExtension(path),
                CurrentInputs = CaptureCurrentInputs(),
                Iterations = _openIterations,
            };
            File.WriteAllText(path, JsonSerializer.Serialize(project, new JsonSerializerOptions { WriteIndented = true }));
            AppSettings.Current.AddRecentProject(path);
            RebuildRecentProjectsMenu();
            UpdateWindowTitle();
        }
        catch (Exception ex) when (!silent)
        {
            MessageBox.Show($"Could not save project:\n{ex.Message}", "GeoBore", MessageBoxButton.OK, MessageBoxImage.Error);
        }
        catch
        {
            // Autosave: fail quietly rather than interrupt the user with a popup.
        }
    }

    private void SaveIteration_Click(object sender, RoutedEventArgs e)
    {
        var prompt = new IterationPromptWindow { Owner = this };
        if (prompt.ShowDialog() != true)
            return;

        _openIterations.Add(new ProjectIteration
        {
            Name = prompt.IterationName,
            Notes = prompt.Notes,
            SavedAtUtc = DateTime.UtcNow,
            Inputs = CaptureCurrentInputs(),
        });

        if (_currentProjectPath is not null)
            SaveProjectToFile(_currentProjectPath);

        MessageBox.Show($"Saved iteration \"{prompt.IterationName}\".", "GeoBore");
    }

    private void IterationHistory_Click(object sender, RoutedEventArgs e)
    {
        var window = new IterationHistoryWindow(_openIterations) { Owner = this };
        var loaded = window.ShowDialog() == true && window.SelectedIteration is not null;
        if (loaded)
            ApplyInputs(window.SelectedIteration!.Inputs);

        if (window.DeletedAny)
        {
            _openIterations = window.Iterations;
            if (_currentProjectPath is not null)
                SaveProjectToFile(_currentProjectPath);
        }
    }

    private void Exit_Click(object sender, RoutedEventArgs e) => Close();

    private void OpenSettings_Click(object sender, RoutedEventArgs e)
    {
        var window = new SettingsWindow { Owner = this };
        if (window.ShowDialog() == true)
            SetupAutosaveTimer();
    }

    private void ViewReadme_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var readmePath = Path.Combine(EngineClient.FindRepoRoot(), "geothermal", "README.md");
            if (!File.Exists(readmePath))
            {
                MessageBox.Show($"README not found at:\n{readmePath}", "GeoBore");
                return;
            }
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(readmePath) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Could not open README:\n{ex.Message}", "GeoBore");
        }
    }

    private async void EngineDiagnostics_Click(object sender, RoutedEventArgs e)
    {
        if (_engine is null)
        {
            MessageBox.Show("Engine not available.", "Engine Diagnostics");
            return;
        }

        try
        {
            var commands = await _engine.CallAsync("list_commands");
            var pipes = await _engine.CallAsync("list_rehau_pipes");
            var grouts = await _engine.CallAsync("list_rehau_grouts");
            MessageBox.Show(
                $"Python interpreter: {_engine.PythonExePath}\n" +
                $"Commands available: {commands.GetArrayLength()}\n" +
                $"REHAU pipes loaded: {pipes.GetArrayLength()}\n" +
                $"REHAU grouts loaded: {grouts.GetArrayLength()}\n" +
                "Status: OK",
                "Engine Diagnostics");
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Python interpreter: {_engine.PythonExePath}\nEngine call failed: {ex.Message}", "Engine Diagnostics");
        }
    }

    private void About_Click(object sender, RoutedEventArgs e) => new AboutWindow { Owner = this }.ShowDialog();
}
