from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import zarr


class ZarrStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _run_path(self, run_id: str) -> Path:
        return self.root / "runs" / run_id / "frames.zarr"

    def create_run(self, run_id: str, grid_shape: tuple[int, int]) -> None:
        run_path = self._run_path(run_id)
        run_path.parent.mkdir(parents=True, exist_ok=True)
        group = zarr.open_group(str(run_path), mode="w")
        chunks = (1,) + grid_shape
        for field in ("psi_real", "psi_imag", "mu"):
            group.create_dataset(
                field,
                shape=(0,) + grid_shape,
                dtype="float32",
                chunks=chunks,
            )

    def append_frame(
        self,
        run_id: str,
        frame_index: int,
        arrays: dict[str, np.ndarray],
    ) -> None:
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r+")
        needed = frame_index + 1
        for field, data in arrays.items():
            ds = group[field]
            if needed > ds.shape[0]:
                ds.resize((needed,) + ds.shape[1:])
            ds[frame_index] = data

    def get_frame(self, run_id: str, frame_index: int) -> dict[str, np.ndarray]:
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r")
        return {
            "psi_real": np.array(group["psi_real"][frame_index]),
            "psi_imag": np.array(group["psi_imag"][frame_index]),
            "mu": np.array(group["mu"][frame_index]),
        }

    def delete_run(self, run_id: str) -> None:
        run_path = self._run_path(run_id)
        if run_path.parent.exists():
            shutil.rmtree(run_path.parent)