# -*- coding: UTF-8 -*-
"""
Levels Module for Revit MCP
Create a level + (optionally) matching FloorPlan and CeilingPlan views in one
transaction so a single Ctrl+Z reverts the whole thing.

Units: elevation is in MILLIMETRES on the API; converted internally to Revit's
decimal feet via UnitUtils.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value
import json
import traceback
import logging

logger = logging.getLogger(__name__)


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


def _find_view_family_type(doc, family):
    """Find the first ViewFamilyType of the given DB.ViewFamily. None if absent."""
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        if v.ViewFamily == family:
            return v
    return None


def register_levels_routes(api):
    """Register level-creation routes."""

    @api.route("/create_level/", methods=["POST"])
    def create_level(doc, request):
        """
        Create a level at the given elevation, optionally with matching plan views.

        Expected payload:
        {
            "name":               "Level 3",   # required, unique within project
            "elevation_mm":       7500.0,      # required, MILLIMETRES
            "create_floor_plan":  true,         # optional, default true
            "create_ceiling_plan": false,      # optional, default false
            "view_name":          null          # optional, defaults to level name
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            name = data.get("name")
            elevation_mm = data.get("elevation_mm")
            create_fp = bool(data.get("create_floor_plan", True))
            create_cp = bool(data.get("create_ceiling_plan", False))
            view_name = data.get("view_name") or name

            if not name or not isinstance(name, str):
                return routes.make_response(data={"error": "Missing required field: name (string)"}, status=400)
            if elevation_mm is None:
                return routes.make_response(data={"error": "Missing required field: elevation_mm (number, millimetres)"}, status=400)
            try:
                elevation_mm = float(elevation_mm)
            except (TypeError, ValueError):
                return routes.make_response(data={"error": "elevation_mm must be a number"}, status=400)

            # Refuse to create a duplicate level by name
            for lvl in DB.FilteredElementCollector(doc).OfClass(DB.Level):
                if DB.Element.Name.__get__(lvl) == name:
                    return routes.make_response(data={
                        "status": "name_collision",
                        "error": "A level named '{}' already exists (id={}).".format(
                            name, element_id_value(lvl.Id)),
                        "existing_level_id": element_id_value(lvl.Id),
                    })

            # Resolve view family types up front (cheaper to fail before opening the transaction)
            vft_fp = _find_view_family_type(doc, DB.ViewFamily.FloorPlan) if create_fp else None
            vft_cp = _find_view_family_type(doc, DB.ViewFamily.CeilingPlan) if create_cp else None
            if create_fp and vft_fp is None:
                return routes.make_response(data={
                    "error": "No FloorPlan ViewFamilyType found in the project; cannot create floor plan view.",
                })
            if create_cp and vft_cp is None:
                return routes.make_response(data={
                    "error": "No CeilingPlan ViewFamilyType found in the project; cannot create ceiling plan view.",
                })

            new_level = None
            floor_plan = None
            ceiling_plan = None
            with DB.Transaction(doc, "MCP: Create Level + Views") as t:
                t.Start()
                new_level = DB.Level.Create(doc, _mm_to_feet(elevation_mm))
                new_level.Name = name
                if create_fp:
                    floor_plan = DB.ViewPlan.Create(doc, vft_fp.Id, new_level.Id)
                    floor_plan.Name = view_name
                if create_cp:
                    ceiling_plan = DB.ViewPlan.Create(doc, vft_cp.Id, new_level.Id)
                    # Ceiling plan share-names with the floor plan would collide;
                    # suffix with " - Ceiling" when both views are created.
                    ceiling_plan.Name = (view_name + " - Ceiling") if create_fp else view_name
                t.Commit()

            return routes.make_response(data={
                "status": "success",
                "level": {
                    "id": element_id_value(new_level.Id),
                    "name": normalize_string(name),
                    "elevation_mm": elevation_mm,
                    "elevation_ft": float(_mm_to_feet(elevation_mm)),
                },
                "floor_plan": (
                    {"id": element_id_value(floor_plan.Id), "name": DB.Element.Name.__get__(floor_plan)}
                    if floor_plan is not None else None
                ),
                "ceiling_plan": (
                    {"id": element_id_value(ceiling_plan.Id), "name": DB.Element.Name.__get__(ceiling_plan)}
                    if ceiling_plan is not None else None
                ),
            })

        except Exception as e:
            logger.error("create_level failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Levels routes registered successfully")
