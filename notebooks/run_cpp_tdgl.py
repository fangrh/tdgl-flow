#%%
"""Submit cpp-tdgl simulation and view results with Rust viewer.

Prerequisites:
    kubectl port-forward -n tdgl svc/argo-server 30080:2746 &
    kubectl port-forward -n tdgl svc/minio 30900:9000 &
    cd tdgl-viewer-rust && maturin develop --release
    docker build -f services/cpp-tdgl-runner/Dockerfile -t ghcr.io/fangrh/cpp-tdgl-runner:dev .
"""

#%%
import json
import time
import uuid
import sys
sys.path.insert(0, "../src")

import httpx
from tdgl_viewer_rust.widget import TdglViewer

MINIO_URL = "http://localhost:30900"
ARGO_URL = "http://localhost:30080"
NAMESPACE = "tdgl"

#%%
# ── Simulation parameters (same format as py-tdgl) ────────────────────────
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
from datetime import datetime, timezone

run_id = (
    datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    + "-" + uuid.uuid4().hex[:6]
)

wf_body = {
    "serverDryRun": False,
    "workflow": {
        "generateName": f"cpp-tdgl-sim-{run_id[:13]}-",
        "namespace": NAMESPACE,
        "spec": {
            "workflowTemplateRef": {"name": "cpp-tdgl-sim"},
            "arguments": {
                "parameters": [
                    {"name": "run-id", "value": run_id},
                    {"name": "device-params-json", "value": json.dumps(DEVICE_PARAMS)},
                    {"name": "timing-params-json", "value": json.dumps(TIMING_PARAMS)},
                    {"name": "solver-options-json", "value": json.dumps(SOLVER_OPTIONS)},
                    {"name": "epsilon-params-json", "value": json.dumps(EPSILON_PARAMS)},
                ]
            },
        },
    },
}

resp = httpx.post(
    f"{ARGO_URL}/api/v1/workflows/{NAMESPACE}",
    json=wf_body,
    verify=False,
    timeout=30,
)
resp.raise_for_status()
wf_name = resp.json()["metadata"]["name"]
print(f"Submitted: run_id={run_id}, workflow={wf_name}")
print("Simulation running — open viewer below to watch in real-time.")

#%%
# ── Monitor workflow + open viewer when ready ──────────────────────────────
viewer = TdglViewer(
    MINIO_URL,
    fps=10,
    speed=1,
    average_time=0.5,
    show_vt_dot=True,
)

print(f"Workflow: {wf_name}  Run: {run_id}")
while True:
    try:
        r = httpx.get(f"{ARGO_URL}/api/v1/workflows/{NAMESPACE}/{wf_name}", verify=False, timeout=5)
        phase = (r.json().get("status") or {}).get("phase", "Unknown")
    except Exception:
        phase = "Unknown"

    if phase in ("Failed", "Error"):
        print(f"\r  Workflow {phase}                          ")
        break
    elif phase == "Succeeded":
        try:
            viewer.open(run_id=run_id)
            print(f"\r  Done — {viewer.total_frames()} frames                ")
            viewer.display()
        except Exception as e:
            print(f"\r  Completed but viewer error: {e}    ")
        break
    else:
        try:
            viewer.open(run_id=run_id)
            n = viewer.total_frames()
            print(f"\r  [{phase}] {n} frames so far...  ", end="", flush=True)
            viewer.display()
            break
        except Exception:
            print(f"\r  [{phase}] waiting for data...  ", end="", flush=True)
    time.sleep(3)

#%%
