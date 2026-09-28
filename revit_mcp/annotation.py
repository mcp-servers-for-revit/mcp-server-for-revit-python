# -*- coding: UTF-8 -*-
"""
Annotation Module for Revit MCP
Handles creating annotations (tags) on model elements.
"""
from pyrevit import routes, DB
import json
import logging

from utils import element_id_value, normalize_string, get_element_name
import creation_tracker

logger = logging.getLogger(__name__)


def register_annotation_routes(api):
    """Register annotation-related routes with the API"""

    @api.route("/tag_walls/", methods=["POST"])
    def tag_walls(doc, uidoc, request=None):
        """
        Tag all walls visible in the current view that don't already have
        a wall tag in that view.

        Optional payload:
        {
            "tag_type_name": null   # substring match on the tag type name;
                                     # defaults to the first loaded wall tag type
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

            tag_type_filter = None
            if request and request.data:
                try:
                    data = (
                        json.loads(request.data)
                        if isinstance(request.data, str)
                        else request.data
                    )
                    tag_type_filter = data.get("tag_type_name")
                except Exception:
                    tag_type_filter = None

            tag_symbols = (
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_WallTags)
                .WhereElementIsElementType()
                .ToElements()
            )

            target_symbol = None
            if tag_type_filter:
                needle = normalize_string(tag_type_filter).lower()
                for sym in tag_symbols:
                    if needle in normalize_string(get_element_name(sym)).lower():
                        target_symbol = sym
                        break
            if not target_symbol and tag_symbols:
                target_symbol = tag_symbols[0]

            if not target_symbol:
                return routes.make_response(
                    data={"error": "No wall tag type is loaded in this project"},
                    status=404,
                )

            walls = (
                DB.FilteredElementCollector(doc, current_view.Id)
                .OfCategory(DB.BuiltInCategory.OST_Walls)
                .WhereElementIsNotElementType()
                .ToElements()
            )

            # Existing wall tags in the view, so we don't double-tag
            existing_tags = (
                DB.FilteredElementCollector(doc, current_view.Id)
                .OfCategory(DB.BuiltInCategory.OST_WallTags)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            tagged_host_ids = set()
            for tag in existing_tags:
                try:
                    for host_id in tag.GetTaggedLocalElementIds():
                        tagged_host_ids.add(element_id_value(host_id))
                except Exception:
                    continue

            tagged = []
            skipped = []

            t = DB.Transaction(doc, "Tag Walls via MCP")
            t.Start()
            try:
                if not target_symbol.IsActive:
                    target_symbol.Activate()
                    doc.Regenerate()

                for wall in walls:
                    wall_id_value = element_id_value(wall.Id)
                    if wall_id_value in tagged_host_ids:
                        skipped.append(wall_id_value)
                        continue
                    try:
                        curve = wall.Location.Curve
                        midpoint = curve.Evaluate(0.5, True)
                        reference = DB.Reference(wall)

                        try:
                            # Revit 2022+ overload (explicit tag type id)
                            new_tag = DB.IndependentTag.Create(
                                doc,
                                target_symbol.Id,
                                current_view.Id,
                                reference,
                                False,
                                DB.TagOrientation.Horizontal,
                                midpoint,
                            )
                        except TypeError:
                            # Pre-2022 API
                            new_tag = DB.IndependentTag.Create(
                                doc,
                                current_view.Id,
                                reference,
                                False,
                                DB.TagMode.TM_ADDBY_CATEGORY,
                                DB.TagOrientation.Horizontal,
                                midpoint,
                            )
                            new_tag.ChangeTypeId(target_symbol.Id)

                        tagged.append(wall_id_value)
                        creation_tracker.register_created([new_tag.Id])
                    except Exception as tag_err:
                        logger.warning(
                            "Could not tag wall {}: {}".format(
                                wall_id_value, str(tag_err)
                            )
                        )
                        skipped.append(wall_id_value)

                t.Commit()
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

            return routes.make_response(
                data={
                    "status": "success",
                    "tagged_count": len(tagged),
                    "tagged_wall_ids": tagged,
                    "skipped_wall_ids": skipped,
                    "tag_type": normalize_string(get_element_name(target_symbol)),
                }
            )

        except Exception as e:
            logger.error("Failed to tag walls: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to tag walls: {}".format(str(e))},
                status=500,
            )

    logger.info("Annotation routes registered successfully")
