import time


class DebugLog:
    """Lightweight timestamped event logger for live player debugging.

    Not the same as the agent diagnostic API (get_status, diagnose_mapping).
    This records a timeline of events as they happen; the agent API returns
    point-in-time snapshots.
    """

    def __init__(self, max_entries=10000):
        self.entries = []
        self._max = max_entries
        self._t0 = time.perf_counter()

    def log(self, event, **data):
        ts = time.perf_counter() - self._t0
        self.entries.append((ts, event, data))
        if len(self.entries) > self._max:
            self.entries = self.entries[-self._max // 2:]

    def clear(self):
        self.entries.clear()
        self._t0 = time.perf_counter()

    def dump(self, last_n=None):
        items = self.entries[-last_n:] if last_n else self.entries
        lines = []
        for ts, event, data in items:
            parts = ", ".join(f"{k}={v!r}" for k, v in data.items())
            lines.append(f"[{ts:8.3f}s] {event}  {parts}")
        return "\n".join(lines)

    def recent(self, n=20):
        return self.entries[-n:]

    def __len__(self):
        return len(self.entries)