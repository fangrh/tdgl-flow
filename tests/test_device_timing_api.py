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


def test_workflow_submit_rejects_unknown_solver(client):
    resp = client.post("/api/workflows/submit", json={
        "solver_type": "unknown",
        "device_params": {},
        "timing_params": {},
        "mesh_data": {"num_sites": 1, "sites": [[0.0, 0.0]], "elements": []},
        "schedule": {"n_steps": 1},
        "solver_options": {},
        "resources": {"cpu_cores": 1, "memory_gb": 1},
    })

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unsupported solver_type: unknown"


def test_workflow_delete_run_proxies_to_data_service(client, monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 204
        content = b""

    async def fake_delete(self, url, **kwargs):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.delete", fake_delete)

    resp = client.delete("/api/runs/run-123")

    assert resp.status_code == 204
    assert calls
    assert calls[0].endswith("/api/runs/run-123")
