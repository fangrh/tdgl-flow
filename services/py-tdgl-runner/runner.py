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

sys.path.insert(0, "/app/vendor")

import boto3
import h5py
import numpy as np
import tdgl
from botocore.config import Config as BotoConfig
from tdgl_workflow.epsilon import make_gaussian_epsilon

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


def _dataset_location(ds):
    shape = [int(d) for d in ds.shape]
    size = int(np.prod(shape, dtype=np.int64)) * int(ds.dtype.itemsize)
    offset = ds.id.get_offset()
    if offset is None:
        offset = 0
    return {
        "offset": int(offset),
        "size": int(size),
        "element_size": int(ds.dtype.itemsize),
        "shape": shape,
    }


def _find_dataset(file, paths):
    for path in paths:
        if path in file:
            return file[path]
    return None


def _compute_frame_voltage(rsmu, rsdt):
    k = len(rsdt)
    if k == 0 or len(rsmu) < 2 * k:
        return None
    voltage = np.asarray(rsmu[:k]) - np.asarray(rsmu[k:2 * k])
    dt_sum = float(np.sum(rsdt))
    if dt_sum > 0:
        return float(np.sum(voltage * rsdt) / dt_sum)
    return float(np.mean(voltage))


def _build_viewer_index(output_path):
    with h5py.File(output_path, "r") as f:
        data = f["data"]
        frame_indices = sorted(int(name) for name in data.keys() if str(name).isdigit())
        if not frame_indices:
            raise RuntimeError("No frames found in HDF5 output")

        first = data[str(frame_indices[0])]
        n_sites = int(first["psi"].shape[0])

        frame_psi_offsets = []
        frame_mu_offsets = []
        frame_rsmu_offsets = []
        frame_rsdt_offsets = []
        frame_rsdt_sizes = []
        frame_supercurrent_offsets = []
        frame_times = []
        cumulative_time = 0.0

        for fi in frame_indices:
            group = data[str(fi)]
            frame_psi_offsets.append(int(group["psi"].id.get_offset() or 0))
            frame_mu_offsets.append(int(group["mu"].id.get_offset() or 0) if "mu" in group else 0)
            if "supercurrent" in group:
                frame_supercurrent_offsets.append(int(group["supercurrent"].id.get_offset() or 0))
            else:
                frame_supercurrent_offsets.append(0)

            rsmu_offset = 0
            rsdt_offset = 0
            rsdt_size = 0
            if "running_state" in group:
                rs = group["running_state"]
                if "mu" in rs:
                    rsmu_offset = int(rs["mu"].id.get_offset() or 0)
                if "dt" in rs:
                    rsdt = rs["dt"]
                    rsdt_offset = int(rsdt.id.get_offset() or 0)
                    rsdt_size = int(np.prod(rsdt.shape, dtype=np.int64)) * int(rsdt.dtype.itemsize)
                    cumulative_time += float(np.sum(rsdt[...]))
            frame_rsmu_offsets.append(rsmu_offset)
            frame_rsdt_offsets.append(rsdt_offset)
            frame_rsdt_sizes.append(rsdt_size)
            frame_times.append(cumulative_time)

        sites = _find_dataset(
            f,
            ["solution/device/mesh/sites", "mesh/sites", "sites", "mesh_sites"],
        )
        edges = _find_dataset(f, ["mesh/edges", "edges", "mesh_edges"])
        psi = first["psi"]

        return {
            "mesh_sites": _dataset_location(sites) if sites is not None else {
                "offset": 0, "size": n_sites * 2 * 8, "element_size": 8, "shape": [n_sites, 2],
            },
            "mesh_edges": _dataset_location(edges) if edges is not None else {
                "offset": 0, "size": 0, "element_size": 0, "shape": [],
            },
            "frame_psi_offsets": frame_psi_offsets,
            "frame_mu_offsets": frame_mu_offsets,
            "frame_rsmu_offsets": frame_rsmu_offsets,
            "frame_rsdt_offsets": frame_rsdt_offsets,
            "frame_rsdt_sizes": frame_rsdt_sizes,
            "frame_supercurrent_offsets": frame_supercurrent_offsets,
            "total_frames": len(frame_indices),
            "mesh_points": n_sites,
            "frame_times": frame_times,
            "file_size": os.path.getsize(output_path),
            "psi_compressed": bool(psi.compression or psi.chunks is not None),
        }


def _compute_iv_sidecar(output_path, timing_steps, average_time=0.5):
    index = _build_viewer_index(output_path)
    frame_times = index["frame_times"]
    points = []
    vt_by_step = {}

    with h5py.File(output_path, "r") as f:
        data = f["data"]
        for step_idx, step in enumerate(timing_steps):
            ramp_start = float(step["ramp_start"])
            ramp_end = float(step["ramp_end"])
            stable_end = float(step["stable_end"])
            stable_duration = max(0.0, stable_end - ramp_end)
            avg_start = stable_end - float(average_time) * stable_duration
            vt = []
            v_sum = 0.0
            v_count = 0

            for fi, frame_time in enumerate(frame_times):
                if frame_time < ramp_start:
                    continue
                if frame_time >= stable_end:
                    break
                group = data.get(str(fi))
                if group is None or "running_state" not in group:
                    continue
                rs = group["running_state"]
                if "mu" not in rs or "dt" not in rs:
                    continue
                voltage = _compute_frame_voltage(rs["mu"][...].reshape(-1), rs["dt"][...].reshape(-1))
                if voltage is None or not np.isfinite(voltage):
                    continue
                vt.append([float(frame_time - ramp_start), voltage])
                if frame_time >= avg_start:
                    v_sum += voltage
                    v_count += 1

            if vt:
                sample = max(1, len(vt) // 300)
                vt_by_step[str(step_idx)] = vt[::sample]
            if v_count:
                points.append({
                    "i": float(step.get("je_end", 0.0)),
                    "v": float(v_sum / v_count),
                    "step_idx": step_idx,
                })

    return {
        "average_time": average_time,
        "points": points,
        "vt_by_step": vt_by_step,
    }


def _write_and_upload_sidecars(output_path, bucket, run_id, timing_steps, include_iv=False):
    index = _build_viewer_index(output_path)
    index_path = os.path.join(DATA_DIR, "viewer-index.json")
    with open(index_path, "w") as f:
        json.dump(index, f)
    _upload_to_minio(index_path, bucket, f"tdgl-runs/{run_id}/viewer-index.json")

    if include_iv:
        iv = _compute_iv_sidecar(output_path, timing_steps, average_time=0.5)
        iv_path = os.path.join(DATA_DIR, "iv.json")
        with open(iv_path, "w") as f:
            json.dump(iv, f)
        _upload_to_minio(iv_path, bucket, f"tdgl-runs/{run_id}/iv.json")


def _periodic_upload(output_path, bucket, run_id, stop_event, interval=30, timing_steps=None):
    """Background thread: upload growing HDF5 to MinIO every interval seconds."""
    s3 = _get_minio_client()
    key = f"tdgl-runs/{run_id}/output.h5"
    first_success = False
    while not stop_event.is_set():
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            try:
                s3.upload_file(output_path, bucket, key)
                if timing_steps is not None:
                    _write_and_upload_sidecars(output_path, bucket, run_id, timing_steps, include_iv=False)
                first_success = True
            except Exception:
                pass
        wait_seconds = interval if first_success else min(3.0, float(interval))
        stop_event.wait(wait_seconds)


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

    epsilon_params_raw = os.environ.get("EPSILON_PARAMS", "{}")
    epsilon_params = json.loads(epsilon_params_raw)

    epsilon_fn = None
    if epsilon_params.get("type") == "gaussian":
        epsilon_fn = make_gaussian_epsilon(
            positions=epsilon_params["positions"],
            widths=epsilon_params["widths"],
            strengths=epsilon_params["strengths"],
        )
        print(f"Epsilon: Gaussian array, {len(epsilon_params['positions'])} spots")

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

    raw_timing_params = json.loads(os.environ.get("TIMING_PARAMS", "{}"))

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
        "timing_steps": timing_data.get("steps", []),
        "raw_timing_params": raw_timing_params,
        "solver_options": solver_options,
    }, bucket, run_id)

    # Start periodic HDF5 upload for real-time viewing
    upload_stop = threading.Event()
    upload_thread = threading.Thread(
        target=_periodic_upload,
        args=(output_path, bucket, run_id, upload_stop, 30, steps),
        daemon=True,
    )
    upload_thread.start()

    try:
        solve_kwargs = dict(
            device=device,
            options=options,
            terminal_currents=get_terminal_currents,
        )
        if epsilon_fn is not None:
            solve_kwargs["disorder_epsilon"] = epsilon_fn
        solution = tdgl.solve(**solve_kwargs)

        # Stop periodic upload, do final upload
        upload_stop.set()
        upload_thread.join(timeout=60)
        _upload_to_minio(output_path, bucket, f"tdgl-runs/{run_id}/output.h5")
        _write_and_upload_sidecars(output_path, bucket, run_id, steps, include_iv=True)

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
            "timing_steps": timing_data.get("steps", []),
            "raw_timing_params": raw_timing_params,
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
