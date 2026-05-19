import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from tdgl_data.app import create_app
from tdgl_data.dev_app import create_dev_app


def test_create_and_get_run(client):
    response = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 12})
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


def test_delete_run_removes_database_record(client):
    created = client.post(
        "/api/runs",
        json={"solver_type": "synthetic", "n_sites": 9},
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    frame_body = {
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "psi_imag": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "mu": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    client.post(f"/api/runs/{run_id}/frames", json=frame_body)

    deleted = client.delete(f"/api/runs/{run_id}")

    assert deleted.status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.get(f"/api/runs/{run_id}/timeline").status_code == 404


def test_delete_missing_run_returns_404(client):
    response = client.delete("/api/runs/not-found")

    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"


def test_list_runs_returns_created_runs(client):
    first = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 12})
    second = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 10})

    response = client.get("/api/runs")

    assert response.status_code == 200
    run_ids = {run["run_id"] for run in response.json()}
    assert {first.json()["run_id"], second.json()["run_id"]} <= run_ids


def test_create_run_response_includes_metadata(client):
    payload = {
        "solver_type": "synthetic",
        "n_sites": 12,
        "device_params": {"length": 12},
        "timing_params": {"dt": 0.25},
        "metadata": {"label": "smoke"},
    }

    response = client.post("/api/runs", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["mesh_metadata"]["n_sites"] == 12
    assert body["n_sites"] == 12
    assert "zarr_root" not in body
    assert body["device_params"] == {"length": 12}
    assert body["timing_params"] == {"dt": 0.25}
    assert body["metadata"] == {"label": "smoke"}


def test_create_schema_false_preserves_missing_schema_boundary(tmp_path):
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=str(tmp_path / "zarr"),
        create_schema=False,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/runs",
            json={"solver_type": "synthetic", "n_sites": 12},
        )

    assert response.status_code == 500


def test_cors_uses_settings_allow_origins(monkeypatch, tmp_path):
    monkeypatch.setenv("TDGL_CORS_ALLOW_ORIGINS", '["https://client.test"]')
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=str(tmp_path / "zarr"),
        create_schema=True,
    )

    with TestClient(app) as client:
        response = client.get("/api/runs", headers={"Origin": "https://client.test"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.test"


def test_viewer_returns_html(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TDGL Heatmap Viewer" in response.text


def test_viewer_includes_iv_curve_plot(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert 'id="ivPlot"' in response.text
    assert 'I-V curve' in response.text
    assert "renderIvCurve" in response.text
    assert "/iv" in response.text


def test_viewer_includes_fixed_global_colorbars(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert "colorbar" in response.text
    assert "adaptivePsiBounds" in response.text
    assert "renderPsiHeatmap" in response.text
    assert "renderMuHeatmap" in response.text


def test_viewer_includes_plotly_heatmap_rendering(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert "heatmapLayout" in response.text
    assert "Plotly.react" in response.text
    assert "colorscale" in response.text


def test_viewer_uses_single_row_plot_layout(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert 'class="plots plots-row"' in response.text


def test_viewer_uses_plotly_iv_plot_rendering(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert "renderIvCurve" in response.text
    assert "lines+markers" in response.text
    assert '"scatter"' in response.text




def test_viewer_heatmap_uses_plotly_scaleanchor(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert "scaleanchor" in response.text
    assert "constrain" in response.text


def test_viewer_is_run_specific_without_dataset_list(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert 'id="runList"' not in response.text
    assert 'class="run-item-delete"' not in response.text
    assert "deleteRun" not in response.text
    assert "URLSearchParams" in response.text
    assert "run_id" in response.text


def test_viewer_has_empty_state_for_missing_run_id(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert "No run selected" in response.text


def test_viewer_playback_step_accepts_unbounded_numeric_skips(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert 'id="playbackStep"' in response.text
    assert 'type="number"' in response.text
    assert 'min="1"' in response.text
    assert "playbackStepSize" in response.text
    assert "nextFrameIndex" in response.text
    assert "state.currentFrameIndex + playbackStepSize()" in response.text
    assert "state.currentFrameIndex - playbackStepSize()" in response.text


def test_viewer_frame_bar_shows_fixed_loaded_frame_count(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert 'id="framePositionValue"' in response.text
    assert "updateFramePositionLabel" in response.text
    assert "`${frameIndex + 1} / ${total}`" in response.text
    assert 'els.framePositionValue.textContent = "0 / 0"' in response.text
    assert 'Frame index' in response.text


def test_viewer_sets_frame_bar_scale_before_loading_first_frame(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    slider_max_index = response.text.index('els.frameSlider.max = String(state.totalFrames - 1)')
    controls_enabled_index = response.text.index("setControlsEnabled(true)")
    assert slider_max_index < controls_enabled_index


def test_viewer_uses_adaptive_psi_colorbars(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert "computePsiBounds" not in response.text
    assert "expandBounds" not in response.text
    assert "adaptivePsiBounds" in response.text


def test_viewer_includes_frame_buffer(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert "frameBuffer" in response.text
    assert "fillBuffer" in response.text
    assert "bufferRadius" in response.text


def test_viewer_includes_playback_speed_control(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert 'id="playbackSpeed"' in response.text


def test_root_redirects_to_viewer(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] in ("/viewer", "viewer")




def test_dev_app_factory_creates_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("TDGL_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'viewer.db'}")
    monkeypatch.setenv("TDGL_ZARR_ROOT", str(tmp_path / "zarr"))

    app = create_dev_app()

    with TestClient(app) as client:
        response = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 4})

    assert response.status_code == 201


def test_append_frame_and_read_timeline_iv_and_frame(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 4})
    run_id = created.json()["run_id"]

    frame_body = {
        "frame_index": 0,
        "time_value": 0.1,
        "je": 1.2,
        "voltage": 0.024,
        "psi_real": [1.0, 0.5, 0.25, 0.0],
        "psi_imag": [0.0, 0.5, 0.75, 1.0],
        "mu": [-0.1, 0.0, 0.1, 0.2],
    }
    appended = client.post(f"/api/runs/{run_id}/frames", json=frame_body)
    assert appended.status_code == 201

    timeline = client.get(f"/api/runs/{run_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["frames"][0]["frame_index"] == 0
    assert timeline.json()["stats"]["mu"]["max"] == pytest.approx(0.2)

    iv_before = client.get(f"/api/runs/{run_id}/iv")
    assert iv_before.status_code == 200
    assert iv_before.json() == []

    iv_resp = client.post(f"/api/runs/{run_id}/iv", json={
        "frame_index": 0,
        "time_value": 0.1,
        "je": 1.2,
        "voltage": 0.024,
    })
    assert iv_resp.status_code == 201

    iv = client.get(f"/api/runs/{run_id}/iv")
    assert iv.status_code == 200
    assert iv.json()[0]["je"] == 1.2

    frame = client.get(f"/api/runs/{run_id}/frames/0")
    assert frame.status_code == 200
    assert frame.json()["arrays"]["psi_real"][0] == 1.0


def test_append_duplicate_frame_returns_409(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 1})
    run_id = created.json()["run_id"]
    body = {
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [0.0],
        "psi_imag": [0.0],
        "mu": [0.0],
    }

    assert client.post(f"/api/runs/{run_id}/frames", json=body).status_code == 201
    duplicate = client.post(f"/api/runs/{run_id}/frames", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Frame already exists"


def test_append_commit_failure_leaves_no_readable_frame(tmp_path):
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=str(tmp_path / "zarr"),
        create_schema=True,
    )
    engine = app.state.session_factory.kw["bind"]

    def fail_commit(_connection):
        raise RuntimeError("forced commit failure")

    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post(
            "/api/runs",
            json={"solver_type": "synthetic", "n_sites": 1},
        )
        run_id = created.json()["run_id"]
        event.listen(engine, "commit", fail_commit)
        try:
            response = client.post(
                f"/api/runs/{run_id}/frames",
                json={
                    "frame_index": 0,
                    "time_value": 0.0,
                    "je": 0.0,
                    "voltage": 0.0,
                    "psi_real": [5.0],
                    "psi_imag": [0.0],
                    "mu": [0.0],
                },
            )
        finally:
            event.remove(engine, "commit", fail_commit)

        assert response.status_code == 500
        assert client.get(f"/api/runs/{run_id}/frames/0").status_code == 404
        timeline = client.get(f"/api/runs/{run_id}/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["frames"] == []
        assert timeline.json()["stats"] == {}


def test_append_duplicate_frame_does_not_alter_existing_stored_frame(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 1})
    run_id = created.json()["run_id"]
    original = {
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [1.0],
        "psi_imag": [0.0],
        "mu": [0.0],
    }
    duplicate = {**original, "psi_real": [9.0]}

    assert client.post(f"/api/runs/{run_id}/frames", json=original).status_code == 201
    response = client.post(f"/api/runs/{run_id}/frames", json=duplicate)
    frame = client.get(f"/api/runs/{run_id}/frames/0")

    assert response.status_code == 409
    assert frame.status_code == 200
    assert frame.json()["arrays"]["psi_real"] == [1.0]


@pytest.mark.parametrize(
    "body_update",
    [
        {"psi_real": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
        {"psi_real": [1.0]},
    ],
)
def test_append_frame_rejects_wrong_length_arrays(client, body_update):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 4})
    run_id = created.json()["run_id"]
    body = {
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [1.0, 2.0, 3.0, 4.0],
        "psi_imag": [0.0, 0.0, 0.0, 0.0],
        "mu": [0.0, 0.0, 0.0, 0.0],
        **body_update,
    }

    response = client.post(f"/api/runs/{run_id}/frames", json=body)

    assert response.status_code == 422


@pytest.mark.parametrize("missing_field", ["psi_real", "psi_imag", "mu"])
def test_append_frame_rejects_missing_array_fields(client, missing_field):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 1})
    run_id = created.json()["run_id"]
    body = {
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [0.0],
        "psi_imag": [0.0],
        "mu": [0.0],
    }
    body.pop(missing_field)

    response = client.post(f"/api/runs/{run_id}/frames", json=body)

    assert response.status_code == 422


@pytest.mark.parametrize("frame_index", [-1, True, 1.2, "0"])
def test_append_frame_rejects_invalid_frame_index(client, frame_index):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 1})
    run_id = created.json()["run_id"]
    response = client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": frame_index,
            "time_value": 0.0,
            "je": 0.0,
            "voltage": 0.0,
            "psi_real": [0.0],
            "psi_imag": [0.0],
            "mu": [0.0],
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("frame_index", ["-1", "true", "1.2"])
def test_read_frame_rejects_invalid_frame_index(client, frame_index):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 1})
    run_id = created.json()["run_id"]

    response = client.get(f"/api/runs/{run_id}/frames/{frame_index}")

    assert response.status_code == 422


def test_append_frame_stores_frame_stats(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 4})
    run_id = created.json()["run_id"]

    response = client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": 0,
            "time_value": 0.1,
            "je": 1.0,
            "voltage": 0.03,
            "psi_real": [1.0, 0.5, 0.25, 0.0],
            "psi_imag": [0.0, 0.5, 0.75, 1.0],
            "mu": [-0.1, 0.0, 0.1, 0.2],
        },
    )
    assert response.status_code == 201
    from tdgl_data.repository import get_frame
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        frame = get_frame(session, run_id, 0)
        assert frame is not None
        assert frame.frame_stats is not None
        assert "psi_real" in frame.frame_stats
        assert "psi_imag" in frame.frame_stats
        assert "mu" in frame.frame_stats
        assert frame.frame_stats["psi_real"]["min"] == 0.0
        assert frame.frame_stats["psi_real"]["max"] == 1.0
        assert frame.frame_stats["mu"]["min"] == pytest.approx(-0.1)
        assert frame.frame_stats["mu"]["max"] == pytest.approx(0.2)


def test_timeline_stats_use_cached_frame_stats(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 4})
    run_id = created.json()["run_id"]

    client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": 0,
            "time_value": 0.0,
            "je": 0.0,
            "voltage": 0.0,
            "psi_real": [1.0, 2.0, 3.0, 4.0],
            "psi_imag": [0.0, 0.0, 0.0, 0.0],
            "mu": [0.5, 1.0, 1.5, 2.0],
        },
    )
    client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": 1,
            "time_value": 0.1,
            "je": 1.0,
            "voltage": 0.03,
            "psi_real": [-1.0, 0.0, 0.0, 5.0],
            "psi_imag": [0.0, 0.0, 0.0, 0.0],
            "mu": [-1.0, 0.0, 0.0, 3.0],
        },
    )

    timeline = client.get(f"/api/runs/{run_id}/timeline")
    assert timeline.status_code == 200
    stats = timeline.json()["stats"]

    assert stats["psi_real"]["min"] == pytest.approx(-1.0)
    assert stats["psi_real"]["max"] == pytest.approx(5.0)
    assert stats["mu"]["min"] == pytest.approx(-1.0)
    assert stats["mu"]["max"] == pytest.approx(3.0)


def test_sse_returns_404_for_unknown_run(client):
    response = client.get("/api/runs/nonexistent/events")
    assert response.status_code == 404


def test_sse_endpoint_exists_for_valid_run(client):
    created = client.post(
        "/api/runs",
        json={"solver_type": "synthetic", "n_sites": 4},
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    response = client.head(f"/api/runs/{run_id}/events")
    assert response.status_code != 404


def test_viewer_supports_live_updates(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert "EventSource" in response.text


def test_frame_arrays_roundtrip_through_zarr(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "n_sites": 4})
    run_id = created.json()["run_id"]

    frame_body = {
        "frame_index": 0,
        "time_value": 0.5,
        "je": 1.0,
        "voltage": 0.02,
        "psi_real": [1.0, 2.0, 3.0, 4.0],
        "psi_imag": [-1.0, -2.0, -3.0, -4.0],
        "mu": [0.1, 0.2, 0.3, 0.4],
    }
    appended = client.post(f"/api/runs/{run_id}/frames", json=frame_body)
    assert appended.status_code == 201

    resp = client.get(f"/api/runs/{run_id}/frames/0")
    assert resp.status_code == 200
    arrays = resp.json()["arrays"]
    assert np.allclose(arrays["psi_real"], [1.0, 2.0, 3.0, 4.0])
    assert np.allclose(arrays["psi_imag"], [-1.0, -2.0, -3.0, -4.0])
    assert np.allclose(arrays["mu"], [0.1, 0.2, 0.3, 0.4])


def test_viewer_supports_live_updates(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert "EventSource" in response.text
    assert "frame_available" in response.text
    assert "autoFollow" in response.text
    assert "closeEventSource" in response.text
    assert "openEventSource" in response.text


def test_get_mesh_returns_stored_mesh_data(client):
    created = client.post(
        "/api/runs",
        json={
            "solver_type": "cpp-tdgl",
            "n_sites": 3,
            "mesh_sites": [[0.0, 0.0], [1.0, 0.0], [0.5, 0.8]],
            "mesh_elements": [[0, 1, 2]],
            "device_params": {"mesh": {"probe_indices": [0, 2]}},
        },
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    response = client.get(f"/api/runs/{run_id}/mesh")
    assert response.status_code == 200
    data = response.json()
    assert data["sites"] == [[0.0, 0.0], [1.0, 0.0], [0.5, 0.8]]
    assert data["elements"] == [[0, 1, 2]]
    assert data["probe_indices"] == [0, 2]
    assert data["n_sites"] == 3


def test_get_mesh_returns_404_when_no_mesh(client):
    created = client.post(
        "/api/runs",
        json={"solver_type": "synthetic", "n_sites": 4},
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    response = client.get(f"/api/runs/{run_id}/mesh")
    assert response.status_code == 404
    assert response.json()["detail"] == "Run has no mesh data"


def test_get_mesh_returns_404_for_unknown_run(client):
    response = client.get("/api/runs/nonexistent/mesh")
    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"


def test_frame_append_does_not_create_iv_until_explicit_post(client):
    created = client.post("/api/runs", json={"solver_type": "cpp-tdgl", "n_sites": 2})
    run_id = created.json()["run_id"]

    frame_body = {
        "frame_index": 0,
        "time_value": 0.5,
        "je": 1.0,
        "voltage": 0.02,
        "psi_real": [1.0, 1.0],
        "psi_imag": [0.0, 0.0],
        "mu": [0.0, 0.02],
        "frame_stats": {
            "physical_time": 4.5,
            "save_window_index": 0,
            "window_frame_index": 0,
            "voltage_valid": True,
        },
    }
    assert client.post(f"/api/runs/{run_id}/frames", json=frame_body).status_code == 201

    iv_before = client.get(f"/api/runs/{run_id}/iv")
    assert iv_before.status_code == 200
    assert iv_before.json() == []

    iv_resp = client.post(f"/api/runs/{run_id}/iv", json={
        "frame_index": 0,
        "time_value": 0.5,
        "je": 1.0,
        "voltage": 0.02,
    })
    assert iv_resp.status_code == 201

    iv_after = client.get(f"/api/runs/{run_id}/iv")
    assert iv_after.json() == [{
        "frame_index": 0,
        "time_value": 0.5,
        "je": 1.0,
        "voltage": 0.02,
    }]

    frame = client.get(f"/api/runs/{run_id}/frames/0")
    assert frame.status_code == 200
