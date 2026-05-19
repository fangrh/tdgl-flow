def _validate_timing_inputs(je_step: float, ramp_time: float, stable_time: float, save_time: float) -> None:
    if je_step == 0:
        raise ValueError("je_step must be non-zero")
    if ramp_time < 0:
        raise ValueError("ramp_time must be greater than or equal to 0")
    if stable_time <= 0:
        raise ValueError("stable_time must be greater than 0")
    if save_time <= 0:
        raise ValueError("save_time must be greater than 0")
    if save_time > stable_time:
        raise ValueError("save_time must be less than or equal to stable_time")


def _build_steps(je_initial, je_final, je_step, ramp_time, stable_time, save_time, t_offset=0):
    _validate_timing_inputs(je_step, ramp_time, stable_time, save_time)
    n_steps = max(1, round(abs(je_final - je_initial) / abs(je_step)))
    period = ramp_time + stable_time
    sign = 1 if je_final >= je_initial else -1

    steps = []
    for i in range(n_steps):
        t = t_offset + i * period
        je_start = je_initial + sign * i * abs(je_step)
        je_end = je_start + sign * abs(je_step)
        stable_end = t + period
        steps.append({
            "je_start": je_start,
            "je_end": je_end,
            "ramp_start": t,
            "ramp_end": t + ramp_time,
            "stable_end": stable_end,
            "save_start": stable_end - save_time,
            "save_end": stable_end,
        })

    total_time = n_steps * period
    return steps, total_time, n_steps


def build_timing(
    *,
    je_initial: float,
    je_final: float,
    je_step: float,
    ramp_time: float,
    stable_time: float,
    save_time: float,
    ramp_down: bool = False,
) -> dict:
    steps, total_up_time, n_up = _build_steps(
        je_initial, je_final, je_step, ramp_time, stable_time, save_time
    )

    ramp_down_steps = []
    if ramp_down:
        ramp_down_steps, total_down_time, n_down = _build_steps(
            je_final, je_initial, je_step, ramp_time, stable_time, save_time,
            t_offset=total_up_time,
        )

    solve_time = total_up_time + (total_down_time if ramp_down else 0)
    total_steps = n_up + (n_down if ramp_down else 0)

    return {
        "steps": steps,
        "ramp_down_steps": ramp_down_steps,
        "solve_time": solve_time,
        "n_steps": total_steps,
        "mode": "simple",
        "je_initial": je_initial,
        "je_final": je_final,
        "je_step": je_step,
        "ramp_time": ramp_time,
        "stable_time": stable_time,
        "save_time": save_time,
        "ramp_down": ramp_down,
    }


def build_timing_segmented(
    *,
    segments: list[dict],
    ramp_time: float,
    stable_time: float,
    save_time: float,
) -> dict:
    all_steps = []
    t_offset = 0.0

    for seg in segments:
        seg_steps, seg_time, _ = _build_steps(
            seg["je_initial"], seg["je_final"], seg["je_step"],
            ramp_time, stable_time, save_time,
            t_offset=t_offset,
        )
        all_steps.extend(seg_steps)
        t_offset += seg_time

    return {
        "steps": all_steps,
        "ramp_down_steps": [],
        "solve_time": t_offset,
        "n_steps": len(all_steps),
        "mode": "segmented",
        "segments": segments,
        "ramp_time": ramp_time,
        "stable_time": stable_time,
        "save_time": save_time,
    }
