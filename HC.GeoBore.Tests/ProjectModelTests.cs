using System.Text.Json;

namespace HC.GeoBore.Tests;

/// <summary>
/// Pure C# round-trip tests for the .geobore project file format -- no
/// engine subprocess needed, since ProjectInputs/ProjectIteration/ProjectFile
/// are plain DTOs serialized with System.Text.Json, same as MainWindow's
/// SaveProjectToFile/LoadProjectFromFile do.
/// </summary>
public class ProjectModelTests
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    [Fact]
    public void ProjectFile_RoundTripsThroughJson()
    {
        var original = new ProjectFile
        {
            ProjectName = "Test Building",
            CurrentInputs = new ProjectInputs
            {
                N1 = 5, N2 = 4, Spacing = 7.5, Depth = 180.0,
                PipeProductIndex = 2, GroutProductIndex = 1, FluidIndex = 3,
                TowerEnabled = true, TowerCapacity = 42.0,
                AreaUseAmbient = false, AreaAmbientArea = 999.0,
            },
        };
        original.CurrentInputs.LoadRows.Add(new MonthlyLoadRow
        {
            Month = "Jan", BaseHeatingKWh = 100.0, BaseCoolingKWh = 0.0, PeakHeatingKW = 10.0, PeakCoolingKW = 0.0,
        });

        var json = JsonSerializer.Serialize(original, Options);
        var restored = JsonSerializer.Deserialize<ProjectFile>(json);

        Assert.NotNull(restored);
        Assert.Equal(original.ProjectName, restored!.ProjectName);
        Assert.Equal(original.CurrentInputs.N1, restored.CurrentInputs.N1);
        Assert.Equal(original.CurrentInputs.Depth, restored.CurrentInputs.Depth);
        Assert.Equal(original.CurrentInputs.PipeProductIndex, restored.CurrentInputs.PipeProductIndex);
        Assert.Equal(original.CurrentInputs.TowerEnabled, restored.CurrentInputs.TowerEnabled);
        Assert.Equal(original.CurrentInputs.TowerCapacity, restored.CurrentInputs.TowerCapacity);
        Assert.Equal(original.CurrentInputs.AreaUseAmbient, restored.CurrentInputs.AreaUseAmbient);
        Assert.Single(restored.CurrentInputs.LoadRows);
        Assert.Equal("Jan", restored.CurrentInputs.LoadRows[0].Month);
        Assert.Equal(10.0, restored.CurrentInputs.LoadRows[0].PeakHeatingKW);
    }

    [Fact]
    public void ProjectFile_PreservesEmbeddedIterations()
    {
        var project = new ProjectFile { ProjectName = "Iterated" };
        project.Iterations.Add(new ProjectIteration
        {
            Name = "Baseline", Notes = "no tower", SavedAtUtc = new DateTime(2026, 1, 1, 12, 0, 0, DateTimeKind.Utc),
            Inputs = new ProjectInputs { N1 = 4, N2 = 3 },
        });
        project.Iterations.Add(new ProjectIteration
        {
            Name = "With tower", Notes = null, SavedAtUtc = new DateTime(2026, 1, 2, 12, 0, 0, DateTimeKind.Utc),
            Inputs = new ProjectInputs { N1 = 4, N2 = 3, TowerEnabled = true, TowerCapacity = 60.0 },
        });

        var json = JsonSerializer.Serialize(project, Options);
        var restored = JsonSerializer.Deserialize<ProjectFile>(json);

        Assert.NotNull(restored);
        Assert.Equal(2, restored!.Iterations.Count);
        Assert.Equal("Baseline", restored.Iterations[0].Name);
        Assert.Equal("no tower", restored.Iterations[0].Notes);
        Assert.False(restored.Iterations[0].Inputs.TowerEnabled);
        Assert.True(restored.Iterations[1].Inputs.TowerEnabled);
        Assert.Equal(60.0, restored.Iterations[1].Inputs.TowerCapacity);
    }

    [Fact]
    public void ProjectFile_DefaultsToCurrentSchemaVersion()
    {
        var project = new ProjectFile();
        Assert.Equal(ProjectFile.CurrentSchemaVersion, project.SchemaVersion);
    }

    [Fact]
    public void ProjectInputs_DefaultsAreSensibleAndUsable()
    {
        // Same defaults MainWindow's default-populated boxes ship with, so New Project
        // produces a runnable starting point, not a blank/zeroed-out one.
        var inputs = new ProjectInputs();
        Assert.True(inputs.N1 > 0 && inputs.N2 > 0);
        Assert.True(inputs.Depth > 0);
        Assert.True(inputs.Spacing > 0);
        Assert.True(inputs.BoreholeRadius > 0);
    }
}
