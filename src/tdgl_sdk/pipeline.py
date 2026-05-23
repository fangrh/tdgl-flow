"""End-to-end TDGL simulation pipeline.

Coordinates: Argo Workflow submission -> MinIO download -> data verification -> viewer.
"""
import json
import time
import uuid
from datetime import datetime, timezone

import httpx

from tdgl_sdk.client import TDGLRunStore


class SimulationPipeline:
    """Coordinates Argo Workflows + MinIO + viewer for TDGL simulations."""

    def __init__(
        self,
        argo_url: str = "http://localhost:30080",
        minio_endpoint: str = "http://localhost:30900",
        minio_access_key: str = "minioadmin",
        minio_secret_key: str = "minioadmin123",
        minio_bucket: str = "tdgl-results",
        namespace: str = "tdgl",
    ):
        self.argo_url = argo_url
        self.namespace = namespace
        self.store = TDGLRunStore(
            endpoint_url=minio_endpoint,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            bucket=minio_bucket,
        )

    def submit(
        self,
        device_params: dict,
        timing_params: dict,
        solver_options: dict | None = None,
        cpu: str = "2",
        memory: str = "4Gi",
    ) -> tuple[str, str]:
        """Submit a py-tdgl-sim workflow. Returns (run_id, wf_name)."""
        from hera.workflows import Workflow, Parameter
        from hera.workflows.models import WorkflowTemplateRef as WTR

        run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-" + uuid.uuid4().hex[:6]
        )

        wf = Workflow(
            generate_name=f"py-tdgl-sim-{run_id[:13]}-",
            namespace=self.namespace,
            workflow_template_ref=WTR(name="py-tdgl-sim"),
            arguments=[
                Parameter(name="run-id", value=run_id),
                Parameter(name="device-params-json", value=json.dumps(device_params)),
                Parameter(name="timing-params-json", value=json.dumps(timing_params)),
                Parameter(name="solver-options-json", value=json.dumps(solver_options or {})),
                Parameter(name="cpu", value=cpu),
                Parameter(name="memory", value=memory),
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

    def poll(self, wf_name: str, timeout: int = 600) -> str:
        """Poll workflow until it completes. Returns final phase."""
        hint_map = {
            "Submitted": "Scheduling...",
            "Pending": "Pulling image...",
            "Running": "Running...",
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
                try:
                    logs_resp = httpx.get(
                        f"{self.argo_url}/api/v1/workflows/{self.namespace}/{wf_name}/log"
                        "?logOptions.container=main&logOptions.tailLines=30",
                        verify=False, timeout=10,
                    )
                    print(f"  Logs:\n{logs_resp.text[:3000]}")
                except Exception:
                    pass
                raise RuntimeError(f"Workflow {wf_name} {phase}")

            hint = hint_map.get(phase, "Processing...")
            print(f"  [{phase}] {hint} ({elapsed:.0f}s)")
            time.sleep(5)

    def download(self, run_id: str) -> str:
        """Download the HDF5 result for a run. Returns local file path."""
        h5_path = self.store.download_h5(run_id)
        if h5_path is None:
            raise FileNotFoundError(f"No HDF5 found for run {run_id}")
        return h5_path