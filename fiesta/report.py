"""Figures, CSV summaries and REPORT.md from a saved results set."""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np

from .config import Case
from .experiments import ref_vectors
from .reference import load_reference_config

# validated categorical palette, fixed order; grey = constant baseline (not a series slot)
BLUE, ORANGE, AQUA, YELLOW, MAGENTA = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
INK, INK2, MUTED, GRID, SURF, TERRAIN = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1", "#fcfcfb", "#b9b7ae"
MCOL = {"A": BLUE, "B": ORANGE, "C": AQUA, "D": YELLOW}
plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False,
    "axes.spines.right": False, "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "legend.frameon": False, "lines.linewidth": 1.6,
})


def _load(p: Path, name):
    f = p / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _title(fig, text, sub=None):
    fig.suptitle(text, x=0.01, ha="left", fontsize=12, fontweight="bold")
    if sub:
        fig.text(0.01, 0.935, sub, fontsize=8.5, color=INK2)


def elevation(case, x):
    rise = case.seg_len * np.sin(np.arctan(case.grades / 100))
    z = np.concatenate([[0], np.cumsum(rise)])
    i = np.clip((x // case.seg_len).astype(int), 0, len(case.grades) - 1)
    return z[i] + (x - i * case.seg_len) * np.sin(np.arctan(case.grades[i] / 100))


# ------------------------------------------------------------------ figure 1
def fig_overlay(cfg, best_rec, path):
    case = Case(cfg, "rolling")
    v = ref_vectors()
    runs = [("constant throttle (baseline)", v["rolling/constant"], MUTED, 1.3),
            ("v1 best (N=8)", v["rolling/N8"], MAGENTA, 1.3),
            (f"v2 best: method {best_rec['method']}, N={best_rec['n_sections']}", best_rec["throttle"],
             MCOL[best_rec["method"]], 1.8)]
    traces = [(lab, case.run(th, record=True), col, lw) for lab, th, col, lw in runs]
    fig, axs = plt.subplots(5, 1, figsize=(11, 10.5), sharex=True,
                            gridspec_kw={"height_ratios": [0.7, 0.9, 0.7, 1.1, 1.1], "hspace": 0.15})
    xs = np.linspace(0, case.length, 600)
    k = case.cfg["routes"]["rolling"]["steep_approach_segment_index"]
    for ax in axs:
        ax.axvspan(k * case.seg_len, (k + 1) * case.seg_len, color="#f3ecdf", lw=0, zorder=0)
    z = elevation(case, xs)
    axs[0].fill_between(xs, z, z.min() - 1, color=TERRAIN, lw=0)
    for i, g in enumerate(case.grades):
        xm = (i + 0.5) * case.seg_len
        axs[0].text(xm, elevation(case, np.array([xm]))[0] + 0.8, f"{g:+.0f}%", ha="center", fontsize=7.5, color=INK2)
    for idx, (lab, r, col, lw) in enumerate(traces):
        d = r.trace_dict()
        x = d["x_m"]
        axs[1].step(x[:-1], d["throttle"][:-1], where="post", color=col, lw=lw)
        axs[2].step(x, d["gear"], where="post", color=col, lw=lw)
        axs[3].plot(x, d["v_m_s"] * 3.6, color=col, lw=lw)
        axs[4].plot(x, d["energy_J"] / 1e3, color=col, lw=lw, label=lab)
        axs[4].annotate(f"{r.energy_J / 1e3:.0f} kJ", (x[-1], d["energy_J"][-1] / 1e3),
                        xytext=(4, [0, 7, -7][idx]),
                        textcoords="offset points", va="center", fontsize=8)
    for ax, lab in zip(axs, ["elevation (m)", "throttle", "gear", "speed (km/h)", "shaft energy (kJ)"]):
        ax.set_ylabel(lab, rotation=0, ha="right", va="center", fontsize=8.5)
    axs[2].set_yticks([1, 2, 3, 4, 5])
    axs[4].set_xlabel("distance (m)")
    axs[4].legend(loc="upper left", fontsize=8.5)
    base, best = traces[0][1], traces[2][1]
    _title(fig, "Rolling mile: terrain, throttle, gear, speed and shaft energy against distance",
           f"Saving vs constant: v1 {100 * (base.energy_J - traces[1][1].energy_J) / base.energy_J:.1f}%, "
           f"v2 {100 * (base.energy_J - best.energy_J) / base.energy_J:.1f}%.  Shaded: steep +6% section.")
    fig.subplots_adjust(left=0.13, right=0.94, top=0.92, bottom=0.05)
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------ figure 2
def fig_progress(lad, base_E, path):
    counts = sorted({r["n_sections"] for m in lad for r in lad[m]["records"]})
    cols = 4
    rows_ = math.ceil(len(counts) / cols)
    fig, axs = plt.subplots(rows_, cols, figsize=(13, 3.2 * rows_ + 0.8), squeeze=False)
    for ax, n in zip(axs.ravel(), counts):
        for m in sorted(lad):
            rec = next((r for r in lad[m]["records"] if r["n_sections"] == n), None)
            if rec is None or not rec["history"]:
                continue
            h = np.array(rec["history"])
            xs = np.concatenate([h[:, 0], [rec["cumulative_sims"]]])
            ys = 100 * (base_E - np.concatenate([h[:, 1], [h[-1, 1]]])) / base_E
            ax.step(xs, ys, where="post", color=MCOL[m], label=m)
            ax.plot(xs[0], ys[0], "o", color=MCOL[m], ms=4)
        ax.set_title(f"N = {n}")
        ax.set_xscale("log")
        ax.set_ylim(-1.5, 33)                  # one shared scale so panels compare honestly
        ax.xaxis.set_major_formatter(matplotlib.ticker.LogFormatterSciNotation(labelOnlyBase=True))
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.set_xlabel("cumulative whole-route simulations")
    for ax in axs.ravel()[len(counts):]:
        ax.axis("off")
    for ax in axs[:, 0]:
        ax.set_ylabel("best feasible saving (%)")
    h_, l_ = axs[0, 0].get_legend_handles_labels()
    fig.legend(h_, ["A: NM inner, flat start", "B: bisection, flat start", "C: bisection, inherited start",
                    "D: C + secant, tight scoring"][:len(h_)], loc="lower center", ncol=4, fontsize=8.5,
               bbox_to_anchor=(0.5, 0.0))
    _title(fig, "Best feasible energy reached vs simulations spent",
           "Dot = first feasible run. C and D are charged for every coarser solve, so their curves start "
           "to the right. Higher is better.")
    fig.tight_layout(rect=(0, 0.05, 1, 0.92))
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------ inner solver figures
def fig_scans(inner, path, target=120.0, tol=0.1):
    names = list(inner["scans"])
    fig, axs = plt.subplots(2, len(names), figsize=(13, 6.2), sharex=True,
                            gridspec_kw={"height_ratios": [2, 1]})
    for j, name in enumerate(names):
        s = inner["scans"][name]
        k = np.array([p["k"] for p in s])
        t = np.array([p["time_s"] for p in s], float)
        sh = np.array([p["shifts"] for p in s])
        fin = np.isfinite(t)
        ax = axs[0, j]
        ax.axhspan(target - tol, target + tol, color=ORANGE, alpha=0.25, lw=0)
        ax.axhline(target, color=ORANGE, lw=0.8)
        ax.plot(k[fin], t[fin], ".", ms=2.5, color=BLUE)
        ax.plot(k[~fin], np.full((~fin).sum(), 245), "x", ms=4, color=INK2)
        ax.set_ylim(60, 250)
        ax.set_title(name, fontsize=9)
        jumps = np.nonzero(np.abs(np.diff(t)) > 2.0)[0]
        for i in jumps:
            if fin[i] and fin[i + 1]:
                ax.axvline(k[i], color=GRID, lw=0.8)
        axs[1, j].step(k, sh, where="mid", color=AQUA, lw=1.2)
        axs[1, j].set_xlabel("throttle scale k (max section throttle)")
        # zoom inset near the target
        near = fin & (np.abs(t - target) < 6)
        if near.sum() > 3:
            ins = ax.inset_axes([0.1, 0.07, 0.4, 0.4])
            ins.axhspan(target - tol, target + tol, color=ORANGE, alpha=0.25, lw=0)
            ins.plot(k[near], t[near], ".-", ms=3, lw=0.6, color=BLUE)
            ins.tick_params(labelsize=7)
            ins.set_title("near 120 s", fontsize=7, fontweight="normal")
    axs[0, 0].set_ylabel("arrival time (s)")
    axs[1, 0].set_ylabel("gear changes")
    axs[0, 0].text(1.0, 236, "x = never arrives", fontsize=7.5, color=INK2, va="top", ha="right")
    _title(fig, "Inner problem: arrival time against throttle scale for three fixed shapes",
           "Band = 120 +/- 0.1 s. Steps in the gear-change count line up with jumps in arrival time.")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_inner_cost(inner, path):
    rows = inner["rows"]
    solvers = list(dict.fromkeys(r["solver"] for r in rows))
    cols = [BLUE, ORANGE, YELLOW]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    rng = np.random.default_rng(0)
    for i, (s, c) in enumerate(zip(solvers, cols)):
        sub = [r for r in rows if r["solver"] == s]
        sims = np.array([r["sims"] for r in sub])
        ok = np.array([r["found"] for r in sub])
        y = i + rng.uniform(-0.18, 0.18, len(sims))
        ax.scatter(sims[ok], y[ok], s=18, color=c, lw=0, alpha=0.8)
        ax.scatter(sims[~ok], y[~ok], s=30, marker="x", color=INK2)
        ax.text(max(sims.max(), 1) * 1.15, i, f"median {np.median(sims):.0f} sims, found {ok.sum()}/{len(ok)}",
                va="center", fontsize=8)
    ax.set_yticks(range(len(solvers)), solvers)
    ax.set_xscale("log")
    ax.set_xlabel("whole-route simulations to time-match one shape (log)")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0.8, 400)
    ax.set_title("Inner solvers on identical shapes (x = no feasible scale found)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------ resolution, multistart, finish, dt, hunting
def fig_resolution(lad, base_E, path):
    fig, axs = plt.subplots(1, 2, figsize=(12, 4))
    for m in sorted(lad):
        recs = [r for r in lad[m]["records"] if r["feasible"]]
        n = [r["n_sections"] for r in recs]
        axs[0].plot(n, [r["saving_percent"] for r in recs], "o-", color=MCOL[m], ms=5, label=m)
        axs[1].plot(n, [r["cumulative_sims"] for r in recs], "o-", color=MCOL[m], ms=5, label=m)
    for ax in axs:
        ax.set_xscale("log", base=2)
        ax.set_xticks([1, 2, 4, 8, 16, 32, 64], ["1", "2", "4", "8", "16", "32", "64"])
        ax.set_xlabel("throttle sections")
    axs[0].set_ylabel("best feasible saving (%)")
    axs[0].set_ylim(-1, None)
    axs[1].set_ylabel("cumulative simulations")
    axs[1].set_yscale("log")
    axs[0].legend(loc="lower right")
    axs[0].set_title("Saving by resolution")
    axs[1].set_title("Cost by resolution (C, D include coarser solves)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_multistart(ms, path):
    methods = sorted({r["method"] for r in ms})
    fig, ax = plt.subplots(figsize=(8, 3.4))
    for i, m in enumerate(methods):
        s = [r["saving_percent"] for r in ms if r["method"] == m and r["saving_percent"] is not None]
        ax.scatter(s, np.full(len(s), i), s=40, color=MCOL[m], edgecolor=SURF, lw=1.2, zorder=3)
        if s:
            ax.plot([min(s), max(s)], [i, i], color=GRID, lw=3, zorder=1)
            ax.text(max(s) + 0.15, i, f"{min(s):.1f}-{max(s):.1f}%", va="center", fontsize=8)
    ax.set_yticks(range(len(methods)), methods)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("best feasible saving (%), rolling mile, 8 sections, 6 documented starts")
    ax.set_title("How much the result depends on the starting simplex")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_time_step(ts, path):
    profiles = list(dict.fromkeys(r["profile"] for r in ts))
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.axhspan(-0.1, 0.1, color=ORANGE, alpha=0.2, lw=0)
    for p, c in zip(profiles, [MUTED, MAGENTA, AQUA]):
        sub = [r for r in ts if r["profile"] == p]
        ax.plot([r["dt_s"] for r in sub], [r["time_error_s"] for r in sub], "o-", color=c, label=p)
    ax.set_xscale("log")
    ax.set_xticks([0.025, 0.05, 0.1], ["0.025", "0.05", "0.1"])
    ax.set_xlabel("physics step (s); controller update 0.1 s and forecast 1.0 s held fixed")
    ax.set_ylabel("arrival time error (s)")
    ax.legend(fontsize=8)
    ax.set_title("Replaying stored profiles at finer physics steps (band = +/-0.1 s window)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_hunting(hu, path):
    fig, axs = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    for key, c, lw in (("disabled", ORANGE, 0.8), ("enabled", BLUE, 2.0)):
        d = {k: np.asarray(v, float) for k, v in hu[key]["trace"].items()}
        climb = d["grade_percent"] > 0
        t = d["t_s"]
        axs[0].step(t, d["gear"], where="post", color=c, lw=lw,
                    label=f"upshift margin {key}: {hu[key]['shifts_on_climb']} shifts, "
                          f"{hu[key]['rapid_reversals']} reversals <= 2 s")
        axs[1].plot(t, d["fc_up_dv_m_s"], color=c, lw=0.8)
        axs[2].plot(t, d["v_m_s"] * 3.6, color=c, lw=lw)
    axs[1].axhline(0.10, color=INK2, lw=0.8)
    axs[1].text(0, 0.12, " +0.10 m/s upshift reserve", fontsize=7.5, color=INK2)
    axs[0].set_ylabel("gear")
    axs[1].set_ylabel("forecast dv if\nupshifting (m/s)")
    axs[2].set_ylabel("speed (km/h)")
    axs[2].set_xlabel("time (s)")
    axs[0].legend(fontsize=8, loc="lower left")
    _title(fig, "Steep +7% climb at constant throttle 0.70: 3rd/4th shift decisions")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------ report
def _fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def table(rows, cols, heads=None, nd=2):
    heads = heads or cols
    out = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(_fmt(r.get(c), nd) for c in cols) + " |")
    return "\n".join(out)


def build(path: Path):
    cfg = load_reference_config()
    fig_dir = path / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    lad = _load(path, "ladders.json")
    inner = _load(path, "inner_comparison.json")
    ms = _load(path, "multistart.json")
    fs = _load(path, "finish_speed.json")
    ts = _load(path, "time_step.json")
    hu = _load(path, "hunting.json")
    meta = _load(path, "meta.json")
    base = Case(cfg, "rolling").run(ref_vectors()["rolling/constant"])
    base_E = base.energy_J
    recs = [r for m in lad for r in lad[m]["records"]]
    best = min((r for r in recs if r["feasible"]), key=lambda r: r["energy_kJ"])
    fig_overlay(cfg, best, fig_dir / "1_overlay_distance.png")
    fig_progress(lad, base_E, fig_dir / "2_energy_vs_calls.png")
    fig_scans(inner, fig_dir / "3_inner_scans.png")
    fig_inner_cost(inner, fig_dir / "4_inner_cost.png")
    fig_resolution(lad, base_E, fig_dir / "5_resolution.png")
    if ms:
        fig_multistart(ms, fig_dir / "6_multistart.png")
    fig_time_step(ts, fig_dir / "7_time_step.png")
    fig_hunting(hu, fig_dir / "8_gear_hunting.png")

    m_kg = cfg["vehicle"]["mass_kg"]
    for r in recs:
        r["ke_finish_kJ"] = 0.5 * m_kg * r["finish_speed_m_s"] ** 2 / 1e3 if r["feasible"] else None
        r["calls"] = r["cumulative_sims"]
    rows = sorted(recs, key=lambda r: (r["n_sections"], r["method"]))
    retimed = path / "retimed_finalists.csv"
    lines = [f"# Results report ({path.name})", "",
             "Reconstruction on substitute inputs (v1 reference case, calibrated Fiesta 1.25 82 PS). "
             "Rolling mile, 120 +/- 0.1 s, free finishing speed unless stated.", "",
             "![overlay](figures/1_overlay_distance.png)", "", "![progress](figures/2_energy_vs_calls.png)", "",
             f"Constant-throttle baseline: {base.throttle[0]:.4f} throttle, {base_E / 1e3:.1f} kJ, "
             f"{base.time_s:.3f} s, finish {base.finish_speed_m_s:.2f} m/s "
             f"(1/2 m v^2 = {0.5 * m_kg * base.finish_speed_m_s ** 2 / 1e3:.1f} kJ).", "",
             "## Main comparison", "",
             table(rows, ["method", "n_sections", "energy_kJ", "saving_percent", "residual_s", "finish_speed_m_s",
                          "ke_finish_kJ", "shifts", "calls", "first_feasible_at", "feasible_shapes", "wall_s",
                          "budget_limited"],
                   ["method", "N", "energy kJ", "saving %", "residual s", "finish m/s", "1/2mv^2 kJ", "shifts",
                    "calls (cum.)", "first feasible at", "feasible shapes", "wall s", "budget-limited"]), "",
             "## Inner solvers on identical shapes", "", "![scans](figures/3_inner_scans.png)", "",
             "![cost](figures/4_inner_cost.png)", ""]
    solv = list(dict.fromkeys(r["solver"] for r in inner["rows"]))
    summ = []
    for s in solv:
        sub = [r for r in inner["rows"] if r["solver"] == s]
        summ.append({"solver": s, "shapes": len(sub), "found": sum(r["found"] for r in sub),
                     "median_sims": float(np.median([r["sims"] for r in sub])),
                     "mean_sims": float(np.mean([r["sims"] for r in sub])),
                     "max_sims": max(r["sims"] for r in sub)})
    lines += [table(summ, ["solver", "shapes", "found", "median_sims", "mean_sims", "max_sims"], nd=1), ""]
    named = [r for r in inner["rows"] if r["log"] is not None]
    lines += [table(named, ["shape", "solver", "found", "sims", "residual_s", "energy_kJ", "message"]), ""]
    lines += ["## Resolution", "", "![resolution](figures/5_resolution.png)", ""]
    if retimed.exists():
        import csv
        rt = list(csv.DictReader(open(retimed, encoding="utf-8")))
        for r in rt:
            for k in ("energy_kJ", "residual_s", "retimed_energy_kJ", "retimed_residual_s"):
                r[k] = float(r[k]) if r[k] not in ("", "None") else None
        lines += ["### Finalists re-timed to |r| <= 0.01 s", "",
                  table(rt, ["n_sections", "method", "energy_kJ", "residual_s", "retimed_energy_kJ",
                             "retimed_residual_s", "rank_original", "rank_retimed"]), ""]
    if ms:
        lines += ["## Multistart (rolling, N = 8)", "", "![ms](figures/6_multistart.png)", "",
                  table(ms, ["method", "perturbation", "energy_kJ", "saving_percent", "residual_s", "sims", "stop"]), ""]
    if fs:
        lines += ["## Matched finishing speed (within 0.2 m/s of the constant car)", "",
                  table(fs, ["n_sections", "baseline_kJ", "baseline_finish_m_s", "energy_kJ", "finish_m_s",
                             "residual_s", "saving_percent", "kept_baseline", "sims", "stop"]), ""]
    lines += ["## Time-step sensitivity (controller 0.1 s, forecast 1.0 s fixed)", "",
              "![dt](figures/7_time_step.png)", "",
              table(ts, ["profile", "dt_s", "time_error_s", "in_window", "energy_kJ", "shifts", "same_shift_sequence",
                         "max_shift_shift_m", "retimed_energy_kJ", "retimed_residual_s"], nd=3), "",
              "## Gear hunting", "", "![hunting](figures/8_gear_hunting.png)", "",
              table([{"upshift margin": k, **{kk: v for kk, v in hu[k].items() if kk != "trace"}} for k in hu],
                    ["upshift margin", "shifts_on_climb", "rapid_reversals", "time_s", "energy_kJ"]), "",
              "## Provenance", "",
              f"* Reference parity (time, energy, finish speed, gear-change locations): "
              f"**{'OK' if meta['reference_parity'] else 'FAILED'}** (`reference_replay.csv`).",
              f"* Inputs copied from `{meta['inputs']['source']}`, commit `{meta['inputs']['source_git_commit']}`; "
              f"SHA-256 of every copied file in `reference/COPIED_FROM.json`.",
              f"* Software: {', '.join(f'{k} {v}' for k, v in meta['versions'].items())}.",
              f"* Total wall time {meta['wall_s']:.0f} s."]
    (path / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {path / 'REPORT.md'}")
