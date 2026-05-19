# -*- coding: UTF-8 -*-
"""
Element Management Module for Revit MCP
Delete, modify-parameter, and reset endpoints. All mutating endpoints honour
a `confirm` flag in the payload — if False (the default), the endpoint
performs a dry-run lookup and returns what it WOULD do, without modifying
the model. The MCP-side tool wrappers enforce the same gate at the
user-facing layer.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
from System import Int64
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


# Categories considered "process" content for reset_model — geometry the user
# would typically generate or import, NOT views/levels/sheets/families/types.
# Bias toward leaving the project skeleton intact unless the caller explicitly
# expands the list.
_DEFAULT_RESET_CATEGORIES = [
    DB.BuiltInCategory.OST_Walls,
    DB.BuiltInCategory.OST_Floors,
    DB.BuiltInCategory.OST_Ceilings,
    DB.BuiltInCategory.OST_Roofs,
    DB.BuiltInCategory.OST_Doors,
    DB.BuiltInCategory.OST_Windows,
    DB.BuiltInCategory.OST_Furniture,
    DB.BuiltInCategory.OST_GenericModel,
    DB.BuiltInCategory.OST_Stairs,
    DB.BuiltInCategory.OST_Railings,
    DB.BuiltInCategory.OST_Columns,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFoundation,
]


def _category_name_to_bic(name):
    """Map a friendly category name (e.g. 'walls') to BuiltInCategory."""
    if not name:
        return None
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "wall": DB.BuiltInCategory.OST_Walls,
        "walls": DB.BuiltInCategory.OST_Walls,
        "floor": DB.BuiltInCategory.OST_Floors,
        "floors": DB.BuiltInCategory.OST_Floors,
        "ceiling": DB.BuiltInCategory.OST_Ceilings,
        "ceilings": DB.BuiltInCategory.OST_Ceilings,
        "roof": DB.BuiltInCategory.OST_Roofs,
        "roofs": DB.BuiltInCategory.OST_Roofs,
        "door": DB.BuiltInCategory.OST_Doors,
        "doors": DB.BuiltInCategory.OST_Doors,
        "window": DB.BuiltInCategory.OST_Windows,
        "windows": DB.BuiltInCategory.OST_Windows,
        "furniture": DB.BuiltInCategory.OST_Furniture,
        "generic_model": DB.BuiltInCategory.OST_GenericModel,
        "stair": DB.BuiltInCategory.OST_Stairs,
        "stairs": DB.BuiltInCategory.OST_Stairs,
        "railing": DB.BuiltInCategory.OST_Railings,
        "railings": DB.BuiltInCategory.OST_Railings,
        "column": DB.BuiltInCategory.OST_Columns,
        "columns": DB.BuiltInCategory.OST_Columns,
        "structural_framing": DB.BuiltInCategory.OST_StructuralFraming,
        "structural_columns": DB.BuiltInCategory.OST_StructuralColumns,
        "structural_foundation": DB.BuiltInCategory.OST_StructuralFoundation,
    }
    return aliases.get(key)


def register_element_management_routes(api):
    """Register delete / modify / reset endpoints."""

    @api.route("/delete_elements/", methods=["POST"])
    def delete_elements(doc, request):
        """
        Delete elements by ID.

        Expected payload:
        {
            "element_ids": [12345, 67890],
            "confirm": false                 // must be true to actually delete
        }

        If confirm=false: returns a preview of what would be deleted (id, name, category).
        If confirm=true:  runs the delete inside a single transaction.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _parse_json_request(request)
            ids = data.get("element_ids") or []
            confirm = bool(data.get("confirm", False))
            if not isinstance(ids, list) or not ids:
                return routes.make_response(data={"error": "element_ids must be a non-empty list of integers"}, status=400)

            # Lookup phase (always runs)
            preview = []
            valid_ids = []
            for raw_id in ids:
                try:
                    eid = DB.ElementId(Int64(int(raw_id)))
                    el = doc.GetElement(eid)
                    if el is None:
                        preview.append({"id": int(raw_id), "status": "not_found"})
                        continue
                    cat = el.Category
                    preview.append({
                        "id": int(raw_id),
                        "name": normalize_string(get_element_name(el)),
                        "category": normalize_string(cat.Name) if cat else u"(no category)",
                        "status": "would_delete",
                    })
                    valid_ids.append(eid)
                except Exception as ex:
                    preview.append({"id": raw_id, "status": "error", "error": str(ex)})

            if not confirm:
                return routes.make_response(data={
                    "status": "preview",
                    "confirm_required": True,
                    "would_delete_count": len(valid_ids),
                    "preview": preview,
                    "hint": "Re-call with confirm=true to actually delete.",
                })

            if not valid_ids:
                return routes.make_response(data={
                    "status": "no_op",
                    "preview": preview,
                    "message": "No valid element IDs to delete.",
                })

            with DB.Transaction(doc, "MCP: Delete Elements") as t:
                t.Start()
                from System.Collections.Generic import List as NetList
                id_list = NetList[DB.ElementId]()
                for eid in valid_ids:
                    id_list.Add(eid)
                deleted = doc.Delete(id_list)
                t.Commit()

            return routes.make_response(data={
                "status": "success",
                "deleted_count": len(deleted),
                "deleted_ids": [element_id_value(d) for d in deleted],
                "preview": preview,
            })

        except Exception as e:
            logger.error("delete_elements failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/modify_element/", methods=["POST"])
    def modify_element(doc, request):
        """
        Modify instance parameters of a single element.

        Expected payload:
        {
            "element_id": 12345,
            "parameters": {
                "Comments": "Updated via MCP",
                "Mark": "A1"
            }
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _parse_json_request(request)
            raw_id = data.get("element_id")
            params = data.get("parameters") or {}
            if raw_id is None:
                return routes.make_response(data={"error": "Missing element_id"}, status=400)
            if not isinstance(params, dict) or not params:
                return routes.make_response(data={"error": "parameters must be a non-empty dict"}, status=400)

            eid = DB.ElementId(Int64(int(raw_id)))
            el = doc.GetElement(eid)
            if el is None:
                return routes.make_response(data={"error": "Element {} not found".format(raw_id)}, status=404)

            results = []
            with DB.Transaction(doc, "MCP: Modify Element") as t:
                t.Start()
                for name, value in params.items():
                    try:
                        p = el.LookupParameter(name)
                        if p is None:
                            results.append({"parameter": name, "status": "not_found"})
                            continue
                        if p.IsReadOnly:
                            results.append({"parameter": name, "status": "read_only"})
                            continue
                        st = p.StorageType
                        if st == DB.StorageType.String:
                            p.Set(unicode(value) if isinstance(value, str) else value)
                        elif st == DB.StorageType.Integer:
                            p.Set(int(value))
                        elif st == DB.StorageType.Double:
                            p.Set(float(value))
                        elif st == DB.StorageType.ElementId:
                            p.Set(DB.ElementId(Int64(int(value))))
                        else:
                            results.append({"parameter": name, "status": "unsupported_storage_type", "storage": str(st)})
                            continue
                        results.append({"parameter": name, "status": "set", "value": value})
                    except Exception as pe:
                        results.append({"parameter": name, "status": "error", "error": str(pe)})
                t.Commit()

            return routes.make_response(data={
                "status": "success",
                "element_id": int(raw_id),
                "element_name": normalize_string(get_element_name(el)),
                "results": results,
            })

        except Exception as e:
            logger.error("modify_element failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/reset_model/", methods=["POST"])
    def reset_model(doc, request):
        """
        Delete all instances of the specified categories. DESTRUCTIVE.

        Expected payload:
        {
            "categories": ["walls", "doors", "windows"],   // optional; defaults to a built-in safe set
            "confirm": false                                // must be true to execute
        }

        Always returns a preview first. Only proceeds when confirm=true.
        Does NOT delete views, levels, sheets, family types, or other "project skeleton" content.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _parse_json_request(request)
            confirm = bool(data.get("confirm", False))
            requested = data.get("categories")

            if requested:
                bics = []
                unknown = []
                for name in requested:
                    bic = _category_name_to_bic(name)
                    if bic is None:
                        unknown.append(name)
                    else:
                        bics.append(bic)
                if unknown:
                    return routes.make_response(data={
                        "error": "Unknown categories: {}. Supported: walls, floors, ceilings, roofs, doors, windows, furniture, generic_model, stairs, railings, columns, structural_framing, structural_columns, structural_foundation".format(unknown),
                    }, status=400)
            else:
                bics = list(_DEFAULT_RESET_CATEGORIES)

            # Lookup phase
            preview_by_cat = {}
            all_ids = []
            for bic in bics:
                try:
                    cat_name = str(bic).split(".")[-1]
                except Exception:
                    cat_name = u"?"
                col = (DB.FilteredElementCollector(doc)
                       .OfCategory(bic)
                       .WhereElementIsNotElementType())
                ids = [el.Id for el in col]
                preview_by_cat[cat_name] = len(ids)
                all_ids.extend(ids)

            if not confirm:
                return routes.make_response(data={
                    "status": "preview",
                    "confirm_required": True,
                    "would_delete_total": len(all_ids),
                    "would_delete_by_category": preview_by_cat,
                    "hint": "Re-call with confirm=true to actually wipe. DESTRUCTIVE — undo only via Revit's own undo stack.",
                })

            if not all_ids:
                return routes.make_response(data={
                    "status": "no_op",
                    "by_category": preview_by_cat,
                    "message": "Nothing to delete in the requested categories.",
                })

            with DB.Transaction(doc, "MCP: Reset Model") as t:
                t.Start()
                from System.Collections.Generic import List as NetList
                id_list = NetList[DB.ElementId]()
                for eid in all_ids:
                    id_list.Add(eid)
                deleted = doc.Delete(id_list)
                t.Commit()

            return routes.make_response(data={
                "status": "success",
                "deleted_count": len(deleted),
                "by_category_attempted": preview_by_cat,
            })

        except Exception as e:
            logger.error("reset_model failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Element-management routes registered successfully")
