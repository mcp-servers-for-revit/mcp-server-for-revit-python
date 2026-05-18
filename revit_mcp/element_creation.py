# -*- coding: UTF-8 -*-
"""
Element Creation Module for Revit MCP
Line-based (Wall) and surface-based (Floor / Ceiling) creation endpoints.

Units: input coordinates and dimensions are expected in MILLIMETRES.
Internally converted to Revit's decimal-feet via UnitUtils.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value
import json
import traceback
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _mm_to_feet(value_mm):
    """Convert a length in millimetres to Revit's internal feet."""
    try:
        # Revit 2021+
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.UnitTypeId.Millimeters)
    except AttributeError:
        # Revit <2021 fallback
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.DisplayUnitType.DUT_MILLIMETERS)


def _xyz_mm(point_dict):
    """Build a DB.XYZ in internal feet from a {x,y,z} dict expressed in mm."""
    return DB.XYZ(_mm_to_feet(point_dict["x"]), _mm_to_feet(point_dict["y"]), _mm_to_feet(point_dict["z"]))


def _find_level_by_name(doc, level_name):
    """Locate a Level by exact name. Returns None if not found."""
    levels = DB.FilteredElementCollector(doc).OfClass(DB.Level)
    for lvl in levels:
        if lvl.Name == level_name:
            return lvl
    return None


def _find_type_by_name(doc, type_class, type_name):
    """Locate an ElementType (WallType / FloorType / CeilingType) by exact name."""
    collector = DB.FilteredElementCollector(doc).OfClass(type_class)
    for t in collector:
        if t.Name == type_name:
            return t
    return None


def _parse_json_request(request):
    """Pull the JSON payload from a pyRevit route request. Returns dict or raises."""
    if not request or not request.data:
        raise ValueError("No data provided")
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _set_instance_parameters(element, properties):
    """Apply a dict of {param_name: value} to an element, best-effort."""
    if not properties:
        return []
    written = []
    for name, value in properties.items():
        try:
            p = element.LookupParameter(name)
            if p is None or p.IsReadOnly:
                continue
            st = p.StorageType
            if st == DB.StorageType.String:
                p.Set(unicode(value) if isinstance(value, str) else value)
            elif st == DB.StorageType.Integer:
                p.Set(int(value))
            elif st == DB.StorageType.Double:
                # Caller is responsible for unit conversion; accept raw float
                p.Set(float(value))
            elif st == DB.StorageType.ElementId:
                p.Set(DB.ElementId(int(value)))
            written.append(name)
        except Exception as pe:
            logger.warning("Could not set parameter {}: {}".format(name, str(pe)))
    return written


# ─────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────

def register_element_creation_routes(api):
    """Register element-creation routes (line + surface based)."""

    @api.route("/create_line_based_element/", methods=["POST"])
    def create_line_based_element(doc, request):
        """
        Create a line-based element. Currently supports walls only.

        Expected payload:
        {
            "category": "wall",
            "type_name": "Generic - 200mm",
            "level_name": "Level 1",
            "start": {"x": 0.0, "y": 0.0, "z": 0.0},   // mm, relative to project origin
            "end":   {"x": 5000.0, "y": 0.0, "z": 0.0},
            "height_mm": 3000.0,
            "structural": false,                         // optional
            "properties": { "Comments": "via MCP" }      // optional, instance params
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            category = (data.get("category") or "wall").lower()
            if category != "wall":
                return routes.make_response(data={
                    "error": "Only category='wall' is implemented. Beams/pipes require Structural/MEP family discipline and are not yet wired."
                }, status=400)

            type_name = data.get("type_name")
            level_name = data.get("level_name")
            start = data.get("start")
            end = data.get("end")
            height_mm = data.get("height_mm")
            structural = bool(data.get("structural", False))
            properties = data.get("properties", {})

            for field, val in (("type_name", type_name), ("level_name", level_name),
                               ("start", start), ("end", end), ("height_mm", height_mm)):
                if val in (None, ""):
                    return routes.make_response(data={"error": "Missing required field: {}".format(field)}, status=400)

            wall_type = _find_type_by_name(doc, DB.WallType, type_name)
            if wall_type is None:
                return routes.make_response(data={"error": "WallType not found: {}".format(type_name)}, status=404)

            level = _find_level_by_name(doc, level_name)
            if level is None:
                return routes.make_response(data={"error": "Level not found: {}".format(level_name)}, status=404)

            start_xyz = _xyz_mm(start)
            end_xyz = _xyz_mm(end)
            if start_xyz.DistanceTo(end_xyz) < 1e-6:
                return routes.make_response(data={"error": "Wall start and end coincide"}, status=400)

            with DB.Transaction(doc, "MCP: Create Wall") as t:
                t.Start()
                line = DB.Line.CreateBound(start_xyz, end_xyz)
                wall = DB.Wall.Create(
                    doc, line, wall_type.Id, level.Id,
                    _mm_to_feet(height_mm), 0.0, False, structural
                )
                written_params = _set_instance_parameters(wall, properties)
                t.Commit()

            return routes.make_response(data={
                "status": "success",
                "element_id": element_id_value(wall.Id),
                "wall_type": type_name,
                "level": level_name,
                "length_mm": float(start_xyz.DistanceTo(end_xyz)) * 304.8,
                "height_mm": float(height_mm),
                "structural": structural,
                "parameters_written": written_params,
            })

        except Exception as e:
            logger.error("create_line_based_element failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/create_surface_based_element/", methods=["POST"])
    def create_surface_based_element(doc, request):
        """
        Create a surface-based element (floor or ceiling) from a closed boundary.

        Expected payload:
        {
            "category": "floor",                          // or "ceiling"
            "type_name": "Generic 150mm",
            "level_name": "Level 1",
            "boundary": [                                  // mm, closed loop (first == last not required; will be closed automatically)
                {"x": 0,    "y": 0,    "z": 0},
                {"x": 5000, "y": 0,    "z": 0},
                {"x": 5000, "y": 5000, "z": 0},
                {"x": 0,    "y": 5000, "z": 0}
            ],
            "properties": { "Comments": "via MCP" }       // optional
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            category = (data.get("category") or "").lower()
            if category not in ("floor", "ceiling"):
                return routes.make_response(data={"error": "category must be 'floor' or 'ceiling'"}, status=400)

            type_name = data.get("type_name")
            level_name = data.get("level_name")
            boundary = data.get("boundary")
            properties = data.get("properties", {})

            for field, val in (("type_name", type_name), ("level_name", level_name), ("boundary", boundary)):
                if val in (None, ""):
                    return routes.make_response(data={"error": "Missing required field: {}".format(field)}, status=400)
            if not isinstance(boundary, list) or len(boundary) < 3:
                return routes.make_response(data={"error": "boundary must be a list of >= 3 points"}, status=400)

            level = _find_level_by_name(doc, level_name)
            if level is None:
                return routes.make_response(data={"error": "Level not found: {}".format(level_name)}, status=404)

            # Build CurveLoop from boundary, closing it explicitly if not already closed
            pts = [_xyz_mm(p) for p in boundary]
            if pts[0].DistanceTo(pts[-1]) > 1e-6:
                pts.append(pts[0])
            loop = DB.CurveLoop()
            for i in range(len(pts) - 1):
                if pts[i].DistanceTo(pts[i + 1]) < 1e-6:
                    continue
                loop.Append(DB.Line.CreateBound(pts[i], pts[i + 1]))

            if category == "floor":
                floor_type = _find_type_by_name(doc, DB.FloorType, type_name)
                if floor_type is None:
                    return routes.make_response(data={"error": "FloorType not found: {}".format(type_name)}, status=404)
                with DB.Transaction(doc, "MCP: Create Floor") as t:
                    t.Start()
                    # Revit 2022+ requires a List[CurveLoop]; build via .NET generics
                    from System.Collections.Generic import List as NetList
                    loop_list = NetList[DB.CurveLoop]()
                    loop_list.Add(loop)
                    floor = DB.Floor.Create(doc, loop_list, floor_type.Id, level.Id)
                    written_params = _set_instance_parameters(floor, properties)
                    t.Commit()
                el_id = floor.Id
            else:  # ceiling
                ceiling_type = _find_type_by_name(doc, DB.CeilingType, type_name)
                if ceiling_type is None:
                    return routes.make_response(data={"error": "CeilingType not found: {}".format(type_name)}, status=404)
                with DB.Transaction(doc, "MCP: Create Ceiling") as t:
                    t.Start()
                    from System.Collections.Generic import List as NetList
                    loop_list = NetList[DB.CurveLoop]()
                    loop_list.Add(loop)
                    ceiling = DB.Ceiling.Create(doc, loop_list, ceiling_type.Id, level.Id)
                    written_params = _set_instance_parameters(ceiling, properties)
                    t.Commit()
                el_id = ceiling.Id

            return routes.make_response(data={
                "status": "success",
                "element_id": element_id_value(el_id),
                "category": category,
                "type": type_name,
                "level": level_name,
                "vertex_count": len(boundary),
                "parameters_written": written_params,
            })

        except Exception as e:
            logger.error("create_surface_based_element failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Element-creation routes registered successfully")
