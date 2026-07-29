# -*- coding: UTF-8 -*-
"""
Annotation Module for Revit MCP
Handles automatic tagging of elements in the active view.
"""

from pyrevit import routes, DB
import json
import logging
import traceback

from utils import normalize_string, get_element_name, element_id_value

logger = logging.getLogger(__name__)


def register_annotation_routes(api):
    """Register all annotation-related routes with the API"""

    @api.route("/tag_walls/", methods=["POST"])
    def tag_walls(doc, uidoc, request):
        """
        Tag all walls visible in the currently active view.

        Requires a Wall Tag family to be loaded in the project.

        Expected JSON payload (optional):
        {
            "skip_tagged": true
        }
        """
        try:
            if not doc or not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            current_view = uidoc.ActiveView
            if not current_view:
                return routes.make_response(
                    data={"error": "No active view found"}, status=404
                )

            skip_tagged = True
            try:
                if request and request.data:
                    data = (
                        json.loads(request.data)
                        if isinstance(request.data, str)
                        else request.data
                    )
                    skip_tagged = bool(data.get("skip_tagged", True))
            except Exception:
                pass

            # A wall tag family/type must be loaded in the project
            wall_tag_symbol = (
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_WallTags)
                .WhereElementIsElementType()
                .FirstElement()
            )

            if not wall_tag_symbol:
                return routes.make_response(
                    data={
                        "error": "No Wall Tag family is loaded in this project. "
                        "Load a Wall Tag family first, then try again."
                    },
                    status=404,
                )

            # Walls visible in the current view
            walls = (
                DB.FilteredElementCollector(doc, current_view.Id)
                .OfCategory(DB.BuiltInCategory.OST_Walls)
                .WhereElementIsNotElementType()
                .ToElements()
            )

            if not walls:
                return routes.make_response(
                    data={
                        "status": "success",
                        "message": "No walls found in the current view.",
                        "view_name": normalize_string(get_element_name(current_view)),
                        "total_walls": 0,
                        "tagged_count": 0,
                        "skipped_count": 0,
                        "failed_count": 0,
                    }
                )

            # Existing tags in view, so we don't double-tag walls
            existing_tagged_ids = set()
            if skip_tagged:
                try:
                    existing_tags = (
                        DB.FilteredElementCollector(doc, current_view.Id)
                        .OfClass(DB.IndependentTag)
                        .ToElements()
                    )
                    for tag in existing_tags:
                        try:
                            for ref_id in tag.GetTaggedLocalElementIds():
                                existing_tagged_ids.add(element_id_value(ref_id))
                        except Exception:
                            continue
                except Exception as e:
                    logger.warning(
                        "Could not collect existing tags: {}".format(str(e))
                    )

            tagged_count = 0
            skipped_count = 0
            failed_count = 0
            failed_walls = []

            logger.info(
                "Tagging walls in view: {}".format(
                    normalize_string(get_element_name(current_view))
                )
            )

            t = DB.Transaction(doc, "Tag Walls via MCP")
            t.Start()

            try:
                for wall in walls:
                    try:
                        wid = element_id_value(wall.Id)
                        if skip_tagged and wid in existing_tagged_ids:
                            skipped_count += 1
                            continue

                        location = wall.Location
                        if hasattr(location, "Curve") and location.Curve:
                            curve = location.Curve
                            point = curve.Evaluate(0.5, True)
                        else:
                            skipped_count += 1
                            continue

                        reference = DB.Reference(wall)
                        DB.IndependentTag.Create(
                            doc,
                            wall_tag_symbol.Id,
                            current_view.Id,
                            reference,
                            False,
                            DB.TagOrientation.Horizontal,
                            point,
                        )
                        tagged_count += 1

                    except Exception as tag_error:
                        failed_count += 1
                        failed_walls.append(
                            {
                                "wall_id": element_id_value(wall.Id),
                                "error": str(tag_error),
                            }
                        )

                t.Commit()
                logger.info("Transaction committed successfully")

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                    logger.error("Transaction rolled back due to error")
                raise tx_error

            result = {
                "status": "success",
                "view_name": normalize_string(get_element_name(current_view)),
                "total_walls": len(walls),
                "tagged_count": tagged_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
            }

            if failed_walls:
                result["failed_walls"] = failed_walls[:20]

            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Tag walls failed: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={
                    "error": "Failed to tag walls: {}".format(str(e)),
                    "traceback": error_trace,
                },
                status=500,
            )

    logger.info("Annotation routes registered successfully")
