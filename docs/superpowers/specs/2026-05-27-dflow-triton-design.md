# DFlow DispatcherExecutor + Sidecar Sync for Triton HPC

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the hand-written SSH/sbatch/rsync Triton integration with DFlow framework, using DispatcherExecutor for HPC job submission and a parallel sidecar-sync step for real-time viewer data.

**Architecture:** Two parallel DFlow Steps — Step 1 (DispatcherExecutor) handles SLURM job lifecycle, Step 2 (K8s pod) rsyncs sidecar frames to MinIO every 5 seconds for live viewing. slurm_runner.py on Triton is unchanged.

**Tech Stack:** DFlow (pydflow), DPDispatcher, Argo Workflows, MinIO, existing Rust viewer.

---

## Current State

The Triton integration uses a custom Argo WorkflowTemplate (`triton-tdgl-sim.yaml`) with a hand-written runner (`services/triton-runner/runner.py`) that:
- SSHs to Triton, uploads config files via scp
- Submits SLURM job via `sbatch`, polls `squeue` every 5s
- Rsyncs sidecar frames incrementally, uploads to MinIO
- Downloads final HDF5, uploads to MinIO

Pain points: SSH key management, fragile connectivity, ~350 lines of hand-maintained SSH/sbatch/rsync code, manual artifact handling.

## New Architecture

```
Notebook (DFlowTritonPipeline)
  |
  +-- DFlow Workflow (Python API -> Argo CRD)
  |    +-- Step 1: sim-step (DispatcherExecutor)
  |    |     -> dpdispatcher SSHs to Triton
  |    |     -> uploads input artifacts to remote_root
  |    |     -> generates sbatch script, submits job
  |    |     -> polls squeue until job completes
  |    |     -> downloads output.h5 as artifact
  |    |
  |    +-- Step 2: sidecar-sync (K8s pod, parallel with Step 1)
  |          -> rsync sidecar .npz from Triton every 5s
  |          -> upload .npz + viewer-index.json + iv.json to MinIO
  |          -> detects index.json status="completed" then exits
  |
  +-- slurm_runner.py (on Triton compute node, unchanged)
       -> tdgl.solve() + child process reads HDF5, writes sidecars
       -> writes index.json status="completed" when done
```

### Key Properties

- **No Argo Controller changes.** DispatcherExecutor is a DFlow-level concept (Python code in the step container calls dpdispatcher), not an Argo executor plugin.
- **Steps run in parallel.** No dependency between sim-step and sidecar-sync. The sync step starts immediately and polls until sidecar files appear, then continuously syncs.
- **slurm_runner.py unchanged.** The child-process HDF5 reader with `HDF5_USE_FILE_LOCKING=FALSE` continues to work as-is.
- **Artifact path alignment.** DispatcherExecutor's `remote_root` is set to `/scratch/work/fangr1/tdgl-runner/jobs/{run_id}`. Input artifacts are uploaded there. slurm_runner.py reads from `os.path.join(os.path.dirname(__file__), "jobs", run_id)` which resolves to the same path.

## Components

### 1. DFlowTritonPipeline (`src/tdgl_sdk/_dflow_pipeline.py`)

New SDK class that replaces `TritonSimulationPipeline`. Uses DFlow Python API to define the workflow:

```python
from dflow import Step, Workflow, upload_artifact, config, ShellOPTemplate
from dflow.plugins.dispatcher import DispatcherExecutor
from dflow.python import OP, OPIO, OPIOSign, Artifact, PythonOPTemplate

class DFlowTritonPipeline:
    def __init__(self, argo_url, minio_endpoint, sbatch_options=None, sidecar_interval=5):
        config["s3_endpoint"] = minio_endpoint.replace("http://", "")
        config["s3_access_key"] = "minioadmin"
        config["s3_secret_key"] = "minioadmin123"
        self.argo_url = argo_url
        self.sbatch_options = sbatch_options or {
            "partition": "batch-csl",
            "cpus-per-task": "4",
            "mem": "16G",
            "time": "04:00:00",
        }
        self.sidecar_interval = sidecar_interval

    def submit(self, device, timing_params, solver_options=None, epsilon_params=None):
        run_id = generate_run_id()

        # Upload device.pkl as DFlow artifact (large file)
        device_pkl = write_device_pkl(device, run_id)
        device_artifact = upload_artifact(device_pkl)

        # Upload config files as artifacts
        timing_artifact = upload_artifact(write_json("timing.json", timing_params))
        solver_artifact = upload_artifact(write_json("solver_options.json", solver_options or {}))
        eps_artifact = upload_artifact(write_json("epsilon_params.json", epsilon_params)) if epsilon_params else None

        executor = DispatcherExecutor(
            host="code.triton.aalto.fi",
            username="fangr1",
            rsa_key=os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa"),
            queue_name=self.sbatch_options.get("partition", "batch-csl"),
            remote_root=f"/scratch/work/fangr1/tdgl-runner/jobs/{run_id}",
        )

        sim_step = Step(
            name="simulate",
            template=ShellOPTemplate(
                "simulate",
                image="python:3.12-slim",  # placeholder, DispatcherExecutor runs on HPC
                script=(
                    "source /scratch/work/fangr1/miniforge3/etc/profile.d/conda.sh && "
                    "conda activate tdgl && "
                    "python /scratch/work/fangr1/tdgl-runner/slurm_runner.py "
                    f"{run_id} --sidecar-interval {self.sidecar_interval}"
                ),
            ),
            artifacts={"device": device_artifact, "timing": timing_artifact,
                       "solver": solver_artifact},
            executor=executor,
        )

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
        return run_id, wf.name
```

### 2. SidecarSyncOP and Shared Helpers (`src/tdgl_sdk/sidecar_sync.py`)

One file containing the DFlow OP class and the shared helper functions. The helpers are extracted from the existing `services/triton-runner/runner.py`.

**Helper functions (pure, no DFlow dependency):**

```python
def rsync_sidecars(remote_dir, local_dir, ssh_key, host):
    """Incremental rsync of sidecar .npz and index.json from Triton."""
    os.makedirs(local_dir, exist_ok=True)
    subprocess.run([
        "rsync", "-az", "--update", "--partial",
        "-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
        "--include=frame_*.npz", "--include=index.json", "--exclude=*",
        f"{host}:{remote_dir}/", f"{local_dir}/",
    ], timeout=120, check=False)

def build_viewer_index(local_dir, run_id):
    """Scan sidecar .npz files, return viewer-compatible index dict."""
    # Reads frame_*.npz -> extracts psi shape, time, V_t, I_t
    # Returns {"total_frames": N, "mesh_points": M, "frame_times": [...], ...}

def build_iv_data(local_dir):
    """Build I-V curve data from sidecar frames. Returns dict with points + vt_by_step."""

def upload_to_minio(local_path, bucket, key, endpoint):
    """Upload a single file to MinIO via boto3."""
```

**DFlow OP:**

```python
from dflow.python import OP, OPIO, OPIOSign, Artifact
from pathlib import Path

class SidecarSyncOP(OP):
    @classmethod
    def get_input_sign(cls):
        return OPIOSign({"run_id": str})

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({"status": str})

    def execute(self, op_in: OPIO) -> OPIO:
        run_id = op_in["run_id"]
        remote_dir = f"/scratch/work/fangr1/tdgl-runner/jobs/{run_id}/sidecars"
        local_dir = f"/tmp/triton-{run_id}/sidecars"
        ssh_key = os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa")
        host = os.environ.get("TRITON_HOST", "fangr1@code.triton.aalto.fi")
        bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")
        endpoint = os.environ.get("MINIO_ENDPOINT", "http://minio.tdgl.svc.cluster.local:9000")

        start_time = time.time()
        timeout = int(os.environ.get("SYNC_TIMEOUT", "14400"))  # 4 hours

        while True:
            if time.time() - start_time > timeout:
                return OPIO({"status": "timeout"})

            try:
                rsync_sidecars(remote_dir, local_dir, ssh_key, host)
                frames = [f for f in os.listdir(local_dir) if f.startswith("frame_") and f.endswith(".npz")]

                if frames:
                    # Upload all sidecar .npz to MinIO
                    for fname in sorted(frames):
                        local = os.path.join(local_dir, fname)
                        key = f"tdgl-runs/{run_id}/sidecars/{fname}"
                        if not minio_object_exists(endpoint, bucket, key):
                            upload_to_minio(local, bucket, key, endpoint)

                    # Rebuild and upload viewer-index.json
                    index = build_viewer_index(local_dir, run_id)
                    upload_json_to_minio(index, bucket, f"tdgl-runs/{run_id}/viewer-index.json", endpoint)

                    # Rebuild and upload iv.json
                    iv = build_iv_data(local_dir)
                    upload_json_to_minio(iv, bucket, f"tdgl-runs/{run_id}/iv.json", endpoint)

                # Check remote status
                index_path = os.path.join(local_dir, "index.json")
                if os.path.exists(index_path):
                    with open(index_path) as f:
                        status = json.load(f).get("status", "running")
                    if status in ("completed", "failed"):
                        return OPIO({"status": status})

            except Exception as e:
                print(f"sidecar-sync error (will retry): {e}")

            time.sleep(5)
```

### 3. Unchanged: slurm_runner.py (on Triton)

No changes needed. Continues to:
- Set `HDF5_USE_FILE_LOCKING=FALSE`
- Run `tdgl.solve()` in main process
- Run `_sidecar_subprocess()` (multiprocessing.Process) to read growing HDF5 and write sidecar frames
- Write `index.json` with status progression: "running" -> "completed"/"failed"

### 4. Unchanged: Rust Viewer

Continues to read MinIO (`tdgl-runs/{run_id}/viewer-index.json` + sidecar .npz) with `refresh_interval=5s`. The data format is identical.

## Infrastructure Requirements

| What | Change needed |
|------|---------------|
| Argo Controller | **None.** DFlow generates standard Argo CRDs. |
| MinIO | **None.** DFlow configured to use existing MinIO. |
| Triton HPC | **None.** slurm_runner.py + conda tdgl env already deployed. |
| K8s Secret (SSH key) | **Keep.** Both DispatcherExecutor and sidecar-sync need it. |
| triton-runner image | **Keep.** Used by sidecar-sync step (has SSH + rsync + boto3). |

### New Dependencies

| Package | Where | Purpose |
|---------|-------|---------|
| `pydflow` | SDK (pyproject.toml) | DFlow framework for Argo Workflows |
| `dpdispatcher` | SDK (pyproject.toml) | HPC job submission via SSH/SLURM |

## Files Changed

| File | Action |
|------|--------|
| `src/tdgl_sdk/_dflow_pipeline.py` | **Create** — DFlowTritonPipeline class |
| `src/tdgl_sdk/sidecar_sync.py` | **Create** — SidecarSyncOP + shared sync helpers |
| `src/tdgl_sdk/__init__.py` | **Modify** — export DFlowTritonPipeline |
| `pyproject.toml` | **Modify** — add pydflow, dpdispatcher dependencies |
| `notebooks/run_triton_sim.py` | **Modify** — use DFlowTritonPipeline |

### Files to Keep (not delete yet)

| File | Reason |
|------|--------|
| `services/triton-runner/runner.py` | Logic extracted to sidecar_sync.py, keep as fallback |
| `workflows/triton-tdgl-sim.yaml` | DFlow generates CRDs, this becomes unused but keep as fallback |
| `src/tdgl_sdk/_triton_pipeline.py` | Old pipeline, keep until DFlow version is validated |

## Error Handling

### SLURM job fails
- DispatcherExecutor detects non-zero exit code -> DFlow Step marks as Failed
- slurm_runner.py catches exception -> writes `index.json status="failed"`
- sidecar-sync reads status="failed" -> exits, Step 2 completes
- Notebook can check workflow status via DFlow API

### Sidecar-sync pod crashes
- Argo restarts pod via `retryStrategy`
- rsync `--update` is idempotent — no duplicate transfers on restart
- viewer-index.json rebuilt from scratch each round — no partial state

### SSH connection drops
- dpdispatcher has built-in SSH retry (connect timeout + retry count)
- rsync `--partial` enables resume of interrupted transfers
- sidecar-sync catches exceptions per round, doesn't exit on single failure

### Sidecar-sync timeout
- If SLURM job is stuck (e.g., PENDING too long), sidecar-sync has a configurable timeout (default: 4 hours)
- Timeout -> sidecar-sync exits -> Argo marks Step 2 as Failed

### Data consistency
- rsync `--update` + atomic npz writes -> viewer never reads partial files
- `_write_index()` overwrites atomically -> always reflects latest state
- dpdispatcher artifact transfer happens after job completion -> no intermediate state in final artifacts

## Data Flow Summary

```
Input:
  notebook -> DFlowTritonPipeline.submit()
    -> device.pkl uploaded to MinIO as DFlow artifact
    -> timing.json, solver_options.json, epsilon_params.json as artifacts
    -> DFlow Workflow submitted to Argo

Real-time (during simulation):
  Triton compute node:
    slurm_runner.py -> tdgl.solve() writes HDF5
    child process reads HDF5 (HDF5_USE_FILE_LOCKING=FALSE) -> writes sidecar .npz
  K8s sidecar-sync pod:
    rsync from Triton:/scratch/.../sidecars/ -> /tmp/sidecars/
    upload .npz to MinIO tdgl-runs/{run_id}/sidecars/
    rebuild viewer-index.json + iv.json -> upload to MinIO
  Viewer:
    TdglViewer reads MinIO -> refresh_interval=5s -> live display

Output:
  DispatcherExecutor downloads output.h5 -> DFlow artifact
  Final viewer-index.json with status="completed"
```
