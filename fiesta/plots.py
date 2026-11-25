"""Figures. Colours: constant throttle = grey-blue, optimised profile = orange."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

C_BASE = "#4C6A92"
C_PROF = "#D9822B"
C_TERR = "#7A8B6F"


def elevation(case, x):
    edges = np.arange(len(case.grades) + 1) * case.seg_len
    rise = case.seg_len * np.sin(np.arctan(case.grades / 100))
    z_edges = np.concatenate([[0], np.cumsum(rise)])
    i = np.clip((x // case.seg_len).astype(int), 0, len(case.grades) - 1)
    return z_edges[i] + (x - edges[i]) * np.sin(np.arctan(case.grades[i] / 100))


def _marks(ax, marks):
    if not marks:
        return
    a0, a1, crest = marks
    ax.axvspan(a0, a1, color="#E8D5B5", alpha=0.35, lw=0)
    ax.axvline(crest, color="#8C2D19", lw=0.9, ls="--")


def distance_plot(case, base, prof, path, title, marks=None, labels=("constant throttle", "optimised profile")):
    fig, axs = plt.subplots(5, 1, figsize=(10, 11), sharex=True,
                            gridspec_kw={"height_ratios": [1, 1, 0.8, 1.2, 1.2]})
    xs = np.linspace(0, case.length, 800)
    axs[0].fill_between(xs, elevation(case, xs), elevation(case, xs).min() - 2, color=C_TERR, alpha=0.5)
    axs[0].plot(xs, elevation(case, xs), color=C_TERR)
    for k, g in enumerate(case.grades):
        axs[0].text((k + 0.5) * case.seg_len, elevation(case, np.array([(k + 0.5) * case.seg_len]))[0] + 0.6,
                    f"{g:+.0f}%", ha="center", fontsize=8, color="#444")
    axs[0].set_ylabel("elevation (m)")
    for d, c, lab in ((base, C_BASE, labels[0]), (prof, C_PROF, labels[1])):
        x = d["x_m"]
        axs[1].step(x[:-1], d["throttle"][:-1], where="post", color=c, label=lab)
        axs[2].step(x, d["gear"], where="post", color=c)
        axs[3].plot(x, d["v_m_s"] * 3.6, color=c)
        axs[4].plot(x, d["energy_J"] / 1e3, color=c)
    axs[1].set_ylabel("throttle")
    axs[1].set_ylim(-0.05, 1.05)
    axs[1].legend(loc="upper right", frameon=False)
    axs[2].set_ylabel("gear")
    axs[2].set_yticks([1, 2, 3, 4, 5])
    axs[3].set_ylabel("speed (km/h)")
    axs[4].set_ylabel("shaft energy (kJ)")
    axs[4].set_xlabel("distance (m)")
    for ax in axs:
        _marks(ax, marks)
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    if marks:
        axs[0].text(marks[2], axs[0].get_ylim()[1], " crest", color="#8C2D19", va="top", fontsize=8)
        axs[0].text((marks[0] + marks[1]) / 2, axs[0].get_ylim()[0], "steep approach", ha="center",
                    va="bottom", fontsize=8, color="#7a5a20")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def time_plot(base, prof, path, title, target):
    fig, axs = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for d, c, lab in ((base, C_BASE, "constant throttle"), (prof, C_PROF, "optimised profile")):
        axs[0].plot(d["t_s"], d["v_m_s"] * 3.6, color=c, label=lab)
        axs[1].plot(d["t_s"], d["energy_J"] / 1e3, color=c)
    for ax in axs:
        ax.axvline(target, color="#888", ls=":", lw=1)
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    axs[0].set_ylabel("speed (km/h)")
    axs[0].legend(frameon=False)
    axs[1].set_ylabel("shaft energy (kJ)")
    axs[1].set_xlabel("time (s)")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def savings_plot(rows, path):
    fig, ax = plt.subplots(figsize=(7, 4))
    routes = sorted({r["route"] for r in rows})
    width = 0.8 / len(routes)
    ns = sorted({r["n_sections"] for r in rows})
    for j, rt in enumerate(routes):
        vals = [next((r["saving_percent"] for r in rows if r["route"] == rt and r["n_sections"] == n), np.nan) for n in ns]
        xs = np.arange(len(ns)) + (j - (len(routes) - 1) / 2) * width
        ax.bar(xs, vals, width, label=rt, color=[C_PROF, C_BASE][j % 2])
        for xx, vv in zip(xs, vals):
            if np.isfinite(vv):
                ax.text(xx, vv + 0.5, f"{vv:.1f}", ha="center", fontsize=8)
    ax.set_xticks(np.arange(len(ns)), [str(n) for n in ns])
    ax.set_xlabel("throttle sections")
    ax.set_ylabel("saving vs constant throttle (%)")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def accel_plot(runs, ref, path):
    fig, axs = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    colors = [C_BASE, C_PROF, "#5B8C5A"]
    for (name, a), c in zip(runs.items(), colors):
        d = a["trace"]
        axs[0].plot(d["t_s"], d["v_m_s"] * 3.6, color=c,
                    label=f"{name}: 0-100 km/h {a['zero_to_100_kmh_s']:.2f} s, 0-60 mph {a['zero_to_60_mph_s']:.2f} s")
        axs[1].step(d["t_s"], d["gear"], where="post", color=c)
    axs[0].axhline(100, color="#888", ls=":", lw=1)
    axs[0].axhline(60 * 1.609344, color="#bbb", ls=":", lw=1)
    axs[0].axvline(ref["zero_to_100_kmh_s"], color="#8C2D19", ls="--", lw=1)
    axs[0].text(ref["zero_to_100_kmh_s"], 5, " Ford 0-100 km/h 13.3 s", color="#8C2D19", fontsize=8)
    axs[0].set_xlim(0, 25)
    axs[0].set_ylim(0, 160)
    axs[0].set_ylabel("speed (km/h)")
    axs[0].legend(frameon=False, fontsize=8, loc="lower right")
    axs[1].set_ylabel("gear")
    axs[1].set_xlabel("time (s)")
    for ax in axs:
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Flat-road full-throttle acceleration")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def hunting_plot(case, on, off, path, throttle):
    fig, axs = plt.subplots(3, 1, figsize=(10, 7), sharex=True,
                            gridspec_kw={"height_ratios": [0.7, 1, 1]})
    xs = np.linspace(0, case.length, 600)
    axs[0].plot(xs, elevation(case, xs), color=C_TERR)
    axs[0].set_ylabel("elevation (m)")
    for d, c, lab, lw in ((off, C_PROF, "upshift margin disabled", 0.8), (on, C_BASE, "upshift margin enabled", 2.2)):
        axs[1].step(d["x_m"], d["gear"], where="post", color=c, label=lab, lw=lw)
        axs[2].plot(d["x_m"], d["v_m_s"] * 3.6, color=c, lw=lw)
    axs[1].set_ylabel("gear")
    axs[1].legend(frameon=False)
    axs[2].set_ylabel("speed (km/h)")
    axs[2].set_xlabel("distance (m)")
    for ax in axs:
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"Steep hill (+7%), constant throttle {throttle:.2f}: 3rd/4th gear hunting")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
