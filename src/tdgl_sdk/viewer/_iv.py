import threading

import h5py
import numpy as np


class IVCache:
    """Incremental I-V cache for an HDF5 file."""

    def __init__(self, h5_path, mesh, poll_interval=1.0, batch_size=64):
        self.h5_path = h5_path
        self._mesh = mesh
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
        cross = self._mesh["cross"]
        edge_dirs = self._mesh["edge_dirs"]
        dual_lengths = self._mesh["dual_lengths"]
        J = np.array(d["normal_current"]) + np.array(d["supercurrent"])
        I_val = float(np.sum(J[cross] * edge_dirs[cross, 0] * dual_lengths[cross]))
        try:
            mu_rs = np.array(d["running_state/mu"])
            dt_rs = np.array(d["running_state/dt"])
            voltage_samples = mu_rs[0] - mu_rs[1]
            dt_sum = float(dt_rs.sum())
            V_val = float(np.sum(voltage_samples * dt_rs) / dt_sum) if dt_sum > 0 else float(voltage_samples.mean())
        except Exception:
            V_val = 0.0
        t_val = float(d.attrs.get("time", idx))
        return I_val, V_val, t_val

    def update_available(self, target=None):
        with self.lock:
            start = len(self.I)
        with h5py.File(self.h5_path, "r") as f:
            available = len(f["data"].keys())
            end = available if target is None else min(available, int(target) + 1)
            while start < end:
                batch_end = min(end, start + self.batch_size)
                batch = [self._frame_iv(f, i) for i in range(start, batch_end)]
                with self.lock:
                    self.I.extend(x[0] for x in batch)
                    self.V.extend(x[1] for x in batch)
                    self.t.extend(x[2] for x in batch)
                    self.last_total = available
                start = batch_end
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

    def ranges(self):
        I, V, _ = self.arrays()
        if len(I) == 0:
            return 0.0, 1.0, 0.0, 1.0
        I_min, I_max = float(I.min()), float(I.max())
        V_min, V_max = float(V.min()), float(V.max())
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
