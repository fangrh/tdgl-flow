import pytest


def test_build_timing_returns_step_list():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0, je_final=5.0, je_step=1.0,
        ramp_time=0.5, stable_time=2.0, save_time=1.0, ramp_down=False,
    )
    assert "steps" in result
    assert "solve_time" in result
    assert "n_steps" in result
    assert isinstance(result["steps"], list)
    assert len(result["steps"]) == 5


def test_build_timing_step_fields():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0, je_final=3.0, je_step=1.0,
        ramp_time=1.0, stable_time=4.0, save_time=2.0, ramp_down=False,
    )
    step = result["steps"][0]
    assert step["je_start"] == 0.0
    assert step["je_end"] == 1.0
    assert "ramp_start" in step
    assert "ramp_end" in step
    assert "stable_end" in step
    assert "save_start" in step
    assert "save_end" in step


def test_build_timing_solve_time():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0, je_final=2.0, je_step=1.0,
        ramp_time=1.0, stable_time=3.0, save_time=1.0, ramp_down=False,
    )
    assert result["solve_time"] == pytest.approx(result["n_steps"] * 4.0)


def test_build_timing_with_ramp_down():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0, je_final=2.0, je_step=1.0,
        ramp_time=1.0, stable_time=3.0, save_time=1.0, ramp_down=True,
    )
    n = result["n_steps"]
    assert result["solve_time"] == pytest.approx(n * 4.0 + n * 1.0)


def test_build_timing_saves_ramp_down_steps():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0, je_final=2.0, je_step=1.0,
        ramp_time=1.0, stable_time=3.0, save_time=1.0, ramp_down=True,
    )
    assert "ramp_down_steps" in result
    assert len(result["ramp_down_steps"]) == result["n_steps"]