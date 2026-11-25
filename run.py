"""Fiesta optimisation, brief v2: better time matching, warm starts, finer throttle control.

    python run.py quick      # ~1 min: reference parity, inner solvers on 3 shapes, A/B/C/D to 4 sections
    python run.py full       # ~15 min: everything in the brief, sections 1..32 (+64 for C/D)
    python run.py report     # rebuild figures, CSVs and REPORT.md from saved results
    python -m pytest -q      # tests
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

from fiesta import experiments as X
from fiesta.config import ROOT
from fiesta.reference import load_reference_config, parity_ok, replay_reference

RESULTS = ROOT / "results" / "v2"


def versions() -> dict:
    import matplotlib, numba, scipy
    return {"python": sys.version.split()[0], "platform": platform.platform(), "numpy": np.__version__,
            "scipy": scipy.__version__, "matplotlib": matplotlib.__version__, "numba": numba.__version__}


def save(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)),
                    encoding="utf-8")


def save_csv(path: Path, rows: list[dict], cols: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = cols or [k for k in rows[0] if not isinstance(rows[0][k], (list, dict))]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def compute(mode: str):
    out = RESULTS / mode
    t_all = time.perf_counter()
    cfg = load_reference_config()
    exp = X.load_experiment()
    meta = {"mode": mode, "versions": versions(),
            "inputs": json.loads((ROOT / "reference" / "COPIED_FROM.json").read_text(encoding="utf-8")),
            "experiment_config": exp}

    print("1/8 reference replay ...", flush=True)
    ref = replay_reference(cfg)
    meta["reference_parity"] = parity_ok(ref)
    save_csv(out / "reference_replay.csv", ref)
    if not meta["reference_parity"]:
        raise SystemExit("reference replay does not match the saved v1 traces - resolve before comparing")

    print("2/8 inner solvers on identical shapes ...", flush=True)
    inner = X.inner_comparison(cfg, exp if mode == "full" else dict(exp, inner_comparison=dict(
        exp["inner_comparison"], random_shapes=6, scan_points=150)))
    save(out / "inner_comparison.json", inner)
    save_csv(out / "inner_comparison.csv", inner["rows"])

    print("3/8 A/B/C/D ladders ...", flush=True)
    lad = X.ladders(exp, quick=(mode == "quick"))
    save(out / "ladders.json", lad)
    rows = [r for m in lad for r in lad[m]["records"]]
    save_csv(out / "ladders.csv", rows)

    print("4/8 re-time finalists ...", flush=True)
    rt = X.retime_finalists(cfg, exp, lad)
    save_csv(out / "retimed_finalists.csv", rt)

    if mode == "full":
        print("5/8 multistart ...", flush=True)
        ms = X.multistart(exp, lad)
        save(out / "multistart.json", ms)
        save_csv(out / "multistart.csv", ms)
        print("6/8 matched finishing speed ...", flush=True)
        fs = X.finish_speed(exp)
        save(out / "finish_speed.json", fs)
        save_csv(out / "finish_speed.csv", fs)
    print("7/8 time-step sensitivity ...", flush=True)
    v = X.ref_vectors()
    best = min((r for r in rows if r["feasible"]), key=lambda r: r["energy_kJ"])
    profiles = {"constant baseline": ("rolling", v["rolling/constant"]),
                "v1 best (N=8)": ("rolling", v["rolling/N8"]),
                f"v2 best ({best['method']}, N={best['n_sections']})": ("rolling", best["throttle"])}
    ts = X.time_step(cfg, exp, profiles)
    save_csv(out / "time_step.csv", ts)
    save(out / "time_step.json", ts)
    print("8/8 gear hunting ...", flush=True)
    hu = X.hunting(cfg, exp)
    save(out / "hunting.json", hu)
    meta["wall_s"] = time.perf_counter() - t_all
    save(out / "meta.json", meta)
    print(f"done in {meta['wall_s']:.0f} s -> {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["quick", "full", "report"])
    ap.add_argument("--from", dest="src", default="full", help="results set used by 'report'")
    a = ap.parse_args(argv)
    if a.mode in ("quick", "full"):
        compute(a.mode)
        src = a.mode
    else:
        src = a.src
    from fiesta import report
    report.build(RESULTS / src)


if __name__ == "__main__":
    main()
