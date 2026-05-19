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

SOLVER_PATH = os.environ.get("TDGL_SOLVER_PATH", "/usr/local/bin/tdgl_solve")
DATA_DIR = os.environ.get("DATA_DIR", "/data")


def build_solver(source_dir: str, install_prefix: str = "/usr/local") -> str:
    """Build the C++ solver from mounted source. Returns path to binary."""
    build_dir = os.path.join(source_dir, "build")
    os.makedirs(build_dir, exist_ok=True)

    cmake_cmd = [
        "cmake", source_dir,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_TESTING=OFF",
        "-DBUILD_BENCHMARKS=OFF",
        f"-DCMAKE_INSTALL_PREFIX={install_prefix}",
    ]
    print(f"Configuring solver: {' '.join(cmake_cmd)}")
    subprocess.run(cmake_cmd, cwd=build_dir, check=True, capture_output=True, text=True)

    build_cmd = ["cmake", "--build", ".", "-j4"]
    print("Building solver from mounted source...")
    result = subprocess.run(build_cmd, cwd=build_dir, check=True, capture_output=True, text=True)
    print(f"Build output: {result.stdout[-200:]}")

    install_cmd = ["cmake", "--install", "."]
    subprocess.run(install_cmd, cwd=build_dir, check=True, capture_output=True, text=True)

    solver_path = os.path.join(install_prefix, "bin", "tdgl_solve")
    if not os.path.isfile(solver_path):
        raise FileNotFoundError(f"Solver not found at {solver_path} after build")
    print(f"Solver built and installed to: {solver_path}")
    return solver_path


def build_solver_input(mesh_meta: dict, output_path: str, je: float,
                       step: dict, solver_options: dict) -> None:
    """Copy device.h5 and add solver options group."""
    device_h5 = os.path.join(DATA_DIR, "device.h5")
    import shutil
    shutil.copy2(device_h5, output_path)

    step_duration = step["stable_end"] - step["ramp_start"]
    # Save window relative to step start (solver time is reset to 0)
    save_start_rel = step["save_start"] - step["ramp_start"]
    save_end_rel = step["save_end"] - step["ramp_start"]

    with h5py.File(output_path, "a") as f:
        og = f.create_group("options")
        og.attrs["solve_time"] = step_duration
        og.attrs["skip_time"] = 0.0
        og.attrs["current_ramp_time"] = step["ramp_end"] - step["ramp_start"]
        og.attrs["dt_init"] = solver_options.get("dt", 1e-6)
        og.attrs["dt_max"] = solver_options.get("max_dt", 0.1)
        og.attrs["adaptive"] = solver_options.get("adaptive", True)
        og.attrs["save_every"] = 10
        og.attrs["save_start"] = save_start_rel
        og.attrs["save_end"] = save_end_rel
        og.attrs["terminal_psi"] = 0.0
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


def _read_save_window(hdf5_path: str, save_start_rel: float, save_end_rel: float,
                      max_samples: int = 100) -> dict | None:
    """Read and average all saved steps within the save window.

    Returns averaged psi_real, psi_imag, mu over the window, or falls back
    to the last step if no steps fall within the window.
    """
    with h5py.File(hdf5_path, "r") as f:
        data_grp = f.get("data")
        if not data_grp or not data_grp.keys():
            return None

        # Collect (key, time) pairs within the save window
        in_window = []
        all_keys = []
        for key in data_grp.keys():
            try:
                int(key)
            except ValueError:
                continue
            g = data_grp[key]
            t = float(g.attrs.get("time", 0.0))
            all_keys.append((key, t))
            if save_start_rel <= t <= save_end_rel:
                in_window.append(key)

        # Fall back to last step if no steps in window
        if not in_window:
            if not all_keys:
                return None
            last_key = max(all_keys, key=lambda x: x[1])[0]
            in_window = [last_key]

        # Subsample if too many steps in the window
        if len(in_window) > max_samples:
            indices = np.linspace(0, len(in_window) - 1, max_samples, dtype=int)
            in_window = [in_window[i] for i in indices]

        # Average over the window
        n = len(in_window)
        psi_real_acc = None
        psi_imag_acc = None
        mu_acc = None
        time_acc = 0.0

        for key in in_window:
            g = data_grp[key]
            pr = np.array(g["psi_real"], dtype=np.float64) if "psi_real" in g else None
            pi_ = np.array(g["psi_imag"], dtype=np.float64) if "psi_imag" in g else None
            mu_ = np.array(g["mu"], dtype=np.float64) if "mu" in g else None
            if pr is None or pi_ is None or mu_ is None:
                continue
            if psi_real_acc is None:
                psi_real_acc = pr
                psi_imag_acc = pi_
                mu_acc = mu_
            else:
                psi_real_acc += pr
                psi_imag_acc += pi_
                mu_acc += mu_
            time_acc += float(g.attrs.get("time", 0.0))

        if psi_real_acc is None:
            return None

        return {
            "n_samples": n,
            "time_avg": time_acc / n,
            "psi_real": psi_real_acc / n,
            "psi_imag": psi_imag_acc / n,
            "mu": mu_acc / n,
        }


class SaveWindowTimeline:
    """Maps per-step local times onto a concatenated global timeline."""

    def __init__(self) -> None:
        self.offset = 0.0

    def map_frame(self, *, save_start_rel: float, local_time: float) -> float:
        return self.offset + max(0.0, local_time - save_start_rel)

    def finish_window(self, *, save_time: float) -> None:
        self.offset += save_time


def _read_save_window_frames(hdf5_path: str, save_start_rel: float, save_end_rel: float) -> list[dict]:
    """Return every saved frame inside the save window as a list of dicts."""
    with h5py.File(hdf5_path, "r") as f:
        data_grp = f.get("data")
        if not data_grp:
            raise RuntimeError(f"No solver data group found in {hdf5_path}")

        frames = []
        for key in data_grp.keys():
            try:
                int(key)
            except ValueError:
                continue
            g = data_grp[key]
            local_time = float(g.attrs.get("time", 0.0))
            if save_start_rel <= local_time <= save_end_rel:
                frames.append({
                    "local_time": local_time,
                    "psi_real": np.array(g["psi_real"], dtype=np.float64),
                    "psi_imag": np.array(g["psi_imag"], dtype=np.float64),
                    "mu": np.array(g["mu"], dtype=np.float64),
                })

        frames.sort(key=lambda item: item["local_time"])
        if not frames:
            raise RuntimeError(
                f"No saved frames found in save window [{save_start_rel}, {save_end_rel}] for {hdf5_path}"
            )
        return frames


def _voltage_from_mu(mu: np.ndarray, probe_indices: list[int]) -> tuple[float, bool]:
    """Compute voltage from mu array at probe indices. Returns (voltage, valid)."""
    if len(probe_indices) < 2:
        return 0.0, False
    return float(mu[probe_indices[1]] - mu[probe_indices[0]]), True


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    data_url = os.environ["TDGL_DATA_SERVICE_URL"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)

    # Dev mode: build solver from mounted source
    dev_mode = os.environ.get("DEV_MODE", "false").lower() == "true"
    if dev_mode:
        source_dir = "/src/cpp-tdgl"
        if os.path.isdir(source_dir):
            solver_path = build_solver(source_dir)
        else:
            print(f"DEV_MODE enabled but {source_dir} not found, using pre-built solver")
            solver_path = SOLVER_PATH
    else:
        solver_path = SOLVER_PATH

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
            frame_index = 0
            timeline = SaveWindowTimeline()

            for step_index, step in enumerate(steps):
                je = step["je_end"]

                input_hdf5 = os.path.join(tmpdir, f"input_{step_index}.h5")
                output_hdf5 = os.path.join(tmpdir, f"output_{step_index}.h5")

                build_solver_input(mesh_meta, input_hdf5, je, step, solver_options)

                cmd = [
                    solver_path,
                    "--mesh", input_hdf5,
                    "--output", output_hdf5,
                    "--source-current", str(je),
                    "--drain-current", str(-je),
                    "--source-current-from", str(step["je_start"]),
                    "--drain-current-from", str(-step["je_start"]),
                ]
                if restart_path:
                    cmd.extend(["--restart", restart_path])

                print(f"Step {step_index + 1}/{len(steps)}: Je={je:.4f}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

                if result.returncode != 0:
                    print(f"Solver failed: {result.stderr[-500:]}", file=sys.stderr)
                    n_sites = len(mesh_meta["sites"])
                    frame_data = {
                        "frame_index": frame_index,
                        "time_value": step["stable_end"],
                        "je": je,
                        "voltage": 0.0,
                        "psi_real": [0.0] * n_sites,
                        "psi_imag": [0.0] * n_sites,
                        "mu": [0.0] * n_sites,
                    }
                    resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
                    resp.raise_for_status()
                    print(f"  Posted error frame {frame_index}")
                    frame_index += 1
                else:
                    save_start_rel = step["save_start"] - step["ramp_start"]
                    save_end_rel = step["save_end"] - step["ramp_start"]
                    save_frames = _read_save_window_frames(output_hdf5, save_start_rel, save_end_rel)

                    window_voltages: list[float] = []
                    for win_idx, sf in enumerate(save_frames):
                        voltage, voltage_valid = _voltage_from_mu(sf["mu"], probe_indices)
                        if voltage_valid:
                            window_voltages.append(voltage)

                        physical_time = timeline.map_frame(
                            save_start_rel=save_start_rel,
                            local_time=sf["local_time"],
                        )
                        frame_data = {
                            "frame_index": frame_index,
                            "time_value": physical_time,
                            "je": je,
                            "voltage": voltage,
                            "psi_real": sf["psi_real"].tolist(),
                            "psi_imag": sf["psi_imag"].tolist(),
                            "mu": sf["mu"].tolist(),
                        }
                        frame_stats = {
                            "physical_time": physical_time,
                            "local_time": sf["local_time"],
                            "save_window_index": step_index,
                            "window_frame_index": win_idx,
                            "save_start": step["save_start"],
                            "save_end": step["save_end"],
                            "voltage_valid": voltage_valid,
                            "solver_type": "cpp-tdgl",
                        }
                        resp = client.post(
                            f"/api/runs/{run_id}/frames",
                            json={**frame_data, "stats": frame_stats},
                        )
                        resp.raise_for_status()
                        frame_index += 1

                    # Post averaged IV point if voltages were computed
                    if window_voltages:
                        avg_voltage = float(np.mean(window_voltages))
                        save_time = save_end_rel - save_start_rel
                        iv_point = {
                            "je": je,
                            "voltage": avg_voltage,
                            "save_time": save_time,
                            "n_frames": len(save_frames),
                        }
                        resp = client.post(f"/api/runs/{run_id}/iv", json=iv_point)
                        resp.raise_for_status()
                        print(f"  IV point: Je={je:.4f}, V_avg={avg_voltage:.6f}")

                    save_time = save_end_rel - save_start_rel
                    timeline.finish_window(save_time=save_time)
                    restart_path = output_hdf5
                    print(f"  Posted {len(save_frames)} frames for step {step_index + 1}/{len(steps)}")

        client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
        print(f"Run {run_id} completed")

    except Exception as exc:
        client.patch(f"/api/runs/{run_id}/status", json={"status": "failed"})
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
