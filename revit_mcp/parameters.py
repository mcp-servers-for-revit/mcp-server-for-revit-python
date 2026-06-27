# -*- coding: UTF-8 -*-
"""
Parameters Module for Revit MCP
Handles reading element properties and setting parameter values
"""

from utils import get_element_name, get_element_id_value, make_element_id
from pyrevit import routes, revit, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)


def _safe_str(value):
    """Convert a value to a JSON-safe ASCII string, replacing problematic chars.
    IronPython 2.7 compatible — handles both str (bytes) and unicode."""
    try:
        if value is None:
            return ""
        # Try unicode first (IronPython 2.7 has unicode type)
        try:
            s = unicode(value)
        except Exception:
            try:
                s = str(value)
            except Exception:
                return ""
        # Strip any char with ordinal >= 128
        result = []
        for ch in s:
            try:
                o = ord(ch)
                if o < 128:
                    result.append(chr(o))
                else:
                    result.append("?")
            except Exception:
                result.append("?")
        return "".join(result)
    except Exception:
        return ""


def _get_param_group_name(param):
    """Get the parameter group name safely across Revit versions."""
    try:
        # Revit 2024+
        if hasattr(param.Definition, "GetGroupTypeId"):
            group_id = param.Definition.GetGroupTypeId()
            return _safe_str(DB.LabelUtils.GetLabelForGroup(group_id))
        # Older Revit
        if hasattr(param.Definition, "ParameterGroup"):
            return _safe_str(param.Definition.ParameterGroup)
    except Exception:
        pass
    return "Other"


def _get_param_value_display(param, doc):
    """Get a display-friendly parameter value."""
    try:
        if not param.HasValue:
            return ""
        if param.StorageType == DB.StorageType.String:
            return _safe_str(param.AsString() or "")
        elif param.StorageType == DB.StorageType.Integer:
            display = param.AsValueString()
            if display:
                return _safe_str(display)
            return str(param.AsInteger())
        elif param.StorageType == DB.StorageType.Double:
            display = param.AsValueString()
            if display:
                return _safe_str(display)
            return str(round(param.AsDouble(), 6))
        elif param.StorageType == DB.StorageType.ElementId:
            eid = param.AsElementId()
            if eid and eid != DB.ElementId.InvalidElementId:
                elem = doc.GetElement(eid)
                if elem:
                    return _safe_str(get_element_name(elem))
            return ""
    except Exception:
        return ""
    return ""


def register_parameter_routes(api):
    """Register all parameter routes with the API"""

    @api.route("/element_properties/<element_id>", methods=["GET"])
    def get_element_properties_handler(doc, element_id):
        """Get all properties and parameters of an element."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            elem_id = make_element_id(int(element_id))
            elem = doc.GetElement(elem_id)
            if not elem:
                return routes.make_response(
                    data={"error": "Element not found."},
                    status=404,
                )

            category = ""
            try:
                if elem.Category:
                    category = _safe_str(elem.Category.Name)
            except Exception:
                pass

            family = ""
            type_name = ""
            try:
                type_id = elem.GetTypeId()
                if type_id and type_id != DB.ElementId.InvalidElementId:
                    elem_type = doc.GetElement(type_id)
                    if elem_type:
                        type_name = _safe_str(get_element_name(elem_type))
                        if hasattr(elem_type, "Family") and elem_type.Family:
                            family = _safe_str(get_element_name(elem_type.Family))
                        elif hasattr(elem_type, "FamilyName"):
                            family = _safe_str(elem_type.FamilyName)
            except Exception:
                pass

            # Collect instance parameters
            parameters = []
            seen_names = set()

            for param in elem.GetOrderedParameters():
                try:
                    param_name = _safe_str(param.Definition.Name)
                    if param_name in seen_names:
                        continue
                    seen_names.add(param_name)

                    parameters.append({
                        "name": param_name,
                        "value": _safe_str(_get_param_value_display(param, doc)),
                        "storage_type": str(param.StorageType),
                        "read_only": param.IsReadOnly,
                        "group": _safe_str(_get_param_group_name(param)),
                        "is_instance": True,
                    })
                except Exception:
                    continue

            # Collect type parameters
            try:
                type_id = elem.GetTypeId()
                if type_id and type_id != DB.ElementId.InvalidElementId:
                    elem_type = doc.GetElement(type_id)
                    if elem_type:
                        for param in elem_type.GetOrderedParameters():
                            try:
                                param_name = _safe_str(param.Definition.Name)
                                if param_name in seen_names:
                                    continue
                                seen_names.add(param_name)

                                parameters.append({
                                    "name": param_name,
                                    "value": _safe_str(_get_param_value_display(param, doc)),
                                    "storage_type": str(param.StorageType),
                                    "read_only": param.IsReadOnly,
                                    "group": _safe_str(_get_param_group_name(param)),
                                    "is_instance": False,
                                })
                            except Exception:
                                continue
            except Exception:
                pass

            return routes.make_response(
                data={
                    "status": "success",
                    "element_id": int(element_id),
                    "category": category,
                    "family": family,
                    "type": type_name,
                    "parameters": parameters,
                    "parameter_count": len(parameters),
                    "message": "Found {} parameters on element {}".format(
                        len(parameters), element_id
                    ),
                }
            )

        except Exception as e:
            logger.error("Failed to get element properties: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    @api.route("/set_parameter/", methods=["POST"])
    def set_parameter_handler(doc, request):
        """Set a single parameter value on an element."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            element_id = data.get("element_id")
            parameter_name = data.get("parameter_name")
            value = data.get("value")

            if element_id is None:
                return routes.make_response(
                    data={"error": "element_id is required"}, status=400
                )
            if not parameter_name:
                return routes.make_response(
                    data={"error": "parameter_name is required"}, status=400
                )
            if value is None:
                return routes.make_response(
                    data={"error": "value is required"}, status=400
                )

            elem_id = make_element_id(element_id)
            elem = doc.GetElement(elem_id)
            if not elem:
                return routes.make_response(
                    data={"error": "Element {} not found".format(element_id)},
                    status=404,
                )

            # Find the parameter
            param = elem.LookupParameter(parameter_name)
            if not param:
                # Try type parameters
                type_id = elem.GetTypeId()
                if type_id and type_id != DB.ElementId.InvalidElementId:
                    elem_type = doc.GetElement(type_id)
                    if elem_type:
                        param = elem_type.LookupParameter(parameter_name)

            if not param:
                available = []
                for p in elem.Parameters:
                    try:
                        available.append(p.Definition.Name)
                    except Exception:
                        continue
                available.sort()
                return routes.make_response(
                    data={
                        "error": "Parameter '{}' not found on element {}".format(
                            parameter_name, element_id
                        ),
                        "available_parameters": available[:30],
                    },
                    status=404,
                )

            if param.IsReadOnly:
                return routes.make_response(
                    data={"error": "Parameter '{}' is read-only and cannot be modified.".format(parameter_name)},
                    status=500,
                )

            # Get old value
            old_value = _get_param_value_display(param, doc)

            t = DB.Transaction(doc, "Set Parameter via MCP")
            t.Start()

            try:
                # Set value based on storage type
                if param.StorageType == DB.StorageType.String:
                    param.Set(str(value))
                elif param.StorageType == DB.StorageType.Integer:
                    param.Set(int(value))
                elif param.StorageType == DB.StorageType.Double:
                    param.Set(float(value))
                elif param.StorageType == DB.StorageType.ElementId:
                    param.Set(make_element_id(value))

                t.Commit()

                new_value = _get_param_value_display(param, doc)

                return routes.make_response(
                    data={
                        "status": "success",
                        "element_id": int(element_id),
                        "parameter_name": parameter_name,
                        "old_value": old_value,
                        "new_value": str(value),
                        "message": "Set '{}' from '{}' to '{}' on element {}".format(
                            parameter_name, old_value, value, element_id
                        ),
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to set parameter: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Parameter routes registered successfully")
