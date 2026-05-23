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