"""Argo build-device step: generate mesh and write device.h5 + mesh_meta.json."""
import json
import os
import sys

import h5py
import numpy as np

# tdgl_workflow may be vendored in the Docker image
sys.path.insert(0, "/app/vendor")

from tdgl_workflow.mesh import build_rectangular_device


def main():
    device_params = json.loads(os.environ["DEVICE_PARAMS"])
    data_dir = os.environ.get("DATA_DIR", "/data")

    mesh_data = build_rectangular_device(
        film_width=device_params["film_width"],
        film_height=device_params["film_height"],
        elec_width=device_params["elec_width"],
        elec_height=device_params["elec_height"],
        elec_y_offset=device_params["elec_y_offset"],
        probe_points=[tuple(p) for p in device_params["probe_points"]],
        max_edge_length=device_params["max_edge_length"],
        smooth=device_params.get("smooth", 100),
    )

    # Write mesh_meta.json
    meta_path = os.path.join(data_dir, "mesh_meta.json")
    with open(meta_path, "w") as f:
        json.dump(mesh_data, f)

    # Write device.h5 for C++ solver
    h5_path = os.path.join(data_dir, "device.h5")
    with h5py.File(h5_path, "w") as f:
        dev = f.create_group("device")
        dc = mesh_data["device_constants"]
        for attr in ("name", "length_units", "K0", "A0", "Bc2", "Lambda"):
            if attr in dc:
                dev.attrs[attr] = dc[attr]

        lg = dev.create_group("layer")
        for k, v in mesh_data["layer"].items():
            lg.attrs[k] = v

        dev.create_dataset("probe_point_indices",
                           data=np.array(mesh_data["probe_indices"], dtype=np.int64))

        mg = dev.create_group("mesh")
        mg.create_dataset("sites", data=np.array(mesh_data["sites"], dtype=np.float64))
        mg.create_dataset("elements", data=np.array(mesh_data["elements"], dtype=np.int64))
        mg.create_dataset("boundary_indices",
                          data=np.array(mesh_data["boundary_indices"], dtype=np.int64))
        mg.create_dataset("areas", data=np.array(mesh_data["areas"], dtype=np.float64))

        em = mesh_data["edge_mesh"]
        eg = mg.create_group("edge_mesh")
        eg.create_dataset("centers", data=np.array(em["centers"], dtype=np.float64))
        eg.create_dataset("edges", data=np.array(em["edges"], dtype=np.int64))
        eg.create_dataset("boundary_edge_indices",
                          data=np.array(em["boundary_edge_indices"], dtype=np.int64))
        eg.create_dataset("directions", data=np.array(em["directions"], dtype=np.float64))
        eg.create_dataset("edge_lengths", data=np.array(em["edge_lengths"], dtype=np.float64))
        eg.create_dataset("dual_edge_lengths",
                          data=np.array(em["dual_edge_lengths"], dtype=np.float64))

        tg = dev.create_group("terminals")
        for t in mesh_data["terminals"]:
            tgrp = tg.create_group(t["name"])
            tgrp.attrs["name"] = t["name"]
            tgrp.create_dataset("site_indices",
                                data=np.array(t["site_indices"], dtype=np.int64))
            tgrp.create_dataset("edge_indices",
                                data=np.array(t["edge_indices"], dtype=np.int64))
            tgrp.create_dataset("boundary_edge_indices",
                                data=np.array(t["boundary_edge_indices"], dtype=np.int64))
            tgrp.attrs["length"] = t["length"]

    print(f"Device built: {mesh_data['num_sites']} sites, {mesh_data['num_elements']} elements")


if __name__ == "__main__":
    main()
