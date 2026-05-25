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
    pos = np.asarray(positions, dtype=np.float64)
    w = np.asarray(widths, dtype=np.float64)
    s = np.asarray(strengths, dtype=np.float64)

    def epsilon(r):
        x, y = r
        dx = x - pos[:, 0]
        dy = y - pos[:, 1]
        sx2 = w[:, 0] ** 2
        sy2 = w[:, 1] ** 2
        T = float(np.sum(s * np.exp(-dx**2 / (2 * sx2) - dy**2 / (2 * sy2))))
        return max(0.0, 1.0 - T)

    return epsilon