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
