# DFlow Triton Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hand-written SSH/sbatch/rsync Triton integration with DFlow framework using DispatcherExecutor for HPC job submission and a parallel sidecar-sync step for real-time viewer data.

**Architecture:** Two parallel DFlow Steps — Step 1 (DispatcherExecutor) handles SLURM job lifecycle on Triton, Step 2 (K8s pod) rsyncs sidecar frames to MinIO every 5 seconds for live viewing. slurm_runner.py on Triton is unchanged.

**Tech Stack:** DFlow (pydflow), DPDispatcher, Argo Workflows, MinIO, boto3, existing Rust viewer.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/tdgl_sdk/sidecar_sync.py` | **New** — SidecarSyncOP (DFlow OP class) + pure helper functions for rsync, viewer-index, iv-data, MinIO upload |
| `src/tdgl_sdk/_dflow_pipeline.py` | **New** — DFlowTritonPipeline class (replaces TritonSimulationPipeline) |
| `tests/test_sidecar_sync.py` | **New** — Tests for sidecar sync helper functions |
| `src/tdgl_sdk/__init__.py` | **Modify** — Add DFlowTritonPipeline export |
| `pyproject.toml` | **Modify** — Add pydflow, dpdispatcher dependencies |
| `notebooks/run_triton_sim.py` | **Modify** — Switch to DFlowTritonPipeline |

---

### Task 1: Add pydflow and dpdispatcher dependencies

**Files:**
- Modify: `pyproject.toml:10-21`

- [ ] **Step 1: Add dependencies to pyproject.toml**

Edit `pyproject.toml` to add `pydflow` and `dpdispatcher` to the `dependencies` list:

```toml
dependencies = [
  "boto3>=1.35",
  "dpdispatcher>=1.0",
  "hera-workflows>=5.17",
  "httpx>=0.27",
  "matplotlib>=3.9",
  "numpy>=1.26",
  "pillow>=10.0",
  "pydflow>=2.0",
  "scipy>=1.12",
  "tdgl>=0.8",
  "h5py>=3.10",
  "ipywidgets>=8.0",
]
```

- [ ] **Step 2: Install and verify imports**

Run:
```bash
pip install -e ".[dev]"
python -c "from dflow.plugins.dispatcher import DispatcherExecutor; print('OK')"
python -c "from dflow import Step, Workflow; print('OK')"
```
Expected: Both print "OK"

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pydflow and dpdispatcher dependencies"
```

---

### Task 2: Create sidecar sync helper functions

**Files:**
- Create: `src/tdgl_sdk/sidecar_sync.py`
- Create: `tests/test_sidecar_sync.py`

This task creates pure helper functions extracted from `services/triton-runner/runner.py`. No DFlow dependency yet — just testable functions.

- [ ] **Step 1: Write failing tests for sidecar sync helpers**

Create `tests/test_sidecar_sync.py`:

```python
"""Tests for sidecar sync helper functions."""
import json
import os
import tempfile

import numpy as np
import pytest

from tdgl_sdk.sidecar_sync import (
    build_iv_data,
    build_viewer_index,
    rsync_sidecars,
    upload_to_minio,
)


def _make_sidecar(path, psi_size=100, v_t=0.0, i_t=0.0, step=0, time_val=0.0):
    """Helper: write a single sidecar .npz file."""
    np.savez_compressed(
        path,
        psi=np.zeros(psi_size),
        mu=np.zeros(psi_size),
        V_t=np.float64(v_t),
        I_t=np.float64(i_t),
        step=np.int64(step),
        time=np.float64(time_val),
    )


class TestBuildViewerIndex:
    def test_empty_dir_returns_none(self, tmp_path):
        result = build_viewer_index(str(tmp_path))
        assert result is None

    def test_single_frame(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), psi_size=50)
        result = build_viewer_index(str(tmp_path))
        assert result is not None
        assert result["total_frames"] == 1
        assert result["mesh_points"] == 50
        assert result["status"] == "running"

    def test_multiple_frames(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), step=0, time_val=0.0)
        _make_sidecar(str(tmp_path / "frame_000001.npz"), step=100, time_val=10.0)
        _make_sidecar(str(tmp_path / "frame_000002.npz"), step=200, time_val=20.0)
        result = build_viewer_index(str(tmp_path))
        assert result["total_frames"] == 3
        assert len(result["frame_times"]) == 3
        assert result["frame_times"] == [0.0, 10.0, 20.0]

    def test_reads_index_json_status(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"))
        with open(tmp_path / "index.json", "w") as f:
            json.dump({"status": "completed", "completed_steps": 100, "total_steps": 100}, f)
        result = build_viewer_index(str(tmp_path))
        assert result["status"] == "completed"
        assert result["completed_steps"] == 100
        assert result["total_steps"] == 100


class TestBuildIvData:
    def test_empty_dir_returns_none(self, tmp_path):
        result = build_iv_data(str(tmp_path))
        assert result is None

    def test_single_frame(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), v_t=0.5, i_t=10.0, step=0, time_val=5.0)
        result = build_iv_data(str(tmp_path))
        assert result is not None
        assert len(result["points"]) == 1
        assert result["points"][0] == {"i": 10.0, "v": 0.5}
        assert "0" in result["vt_by_step"]
        assert result["vt_by_step"]["0"] == [[5.0, 0.5]]

    def test_dedup_same_current(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), v_t=0.1, i_t=10.0, step=0, time_val=5.0)
        _make_sidecar(str(tmp_path / "frame_000001.npz"), v_t=0.2, i_t=10.0, step=0, time_val=10.0)
        result = build_iv_data(str(tmp_path))
        # Same current -> only one point
        assert len(result["points"]) == 1
        # But two vt entries for that step
        assert len(result["vt_by_step"]["0"]) == 2

    def test_different_currents(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), v_t=0.1, i_t=5.0, step=0, time_val=5.0)
        _make_sidecar(str(tmp_path / "frame_000001.npz"), v_t=0.2, i_t=10.0, step=100, time_val=10.0)
        result = build_iv_data(str(tmp_path))
        assert len(result["points"]) == 2
        assert result["points"][0]["i"] == 5.0
        assert result["points"][1]["i"] == 10.0


class TestRsyncSidecars:
    def test_rsync_sidecars_is_callable(self):
        """rsync_sidecars should be importable and accept the right args."""
        import inspect
        sig = inspect.signature(rsync_sidecars)
        params = list(sig.parameters.keys())
        assert "remote_dir" in params
        assert "local_dir" in params
        assert "ssh_key" in params
        assert "host" in params


class TestUploadToMinio:
    def test_upload_to_minio_is_callable(self):
        """upload_to_minio should be importable and accept the right args."""
        import inspect
        sig = inspect.signature(upload_to_minio)
        params = list(sig.parameters.keys())
        assert "local_path" in params
        assert "bucket" in params
        assert "key" in params
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sidecar_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tdgl_sdk.sidecar_sync'`

- [ ] **Step 3: Implement sidecar_sync.py helper functions**

Create `src/tdgl_sdk/sidecar_sync.py` with the pure helper functions (no DFlow OP yet):

```python
"""Sidecar sync helpers for Triton HPC real-time data.

Pure functions for rsync, viewer-index building, I-V data extraction,
and MinIO upload. Used by both SidecarSyncOP and standalone scripts.
"""
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
    """Scan sidecar .npz files and return viewer-compatible index dict.

    Returns None if no frames found.
    """
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
    """Build I-V curve data from sidecar frames.

    Returns dict with 'points' (unique current-voltage pairs) and
    'vt_by_step' (voltage vs time per step). Returns None if no frames.
    """
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sidecar_sync.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_sdk/sidecar_sync.py tests/test_sidecar_sync.py
git commit -m "feat: add sidecar sync helper functions for DFlow Triton integration"
```

---

### Task 3: Create SidecarSyncOP DFlow operator

**Files:**
- Modify: `src/tdgl_sdk/sidecar_sync.py` (append SidecarSyncOP class)

This task adds the DFlow OP class that wraps the helper functions into a workflow step.

- [ ] **Step 1: Write failing test for SidecarSyncOP**

Append to `tests/test_sidecar_sync.py`:

```python
class TestSidecarSyncOP:
    def test_op_class_exists_and_has_required_methods(self):
        from tdgl_sdk.sidecar_sync import SidecarSyncOP
        assert hasattr(SidecarSyncOP, "get_input_sign")
        assert hasattr(SidecarSyncOP, "get_output_sign")
        assert hasattr(SidecarSyncOP, "execute")

    def test_input_sign_has_run_id(self):
        from tdgl_sdk.sidecar_sync import SidecarSyncOP
        sign = SidecarSyncOP.get_input_sign()
        assert "run_id" in sign

    def test_output_sign_has_status(self):
        from tdgl_sdk.sidecar_sync import SidecarSyncOP
        sign = SidecarSyncOP.get_output_sign()
        assert "status" in sign
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sidecar_sync.py::TestSidecarSyncOP -v`
Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Implement SidecarSyncOP**

Append to `src/tdgl_sdk/sidecar_sync.py`:

```python
import time


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sidecar_sync.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_sdk/sidecar_sync.py tests/test_sidecar_sync.py
git commit -m "feat: add SidecarSyncOP DFlow operator for real-time sidecar sync"
```

---

### Task 4: Create DFlowTritonPipeline

**Files:**
- Create: `src/tdgl_sdk/_dflow_pipeline.py`

This is the main SDK class that replaces `TritonSimulationPipeline`.

- [ ] **Step 1: Write failing test for DFlowTritonPipeline**

Create `tests/test_dflow_pipeline.py`:

```python
"""Tests for DFlowTritonPipeline."""
import pytest


def test_import_dflow_pipeline():
    from tdgl_sdk import DFlowTritonPipeline
    assert DFlowTritonPipeline is not None


def test_pipeline_init():
    from tdgl_sdk import DFlowTritonPipeline
    pipe = DFlowTritonPipeline(
        argo_url="http://localhost:30080",
        minio_endpoint="http://localhost:30900",
    )
    assert pipe.argo_url == "http://localhost:30080"
    assert pipe.sbatch_options is not None
    assert pipe.sbatch_options["partition"] == "batch-csl"
    assert pipe.sidecar_interval == 5


def test_pipeline_custom_sbatch():
    from tdgl_sdk import DFlowTritonPipeline
    pipe = DFlowTritonPipeline(
        argo_url="http://localhost:30080",
        minio_endpoint="http://localhost:30900",
        sbatch_options={"partition": "gpu", "cpus-per-task": "8"},
        sidecar_interval=10,
    )
    assert pipe.sbatch_options["partition"] == "gpu"
    assert pipe.sbatch_options["cpus-per-task"] == "8"
    assert pipe.sidecar_interval == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dflow_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'DFlowTritonPipeline'`

- [ ] **Step 3: Implement DFlowTritonPipeline**

Create `src/tdgl_sdk/_dflow_pipeline.py`:

```python
"""DFlowTritonPipeline: submit TDGL simulations to Triton HPC via DFlow.

Uses DFlow DispatcherExecutor for HPC job submission and a parallel
sidecar-sync step for real-time viewer data.
"""
import json
import os
import pickle
import tempfile
import uuid
from datetime import datetime, timezone

from dflow import Step, Workflow, config, upload_artifact
from dflow.plugins.dispatcher import DispatcherExecutor

from tdgl_sdk.sidecar_sync import SidecarSyncOP


class DFlowTritonPipeline:
    """Submit TDGL simulations to Triton HPC via DFlow + DispatcherExecutor."""

    def __init__(
        self,
        argo_url: str = "http://localhost:30080",
        minio_endpoint: str = "http://localhost:30900",
        minio_access_key: str = "minioadmin",
        minio_secret_key: str = "minioadmin123",
        minio_bucket: str = "tdgl-results",
        namespace: str = "tdgl",
        triton_host: str = "fangr1@code.triton.aalto.fi",
        triton_work_dir: str = "/scratch/work/fangr1/tdgl-runner",
        sbatch_options: dict | None = None,
        sidecar_interval: int = 5,
    ):
        self.argo_url = argo_url
        self.namespace = namespace
        self.triton_host = triton_host
        self.triton_work_dir = triton_work_dir
        self.sbatch_options = sbatch_options or {
            "partition": "batch-csl",
            "cpus-per-task": "4",
            "mem": "16G",
            "time": "04:00:00",
        }
        self.sidecar_interval = sidecar_interval
        self._minio_endpoint = minio_endpoint
        self._minio_bucket = minio_bucket

        config["s3_endpoint"] = minio_endpoint.replace("http://", "")
        config["s3_access_key"] = minio_access_key
        config["s3_secret_key"] = minio_secret_key

    def _generate_run_id(self) -> str:
        return (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-" + uuid.uuid4().hex[:6]
        )

    def _write_temp_json(self, data, filename, tmp_dir):
        path = os.path.join(tmp_dir, filename)
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def submit(
        self,
        device,
        timing_params: dict,
        solver_options: dict | None = None,
        epsilon_params: dict | None = None,
    ) -> tuple[str, str]:
        """Submit a DFlow workflow. Returns (run_id, wf_name)."""
        from dflow import ShellOPTemplate
        from dflow.python import PythonOPTemplate

        run_id = self._generate_run_id()
        tmp = tempfile.mkdtemp()

        # Write artifacts to temp dir
        device_path = os.path.join(tmp, "device.pkl")
        with open(device_path, "wb") as f:
            pickle.dump(device, f)

        timing_path = self._write_temp_json(timing_params, "timing.json", tmp)
        solver_path = self._write_temp_json(solver_options or {}, "solver_options.json", tmp)

        artifacts = {
            "device": upload_artifact(device_path),
            "timing": upload_artifact(timing_path),
            "solver": upload_artifact(solver_path),
        }

        if epsilon_params:
            eps_path = self._write_temp_json(epsilon_params, "epsilon_params.json", tmp)
            artifacts["epsilon"] = upload_artifact(eps_path)

        # Step 1: Simulation via DispatcherExecutor
        executor = DispatcherExecutor(
            host=self.triton_host.split("@")[-1],
            username=self.triton_host.split("@")[0],
            rsa_key=os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa"),
            queue_name=self.sbatch_options.get("partition", "batch-csl"),
            remote_root=f"{self.triton_work_dir}/jobs/{run_id}",
        )

        sim_step = Step(
            name="simulate",
            template=ShellOPTemplate(
                "simulate",
                image="python:3.12-slim",
                script=(
                    "source /scratch/work/fangr1/miniforge3/etc/profile.d/conda.sh && "
                    "conda activate tdgl && "
                    f"python {self.triton_work_dir}/slurm_runner.py "
                    f"{run_id} --sidecar-interval {self.sidecar_interval}"
                ),
            ),
            artifacts=artifacts,
            executor=executor,
        )

        # Step 2: Sidecar sync (K8s pod)
        sync_step = Step(
            name="sidecar-sync",
            template=PythonOPTemplate(
                SidecarSyncOP,
                image="172.22.133.208:30500/triton-runner:latest",
            ),
            parameters={"run_id": run_id},
        )

        wf = Workflow(name=f"triton-tdgl-{run_id}")
        wf.add([sim_step, sync_step])
        wf.submit()

        wf_name = wf.name if hasattr(wf, 'name') else f"triton-tdgl-{run_id}"
        return run_id, wf_name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dflow_pipeline.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_sdk/_dflow_pipeline.py tests/test_dflow_pipeline.py
git commit -m "feat: add DFlowTritonPipeline for DFlow-based Triton HPC submission"
```

---

### Task 5: Export DFlowTritonPipeline from SDK

**Files:**
- Modify: `src/tdgl_sdk/__init__.py`

- [ ] **Step 1: Add export**

Edit `src/tdgl_sdk/__init__.py` to add `DFlowTritonPipeline`:

```python
from tdgl_sdk.client import TDGLRunStore
from tdgl_sdk.pipeline import SimulationPipeline, verify_run
from tdgl_sdk._triton_pipeline import TritonSimulationPipeline
from tdgl_sdk._dflow_pipeline import DFlowTritonPipeline
from tdgl_sdk.viewer import create_player, create_player_2x2, debug_player, watch_run, examine_h5, format_report

__all__ = [
    "TDGLRunStore",
    "SimulationPipeline",
    "TritonSimulationPipeline",
    "DFlowTritonPipeline",
    "verify_run",
    "create_player",
    "create_player_2x2",
    "debug_player",
    "watch_run",
    "examine_h5",
    "format_report",
]
```

- [ ] **Step 2: Verify import**

Run: `python -c "from tdgl_sdk import DFlowTritonPipeline; print('OK')"`
Expected: prints "OK"

- [ ] **Step 3: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/tdgl_sdk/__init__.py
git commit -m "feat: export DFlowTritonPipeline from SDK"
```

---

### Task 6: Update notebook to use DFlowTritonPipeline

**Files:**
- Modify: `notebooks/run_triton_sim.py`

- [ ] **Step 1: Update notebook imports and pipeline usage**

Edit `notebooks/run_triton_sim.py` to replace `TritonSimulationPipeline` with `DFlowTritonPipeline`.

Replace the import line:
```python
from tdgl_sdk import TritonSimulationPipeline
```
with:
```python
from tdgl_sdk import DFlowTritonPipeline
```

Replace the pipeline instantiation:
```python
pipe = TritonSimulationPipeline(
    argo_url=ARGO_URL,
    minio_endpoint=MINIO_URL,
    sbatch_options=SBATCH_OPTIONS,
    sidecar_interval=500,
)
```
with:
```python
pipe = DFlowTritonPipeline(
    argo_url=ARGO_URL,
    minio_endpoint=MINIO_URL,
    sbatch_options=SBATCH_OPTIONS,
    sidecar_interval=5,
)
```

- [ ] **Step 2: Verify notebook syntax**

Run: `python -c "import ast; ast.parse(open('notebooks/run_triton_sim.py').read()); print('OK')"`
Expected: prints "OK"

- [ ] **Step 3: Commit**

```bash
git add notebooks/run_triton_sim.py
git commit -m "feat: switch notebook to DFlowTritonPipeline"
```

---

### Task 7: Integration test with sidecar sync triton-runner image

**Files:**
- Modify: `services/triton-runner/Dockerfile` (add `dflow` dependencies to image)
- Modify: `services/triton-runner/runner.py` (update to use shared sidecar_sync module)

The sidecar-sync step runs inside the `triton-runner` image. The image needs `dflow` installed so SidecarSyncOP can run. We also update `runner.py` to import from the shared module.

- [ ] **Step 1: Update Dockerfile**

Read `services/triton-runner/Dockerfile`. Update it to install the SDK package:

```dockerfile
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client rsync && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install SDK with dflow dependencies
COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
RUN pip install --no-cache-dir ".[dev]" || pip install --no-cache-dir /app

# Keep runner.py as fallback entrypoint
COPY services/triton-runner/runner.py /app/runner.py

CMD ["python", "/app/runner.py"]
```

- [ ] **Step 2: Rebuild and push image**

Run:
```bash
docker build -f services/triton-runner/Dockerfile -t 172.22.133.208:30500/triton-runner:latest .
docker push 172.22.133.208:30500/triton-runner:latest
```
Expected: Image builds and pushes successfully

- [ ] **Step 3: Commit**

```bash
git add services/triton-runner/Dockerfile
git commit -m "feat: update triton-runner image with dflow SDK for sidecar-sync"
```
