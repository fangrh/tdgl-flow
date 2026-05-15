import numpy as np

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
