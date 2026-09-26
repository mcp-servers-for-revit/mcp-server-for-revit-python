# -*- coding: UTF-8 -*-
"""
Pure-Python spatial helpers for formwork generation.

No Revit API imports here, so this module runs both inside Revit's
IronPython 2 engine and under CPython for unit tests. Boxes are plain
6-tuples (min_x, min_y, min_z, max_x, max_y, max_z) and points/vectors
are 3-tuples, which keeps the hot loops free of .NET interop calls.
"""

import math


def bbox_to_tuple(bbox):
    """Convert anything with .Min/.Max XYZ (e.g. BoundingBoxXYZ) to a box
    tuple, or None."""
    if bbox is None:
        return None
    try:
        mn, mx = bbox.Min, bbox.Max
        return (mn.X, mn.Y, mn.Z, mx.X, mx.Y, mx.Z)
    except Exception:
        return None


def boxes_overlap(a, b, tol):
    if a is None or b is None:
        return False
    return (
        a[0] - tol <= b[3]
        and a[3] + tol >= b[0]
        and a[1] - tol <= b[4]
        and a[4] + tol >= b[1]
        and a[2] - tol <= b[5]
        and a[5] + tol >= b[2]
    )


def point_in_box(p, box, tol):
    return (
        box[0] - tol <= p[0] <= box[3] + tol
        and box[1] - tol <= p[1] <= box[4] + tol
        and box[2] - tol <= p[2] <= box[5] + tol
    )


def box_corners(box, tol=0.0):
    xs = (box[0] - tol, box[3] + tol)
    ys = (box[1] - tol, box[4] + tol)
    zs = (box[2] - tol, box[5] + tol)
    return [(x, y, z) for x in xs for y in ys for z in zs]


class SpatialHash(object):
    """Uniform XY grid of box indices, so neighbor lookup is ~O(n)
    instead of comparing every element against every other one."""

    def __init__(self, cell_size):
        self.cell_size = float(cell_size)
        self.cells = {}
        self.boxes = {}

    def _range(self, lo, hi):
        return range(
            int(math.floor(lo / self.cell_size)),
            int(math.floor(hi / self.cell_size)) + 1,
        )

    def insert(self, key, box):
        if box is None:
            return
        self.boxes[key] = box
        for ix in self._range(box[0], box[3]):
            for iy in self._range(box[1], box[4]):
                self.cells.setdefault((ix, iy), []).append(key)

    def query(self, box, tol):
        """Keys whose box overlaps `box` (expanded by `tol`)."""
        if box is None:
            return []
        seen = set()
        result = []
        for ix in self._range(box[0] - tol, box[3] + tol):
            for iy in self._range(box[1] - tol, box[4] + tol):
                for key in self.cells.get((ix, iy), ()):
                    if key in seen:
                        continue
                    seen.add(key)
                    if boxes_overlap(box, self.boxes[key], tol):
                        result.append(key)
        return result


class PlaneFrame(object):
    """Affine UV <-> XYZ map of a planar face: P(u, v) = origin +
    bx * (u - u_mid) + by * (v - v_mid). Exact for planar faces, whose
    parametrization is linear."""

    def __init__(self, origin, bx, by, u_mid, v_mid):
        self.origin = origin
        self.bx = bx
        self.by = by
        self.u_mid = u_mid
        self.v_mid = v_mid
        a = _dot(bx, bx)
        b = _dot(bx, by)
        c = _dot(by, by)
        self._gram = (a, b, c, a * c - b * b)

    def point(self, u, v):
        du = u - self.u_mid
        dv = v - self.v_mid
        o, bx, by = self.origin, self.bx, self.by
        return (
            o[0] + bx[0] * du + by[0] * dv,
            o[1] + bx[1] * du + by[1] * dv,
            o[2] + bx[2] * du + by[2] * dv,
        )

    def project(self, p):
        """UV of the orthogonal projection of `p` onto the plane."""
        a, b, c, det = self._gram
        d = (p[0] - self.origin[0], p[1] - self.origin[1], p[2] - self.origin[2])
        r1 = _dot(d, self.bx)
        r2 = _dot(d, self.by)
        if abs(det) < 1e-12:
            return (self.u_mid, self.v_mid)
        return (
            self.u_mid + (c * r1 - b * r2) / det,
            self.v_mid + (a * r2 - b * r1) / det,
        )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cell_index_range(lo, hi, origin, step, count):
    """Inclusive index range of grid cells [origin + i*step, +step) that
    intersect [lo, hi], clamped to [0, count-1]; None if disjoint."""
    if hi < origin or lo > origin + step * count:
        return None
    i_lo = max(0, int(math.floor((lo - origin) / step)))
    i_hi = min(count - 1, int(math.floor((hi - origin) / step)))
    if i_hi < i_lo:
        return None
    return (i_lo, i_hi)


def candidate_cells(frame, boxes, tol, u0, du, u_steps, v0, dv, v_steps):
    """Grid cells of a face that could possibly touch any of `boxes`:
    {(i, j): [box indices]}. Cells whose centre is not inside a box
    (expanded by `tol`) are dropped, so a neighbor that only grazes the
    face along an edge contributes nothing."""
    cells = {}
    for idx, box in enumerate(boxes):
        uvs = [frame.project(c) for c in box_corners(box, tol)]
        i_range = cell_index_range(
            min(p[0] for p in uvs), max(p[0] for p in uvs), u0, du, u_steps
        )
        j_range = cell_index_range(
            min(p[1] for p in uvs), max(p[1] for p in uvs), v0, dv, v_steps
        )
        if i_range is None or j_range is None:
            continue
        for j in range(j_range[0], j_range[1] + 1):
            v_mid = v0 + (j + 0.5) * dv
            for i in range(i_range[0], i_range[1] + 1):
                u_mid = u0 + (i + 0.5) * du
                if point_in_box(frame.point(u_mid, v_mid), box, tol):
                    cells.setdefault((i, j), []).append(idx)
    return cells
