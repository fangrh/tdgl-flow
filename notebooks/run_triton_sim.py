#%%
"""Submit py-tdgl simulation to Triton HPC and view results with Rust viewer.

Prerequisites:
    kubectl port-forward -n tdgl svc/argo-server 30080:2746 &
    kubectl port-forward -n tdgl svc/minio 30900:9000 &
    cd tdgl-viewer-rust && maturin develop --release
    Deploy triton/ scripts to /scratch/work/fangr1/tdgl-runner/ on Triton
"""

#%%
import sys
sys.path.insert(0, "../src")

from tdgl_sdk import DFlowTritonPipeline
from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.timing import build_timing
from tdgl_viewer_rust.widget import TdglDiscreteViewer

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
    "probe_points": [[-3, 0], [3, 0]],
    "max_edge_length": 0.25,
    "smooth": 100,
}

TIMING_PARAMS = {
    "mode": "simple",
    "je_initial": 0.0,
    "je_final": 25.0,
    "je_step": 0.2,
    "ramp_time": 100.0,
    "stable_time": 200.0,
    "ramp_down": True,
}

SOLVER_OPTIONS = {
    "dt_init": 1e-6,
    "dt_max": 0.1,
    "adaptive": True,
    "save_every": 100,
}

EPSILON_PARAMS = {
    "type": "gaussian",
    "positions": [[0.0, y] for y in [-2.5, -1.25, 0.0, 1.25, 2.5]],
    "widths": [[0.4, 0.4]] * 5,
    "strengths": [0.9] * 5,
}

SBATCH_OPTIONS = {
    "partition": "batch-csl",
    "cpus-per-task": "4",
    "mem": "16G",
    "time": "02:00:00",
}

#%%
# ── Build device ──────────────────────────────────────────────────────────
_mesh_meta, device = build_rectangular_device(
    film_width=DEVICE_PARAMS["film_width"],
    film_height=DEVICE_PARAMS["film_height"],
    elec_width=DEVICE_PARAMS["elec_width"],
    elec_height=DEVICE_PARAMS["elec_height"],
    elec_y_offset=DEVICE_PARAMS["elec_y_offset"],
    probe_points=DEVICE_PARAMS["probe_points"],
    max_edge_length=DEVICE_PARAMS["max_edge_length"],
    smooth=DEVICE_PARAMS["smooth"],
)

timing_data = build_timing(
    je_initial=TIMING_PARAMS["je_initial"],
    je_final=TIMING_PARAMS["je_final"],
    je_step=TIMING_PARAMS["je_step"],
    ramp_time=TIMING_PARAMS["ramp_time"],
    stable_time=TIMING_PARAMS["stable_time"],
    ramp_down=TIMING_PARAMS.get("ramp_down", False),
)

print(f"Device: {len(device.points)} sites")
print(f"Timing: {timing_data['n_steps']} steps, solve_time={timing_data['solve_time']}")

#%%
# ── Submit to Triton ──────────────────────────────────────────────────────
import os
os.environ.setdefault("SSH_KEY_PATH", os.path.expanduser("~/.ssh/id_ed25519"))

pipe = DFlowTritonPipeline(
    argo_url=ARGO_URL,
    minio_endpoint=MINIO_URL,
    sbatch_options=SBATCH_OPTIONS,
    sidecar_interval=5,
)

run_id, wf_name = pipe.submit(
    device=device,
    timing_params=timing_data,
    solver_options=SOLVER_OPTIONS,
    epsilon_params=EPSILON_PARAMS,
)
print(f"Submitted: run_id={run_id}, workflow={wf_name}")
print("Simulation running on Triton — open viewer below to watch in real-time.")

#%%
# ── Open viewer (live refresh built-in) ───────────────────────────────────
import time
import httpx

viewer = TdglDiscreteViewer(
    MINIO_URL,
    fps=10,
    speed=5,
    average_time=0.5,
    show_vt_dot=True,
    refresh_interval=5.0,
    debug=True,
)

# Try to open the submitted run; if data isn't ready yet, the dropdown
# will show all available runs and live-refresh will pick it up once
# the sync pod starts uploading.
print(f"Submitted run: {run_id}")
try:
    viewer.open(run_id=run_id)
    print(f"  {viewer.total_frames()} frames loaded")
except Exception:
    print("  Data not yet available — viewer will show in dropdown once ready")

viewer.display()

#%%
