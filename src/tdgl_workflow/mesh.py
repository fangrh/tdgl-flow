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

    return {
        "sites": points.tolist(),
        "elements": triangles.tolist(),
        "probe_indices": probe_indices,
        "num_sites": int(len(points)),
        "num_elements": int(len(triangles)),
        "film_width": film_width,
        "film_height": film_height,
        "elec_width": elec_width,
        "elec_height": elec_height,
        "elec_y_offset": elec_y_offset,
        "max_edge_length": max_edge_length,
        "smooth": smooth,
        "probe_points": list(probe_points),
    }