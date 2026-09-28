namespace HC.GeoBore;

/// <summary>
/// Every input control's value across the shared panel and all 4 tabs --
/// deliberately NOT results (those are cheap to recompute and would just
/// bloat the file / go stale). Combo selections are saved as plain
/// SelectedIndex; for the manufacturer/product combos that means a
/// project file is tied to the vendor catalog's current order (which
/// manufacturer is Nth in the list, which product is Mth for that
/// manufacturer) -- a documented simplification, not a text-matching
/// resolver.
/// </summary>
public sealed class ProjectInputs
{
    // Field layout
    public int N1 { get; set; } = 4;
    public int N2 { get; set; } = 3;
    public double Spacing { get; set; } = 6.0;
    public double Depth { get; set; } = 150.0;
    public double BuriedDepth { get; set; } = 2.0;
    public double BoreholeRadius { get; set; } = 0.075;

    // Ground properties
    public double Alpha { get; set; } = 1.0e-6;
    public double Conductivity { get; set; } = 2.0;
    public double GroundTemp { get; set; } = 12.0;

    // Pipe and fluid
    public int PipeManufacturerIndex { get; set; } = -1;
    public int PipeProductIndex { get; set; } = -1;
    public int PipeConfigIndex { get; set; } = 0;
    public int GroutManufacturerIndex { get; set; } = -1;
    public int GroutProductIndex { get; set; } = -1;
    public double GroutConductivity { get; set; }
    public double ShankSpacing { get; set; } = 30.0;
    public double PipeRIn { get; set; }
    public double PipeROut { get; set; }
    public double PipeConductivity { get; set; }
    public double FlowPerBorehole { get; set; } = 0.30;
    public int FluidIndex { get; set; } = -1;
    public double FluidPercent { get; set; }
    public double FluidTemperature { get; set; }

    // Monthly load profile (shared by Simulation/Sizing)
    public List<MonthlyLoadRow> LoadRows { get; set; } = new();

    // Hybrid cooling tower
    public bool TowerEnabled { get; set; }
    public double TowerCapacity { get; set; } = 50.0;

    // g-Function tab
    public int BoundaryConditionIndex { get; set; }
    public int SolverIndex { get; set; }
    public double DesignLife { get; set; } = 25.0;

    // Simulation tab
    public int SimPeriodYears { get; set; } = 20;
    public int SimAlgorithmIndex { get; set; }

    // Sizing tab
    public double SizeMinFluidTemp { get; set; } = -2.0;
    public double SizeMaxFluidTemp { get; set; } = 20.0;
    public int SizePeriodYears { get; set; } = 20;
    public double SizeHMin { get; set; } = 20.0;
    public double SizeHMax { get; set; } = 400.0;
    public bool SizeCrossCheck { get; set; }

    // Area Sizing tab
    public double AreaPeakHeating { get; set; } = 80.0;
    public double AreaPeakCooling { get; set; } = 120.0;
    public int AreaHeatingSeasonMonths { get; set; } = 4;
    public int AreaCoolingSeasonMonths { get; set; } = 4;
    public bool AreaUseAmbient { get; set; } = true;
    public double AreaAmbientArea { get; set; } = 1000.0;
    public bool AreaUseUnderBuilding { get; set; } = true;
    public double AreaUnderBuildingArea { get; set; } = 300.0;
    public int AreaMaxRows { get; set; }
    public double AreaMinFluidTemp { get; set; } = -2.0;
    public double AreaMaxFluidTemp { get; set; } = 35.0;
    public int AreaPeriodYears { get; set; } = 10;
    public double AreaHMin { get; set; } = 20.0;
    public double AreaHMax { get; set; } = 200.0;
}

/// <summary>A named snapshot of ProjectInputs, embedded in a ProjectFile.</summary>
public sealed class ProjectIteration
{
    public string Name { get; set; } = "";
    public DateTime SavedAtUtc { get; set; } = DateTime.UtcNow;
    public string? Notes { get; set; }
    public ProjectInputs Inputs { get; set; } = new();
}

/// <summary>
/// The on-disk shape of a .geobore project file: the live working inputs,
/// plus every named iteration saved alongside them in the same file.
/// </summary>
public sealed class ProjectFile
{
    public const string CurrentSchemaVersion = "1.0";

    public string SchemaVersion { get; set; } = CurrentSchemaVersion;
    public string ProjectName { get; set; } = "Untitled";
    public ProjectInputs CurrentInputs { get; set; } = new();
    public List<ProjectIteration> Iterations { get; set; } = new();
}
