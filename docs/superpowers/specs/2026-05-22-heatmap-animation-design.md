# Heatmap Animation: Plotly + ipywidgets Interactive Viewer

Replace the current matplotlib HTML5-video animation with an interactive Plotly + ipywidgets viewer that reads HDF5 directly and supports play/pause/seek.

## Problem

Current Cell 23 in `local_tdgl_sim.ipynb`:
- Blocks until simulation finishes (`sim_done.wait()`)
- Pre-computes all frames upfront (~10s for 211 steps)
- Outputs a static HTML5 video with no seek/scrub control
- Re-running requires full recomputation

## Solution

Replace with a single-trace Plotly `FigureWidget` controlled by `ipywidgets.Play` + `IntSlider`. Each frame is computed on-demand from HDF5 via `griddata` interpolation.

## Architecture

```
Cell A: Solver config + background thread (unchanged)
Cell B: Interactive viewer (new)
Cell C: Single-frame viewer (simplified existing)
```

### Cell B — Interactive Viewer

Components:
- `ipywidgets.Play`: play/pause, speed control via `interval` attribute
- `ipywidgets.IntSlider`: manual seek to any step
- `plotly.graph_objects.FigureWidget` with single `Heatmap` trace

Data flow on slider change:
1. Get current step index from slider value
2. Map step → HDF5 frame via timing schedule (`steps[step_idx]`)
3. Read `psi` from HDF5, compute `|psi|`
4. `griddata` interpolate to regular grid (100x50)
5. Update `fig_widget.data[0].z = new_Z`, `fig_widget.update_layout(title=...)`

Frame mapping logic (same as current Cell 23):
- Each timing step has `save_start` / `save_end`
- Find HDF5 frames where `frame_time ∈ [save_start, save_end]`
- Pick the middle frame (`step_fi[len(step_fi) // 2]`)
- If no frame in range, reuse last valid frame

Dynamic range during simulation:
- `slider.max` starts at number of completed steps
- A polling mechanism (`threading.Timer` or `ipywidgets.Timer`) checks HDF5 frame count periodically
- When simulation completes, fix `slider.max` to total step count

### Cell C — Single Frame Viewer

Simplified version of current Cell 24:
- Input: `step_idx` integer
- Read HDF5 → griddata → Plotly Heatmap (static `go.Figure`, not `FigureWidget`)
- No animation controls

## Implementation Details

### FigureWidget setup

```python
fig = go.FigureWidget(
    go.Heatmap(x=gx, y=gy, z=initial_Z,
               colorscale='Inferno', zmin=0, zmax=1.05,
               colorbar=dict(title='|ψ|')),
)
```

### Play + Slider wiring

```python
slider = widgets.IntSlider(min=0, max=len(steps)-1, step=1, value=0)
play = widgets.Play(min=0, max=len(steps)-1, interval=80)
widgets.jslink((play, 'value'), (slider, 'value'))
```

### On-demand frame update callback

```python
def on_slider_change(change):
    idx = change['new']
    Z = compute_frame(idx)  # HDF5 read + griddata
    fig.data[0].z = Z
    fig.update_layout(title=f'je={info["je"]:.2f}  step {idx+1}/{n}')

slider.observe(on_slider_change, names='value')
```

### Dynamic max during live simulation

```python
def poll_progress():
    with h5py.File(output_path, 'r') as f:
        total = len(f['data'].keys())
    completed = count_completed_steps(total, steps, frame_times)
    slider.max = completed - 1
    play.max = completed - 1
    if not sim_done.is_set():
        threading.Timer(2.0, poll_progress).start()
```

## Dependencies

- `ipywidgets` (Jupyter built-in)
- `plotly` (already used in notebook)
- `h5py`, `scipy`, `numpy` (already used)

## Scope

- In-process background thread simulation only
- K8s/remote simulation support deferred to future iteration
- No separate cache layer — HDF5 is the data source

## Affected Files

- `notebooks/local_tdgl_sim.ipynb` — cells 22-24 replaced
