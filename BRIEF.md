# Claude Code brief 2 | Improve the Fiesta optimisation and measure the difference

Build and run a separate Python project in `fiesta-improved/`. The question is whether a better numerical method can find a time-matched driving strategy with fewer whole-journey simulations, and whether spending that saved computation on finer throttle control produces a meaningful energy saving. I also want to know how much the answer depends on the speed at which the car crosses the finish line.

## Establish the reference experiment

If a sibling `fiesta-reconstruction/` exists, copy its simulation code and resolved `results/reference-case/` into this project. Use the sibling as a read-only source. The improved project must run independently once copied. Replay its constant-throttle and best available optimised vectors in reference mode. Compare arrival time, shaft energy, finishing speed and gear-change locations against the saved traces. Resolve discrepancies before interpreting a cross-version difference. Record the input files and versions used.

This brief also works on its own. If starting fresh, use any university files supplied in `original-materials/` first. Otherwise build a local reference from the contract and fallback below. Compare both solvers on that same reference and label the results as a reconstruction.

- **Task.** A Ford Fiesta starts from rest in first and covers a rolling mile (1,609.344 m) in 120.0 ± 0.1 s. The default time step is 0.1 s. The route stays fixed while the number of distance-based throttle sections increases through 1, 2, 4, 8 and 16. Preserve the free finishing speed in the main comparison. Both the time tolerance and the step come from Doug’s later recollection.
- **Simulator.** Calculate engine speed from road speed and gearing, torque from rpm and throttle, and wheel force from torque, gearing, driveline efficiency and rolling radius. Integrate longitudinal motion with grade, drag and rolling resistance for the substitute. Sum actual shaft work as `torque_Nm × rpm × 2π/60 × elapsed_step_s`. Include the launch from rest and apply the same finish-line convention to time and energy. The fallback uses constant acceleration within a step and a fractional step at the finish.
- **Gears.** Forecast holding, upshifting and downshifting at the requested throttle over the same short horizon. Compare legal gears on predicted speed loss and then mechanical energy. Require extra speed reserve before shifting up. Allow the current gear to stay engaged with a smaller reserve, and allow a lower gear when it better supports the requested climb. Hold the gear on ties. A deliberate throttle lift can shed speed near the crest. Only the committed real step counts towards trip time and energy.
- **Historical optimiser.** Start from an approximately suitable flat throttle shape. Outer Nelder–Mead changes the shape over the sections. For each shape, one-dimensional inner Nelder–Mead adjusts one overall multiplier to minimise absolute time error. Use bounded applied throttles in [0, 1]. The outer objective is total shaft energy for completed, time-feasible runs. Include a separately time-matched constant-throttle baseline. Doug reported a best simulated saving of up to 34% in the university project. Treat that as a historical comparison point.

## Improvement 1 | Solve the actual timing equation

For a fixed nonzero shape `q`, the inner problem is to find a scale `k` such that `r(k) = arrival_time(k × q) − 120 s` lies within the allowed window. Implement bounded bisection as the primary alternative to scalar Nelder–Mead.

1. Set the admissible scale interval from the throttle bounds. For the shape-preserving reference, the upper limit is `1 / max(q)`. Reuse the reference’s actual throttle mapping if original material gives a different one. Evaluate a small labelled sequence of scales, including the nominal or previously successful scale. Cache repeated evaluations within the same shape and configuration.
2. Accept an evaluated scale that hits the time tolerance. Otherwise find two completed runs with residuals of opposite signs and bisect that interval. An unfinished run has no finite arrival time. Keep its failure status and sample further within the available bounds. Choose between candidate brackets deterministically, for example by proximity to the nominal scale, and log that choice.
3. A gear change can introduce a jump in arrival time. A narrow bracket, solver success flag or change of sign alone does not establish a feasible run. Check the actual simulated residual. If bisection stalls, search other sampled intervals or subdivide near promising scales within a declared evaluation budget. Handle same-sign endpoints that hide an interior crossing. If the search exhausts its budget, return “no feasible scale found” with the best observed residual.
4. On a flat profile and on two hill profiles, plot arrival time against scale with the target band, gear-change counts and failed runs marked. Compare the inner solvers on **identical shapes** before changing the outer search. Count every simulation used for bracketing, retries and fallback.

## Improvement 2 | Use the coarse solution at the next resolution

Keep outer Nelder–Mead for the main comparison. When doubling the number of equal-length throttle sections, copy each old setting into both children. For recovered nonuniform partitions, use nested breakpoints that preserve the old profile, or document where an exact transfer is impossible. Replay the inherited profile before making improvements. With identical applied throttles it should give the same drive. Keep it as the incumbent and perturb the fine sections independently to seed a full-dimensional simplex.

Run through 16 sections. Try 32 under a declared budget and report the marginal change in energy and runtime. Sixteen and 32 describe throttle resolution. Keep the same underlying terrain in both. Record the best feasible incumbent even when a search stops on budget. This makes it possible to tell whether extra sections bought useful control freedom or only consumed more evaluations.

## Run three controlled comparisons

| Run | Inner time solver | Outer starting profile | What the comparison measures |
| --- | --- | --- | --- |
| A | Scalar Nelder–Mead | Flat at each resolution | Historical reference |
| B | Bounded bisection with the fallback above | Same flat starts and outer simplex settings as A | Effect of replacing the inner search |
| C | Same solver as B | Previous resolution’s best feasible profile | Effect of reusing coarse solutions |

Use the same simulator, route, throttle limits, gear controller, baseline and timing criteria in A/B/C. Give each method a comparable budget of whole-route simulations. Charge C for its coarse solves when reporting time or calls to reach a fine solution. Report both cost to first feasible solution and best feasible energy versus cumulative calls. Profiles that differ only by an overall multiplier can produce identical applied throttles. For any shared simulation cache, key on the actual applied throttle vector and full simulator configuration, and apply the same cache policy across methods. Record wall time, number of feasible shapes, gear shifts and arrival residual as well. On a representative rolling route, repeat the outer search from several documented perturbations to show how much results vary across starts. If the energy difference is small and the drives use opposite ends of the time window, re-time the finalists to a common tighter residual where achievable and report whether the ranking holds.

## Check what the energy saving means

- **Finish speed.** Show the baseline and optimised finishing speeds and `½ m v_finish²` beside shaft work. Then run a second experiment requiring the optimised car to finish within 0.2 m/s of the constant-throttle car’s finishing speed, with the original timing window. This tolerance is a new experimental choice. Seed with the feasible constant profile, enforce both conditions on the full simulation, and retain the baseline if no better feasible profile is found. Report how the percentage saving changes. Keep shaft work as the objective in both experiments.
- **Time-step sensitivity.** Replay the stored baseline and winning throttle profiles with 0.1, 0.05 and 0.025 s physics steps. Hold the controller update period and preview duration fixed in seconds. Report time error, shaft energy and shift locations before any profile is re-timed. If timing drifts outside tolerance, also report a distinctly labelled re-timed comparison at the finer step.
- **Gear hunting and work.** Show a trace of the third/fourth shift decisions on the steep section with the upshift margin on and off. Count rapid reversals using one stated time window. Test the force and power conversions on an ideal locked drivetrain, where shaft power equals tractive wheel force times road speed. A lower rpm alone supplies no mechanical-work saving at the same wheel force and speed.
- **Regression cases.** Exercise a bracketed time target, an unreachable target, an unfinished low-throttle run and a synthetic gear-switch jump that skips the time band. Recheck replay parity, a hand-calculated shaft-work integral and equivalence when a throttle section is split in two. Run the tests and report their outcome.

## Inputs if the university files are missing

Use a labelled substitute that Doug can replace in one configuration file. Keep the route definition, throttle boundaries, drivetrain data and controller settings in machine-readable form.

| Input | Runnable substitute |
| --- | --- |
| Published vehicle | Ford Fiesta 1.25 Duratec 82 PS, five-door, five-speed manual. Kerb mass 1,055 kg including the driver. Gear ratios from first to fifth are 3.583, 1.926, 1.281, 0.951 and 0.756. Final drive is 4.06. Ford gives 114 N·m at 4,200 rpm and 60 kW at 5,800 rpm. |
| Full-load torque curve | Prefer a recovered curve for the same engine. For an offline approximation, linearly interpolate rpm `[850, 1500, 2500, 3500, 4200, 5000, 5800, 6250]` against N·m `[55, 75, 95, 108, 114, 106, 98.8, 85]`. Only the peak-torque point and the torque implied by peak power have manufacturer anchors. The remaining samples are illustrative. |
| Other assumed vehicle inputs | Wheel rolling radius 0.285 m, drag coefficient 0.32, frontal area 2.0 m², rolling coefficient 0.012, driveline efficiency 0.90, air density 1.225 kg/m³, gravity 9.81 m/s². For this substitute, scale full-load torque linearly with throttle and model zero-throttle coasting with zero shaft torque. Allow clutch slip at an assumed 850 rpm for launch in first, then use the locked gear ratio. Use 6,250 rpm as the assumed upper limit. |
| Example route | Split one mile into eight equal road lengths. Use grades in percent of `[0, -2, -2, +4, +6, -4, -2, 0]`, uphill positive. Also provide a flat test course. Use equally spaced throttle boundaries if the original breakpoints are unavailable. |
| Assumed shift predictor | Update every 0.1 s. Forecast 1.0 s at the requested throttle, sampling grade at predicted position. For an upshift, require no predicted dip below entry speed and an end speed at least 0.10 m/s above entry speed. To stay in the current gear under load, a level forecast is sufficient. Keep both the allowable dip and the extra upshift margin configurable. Allow easing off or coasting to reduce speed. |

Ford’s comparison figures for this substitute are 13.3 s to 100 km/h (listed as 0–62 mph) and a top speed of 104 mph. Record the simulated 0–60 mph time separately. The figures, gearing and mass come from the performance, weights and 1.25-litre engine tables in its November 2012 press pack. [Ford Fiesta technical specifications, pp. 10, 11 and 15](https://www.fordpers.be/content/old/pdf_2012-11-28_fiesta_dossier_de_presse_fr.pdf)

## What Doug should receive

A runnable project with exact Windows-friendly quick/full/test commands, the executed A/B/C comparisons, reusable input files, CSVs and a short local results report. Put two figures first. One should overlay terrain, throttle, gear, speed and cumulative shaft energy against distance. The other should show the best feasible energy reached versus cumulative simulation calls for A, B and C. Include a compact results table with energy in kJ, savings against the time-matched constant baseline, time residual, finishing speed, calls and runtime. Conclude with the measured benefit or cost of bisection, what moving beyond 16 sections bought, and how much saving survived the matched-finish-speed check.
