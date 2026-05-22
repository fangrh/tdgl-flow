# Heatmap Animation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blocking matplotlib HTML5-video heatmap animation with an interactive Plotly + ipywidgets viewer that reads HDF5 on-demand.

**Architecture:** Single Plotly `FigureWidget` with one `Heatmap` trace, controlled by `ipywidgets.Play` + `IntSlider`. Each frame is computed on-demand via `griddata` interpolation from HDF5. A background poller updates the slider max as new frames become available during live simulations.

**Tech Stack:** plotly, ipywidgets, h5py, scipy.interpolate.griddata, numpy

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `notebooks/local_tdgl_sim.ipynb` | Modify cells 21–24 | Replace matplotlib animation with interactive viewer |

Only one file is modified. The notebook is duplicated (cells 0–29 and 30–59 appear to be two copies of the same workflow). This plan targets cells 21–24 (first copy) and 51–54 (second copy) with identical changes.

---

### Task 1: Replace Cell 21 (markdown header)

**Files:**
- Modify: `notebooks/local_tdgl_sim.ipynb` — cells 21 and 51

- [ ] **Step 1: Update cell 21 markdown**

Replace the markdown text with updated description for the new interactive viewer.

New content for cell 21:
```markdown
## Heatmap Animation

Interactive Plotly viewer with play/pause/seek controls.
One frame per timing step, computed on-demand from HDF5.
Works with completed simulation or live background solver (cell 22).
```

- [ ] **Step 2: Apply same change to cell 51**

Identical markdown content for the second copy of the notebook.

---

### Task 2: Replace Cell 22 (background solver — keep unchanged)

**Files:**
- Modify: `notebooks/local_tdgl_sim.ipynb` — cells 22 and 52

No changes needed. Cell 22 already launches the solver in a background thread with `sim_done = threading.Event()`. The new viewer will use this same `sim_done` event for polling.

- [ ] **Step 1: Verify cell 22 and cell 52 are identical to current**

No code changes. Confirm cells are untouched.

---

### Task 3: Replace Cell 23 (main interactive viewer)

**Files:**
- Modify: `notebooks/local_tdgl_sim.ipynb` — cells 23 and 53

This is the core task. Replace the blocking matplotlib animation with the interactive Plotly + ipywidgets viewer.

- [ ] **Step 1: Write the new cell 23 code**

New content for cell 23:

```python
import ipywidgets as widgets
import h5py
from scipy.interpolate import griddata

# Check sim_done event (set by cell 22 background solver)
try:
    _ = sim_done
except NameError:
    sim_done = threading.Event()
    sim_done.set()

# Interpolation grid
xmin, xmax = points[:, 0].min(), points[:, 0].max()
ymin, ymax = points[:, 1].min(), points[:, 1].max()
nx, ny = 100, 50
gx = np.linspace(xmin, xmax, nx)
gy = np.linspace(ymin, ymax, ny)
GX, GY = np.meshgrid(gx, gy)
grid_pts = np.column_stack([GX.ravel(), GY.ravel()])

# Read frame times from HDF5
def read_frame_times():
    if not os.path.exists(output_path):
        return np.array([])
    try:
        with h5py.File(output_path, 'r') as f:
            if 'data' not in f:
                return np.array([])
            total = len(f['data'].keys())
            if total == 0:
                return np.array([])
            return np.array([
                float(f[f'data/{fi}'].attrs.get('time', 0))
                for fi in range(total)
            ])
    except Exception:
        return np.array([])

# Compute heatmap for a given step index
def compute_frame(step_idx):
    """Compute interpolated |psi| heatmap for timing step step_idx (0-based)."""
    s = steps[step_idx]
    frame_times = read_frame_times()
    if len(frame_times) == 0:
        return np.zeros((ny, nx)), {'je': s['je_end'], 'step': step_idx + 1, 'time': 0}

    mid_t = (s['save_start'] + s['save_end']) / 2
    mask = (frame_times >= s['save_start']) & (frame_times <= s['save_end'])
    step_fi = np.where(mask)[0]

    if len(step_fi) > 0:
        fi = step_fi[len(step_fi) // 2]
    else:
        fi = int(np.argmin(np.abs(frame_times - mid_t)))

    with h5py.File(output_path, 'r') as f:
        psi = np.abs(np.array(f[f'data/{fi}/psi']))

    Z = griddata(points, psi, grid_pts, method='cubic',
                 fill_value=0.0).reshape(ny, nx)
    Z = np.clip(Z, 0, None)
    info = {'je': s['je_end'], 'step': step_idx + 1,
            'time': (s['save_start'] + s['save_end']) / 2}
    return Z, info

n_steps = len(steps)

# Initial frame
initial_Z, initial_info = compute_frame(0)

# Electrode rectangles as shapes
electrode_shapes = []
for t in device.terminal_info():
    idx = t.site_indices
    x0, x1 = points[idx, 0].min(), points[idx, 0].max()
    y0, y1 = points[idx, 1].min(), points[idx, 1].max()
    electrode_shapes.append(dict(
        type='rect', x0=x0, x1=x1, y0=y0, y1=y1,
        line=dict(color='cyan', width=1.5, dash='dash'),
        fillcolor='rgba(0,0,0,0)', layer='above',
    ))

# Build Plotly FigureWidget
fig = go.FigureWidget(
    go.Heatmap(x=gx, y=gy, z=initial_Z,
               colorscale='Inferno', zmin=0, zmax=1.05,
               colorbar=dict(title='|ψ|'),
               showscale=True),
)
fig.update_layout(
    title=f'|ψ|  je={initial_info["je"]:.2f}  t={initial_info["time"]:.0f}s  '
          f'(step 1/{n_steps})',
    xaxis=dict(showline=True, linewidth=1, linecolor='black',
               mirror=True, ticks='outside'),
    yaxis=dict(scaleanchor='x', scaleratio=1, showline=True,
               linewidth=1, linecolor='black', mirror=True, ticks='outside'),
    margin=dict(l=50, r=10, t=35, b=50),
    height=400, width=700, plot_bgcolor='white',
    shapes=electrode_shapes,
)

# Play + Slider controls
slider = widgets.IntSlider(
    value=0, min=0, max=n_steps - 1, step=1,
    description='Step:', continuous_update=True,
)
play = widgets.Play(
    value=0, min=0, max=n_steps - 1, interval=80,
    description='Play',
)
widgets.jslink((play, 'value'), (slider, 'value'))

# Frame update callback
def on_slider_change(change):
    idx = change['new']
    Z, info = compute_frame(idx)
    fig.data[0].z = Z
    fig.update_layout(
        title=f'|ψ|  je={info["je"]:.2f}  t={info["time"]:.0f}s  '
              f'(step {info["step"]}/{n_steps})')

slider.observe(on_slider_change, names='value')

# Poller: update slider max during live simulation
def poll_progress():
    if sim_done.is_set():
        return
    frame_times = read_frame_times()
    if len(frame_times) > 0:
        completed = 0
        for s in steps:
            if np.any((frame_times >= s['save_start']) & (frame_times <= s['save_end'])):
                completed += 1
            else:
                break
        if completed > 0:
            slider.max = completed - 1
            play.max = completed - 1
    threading.Timer(2.0, poll_progress).start()

if not sim_done.is_set():
    poll_progress()

# Display
controls = widgets.HBox([play, slider])
display(widgets.VBox([controls, fig]))
print(f'{n_steps} timing steps — use Play/Slider to browse frames')
```

- [ ] **Step 2: Apply same code to cell 53 (second copy)**

Identical content for the second copy of the notebook.

- [ ] **Step 3: Manually verify in Jupyter**

Run the notebook through cells 0–23 and confirm:
1. `ipywidgets.Play` and `IntSlider` appear
2. Heatmap renders with initial frame
3. Slider changes update the heatmap
4. Play button animates through frames

---

### Task 4: Replace Cell 24 (single frame viewer — simplify)

**Files:**
- Modify: `notebooks/local_tdgl_sim.ipynb` — cells 24 and 54

Simplify the single-frame viewer to use the shared `compute_frame` function and `gx`/`gy` grid from cell 23.

- [ ] **Step 1: Write simplified cell 24 code**

New content for cell 24:

```python
# View a single frame by timing step index (1-based)
step_idx = 100  # change this (1..211)

if step_idx < 1 or step_idx > len(steps):
    print(f'Step must be 1..{len(steps)}')
else:
    Z, info = compute_frame(step_idx - 1)
    fig_single = go.Figure(go.Heatmap(
        x=gx, y=gy, z=Z, colorscale='Inferno',
        zmin=0, zmax=1.05, showscale=True,
        colorbar=dict(title='|ψ|'),
    ))
    fig_single.update_layout(
        title=f'|ψ|  je={info["je"]:.2f}  t={info["time"]:.0f}s  '
              f'(step {info["step"]}/{len(steps)})',
        xaxis=dict(showline=True, linewidth=1, linecolor='black',
                   mirror=True, ticks='outside'),
        yaxis=dict(scaleanchor='x', scaleratio=1, showline=True,
                   linewidth=1, linecolor='black', mirror=True, ticks='outside'),
        margin=dict(l=50, r=10, t=35, b=50),
        height=400, width=700, plot_bgcolor='white',
        shapes=electrode_shapes,
    )
    fig_single.show()
```

- [ ] **Step 2: Apply same code to cell 54 (second copy)**

Identical content for the second copy.

---

### Task 5: Commit

- [ ] **Step 1: Stage and commit**

```bash
git add notebooks/local_tdgl_sim.ipynb docs/superpowers/specs/2026-05-22-heatmap-animation-design.md
git commit -m "feat: replace matplotlib heatmap animation with interactive Plotly + ipywidgets viewer"
```
