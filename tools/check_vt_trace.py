#!/usr/bin/env python3
"""Check whether 2x2 V-vs-t trace input can move backwards.

The 2x2 renderer draws the V-t panel from HDF5 frame attrs plus the
manifest timing steps. This script audits the same inputs without opening
widgets.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import h5py
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _frame_keys(data_group) -> list[str]:
    return sorted(data_group.keys(), key=lambda key: int(key))


def _read_times_and_voltage(h5_path: str) -> tuple[np.ndarray, np.ndarray, list[int]]:
    times: list[float] = []
    voltages: list[float] = []
    frame_indices: list[int] = []
    with h5py.File(h5_path, "r") as f:
        data = f["data"]
        for key in _frame_keys(data):
            idx = int(key)
            d = data[key]
            frame_indices.append(idx)
            times.append(float(d.attrs.get("time", idx)))
            try:
                mu_rs = np.array(d["running_state/mu"])
                dt_rs = np.array(d["running_state/dt"])
                voltage_samples = mu_rs[0] - mu_rs[1]
                dt_sum = float(dt_rs.sum())
                if dt_sum > 0:
                    voltages.append(float(np.sum(voltage_samples * dt_rs) / dt_sum))
                else:
                    voltages.append(float(voltage_samples.mean()))
            except Exception:
                voltages.append(float("nan"))
    return np.array(times), np.array(voltages), frame_indices


def _download_or_resolve_h5(args) -> tuple[str, dict]:
    if args.h5:
        return args.h5, {}

    from tdgl_sdk.client import TDGLRunStore

    store = TDGLRunStore(
        endpoint_url=args.minio_endpoint,
        access_key=args.minio_access_key,
        secret_key=args.minio_secret_key,
        bucket=args.minio_bucket,
    )
    run_id = args.run_id
    manifest = None
    if run_id is None:
        runs = [r for r in store.list_runs() if r.get("status") == "completed"]
        if not runs:
            raise RuntimeError("No completed runs found in MinIO")
        manifest = runs[0]
        run_id = manifest["run_id"]
    else:
        manifest = store.get_run(run_id) or {}

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    h5_path = store.download_h5(run_id, str(cache_dir))
    if h5_path is None:
        raise FileNotFoundError(f"No HDF5 output found for run {run_id!r}")
    return h5_path, manifest


def _timing_steps(manifest: dict, h5_path: str) -> list[dict]:
    steps = list(manifest.get("timing_steps") or [])
    if steps:
        return steps

    raw = manifest.get("raw_timing_params") or {}
    if raw.get("je_step"):
        try:
            from tdgl_workflow.timing import build_timing

            timing = build_timing(**raw)
            steps = list(timing.get("steps") or [])
            steps.extend(timing.get("ramp_down_steps") or [])
            if steps:
                return steps
        except Exception:
            pass

    try:
        from tdgl_sdk.viewer._iv import load_timing_steps_from_solution

        return list(load_timing_steps_from_solution(h5_path) or [])
    except Exception:
        return []


def _find_step(t: float, steps: list[dict]) -> int | None:
    for i, step in enumerate(steps):
        if step["ramp_start"] <= t < step["stable_end"]:
            return i
    return None


def _summarize_backtracks(times: np.ndarray, steps: list[dict]) -> dict:
    global_back = np.where(np.diff(times) < 0)[0]
    same_step_back: list[tuple[int, int, float, float]] = []
    step_resets: list[tuple[int, int, int, int, float, float]] = []
    unmatched: list[tuple[int, float]] = []

    prev_step = None
    prev_local = None
    for i, t in enumerate(times):
        step_idx = _find_step(float(t), steps) if steps else None
        if steps and step_idx is None:
            unmatched.append((i, float(t)))
            continue
        local = float(t - steps[step_idx]["ramp_start"]) if step_idx is not None else float(t)
        if i > 0 and step_idx == prev_step and prev_local is not None and local < prev_local:
            same_step_back.append((i - 1, i, prev_local, local))
        if i > 0 and prev_step is not None and step_idx is not None and step_idx != prev_step:
            step_resets.append((i - 1, i, prev_step, step_idx, prev_local or 0.0, local))
        prev_step = step_idx
        prev_local = local

    return {
        "global_back": global_back,
        "same_step_back": same_step_back,
        "step_resets": step_resets,
        "unmatched": unmatched,
    }


def _largest_voltage_swings(times: np.ndarray, voltages: np.ndarray, steps: list[dict], n: int) -> list[dict]:
    rows = []
    for i in range(1, len(times)):
        prev_v = voltages[i - 1]
        cur_v = voltages[i]
        if math.isnan(prev_v) or math.isnan(cur_v):
            continue
        step_idx = _find_step(float(times[i]), steps) if steps else None
        rows.append({
            "frame": i,
            "step": None if step_idx is None else step_idx + 1,
            "t": float(times[i]),
            "dt_frame": float(times[i] - times[i - 1]),
            "dV": float(cur_v - prev_v),
            "V": float(cur_v),
        })
    rows.sort(key=lambda row: abs(row["dV"]), reverse=True)
    return rows[:n]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--h5", help="Local HDF5 path")
    parser.add_argument("--run-id", help="MinIO run id. Defaults to latest completed run.")
    parser.add_argument("--cache-dir", default=str(ROOT / ".cache" / "tdgl-h5"))
    parser.add_argument("--minio-endpoint", default="http://localhost:30900")
    parser.add_argument("--minio-access-key", default="minioadmin")
    parser.add_argument("--minio-secret-key", default="minioadmin123")
    parser.add_argument("--minio-bucket", default="tdgl-results")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    h5_path, manifest = _download_or_resolve_h5(args)
    steps = _timing_steps(manifest, h5_path)
    times, voltages, frame_indices = _read_times_and_voltage(h5_path)
    summary = _summarize_backtracks(times, steps)

    print(f"HDF5: {h5_path}")
    if manifest:
        print(f"Run: {manifest.get('run_id')} status={manifest.get('status')} frames={manifest.get('n_frames')}")
    print(f"Frames read: {len(times)} keys {frame_indices[0] if frame_indices else '?'}..{frame_indices[-1] if frame_indices else '?'}")
    if len(times):
        print(f"Time range: {times[0]:.12g} .. {times[-1]:.12g}")
    print(f"Timing steps: {len(steps)}")

    key_gaps = sorted(set(range(frame_indices[0], frame_indices[-1] + 1)) - set(frame_indices)) if frame_indices else []
    print(f"Frame key gaps: {len(key_gaps)}")
    if key_gaps:
        print(f"  first gaps: {key_gaps[:20]}")

    global_back = summary["global_back"]
    print(f"Global time backtracks: {len(global_back)}")
    for i in global_back[: args.top]:
        print(f"  frame {i}->{i + 1}: t {times[i]:.12g} -> {times[i + 1]:.12g}")

    same_step_back = summary["same_step_back"]
    print(f"Same-step local-time backtracks: {len(same_step_back)}")
    for prev_i, cur_i, prev_local, local in same_step_back[: args.top]:
        print(f"  frame {prev_i}->{cur_i}: local {prev_local:.12g} -> {local:.12g}")

    step_resets = summary["step_resets"]
    print(f"Step-boundary local-time resets: {len(step_resets)}")
    for prev_i, cur_i, prev_step, step_idx, prev_local, local in step_resets[: args.top]:
        print(
            "  frame "
            f"{prev_i}->{cur_i}: step {prev_step + 1}->{step_idx + 1}, "
            f"local {prev_local:.12g}->{local:.12g}, global {times[prev_i]:.12g}->{times[cur_i]:.12g}"
        )

    unmatched = summary["unmatched"]
    print(f"Frames outside timing steps: {len(unmatched)}")
    for frame, t in unmatched[: args.top]:
        print(f"  frame {frame}: t={t:.12g}")

    print("Largest adjacent V jumps:")
    for row in _largest_voltage_swings(times, voltages, steps, args.top):
        print(
            f"  frame {row['frame']}: step={row['step']} "
            f"t={row['t']:.12g} dt_frame={row['dt_frame']:.6g} "
            f"dV={row['dV']:.6g} V={row['V']:.6g}"
        )

    if len(global_back) or same_step_back:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
