"""Design-risk reporting: Monte Carlo sizing under uncertain ground/load
inputs, reporting sized borehole length as percentiles (P50/P90/...) instead
of geothermal.sizing.size_field's single deterministic answer.

size_field's own inputs (k_s, T_g, the load, ...) are rarely known exactly --
a TRT (see geothermal/trt.py) reports k_s with a standard error, not a bare
number, and T_g/loads carry their own real uncertainty. Sizing once at a
single "best guess" value hides that: this module resamples size_field many
times, each trial drawing fresh values for whichever inputs the caller
declares uncertain, and reports the resulting spread of sized depths.

base_kwargs is a plain dict of the exact keyword arguments size_field()
itself takes (the ones a caller already builds for a normal, deterministic
"Size Field" call) -- deliberately NOT duplicated as a 20-parameter function
signature here, the way most other size_field wrappers in this codebase
(hybrid.size_field_with_deadband_tower etc.) do, since the entire point of a
generic Monte Carlo wrapper is to pass whatever size_field accepts straight
through unmodified, including future kwargs (e.g. a tower_control_factory)
this module has no reason to know about individually.

uncertain_inputs maps a size_field parameter name to a distribution spec:
    {"k_s": {"dist": "normal", "mean": 2.0, "stddev": 0.15},
     "T_g": {"dist": "uniform", "low": 11.0, "high": 13.0}}
Each sampled value overrides that key in base_kwargs for that one trial;
every other key is held fixed at base_kwargs' value. Only "normal"
(mean/stddev) and "uniform" (low/high) are supported -- the two distribution
shapes an input like a TRT's reported stderr or a stated ground-temperature
range actually gives you, not a wider parametric family invented for its
own sake.

Failure handling: size_field raises ValueError when even H_max fails the
fluid-temperature limit. A trial that fails this way is NOT dropped from the
percentile calculation (dropping it would silently understate how bad the
worst outcomes really are) -- its H_m is recorded as H_max itself, a
censored lower bound on what would actually be needed, and counted in
n_failed/fraction_failed so a caller can see how often this happened. A
"P90 == H_max" result together with a non-trivial fraction_failed is itself
a real finding (H_max is too tight for the stated uncertainty), not a
plain answer -- surfaced, not hidden.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

import numpy as np

from geothermal import sizing

_DISTRIBUTIONS = ("normal", "uniform")


def _sample(rng: np.random.Generator, spec: dict[str, Any], n: int) -> np.ndarray:
    dist = spec.get("dist")
    if dist == "normal":
        if spec["stddev"] < 0:
            raise ValueError("normal distribution stddev must be >= 0")
        return rng.normal(spec["mean"], spec["stddev"], n)
    if dist == "uniform":
        if spec["low"] > spec["high"]:
            raise ValueError("uniform distribution needs low <= high")
        return rng.uniform(spec["low"], spec["high"], n)
    raise ValueError(f"unknown dist {dist!r}, expected one of {_DISTRIBUTIONS}")


def size_field_monte_carlo(
    base_kwargs: dict[str, Any],
    uncertain_inputs: dict[str, dict[str, Any]],
    n_samples: int = 200,
    percentiles: Optional[list[float]] = None,
    random_seed: Optional[int] = None,
) -> dict[str, Any]:
    if n_samples <= 0:
        raise ValueError("n_samples must be > 0")
    if not uncertain_inputs:
        raise ValueError("uncertain_inputs must declare at least one varying input")
    size_field_params = inspect.signature(sizing.size_field).parameters
    unknown = set(uncertain_inputs) - set(size_field_params)
    if unknown:
        raise ValueError(f"uncertain_inputs names {sorted(unknown)} are not size_field parameters")

    percentiles = [50.0, 90.0] if percentiles is None else list(percentiles)
    if any(p < 0 or p > 100 for p in percentiles):
        raise ValueError("percentiles must each be within [0, 100]")

    rng = np.random.default_rng(random_seed)
    samples = {name: _sample(rng, spec, n_samples) for name, spec in uncertain_inputs.items()}

    H_max = base_kwargs["H_max"] if "H_max" in base_kwargs else size_field_params["H_max"].default
    H_m_samples: list[float] = []
    failed_trials: list[dict[str, float]] = []
    for i in range(n_samples):
        trial_kwargs = dict(base_kwargs)
        trial_kwargs.update({name: float(values[i]) for name, values in samples.items()})
        try:
            result = sizing.size_field(**trial_kwargs)
            H_m_samples.append(result["H_m"])
        except ValueError:
            H_m_samples.append(float(H_max))
            failed_trials.append({name: float(values[i]) for name, values in samples.items()})

    H_arr = np.asarray(H_m_samples)
    percentile_values = np.percentile(H_arr, percentiles)

    return {
        "n_samples": n_samples,
        "n_failed": len(failed_trials),
        "fraction_failed": len(failed_trials) / n_samples,
        "failed_trial_inputs": failed_trials,
        "H_m_samples": H_arr.tolist(),
        "H_m_mean": float(H_arr.mean()),
        "H_m_stddev": float(H_arr.std(ddof=1)) if n_samples > 1 else 0.0,
        "percentiles": [{"p": float(p), "H_m": float(v)} for p, v in zip(percentiles, percentile_values)],
        "sampled_inputs": {name: values.tolist() for name, values in samples.items()},
    }
