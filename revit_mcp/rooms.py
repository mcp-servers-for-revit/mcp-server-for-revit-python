# -*- coding: UTF-8 -*-
"""
Rooms Module for Revit MCP
Create a Room element at a 2D point on a level. The point must lie inside an
area enclosed by room-bounding elements (walls, room separators) — otherwise
Revit refuses to create the room and we return status='not_in_enclosed_area'.

Units: x/y/offsets are in MILLIMETRES on the API; converted internally to
Revit's decimal feet via UnitUtils.

Ported from Sparx mcp-servers-for-revit's CreateRoomCommand (Apache-2.0
licensed C# source at commandset/Services/Architecture/CreateRoomEventHandler.cs)
into our IronPython pyRevit Routes pattern. Single-room creation only in v1;
batch can be added later if a real workflow demands it.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value
import json
import traceback
import logging

logger = logging.getLogger(__name__)

FT_TO_MM = 304.8


def _parse_json_request(request):
    if not request or not request.data:
        raise ValueError("No data provided")
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _mm_to_feet(value_mm):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.UnitTypeId.Millimeters)
    except AttributeError:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.DisplayUnitType.DUT_MILLIMETERS)


def _resolve_level(doc, level_id=None, elevation_mm=None):
    """
    Pick the target level. Priority:
      1. Explicit level_id (must exist)
      2. Nearest level to given elevation_mm
      3. Lowest-elevation level in the project (fallback)

    Returns (Level, reason_string) or (None, error_message).
    """
    if level_id is not None:
        try:
            lid = DB.ElementId(int(level_id))
        except Exception:
            return None, "level_id must be an integer"
        el = doc.GetElement(lid)
        if el is None or not isinstance(el, DB.Level):
            return None, "level_id {} not found or not a Level".format(level_id)
        return el, "explicit_level_id"

    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    if not levels:
        return None, "no_levels"

    if elevation_mm is not None:
        try:
            target_ft = _mm_to_feet(float(elevation_mm))
        except (TypeError, ValueError):
            return None, "elevation_mm must be a number"
        nearest = None
        best_dist = None
        for lv in levels:
            d = abs(lv.Elevation - target_ft)
            if best_dist is None or d < best_dist:
                best_dist = d
                nearest = lv
        return nearest, "nearest_to_elevation"

    levels.sort(key=lambda l: l.Elevation)
    return levels[0], "fallback_lowest_level"


def _collect_existing_room_numbers(doc):
    """Return a set of strings of every existing room number in the project."""
    out = set()
    for r in DB.FilteredElementCollector(doc) \
                .OfCategory(DB.BuiltInCategory.OST_Rooms) \
                .WhereElementIsNotElementType():
        try:
            n = r.Number
            if n:
                out.add(n)
        except Exception:
            continue
    return out


def _next_available_number(existing):
    """Smallest positive integer-string not in `existing`. Handles legacy 'Room 101' style by
    extracting trailing digits and incrementing past the max."""
    max_num = 0
    for s in existing:
        # Try whole-string parse first
        try:
            v = int(s)
            if v > max_num:
                max_num = v
            continue
        except ValueError:
            pass
        # Trailing-digits fallback
        tail = []
        for ch in reversed(s):
            if ch.isdigit():
                tail.append(ch)
            elif tail:
                break
        if tail:
            try:
                v = int("".join(reversed(tail)))
                if v > max_num:
                    max_num = v
            except ValueError:
                pass
    candidate = max_num + 1
    for _ in range(10000):
        c = str(candidate)
        if c not in existing:
            return c
        candidate += 1
    return str(max_num + 1)


def _unique_number_from(requested, existing):
    """Return a unique number; if `requested` is taken, increment the trailing numeric portion
    until free. Falls back to suffix letters A-Z, then -2,-3,... if needed."""
    if not requested:
        return _next_available_number(existing)
    if requested not in existing:
        return requested

    # Extract last numeric run for incrementing
    last_end = -1
    last_start = -1
    for i in range(len(requested) - 1, -1, -1):
        if requested[i].isdigit():
            if last_end == -1:
                last_end = i
            last_start = i
        elif last_end != -1:
            break
    if last_start != -1:
        prefix = requested[:last_start]
        numpart = requested[last_start:last_end + 1]
        suffix = requested[last_end + 1:]
        try:
            n = int(numpart)
            width = len(numpart)
            for i in range(1, 1001):
                cand = prefix + str(n + i).rjust(width, "0") + suffix
                if cand not in existing:
                    return cand
        except ValueError:
            pass

    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        cand = requested + c
        if cand not in existing:
            return cand
    for i in range(2, 1001):
        cand = "{}-{}".format(requested, i)
        if cand not in existing:
            return cand
    return requested  # last resort


def _set_string_param(elem, bip, value):
    """Set a BuiltInParameter string if non-empty and writable. No-op otherwise."""
    if not value:
        return
    p = elem.get_Parameter(bip)
    if p is not None and not p.IsReadOnly:
        try:
            p.Set(value)
        except Exception:
            pass


def _set_double_param_mm(elem, bip, value_mm):
    """Set a BuiltInParameter double (Revit's internal feet) from a mm input."""
    if value_mm is None:
        return
    p = elem.get_Parameter(bip)
    if p is not None and not p.IsReadOnly:
        try:
            p.Set(_mm_to_feet(value_mm))
        except Exception:
            pass


def _set_elemid_param(elem, bip, value_id):
    """Set a BuiltInParameter ElementId from an integer."""
    if value_id is None:
        return
    p = elem.get_Parameter(bip)
    if p is not None and not p.IsReadOnly:
        try:
            p.Set(DB.ElementId(int(value_id)))
        except Exception:
            pass


def register_rooms_routes(api):
    """Register room-creation routes."""

    @api.route("/create_room/", methods=["POST"])
    def create_room(doc, request):
        """
        Create a Room element at a 2D point.

        Expected payload (all coordinates / offsets in MILLIMETRES):
        {
            "x_mm":            -10000.0,   # required, location X
            "y_mm":             -5000.0,   # required, location Y
            "level_id":         311,       # optional, explicit Level element id
            "elevation_mm":     null,      # optional, alternative to level_id (uses nearest Level)
            "name":             "Office",  # optional, default "Room"
            "number":           "101",     # optional, auto-generated or deduped if collides
            "upper_limit_id":   null,      # optional, Level ElementId for upper limit
            "limit_offset_mm":  null,      # optional, offset above upper limit
            "base_offset_mm":   0,         # optional, offset above base level
            "department":       null,      # optional
            "comments":         null       # optional
        }

        If both level_id and elevation_mm are omitted, the lowest-elevation
        level in the project is used.

        Returns status='success' with the created room metadata, OR
        status='not_in_enclosed_area' if the point isn't inside walls/separators.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)

            # Required fields
            if "x_mm" not in data or "y_mm" not in data:
                return routes.make_response(data={
                    "error": "Missing required field(s): x_mm and y_mm (numbers, millimetres)"
                }, status=400)
            try:
                x_mm = float(data["x_mm"])
                y_mm = float(data["y_mm"])
            except (TypeError, ValueError):
                return routes.make_response(data={
                    "error": "x_mm and y_mm must be numbers"
                }, status=400)

            # Resolve level
            level, level_reason = _resolve_level(
                doc,
                level_id=data.get("level_id"),
                elevation_mm=data.get("elevation_mm"),
            )
            if level is None:
                return routes.make_response(data={
                    "status": "level_not_found" if level_reason != "no_levels" else "no_levels",
                    "error": level_reason,
                })

            # Prep name + number deduplication BEFORE the transaction so we
            # don't trigger Revit's duplicate-number warning dialog.
            name = data.get("name") or "Room"
            requested_number = data.get("number")
            existing_numbers = _collect_existing_room_numbers(doc)
            assigned_number = _unique_number_from(requested_number, existing_numbers)

            x_ft = _mm_to_feet(x_mm)
            y_ft = _mm_to_feet(y_mm)
            uv = DB.UV(x_ft, y_ft)

            room = None
            with DB.Transaction(doc, "MCP: Create Room") as t:
                t.Start()
                try:
                    room = doc.Create.NewRoom(level, uv)
                except Exception as create_err:
                    t.RollBack()
                    return routes.make_response(data={
                        "status": "create_failed",
                        "error": str(create_err),
                        "level_name": normalize_string(level.Name),
                        "level_id": element_id_value(level.Id),
                    })

                if room is None:
                    # Revit returns None when the point isn't inside an enclosed area
                    t.RollBack()
                    return routes.make_response(data={
                        "status": "not_in_enclosed_area",
                        "error": "The point ({:.1f}, {:.1f}) is not inside a room-bounded area on level '{}'. "
                                 "Make sure walls or room separators fully enclose the location.".format(
                                     x_mm, y_mm, normalize_string(level.Name)),
                        "level_name": normalize_string(level.Name),
                        "level_id": element_id_value(level.Id),
                    })

                # Set the name + number first (must be done before any other parameter
                # to ensure unique number takes effect before Revit auto-validates).
                _set_string_param(room, DB.BuiltInParameter.ROOM_NAME, name)
                _set_string_param(room, DB.BuiltInParameter.ROOM_NUMBER, assigned_number)
                _set_elemid_param(room, DB.BuiltInParameter.ROOM_UPPER_LEVEL, data.get("upper_limit_id"))
                _set_double_param_mm(room, DB.BuiltInParameter.ROOM_UPPER_OFFSET, data.get("limit_offset_mm"))
                _set_double_param_mm(room, DB.BuiltInParameter.ROOM_LOWER_OFFSET, data.get("base_offset_mm"))
                _set_string_param(room, DB.BuiltInParameter.ROOM_DEPARTMENT, data.get("department"))
                _set_string_param(room, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, data.get("comments"))

                t.Commit()

            # Revit stores Area in ft² internally; expose m² for human use
            area_ft2 = float(room.Area)
            area_m2 = area_ft2 * 0.092903   # 1 ft² = 0.092903 m²
            perimeter_ft = float(room.Perimeter)
            perimeter_m = perimeter_ft * 0.3048

            return routes.make_response(data={
                "status": "success",
                "level_resolution": level_reason,
                "room": {
                    "id": element_id_value(room.Id),
                    "unique_id": room.UniqueId,
                    "name": normalize_string(name),
                    "number": assigned_number,
                    "requested_number": requested_number,
                    "level_name": normalize_string(level.Name),
                    "level_id": element_id_value(level.Id),
                    "area_m2": round(area_m2, 3),
                    "area_ft2": round(area_ft2, 3),
                    "perimeter_m": round(perimeter_m, 3),
                    "perimeter_ft": round(perimeter_ft, 3),
                },
            })

        except Exception as e:
            logger.error("create_room failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Rooms routes registered successfully")
