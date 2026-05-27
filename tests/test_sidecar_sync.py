"""Tests for sidecar sync helper functions."""
import json
import os
import tempfile

import numpy as np
import pytest

from tdgl_sdk.sidecar_sync import (
    build_iv_data,
    build_viewer_index,
    rsync_sidecars,
    upload_to_minio,
)


def _make_sidecar(path, psi_size=100, v_t=0.0, i_t=0.0, step=0, time_val=0.0):
    np.savez_compressed(
        path,
        psi=np.zeros(psi_size),
        mu=np.zeros(psi_size),
        V_t=np.float64(v_t),
        I_t=np.float64(i_t),
        step=np.int64(step),
        time=np.float64(time_val),
    )


class TestBuildViewerIndex:
    def test_empty_dir_returns_none(self, tmp_path):
        result = build_viewer_index(str(tmp_path))
        assert result is None

    def test_single_frame(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), psi_size=50)
        result = build_viewer_index(str(tmp_path))
        assert result is not None
        assert result["total_frames"] == 1
        assert result["mesh_points"] == 50
        assert result["status"] == "running"

    def test_multiple_frames(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), step=0, time_val=0.0)
        _make_sidecar(str(tmp_path / "frame_000001.npz"), step=100, time_val=10.0)
        _make_sidecar(str(tmp_path / "frame_000002.npz"), step=200, time_val=20.0)
        result = build_viewer_index(str(tmp_path))
        assert result["total_frames"] == 3
        assert len(result["frame_times"]) == 3
        assert result["frame_times"] == [0.0, 10.0, 20.0]

    def test_reads_index_json_status(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"))
        with open(tmp_path / "index.json", "w") as f:
            json.dump({"status": "completed", "completed_steps": 100, "total_steps": 100}, f)
        result = build_viewer_index(str(tmp_path))
        assert result["status"] == "completed"
        assert result["completed_steps"] == 100
        assert result["total_steps"] == 100


class TestBuildIvData:
    def test_empty_dir_returns_none(self, tmp_path):
        result = build_iv_data(str(tmp_path))
        assert result is None

    def test_single_frame(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), v_t=0.5, i_t=10.0, step=0, time_val=5.0)
        result = build_iv_data(str(tmp_path))
        assert result is not None
        assert len(result["points"]) == 1
        assert result["points"][0] == {"i": 10.0, "v": 0.5}
        assert "0" in result["vt_by_step"]
        assert result["vt_by_step"]["0"] == [[5.0, 0.5]]

    def test_dedup_same_current(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), v_t=0.1, i_t=10.0, step=0, time_val=5.0)
        _make_sidecar(str(tmp_path / "frame_000001.npz"), v_t=0.2, i_t=10.0, step=0, time_val=10.0)
        result = build_iv_data(str(tmp_path))
        assert len(result["points"]) == 1
        assert len(result["vt_by_step"]["0"]) == 2

    def test_different_currents(self, tmp_path):
        _make_sidecar(str(tmp_path / "frame_000000.npz"), v_t=0.1, i_t=5.0, step=0, time_val=5.0)
        _make_sidecar(str(tmp_path / "frame_000001.npz"), v_t=0.2, i_t=10.0, step=100, time_val=10.0)
        result = build_iv_data(str(tmp_path))
        assert len(result["points"]) == 2
        assert result["points"][0]["i"] == 5.0
        assert result["points"][1]["i"] == 10.0


class TestRsyncSidecars:
    def test_rsync_sidecars_is_callable(self):
        import inspect
        sig = inspect.signature(rsync_sidecars)
        params = list(sig.parameters.keys())
        assert "remote_dir" in params
        assert "local_dir" in params
        assert "ssh_key" in params
        assert "host" in params


class TestUploadToMinio:
    def test_upload_to_minio_is_callable(self):
        import inspect
        sig = inspect.signature(upload_to_minio)
        params = list(sig.parameters.keys())
        assert "local_path" in params
        assert "bucket" in params
        assert "key" in params