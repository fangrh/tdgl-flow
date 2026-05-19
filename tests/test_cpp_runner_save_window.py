import importlib.util
from pathlib import Path

import h5py
import numpy as np
import pytest


RUNNER_PATH = Path(__file__).parents[1] / "services" / "cpp-tdgl-runner" / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("cpp_tdgl_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_output(path: Path) -> None:
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        for index, time_value in enumerate([0.5, 1.0, 1.5, 2.0]):
            g = data.create_group(str(index))
            g.attrs["time"] = time_value
            g.create_dataset("psi_real", data=np.array([1.0, 1.0 + index]))
            g.create_dataset("psi_imag", data=np.array([0.0, 0.1 * index]))
            g.create_dataset("mu", data=np.array([0.0, 0.2 * index]))


def test_read_save_window_returns_each_frame(tmp_path):
    runner = load_runner()
    output = tmp_path / "out.h5"
    write_output(output)

    frames = runner._read_save_window_frames(str(output), save_start_rel=1.0, save_end_rel=2.0)

    assert [frame["local_time"] for frame in frames] == [1.0, 1.5, 2.0]
    assert frames[0]["mu"].tolist() == [0.0, 0.2]
    assert frames[2]["psi_real"].tolist() == [1.0, 4.0]


def test_read_save_window_rejects_empty_window(tmp_path):
    runner = load_runner()
    output = tmp_path / "out.h5"
    write_output(output)

    with pytest.raises(RuntimeError, match="No saved frames found"):
        runner._read_save_window_frames(str(output), save_start_rel=3.0, save_end_rel=4.0)


def test_saved_window_time_mapper_concatenates_windows():
    runner = load_runner()
    mapper = runner.SaveWindowTimeline()

    first = mapper.map_frame(save_start_rel=1.0, local_time=1.0)
    second = mapper.map_frame(save_start_rel=1.0, local_time=2.0)
    mapper.finish_window(save_time=1.0)
    third = mapper.map_frame(save_start_rel=3.0, local_time=3.0)

    assert first == pytest.approx(0.0)
    assert second == pytest.approx(1.0)
    assert third == pytest.approx(1.0)
