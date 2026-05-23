"""Argo build-device step: generate mesh and write device.pkl as artifact."""
import json
import os
import pickle
import sys

sys.path.insert(0, "/app/vendor")

from tdgl_workflow.mesh import build_rectangular_device


def main():
    try:
        device_params = json.loads(os.environ["DEVICE_PARAMS"])
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Invalid DEVICE_PARAMS: {e}", file=sys.stderr)
        sys.exit(1)

    data_dir = os.environ.get("DATA_DIR", "/data")
    os.makedirs(data_dir, exist_ok=True)

    mesh_data, device = build_rectangular_device(
        film_width=device_params["film_width"],
        film_height=device_params["film_height"],
        elec_width=device_params["elec_width"],
        elec_height=device_params["elec_height"],
        elec_y_offset=device_params["elec_y_offset"],
        probe_points=[tuple(p) for p in device_params["probe_points"]],
        max_edge_length=device_params["max_edge_length"],
        smooth=device_params.get("smooth", 100),
    )

    mesh_meta_path = os.path.join(data_dir, "mesh_meta.json")
    with open(mesh_meta_path, "w") as f:
        json.dump(mesh_data, f)

    device_path = os.path.join(data_dir, "device.pkl")
    with open(device_path, "wb") as f:
        pickle.dump(device, f)

    print(f"Device built: {mesh_data['num_sites']} sites, {mesh_data['num_elements']} elements")
    print(f"Artifacts: {mesh_meta_path}, {device_path}")


if __name__ == "__main__":
    main()
