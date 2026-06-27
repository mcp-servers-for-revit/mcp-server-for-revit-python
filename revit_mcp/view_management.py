# -*- coding: UTF-8 -*-
"""
View Management Module for Revit MCP
Handles view creation and active view switching
"""

from utils import get_element_name, get_element_id_value
from pyrevit import routes, revit, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def register_view_management_routes(api):
    """Register all view management routes with the API"""

    @api.route("/create_view/", methods=["POST"])
    def create_view_handler(doc, request):
        """Create a new view in the Revit model."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            view_type = data.get("view_type")
            name = data.get("name")

            if not view_type:
                return routes.make_response(
                    data={"error": "view_type is required (floor_plan, ceiling_plan, section, elevation, 3d)"},
                    status=400,
                )
            if not name:
                return routes.make_response(
                    data={"error": "name is required"}, status=400
                )

            level_name = data.get("level_name")

            t = DB.Transaction(doc, "Create View via MCP")
            t.Start()

            try:
                new_view = None

                if view_type in ("floor_plan", "ceiling_plan"):
                    if not level_name:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "level_name is required for {} views".format(view_type)},
                            status=400,
                        )

                    # Find the level
                    levels = (
                        DB.FilteredElementCollector(doc)
                        .OfCategory(DB.BuiltInCategory.OST_Levels)
                        .WhereElementIsNotElementType()
                        .ToElements()
                    )
                    target_level = None
                    for lv in levels:
                        if get_element_name(lv) == level_name:
                            target_level = lv
                            break

                    if not target_level:
                        t.RollBack()
                        available = [get_element_name(lv) for lv in levels]
                        return routes.make_response(
                            data={
                                "error": "Level '{}' not found".format(level_name),
                                "available_levels": available,
                            },
                            status=404,
                        )

                    # Find the appropriate view family type
                    view_family_types = (
                        DB.FilteredElementCollector(doc)
                        .OfClass(DB.ViewFamilyType)
                        .ToElements()
                    )

                    if view_type == "floor_plan":
                        target_family = DB.ViewFamily.FloorPlan
                    else:
                        target_family = DB.ViewFamily.CeilingPlan

                    vft = None
                    for vf in view_family_types:
                        if vf.ViewFamily == target_family:
                            vft = vf
                            break

                    if not vft:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "No {} view family type found in project".format(view_type)},
                            status=500,
                        )

                    new_view = DB.ViewPlan.Create(doc, vft.Id, target_level.Id)

                elif view_type == "section":
                    section_box = data.get("section_box")
                    if not section_box:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "section_box is required for section views"},
                            status=400,
                        )

                    # Find section view family type
                    view_family_types = (
                        DB.FilteredElementCollector(doc)
                        .OfClass(DB.ViewFamilyType)
                        .ToElements()
                    )
                    vft = None
                    for vf in view_family_types:
                        if vf.ViewFamily == DB.ViewFamily.Section:
                            vft = vf
                            break

                    if not vft:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "No section view family type found"},
                            status=500,
                        )

                    # Build the section box transform
                    origin = section_box.get("origin", {})
                    direction = section_box.get("direction", {"x": 0, "y": 1, "z": 0})
                    up = section_box.get("up", {"x": 0, "y": 0, "z": 1})
                    width = float(section_box.get("width", 10000)) * MM_TO_FEET
                    height = float(section_box.get("height", 10000)) * MM_TO_FEET
                    depth = float(section_box.get("depth", 10000)) * MM_TO_FEET

                    origin_pt = DB.XYZ(
                        float(origin.get("x", 0)) * MM_TO_FEET,
                        float(origin.get("y", 0)) * MM_TO_FEET,
                        float(origin.get("z", 0)) * MM_TO_FEET,
                    )
                    dir_vec = DB.XYZ(
                        float(direction.get("x", 0)),
                        float(direction.get("y", 1)),
                        float(direction.get("z", 0)),
                    ).Normalize()
                    up_vec = DB.XYZ(
                        float(up.get("x", 0)),
                        float(up.get("y", 0)),
                        float(up.get("z", 1)),
                    ).Normalize()

                    # Right vector = direction cross up
                    right_vec = dir_vec.CrossProduct(up_vec).Normalize()

                    # Create transform
                    transform = DB.Transform.Identity
                    transform.Origin = origin_pt
                    transform.BasisX = right_vec
                    transform.BasisY = up_vec
                    transform.BasisZ = dir_vec

                    # Create bounding box for section
                    section_bb = DB.BoundingBoxXYZ()
                    section_bb.Transform = transform
                    section_bb.Min = DB.XYZ(-width / 2.0, -height / 2.0, 0)
                    section_bb.Max = DB.XYZ(width / 2.0, height / 2.0, depth)

                    new_view = DB.ViewSection.CreateSection(doc, vft.Id, section_bb)

                elif view_type == "3d":
                    # Find 3D view family type
                    view_family_types = (
                        DB.FilteredElementCollector(doc)
                        .OfClass(DB.ViewFamilyType)
                        .ToElements()
                    )
                    vft = None
                    for vf in view_family_types:
                        if vf.ViewFamily == DB.ViewFamily.ThreeDimensional:
                            vft = vf
                            break

                    if not vft:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "No 3D view family type found"},
                            status=500,
                        )

                    new_view = DB.View3D.CreateIsometric(doc, vft.Id)

                elif view_type == "elevation":
                    # Find elevation view family type
                    view_family_types = (
                        DB.FilteredElementCollector(doc)
                        .OfClass(DB.ViewFamilyType)
                        .ToElements()
                    )
                    vft = None
                    for vf in view_family_types:
                        if vf.ViewFamily == DB.ViewFamily.Elevation:
                            vft = vf
                            break

                    if not vft:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "No elevation view family type found"},
                            status=500,
                        )

                    # Find a floor plan view to host the elevation marker
                    elev_level = data.get("level_name")
                    plan_view = None
                    plan_views = (
                        DB.FilteredElementCollector(doc)
                        .OfClass(DB.ViewPlan)
                        .WhereElementIsNotElementType()
                        .ToElements()
                    )
                    # Try matching level first
                    if elev_level:
                        for pv in plan_views:
                            if pv.ViewType == DB.ViewType.FloorPlan and not pv.IsTemplate:
                                if pv.GenLevel and get_element_name(pv.GenLevel) == elev_level:
                                    plan_view = pv
                                    break
                    # Fallback: active view if it's a plan
                    if not plan_view:
                        active = doc.ActiveView
                        if hasattr(active, "ViewType") and active.ViewType == DB.ViewType.FloorPlan:
                            plan_view = active
                    # Fallback: any floor plan
                    if not plan_view:
                        for pv in plan_views:
                            if pv.ViewType == DB.ViewType.FloorPlan and not pv.IsTemplate:
                                plan_view = pv
                                break

                    if not plan_view:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "No floor plan view found to host the elevation marker"},
                            status=500,
                        )

                    # Create elevation marker at origin, then get the view
                    marker = DB.ElevationMarker.CreateElevationMarker(
                        doc, vft.Id, DB.XYZ.Zero, 1
                    )
                    new_view = marker.CreateElevation(doc, plan_view.Id, 0)

                else:
                    t.RollBack()
                    return routes.make_response(
                        data={"error": "Unsupported view_type '{}'. Use: floor_plan, ceiling_plan, section, elevation, 3d".format(view_type)},
                        status=400,
                    )

                if new_view:
                    new_view.Name = name

                t.Commit()

                actual_type = ""
                try:
                    actual_type = str(new_view.ViewType)
                except Exception:
                    actual_type = view_type

                return routes.make_response(
                    data={
                        "status": "success",
                        "view_id": get_element_id_value(new_view),
                        "name": name,
                        "view_type": actual_type,
                        "message": "Created {} view '{}'".format(view_type, name),
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to create view: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    @api.route("/set_active_view/", methods=["POST"])
    def set_active_view_handler(doc, uidoc, request):
        """Set the active view in Revit UI."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            view_name = data.get("view_name")
            if not view_name:
                return routes.make_response(
                    data={"error": "view_name is required"}, status=400
                )

            # Find the view
            views = (
                DB.FilteredElementCollector(doc)
                .OfClass(DB.View)
                .WhereElementIsNotElementType()
                .ToElements()
            )

            target_view = None
            available_views = []
            for v in views:
                try:
                    vname = get_element_name(v)
                    if not v.IsTemplate:
                        available_views.append(vname)
                    if vname == view_name and not v.IsTemplate:
                        target_view = v
                except Exception:
                    continue

            if not target_view:
                return routes.make_response(
                    data={
                        "error": "View '{}' not found".format(view_name),
                        "available_views": sorted(available_views)[:30],
                    },
                    status=404,
                )

            uidoc.ActiveView = target_view

            return routes.make_response(
                data={
                    "status": "success",
                    "view_id": get_element_id_value(target_view),
                    "name": view_name,
                    "view_type": str(target_view.ViewType),
                    "message": "Switched to view '{}'".format(view_name),
                }
            )

        except Exception as e:
            logger.error("Failed to set active view: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("View management routes registered successfully")
