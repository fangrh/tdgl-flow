#!/usr/bin/env python3
"""Generate comparison videos for field_current and logo benchmarks."""

import h5py
import numpy as np
import subprocess
import os
import multiprocessing as mp
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri

BASE = Path("results")
FRAMES = BASE / "frames"
QUANTITIES = ["order_parameter", "phase", "supercurrent", "mu"]
FPS = 15

os.environ["OPENBLAS_NUM_THREADS"] = "1"


def load_mesh(h5p):
    with h5py.File(h5p, "r") as f:
        for mp in ["mesh", "solution/device/mesh"]:
            if mp in f:
                return f[f"{mp}/sites"][()], f[f"{mp}/elements"][()]
    raise ValueError(f"No mesh in {h5p}")


def load_step(h5p, sk, dp):
    with h5py.File(h5p, "r") as f:
        g = f[dp][sk]
        d = {}
        if "psi" in g:
            psi = g["psi"][()]
            d["abs_psi_sq"] = np.abs(psi) ** 2
            d["phase"] = np.angle(psi)
        elif "psi_real" in g:
            pr, pi = g["psi_real"][()], g["psi_imag"][()]
            d["abs_psi_sq"] = pr ** 2 + pi ** 2
            d["phase"] = np.arctan2(pi, pr)
        for n in ["mu", "supercurrent", "normal_current"]:
            if n in g:
                d[n] = g[n][()]
        d["time"] = float(g.attrs.get("time", 0))
    return d


def render(args):
    h5p, sk, opn, qty, dp, lb = args
    try:
        sites, elements = load_mesh(h5p)
        triang = mtri.Triangulation(sites[:, 0], sites[:, 1], elements)
        sd = load_step(h5p, sk, dp)
        fig, ax = plt.subplots(figsize=(5.5, 4), dpi=100)
        ax.set_aspect("equal")
        if qty == "order_parameter":
            tpc = ax.tripcolor(triang, sd["abs_psi_sq"], cmap="viridis", vmin=0, vmax=1.05)
            title = f"|psi|^2  t={sd['time']:.1f}"
        elif qty == "phase":
            vals = np.where(sd["abs_psi_sq"] < 0.05, np.nan, sd["phase"])
            tpc = ax.tripcolor(triang, vals, cmap="twilight_shifted", vmin=-np.pi, vmax=np.pi)
            title = f"phase  t={sd['time']:.1f}"
        elif qty == "supercurrent":
            J = sd.get("supercurrent")
            if J is not None:
                Jm = np.sqrt(J[:, 0] ** 2 + J[:, 1] ** 2) if J.ndim == 2 else np.abs(J)
                Js = np.zeros(sites.shape[0])
                cn = np.zeros(sites.shape[0])
                ed = None
                with h5py.File(h5p, "r") as hf:
                    for mp2 in ["mesh", "solution/device/mesh"]:
                        if mp2 in hf:
                            ed = hf[f"{mp2}/edge_mesh/edges"][()]
                            break
                if ed is not None:
                    for ei in range(len(Jm)):
                        j0, j1 = ed[ei]
                        Js[j0] += Jm[ei]
                        Js[j1] += Jm[ei]
                        cn[j0] += 1
                        cn[j1] += 1
                m = cn > 0
                Js[m] /= cn[m]
                tpc = ax.tripcolor(triang, Js, cmap="inferno")
                title = f"|Js|  t={sd['time']:.1f}"
        elif qty == "mu":
            mu = sd.get("mu")
            if mu is not None:
                tpc = ax.tripcolor(triang, mu - mu.mean(), cmap="coolwarm")
                title = f"mu  t={sd['time']:.1f}"
        ax.set_title(f"[{lb}] {title}", fontsize=10)
        fig.colorbar(tpc, ax=ax, shrink=0.8)
        plt.tight_layout()
        fig.savefig(opn, dpi=100)
        plt.close(fig)
    except Exception as e:
        print(f"  ERROR {opn}: {e}")


def gen_frames(h5p, lb, qty, mw=None):
    if mw is None:
        mw = max(1, mp.cpu_count() - 2)
    with h5py.File(h5p, "r") as f:
        dp = "solution/data" if "solution/data" in f else "data"
        keys = sorted(f[dp].keys(), key=lambda x: int(x.lstrip("-")))
    print(f"  {lb}: {len(keys)} steps")
    tasks = []
    for q in qty:
        d = FRAMES / lb / q
        d.mkdir(parents=True, exist_ok=True)
        for i, sk in enumerate(keys):
            tasks.append((str(h5p), sk, str(d / f"frame_{i:04d}.png"), q, dp, lb))
    print(f"  {lb}: {len(tasks)} frames")
    with ProcessPoolExecutor(max_workers=mw) as pool:
        futures = {pool.submit(render, t): t for t in tasks}
        for i, _ in enumerate(as_completed(futures), 1):
            if i % 100 == 0 or i == len(tasks):
                print(f"    {i}/{len(tasks)}")
    return len(keys)


def mkvids(pyd, cppd, base, qty, fps):
    for d, pfx in [(pyd, "py"), (cppd, "cpp")]:
        v = BASE / f"{base}_{pfx}_{qty}.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(d / "frame_%04d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(v)],
            capture_output=True, text=True)
        print(f"  {v.name}: OK" if r.returncode == 0 else f"  {v.name}: FAIL")
    s = BASE / f"{base}_comparison_{qty}.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(pyd / "frame_%04d.png"),
         "-framerate", str(fps), "-i", str(cppd / "frame_%04d.png"),
         "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(s)],
        capture_output=True, text=True)
    print(f"  {s.name}: OK" if r.returncode == 0 else f"  {s.name}: FAIL")


def main():
    FRAMES.mkdir(parents=True, exist_ok=True)
    mw = max(1, mp.cpu_count() - 2)
    configs = [
        ("results/py_field_current.h5", "results/cpp_field_current.h5", "field_current"),
        ("results/py_logo.h5", "results/cpp_logo.h5", "logo"),
    ]
    for py_p, cpp_p, lb in configs:
        print(f"\n=== {lb} ===")
        for d in [FRAMES / f"{lb}_py", FRAMES / f"{lb}_cpp"]:
            if d.exists():
                shutil.rmtree(d)
        n_py = gen_frames(py_p, f"{lb}_py", QUANTITIES, mw)
        n_cpp = gen_frames(cpp_p, f"{lb}_cpp", QUANTITIES, mw)
        n = min(n_py, n_cpp)
        print(f"  Using {n} frames")
        for d in [FRAMES / f"{lb}_py", FRAMES / f"{lb}_cpp"]:
            for qd in d.iterdir():
                for f in list(qd.glob("frame_*.png")):
                    if int(f.stem.split("_")[1]) >= n:
                        f.unlink()
        for q in QUANTITIES:
            mkvids(FRAMES / f"{lb}_py" / q, FRAMES / f"{lb}_cpp" / q, lb, q, FPS)
    print("\nAll done!")


if __name__ == "__main__":
    main()
