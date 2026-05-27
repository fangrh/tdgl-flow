"""Sidecar sync helpers for Triton HPC real-time data."""
import json
import os
import subprocess
import tempfile
import time

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


class SidecarSyncOP:
    """DFlow OP that syncs sidecar frames from Triton to MinIO.

    Runs as a K8s pod in parallel with the simulation step. Loops:
    rsync from Triton -> upload to MinIO -> check completion -> repeat.
    """

    @classmethod
    def get_input_sign(cls):
        return {"run_id": str}

    @classmethod
    def get_output_sign(cls):
        return {"status": str}

    def execute(self, op_in):
        run_id = op_in["run_id"]
        remote_dir = f"/scratch/work/fangr1/tdgl-runner/jobs/{run_id}/sidecars"
        local_dir = f"/tmp/triton-{run_id}/sidecars"
        ssh_key = os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa")
        host = os.environ.get("TRITON_HOST", "fangr1@code.triton.aalto.fi")
        bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")
        endpoint = os.environ.get(
            "MINIO_ENDPOINT", "http://minio.tdgl.svc.cluster.local:9000"
        )
        timeout = int(os.environ.get("SYNC_TIMEOUT", "14400"))

        start_time = time.time()

        while True:
            if time.time() - start_time > timeout:
                return {"status": "timeout"}

            try:
                rsync_sidecars(remote_dir, local_dir, ssh_key, host)
                frames = sorted(
                    f for f in os.listdir(local_dir)
                    if f.startswith("frame_") and f.endswith(".npz")
                )

                if frames:
                    for fname in frames:
                        local_path = os.path.join(local_dir, fname)
                        key = f"tdgl-runs/{run_id}/sidecars/{fname}"
                        if not minio_object_exists(endpoint, bucket, key):
                            upload_to_minio(local_path, bucket, key, endpoint)

                    index = build_viewer_index(local_dir, run_id)
                    if index:
                        upload_json_to_minio(
                            index, bucket,
                            f"tdgl-runs/{run_id}/viewer-index.json",
                            endpoint,
                        )

                    iv = build_iv_data(local_dir)
                    if iv:
                        upload_json_to_minio(
                            iv, bucket,
                            f"tdgl-runs/{run_id}/iv.json",
                            endpoint,
                        )

                index_path = os.path.join(local_dir, "index.json")
                if os.path.exists(index_path):
                    with open(index_path) as f:
                        status = json.load(f).get("status", "running")
                    if status in ("completed", "failed"):
                        return {"status": status}

            except Exception as e:
                print(f"sidecar-sync error (will retry): {e}")

            time.sleep(5)