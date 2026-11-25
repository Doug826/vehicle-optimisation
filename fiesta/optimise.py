"""Nested Nelder-Mead: outer search over throttle shape q, inner search over scale k."""
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


def constant_baseline(case: Case, opt: dict) -> dict:
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


def optimise_profile(case: Case, n_sections: int, opt: dict, max_outer_evals: int) -> dict:
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
