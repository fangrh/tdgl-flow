import numpy as np

from tdgl_data.synthetic import SyntheticFrame, generate_synthetic_run


def test_generate_synthetic_run_is_deterministic():
    first = list(generate_synthetic_run(frame_count=3, grid_shape=(6, 5), seed=123))
    second = list(generate_synthetic_run(frame_count=3, grid_shape=(6, 5), seed=123))

    assert len(first) == 3
    assert isinstance(first[0], SyntheticFrame)
    assert first[0].psi_real.shape == (6, 5)
    assert np.allclose(first[1].psi_real, second[1].psi_real)
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
