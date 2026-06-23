# -*- coding: utf-8 -*-
__title__ = "Crop Grids\nTo View"
__doc__ = (
    "Trim grid lines' view-specific extents so they stop exactly at the "
    "active view's crop region boundary.\n\n"
    "Select one or more grids first to only crop those, or run with nothing "
    "selected to crop every grid visible in the active view."
)

from pyrevit import revit, DB, forms

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView


def clip_line_to_polygon(ax, ay, dx, dy, polygon):
    """Intersect the infinite line through (ax, ay) with direction (dx, dy)
    against the polygon's edges. Returns sorted parametric t values.
    """
    t_values = []
    n = len(polygon)
    for i in range(n):
        ex0, ey0 = polygon[i]
        ex1, ey1 = polygon[(i + 1) % n]
        edx = ex1 - ex0
        edy = ey1 - ey0

        denom = dx * edy - dy * edx
        if abs(denom) < 1e-12:
            continue

        rx = ex0 - ax
        ry = ey0 - ay
        s = (dy * rx - dx * ry) / denom
        if s < -1e-9 or s > 1 + 1e-9:
            continue

        t = (edy * rx - edx * ry) / denom
        t_values.append(t)

    return sorted(t_values)


if not view.CropBoxActive:
    forms.alert("The active view has no active crop box/region.", exitscript=True)

mgr = view.GetCropRegionShapeManager()
loops = list(mgr.GetCropShape())
polygons = []
crop_z = None
for loop in loops:
    points = []
    for curve in loop:
        p0 = curve.GetEndPoint(0)
        if crop_z is None:
            crop_z = p0.Z
        points.append((p0.X, p0.Y))
    polygons.append(points)

if not polygons:
    forms.alert("Could not resolve a crop region shape for this view.", exitscript=True)

selected_ids = uidoc.Selection.GetElementIds()
grids = [doc.GetElement(eid) for eid in selected_ids if isinstance(doc.GetElement(eid), DB.Grid)]

if not grids:
    grids = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Grid))

if not grids:
    forms.alert("No grids selected or visible in this view.", exitscript=True)

cropped = 0
errors = []
with revit.Transaction("Crop Grids To View"):
    for grid in grids:
        curve = grid.Curve
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)
        dx = p1.X - p0.X
        dy = p1.Y - p0.Y

        all_t = []
        for polygon in polygons:
            all_t.extend(clip_line_to_polygon(p0.X, p0.Y, dx, dy, polygon))

        if len(all_t) < 2:
            errors.append("Grid {}: does not cross the crop region".format(grid.Name))
            continue

        t_min = min(all_t)
        t_max = max(all_t)

        # Use the Z Revit already expects for this view's in-view curves
        # (not the crop shape's Z), otherwise SetCurveInView rejects the
        # curve as not coincident with the grid's datum plane.
        existing_inview = list(
            grid.GetCurvesInView(DB.DatumExtentType.ViewSpecific, view)
        )
        inview_z = existing_inview[0].GetEndPoint(0).Z if existing_inview else p0.Z

        new_p0 = DB.XYZ(p0.X + t_min * dx, p0.Y + t_min * dy, inview_z)
        new_p1 = DB.XYZ(p0.X + t_max * dx, p0.Y + t_max * dy, inview_z)

        try:
            new_curve = DB.Line.CreateBound(new_p0, new_p1)
            grid.SetCurveInView(DB.DatumExtentType.ViewSpecific, view, new_curve)
            cropped += 1
        except Exception as set_err:
            errors.append("Grid {}: {}".format(grid.Name, set_err))

message = "Cropped {} grid(s) to the view's crop region.".format(cropped)
if errors:
    message += "\n\nErrors:\n" + "\n".join(errors)

forms.alert(message)
