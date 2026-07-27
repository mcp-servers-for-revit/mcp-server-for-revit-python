# -*- coding: UTF-8 -*-
"""
Dimensions Module for Revit MCP
Create a linear Dimension between two points in a view, optionally locking the
witness lines to geometric references on a set of elements (the typical
"dimension between two parallel walls" case).

Units: all inputs are in MILLIMETRES; converted internally to Revit's decimal
feet via UnitUtils.

Ported from Sparx mcp-servers-for-revit's CreateDimensionCommand (Apache-2.0
licensed C# source at commandset/Services/AnnotationComponents/
CreateDimensionEventHandler.cs) into our IronPython pyRevit Routes pattern.

Notable differences from the C# source:
- v1 ships single-dimension creation only; batch can be added later if a real
  workflow demands it.
- When `line_point` is supplied we actually USE it: the dim line is
  reconstructed parallel to the start->end direction but anchored on the
  supplied line point. The Sparx C# computes `linePoint` but never threads it
  into `Doc.Create.NewDimension`, so its callers had to put start/end on the
  desired dim line themselves.
- When `line_point` is omitted, we default to a 1 ft (304.8 mm) perpendicular
  offset from the midpoint of start/end (in the XY plane). Sparx instead used
  start/end verbatim as the dim line, which often runs the dim line straight
  through the elements being measured.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value
from System import Int64
import json
import traceback
import logging

logger = logging.getLogger(__name__)

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8


def _parse_json_request(request):
    if not request or not request.data:
        raise ValueError("No data provided")
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _mm_to_feet(value_mm):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.UnitTypeId.Millimeters)
    except AttributeError:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_mm), DB.DisplayUnitType.DUT_MILLIMETERS)


def _xyz_from_mm(x_mm, y_mm, z_mm):
    return DB.XYZ(_mm_to_feet(x_mm), _mm_to_feet(y_mm), _mm_to_feet(z_mm))


def _resolve_view(doc, view_id):
    """Return (view, source) or (None, reason)."""
    if view_id is not None:
        try:
            vid = DB.ElementId(Int64(int(view_id)))
        except Exception:
            return None, "view_id_must_be_int"
        el = doc.GetElement(vid)
        if el is None or not isinstance(el, DB.View):
            return None, "view_id_not_found"
        return el, "explicit_view_id"
    try:
        uidoc = revit.uidoc
    except Exception:
        uidoc = None
    if uidoc is None or uidoc.ActiveView is None:
        return None, "no_active_view"
    return uidoc.ActiveView, "active_view"


def _safe_category_name(elem):
    try:
        cat = elem.Category
        if cat is None:
            return None
        return normalize_string(cat.Name)
    except Exception:
        return None


def _get_references_for_element(elem, view, dim_direction):
    """
    Walk an element's geometry in the given view, pick the planar face whose
    normal is most aligned with `dim_direction` (in absolute value, so we
    accept faces pointing either toward or away from the dim line — they're
    the two parallel faces of a wall).

    Faces whose normal is mostly vertical (|Z| > 0.9) are skipped — those are
    the top/bottom of a wall and have no use in a plan dimension.

    Returns a list (0 or 1 references) in order of priority:
      1. Best-aligned planar face Reference.
      2. Generic Reference(element) fallback if no usable face is found.
    """
    refs = []

    opts = DB.Options()
    opts.View = view
    opts.ComputeReferences = True

    try:
        geom = elem.get_Geometry(opts)
    except Exception:
        geom = None

    best_ref = None
    best_alignment = -1.0

    def _scan_solid(solid):
        # Closure over best_ref / best_alignment via list to allow mutation in IronPython 2
        # (nonlocal isn't available). Returns (best_ref, best_alignment) tuple.
        local_best = best_ref_holder[0]
        local_align = best_align_holder[0]
        try:
            face_iter = solid.Faces
        except Exception:
            return
        for face in face_iter:
            if not isinstance(face, DB.PlanarFace):
                continue
            try:
                normal = face.FaceNormal
            except Exception:
                continue
            if abs(normal.Z) > 0.9:
                # Horizontal face — skip (top/bottom of wall is useless for plan dim)
                continue
            if dim_direction is not None:
                try:
                    alignment = abs(normal.DotProduct(dim_direction))
                except Exception:
                    continue
                if alignment > local_align:
                    local_align = alignment
                    local_best = face.Reference
            else:
                # No direction info — first vertical face wins
                if face.Reference is not None:
                    refs.append(face.Reference)
                    return
        best_ref_holder[0] = local_best
        best_align_holder[0] = local_align

    best_ref_holder = [None]
    best_align_holder = [-1.0]

    if geom is not None:
        for obj in geom:
            if isinstance(obj, DB.Solid) and obj.Faces.Size > 0:
                _scan_solid(obj)
                if refs:  # early-exit fallback path
                    return refs
            elif isinstance(obj, DB.GeometryInstance):
                try:
                    sub_geom = obj.GetInstanceGeometry()
                except Exception:
                    sub_geom = None
                if sub_geom is None:
                    continue
                for sub_obj in sub_geom:
                    if isinstance(sub_obj, DB.Solid) and sub_obj.Faces.Size > 0:
                        _scan_solid(sub_obj)
                        if refs:
                            return refs

    best_ref = best_ref_holder[0]
    if best_ref is not None:
        refs.append(best_ref)
        return refs

    # Last-resort fallback: generic reference to the element itself.
    try:
        refs.append(DB.Reference(elem))
    except Exception:
        pass
    return refs


def _find_reference_at_point(doc, view, point, dim_direction, tolerance_ft=5.0):
    """
    Point-based fallback used when no element_ids are supplied. Finds the
    closest element in the view to `point` (within `tolerance_ft` feet) and
    delegates to _get_references_for_element to pick a face on it.
    """
    coll = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()

    closest_elem = None
    min_dist = float("inf")

    for elem in coll:
        try:
            loc = elem.Location
        except Exception:
            loc = None
        if loc is None:
            continue
        elem_pt = None
        try:
            if isinstance(loc, DB.LocationPoint):
                elem_pt = loc.Point
            elif isinstance(loc, DB.LocationCurve):
                try:
                    proj = loc.Curve.Project(point)
                except Exception:
                    proj = None
                if proj is not None:
                    elem_pt = proj.XYZPoint
        except Exception:
            elem_pt = None
        if elem_pt is None:
            continue
        try:
            d = point.DistanceTo(elem_pt)
        except Exception:
            continue
        if d < min_dist:
            min_dist = d
            closest_elem = elem

    if closest_elem is None or min_dist > tolerance_ft:
        return None, closest_elem, min_dist

    refs = _get_references_for_element(closest_elem, view, dim_direction)
    if not refs:
        return None, closest_elem, min_dist
    return refs[0], closest_elem, min_dist


def _default_line_point(start, end, dim_direction):
    """
    Build the default dim line: perpendicular to dim_direction in XY, offset
    1 ft (304.8 mm) from the midpoint of start/end. For purely vertical
    dimensions where the XY projection is zero, fall back to +X as the
    perpendicular direction.
    """
    dim_xy_x = dim_direction.X
    dim_xy_y = dim_direction.Y
    xy_len_sq = dim_xy_x * dim_xy_x + dim_xy_y * dim_xy_y
    if xy_len_sq > 1e-12:
        xy_len = xy_len_sq ** 0.5
        # Rotate 90° CCW in XY plane: (x, y) -> (-y, x)
        perp = DB.XYZ(-dim_xy_y / xy_len, dim_xy_x / xy_len, 0.0)
    else:
        perp = DB.XYZ(1.0, 0.0, 0.0)
    offset_ft = _mm_to_feet(304.8)  # exactly 1 ft
    midpoint = DB.XYZ(
        (start.X + end.X) / 2.0,
        (start.Y + end.Y) / 2.0,
        (start.Z + end.Z) / 2.0,
    )
    return midpoint.Add(perp.Multiply(offset_ft))


def register_dimensions_routes(api):
    """Register dimension-creation routes."""

    @api.route("/create_dimension/", methods=["POST"])
    def create_dimension(doc, request):
        """
        Create a linear Dimension between two points (and optionally between
        a list of elements) in the given view.

        Expected payload (all coordinates in MILLIMETRES):
        {
            "start_x_mm":         0.0,         # required
            "start_y_mm":         0.0,         # required
            "start_z_mm":         0.0,         # required
            "end_x_mm":           5000.0,      # required
            "end_y_mm":           0.0,         # required
            "end_z_mm":           0.0,         # required
            "line_x_mm":          null,        # optional dim-line anchor
            "line_y_mm":          null,
            "line_z_mm":          null,
            "element_ids":        [123, 456],  # optional, walls/elements to dim
            "view_id":            null,        # optional, defaults to active view
            "dimension_style_id": null         # optional DimensionType id
        }

        Returns status='success' with the new dimension's id and measured
        value, OR a recoverable status like 'no_references_found' /
        'fewer_than_2_references' when the geometric reference picker can't
        find what to lock onto.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)

            # ----- Required: start / end points -----
            required = ("start_x_mm", "start_y_mm", "start_z_mm",
                        "end_x_mm", "end_y_mm", "end_z_mm")
            for k in required:
                if k not in data or data[k] is None:
                    return routes.make_response(data={
                        "error": "Missing required field: {} (number, millimetres)".format(k)
                    }, status=400)
            try:
                start = _xyz_from_mm(data["start_x_mm"], data["start_y_mm"], data["start_z_mm"])
                end = _xyz_from_mm(data["end_x_mm"], data["end_y_mm"], data["end_z_mm"])
            except (TypeError, ValueError) as ve:
                return routes.make_response(data={
                    "error": "start/end coordinates must be numbers: {}".format(ve)
                }, status=400)

            try:
                separation_ft = start.DistanceTo(end)
            except Exception:
                separation_ft = 0.0
            if separation_ft < 1e-9:
                return routes.make_response(data={
                    "error": "start and end points are identical; dimension direction undefined"
                }, status=400)

            dim_direction = (end.Subtract(start)).Normalize()

            # ----- Optional: line point (anchors the dim line) -----
            has_line_pt = (
                data.get("line_x_mm") is not None
                and data.get("line_y_mm") is not None
                and data.get("line_z_mm") is not None
            )
            if has_line_pt:
                try:
                    line_anchor = _xyz_from_mm(data["line_x_mm"], data["line_y_mm"], data["line_z_mm"])
                except (TypeError, ValueError) as ve:
                    return routes.make_response(data={
                        "error": "line point coordinates must be numbers: {}".format(ve)
                    }, status=400)
                line_anchor_source = "explicit"
            else:
                line_anchor = _default_line_point(start, end, dim_direction)
                line_anchor_source = "default_1ft_perp_offset"

            half_len = separation_ft / 2.0
            dim_line_start = line_anchor.Subtract(dim_direction.Multiply(half_len))
            dim_line_end = line_anchor.Add(dim_direction.Multiply(half_len))

            # ----- Resolve view -----
            view, view_source = _resolve_view(doc, data.get("view_id"))
            if view is None:
                return routes.make_response(data={
                    "status": "view_not_found",
                    "error": view_source,
                })

            # ----- Resolve element_ids (optional) -----
            element_ids_raw = data.get("element_ids") or []
            if not isinstance(element_ids_raw, list):
                return routes.make_response(data={
                    "error": "element_ids must be a list of integers"
                }, status=400)

            elements = []
            invalid_ids = []
            for eid in element_ids_raw:
                try:
                    el = doc.GetElement(DB.ElementId(Int64(int(eid))))
                except Exception:
                    el = None
                if el is None:
                    invalid_ids.append(eid)
                else:
                    elements.append(el)

            # ----- Build references -----
            ref_array = DB.ReferenceArray()
            ref_diag = []
            picker_path = None

            if elements:
                picker_path = "element_ids"
                for elem in elements:
                    picked = _get_references_for_element(elem, view, dim_direction)
                    for r in picked:
                        ref_array.Append(r)
                        ref_diag.append({
                            "element_id": element_id_value(elem.Id),
                            "category": _safe_category_name(elem),
                            "source": "element_geometry",
                        })
            else:
                picker_path = "point_proximity"
                start_ref, start_elem, start_dist = _find_reference_at_point(doc, view, start, dim_direction)
                end_ref, end_elem, end_dist = _find_reference_at_point(doc, view, end, dim_direction)
                if start_ref is not None:
                    ref_array.Append(start_ref)
                    ref_diag.append({
                        "source": "start_point_proximity",
                        "element_id": element_id_value(start_elem.Id) if start_elem is not None else None,
                        "distance_ft": round(start_dist, 4) if start_dist != float("inf") else None,
                    })
                if end_ref is not None:
                    ref_array.Append(end_ref)
                    ref_diag.append({
                        "source": "end_point_proximity",
                        "element_id": element_id_value(end_elem.Id) if end_elem is not None else None,
                        "distance_ft": round(end_dist, 4) if end_dist != float("inf") else None,
                    })

            if ref_array.Size < 2:
                return routes.make_response(data={
                    "status": "no_references_found" if ref_array.Size == 0 else "fewer_than_2_references",
                    "error": "Could not gather >= 2 geometric references to dimension "
                             "(found {}). Provide element_ids of two parallel walls/elements, "
                             "or make sure start/end points lie within 5 ft of dimensionable "
                             "elements visible in the view.".format(ref_array.Size),
                    "references_collected": ref_array.Size,
                    "references": ref_diag,
                    "invalid_element_ids": invalid_ids,
                    "picker_path": picker_path,
                    "view_name": normalize_string(view.Name),
                    "view_id": element_id_value(view.Id),
                })

            # ----- Create the dimension in one transaction -----
            dimension = None
            with DB.Transaction(doc, "MCP: Create Dimension") as t:
                t.Start()
                try:
                    dim_line = DB.Line.CreateBound(dim_line_start, dim_line_end)
                except Exception as line_err:
                    t.RollBack()
                    return routes.make_response(data={
                        "status": "invalid_dim_line",
                        "error": "Failed to construct dim line: {}".format(line_err),
                    })
                try:
                    dimension = doc.Create.NewDimension(view, dim_line, ref_array)
                except Exception as create_err:
                    t.RollBack()
                    return routes.make_response(data={
                        "status": "create_failed",
                        "error": str(create_err),
                        "references_collected": ref_array.Size,
                        "references": ref_diag,
                        "picker_path": picker_path,
                        "view_name": normalize_string(view.Name),
                        "view_id": element_id_value(view.Id),
                    })

                if dimension is None:
                    t.RollBack()
                    return routes.make_response(data={
                        "status": "create_failed",
                        "error": "Revit returned None from NewDimension (references likely don't lie on the dim line)",
                        "references_collected": ref_array.Size,
                        "references": ref_diag,
                        "picker_path": picker_path,
                    })

                # Optional dimension style
                style_id = data.get("dimension_style_id")
                applied_style_id = None
                applied_style_name = None
                if style_id is not None:
                    try:
                        style_id_int = int(style_id)
                    except (TypeError, ValueError):
                        style_id_int = -1
                    if style_id_int > 0:
                        try:
                            dtype = doc.GetElement(DB.ElementId(Int64(style_id_int)))
                            if isinstance(dtype, DB.DimensionType):
                                dimension.DimensionType = dtype
                                applied_style_id = element_id_value(dtype.Id)
                                try:
                                    applied_style_name = normalize_string(dtype.Name)
                                except Exception:
                                    applied_style_name = None
                        except Exception:
                            pass

                t.Commit()

            # ----- Read the measured value -----
            value_ft = None
            try:
                v = dimension.Value
                if v is not None:
                    value_ft = float(v)
            except Exception:
                value_ft = None
            value_mm = (value_ft * FT_TO_MM) if value_ft is not None else None

            return routes.make_response(data={
                "status": "success",
                "dimension": {
                    "id": element_id_value(dimension.Id),
                    "unique_id": dimension.UniqueId,
                    "references_count": ref_array.Size,
                    "value_mm": round(value_mm, 3) if value_mm is not None else None,
                    "value_ft": round(value_ft, 4) if value_ft is not None else None,
                    "view_name": normalize_string(view.Name),
                    "view_id": element_id_value(view.Id),
                    "dimension_style_id": applied_style_id,
                    "dimension_style_name": applied_style_name,
                },
                "references": ref_diag,
                "picker_path": picker_path,
                "view_source": view_source,
                "line_anchor_source": line_anchor_source,
                "invalid_element_ids": invalid_ids,
            })

        except Exception as e:
            logger.error("create_dimension failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Dimensions routes registered successfully")
