import math

import numpy as np
import pytest

from fiesta import model as M
from fiesta.config import Case, SimResult, load_config, time_ok
from fiesta.optimise import constant_baseline, optimise_profile
from fiesta import performance as perf

CFG = load_config()


@pytest.fixture(scope="module")
def rolling():
    return Case(CFG, "rolling")


# ---------------------------------------------------------------- energy accounting
def test_known_energy_integral():
    # 100 N m at 200 rad/s for 2 s = 40 kJ
    assert M.shaft_energy(100.0, 200.0, 2.0) == pytest.approx(40_000.0, rel=1e-15)
    # same integral expressed in rpm, as the simulator does it: P = T * rpm * 2pi/60
    rpm = 200.0 / M.TWO_PI_60
    assert 100.0 * rpm * M.TWO_PI_60 * 2.0 == pytest.approx(40_000.0, rel=1e-12)


def test_trip_meter_excludes_forecasts(rolling):
    """Trip energy equals the sum of applied shaft power x step length only."""
    r = rolling.run([0.45], record=True)
    d = r.trace_dict()
    dt = np.diff(d["t_s"])
    E = np.sum(d["shaft_power_W"][:-1] * dt)
    assert E == pytest.approx(r.energy_J, rel=1e-12)
    assert np.allclose(np.diff(d["energy_J"]), d["shaft_power_W"][:-1] * dt, rtol=1e-9, atol=1e-9)


def test_work_energy_theorem_flat_no_inertia():
    """With rotational inertia off, sum (F_trac - R) dx must equal 1/2 m v_f^2 exactly
    under the constant-acceleration step (checks the integrator and finish fraction)."""
    c = Case(CFG, "rolling", {"vehicle.include_rotational_inertia": False})
    r = c.run([0.5, 0.2, 0.8, 0.0], record=True)
    d = r.trace_dict()
    dx = np.diff(d["x_m"])
    v = d["v_m_s"][:-1]
    sin_t = np.sin(np.arctan(d["grade_percent"][:-1] / 100))
    cos_t = np.cos(np.arctan(d["grade_percent"][:-1] / 100))
    m, g = c.P[M.V_MASS], c.P[M.V_G]
    crr = c.P[M.V_CRR] * (1 + c.P[M.V_CRR_V] * v * 3.6)
    R = m * g * sin_t + crr * m * g * cos_t + 0.5 * c.P[M.V_RHO] * c.P[M.V_CD] * c.P[M.V_A] * v ** 2
    net = d["wheel_force_N"][:-1] - R
    held = (v <= 0) & (net < 0)
    work = np.sum(np.where(held, 0.0, net) * dx)
    assert work == pytest.approx(0.5 * m * r.finish_speed_m_s ** 2, rel=1e-9)


def test_shaft_energy_covers_wheel_work(rolling):
    """Shaft work >= wheel work / efficiency (equal when clutch locked, more when slipping)."""
    r = rolling.run([0.45], record=True)
    d = r.trace_dict()
    dt = np.diff(d["t_s"])
    wheel = np.sum(d["wheel_force_N"][:-1] * d["v_m_s"][:-1] * dt)
    assert r.energy_J * rolling.P[M.V_ETA] >= wheel * (1 - 1e-9)
    locked = d["clutch_slip"][:-1] == 0
    lhs = np.sum((d["shaft_power_W"][:-1] * dt)[locked]) * rolling.P[M.V_ETA]
    rhs = np.sum((d["wheel_force_N"][:-1] * d["v_m_s"][:-1] * dt)[locked])
    assert lhs == pytest.approx(rhs, rel=1e-9)


# ---------------------------------------------------------------- start, finish, timing
def test_standing_start(rolling):
    r = rolling.run([0.4], record=True)
    d = r.trace_dict()
    assert d["v_m_s"][0] == 0.0 and d["x_m"][0] == 0.0 and d["gear"][0] == 1
    assert d["clutch_slip"][0] == 1 and d["engine_rpm"][0] == pytest.approx(850.0)
    assert np.all(d["v_m_s"] >= 0) and np.all(np.diff(d["x_m"]) >= 0)
    assert d["v_m_s"][1] > 0


def test_partial_final_step_charged(rolling):
    r = rolling.run([0.4], record=True)
    d = r.trace_dict()
    dt = rolling.C[M.C_DT]
    tau = d["t_s"][-1] - d["t_s"][-2]
    assert 0 < tau <= dt + 1e-12
    assert d["x_m"][-1] == rolling.length
    assert r.energy_J - d["energy_J"][-2] == pytest.approx(d["shaft_power_W"][-2] * tau, rel=1e-12)
    # the fraction is exact: position at tau from the last full step reaches the line
    x0, v0, a = d["x_m"][-2], d["v_m_s"][-2], d["accel_m_s2"][-2]
    assert x0 + v0 * tau + 0.5 * a * tau ** 2 == pytest.approx(rolling.length, abs=1e-9)
    assert abs(round(d["t_s"][-2] / dt) * dt - d["t_s"][-2]) < 1e-6


@pytest.mark.parametrize("t,ok", [(120.1, True), (119.9, True), (120.0, True),
                                  (120.1 + 1e-6, False), (119.9 - 1e-6, False)])
def test_timing_window_edges(t, ok):
    assert time_ok(t, 120.0, 0.1) is ok


def test_window_independent_of_step(rolling):
    c = Case(CFG, "rolling", {"simulation.time_tolerance_s": 0.5})
    fake = SimResult(M.FINISHED, 120.45, c.length, 10.0, 1.0, 0, 0, np.ones(1))
    assert c.feasible(fake) and not rolling.feasible(fake)
    assert not rolling.feasible(SimResult(M.TIMEOUT, 120.0, 100.0, 0, 1.0, 0, 0, np.ones(1)))


def test_all_zero_profile_cannot_finish(rolling):
    r = rolling.run(np.zeros(8))
    assert r.status_name == "stalled" and r.distance_m == 0.0


# ---------------------------------------------------------------- replay invariance
def test_constant_throttle_same_drive_any_section_count(rolling):
    ref = rolling.run([0.43], record=True)
    for n in (2, 4, 8, 16):
        r = rolling.run(np.full(n, 0.43), record=True)
        assert r.time_s == ref.time_s and r.energy_J == ref.energy_J and r.shifts == ref.shifts
        assert np.array_equal(r.trace, ref.trace, equal_nan=True)


def test_one_section_optimum_matches_baseline(rolling):
    opt = CFG["optimiser"]
    b = constant_baseline(rolling, opt)
    o = optimise_profile(rolling, 1, opt, 6)
    assert b["feasible"] and o["feasible"]
    assert o["result"].energy_J == pytest.approx(b["result"].energy_J, rel=0.01)


# ---------------------------------------------------------------- gear logic
def test_rpm_limits_respected(rolling):
    for th in (0.3, 0.6, 1.0):
        d = rolling.run([th], record=True).trace_dict()
        assert np.nanmax(d["engine_rpm"]) <= rolling.P[M.V_RPMLIM] + 1e-6
        assert np.all(np.isin(d["gear"], [1, 2, 3, 4, 5]))


def test_forecast_columns_recorded(rolling):
    d = rolling.run([0.45], record=True).trace_dict()
    assert np.isfinite(d["fc_hold_rpm"][:-1]).all()
    assert np.isfinite(d["fc_hold_dv_m_s"][:-1]).all()


def test_upshift_margin_prevents_hunting():
    on = perf.hunting_demo(CFG, 0.7, True)
    off = perf.hunting_demo(CFG, 0.7, False)
    assert on["shifts_on_climb"] <= 3
    assert off["shifts_on_climb"] > 20 * max(on["shifts_on_climb"], 1)


# ---------------------------------------------------------------- physical plausibility
REF = CFG["performance_test"]["reference"]


def test_top_speed_physically_consistent():
    top = perf.steady_top_speed(Case(CFG, "flat"))
    b = top["best"]
    # calibrated: within 0.5 mph of Ford's 104 mph and of 168 km/h
    assert abs(b["v_max_mph"] - REF["top_speed_mph"]) <= 0.5
    assert abs(b["v_max_kmh"] - REF["top_speed_kmh"]) / 1.609344 <= 0.5
    # it is a power/drag balance, not an rpm limit, and aero dominates at that speed
    assert b["limited_by"] == "power/drag balance"
    assert b["wheel_power_kW"] == pytest.approx(b["resistive_power_kW"], rel=0.01)
    assert b["aero_share"] > 0.8
    # engine near (not above) its 60 kW rating
    assert 50.0 < b["engine_power_kW"] <= 60.0 + 1e-6
    # gear maxima increase monotonically and gears 1-3 are rpm-limited
    vmax = [g["v_max_kmh"] for g in top["per_gear"]]
    assert vmax[0] < vmax[1] < vmax[2] < max(vmax[3], vmax[4])
    assert all(g["limited_by"] == "rpm limit" for g in top["per_gear"][:3])


def test_simulated_top_speed_matches_steady_state():
    top = perf.steady_top_speed(Case(CFG, "flat"))["best"]["v_max_kmh"]
    sim = perf.simulated_top_speed(CFG, CFG["performance_test"]["policies"]["documented"])
    assert sim["v_end_kmh"] == pytest.approx(top, abs=0.5)
    assert abs(sim["accel_end_m_s2"]) < 1e-3


def test_acceleration_physically_plausible():
    pol = CFG["performance_test"]["policies"]["documented"]
    a = perf.acceleration(CFG, pol)
    t60, t62, t100 = a["zero_to_60_mph_s"], a["zero_to_62_mph_s"], a["zero_to_100_kmh_s"]
    assert t60 < t62 <= t100 < t62 + 0.2       # 62 mph = 99.8 km/h
    assert abs(t100 - REF["zero_to_100_kmh_s"]) <= 0.1      # calibrated to Ford's 13.3 s
    assert 12.0 < t60 < 13.0                                  # 0-60 mph is reported, not fitted
    # bound from power: KE gain at 100 km/h cannot exceed peak wheel power x time
    m = CFG["vehicle"]["mass_kg"]
    ke = 0.5 * m * (100 / 3.6) ** 2
    assert ke / (60e3 * CFG["vehicle"]["driveline_efficiency"]) < t100
    # shifts happen at the rpm limit: 6250 rpm in 1st and 2nd
    r, fd = CFG["vehicle"]["wheel_radius_m"], CFG["vehicle"]["final_drive"]
    for g, v_kmh in zip((0, 1), a["shift_speeds_kmh"][:2]):
        v_lim = 6250 * M.TWO_PI_60 / (CFG["vehicle"]["gear_ratios"][g] * fd) * r * 3.6
        assert v_kmh == pytest.approx(v_lim, abs=1.5)


def test_rotational_inertia_slows_acceleration():
    base = dict(CFG["performance_test"]["policies"]["documented"])
    with_i = perf.acceleration(CFG, base)["zero_to_100_kmh_s"]
    base["include_rotational_inertia"] = False
    without = perf.acceleration(CFG, base)["zero_to_100_kmh_s"]
    assert without < with_i


def test_grade_conversion_uses_atan():
    c = Case(CFG, "rolling")
    assert c.sin_a[4] == pytest.approx(math.sin(math.atan(0.06)))


def test_calibration_reproduces_config():
    """Re-fitting from the config's own inputs returns the values stored in it."""
    r = perf.calibrate(CFG)
    assert r["frontal_area_m2"] == pytest.approx(CFG["vehicle"]["frontal_area_m2"], abs=5e-4)
    assert r["shift_time_s"] == pytest.approx(
        CFG["performance_test"]["policies"]["documented"]["shift_time_s"], abs=5e-3)


def test_calibrated_area_is_physically_plausible():
    box = 1.722 * 1.495  # Ford overall width (no mirrors) x height, p.12
    assert 0.75 <= CFG["vehicle"]["frontal_area_m2"] / box <= 0.88


def test_rolling_resistance_rises_with_speed():
    c = Case(CFG, "flat")
    assert M.resistance(46.7, c.P, 0.0, 1.0) > M.resistance(13.4, c.P, 0.0, 1.0)
    crr_trip = c.P[M.V_CRR] * (1 + c.P[M.V_CRR_V] * 48)
    assert 0.011 < crr_trip < 0.014   # close to the brief's constant 0.012 at trip speeds


# ---------------------------------------------------------------- ratio optimiser
def test_ratio_method_keeps_throttles_inside_bounds(rolling):
    opt = CFG["optimiser"]
    o = optimise_profile(rolling, 2, opt, 40)
    th = o["result"].throttle
    assert o["feasible"] and rolling.feasible(o["result"])
    assert np.all(th > opt["throttle_min"]) and np.all(th < opt["throttle_max"])
    assert th == pytest.approx(np.array(o["best_ratios"]) / o["best_ratios"][0] * th[0])


def test_ratio_start_simplex_is_reproducible_and_clipped(rolling):
    opt = CFG["optimiser"]
    a = np.array(optimise_profile(rolling, 4, opt, 6)["outer_start_simplex"])
    b = np.array(optimise_profile(rolling, 4, opt, 6)["outer_start_simplex"])
    assert a.shape == (5, 4) and np.array_equal(a, b)
    assert a.min() >= opt["ratio_clip"][0] and a.max() <= opt["ratio_clip"][1]
    assert np.array_equal(a[0], np.ones(4))


def test_shape_method_still_selectable(rolling):
    opt = {**CFG["optimiser"], "method": "shape"}
    b = constant_baseline(rolling, opt)
    o = optimise_profile(rolling, 1, opt, 6)
    assert b["feasible"] and o["feasible"] and "outer_legs" in o
