# -*- coding: UTF-8 -*-
"""
Placement Module for Revit MCP
Handles family placement and element creation functionality
"""

from utils import get_element_name, find_family_symbol_safely, normalize_string, element_id_value, fix_request_string
from pyrevit import routes, revit, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)


def register_placement_routes(api):
    """Register all placement-related routes with the API"""

    @api.route("/place_family/", methods=["POST"])
    def place_family(doc, request):
        """
        Place a family instance at a specified location in the model.

        Expected request data:
        {
            "family_name": "Basic Wall",
            "type_name": "Generic - 200mm",
            "location": {"x": 0.0, "y": 0.0, "z": 0.0},
            "rotation": 0.0,
            "level_name": "Level 1",
            "properties": {
                "Mark": "A1",
                "Comments": "Placed through API"
            }
        }
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            # Parse request data
            if not request or not request.data:
                return routes.make_response(
                    data={"error": "No data provided or invalid request format"},
                    status=400,
                )

            # Parse JSON if needed
            data = None
            if isinstance(request.data, str):
                try:
                    data = json.loads(request.data)
                except Exception as json_err:
                    return routes.make_response(
                        data={"error": "Invalid JSON format: {}".format(str(json_err))},
                        status=400,
                    )
            else:
                data = request.data

            # Validate data structure
            if not data or not isinstance(data, dict):
                return routes.make_response(
                    data={"error": "Invalid data format - expected JSON object"},
                    status=400,
                )

            # Extract required fields
            # family_name/type_name/level_name are run through fix_request_string()
            # because the Routes server mis-decodes non-ASCII request bodies (see
            # fix_request_string's docstring) -- without it, any accented family,
            # type, or level name silently fails to match against Revit's own
            # (correctly decoded) element names.
            family_name = fix_request_string(data.get("family_name"))
            type_name = fix_request_string(data.get("type_name"))
            location = data.get("location", {})
            rotation = data.get("rotation", 0.0)
            level_name = fix_request_string(data.get("level_name"))
            properties = data.get("properties", {})

            # Basic validation
            if not family_name:
                return routes.make_response(
                    data={"error": "No family_name provided"}, status=400
                )

            # Validate location
            if not location or not all(k in location for k in ["x", "y", "z"]):
                return routes.make_response(
                    data={
                        "error": "Invalid location - must include x, y, z coordinates"
                    },
                    status=400,
                )

            logger.info(
                "Placing family: {} - {}".format(
                    family_name, type_name or "Default Type"
                )
            )

            # Find the appropriate family symbol (type)
            target_symbol = find_family_symbol_safely(doc, family_name, type_name)

            if not target_symbol:
                # Get list of available families for better error message
                available_families = []
                try:
                    symbols = (
                        DB.FilteredElementCollector(doc)
                        .OfClass(DB.FamilySymbol)
                        .ToElements()
                    )
                    family_names = set()
                    for symbol in symbols[
                        :200
                    ]:  # Limit to prevent overwhelming response
                        try:
                            family_name_safe = normalize_string(symbol.Family.Name)
                            family_names.add(family_name_safe)
                        except:
                            continue
                    available_families = sorted(list(family_names))
                except:
                    available_families = ["Could not retrieve family list"]

                return routes.make_response(
                    data={
                        "error": "Family type not found: {} - {}".format(
                            family_name, type_name or "Any"
                        ),
                        "available_families": available_families[:20],  # Show first 20
                    },
                    status=404,
                )

            # Find level if specified
            target_level = None
            if level_name:
                levels = (
                    DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_Levels)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )

                for level in levels:
                    try:
                        level_name_safe = get_element_name(level)
                        if level_name_safe == level_name:
                            target_level = level
                            break
                    except:
                        continue

                if not target_level:
                    return routes.make_response(
                        data={"error": "Level not found: {}".format(level_name)},
                        status=404,
                    )

            # Create the location point
            try:
                point = DB.XYZ(
                    float(location["x"]), float(location["y"]), float(location["z"])
                )
            except (ValueError, TypeError) as coord_error:
                return routes.make_response(
                    data={"error": "Invalid coordinates: {}".format(str(coord_error))},
                    status=400,
                )

            # Start a transaction
            transaction_name = "Place Family Instance via MCP"
            t = DB.Transaction(doc, transaction_name)
            t.Start()

            try:
                # Ensure the symbol is activated
                if not target_symbol.IsActive:
                    target_symbol.Activate()
                    doc.Regenerate()  # Ensure activation takes effect

                # Create the instance
                if target_level:
                    # Place on specific level
                    new_instance = doc.Create.NewFamilyInstance(
                        point,
                        target_symbol,
                        target_level,
                        DB.Structure.StructuralType.NonStructural,
                    )
                else:
                    # Place without level specification
                    new_instance = doc.Create.NewFamilyInstance(
                        point, target_symbol, DB.Structure.StructuralType.NonStructural
                    )

                logger.info(
                    "Family instance created with ID: {}".format(
                        element_id_value(new_instance.Id)
                    )
                )

                # Apply rotation if specified
                if rotation != 0:
                    try:
                        rotation_radians = float(rotation) * (3.14159265359 / 180.0)
                        axis = DB.Line.CreateBound(point, point.Add(DB.XYZ(0, 0, 1)))

                        if hasattr(new_instance.Location, "Rotate"):
                            success = new_instance.Location.Rotate(
                                axis, rotation_radians
                            )
                            if success:
                                logger.info(
                                    "Element rotated by {} degrees".format(rotation)
                                )
                            else:
                                logger.warning(
                                    "Rotation failed - element may not support rotation"
                                )
                    except Exception as rotate_err:
                        logger.warning(
                            "Could not rotate element: {}".format(str(rotate_err))
                        )

                # Set custom properties
                properties_set = []
                properties_failed = []

                for param_name, param_value in properties.items():
                    try:
                        param = new_instance.LookupParameter(param_name)
                        if param and not param.IsReadOnly:
                            # Set parameter based on its storage type
                            if param.StorageType == DB.StorageType.String:
                                param.Set(str(param_value))
                                properties_set.append(param_name)
                            elif param.StorageType == DB.StorageType.Integer:
                                param.Set(int(param_value))
                                properties_set.append(param_name)
                            elif param.StorageType == DB.StorageType.Double:
                                param.Set(float(param_value))
                                properties_set.append(param_name)
                            else:
                                properties_failed.append(
                                    "{} (unsupported type)".format(param_name)
                                )
                        else:
                            if param:
                                properties_failed.append(
                                    "{} (read-only)".format(param_name)
                                )
                            else:
                                properties_failed.append(
                                    "{} (not found)".format(param_name)
                                )
                    except Exception as param_error:
                        properties_failed.append(
                            "{} (error: {})".format(param_name, str(param_error))
                        )

                t.Commit()
                logger.info("Transaction committed successfully")

                # Get actual placed location (may differ due to level constraints)
                try:
                    actual_location = new_instance.Location.Point
                    actual_coords = {
                        "x": actual_location.X,
                        "y": actual_location.Y,
                        "z": actual_location.Z,
                    }
                except:
                    actual_coords = {"x": point.X, "y": point.Y, "z": point.Z}

                # Return information about the placed instance
                response_data = {
                    "status": "success",
                    "element_id": element_id_value(new_instance.Id),
                    "family_name": family_name,
                    "type_name": type_name,
                    "requested_location": {"x": point.X, "y": point.Y, "z": point.Z},
                    "actual_location": actual_coords,
                    "rotation_degrees": rotation,
                    "level": level_name if target_level else None,
                    "properties_set": properties_set,
                    "properties_failed": properties_failed,
                }

                return routes.make_response(data=response_data)

            except Exception as tx_error:
                # Roll back the transaction if something went wrong
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                    logger.error("Transaction rolled back due to error")
                raise tx_error

        except Exception as e:
            logger.error("Failed to place family: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    @api.route("/list_families/", methods=["POST"])
    def list_families(doc, request):
        """
        Get a flat list of family names and their types in the current Revit model.

        Optional POST body:
        {
            "contains": "chair",   # case-insensitive substring filter on family or type name
            "limit": 50            # max results (default 50)
        }

        Returns:
            list: [{ 'family_name': str, 'type_name': str, 'category': str, 'is_active': bool }]
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            # Parse optional filter params from JSON body
            contains_filter = None
            limit = 50
            if request and request.data:
                try:
                    body = json.loads(request.data) if isinstance(request.data, str) else request.data
                    if isinstance(body, dict):
                        contains_filter = body.get("contains")
                        limit = int(body.get("limit", 50))
                except Exception:
                    pass

            symbols = (
                DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()
            )
            families = []
            for symbol in symbols:
                if len(families) >= limit:
                    break
                try:
                    fam_name = normalize_string(symbol.Family.Name)
                    type_name = normalize_string(get_element_name(symbol))
                    category = symbol.Category.Name if symbol.Category else "Unknown"
                    is_active = symbol.IsActive

                    # Apply contains filter (case-insensitive, matches family or type name)
                    if contains_filter:
                        needle = normalize_string(contains_filter).lower()
                        if needle not in fam_name.lower() and needle not in type_name.lower():
                            continue

                    families.append(
                        {
                            "family_name": fam_name,
                            "type_name": type_name,
                            "category": category,
                            "is_active": is_active,
                        }
                    )
                except Exception:
                    continue
            return routes.make_response(
                data={
                    "families": families,
                    "count": len(families),
                    "filtered_by": contains_filter,
                    "status": "success",
                }
            )
        except Exception as e:
            logger.error("Failed to list families: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to list families: {}".format(str(e))}, status=500
            )

    @api.route("/list_family_categories/", methods=["GET"])
    def list_family_categories(doc):
        """
        Get a list of all family categories in the current Revit model

        Returns:
            dict: List of categories with family counts
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            logger.info("Listing all family categories")

            # Get all family symbols
            symbols = (
                DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()
            )

            categories = {}

            for symbol in symbols:
                try:
                    # Get category name
                    category_name = "Unknown"
                    try:
                        if symbol.Category:
                            category_name = symbol.Category.Name
                    except:
                        pass

                    if category_name not in categories:
                        categories[category_name] = 0

                    categories[category_name] += 1

                except Exception as e:
                    logger.warning("Could not process family symbol: {}".format(str(e)))
                    continue

            # Sort by name
            sorted_categories = dict(sorted(categories.items()))

            return routes.make_response(
                data={
                    "categories": sorted_categories,
                    "total_categories": len(sorted_categories),
                    "status": "success",
                }
            )

        except Exception as e:
            logger.error("Failed to list family categories: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to list family categories: {}".format(str(e))},
                status=500,
            )

    @api.route("/list_levels/", methods=["GET"])
    def list_levels(doc):
        """
        Get a list of all levels in the current Revit model

        Returns:
            dict: List of levels with their elevations
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            logger.info("Listing all available levels")

            # Get all levels
            levels = (
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Levels)
                .WhereElementIsNotElementType()
                .ToElements()
            )

            levels_info = []

            for level in levels:
                try:
                    level_name = get_element_name(level)
                    elevation = level.Elevation

                    levels_info.append(
                        {
                            "name": level_name,
                            "elevation": round(elevation, 2),
                            "id": element_id_value(level.Id),
                        }
                    )

                except Exception as e:
                    logger.warning("Could not process level: {}".format(str(e)))
                    continue

            # Sort by elevation
            levels_info.sort(key=lambda x: x["elevation"])

            return routes.make_response(
                data={
                    "levels": levels_info,
                    "total_levels": len(levels_info),
                    "status": "success",
                }
            )

        except Exception as e:
            logger.error("Failed to list levels: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to list levels: {}".format(str(e))}, status=500
            )

    logger.info("Placement routes registered successfully")
