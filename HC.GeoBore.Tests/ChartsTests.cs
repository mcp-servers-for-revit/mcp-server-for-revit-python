using System.Linq;
using OxyPlot;
using OxyPlot.Series;

namespace HC.GeoBore.Tests;

/// <summary>
/// The chart builders are pure functions of arrays the engine returns, so they can be checked without
/// a window or an engine: the numbers on the axes have to be the numbers the engine produced.
/// </summary>
public class ChartsTests
{
    private static List<double> FlatYear(double watts) => Enumerable.Repeat(watts, 8760).ToList();

    [Fact]
    public void MonthlyKWh_ConservesTotalEnergyAndFollowsCalendarMonthLengths()
    {
        var months = Charts.MonthlyKWh(FlatYear(1000.0));

        Assert.Equal(12, months.Length);
        Assert.Equal(8760.0, months.Sum(), 6);       // 1 kW for a year
        Assert.Equal(31 * 24.0, months[0], 6);       // January
        Assert.Equal(28 * 24.0, months[1], 6);       // February, non-leap like sizing.py's 8760-hour year
    }

    [Fact]
    public void MonthlyKWh_KeepsExtractionPositiveAndInjectionNegative()
    {
        var load = FlatYear(0);
        for (var h = 0; h < 31 * 24; h++) load[h] = 2000.0;                 // January: extraction
        for (var h = 181 * 24; h < 212 * 24; h++) load[h] = -3000.0;        // July: injection

        var months = Charts.MonthlyKWh(load);

        Assert.Equal(2000.0 * 31 * 24 / 1000.0, months[0], 6);
        Assert.Equal(-3000.0 * 31 * 24 / 1000.0, months[6], 6);
    }

    [Fact]
    public void TemperatureHistory_DownsamplingKeepsTheFluidExtremes()
    {
        // 20 years of hourly data with a single sharp cold hour and a single hot hour buried in the middle:
        // bucketed drawing must still reach both, or the chart would hide the design-driving peak.
        var years = 20;
        var fluid = Enumerable.Repeat(10.0, 8760 * years).ToArray();
        var wall = Enumerable.Repeat(11.0, 8760 * years).ToArray();
        fluid[12345] = -4.5;
        fluid[98765] = 31.25;

        var model = Charts.TemperatureHistory(fluid, wall, minLimitC: -2.0, maxLimitC: 35.0, title: "t");

        var envelope = model.Series.OfType<AreaSeries>().Single();
        Assert.Equal(31.25, envelope.Points.Max(p => p.Y), 6);
        Assert.Equal(-4.5, envelope.Points2.Min(p => p.Y), 6);
        Assert.True(envelope.Points.Count < 2000, "the 175k-hour series should be bucketed down");
        Assert.Equal(years, envelope.Points.Max(p => p.X), 0);
    }

    [Fact]
    public void TemperatureHistory_ScalesTheAxisToIncludeTheLimitLines()
    {
        var fluid = Enumerable.Repeat(10.0, 8760).ToArray();
        var model = Charts.TemperatureHistory(fluid, fluid, minLimitC: -2.0, maxLimitC: 35.0, title: "t");

        var yAxis = model.Axes.Single(a => a.Position == OxyPlot.Axes.AxisPosition.Left);
        Assert.True(yAxis.Minimum < -2.0 && yAxis.Maximum > 35.0);
        Assert.Equal(2, model.Annotations.Count);
    }

    [Fact]
    public void TemperatureHistory_WithoutLimitsDrawsNoLimitLines()
    {
        var fluid = Enumerable.Repeat(10.0, 8760).ToArray();
        var model = Charts.TemperatureHistory(fluid, fluid, null, null, "t");
        Assert.Empty(model.Annotations);
    }

    [Fact]
    public void MonthlyLoad_ColoursColumnsBySignWhenThereIsOnlyOneSeries()
    {
        var load = FlatYear(0);
        for (var h = 0; h < 31 * 24; h++) load[h] = 1000.0;
        for (var h = 181 * 24; h < 212 * 24; h++) load[h] = -1000.0;

        var series = Charts.MonthlyLoad(load).Series.OfType<RectangleBarSeries>().Single();

        Assert.Equal(12, series.Items.Count);
        Assert.True(series.Items[0].Y1 > 0);
        Assert.True(series.Items[6].Y1 < 0);
        Assert.NotEqual(series.Items[0].Color, series.Items[6].Color);
    }

    [Fact]
    public void MonthlyLoad_ShowsTheTowerReducingSummerInjectionSideBySide()
    {
        var withoutTower = FlatYear(0);
        var withTower = FlatYear(0);
        for (var h = 181 * 24; h < 212 * 24; h++) { withoutTower[h] = -5000.0; withTower[h] = -2000.0; }

        var model = Charts.MonthlyLoad(withTower, withoutTower);
        var series = model.Series.OfType<RectangleBarSeries>().ToList();

        Assert.Equal(2, series.Count);
        Assert.Equal("Without tower", series[0].Title);
        Assert.True(series[0].Items[6].Y1 < series[1].Items[6].Y1, "the tower-adjusted July injection is smaller in magnitude");
        // Side by side, not overlapping.
        Assert.True(series[0].Items[6].X1 <= series[1].Items[6].X0);
    }

    [Fact]
    public void DepthComparison_OneBarPerMethodInOrder()
    {
        var model = Charts.DepthComparison(new List<(string, double)> { ("Engine", 54.1), ("GHEtool L3", 57.9) });

        var bars = model.Series.OfType<BarSeries>().Single();
        Assert.Equal(new[] { 54.1, 57.9 }, bars.Items.Select(i => i.Value).ToArray());
    }

    [Fact]
    public void GFunction_TopAxisShowsYearsConsistentWithTheLnTimeAxis()
    {
        // ts = 10 years, so t = ts * exp(ln(t/ts)): ln(t/ts) = 0 must read as 10 years on the top axis.
        var rows = new List<GfunctionRow>
        {
            new() { TimeYears = 10.0 * Math.Exp(-3), LnTTs = -3, G = 1.0 },
            new() { TimeYears = 10.0, LnTTs = 0, G = 5.0 },
        };

        var model = Charts.GFunction(rows, designLifeYears: 10, boundaryCondition: "UBWT");

        var top = model.Axes.Single(a => a.Position == OxyPlot.Axes.AxisPosition.Top);
        Assert.Equal("10", top.LabelFormatter(0.0));
        Assert.Equal(2, model.Series.OfType<LineSeries>().Single().Points.Count);
    }

    [Fact]
    public void EmptyModelsCarryTheWaitingHintAndNoAxes()
    {
        var model = Charts.Empty("Temperature");
        Assert.Equal("Temperature", model.Title);
        Assert.Empty(model.Axes);
        Assert.Empty(model.Series);
    }
}
