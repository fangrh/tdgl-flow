"""Convert a tdgl Device to cpp-tdgl-compatible HDF5.

Usage:
    from convert_mesh import write_cpp_mesh
    write_cpp_mesh(device, "device.h5", solver_options={...})
"""
import numpy as np
import h5py


def write_cpp_mesh(
    device,
    output_path: str,
    solver_options: dict | None = None,
    epsilon_fn=None,
):
    """Write a tdgl Device to an HDF5 file that cpp-tdgl can read.

    Args:
        device: A tdgl.Device with mesh already built (device.make_mesh() called).
        output_path: Path to the output HDF5 file.
        solver_options: Dict with optional keys:
            solve_time, dt_init, dt_max, adaptive, save_every.
        epsilon_fn: Optional callable epsilon(x, y) -> float, evaluated at each
            mesh site.  Written as a (N,) float64 dataset at ``/epsilon``.
    """
    if device.mesh is None:
        raise ValueError("Device mesh has not been built. Call device.make_mesh() first.")

    mesh = device.mesh
    em = mesh.edge_mesh
    xi = device.layer.coherence_length

    # Dimensionless sites (divide physical points by xi)
    sites = np.asarray(mesh.sites, dtype=np.float64)
    elements = np.asarray(mesh.elements, dtype=np.int64)
    boundary_indices = np.asarray(mesh.boundary_indices, dtype=np.int64)
    areas = np.asarray(mesh.areas, dtype=np.float64)

    with h5py.File(output_path, "w") as f:
        # --- /device group ---
        dev = f.create_group("device")
        dev.attrs["name"] = device.name
        dev.attrs["length_units"] = device.length_units
        dev.attrs["K0"] = float(device.K0.magnitude)
        dev.attrs["A0"] = float(device.A0.magnitude)
        dev.attrs["Bc2"] = float(device.Bc2.magnitude)
        dev.attrs["Lambda"] = float(device.Lambda.magnitude)

        # probe_point_indices
        ppi = device.probe_point_indices
        if ppi is not None:
            dev.create_dataset("probe_point_indices", data=np.array(ppi, dtype=np.int64))

        # --- /device/layer group ---
        layer = dev.create_group("layer")
        layer.attrs["coherence_length"] = float(device.layer.coherence_length)
        layer.attrs["london_lambda"] = float(device.layer.london_lambda)
        layer.attrs["thickness"] = float(device.layer.thickness)
        layer.attrs["u"] = float(device.layer.u)
        layer.attrs["gamma"] = float(device.layer.gamma)
        if device.layer.conductivity is not None:
            layer.attrs["conductivity"] = float(device.layer.conductivity)

        # --- /device/terminals group ---
        terminals_group = dev.create_group("terminals")
        terminal_info = device.terminal_info()
        for ti in terminal_info:
            t_group = terminals_group.create_group(ti.name)
            t_group.create_dataset("site_indices", data=np.asarray(ti.site_indices, dtype=np.int64))
            t_group.create_dataset("edge_indices", data=np.asarray(ti.edge_indices, dtype=np.int64))
            t_group.create_dataset(
                "boundary_edge_indices",
                data=np.asarray(ti.boundary_edge_indices, dtype=np.int64),
            )
            t_group.attrs["length"] = float(ti.length)

        # --- /mesh group ---
        m = f.create_group("mesh")
        m.create_dataset("sites", data=sites)
        m.create_dataset("elements", data=elements)
        m.create_dataset("boundary_indices", data=boundary_indices)
        m.create_dataset("areas", data=areas)

        # --- /mesh/edge_mesh group ---
        em_group = m.create_group("edge_mesh")
        em_group.create_dataset("centers", data=np.asarray(em.centers, dtype=np.float64))
        em_group.create_dataset("edges", data=np.asarray(em.edges, dtype=np.int64))
        em_group.create_dataset("edge_lengths", data=np.asarray(em.edge_lengths, dtype=np.float64))
        em_group.create_dataset("dual_edge_lengths", data=np.asarray(em.dual_edge_lengths, dtype=np.float64))

        # --- /epsilon (optional) ---
        if epsilon_fn is not None:
            epsilon = np.empty(len(sites), dtype=np.float64)
            for i in range(len(sites)):
                # epsilon_fn expects physical coordinates (length_units)
                x_phys = sites[i, 0] * xi
                y_phys = sites[i, 1] * xi
                epsilon[i] = float(epsilon_fn(x_phys, y_phys))
            f.create_dataset("epsilon", data=epsilon)

        # --- /options (optional) ---
        if solver_options:
            opts = f.create_group("options")
            for key in ("solve_time", "dt_init", "dt_max", "adaptive", "save_every"):
                if key in solver_options:
                    opts.attrs[key] = solver_options[key]


def write_cpp_mesh_from_data(mesh_data: dict, output_path: str):
    """Write cpp-tdgl HDF5 from a mesh_data dict (as produced by build_rectangular_device).

    This is a convenience wrapper when you already have the serialised mesh_data dict
    (e.g. from mesh_meta.json) rather than a live Device object.

    Args:
        mesh_data: Dict with keys: sites, elements, boundary_indices, areas,
            edge_mesh, terminals, layer, device_constants, probe_indices.
        output_path: Path to the output HDF5 file.
    """
    sites = np.asarray(mesh_data["sites"], dtype=np.float64)
    # Sites in mesh_data are in physical units; convert to dimensionless
    xi = mesh_data["layer"]["coherence_length"]
    sites_dimless = sites / xi

    elements = np.asarray(mesh_data["elements"], dtype=np.int64)
    boundary_indices = np.asarray(mesh_data["boundary_indices"], dtype=np.int64)
    areas = np.asarray(mesh_data["areas"], dtype=np.float64)
    em_dict = mesh_data["edge_mesh"]

    dc = mesh_data["device_constants"]

    with h5py.File(output_path, "w") as f:
        dev = f.create_group("device")
        dev.attrs["name"] = dc["name"]
        dev.attrs["length_units"] = dc["length_units"]
        dev.attrs["K0"] = dc["K0"]
        dev.attrs["A0"] = dc["A0"]
        dev.attrs["Bc2"] = dc["Bc2"]
        dev.attrs["Lambda"] = dc["Lambda"]

        probe_indices = mesh_data.get("probe_indices")
        if probe_indices:
            dev.create_dataset("probe_point_indices", data=np.array(probe_indices, dtype=np.int64))

        layer = dev.create_group("layer")
        ld = mesh_data["layer"]
        layer.attrs["coherence_length"] = ld["coherence_length"]
        layer.attrs["london_lambda"] = ld["london_lambda"]
        layer.attrs["thickness"] = ld["thickness"]
        layer.attrs["u"] = ld["u"]
        layer.attrs["gamma"] = ld["gamma"]

        terminals_group = dev.create_group("terminals")
        for t in mesh_data["terminals"]:
            t_group = terminals_group.create_group(t["name"])
            t_group.create_dataset("site_indices", data=np.asarray(t["site_indices"], dtype=np.int64))
            t_group.create_dataset("edge_indices", data=np.asarray(t["edge_indices"], dtype=np.int64))
            t_group.create_dataset(
                "boundary_edge_indices",
                data=np.asarray(t["boundary_edge_indices"], dtype=np.int64),
            )
            t_group.attrs["length"] = t["length"]

        m = f.create_group("mesh")
        m.create_dataset("sites", data=sites_dimless)
        m.create_dataset("elements", data=elements)
        m.create_dataset("boundary_indices", data=boundary_indices)
        m.create_dataset("areas", data=areas)

        em_group = m.create_group("edge_mesh")
        em_group.create_dataset("centers", data=np.asarray(em_dict["centers"], dtype=np.float64))
        em_group.create_dataset("edges", data=np.asarray(em_dict["edges"], dtype=np.int64))
        em_group.create_dataset("edge_lengths", data=np.asarray(em_dict["edge_lengths"], dtype=np.float64))
        em_group.create_dataset("dual_edge_lengths", data=np.asarray(em_dict["dual_edge_lengths"], dtype=np.float64))
