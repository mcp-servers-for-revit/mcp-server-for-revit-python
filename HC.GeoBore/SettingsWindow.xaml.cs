using System.Globalization;
using System.Windows;
using System.Windows.Media;
using Microsoft.Win32;

namespace HC.GeoBore;

public partial class SettingsWindow : Window
{
    public SettingsWindow()
    {
        InitializeComponent();

        try
        {
            CurrentEnginePathText.Text = EngineClient.CreateDefault().PythonExePath;
        }
        catch (Exception ex)
        {
            CurrentEnginePathText.Text = $"(not found: {ex.Message})";
        }

        var settings = AppSettings.Current;
        EngineOverrideBox.Text = settings.EnginePathOverride ?? "";
        FlowDeltaTBox.Text = settings.FlowDesignDeltaT.ToString(CultureInfo.InvariantCulture);
        FlowMinReynoldsBox.Text = settings.FlowMinReynolds.ToString(CultureInfo.InvariantCulture);
        AutosaveEnabledCheckBox.IsChecked = settings.AutosaveEnabled;
        AutosaveIntervalBox.Text = settings.AutosaveIntervalMinutes.ToString(CultureInfo.InvariantCulture);
    }

    private void BrowseEngineButton_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog
        {
            Filter = "python.exe|python.exe|Executable (*.exe)|*.exe|All files (*.*)|*.*",
            Title = "Select python.exe",
        };
        if (dialog.ShowDialog() == true)
            EngineOverrideBox.Text = dialog.FileName;
    }

    private async void TestConnectionButton_Click(object sender, RoutedEventArgs e)
    {
        // Temporarily point AppSettings at whatever's in the box right now (reusing
        // CreateDefault()'s resolution logic) without persisting -- lets the user try an
        // override before committing to it with OK.
        var previousOverride = AppSettings.Current.EnginePathOverride;
        AppSettings.Current.EnginePathOverride = string.IsNullOrWhiteSpace(EngineOverrideBox.Text) ? null : EngineOverrideBox.Text.Trim();
        TestConnectionResultText.Foreground = Brushes.Black;
        TestConnectionResultText.Text = "Testing...";

        try
        {
            var testClient = EngineClient.CreateDefault();
            var result = await testClient.CallAsync("list_commands");
            TestConnectionResultText.Foreground = Brushes.DarkGreen;
            TestConnectionResultText.Text = $"OK -- {result.GetArrayLength()} commands available ({testClient.PythonExePath}).";
        }
        catch (Exception ex)
        {
            TestConnectionResultText.Foreground = Brushes.DarkRed;
            TestConnectionResultText.Text = $"Failed: {ex.Message}";
        }
        finally
        {
            AppSettings.Current.EnginePathOverride = previousOverride;
        }
    }

    private static double ParseDoubleOrDefault(System.Windows.Controls.TextBox box, double fallback) =>
        double.TryParse(box.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) ? value : fallback;

    private static int ParseIntOrDefault(System.Windows.Controls.TextBox box, int fallback) =>
        int.TryParse(box.Text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value) ? value : fallback;

    private void OkButton_Click(object sender, RoutedEventArgs e)
    {
        var settings = AppSettings.Current;
        settings.EnginePathOverride = string.IsNullOrWhiteSpace(EngineOverrideBox.Text) ? null : EngineOverrideBox.Text.Trim();
        settings.FlowDesignDeltaT = ParseDoubleOrDefault(FlowDeltaTBox, settings.FlowDesignDeltaT);
        settings.FlowMinReynolds = ParseDoubleOrDefault(FlowMinReynoldsBox, settings.FlowMinReynolds);
        settings.AutosaveEnabled = AutosaveEnabledCheckBox.IsChecked == true;
        settings.AutosaveIntervalMinutes = ParseIntOrDefault(AutosaveIntervalBox, settings.AutosaveIntervalMinutes);
        settings.Save();
        DialogResult = true;
    }

    private void CancelButton_Click(object sender, RoutedEventArgs e) => DialogResult = false;
}
