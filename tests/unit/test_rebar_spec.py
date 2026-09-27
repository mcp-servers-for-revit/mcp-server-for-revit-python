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
    """The stirrup distribution rules agreed with the user
    (OL-STR.tab/Structural.panel/Acero.pushbutton/REGLAS_ACERO.md): if one
    of these fails, the rule was broken - fix the code, not the test."""

    def test_both_ends_mirror_each_other(self):
        pos = stirrup_positions(2.65, [(1, 0.05), (10, 0.10)], 0.20)
        assert len(pos) == 24
        assert pos[0] == pytest.approx(0.05)
        assert pos[-1] == pytest.approx(2.60)
        for a, b in zip(pos, reversed(pos)):
            assert a + b == pytest.approx(2.65)

    def test_one_set_per_zone_and_end_as_written(self):
        from revit_mcp.rebar_spec import stirrup_sets

        sets = stirrup_sets(4.0, [(1, 0.05), (5, 0.10)], 0.20)
        expected = [
            (0.05, 1, 0.0, 0, 1),     # 1@0.05: a single stirrup
            (0.15, 5, 0.10, 1, 1),    # 5@0.10 counted from it
            (0.75, 7, 0.20, 2, 1),    # rto@0.20 up from the last of 5@0.10 (0.75 ... 1.95)
            (2.05, 7, 0.20, 2, -1),   # rto@0.20 down from the top 5@0.10 (3.25 ... 2.05)
            (3.45, 5, 0.10, 1, -1),   # 5@0.10 at the top (3.45 ... 3.85)
            (3.95, 1, 0.0, 0, -1),    # 1@0.05 under the beam
        ]
        assert len(sets) == len(expected)
        for got, want in zip(sets, expected):
            assert got[:3] == pytest.approx(want[:3]) and got[3:] == want[3:]

    @pytest.mark.parametrize("length", [4.0, 2.5, 3.25, 2.4, 4.65, 3.4, 2.1, 1.95, 2.05])
    def test_each_end_as_written_and_no_gap_over_the_rest(self, length):
        from revit_mcp.rebar_spec import STIRRUP_MERGE_GAP

        zones, rest = [(1, 0.05), (5, 0.10)], 0.20
        pos = stirrup_positions(length, zones, rest)
        ends = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55]
        assert pos[:6] == pytest.approx(ends)  # from the bottom
        assert [length - p for p in reversed(pos)][:6] == pytest.approx(ends)  # from the top
        gaps = [b - a for a, b in zip(pos, pos[1:])]
        assert max(gaps) <= rest + STIRRUP_MERGE_GAP + 1e-9
        assert min(gaps) >= STIRRUP_MERGE_GAP - 1e-9  # two stirrups never on the same spot
        # the rest runs every 0.20 from the last stirrup of 5@0.10 of each
        # end (a stirrup right at the middle only fills the leftover)
        for p in pos:
            d = min(p, length - p)  # from the nearer end
            if d > 0.55 + 1e-6 and abs(p - length / 2.0) > 1e-6:
                k = (d - 0.55) / 0.20
                assert k == pytest.approx(round(k))

    def test_short_column_zones_stop_at_the_middle(self):
        pos = stirrup_positions(0.9, [(1, 0.05), (5, 0.10)], 0.20)
        assert pos == pytest.approx([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85])

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


def test_weight_table_matches_the_supplier_table():
    from revit_mcp.rebar_spec import read_weight_table

    table = read_weight_table()
    expected = {  # key: (area mm2, nominal kg/m, minimum kg/m)
        "6mm": (28, 0.222, 0.207), "8mm": (50, 0.395, 0.371), '3/8"': (71, 0.56, 0.526),
        "12mm": (113, 0.888, 0.835), '1/2"': (129, 0.994, 0.934), '5/8"': (199, 1.552, 1.459),
        '3/4"': (284, 2.235, 2.101), '1"': (510, 3.973, 3.735), '1 3/8"': (1006, 7.907, 7.433),
    }
    assert set(table) == set(expected)
    for key, (area, nominal, minimum) in expected.items():
        assert (table[key]["area_mm2"], table[key]["nominal"], table[key]["minimum"]) == (area, nominal, minimum)
        assert bar_weight_kg_per_m(key) == nominal
    # not in the table: steel density times the area
    assert bar_weight_kg_per_m('1/4"') == pytest.approx(0.249, abs=0.001)


def test_a_diameter_added_to_the_table_becomes_known(tmp_path, monkeypatch):
    import revit_mcp.rebar_spec as rs

    monkeypatch.setattr(rs, "BAR_DIAMETERS_MM", dict(rs.BAR_DIAMETERS_MM))
    table_file = tmp_path / "pesos.csv"
    table_file.write_text(u'DIAMETRO;AREA;NOMINAL;MINIMO\n7/8";387;3.04;2.86\n', encoding="utf-8")
    table = rs.read_weight_table(str(table_file))
    assert table['7/8"']["nominal"] == 3.04
    assert rs.parse_diameter(u"Ø7/8") == '7/8"'
    assert rs.BAR_DIAMETERS_MM['7/8"'] == pytest.approx(22.2, abs=0.05)
    assert '7/8"' in rs.bar_diameter_keys()


@pytest.mark.parametrize(
    "name, key",
    [
        (u'SRB_ACERO DE REFUERZO FY=4200 KG/CM2_Ø5/8"_ZAPATA_Z-1', '5/8"'),
        (u"Ø12mm_COLUMNA C-8", "12mm"),
        (u'SRB_..._Ø1/4"_LOSA ALIGERADA_B5', '1/4"'),
        (u'Ø1 3/8"_COLUMNA', '1 3/8"'),
        (u"13M", None),
        (u"Armadura estructural 1", None),
    ],
)
def test_diameter_from_bar_type_name(name, key):
    from revit_mcp.rebar_spec import diameter_from_name

    assert diameter_from_name(name) == key


def test_stack_lifts_puts_stirrups_side_by_side():
    from revit_mcp.rebar_spec import stack_lifts

    d = 0.009525
    # drawn: a confinement tie, two edge stirrups, a confinement stirrup
    items = [("confinamiento", d, True), ("borde", d, False), ("borde", d, False),
             ("confinamiento", 0.008, False)]
    assert stack_lifts(items) == pytest.approx([2 * d + 0.008, 0.0, d, 2 * d])
    assert stack_lifts([("borde", d, False)]) == [0.0]
    assert stack_lifts([]) == []


class TestPlaceBar:
    # a 35 x 80 column, edge stirrup 3/8" on a 4 cm cover: outer face at
    # x = +-0.135, y = +-0.36; a 3/4" bar sits at 0.135 - 0.0095 - 0.0095
    outline = [(-0.135, -0.36), (0.135, -0.36), (0.135, 0.36), (-0.135, 0.36)]
    stirrups = [(outline, '3/8"')]
    edge = 0.135 - 0.009525 - 0.009525

    def test_sits_against_the_face_and_in_the_corner(self):
        from revit_mcp.rebar_spec import place_bar

        x, y = place_bar((0.07, 0.10), '3/4"', [], self.stirrups)  # 4.6 cm off the face
        assert x == pytest.approx(self.edge) and y == pytest.approx(0.10)
        x, y = place_bar((0.10, 0.31), '3/4"', [], self.stirrups)  # near the corner
        assert (x, y) == pytest.approx((self.edge, 0.36 - 0.009525 - 0.009525))

    def test_lines_up_with_the_facing_bar(self):
        from revit_mcp.rebar_spec import place_bar

        bars = [(-self.edge, -0.12, '3/4"')]
        x, y = place_bar((0.11, -0.135), '3/4"', bars, self.stirrups)
        assert x == pytest.approx(self.edge) and y == pytest.approx(-0.12)

    def test_far_from_stirrups_only_lines_up(self):
        from revit_mcp.rebar_spec import place_bar

        bars = [(0.0, 0.2, '5/8"')]
        assert place_bar((0.01, -0.05), '5/8"', bars, self.stirrups) == pytest.approx((0.0, -0.05))


def test_nearest_diameter():
    from revit_mcp.rebar_spec import nearest_diameter

    assert nearest_diameter(9.5) == '3/8"'
    assert nearest_diameter(6.4) == '1/4"'
    assert nearest_diameter(35.8) == '1 3/8"'
    assert nearest_diameter(22.2) is None


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
        kind, poly, wrap, is_open = design["stirrups"][0]
        assert kind == "borde" and not is_open
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
        design["stirrups"] = [("confinamiento", [(0, 0), (0.1, 0), (0.1, 0.1)], 0.008, True)]
        design["ties"] = [("borde", (0, 0), (0.1, 0.1))]
        again = design_from_text(design_to_text(design))
        assert again["bars"] == [(0.1, -0.2, '3/4"')]
        assert again["stirrups"][0][0] == "confinamiento"
        assert again["stirrups"][0][2] == pytest.approx(0.008)
        assert again["stirrups"][0][3] is True
        assert again["ties"][0][0] == "borde"
        assert design_to_text(empty_design()) == ""
        assert design_from_text("") is None
        with pytest.raises(SpecError):
            design_from_text("{no es json")

    def test_shapes_follow_their_stirrups_and_ties(self):
        from revit_mcp.rebar_spec import (
            add_item, design_from_text, design_shapes, design_to_text, empty_design, remove_item)

        design = empty_design()
        square = [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)]
        add_item(design, "stirrups", ("borde", square, 0.008, False), "M_T1")
        add_item(design, "stirrups", ("confinamiento", square[:3], 0.0, True))
        add_item(design, "stirrups", ("confinamiento", square, 0.0, False), "M_T1")
        add_item(design, "ties", ("confinamiento", (0, 0), (0.1, 0)), "M_02")
        remove_item(design, "stirrups", 0)
        assert design_shapes(design, "stirrups") == [None, "M_T1"]
        again = design_from_text(design_to_text(design))
        assert design_shapes(again, "stirrups") == [None, "M_T1"]
        assert design_shapes(again, "ties") == ["M_02"]
        # a design without shapes (older drawings, auto_design) reads as None
        old = empty_design()
        old["stirrups"] = [("borde", square, 0.008, False)]
        assert design_shapes(old, "stirrups") == [None]

    def test_config_file_roundtrip(self):
        from revit_mcp.rebar_spec import (
            add_item, config_file_text, design_shapes, empty_design, read_config_file)

        design = empty_design()
        design["bars"] = [(-0.09, -0.34, '5/8"'), (0.09, 0.34, '5/8"')]
        add_item(design, "stirrups", ("borde", [(-0.09, -0.34), (0.09, -0.34), (0.09, 0.34)], 0.008, False), "M_T1")
        form = {"conf": '3/8"', "conf_dist": "", "edge": '3/8"', "edge_dist": "1@0.05, 5@0.10, rto@0.20",
                "cover": "4.0", "nucleo": "10"}
        text = config_file_text(u"C-3_0.25x0.80m", (25.0, 80.0), form, design)
        name, size, form2, design2 = read_config_file(text)
        assert name == u"C-3_0.25x0.80m"
        assert size == (25.0, 80.0)
        assert form2 == form
        assert design2["bars"] == design["bars"]
        assert design_shapes(design2, "stirrups") == ["M_T1"]
        # a form without drawing reads with design None
        assert read_config_file(config_file_text(u"X", (20, 60), form, empty_design()))[3] is None
        for bad in ("{no es json", '{"otra": 1}', "[1, 2]"):
            with pytest.raises(SpecError):
                read_config_file(bad)

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
        kind, poly, wrap, _ = design["stirrups"][0]
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


class TestOpenStirrup:
    # U drawn through three bars' centers, open to the left (like a bracket)
    u = [(-0.10, 0.30), (0.10, 0.30), (0.10, -0.30), (-0.10, -0.30)]

    def test_offset_goes_away_from_the_inside(self):
        from revit_mcp.rebar_spec import offset_polyline_outward

        out = offset_polyline_outward(self.u, 0.01)
        assert out[0] == pytest.approx((-0.10, 0.31))      # top leg moved up
        assert out[1] == pytest.approx((0.11, 0.31))       # corner mitered out
        assert out[2] == pytest.approx((0.11, -0.31))
        assert out[3] == pytest.approx((-0.10, -0.31))     # bottom leg moved down

    def test_open_stirrup_roundtrip_and_measures(self):
        from revit_mcp.rebar_spec import design_from_text, design_to_text, empty_design, side_lengths, stirrup_outline

        design = empty_design()
        design["stirrups"] = [("confinamiento", self.u, 0.0, True)]
        again = design_from_text(design_to_text(design))
        kind, poly, wrap, is_open = again["stirrups"][0]
        assert is_open
        sides = side_lengths(stirrup_outline(poly, [], '3/8"', wrap, is_open), closed=False)
        assert len(sides) == 3

    def test_needs_two_points(self):
        from revit_mcp.rebar_spec import offset_polyline_outward

        with pytest.raises(SpecError):
            offset_polyline_outward([(0, 0)], 0.01)


def test_clean_polyline():
    from revit_mcp.rebar_spec import clean_polyline

    straight = [(0, 0), (0, 0.1), (0, 0.1), (0, 0.2), (0.1, 0.2)]
    assert clean_polyline(straight, closed=False) == [(0, 0), (0, 0.2), (0.1, 0.2)]
    square = [(0, 0), (0.05, 0), (0.1, 0), (0.1, 0.1), (0, 0.1), (0, 0)]
    assert clean_polyline(square, closed=True) == [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)]


class TestResizeSegment:
    # bracket: leg, chamfer, back, chamfer, leg (like the user's drawing)
    pts = [(0.03, 0.30), (-0.01, 0.30), (-0.05, 0.26), (-0.05, -0.26), (-0.01, -0.30), (0.03, -0.30)]

    def test_outer_measure_changes_by_the_same_amount(self):
        from revit_mcp.rebar_spec import resize_segment, side_lengths, stirrup_outline

        before = side_lengths(stirrup_outline(self.pts, [], '3/8"', 0.0, True), closed=False)
        new = resize_segment(self.pts, 2, 0.10)  # back 10 cm longer
        after = side_lengths(stirrup_outline(new, [], '3/8"', 0.0, True), closed=False)
        assert after[2] == pytest.approx(before[2] + 0.10)
        for k in (0, 1, 3, 4):  # the others keep their size (angles kept)
            assert after[k] == pytest.approx(before[k])

    def test_too_short(self):
        from revit_mcp.rebar_spec import resize_segment

        with pytest.raises(SpecError):
            resize_segment(self.pts, 0, -0.05)


def test_legs_grow_from_their_free_end():
    from revit_mcp.rebar_spec import resize_segment

    pts = TestResizeSegment.pts
    first = resize_segment(pts, 0, 0.05)
    assert first[0] == pytest.approx((0.08, 0.30)) and first[1:] == pts[1:]
    last = resize_segment(pts, len(pts) - 2, 0.05)
    assert last[:-1] == pts[:-1] and last[-1] == pytest.approx((0.08, -0.30))


class TestShapes:
    def test_closed_stirrup_with_hooks(self):
        from revit_mcp.rebar_spec import shape_outline

        # hook, 4 sides (last overlapping the start corner), hook
        lines = [((0.3, 0.9), (0.1, 1.0)), ((0.0, 1.0), (0.0, 0.0)), ((0.0, 0.0), (1.0, 0.0)),
                 ((1.0, 0.0), (1.0, 1.0)), ((1.0, 1.0), (0.05, 1.0)), ((0.05, 1.0), (0.2, 0.85))]
        vertices, closed = shape_outline(lines, True, True)
        assert closed and len(vertices) == 4

    def test_open_bracket(self):
        from revit_mcp.rebar_spec import shape_outline

        lines = [((1.0, 1.0), (0.2, 1.0)), ((0.0, 0.8), (0.0, 0.2)), ((0.2, 0.0), (1.0, 0.0))]
        vertices, closed = shape_outline(lines)
        assert not closed
        assert vertices == [(1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0)]

    def test_fit_to_box(self):
        from revit_mcp.rebar_spec import fit_polyline_to_box

        pts = fit_polyline_to_box([(0, 0), (2, 0), (2, 1)], (-0.1, -0.3, 0.1, 0.3))
        assert pts == [(-0.1, -0.3), (0.1, -0.3), (0.1, 0.3)]
        straight = fit_polyline_to_box([(0, 0), (0, 5)], (-0.1, -0.3, 0.1, 0.3))
        assert straight[0][0] == 0.0 and straight[1][1] == 0.3


def test_open_shape_keeps_its_legs():
    from revit_mcp.rebar_spec import shape_outline

    # a U with short legs up, even if Revit reports hooks at its ends
    lines = [((0.0, 0.3), (0.0, 0.0)), ((0.0, 0.0), (1.0, 0.0)), ((1.0, 0.0), (1.0, 0.3))]
    vertices, closed = shape_outline(lines, True, True)
    assert not closed and len(vertices) == 4


def test_closed_stirrup_whose_hooks_are_not_straight_segments():
    from revit_mcp.rebar_spec import shape_outline

    # M_T1 in Revit's browser: just its 4 sides (hooks drawn as arcs)
    lines = [((0.0, 1.0), (0.0, 0.0)), ((0.0, 0.0), (1.0, 0.0)),
             ((1.0, 0.0), (1.0, 1.0)), ((1.0, 1.0), (0.0, 1.0))]
    vertices, closed = shape_outline(lines, True, True)
    assert closed and len(vertices) == 4


def test_m_t1_corner_is_rebuilt():
    from revit_mcp.rebar_spec import shape_outline

    # M_T1 exactly as Revit's browser returns it (gap at the hook corner)
    lines = [((0.0, -0.025), (-3.912, -0.025)), ((-3.912, -0.025), (-3.912, -3.912)),
             ((-3.912, -3.912), (-0.025, -3.912)), ((-0.025, -3.912), (-0.025, 0.0))]
    vertices, closed = shape_outline(lines, True, True)
    assert closed
    assert sorted(set(round(x, 4) for x, _ in vertices)) == [-3.912, -0.025]
    assert sorted(set(round(y, 4) for _, y in vertices)) == [-3.912, -0.025]


def test_zone_positions_match_plain_positions():
    from revit_mcp.rebar_spec import stirrup_positions, stirrup_zone_positions

    for length in (2.65, 3.25, 0.9, 0.3):
        zones, rest = [(1, 0.05), (6, 0.10)], 0.20
        tagged = stirrup_zone_positions(length, zones, rest)
        assert [p for p, _ in tagged] == pytest.approx(stirrup_positions(length, zones, rest))
    tagged = stirrup_zone_positions(2.65, [(1, 0.05), (6, 0.10)], 0.20)
    assert tagged[0][1] == 0 and tagged[1][1] == 1 and tagged[-1][1] == 0
    assert any(zone == 2 for _, zone in tagged)


class TestCustomBars:
    inner = (-0.1005, -0.3505, 0.1005, 0.3505)  # 30x80, cover 4 cm, 3/8" stirrup

    def test_mixed_diameters_all_touch_the_stirrup(self):
        from revit_mcp.rebar_spec import custom_bar_layout

        bars = custom_bar_layout(self.inner, '3/4"', (1, '5/8"'), (2, '5/8"'))
        assert len(bars) == 4 + 2 + 4
        assert bars[0] == (pytest.approx(-0.1005 + 0.009525), pytest.approx(-0.3505 + 0.009525), '3/4"')
        face_y = [b for b in bars[4:] if b[0] < 0]
        for x, y, key in face_y:  # left face bars: their own radius off the stirrup
            assert x == pytest.approx(-0.1005 + 0.0079375)

    def test_seat_on_a_side_and_in_a_corner(self):
        from revit_mcp.rebar_spec import bar_seat

        outline = [(-0.11, -0.36), (0.11, -0.36), (0.11, 0.36), (-0.11, 0.36)]
        side = bar_seat((-0.085, 0.0), outline, '3/8"', '5/8"')
        assert side == (pytest.approx(-0.11 + 0.009525 + 0.0079375), pytest.approx(0.0))
        corner = bar_seat((-0.09, -0.34), outline, '3/8"', '5/8"')
        assert corner == (pytest.approx(-0.0925375), pytest.approx(-0.3425375))
        assert bar_seat((0.0, 0.0), outline, '3/8"', '5/8"') is None  # far from it
