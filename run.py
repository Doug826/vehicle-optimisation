"""Fiesta energy-optimisation reconstruction.

    python run.py quick        # fast end-to-end example (1/2/4 sections, small budgets)
    python run.py full         # full 1/2/4/8/16 sweep on every sweep route + reference case
    python run.py perf         # 0-60 / 0-100 / top speed / gear-hunting checks
    python run.py calibrate    # fit frontal area + shift time to Ford's published figures
    python run.py sensitivity  # how declared assumptions move the rolling N=8 saving
    python run.py visuals      # journey dashboards from results/full
    python run.py replay DIR   # replay a saved reference case and compare
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from fiesta import model as M
from fiesta.config import ROOT, Case, load_config
from fiesta.optimise import constant_baseline, optimise_profile
from fiesta import performance as perf
from fiesta import plots

RESULTS = ROOT / "results"


# --------------------------------------------------------------------------- helpers
def versions() -> dict:
    import matplotlib, scipy
    out = {"python": sys.version.split()[0], "platform": platform.platform(),
           "numpy": np.__version__, "scipy": scipy.__version__, "matplotlib": matplotlib.__version__}
    try:
        import numba
        out["numba"] = numba.__version__
    except Exception:
        out["numba"] = None
    return out


def write_trace(path: Path, d: dict):
    cols = M.TRACE_COLS
    arr = np.column_stack([d[c] for c in cols])
    np.savetxt(path, arr, delimiter=",", header=",".join(cols), comments="", fmt="%.9g")


def write_table(path_csv: Path, path_md: Path, rows: list[dict], cols: list[str]):
    with open(path_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    def fmt(v):
        if isinstance(v, float):
            return f"{v:.3f}" if abs(v) < 1000 else f"{v:.0f}"
        return str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r.get(c, "")) for c in cols) + " |" for r in rows]
    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def hill_marks(case: Case):
    rt = case.cfg["routes"][case.route]
    k = rt.get("steep_approach_segment_index")
    if k is None:
        return None
    return (k * case.seg_len, (k + 1) * case.seg_len, (k + 1) * case.seg_len)


def speed_at(d, x):
    return float(np.interp(x, d["x_m"], d["v_m_s"]) * 3.6)


def hill_behaviour(case: Case, d: dict) -> dict | None:
    """Let the trace say whether speed is built before the climb and shed near the top."""
    rt = case.cfg["routes"][case.route]
    k = rt.get("steep_approach_segment_index")
    if k is None:
        return None
    g = case.grades
    foot_seg = k
    while foot_seg > 0 and g[foot_seg - 1] > 0:
        foot_seg -= 1
    foot = foot_seg * case.seg_len
    crest = (k + 1) * case.seg_len
    xs = d["x_m"]
    on_climb = (xs >= foot) & (xs <= crest)
    v_before = speed_at(d, max(foot - case.seg_len, 0))
    v_foot = speed_at(d, foot)
    v_crest = speed_at(d, crest)
    v_crest_m100 = speed_at(d, crest - 100)
    thr_before = float(np.mean(d["throttle"][(xs >= foot - case.seg_len) & (xs < foot)]))
    thr_top = float(np.mean(d["throttle"][(xs >= crest - 100) & (xs < crest)]))
    steep = k * case.seg_len
    thr_approach = float(np.mean(d["throttle"][(xs >= foot) & (xs < steep)])) if steep > foot else np.nan
    thr_steep = float(np.mean(d["throttle"][(xs >= steep) & (xs < crest)]))
    v_steep = speed_at(d, steep)
    return {
        "climb_foot_m": foot, "steep_entry_m": steep, "crest_m": crest,
        "mean_throttle_on_approach": thr_approach, "mean_throttle_on_steep_section": thr_steep,
        "speed_kmh_at_steep_entry": v_steep,
        "builds_speed_into_steep_section": v_steep > max(v_foot, v_before) + 0.5,
        "speed_kmh_one_segment_before_foot": v_before, "speed_kmh_at_foot": v_foot,
        "min_speed_kmh_on_climb": float(d["v_m_s"][on_climb].min() * 3.6),
        "speed_kmh_100m_before_crest": v_crest_m100, "speed_kmh_at_crest": v_crest,
        "mean_throttle_segment_before_foot": thr_before, "mean_throttle_last_100m_of_climb": thr_top,
        "builds_speed_before_hill": v_foot > v_before + 0.5,
        "sheds_speed_near_top": v_crest < v_crest_m100 - 0.5,
    }


def row_for(route, n, r, base_E, extra):
    return {
        "route": route, "n_sections": n, "status": r.status_name,
        "time_s": r.time_s, "energy_kJ": r.energy_J / 1e3,
        "finish_speed_kmh": r.finish_speed_m_s * 3.6, "shifts": r.shifts,
        "saving_percent": 100 * (base_E - r.energy_J) / base_E, **extra,
    }


# --------------------------------------------------------------------------- sweep
def sweep(cfg: dict, mode: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "traces").mkdir(exist_ok=True)
    opt = cfg["optimiser"]
    budgets = opt["budgets"][mode]
    routes = opt["sweep_routes"] if mode == "full" else ["rolling"]
    rows, runs, vectors, best_by_route = [], {}, {}, {}
    for route in routes:
        case = Case(cfg, route)
        print(f"[{route}] constant-throttle baseline ...", flush=True)
        b = constant_baseline(case, opt)
        if not b["feasible"]:
            raise RuntimeError(f"no in-window constant throttle on {route}")
        base = case.run(b["result"].throttle, record=True)
        base_d = base.trace_dict()
        write_trace(out / "traces" / f"{route}_constant.csv", base_d)
        vectors[f"{route}/constant"] = base.throttle.tolist()
        rows.append(row_for(route, "constant", base, base.energy_J, {
            "sim_calls": b["sim_calls"], "runtime_s": b["runtime_s"], "budget_limited": False,
            "termination": b["inner_message"]}))
        runs[f"{route}/constant"] = {k: v for k, v in b.items() if k != "result"}
        best = None
        for n_str, cap in budgets.items():
            n = int(n_str)
            t0 = time.perf_counter()
            o = optimise_profile(case, n, opt, cap)
            print(f"[{route}] N={n:2d} cap={cap}: {o['outer_message']} "
                  f"({o['sim_calls']} sims, {time.perf_counter() - t0:.1f} s)", flush=True)
            if not o["feasible"]:
                rows.append({"route": route, "n_sections": n, "status": "no feasible run",
                             "sim_calls": o["sim_calls"], "runtime_s": o["runtime_s"],
                             "budget_limited": o["budget_limited"], "termination": o["outer_message"]})
                continue
            rep = case.run(o["result"].throttle, record=True)  # replay the saved vector
            assert abs(rep.energy_J - o["result"].energy_J) < 1e-6
            d = rep.trace_dict()
            write_trace(out / "traces" / f"{route}_N{n:02d}.csv", d)
            vectors[f"{route}/N{n}"] = rep.throttle.tolist()
            rows.append(row_for(route, n, rep, base.energy_J, {
                "sim_calls": o["sim_calls"], "runtime_s": o["runtime_s"],
                "budget_limited": o["budget_limited"], "termination": o["outer_message"]}))
            runs[f"{route}/N{n}"] = {k: v for k, v in o.items() if k != "result"}
            if best is None or rep.energy_J < best[1].energy_J:
                best = (n, rep, d)
        best_by_route[route] = (case, base, base_d, best)
        # figures
        n, rep, d = best
        marks = hill_marks(case)
        plots.distance_plot(case, base_d, d, out / f"{route}_distance.png",
                            f"{route} course: constant throttle vs best profile (N={n})", marks)
        plots.time_plot(base_d, d, out / f"{route}_time.png",
                        f"{route} course: speed and shaft energy vs time (N={n})", case.target)

    plots.savings_plot([r for r in rows if r["n_sections"] != "constant" and "saving_percent" in r],
                       out / "savings.png")
    cols = ["route", "n_sections", "status", "time_s", "energy_kJ", "finish_speed_kmh", "shifts",
            "saving_percent", "sim_calls", "runtime_s", "budget_limited", "termination"]
    write_table(out / "summary.csv", out / "summary.md", rows, cols)
    prof = [r for r in rows if r["n_sections"] != "constant" and "saving_percent" in r]
    mx = max(prof, key=lambda r: r["saving_percent"])
    hills = {rt: {"constant": hill_behaviour(c, bd), f"profile_N{bb[0]}": hill_behaviour(c, bb[2])}
             for rt, (c, _, bd, bb) in best_by_route.items()}
    meta = {
        "mode": mode, "budgets": budgets, "versions": versions(),
        "solver_settings": {k: opt[k] for k in opt if k != "budgets"},
        "maximum_saving": {"route": mx["route"], "n_sections": mx["n_sections"],
                           "saving_percent": mx["saving_percent"],
                           "comparison": "best feasible N-section profile vs lowest-energy in-window constant throttle, same vehicle/gear policy/accounting/finish"},
        "hill_behaviour": hills, "runs": runs,
    }
    (out / "runs.json").write_text(json.dumps(meta, indent=2, default=float), encoding="utf-8")
    (out / "throttle_vectors.json").write_text(json.dumps(vectors, indent=2), encoding="utf-8")
    print(f"maximum saving {mx['saving_percent']:.1f}% on {mx['route']} with N={mx['n_sections']}")
    return {"rows": rows, "meta": meta, "best": best_by_route, "vectors": vectors}


def export_reference(cfg: dict, res: dict, dest: Path):
    """Portable replay bundle for the second project."""
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "timeseries").mkdir(parents=True)
    cases, expected = {}, {}
    for route, (case, base, base_d, (n, rep, d)) in res["best"].items():
        cases[route] = case.resolved()
        write_trace(dest / "timeseries" / f"{route}_constant.csv", base_d)
        write_trace(dest / "timeseries" / f"{route}_best_N{n:02d}.csv", d)
        expected[f"{route}/constant"] = {"time_s": base.time_s, "energy_J": base.energy_J,
                                         "finish_speed_m_s": base.finish_speed_m_s, "shifts": base.shifts}
        expected[f"{route}/best_N{n}"] = {"time_s": rep.time_s, "energy_J": rep.energy_J,
                                          "finish_speed_m_s": rep.finish_speed_m_s, "shifts": rep.shifts}
    (dest / "resolved_cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    (dest / "throttle_vectors.json").write_text(json.dumps(res["vectors"], indent=2), encoding="utf-8")
    (dest / "expected_results.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    (dest / "solver_settings.json").write_text(json.dumps(
        {"optimiser": cfg["optimiser"], "runs": res["meta"]["runs"]}, indent=2, default=float), encoding="utf-8")
    (dest / "software_versions.json").write_text(json.dumps(versions(), indent=2), encoding="utf-8")
    shutil.copy(ROOT / "config" / "substitute.json", dest / "config_used.json")
    shutil.copy(RESULTS / "full" / "summary.csv", dest / "summary.csv")
    shutil.copy(RESULTS / "full" / "summary.md", dest / "summary.md")
    (dest / "README.md").write_text(REF_README, encoding="utf-8")


REF_README = """# Reference case (portable)

Inputs are the labelled SUBSTITUTE (no original university files were available).

* `config_used.json` - the full configuration file, replayable as-is.
* `resolved_cases.json` - vehicle, route, dynamics and controller settings per route, plus
  integration/finish/energy conventions and derived quantities.
* `throttle_vectors.json` - the exact throttle vector for every saved run (key `route/N<n>`
  or `route/constant`); sections are equal lengths of the route, looked up by distance.
* `expected_results.json` - time, shaft energy, finish speed and shifts each replay must reproduce.
* `timeseries/*.csv` - 0.1 s traces (constant baseline and best profile per route), including
  the per-step gear forecasts (rpm, wheel force, signed dv, energy for down/hold/up).
* `summary.csv|md`, `solver_settings.json`, `software_versions.json`.

Replay: `python run.py replay results/reference-case`
"""


def replay(dest: Path) -> bool:
    cfg = json.loads((dest / "config_used.json").read_text(encoding="utf-8"))
    vec = json.loads((dest / "throttle_vectors.json").read_text(encoding="utf-8"))
    exp = json.loads((dest / "expected_results.json").read_text(encoding="utf-8"))
    ok = True
    for key, e in exp.items():
        route, which = key.split("/")
        vkey = f"{route}/constant" if which == "constant" else f"{route}/N{which.split('_N')[1]}"
        r = Case(cfg, route).run(vec[vkey])
        good = abs(r.energy_J - e["energy_J"]) < 1e-3 and abs(r.time_s - e["time_s"]) < 1e-9
        ok &= good
        print(f"{key:24s} time {r.time_s:9.4f} s  energy {r.energy_J / 1e3:9.3f} kJ  "
              f"{'OK' if good else 'MISMATCH'}")
    return ok


# --------------------------------------------------------------------------- perf
def performance(cfg: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    pt = cfg["performance_test"]
    ref = pt["reference"]
    acc = {name: perf.acceleration(cfg, pol) for name, pol in pt["policies"].items()}
    top = perf.steady_top_speed(Case(cfg, "flat"))
    top_lit = perf.steady_top_speed(Case(cfg, "flat", {"vehicle.include_rotational_inertia": False}))
    sim_top = perf.simulated_top_speed(cfg, pt["policies"]["documented"])
    cal = perf.calibrate(cfg)
    th = 0.70
    hon = perf.hunting_demo(cfg, th, True)
    hoff = perf.hunting_demo(cfg, th, False)
    plots.accel_plot(acc, ref, out / "acceleration.png")
    plots.hunting_plot(Case(cfg, "steep_hill"), hon["trace"], hoff["trace"], out / "gear_hunting.png", th)
    b = top["best"]
    summary = {
        "reference": ref,
        "acceleration": {n: {k: v for k, v in a.items() if k not in ("trace", "case")} for n, a in acc.items()},
        "errors_vs_ford": {n: {"zero_to_100_kmh_error_s": a["zero_to_100_kmh_s"] - ref["zero_to_100_kmh_s"]}
                           for n, a in acc.items()},
        "top_speed": {"steady_best": b, "per_gear": top["per_gear"],
                      "error_kmh": b["v_max_kmh"] - ref["top_speed_kmh"],
                      "error_mph": b["v_max_mph"] - ref["top_speed_mph"],
                      "simulated_400s_full_throttle": sim_top,
                      "brief_literal_same": top_lit["best"]["v_max_kmh"]},
        "calibration_check": cal,
        "gear_hunting": {"throttle": th,
                         "enabled": {k: v for k, v in hon.items() if k not in ("trace", "result")},
                         "disabled": {k: v for k, v in hoff.items() if k not in ("trace", "result")}},
    }
    (out / "performance.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    for n, a in acc.items():
        write_trace(out / f"acceleration_{n}.csv", a["trace"])
    write_trace(out / "hunting_enabled.csv", hon["trace"])
    write_trace(out / "hunting_disabled.csv", hoff["trace"])
    for n, a in summary["acceleration"].items():
        print(f"{n:14s} 0-60 mph {a['zero_to_60_mph_s']:.2f} s | 0-62 mph {a['zero_to_62_mph_s']:.2f} s | "
              f"0-100 km/h {a['zero_to_100_kmh_s']:.2f} s (Ford 13.3, error {a['zero_to_100_kmh_s'] - 13.3:+.2f} s)")
    print(f"top speed {b['v_max_kmh']:.1f} km/h = {b['v_max_mph']:.1f} mph in gear {b['gear']} at {b['engine_rpm']:.0f} rpm "
          f"(Ford 168 km/h / 104 mph, error {summary['top_speed']['error_mph']:+.1f} mph)")
    print(f"gear hunting at throttle {th}: shifts on climb enabled={hon['shifts_on_climb']} disabled={hoff['shifts_on_climb']}")
    return summary


def sensitivity(cfg_path, out: Path):
    from fiesta.sensitivity import run_all
    out.mkdir(parents=True, exist_ok=True)
    rows = run_all(cfg_path=cfg_path)
    for r in rows:
        print(f"{r['variant']:50s} saving {r['saving_percent']:6.2f}%  ({r['runtime_s']:.0f} s)")
    (out / "sensitivity.json").write_text(json.dumps(rows, indent=2, default=float), encoding="utf-8")
    write_table(out / "sensitivity.csv", out / "sensitivity.md", rows,
                ["variant", "route", "n_sections", "baseline_kJ", "profile_kJ", "saving_percent",
                 "baseline_finish_kmh", "profile_finish_kmh", "budget_limited", "runtime_s"])
    try:
        from fiesta import visuals
        visuals.sensitivity_plot(rows, out / "sensitivity.png")
    except ImportError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["quick", "full", "perf", "replay", "calibrate", "sensitivity", "visuals"])
    ap.add_argument("path", nargs="?", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.mode == "replay":
        sys.exit(0 if replay(Path(a.path or RESULTS / "reference-case")) else 1)
    if a.mode == "sensitivity":
        sensitivity(a.config, RESULTS / "sensitivity")
        return
    if a.mode == "visuals":
        from fiesta import visuals
        visuals.make_all(cfg, RESULTS / "full", RESULTS / "visuals")
        return
    if a.mode == "calibrate":
        print(json.dumps(perf.calibrate(cfg), indent=2))
        print("copy frontal_area_m2 and shift_time_s into the config to apply")
        return
    if a.mode == "perf":
        performance(cfg, RESULTS / "performance")
        return
    res = sweep(cfg, a.mode, RESULTS / a.mode)
    if a.mode == "full":
        performance(cfg, RESULTS / "performance")
        export_reference(cfg, res, RESULTS / "reference-case")
        print("reference case written; replay check:")
        replay(RESULTS / "reference-case")


if __name__ == "__main__":
    main()
