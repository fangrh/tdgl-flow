"""Sidecar sync helpers for Triton HPC real-time data."""
import json
import os
import subprocess
import tempfile

import boto3
import numpy as np
from botocore.config import Config as BotoConfig


def rsync_sidecars(remote_dir, local_dir, ssh_key, host):
    """Incremental rsync of sidecar .npz and index.json from Triton."""
    os.makedirs(local_dir, exist_ok=True)
    ssh_opts = (
        f"ssh -i {ssh_key}"
        " -o StrictHostKeyChecking=no"
        " -o ConnectTimeout=10"
        " -o UserKnownHostsFile=/dev/null"
    )
    subprocess.run(
        [
            "rsync", "-az", "--update", "--partial",
            "-e", ssh_opts,
            "--include=frame_*.npz",
            "--include=index.json",
            "--exclude=*",
            f"{host}:{remote_dir}/",
            f"{local_dir}/",
        ],
        timeout=120, check=False,
    )


def _get_minio_client(endpoint):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin123"),
        region_name="us-east-1",
        config=BotoConfig(connect_timeout=10, retries={"max_attempts": 3}),
    )


def minio_object_exists(endpoint, bucket, key):
    """Check if an object exists in MinIO."""
    from botocore.exceptions import ClientError
    s3 = _get_minio_client(endpoint)
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def upload_to_minio(local_path, bucket, key, endpoint):
    """Upload a single file to MinIO."""
    s3 = _get_minio_client(endpoint)
    s3.upload_file(local_path, bucket, key)


def upload_json_to_minio(data, bucket, key, endpoint):
    """Upload a JSON-serializable dict to MinIO."""
    path = os.path.join(tempfile.gettempdir(), os.path.basename(key))
    with open(path, "w") as f:
        json.dump(data, f)
    upload_to_minio(path, bucket, key, endpoint)


def build_viewer_index(local_dir, run_id=None):
    """Scan sidecar .npz files and return viewer-compatible index dict. Returns None if no frames."""
    frames = sorted(
        f for f in os.listdir(local_dir)
        if f.startswith("frame_") and f.endswith(".npz")
    )
    if not frames:
        return None

    index_path = os.path.join(local_dir, "index.json")
    if os.path.exists(index_path):
        with open(index_path) as f:
            triton_index = json.load(f)
    else:
        triton_index = {}

    first = np.load(os.path.join(local_dir, frames[0]))
    n_sites = int(first["psi"].shape[0])
    first.close()

    frame_times = []
    for fname in frames:
        data = np.load(os.path.join(local_dir, fname))
        frame_times.append(float(data["time"]))
        data.close()

    return {
        "total_frames": len(frames),
        "mesh_points": n_sites,
        "frame_times": frame_times,
        "status": triton_index.get("status", "running"),
        "completed_steps": triton_index.get("completed_steps", 0),
        "total_steps": triton_index.get("total_steps", 0),
        "sidecar_mode": True,
    }


def build_iv_data(local_dir):
    """Build I-V curve data from sidecar frames. Returns None if no frames."""
    frames = sorted(
        f for f in os.listdir(local_dir)
        if f.startswith("frame_") and f.endswith(".npz")
    )
    if not frames:
        return None

    points = []
    seen_i = []
    vt_by_step = {}
    for fname in frames:
        data = np.load(os.path.join(local_dir, fname))
        i_t = float(data["I_t"])
        v_t = float(data["V_t"])
        step = int(data["step"])
        t = float(data["time"])
        data.close()

        if i_t not in seen_i:
            seen_i.append(i_t)
            points.append({"i": i_t, "v": v_t})

        step_key = str(step)
        if step_key not in vt_by_step:
            vt_by_step[step_key] = []
        vt_by_step[step_key].append([t, v_t])

    return {"points": points, "vt_by_step": vt_by_step}