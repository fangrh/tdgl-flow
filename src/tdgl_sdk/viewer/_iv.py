import pickle
import threading

import h5py
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
        self._debug = debug_log

        self._tc_fn = _load_terminal_currents(h5_path, **s3_kwds)

    def start(self):
        self.stop()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=1)

    def _frame_iv(self, f, idx):
        d = f[f"data/{idx}"]
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
            V_val = float(np.sum(voltage_samples * dt_rs) / dt_sum) if dt_sum > 0 else float(voltage_samples.mean())
        except Exception:
            V_val = float("nan")
        return I_val, V_val, t_val

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
        I, V, _ = self.arrays(upto=upto)
        if len(I) == 0:
            return 0.0, 1.0, 0.0, 1.0
        valid_I = I[~np.isnan(I)]
        valid_V = V[~np.isnan(V)]
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

    def set_timing_steps(self, steps):
        """Set timing step boundaries for Je-step-averaged I-V."""
        self._timing_steps = steps

    def step_averaged_iv(self, current_frame_idx=None):
        """Return I-V data averaged per completed Je step.

        Groups frames by full step period [ramp_start, stable_end) then
        averages V over the stable period [ramp_end, stable_end] when
        frames exist there, falling back to all frames in the step.

        A step is complete when its last frame time >= ramp_end (stable
        period has started).  When no timing steps are set, falls back
        to raw frame-by-frame data up to current_frame_idx.

        Returns:
            (I_arr, V_arr, n_completed_steps, total_steps)
        """
        if self._timing_steps is None:
            upto = current_frame_idx
            I, V, _ = self.arrays(upto=upto)
            if self._debug:
                self._debug.log("step_avg_fallback", n=len(I))
            return I, V, len(I), 0

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

            # Group by full step period — much wider than save_time window
            indices = [i for i, t in enumerate(t_all) if ramp_start <= t < stable_end]
            if not indices:
                continue

            # Step is complete when we have frames into the stable period
            last_t = t_all[indices[-1]]
            if last_t < ramp_end:
                continue

            # Prefer averaging over stable period; fall back to all step frames
            stable_idx = [i for i in indices if t_all[i] >= ramp_end]
            use_idx = stable_idx if stable_idx else indices

            step_V = [V_all[i] for i in use_idx]
            step_I = [I_all[i] for i in use_idx]
            valid = [(i, v) for i, v in zip(step_I, step_V) if not np.isnan(v)]

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
