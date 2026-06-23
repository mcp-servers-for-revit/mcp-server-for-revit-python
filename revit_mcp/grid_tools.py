# -*- coding: UTF-8 -*-
"""
Grid Tools Module for Revit MCP
Trims grid lines' view-specific extents to a view's crop region boundary.
"""

from pyrevit import routes, DB
from utils import element_id_value
from System import Int64
import json
import logging
import traceback

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def _element_id(value):
    """Build an ElementId from a plain int, disambiguating the overload."""
    return DB.ElementId(Int64(value))


def _crop_polygon_xy(view):
    """Flatten the view's crop region shape into a list of (x, y) polygons
    (one list of points per loop), plus the constant Z of the crop curves.
    """
    mgr = view.GetCropRegionShapeManager()
    loops = list(mgr.GetCropShape())
    polygons = []
    z = None
    for loop in loops:
        points = []
        for curve in loop:
            p0 = curve.GetEndPoint(0)
            if z is None:
                z = p0.Z
            points.append((p0.X, p0.Y))
        polygons.append(points)
    return polygons, z


def _clip_line_to_polygon(ax, ay, dx, dy, polygon):
    """Intersect the infinite line through (ax, ay) with direction (dx, dy)
    against the polygon's edges (list of (x, y) vertices, closed loop).
    Returns a sorted list of parametric t values (along the direction,
    unnormalized) where the line crosses an edge.
    """
    t_values = []
    n = len(polygon)
    for i in range(n):
        ex0, ey0 = polygon[i]
        ex1, ey1 = polygon[(i + 1) % n]
        edx = ex1 - ex0
        edy = ey1 - ey0

        # Solve A + t*D = E0 + s*Ed, i.e. t*D - s*Ed = E0 - A
        denom = dx * edy - dy * edx
        if abs(denom) < 1e-12:
            continue  # parallel

        rx = ex0 - ax
        ry = ey0 - ay
        s = (dy * rx - dx * ry) / denom
        if s < -1e-9 or s > 1 + 1e-9:
            continue  # intersection outside this edge segment

        t = (edy * rx - edx * ry) / denom
        t_values.append(t)

    return sorted(t_values)


def register_grid_tools_routes(api):
    """Register grid-related routes with the API"""

    @api.route("/crop_grids_to_view/", methods=["POST"])
    def crop_grids_to_view(doc, uidoc, request):
        """
        Trim grid lines' view-specific extents so they stop exactly at the
        active (or specified) view's crop region boundary.

        Expected payload:
        {
            "view_id": 123456,       # optional; defaults to the active view
            "grid_ids": [111, 222],  # optional; defaults to current selection,
                                      # then to all grids visible in the view
            "margin_mm": 0.0         # optional; positive extends past the crop
                                      # edge, negative insets from it
        }
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = {}
            if request and request.data:
                data = (
                    json.loads(request.data)
                    if isinstance(request.data, str)
                    else request.data
                )

            view_id = data.get("view_id")
            grid_ids = data.get("grid_ids")
            margin_mm = float(data.get("margin_mm", 0.0))
            margin_ft = margin_mm * MM_TO_FEET

            view = doc.GetElement(_element_id(view_id)) if view_id else doc.ActiveView
            if not isinstance(view, DB.View):
                return routes.make_response(
                    data={"error": "view_id does not refer to a View"}, status=400
                )

            if not view.CropBoxActive:
                return routes.make_response(
                    data={"error": "View has no active crop box/region"}, status=400
                )

            if not grid_ids and uidoc:
                selected_ids = list(uidoc.Selection.GetElementIds())
                grid_ids = [
                    element_id_value(eid)
                    for eid in selected_ids
                    if isinstance(doc.GetElement(eid), DB.Grid)
                ]

            if not grid_ids:
                grids = list(
                    DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Grid)
                )
                grid_ids = [element_id_value(g.Id) for g in grids]

            if not grid_ids:
                return routes.make_response(
                    data={"error": "No grids specified, selected, or visible in the view"},
                    status=400,
                )

            polygons, crop_z = _crop_polygon_xy(view)
            if not polygons:
                return routes.make_response(
                    data={"error": "Could not resolve a crop region shape for the view"},
                    status=400,
                )

            results = []
            t = DB.Transaction(doc, "Crop grids to view via MCP")
            t.Start()
            try:
                for gid in grid_ids:
                    grid = doc.GetElement(_element_id(gid))
                    if not isinstance(grid, DB.Grid):
                        results.append({"grid_id": gid, "error": "Element is not a Grid"})
                        continue

                    curve = grid.Curve
                    p0 = curve.GetEndPoint(0)
                    p1 = curve.GetEndPoint(1)
                    dx = p1.X - p0.X
                    dy = p1.Y - p0.Y

                    all_t = []
                    for polygon in polygons:
                        all_t.extend(
                            _clip_line_to_polygon(p0.X, p0.Y, dx, dy, polygon)
                        )

                    if len(all_t) < 2:
                        results.append({
                            "grid_id": gid,
                            "error": "Grid does not cross the view's crop region",
                        })
                        continue

                    t_min = min(all_t) - margin_ft
                    t_max = max(all_t) + margin_ft

                    # Use the Z Revit already expects for this view's
                    # in-view curves (not the crop shape's Z) - otherwise
                    # SetCurveInView rejects the curve as not coincident
                    # with the grid's datum plane.
                    existing_inview = list(
                        grid.GetCurvesInView(DB.DatumExtentType.ViewSpecific, view)
                    )
                    inview_z = (
                        existing_inview[0].GetEndPoint(0).Z
                        if existing_inview
                        else p0.Z
                    )

                    new_p0 = DB.XYZ(p0.X + t_min * dx, p0.Y + t_min * dy, inview_z)
                    new_p1 = DB.XYZ(p0.X + t_max * dx, p0.Y + t_max * dy, inview_z)

                    try:
                        new_curve = DB.Line.CreateBound(new_p0, new_p1)
                        grid.SetCurveInView(
                            DB.DatumExtentType.ViewSpecific, view, new_curve
                        )
                    except Exception as set_err:
                        results.append({"grid_id": gid, "error": str(set_err)})
                        continue

                    results.append({
                        "grid_id": gid,
                        "status": "success",
                        "new_extent": {
                            "from": {"x": new_p0.X, "y": new_p0.Y},
                            "to": {"x": new_p1.X, "y": new_p1.Y},
                        },
                    })

                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            return routes.make_response(data={
                "status": "success",
                "view_id": element_id_value(view.Id),
                "margin_mm": margin_mm,
                "results": results,
            })

        except Exception as e:
            logger.error("Failed to crop grids to view: {}".format(str(e)))
            return routes.make_response(
                data={"error": str(e), "traceback": traceback.format_exc()},
                status=500,
            )

    logger.info("Grid tools routes registered successfully")
