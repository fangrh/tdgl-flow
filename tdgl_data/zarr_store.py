from collections.abc import Iterable
from pathlib import Path

import numpy as np
import zarr
from numcodecs import Blosc


class FilesystemZarrStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def get_store_uri(self, run_id: str) -> str:
        return f"runs/{run_id}/frames.zarr"

    def _path(self, run_id: str) -> Path:
        return self.root / self.get_store_uri(run_id)

    def create_run_store(
        self,
        run_id: str,
        *,
        grid_shape: tuple[int, int],
        fields: Iterable[str],
    ) -> str:
        path = self._path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        root = zarr.open_group(str(path), mode="a")
        compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)

        for field in fields:
            if field not in root:
                root.create_dataset(
                    field,
                    shape=(0, *grid_shape),
                    chunks=(1, *grid_shape),
                    dtype="float32",
                    compressor=compressor,
                    overwrite=False,
                )

        return self.get_store_uri(run_id)

    def append_frame(self, run_id: str, frame_index: int, arrays: dict[str, np.ndarray]) -> None:
        root = zarr.open_group(str(self._path(run_id)), mode="a")

        for field, value in arrays.items():
            arr = root[field]
            data = np.asarray(value, dtype="float32")
            expected_shape = tuple(arr.shape[1:])

            if data.shape != expected_shape:
                raise ValueError(f"{field} shape {data.shape} does not match {expected_shape}")

            if arr.shape[0] <= frame_index:
                arr.resize(frame_index + 1, *arr.shape[1:])

            arr[frame_index, :, :] = data

    def read_frame(
        self,
        run_id: str,
        frame_index: int,
        fields: Iterable[str],
    ) -> dict[str, np.ndarray]:
        root = zarr.open_group(str(self._path(run_id)), mode="r")
        return {field: np.asarray(root[field][frame_index, :, :]) for field in fields}
