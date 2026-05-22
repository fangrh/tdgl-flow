# Real-Time Frame Player Design (ipycanvas)

Streaming-style heatmap player for TDGL HDF5 simulation data in JupyterLab.

## Problem

The current ipympl-based frame player has persistent rendering issues in JupyterLab — widgets don't display reliably despite multiple fix attempts (4 commits). We need a player that renders reliably and supports asynchronous frame streaming with bounded memory.

## Design Decision

Replace ipympl with **ipycanvas** for rendering. Rationale:

- ipycanvas renders directly to HTML5 Canvas via a single `put_image_data()` call — no figure managers, no IOLoop, no display issues
- ipympl required 4 fix commits and still doesn't render reliably in JupyterLab
- Colormap applied via numpy using matplotlib.cm LUT (import only, no figures)
- Text range label instead of colorbar (YAGNI — gradient bar not needed for monitoring)

## Data Characteristics

- HDF5 file with `data/{idx}` groups containing `psi` (complex128, shape 860)
- 860 mesh sites, interpolated to 100x50 grid via `griddata`
- Per-frame raw: 13.4 KB; interpolated: 40 KB
- Total frames: 3376; total data: 44.3 MB
- Frame existence is asynchronous — any frame may not yet be written

## Architecture

```
HDF5 file
    |  h5py (read-only, per-frame)
FrameSource
    |  load_frame(idx) -> np.ndarray (ny, nx)
FrameCache (sliding window, ~7 frames)
    |  ensure(idx) -> np.ndarray
FramePlayer (playback controller + UI)
    |  cmap(norm) -> RGBA -> put_image_data()
ipycanvas Canvas
    +
ipywidgets (ToggleButton, IntSlider, Label)
```

Three classes, single-direction data flow. No circular dependencies.

## Components

### FrameSource

HDF5 adapter. Unchanged from current implementation.

```python
class FrameSource:
    def __init__(self, h5_path: str, points: np.ndarray,
                 grid_pts: np.ndarray, nx: int, ny: int,
                 total_frames: int = None)

    def frame_exists(self, idx: int) -> bool
    def load_frame(self, idx: int) -> np.ndarray    # shape (ny, nx)
    def build_interpolation_grid(self) -> tuple[np.ndarray, np.ndarray]

    @property
    def latest_available(self) -> int
    @property
    def total_frames(self) -> int
```

- `frame_exists`: open HDF5 read-only, check `f'data/{idx}'` in group keys
- `load_frame`: read psi -> abs -> griddata cubic interpolation -> clip to [0, inf)
- `latest_available`: scan HDF5 data group, return count of available frames
- `total_frames`: fixed value set at construction (or auto-detected from HDF5)

### FrameCache

Sliding window cache with background prefetch. Unchanged from current implementation.

```python
class FrameCache:
    def __init__(self, source: FrameSource,
                 prefetch_ahead: int = 5, keep_behind: int = 2)

    def get(self, idx: int) -> np.ndarray | None
    def ensure(self, idx: int) -> np.ndarray
    def prefetch(self, from_idx: int, count: int) -> None
    def evict(self, before_idx: int) -> None

    @property
    def cached_indices(self) -> list[int]
```

- `_cache: dict[int, ndarray]` — frame index to interpolated 2D array
- `_lock: threading.Lock` — protects `_cache`
- `get`: return cached frame or None (non-blocking)
- `ensure`: blocking load if not cached, triggers prefetch ahead and evict behind
- `prefetch`: spawns daemon thread to load `count` frames starting from `from_idx`, skips already-cached and non-existent frames
- `evict`: removes all cached frames with index < `before_idx`

Cache window at steady state: `[current - keep_behind, current + prefetch_ahead]` = ~7 frames x 40 KB = 280 KB.

### FramePlayer

Playback controller with ipycanvas rendering. **This is the redesigned component.**

```python
class FramePlayer:
    def __init__(self, source: FrameSource, cache: FrameCache,
                 interval_ms: int = 100)

    def show(self) -> None
    def play(self) -> None
    def pause(self) -> None
    def jump(self, idx: int) -> None
```

State:
- `_playing: bool`
- `_current: int` — current frame index
- `_latest: int` — latest available frame index (updated by poller)
- `_timer: threading.Timer | None` — tick scheduler
- `_poller: threading.Timer | None` — new-frame poller
- `_vmin: float` — colormap minimum (default 0.0)
- `_vmax: float` — colormap maximum (default 1.05)

#### Rendering pipeline

```python
import matplotlib as mpl

# Init: build colormap LUT once
self._cmap = mpl.colormaps['inferno']

# Per-frame render:
def _render(self):
    Z = self.cache.ensure(self._current)
    norm = np.clip((Z - self._vmin) / (self._vmax - self._vmin), 0, 1)
    rgba = (self._cmap(norm) * 255).astype(np.uint8)
    self.canvas.put_image_data(rgba, 0, 0)
    self.title.value = f'|psi|  frame {self._current} / {self.source.total_frames}'
    self.slider.value = self._current
    self.label.value = f'{self._current} / {self.source.total_frames}'
```

No matplotlib figures. No `draw()`. No `draw_idle()`. No `IOLoop.add_callback()`. Single `put_image_data()` call per frame.

#### Canvas sizing

Data grid is `(ny, nx)` (e.g. 50x100). Canvas pixel size must match to avoid scaling artifacts. Use `Canvas(width=nx, height=ny)` and let the `layout` dict scale it via CSS:

```python
self.canvas = Canvas(width=NX, height=NY,
                     layout=Layout(width='100%', max_width='700px'))
```

This renders at native data resolution and CSS-scales the display to ~700px wide with correct aspect ratio. No upsampling needed.

#### Widget setup

```python
from ipycanvas import Canvas

self.canvas = Canvas(width=NX, height=NY,
                     layout=Layout(width='100%', max_width='700px'))
self.btn = ToggleButton(description='▶', layout=Layout(width='50px'))
self.slider = IntSlider(min=0, max=total-1, continuous_update=False,
                         layout=Layout(flex='1', min_width='300px'))
self.label = Label(value='0 / N', layout=Layout(width='100px'))
self.ui = VBox([HBox([btn, slider, label]), canvas])
```

No `new_figure_manager_given_figure`. No matplotlib Figure. Pure ipycanvas + ipywidgets.

#### Tick loop

```python
def _schedule_tick(self, delay=None):
    if self._timer: self._timer.cancel()
    d = delay if delay is not None else self._interval
    self._timer = threading.Timer(d, self._tick)
    self._timer.daemon = True
    self._timer.start()

def _tick(self):
    if not self._playing: return
    next_idx = self._current + 1
    if next_idx >= self.source.total_frames:
        self.pause(); return
    if not self.source.frame_exists(next_idx):
        self._schedule_tick(delay=0.2); return
    self._current = next_idx
    self._render()
    self._schedule_tick()
```

No IOLoop — ipycanvas `put_image_data` is thread-safe (serialized to frontend via widget comm).

#### Jump behavior

```
jump(idx):
  if source.frame_exists(idx):
    _current = idx, render immediately
  else:
    _current = min(_latest - 1, idx)
    render latest available
  if playing: _schedule_tick()
```

#### New frame polling

```python
def _poll(self):
    self._latest = self.source.latest_available
    self.slider.max = max(self._latest - 1, 0)
    if self._latest < self.source.total_frames:
        self._poller = threading.Timer(2.0, self._poll)
        self._poller.daemon = True
        self._poller.start()
```

## UI Layout

```
+---------------------------------------------+
| [Play/Pause]  [======slider======]  120/3376 |
+---------------------------------------------+
|                                             |
|         ipycanvas heatmap (700x350)         |
|         inferno cmap, text range label      |
|                                             |
+---------------------------------------------+
```

Widgets:
- `ToggleButton`: play/pause (unicode arrow/bar icons)
- `IntSlider`: progress bar, `min=0`, `max=total_frames-1`, `continuous_update=False`
- `Label`: frame counter `"120 / 3376"`
- `Canvas`: ipycanvas, native data resolution (NX x NY), CSS-scaled to max 700px wide
- Layout: `VBox([HBox([btn, slider, label]), canvas])`

## Notebook Structure

```
Cell 1: Imports + configuration
Cell 2: FrameSource class
Cell 3: FrameCache class
Cell 4: FramePlayer class
Cell 5: Instantiate and display
```

Drop-in replacement for current `notebooks/frame_player.ipynb`.

## Dependencies

- `ipycanvas` (new — `pip install ipycanvas`)
- `ipywidgets` (existing)
- `matplotlib.cm` (existing, colormap LUT only — no figures)
- `h5py`, `scipy`, `numpy` (existing)
- `ipympl` no longer needed

## Testing

Test notebook reads from existing `sim_output.h5` (3376 frames). All frames are pre-generated, so `frame_exists` always returns True for valid indices. Async behavior can be simulated by setting `total_frames` higher than actual available frames.

## Scope

- Single HDF5 file, in-process reading only
- No remote/K8s simulation support in this iteration
- No audio/metadata tracks
- No frame export or save functionality
- Text range label only (no gradient colorbar)
