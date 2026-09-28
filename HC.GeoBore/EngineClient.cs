using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace HC.GeoBore;

/// <summary>
/// Thrown when geothermal.engine_cli returns {"error": ...}, or the process
/// itself fails to run or produce parseable JSON.
/// </summary>
public sealed class EngineException : Exception
{
    public EngineException(string message, string? pythonTraceback = null) : base(message)
    {
        PythonTraceback = pythonTraceback;
    }

    public string? PythonTraceback { get; }
}

/// <summary>
/// Calls geothermal.engine_cli as a subprocess: one process per request, JSON
/// on stdin, JSON on stdout. See geothermal/engine_cli.py and
/// geothermal/README.md for the protocol and why GHEtool-backed commands
/// (size_field_ghetool) must stay isolated this way.
///
/// Points at the repo's own .venv interpreter during development. When this
/// app is packaged for distribution, swap CreateDefault() for a frozen
/// engine .exe's path instead of a venv python.exe -- the protocol on stdin/
/// stdout does not change.
/// </summary>
public sealed class EngineClient
{
    private readonly string _pythonExePath;
    private readonly string _workingDirectory;

    public string PythonExePath => _pythonExePath;

    public EngineClient(string pythonExePath, string workingDirectory)
    {
        _pythonExePath = pythonExePath;
        _workingDirectory = workingDirectory;
    }

    public static EngineClient CreateDefault()
    {
        var repoRoot = FindRepoRoot();

        // Settings > Engine lets the user point at a different python.exe (e.g. a separate
        // venv); takes effect on next launch/EngineClient creation, not hot-swapped mid-session.
        var overridePath = AppSettings.Current.EnginePathOverride;
        if (!string.IsNullOrWhiteSpace(overridePath) && File.Exists(overridePath))
            return new EngineClient(overridePath, repoRoot);

        var pythonExe = Path.Combine(repoRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(pythonExe))
        {
            throw new FileNotFoundException(
                $"Python venv interpreter not found at '{pythonExe}'. Expected the geothermal engine's " +
                "virtual environment at <repo root>\\.venv\\Scripts\\python.exe -- run 'uv sync' in the repo first.",
                pythonExe);
        }
        return new EngineClient(pythonExe, repoRoot);
    }

    internal static string FindRepoRoot()
    {
        // Walk up from the running assembly looking for the geothermal/ package,
        // so this resolves correctly whether run from bin\Debug\net8.0-windows\
        // or a future installed location that sits elsewhere relative to it.
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        while (current is not null)
        {
            if (Directory.Exists(Path.Combine(current.FullName, "geothermal")))
                return current.FullName;
            current = current.Parent;
        }
        throw new DirectoryNotFoundException(
            $"Could not find a 'geothermal' package directory above '{AppContext.BaseDirectory}'. " +
            "Run HC.GeoBore from within (or under) the RevitMCP repo.");
    }

    /// <param name="args">
    /// Argument names must match the Python function's parameter names exactly
    /// (e.g. "N_1", "r_b") -- use a Dictionary&lt;string, object?&gt; rather than
    /// a typed class, since dictionary string keys are written verbatim by
    /// System.Text.Json (no camelCase/naming-policy rewriting), unlike class
    /// property names.
    /// </param>
    public async Task<JsonElement> CallAsync(
        string command, IReadOnlyDictionary<string, object?>? args = null, CancellationToken cancellationToken = default)
    {
        var requestJson = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["command"] = command,
            ["args"] = args ?? new Dictionary<string, object?>(),
        });

        var startInfo = new ProcessStartInfo
        {
            FileName = _pythonExePath,
            WorkingDirectory = _workingDirectory,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        startInfo.ArgumentList.Add("-m");
        startInfo.ArgumentList.Add("geothermal.engine_cli");

        using var process = new Process { StartInfo = startInfo };
        process.Start();

        await process.StandardInput.WriteAsync(requestJson.AsMemory(), cancellationToken);
        process.StandardInput.Close();

        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.WaitForExitAsync(cancellationToken);
        var stdout = await stdoutTask;
        var stderr = await stderrTask;

        if (process.ExitCode != 0)
        {
            throw new EngineException(
                $"geothermal.engine_cli exited {process.ExitCode} for command '{command}'.\n{stderr}");
        }

        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(stdout);
        }
        catch (JsonException ex)
        {
            throw new EngineException(
                $"engine_cli returned non-JSON output for command '{command}': {ex.Message}\n" +
                $"stdout: {stdout}\nstderr: {stderr}");
        }

        using (doc)
        {
            var root = doc.RootElement;
            if (root.TryGetProperty("error", out var errorElement))
            {
                var message = errorElement.GetString() ?? "unknown engine error";
                var traceback = root.TryGetProperty("traceback", out var tbElement) ? tbElement.GetString() : null;
                throw new EngineException(message, traceback);
            }
            if (!root.TryGetProperty("result", out var resultElement))
            {
                throw new EngineException(
                    $"engine_cli response for '{command}' had neither 'result' nor 'error': {stdout}");
            }
            return resultElement.Clone();
        }
    }
}
