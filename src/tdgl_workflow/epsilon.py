"""Gaussian spot array epsilon for TDGL simulations."""

import numpy as np


def make_gaussian_epsilon(
    positions: list[list[float]],
    widths: list[list[float]],
    strengths: list[float],
):
    """Return an epsilon(r) callable for tdgl.solve(disorder_epsilon=...).

    Args:
        positions: Nx2 array of spot centers [x, y] in device coordinates.
        widths: Nx2 array of [sigma_x, sigma_y] for each elliptical spot.
        strengths: N array of peak T suppression for each spot.

    Returns:
        Callable epsilon(r) where r is (x, y) tuple.
        epsilon = clamp(1 - sum(T_i), 0, 1)
    """
    if len(positions) != len(widths) or len(positions) != len(strengths):
        raise ValueError("positions, widths, and strengths must have same length")
    pos = np.asarray(positions, dtype=np.float64).reshape(-1, 2)
    w = np.asarray(widths, dtype=np.float64).reshape(-1, 2)
    s = np.asarray(strengths, dtype=np.float64)
    if len(pos) > 0 and np.any(w <= 0):
        raise ValueError("widths must be positive")

    def epsilon(r: tuple[float, float]) -> float:
        x, y = r
        dx = x - pos[:, 0]
        dy = y - pos[:, 1]
        sx2 = w[:, 0] ** 2
        sy2 = w[:, 1] ** 2
        T = float(np.sum(s * np.exp(-dx**2 / (2 * sx2) - dy**2 / (2 * sy2))))
        return float(np.clip(1.0 - T, 0.0, 1.0))

    return epsilon