# -*- coding: UTF-8 -*-
"""
Selection Module for Revit MCP
Exposes the currently-selected elements in the active Revit UI.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
import logging

logger = logging.getLogger(__name__)


def register_selection_routes(api):
    """Register all selection-related routes with the API"""

    @api.route("/selection/", methods=["GET"])
    def get_selected_elements(doc):
        """Return the elements currently selected by the user in the Revit UI."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            uidoc = revit.uidoc
            if not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit UI document"}, status=503
                )

            sel_ids = list(uidoc.Selection.GetElementIds())
            items = []
            for eid in sel_ids:
                el = doc.GetElement(eid)
                if el is None:
                    continue
                cat = el.Category
                type_el = doc.GetElement(el.GetTypeId()) if el.GetTypeId() != DB.ElementId.InvalidElementId else None
                items.append({
                    "id": element_id_value(eid),
                    "name": normalize_string(get_element_name(el)),
                    "category": normalize_string(cat.Name) if cat else u"(no category)",
                    "type_id": element_id_value(el.GetTypeId()) if el.GetTypeId() != DB.ElementId.InvalidElementId else None,
                    "type_name": normalize_string(get_element_name(type_el)) if type_el is not None else None,
                    "level_id": element_id_value(el.LevelId) if hasattr(el, "LevelId") and el.LevelId != DB.ElementId.InvalidElementId else None,
                })

            return routes.make_response(data={
                "status": "success",
                "count": len(items),
                "elements": items,
            })

        except Exception as e:
            logger.error("get_selected_elements failed: {}".format(str(e)))
            return routes.make_response(data={"error": str(e)}, status=500)

    logger.info("Selection routes registered successfully")
