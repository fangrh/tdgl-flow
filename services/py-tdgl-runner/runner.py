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


class SaveWindowTimeline:
    def __init__(self) -> None:
        self.offset = 0.0

    def map_physical(self, *, save_start: float, physical_time: float) -> float:
        return self.offset + max(0.0, physical_time - save_start)

    def finish_window(self, *, save_time: float) -> None:
        self.offset += save_time


def _group_solution_indices_by_save_window(times: np.ndarray, steps: list[dict]) -> list[list[int]]:
    grouped = []
    for step in steps:
        indices = [
            int(i)
            for i, time_value in enumerate(times)
            if step["save_start"] <= float(time_value) <= step["save_end"]
        ]
        if not indices:
            raise RuntimeError(
                f"No saved frames found in save window [{step['save_start']}, {step['save_end']}]"
            )
        grouped.append(indices)
    return grouped


def _voltage_from_mu(mu: np.ndarray, probe_indices: list[int]) -> tuple[float, bool]:
    if len(probe_indices) < 2:
        return 0.0, False
    return float(mu[probe_indices[1]] - mu[probe_indices[0]]), True


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

        solution_times = np.asarray(solution.times, dtype=np.float64)
        grouped_indices = _group_solution_indices_by_save_window(solution_times, steps)
        timeline = SaveWindowTimeline()
        frame_index = 0

        for step_index, (step, indices) in enumerate(zip(steps, grouped_indices)):
            valid_voltages = []
            last_frame_time_value = None
            je = float(step["je_end"])

            for window_frame_index, solution_index in enumerate(indices):
                physical_time = float(solution_times[solution_index])
                psi = solution.psi[solution_index]
                mu = solution.mu[solution_index]
                voltage, voltage_valid = _voltage_from_mu(mu, probe_indices)
                if voltage_valid:
                    valid_voltages.append(voltage)

                time_value = timeline.map_physical(
                    save_start=step["save_start"],
                    physical_time=physical_time,
                )
                frame_data = {
                    "frame_index": frame_index,
                    "time_value": time_value,
                    "je": je,
                    "voltage": voltage,
                    "psi_real": psi.real.tolist(),
                    "psi_imag": psi.imag.tolist(),
                    "mu": mu.tolist(),
                    "frame_stats": {
                        "physical_time": physical_time,
                        "local_time": physical_time,
                        "save_window_index": step_index,
                        "window_frame_index": window_frame_index,
                        "save_start": step["save_start"],
                        "save_end": step["save_end"],
                        "voltage_valid": voltage_valid,
                        "solver_type": "py-tdgl",
                    },
                }
                resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
                resp.raise_for_status()
                last_frame_time_value = time_value
                frame_index += 1

            if valid_voltages and last_frame_time_value is not None:
                iv_resp = client.post(f"/api/runs/{run_id}/iv", json={
                    "frame_index": frame_index - 1,
                    "time_value": last_frame_time_value,
                    "je": je,
                    "voltage": float(np.mean(valid_voltages)),
                })
                iv_resp.raise_for_status()
            timeline.finish_window(save_time=step["save_end"] - step["save_start"])

        client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
        print(f"Run {run_id} completed")

    except Exception as exc:
        client.patch(f"/api/runs/{run_id}/status", json={"status": "failed"})
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
