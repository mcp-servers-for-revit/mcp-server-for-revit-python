"""Thermal response test (TRT) parameter estimation: given a measured
inlet/outlet fluid temperature history from a constant-ish heat-injection (or
extraction) test on a single borehole, estimate the ground conductivity k_s
and borehole resistance R_b that the rest of this engine would need as
inputs -- the standard TRT analysis problem, done here as a genuine nonlinear
fit against the same finite-line-source physics geothermal.simulation uses
for sizing, rather than the classical infinite-line-source semi-log method
(which ignores borehole length/buried depth D and is less accurate,
especially for short/shallow test boreholes or medium test durations).

Method: for a single borehole, UBWT and UHTR g-functions are numerically
identical (there is only one borehole to be "uniform" across), so the same
UBWT machinery geothermal.simulation.run_hourly_simulation relies on for
sizing applies directly here -- see
test_trt.py::test_uhtr_and_ubwt_agree_for_a_single_borehole for the
regression pinning that assumption. Because a TRT is a short (days, not
years), few-hundred-point measurement, this module does an EXACT discrete
temporal superposition (a causal convolution against g(t) evaluated once per
fit iteration) rather than reaching for pygfunction's approximate
ClaessonJaved/MLAA/Liu load-aggregation algorithms geothermal.simulation uses
for multi-year runs -- those trade some accuracy for speed on very long
simulations, a trade not needed (or wanted, per this project's own
accuracy-over-speed priority) for a few hundred hourly points.

Two fit modes, selected by whether R_b_mK_W is given:
  - R_b_mK_W given: R_b is held fixed (e.g. computed independently via
    geothermal.pipes.effective_resistance from known pipe/grout/flow specs)
    and only k_s is fit -- a 1-parameter fit, the more robust choice
    whenever the borehole's internals are already known with confidence.
  - R_b_mK_W omitted: k_s and R_b are fit jointly (2 parameters) -- the
    standard "raw TRT data only" analysis, and also useful as a QA check
    even when pipe/grout specs ARE known (comparing the fitted R_b against
    pipes.effective_resistance's calculated value can flag a grouting
    problem).

Ground volumetric heat capacity (rho_cp_J_m3K) is NOT fit -- a single TRT's
temperature response is known to poorly constrain k_s and alpha
independently (a longer test barely improves this), so, matching standard
practice, rho_cp is taken as a known/assumed input and only k_s (which
DOES have good sensitivity) is estimated. Early-time data before t_min_s is
excluded from the fit residuals (not from the heat-injection history used to
compute them -- excluding it there would silently change the physics) since
the true finite-line-source response is not yet a good match to a
real borehole in that regime (non-uniform initial fluid temperature,
short-term borehole-internal effects this engine does not model -- see
geothermal/README.md's Phase 1 audit, item 6). Callers should pick t_min_s
from the standard Fourier-number guidance (Fo = alpha*t/r_b**2 > ~5) for
their own expected ground type; this module does not guess one, since a
wrong guess here would silently bias the fit.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from scipy.optimize import curve_fit

from geothermal import fields, gfunctions

_TWO_PI = 2.0 * np.pi


def _validate_uniform_grid(time_s: np.ndarray) -> float:
    if len(time_s) < 2:
        raise ValueError("time_s must have at least 2 points")
    if np.any(np.diff(time_s) <= 0):
        raise ValueError("time_s must be strictly increasing")
    dt = time_s[0]
    steps = np.diff(np.concatenate(([0.0], time_s)))
    if not np.allclose(steps, dt, rtol=1e-6, atol=1e-6):
        raise ValueError(
            "time_s must be an evenly spaced grid starting at t=dt (i.e. "
            "time_s[i] == (i+1)*dt) -- the test is assumed to start at t=0 "
            "with the first measurement one sampling interval later, same "
            "convention as geothermal.simulation.run_hourly_simulation's "
            "hourly stepping. Resample irregular logger data onto a uniform "
            "grid before calling this function."
        )
    return float(dt)


def estimate_ground_properties_from_trt(
    H: float, D: float, r_b: float,
    time_s: list[float], T_f_C: list[float], Q_W: float | list[float],
    T_g: float, rho_cp_J_m3K: float,
    k_s_guess: float, t_min_s: float,
    R_b_mK_W: Optional[float] = None, R_b_guess: Optional[float] = None,
    gfunc_method: str = "equivalent",
) -> dict[str, Any]:
    """Fit k_s (and, unless R_b_mK_W is given, R_b) against a measured TRT
    fluid-temperature history on a single borehole.

    Q_W: heat rate applied during the test, same sign convention as
    geothermal.simulation's hourly_load_W (positive = heat EXTRACTED from
    the ground) -- a typical heat-injection TRT uses a negative Q_W. Either
    a single constant value (broadcast across the whole test) or one value
    per time_s sample (a step history, applied over the interval ending at
    each sample).

    Returns k_s_W_mK/R_b_mK_W (fitted or fixed), their standard errors from
    the fit's covariance (R_b_stderr_mK_W is None when R_b was held fixed),
    the implied alpha_m2_s = k_s/rho_cp_J_m3K, how many of the n_points_total
    measurements were inside [t_min_s, end] and actually used
    (n_points_used), the fit's RMSE over just those points (rmse_C), and the
    model's predicted T_f_C over the FULL test duration (including the
    excluded early window) for plotting fit vs. measured.
    """
    if rho_cp_J_m3K <= 0:
        raise ValueError("rho_cp_J_m3K must be > 0")
    if k_s_guess <= 0:
        raise ValueError("k_s_guess must be > 0")
    if R_b_mK_W is not None and R_b_guess is not None:
        raise ValueError("give either R_b_mK_W (held fixed) or R_b_guess (fit for it), not both")
    if R_b_mK_W is None and R_b_guess is None:
        raise ValueError("R_b_guess is required when R_b_mK_W is not given (R_b is then a free parameter)")
    if R_b_mK_W is not None and R_b_mK_W <= 0:
        raise ValueError("R_b_mK_W must be > 0")
    if R_b_guess is not None and R_b_guess <= 0:
        raise ValueError("R_b_guess must be > 0")

    time_arr = np.asarray(time_s, dtype=float)
    T_f_arr = np.asarray(T_f_C, dtype=float)
    n = len(time_arr)
    if len(T_f_arr) != n:
        raise ValueError("time_s and T_f_C must have the same length")
    Q_arr = np.full(n, float(Q_W)) if np.isscalar(Q_W) else np.asarray(Q_W, dtype=float)
    if len(Q_arr) != n:
        raise ValueError("Q_W must be a scalar or have the same length as time_s")

    dt = _validate_uniform_grid(time_arr)

    mask = time_arr >= t_min_s
    fit_R_b = R_b_mK_W is None
    n_params = 2 if fit_R_b else 1
    if mask.sum() <= n_params:
        raise ValueError(
            f"only {int(mask.sum())} points remain at or after t_min_s={t_min_s} s, "
            f"not enough to fit {n_params} parameter(s) with any residual degrees of freedom"
        )

    field = fields.custom_field(x=[0.0], y=[0.0], H=H, D=D, r_b=r_b)
    # Per unit borehole length (W/m), same convention as
    # geothermal.simulation.run_hourly_simulation's q_i / total_length_m --
    # the g-function itself is normalized per unit length, so leaving Q in
    # raw watts here would be off by a factor of H.
    q_per_length = Q_arr / H
    dq = np.diff(np.concatenate(([0.0], q_per_length)))
    idx_full = np.arange(n)

    def _T_f_full(k_s: float, R_b: float) -> np.ndarray:
        alpha = k_s / rho_cp_J_m3K
        g = np.asarray(gfunctions.evaluate(
            field, alpha, time_arr.tolist(), method=gfunc_method, boundary_condition="UBWT",
        )["g"])
        T_b = T_g - np.convolve(dq, g / (_TWO_PI * k_s), mode="full")[:n]
        return T_b - q_per_length * R_b

    def _model(idx: np.ndarray, *params: float) -> np.ndarray:
        if fit_R_b:
            k_s, R_b = params
        else:
            k_s = params[0]
            R_b = R_b_mK_W
        return _T_f_full(k_s, R_b)[idx.astype(int)]

    p0 = [k_s_guess, R_b_guess] if fit_R_b else [k_s_guess]
    bounds = ([1e-6] * n_params, [np.inf] * n_params)

    popt, pcov = curve_fit(_model, idx_full[mask], T_f_arr[mask], p0=p0, bounds=bounds)
    stderr = np.sqrt(np.diag(pcov))

    if fit_R_b:
        k_s_fit, R_b_fit = float(popt[0]), float(popt[1])
        k_s_stderr, R_b_stderr = float(stderr[0]), float(stderr[1])
    else:
        k_s_fit, R_b_fit = float(popt[0]), float(R_b_mK_W)
        k_s_stderr, R_b_stderr = float(stderr[0]), None

    predicted_full = _T_f_full(k_s_fit, R_b_fit)
    residuals_fit = predicted_full[mask] - T_f_arr[mask]

    return {
        "k_s_W_mK": k_s_fit,
        "k_s_stderr_W_mK": k_s_stderr,
        "R_b_mK_W": R_b_fit,
        "R_b_stderr_mK_W": R_b_stderr,
        "R_b_was_fixed": not fit_R_b,
        "alpha_m2_s": k_s_fit / rho_cp_J_m3K,
        "dt_s": dt,
        "t_min_s": t_min_s,
        "n_points_total": n,
        "n_points_used": int(mask.sum()),
        "rmse_C": float(np.sqrt(np.mean(residuals_fit ** 2))),
        "T_f_predicted_C": predicted_full.tolist(),
    }
