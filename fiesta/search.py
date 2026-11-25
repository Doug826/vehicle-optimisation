"""Outer Nelder-Mead over throttle shape and the A/B/C(/D) resolution ladders."""
from __future__ import annotations

import math
import time

import numpy as np
from scipy.optimize import minimize

from .config import Case, SimResult
from .timing import SOLVERS, BudgetExhausted, Evaluator, InnerResult


def normalise(q) -> np.ndarray | None:
    qp = np.maximum(np.asarray(q, float), 0.0)
    m = qp.max()
    if not np.isfinite(m) or m <= 0:
        return None
    return qp / m


def refine(v: np.ndarray) -> np.ndarray:
    """Double the resolution: each equal-length section splits into two identical children."""
    return np.repeat(np.asarray(v, float), 2)


def flat_simplex(n: int, outer: dict) -> tuple[np.ndarray, np.ndarray]:
    x0 = np.full(n, float(outer["initial_shape"]))
    return x0, np.vstack([x0] + [x0 + outer["perturbation"] * np.eye(n)[i] for i in range(n)])


def perturbed_simplex(x0: np.ndarray, pert, outer: dict) -> np.ndarray:
    n = len(x0)
    scale = float(np.max(x0)) if np.max(x0) > 0 else 1.0
    if isinstance(pert, str) and pert.startswith("random:"):
        rng = np.random.default_rng(int(pert.split(":")[1]))
        d = rng.uniform(-0.3, 0.3, n)
        return np.vstack([x0] + [x0 + scale * d[i] * np.eye(n)[i] for i in range(n)])
    return np.vstack([x0] + [x0 + scale * float(pert) * np.eye(n)[i] for i in range(n)])


def outer_search(ev: Evaluator, x0: np.ndarray, simplex: np.ndarray, inner_name: str,
                 inner_opt: dict, outer: dict, nominal: float | None = None,
                 score=None) -> dict:
    """Outer Nelder-Mead on the shape; each shape is time-matched by the inner solver and
    scored by the shaft energy of that time-feasible run (penalty otherwise). Restarts with a
    fresh simplex at the converged point while it improves. Stops cleanly on BudgetExhausted;
    the evaluator keeps the best feasible incumbent seen anywhere."""
    inner_fn = SOLVERS[inner_name]
    state = {"nominal": nominal, "shapes": 0, "feasible_shapes": 0, "inner_sims": [], "inner_msgs": {}}
    pen = outer["infeasible_penalty_J"]

    def flat_directions(q):
        # q and c*q, and any negative entry (clipped to zero throttle), give identical applied
        # throttles; a small quadratic term removes these flat directions without changing
        # which applied profile wins
        q = np.asarray(q, float)
        neg = np.minimum(q, 0.0)
        return outer["scale_regulariser_J"] * float(np.sum(neg * neg))

    def objective(q):
        shape = normalise(q)
        if shape is None:
            return 100 * pen + flat_directions(q)
        state["shapes"] += 1
        res: InnerResult = inner_fn(ev, shape, inner_opt, state["nominal"])
        state["inner_sims"].append(res.sims)
        key = res.message.split(":")[0]
        state["inner_msgs"][key] = state["inner_msgs"].get(key, 0) + 1
        if not res.found:
            return pen + 1e5 * min(res.best_residual, 100.0) + flat_directions(q)
        state["feasible_shapes"] += 1
        state["nominal"] = res.k
        # scale regulariser: zero at max(q) = 1, identical for every shape, so it cannot
        # change which shape wins; it only removes the free scale direction of the simplex
        reg = outer["scale_regulariser_J"] * math.log(float(np.max(q))) ** 2 + flat_directions(q)
        return (score(res.run) if score else res.run.energy_J) + reg

    # Shapes are scale-invariant (q and 2q give the same normalised shape), so the simplex can
    # drift along that direction on free cache hits; cap shape evaluations as well as sims.
    max_shapes = int(outer["max_shape_evals_per_sim"] * ev.budget)
    legs, best_f, x_start, simp = [], math.inf, x0, simplex
    stop = "converged"
    try:
        for leg in range(1 + int(outer["restarts"])):
            left = max_shapes - state["shapes"]
            if left <= len(x0) + 1:
                stop = "shape-evaluation cap"
                break
            res = minimize(objective, x0=x_start, method="Nelder-Mead",
                           options={"initial_simplex": simp, "xatol": outer["xatol"],
                                    "fatol": outer["fatol_J"], "maxfev": left})
            if state["shapes"] >= max_shapes:
                stop = "shape-evaluation cap"
            legs.append({"leg": leg, "nfev": int(res.nfev), "best": float(res.fun), "message": str(res.message)})
            if not res.fun < best_f - outer["fatol_J"]:
                best_f = min(best_f, float(res.fun))
                break
            best_f = float(res.fun)
            x_start = np.asarray(res.x, float)
            simp = perturbed_simplex(x_start, outer["perturbation"], outer)
    except BudgetExhausted:
        stop = "budget exhausted"
        legs.append({"leg": len(legs), "message": "stopped: simulation budget exhausted"})
    isims = state["inner_sims"]
    return {"stop": stop, "legs": legs, "shapes": state["shapes"],
            "feasible_shapes": state["feasible_shapes"], "inner_msgs": state["inner_msgs"],
            "mean_sims_per_shape": float(np.mean(isims)) if isims else float("nan"),
            "nominal_k": state["nominal"]}


def run_ladder(cfg: dict, exp: dict, method: str, counts: list[int], route: str | None = None,
               baseline: SimResult | None = None, start_override=None, case_overrides=None) -> list[dict]:
    """Run one method through the given resolutions. Returns one record per resolution."""
    mdef = exp["methods"][method]
    inner_opt = dict(exp["inner"])
    for key_ in ("split", "accept_tol_s"):
        if key_ in mdef:
            inner_opt[key_] = mdef[key_]
    outer = exp["outer"]
    case = Case(cfg, route or exp["route"], case_overrides or {})
    out, prev_best, charged = [], None, 0
    for n in counts:
        ev = Evaluator(case, budget=int(exp["budget_sims"][str(n)]))
        ev.offset = charged if mdef["start"] == "inherit" else 0
        t0 = time.perf_counter()
        inherited = None
        replay_ok = None
        nominal = None
        if mdef["start"] == "inherit" and prev_best is not None:
            inherited = refine(prev_best.throttle)
            r = ev.run(inherited)                         # replay before improving
            replay_ok = (r.energy_J == prev_best.energy_J and r.time_s == prev_best.time_s
                         and r.shifts == prev_best.shifts)
            x0 = inherited.copy()
            simplex = perturbed_simplex(x0, outer["perturbation"], outer)
            nominal = float(np.max(inherited))
        else:
            x0, simplex = flat_simplex(n, outer)
        info = outer_search(ev, x0, simplex, mdef["inner"], inner_opt, outer, nominal)
        wall = time.perf_counter() - t0
        b = ev.best
        rec = {
            "method": method, "route": case.route, "n_sections": n,
            "feasible": b is not None,
            "energy_kJ": b.energy_J / 1e3 if b else None,
            "saving_percent": 100 * (baseline.energy_J - b.energy_J) / baseline.energy_J if (b and baseline) else None,
            "time_s": b.time_s if b else None,
            "residual_s": b.time_s - case.target if b else None,
            "finish_speed_m_s": b.finish_speed_m_s if b else None,
            "shifts": b.shifts if b else None,
            "sims": ev.sims, "cache_hits": ev.cache_hits,
            "cumulative_sims": ev.offset + ev.sims,
            "first_feasible_at": ev.first_feasible_at,
            "feasible_runs": ev.feasible_runs,
            "budget": ev.budget, "budget_limited": info["stop"] in ("budget exhausted", "shape-evaluation cap"),
            "wall_s": wall, "inherited_replay_identical": replay_ok,
            "throttle": b.throttle.tolist() if b else None,
            "history": ev.history, **{k: v for k, v in info.items()},
        }
        out.append(rec)
        if b is not None:
            prev_best = b
        charged += ev.sims
    return out
