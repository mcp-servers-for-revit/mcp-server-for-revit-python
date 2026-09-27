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
