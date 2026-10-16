# -*- coding: utf-8 -*-
"""Authentication and in-Revit review for bridge requests."""
import json
from pyrevit import routes
from .pairing import AUTH_FIELD, validate_request


def _pop_body_token(request):
    """Remove the pairing secret from the payload so routes and review never see it."""
    data = request.data
    if data and not isinstance(data, dict):
        try:
            parsed = json.loads(data)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            request.data = data = parsed
    if isinstance(data, dict):
        return data.pop(AUTH_FIELD, None)
    return None


def authorize_request(request, action=None):
    """Return a denial response, or None when the request may proceed."""
    body_token = _pop_body_token(request)
    # pyRevit's dispatcher can post a route response to callbackUrl. Remove it
    # before returning so even a denial cannot initiate an undeclared request.
    if isinstance(request.data, dict) and "callbackUrl" in request.data:
        request.data.pop("callbackUrl", None)
        return routes.make_response(data={"error": "Callbacks are not supported."}, status=400)
    if not validate_request(request.headers, body_token):
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
