"""Triton runner: Argo Workflow step that orchestrates a SLURM job on Triton.

SSHs to Triton, submits a SLURM job, polls squeue every 5s while rsyncing
sidecar frames incrementally, uploads them to MinIO for live viewing.
"""
import base64
import json
import os
import subprocess
import sys
import time


# --- SSH helpers ---

_SSH_KEY = os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa")
_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "UserKnownHostsFile=/dev/null",
]


def _ssh(cmd, host, timeout=30, check=True):
    """Run a command on Triton via SSH. Returns CompletedProcess."""
    result = subprocess.run(
        ["ssh", "-i", _SSH_KEY, *_SSH_OPTS, host, cmd],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"SSH command failed: {cmd}\n{result.stderr}")
    return result


def _scp(local_path, remote_path, host):
    """Upload a file to Triton via scp."""
    subprocess.run(
        ["scp", "-i", _SSH_KEY, *_SSH_OPTS,
         local_path, f"{host}:{remote_path}"],
        capture_output=True, text=True, timeout=60, check=True,
    )


def _rsync_sidecars(remote_sidecar_dir, local_sidecar_dir, host):
    """Rsync only new sidecar files from Triton. Returns list of new files."""
    os.makedirs(local_sidecar_dir, exist_ok=True)
    result = subprocess.run(
        ["rsync", "-az", "-e", f"ssh -i {_SSH_KEY} {' '.join(_SSH_OPTS)}",
         "--include=sidecars/***",
         "--include=sidecars/",
         "--exclude=*",
         f"{host}:{remote_sidecar_dir}/",
         f"{local_sidecar_dir}/"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        print(f"  rsync warning: {result.stderr.strip()}", file=sys.stderr)
    # Find new files by listing local sidecar dir
    frames = sorted(
        f for f in os.listdir(local_sidecar_dir)
        if f.startswith("frame_") and f.endswith(".npz")
    )
    return frames


# --- MinIO helpers ---

def _get_minio_client():
    import boto3
    from botocore.config import Config as BotoConfig
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get(
            "MINIO_ENDPOINT", "http://minio.tdgl.svc.cluster.local:9000"
        ),
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin123"),
        region_name="us-east-1",
        config=BotoConfig(connect_timeout=10, retries={"max_attempts": 3}),
    )


def _upload_file(local_path, bucket, key):
    s3 = _get_minio_client()
    s3.upload_file(local_path, bucket, key)
    print(f"  Uploaded {os.path.basename(local_path)} -> s3://{bucket}/{key}")


def _upload_manifest(manifest, bucket, run_id):
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"manifest-{run_id}.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    _upload_file(path, bucket, f"tdgl-runs/{run_id}/manifest.json")


# --- Sidecar -> viewer index conversion ---

def _build_viewer_index_from_sidecars(local_sidecar_dir, run_id):
    """Scan sidecar npz files and build a viewer-compatible index."""
    import numpy as np
    frames = sorted(
        f for f in os.listdir(local_sidecar_dir)
        if f.startswith("frame_") and f.endswith(".npz")
    )
    if not frames:
        return None

    # Read index.json for progress info
    index_path = os.path.join(local_sidecar_dir, "index.json")
    if os.path.exists(index_path):
        with open(index_path) as f:
            triton_index = json.load(f)
    else:
        triton_index = {}

    # Read first frame to get site count
    first = np.load(os.path.join(local_sidecar_dir, frames[0]))
    n_sites = int(first["psi"].shape[0])

    frame_times = []
    for fname in frames:
        data = np.load(os.path.join(local_sidecar_dir, fname))
        frame_times.append(float(data["time"]))

    return {
        "total_frames": len(frames),
        "mesh_points": n_sites,
        "frame_times": frame_times,
        "status": triton_index.get("status", "running"),
        "completed_steps": triton_index.get("completed_steps", 0),
        "total_steps": triton_index.get("total_steps", 0),
        "sidecar_mode": True,
    }


def _upload_sidecar_index(local_sidecar_dir, bucket, run_id):
    """Build and upload viewer index from sidecar frames."""
    import tempfile
    index = _build_viewer_index_from_sidecars(local_sidecar_dir, run_id)
    if index is None:
        return
    path = os.path.join(tempfile.gettempdir(), f"viewer-index-{run_id}.json")
    with open(path, "w") as f:
        json.dump(index, f)
    _upload_file(path, bucket, f"tdgl-runs/{run_id}/viewer-index.json")


def _upload_iv_from_sidecars(local_sidecar_dir, bucket, run_id):
    """Build I-V data from sidecar frames and upload."""
    import numpy as np
    import tempfile
    frames = sorted(
        f for f in os.listdir(local_sidecar_dir)
        if f.startswith("frame_") and f.endswith(".npz")
    )
    if not frames:
        return
    points = []
    vt_by_step = {}
    for fname in frames:
        data = np.load(os.path.join(local_sidecar_dir, fname))
        i_t = float(data["I_t"])
        v_t = float(data["V_t"])
        step = int(data["step"])
        t = float(data["time"])
        if i_t not in [p["i"] for p in points]:
            points.append({"i": i_t, "v": v_t})
        step_key = str(step)
        if step_key not in vt_by_step:
            vt_by_step[step_key] = []
        vt_by_step[step_key].append([t, v_t])

    iv = {"points": points, "vt_by_step": vt_by_step}
    path = os.path.join(tempfile.gettempdir(), f"iv-{run_id}.json")
    with open(path, "w") as f:
        json.dump(iv, f)
    _upload_file(path, bucket, f"tdgl-runs/{run_id}/iv.json")


# --- Main ---

def _retry(fn, retries=3, delay=10):
    """Retry a function with delay between attempts."""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            print(f"  Attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    raise last_exc


def main():
    run_id = os.environ["TDGL_RUN_ID"]
    host = os.environ.get("TRITON_HOST", "fangr1@code.triton.aalto.fi")
    work_dir = os.environ.get("TRITON_WORK_DIR", "/scratch/work/fangr1/tdgl-runner")
    bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")

    timing_json_b64 = os.environ.get("TIMING_JSON_B64", "")
    solver_options_b64 = os.environ.get("SOLVER_OPTIONS_B64", "e30=")
    device_pickle_b64 = os.environ.get("DEVICE_PICKLE_B64", "")
    epsilon_params_b64 = os.environ.get("EPSILON_PARAMS_B64", "")
    sbatch_options = json.loads(os.environ.get("SBATCH_OPTIONS", "{}"))
    sidecar_interval = os.environ.get("SIDECAR_INTERVAL", "500")
    poll_interval = int(os.environ.get("POLL_INTERVAL", "5"))
    pending_timeout = int(os.environ.get("PENDING_TIMEOUT", "3600"))

    job_dir = f"{work_dir}/jobs/{run_id}"
    remote_sidecar_dir = f"{job_dir}/sidecars"
    local_base = f"/tmp/triton-{run_id}"
    local_sidecar_dir = f"{local_base}/sidecars"

    print(f"=== Triton runner started: run_id={run_id} ===")

    # 1. Upload config files to Triton
    print("Step 1: Uploading config to Triton...")
    _retry(lambda: _ssh(f"mkdir -p {job_dir}/sidecars", host))

    timing_json = base64.b64decode(timing_json_b64).decode()
    solver_json = base64.b64decode(solver_options_b64).decode()

    # Write files locally then scp
    import tempfile
    tmp = tempfile.mkdtemp()
    for name, content in [
        ("timing.json", timing_json),
        ("solver_options.json", solver_json),
    ]:
        path = os.path.join(tmp, name)
        with open(path, "w") as f:
            f.write(content)
        _retry(lambda p=path, n=name: _scp(p, f"{job_dir}/{n}", host))

    if device_pickle_b64:
        device_path = os.path.join(tmp, "device.pkl")
        with open(device_path, "wb") as f:
            f.write(base64.b64decode(device_pickle_b64))
        _retry(lambda: _scp(device_path, f"{job_dir}/device.pkl", host))

    if epsilon_params_b64:
        eps_path = os.path.join(tmp, "epsilon_params.json")
        with open(eps_path, "w") as f:
            f.write(base64.b64decode(epsilon_params_b64).decode().encode())
        _retry(lambda: _scp(eps_path, f"{job_dir}/epsilon_params.json", host))

    # 2. Submit SLURM job
    print("Step 2: Submitting SLURM job...")
    sbatch_flags = ""
    for k, v in sbatch_options.items():
        sbatch_flags += f" --{k}={v}"

    result = _retry(lambda: _ssh(
        f"cd {work_dir} && sbatch {sbatch_flags} submit.sh {run_id} {sidecar_interval}",
        host,
    ))
    # Parse job ID from sbatch output: "Submitted batch job 12345"
    job_id = result.stdout.strip().split()[-1]
    print(f"  SLURM job ID: {job_id}")

    # 3. Upload "running" manifest
    _upload_manifest({
        "run_id": run_id,
        "status": "running",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "triton_job_id": job_id,
    }, bucket, run_id)

    # 4. Poll loop: check job status + rsync sidecars + upload to MinIO
    print("Step 3: Polling SLURM job and syncing sidecars...")
    known_frames = 0
    pending_start = time.time()

    while True:
        # Check job status
        result = _ssh(
            f"squeue -j {job_id} -h -o %T 2>/dev/null || echo GONE",
            host, check=False,
        )
        state = result.stdout.strip()

        if state in ("", "GONE"):
            # Job finished or no longer in queue — check final state
            state_result = _ssh(
                f"sacct -j {job_id} -n -o State -X 2>/dev/null | head -1",
                host, check=False,
            )
            final_state = state_result.stdout.strip().split()[0] if state_result.stdout.strip() else "UNKNOWN"
            print(f"  Job {job_id} final state: {final_state}")
            break

        if state == "PENDING":
            if time.time() - pending_start > pending_timeout:
                _ssh(f"scancel {job_id}", host, check=False)
                _upload_manifest({
                    "run_id": run_id, "status": "failed",
                    "error": f"SLURM job pending >{pending_timeout}s, cancelled",
                    "triton_job_id": job_id,
                }, bucket, run_id)
                raise RuntimeError(f"Job {job_id} pending timeout")
            time.sleep(poll_interval)
            continue

        if state == "CANCELLED":
            _upload_manifest({
                "run_id": run_id, "status": "failed",
                "error": "SLURM job cancelled",
                "triton_job_id": job_id,
            }, bucket, run_id)
            raise RuntimeError(f"Job {job_id} cancelled")

        # Job is RUNNING or similar — rsync sidecars
        try:
            frames = _rsync_sidecars(remote_sidecar_dir, local_sidecar_dir, host)
            new_count = len(frames)
            if new_count > known_frames:
                print(f"  Synced {new_count - known_frames} new sidecar frames (total: {new_count})")
                known_frames = new_count
                _upload_sidecar_index(local_sidecar_dir, bucket, run_id)
                _upload_iv_from_sidecars(local_sidecar_dir, bucket, run_id)
        except Exception as e:
            print(f"  rsync error (will retry): {e}", file=sys.stderr)

        time.sleep(poll_interval)

    # 5. Final sync: rsync remaining sidecars + main HDF5
    print("Step 4: Final sync...")
    _retry(lambda: _rsync_sidecars(remote_sidecar_dir, local_sidecar_dir, host))

    # Rsync main HDF5
    subprocess.run(
        ["rsync", "-az", "-e", f"ssh -i {_SSH_KEY} {' '.join(_SSH_OPTS)}",
         f"{host}:{job_dir}/output.h5",
         f"{local_base}/output.h5"],
        capture_output=True, text=True, timeout=600, check=False,
    )

    h5_local = f"{local_base}/output.h5"
    if os.path.exists(h5_local) and os.path.getsize(h5_local) > 0:
        _upload_file(h5_local, bucket, f"tdgl-runs/{run_id}/output.h5")
        print(f"  Uploaded final HDF5 ({os.path.getsize(h5_local)} bytes)")

    # Final sidecar + index upload
    _upload_sidecar_index(local_sidecar_dir, bucket, run_id)
    _upload_iv_from_sidecars(local_sidecar_dir, bucket, run_id)

    # 6. Upload completed manifest
    _upload_manifest({
        "run_id": run_id,
        "status": "completed",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "triton_job_id": job_id,
        "n_sidecar_frames": known_frames,
    }, bucket, run_id)

    print(f"=== Triton runner completed: run_id={run_id} ===")


if __name__ == "__main__":
    main()
