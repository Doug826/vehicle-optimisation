# Evidence ledger

## What was available

| Source | Status |
| --- | --- |
| `original-materials/` (scripts, report, data tables, figures) | **Not supplied.** The folder held only the brief. Nothing original to preserve or copy. |
| Doug's latest spoken account (in the brief) | Used for the method: nested Nelder–Mead, 1-mile route, 120 s target, 0.1 s step, 1/2/4/8/16 sections, gear forecasting, shaft-energy objective. |
| Doug's CV | Corroborates Python, nested optimisation, "up to 34% less simulated energy". The 34% was used only to interpret the finished results. |
| Ford Fiesta press pack, 28 Nov 2012 (French dossier, English tables) | Downloaded; spec-table pages 10–16 text-extracted: [`docs/sources/ford-fiesta-press-pack-2012-11-spec-tables-p10-16.txt`](docs/sources/ford-fiesta-press-pack-2012-11-spec-tables-p10-16.txt). Source URL: <https://www.fordpers.be/content/old/pdf_2012-11-28_fiesta_dossier_de_presse_fr.pdf> |

Because there are no originals, there are **no code/report/recollection disagreements to resolve from files**. Every terrain, breakpoint, forecast-rule and optimiser number is a substitute or assumption, as labelled in `config/substitute.json` (`_source` keys).

## Vehicle data checked against Ford's published tables

| Quantity | Brief substitute | Ford press pack | Used | Page |
| --- | --- | --- | --- | --- |
| Variant | 1.25 Duratec 82 PS, 5-dr, 5-sp manual | listed | same | 10, 11, 15 |
| Gear ratios 1–5 | 3.583 / 1.926 / 1.281 / 0.951 / 0.756 | identical (iB5) | same | 15 |
| Final drive | 4.06 | 4.06 for the 1.25 (4.56 for the 1.0 Ti-VCT, 3.61 for the 1.0 EcoBoost) | 4.06 | 13–15 |
| Peak torque | 114 N·m @ 4200 rpm | 114 N·m @ 4200 rpm | same | 15 |
| Peak power | 60 kW @ 5800 rpm | 82 PS (60 kW) @ 5800 rpm | same (98.8 N·m implied) | 15 |
| Kerb mass | 1055 kg incl. driver | 5-dr 1055 kg; "lightest kerbweight assuming driver at 75 kg, full fluids, 90% fuel" | 1055 kg | 11 |
| Drag coefficient | 0.32 | **Cd = 0.328** (3-dr and 5-dr) | **0.328 (corrected)** | 12 |
| Overall width / height | – | 1722 mm (without mirrors) / 1495 mm | used only to sanity-check the frontal area | 12 |
| 0–100 km/h | 13.3 s | 13.3 s (column headed "0-100 kph / 0-62 mph") | reference | 10 |
| Top speed | 104 mph | 168 km/h (104 mph) | reference | 10 |
| Tyre size, rolling radius | 0.285 m | not given | 0.285 m (assumption; matches a loaded 175/65 R14, close to 195/50 R15) | – |
| Frontal area | 2.0 m² | not given | **2.066 m², calibrated** to the 168 km/h top speed (0.80 × width × height) | – |
| Rolling resistance | constant 0.012 | not given | **0.010 × (1 + V/160)**, the standard passenger-tyre fit (≈0.0125 at trip speeds) | – |
| η, torque-curve shape | given | not given | brief values (assumptions) | – |

Ford's 13.3 s and 168 km/h are **manufacturer claims, not an independent road test**. I found no published independent instrumented 0–60 mph test for this exact variant. Spec databases quote "0–60 12.9 s", which looks like a conversion rather than a measurement, so it is not used.

## Reconstruction assumptions not in the brief (all configurable)

* **Rotational inertia** of the engine (0.10 kg·m²) and wheels (4 × 0.6 kg·m²). In first gear this adds about 30% to the effective mass. It is included by default; without it the 0–100 km/h time is about 1.7 s faster than Ford's figure. `include_rotational_inertia=false` gives the brief's literal model.
* **Minimum engaged rpm** of 1200 for gears above first. A gear is illegal below this, and the car is forced to downshift.
* **"Struggling"** means the current gear cannot hold the current speed even at full throttle. Under that condition a downshift that can hold speed takes priority.
* **Upshift margin "disabled"** (hunting demo only) means any legal upshift is admissible on energy grounds, with no speed-reserve check.
* **Performance shift policy:** full throttle, clutch slip at a 3000 rpm launch, upshift at the 6250 rpm limit (the lower gear gives more wheel force up to the limit). The torque interruption per shift is **0.498 s, calibrated** to Ford's 0–100 km/h time of 13.3 s.
* **Calibration:** `python run.py calibrate` fits frontal area and then shift time, in that order. A test checks that the fit reproduces the stored values. 0–60 mph is reported, not fitted, because Ford publishes no 0–60 time. The brief-literal variant (850 rpm launch, instant shifts, no inertia) is also reported.
* **Outer Nelder–Mead restarts:** after convergence, the search restarts from the converged point with a fresh N+1 simplex while energy improves by more than 50 J, within the same evaluation cap.
* **Baseline policy:** the constant throttle is the lowest-energy in-window run found by the same inner Nelder–Mead. No other baseline policy was recovered, so no second one is reproduced.
* **Schedule feedback:** none. No original evidence specifies a link between running behind schedule and downshifting.
