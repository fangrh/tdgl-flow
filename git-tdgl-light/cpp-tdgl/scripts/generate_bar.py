#!/usr/bin/env python3
"""Generate cpp-tdgl input files for the bar device from py-tdgl screening notebook.

Usage:
    python scripts/generate_bar.py
"""

import sys
import os
import numpy as np
import h5py

sys.path.insert(0, "../py-tdgl")
os.environ["OPENBLAS_NUM_THREADS"] = "1"

import tdgl
from tdgl.geometry import box


def main():
    length_units = "um"
    xi = 0.1
    london_lambda = 0.075
    thickness = 0.05
    layer = tdgl.Layer(coherence_length=xi, london_lambda=london_lambda,
                        thickness=thickness, gamma=1)

    width = 2
    height = 1

    film = tdgl.Polygon("film", points=box(width, height, points=301))

    device = tdgl.Device(
        "bar",
        layer=layer,
        film=film,
        length_units=length_units,
    )

    print("Generating mesh...")
    device.make_mesh(max_edge_length=xi / 2, smooth=100)
    ns = len(device.mesh.sites)
    ne = len(device.mesh.edge_mesh.edges)
    print(f"Device: bar, {ns} sites, {ne} edges")

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

    # Save mesh only once (shared by both variants)
    mesh_path = "data/bar_device.h5"
    with h5py.File(mesh_path, "w") as f:
        mg = f.create_group("mesh")
        device.mesh.to_hdf5(mg)

    # Options for both variants
    configs = [
        ("data/bar_no_screen.h5", False, 1e-2),
        ("data/bar_screen.h5", True, 1e-3),
    ]

    applied_field = 0.1  # mT

    for output_path, screening, screening_tol in configs:
        with h5py.File(output_path, "w") as fout:
            # Copy mesh
            with h5py.File(mesh_path, "r") as fin:
                fin.copy("mesh", fout)

            # Device group
            dg = fout.create_group("device")
            dg.attrs["name"] = device.name
            dg.attrs["length_units"] = device.length_units
            dg.attrs["K0"] = K0
            dg.attrs["A0"] = A0
            dg.attrs["Bc2"] = Bc2
            dg.attrs["Lambda"] = Lambda_m

            # Layer
            lg = dg.create_group("layer")
            lg.attrs["coherence_length"] = xi
            lg.attrs["london_lambda"] = london_lambda
            lg.attrs["thickness"] = thickness
            lg.attrs["gamma"] = device.layer.gamma
            lg.attrs["u"] = device.layer.u
            lg.attrs["z0"] = 0.0

            # Options
            og = fout.create_group("options")
            og.attrs["solve_time"] = 5.0
            og.attrs["skip_time"] = 0.0
            og.attrs["dt_init"] = 1e-4
            og.attrs["dt_max"] = 1e-3 if screening else 1e-2
            og.attrs["adaptive"] = True
            og.attrs["adaptive_window"] = 10
            og.attrs["max_solve_retries"] = 10
            og.attrs["adaptive_time_step_multiplier"] = 0.25
            og.attrs["terminal_psi"] = 0.0
            og.attrs["save_every"] = 10
            og.attrs["include_screening"] = screening
            og.attrs["max_iterations_per_step"] = 100
            og.attrs["screening_tolerance"] = screening_tol
            og.attrs["screening_step_size"] = 0.1
            og.attrs["screening_step_drag"] = 0.5
            og.attrs["field_units"] = "mT"
            og.attrs["current_units"] = "uA"
            og.attrs["applied_field"] = applied_field

        label = "screen" if screening else "no_screen"
        print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
