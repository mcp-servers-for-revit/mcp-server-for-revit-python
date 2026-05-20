# -*- coding: UTF-8 -*-
"""
Element Operations Module for Revit MCP

A single multi-action endpoint that performs view-state, selection, and
visibility operations on a set of elements. Ported from the Sparx
mcp-servers-for-revit `OperateElementCommand` / `OperateElementEventHandler`.

Supported actions (case-insensitive):

    select           -- set the Revit UI selection to the given elements
    selection_box    -- enable a 3D section box around the given elements
    set_color        -- override projection/cut/surface colour in a view
    set_transparency -- override surface transparency (0-100) in a view
    hide             -- permanently hide elements in a view
    unhide           -- un-hide elements in a view
    temp_hide        -- temporarily hide elements (session-only view mode)
    isolate          -- temporarily isolate elements (session-only view mode)
    reset_isolate    -- clear the temporary hide/isolate view mode
    delete           -- delete elements (confirm-gated; preview unless confirm)

Differences from the Sparx C# original:
  * Revit 2026: ElementId is built via a System.Int64 cast -- the bare int
    ctor `ElementId(int)` is ambiguous/removed on Revit 2026.
  * The temporary view-mode calls (HideElementsTemporary / IsolateElements-
    Temporary / DisableTemporaryViewMode) MUST NOT run inside a transaction
    -- the Revit API forbids it. Sparx wraps all three in a Transaction, so
    those actions throw. This port runs them transaction-free.
  * Returns structured statuses (element_not_found, not_3d_view, no_3d_view,
    no_bounding_box, invalid_color, ...) instead of throwing generic
    exceptions.
  * Per-element id validation -- unresolved ids are collected in `invalid_ids`
    and the operation proceeds on the valid subset instead of NRE-ing.
  * The `delete` action is confirm-gated (Sparx deletes unconditionally).
  * Optional `view_id` targets a specific view instead of always mutating the
    active view; when a 3D view must be activated for `selection_box` the
    switch is reported (`view_switched`, `previous_view_name`).
  * `hide` filters by Element.CanBeHidden(view) so a single un-hideable
    element does not abort the whole batch.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
from System import Int64
from System.Collections.Generic import List as NetList
import json
import logging

logger = logging.getLogger(__name__)


_VALID_ACTIONS = frozenset([
    "select", "selection_box", "set_color", "set_transparency",
    "hide", "unhide", "temp_hide", "isolate", "reset_isolate", "delete",
])
# Actions that do not require an element_ids list.
_NO_ELEMENT_ACTIONS = frozenset(["reset_isolate"])


def _parse_json_request(request):
    if not request or not request.data:
        raise ValueError("No data provided")
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _id_collection(ids):
    """Build a .NET ICollection[ElementId] from an iterable of ElementId."""
    coll = NetList[DB.ElementId]()
    for eid in ids:
        coll.Add(eid)
    return coll


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_color(color):
    return (isinstance(color, (list, tuple)) and len(color) >= 3 and
            all(_is_number(c) for c in color[:3]))


def _ft_to_mm(value):
    return round(DB.UnitUtils.ConvertFromInternalUnits(
        value, DB.UnitTypeId.Millimeters), 2)


def _resolve_view(doc, view_id):
    """Return (view, error) where error is None / 'invalid' / 'not_found'."""
    if view_id is None:
        return doc.ActiveView, None
    try:
        vid = DB.ElementId(Int64(int(view_id)))
    except Exception:
        return None, "invalid"
    view = doc.GetElement(vid)
    if view is None or not isinstance(view, DB.View):
        return None, "not_found"
    return view, None


def _find_solid_fill_pattern(doc):
    """First solid-fill FillPatternElement in the document, or None."""
    try:
        for p in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
            fp = p.GetFillPattern()
            if fp is not None and fp.IsSolidFill:
                return p
    except Exception:
        pass
    return None


def _apply_selection_box(doc, uidoc, valid_elems, elem_ids, invalid_ids, view_id):
    """Enable a 3D section box around the given elements."""
    # Resolve a 3D view to host the section box.
    if view_id is not None:
        view3d, err = _resolve_view(doc, view_id)
        if err == "invalid":
            return routes.make_response(
                data={"error": "view_id must be an integer"}, status=400)
        if view3d is None:
            return routes.make_response(data={
                "status": "view_not_found",
                "error": "No view found for view_id={}.".format(view_id),
            })
        if not isinstance(view3d, DB.View3D):
            return routes.make_response(data={
                "status": "not_3d_view",
                "error": "view_id={} is not a 3D view; selection_box needs a "
                         "3D view.".format(view_id),
            })
    elif isinstance(doc.ActiveView, DB.View3D):
        view3d = doc.ActiveView
    else:
        view3d = None
        for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D):
            if not v.IsTemplate:
                view3d = v
                break
        if view3d is None:
            return routes.make_response(data={
                "status": "no_3d_view",
                "error": "No non-template 3D view exists to host a section box.",
            })

    # Union the element bounding boxes (model coordinates, feet).
    bb_min = None
    bb_max = None
    without_bbox = []
    for (eid, el) in valid_elems:
        eb = el.get_BoundingBox(None)
        if eb is None:
            without_bbox.append(element_id_value(eid))
            continue
        lo = [eb.Min.X, eb.Min.Y, eb.Min.Z]
        hi = [eb.Max.X, eb.Max.Y, eb.Max.Z]
        if bb_min is None:
            bb_min, bb_max = lo, hi
        else:
            bb_min = [min(bb_min[i], lo[i]) for i in range(3)]
            bb_max = [max(bb_max[i], hi[i]) for i in range(3)]

    if bb_min is None:
        return routes.make_response(data={
            "status": "no_bounding_box",
            "error": "None of the supplied elements have a bounding box.",
            "invalid_ids": invalid_ids,
        })

    offset = 1.0  # 1 ft of padding so the box sits slightly proud of the geometry
    box = DB.BoundingBoxXYZ()
    box.Min = DB.XYZ(bb_min[0] - offset, bb_min[1] - offset, bb_min[2] - offset)
    box.Max = DB.XYZ(bb_max[0] + offset, bb_max[1] + offset, bb_max[2] + offset)

    with DB.Transaction(doc, "MCP: Operate Element (selection_box)") as t:
        t.Start()
        view3d.IsSectionBoxActive = True
        view3d.SetSectionBox(box)
        t.Commit()

    # Activate the 3D view if it is not already current, then scroll to the
    # elements. The switch is opt-out-able only by passing an already-active
    # view_id; either way it is reported.
    view_switched = False
    previous_view_name = None
    if element_id_value(doc.ActiveView.Id) != element_id_value(view3d.Id):
        previous_view_name = normalize_string(get_element_name(doc.ActiveView))
        uidoc.ActiveView = view3d
        view_switched = True
    uidoc.ShowElements(_id_collection(elem_ids))

    return routes.make_response(data={
        "status": "success",
        "action": "selection_box",
        "view_id": element_id_value(view3d.Id),
        "view_name": normalize_string(get_element_name(view3d)),
        "view_switched": view_switched,
        "previous_view_name": previous_view_name,
        "section_box_min_mm": [_ft_to_mm(box.Min.X), _ft_to_mm(box.Min.Y), _ft_to_mm(box.Min.Z)],
        "section_box_max_mm": [_ft_to_mm(box.Max.X), _ft_to_mm(box.Max.Y), _ft_to_mm(box.Max.Z)],
        "affected_count": len(elem_ids),
        "affected_ids": [element_id_value(e) for e in elem_ids],
        "elements_without_bbox": without_bbox,
        "invalid_ids": invalid_ids,
    })


def register_element_operations_routes(api):
    """Register the multi-action /operate_element/ endpoint."""

    @api.route("/operate_element/", methods=["POST"])
    def operate_element(doc, request):
        """Run a view-state / selection / visibility operation on elements."""
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            action = str(data.get("action", "")).strip().lower()
            raw_ids = data.get("element_ids") or []
            confirm = bool(data.get("confirm", False))
            view_id = data.get("view_id")

            if action not in _VALID_ACTIONS:
                return routes.make_response(data={
                    "status": "unsupported_action",
                    "error": "Unsupported action '{}'.".format(action),
                    "valid_actions": sorted(_VALID_ACTIONS),
                })

            if not isinstance(raw_ids, list):
                return routes.make_response(data={
                    "error": "element_ids must be a list of integers"}, status=400)

            if not raw_ids and action not in _NO_ELEMENT_ACTIONS:
                return routes.make_response(data={
                    "status": "no_elements",
                    "error": "Action '{}' requires a non-empty element_ids "
                             "list.".format(action),
                })

            uidoc = revit.uidoc
            if not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit UI document"}, status=503)

            # --- Resolve & validate element ids ---
            valid_elems = []   # list of (ElementId, Element)
            invalid_ids = []
            for raw in raw_ids:
                try:
                    eid = DB.ElementId(Int64(int(raw)))
                except Exception:
                    invalid_ids.append(raw)
                    continue
                el = doc.GetElement(eid)
                if el is None:
                    invalid_ids.append(int(raw))
                else:
                    valid_elems.append((eid, el))

            if not valid_elems and action not in _NO_ELEMENT_ACTIONS:
                return routes.make_response(data={
                    "status": "element_not_found",
                    "error": "None of the supplied element_ids resolved to "
                             "elements in the active document.",
                    "invalid_ids": invalid_ids,
                })

            elem_ids = [eid for (eid, _el) in valid_elems]

            # ===== SELECT (no transaction) =====
            if action == "select":
                uidoc.Selection.SetElementIds(_id_collection(elem_ids))
                return routes.make_response(data={
                    "status": "success",
                    "action": "select",
                    "selected_count": len(elem_ids),
                    "selected_ids": [element_id_value(e) for e in elem_ids],
                    "invalid_ids": invalid_ids,
                })

            # ===== SELECTION BOX (3D section box) =====
            if action == "selection_box":
                return _apply_selection_box(
                    doc, uidoc, valid_elems, elem_ids, invalid_ids, view_id)

            # ===== DELETE (confirm-gated) =====
            if action == "delete":
                preview = []
                for (eid, el) in valid_elems:
                    cat = el.Category
                    preview.append({
                        "id": element_id_value(eid),
                        "name": normalize_string(get_element_name(el)),
                        "category": normalize_string(cat.Name) if cat else u"(no category)",
                    })
                if not confirm:
                    return routes.make_response(data={
                        "status": "preview",
                        "action": "delete",
                        "confirm_required": True,
                        "would_delete_count": len(preview),
                        "would_delete": preview,
                        "invalid_ids": invalid_ids,
                        "hint": "Re-call with confirm=true to delete. For "
                                "delete-only work prefer the delete_elements tool.",
                    })
                with DB.Transaction(doc, "MCP: Operate Element (delete)") as t:
                    t.Start()
                    deleted = doc.Delete(_id_collection(elem_ids))
                    t.Commit()
                return routes.make_response(data={
                    "status": "success",
                    "action": "delete",
                    "deleted_count": len(deleted),
                    "deleted_ids": [element_id_value(d) for d in deleted],
                    "invalid_ids": invalid_ids,
                })

            # --- Remaining actions are view-scoped; resolve the target view ---
            target_view, verr = _resolve_view(doc, view_id)
            if verr == "invalid":
                return routes.make_response(
                    data={"error": "view_id must be an integer"}, status=400)
            if target_view is None:
                return routes.make_response(data={
                    "status": "view_not_found",
                    "error": "No view found for view_id={}.".format(view_id),
                })
            view_meta = {
                "view_id": element_id_value(target_view.Id),
                "view_name": normalize_string(get_element_name(target_view)),
            }

            # ===== SET COLOR =====
            if action == "set_color":
                color = data.get("color")
                if not _valid_color(color):
                    return routes.make_response(data={
                        "status": "invalid_color",
                        "error": "set_color requires 'color' as [r, g, b] with "
                                 "values 0-255.",
                    })
                r, g, b = [max(0, min(255, int(c))) for c in color[:3]]
                ogs = DB.OverrideGraphicSettings()
                rev_color = DB.Color(r, g, b)
                ogs.SetProjectionLineColor(rev_color)
                ogs.SetCutLineColor(rev_color)
                ogs.SetSurfaceForegroundPatternColor(rev_color)
                ogs.SetSurfaceBackgroundPatternColor(rev_color)
                solid = _find_solid_fill_pattern(doc)
                if solid is not None:
                    ogs.SetSurfaceForegroundPatternId(solid.Id)
                    ogs.SetSurfaceForegroundPatternVisible(True)
                with DB.Transaction(doc, "MCP: Operate Element (set_color)") as t:
                    t.Start()
                    for eid in elem_ids:
                        target_view.SetElementOverrides(eid, ogs)
                    t.Commit()
                resp = {
                    "status": "success", "action": "set_color",
                    "color": [r, g, b],
                    "affected_count": len(elem_ids),
                    "affected_ids": [element_id_value(e) for e in elem_ids],
                    "invalid_ids": invalid_ids,
                }
                resp.update(view_meta)
                return routes.make_response(data=resp)

            # ===== SET TRANSPARENCY =====
            if action == "set_transparency":
                transparency = data.get("transparency")
                if not _is_number(transparency):
                    return routes.make_response(data={
                        "status": "invalid_transparency",
                        "error": "set_transparency requires 'transparency' as a "
                                 "number 0-100.",
                    })
                tval = max(0, min(100, int(transparency)))
                ogs = DB.OverrideGraphicSettings()
                ogs.SetSurfaceTransparency(tval)
                with DB.Transaction(doc, "MCP: Operate Element (set_transparency)") as t:
                    t.Start()
                    for eid in elem_ids:
                        target_view.SetElementOverrides(eid, ogs)
                    t.Commit()
                resp = {
                    "status": "success", "action": "set_transparency",
                    "transparency": tval,
                    "affected_count": len(elem_ids),
                    "affected_ids": [element_id_value(e) for e in elem_ids],
                    "invalid_ids": invalid_ids,
                }
                resp.update(view_meta)
                return routes.make_response(data=resp)

            # ===== HIDE (permanent) =====
            if action == "hide":
                hideable = []
                skipped = []
                for (eid, el) in valid_elems:
                    try:
                        can_hide = el.CanBeHidden(target_view)
                    except Exception:
                        can_hide = False
                    (hideable if can_hide else skipped).append(eid)
                if not hideable:
                    return routes.make_response(data={
                        "status": "nothing_to_hide",
                        "error": "None of the elements can be hidden in this view.",
                        "skipped_cannot_hide": [element_id_value(e) for e in skipped],
                    })
                with DB.Transaction(doc, "MCP: Operate Element (hide)") as t:
                    t.Start()
                    target_view.HideElements(_id_collection(hideable))
                    t.Commit()
                resp = {
                    "status": "success", "action": "hide",
                    "affected_count": len(hideable),
                    "affected_ids": [element_id_value(e) for e in hideable],
                    "skipped_cannot_hide": [element_id_value(e) for e in skipped],
                    "invalid_ids": invalid_ids,
                }
                resp.update(view_meta)
                return routes.make_response(data=resp)

            # ===== UNHIDE (permanent) =====
            if action == "unhide":
                with DB.Transaction(doc, "MCP: Operate Element (unhide)") as t:
                    t.Start()
                    target_view.UnhideElements(_id_collection(elem_ids))
                    t.Commit()
                resp = {
                    "status": "success", "action": "unhide",
                    "affected_count": len(elem_ids),
                    "affected_ids": [element_id_value(e) for e in elem_ids],
                    "invalid_ids": invalid_ids,
                }
                resp.update(view_meta)
                return routes.make_response(data=resp)

            # ===== TEMPORARY VIEW MODES (no transaction -- API forbids it) =====
            if action == "temp_hide":
                target_view.HideElementsTemporary(_id_collection(elem_ids))
                resp = {
                    "status": "success", "action": "temp_hide",
                    "affected_count": len(elem_ids),
                    "affected_ids": [element_id_value(e) for e in elem_ids],
                    "invalid_ids": invalid_ids,
                    "note": "Temporary -- cleared by action=reset_isolate or "
                            "Revit's temporary view-mode toolbar.",
                }
                resp.update(view_meta)
                return routes.make_response(data=resp)

            if action == "isolate":
                target_view.IsolateElementsTemporary(_id_collection(elem_ids))
                resp = {
                    "status": "success", "action": "isolate",
                    "affected_count": len(elem_ids),
                    "affected_ids": [element_id_value(e) for e in elem_ids],
                    "invalid_ids": invalid_ids,
                    "note": "Temporary -- cleared by action=reset_isolate or "
                            "Revit's temporary view-mode toolbar.",
                }
                resp.update(view_meta)
                return routes.make_response(data=resp)

            if action == "reset_isolate":
                target_view.DisableTemporaryViewMode(
                    DB.TemporaryViewMode.TemporaryHideIsolate)
                resp = {
                    "status": "success", "action": "reset_isolate",
                }
                resp.update(view_meta)
                return routes.make_response(data=resp)

            # Should be unreachable -- every valid action is handled above.
            return routes.make_response(data={
                "status": "unsupported_action",
                "error": "Action '{}' has no handler.".format(action),
            })

        except Exception as e:
            import traceback
            logger.error("operate_element failed: {}".format(traceback.format_exc()))
            return routes.make_response(
                data={"error": str(e), "traceback": traceback.format_exc()},
                status=500)

    logger.info("Element-operations routes registered successfully")
