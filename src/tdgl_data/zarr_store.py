from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import zarr


class ZarrStore:
    """Stores per-site simulation data as 1D arrays (n_steps, n_sites).

    Each field (psi_real, psi_imag, mu) is a 2D zarr array with shape
    (n_steps, n_sites), where n_sites is the number of mesh sites from
    the C++ solver's irregular triangular mesh.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _run_path(self, run_id: str) -> Path:
        return self.root / "runs" / run_id / "frames.zarr"

    def create_run(self, run_id: str, n_sites: int) -> None:
        """Create a new run store with arrays of shape (0, n_sites)."""
        run_path = self._run_path(run_id)
        run_path.parent.mkdir(parents=True, exist_ok=True)
        group = zarr.open_group(str(run_path), mode="w")
        for field in ("psi_real", "psi_imag", "mu"):
            group.create_array(
                field,
                shape=(0, n_sites),
                dtype="float64",
                chunks=(1, n_sites),
            )

    def append_frame(
        self,
        run_id: str,
        frame_index: int,
        arrays: dict[str, np.ndarray],
    ) -> None:
        """Append a single frame. Each array must be 1D of shape (n_sites,)."""
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r+")
        needed = frame_index + 1
        for field, data in arrays.items():
            ds = group[field]
            if needed > ds.shape[0]:
                ds.resize((needed, ds.shape[1]))
            ds[frame_index] = data

    def get_frame(self, run_id: str, frame_index: int) -> dict[str, np.ndarray]:
        """Return a single frame as a dict of 1D numpy arrays."""
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r")
        return {
            "psi_real": np.array(group["psi_real"][frame_index]),
            "psi_imag": np.array(group["psi_imag"][frame_index]),
            "mu": np.array(group["mu"][frame_index]),
        }

    def get_all_frames(self, run_id: str) -> dict[str, np.ndarray]:
        """Return all frames as a dict of 2D arrays with shape (n_steps, n_sites)."""
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r")
        return {
            "psi_real": np.array(group["psi_real"][:]),
            "psi_imag": np.array(group["psi_imag"][:]),
            "mu": np.array(group["mu"][:]),
        }

    def delete_run(self, run_id: str) -> None:
        """Delete all data for a run."""
        run_path = self._run_path(run_id)
        if run_path.parent.exists():
            shutil.rmtree(run_path.parent)
