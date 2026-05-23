import json
import tempfile
from pathlib import Path

import boto3
import h5py
import httpx


class TDGLClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def build_device(self, **params) -> dict:
        resp = httpx.post(f"{self.base_url}/api/device/build", json=params, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def build_timing(self, **params) -> dict:
        resp = httpx.post(f"{self.base_url}/api/timing/build", json=params, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def submit_simulation(self, *, device_params: dict, timing_params: dict,
                          mesh_data: dict, schedule: dict,
                          solver_options: dict | None = None,
                          resources: dict | None = None) -> dict:
        resp = httpx.post(f"{self.base_url}/api/workflows/submit", json={
            "device_params": device_params,
            "timing_params": timing_params,
            "mesh_data": mesh_data,
            "schedule": schedule,
            "solver_options": solver_options or {},
            "resources": resources or {"cpu_cores": 2, "memory_mib": 2048},
        }, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def get_run(self, run_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def list_runs(self) -> list[dict]:
        resp = httpx.get(f"{self.base_url}/api/runs", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_run_status(self, run_id: str) -> str:
        return self.get_run(run_id)["status"]

    def get_mesh(self, run_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}/mesh", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_frame(self, run_id: str, frame_index: int) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}/frames/{frame_index}", timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def preview_device(self, device_result: dict):
        import plotly.graph_objects as go
        import numpy as np

        sites = np.array(device_result["sites"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=sites[:, 0], y=sites[:, 1],
            mode="markers", marker=dict(size=3),
            name="Mesh sites",
        ))
        fig.update_layout(title=f"Device: {device_result['num_sites']} sites")
        return fig

    def preview_timing(self, timing_result: dict):
        import plotly.graph_objects as go

        steps = timing_result["steps"]
        jes = [s["je_end"] for s in steps]
        times = [s["stable_end"] for s in steps]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=times, y=jes,
            mode="lines+markers",
            name="Je sequence",
        ))
        fig.update_layout(
            title=f"Timing: {timing_result['n_steps']} steps",
            xaxis_title="Time (s)",
            yaxis_title="Je (uA)",
        )
        return fig

    def view_results(self, run_id: str):
        import plotly.graph_objects as go
        import numpy as np

        mesh = self.get_mesh(run_id)
        runs = self.list_runs()
        run = next(r for r in runs if r["run_id"] == run_id)
        total = run.get("total_frames", 0)

        frames = []
        for i in range(total):
            try:
                frames.append(self.get_frame(run_id, i))
            except Exception:
                break

        if not frames:
            print("No frames available yet.")
            return None

        sites = np.array(mesh["sites"])
        last = frames[-1]
        pr = np.array(last["arrays"]["psi_real"])
        pi = np.array(last["arrays"]["psi_imag"])
        mu = np.array(last["arrays"]["mu"])
        psq = pr**2 + pi**2

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=sites[:, 0], y=sites[:, 1],
            mode="markers",
            marker=dict(color=psq, colorscale="Viridis", size=5, showscale=True),
            name="|psi|^2",
        ))
        fig.update_layout(title=f"Run {run_id[:8]} - last frame |psi|^2")
        return fig


class TDGLRunStore:
    """Access TDGL simulation results stored in MinIO."""

    def __init__(
        self,
        endpoint_url: str = "http://localhost:30900",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin123",
        bucket: str = "tdgl-results",
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )

    def list_runs(self) -> list[dict]:
        """List all runs by scanning manifest.json objects."""
        paginator = self.s3.get_paginator("list_objects_v2")
        runs = []
        for page in paginator.paginate(
            Bucket=self.bucket, Prefix="tdgl-runs/", Suffix="manifest.json"
        ):
            for obj in page.get("Contents", []):
                resp = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])
                manifest = json.loads(resp["Body"].read())
                runs.append(manifest)
        return sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)

    def get_run(self, run_id: str) -> dict | None:
        """Get a single run's manifest. Returns None if not found."""
        from botocore.exceptions import ClientError
        try:
            resp = self.s3.get_object(
                Bucket=self.bucket, Key=f"tdgl-runs/{run_id}/manifest.json"
            )
            return json.loads(resp["Body"].read())
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def download_h5(self, run_id: str, local_path: str | None = None) -> str | None:
        """Download the HDF5 file for a run. Returns the local file path, or None if not found."""
        from botocore.exceptions import ClientError
        if local_path is None:
            local_path = str(
                Path(tempfile.gettempdir()) / f"tdgl-{run_id}.h5"
            )
        try:
            self.s3.download_file(
                self.bucket, f"tdgl-runs/{run_id}/output.h5", local_path
            )
            return local_path
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "Not Found"):
                return None
            raise

    def open_h5(self, run_id: str, cache_dir: str | None = None) -> h5py.File:
        """Download and open HDF5 file. Returns an h5py.File object."""
        local_path = self.download_h5(run_id, cache_dir)
        return h5py.File(local_path, "r")

    def get_run_status(self, run_id: str) -> str:
        manifest = self.get_run(run_id)
        return manifest.get("status", "unknown")

    def delete_run(self, run_id: str) -> None:
        """Delete all objects for a run."""
        prefix = f"tdgl-runs/{run_id}/"
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                self.s3.delete_object(Bucket=self.bucket, Key=obj["Key"])

    def h5_url(self, run_id: str) -> str:
        """Return the MinIO URL for a run's HDF5 file (for ROS3 direct read)."""
        return f"{self.endpoint_url}/{self.bucket}/tdgl-runs/{run_id}/output.h5"

    def open_viewer(self, run_id: str, live: bool = False):
        """Create a viewer that reads HDF5 directly from MinIO via ROS3.

        No local download needed. Requires h5py built with ROS3 support.
        """
        from tdgl_sdk.viewer._player import create_player
        return create_player(
            self.h5_url(run_id),
            live=live,
            s3_access_key=self.s3._request_signer._credentials.access_key,
            s3_secret_key=self.s3._request_signer._credentials.secret_key,
        )
