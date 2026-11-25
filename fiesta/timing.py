"""Whole-route simulation accounting and the inner time-matching problem.

For a fixed non-zero throttle shape q (normalised so max(q) = 1) the inner problem is to
find a scale k in (0, 1] such that   r(k) = arrival_time(k * q) - target   lies inside the
time window. Two solvers are provided on top of one shared :class:`Evaluator`:

* :func:`solve_nelder_mead`  - the historical 1-D Nelder-Mead on |r(k)|  (run A);
* :func:`solve_bisection`    - bounded bracketing + bisection with explicit handling of
  unfinished runs, gear-change jumps and same-sign endpoints (runs B, C).

Every whole-route simulation goes through Evaluator.run, which counts it, applies the
shared cache policy (key = applied throttle vector + full simulator configuration) and
stops the search with BudgetExhausted when the declared budget is spent.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.optimize import minimize

from .config import Case, SimResult


class BudgetExhausted(Exception):
    """Raised when a search asks for one simulation more than its budget."""


# ------------------------------------------------------------------ evaluator
@dataclass
class Evaluator:
    """Counts, caches and scores whole-route simulations for one search."""
    case: Case
    budget: int = 10**9
    use_cache: bool = True
    feasible_fn: Callable[[SimResult], bool] | None = None
    sims: int = 0
    cache_hits: int = 0
    feasible_runs: int = 0
    best: SimResult | None = None
    first_feasible_at: int | None = None
    history: list = field(default_factory=list)       # (sims, best energy J) on improvement
    offset: int = 0                                    # calls already charged (method C)
    t0: float = field(default_factory=time.perf_counter)

    def __post_init__(self):
        c = self.case
        h = hashlib.sha256()
        for a in (c.P, c.C, c.ratios, c.tq_rpm, c.tq_nm, c.sin_a, c.cos_a):
            h.update(np.ascontiguousarray(a, float).tobytes())
        h.update(repr((c.length, c.target, c.tol)).encode())
        self.config_key = h.hexdigest()[:16]
        self._cache: dict = {}

    def feasible(self, r: SimResult) -> bool:
        return self.feasible_fn(r) if self.feasible_fn else self.case.feasible(r)

    def run(self, throttle) -> SimResult:
        th = np.clip(np.asarray(throttle, float).ravel(), 0.0, 1.0)
        key = (th.tobytes(), self.config_key)
        if self.use_cache and key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        if self.sims >= self.budget:
            raise BudgetExhausted
        self.sims += 1
        r = self.case.run(th)
        if self.use_cache:
            self._cache[key] = r
        if self.feasible(r):
            self.feasible_runs += 1
            if self.first_feasible_at is None:
                self.first_feasible_at = self.offset + self.sims
            if self.best is None or r.energy_J < self.best.energy_J:
                self.best = r
                self.history.append((self.offset + self.sims, r.energy_J))
        return r

    @property
    def wall_s(self) -> float:
        return time.perf_counter() - self.t0


# ------------------------------------------------------------------ results
@dataclass
class Point:
    k: float
    label: str
    status: str
    residual: float          # +inf for an unfinished run (never arrives => late)
    shifts: int
    result: SimResult


@dataclass
class InnerResult:
    run: SimResult | None    # time-feasible run, or None ("no feasible scale found")
    k: float | None
    best_residual: float     # smallest |r| observed among finished runs
    sims: int                # whole-route simulations charged to this solve (cache misses)
    evaluations: int         # including cache hits
    message: str
    log: list = field(default_factory=list)
    points: list = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.run is not None


def _evaluate(ev: Evaluator, shape, k, label, pts, log):
    r = ev.run(k * shape)
    res = (r.time_s - ev.case.target) if r.finished else math.inf
    p = Point(float(k), label, r.status_name, res, r.shifts, r)
    pts.append(p)
    log.append(f"{label}: k={k:.6f} {r.status_name} r={res:+.4f}s shifts={r.shifts}")
    return p


def _hit(ev: Evaluator, p: Point, accept_tol: float | None = None) -> bool:
    """Accept only on the actual simulated result. accept_tol (optional, <= window) asks
    for a tighter residual than the feasibility window, to reduce scoring noise."""
    if not (p.result.finished and ev.case.feasible(p.result)):   # timing only
        return False
    return accept_tol is None or abs(p.residual) <= accept_tol


# ------------------------------------------------------------------ historical inner solver
def solve_nelder_mead(ev: Evaluator, shape: np.ndarray, opt: dict, nominal: float | None = None) -> InnerResult:
    """Run A: scalar Nelder-Mead on |r(k)| from two starting scales (v1 behaviour)."""
    s0 = ev.sims
    pts, log = [], []
    best = {"p": None}

    def f(kv):
        k = float(kv[0])
        kk = min(max(k, 0.0), 1.0)
        pen = 100.0 * abs(k - kk)
        if kk <= 0.0:
            return 2e3 + pen
        p = _evaluate(ev, shape, kk, "nelder-mead", pts, log)
        if not p.result.finished:
            return 1e3 + 1e3 * (1 - p.result.distance_m / ev.case.length) + pen
        if best["p"] is None or abs(p.residual) < abs(best["p"].residual):
            best["p"] = p
        return abs(p.residual) + pen

    a, b = opt["nm_start_scales"]
    res = minimize(f, x0=[a], method="Nelder-Mead",
                   options={"initial_simplex": np.array([[a], [b]]), "xatol": opt["nm_xatol"],
                            "fatol": opt["nm_fatol_s"], "maxfev": opt["nm_maxfev"]})
    p = best["p"]
    ok = p is not None and _hit(ev, p)
    return InnerResult(p.result if ok else None, p.k if ok else None,
                       abs(p.residual) if p else math.inf, ev.sims - s0, len(pts),
                       str(res.message) if ok else f"no feasible scale found ({res.message})", log, pts)


# ------------------------------------------------------------------ bracketing + bisection
def solve_bisection(ev: Evaluator, shape: np.ndarray, opt: dict, nominal: float | None = None) -> InnerResult:
    """Runs B/C: bounded bracketing and bisection on r(k).

    1. Labelled probes: the nominal (previously successful) scale, then steps outward in
       the direction its residual indicates (late -> larger k), doubling the step, until the
       residual changes sign or the admissible bound [k_min, 1] is reached.
    2. Unfinished runs are kept as failures; for bracketing they count as late (r = +inf),
       because a car that never arrives is later than any finite arrival.
    3. Every adjacent pair with opposite signs is a candidate bracket; brackets are tried
       in order of their midpoint's distance from the nominal scale (logged).
    4. Within a bracket, split at the midpoint (or, if opt['split'] == 'interpolate', at a
       safeguarded secant point). The actual simulated residual decides acceptance.
       If the bracket shrinks below opt['k_jump_width'] without a hit, r(k) jumps over the
       window there (typically a gear-policy change): the bracket is recorded as a jump and
       the next bracket is tried.
    5. If no usable bracket remains, sample midpoints of the intervals next to the point
       with the smallest |r| (same-sign endpoints can hide an interior crossing), until the
       per-solve budget is spent. Then return "no feasible scale found" with the best |r|.
    """
    s0 = ev.sims
    k_lo, k_hi = opt["k_min"], 1.0
    budget = opt["bisect_max_sims"]
    pts: list[Point] = []
    log: list[str] = []
    tol = ev.case.tol
    acc = opt.get("accept_tol_s")       # None = accept anywhere in the window (runs B, C)
    jumps: list[tuple[float, float]] = []

    def done(p, msg):
        return InnerResult(p.result, p.k, abs(p.residual), ev.sims - s0, len(pts), msg, log, pts)

    def fail(msg):
        ok = [p for p in pts if _hit(ev, p)]
        if ok:  # tighter accept_tol not reached, but an in-window run exists
            return done(min(ok, key=lambda p: abs(p.residual)), f"in window, tighter target not reached ({msg})")
        fin = [p for p in pts if math.isfinite(p.residual)]
        br = min((abs(p.residual) for p in fin), default=math.inf)
        return InnerResult(None, None, br, ev.sims - s0, len(pts), f"no feasible scale found: {msg}", log, pts)

    def spent():
        return ev.sims - s0 >= budget

    def probe(k, label):
        k = float(min(max(k, k_lo), k_hi))
        for p in pts:
            if abs(p.k - k) < 1e-12:
                return p
        return _evaluate(ev, shape, k, label, pts, log)

    k_nom = float(np.clip(nominal if nominal else opt["k_nominal"], k_lo, k_hi))
    p = probe(k_nom, "nominal")
    if _hit(ev, p, acc):
        return done(p, "hit at nominal scale")

    # 1. directional expansion until the residual changes sign or a bound is reached
    step = opt["probe_step"]
    while not spent():
        if any(q.residual > 0 for q in pts) and any(q.residual < 0 for q in pts):
            break
        if p.residual > 0:
            if p.k >= k_hi:
                break
            p = probe(min(p.k * (1 + step), k_hi), f"probe up {step:.0%}")
        else:
            if p.k <= k_lo:
                break
            p = probe(max(p.k * (1 - step), k_lo), f"probe down {step:.0%}")
        if _hit(ev, p, acc):
            return done(p, "hit while bracketing")
        step = min(step * 2, 0.9)

    def brackets():
        srt = sorted(pts, key=lambda q: q.k)
        out = []
        for a, b in zip(srt, srt[1:]):
            if (a.residual > 0) != (b.residual > 0) and b.k - a.k > opt["k_jump_width"]:
                out.append((a, b))
        out.sort(key=lambda ab: (abs(0.5 * (ab[0].k + ab[1].k) - k_nom), ab[0].k))
        return out

    while not spent():
        cands = brackets()
        if cands:
            # 3./4. bisect the bracket closest to the nominal scale
            a, b = cands[0]
            log.append(f"bracket chosen [{a.k:.6f}, {b.k:.6f}] (closest to nominal of {len(cands)})")
            while not spent():
                if (opt.get("split") == "interpolate" and math.isfinite(a.residual)
                        and math.isfinite(b.residual)):
                    km = a.k + (b.k - a.k) * a.residual / (a.residual - b.residual)
                    km = min(max(km, a.k + 0.1 * (b.k - a.k)), b.k - 0.1 * (b.k - a.k))
                    label = "interpolate"
                else:
                    km, label = 0.5 * (a.k + b.k), "bisect"
                m = probe(km, label)
                if _hit(ev, m, acc):
                    return done(m, f"hit by {label}")
                if (m.residual > 0) == (a.residual > 0):
                    a = m
                else:
                    b = m
                if b.k - a.k <= opt["k_jump_width"]:
                    jumps.append((a.k, b.k))
                    log.append(f"jump: r goes {a.residual:+.3f} -> {b.residual:+.3f} s within "
                               f"dk={b.k - a.k:.2e} (shifts {a.shifts}->{b.shifts}); window skipped")
                    break
            continue
        # 5. no usable bracket: subdivide next to the most promising finished point
        fin = sorted([q for q in pts if math.isfinite(q.residual)], key=lambda q: abs(q.residual))
        if not fin:
            m = probe(min(1.0, max(q.k for q in pts) * 1.5), "explore (nothing finished yet)")
            if m.k >= k_hi and not math.isfinite(m.residual):
                return fail("no finished run even at the upper bound")
        else:
            srt = sorted(pts, key=lambda q: q.k)
            c = fin[0]
            i = srt.index(c)
            gaps = [srt[j] for j in (i - 1, i + 1) if 0 <= j < len(srt)
                    and abs(srt[j].k - c.k) > 2 * opt["k_jump_width"]]
            if not gaps:
                break
            n = max(gaps, key=lambda q: abs(q.k - c.k))
            m = probe(0.5 * (c.k + n.k), "subdivide")
        if _hit(ev, m, acc):
            return done(m, "hit by subdivision")
    if jumps and not spent():
        return fail(f"r(k) jumps over the window at {len(jumps)} place(s)")
    return fail("budget spent" if spent() else "no bracket and nothing left to subdivide")


SOLVERS = {"nelder-mead": solve_nelder_mead, "bisection": solve_bisection}
