"""Forward longitudinal simulation with forecast-based gear selection.

All hot loops are plain scalar code compiled with numba when available
(falls back to pure Python, ~100x slower, if numba is missing).

Conventions (reconstruction defaults from the brief):
  * explicit step: force evaluated at the start of each 0.1 s step,
    constant acceleration within the step;
  * forward-only motion: speed is clamped at zero, the car never rolls back;
  * the final step is truncated at the exact fraction that reaches the
    finish line, and that same fraction is applied to time and energy;
  * shaft energy  dE = T_engine * omega_engine * dt   (P_W = T * rpm * 2pi/60);
  * forecasts are disposable local copies and never touch the trip meter.
"""
from __future__ import annotations

import math
import numpy as np

try:  # pragma: no cover - exercised implicitly
    from numba import njit
    HAVE_NUMBA = True
except Exception:  # pragma: no cover
    HAVE_NUMBA = False

    def njit(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

TWO_PI_60 = 2.0 * math.pi / 60.0

# ---- vehicle parameter vector indices -------------------------------------
V_MASS, V_R, V_CD, V_A, V_CRR, V_ETA, V_RHO, V_G, V_FD = 0, 1, 2, 3, 4, 5, 6, 7, 8
V_LAUNCH, V_MINRPM, V_RPMLIM, V_IE, V_IW, V_INERTIA = 9, 10, 11, 12, 13, 14
V_CRR_V = 15  # rolling-resistance speed term: Crr(v) = Crr0 * (1 + V_CRR_V * v_kmh)
N_VEH = 16

# ---- control/simulation parameter vector indices --------------------------
C_DT, C_NH, C_UPM, C_DIP, C_X34, C_HOLD, C_UPCHK, C_TIE = 0, 1, 2, 3, 4, 5, 6, 7
C_MAXT, C_STALL, C_LEN, C_MODE, C_SHIFT_RPM, C_SHIFT_T = 8, 9, 10, 11, 12, 13
N_CTRL = 14

MODE_CONTROLLER = 0.0   # forecast-based gear selection (energy experiment)
MODE_PERFORMANCE = 1.0  # full-load acceleration: upshift at shift_rpm

# ---- status codes ----------------------------------------------------------
FINISHED, TIMEOUT, STALLED = 0, 1, 2
STATUS_NAMES = {FINISHED: "finished", TIMEOUT: "timeout", STALLED: "stalled"}

# ---- gear decision codes ---------------------------------------------------
D_HOLD, D_UP, D_DOWN_STRUGGLE, D_DOWN_ENERGY = 0, 1, 2, 3
D_FORCED_UP, D_FORCED_DOWN, D_ALL_STRUGGLE, D_PERF_SHIFT = 4, 5, 6, 7
DECISION_NAMES = {
    D_HOLD: "hold", D_UP: "upshift (reserve met, lower energy)",
    D_DOWN_STRUGGLE: "downshift (current gear struggling)",
    D_DOWN_ENERGY: "downshift (lower energy)",
    D_FORCED_UP: "forced upshift (rpm limit)",
    D_FORCED_DOWN: "forced downshift (below min engaged rpm)",
    D_ALL_STRUGGLE: "all candidates struggle: best speed",
    D_PERF_SHIFT: "performance upshift",
}

# ---- trace columns ---------------------------------------------------------
TRACE_COLS = [
    "t_s", "x_m", "v_m_s", "gear", "throttle", "engine_rpm", "torque_Nm",
    "shaft_power_W", "energy_J", "grade_percent", "wheel_force_N", "accel_m_s2",
    "clutch_slip", "decision",
    "fc_down_rpm", "fc_down_force_N", "fc_down_dv_m_s", "fc_down_energy_J",
    "fc_hold_rpm", "fc_hold_force_N", "fc_hold_dv_m_s", "fc_hold_energy_J",
    "fc_up_rpm", "fc_up_force_N", "fc_up_dv_m_s", "fc_up_energy_J",
]
NCOL = len(TRACE_COLS)


@njit(cache=True)
def full_torque(rpm, tq_rpm, tq_nm, rpm_limit):
    """Full-load torque by linear interpolation; zero above the rpm limit (fuel cut)."""
    if rpm > rpm_limit + 1e-9:
        return 0.0
    n = tq_rpm.shape[0]
    if rpm <= tq_rpm[0]:
        return tq_nm[0]
    if rpm >= tq_rpm[n - 1]:
        return tq_nm[n - 1]
    for i in range(n - 1):
        if rpm <= tq_rpm[i + 1]:
            f = (rpm - tq_rpm[i]) / (tq_rpm[i + 1] - tq_rpm[i])
            return tq_nm[i] + f * (tq_nm[i + 1] - tq_nm[i])
    return tq_nm[n - 1]


@njit(cache=True)
def shaft_energy(torque_nm, omega_rad_s, dt):
    """Shaft work for constant torque and speed: E = T * omega * dt  [J]."""
    return torque_nm * omega_rad_s * dt


@njit(cache=True)
def locked_rpm(v, gear, P, ratios):
    return v / P[V_R] * ratios[gear - 1] * P[V_FD] / TWO_PI_60


@njit(cache=True)
def seg_index(x, seg_len, nseg):
    i = int(x / seg_len)
    if i < 0:
        return 0
    if i >= nseg:
        return nseg - 1
    return i


@njit(cache=True)
def resistance(v, P, sin_t, cos_t):
    m = P[V_MASS]
    g = P[V_G]
    crr = P[V_CRR] * (1.0 + P[V_CRR_V] * v * 3.6)
    return (m * g * sin_t + crr * m * g * cos_t
            + 0.5 * P[V_RHO] * P[V_CD] * P[V_A] * v * v)


@njit(cache=True)
def drive(v, gear, thr, P, ratios, tq_rpm, tq_nm, sin_t, cos_t, cut):
    """Start-of-step dynamics. Returns (accel, engine_rpm, torque, wheel_force, slip)."""
    rpm_l = locked_rpm(v, gear, P, ratios)
    slip = rpm_l < P[V_LAUNCH]
    rpm_e = P[V_LAUNCH] if slip else rpm_l
    T = 0.0
    if not cut and thr > 0.0:
        T = thr * full_torque(rpm_e, tq_rpm, tq_nm, P[V_RPMLIM])
    G = ratios[gear - 1] * P[V_FD]
    r = P[V_R]
    F = T * G * P[V_ETA] / r
    m_eff = P[V_MASS]
    if P[V_INERTIA] > 0.5:
        m_eff += P[V_IW] / (r * r)
        if not slip:
            m_eff += P[V_IE] * G * G / (r * r)
    a = (F - resistance(v, P, sin_t, cos_t)) / m_eff
    if v <= 0.0 and a < 0.0:
        a = 0.0  # held stationary: forward-only motion, no roll-back
    return a, rpm_e, T, F, slip


@njit(cache=True)
def advance(x, v, a, dt):
    """Constant-acceleration step with forward-only motion. Returns (x1, v1)."""
    v1 = v + a * dt
    if v1 < 0.0:
        ts = v / (-a)
        return x + 0.5 * v * ts, 0.0
    return x + v * dt + 0.5 * a * dt * dt, v1


@njit(cache=True)
def finish_fraction(x, v, a, dist):
    """Time tau within a step to cover `dist` at constant acceleration."""
    if abs(a) < 1e-12:
        return dist / v
    disc = v * v + 2.0 * a * dist
    if disc < 0.0:
        disc = 0.0
    return (-v + math.sqrt(disc)) / a


@njit(cache=True)
def full_force(v, gear, P, ratios, tq_rpm, tq_nm):
    rpm_l = locked_rpm(v, gear, P, ratios)
    rpm_e = P[V_LAUNCH] if rpm_l < P[V_LAUNCH] else rpm_l
    return full_torque(rpm_e, tq_rpm, tq_nm, P[V_RPMLIM]) * ratios[gear - 1] * P[V_FD] * P[V_ETA] / P[V_R]


@njit(cache=True)
def legal(v, gear, P, ratios):
    if gear < 1 or gear > ratios.shape[0]:
        return False
    rpm = locked_rpm(v, gear, P, ratios)
    if rpm > P[V_RPMLIM]:
        return False
    if gear > 1 and rpm < P[V_MINRPM]:
        return False
    return True


@njit(cache=True)
def forecast(x, v, gear, P, ratios, tq_rpm, tq_nm, seg_len, sin_a, cos_a, th, sec_len, C):
    """Disposable forecast holding `gear` for the horizon at the requested throttle,
    sampling throttle and grade at the predicted position.
    Returns (v_end, v_min, energy_J, rpm0, force0)."""
    dt = C[C_DT]
    nH = int(C[C_NH] + 0.5)
    nseg = sin_a.shape[0]
    nsec = th.shape[0]
    vmin = v
    E = 0.0
    rpm0 = 0.0
    f0 = 0.0
    for k in range(nH):
        i = seg_index(x, seg_len, nseg)
        thr = th[seg_index(x, sec_len, nsec)]
        a, rpm_e, T, F, slip = drive(v, gear, thr, P, ratios, tq_rpm, tq_nm, sin_a[i], cos_a[i], False)
        if k == 0:
            rpm0 = rpm_e
            f0 = F
        E += shaft_energy(T, rpm_e * TWO_PI_60, dt)
        x, v = advance(x, v, a, dt)
        if v < vmin:
            vmin = v
    return v, vmin, E, rpm0, f0


@njit(cache=True)
def struggling(x, v, gear, P, ratios, tq_rpm, tq_nm, seg_len, sin_a, cos_a):
    """A gear struggles when even full throttle cannot hold the current speed."""
    i = seg_index(x, seg_len, sin_a.shape[0])
    return full_force(v, gear, P, ratios, tq_rpm, tq_nm) < resistance(v, P, sin_a[i], cos_a[i])


@njit(cache=True)
def choose_gear(x, v, gear, P, ratios, tq_rpm, tq_nm, seg_len, sin_a, cos_a, th, sec_len, C, fc):
    """Forecast down/hold/up from the same state and pick the next gear.
    fc (3x4) receives rpm, wheel force, signed dv, energy for each candidate (NaN if illegal)."""
    ng = ratios.shape[0]
    vend = np.full(3, np.nan)
    vmin = np.full(3, np.nan)
    en = np.full(3, np.nan)
    ok = np.zeros(3, dtype=np.bool_)
    for j in range(3):
        fc[j, 0] = np.nan
        fc[j, 1] = np.nan
        fc[j, 2] = np.nan
        fc[j, 3] = np.nan
        g = gear - 1 + j
        if g < 1 or g > ng:
            continue
        if j != 1 and not legal(v, g, P, ratios):
            continue
        ve, vm, e, r0, f0 = forecast(x, v, g, P, ratios, tq_rpm, tq_nm, seg_len, sin_a, cos_a, th, sec_len, C)
        ok[j] = True
        vend[j] = ve
        vmin[j] = vm
        en[j] = e
        fc[j, 0] = r0
        fc[j, 1] = f0
        fc[j, 2] = ve - v
        fc[j, 3] = e

    # explicit rpm-limit handling for the current gear
    rpm_now = locked_rpm(v, gear, P, ratios)
    if rpm_now > P[V_RPMLIM]:
        if ok[2]:
            return gear + 1, D_FORCED_UP
        return gear, D_HOLD  # fuel cut in top gear
    if gear > 1 and rpm_now < P[V_MINRPM]:
        if ok[0]:
            return gear - 1, D_FORCED_DOWN
        return gear, D_HOLD

    hold_str = struggling(x, v, gear, P, ratios, tq_rpm, tq_nm, seg_len, sin_a, cos_a)
    down_str = True
    if ok[0]:
        down_str = struggling(x, v, gear - 1, P, ratios, tq_rpm, tq_nm, seg_len, sin_a, cos_a)
    if hold_str and ok[0] and not down_str:
        return gear - 1, D_DOWN_STRUGGLE

    meets = np.zeros(3, dtype=np.bool_)
    meets[1] = not hold_str
    if ok[0]:
        meets[0] = not down_str
    if ok[2]:
        if C[C_UPCHK] > 0.5:
            margin = C[C_UPM]
            if gear == 3:
                margin += C[C_X34]
            meets[2] = (vmin[2] >= v - C[C_DIP] - 1e-12) and (vend[2] >= v + margin - 1e-12)
        else:
            meets[2] = True  # margin disabled: any legal upshift is admissible

    if not (meets[0] or meets[1] or meets[2]):
        best = 1
        for j in range(3):
            if ok[j] and vend[j] > vend[best] + 1e-12:
                best = j
        return gear - 1 + best, D_ALL_STRUGGLE

    best = 1 if meets[1] else -1
    for j in (0, 2):
        if not meets[j]:
            continue
        if best < 0:
            best = j
            continue
        tie = C[C_TIE] * max(abs(en[best]), 1.0)
        if en[j] < en[best] - tie:
            best = j
    if best == 1:
        return gear, D_HOLD
    if best == 2:
        return gear + 1, D_UP
    return gear - 1, D_DOWN_ENERGY


@njit(cache=True)
def simulate(P, ratios, tq_rpm, tq_nm, seg_len, sin_a, cos_a, th, C, record):
    """Run one trip from rest in first gear. Returns
    (status, time_s, x_m, v_m_s, energy_J, shifts, steps, trace)."""
    dt = C[C_DT]
    L = C[C_LEN]
    nmax = int(C[C_MAXT] / dt + 0.5) + 2
    trace = np.full((nmax if record else 1, 26), np.nan)
    nseg = sin_a.shape[0]
    nsec = th.shape[0]
    sec_len = L / nsec
    fc = np.full((3, 4), np.nan)

    t = 0.0
    x = 0.0
    v = 0.0
    E = 0.0
    gear = 1
    shifts = 0
    still = 0.0
    shift_left = 0.0
    status = TIMEOUT
    n = 0
    perf = C[C_MODE] > 0.5
    while n < nmax - 1:
        i = seg_index(x, seg_len, nseg)
        thr = th[seg_index(x, sec_len, nsec)]
        decision = D_HOLD
        if perf:
            for j in range(3):
                for c in range(4):
                    fc[j, c] = np.nan
            if shift_left <= 1e-12 and gear < ratios.shape[0] and locked_rpm(v, gear, P, ratios) >= C[C_SHIFT_RPM] - 1e-9:
                gear += 1
                shifts += 1
                decision = D_PERF_SHIFT
                shift_left = C[C_SHIFT_T]
        else:
            g_new, decision = choose_gear(x, v, gear, P, ratios, tq_rpm, tq_nm, seg_len,
                                          sin_a, cos_a, th, sec_len, C, fc)
            if g_new != gear:
                shifts += 1
                gear = g_new
        # torque interruption during a performance upshift; a partially interrupted
        # step delivers the step-averaged torque
        cut_frac = min(max(shift_left / dt, 0.0), 1.0)
        a, rpm_e, T, F, slip = drive(v, gear, thr * (1.0 - cut_frac), P, ratios, tq_rpm, tq_nm,
                                     sin_a[i], cos_a[i], cut_frac >= 1.0 - 1e-12)
        Pw = T * rpm_e * TWO_PI_60
        if record:
            row = trace[n]
            row[0] = t
            row[1] = x
            row[2] = v
            row[3] = gear
            row[4] = thr * (1.0 - cut_frac)
            row[5] = rpm_e
            row[6] = T
            row[7] = Pw
            row[8] = E
            row[9] = 100.0 * sin_a[i] / cos_a[i]
            row[10] = F
            row[11] = a
            row[12] = 1.0 if slip else 0.0
            row[13] = decision
            for j in range(3):
                for c in range(4):
                    row[14 + 4 * j + c] = fc[j, c]
        x1, v1 = advance(x, v, a, dt)
        if x1 >= L:
            tau = finish_fraction(x, v, a, L - x)
            if tau > dt:
                tau = dt
            if tau < 0.0:
                tau = 0.0
            t += tau
            E += Pw * tau
            v = v + a * tau
            x = L
            n += 1
            status = FINISHED
            break
        x, v = x1, v1
        t += dt
        E += Pw * dt
        if shift_left > 0.0:
            shift_left = max(shift_left - dt, 0.0)
        n += 1
        if v <= 0.0 and t > dt:
            still += dt
            if still >= C[C_STALL] - 1e-9:
                status = STALLED
                break
        else:
            still = 0.0
        if t >= C[C_MAXT] - 1e-9:
            status = TIMEOUT
            break
    if record:
        row = trace[n]
        row[0] = t
        row[1] = x
        row[2] = v
        row[3] = gear
        row[8] = E
        trace = trace[: n + 1]
    return status, t, x, v, E, shifts, n, trace
