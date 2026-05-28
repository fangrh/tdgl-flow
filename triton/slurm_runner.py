"""SLURM job runner for py-tdgl simulations on Triton (discrete per-Je mode).

Runs inside a SLURM compute job. Reads device.pkl + timing.json from
jobs/{run_id}/, loops over timing steps running tdgl.solve() per step,
each saving to its own je_NNNN.h5 file. State is chained between steps
via seed_solution. A discrete_index.json is updated after each step.

Usage:
    python slurm_runner.py <run_id> [--sidecar-interval N]
"""
import argparse
import json
import os
import pickle
import sys
import time

import h5py

import tdgl

sys.path.insert(0, os.path.dirname(__file__))
from epsilon import make_gaussian_epsilon


def _single_step_currents(step):
    """Return a terminal_currents function for a single step.

    t is relative to the step start (0 = ramp_start).
    """
    ramp_duration = step["ramp_end"] - step["ramp_start"]
    je_start = step["je_start"]
    je_end = step["je_end"]

    def get_currents(t):
        if ramp_duration > 0 and t <= ramp_duration:
            frac = t / ramp_duration
            je = je_start + frac * (je_end - je_start)
        else:
            je = je_end
        return {"source": je, "drain": -je}

    return get_currents


def _terminal_currents_from_steps(steps):
    """Return terminal_currents(t) for the full timing schedule."""
    def get_currents(t):
        for step in steps:
            if t < step["ramp_start"]:
                continue
            ramp_duration = step["ramp_end"] - step["ramp_start"]
            if ramp_duration > 0 and t <= step["ramp_end"]:
                frac = (t - step["ramp_start"]) / ramp_duration
                je = step["je_start"] + frac * (step["je_end"] - step["je_start"])
                return {"source": je, "drain": -je}
            if t <= step["stable_end"]:
                je = step["je_end"]
                return {"source": je, "drain": -je}

        if steps:
            je = steps[-1]["je_end"]
            return {"source": je, "drain": -je}
        return {"source": 0.0, "drain": 0.0}

    return get_currents


def _step_metadata_for_time(steps, t):
    """Return schedule metadata for a solver time."""
    for idx, step in enumerate(steps):
        if t < step["ramp_start"]:
            continue
        ramp_duration = step["ramp_end"] - step["ramp_start"]
        if ramp_duration > 0 and t <= step["ramp_end"]:
            frac = (t - step["ramp_start"]) / ramp_duration
            je = step["je_start"] + frac * (step["je_end"] - step["je_start"])
        elif t <= step["stable_end"]:
            je = step["je_end"]
        else:
            continue
        return idx, step, je
    if steps:
        return len(steps) - 1, steps[-1], steps[-1]["je_end"]
    return -1, {}, 0.0


def _install_step_metadata_writer(steps):
    """Annotate py-tdgl HDF5 frames with schedule step metadata."""
    try:
        from tdgl.solver.runner import DataHandler
    except Exception:
        return

    if getattr(DataHandler.save_time_step, "_tdgl_step_metadata", False):
        return

    original = DataHandler.save_time_step

    def save_time_step_with_metadata(self, state, data, running_state):
        original(self, state, data, running_state)
        try:
            group = self.time_step_group[str(self.save_number - 1)]
            t = float(state.get("time", 0.0))
            step_idx, step, je = _step_metadata_for_time(steps, t)
            group.attrs["je_step_idx"] = int(step_idx)
            group.attrs["je_step_total"] = int(len(steps))
            group.attrs["je"] = float(je)
            if step:
                group.attrs["je_start"] = float(step["je_start"])
                group.attrs["je_end"] = float(step["je_end"])
                group.attrs["ramp_start"] = float(step["ramp_start"])
                group.attrs["ramp_end"] = float(step["ramp_end"])
                group.attrs["stable_end"] = float(step["stable_end"])
        except Exception:
            pass

    save_time_step_with_metadata._tdgl_step_metadata = True
    DataHandler.save_time_step = save_time_step_with_metadata


def _count_h5_frames(h5_path):
    """Count frame groups in an HDF5 file's /data."""
    try:
        with h5py.File(h5_path, "r") as f:
            if "data" in f:
                return sum(1 for k in f["data"].keys() if k.isdigit())
    except Exception:
        pass
    return 0


def _update_continuous_index(run_dir, total_steps, solve_time, status="running"):
    index = {
        "format": "continuous",
        "completed_steps": 0,
        "total_steps": total_steps,
        "status": status,
        "solve_time": solve_time,
        "last_update": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    output_path = os.path.join(run_dir, "output.h5")
    if os.path.exists(output_path):
        try:
            with h5py.File(output_path, "r") as h5:
                if "data" in h5:
                    step_indices = []
                    for key in h5["data"].keys():
                        if not key.isdigit():
                            continue
                        attrs = h5["data"][key].attrs
                        if "je_step_idx" in attrs:
                            step_indices.append(int(attrs["je_step_idx"]))
                    if step_indices:
                        index["completed_steps"] = max(step_indices) + 1
        except Exception:
            pass
    if status == "completed":
        index["completed_steps"] = total_steps
    with open(os.path.join(run_dir, "continuous_index.json"), "w") as f:
        json.dump(index, f)


def _update_discrete_index(run_dir, step_idx, step, h5_path, total_steps, status="running"):
    """Update discrete_index.json after a step completes."""
    index_path = os.path.join(run_dir, "discrete_index.json")

    if os.path.exists(index_path):
        with open(index_path) as f:
            index = json.load(f)
    else:
        index = {
            "format": "discrete",
            "steps": [],
            "completed_steps": 0,
            "total_steps": total_steps,
            "status": "running",
        }

    n_frames = _count_h5_frames(h5_path)

    step_entry = {
        "step_idx": step_idx,
        "h5_file": os.path.basename(h5_path),
        "je_start": step["je_start"],
        "je_end": step["je_end"],
        "ramp_start": step["ramp_start"],
        "ramp_end": step["ramp_end"],
        "stable_end": step["stable_end"],
        "n_frames": n_frames,
        "status": "completed",
    }

    found = False
    for i, s in enumerate(index["steps"]):
        if s["step_idx"] == step_idx:
            index["steps"][i] = step_entry
            found = True
            break
    if not found:
        index["steps"].append(step_entry)

    index["completed_steps"] = sum(
        1 for s in index["steps"] if s["status"] == "completed"
    )
    index["total_steps"] = total_steps
    index["status"] = status
    index["last_update"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with open(index_path, "w") as f:
        json.dump(index, f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--sidecar-interval", type=int, default=5,
                        help="Ignored (kept for CLI compat)")
    args = parser.parse_args()

    run_dir = os.path.join(os.path.dirname(__file__), "jobs", args.run_id)

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
    total_steps = len(steps)
    solve_time = steps[-1]["stable_end"] if steps else 0.0

    print(f"Continuous run {args.run_id}: {total_steps} scheduled current steps")
    _update_continuous_index(run_dir, total_steps, solve_time, status="running")
    _install_step_metadata_writer(steps)

    output_path = os.path.join(run_dir, "output.h5")
    options = tdgl.SolverOptions(
        solve_time=solve_time,
        dt_init=solver_options.get("dt_init", 1e-6),
        dt_max=solver_options.get("dt_max", 0.1),
        adaptive=solver_options.get("adaptive", True),
        save_every=solver_options.get("save_every", 100),
        output_file=output_path,
    )

    solve_kwargs = dict(
        device=device,
        options=options,
        terminal_currents=_terminal_currents_from_steps(steps),
    )
    if epsilon_fn is not None:
        solve_kwargs["disorder_epsilon"] = epsilon_fn

    try:
        tdgl.solve(**solve_kwargs)
        _update_continuous_index(run_dir, total_steps, solve_time, status="completed")
    except Exception as exc:
        _update_continuous_index(run_dir, total_steps, solve_time, status="failed")
        print(f"Run {args.run_id} failed: {exc}", file=sys.stderr)
        raise

    print(f"Run {args.run_id} completed. output.h5 written.")


if __name__ == "__main__":
    main()
