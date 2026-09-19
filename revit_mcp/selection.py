# -*- coding: UTF-8 -*-
"""
Selection Module for Revit MCP
Provides information about the user's current element selection in Revit.
"""
from pyrevit import routes, DB
import logging

from utils import normalize_string, get_element_name, element_id_value

logger = logging.getLogger(__name__)


def register_selection_routes(api):
    """Register selection-related routes with the API"""

    @api.route("/selected_elements/", methods=["GET"])
    def get_selected_elements(doc, uidoc):
        """
        Get information about the elements currently selected by the user
        in the Revit UI.

        Returns:
            dict: List of selected elements with id, category, name and type.
        """
        try:
            if not doc or not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            selected_ids = uidoc.Selection.GetElementIds()

            elements_info = []
            for elem_id in selected_ids:
                try:
                    elem = doc.GetElement(elem_id)
                    if elem is None:
                        continue

                    cat = elem.Category
                    cat_name = cat.Name if cat else "Unknown"

                    type_name = None
                    try:
                        type_id = elem.GetTypeId()
                        if type_id and type_id != DB.ElementId.InvalidElementId:
                            type_elem = doc.GetElement(type_id)
                            if type_elem:
                                type_name = normalize_string(
                                    get_element_name(type_elem)
                                )
                    except Exception:
                        type_name = None

                    elements_info.append(
                        {
                            "element_id": element_id_value(elem.Id),
                            "name": normalize_string(get_element_name(elem)),
                            "category": cat_name,
                            "type_name": type_name,
                        }
                    )
                except Exception as e:
                    logger.warning(
                        "Could not process selected element: {}".format(str(e))
                    )
                    continue

            return routes.make_response(
                data={
                    "status": "success",
                    "count": len(elements_info),
                    "elements": elements_info,
                }
            )

        except Exception as e:
            logger.error("Failed to get selected elements: {}".format(str(e)))
            return routes.make_response(
                data={
                    "error": "Failed to get selected elements: {}".format(str(e))
                },
                status=500,
            )

    logger.info("Selection routes registered successfully")
