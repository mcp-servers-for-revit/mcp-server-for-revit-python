# -*- coding: UTF-8 -*-
"""
Pure-Python spatial helpers for formwork generation.

No Revit API imports here, so this module runs both inside Revit's
IronPython 2 engine and under CPython for unit tests. Boxes are plain
6-tuples (min_x, min_y, min_z, max_x, max_y, max_z) and points/vectors
are 3-tuples, which keeps the hot loops free of .NET interop calls.
"""

import bisect
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


def ray_box_2d(origin, direction, box, tol=0.0):
    """Distance along a 2D ray (plan view) to where it enters `box`
    (expanded by `tol`), 0 if it starts inside, None if it misses."""
    t_min, t_max = 0.0, float("inf")
    for axis, lo, hi in ((0, box[0] - tol, box[3] + tol), (1, box[1] - tol, box[4] + tol)):
        o = origin[axis]
        d = direction[axis]
        if abs(d) < 1e-12:
            if o < lo or o > hi:
                return None
            continue
        t1 = (lo - o) / d
        t2 = (hi - o) / d
        if t1 > t2:
            t1, t2 = t2, t1
        t_min = max(t_min, t1)
        t_max = min(t_max, t2)
        if t_min > t_max:
            return None
    return t_min


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

    def query_ray(self, origin, direction, length, z, tol=0.0):
        """Keys whose box is crossed by the horizontal ray from `origin`
        (x, y) along unit `direction` (dx, dy) for `length`, at height
        `z`. Returns (distance, key) pairs sorted by distance."""
        seen = set()
        hits = []
        step = self.cell_size / 4.0
        n_steps = int(length / step) + 2
        for k in range(n_steps):
            x = origin[0] + direction[0] * step * k
            y = origin[1] + direction[1] * step * k
            cell = (
                int(math.floor(x / self.cell_size)),
                int(math.floor(y / self.cell_size)),
            )
            for key in self.cells.get(cell, ()):
                if key in seen:
                    continue
                seen.add(key)
                box = self.boxes[key]
                if not (box[2] - tol <= z <= box[5] + tol):
                    continue
                d = ray_box_2d(origin, direction, box, tol)
                if d is not None and d <= length:
                    hits.append((d, key))
        hits.sort(key=lambda h: h[0])
        return hits

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


def box_uv_range(frame, box, tol=0.0):
    """(u_min, u_max, v_min, v_max) of a box projected onto a face plane."""
    uvs = [frame.project(c) for c in box_corners(box, tol)]
    return (
        min(p[0] for p in uvs),
        max(p[0] for p in uvs),
        min(p[1] for p in uvs),
        max(p[1] for p in uvs),
    )


def axis_lines(lo, hi, steps, breaks=(), eps=1e-4):
    """Sorted grid lines over [lo, hi]: `steps` uniform divisions plus any
    `breaks` falling strictly inside, so cell edges land exactly on
    neighbor boundaries instead of being rounded to the uniform grid.
    Lines closer than `eps` are merged (endpoints always kept)."""
    raw = [lo + (hi - lo) * k / float(steps) for k in range(1, steps)]
    raw.extend(b for b in breaks if lo + eps < b < hi - eps)
    raw.sort()
    lines = [lo]
    for x in raw:
        if x - lines[-1] > eps:
            lines.append(x)
    if hi - lines[-1] <= eps:
        lines[-1] = hi
    else:
        lines.append(hi)
    return lines


def cell_index_range(lo, hi, lines):
    """Inclusive index range of the cells [lines[k], lines[k+1]] that
    intersect [lo, hi]; None if disjoint."""
    count = len(lines) - 1
    if count < 1 or hi < lines[0] or lo > lines[-1]:
        return None
    i_lo = max(0, bisect.bisect_right(lines, lo) - 1)
    i_hi = min(count - 1, bisect.bisect_left(lines, hi) - 1)
    if i_hi < i_lo:
        i_hi = i_lo
    return (min(i_lo, count - 1), i_hi)


def candidate_cells(frame, boxes, tol, u_lines, v_lines):
    """Grid cells of a face that could possibly touch any of `boxes`:
    {(i, j): [box indices]}. Cells whose centre is not inside a box
    (expanded by `tol`) are dropped, so a neighbor that only grazes the
    face along an edge contributes nothing."""
    cells = {}
    for idx, box in enumerate(boxes):
        u_min, u_max, v_min, v_max = box_uv_range(frame, box, tol)
        i_range = cell_index_range(u_min, u_max, u_lines)
        j_range = cell_index_range(v_min, v_max, v_lines)
        if i_range is None or j_range is None:
            continue
        for j in range(j_range[0], j_range[1] + 1):
            v_mid = (v_lines[j] + v_lines[j + 1]) / 2.0
            for i in range(i_range[0], i_range[1] + 1):
                u_mid = (u_lines[i] + u_lines[i + 1]) / 2.0
                if point_in_box(frame.point(u_mid, v_mid), box, tol):
                    cells.setdefault((i, j), []).append(idx)
    return cells


def point_in_polygons(pt, polygons):
    """Even-odd test against several 2D polygons (outer loop + holes)."""
    x, y = pt
    inside = False
    for poly in polygons:
        n = len(poly)
        for k in range(n):
            x1, y1 = poly[k]
            x2, y2 = poly[(k + 1) % n]
            if (y1 > y) != (y2 > y):
                if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
    return inside


def edge_outward_normals(polygon, polygons, eps=1e-3):
    """Unit 2D normal per edge of `polygon` pointing away from the region
    described by `polygons` (even-odd), so it works for holes too."""
    normals = []
    n = len(polygon)
    for k in range(n):
        x1, y1 = polygon[k]
        x2, y2 = polygon[(k + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-12:
            normals.append((0.0, 0.0))
            continue
        left = (-dy / length, dx / length)
        mid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        probe = (mid[0] + left[0] * eps, mid[1] + left[1] * eps)
        if point_in_polygons(probe, polygons):
            normals.append((-left[0], -left[1]))
        else:
            normals.append(left)
    return normals


def split_polygon_edges(polygon, obstacles, eps=1e-3):
    """Insert extra vertices along each edge wherever an obstacle's 2D
    extent (given as its corner points) starts or ends along that edge,
    so per-edge rules can differ along one straight edge."""
    result = []
    n = len(polygon)
    for k in range(n):
        a = polygon[k]
        b = polygon[(k + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        result.append(a)
        if length < 2 * eps:
            continue
        ux, uy = dx / length, dy / length
        cuts = set()
        for corners in obstacles:
            s_vals = [(c[0] - a[0]) * ux + (c[1] - a[1]) * uy for c in corners]
            for s in (min(s_vals), max(s_vals)):
                if eps < s < length - eps:
                    cuts.add(round(s, 6))
        for s in sorted(cuts):
            result.append((a[0] + ux * s, a[1] + uy * s))
    return result


def drop_collinear_points(polygon, eps=1e-9):
    """Remove vertices lying on a straight line between their neighbors
    (and duplicates), keeping the polygon's shape and orientation."""
    pts = []
    for p in polygon:
        if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-7:
            pts.append(p)
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 1e-7:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for k in range(len(pts)):
            a = pts[k - 1]
            b = pts[k]
            c = pts[(k + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cross) <= eps * max(1.0, math.hypot(c[0] - a[0], c[1] - a[1])):
                pts.pop(k)
                changed = True
                break
    return pts


def offset_polygon(polygon, normals, offsets):
    """Move each edge k of `polygon` by offsets[k] along normals[k]
    (positive = outward) and rebuild the corners from the shifted edge
    lines. Vertex order, and so orientation, is preserved; collinear
    neighbors with different offsets get a small step."""
    n = len(polygon)
    result = []
    for k in range(n):
        prev = (k - 1) % n
        pa = polygon[prev]
        pb = polygon[k]
        pc = polygon[(k + 1) % n]
        na, oa = normals[prev], offsets[prev]
        nb, ob = normals[k], offsets[k]
        d1 = (pb[0] - pa[0], pb[1] - pa[1])
        d2 = (pc[0] - pb[0], pc[1] - pb[1])
        p1 = (pb[0] + na[0] * oa, pb[1] + na[1] * oa)
        p2 = (pb[0] + nb[0] * ob, pb[1] + nb[1] * ob)
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cross) < 1e-9 * (math.hypot(*d1) * math.hypot(*d2) + 1e-12):
            result.append(p1)
            if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) > 1e-9:
                result.append(p2)
            continue
        w = (p2[0] - p1[0], p2[1] - p1[1])
        a = (w[0] * d2[1] - w[1] * d2[0]) / cross
        result.append((p1[0] + d1[0] * a, p1[1] + d1[1] * a))
    return result
