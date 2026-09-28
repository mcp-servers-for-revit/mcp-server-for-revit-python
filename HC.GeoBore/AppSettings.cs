using System.IO;
using System.Text.Json;

namespace HC.GeoBore;

/// <summary>
/// Small app-level settings persisted to %APPDATA%\GeoBore\settings.json --
/// separate from a .geobore project file, since these are per-machine tool
/// preferences (engine override, flow defaults, autosave, recent files),
/// not part of any one design.
/// </summary>
public sealed class AppSettings
{
    private static readonly string SettingsDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "GeoBore");
    private static readonly string SettingsPath = Path.Combine(SettingsDir, "settings.json");

    public string? EnginePathOverride { get; set; }
    public double FlowDesignDeltaT { get; set; } = 5.0;
    public double FlowMinReynolds { get; set; } = 4000.0;
    public bool AutosaveEnabled { get; set; }
    public int AutosaveIntervalMinutes { get; set; } = 5;
    public List<string> RecentProjects { get; set; } = new();

    private static AppSettings? _current;
    public static AppSettings Current => _current ??= Load();

    private static AppSettings Load()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                var loaded = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(SettingsPath));
                if (loaded is not null)
                    return loaded;
            }
        }
        catch
        {
            // A corrupt or unreadable settings file shouldn't block startup -- fall through to defaults.
        }
        return new AppSettings();
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(SettingsDir);
            File.WriteAllText(SettingsPath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch
        {
            // Best-effort -- settings persistence failing shouldn't crash the app.
        }
    }

    public void AddRecentProject(string path)
    {
        RecentProjects.RemoveAll(p => string.Equals(p, path, StringComparison.OrdinalIgnoreCase));
        RecentProjects.Insert(0, path);
        while (RecentProjects.Count > 5)
            RecentProjects.RemoveAt(RecentProjects.Count - 1);
        Save();
    }
}
