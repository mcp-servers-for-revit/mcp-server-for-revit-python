# -*- coding: UTF-8 -*-
"""
Text-to-Plan Floor Plan Builder for Revit MCP (Maket-style)

Builds a REAL floor plan (Level, Walls, Rooms, Doors) from a structured room
program. The "text understanding" step (turning a plain-English brief like
"3-bed ranch, open kitchen/living, one bath" into the room list below) is
expected to already be done by the calling LLM - this module only builds
geometry from a spec, it never parses natural language itself.

Space planning (packing the room program into the boundary, corridor
routing) is NOT reimplemented here - it's reused from layout_algorithm.py,
the same solver behind the "Generate Layout" pushbutton. That tool stops at
drawing a diagrammatic reference (filled regions in a Drafting View); this
route takes the same kind of program and actually constructs Walls, Rooms,
and Doors in the model.

Design aid only - not a code-compliance tool. Zoning checks here are a
simple setback/envelope arithmetic check, not a substitute for a real
zoning/code review.
"""

from utils import get_element_name, set_element_name, element_id_value
from layout_algorithm import build_layout, check_egress_heuristics
from pyrevit import routes, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Program spec normalization
# ---------------------------------------------------------------------------

def _normalize_room_spec(rooms):
    """Splits the caller's flat room list into build_layout's two shapes:
    enclosed_program (exact width/depth, gets real walls) and open_program
    (target_area only, fills leftover space - e.g. open kitchen/living).
    Tolerant of missing optional keys, same spirit as the existing
    ProgramForm.read_enclosed_program/read_open_program in the Generate
    Layout pushbutton."""
    enclosed, open_zones = [], []
    for r in rooms:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        category = (r.get("category") or name).strip()
        try:
            qty = max(int(r.get("qty", 1)), 1)
        except (TypeError, ValueError):
            qty = 1
        adjacency = (r.get("adjacency") or "").strip()

        if "width" in r and "depth" in r:
            try:
                width, depth = float(r["width"]), float(r["depth"])
            except (TypeError, ValueError):
                continue
            enclosed.append({
                "name": name, "category": category,
                "width": width, "max_width": float(r.get("max_width", width)),
                "depth": depth, "max_depth": float(r.get("max_depth", depth)),
                "qty": qty, "adjacency": adjacency,
            })
        elif "target_area" in r:
            try:
                target_area = float(r["target_area"])
            except (TypeError, ValueError):
                continue
            open_zones.append({
                "name": name, "category": category,
                "target_area": target_area,
                "min_width": float(r.get("min_width", 6.0)),
                "qty": qty, "adjacency": adjacency,
            })
        # Rows with neither width/depth nor target_area are silently
        # skipped - same "never let a malformed row blow up the tool"
        # tolerance the pushbutton form already relies on.
    return enclosed, open_zones


def _check_zoning(boundary_w, boundary_h, lot, setback):
    """Heuristic-only compliance check (Maket's "zoning compliance" step) -
    returns a list of warning strings, never raises and never blocks
    generation. Only runs if the caller supplied `lot` dimensions; a bare
    `setback` with no `lot` can't be checked against anything."""
    warnings = []
    if not lot:
        if setback:
            warnings.append(
                "Setback given without lot dimensions - buildable envelope was not verified.")
        return warnings

    try:
        lot_w, lot_d = float(lot.get("width", 0)), float(lot.get("depth", 0))
        side = float(setback.get("side", 0))
        front = float(setback.get("front", 0))
        rear = float(setback.get("rear", 0))
    except (TypeError, ValueError):
        warnings.append("Lot/setback values were not numeric - buildable envelope was not verified.")
        return warnings

    required_w = boundary_w + 2 * side
    required_d = boundary_h + front + rear
    if required_w > lot_w + 1e-6:
        warnings.append(
            "Building width + side setbacks ({:.1f}') exceeds lot width ({:.1f}').".format(required_w, lot_w))
    if required_d > lot_d + 1e-6:
        warnings.append(
            "Building depth + front/rear setbacks ({:.1f}') exceeds lot depth ({:.1f}').".format(required_d, lot_d))
    return warnings


# ---------------------------------------------------------------------------
# Level / type lookups
# ---------------------------------------------------------------------------

def _get_or_create_level(doc, level_name):
    levels = DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements()
    for level in levels:
        if get_element_name(level) == level_name:
            return level, False
    base_elev = 0.0
    if levels:
        base_elev = max(lvl.Elevation for lvl in levels)
    new_level = DB.Level.Create(doc, base_elev)
    set_element_name(new_level, level_name)
    return new_level, True


def _find_wall_type(doc, type_name):
    wall_types = list(DB.FilteredElementCollector(doc).OfClass(DB.WallType).ToElements())
    if type_name:
        for wt in wall_types:
            if get_element_name(wt) == type_name:
                return wt
    default_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.WallType)
    default_type = doc.GetElement(default_id) if default_id else None
    return default_type or (wall_types[0] if wall_types else None)


def _find_door_symbol(doc, type_name):
    symbols = list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilySymbol)
        .OfCategory(DB.BuiltInCategory.OST_Doors)
        .ToElements()
    )
    if not symbols:
        return None
    if type_name:
        for sym in symbols:
            if get_element_name(sym) == type_name:
                return sym
    return symbols[0]


# ---------------------------------------------------------------------------
# Geometry: rectangles -> a real, deduplicated wall network
# ---------------------------------------------------------------------------

def _room_edge_lines(room):
    """Each of a room's 4 edges as (axis, coord, lo, hi): axis "v" is a
    vertical line x=coord spanning y in [lo, hi]; "h" is a horizontal line
    y=coord spanning x in [lo, hi]. This is keyed by the LINE an edge sits
    on, not the edge's own endpoints - two rooms of different depths
    sharing a boundary (e.g. a 12'-deep room next to an 11'-deep one) put
    different-length edges on the *same* line, and only grouping by line
    lets those get merged into one wall instead of two overlapping ones."""
    x, y, w, h = room["x"], room["y"], room["w"], room["h"]
    return [
        ("v", x, y, y + h),
        ("v", x + w, y, y + h),
        ("h", y, x, x + w),
        ("h", y + h, x, x + w),
    ]


def _line_key(axis, coord, tol_digits=3):
    return (axis, round(coord, tol_digits))


def _merge_intervals(intervals, tol=1e-3):
    """Merges overlapping or touching (lo, hi) spans on the same line into
    the minimal covering set. This is what actually fixes the shared-wall
    problem: exact-edge dedup only catches identical edges, but shelf-
    packed rooms routinely produce edges that partially overlap on a
    shared line without matching endpoint-for-endpoint."""
    if not intervals:
        return []
    ivs = sorted(tuple(sorted(iv)) for iv in intervals)
    merged = [list(ivs[0])]
    for lo, hi in ivs[1:]:
        if lo <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _midpoint(p0, p1):
    return ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)


def _dist(p0, p1):
    return ((p0[0] - p1[0]) ** 2 + (p0[1] - p1[1]) ** 2) ** 0.5


def _is_boundary_edge(p0, p1, boundary_w, boundary_h, tol=1e-3):
    """True if an edge lies on the outer site boundary (gets the exterior
    wall type) as opposed to any interior edge - including one facing the
    corridor, which is still an interior partition, not an exterior wall."""
    xs, ys = (p0[0], p1[0]), (p0[1], p1[1])
    on_left = all(abs(x - 0.0) < tol for x in xs)
    on_right = all(abs(x - boundary_w) < tol for x in xs)
    on_bottom = all(abs(y - 0.0) < tol for y in ys)
    on_top = all(abs(y - boundary_h) < tol for y in ys)
    return on_left or on_right or on_bottom or on_top


def _build_wall_network(doc, rooms, level, ext_type, int_type, boundary_w, boundary_h, wall_height, tol=1e-3):
    """Groups every room edge by the line it sits on (same axis + same
    coordinate), merges overlapping/touching spans on each line into the
    minimal covering set (_merge_intervals), and builds ONE wall per
    merged span. Two rooms of different depths sharing a boundary get a
    single party wall spanning their combined extent, instead of two
    overlapping walls a foot apart.

    Returns:
      wall_ids: flat list of created wall ElementIds
      room_wall_edges: list parallel to `rooms`, each a list of
        (wall, p0, p1) for that room's 4 edges - p0/p1 are the ROOM's own
        edge endpoints (not the merged wall's full extent), used as the
        door host point so a door lands next to this specific room, not
        centered on a wall that may span several rooms.
    """
    groups = {}  # (axis, coord) -> list of (lo, hi), one per room edge on that line
    for room in rooms:
        for axis, coord, lo, hi in _room_edge_lines(room):
            groups.setdefault(_line_key(axis, coord), []).append((lo, hi))

    # (axis, rounded coord) -> list of (merged_lo, merged_hi, wall)
    built = {}
    wall_ids = []
    for (axis, coord), intervals in groups.items():
        is_boundary = (
            (axis == "v" and (abs(coord - 0.0) < tol or abs(coord - boundary_w) < tol)) or
            (axis == "h" and (abs(coord - 0.0) < tol or abs(coord - boundary_h) < tol))
        )
        wall_type = ext_type if is_boundary else int_type
        if wall_type is None:
            continue
        segments = []
        for lo, hi in _merge_intervals(intervals, tol):
            if axis == "v":
                p0, p1 = (coord, lo), (coord, hi)
            else:
                p0, p1 = (lo, coord), (hi, coord)
            curve = DB.Line.CreateBound(DB.XYZ(p0[0], p0[1], 0), DB.XYZ(p1[0], p1[1], 0))
            wall = DB.Wall.Create(doc, curve, wall_type.Id, level.Id, wall_height, 0.0, False, False)
            wall_ids.append(wall.Id)
            segments.append((lo, hi, wall))
        built[(axis, coord)] = segments

    room_wall_edges = []
    for room in rooms:
        edges = []
        for axis, coord, lo, hi in _room_edge_lines(room):
            wall = None
            # The merge step guarantees every input interval is fully
            # contained in exactly one merged segment on its line, so this
            # lookup always finds a match once a wall was actually built.
            for seg_lo, seg_hi, seg_wall in built.get(_line_key(axis, coord), []):
                if seg_lo <= lo + tol and seg_hi >= hi - tol:
                    wall = seg_wall
                    break
            p0 = (coord, lo) if axis == "v" else (lo, coord)
            p1 = (coord, hi) if axis == "v" else (hi, coord)
            edges.append((wall, p0, p1))
        room_wall_edges.append(edges)

    return wall_ids, room_wall_edges


def _create_rooms(doc, rooms, level):
    room_ids = []
    for room in rooms:
        cx = room["x"] + room["w"] / 2.0
        cy = room["y"] + room["h"] / 2.0
        new_room = doc.Create.NewRoom(level, DB.UV(cx, cy))
        set_element_name(new_room, room["name"])
        room_ids.append(new_room.Id)
    return room_ids


def _place_doors(doc, layout, room_wall_edges, door_symbol, level, boundary_w, boundary_h):
    """One door per enclosed room, hosted at the midpoint of whichever edge
    is closest to the corridor - preferring an interior edge (one that
    actually borders circulation space or another room) over an exterior
    one. A wall too short/otherwise invalid for a door instance is skipped,
    not fatal to the rest of the plan."""
    if door_symbol is None:
        return []
    if not door_symbol.IsActive:
        door_symbol.Activate()
        doc.Regenerate()

    corridor = layout["corridor"]
    corridor_center = (corridor["x"] + corridor["w"] / 2.0, corridor["y"] + corridor["h"] / 2.0)

    door_ids = []
    for edges in room_wall_edges:
        interior_edges = [e for e in edges if e[0] is not None and not _is_boundary_edge(e[1], e[2], boundary_w, boundary_h)]
        candidates = interior_edges or [e for e in edges if e[0] is not None]
        if not candidates:
            continue
        wall, p0, p1 = min(candidates, key=lambda e: _dist(_midpoint(e[1], e[2]), corridor_center))
        mid = _midpoint(p0, p1)
        point = DB.XYZ(mid[0], mid[1], 0)
        try:
            door = doc.Create.NewFamilyInstance(
                point, door_symbol, wall, level, DB.Structure.StructuralType.NonStructural
            )
            door_ids.append(door.Id)
        except Exception:
            continue
    return door_ids


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

def register_text_to_plan_routes(api):
    """Register the text-to-plan floor plan builder route"""

    @api.route("/generate_floor_plan/", methods=["POST"])
    def generate_floor_plan(doc, request):
        """
        Build a real floor plan (walls, rooms, doors) from a room program.

        Expected request data:
        {
            "level_name": "Level 1",
            "boundary": {"width": 40.0, "depth": 35.0},
            "lot": {"width": 60.0, "depth": 40.0},           # optional
            "setback": {"front": 20.0, "side": 5.0, "rear": 10.0},  # optional
            "corridor_width": 5.0,
            "wall_height": 10.0,
            "exterior_wall_type": "Generic - 8\"",           # optional, falls back to doc default
            "interior_wall_type": "Generic - 5\"",           # optional, falls back to doc default
            "door_type_name": "Single-Flush 32\" x 84\"",    # optional, falls back to first loaded door type
            "rooms": [
                {"name": "Bedroom 1", "category": "Bedroom", "width": 12, "depth": 12, "qty": 1},
                {"name": "Bathroom", "category": "Bath", "width": 7, "depth": 8, "qty": 1},
                {"name": "Kitchen/Living", "category": "Open", "target_area": 320, "min_width": 14, "qty": 1}
            ]
        }
        Rooms with width+depth become exact-size walled rooms (enclosed_program).
        Rooms with target_area fill leftover space (open_program), e.g. open living areas.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            if not request or not request.data:
                return routes.make_response(
                    data={"error": "No data provided or invalid request format"}, status=400
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data
            if not isinstance(data, dict):
                return routes.make_response(
                    data={"error": "Invalid data format - expected JSON object"}, status=400
                )

            boundary = data.get("boundary") or {}
            if "width" not in boundary or "depth" not in boundary:
                return routes.make_response(
                    data={"error": "boundary.width and boundary.depth are required"}, status=400
                )
            boundary_w, boundary_h = float(boundary["width"]), float(boundary["depth"])

            rooms = data.get("rooms") or []
            if not rooms:
                return routes.make_response(data={"error": "No rooms provided"}, status=400)

            enclosed_program, open_program = _normalize_room_spec(rooms)
            if not enclosed_program and not open_program:
                return routes.make_response(
                    data={"error": "No valid rooms - each room needs either width+depth or target_area"},
                    status=400,
                )

            zoning_warnings = _check_zoning(boundary_w, boundary_h, data.get("lot"), data.get("setback", {}))

            # --- Space planning: reuse the existing solver, don't reinvent it ---
            layout = build_layout(
                boundary_w=boundary_w, boundary_h=boundary_h,
                enclosed_program=enclosed_program, open_program=open_program,
                corridor_width=float(data.get("corridor_width", 5.0)),
            )
            egress_flags = check_egress_heuristics(layout, door_points=[])

            level_name = data.get("level_name", "Level 1")
            wall_height = float(data.get("wall_height", 10.0))
            door_type_name = data.get("door_type_name")

            # Read-only lookups are safe outside a transaction; Level.Create
            # (inside _get_or_create_level, when no matching level exists yet)
            # is NOT - Revit requires every document edit to happen inside an
            # active Transaction, so that call has to move inside the one
            # below rather than run before it starts.
            ext_type = _find_wall_type(doc, data.get("exterior_wall_type"))
            int_type = _find_wall_type(doc, data.get("interior_wall_type"))
            door_symbol = _find_door_symbol(doc, door_type_name)

            if ext_type is None or int_type is None:
                return routes.make_response(
                    data={"error": "No WallType found in the document to build with"}, status=404
                )

            t = DB.Transaction(doc, "Generate floor plan from program")
            t.Start()
            try:
                level, level_created = _get_or_create_level(doc, level_name)
                wall_ids, room_wall_edges = _build_wall_network(
                    doc, layout["rooms"], level, ext_type, int_type, boundary_w, boundary_h, wall_height
                )
                doc.Regenerate()  # walls must exist & bound space before Room.Create can find it
                room_ids = _create_rooms(doc, layout["rooms"], level)
                door_ids = _place_doors(doc, layout, room_wall_edges, door_symbol, level, boundary_w, boundary_h)
                t.Commit()
            except Exception:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise

            logger.info(
                "generate_floor_plan: %s walls, %s rooms, %s doors created",
                len(wall_ids), len(room_ids), len(door_ids),
            )

            return routes.make_response(data={
                "status": "success",
                "level": level_name,
                "level_created": level_created,
                "walls_created": len(wall_ids),
                "wall_ids": [element_id_value(i) for i in wall_ids],
                "rooms_created": len(room_ids),
                "room_ids": [element_id_value(i) for i in room_ids],
                "doors_placed": len(door_ids),
                "door_ids": [element_id_value(i) for i in door_ids],
                "unplaced_rooms": [r["name"] for r in layout["unplaced"]],
                "unallocated_area_count": len(layout["leftover"]),
                "zoning_warnings": zoning_warnings,
                "egress_flags": egress_flags,
                "disclaimer": (
                    "Design aid only - not a code compliance review. Verify egress, "
                    "corridor width, setbacks, and occupancy against the applicable "
                    "code before use."
                ),
            })

        except Exception as e:
            logger.error("Failed to generate floor plan: %s", str(e))
            error_trace = traceback.format_exc()
            return routes.make_response(data={"error": str(e), "traceback": error_trace}, status=500)

    logger.info("Text-to-plan routes registered successfully")
