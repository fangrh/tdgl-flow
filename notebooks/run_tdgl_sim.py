#%%
"""End-to-end TDGL simulation test via Argo Workflows.

Uses the SimulationPipeline SDK to: submit workflow, poll until completion,
download HDF5 from MinIO, verify data integrity, and show animation preview.

Usage:
    python notebooks/run_tdgl_sim.py

Prerequisites:
    pip install hera-workflows boto3 httpx h5py numpy scipy pillow matplotlib tdgl
    Argo Workflows accessible at localhost:30080 (nginx ingress)
    kubectl port-forward -n tdgl svc/minio 30900:9000
"""
#%%
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tdgl_sdk import SimulationPipeline, examine_h5, format_report, create_player

# ── Configuration ──────────────────────────────────────────────────────
ARGO_URL = "http://localhost:30080"
MINIO_ENDPOINT = "http://localhost:30900"

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


def main():
    print("=" * 60)
    print("  TDGL Simulation — End-to-End Pipeline Test")
    print("=" * 60)
    print(f"  Argo:  {ARGO_URL}")
    print(f"  MinIO: {MINIO_ENDPOINT}")
    print()

    pipeline = SimulationPipeline(
        argo_url=ARGO_URL,
        minio_endpoint=MINIO_ENDPOINT,
    )

    # Full pipeline: submit -> poll -> download -> verify
    result = pipeline.run(
        device_params=DEVICE_PARAMS,
        timing_params=TIMING_PARAMS,
        solver_options=SOLVER_OPTIONS,
    )

    print()
    print("=" * 60)
    print("  Pipeline Result")
    print("=" * 60)
    print(f"  Run ID:     {result['run_id']}")
    print(f"  Workflow:   {result['wf_name']}")
    print(f"  Phase:      {result['phase']}")
    print(f"  HDF5:       {result['h5_path']}")
    print(f"  Healthy:    {result['report']['healthy']}")
    print(f"  Summary:    {result['report']['summary']}")
    print()

    # Show detailed diagnostic report
    print(result["report"]["examine_text"])
    print()

    # Try interactive viewer
    try:
        player = create_player(result["h5_path"])
        print(f"Player ready: {player.total} frames")
        print("Use player.show(idx) or player.display_player() for interactive widget.")
    except ImportError:
        print("(ipywidgets not available — skipping interactive player)")

    print()
    if result["report"]["healthy"]:
        print("ALL CHECKS PASSED")
    else:
        print("WARNING: Data quality issues detected!")
        for issue in result["report"]["examine"]["issues"]:
            print(f"  - {issue}")
        for error in result["report"]["debug"]["errors"]:
            print(f"  - {error}")

    return result


if __name__ == "__main__":
    main()
# %%
