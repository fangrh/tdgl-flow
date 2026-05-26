#!/usr/bin/env python3
"""Generate test data for cpp-tdgl by running py-tdgl and saving results to HDF5.

Usage:
    python scripts/generate_test_data.py --output data/reference.h5 [--steps 100] [--screening]
"""

import argparse
import os
import sys
import tempfile
import numpy as np
import h5py
import tdgl


def make_device():
    """Create a simple rectangular device with two terminals."""
    from tdgl import Polygon, Layer

    layer = Layer(
        london_lambda=2.0,
        coherence_length=0.5,
        thickness=0.1,
        gamma=10.0,
        u=5.79,
    )

    film = Polygon(name="film", points=[
        (0, 0), (10, 0), (10, 2), (0, 2)
    ])

    source = Polygon(name="source", points=[
        (-0.5, 0.5), (0.5, 0.5), (0.5, 1.5), (-0.5, 1.5)
    ])
    drain = Polygon(name="drain", points=[
        (9.5, 0.5), (10.5, 0.5), (10.5, 1.5), (9.5, 1.5)
    ])

    device = tdgl.Device(
        name="test_device",
        layer=layer,
        film=film,
        terminals=[source, drain],
        probe_points=[(2, 1), (8, 1)],
    )
    device.make_mesh(max_edge_length=0.5, smooth=10)
    return device


def run_py_tdgl(device, steps, include_screening, output_file):
    """Run py-tdgl solver and return Solution."""
    options = tdgl.SolverOptions(
        solve_time=0.05,
        dt_init=1e-4,
        dt_max=1e-2,
        adaptive=True,
        adaptive_window=10,
        max_solve_retries=10,
        adaptive_time_step_multiplier=0.25,
        terminal_psi=0.0,
        save_every=2,
        include_screening=include_screening,
        max_iterations_per_step=100,
        screening_tolerance=1e-3,
        screening_step_size=0.1,
        screening_step_drag=0.5,
        output_file=output_file,
    )

    solution = tdgl.solve(
        device=device,
        options=options,
        applied_vector_potential=0.0,
        terminal_currents={"source": 1.0, "drain": -1.0},
        disorder_epsilon=1.0,
    )
    return solution, options


def write_reference_hdf5(output_path, py_output_path, device, options):
    """Write all data needed by C++ solver into a single HDF5 file.

    Structure:
    /mesh/                      - mesh data (from device)
    /device/                    - device info (layer, terminals, probes, scales)
    /options/                   - solver options as attributes
    /data/<step>/               - reference solution snapshots from py-tdgl
    """
    if os.path.exists(output_path):
        os.remove(output_path)

    with h5py.File(output_path, "w") as f:
        # --- Mesh (from device.mesh) ---
        mesh_grp = f.create_group("mesh")
        device.mesh.to_hdf5(mesh_grp)

        # --- Device ---
        dev_grp = f.create_group("device")
        dev_grp.attrs["name"] = device.name
        dev_grp.attrs["length_units"] = device.length_units

        # Layer
        layer_grp = dev_grp.create_group("layer")
        device.layer.to_hdf5(layer_grp)

        # Terminal info
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

        # Probe points
        if device.probe_points is not None:
            dev_grp["probe_points"] = np.array(device.probe_points, dtype=np.float64)
            # Store probe point indices (nearest mesh sites)
            dev_grp["probe_point_indices"] = np.array(
                device.probe_point_indices, dtype=np.int64
            )

        # Unit conversion factors (pre-computed)
        xi = device.layer.coherence_length  # um
        london_lambda = device.layer.london_lambda  # um
        thickness = device.layer.thickness  # um
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

        # --- Options ---
        opt_grp = f.create_group("options")
        for k, v in options.__dict__.items():
            if k == "sparse_solver":
                v = v.value
            if v is not None:
                try:
                    opt_grp.attrs[k] = v
                except (TypeError, OSError):
                    pass  # Skip non-serializable attrs

        # --- Reference solution data (copy from py-tdgl output) ---
        with h5py.File(py_output_path, "r") as sf:
            # Copy /data/ group
            if "data" in sf:
                sf.copy("data", f, name="data")

            # Copy /solution/ group (device + options metadata)
            if "solution" in sf:
                sf.copy("solution", f, name="solution")

    print(f"Reference data written to: {output_path}")
    print(f"  Mesh: {len(device.mesh.sites)} sites, {len(device.mesh.elements)} elements, "
          f"{len(device.mesh.edge_mesh.edges)} edges")
    print(f"  Probes: {len(device.probe_point_indices)} points")
    print(f"  Terminals: {[ti.name for ti in device.terminal_info()]}")

    # List saved steps
    with h5py.File(output_path, "r") as f:
        steps = sorted(int(k) for k in f["data"].keys() if k.lstrip("-").isdigit())
        print(f"  Saved steps: {len(steps)} ({steps[0]} .. {steps[-1]})")


def main():
    parser = argparse.ArgumentParser(description="Generate test data for cpp-tdgl")
    parser.add_argument("--output", "-o", default="data/reference.h5",
                        help="Output HDF5 file path")
    parser.add_argument("--steps", type=int, default=100,
                        help="Number of solve steps")
    parser.add_argument("--screening", action="store_true",
                        help="Enable electromagnetic screening")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    print("Creating device...")
    device = make_device()
    print(f"  Device: {device.name}, {len(device.mesh.sites)} sites")

    # py-tdgl writes to a temp file, we'll combine into final output
    py_output = os.path.join(os.path.dirname(args.output), "_py_output.h5")
    # Remove old py-tdgl output so it doesn't get renamed
    if os.path.exists(py_output):
        os.remove(py_output)

    print(f"Running py-tdgl solver (steps={args.steps}, screening={args.screening})...")
    solution, options = run_py_tdgl(device, args.steps, args.screening, py_output)
    print(f"  Solution time: {solution.total_seconds:.2f}s")

    print("Writing reference HDF5...")
    write_reference_hdf5(args.output, py_output, device, options)


if __name__ == "__main__":
    main()
