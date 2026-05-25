# Browse Runs Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a notebook that lists all simulation runs from MinIO with device/timing params, lets user pick one by index to display.

**Architecture:** Single notebook file. Uses `TDGLRunStore.list_runs()` to get manifests, prints a formatted table, user sets `SELECTED_INDEX` variable, then calls `create_player()` on the chosen run.

**Tech Stack:** tdgl_sdk (TDGLRunStore, create_player), h5py, ipywidgets

---

### Task 1: Create browse_runs notebook

**Files:**
- Create: `notebooks/browse_runs.py`

- [ ] **Step 1: Create the notebook**

```python
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
print(f"{'#':>3}  {'Run ID':<24} {'Status':<10} {'Sites':>6} {'Frames':>6}  {'Film WxH':<14} {'Je range':<16} {'Created'}")
print("-" * 120)

for i, r in enumerate(runs):
    rid = r.get("run_id", "?")[:24]
    status = r.get("status", "?")
    n_sites = r.get("n_sites", "-")
    n_frames = r.get("n_frames", "-")

    dp = r.get("device_params", {})
    film = f"{dp.get('film_width', '?')}x{dp.get('film_height', '?')}" if dp else "?"

    tp = r.get("timing_params", {})
    if tp:
        je_init = tp.get("je_initial", tp.get("je_start", "?"))
        je_final = tp.get("je_final", tp.get("je_end", "?"))
        je_step = tp.get("je_step", "?")
        je_range = f"{je_init}→{je_final} (Δ{je_step})"
    else:
        je_range = "?"

    created = r.get("created_at", "?")[:19]

    print(f"{i:>3}  {rid:<24} {status:<10} {n_sites:>6} {n_frames:>6}  {film:<14} {je_range:<16} {created}")

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
# For completed runs, use create_player. For running runs, use watch_live.

AVERAGE_TIME = 50.0  # Set to None to use full stable period for I-V averaging

if selected.get("status") == "running":
    from tdgl_sdk.pipeline import SimulationPipeline
    pipeline = SimulationPipeline(minio_endpoint=MINIO_ENDPOINT)
    player = pipeline.watch_live(
        run_id,
        timing_params=selected.get("timing_params"),
        average_time=AVERAGE_TIME,
        debug=True,
    )
    player.display_player()
else:
    from tdgl_sdk.viewer._player import create_player
    from tdgl_workflow.timing import build_timing

    h5_url = store.h5_url(run_id)
    s3_kwds = {
        "s3_access_key": store.s3._request_signer._credentials.access_key,
        "s3_secret_key": store.s3._request_signer._credentials.secret_key,
    }

    tp = selected.get("timing_params") or {}
    timing_steps = None
    if tp.get("n_steps"):
        try:
            timing_data = build_timing(
                je_initial=tp.get("je_initial", 0),
                je_final=tp.get("je_final", tp.get("je_end", 0)),
                je_step=tp.get("je_step", 0.1),
                ramp_time=tp.get("ramp_time", 100),
                stable_time=tp.get("stable_time", 200),
                ramp_down=tp.get("ramp_down", False),
            )
            timing_steps = timing_data.get("steps", [])
        except Exception:
            pass

    player = create_player(
        h5_url,
        timing_steps=timing_steps,
        average_time=AVERAGE_TIME,
        debug=True,
        **s3_kwds,
    )
    print(f"Player: {player.total} frames")
    player.display_player()

#%%
# ── Step 4: I-V curve ───────────────────────────────────────────────────
import math
import matplotlib.pyplot as plt

iv = player.get_iv_data(step_averaged=True)
print(f"I-V points (Je steps): {iv['n_points']}")
print(f"I range: [{iv['I_range'][0]:.4f}, {iv['I_range'][1]:.4f}]")
print(f"V range: [{iv['V_range'][0]:.4f}, {iv['V_range'][1]:.4f}]")

fig, ax = plt.subplots(1, 1, figsize=(6, 4))
ax.plot(iv["I"], iv["V"], "r-", linewidth=1)
if iv["current_I"] is not None and iv["current_V"] is not None:
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
```

- [ ] **Step 2: Commit**

```bash
git add notebooks/browse_runs.py
git commit -m "feat: add browse_runs notebook for listing and displaying past simulations"
```

## Verification

1. Ensure MinIO port-forward is active: `kubectl port-forward -n tdgl svc/minio 30900:9000`
2. Run cells in VS Code Interactive — should see a table of all runs with device/timing params
3. Change `SELECTED_INDEX` to pick a different run, verify it displays correctly
4. Verify I-V curve renders for the selected run
