#!/usr/bin/env python3
"""Generate side-by-side comparison videos for screening benchmark."""

import os
import sys
import subprocess
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import h5py

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "results"
FRAMES_DIR = RESULTS_DIR / "frames"
PY_OUTPUT = RESULTS_DIR / "py_screen.h5"
CPP_OUTPUT = RESULTS_DIR / "cpp_screen.h5"

QUANTITIES = ["order_parameter", "phase", "supercurrent", "mu"]
FPS = 15

os.environ["OPENBLAS_NUM_THREADS"] = "1"


def load_mesh_from_h5(h5path):
    with h5py.File(h5path, "r") as f:
        for mp in ["mesh", "solution/device/mesh"]:
            if mp in f:
                sites = f[f"{mp}/sites"][()]
                elements = f[f"{mp}/elements"][()]
                return sites, elements
    raise ValueError(f"No mesh in {h5path}")


def load_step_data(h5path, step_key, data_path):
    with h5py.File(h5path, "r") as f:
        grp = f[data_path][step_key]
        step_data = {}
        if "psi" in grp:
            psi = grp["psi"][()]
            step_data["abs_psi_sq"] = np.abs(psi) ** 2
            step_data["phase"] = np.angle(psi)
        elif "psi_real" in grp and "psi_imag" in grp:
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

        fig, ax = plt.subplots(figsize=(5.5, 4), dpi=100)
        ax.set_aspect("equal")

        if quantity == "order_parameter":
            vals = step_data["abs_psi_sq"]
            tpc = ax.tripcolor(triang, vals, cmap="viridis", vmin=0, vmax=1.1)
            title = f"|psi|^2  t={step_data['time']:.4f}"
        elif quantity == "phase":
            vals = step_data["phase"]
            mask = step_data["abs_psi_sq"] < 0.05
            vals_plot = np.where(mask, np.nan, vals)
            tpc = ax.tripcolor(triang, vals_plot, cmap="twilight_shifted", vmin=-np.pi, vmax=np.pi)
            title = f"phase(psi)  t={step_data['time']:.4f}"
        elif quantity == "supercurrent":
            J = step_data.get("supercurrent")
            if J is not None:
                if J.ndim == 2:
                    J_mag = np.sqrt(J[:, 0]**2 + J[:, 1]**2)
                else:
                    J_mag = np.abs(J)
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
                m = count > 0
                J_site[m] /= count[m]
                tpc = ax.tripcolor(triang, J_site, cmap="inferno")
                title = f"|Js|  t={step_data['time']:.4f}"
            else:
                ax.text(0.5, 0.5, "No supercurrent", transform=ax.transAxes)
                title = f"|Js|  t={step_data['time']:.4f}"
        elif quantity == "mu":
            mu = step_data.get("mu")
            if mu is not None:
                mu_c = mu - mu.mean()
                tpc = ax.tripcolor(triang, mu_c, cmap="coolwarm")
                title = f"mu  t={step_data['time']:.4f}"
            else:
                ax.text(0.5, 0.5, "No mu", transform=ax.transAxes)
                title = f"mu  t={step_data['time']:.4f}"

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
    with h5py.File(h5path, "r") as f:
        if "solution/data" in f:
            data_path = "solution/data"
        elif "data" in f and isinstance(f["data"], h5py.Group):
            data_path = "data"
        else:
            raise ValueError(f"No data group in {h5path}")
        data = f[data_path]
        keys = sorted(data.keys(), key=lambda x: int(x.lstrip("-")))
        return keys, data_path


def generate_frames(h5path, label, quantities, max_workers=None):
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
            if done % 50 == 0 or done == len(tasks):
                print(f"    {done}/{len(tasks)} frames")
    print(f"  {label}: done")
    return len(step_keys)


def main():
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    max_workers = max(1, mp.cpu_count() - 2)

    # Generate frames
    n_py = generate_frames(PY_OUTPUT, "py_screen", QUANTITIES, max_workers)
    n_cpp = generate_frames(CPP_OUTPUT, "cpp_screen", QUANTITIES, max_workers)

    n_frames = min(n_py, n_cpp)
    print(f"\nUsing {n_frames} frames (min of py={n_py}, cpp={n_cpp})")

    # Trim extra frames
    for d in [FRAMES_DIR / "py_screen", FRAMES_DIR / "cpp_screen"]:
        for qty_dir in d.iterdir():
            for f in list(qty_dir.glob("frame_*.png")):
                idx = int(f.stem.split("_")[1])
                if idx >= n_frames:
                    f.unlink()

    # Generate videos
    for qty in QUANTITIES:
        py_dir = FRAMES_DIR / "py_screen" / qty
        cpp_dir = FRAMES_DIR / "cpp_screen" / qty

        # Individual videos
        for d, prefix in [(py_dir, "py"), (cpp_dir, "cpp")]:
            vid = RESULTS_DIR / f"{prefix}_screen_{qty}.mp4"
            cmd = ["ffmpeg", "-y", "-framerate", str(FPS),
                   "-i", str(d / "frame_%04d.png"),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-crf", "18", "-preset", "medium", str(vid)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            print(f"  {vid.name}: {'OK' if r.returncode == 0 else 'FAIL'}")

        # Side-by-side
        side_vid = RESULTS_DIR / f"comparison_screen_{qty}.mp4"
        cmd = ["ffmpeg", "-y",
               "-framerate", str(FPS), "-i", str(py_dir / "frame_%04d.png"),
               "-framerate", str(FPS), "-i", str(cpp_dir / "frame_%04d.png"),
               "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
               "-map", "[v]",
               "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-crf", "18", "-preset", "medium", str(side_vid)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(f"  {side_vid.name}: {'OK' if r.returncode == 0 else 'FAIL'}")

    print("\nDone! Videos in results/")


if __name__ == "__main__":
    main()
