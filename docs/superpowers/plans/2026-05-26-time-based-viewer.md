# Time-Based Viewer with Live Refresh

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the frame-index slider with a simulation-time slider that supports live viewing — the slider spans 0 to total solve time (known from timing params), and as new frames arrive from a running simulation the viewer refreshes automatically.

**Architecture:** The Rust side adds `refresh_index()`, `solve_time()`, `time_to_frame(t)`, and `latest_frame_time()` methods. The Python widget replaces the IntSlider with a FloatSlider on the time axis, runs a background thread that periodically calls `refresh_index()` to pick up new frames, and maps time clicks to frame indices with clamping.

**Tech Stack:** Rust (pyo3), Python (ipywidgets, threading), HDF5 binary index

---

## File Structure

| File | Change |
|------|--------|
| `tdgl-viewer-rust/src/lib.rs` | Add `refresh_index`, `solve_time`, `time_to_frame`, `latest_frame_time` methods |
| `tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py` | Replace IntSlider with time-based FloatSlider + live refresh thread |
| `notebooks/run_py_tdgl.py` | Simplify viewer block (no more manual polling loop) |

---

### Task 1: Rust — Add time-based query methods

**Files:**
- Modify: `tdgl-viewer-rust/src/lib.rs`

- [ ] **Step 1: Add `solve_time` method**

Returns total simulation time from the manifest timing data. This is the `stable_end` of the last timing step, or `timing_params.solve_time` if available.

Add after `total_frames` method (after line 376):

```rust
/// Total solve time for the current run (from timing steps).
fn solve_time(&self) -> PyResult<f64> {
    let idx = self
        .current_run_index
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
    let steps = self.runs[idx].all_timing_steps();
    if steps.is_empty() {
        return Ok(0.0);
    }
    Ok(steps.last().unwrap().stable_end)
}

/// Simulation time at a given frame index.
fn frame_time_at(&self, frame_idx: usize) -> PyResult<Option<f64>> {
    Ok(self.frame_time(frame_idx))
}

/// Find the frame index closest to a given simulation time.
/// Returns the frame whose frame_time is <= t, or the last frame if t exceeds all.
fn time_to_frame(&self, t: f64) -> PyResult<usize> {
    let index = self
        .index
        .as_ref()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
    if index.frame_times.is_empty() {
        return Ok(0);
    }
    // Binary search: find last frame where frame_time <= t
    let pos = index.frame_times.partition_point(|&ft| ft <= t);
    if pos == 0 {
        return Ok(0);
    }
    Ok(pos.saturating_sub(1).min(index.total_frames.saturating_sub(1)))
}

/// Simulation time of the latest available frame.
fn latest_frame_time(&self) -> PyResult<f64> {
    let index = self
        .index
        .as_ref()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
    if index.frame_times.is_empty() {
        return Ok(0.0);
    }
    Ok(index.frame_times[index.total_frames.saturating_sub(1)])
}

/// Refresh the HDF5 index by clearing cache and re-scanning from MinIO.
/// Returns the new total_frames count.
fn refresh_index(&mut self) -> PyResult<usize> {
    let idx = self
        .current_run_index
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
    let run_id = &self.runs[idx].run_id;

    // Clear cached index to force re-download
    hdf5_index::clear_index_cache(Some(run_id));

    let index = hdf5_index::build_index(&self.client, run_id)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

    // Rebuild interpolation grid (mesh doesn't change, but reader needs new index)
    let reader = frame_reader::FrameReader::new(&self.client, run_id, &index);
    let sites = reader
        .read_mesh_sites()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
    self.interp = Some(InterpolationGrid::new(&sites, NX, NY));

    let total = index.total_frames;
    self.buffer.clear();
    self.step_vt_cache.clear();
    self.index = Some(index);
    Ok(total)
}
```

- [ ] **Step 2: Build and verify compilation**

Run: `cd tdgl-viewer-rust && maturin develop --release`
Expected: builds without errors

- [ ] **Step 3: Test new methods interactively**

```python
import sys; sys.path.insert(0, 'src')
from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as RV
v = RV('http://localhost:30900')
v.open(run_index=0)
print('solve_time:', v.solve_time())
print('total_frames:', v.total_frames())
print('latest_frame_time:', v.latest_frame_time())
print('time_to_frame(50.0):', v.time_to_frame(50.0))
print('frame_time_at(0):', v.frame_time_at(0))
print('frame_time_at(66):', v.frame_time_at(66))
n = v.refresh_index()
print('refresh_index:', n)
```

- [ ] **Step 4: Commit**

```bash
git add tdgl-viewer-rust/src/lib.rs
git commit -m "feat: add solve_time, time_to_frame, refresh_index methods to Rust viewer"
```

---

### Task 2: Python widget — Time-based slider with live refresh

**Files:**
- Modify: `tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py`

This is the core UI change. The slider axis becomes simulation time (0 to solve_time). A background thread periodically calls `refresh_index()` to pick up new frames. When the user clicks a time on the slider, it maps to the nearest available frame.

Before changing `display()`, update `__init__` to accept `refresh_interval`:

```python
    def __init__(
        self,
        minio_url="http://localhost:30900",
        fps=10,
        speed=1,
        average_time=0.5,
        show_vt_dot=True,
        refresh_interval=5.0,
    ):
        self._rust = _RustViewer(minio_url)
        self._playing = False
        self._stop = threading.Event()
        self._thread = None
        self._fps = max(1, int(fps))
        self._speed = max(0.1, float(speed))
        self._average_time = float(average_time)
        self._show_vt_dot = bool(show_vt_dot)
        self._refresh_interval = max(1.0, float(refresh_interval))
        self._iv_monitor_thread = None
```

- [ ] **Step 1: Replace IntSlider with FloatSlider and add live refresh**

The full replacement for the `display()` method. Key changes:
1. `time_slider` (FloatSlider, 0 to solve_time) replaces the old `slider` (IntSlider, 0 to total_frames)
2. `_live_thread` periodically calls `refresh_index()` and updates `time_slider.max` and status labels
3. `_time_to_frame(t)` maps a time value to the nearest available frame (clamped to latest if beyond available)
4. The playback loop advances time instead of frame index
5. Speed is in simulation-time units (e.g., speed=2 means advance 2s of sim time per real second)
6. `refresh_input` (BoundedFloatText, min=1.0, default=5.0) controls refresh interval — changing it only affects the next wait cycle, no cache clearing or side effects

Replace the entire `display` method body. The method starts at line 63 and ends at line 311.

```python
    def display(self):
        """Display interactive viewer in Jupyter with time-based slider."""
        runs = self._rust.list_runs()
        if not runs:
            print("No runs found.")
            return

        run_dropdown = widgets.Dropdown(
            options=[(label, i) for i, label in enumerate(runs)],
            description="Run:",
            layout=widgets.Layout(width="clamp(400px, 60vw, 800px)"),
        )

        self._rust.open(run_index=0)
        self._rust.set_show_vt_dot(self._show_vt_dot)

        # Time axis setup
        solve_t = self._rust.solve_time()
        latest_t = self._rust.latest_frame_time()
        total = self._rust.total_frames()

        image = widgets.Image(format="png", width=FRAME_W)
        play_btn = widgets.Button(
            description="Play", icon="play",
            layout=widgets.Layout(width="92px"),
        )
        time_slider = widgets.FloatSlider(
            value=0.0, min=0.0, max=max(solve_t, 0.1), step=0.1,
            continuous_update=False,
            layout=widgets.Layout(width="500px"),
        )
        time_label = widgets.Label(
            value=f"t=0.0 / {solve_t:.1f}",
            layout=widgets.Layout(width="220px"),
        )
        fps_slider = widgets.IntSlider(
            value=self._fps, min=1, max=30, description="FPS",
            continuous_update=False,
            layout=widgets.Layout(width="180px"),
        )
        speed_input = widgets.BoundedFloatText(
            value=self._speed, min=0.1, max=10000, description="Speed",
            layout=widgets.Layout(width="150px"),
        )
        vt_dot_check = widgets.Checkbox(
            value=self._show_vt_dot, description="V(t) dot",
            indent=False,
            layout=widgets.Layout(width="110px"),
        )
        avg_input = widgets.BoundedFloatText(
            value=self._average_time, min=0.1, max=1.0, step=0.05,
            description="Avg",
            style={"description_width": "32px"},
            layout=widgets.Layout(width="100px"),
        )
        progress_label = widgets.Label(
            value=f"simulated: {latest_t:.1f} / {solve_t:.1f}",
            layout=widgets.Layout(width="200px"),
        )
        refresh_input = widgets.BoundedFloatText(
            value=self._refresh_interval, min=1.0, max=300.0, step=1.0,
            description="Refresh(s)",
            style={"description_width": "70px"},
            layout=widgets.Layout(width="120px"),
        )
        iv_status = widgets.Label(value="I-V: idle")
        status = widgets.Label(value="ready")

        # Frame cache
        _cache = {}
        _cache_lock = threading.Lock()
        _current_frame = [0]
        _render_token = [0]
        _play_token = [0]
        _suppress_slider = [False]
        _latest_frame = [total - 1]  # index of latest available frame
        _solve_time = [solve_t]
        _live_stop = threading.Event()
        _live_thread = [None]

        def _time_to_frame(t):
            """Map a time value to the nearest available frame, clamped."""
            f = self._rust.time_to_frame(t)
            return min(f, _latest_frame[0])

        def _evict_for(center):
            step = max(1, int(self._speed))
            keep_min = center
            keep_max = center + step * (PREFETCH_AHEAD + 2)
            with _cache_lock:
                for k in list(_cache):
                    if k < keep_min or k > keep_max:
                        del _cache[k]

        def _prefetch(center):
            if center != _current_frame[0]:
                return
            total = self._rust.total_frames()
            step = max(1, int(self._speed))
            for n in range(1, PREFETCH_AHEAD + 1):
                if center != _current_frame[0]:
                    return
                i = center + n * step
                if i >= total:
                    break
                with _cache_lock:
                    if i in _cache:
                        continue
                png = self._rust.render_frame(i)
                if center != _current_frame[0]:
                    return
                with _cache_lock:
                    _cache[i] = png
            _evict_for(center)

        def _render(frame_idx):
            frame_idx = max(0, min(frame_idx, _latest_frame[0]))
            _current_frame[0] = frame_idx
            _render_token[0] += 1
            token = _render_token[0]
            # Update time slider to match frame
            ft = self._rust.frame_time_at(frame_idx)
            t_val = ft if ft is not None else 0.0
            if abs(time_slider.value - t_val) > 0.05:
                _suppress_slider[0] = True
                time_slider.value = t_val
                _suppress_slider[0] = False
            total = self._rust.total_frames()
            time_label.value = f"t={t_val:.1f} / {_solve_time[0]:.1f}"
            status.value = f"frame {frame_idx}/{total - 1}"
            _evict_for(frame_idx)

            with _cache_lock:
                png = _cache.get(frame_idx)
            if png is not None:
                image.value = png
                status.value = f"frame {frame_idx}/{total - 1}"
                threading.Thread(target=_prefetch, args=(frame_idx,), daemon=True).start()
                return

            def _render_worker(fi, rt):
                png = self._rust.render_frame(fi)
                with _cache_lock:
                    _cache[fi] = png
                if _render_token[0] == rt and _current_frame[0] == fi:
                    image.value = png
                    total = self._rust.total_frames()
                    status.value = f"frame {fi}/{total - 1}"
                    _prefetch(fi)

            threading.Thread(target=_render_worker, args=(frame_idx, token), daemon=True).start()

        def _live_refresh():
            """Background thread: refresh index to pick up new frames.
            Reads self._refresh_interval each cycle — changing it only affects the next wait.
            """
            while not _live_stop.is_set():
                _live_stop.wait(max(1.0, self._refresh_interval))
                if _live_stop.is_set():
                    break
                try:
                    n = self._rust.refresh_index()
                    lt = self._rust.latest_frame_time()
                    _latest_frame[0] = n - 1
                    progress_label.value = f"simulated: {lt:.1f} / {_solve_time[0]:.1f}"
                except Exception:
                    pass

        # Start live refresh
        _live_stop.clear()
        t = threading.Thread(target=_live_refresh, daemon=True)
        t.start()
        _live_thread[0] = t

        # Start IV scanner
        def _start_iv(average_time):
            try:
                self._rust.start_iv_scan(average_time=average_time)
                iv_status.value = "I-V: scanning..."
            except Exception as e:
                iv_status.value = f"I-V: {e}"

        def _monitor_iv():
            while not self._stop.is_set():
                try:
                    prog = json.loads(self._rust.get_iv_progress())
                    done = prog.get("done", False)
                    completed = prog.get("steps_completed", 0)
                    total_s = prog.get("steps_total", 0)
                    if done:
                        iv_status.value = f"I-V: done ({completed}/{total_s})"
                        break
                    else:
                        iv_status.value = f"I-V: {completed}/{total_s}..."
                except Exception:
                    pass
                self._stop.wait(0.5)

        def on_avg_change(change):
            avg = float(change["new"])
            self._average_time = avg
            self._rust.stop_iv_scan()
            _start_iv(avg)
            if not (self._iv_monitor_thread and self._iv_monitor_thread.is_alive()):
                self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
                self._iv_monitor_thread.start()

        def on_dropdown(change):
            idx = change["new"]
            _play_token[0] += 1
            self._stop_playback()
            _live_stop.set()
            self._rust.stop_iv_scan()
            self._rust.open(run_index=idx)
            self._rust.set_show_vt_dot(self._show_vt_dot)
            st = self._rust.solve_time()
            _solve_time[0] = st
            time_slider.max = max(st, 0.1)
            time_slider.value = 0.0
            _cache.clear()
            total = self._rust.total_frames()
            _latest_frame[0] = total - 1
            progress_label.value = f"simulated: {self._rust.latest_frame_time():.1f} / {st:.1f}"
            _render(0)
            _start_iv(avg_input.value)
            self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
            self._iv_monitor_thread.start()
            # Restart live refresh
            _live_stop.clear()
            t = threading.Thread(target=_live_refresh, daemon=True)
            t.start()
            _live_thread[0] = t

        def on_time_slider(change):
            if _suppress_slider[0]:
                return
            was_playing = self._playing
            if was_playing:
                _play_token[0] += 1
            t_val = change["new"]
            frame_idx = _time_to_frame(t_val)
            _render(frame_idx)
            if was_playing:
                self._start_playback(
                    time_slider, image, time_label, status, play_btn,
                    _cache, _cache_lock, _suppress_slider, _play_token,
                    progress_label,
                )

        def on_play(_):
            if self._playing:
                self._stop_playback()
                play_btn.description = "Play"
                play_btn.icon = "play"
            else:
                self._start_playback(
                    time_slider, image, time_label, status, play_btn,
                    _cache, _cache_lock, _suppress_slider, _play_token,
                    progress_label,
                )

        def on_fps(change):
            self._fps = change["new"]

        def on_speed(change):
            self._speed = max(0.1, float(change["new"]))
            _evict_for(_current_frame[0])
            threading.Thread(target=_prefetch, args=(_current_frame[0],), daemon=True).start()

        def on_vt_dot(change):
            self.set_show_vt_dot(bool(change["new"]))
            with _cache_lock:
                _cache.clear()
            _render(_current_frame[0])

        def on_refresh(change):
            # Only update the interval — next _live_refresh cycle picks it up.
            # No cache clearing, no thread restart, no side effects.
            self._refresh_interval = max(1.0, float(change["new"]))

        run_dropdown.observe(on_dropdown, names="value")
        time_slider.observe(on_time_slider, names="value")
        play_btn.on_click(on_play)
        fps_slider.observe(on_fps, names="value")
        speed_input.observe(on_speed, names="value")
        vt_dot_check.observe(on_vt_dot, names="value")
        avg_input.observe(on_avg_change, names="value")
        refresh_input.observe(on_refresh, names="value")

        # Auto-start IV scan
        _start_iv(self._average_time)
        self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
        self._iv_monitor_thread.start()

        # Initial render
        _render(0)

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, time_slider, time_label]),
            widgets.HBox(
                [fps_slider, speed_input, vt_dot_check, avg_input, iv_status, refresh_input, progress_label],
                layout=widgets.Layout(gap="16px"),
            ),
            image,
        ], layout=widgets.Layout(gap="6px", padding="4px"))
        display(ui)
```

- [ ] **Step 2: Update `_start_playback`, `_stop_playback`, and `_loop` to work with time axis**

Replace the playback methods. The key change: `_loop` now advances simulation time instead of frame index.

Replace `_start_playback` (line 313), `_stop_playback` (line 333), and `_loop` (line 340):

```python
    def _start_playback(
        self, time_slider, image, time_label, status, play_btn,
        cache, cache_lock, suppress_slider, play_token,
        progress_label,
    ):
        self._playing = True
        self._stop.clear()
        play_token[0] += 1
        token = play_token[0]
        play_btn.description = "Pause"
        play_btn.icon = "pause"
        self._thread = threading.Thread(
            target=self._loop,
            args=(
                time_slider, image, time_label, status, cache, cache_lock,
                suppress_slider, play_token, token, progress_label,
            ),
            daemon=True,
        )
        self._thread.start()

    def _stop_playback(self):
        self._playing = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(
        self, time_slider, image, time_label, status, cache, cache_lock,
        suppress_slider, play_token, token, progress_label,
    ):
        while not self._stop.is_set():
            if play_token[0] != token:
                break

            # Get current time from slider
            current_t = time_slider.value
            solve_t = self._rust.solve_time()

            # Advance by speed * interval (in sim time)
            speed = max(0.1, float(self._speed))
            interval = 1.0 / self._fps
            next_t = current_t + speed * interval

            if next_t >= solve_t:
                next_t = solve_t
                self._stop.set()

            # Map time to frame
            next_frame = self._rust.time_to_frame(next_t)
            latest = self._rust.total_frames() - 1
            if next_frame >= latest and next_t >= solve_t:
                self._stop.set()

            # Render
            with cache_lock:
                png = cache.get(next_frame)

            if png is None:
                t0 = time.perf_counter()
                png = self._rust.render_frame(next_frame)
                if play_token[0] != token or self._stop.is_set():
                    break
                with cache_lock:
                    cache[next_frame] = png
            else:
                t0 = time.perf_counter()

            if play_token[0] != token or self._stop.is_set():
                break

            image.value = png
            ft = self._rust.frame_time_at(next_frame)
            t_val = ft if ft is not None else next_t
            suppress_slider[0] = True
            time_slider.value = t_val
            suppress_slider[0] = False
            time_label.value = f"t={t_val:.1f} / {solve_t:.1f}"
            total = self._rust.total_frames()
            status.value = f"frame {next_frame}/{total - 1}"

            # Prefetch
            threading.Thread(
                target=self._prefetch_range,
                args=(next_frame + 1, PREFETCH_AHEAD, 1, cache, cache_lock),
                daemon=True,
            ).start()

            elapsed = time.perf_counter() - t0
            remaining = max(0.0, interval - elapsed)
            self._stop.wait(remaining)
```

- [ ] **Step 3: Build and test in Jupyter**

Run: `cd tdgl-viewer-rust && maturin develop --release`
Expected: builds without errors

Then test in a notebook:
```python
from tdgl_viewer_rust.widget import TdglViewer
v = TdglViewer("http://localhost:30900", fps=10, speed=5, average_time=0.5, show_vt_dot=True)
v.open(run_id="20260526-070246-88f6a1")
v.display()
```

Verify:
- Slider shows simulation time (0 to ~150)
- Status shows "simulated: 150.0 / 150.0" for completed run
- Play advances through time
- IV scan still works

- [ ] **Step 4: Commit**

```bash
git add tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py
git commit -m "feat: time-based slider with live refresh for viewer"
```

---

### Task 3: Simplify notebook viewer block

**Files:**
- Modify: `notebooks/run_py_tdgl.py`

The viewer block no longer needs a manual polling loop — `display()` now handles live refresh internally.

- [ ] **Step 1: Replace viewer block (lines 127-171)**

Replace the entire viewer block with:

```python
#%%
# ── Open viewer (live refresh built-in) ───────────────────────────────────
import time, httpx

viewer = TdglViewer(
    MINIO_URL,
    fps=10,
    speed=5,
    average_time=0.5,
    show_vt_dot=True,
    refresh_interval=5.0,
)

print(f"Run: {run_id}")
# Wait for data to appear in MinIO
while True:
    try:
        viewer.open(run_id=run_id)
        print(f"  {viewer.total_frames()} frames loaded, solve_time={viewer._rust.solve_time():.1f}")
        break
    except Exception:
        # Check if workflow failed
        try:
            r = httpx.get(f"{ARGO_URL}/api/v1/workflows/tdgl/{wf_name}", verify=False, timeout=5)
            phase = (r.json().get("status") or {}).get("phase", "Unknown")
            if phase in ("Failed", "Error"):
                print(f"  Workflow {phase}")
                raise SystemExit(1)
            print(f"\r  [{phase}] waiting for data...", end="", flush=True)
        except SystemExit:
            raise
        except Exception:
            print(f"\r  waiting for data...", end="", flush=True)
    time.sleep(3)

viewer.display()
```

- [ ] **Step 2: Run notebook end-to-end**

Submit a new workflow and verify:
1. Viewer opens when data first appears (during Running phase)
2. Live refresh picks up new frames automatically
3. Time slider spans 0 to solve_time
4. "simulated" label updates as frames arrive
5. When simulation completes, viewer has all frames

- [ ] **Step 3: Commit**

```bash
git add notebooks/run_py_tdgl.py
git commit -m "feat: simplify notebook viewer block with built-in live refresh"
```

---

## Self-Review

**1. Spec coverage:**
- Time-based slider axis (known from timing): Task 2 ✅
- Internal logic still uses frames: Tasks 1, 2 ✅
- Click-to-seek with fallback to latest: Task 2 `_time_to_frame()` ✅
- Live refresh of frame index: Task 1 `refresh_index()`, Task 2 `_live_refresh()` ✅
- Progress tracking (simulated vs total): Task 2 `progress_label` ✅
- Configurable refresh interval (constructor param + UI input, min 1s): Task 2 ✅
- Refresh interval change only affects next cycle, no side effects: Task 2 `on_refresh()` ✅

**2. Placeholder scan:** No TBD/TODO found. All code shown inline.

**3. Type consistency:**
- `solve_time()` returns `f64` in Rust, exposed as Python float → `time_slider.max` is float ✅
- `time_to_frame(t)` takes `f64` → returns `usize` → used as frame index ✅
- `frame_time_at(idx)` takes `usize` → returns `Option<f64>` ✅
- `latest_frame_time()` returns `f64` → used in progress label ✅
- `refresh_index()` returns `usize` (total_frames) ✅
- `speed_input` changed to `BoundedFloatText` to allow fractional speed ✅
- `refresh_input` is `BoundedFloatText(min=1.0)`, stored as `self._refresh_interval` (float) ✅
- `_live_refresh` reads `self._refresh_interval` each cycle, not cached locally ✅
