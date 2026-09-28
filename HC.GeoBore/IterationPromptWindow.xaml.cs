using System.Windows;

namespace HC.GeoBore;

public partial class IterationPromptWindow : Window
{
    public string IterationName { get; private set; } = "";
    public string? Notes { get; private set; }

    public IterationPromptWindow()
    {
        InitializeComponent();
        NameBox.Text = $"Iteration {DateTime.Now:yyyy-MM-dd HH:mm}";
        NameBox.Focus();
        NameBox.SelectAll();
    }

    private void SaveButton_Click(object sender, RoutedEventArgs e)
    {
        var name = NameBox.Text.Trim();
        if (name.Length == 0)
        {
            ErrorText.Text = "Enter a name for this iteration.";
            return;
        }

        IterationName = name;
        Notes = string.IsNullOrWhiteSpace(NotesBox.Text) ? null : NotesBox.Text.Trim();
        DialogResult = true;
    }

    private void CancelButton_Click(object sender, RoutedEventArgs e) => DialogResult = false;
}
