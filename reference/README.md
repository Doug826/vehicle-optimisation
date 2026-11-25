# Reference case (portable)

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
