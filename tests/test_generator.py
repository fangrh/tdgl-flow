import pytest
from fastapi.testclient import TestClient

from tdgl_generator.app import create_app


@pytest.fixture
def gen_client():
    app = create_app(data_service_url="http://localhost:9999")
    with TestClient(app) as client:
        yield client


def test_generator_returns_html(gen_client):
    response = gen_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TDGL Data Generator" in response.text


def test_generator_page_has_form_controls(gen_client):
    response = gen_client.get("/")
    assert response.status_code == 200
    assert 'id="jeMin"' in response.text
    assert 'id="jeMax"' in response.text
    assert 'id="jeCount"' in response.text
    assert 'id="framesPerJe"' in response.text
    assert 'id="gridY"' in response.text
    assert 'id="gridX"' in response.text
    assert 'id="delaySeconds"' in response.text
    assert 'id="startBtn"' in response.text
    assert 'id="stopBtn"' in response.text


def test_status_returns_idle(gen_client):
    response = gen_client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["status"] == "idle"


def test_stop_when_idle_returns_idle(gen_client):
    response = gen_client.post("/api/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "idle"