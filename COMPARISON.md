# v1 (tag `v1.0.0`, brief v1) vs v2 (this branch, brief v2)

Both versions use the same vehicle, route, gear controller and baseline. `config/vehicle_route.json` is byte-identical to v1's `config/substitute.json` (git shows it as a pure rename). v2 replays v1's saved reference case bit-for-bit (`results/v2/full/reference_replay.csv`), so the differences below come from the numerical method, not the model.

## Rolling mile, 120 ± 0.1 s, free finishing speed

Saving is measured against the same time-matched constant throttle: 0.437 throttle, 664.4 kJ.

| Sections | v1: saving / simulations | v2 A (historical, budgeted) | v2 B (bisection) | v2 C (bisection + inherited start) |
|---|---|---|---|---|
| 2 | 26.87% / 3 297 | 26.88% / 1 500 | 26.88% / 397 | 26.87% / 165 cum. |
| 4 | 27.06% / 15 092 | 27.10% / 4 000 | 27.06% / 1 258 | 27.04% / 966 cum. |
| 8 | 28.65% / 31 976 | 28.79% / 8 000 | 29.82% / 2 080 | **30.07% / 2 797 cum.** |
| 16 | 28.47% / 53 675 | 28.75% / 15 000 | 29.58% / 6 353 | 30.07% / 17 797 cum. (no gain) |
| 32 | – | 26.58% / 25 000 | 27.90% / 25 000 | 30.07% / 42 797 cum. (no gain) |
| 64 | – | – | – | 30.07% / 77 797 cum. (no gain) |

v1 simulation counts include repeats, because v1 had no cache. v2 counts unique simulations under a shared cache policy. "cum." means C is also charged for every coarser solve it built on.

## What v2 adds that v1 could not say

| Question | v1 | v2 |
|---|---|---|
| Simulations to time-match one shape | ~30 (Nelder–Mead) | median 12 (bisection); 36/43 shapes found vs 30/43 |
| Sensitivity to the starting simplex (N = 8) | not measured | A 22.1–30.0%, B 20.8–30.1%, **C 29.7–30.1%** |
| Saving if the finishing speed must match the baseline (±0.2 m/s) | not measured | **3.1%** (N = 8) |
| Best profile at 0.05 / 0.025 s physics steps | not measured | leaves the ±0.1 s window (+0.27 / +0.35 s); re-timed saving about 29.8% |
| v1 best profile at finer steps | – | leaves the window (+0.70 / +0.37 s) |
| Ranking after re-timing finalists to a common residual | not measured | C < D < B < A holds for N ≥ 8 |
| Gear hunting on a +7% climb | 1 vs 377 shifts | same, plus 362 rapid reversals within 2 s without the margin |
| Full run time | ~25 min (serial) | ~5 min (parallel processes) |

## Unchanged

Calibrated performance is unchanged: 0–100 km/h in 13.30 s, top speed 168.0 km/h, 0–60 mph in 12.48 s. A test checks this in v2.

## Where things are

* **v1 results:** moved unchanged to `results/v1/`. The code that produced them is at tag `v1.0.0`.
* **v2 results:** `results/v2/full/REPORT.md` and its CSVs and figures.
* **Briefs:** `docs/briefs/brief-v1.md` and `docs/briefs/brief-v2.md`.
