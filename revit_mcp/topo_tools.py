# -*- coding: UTF-8 -*-
"""
Toposolid Tools Module for Revit MCP
Aligns toposolid edit points to nearby detail/model lines in plan (XY only),
leaving each point's elevation (Z) untouched.
"""

from pyrevit import routes, DB
from utils import element_id_value
from System import Int64
import json
import logging
import math
import traceback

logger = logging.getLogger(__name__)


def _element_id(value):
    """Build an ElementId from a plain int, disambiguating the overload."""
    return DB.ElementId(Int64(value))

# Default snap tolerance in millimeters
DEFAULT_TOLERANCE_MM = 50.0
MM_TO_FEET = 1.0 / 304.8
FEET_TO_M = 0.3048

# Default tolerance for matching a point's elevation against target_height_m
DEFAULT_HEIGHT_TOLERANCE_M = 0.01


def _perpendicular_foot_on_segment(px, py, x1, y1, x2, y2):
    """Foot of the perpendicular from (px, py) onto the line through
    (x1,y1)-(x2,y2), in plan. Returns None if that foot falls outside the
    bound segment (i.e. the point doesn't meet the line perpendicularly
    within its extent) - it must NOT be snapped to an endpoint in that case.
    """
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return None
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    if t < 0.0 or t > 1.0:
        return None
    return x1 + t * dx, y1 + t * dy


def _line_segments_xy(doc, line_ids):
    """Resolve line-like element ids to a flat list of (x1, y1, x2, y2) plan segments."""
    segments = []
    skipped = []
    for lid in line_ids:
        elem = doc.GetElement(_element_id(lid))
        if elem is None:
            skipped.append({"id": lid, "reason": "not found"})
            continue
        curve = None
        try:
            curve = elem.GeometryCurve
        except AttributeError:
            try:
                loc = elem.Location
                curve = loc.Curve if loc else None
            except AttributeError:
                curve = None
        if curve is None:
            skipped.append({"id": lid, "reason": "no curve geometry"})
            continue
        try:
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            segments.append((p0.X, p0.Y, p1.X, p1.Y))
        except Exception as curve_err:
            skipped.append({"id": lid, "reason": str(curve_err)})
    return segments, skipped


def register_topo_tools_routes(api):
    """Register toposolid-related routes with the API"""

    @api.route("/align_toposolid_points/", methods=["POST"])
    def align_toposolid_points(doc, uidoc, request):
        """
        Snap toposolid edit points that fall within a tolerance of one or more
        lines (detail lines, model lines, etc.) onto those lines in plan (X/Y).
        Point elevation (Z) is never modified.

        Expected payload:
        {
            "toposolid_ids": [123456],       # optional; falls back to selection
            "line_ids": [111, 222],          # optional; falls back to selection
            "tolerance_mm": 50.0,            # optional; default 50mm
            "target_height_m": 15.0,         # optional; only move points at this elevation
            "height_tolerance_m": 0.01       # optional; default 1cm match window
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

            toposolid_ids = data.get("toposolid_ids")
            line_ids = data.get("line_ids")
            tolerance_mm = float(data.get("tolerance_mm", DEFAULT_TOLERANCE_MM))
            tolerance_ft = tolerance_mm * MM_TO_FEET

            target_height_m = data.get("target_height_m")
            if target_height_m is not None:
                target_height_m = float(target_height_m)
            height_tolerance_m = float(
                data.get("height_tolerance_m", DEFAULT_HEIGHT_TOLERANCE_M)
            )

            # Fall back to current UI selection for whichever side wasn't provided
            if not toposolid_ids or not line_ids:
                selected_ids = list(uidoc.Selection.GetElementIds()) if uidoc else []
                selected_elems = [doc.GetElement(eid) for eid in selected_ids]

                if not toposolid_ids:
                    toposolid_ids = [
                        element_id_value(e.Id)
                        for e in selected_elems
                        if isinstance(e, DB.Toposolid)
                    ]
                if not line_ids:
                    line_ids = [
                        element_id_value(e.Id)
                        for e in selected_elems
                        if isinstance(e, (DB.DetailLine, DB.ModelLine, DB.DetailCurve, DB.ModelCurve))
                    ]

            if not toposolid_ids:
                return routes.make_response(
                    data={"error": "No toposolid specified or selected"}, status=400
                )
            if not line_ids:
                return routes.make_response(
                    data={"error": "No lines specified or selected"}, status=400
                )

            segments, skipped_lines = _line_segments_xy(doc, line_ids)
            if not segments:
                return routes.make_response(
                    data={"error": "None of the provided lines had usable geometry",
                          "skipped_lines": skipped_lines},
                    status=400,
                )

            results = []
            t = DB.Transaction(doc, "Align toposolid points to lines via MCP")
            t.Start()
            try:
                for topo_id in toposolid_ids:
                    topo = doc.GetElement(_element_id(topo_id))
                    if not isinstance(topo, DB.Toposolid):
                        results.append({
                            "toposolid_id": topo_id,
                            "error": "Element is not a Toposolid",
                        })
                        continue

                    editor = topo.GetSlabShapeEditor()
                    if editor is None or not editor.IsEnabled:
                        results.append({
                            "toposolid_id": topo_id,
                            "error": "Toposolid has no editable shape (SlabShapeEditor unavailable)",
                        })
                        continue

                    vertices = list(editor.SlabShapeVertices)
                    to_delete = []
                    to_add = []
                    moved = []

                    for v in vertices:
                        # Skip the toposolid's boundary - only Interior points
                        # are eligible to move, never Corner/Edge vertices.
                        if v.VertexType != DB.SlabShapeVertexType.Interior:
                            continue

                        p = v.Position

                        if target_height_m is not None:
                            point_height_m = p.Z * FEET_TO_M
                            if abs(point_height_m - target_height_m) > height_tolerance_m:
                                continue

                        best_dist = tolerance_ft
                        best_xy = None
                        for (x1, y1, x2, y2) in segments:
                            foot = _perpendicular_foot_on_segment(p.X, p.Y, x1, y1, x2, y2)
                            if foot is None:
                                continue
                            cx, cy = foot
                            dist = math.hypot(p.X - cx, p.Y - cy)
                            if dist <= best_dist:
                                best_dist = dist
                                best_xy = (cx, cy)

                        if best_xy is not None:
                            new_point = DB.XYZ(best_xy[0], best_xy[1], p.Z)
                            to_delete.append(v)
                            to_add.append(new_point)
                            moved.append({
                                "from": {"x": p.X, "y": p.Y, "z": p.Z},
                                "to": {"x": new_point.X, "y": new_point.Y, "z": new_point.Z},
                                "distance_mm": best_dist / MM_TO_FEET,
                            })

                    # Move each vertex by deleting it and re-adding at the snapped
                    # X/Y with its original Z (SlabShapeEditor has no direct
                    # "move vertex in plan" call - ModifySubElement only adjusts
                    # elevation, not X/Y).
                    for v in to_delete:
                        editor.DeletePoint(v)
                    for new_point in to_add:
                        editor.AddPoint(new_point)

                    results.append({
                        "toposolid_id": topo_id,
                        "total_points": len(vertices),
                        "points_moved": len(moved),
                        "moved": moved,
                    })

                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            return routes.make_response(data={
                "status": "success",
                "tolerance_mm": tolerance_mm,
                "target_height_m": target_height_m,
                "height_tolerance_m": height_tolerance_m,
                "line_ids_used": line_ids,
                "skipped_lines": skipped_lines,
                "results": results,
            })

        except Exception as e:
            logger.error("Failed to align toposolid points: {}".format(str(e)))
            return routes.make_response(
                data={"error": str(e), "traceback": traceback.format_exc()},
                status=500,
            )

    @api.route("/create_toposolid_subdivision/", methods=["POST"])
    def create_toposolid_subdivision(doc, uidoc, request):
        """
        Create a Toposolid subdivision using one or more Filled Regions as the
        subdivision boundary/boundaries.

        Expected payload:
        {
            "toposolid_id": 123456,            # optional; falls back to selection
            "filled_region_ids": [111, 222],   # optional; falls back to selection
            "type_name": "Some Toposolid Type" # optional; defaults to host's type
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

            toposolid_id = data.get("toposolid_id")
            filled_region_ids = data.get("filled_region_ids")
            type_name = data.get("type_name")

            # Fall back to current UI selection for whichever side wasn't provided
            if not toposolid_id or not filled_region_ids:
                selected_ids = list(uidoc.Selection.GetElementIds()) if uidoc else []
                selected_elems = [doc.GetElement(eid) for eid in selected_ids]

                if not toposolid_id:
                    topo_candidates = [
                        e for e in selected_elems if isinstance(e, DB.Toposolid)
                    ]
                    if topo_candidates:
                        toposolid_id = element_id_value(topo_candidates[0].Id)

                if not filled_region_ids:
                    filled_region_ids = [
                        element_id_value(e.Id)
                        for e in selected_elems
                        if isinstance(e, DB.FilledRegion)
                    ]

            if not toposolid_id:
                return routes.make_response(
                    data={"error": "No host toposolid specified or selected"},
                    status=400,
                )
            if not filled_region_ids:
                return routes.make_response(
                    data={"error": "No filled regions specified or selected"},
                    status=400,
                )

            host = doc.GetElement(_element_id(toposolid_id))
            if not isinstance(host, DB.Toposolid):
                return routes.make_response(
                    data={"error": "toposolid_id does not refer to a Toposolid"},
                    status=400,
                )

            target_type_id = None
            if type_name:
                topo_types = DB.FilteredElementCollector(doc).OfClass(
                    DB.ToposolidType
                )
                for tt in topo_types:
                    name_param = tt.get_Parameter(
                        DB.BuiltInParameter.ALL_MODEL_TYPE_NAME
                    )
                    if name_param and name_param.AsString() == type_name:
                        target_type_id = tt.Id
                        break
                if target_type_id is None:
                    return routes.make_response(
                        data={"error": "Toposolid type not found: {}".format(type_name)},
                        status=404,
                    )

            results = []
            t = DB.Transaction(doc, "Create toposolid subdivision via MCP")
            t.Start()
            try:
                for fr_id in filled_region_ids:
                    fr = doc.GetElement(_element_id(fr_id))
                    if not isinstance(fr, DB.FilledRegion):
                        results.append({
                            "filled_region_id": fr_id,
                            "error": "Element is not a FilledRegion",
                        })
                        continue

                    loops = list(fr.GetBoundaries())
                    if not loops:
                        results.append({
                            "filled_region_id": fr_id,
                            "error": "Filled region has no boundary loops",
                        })
                        continue

                    from System.Collections.Generic import List

                    profiles = List[DB.CurveLoop](loops)

                    try:
                        if target_type_id is not None:
                            subdivision = host.CreateSubDivision(
                                doc, target_type_id, profiles
                            )
                        else:
                            subdivision = host.CreateSubDivision(doc, profiles)
                    except Exception as create_err:
                        results.append({
                            "filled_region_id": fr_id,
                            "error": str(create_err),
                        })
                        continue

                    results.append({
                        "filled_region_id": fr_id,
                        "subdivision_id": element_id_value(subdivision.Id),
                        "status": "success",
                    })

                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            return routes.make_response(data={
                "status": "success",
                "toposolid_id": toposolid_id,
                "type_name": type_name,
                "results": results,
            })

        except Exception as e:
            logger.error("Failed to create toposolid subdivision: {}".format(str(e)))
            return routes.make_response(
                data={"error": str(e), "traceback": traceback.format_exc()},
                status=500,
            )

    logger.info("Toposolid tools routes registered successfully")
