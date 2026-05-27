"""SLURM job runner for py-tdgl simulations on Triton.

Runs inside a SLURM compute job. Reads device.pkl + timing.json from
jobs/{run_id}/, runs the tdgl solver, writes sidecar frames to
jobs/{run_id}/sidecars/ every N steps, and produces the final HDF5.

Usage:
    python slurm_runner.py <run_id> [--sidecar-interval 500]
"""
import argparse
import json
import os
import pickle
import sys
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


class SidecarCallback:
    """tdgl solve callback that writes sidecar .npz files every N steps."""

    def __init__(self, sidecar_dir, interval, total_steps):
        self.sidecar_dir = sidecar_dir
        self.interval = interval
        self.total_steps = total_steps
        self.frame_count = 0
        self.steps_since_last = 0
        os.makedirs(sidecar_dir, exist_ok=True)

    def __call__(self, solution, step, time_val):
        self.steps_since_last += 1
        if self.steps_since_last < self.interval:
            return
        self.steps_since_last = 0
        self._write_frame(solution, step, time_val)

    def _write_frame(self, solution, step, time_val):
        psi = solution.psi
        mu = solution.mu if hasattr(solution, "mu") and solution.mu is not None else np.zeros_like(psi, dtype=float)

        v_t = 0.0
        i_t = 0.0
        if hasattr(solution, "terminal_currents") and solution.terminal_currents:
            i_t = solution.terminal_currents.get("source", 0.0)
        if hasattr(solution, "voltage") and solution.voltage is not None:
            v_t = float(solution.voltage)

        frame_path = os.path.join(
            self.sidecar_dir, f"frame_{self.frame_count:06d}.npz"
        )
        np.savez_compressed(
            frame_path,
            psi=psi,
            mu=mu,
            V_t=np.float64(v_t),
            I_t=np.float64(i_t),
            step=np.int64(step),
            time=np.float64(time_val),
        )
        self.frame_count += 1
        _write_index(
            self.sidecar_dir,
            self.frame_count,
            step,
            self.total_steps,
            "running",
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--sidecar-interval", type=int, default=500)
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

    sidecar_cb = SidecarCallback(sidecar_dir, args.sidecar_interval, total_steps)
    _write_index(sidecar_dir, 0, 0, total_steps, "running")

    try:
        solve_kwargs = dict(
            device=device,
            options=options,
            terminal_currents=get_terminal_currents,
        )
        if epsilon_fn is not None:
            solve_kwargs["disorder_epsilon"] = epsilon_fn
        solution = tdgl.solve(**solve_kwargs)

        _write_index(
            sidecar_dir,
            sidecar_cb.frame_count,
            total_steps,
            total_steps,
            "completed",
        )
        print(f"Run {args.run_id} completed. {sidecar_cb.frame_count} sidecar frames.")
    except Exception as exc:
        _write_index(
            sidecar_dir,
            sidecar_cb.frame_count,
            sidecar_cb.steps_since_last,
            total_steps,
            "failed",
        )
        print(f"Run {args.run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
