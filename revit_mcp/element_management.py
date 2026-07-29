# -*- coding: UTF-8 -*-
"""
Element Management Module for Revit MCP
Handles modification of existing elements in the model.
"""

from pyrevit import routes, DB
from System.Collections.Generic import List
import json
import logging
import traceback

from utils import element_id_value

logger = logging.getLogger(__name__)


def register_element_management_routes(api):
    """Register all element management routes with the API"""

    @api.route("/modify_element/", methods=["POST"])
    def modify_element(doc, request):
        """
        Modify instance parameters of an existing element.

        Expected request data:
        {
            "element_id": 123456,
            "properties": {
                "Mark": "A2",
                "Comments": "Updated via MCP"
            }
        }
        """
        data = None
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            if not request or not request.data:
                return routes.make_response(
                    data={"error": "No data provided or invalid request format"},
                    status=400,
                )

            if isinstance(request.data, str):
                try:
                    data = json.loads(request.data)
                except Exception as json_err:
                    return routes.make_response(
                        data={
                            "error": "Invalid JSON format: {}".format(str(json_err))
                        },
                        status=400,
                    )
            else:
                data = request.data

            if not data or not isinstance(data, dict):
                return routes.make_response(
                    data={"error": "Invalid data format - expected JSON object"},
                    status=400,
                )

            element_id = data.get("element_id")
            properties = data.get("properties", {})

            if element_id is None:
                return routes.make_response(
                    data={"error": "No element_id provided"}, status=400
                )

            if not properties or not isinstance(properties, dict):
                return routes.make_response(
                    data={
                        "error": "No properties provided - expected a dict of "
                        "parameter_name: value"
                    },
                    status=400,
                )

            try:
                eid = DB.ElementId(int(element_id))
            except (ValueError, TypeError):
                return routes.make_response(
                    data={"error": "Invalid element_id: {}".format(element_id)},
                    status=400,
                )

            element = doc.GetElement(eid)
            if not element:
                return routes.make_response(
                    data={"error": "Element not found: {}".format(element_id)},
                    status=404,
                )

            logger.info(
                "Modifying element {}: {}".format(element_id, list(properties.keys()))
            )

            t = DB.Transaction(doc, "Modify Element via MCP")
            t.Start()

            try:
                properties_set = []
                properties_failed = []

                for param_name, param_value in properties.items():
                    try:
                        param = element.LookupParameter(param_name)
                        if param and not param.IsReadOnly:
                            if param.StorageType == DB.StorageType.String:
                                param.Set(str(param_value))
                                properties_set.append(param_name)
                            elif param.StorageType == DB.StorageType.Integer:
                                param.Set(int(param_value))
                                properties_set.append(param_name)
                            elif param.StorageType == DB.StorageType.Double:
                                param.Set(float(param_value))
                                properties_set.append(param_name)
                            elif param.StorageType == DB.StorageType.ElementId:
                                param.Set(DB.ElementId(int(param_value)))
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

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                    logger.error("Transaction rolled back due to error")
                raise tx_error

            return routes.make_response(
                data={
                    "status": "success",
                    "element_id": element_id_value(element.Id),
                    "properties_set": properties_set,
                    "properties_failed": properties_failed,
                }
            )

        except Exception as e:
            logger.error("Failed to modify element: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={
                    "error": str(e),
                    "traceback": error_trace,
                    "element_id": data.get("element_id") if data else None,
                },
                status=500,
            )

    @api.route("/delete_elements/", methods=["POST"])
    def delete_elements(doc, request):
        """
        Delete one or more elements from the model.

        Expected request data:
        {
            "element_ids": [123456, 789012]
        }
        A single "element_id" (int) is also accepted as a shorthand for one element.
        """
        data = None
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            if not request or not request.data:
                return routes.make_response(
                    data={"error": "No data provided or invalid request format"},
                    status=400,
                )

            if isinstance(request.data, str):
                try:
                    data = json.loads(request.data)
                except Exception as json_err:
                    return routes.make_response(
                        data={
                            "error": "Invalid JSON format: {}".format(str(json_err))
                        },
                        status=400,
                    )
            else:
                data = request.data

            if not data or not isinstance(data, dict):
                return routes.make_response(
                    data={"error": "Invalid data format - expected JSON object"},
                    status=400,
                )

            element_ids = data.get("element_ids")
            if element_ids is None:
                single = data.get("element_id")
                if single is not None:
                    element_ids = [single]

            if not element_ids or not isinstance(element_ids, list):
                return routes.make_response(
                    data={
                        "error": "No element_ids provided - expected a list of "
                        "element IDs (or element_id for a single one)"
                    },
                    status=400,
                )

            # Resolve and validate elements before starting the transaction
            target_ids = []
            not_found = []
            for eid in element_ids:
                try:
                    elem_id = DB.ElementId(int(eid))
                except (ValueError, TypeError):
                    not_found.append({"element_id": eid, "error": "Invalid element_id"})
                    continue

                elem = doc.GetElement(elem_id)
                if not elem:
                    not_found.append({"element_id": eid, "error": "Element not found"})
                    continue

                target_ids.append(elem_id)

            if not target_ids:
                return routes.make_response(
                    data={
                        "error": "None of the provided element_ids exist in the model",
                        "not_found": not_found,
                    },
                    status=404,
                )

            logger.info(
                "Deleting {} element(s): {}".format(
                    len(target_ids),
                    [element_id_value(e) for e in target_ids],
                )
            )

            t = DB.Transaction(doc, "Delete Elements via MCP")
            t.Start()

            try:
                id_collection = List[DB.ElementId](target_ids)
                deleted_ids = doc.Delete(id_collection)
                t.Commit()
                logger.info("Transaction committed successfully")
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                    logger.error("Transaction rolled back due to error")
                raise tx_error

            deleted_id_values = (
                [element_id_value(d) for d in deleted_ids] if deleted_ids else []
            )

            result = {
                "status": "success",
                "requested_count": len(element_ids),
                "deleted_count": len(deleted_id_values),
                "deleted_ids": deleted_id_values,
            }

            if not_found:
                result["not_found"] = not_found

            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Failed to delete elements: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={
                    "error": str(e),
                    "traceback": error_trace,
                    "element_ids": data.get("element_ids") if data else None,
                },
                status=500,
            )

    logger.info("Element management routes registered successfully")
