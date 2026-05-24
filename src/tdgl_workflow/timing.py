def _validate_timing_inputs(je_step: float, ramp_time: float, stable_time: float) -> None:
    if je_step == 0:
        raise ValueError("je_step must be non-zero")
    if ramp_time < 0:
        raise ValueError("ramp_time must be greater than or equal to 0")
    if stable_time <= 0:
        raise ValueError("stable_time must be greater than 0")


def _build_steps(je_initial, je_final, je_step, ramp_time, stable_time, t_offset=0):
    _validate_timing_inputs(je_step, ramp_time, stable_time)
    n_steps = max(1, round(abs(je_final - je_initial) / abs(je_step)))
    period = ramp_time + stable_time
    sign = 1 if je_final >= je_initial else -1

    steps = []
    for i in range(n_steps):
        t = t_offset + i * period
        je_start = je_initial + sign * i * abs(je_step)
        je_end = je_start + sign * abs(je_step)
        if sign > 0:
            je_end = min(je_end, je_final)
        else:
            je_end = max(je_end, je_final)
        stable_end = t + period
        steps.append({
            "je_start": je_start,
            "je_end": je_end,
            "ramp_start": t,
            "ramp_end": t + ramp_time,
            "stable_end": stable_end,
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
    ramp_down: bool = False,
) -> dict:
    steps, total_up_time, n_up = _build_steps(
        je_initial, je_final, je_step, ramp_time, stable_time
    )

    ramp_down_steps = []
    if ramp_down:
        ramp_down_steps, total_down_time, n_down = _build_steps(
            je_final, je_initial, je_step, ramp_time, stable_time,
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
        "ramp_down": ramp_down,
    }


def _build_equilibration_step(je, stable_time, t_offset=0.0):
    return {
        "je_start": je,
        "je_end": je,
        "ramp_start": t_offset,
        "ramp_end": t_offset,
        "stable_end": t_offset + stable_time,
    }


def build_timing_segmented(
    *,
    segments: list[dict],
    ramp_time: float = 5.0,
    stable_time: float = 10.0,
    initial_stable_time: float | None = None,
) -> dict:
    all_steps = []
    t_offset = 0.0

    # Prepend an equilibration hold at the first segment's je_initial
    if initial_stable_time is None:
        initial_stable_time = segments[0].get("stable_time", stable_time)
    eq_step = _build_equilibration_step(
        segments[0]["je_initial"], initial_stable_time, t_offset=0.0,
    )
    all_steps.append(eq_step)
    t_offset += initial_stable_time

    for seg in segments:
        seg_ramp = seg.get("ramp_time", ramp_time)
        seg_stable = seg.get("stable_time", stable_time)
        seg_steps, seg_time, _ = _build_steps(
            seg["je_initial"], seg["je_final"], seg["je_step"],
            seg_ramp, seg_stable,
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
    }
