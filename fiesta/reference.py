"""Replay the copied v1 reference case and compare it against the saved v1 traces."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .config import ROOT, Case

REF_DIR = ROOT / "reference"


def load_reference_config() -> dict:
    return json.loads((REF_DIR / "config_used.json").read_text(encoding="utf-8"))


def _saved_trace(name: str) -> dict:
    path = REF_DIR / "timeseries" / f"{name}.csv"
    arr = np.genfromtxt(path, delimiter=",", names=True)
    return {k: arr[k] for k in arr.dtype.names}


def gear_change_locations(d: dict) -> list[tuple[float, int, int]]:
    g = d["gear"]
    idx = np.nonzero(np.diff(g) != 0)[0] + 1
    return [(float(d["x_m"][i]), int(g[i - 1]), int(g[i])) for i in idx]


def replay_reference(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load_reference_config()
    vec = json.loads((REF_DIR / "throttle_vectors.json").read_text(encoding="utf-8"))
    exp = json.loads((REF_DIR / "expected_results.json").read_text(encoding="utf-8"))
    rows = []
    for key, e in exp.items():
        route, which = key.split("/")
        vkey = f"{route}/constant" if which == "constant" else f"{route}/N{int(which.split('_N')[1])}"
        tname = f"{route}_constant" if which == "constant" else f"{route}_best_N{int(which.split('_N')[1]):02d}"
        r = Case(cfg, route).run(vec[vkey], record=True)
        d = r.trace_dict()
        saved = _saved_trace(tname)
        g_new, g_old = gear_change_locations(d), gear_change_locations(saved)
        rows.append({
            "case": key, "throttle_vector": vkey, "n_sections": len(vec[vkey]),
            "time_s": r.time_s, "saved_time_s": e["time_s"], "d_time_s": r.time_s - e["time_s"],
            "energy_kJ": r.energy_J / 1e3, "saved_energy_kJ": e["energy_J"] / 1e3,
            "d_energy_J": r.energy_J - e["energy_J"],
            "finish_speed_m_s": r.finish_speed_m_s, "d_finish_speed_m_s": r.finish_speed_m_s - e["finish_speed_m_s"],
            "shifts": r.shifts, "saved_shifts": e["shifts"],
            "gear_changes_match": len(g_new) == len(g_old) and all(
                a[1:] == b[1:] and abs(a[0] - b[0]) < 1e-4 for a, b in zip(g_new, g_old)),  # CSV keeps 9 s.f.
            "max_abs_speed_diff_m_s": float(np.max(np.abs(d["v_m_s"] - saved["v_m_s"])))
            if len(d["v_m_s"]) == len(saved["v_m_s"]) else float("nan"),
            "gear_change_x_m": [round(a[0], 2) for a in g_new],
        })
    return rows


def parity_ok(rows, energy_tol_J=1e-3, time_tol_s=1e-9) -> bool:
    return all(abs(r["d_energy_J"]) <= energy_tol_J and abs(r["d_time_s"]) <= time_tol_s
               and r["gear_changes_match"] and r["max_abs_speed_diff_m_s"] < 1e-6 for r in rows)
