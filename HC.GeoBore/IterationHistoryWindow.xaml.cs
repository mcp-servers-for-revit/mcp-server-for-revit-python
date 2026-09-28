using System.Collections.ObjectModel;
using System.Linq;
using System.Windows;

namespace HC.GeoBore;

public partial class IterationHistoryWindow : Window
{
    public List<ProjectIteration> Iterations { get; }
    public ProjectIteration? SelectedIteration { get; private set; }
    public bool DeletedAny { get; private set; }

    private sealed class IterationRow
    {
        public required ProjectIteration Source { get; init; }
        public string Name => Source.Name;
        public string SavedAtLocal => Source.SavedAtUtc.ToLocalTime().ToString("yyyy-MM-dd HH:mm");
        public string Notes => Source.Notes ?? "";
    }

    private readonly ObservableCollection<IterationRow> _rows = new();

    public IterationHistoryWindow(List<ProjectIteration> iterations)
    {
        InitializeComponent();
        Iterations = new List<ProjectIteration>(iterations);
        IterationsList.ItemsSource = _rows;
        RefreshRows();
    }

    private void RefreshRows()
    {
        _rows.Clear();
        foreach (var iteration in Iterations.OrderByDescending(i => i.SavedAtUtc))
            _rows.Add(new IterationRow { Source = iteration });
    }

    private void LoadButton_Click(object sender, RoutedEventArgs e)
    {
        if (IterationsList.SelectedItem is not IterationRow row)
        {
            MessageBox.Show("Select an iteration to load.", "GeoBore");
            return;
        }
        SelectedIteration = row.Source;
        DialogResult = true;
    }

    private void DeleteButton_Click(object sender, RoutedEventArgs e)
    {
        if (IterationsList.SelectedItem is not IterationRow row)
        {
            MessageBox.Show("Select an iteration to delete.", "GeoBore");
            return;
        }
        if (MessageBox.Show($"Delete iteration \"{row.Name}\"? This can't be undone.", "GeoBore",
                MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes)
            return;

        Iterations.Remove(row.Source);
        DeletedAny = true;
        RefreshRows();
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }
}
