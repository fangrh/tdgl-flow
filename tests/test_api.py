import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from tdgl_data.app import create_app


def test_create_and_get_run(client):
    response = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [4, 3]})
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    loaded = client.get(f"/api/runs/{run_id}")
    assert loaded.status_code == 200
    assert loaded.json()["run_id"] == run_id
    assert loaded.json()["status"] == "created"


def test_missing_run_returns_404(client):
    response = client.get("/api/runs/not-found")
    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"


def test_list_runs_returns_created_runs(client):
    first = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [4, 3]})
    second = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [5, 2]})

    response = client.get("/api/runs")

    assert response.status_code == 200
    run_ids = {run["run_id"] for run in response.json()}
    assert {first.json()["run_id"], second.json()["run_id"]} <= run_ids


def test_create_run_response_includes_metadata_and_logical_zarr_uri(client):
    payload = {
        "solver_type": "synthetic",
        "grid_shape": [4, 3],
        "device_params": {"length": 12},
        "timing_params": {"dt": 0.25},
        "metadata": {"label": "smoke"},
    }

    response = client.post("/api/runs", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["mesh_metadata"]["grid_shape"] == [4, 3]
    assert body["zarr_root"] == f"runs/{body['run_id']}/frames.zarr"
    assert body["device_params"] == {"length": 12}
    assert body["timing_params"] == {"dt": 0.25}
    assert body["metadata"] == {"label": "smoke"}


def test_create_run_creates_zarr_store_under_configured_root(tmp_path):
    zarr_root = tmp_path / "zarr"
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=zarr_root,
        create_schema=True,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={"solver_type": "synthetic", "grid_shape": [4, 3]},
        )

    assert response.status_code == 201
    body = response.json()
    assert (zarr_root / body["zarr_root"] / "psi_real").exists()
    assert (zarr_root / body["zarr_root"] / "psi_imag").exists()
    assert (zarr_root / body["zarr_root"] / "mu").exists()


@pytest.mark.parametrize(
    "grid_shape",
    [[-1, 3], [0, 3], [4, 0], [4, -2], [True, 3], [3, True]],
)
def test_create_run_rejects_invalid_grid_shape_dimensions(client, grid_shape):
    response = client.post(
        "/api/runs",
        json={"solver_type": "synthetic", "grid_shape": grid_shape},
    )

    assert response.status_code == 422


def test_create_schema_false_preserves_missing_schema_boundary(tmp_path):
    database_path = tmp_path / "runs.db"
    app = create_app(
        database_url=f"sqlite+pysqlite:///{database_path}",
        zarr_root=tmp_path / "zarr",
        create_schema=False,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/runs",
            json={"solver_type": "synthetic", "grid_shape": [4, 3]},
        )

    assert response.status_code == 500


def test_cors_uses_settings_allow_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("TDGL_CORS_ALLOW_ORIGINS", '["https://client.test"]')
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=tmp_path / "zarr",
        create_schema=True,
    )

    with TestClient(app) as client:
        response = client.get("/api/runs", headers={"Origin": "https://client.test"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.test"


def test_create_run_cleans_up_zarr_store_when_commit_fails(tmp_path):
    zarr_root = tmp_path / "zarr"
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=zarr_root,
        create_schema=True,
    )
    engine = app.state.session_factory.kw["bind"]

    def fail_commit(_connection):
        raise RuntimeError("forced commit failure")

    event.listen(engine, "commit", fail_commit)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/runs",
                json={"solver_type": "synthetic", "grid_shape": [4, 3]},
            )
    finally:
        event.remove(engine, "commit", fail_commit)

    assert response.status_code == 500
    assert not list(zarr_root.glob("runs/*/frames.zarr"))


def test_append_frame_and_read_timeline_iv_and_frame(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [2, 2]})
    run_id = created.json()["run_id"]

    frame_body = {
        "frame_index": 0,
        "time_value": 0.1,
        "je": 1.2,
        "voltage": 0.024,
        "psi_real": [[1.0, 0.5], [0.25, 0.0]],
        "psi_imag": [[0.0, 0.5], [0.75, 1.0]],
        "mu": [[-0.1, 0.0], [0.1, 0.2]],
    }
    appended = client.post(f"/api/runs/{run_id}/frames", json=frame_body)
    assert appended.status_code == 201

    timeline = client.get(f"/api/runs/{run_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["frames"][0]["frame_index"] == 0
    assert timeline.json()["stats"]["mu"]["max"] == 0.2

    iv = client.get(f"/api/runs/{run_id}/iv")
    assert iv.status_code == 200
    assert iv.json()[0]["je"] == 1.2

    frame = client.get(f"/api/runs/{run_id}/frames/0")
    assert frame.status_code == 200
    assert frame.json()["arrays"]["psi_real"][0][0] == 1.0


def test_append_duplicate_frame_returns_409(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [1, 1]})
    run_id = created.json()["run_id"]
    body = {
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [[0.0]],
        "psi_imag": [[0.0]],
        "mu": [[0.0]],
    }

    assert client.post(f"/api/runs/{run_id}/frames", json=body).status_code == 201
    duplicate = client.post(f"/api/runs/{run_id}/frames", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Frame already exists"
