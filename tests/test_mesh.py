import pytest


def test_build_rectangular_device_returns_mesh_data():
    from tdgl_workflow.mesh import build_rectangular_device

    result = build_rectangular_device(
        film_width=10.0,
        film_height=2.0,
        elec_width=0.5,
        elec_height=1.0,
        elec_y_offset=0.0,
        probe_points=[(-3.0, 0.0), (3.0, 0.0)],
        max_edge_length=1.0,
        smooth=100,
    )

    assert "sites" in result
    assert "elements" in result
    assert "probe_indices" in result
    assert "num_sites" in result
    assert "num_elements" in result
    assert isinstance(result["sites"], list)
    assert isinstance(result["elements"], list)
    assert isinstance(result["probe_indices"], list)
    assert result["num_sites"] > 0
    assert result["num_elements"] > 0
    assert len(result["sites"][0]) == 2


def test_build_rectangular_device_probe_indices_valid():
    from tdgl_workflow.mesh import build_rectangular_device

    result = build_rectangular_device(
        film_width=10.0,
        film_height=2.0,
        elec_width=0.5,
        elec_height=1.0,
        elec_y_offset=0.0,
        probe_points=[(-3.0, 0.0), (3.0, 0.0)],
        max_edge_length=1.0,
        smooth=100,
    )

    num_sites = result["num_sites"]
    for idx in result["probe_indices"]:
        assert 0 <= idx < num_sites