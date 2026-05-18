import numpy as np
import tdgl
from tdgl.geometry import box


def build_rectangular_device(
    *,
    film_width: float,
    film_height: float,
    elec_width: float,
    elec_height: float,
    elec_y_offset: float,
    probe_points: list[tuple[float, float]],
    max_edge_length: float,
    smooth: int = 100,
) -> dict:
    layer = tdgl.Layer(coherence_length=0.5, london_lambda=2.0, thickness=0.1, gamma=1)

    film = tdgl.Polygon("film", points=box(film_width, film_height))

    source = tdgl.Polygon(
        "source", points=box(elec_width, elec_height)
    ).translate(dx=-film_width / 2, dy=elec_y_offset)
    drain = tdgl.Polygon(
        "drain", points=box(elec_width, elec_height)
    ).translate(dx=film_width / 2, dy=elec_y_offset)

    device = tdgl.Device(
        "rectangular_device",
        layer=layer,
        film=film,
        terminals=[source, drain],
        probe_points=probe_points,
    )
    device.make_mesh(max_edge_length=max_edge_length, smooth=smooth)

    points = np.asarray(device.points)
    triangles = np.asarray(device.triangles)

    probe_indices = []
    for px, py in probe_points:
        distances = np.sqrt((points[:, 0] - px) ** 2 + (points[:, 1] - py) ** 2)
        probe_indices.append(int(np.argmin(distances)))

    terminal_info = device.terminal_info()
    em = device.mesh.edge_mesh

    return {
        "sites": points.tolist(),
        "elements": triangles.tolist(),
        "probe_indices": probe_indices,
        "num_sites": int(len(points)),
        "num_elements": int(len(triangles)),
        "boundary_indices": device.mesh.boundary_indices.tolist(),
        "areas": device.mesh.areas.tolist(),
        "edge_mesh": {
            "centers": em.centers.tolist(),
            "edges": em.edges.tolist(),
            "boundary_edge_indices": em.boundary_edge_indices.tolist(),
            "directions": em.directions.tolist(),
            "edge_lengths": em.edge_lengths.tolist(),
            "dual_edge_lengths": em.dual_edge_lengths.tolist(),
        },
        "terminals": [
            {
                "name": t.name,
                "site_indices": t.site_indices.tolist(),
                "edge_indices": t.edge_indices.tolist(),
                "boundary_edge_indices": t.boundary_edge_indices.tolist(),
                "length": float(t.length),
            }
            for t in terminal_info
        ],
        "layer": {
            "coherence_length": float(device.layer.coherence_length),
            "london_lambda": float(device.layer.london_lambda),
            "thickness": float(device.layer.thickness),
            "u": float(device.layer.u),
            "gamma": float(device.layer.gamma),
        },
        "device_constants": {
            "K0": float(device.K0.magnitude),
            "A0": float(device.A0.magnitude),
            "Bc2": float(device.Bc2.magnitude),
            "Lambda": float(device.Lambda.magnitude),
            "name": device.name,
            "length_units": device.length_units,
        },
        "film_width": film_width,
        "film_height": film_height,
        "elec_width": elec_width,
        "elec_height": elec_height,
        "elec_y_offset": elec_y_offset,
        "max_edge_length": max_edge_length,
        "smooth": smooth,
        "probe_points": list(probe_points),
    }