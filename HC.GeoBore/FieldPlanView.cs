using System.Globalization;
using System.Windows;
using System.Windows.Media;

namespace HC.GeoBore;

/// <summary>
/// Plan view of a borehole field, drawn to scale: each borehole is a circle at its (x, y), each sits in
/// a dotted B1 x B2 cell, the dashed rectangle is the centre-to-centre span the "footprint" figures in
/// Area Sizing refer to, and the dimension lines give that span in metres. It takes plain positions
/// rather than N1/N2 so a future non-rectangular layout can reuse it via SetBoreholes.
/// </summary>
public sealed class FieldPlanView : FrameworkElement
{
    private static readonly Brush BoreholeFill = Frozen(new SolidColorBrush(Color.FromRgb(0x1F, 0x77, 0xB4)));
    private static readonly Pen BoreholeOutline = Frozen(new Pen(new SolidColorBrush(Color.FromRgb(0x0F, 0x3E, 0x63)), 1));
    private static readonly Pen CellPen = Frozen(new Pen(new SolidColorBrush(Color.FromArgb(70, 0, 0, 0)), 0.75) { DashStyle = DashStyles.Dot });
    private static readonly Pen ExtentPen = Frozen(new Pen(new SolidColorBrush(Color.FromRgb(0xBB, 0xBB, 0xBB)), 1));
    private static readonly Pen SpanPen = Frozen(new Pen(new SolidColorBrush(Color.FromRgb(0xE0, 0x6C, 0x00)), 1.25) { DashStyle = DashStyles.Dash });
    private static readonly Pen DimensionPen = Frozen(new Pen(new SolidColorBrush(Color.FromRgb(0x55, 0x55, 0x55)), 1));
    private static readonly Brush TextBrush = Frozen(new SolidColorBrush(Color.FromRgb(0x33, 0x33, 0x33)));
    private static readonly Brush MutedBrush = Frozen(new SolidColorBrush(Color.FromRgb(0x88, 0x88, 0x88)));
    private static readonly Typeface Face = new("Segoe UI");

    private IReadOnlyList<Point> _points = Array.Empty<Point>();
    private double _spacingX;
    private double _spacingY;
    private double _radiusM;
    private string _caption = string.Empty;

    private static T Frozen<T>(T freezable) where T : Freezable
    {
        freezable.Freeze();
        return freezable;
    }

    public void SetRectangle(int n1, int n2, double spacingX, double spacingY, double boreholeRadiusM)
    {
        var points = new List<Point>(n1 * n2);
        for (var j = 0; j < n2; j++)
            for (var i = 0; i < n1; i++)
                points.Add(new Point(i * spacingX, j * spacingY));
        SetBoreholes(points, spacingX, spacingY, boreholeRadiusM, $"{n1} × {n2} = {n1 * n2} boreholes");
    }

    /// <param name="spacingX">Cell size drawn around each borehole; for an irregular layout, the typical spacing.</param>
    public void SetBoreholes(IReadOnlyList<Point> pointsM, double spacingX, double spacingY, double boreholeRadiusM, string caption)
    {
        _points = pointsM;
        _spacingX = spacingX;
        _spacingY = spacingY;
        _radiusM = boreholeRadiusM;
        _caption = caption;
        InvalidateVisual();
    }

    public void Clear()
    {
        _points = Array.Empty<Point>();
        _caption = string.Empty;
        InvalidateVisual();
    }

    private FormattedText Text(string text, double size, Brush brush, bool bold = false) => new(
        text, CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
        new Typeface(Face.FontFamily, FontStyles.Normal, bold ? FontWeights.Bold : FontWeights.Normal, FontStretches.Normal),
        size, brush, VisualTreeHelper.GetDpi(this).PixelsPerDip);

    protected override void OnRender(DrawingContext dc)
    {
        var width = ActualWidth;
        var height = ActualHeight;
        dc.DrawRectangle(Brushes.White, null, new Rect(0, 0, width, height));

        if (_points.Count == 0)
        {
            var hint = Text("Field layout appears here\nafter the calculation runs.", 11, MutedBrush);
            hint.TextAlignment = TextAlignment.Center;
            hint.MaxTextWidth = Math.Max(10, width - 20);
            dc.DrawText(hint, new Point(10, Math.Max(0, (height - hint.Height) / 2)));
            return;
        }

        const double marginLeft = 40, marginRight = 14, marginTop = 42, marginBottom = 30;
        var spanX = _points.Max(p => p.X) - _points.Min(p => p.X);
        var spanY = _points.Max(p => p.Y) - _points.Min(p => p.Y);
        var originX = _points.Min(p => p.X);
        var originY = _points.Min(p => p.Y);
        var extentX = spanX + _spacingX;
        var extentY = spanY + _spacingY;

        var availableW = width - marginLeft - marginRight;
        var availableH = height - marginTop - marginBottom;
        if (availableW < 30 || availableH < 30) return;

        var scale = Math.Min(availableW / extentX, availableH / extentY);
        var drawW = extentX * scale;
        var drawH = extentY * scale;
        var left = marginLeft + (availableW - drawW) / 2;
        var top = marginTop + (availableH - drawH) / 2;

        // Plan orientation: +y is up the page.
        Point ToScreen(double x, double y) => new(
            left + (x - originX + _spacingX / 2) * scale,
            top + drawH - (y - originY + _spacingY / 2) * scale);

        dc.DrawRectangle(null, ExtentPen, new Rect(left, top, drawW, drawH));

        var cellPx = Math.Min(_spacingX, _spacingY) * scale;
        if (cellPx >= 9)
        {
            var columns = (int)Math.Round(extentX / _spacingX);
            var rows = (int)Math.Round(extentY / _spacingY);
            for (var i = 1; i < columns; i++)
                dc.DrawLine(CellPen, new Point(left + i * _spacingX * scale, top), new Point(left + i * _spacingX * scale, top + drawH));
            for (var j = 1; j < rows; j++)
                dc.DrawLine(CellPen, new Point(left, top + j * _spacingY * scale), new Point(left + drawW, top + j * _spacingY * scale));
        }

        // Centre-to-centre span: the "footprint" rectangle.
        var spanTopLeft = ToScreen(originX, originY + spanY);
        var spanBottomRight = ToScreen(originX + spanX, originY);
        if (spanX > 0 && spanY > 0)
            dc.DrawRectangle(null, SpanPen, new Rect(spanTopLeft, spanBottomRight));

        var radiusPx = cellPx < 5 ? 1.2 : Math.Clamp(_radiusM * scale, 2.5, Math.Min(cellPx / 2 - 1, 7));
        foreach (var p in _points)
            dc.DrawEllipse(BoreholeFill, cellPx < 5 ? null : BoreholeOutline, ToScreen(p.X, p.Y), radiusPx, radiusPx);

        // Caption and span text.
        var caption = Text(_caption, 12, TextBrush, bold: true);
        dc.DrawText(caption, new Point(marginLeft, 6));
        var detail = Text($"spacing {_spacingX:0.#} × {_spacingY:0.#} m   ·   span {spanX:0.#} × {spanY:0.#} m", 10, MutedBrush);
        dc.DrawText(detail, new Point(marginLeft, 23));

        // Dimension lines along the bottom and left of the span rectangle.
        if (spanX > 0)
        {
            var y = top + drawH + 13;
            DrawDimension(dc, new Point(spanTopLeft.X, y), new Point(spanBottomRight.X, y), $"{spanX:0.#} m", vertical: false);
        }
        if (spanY > 0)
        {
            var x = left - 13;
            DrawDimension(dc, new Point(x, spanBottomRight.Y), new Point(x, spanTopLeft.Y), $"{spanY:0.#} m", vertical: true);
        }
    }

    private void DrawDimension(DrawingContext dc, Point a, Point b, string label, bool vertical)
    {
        dc.DrawLine(DimensionPen, a, b);
        const double tick = 4;
        if (vertical)
        {
            dc.DrawLine(DimensionPen, new Point(a.X - tick, a.Y), new Point(a.X + tick, a.Y));
            dc.DrawLine(DimensionPen, new Point(b.X - tick, b.Y), new Point(b.X + tick, b.Y));
        }
        else
        {
            dc.DrawLine(DimensionPen, new Point(a.X, a.Y - tick), new Point(a.X, a.Y + tick));
            dc.DrawLine(DimensionPen, new Point(b.X, b.Y - tick), new Point(b.X, b.Y + tick));
        }

        var text = Text(label, 10, TextBrush);
        var mid = new Point((a.X + b.X) / 2, (a.Y + b.Y) / 2);
        // Small white plate so the label reads over the dimension line.
        if (vertical)
        {
            dc.PushTransform(new RotateTransform(-90, mid.X, mid.Y));
            var origin = new Point(mid.X - text.Width / 2, mid.Y - text.Height / 2);
            dc.DrawRectangle(Brushes.White, null, new Rect(origin, new Size(text.Width, text.Height)));
            dc.DrawText(text, origin);
            dc.Pop();
        }
        else
        {
            var origin = new Point(mid.X - text.Width / 2, mid.Y - text.Height / 2);
            dc.DrawRectangle(Brushes.White, null, new Rect(origin, new Size(text.Width, text.Height)));
            dc.DrawText(text, origin);
        }
    }
}
