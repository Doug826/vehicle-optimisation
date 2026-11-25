"""Load the machine-readable configuration and wrap the compiled simulator."""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import model as M

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "vehicle_route.json"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class SimResult:
    status: int
    time_s: float
    distance_m: float
    finish_speed_m_s: float
    energy_J: float
    shifts: int
    steps: int
    throttle: np.ndarray
    trace: np.ndarray | None = None

    @property
    def finished(self) -> bool:
        return self.status == M.FINISHED

    @property
    def status_name(self) -> str:
        return M.STATUS_NAMES[self.status]

    def trace_dict(self) -> dict:
        return {c: self.trace[:, k] for k, c in enumerate(M.TRACE_COLS)}


def time_ok(t: float, target: float, tol: float) -> bool:
    """Timing window: accept |t - target| <= tol (inclusive edge, float-safe)."""
    return abs(t - target) <= tol + 1e-9


@dataclass
class Case:
    """A fully resolved vehicle + route + controller combination."""
    cfg: dict
    route: str = "rolling"
    overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        self.cfg = copy.deepcopy(self.cfg)
        for dotted, val in self.overrides.items():
            d = self.cfg
            keys = dotted.split(".")
            for k in keys[:-1]:
                d = d[k]
            d[keys[-1]] = val
        veh, sim, ctl = self.cfg["vehicle"], self.cfg["simulation"], self.cfg["controller"]
        rt = self.cfg["routes"][self.route]
        P = np.zeros(M.N_VEH)
        P[M.V_MASS] = veh["mass_kg"]
        P[M.V_R] = veh["wheel_radius_m"]
        P[M.V_CD] = veh["drag_coefficient"]
        P[M.V_A] = veh["frontal_area_m2"]
        P[M.V_CRR] = veh["rolling_coefficient"]
        P[M.V_ETA] = veh["driveline_efficiency"]
        P[M.V_RHO] = veh["air_density_kg_m3"]
        P[M.V_G] = veh["gravity_m_s2"]
        P[M.V_FD] = veh["final_drive"]
        P[M.V_LAUNCH] = veh["launch_rpm"]
        P[M.V_MINRPM] = veh["min_engaged_rpm"]
        P[M.V_RPMLIM] = veh["rpm_limit"]
        P[M.V_IE] = veh["engine_inertia_kg_m2"]
        P[M.V_IW] = veh["wheel_inertia_total_kg_m2"]
        P[M.V_INERTIA] = 1.0 if veh["include_rotational_inertia"] else 0.0
        P[M.V_CRR_V] = veh.get("rolling_speed_factor_per_kmh", 0.0)
        self.P = P
        self.ratios = np.asarray(veh["gear_ratios"], float)
        self.tq_rpm = np.asarray(veh["torque_curve_rpm"], float)
        self.tq_nm = np.asarray(veh["torque_curve_Nm"], float)
        self.length = float(rt["length_m"])
        grades = np.asarray(rt["segment_grades_percent"], float)
        ang = np.arctan(grades / 100.0)  # brief: atan(grade_percent / 100)
        self.grades = grades
        self.sin_a, self.cos_a = np.sin(ang), np.cos(ang)
        self.seg_len = self.length / len(grades)
        dt = sim["dt_s"]
        C = np.zeros(M.N_CTRL)
        C[M.C_DT] = dt
        C[M.C_NH] = round(ctl["horizon_s"] / dt)
        C[M.C_UPM] = ctl["upshift_margin_m_s"]
        C[M.C_DIP] = ctl["allowed_dip_m_s"]
        C[M.C_X34] = ctl["extra_reserve_3_to_4_m_s"]
        C[M.C_HOLD] = ctl["hold_tolerance_m_s"]
        C[M.C_UPCHK] = 1.0 if ctl["upshift_check_enabled"] else 0.0
        C[M.C_TIE] = ctl["energy_tie_rel"]
        C[M.C_MAXT] = sim["max_time_s"]
        C[M.C_STALL] = sim["stall_time_s"]
        C[M.C_LEN] = self.length
        C[M.C_MODE] = M.MODE_CONTROLLER
        # controller update period and forecast horizon are fixed in seconds; the
        # physics step may be refined independently (time-step sensitivity study)
        upd = ctl.get("update_period_s", dt)
        every = upd / dt
        if abs(every - round(every)) > 1e-9:
            raise ValueError("update_period_s must be a whole number of physics steps")
        C[M.C_CTRL_EVERY] = round(every)
        self.C = C
        self.target = sim["target_time_s"]
        self.tol = sim["time_tolerance_s"]
        self.calls = 0

    def run(self, throttle, record: bool = False, C: np.ndarray | None = None) -> SimResult:
        th = np.ascontiguousarray(np.clip(np.asarray(throttle, float).ravel(), 0.0, 1.0))
        self.calls += 1
        st, t, x, v, E, sh, n, tr = M.simulate(self.P, self.ratios, self.tq_rpm, self.tq_nm,
                                               self.seg_len, self.sin_a, self.cos_a, th,
                                               self.C if C is None else C, record)
        return SimResult(int(st), float(t), float(x), float(v), float(E), int(sh), int(n), th,
                         tr if record else None)

    def feasible(self, r: SimResult) -> bool:
        return r.finished and time_ok(r.time_s, self.target, self.tol)

    def resolved(self) -> dict:
        """Everything needed to replay this case."""
        return {
            "route_name": self.route,
            "route": self.cfg["routes"][self.route],
            "vehicle": self.cfg["vehicle"],
            "simulation": self.cfg["simulation"],
            "controller": self.cfg["controller"],
            "derived": {
                "segment_length_m": self.seg_len,
                "grade_angle_rad": [math.atan(g / 100) for g in self.grades],
                "overall_ratios": (self.ratios * self.P[M.V_FD]).tolist(),
                "conventions": {
                    "integration": "explicit: start-of-step force, constant acceleration within step",
                    "motion": "forward-only (speed clamped at 0, no roll-back)",
                    "finish": "fractional final step to exactly route length; same fraction applied to time and energy",
                    "energy": "shaft work P = T*rpm*2pi/60, dE = P*dt; forecasts excluded; coasting = zero shaft torque",
                    "throttle_lookup": "by distance travelled, equal-length sections",
                    "launch": "clutch slip with engine at launch_rpm while locked rpm < launch_rpm",
                },
            },
        }
