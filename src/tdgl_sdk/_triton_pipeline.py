"""TritonSimulationPipeline: submit TDGL simulations to Aalto Triton HPC.

Same interface as SimulationPipeline but uses triton-tdgl-sim WorkflowTemplate
which orchestrates a SLURM job on Triton with real-time sidecar sync.
"""
import base64
import json
import pickle
import time
import uuid
from datetime import datetime, timezone

import httpx

from tdgl_sdk.client import TDGLRunStore


class TritonSimulationPipeline:
    """Coordinates Argo Workflow + Triton SLURM + MinIO for TDGL simulations."""

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
        sidecar_interval: int = 500,
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
        self.store = TDGLRunStore(
            endpoint_url=minio_endpoint,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            bucket=minio_bucket,
        )

    def submit(
        self,
        device,
        timing_params: dict,
        solver_options: dict | None = None,
        epsilon_params: dict | None = None,
    ) -> tuple[str, str]:
        """Submit a triton-tdgl-sim workflow. Returns (run_id, wf_name).

        `device` is a tdgl.Device object (will be pickled and base64-encoded).
        """
        from hera.workflows import Workflow, Parameter
        from hera.workflows.models import WorkflowTemplateRef as WTR

        run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-" + uuid.uuid4().hex[:6]
        )

        device_pickle_b64 = base64.b64encode(
            pickle.dumps(device)
        ).decode()

        timing_json_b64 = base64.b64encode(
            json.dumps(timing_params).encode()
        ).decode()

        solver_b64 = base64.b64encode(
            json.dumps(solver_options or {}).encode()
        ).decode()

        eps_b64 = ""
        if epsilon_params:
            eps_b64 = base64.b64encode(
                json.dumps(epsilon_params).encode()
            ).decode()

        wf = Workflow(
            generate_name=f"triton-tdgl-{run_id[:13]}-",
            namespace=self.namespace,
            workflow_template_ref=WTR(name="triton-tdgl-sim"),
            arguments=[
                Parameter(name="run-id", value=run_id),
                Parameter(name="device-pickle-b64", value=device_pickle_b64),
                Parameter(name="timing-json-b64", value=timing_json_b64),
                Parameter(name="solver-options-b64", value=solver_b64),
                Parameter(name="epsilon-params-b64", value=eps_b64),
                Parameter(
                    name="sbatch-options",
                    value=json.dumps(self.sbatch_options),
                ),
                Parameter(
                    name="sidecar-interval",
                    value=str(self.sidecar_interval),
                ),
            ],
            workflows_service=self._argo_service(),
        )

        created = wf.create()
        wf_name = created.metadata.name
        return run_id, wf_name

    def _argo_service(self):
        from hera.workflows import WorkflowsService
        return WorkflowsService(
            host=self.argo_url, verify_ssl=False, namespace=self.namespace
        )

    def poll(self, wf_name: str, timeout: int = 3600) -> str:
        """Poll workflow until it completes. Returns final phase."""
        hint_map = {
            "Submitted": "Scheduling...",
            "Pending": "Pulling image...",
            "Running": "Running on Triton...",
        }
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise TimeoutError(
                    f"Workflow {wf_name} did not complete within {timeout}s"
                )
            url = f"{self.argo_url}/api/v1/workflows/{self.namespace}/{wf_name}"
            resp = httpx.get(url, verify=False, timeout=10)
            resp.raise_for_status()
            phase = (resp.json().get("status") or {}).get("phase", "Unknown")
            if phase == "Succeeded":
                return phase
            elif phase in {"Failed", "Error"}:
                raise RuntimeError(f"Workflow {wf_name} {phase}")
            hint = hint_map.get(phase, "Processing...")
            print(f"  [{phase}] {hint} ({elapsed:.0f}s)")
            time.sleep(5)

    def watch_live(
        self,
        run_id: str,
        poll_interval: int = 5,
        debug: bool = False,
    ):
        """Open live viewer for a running Triton simulation."""
        from tdgl_sdk.viewer._player import watch_run
        return watch_run(
            self.store, run_id,
            poll_interval=poll_interval,
            argo_host=self.argo_url,
            debug=debug,
        )
