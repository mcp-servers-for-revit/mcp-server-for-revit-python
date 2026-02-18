# -*- coding: UTF-8 -*-
"""
Model Info Module for Revit MCP
Provides comprehensive model information for architects and designers
"""

from pyrevit import routes, revit, DB
from pyrevit.revit.db import ProjectInfo as RevitProjectInfo
import pyrevit.revit.db.query as q
import logging

from utils import normalize_string, get_element_name

logger = logging.getLogger(__name__)


def register_model_info_routes(api):
    """Register all model information routes with the API"""

    @api.route("/model_info/", methods=["GET"])
    def get_model_info():
        """
        Get comprehensive information about the current Revit model

        Returns architect-focused information including:
        - Project details (name, number, client)
        - Element counts by major categories
        - Basic warnings count
        - Views and sheets overview
        - Room information with levels
        - Link status
        """
        try:
            doc = revit.doc
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            # ============ PROJECT INFORMATION ============
            try:
                revit_project_info = RevitProjectInfo(doc)
                project_info = {
                    "name": normalize_string(revit_project_info.name),
                    "number": normalize_string(revit_project_info.number),
                    "client": normalize_string(revit_project_info.client_name),
                    "file_name": normalize_string(doc.Title),
                }
            except Exception as e:
                logger.warning("Could not get full project info: {}".format(str(e)))
                project_info = {
                    "name": normalize_string(doc.Title),
                    "number": "Not Set",
                    "client": "Not Set",
                    "file_name": normalize_string(doc.Title),
                }

            # ============ ELEMENT COUNTS ============
            element_categories = {
                "Walls": DB.BuiltInCategory.OST_Walls,
                "Floors": DB.BuiltInCategory.OST_Floors,
                "Ceilings": DB.BuiltInCategory.OST_Ceilings,
                "Roofs": DB.BuiltInCategory.OST_Roofs,
                "Doors": DB.BuiltInCategory.OST_Doors,
                "Windows": DB.BuiltInCategory.OST_Windows,
                "Stairs": DB.BuiltInCategory.OST_Stairs,
                "Railings": DB.BuiltInCategory.OST_Railings,
                "Columns": DB.BuiltInCategory.OST_Columns,
                "Structural_Framing": DB.BuiltInCategory.OST_StructuralFraming,
                "Furniture": DB.BuiltInCategory.OST_Furniture,
                "Lighting_Fixtures": DB.BuiltInCategory.OST_LightingFixtures,
                "Plumbing_Fixtures": DB.BuiltInCategory.OST_PlumbingFixtures,
            }

            element_counts = {}
            total_elements = 0

            for name, category in element_categories.items():
                try:
                    count = (
                        DB.FilteredElementCollector(doc)
                        .OfCategory(category)
                        .WhereElementIsNotElementType()
                        .GetElementCount()
                    )
                    element_counts[name] = count
                    total_elements += count
                except:
                    element_counts[name] = 0

            # ============ WARNINGS ============
            try:
                warnings = doc.GetWarnings()
                warnings_count = len(warnings)
                # Count critical warnings (simplified check)
                critical_warnings = sum(
                    1 for w in warnings if w.GetSeverity() == DB.WarningType.Error
                )
            except:
                warnings_count = 0
                critical_warnings = 0

            # ============ LEVELS ============
            try:
                levels_collector = (
                    DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_Levels)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )

                levels_info = []
                for level in levels_collector:
                    level_name = get_element_name(level)
                    try:
                        elevation = level.Elevation
                        levels_info.append(
                            {
                                "name": normalize_string(level_name),
                                "elevation": round(elevation, 2),
                            }
                        )
                    except:
                        levels_info.append(
                            {
                                "name": normalize_string(level_name),
                                "elevation": "Unknown",
                            }
                        )

                # Sort by elevation if available
                try:
                    levels_info.sort(
                        key=lambda x: (
                            x["elevation"]
                            if isinstance(x["elevation"], (int, float))
                            else 0
                        )
                    )
                except:
                    pass

            except Exception as e:
                logger.warning("Could not get levels: {}".format(str(e)))
                levels_info = []

            # ============ ROOMS ============
            try:
                rooms_collector = (
                    DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_Rooms)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )

                rooms_info = []
                unplaced_rooms = 0

                for room in rooms_collector:
                    try:
                        # Get room name safely
                        name_param = room.LookupParameter("Name")
                        room_name = (
                            name_param.AsString()
                            if name_param and name_param.HasValue
                            else "Unnamed Room"
                        )

                        # Get room number safely
                        number_param = room.LookupParameter("Number")
                        room_number = (
                            number_param.AsString()
                            if number_param and number_param.HasValue
                            else ""
                        )

                        # Get room level
                        level_name = "Unknown Level"
                        try:
                            level = doc.GetElement(room.LevelId)
                            if level:
                                level_name = get_element_name(level)
                        except:
                            pass

                        # Check if room is placed
                        try:
                            area = room.Area
                            is_placed = area > 0
                            if not is_placed:
                                unplaced_rooms += 1
                        except:
                            is_placed = False
                            unplaced_rooms += 1

                        room_info = {
                            "name": normalize_string(room_name),
                            "number": normalize_string(room_number),
                            "level": normalize_string(level_name),
                            "is_placed": is_placed,
                        }

                        if is_placed:
                            try:
                                room_info["area"] = round(area, 2)
                            except:
                                room_info["area"] = "Unknown"

                        rooms_info.append(room_info)

                    except Exception as e:
                        logger.warning("Could not process room: {}".format(str(e)))
                        continue

            except Exception as e:
                logger.warning("Could not get rooms: {}".format(str(e)))
                rooms_info = []
                unplaced_rooms = 0

            # ============ VIEWS AND SHEETS ============
            try:
                # Get sheets
                sheets_count = (
                    DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_Sheets)
                    .WhereElementIsNotElementType()
                    .GetElementCount()
                )

                # Get views (excluding templates and invalid types)
                all_views = (
                    DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
                )

                valid_views = [
                    v
                    for v in all_views
                    if hasattr(v, "IsTemplate")
                    and not v.IsTemplate
                    and v.ViewType != DB.ViewType.Internal
                    and v.ViewType != DB.ViewType.ProjectBrowser
                ]

                views_count = len(valid_views)

                # Count major view types
                floor_plans = sum(
                    1 for v in valid_views if v.ViewType == DB.ViewType.FloorPlan
                )
                elevations = sum(
                    1 for v in valid_views if v.ViewType == DB.ViewType.Elevation
                )
                sections = sum(
                    1 for v in valid_views if v.ViewType == DB.ViewType.Section
                )
                threed_views = sum(
                    1 for v in valid_views if v.ViewType == DB.ViewType.ThreeD
                )
                schedules = sum(
                    1 for v in valid_views if v.ViewType == DB.ViewType.Schedule
                )

            except Exception as e:
                logger.warning("Could not get views/sheets: {}".format(str(e)))
                sheets_count = 0
                views_count = 0
                floor_plans = elevations = sections = threed_views = schedules = 0

            # ============ LINKED MODELS ============
            try:
                linked_models = []
                rvt_links = q.get_linked_model_instances(doc).ToElements()

                for link_instance in rvt_links:
                    try:
                        link_doc = link_instance.GetLinkDocument()
                        link_name = (
                            q.get_rvt_link_instance_name(link_instance)
                            if hasattr(q, "get_rvt_link_instance_name")
                            else "Unknown Link"
                        )

                        # Get load status
                        link_type = doc.GetElement(link_instance.GetTypeId())
                        status = (
                            str(link_type.GetLinkedFileStatus()).split(".")[-1]
                            if link_type
                            else "Unknown"
                        )

                        # Check if pinned
                        is_pinned = getattr(link_instance, "Pinned", False)

                        linked_models.append(
                            {
                                "name": normalize_string(link_name),
                                "status": status,
                                "is_loaded": link_doc is not None,
                                "is_pinned": is_pinned,
                            }
                        )

                    except Exception as e:
                        logger.warning(
                            "Could not process linked model: {}".format(str(e))
                        )
                        continue

            except Exception as e:
                logger.warning("Could not get linked models: {}".format(str(e)))
                linked_models = []

            # ============ COMPILE RESPONSE ============
            model_data = {
                "status": "success",
                "project_info": project_info,
                "element_summary": {
                    "total_elements": total_elements,
                    "by_category": element_counts,
                },
                "model_health": {
                    "total_warnings": warnings_count,
                    "critical_warnings": critical_warnings,
                    "unplaced_rooms": unplaced_rooms,
                },
                "spatial_organization": {
                    "levels": levels_info,
                    "rooms": rooms_info,
                    "room_count": len(rooms_info),
                },
                "documentation": {
                    "total_views": views_count,
                    "view_breakdown": {
                        "floor_plans": floor_plans,
                        "elevations": elevations,
                        "sections": sections,
                        "3d_views": threed_views,
                        "schedules": schedules,
                    },
                    "sheets_count": sheets_count,
                },
                "linked_models": {"count": len(linked_models), "models": linked_models},
            }

            return routes.make_response(data=model_data)

        except Exception as e:
            logger.error("Failed to get model info: {}".format(str(e)))
            return routes.make_response(
                data={
                    "error": "Failed to retrieve model information: {}".format(str(e))
                },
                status=500,
            )

    logger.info("Model info routes registered successfully")
