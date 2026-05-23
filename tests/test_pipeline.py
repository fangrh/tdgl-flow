"""Tests for tdgl_sdk.pipeline — SimulationPipeline."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def pipeline():
    from tdgl_sdk.pipeline import SimulationPipeline
    return SimulationPipeline(
        argo_url="http://localhost:30080",
        minio_endpoint="http://localhost:30900",
        minio_access_key="minioadmin",
        minio_secret_key="minioadmin123",
    )


@pytest.fixture
def device_params():
    return {
        "film_width": 6.0,
        "film_height": 2.0,
        "elec_width": 0.5,
        "elec_height": 1.0,
        "elec_y_offset": 0.0,
        "probe_points": [[-2.0, 0.0], [2.0, 0.0]],
        "max_edge_length": 0.5,
        "smooth": 100,
    }


@pytest.fixture
def timing_params():
    return {
        "je_initial": 0.0,
        "je_final": 0.5,
        "je_step": 0.5,
        "ramp_time": 2.0,
        "stable_time": 3.0,
        "save_time": 2.0,
        "ramp_down": False,
    }


@pytest.fixture
def solver_options():
    return {"dt_init": 1e-4, "dt_max": 0.1, "save_every": 500}


def test_pipeline_config_stores_args(pipeline):
    assert pipeline.argo_url == "http://localhost:30080"
    assert pipeline.namespace == "tdgl"
    assert pipeline.store is not None


def test_pipeline_submit_returns_run_id(pipeline, device_params, timing_params, solver_options):
    mock_wf = MagicMock()
    mock_created = MagicMock()
    mock_created.metadata.name = "py-tdgl-sim-test-wf"
    mock_wf.create.return_value = mock_created

    with patch("hera.workflows.Workflow", return_value=mock_wf):
        run_id, wf_name = pipeline.submit(
            device_params=device_params,
            timing_params=timing_params,
            solver_options=solver_options,
        )

    assert wf_name == "py-tdgl-sim-test-wf"
    assert isinstance(run_id, str)
    assert len(run_id) > 10
    mock_wf.create.assert_called_once()


def test_pipeline_submit_uses_workflow_template(pipeline, device_params, timing_params, solver_options):
    mock_wf = MagicMock()
    mock_wf.create.return_value = MagicMock(
        metadata=MagicMock(name="test-wf")
    )

    with patch("hera.workflows.Workflow", return_value=mock_wf) as MockWF:
        pipeline.submit(
            device_params=device_params,
            timing_params=timing_params,
            solver_options=solver_options,
        )

        call_kwargs = MockWF.call_args
        assert "py-tdgl-sim" in str(call_kwargs)


def test_pipeline_poll_succeeds(pipeline):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": {"phase": "Succeeded"}
    }

    with patch("httpx.get", return_value=mock_response):
        phase = pipeline.poll("test-wf", timeout=30)

    assert phase == "Succeeded"


def test_pipeline_poll_raises_on_failure(pipeline):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": {"phase": "Failed"}
    }
    mock_log_response = MagicMock()
    mock_log_response.text = "error log"

    with patch("httpx.get", side_effect=[mock_response, mock_log_response]):
        with pytest.raises(RuntimeError, match="Failed"):
            pipeline.poll("test-wf", timeout=30)


def test_pipeline_download_returns_path(pipeline):
    with patch.object(pipeline.store, "download_h5", return_value="/tmp/test.h5"):
        h5_path = pipeline.download("test-run-id")

    assert h5_path == "/tmp/test.h5"


def test_pipeline_download_raises_when_missing(pipeline):
    with patch.object(pipeline.store, "download_h5", return_value=None):
        with pytest.raises(FileNotFoundError, match="test-run-id"):
            pipeline.download("test-run-id")


def test_pipeline_verify_returns_report(pipeline, tmp_path):
    """verify() calls examine_h5 + debug_player and returns combined report."""
    import numpy as np
    import h5py

    h5_path = str(tmp_path / "test.h5")
    n_sites = 20
    n_edges = 10
    with h5py.File(h5_path, "w") as f:
        mesh = f.create_group("solution/device/mesh")
        mesh.create_dataset("sites", data=np.random.rand(n_sites, 2))
        mesh.create_dataset("edge_mesh/edges", data=np.zeros((n_edges, 2), dtype=int))
        mesh.create_dataset("edge_mesh/directions", data=np.random.rand(n_edges, 2))
        mesh.create_dataset("edge_mesh/dual_edge_lengths", data=np.random.rand(n_edges))
        data = f.create_group("data")
        for i in range(5):
            g = data.create_group(str(i))
            g.attrs["time"] = float(i) * 0.5
            g.create_dataset("psi", data=np.random.rand(n_sites))
            g.create_dataset("mu", data=np.random.randn(n_sites) * 0.5)
            g.create_dataset("normal_current", data=np.random.randn(n_edges) * 0.1)
            g.create_dataset("supercurrent", data=np.random.randn(n_edges) * 0.1)

    report = pipeline.verify(h5_path)

    assert "examine" in report
    assert "debug" in report
    assert "healthy" in report
    assert report["examine"]["healthy"] is True
    assert report["debug"]["passed"] is True


def test_pipeline_verify_detects_problems(pipeline, tmp_path):
    """verify() reports problems in unhealthy files."""
    import numpy as np
    import h5py

    h5_path = str(tmp_path / "bad.h5")
    with h5py.File(h5_path, "w") as f:
        mesh = f.create_group("solution/device/mesh")
        mesh.create_dataset("sites", data=np.random.rand(10, 2))
        mesh.create_dataset("edge_mesh/edges", data=np.zeros((5, 2), dtype=int))
        mesh.create_dataset("edge_mesh/directions", data=np.random.rand(5, 2))
        mesh.create_dataset("edge_mesh/dual_edge_lengths", data=np.random.rand(5))
        data = f.create_group("data")
        g = data.create_group("0")
        g.attrs["time"] = 0.0
        psi = np.array([1.0, float("nan"), 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        g.create_dataset("psi", data=psi)
        g.create_dataset("mu", data=np.zeros(10))
        g.create_dataset("normal_current", data=np.random.randn(5))
        g.create_dataset("supercurrent", data=np.random.randn(5))

    report = pipeline.verify(h5_path)

    assert report["healthy"] is False
    assert report["examine"]["healthy"] is False
    assert report["debug"]["passed"] is False


def test_pipeline_run_full_flow(pipeline, device_params, timing_params, solver_options, tmp_path):
    """run() submits, polls, downloads, verifies — all steps in sequence."""
    import numpy as np
    import h5py

    mock_wf = MagicMock()
    mock_wf.create.return_value = MagicMock(
        metadata=MagicMock(name="test-wf")
    )

    mock_poll_resp = MagicMock()
    mock_poll_resp.status_code = 200
    mock_poll_resp.json.return_value = {"status": {"phase": "Succeeded"}}

    h5_path = str(tmp_path / "tdgl-test-run.h5")
    n_sites = 20
    n_edges = 10
    with h5py.File(h5_path, "w") as f:
        mesh = f.create_group("solution/device/mesh")
        mesh.create_dataset("sites", data=np.random.rand(n_sites, 2))
        mesh.create_dataset("edge_mesh/edges", data=np.zeros((n_edges, 2), dtype=int))
        mesh.create_dataset("edge_mesh/directions", data=np.random.rand(n_edges, 2))
        mesh.create_dataset("edge_mesh/dual_edge_lengths", data=np.random.rand(n_edges))
        data = f.create_group("data")
        for i in range(3):
            g = data.create_group(str(i))
            g.attrs["time"] = float(i)
            g.create_dataset("psi", data=np.random.rand(n_sites))
            g.create_dataset("mu", data=np.random.randn(n_sites))
            g.create_dataset("normal_current", data=np.random.randn(n_edges))
            g.create_dataset("supercurrent", data=np.random.randn(n_edges))

    with patch("hera.workflows.Workflow", return_value=mock_wf), \
         patch("httpx.get", return_value=mock_poll_resp), \
         patch.object(pipeline.store, "download_h5", return_value=h5_path), \
         patch.object(pipeline.store, "get_run", return_value={"status": "completed", "n_frames": 3}):

        result = pipeline.run(
            device_params=device_params,
            timing_params=timing_params,
            solver_options=solver_options,
        )

    assert result["phase"] == "Succeeded"
    assert result["h5_path"] == h5_path
    assert result["report"]["healthy"] is True
    assert "run_id" in result
    assert "wf_name" in result


def test_pipeline_watch_live_returns_streaming_player(pipeline):
    """watch_live() creates a StreamingTDGLPlayer for a run."""
    mock_store = MagicMock()

    with patch.object(pipeline, "store", mock_store):
        player = pipeline.watch_live("test-run-id", poll_interval=5)

    from tdgl_sdk.viewer._player import StreamingTDGLPlayer
    assert isinstance(player, StreamingTDGLPlayer)
    assert player.run_id == "test-run-id"
    player.stop()