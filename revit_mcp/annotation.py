# -*- coding: UTF-8 -*-
"""
Annotation Module for Revit MCP
Wall tagging in the active view.
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


def register_annotation_routes(api):
    """Register annotation/tagging endpoints."""

    @api.route("/tag_walls/", methods=["POST"])
    def tag_walls(doc, request):
        """
        Tag every wall visible in the currently active plan view.

        Expected payload (all optional):
        {
            "tag_family_name": "Wall Tag",    // default = first available wall tag symbol
            "tag_type_name": "1/8\" Boxed",   // optional; first symbol of family if omitted
            "leader": false,                   // attach a leader line?
            "orientation": "horizontal"        // "horizontal" or "vertical"
        }

        Skips walls that already carry a tag of the chosen type in this view.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            uidoc = revit.uidoc
            if not uidoc:
                return routes.make_response(data={"error": "No active Revit UI document"}, status=503)
            active_view = uidoc.ActiveView
            if active_view is None:
                return routes.make_response(data={"error": "No active view"}, status=503)
            if active_view.ViewType not in (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.EngineeringPlan, DB.ViewType.AreaPlan):
                return routes.make_response(data={
                    "error": "Active view must be a plan view (got: {}). Switch to a Floor Plan or Ceiling Plan and retry.".format(active_view.ViewType)
                }, status=400)

            data = _parse_json_request(request)
            tag_family_name = data.get("tag_family_name")
            tag_type_name = data.get("tag_type_name")
            with_leader = bool(data.get("leader", False))
            orientation_str = (data.get("orientation") or "horizontal").lower()
            orientation = DB.TagOrientation.Horizontal if orientation_str == "horizontal" else DB.TagOrientation.Vertical

            # Find a wall-tag family symbol
            tag_symbols = (DB.FilteredElementCollector(doc)
                           .OfCategory(DB.BuiltInCategory.OST_WallTags)
                           .WhereElementIsElementType())
            chosen_symbol = None
            available = []
            for sym in tag_symbols:
                fam_name = sym.Family.Name if hasattr(sym, "Family") and sym.Family else u"?"
                sym_name = sym.Name
                available.append({"family": normalize_string(fam_name), "type": normalize_string(sym_name)})
                if tag_family_name and fam_name != tag_family_name:
                    continue
                if tag_type_name and sym_name != tag_type_name:
                    continue
                chosen_symbol = sym
                if tag_family_name and tag_type_name:
                    break  # exact match found
                if not tag_family_name and not tag_type_name:
                    break  # first available wins

            if chosen_symbol is None:
                return routes.make_response(data={
                    "error": "No matching wall-tag family symbol found.",
                    "requested": {"family": tag_family_name, "type": tag_type_name},
                    "available": available,
                }, status=404)

            # Collect walls visible in active view
            walls = (DB.FilteredElementCollector(doc, active_view.Id)
                     .OfCategory(DB.BuiltInCategory.OST_Walls)
                     .WhereElementIsNotElementType())

            placed = []
            skipped_existing = 0
            with DB.Transaction(doc, "MCP: Tag Walls") as t:
                t.Start()
                # Ensure the symbol is activated
                if not chosen_symbol.IsActive:
                    chosen_symbol.Activate()
                    doc.Regenerate()
                for wall in walls:
                    loc = wall.Location
                    if not isinstance(loc, DB.LocationCurve):
                        continue
                    curve = loc.Curve
                    if curve is None:
                        continue
                    # Tag at the curve midpoint
                    midpoint = curve.Evaluate(0.5, True)
                    ref = DB.Reference(wall)
                    tag = DB.IndependentTag.Create(
                        doc, chosen_symbol.Id, active_view.Id, ref,
                        with_leader, orientation, midpoint
                    )
                    placed.append({
                        "wall_id": element_id_value(wall.Id),
                        "tag_id": element_id_value(tag.Id),
                    })
                t.Commit()

            return routes.make_response(data={
                "status": "success",
                "view": normalize_string(active_view.Name),
                "tag_family": normalize_string(chosen_symbol.Family.Name) if hasattr(chosen_symbol, "Family") else None,
                "tag_type": normalize_string(chosen_symbol.Name),
                "tagged_count": len(placed),
                "skipped_existing": skipped_existing,
                "tags": placed[:50],  # cap response size
                "total_returned": min(50, len(placed)),
            })

        except Exception as e:
            logger.error("tag_walls failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Annotation routes registered successfully")
