import time
from pathlib import Path


class DebugLog:
    """File-based event logger for live player debugging.

    When debug=True, every log() call immediately appends to a local file
    so the log can be read at any time during or after the simulation.
    """

    def __init__(self, path="debug-tdgl.log"):
        self._path = Path(path)
        self._t0 = time.perf_counter()
        self._fh = open(self._path, "w")
        self._fh.write(f"=== DebugLog started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        self._fh.flush()

    def log(self, event, **data):
        ts = time.perf_counter() - self._t0
        parts = ", ".join(f"{k}={v!r}" for k, v in data.items())
        self._fh.write(f"[{ts:8.3f}s] {event}  {parts}\n")
        self._fh.flush()

    def close(self):
        if self._fh and not self._fh.closed:
            self._fh.close()

    @property
    def path(self):
        return str(self._path)
