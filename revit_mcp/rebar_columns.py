# -*- coding: UTF-8 -*-
"""
Column Rebar Module for Revit MCP ("Acero", stage 1: columns)

Builds real Revit Rebar in rectangular concrete columns from a
per-type reinforcement table stored in type parameters:

- EA_Longitudinal          e.g. 8Ø5/8"  or  4Ø3/4" + 4Ø5/8"
- EA_Estribo_Diametro      e.g. 3/8"
- EA_Estribo_Distribucion  e.g. 1@.05, 10@.10, rto@.20

Longitudinal bars run the column's full height (level to level; laps
and anchorages are not modeled). Closed 135-degree stirrups are laid out
from both ends over the clear height, i.e. up to the underside of the
deepest beam/slab framing into the column top. Each column gets its
steel weight in EA_Peso_Acero_kg; every bar created carries its column's
id in EA_Origen_Id so a new run replaces it.

Runs inside Revit's IronPython engine - no f-strings.
"""
import re

from pyrevit import DB
from Autodesk.Revit.DB.Structure import (
    MultiplanarOption,
    Rebar,
    RebarBarType,
    RebarHookOrientation,
    RebarHookType,
    RebarHostData,
    RebarStyle,
)
from System.Collections.Generic import List

import formwork_params as fw_params
import rebar_spec as spec
from utils import element_id_value

FT = 0.3048  # m per ft
GROUP_NAME = "Acero"
TYPE_PARAMS = (
    "EA_Longitudinal",
    "EA_Estribo_Diametro",
    "EA_Estribo_Distribucion",
    "EA_Recubrimiento_cm",
)
WEIGHT_PARAM = "EA_Peso_Acero_kg"
ORIGIN_PARAM = "EA_Origen_Id"
DEFAULT_COVER_CM = 4.0  # E.060 columns
CONFINEMENT_COVER_CM = 2.5  # columnetas (confinement columns)
# Framing whose underside is at most this far below a column top frames
# into it (limits the stirrup clear height).
TOP_FRAMING_REACH_FT = 5.0


def element_name(element):
    """Name of any element or type. IronPython can't always read `.Name`
    on ElementType subclasses, so fall back to the base-class property."""
    try:
        return element.Name or u""
    except AttributeError:
        pass
    try:
        return DB.Element.Name.__get__(element) or u""
    except Exception:
        return u""


def ensure_parameters(doc):
    """Create/bind the rebar parameters. Inside an active Transaction."""
    columns = [DB.BuiltInCategory.OST_StructuralColumns]
    specs = [(name, True, columns, False) for name in TYPE_PARAMS]
    specs.append((WEIGHT_PARAM, False, columns, True))
    specs.append((ORIGIN_PARAM, True, [DB.BuiltInCategory.OST_Rebar], True))
    return fw_params.ensure_parameters(doc, GROUP_NAME, specs)


def default_cover_cm(type_name):
    """Cover suggested for a column type: 2.5 cm for columnetas, else 4."""
    name = (type_name or u"").upper()
    if u"COLUMNETA" in name or (type_mark(type_name) or u"").startswith(u"CC-"):
        return CONFINEMENT_COVER_CM
    return DEFAULT_COVER_CM


def read_type_spec(column_type):
    """(longitudinal, stirrup diameter, stirrup distribution, cover cm)
    texts of a column type; blanks when unset or not bound yet."""
    values = []
    for name in TYPE_PARAMS:
        p = column_type.LookupParameter(name)
        values.append((p.AsString() or u"") if p is not None else u"")
    return tuple(values)


def write_type_spec(column_type, values):
    for name, value in zip(TYPE_PARAMS, values):
        p = column_type.LookupParameter(name)
        if p is not None and not p.IsReadOnly:
            p.Set(value or u"")


class ColumnSpec(object):
    """Parsed reinforcement of one column type (raises spec.SpecError)."""

    def __init__(self, longitudinal, stirrup_diameter, distribution, cover_cm):
        self.groups = spec.parse_longitudinal(longitudinal)
        self.stirrup_key = spec.parse_diameter(stirrup_diameter)
        self.zones, self.rest = spec.parse_distribution(distribution)
        try:
            self.cover_ft = float(str(cover_cm).replace(u",", u".")) / 100.0 / FT
        except ValueError:
            raise spec.SpecError(u'Recubrimiento invalido: "{}" (en cm, ej. 4)'.format(cover_cm))
        if not 1.0 / 100.0 / FT <= self.cover_ft <= 10.0 / 100.0 / FT:
            raise spec.SpecError(u"Recubrimiento fuera de rango (1 a 10 cm)")


def type_mark(type_name):
    """'C-2' / 'CC-1' out of a column type name ('C-2_0.23x0.60m',
    '..._CC-1_0.14x0.20m'), or None."""
    m = re.search(r"(?<![A-Z0-9])(C{1,2}-\d+)", (type_name or u"").upper())
    return m.group(1) if m else None


class BarTypes(object):
    """Picks the project's RebarBarType for a bar diameter: same nominal
    diameter, preferring the one named for this column type (…COLUMNA C-2),
    then any column/columneta bar, then the SRB_ family."""

    def __init__(self, doc):
        self.types = []
        for bt in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
            d = getattr(bt, "BarNominalDiameter", None) or bt.BarDiameter
            self.types.append((d * FT * 1000.0, element_name(bt).upper(), bt))

    def pick(self, key, mark):
        d_mm = spec.BAR_DIAMETERS_MM[key]
        best = None
        for diameter, name, bt in self.types:
            if abs(diameter - d_mm) > 0.4:
                continue
            score = 0
            if mark and mark in name:
                score += 4
            if u"COLUMN" in name:
                score += 2
            if name.startswith(u"SRB_"):
                score += 1
            if best is None or score > best[0]:
                best = (score, bt)
        return best[1] if best else None


def stirrup_hook(doc, diameter_key):
    """The 135-degree stirrup hook for a bar diameter: one named for it
    ('Estribo 3/8" - 135'), else the seismic one (its length scales with
    the bar diameter), else any 135-degree hook; None if there is none.
    A hook sized for a bigger bar would cross a small section and stick
    out of it (a 3/8" hook on a 1/4" tie in a 14 cm columneta)."""
    ranked = []
    for h in DB.FilteredElementCollector(doc).OfClass(RebarHookType):
        if abs(h.HookAngle * 57.29578 - 135.0) > 1.0:
            continue
        name = element_name(h).lower()
        if diameter_key.lower() in name:
            rank = 0
        elif u"seismic" in name or u"sismic" in name:
            rank = 1
        elif u'"' in name or u"mm" in name:
            rank = 3  # named for another diameter
        else:
            rank = 2
        ranked.append((rank, h))
    ranked.sort(key=lambda r: r[0])
    return ranked[0][1] if ranked else None


class StirrupHooks(object):
    """Per-diameter cache of `stirrup_hook`."""

    def __init__(self, doc):
        self.doc = doc
        self.cache = {}

    def get(self, diameter_key):
        if diameter_key not in self.cache:
            self.cache[diameter_key] = stirrup_hook(self.doc, diameter_key)
        return self.cache[diameter_key]


def _solids(geometry):
    for obj in geometry or []:
        if isinstance(obj, DB.Solid) and obj.Volume > 1e-9:
            yield obj
        elif isinstance(obj, DB.GeometryInstance):
            for s in _solids(obj.GetInstanceGeometry()):
                yield s


class Section(object):
    """Local frame of a vertical rectangular column (feet): `transform`
    maps local (x along b, y along h, centered) to world; z is world."""

    def __init__(self, column):
        t = column.GetTransform()
        if abs(t.BasisZ.Z) < 0.999:
            raise spec.SpecError(u"columna inclinada (no soportada)")
        pts = []
        volume = 0.0
        # Original geometry: the family's own shape, before joins cut it.
        for solid in _solids(column.GetOriginalGeometry(DB.Options())):
            volume += solid.Volume
            for edge in solid.Edges:
                pts.extend(edge.Tessellate())
        if not pts:
            raise spec.SpecError(u"sin geometria")
        xs = [p.X for p in pts]
        ys = [p.Y for p in pts]
        zs = [p.Z for p in pts]
        self.b = max(xs) - min(xs)
        self.h = max(ys) - min(ys)
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        z0, z1 = min(zs), max(zs)
        box_volume = self.b * self.h * (z1 - z0)
        if box_volume <= 0 or abs(volume - box_volume) > 0.03 * box_volume:
            raise spec.SpecError(u"seccion no rectangular (no soportada)")
        self.transform = t
        self.center = (cx, cy)
        self.z_bottom = t.OfPoint(DB.XYZ(cx, cy, z0)).Z
        self.z_top = t.OfPoint(DB.XYZ(cx, cy, z1)).Z

    def point(self, x, y, z_world):
        """World point at local section offset (x, y) (ft, from the
        section center) and world elevation z."""
        p = self.transform.OfPoint(DB.XYZ(self.center[0] + x, self.center[1] + y, 0.0))
        return DB.XYZ(p.X, p.Y, z_world)


def clear_top(doc, column, section):
    """Underside of the deepest beam/slab framing into the column top, or
    the column top when nothing frames into it."""
    bb = column.get_BoundingBox(None)
    if bb is None:
        return section.z_top
    reach = 0.05
    outline = DB.Outline(
        DB.XYZ(bb.Min.X - reach, bb.Min.Y - reach, section.z_top - TOP_FRAMING_REACH_FT),
        DB.XYZ(bb.Max.X + reach, bb.Max.Y + reach, section.z_top + 0.01),
    )
    mid = (section.z_bottom + section.z_top) / 2.0
    best = section.z_top
    for bic in (DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_Floors):
        framing = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
        )
        for e in framing:
            ebb = e.get_BoundingBox(None)
            if ebb is not None and mid < ebb.Min.Z < best:
                best = ebb.Min.Z
    return best


def delete_generated(doc, column):
    """Delete the bars an earlier run generated for this column."""
    column_id = str(element_id_value(column.Id))
    host_data = RebarHostData.GetRebarHostData(column)
    if host_data is None:
        return 0
    old = []
    for rebar in host_data.GetRebarsInHost():
        p = rebar.LookupParameter(ORIGIN_PARAM)
        if p is not None and p.AsString() == column_id:
            old.append(rebar.Id)
    if old:
        doc.Delete(List[DB.ElementId](old))
    return len(old)


def _tag(rebar, column):
    p = rebar.LookupParameter(ORIGIN_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(str(element_id_value(column.Id)))


def _counterclockwise(points):
    """`points` ordered counterclockwise seen from above (+Z), so the
    stirrup hooks (Left orientation) always turn into the column - even on
    a mirrored instance, whose local frame is left-handed."""
    area = 0.0
    for k in range(len(points)):
        a, b = points[k], points[(k + 1) % len(points)]
        area += a.X * b.Y - b.X * a.Y
    return points if area > 0 else list(reversed(points))


def generate_column(doc, column, column_spec, bar_types, hooks, mark):
    """Create the column's longitudinal bars and stirrups (inside an
    active Transaction; Revit needs a Regenerate before their lengths are
    known, see `record_weight`). Returns [(rebar, diameter_key,
    is_stirrup)]."""
    section = Section(column)
    cover = column_spec.cover_ft
    stirrup_d = spec.BAR_DIAMETERS_MM[column_spec.stirrup_key] / 304.8

    stirrup_type = bar_types.pick(column_spec.stirrup_key, mark)
    if stirrup_type is None:
        raise spec.SpecError(u"no hay tipo de barra de {}".format(column_spec.stirrup_key))

    delete_generated(doc, column)
    created = []

    # Longitudinal bars (layout in meters, geometry in feet).
    layout = spec.layout_rectangular_bars(
        section.b * FT, section.h * FT, cover * FT, stirrup_d * FT, column_spec.groups
    )
    normal = section.transform.BasisX
    for x, y, key in layout:
        bar_type = bar_types.pick(key, mark)
        if bar_type is None:
            raise spec.SpecError(u"no hay tipo de barra de {}".format(key))
        line = DB.Line.CreateBound(
            section.point(x / FT, y / FT, section.z_bottom),
            section.point(x / FT, y / FT, section.z_top),
        )
        rebar = Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, bar_type, None, None, column, normal,
            List[DB.Curve]([line]),
            RebarHookOrientation.Right, RebarHookOrientation.Right, True, True,
        )
        _tag(rebar, column)
        created.append((rebar, key, False))

    # Stirrups: closed loop on the bar-center line, one rebar set per run
    # of constant spacing.
    hook = hooks.get(column_spec.stirrup_key)
    z_clear_top = clear_top(doc, column, section)
    clear_m = (z_clear_top - section.z_bottom) * FT
    positions = spec.stirrup_positions(clear_m, column_spec.zones, column_spec.rest)
    half_b = section.b / 2.0 - cover - stirrup_d / 2.0
    half_h = section.h / 2.0 - cover - stirrup_d / 2.0
    for start_m, n, spacing_m in spec.group_runs(positions):
        z = section.z_bottom + start_m / FT
        corners = _counterclockwise(
            [
                section.point(-half_b, -half_h, z),
                section.point(half_b, -half_h, z),
                section.point(half_b, half_h, z),
                section.point(-half_b, half_h, z),
            ]
        )
        loop = List[DB.Curve](
            [DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]) for k in range(4)]
        )
        rebar = Rebar.CreateFromCurves(
            doc, RebarStyle.StirrupTie, stirrup_type, hook, hook, column, DB.XYZ.BasisZ,
            loop, RebarHookOrientation.Left, RebarHookOrientation.Left, True, True,
        )
        if n > 1:
            rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(
                n, spacing_m / FT, True, True, True
            )
        _tag(rebar, column)
        created.append((rebar, column_spec.stirrup_key, True))
    return created


def _centerline_points(rebar):
    """Points along the first bar of a rebar set, hooks included."""
    try:
        curves = rebar.GetCenterlineCurves(
            False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0
        )
    except Exception:
        return []
    points = []
    for curve in curves:
        points.extend(curve.Tessellate())
    return points


def record_weight(column, created):
    """Steel weight of the bars `generate_column` created (after a
    Regenerate), written to EA_Peso_Acero_kg. Returns (longitudinal_kg,
    stirrup_kg, number_of_bars, stirrups_stick_out)."""
    long_kg = 0.0
    stirrup_kg = 0.0
    bars = 0
    stick_out = False
    section = Section(column)
    inverse = section.transform.Inverse
    tol = 0.005  # ~1.5 mm
    for rebar, key, is_stirrup in created:
        kg = rebar.TotalLength * FT * spec.bar_weight_kg_per_m(key)
        if is_stirrup:
            stirrup_kg += kg
            if not stick_out:
                radius = spec.BAR_DIAMETERS_MM[key] / 304.8 / 2.0
                for point in _centerline_points(rebar):
                    p = inverse.OfPoint(point)
                    if (abs(p.X - section.center[0]) + radius > section.b / 2.0 + tol
                            or abs(p.Y - section.center[1]) + radius > section.h / 2.0 + tol):
                        stick_out = True
                        break
        else:
            long_kg += kg
        bars += rebar.Quantity
    p = column.LookupParameter(WEIGHT_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(round(long_kg + stirrup_kg, 2))
    return long_kg, stirrup_kg, bars, stick_out
