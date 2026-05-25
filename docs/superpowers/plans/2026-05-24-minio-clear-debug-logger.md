# MinIO Clear + Debug Logger for Live Player

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `clear_all_runs()` to TDGLRunStore, and insert a timestamped debug logger into the live player pipeline (StreamingTDGLPlayer → RealtimeTDGLWidgetPlayer → IVCache → _draw_iv) that records every significant action when `debug=True`.

**Architecture:** A lightweight `DebugLog` class stores timestamped `(event, data)` entries in memory. It is created when `debug=True` is passed and threaded through the entire pipeline: `watch_live()` → `StreamingTDGLPlayer` → `create_player()` → `RealtimeTDGLWidgetPlayer` + `IVCache`. Each component logs its key actions. Accessible via `player.debug_log.dump()`. Completely inert when `debug=False` (default).

**Tech Stack:** Python stdlib (`time`, `logging`), existing stack (boto3, ipywidgets).

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/tdgl_sdk/viewer/_debug.py` | Create | DebugLog class: timestamped event recording, dump, clear |
| `src/tdgl_sdk/client.py` | Modify | Add `clear_all_runs()` method to TDGLRunStore |
| `src/tdgl_sdk/viewer/_iv.py` | Modify | Add debug_log parameter, log step_averaged_iv results and update_available |
| `src/tdgl_sdk/viewer/_render.py` | Modify | Log _draw_iv inputs and outputs |
| `src/tdgl_sdk/viewer/_player.py` | Modify | Thread debug through create_player/watch_run/StreamingTDGLPlayer, log poll loop, show, playback |
| `src/tdgl_sdk/pipeline.py` | Modify | Thread debug through watch_live |
| `notebooks/e2e_sim_test.py` | Modify | Add clear MinIO cell, pass debug=True |

---

### Task 1: Create DebugLog class

**Files:**
- Create: `src/tdgl_sdk/viewer/_debug.py`

- [ ] **Step 1: Write DebugLog class**

Create `src/tdgl_sdk/viewer/_debug.py`:

```python
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
```

- [ ] **Step 2: Verify import**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._debug import DebugLog; d=DebugLog(); d.log('test', x=1); print(d.dump())"`
Expected: Prints `[    0.00xs] test  x=1`

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_sdk/viewer/_debug.py
git commit -m "feat: add DebugLog class for live player debugging"
```

---

### Task 2: Add clear_all_runs to TDGLRunStore

**Files:**
- Modify: `src/tdgl_sdk/client.py`

- [ ] **Step 1: Add clear_all_runs method**

In `src/tdgl_sdk/client.py`, add after the existing `delete_run` method (after line 95):

```python
    def clear_all_runs(self) -> int:
        """Delete all simulation data from the bucket. Returns count of deleted objects."""
        deleted = 0
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix="tdgl-runs/"):
            for obj in page.get("Contents", []):
                self.s3.delete_object(Bucket=self.bucket, Key=obj["Key"])
                deleted += 1
        return deleted
```

- [ ] **Step 2: Verify import**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.client import TDGLRunStore; assert hasattr(TDGLRunStore, 'clear_all_runs'); print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_sdk/client.py
git commit -m "feat: add clear_all_runs to TDGLRunStore"
```

---

### Task 3: Add debug logging to IVCache

**Files:**
- Modify: `src/tdgl_sdk/viewer/_iv.py`

- [ ] **Step 1: Add debug_log parameter and logging**

In `src/tdgl_sdk/viewer/_iv.py`, modify `IVCache.__init__` to accept and store `debug_log`:

Change the `__init__` signature (line 24):
```python
    def __init__(self, h5_path, mesh, poll_interval=1.0, batch_size=64, debug_log=None, **s3_kwds):
```

Add after `self._timing_steps = None` (line 38):
```python
        self._debug = debug_log
```

- [ ] **Step 2: Add logging to update_available**

In `update_available` (around line 86), add logging at start and end:

After `with self.lock:` / `start = len(self.I)`:
```python
        if self._debug:
            self._debug.log("iv_update_start", cached=start, target=target)
```

After the `while start < end:` loop (after line 100):
```python
        if self._debug:
            self._debug.log("iv_update_done", new=end - start, total=len(self.I))
```

Note: the second log must be AFTER the `with h5open` block but still inside `update_available`. Place it right before `return self.size()`.

- [ ] **Step 3: Add logging to step_averaged_iv**

In `step_averaged_iv`, add logging at the end, right before the final return (around line 206):

```python
        if self._debug:
            self._debug.log(
                "step_avg", n_completed=n_completed,
                n_total=len(self._timing_steps),
                avg_I=[round(x, 4) for x in avg_I[:5]],
                avg_V=[round(x, 4) for x in avg_V[:5]],
            )
```

This goes right before:
```python
        return (
            np.array(avg_I),
            np.array(avg_V),
            n_completed,
            len(self._timing_steps),
        )
```

And in the fallback path (when `_timing_steps is None`), add logging before the early return:
```python
        if self._debug:
            self._debug.log("step_avg_fallback", n=len(I))
```

- [ ] **Step 4: Verify import**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._iv import IVCache; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_sdk/viewer/_iv.py
git commit -m "feat: add debug logging to IVCache (update_available, step_averaged_iv)"
```

---

### Task 4: Add debug logging to _draw_iv

**Files:**
- Modify: `src/tdgl_sdk/viewer/_render.py`

- [ ] **Step 1: Add debug_log parameter to _draw_iv and render_frame_png**

Change `render_frame_png` signature (line 70) to accept debug_log:
```python
def render_frame_png(h5_path, mesh, iv_cache, mu_vmax, idx, debug_log=None, **s3_kwds):
```

Pass it to `_draw_iv` (line 93):
```python
    _draw_iv(draw, iv_cache, idx, (14, 252, 746, 454), debug_log=debug_log)
```

Change `_draw_iv` signature (line 100):
```python
def _draw_iv(draw, iv_cache, idx, box, debug_log=None):
```

Add logging after computing avg data (after line 105):
```python
    if debug_log:
        debug_log.log(
            "draw_iv", frame=idx, cache_size=iv_cache.size(),
            n_avg=len(avg_I), n_completed=n_completed, n_total=n_total,
        )
```

- [ ] **Step 2: Verify import**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._render import render_frame_png; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_sdk/viewer/_render.py
git commit -m "feat: add debug logging to _draw_iv render"
```

---

### Task 5: Thread debug through the player pipeline

**Files:**
- Modify: `src/tdgl_sdk/viewer/_player.py`

- [ ] **Step 1: Add debug_log to create_player**

Change `create_player` signature (around line 612):
```python
def create_player(
    h5_path: str,
    live: bool = False,
    playback_dt: float = 1.0,
    timing_steps: list | None = None,
    debug: bool = False,
    **s3_kwds,
) -> RealtimeTDGLWidgetPlayer:
```

In the function body, create DebugLog when debug=True and pass to IVCache:
```python
    from tdgl_sdk.viewer._debug import DebugLog
    debug_log = DebugLog() if debug else None
```

Pass `debug_log=debug_log` to IVCache constructor:
```python
    iv_cache = IVCache(h5_path, mesh, poll_interval=1.0, batch_size=128, debug_log=debug_log, **s3_kwds)
```

Pass `debug_log=debug_log` to render via the player — store it on the player:
```python
    player = RealtimeTDGLWidgetPlayer(h5_path, mesh, iv_cache, mu_vmax, debug_log=debug_log, **s3_kwds)
```

- [ ] **Step 2: Add debug_log to RealtimeTDGLWidgetPlayer**

In `RealtimeTDGLWidgetPlayer.__init__` (around line 38), add parameter and store:
```python
    def __init__(self, h5_path, mesh, iv_cache, mu_vmax, debug_log=None, **s3_kwds):
        ...
        self._debug = debug_log
```

In `_render` method (around line 162), pass debug_log:
```python
    def _render(self, idx):
        return render_frame_png(
            self.h5_path, self._mesh, self.iv_cache, self.mu_vmax, idx,
            debug_log=self._debug, **self._s3_kwds
        )
```

Add logging in `show` method — after `frame_idx = min(frame_idx, self.total - 1)` (around line 217):
```python
        if self._debug:
            self._debug.log("show", step=step, frame=frame_idx,
                           total_frames=self.total, playing=self.playing)
```

Add logging in `_loop` — at the start of each iteration (after `while not self.stop_event.is_set():`):
```python
            if self._debug:
                self._debug.log("loop_tick", current=self.current,
                               max_step=len(self.time_grid)-1)
```

Add a public `debug_log` property for external access:
```python
    @property
    def debug_log(self):
        return self._debug
```

- [ ] **Step 3: Add debug to watch_run and StreamingTDGLPlayer**

Change `watch_run` signature (around line 641):
```python
def watch_run(
    store, run_id: str, poll_interval: int = 15, argo_host: str | None = None,
    timing_params: dict | None = None, solver_options: dict | None = None,
    playback_dt: float = 1.0, debug: bool = False,
) -> StreamingTDGLPlayer:
```

Pass debug through:
```python
    return StreamingTDGLPlayer(
        store, run_id, poll_interval,
        argo_host=argo_host,
        timing_params=timing_params,
        solver_options=solver_options,
        playback_dt=playback_dt,
        debug=debug,
    )
```

In `StreamingTDGLPlayer.__init__` (around line 433), add `debug=False` parameter:
```python
    def __init__(self, store, run_id, poll_interval=15, argo_host=None,
                 timing_params=None, solver_options=None, playback_dt=1.0, debug=False):
```

Store debug flag and create log:
```python
        self._debug_flag = debug
        from tdgl_sdk.viewer._debug import DebugLog
        self._debug = DebugLog() if debug else None
```

In `_poll_loop`, add logging at key points. After each `manifest = self.store.get_run(...)`:
```python
                if self._debug:
                    self._debug.log("poll", manifest_status=manifest.get("status") if manifest else None,
                                   n_frames=n_frames if 'n_frames' in dir() else 0)
```

Note: `n_frames` is only defined inside the `if status in ("running", "completed"):` block. Move the logging to after the status check:

After `n_frames` is computed (inside the `if status in ("running", "completed"):` block, around line 498):
```python
                    if self._debug:
                        self._debug.log("poll_frames", status=status, n_frames=n_frames, solve_time=solve_time)
```

In `_create_player`, pass debug flag:
```python
        self._player = create_player(
            self._h5_url, live=(status == "running"),
            playback_dt=self._playback_dt,
            timing_steps=timing_steps,
            debug=self._debug_flag,
            **self._s3_kwds,
        )
```

Add `debug_log` property:
```python
    @property
    def debug_log(self):
        return self._debug if self._debug else (self._player.debug_log if self._player else None)
```

- [ ] **Step 4: Verify import**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._player import create_player, watch_run, StreamingTDGLPlayer; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_sdk/viewer/_player.py
git commit -m "feat: thread debug log through player pipeline"
```

---

### Task 6: Thread debug through SimulationPipeline.watch_live

**Files:**
- Modify: `src/tdgl_sdk/pipeline.py`

- [ ] **Step 1: Add debug parameter to watch_live**

In `src/tdgl_sdk/pipeline.py`, change `watch_live` signature:
```python
    def watch_live(
        self,
        run_id: str,
        poll_interval: int = 15,
        timing_params: dict | None = None,
        solver_options: dict | None = None,
        playback_dt: float = 1.0,
        debug: bool = False,
    ):
```

Pass `debug=debug` to `watch_run`:
```python
        return watch_run(
            self.store, run_id,
            poll_interval=poll_interval,
            argo_host=self.argo_url,
            timing_params=timing_params,
            solver_options=solver_options,
            playback_dt=playback_dt,
            debug=debug,
        )
```

- [ ] **Step 2: Verify import**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk import SimulationPipeline; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_sdk/pipeline.py
git commit -m "feat: thread debug flag through SimulationPipeline.watch_live"
```

---

### Task 7: Update notebook — clear MinIO cell + debug activation

**Files:**
- Modify: `notebooks/e2e_sim_test.py`

- [ ] **Step 1: Add clear MinIO cell**

Add a new cell BEFORE Step 1 (between the Config cell and Step 1), as Step 0:

```python
#%%
# ── Step 0: Clear MinIO (optional) ───────────────────────────────────────
# Uncomment to delete all previous simulation data from MinIO.
# pipeline = SimulationPipeline(argo_url=ARGO_URL, minio_endpoint=MINIO_ENDPOINT)
# deleted = pipeline.store.clear_all_runs()
# print(f"Deleted {deleted} objects from MinIO")
```

- [ ] **Step 2: Enable debug on watch_live**

In Step 2 (around line 87), add `debug=True`:

```python
live_player = pipeline.watch_live(
    run_id, poll_interval=10,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
    debug=True,
)
```

- [ ] **Step 3: Add debug dump cell**

Add a new cell AFTER Step 3 (workflow status check):

```python
#%%
# ── Step 3b: Debug log ─────────────────────────────────────────────────
# View the debug log to trace what the player is doing.
log = live_player.debug_log
if log:
    print(log.dump(last_n=40))
else:
    print("Debug not enabled (pass debug=True to watch_live)")
```

- [ ] **Step 4: Verify syntax**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "import ast; ast.parse(open('notebooks/e2e_sim_test.py').read()); print('Syntax OK')"`

- [ ] **Step 5: Commit**

```bash
git add notebooks/e2e_sim_test.py
git commit -m "feat: add MinIO clear cell and debug=True to notebook"
```

---

### Task 8: Smoke test

- [ ] **Step 1: Verify full import chain and debug flow**

Run:
```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "
from tdgl_sdk.viewer._debug import DebugLog
from tdgl_sdk.viewer._iv import IVCache
from tdgl_sdk.viewer._render import render_frame_png
from tdgl_sdk.viewer._player import create_player, watch_run, StreamingTDGLPlayer
from tdgl_sdk.client import TDGLRunStore
from tdgl_sdk.pipeline import SimulationPipeline
import inspect

# Verify DebugLog
d = DebugLog()
d.log('test', x=1)
assert len(d) == 1
print(d.dump())

# Verify clear_all_runs exists
assert hasattr(TDGLRunStore, 'clear_all_runs')

# Verify debug param in create_player
sig = inspect.signature(create_player)
assert 'debug' in sig.parameters

# Verify debug param in watch_run
sig2 = inspect.signature(watch_run)
assert 'debug' in sig2

# Verify debug param in SimulationPipeline.watch_live
sig3 = inspect.signature(SimulationPipeline.watch_live)
assert 'debug' in sig3

print('All checks passed')
"
```
Expected: `All checks passed`

- [ ] **Step 2: Commit if needed**

```bash
git add -A
git commit -m "chore: debug logger + MinIO clear — smoke test verified"
```
