#!/usr/bin/env python3
"""Generate cpp-tdgl input files from the quickstart weak-link example.

This creates the device mesh in py-tdgl (which computes terminal site indices),
then saves in cpp-tdgl's expected HDF5 format.

Usage:
    python scripts/generate_benchmark.py [--screening]
"""

import sys
import os
import numpy as np
import h5py

sys.path.insert(0, "../py-tdgl")

os.environ["OPENBLAS_NUM_THREADS"] = "1"

import tdgl
from tdgl.geometry import box, circle


def main():
    # Replicate quickstart device exactly
    length_units = "um"
    xi = 0.5
    london_lambda = 2
    d = 0.1
    layer = tdgl.Layer(coherence_length=xi, london_lambda=london_lambda, thickness=d, gamma=1)

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
        "weak_link",
        layer=layer,
        film=film,
        holes=[round_hole, square_hole],
        terminals=[source, drain],
        probe_points=[(0, total_length / 2.5), (0, -total_length / 2.5)],
        length_units=length_units,
    )

    print("Generating mesh (this takes a moment)...")
    device.make_mesh(max_edge_length=xi / 2, smooth=100)
    ns = len(device.mesh.sites)
    ne = len(device.mesh.edge_mesh.edges)
    print(f"Device: weak_link, {ns} sites, {ne} edges")

    # Derived constants (in SI units to match py-tdgl's K0 computation)
    Phi0 = 2.067833848e-15
    mu_0 = 4 * np.pi * 1e-7
    xi_m = xi * 1e-6           # μm → m
    lam_m = london_lambda * 1e-6
    d_m = d * 1e-6
    Lambda_m = lam_m**2 / d_m  # effective penetration depth [m]
    Bc2 = Phi0 / (2 * np.pi * xi_m**2)  # upper critical field [T]
    K0 = 4 * xi_m * Bc2 / (mu_0 * Lambda_m)  # sheet current density scale [A/m]
    A0 = xi_m * Bc2            # vector potential scale [T·m]

    # Extract terminal info
    terminal_infos = device.terminal_info()
    for ti in terminal_infos:
        print(f"  Terminal '{ti.name}': {len(ti.site_indices)} sites, "
              f"{len(ti.boundary_edge_indices)} boundary edges, length={ti.length:.4f}")

    # Save in cpp-tdgl format
    output_no_screen = "data/weak_link_no_screen.h5"
    output_screen = "data/weak_link_screen.h5"

    for output_path, screening in [(output_no_screen, False), (output_screen, True)]:
        with h5py.File(output_path, "w") as fout:
            # Copy mesh
            with h5py.File("data/weak_link_device.h5", "r") as fin:
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
            lg.attrs["thickness"] = d
            lg.attrs["gamma"] = device.layer.gamma
            lg.attrs["u"] = device.layer.u
            lg.attrs["z0"] = 0.0

            # Terminals
            tg = dg.create_group("terminals")
            for ti in terminal_infos:
                tgrp = tg.create_group(ti.name)
                tgrp.create_dataset("site_indices",
                                    data=np.array(ti.site_indices, dtype=np.int64))
                tgrp.create_dataset("edge_indices",
                                    data=np.array(ti.edge_indices, dtype=np.int64))
                tgrp.create_dataset("boundary_edge_indices",
                                    data=np.array(ti.boundary_edge_indices, dtype=np.int64))
                tgrp.attrs["length"] = ti.length

            # Options
            og = fout.create_group("options")
            og.attrs["solve_time"] = 50.0
            og.attrs["skip_time"] = 0.0
            og.attrs["dt_init"] = 1e-4
            og.attrs["dt_max"] = 1e-2
            og.attrs["adaptive"] = True
            og.attrs["adaptive_window"] = 10
            og.attrs["max_solve_retries"] = 10
            og.attrs["adaptive_time_step_multiplier"] = 0.25
            og.attrs["terminal_psi"] = 0.0
            og.attrs["save_every"] = 100
            og.attrs["include_screening"] = screening
            og.attrs["max_iterations_per_step"] = 100
            og.attrs["screening_tolerance"] = 1e-3
            og.attrs["screening_step_size"] = 0.1
            og.attrs["screening_step_drag"] = 0.5
            og.attrs["field_units"] = "mT"
            og.attrs["current_units"] = "uA"

        label = "screening" if screening else "no_screen"
        print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
