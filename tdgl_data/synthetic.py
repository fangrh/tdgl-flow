from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SyntheticFrame:
    frame_index: int
    time_value: float
    je: float
    voltage: float
    psi_real: np.ndarray
    psi_imag: np.ndarray
    mu: np.ndarray

    def arrays(self) -> dict[str, np.ndarray]:
        return {
            "psi_real": self.psi_real,
            "psi_imag": self.psi_imag,
            "mu": self.mu,
        }


def generate_synthetic_run(
    frame_count: int,
    grid_shape: tuple[int, int],
    seed: int = 0,
) -> Iterator[SyntheticFrame]:
    if type(frame_count) is not int or frame_count <= 0:
        raise ValueError("frame_count must be a positive integer")
    if (
        not isinstance(grid_shape, tuple)
        or len(grid_shape) != 2
        or not all(type(value) is int and value > 0 for value in grid_shape)
    ):
        raise ValueError("grid_shape must be a tuple of two positive integers")

    rng = np.random.default_rng(seed)
    y = np.linspace(-1.0, 1.0, grid_shape[0], dtype="float32")
    x = np.linspace(-1.0, 1.0, grid_shape[1], dtype="float32")
    yy, xx = np.meshgrid(y, x, indexing="ij")
    phase_noise = rng.normal(0.0, 0.03, size=grid_shape).astype("float32")

    for frame_index in range(frame_count):
        time_value = frame_index * 0.1
        je = -1.0 + (2.0 * frame_index / max(frame_count - 1, 1))
        voltage = 0.02 * je
        angle = 2.5 * xx + 1.7 * yy + time_value + phase_noise
        envelope = 0.75 + 0.2 * np.cos(np.pi * xx * yy + time_value)
        psi_real = (envelope * np.cos(angle)).astype("float32")
        psi_imag = (envelope * np.sin(angle)).astype("float32")
        mu = (0.4 * np.sin(np.pi * xx + time_value) * np.cos(np.pi * yy)).astype("float32")
        yield SyntheticFrame(
            frame_index,
            time_value,
            float(je),
            float(voltage),
            psi_real,
            psi_imag,
            mu,
        )
