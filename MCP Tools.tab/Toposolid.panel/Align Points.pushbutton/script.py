# -*- coding: utf-8 -*-
__title__ = "Align\nPoints"
__doc__ = (
    "Snap selected Toposolid edit points that are within a tolerance of "
    "selected detail/model lines onto those lines, in plan (X/Y) only. "
    "Point elevation (Z) is never changed.\n\n"
    "Select a Toposolid plus one or more lines, then run this tool."
)

from pyrevit import revit, DB, forms
import math

doc = revit.doc
uidoc = revit.uidoc

DEFAULT_TOLERANCE_MM = "50"
MM_TO_FEET = 1.0 / 304.8
FEET_TO_M = 0.3048
DEFAULT_HEIGHT_TOLERANCE_M = 0.01


def perpendicular_foot_on_segment(px, py, x1, y1, x2, y2):
    """Foot of the perpendicular from (px, py) onto the line through
    (x1,y1)-(x2,y2), in plan. Returns None if that foot falls outside the
    bound segment - the point must NOT be snapped to an endpoint in that case.
    """
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return None
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    if t < 0.0 or t > 1.0:
        return None
    return x1 + t * dx, y1 + t * dy


selected_ids = uidoc.Selection.GetElementIds()
selected_elems = [doc.GetElement(eid) for eid in selected_ids]

toposolids = [e for e in selected_elems if isinstance(e, DB.Toposolid)]
lines = [
    e
    for e in selected_elems
    if isinstance(e, (DB.DetailLine, DB.ModelLine, DB.DetailCurve, DB.ModelCurve))
]

if not toposolids:
    forms.alert("Select a Toposolid (plus one or more lines) first.", exitscript=True)
if not lines:
    forms.alert(
        "Select one or more detail/model lines (plus the Toposolid) first.",
        exitscript=True,
    )

tolerance_str = forms.ask_for_string(
    default=DEFAULT_TOLERANCE_MM,
    prompt="Snap tolerance (mm):",
    title="Align Toposolid Points",
)
if not tolerance_str:
    import sys

    sys.exit()

try:
    tolerance_mm = float(tolerance_str.replace(",", "."))
except ValueError:
    forms.alert("Invalid tolerance value.", exitscript=True)

tolerance_ft = tolerance_mm * MM_TO_FEET

height_str = forms.ask_for_string(
    default="",
    prompt="Only move points at this height in meters (leave blank for any height):",
    title="Align Toposolid Points",
)

target_height_m = None
if height_str and height_str.strip():
    try:
        target_height_m = float(height_str.replace(",", "."))
    except ValueError:
        forms.alert("Invalid height value.", exitscript=True)

segments = []
for ln in lines:
    curve = getattr(ln, "GeometryCurve", None)
    if curve is None:
        loc = ln.Location
        curve = loc.Curve if loc else None
    if curve is None:
        continue
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    segments.append((p0.X, p0.Y, p1.X, p1.Y))

if not segments:
    forms.alert("None of the selected lines have usable geometry.", exitscript=True)

total_moved = 0
with revit.Transaction("Align Toposolid Points to Lines"):
    for topo in toposolids:
        editor = topo.GetSlabShapeEditor()
        if editor is None or not editor.IsEnabled:
            continue

        vertices = list(editor.SlabShapeVertices)
        to_delete = []
        to_add = []

        for v in vertices:
            # Skip the toposolid's boundary - only Interior points are
            # eligible to move, never Corner/Edge vertices.
            if v.VertexType != DB.SlabShapeVertexType.Interior:
                continue

            p = v.Position

            if target_height_m is not None:
                point_height_m = p.Z * FEET_TO_M
                if abs(point_height_m - target_height_m) > DEFAULT_HEIGHT_TOLERANCE_M:
                    continue

            best_dist = tolerance_ft
            best_xy = None
            for (x1, y1, x2, y2) in segments:
                foot = perpendicular_foot_on_segment(p.X, p.Y, x1, y1, x2, y2)
                if foot is None:
                    continue
                cx, cy = foot
                dist = math.hypot(p.X - cx, p.Y - cy)
                if dist <= best_dist:
                    best_dist = dist
                    best_xy = (cx, cy)
            if best_xy is not None:
                to_delete.append(v)
                to_add.append(DB.XYZ(best_xy[0], best_xy[1], p.Z))

        # SlabShapeEditor has no direct "move vertex in plan" call, so move by
        # deleting the old vertex and adding a new one at the snapped X/Y with
        # the original Z preserved.
        for v in to_delete:
            editor.DeletePoint(v)
        for new_point in to_add:
            editor.AddPoint(new_point)

        total_moved += len(to_add)

forms.alert("Moved {} toposolid point(s) onto the selected line(s).".format(total_moved))
