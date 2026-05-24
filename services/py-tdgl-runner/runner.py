"""Python tdgl simulation runner (Argo simulate step).

Reads device.pkl and timing.json from shared volume, runs the Python tdgl solver
with output_file to produce HDF5, uploads to MinIO periodically during the solve
for real-time viewing, and writes a final manifest on completion.
"""
import json
import os
import pickle
import sys
import threading
from datetime import datetime, timezone

import boto3
import numpy as np
import tdgl
from botocore.config import Config as BotoConfig

DATA_DIR = os.environ.get("DATA_DIR", "/data")


class SaveWindowTimeline:
    def __init__(self) -> None:
        self.offset = 0.0

    def map_physical(self, *, save_start: float, physical_time: float) -> float:
        return self.offset + max(0.0, physical_time - save_start)

    def finish_window(self, *, save_time: float) -> None:
        self.offset += save_time


def _group_solution_indices_by_save_window(times: np.ndarray, steps: list[dict]) -> list[list[int]]:
    grouped = []
    for step in steps:
        indices = [
            int(i)
            for i, time_value in enumerate(times)
            if step["save_start"] <= float(time_value) <= step["save_end"]
        ]
        if not indices:
            raise RuntimeError(
                f"No saved frames found in save window [{step['save_start']}, {step['save_end']}]"
            )
        grouped.append(indices)
    return grouped


def _get_minio_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://minio.tdgl.svc.cluster.local:9000"),
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin123"),
        region_name="us-east-1",
        config=BotoConfig(connect_timeout=10, retries={"max_attempts": 3}),
    )


def _upload_to_minio(local_path, bucket, key):
    s3 = _get_minio_client()
    s3.upload_file(local_path, bucket, key)
    print(f"Uploaded {local_path} -> s3://{bucket}/{key}")


def _upload_manifest(manifest, bucket, run_id):
    manifest_path = os.path.join(DATA_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    _upload_to_minio(manifest_path, bucket, f"tdgl-runs/{run_id}/manifest.json")


def _periodic_upload(output_path, bucket, run_id, stop_event, interval=30):
    """Background thread: upload growing HDF5 to MinIO every interval seconds."""
    s3 = _get_minio_client()
    key = f"tdgl-runs/{run_id}/output.h5"
    while not stop_event.is_set():
        stop_event.wait(interval)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            try:
                s3.upload_file(output_path, bucket, key)
            except Exception:
                pass


def _terminal_currents_from_steps(steps):
    """Build the py-tdgl terminal current function for a timing schedule."""
    def get_terminal_currents(t):
        for step in steps:
            if t < step["ramp_start"]:
                continue
            ramp_duration = step["ramp_end"] - step["ramp_start"]
            if ramp_duration > 0 and t <= step["ramp_end"]:
                frac = (t - step["ramp_start"]) / ramp_duration
                je = step["je_start"] + frac * (step["je_end"] - step["je_start"])
                return {"source": je, "drain": -je}
            if t <= step["stable_end"]:
                return {"source": step["je_end"], "drain": -step["je_end"]}

        if steps:
            # Solver callbacks can be evaluated just past solve_time. Holding the
            # final scheduled current avoids a spurious return-to-zero I-V tail.
            je = steps[-1]["je_end"]
            return {"source": je, "drain": -je}
        return {"source": 0.0, "drain": 0.0}

    return get_terminal_currents


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)
    bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")
    now = datetime.now(timezone.utc).isoformat()

    with open(os.path.join(DATA_DIR, "device.pkl"), "rb") as f:
        device = pickle.load(f)

    with open(os.path.join(DATA_DIR, "timing.json")) as f:
        timing_data = json.load(f)

    n_sites = len(device.points)

    # Read mesh_meta.json for manifest metadata if available
    mesh_meta_path = os.path.join(DATA_DIR, "mesh_meta.json")
    mesh_meta = {}
    if os.path.exists(mesh_meta_path):
        with open(mesh_meta_path) as f:
            mesh_meta = json.load(f)

    steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])

    get_terminal_currents = _terminal_currents_from_steps(steps)

    output_path = os.path.join(DATA_DIR, "output.h5")

    options = tdgl.SolverOptions(
        solve_time=timing_data["solve_time"],
        dt_init=solver_options.get("dt_init", 1e-6),
        dt_max=solver_options.get("dt_max", 0.1),
        adaptive=solver_options.get("adaptive", True),
        save_every=solver_options.get("save_every", 100),
        output_file=output_path,
    )

    # Upload "running" manifest so viewer knows the simulation is in progress
    _upload_manifest({
        "run_id": run_id,
        "status": "running",
        "created_at": now,
        "n_sites": n_sites,
        "device_params": {
            "film_width": mesh_meta.get("film_width"),
            "film_height": mesh_meta.get("film_height"),
            "elec_width": mesh_meta.get("elec_width"),
            "elec_height": mesh_meta.get("elec_height"),
            "max_edge_length": mesh_meta.get("max_edge_length"),
            "smooth": mesh_meta.get("smooth"),
        },
        "timing_params": {
            "mode": timing_data["mode"],
            "n_steps": timing_data["n_steps"],
            "solve_time": timing_data["solve_time"],
        },
        "solver_options": solver_options,
    }, bucket, run_id)

    # Start periodic HDF5 upload for real-time viewing
    upload_stop = threading.Event()
    upload_thread = threading.Thread(
        target=_periodic_upload,
        args=(output_path, bucket, run_id, upload_stop, 30),
        daemon=True,
    )
    upload_thread.start()

    try:
        solution = tdgl.solve(
            device,
            options,
            terminal_currents=get_terminal_currents,
        )

        # Stop periodic upload, do final upload
        upload_stop.set()
        upload_thread.join(timeout=60)
        _upload_to_minio(output_path, bucket, f"tdgl-runs/{run_id}/output.h5")

        manifest = {
            "run_id": run_id,
            "status": "completed",
            "created_at": now,
            "n_sites": n_sites,
            "n_frames": len(solution.times),
            "device_params": {
                "film_width": mesh_meta.get("film_width"),
                "film_height": mesh_meta.get("film_height"),
                "elec_width": mesh_meta.get("elec_width"),
                "elec_height": mesh_meta.get("elec_height"),
                "max_edge_length": mesh_meta.get("max_edge_length"),
                "smooth": mesh_meta.get("smooth"),
            },
            "timing_params": {
                "mode": timing_data["mode"],
                "n_steps": timing_data["n_steps"],
                "solve_time": timing_data["solve_time"],
            },
            "solver_options": solver_options,
        }
        _upload_manifest(manifest, bucket, run_id)
        print(f"Run {run_id} completed. {len(solution.times)} frames.")

    except Exception as exc:
        upload_stop.set()
        upload_thread.join(timeout=60)
        manifest = {
            "run_id": run_id,
            "status": "failed",
            "created_at": now,
            "error": str(exc),
        }
        _upload_manifest(manifest, bucket, run_id)
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
