"""Which declared assumptions move the headline saving, and by how much?

Each variant changes one input (or one documented alternative from the brief/recollection),
re-runs the constant-throttle baseline and the N-section profile search on that variant, and
reports the saving. Nothing here edits the reference configuration.
"""
from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor

from .config import Case, load_config
from .optimise import constant_baseline, optimise_profile

VARIANTS = {
    "reference (calibrated)": {},
    "time window +/-0.5 s (earlier recollection)": {"simulation.time_tolerance_s": 0.5},
    "no rotational inertia (brief literal)": {"vehicle.include_rotational_inertia": False},
    "uncalibrated car (A=2.0 m2, constant Crr 0.012)": {
        "vehicle.frontal_area_m2": 2.0, "vehicle.rolling_coefficient": 0.012,
        "vehicle.rolling_speed_factor_per_kmh": 0.0},
    "min engaged rpm 1000 (was 1200)": {"vehicle.min_engaged_rpm": 1000.0},
    "upshift margin 0.0 m/s (was 0.10)": {"controller.upshift_margin_m_s": 0.0},
    "forecast horizon 2.0 s (was 1.0)": {"controller.horizon_s": 2.0},
    "target time 110 s (was 120)": {"simulation.target_time_s": 110.0},
    "target time 130 s (was 120)": {"simulation.target_time_s": 130.0},
}


def run_variant(args):
    name, ov, route, n, cap, cfg_path = args
    cfg = load_config(cfg_path)
    case = Case(cfg, route, ov)
    opt = cfg["optimiser"]
    t0 = time.perf_counter()
    b = constant_baseline(case, opt)
    o = optimise_profile(case, n, opt, cap)
    out = {"variant": name, "route": route, "n_sections": n, "overrides": ov,
           "runtime_s": time.perf_counter() - t0, "budget_limited": o["budget_limited"]}
    if b["feasible"] and o["feasible"]:
        Eb, Ep = b["result"].energy_J, o["result"].energy_J
        out.update(baseline_kJ=Eb / 1e3, profile_kJ=Ep / 1e3, saving_percent=100 * (Eb - Ep) / Eb,
                   baseline_finish_kmh=b["result"].finish_speed_m_s * 3.6,
                   profile_finish_kmh=o["result"].finish_speed_m_s * 3.6,
                   throttle=o["result"].throttle.tolist())
    else:
        out.update(saving_percent=float("nan"))
    return out


def run_all(route="rolling", n=8, cap=2000, cfg_path=None, workers=4):
    jobs = [(k, v, route, n, cap, cfg_path) for k, v in VARIANTS.items()]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(run_variant, jobs))
