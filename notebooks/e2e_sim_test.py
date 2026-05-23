#%%
"""End-to-end TDGL simulation test with live animation.

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

import numpy as np

from tdgl_sdk import SimulationPipeline, verify_run, examine_h5, format_report, create_player

print("Imports OK")

#%%
# ── Configuration ──────────────────────────────────────────────────────
ARGO_URL = "http://localhost:30080"
MINIO_ENDPOINT = "http://localhost:30900"

DEVICE_PARAMS = {
    "film_width": 6.0,
    "film_height": 2.0,
    "elec_width": 0.5,
    "elec_height": 1.0,
    "elec_y_offset": 0.0,
    "probe_points": [[-2.0, 0.0], [2.0, 0.0]],
    "max_edge_length": 0.5,
    "smooth": 100,
}

TIMING_PARAMS = {
    "je_initial": 0.0,
    "je_final": 0.5,
    "je_step": 0.5,
    "ramp_time": 2.0,
    "stable_time": 3.0,
    "save_time": 2.0,
    "ramp_down": False,
}

SOLVER_OPTIONS = {
    "dt_init": 1e-4,
    "dt_max": 0.1,
    "save_every": 500,
}

print("Config ready")
print(f"  Device: {DEVICE_PARAMS['film_width']}x{DEVICE_PARAMS['film_height']}")
print(f"  Timing: Je {TIMING_PARAMS['je_initial']}->{TIMING_PARAMS['je_final']}, step={TIMING_PARAMS['je_step']}")
print(f"  Solver: save_every={SOLVER_OPTIONS['save_every']}")

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

live_player = pipeline.watch_live(run_id, poll_interval=10)
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
    print(f"  Frames: {p['total']}, Current: {p['current']}, Playing: {p['playing']}")

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

player = create_player(h5_url, **s3_kwds)
print(f"Player: {player.total} frames")

def show_frame(idx):
    idx = max(0, min(player.total - 1, idx))
    from tdgl_sdk.viewer._render import render_frame_png
    png = render_frame_png(player.h5_path, player._mesh, player.iv_cache, player.mu_vmax, idx, **s3_kwds)
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
iv = player.get_iv_data()
print(f"I-V points: {iv['n_points']}")
print(f"I range: [{iv['I_range'][0]:.4f}, {iv['I_range'][1]:.4f}]")
print(f"V range: [{iv['V_range'][0]:.4f}, {iv['V_range'][1]:.4f}]")

import matplotlib.pyplot as plt

fig, ax = plt.subplots(1, 1, figsize=(6, 4))
ax.plot(iv["I"], iv["V"], "r-", linewidth=1)
ax.set_xlabel("I (transport current)")
ax.set_ylabel("V (voltage)")
ax.set_title(f"I-V Curve ({iv['n_points']} points)")
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

#%%
# ── Cleanup ─────────────────────────────────────────────────────────────
player.iv_cache.stop()
print("Done.")
