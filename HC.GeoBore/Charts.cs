using OxyPlot;
using OxyPlot.Annotations;
using OxyPlot.Axes;
using OxyPlot.Legends;
using OxyPlot.Series;

namespace HC.GeoBore;

/// <summary>
/// Builds the OxyPlot models shown in each tab's results. Everything here is a pure function of
/// arrays the engine already returned (size_field / minimum_tower_capacity hand back the full hourly
/// T_f_C / T_b_C series of the design they settled on), so no chart triggers an extra engine call.
/// Degree signs and dashes are \u escapes so the file survives any editor's default encoding.
/// </summary>
public static class Charts
{
    private static readonly OxyColor Blue = OxyColor.FromRgb(0x1F, 0x77, 0xB4);
    private static readonly OxyColor Orange = OxyColor.FromRgb(0xE0, 0x6C, 0x00);
    private static readonly OxyColor Teal = OxyColor.FromRgb(0x2A, 0x9D, 0x8F);
    private static readonly OxyColor Grey = OxyColor.FromRgb(0x9A, 0x9A, 0x9A);
    private static readonly OxyColor Red = OxyColor.FromRgb(0xC6, 0x28, 0x28);
    private static readonly OxyColor GridColor = OxyColor.FromArgb(45, 0, 0, 0);

    private static readonly string[] MonthNames =
        { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
    // Same non-leap month lengths geothermal/sizing.py uses to lay out its 8760-hour year.
    private static readonly int[] HoursInMonth =
        { 31 * 24, 28 * 24, 31 * 24, 30 * 24, 31 * 24, 30 * 24, 31 * 24, 31 * 24, 30 * 24, 31 * 24, 30 * 24, 31 * 24 };

    private const double HoursPerYear = 8760.0;

    private static PlotModel NewModel(string title, string? subtitle = null) => new()
    {
        Title = title,
        Subtitle = subtitle,
        TitleFontSize = 13,
        TitleFontWeight = FontWeights.Bold,
        SubtitleFontSize = 10,
        SubtitleColor = OxyColor.FromRgb(0x66, 0x66, 0x66),
        PlotAreaBorderColor = OxyColor.FromRgb(0xCC, 0xCC, 0xCC),
    };

    private static LinearAxis ValueAxis(AxisPosition position, string title, double? min = null, double? max = null)
    {
        var axis = new LinearAxis
        {
            Position = position,
            Title = title,
            MajorGridlineStyle = LineStyle.Dot,
            MajorGridlineColor = GridColor,
            AxisTitleDistance = 6,
        };
        if (min.HasValue) axis.Minimum = min.Value;
        if (max.HasValue) axis.Maximum = max.Value;
        return axis;
    }

    private static void AddBottomLegend(PlotModel model) => model.Legends.Add(new Legend
    {
        LegendPosition = LegendPosition.BottomCenter,
        LegendPlacement = LegendPlacement.Outside,
        LegendOrientation = LegendOrientation.Horizontal,
        LegendFontSize = 10,
    });

    /// <summary>Blank chart with just a title and a hint, shown before a calculation has run.</summary>
    public static PlotModel Empty(string title) => new()
    {
        Title = title,
        TitleFontSize = 13,
        TitleFontWeight = FontWeights.Bold,
        Subtitle = "Run the calculation to see this chart.",
        SubtitleFontSize = 10,
        SubtitleColor = OxyColor.FromRgb(0x88, 0x88, 0x88),
        PlotAreaBorderThickness = new OxyThickness(0),
    };

    /// <summary>g-function against ln(t/ts), with a secondary time-in-years axis and a design-life marker.</summary>
    public static PlotModel GFunction(IReadOnlyList<GfunctionRow> rows, double designLifeYears, string boundaryCondition)
    {
        var model = NewModel("g-function", $"{boundaryCondition} boundary condition");
        if (rows.Count == 0) return model;

        var xMin = rows.Min(r => r.LnTTs);
        var xMax = rows.Max(r => r.LnTTs);
        var xPad = Math.Max(0.2, (xMax - xMin) * 0.03);
        var gMin = rows.Min(r => r.G);
        var gMax = rows.Max(r => r.G);
        var gPad = Math.Max(0.05, (gMax - gMin) * 0.06);
        // ts in years, recovered from any row (t = ts * exp(ln(t/ts))).
        var tsYears = rows[0].TimeYears / Math.Exp(rows[0].LnTTs);

        var bottom = ValueAxis(AxisPosition.Bottom, "ln(t / ts)", xMin - xPad, xMax + xPad);
        var top = new LinearAxis
        {
            Position = AxisPosition.Top,
            Title = "Time (years)",
            Minimum = xMin - xPad,
            Maximum = xMax + xPad,
            TickStyle = TickStyle.Outside,
            AxisTitleDistance = 6,
            LabelFormatter = v => (tsYears * Math.Exp(v)).ToString("G2", System.Globalization.CultureInfo.InvariantCulture),
        };
        // Keep the years axis locked to the ln(t/ts) axis when the user pans or zooms.
#pragma warning disable CS0618 // Axis.AxisChanged is flagged for removal in OxyPlot 4.0; no replacement exists yet
        bottom.AxisChanged += (_, _) => top.Zoom(bottom.ActualMinimum, bottom.ActualMaximum);
#pragma warning restore CS0618
        model.Axes.Add(bottom);
        model.Axes.Add(top);
        model.Axes.Add(ValueAxis(AxisPosition.Left, "g-function", gMin - gPad, gMax + gPad));

        var line = new LineSeries
        {
            Color = Blue,
            StrokeThickness = 2,
            MarkerType = MarkerType.Circle,
            MarkerSize = 3,
            MarkerFill = Blue,
            TrackerFormatString = "ln(t/ts) = {2:0.00}\ng = {4:0.000}",
        };
        foreach (var r in rows) line.Points.Add(new DataPoint(r.LnTTs, r.G));
        model.Series.Add(line);

        // Design-life marker: a dashed guide plus a label tucked left of and below the end of the curve,
        // where it never collides with the plot border or the top axis.
        var last = rows[^1];
        model.Annotations.Add(new LineAnnotation
        {
            Type = LineAnnotationType.Vertical,
            X = last.LnTTs,
            Color = Grey,
            LineStyle = LineStyle.Dash,
        });
        model.Annotations.Add(new TextAnnotation
        {
            Text = $"Design life {designLifeYears:0} y\ng = {last.G:0.000}",
            TextPosition = new DataPoint(last.LnTTs - xPad * 0.5, gMin + (gMax - gMin) * 0.42),
            TextHorizontalAlignment = HorizontalAlignment.Right,
            TextVerticalAlignment = VerticalAlignment.Top,
            FontSize = 10,
            TextColor = OxyColor.FromRgb(0x44, 0x44, 0x44),
            Stroke = OxyColors.Transparent,
            Background = OxyColors.Transparent,
        });
        return model;
    }

    /// <summary>
    /// Fluid and borehole-wall temperature over the whole simulated period. The hourly series (up to
    /// ~175k points) is bucketed into ~1500 min/max pairs so peaks survive the downsampling, drawn as a
    /// shaded envelope; optional fluid-temperature limits are drawn as dashed lines.
    /// </summary>
    public static PlotModel TemperatureHistory(
        IReadOnlyList<double> fluidC, IReadOnlyList<double> wallC, double? minLimitC, double? maxLimitC, string title)
    {
        var n = fluidC.Count;
        var fMinAll = fluidC.Min();
        var fMaxAll = fluidC.Max();
        var model = NewModel(title, $"Fluid {fMinAll:0.0} to {fMaxAll:0.0} °C over {n / HoursPerYear:0.#} years");

        var bucket = Math.Max(1, n / 1500);
        var upper = new List<DataPoint>();
        var lower = new List<DataPoint>();
        var wall = new List<DataPoint>();
        for (var start = 0; start < n; start += bucket)
        {
            var end = Math.Min(n, start + bucket);
            double lo = double.MaxValue, hi = double.MinValue, wallSum = 0;
            for (var i = start; i < end; i++)
            {
                if (fluidC[i] < lo) lo = fluidC[i];
                if (fluidC[i] > hi) hi = fluidC[i];
                wallSum += wallC[i];
            }
            var x = (start + end) / 2.0 / HoursPerYear;
            lower.Add(new DataPoint(x, lo));
            upper.Add(new DataPoint(x, hi));
            wall.Add(new DataPoint(x, wallSum / (end - start)));
        }

        var yLo = Math.Min(fMinAll, Math.Min(wallC.Min(), minLimitC ?? double.MaxValue));
        var yHi = Math.Max(fMaxAll, Math.Max(wallC.Max(), maxLimitC ?? double.MinValue));
        var pad = Math.Max(1.0, (yHi - yLo) * 0.08);

        model.Axes.Add(ValueAxis(AxisPosition.Bottom, "Time (years)", 0, n / HoursPerYear));
        model.Axes.Add(ValueAxis(AxisPosition.Left, "Temperature (°C)", yLo - pad, yHi + pad));

        var envelope = new AreaSeries
        {
            Title = "Fluid (min–max envelope)",
            Color = Blue,
            Fill = OxyColor.FromAColor(70, Blue),
            StrokeThickness = 1,
            TrackerFormatString = "Year {2:0.0}\n{4:0.0} °C",
        };
        envelope.Points.AddRange(upper);
        envelope.Points2.AddRange(lower);
        model.Series.Add(envelope);

        var wallLine = new LineSeries
        {
            Title = "Borehole wall (mean)",
            Color = Orange,
            StrokeThickness = 1.5,
            TrackerFormatString = "Year {2:0.0}\nwall {4:0.0} °C",
        };
        wallLine.Points.AddRange(wall);
        model.Series.Add(wallLine);

        if (maxLimitC.HasValue) model.Annotations.Add(LimitLine(maxLimitC.Value, "Max limit", above: true));
        if (minLimitC.HasValue) model.Annotations.Add(LimitLine(minLimitC.Value, "Min limit", above: false));
        AddBottomLegend(model);
        return model;
    }

    // The label goes on the outside of the limit (above the max line, below the min line), right-aligned
    // so it stays inside the plot area instead of being clipped by its right-hand border.
    private static LineAnnotation LimitLine(double valueC, string label, bool above) => new()
    {
        Type = LineAnnotationType.Horizontal,
        Y = valueC,
        Color = Red,
        LineStyle = LineStyle.Dash,
        StrokeThickness = 1.5,
        Text = $"{label} {valueC:0.0} °C",
        TextColor = Red,
        FontSize = 10,
        TextHorizontalAlignment = HorizontalAlignment.Right,
        TextVerticalAlignment = above ? VerticalAlignment.Bottom : VerticalAlignment.Top,
    };

    /// <summary>Sums an hourly W series (one 8760-hour year, + = extracted from ground) into kWh per calendar month.</summary>
    public static double[] MonthlyKWh(IReadOnlyList<double> hourlyW)
    {
        var months = new double[12];
        var hour = 0;
        for (var m = 0; m < 12; m++)
            for (var h = 0; h < HoursInMonth[m] && hour < hourlyW.Count; h++, hour++)
                months[m] += hourlyW[hour] / 1000.0;
        return months;
    }

    /// <summary>
    /// Net monthly energy the ground sees. With only groundLoadW the columns are coloured by sign
    /// (extraction warm, injection cool); with a reference series (the same load before a cooling tower)
    /// the two are shown side by side so the tower's effect on summer injection is visible.
    /// </summary>
    public static PlotModel MonthlyLoad(IReadOnlyList<double> groundLoadW, IReadOnlyList<double>? withoutTowerW = null)
    {
        var dual = withoutTowerW is not null;
        var model = NewModel(
            "Monthly net ground load",
            "+ extracted / − injected (kWh)");

        // OxyPlot 2.2's BarSeries is horizontal-only, so vertical columns are LinearBarSeries on a plain
        // numeric axis (month 1..12) whose tick labels are rewritten to month names.
        var months = new LinearAxis
        {
            Position = AxisPosition.Bottom,
            Minimum = 0.4,
            Maximum = 12.6,
            MajorStep = 1,
            MinorStep = 1,
            MajorGridlineStyle = LineStyle.None,
            // Initials keep 12 labels legible when the card is narrow; the tracker gives the month number.
            LabelFormatter = v => Math.Abs(v - Math.Round(v)) < 1e-6 && v >= 1 && v <= 12 ? MonthNames[(int)Math.Round(v) - 1][..1] : string.Empty,
        };
        model.Axes.Add(months);
        var value = ValueAxis(AxisPosition.Left, "Energy (kWh)");
        value.ExtraGridlines = new[] { 0.0 };
        value.ExtraGridlineColor = OxyColors.Black;
        value.ExtraGridlineThickness = 1;
        model.Axes.Add(value);

        // Column geometry is in month units (data space), so bar widths track the chart size.
        RectangleBarSeries Columns(string? title, OxyColor positive, OxyColor negative, double left, double right, IReadOnlyList<double> hourlyW)
        {
            var series = new RectangleBarSeries { Title = title, FillColor = positive, StrokeColor = OxyColors.Transparent };
            var kwh = MonthlyKWh(hourlyW);
            for (var m = 0; m < 12; m++)
                series.Items.Add(new RectangleBarItem(m + 1 + left, 0, m + 1 + right, kwh[m]) { Color = kwh[m] >= 0 ? positive : negative });
            return series;
        }

        if (dual)
        {
            model.Series.Add(Columns("Without tower", Grey, Grey, -0.38, -0.02, withoutTowerW!));
            model.Series.Add(Columns("Ground load with tower", Teal, Teal, 0.02, 0.38, groundLoadW));
            AddBottomLegend(model);
        }
        else
        {
            model.Series.Add(Columns(null, Orange, Blue, -0.32, 0.32, groundLoadW));
        }
        return model;
    }

    /// <summary>Horizontal bars comparing the depth per borehole reached by each method.</summary>
    public static PlotModel DepthComparison(IReadOnlyList<(string Label, double DepthM)> bars)
    {
        var model = NewModel("Required depth per borehole");
        var category = new CategoryAxis { Position = AxisPosition.Left, StartPosition = 1, EndPosition = 0, GapWidth = 0.5 };
        foreach (var bar in bars) category.Labels.Add(bar.Label);
        model.Axes.Add(category);
        model.Axes.Add(ValueAxis(AxisPosition.Bottom, "Depth H (m)", 0, bars.Max(b => b.DepthM) * 1.25));

        var palette = new[] { Blue, Teal, Orange };
        var series = new BarSeries { LabelPlacement = LabelPlacement.Outside, LabelFormatString = "{0:0.0} m", StrokeColor = OxyColors.Transparent };
        for (var i = 0; i < bars.Count; i++)
            series.Items.Add(new BarItem(bars[i].DepthM, i) { Color = palette[i % palette.Length] });
        model.Series.Add(series);
        return model;
    }
}
