# -*- coding: UTF-8 -*-
"""
Grids Module for Revit MCP
Generates a project grid from existing wall centerlines:
- Vertical grids (constant X) numbered 1, 2, 3, ... from LEFT (smallest X)
- Horizontal grids (constant Y) lettered A, B, C, ... AA, AB, ... from SOUTH (smallest Y)

Only axis-aligned walls contribute. Diagonal and curved walls are reported
but skipped. Walls whose centerlines are within `tolerance_mm` (default
10 mm) of each other on the same axis collapse into a single grid line.

All grids are created inside one transaction so the entire grid system
can be undone with a single Ctrl+Z.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value
import json
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


def _mm_to_feet(value_mm):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.UnitTypeId.Millimeters)
    except AttributeError:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.DisplayUnitType.DUT_MILLIMETERS)


def _ft_to_mm(value_ft):
    return value_ft * 304.8


def _alpha_name(i, start_letter="A"):
    """Excel-style spreadsheet column naming. 0 -> 'A', 25 -> 'Z', 26 -> 'AA', ..."""
    if start_letter and len(start_letter) == 1 and "A" <= start_letter.upper() <= "Z":
        offset = ord(start_letter.upper()) - ord("A")
    else:
        offset = 0
    s = ""
    n = i + 1 + offset
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def _dedupe(values, tol):
    """Merge near-duplicate floats within `tol` into a single averaged value."""
    out = []
    for v in sorted(values):
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
        else:
            out[-1] = (out[-1] + v) / 2.0
    return out


def register_grids_routes(api):
    """Register grid-generation routes."""

    @api.route("/create_grids_from_walls/", methods=["POST"])
    def create_grids_from_walls(doc, request):
        """
        Create a grid system from axis-aligned wall centerlines.

        Expected payload (all optional):
        {
            "tolerance_mm":         10.0,   # collapse near-duplicate centerlines within this distance
            "padding_mm":           3000.0, # extend grid lines beyond the building bbox by this much
            "start_vertical_number": 1,     # first number for vertical grids ("1", "2", ...)
            "start_horizontal_letter":"A",  # first letter for horizontal grids ("A", "B", ...)
            "vertical_prefix":     "",      # prefix prepended to each vertical grid name (e.g. "V-")
            "horizontal_prefix":   "",      # prefix prepended to each horizontal grid name (e.g. "H-")
            "dry_run":             false    # if true, report what would be created without modifying the model
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            tolerance_mm = float(data.get("tolerance_mm", 10.0))
            padding_mm = float(data.get("padding_mm", 3000.0))
            start_v_num = int(data.get("start_vertical_number", 1))
            start_h_letter = str(data.get("start_horizontal_letter", "A") or "A")
            v_prefix = str(data.get("vertical_prefix", "") or "")
            h_prefix = str(data.get("horizontal_prefix", "") or "")
            dry_run = bool(data.get("dry_run", False))

            tol_ft = _mm_to_feet(tolerance_mm)
            pad_ft = _mm_to_feet(padding_mm)

            walls = list(DB.FilteredElementCollector(doc)
                         .OfCategory(DB.BuiltInCategory.OST_Walls)
                         .WhereElementIsNotElementType())
            if not walls:
                return routes.make_response(data={
                    "status": "no_walls",
                    "error": "No walls in the active document.",
                    "document_title": normalize_string(doc.Title),
                })

            vertical_xs = []
            horizontal_ys = []
            all_xs, all_ys = [], []
            skipped_diagonal = 0
            skipped_curved = 0
            skipped_non_curve = 0

            for w in walls:
                loc = w.Location
                if not isinstance(loc, DB.LocationCurve):
                    skipped_non_curve += 1
                    continue
                curve = loc.Curve
                if not isinstance(curve, DB.Line):
                    skipped_curved += 1
                    continue
                p0 = curve.GetEndPoint(0)
                p1 = curve.GetEndPoint(1)
                dx = p1.X - p0.X
                dy = p1.Y - p0.Y
                if abs(dx) < tol_ft and abs(dy) > tol_ft:
                    vertical_xs.append((p0.X + p1.X) / 2.0)
                elif abs(dy) < tol_ft and abs(dx) > tol_ft:
                    horizontal_ys.append((p0.Y + p1.Y) / 2.0)
                else:
                    skipped_diagonal += 1
                    continue
                all_xs.append(p0.X); all_xs.append(p1.X)
                all_ys.append(p0.Y); all_ys.append(p1.Y)

            if not vertical_xs and not horizontal_ys:
                return routes.make_response(data={
                    "status": "no_axis_aligned_walls",
                    "error": "Walls found but none are axis-aligned. Cannot generate axis-aligned grids.",
                    "walls_scanned": len(walls),
                    "skipped_diagonal": skipped_diagonal,
                    "skipped_curved": skipped_curved,
                })

            xs = _dedupe(vertical_xs, tol_ft)
            ys = _dedupe(horizontal_ys, tol_ft)

            min_x = min(all_xs) - pad_ft
            max_x = max(all_xs) + pad_ft
            min_y = min(all_ys) - pad_ft
            max_y = max(all_ys) + pad_ft

            # Build planned grid list (used by both dry-run and the real path)
            planned_v = []
            for i, x in enumerate(xs):
                name = "{}{}".format(v_prefix, start_v_num + i)
                planned_v.append({"name": name, "x_mm": round(_ft_to_mm(x), 3)})
            planned_h = []
            for i, y in enumerate(ys):
                name = "{}{}".format(h_prefix, _alpha_name(i, start_h_letter))
                planned_h.append({"name": name, "y_mm": round(_ft_to_mm(y), 3)})

            if dry_run:
                return routes.make_response(data={
                    "status": "dry_run",
                    "walls_scanned": len(walls),
                    "skipped_diagonal": skipped_diagonal,
                    "skipped_curved": skipped_curved,
                    "skipped_non_curve": skipped_non_curve,
                    "would_create_vertical": planned_v,
                    "would_create_horizontal": planned_h,
                    "extent_mm": {
                        "min_x": round(_ft_to_mm(min_x), 3),
                        "max_x": round(_ft_to_mm(max_x), 3),
                        "min_y": round(_ft_to_mm(min_y), 3),
                        "max_y": round(_ft_to_mm(max_y), 3),
                    },
                })

            # Check for name collisions with existing grids
            existing_names = set()
            for g in DB.FilteredElementCollector(doc).OfClass(DB.Grid):
                try:
                    existing_names.add(DB.Element.Name.__get__(g))
                except Exception:
                    pass

            collisions = [p for p in (planned_v + planned_h) if p["name"] in existing_names]
            if collisions:
                return routes.make_response(data={
                    "status": "name_collision",
                    "error": "Grid name(s) already in use: {}. Choose different vertical_prefix / horizontal_prefix / start_* values, or delete the existing grids first.".format(
                        ", ".join(p["name"] for p in collisions[:10])),
                    "collisions": [p["name"] for p in collisions],
                })

            created_v = []
            created_h = []
            with DB.Transaction(doc, "MCP: Create grids from wall centerlines") as t:
                t.Start()
                for p in planned_v:
                    x = _mm_to_feet(p["x_mm"])
                    line = DB.Line.CreateBound(DB.XYZ(x, min_y, 0), DB.XYZ(x, max_y, 0))
                    g = DB.Grid.Create(doc, line)
                    g.Name = p["name"]
                    created_v.append({
                        "name": p["name"],
                        "x_mm": p["x_mm"],
                        "element_id": element_id_value(g.Id),
                    })
                for p in planned_h:
                    y = _mm_to_feet(p["y_mm"])
                    line = DB.Line.CreateBound(DB.XYZ(min_x, y, 0), DB.XYZ(max_x, y, 0))
                    g = DB.Grid.Create(doc, line)
                    g.Name = p["name"]
                    created_h.append({
                        "name": p["name"],
                        "y_mm": p["y_mm"],
                        "element_id": element_id_value(g.Id),
                    })
                t.Commit()

            return routes.make_response(data={
                "status": "success",
                "document_title": normalize_string(doc.Title),
                "walls_scanned": len(walls),
                "skipped_diagonal": skipped_diagonal,
                "skipped_curved": skipped_curved,
                "vertical_count": len(created_v),
                "horizontal_count": len(created_h),
                "vertical_grids": created_v,
                "horizontal_grids": created_h,
                "extent_mm": {
                    "min_x": round(_ft_to_mm(min_x), 3),
                    "max_x": round(_ft_to_mm(max_x), 3),
                    "min_y": round(_ft_to_mm(min_y), 3),
                    "max_y": round(_ft_to_mm(max_y), 3),
                },
            })

        except Exception as e:
            logger.error("create_grids_from_walls failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Grids routes registered successfully")
