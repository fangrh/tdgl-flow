"""Tests for Gaussian epsilon factory."""

import numpy as np
import pytest

from tdgl_workflow.epsilon import make_gaussian_epsilon


def test_single_spot_center():
    """At the center of a single spot, epsilon = 1 - strength."""
    epsilon = make_gaussian_epsilon(
        positions=[[2.0, 1.0]],
        widths=[[0.5, 0.5]],
        strengths=[0.8],
    )
    result = epsilon((2.0, 1.0))
    assert np.isclose(result, 0.2)


def test_single_spot_far_away():
    """Far from any spot, epsilon ~ 1.0."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0]],
        widths=[[1.0, 1.0]],
        strengths=[0.5],
    )
    result = epsilon((100.0, 100.0))
    assert np.isclose(result, 1.0)


def test_two_spots_add_linearly():
    """Two overlapping spots: T values add linearly."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0], [0.0, 0.0]],
        widths=[[1.0, 1.0], [1.0, 1.0]],
        strengths=[0.3, 0.3],
    )
    result = epsilon((0.0, 0.0))
    assert np.isclose(result, 0.4)  # 1 - (0.3 + 0.3)


def test_clamp_to_zero():
    """Overlapping spots with total T > 1 clamp epsilon to 0."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0], [0.0, 0.0]],
        widths=[[0.5, 0.5], [0.5, 0.5]],
        strengths=[0.8, 0.8],
    )
    result = epsilon((0.0, 0.0))
    assert result == 0.0


def test_elliptical_spot():
    """Elliptical spot: different widths in x and y."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0]],
        widths=[[1.0, 10.0]],
        strengths=[1.0],
    )
    # At (1,0): exp(-1/2) ~ 0.607 -> epsilon ~ 0.393
    val_x = epsilon((1.0, 0.0))
    assert np.isclose(val_x, 1.0 - np.exp(-0.5), atol=1e-10)
    # At (0,1): exp(-1/200) ~ 0.995 -> epsilon ~ 0.005
    val_y = epsilon((0.0, 1.0))
    assert np.isclose(val_y, 1.0 - np.exp(-1.0 / 200), atol=1e-10)


def test_no_spots_epsilon_is_one():
    """With zero spots, epsilon is 1.0 everywhere."""
    epsilon = make_gaussian_epsilon(
        positions=[],
        widths=[],
        strengths=[],
    )
    assert epsilon((5.0, -3.0)) == 1.0


def test_mismatched_lengths_raises():
    """Mismatched array lengths raise ValueError."""
    with pytest.raises(ValueError, match="same length"):
        make_gaussian_epsilon(
            positions=[[0.0, 0.0]],
            widths=[[1.0, 1.0], [2.0, 2.0]],
            strengths=[0.5],
        )


def test_zero_width_raises():
    """Zero or negative widths raise ValueError."""
    with pytest.raises(ValueError, match="positive"):
        make_gaussian_epsilon(
            positions=[[0.0, 0.0]],
            widths=[[0.0, 1.0]],
            strengths=[0.5],
        )