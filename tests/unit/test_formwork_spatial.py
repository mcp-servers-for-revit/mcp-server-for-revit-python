# -*- coding: utf-8 -*-
"""Tests for the pure-Python spatial helpers used by formwork generation."""
import pytest

from revit_mcp.formwork_spatial import (
    PlaneFrame,
    SpatialHash,
    boxes_overlap,
    candidate_cells,
    cell_index_range,
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


class TestCellIndexRange:
    def test_inside(self):
        assert cell_index_range(1.2, 2.7, 0.0, 0.5, 10) == (2, 5)

    def test_clamped(self):
        assert cell_index_range(-3, 30, 0.0, 0.5, 10) == (0, 9)

    def test_disjoint(self):
        assert cell_index_range(6, 7, 0.0, 0.5, 10) is None


class TestCandidateCells:
    # 4 ft x 10 ft column side face in the XZ plane (y = 0), u = X, v = Z.
    frame = PlaneFrame((2.0, 0.0, 5.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 2.0, 5.0)
    grid = dict(u0=0.0, du=0.25, u_steps=16, v0=0.0, dv=0.25, v_steps=40)

    def test_beam_framing_in_covers_only_its_patch(self):
        beam = box(1.0, -6.0, 8.0, 3.0, 0.0, 10.0)  # 2 ft wide, top 2 ft of face
        cells = candidate_cells(self.frame, [beam], TOL, **self.grid)
        assert len(cells) == 8 * 8
        assert all(4 <= i < 12 and 32 <= j < 40 for i, j in cells)

    def test_edge_graze_contributes_nothing(self):
        # Slab whose box only touches the face's top edge (z = 10).
        slab = box(-5.0, -5.0, 10.0, 9.0, 5.0, 10.7)
        assert candidate_cells(self.frame, [slab], TOL, **self.grid) == {}

    def test_far_neighbor(self):
        far = box(50, 50, 0, 51, 51, 10)
        assert candidate_cells(self.frame, [far], TOL, **self.grid) == {}
