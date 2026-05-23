"""Python tdgl simulation runner (Argo simulate step).

Reads mesh_meta.json and timing.json from shared volume, runs the Python tdgl solver
with output_file to produce HDF5, uploads to MinIO periodically during the solve
for real-time viewing, and writes a final manifest on completion.
"""
import json
import os
import sys
import threading
from datetime import datetime, timezone

import boto3
import numpy as np
from botocore.config import Config as BotoConfig

DATA_DIR = os.environ.get("DATA_DIR", "/data")


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


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)
    bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")
    now = datetime.now(timezone.utc).isoformat()

    with open(os.path.join(DATA_DIR, "mesh_meta.json")) as f:
        mesh_meta = json.load(f)

    with open(os.path.join(DATA_DIR, "timing.json")) as f:
        timing_data = json.load(f)

    import tdgl

    sites = np.array(mesh_meta["sites"], dtype=np.float64)
    triangles = np.array(mesh_meta["elements"], dtype=np.int64)

    layer = tdgl.Layer(
        coherence_length=mesh_meta["layer"]["coherence_length"],
        london_lambda=mesh_meta["layer"]["london_lambda"],
        thickness=mesh_meta["layer"]["thickness"],
        gamma=mesh_meta["layer"]["gamma"],
    )

    device = tdgl.Device(
        name=mesh_meta["device_constants"]["name"],
        layer=layer,
        film=tdgl.Polygon("film", points=sites[triangles].reshape(-1, 2)),
        terminals=[
            tdgl.Polygon(t["name"], points=sites[t["site_indices"]].reshape(-1, 2))
            for t in mesh_meta["terminals"]
        ],
        probe_points=[sites[i] for i in mesh_meta["probe_indices"]],
    )
    device._points = sites
    device._triangles = triangles
    device.make_mesh(max_edge_length=mesh_meta["max_edge_length"], smooth=mesh_meta["smooth"])

    steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])
    times = [s["stable_end"] for s in steps]
    je_values = [s["je_end"] for s in steps]
    terminal_currents_list = [{"source": je, "drain": -je} for je in je_values]

    scenario = tdgl.SweepScenario(
        times=times,
        terminal_currents=terminal_currents_list,
    )

    output_path = os.path.join(DATA_DIR, "output.h5")

    options = tdgl.SolverOptions(
        solve_time=timing_data["solve_time"],
        dt=solver_options.get("dt", 1e-6),
        max_dt=solver_options.get("max_dt", 0.1),
        adaptive=solver_options.get("adaptive", True),
        save_every=solver_options.get("save_every", 100),
        output_file=output_path,
    )

    # Upload "running" manifest so viewer knows the simulation is in progress
    _upload_manifest({
        "run_id": run_id,
        "status": "running",
        "created_at": now,
        "n_sites": mesh_meta["num_sites"],
        "device_params": {
            "film_width": mesh_meta["film_width"],
            "film_height": mesh_meta["film_height"],
            "elec_width": mesh_meta["elec_width"],
            "elec_height": mesh_meta["elec_height"],
            "max_edge_length": mesh_meta["max_edge_length"],
            "smooth": mesh_meta["smooth"],
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
            scenario,
            options,
            checkpoint_path=os.path.join(DATA_DIR, "checkpoint.zarr"),
        )

        # Stop periodic upload, do final upload
        upload_stop.set()
        upload_thread.join(timeout=60)
        _upload_to_minio(output_path, bucket, f"tdgl-runs/{run_id}/output.h5")

        manifest = {
            "run_id": run_id,
            "status": "completed",
            "created_at": now,
            "n_sites": mesh_meta["num_sites"],
            "n_frames": len(solution.times),
            "device_params": {
                "film_width": mesh_meta["film_width"],
                "film_height": mesh_meta["film_height"],
                "elec_width": mesh_meta["elec_width"],
                "elec_height": mesh_meta["elec_height"],
                "max_edge_length": mesh_meta["max_edge_length"],
                "smooth": mesh_meta["smooth"],
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
