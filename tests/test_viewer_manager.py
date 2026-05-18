"""Tests for viewer-manager session API (DB layer only, K8s mocked)."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from viewer_manager.app import create_app
from viewer_manager.models import Base
from viewer_manager.db import create_engine_from_url, create_session_factory


@pytest.fixture
def client():
    app = create_app(database_url="sqlite+pysqlite:///test_viewer.db", create_schema=True, start_cleanup=False)
    with TestClient(app) as c:
        yield c
    import os
    if os.path.exists("test_viewer.db"):
        os.remove("test_viewer.db")


def test_create_session_returns_starting(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-1", "viewer_type": "data-viewer"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "STARTING"
    assert data["run_id"] == "run-1"
    assert data["active_clients"] == 1
    assert data["session_url"] is not None


def test_reuse_existing_session(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))

        resp1 = client.post("/api/viewer-sessions", json={"run_id": "run-1", "viewer_type": "data-viewer"})
        assert resp1.status_code == 200
        sid = resp1.json()["session_id"]

        sf = client.app.state.session_factory
        from viewer_manager.models import ViewerSession
        with sf() as db:
            vs = db.get(ViewerSession, sid)
            vs.status = "READY"
            db.commit()

        resp2 = client.post("/api/viewer-sessions", json={"run_id": "run-1", "viewer_type": "data-viewer"})
        assert resp2.status_code == 200
        assert resp2.json()["session_id"] == sid
        assert resp2.json()["active_clients"] == 2


def test_get_session_checks_pod_status(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-2", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    with patch("viewer_manager.app.is_pod_ready", return_value=True):
        resp = client.get(f"/api/viewer-sessions/{sid}")
    assert resp.json()["status"] == "READY"


def test_heartbeat_updates_access_time(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-3", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    resp = client.post(f"/api/viewer-sessions/{sid}/heartbeat")
    assert resp.status_code == 200


def test_release_decrements_clients(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-4", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    resp = client.post(f"/api/viewer-sessions/{sid}/release")
    assert resp.json()["active_clients"] == 0


def test_delete_session(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-5", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    with patch("viewer_manager.k8s_client.delete_viewer_pod"):
        resp = client.delete(f"/api/viewer-sessions/{sid}")
    assert resp.status_code == 204


def test_session_not_found(client):
    resp = client.get("/api/viewer-sessions/nonexistent")
    assert resp.status_code == 404
