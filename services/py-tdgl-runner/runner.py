"""Python tdgl simulation runner (Argo simulate step).

Reads mesh_meta.json and timing.json from shared volume, runs the Python tdgl solver,
writes per-site data directly to Zarr via data-service API.
"""
import json
import os
import sys

import httpx
import numpy as np

DATA_DIR = os.environ.get("DATA_DIR", "/data")


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    data_url = os.environ["TDGL_DATA_SERVICE_URL"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)

    client = httpx.Client(base_url=data_url, timeout=120.0)

    # Read mesh meta
    with open(os.path.join(DATA_DIR, "mesh_meta.json")) as f:
        mesh_meta = json.load(f)

    # Read timing
    with open(os.path.join(DATA_DIR, "timing.json")) as f:
        timing_data = json.load(f)

    # Import tdgl here (installed in Docker image)
    import tdgl

    # Build sites and triangles arrays
    sites = np.array(mesh_meta["sites"], dtype=np.float64)
    triangles = np.array(mesh_meta["elements"], dtype=np.int64)

    # Build layer from mesh_meta
    layer = tdgl.Layer(
        coherence_length=mesh_meta["layer"]["coherence_length"],
        london_lambda=mesh_meta["layer"]["london_lambda"],
        thickness=mesh_meta["layer"]["thickness"],
        gamma=mesh_meta["layer"]["gamma"],
    )

    # Build device from mesh data
    device = tdgl.Device(
        name=mesh_meta["device_constants"]["name"],
        layer=layer,
        film=tdgl.Polygon("film", points=sites[triangles].reshape(-1, 2)),
        terminals=[
            tdgl.Polygon(t["name"], points=sites[t["site_indices"]].reshape(-1, 2))
            for t in mesh_meta["terminals"]
        ],
        probe_points=[sites[i] for i in mesh_meta["probe_indices"]],
    )

    # Re-apply mesh from the generated data
    device._points = sites
    device._triangles = triangles
    device.make_mesh(max_edge_length=mesh_meta["max_edge_length"], smooth=mesh_meta["smooth"])

    # Build timing steps
    steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])

    # Create sweep scenario
    times = [s["stable_end"] for s in steps]
    je_values = [s["je_end"] for s in steps]

    # Terminal currents for each time step
    terminal_currents_list = [
        {"source": je, "drain": -je} for je in je_values
    ]

    # Create sweep scenario
    scenario = tdgl.SweepScenario(
        times=times,
        terminal_currents=terminal_currents_list,
    )

    # Solver options
    options = tdgl.SolverOptions(
        solve_time=timing_data["solve_time"],
        dt=solver_options.get("dt", 1e-6),
        max_dt=solver_options.get("max_dt", 0.1),
        adaptive=solver_options.get("adaptive", True),
        save_every=solver_options.get("save_every", 1),
    )

    client.patch(f"/api/runs/{run_id}/status", json={"status": "running"})

    try:
        # Run solver
        solution = tdgl.solve(
            device,
            scenario,
            options,
            checkpoint_path=os.path.join(DATA_DIR, "checkpoint.zarr"),
        )

        probe_indices = mesh_meta["probe_indices"]

        # Post frame data
        for i, (time, psi, mu) in enumerate(zip(solution.times, solution.psi, solution.mu)):
            voltage = 0.0
            if len(probe_indices) >= 2:
                voltage = float(mu[probe_indices[1]] - mu[probe_indices[0]])

            frame_data = {
                "frame_index": i,
                "time_value": float(time),
                "je": je_values[i] if i < len(je_values) else 0.0,
                "voltage": voltage,
                "psi_real": psi.real.tolist(),
                "psi_imag": psi.imag.tolist(),
                "mu": mu.tolist(),
            }
            resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
            resp.raise_for_status()
            print(f"  Posted frame {i + 1}/{len(solution.times)}")

        client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
        print(f"Run {run_id} completed")

    except Exception as exc:
        client.patch(f"/api/runs/{run_id}/status", json={"status": "failed"})
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
