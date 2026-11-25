"""Flat-road full-load acceleration, steady top speed and gear-hunting demo."""
from __future__ import annotations

import numpy as np

from . import model as M
from .config import Case

MPH = 0.44704
KMH = 1 / 3.6


def perf_case(cfg: dict, policy: dict, route: str = "flat", length_m: float = 3000.0) -> Case:
    ov = {
        "vehicle.launch_rpm": policy["launch_rpm"],
        "vehicle.include_rotational_inertia": policy["include_rotational_inertia"],
        f"routes.{route}.length_m": length_m,
        "simulation.max_time_s": 600.0,
    }
    return Case(cfg, route, ov)


def crossing_time(t, v, target):
    idx = np.nonzero(v >= target)[0]
    if len(idx) == 0:
        return np.nan
    k = idx[0]
    if k == 0:
        return float(t[0])
    return float(t[k - 1] + (target - v[k - 1]) * (t[k] - t[k - 1]) / (v[k] - v[k - 1]))


def acceleration(cfg: dict, policy: dict) -> dict:
    case = perf_case(cfg, policy)
    C = case.C.copy()
    C[M.C_MODE] = M.MODE_PERFORMANCE
    C[M.C_SHIFT_RPM] = policy["shift_rpm"]
    C[M.C_SHIFT_T] = policy["shift_time_s"]
    r = case.run([1.0], record=True, C=C)
    d = r.trace_dict()
    t, v = d["t_s"], d["v_m_s"]
    return {
        "zero_to_60_mph_s": crossing_time(t, v, 60 * MPH),
        "zero_to_62_mph_s": crossing_time(t, v, 62 * MPH),
        "zero_to_100_kmh_s": crossing_time(t, v, 100 * KMH),
        "shift_speeds_kmh": [float(v[k] / KMH) for k in np.nonzero(np.diff(d["gear"]) > 0)[0]],
        "trace": d,
        "case": case,
    }


def steady_top_speed(case: Case) -> dict:
    """Highest speed in any gear where full-load tractive force equals flat-road
    resistance, with engine speed inside the rpm limit. Power balance is reported
    for the physical sanity check."""
    P = case.P
    best = {"v_m_s": 0.0}
    per_gear = []
    vs = np.linspace(1.0, 80.0, 79001)
    for g in range(1, len(case.ratios) + 1):
        Ff = np.array([M.full_force(v, g, P, case.ratios, case.tq_rpm, case.tq_nm) for v in vs])
        Fr = np.array([M.resistance(v, P, 0.0, 1.0) for v in vs])
        rpm = vs / P[M.V_R] * case.ratios[g - 1] * P[M.V_FD] / M.TWO_PI_60
        surplus = (Ff - Fr) >= 0.0
        surplus &= rpm <= P[M.V_RPMLIM]
        if not surplus.any():
            per_gear.append({"gear": g, "v_max_kmh": None})
            continue
        k = np.nonzero(surplus)[0][-1]
        v = float(vs[k])
        rpm_k = float(rpm[k])
        limited = "rpm limit" if rpm_k > P[M.V_RPMLIM] - 30 else "power/drag balance"
        T = M.full_torque(rpm_k, case.tq_rpm, case.tq_nm, P[M.V_RPMLIM])
        rec = {
            "gear": g, "v_max_kmh": v / KMH, "v_max_mph": v / MPH, "engine_rpm": rpm_k,
            "limited_by": limited,
            "engine_power_kW": T * rpm_k * M.TWO_PI_60 / 1e3,
            "wheel_power_kW": T * rpm_k * M.TWO_PI_60 * P[M.V_ETA] / 1e3,
            "resistive_power_kW": float(Fr[k]) * v / 1e3,
            "aero_share": (0.5 * P[M.V_RHO] * P[M.V_CD] * P[M.V_A] * v * v) / float(Fr[k]),
        }
        per_gear.append(rec)
        if v > best["v_m_s"]:
            best = dict(rec, v_m_s=v)
    return {"best": best, "per_gear": per_gear}


def simulated_top_speed(cfg: dict, policy: dict, seconds: float = 400.0) -> dict:
    """Confirm the steady top speed by integrating a long full-throttle run, upshifting
    only when the next gear still gives more tractive force than resistance."""
    case = perf_case(cfg, policy, length_m=40000.0)
    C = case.C.copy()
    C[M.C_MODE] = M.MODE_PERFORMANCE
    C[M.C_SHIFT_RPM] = policy["shift_rpm"]
    C[M.C_SHIFT_T] = policy["shift_time_s"]
    C[M.C_MAXT] = seconds
    r = case.run([1.0], record=True, C=C)
    d = r.trace_dict()
    return {"v_end_kmh": float(d["v_m_s"][-1] / KMH), "gear_end": int(d["gear"][-1]),
            "accel_end_m_s2": float(np.nanmean(d["accel_m_s2"][-20:-1]))}


def hunting_demo(cfg: dict, throttle: float, enabled: bool) -> dict:
    case = Case(cfg, "steep_hill", {"controller.upshift_check_enabled": enabled})
    r = case.run([throttle], record=True)
    d = r.trace_dict()
    seg = d["grade_percent"] > 0
    g = d["gear"][:-1]
    changes = int(np.sum(np.diff(d["gear"][seg]) != 0))
    return {"result": r, "trace": d, "shifts_on_climb": changes, "shifts": r.shifts,
            "time_s": r.time_s, "energy_kJ": r.energy_J / 1e3, "status": r.status_name}


def calibrate(cfg: dict, policy_name: str = "documented") -> dict:
    """Fit the two unpublished quantities to Ford's two published figures, in sequence:
    1. frontal area -> steady top speed = Ford top speed (acceleration barely affects it);
    2. shift time (torque interruption per upshift) -> 0-100 km/h = Ford 0-100 km/h.
    Returns the fitted values and the resulting errors; does not edit the config file."""
    import copy
    c = copy.deepcopy(cfg)
    ref = c["performance_test"]["reference"]
    lo, hi = 1.5, 3.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        c["vehicle"]["frontal_area_m2"] = mid
        if steady_top_speed(Case(c, "flat"))["best"]["v_max_kmh"] > ref["top_speed_kmh"]:
            lo = mid
        else:
            hi = mid
    area = 0.5 * (lo + hi)
    c["vehicle"]["frontal_area_m2"] = area
    pol = dict(c["performance_test"]["policies"][policy_name])
    lo, hi = 0.0, 1.5
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        pol["shift_time_s"] = mid
        if acceleration(c, pol)["zero_to_100_kmh_s"] < ref["zero_to_100_kmh_s"]:
            lo = mid
        else:
            hi = mid
    pol["shift_time_s"] = 0.5 * (lo + hi)
    a = acceleration(c, pol)
    t = steady_top_speed(Case(c, "flat"))["best"]
    return {"frontal_area_m2": area, "shift_time_s": pol["shift_time_s"],
            "zero_to_100_kmh_s": a["zero_to_100_kmh_s"], "zero_to_60_mph_s": a["zero_to_60_mph_s"],
            "top_speed_kmh": t["v_max_kmh"], "top_speed_mph": t["v_max_mph"]}
