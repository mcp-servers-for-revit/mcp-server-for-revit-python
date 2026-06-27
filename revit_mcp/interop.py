# -*- coding: UTF-8 -*-
"""
Interop Module for Revit MCP
Handles IFC export and external file linking/importing
"""

from utils import get_element_name, get_element_id_value
from pyrevit import routes, revit, DB
import json
import os
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def register_interop_routes(api):
    """Register all interop routes with the API"""

    @api.route("/export_ifc/", methods=["POST"])
    def export_ifc_handler(doc, request):
        """Export the model to IFC format."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            file_path = data.get("file_path")
            if not file_path:
                return routes.make_response(
                    data={"error": "file_path is required (must end in .ifc)"},
                    status=400,
                )

            if not file_path.lower().endswith(".ifc"):
                return routes.make_response(
                    data={"error": "file_path must end in .ifc"},
                    status=400,
                )

            ifc_version = data.get("ifc_version", "IFC2x3")
            export_base_quantities = data.get("export_base_quantities", True)
            view_name = data.get("view_name")

            # Ensure output directory exists
            output_dir = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)

            if output_dir and not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir)
                except Exception as dir_err:
                    return routes.make_response(
                        data={"error": "Cannot create output directory: {}".format(str(dir_err))},
                        status=500,
                    )

            # Set up IFC export options
            ifc_options = DB.IFCExportOptions()

            # Set IFC version
            if ifc_version == "IFC4":
                ifc_options.FileVersion = DB.IFCVersion.IFC4
            else:
                ifc_options.FileVersion = DB.IFCVersion.IFC2x3

            ifc_options.ExportBaseQuantities = export_base_quantities

            # Filter by view if specified
            if view_name:
                views = (
                    DB.FilteredElementCollector(doc)
                    .OfClass(DB.View)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
                target_view = None
                for v in views:
                    if get_element_name(v) == view_name and not v.IsTemplate:
                        target_view = v
                        break
                if target_view:
                    ifc_options.FilterViewId = target_view.Id

            t = DB.Transaction(doc, "Export IFC via MCP")
            t.Start()

            try:
                doc.Export(output_dir or ".", file_name, ifc_options)
                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            # Get file size
            file_size_kb = 0
            try:
                if os.path.exists(file_path):
                    file_size_kb = int(os.path.getsize(file_path) / 1024)
            except Exception:
                pass

            return routes.make_response(
                data={
                    "status": "success",
                    "file_path": file_path,
                    "file_size_kb": file_size_kb,
                    "ifc_version": ifc_version,
                    "message": "Exported IFC to '{}' ({} KB)".format(file_path, file_size_kb),
                }
            )

        except Exception as e:
            logger.error("Failed to export IFC: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    @api.route("/link_file/", methods=["POST"])
    def link_file_handler(doc, request):
        """Link or import an external file."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            file_path = data.get("file_path")
            if not file_path:
                return routes.make_response(
                    data={"error": "file_path is required"},
                    status=400,
                )

            if not os.path.exists(file_path):
                return routes.make_response(
                    data={"error": "File not found at the specified path."},
                    status=500,
                )

            mode = data.get("mode", "link")
            position = data.get("position")

            file_ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)
            file_type = file_ext.lstrip(".").upper()

            t = DB.Transaction(doc, "Link/Import File via MCP")
            t.Start()

            try:
                result_id = None

                if file_ext == ".rvt":
                    # Revit link
                    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(file_path)
                    link_options = DB.RevitLinkOptions(False)  # not relative
                    link_result = DB.RevitLinkType.Create(doc, model_path, link_options)
                    if link_result and link_result.ElementId:
                        result_id = get_element_id_value(link_result.ElementId)
                        # Place instance
                        link_instance = DB.RevitLinkInstance.Create(doc, link_result.ElementId)
                        if link_instance:
                            result_id = get_element_id_value(link_instance)

                elif file_ext in (".dwg", ".dxf", ".dgn"):
                    # CAD file
                    options = DB.DWGImportOptions()
                    options.Placement = DB.ImportPlacement.Origin

                    if position:
                        # Will be applied after import via move
                        pass

                    active_view = doc.ActiveView
                    linked_id = None  # clr.Reference[DB.ElementId]()

                    if mode == "import":
                        success = doc.Import(file_path, options, active_view, linked_id)
                    else:
                        success = doc.Link(file_path, options, active_view, linked_id)

                    if linked_id:
                        result_id = get_element_id_value(linked_id)

                else:
                    t.RollBack()
                    return routes.make_response(
                        data={"error": "Unsupported file type '{}'. Supported: DWG, DXF, DGN, RVT".format(file_ext)},
                        status=400,
                    )

                t.Commit()

                return routes.make_response(
                    data={
                        "status": "success",
                        "element_id": result_id,
                        "file_name": file_name,
                        "file_type": file_type,
                        "mode": mode,
                        "message": "{}ed file '{}'".format(
                            mode.capitalize(), file_name
                        ),
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to link file: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Interop routes registered successfully")
