#!/usr/bin/env python3
"""Run both py-tdgl and cpp-tdgl on the weak-link device, benchmark timing,
generate side-by-side comparison videos, and compare outputs.

Usage:
    python scripts/run_benchmark_video.py [--skip-py] [--skip-cpp] [--skip-video]
"""

import argparse
import os
import sys
import subprocess
import time
import shutil
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import h5py

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
BUILD_DIR = BASE_DIR / "build"
RESULTS_DIR = BASE_DIR / "results"
FRAMES_DIR = RESULTS_DIR / "frames"

CPP_INPUT = DATA_DIR / "weak_link_no_screen.h5"
PY_OUTPUT = RESULTS_DIR / "py_tdgl_output.h5"
CPP_OUTPUT = RESULTS_DIR / "cpp_tdgl_output.h5"

SOLVE_TIME = 50.0
SAVE_EVERY = 50  # ~100 frames
FPS = 20
QUANTITIES = ["order_parameter", "phase"]

os.environ["OPENBLAS_NUM_THREADS"] = "1"
sys.path.insert(0, str(BASE_DIR.parent / "py-tdgl"))


# ============================================================
# 1. RUN SIMULATIONS
# ============================================================

def run_py_tdgl():
    """Run py-tdgl on the weak-link device and save solution."""
    print("=" * 60)
    print("RUNNING PY-TDGL")
    print("=" * 60)

    import tdgl
    from tdgl.geometry import box, circle

    length_units = "um"
    xi = 0.5
    london_lambda = 2
    d = 0.1
    layer = tdgl.Layer(coherence_length=xi, london_lambda=london_lambda,
                        thickness=d, gamma=1)

    total_width = 5
    total_length = 3.5 * total_width
    link_width = total_width / 3

    right_notch = (
        tdgl.Polygon(points=box(total_width))
        .rotate(45)
        .translate(dx=(np.sqrt(2) * total_width + link_width) / 2)
    )
    left_notch = right_notch.scale(xfact=-1)
    film = (
        tdgl.Polygon("film", points=box(total_width, total_length))
        .difference(right_notch, left_notch)
        .resample(401)
        .buffer(0)
    )

    round_hole = (
        tdgl.Polygon("round_hole", points=circle(link_width / 2))
        .translate(dy=total_length / 5)
    )
    square_hole = (
        tdgl.Polygon("square_hole", points=box(link_width))
        .rotate(45)
        .translate(dy=-total_length / 5)
    )

    source = (
        tdgl.Polygon("source", points=box(1.1 * total_width, total_length / 100))
        .translate(dy=total_length / 2)
    )
    drain = source.scale(yfact=-1).set_name("drain")

    device = tdgl.Device(
        "weak_link", layer=layer, film=film,
        holes=[round_hole, square_hole],
        terminals=[source, drain],
        probe_points=[(0, total_length / 2.5), (0, -total_length / 2.5)],
        length_units=length_units,
    )
    device.make_mesh(max_edge_length=xi / 2, smooth=100)
    ns = len(device.mesh.sites)
    ne = len(device.mesh.edge_mesh.edges)
    print(f"Device: weak_link, {ns} sites, {ne} edges")

    PY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    options = tdgl.SolverOptions(
        solve_time=SOLVE_TIME,
        dt_init=1e-4,
        dt_max=1e-2,
        adaptive=True,
        adaptive_window=10,
        max_solve_retries=10,
        adaptive_time_step_multiplier=0.25,
        field_units="mT",
        current_units="uA",
        save_every=SAVE_EVERY,
        include_screening=False,
        output_file=str(PY_OUTPUT),
    )

    t0 = time.perf_counter()
    solution = tdgl.solve(device, options,
                          terminal_currents=dict(source=12, drain=-12))
    elapsed = time.perf_counter() - t0

    print(f"py-tdgl saved to {PY_OUTPUT}")
    print(f"py-tdgl time: {elapsed:.3f} s")

    # Count steps
    with h5py.File(PY_OUTPUT, "r") as f:
        data = f.get("solution/data", f.get("data"))
        n_steps = sum(1 for k in data.keys() if k.lstrip("-").isdigit())
    print(f"py-tdgl steps saved: {n_steps}")

    return elapsed, ns, ne, n_steps


def run_cpp_tdgl():
    """Run cpp-tdgl on the weak-link device and measure time."""
    print("=" * 60)
    print("RUNNING CPP-TDGL")
    print("=" * 60)

    exe = BUILD_DIR / "tdgl_solve"
    if not exe.exists():
        raise RuntimeError(f"cpp-tdgl executable not found: {exe}")

    cmd = [str(exe), "--mesh", str(CPP_INPUT), "--output", str(CPP_OUTPUT),
           "--source-current", "12", "--drain-current", "-12"]
    print(f"Command: {' '.join(cmd)}")

    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0

    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"cpp-tdgl exited with code {result.returncode}")

    print(f"cpp-tdgl time: {elapsed:.3f} s")

    # Count steps and get mesh info
    with h5py.File(CPP_OUTPUT, "r") as f:
        data = f.get("solution/data", f.get("data"))
        n_steps = len(data)
        ns = f["mesh/sites"].shape[0]
        ne = f["mesh/edge_mesh/edges"].shape[0]
    print(f"cpp-tdgl steps saved: {n_steps}")

    return elapsed, ns, ne, n_steps


# ============================================================
# 2. VIDEO FRAME GENERATION
# ============================================================

def load_mesh_from_h5(h5path):
    """Load mesh (sites, elements) from HDF5 file."""
    with h5py.File(h5path, "r") as f:
        # Try multiple possible paths
        for mesh_path in ["mesh", "solution/device/mesh"]:
            if mesh_path in f:
                sites = f[f"{mesh_path}/sites"][()]
                elements = f[f"{mesh_path}/elements"][()]
                return sites, elements
        raise ValueError(f"No mesh found in {h5path}")
    return sites, elements


def load_step_data(h5path, step_key, data_path="data"):
    """Load data for one time step."""
    with h5py.File(h5path, "r") as f:
        grp = f[data_path][step_key]
        step_data = {}
        if "psi" in grp:
            psi = grp["psi"][()]
            step_data["abs_psi_sq"] = np.abs(psi) ** 2
            step_data["phase"] = np.angle(psi)
        else:
            pr = grp["psi_real"][()]
            pi = grp["psi_imag"][()]
            step_data["abs_psi_sq"] = pr ** 2 + pi ** 2
            step_data["phase"] = np.arctan2(pi, pr)

        for ds_name in ["mu", "supercurrent", "normal_current"]:
            if ds_name in grp:
                step_data[ds_name] = grp[ds_name][()]

        step_data["time"] = float(grp.attrs.get("time", grp.attrs.get("step", 0)))
        step_data["step"] = int(grp.attrs.get("step", step_key))
    return step_data


def render_frame(args):
    """Render a single frame for one source. Called in parallel."""
    h5path, step_key, out_png, quantity, data_path, label = args
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.tri as mtri

        sites, elements = load_mesh_from_h5(h5path)
        x, y = sites[:, 0], sites[:, 1]
        triang = mtri.Triangulation(x, y, elements)

        step_data = load_step_data(h5path, step_key, data_path)

        fig, ax = plt.subplots(figsize=(6, 4.5), dpi=100)
        ax.set_aspect("equal")

        if quantity == "order_parameter":
            vals = step_data["abs_psi_sq"]
            tpc = ax.tripcolor(triang, vals, cmap="viridis", vmin=0, vmax=1.1)
            title = f"|ψ|²  t = {step_data['time']:.4f}"
        elif quantity == "phase":
            vals = step_data["phase"]
            mask = step_data["abs_psi_sq"] < 0.05
            vals_plot = np.where(mask, np.nan, vals)
            tpc = ax.tripcolor(triang, vals_plot, cmap="twilight_shifted",
                               vmin=-np.pi, vmax=np.pi)
            title = f"phase(ψ)  t = {step_data['time']:.4f}"
        elif quantity == "supercurrent":
            J = step_data.get("supercurrent")
            if J is not None:
                if J.ndim == 2:
                    J_mag = np.sqrt(J[:, 0] ** 2 + J[:, 1] ** 2)
                else:
                    J_mag = np.abs(J)
                # Interpolate edge values to sites via simple average of adjacent edges
                J_site = np.zeros(sites.shape[0])
                count = np.zeros(sites.shape[0])
                edges_data = None
                with h5py.File(h5path, "r") as hf:
                    for mp in ["mesh", "solution/device/mesh"]:
                        if mp in hf:
                            edges_data = hf[f"{mp}/edge_mesh/edges"][()]
                            break
                if edges_data is not None:
                    for e_idx in range(len(J_mag)):
                        j0, j1 = edges_data[e_idx, 0], edges_data[e_idx, 1]
                        J_site[j0] += J_mag[e_idx]
                        J_site[j1] += J_mag[e_idx]
                        count[j0] += 1
                        count[j1] += 1
                mask = count > 0
                J_site[mask] /= count[mask]
                tpc = ax.tripcolor(triang, J_site, cmap="inferno")
                title = f"|Jₛ|  t = {step_data['time']:.4f}"
            else:
                ax.text(0.5, 0.5, "No supercurrent data", transform=ax.transAxes)
                title = f"|Jₛ|  t = {step_data['time']:.4f}"
        elif quantity == "mu":
            mu = step_data.get("mu")
            if mu is not None:
                tpc = ax.tripcolor(triang, mu, cmap="coolwarm")
                title = f"μ  t = {step_data['time']:.4f}"
            else:
                ax.text(0.5, 0.5, "No mu data", transform=ax.transAxes)
                title = f"μ  t = {step_data['time']:.4f}"
        else:
            raise ValueError(f"Unknown quantity: {quantity}")

        ax.set_title(f"[{label}] {title}", fontsize=10)
        fig.colorbar(tpc, ax=ax, shrink=0.8)
        plt.tight_layout()
        fig.savefig(out_png, dpi=100)
        plt.close(fig)
        return out_png
    except Exception as e:
        print(f"  ERROR rendering {out_png}: {e}")
        return None


def get_step_keys_and_data_path(h5path):
    """Get sorted step keys and data path from HDF5."""
    with h5py.File(h5path, "r") as f:
        # Determine data path
        if "solution/data" in f:
            data_path = "solution/data"
        elif "data" in f and isinstance(f["data"], h5py.Group):
            data_path = "data"
        else:
            raise ValueError(f"Cannot find data group in {h5path}")

        data = f[data_path]
        keys = sorted(data.keys(), key=lambda x: int(x.lstrip("-")))
        return keys, data_path


def generate_frames_parallel(h5path, label, quantities, max_workers=None):
    """Generate all frames for a solution file using parallel workers."""
    if max_workers is None:
        max_workers = max(1, mp.cpu_count() - 1)

    step_keys, data_path = get_step_keys_and_data_path(h5path)
    print(f"  {label}: {len(step_keys)} steps, data_path={data_path}")

    tasks = []
    for qty in quantities:
        qty_dir = FRAMES_DIR / label / qty
        qty_dir.mkdir(parents=True, exist_ok=True)
        for i, sk in enumerate(step_keys):
            out_png = qty_dir / f"frame_{i:04d}.png"
            tasks.append((str(h5path), sk, str(out_png), qty, data_path, label))

    print(f"  {label}: generating {len(tasks)} frames with {max_workers} workers...")

    done = 0
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(render_frame, t): t for t in tasks}
        for f in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == len(tasks):
                print(f"    {done}/{len(tasks)} frames")

    print(f"  {label}: done")
    return len(step_keys)


def combine_frames_to_video(frames_dir, output_mp4, fps=20):
    """Use ffmpeg to combine PNG frames into MP4."""
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-preset", "medium",
        str(output_mp4),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}")
        return False
    return True


def create_side_by_side_video(py_frames_dir, cpp_frames_dir, output_mp4, fps=20):
    """Create side-by-side comparison video from py and cpp frames."""
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(py_frames_dir / "frame_%04d.png"),
        "-framerate", str(fps),
        "-i", str(cpp_frames_dir / "frame_%04d.png"),
        "-filter_complex",
        "[0:v][1:v]hstack=inputs=2[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-preset", "medium",
        str(output_mp4),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}")
        return False
    return True


def generate_videos(quantities, fps):
    """Generate individual and side-by-side videos for all quantities."""
    print("\n" + "=" * 60)
    print("GENERATING VIDEOS")
    print("=" * 60)

    max_workers = max(1, mp.cpu_count() - 2)

    # Generate frames in parallel for both sources
    n_py = generate_frames_parallel(PY_OUTPUT, "py", quantities, max_workers)
    n_cpp = generate_frames_parallel(CPP_OUTPUT, "cpp", quantities, max_workers)

    n_frames = min(n_py, n_cpp)
    print(f"\nUsing {n_frames} frames (min of py={n_py}, cpp={n_cpp})")

    # Generate individual videos
    videos = []
    for qty in quantities:
        py_dir = FRAMES_DIR / "py" / qty
        cpp_dir = FRAMES_DIR / "cpp" / qty

        # Trim extra frames
        for d in [py_dir, cpp_dir]:
            for f in list(d.glob("frame_*.png")):
                idx = int(f.stem.split("_")[1])
                if idx >= n_frames:
                    f.unlink()

        py_vid = RESULTS_DIR / f"py_{qty}.mp4"
        cpp_vid = RESULTS_DIR / f"cpp_{qty}.mp4"
        side_vid = RESULTS_DIR / f"comparison_{qty}.mp4"

        print(f"\n  Combining py frames -> {py_vid}")
        if combine_frames_to_video(py_dir, py_vid, fps):
            videos.append(py_vid)

        print(f"  Combining cpp frames -> {cpp_vid}")
        if combine_frames_to_video(cpp_dir, cpp_vid, fps):
            videos.append(cpp_vid)

        print(f"  Creating side-by-side -> {side_vid}")
        if create_side_by_side_video(py_dir, cpp_dir, side_vid, fps):
            videos.append(side_vid)

    return videos


# ============================================================
# 3. COMPARISON
# ============================================================

def compare_outputs():
    """Compare py-tdgl and cpp-tdgl outputs."""
    print("\n" + "=" * 60)
    print("COMPARING OUTPUTS")
    print("=" * 60)

    compare_script = BASE_DIR / "scripts" / "compare.py"
    if not compare_script.exists():
        print("  compare.py not found, skipping")
        return False

    result = subprocess.run(
        [sys.executable, str(compare_script),
         "--py-tdgl", str(PY_OUTPUT),
         "--cpp-tdgl", str(CPP_OUTPUT)],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.returncode == 0


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark py-tdgl vs cpp-tdgl with video generation")
    parser.add_argument("--skip-py", action="store_true",
                        help="Skip py-tdgl simulation")
    parser.add_argument("--skip-cpp", action="store_true",
                        help="Skip cpp-tdgl simulation")
    parser.add_argument("--skip-video", action="store_true",
                        help="Skip video generation")
    parser.add_argument("--skip-compare", action="store_true",
                        help="Skip output comparison")
    parser.add_argument("--quantities", nargs="+",
                        default=["order_parameter", "phase", "supercurrent", "mu"],
                        help="Quantities to visualize")
    parser.add_argument("--fps", type=int, default=FPS)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Run simulations
    py_time = py_ns = py_ne = py_steps = None
    cpp_time = cpp_ns = cpp_ne = cpp_steps = None

    if not args.skip_py:
        py_time, py_ns, py_ne, py_steps = run_py_tdgl()
    else:
        print("Skipping py-tdgl (using existing output)")
        with h5py.File(PY_OUTPUT, "r") as f:
            d = f.get("solution/data", f.get("data"))
            py_steps = sum(1 for k in d.keys() if k.lstrip("-").isdigit())
            mesh = f.get("mesh", f.get("solution/device/mesh"))
            py_ns = mesh["sites"].shape[0]
            py_ne = mesh["edge_mesh/edges"].shape[0]

    if not args.skip_cpp:
        cpp_time, cpp_ns, cpp_ne, cpp_steps = run_cpp_tdgl()
    else:
        print("Skipping cpp-tdgl (using existing output)")
        with h5py.File(CPP_OUTPUT, "r") as f:
            d = f.get("solution/data", f.get("data"))
            cpp_steps = sum(1 for k in d.keys() if k.lstrip("-").isdigit())
            mesh = f.get("mesh", f.get("solution/device/mesh"))
            cpp_ns = mesh["sites"].shape[0]
            cpp_ne = mesh["edge_mesh/edges"].shape[0]

    # 2. Generate videos
    videos = []
    if not args.skip_video:
        videos = generate_videos(args.quantities, args.fps)

    # 3. Compare outputs
    passed = None
    if not args.skip_compare:
        passed = compare_outputs()

    # 4. Summary
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"  Mesh:        {py_ns} sites, {py_ne} edges")
    print(f"  Solve time:  {SOLVE_TIME}")
    print(f"  Save every:  {SAVE_EVERY}")
    print(f"  py-tdgl:     {py_time:.3f} s  ({py_steps} frames)"
          if py_time else "  py-tdgl:     (skipped)")
    print(f"  cpp-tdgl:    {cpp_time:.3f} s  ({cpp_steps} frames)"
          if cpp_time else "  cpp-tdgl:    (skipped)")
    if py_time and cpp_time:
        speedup = py_time / cpp_time
        print(f"  Speedup:     {speedup:.1f}x")

    if videos:
        print(f"\n  Videos generated:")
        for v in videos:
            size_mb = v.stat().st_size / 1e6
            print(f"    {v.name}  ({size_mb:.1f} MB)")

    if passed is not None:
        print(f"\n  Output comparison: {'PASSED' if passed else 'FAILED'}")


if __name__ == "__main__":
    main()
