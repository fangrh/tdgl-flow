import h5py
import numpy as np
from scipy.interpolate import griddata

NX, NY = 100, 50
PSI_VMAX = 1.05


def load_mesh(h5_path):
    with h5py.File(h5_path, "r") as f:
        points = np.array(f["solution/device/mesh/sites"])
        edges = np.array(f["solution/device/mesh/edge_mesh/edges"])
        edge_dirs = np.array(f["solution/device/mesh/edge_mesh/directions"])
        dual_lengths = np.array(f["solution/device/mesh/edge_mesh/dual_edge_lengths"])
        total = len(f["data"].keys())

    xmin, xmax = points[:, 0].min(), points[:, 0].max()
    ymin, ymax = points[:, 1].min(), points[:, 1].max()
    gx = np.linspace(xmin, xmax, NX)
    gy = np.linspace(ymin, ymax, NY)
    GX, GY = np.meshgrid(gx, gy)
    grid_pts = np.column_stack([GX.ravel(), GY.ravel()])

    s1x, s2x = points[edges[:, 0], 0], points[edges[:, 1], 0]
    cross = ((s1x <= 0) & (s2x > 0)) | ((s1x > 0) & (s2x <= 0))

    return {
        "points": points,
        "edges": edges,
        "edge_dirs": edge_dirs,
        "dual_lengths": dual_lengths,
        "cross": cross,
        "grid_pts": grid_pts,
        "total_frames": total,
    }


def estimate_mu_vmax(h5_path, total):
    mu_maxes = []
    with h5py.File(h5_path, "r") as f:
        for i in range(total):
            try:
                mu_maxes.append(float(np.abs(np.array(f[f"data/{i}/mu"])).max()))
            except Exception:
                pass
    if mu_maxes and max(mu_maxes) > 0:
        return float(np.percentile(mu_maxes, 99))
    return 1.0


def interpolate(points, grid_pts, raw):
    return griddata(points, raw, grid_pts, method="cubic", fill_value=0.0).reshape(NY, NX)
