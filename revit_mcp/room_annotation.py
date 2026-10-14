# -*- coding: UTF-8 -*-
"""
Room Tagging Module for Revit MCP
Bulk-place RoomTag elements over placed rooms in a plan view.

Single transaction (single Ctrl+Z reverts everything).

Ported from Sparx mcp-servers-for-revit's TagRoomsEventHandler
(Apache-2.0 licensed C# at commandset/Services/TagRoomsEventHandler.cs)
into our IronPython pyRevit Routes pattern. This is the room equivalent
of our existing `tag_walls` route.

Differences from the Sparx C# source:
- View switching is opt-in via `auto_switch_view` (default true). Sparx
  swallowed the active view and silently switched when the view type or
  level didn't match — surprising behavior. We tell the caller via
  `view_switched=true` + `previous_view_name` in the response.
- Adds explicit `view_id` to target a specific view without touching the
  user's UI. When omitted, falls back to `uidoc.ActiveView`.
- Skip categorization is reported as separate counters
  (`skipped_existing` / `skipped_unplaced`) rather than Sparx's lumped
  `skipped_rooms` list.
- Each created tag location is reported in mm (Sparx returned both raw
  feet and mm; mm is enough).
- Revit 2026 safety: all `DB.ElementId(int_literal)` casts go via
  `Int64(...)`; all `.Name` reads on tag-symbol FamilySymbols go via
  `get_element_name()`. See feedback_revit_2026_elementid.md.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
from System import Int64
import json
import traceback
import logging

logger = logging.getLogger(__name__)


# Plan view types that can host room tags. Sparx accepted FloorPlan +
# CeilingPlan; AreaPlan and EngineeringPlan can also host RoomTags via
# NewRoomTag so we accept them too.
_PLAN_VIEW_TYPES = (
    DB.ViewType.FloorPlan,
    DB.ViewType.CeilingPlan,
    DB.ViewType.AreaPlan,
    DB.ViewType.EngineeringPlan,
)


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


def _resolve_explicit_view(doc, view_id):
    """Return (View|None, error_dict|None). view_id=None means caller didn't supply one."""
    if view_id is None:
        return None, None
    try:
        vid = DB.ElementId(Int64(int(view_id)))
    except Exception:
        return None, {"status": "view_not_found", "error": "view_id must be int"}
    el = doc.GetElement(vid)
    if el is None or not isinstance(el, DB.View):
        return None, {"status": "view_not_found", "error": "view_id_not_found"}
    return el, None


def _is_plan_view(view):
    try:
        return view.ViewType in _PLAN_VIEW_TYPES
    except Exception:
        return False


def _find_first_floor_plan_for_level(doc, level_id):
    """Find a non-template FloorPlan view bound to this level. Returns view or None."""
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        try:
            if v.IsTemplate:
                continue
            if v.ViewType != DB.ViewType.FloorPlan:
                continue
            gen = v.GenLevel
            if gen is not None and gen.Id == level_id:
                return v
        except Exception:
            continue
    return None


def _find_room_tag_symbol(doc, tag_type_id, tag_family_name, tag_type_name):
    """
    Pick a RoomTag FamilySymbol. Precedence:
    1) Explicit tag_type_id (Int64-cast) if it resolves to a RoomTag FamilySymbol.
    2) tag_family_name + tag_type_name match.
    3) First available RoomTag FamilySymbol.

    Returns (symbol|None, available_list).
    """
    available = []
    chosen = None

    # Path 1: explicit type id
    if tag_type_id is not None:
        try:
            tid = DB.ElementId(Int64(int(tag_type_id)))
            el = doc.GetElement(tid)
            if el is not None and isinstance(el, DB.FamilySymbol):
                cat = el.Category
                if cat is not None:
                    cat_id_val = element_id_value(cat.Id)
                    if cat_id_val == int(DB.BuiltInCategory.OST_RoomTags):
                        chosen = el
        except Exception:
            pass

    # Path 2+3: walk all RoomTag FamilySymbols (for the available list and name match)
    for sym in (DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_RoomTags)
                .WhereElementIsElementType()):
        if not isinstance(sym, DB.FamilySymbol):
            continue
        try:
            fam_name = sym.Family.Name if sym.Family else u"?"
        except Exception:
            fam_name = u"?"
        try:
            sym_name = get_element_name(sym)
        except Exception:
            sym_name = u"?"
        available.append({
            "id": element_id_value(sym.Id),
            "family": normalize_string(fam_name),
            "type": normalize_string(sym_name),
        })

        if chosen is None and tag_family_name and tag_type_name:
            if fam_name == tag_family_name and sym_name == tag_type_name:
                chosen = sym
        elif chosen is None and tag_family_name and not tag_type_name:
            if fam_name == tag_family_name:
                chosen = sym
        elif chosen is None and not tag_family_name and tag_type_name:
            if sym_name == tag_type_name:
                chosen = sym
        elif chosen is None and not tag_family_name and not tag_type_name:
            chosen = sym  # first available wins

    return chosen, available


class _SuppressRoomNumberWarnings(object):
    """
    pyRevit/IronPython port of Sparx's TagRoomFailurePreprocessor —
    silently deletes duplicate-room-number warnings raised during tag
    placement so the transaction commits cleanly. Implements
    IFailuresPreprocessor via duck typing (pyRevit's Routes API exposes
    it that way).
    """
    def PreprocessFailures(self, failuresAccessor):
        try:
            failures = failuresAccessor.GetFailureMessages()
            for f in failures:
                try:
                    desc = f.GetDescriptionText() or u""
                    low = desc.lower()
                    if "number" in low or "duplicate" in low:
                        failuresAccessor.DeleteWarning(f)
                except Exception:
                    continue
        except Exception:
            pass
        return DB.FailureProcessingResult.Continue


def register_room_annotation_routes(api):
    """Register room-tagging endpoints."""

    @api.route("/tag_rooms/", methods=["POST"])
    def tag_rooms(doc, request):
        """
        Bulk-place RoomTag elements over rooms in a plan view.

        Expected payload (all optional):
        {
            "room_ids":         [123, 456],          // null = all rooms visible in target view
            "tag_family_name":  "Room Tag",          // null = first match
            "tag_type_name":    "Standard",          // null = first match within family
            "tag_type_id":      null,                // explicit FamilySymbol id; overrides name lookup
            "leader":           false,
            "view_id":          null,                // null = use uidoc.ActiveView
            "auto_switch_view": true                 // if active/given view isn't a plan view
                                                     // on the rooms' level, switch to a matching FP
        }

        Skip rules:
            - room.Area <= 0 (unplaced)        -> counted in `skipped_unplaced`
            - room already has tag in view     -> counted in `skipped_existing`

        Returns:
            { status, view_id, view_name, view_switched, previous_view_name,
              tag_family, tag_type, total_rooms, tagged_count,
              skipped_existing, skipped_unplaced, tags: [...up to 100...],
              errors: [...] }

        Status values:
            - 'success'                — at least the transaction completed
            - 'view_not_supported'     — view isn't a plan view and auto_switch_view=false
            - 'no_room_tag_family'     — project has no RoomTag FamilySymbol
            - 'view_not_found'         — explicit view_id was invalid
            - 'no_rooms_in_view'       — view-scoped scan returned 0 rooms
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

            tag_family_name = data.get("tag_family_name")
            tag_type_name = data.get("tag_type_name")
            tag_type_id = data.get("tag_type_id")
            with_leader = bool(data.get("leader", False))
            auto_switch_view = bool(data.get("auto_switch_view", True))

            # ----- Resolve starting view -----
            view, view_err = _resolve_explicit_view(doc, data.get("view_id"))
            if view_err is not None:
                return routes.make_response(data=view_err)

            uidoc = revit.uidoc
            if view is None:
                if uidoc is None:
                    return routes.make_response(data={
                        "error": "No active Revit UI document and no view_id supplied"
                    }, status=503)
                view = uidoc.ActiveView

            if view is None:
                return routes.make_response(data={
                    "status": "view_not_supported",
                    "error": "No target view (active view is null and no view_id supplied)",
                })

            previous_view_name = normalize_string(view.Name)
            view_switched = False

            # ----- Resolve target level from rooms (for view-switch decisioning) -----
            target_level_id = None
            if room_ids_raw:
                # Use the first valid room's level
                for rid in room_ids_raw:
                    try:
                        el = doc.GetElement(DB.ElementId(Int64(int(rid))))
                    except Exception:
                        continue
                    if isinstance(el, DB.Architecture.Room):
                        try:
                            target_level_id = el.LevelId
                        except Exception:
                            pass
                        break
            else:
                # Any placed room in the project (Sparx pattern)
                for r in (DB.FilteredElementCollector(doc)
                          .OfCategory(DB.BuiltInCategory.OST_Rooms)
                          .WhereElementIsNotElementType()):
                    if not isinstance(r, DB.Architecture.Room):
                        continue
                    try:
                        if r.Area > 0:
                            target_level_id = r.LevelId
                            break
                    except Exception:
                        continue

            # ----- View-type / level check -----
            is_plan = _is_plan_view(view)
            level_ok = True
            if is_plan and target_level_id is not None:
                try:
                    gen = view.GenLevel if isinstance(view, DB.ViewPlan) else None
                    level_ok = (gen is not None and gen.Id == target_level_id)
                except Exception:
                    level_ok = False

            needs_switch = (not is_plan) or (not level_ok)
            if needs_switch:
                if not auto_switch_view:
                    return routes.make_response(data={
                        "status": "view_not_supported",
                        "view_id": element_id_value(view.Id),
                        "view_name": previous_view_name,
                        "view_type": str(view.ViewType),
                        "needs_level_id": element_id_value(target_level_id) if target_level_id is not None else None,
                        "error": "Active view isn't a plan view bound to the rooms' level, and auto_switch_view=False",
                    })
                if target_level_id is None:
                    return routes.make_response(data={
                        "status": "view_not_supported",
                        "view_id": element_id_value(view.Id),
                        "view_name": previous_view_name,
                        "view_type": str(view.ViewType),
                        "error": "No rooms found in the project, so we can't pick a target plan view automatically",
                    })
                new_view = _find_first_floor_plan_for_level(doc, target_level_id)
                if new_view is None:
                    return routes.make_response(data={
                        "status": "view_not_supported",
                        "view_id": element_id_value(view.Id),
                        "view_name": previous_view_name,
                        "view_type": str(view.ViewType),
                        "needs_level_id": element_id_value(target_level_id),
                        "error": "No matching FloorPlan view bound to the rooms' level was found",
                    })
                if uidoc is not None:
                    try:
                        uidoc.ActiveView = new_view
                    except Exception:
                        # Non-fatal: still tag in new_view even if we couldn't focus it
                        pass
                view = new_view
                view_switched = True

            # ----- Find a RoomTag FamilySymbol -----
            chosen_symbol, available = _find_room_tag_symbol(
                doc, tag_type_id, tag_family_name, tag_type_name
            )
            if chosen_symbol is None:
                return routes.make_response(data={
                    "status": "no_room_tag_family",
                    "view_id": element_id_value(view.Id),
                    "view_name": normalize_string(view.Name),
                    "view_switched": view_switched,
                    "previous_view_name": previous_view_name if view_switched else None,
                    "available": available,
                    "error": "No RoomTag FamilySymbol in this project. Load a Room Tag family and retry.",
                })

            # ----- Build room set -----
            if room_ids_raw:
                rooms = []
                invalid_ids = []
                for rid in room_ids_raw:
                    try:
                        el = doc.GetElement(DB.ElementId(Int64(int(rid))))
                    except Exception:
                        invalid_ids.append(rid)
                        continue
                    if el is None or not isinstance(el, DB.Architecture.Room):
                        invalid_ids.append(rid)
                        continue
                    rooms.append(el)
            else:
                invalid_ids = []
                rooms = list(DB.FilteredElementCollector(doc, view.Id)
                             .OfCategory(DB.BuiltInCategory.OST_Rooms)
                             .WhereElementIsNotElementType())

            if not rooms:
                return routes.make_response(data={
                    "status": "no_rooms_in_view",
                    "view_id": element_id_value(view.Id),
                    "view_name": normalize_string(view.Name),
                    "view_switched": view_switched,
                    "previous_view_name": previous_view_name if view_switched else None,
                    "invalid_room_ids": invalid_ids,
                })

            # ----- Existing-tag set: skip rooms that already have a tag in this view -----
            rooms_with_tags = set()
            for tag in (DB.FilteredElementCollector(doc, view.Id)
                        .OfCategory(DB.BuiltInCategory.OST_RoomTags)
                        .WhereElementIsNotElementType()):
                if not isinstance(tag, DB.Architecture.RoomTag):
                    continue
                try:
                    r = tag.Room
                    if r is not None:
                        rooms_with_tags.add(element_id_value(r.Id))
                except Exception:
                    continue

            # ----- Tag placement loop -----
            placed = []
            errors = []
            skipped_existing = 0
            skipped_unplaced = 0

            with DB.Transaction(doc, "MCP: Tag Rooms") as t:
                # Suppress duplicate-number warnings (Sparx pattern)
                try:
                    fho = t.GetFailureHandlingOptions()
                    fho.SetFailuresPreprocessor(_SuppressRoomNumberWarnings())
                    fho.SetClearAfterRollback(True)
                    fho.SetDelayedMiniWarnings(False)
                    t.SetFailureHandlingOptions(fho)
                except Exception as fh_err:
                    logger.warning("Could not install failure preprocessor: %s", str(fh_err))

                t.Start()
                if not chosen_symbol.IsActive:
                    chosen_symbol.Activate()
                    doc.Regenerate()

                for room in rooms:
                    if not isinstance(room, DB.Architecture.Room):
                        continue
                    try:
                        if room.Area <= 0:
                            skipped_unplaced += 1
                            continue
                        room_id_v = element_id_value(room.Id)
                        if room_id_v in rooms_with_tags:
                            skipped_existing += 1
                            continue

                        # Pick tag insertion point — prefer LocationPoint, fall back to bbox centre
                        loc = room.Location
                        if isinstance(loc, DB.LocationPoint) and loc.Point is not None:
                            center = loc.Point
                        else:
                            bbox = room.get_BoundingBox(view)
                            if bbox is None:
                                errors.append({
                                    "room_id": room_id_v,
                                    "error": "No LocationPoint and no bounding box in view",
                                })
                                continue
                            center = DB.XYZ(
                                (bbox.Min.X + bbox.Max.X) / 2.0,
                                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                                (bbox.Min.Z + bbox.Max.Z) / 2.0,
                            )

                        tag_uv = DB.UV(center.X, center.Y)
                        link_id = DB.LinkElementId(room.Id)
                        tag = doc.Create.NewRoomTag(link_id, tag_uv, view.Id)
                        if tag is None:
                            errors.append({
                                "room_id": room_id_v,
                                "error": "NewRoomTag returned null",
                            })
                            continue

                        if with_leader:
                            try:
                                tag.HasLeader = True
                            except Exception:
                                pass

                        # Capture room name + number for response
                        try:
                            rn_param = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
                            room_name = rn_param.AsString() if rn_param is not None else None
                        except Exception:
                            room_name = None
                        try:
                            room_number = room.Number
                        except Exception:
                            room_number = None

                        placed.append({
                            "tag_id": element_id_value(tag.Id),
                            "room_id": room_id_v,
                            "room_name": normalize_string(room_name) if room_name else None,
                            "room_number": normalize_string(room_number) if room_number else None,
                            "location_mm": {
                                "x": round(_ft_to_mm(center.X), 3),
                                "y": round(_ft_to_mm(center.Y), 3),
                                "z": round(_ft_to_mm(center.Z), 3),
                            },
                        })
                    except Exception as room_err:
                        errors.append({
                            "room_id": element_id_value(room.Id) if room is not None else None,
                            "error": str(room_err),
                        })
                        continue

                t.Commit()

            # Truncate tags response to keep payloads sane
            TAG_CAP = 100
            tags_truncated = len(placed) > TAG_CAP

            return routes.make_response(data={
                "status": "success",
                "view_id": element_id_value(view.Id),
                "view_name": normalize_string(view.Name),
                "view_switched": view_switched,
                "previous_view_name": previous_view_name if view_switched else None,
                "tag_family": normalize_string(chosen_symbol.Family.Name) if chosen_symbol.Family else None,
                "tag_type": normalize_string(get_element_name(chosen_symbol)),
                "tag_type_id": element_id_value(chosen_symbol.Id),
                "total_rooms": len(rooms),
                "tagged_count": len(placed),
                "skipped_existing": skipped_existing,
                "skipped_unplaced": skipped_unplaced,
                "tags": placed[:TAG_CAP],
                "tags_truncated": tags_truncated,
                "errors": errors,
                "invalid_room_ids": invalid_ids,
            })

        except Exception as e:
            logger.error("tag_rooms failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Room-annotation routes registered successfully")
