"""End-to-end TDGL simulation test via Argo Workflows.

Submits a py-tdgl-sim workflow, polls until completion,
downloads the HDF5 result from MinIO, and shows an animation preview.

Usage:
    python notebooks/run_tdgl_sim.py

Prerequisites:
    pip install hera-workflows boto3 httpx h5py numpy scipy pillow matplotlib tdgl
    kubectl port-forward -n argo svc/argo-workflows-server 2746:2746
    kubectl port-forward -n tdgl svc/minio 30900:9000
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx
from hera.workflows import Workflow, WorkflowsService, Parameter
from hera.workflows.models import WorkflowTemplateRef as WTR

from tdgl_sdk import TDGLRunStore

# ── Configuration ──────────────────────────────────────────────────────
GATEWAY = "http://localhost:2746"
MINIO_ENDPOINT = "http://localhost:30900"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin123"
MINIO_BUCKET = "tdgl-results"
NAMESPACE = "tdgl"

# Fast test parameters — small device, short timing
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


def create_argo_service():
    return WorkflowsService(host=GATEWAY, verify_ssl=False, namespace=NAMESPACE)


def create_store():
    return TDGLRunStore(
        endpoint_url=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        bucket=MINIO_BUCKET,
    )


def submit_workflow(argo_svc):
    """Submit the py-tdgl-sim workflow and return (run_id, wf_name)."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    print(f"Run ID: {run_id}")

    wf = Workflow(
        generate_name=f"py-tdgl-sim-{run_id[:13]}-",
        namespace=NAMESPACE,
        workflow_template_ref=WTR(name="py-tdgl-sim"),
        arguments=[
            Parameter(name="run-id", value=run_id),
            Parameter(name="device-params-json", value=json.dumps(DEVICE_PARAMS)),
            Parameter(name="timing-params-json", value=json.dumps(TIMING_PARAMS)),
            Parameter(name="solver-options-json", value=json.dumps(SOLVER_OPTIONS)),
        ],
        workflows_service=argo_svc,
    )

    created = wf.create()
    wf_name = created.metadata.name
    print(f"Submitted: {wf_name}")
    return run_id, wf_name


def poll_workflow(argo_svc, wf_name, timeout=600):
    """Poll the workflow until it completes. Returns final phase."""
    hint_map = {
        "Submitted": "Scheduling...",
        "Pending": "Pulling image...",
        "Running": "Running...",
    }
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            raise TimeoutError(f"Workflow {wf_name} did not complete within {timeout}s")

        url = f"{argo_svc.host}/api/v1/workflows/{NAMESPACE}/{wf_name}"
        resp = httpx.get(url, verify=False, timeout=10)
        resp.raise_for_status()
        phase = (resp.json().get("status") or {}).get("phase", "Unknown")

        if phase == "Succeeded":
            print(f"  {wf_name} succeeded in {elapsed:.0f}s")
            return phase
        elif phase in {"Failed", "Error"}:
            try:
                logs_resp = httpx.get(
                    f"{argo_svc.host}/api/v1/workflows/{NAMESPACE}/{wf_name}/log"
                    "?logOptions.container=main&logOptions.tailLines=30",
                    verify=False, timeout=10,
                )
                print(f"  Logs:\n{logs_resp.text[:3000]}")
            except Exception:
                pass
            raise RuntimeError(f"Workflow {wf_name} {phase}")

        hint = hint_map.get(phase, "Processing...")
        print(f"  [{phase}] {hint} ({elapsed:.0f}s)")
        time.sleep(5)


def check_manifest(store, run_id):
    """Check the manifest in MinIO and return it."""
    manifest = store.get_run(run_id)
    if manifest is None:
        raise AssertionError(f"No manifest found for run {run_id}")
    print(f"Manifest status: {manifest.get('status')}")
    print(f"Frames: {manifest.get('n_frames', '?')}")
    print(f"Sites: {manifest.get('n_sites', '?')}")
    return manifest


def download_result(store, run_id):
    """Download the HDF5 file and return its local path."""
    h5_path = store.download_h5(run_id)
    if h5_path is None:
        raise AssertionError(f"No HDF5 found for run {run_id}")
    print(f"Downloaded HDF5 to: {h5_path}")

    import h5py
    with h5py.File(h5_path, "r") as f:
        top_keys = list(f.keys())
        n_frames = len(f["data"].keys()) if "data" in f else 0
        print(f"HDF5 keys: {top_keys}")
        print(f"Frames: {n_frames}")
    return h5_path


def preview_animation(h5_path):
    """Create and display the widget player with psi/mu heatmaps + I-V curve."""
    from tdgl_sdk import create_player

    player = create_player(h5_path)
    print(f"Player ready: {player.total} frames")
    print("Use player.show(idx) to display a frame, or player.display_player() for the interactive widget.")
    return player


def print_static_summary(h5_path):
    """Print a text summary of the solution (works in terminals without widgets)."""
    import h5py
    import numpy as np

    with h5py.File(h5_path, "r") as f:
        n_frames = len(f["data"].keys())
        print(f"\n{'='*60}")
        print(f"  TDGL Simulation Summary")
        print(f"{'='*60}")
        print(f"  HDF5: {h5_path}")
        print(f"  Total frames: {n_frames}")

        if n_frames > 0:
            first = f["data/0"]
            last_idx = n_frames - 1
            last = f[f"data/{last_idx}"]
            print(f"  Frame keys: {list(first.keys())}")

            if "psi" in last:
                psi = np.array(last["psi"])
                print(f"  Last frame psi: shape={psi.shape}, "
                      f"|psi| range=[{np.abs(psi).min():.4f}, {np.abs(psi).max():.4f}]")
            if "mu" in last:
                mu = np.array(last["mu"])
                print(f"  Last frame mu:  shape={mu.shape}, "
                      f"range=[{mu.min():.4f}, {mu.max():.4f}]")

            times = []
            for i in range(min(n_frames, 10)):
                t = float(f[f"data/{i}"].attrs.get("time", i))
                times.append(t)
            if len(times) > 1:
                print(f"  Time range: {times[0]:.4f} .. {times[-1]:.4f}")
                if n_frames > 10:
                    last_t = float(f[f"data/{last_idx}"].attrs.get("time", last_idx))
                    print(f"  ... to {last_t:.4f}")

        print(f"{'='*60}\n")


def main():
    print("=" * 60)
    print("  TDGL Simulation — Argo Workflow End-to-End Test")
    print("=" * 60)
    print()

    # Step 1: Connect
    print("Step 1: Connecting to Argo and MinIO...")
    argo_svc = create_argo_service()
    store = create_store()
    print(f"  Argo:  {GATEWAY}")
    print(f"  MinIO: {MINIO_ENDPOINT}")
    print()

    # Step 2: Submit
    print("Step 2: Submitting simulation workflow...")
    run_id, wf_name = submit_workflow(argo_svc)
    print()

    # Step 3: Poll
    print(f"Step 3: Polling workflow {wf_name}...")
    phase = poll_workflow(argo_svc, wf_name)
    print()

    # Step 4: Check manifest
    print(f"Step 4: Checking manifest for {run_id[:13]}...")
    manifest = check_manifest(store, run_id)
    print()

    # Step 5: Download
    print(f"Step 5: Downloading HDF5 result...")
    h5_path = download_result(store, run_id)
    print()

    # Step 6: Preview
    print("Step 6: Generating preview...")
    print_static_summary(h5_path)

    try:
        player = preview_animation(h5_path)
    except ImportError:
        print("  (ipywidgets not available — skipping interactive player)")
        print("  Install with: pip install ipywidgets")
    print()

    print("ALL STEPS PASSED")
    print(f"Run ID:  {run_id}")
    print(f"HDF5:    {h5_path}")
    print(f"Status:  {manifest.get('status')}")
    return run_id, h5_path


if __name__ == "__main__":
    main()
