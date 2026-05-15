import numpy as np
import pytest
import zarr

from tdgl_data.zarr_store import FilesystemZarrStore


def test_create_append_and_read_frame(tmp_path):
    store = FilesystemZarrStore(tmp_path)
    store.create_run_store("run-1", grid_shape=(4, 3), fields=("psi_real", "psi_imag", "mu"))

    arrays = {
        "psi_real": np.ones((4, 3), dtype="float32"),
        "psi_imag": np.full((4, 3), 2.0, dtype="float32"),
        "mu": np.full((4, 3), -0.5, dtype="float32"),
    }
    store.append_frame("run-1", 0, arrays)
    loaded = store.read_frame("run-1", 0, fields=("psi_real", "psi_imag", "mu"))

    assert loaded["psi_real"].shape == (4, 3)
    assert loaded["psi_real"].dtype == np.float32
    assert np.allclose(loaded["psi_real"], 1.0)
    assert np.allclose(loaded["psi_imag"], 2.0)
    assert np.allclose(loaded["mu"], -0.5)


def test_get_store_uri_is_logical_path(tmp_path):
    store = FilesystemZarrStore(tmp_path)
    assert store.get_store_uri("run-1") == "runs/run-1/frames.zarr"


@pytest.mark.parametrize("run_id", ["", ".", "..", "../outside", "nested/run", r"nested\run"])
def test_unsafe_run_id_is_rejected_without_creating_outside_path(tmp_path, run_id):
    store = FilesystemZarrStore(tmp_path / "root")
    outside = tmp_path / "outside"

    with pytest.raises(ValueError):
        store.create_run_store(run_id, grid_shape=(4, 3), fields=("psi_real",))

    assert not outside.exists()
    assert not (tmp_path / "root" / "runs").exists()


def test_negative_append_and_read_frame_index_rejected(tmp_path):
    store = FilesystemZarrStore(tmp_path)
    store.create_run_store("run-1", grid_shape=(4, 3), fields=("psi_real",))

    with pytest.raises(ValueError):
        store.append_frame("run-1", -1, {"psi_real": np.ones((4, 3), dtype="float32")})

    with pytest.raises(ValueError):
        store.read_frame("run-1", -1, fields=("psi_real",))


def test_append_to_missing_run_fails_without_creating_store(tmp_path):
    store = FilesystemZarrStore(tmp_path)

    with pytest.raises((FileNotFoundError, ValueError)):
        store.append_frame("missing", 0, {"psi_real": np.ones((4, 3), dtype="float32")})

    assert not (tmp_path / "runs" / "missing").exists()


@pytest.mark.parametrize(
    "arrays",
    [
        {"psi_real": np.ones((4, 3), dtype="float32")},
        {
            "psi_real": np.ones((4, 3), dtype="float32"),
            "psi_imag": np.ones((4, 3), dtype="float32"),
            "mu": np.ones((4, 3), dtype="float32"),
            "extra": np.ones((4, 3), dtype="float32"),
        },
        {
            "psi_real": np.ones((4, 3), dtype="float32"),
            "psi_imag": np.ones((2, 2), dtype="float32"),
            "mu": np.ones((4, 3), dtype="float32"),
        },
    ],
)
def test_append_validation_failures_do_not_partially_write(tmp_path, arrays):
    store = FilesystemZarrStore(tmp_path)
    store.create_run_store("run-1", grid_shape=(4, 3), fields=("psi_real", "psi_imag", "mu"))

    with pytest.raises(ValueError):
        store.append_frame("run-1", 0, arrays)

    root = zarr.open_group(str(tmp_path / "runs" / "run-1" / "frames.zarr"), mode="r")
    assert root["psi_real"].shape == (0, 4, 3)
    assert root["psi_imag"].shape == (0, 4, 3)
    assert root["mu"].shape == (0, 4, 3)


def test_create_run_store_is_idempotent_for_same_shape_and_rejects_incompatible_shape(tmp_path):
    store = FilesystemZarrStore(tmp_path)

    first_uri = store.create_run_store("run-1", grid_shape=(4, 3), fields=("psi_real", "psi_imag"))
    second_uri = store.create_run_store("run-1", grid_shape=(4, 3), fields=("psi_real", "psi_imag"))

    assert second_uri == first_uri
    with pytest.raises(ValueError):
        store.create_run_store("run-1", grid_shape=(3, 4), fields=("psi_real", "psi_imag"))
