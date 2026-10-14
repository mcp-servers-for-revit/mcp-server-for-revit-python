# -*- coding: UTF-8 -*-
"""
Room Data Export Module for Revit MCP
Read-only export of every Room element in the project (or a filtered subset)
with full parameter set: name, number, level, phase, area, volume, perimeter,
height, location, bounding box, department, occupancy, comments.

This is a READ-ONLY data-extraction tool — no transactions.

Ported from Sparx mcp-servers-for-revit's ExportRoomDataEventHandler
(Apache-2.0 licensed C# at commandset/Services/DataExtraction/
ExportRoomDataEventHandler.cs) into our IronPython pyRevit Routes pattern.

Differences from the Sparx C# source:
- Output units: area in m² (`area_m2`), volume in m³ (`volume_m3`),
  perimeter in mm (`perimeter_mm`), height in mm (`unbounded_height_mm`).
  Sparx returned raw decimal feet/ft²/ft³ verbatim.
- Adds optional filters: level_id, level_name, view_id, phase_id, room_ids.
- Properly separates "unplaced" (no LocationPoint) from "not enclosed"
  (has LocationPoint but Area==0). Sparx's two flags were identical bugs
  — they both checked `Area == 0` with no way to distinguish.
- Adds per-room `location_mm` and `bounding_box_mm` to support downstream
  coordinate-based operations (e.g. piping the output into tag_rooms or
  create_dimension).
- Returns `applied_filters` diagnostic + per-status invalid-id lists
  (invalid_room_ids / level_not_found / phase_not_found).
- Revit 2026 safety: Int64-cast ElementIds; get_element_name() for Level
  + Phase + ElementType name reads.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
from System import Int64
import json
import traceback
import logging

logger = logging.getLogger(__name__)


def _parse_json_request(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        try:
            return json.loads(request.data)
        except Exception:
            return {}
    return request.data or {}


def _ft_to_mm(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.Millimeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_MILLIMETERS)
    except Exception:
        return float(v) * 304.8


def _ft2_to_m2(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.SquareMeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_SQUARE_METERS)
    except Exception:
        return float(v) * 0.092903


def _ft3_to_m3(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.CubicMeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_CUBIC_METERS)
    except Exception:
        return float(v) * 0.0283168


def _safe_get_string_param(elem, bip):
    try:
        p = elem.get_Parameter(bip)
        if p is None:
            return None
        s = p.AsString()
        return s if s else None
    except Exception:
        return None


def _safe_get_elementid_param(elem, bip):
    try:
        p = elem.get_Parameter(bip)
        if p is None:
            return None
        eid = p.AsElementId()
        if eid is None or eid == DB.ElementId.InvalidElementId:
            return None
        return eid
    except Exception:
        return None


def _resolve_level(doc, level_id, level_name):
    """Return (Level|None, error_string|None). All-None args means no level filter."""
    if level_id is None and not level_name:
        return None, None

    if level_id is not None:
        try:
            lid = DB.ElementId(Int64(int(level_id)))
        except Exception:
            return None, "level_id_must_be_int"
        el = doc.GetElement(lid)
        if el is None or not isinstance(el, DB.Level):
            return None, "level_id_not_found"
        return el, None

    # level_name path
    for lvl in DB.FilteredElementCollector(doc).OfClass(DB.Level):
        try:
            if get_element_name(lvl) == level_name:
                return lvl, None
        except Exception:
            continue
    return None, "level_name_not_found"


def _resolve_phase(doc, phase_id):
    if phase_id is None:
        return None, None
    try:
        pid = DB.ElementId(Int64(int(phase_id)))
    except Exception:
        return None, "phase_id_must_be_int"
    el = doc.GetElement(pid)
    if el is None or not isinstance(el, DB.Phase):
        return None, "phase_id_not_found"
    return el, None


def _resolve_view(doc, view_id):
    if view_id is None:
        return None, None
    try:
        vid = DB.ElementId(Int64(int(view_id)))
    except Exception:
        return None, "view_id_must_be_int"
    el = doc.GetElement(vid)
    if el is None or not isinstance(el, DB.View):
        return None, "view_id_not_found"
    return el, None


def _classify_room_placement(room):
    """
    Return one of: 'placed', 'unplaced', 'not_enclosed'.

    - 'unplaced':     room.Location is None (Revit's "Not Placed" schedule bucket)
    - 'not_enclosed': room has a Location but Area==0 (room exists at a point
                      but the bounding curves don't form a closed loop)
    - 'placed':       Area > 0
    """
    try:
        if room.Location is None:
            return "unplaced"
    except Exception:
        return "unplaced"
    try:
        if room.Area <= 0:
            return "not_enclosed"
    except Exception:
        return "not_enclosed"
    return "placed"


def _build_room_record(doc, room, view_for_bbox):
    """Turn a Room into a JSON-serialisable dict with mm-normalised geometry."""
    placement = _classify_room_placement(room)

    rec = {
        "id": element_id_value(room.Id),
        "unique_id": normalize_string(room.UniqueId) if hasattr(room, "UniqueId") else None,
        "name": None,
        "number": None,
        "placement": placement,
        "level_id": None,
        "level_name": None,
        "phase_id": None,
        "phase_name": None,
        "area_m2": 0.0,
        "volume_m3": 0.0,
        "perimeter_mm": 0.0,
        "unbounded_height_mm": 0.0,
        "department": None,
        "occupancy": None,
        "comments": None,
        "location_mm": None,
        "bounding_box_mm": None,
    }

    # Name + number
    rec["name"] = normalize_string(
        _safe_get_string_param(room, DB.BuiltInParameter.ROOM_NAME) or u""
    ) or None
    try:
        rec["number"] = normalize_string(room.Number) if room.Number else None
    except Exception:
        pass

    # Level
    try:
        lvl = room.Level
    except Exception:
        lvl = None
    if lvl is not None:
        rec["level_id"] = element_id_value(lvl.Id)
        try:
            rec["level_name"] = normalize_string(get_element_name(lvl))
        except Exception:
            pass

    # Phase
    phase_eid = _safe_get_elementid_param(room, DB.BuiltInParameter.ROOM_PHASE)
    if phase_eid is not None:
        phase = doc.GetElement(phase_eid)
        if phase is not None:
            rec["phase_id"] = element_id_value(phase_eid)
            try:
                rec["phase_name"] = normalize_string(get_element_name(phase))
            except Exception:
                pass

    # Numeric geometry (in ft / ft² / ft³ internally → convert)
    try:
        rec["area_m2"] = round(_ft2_to_m2(float(room.Area)), 4)
    except Exception:
        pass
    try:
        rec["volume_m3"] = round(_ft3_to_m3(float(room.Volume)), 4)
    except Exception:
        pass
    try:
        rec["perimeter_mm"] = round(_ft_to_mm(float(room.Perimeter)), 3)
    except Exception:
        pass
    try:
        rec["unbounded_height_mm"] = round(_ft_to_mm(float(room.UnboundedHeight)), 3)
    except Exception:
        pass

    # Free-text params
    rec["department"] = normalize_string(
        _safe_get_string_param(room, DB.BuiltInParameter.ROOM_DEPARTMENT) or u""
    ) or None
    rec["occupancy"] = normalize_string(
        _safe_get_string_param(room, DB.BuiltInParameter.ROOM_OCCUPANCY) or u""
    ) or None
    rec["comments"] = normalize_string(
        _safe_get_string_param(room, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS) or u""
    ) or None

    # Location point
    try:
        loc = room.Location
        if isinstance(loc, DB.LocationPoint) and loc.Point is not None:
            p = loc.Point
            rec["location_mm"] = {
                "x": round(_ft_to_mm(p.X), 3),
                "y": round(_ft_to_mm(p.Y), 3),
                "z": round(_ft_to_mm(p.Z), 3),
            }
    except Exception:
        pass

    # Bounding box (model space if no view supplied; otherwise view-clipped)
    try:
        bbox = room.get_BoundingBox(view_for_bbox)
        if bbox is not None and bbox.Min is not None and bbox.Max is not None:
            rec["bounding_box_mm"] = {
                "min": {
                    "x": round(_ft_to_mm(bbox.Min.X), 3),
                    "y": round(_ft_to_mm(bbox.Min.Y), 3),
                    "z": round(_ft_to_mm(bbox.Min.Z), 3),
                },
                "max": {
                    "x": round(_ft_to_mm(bbox.Max.X), 3),
                    "y": round(_ft_to_mm(bbox.Max.Y), 3),
                    "z": round(_ft_to_mm(bbox.Max.Z), 3),
                },
            }
    except Exception:
        pass

    return rec


def register_room_data_routes(api):
    """Register room-data extraction routes."""

    @api.route("/export_room_data/", methods=["POST"])
    def export_room_data(doc, request):
        """
        Export every Room in the project (or a filtered subset) as JSON.

        Expected payload (all fields optional):
        {
            "room_ids":            [123, 456],     // explicit set, supersedes filters
            "level_id":            null,
            "level_name":          "Level 1",      // alternate to level_id
            "view_id":             null,            // restrict to visible-in-view
            "phase_id":            null,
            "include_unplaced":    false,           // rooms with Location==None
            "include_not_enclosed":false,           // rooms with Location but Area==0
            "sort_by":             "number"         // "number" | "name" | "level" | "area"
                                                    // default = "number"
        }

        Returns status='success' with `rooms` list (each carrying the full
        parameter set) + a `totals` block. Each room is classified as
        'placed' / 'unplaced' / 'not_enclosed' via the `placement` field.

        Status values:
            - 'success'             — at least the filter was valid
            - 'no_rooms_found'      — filter produced an empty set
            - 'level_not_found'     — explicit level_id/level_name invalid
            - 'phase_not_found'     — explicit phase_id invalid
            - 'view_not_found'      — explicit view_id invalid

        No transactions — pure read.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            room_ids_raw = data.get("room_ids") or []
            if not isinstance(room_ids_raw, list):
                return routes.make_response(data={
                    "error": "room_ids must be a list of integers"
                }, status=400)

            include_unplaced = bool(data.get("include_unplaced", False))
            include_not_enclosed = bool(data.get("include_not_enclosed", False))
            sort_by = (data.get("sort_by") or "number").lower()
            if sort_by not in ("number", "name", "level", "area"):
                return routes.make_response(data={
                    "error": "sort_by must be one of: number, name, level, area"
                }, status=400)

            # Resolve scope filters
            level, level_err = _resolve_level(doc, data.get("level_id"), data.get("level_name"))
            if level_err is not None:
                return routes.make_response(data={
                    "status": "level_not_found",
                    "error": level_err,
                })
            phase, phase_err = _resolve_phase(doc, data.get("phase_id"))
            if phase_err is not None:
                return routes.make_response(data={
                    "status": "phase_not_found",
                    "error": phase_err,
                })
            view, view_err = _resolve_view(doc, data.get("view_id"))
            if view_err is not None:
                return routes.make_response(data={
                    "status": "view_not_found",
                    "error": view_err,
                })

            applied_filters = []
            if room_ids_raw:
                applied_filters.append("room_ids")
            if level is not None:
                applied_filters.append("level_id" if data.get("level_id") is not None else "level_name")
            if phase is not None:
                applied_filters.append("phase_id")
            if view is not None:
                applied_filters.append("view_id")
            if include_unplaced:
                applied_filters.append("include_unplaced")
            if include_not_enclosed:
                applied_filters.append("include_not_enclosed")

            # ----- Build room set -----
            invalid_ids = []
            if room_ids_raw:
                rooms_in = []
                for rid in room_ids_raw:
                    try:
                        el = doc.GetElement(DB.ElementId(Int64(int(rid))))
                    except Exception:
                        invalid_ids.append(rid)
                        continue
                    if el is None or not isinstance(el, DB.Architecture.Room):
                        invalid_ids.append(rid)
                        continue
                    rooms_in.append(el)
            else:
                if view is not None:
                    collector = DB.FilteredElementCollector(doc, view.Id)
                else:
                    collector = DB.FilteredElementCollector(doc)
                collector = collector.OfCategory(DB.BuiltInCategory.OST_Rooms) \
                    .WhereElementIsNotElementType()
                rooms_in = [r for r in collector if isinstance(r, DB.Architecture.Room)]

            # ----- Per-room filtering + record build -----
            kept = []
            skipped_unplaced = 0
            skipped_not_enclosed = 0
            skipped_other_level = 0
            skipped_other_phase = 0

            for room in rooms_in:
                placement = _classify_room_placement(room)
                if placement == "unplaced" and not include_unplaced:
                    skipped_unplaced += 1
                    continue
                if placement == "not_enclosed" and not include_not_enclosed:
                    skipped_not_enclosed += 1
                    continue

                if level is not None:
                    try:
                        lvl_id = room.LevelId
                    except Exception:
                        lvl_id = None
                    if lvl_id is None or lvl_id != level.Id:
                        skipped_other_level += 1
                        continue

                if phase is not None:
                    phase_eid = _safe_get_elementid_param(room, DB.BuiltInParameter.ROOM_PHASE)
                    if phase_eid is None or phase_eid != phase.Id:
                        skipped_other_phase += 1
                        continue

                rec = _build_room_record(doc, room, view)
                kept.append(rec)

            if not kept:
                return routes.make_response(data={
                    "status": "no_rooms_found",
                    "applied_filters": applied_filters,
                    "skipped_unplaced": skipped_unplaced,
                    "skipped_not_enclosed": skipped_not_enclosed,
                    "skipped_other_level": skipped_other_level,
                    "skipped_other_phase": skipped_other_phase,
                    "invalid_room_ids": invalid_ids,
                    "level_filter": normalize_string(get_element_name(level)) if level else None,
                    "phase_filter": normalize_string(get_element_name(phase)) if phase else None,
                    "view_filter": normalize_string(view.Name) if view else None,
                })

            # ----- Sort -----
            def _natural_number_sort_key(s):
                # "101A" sorts before "101B" and after "101"; numeric prefix is primary key.
                if s is None:
                    return (1, 0, u"")
                s = s or u""
                # Walk numeric prefix
                i = 0
                while i < len(s) and s[i].isdigit():
                    i += 1
                num_part = int(s[:i]) if i > 0 else 10**9  # rooms without numeric prefix sort last
                return (0 if i > 0 else 1, num_part, s[i:].lower())

            if sort_by == "number":
                kept.sort(key=lambda r: _natural_number_sort_key(r.get("number")))
            elif sort_by == "name":
                kept.sort(key=lambda r: (r.get("name") or u"").lower())
            elif sort_by == "level":
                kept.sort(key=lambda r: (
                    (r.get("level_name") or u"~").lower(),
                    _natural_number_sort_key(r.get("number")),
                ))
            elif sort_by == "area":
                kept.sort(key=lambda r: -float(r.get("area_m2") or 0.0))

            # ----- Totals -----
            total_area_m2 = sum(float(r.get("area_m2") or 0.0) for r in kept)
            total_volume_m3 = sum(float(r.get("volume_m3") or 0.0) for r in kept)

            return routes.make_response(data={
                "status": "success",
                "rooms": kept,
                "totals": {
                    "room_count": len(kept),
                    "area_m2": round(total_area_m2, 4),
                    "volume_m3": round(total_volume_m3, 4),
                    "placed_count": sum(1 for r in kept if r["placement"] == "placed"),
                    "unplaced_count": sum(1 for r in kept if r["placement"] == "unplaced"),
                    "not_enclosed_count": sum(1 for r in kept if r["placement"] == "not_enclosed"),
                },
                "applied_filters": applied_filters,
                "skipped_unplaced": skipped_unplaced,
                "skipped_not_enclosed": skipped_not_enclosed,
                "skipped_other_level": skipped_other_level,
                "skipped_other_phase": skipped_other_phase,
                "invalid_room_ids": invalid_ids,
                "level_filter": normalize_string(get_element_name(level)) if level else None,
                "phase_filter": normalize_string(get_element_name(phase)) if phase else None,
                "view_filter": normalize_string(view.Name) if view else None,
                "sort_by": sort_by,
            })

        except Exception as e:
            logger.error("export_room_data failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Room-data routes registered successfully")
