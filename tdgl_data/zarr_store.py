import re
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import zarr
from numcodecs import Blosc

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class FilesystemZarrStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def get_store_uri(self, run_id: str) -> str:
        self._validate_run_id(run_id)
        return f"runs/{run_id}/frames.zarr"

    def _path(self, run_id: str) -> Path:
        root = self.root.resolve()
        path = (root / self.get_store_uri(run_id)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"run_id resolves outside zarr root: {run_id!r}") from exc
        return path

    def _validate_run_id(self, run_id: str) -> None:
        if not run_id or not _SAFE_RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
            raise ValueError(f"unsafe run_id: {run_id!r}")

    def create_run_store(
        self,
        run_id: str,
        *,
        grid_shape: tuple[int, int],
        fields: Iterable[str],
    ) -> str:
        requested_fields = tuple(fields)
        path = self._path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        root = zarr.open_group(str(path), mode="a")
        compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
        existing_fields = set(root.array_keys())

        if existing_fields:
            if existing_fields != set(requested_fields):
                raise ValueError(
                    f"existing fields {sorted(existing_fields)} do not match "
                    f"requested fields {sorted(requested_fields)}"
                )
            for field in requested_fields:
                existing_shape = tuple(root[field].shape[1:])
                if existing_shape != tuple(grid_shape):
                    raise ValueError(
                        f"{field} grid shape {existing_shape} does not match {tuple(grid_shape)}"
                    )

        for field in requested_fields:
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
        if frame_index < 0:
            raise ValueError("frame_index must be non-negative")

        path = self._path(run_id)
        if not path.exists():
            raise FileNotFoundError(path)

        root = zarr.open_group(str(path), mode="r+")
        required_fields = set(root.array_keys())
        provided_fields = set(arrays)
        if provided_fields != required_fields:
            raise ValueError(
                f"provided fields {sorted(provided_fields)} do not match "
                f"required fields {sorted(required_fields)}"
            )

        frame_data: dict[str, np.ndarray] = {}
        for field, value in arrays.items():
            arr = root[field]
            data = np.asarray(value, dtype="float32")
            expected_shape = tuple(arr.shape[1:])

            if data.shape != expected_shape:
                raise ValueError(f"{field} shape {data.shape} does not match {expected_shape}")

            frame_data[field] = data

        for field, data in frame_data.items():
            arr = root[field]
            if arr.shape[0] <= frame_index:
                arr.resize(frame_index + 1, *arr.shape[1:])

            arr[frame_index, :, :] = data

    def clear_frame(self, run_id: str, frame_index: int) -> None:
        if frame_index < 0:
            raise ValueError("frame_index must be non-negative")

        path = self._path(run_id)
        if not path.exists():
            return

        root = zarr.open_group(str(path), mode="r+")
        fields = tuple(root.array_keys())
        if not fields:
            return

        frame_count = max(root[field].shape[0] for field in fields)
        if frame_index >= frame_count:
            return

        if frame_index == frame_count - 1:
            for field in fields:
                arr = root[field]
                if arr.shape[0] == frame_count:
                    arr.resize(frame_index, *arr.shape[1:])
            return

        for field in fields:
            arr = root[field]
            if frame_index < arr.shape[0]:
                arr[frame_index, :, :] = np.nan

    def read_frame(
        self,
        run_id: str,
        frame_index: int,
        fields: Iterable[str],
    ) -> dict[str, np.ndarray]:
        if frame_index < 0:
            raise ValueError("frame_index must be non-negative")

        root = zarr.open_group(str(self._path(run_id)), mode="r")
        return {field: np.asarray(root[field][frame_index, :, :]) for field in fields}
