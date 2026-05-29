#%%
"""Submit cpp-tdgl simulation and view results with the Rust viewer.

Prerequisites:
    kubectl port-forward -n tdgl svc/argo-server 30080:2746 &
    kubectl port-forward -n tdgl svc/minio 30900:9000 &
    cd cpp-tdgl-viewer-rust && maturin develop --release
"""

#%%
import sys
sys.path.insert(0, "../src")

from tdgl_sdk.pipeline import SimulationPipeline
from cpp_tdgl_viewer_rust.widget import CppTdglViewer

MINIO_URL = "http://localhost:30900"
ARGO_URL = "http://localhost:30080"

#%%
# ── Simulation parameters ─────────────────────────────────────────────────
DEVICE_PARAMS = {
    "film_width": 10.0,
    "film_height": 5.0,
    "elec_width": 2.0,
    "elec_height": 1.0,
    "elec_y_offset": 2.0,
    "probe_points": [[0, 2.5], [10, 2.5]],
    "max_edge_length": 0.25,
    "smooth": 100,
}

TIMING_PARAMS = {
    "mode": "simple",
    "je_initial": 0.0,
    "je_final": 2.0,
    "je_step": 0.2,
    "ramp_time": 5.0,
    "stable_time": 10.0,
}

SOLVER_OPTIONS = {
    "dt_init": 1e-6,
    "dt_max": 0.1,
    "adaptive": True,
    "save_every": 100,
}

EPSILON_PARAMS = {
    "type": "gaussian",
    "positions": [[5.0, 2.5]],
    "widths": [[1.0, 1.0]],
    "strengths": [0.5],
}

#%%
# ── Submit cpp-tdgl workflow ──────────────────────────────────────────────
pipe = SimulationPipeline(argo_url=ARGO_URL, minio_endpoint=MINIO_URL)

run_id, wf_name = pipe.submit(
    device_params=DEVICE_PARAMS,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
    epsilon_params=EPSILON_PARAMS,
    workflow_name="cpp-tdgl-sim",
)
print(f"Submitted: run_id={run_id}, workflow={wf_name}")
print("Simulation running — open viewer below to watch in real-time.")

#%%
# ── Open viewer (poll until data appears) ────────────────────────────────
import time
import httpx

viewer = CppTdglViewer(
    MINIO_URL,
    fps=10,
    speed=5,
)

print(f"Run: {run_id}")
while True:
    try:
        viewer.open(run_id=run_id)
        n_steps = viewer._viewer.get_step_count()
        print(f"  {n_steps} steps available")
        break
    except Exception:
        try:
            r = httpx.get(
                f"{ARGO_URL}/api/v1/workflows/tdgl/{wf_name}",
                verify=False, timeout=5,
            )
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
