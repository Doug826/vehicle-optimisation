"""All brief-v2 experiments. Each function returns plain dicts/lists that run.py saves."""
from __future__ import annotations

import copy
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from . import model as M
from .config import ROOT, Case
from .reference import REF_DIR, gear_change_locations, load_reference_config
from .search import normalise, outer_search, perturbed_simplex, refine, run_ladder
from .timing import Evaluator, solve_bisection, solve_nelder_mead


def load_experiment() -> dict:
    return json.loads((ROOT / "config" / "experiment.json").read_text(encoding="utf-8"))


def ref_vectors() -> dict:
    return json.loads((REF_DIR / "throttle_vectors.json").read_text(encoding="utf-8"))


def baseline_run(cfg, route="rolling", overrides=None):
    """The separately time-matched constant-throttle baseline: the v1 reference vector."""
    return Case(cfg, route, overrides or {}).run(ref_vectors()[f"{route}/constant"], record=True)


# ------------------------------------------------------------------ inner solvers on identical shapes
def named_shapes() -> dict:
    v = ref_vectors()
    return {"flat (constant shape)": np.ones(8),
            "hill profile 1 (v1 best, N=8)": normalise(v["rolling/N8"]),
            "hill profile 2 (v1 N=16)": normalise(v["rolling/N16"])}


def scan_scale(cfg, shape, route="rolling", n=600):
    case = Case(cfg, route)
    ks = np.linspace(0.02, 1.0, n)
    rows = []
    for k in ks:
        r = case.run(k * shape)
        rows.append({"k": float(k), "status": r.status_name,
                     "time_s": r.time_s if r.finished else float("nan"),
                     "shifts": r.shifts, "energy_kJ": r.energy_J / 1e3})
    return rows


def inner_comparison(cfg, exp) -> dict:
    case = Case(cfg, exp["route"])
    shapes = dict(named_shapes())
    rng = np.random.default_rng(exp["inner_comparison"]["seed"])
    for i in range(exp["inner_comparison"]["random_shapes"]):
        n = int(rng.choice([4, 8, 16]))
        shapes[f"random {i + 1:02d} (N={n})"] = normalise(rng.uniform(0.0, 1.0, n))
    solvers = {
        "A: Nelder-Mead": (solve_nelder_mead, dict(exp["inner"])),
        "B: bisection": (solve_bisection, dict(exp["inner"])),
        "D: secant + 0.02 s": (solve_bisection, dict(exp["inner"], split="interpolate", accept_tol_s=0.02)),
    }
    rows = []
    for name, shape in shapes.items():
        for sname, (fn, opt) in solvers.items():
            ev = Evaluator(case, budget=10**6)          # fresh cache: no sharing between solvers
            t0 = time.perf_counter()
            r = fn(ev, shape, opt, None)
            rows.append({"shape": name, "solver": sname, "found": r.found,
                         "sims": ev.sims, "cache_hits": ev.cache_hits,
                         "residual_s": (r.run.time_s - case.target) if r.found else None,
                         "best_abs_residual_s": r.best_residual,
                         "energy_kJ": r.run.energy_J / 1e3 if r.found else None,
                         "k": r.k, "wall_ms": 1e3 * (time.perf_counter() - t0),
                         "message": r.message, "log": r.log if name in named_shapes() else None})
    scans = {name: scan_scale(cfg, shape, exp["route"], exp["inner_comparison"]["scan_points"])
             for name, shape in named_shapes().items()}
    return {"rows": rows, "scans": scans}


# ------------------------------------------------------------------ A/B/C(/D) ladders
def _ladder_job(args):
    method, counts, budgets_override = args
    cfg = load_reference_config()
    exp = load_experiment()
    if budgets_override:
        exp["budget_sims"].update(budgets_override)
    base = baseline_run(cfg)
    t0 = time.perf_counter()
    recs = run_ladder(cfg, exp, method, counts, baseline=base)
    return method, recs, time.perf_counter() - t0


def ladders(exp, quick=False, workers=4) -> dict:
    counts = [1, 2, 4] if quick else exp["section_counts"]
    extra = [] if quick else exp["extra_section_counts_method_C"]
    budgets = {"1": 150, "2": 600, "4": 1500} if quick else None
    jobs = [(m, counts + (extra if m in ("C", "D") else []), budgets) for m in exp["methods"]]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(_ladder_job, jobs))
    return {m: {"records": recs, "wall_s": w} for m, recs, w in res}


# ------------------------------------------------------------------ multistart
def _multistart_job(args):
    method, pert, n, inherited = args
    cfg = load_reference_config()
    exp = load_experiment()
    mdef = exp["methods"][method]
    inner_opt = dict(exp["inner"])
    for k in ("split", "accept_tol_s"):
        if k in mdef:
            inner_opt[k] = mdef[k]
    case = Case(cfg, exp["route"])
    ev = Evaluator(case, budget=int(exp["budget_sims"][str(n)]))
    base = baseline_run(cfg)
    if mdef["start"] == "inherit" and inherited is not None:
        x0 = refine(np.asarray(inherited))
        ev.run(x0)
        nominal = float(np.max(x0))
    else:
        x0 = np.full(n, float(exp["outer"]["initial_shape"]))
        nominal = None
    simplex = perturbed_simplex(x0, pert, exp["outer"])
    info = outer_search(ev, x0, simplex, mdef["inner"], inner_opt, exp["outer"], nominal)
    b = ev.best
    return {"method": method, "perturbation": str(pert), "n_sections": n, "sims": ev.sims,
            "energy_kJ": b.energy_J / 1e3 if b else None,
            "saving_percent": 100 * (base.energy_J - b.energy_J) / base.energy_J if b else None,
            "residual_s": b.time_s - case.target if b else None, "stop": info["stop"],
            "throttle": b.throttle.tolist() if b else None}


def multistart(exp, ladder_res, workers=12) -> list:
    n = exp["multistart"]["n_sections"]
    jobs = []
    for m in exp["methods"]:
        inh = None
        if exp["methods"][m]["start"] == "inherit":
            prev = [r for r in ladder_res[m]["records"] if r["n_sections"] == n // 2]
            inh = prev[0]["throttle"] if prev else None
        for p in exp["multistart"]["perturbations"]:
            jobs.append((m, p, n, inh))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_multistart_job, jobs))


# ------------------------------------------------------------------ re-time finalists
def retime(cfg, exp, throttle, accept=None) -> dict:
    """Re-time a finalist's shape to a tighter residual with bisection (labelled re-timed)."""
    case = Case(cfg, exp["route"])
    ev = Evaluator(case, budget=200)
    shape = normalise(throttle)
    opt = dict(exp["inner"], split="interpolate", accept_tol_s=accept or exp["retime_residual_s"],
               bisect_max_sims=200)
    r = solve_bisection(ev, shape, opt, nominal=float(np.max(throttle)))
    return {"found": r.found, "energy_kJ": r.run.energy_J / 1e3 if r.found else None,
            "residual_s": r.run.time_s - case.target if r.found else None, "sims": ev.sims,
            "message": r.message}


def retime_finalists(cfg, exp, ladder_res) -> list:
    rows = []
    counts = sorted({r["n_sections"] for m in ladder_res for r in ladder_res[m]["records"]})
    for n in counts:
        fin = {m: next((r for r in ladder_res[m]["records"] if r["n_sections"] == n and r["feasible"]), None)
               for m in ladder_res}
        for m, r in fin.items():
            if r is None:
                continue
            rt = retime(cfg, exp, r["throttle"])
            rows.append({"n_sections": n, "method": m, "energy_kJ": r["energy_kJ"], "residual_s": r["residual_s"],
                         "retimed_energy_kJ": rt["energy_kJ"], "retimed_residual_s": rt["residual_s"],
                         "retime_message": rt["message"]})
        sub = [x for x in rows if x["n_sections"] == n]
        orig = [x["method"] for x in sorted(sub, key=lambda x: x["energy_kJ"])]
        ret = [x["method"] for x in sorted(sub, key=lambda x: (x["retimed_energy_kJ"] is None, x["retimed_energy_kJ"] or 0))]
        for x in sub:
            x["rank_original"] = orig.index(x["method"]) + 1
            x["rank_retimed"] = ret.index(x["method"]) + 1
    return rows


# ------------------------------------------------------------------ matched finishing speed
def _finish_job(args):
    n, = args
    cfg = load_reference_config()
    exp = load_experiment()
    fs = exp["finish_speed"]
    case = Case(cfg, exp["route"])
    base = baseline_run(cfg)
    vb, tol = base.finish_speed_m_s, fs["tolerance_m_s"]

    def ok(r):
        return case.feasible(r) and abs(r.finish_speed_m_s - vb) <= tol + 1e-12

    ev = Evaluator(case, budget=fs["budget_sims"], feasible_fn=ok)
    seed = np.full(n, float(base.throttle[0]))
    seed_run = ev.run(seed)                                   # the feasible constant profile
    assert ok(seed_run)
    inner_opt = dict(exp["inner"], split="interpolate")

    def score(r):
        return r.energy_J + 1e6 * max(0.0, abs(r.finish_speed_m_s - vb) - tol)

    x0 = seed / np.max(seed)
    info = outer_search(ev, x0, perturbed_simplex(x0, exp["outer"]["perturbation"], exp["outer"]),
                        "bisection", inner_opt, exp["outer"], nominal=float(base.throttle[0]), score=score)
    b = ev.best
    return {"n_sections": n, "baseline_kJ": base.energy_J / 1e3, "baseline_finish_m_s": vb,
            "energy_kJ": b.energy_J / 1e3, "finish_m_s": b.finish_speed_m_s,
            "residual_s": b.time_s - case.target,
            "saving_percent": 100 * (base.energy_J - b.energy_J) / base.energy_J,
            "kept_baseline": bool(np.allclose(b.throttle, seed)), "sims": ev.sims, "stop": info["stop"],
            "throttle": b.throttle.tolist()}


def finish_speed(exp, workers=4) -> list:
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_finish_job, [(n,) for n in exp["finish_speed"]["n_sections"]]))


# ------------------------------------------------------------------ time-step sensitivity
def time_step(cfg, exp, profiles: dict) -> list:
    ts = exp["time_step"]
    rows = []
    for name, (route, vec) in profiles.items():
        ref_shifts = None
        for dt in ts["dt_s"]:
            ov = {"simulation.dt_s": dt, "controller.update_period_s": ts["update_period_s"],
                  "controller.horizon_s": ts["horizon_s"]}
            case = Case(cfg, route, ov)
            r = case.run(vec, record=True)
            d = r.trace_dict()
            shifts = gear_change_locations(d)
            if ref_shifts is None:
                ref_shifts = shifts
            row = {"profile": name, "dt_s": dt, "status": r.status_name, "time_s": r.time_s,
                   "time_error_s": r.time_s - case.target, "in_window": case.feasible(r),
                   "energy_kJ": r.energy_J / 1e3, "finish_speed_m_s": r.finish_speed_m_s,
                   "shifts": r.shifts,
                   "shift_x_m": [round(s[0], 1) for s in shifts],
                   "max_shift_shift_m": (max(abs(a[0] - b[0]) for a, b in zip(shifts, ref_shifts))
                                         if len(shifts) == len(ref_shifts) and shifts else None),
                   "same_shift_sequence": [s[1:] for s in shifts] == [s[1:] for s in ref_shifts]}
            if not case.feasible(r):
                ev = Evaluator(case, budget=200)
                opt = dict(exp["inner"], split="interpolate", bisect_max_sims=200)
                rr = solve_bisection(ev, normalise(vec), opt, nominal=float(np.max(vec)))
                row.update(retimed_found=rr.found,
                           retimed_energy_kJ=rr.run.energy_J / 1e3 if rr.found else None,
                           retimed_residual_s=rr.run.time_s - case.target if rr.found else None,
                           retimed_sims=ev.sims)
            rows.append(row)
    return rows


# ------------------------------------------------------------------ gear hunting
def rapid_reversals(gears: np.ndarray, t: np.ndarray, window_s: float) -> int:
    """Count shifts that are undone (opposite direction) within window_s."""
    idx = np.nonzero(np.diff(gears) != 0)[0] + 1
    n = 0
    for a, b in zip(idx, idx[1:]):
        if np.sign(gears[a] - gears[a - 1]) != np.sign(gears[b] - gears[b - 1]) and t[b] - t[a] <= window_s:
            n += 1
    return n


def hunting(cfg, exp) -> dict:
    h = exp["hunting"]
    out = {}
    for on in (True, False):
        case = Case(cfg, h["route"], {"controller.upshift_check_enabled": on})
        r = case.run([h["throttle"]], record=True)
        d = r.trace_dict()
        climb = d["grade_percent"] > 0
        out["enabled" if on else "disabled"] = {
            "trace": d, "shifts": r.shifts,
            "rapid_reversals": rapid_reversals(d["gear"][climb], d["t_s"][climb], h["reversal_window_s"]),
            "shifts_on_climb": int(np.sum(np.diff(d["gear"][climb]) != 0)),
            "time_s": r.time_s, "energy_kJ": r.energy_J / 1e3}
    return out
