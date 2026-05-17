import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _fig_to_base64(fig: matplotlib.figure.Figure) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def render_mesh_plot(mesh_data: dict) -> str:
    sites = np.array(mesh_data["sites"])
    elements = np.array(mesh_data["elements"])
    probe_indices = mesh_data["probe_indices"]
    probe_points = mesh_data["probe_points"]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.triplot(sites[:, 0], sites[:, 1], elements, linewidth=0.3, color="#6688cc")

    for idx in probe_indices:
        ax.plot(sites[idx, 0], sites[idx, 1], "rs", markersize=6, label="Probe" if idx == probe_indices[0] else "")

    if probe_points:
        for px, py in probe_points:
            ax.axvline(x=px, color="red", linewidth=0.5, linestyle="--", alpha=0.4)

    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title(f"Mesh: {mesh_data['num_sites']} sites, {mesh_data['num_elements']} elements")
    ax.set_aspect("equal")

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend()

    return _fig_to_base64(fig)


def render_timing_plot(timing_data: dict) -> str:
    steps = timing_data["steps"]
    ramp_down_steps = timing_data.get("ramp_down_steps", [])

    fig, ax = plt.subplots(figsize=(10, 3))

    for step in steps:
        ramp = [(step["ramp_start"], step["je_start"]), (step["ramp_end"], step["je_end"])]
        stable = [(step["ramp_end"], step["je_end"]), (step["stable_end"], step["je_end"])]
        ax.plot([p[0] for p in ramp], [p[1] for p in ramp], color="#2563eb", linewidth=1.5)
        ax.plot([p[0] for p in stable], [p[1] for p in stable], color="#2563eb", linewidth=1.5)
        ax.axvspan(step["save_start"], step["save_end"], alpha=0.15, color="green")

    for step in ramp_down_steps:
        ramp = [(step["ramp_start"], step["je_start"]), (step["ramp_end"], step["je_end"])]
        ax.plot([p[0] for p in ramp], [p[1] for p in ramp], color="#dc2626", linewidth=1.5, linestyle="--")

    ax.set_xlabel("Time")
    ax.set_ylabel("Je")
    ax.set_title(f"Current sweep: {timing_data['n_steps']} steps, solve_time={timing_data['solve_time']:.2f}")
    ax.grid(True, alpha=0.3)

    return _fig_to_base64(fig)