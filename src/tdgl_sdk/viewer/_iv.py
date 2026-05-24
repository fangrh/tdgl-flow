import pickle
import threading

import numpy as np

from tdgl_sdk.viewer._mesh import h5open


def _load_terminal_currents(h5_path, **s3_kwds):
    """Load the terminal_currents callable from the HDF5 solution pickle."""
    try:
        with h5open(h5_path, "r", **s3_kwds) as f:
            raw = f["solution/terminal_currents.pickle"]
            blob = np.void(raw)
        return pickle.loads(blob)
    except Exception:
        return None


def _extract_timing_steps_from_terminal_currents(tc_fn):
    """Extract timing step metadata captured by the runner's current callback."""
    if tc_fn is None or tc_fn.__closure__ is None:
        return None

    required = {"je_start", "je_end", "ramp_start", "ramp_end", "stable_end"}
    for cell in tc_fn.__closure__:
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if not isinstance(value, list) or not value:
            continue
        if not all(isinstance(step, dict) and required <= set(step) for step in value):
            continue
        return [dict(step) for step in value]
    return None


def load_timing_steps_from_solution(h5_path, **s3_kwds):
    """Load timing steps embedded in a completed HDF5 solution, if available."""
    return _extract_timing_steps_from_terminal_currents(
        _load_terminal_currents(h5_path, **s3_kwds)
    )


class IVCache:
    """Incremental I-V cache for an HDF5 file."""

    def __init__(self, h5_path, mesh, poll_interval=1.0, batch_size=64, debug_log=None, **s3_kwds):
        self.h5_path = h5_path
        self._mesh = mesh
        self._s3_kwds = s3_kwds
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.I = []
        self.V = []
        self.t = []
        self.last_total = 0
        self.error = None
        self._timing_steps = None
        self._average_time = None
        self._step_avg_cache = None
        self._step_avg_thread = None
        self._step_avg_error = None
        self._frame_iv_cache = {}
        self._vt_step_cache = {}
        self._version = 0
        self._debug = debug_log

        self._tc_fn = _load_terminal_currents(h5_path, **s3_kwds)
        self._timing_steps = _extract_timing_steps_from_terminal_currents(self._tc_fn)

    def start(self):
        self.stop()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=1)
        if self._step_avg_thread and self._step_avg_thread.is_alive():
            self._step_avg_thread.join(timeout=1)

    def _frame_iv_from_group(self, d, idx):
        t_val = float(d.attrs.get("time", idx))

        # Use terminal_currents from the solution pickle when available.
        # This gives the correct applied current in physical units (matching je_final),
        # avoiding the edge-integration unit mismatch.
        if self._tc_fn is not None:
            try:
                tc = self._tc_fn(t_val)
                I_val = float(tc["source"]) if isinstance(tc, dict) else float(tc)
            except Exception:
                I_val = self._edge_current(d)
        else:
            I_val = self._edge_current(d)

        try:
            mu_rs = np.array(d["running_state/mu"])
            dt_rs = np.array(d["running_state/dt"])
            voltage_samples = mu_rs[0] - mu_rs[1]
            dt_sum = float(dt_rs.sum())
            if dt_sum > 0:
                V_val = float(np.sum(voltage_samples * dt_rs) / dt_sum)
            else:
                V_val = float(voltage_samples.mean())
        except Exception:
            V_val = float("nan")
        return I_val, V_val, t_val

    def _frame_iv(self, f, idx):
        return self._frame_iv_from_group(f[f"data/{idx}"], idx)

    def frame_iv(self, idx):
        idx = int(idx)
        with self.lock:
            if 0 <= idx < len(self.I):
                return self.I[idx], self.V[idx], self.t[idx]
            cached = self._frame_iv_cache.get(idx)
            if cached is not None:
                return cached
        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            value = self._frame_iv(f, idx)
        with self.lock:
            self._frame_iv_cache[idx] = value
        return value

    def vt_step(self, idx):
        idx = int(idx)
        timing_steps = self._timing_steps or []

        # Get frame time: check self.t first, then _frame_iv_cache (populated
        # by _step_average_worker), fall back to HDF5 only as last resort.
        with self.lock:
            if 0 <= idx < len(self.t):
                current_time = self.t[idx]
            else:
                cached_iv = self._frame_iv_cache.get(idx)
                current_time = cached_iv[2] if cached_iv else None
        if current_time is None:
            with h5open(self.h5_path, "r", **self._s3_kwds) as f:
                current_time = float(f[f"data/{idx}"].attrs.get("time", idx))

        # Determine which timing step this frame belongs to
        current_step = None
        step_idx = 0
        for si, step in enumerate(timing_steps):
            if step["ramp_start"] <= current_time < step["stable_end"]:
                current_step = step
                step_idx = si + 1
                break
        if current_step is None and timing_steps and current_time >= timing_steps[-1]["stable_end"]:
            current_step = timing_steps[-1]
            step_idx = len(timing_steps)

        if current_step is None:
            _, v_all, t_all = self.arrays(upto=idx)
            return t_all, v_all, 0, 0

        # Return cached data if available — no HDF5 open needed
        with self.lock:
            cached = self._vt_step_cache.get(step_idx)
            if cached is not None:
                _, local_times, voltages = cached
                return local_times, voltages, step_idx, len(timing_steps)

        # Cache miss — expensive! Reads ALL frames in the step over network.
        # Should never happen during normal playback because _step_average_worker
        # pre-populates _vt_step_cache. If you see this during playback, the
        # pre-computation hasn't finished yet or timing_steps was not provided.
        ramp_start = current_step["ramp_start"]
        stable_end = current_step["stable_end"]
        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            data = f["data"]
            local_times = []
            voltages = []
            max_time = current_time
            for key in sorted(data.keys(), key=lambda item: int(item)):
                frame_idx = int(key)
                group = data[key]
                t_val = float(group.attrs.get("time", frame_idx))
                if t_val < ramp_start:
                    continue
                if t_val >= stable_end:
                    break
                i_val, v_val, _ = self._frame_iv_from_group(group, frame_idx)
                local_times.append(t_val - ramp_start)
                voltages.append(v_val)
                max_time = max(max_time, t_val)
                with self.lock:
                    self._frame_iv_cache[frame_idx] = (i_val, v_val, t_val)

            local_times_arr = np.array(local_times, dtype=np.float64)
            voltages_arr = np.array(voltages, dtype=np.float64)
            with self.lock:
                self._vt_step_cache[step_idx] = (max_time, local_times_arr, voltages_arr)
            return local_times_arr, voltages_arr, step_idx, len(timing_steps)

    def _edge_current(self, d):
        """Fallback: integrate edge current density across x=0 cross-section."""
        cross = self._mesh["cross"]
        norm_dirs = self._mesh["norm_dirs"]
        dual_lengths = self._mesh["dual_lengths"]
        J = np.array(d["normal_current"]) + np.array(d["supercurrent"])
        return float(np.sum(J[cross] * norm_dirs[cross, 0] * dual_lengths[cross]))

    def update_available(self, target=None):
        with self.lock:
            start = len(self.I)
        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            available = len(f["data"].keys())
            end = available if target is None else min(available, int(target) + 1)
            if self._debug:
                self._debug.log("iv_update_start", cached=start, target=target)
            while start < end:
                batch_end = min(end, start + self.batch_size)
                batch = [self._frame_iv(f, i) for i in range(start, batch_end)]
                with self.lock:
                    self.I.extend(x[0] for x in batch)
                    self.V.extend(x[1] for x in batch)
                    self.t.extend(x[2] for x in batch)
                    self.last_total = available
                    self._version += 1
                start = batch_end
        if self._debug:
            self._debug.log("iv_update_done", new=end - start, total=len(self.I))
        return self.size()

    def ensure(self, idx):
        with self.lock:
            if len(self.I) > idx:
                return len(self.I)
        return self.update_available(target=idx)

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                self.update_available()
                self.error = None
            except Exception as exc:
                self.error = exc
            self.stop_event.wait(self.poll_interval)

    def arrays(self, upto=None):
        with self.lock:
            n = len(self.I) if upto is None else min(len(self.I), int(upto) + 1)
            return np.array(self.I[:n]), np.array(self.V[:n]), np.array(self.t[:n])

    def ranges(self, upto=None):
        i_arr, v_arr, _ = self.arrays(upto=upto)
        if len(i_arr) == 0:
            return 0.0, 1.0, 0.0, 1.0
        valid_I = i_arr[~np.isnan(i_arr)]
        valid_V = v_arr[~np.isnan(v_arr)]
        if len(valid_I):
            I_min, I_max = float(valid_I.min()), float(valid_I.max())
        else:
            I_min, I_max = 0.0, 1.0
        if len(valid_V):
            V_min, V_max = float(valid_V.min()), float(valid_V.max())
        else:
            V_min, V_max = 0.0, 1.0
        if I_min == I_max:
            I_min -= 0.5
            I_max += 0.5
        if V_min == V_max:
            V_min -= 0.5
            V_max += 0.5
        return I_min, I_max, V_min, V_max

    def size(self):
        with self.lock:
            return len(self.I)

    def version(self):
        with self.lock:
            return self._version

    def step_average_progress(self):
        with self.lock:
            if self._step_avg_cache is None:
                return 0, len(self._timing_steps or [])
            return self._step_avg_cache[2], self._step_avg_cache[3]

    def start_step_average_prefetch(self):
        if not self._timing_steps:
            return
        if self._step_avg_thread and self._step_avg_thread.is_alive():
            return
        self._step_avg_thread = threading.Thread(
            target=self._step_average_worker,
            daemon=True,
        )
        self._step_avg_thread.start()

    def _step_average_worker(self):
        try:
            steps = list(self._timing_steps or [])
            if not steps:
                return
            avg_i = []
            avg_v = []
            with h5open(self.h5_path, "r", **self._s3_kwds) as f:
                data = f["data"]
                keys = sorted(data.keys(), key=lambda key: int(key))
                times = [float(data[key].attrs.get("time", int(key))) for key in keys]

                for si, step in enumerate(steps):
                    ramp_start = step["ramp_start"]
                    ramp_end = step["ramp_end"]
                    stable_end = step["stable_end"]
                    avg_start = stable_end - self._average_time if self._average_time is not None else ramp_end
                    step_indices = [
                        i for i, t in enumerate(times)
                        if ramp_start <= t < stable_end
                    ]
                    if not step_indices:
                        self._publish_step_average(avg_i, avg_v, len(steps))
                        continue

                    # Compute V for all frames in step (shared by vt_step cache and I-V average)
                    frame_vt = []
                    for i in step_indices:
                        t_val = times[i]
                        d = data[keys[i]]
                        try:
                            mu_rs = np.array(d["running_state/mu"])
                            dt_rs = np.array(d["running_state/dt"])
                            voltage_samples = mu_rs[0] - mu_rs[1]
                            dt_sum = float(dt_rs.sum())
                            if dt_sum > 0:
                                v_val = float(np.sum(voltage_samples * dt_rs) / dt_sum)
                            else:
                                v_val = float(voltage_samples.mean())
                        except Exception:
                            v_val = float("nan")
                        if self._tc_fn is not None:
                            try:
                                tc = self._tc_fn(t_val)
                                i_val = float(tc["source"]) if isinstance(tc, dict) else float(tc)
                            except Exception:
                                i_val = step["je_end"]
                        else:
                            i_val = step["je_end"]
                        frame_vt.append((int(keys[i]), t_val, i_val, v_val))

                    # IMPORTANT: Pre-populate vt_step cache here to avoid playback stutter.
                    # Without this, vt_step() would have a cache miss at every Je step
                    # boundary (~every 8 frames), requiring a full HDF5 scan of all
                    # frames in the new step over ROS3 (~200ms, far exceeding the 100ms
                    # frame budget at 10 FPS). This is the #1 cause of periodic stutter.
                    vt_times = [fv[1] - ramp_start for fv in frame_vt]
                    vt_volts = [fv[3] for fv in frame_vt]
                    with self.lock:
                        self._vt_step_cache[si + 1] = (
                            frame_vt[-1][1],
                            np.array(vt_times, dtype=np.float64),
                            np.array(vt_volts, dtype=np.float64),
                        )
                        for frame_idx, t_val, i_val, v_val in frame_vt:
                            self._frame_iv_cache[frame_idx] = (i_val, v_val, t_val)

                    # I-V averaging over [avg_start, stable_end]
                    if times[step_indices[-1]] < avg_start:
                        self._publish_step_average(avg_i, avg_v, len(steps))
                        continue
                    values = [
                        (fv[2], fv[3])
                        for fv in frame_vt
                        if fv[1] >= avg_start and not np.isnan(fv[3])
                    ]
                    if values:
                        avg_i.append(float(np.mean([x[0] for x in values])))
                        avg_v.append(float(np.mean([x[1] for x in values])))
                    self._publish_step_average(avg_i, avg_v, len(steps))
            # IMPORTANT: Only bump _version once after all steps are processed.
            # If incremented per-step, each _publish_step_average triggers
            # buffer.clear() in _status_loop and show(), destroying prefetched
            # frames and causing playback stutter (especially with ramp_down
            # where step count doubles to ~200). Status progress still updates
            # per-step via step_average_progress() which reads _step_avg_cache.
            with self.lock:
                self._version += 1
        except Exception as exc:
            self._step_avg_error = exc

    def _publish_step_average(self, avg_i, avg_v, total_steps):
        with self.lock:
            self._step_avg_cache = (
                np.array(avg_i, dtype=np.float64),
                np.array(avg_v, dtype=np.float64),
                len(avg_i),
                total_steps,
            )

    def set_timing_steps(self, steps, average_time=None):
        """Set timing step boundaries and averaging window for Je-step-averaged I-V.

        Args:
            steps: List of step dicts from build_timing(), each with
                   ramp_start, ramp_end, stable_end.
            average_time: Duration at the end of each step's stable period to
                          average V over. If None, uses the full stable period
                          [ramp_end, stable_end].
        """
        self._timing_steps = steps
        self._average_time = average_time
        self._step_avg_cache = None

    def step_averaged_iv(self, current_frame_idx=None):
        """Return I-V data averaged per completed Je step.

        Groups frames by full step period [ramp_start, stable_end) to determine
        completeness. Averages V over the last average_time of each step's stable
        period: [stable_end - average_time, stable_end]. If average_time is not
        set, uses the full stable period [ramp_end, stable_end].

        A step is complete when its last frame time >= avg_start. When no timing
        steps are set, falls back to raw frame-by-frame data up to
        current_frame_idx.

        Returns:
            (I_arr, V_arr, n_completed_steps, total_steps)
        """
        if self._timing_steps is None:
            upto = current_frame_idx
            i_arr, v_arr, _ = self.arrays(upto=upto)
            if self._debug:
                self._debug.log("step_avg_fallback", n=len(i_arr))
            return i_arr, v_arr, len(i_arr), 0

        with self.lock:
            if self._step_avg_cache is not None:
                avg_i, avg_v, n_completed, n_total = self._step_avg_cache
                return avg_i.copy(), avg_v.copy(), n_completed, n_total

        with self.lock:
            t_all = list(self.t)
            I_all = list(self.I)
            V_all = list(self.V)

        if not t_all:
            return np.array([]), np.array([]), 0, len(self._timing_steps)

        avg_I = []
        avg_V = []
        n_completed = 0

        for step in self._timing_steps:
            ramp_start = step["ramp_start"]
            ramp_end = step["ramp_end"]
            stable_end = step["stable_end"]

            # Group by full step period to find all frames
            indices = [i for i, t in enumerate(t_all) if ramp_start <= t < stable_end]
            if not indices:
                continue

            # Compute averaging window
            if self._average_time is not None:
                avg_start = stable_end - self._average_time
            else:
                avg_start = ramp_end

            # Step is complete when we have frames into the averaging window
            last_t = t_all[indices[-1]]
            if last_t < avg_start:
                continue

            # Average V over [avg_start, stable_end]
            use_idx = [i for i in indices if t_all[i] >= avg_start]
            if not use_idx:
                use_idx = [i for i in indices if t_all[i] >= ramp_end]

            step_V = [V_all[i] for i in use_idx]
            step_I = [I_all[i] for i in use_idx]
            valid = [
                (i, v)
                for i, v in zip(step_I, step_V, strict=True)
                if not np.isnan(v)
            ]

            if valid:
                avg_I.append(float(np.mean([x[0] for x in valid])))
                avg_V.append(float(np.mean([x[1] for x in valid])))
                n_completed += 1

        if self._debug:
            self._debug.log(
                "step_avg", n_completed=n_completed,
                n_total=len(self._timing_steps),
                avg_I=[round(x, 4) for x in avg_I[:5]],
                avg_V=[round(x, 4) for x in avg_V[:5]],
            )

        return (
            np.array(avg_I),
            np.array(avg_V),
            n_completed,
            len(self._timing_steps),
        )

    def table(self, upto=None):
        """Return frame-by-frame I-V data for verification."""
        with self.lock:
            n = len(self.I) if upto is None else min(len(self.I), int(upto) + 1)
            return [
                {"frame": i, "time": self.t[i], "I": self.I[i], "V": self.V[i]}
                for i in range(n)
            ]

    def inspect_running_state(self, h5_path, idx=0, **s3_kwds):
        """Inspect raw running_state data for a frame. Returns shapes and sample values."""
        with h5open(h5_path, "r", **s3_kwds) as f:
            d = f[f"data/{idx}"]
            result = {
                "frame": idx,
                "time": float(d.attrs.get("time", idx)),
                "datasets": sorted(d.keys()),
            }
            if "running_state" in d:
                rs = d["running_state"]
                result["rs_keys"] = sorted(rs.keys())
                for k in rs.keys():
                    arr = np.array(rs[k])
                    result[f"rs_{k}_shape"] = list(arr.shape)
                    result[f"rs_{k}_dtype"] = str(arr.dtype)
                    if arr.size <= 10:
                        result[f"rs_{k}_values"] = arr.tolist()
                    else:
                        result[f"rs_{k}_sample"] = arr.flat[:5].tolist()
            else:
                result["rs_keys"] = []
            return result
