#%%
"""End-to-end TDGL simulation test with 2x2 live animation (psi, mu, V-vs-t, I-V).

Run cell-by-cell in VS Code Interactive or Jupyter.
After submitting the workflow, immediately display a live viewer that
polls MinIO and auto-updates as new frames arrive — no local download.

Prerequisites:
    pip install hera-workflows boto3 httpx h5py numpy scipy pillow matplotlib tdgl ipywidgets
    Argo Workflows: kubectl port-forward -n tdgl svc/nginx-ingress 30080:80
    MinIO:          kubectl port-forward -n tdgl svc/minio 30900:9000
    h5py ROS3:      pip install --no-binary=h5py --force-reinstall h5py
"""

#%%
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import matplotlib.pyplot as plt
import numpy as np

from tdgl_sdk import SimulationPipeline, verify_run, examine_h5, format_report, create_player_2x2
from tdgl_workflow.epsilon import make_gaussian_epsilon

print("Imports OK")

#%%
# ── Configuration ──────────────────────────────────────────────────────
ARGO_URL = "http://localhost:30080"
MINIO_ENDPOINT = "http://localhost:30900"

DEVICE_PARAMS = {
    "film_width": 6.0,
    "film_height": 4.0,
    "elec_width": 0.2,
    "elec_height": 4.1,
    "elec_y_offset": 0.0,
    "probe_points": [[-1.0, 0.0], [1.0, 0.0]],
    "max_edge_length": 0.25,
    "smooth": 100,
}

TIMING_PARAMS = {
    "je_initial": 0.0,
    "je_final": 20,
    "je_step": 0.2,
    "ramp_time": 100.0,
    "stable_time": 200.0,
    "ramp_down": True,
}

SOLVER_OPTIONS = {
    "dt_init": 1e-4,
    "dt_max": 0.1,
    "save_every": 50,
}

AVERAGE_TIME = 50.0

print("Config ready")
print(f"  Device: {DEVICE_PARAMS['film_width']}x{DEVICE_PARAMS['film_height']}")
print(f"  Timing: Je {TIMING_PARAMS['je_initial']}->{TIMING_PARAMS['je_final']}, step={TIMING_PARAMS['je_step']}")
print(f"  Solver: save_every={SOLVER_OPTIONS['save_every']}")

#%%
# ── Epsilon: Gaussian spot array ────────────────────────────────────────
# Configure a spatially-varying disorder_epsilon from Gaussian light spots.
# Set EPSILON_PARAMS = None to disable (no epsilon).
#
# Each spot: position [x, y], width [sigma_x, sigma_y], strength (peak T suppression)
# Formula: T = sum(strengths[i] * exp(-dx²/(2*sx²) - dy²/(2*sy²)))
#          epsilon = clamp(1 - T, 0, 1)

# ── Example: 3x3 circular spot array ────────────────────────────────────
# Uncomment one of the examples below, or define your own.

# --- 3x3 circular grid ---
_xs = np.linspace(-2.0, 2.0, 3)
_ys = np.linspace(-1.2, 1.2, 3)
_positions = [[float(x), float(y)] for y in _ys for x in _xs]
EPSILON_PARAMS = {
    "type": "gaussian",
    "positions": _positions,
    "widths": [[0.3, 0.3]] * 9,      # circular spots
    "strengths": [0.4] * 9,
}

# --- 2 elliptical spots (original) ---
# EPSILON_PARAMS = {
#     "type": "gaussian",
#     "positions": [[-1.0, 0.0], [1.0, 0.0]],
#     "widths": [[0.5, 0.3], [0.5, 0.3]],
#     "strengths": [0.5, 0.5],
# }

# --- Disable epsilon ---
# EPSILON_PARAMS = None

n_spots = len(EPSILON_PARAMS["positions"]) if EPSILON_PARAMS else 0
print(f"  Epsilon: {n_spots} spots" + ("" if not EPSILON_PARAMS else f" (type={EPSILON_PARAMS['type']})"))

#%%
# ── Preview: T and epsilon distribution ─────────────────────────────────
# Plot the temperature suppression and epsilon before running the simulation.
# Only runs if EPSILON_PARAMS is set.

if EPSILON_PARAMS is not None:
    epsilon_fn = make_gaussian_epsilon(
        positions=EPSILON_PARAMS["positions"],
        widths=EPSILON_PARAMS["widths"],
        strengths=EPSILON_PARAMS["strengths"],
    )

    fw = DEVICE_PARAMS["film_width"]
    fh = DEVICE_PARAMS["film_height"]
    nx, ny = 300, 200
    x = np.linspace(-fw / 2, fw / 2, nx)
    y = np.linspace(-fh / 2, fh / 2, ny)
    X, Y = np.meshgrid(x, y)

    T_map = np.zeros((ny, nx))
    for j in range(ny):
        for i in range(nx):
            T_map[j, i] = 1.0 - epsilon_fn((x[i], y[j]))

    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))

    # T distribution
    ax0 = axes[0]
    im0 = ax0.pcolormesh(X, Y, T_map, shading="auto", cmap="hot")
    ax0.set_title("T (temperature suppression)")
    ax0.set_xlabel("x"); ax0.set_ylabel("y"); ax0.set_aspect("equal")
    fig.colorbar(im0, ax=ax0, label="T")
    for p in EPSILON_PARAMS["positions"]:
        ax0.plot(p[0], p[1], "c+", ms=10, mew=2)

    # Epsilon distribution
    ax1 = axes[1]
    im1 = ax1.pcolormesh(X, Y, 1 - T_map, shading="auto", cmap="viridis", vmin=0, vmax=1)
    ax1.set_title("epsilon = 1 - T")
    ax1.set_xlabel("x"); ax1.set_ylabel("y"); ax1.set_aspect("equal")
    fig.colorbar(im1, ax=ax1, label="epsilon")
    for p in EPSILON_PARAMS["positions"]:
        ax1.plot(p[0], p[1], "r+", ms=10, mew=2)

    # Cross-section at y=0
    ax2 = axes[2]
    mid_y = ny // 2
    ax2.plot(x, T_map[mid_y, :], "r-", linewidth=1.5, label="T")
    ax2.plot(x, 1 - T_map[mid_y, :], "b-", linewidth=1.5, label="epsilon")
    ax2.set_title("Cross-section at y=0")
    ax2.set_xlabel("x"); ax2.legend(); ax2.grid(True, alpha=0.3)
    ax2.set_ylim(-0.05, 1.05)

    plt.tight_layout()
    plt.show()

    print(f"T range:      [{T_map.min():.4f}, {T_map.max():.4f}]")
    print(f"epsilon range: [{(1 - T_map).min():.4f}, {(1 - T_map).max():.4f}]")
else:
    print("Epsilon disabled (EPSILON_PARAMS = None)")

#%%
# ── Step 0: Clear MinIO (optional) ───────────────────────────────────────
# Uncomment to delete all previous simulation data from MinIO.
# pipeline = SimulationPipeline(argo_url=ARGO_URL, minio_endpoint=MINIO_ENDPOINT)
# deleted = pipeline.store.clear_all_runs()
# print(f"Deleted {deleted} objects from MinIO")

#%%
# ── Step 1: Create pipeline and submit ──────────────────────────────────
pipeline = SimulationPipeline(
    argo_url=ARGO_URL,
    minio_endpoint=MINIO_ENDPOINT,
)

run_id, wf_name = pipeline.submit(
    device_params=DEVICE_PARAMS,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
    epsilon_params=EPSILON_PARAMS,
)
print(f"Submitted: run_id={run_id}, workflow={wf_name}")

#%%
# ── Step 2: Watch live animation ────────────────────────────────────────
# This immediately opens a live viewer that polls MinIO for new frames.
# The viewer auto-updates as the simulation produces output.
# Run this cell right after submitting — no need to wait for completion.
#
# The viewer reads HDF5 directly from MinIO via ROS3 (no local download).
# Click "Stop watching" when done, or let it run until the workflow finishes.

live_player = pipeline.watch_live(
    run_id, poll_interval=10,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
    average_time=AVERAGE_TIME,
    debug=True,
)
live_player.display_player()

#%%
# ── Step 3 (optional): Check workflow status ────────────────────────────
# While the live viewer runs above, you can check workflow progress here.
import httpx

resp = httpx.get(
    f"{ARGO_URL}/api/v1/workflows/tdgl/{wf_name}",
    verify=False, timeout=10,
)
wf_status = resp.json().get("status", {})
phase = wf_status.get("phase", "Unknown")
started = wf_status.get("startedAt", "?")
finished = wf_status.get("finishedAt", "-")
print(f"Workflow: {phase}")
print(f"Started:  {started}")
print(f"Finished: {finished}")

# Check live player state
status = live_player.get_status()
print(f"\nLive viewer: watching={status['watching']}, url={status['h5_url']}")
if "player" in status:
    p = status["player"]
    print(f"  Frames: {p['total_frames']}, Current: {p['current_frame']}, Playing: {p['playing']}")

#%%
# ── Step 3b: Debug log ─────────────────────────────────────────────────
# Debug log is written to a local file. Read it anytime during/after simulation.
log = live_player.debug_log
if log:
    print(f"Debug log: {log.path}")
    with open(log.path) as f:
        lines = f.readlines()
    for line in lines[-40:]:
        print(line, end="")
else:
    print("Debug not enabled (pass debug=True to watch_live)")

#%%
# ── Step 4: Poll until complete (blocks) ────────────────────────────────
# Wait for the workflow to finish. The live viewer above keeps updating.
# Skip this cell if you prefer to just watch the live viewer.
phase = pipeline.poll(wf_name, timeout=600)
print(f"\nWorkflow {phase}")
live_player.stop()

#%%
# ── Step 5: Verify results (direct MinIO read) ──────────────────────────
h5_url = pipeline.store.h5_url(run_id)
s3_kwds = {
    "s3_access_key": pipeline.store.s3._request_signer._credentials.access_key,
    "s3_secret_key": pipeline.store.s3._request_signer._credentials.secret_key,
}

report = verify_run(h5_url, **s3_kwds)
print(f"Healthy: {report['healthy']}")
print(f"Summary: {report['summary']}")
print()
print(report["examine_text"])

#%%
# ── Step 6: Static frame preview (direct MinIO read) ────────────────────
from IPython.display import HTML, display
import base64
from tdgl_workflow.timing import build_timing

_timing = build_timing(**TIMING_PARAMS)
player = create_player_2x2(h5_url, timing_steps=_timing.get("steps", []) + _timing.get("ramp_down_steps", []), average_time=AVERAGE_TIME, debug=True, **s3_kwds)
print(f"Player: {player.total} frames")

def show_frame(idx):
    idx = max(0, min(player.total - 1, idx))
    from tdgl_sdk.viewer._render import render_frame_2x2
    png = render_frame_2x2(player.h5_path, player._mesh, player.iv_cache, player.mu_vmax, idx, **s3_kwds)
    b64 = base64.b64encode(png).decode("ascii")
    display(HTML(f'<img src="data:image/png;base64,{b64}" width="760" style="display:block;background:#1e1e1e"/>'))

print("Frame 0:")
show_frame(0)

#%%
print(f"Frame {player.total - 1} (last):")
show_frame(player.total - 1)

#%%
# ── Step 7: Interactive player (post-simulation) ────────────────────────
# Full interactive player for the completed simulation.
# Drag slider or click Play to animate all frames.
player.display_player()

#%%
# ── Step 8: I-V curve ───────────────────────────────────────────────────
# Step-averaged I-V: one point per completed Je step, V averaged over average_time.
# Blue dot marks the current playback position.
iv = player.get_iv_data(step_averaged=True)
print(f"I-V points (Je steps): {iv['n_points']}")
print(f"I range: [{iv['I_range'][0]:.4f}, {iv['I_range'][1]:.4f}]")
print(f"V range: [{iv['V_range'][0]:.4f}, {iv['V_range'][1]:.4f}]")

fig, ax = plt.subplots(1, 1, figsize=(6, 4))
ax.plot(iv["I"], iv["V"], "r-", linewidth=1)
if iv["current_I"] is not None and iv["current_V"] is not None:
    import math
    if not (math.isnan(iv["current_V"]) or math.isnan(iv["current_I"])):
        ax.plot(iv["current_I"], iv["current_V"], "bo", markersize=8, zorder=5)
ax.set_xlabel("I (transport current)")
ax.set_ylabel("V (voltage)")
ax.set_title(f"I-V Curve ({iv['n_points']} valid points)")
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

#%%
# ── Cleanup ─────────────────────────────────────────────────────────────
player.iv_cache.stop()
print("Done.")
