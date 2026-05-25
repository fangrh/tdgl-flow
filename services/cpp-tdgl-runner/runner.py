"""cpp-tdgl simulation runner (Argo simulate step).

Builds mesh with Python tdgl, converts to cpp-tdgl HDF5 format,
runs C++ solver, uploads results to MinIO for real-time viewing.
"""
import json
import os
import pickle
import subprocess
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, "/app/vendor")

import boto3
import h5py
import numpy as np
from botocore.config import Config as BotoConfig
from tdgl_workflow.epsilon import make_gaussian_epsilon
from convert_mesh import write_cpp_mesh

DATA_DIR = os.environ.get("DATA_DIR", "/data")
CPP_SOLVER = os.environ.get("CPP_SOLVER", "/usr/local/bin/cpp-tdgl-solve")


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
    path = os.path.join(DATA_DIR, "manifest.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    _upload_to_minio(path, bucket, f"tdgl-runs/{run_id}/manifest.json")


def _periodic_upload(output_path, bucket, run_id, stop_event, interval=30):
    s3 = _get_minio_client()
    key = f"tdgl-runs/{run_id}/output.h5"
    while not stop_event.is_set():
        stop_event.wait(interval)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            try:
                s3.upload_file(output_path, bucket, key)
            except Exception:
                pass


def main():
    run_id = os.environ["TDGL_RUN_ID"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)
    epsilon_params_raw = os.environ.get("EPSILON_PARAMS", "{}")
    epsilon_params = json.loads(epsilon_params_raw)
    bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")
    now = datetime.now(timezone.utc).isoformat()

    # Load device (built by build_device.py)
    with open(os.path.join(DATA_DIR, "device.pkl"), "rb") as f:
        device = pickle.load(f)

    # Load timing (built by build_timing.py)
    with open(os.path.join(DATA_DIR, "timing.json")) as f:
        timing_data = json.load(f)

    # Load mesh metadata
    mesh_meta_path = os.path.join(DATA_DIR, "mesh_meta.json")
    mesh_meta = {}
    if os.path.exists(mesh_meta_path):
        with open(mesh_meta_path) as f:
            mesh_meta = json.load(f)

    n_sites = len(device.points)

    # Build epsilon function
    epsilon_fn = None
    if epsilon_params.get("type") == "gaussian":
        epsilon_fn = make_gaussian_epsilon(
            positions=epsilon_params["positions"],
            widths=epsilon_params["widths"],
            strengths=epsilon_params["strengths"],
        )
        print(f"Epsilon: Gaussian, {len(epsilon_params['positions'])} spots")

    # Convert device to cpp-tdgl-compatible HDF5
    cpp_mesh_path = os.path.join(DATA_DIR, "cpp_mesh.h5")
    write_cpp_mesh(device, cpp_mesh_path,
                   solver_options=solver_options,
                   epsilon_fn=epsilon_fn)
    print(f"cpp-tdgl mesh written: {cpp_mesh_path}")

    # Prepare output paths
    output_path = os.path.join(DATA_DIR, "output.h5")
    timing_path = os.path.join(DATA_DIR, "timing.json")

    # Upload "running" manifest
    raw_timing_params = json.loads(os.environ.get("TIMING_PARAMS", "{}"))
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
        "timing_steps": timing_data.get("steps", []),
        "raw_timing_params": raw_timing_params,
        "solver_options": solver_options,
    }, bucket, run_id)

    # Start periodic upload
    upload_stop = threading.Event()
    upload_thread = threading.Thread(
        target=_periodic_upload,
        args=(output_path, bucket, run_id, upload_stop, 30),
        daemon=True,
    )
    upload_thread.start()

    try:
        # Build C++ solver command
        cmd = [
            CPP_SOLVER,
            "--mesh", cpp_mesh_path,
            "--output", output_path,
            "--timing", timing_path,
            "--solver-options", json.dumps(solver_options),
        ]
        print(f"Running: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            raise RuntimeError(f"cpp-tdgl-solve exited with code {result.returncode}")

        # Final upload
        upload_stop.set()
        upload_thread.join(timeout=60)
        _upload_to_minio(output_path, bucket, f"tdgl-runs/{run_id}/output.h5")

        # Count frames
        n_frames = 0
        with h5py.File(output_path, "r") as f:
            n_frames = len(f["data"].keys())

        manifest = {
            "run_id": run_id,
            "status": "completed",
            "created_at": now,
            "n_sites": n_sites,
            "n_frames": n_frames,
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
            "timing_steps": timing_data.get("steps", []),
            "raw_timing_params": raw_timing_params,
            "solver_options": solver_options,
        }
        _upload_manifest(manifest, bucket, run_id)
        print(f"Run {run_id} completed. {n_frames} frames.")

    except Exception as exc:
        upload_stop.set()
        upload_thread.join(timeout=60)
        _upload_manifest({
            "run_id": run_id,
            "status": "failed",
            "created_at": now,
            "error": str(exc),
        }, bucket, run_id)
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
