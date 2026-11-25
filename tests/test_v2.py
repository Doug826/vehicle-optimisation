import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from fiesta import model as M
from fiesta import performance as perf
from fiesta.config import Case, SimResult
from fiesta.experiments import load_experiment, rapid_reversals, ref_vectors
from fiesta.reference import load_reference_config, parity_ok, replay_reference
from fiesta.search import normalise, refine, run_ladder
from fiesta.timing import Evaluator, solve_bisection, solve_nelder_mead

CFG = load_reference_config()
EXP = load_experiment()


@pytest.fixture(scope="module")
def rolling():
    return Case(CFG, "rolling")


# ------------------------------------------------------------------ reference & invariants
def test_reference_replay_parity():
    rows = replay_reference(CFG)
    assert parity_ok(rows), rows


def test_hand_calculated_shaft_work():
    assert M.shaft_energy(100.0, 200.0, 2.0) == pytest.approx(40_000.0)
    rpm = 200.0 / M.TWO_PI_60
    assert 100.0 * rpm * M.TWO_PI_60 * 2.0 == pytest.approx(40_000.0)


def test_split_section_equivalence(rolling):
    v = np.array(ref_vectors()["rolling/N8"])
    a = rolling.run(v, record=True)
    b = rolling.run(refine(v), record=True)
    c = rolling.run(refine(refine(v)), record=True)
    for r in (b, c):
        assert r.energy_J == a.energy_J and r.time_s == a.time_s and r.shifts == a.shifts
        assert np.array_equal(r.trace, a.trace, equal_nan=True)


def test_update_period_default_reproduces_v1(rolling):
    c2 = Case(CFG, "rolling", {"controller.update_period_s": 0.1})
    v = ref_vectors()["rolling/N8"]
    assert c2.run(v).energy_J == rolling.run(v).energy_J


def test_update_period_must_be_whole_steps():
    with pytest.raises(ValueError):
        Case(CFG, "rolling", {"simulation.dt_s": 0.03, "controller.update_period_s": 0.1})


def test_finer_step_keeps_controller_period():
    c = Case(CFG, "rolling", {"simulation.dt_s": 0.025, "controller.update_period_s": 0.1})
    assert c.C[M.C_CTRL_EVERY] == 4 and c.C[M.C_NH] == 40
    d = c.run([0.44], record=True).trace_dict()
    decided = np.isfinite(d["fc_hold_rpm"][:-1])
    assert decided[::4].all() and not decided[1::4].any()


# ------------------------------------------------------------------ drivetrain physics
def test_ideal_locked_drivetrain_shaft_power_equals_wheel_power():
    c = Case(CFG, "rolling", {"vehicle.driveline_efficiency": 1.0})
    d = c.run([0.5], record=True).trace_dict()
    locked = (d["clutch_slip"][:-1] == 0) & (d["torque_Nm"][:-1] > 0)
    shaft = d["shaft_power_W"][:-1][locked]
    wheel = d["wheel_force_N"][:-1][locked] * d["v_m_s"][:-1][locked]
    assert np.allclose(shaft, wheel, rtol=1e-12)


def test_lower_rpm_alone_saves_no_work():
    """Same wheel force and road speed in 4th and 5th: shaft power identical (eta = 1)."""
    c = Case(CFG, "rolling", {"vehicle.driveline_efficiency": 1.0})
    P, v, F = c.P, 20.0, 800.0
    powers = []
    for g in (4, 5):
        G = c.ratios[g - 1] * P[M.V_FD]
        T = F * P[M.V_R] / (G * P[M.V_ETA])            # torque needed for that wheel force
        rpm = M.locked_rpm(v, g, P, c.ratios)
        powers.append(T * rpm * M.TWO_PI_60)
    assert powers[0] == pytest.approx(powers[1], rel=1e-12) == pytest.approx(F * v, rel=1e-12)


def test_performance_calibration_still_holds():
    a = perf.acceleration(CFG, CFG["performance_test"]["policies"]["documented"])
    assert abs(a["zero_to_100_kmh_s"] - 13.3) <= 0.1
    top = perf.steady_top_speed(Case(CFG, "flat"))["best"]
    assert abs(top["v_max_kmh"] - 168.0) < 0.8


# ------------------------------------------------------------------ inner solver regression cases
def test_bracketed_target_found(rolling):
    ev = Evaluator(rolling)
    r = solve_bisection(ev, np.ones(4), EXP["inner"])
    assert r.found and abs(r.run.time_s - 120) <= 0.1 and r.sims <= 12


def test_bisection_uses_fewer_sims_than_nelder_mead_on_same_shape(rolling):
    shape = normalise(ref_vectors()["rolling/N8"])
    a = solve_nelder_mead(Evaluator(rolling), shape, EXP["inner"])
    b = solve_bisection(Evaluator(rolling), shape, EXP["inner"])
    assert a.found and b.found and b.sims < a.sims


def test_unreachable_target_reports_best_residual():
    c = Case(CFG, "rolling", {"simulation.target_time_s": 30.0})
    ev = Evaluator(c)
    r = solve_bisection(ev, np.ones(4), EXP["inner"])
    assert not r.found and r.message.startswith("no feasible scale found")
    assert math.isfinite(r.best_residual) and r.best_residual > 30
    assert r.sims <= EXP["inner"]["bisect_max_sims"]


def test_unfinished_low_throttle_run_is_late_failure(rolling):
    r = rolling.run(0.02 * np.ones(4))
    assert not r.finished and r.status_name in ("stalled", "timeout")
    ev = Evaluator(rolling)
    res = solve_bisection(ev, np.ones(4), dict(EXP["inner"], k_nominal=0.02))
    assert res.points[0].status != "finished" and res.points[0].residual == math.inf
    assert res.found                                   # it probes upward and recovers


class _FakeCase:
    """r(k) with a gear-switch jump at k = 0.5 that skips the time band entirely."""
    target, tol, length = 120.0, 0.1, 1609.344

    def __init__(self):
        self.P = self.C = self.ratios = self.tq_rpm = self.tq_nm = self.sin_a = self.cos_a = np.zeros(1)

    def run(self, th):
        k = float(np.max(th))
        t = 150.0 - 40.0 * k if k < 0.5 else 117.0 - 10.0 * (k - 0.5)   # 130 -> 117 jump at 0.5
        return SimResult(M.FINISHED, t, self.length, 10.0, 1e5 * k, 4 if k < 0.5 else 3, 1, np.asarray(th))

    def feasible(self, r):
        return r.finished and abs(r.time_s - self.target) <= self.tol + 1e-9


def test_synthetic_gear_jump_skipping_band_is_reported():
    ev = Evaluator(_FakeCase(), budget=200)
    r = solve_bisection(ev, np.ones(1), dict(EXP["inner"], bisect_max_sims=60))
    assert not r.found
    assert any(line.startswith("jump:") for line in r.log)
    assert r.best_residual == pytest.approx(3.0, abs=1e-3)   # best is just after the jump (117 s)


def test_nelder_mead_does_not_claim_success_across_jump():
    ev = Evaluator(_FakeCase(), budget=200)
    r = solve_nelder_mead(ev, np.ones(1), EXP["inner"])
    assert not r.found


# ------------------------------------------------------------------ cache & budget
def test_cache_keys_on_applied_throttle_and_config(rolling):
    ev = Evaluator(rolling)
    ev.run(np.array([0.4, 0.5]))
    ev.run(np.array([0.4, 0.5]))
    assert ev.sims == 1 and ev.cache_hits == 1
    other = Evaluator(Case(CFG, "rolling", {"vehicle.mass_kg": 1100.0}))
    assert other.config_key != ev.config_key


def test_budget_is_enforced(rolling):
    from fiesta.timing import BudgetExhausted
    ev = Evaluator(rolling, budget=2)
    ev.run([0.4]); ev.run([0.5])
    with pytest.raises(BudgetExhausted):
        ev.run([0.6])


def test_method_c_replays_inherited_profile_identically():
    exp = json.loads(json.dumps(EXP))
    exp["budget_sims"].update({"1": 100, "2": 300, "4": 400})
    base = Case(CFG, "rolling").run(ref_vectors()["rolling/constant"])
    recs = run_ladder(CFG, exp, "C", [1, 2, 4], baseline=base)
    assert all(r["inherited_replay_identical"] for r in recs[1:])
    assert recs[2]["cumulative_sims"] == sum(r["sims"] for r in recs)
    # the inherited incumbent can only improve: energy is monotone non-increasing with N
    e = [r["energy_kJ"] for r in recs]
    assert e[1] <= e[0] + 1e-9 and e[2] <= e[1] + 1e-9


def test_rapid_reversal_counter():
    t = np.arange(10) * 0.5
    g = np.array([3, 3, 4, 3, 3, 3, 3, 3, 4, 4])     # up then down within 0.5 s, then up later
    assert rapid_reversals(g, t, 2.0) == 1
    assert rapid_reversals(g, t, 0.25) == 0
