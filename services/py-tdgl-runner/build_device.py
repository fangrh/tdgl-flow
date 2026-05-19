"""Argo build-device step: generate mesh and write mesh_meta.json.

Outputs Python tdgl native mesh format for the simulate step.
"""
import json
import os
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

    # Write mesh_meta.json (Python tdgl native format)
    meta_path = os.path.join(data_dir, "mesh_meta.json")
    with open(meta_path, "w") as f:
        json.dump(mesh_data, f)

    print(f"Device built: {mesh_data['num_sites']} sites, {mesh_data['num_elements']} elements")

    # Output mesh JSON to stdout for consumers (e.g. marimo via pod logs)
    print("MESH_JSON_START")
    print(json.dumps(mesh_data))
    print("MESH_JSON_END")


if __name__ == "__main__":
    main()