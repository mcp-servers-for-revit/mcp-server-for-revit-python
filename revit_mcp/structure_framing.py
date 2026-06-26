# -*- coding: UTF-8 -*-
"""
Structural Framing Module for Revit MCP
Net-new capabilities developed for RAM-plot -> Revit modeling:
  - create_grids            : batch grid creation from line coordinates
  - configure_levels        : set elevation and/or rename levels
  - place_framing           : batch line-based framing (beams / joists)
  - create_beam_system      : structural beam system over a rectangular bay
  - tag_all_framing         : tag every framing member (and optionally columns) in a view
  - create_sheet            : new sheet with a title block and a placed view

All lengths are in feet (Revit internal units).
"""

from utils import get_element_name, find_family_symbol_safely, element_id_value
from pyrevit import routes, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)
ST = DB.Structure.StructuralType


def _parse(request):
    """Return parsed dict body or None."""
    if not request or not request.data:
        return None
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _find_symbol_by_category(doc, family_name, type_name):
    """Resolve a FamilySymbol by family + type name."""
    return find_family_symbol_safely(doc, family_name, type_name)


def _level_by_name(doc, name):
    for lvl in (DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Levels)
                .WhereElementIsNotElementType().ToElements()):
        if get_element_name(lvl) == name:
            return lvl
    return None


def _view_by_name(doc, name):
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
        if not v.IsTemplate and get_element_name(v) == name:
            return v
    return None


def register_structure_framing_routes(api):
    """Register structural framing routes with the API."""

    # ------------------------------------------------------------------ grids
    @api.route("/create_grids/", methods=["POST"])
    def create_grids(doc, request):
        """
        Create multiple grids.
        Body: {"grids": [{"name": "A", "x0": 0, "y0": -5, "x1": 0, "y1": 120}, ...]}
        Each grid is a straight line between (x0,y0) and (x1,y1) at z=0.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            data = _parse(request)
            grids = (data or {}).get("grids")
            if not grids:
                return routes.make_response(data={"error": "No 'grids' provided"}, status=400)
            t = DB.Transaction(doc, "MCP Create Grids")
            t.Start()
            made = []
            try:
                for g in grids:
                    ln = DB.Line.CreateBound(
                        DB.XYZ(float(g["x0"]), float(g["y0"]), 0.0),
                        DB.XYZ(float(g["x1"]), float(g["y1"]), 0.0))
                    grid = DB.Grid.Create(doc, ln)
                    if g.get("name"):
                        try:
                            grid.Name = g["name"]
                        except Exception as ne:
                            logger.warning("grid name '%s': %s", g.get("name"), ne)
                    made.append({"id": element_id_value(grid.Id), "name": get_element_name(grid)})
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "created": made, "count": len(made)})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    # ----------------------------------------------------------------- levels
    @api.route("/configure_levels/", methods=["POST"])
    def configure_levels(doc, request):
        """
        Set elevation and/or rename existing levels.
        Body: {"levels": [{"name": "Level 2", "elevation": 16.583, "new_name": "2nd Level"}, ...]}
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            data = _parse(request)
            specs = (data or {}).get("levels")
            if not specs:
                return routes.make_response(data={"error": "No 'levels' provided"}, status=400)
            t = DB.Transaction(doc, "MCP Configure Levels")
            t.Start()
            res = []
            try:
                for s in specs:
                    lvl = _level_by_name(doc, s.get("name")) or _level_by_name(doc, s.get("new_name", ""))
                    if not lvl:
                        res.append({"name": s.get("name"), "error": "not found"})
                        continue
                    if "elevation" in s and s["elevation"] is not None:
                        lvl.Elevation = float(s["elevation"])
                    if s.get("new_name"):
                        try:
                            lvl.Name = s["new_name"]
                        except Exception as ne:
                            res.append({"name": s.get("name"), "rename_error": str(ne)})
                    res.append({"name": get_element_name(lvl), "elevation": round(lvl.Elevation, 4)})
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "levels": res})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    # ---------------------------------------------------------------- framing
    @api.route("/place_framing/", methods=["POST"])
    def place_framing(doc, request):
        """
        Batch-place line-based structural framing (beams / joists).
        Body: {
          "level": "2nd Level",
          "members": [
             {"family": "5 W Shapes", "type": "W16x26",
              "x0": 0, "y0": 0, "x1": 20, "y1": 0,
              "z_justification": "top", "z_offset": 0.0}
          ]
        }
        z_justification: top|center|bottom|origin (default top). Coordinates in feet at the level.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            data = _parse(request)
            members = (data or {}).get("members")
            if not members:
                return routes.make_response(data={"error": "No 'members' provided"}, status=400)
            default_level = (data or {}).get("level")
            zmap = {"top": 0, "center": 1, "bottom": 2, "origin": 3}

            t = DB.Transaction(doc, "MCP Place Framing")
            t.Start()
            placed = 0
            errors = []
            try:
                symcache = {}
                for m in members:
                    key = (m["family"], m["type"])
                    sym = symcache.get(key)
                    if sym is None:
                        sym = _find_symbol_by_category(doc, m["family"], m["type"])
                        symcache[key] = sym
                    if not sym:
                        errors.append("symbol not found: {} : {}".format(m["family"], m["type"]))
                        continue
                    if not sym.IsActive:
                        sym.Activate()
                        doc.Regenerate()
                    lvl = _level_by_name(doc, m.get("level", default_level))
                    if not lvl:
                        errors.append("level not found: {}".format(m.get("level", default_level)))
                        continue
                    z = lvl.Elevation
                    p0 = DB.XYZ(float(m["x0"]), float(m["y0"]), z)
                    p1 = DB.XYZ(float(m["x1"]), float(m["y1"]), z)
                    if p0.DistanceTo(p1) < 0.1:
                        errors.append("degenerate member skipped")
                        continue
                    inst = doc.Create.NewFamilyInstance(
                        DB.Line.CreateBound(p0, p1), sym, lvl, ST.Beam)
                    zj = inst.get_Parameter(DB.BuiltInParameter.Z_JUSTIFICATION)
                    if zj and m.get("z_justification"):
                        zj.Set(zmap.get(str(m["z_justification"]).lower(), 0))
                    zo = inst.get_Parameter(DB.BuiltInParameter.Z_OFFSET_VALUE)
                    if zo and m.get("z_offset") is not None:
                        zo.Set(float(m["z_offset"]))
                    placed += 1
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "placed": placed,
                                              "requested": len(members), "errors": errors[:15]})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    # ------------------------------------------------------------ beam system
    @api.route("/create_beam_system/", methods=["POST"])
    def create_beam_system(doc, request):
        """
        Create a structural beam system over a rectangular bay.
        Body: {
          "level": "Roof", "family": "5 K Joist", "type": "16K2",
          "rect": {"xmin": 97.5, "xmax": 158.7, "ymin": 19.8, "ymax": 35.7},
          "direction": {"x": 0, "y": 1},
          "layout": {"rule": "fixed_number", "value": 11},   # or "fixed_distance" + spacing
          "z_justification": "bottom"                        # optional, applied to generated beams
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            data = _parse(request) or {}
            lvl = _level_by_name(doc, data.get("level"))
            if not lvl:
                return routes.make_response(data={"error": "level not found"}, status=404)
            sym = _find_symbol_by_category(doc, data.get("family"), data.get("type"))
            if not sym:
                return routes.make_response(data={"error": "beam type not found"}, status=404)
            r = data.get("rect") or {}
            d = data.get("direction") or {"x": 0, "y": 1}
            lay = data.get("layout") or {"rule": "fixed_number", "value": 2}
            z = lvl.Elevation
            from System.Collections.Generic import List
            pts = [DB.XYZ(r["xmin"], r["ymin"], z), DB.XYZ(r["xmax"], r["ymin"], z),
                   DB.XYZ(r["xmax"], r["ymax"], z), DB.XYZ(r["xmin"], r["ymax"], z)]
            prof = List[DB.Curve]()
            for i in range(4):
                prof.Add(DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]))
            zmap = {"top": 0, "center": 1, "bottom": 2, "origin": 3}

            t = DB.Transaction(doc, "MCP Create Beam System")
            t.Start()
            try:
                if not sym.IsActive:
                    sym.Activate()
                    doc.Regenerate()
                bs = DB.BeamSystem.Create(doc, prof, lvl, DB.XYZ(float(d["x"]), float(d["y"]), 0), False)
                bs.BeamType = sym
                if str(lay.get("rule", "fixed_number")) == "fixed_distance":
                    bs.LayoutRule = DB.LayoutRuleFixedDistance(float(lay["value"]))
                else:
                    bs.LayoutRule = DB.LayoutRuleFixedNumber(int(lay["value"]))
                doc.Regenerate()
                beam_ids = list(bs.GetBeamIds())
                if data.get("z_justification"):
                    jv = zmap.get(str(data["z_justification"]).lower(), 0)
                    for bid in beam_ids:
                        be = doc.GetElement(bid)
                        zj = be.get_Parameter(DB.BuiltInParameter.Z_JUSTIFICATION)
                        if zj:
                            zj.Set(jv)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success",
                                              "beam_system_id": element_id_value(bs.Id),
                                              "beams": len(beam_ids)})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    # ---------------------------------------------------------------- tagging
    @api.route("/tag_all_framing/", methods=["POST"])
    def tag_all_framing(doc, request):
        """
        Tag every structural framing member in a plan view (optionally columns too).
        Body: {
          "view_name": "2 - Level 2",
          "tag_family": "KPFF_Tag_Framing", "tag_type": "Type",
          "level": "2nd Level",          # only tag members on this reference level (optional)
          "include_columns": false,
          "column_tag_family": "KPFF_Tag_Column", "column_tag_type": "Type Name"
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            data = _parse(request) or {}
            view = _view_by_name(doc, data.get("view_name"))
            if not view:
                return routes.make_response(data={"error": "view not found"}, status=404)
            ftag = _find_symbol_by_category(doc, data.get("tag_family"), data.get("tag_type"))
            if not ftag:
                return routes.make_response(data={"error": "framing tag type not found"}, status=404)
            only_level = data.get("level")

            def ref_level_name(e):
                p = e.get_Parameter(DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM)
                if p and p.AsElementId().IntegerValue > 0:
                    return get_element_name(doc.GetElement(p.AsElementId()))
                return None

            framing = list(DB.FilteredElementCollector(doc)
                           .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                           .WhereElementIsNotElementType())
            t = DB.Transaction(doc, "MCP Tag Framing")
            t.Start()
            n = 0
            try:
                if not ftag.IsActive:
                    ftag.Activate()
                    doc.Regenerate()
                for e in framing:
                    if only_level and ref_level_name(e) != only_level:
                        continue
                    try:
                        m = e.Location.Curve.Evaluate(0.5, True)
                        DB.IndependentTag.Create(doc, ftag.Id, view.Id, DB.Reference(e),
                                                 False, DB.TagOrientation.Horizontal,
                                                 DB.XYZ(m.X, m.Y, 0))
                        n += 1
                    except Exception:
                        continue
                nc = 0
                if data.get("include_columns"):
                    ctag = _find_symbol_by_category(doc, data.get("column_tag_family"),
                                                    data.get("column_tag_type"))
                    if ctag:
                        if not ctag.IsActive:
                            ctag.Activate()
                            doc.Regenerate()
                        for c in (DB.FilteredElementCollector(doc)
                                  .OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
                                  .WhereElementIsNotElementType()):
                            try:
                                p = c.Location.Point
                                DB.IndependentTag.Create(doc, ctag.Id, view.Id, DB.Reference(c),
                                                         False, DB.TagOrientation.Horizontal,
                                                         DB.XYZ(p.X + 1.0, p.Y + 1.0, 0))
                                nc += 1
                            except Exception:
                                continue
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "framing_tagged": n,
                                              "columns_tagged": nc})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    # ----------------------------------------------------------------- sheets
    @api.route("/create_sheet/", methods=["POST"])
    def create_sheet(doc, request):
        """
        Create a sheet with a title block and place a view on it.
        Body: {
          "title_block_family": "KPFF Border_24x36", "title_block_type": "24x36",
          "sheet_number": "S2.1", "sheet_name": "SECOND FLOOR FRAMING PLAN",
          "view_name": "2 - Level 2", "x": 1.25, "y": 1.0
        }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            data = _parse(request) or {}
            tb = _find_symbol_by_category(doc, data.get("title_block_family"),
                                          data.get("title_block_type"))
            if not tb:
                return routes.make_response(data={"error": "title block not found"}, status=404)
            t = DB.Transaction(doc, "MCP Create Sheet")
            t.Start()
            try:
                if not tb.IsActive:
                    tb.Activate()
                    doc.Regenerate()
                sheet = DB.ViewSheet.Create(doc, tb.Id)
                if data.get("sheet_number"):
                    sheet.SheetNumber = data["sheet_number"]
                if data.get("sheet_name"):
                    sheet.Name = data["sheet_name"]
                placed_view = None
                view = _view_by_name(doc, data.get("view_name"))
                if view:
                    DB.Viewport.Create(doc, sheet.Id, view.Id,
                                       DB.XYZ(float(data.get("x", 1.25)), float(data.get("y", 1.0)), 0))
                    placed_view = get_element_name(view)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success",
                                              "sheet_id": element_id_value(sheet.Id),
                                              "sheet_number": sheet.SheetNumber,
                                              "sheet_name": get_element_name(sheet),
                                              "view_placed": placed_view})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Structural framing routes registered successfully")
