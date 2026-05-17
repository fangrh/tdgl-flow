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
    n_steps = max(1, round((je_final - je_initial) / je_step))
    period = ramp_time + stable_time

    steps = []
    for i in range(n_steps):
        t_offset = i * period
        je_start = je_initial + i * je_step
        je_end = je_start + je_step
        steps.append({
            "je_start": je_start,
            "je_end": je_end,
            "ramp_start": t_offset,
            "ramp_end": t_offset + ramp_time,
            "stable_end": t_offset + period,
            "save_start": t_offset + ramp_time + (stable_time - save_time) / 2,
            "save_end": t_offset + ramp_time + (stable_time + save_time) / 2,
        })

    total_up_time = n_steps * period

    ramp_down_steps = []
    if ramp_down:
        for i in range(n_steps):
            t_offset = total_up_time + i * ramp_time
            je_start = je_initial + (n_steps - i) * je_step
            je_end = je_start - je_step
            ramp_down_steps.append({
                "je_start": je_start,
                "je_end": je_end,
                "ramp_start": t_offset,
                "ramp_end": t_offset + ramp_time,
            })

    solve_time = total_up_time + (n_steps * ramp_time if ramp_down else 0)

    return {
        "steps": steps,
        "ramp_down_steps": ramp_down_steps,
        "solve_time": solve_time,
        "n_steps": n_steps,
        "je_initial": je_initial,
        "je_final": je_final,
        "je_step": je_step,
        "ramp_time": ramp_time,
        "stable_time": stable_time,
        "save_time": save_time,
        "ramp_down": ramp_down,
        "period": period,
    }