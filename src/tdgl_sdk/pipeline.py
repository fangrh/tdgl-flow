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

    def verify(self, h5_path: str, **s3_kwds) -> dict:
        """Run examine_h5 + debug_player on an HDF5 file. Returns combined report."""
        from tdgl_sdk.viewer.diagnostics import examine_h5, format_report
        from tdgl_sdk.viewer._player import debug_player

        examine_report = examine_h5(h5_path, **s3_kwds)
        debug_result = debug_player(h5_path, **s3_kwds)

        healthy = examine_report["healthy"] and debug_result["passed"]

        return {
            "healthy": healthy,
            "examine": examine_report,
            "examine_text": format_report(examine_report),
            "debug": debug_result,
            "summary": (
                f"Healthy: {healthy}, "
                f"Frames: {examine_report['frames']['total']}, "
                f"Issues: {examine_report['issues'] or 'none'}, "
                f"Player test: {'passed' if debug_result['passed'] else 'failed'} "
                f"({len(debug_result['errors'])} errors)"
            ),
        }

    def run(
        self,
        device_params: dict,
        timing_params: dict,
        solver_options: dict | None = None,
        poll_timeout: int = 600,
    ) -> dict:
        """Full pipeline: submit -> poll -> download -> verify.

        Returns dict with: run_id, wf_name, phase, h5_path, manifest, report.
        """
        print("Step 1: Submitting simulation workflow...")
        run_id, wf_name = self.submit(
            device_params=device_params,
            timing_params=timing_params,
            solver_options=solver_options,
        )
        print(f"  Run ID: {run_id}, Workflow: {wf_name}")

        print("Step 2: Polling workflow...")
        phase = self.poll(wf_name, timeout=poll_timeout)
        print(f"  Phase: {phase}")

        print("Step 3: Checking manifest...")
        manifest = self.store.get_run(run_id)
        if manifest is None:
            raise FileNotFoundError(f"No manifest for run {run_id}")

        print("Step 4: Reading HDF5 from MinIO via ROS3...")
        h5_url = self.store.h5_url(run_id)
        print(f"  URL: {h5_url}")

        print("Step 5: Verifying data integrity...")
        s3_kwds = {
            "s3_access_key": self.store.s3._request_signer._credentials.access_key,
            "s3_secret_key": self.store.s3._request_signer._credentials.secret_key,
        }
        report = self.verify(h5_url, **s3_kwds)
        print(f"  {report['summary']}")

        return {
            "run_id": run_id,
            "wf_name": wf_name,
            "phase": phase,
            "h5_path": h5_url,
            "manifest": manifest,
            "report": report,
        }

    def watch_live(
        self,
        run_id: str,
        poll_interval: int = 15,
        timing_params: dict | None = None,
        solver_options: dict | None = None,
        playback_dt: float = 1.0,
    ):
        """Create a streaming viewer for a running simulation.

        The viewer polls MinIO for new frames and auto-updates.
        Pass timing_params + solver_options to pre-allocate the progress bar.
        playback_dt: simulation time per animation step (default 1.0).
        Returns a StreamingTDGLPlayer (call .display_player() in Jupyter).
        """
        from tdgl_sdk.viewer._player import watch_run
        return watch_run(
            self.store, run_id,
            poll_interval=poll_interval,
            argo_host=self.argo_url,
            timing_params=timing_params,
            solver_options=solver_options,
            playback_dt=playback_dt,
        )


def verify_run(h5_path: str, **s3_kwds) -> dict:
    """Convenience function: examine + debug a single HDF5 file.

    Agents can call this without constructing a SimulationPipeline.
    Works with local paths or MinIO URLs (pass s3_access_key/s3_secret_key).
    Returns the same dict as SimulationPipeline.verify().
    """
    from tdgl_sdk.viewer.diagnostics import examine_h5, format_report
    from tdgl_sdk.viewer._player import debug_player

    examine_report = examine_h5(h5_path, **s3_kwds)
    debug_result = debug_player(h5_path, **s3_kwds)

    healthy = examine_report["healthy"] and debug_result["passed"]

    return {
        "healthy": healthy,
        "examine": examine_report,
        "examine_text": format_report(examine_report),
        "debug": debug_result,
        "summary": (
            f"Healthy: {healthy}, "
            f"Frames: {examine_report['frames']['total']}, "
            f"Issues: {examine_report['issues'] or 'none'}, "
            f"Player test: {'passed' if debug_result['passed'] else 'failed'} "
            f"({len(debug_result['errors'])} errors)"
        ),
    }