#%%
"""Test Gaussian epsilon: plot T/epsilon distribution, then run simulation.

Run cell-by-cell in VS Code Interactive or Jupyter.
Prerequisites:
    kubectl port-forward -n tdgl svc/nginx-ingress 30080:80
    kubectl port-forward -n tdgl svc/minio 30900:9000
"""

#%%
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import matplotlib.pyplot as plt
import numpy as np

from tdgl_workflow.epsilon import make_gaussian_epsilon

print("Imports OK")

#%%
# ── Epsilon configuration ──────────────────────────────────────────────
# Device: 6x4 film. Spots placed symmetrically around the center.
FILM_WIDTH = 6.0
FILM_HEIGHT = 4.0

EPSILON_PARAMS = {
    "type": "gaussian",
    "positions": [[-1.0, 0.0], [1.0, 0.0], [0.0, 1.5]],
    "widths": [[0.5, 0.3], [0.5, 0.3], [0.4, 0.4]],
    "strengths": [0.5, 0.5, 0.3],
}

print(f"Spots: {len(EPSILON_PARAMS['positions'])}")
for i, (pos, w, s) in enumerate(zip(EPSILON_PARAMS["positions"],
                                      EPSILON_PARAMS["widths"],
                                      EPSILON_PARAMS["strengths"])):
    print(f"  [{i}] pos={pos}, sigma={w}, strength={s}")

#%%
# ── Plot T distribution and epsilon distribution ───────────────────────
epsilon_fn = make_gaussian_epsilon(
    positions=EPSILON_PARAMS["positions"],
    widths=EPSILON_PARAMS["widths"],
    strengths=EPSILON_PARAMS["strengths"],
)

nx, ny = 200, 140
x = np.linspace(-FILM_WIDTH / 2, FILM_WIDTH / 2, nx)
y = np.linspace(-FILM_HEIGHT / 2, FILM_HEIGHT / 2, ny)
X, Y = np.meshgrid(x, y)

T_map = np.zeros_like(X)
eps_map = np.zeros_like(X)
for j in range(ny):
    for i in range(nx):
        eps = epsilon_fn((X[j, i], Y[j, i]))
        T_map[j, i] = 1.0 - eps
        eps_map[j, i] = eps

fig, axes = plt.subplots(1, 2, figsize=(14, 4.5))

# T distribution (temperature suppression)
ax0 = axes[0]
im0 = ax0.pcolormesh(X, Y, T_map, shading="auto", cmap="hot")
ax0.set_title("T (temperature suppression)")
ax0.set_xlabel("x")
ax0.set_ylabel("y")
ax0.set_aspect("equal")
fig.colorbar(im0, ax=ax0, label="T")

# Mark spot centers
for pos in EPSILON_PARAMS["positions"]:
    ax0.plot(pos[0], pos[1], "c+", markersize=10, markeredgewidth=2)

# Epsilon distribution
ax1 = axes[1]
im1 = ax1.pcolormesh(X, Y, eps_map, shading="auto", cmap="viridis", vmin=0, vmax=1)
ax1.set_title("epsilon = 1 - T (clamped to [0, 1])")
ax1.set_xlabel("x")
ax1.set_ylabel("y")
ax1.set_aspect("equal")
fig.colorbar(im1, ax=ax1, label="epsilon")

for pos in EPSILON_PARAMS["positions"]:
    ax1.plot(pos[0], pos[1], "r+", markersize=10, markeredgewidth=2)

plt.tight_layout()
plt.show()

print(f"T range: [{T_map.min():.4f}, {T_map.max():.4f}]")
print(f"epsilon range: [{eps_map.min():.4f}, {eps_map.max():.4f}]")

#%%
# ── Cross-section plots ────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(12, 4))

# Horizontal cross-section at y=0
mid_y = ny // 2
ax0 = axes[0]
ax0.plot(x, T_map[mid_y, :], "r-", label="T")
ax0.plot(x, eps_map[mid_y, :], "b-", label="epsilon")
ax0.set_title(f"Cross-section at y=0")
ax0.set_xlabel("x")
ax0.legend()
ax0.grid(True, alpha=0.3)

# Vertical cross-section at x=0
mid_x = nx // 2
ax1 = axes[1]
ax1.plot(y, T_map[:, mid_x], "r-", label="T")
ax1.plot(y, eps_map[:, mid_x], "b-", label="epsilon")
ax1.set_title(f"Cross-section at x=0")
ax1.set_xlabel("y")
ax1.legend()
ax1.grid(True, alpha=0.3)

plt.tight_layout()
plt.show()

#%%
# ── Run simulation ─────────────────────────────────────────────────────
from tdgl_sdk import SimulationPipeline, verify_run, examine_h5, format_report, create_player_2x2

ARGO_URL = "http://localhost:30080"
MINIO_ENDPOINT = "http://localhost:30900"

DEVICE_PARAMS = {
    "film_width": FILM_WIDTH,
    "film_height": FILM_HEIGHT,
    "elec_width": 0.2,
    "elec_height": 4.1,
    "elec_y_offset": 0.0,
    "probe_points": [[-1.0, 0.0], [1.0, 0.0]],
    "max_edge_length": 0.25,
    "smooth": 100,
}

TIMING_PARAMS = {
    "je_initial": 0.0,
    "je_final": 5,
    "je_step": 0.5,
    "ramp_time": 100.0,
    "stable_time": 200.0,
    "ramp_down": True,
}

SOLVER_OPTIONS = {
    "dt_init": 1e-4,
    "dt_max": 0.1,
    "save_every": 50,
}

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
print(f"Epsilon: {EPSILON_PARAMS['type']}, {len(EPSILON_PARAMS['positions'])} spots")

#%%
# ── Watch live ─────────────────────────────────────────────────────────
live_player = pipeline.watch_live(
    run_id, poll_interval=10,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
    average_time=50.0,
    debug=True,
)
live_player.display_player()

#%%
# ── Wait for completion ────────────────────────────────────────────────
phase = pipeline.poll(wf_name, timeout=600)
print(f"\nWorkflow {phase}")
live_player.stop()

#%%
# ── Verify: read results from MinIO ────────────────────────────────────
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
# ── View frames with 2x2 player ────────────────────────────────────────
from tdgl_workflow.timing import build_timing
from IPython.display import HTML, display
import base64

_timing = build_timing(**TIMING_PARAMS)
player = create_player_2x2(
    h5_url,
    timing_steps=_timing.get("steps", []) + _timing.get("ramp_down_steps", []),
    average_time=50.0,
    debug=True,
    **s3_kwds,
)
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
# ── Interactive player ─────────────────────────────────────────────────
player.display_player()

#%%
# ── I-V curve ──────────────────────────────────────────────────────────
iv = player.get_iv_data(step_averaged=True)
print(f"I-V points: {iv['n_points']}")
print(f"I range: [{iv['I_range'][0]:.4f}, {iv['I_range'][1]:.4f}]")
print(f"V range: [{iv['V_range'][0]:.4f}, {iv['V_range'][1]:.4f}]")

fig, ax = plt.subplots(1, 1, figsize=(6, 4))
ax.plot(iv["I"], iv["V"], "r-", linewidth=1)
ax.set_xlabel("I (transport current)")
ax.set_ylabel("V (voltage)")
ax.set_title(f"I-V Curve ({iv['n_points']} points)")
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

#%%
# ── Cleanup ────────────────────────────────────────────────────────────
player.iv_cache.stop()
print("Done.")
