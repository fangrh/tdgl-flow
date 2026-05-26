#%%
"""Submit py-tdgl simulation and view results with Rust viewer.

Prerequisites:
    kubectl port-forward -n tdgl svc/argo-server 30080:2746 &
    kubectl port-forward -n tdgl svc/minio 30900:9000 &
    cd tdgl-viewer-rust && maturin develop --release
"""

#%%
import json
import sys
sys.path.insert(0, "../src")

from tdgl_sdk.pipeline import SimulationPipeline
from tdgl_viewer_rust.widget import TdglViewer

MINIO_URL = "http://localhost:30900"
ARGO_URL = "http://localhost:30080"

#%%
# ── Simulation parameters ─────────────────────────────────────────────────
DEVICE_PARAMS = {
    "film_width": 10.0,
    "film_height": 5.0,
    "elec_width": 0.2,
    "elec_height": 5.1,
    "elec_y_offset": 0.0,
    "probe_points": [[-1, 0], [1, 0]],
    "max_edge_length": 0.25,
    "smooth": 100,
}

TIMING_PARAMS = {
    "mode": "simple",
    "je_initial": 0.0,
    "je_final": 20.0,
    "je_step": 0.2,
    "ramp_time": 100.0,
    "stable_time": 200.0,
    "ramp_down": True
}

SOLVER_OPTIONS = {
    "dt_init": 1e-6,
    "dt_max": 0.1,
    "adaptive": True,
    "save_every": 100,
}

# 5x3 array of Gaussian spots across the film (centered at origin)
_eps_positions = [[x, y]
                  for x in [-4.0, -2.0, 0.0, 2.0, 4.0]
                  for y in [-1.25, 0.0, 1.25]]
EPSILON_PARAMS = {
    "type": "gaussian",
    "positions": _eps_positions,
    "widths": [[0.4, 0.4]] * len(_eps_positions),
    "strengths": [0.9] * len(_eps_positions),
}

#%%
# ── Plot device + epsilon distribution ────────────────────────────────────
import numpy as np
import matplotlib.pyplot as plt

fw = DEVICE_PARAMS["film_width"]
fh = DEVICE_PARAMS["film_height"]
ew = DEVICE_PARAMS["elec_width"]
eh = DEVICE_PARAMS["elec_height"]
ey = DEVICE_PARAMS["elec_y_offset"]

fig, ax = plt.subplots(1, 1, figsize=(8, 4))

# Film outline (box(fw,fh) centers at origin)
x0, y0 = -fw / 2, -fh / 2
ax.plot([x0, x0+fw, x0+fw, x0, x0], [y0, y0, y0+fh, y0+fh, y0], "k-", linewidth=1.5)

# Electrodes (source/drain at film edges)
sx = -fw / 2 - ew / 2
dx = fw / 2 - ew / 2
ey_lo, ey_hi = ey - eh / 2, ey + eh / 2
ax.fill_betweenx([ey_lo, ey_hi], sx, sx + ew, color="gold", alpha=0.6, label="source")
ax.fill_betweenx([ey_lo, ey_hi], dx, dx + ew, color="gold", alpha=0.6, label="drain")

# Probe points
for px, py in DEVICE_PARAMS["probe_points"]:
    ax.plot(px, py, "rx", markersize=10, markeredgewidth=2)

# Epsilon heatmap
if EPSILON_PARAMS.get("type") == "gaussian":
    gx = np.linspace(x0, x0 + fw, 400)
    gy = np.linspace(y0, y0 + fh, 200)
    GX, GY = np.meshgrid(gx, gy)
    eps_map = np.zeros_like(GX)
    for (px, py), (sx, sy), s in zip(
        EPSILON_PARAMS["positions"],
        EPSILON_PARAMS["widths"],
        EPSILON_PARAMS["strengths"],
    ):
        eps_map += s * np.exp(-((GX - px)**2 / (2 * sx**2) + (GY - py)**2 / (2 * sy**2)))
    im = ax.pcolormesh(gx, gy, eps_map, cmap="hot", alpha=0.7, shading="auto")
    plt.colorbar(im, ax=ax, label="epsilon (Tc suppression)")
    for px, py in EPSILON_PARAMS["positions"]:
        ax.plot(px, py, "w+", markersize=5, markeredgewidth=0.8)

ax.set_xlabel("x (um)")
ax.set_ylabel("y (um)")
ax.set_title(f"Device {fw}x{fh} + {len(EPSILON_PARAMS['positions'])} epsilon spots")
ax.set_aspect("equal")
ax.legend(loc="lower right", fontsize=8)
plt.tight_layout()
plt.show()

#%%
# ── Plot timing sequence ──────────────────────────────────────────────────
tp = TIMING_PARAMS
je_i, je_f, je_s = tp["je_initial"], tp["je_final"], tp["je_step"]
ramp_t, stable_t = tp["ramp_time"], tp["stable_time"]
ramp_down = tp.get("ramp_down", False)
n_up = int(round((je_f - je_i) / je_s))
period = ramp_t + stable_t

fig, ax = plt.subplots(figsize=(10, 3))

# Up ramp
up_starts = [je_i + i * je_s for i in range(n_up)]
for i, js in enumerate(up_starts):
    je_end = js + je_s
    t0 = i * period
    ax.plot([t0, t0 + ramp_t], [js, je_end], "C0-", linewidth=1.5)
    ax.plot([t0 + ramp_t, t0 + period], [je_end, je_end], "C0-", linewidth=1.5)

# Down ramp
if ramp_down:
    down_offset = n_up * period
    down_starts = [je_f - i * je_s for i in range(n_up)]
    for i, js in enumerate(down_starts):
        je_end = js - je_s
        t0 = down_offset + i * period
        ax.plot([t0, t0 + ramp_t], [js, je_end], "C1-", linewidth=1.5)
        ax.plot([t0 + ramp_t, t0 + period], [je_end, je_end], "C1-", linewidth=1.5)

total_t = n_up * period * (2 if ramp_down else 1)
n_total = n_up * (2 if ramp_down else 1)
ax.set_xlabel("Time (dimensionless)")
ax.set_ylabel("Je")
title = f"Timing: {n_total} steps, Je {je_i}→{je_f}"
if ramp_down:
    title += f"→{je_i}"
title += f", ramp={ramp_t}, stable={stable_t}"
ax.set_title(title)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

#%%
# ── Submit py-tdgl workflow ───────────────────────────────────────────────
pipe = SimulationPipeline(argo_url=ARGO_URL, minio_endpoint=MINIO_URL)

run_id, wf_name = pipe.submit(
    device_params=DEVICE_PARAMS,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
    epsilon_params=EPSILON_PARAMS,
)
print(f"Submitted: run_id={run_id}, workflow={wf_name}")
print("Simulation running — open viewer below to watch in real-time.")

#%%
# ── Open viewer (live refresh built-in) ───────────────────────────────────
import time, httpx

viewer = TdglViewer(
    MINIO_URL,
    fps=10,
    speed=5,
    average_time=0.5,
    show_vt_dot=True,
    refresh_interval=5.0,
    debug=True,
)

print(f"Run: {run_id}")
# Wait for data to appear in MinIO
while True:
    try:
        viewer.open(run_id=run_id)
        print(f"  {viewer.total_frames()} frames, solve_time={viewer._rust.solve_time():.1f}")
        break
    except Exception:
        try:
            r = httpx.get(f"{ARGO_URL}/api/v1/workflows/tdgl/{wf_name}", verify=False, timeout=5)
            phase = (r.json().get("status") or {}).get("phase", "Unknown")
            if phase in ("Failed", "Error"):
                print(f"  Workflow {phase}")
                raise SystemExit(1)
            print(f"\r  [{phase}] waiting for data...", end="", flush=True)
        except SystemExit:
            raise
        except Exception:
            print(f"\r  waiting for data...", end="", flush=True)
    time.sleep(3)

viewer.display()

#%%
