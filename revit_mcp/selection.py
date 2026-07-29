# -*- coding: UTF-8 -*-
"""
Selection Module for Revit MCP
Handles reading the user's current element selection in the active Revit UI.
"""

from pyrevit import routes, DB
import json
import logging

from utils import normalize_string, get_element_name, element_id_value

logger = logging.getLogger(__name__)


def register_selection_routes(api):
    """Register all selection-related routes with the API"""

    @api.route("/selected_elements/", methods=["POST"])
    def get_selected_elements(doc, uidoc, request):
        """
        Get information about the elements currently selected in the Revit UI.

        Expected JSON payload (all fields optional):
        {
            "limit": 5000,
            "include_levels": false,
            "include_location": false
        }
        """
        try:
            if not doc or not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            # Parse optional parameters from request body
            limit = 5000
            include_levels = False
            include_location = False
            try:
                if request and request.data:
                    data = (
                        json.loads(request.data)
                        if isinstance(request.data, str)
                        else request.data
                    )
                    limit = int(data.get("limit", 5000))
                    include_levels = bool(data.get("include_levels", False))
                    include_location = bool(data.get("include_location", False))
            except Exception:
                pass  # Use defaults if parsing fails

            selected_ids = uidoc.Selection.GetElementIds()

            if not selected_ids or selected_ids.Count == 0:
                return routes.make_response(
                    data={
                        "status": "success",
                        "total_elements": 0,
                        "returned_elements": 0,
                        "limit": limit,
                        "truncated": False,
                        "elements": [],
                        "category_counts": {},
                        "message": "No elements are currently selected in Revit.",
                    }
                )

            # Level cache to avoid redundant doc.GetElement() calls
            level_cache = {}  # {int(level_id): {"name": str, "id": int}}

            elements_info = []
            category_counts = {}
            total_elements = 0

            for elem_id in selected_ids:
                try:
                    elem = doc.GetElement(elem_id)
                    if not elem:
                        continue

                    # Get category name for counting (always counted, even beyond limit)
                    cat = elem.Category
                    if cat:
                        cat_name = cat.Name
                    else:
                        cat_name = "Unknown"

                    if cat_name in category_counts:
                        category_counts[cat_name] = category_counts[cat_name] + 1
                    else:
                        category_counts[cat_name] = 1
                    total_elements = total_elements + 1

                    # Only build full element info up to the limit
                    if len(elements_info) >= limit:
                        continue

                    element_info = {
                        "element_id": element_id_value(elem.Id),
                        "name": normalize_string(get_element_name(elem)),
                        "category": cat_name,
                    }

                    if cat:
                        element_info["category_id"] = element_id_value(cat.Id)
                    else:
                        element_info["category_id"] = None

                    # Add level information only if requested (opt-in)
                    if include_levels:
                        try:
                            level_param = elem.get_Parameter(
                                DB.BuiltInParameter.FAMILY_LEVEL_PARAM
                            )
                            if level_param:
                                level_id = level_param.AsElementId()
                                if level_id != DB.ElementId.InvalidElementId:
                                    lid = element_id_value(level_id)
                                    if lid in level_cache:
                                        cached = level_cache[lid]
                                        element_info["level"] = cached["name"]
                                        element_info["level_id"] = cached["id"]
                                    else:
                                        level_elem = doc.GetElement(level_id)
                                        lname = normalize_string(
                                            get_element_name(level_elem)
                                        )
                                        level_cache[lid] = {"name": lname, "id": lid}
                                        element_info["level"] = lname
                                        element_info["level_id"] = lid
                                else:
                                    element_info["level"] = None
                                    element_info["level_id"] = None
                            else:
                                element_info["level"] = None
                                element_info["level_id"] = None
                        except Exception:
                            element_info["level"] = None
                            element_info["level_id"] = None

                    # Add location information only if requested (opt-in)
                    if include_location:
                        try:
                            location = elem.Location
                            if hasattr(location, "Point"):
                                pt = location.Point
                                element_info["location"] = {
                                    "type": "point",
                                    "x": pt.X,
                                    "y": pt.Y,
                                    "z": pt.Z,
                                }
                            elif hasattr(location, "Curve"):
                                curve = location.Curve
                                start = curve.GetEndPoint(0)
                                end = curve.GetEndPoint(1)
                                element_info["location"] = {
                                    "type": "curve",
                                    "start": {"x": start.X, "y": start.Y, "z": start.Z},
                                    "end": {"x": end.X, "y": end.Y, "z": end.Z},
                                }
                            else:
                                element_info["location"] = {"type": "unknown"}
                        except Exception:
                            element_info["location"] = {"type": "unknown"}

                    elements_info.append(element_info)

                except Exception as elem_error:
                    logger.warning(
                        "Could not process selected element: {}".format(
                            str(elem_error)
                        )
                    )
                    continue

            truncated = total_elements > len(elements_info)

            result = {
                "status": "success",
                "total_elements": total_elements,
                "returned_elements": len(elements_info),
                "limit": limit,
                "truncated": truncated,
                "elements": elements_info,
                "category_counts": category_counts,
            }

            if truncated:
                result["message"] = (
                    "Results truncated: showing {} of {} elements. "
                    "Use limit parameter to retrieve more.".format(
                        len(elements_info), total_elements
                    )
                )

            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Get selected elements failed: {}".format(str(e)))
            return routes.make_response(
                data={
                    "error": "Failed to get selected elements: {}".format(str(e))
                },
                status=500,
            )

    logger.info("Selection routes registered successfully")
