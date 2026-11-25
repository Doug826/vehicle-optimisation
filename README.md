# Fiesta energy-optimisation reconstruction

This is a Python reconstruction of Doug's final-year project, *Vehicle Energy Optimisation over Undulating Terrain*.

> **Inputs are substitutes.** No `original-materials/` were supplied. The vehicle data were checked against Ford's November 2012 press pack, which corrected Cd to 0.328.
>
> **Two unpublished quantities are calibrated to Ford's published performance figures:**
> * frontal area, fitted to the 168 km/h top speed;
> * gear-change time, fitted to the 0–100 km/h time of 13.3 s.
>
> Everything else is a labelled substitute or assumption. See [PROVENANCE.md](PROVENANCE.md). Every input is in one file: [`config/substitute.json`](config/substitute.json).

## Setup (Windows, PowerShell)

```powershell
cd fiesta-reconstruction
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The code was developed on Python 3.14 with numpy 2, scipy, matplotlib and numba. numba is optional: without it the code still runs, about 100× slower.

## Commands

| What | Command | Time |
| --- | --- | --- |
| Quick end-to-end example (rolling course, N = 1/2/4, small budgets) | `python run.py quick` | ~20 s |
| Full sweep (N = 1/2/4/8/16, rolling and flat courses), plus performance checks and the reference case | `python run.py full` | ~25 min |
| Journey dashboards and other figures (needs `results/full`) | `python run.py visuals` | ~30 s |
| Performance checks (0–60, 0–100, top speed, gear hunting) | `python run.py perf` | ~30 s |
| Re-fit frontal area and shift time to Ford's figures | `python run.py calibrate` | ~20 s |
| How individual assumptions move the saving | `python run.py sensitivity` | ~4 min |
| Replay the saved reference case | `python run.py replay results/reference-case` | ~5 s |
| Tests (26) | `python -m pytest -q` | ~30 s |

## Visuals (`results/visuals/`)

| Figure | What it shows |
| --- | --- |
| [rolling_journey_vs_time](results/visuals/rolling_journey_vs_time.png) | Rolling mile against time: speed, acceleration, jerk, throttle, gear, engine rpm, shaft power and cumulative energy. Constant throttle vs the best profile. |
| [rolling_journey_vs_distance](results/visuals/rolling_journey_vs_distance.png) | The same, against distance, with the elevation profile and the steep section shaded. |
| [flat_journey_vs_time](results/visuals/flat_journey_vs_time.png) / [flat_journey_vs_distance](results/visuals/flat_journey_vs_distance.png) | The same for the flat mile. |
| [rolling_engine_map](results/visuals/rolling_engine_map.png) / [flat_engine_map](results/visuals/flat_engine_map.png) | Where the engine runs (torque against rpm), one dot per 0.1 s, against the full-load curve and constant-power lines. |
| [energy_breakdown](results/visuals/energy_breakdown.png) | Where the shaft energy goes: driveline losses, aerodynamic drag, rolling resistance, and kinetic energy carried over the line. |
| [energy_vs_time](results/visuals/energy_vs_time.png) | Every constant throttle from 0.22 to 1.0: journey time against energy, coloured by the highest gear reached. Shows the stuck-in-gear effect. |
| [throttle_profiles](results/visuals/throttle_profiles.png) | The best throttle shape at N = 1/2/4/8/16 on each course. |
| [savings_by_sections](results/visuals/savings_by_sections.png) | Saving against throttle resolution, with the 34% line. |
| [acceleration_dashboard](results/visuals/acceleration_dashboard.png) | The calibrated full-throttle run: speed, acceleration, jerk, gear, rpm and power, with the 0–60 and 0–100 marks. |
| [sensitivity](results/sensitivity/sensitivity.png) | One assumption changed at a time, and how far it moves the saving. |
| [gear_hunting](results/performance/gear_hunting.png) | The 3rd/4th gear hunting on a +7% hill, with the upshift margin on and off. |

## Results

### 1. Performance, calibrated (`results/performance/`)

| Check | Simulated | Ford (p.10) | Error |
| --- | --- | --- | --- |
| 0–100 km/h | **13.30 s** | 13.3 s | 0.00 s |
| 0–62 mph | 13.25 s | 13.3 s (Ford labels its figure "0–100 kph / 0–62 mph") | −0.05 s |
| 0–60 mph | **12.48 s** | *not published* | – |
| Top speed (4th gear, 6037 rpm; 5th gives 163 km/h) | **168.0 km/h = 104.4 mph** | 168 km/h = 104 mph | 0.0 km/h; +0.4 mph against the rounded mph figure |

What changed to get there:

1. **Rolling resistance now rises with speed.** It uses the standard passenger-tyre fit Crr = 0.010 × (1 + V/160), with V in km/h. That gives about 0.0125 at the 40–60 km/h trip speeds (the brief's constant was 0.012) and 0.0205 at 168 km/h.
2. **Frontal area is now 2.066 m²** (the brief had 2.0). That is 80% of Ford's width × height, a typical fill factor for a supermini. Using frontal area alone, with constant rolling resistance, would have needed 2.27 m² (88%), which is implausibly large.
3. **The gear change in the acceleration test now takes 0.50 s** (previously assumed 0.4 s), a plausible manual shift.

Everything else is unchanged: mass, gearing, Cd, torque curve and driveline efficiency are not fitted. The tests now require 0–100 km/h within 0.1 s and top speed within 0.5 mph. They also check that the calibration reproduces itself, that the fitted area is physically plausible, and the power balance and work–energy theorem.

Doug recalled matching **0–60** within 0.1 s, but Ford publishes no 0–60 time, only 0–100 km/h (≈0–62 mph). If his comparison treated 13.3 s as a 0–60 figure, that would be about 0.8 s off.

### 2. Energy comparison, calibrated car (`results/full/summary.md`)

| Course | N | Shaft energy (kJ) | Finish (km/h) | Saving |
| --- | --- | --- | --- | --- |
| rolling | constant 0.44 | 664.4 | 71.4 | – |
| rolling | 2 / 4 / 8 / 16 | 485.9 / 484.6 / **474.1** / 475.2 | ~37 | 26.9 / 27.1 / **28.6** / 28.5% |
| flat | constant 0.35 | 683.4 | 72.9 | – |
| flat | 2 / 4 / 8 / 16 | 517.8 / 482.6 / 548.0 / **454.8** | 33–54 | 24.2 / 29.4 / 19.8 / **33.4%** |

The maximum is **33.4% on the flat course with 16 sections**. Every run is within 120 ± 0.1 s, and N = 1 reproduces the baseline exactly.

**Hill behaviour (rolling, N = 8).** Throttle by section is [0.88, 0.36, 0.69, 0.12, 0.39, 0, 0, 0].
* **Before the hill:** the profile drives on the second −2% descent and reaches **78.5 km/h at the foot of the climb**. The constant run reaches 66.6 km/h there.
* **On the climb:** it eases to 0.12 on the +4% section and uses 0.39 on the steep +6% section. Speed falls from 47.0 km/h 100 m before the crest to **33.7 km/h at the crest**. The constant run reaches the crest at 31.6 km/h.
* **After the crest:** it coasts to the line.

So it builds speed before the hill and sheds it near the top.

### 3. Finding: the upshift rule can leave the car stuck in a gear

The brief's rule is that an upshift must still gain 0.10 m/s within a 1 s forecast. At some constant throttles this means the car never upshifts. At 0.30 throttle on the flat it stays in 3rd at 4,350 rpm and finishes in 109 s using 908 kJ. At 0.325 it reaches 4th and finishes in 122 s using 646 kJ.

The flat-course constant baseline (0.35) never gets out of 4th. The optimised profile reaches 5th by accelerating harder early on. So part of the flat-course saving is escaping this trap, not coasting. See [energy_vs_time](results/visuals/energy_vs_time.png). These jumps also explain the erratic optimiser results: flat N = 8 found only 19.8%, while N = 4 found 29.4%.

### 4. Where the energy goes ([energy_breakdown](results/visuals/energy_breakdown.png))

| | Driveline losses | Aero | Rolling | Kinetic energy over the line | Total |
| --- | --- | --- | --- | --- | --- |
| rolling, constant | 68 | 156 | 222 | 219 | 664 kJ |
| rolling, N = 8 | 48 | 147 | 220 | 60 | 474 kJ |
| flat, constant | 70 | 161 | 223 | 230 | 683 kJ |
| flat, N = 16 | 46 | 140 | 220 | 49 | 455 kJ |

Rolling resistance is almost the same for every strategy, because it is set mainly by the distance. Aero drops a little. **Most of the saving (about 160–180 kJ of the roughly 190–230 kJ) is kinetic energy not carried over the finish line.** This is because the brief has no finishing-speed requirement and coasting costs zero shaft work. The saving belongs to the whole drive. It is not caused by the hill: the flat course saves more than the rolling one.

## Conventions

* **Integration:** explicit 0.1 s steps with force taken at the start of the step and constant acceleration within it. Motion is forward-only.
* **Finish:** the final step is fractional and ends exactly at the line; the same fraction applies to time and energy.
* **Energy:** `P = T·rpm·2π/60`, `ΔE = P·Δt`. Forecasts do not count. Zero throttle means zero shaft torque.
* **Launch:** clutch slip at 850 rpm in 1st gear for journeys, and at 3000 rpm for the acceleration test.
* **Gear rule:** every 0.1 s the controller runs a 1.0 s forecast for down/hold/up.
  * An upshift needs no dip in speed and +0.10 m/s by the end of the forecast; holding a gear needs only a level forecast.
  * A struggling gear (one that cannot hold speed even at full throttle) is downshifted.
  * Otherwise the lowest-energy candidate wins, holding the gear on a tie.
  * Gears outside 1200–6250 rpm are illegal.
* **Optimiser:**
  * Nested Nelder–Mead. The outer search starts from N + 1 vertices (a flat shape plus a +0.25 perturbation of each section) and restarts while energy improves by more than 50 J.
  * The inner search finds the scale k from starts 0.30 and 0.40.
  * Only finished, in-window runs can win.
  * Settings and termination reasons are in `results/full/runs.json`.
