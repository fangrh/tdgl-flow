#!/usr/bin/env python3
"""Convert a py-tdgl device HDF5 file to cpp-tdgl format.

Usage:
    python scripts/convert_device.py input_pytdgl.h5 output_cpptdgl.h5 [--screening]
"""

import argparse
import sys
import numpy as np
import h5py

sys.path.insert(0, "../py-tdgl")
import tdgl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="py-tdgl device HDF5 file")
    parser.add_argument("output", help="cpp-tdgl output HDF5 file")
    parser.add_argument("--screening", action="store_true")
    args = parser.parse_args()

    # Load py-tdgl device
    device = tdgl.Device.from_hdf5(args.input)
    xi = device.layer.coherence_length
    lam = device.layer.london_lambda
    d = device.layer.thickness

    # Compute derived quantities (same as Device.__init__ in py-tdgl)
    Phi0 = 2.067833848e-15  # Wb
    mu0 = 4 * np.pi * 1e-7
    K0 = d / Phi0
    A0 = Phi0 / (2 * np.pi * xi**2)
    Bc2 = Phi0 / (2 * np.pi * xi**2)
    Lambda = lam**2 / d

    print(f"Device: {device.name}")
    print(f"  Sites: {len(device.mesh.sites)}, Edges: {len(device.mesh.edge_mesh.edges)}")
    print(f"  xi={xi}, lambda={lam}, d={d}")
    print(f"  K0={K0:.6e}, A0={A0:.6e}, Lambda={Lambda:.6e}")
    print(f"  Terminals: {[t.name for t in device.terminals]}")

    with h5py.File(args.input, "r") as fin, h5py.File(args.output, "w") as fout:
        # Copy mesh as-is (cpp-tdgl reads mesh/ from root)
        fin.copy("mesh", fout)

        # Write device group
        dg = fout.create_group("device")
        dg.attrs["name"] = device.name
        dg.attrs["length_units"] = device.length_units
        dg.attrs["K0"] = K0
        dg.attrs["A0"] = A0
        dg.attrs["Bc2"] = Bc2
        dg.attrs["Lambda"] = Lambda

        # Write layer
        lg = dg.create_group("layer")
        lg.attrs["coherence_length"] = xi
        lg.attrs["london_lambda"] = lam
        lg.attrs["thickness"] = d
        lg.attrs["gamma"] = device.layer.gamma
        lg.attrs["u"] = device.layer.u
        if hasattr(device.layer, "z0"):
            lg.attrs["z0"] = device.layer.z0

        # Write terminals
        tg = dg.create_group("terminals")
        for term in device.terminals:
            tgrp = tg.create_group(term.name)
            tgrp.create_dataset("site_indices", data=term.site_indices.astype(np.int64))
            tgrp.create_dataset("edge_indices", data=term.edge_indices.astype(np.int64))
            tgrp.create_dataset("boundary_edge_indices",
                                data=term.boundary_edge_indices.astype(np.int64))
            tgrp.attrs["length"] = term.length

        # Write options
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
        og.attrs["include_screening"] = args.screening
        og.attrs["max_iterations_per_step"] = 100
        og.attrs["screening_tolerance"] = 1e-3
        og.attrs["screening_step_size"] = 0.1
        og.attrs["screening_step_drag"] = 0.5
        og.attrs["field_units"] = "mT"
        og.attrs["current_units"] = "uA"

    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
