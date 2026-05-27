"""SLURM job runner for py-tdgl simulations on Triton.

Runs inside a SLURM compute job. Reads device.pkl + timing.json from
jobs/{run_id}/, runs the tdgl solver. A background thread monitors the
growing HDF5 file size for progress. After completion, extracts sidecar
frames from the final HDF5.

Usage:
    python slurm_runner.py <run_id> [--sidecar-interval 10]
"""
import argparse
import json
import os
import pickle
import sys
import threading
import time

import h5py
import numpy as np

import tdgl

sys.path.insert(0, os.path.dirname(__file__))
from epsilon import make_gaussian_epsilon


def _terminal_currents_from_steps(steps):
    def get_terminal_currents(t):
        for step in steps:
            if t < step["ramp_start"]:
                continue
            ramp_duration = step["ramp_end"] - step["ramp_start"]
            if ramp_duration > 0 and t <= step["ramp_end"]:
                frac = (t - step["ramp_start"]) / ramp_duration
                je = step["je_start"] + frac * (step["je_end"] - step["je_start"])
                return {"source": je, "drain": -je}
            if t <= step["stable_end"]:
                return {"source": step["je_end"], "drain": -step["je_end"]}
        if steps:
            je = steps[-1]["je_end"]
            return {"source": je, "drain": -je}
        return {"source": 0.0, "drain": 0.0}
    return get_terminal_currents


def _write_index(sidecar_dir, total_frames, completed_steps, total_steps, status):
    index = {
        "total_frames": total_frames,
        "completed_steps": completed_steps,
        "total_steps": total_steps,
        "status": status,
        "last_update": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(sidecar_dir, "index.json"), "w") as f:
        json.dump(index, f)


def _progress_poller(output_path, sidecar_dir, total_steps, stop_event, interval=10):
    """Background thread: monitor HDF5 file size for progress estimation."""
    last_size = 0
    while not stop_event.is_set():
        try:
            size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            if size > last_size:
                last_size = size
                estimated_steps = min(total_steps, int(total_steps * min(1.0, size / max(1, total_steps * 500))))
                _write_index(sidecar_dir, 0, estimated_steps, total_steps, "running")
        except OSError:
            pass
        stop_event.wait(interval)


def _extract_sidecars_final(output_path, sidecar_dir, steps):
    """Extract all frames from the completed HDF5 and write sidecars."""
    frame_count = 0
    with h5py.File(output_path, "r") as f:
        if "data" not in f:
            return 0
        data = f["data"]
        frame_indices = sorted(int(k) for k in data.keys() if k.isdigit())

        for fi in frame_indices:
            group = data[str(fi)]
            psi = np.array(group["psi"])
            mu = np.array(group["mu"]) if "mu" in group else np.zeros_like(psi, dtype=float)

            v_t = 0.0
            i_t = 0.0
            if "running_state" in group:
                rs = group["running_state"]
                if "mu" in rs and "dt" in rs:
                    rsmu = rs["mu"][...].reshape(-1)
                    rsdt = rs["dt"][...].reshape(-1)
                    k = len(rsdt)
                    if k > 0 and len(rsmu) >= 2 * k:
                        voltage = np.asarray(rsmu[:k]) - np.asarray(rsmu[k:2 * k])
                        dt_sum = float(np.sum(rsdt))
                        v_t = float(np.sum(voltage * rsdt) / dt_sum) if dt_sum > 0 else float(np.mean(voltage))

            time_val = 0.0
            for s in steps:
                if "ramp_start" in s:
                    t_start = float(s["ramp_start"])
                    t_end = float(s["stable_end"])
                    mid = (t_start + t_end) / 2
                    if time_val < mid:
                        i_t = float(s.get("je_end", 0.0))
                        break

            frame_path = os.path.join(sidecar_dir, f"frame_{frame_count:06d}.npz")
            np.savez_compressed(
                frame_path,
                psi=psi,
                mu=mu,
                V_t=np.float64(v_t),
                I_t=np.float64(i_t),
                step=np.int64(fi),
                time=np.float64(time_val),
            )
            frame_count += 1

    return frame_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--sidecar-interval", type=int, default=10)
    args = parser.parse_args()

    run_dir = os.path.join(os.path.dirname(__file__), "jobs", args.run_id)
    sidecar_dir = os.path.join(run_dir, "sidecars")
    os.makedirs(sidecar_dir, exist_ok=True)

    with open(os.path.join(run_dir, "device.pkl"), "rb") as f:
        device = pickle.load(f)
    with open(os.path.join(run_dir, "timing.json")) as f:
        timing_data = json.load(f)
    with open(os.path.join(run_dir, "solver_options.json")) as f:
        solver_options = json.load(f)

    epsilon_params_path = os.path.join(run_dir, "epsilon_params.json")
    epsilon_fn = None
    if os.path.exists(epsilon_params_path):
        with open(epsilon_params_path) as f:
            epsilon_params = json.load(f)
        if epsilon_params.get("type") == "gaussian":
            epsilon_fn = make_gaussian_epsilon(
                positions=epsilon_params["positions"],
                widths=epsilon_params["widths"],
                strengths=epsilon_params["strengths"],
            )

    steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])
    get_terminal_currents = _terminal_currents_from_steps(steps)
    total_steps = int(timing_data["solve_time"] / solver_options.get("dt_max", 0.1))

    output_path = os.path.join(run_dir, "output.h5")
    options = tdgl.SolverOptions(
        solve_time=timing_data["solve_time"],
        dt_init=solver_options.get("dt_init", 1e-6),
        dt_max=solver_options.get("dt_max", 0.1),
        adaptive=solver_options.get("adaptive", True),
        save_every=solver_options.get("save_every", 100),
        output_file=output_path,
    )

    _write_index(sidecar_dir, 0, 0, total_steps, "running")

    # Start progress monitor thread
    stop_event = threading.Event()
    poller = threading.Thread(
        target=_progress_poller,
        args=(output_path, sidecar_dir, total_steps, stop_event, args.sidecar_interval),
        daemon=True,
    )
    poller.start()

    try:
        solve_kwargs = dict(
            device=device,
            options=options,
            terminal_currents=get_terminal_currents,
        )
        if epsilon_fn is not None:
            solve_kwargs["disorder_epsilon"] = epsilon_fn
        print(f"Starting tdgl.solve() — solve_time={timing_data['solve_time']}, {total_steps} estimated steps")
        solution = tdgl.solve(**solve_kwargs)

        stop_event.set()
        poller.join(timeout=30)

        # Extract sidecars from final HDF5
        print("Extracting sidecar frames from completed HDF5...")
        frame_count = _extract_sidecars_final(output_path, sidecar_dir, steps)
        _write_index(sidecar_dir, frame_count, total_steps, total_steps, "completed")
        print(f"Run {args.run_id} completed. {frame_count} sidecar frames extracted.")
    except Exception as exc:
        stop_event.set()
        poller.join(timeout=10)
        _write_index(sidecar_dir, 0, 0, total_steps, "failed")
        print(f"Run {args.run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
