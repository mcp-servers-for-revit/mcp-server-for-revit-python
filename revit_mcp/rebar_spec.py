# -*- coding: UTF-8 -*-
"""
Pure-Python helpers for the rebar ("Acero") generator: reading the
per-type reinforcement table, stirrup spacing, longitudinal bar layout and
bar weights. No Revit imports, so it can be unit-tested outside Revit.

Lengths are in meters unless a name says otherwise.
"""
import json
import math
import re

# Nominal diameters (mm) of the bars used in Peru (ASTM A615 / NTP 341.031).
BAR_DIAMETERS_MM = {
    u'1/4"': 6.35,
    u"6mm": 6.0,
    u"8mm": 8.0,
    u'3/8"': 9.525,
    u"12mm": 12.0,
    u'1/2"': 12.7,
    u'5/8"': 15.875,
    u'3/4"': 19.05,
    u'1"': 25.4,
    u'1 3/8"': 34.925,
}

STEEL_DENSITY_KG_M3 = 7850.0


class SpecError(ValueError):
    """A reinforcement table entry that can't be read (message in Spanish,
    shown to the user)."""


def bar_weight_kg_per_m(diameter_key):
    d = BAR_DIAMETERS_MM[diameter_key] / 1000.0
    return STEEL_DENSITY_KG_M3 * math.pi * d * d / 4.0


def _clean(text):
    t = (text or u"").strip().lower()
    # A space, not nothing: "8Ø5/8" must stay "8 5/8", not "85/8".
    for ch in (u"ø", u"Ø", u"φ", u"∅", u"#"):
        t = t.replace(ch, u" ")
    for q in (u"”", u"″", u"''", u"“"):
        t = t.replace(q, u'"')
    return re.sub(r"\s+", u" ", t).strip()


def parse_diameter(text):
    """'5/8"', 'Ø5/8', '5/8 pulg', '1 3/8"', '12mm', '8 mm' -> a
    BAR_DIAMETERS_MM key."""
    t = _clean(text)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*mm$", t)
    if m:
        key = u"{}mm".format(m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else m.group(1))
        if key in BAR_DIAMETERS_MM:
            return key
        raise SpecError(u"Diametro no reconocido: {}".format(text))
    t = re.sub(r'\s*(pulg|in|")\s*$', u"", t).replace(u"-", u" ").strip()
    key = t + u'"'
    if key in BAR_DIAMETERS_MM:
        return key
    raise SpecError(
        u'Diametro no reconocido: "{}" (usa 3/8", 1/2", 5/8", 3/4", 1", 8mm, 12mm...)'.format(text)
    )


def parse_longitudinal(text):
    """'8Ø5/8"', '4Ø3/4" + 4Ø5/8"', '8 5/8' -> [(count, diameter_key)],
    largest diameter first (those go to the corners)."""
    t = (text or u"").strip()
    if not t:
        raise SpecError(u"Falta el acero longitudinal")
    groups = []
    for part in t.split(u"+"):
        p = _clean(part)
        m = re.match(r"^(\d+)\s*[x×-]?\s*(.+)$", p)
        if not m:
            raise SpecError(u'No se entiende "{}": usa por ejemplo 8Ø5/8"'.format(part.strip()))
        count = int(m.group(1))
        if count <= 0:
            raise SpecError(u'Cantidad invalida en "{}"'.format(part.strip()))
        groups.append((count, parse_diameter(m.group(2))))
    total = sum(c for c, _ in groups)
    if total < 4:
        raise SpecError(u"Una columna rectangular necesita al menos 4 barras (una por esquina)")
    if (total - 4) % 2:
        raise SpecError(
            u"{} barras: el total debe ser par para repartirlas simetricamente".format(total)
        )
    groups.sort(key=lambda g: -BAR_DIAMETERS_MM[g[1]])
    return groups


_REST_WORDS = (u"r", u"rto", u"resto", u"rest", u"rt")


def parse_distribution(text):
    """'1@.05, 10@.10, rto@.20' (meters, or cm when >= 1: '1@5, 10@10,
    R@20') -> ([(count, spacing_m), ...], rest_spacing_m). Read from each
    end of the element towards its middle."""
    t = _clean(text)
    if not t:
        raise SpecError(u"Falta la distribucion de estribos")
    zones = []
    rest = None
    # Tokens like "10@.10" / "rto@20cm", separated by commas, semicolons
    # or just spaces ("1@5 6@10 Rto@25").
    token_re = re.compile(r"([a-z]+|\d+)\s*@\s*(\d*\.?\d+)\s*(cm|m)?(?![a-z0-9])")
    leftover = token_re.sub(u"", t)
    if re.sub(r"[\s,;]", u"", leftover):
        raise SpecError(
            u'No se entiende "{}": usa por ejemplo 1@.05, 10@.10, rto@.20'.format(text.strip())
        )
    for m in token_re.finditer(t):
        token = m.group(0).strip()
        value = float(m.group(2))
        unit = m.group(3)
        spacing = value / 100.0 if unit == u"cm" or (unit is None and value >= 1.0) else value
        if spacing <= 0:
            raise SpecError(u'Espaciamiento invalido en "{}"'.format(token))
        if m.group(1).isdigit():
            if rest is not None:
                raise SpecError(u"El resto (rto@...) debe ir al final")
            zones.append((int(m.group(1)), spacing))
        elif m.group(1) in _REST_WORDS:
            rest = spacing
        else:
            raise SpecError(u'No se entiende "{}"'.format(token))
    if rest is None:
        raise SpecError(u"Falta el resto, por ejemplo: rto@.20")
    return zones, rest


def stirrup_positions(length, zones, rest):
    """Stirrup offsets (m) along a clear length, laid out from both ends
    towards the middle (Peruvian '1@.05, 10@.10, rto@.20' convention)."""
    if length <= 0:
        return []
    half = length / 2.0
    eps = 1e-6
    bottom = []
    z = 0.0
    for count, spacing in zones:
        for _ in range(count):
            if z + spacing > half + eps:
                break
            z += spacing
            bottom.append(z)
    while z + rest <= half + eps:
        z += rest
        bottom.append(z)
    if not bottom:
        return [half]
    top = [length - p for p in reversed(bottom)]
    last, first_top = bottom[-1], top[0]
    gap = first_top - last
    if gap < rest / 2.0:
        # The two halves meet: one stirrup at the middle instead of two.
        bottom = bottom[:-1]
        top = top[1:]
        middle = [half]
    elif gap > rest + eps:
        middle = [half]
    else:
        middle = []
    return bottom + middle + top


def group_runs(positions, tol=1e-4):
    """Split sorted positions into runs of constant spacing:
    [(start, count, spacing)] - each run becomes one rebar set."""
    runs = []
    i = 0
    n = len(positions)
    while i < n:
        if i + 1 >= n:
            runs.append((positions[i], 1, 0.0))
            break
        spacing = positions[i + 1] - positions[i]
        j = i + 1
        while j + 1 < n and abs((positions[j + 1] - positions[j]) - spacing) <= tol:
            j += 1
        count = j - i + 1
        runs.append((positions[i], count, spacing))
        i = j + 1
    return runs


def layout_rectangular_bars(b, h, cover, stirrup_diameter, groups):
    """Longitudinal bar centers of a b x h section (x along b, y along h,
    origin at the center): the 4 largest bars at the corners, the rest in
    symmetric pairs on the faces with the widest spacing.
    `groups` as from `parse_longitudinal`. Returns [(x, y, diameter_key)]."""
    diameters = []
    for count, key in groups:
        diameters.extend([key] * count)
    corners, extra = diameters[:4], diameters[4:]

    def inset(key):
        return cover + stirrup_diameter + BAR_DIAMETERS_MM[key] / 2000.0

    ci = inset(corners[0])
    span_x = b - 2 * ci  # between corner bar centers, along b
    span_y = h - 2 * ci
    if span_x <= 0 or span_y <= 0:
        raise SpecError(u"La seccion es muy pequena para ese recubrimiento y diametros")

    # Bars per face: faces at x = +/- (along h) and at y = +/- (along b).
    on_x_faces = 0
    on_y_faces = 0
    for _ in range(len(extra) // 2):
        if span_y / (on_x_faces + 1) >= span_x / (on_y_faces + 1):
            on_x_faces += 1
        else:
            on_y_faces += 1

    bars = [
        (-span_x / 2.0, -span_y / 2.0, corners[0]),
        (span_x / 2.0, -span_y / 2.0, corners[1]),
        (span_x / 2.0, span_y / 2.0, corners[2]),
        (-span_x / 2.0, span_y / 2.0, corners[3]),
    ]
    pairs = []
    for k in range(1, on_x_faces + 1):
        y = -span_y / 2.0 + span_y * k / (on_x_faces + 1)
        pairs.append(((-1, y, "x"), (1, y, "x")))
    for k in range(1, on_y_faces + 1):
        x = -span_x / 2.0 + span_x * k / (on_y_faces + 1)
        pairs.append(((x, -1, "y"), (x, 1, "y")))
    keys = iter(extra)
    for pair in pairs:
        for a, c, face in pair:
            key = next(keys)
            if face == "x":
                x = a * (b / 2.0 - inset(key))
                bars.append((x, c, key))
            else:
                y = c * (h / 2.0 - inset(key))
                bars.append((a, y, key))
    return bars


# --- Section drawing ("dibujo del armado") -----------------------------------
# A design lives in the column type as JSON, in local section coordinates
# (meters, x along the family's X, origin at the section's bounding-box
# center): longitudinal bars [x, y, diameter_key], closed stirrups drawn
# through the centers of the bars they wrap, and crossties (grapas) from
# one bar center to another.

DESIGN_VERSION = 2
# Each drawn stirrup/crosstie belongs to one stirrup family, chosen when
# sketching: the edge ("borde") or the confinement settings.
KIND_EDGE = u"borde"
KIND_CONFINEMENT = u"confinamiento"
KINDS = (KIND_EDGE, KIND_CONFINEMENT)


def empty_design():
    return {"v": DESIGN_VERSION, "bars": [], "stirrups": [], "ties": []}


def design_to_text(design):
    def r(v):
        return round(v, 4)

    data = {
        "v": DESIGN_VERSION,
        "bars": [[r(x), r(y), key] for x, y, key in design.get("bars", [])],
        "stirrups": [
            {"k": kind, "p": [[r(x), r(y)] for x, y in poly]}
            for kind, poly in design.get("stirrups", [])
        ],
        "ties": [
            {"k": kind, "p": [[r(a[0]), r(a[1])], [r(b[0]), r(b[1])]]}
            for kind, a, b in design.get("ties", [])
        ],
    }
    if not (data["bars"] or data["stirrups"] or data["ties"]):
        return u""
    return json.dumps(data, separators=(",", ":"))


def _kind_and_points(entry, default_kind):
    """(kind, points) of a saved stirrup/tie: {"k", "p"}, or a bare point
    list from the first format (kind unknown -> `default_kind`)."""
    if isinstance(entry, dict):
        kind = entry.get("k")
        points = entry.get("p", [])
    else:
        kind, points = default_kind, entry
    if kind not in KINDS:
        kind = default_kind
    return kind, [(float(x), float(y)) for x, y in points]


def design_from_text(text):
    """Design dict from its JSON text; None for blank text."""
    if not (text or u"").strip():
        return None
    try:
        data = json.loads(text)
        design = empty_design()
        for x, y, key in data.get("bars", []):
            if key not in BAR_DIAMETERS_MM:
                raise SpecError(u"Diametro desconocido en el dibujo: {}".format(key))
            design["bars"].append((float(x), float(y), key))
        for entry in data.get("stirrups", []):
            kind, points = _kind_and_points(entry, None)
            if len(points) >= 3:
                design["stirrups"].append((kind, points))
        for entry in data.get("ties", []):
            kind, points = _kind_and_points(entry, KIND_CONFINEMENT)
            if len(points) == 2:
                design["ties"].append((kind, points[0], points[1]))
        # Stirrups of the first format: perimeter ones are edge stirrups.
        design["stirrups"] = [
            (kind or (KIND_EDGE if is_edge_stirrup(poly, design["bars"]) else KIND_CONFINEMENT), poly)
            for kind, poly in design["stirrups"]
        ]
        return design
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SpecError(u"El dibujo guardado esta danado; vuelve a dibujarlo")


def polygon_signed_area(points):
    s = 0.0
    n = len(points)
    for k in range(n):
        x1, y1 = points[k]
        x2, y2 = points[(k + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def counterclockwise(points):
    return list(points) if polygon_signed_area(points) > 0 else list(reversed(points))


def offset_polygon_outward(points, distance):
    """Miter offset of a simple polygon, `distance` outward (any vertex
    order). Collinear vertices are dropped first."""
    pts = counterclockwise(points)
    cleaned = []
    n = len(pts)
    for k in range(n):
        ax, ay = pts[k - 1]
        bx, by = pts[k]
        cx, cy = pts[(k + 1) % n]
        if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) > 1e-12:
            cleaned.append(pts[k])
    pts = cleaned
    n = len(pts)
    if n < 3:
        raise SpecError(u"El estribo necesita al menos 3 esquinas")
    out = []
    for k in range(n):
        p0, p1, p2 = pts[k - 1], pts[k], pts[(k + 1) % n]
        lines = []
        for a, b in ((p0, p1), (p1, p2)):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            nx, ny = dy / length, -dx / length  # outward normal of a CCW edge
            lines.append(((a[0] + nx * distance, a[1] + ny * distance), (dx, dy)))
        (ax, ay), (adx, ady) = lines[0]
        (bx, by), (bdx, bdy) = lines[1]
        det = adx * bdy - ady * bdx
        t = ((bx - ax) * bdy - (by - ay) * bdx) / det
        out.append((ax + adx * t, ay + ady * t))
    return out


def point_in_polygon(point, polygon):
    x, y = point
    inside = False
    n = len(polygon)
    for k in range(n):
        x1, y1 = polygon[k]
        x2, y2 = polygon[(k + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def distance_to_polygon(point, polygon):
    """Distance from a point to the polygon's outline."""
    px, py = point
    best = None
    n = len(polygon)
    for k in range(n):
        (x1, y1), (x2, y2) = polygon[k], polygon[(k + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
        d = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
        best = d if best is None else min(best, d)
    return best


def _bar_at(point, bars, tol=0.005):
    for x, y, key in bars:
        if math.hypot(point[0] - x, point[1] - y) <= tol:
            return key
    return None


def stirrup_centerline(vertices, bars, stirrup_key):
    """Centerline of a stirrup drawn through bar centers: pushed outward
    by the wrapped bar radius plus half the stirrup diameter."""
    ds = BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    radii = [BAR_DIAMETERS_MM[k] / 2000.0 for k in (_bar_at(v, bars) for v in vertices) if k]
    return offset_polygon_outward(vertices, (max(radii) if radii else 0.0) + ds / 2.0)


def tie_centerline(start, end, bars, stirrup_key):
    """A crosstie drawn from one bar center to another, lengthened at each
    end to the far side of that bar (where its hook wraps it)."""
    ds = BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 0.02:
        raise SpecError(u"Grapa demasiado corta")
    ux, uy = dx / length, dy / length
    ks, ke = _bar_at(start, bars), _bar_at(end, bars)
    es = (BAR_DIAMETERS_MM[ks] / 2000.0 if ks else 0.0) + ds / 2.0
    ee = (BAR_DIAMETERS_MM[ke] / 2000.0 if ke else 0.0) + ds / 2.0
    return ((start[0] - ux * es, start[1] - uy * es), (end[0] + ux * ee, end[1] + uy * ee))


def auto_design(b, h, cover, stirrup_key, groups):
    """Rectangular section: bars from `layout_rectangular_bars` and one
    perimeter stirrup through the four corner bars."""
    ds = BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    bars = layout_rectangular_bars(b, h, cover, ds, groups)
    design = empty_design()
    design["bars"] = [(x, y, k) for x, y, k in bars]
    design["stirrups"] = [(KIND_EDGE, [(x, y) for x, y, _ in bars[:4]])]
    return design


def joint_positions(joint_length, spacing, end_clear=0.05):
    """Stirrup offsets inside the beam-column joint ("nucleo"): from the
    beam underside up to `end_clear` below the column top."""
    positions = []
    z = spacing
    while z <= joint_length - end_clear + 1e-6:
        positions.append(z)
        z += spacing
    if not positions and joint_length > 2 * end_clear:
        positions.append(joint_length / 2.0)
    return positions


def is_edge_stirrup(polygon, bars, tol=0.005):
    """A stirrup drawn around every longitudinal bar is the perimeter
    ("borde") stirrup; one around only some of them is a confinement
    ("confinamiento") stirrup."""
    if not bars:
        return True
    for x, y, _ in bars:
        if not (point_in_polygon((x, y), polygon) or distance_to_polygon((x, y), polygon) <= tol):
            return False
    return True


def auto_tie(click, bars, tol=0.01):
    """Crosstie for a single click: the pair of facing bars (the only two
    bars on a horizontal or vertical line of the section, so the tie
    crosses it from face to face) whose segment passes closest to the
    click. Returns (start, end) bar centers; SpecError if no pair."""
    best = None
    for i, (x1, y1, _) in enumerate(bars):
        for x2, y2, _ in bars[i + 1:]:
            if abs(y1 - y2) <= tol:
                axis = 1  # same y: tie along x
            elif abs(x1 - x2) <= tol:
                axis = 0  # same x: tie along y
            else:
                continue
            on_line = [
                b for b in bars
                if abs((b[1] - y1) if axis == 1 else (b[0] - x1)) <= tol
            ]
            if len(on_line) != 2:
                continue  # a row of 3+ bars is a face, not a crossing
            d = distance_to_polygon(click, [(x1, y1), (x2, y2)])
            if best is None or d < best[0]:
                best = (d, (x1, y1), (x2, y2))
    if best is None:
        raise SpecError(u"No hay dos barras enfrentadas para la grapa")
    return best[1], best[2]
