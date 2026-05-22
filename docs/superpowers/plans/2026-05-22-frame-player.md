# Frame Player Implementation Plan (ipycanvas)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ipympl-based frame player with an ipycanvas-based player that renders reliably in JupyterLab.

**Architecture:** Three-class pipeline: FrameSource reads individual frames from HDF5 on demand, FrameCache maintains a sliding window of ~7 pre-interpolated frames, FramePlayer drives playback via Timer ticks and renders via ipycanvas `put_image_data()`. All classes live in a single notebook.

**Tech Stack:** ipycanvas, ipywidgets, matplotlib.cm (colormap LUT only), h5py, scipy.interpolate.griddata, numpy, threading

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `notebooks/frame_player.ipynb` | Rewrite | FrameSource + FrameCache + FramePlayer with ipycanvas |

---

### Task 1: Install ipycanvas dependency

**Files:** None (environment only)

- [ ] **Step 1: Install ipycanvas**

```bash
pip install ipycanvas
```

- [ ] **Step 2: Verify installation**

```bash
python3 -c "import ipycanvas; print(ipycanvas.__version__)"
```

Expected: version string printed (e.g., `0.13.1`)

- [ ] **Step 3: Verify JupyterLab extension**

```bash
jupyter labextension list 2>&1 | grep ipycanvas
```

Expected: `ipycanvas` listed as `enabled OK`. ipycanvas bundles its own labextension so no separate install needed.

---

### Task 2: Create Cell 1 (imports + config) and Cell 2 (FrameSource)

**Files:**
- Rewrite: `notebooks/frame_player.ipynb`

- [ ] **Step 1: Create the notebook with imports and FrameSource**

Run this Python script from project root:

```python
import json

cells = []

# Cell 1: Imports + configuration
cells.append({
    "cell_type": "code",
    "execution_count": None,
    "id": "cell-imports",
    "metadata": {},
    "outputs": [],
    "source": [
        "import os, threading\n",
        "import numpy as np\n",
        "import h5py\n",
        "from scipy.interpolate import griddata\n",
        "import matplotlib as mpl\n",
        "from ipycanvas import Canvas\n",
        "import ipywidgets as widgets\n",
        "\n",
        "# ── Configuration ──\n",
        "H5_PATH = '/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/notebooks/sim_output.h5'\n",
        "NX, NY = 100, 50  # interpolation grid size\n",
        "PREFETCH_AHEAD = 5\n",
        "KEEP_BEHIND = 2\n",
        "PLAYBACK_INTERVAL_MS = 100\n",
        "\n",
        "print(f'HDF5: {H5_PATH}')\n",
        "print(f'Exists: {os.path.exists(H5_PATH)}')"
    ]
})

# Cell 2: FrameSource class
cells.append({
    "cell_type": "code",
    "execution_count": None,
    "id": "cell-frame-source",
    "metadata": {},
    "outputs": [],
    "source": [
        "class FrameSource:\n",
        "    \"\"\"Reads individual frames from an HDF5 file.\"\"\"\n",
        "\n",
        "    def __init__(self, h5_path: str, points: np.ndarray,\n",
        "                 grid_pts: np.ndarray, nx: int, ny: int,\n",
        "                 total_frames: int = None):\n",
        "        self.h5_path = h5_path\n",
        "        self.points = points\n",
        "        self.grid_pts = grid_pts\n",
        "        self.nx = nx\n",
        "        self.ny = ny\n",
        "        if total_frames is None:\n",
        "            with h5py.File(h5_path, 'r') as f:\n",
        "                self._total_frames = len(f['data'].keys())\n",
        "        else:\n",
        "            self._total_frames = total_frames\n",
        "\n",
        "    def frame_exists(self, idx: int) -> bool:\n",
        "        try:\n",
        "            with h5py.File(self.h5_path, 'r') as f:\n",
        "                return str(idx) in f['data']\n",
        "        except Exception:\n",
        "            return False\n",
        "\n",
        "    def load_frame(self, idx: int) -> np.ndarray:\n",
        "        \"\"\"Load frame idx, return interpolated 2D array (ny, nx).\"\"\"\n",
        "        with h5py.File(self.h5_path, 'r') as f:\n",
        "            psi = np.abs(np.array(f[f'data/{idx}/psi']))\n",
        "        Z = griddata(self.points, psi, self.grid_pts, method='cubic',\n",
        "                     fill_value=0.0).reshape(self.ny, self.nx)\n",
        "        return np.clip(Z, 0, None)\n",
        "\n",
        "    @property\n",
        "    def latest_available(self) -> int:\n",
        "        try:\n",
        "            with h5py.File(self.h5_path, 'r') as f:\n",
        "                return len(f['data'].keys())\n",
        "        except Exception:\n",
        "            return 0\n",
        "\n",
        "    @property\n",
        "    def total_frames(self) -> int:\n",
        "        return self._total_frames\n",
        "\n",
        "    def build_interpolation_grid(self):\n",
        "        \"\"\"Return (gx, gy) arrays for heatmap axes.\"\"\"\n",
        "        xmin, xmax = self.points[:, 0].min(), self.points[:, 0].max()\n",
        "        ymin, ymax = self.points[:, 1].min(), self.points[:, 1].max()\n",
        "        return (np.linspace(xmin, xmax, self.nx),\n",
        "                np.linspace(ymin, ymax, self.ny))\n",
        "\n",
        "print('FrameSource defined')"
    ]
})

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.13.0"}
    },
    "nbformat": 4,
    "nbformat_minor": 5
}
with open('notebooks/frame_player.ipynb', 'w') as f:
    json.dump(nb, f, indent=1)
print('notebooks/frame_player.ipynb created with 2 cells')
```

- [ ] **Step 2: Verify notebook creation**

```bash
python3 -c "import json; nb=json.load(open('notebooks/frame_player.ipynb')); print(f'{len(nb[\"cells\"])} cells')"
```

Expected: `2 cells`

---

### Task 3: Add Cell 3 (FrameCache) and Cell 4 (FramePlayer with ipycanvas)

**Files:**
- Modify: `notebooks/frame_player.ipynb` — append 2 cells

- [ ] **Step 1: Append FrameCache and FramePlayer cells**

Run this Python script from project root:

```python
import json

with open('notebooks/frame_player.ipynb') as f:
    nb = json.load(f)

# Cell 3: FrameCache (unchanged from current)
nb["cells"].append({
    "cell_type": "code",
    "execution_count": None,
    "id": "cell-frame-cache",
    "metadata": {},
    "outputs": [],
    "source": [
        "class FrameCache:\n",
        "    \"\"\"Sliding window cache with background prefetch.\"\"\"\n",
        "\n",
        "    def __init__(self, source: FrameSource,\n",
        "                 prefetch_ahead: int = 5, keep_behind: int = 2):\n",
        "        self.source = source\n",
        "        self.prefetch_ahead = prefetch_ahead\n",
        "        self.keep_behind = keep_behind\n",
        "        self._cache: dict[int, np.ndarray] = {}\n",
        "        self._lock = threading.Lock()\n",
        "\n",
        "    def get(self, idx: int) -> np.ndarray | None:\n",
        "        with self._lock:\n",
        "            return self._cache.get(idx)\n",
        "\n",
        "    def ensure(self, idx: int) -> np.ndarray:\n",
        "        \"\"\"Blocking: load frame if not cached, prefetch ahead, evict behind.\"\"\"\n",
        "        with self._lock:\n",
        "            if idx in self._cache:\n",
        "                frame = self._cache[idx]\n",
        "            else:\n",
        "                frame = None\n",
        "\n",
        "        if frame is None:\n",
        "            frame = self.source.load_frame(idx)\n",
        "            with self._lock:\n",
        "                self._cache[idx] = frame\n",
        "\n",
        "        self.evict(idx - self.keep_behind)\n",
        "        self.prefetch(idx + 1, self.prefetch_ahead)\n",
        "        return frame\n",
        "\n",
        "    def prefetch(self, from_idx: int, count: int) -> None:\n",
        "        \"\"\"Background thread: load count frames starting from from_idx.\"\"\"\n",
        "        def _worker():\n",
        "            for i in range(from_idx, from_idx + count):\n",
        "                if i >= self.source.total_frames:\n",
        "                    break\n",
        "                with self._lock:\n",
        "                    if i in self._cache:\n",
        "                        continue\n",
        "                if not self.source.frame_exists(i):\n",
        "                    continue\n",
        "                frame = self.source.load_frame(i)\n",
        "                with self._lock:\n",
        "                    self._cache[i] = frame\n",
        "\n",
        "        t = threading.Thread(target=_worker, daemon=True)\n",
        "        t.start()\n",
        "\n",
        "    def evict(self, before_idx: int) -> None:\n",
        "        \"\"\"Remove frames with index < before_idx.\"\"\"\n",
        "        with self._lock:\n",
        "            to_remove = [k for k in self._cache if k < before_idx]\n",
        "            for k in to_remove:\n",
        "                del self._cache[k]\n",
        "\n",
        "    @property\n",
        "    def cached_indices(self) -> list[int]:\n",
        "        with self._lock:\n",
        "            return sorted(self._cache.keys())\n",
        "\n",
        "print('FrameCache defined')"
    ]
})

# Cell 4: FramePlayer (ipycanvas-based — the redesigned component)
nb["cells"].append({
    "cell_type": "code",
    "execution_count": None,
    "id": "cell-frame-player",
    "metadata": {},
    "outputs": [],
    "source": [
        "class FramePlayer:\n",
        "    \"\"\"Playback controller with ipycanvas rendering and ipywidgets controls.\"\"\"\n",
        "\n",
        "    def __init__(self, source: FrameSource, cache: FrameCache,\n",
        "                 interval_ms: int = 100):\n",
        "        self.source = source\n",
        "        self.cache = cache\n",
        "        self._interval = interval_ms / 1000.0\n",
        "        self._playing = False\n",
        "        self._current = 0\n",
        "        self._latest = source.latest_available\n",
        "        self._timer = None\n",
        "        self._poller = None\n",
        "        self._vmin = 0.0\n",
        "        self._vmax = 1.05\n",
        "        self._cmap = mpl.colormaps['inferno']\n",
        "\n",
        "        # Load initial frame\n",
        "        Z0 = cache.ensure(0)\n",
        "\n",
        "        # ipycanvas — native data resolution, CSS-scaled for display\n",
        "        self.canvas = Canvas(\n",
        "            width=source.nx, height=source.ny,\n",
        "            layout=widgets.Layout(width='100%', max_width='700px'))\n",
        "        self._draw_frame(Z0)\n",
        "\n",
        "        # Widgets\n",
        "        self.btn = widgets.ToggleButton(\n",
        "            value=False, description='\\u25b6',\n",
        "            tooltip='Play / Pause', button_style='',\n",
        "            layout=widgets.Layout(width='50px'),\n",
        "        )\n",
        "        self.slider = widgets.IntSlider(\n",
        "            value=0, min=0, max=max(self._latest - 1, 0),\n",
        "            step=1, description='',\n",
        "            continuous_update=False,\n",
        "            layout=widgets.Layout(flex='1', min_width='300px'),\n",
        "        )\n",
        "        self.label = widgets.Label(\n",
        "            value=f'0 / {source.total_frames}',\n",
        "            layout=widgets.Layout(width='100px'),\n",
        "        )\n",
        "\n",
        "        # Wire callbacks\n",
        "        self.btn.observe(self._on_btn, names='value')\n",
        "        self.slider.observe(self._on_slider, names='value')\n",
        "\n",
        "        # Layout\n",
        "        self.controls = widgets.HBox([self.btn, self.slider, self.label])\n",
        "        self.ui = widgets.VBox([self.controls, self.canvas])\n",
        "\n",
        "        # Start polling for new frames\n",
        "        self._schedule_poll()\n",
        "\n",
        "    # ── Public API ──\n",
        "\n",
        "    def show(self):\n",
        "        \"\"\"Display the player UI.\"\"\"\n",
        "        display(self.ui)\n",
        "\n",
        "    def play(self):\n",
        "        self._playing = True\n",
        "        self.btn.value = True\n",
        "        self.btn.description = '\\u23f8'\n",
        "        self._schedule_tick()\n",
        "\n",
        "    def pause(self):\n",
        "        self._playing = False\n",
        "        self.btn.value = False\n",
        "        self.btn.description = '\\u25b6'\n",
        "        if self._timer:\n",
        "            self._timer.cancel()\n",
        "            self._timer = None\n",
        "\n",
        "    def jump(self, idx: int):\n",
        "        idx = max(0, min(idx, self.source.total_frames - 1))\n",
        "        if self.source.frame_exists(idx):\n",
        "            self._current = idx\n",
        "        else:\n",
        "            self._current = min(self._latest - 1, idx)\n",
        "        self._render()\n",
        "        if self._playing:\n",
        "            self._schedule_tick()\n",
        "\n",
        "    # ── Internals ──\n",
        "\n",
        "    def _on_btn(self, change):\n",
        "        if change['new']:\n",
        "            self.play()\n",
        "        else:\n",
        "            self.pause()\n",
        "\n",
        "    def _on_slider(self, change):\n",
        "        self.jump(change['new'])\n",
        "\n",
        "    def _schedule_tick(self, delay=None):\n",
        "        if self._timer:\n",
        "            self._timer.cancel()\n",
        "        d = delay if delay is not None else self._interval\n",
        "        self._timer = threading.Timer(d, self._tick)\n",
        "        self._timer.daemon = True\n",
        "        self._timer.start()\n",
        "\n",
        "    def _tick(self):\n",
        "        if not self._playing:\n",
        "            return\n",
        "        next_idx = self._current + 1\n",
        "        if next_idx >= self.source.total_frames:\n",
        "            self.pause()\n",
        "            return\n",
        "        if not self.source.frame_exists(next_idx):\n",
        "            self._schedule_tick(delay=0.2)\n",
        "            return\n",
        "        self._current = next_idx\n",
        "        self._render()\n",
        "        self._schedule_tick()\n",
        "\n",
        "    def _draw_frame(self, Z):\n",
        "        \"\"\"Apply colormap and push RGBA data to ipycanvas.\"\"\"\n",
        "        norm = np.clip((Z - self._vmin) / (self._vmax - self._vmin), 0, 1)\n",
        "        rgba = (self._cmap(norm) * 255).astype(np.uint8)\n",
        "        self.canvas.put_image_data(rgba, 0, 0)\n",
        "\n",
        "    def _render(self):\n",
        "        Z = self.cache.ensure(self._current)\n",
        "        self._draw_frame(Z)\n",
        "        self.slider.value = self._current\n",
        "        self.label.value = f'{self._current} / {self.source.total_frames}'\n",
        "\n",
        "    def _schedule_poll(self):\n",
        "        self._poller = threading.Timer(2.0, self._poll_new_frames)\n",
        "        self._poller.daemon = True\n",
        "        self._poller.start()\n",
        "\n",
        "    def _poll_new_frames(self):\n",
        "        self._latest = self.source.latest_available\n",
        "        if self._latest > 0:\n",
        "            self.slider.max = self._latest - 1\n",
        "        if self._latest < self.source.total_frames:\n",
        "            self._schedule_poll()\n",
        "\n",
        "print('FramePlayer defined')"
    ]
})

with open('notebooks/frame_player.ipynb', 'w') as f:
    json.dump(nb, f, indent=1)
print(f'notebooks/frame_player.ipynb now has {len(nb["cells"])} cells')
```

- [ ] **Step 2: Verify cell count**

```bash
python3 -c "import json; nb=json.load(open('notebooks/frame_player.ipynb')); print(f'{len(nb[\"cells\"])} cells')"
```

Expected: `4 cells`

---

### Task 4: Add Cell 5 (instantiate and display player)

**Files:**
- Modify: `notebooks/frame_player.ipynb` — append Cell 5

- [ ] **Step 1: Append instantiation cell**

Run this Python script from project root:

```python
import json

with open('notebooks/frame_player.ipynb') as f:
    nb = json.load(f)

nb["cells"].append({
    "cell_type": "code",
    "execution_count": None,
    "id": "cell-instantiate",
    "metadata": {},
    "outputs": [],
    "source": [
        "# ── Instantiate and display ──\n",
        "\n",
        "# Extract mesh points from HDF5\n",
        "with h5py.File(H5_PATH, 'r') as f:\n",
        "    _points = np.array(f['solution/device/mesh/sites'])\n",
        "\n",
        "# Build interpolation grid points\n",
        "_xmin, _xmax = _points[:, 0].min(), _points[:, 0].max()\n",
        "_ymin, _ymax = _points[:, 1].min(), _points[:, 1].max()\n",
        "_gx = np.linspace(_xmin, _xmax, NX)\n",
        "_gy = np.linspace(_ymin, _ymax, NY)\n",
        "_GX, _GY = np.meshgrid(_gx, _gy)\n",
        "_grid_pts = np.column_stack([_GX.ravel(), _GY.ravel()])\n",
        "\n",
        "# Build components\n",
        "source = FrameSource(H5_PATH, _points, _grid_pts, NX, NY)\n",
        "cache = FrameCache(source, prefetch_ahead=PREFETCH_AHEAD,\n",
        "                   keep_behind=KEEP_BEHIND)\n",
        "player = FramePlayer(source, cache, interval_ms=PLAYBACK_INTERVAL_MS)\n",
        "\n",
        "print(f'Source: {source.total_frames} frames, {source.latest_available} available')\n",
        "print(f'Grid: {_points.shape[0]} mesh points -> {NX}x{NY} interpolation')\n",
        "player.show()"
    ]
})

with open('notebooks/frame_player.ipynb', 'w') as f:
    json.dump(nb, f, indent=1)
print(f'notebooks/frame_player.ipynb now has {len(nb["cells"])} cells')
```

- [ ] **Step 2: Verify cell count**

```bash
python3 -c "import json; nb=json.load(open('notebooks/frame_player.ipynb')); print(f'{len(nb[\"cells\"])} cells')"
```

Expected: `5 cells`

---

### Task 5: Execute and verify

**Files:** None (verification only)

- [ ] **Step 1: Execute the notebook with nbconvert**

```bash
jupyter nbconvert --to notebook --execute notebooks/frame_player.ipynb \
  --output /tmp/frame_player_out.ipynb --ExecutePreprocessor.timeout=60
```

Expected: completes without error.

- [ ] **Step 2: Check outputs**

```bash
python3 -c "
import json
with open('/tmp/frame_player_out.ipynb') as f:
    nb = json.load(f)
for i, cell in enumerate(nb['cells']):
    for out in cell.get('outputs', []):
        if out.get('output_type') == 'stream':
            text = ''.join(out.get('text', []))
            print(f'Cell {i}: {text[:200]}')
        elif out.get('output_type') == 'error':
            print(f'Cell {i} ERROR: {out.get(\"ename\")}: {out.get(\"evalue\")}')
        elif 'application/vnd.jupyter.widget-view+json' in out.get('data', {}):
            print(f'Cell {i}: [widget rendered]')
        elif 'image/png' in out.get('data', {}):
            print(f'Cell {i}: [image rendered]')
"
```

Expected:
- Cell 0: `HDF5: /mnt/... Exists: True`
- Cell 1: `FrameSource defined`
- Cell 2: `FrameCache defined`
- Cell 3: `FramePlayer defined`
- Cell 4: `Source: 3376 frames, 3376 available` + `[widget rendered]`

If Cell 4 shows a widget without errors, the ipycanvas rendering works.

---

### Task 6: Commit

- [ ] **Step 1: Stage and commit**

```bash
git add notebooks/frame_player.ipynb
git commit -m "feat: rewrite frame player with ipycanvas rendering

Replace ipympl with ipycanvas for reliable JupyterLab rendering.
Key changes: put_image_data for heatmap display, matplotlib.cm for
colormap LUT only (no figures), threading.Timer tick loop without
IOLoop, native data resolution canvas with CSS scaling."
```
