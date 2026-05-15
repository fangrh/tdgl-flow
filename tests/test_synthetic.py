import numpy as np
import pytest

from tdgl_data.synthetic import SyntheticFrame, generate_synthetic_run


def test_generate_synthetic_run_is_deterministic():
    first = list(generate_synthetic_run(frame_count=3, grid_shape=(6, 5), seed=123))
    second = list(generate_synthetic_run(frame_count=3, grid_shape=(6, 5), seed=123))

    assert len(first) == 3
    assert isinstance(first[0], SyntheticFrame)
    assert first[0].psi_real.shape == (6, 5)
    for first_frame, second_frame in zip(first, second, strict=True):
        assert first_frame.frame_index == second_frame.frame_index
        assert first_frame.time_value == second_frame.time_value
        assert first_frame.je == second_frame.je
        assert first_frame.voltage == second_frame.voltage
        assert np.allclose(first_frame.psi_real, second_frame.psi_real)
        assert np.allclose(first_frame.psi_imag, second_frame.psi_imag)
        assert np.allclose(first_frame.mu, second_frame.mu)
        assert first_frame.psi_real.dtype == np.float32
        assert first_frame.psi_imag.dtype == np.float32
        assert first_frame.mu.dtype == np.float32
    assert first[2].frame_index == 2
    assert first[2].je > first[0].je


def test_generate_synthetic_run_accepts_positional_frame_count_and_grid_shape():
    frames = list(generate_synthetic_run(3, (6, 5), seed=123))

    assert len(frames) == 3
    assert frames[0].psi_real.shape == (6, 5)


def test_generated_current_and_voltage_are_nondecreasing():
    frames = list(generate_synthetic_run(frame_count=100, grid_shape=(6, 5), seed=123))

    je_values = np.array([frame.je for frame in frames])
    voltage_values = np.array([frame.voltage for frame in frames])

    assert np.all(np.diff(je_values) >= 0.0)
    assert np.all(np.diff(voltage_values) >= 0.0)


@pytest.mark.parametrize("frame_count", [0, -1, 1.5, True])
def test_generate_synthetic_run_rejects_invalid_frame_count(frame_count):
    with pytest.raises(ValueError, match="frame_count must be a positive integer"):
        list(generate_synthetic_run(frame_count=frame_count, grid_shape=(6, 5), seed=123))


@pytest.mark.parametrize(
    "grid_shape",
    [(0, 5), (5, 0), (5,), [5, 5], (5.0, 5), (True, 5), (6, True)],
)
def test_generate_synthetic_run_rejects_invalid_grid_shape(grid_shape):
    with pytest.raises(ValueError, match="grid_shape must be a tuple of two positive integers"):
        list(generate_synthetic_run(frame_count=3, grid_shape=grid_shape, seed=123))
