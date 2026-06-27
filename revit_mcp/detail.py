# -*- coding: UTF-8 -*-
"""
Detail Module for Revit MCP
Handles detail line creation for view-specific annotation
"""

from utils import get_element_name, get_element_id_value
from pyrevit import routes, revit, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def register_detail_routes(api):
    """Register all detail routes with the API"""

    @api.route("/create_detail_line/", methods=["POST"])
    def create_detail_line_handler(doc, request):
        """Create a detail line in a view."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            start_point = data.get("start_point")
            end_point = data.get("end_point")
            if not start_point or not end_point:
                return routes.make_response(
                    data={"error": "start_point and end_point are required"},
                    status=400,
                )

            # Find the view
            view_name = data.get("view_name")
            target_view = None
            if view_name:
                views = (
                    DB.FilteredElementCollector(doc)
                    .OfClass(DB.View)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
                for v in views:
                    if get_element_name(v) == view_name and not v.IsTemplate:
                        target_view = v
                        break
                if not target_view:
                    return routes.make_response(
                        data={"error": "View '{}' not found".format(view_name)},
                        status=404,
                    )
            else:
                target_view = doc.ActiveView

            # Check view type compatibility
            try:
                vt = target_view.ViewType
                allowed_types = [
                    DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan,
                    DB.ViewType.Section, DB.ViewType.Detail,
                    DB.ViewType.Elevation, DB.ViewType.DraftingView,
                    DB.ViewType.AreaPlan,
                ]
                if vt not in allowed_types:
                    return routes.make_response(
                        data={"error": "Cannot create detail line — the specified view is not a plan or detail view."},
                        status=500,
                    )
            except Exception:
                pass

            start = DB.XYZ(
                float(start_point.get("x", 0)) * MM_TO_FEET,
                float(start_point.get("y", 0)) * MM_TO_FEET,
                float(start_point.get("z", 0)) * MM_TO_FEET,
            )
            end = DB.XYZ(
                float(end_point.get("x", 0)) * MM_TO_FEET,
                float(end_point.get("y", 0)) * MM_TO_FEET,
                float(end_point.get("z", 0)) * MM_TO_FEET,
            )

            line = DB.Line.CreateBound(start, end)

            t = DB.Transaction(doc, "Create Detail Line via MCP")
            t.Start()

            try:
                detail_curve = doc.Create.NewDetailCurve(target_view, line)

                if not detail_curve:
                    t.RollBack()
                    return routes.make_response(
                        data={"error": "Failed to create detail line"},
                        status=500,
                    )

                # Set line style if specified
                line_style = data.get("line_style")
                if line_style:
                    try:
                        # Find the line style
                        line_styles = detail_curve.GetLineStyleIds()
                        cat = doc.Settings.Categories
                        line_cat = None
                        try:
                            line_cat = cat.get_Item(DB.BuiltInCategory.OST_Lines)
                        except Exception:
                            pass

                        if line_cat:
                            for sub_cat in line_cat.SubCategories:
                                if get_element_name(sub_cat) == line_style:
                                    # Get the GraphicsStyle for this category
                                    gs_id = sub_cat.GetGraphicsStyle(DB.GraphicsStyleType.Projection).Id
                                    detail_curve.LineStyle = doc.GetElement(gs_id)
                                    break
                    except Exception as style_err:
                        logger.debug("Could not set line style: {}".format(str(style_err)))

                t.Commit()

                actual_view_name = get_element_name(target_view)

                return routes.make_response(
                    data={
                        "status": "success",
                        "line_id": get_element_id_value(detail_curve),
                        "view_name": actual_view_name,
                        "message": "Created detail line in view '{}'".format(actual_view_name),
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to create detail line: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Detail routes registered successfully")
