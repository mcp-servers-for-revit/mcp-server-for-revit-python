# -*- coding: UTF-8 -*-
"""
Pure-Python helpers for the rebar ("Acero") generator: reading the
per-type reinforcement table, stirrup spacing, longitudinal bar layout and
bar weights. No Revit imports, so it can be unit-tested outside Revit.

Lengths are in meters unless a name says otherwise.
"""
import io
import json
import math
import os
import re

# Nominal diameters (mm) of the bars used in Peru (ASTM A615 / NTP 341.031).
# 1 3/8" is the #11 bar (35.8 mm, 1006 mm2), as sold under that name.
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
    u'1 3/8"': 35.8,
}

STEEL_DENSITY_KG_M3 = 7850.0

# Weight per diameter table (data/acero_pesos_por_diametro.csv), editable.
WEIGHT_TABLE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "acero_pesos_por_diametro.csv")
_weight_table = None


class SpecError(ValueError):
    """A reinforcement table entry that can't be read (message in Spanish,
    shown to the user)."""


def read_weight_table(path=WEIGHT_TABLE_FILE):
    """{diameter key: {"area_mm2", "nominal", "minimum"}} (kg/m) from the
    weight table: 'DIAMETRO;AREA_NOMINAL_MM2;PESO_NOMINAL_KG_M;PESO_MINIMO_KG_M'
    lines, '#' comments and the header skipped. A diameter the plugin
    doesn't know yet (a 7/8" row added to the table) becomes a known bar,
    its diameter taken from its nominal area."""
    table = {}
    with io.open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith(u"#") or line.upper().startswith(u"DIAMETRO"):
                continue
            cells = [c.strip() for c in line.split(u";")]
            try:
                key = _diameter_key(cells[0])
                row = {
                    "area_mm2": float(cells[1]),
                    "nominal": float(cells[2]),
                    "minimum": float(cells[3]),
                }
            except (IndexError, ValueError):
                raise SpecError(u"Tabla de pesos, linea {}: no se entiende '{}'".format(number, line))
            if key not in BAR_DIAMETERS_MM:
                BAR_DIAMETERS_MM[key] = round(math.sqrt(4.0 * row["area_mm2"] / math.pi), 2)
            table[key] = row
    return table


def weight_table():
    """The weight table, read once."""
    global _weight_table
    if _weight_table is None:
        _weight_table = read_weight_table()
    return _weight_table


def bar_weight_kg_per_m(diameter_key):
    """Nominal weight of the table; for a diameter not in it, the steel
    density times the bar's area."""
    row = weight_table().get(diameter_key)
    if row:
        return row["nominal"]
    d = BAR_DIAMETERS_MM[diameter_key] / 1000.0
    return STEEL_DENSITY_KG_M3 * math.pi * d * d / 4.0


def stack_lifts(items):
    """How far (m) each stirrup/tie is raised over its set height so the
    ones set at the same height lie stacked, touching, like on site, not
    through each other. `items`: [(kind, diameter_m, is_tie)] in drawing
    order. Edge ("borde") stirrups go first, then confinement; within a
    kind, stirrups before ties. The first one stays where it is set."""
    order = sorted(range(len(items)), key=lambda i: (
        0 if items[i][0] == KIND_EDGE else 1, 1 if items[i][2] else 0, i))
    lifts = [0.0] * len(items)
    height = 0.0
    for i in order:
        lifts[i] = height
        height += items[i][1]
    return lifts


def diameter_from_name(name):
    """The bar diameter written in a bar type name ('..._Ø5/8"_ZAPATA',
    'Ø12mm_COLUMNA C-8'), or None."""
    m = re.search(u'[Øø∅]\\s*(\\d+(?:\\s+\\d+/\\d+|/\\d+)?\\s*(?:"|”|mm))', name or u"", re.IGNORECASE)
    if not m:
        return None
    try:
        return parse_diameter(m.group(1))
    except SpecError:
        return None


def nearest_diameter(diameter_mm, tolerance_mm=0.5):
    """The diameter key whose nominal diameter is within tolerance, or None."""
    key, best = None, tolerance_mm
    for k, d in BAR_DIAMETERS_MM.items():
        if abs(d - diameter_mm) <= best:
            key, best = k, abs(d - diameter_mm)
    return key


def _clean(text):
    t = (text or u"").strip().lower()
    # A space, not nothing: "8Ø5/8" must stay "8 5/8", not "85/8".
    for ch in (u"ø", u"Ø", u"φ", u"∅", u"#"):
        t = t.replace(ch, u" ")
    for q in (u"”", u"″", u"''", u"“"):
        t = t.replace(q, u'"')
    return re.sub(r"\s+", u" ", t).strip()


def _diameter_key(text):
    """'5/8"', 'Ø5/8', '5/8 pulg', '1 3/8"', '12mm', '8 mm' -> the key
    form ('5/8"', '12mm'), known or not."""
    t = _clean(text)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*mm$", t)
    if m:
        return u"{}mm".format(m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else m.group(1))
    t = re.sub(r'\s*(pulg|in|")\s*$', u"", t).replace(u"-", u" ").strip()
    return t + u'"'


def parse_diameter(text):
    """'5/8"', 'Ø5/8', '5/8 pulg', '1 3/8"', '12mm', '8 mm' -> a
    BAR_DIAMETERS_MM key."""
    key = _diameter_key(text)
    if key in BAR_DIAMETERS_MM:
        return key
    raise SpecError(
        u'Diametro no reconocido: "{}" (usa 3/8", 1/2", 5/8", 3/4", 1", 8mm, 12mm... '
        u'o agregalo a la tabla de pesos)'.format(text)
    )


def bar_diameter_keys():
    """Every known bar diameter, thinnest first (the weight table's ones
    included)."""
    return sorted(BAR_DIAMETERS_MM, key=lambda k: BAR_DIAMETERS_MM[k])


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
    return [p for p, _ in stirrup_zone_positions(length, zones, rest)]


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


def design_shapes(design, key):
    """Rebar shape name of each of the design's "stirrups" or "ties" (key),
    in the same order; None where Revit picks the shape itself."""
    shapes = design.setdefault("shapes", {}).setdefault(key, [])
    count = len(design.get(key, []))
    shapes.extend([None] * (count - len(shapes)))
    del shapes[count:]
    return shapes


def add_item(design, key, item, shape=None):
    """Append a stirrup or tie (key "stirrups" / "ties") with its shape."""
    shapes = design_shapes(design, key)
    design[key].append(item)
    shapes.append(shape)


def remove_item(design, key, index):
    """Delete one stirrup or tie together with its shape."""
    shapes = design_shapes(design, key)
    del design[key][index]
    del shapes[index]


def design_to_text(design):
    def r(v):
        return round(v, 4)

    def shape(key, i):
        name = design_shapes(design, key)[i]
        return [("f", name)] if name else []

    data = {
        "v": DESIGN_VERSION,
        "bars": [[r(x), r(y), key] for x, y, key in design.get("bars", [])],
        "stirrups": [
            dict(
                [("k", kind), ("p", [[r(x), r(y)] for x, y in poly])]
                + ([("r", r(wrap))] if wrap is not None else [])
                + ([("o", 1)] if is_open else [])
                + shape("stirrups", i)
            )
            for i, (kind, poly, wrap, is_open) in enumerate(design.get("stirrups", []))
        ],
        "ties": [
            dict([("k", kind), ("p", [[r(a[0]), r(a[1])], [r(b[0]), r(b[1])]])] + shape("ties", i))
            for i, (kind, a, b) in enumerate(design.get("ties", []))
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
            wrap = entry.get("r") if isinstance(entry, dict) else None
            is_open = bool(entry.get("o")) if isinstance(entry, dict) else False
            shape = entry.get("f") if isinstance(entry, dict) else None
            if len(points) >= (2 if is_open else 3):
                add_item(design, "stirrups",
                         (kind, points, None if wrap is None else float(wrap), is_open), shape)
        for entry in data.get("ties", []):
            kind, points = _kind_and_points(entry, KIND_CONFINEMENT)
            shape = entry.get("f") if isinstance(entry, dict) else None
            if len(points) == 2:
                add_item(design, "ties", (kind, points[0], points[1]), shape)
        # Stirrups of the first format: perimeter ones are edge stirrups.
        design["stirrups"] = [
            (kind or (KIND_EDGE if is_edge_stirrup(poly, design["bars"]) else KIND_CONFINEMENT),
             poly, wrap, is_open)
            for kind, poly, wrap, is_open in design["stirrups"]
        ]
        return design
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SpecError(u"El dibujo guardado esta danado; vuelve a dibujarlo")


# --- configuration files (Acero "Guardar en archivo" / "Abrir archivo") -------

CONFIG_FILE_VERSION = 1
CONFIG_FORM_KEYS = ("conf", "conf_dist", "edge", "edge_dist", "cover", "nucleo")


def config_file_text(type_name, size_cm, form, design):
    """JSON text of a column type's steel configuration: the stirrup
    settings of the form, the section drawing and the section size (b, h
    in cm) it was drawn on."""
    return json.dumps({
        "acero_columnas": CONFIG_FILE_VERSION,
        "tipo": type_name,
        "seccion_cm": [round(size_cm[0], 1), round(size_cm[1], 1)],
        "estribos": dict((k, form.get(k) or u"") for k in CONFIG_FORM_KEYS),
        "dibujo": design_to_text(design),
    }, indent=2, ensure_ascii=False)


def read_config_file(text):
    """(type name, (b, h) cm, form, design or None) of a configuration file
    written by `config_file_text` (raises SpecError)."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or "acero_columnas" not in data:
            raise SpecError(u"el archivo no es una configuracion de Acero - Columnas")
        b, h = data.get("seccion_cm") or (0.0, 0.0)
        estribos = data.get("estribos") or {}
        form = dict((k, u"{}".format(estribos.get(k) or u"")) for k in CONFIG_FORM_KEYS)
        return data.get("tipo") or u"", (float(b), float(h)), form, design_from_text(data.get("dibujo"))
    except (ValueError, TypeError, AttributeError) as e:
        if isinstance(e, SpecError):
            raise
        raise SpecError(u"el archivo esta danado o no es una configuracion de Acero")


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


def wrap_radius(vertices, bars):
    """Radius of the largest bar a stirrup drawn through `vertices` wraps
    (0 when its corners aren't on bars)."""
    radii = [BAR_DIAMETERS_MM[k] / 2000.0 for k in (_bar_at(v, bars) for v in vertices) if k]
    return max(radii) if radii else 0.0


def offset_polyline_outward(points, distance):
    """Offset of an open polyline (an open, U-shaped stirrup) away from the
    bars it wraps: the side opposite to its inside, taken as the polygon
    it would make if closed. Inner corners are mitered; the two ends move
    square to their own segment."""
    pts = list(points)
    n = len(pts)
    if n < 2:
        raise SpecError(u"El estribo abierto necesita al menos 2 puntos")
    sign = 1.0 if n < 3 or polygon_signed_area(pts) > 0 else -1.0
    normals = []
    for k in range(n - 1):
        dx, dy = pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            raise SpecError(u"El estribo tiene dos puntos repetidos")
        normals.append((sign * dy / length, -sign * dx / length))
    out = []
    for k in range(n):
        if k == 0 or k == n - 1:
            nx, ny = normals[0] if k == 0 else normals[-1]
            out.append((pts[k][0] + nx * distance, pts[k][1] + ny * distance))
            continue
        (ax, ay), (bx, by) = normals[k - 1], normals[k]
        mx, my = ax + bx, ay + by
        dot = mx * ax + my * ay
        if abs(dot) < 1e-9:  # a U-turn: just push along the first normal
            mx, my, dot = ax, ay, 1.0
        scale = distance / dot
        out.append((pts[k][0] + mx * scale, pts[k][1] + my * scale))
    return out


def stirrup_centerline(vertices, bars, stirrup_key, wrap=None, is_open=False):
    """Centerline of a stirrup drawn through bar centers: pushed outward
    by the wrapped bar radius (`wrap`, stored with the stirrup so editing
    its measures can't change it; computed from the bars when None) plus
    half the stirrup diameter. `is_open`: a U-shaped stirrup whose ends
    are not joined."""
    ds = BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    if wrap is None:
        wrap = wrap_radius(vertices, bars)
    offset = offset_polyline_outward if is_open else offset_polygon_outward
    return offset(vertices, wrap + ds / 2.0)


def stirrup_outline(vertices, bars, stirrup_key, wrap=None, is_open=False):
    """Outer face of the stirrup (its measured size)."""
    ds = BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    if wrap is None:
        wrap = wrap_radius(vertices, bars)
    offset = offset_polyline_outward if is_open else offset_polygon_outward
    return offset(vertices, wrap + ds)


def side_lengths(polygon, closed=True):
    n = len(polygon)
    count = n if closed else n - 1
    return [math.hypot(polygon[(k + 1) % n][0] - polygon[k][0], polygon[(k + 1) % n][1] - polygon[k][1])
            for k in range(count)]


def distance_to_polyline(point, points):
    """Distance from a point to an open polyline."""
    best = None
    for k in range(len(points) - 1):
        d = distance_to_polygon(point, [points[k], points[k + 1]])
        best = d if best is None else min(best, d)
    return best if best is not None else float("inf")


def rect_measures(outline, section_polygon, tol=1e-4):
    """(width, height, to_left_face, to_bottom_face) of an axis-aligned
    rectangular stirrup outline, measured from the section's bounding
    faces; None for any other shape."""
    if len(outline) != 4:
        return None
    xs = sorted(set(round(x, 6) for x, _ in outline))
    ys = sorted(set(round(y, 6) for _, y in outline))
    if len(xs) != 2 or len(ys) != 2 or xs[1] - xs[0] < tol or ys[1] - ys[0] < tol:
        return None
    left = min(x for x, _ in section_polygon)
    bottom = min(y for _, y in section_polygon)
    return xs[1] - xs[0], ys[1] - ys[0], xs[0] - left, ys[0] - bottom


def rect_from_measures(width, height, to_left, to_bottom, section_polygon, stirrup_key, wrap):
    """Vertices (bar-center rectangle, as drawn) of a rectangular stirrup
    whose outer face measures width x height at the given distances from
    the section's left and bottom faces."""
    inset = wrap + BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    if width <= 2 * inset + 0.01 or height <= 2 * inset + 0.01:
        raise SpecError(u"Medidas demasiado pequenas para ese diametro")
    x0 = min(x for x, _ in section_polygon) + to_left + inset
    y0 = min(y for _, y in section_polygon) + to_bottom + inset
    x1 = x0 + width - 2 * inset
    y1 = y0 + height - 2 * inset
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


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
    corners = [(x, y) for x, y, _ in bars[:4]]
    design["stirrups"] = [(KIND_EDGE, corners, wrap_radius(corners, design["bars"]), False)]
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


# --- Fitting rectangular stirrups to the cover --------------------------------
# Rectangles here are (x0, y0, x1, y1) of a stirrup's outer face.

COVER_SNAP = 0.025  # a drawn side this close to the cover line lands on it


def cover_bounds(section_polygon, cover):
    """Rectangle a stirrup's outer face must stay inside: the section's
    bounding faces moved in by the cover."""
    xs = [x for x, _ in section_polygon]
    ys = [y for _, y in section_polygon]
    return min(xs) + cover, min(ys) + cover, max(xs) - cover, max(ys) - cover


def snap_rect_to_cover(rect, bounds, snap=COVER_SNAP):
    """A drawn stirrup: each side that crosses the cover line, or is within
    `snap` of it, is placed on it."""
    x0, y0, x1, y1 = rect
    bx0, by0, bx1, by1 = bounds
    x0 = bx0 if x0 - bx0 <= snap else x0
    y0 = by0 if y0 - by0 <= snap else y0
    x1 = bx1 if bx1 - x1 <= snap else x1
    y1 = by1 if by1 - y1 <= snap else y1
    return x0, y0, x1, y1


def resize_rect_in_cover(rect, width, height, bounds):
    """A stirrup resized to width x height around its own center, then
    shifted (and if needed shrunk) so it stays inside the cover."""
    bx0, by0, bx1, by1 = bounds
    width = min(width, bx1 - bx0)
    height = min(height, by1 - by0)
    cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
    x0 = min(max(cx - width / 2.0, bx0), bx1 - width)
    y0 = min(max(cy - height / 2.0, by0), by1 - height)
    return x0, y0, x0 + width, y0 + height


def rect_vertices(rect, stirrup_key, wrap):
    """Drawn vertices (through the wrapped bar centers) of a stirrup whose
    outer face is `rect`."""
    inset = wrap + BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    x0, y0, x1, y1 = rect[0] + inset, rect[1] + inset, rect[2] - inset, rect[3] - inset
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        raise SpecError(u"Medidas demasiado pequenas para ese diametro")
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def outer_rect(vertices, bars, stirrup_key, wrap=None):
    """(x0, y0, x1, y1) of a stirrup's outer face."""
    outline = stirrup_outline(vertices, bars, stirrup_key, wrap)
    xs = [x for x, _ in outline]
    ys = [y for _, y in outline]
    return min(xs), min(ys), max(xs), max(ys)


def fit_vertex(point, section_polygon, cover, stirrup_key, bar_radius=0.0,
               rectangular=True, snap=COVER_SNAP, tol=1e-4):
    """A stirrup corner clicked at `point` (on a bar of `bar_radius`, or 0
    for a free point), or None if the stirrup would leave the cover there.
    In a rectangular section a free corner within `snap` inside the limit
    is moved onto it; a corner past the limit is never accepted."""
    margin = cover + BAR_DIAMETERS_MM[stirrup_key] / 1000.0 + bar_radius
    zone = offset_polygon_outward(section_polygon, -margin)
    x, y = point
    if rectangular:
        x0 = min(p[0] for p in zone)
        x1 = max(p[0] for p in zone)
        y0 = min(p[1] for p in zone)
        y1 = max(p[1] for p in zone)
        if not (x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol):
            return None
        if not bar_radius:
            x = x0 if x - x0 <= snap else (x1 if x1 - x <= snap else x)
            y = y0 if y - y0 <= snap else (y1 if y1 - y <= snap else y)
        return (x, y)
    if point_in_polygon(point, zone) or distance_to_polygon(point, zone) <= tol:
        return point
    return None


def stirrup_inside_cover(vertices, bars, stirrup_key, wrap, section_polygon, cover, tol=1e-3,
                         is_open=False):
    """Does the stirrup's outer face stay within the cover?"""
    zone = offset_polygon_outward(section_polygon, -cover)
    for p in stirrup_outline(vertices, bars, stirrup_key, wrap, is_open):
        if not (point_in_polygon(p, zone) or distance_to_polygon(p, zone) <= tol):
            return False
    return True


FREE_CORNER_SNAP = 0.01  # Estribo tool: free corners snap to the cover only this close


def clean_polyline(points, closed, tol=1e-4):
    """Drop repeated points and corners lying on a straight line (Revit
    can't build a stirrup from collinear consecutive segments)."""
    pts = []
    for p in points:
        if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > tol:
            pts.append(p)
    if closed and len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= tol:
        pts.pop()
    changed = True
    while changed and len(pts) > (3 if closed else 2):
        changed = False
        n = len(pts)
        for k in range(n) if closed else range(1, n - 1):
            a, b, c = pts[k - 1], pts[k], pts[(k + 1) % n]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cross) <= tol * tol:
                del pts[k]
                changed = True
                break
    return pts


def resize_segment(points, index, delta):
    """Lengthen (delta > 0) or shorten segment `index` of an open stirrup
    along its own direction, keeping all angles (chamfers, jogs). The first
    segment grows from its free end (the start point moves back); any
    other one pushes every point after it. Returns the new points."""
    pts = list(points)
    (x1, y1), (x2, y2) = pts[index], pts[index + 1]
    length = math.hypot(x2 - x1, y2 - y1)
    if length + delta < 0.005:
        raise SpecError(u"El tramo {} quedaria demasiado corto".format(index + 1))
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    if index == 0 and len(pts) > 2:
        return [(x1 - ux * delta, y1 - uy * delta)] + pts[1:]
    return pts[:index + 1] + [(x + ux * delta, y + uy * delta) for x, y in pts[index + 1:]]


# --- Rebar shapes from Revit's shape browser -----------------------------------
# A shape arrives as the straight segments of its browser drawing (bends
# are arcs and are left out), in drawing order: [((x0, y0), (x1, y1)), ...].

def chain_vertices(lines, tol=1e-6):
    """Corner points of a chain of straight segments: where each segment's
    line meets the next one's (the bend arcs between them removed)."""
    if not lines:
        raise SpecError(u"La forma no tiene tramos rectos")
    segs = [list(lines[0])]
    for a, b in lines[1:]:
        last = segs[-1][1]
        # orient each segment to continue from the previous one
        if math.hypot(b[0] - last[0], b[1] - last[1]) < math.hypot(a[0] - last[0], a[1] - last[1]):
            a, b = b, a
        segs.append([a, b])
    if len(segs) > 1:
        # the first segment may need flipping to lead into the second
        a, b = segs[0]
        nxt = segs[1][0]
        if math.hypot(a[0] - nxt[0], a[1] - nxt[1]) < math.hypot(b[0] - nxt[0], b[1] - nxt[1]):
            segs[0] = [b, a]
    vertices = [segs[0][0]]
    for (a1, b1), (a2, b2) in zip(segs, segs[1:]):
        d1 = (b1[0] - a1[0], b1[1] - a1[1])
        d2 = (b2[0] - a2[0], b2[1] - a2[1])
        det = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(det) < tol:
            vertices.append(b1)  # parallel: no corner to rebuild
            continue
        t = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / det
        vertices.append((a1[0] + d1[0] * t, a1[1] + d1[1] * t))
    vertices.append(segs[-1][1])
    return vertices


def _close_corner(vertices):
    """A closed chain's last-to-first corner rebuilt where the first and
    last segments' lines meet (Revit leaves a gap there for the hook)."""
    pts = list(vertices[:-1])
    (a1, b1), (a2, b2) = (vertices[-2], vertices[-1]), (vertices[0], vertices[1])
    d1 = (b1[0] - a1[0], b1[1] - a1[1])
    d2 = (b2[0] - a2[0], b2[1] - a2[1])
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) > 1e-9:
        t = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / det
        pts[0] = (a1[0] + d1[0] * t, a1[1] + d1[1] * t)
    return pts


def _closes(vertices):
    """Do a chain's two ends meet (a closed stirrup)?"""
    if len(vertices) < 4:
        return False
    xs = [x for x, _ in vertices]
    ys = [y for _, y in vertices]
    size = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    first, last = vertices[0], vertices[-1]
    return math.hypot(first[0] - last[0], first[1] - last[1]) <= 0.15 * size


def shape_outline(lines, hook_at_start=False, hook_at_end=False):
    """(vertices, is_closed) of a rebar shape for the sketch. A closed
    stirrup - its ends meet as drawn, or once the straight legs of its
    hooks are dropped - comes without hooks (Revit adds its 135-degree
    ones back); an open shape keeps every segment it shows in Revit."""
    lines = list(lines)
    body = list(lines)
    if hook_at_start and len(body) > 2:
        body = body[1:]
    if hook_at_end and len(body) > 2:
        body = body[:-1]
    if len(body) < len(lines):
        without_hooks = chain_vertices(body)
        if _closes(without_hooks):
            return clean_polyline(_close_corner(without_hooks), closed=True), True
    vertices = chain_vertices(lines)
    if _closes(vertices):
        return clean_polyline(_close_corner(vertices), closed=True), True
    return clean_polyline(vertices, closed=False), False


def fit_polyline_to_box(points, box):
    """Stretch a shape to fill box (x0, y0, x1, y1); a straight bar is
    centered along its missing dimension."""
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    x0, y0, x1, y1 = box

    def along(v, lo, span, a, b):
        return (a + b) / 2.0 if span < 1e-9 else a + (v - lo) / span * (b - a)

    return [(along(x, min(xs), w, x0, x1), along(y, min(ys), h, y0, y1)) for x, y in points]


STIRRUP_MERGE_GAP = 0.01  # two stirrups closer than this are one


def stirrup_sets(length, zones, rest):
    """(Rule agreed with the user, see Acero.pushbutton/REGLAS_ACERO.md: do
    not change it without their say.)

    The stirrup sets of a clear length, in order from the bottom, as
    they are placed on site ('1@.05, 5@.10, rto@.20'), everything measured
    from each end towards the middle, the top mirroring the bottom:
    - every zone is its own set: 1 stirrup at 0.05 (a single one), then 5
      at 0.10 counted from it (0.15 ... 0.55);
    - the rest, a set from each end every `rest` counted from the last
      stirrup of that end's zones (0.75, 0.95 ...), up to the middle;
    - the leftover gap is in the middle: one more stirrup there when it is
      wider than `rest`, and just one where the two ends meet on the same
      spot.
    Zones that don't fit in a short column stop at its middle.
    Returns [(start, count, spacing, zone_index, side)]: zone_index the
    position in `zones` or len(zones) for the rest; side +1 for the bottom
    half, -1 for the top half (see rc._runs)."""
    if length <= 0:
        return []
    rest_zone = len(zones)
    half = length / 2.0
    eps = 1e-6
    bottom = []  # [(offset, zone)] of the bottom half
    z = 0.0
    for index, (count, spacing) in enumerate(zones):
        for _ in range(count):
            if z + spacing > half + eps:
                break
            z += spacing
            bottom.append((z, index))
    while z + rest <= half + eps:
        z += rest
        bottom.append((z, rest_zone))
    top = [(length - p, zone) for p, zone in reversed(bottom)]
    middle = []
    if not bottom:
        middle = [(half, rest_zone)]
    elif top[0][0] - bottom[-1][0] < STIRRUP_MERGE_GAP:
        top = top[1:]  # both ends reach the same middle spot: one stirrup there
    elif top[0][0] - bottom[-1][0] > rest + eps:
        middle = [(half, rest_zone)]  # the leftover in the middle, never wider than rest

    sets = []

    def add_zones(items, side):
        i = 0
        while i < len(items):
            j = i
            while j + 1 < len(items) and items[j + 1][1] == items[i][1]:
                j += 1
            points = [p for p, _ in items[i:j + 1]]
            spacing = points[1] - points[0] if len(points) > 1 else 0.0
            sets.append((points[0], len(points), spacing, items[i][1], side))
            i = j + 1

    add_zones(bottom, 1)
    add_zones(middle, 1)
    add_zones(top, -1)
    return sets


def stirrup_zone_positions(length, zones, rest):
    """Every stirrup of `stirrup_sets`, bottom up, tagged with its zone:
    [(offset, zone_index)]."""
    return [(start + k * spacing, zone)
            for start, count, spacing, zone, _ in stirrup_sets(length, zones, rest)
            for k in range(count)]


# --- Custom longitudinal bars, seated against the stirrup ---------------------

BAR_SEAT_SNAP = 0.03  # a bar clicked this close to a stirrup sits against it


def custom_bar_layout(inner, corner_key, face_x, face_y):
    """Bars of a rectangular section laid against a stirrup's inner face.
    `inner` = (x0, y0, x1, y1) of that face; corner bars (corner_key) sit
    in its corners; face_x = (count, key) bars on EACH face parallel to x
    (top and bottom, the corners not counted), face_y the same for the two
    faces parallel to y. Every bar touches the stirrup with its own
    diameter. Returns [(x, y, key)], corners first."""
    x0, y0, x1, y1 = inner

    def r(key):
        return BAR_DIAMETERS_MM[key] / 2000.0

    rc_ = r(corner_key)
    if x1 - x0 < 4 * rc_ or y1 - y0 < 4 * rc_:
        raise SpecError(u"La seccion es muy pequena para esas barras")
    bars = [(x0 + rc_, y0 + rc_, corner_key), (x1 - rc_, y0 + rc_, corner_key),
            (x1 - rc_, y1 - rc_, corner_key), (x0 + rc_, y1 - rc_, corner_key)]
    count, key = face_x
    for k in range(1, count + 1):
        x = (x0 + rc_) + (x1 - x0 - 2 * rc_) * k / (count + 1)
        bars.append((x, y0 + r(key), key))
        bars.append((x, y1 - r(key), key))
    count, key = face_y
    for k in range(1, count + 1):
        y = (y0 + rc_) + (y1 - y0 - 2 * rc_) * k / (count + 1)
        bars.append((x0 + r(key), y, key))
        bars.append((x1 - r(key), y, key))
    return bars


BAR_PLACE_SNAP = 0.06  # the bar tool seats a bar on a stirrup this close to it
BAR_ALIGN_SNAP = 0.03  # ...and lines it up with a bar this close in x or y


def _nearest_seat(click, seats, snap):
    """(distance, point, edge index or None at a corner) of the nearest
    place on the seat polygon, or None beyond `snap`."""
    for corner in seats:
        if math.hypot(click[0] - corner[0], click[1] - corner[1]) <= snap * 0.7:
            return (math.hypot(click[0] - corner[0], click[1] - corner[1]), corner, None)
    best = None
    n = len(seats)
    for k in range(n):
        (ax, ay), (bx, by) = seats[k], seats[(k + 1) % n]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 < 1e-12:
            continue
        t = max(0.0, min(1.0, ((click[0] - ax) * dx + (click[1] - ay) * dy) / l2))
        px, py = ax + t * dx, ay + t * dy
        d = math.hypot(click[0] - px, click[1] - py)
        if d <= snap and (best is None or d < best[0]):
            best = (d, (px, py), k)
    return best


def _align(value, others, tol):
    near = [o for o in others if abs(o - value) <= tol]
    return min(near, key=lambda o: abs(o - value)) if near else value


def place_bar(click, bar_key, bars, stirrups, snap=BAR_PLACE_SNAP, align=BAR_ALIGN_SNAP):
    """Where a longitudinal bar clicked at `click` goes. On site every bar
    is tied to the stirrup: within `snap` of a closed stirrup (`stirrups`:
    [(outline, stirrup_key)]) the bar sits against its inner face - in its
    corner near one -, and along that face it lines up with the bars
    already placed (same x or y within `align`), so facing bars sit face
    to face. Away from any stirrup it only lines up with the other bars."""
    rb = BAR_DIAMETERS_MM[bar_key] / 2000.0
    xs = [x for x, _, _ in bars]
    ys = [y for _, y, _ in bars]
    best = None
    for outline, stirrup_key in stirrups:
        ds = BAR_DIAMETERS_MM[stirrup_key] / 1000.0
        try:
            seats = offset_polygon_outward(outline, -(ds + rb))
        except SpecError:
            continue
        found = _nearest_seat(click, seats, snap)
        if found and (best is None or found[0] < best[0][0]):
            best = (found, seats)
    if best is None:
        return (_align(click[0], xs, align), _align(click[1], ys, align))
    (_, (px, py), edge), seats = best
    if edge is None:
        return (px, py)  # a corner
    (ax, ay), (bx, by) = seats[edge], seats[(edge + 1) % len(seats)]
    if abs(bx - ax) < 1e-9:  # a face along y: line up in y
        return (px, max(min(ay, by), min(max(ay, by), _align(py, ys, align))))
    if abs(by - ay) < 1e-9:  # a face along x: line up in x
        return (max(min(ax, bx), min(max(ax, bx), _align(px, xs, align))), py)
    return (px, py)


def bar_seat(click, stirrup_outline, stirrup_key, bar_key, snap=BAR_SEAT_SNAP):
    """Where a bar clicked at `click` sits against a closed stirrup: its
    center on the line `stirrup + bar radius` inside the stirrup's inner
    face (in a corner when the click is near one), or None if the click
    isn't within `snap` of that line."""
    ds = BAR_DIAMETERS_MM[stirrup_key] / 1000.0
    rb = BAR_DIAMETERS_MM[bar_key] / 2000.0
    try:
        seats = offset_polygon_outward(stirrup_outline, -(ds + rb))
    except SpecError:
        return None
    found = _nearest_seat(click, seats, snap)
    return found[1] if found else None


# The diameters the weight table adds are known from the start (parse_diameter).
try:
    weight_table()
except (IOError, OSError, SpecError):
    pass
