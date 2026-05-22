# Real-Time Frame Player Design

Streaming-style heatmap player for TDGL HDF5 simulation data in JupyterLab.

## Problem

The previous Plotly-based heatmap viewer loads all frames upfront and requires the simulation to complete before rendering. We need a player that works like a video streamer: frames arrive asynchronously, the player shows what's available, and memory stays bounded.

## Data Characteristics

- HDF5 file with `data/{idx}` groups containing `psi` (complex128, shape 860)
- 860 mesh sites, interpolated to 100×50 grid via `griddata`
- Per-frame raw: 13.4 KB; interpolated: 40 KB
- Total frames: 3376; total data: 44.3 MB
- Frame existence is asynchronous — any frame may not yet be written

## Architecture

```
FrameSource (HDF5 adapter)
    ↕ h5py
FrameCache (sliding window, ~7 frames)
    ↕ ensure(idx)
FramePlayer (playback controller)
    ↕ im.set_data()
ipympl Figure (inline rendering)
    +
ipywidgets (Play/Pause, Slider, Label)
```

## Components

### FrameSource

HDF5 adapter that knows how to check existence and load a single frame.

```python
class FrameSource:
    def __init__(self, h5_path: str, points: np.ndarray,
                 grid_pts: np.ndarray, nx: int, ny: int)

    def frame_exists(self, idx: int) -> bool
    def load_frame(self, idx: int) -> np.ndarray    # shape (ny, nx)
    @property
    def latest_available(self) -> int
    @property
    def total_frames(self) -> int
```

- `frame_exists`: open HDF5 read-only, check `f'data/{idx}'` in group keys
- `load_frame`: read psi → abs → griddata cubic interpolation → clip to [0, ∞)
- `latest_available`: scan HDF5 data group, return max index + 1
- `total_frames`: fixed value set at construction

### FrameCache

Sliding window cache with background prefetch.

```python
class FrameCache:
    def __init__(self, source: FrameSource,
                 prefetch_ahead: int = 5, keep_behind: int = 2)

    def get(self, idx: int) -> np.ndarray | None
    def ensure(self, idx: int) -> np.ndarray
    def prefetch(self, from_idx: int, count: int) -> None
    def evict(self, before_idx: int) -> None
```

- `_cache: dict[int, ndarray]` — frame index → interpolated 2D array
- `_lock: threading.Lock` — protects `_cache`
- `get`: return cached frame or None (non-blocking)
- `ensure`: blocking load if not cached, triggers prefetch ahead and evict behind
- `prefetch`: spawns daemon thread to load `count` frames starting from `from_idx`, skips non-existent frames
- `evict`: removes all cached frames with index < `before_idx`

Cache window at any time: `[current - keep_behind, current + prefetch_ahead]`. Memory: ~7 frames × 40 KB ≈ 280 KB.

### FramePlayer

Playback controller with Timer-driven tick loop.

```python
class FramePlayer:
    def __init__(self, source: FrameSource, cache: FrameCache,
                 interval_ms: int = 100)

    def play(self) -> None
    def pause(self) -> None
    def jump(self, idx: int) -> None
    def _tick(self) -> None
    def _render(self) -> None
    def _poll_new_frames(self) -> None
```

State:
- `_playing: bool`
- `_current: int` — current frame index
- `_latest: int` — latest available frame index (updated by poller)

`play()`: set `_playing = True`, call `_tick()`.
`pause()`: set `_playing = False`.
`jump(idx)`:
  - If `source.frame_exists(idx)`: set `_current = idx`, render
  - Else: set `_current = _latest`, render
  - If playing: schedule `_tick()`

`_tick()`:
  - If not playing: return
  - `next = _current + 1`
  - If `next >= total_frames`: pause, return
  - If `not source.frame_exists(next)`: schedule retry in 200ms, return
  - Set `_current = next`, render, schedule next tick after `interval_ms`

`_render()`:
  - `Z = cache.ensure(_current)`
  - `im.set_data(Z)`, update title text, update slider value
  - `fig.canvas.draw_idle()`

`_poll_new_frames()`:
  - Update `_latest = source.latest_available`
  - Update `slider.max = _latest`
  - If simulation not done: schedule next poll in 2s

## UI Layout

```
┌─────────────────────────────────────────────┐
│ [▶/⏸]  [═══════════╪═══════════]  120/3376 │
├─────────────────────────────────────────────┤
│                                             │
│           ipympl heatmap (imshow)           │
│           700×350 px, inferno cmap          │
│                                             │
└─────────────────────────────────────────────┘
```

Widgets:
- `ToggleButton`: play/pause (▶/⏸ icons)
- `IntSlider`: progress bar, `min=0`, `max=total_frames-1`, `continuous_update=False`
- `Label`: frame counter `"120 / 3376"`
- Layout: `VBox([HBox([btn, slider, label]), fig.canvas])`

Interactions:
- Click Play → `player.play()`
- Click Pause → `player.pause()`
- Drag slider → `player.jump(idx)`
- Slider `continuous_update=False` to avoid flooding callbacks during drag

## Notebook Structure

```
Cell 1: Imports + %matplotlib widget + configuration
Cell 2: FrameSource class
Cell 3: FrameCache class
Cell 4: FramePlayer class (includes UI setup)
Cell 5: Instantiate and display player
```

New file: `notebooks/frame_player.ipynb`

## Dependencies

- `ipympl` (needs `pip install ipympl`, enables `%matplotlib widget`)
- `ipywidgets` (already installed)
- `matplotlib` (already installed)
- `h5py`, `scipy`, `numpy` (already installed)
- `plotly` NOT used — ipympl replaces it

## Jump Behavior

```
User jumps to frame X:
  if X exists:
    current = X, render immediately
  else:
    current = latest_available
    render latest_available
    if playing: tick loop waits for new frames, resumes when X or later arrives
```

## Frame Detection During Live Simulation

When `_poll_new_frames` detects a new `_latest` value:
1. Update `slider.max` so the user can see progress
2. If player is paused at `_latest` and was waiting: auto-resume by jumping to new frame
3. If player is playing and hits a non-existent frame: tick retries every 200ms

## Testing

Test notebook reads from existing `sim_output.h5` (3376 frames). All frames are pre-generated, so `frame_exists` always returns True for valid indices. The async behavior can be simulated by setting `total_frames` higher than actual available frames.

## Scope

- Single HDF5 file, in-process reading only
- No remote/K8s simulation support in this iteration
- No audio/metadata tracks
- No frame export or save functionality
