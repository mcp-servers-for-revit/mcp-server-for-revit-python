# -*- coding: utf-8 -*-
"""Code execution tools for the MCP server."""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_code_execution_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register code execution tools with the MCP server."""
    # Note: revit_get and revit_image are unused but kept for interface consistency
    _ = revit_get, revit_image  # Acknowledge unused parameters

    @mcp.tool()
    async def execute_revit_code(
        code: str, description: str = "Code execution", ctx: Context = None
    ) -> str:
        """
        Execute IronPython 2.7 code directly in Revit context (Revit 2027).

        Note: this is IronPython 2.7, not CPython 3. No f-strings, use
        "{}".format(x). Integer division truncates. No numpy/pandas/requests.

        The code has access to:
        - doc: The active Revit document
        - uidoc: The active UIDocument (use for UI operations like switching the active view)
        - DB: Revit API Database namespace
        - revit: pyRevit module
        - System (the .NET System namespace)
        - json: json module whose dumps() also accepts Int64, ElementId, XYZ
        - print: Function to output text (returned in response)

        Helpers injected to avoid the common Revit 2027 / IronPython failures:
        - eid(value)      -> DB.ElementId from an int/str/enum. ALWAYS use this instead
                             of DB.ElementId(n): a bare int is ambiguous on Revit 2024+
                             ("Multiple targets could match: ElementId(BuiltInParameter)...").
        - id_of(element)  -> plain int id of an element or ElementId. Use this instead of
                             element.Id.Value when the value will be printed or serialised
                             (Value is Int64 -> "0L is not JSON serializable").
                             ElementId.IntegerValue no longer exists.
        - name_of(element)-> element name, safe on DirectShape / IFC-imported elements
                             where element.Name raises "AttributeError: Name".
        - to_json(obj)    -> JSON string with Revit values converted.

        doc.GetElement(id) returns None for a bad id and LookupParameter() returns
        None when the parameter is missing: check before indexing or reading.

        No transaction is opened automatically. Wrap model-modifying code yourself:
            t = DB.Transaction(doc, "My change")
            t.Start()
            # ... modify model ...
            t.Commit()

        For UI operations that cannot run inside a transaction (e.g. switching the active view):
            all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
            target = next((v for v in all_views if name_of(v) == "Level 1"), None)
            if target:
                uidoc.ActiveView = target

        Example that reads safely:
            el = doc.GetElement(eid(123456))
            if el:
                print(to_json({"id": id_of(el), "name": name_of(el)}))

        Keep collectors filtered at the source (OfCategory / OfClass /
        WhereElementIsNotElementType); never collect the whole model and filter in Python.
        """
        try:
            payload = {"code": code, "description": description}

            if ctx:
                await ctx.info("Executing code: {}".format(description))

            response = await revit_post("/execute_code/", payload, ctx, timeout=60.0)
            return format_response(response)

        except (ConnectionError, ValueError, RuntimeError) as e:
            error_msg = "Error during code execution: {}".format(str(e))
            if ctx:
                await ctx.error(error_msg)
            return error_msg
