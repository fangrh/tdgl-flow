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


def rsync_discrete_h5(remote_dir, local_dir, ssh_key, host):
    """Incremental rsync of discrete H5 files and index from Triton."""
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
            "--include=je_*.h5",
            "--include=discrete_index.json",
            "--exclude=*",
            f"{host}:{remote_dir}/",
            f"{local_dir}/",
        ],
        timeout=300, check=False,
    )


def build_discrete_viewer_index(local_dir, run_id=None):
    """Build viewer-compatible index from discrete H5 files.

    Reads local discrete_index.json, parses each step's H5 with h5py to
    extract byte offsets for psi/mu/running_state, and returns a dict
    with absolute frame times, step mapping, and per-step offsets.
    Returns None if no completed steps.
    """
    import h5py as _h5py

    index_path = os.path.join(local_dir, "discrete_index.json")
    if not os.path.exists(index_path):
        return None

    with open(index_path) as f:
        dindex = json.load(f)

    completed = [s for s in dindex.get("steps", []) if s.get("status") == "completed"]
    if not completed:
        return None

    # Read mesh info from first H5
    # tdgl stores mesh at solution/device/mesh/sites
    mesh_points = 0
    mesh_sites_offset = 0
    mesh_sites_size = 0
    first_h5 = os.path.join(local_dir, completed[0]["h5_file"])
    if os.path.exists(first_h5):
        try:
            with _h5py.File(first_h5, "r") as f:
                sites_ds = None
                for path in ("mesh/sites", "solution/device/mesh/sites"):
                    parts = path.split("/")
                    obj = f
                    for p in parts:
                        if p in obj:
                            obj = obj[p]
                        else:
                            obj = None
                            break
                    if obj is not None and isinstance(obj, _h5py.Dataset):
                        sites_ds = obj
                        break
                if sites_ds is not None:
                    mesh_points = sites_ds.shape[0]
                    mesh_sites_offset = sites_ds.id.get_offset() or 0
                    mesh_sites_size = sites_ds.size * sites_ds.dtype.itemsize
        except Exception:
            pass

    # Build per-step offset arrays
    step_indices = []
    frame_times = []
    frame_step_map = []
    frame_local_idx = []
    total_frames = 0

    for si, step_info in enumerate(completed):
        h5_path = os.path.join(local_dir, step_info["h5_file"])
        if not os.path.exists(h5_path):
            continue

        step_offsets = {
            "h5_file": step_info["h5_file"],
            "je_start": step_info["je_start"],
            "je_end": step_info["je_end"],
            "ramp_start": step_info["ramp_start"],
            "ramp_end": step_info["ramp_end"],
            "stable_end": step_info["stable_end"],
            "n_frames": 0,
            "psi_offsets": [],
            "mu_offsets": [],
            "rsmu_offsets": [],
            "rsdt_offsets": [],
            "rsdt_sizes": [],
        }

        try:
            with _h5py.File(h5_path, "r") as f:
                if "data" not in f:
                    continue
                data = f["data"]
                frame_keys = sorted(int(k) for k in data.keys() if k.isdigit())
                step_offsets["n_frames"] = len(frame_keys)
                step_duration = step_info["stable_end"] - step_info["ramp_start"]

                for li, fi in enumerate(frame_keys):
                    grp = data[str(fi)]

                    # psi offset
                    psi_off = grp["psi"].id.get_offset() if "psi" in grp else 0
                    step_offsets["psi_offsets"].append(psi_off or 0)

                    # mu offset
                    mu_off = grp["mu"].id.get_offset() if "mu" in grp else 0
                    step_offsets["mu_offsets"].append(mu_off or 0)

                    # running_state offsets
                    if "running_state" in grp:
                        rs = grp["running_state"]
                        rsmu_off = rs["mu"].id.get_offset() if "mu" in rs else 0
                        step_offsets["rsmu_offsets"].append(rsmu_off or 0)
                        if "dt" in rs:
                            dt_ds = rs["dt"]
                            rsdt_off = dt_ds.id.get_offset() or 0
                            rsdt_sz = dt_ds.size * dt_ds.dtype.itemsize
                            step_offsets["rsdt_offsets"].append(rsdt_off)
                            step_offsets["rsdt_sizes"].append(rsdt_sz)
                        else:
                            step_offsets["rsdt_offsets"].append(0)
                            step_offsets["rsdt_sizes"].append(0)
                    else:
                        step_offsets["rsmu_offsets"].append(0)
                        step_offsets["rsdt_offsets"].append(0)
                        step_offsets["rsdt_sizes"].append(0)

                    # Frame time (approximate: spread evenly across step)
                    n = len(frame_keys)
                    frac = li / max(n - 1, 1) if n > 1 else 0.0
                    abs_time = step_info["ramp_start"] + frac * step_duration
                    frame_times.append(abs_time)
                    frame_step_map.append(si)
                    frame_local_idx.append(li)
                    total_frames += 1

        except Exception:
            continue

        step_indices.append(step_offsets)

    if total_frames == 0:
        return None

    return {
        "total_frames": total_frames,
        "mesh_points": mesh_points,
        "mesh_sites_offset": mesh_sites_offset,
        "mesh_sites_size": mesh_sites_size,
        "frame_times": frame_times,
        "frame_step_map": frame_step_map,
        "frame_local_idx": frame_local_idx,
        "completed_steps": len(completed),
        "total_steps": dindex.get("total_steps", len(completed)),
        "status": dindex.get("status", "running"),
        "solve_time": dindex.get("solve_time", 0.0),
        "discrete_mode": True,
        "run_id": run_id,
        "steps": step_indices,
    }


def build_discrete_iv_data(local_dir):
    """Build I-V curve data from discrete H5 files.

    Reads each completed step's H5, extracts V_t and I_t from running_state,
    returns points list and vt_by_step dict.
    Returns None if no data.
    """
    import h5py as _h5py

    index_path = os.path.join(local_dir, "discrete_index.json")
    if not os.path.exists(index_path):
        return None

    with open(index_path) as f:
        dindex = json.load(f)

    completed = [s for s in dindex.get("steps", []) if s.get("status") == "completed"]
    if not completed:
        return None

    points = []
    seen_je = []
    vt_by_step = {}

    for step_info in completed:
        h5_path = os.path.join(local_dir, step_info["h5_file"])
        if not os.path.exists(h5_path):
            continue

        try:
            with _h5py.File(h5_path, "r") as f:
                if "data" not in f:
                    continue
                data = f["data"]
                indices = sorted(int(k) for k in data.keys() if k.isdigit())
                if not indices:
                    continue

                step_idx = step_info["step_idx"]
                step_key = str(step_idx)

                # Use last frame's V_t as the steady-state voltage for this Je
                last_group = data[str(indices[-1])]
                v_t = 0.0
                if "running_state" in last_group:
                    rs = last_group["running_state"]
                    if "mu" in rs and "dt" in rs:
                        rsmu = rs["mu"][...].reshape(-1)
                        rsdt = rs["dt"][...].reshape(-1)
                        k = len(rsdt)
                        if k > 0 and len(rsmu) >= 2 * k:
                            voltage = np.asarray(rsmu[:k]) - np.asarray(rsmu[k:2 * k])
                            dt_sum = float(np.sum(rsdt))
                            v_t = float(np.sum(voltage * rsdt) / dt_sum) if dt_sum > 0 else float(np.mean(voltage))

                je = step_info["je_end"]
                if je not in seen_je:
                    seen_je.append(je)
                    points.append({"i": je, "v": v_t})

                # Collect V(t) for all frames in this step
                if step_key not in vt_by_step:
                    vt_by_step[step_key] = []
                ramp_duration = step_info["ramp_end"] - step_info["ramp_start"]
                step_duration = step_info["stable_end"] - step_info["ramp_start"]

                for fi in indices:
                    grp = data[str(fi)]
                    fv = 0.0
                    if "running_state" in grp:
                        rs = grp["running_state"]
                        if "mu" in rs and "dt" in rs:
                            rsmu = rs["mu"][...].reshape(-1)
                            rsdt = rs["dt"][...].reshape(-1)
                            k = len(rsdt)
                            if k > 0 and len(rsmu) >= 2 * k:
                                voltage = np.asarray(rsmu[:k]) - np.asarray(rsmu[k:2 * k])
                                dt_sum = float(np.sum(rsdt))
                                fv = float(np.sum(voltage * rsdt) / dt_sum) if dt_sum > 0 else float(np.mean(voltage))

                    # Approximate time: spread frames evenly
                    n = len(indices)
                    frac = indices.index(fi) / max(n - 1, 1) if n > 1 else 0.0
                    abs_time = step_info["ramp_start"] + frac * step_duration
                    vt_by_step[step_key].append([abs_time, fv])

        except Exception:
            continue

    if not points:
        return None

    return {"points": points, "vt_by_step": vt_by_step}


from dflow.python import OP, OPIO, OPIOSign


class SidecarSyncOP(OP):
    """DFlow OP that syncs sidecar frames from Triton to MinIO.

    Runs as a K8s pod in parallel with the simulation step. Loops:
    rsync from Triton -> upload to MinIO -> check completion -> repeat.
    """

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({"run_id": str})

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({"status": str})

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
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
                return OPIO({"status": "timeout"})

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
                        return OPIO({"status": status})

            except Exception as e:
                print(f"sidecar-sync error (will retry): {e}")

            time.sleep(5)