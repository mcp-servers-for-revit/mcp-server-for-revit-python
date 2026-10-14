# -*- coding: UTF-8 -*-
"""
MEP-to-Grid Dimensioning Module for Revit MCP

Automatically dimensions MEP elements (ducts, cable trays, pipes) to the
project grid system in a plan view, laid out with clean, even spacing.

This is the "locate the services against the grid" workflow that an MEP
drafter does by hand on a coordination drawing: for each grid axis, the
position of every duct/pipe centerline is dimensioned relative to the grid
lines that box it in.

----------------------------------------------------------------------
HOW IT WORKS
----------------------------------------------------------------------
1. MEP runs are classified by their run direction:
     - an X-running (East-West) run is LOCATED by its Y coordinate, so it is
       dimensioned against the HORIZONTAL grids -> a VERTICAL dimension string.
     - a Y-running (North-South) run is located by its X coordinate, so it is
       dimensioned against the VERTICAL grids -> a HORIZONTAL dimension string.
     - a Z-running riser (a point in plan) is skipped.
2. Witness references:
     - Grid           -> DB.Reference(grid)
     - MEP centerline -> DB.Reference(mep_element)
   Revit resolves a plain element reference on a Grid to the grid line and on
   an MEPCurve (Duct / Pipe / CableTray) to its centerline. A linear
   `Document.Create.NewDimension(view, line, ReferenceArray)` call with N
   references in coordinate order produces one continuous (N-1)-segment
   dimension string -- exactly the MEP-coordination pattern.
3. Layout:
     - `string_style="continuous"` -> ONE continuous string per orientation
       witnessing the chosen grids + every MEP centerline (recommended).
     - `string_style="individual"` -> one 3-reference dimension per MEP run
       (grid-below -> run -> grid-above), greedily lane-stacked to avoid
       overlaps.
     - The dimension line sits `offset_mm` clear of the dimensioned geometry;
       stacked strings (individual mode) are `gap_mm` apart.

----------------------------------------------------------------------
REVIT 2026 IRONPYTHON NOTES (see CLAUDE.md)
----------------------------------------------------------------------
- ElementId integer read via the shared `element_id_value()` helper (.Value).
- `DB.ElementId(System.Int64(id))` for any id constructed from an int literal.
- `DB.Element.Name.__get__(elem)` for Grid / ElementType name reads.
- `NewDimension` does NOT open independent sub-transactions, so a single
  `DB.Transaction` (not a TransactionGroup) gives atomic single-Ctrl+Z undo.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
from System import Int64
import json
import traceback
import logging

logger = logging.getLogger(__name__)

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

# MEP categories this tool can dimension. Keys are the friendly aliases the
# tool accepts; values are the BuiltInCategory.
MEP_CATEGORIES = {
    "ducts": DB.BuiltInCategory.OST_DuctCurves,
    "cable_trays": DB.BuiltInCategory.OST_CableTray,
    "pipes": DB.BuiltInCategory.OST_PipeCurves,
}
# Alias normalisation so callers can pass "duct", "cabletray", "OST_PipeCurves", ...
_CATEGORY_ALIASES = {
    "duct": "ducts", "ducts": "ducts", "ductcurves": "ducts",
    "ost_ductcurves": "ducts",
    "cabletray": "cable_trays", "cabletrays": "cable_trays",
    "cable_tray": "cable_trays", "cable_trays": "cable_trays",
    "ost_cabletray": "cable_trays",
    "pipe": "pipes", "pipes": "pipes", "pipecurves": "pipes",
    "ost_pipecurves": "pipes",
}


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
    return value_ft * FT_TO_MM


def _resolve_plan_view(doc, view_id):
    """Return (view, source) or (None, reason). The view must be a ViewPlan."""
    if view_id is not None:
        try:
            vid = DB.ElementId(Int64(int(view_id)))
        except Exception:
            return None, "view_id_must_be_int"
        el = doc.GetElement(vid)
        if el is None or not isinstance(el, DB.View):
            return None, "view_id_not_found"
        if not isinstance(el, DB.ViewPlan):
            return None, "view_not_plan"
        return el, "explicit_view_id"
    try:
        uidoc = revit.uidoc
    except Exception:
        uidoc = None
    if uidoc is None or uidoc.ActiveView is None:
        return None, "no_active_view"
    av = uidoc.ActiveView
    if not isinstance(av, DB.ViewPlan):
        return None, "view_not_plan"
    return av, "active_view"


def _line_orientation(curve, tol_ft):
    """Classify a DB.Line as 'x', 'y', 'z' or 'diagonal'.

    'x' = runs mostly along X, 'y' = along Y, 'z' = vertical riser.
    """
    if not isinstance(curve, DB.Line):
        return None
    d = curve.Direction
    ax, ay, az = abs(d.X), abs(d.Y), abs(d.Z)
    if az > 0.7:
        return "z"
    if ax >= ay:
        return "x" if ay < 0.34 else "diagonal"
    return "y" if ax < 0.34 else "diagonal"


def _classify_grid(grid):
    """
    Return (orientation, coord_ft) for an axis-aligned straight grid, or
    (None, None) for arc / diagonal grids.

    orientation 'vertical'   -> constant X line, coord = X
    orientation 'horizontal' -> constant Y line, coord = Y
    """
    try:
        curve = grid.Curve
    except Exception:
        return None, None
    if not isinstance(curve, DB.Line):
        return None, None
    d = curve.Direction
    p0 = curve.GetEndPoint(0)
    if abs(d.X) < 1e-6 and abs(d.Y) > 1e-6:
        return "vertical", p0.X
    if abs(d.Y) < 1e-6 and abs(d.X) > 1e-6:
        return "horizontal", p0.Y
    return None, None


def register_mep_dimensions_routes(api):
    """Register the MEP-to-grid dimensioning route."""

    @api.route("/dimension_mep_to_grids/", methods=["POST"])
    def dimension_mep_to_grids(doc, request):
        """
        Dimension MEP elements (ducts / cable trays / pipes) to the project
        grid system in a plan view.

        Expected payload (all optional):
        {
            "view_id":                 null,     # plan view; default = active view
            "element_ids":             [1,2,3],  # explicit MEP elements to dimension
            "categories":              ["ducts","pipes","cable_trays"],
            "string_style":            "continuous",  # or "individual"
            "grid_scope":              "nearest",     # or "all"
            "offset_mm":               2500.0,
            "gap_mm":                  1200.0,
            "coordinate_tolerance_mm": 10.0,
            "max_elements":            200,
            "dimension_style_id":      null,
            "dry_run":                 false
        }

        When `element_ids` is omitted, every MEP element of the requested
        categories that is visible in the view is dimensioned (subject to
        `max_elements`).
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)

            string_style = str(data.get("string_style", "continuous") or "continuous").lower()
            if string_style not in ("continuous", "individual"):
                return routes.make_response(data={
                    "error": "string_style must be 'continuous' or 'individual'"}, status=400)
            grid_scope = str(data.get("grid_scope", "nearest") or "nearest").lower()
            if grid_scope not in ("nearest", "all"):
                return routes.make_response(data={
                    "error": "grid_scope must be 'nearest' or 'all'"}, status=400)

            offset_mm = float(data.get("offset_mm", 2500.0))
            gap_mm = float(data.get("gap_mm", 1200.0))
            tol_mm = float(data.get("coordinate_tolerance_mm", 10.0))
            max_elements = int(data.get("max_elements", 200))
            dry_run = bool(data.get("dry_run", False))
            dim_style_id = data.get("dimension_style_id")

            offset_ft = _mm_to_feet(offset_mm)
            gap_ft = _mm_to_feet(gap_mm)
            tol_ft = _mm_to_feet(tol_mm)
            end_margin_ft = _mm_to_feet(1000.0)

            # ----- Resolve view -----
            view, view_source = _resolve_plan_view(doc, data.get("view_id"))
            if view is None:
                return routes.make_response(data={
                    "status": "view_not_found" if view_source != "view_not_plan" else "view_not_plan",
                    "error": {
                        "view_id_must_be_int": "view_id must be an integer",
                        "view_id_not_found": "view_id does not resolve to a view",
                        "view_not_plan": "Target view is not a plan view. MEP-to-grid "
                                         "dimensioning needs a FloorPlan / CeilingPlan / "
                                         "EngineeringPlan / AreaPlan.",
                        "no_active_view": "No active view and no view_id supplied",
                    }.get(view_source, view_source),
                })

            # ----- Which MEP categories -----
            cats_raw = data.get("categories")
            if cats_raw:
                if not isinstance(cats_raw, list):
                    return routes.make_response(data={
                        "error": "categories must be a list of strings"}, status=400)
                wanted = []
                for c in cats_raw:
                    key = _CATEGORY_ALIASES.get(str(c).strip().lower())
                    if key and key not in wanted:
                        wanted.append(key)
                if not wanted:
                    return routes.make_response(data={
                        "error": "categories did not match any of: ducts, cable_trays, pipes"},
                        status=400)
            else:
                wanted = ["ducts", "cable_trays", "pipes"]

            # ----- Collect grids visible in the view -----
            grid_v = []   # (coord_ft, grid)  vertical grids (constant X)
            grid_h = []   # (coord_ft, grid)  horizontal grids (constant Y)
            skipped_grids = 0
            for g in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Grid):
                orient, coord = _classify_grid(g)
                if orient == "vertical":
                    grid_v.append((coord, g))
                elif orient == "horizontal":
                    grid_h.append((coord, g))
                else:
                    skipped_grids += 1
            grid_v.sort(key=lambda t: t[0])
            grid_h.sort(key=lambda t: t[0])

            if not grid_v and not grid_h:
                return routes.make_response(data={
                    "status": "no_grids",
                    "error": "No axis-aligned grids are visible in view '{}'. "
                             "Create a grid system first (see create_grids_from_walls).".format(
                                 normalize_string(view.Name)),
                    "skipped_non_axis_aligned_grids": skipped_grids,
                    "view_name": normalize_string(view.Name),
                    "view_id": element_id_value(view.Id),
                })

            # ----- Collect MEP elements -----
            mep_elements = []
            invalid_ids = []
            wrong_category_ids = []
            element_ids_raw = data.get("element_ids")
            source_mode = None

            if element_ids_raw:
                source_mode = "element_ids"
                if not isinstance(element_ids_raw, list):
                    return routes.make_response(data={
                        "error": "element_ids must be a list of integers"}, status=400)
                for eid in element_ids_raw:
                    try:
                        el = doc.GetElement(DB.ElementId(Int64(int(eid))))
                    except Exception:
                        el = None
                    if el is None:
                        invalid_ids.append(eid)
                        continue
                    if not isinstance(el, DB.MEPCurve):
                        wrong_category_ids.append(eid)
                        continue
                    mep_elements.append(el)
            else:
                source_mode = "view_visible"
                for key in wanted:
                    bic = MEP_CATEGORIES[key]
                    for el in (DB.FilteredElementCollector(doc, view.Id)
                               .OfCategory(bic).WhereElementIsNotElementType()):
                        if isinstance(el, DB.MEPCurve):
                            mep_elements.append(el)

            if not mep_elements:
                return routes.make_response(data={
                    "status": "no_mep_elements",
                    "error": "No dimensionable MEP elements found "
                             "({}).".format("from the supplied element_ids"
                                            if source_mode == "element_ids"
                                            else "visible in the view"),
                    "invalid_element_ids": invalid_ids,
                    "wrong_category_element_ids": wrong_category_ids,
                    "view_name": normalize_string(view.Name),
                })

            if source_mode == "view_visible" and len(mep_elements) > max_elements:
                return routes.make_response(data={
                    "status": "too_many_elements",
                    "error": "{} MEP elements are visible in the view, which exceeds "
                             "max_elements={}. Dimensioning that many at once would be "
                             "unreadable. Pass an explicit element_ids list for the runs "
                             "you want, narrow the view, or raise max_elements "
                             "deliberately.".format(len(mep_elements), max_elements),
                    "mep_visible_count": len(mep_elements),
                    "max_elements": max_elements,
                    "view_name": normalize_string(view.Name),
                })

            # ----- Classify each MEP element by run orientation -----
            # x_runs: located by Y -> dimensioned against horizontal grids
            # y_runs: located by X -> dimensioned against vertical grids
            x_runs = []   # dict per run
            y_runs = []
            skipped_mep = []   # (id, reason)
            for el in mep_elements:
                loc = el.Location
                if not isinstance(loc, DB.LocationCurve):
                    skipped_mep.append((element_id_value(el.Id), "no_location_curve"))
                    continue
                curve = loc.Curve
                orient = _line_orientation(curve, tol_ft)
                if orient is None:
                    skipped_mep.append((element_id_value(el.Id), "non_linear_curve"))
                    continue
                if orient == "z":
                    skipped_mep.append((element_id_value(el.Id), "vertical_riser"))
                    continue
                if orient == "diagonal":
                    skipped_mep.append((element_id_value(el.Id), "diagonal_run"))
                    continue
                p0 = curve.GetEndPoint(0)
                p1 = curve.GetEndPoint(1)
                try:
                    cat_name = normalize_string(el.Category.Name)
                except Exception:
                    cat_name = None
                run = {
                    "element": el,
                    "id": element_id_value(el.Id),
                    "category": cat_name,
                }
                if orient == "x":
                    run["coord"] = (p0.Y + p1.Y) / 2.0          # located by Y
                    run["ext_min"] = min(p0.X, p1.X)
                    run["ext_max"] = max(p0.X, p1.X)
                    x_runs.append(run)
                else:
                    run["coord"] = (p0.X + p1.X) / 2.0          # located by X
                    run["ext_min"] = min(p0.Y, p1.Y)
                    run["ext_max"] = max(p0.Y, p1.Y)
                    y_runs.append(run)

            if not x_runs and not y_runs:
                return routes.make_response(data={
                    "status": "no_dimensionable_mep",
                    "error": "MEP elements were found but none are axis-aligned "
                             "horizontal runs. Risers and diagonal runs cannot be "
                             "dimensioned to grids in plan.",
                    "skipped_mep": [{"id": i, "reason": r} for i, r in skipped_mep],
                    "view_name": normalize_string(view.Name),
                })

            # ----- Build the dimension plan for each orientation -----
            # An orientation "job" pairs one MEP-run group with the grid list
            # it is dimensioned against and the axis the string measures along.
            jobs = []
            if x_runs:
                jobs.append({
                    "name": "horizontal_runs_vs_horizontal_grids",
                    "runs": x_runs,
                    "grids": grid_h,
                    "measure_axis": "y",   # string measures Y, runs vertically
                })
            if y_runs:
                jobs.append({
                    "name": "vertical_runs_vs_vertical_grids",
                    "runs": y_runs,
                    "grids": grid_v,
                    "measure_axis": "x",   # string measures X, runs horizontally
                })

            planned = []   # one entry per dimension string we intend to create
            warnings = []

            for job in jobs:
                runs = job["runs"]
                grids = job["grids"]
                if not grids:
                    warnings.append(
                        "{}: no grids of the required orientation are visible in "
                        "the view; {} run(s) left undimensioned.".format(
                            job["name"], len(runs)))
                    for r in runs:
                        skipped_mep.append((r["id"], "no_grids_for_orientation"))
                    continue

                grid_coords = [c for c, _ in grids]

                # Dedupe MEP runs that share a coordinate (parallel segments of
                # one visual run). Keep the first; record the rest.
                runs_sorted = sorted(runs, key=lambda r: r["coord"])
                mep_nodes = []
                for r in runs_sorted:
                    if mep_nodes and abs(r["coord"] - mep_nodes[-1]["coord"]) <= tol_ft:
                        mep_nodes[-1]["merged_ids"].append(r["id"])
                        continue
                    mep_nodes.append({
                        "coord": r["coord"],
                        "element": r["element"],
                        "id": r["id"],
                        "category": r["category"],
                        "ext_min": r["ext_min"],
                        "ext_max": r["ext_max"],
                        "merged_ids": [],
                    })

                # Which grids participate.
                if grid_scope == "all":
                    chosen_grid_idx = set(range(len(grids)))
                else:
                    chosen_grid_idx = set()
                    for mn in mep_nodes:
                        mc = mn["coord"]
                        below = None
                        above = None
                        for gi, gc in enumerate(grid_coords):
                            if gc <= mc + tol_ft:
                                below = gi
                            if gc >= mc - tol_ft and above is None:
                                above = gi
                        if below is not None:
                            chosen_grid_idx.add(below)
                        if above is not None:
                            chosen_grid_idx.add(above)

                grid_nodes = [{
                    "coord": grids[gi][0],
                    "element": grids[gi][1],
                    "name": get_element_name(grids[gi][1]),
                } for gi in sorted(chosen_grid_idx)]

                # Combined, coordinate-sorted node list. When a MEP run sits on
                # a grid (within tolerance) we keep the grid and drop the run.
                combined = []
                for gn in grid_nodes:
                    combined.append(("grid", gn["coord"], gn))
                for mn in mep_nodes:
                    combined.append(("mep", mn["coord"], mn))
                combined.sort(key=lambda t: t[1])

                nodes = []
                for kind, coord, payload in combined:
                    if nodes and abs(coord - nodes[-1]["coord"]) <= tol_ft:
                        prev = nodes[-1]
                        if prev["kind"] == "mep" and kind == "grid":
                            # Grid wins the slot; the run is on the grid line.
                            skipped_mep.append((prev["payload"]["id"], "coincides_with_grid"))
                            nodes[-1] = {"kind": kind, "coord": coord, "payload": payload}
                        elif kind == "mep":
                            skipped_mep.append((payload["id"], "coincides_with_grid"
                                                if prev["kind"] == "grid"
                                                else "duplicate_coordinate"))
                        # grid-on-grid duplicate: keep the first, drop silently.
                        continue
                    nodes.append({"kind": kind, "coord": coord, "payload": payload})

                mep_node_count = len([n for n in nodes if n["kind"] == "mep"])
                if mep_node_count == 0:
                    warnings.append(
                        "{}: every run coincided with a grid line; nothing to "
                        "dimension.".format(job["name"]))
                    continue
                if len(nodes) < 2:
                    warnings.append(
                        "{}: fewer than 2 distinct witness coordinates; cannot "
                        "build a dimension.".format(job["name"]))
                    continue

                # Geometry extent of the dimensioned runs (for offset placement).
                ext_lo = min(mn["ext_min"] for mn in mep_nodes)
                ext_hi = max(mn["ext_max"] for mn in mep_nodes)
                coord_lo = nodes[0]["coord"]
                coord_hi = nodes[-1]["coord"]

                job_plan = {
                    "name": job["name"],
                    "measure_axis": job["measure_axis"],
                    "nodes": nodes,
                    "ext_lo": ext_lo,
                    "ext_hi": ext_hi,
                    "coord_lo": coord_lo,
                    "coord_hi": coord_hi,
                    "strings": [],
                }

                # ---- Build the dimension string(s) for this job ----
                if string_style == "continuous":
                    job_plan["strings"].append({
                        "node_indices": list(range(len(nodes))),
                        "lane": 0,
                    })
                else:
                    # Individual: one dim per MEP node = [grid_below, mep, grid_above]
                    # Greedy lane stacking so overlapping spans don't collide.
                    dims = []
                    for i, n in enumerate(nodes):
                        if n["kind"] != "mep":
                            continue
                        gb = None
                        ga = None
                        for j in range(i - 1, -1, -1):
                            if nodes[j]["kind"] == "grid":
                                gb = j
                                break
                        for j in range(i + 1, len(nodes)):
                            if nodes[j]["kind"] == "grid":
                                ga = j
                                break
                        idxs = [x for x in (gb, i, ga) if x is not None]
                        if len(idxs) < 2:
                            skipped_mep.append((n["payload"]["id"], "no_bracketing_grid"))
                            continue
                        dims.append({
                            "node_indices": idxs,
                            "span_lo": nodes[idxs[0]]["coord"],
                            "span_hi": nodes[idxs[-1]]["coord"],
                        })
                    dims.sort(key=lambda d: d["span_lo"])
                    lane_last_hi = []
                    for d in dims:
                        placed = False
                        for lane in range(len(lane_last_hi)):
                            if d["span_lo"] >= lane_last_hi[lane] - tol_ft:
                                d["lane"] = lane
                                lane_last_hi[lane] = d["span_hi"]
                                placed = True
                                break
                        if not placed:
                            d["lane"] = len(lane_last_hi)
                            lane_last_hi.append(d["span_hi"])
                        job_plan["strings"].append(d)

                planned.append(job_plan)

            if not planned:
                return routes.make_response(data={
                    "status": "nothing_created",
                    "error": "No dimension strings could be planned. See warnings "
                             "and skipped_mep for why.",
                    "warnings": warnings,
                    "skipped_mep": [{"id": i, "reason": r} for i, r in skipped_mep],
                    "view_name": normalize_string(view.Name),
                })

            # ----- Resolve the dimension style (optional) -----
            dim_type = None
            if dim_style_id is not None:
                try:
                    dt = doc.GetElement(DB.ElementId(Int64(int(dim_style_id))))
                    if isinstance(dt, DB.DimensionType):
                        dim_type = dt
                except Exception:
                    dim_type = None

            # ----- Compute the dimension-line geometry for every planned string -----
            # measure_axis 'y' -> vertical string, placed at an X west of the runs.
            # measure_axis 'x' -> horizontal string, placed at a Y south of the runs.
            def _string_geometry(job_plan, lane):
                axis = job_plan["measure_axis"]
                c_lo = job_plan["coord_lo"] - end_margin_ft
                c_hi = job_plan["coord_hi"] + end_margin_ft
                if axis == "y":
                    base = job_plan["ext_lo"] - offset_ft
                    pos = base - lane * gap_ft
                    p_start = DB.XYZ(pos, c_lo, 0.0)
                    p_end = DB.XYZ(pos, c_hi, 0.0)
                else:
                    base = job_plan["ext_lo"] - offset_ft
                    pos = base - lane * gap_ft
                    p_start = DB.XYZ(c_lo, pos, 0.0)
                    p_end = DB.XYZ(c_hi, pos, 0.0)
                return DB.Line.CreateBound(p_start, p_end), pos

            # ----- Dry run: report the plan without touching the model -----
            if dry_run:
                preview = []
                for jp in planned:
                    strings_preview = []
                    for s in jp["strings"]:
                        node_idx = s["node_indices"]
                        _, pos = _string_geometry(jp, s["lane"])
                        strings_preview.append({
                            "lane": s["lane"],
                            "reference_count": len(node_idx),
                            "segments": len(node_idx) - 1,
                            "witnesses": [
                                {"kind": jp["nodes"][k]["kind"],
                                 "coord_mm": round(_ft_to_mm(jp["nodes"][k]["coord"]), 1)}
                                for k in node_idx],
                            "dimension_line_position_mm": round(_ft_to_mm(pos), 1),
                        })
                    preview.append({
                        "orientation": jp["name"],
                        "string_count": len(strings_preview),
                        "strings": strings_preview,
                    })
                return routes.make_response(data={
                    "status": "dry_run",
                    "string_style": string_style,
                    "grid_scope": grid_scope,
                    "view_name": normalize_string(view.Name),
                    "view_id": element_id_value(view.Id),
                    "planned": preview,
                    "warnings": warnings,
                    "skipped_mep": [{"id": i, "reason": r} for i, r in skipped_mep],
                })

            # ----- Create every dimension string in ONE transaction -----
            created = []
            create_errors = []
            with DB.Transaction(doc, "MCP: Dimension MEP to grids") as t:
                t.Start()
                for jp in planned:
                    nodes = jp["nodes"]
                    for s in jp["strings"]:
                        node_idx = s["node_indices"]
                        ref_array = DB.ReferenceArray()
                        for k in node_idx:
                            try:
                                ref_array.Append(DB.Reference(nodes[k]["payload"]["element"]))
                            except Exception as ref_err:
                                create_errors.append({
                                    "orientation": jp["name"],
                                    "error": "reference build failed: {}".format(ref_err),
                                })
                        if ref_array.Size < 2:
                            create_errors.append({
                                "orientation": jp["name"],
                                "error": "fewer than 2 valid references for a string",
                            })
                            continue
                        dim_line, pos = _string_geometry(jp, s["lane"])
                        try:
                            dim = doc.Create.NewDimension(view, dim_line, ref_array)
                        except Exception as ce:
                            create_errors.append({
                                "orientation": jp["name"],
                                "error": str(ce),
                            })
                            continue
                        if dim is None:
                            create_errors.append({
                                "orientation": jp["name"],
                                "error": "NewDimension returned None",
                            })
                            continue
                        if dim_type is not None:
                            try:
                                dim.DimensionType = dim_type
                            except Exception:
                                pass
                        # Read measured values.
                        seg_values_mm = []
                        try:
                            n_seg = dim.NumberOfSegments
                        except Exception:
                            n_seg = 0
                        try:
                            if n_seg and n_seg > 1 and dim.Segments is not None:
                                for seg in dim.Segments:
                                    sv = seg.Value
                                    seg_values_mm.append(
                                        round(sv * FT_TO_MM, 1) if sv is not None else None)
                            else:
                                dv = dim.Value
                                if dv is not None:
                                    seg_values_mm.append(round(dv * FT_TO_MM, 1))
                        except Exception:
                            pass
                        created.append({
                            "orientation": jp["name"],
                            "dimension_id": element_id_value(dim.Id),
                            "lane": s["lane"],
                            "reference_count": ref_array.Size,
                            "segments": len(seg_values_mm),
                            "segment_values_mm": seg_values_mm,
                            "dimension_line_position_mm": round(_ft_to_mm(pos), 1),
                        })
                if created:
                    t.Commit()
                else:
                    t.RollBack()

            if not created:
                return routes.make_response(data={
                    "status": "create_failed",
                    "error": "Revit refused every planned dimension string.",
                    "create_errors": create_errors,
                    "warnings": warnings,
                    "skipped_mep": [{"id": i, "reason": r} for i, r in skipped_mep],
                    "view_name": normalize_string(view.Name),
                })

            return routes.make_response(data={
                "status": "success",
                "string_style": string_style,
                "grid_scope": grid_scope,
                "view_name": normalize_string(view.Name),
                "view_id": element_id_value(view.Id),
                "source_mode": source_mode,
                "dimensions_created": len(created),
                "dimensions": created,
                "grids_visible": {"vertical": len(grid_v), "horizontal": len(grid_h)},
                "mep_input_count": len(mep_elements),
                "mep_dimensioned": len(mep_elements) - len(skipped_mep),
                "skipped_mep": [{"id": i, "reason": r} for i, r in skipped_mep],
                "invalid_element_ids": invalid_ids,
                "wrong_category_element_ids": wrong_category_ids,
                "create_errors": create_errors,
                "warnings": warnings,
                "offset_mm": offset_mm,
                "gap_mm": gap_mm,
            })

        except Exception as e:
            logger.error("dimension_mep_to_grids failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("MEP dimensions routes registered successfully")
