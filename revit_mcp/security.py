# -*- coding: utf-8 -*-
"""Authentication and in-Revit review for bridge requests."""
import json
from pyrevit import routes
from .pairing import validate_request


def install_header_passthrough(handler_class, log=None):
    """Copy HTTP headers onto pyRevit route requests.

    pyRevit builds route requests without headers, so the bearer check below
    would always fail. Returns False when the class is already patched.
    """
    original = handler_class._prepare_request
    if getattr(original, "_revit_mcp_headers", False):
        return False

    def _prepare_request(self, route, path, method):
        request = original(self, route, path, method)
        try:
            for key, value in self.headers.items():
                request.add_header(key, value)
        except Exception as error:
            # The route then denies the request; pyRevit's own routes keep working.
            if log:
                log("Header passthrough failed: {}".format(error))
        return request

    _prepare_request._revit_mcp_headers = True
    handler_class._prepare_request = _prepare_request
    return True


def authorize_request(request, action=None):
    """Return a denial response, or None when the request may proceed."""
    # pyRevit's dispatcher can post a route response to callbackUrl. Remove it
    # before returning so even a denial cannot initiate an undeclared request.
    if isinstance(request.data, dict) and "callbackUrl" in request.data:
        request.data.pop("callbackUrl", None)
        return routes.make_response(data={"error": "Callbacks are not supported."}, status=400)
    if not validate_request(request.headers):
        return routes.make_response(data={"error": "Bridge is disconnected or pairing is invalid."}, status=401)
    if action:
        from pyrevit import forms
        try:
            data = json.loads(request.data) if isinstance(request.data, str) else request.data
            preview = json.dumps(data, indent=2, ensure_ascii=True)
        except (ValueError, TypeError):
            return routes.make_response(data={"error": "Invalid request payload."}, status=400)
        warning = "An MCP client requests: {}.\nReview the full request below before allowing it.".format(action)
        if action == "Execute Python code":
            warning += "\nThis code is NOT sandboxed. It can access files, network services and any data available to your Windows account."
        if not forms.alert(warning, sub_msg=preview, title="Revit MCP request review",
                           ok=False, yes=True, no=True, warn_icon=True):
            return routes.make_response(data={"error": "The user declined the request in Revit."}, status=403)
    return None
