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
