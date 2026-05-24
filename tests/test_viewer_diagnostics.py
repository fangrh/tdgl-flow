"""Tests for tdgl_sdk.viewer.diagnostics — examine_h5 and format_report."""
from pathlib import Path

import h5py
import numpy as np
import pytest

from tdgl_sdk.viewer.diagnostics import examine_h5, format_report


def _write_healthy_h5(path: Path, n_frames: int = 5) -> None:
    n_sites = 20
    n_edges = 30
    with h5py.File(path, "w") as f:
        mesh = f.create_group("solution/device/mesh")
        mesh.create_dataset("sites", data=np.random.rand(n_sites, 2))
        mesh.create_dataset("edge_mesh/edges", data=np.zeros((n_edges, 2), dtype=int))
        mesh.create_dataset("edge_mesh/directions", data=np.random.rand(n_edges, 2))
        mesh.create_dataset("edge_mesh/dual_edge_lengths", data=np.random.rand(n_edges))

        data = f.create_group("data")
        for i in range(n_frames):
            g = data.create_group(str(i))
            g.attrs["time"] = float(i) * 0.5
            psi = np.random.rand(n_sites) + 1j * np.random.rand(n_sites)
            g.create_dataset("psi", data=psi)
            g.create_dataset("mu", data=np.random.randn(n_sites) * 0.5)
            g.create_dataset("normal_current", data=np.random.randn(n_edges) * 0.1)
            g.create_dataset("supercurrent", data=np.random.randn(n_edges) * 0.1)


def _write_nans_h5(path: Path) -> None:
    n_sites = 10
    with h5py.File(path, "w") as f:
        mesh = f.create_group("solution/device/mesh")
        mesh.create_dataset("sites", data=np.random.rand(n_sites, 2))
        mesh.create_dataset("edge_mesh/edges", data=np.zeros((5, 2), dtype=int))
        mesh.create_dataset("edge_mesh/directions", data=np.random.rand(5, 2))
        mesh.create_dataset("edge_mesh/dual_edge_lengths", data=np.random.rand(5))

        data = f.create_group("data")
        g = data.create_group("0")
        g.attrs["time"] = 0.0
        psi = np.array([1.0, float("nan"), 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        g.create_dataset("psi", data=psi)
        g.create_dataset("mu", data=np.zeros(n_sites))


def _write_missing_mesh_h5(path: Path) -> None:
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        g = data.create_group("0")
        g.attrs["time"] = 0.0
        g.create_dataset("psi", data=np.array([1.0]))
        g.create_dataset("mu", data=np.array([0.0]))


def _write_empty_data_h5(path: Path) -> None:
    with h5py.File(path, "w") as f:
        f.create_group("solution/device/mesh")
        f.create_group("data")


def test_examine_healthy_h5(tmp_path):
    h5_path = tmp_path / "healthy.h5"
    _write_healthy_h5(h5_path, n_frames=5)

    report = examine_h5(str(h5_path))

    assert report["healthy"] is True
    assert report["issues"] == []
    assert report["file"]["size_mb"] > 0
    assert report["mesh"]["present"] is True
    assert report["mesh"]["num_sites"] == 20
    assert report["frames"]["total"] == 5
    assert report["frames"]["time_range"] == [0.0, 2.0]
    assert report["iv_available"] is True
    assert "first_psi" in report["data_quality"]
    assert "last_psi" in report["data_quality"]
    assert report["data_quality"]["first_psi"]["nan_count"] == 0
    assert report["data_quality"]["first_mu"]["nan_count"] == 0


def test_examine_nans_detected(tmp_path):
    h5_path = tmp_path / "nans.h5"
    _write_nans_h5(h5_path)

    report = examine_h5(str(h5_path))

    assert report["healthy"] is False
    assert any("NaN" in issue for issue in report["issues"])
    assert report["data_quality"]["first_psi"]["nan_count"] == 1


def test_examine_missing_mesh(tmp_path):
    h5_path = tmp_path / "no_mesh.h5"
    _write_missing_mesh_h5(h5_path)

    report = examine_h5(str(h5_path))

    assert report["healthy"] is False
    assert any("mesh" in issue.lower() for issue in report["issues"])


def test_examine_empty_data(tmp_path):
    h5_path = tmp_path / "empty.h5"
    _write_empty_data_h5(h5_path)

    report = examine_h5(str(h5_path))

    assert report["healthy"] is False
    assert any("no frames" in issue.lower() for issue in report["issues"])
    assert report["frames"]["total"] == 0
    assert report["iv_available"] is False


def test_format_report_returns_string(tmp_path):
    h5_path = tmp_path / "healthy.h5"
    _write_healthy_h5(h5_path, n_frames=3)

    report = examine_h5(str(h5_path))
    text = format_report(report)

    assert isinstance(text, str)
    assert "Healthy: True" in text
    assert "Frames: 3" in text
    assert "Issues: none" in text


def test_format_report_shows_issues(tmp_path):
    h5_path = tmp_path / "nans.h5"
    _write_nans_h5(h5_path)

    report = examine_h5(str(h5_path))
    text = format_report(report)

    assert "Healthy: False" in text
    assert "NaN" in text


# ── Agent API tests: get_status, get_frame_data, get_iv_data ──────────

def _write_player_h5(path: Path, n_frames: int = 3) -> None:
    n_sites = 10
    n_edges = 5
    with h5py.File(path, "w") as f:
        mesh = f.create_group("solution/device/mesh")
        mesh.create_dataset("sites", data=np.random.rand(n_sites, 2))
        mesh.create_dataset("edge_mesh/edges", data=np.zeros((n_edges, 2), dtype=int))
        mesh.create_dataset("edge_mesh/directions", data=np.random.rand(n_edges, 2))
        mesh.create_dataset("edge_mesh/dual_edge_lengths", data=np.random.rand(n_edges))

        data = f.create_group("data")
        for i in range(n_frames):
            g = data.create_group(str(i))
            g.attrs["time"] = float(i)
            g.create_dataset("psi", data=np.random.rand(n_sites))
            g.create_dataset("mu", data=np.random.randn(n_sites))
            g.create_dataset("normal_current", data=np.random.randn(n_edges))
            g.create_dataset("supercurrent", data=np.random.randn(n_edges))


def test_player_get_status(tmp_path):
    h5_path = tmp_path / "player.h5"
    _write_player_h5(h5_path, n_frames=5)

    from tdgl_sdk.viewer._player import create_player
    player = create_player(str(h5_path))

    status = player.get_status()
    assert status["available_frames"] == 5
    assert status["current_step"] == 0
    assert status["playing"] is False
    assert "iv_cache" in status
    assert status["iv_cache"]["cached_points"] >= 0
    assert status["h5_path"] == str(h5_path)

    player.iv_cache.stop()


def test_player_get_frame_data(tmp_path):
    h5_path = tmp_path / "player.h5"
    _write_player_h5(h5_path, n_frames=5)

    from tdgl_sdk.viewer._player import create_player
    player = create_player(str(h5_path))

    data = player.get_frame_data(0)
    assert data["frame_idx"] == 0
    assert data["time"] == 0.0
    assert data["psi_present"] is True
    assert data["mu_present"] is True
    assert data["psi_nan"] == 0
    assert "psi_range" in data
    assert len(data["psi_range"]) == 2
    assert data["normal_current_present"] is True

    data_last = player.get_frame_data(4)
    assert data_last["frame_idx"] == 4
    assert data_last["time"] == 4.0

    player.iv_cache.stop()


def test_player_get_iv_data(tmp_path):
    h5_path = tmp_path / "player.h5"
    _write_player_h5(h5_path, n_frames=5)

    from tdgl_sdk.viewer._player import create_player
    player = create_player(str(h5_path))

    iv = player.get_iv_data()
    assert "n_points" in iv
    assert "I" in iv
    assert "V" in iv
    assert "t" in iv
    assert "I_range" in iv
    assert "V_range" in iv
    assert len(iv["I_range"]) == 2

    player.iv_cache.stop()


def test_player_seek_and_status(tmp_path):
    h5_path = tmp_path / "player.h5"
    _write_player_h5(h5_path, n_frames=5)

    from tdgl_sdk.viewer._player import create_player
    player = create_player(str(h5_path))

    player.show(3)
    status = player.get_status()
    assert status["current_step"] == 3

    player.stop()
    status = player.get_status()
    assert status["current_step"] == 0
    assert status["playing"] is False

    player.iv_cache.stop()


# ── debug_player smoke test ──────────────────────────────────────────

def test_debug_player_passes(tmp_path):
    h5_path = tmp_path / "player.h5"
    _write_player_h5(h5_path, n_frames=10)

    from tdgl_sdk.viewer._player import debug_player
    result = debug_player(str(h5_path), seed=7)

    assert result["passed"] is True
    assert result["total_frames"] == 10
    assert result["errors"] == []
    assert len(result["steps"]) >= 7  # init, play_pause, 4 seeks, iv, seek_beyond, stop

    # Check step actions
    actions = [s["action"] for s in result["steps"]]
    assert "init" in actions
    assert "play_pause" in actions
    assert "seek" in actions
    assert "check_iv" in actions
    assert "seek_beyond" in actions
    assert "stop" in actions

    # All steps should be ok
    for step in result["steps"]:
        assert step["ok"], f"Step {step['action']} failed: {step.get('error')}"

    # Verify seek_beyond snapped to latest available step
    beyond_step = next(s for s in result["steps"] if s["action"] == "seek_beyond")
    assert beyond_step["status"]["current_step"] == 9  # last step in a 10-frame file


def test_debug_player_detects_no_frames(tmp_path):
    h5_path = tmp_path / "empty.h5"
    _write_empty_data_h5(h5_path)

    from tdgl_sdk.viewer._player import debug_player
    result = debug_player(str(h5_path))

    assert result["passed"] is False
    assert len(result["errors"]) > 0  # fails because can't load mesh from empty file


def _write_nans_with_mesh_h5(path: Path) -> None:
    n_sites = 10
    with h5py.File(path, "w") as f:
        mesh = f.create_group("solution/device/mesh")
        mesh.create_dataset("sites", data=np.random.rand(n_sites, 2))
        mesh.create_dataset("edge_mesh/edges", data=np.zeros((5, 2), dtype=int))
        mesh.create_dataset("edge_mesh/directions", data=np.random.rand(5, 2))
        mesh.create_dataset("edge_mesh/dual_edge_lengths", data=np.random.rand(5))

        data = f.create_group("data")
        g = data.create_group("0")
        g.attrs["time"] = 0.0
        psi = np.array([1.0, float("nan"), 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        g.create_dataset("psi", data=psi)
        g.create_dataset("mu", data=np.zeros(n_sites))
        g.create_dataset("normal_current", data=np.random.randn(5))
        g.create_dataset("supercurrent", data=np.random.randn(5))


def test_debug_player_detects_nans(tmp_path):
    h5_path = tmp_path / "nans.h5"
    _write_nans_with_mesh_h5(h5_path)

    from tdgl_sdk.viewer._player import debug_player
    result = debug_player(str(h5_path))

    assert result["passed"] is False
    assert any("NaN" in e for e in result["errors"])


# ── Live mode tests: seek beyond available frames ─────────────────────

def test_live_mode_seek_beyond_snaps_to_latest(tmp_path):
    """Seek to frame 100 when only 5 exist — should land on frame 4."""
    h5_path = tmp_path / "live.h5"
    _write_player_h5(h5_path, n_frames=5)

    from tdgl_sdk.viewer._player import create_player
    player = create_player(str(h5_path), live=True)

    assert player.live is True

    # Seek far beyond available
    player.show(100, wait=False)
    status = player.get_status()

    assert status["current_step"] == 4  # clamped to last step in time_grid
    assert status["available_frames"] == 5
    assert status["live"] is True

    player.iv_cache.stop()


def test_live_mode_sequential_seek(tmp_path):
    """Seek 0, 2, 4, then 999 — last one should snap to 4."""
    h5_path = tmp_path / "live.h5"
    _write_player_h5(h5_path, n_frames=5)

    from tdgl_sdk.viewer._player import create_player
    player = create_player(str(h5_path), live=True)

    for idx in [0, 2, 4]:
        player.show(idx, wait=False)
        assert player.get_status()["current_step"] == idx

    player.show(999, wait=False)
    assert player.get_status()["current_step"] == 4

    player.iv_cache.stop()


def test_non_live_mode_seek_beyond_clamps_to_total(tmp_path):
    """In non-live mode, seeking beyond total still clamps."""
    h5_path = tmp_path / "static.h5"
    _write_player_h5(h5_path, n_frames=5)

    from tdgl_sdk.viewer._player import create_player
    player = create_player(str(h5_path), live=False)

    player.show(100, wait=False)
    status = player.get_status()
    assert status["current_step"] == 4  # clamped to last step in time_grid
    assert status["live"] is False

    player.iv_cache.stop()


def test_draw_iv_only_reads_history_to_current_frame():
    from PIL import Image, ImageDraw

    from tdgl_sdk.viewer._render import _draw_iv

    class FakeIVCache:
        def __init__(self):
            self.I = np.array([0.0, 1.0, 2.0, 1.0, 0.0])
            self.V = np.array([0.0, 0.1, 0.2, 0.15, 0.05])
            self.t = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
            self.ensure_calls = []

        def update_available(self):
            raise AssertionError("render should not prefetch all I-V frames")

        def ensure(self, idx):
            self.ensure_calls.append(idx)

        def arrays(self, upto=None):
            n = len(self.I) if upto is None else int(upto) + 1
            return self.I[:n], self.V[:n], self.t[:n]

        def ranges(self, upto=None):
            I, V, _ = self.arrays(upto=upto)
            return float(I.min()), float(I.max()), float(V.min()), float(V.max())

        def size(self):
            return len(self.I)

    cache = FakeIVCache()
    image = Image.new("RGBA", (760, 470))
    draw = ImageDraw.Draw(image)

    _draw_iv(draw, cache, 2, (14, 252, 746, 454))

    assert cache.ensure_calls == [2]
