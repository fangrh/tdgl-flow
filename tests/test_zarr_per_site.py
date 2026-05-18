"""Tests for per-site ZarrStore (shape (n_steps, n_sites))."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from tdgl_data.zarr_store import ZarrStore


@pytest.fixture
def store(tmp_path: Path) -> ZarrStore:
    return ZarrStore(tmp_path)


N_SITES = 10


def test_create_run_per_site(store: ZarrStore) -> None:
    """Verify create_run produces arrays with shape (0, n_sites) per field."""
    store.create_run("run-1", n_sites=N_SITES)

    import zarr

    group = zarr.open_group(str(store._run_path("run-1")), mode="r")
    for field in ("psi_real", "psi_imag", "mu"):
        arr = group[field]
        assert arr.shape == (0, N_SITES), f"{field} shape should be (0, {N_SITES}), got {arr.shape}"
        assert arr.dtype == np.float64, f"{field} dtype should be float64, got {arr.dtype}"
        assert arr.chunks == (1, N_SITES), f"{field} chunks should be (1, {N_SITES}), got {arr.chunks}"


def test_append_frame_per_site(store: ZarrStore) -> None:
    """Append one frame and verify values roundtrip exactly."""
    store.create_run("run-2", n_sites=N_SITES)

    data = {
        "psi_real": np.ones(N_SITES) * 1.1,
        "psi_imag": np.ones(N_SITES) * 2.2,
        "mu": np.ones(N_SITES) * 3.3,
    }
    store.append_frame("run-2", frame_index=0, arrays=data)

    result = store.get_frame("run-2", frame_index=0)
    for field in ("psi_real", "psi_imag", "mu"):
        assert result[field].shape == (N_SITES,), f"{field} should be 1D shape ({N_SITES},)"
        np.testing.assert_array_equal(result[field], data[field])


def test_append_multiple_frames(store: ZarrStore) -> None:
    """Append 3 frames and verify each individually."""
    store.create_run("run-3", n_sites=N_SITES)

    frames = []
    for i in range(3):
        frame = {
            "psi_real": np.full(N_SITES, float(i + 1)),
            "psi_imag": np.full(N_SITES, float(i + 10)),
            "mu": np.full(N_SITES, float(i + 100)),
        }
        store.append_frame("run-3", frame_index=i, arrays=frame)
        frames.append(frame)

    for i in range(3):
        result = store.get_frame("run-3", frame_index=i)
        for field in ("psi_real", "psi_imag", "mu"):
            np.testing.assert_array_equal(
                result[field],
                frames[i][field],
                err_msg=f"Frame {i} field {field} mismatch",
            )


def test_get_all_frames(store: ZarrStore) -> None:
    """Verify get_all_frames returns full 2D arrays (n_steps, n_sites)."""
    store.create_run("run-4", n_sites=N_SITES)

    for i in range(3):
        store.append_frame(
            "run-4",
            frame_index=i,
            arrays={
                "psi_real": np.full(N_SITES, float(i)),
                "psi_imag": np.full(N_SITES, float(i + 1)),
                "mu": np.full(N_SITES, float(i + 2)),
            },
        )

    all_data = store.get_all_frames("run-4")
    for field in ("psi_real", "psi_imag", "mu"):
        assert all_data[field].shape == (3, N_SITES), (
            f"{field} full shape should be (3, {N_SITES}), got {all_data[field].shape}"
        )

    # Spot-check first and last frame values
    np.testing.assert_array_equal(all_data["psi_real"][0], np.zeros(N_SITES))
    np.testing.assert_array_equal(all_data["psi_real"][2], np.full(N_SITES, 2.0))


def test_delete_run(store: ZarrStore) -> None:
    """Verify delete_run removes the run directory."""
    store.create_run("run-5", n_sites=N_SITES)
    run_dir = store._run_path("run-5").parent
    assert run_dir.exists(), "Run directory should exist after create_run"

    store.delete_run("run-5")
    assert not run_dir.exists(), "Run directory should be removed after delete_run"
