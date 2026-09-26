# -*- coding: utf-8 -*-
"""
Generar Encofrado - Dynamo Python Script node
Automated structural formwork (encofrado) generation, standalone version
for Dynamo (CPython3 engine). Self-contained: no dependency on pyRevit or
this repo's `revit_mcp` package.

Creates one DirectShape panel (Generic Models) per free face of every
foundation, wall, structural column, beam and floor in the active model,
skipping faces that are in contact with a neighboring structural element
(so a beam framing into a column doesn't get double-thickness formwork
at the joint) and, optionally, the top pour face and foundation
undersides that rest on ground/blinding.

Panel thickness defaults to 2" per project requirement.
Edit the CONFIGURATION block below to change categories/behaviour.
"""

import clr
import System

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveLoop,
    DirectShape,
    Element,
    ElementId,
    FilteredElementCollector,
    GeometryCreationUtilities,
    GeometryInstance,
    GeometryObject,
    Line,
    Options,
    PlanarFace,
    Solid,
    SolidCurveIntersectionOptions,
    UV,
    ViewDetailLevel,
    XYZ,
)

clr.AddReference('RevitServices')
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

from System.Collections.Generic import List

doc = DocumentManager.Instance.CurrentDBDocument

# ---------------------------------------------------------------------
# CONFIGURATION - edit these to change behaviour, then run the node
# ---------------------------------------------------------------------
PANEL_THICKNESS_IN = 2.0          # formwork thickness, inches (project requirement)
CONTACT_TOLERANCE_MM = 5.0        # gap treated as "touching" between elements
EXCLUDE_TOP_FACES = True          # skip the open top face (pour surface)
EXCLUDE_FOUNDATION_BOTTOM = True  # skip foundation faces resting on ground/blinding
CATEGORIES = ["foundations", "walls", "columns", "beams", "slabs"]
CREATE_GEOMETRY = True            # False = quantity takeoff only, no panels created
# ---------------------------------------------------------------------

IN_TO_FT = 1.0 / 12.0
MM_TO_FT = 1.0 / 304.8
FT2_TO_M2 = 0.09290304

PANEL_THICKNESS_FT = PANEL_THICKNESS_IN * IN_TO_FT
CONTACT_TOLERANCE_FT = CONTACT_TOLERANCE_MM * MM_TO_FT

TOP_NORMAL_Z = 0.98      # cos(~11.5deg) - near-vertical-up normals count as "top"
BOTTOM_NORMAL_Z = -0.98
DEFAULT_CONTACT_GRID_FT = 0.25
MAX_GRID_STEPS = 80

CATEGORY_MAP = {
    "foundations": {
        "bic": BuiltInCategory.OST_StructuralFoundation,
        "label": "Cimentacion",
        "rests_on_ground": True,
    },
    "walls": {
        "bic": BuiltInCategory.OST_Walls,
        "label": "Muros",
        "rests_on_ground": False,
    },
    "columns": {
        "bic": BuiltInCategory.OST_StructuralColumns,
        "label": "Columnas",
        "rests_on_ground": False,
    },
    "beams": {
        "bic": BuiltInCategory.OST_StructuralFraming,
        "label": "Vigas",
        "rests_on_ground": False,
    },
    "slabs": {
        "bic": BuiltInCategory.OST_Floors,
        "label": "Losas",
        "rests_on_ground": False,
    },
}


# ----------------------------- helpers --------------------------------

def make_element_id(value):
    """ElementId(int) is ambiguous on Revit 2027+ (added ElementId(BuiltInParameter)
    / ElementId(BuiltInCategory) overloads). System.Int64 forces the right one."""
    try:
        return ElementId(System.Int64(value))
    except Exception:
        return ElementId(int(value))


def element_id_value(element_id):
    """Revit 2024+ uses .Value (int64); Revit 2026 removed .IntegerValue."""
    try:
        return int(element_id.Value)
    except AttributeError:
        return int(element_id.IntegerValue)


_GEOM_OPTIONS = None


def geometry_options():
    global _GEOM_OPTIONS
    if _GEOM_OPTIONS is None:
        opts = Options()
        opts.ComputeReferences = True
        opts.DetailLevel = ViewDetailLevel.Fine
        opts.IncludeNonVisibleObjects = False
        _GEOM_OPTIONS = opts
    return _GEOM_OPTIONS


def collect_solids(geometry_element, solids):
    for obj in geometry_element:
        if isinstance(obj, Solid):
            try:
                if obj.Volume > 1e-9 and obj.Faces.Size > 0:
                    solids.append(obj)
            except Exception:
                continue
        elif isinstance(obj, GeometryInstance):
            try:
                collect_solids(obj.GetInstanceGeometry(), solids)
            except Exception:
                continue


def get_element_solids(element):
    solids = []
    try:
        geom = element.get_Geometry(geometry_options())
    except Exception:
        return solids
    if geom is None:
        return solids
    collect_solids(geom, solids)
    return solids


def get_mark_or_name(element):
    try:
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p and p.HasValue:
            val = (p.AsString() or "").strip()
            if val:
                return val
    except Exception:
        pass
    try:
        return element.Name
    except Exception:
        return "Sin nombre"


class Candidate(object):
    def __init__(self, element, category_key, bbox):
        self.element = element
        self.category_key = category_key
        self.bbox = bbox
        self._solids = None

    def get_solids(self):
        if self._solids is None:
            self._solids = get_element_solids(self.element)
        return self._solids


def bbox_overlaps(bbox_a, bbox_b, tol_ft):
    if bbox_a is None or bbox_b is None:
        return False
    return (
        bbox_a.Min.X - tol_ft <= bbox_b.Max.X
        and bbox_a.Max.X + tol_ft >= bbox_b.Min.X
        and bbox_a.Min.Y - tol_ft <= bbox_b.Max.Y
        and bbox_a.Max.Y + tol_ft >= bbox_b.Min.Y
        and bbox_a.Min.Z - tol_ft <= bbox_b.Max.Z
        and bbox_a.Max.Z + tol_ft >= bbox_b.Min.Z
    )


class MinMaxBox(object):
    __slots__ = ("Min", "Max")

    def __init__(self, min_pt, max_pt):
        self.Min = min_pt
        self.Max = max_pt


def face_world_bbox(face):
    try:
        mesh = face.Triangulate()
    except Exception:
        return None
    if mesh is None or mesh.NumTriangles == 0:
        return None

    min_x = min_y = min_z = None
    max_x = max_y = max_z = None
    for i in range(mesh.NumTriangles):
        tri = mesh.get_Triangle(i)
        for k in range(3):
            v = tri.get_Vertex(k)
            if min_x is None or v.X < min_x:
                min_x = v.X
            if max_x is None or v.X > max_x:
                max_x = v.X
            if min_y is None or v.Y < min_y:
                min_y = v.Y
            if max_y is None or v.Y > max_y:
                max_y = v.Y
            if min_z is None or v.Z < min_z:
                min_z = v.Z
            if max_z is None or v.Z > max_z:
                max_z = v.Z

    if min_x is None:
        return None
    return MinMaxBox(XYZ(min_x, min_y, min_z), XYZ(max_x, max_y, max_z))


def filter_touching_neighbors(face, neighbors, tol_ft):
    face_bbox = face_world_bbox(face)
    if face_bbox is None:
        return neighbors
    return [
        n for n in neighbors
        if n.bbox is not None and bbox_overlaps(face_bbox, n.bbox, tol_ft)
    ]


def face_sample_point(face):
    try:
        mesh = face.Triangulate()
        if mesh is None or mesh.NumTriangles == 0:
            return None
        tri = mesh.get_Triangle(0)
        v0, v1, v2 = tri.get_Vertex(0), tri.get_Vertex(1), tri.get_Vertex(2)
        return XYZ(
            (v0.X + v1.X + v2.X) / 3.0,
            (v0.Y + v1.Y + v2.Y) / 3.0,
            (v0.Z + v1.Z + v2.Z) / 3.0,
        )
    except Exception:
        return None


def point_probes_into_solid(point, direction, solid, tolerance_ft):
    try:
        probe_len = max(tolerance_ft * 2.0, 0.001)
        end_point = point.Add(direction.Multiply(probe_len))
        line = Line.CreateBound(point, end_point)
    except Exception:
        return False
    try:
        result = solid.IntersectWithCurve(line, SolidCurveIntersectionOptions())
        return result is not None and result.SegmentCount > 0
    except Exception:
        return False


def has_contact(sample_point, normal, tolerance_ft, neighbors):
    for neighbor in neighbors:
        for solid in neighbor.get_solids():
            if point_probes_into_solid(sample_point, normal, solid, tolerance_ft):
                return True
    return False


def quad_loop(p00, p10, p11, p01, normal):
    edge1 = p10 - p00
    edge2 = p01 - p00
    computed_normal = edge1.CrossProduct(edge2)
    pts = [p00, p10, p11, p01]
    if computed_normal.DotProduct(normal) < 0:
        pts.reverse()
    loop = CurveLoop()
    for idx in range(4):
        a = pts[idx]
        b = pts[(idx + 1) % 4]
        loop.Append(Line.CreateBound(a, b))
    return loop


def partition_face_by_contact(face, normal, neighbors, tol_ft, grid_ft):
    """Split a planar face into the free sub-region (gets formwork) and the
    sub-region touching a neighbor (e.g. a beam framing into a column),
    via a cheap UV point-probe grid rather than a fragile solid boolean."""
    try:
        bbox_uv = face.GetBoundingBox()
    except Exception:
        return None

    u0, u1 = bbox_uv.Min.U, bbox_uv.Max.U
    v0, v1 = bbox_uv.Min.V, bbox_uv.Max.V
    if u1 <= u0 or v1 <= v0:
        return None

    mid_uv = UV((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    try:
        deriv = face.ComputeDerivatives(mid_uv)
        u_len = deriv.BasisX.GetLength()
        v_len = deriv.BasisY.GetLength()
    except Exception:
        return None

    if u_len < 1e-9 or v_len < 1e-9:
        return None

    u_steps = max(1, min(MAX_GRID_STEPS, int(round((u1 - u0) * u_len / grid_ft))))
    v_steps = max(1, min(MAX_GRID_STEPS, int(round((v1 - v0) * v_len / grid_ft))))

    du = (u1 - u0) / u_steps
    dv = (v1 - v0) / v_steps
    cell_area_ft2 = du * u_len * dv * v_len

    grid = [[False for _ in range(u_steps)] for _ in range(v_steps)]
    contact_cells = 0
    evaluated_cells = 0

    for j in range(v_steps):
        v_mid = v0 + (j + 0.5) * dv
        for i in range(u_steps):
            u_mid = u0 + (i + 0.5) * du
            uv = UV(u_mid, v_mid)
            try:
                if not face.IsInside(uv):
                    continue
                point = face.Evaluate(uv)
            except Exception:
                continue

            evaluated_cells += 1
            if has_contact(point, normal, tol_ft, neighbors):
                contact_cells += 1
            else:
                grid[j][i] = True

    if evaluated_cells == 0:
        return None

    rectangles = []
    open_rects = {}
    for j in range(v_steps):
        row_runs = []
        i = 0
        while i < u_steps:
            if grid[j][i]:
                start = i
                while i < u_steps and grid[j][i]:
                    i += 1
                row_runs.append((start, i))
            else:
                i += 1

        row_run_set = set(row_runs)
        for key in list(open_rects.keys()):
            if key not in row_run_set:
                start_j = open_rects.pop(key)
                rectangles.append((key[0], key[1], start_j, j))
        for run in row_runs:
            if run not in open_rects:
                open_rects[run] = j

    for key, start_j in open_rects.items():
        rectangles.append((key[0], key[1], start_j, v_steps))

    included_specs = []
    included_area_m2 = 0.0
    for i_start, i_end, j_start, j_end in rectangles:
        u_a = u0 + i_start * du
        u_b = u0 + i_end * du
        v_a = v0 + j_start * dv
        v_b = v0 + j_end * dv
        try:
            p00 = face.Evaluate(UV(u_a, v_a))
            p10 = face.Evaluate(UV(u_b, v_a))
            p11 = face.Evaluate(UV(u_b, v_b))
            p01 = face.Evaluate(UV(u_a, v_b))
            loop = quad_loop(p00, p10, p11, p01, normal)
        except Exception:
            continue

        area_m2 = (u_b - u_a) * u_len * (v_b - v_a) * v_len * FT2_TO_M2
        included_specs.append({
            "loops": List[CurveLoop]([loop]),
            "direction": normal,
            "area_m2": round(area_m2, 6),
        })
        included_area_m2 += area_m2

    contact_area_m2 = cell_area_ft2 * contact_cells * FT2_TO_M2

    return {
        "included_faces": included_specs,
        "included_area_m2": included_area_m2,
        "contact_area_m2": contact_area_m2,
    }


def classify_element_faces(candidate, neighbors, warnings):
    cat_info = CATEGORY_MAP[candidate.category_key]

    included_faces = []
    area_included = 0.0
    face_count = 0
    skipped_curved = 0

    for solid in candidate.get_solids():
        for face in solid.Faces:
            if not isinstance(face, PlanarFace):
                skipped_curved += 1
                continue
            face_count += 1
            normal = face.FaceNormal
            area_m2 = face.Area * FT2_TO_M2
            is_bottom = normal.Z < BOTTOM_NORMAL_Z

            if EXCLUDE_TOP_FACES and normal.Z > TOP_NORMAL_Z:
                continue

            if is_bottom and cat_info["rests_on_ground"] and EXCLUDE_FOUNDATION_BOTTOM:
                continue

            touching = filter_touching_neighbors(face, neighbors, CONTACT_TOLERANCE_FT)

            if touching:
                partition = None
                try:
                    partition = partition_face_by_contact(
                        face, normal, touching, CONTACT_TOLERANCE_FT, DEFAULT_CONTACT_GRID_FT
                    )
                except Exception as e:
                    warnings.append(
                        "Elemento {}: fallo el recorte de una cara por contacto - {}".format(
                            element_id_value(candidate.element.Id), str(e)
                        )
                    )

                if partition is not None:
                    included_faces.extend(partition["included_faces"])
                    area_included += partition["included_area_m2"]
                    continue

                sample_point = face_sample_point(face)
                if sample_point is None:
                    skipped_curved += 1
                    continue
                if has_contact(sample_point, normal, CONTACT_TOLERANCE_FT, touching):
                    continue

            try:
                loops = List[CurveLoop](face.GetEdgesAsCurveLoops())
            except Exception:
                warnings.append(
                    "No se pudieron extraer los bordes de una cara en el elemento {}".format(
                        element_id_value(candidate.element.Id)
                    )
                )
                continue

            included_faces.append({"loops": loops, "direction": normal, "area_m2": area_m2})
            area_included += area_m2

    if skipped_curved:
        warnings.append(
            "Elemento {} ({}): {} cara(s) no planas omitidas".format(
                element_id_value(candidate.element.Id), cat_info["label"], skipped_curved
            )
        )

    return {
        "included_faces": included_faces,
        "included_area_m2": round(area_included, 4),
        "face_count": face_count,
    }


def create_formwork_panel(document, included_face, source_element, category_key):
    solid = GeometryCreationUtilities.CreateExtrusionGeometry(
        included_face["loops"], included_face["direction"], PANEL_THICKNESS_FT
    )
    category_id = ElementId(BuiltInCategory.OST_GenericModel)
    ds = DirectShape.CreateElement(document, category_id)
    ds.SetShape(List[GeometryObject]([solid]))
    ds.Name = "Encofrado"

    comments = "Encofrado 2in | Origen: {} {} (Id {}) | Area: {} m2".format(
        CATEGORY_MAP[category_key]["label"],
        get_mark_or_name(source_element),
        element_id_value(source_element.Id),
        round(included_face["area_m2"], 3),
    )
    try:
        p = ds.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p and not p.IsReadOnly:
            p.Set(comments)
    except Exception:
        pass

    return ds


def collect_candidates():
    built = []
    for category_key in CATEGORIES:
        cat_info = CATEGORY_MAP.get(category_key)
        if not cat_info:
            continue
        collector = (
            FilteredElementCollector(doc)
            .OfCategory(cat_info["bic"])
            .WhereElementIsNotElementType()
        )
        for element in collector:
            try:
                bbox = element.get_BoundingBox(None)
            except Exception:
                bbox = None
            built.append(Candidate(element, category_key, bbox))
    return built


# ----------------------------- main ------------------------------------

warnings = []
candidates = collect_candidates()

element_results = []
category_totals = {}
panels = []

if CREATE_GEOMETRY:
    TransactionManager.Instance.EnsureInTransaction(doc)

for candidate in candidates:
    neighbors = [
        other for other in candidates
        if other is not candidate and bbox_overlaps(candidate.bbox, other.bbox, CONTACT_TOLERANCE_FT)
    ]

    solids = candidate.get_solids()
    if not solids:
        warnings.append(
            "Elemento {}: no se pudo extraer geometria solida, se omite".format(
                element_id_value(candidate.element.Id)
            )
        )
        continue

    classification = classify_element_faces(candidate, neighbors, warnings)

    if CREATE_GEOMETRY:
        for face_spec in classification["included_faces"]:
            try:
                panel = create_formwork_panel(doc, face_spec, candidate.element, candidate.category_key)
                panels.append(panel)
            except Exception as e:
                warnings.append(
                    "Elemento {}: no se pudo crear el panel de una cara ({} m2) - {}".format(
                        element_id_value(candidate.element.Id), face_spec["area_m2"], str(e)
                    )
                )

    cat_label = CATEGORY_MAP[candidate.category_key]["label"]
    totals = category_totals.setdefault(cat_label, {"included_area_m2": 0.0, "element_count": 0})
    totals["included_area_m2"] = round(totals["included_area_m2"] + classification["included_area_m2"], 4)
    totals["element_count"] += 1

    element_results.append({
        "id": element_id_value(candidate.element.Id),
        "mark": get_mark_or_name(candidate.element),
        "category": cat_label,
        "included_area_m2": classification["included_area_m2"],
    })

if CREATE_GEOMETRY:
    TransactionManager.Instance.TransactionTaskDone()

report = {
    "panel_thickness_in": PANEL_THICKNESS_IN,
    "elements_processed": len(element_results),
    "panels_created": len(panels),
    "category_totals": category_totals,
    "warnings": warnings,
    "elements": element_results,
}

OUT = (panels, report)
