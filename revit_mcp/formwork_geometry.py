# -*- coding: UTF-8 -*-
"""
Formwork Geometry Module for Revit MCP
Pure geometry/classification logic for automated structural formwork
(encofrado) generation and quantity takeoff. No routing/HTTP concerns here.

Runs inside Revit's IronPython 2 engine (via pyRevit Routes) — no f-strings,
no `typing`, no dataclasses.
"""

from pyrevit import DB
import clr

clr.AddReference("System")
from System.Collections.Generic import List

from utils import normalize_string, element_id_value

FT2_TO_M2 = 0.09290304  # square feet -> square meters
TOP_NORMAL_Z = 0.98  # cos(~11.5deg) — treat near-vertical-up normals as "top"
BOTTOM_NORMAL_Z = -0.98

# Category catalogue: request key -> Revit category + default construction
# sequence priority (lower = poured earlier) + whether it rests on
# ground/blinding rather than on another modeled structural element.
CATEGORY_MAP = {
    "foundations": {
        "bic": DB.BuiltInCategory.OST_StructuralFoundation,
        "label": "Cimentacion",
        "sequence": 1,
        "rests_on_ground": True,
    },
    "walls": {
        "bic": DB.BuiltInCategory.OST_Walls,
        "label": "Muros",
        "sequence": 2,
        "rests_on_ground": False,
    },
    "columns": {
        "bic": DB.BuiltInCategory.OST_StructuralColumns,
        "label": "Columnas",
        "sequence": 3,
        "rests_on_ground": False,
    },
    "beams": {
        "bic": DB.BuiltInCategory.OST_StructuralFraming,
        "label": "Vigas",
        "sequence": 4,
        "rests_on_ground": False,
    },
    "slabs": {
        "bic": DB.BuiltInCategory.OST_Floors,
        "label": "Losas",
        "sequence": 5,
        "rests_on_ground": False,
    },
}

DEFAULT_CATEGORIES = ["foundations", "walls", "columns", "beams", "slabs"]

_GEOM_OPTIONS = None


def _geometry_options():
    global _GEOM_OPTIONS
    if _GEOM_OPTIONS is None:
        opts = DB.Options()
        opts.ComputeReferences = True
        opts.DetailLevel = DB.ViewDetailLevel.Fine
        opts.IncludeNonVisibleObjects = False
        _GEOM_OPTIONS = opts
    return _GEOM_OPTIONS


def _collect_solids(geometry_element, solids):
    """Recursively collect non-empty Solids from a GeometryElement,
    unwrapping GeometryInstance (family instances) as needed."""
    for obj in geometry_element:
        if isinstance(obj, DB.Solid):
            try:
                if obj.Volume > 1e-9 and obj.Faces.Size > 0:
                    solids.append(obj)
            except Exception:
                continue
        elif isinstance(obj, DB.GeometryInstance):
            try:
                _collect_solids(obj.GetInstanceGeometry(), solids)
            except Exception:
                continue


def get_element_solids(element):
    """Return the list of world-space Solids that make up an element."""
    solids = []
    try:
        geom = element.get_Geometry(_geometry_options())
    except Exception:
        return solids
    if geom is None:
        return solids
    _collect_solids(geom, solids)
    return solids


def element_matches_material(element, material_filter):
    """Best-effort structural-material check. Fails OPEN (includes the
    element) when the material can't be determined, so an incomplete/odd
    project setup never silently drops elements from the takeoff.

    Checks, in order: the STRUCTURAL_MATERIAL_PARAM instance/type
    parameter (reliable for framing/columns), an explicit "Structural
    Material" type lookup, and finally the element/type NAME itself —
    many real projects (this one included) encode the material in the
    family/type name (e.g. "MURO DE CORTE_CONCRETO..." vs "MURO DE
    ALBANILERIA...") rather than in a material parameter, especially for
    walls and floors where Revit has no single per-instance material."""
    if not material_filter:
        return True
    needle = material_filter.strip().lower()
    if not needle:
        return True

    candidate_names = []
    try:
        mat_param = element.get_Parameter(DB.BuiltInParameter.STRUCTURAL_MATERIAL_PARAM)
        if mat_param and mat_param.HasValue:
            mat_id = mat_param.AsElementId()
            if mat_id and element_id_value(mat_id) > 0:
                mat_elem = element.Document.GetElement(mat_id)
                if mat_elem:
                    candidate_names.append(normalize_string(get_name_safe(mat_elem)))
    except Exception:
        pass

    type_elem = None
    try:
        type_elem = element.Document.GetElement(element.GetTypeId())
    except Exception:
        pass

    if type_elem:
        try:
            p = type_elem.LookupParameter("Structural Material")
            if p and p.HasValue and p.StorageType == DB.StorageType.ElementId:
                mat_id = p.AsElementId()
                if mat_id and element_id_value(mat_id) > 0:
                    mat_elem = element.Document.GetElement(mat_id)
                    if mat_elem:
                        candidate_names.append(normalize_string(get_name_safe(mat_elem)))
        except Exception:
            pass
        candidate_names.append(normalize_string(get_name_safe(type_elem)))

    candidate_names.append(normalize_string(get_name_safe(element)))

    if not candidate_names:
        return True  # can't tell -> don't exclude

    for name in candidate_names:
        if needle in name.lower():
            return True
    return False


def get_name_safe(element):
    try:
        return element.Name
    except AttributeError:
        return DB.Element.Name.__get__(element)


def get_mark_or_name(element):
    try:
        p = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        if p and p.HasValue:
            val = normalize_string(p.AsString())
            if val and val != u"Unnamed":
                return val
    except Exception:
        pass
    return normalize_string(get_name_safe(element))


class _Candidate(object):
    """A structural element plus its cached solids/bbox, used both as the
    element being processed and as a neighbor lookup target."""

    def __init__(self, element, category_key, bbox):
        self.element = element
        self.category_key = category_key
        self.bbox = bbox
        self.solids = None  # lazy

    def get_solids(self):
        if self.solids is None:
            self.solids = get_element_solids(self.element)
        return self.solids


def _bbox_overlaps(bbox_a, bbox_b, tol_ft):
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


def _face_sample_point(face):
    """A point guaranteed to lie ON the face surface, robust to L-shapes
    and faces with holes (unlike a UV bounding-box midpoint)."""
    try:
        mesh = face.Triangulate()
        if mesh is None or mesh.NumTriangles == 0:
            return None
        tri = mesh.get_Triangle(0)
        v0, v1, v2 = tri.get_Vertex(0), tri.get_Vertex(1), tri.get_Vertex(2)
        return DB.XYZ(
            (v0.X + v1.X + v2.X) / 3.0,
            (v0.Y + v1.Y + v2.Y) / 3.0,
            (v0.Z + v1.Z + v2.Z) / 3.0,
        )
    except Exception:
        return None


def _point_probes_into_solid(point, direction, solid, tolerance_ft):
    """Does a short probe segment from `point` along `direction` land
    inside `solid`? Used to detect face-to-face contact between two
    elements without relying on fragile Boolean ops between coincident
    solids."""
    try:
        probe_len = max(tolerance_ft * 2.0, 0.001)
        end_point = point.Add(direction.Multiply(probe_len))
        line = DB.Line.CreateBound(point, end_point)
    except Exception:
        return False
    try:
        result = solid.IntersectWithCurve(line, DB.SolidCurveIntersectionOptions())
        return result is not None and result.SegmentCount > 0
    except Exception:
        return False


def _has_contact(sample_point, normal, tolerance_ft, neighbors):
    for neighbor in neighbors:
        for solid in neighbor.get_solids():
            if _point_probes_into_solid(sample_point, normal, solid, tolerance_ft):
                return True
    return False


def classify_element_faces(candidate, neighbors, config, warnings):
    """Classify every planar face of `candidate`'s solids into
    included/excluded buckets. Returns a dict summary plus the list of
    included face specs (for optional panel creation)."""

    tol_ft = config["contact_tolerance_ft"]
    exclude_top = config["exclude_top_faces"]
    cat_info = CATEGORY_MAP[candidate.category_key]

    included_faces = []
    area_included = 0.0
    area_top = 0.0
    area_bottom_excluded = 0.0
    area_contact = 0.0
    face_count = 0
    skipped_curved = 0

    for solid in candidate.get_solids():
        for face in solid.Faces:
            if not isinstance(face, DB.PlanarFace):
                skipped_curved += 1
                continue
            face_count += 1
            normal = face.FaceNormal
            area_m2 = face.Area * FT2_TO_M2

            if exclude_top and normal.Z > TOP_NORMAL_Z:
                area_top += area_m2
                continue

            sample_point = _face_sample_point(face)
            if sample_point is None:
                skipped_curved += 1
                continue

            if normal.Z < BOTTOM_NORMAL_Z:
                if cat_info["rests_on_ground"] and config["exclude_foundation_bottom"]:
                    area_bottom_excluded += area_m2
                    continue
                if _has_contact(sample_point, normal, tol_ft, neighbors):
                    area_bottom_excluded += area_m2
                    continue
                # suspended soffit (beam/slab in cantilever or over a void)
                # -> falls through to "included" below
            else:
                if _has_contact(sample_point, normal, tol_ft, neighbors):
                    area_contact += area_m2
                    continue

            try:
                loops = List[DB.CurveLoop](face.GetEdgesAsCurveLoops())
            except Exception:
                warnings.append(
                    "No se pudieron extraer los bordes de una cara en el elemento {}".format(
                        element_id_value(candidate.element.Id)
                    )
                )
                continue

            included_faces.append(
                {
                    "loops": loops,
                    "direction": normal,
                    "area_m2": area_m2,
                }
            )
            area_included += area_m2

    if skipped_curved:
        warnings.append(
            "Elemento {} ({}): {} cara(s) no planas omitidas (ej. columna circular)".format(
                element_id_value(candidate.element.Id), cat_info["label"], skipped_curved
            )
        )

    return {
        "included_faces": included_faces,
        "included_area_m2": round(area_included, 4),
        "excluded_top_area_m2": round(area_top, 4),
        "excluded_bottom_area_m2": round(area_bottom_excluded, 4),
        "excluded_contact_area_m2": round(area_contact, 4),
        "face_count": face_count,
        "skipped_faces": skipped_curved,
    }


def create_formwork_panel(doc, included_face, source_element, category_key, thickness_ft):
    """Create one DirectShape panel (Generic Models) for an included face."""
    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        included_face["loops"], included_face["direction"], thickness_ft
    )
    category_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
    ds = DB.DirectShape.CreateElement(doc, category_id)
    ds.SetShape(List[DB.GeometryObject]([solid]))
    ds.Name = "Encofrado"

    for name, value in (
        ("EF_Elemento_Origen_Id", str(element_id_value(source_element.Id))),
        ("EF_Categoria_Origen", CATEGORY_MAP[category_key]["label"]),
    ):
        p = ds.LookupParameter(name)
        if p and not p.IsReadOnly:
            p.Set(value)

    area_param = ds.LookupParameter("EF_Area_m2")
    if area_param and not area_param.IsReadOnly:
        area_param.Set(float(included_face["area_m2"]))

    return ds


def write_quantity_parameter(element, area_m2, warnings):
    param = element.LookupParameter("EF_Area_Encofrado_m2")
    if not param:
        warnings.append(
            "Elemento {}: no se encontro el parametro EF_Area_Encofrado_m2 (revisar setup de parametros compartidos)".format(
                element_id_value(element.Id)
            )
        )
        return False
    if param.IsReadOnly:
        warnings.append(
            "Elemento {}: EF_Area_Encofrado_m2 es de solo lectura".format(
                element_id_value(element.Id)
            )
        )
        return False
    param.Set(float(area_m2))
    return True


def process_formwork(doc, elements_by_category, config, warnings):
    """Main orchestrator. `elements_by_category` = {category_key: [Element,...]}.
    `config` = dict with contact_tolerance_ft, panel_thickness_ft,
    exclude_top_faces, exclude_foundation_bottom, create_geometry,
    write_quantities. Returns the report dict (mutates the model when
    create_geometry/write_quantities are true — caller must run this
    inside an active Transaction)."""

    candidates = []
    for category_key, elements in elements_by_category.items():
        for element in elements:
            try:
                bbox = element.get_BoundingBox(None)
            except Exception:
                bbox = None
            candidates.append(_Candidate(element, category_key, bbox))

    tol_ft = config["contact_tolerance_ft"]
    element_results = []
    category_totals = {}
    panels_created = 0

    for candidate in candidates:
        neighbors = [
            other
            for other in candidates
            if other is not candidate
            and _bbox_overlaps(candidate.bbox, other.bbox, tol_ft)
        ]

        solids = candidate.get_solids()
        if not solids:
            warnings.append(
                "Elemento {}: no se pudo extraer geometria solida, se omite".format(
                    element_id_value(candidate.element.Id)
                )
            )
            continue

        classification = classify_element_faces(candidate, neighbors, config, warnings)

        if config["create_geometry"]:
            for face_spec in classification["included_faces"]:
                try:
                    create_formwork_panel(
                        doc,
                        face_spec,
                        candidate.element,
                        candidate.category_key,
                        config["panel_thickness_ft"],
                    )
                    panels_created += 1
                except Exception as e:
                    warnings.append(
                        "Elemento {}: no se pudo crear el panel de una cara ({} m2) - {}".format(
                            element_id_value(candidate.element.Id),
                            face_spec["area_m2"],
                            str(e),
                        )
                    )

        if config["write_quantities"]:
            write_quantity_parameter(
                candidate.element, classification["included_area_m2"], warnings
            )

        cat_label = CATEGORY_MAP[candidate.category_key]["label"]
        totals = category_totals.setdefault(
            cat_label, {"included_area_m2": 0.0, "element_count": 0}
        )
        totals["included_area_m2"] = round(
            totals["included_area_m2"] + classification["included_area_m2"], 4
        )
        totals["element_count"] += 1

        element_results.append(
            {
                "id": element_id_value(candidate.element.Id),
                "mark": get_mark_or_name(candidate.element),
                "category": cat_label,
                "included_area_m2": classification["included_area_m2"],
                "excluded_top_area_m2": classification["excluded_top_area_m2"],
                "excluded_bottom_area_m2": classification["excluded_bottom_area_m2"],
                "excluded_contact_area_m2": classification["excluded_contact_area_m2"],
                "face_count": classification["face_count"],
            }
        )

    return {
        "elements": element_results,
        "category_totals": category_totals,
        "panels_created": panels_created,
        "element_count": len(element_results),
    }
