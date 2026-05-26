#!/usr/bin/env python3
"""Generate a large mesh HDF5 file for benchmarking CPU vs GPU TDGL solvers.

Usage:
    python scripts/generate_benchmark_mesh.py [--sites 50000] [--output data/benchmark_large.h5]
"""
import argparse
import os
import numpy as np
import h5py
import tdgl
from tdgl import Polygon, Layer


def make_device(target_sites):
    """Create a rectangular device with target number of sites."""
    layer = Layer(
        london_lambda=2.0,
        coherence_length=0.5,
        thickness=0.1,
        gamma=10.0,
        u=5.79,
    )

    # Sites scale roughly as area / max_edge^2.
    # For ~50k sites at target_sites:
    # Use a fixed 50x10 um film with max_edge controlling density.
    # max_edge=0.1 on 50x10 gives ~50k sites.
    W = 50.0
    H = 10.0
    # Sites ~ area * 3.6 / max_edge^2 (empirical from Delaunay triangulation)
    # For ~50k: max_edge = sqrt(500 * 3.6 / 50000) ≈ 0.19
    max_edge = np.sqrt(W * H * 3.6 / target_sites) if target_sites > 0 else 0.5

    print(f"  Film size: {W:.1f} x {H:.1f} um, max_edge_length={max_edge}")

    film = Polygon(name="film", points=[
        (0, 0), (W, 0), (W, H), (0, H)
    ])

    source = Polygon(name="source", points=[
        (-0.5, H * 0.25), (0.5, H * 0.25),
        (0.5, H * 0.75), (-0.5, H * 0.75)
    ])
    drain = Polygon(name="drain", points=[
        (W - 0.5, H * 0.25), (W + 0.5, H * 0.25),
        (W + 0.5, H * 0.75), (W - 0.5, H * 0.75)
    ])

    device = tdgl.Device(
        name="benchmark_device",
        layer=layer,
        film=film,
        terminals=[source, drain],
        probe_points=[(W * 0.25, H / 2), (W * 0.75, H / 2)],
    )
    device.make_mesh(max_edge_length=max_edge, smooth=10)
    return device


def write_benchmark_hdf5(output_path, device):
    """Write mesh + device data (no solution data needed for benchmarking)."""
    if os.path.exists(output_path):
        os.remove(output_path)

    with h5py.File(output_path, "w") as f:
        # Mesh
        mesh_grp = f.create_group("mesh")
        device.mesh.to_hdf5(mesh_grp)

        # Device
        dev_grp = f.create_group("device")
        dev_grp.attrs["name"] = device.name
        dev_grp.attrs["length_units"] = device.length_units

        layer_grp = dev_grp.create_group("layer")
        device.layer.to_hdf5(layer_grp)

        if device.terminal_info():
            term_grp = dev_grp.create_group("terminals")
            for ti in device.terminal_info():
                tg = term_grp.create_group(ti.name)
                tg["site_indices"] = np.array(ti.site_indices, dtype=np.int64)
                tg["edge_indices"] = np.array(ti.edge_indices, dtype=np.int64)
                tg["boundary_edge_indices"] = np.array(
                    ti.boundary_edge_indices, dtype=np.int64
                )
                tg.attrs["length"] = ti.length

        if device.probe_points is not None:
            dev_grp["probe_points"] = np.array(device.probe_points, dtype=np.float64)
            dev_grp["probe_point_indices"] = np.array(
                device.probe_point_indices, dtype=np.int64
            )

        # Unit conversion factors
        xi = device.layer.coherence_length
        london_lambda = device.layer.london_lambda
        thickness = device.layer.thickness
        Phi_0 = 2.067833848e-15
        xi_m = xi * 1e-6
        lam_m = london_lambda * 1e-6
        d_m = thickness * 1e-6
        Bc2 = Phi_0 / (2 * np.pi * xi_m ** 2)
        Lambda = lam_m ** 2 / d_m
        mu_0 = 4 * np.pi * 1e-7
        K0 = 4 * xi_m * Bc2 / (mu_0 * Lambda)
        A0 = xi_m * Bc2

        dev_grp.attrs["K0"] = K0
        dev_grp.attrs["A0"] = A0
        dev_grp.attrs["Bc2"] = Bc2
        dev_grp.attrs["Lambda"] = Lambda
        dev_grp.attrs["mu_0"] = mu_0

    ns = len(device.mesh.sites)
    ne = len(device.mesh.edge_mesh.edges)
    print(f"  Written: {output_path}")
    print(f"  {ns} sites, {ne} edges")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", type=int, default=50000,
                        help="Target number of sites")
    parser.add_argument("--output", "-o",
                        default="data/benchmark_large.h5",
                        help="Output path")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    print(f"Generating mesh (target ~{args.sites} sites)...")
    device = make_device(args.sites)
    ns = len(device.mesh.sites)
    ne = len(device.mesh.edge_mesh.edges)
    print(f"  Actual: {ns} sites, {ne} edges")

    write_benchmark_hdf5(args.output, device)


if __name__ == "__main__":
    main()
