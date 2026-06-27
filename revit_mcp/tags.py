# -*- coding: UTF-8 -*-
"""
Tags Module for Revit MCP
Handles element tagging with annotation symbols
"""

from utils import get_element_name, get_element_id_value, make_element_id
from pyrevit import routes, revit, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)

MM_TO_FEET = 1.0 / 304.8


def register_tag_routes(api):
    """Register all tag routes with the API"""

    @api.route("/tag_elements/", methods=["POST"])
    def tag_elements_handler(doc, request):
        """Tag elements with annotation symbols in a view."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = json.loads(request.data) if isinstance(request.data, str) else request.data

            element_ids = data.get("element_ids", [])
            if not element_ids:
                return routes.make_response(
                    data={"error": "element_ids is required and must not be empty"},
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

            add_leader = data.get("add_leader", False)
            orientation = data.get("orientation", "horizontal")
            offset = data.get("offset", {})
            tag_type_name = data.get("tag_type_name")

            offset_x = float(offset.get("x", 0)) * MM_TO_FEET if offset else 0
            offset_y = float(offset.get("y", 0)) * MM_TO_FEET if offset else 0

            # Determine tag orientation
            if orientation == "vertical":
                tag_orientation = DB.TagOrientation.Vertical
            else:
                tag_orientation = DB.TagOrientation.Horizontal

            # Map element categories to their tag categories
            CATEGORY_TO_TAG_CATEGORY = {
                DB.BuiltInCategory.OST_Walls: DB.BuiltInCategory.OST_WallTags,
                DB.BuiltInCategory.OST_Doors: DB.BuiltInCategory.OST_DoorTags,
                DB.BuiltInCategory.OST_Windows: DB.BuiltInCategory.OST_WindowTags,
                DB.BuiltInCategory.OST_Rooms: DB.BuiltInCategory.OST_RoomTags,
                DB.BuiltInCategory.OST_Floors: DB.BuiltInCategory.OST_FloorTags,
                DB.BuiltInCategory.OST_StructuralFraming: DB.BuiltInCategory.OST_StructuralFramingTags,
                DB.BuiltInCategory.OST_StructuralColumns: DB.BuiltInCategory.OST_StructuralColumnTags,
            }

            def find_tag_type_for_element(element):
                """Find the appropriate tag type for an element's category."""
                elem_cat = element.Category
                if not elem_cat:
                    return None

                # Try mapping by BuiltInCategory
                try:
                    bic = elem_cat.BuiltInCategory
                    tag_bic = CATEGORY_TO_TAG_CATEGORY.get(bic)
                    if tag_bic:
                        tag_symbols = (
                            DB.FilteredElementCollector(doc)
                            .OfCategory(tag_bic)
                            .OfClass(DB.FamilySymbol)
                            .ToElements()
                        )
                        # If user specified a tag type name, try to match
                        if tag_type_name:
                            for ts in tag_symbols:
                                if get_element_name(ts) == tag_type_name:
                                    return ts
                        # Return first available
                        if tag_symbols and tag_symbols.Count > 0:
                            return tag_symbols[0]
                except Exception:
                    pass
                return None

            t = DB.Transaction(doc, "Tag Elements via MCP")
            t.Start()

            try:
                tagged_ids = []
                skipped = []

                for eid in element_ids:
                    elem_id = make_element_id(eid)
                    elem = doc.GetElement(elem_id)

                    if not elem:
                        skipped.append({
                            "element_id": eid,
                            "reason": "Element not found",
                        })
                        continue

                    if not elem.Category:
                        skipped.append({
                            "element_id": eid,
                            "reason": "Element has no category",
                        })
                        continue

                    # Find tag type for this element's category
                    tag_type = find_tag_type_for_element(elem)
                    if not tag_type:
                        skipped.append({
                            "element_id": eid,
                            "reason": "No tag type found for category '{}'".format(
                                get_element_name(elem.Category)
                            ),
                        })
                        continue

                    # Ensure tag type is activated
                    if not tag_type.IsActive:
                        tag_type.Activate()
                        doc.Regenerate()

                    # Get element location for tag placement
                    tag_point = None
                    try:
                        loc = elem.Location
                        if hasattr(loc, "Point"):
                            pt = loc.Point
                            tag_point = DB.XYZ(pt.X + offset_x, pt.Y + offset_y, pt.Z)
                        elif hasattr(loc, "Curve"):
                            curve = loc.Curve
                            mid = curve.Evaluate(0.5, True)
                            tag_point = DB.XYZ(mid.X + offset_x, mid.Y + offset_y, mid.Z)
                        else:
                            bb = elem.get_BoundingBox(target_view)
                            if bb:
                                center = DB.XYZ(
                                    (bb.Min.X + bb.Max.X) / 2.0 + offset_x,
                                    (bb.Min.Y + bb.Max.Y) / 2.0 + offset_y,
                                    (bb.Min.Z + bb.Max.Z) / 2.0,
                                )
                                tag_point = center
                    except Exception:
                        bb = elem.get_BoundingBox(target_view)
                        if bb:
                            tag_point = DB.XYZ(
                                (bb.Min.X + bb.Max.X) / 2.0 + offset_x,
                                (bb.Min.Y + bb.Max.Y) / 2.0 + offset_y,
                                (bb.Min.Z + bb.Max.Z) / 2.0,
                            )

                    if not tag_point:
                        skipped.append({
                            "element_id": eid,
                            "reason": "Cannot determine element location",
                        })
                        continue

                    try:
                        # Use IndependentTag.Create (Revit 2024+)
                        # 7-arg signature: doc, tagTypeId, viewId, ref, addLeader, orientation, point
                        ref = DB.Reference(elem)
                        tag = DB.IndependentTag.Create(
                            doc,
                            tag_type.Id,
                            target_view.Id,
                            ref,
                            add_leader,
                            tag_orientation,
                            tag_point,
                        )

                        if tag:
                            tagged_ids.append(get_element_id_value(tag))
                        else:
                            skipped.append({
                                "element_id": eid,
                                "reason": "Tag creation returned null — no tag type available for this category",
                            })
                    except Exception as tag_err:
                        skipped.append({
                            "element_id": eid,
                            "reason": "Tag failed: {}".format(str(tag_err)),
                        })

                t.Commit()

                return routes.make_response(
                    data={
                        "status": "success",
                        "tagged_count": len(tagged_ids),
                        "tag_ids": tagged_ids,
                        "skipped": skipped,
                        "message": "Tagged {} element{}, {} skipped".format(
                            len(tagged_ids),
                            "s" if len(tagged_ids) != 1 else "",
                            len(skipped),
                        ),
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to tag elements: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Tag routes registered successfully")
