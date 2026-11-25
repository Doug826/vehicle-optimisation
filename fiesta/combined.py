"""Combined variant: every single change that raised the saving, applied together
(calibrated car kept). Run: python -m fiesta.combined"""
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .sensitivity import run_variant

COMBO = {"vehicle.min_engaged_rpm": 1000.0, "simulation.target_time_s": 110.0,
         "simulation.time_tolerance_s": 0.5}

if __name__ == "__main__":
    jobs = [("combined: rpm 1000 + 110 s + +/-0.5 s", COMBO, r, n, 2000 if n == 8 else 5000, None)
            for r, n in [("rolling", 8), ("flat", 8), ("rolling", 16), ("flat", 16)]]
    with ProcessPoolExecutor(4) as ex:
        rows = list(ex.map(run_variant, jobs))
    for r in rows:
        print(r["route"], r["n_sections"], round(r["saving_percent"], 2), "budget-limited" if r["budget_limited"] else "")
    Path("results/sensitivity/combined.json").write_text(json.dumps(rows, indent=2, default=float))
