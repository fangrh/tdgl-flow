# Local cpp-tdgl vs py-tdgl Benchmark

**Goal:** Run both solvers locally with identical parameters, compare speed, and verify cpp-tdgl's step-by-step HDF5 output works.

**Output:** `notebooks/benchmark_cpp_vs_py.py` — 6 cells, run in VS Code Interactive.

## Parameters

Match `e2e_sim_test.py`:
- Device: 6×4 film, elec 0.2×4.1, probe_points=[[-1,0],[1,0]], max_edge_length=0.25, smooth=100
- Timing: Je 0→20, step=0.2, ramp_time=100, stable_time=200 (100 steps)
- Solver: dt_init=1e-4, dt_max=0.1, save_every=50

## Cells

### Cell 1: Imports + config
- Import tdgl, subprocess, time, tempfile, h5py, numpy
- Set paths: cpp-tdgl-solve binary at `cpp-tdgl/build/tdgl_solve`
- Define shared device/timing/solver params

### Cell 2: Build shared device + timing
- Call `build_rectangular_device()` to get device object
- Call `build_timing()` to get timing schedule
- Print mesh stats

### Cell 3: Run py-tdgl, measure time
- Call `tdgl.simulate(device, ..., steps=timing_steps)` with `time.perf_counter()` around it
- Record wall time, frames, output size

### Cell 4: Convert device → cpp-tdgl mesh + write timing JSON
- Call `write_cpp_mesh(device, mesh_h5_path, solver_options)`
- Write timing.json for cpp-tdgl-solve
- Print mesh file size

### Cell 5: Run cpp-tdgl-solve with --output-dir
- `subprocess.run(cpp-tdgl-solve --mesh mesh.h5 --output output.h5 --timing timing.json --output-dir /tmp/steps --solver-options {...})`
- Time with `time.perf_counter()`
- Record wall time, step files count, output size

### Cell 6: Comparison table + verify step files
- Print table: solver | wall_time | steps/sec | total_frames | output_size
- List per-step HDF5 files from --output-dir
- Verify each step file has expected datasets (psi, mu, etc.)
