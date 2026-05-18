import pytest
from unittest.mock import patch, MagicMock
from tdgl_sdk.client import TDGLClient


def test_build_device_calls_api():
    client = TDGLClient("http://test-host")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "sites": [[0, 0], [1, 0], [0.5, 0.8]],
        "elements": [[0, 1, 2]],
        "num_sites": 3,
        "probe_indices": [0, 2],
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = client.build_device(film_width=10, film_height=2)
        mock_post.assert_called_once()
        assert result["num_sites"] == 3


def test_build_timing_calls_api():
    client = TDGLClient("http://test-host")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "steps": [{"je_start": 0, "je_end": 1}],
        "n_steps": 1,
        "solve_time": 5.0,
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = client.build_timing(je_initial=0, je_final=5)
        mock_post.assert_called_once()
        assert result["n_steps"] == 1


def test_get_run_status():
    client = TDGLClient("http://test-host")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"run_id": "abc", "status": "running"}

    with patch("httpx.get", return_value=mock_resp):
        run = client.get_run("abc")
        assert run["status"] == "running"


def test_list_runs():
    client = TDGLClient("http://test-host")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"run_id": "abc"}, {"run_id": "def"}]

    with patch("httpx.get", return_value=mock_resp):
        runs = client.list_runs()
        assert len(runs) == 2


def test_base_url_strips_trailing_slash():
    client = TDGLClient("http://test-host/")
    assert client.base_url == "http://test-host"
