import importlib.util
from pathlib import Path

import numpy as np
import pytest


RUNNER_PATH = Path(__file__).parents[1] / "services" / "py-tdgl-runner" / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("py_tdgl_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_group_solution_frames_by_save_window():
    runner = load_runner()
    steps = [
        {"je_end": 1.0, "save_start": 3.0, "save_end": 5.0},
        {"je_end": 2.0, "save_start": 8.0, "save_end": 10.0},
    ]
    times = np.array([2.0, 3.0, 4.0, 5.0, 8.0, 9.0, 10.0, 11.0])

    grouped = runner._group_solution_indices_by_save_window(times, steps)

    assert grouped == [[1, 2, 3], [4, 5, 6]]


def test_playback_time_from_physical_time_concatenates_windows():
    runner = load_runner()
    mapper = runner.SaveWindowTimeline()

    assert mapper.map_physical(save_start=3.0, physical_time=3.0) == pytest.approx(0.0)
    assert mapper.map_physical(save_start=3.0, physical_time=5.0) == pytest.approx(2.0)
    mapper.finish_window(save_time=2.0)
    assert mapper.map_physical(save_start=8.0, physical_time=8.0) == pytest.approx(2.0)
