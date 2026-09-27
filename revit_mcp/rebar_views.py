# -*- coding: UTF-8 -*-
"""
Views of the Acero window besides the plan sketch: the column elevation
(stirrup zones in color, like a detail drawing) and a 3D view with Revit-
like detail levels. Plus the zoom/pan helper shared by the 2D views.

WPF drawing only (no Revit API): the window hands in plain data - meters,
local section coordinates, z measured up from the column base.

Runs inside Revit's IronPython engine - no f-strings.
"""
import math

from System.Windows import FontWeights, Point, Size
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Media import Color, Colors, DoubleCollection, RotateTransform, SolidColorBrush
from System.Windows.Media.Media3D import (
    AmbientLight,
    DiffuseMaterial,
    DirectionalLight,
    GeometryModel3D,
    MaterialGroup,
    MeshGeometry3D,
    Model3DGroup,
    ModelVisual3D,
    PerspectiveCamera,
    Point3D,
    SpecularMaterial,
    Vector3D,
)
from System.Windows.Shapes import Line, Rectangle

DETAIL_LEVELS = (u"Bajo", u"Medio", u"Alto")


def _brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


def _color(r, g, b, a=255):
    return Color.FromArgb(a, r, g, b)


# Zone colors (Z1, Z2, Z3...) like a stirrup detail drawing.
ZONE_BRUSHES = [
    _brush(232, 90, 160), _brush(70, 190, 120), _brush(185, 90, 220),
    _brush(240, 160, 50), _brush(70, 150, 230),
]
C_EDGE = _brush(214, 120, 60)
C_CONF = _brush(40, 150, 90)
C_BAR = _brush(40, 40, 40)
C_CONCRETE = _brush(225, 228, 232)
C_JOINT = _brush(200, 204, 210)
C_NEIGHBOR = _brush(236, 238, 241)
C_NEIGHBOR_EDGE = _brush(150, 155, 162)
C_NEIGHBOR_TEXT = _brush(90, 95, 105)
C_TEXT = _brush(40, 40, 40)
C_DIM = _brush(40, 110, 220)


# --- zoom / pan for the 2D canvases ------------------------------------------

class Nav2D(object):
    """Zoom and pan of a 2D view. `frame` is (scale, ox, oy) - pixel =
    (ox + x * scale, oy - y * scale) - or None to fit the drawing."""

    def __init__(self):
        self.frame = None
        self._drag = None

    def reset(self):
        self.frame = None
        self._drag = None

    def resolve(self, fitted):
        return self.frame or fitted

    def wheel(self, fitted, point, delta):
        """Zoom by the wheel, keeping the point under the cursor fixed."""
        scale, ox, oy = self.resolve(fitted)
        factor = 1.2 if delta > 0 else 1 / 1.2
        wx, wy = (point.X - ox) / scale, (oy - point.Y) / scale
        new_scale = scale * factor
        self.frame = (new_scale, point.X - wx * new_scale, point.Y + wy * new_scale)

    def start_pan(self, fitted, point):
        self._drag = (point, self.resolve(fitted))

    def pan(self, point):
        """True while a pan is going on (the frame moved)."""
        if self._drag is None:
            return False
        start, (scale, ox, oy) = self._drag
        self.frame = (scale, ox + point.X - start.X, oy + point.Y - start.Y)
        return True

    def end_pan(self):
        self._drag = None


def fit_frame(width, height, x0, y0, x1, y1, margins=(30, 30, 30, 30)):
    """(scale, ox, oy) fitting box (x0..x1, y0..y1) into a width x height
    canvas; margins = left, top, right, bottom in pixels."""
    left, top, right, bottom = margins
    span_x = max(x1 - x0, 1e-6)
    span_y = max(y1 - y0, 1e-6)
    scale = min((width - left - right) / span_x, (height - top - bottom) / span_y)
    cx = left + (width - left - right) / 2.0
    cy = top + (height - top - bottom) / 2.0
    return scale, cx - (x0 + x1) / 2.0 * scale, cy + (y0 + y1) / 2.0 * scale


# --- elevation ----------------------------------------------------------------

def _px(frame, x, y):
    scale, ox, oy = frame
    return ox + x * scale, oy - y * scale


def _line(canvas, frame, a, b, brush, thickness, dash=False):
    x1, y1 = _px(frame, a[0], a[1])
    x2, y2 = _px(frame, b[0], b[1])
    line = Line()
    line.X1, line.Y1, line.X2, line.Y2 = x1, y1, x2, y2
    line.Stroke = brush
    line.StrokeThickness = thickness
    if dash:
        dashes = DoubleCollection()
        dashes.Add(4)
        dashes.Add(2)
        line.StrokeDashArray = dashes
    line.IsHitTestVisible = False
    canvas.Children.Add(line)


def _rect(canvas, frame, x0, y0, x1, y1, fill, stroke=None):
    left, top = _px(frame, x0, y1)
    right, bottom = _px(frame, x1, y0)
    rect = Rectangle()
    rect.Width = max(right - left, 0.5)
    rect.Height = max(bottom - top, 0.5)
    rect.Fill = fill
    if stroke is not None:
        rect.Stroke = stroke
        rect.StrokeThickness = 1
    rect.IsHitTestVisible = False
    Canvas.SetLeft(rect, left)
    Canvas.SetTop(rect, top)
    canvas.Children.Add(rect)


def _text(canvas, frame, x, y, text, brush=C_TEXT, size=11, anchor="center", bold=False):
    tb = TextBlock()
    tb.Text = text
    tb.FontSize = size
    tb.Foreground = brush
    if bold:
        tb.FontWeight = FontWeights.Bold
    tb.IsHitTestVisible = False
    tb.Measure(Size(1e4, 1e4))
    px, py = _px(frame, x, y)
    w, h = tb.DesiredSize.Width, tb.DesiredSize.Height
    left = {"center": px - w / 2.0, "left": px, "right": px - w}[anchor]
    Canvas.SetLeft(tb, left)
    Canvas.SetTop(tb, py - h / 2.0)
    canvas.Children.Add(tb)


def _zone_runs(tagged):
    """[(zone, first_offset, last_offset, count)] of consecutive stirrups
    in the same zone."""
    runs = []
    for offset, zone in tagged:
        if runs and runs[-1][0] == zone:
            z, first, _, count = runs[-1]
            runs[-1] = (z, first, offset, count + 1)
        else:
            runs.append((zone, offset, offset, 1))
    return runs


def _zone_label(family, zone):
    if zone < len(family["zones"]):
        count, spacing = family["zones"][zone]
        return u"{}@{:.2f}".format(count, spacing)
    return u"rto@{:.2f}".format(family["rest"])


def _widen(data):
    """Horizontal exaggeration of the elevation: a 0.30 x 3.45 m column at
    true scale is a thin strip, so its width is drawn up to 4 times wider
    (heights stay true)."""
    return max(1.0, min(4.0, data["height"] / (data["width"] * 4.0)))


def _vtext(canvas, frame, x, y, text, brush, size=10):
    """Text turned 90 degrees (reading upwards) just left of the vertical
    line at x, centred on y - how a vertical dimension is written."""
    tb = TextBlock()
    tb.Text = text
    tb.FontSize = size
    tb.Foreground = brush
    tb.IsHitTestVisible = False
    tb.Measure(Size(1e4, 1e4))
    w, h = tb.DesiredSize.Width, tb.DesiredSize.Height
    px, py = _px(frame, x, y)
    tb.RenderTransformOrigin = Point(0.5, 0.5)
    tb.RenderTransform = RotateTransform(-90)
    cx = px - 3 - h / 2.0  # the turned text is h wide
    Canvas.SetLeft(tb, cx - w / 2.0)
    Canvas.SetTop(tb, py - h / 2.0)
    canvas.Children.Add(tb)
    return w  # its length along the line, in pixels


def _elevation_unit(data):
    """Spacing unit (m) of the elevation's annotations: 1/25 of the drawing
    height, so dimensions and texts keep the same room at any size."""
    zs = [0.0, data["height"]]
    zs += [z for n in data.get("neighbors", []) for z in (n["box"][2], n["box"][5])]
    return max(max(zs) - min(zs), 1.0) / 25.0


def _dimension_layout(data):
    """(edge, x_chain, x_total): the left edge of the drawing (the slab,
    beam or footing reaching furthest, or the zone bands), and the x of
    the dimension chain and of the total height, well clear of it."""
    widen = _widen(data)
    half = data["width"] * widen / 2.0
    u = _elevation_unit(data)
    edge = min([s["x"] * widen - half - 0.17 for s in _segments(data)]
               + [n["box"][0] * widen for n in data.get("neighbors", [])])
    x_chain = edge - 1.6 * u
    return edge, x_chain, x_chain - 2.6 * u


def _segments(data):
    """The columns of the drawing, bottom up: several picked columns of one
    type stacked ("segments": x/y offset and base z from the lowest one, m),
    or the one column. Each: x, y, z, height, clear, edge, conf, joint."""
    return data.get("segments") or [{
        "x": 0.0, "y": 0.0, "z": 0.0, "height": data["height"],
        "clear": data.get("clear", data["height"]), "edge": data.get("edge"),
        "conf": data.get("conf"), "joint": data.get("joint", []),
    }]


def _dimension(canvas, frame, x, z0, z1, text, x_from, u, place=0):
    """A vertical dimension at x from z0 to z1: extension lines from
    x_from, the dimension line with ticks and its value written along it.
    A value that doesn't fit between the ticks goes past the end: place
    -1 below z0, +1 above z1 (0 keeps it in the middle)."""
    t = 0.25 * u
    for z in (z0, z1):
        _line(canvas, frame, (x_from, z), (x - t, z), C_DIM, 0.8)
        _line(canvas, frame, (x - t * 0.7, z - t * 0.7), (x + t * 0.7, z + t * 0.7), C_DIM, 1.2)
    _line(canvas, frame, (x, z0), (x, z1), C_DIM, 1)
    scale = frame[0]
    probe = TextBlock()
    probe.Text = text
    probe.FontSize = 10
    probe.Measure(Size(1e4, 1e4))
    length = probe.DesiredSize.Width / scale  # meters along the line
    if length + 0.3 * u < z1 - z0:
        z = (z0 + z1) / 2.0
    elif place == 0:
        # too short in the middle of the chain: beside it, further out
        _vtext(canvas, frame, x - 0.9 * u, (z0 + z1) / 2.0, text, C_DIM)
        return
    elif place > 0:
        z = z1 + 0.2 * u + length / 2.0
        _line(canvas, frame, (x, z1), (x, z1 + 0.2 * u + length), C_DIM, 0.8)
    else:
        z = z0 - 0.2 * u - length / 2.0
        _line(canvas, frame, (x, z0), (x, z0 - 0.2 * u - length), C_DIM, 0.8)
    _vtext(canvas, frame, x, z, text, C_DIM)


def _dimensions(canvas, frame, data):
    """Dimensions on the left of the elevation, past the slab/footing: a
    chain through footing, clear height and the beam/slab over it (of each
    stacked column), plus the total height further out."""
    height = data["height"]
    neighbors = data.get("neighbors", [])
    marks = [0.0, height]
    for s in _segments(data):
        marks += [s["z"], s["z"] + s["clear"], s["z"] + s["height"]]
    footings = [n["box"][2] for n in neighbors if n["label"] == u"ZAPATA"]
    if footings and min(footings) < -0.01:
        marks.append(min(footings))
    framing = [n["box"][5] for n in neighbors if n["label"] in (u"VIGA", u"LOSA")]
    if framing and max(framing) > height + 0.01:
        marks.append(max(framing))
    levels = []
    for z in sorted(marks):
        if not levels or z - levels[-1] > 0.01:
            levels.append(z)
    u = _elevation_unit(data)
    edge, x_chain, x_total = _dimension_layout(data)
    x_from = edge - 0.3 * u  # extension lines start a little off the drawing
    spans = list(zip(levels, levels[1:]))
    for i, (z0, z1) in enumerate(spans):
        # a short span at the top writes its value above the chain; the
        # others beside it (below would run into the texts at the bottom)
        place = 1 if i == len(spans) - 1 and i > 0 else 0
        _dimension(canvas, frame, x_chain, z0, z1, u"{:.2f}".format(z1 - z0), x_from, u, place)
    _dimension(canvas, frame, x_total, 0.0, height, u"H = {:.2f}".format(height), x_chain, u)


def _floor(data):
    return min([0.0] + [n["box"][2] for n in data.get("neighbors", [])])


def elevation_extent(data):
    u = _elevation_unit(data)
    widen = _widen(data)
    half = data["width"] * widen / 2.0
    neighbors = data.get("neighbors", [])
    zs = [z for n in neighbors for z in (n["box"][2], n["box"][5])]
    right = max([s["x"] * widen + half + 0.30 for s in _segments(data)]
                + [n["box"][3] * widen for n in neighbors])
    top = max([data["height"]] + zs) + 1.2 * u
    bottom = _floor(data) - 2.8 * u  # "Luz libre" and the message
    _, _, x_total = _dimension_layout(data)
    return x_total - 1.2 * u, bottom, right, top


def draw_elevation(canvas, data, frame):
    """Column elevation: concrete, beam/joint at the top, longitudinal bars,
    stirrups at their real heights, and the stirrup zones of the edge
    distribution as colored bands (Z1, Z2...) with their spacing.

    `data`: width, height, clear (m, from the base), bars_x [x...], edge /
    conf families {"zones", "rest", "tagged": [(offset, zone)]} or None,
    joint [offsets above the clear height], message (text or None); or,
    for several stacked columns, "segments" with those per column (see
    `_segments`) and height the total."""
    canvas.Children.Clear()
    if data is None:
        return
    widen = _widen(data)
    half = data["width"] * widen / 2.0
    neighbors = data.get("neighbors", [])
    segments = _segments(data)
    # The elements touching the columns, behind them (x widened like the
    # column), each with its name; the columns are drawn over them.
    for n in neighbors:
        x0, _, z0, x1, _, z1 = n["box"]
        _rect(canvas, frame, x0 * widen, z0, x1 * widen, z1, C_NEIGHBOR, C_NEIGHBOR_EDGE)
    for s in segments:
        cx, base = s["x"] * widen, s["z"]
        clear, top = base + s["clear"], base + s["height"]
        _rect(canvas, frame, cx - half, base, cx + half, clear, C_CONCRETE, C_BAR)
        if top - clear > 1e-3:
            _rect(canvas, frame, cx - half, clear, cx + half, top, C_JOINT, C_BAR)
            if not neighbors:
                _text(canvas, frame, cx, top + 0.06, u"VIGA / NUDO", bold=True)
    # one name per kind (VIGA, LOSA, ZAPATA) and column, on its biggest piece
    biggest = {}
    for n in neighbors:
        x0, _, z0, x1, _, z1 = n["box"]
        area = (x1 - x0) * (z1 - z0)
        key = (n["label"], n.get("seg", 0))
        if key not in biggest or area > biggest[key][0]:
            biggest[key] = (area, n)
    for _, n in biggest.values():
        x0, _, z0, x1, _, z1 = n["box"]
        cx = segments[min(n.get("seg", 0), len(segments) - 1)]["x"] * widen
        # name beside the column, on the side the element reaches furthest
        side = x1 * widen if abs(x1 * widen - cx) >= abs(x0 * widen - cx) else x0 * widen
        x = (side + (cx + half if side > cx else cx - half)) / 2.0
        if abs(side - cx) <= half + 1e-6:
            x = cx
        _text(canvas, frame, x, (z0 + z1) / 2.0, n["label"], brush=C_NEIGHBOR_TEXT, size=10, bold=True)

    for s in segments:
        cx, base = s["x"] * widen, s["z"]
        clear, top = base + s["clear"], base + s["height"]
        for x in data["bars_x"]:
            _line(canvas, frame, (cx + x * widen, base), (cx + x * widen, top), C_BAR, 2)
        edge = s.get("edge")
        if edge:
            band_x0, band_x1 = cx - half - 0.17, cx - half - 0.05
            runs = _zone_runs(edge["tagged"])
            for i, (zone, first, last, count) in enumerate(runs):
                # continuous bands: each one reaches halfway to its neighbours
                lo = 0.0 if i == 0 else (runs[i - 1][2] + first) / 2.0
                hi = s["clear"] if i == len(runs) - 1 else (last + runs[i + 1][1]) / 2.0
                _rect(canvas, frame, band_x0, base + lo, band_x1, base + hi,
                      ZONE_BRUSHES[zone % len(ZONE_BRUSHES)])
                _text(canvas, frame, (band_x0 + band_x1) / 2.0, base + (lo + hi) / 2.0,
                      u"Z{}".format(zone + 1), size=10, bold=True)
                if (lo + hi) / 2.0 <= s["clear"] / 2.0 + 1e-6:  # spacing written once, bottom half
                    _text(canvas, frame, cx + half + 0.03, base + (lo + hi) / 2.0, _zone_label(edge, zone),
                          brush=C_DIM, size=10, anchor="left")
            for offset, zone in edge["tagged"]:
                _line(canvas, frame, (cx - half + 0.01, base + offset), (cx + half - 0.01, base + offset),
                      C_EDGE, 2)
        conf = s.get("conf")
        if conf:
            for offset, _ in conf["tagged"]:
                _line(canvas, frame, (cx - half * 0.55, base + offset), (cx + half * 0.55, base + offset),
                      C_CONF, 1.5, dash=True)
        for offset in s.get("joint", []):
            _line(canvas, frame, (cx - half + 0.01, clear + offset), (cx + half - 0.01, clear + offset),
                  C_EDGE, 2)

    _dimensions(canvas, frame, data)
    # below everything (a footing under the base included), one line each
    floor, u = _floor(data), _elevation_unit(data)
    clears = u" / ".join(u"{:.2f}".format(s["clear"]) for s in segments)
    _text(canvas, frame, 0.0, floor - 0.8 * u,
          u"{} {} m".format(u"Luz libre" if len(segments) == 1 else u"Luces libres (de abajo arriba):", clears),
          brush=C_DIM, size=10)
    if data.get("message"):
        _text(canvas, frame, 0.0, floor - 2.0 * u, data["message"], brush=_brush(192, 57, 43), size=10)


# --- 3D -----------------------------------------------------------------------

def _add_tube(mesh, a, b, radius, sides):
    """Append a cylinder from a to b (Point3D-like tuples) to `mesh`."""
    ax, ay, az = a
    bx, by, bz = b
    dx, dy, dz = bx - ax, by - ay, bz - az
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-9:
        return
    ux, uy, uz = dx / length, dy / length, dz / length
    # any vector not parallel to the axis, then two normals
    px, py, pz = (0.0, 0.0, 1.0) if abs(uz) < 0.9 else (1.0, 0.0, 0.0)
    n1 = (uy * pz - uz * py, uz * px - ux * pz, ux * py - uy * px)
    l1 = math.sqrt(sum(c * c for c in n1))
    n1 = tuple(c / l1 for c in n1)
    n2 = (uy * n1[2] - uz * n1[1], uz * n1[0] - ux * n1[2], ux * n1[1] - uy * n1[0])
    base = mesh.Positions.Count
    for k in range(sides):
        ang = 2 * math.pi * k / sides
        cx = math.cos(ang) * radius
        cy = math.sin(ang) * radius
        off = (n1[0] * cx + n2[0] * cy, n1[1] * cx + n2[1] * cy, n1[2] * cx + n2[2] * cy)
        mesh.Positions.Add(Point3D(ax + off[0], ay + off[1], az + off[2]))
        mesh.Positions.Add(Point3D(bx + off[0], by + off[1], bz + off[2]))
        mesh.Normals.Add(Vector3D(off[0], off[1], off[2]))
        mesh.Normals.Add(Vector3D(off[0], off[1], off[2]))
    for k in range(sides):
        i0 = base + 2 * k
        i1 = base + 2 * ((k + 1) % sides)
        for idx in (i0, i1, i0 + 1, i1, i1 + 1, i0 + 1):
            mesh.TriangleIndices.Add(idx)


def _add_prism(mesh, polygon, height, offset=(0.0, 0.0, 0.0)):
    """Section polygon extruded from 0 to height, moved by offset (fan-
    triangulated caps: fine for the convex sections columns have)."""
    n = len(polygon)
    dx, dy, dz = offset
    polygon = [(x + dx, y + dy) for x, y in polygon]
    cx = sum(p[0] for p in polygon) / n
    cy = sum(p[1] for p in polygon) / n
    base = mesh.Positions.Count
    for z in (dz, dz + height):
        mesh.Positions.Add(Point3D(cx, cy, z))
        for x, y in polygon:
            mesh.Positions.Add(Point3D(x, y, z))
    top = base + n + 1
    for k in range(n):
        a, b = 1 + k, 1 + (k + 1) % n
        for idx in (base, base + b, base + a, top, top + a, top + b):
            mesh.TriangleIndices.Add(idx)
        # side quad
        for idx in (base + a, base + b, top + b, base + a, top + b, top + a):
            mesh.TriangleIndices.Add(idx)


def _material(color, specular=True):
    group = MaterialGroup()
    group.Children.Add(DiffuseMaterial(SolidColorBrush(color)))
    if specular:
        group.Children.Add(SpecularMaterial(SolidColorBrush(Colors.White), 30))
    return group


class Scene3D(object):
    """The column and its rebar in a Viewport3D, with an orbit camera:
    left drag orbits, wheel zooms, middle drag pans, fit() refits."""

    def __init__(self, viewport):
        self.viewport = viewport
        self.camera = PerspectiveCamera()
        self.camera.FieldOfView = 40
        self.camera.UpDirection = Vector3D(0, 0, 1)
        viewport.Camera = self.camera
        self.visual = ModelVisual3D()
        viewport.Children.Add(self.visual)
        self.target = (0.0, 0.0, 1.0)
        self.distance = 5.0
        self.azimuth = 40.0
        self.elevation = 22.0
        self._drag = None
        self._extent = None

    # camera
    def _update_camera(self):
        az, el = math.radians(self.azimuth), math.radians(self.elevation)
        tx, ty, tz = self.target
        dx = math.cos(el) * math.cos(az)
        dy = math.cos(el) * math.sin(az)
        dz = math.sin(el)
        pos = (tx + dx * self.distance, ty + dy * self.distance, tz + dz * self.distance)
        self.camera.Position = Point3D(*pos)
        self.camera.LookDirection = Vector3D(tx - pos[0], ty - pos[1], tz - pos[2])

    def fit(self):
        if self._extent is None:
            return
        width, height = self._extent
        self.target = (0.0, 0.0, height / 2.0)
        # WPF's FieldOfView is horizontal: the vertical one follows the
        # viewport's aspect, and the column must fit both ways.
        w = self.viewport.ActualWidth or 1.0
        h = self.viewport.ActualHeight or 1.0
        tan_h = math.tan(math.radians(self.camera.FieldOfView / 2.0))
        tan_v = tan_h * h / w
        self.distance = max(height * 0.56 / tan_v, width * 0.9 / tan_h) + width
        self.azimuth, self.elevation = 40.0, 22.0
        self._update_camera()

    def wheel(self, delta):
        self.distance *= 0.87 if delta > 0 else 1 / 0.87
        self._update_camera()

    def start_drag(self, point, orbit):
        self._drag = (point, orbit, self.azimuth, self.elevation, self.target)

    def drag(self, point):
        if self._drag is None:
            return False
        start, orbit, az0, el0, target0 = self._drag
        dx, dy = point.X - start.X, point.Y - start.Y
        if orbit:
            self.azimuth = az0 - dx * 0.5
            self.elevation = max(-85.0, min(85.0, el0 + dy * 0.5))
        else:
            az = math.radians(self.azimuth)
            right = (-math.sin(az), math.cos(az))
            k = self.distance / 600.0
            tx, ty, tz = target0
            self.target = (tx - right[0] * dx * k, ty - right[1] * dx * k, tz + dy * k)
        self._update_camera()
        return True

    def end_drag(self):
        self._drag = None

    # model
    def build(self, data, detail):
        """`data`: polygon (section, m), height, bars [(x, y, radius)],
        loops [(kind, [(x, y)...], closed, [z...], radius)] for stirrups and
        ties. detail: Bajo (bars as lines), Medio (colored lines inside the
        concrete), Alto (solid bars at their real diameter)."""
        group = Model3DGroup()
        group.Children.Add(AmbientLight(_color(90, 90, 90)))
        group.Children.Add(DirectionalLight(Colors.White, Vector3D(-0.6, -0.8, -1.0)))
        group.Children.Add(DirectionalLight(_color(120, 120, 120), Vector3D(0.7, 0.5, 0.3)))
        if data is None:
            self.visual.Content = group
            return
        solid = detail == u"Alto"
        # "line" bars: thick enough to read as continuous lines at the
        # distance the whole column is seen from
        thin = max(0.004, data["height"] / 350.0)
        sides = 10 if solid else 5
        colors = {
            "longitudinal": _color(60, 60, 60),
            "borde": _color(214, 120, 60),
            "confinamiento": _color(40, 150, 90),
        }
        if detail == u"Bajo":
            colors = dict((k, _color(50, 50, 50)) for k in colors)
        meshes = dict((k, MeshGeometry3D()) for k in colors)
        segments = _segments(data)  # stacked columns (loops come placed)
        for s in segments:
            for x, y, radius in data["bars"]:
                _add_tube(meshes["longitudinal"], (x + s["x"], y + s["y"], s["z"]),
                          (x + s["x"], y + s["y"], s["z"] + s["height"]), radius if solid else thin, sides)
        for kind, pts, closed, zs, radius in data["loops"]:
            r = radius if solid else thin * 0.8
            count = len(pts) if closed else len(pts) - 1
            for z in zs:
                for k in range(count):
                    a, b = pts[k], pts[(k + 1) % len(pts)]
                    _add_tube(meshes[kind], (a[0], a[1], z), (b[0], b[1], z), r, sides)
        for kind, mesh in meshes.items():
            if mesh.Positions.Count:
                group.Children.Add(GeometryModel3D(mesh, _material(colors[kind], solid)))
        neighbors = data.get("neighbors") or []
        if neighbors:
            # beams, slabs, footings... touching the column, see-through gray
            mesh = MeshGeometry3D()
            for n in neighbors:
                for a, b, c in n["triangles"]:
                    base = mesh.Positions.Count
                    for x, y, z in (a, b, c):
                        mesh.Positions.Add(Point3D(x, y, z))
                    for idx in (base, base + 1, base + 2):
                        mesh.TriangleIndices.Add(idx)
            gray = DiffuseMaterial(SolidColorBrush(_color(150, 155, 165, 110)))
            model = GeometryModel3D(mesh, gray)
            model.BackMaterial = gray
            group.Children.Add(model)
        if detail != u"Bajo":
            concrete = MeshGeometry3D()
            for s in segments:
                _add_prism(concrete, data["polygon"], s["height"], (s["x"], s["y"], s["z"]))
            glass = DiffuseMaterial(SolidColorBrush(_color(170, 180, 195, 70)))
            model = GeometryModel3D(concrete, glass)
            model.BackMaterial = glass
            group.Children.Add(model)
        self.visual.Content = group
        xs = [p[0] for p in data["polygon"]]
        ys = [p[1] for p in data["polygon"]]
        extent = (max(max(xs) - min(xs), max(ys) - min(ys)), data["height"])
        if self._extent is None or self._extent != extent:
            self._extent = extent
            self.fit()
