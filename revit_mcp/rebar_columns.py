# -*- coding: UTF-8 -*-
"""
Column Rebar Module for Revit MCP ("Acero": columns)

Builds real Revit Rebar in concrete columns from a configuration stored
in the column type's parameters:

- EA_Longitudinal          e.g. 8Ø5/8"  or  4Ø3/4" + 4Ø5/8"
- EA_Estribo_Diametro      e.g. 3/8"
- EA_Estribo_Distribucion  e.g. 1@.05, 10@.10, rto@.20  (or 1@5 6@10 Rto@25)
- EA_Recubrimiento_cm      e.g. 4
- EA_Nucleo_cm             stirrup spacing inside the beam-column joint
                           (blank = no stirrups in the joint)
- EA_Seccion_Armado        hand-drawn section (JSON, see rebar_spec): bars,
                           closed stirrups and crossties (grapas)

With a drawing, its bars/stirrups/ties are used as drawn (any section
shape: rectangle, trapezoid, L, T...). Without one, a rectangular section
gets the automatic layout from EA_Longitudinal and one perimeter stirrup.

Longitudinal bars run the column's full height (level to level; laps and
anchorages are not modeled). Stirrups and ties are laid out from both ends
over the clear height, up to the underside of the deepest beam/slab
framing into the column top; with EA_Nucleo_cm they also continue through
that joint. Each column gets its steel weight in EA_Peso_Acero_kg; every
bar carries its column's id in EA_Origen_Id so a new run replaces it.

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
    "EA_Nucleo_cm",
    "EA_Seccion_Armado",
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


def type_mark(type_name):
    """'C-2' / 'CC-1' out of a column type name ('C-2_0.23x0.60m',
    '..._CC-1_0.14x0.20m'), or None."""
    m = re.search(r"(?<![A-Z0-9])(C{1,2}-\d+)", (type_name or u"").upper())
    return m.group(1) if m else None


def default_cover_cm(type_name):
    """Cover suggested for a column type: 2.5 cm for columnetas, else 4."""
    name = (type_name or u"").upper()
    if u"COLUMNETA" in name or (type_mark(type_name) or u"").startswith(u"CC-"):
        return CONFINEMENT_COVER_CM
    return DEFAULT_COVER_CM


def read_type_config(column_type):
    """{param name: text} of a column type; blanks when unset/unbound."""
    config = {}
    for name in TYPE_PARAMS:
        p = column_type.LookupParameter(name)
        config[name] = (p.AsString() or u"") if p is not None else u""
    return config


def write_type_config(column_type, config):
    for name in TYPE_PARAMS:
        if name not in config:
            continue
        p = column_type.LookupParameter(name)
        if p is not None and not p.IsReadOnly:
            p.Set(config[name] or u"")


def _float_cm(text, label, lo, hi):
    try:
        value = float(u"{}".format(text).replace(u",", u"."))
    except ValueError:
        raise spec.SpecError(u'{} invalido: "{}" (en cm)'.format(label, text))
    if not lo <= value <= hi:
        raise spec.SpecError(u"{} fuera de rango ({} a {} cm)".format(label, lo, hi))
    return value / 100.0


class ColumnSpec(object):
    """Parsed configuration of one column type (raises spec.SpecError)."""

    def __init__(self, config):
        self.stirrup_key = spec.parse_diameter(config.get("EA_Estribo_Diametro"))
        self.zones, self.rest = spec.parse_distribution(config.get("EA_Estribo_Distribucion"))
        self.cover_m = _float_cm(config.get("EA_Recubrimiento_cm") or u"", u"Recubrimiento", 1, 10)
        nucleus = (config.get("EA_Nucleo_cm") or u"").strip()
        self.joint_spacing_m = _float_cm(nucleus, u"Espaciamiento en nucleo", 3, 30) if nucleus else None
        self.design = spec.design_from_text(config.get("EA_Seccion_Armado"))
        longitudinal = (config.get("EA_Longitudinal") or u"").strip()
        self.groups = spec.parse_longitudinal(longitudinal) if longitudinal else None
        if not self.groups and not (self.design and self.design["bars"]):
            raise spec.SpecError(u"Falta el acero longitudinal (texto o barras dibujadas)")

    def design_for(self, section):
        """The drawing, or the automatic layout of a rectangular section."""
        if self.design and self.design["bars"]:
            return self.design
        if not section.is_rectangle:
            raise spec.SpecError(u"seccion no rectangular: dibuja el armado")
        return spec.auto_design(
            section.b * FT, section.h * FT, self.cover_m, self.stirrup_key, self.groups
        )


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
    """Cross-section of a vertical prismatic column, from its original
    (uncut) family geometry. Local frame: x/y of the family, origin at the
    section's bounding-box center; `polygon` is the outline in meters.
    Lengths on the object are in feet unless named `_m`."""

    def __init__(self, column):
        t = column.GetTransform()
        if abs(t.BasisZ.Z) < 0.999:
            raise spec.SpecError(u"columna inclinada (no soportada)")
        solids = list(_solids(column.GetOriginalGeometry(DB.Options())))
        if len(solids) != 1:
            raise spec.SpecError(u"geometria de {} solidos (no soportada)".format(len(solids)))
        solid = solids[0]
        bottom = None
        z_top = None
        for face in solid.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            if face.FaceNormal.Z < -0.999 and (bottom is None or face.Area > bottom.Area):
                bottom = face
            elif face.FaceNormal.Z > 0.999:
                z_top = face.Origin.Z if z_top is None else max(z_top, face.Origin.Z)
        if bottom is None or z_top is None:
            raise spec.SpecError(u"sin cara inferior/superior horizontal")
        loops = list(bottom.GetEdgesAsCurveLoops())
        if len(loops) != 1:
            raise spec.SpecError(u"seccion con huecos (no soportada)")
        outline = []
        for curve in loops[0]:
            if not isinstance(curve, DB.Line):
                raise spec.SpecError(u"seccion con bordes curvos (no soportada)")
            outline.append(curve.GetEndPoint(0))
        z_bottom = bottom.Origin.Z
        height = z_top - z_bottom
        xs = [p.X for p in outline]
        ys = [p.Y for p in outline]
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        polygon_ft = [(p.X - cx, p.Y - cy) for p in outline]
        area = abs(spec.polygon_signed_area(polygon_ft))
        if height <= 0 or abs(solid.Volume - area * height) > 0.02 * solid.Volume:
            raise spec.SpecError(u"la seccion cambia con la altura (no soportada)")

        self.transform = t
        self.center = (cx, cy)
        self.polygon_m = [(x * FT, y * FT) for x, y in polygon_ft]
        self.b = max(xs) - min(xs)
        self.h = max(ys) - min(ys)
        self.is_rectangle = len(polygon_ft) == 4 and abs(area - self.b * self.h) < 1e-4 * self.b * self.h + 1e-6
        self.z_bottom = t.OfPoint(DB.XYZ(cx, cy, z_bottom)).Z
        self.z_top = t.OfPoint(DB.XYZ(cx, cy, z_top)).Z

    def point_m(self, x_m, y_m, z_world):
        """World point at a local section offset (meters, from the section
        center) and world elevation z (ft)."""
        p = self.transform.OfPoint(
            DB.XYZ(self.center[0] + x_m / FT, self.center[1] + y_m / FT, 0.0)
        )
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


def _runs(column_spec, section, z_clear_top):
    """(z_start_ft, count, spacing_ft) runs of stirrups/ties: the clear
    height distribution, plus the joint when EA_Nucleo_cm is set."""
    clear_m = (z_clear_top - section.z_bottom) * FT
    runs = [
        (section.z_bottom + start / FT, n, spacing / FT)
        for start, n, spacing in spec.group_runs(
            spec.stirrup_positions(clear_m, column_spec.zones, column_spec.rest)
        )
    ]
    if column_spec.joint_spacing_m and section.z_top - z_clear_top > 0.1 / FT:
        joint = spec.joint_positions(
            (section.z_top - z_clear_top) * FT, column_spec.joint_spacing_m
        )
        for start, n, spacing in spec.group_runs(joint):
            runs.append((z_clear_top + start / FT, n, spacing / FT))
    return runs


def _set(doc, rebar, n, spacing_ft):
    if n > 1:
        rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(
            n, spacing_ft, True, True, True
        )


def generate_column(doc, column, column_spec, bar_types, hooks, mark):
    """Create the column's longitudinal bars, stirrups and ties (inside an
    active Transaction; Revit needs a Regenerate before their lengths are
    known, see `record_weight`). Returns [(rebar, diameter_key,
    is_stirrup)]."""
    section = Section(column)
    design = column_spec.design_for(section)
    stirrup_key = column_spec.stirrup_key
    stirrup_type = bar_types.pick(stirrup_key, mark)
    if stirrup_type is None:
        raise spec.SpecError(u"no hay tipo de barra de {}".format(stirrup_key))
    hook = hooks.get(stirrup_key)

    delete_generated(doc, column)
    created = []

    normal = section.transform.BasisX
    for x, y, key in design["bars"]:
        bar_type = bar_types.pick(key, mark)
        if bar_type is None:
            raise spec.SpecError(u"no hay tipo de barra de {}".format(key))
        line = DB.Line.CreateBound(
            section.point_m(x, y, section.z_bottom), section.point_m(x, y, section.z_top)
        )
        rebar = Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, bar_type, None, None, column, normal,
            List[DB.Curve]([line]),
            RebarHookOrientation.Right, RebarHookOrientation.Right, True, True,
        )
        _tag(rebar, column)
        created.append((rebar, key, False))

    runs = _runs(column_spec, section, clear_top(doc, column, section))
    stirrup_lines = [
        spec.stirrup_centerline(poly, design["bars"], stirrup_key)
        for poly in design["stirrups"]
    ]
    tie_lines = [
        spec.tie_centerline(a, b, design["bars"], stirrup_key) for a, b in design["ties"]
    ]
    for z, n, spacing in runs:
        for line in stirrup_lines:
            # Counterclockwise seen from above, so Left hooks turn inward
            # (also on mirrored instances, whose local frame is flipped).
            pts = [section.point_m(x, y, z) for x, y in line]
            pts = _counterclockwise(pts)
            loop = List[DB.Curve](
                [DB.Line.CreateBound(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts))]
            )
            rebar = Rebar.CreateFromCurves(
                doc, RebarStyle.StirrupTie, stirrup_type, hook, hook, column, DB.XYZ.BasisZ,
                loop, RebarHookOrientation.Left, RebarHookOrientation.Left, True, True,
            )
            _set(doc, rebar, n, spacing)
            _tag(rebar, column)
            created.append((rebar, stirrup_key, True))
        for a, b in tie_lines:
            line = DB.Line.CreateBound(section.point_m(a[0], a[1], z), section.point_m(b[0], b[1], z))
            rebar = Rebar.CreateFromCurves(
                doc, RebarStyle.StirrupTie, stirrup_type, hook, hook, column, DB.XYZ.BasisZ,
                List[DB.Curve]([line]), RebarHookOrientation.Left, RebarHookOrientation.Right,
                True, True,
            )
            _set(doc, rebar, n, spacing)
            _tag(rebar, column)
            created.append((rebar, stirrup_key, True))
    return created


def _counterclockwise(points):
    area = 0.0
    for k in range(len(points)):
        a, b = points[k], points[(k + 1) % len(points)]
        area += a.X * b.Y - b.X * a.Y
    return points if area > 0 else list(reversed(points))


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
    tol = 0.0015  # m
    for rebar, key, is_stirrup in created:
        kg = rebar.TotalLength * FT * spec.bar_weight_kg_per_m(key)
        if is_stirrup:
            stirrup_kg += kg
            if not stick_out:
                radius = spec.BAR_DIAMETERS_MM[key] / 2000.0
                for point in _centerline_points(rebar):
                    p = inverse.OfPoint(point)
                    local = ((p.X - section.center[0]) * FT, (p.Y - section.center[1]) * FT)
                    inside = spec.point_in_polygon(local, section.polygon_m)
                    if not inside or spec.distance_to_polygon(local, section.polygon_m) < radius - tol:
                        stick_out = True
                        break
        else:
            long_kg += kg
        bars += rebar.Quantity
    p = column.LookupParameter(WEIGHT_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(round(long_kg + stirrup_kg, 2))
    return long_kg, stirrup_kg, bars, stick_out
