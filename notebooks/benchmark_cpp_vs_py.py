#%%
"""Local benchmark: cpp-tdgl vs py-tdgl with identical parameters.

Runs both solvers locally, measures wall time, and verifies cpp-tdgl's
step-by-step HDF5 output via --output-dir (SplitSolutionWriter).

Prerequisites:
    pip install tdgl h5py numpy scipy
    cd cpp-tdgl/build && cmake .. && make tdgl_solve
"""

#%%
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import json
import os
import subprocess
import tempfile
import time

import h5py
import numpy as np

import tdgl
from tdgl import SolverOptions, solve
from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.timing import build_timing

CPP_SOLVER = str(Path(__file__).resolve().parent.parent / "cpp-tdgl" / "build" / "tdgl_solve")

# ── Shared parameters (from e2e_sim_test.py) ───────────────────────────
DEVICE_PARAMS = {
    "film_width": 6.0,
    "film_height": 4.0,
    "elec_width": 0.2,
    "elec_height": 4.1,
    "elec_y_offset": 0.0,
    "probe_points": [(-1.0, 0.0), (1.0, 0.0)],
    "max_edge_length": 0.25,
    "smooth": 100,
}

TIMING_PARAMS = {
    "je_initial": 0.0,
    "je_final": 2.0,
    "je_step": 0.5,
    "ramp_time": 5.0,
    "stable_time": 10.0,
    "ramp_down": False,
}

SOLVER_OPTIONS = {
    "dt_init": 1e-4,
    "dt_max": 0.1,
    "save_every": 50,
}

# ── Derived quantities ──────────────────────────────────────────────────
timing_data = build_timing(**TIMING_PARAMS)
total_steps = timing_data["n_steps"]
solve_time = timing_data["solve_time"]
all_steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])

print(f"Timing: {total_steps} steps, solve_time={solve_time:.2f}s")
print(f"Solver: {SOLVER_OPTIONS}")

#%%
# ── Build shared device ────────────────────────────────────────────────
mesh_data, device = build_rectangular_device(**DEVICE_PARAMS)
print(f"Device: {len(device.points)} sites, {len(device.triangles)} elements")

#%%
# ── Run py-tdgl ────────────────────────────────────────────────────────
tmpdir = tempfile.mkdtemp(prefix="tdgl-bench-")
py_output = os.path.join(tmpdir, "py_output.h5")

# Build terminal currents function from timing
source = device.terminal_info()[0] if device.terminal_info() else None
drain = device.terminal_info()[1] if len(device.terminal_info()) > 1 else None

def terminal_currents_fn(t):
    """Step-function terminal currents matching the timing schedule."""
    for step in all_steps:
        if t < step["ramp_end"]:
            frac = (t - step["ramp_start"]) / max(step["ramp_end"] - step["ramp_start"], 1e-30)
            je = step["je_end"] * frac
            break
        if t < step["stable_end"]:
            je = step["je_end"]
            break
    else:
        je = all_steps[-1]["je_end"] if all_steps else 0.0

    result = {}
    if source:
        result[source.name] = je
    if drain:
        result[drain.name] = -je
    return result

options = SolverOptions(
    solve_time=solve_time,
    dt_init=SOLVER_OPTIONS["dt_init"],
    dt_max=SOLVER_OPTIONS["dt_max"],
    save_every=SOLVER_OPTIONS["save_every"],
    output_file=py_output,
)

print(f"Running py-tdgl: solve_time={solve_time}s, dt_max={SOLVER_OPTIONS['dt_max']}")
py_start = time.perf_counter()
result = tdgl.solve(
    device,
    options=options,
    terminal_currents=terminal_currents_fn,
)
py_elapsed = time.perf_counter() - py_start
py_frames = result.grid.shape[0] if hasattr(result, "grid") else 0
py_size = os.path.getsize(py_output) if os.path.exists(py_output) else 0

# Count frames from HDF5
if os.path.exists(py_output):
    with h5py.File(py_output, "r") as f:
        if "data" in f:
            py_frames = len([k for k in f["data"].keys() if k.isdigit()])
        elif "psi" in f:
            py_frames = f["psi"].shape[0]
        else:
            py_frames = 0

print(f"py-tdgl done: {py_elapsed:.2f}s, {py_frames} frames, {py_size/1024/1024:.1f} MB")

#%%
# ── Run cpp-tdgl ───────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "cpp-tdgl-runner"))
from convert_mesh import write_cpp_mesh

cpp_mesh_path = os.path.join(tmpdir, "cpp_mesh.h5")
cpp_output = os.path.join(tmpdir, "cpp_output.h5")
cpp_steps_dir = os.path.join(tmpdir, "cpp_steps")
timing_path = os.path.join(tmpdir, "timing.json")

# Write mesh
write_cpp_mesh(device, cpp_mesh_path, solver_options=SOLVER_OPTIONS)
print(f"cpp mesh: {os.path.getsize(cpp_mesh_path)/1024:.0f} KB")

# Write timing JSON
with open(timing_path, "w") as f:
    json.dump(timing_data, f)

# Run solver with --output-dir for step-by-step output
os.makedirs(cpp_steps_dir, exist_ok=True)
cmd = [
    CPP_SOLVER,
    "--mesh", cpp_mesh_path,
    "--output", cpp_output,
    "--timing", timing_path,
    "--output-dir", cpp_steps_dir,
    "--solver-options", json.dumps(SOLVER_OPTIONS),
]

print(f"Running cpp-tdgl: {' '.join(cmd[:4])} ...")
cpp_start = time.perf_counter()
result = subprocess.run(cmd, capture_output=True, text=True)
cpp_elapsed = time.perf_counter() - cpp_start

if result.returncode != 0:
    print(f"STDERR:\n{result.stderr[-2000:]}")
    print(f"STDOUT:\n{result.stdout[-2000:]}")
    raise RuntimeError(f"cpp-tdgl-solve exited with code {result.returncode}")

cpp_size = os.path.getsize(cpp_output) if os.path.exists(cpp_output) else 0

# Count frames from output (main file or step files)
cpp_frames = 0
if os.path.exists(cpp_output):
    with h5py.File(cpp_output, "r") as f:
        if "data" in f:
            cpp_frames = len([k for k in f["data"].keys() if k.isdigit()])

# Count step files (exclude mesh.h5 copy)
step_files = sorted([
    f for f in os.listdir(cpp_steps_dir)
    if f.startswith("step_") and f.endswith(".h5")
]) if os.path.isdir(cpp_steps_dir) else []

# If no frames in main output, count from step files
if cpp_frames == 0 and step_files:
    cpp_frames = len(step_files)
    # Sum up step file sizes
    cpp_size = sum(os.path.getsize(os.path.join(cpp_steps_dir, f)) for f in step_files)

# Check a step file for content
step_sample_info = ""
if step_files:
    sample = os.path.join(cpp_steps_dir, step_files[0])
    try:
        with h5py.File(sample, "r") as h:
            datasets = []
            def _visitor(name, obj):
                if isinstance(obj, h5py.Dataset):
                    datasets.append(f"{name}:{obj.shape}")
            h.visititems(_visitor)
            step_sample_info = f"{step_files[0]}: {', '.join(datasets[:5])}" if datasets else f"{step_files[0]}: (empty)"
    except Exception as e:
        step_sample_info = f"{step_files[0]}: error ({e})"

print(f"cpp-tdgl done: {cpp_elapsed:.2f}s, {cpp_frames} steps, {cpp_size/1024/1024:.1f} MB total")
print(f"Step files: {len(step_files)}")
if step_files:
    print(f"  {step_files[0]} .. {step_files[-1]}")
    if step_sample_info:
        print(f"  Sample: {step_sample_info}")

#%%
# ── Comparison table ───────────────────────────────────────────────────
print("=" * 70)
print(f"{'Metric':<25} {'py-tdgl':>15} {'cpp-tdgl':>15} {'Speedup':>10}")
print("-" * 70)
print(f"{'Wall time (s)':<25} {py_elapsed:>15.2f} {cpp_elapsed:>15.2f} {'':>10}")

if py_elapsed > 0 and cpp_elapsed > 0:
    speedup = py_elapsed / cpp_elapsed
    print(f"{'Steps/sec':<25} {total_steps/py_elapsed:>15.2f} {total_steps/cpp_elapsed:>15.2f} {speedup:>9.2f}x")

print(f"{'Total frames/steps':<25} {py_frames:>15d} {cpp_frames:>15d} {'':>10}")
print(f"{'Output size (MB)':<25} {py_size/1024/1024:>15.1f} {cpp_size/1024/1024:>15.1f} {'':>10}")
print(f"{'Step files (separate)':<25} {'N/A':>15} {len(step_files):>15d} {'':>10}")
print(f"{'Sites':<25} {len(device.points):>15d} {len(device.points):>15d} {'':>10}")
print("=" * 70)
print(f"\ncpp-tdgl is {py_elapsed/cpp_elapsed:.1f}x faster than py-tdgl")
print(f"cpp-tdgl saves {len(step_files)} separate step files via --output-dir")
