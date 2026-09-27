# -*- coding: utf-8 -*-
"""Tests for the pure rebar ("Acero") helpers."""
import pytest

from revit_mcp.rebar_spec import (
    SpecError,
    bar_weight_kg_per_m,
    group_runs,
    layout_rectangular_bars,
    parse_diameter,
    parse_distribution,
    parse_longitudinal,
    stirrup_positions,
)


class TestParsing:
    @pytest.mark.parametrize(
        "text, key",
        [('5/8"', '5/8"'), ("Ø3/8", '3/8"'), ("1 3/8 pulg", '1 3/8"'), ("12mm", "12mm"), ("8 mm", "8mm")],
    )
    def test_diameter(self, text, key):
        assert parse_diameter(text) == key

    def test_longitudinal_keeps_count_next_to_symbol(self):
        # "8Ø5/8" must not become "85/8".
        assert parse_longitudinal('8Ø5/8"') == [(8, '5/8"')]
        assert parse_longitudinal("8 5/8") == [(8, '5/8"')]

    def test_longitudinal_mixed_largest_first(self):
        assert parse_longitudinal('4Ø5/8" + 4Ø3/4"') == [(4, '3/4"'), (4, '5/8"')]

    @pytest.mark.parametrize("text", ["", '5Ø5/8"', "3 1/2", "8x7/8"])
    def test_longitudinal_errors(self, text):
        with pytest.raises(SpecError):
            parse_longitudinal(text)

    def test_distribution_meters_and_cm(self):
        assert parse_distribution("1@.05, 10@.10, rto@.20") == ([(1, 0.05), (10, 0.10)], 0.20)
        zones, rest = parse_distribution("1@5, 10@10, R@20")
        assert zones == [(1, pytest.approx(0.05)), (10, pytest.approx(0.10))]
        assert rest == pytest.approx(0.20)

    def test_distribution_needs_rest(self):
        with pytest.raises(SpecError):
            parse_distribution("1@.05, 10@.10")


class TestStirrups:
    def test_symmetric_from_both_ends(self):
        pos = stirrup_positions(2.65, [(1, 0.05), (10, 0.10)], 0.20)
        assert len(pos) == 24
        assert pos[0] == pytest.approx(0.05)
        assert pos[-1] == pytest.approx(2.60)
        for a, b in zip(pos, reversed(pos)):
            assert a + b == pytest.approx(2.65)

    def test_runs_of_constant_spacing(self):
        pos = stirrup_positions(2.65, [(1, 0.05), (10, 0.10)], 0.20)
        runs = group_runs(pos)
        assert sum(n for _, n, _ in runs) == len(pos)
        assert runs[0] == (pytest.approx(0.05), 11, pytest.approx(0.10))


class TestLayout:
    def test_corners_get_the_largest_bars(self):
        bars = layout_rectangular_bars(0.30, 0.90, 0.04, 0.0095, parse_longitudinal('4Ø3/4" + 6Ø5/8"'))
        assert len(bars) == 10
        assert [k for _, _, k in bars[:4]] == ['3/4"'] * 4
        # the 6 extra bars go to the long faces (x = +/-), 3 per face
        assert sum(1 for x, _, k in bars[4:] if x < 0) == 3

    def test_section_too_small(self):
        with pytest.raises(SpecError):
            layout_rectangular_bars(0.08, 0.08, 0.04, 0.0095, parse_longitudinal("4 5/8"))


def test_weight_matches_peruvian_tables():
    assert bar_weight_kg_per_m('3/8"') == pytest.approx(0.56, abs=0.01)
    assert bar_weight_kg_per_m('5/8"') == pytest.approx(1.55, abs=0.01)
    assert bar_weight_kg_per_m('1"') == pytest.approx(3.97, abs=0.01)


class TestSpaceSeparatedDistribution:
    def test_like_the_reference_tool(self):
        zones, rest = parse_distribution("1@5 6@10 Rto@25")
        assert zones == [(1, pytest.approx(0.05)), (6, pytest.approx(0.10))]
        assert rest == pytest.approx(0.25)

    def test_garbage_between_tokens(self):
        with pytest.raises(SpecError):
            parse_distribution("1@5 y 6@10 rto@25")


class TestDrawing:
    def test_auto_design_stirrup_matches_cover(self):
        from revit_mcp.rebar_spec import auto_design, stirrup_centerline

        design = auto_design(0.30, 0.60, 0.04, '3/8"', parse_longitudinal("8 5/8"))
        kind, poly, wrap = design["stirrups"][0]
        assert kind == "borde"
        line = stirrup_centerline(poly, design["bars"], '3/8"', wrap)
        xs = [x for x, _ in line]
        # stirrup centerline sits cover + half the stirrup inside the face
        assert max(xs) == pytest.approx(0.15 - 0.04 - 0.009525 / 2)

    def test_offset_trapezoid(self):
        from revit_mcp.rebar_spec import offset_polygon_outward, polygon_signed_area

        trap = [(-0.3, -0.1), (0.3, -0.1), (0.25, 0.1), (-0.25, 0.1)]
        out = offset_polygon_outward(trap, 0.01)
        assert polygon_signed_area(out) > polygon_signed_area(trap)
        assert len(out) == 4

    def test_tie_wraps_both_bars(self):
        from revit_mcp.rebar_spec import tie_centerline

        bars = [(-0.1, 0.0, '5/8"'), (0.1, 0.0, '5/8"')]
        a, b = tie_centerline((-0.1, 0.0), (0.1, 0.0), bars, '3/8"')
        assert b[0] - a[0] == pytest.approx(0.2 + 0.015875 + 0.009525)

    def test_roundtrip_and_blank(self):
        from revit_mcp.rebar_spec import design_from_text, design_to_text, empty_design

        design = empty_design()
        design["bars"] = [(0.1, -0.2, '3/4"')]
        design["stirrups"] = [("confinamiento", [(0, 0), (0.1, 0), (0.1, 0.1)], 0.008)]
        design["ties"] = [("borde", (0, 0), (0.1, 0.1))]
        again = design_from_text(design_to_text(design))
        assert again["bars"] == [(0.1, -0.2, '3/4"')]
        assert again["stirrups"][0][0] == "confinamiento"
        assert again["stirrups"][0][2] == pytest.approx(0.008)
        assert again["ties"][0][0] == "borde"
        assert design_to_text(empty_design()) == ""
        assert design_from_text("") is None
        with pytest.raises(SpecError):
            design_from_text("{no es json")

    def test_joint_positions(self):
        from revit_mcp.rebar_spec import joint_positions

        assert joint_positions(0.60, 0.10) == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])
        assert joint_positions(0.12, 0.15) == pytest.approx([0.06])


def test_edge_vs_confinement_stirrups():
    from revit_mcp.rebar_spec import is_edge_stirrup

    bars = [(-0.1, -0.3, '5/8"'), (0.1, -0.3, '5/8"'), (0.1, 0.3, '5/8"'), (-0.1, 0.3, '5/8"'),
            (-0.1, 0.0, '5/8"'), (0.1, 0.0, '5/8"')]
    outer = [(-0.1, -0.3), (0.1, -0.3), (0.1, 0.3), (-0.1, 0.3)]
    inner = [(-0.1, -0.3), (0.1, -0.3), (0.1, 0.0), (-0.1, 0.0)]
    assert is_edge_stirrup(outer, bars)
    assert not is_edge_stirrup(inner, bars)


def test_first_format_drawings_still_load():
    from revit_mcp.rebar_spec import design_from_text

    old = ('{"v":1,"bars":[[-0.1,-0.3,"5/8\\""],[0.1,-0.3,"5/8\\""],[0.1,0.3,"5/8\\""],[-0.1,0.3,"5/8\\""]],'
           '"stirrups":[[[-0.1,-0.3],[0.1,-0.3],[0.1,0.3],[-0.1,0.3]]],"ties":[[[-0.1,0],[0.1,0]]]}')
    design = design_from_text(old)
    assert design["stirrups"][0][0] == "borde"  # perimeter one
    assert design["ties"][0][0] == "confinamiento"


class TestAutoTie:
    bars = [(-0.1, -0.35, '5/8"'), (0.1, -0.35, '5/8"'), (0.1, 0.35, '5/8"'), (-0.1, 0.35, '5/8"'),
            (-0.1, -0.117, '5/8"'), (0.1, -0.117, '5/8"'), (-0.1, 0.117, '5/8"'), (0.1, 0.117, '5/8"')]

    def test_click_near_middle_pair(self):
        from revit_mcp.rebar_spec import auto_tie

        a, b = auto_tie((0.02, 0.10), self.bars)
        assert {a, b} == {(-0.1, 0.117), (0.1, 0.117)}

    def test_never_along_a_face(self):
        from revit_mcp.rebar_spec import auto_tie

        # click right on the long face: the 4 bars at x=-0.1 are a face,
        # so the tie still crosses the section
        a, b = auto_tie((-0.1, 0.0), self.bars)
        assert abs(a[1] - b[1]) < 1e-9

    def test_no_facing_bars(self):
        from revit_mcp.rebar_spec import SpecError, auto_tie

        with pytest.raises(SpecError):
            auto_tie((0, 0), [(0, 0, '5/8"'), (0.1, 0.2, '5/8"')])


class TestMeasures:
    section = [(-0.15, -0.40), (0.15, -0.40), (0.15, 0.40), (-0.15, 0.40)]

    def test_perimeter_stirrup_measures_the_cover(self):
        from revit_mcp.rebar_spec import auto_design, rect_measures, stirrup_outline

        design = auto_design(0.30, 0.80, 0.04, '3/8"', parse_longitudinal("8 5/8"))
        kind, poly, wrap = design["stirrups"][0]
        width, height, left, bottom = rect_measures(
            stirrup_outline(poly, design["bars"], '3/8"', wrap), self.section)
        assert (width, height) == (pytest.approx(0.22), pytest.approx(0.72))
        assert (left, bottom) == (pytest.approx(0.04), pytest.approx(0.04))

    def test_edit_roundtrip(self):
        from revit_mcp.rebar_spec import rect_from_measures, rect_measures, stirrup_outline

        pts = rect_from_measures(0.20, 0.30, 0.05, 0.25, self.section, '3/8"', 0.008)
        got = rect_measures(stirrup_outline(pts, [], '3/8"', 0.008), self.section)
        assert got == (pytest.approx(0.20), pytest.approx(0.30), pytest.approx(0.05), pytest.approx(0.25))

    def test_not_a_rectangle(self):
        from revit_mcp.rebar_spec import rect_measures

        assert rect_measures([(0, 0), (1, 0), (0.9, 1), (0.1, 1)], self.section) is None

    def test_too_small(self):
        from revit_mcp.rebar_spec import rect_from_measures

        with pytest.raises(SpecError):
            rect_from_measures(0.02, 0.30, 0.05, 0.05, self.section, '3/8"', 0.008)


class TestCoverFit:
    section = [(-0.15, -0.40), (0.15, -0.40), (0.15, 0.40), (-0.15, 0.40)]

    def test_drawn_sides_land_on_the_cover(self):
        from revit_mcp.rebar_spec import cover_bounds, snap_rect_to_cover

        bounds = cover_bounds(self.section, 0.04)  # (-0.11, -0.36, 0.11, 0.36)
        # left side 1 cm inside the cover line, top 1 cm past it: both snap;
        # the bottom is 40 cm away and stays
        got = snap_rect_to_cover((-0.10, 0.0, 0.05, 0.37), bounds)
        assert got == (pytest.approx(-0.11), 0.0, 0.05, pytest.approx(0.36))

    def test_resize_stays_inside(self):
        from revit_mcp.rebar_spec import cover_bounds, resize_rect_in_cover

        bounds = cover_bounds(self.section, 0.04)
        got = resize_rect_in_cover((-0.11, 0.20, 0.11, 0.36), 0.22, 0.30, bounds)
        assert got == (pytest.approx(-0.11), pytest.approx(0.06), pytest.approx(0.11), pytest.approx(0.36))
        # wider than fits -> clamped to the space inside the cover
        got = resize_rect_in_cover((-0.05, 0.0, 0.05, 0.1), 0.50, 0.10, bounds)
        assert got[2] - got[0] == pytest.approx(0.22)

    def test_vertices_roundtrip(self):
        from revit_mcp.rebar_spec import outer_rect, rect_vertices

        rect = (-0.11, -0.36, 0.11, 0.36)
        pts = rect_vertices(rect, '3/8"', 0.0079375)
        assert outer_rect(pts, [], '3/8"', 0.0079375) == tuple(pytest.approx(v) for v in rect)


class TestCoverLimit:
    section = [(-0.15, -0.40), (0.15, -0.40), (0.15, 0.40), (-0.15, 0.40)]

    def test_corner_past_the_cover_is_refused(self):
        from revit_mcp.rebar_spec import fit_vertex

        # cover 4 cm + 3/8" stirrup: free corners must stay within |x| <= 0.1005
        assert fit_vertex((0.12, 0.0), self.section, 0.04, '3/8"') is None
        assert fit_vertex((0.0, -0.39), self.section, 0.04, '3/8"') is None

    def test_corner_near_the_limit_snaps_onto_it(self):
        from revit_mcp.rebar_spec import fit_vertex

        x, y = fit_vertex((0.09, 0.0), self.section, 0.04, '3/8"')
        assert x == pytest.approx(0.15 - 0.04 - 0.009525) and y == 0.0

    def test_trapezoid_uses_the_real_outline(self):
        from revit_mcp.rebar_spec import fit_vertex

        trap = [(-0.355, -0.125), (0.355, -0.125), (0.385, 0.125), (-0.385, 0.125)]
        assert fit_vertex((0.30, 0.0), trap, 0.04, '3/8"', rectangular=False) == (0.30, 0.0)
        assert fit_vertex((0.34, -0.07), trap, 0.04, '3/8"', rectangular=False) is None

    def test_whole_stirrup_check(self):
        from revit_mcp.rebar_spec import stirrup_inside_cover

        inside = [(-0.10, -0.35), (0.10, -0.35), (0.10, 0.35), (-0.10, 0.35)]
        outside = [(-0.13, -0.35), (0.10, -0.35), (0.10, 0.35), (-0.13, 0.35)]
        assert stirrup_inside_cover(inside, [], '3/8"', 0.0, self.section, 0.04)
        assert not stirrup_inside_cover(outside, [], '3/8"', 0.0, self.section, 0.04)
