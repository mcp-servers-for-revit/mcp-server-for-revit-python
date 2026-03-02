# -*- coding: UTF-8 -*-
"""
Commands Module for Revit MCP
Handles Command execution
"""
from pyrevit import HOST_APP
from pyrevit.coreutils.logger import get_logger
from pyrevit.loader import sessioninfo

import glob
import os

from pyrevit import routes
from pyrevit.routes.server import serverinfo

from pyrevit import routes
import logging

logger = logging.getLogger(__name__)


# Helper methods
def _find_active_log():
    # type: ()->(str, int)
    """Return (log_path, current_byte_offset) for the most recent PyRevit log.
    returned offset can be used to capture a range of text inside said log.

    Not 100% reliable when multiple revits are in play, and file logging must be enabled for this to work.
    """
    appdata = os.environ.get('APPDATA', '')
    if not appdata:
        return None, 0
    pattern = os.path.join(appdata, 'pyRevit', '*', 'pyRevit_*_runtime.log')
    matches = glob.glob(pattern)
    if not matches:
        return None, 0
    log_path = max(matches, key=os.path.getmtime)
    try:
        offset = os.path.getsize(log_path)
    except OSError:
        offset = 0
    return log_path, offset


def _read_log_since(log_path, offset):
    # type: (str, int)->(str|None)
    """reads log data from a file FROM an offset position"""
    try:
        with open(log_path, 'rb') as f:
            f.seek(offset)
            raw = f.read()
        return raw.decode('utf-8', errors='replace')
    except Exception:
        return None

def register_commands_routes(api):
    """Register all commands-related routes with the API"""

    @api.route('/commands_list', methods=['GET'])
    def get_commands(uiapp):
        """List all loaded pyRevit commands with their control IDs."""
        from pyrevit.loader import sessionmgr
        commands = sessionmgr.find_all_commands(cache=True)
        return [
            {
                "name": cmd.name,
                "control_id": cmd.control_id,
                "bundle": cmd.bundle,
                "extension": cmd.extension,
                "unique_id": cmd.unique_id,
            }
            for cmd in commands
        ]

    @api.route('/commands_run', methods=['POST'])
    def run_command(request, uiapp):
        """Run a pyRevit command by control ID.

        Has 2 modes depending on provided body:
        wait = true  => runs execution immediatly and returns script logs (where possible)
        Wait = false => posts command to revit ui thread, to be executed after this api call.

        Request body (JSON): {
            "control_id": "CustomCtrl_%CustomCtrl_%...",
            "wait": true  (optional, default true — set false for fire-and-forget)
        }
        """
        from pyrevit.loader import sessionmgr
        from datetime import datetime
        data = request.data or {}
        control_id = data.get('control_id', None)
        wait = data.get('wait', True)

        if not control_id:
            return {"error": "control_id is required in request body"}

        # fire-and-forget via PostCommand
        if not wait:
            command_id = UI.RevitCommandId.LookupCommandId(control_id)
            if command_id is None:
                return {"error": "Command not found: {}".format(control_id)}
            uiapp.PostCommand(command_id)
            return {"result": "posted", "control_id": control_id}


        cmd = next((c for c in sessionmgr.find_all_commands()
                    if c.control_id == control_id), None)
        if cmd is None:
            return {"error": "Command not found: {}".format(control_id)}


        # PyRevit reload destroys the IronPython engine mid-execution, so the HTTP
        # response can never be sent. Waiting on it will always time out so we don't allow this
        # command to be run with 'wait'
        if cmd.unique_id == 'pyrevitcore_pyrevit_pyrevit_tools_reload':
            return {
                "error": "Cannot await PyRevit reload: the reload script destroys "
                        "the runtime engine before a response can be sent. "
                        "Use wait=false to fire-and-forget instead."
            }
        
        # Snapshot log position before any execution so we can return only the
        # lines produced by this command.
        log_path, log_offset = _find_active_log()

        now = datetime.now()
        envvars.set_pyrevit_env_var('PYREVIT_HEADLESS', '1')
        result = None
        except_info = None
        try:
            mlogger.debug('[HEADLESS:START] command=%s controlId=%s', cmd.unique_id, control_id)
            result = sessionmgr.execute_command_cls(cmd.extcmd_type)

        except Exception as e:
            except_info = except_info
            logger.exception(e)
        finally:
            mlogger.debug('[HEADLESS:END] command=%s controlId=%s result=%s', cmd.unique_id, control_id, result)
            envvars.set_pyrevit_env_var('PYREVIT_HEADLESS', '')

        # Read log lines produced between [HEADLESS:START] and [HEADLESS:END].
        execution_log = _read_log_since(log_path, log_offset) if log_path else None

        response = {
            "result": str(result) if result else  'error',
            "execution_time": str(datetime.now() - now),
            "command": {
                "name": cmd.name,
                "control_id": cmd.control_id,
                "bundle": cmd.bundle,
                "extension": cmd.extension,
                "unique_id": cmd.unique_id,
            },
        }
        if except_info is not None:
            response["error"] = except_info

        if execution_log is not None:
            response["output"] = execution_log
        else:
            response["output"] = "Logs are disabled or failed to collect. You can turn them on in the PyRevit Settings"

        return response
