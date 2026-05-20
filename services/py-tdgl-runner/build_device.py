"""Argo build-device step: generate mesh and write mesh_result.json as artifact.

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

    # Write mesh artifact
    artifact_path = os.path.join(data_dir, "mesh_result.json")
    with open(artifact_path, "w") as f:
        json.dump(mesh_data, f)

    print(f"Device built: {mesh_data['num_sites']} sites, {mesh_data['num_elements']} elements")
    print(f"Artifact written: {artifact_path}")


if __name__ == "__main__":
    main()