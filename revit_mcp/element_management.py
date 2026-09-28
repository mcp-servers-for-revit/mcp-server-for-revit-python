# -*- coding: UTF-8 -*-
"""
Element Management Module for Revit MCP
Handles deleting and modifying elements, and resetting elements created via
MCP tools during the current Revit session.
"""
from pyrevit import routes, DB
import json
import logging

from utils import (
    element_id_value,
    element_id_from_value,
    get_element_name,
    normalize_string,
)
import creation_tracker

logger = logging.getLogger(__name__)


def register_element_management_routes(api):
    """Register element management routes with the API"""

    @api.route("/delete_elements/", methods=["POST"])
    def delete_elements(doc, request):
        """
        Delete specified elements from the model.

        Expected payload:
        {
            "element_ids": [123456, 123457]
        }
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = (
                json.loads(request.data)
                if isinstance(request.data, str)
                else request.data
            )
            element_ids = data.get("element_ids") if data else None

            if not element_ids:
                return routes.make_response(
                    data={"error": "No element_ids provided"}, status=400
                )

            deleted = []
            not_found = []

            t = DB.Transaction(doc, "Delete Elements via MCP")
            t.Start()
            try:
                for raw_id in element_ids:
                    try:
                        int_id = int(raw_id)
                        eid = element_id_from_value(int_id)
                        elem = doc.GetElement(eid)
                        if elem is None:
                            not_found.append(int_id)
                            continue
                        doc.Delete(eid)
                        deleted.append(int_id)
                    except Exception as del_err:
                        logger.warning(
                            "Could not delete element {}: {}".format(
                                raw_id, str(del_err)
                            )
                        )
                        not_found.append(raw_id)
                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            creation_tracker.discard(deleted)

            return routes.make_response(
                data={
                    "status": "success",
                    "deleted": deleted,
                    "deleted_count": len(deleted),
                    "not_found": not_found,
                }
            )

        except Exception as e:
            logger.error("Failed to delete elements: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to delete elements: {}".format(str(e))},
                status=500,
            )

    @api.route("/modify_element/", methods=["POST"])
    def modify_element(doc, request):
        """
        Modify instance parameters on an existing element.

        Expected payload:
        {
            "element_id": 123456,
            "properties": {"Comments": "Updated via MCP", "Mark": "A1"}
        }
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = (
                json.loads(request.data)
                if isinstance(request.data, str)
                else request.data
            )
            element_id = data.get("element_id") if data else None
            properties = data.get("properties") if data else None

            if element_id is None:
                return routes.make_response(
                    data={"error": "No element_id provided"}, status=400
                )
            if not properties:
                return routes.make_response(
                    data={"error": "No properties provided"}, status=400
                )

            elem = doc.GetElement(element_id_from_value(element_id))
            if elem is None:
                return routes.make_response(
                    data={"error": "Element not found: {}".format(element_id)},
                    status=404,
                )

            properties_set = []
            properties_failed = []

            t = DB.Transaction(doc, "Modify Element via MCP")
            t.Start()
            try:
                for param_name, param_value in properties.items():
                    try:
                        param = elem.LookupParameter(param_name)
                        if not param:
                            properties_failed.append(
                                "{} (not found)".format(param_name)
                            )
                            continue
                        if param.IsReadOnly:
                            properties_failed.append(
                                "{} (read-only)".format(param_name)
                            )
                            continue

                        if param.StorageType == DB.StorageType.String:
                            param.Set(str(param_value))
                        elif param.StorageType == DB.StorageType.Integer:
                            param.Set(int(param_value))
                        elif param.StorageType == DB.StorageType.Double:
                            param.Set(float(param_value))
                        elif param.StorageType == DB.StorageType.ElementId:
                            param.Set(element_id_from_value(param_value))
                        else:
                            properties_failed.append(
                                "{} (unsupported type)".format(param_name)
                            )
                            continue

                        properties_set.append(param_name)
                    except Exception as param_error:
                        properties_failed.append(
                            "{} (error: {})".format(param_name, str(param_error))
                        )

                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            return routes.make_response(
                data={
                    "status": "success",
                    "element_id": element_id_value(elem.Id),
                    "properties_set": properties_set,
                    "properties_failed": properties_failed,
                }
            )

        except Exception as e:
            logger.error("Failed to modify element: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to modify element: {}".format(str(e))},
                status=500,
            )

    @api.route("/reset_model/", methods=["POST"])
    def reset_model(doc, request=None):
        """
        Delete elements created via MCP tools during this Revit session
        (tracked in-memory since pyRevit last loaded, or since the last
        reset). Never touches elements that already existed in the model.

        Expected payload:
        {
            "confirm": false   # must be true to actually delete
        }
        With confirm=false (default), returns a preview of what would be
        deleted instead of deleting anything.
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            confirm = False
            if request and request.data:
                try:
                    data = (
                        json.loads(request.data)
                        if isinstance(request.data, str)
                        else request.data
                    )
                    confirm = bool(data.get("confirm", False))
                except Exception:
                    confirm = False

            tracked_ids = creation_tracker.get_created_ids()

            preview = []
            valid_ids = []
            for raw_id in tracked_ids:
                try:
                    eid = element_id_from_value(raw_id)
                    elem = doc.GetElement(eid)
                    if elem is None:
                        continue
                    valid_ids.append(raw_id)
                    preview.append(
                        {
                            "element_id": raw_id,
                            "category": (
                                elem.Category.Name if elem.Category else "Unknown"
                            ),
                            "name": normalize_string(get_element_name(elem)),
                        }
                    )
                except Exception:
                    continue

            if not confirm:
                return routes.make_response(
                    data={
                        "status": "preview",
                        "message": (
                            "Dry run - pass confirm=true to delete these "
                            "elements."
                        ),
                        "candidate_count": len(preview),
                        "candidates": preview,
                    }
                )

            deleted = []
            t = DB.Transaction(doc, "Reset Model via MCP")
            t.Start()
            try:
                for raw_id in valid_ids:
                    try:
                        doc.Delete(element_id_from_value(raw_id))
                        deleted.append(raw_id)
                    except Exception as del_err:
                        logger.warning(
                            "Could not delete element {} during reset: {}".format(
                                raw_id, str(del_err)
                            )
                        )
                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            creation_tracker.discard(deleted)

            return routes.make_response(
                data={
                    "status": "success",
                    "deleted_count": len(deleted),
                    "deleted": deleted,
                }
            )

        except Exception as e:
            logger.error("Failed to reset model: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to reset model: {}".format(str(e))},
                status=500,
            )

    logger.info("Element management routes registered successfully")
