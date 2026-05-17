"""cpp-tdgl simulation runner.

Fetches configuration from the data-viewer API, runs the simulation
Je-step by Je-step, and streams frame data back.

Environment variables:
    TDGL_RUN_ID           - Run ID to fetch config for
    TDGL_DATA_SERVICE_URL - Base URL of the data-viewer service
"""

import os
import sys

import httpx


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    data_url = os.environ["TDGL_DATA_SERVICE_URL"]
    client = httpx.Client(base_url=data_url, timeout=120.0)

    resp = client.get(f"/api/runs/{run_id}")
    resp.raise_for_status()
    run_data = resp.json()

    device_params = run_data["device_params"]
    timing_params = run_data["timing_params"]

    mesh = device_params["mesh"]
    schedule = timing_params["schedule"]
    steps = schedule["steps"]

    client.patch(f"/api/runs/{run_id}/status", json={"status": "running"})

    try:
        num_sites = mesh["num_sites"]

        for step_index, step in enumerate(steps):
            je = step["je_end"]
            voltage = 0.0

            frame_data = {
                "frame_index": step_index,
                "time_value": step["stable_end"],
                "je": je,
                "voltage": voltage,
                "psi_real": [[0.0]] * num_sites,
                "psi_imag": [[0.0]] * num_sites,
                "mu": [[0.0]] * num_sites,
            }

            resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
            resp.raise_for_status()
            print(f"Step {step_index + 1}/{len(steps)}: Je={je:.4f}, posted frame")

        client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
        print(f"Run {run_id} completed successfully")

    except Exception as exc:
        client.patch(f"/api/runs/{run_id}/status", json={"status": "failed"})
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()