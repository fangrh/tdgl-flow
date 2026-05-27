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


def _count_h5_frames(h5_path):
    """Count frame groups in an HDF5 file's /data."""
    try:
        with h5py.File(h5_path, "r") as f:
            if "data" in f:
                return sum(1 for k in f["data"].keys() if k.isdigit())
    except Exception:
        pass
    return 0


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

    print(f"Discrete run {args.run_id}: {total_steps} steps")

    # Write initial discrete_index.json with solve_time
    _update_discrete_index(
        run_dir, -1, {"je_start": 0, "je_end": 0, "ramp_start": 0, "ramp_end": 0, "stable_end": 0},
        "", total_steps, status="running",
    )
    idx_path = os.path.join(run_dir, "discrete_index.json")
    with open(idx_path) as f:
        idx = json.load(f)
    idx["solve_time"] = solve_time
    with open(idx_path, "w") as f:
        json.dump(idx, f)

    prev_solution = None

    for step_idx, step in enumerate(steps):
        h5_name = f"je_{step_idx:04d}.h5"
        output_path = os.path.join(run_dir, h5_name)
        step_duration = step["stable_end"] - step["ramp_start"]

        get_currents = _single_step_currents(step)
        options = tdgl.SolverOptions(
            solve_time=step_duration,
            dt_init=solver_options.get("dt_init", 1e-6),
            dt_max=solver_options.get("dt_max", 0.1),
            adaptive=solver_options.get("adaptive", True),
            save_every=solver_options.get("save_every", 100),
            output_file=output_path,
        )

        solve_kwargs = dict(
            device=device,
            options=options,
            terminal_currents=get_currents,
        )
        if prev_solution is not None:
            solve_kwargs["seed_solution"] = prev_solution
        if epsilon_fn is not None:
            solve_kwargs["disorder_epsilon"] = epsilon_fn

        print(f"  Step {step_idx}/{total_steps}: je {step['je_start']:.3f} -> {step['je_end']:.3f}, duration={step_duration:.1f}")
        try:
            solution = tdgl.solve(**solve_kwargs)
            prev_solution = solution

            is_last = step_idx == total_steps - 1
            _update_discrete_index(
                run_dir, step_idx, step, output_path, total_steps,
                status="completed" if is_last else "running",
            )
            print(f"    -> done, saved {h5_name}")
        except Exception as exc:
            _update_discrete_index(
                run_dir, step_idx, step, output_path, total_steps,
                status="failed",
            )
            print(f"  Step {step_idx} failed: {exc}", file=sys.stderr)
            raise

    print(f"Run {args.run_id} completed. {total_steps} discrete H5 files written.")


if __name__ == "__main__":
    main()
