# -*- coding: UTF-8 -*-
"""
Integration Module for Revit MCP
Discover other pyRevit extensions installed on the system and invoke named
functions from their lib/ directories.

`use_module` is intentionally narrow: it imports the named module from the
named extension's `lib/` directory and calls a named function. This is
strictly more limited than `execute_revit_code` (no arbitrary statements,
must be a function that already exists). Confirmation gate present anyway.
"""

from pyrevit import routes
import os
import json
import importlib
import traceback
import logging

logger = logging.getLogger(__name__)


def _parse_json_request(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        try:
            return json.loads(request.data)
        except Exception:
            return {}
    return request.data or {}


def _list_extensions():
    """Discover pyRevit extensions on disk.

    Returns a list of {name, path, type, has_lib, command_count} dicts.
    Scans pyRevit's bundled extensions root plus all user-configured
    custom-extension parent dirs.
    """
    try:
        from pyrevit.userconfig import user_config
        from pyrevit import EXTENSIONS_DEFAULT_DIR
    except ImportError:
        return []

    roots = set()
    try:
        if EXTENSIONS_DEFAULT_DIR and os.path.exists(EXTENSIONS_DEFAULT_DIR):
            roots.add(EXTENSIONS_DEFAULT_DIR)
    except Exception:
        pass
    try:
        for d in user_config.get_thirdparty_ext_root_dirs():
            if os.path.exists(d):
                roots.add(d)
    except Exception:
        pass

    found = []
    for root in roots:
        try:
            for name in os.listdir(root):
                full = os.path.join(root, name)
                if not os.path.isdir(full):
                    continue
                if name.endswith(".extension"):
                    ext_type = "ui"
                elif name.endswith(".lib"):
                    ext_type = "lib"
                else:
                    continue
                lib_path = os.path.join(full, "lib")
                # Quick command count: walk for .pushbutton folders (UI extensions only)
                cmd_count = 0
                if ext_type == "ui":
                    for dirpath, dirnames, _ in os.walk(full):
                        for dn in dirnames:
                            if dn.endswith(".pushbutton"):
                                cmd_count += 1
                found.append({
                    "name": name,
                    "path": full,
                    "type": ext_type,
                    "has_lib": os.path.isdir(lib_path),
                    "command_count": cmd_count,
                })
        except Exception as e:
            logger.warning("Could not scan extension root {}: {}".format(root, str(e)))
    return found


def register_integration_routes(api):
    """Register module-discovery and module-invocation endpoints."""

    @api.route("/search_modules/", methods=["GET", "POST"])
    def search_modules(request):
        """
        List installed pyRevit extensions and (optionally) filter by query.

        GET: returns all installed extensions.
        POST payload: {"query": "tag"} — case-insensitive substring filter on name.
        """
        try:
            data = _parse_json_request(request) if request else {}
            query = (data.get("query") or "").strip().lower() if isinstance(data, dict) else ""

            extensions = _list_extensions()
            if query:
                extensions = [e for e in extensions if query in e["name"].lower()]

            return routes.make_response(data={
                "status": "success",
                "query": query or None,
                "count": len(extensions),
                "extensions": extensions,
            })
        except Exception as e:
            logger.error("search_modules failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/use_module/", methods=["POST"])
    def use_module(doc, request):
        """
        Import a named module from a named extension's lib/ directory and
        call a named function. Stricter than `execute_revit_code` — the
        callable must already exist; no arbitrary statements.

        Expected payload:
        {
            "extension_name": "my-tools.extension",      // full folder name (with .extension or .lib suffix)
            "module_path":    "submodule.helpers",       // dotted import path under <extension>/lib/
            "function_name":  "do_thing",
            "args":           [],                         // positional args (optional, must be JSON-serialisable)
            "kwargs":         {},                         // keyword args (optional)
            "confirm":        false                        // must be true to execute (gate)
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            ext_name = data.get("extension_name")
            module_path = data.get("module_path")
            function_name = data.get("function_name")
            args = data.get("args") or []
            kwargs = data.get("kwargs") or {}
            confirm = bool(data.get("confirm", False))

            for field, val in (("extension_name", ext_name), ("module_path", module_path), ("function_name", function_name)):
                if not val:
                    return routes.make_response(data={"error": "Missing required field: {}".format(field)}, status=400)

            # Locate the extension folder
            target = None
            for ext in _list_extensions():
                if ext["name"] == ext_name:
                    target = ext
                    break
            if target is None:
                return routes.make_response(data={"error": "Extension not found: {}".format(ext_name)}, status=404)
            if not target["has_lib"]:
                return routes.make_response(data={
                    "error": "Extension '{}' has no lib/ directory; cannot use_module against it.".format(ext_name),
                }, status=400)

            lib_dir = os.path.join(target["path"], "lib")

            if not confirm:
                return routes.make_response(data={
                    "status": "preview",
                    "confirm_required": True,
                    "would_invoke": {
                        "extension": ext_name,
                        "lib_dir": lib_dir,
                        "module": module_path,
                        "function": function_name,
                        "args": args,
                        "kwargs": kwargs,
                    },
                    "hint": "Re-call with confirm=true to actually invoke. Note: arbitrary Python runs in Revit context with full API access.",
                })

            # Inject lib_dir into sys.path, import, call
            import sys
            inserted = False
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
                inserted = True
            try:
                if module_path in sys.modules:
                    # Force a fresh import so dev iteration on the called module takes effect
                    mod = importlib.reload(sys.modules[module_path])
                else:
                    mod = importlib.import_module(module_path)

                fn = getattr(mod, function_name, None)
                if fn is None or not callable(fn):
                    return routes.make_response(data={
                        "error": "Function '{}' not found (or not callable) in module '{}'".format(function_name, module_path),
                    }, status=404)

                result = fn(*args, **kwargs)
                # Best-effort JSON-safety on the result
                try:
                    json.dumps(result)
                    safe_result = result
                except (TypeError, ValueError):
                    safe_result = unicode(result) if result is not None else None

                return routes.make_response(data={
                    "status": "success",
                    "extension": ext_name,
                    "module": module_path,
                    "function": function_name,
                    "result": safe_result,
                })
            finally:
                if inserted:
                    try:
                        sys.path.remove(lib_dir)
                    except ValueError:
                        pass

        except Exception as e:
            logger.error("use_module failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Integration routes registered successfully")
