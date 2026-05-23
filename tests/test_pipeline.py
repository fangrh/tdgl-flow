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