# Claude Code brief 1 | Reconstruct the Fiesta energy-optimisation project

Build and run Doug’s final-year physics project, *Vehicle Energy Optimisation over Undulating Terrain*, in a new `fiesta-reconstruction/` directory. Use Python. I want to see how the two strategies take the same hill, see where their energy totals diverge, and inspect the numbers behind the plots.

## Decide what counts as historical evidence

First inspect any original scripts, report, data tables or figures supplied in `original-materials/` beside this brief. Use them to recover the Fiesta variant, torque curve, terrain, control boundaries, timing, optimiser and gear rules. Record any disagreement between the code, report and recollection, with the file or figure that resolves it. Preserve supplied originals and work on a copy.

The method below follows Doug’s latest spoken account. His CV corroborates Python, nested optimisation and **up to 34% less simulated energy** than constant throttle. The exact terrain, vehicle variant, forecast rule and optimiser settings have not been established from original files. Start with the supplied fallback when evidence runs out. In the results, identify recovered inputs, recollections and modelling assumptions. Use the 34% only when interpreting the completed experiment. Select parameters on physical grounds before running the energy comparison.

## Reconstruct the experiment

| Quantity | Working specification |
| --- | --- |
| Route and start | Travel 1 mile (1,609.344 m) over undulating terrain, starting from rest in first gear. Record finishing speed. The recollection specifies arrival at the finish line and gives no stopping requirement. |
| Journey time | Aim for 120.0 s. Accept a completed run when its absolute error is at most 0.1 s. An earlier recollection used ±0.5 s. Make this a configurable tolerance independent of the simulation step. |
| Simulation and controls | Advance in 0.1 s steps. Read gradient and throttle by distance travelled. Run the same terrain with 1, 2, 4, 8 and 16 throttle sections. Sixteen was the finest throttle resolution attempted. |
| Objective | Minimise mechanical work delivered at the engine shaft over the completed trip, in joules. Gear selection runs inside every forward simulation. Nelder–Mead chooses throttle parameters. |
| Comparison | On each terrain, find a constant throttle that also meets the time target. Use identical vehicle, initial state, gear policy, energy accounting and finish condition for the controlled comparison. If recovered work used another baseline policy, reproduce it too and identify it. |

### Forward simulation and gear selection

1. Model longitudinal motion. When the clutch is engaged, wheel angular speed is vehicle speed divided by rolling radius. Multiply by the selected gear ratio and final drive for engine angular speed. Obtain delivered torque from engine rpm and requested throttle. Convert that torque to wheel force using gearing, efficiency and wheel radius. Include grade, aerodynamic drag and the resistance terms supported by the inputs. If using the substitute, use rolling resistance as well. Convert percent grade to angle with `atan(grade_percent / 100)`.
2. At each controller update, forecast holding the gear and selecting either legal adjacent gear. Start each forecast from the same state and compare speed traces and shaft work over the same short horizon. On a climb, reject an upshift that would lose too much speed. Require extra speed reserve to enter fourth from third. Once in fourth, a smaller reserve suffices to hold it. Keep projected engine rpm, wheel force and the signed speed change for each forecast so a shift decision can be traced. Among gears meeting demand, prefer the lower forecast energy, holding the current gear on ties. If the current gear struggles under load, a downshift can take priority when it restores the required tractive effort. If all candidates struggle, choose the legal gear that best limits speed loss and continue the simulation. Handle launch and rpm limits explicitly.
3. Apply only the chosen gear to the next real step. Integrate actual shaft power as `P_W = torque_Nm * rpm * 2π / 60` and `ΔE_J = P_W * Δt_s`. Forecasts are disposable copies of state and contribute zero to the trip meter. A throttle reduction near the crest can let speed fall. The shift check governs a proposed gear change. The 120 s constraint governs the whole journey. Doug recalls a possible link between being behind schedule and downshifting. Add an explicit schedule-feedback rule only if original evidence specifies one.
4. Recover the integration and finish-line conventions if possible. The reconstruction default uses start-of-step force, constant acceleration within the step, forward-only motion, and the fractional final step that reaches exactly one mile. Apply that same fraction to time and energy. Bound simulations of stalled or unfinished trips and record their outcome.

### Nested Nelder–Mead

For each section count, start with a flat, approximately suitable throttle shape. Seed the outer simplex with distinct perturbations of individual sections so that it can discover shapes such as extra throttle before a climb.

```text
outer Nelder–Mead proposes N relative throttle settings q
    inner Nelder–Mead varies one positive scalar k
        simulate the entire route with section throttles k × q
        score that scale by its absolute arrival-time error
    return the best time-matched run found for this shape
score a feasible shape by its total shaft energy
retain the lowest-energy feasible run seen during the outer search
```

Use both Nelder–Mead loops in this version. Use N + 1 distinct outer simplex vertices for N sections and two starting scale values in the inner search. Keep each applied throttle between zero and one. In the fallback, bound the admissible scale so it preserves the proposed shape. An all-zero profile from rest cannot complete the journey. Give the optimiser a defined way to back out of infeasible proposals and ensure that only completed, in-window runs qualify as winners. Record starting simplexes, stopping tolerances, evaluation caps and termination reasons. For one section the shape is constant, which gives a direct check against the baseline.

## What to run and show

- Implement a quick end-to-end example and the full 1/2/4/8/16 sweep for each recovered terrain. With the substitute inputs, use the rolling course for that sweep. Run both, with declared finite evaluation budgets. Save the best feasible run reached at each resolution and tag budget-limited runs. Rerun each saved throttle vector through the simulator to obtain the reported trace.
- Make one aligned distance plot showing terrain, throttle, gear, speed and cumulative shaft energy for constant throttle and the best feasible profile. Also plot speed and cumulative energy against time. Mark the steep approach and crest. Report whether the simulated car builds speed before the hill and sheds it near the top. Let the traces answer this.
- For each terrain and section count, report time, shaft energy in kJ, finishing speed, shifts, simulation calls, runtime and percentage saving `100 × (E_constant − E_profile) / E_constant`. Label the terrain and comparison that produced the maximum. Attribute the saving to the whole drive unless a separate experiment isolates a component.
- Check flat-road full-throttle acceleration and steady top speed with a documented performance shift policy. Doug recalls the historical model matching 0–60 mph within about 0.1 s and top speed within about 0.5 mph. Report the errors you actually find against comparable reference measurements. Show a steep-hill trace with the upshift margin enabled and disabled to illustrate the third/fourth gear-hunting issue.
- Test a known energy integral (100 N·m at 200 rad/s for 2 s gives 40 kJ), a standing start, timing-window edges, energy charged on the partial final step, and replay of the same constant throttle at different section counts. The latter should reproduce the same drive on the same terrain.

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

## Handoff

Include a README with Windows-friendly setup, exact quick/full/test commands and a short results report linking the generated plots. Save a portable `results/reference-case/` containing resolved vehicle, route, dynamics and controller settings, actual throttle vectors, representative time-series CSVs, summary table, solver settings and software versions. The second project should be able to replay this case. End by telling Doug what matched his account, where the reconstruction differed, and which missing original input would most help explain the difference.
