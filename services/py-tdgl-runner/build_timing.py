"""Argo build-timing step: generate timing schedule and write timing.json."""
import json
import os
import sys

sys.path.insert(0, "/app/vendor")

from tdgl_workflow.timing import build_timing, build_timing_segmented


def main():
    timing_params = json.loads(os.environ["TIMING_PARAMS"])
    data_dir = os.environ.get("DATA_DIR", "/data")

    mode = timing_params.get("mode", "simple")
    if mode == "segmented":
        timing_data = build_timing_segmented(
            segments=timing_params["segments"],
            ramp_time=timing_params["ramp_time"],
            stable_time=timing_params["stable_time"],
            save_time=timing_params["save_time"],
            initial_stable_time=timing_params.get("initial_stable_time"),
        )
    else:
        timing_data = build_timing(
            je_initial=timing_params["je_initial"],
            je_final=timing_params["je_final"],
            je_step=timing_params["je_step"],
            ramp_time=timing_params["ramp_time"],
            stable_time=timing_params["stable_time"],
            save_time=timing_params["save_time"],
            ramp_down=timing_params.get("ramp_down", False),
        )

    out_path = os.path.join(data_dir, "timing.json")
    with open(out_path, "w") as f:
        json.dump(timing_data, f, indent=2)

    print(f"Timing built: {timing_data['n_steps']} steps, solve_time={timing_data['solve_time']:.2f}s")


if __name__ == "__main__":
    main()
