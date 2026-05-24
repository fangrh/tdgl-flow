import json
import tempfile
from pathlib import Path

import boto3
import h5py


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
        for page in paginator.paginate(Bucket=self.bucket, Prefix="tdgl-runs/"):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith("/manifest.json"):
                    continue
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
        else:
            path = Path(local_path)
            if path.exists() and path.is_dir():
                local_path = str(path / f"tdgl-{run_id}.h5")
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
        if local_path is None:
            raise FileNotFoundError(f"No HDF5 output found for run {run_id!r}")
        return h5py.File(local_path, "r")

    def get_run_status(self, run_id: str) -> str:
        manifest = self.get_run(run_id)
        if manifest is None:
            return "unknown"
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
