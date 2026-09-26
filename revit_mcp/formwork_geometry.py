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
from formwork_spatial import (
    PlaneFrame,
    SpatialHash,
    bbox_to_tuple,
    boxes_overlap,
    candidate_cells,
)

FT2_TO_M2 = 0.09290304  # square feet -> square meters
TOP_NORMAL_Z = 0.98  # cos(~11.5deg) — treat near-vertical-up normals as "top"
BOTTOM_NORMAL_Z = -0.98

# Sampling grid used to trim a face against a neighbor that only partially
# overlaps it (e.g. a beam framing into the side of a column) — see
# `_partition_face_by_contact`. ~7.6 cm, fine enough to resolve typical
# beam/column interfaces without an excessive cell count.
DEFAULT_CONTACT_GRID_FT = 0.25
MAX_GRID_STEPS = 80  # cap per axis so a huge face can't blow up runtime
# XY bucket size for the neighbor lookup (~5 m): big enough that most
# elements land in 1-4 buckets, small enough to keep buckets short.
NEIGHBOR_HASH_CELL_FT = 16.0

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
        # Plain-float box tuple: comparing tuples avoids .NET interop in
        # the neighbor/face filters, which run a very large number of times.
        self.bbox = bbox_to_tuple(bbox)
        self.solids = None  # lazy

    def get_solids(self):
        if self.solids is None:
            self.solids = get_element_solids(self.element)
        return self.solids


def _face_world_bbox(face):
    """Approximate world-space bounding box of a face as a box tuple,
    built from its triangulated mesh (works for any loop shape, holes
    included)."""
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
    return (min_x, min_y, min_z, max_x, max_y, max_z)


def _filter_touching_neighbors(face, neighbors, tol_ft):
    """Narrow `neighbors` down to the ones whose bounding box actually
    overlaps THIS face (not just the candidate element's overall bbox),
    so faces with nothing nearby skip the grid partition below."""
    face_bbox = _face_world_bbox(face)
    if face_bbox is None:
        return neighbors
    return [
        n
        for n in neighbors
        if n.bbox is not None and boxes_overlap(face_bbox, n.bbox, tol_ft)
    ]


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


def _quad_loop(p00, p10, p11, p01, normal):
    """Build a CurveLoop for a planar quad, oriented so it works with
    `CreateExtrusionGeometry(loop, normal, thickness)` regardless of the
    order the four corners were computed in."""
    edge1 = p10 - p00
    edge2 = p01 - p00
    computed_normal = edge1.CrossProduct(edge2)
    pts = [p00, p10, p11, p01]
    if computed_normal.DotProduct(normal) < 0:
        pts.reverse()
    loop = DB.CurveLoop()
    for idx in range(4):
        a = pts[idx]
        b = pts[(idx + 1) % 4]
        loop.Append(DB.Line.CreateBound(a, b))
    return loop


def _partition_face_by_contact(face, normal, neighbors, tol_ft, grid_ft):
    """Split a planar face into the sub-region that is actually free
    (gets formwork) and the sub-region in contact with a neighbor element
    (e.g. a beam framing into the side of a column), instead of an
    all-or-nothing decision for the whole face.

    Works on a UV grid (cheap point probes, same technique as
    `_has_contact`) rather than a 3D solid boolean: booleans between
    coincident/touching Revit solids are notoriously fragile, which is
    why the rest of this module avoids them.

    Returns a dict with `included_faces` (panel specs, possibly several
    per face), `included_area_m2` and `contact_area_m2`; `{"no_contact":
    True}` when no neighbor actually touches the face; or None if the
    face's parametrization couldn't be read (caller falls back to the
    single-sample whole-face check).
    """
    try:
        bbox_uv = face.GetBoundingBox()
    except Exception:
        return None

    u0, u1 = bbox_uv.Min.U, bbox_uv.Max.U
    v0, v1 = bbox_uv.Min.V, bbox_uv.Max.V
    if u1 <= u0 or v1 <= v0:
        return None

    mid_uv = DB.UV((u0 + u1) / 2.0, (v0 + v1) / 2.0)
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

    # Pass 1 — probe only the cells that lie inside some neighbor's
    # bounding box. Probing is the expensive part (a Revit solid/curve
    # intersection per cell and neighbor solid), and on a real model most
    # of a face is nowhere near any neighbor, or the neighbor only grazes
    # one edge of it (a slab sitting on a wall, a wall meeting another).
    o = deriv.Origin
    frame = PlaneFrame(
        (o.X, o.Y, o.Z),
        (deriv.BasisX.X, deriv.BasisX.Y, deriv.BasisX.Z),
        (deriv.BasisY.X, deriv.BasisY.Y, deriv.BasisY.Z),
        mid_uv.U,
        mid_uv.V,
    )
    nearby = candidate_cells(
        frame,
        [n.bbox for n in neighbors],
        # Same reach as the probe segment in `_point_probes_into_solid`.
        max(tol_ft * 2.0, 0.001),
        u0,
        du,
        u_steps,
        v0,
        dv,
        v_steps,
    )

    contact = set()
    for (i, j), neighbor_idxs in nearby.items():
        uv = DB.UV(u0 + (i + 0.5) * du, v0 + (j + 0.5) * dv)
        try:
            if not face.IsInside(uv):
                continue
            point = face.Evaluate(uv)
        except Exception:
            continue
        if _has_contact(point, normal, tol_ft, [neighbors[k] for k in neighbor_idxs]):
            contact.add((i, j))

    if not contact:
        # Nothing actually touches this face: the caller keeps the whole
        # face with its exact edge loops, no grid needed.
        return {"no_contact": True}

    # Pass 2 — only faces with real contact pay for the full grid, which
    # is needed to carve the free region into panel rectangles.
    grid = [[False for _ in range(u_steps)] for _ in range(v_steps)]
    contact_cells = len(contact)
    for j in range(v_steps):
        v_mid = v0 + (j + 0.5) * dv
        for i in range(u_steps):
            if (i, j) in contact:
                continue
            try:
                grid[j][i] = face.IsInside(DB.UV(u0 + (i + 0.5) * du, v_mid))
            except Exception:
                continue

    # Merge contiguous included cells into rectangles: runs of True
    # within a row, then merge runs across vertically-adjacent rows that
    # share the exact same (i_start, i_end) span into a taller rectangle.
    rectangles = []
    open_rects = {}  # (i_start, i_end) -> row where the rectangle started
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
            p00 = face.Evaluate(DB.UV(u_a, v_a))
            p10 = face.Evaluate(DB.UV(u_b, v_a))
            p11 = face.Evaluate(DB.UV(u_b, v_b))
            p01 = face.Evaluate(DB.UV(u_a, v_b))
            loop = _quad_loop(p00, p10, p11, p01, normal)
        except Exception:
            continue

        area_m2 = (u_b - u_a) * u_len * (v_b - v_a) * v_len * FT2_TO_M2
        included_specs.append(
            {
                "loops": List[DB.CurveLoop]([loop]),
                "direction": normal,
                "area_m2": round(area_m2, 6),
            }
        )
        included_area_m2 += area_m2

    contact_area_m2 = cell_area_ft2 * contact_cells * FT2_TO_M2

    return {
        "included_faces": included_specs,
        "included_area_m2": included_area_m2,
        "contact_area_m2": contact_area_m2,
    }


def classify_element_faces(candidate, neighbors, config, warnings):
    """Classify every planar face of `candidate`'s solids into
    included/excluded buckets. Returns a dict summary plus the list of
    included face specs (for optional panel creation)."""

    tol_ft = config["contact_tolerance_ft"]
    grid_ft = config.get("contact_grid_ft", DEFAULT_CONTACT_GRID_FT)
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
            is_bottom = normal.Z < BOTTOM_NORMAL_Z

            if exclude_top and normal.Z > TOP_NORMAL_Z:
                area_top += area_m2
                continue

            if is_bottom and cat_info["rests_on_ground"] and config["exclude_foundation_bottom"]:
                area_bottom_excluded += area_m2
                continue

            # Only elements actually near this face can be in contact with
            # it — narrowing down here lets untouched faces (the common
            # case) skip straight to "fully included" below.
            touching = _filter_touching_neighbors(face, neighbors, tol_ft)

            if touching:
                partition = None
                try:
                    partition = _partition_face_by_contact(
                        face, normal, touching, tol_ft, grid_ft
                    )
                except Exception as e:
                    warnings.append(
                        "Elemento {}: fallo el recorte de una cara por contacto, "
                        "se uso el metodo simple - {}".format(
                            element_id_value(candidate.element.Id), str(e)
                        )
                    )

                if partition is not None and partition.get("no_contact"):
                    pass  # nothing touches it: whole-face path below
                elif partition is not None:
                    included_faces.extend(partition["included_faces"])
                    area_included += partition["included_area_m2"]
                    if is_bottom:
                        area_bottom_excluded += partition["contact_area_m2"]
                    else:
                        area_contact += partition["contact_area_m2"]
                    continue
                else:
                    # Partition couldn't be computed (unusual face
                    # parametrization) — fall back to the previous
                    # whole-face single-sample check rather than dropping it.
                    sample_point = _face_sample_point(face)
                    if sample_point is None:
                        skipped_curved += 1
                        continue
                    if _has_contact(sample_point, normal, tol_ft, touching):
                        if is_bottom:
                            area_bottom_excluded += area_m2
                        else:
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


def find_material_id(doc, material_name):
    """Resolve a material name to its ElementId, or None if blank/not
    found (a freely-typed tag that isn't a real project material)."""
    if not material_name:
        return None
    try:
        for m in DB.FilteredElementCollector(doc).OfClass(DB.Material):
            if getattr(m, "Name", None) == material_name:
                return m.Id
    except Exception:
        pass
    return None


def create_formwork_panel(
    doc,
    included_face,
    source_element,
    category_key,
    thickness_ft,
    formwork_material="",
    formwork_material_id=None,
):
    """Create one DirectShape panel (Generic Models) for an included face.

    `formwork_material` is a free-text tag (e.g. "Madera", "Metalico") that
    identifies what the formwork itself is made of - it has no relation to
    the source element's own material and is never used to filter which
    elements get processed, only recorded on the panel for later takeoff.

    `formwork_material_id`, when it resolves to a real project material
    (see `find_material_id`), is baked into the panel's own geometry so it
    renders with that material's actual color/appearance in the model."""
    solid_options = None
    if formwork_material_id is not None:
        try:
            solid_options = DB.SolidOptions(
                formwork_material_id, DB.ElementId.InvalidElementId
            )
        except Exception:
            solid_options = None

    if solid_options is not None:
        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            included_face["loops"],
            included_face["direction"],
            thickness_ft,
            solid_options,
        )
    else:
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
        ("EF_Material_Encofrado", formwork_material or ""),
    ):
        p = ds.LookupParameter(name)
        if p and not p.IsReadOnly:
            p.Set(value)

    area_param = ds.LookupParameter("EF_Area_m2")
    if area_param and not area_param.IsReadOnly:
        area_param.Set(float(included_face["area_m2"]))

    return ds


def process_formwork(
    doc, elements_by_category, config, warnings, context_elements_by_category=None
):
    """Main orchestrator. `elements_by_category` = {category_key: [Element,...]}
    are the elements to actually report on / create panels for.
    `context_elements_by_category` (same shape) are extra elements used
    ONLY for contact/neighbor detection - never reported or paneled
    themselves (unless they also appear in `elements_by_category`).
    Pass every structural category here (not just the ones the caller
    asked to process) so e.g. a wall's panel still gets trimmed against a
    beam it touches even when the beam itself wasn't requested this run -
    otherwise contact can only ever be detected between elements
    processed together in the very same call.

    `config` = dict with contact_tolerance_ft, panel_thickness_ft,
    exclude_top_faces, exclude_foundation_bottom, create_geometry, and
    optionally formwork_materials ({category_key: material_tag}) - a
    free-text label per category recorded on the created panels to
    identify what the formwork is made of (wood, metal, etc.); it never
    filters which elements get processed. Returns the report dict
    (mutates the model when create_geometry is true — caller must run
    this inside an active Transaction). Quantity takeoff is always
    returned in the report (per-element and per-category totals); build
    a native Revit schedule off the created panels' EF_Area_m2 /
    EF_Categoria_Origen / EF_Material_Encofrado parameters if you need a
    table in the model."""

    def _build_candidates(by_category):
        built = []
        for category_key, elements in by_category.items():
            for element in elements:
                try:
                    bbox = element.get_BoundingBox(None)
                except Exception:
                    bbox = None
                built.append(_Candidate(element, category_key, bbox))
        return built

    candidates = _build_candidates(elements_by_category)

    report_ids = set(element_id_value(c.element.Id) for c in candidates)
    neighbor_pool = list(candidates)
    if context_elements_by_category:
        for candidate in _build_candidates(context_elements_by_category):
            if element_id_value(candidate.element.Id) in report_ids:
                continue  # already in candidates, avoid a duplicate entry
            neighbor_pool.append(candidate)

    tol_ft = config["contact_tolerance_ft"]
    element_results = []
    category_totals = {}
    panels_created = 0
    material_id_cache = {}

    neighbor_hash = SpatialHash(NEIGHBOR_HASH_CELL_FT)
    for idx, other in enumerate(neighbor_pool):
        neighbor_hash.insert(idx, other.bbox)

    for candidate in candidates:
        neighbors = [
            neighbor_pool[idx]
            for idx in neighbor_hash.query(candidate.bbox, tol_ft)
            if neighbor_pool[idx] is not candidate
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
            formwork_material = config.get("formwork_materials", {}).get(
                candidate.category_key, ""
            )
            if formwork_material not in material_id_cache:
                material_id_cache[formwork_material] = find_material_id(
                    doc, formwork_material
                )
            formwork_material_id = material_id_cache[formwork_material]

            for face_spec in classification["included_faces"]:
                try:
                    create_formwork_panel(
                        doc,
                        face_spec,
                        candidate.element,
                        candidate.category_key,
                        config["panel_thickness_ft"],
                        formwork_material,
                        formwork_material_id,
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
