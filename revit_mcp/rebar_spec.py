# -*- coding: UTF-8 -*-
"""
Pure-Python helpers for the rebar ("Acero") generator: reading the
per-type reinforcement table, stirrup spacing, longitudinal bar layout and
bar weights. No Revit imports, so it can be unit-tested outside Revit.

Lengths are in meters unless a name says otherwise.
"""
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
    for token in re.split(r"[,;]", t):
        token = token.strip()
        if not token:
            continue
        m = re.match(r"^([a-z]+|\d+)\s*@\s*(\d*\.?\d+)\s*(cm|m)?$", token)
        if not m:
            raise SpecError(u'No se entiende "{}": usa por ejemplo 1@.05, 10@.10, rto@.20'.format(token))
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
