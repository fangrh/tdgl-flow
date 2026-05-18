"""Base service entry point.

Adapt: add your service logic, env vars, startup behavior.
"""
import os

import httpx

DATA_SERVICE_URL = os.environ.get("TDGL_DATA_SERVICE_URL", "http://data-viewer.tdgl.svc.cluster.local")
PORT = int(os.environ.get("PORT", "8080"))


def main() -> None:
    print(f"Service starting, data service at {DATA_SERVICE_URL}")
    # Add your service logic here:
    # - FastAPI/Flask app for HTTP services
    # - Polling loop for background workers
    # - One-shot script for batch jobs


if __name__ == "__main__":
    main()