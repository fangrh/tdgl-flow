from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workflow_client() -> Iterator[TestClient]:
    from tdgl_workflow.app import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client


def test_root_redirects_to_device(workflow_client):
    response = workflow_client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/device"


def test_device_page_loads(workflow_client):
    response = workflow_client.get("/device")
    assert response.status_code == 200
    assert "Film Width" in response.text


def test_device_preview_returns_plot(workflow_client):
    response = workflow_client.post("/device", data={
        "film_width": "10", "film_height": "2",
        "elec_width": "0.5", "elec_height": "1", "elec_y_offset": "0",
        "probe1_x": "-3", "probe1_y": "0",
        "probe2_x": "3", "probe2_y": "0",
        "max_edge_length": "1.0", "smooth": "100",
        "action": "preview",
    })
    assert response.status_code == 200
    assert "data:image/png;base64," in response.text


def test_timing_page_loads(workflow_client):
    response = workflow_client.get("/timing")
    assert response.status_code == 200
    # When no device is configured, shows a warning message
    assert "Configure a device first" in response.text or "Je Initial" in response.text


def test_timing_preview_returns_plot(workflow_client):
    # First set up device in session
    workflow_client.post("/device", data={
        "film_width": "10", "film_height": "2",
        "elec_width": "0.5", "elec_height": "1", "elec_y_offset": "0",
        "probe1_x": "-3", "probe1_y": "0",
        "probe2_x": "3", "probe2_y": "0",
        "max_edge_length": "1.0", "smooth": "100",
        "action": "preview",
    })
    response = workflow_client.post("/timing", data={
        "je_initial": "0", "je_final": "5", "je_step": "1",
        "ramp_time": "0.5", "stable_time": "2", "save_time": "1",
        "action": "preview",
    })
    assert response.status_code == 200
    assert "data:image/png;base64," in response.text


def test_simulate_page_shows_warning_without_session(workflow_client):
    response = workflow_client.get("/simulate")
    assert response.status_code == 200
    assert "device" in response.text.lower() or "Configure" in response.text


def test_simulate_page_renders_solver_selector(workflow_client):
    workflow_client.post("/device", data={
        "film_width": "10", "film_height": "2",
        "elec_width": "0.5", "elec_height": "1",
        "elec_y_offset": "0", "probe1_x": "-2",
        "probe1_y": "0", "probe2_x": "2", "probe2_y": "0",
        "max_edge_length": "1", "smooth": "100",
    })
    workflow_client.post("/timing", data={
        "je_initial": "0", "je_final": "2", "je_step": "1",
        "ramp_time": "1", "stable_time": "3", "save_time": "1",
    })

    response = workflow_client.get("/simulate")

    assert response.status_code == 200
    assert 'name="solver_type"' in response.text
    assert 'value="cpp-tdgl"' in response.text
    assert 'value="py-tdgl"' in response.text


def test_simulate_page_contains_embedded_viewer_panel(workflow_client):
    response = workflow_client.get("/simulate")

    assert response.status_code == 200
    assert 'id="workflowRunPanel"' in response.text
    assert 'id="viewerFrame"' in response.text
    assert 'getElementById("viewerFrame")' in response.text
    assert "/tdgl/viewer?run_id=" in response.text
    assert "deleteWorkflowRun" in response.text