"""Nested Nelder-Mead.

Two methods, chosen by optimiser.method in the config:

  "ratio" (default)  the original project's structure. The outer search varies the
                     throttle of each section as a ratio to the first section; the inner
                     search varies the first section's throttle until the journey time
                     matches. Throttles are kept strictly inside (throttle_min, throttle_max)
                     by a flat penalty, so the car never coasts at zero throttle.
  "shape"            the first reconstruction: max-normalised shape, scale k in (0, 1],
                     graded penalties, restarts, zero-throttle coasting allowed. Its
                     settings live in optimiser.shape_method.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from .config import Case, SimResult

INFEASIBLE_SHAPE = 1e9      # all-zero / non-positive proposal: cannot leave rest
UNMATCHED_SHAPE = 1e7       # shape whose inner search found no in-window run
UNFINISHED = 1e3            # inner penalty for a trip that never reached the line


@dataclass
class InnerOutcome:
    best: SimResult | None          # closest-to-target finished run
    best_err: float
    nfev: int
    message: str
    simplex: list


@dataclass
class Tracker:
    """Keeps the lowest-energy feasible run seen anywhere in the search."""
    case: Case
    best: SimResult | None = None
    feasible_runs: int = 0
    sims: int = 0

    def offer(self, r: SimResult):
        self.sims += 1
        if self.case.feasible(r):
            self.feasible_runs += 1
            if self.best is None or r.energy_J < self.best.energy_J:
                self.best = r


def normalise_shape(q) -> np.ndarray | None:
    qp = np.maximum(np.asarray(q, float), 0.0)
    m = qp.max()
    if not np.isfinite(m) or m <= 0.0:
        return None
    return qp / m


def inner_search(case: Case, shape: np.ndarray, opt: dict, tracker: Tracker) -> InnerOutcome:
    """1-D Nelder-Mead over k in (0, 1]; applied throttle = k * shape (shape max = 1),
    so the admissible scale bound keeps every throttle in [0, 1] without clipping,
    preserving the proposed shape."""
    best = {"r": None, "err": np.inf}

    def f(kv):
        k = float(kv[0])
        kk = min(max(k, 0.0), 1.0)
        pen = 100.0 * abs(k - kk)                 # back out of out-of-bound scales
        if kk <= 0.0:
            return UNFINISHED * 2 + pen
        r = case.run(kk * shape)
        tracker.offer(r)
        if not r.finished:
            return UNFINISHED + UNFINISHED * (1 - r.distance_m / case.length) + pen
        err = abs(r.time_s - case.target)
        if err < best["err"]:
            best["r"], best["err"] = r, err
        return err + pen

    s0, s1 = opt["inner_start_scales"]
    simplex = [[s0], [s1]]
    res = minimize(f, x0=[s0], method="Nelder-Mead",
                   options={"initial_simplex": np.array(simplex), "xatol": opt["inner_xatol"],
                            "fatol": opt["inner_fatol_s"], "maxfev": opt["inner_maxfev"]})
    return InnerOutcome(best["r"], best["err"], int(res.nfev), str(res.message), simplex)


def _shape_baseline(case: Case, opt: dict) -> dict:
    """Constant throttle that meets the time target (same vehicle, gear policy, finish)."""
    tr = Tracker(case)
    t0 = time.perf_counter()
    inner = inner_search(case, np.ones(1), opt, tr)
    # Same selection rule as the profile search: lowest-energy in-window run seen.
    r = tr.best if tr.best is not None else inner.best
    return {
        "result": r,
        "feasible": r is not None and case.feasible(r),
        "throttle": float(r.throttle[0]) if r is not None else None,
        "time_error_s": inner.best_err,
        "sim_calls": tr.sims,
        "runtime_s": time.perf_counter() - t0,
        "inner_message": inner.message,
        "inner_start_simplex": inner.simplex,
    }


def _shape_profile(case: Case, n_sections: int, opt: dict, max_outer_evals: int) -> dict:
    tr = Tracker(case)
    t0 = time.perf_counter()
    inner_log = {"calls": 0, "unmatched": 0, "nfev": 0, "messages": {}}

    def outer(q):
        shape = normalise_shape(q)
        if shape is None:
            return INFEASIBLE_SHAPE
        o = inner_search(case, shape, opt, tr)
        inner_log["calls"] += 1
        inner_log["nfev"] += o.nfev
        inner_log["messages"][o.message] = inner_log["messages"].get(o.message, 0) + 1
        if o.best is None or not case.feasible(o.best):
            inner_log["unmatched"] += 1
            return UNMATCHED_SHAPE + 1e5 * min(o.best_err, 100.0)
        return o.best.energy_J

    x0 = np.full(n_sections, float(opt["outer_initial_shape"]))
    simplex = [x0.copy()]
    for i in range(n_sections):
        v = x0.copy()
        v[i] += opt["outer_perturbation"]
        simplex.append(v)
    simplex = np.array(simplex)
    first_simplex = simplex.tolist()
    # Nelder-Mead stalls in high dimension; restart from the converged point with a
    # fresh axis-perturbation simplex while it keeps improving and budget remains.
    used, nit, legs = 0, 0, []
    best_f = np.inf
    x_start = x0
    for leg in range(1 + int(opt.get("outer_restarts", 0))):
        cap = max_outer_evals - used
        if cap <= n_sections + 1:
            break
        res = minimize(outer, x0=x_start, method="Nelder-Mead",
                       options={"initial_simplex": simplex, "xatol": opt["outer_xatol"],
                                "fatol": opt["outer_fatol_J"], "maxfev": cap, "adaptive": False})
        used += int(res.nfev)
        nit += int(res.nit)
        legs.append({"leg": leg, "nfev": int(res.nfev), "best_score": float(res.fun),
                     "message": str(res.message)})
        improved = res.fun < best_f - opt["outer_fatol_J"]
        best_f = min(best_f, float(res.fun))
        if "Maximum" in str(res.message) or not improved:
            break
        x_start = np.asarray(res.x, float)
        simplex = np.array([x_start] + [x_start + opt["outer_perturbation"] * np.eye(n_sections)[i]
                                        for i in range(n_sections)])
    runtime = time.perf_counter() - t0
    budget_limited = used >= max_outer_evals or "Maximum" in legs[-1]["message"]
    return {
        "n_sections": n_sections,
        "result": tr.best,
        "feasible": tr.best is not None,
        "outer_nfev": used,
        "outer_nit": nit,
        "outer_message": legs[-1]["message"] + (f" (after {len(legs)} Nelder-Mead leg(s))"),
        "outer_legs": legs,
        "outer_max_evals": max_outer_evals,
        "budget_limited": bool(budget_limited),
        "outer_start_simplex": first_simplex,
        "inner_searches": inner_log["calls"],
        "inner_nfev_total": inner_log["nfev"],
        "inner_unmatched": inner_log["unmatched"],
        "inner_messages": inner_log["messages"],
        "sim_calls": tr.sims,
        "feasible_runs_seen": tr.feasible_runs,
        "runtime_s": runtime,
    }


# --------------------------------------------------------------------------- ratio method
PENALTY = 1e10              # flat penalty for any out-of-range or unmatched proposal


class RatioSearch:
    """Inner time match and outer energy objective of the ratio method."""

    def __init__(self, case: Case, opt: dict):
        self.case, self.opt = case, opt
        self.lo, self.hi = float(opt["throttle_min"]), float(opt["throttle_max"])
        self.sims = 0
        self.feasible_runs = 0
        self.inner = {"calls": 0, "unmatched": 0, "nfev": 0, "messages": {}}

    def out_of_range(self, th) -> bool:
        return bool(np.any((th >= self.hi) | (th <= self.lo)))

    def simulate(self, th):
        """Returns (energy_J, time_s). A trip that never finishes counts as the time cap."""
        self.sims += 1
        r = self.case.run(th)
        if self.case.feasible(r):
            self.feasible_runs += 1
        return r.energy_J, (r.time_s if r.finished else self.case.cfg["simulation"]["max_time_s"])

    def time_objective(self, ratios, start=None):
        """Section throttles for these ratios that meet the time target, or None."""
        ratios = np.asarray(ratios, float)
        self.inner["calls"] += 1
        if not np.all(np.isfinite(ratios)) or ratios[0] <= 0.0:
            self.inner["unmatched"] += 1
            return None
        adjusted = ratios / ratios[0]

        def f(guess):
            th = adjusted * guess[0]
            if self.out_of_range(th):
                return PENALTY
            _, t = self.simulate(th)
            return abs(t - self.case.target)

        res = minimize(f, [self.opt["inner_start_throttle"] if start is None else start], method="Nelder-Mead",
                       options={"xatol": self.opt["inner_xatol"]})
        self.inner["nfev"] += int(res.nfev)
        msg = str(res.message)
        self.inner["messages"][msg] = self.inner["messages"].get(msg, 0) + 1
        th = adjusted * res.x[0]
        if self.out_of_range(th):
            self.inner["unmatched"] += 1
            return None
        _, t = self.simulate(th)
        if abs(t - self.case.target) > self.case.tol:
            self.inner["unmatched"] += 1
            return None
        return th

    def matched(self, ratios, retry: bool):
        """Time match from the configured start; with retry, also from the fallback starts.
        Time against throttle jumps where the gear pattern changes, so a single inner
        search can stop at a jump without matching the time."""
        starts = [self.opt["inner_start_throttle"]]
        if retry:
            starts += list(self.opt.get("inner_retry_starts", []))
        for start in starts:
            th = self.time_objective(ratios, start)
            if th is not None:
                return th, start
        return None, starts[-1]

    def energy_objective(self, ratios):
        th, _ = self.matched(ratios, bool(self.opt.get("retry_inner_in_search", False)))
        if th is None:
            return PENALTY
        e, _ = self.simulate(th)
        return e


def _ratio_baseline(case: Case, opt: dict) -> dict:
    """Constant throttle that meets the time target, from the same inner search."""
    s = RatioSearch(case, opt)
    t0 = time.perf_counter()
    th, used = s.matched(np.ones(1), True)   # the baseline always retries
    r = case.run(th) if th is not None else None
    return {
        "result": r,
        "feasible": r is not None and case.feasible(r),
        "throttle": float(th[0]) if th is not None else None,
        "time_error_s": abs(r.time_s - case.target) if r is not None else None,
        "sim_calls": s.sims,
        "runtime_s": time.perf_counter() - t0,
        "inner_message": next(iter(s.inner["messages"]), "inner search not run"),
        "inner_start_throttle": used,
    }


def _ratio_profile(case: Case, n_sections: int, opt: dict, max_outer_evals: int) -> dict:
    s = RatioSearch(case, opt)
    t0 = time.perf_counter()
    ratios = np.ones(n_sections)
    # Wide starting simplex: one random perturbation per section, clipped to the ratio range.
    rng = np.random.default_rng(int(opt["simplex_seed"]) + n_sections)
    p = float(opt["outer_perturbation"])
    rlo, rhi = opt["ratio_clip"]
    simplex = [ratios.copy()]
    for i in range(n_sections):
        v = ratios.copy()
        v[i] += rng.uniform(-p, p)
        simplex.append(np.clip(v, rlo, rhi))
    simplex = np.array(simplex)
    res = minimize(s.energy_objective, ratios, method="Nelder-Mead",
                   options={"initial_simplex": simplex, "xatol": opt["outer_xatol"],
                            "fatol": opt["outer_fatol_J"], "maxiter": opt["outer_maxiter"],
                            "maxfev": max_outer_evals})
    # The reported run is the converged ratios put back through the time match.
    th = s.matched(res.x, bool(opt.get("retry_inner_in_search", False)))[0] if res.fun < PENALTY else None
    r = case.run(th) if th is not None else None
    msg = str(res.message)
    return {
        "n_sections": n_sections,
        "result": r,
        "feasible": r is not None and case.feasible(r),
        "best_ratios": np.asarray(res.x, float).tolist(),
        "outer_nfev": int(res.nfev),
        "outer_nit": int(res.nit),
        "outer_message": msg,
        "outer_max_evals": max_outer_evals,
        "outer_maxiter": opt["outer_maxiter"],
        "budget_limited": "Maximum" in msg,
        "outer_start_simplex": simplex.tolist(),
        "inner_searches": s.inner["calls"],
        "inner_nfev_total": s.inner["nfev"],
        "inner_unmatched": s.inner["unmatched"],
        "inner_messages": s.inner["messages"],
        "sim_calls": s.sims,
        "feasible_runs_seen": s.feasible_runs,
        "runtime_s": time.perf_counter() - t0,
    }


# --------------------------------------------------------------------------- dispatch
def _is_shape(opt: dict) -> bool:
    return opt.get("method", "ratio") == "shape"


def constant_baseline(case: Case, opt: dict) -> dict:
    if _is_shape(opt):
        return _shape_baseline(case, {**opt, **opt["shape_method"]})
    return _ratio_baseline(case, opt)


def optimise_profile(case: Case, n_sections: int, opt: dict, max_outer_evals: int) -> dict:
    if _is_shape(opt):
        return _shape_profile(case, n_sections, {**opt, **opt["shape_method"]}, max_outer_evals)
    return _ratio_profile(case, n_sections, opt, max_outer_evals)
