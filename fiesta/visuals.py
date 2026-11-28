"""Journey dashboards and supporting figures.

Colour roles (validated categorical palette, fixed order):
  slot 1 blue   = constant throttle
  slot 2 orange = optimised profile
Energy-breakdown components use their own legend (slots 1-5 in order) in a separate chart.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import model as M
from . import performance as perf
from .config import Case

BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET = (
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1", "#fcfcfb"
TERRAIN = "#b9b7ae"

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.6,
    "font.size": 9, "axes.titlesize": 10, "axes.titlecolor": INK, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "legend.frameon": False, "text.color": INK,
})

STRAT = (("constant throttle", BLUE), ("optimised profile", ORANGE))


# ---------------------------------------------------------------------- data
def load_best(full_dir: Path, cfg: dict):
    """Replay the constant baseline and the lowest-energy profile for each route."""
    vec = json.loads((full_dir / "throttle_vectors.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader(open(full_dir / "summary.csv", encoding="utf-8")))
    out = {}
    for route in cfg["optimiser"]["sweep_routes"]:
        prof = [r for r in rows if r["route"] == route and r["n_sections"] != "constant" and r["energy_kJ"]]
        best = min(prof, key=lambda r: float(r["energy_kJ"]))
        n = int(best["n_sections"])
        case = Case(cfg, route)
        base = case.run(vec[f"{route}/constant"], record=True)
        opt = case.run(vec[f"{route}/N{n}"], record=True)
        out[route] = {"case": case, "n": n, "runs": (base, opt),
                      "traces": (derive(base.trace_dict()), derive(opt.trace_dict())),
                      "rows": [r for r in rows if r["route"] == route], "vectors": vec}
    return out


def derive(d: dict) -> dict:
    d = dict(d)
    t, a = d["t_s"], d["accel_m_s2"]
    j = np.full_like(t, np.nan)
    j[1:-1] = np.diff(a[:-1]) / np.diff(t[:-1])
    d["jerk_m_s3"] = j
    d["speed_kmh"] = d["v_m_s"] * 3.6
    d["power_kW"] = d["shaft_power_W"] / 1e3
    d["energy_kJ"] = d["energy_J"] / 1e3
    return d


def elevation(case, x):
    rise = case.seg_len * np.sin(np.arctan(case.grades / 100))
    z_edges = np.concatenate([[0], np.cumsum(rise)])
    i = np.clip((x // case.seg_len).astype(int), 0, len(case.grades) - 1)
    return z_edges[i] + (x - i * case.seg_len) * np.sin(np.arctan(case.grades[i] / 100))


def _hill_marks(ax, case):
    k = case.cfg["routes"][case.route].get("steep_approach_segment_index")
    if k is None:
        return
    ax.axvspan(k * case.seg_len, (k + 1) * case.seg_len, color="#f3ecdf", lw=0, zorder=0)


# ---------------------------------------------------------------------- journey dashboards
PANELS = [
    ("speed_kmh", "speed\n(km/h)", "line"),
    ("accel_m_s2", "acceleration\n(m/s²)", "step"),
    ("jerk_m_s3", "jerk\n(m/s³)", "line"),
    ("throttle", "throttle", "step"),
    ("gear", "gear", "step"),
    ("engine_rpm", "engine\n(rpm)", "step"),
    ("power_kW", "shaft power\n(kW)", "step"),
    ("energy_kJ", "cumulative shaft\nenergy (kJ)", "line"),
]


def journey_dashboard(route_data, axis: str, path: Path, cfg: dict):
    case = route_data["case"]
    traces = route_data["traces"]
    runs = route_data["runs"]
    xkey = "t_s" if axis == "time" else "x_m"
    hilly = bool(np.any(case.grades != 0))
    panels = ([("elev", "elevation\n(m)", "terrain")] if axis == "distance" and hilly else []) + PANELS
    hr = [0.6 if p[0] in ("elev", "gear", "throttle") else 1 for p in panels]
    fig, axs = plt.subplots(len(panels), 1, figsize=(11, 1.35 * len(panels) + 1.2), sharex=True,
                            gridspec_kw={"height_ratios": hr, "hspace": 0.18})
    for ax, (key, lab, kind) in zip(axs, panels):
        if axis == "distance":
            _hill_marks(ax, case)
        if kind == "terrain":
            xs = np.linspace(0, case.length, 600)
            z = elevation(case, xs)
            ax.fill_between(xs, z, z.min() - 1, color=TERRAIN, lw=0)
            for k, g in enumerate(case.grades):
                if len(case.grades) > 1:
                    xm = (k + 0.5) * case.seg_len
                    ax.text(xm, elevation(case, np.array([xm]))[0] + 0.8, f"{g:+.0f}%",
                            ha="center", fontsize=7.5, color=INK2)
        else:
            for d, (name, col) in zip(traces, STRAT):
                x, y = d[xkey], d[key]
                if kind == "step":
                    ax.step(x[:-1], y[:-1], where="post", color=col, lw=1.3)
                else:
                    ax.plot(x, y, color=col, lw=1.3 if key != "jerk_m_s3" else 0.8)
        ax.set_ylabel(lab, rotation=0, ha="right", va="center", fontsize=8.5, labelpad=8)
        if key == "gear":
            ax.set_yticks([1, 2, 3, 4, 5])
        if key == "throttle":
            ax.set_ylim(-0.05, 1.05)
    # selective direct labels: final energy of each strategy
    ax = axs[-1]
    for d, (name, col), r in zip(traces, STRAT, runs):
        ax.annotate(f"{r.energy_J / 1e3:.0f} kJ", (d[xkey][-1], d["energy_kJ"][-1]),
                    xytext=(4, 0), textcoords="offset points", va="center", fontsize=8, color=INK)
    if axis == "time":
        for a_ in axs:
            a_.axvline(case.target, color=MUTED, lw=0.8)
        axs[0].text(case.target, axs[0].get_ylim()[1], " 120 s target", fontsize=7.5, color=INK2, va="top")
    axs[-1].set_xlabel("time (s)" if axis == "time" else "distance (m)")
    base, opt = runs
    saving = 100 * (base.energy_J - opt.energy_J) / base.energy_J
    handles = [plt.Line2D([], [], color=c, lw=2) for _, c in STRAT]
    labels = [f"constant throttle {base.throttle[0]:.2f}",
              f"optimised profile, {route_data['n']} sections"]
    fig.legend(handles, labels, loc="upper right", ncol=2, bbox_to_anchor=(0.98, 0.995), fontsize=9)
    fig.suptitle(f"{case.route.capitalize()} mile vs {axis}: saving {saving:.1f}%",
                 x=0.07, ha="left", y=0.995, fontsize=12, fontweight="bold")
    fig.text(0.07, 0.968, "Jerk is the step-to-step change in acceleration; spikes mark throttle steps "
             "and gear changes." + ("  Shaded band: steep +6% section." if axis == "distance" and
                                     case.route == "rolling" else ""),
             fontsize=8, color=INK2)
    fig.subplots_adjust(left=0.13, right=0.94, top=0.95, bottom=0.05)
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------- engine map
def engine_map(route_data, path: Path):
    case = route_data["case"]
    rpm = np.linspace(800, 6300, 300)
    full = np.array([M.full_torque(r, case.tq_rpm, case.tq_nm, case.P[M.V_RPMLIM]) for r in rpm])
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, d, (name, col), r in zip(axs, route_data["traces"], STRAT, route_data["runs"]):
        for p_kw in (10, 20, 30, 40, 50, 60):
            ax.plot(rpm, p_kw * 1e3 / (rpm * M.TWO_PI_60), color=GRID, lw=0.8, zorder=1)
            rr = 1500 if p_kw <= 10 else (3200 if p_kw <= 20 else 5000)
            ax.text(rr, p_kw * 1e3 / (rr * M.TWO_PI_60), f"{p_kw} kW", fontsize=7, color=MUTED, va="bottom")
        ax.plot(rpm, full, color=INK2, lw=1.2, zorder=2)
        on = d["torque_Nm"][:-1] > 0.5
        ax.scatter(d["engine_rpm"][:-1][on], d["torque_Nm"][:-1][on], s=9, color=col, alpha=0.35,
                   lw=0, zorder=3)
        coast = (~on).sum() * case.C[M.C_DT]
        ax.set_title(f"{name}: {on.sum() * case.C[M.C_DT]:.0f} s driving, {coast:.0f} s coasting")
        ax.set_xlabel("engine speed (rpm)")
        ax.set_xlim(700, 6400)
        ax.set_ylim(0, 125)
    axs[0].set_ylabel("engine torque (N·m)")
    axs[0].text(4300, 117, "full load", fontsize=7.5, color=INK2)
    fig.suptitle(f"Engine operating points, {case.route} mile (one dot per 0.1 s)", x=0.06, ha="left",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------- energy breakdown
def breakdown(case, d) -> dict:
    P = case.P
    dx = np.diff(d["x_m"])
    v = d["v_m_s"][:-1]
    th = np.arctan(d["grade_percent"][:-1] / 100)
    m, g = P[M.V_MASS], P[M.V_G]
    crr = P[M.V_CRR] * (1 + P[M.V_CRR_V] * v * 3.6)
    E = d["energy_J"][-1]
    W_wheel = np.sum(d["wheel_force_N"][:-1] * dx)
    W_aero = np.sum(0.5 * P[M.V_RHO] * P[M.V_CD] * P[M.V_A] * v ** 2 * dx)
    W_roll = np.sum(crr * m * g * np.cos(th) * dx)
    W_grade = np.sum(m * g * np.sin(th) * dx)
    return {"driveline & clutch losses": (E - W_wheel) / 1e3, "aerodynamic drag": W_aero / 1e3,
            "rolling resistance": W_roll / 1e3, "net climbing": W_grade / 1e3,
            "kinetic energy at the line": (W_wheel - W_aero - W_roll - W_grade) / 1e3}


def energy_breakdown(all_routes, path: Path):
    comps = ["driveline & clutch losses", "aerodynamic drag", "rolling resistance",
             "kinetic energy at the line"]
    cols = [BLUE, ORANGE, AQUA, YELLOW, MAGENTA]
    bars, labels = [], []
    for route, rd in all_routes.items():
        for d, (name, _), r in zip(rd["traces"], STRAT, rd["runs"]):
            bars.append(breakdown(rd["case"], d))
            labels.append(f"{route}: {name}" + (f" (N={rd['n']})" if name.startswith("opt") else ""))
    fig, ax = plt.subplots(figsize=(11, 3.8))
    y = np.arange(len(bars))[::-1]
    left = np.zeros(len(bars))
    for comp, col in zip(comps, cols):
        vals = np.array([b[comp] for b in bars])
        ax.barh(y, vals, left=left, color=col, height=0.6, edgecolor=SURF, linewidth=2, label=comp)
        for yy, l, vv in zip(y, left, vals):
            if vv > 35:
                ax.text(l + vv / 2, yy, f"{vv:.0f}", ha="center", va="center", fontsize=8, color=INK)
        left += vals
    for yy, tot in zip(y, left):
        ax.text(tot + 5, yy, f"{tot:.0f} kJ", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(y, labels)
    ax.set_xlabel("shaft energy (kJ)")
    ax.grid(axis="y", visible=False)
    ax.legend(ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8)
    ax.set_title("Where the shaft energy goes (net climbing is ~0: both courses end at start height)",
                 pad=26)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return dict(zip(labels, bars))


# ---------------------------------------------------------------------- throttle small multiples
def throttle_profiles(all_routes, path: Path):
    ns = [1, 2, 4, 8, 16]
    routes = list(all_routes)
    fig, axs = plt.subplots(len(routes), len(ns), figsize=(13, 2.3 * len(routes) + 0.6),
                            sharex=True, sharey=True)
    for i, route in enumerate(routes):
        rd = all_routes[route]
        case = rd["case"]
        rowmap = {r["n_sections"]: r for r in rd["rows"]}
        for j, n in enumerate(ns):
            ax = axs[i, j]
            if route == "rolling":
                _hill_marks(ax, case)
            if f"{route}/N{n}" not in rd["vectors"]:
                ax.set_title(f"{route}, N={n}: no run found", fontsize=9)
                continue
            q = np.asarray(rd["vectors"][f"{route}/N{n}"])
            edges = np.linspace(0, case.length, n + 1)
            ax.stairs(q, edges, color=ORANGE, lw=1.6, baseline=None)
            c0 = rd["vectors"][f"{route}/constant"][0]
            ax.axhline(c0, color=BLUE, lw=1.0)
            s = float(rowmap[str(n)]["saving_percent"])
            ax.set_title(f"{route}, N={n}: {s:.1f}%", fontsize=9)
            ax.set_ylim(-0.03, 1.03)
            if j == 0:
                ax.set_ylabel("throttle")
            if i == len(routes) - 1:
                ax.set_xlabel("distance (m)")
    handles = [plt.Line2D([], [], color=BLUE), plt.Line2D([], [], color=ORANGE, lw=2)]
    fig.legend(handles, ["constant throttle (same course)", "best profile found"], loc="upper right",
               ncol=2, bbox_to_anchor=(0.99, 1.0))
    fig.suptitle("Best throttle profile at each resolution (title: saving vs constant)", x=0.01,
                 ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------- trade-off
def energy_vs_time(all_routes, cfg, path: Path):
    """Constant-throttle sweep: journey time vs shaft energy, coloured by the highest gear
    the forecast rule ever selects. Jumps between colours are the stuck-in-gear effect."""
    gear_col = {3: AQUA, 4: BLUE, 5: ORANGE}
    fig, axs = plt.subplots(1, len(all_routes), figsize=(12, 4.6), sharey=True)
    ths = np.linspace(0.22, 1.0, 157)
    for ax, (route, rd) in zip(np.atleast_1d(axs), all_routes.items()):
        case = Case(cfg, route)
        pts = []
        for t in ths:
            r = case.run([t], record=True)
            if r.finished:
                pts.append((r.time_s, r.energy_J / 1e3, int(r.trace[:, 3].max())))
        pts = np.array(pts)
        for g, col in gear_col.items():
            m = pts[:, 2] == g
            if m.any():
                ax.scatter(pts[m, 0], pts[m, 1], s=16, color=col, lw=0, label=f"top gear reached: {g}")
        base, opt = rd["runs"]
        ax.scatter([opt.time_s], [opt.energy_J / 1e3], s=90, marker="*", color=INK, zorder=4)
        ax.annotate(f"optimised, N={rd['n']}: {opt.energy_J / 1e3:.0f} kJ", (opt.time_s, opt.energy_J / 1e3),
                    xytext=(8, -4), textcoords="offset points", fontsize=8)
        ax.annotate(f"constant baseline: {base.energy_J / 1e3:.0f} kJ", (base.time_s, base.energy_J / 1e3),
                    xytext=(8, 10), textcoords="offset points", fontsize=8)
        ax.axvline(cfg["simulation"]["target_time_s"], color=MUTED, lw=0.8)
        ax.set_xlim(60, 200)
        ax.set_xlabel("journey time for the mile (s)")
        ax.set_title(f"{route} mile, constant throttle 0.22-1.0")
    np.atleast_1d(axs)[0].set_ylabel("shaft energy (kJ)")
    np.atleast_1d(axs)[0].legend(loc="upper right", fontsize=8)
    fig.suptitle("Energy vs journey time. Each dot is one constant throttle. Same-time runs differ in energy "
                 "when the gear rule never upshifts", x=0.01, ha="left", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def savings_by_n(all_routes, path: Path):
    fig, ax = plt.subplots(figsize=(8, 4))
    for (route, rd), col, mk in zip(all_routes.items(), (BLUE, AQUA), ("o", "s")):
        rows = [r for r in rd["rows"] if r["n_sections"] != "constant" and r["saving_percent"]]
        n = [int(r["n_sections"]) for r in rows]
        s = [float(r["saving_percent"]) for r in rows]
        ax.plot(n, s, color=col, marker=mk, ms=7, label=route)
        k = int(np.argmax(s))
        ax.annotate(f"{s[k]:.1f}%", (n[k], s[k]), xytext=(0, 8), textcoords="offset points",
                    ha="center", fontsize=8)
    ax.axhline(34, color=INK2, lw=0.8)
    ax.text(1, 34.3, "34% (Doug's CV)", fontsize=8, color=INK2)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 2, 4, 8, 16], ["1", "2", "4", "8", "16"])
    ax.set_xlabel("throttle sections")
    ax.set_ylabel("saving vs constant throttle (%)")
    ax.set_ylim(-2, 40)
    ax.legend(loc="lower right")
    ax.set_title("Saving by throttle resolution")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def acceleration_dashboard(cfg, path: Path):
    pol = cfg["performance_test"]["policies"]["documented"]
    a = perf.acceleration(cfg, pol)
    d = derive(a["trace"])
    keep = d["t_s"] <= 30
    d = {k: v[keep] for k, v in d.items()}
    panels = [("speed_kmh", "speed (km/h)"), ("accel_m_s2", "acceleration (m/s²)"),
              ("jerk_m_s3", "jerk (m/s³)"), ("gear", "gear"), ("engine_rpm", "engine (rpm)"),
              ("power_kW", "shaft power (kW)")]
    fig, axs = plt.subplots(len(panels), 1, figsize=(10, 9), sharex=True)
    for ax, (k, lab) in zip(axs, panels):
        if k in ("gear", "accel_m_s2", "engine_rpm", "power_kW"):
            ax.step(d["t_s"], d[k], where="post", color=BLUE, lw=1.4)
        else:
            ax.plot(d["t_s"], d[k], color=BLUE, lw=1.4 if k != "jerk_m_s3" else 0.8)
        ax.set_ylabel(lab, rotation=0, ha="right", va="center", fontsize=8.5)
    for t, lab, ha in ((a["zero_to_60_mph_s"], "0-60 mph", "right"),
                       (a["zero_to_100_kmh_s"], "0-100 km/h", "left")):
        for ax in axs:
            ax.axvline(t, color=MUTED, lw=0.8)
        axs[0].text(t + (0.15 if ha == "left" else -0.15), 10, f"{lab}\n{t:.2f} s", fontsize=8,
                    color=INK2, ha=ha)
    axs[3].set_yticks([1, 2, 3, 4, 5])
    axs[-1].set_xlabel("time (s)")
    fig.suptitle("Calibrated full-throttle run on the flat (Ford: 0-100 km/h 13.3 s)", x=0.07,
                 ha="left", fontsize=12, fontweight="bold")
    fig.text(0.07, 0.945, "3000 rpm clutch launch, upshift at 6250 rpm, "
             f"{pol['shift_time_s']:.2f} s torque interruption per shift", fontsize=8, color=INK2)
    fig.subplots_adjust(left=0.16, right=0.97, top=0.93, bottom=0.06)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def sensitivity_plot(rows, path: Path):
    rows = sorted([r for r in rows if np.isfinite(r.get("saving_percent", np.nan))],
                  key=lambda r: r["saving_percent"])
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(rows) + 1.4))
    y = np.arange(len(rows))
    ref = next((r["saving_percent"] for r in rows if r["variant"].startswith("reference")), None)
    for yy, r in zip(y, rows):
        col = ORANGE if r["variant"].startswith("reference") else BLUE
        ax.plot([ref, r["saving_percent"]], [yy, yy], color=GRID, lw=2, zorder=1)
        ax.scatter([r["saving_percent"]], [yy], s=60, color=col, zorder=3, edgecolor=SURF, linewidth=1.5)
        ax.text(r["saving_percent"] + 0.25, yy, f"{r['saving_percent']:.1f}%", va="center", fontsize=8)
    ax.axvline(34, color=INK2, lw=0.8)
    ax.text(34, len(rows) - 0.4, " 34% (CV)", fontsize=8, color=INK2)
    ax.set_yticks(y, [r["variant"] for r in rows])
    ax.set_xlabel("saving vs constant throttle (%), rolling mile, 8 sections")
    ax.grid(axis="y", visible=False)
    ax.set_title("What moves the saving: one assumption changed at a time")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------- entry point
def make_all(cfg: dict, full_dir: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    data = load_best(full_dir, cfg)
    for route, rd in data.items():
        journey_dashboard(rd, "time", out / f"{route}_journey_vs_time.png", cfg)
        journey_dashboard(rd, "distance", out / f"{route}_journey_vs_distance.png", cfg)
        engine_map(rd, out / f"{route}_engine_map.png")
    bd = energy_breakdown(data, out / "energy_breakdown.png")
    (out / "energy_breakdown.json").write_text(json.dumps(bd, indent=2), encoding="utf-8")
    throttle_profiles(data, out / "throttle_profiles.png")
    energy_vs_time(data, cfg, out / "energy_vs_time.png")
    savings_by_n(data, out / "savings_by_sections.png")
    acceleration_dashboard(cfg, out / "acceleration_dashboard.png")
    print(f"figures written to {out}")
    return data
