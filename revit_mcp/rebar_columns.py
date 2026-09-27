# -*- coding: UTF-8 -*-
"""
Column Rebar Module for Revit MCP ("Acero": columns)

Builds real Revit Rebar in concrete columns from a configuration stored
in the column type's parameters:

- EA_Estribo_Borde_Diametro / _Distribucion   perimeter stirrup, e.g. 3/8"
                                              and 1@.05, 10@.10, rto@.20
- EA_Estribo_Conf_Diametro / _Distribucion    inner (confinement) stirrups
                                              and crossties (grapas)
- EA_Recubrimiento_cm      e.g. 4
- EA_Nucleo_cm             stirrup spacing inside the beam-column joint
                           (blank = no stirrups in the joint)
- EA_Seccion_Armado        hand-drawn section (JSON, see rebar_spec): the
                           longitudinal bars, closed stirrups and grapas

The drawing is used as drawn (any section shape: rectangle, trapezoid,
L, T...). Each drawn stirrup/crosstie carries the family chosen when it
was sketched: edge ("borde") or confinement. A stirrup can also be open
(U-shaped, ends not joined, no hooks), as confinement stirrups often are.

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
    RebarShape,
    RebarStyle,
)
from System.Collections.Generic import List

import formwork_params as fw_params
import rebar_spec as spec
from utils import element_id_value

FT = 0.3048  # m per ft
GROUP_NAME = "Acero"
TYPE_PARAMS = (
    "EA_Estribo_Borde_Diametro",
    "EA_Estribo_Borde_Distribucion",
    "EA_Estribo_Conf_Diametro",
    "EA_Estribo_Conf_Distribucion",
    "EA_Recubrimiento_cm",
    "EA_Nucleo_cm",
    "EA_Seccion_Armado",
)
# Parameters of the first version: their values move to the new ones
# (None = dropped: longitudinal bars are now only drawn), then they are
# unbound.
OBSOLETE_TYPE_PARAMS = {
    "EA_Estribo_Diametro": "EA_Estribo_Borde_Diametro",
    "EA_Estribo_Distribucion": "EA_Estribo_Borde_Distribucion",
    "EA_Longitudinal": None,
}
EDGE, CONFINEMENT, LONGITUDINAL = "borde", "confinamiento", "longitudinal"
WEIGHT_PARAM = "EA_Peso_Acero_kg"
ORIGIN_PARAM = "EA_Origen_Id"
# Type parameters of the bar types (Structural Rebar), filled from the
# weight table (data/acero_pesos_por_diametro.csv).
BAR_DIAMETER_PARAM = u"DIAMETRO DE BARRA"
BAR_WEIGHT_PARAM = u"PESO NOMINAL (kg/m)"
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
    specs.append((BAR_DIAMETER_PARAM, True, [DB.BuiltInCategory.OST_Rebar], False))
    specs.append((BAR_WEIGHT_PARAM, False, [DB.BuiltInCategory.OST_Rebar], False))
    warnings = fw_params.ensure_parameters(doc, GROUP_NAME, specs)
    try:
        _migrate_obsolete(doc)
        fw_params.unbind_parameters(doc, list(OBSOLETE_TYPE_PARAMS))
    except Exception as e:
        warnings.append(u"No se pudieron migrar los parametros anteriores: {}".format(e))
    try:
        fill_bar_type_parameters(doc)
    except Exception as e:
        warnings.append(u"No se pudieron llenar el diametro y el peso de los tipos de barra: {}".format(e))
    return warnings


def bar_type_diameter(bar_type):
    """Diameter key of a bar type: the one in its name (Ø5/8"), else the
    one of its nominal diameter; None if neither is a known bar."""
    key = spec.diameter_from_name(element_name(bar_type))
    if key is None:
        d = getattr(bar_type, "BarNominalDiameter", None) or bar_type.BarDiameter
        key = spec.nearest_diameter(d * FT * 1000.0)
    return key


def fill_bar_type_parameters(doc):
    """Write DIAMETRO DE BARRA and PESO NOMINAL (kg/m) on every bar type
    from the weight table. Inside an active Transaction. Returns the names
    of the bar types left blank (a diameter the table doesn't know)."""
    unknown = []
    for bar_type in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
        key = bar_type_diameter(bar_type)
        p_d = bar_type.LookupParameter(BAR_DIAMETER_PARAM)
        p_w = bar_type.LookupParameter(BAR_WEIGHT_PARAM)
        if key is None:
            unknown.append(element_name(bar_type))
            continue
        if p_d is not None and not p_d.IsReadOnly and p_d.AsString() != key:
            p_d.Set(key)
        weight = round(spec.bar_weight_kg_per_m(key), 3)
        if p_w is not None and not p_w.IsReadOnly and abs(p_w.AsDouble() - weight) > 1e-6:
            p_w.Set(weight)
    return unknown


def _migrate_obsolete(doc):
    types = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsElementType()
    )
    for column_type in types:
        for old, new in OBSOLETE_TYPE_PARAMS.items():
            if new is None:
                continue
            p_old = column_type.LookupParameter(old)
            p_new = column_type.LookupParameter(new)
            if p_old is None or p_new is None or p_new.IsReadOnly:
                continue
            if (p_old.AsString() or u"") and not (p_new.AsString() or u""):
                p_new.Set(p_old.AsString())


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


class StirrupFamily(object):
    """Diameter and distribution of one kind of stirrup."""

    def __init__(self, diameter_text, distribution_text, label):
        try:
            self.key = spec.parse_diameter(diameter_text)
            self.zones, self.rest = spec.parse_distribution(distribution_text)
        except spec.SpecError as e:
            raise spec.SpecError(u"{}: {}".format(label, e))


class ColumnSpec(object):
    """Parsed configuration of one column type (raises spec.SpecError).
    `require_design`: False to check only the stirrup settings (saving a
    configuration before the section is drawn)."""

    def __init__(self, config, require_design=True):
        self.edge = StirrupFamily(
            config.get("EA_Estribo_Borde_Diametro"),
            config.get("EA_Estribo_Borde_Distribucion"),
            u"Estribo de borde",
        )
        conf_d = (config.get("EA_Estribo_Conf_Diametro") or u"").strip()
        conf_dist = (config.get("EA_Estribo_Conf_Distribucion") or u"").strip()
        self.confinement = (
            StirrupFamily(conf_d, conf_dist, u"Estribo de confinamiento")
            if conf_dist else None
        )
        self.cover_m = _float_cm(config.get("EA_Recubrimiento_cm") or u"", u"Recubrimiento", 1, 10)
        nucleus = (config.get("EA_Nucleo_cm") or u"").strip()
        self.joint_spacing_m = _float_cm(nucleus, u"Espaciamiento en nucleo", 3, 30) if nucleus else None
        self.design = spec.design_from_text(config.get("EA_Seccion_Armado"))
        if not require_design:
            return
        if not (self.design and self.design["bars"]):
            raise spec.SpecError(u"dibuja las barras longitudinales de la seccion (o usa Automatico)")
        if not self.design["stirrups"]:
            raise spec.SpecError(u"dibuja al menos el estribo de borde")
        kinds = [st[0] for st in self.design["stirrups"]] + [t[0] for t in self.design["ties"]]
        if spec.KIND_CONFINEMENT in kinds and self.confinement is None:
            raise spec.SpecError(
                u"el dibujo tiene estribos o grapas de confinamiento: falta su diametro y distribucion"
            )

    def family_of(self, kind):
        """(kind, StirrupFamily) of a drawn stirrup or tie."""
        if kind == spec.KIND_CONFINEMENT:
            return CONFINEMENT, self.confinement
        return EDGE, self.edge


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


def stirrup_hook(doc, diameter_key, angle_deg=135.0):
    """The stirrup hook (135 degrees unless said otherwise) for a bar
    diameter: one named for it ('Estribo 3/8" - 135'), else the seismic one
    (its length scales with the bar diameter), else any hook of that angle;
    None if there is none. A hook sized for a bigger bar would cross a small
    section and stick out of it (a 3/8" hook on a 1/4" tie in a 14 cm
    columneta)."""
    ranked = []
    for h in DB.FilteredElementCollector(doc).OfClass(RebarHookType):
        if abs(h.HookAngle * 57.29578 - angle_deg) > 1.0:
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

    def get(self, diameter_key, angle_deg=135.0):
        key = (diameter_key, round(angle_deg))
        if key not in self.cache:
            self.cache[key] = stirrup_hook(self.doc, diameter_key, angle_deg)
        return self.cache[key]


class RebarShapes(object):
    """The project's rebar shapes by name, for the stirrups and ties drawn
    with a chosen shape. `mismatched` collects (column id, shape name) where
    Revit refused the drawing for that shape and picked one itself."""

    def __init__(self, doc):
        self.by_name = dict(
            (element_name(s), s) for s in DB.FilteredElementCollector(doc).OfClass(RebarShape)
        )
        self.mismatched = []

    def get(self, name):
        return self.by_name.get(name) if name else None


def _hook_angle(shape, end):
    try:
        return shape.GetDefaultHookAngle(end)
    except Exception:
        return 0


def _create_stirrup(doc, shapes, shape_name, column, bar_type, hook, hooks, key, curves,
                    orient_start, orient_end):
    """A stirrup or tie with the shape drawn for it (its hooks those the
    shape asks for); without one, or if Revit refuses the drawing for that
    shape, the shape Revit matches to the curves (with `hook` at both ends)."""
    shape = shapes.get(shape_name) if shapes is not None else None
    if shape is not None:
        ends = [hooks.get(key, _hook_angle(shape, e)) if _hook_angle(shape, e) else None
                for e in (0, 1)]
        try:
            rebar = Rebar.CreateFromCurvesAndShape(
                doc, shape, bar_type, ends[0], ends[1], column, DB.XYZ.BasisZ, curves,
                orient_start, orient_end,
            )
        except Exception:
            rebar = None
        if rebar is not None:  # None too when the curves don't fit the shape
            return rebar
        shapes.mismatched.append((element_id_value(column.Id), shape_name))
    return Rebar.CreateFromCurves(
        doc, RebarStyle.StirrupTie, bar_type, hook, hook, column, DB.XYZ.BasisZ,
        curves, orient_start, orient_end, True, True,
    )


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
    """(Rule agreed with the user, see Acero.pushbutton/REGLAS_ACERO.md.)
    Top of the column's clear height, where its stirrup distribution
    ends: the underside of the deepest beam framing into its top (where
    beams of different depth meet, always the deepest one), else the
    underside of the slab over it, else the column top. From there up to
    the column top is the joint."""
    bb = column.get_BoundingBox(None)
    if bb is None:
        return section.z_top
    reach = 0.05
    outline = DB.Outline(
        DB.XYZ(bb.Min.X - reach, bb.Min.Y - reach, section.z_top - TOP_FRAMING_REACH_FT),
        DB.XYZ(bb.Max.X + reach, bb.Max.Y + reach, section.z_top + 0.01),
    )
    mid = (section.z_bottom + section.z_top) / 2.0
    # Measured where each element meets the column - its solids cut to the
    # column's section grown by 5 cm (beams and slabs are cut back by the
    # column) -, not on its bounding box: a sloped or stepped beam's box
    # reaches far below the column top.
    xs = [p[0] for p in section.polygon_m]
    ys = [p[1] for p in section.polygon_m]
    grow = 0.05
    corners = [section.point_m(x, y, mid) for x, y in (
        (min(xs) - grow, min(ys) - grow), (max(xs) + grow, min(ys) - grow),
        (max(xs) + grow, max(ys) + grow), (min(xs) - grow, max(ys) + grow))]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
    around = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, section.z_top + 0.01 - mid)
    options = DB.Options()

    def underside(element):
        low, failed = None, False
        for solid in _solids(element.get_Geometry(options)):
            try:
                part = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                    solid, around, DB.BooleanOperationsType.Intersect)
            except Exception:
                failed = True
                continue
            if part is None or part.Volume < 1e-6:
                continue
            for edge in part.Edges:
                for p in edge.Tessellate():
                    low = p.Z if low is None else min(low, p.Z)
        if low is None and failed:
            ebb = element.get_BoundingBox(None)
            low = ebb.Min.Z if ebb is not None else None
        return low

    for bic in (DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_Floors):
        best = section.z_top  # the lowest underside: the deepest member
        found = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
        )
        for e in found:
            z = underside(e)
            if z is not None and mid < z < best:
                best = z
        if best < section.z_top - 1e-3:
            return best
    return section.z_top


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


def _runs(family, joint_spacing_m, section, z_clear_top):
    """(z_start_ft, count, spacing_ft, side) runs of one stirrup family: its
    clear-height distribution, one rebar set per zone and end as written
    (spec.stirrup_sets: '1@.05' a single stirrup, '5@.10' a set of 5 at
    0.10, the rest one set in the middle), plus the joint when EA_Nucleo_cm
    is set. side is -1 for the top end zones: the way stirrups set at one
    height stack (towards the middle, so the first one keeps its distance
    from each end)."""
    clear_m = (z_clear_top - section.z_bottom) * FT
    runs = [
        (section.z_bottom + start / FT, n, spacing / FT, side)
        for start, n, spacing, _, side in spec.stirrup_sets(clear_m, family.zones, family.rest)
    ]
    if joint_spacing_m and section.z_top - z_clear_top > 0.1 / FT:
        joint = spec.joint_positions((section.z_top - z_clear_top) * FT, joint_spacing_m)
        for start, n, spacing in spec.group_runs(joint):
            runs.append((z_clear_top + start / FT, n, spacing / FT, 1))
    return runs


def _set(rebar, n, spacing_ft):
    if n > 1:
        rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(
            n, spacing_ft, True, True, True
        )


def generate_column(doc, column, column_spec, bar_types, hooks, mark, shapes=None):
    """Create the column's longitudinal bars, stirrups and ties from its
    drawing (inside an active Transaction; Revit needs a Regenerate before
    their lengths are known, see `record_weight`). Perimeter stirrups use
    the edge ("borde") settings; inner stirrups and ties the confinement
    ones; each takes the rebar shape it was drawn with (`shapes`, a
    RebarShapes) when it has one. Returns [(rebar, diameter_key, kind)]."""
    section = Section(column)
    design = column_spec.design

    def bar_type_for(key):
        bar_type = bar_types.pick(key, mark)
        if bar_type is None:
            raise spec.SpecError(u"no hay tipo de barra de {}".format(key))
        return bar_type

    delete_generated(doc, column)
    created = []

    normal = section.transform.BasisX
    for x, y, key in design["bars"]:
        line = DB.Line.CreateBound(
            section.point_m(x, y, section.z_bottom), section.point_m(x, y, section.z_top)
        )
        rebar = Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, bar_type_for(key), None, None, column, normal,
            List[DB.Curve]([line]),
            RebarHookOrientation.Right, RebarHookOrientation.Right, True, True,
        )
        _tag(rebar, column)
        created.append((rebar, key, LONGITUDINAL))

    z_clear_top = clear_top(doc, column, section)
    # (kind, family, [loop centerlines], [tie centerlines]) per family
    groups = {}
    stirrup_shapes = spec.design_shapes(design, "stirrups")
    for (drawn_kind, poly, wrap, is_open), shape_name in zip(design["stirrups"], stirrup_shapes):
        kind, family = column_spec.family_of(drawn_kind)
        entry = groups.setdefault(kind, (family, [], []))
        line = spec.stirrup_centerline(poly, design["bars"], family.key, wrap, is_open)
        entry[1].append((spec.clean_polyline(line, closed=not is_open), is_open, shape_name))
    for (drawn_kind, a, b), shape_name in zip(design["ties"], spec.design_shapes(design, "ties")):
        kind, family = column_spec.family_of(drawn_kind)
        entry = groups.setdefault(kind, (family, [], []))
        a2, b2 = spec.tie_centerline(a, b, design["bars"], family.key)
        entry[2].append((a2, b2, shape_name))

    # Stirrups and ties set at the same height lie stacked, one bar
    # diameter apart, like on site: side by side, never through each other.
    flat = [(kind, i, is_tie) for kind, (family, loops, ties) in groups.items()
            for is_tie, items in ((False, loops), (True, ties)) for i in range(len(items))]
    lifts = spec.stack_lifts([
        (kind, spec.BAR_DIAMETERS_MM[groups[kind][0].key] / 1000.0, is_tie) for kind, i, is_tie in flat])
    lift_ft = dict(((kind, i, is_tie), lift / FT) for (kind, i, is_tie), lift in zip(flat, lifts))

    for kind, (family, loops, ties) in groups.items():
        stirrup_type = bar_type_for(family.key)
        hook = hooks.get(family.key)
        for z_set, n, spacing, side in _runs(family, column_spec.joint_spacing_m, section, z_clear_top):
            for index, (line, is_open, shape_name) in enumerate(loops):
                z = z_set + side * lift_ft[(kind, index, False)]
                if is_open:
                    # U-shaped stirrup: its drawn segments, ends left
                    # straight (no hooks) unless its shape has them.
                    pts = [section.point_m(x, y, z) for x, y in line]
                    curves = List[DB.Curve](
                        [DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)]
                    )
                    rebar = _create_stirrup(
                        doc, shapes, shape_name, column, stirrup_type, None, hooks, family.key,
                        curves, RebarHookOrientation.Left, RebarHookOrientation.Left,
                    )
                else:
                    # Counterclockwise seen from above, so Left hooks turn
                    # inward (also on mirrored instances, whose frame flips).
                    pts = _counterclockwise([section.point_m(x, y, z) for x, y in line])
                    loop = List[DB.Curve](
                        [DB.Line.CreateBound(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts))]
                    )
                    rebar = _create_stirrup(
                        doc, shapes, shape_name, column, stirrup_type, hook, hooks, family.key,
                        loop, RebarHookOrientation.Left, RebarHookOrientation.Left,
                    )
                _set(rebar, n, spacing)
                _tag(rebar, column)
                created.append((rebar, family.key, kind))
            for index, (a, b, shape_name) in enumerate(ties):
                z = z_set + side * lift_ft[(kind, index, True)]
                line = DB.Line.CreateBound(
                    section.point_m(a[0], a[1], z), section.point_m(b[0], b[1], z)
                )
                rebar = _create_stirrup(
                    doc, shapes, shape_name, column, stirrup_type, hook, hooks, family.key,
                    List[DB.Curve]([line]), RebarHookOrientation.Left, RebarHookOrientation.Right,
                )
                _set(rebar, n, spacing)
                _tag(rebar, column)
                created.append((rebar, family.key, kind))
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
    Regenerate), written to EA_Peso_Acero_kg. Returns ({kind: kg},
    number_of_bars, stirrups_stick_out)."""
    kg_by_kind = {LONGITUDINAL: 0.0, EDGE: 0.0, CONFINEMENT: 0.0}
    bars = 0
    stick_out = False
    section = Section(column)
    inverse = section.transform.Inverse
    tol = 0.0015  # m
    for rebar, key, kind in created:
        kg_by_kind[kind] += rebar.TotalLength * FT * spec.bar_weight_kg_per_m(key)
        bars += rebar.Quantity
        if kind == LONGITUDINAL or stick_out:
            continue
        radius = spec.BAR_DIAMETERS_MM[key] / 2000.0
        for point in _centerline_points(rebar):
            p = inverse.OfPoint(point)
            local = ((p.X - section.center[0]) * FT, (p.Y - section.center[1]) * FT)
            if (not spec.point_in_polygon(local, section.polygon_m)
                    or spec.distance_to_polygon(local, section.polygon_m) < radius - tol):
                stick_out = True
                break
    p = column.LookupParameter(WEIGHT_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(round(sum(kg_by_kind.values()), 2))
    return kg_by_kind, bars, stick_out


# --- Elements around the column, for the elevation and 3D views ---------------

# Only what frames into the column: beams, slabs and its footing.
NEIGHBOR_CATEGORIES = (
    (DB.BuiltInCategory.OST_StructuralFraming, u"VIGA"),
    (DB.BuiltInCategory.OST_Floors, u"LOSA"),
    (DB.BuiltInCategory.OST_StructuralFoundation, u"ZAPATA"),
)


def _box_triangles(box):
    """The 12 triangles of an axis-aligned box (x0, y0, z0, x1, y1, z1)."""
    x0, y0, z0, x1, y1, z1 = box
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
    tris = []
    for a, b, d, e in quads:
        tris.append((c[a], c[b], c[d]))
        tris.append((c[a], c[d], c[e]))
    return tris


def column_neighbors(doc, column, section, reach_m=0.6, contact_m=0.05):
    """Elements touching the column (within `contact_m`: a beam cut back by
    its join with the column still counts) - beams, slabs and its footing -
    cut to `reach_m` around it,
    in the column's local frame (meters; x/y from the section center, z
    from the column base). Returns [{"label", "triangles": [(p, p, p)],
    "box": (x0, y0, z0, x1, y1, z1)}]."""
    bb = column.get_BoundingBox(None)
    if bb is None:
        return []
    touch = contact_m / FT
    near = DB.Outline(
        DB.XYZ(bb.Min.X - touch, bb.Min.Y - touch, bb.Min.Z - touch),
        DB.XYZ(bb.Max.X + touch, bb.Max.Y + touch, bb.Max.Z + touch),
    )
    # Crop box: the section's bounding box grown by reach_m, from reach_m
    # under the base to reach_m over the top, in the column's own frame.
    xs = [p[0] for p in section.polygon_m]
    ys = [p[1] for p in section.polygon_m]
    x0, x1 = min(xs) - reach_m, max(xs) + reach_m
    y0, y1 = min(ys) - reach_m, max(ys) + reach_m
    z0 = section.z_bottom - reach_m / FT
    corners = [section.point_m(x, y, z0) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
    crop = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ,
        (section.z_top - section.z_bottom) + 2 * reach_m / FT,
    )
    inverse = section.transform.Inverse

    def local(p):
        q = inverse.OfPoint(p)
        return ((q.X - section.center[0]) * FT, (q.Y - section.center[1]) * FT,
                (p.Z - section.z_bottom) * FT)

    result = []
    options = DB.Options()
    for bic, label in NEIGHBOR_CATEGORIES:
        found = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .WherePasses(DB.BoundingBoxIntersectsFilter(near))
        )
        for element in found:
            if element.Id == column.Id:
                continue
            triangles = []
            failed = False
            for solid in _solids(element.get_Geometry(options)):
                try:
                    part = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                        solid, crop, DB.BooleanOperationsType.Intersect)
                except Exception:
                    failed = True
                    continue
                if part is None or part.Volume < 1e-6:
                    continue
                for face in part.Faces:
                    mesh = face.Triangulate()
                    for i in range(mesh.NumTriangles):
                        tri = mesh.get_Triangle(i)
                        triangles.append(tuple(local(tri.get_Vertex(k)) for k in range(3)))
            if not triangles and failed:
                # Revit couldn't cut it: show its bounding box, cut to the
                # same zone, rather than leaving it out.
                ebb = element.get_BoundingBox(None)
                if ebb is not None:
                    pts = [local(DB.XYZ(x, y, z)) for x in (ebb.Min.X, ebb.Max.X)
                           for y in (ebb.Min.Y, ebb.Max.Y) for z in (ebb.Min.Z, ebb.Max.Z)]
                    height = (section.z_top - section.z_bottom) * FT
                    box = (max(min(q[0] for q in pts), x0), max(min(q[1] for q in pts), y0),
                           max(min(q[2] for q in pts), -reach_m),
                           min(max(q[0] for q in pts), x1), min(max(q[1] for q in pts), y1),
                           min(max(q[2] for q in pts), height + reach_m))
                    if box[0] < box[3] and box[1] < box[4] and box[2] < box[5]:
                        triangles = _box_triangles(box)
            if not triangles:
                continue
            pts = [p for tri in triangles for p in tri]
            box = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
                   max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
            result.append({"label": label, "triangles": triangles, "box": box})
    return result
