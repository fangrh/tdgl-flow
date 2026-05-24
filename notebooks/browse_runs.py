#%%
"""Browse completed TDGL simulation runs and display one.

Run cell-by-cell in VS Code Interactive or Jupyter.

Prerequisites:
    pip install hera-workflows boto3 httpx h5py numpy scipy pillow matplotlib tdgl ipywidgets
    MinIO: kubectl port-forward -n tdgl svc/minio 30900:9000
"""

#%%
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tdgl_sdk.client import TDGLRunStore

MINIO_ENDPOINT = "http://localhost:30900"

print("Imports OK")

#%%
# ── Step 1: List all runs ───────────────────────────────────────────────
store = TDGLRunStore(endpoint_url=MINIO_ENDPOINT)
runs = store.list_runs()

print(f"Found {len(runs)} runs\n")
print(f"{'#':>3}  {'Run ID':<24} {'Status':<10} {'Sites':>6} {'Frames':>6}  {'Film WxH':<14} {'Mode':<6} {'Steps':>5} {'SolveTime':>9}  {'Created'}")
print("-" * 130)

for i, r in enumerate(runs):
    rid = r.get("run_id", "?")[:24]
    status = r.get("status", "?")
    n_sites = r.get("n_sites", "-")
    n_frames = r.get("n_frames", "-")

    dp = r.get("device_params", {})
    film = f"{dp.get('film_width', '?')}x{dp.get('film_height', '?')}" if dp else "?"

    tp = r.get("timing_params", {})
    mode = tp.get("mode", "?") if tp else "?"
    n_steps = tp.get("n_steps", "-") if tp else "-"
    solve_time = f"{tp.get('solve_time', '?'):.0f}s" if tp and tp.get("solve_time") else "?"

    created = r.get("created_at", "?")[:19]

    print(f"{i:>3}  {rid:<24} {status:<10} {n_sites:>6} {n_frames:>6}  {film:<14} {mode:<6} {n_steps:>5} {solve_time:>9}  {created}")

#%%
# ── Step 2: Select a run ───────────────────────────────────────────────
# Change this index to select a run from the list above.
SELECTED_INDEX = 0

selected = runs[SELECTED_INDEX]
run_id = selected["run_id"]
print(f"Selected: {run_id}")
print(f"  Status: {selected.get('status')}")
print(f"  Device: {selected.get('device_params')}")
print(f"  Timing: {selected.get('timing_params')}")
print(f"  Solver: {selected.get('solver_options')}")

#%%
# ── Step 3: Display player ──────────────────────────────────────────────
# Reads HDF5 directly from MinIO via ROS3 — no local download.
from tdgl_sdk.viewer._player import create_player

h5_url = store.h5_url(run_id)
s3_kwds = {
    "s3_access_key": store.s3._request_signer._credentials.access_key,
    "s3_secret_key": store.s3._request_signer._credentials.secret_key,
}

player = create_player(h5_url, debug=True, **s3_kwds)
print(f"Player: {player.total} frames")
player.display_player()

#%%
# ── Step 4: I-V curve ───────────────────────────────────────────────────
import math

import matplotlib.pyplot as plt

iv = player.get_iv_data()
print(f"I-V points: {iv['n_points']}")
print(f"I range: [{iv['I_range'][0]:.4f}, {iv['I_range'][1]:.4f}]")
print(f"V range: [{iv['V_range'][0]:.4f}, {iv['V_range'][1]:.4f}]")

fig, ax = plt.subplots(1, 1, figsize=(6, 4))
ax.plot(iv["I"], iv["V"], "r-", linewidth=1)
if iv["current_I"] is not None and iv["current_V"] is not None:
    if not (math.isnan(iv["current_V"]) or math.isnan(iv["current_I"])):
        ax.plot(iv["current_I"], iv["current_V"], "bo", markersize=8, zorder=5)
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
