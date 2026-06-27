# -*- coding: UTF-8 -*-
"""
Transforms Module for Revit MCP
Handles move, copy, rotate, and mirror operations on elements
"""

from utils import get_element_id_value, make_element_id
from pyrevit import routes, revit, DB
from System.Collections.Generic import List
import json
import math
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def register_transform_routes(api):
    """Register all transform routes with the API"""

    @api.route("/transform_elements/", methods=["POST"])
    def transform_elements_handler(doc, request):
        """Move, copy, rotate, or mirror elements."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            element_ids = data.get("element_ids", [])
            operation = data.get("operation")

            if not element_ids:
                return routes.make_response(
                    data={"error": "element_ids is required and must not be empty"},
                    status=400,
                )
            if not operation:
                return routes.make_response(
                    data={"error": "operation is required (move, copy, rotate, mirror)"},
                    status=400,
                )
            if operation not in ("move", "copy", "rotate", "mirror"):
                return routes.make_response(
                    data={"error": "Invalid operation '{}'. Use: move, copy, rotate, mirror".format(operation)},
                    status=400,
                )

            # Validate and collect elements
            elem_id_list = []
            for eid in element_ids:
                elem_id = make_element_id(eid)
                elem = doc.GetElement(elem_id)
                if not elem:
                    return routes.make_response(
                        data={"error": "Element {} not found".format(eid)},
                        status=404,
                    )
                # Check if pinned
                if hasattr(elem, "Pinned") and elem.Pinned:
                    return routes.make_response(
                        data={"error": "Element {} is pinned — unpin it first using modify_element before transforming.".format(eid)},
                        status=500,
                    )
                elem_id_list.append(elem_id)

            t = DB.Transaction(doc, "Transform Elements via MCP")
            t.Start()

            try:
                new_element_ids = []

                if operation == "move":
                    vector = data.get("vector")
                    if not vector:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "vector is required for move operation"},
                            status=400,
                        )
                    translation = DB.XYZ(
                        float(vector.get("x", 0)) * MM_TO_FEET,
                        float(vector.get("y", 0)) * MM_TO_FEET,
                        float(vector.get("z", 0)) * MM_TO_FEET,
                    )
                    for eid in elem_id_list:
                        DB.ElementTransformUtils.MoveElement(doc, eid, translation)

                elif operation == "copy":
                    vector = data.get("vector")
                    if not vector:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "vector is required for copy operation"},
                            status=400,
                        )
                    translation = DB.XYZ(
                        float(vector.get("x", 0)) * MM_TO_FEET,
                        float(vector.get("y", 0)) * MM_TO_FEET,
                        float(vector.get("z", 0)) * MM_TO_FEET,
                    )
                    for eid in elem_id_list:
                        copied = DB.ElementTransformUtils.CopyElement(
                            doc, eid, translation
                        )
                        if copied:
                            for cid in copied:
                                new_element_ids.append(get_element_id_value(cid))

                elif operation == "rotate":
                    axis_point = data.get("axis_point")
                    angle = data.get("angle")
                    if not axis_point:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "axis_point is required for rotate operation"},
                            status=400,
                        )
                    if angle is None:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "angle is required for rotate operation"},
                            status=400,
                        )

                    center = DB.XYZ(
                        float(axis_point.get("x", 0)) * MM_TO_FEET,
                        float(axis_point.get("y", 0)) * MM_TO_FEET,
                        float(axis_point.get("z", 0)) * MM_TO_FEET,
                    )
                    # Create vertical axis line through the point
                    axis_line = DB.Line.CreateBound(
                        center, DB.XYZ(center.X, center.Y, center.Z + 1.0)
                    )
                    angle_rad = float(angle) * math.pi / 180.0

                    for eid in elem_id_list:
                        DB.ElementTransformUtils.RotateElement(
                            doc, eid, axis_line, angle_rad
                        )

                elif operation == "mirror":
                    mirror_plane = data.get("mirror_plane")
                    if not mirror_plane:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "mirror_plane is required for mirror operation"},
                            status=400,
                        )

                    origin = mirror_plane.get("origin", {})
                    normal = mirror_plane.get("normal", {})

                    plane_origin = DB.XYZ(
                        float(origin.get("x", 0)) * MM_TO_FEET,
                        float(origin.get("y", 0)) * MM_TO_FEET,
                        float(origin.get("z", 0)) * MM_TO_FEET,
                    )
                    plane_normal = DB.XYZ(
                        float(normal.get("x", 0)),
                        float(normal.get("y", 1)),
                        float(normal.get("z", 0)),
                    ).Normalize()

                    plane = DB.Plane.CreateByNormalAndOrigin(plane_normal, plane_origin)

                    for eid in elem_id_list:
                        DB.ElementTransformUtils.MirrorElement(doc, eid, plane)

                t.Commit()

                result = {
                    "status": "success",
                    "operation": operation,
                    "count": len(elem_id_list),
                    "message": "{} {} element{}".format(
                        {"move": "Moved", "copy": "Copied", "rotate": "Rotated", "mirror": "Mirrored"}.get(operation, operation),
                        len(elem_id_list),
                        "s" if len(elem_id_list) != 1 else "",
                    ),
                }
                if operation == "copy" and new_element_ids:
                    result["new_element_ids"] = new_element_ids

                return routes.make_response(data=result)

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to transform elements: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Transform routes registered successfully")
