"""cpp-tdgl simulation runner (Argo simulate step).

Reads device.h5 and timing.json from shared volume, runs the C++ solver
step-by-step, writes per-site data directly to Zarr via data-service API.
"""
import json
import os
import subprocess
import sys
import tempfile

import h5py
import httpx
import numpy as np

SOLVER_PATH = os.environ.get("TDGL_SOLVER_PATH", "/app/tdgl_solve")
DATA_DIR = os.environ.get("DATA_DIR", "/data")


def build_solver_input(mesh_meta: dict, output_path: str, je: float,
                       solver_options: dict) -> None:
    """Copy device.h5 and add solver options group."""
    device_h5 = os.path.join(DATA_DIR, "device.h5")
    import shutil
    shutil.copy2(device_h5, output_path)

    with h5py.File(output_path, "a") as f:
        og = f.create_group("options")
        og.attrs["solve_time"] = solver_options.get("solve_time", 5.0)
        og.attrs["skip_time"] = 0.0
        og.attrs["dt_init"] = solver_options.get("dt", 1e-6)
        og.attrs["dt_max"] = solver_options.get("max_dt", 0.1)
        og.attrs["adaptive"] = solver_options.get("adaptive", True)
        og.attrs["save_every"] = 1
        og.attrs["terminal_psi"] = 1.0
        og.attrs["applied_field"] = 0.0
        og.attrs["current_units"] = "uA"
        og.attrs["field_units"] = "uT"
        og.attrs["include_screening"] = False
        og.attrs["max_iterations_per_step"] = 50
        og.attrs["screening_tolerance"] = 1e-5
        og.attrs["screening_step_size"] = 1.0
        og.attrs["screening_step_drag"] = 0.0
        og.attrs["adaptive_window"] = 200
        og.attrs["max_solve_retries"] = 4
        og.attrs["adaptive_time_step_multiplier"] = 0.5


def read_last_step(hdf5_path: str) -> dict | None:
    """Read the last saved step from the solver output HDF5."""
    with h5py.File(hdf5_path, "r") as f:
        data_grp = f.get("data")
        if not data_grp or not data_grp.keys():
            return None
        last_key = max(data_grp.keys(), key=lambda k: int(k))
        g = data_grp[last_key]
        result = {
            "step": int(g.attrs.get("step", 0)),
            "time": float(g.attrs.get("time", 0.0)),
            "dt": float(g.attrs.get("dt", 0.0)),
        }
        if "psi_real" in g:
            result["psi_real"] = np.array(g["psi_real"], dtype=np.float64)
        if "psi_imag" in g:
            result["psi_imag"] = np.array(g["psi_imag"], dtype=np.float64)
        if "mu" in g:
            result["mu"] = np.array(g["mu"], dtype=np.float64)
        return result


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    data_url = os.environ["TDGL_DATA_SERVICE_URL"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)

    client = httpx.Client(base_url=data_url, timeout=120.0)

    # Read timing from shared volume
    with open(os.path.join(DATA_DIR, "timing.json")) as f:
        timing_data = json.load(f)

    # Read mesh meta for probe indices
    with open(os.path.join(DATA_DIR, "mesh_meta.json")) as f:
        mesh_meta = json.load(f)

    probe_indices = mesh_meta["probe_indices"]

    steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])

    client.patch(f"/api/runs/{run_id}/status", json={"status": "running"})

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            restart_path = None

            for step_index, step in enumerate(steps):
                je = step["je_end"]

                input_hdf5 = os.path.join(tmpdir, f"input_{step_index}.h5")
                output_hdf5 = os.path.join(tmpdir, f"output_{step_index}.h5")

                build_solver_input(mesh_meta, input_hdf5, je, solver_options)

                cmd = [
                    SOLVER_PATH,
                    "--mesh", input_hdf5,
                    "--output", output_hdf5,
                    "--source-current", str(je),
                    "--drain-current", str(-je),
                ]
                if restart_path:
                    cmd.extend(["--restart", restart_path])

                print(f"Step {step_index + 1}/{len(steps)}: Je={je:.4f}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

                if result.returncode != 0:
                    print(f"Solver failed: {result.stderr[-500:]}", file=sys.stderr)
                    n_sites = len(mesh_meta["sites"])
                    frame_data = {
                        "frame_index": step_index,
                        "time_value": step["stable_end"],
                        "je": je,
                        "voltage": 0.0,
                        "psi_real": [0.0] * n_sites,
                        "psi_imag": [0.0] * n_sites,
                        "mu": [0.0] * n_sites,
                    }
                else:
                    last = read_last_step(output_hdf5)
                    voltage = 0.0
                    if last and "psi_real" in last:
                        psi_real = last["psi_real"].tolist()
                        psi_imag = last["psi_imag"].tolist()
                        mu = last["mu"].tolist()
                        if len(probe_indices) >= 2:
                            voltage = float(mu[probe_indices[1]] - mu[probe_indices[0]])
                    else:
                        psi_real = [0.0] * len(mesh_meta["sites"])
                        psi_imag = [0.0] * len(mesh_meta["sites"])
                        mu = [0.0] * len(mesh_meta["sites"])

                    frame_data = {
                        "frame_index": step_index,
                        "time_value": step["stable_end"],
                        "je": je,
                        "voltage": voltage,
                        "psi_real": psi_real,
                        "psi_imag": psi_imag,
                        "mu": mu,
                    }
                    restart_path = output_hdf5

                resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
                resp.raise_for_status()
                print(f"  Posted frame {step_index + 1}/{len(steps)}")

        client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
        print(f"Run {run_id} completed")

    except Exception as exc:
        client.patch(f"/api/runs/{run_id}/status", json={"status": "failed"})
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
