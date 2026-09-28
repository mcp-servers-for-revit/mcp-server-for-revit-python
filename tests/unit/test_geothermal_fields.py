"""Sanity coverage for every field layout in the pygfunction module map."""
import pytest

from geothermal import fields

H, D, R_B = 100.0, 2.0, 0.075


def test_rectangle_field():
    f = fields.rectangle_field(N_1=3, N_2=2, B_1=6.0, B_2=6.0, H=H, D=D, r_b=R_B)
    assert fields.field_summary(f)["n_boreholes"] == 6


def test_staggered_rectangle_field():
    f = fields.staggered_rectangle_field(N_1=3, N_2=2, B_1=6.0, B_2=6.0, H=H, D=D, r_b=R_B,
                                          include_last_borehole=True)
    assert fields.field_summary(f)["n_boreholes"] > 0


def test_dense_rectangle_field():
    f = fields.dense_rectangle_field(N_1=3, N_2=2, B=6.0, H=H, D=D, r_b=R_B, include_last_borehole=True)
    assert fields.field_summary(f)["n_boreholes"] > 0


def test_box_shaped_field():
    f = fields.box_shaped_field(N_1=4, N_2=4, B_1=6.0, B_2=6.0, H=H, D=D, r_b=R_B)
    summary = fields.field_summary(f)
    assert summary["n_boreholes"] < 16  # hollow box, fewer than a full 4x4 rectangle


def test_U_shaped_field():
    f = fields.U_shaped_field(N_1=4, N_2=4, B_1=6.0, B_2=6.0, H=H, D=D, r_b=R_B)
    assert fields.field_summary(f)["n_boreholes"] > 0


def test_L_shaped_field():
    f = fields.L_shaped_field(N_1=4, N_2=4, B_1=6.0, B_2=6.0, H=H, D=D, r_b=R_B)
    assert fields.field_summary(f)["n_boreholes"] > 0


def test_circle_field():
    f = fields.circle_field(N=8, R=10.0, H=H, D=D, r_b=R_B)
    assert fields.field_summary(f)["n_boreholes"] == 8


def test_custom_field():
    f = fields.custom_field(x=[0.0, 5.0, 10.0], y=[0.0, 0.0, 0.0], H=H, D=D, r_b=R_B)
    assert fields.field_summary(f)["n_boreholes"] == 3


def test_combine_fields():
    a = fields.rectangle_field(N_1=2, N_2=2, B_1=6.0, B_2=6.0, H=H, D=D, r_b=R_B)
    b = fields.circle_field(N=6, R=15.0, H=H, D=D, r_b=R_B)
    combined = fields.combine_fields(a, b)
    assert fields.field_summary(combined)["n_boreholes"] == 4 + 6


def test_field_roundtrip_through_borefield_object():
    f = fields.rectangle_field(N_1=2, N_2=2, B_1=6.0, B_2=6.0, H=H, D=D, r_b=R_B)
    bf = fields.to_borefield(f)
    f2 = fields.from_borefield(bf)
    assert f == f2


def test_field_from_empty_boreholes_raises():
    with pytest.raises(ValueError):
        fields.to_borefield({"boreholes": []})
