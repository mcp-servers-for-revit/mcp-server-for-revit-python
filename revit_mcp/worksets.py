# -*- coding: UTF-8 -*-
"""
Worksets Module for Revit MCP

Creates user worksets in a workshared model.

Worksets only exist once a document has worksharing enabled. This endpoint
creates a workset in an ALREADY-workshared model and returns
status='not_workshared' otherwise -- it deliberately does NOT enable
worksharing itself, because EnableWorksharing is a one-way, model-wide
change that should be an explicit user decision, not an implicit side effect.

Worksharing operations are not transactional: Workset.Create must NOT run
inside a DB.Transaction.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string
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


def _workset_id_value(workset_id):
    """Best-effort integer value of a WorksetId, across Revit versions."""
    try:
        return workset_id.IntegerValue
    except Exception:
        try:
            return int(str(workset_id))
        except Exception:
            return None


def _existing_user_worksets(doc):
    """Return {name: WorksetId} for every user workset in the document."""
    result = {}
    collector = DB.FilteredWorksetCollector(doc).OfKind(DB.WorksetKind.UserWorkset)
    for ws in collector:
        result[ws.Name] = ws.Id
    return result


def register_workset_routes(api):
    """Register workset-creation routes."""

    @api.route("/create_workset/", methods=["POST"])
    def create_workset(doc, request):
        """
        Create a user workset in a workshared model.

        Expected payload:
        {
            "name": "Shell & Core"   # required, unique within the project
        }

        Returns status='not_workshared' when the document has no worksharing,
        status='name_collision' when a workset with that name already exists.
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            name = data.get("name")
            if not name or not isinstance(name, str) or not name.strip():
                return routes.make_response(data={
                    "error": "Missing required field: name (non-empty string)"
                }, status=400)
            name = name.strip()

            if not doc.IsWorkshared:
                return routes.make_response(data={
                    "status": "not_workshared",
                    "error": "This document is not workshared, so it has no user "
                             "worksets. Enable worksharing in Revit (Collaborate "
                             "tab) first -- this tool will not enable worksharing "
                             "implicitly.",
                })

            # Refuse a duplicate name. Check user worksets first so we can
            # report the existing id; IsWorksetNameUnique additionally covers
            # standard/family/view worksets.
            existing = _existing_user_worksets(doc)
            if name in existing:
                return routes.make_response(data={
                    "status": "name_collision",
                    "error": "A workset named '{}' already exists.".format(name),
                    "existing_workset_id": _workset_id_value(existing[name]),
                })
            if not DB.WorksetTable.IsWorksetNameUnique(doc, name):
                return routes.make_response(data={
                    "status": "name_collision",
                    "error": "The workset name '{}' is not available.".format(name),
                })

            # Worksharing operations are not transactional -- no DB.Transaction.
            new_ws = DB.Workset.Create(doc, name)

            return routes.make_response(data={
                "status": "success",
                "workset": {
                    "id": _workset_id_value(new_ws.Id),
                    "name": normalize_string(new_ws.Name),
                    "kind": "UserWorkset",
                },
            })

        except Exception as e:
            logger.error("create_workset failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Workset routes registered successfully")
