#!/usr/bin/env python3
"""Generate cpp-tdgl input for the py-tdgl logo device.

The original uses LinearRamp(0→1.8 mT), but cpp-tdgl only supports
constant applied field. We use constant 1.8 mT and compare the
steady-state solution.
Usage:
    python scripts/generate_logo.py
"""

import sys
import os
import numpy as np
import h5py

sys.path.insert(0, "../py-tdgl")
os.environ["OPENBLAS_NUM_THREADS"] = "1"

import tdgl
from tdgl.geometry import ensure_unique
from matplotlib.path import Path
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties


loops_lowercase = "abdegopq"
loops_uppercase = "ABDOPQR"


def interp_path(path, points_per_segment=11):
    t = np.linspace(0, 1, points_per_segment)
    segments = [
        bezier(t) for bezier, code in path.iter_bezier() if code != Path.MOVETO
    ]
    points = np.concatenate(segments)
    return ensure_unique(points)


def make_polygons(letter, fontsize=10, resample_points=(251, 101)):
    fontprops = FontProperties(weight="bold")
    path = TextPath((0, 0), letter, size=fontsize, prop=fontprops)
    if letter in loops_lowercase + loops_uppercase:
        jumps = np.where(path.codes == TextPath.MOVETO)[0][1:]
        vertices = np.split(path.vertices, jumps)
        codes = np.split(path.codes, jumps)
        paths = [Path(v[:-1], c[:-1]) for v, c in zip(vertices, codes)]
    else:
        paths = [Path(path.vertices[:-1], path.codes[:-1])]
    polygons = [tdgl.Polygon(points=interp_path(p)) for p in paths]
    polygons = sorted(polygons, key=lambda p: p.area, reverse=True)
    polygons = (
        [polygons[0].resample(resample_points[0]).buffer(2e-3)]
        + [p.resample(resample_points[1]) for p in polygons[1:]]
    )
    for i, p in enumerate(polygons):
        if i == 0:
            p.name = letter
        else:
            p.name = f"{letter}_hole{i}"
    return polygons


def main():
    xi = 0.4
    london_lambda = 4
    thickness = 0.1
    layer = tdgl.Layer(coherence_length=xi, london_lambda=london_lambda, thickness=thickness, gamma=1)

    fontsize = 10
    p_outer, p_inner = make_polygons("p", fontsize)
    y_outer, = make_polygons("y", fontsize)

    film = p_outer.union(y_outer.translate(dx=5.75)).resample(501)

    holes = [p_inner]

    print(f"Film area: {film.area:.4f}, holes: {len(holes)}")

    device = tdgl.Device("py", layer=layer, film=film, holes=holes, length_units="um")

    print("Generating mesh...")
    device.make_mesh(max_edge_length=xi / 2, smooth=100)
    ns = len(device.mesh.sites)
    ne = len(device.mesh.edge_mesh.edges)
    print(f"Device: py logo, {ns} sites, {ne} edges")

    # Derived constants (SI)
    Phi0 = 2.067833848e-15
    mu_0 = 4 * np.pi * 1e-7
    xi_m = xi * 1e-6
    lam_m = london_lambda * 1e-6
    d_m = thickness * 1e-6
    Lambda_m = lam_m ** 2 / d_m
    Bc2 = Phi0 / (2 * np.pi * xi_m ** 2)
    K0 = 4 * xi_m * Bc2 / (mu_0 * Lambda_m)
    A0 = xi_m * Bc2

    # Save mesh
    mesh_path = "data/logo_device.h5"
    with h5py.File(mesh_path, "w") as f:
        mg = f.create_group("mesh")
        device.mesh.to_hdf5(mg)
    print(f"Saved mesh to {mesh_path}")

    # Use constant 1.8 mT (final value from the ramp in the notebook)
    # For py-tdgl comparison, we'll run with constant field too
    applied_field = 1.8  # mT
    solve_time = 200.0  # Reduced from 800 since we skip the ramp

    output_path = "data/logo.h5"
    with h5py.File(output_path, "w") as fout:
        with h5py.File(mesh_path, "r") as fin:
            fin.copy("mesh", fout)

        dg = fout.create_group("device")
        dg.attrs["name"] = device.name
        dg.attrs["length_units"] = device.length_units
        dg.attrs["K0"] = K0
        dg.attrs["A0"] = A0
        dg.attrs["Bc2"] = Bc2
        dg.attrs["Lambda"] = Lambda_m

        lg = dg.create_group("layer")
        lg.attrs["coherence_length"] = xi
        lg.attrs["london_lambda"] = london_lambda
        lg.attrs["thickness"] = thickness
        lg.attrs["gamma"] = device.layer.gamma
        lg.attrs["u"] = device.layer.u
        lg.attrs["z0"] = 0.0

        og = fout.create_group("options")
        og.attrs["solve_time"] = solve_time
        og.attrs["skip_time"] = 0.0
        og.attrs["dt_init"] = 1e-4
        og.attrs["dt_max"] = 1e-2
        og.attrs["adaptive"] = True
        og.attrs["adaptive_window"] = 10
        og.attrs["max_solve_retries"] = 10
        og.attrs["adaptive_time_step_multiplier"] = 0.25
        og.attrs["terminal_psi"] = 0.0
        og.attrs["save_every"] = 50
        og.attrs["include_screening"] = False
        og.attrs["max_iterations_per_step"] = 100
        og.attrs["screening_tolerance"] = 1e-3
        og.attrs["screening_step_size"] = 0.1
        og.attrs["screening_step_drag"] = 0.5
        og.attrs["field_units"] = "mT"
        og.attrs["current_units"] = "uA"
        og.attrs["applied_field"] = applied_field

    print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
