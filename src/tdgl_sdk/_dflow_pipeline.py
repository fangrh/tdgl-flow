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
        self._minio_access_key = minio_access_key
        self._minio_secret_key = minio_secret_key
        self._minio_bucket = minio_bucket

        config["host"] = argo_url
        config["namespace"] = namespace

        # Configure DFlow's global S3 client for artifact storage.
        # Upload uses the external endpoint; Argo uses its own artifactRepository config.
        # Must use the same bucket as Argo's artifactRepository (argo-artifacts).
        from dflow.utils import s3_config
        s3_config["endpoint"] = minio_endpoint.replace("http://", "")
        s3_config["access_key"] = minio_access_key
        s3_config["secret_key"] = minio_secret_key
        s3_config["bucket_name"] = "argo-artifacts"
        s3_config["secure"] = False
        # Reset cached client so new config takes effect
        s3_config["storage_client"] = None

    def _generate_run_id(self) -> str:
        return (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-" + uuid.uuid4().hex[:6]
        )

    def submit(
        self,
        device,
        timing_params: dict,
        solver_options: dict | None = None,
        epsilon_params: dict | None = None,
    ) -> tuple[str, str]:
        """Submit a DFlow workflow. Returns (run_id, wf_name)."""
        from dflow import ShellOPTemplate, Inputs, Outputs, InputParameter, InputArtifact, OutputArtifact
        from dflow.python import PythonOPTemplate

        run_id = self._generate_run_id()
        tmp = tempfile.mkdtemp()

        device_path = os.path.join(tmp, "device.pkl")
        with open(device_path, "wb") as f:
            pickle.dump(device, f)

        timing_path = os.path.join(tmp, "timing.json")
        with open(timing_path, "w") as f:
            json.dump(timing_params, f)

        solver_path = os.path.join(tmp, "solver_options.json")
        with open(solver_path, "w") as f:
            json.dump(solver_options or {}, f)

        artifacts = {
            "device": upload_artifact(device_path),
            "timing": upload_artifact(timing_path),
            "solver": upload_artifact(solver_path),
        }

        if epsilon_params:
            eps_path = os.path.join(tmp, "epsilon_params.json")
            with open(eps_path, "w") as f:
                json.dump(epsilon_params, f)
            artifacts["epsilon"] = upload_artifact(eps_path)

        host_parts = self.triton_host.split("@")
        username = host_parts[0]
        hostname = host_parts[-1]

        executor = DispatcherExecutor(
            host=hostname,
            username=username,
            port=22,
            private_key_file=os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa"),
            queue_name=self.sbatch_options.get("partition", "batch-csl"),
            remote_root=f"{self.triton_work_dir}/jobs/{run_id}",
        )

        sim_step = Step(
            name="simulate",
            template=ShellOPTemplate(
                "simulate",
                inputs=Inputs(
                    artifacts={
                        "device": InputArtifact(path="/tmp/device.pkl"),
                        "timing": InputArtifact(path="/tmp/timing.json"),
                        "solver": InputArtifact(path="/tmp/solver_options.json"),
                    },
                ),
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

        wf_name = wf.name if hasattr(wf, "name") else f"triton-tdgl-{run_id}"
        return run_id, wf_name