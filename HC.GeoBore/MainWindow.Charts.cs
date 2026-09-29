using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace HC.GeoBore;

// Chart wiring for the four results tabs. The charts themselves are built in Charts.cs / FieldPlanView.cs;
// this file only decides when they are reset and hands them the arrays the engine returned.
public partial class MainWindow
{
    private void InitializeCharts()
    {
        ResetGfunctionCharts();
        ResetSimulationCharts();
        ResetSizingCharts();
        ResetAreaCharts();
        ResetTrtChart();
    }

    private void ResetGfunctionCharts()
    {
        G_Chart.Model = Charts.Empty("g-function");
        G_PlanView.Clear();
    }

    private void ResetSimulationCharts()
    {
        Sim_TempChart.Model = Charts.Empty("Fluid temperature history");
        Sim_LoadChart.Model = Charts.Empty("Monthly net ground load");
        Sim_PlanView.Clear();
    }

    private void ResetSizingCharts()
    {
        Size_TempChart.Model = Charts.Empty("Fluid temperature at the sized depth");
        Size_LoadChart.Model = Charts.Empty("Monthly net ground load");
        Size_DepthChart.Model = null;
        Size_DepthHost.Visibility = System.Windows.Visibility.Collapsed;
        Size_PlanView.Clear();
        Size_McHistChart.Model = Charts.Empty("Distribution of sized depth H");
        Size_McResultPanel.Visibility = System.Windows.Visibility.Collapsed;
        Size_DeterministicHost.Visibility = System.Windows.Visibility.Visible;
        Size_LoadPlanHost.Visibility = System.Windows.Visibility.Visible;
        Size_TowerCrossCheckHost.Visibility = System.Windows.Visibility.Visible;
    }

    private void ResetAreaCharts()
    {
        Area_TempChart.Model = Charts.Empty("Fluid temperature at the result depth");
        Area_LoadChart.Model = Charts.Empty("Monthly net ground load");
        Area_PlanView.Clear();
        Area_FootprintBar.Value = 0;
        Area_FootprintText.Text = string.Empty;
    }

    private void ResetTrtChart()
    {
        Trt_Chart.Model = Charts.Empty("TRT result");
    }

    private static double[] ReadDoubleArray(JsonElement parent, string property) =>
        parent.GetProperty(property).EnumerateArray().Select(x => x.GetDouble()).ToArray();

    // A DataGrid inside the results ScrollViewer would otherwise swallow every mouse-wheel tick and
    // strand the page; scroll the grid while it has room to move, then hand the wheel to the page.
    private void DataGrid_PreviewMouseWheel(object sender, MouseWheelEventArgs e)
    {
        if (sender is not DataGrid grid || e.Handled) return;

        var inner = FindChild<ScrollViewer>(grid);
        if (inner is not null && inner.ScrollableHeight > 0)
        {
            var atTop = inner.VerticalOffset <= 0 && e.Delta > 0;
            var atBottom = inner.VerticalOffset >= inner.ScrollableHeight && e.Delta < 0;
            if (!atTop && !atBottom) return;
        }

        if (grid.Parent is UIElement parent)
        {
            e.Handled = true;
            parent.RaiseEvent(new MouseWheelEventArgs(e.MouseDevice, e.Timestamp, e.Delta)
            {
                RoutedEvent = UIElement.MouseWheelEvent,
                Source = grid,
            });
        }
    }

    private static T? FindChild<T>(DependencyObject root) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T match) return match;
            var nested = FindChild<T>(child);
            if (nested is not null) return nested;
        }
        return null;
    }
}
