# -*- coding: utf-8 -*-
"""Launch and discovery tools for Revit instances"""

import os
import subprocess
import json
import anyio
from typing import Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def _find_revit_installations():
    """Scan the system for installed Revit versions.

    Checks Windows Registry and common filesystem paths.
    Returns a list of {"year": str, "path": str} sorted newest-first.
    """
    found = {}

    # Strategy 1: Windows Registry
    try:
        import winreg

        base_key_path = r"SOFTWARE\Autodesk\Revit"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                base_key = winreg.OpenKey(hive, base_key_path)
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(base_key, i)
                        i += 1
                        # Subkeys are often like "Autodesk Revit 2025"
                        # Extract the year from the subkey name
                        year = None
                        for token in subkey_name.split():
                            if token.isdigit() and len(token) == 4:
                                year = token
                                break

                        if not year:
                            continue

                        subkey = winreg.OpenKey(base_key, subkey_name)
                        # Try common value names for install path
                        for value_name in (
                            "InstallationLocation",
                            "InstallPath",
                            "",
                        ):
                            try:
                                val, _ = winreg.QueryValueEx(
                                    subkey, value_name
                                )
                                if val and os.path.isdir(val):
                                    exe = os.path.join(val, "Revit.exe")
                                    if os.path.isfile(exe):
                                        found[year] = exe
                                    break
                            except OSError:
                                continue
                        winreg.CloseKey(subkey)
                    except OSError:
                        break
                winreg.CloseKey(base_key)
            except OSError:
                continue
    except ImportError:
        pass  # Not on Windows

    # Strategy 2: Filesystem fallback
    program_files = os.environ.get(
        "ProgramFiles", r"C:\Program Files"
    )
    for year in range(2027, 2019, -1):
        year_str = str(year)
        if year_str in found:
            continue
        exe = os.path.join(
            program_files, "Autodesk", "Revit {}".format(year_str), "Revit.exe"
        )
        if os.path.isfile(exe):
            found[year_str] = exe

    # Sort newest-first
    installations = [
        {"year": y, "path": p}
        for y, p in sorted(found.items(), key=lambda x: x[0], reverse=True)
    ]
    return installations


def _select_revit(installations, version=None):
    """Pick a Revit installation by version year, or the latest available."""
    if not installations:
        return None
    if version:
        for inst in installations:
            if inst["year"] == str(version):
                return inst
        return None
    return installations[0]


def _build_launch_command(revit_path, file_path=None, language=None):
    """Construct the subprocess argument list for launching Revit."""
    args = [revit_path]
    if language:
        args.extend(["/language", language])
    if file_path:
        args.append(file_path)
    return args


async def _wait_for_revit_ready(revit_get, ctx, timeout, poll_interval=5):
    """Poll the pyRevit Routes status endpoint until Revit responds.

    Considers Revit "ready" when the endpoint responds at all (200 or 503),
    since launching without a file means no active document but Routes is active.
    """
    import time

    start = time.time()
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        if ctx:
            await ctx.info(
                "Waiting for Revit to be ready... ({}s / {}s)".format(
                    elapsed, timeout
                )
            )
        try:
            response = await revit_get("/status/", ctx=None, timeout=5.0)
            # Any valid response (dict or error string from a real HTTP response)
            # means pyRevit Routes is active
            if isinstance(response, dict):
                return True, response
            # A string starting with "Error: 5" means HTTP 5xx — server is up
            if isinstance(response, str) and response.startswith("Error: 5"):
                return True, {"status": "active_no_document"}
        except Exception:
            pass
        await anyio.sleep(poll_interval)

    return False, None


def register_launch_tools(mcp, revit_get):
    """Register Revit launch and discovery tools with the MCP server."""

    @mcp.tool()
    async def list_revit_installations(ctx: Context) -> str:
        """Discover all Revit versions installed on this system.

        Returns a list of installed Revit versions with their executable paths.
        Use this to check what's available before calling launch_revit.
        """
        try:
            installations = _find_revit_installations()
        except Exception as e:
            return json.dumps(
                {"status": "error", "error": str(e)}, indent=2
            )

        if not installations:
            return json.dumps(
                {
                    "status": "success",
                    "installations": [],
                    "message": "No Revit installations found. "
                    "Checked Windows Registry and common install paths.",
                },
                indent=2,
            )

        return json.dumps(
            {
                "status": "success",
                "installations": installations,
                "count": len(installations),
            },
            indent=2,
        )

    @mcp.tool()
    async def launch_revit(
        ctx: Context,
        file_path: Optional[str] = None,
        version: Optional[str] = None,
        language: Optional[str] = None,
        timeout: int = 120,
    ) -> str:
        """Launch Revit on this machine, optionally opening a file.

        Finds installed Revit versions automatically. After launching, polls
        the pyRevit Routes health endpoint until Revit is ready for MCP tools.

        For workshared (central model) files, Revit will show its native
        worksharing dialog on open. Use the open_document tool after launch
        for more control over worksharing options like detach from central.

        Args:
            file_path: Path to a .rvt, .rfa, or .rte file to open. Optional.
            version: Revit version year (e.g. "2025"). Uses latest if omitted.
            language: Language code (e.g. "ENU", "FRA"). Optional.
            timeout: Seconds to wait for Revit readiness (default 120).
        """
        # Validate file path if provided
        if file_path:
            if not os.path.isfile(file_path):
                return json.dumps(
                    {
                        "status": "error",
                        "error": "File not found: {}".format(file_path),
                    },
                    indent=2,
                )
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in (".rvt", ".rfa", ".rte"):
                return json.dumps(
                    {
                        "status": "error",
                        "error": "Unsupported file type '{}'. "
                        "Expected .rvt, .rfa, or .rte".format(ext),
                    },
                    indent=2,
                )

        # Find installations
        try:
            installations = _find_revit_installations()
        except Exception as e:
            return json.dumps(
                {
                    "status": "error",
                    "error": "Failed to scan for Revit installations: {}".format(
                        str(e)
                    ),
                },
                indent=2,
            )

        if not installations:
            return json.dumps(
                {
                    "status": "error",
                    "error": "No Revit installations found on this system.",
                },
                indent=2,
            )

        # Select version
        selected = _select_revit(installations, version)
        if not selected:
            available = ", ".join(i["year"] for i in installations)
            return json.dumps(
                {
                    "status": "error",
                    "error": "Revit {} not found. Available versions: {}".format(
                        version, available
                    ),
                },
                indent=2,
            )

        # Build and launch
        cmd = _build_launch_command(
            selected["path"], file_path, language
        )

        try:
            subprocess.Popen(cmd)
        except OSError as e:
            return json.dumps(
                {
                    "status": "error",
                    "error": "Failed to launch Revit: {}".format(str(e)),
                    "attempted_path": selected["path"],
                },
                indent=2,
            )

        if ctx:
            await ctx.info(
                "Revit {} launched. Waiting for pyRevit Routes to become available...".format(
                    selected["year"]
                )
            )

        # Poll for readiness
        ready, status_response = await _wait_for_revit_ready(
            revit_get, ctx, timeout
        )

        result = {
            "status": "success",
            "revit_version": selected["year"],
            "revit_path": selected["path"],
            "file_opened": file_path,
            "revit_ready": ready,
        }

        if ready:
            result["message"] = (
                "Revit {} is running and pyRevit Routes is active.".format(
                    selected["year"]
                )
            )
            if status_response:
                result["revit_status"] = status_response
        else:
            result["message"] = (
                "Revit {} was launched but did not respond within {} seconds. "
                "Ensure pyRevit is installed and Routes Server is enabled in "
                "pyRevit Settings.".format(selected["year"], timeout)
            )

        if file_path:
            result["worksharing_note"] = (
                "If this is a workshared (central) file, Revit will show its "
                "native dialog for creating a local copy. For programmatic "
                "control over worksharing options (detach, audit), use the "
                "open_document tool after Revit is ready."
            )

        return json.dumps(result, indent=2)
