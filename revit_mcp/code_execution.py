# -*- coding: UTF-8 -*-
"""
Code Execution Module for Revit MCP
Handles direct execution of IronPython code in Revit context.
"""
from pyrevit import routes, revit, DB
import System
import json
import logging
import sys
import traceback
from StringIO import StringIO

from .utils import element_id_value, get_element_name

# Standard logger setup
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers injected into the script namespace.
#
# These exist because ad-hoc scripts keep hitting the same IronPython 2.7 /
# Revit 2027 pitfalls. Each helper maps to one recurring error:
#
#   eid(x)      -> "Multiple targets could match: ElementId(BuiltInParameter)..."
#   name_of(el) -> "AttributeError: Name"
#   id_of(el)   -> "0L is not JSON serializable"
#   json.dumps  -> same, when the script serialises Int64 / ElementId itself
# ---------------------------------------------------------------------------


def _eid(value):
    """Build an ElementId without tripping IronPython overload resolution.

    Revit 2024+ exposes ElementId(Int64), ElementId(BuiltInCategory) and
    ElementId(BuiltInParameter). A plain Python int/long is ambiguous between
    them, so we force Int64. Enums and existing ElementIds pass straight
    through. Strings are accepted because ids often arrive from JSON.
    """
    if isinstance(value, DB.ElementId):
        return value
    if isinstance(value, (DB.BuiltInCategory, DB.BuiltInParameter)):
        return DB.ElementId(value)
    return DB.ElementId(System.Int64(int(value)))


def _id_of(element_or_id):
    """Return the integer value of an element's id (or of an ElementId)."""
    if element_or_id is None:
        return None
    if isinstance(element_or_id, DB.ElementId):
        return element_id_value(element_or_id)
    return element_id_value(element_or_id.Id)


def _name_of(element, default=u""):
    """Return an element's Name even when IronPython hides the property."""
    if element is None:
        return default
    try:
        name = get_element_name(element)
    except Exception:
        return default
    return name if name is not None else default


def _json_default(obj):
    """Fallback used by the injected json.dumps for .NET-flavoured values."""
    if isinstance(obj, DB.ElementId):
        return element_id_value(obj)
    if isinstance(obj, (System.Int64, System.Int32, System.Int16, long)):
        return int(obj)
    if isinstance(obj, (System.Double, System.Single, System.Decimal)):
        return float(obj)
    if isinstance(obj, System.Boolean):
        return bool(obj)
    if isinstance(obj, DB.XYZ):
        return [obj.X, obj.Y, obj.Z]
    return unicode(obj)


class _SafeJson(object):
    """Drop-in stand-in for the json module that survives Revit values.

    Scripts do ``json.dumps(data)``; this version silently converts Int64,
    ElementId, XYZ and enums instead of raising ``... is not JSON serializable``.
    """

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("default", _json_default)
        return json.dumps(obj, **kwargs)

    def dump(self, obj, fp, **kwargs):
        kwargs.setdefault("default", _json_default)
        return json.dump(obj, fp, **kwargs)

    def loads(self, s, **kwargs):
        return json.loads(s, **kwargs)

    def load(self, fp, **kwargs):
        return json.load(fp, **kwargs)


def _to_json(obj, **kwargs):
    """Serialise obj to a JSON string, converting Revit values on the way."""
    kwargs.setdefault("default", _json_default)
    return json.dumps(obj, **kwargs)


def _build_hints(error_type, error_msg):
    """Map a failure to a short, actionable hint for the caller."""
    hints = []
    if "Multiple targets could match" in error_msg and "ElementId" in error_msg:
        hints.append(
            "DB.ElementId(<python int>) is ambiguous on Revit 2024+. "
            "Use the injected helper eid(value), or DB.ElementId(System.Int64(value))."
        )
    elif "not JSON serializable" in error_msg:
        hints.append(
            "A .NET value (Int64 / ElementId / XYZ) reached the JSON encoder. "
            "Use id_of(element) for ids and to_json(obj) or the injected json.dumps, "
            "which convert these automatically; or wrap numbers in int()."
        )
    elif error_type == "AttributeError":
        if "Name" in error_msg:
            hints.append(
                "IronPython hides Element.Name on some types (DirectShape, IFC imports). "
                "Use the injected helper name_of(element), or DB.Element.Name.__get__(element)."
            )
        elif "IntegerValue" in error_msg:
            hints.append(
                "ElementId.IntegerValue was removed in Revit 2026+. "
                "Use id_of(element) or element.Id.Value."
            )
        else:
            hints.append(
                "Some Revit API properties are not directly accessible in IronPython. "
                "Try getattr(obj, 'property_name', default_value) for safe access."
            )
    elif error_type == "NullReferenceException" or "NoneType" in error_msg:
        hints.append(
            "An object is None/null. doc.GetElement() returns None for a bad id and "
            "LookupParameter() returns None when the parameter is missing. "
            "Check 'if element:' before indexing or reading properties."
        )
    elif error_type == "InvalidOperationException":
        hints.append(
            "This operation may require a transaction. Wrap model-modifying "
            "code in: t = DB.Transaction(doc, 'desc'); t.Start(); ...; t.Commit()"
        )
    return hints


def register_code_execution_routes(api):
    """Register code execution routes with the API."""

    @api.route("/execute_code/", methods=["POST"])
    def execute_code(doc, uidoc, request):
        """
        Execute IronPython code in Revit context.

        Expected payload:
        {
            "code": "python code as string",
            "description": "optional description of what the code does",
            "use_transaction": true   # set false for UI ops like switching the active view
        }
        """
        try:
            # Parse the request data
            data = (
                json.loads(request.data)
                if isinstance(request.data, str)
                else request.data
            )
            code_to_execute = data.get("code", "")
            description = data.get("description", "Code execution")

            if not code_to_execute:
                return routes.make_response(
                    data={"error": "No code provided"}, status=400
                )

            logger.info("Executing code: {}".format(description))

            old_stdout = sys.stdout
            captured_output = StringIO()
            sys.stdout = captured_output

            namespace = {
                "doc": doc,
                "uidoc": uidoc,
                "DB": DB,
                "revit": revit,
                "System": System,
                "json": _SafeJson(),
                "eid": _eid,
                "id_of": _id_of,
                "name_of": _name_of,
                "to_json": _to_json,
                "__builtins__": __builtins__,
                "print": lambda *args: captured_output.write(
                    " ".join(unicode(arg) for arg in args) + "\n"
                ),
            }

            try:
                exec(code_to_execute, namespace)

                sys.stdout = old_stdout
                output = captured_output.getvalue()
                captured_output.close()

                return routes.make_response(
                    data={
                        "status": "success",
                        "description": description,
                        "output": (
                            output
                            if output
                            else "Code executed successfully (no output)"
                        ),
                        "code_executed": code_to_execute,
                    }
                )

            except Exception as exec_error:
                sys.stdout = old_stdout
                partial_output = captured_output.getvalue()
                captured_output.close()

                error_traceback = traceback.format_exc()
                error_type = type(exec_error).__name__
                error_msg = str(exec_error)
                enhanced_message = "{}: {}".format(error_type, error_msg)
                hints = _build_hints(error_type, error_msg)

                logger.error("Code execution failed: {}".format(enhanced_message))

                response_data = {
                    "status": "error",
                    "error": enhanced_message,
                    "error_type": error_type,
                    "traceback": error_traceback,
                    "code_attempted": code_to_execute,
                }

                if partial_output:
                    response_data["partial_output"] = partial_output

                if hints:
                    response_data["hints"] = hints

                return routes.make_response(data=response_data, status=500)

        except Exception as e:
            logger.error("Execute code request failed: {}".format(str(e)))
            return routes.make_response(data={"error": str(e)}, status=500)

    logger.info("Code execution routes registered successfully.")
