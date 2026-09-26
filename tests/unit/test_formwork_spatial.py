# -*- coding: utf-8 -*-
"""Tests for the pure-Python spatial helpers used by formwork generation."""
import pytest

from revit_mcp.formwork_spatial import (
    PlaneFrame,
    SpatialHash,
    axis_lines,
    boxes_overlap,
    candidate_cells,
    cell_index_range,
    edge_outward_normals,
    offset_polygon,
    point_in_polygons,
)

TOL = 0.016  # ~5 mm in feet


def box(x0, y0, z0, x1, y1, z1):
    return (x0, y0, z0, x1, y1, z1)


class TestBoxesOverlap:
    def test_touching_within_tolerance(self):
        assert boxes_overlap(box(0, 0, 0, 1, 1, 1), box(1.01, 0, 0, 2, 1, 1), TOL)

    def test_separated(self):
        assert not boxes_overlap(box(0, 0, 0, 1, 1, 1), box(1.1, 0, 0, 2, 1, 1), TOL)

    def test_none(self):
        assert not boxes_overlap(None, box(0, 0, 0, 1, 1, 1), TOL)


class TestSpatialHash:
    def test_matches_brute_force(self):
        boxes = [
            box(i * 3.0, j * 2.0, 0, i * 3.0 + 3.5, j * 2.0 + 2.5, 3)
            for i in range(12)
            for j in range(9)
        ] + [box(0, 0, 0, 40, 1, 3)]  # a long wall spanning many buckets
        h = SpatialHash(16.0)
        for k, b in enumerate(boxes):
            h.insert(k, b)
        for k, b in enumerate(boxes):
            expected = {m for m, o in enumerate(boxes) if boxes_overlap(b, o, TOL)}
            assert set(h.query(b, TOL)) == expected

    def test_negative_coordinates(self):
        h = SpatialHash(16.0)
        h.insert("a", box(-20, -20, 0, -17, -17, 3))
        assert h.query(box(-17.01, -18, 0, -15, -16, 3), TOL) == ["a"]

    def test_none_box_ignored(self):
        h = SpatialHash(16.0)
        h.insert("a", None)
        assert h.query(box(0, 0, 0, 1, 1, 1), TOL) == []


class TestPlaneFrame:
    def test_point_project_roundtrip(self):
        # Vertical face in the XZ plane at y=2, u along X, v along Z.
        frame = PlaneFrame((5.0, 2.0, 1.5), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 5.0, 1.5)
        p = frame.point(7.0, 3.0)
        assert p == pytest.approx((7.0, 2.0, 3.0))
        assert frame.project((7.0, 9.0, 3.0)) == pytest.approx((7.0, 3.0))

    def test_non_orthonormal_basis(self):
        frame = PlaneFrame((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 1.0, 0.0), 0.0, 0.0)
        p = frame.point(1.5, -0.5)
        assert frame.project(p) == pytest.approx((1.5, -0.5))


class TestAxisLines:
    def test_uniform(self):
        assert axis_lines(0.0, 1.0, 4) == pytest.approx([0, 0.25, 0.5, 0.75, 1.0])

    def test_breaks_inserted_and_deduped(self):
        lines = axis_lines(0.0, 1.0, 4, [0.6, 0.25000001, -1, 2])
        assert lines == pytest.approx([0, 0.25, 0.5, 0.6, 0.75, 1.0])


class TestCellIndexRange:
    lines = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

    def test_inside(self):
        assert cell_index_range(1.2, 2.7, self.lines) == (2, 5)

    def test_clamped(self):
        assert cell_index_range(-3, 30, self.lines) == (0, 5)

    def test_disjoint(self):
        assert cell_index_range(6, 7, self.lines) is None


class TestCandidateCells:
    # 4 ft x 10 ft column side face in the XZ plane (y = 0), u = X, v = Z.
    frame = PlaneFrame((2.0, 0.0, 5.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 2.0, 5.0)
    u_lines = axis_lines(0.0, 4.0, 16)
    v_lines = axis_lines(0.0, 10.0, 40)

    def test_beam_framing_in_covers_only_its_patch(self):
        beam = box(1.0, -6.0, 8.0, 3.0, 0.0, 10.0)  # 2 ft wide, top 2 ft of face
        cells = candidate_cells(self.frame, [beam], TOL, self.u_lines, self.v_lines)
        assert len(cells) == 8 * 8
        assert all(4 <= i < 12 and 32 <= j < 40 for i, j in cells)

    def test_edge_graze_contributes_nothing(self):
        # Slab whose box only touches the face's top edge (z = 10).
        slab = box(-5.0, -5.0, 10.0, 9.0, 5.0, 10.7)
        assert candidate_cells(self.frame, [slab], TOL, self.u_lines, self.v_lines) == {}

    def test_far_neighbor(self):
        far = box(50, 50, 0, 51, 51, 10)
        assert candidate_cells(self.frame, [far], TOL, self.u_lines, self.v_lines) == {}

    def test_break_lines_make_contact_band_exact(self):
        # Slab 0.656 ft thick against the top of the face: with the slab
        # soffit as a break line the contact band is exactly its thickness.
        slab = box(-5.0, 0.0, 9.344, 9.0, 5.0, 10.0)
        v_lines = axis_lines(0.0, 10.0, 40, [9.344])
        cells = candidate_cells(self.frame, [slab], TOL, self.u_lines, v_lines)
        rows = sorted({j for _, j in cells})
        band = v_lines[rows[-1] + 1] - v_lines[rows[0]]
        assert band == pytest.approx(0.656)


SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]  # CCW


class TestPolygons:
    def test_point_in_polygons_with_hole(self):
        hole = [(0.5, 0.25), (0.5, 0.75), (1.5, 0.75), (1.5, 0.25)]
        assert point_in_polygons((0.2, 0.5), [SQUARE, hole])
        assert not point_in_polygons((1.0, 0.5), [SQUARE, hole])

    def test_outward_normals_ccw_and_cw(self):
        expected = [(0, -1), (1, 0), (0, 1), (-1, 0)]
        assert edge_outward_normals(SQUARE, [SQUARE]) == [pytest.approx(n) for n in expected]
        cw = list(reversed(SQUARE))
        normals = edge_outward_normals(cw, [cw])
        assert normals[0] == pytest.approx((0, 1))  # top edge (0,1)->(2,1)

    def test_outward_normals_of_hole_point_into_hole(self):
        hole = [(0.5, 0.25), (1.5, 0.25), (1.5, 0.75), (0.5, 0.75)]
        normals = edge_outward_normals(hole, [SQUARE, hole])
        assert normals[0] == pytest.approx((0, 1))  # bottom edge of hole -> up into void

    def test_offset_extend_two_sides(self):
        normals = edge_outward_normals(SQUARE, [SQUARE])
        out = offset_polygon(SQUARE, normals, [0.1, 0.0, 0.1, 0.0])
        assert out == [pytest.approx(p) for p in [(0, -0.1), (2, -0.1), (2, 1.1), (0, 1.1)]]

    def test_offset_trim_top(self):
        normals = edge_outward_normals(SQUARE, [SQUARE])
        out = offset_polygon(SQUARE, normals, [0.0, 0.0, -0.06, 0.0])
        assert out == [pytest.approx(p) for p in [(0, 0), (2, 0), (2, 0.94), (0, 0.94)]]

    def test_offset_collinear_step(self):
        poly = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
        normals = edge_outward_normals(poly, [poly])
        out = offset_polygon(poly, normals, [0.1, 0.0, 0.0, 0.0, 0.0])
        assert (1.0, -0.1) in [pytest.approx(p) for p in out]
        assert len(out) == 6


class TestEdgeSplitting:
    def test_split_at_obstacle_extent(self):
        from revit_mcp.formwork_spatial import split_polygon_edges

        # Obstacle spanning x in [0.5, 1.2] above the square's top edge.
        obstacle = [(0.5, 1.0), (1.2, 1.0), (0.5, 2.0), (1.2, 2.0)]
        out = split_polygon_edges(SQUARE, [obstacle])
        # bottom edge (0,0)->(2,0) and top edge (2,1)->(0,1) both get the cuts
        assert (1.2, 1.0) in [pytest.approx(p) for p in out]
        assert (0.5, 0.0) in [pytest.approx(p) for p in out]
        assert len(out) == 8

    def test_partial_trim_makes_step(self):
        from revit_mcp.formwork_spatial import drop_collinear_points, split_polygon_edges

        obstacle = [(0.5, 1.0), (1.2, 1.0), (0.5, 2.0), (1.2, 2.0)]
        split = split_polygon_edges(SQUARE, [obstacle])
        normals = edge_outward_normals(split, [split])
        # trim only the top stretch under the obstacle (edge (1.2,1)->(0.5,1))
        offsets = [0.0] * len(split)
        k = [i for i, p in enumerate(split) if p == pytest.approx((1.2, 1.0))][0]
        offsets[k] = -0.06
        out = drop_collinear_points(offset_polygon(split, normals, offsets))
        assert len(out) == 8  # notch: 4 corners + 4 step points
        assert (1.2, 0.94) in [pytest.approx(p) for p in out]
        assert (0.5, 0.94) in [pytest.approx(p) for p in out]


class TestRays:
    def test_ray_box_2d(self):
        from revit_mcp.formwork_spatial import ray_box_2d

        b = box(10, -1, 0, 12, 1, 3)
        assert ray_box_2d((0, 0), (1, 0), b) == pytest.approx(10)
        assert ray_box_2d((0, 0), (-1, 0), b) is None
        assert ray_box_2d((0, 5), (1, 0), b) is None
        assert ray_box_2d((11, 0), (1, 0), b) == 0.0  # starts inside

    def test_query_ray_sorted_and_height_filtered(self):
        h = SpatialHash(16.0)
        h.insert("near", box(5, -1, 0, 6, 1, 3))
        h.insert("far", box(40, -1, 0, 41, 1, 3))
        h.insert("above", box(20, -1, 10, 21, 1, 12))  # not at ray height
        h.insert("aside", box(20, 5, 0, 21, 6, 3))
        hits = h.query_ray((0, 0), (1, 0), 100, 1.5)
        assert [k for _, k in hits] == ["near", "far"]

    def test_query_ray_respects_length(self):
        h = SpatialHash(16.0)
        h.insert("far", box(40, -1, 0, 41, 1, 3))
        assert h.query_ray((0, 0), (1, 0), 30, 1.5) == []
