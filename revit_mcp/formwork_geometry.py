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
    axis_lines,
    bbox_to_tuple,
    box_corners,
    box_uv_range,
    boxes_overlap,
    candidate_cells,
    drop_collinear_points,
    edge_outward_normals,
    offset_polygon,
    split_polygon_edges,
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
    # Monolithic concrete stairs: the Stairs element's geometry already
    # includes its runs and landings. Treads are top faces (left open);
    # risers, side (stringer) faces and the flight/landing soffits get
    # formwork like any other face.
    "stairs": {
        "bic": DB.BuiltInCategory.OST_Stairs,
        "label": "Escaleras",
        "sequence": 6,
        "rests_on_ground": False,
    },
}

DEFAULT_CATEGORIES = ["foundations", "walls", "columns", "beams", "slabs", "stairs"]

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
        # Masonry walls are never paneled, but stay as neighbors: a column
        # poured against a brick wall needs no formwork on that face.
        self.is_masonry = category_key == "walls" and is_masonry_wall(element)

    def get_solids(self):
        if self.solids is None:
            self.solids = get_element_solids(self.element)
        return self.solids


class _SoilCandidate(object):
    """Pseudo-neighbor standing for the ground in front of a face that is
    poured against the excavation: contact with it means "no formwork"."""

    category_key = "soil"
    is_masonry = False
    element = None

    def __init__(self, box, solid):
        self.bbox = box
        self.solids = [solid]

    def get_solids(self):
        return self.solids


MASONRY_KEYWORDS = ("albanil", "masonry", "ladrillo", "king kong")


def is_masonry_wall(element):
    """Brick/block wall (albanileria): matched on the wall type name or
    the class of its layers' materials."""
    try:
        doc = element.Document
        wall_type = doc.GetElement(element.GetTypeId())
    except Exception:
        return False
    if wall_type is None:
        return False
    names = []
    try:
        p = wall_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        names.append(p.AsString() if p else "")
    except Exception:
        pass
    try:
        structure = wall_type.GetCompoundStructure()
        if structure is not None:
            for layer in structure.GetLayers():
                material = doc.GetElement(layer.MaterialId)
                if material is not None:
                    names.append(getattr(material, "MaterialClass", "") or "")
                    names.append(getattr(material, "Name", "") or "")
    except Exception:
        pass
    text = u" ".join(normalize_string(n) for n in names if n).lower()
    text = text.replace(u"ñ", u"n")
    return any(k in text for k in MASONRY_KEYWORDS)


def find_ground_elevation_ft(doc):
    """Internal elevation (ft) of the natural ground level: the level named
    "NTN..." if there is one, else the project's zero."""
    try:
        for level in DB.FilteredElementCollector(doc).OfClass(DB.Level):
            name = normalize_string(getattr(level, "Name", "")).upper()
            if name.startswith(u"NTN"):
                return level.ProjectElevation
    except Exception:
        pass
    return 0.0


# Where each category keeps its level when `LevelId` is empty: beams
# (reference level), stairs (base level), then the generic ones.
_LEVEL_PARAMS = (
    DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
    DB.BuiltInParameter.FAMILY_BASE_LEVEL_PARAM,
    DB.BuiltInParameter.STAIRS_BASE_LEVEL_PARAM,
    DB.BuiltInParameter.WALL_BASE_CONSTRAINT,
    DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM,
    DB.BuiltInParameter.LEVEL_PARAM,
)


def element_level_id(element, levels=None):
    """Level an element belongs to: its `LevelId`, else its level
    parameter, else (`levels` given) the highest level at or below the
    bottom of its bounding box - e.g. a footing hosted on a face. None if
    nothing applies."""
    invalid = DB.ElementId.InvalidElementId
    try:
        level_id = element.LevelId
        if level_id is not None and level_id != invalid:
            return level_id
    except Exception:
        pass
    for bip in _LEVEL_PARAMS:
        try:
            p = element.get_Parameter(bip)
            if p is not None and p.StorageType == DB.StorageType.ElementId:
                level_id = p.AsElementId()
                if level_id != invalid:
                    return level_id
        except Exception:
            continue
    if levels:
        bb = element.get_BoundingBox(None)
        if bb is not None:
            below = [lv for lv in levels if lv.ProjectElevation <= bb.Min.Z + 1e-3]
            if below:
                return max(below, key=lambda lv: lv.ProjectElevation).Id
            return min(levels, key=lambda lv: lv.ProjectElevation).Id
    return None


# Vertical elements are poured with the slab/beams of the level they
# reach: their top constraint parameter, per category.
_TOP_LEVEL_PARAMS = {
    "columns": DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM,
    "walls": DB.BuiltInParameter.WALL_HEIGHT_TYPE,
    "stairs": DB.BuiltInParameter.STAIRS_TOP_LEVEL_PARAM,
}
POUR_LEVEL_TOL_FT = 0.1  # ~3 cm: a top this close above a level counts as reaching it


def element_pour_level_id(element, category_key, levels):
    """Level whose on-site pour includes the element: beams, slabs and
    foundations go with their own level; columns, walls and stairs with
    the level they reach (top constraint) - the P01->P02 columns are
    poured with the P02 beams and slab. An element without a top
    constraint (unconnected wall, e.g. a parapet) goes with the highest
    level at or below its top."""
    top_param = _TOP_LEVEL_PARAMS.get(category_key)
    if top_param is None:
        return element_level_id(element, levels)
    try:
        p = element.get_Parameter(top_param)
        if p is not None and p.StorageType == DB.StorageType.ElementId:
            level_id = p.AsElementId()
            if level_id != DB.ElementId.InvalidElementId:
                return level_id
    except Exception:
        pass
    bb = element.get_BoundingBox(None)
    if bb is not None and levels:
        below = [lv for lv in levels if lv.ProjectElevation <= bb.Max.Z + POUR_LEVEL_TOL_FT]
        if below:
            return max(below, key=lambda lv: lv.ProjectElevation).Id
    return element_level_id(element, levels)


def category_key_of(element):
    """(CATEGORY_MAP key or None, element to process). A stair run or
    landing stands for its whole stair (whose geometry includes them)."""
    get_stairs = getattr(element, "GetStairs", None)
    if get_stairs is not None:
        try:
            element = get_stairs() or element
        except Exception:
            pass
    category = element.Category
    if category is None:
        return None, element
    for key, info in CATEGORY_MAP.items():
        if element_id_value(category.Id) == int(info["bic"]):
            return key, element
    return None, element


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

    Contact is detected on a UV grid (cheap point probes, same technique
    as `_has_contact`); the free region itself is then cut exactly from
    the neighbors that touch (`_exact_free_region`), one panel per
    connected area. Only if that boolean fails does the grid carve the
    region into rectangles (approximate, reported as `approximate`).

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

    o = deriv.Origin
    frame = PlaneFrame(
        (o.X, o.Y, o.Z),
        (deriv.BasisX.X, deriv.BasisX.Y, deriv.BasisX.Z),
        (deriv.BasisY.X, deriv.BasisY.Y, deriv.BasisY.Z),
        mid_uv.U,
        mid_uv.V,
    )

    # Grid lines also pass through every neighbor's projected box edges,
    # so a contact boundary (e.g. the slab soffit along a beam side) is
    # cut exactly there instead of being rounded to the nearest cell.
    u_breaks = []
    v_breaks = []
    for n in neighbors:
        if n.bbox is None:
            continue
        nu0, nu1, nv0, nv1 = box_uv_range(frame, n.bbox)
        u_breaks.extend((nu0, nu1))
        v_breaks.extend((nv0, nv1))
    u_lines = axis_lines(u0, u1, u_steps, u_breaks)
    v_lines = axis_lines(v0, v1, v_steps, v_breaks)
    u_steps = len(u_lines) - 1
    v_steps = len(v_lines) - 1

    def _cell_center(i, j):
        return DB.UV(
            (u_lines[i] + u_lines[i + 1]) / 2.0, (v_lines[j] + v_lines[j + 1]) / 2.0
        )

    def _cell_area_ft2(i, j):
        return (u_lines[i + 1] - u_lines[i]) * u_len * (v_lines[j + 1] - v_lines[j]) * v_len

    # Pass 1 — probe only the cells that lie inside some neighbor's
    # bounding box. Probing is the expensive part (a Revit solid/curve
    # intersection per cell and neighbor solid), and on a real model most
    # of a face is nowhere near any neighbor, or the neighbor only grazes
    # one edge of it (a slab sitting on a wall, a wall meeting another).
    nearby = candidate_cells(
        frame,
        [n.bbox for n in neighbors],
        # Same reach as the probe segment in `_point_probes_into_solid`.
        max(tol_ft * 2.0, 0.001),
        u_lines,
        v_lines,
    )

    contact = set()
    soil = set()  # poured against the ground: excluded, reported apart
    touching_idxs = set()
    for (i, j), neighbor_idxs in nearby.items():
        uv = _cell_center(i, j)
        try:
            if not face.IsInside(uv):
                continue
            point = face.Evaluate(uv)
        except Exception:
            continue
        near = [neighbors[k] for k in neighbor_idxs]
        if _has_contact(point, normal, tol_ft, [n for n in near if n.category_key != "soil"]):
            contact.add((i, j))
            touching_idxs.update(neighbor_idxs)
        elif _has_contact(point, normal, tol_ft, [n for n in near if n.category_key == "soil"]):
            soil.add((i, j))
            touching_idxs.update(neighbor_idxs)

    if not contact and not soil:
        # Nothing actually touches this face: the caller keeps the whole
        # face with its exact edge loops, no grid needed.
        return {"no_contact": True}

    touching = [neighbors[k] for k in sorted(touching_idxs)]
    exact = _exact_free_region(
        face,
        normal,
        [n for n in touching if n.category_key != "soil"],
        [n for n in touching if n.category_key == "soil"],
        tol_ft,
    )
    if exact is not None:
        return exact

    # Pass 2 (fallback when the exact cut fails) — carve the free region
    # into panel rectangles on the full grid. Approximate: a sloped or
    # curved boundary comes out stair-stepped, in several panels.
    grid = [[False for _ in range(u_steps)] for _ in range(v_steps)]
    for j in range(v_steps):
        for i in range(u_steps):
            if (i, j) in contact or (i, j) in soil:
                continue
            try:
                grid[j][i] = face.IsInside(_cell_center(i, j))
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
        u_a = u_lines[i_start]
        u_b = u_lines[i_end]
        v_a = v_lines[j_start]
        v_b = v_lines[j_end]
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

    contact_area_m2 = sum(_cell_area_ft2(i, j) for i, j in contact) * FT2_TO_M2
    soil_area_m2 = sum(_cell_area_ft2(i, j) for i, j in soil) * FT2_TO_M2

    return {
        "included_faces": included_specs,
        "included_area_m2": included_area_m2,
        "contact_area_m2": contact_area_m2,
        "soil_area_m2": soil_area_m2,
        "approximate": True,
    }


def _subtract_solids(solid, others):
    """`solid` minus every solid in `others`; raises if Revit's boolean
    fails, None once nothing is left."""
    for other in others:
        solid = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
            solid, other, DB.BooleanOperationsType.Difference
        )
        if solid is None or solid.Volume < MIN_PANEL_VOLUME_FT3 * 1e-3:
            return None
    return solid


def _faces_on_plane(solid, normal, point):
    """Planar faces of `solid` facing along `normal` and lying on the
    plane through `point`."""
    if solid is None:
        return []
    found = []
    for f in solid.Faces:
        if not isinstance(f, DB.PlanarFace):
            continue
        if f.FaceNormal.DotProduct(normal) < 0.999:
            continue
        if abs(f.Origin.Subtract(point).DotProduct(normal)) > 1e-5:
            continue
        found.append(f)
    return found


# Retry offsets (ft) of the section outline for `_exact_free_region`: Revit
# booleans often fail when the face's edges lie flush on a neighbor's faces
# (a footing flush with a mat); growing or shrinking the outline by ~1 mm
# breaks that coincidence, and the pieces are offset back afterwards.
EXACT_CUT_GROW_FT = (0.0, 1.0 / 304.8, -1.0 / 304.8)


def _loop_area(loop, normal, signed=False):
    """Enclosed area (ft2) of a planar CurveLoop with plane `normal`;
    `signed`: positive when counterclockwise around `normal` (an outer
    loop in a face's own orientation), negative for its holes."""
    pts = []
    for curve in loop:
        pts.extend(list(curve.Tessellate())[:-1])
    total = DB.XYZ.Zero
    for k in range(len(pts)):
        total = total.Add(pts[k].CrossProduct(pts[(k + 1) % len(pts)]))
    area = total.DotProduct(normal) / 2.0
    return area if signed else abs(area)


def _section_pieces(section_faces, back, grow, normal):
    """Panel outlines (on the original face, in its orientation) and
    areas (ft2) of the free section faces."""
    pieces = []
    for f in section_faces:
        edge_loops = list(f.GetEdgesAsCurveLoops())
        if grow:
            # A loop about as thin as the growth itself is the grown margin
            # left over along a neighbor, not a real panel (and can't be
            # offset back): drop it.
            edge_loops = [
                l
                for l in edge_loops
                if _loop_area(l, normal) > 1.5 * abs(grow) * l.GetExactLength()
            ]
            if not edge_loops:
                continue
        loops = []
        for l in edge_loops:
            loop = DB.CurveLoop.CreateViaTransform(l, back)
            # The section faces away from the face: flip its loops to the
            # face's own orientation, like the no-contact path.
            loop.Flip()
            if grow:
                try:
                    loop = DB.CurveLoop.CreateViaOffset(loop, -grow, normal)
                except Exception:
                    pass  # keep the outline ~1 mm off: right shape, negligible area
            loops.append(loop)
        if grow:
            area = sum(_loop_area(l, normal, signed=True) for l in loops)
        else:
            area = f.Area
        pieces.append((List[DB.CurveLoop](loops), area))
    return pieces


def _exact_free_region(face, normal, contact_neighbors, soil_neighbors, tol_ft):
    """Exact version of the grid partition: the free part of a planar face
    is the section, `tol_ft` in front of it, of a thin slab minus the
    neighbors (and then the soil) - so a sloped or curved contact boundary
    is followed exactly and each connected free area becomes one panel
    with its real outline (holes included).

    The section is taken off the face itself so no boolean works on
    coplanar faces. Returns the same dict as `_partition_face_by_contact`,
    or None if Revit's boolean fails (caller falls back to the grid)."""
    contact_solids = [s for n in contact_neighbors for s in n.get_solids()]
    soil_solids = [s for n in soil_neighbors for s in n.get_solids()]
    lift = DB.Transform.CreateTranslation(normal.Multiply(tol_ft))
    back = DB.Transform.CreateTranslation(normal.Multiply(-tol_ft))
    base_point = face.Origin.Add(normal.Multiply(tol_ft))
    down = normal.Negate()

    for grow in EXACT_CUT_GROW_FT:
        try:
            loops = []
            for l in face.GetEdgesAsCurveLoops():
                loop = DB.CurveLoop.CreateViaTransform(l, lift)
                if grow:
                    loop = DB.CurveLoop.CreateViaOffset(loop, grow, normal)
                loops.append(loop)
            slab = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                List[DB.CurveLoop](loops), normal, tol_ft
            )
            free = _subtract_solids(slab, contact_solids)
            free_pieces = _section_pieces(
                _faces_on_plane(free, down, base_point), back, grow, normal
            )
            if soil_solids and free is not None:
                free = _subtract_solids(free, soil_solids)
                final_pieces = _section_pieces(
                    _faces_on_plane(free, down, base_point), back, grow, normal
                )
            else:
                final_pieces = free_pieces
        except Exception:
            continue

        free_area = sum(a for _, a in free_pieces)
        final_area = sum(a for _, a in final_pieces)
        return {
            "included_faces": [
                {
                    "loops": loops,
                    "direction": normal,
                    "area_m2": round(area * FT2_TO_M2, 6),
                }
                for loops, area in final_pieces
            ],
            "included_area_m2": final_area * FT2_TO_M2,
            "contact_area_m2": max(0.0, face.Area - free_area) * FT2_TO_M2,
            "soil_area_m2": max(0.0, free_area - final_area) * FT2_TO_M2,
        }
    return None


VERTICAL_NORMAL_Z = 0.1  # |normal.Z| below this = vertical face
JOINT_EPS_FT = 0.01  # ~3 mm step past a panel edge to sample what lies there
JOINT_LIFT_FT = 0.05  # ~1.5 cm above a soffit edge, to sample the side face zone
POINT_PROBE_FT = 0.003


def _point_in_solids(point, solids):
    """Is `point` inside any of `solids`? (a tiny vertical segment through
    it intersecting the solid)."""
    d = DB.XYZ(0, 0, POINT_PROBE_FT)
    try:
        line = DB.Line.CreateBound(point.Subtract(d), point.Add(d))
    except Exception:
        return False
    opts = DB.SolidCurveIntersectionOptions()
    for solid in solids:
        try:
            result = solid.IntersectWithCurve(line, opts)
            if result is not None and result.SegmentCount > 0:
                return True
        except Exception:
            continue
    return False


def _signed_area_2d(pts):
    s = 0.0
    n = len(pts)
    for k in range(n):
        x1, y1 = pts[k]
        x2, y2 = pts[(k + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _own_face_width(candidate, normal, point):
    """Horizontal width of `candidate`'s vertical planar face with outward
    `normal` whose plane passes through `point`, or None."""
    cache = candidate.__dict__.setdefault("_face_widths", [])
    if not cache:
        for solid in candidate.get_solids():
            for face in solid.Faces:
                if not isinstance(face, DB.PlanarFace):
                    continue
                n = face.FaceNormal
                if abs(n.Z) >= VERTICAL_NORMAL_Z:
                    continue
                box = _face_world_bbox(face)
                if box is None:
                    continue
                h = DB.XYZ(-n.Y, n.X, 0).Normalize()
                spans = [
                    x * h.X + y * h.Y
                    for x in (box[0], box[3])
                    for y in (box[1], box[4])
                ]
                cache.append((n, face.Origin, max(spans) - min(spans)))
    for n, origin, width in cache:
        if n.DotProduct(normal) > 0.99 and abs(point.Subtract(origin).DotProduct(n)) < 0.01:
            return width
    return None


def _joint_offset(mid, out, normal, candidate, neighbors, t, is_vertical):
    """Offset for one panel edge segment (see `adjust_panel_joints`):
    +t extends the panel past the edge, -t pulls it back, 0 leaves it."""
    own_seq = CATEGORY_MAP[candidate.category_key]["sequence"]
    own = candidate.get_solids()
    up = DB.XYZ(0, 0, 1)

    def in_any_neighbor(point, accept=None):
        for n in neighbors:
            if accept is not None and not accept(n):
                continue
            if _point_in_solids(point, n.get_solids()):
                return True
        return False

    if is_vertical:
        panel_zone = mid.Add(out.Multiply(JOINT_EPS_FT)).Add(normal.Multiply(t / 2.0))
        if out.Z > 0.9:
            # Top edge under a soffit: the slab soffit panel is continuous,
            # so the vertical panel stops under it. A beam soffit only wins
            # over elements poured after or with it (columns and walls keep
            # running up to the beam).
            if in_any_neighbor(
                panel_zone,
                lambda n: n.category_key == "slabs"
                or (
                    n.category_key == "beams"
                    and own_seq >= CATEGORY_MAP["beams"]["sequence"]
                ),
            ):
                return -t
            return 0.0
        if abs(out.Z) >= VERTICAL_NORMAL_Z:
            return 0.0
        # Side edge running into an element poured earlier (a beam or
        # wall reaching a column): that element's panel is continuous,
        # this one stops at its outer face.
        if in_any_neighbor(
            panel_zone,
            lambda n: not n.is_masonry
            and CATEGORY_MAP[n.category_key]["sequence"] < own_seq,
        ):
            return -t
        # Outside corner with another face of this same element: the
        # wider face's panel laps over the narrower one's edge.
        if _point_in_solids(
            mid.Add(out.Multiply(JOINT_EPS_FT)).Subtract(normal.Multiply(JOINT_EPS_FT)),
            own,
        ):
            return 0.0  # not an outside corner (internal or concave edge)
        side_zone = mid.Add(out.Multiply(t / 2.0)).Subtract(normal.Multiply(JOINT_LIFT_FT))
        if _point_in_solids(side_zone, own) or in_any_neighbor(side_zone):
            return 0.0  # the other face has no panel here
        width_here = _own_face_width(candidate, normal, mid)
        width_other = _own_face_width(candidate, out, mid)
        if width_here is None or width_other is None:
            return 0.0
        if width_here > width_other + 1e-3 or (
            abs(width_here - width_other) <= 1e-3 and abs(normal.X) >= abs(normal.Y)
        ):
            return t
        return 0.0

    # Soffit (beam/slab bottom), horizontal edge.
    if abs(out.Z) >= VERTICAL_NORMAL_Z:
        return 0.0
    beside = mid.Add(out.Multiply(t / 2.0)).Add(up.Multiply(JOINT_LIFT_FT))
    if not _point_in_solids(beside, own) and not in_any_neighbor(beside):
        return t  # a side panel stands here: reach its outer face
    if candidate.category_key == "beams":
        below = mid.Add(out.Multiply(JOINT_EPS_FT)).Subtract(up.Multiply(t / 2.0))
        if in_any_neighbor(
            below,
            lambda n: not n.is_masonry
            and CATEGORY_MAP[n.category_key]["sequence"] < own_seq,
        ):
            return -t  # beam bottom stops at the column/wall panel face
    return 0.0


def adjust_panel_joints(face_spec, candidate, neighbors, thickness_ft):
    """Shape a panel so it meets its neighbors like real formwork, without
    changing its takeoff area (`area_m2` stays the concrete contact area):

    - Soffit panels (beam/slab bottoms) extend by the panel thickness past
      every edge where a side panel stands, so they reach the side
      panel's outer face.
    - Vertical panels stop one panel thickness below a slab soffit (and
      below a beam soffit, for beams/slabs) so they end under that
      soffit's panel instead of overlapping it.
    - Panels of an element poured earlier (per CATEGORY_MAP "sequence":
      a column before the beams framing into it) run continuous; the
      later element's side and bottom panels stop at their outer face.
    - At an outside corner of one element, the wider face's panel laps
      over the narrower face's panel edge.

    Every edge is split where neighbor boundaries cross it, so a rule
    applies only along the stretch where it holds. Returns the original
    spec when nothing applies or the loop can't be rebuilt (non-line
    edges, strip thinner than a trim)."""
    normal = face_spec["direction"]
    is_vertical = abs(normal.Z) < VERTICAL_NORMAL_Z
    # Any downward-facing face is a soffit, including a stair flight's
    # sloped underside.
    is_soffit = (
        normal.Z < -VERTICAL_NORMAL_Z
        and not CATEGORY_MAP[candidate.category_key]["rests_on_ground"]
    )
    if not (is_vertical or is_soffit):
        return face_spec

    loops = list(face_spec["loops"])
    if not loops:
        return face_spec

    # Orthonormal 2D frame on the face plane.
    try:
        first = [c for c in loops[0]][0]
        origin = first.GetEndPoint(0)
        ex = first.GetEndPoint(1).Subtract(origin).Normalize()
        ey = normal.CrossProduct(ex).Normalize()
    except Exception:
        return face_spec

    def to2d(p):
        d = p.Subtract(origin)
        return (d.DotProduct(ex), d.DotProduct(ey))

    def to3d(q):
        return origin.Add(ex.Multiply(q[0])).Add(ey.Multiply(q[1]))

    polys = []
    for loop in loops:
        pts = []
        for curve in loop:
            if not isinstance(curve, DB.Line):
                return face_spec
            pts.append(to2d(curve.GetEndPoint(0)))
        polys.append(pts)

    neighbor_corners = [
        [to2d(DB.XYZ(c[0], c[1], c[2])) for c in box_corners(n.bbox)]
        for n in neighbors
        if n.bbox is not None
    ]

    new_polys = []
    changed = False
    for pts in polys:
        split = split_polygon_edges(pts, neighbor_corners)
        normals = edge_outward_normals(split, polys)
        offsets = []
        for k in range(len(split)):
            a = split[k]
            b = split[(k + 1) % len(split)]
            mid = to3d(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
            out = ex.Multiply(normals[k][0]).Add(ey.Multiply(normals[k][1]))
            offsets.append(
                _joint_offset(
                    mid, out, normal, candidate, neighbors, thickness_ft, is_vertical
                )
            )

        if any(offsets):
            new_pts = offset_polygon(split, normals, offsets)
            old_area = _signed_area_2d(pts)
            new_area = _signed_area_2d(new_pts)
            if old_area * new_area <= 0 or abs(new_area) < 1e-4:
                return face_spec  # a trim would collapse the panel
            new_polys.append(new_pts)
            changed = True
        else:
            new_polys.append(pts)

    if not changed:
        return face_spec

    try:
        new_loops = []
        for pts in new_polys:
            pts = drop_collinear_points(pts)
            loop = DB.CurveLoop()
            for k in range(len(pts)):
                loop.Append(
                    DB.Line.CreateBound(to3d(pts[k]), to3d(pts[(k + 1) % len(pts)]))
                )
            new_loops.append(loop)
    except Exception:
        return face_spec

    adjusted = dict(face_spec)
    adjusted["loops"] = List[DB.CurveLoop](new_loops)
    return adjusted


def classify_element_faces(candidate, neighbors, config, warnings, soil_for_face=None):
    """Classify every planar face of `candidate`'s solids into
    included/excluded buckets. Returns a dict summary plus the list of
    included face specs (for optional panel creation).

    `soil_for_face(face, normal)`, when given, returns a soil
    pseudo-neighbor for faces poured against the ground (or None); the
    part of the face touching it is excluded and reported as
    `excluded_soil_area_m2`."""

    tol_ft = config["contact_tolerance_ft"]
    grid_ft = config.get("contact_grid_ft", DEFAULT_CONTACT_GRID_FT)
    exclude_top = config["exclude_top_faces"]
    cat_info = CATEGORY_MAP[candidate.category_key]

    included_faces = []
    area_included = 0.0
    area_top = 0.0
    area_bottom_excluded = 0.0
    area_contact = 0.0
    area_soil = 0.0
    face_count = 0
    skipped_curved = 0
    approximate_faces = 0

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
            if soil_for_face is not None:
                soil = soil_for_face(face, normal)
                if soil is not None:
                    touching = touching + [soil]

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
                    area_soil += partition.get("soil_area_m2", 0.0)
                    if partition.get("approximate"):
                        approximate_faces += 1
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

    if approximate_faces:
        warnings.append(
            "Elemento {} ({}): {} cara(s) recortadas con el metodo aproximado "
            "(cuadricula), el corte exacto fallo".format(
                element_id_value(candidate.element.Id), cat_info["label"], approximate_faces
            )
        )

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
        "excluded_soil_area_m2": round(area_soil, 4),
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


def build_panel_solid(included_face, thickness_ft, formwork_material_id=None):
    """Extrude an included face's loops outward by the panel thickness,
    baking in the formwork material when it resolves to a real one."""
    if formwork_material_id is not None:
        try:
            solid_options = DB.SolidOptions(
                formwork_material_id, DB.ElementId.InvalidElementId
            )
            return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                included_face["loops"],
                included_face["direction"],
                thickness_ft,
                solid_options,
            )
        except Exception:
            pass
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        included_face["loops"], included_face["direction"], thickness_ft
    )


def solid_world_box(solid):
    """World-space box tuple of a Solid, or None."""
    try:
        bb = solid.GetBoundingBox()
        tr = bb.Transform
        pts = [
            tr.OfPoint(DB.XYZ(x, y, z))
            for x in (bb.Min.X, bb.Max.X)
            for y in (bb.Min.Y, bb.Max.Y)
            for z in (bb.Min.Z, bb.Max.Z)
        ]
    except Exception:
        return None
    return (
        min(p.X for p in pts),
        min(p.Y for p in pts),
        min(p.Z for p in pts),
        max(p.X for p in pts),
        max(p.Y for p in pts),
        max(p.Z for p in pts),
    )


# Panels whose boxes overlap by less than this in any axis only touch.
PANEL_OVERLAP_TOL_FT = -1e-4
MIN_PANEL_VOLUME_FT3 = 1e-6


def subtract_existing_panels(solid, box, placed_hash, placed_solids):
    """Remove from `solid` any volume already taken by a panel placed
    earlier this run (a safety net for joints the edge rules don't cover,
    e.g. a stair's sloped soffit meeting its landing, or a riser against
    its side board). Returns the trimmed solid, or None if nothing is
    left."""
    for idx in placed_hash.query(box, PANEL_OVERLAP_TOL_FT):
        try:
            trimmed = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                solid, placed_solids[idx], DB.BooleanOperationsType.Difference
            )
        except Exception:
            continue  # keep the untrimmed shape rather than lose the panel
        if trimmed is None or trimmed.Volume < MIN_PANEL_VOLUME_FT3:
            return None
        solid = trimmed
    return solid


def create_formwork_panel(
    doc,
    included_face,
    source_element,
    category_key,
    thickness_ft,
    formwork_material="",
    formwork_material_id=None,
    solid=None,
):
    """Create one DirectShape panel (Generic Models) for an included face.

    `formwork_material` is a free-text tag (e.g. "Madera", "Metalico") that
    identifies what the formwork itself is made of - it has no relation to
    the source element's own material and is never used to filter which
    elements get processed, only recorded on the panel for later takeoff.

    `formwork_material_id`, when it resolves to a real project material
    (see `find_material_id`), is baked into the panel's own geometry so it
    renders with that material's actual color/appearance in the model.

    `solid`, when given, is used as the panel's shape instead of extruding
    the face (e.g. one already trimmed against other panels)."""
    if solid is None:
        solid = build_panel_solid(included_face, thickness_ft, formwork_material_id)
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


def write_element_results(element, values):
    """Fill the formwork result parameters (formwork_params
    ELEMENT_RESULT_PARAMS) on a source element; any that isn't bound or
    is read-only is skipped."""
    for name, value in values.items():
        try:
            p = element.LookupParameter(name)
            if p is not None and not p.IsReadOnly:
                p.Set(value)
        except Exception:
            continue


SOIL_MARGIN_FT = 1.0  # soil block reaches this far around/below a face


def _soil_block(face_box, ground_z):
    """Soil pseudo-neighbor covering a face's surroundings up to the
    ground level, or None if the face is entirely above ground."""
    z0 = face_box[2] - SOIL_MARGIN_FT
    if ground_z <= face_box[2] + 1e-6:
        return None
    x0, y0 = face_box[0] - SOIL_MARGIN_FT, face_box[1] - SOIL_MARGIN_FT
    x1, y1 = face_box[3] + SOIL_MARGIN_FT, face_box[4] + SOIL_MARGIN_FT
    pts = [DB.XYZ(x0, y0, z0), DB.XYZ(x1, y0, z0), DB.XYZ(x1, y1, z0), DB.XYZ(x0, y1, z0)]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(pts[k], pts[(k + 1) % 4]))
    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ(0, 0, 1), ground_z - z0
    )
    return _SoilCandidate((x0, y0, z0, x1, y1, ground_z), solid)


SOIL_OVERRIDE_PARAM = "EF_Cara_Contra_Terreno"
SOIL_OVERRIDE_VALUES = {
    u"exterior": "exterior",
    u"ext": "exterior",
    u"interior": "interior",
    u"int": "interior",
    u"ambas": "both",
    u"ambos": "both",
    u"both": "both",
    u"ninguna": "none",
    u"ninguno": "none",
    u"no": "none",
    u"none": "none",
}


def wall_soil_override(element):
    """Manual EF_Cara_Contra_Terreno value of a wall: "exterior",
    "interior", "both", "none", or None (blank/unknown = automatic)."""
    try:
        p = element.LookupParameter(SOIL_OVERRIDE_PARAM)
        raw = p.AsString() if p is not None else None
    except Exception:
        raw = None
    if not raw:
        return None
    key = normalize_string(raw).lower().replace(u"í", u"i").strip()
    return SOIL_OVERRIDE_VALUES.get(key)


def _make_soil_detector(neighbor_pool, neighbor_hash, ground_z):
    """Returns candidate -> (face, normal) -> soil pseudo-neighbor | None.

    Poured against the ground (no formwork) below `ground_z`:
    - every face of a foundation;
    - vertical faces of other elements that look out of the building,
      i.e. a horizontal ray from the face hits no other structural
      element (a retaining wall's back face, a perimeter column below
      grade). Faces looking into a basement hit the elements across it
      and keep their formwork.

    A wall's EF_Cara_Contra_Terreno value overrides the automatic
    detection for that wall: the chosen face(s) are against the ground
    over their full height (whatever the ground level), the others get
    formwork."""
    boxes = [c.bbox for c in neighbor_pool if c.bbox is not None]
    if boxes:
        span = max(
            max(b[3] for b in boxes) - min(b[0] for b in boxes),
            max(b[4] for b in boxes) - min(b[1] for b in boxes),
        )
    else:
        span = 0.0
    ray_len = span + 10.0

    def looks_outside(candidate, face_box, normal):
        h_len = (normal.X ** 2 + normal.Y ** 2) ** 0.5
        if h_len < 1e-6:
            return False
        d = (normal.X / h_len, normal.Y / h_len)
        z = min((face_box[2] + face_box[5]) / 2.0, (face_box[2] + ground_z) / 2.0)
        start = (
            (face_box[0] + face_box[3]) / 2.0 + d[0] * 0.05,
            (face_box[1] + face_box[4]) / 2.0 + d[1] * 0.05,
        )
        try:
            ray = DB.Line.CreateBound(
                DB.XYZ(start[0], start[1], z),
                DB.XYZ(start[0] + d[0] * ray_len, start[1] + d[1] * ray_len, z),
            )
        except Exception:
            return False
        opts = DB.SolidCurveIntersectionOptions()
        for _, idx in neighbor_hash.query_ray(start, d, ray_len, z):
            other = neighbor_pool[idx]
            if other is candidate:
                continue
            for solid in other.get_solids():
                try:
                    hit = solid.IntersectWithCurve(ray, opts)
                    if hit is not None and hit.SegmentCount > 0:
                        return False
                except Exception:
                    continue
        return True

    def for_candidate(candidate):
        is_foundation = candidate.category_key == "foundations"
        override = None
        exterior_dir = None
        if candidate.category_key == "walls":
            override = wall_soil_override(candidate.element)
            try:
                exterior_dir = candidate.element.Orientation
            except Exception:
                exterior_dir = None

        def manual_soil_for_face(face, normal):
            if override == "none" or abs(normal.Z) >= VERTICAL_NORMAL_Z:
                return None
            side = None
            if exterior_dir is not None:
                d = normal.DotProduct(exterior_dir)
                side = "exterior" if d > 0.5 else ("interior" if d < -0.5 else None)
            if side is None or override not in ("both", side):
                return None  # wall ends, or the face the user left open
            face_box = _face_world_bbox(face)
            if face_box is None:
                return None
            try:
                return _soil_block(face_box, face_box[5] + SOIL_MARGIN_FT)
            except Exception:
                return None

        if override is not None:
            return manual_soil_for_face

        def soil_for_face(face, normal):
            face_box = _face_world_bbox(face)
            if face_box is None or face_box[2] >= ground_z - 1e-6:
                return None
            if not is_foundation:
                if abs(normal.Z) >= VERTICAL_NORMAL_Z:
                    return None
                if not looks_outside(candidate, face_box, normal):
                    return None
            try:
                return _soil_block(face_box, ground_z)
            except Exception:
                return None

        return soil_for_face

    return for_candidate


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

    # Pour order (CATEGORY_MAP "sequence"): an element poured earlier gets
    # its panels placed first, so later panels are the ones trimmed where
    # they would overlap (see `subtract_existing_panels`).
    requested = _build_candidates(elements_by_category)
    # Masonry walls get no formwork; they only act as neighbors.
    masonry = [c for c in requested if c.is_masonry]
    candidates = sorted(
        [c for c in requested if not c.is_masonry],
        key=lambda c: CATEGORY_MAP[c.category_key]["sequence"],
    )
    placed_hash = SpatialHash(NEIGHBOR_HASH_CELL_FT)
    placed_solids = []

    neighbor_pool = list(candidates) + masonry
    pool_ids = set(element_id_value(c.element.Id) for c in neighbor_pool)
    if context_elements_by_category:
        for candidate in _build_candidates(context_elements_by_category):
            if element_id_value(candidate.element.Id) in pool_ids:
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

    soil_for = None
    if config.get("pour_against_soil"):
        soil_for = _make_soil_detector(
            neighbor_pool, neighbor_hash, config.get("ground_elevation_ft", 0.0)
        )

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

        classification = classify_element_faces(
            candidate,
            neighbors,
            config,
            warnings,
            soil_for(candidate) if soil_for is not None else None,
        )

        if config["create_geometry"]:
            formwork_material = config.get("formwork_materials", {}).get(
                candidate.category_key, ""
            )
            if formwork_material not in material_id_cache:
                material_id_cache[formwork_material] = find_material_id(
                    doc, formwork_material
                )
            formwork_material_id = material_id_cache[formwork_material]

            # Larger panels first, so within one element the big boards run
            # through and the small ones (risers, strips) fit around them.
            specs = sorted(
                classification["included_faces"], key=lambda s: -s["area_m2"]
            )
            element_panels = 0
            for face_spec in specs:
                try:
                    face_spec = adjust_panel_joints(
                        face_spec, candidate, neighbors, config["panel_thickness_ft"]
                    )
                except Exception:
                    pass  # keep the plain face-shaped panel
                try:
                    solid = build_panel_solid(
                        face_spec, config["panel_thickness_ft"], formwork_material_id
                    )
                    box = solid_world_box(solid)
                    if box is not None:
                        solid = subtract_existing_panels(
                            solid, box, placed_hash, placed_solids
                        )
                        if solid is None:
                            continue  # fully covered by earlier panels
                        box = solid_world_box(solid) or box
                    create_formwork_panel(
                        doc,
                        face_spec,
                        candidate.element,
                        candidate.category_key,
                        config["panel_thickness_ft"],
                        formwork_material,
                        formwork_material_id,
                        solid=solid,
                    )
                    if box is not None:
                        placed_hash.insert(len(placed_solids), box)
                        placed_solids.append(solid)
                    panels_created += 1
                    element_panels += 1
                except Exception as e:
                    warnings.append(
                        "Elemento {}: no se pudo crear el panel de una cara ({} m2) - {}".format(
                            element_id_value(candidate.element.Id),
                            face_spec["area_m2"],
                            str(e),
                        )
                    )

            write_element_results(
                candidate.element,
                {
                    "EF_Area_Encofrado_m2": classification["included_area_m2"],
                    "EF_Paneles": element_panels,
                    "EF_Material_Encofrado": formwork_material or "",
                },
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
                "excluded_soil_area_m2": classification["excluded_soil_area_m2"],
                "face_count": classification["face_count"],
            }
        )

    return {
        "elements": element_results,
        "category_totals": category_totals,
        "panels_created": panels_created,
        "element_count": len(element_results),
        "excluded_masonry_walls": len(masonry),
    }
