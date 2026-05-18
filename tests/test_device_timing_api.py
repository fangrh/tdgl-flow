import pytest
from fastapi.testclient import TestClient
from tdgl_workflow.app import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_device_build_returns_mesh(client):
    resp = client.post("/api/device/build", json={
        "film_width": 10.0,
        "film_height": 2.0,
        "elec_width": 0.5,
        "elec_height": 1.0,
        "elec_y_offset": 0.0,
        "probe_points": [[-2.0, 0.0], [2.0, 0.0]],
        "max_edge_length": 1.0,
        "smooth": 100,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "sites" in data
    assert "elements" in data
    assert "probe_indices" in data
    assert "num_sites" in data
    assert data["num_sites"] > 0
    assert len(data["sites"]) == data["num_sites"]


def test_timing_build_returns_steps(client):
    resp = client.post("/api/timing/build", json={
        "mode": "simple",
        "je_initial": 0.0,
        "je_final": 5.0,
        "je_step": 1.0,
        "ramp_time": 1.0,
        "stable_time": 5.0,
        "save_time": 3.0,
        "ramp_down": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "steps" in data
    assert len(data["steps"]) == 5
    assert data["n_steps"] == 5
    assert data["solve_time"] > 0
