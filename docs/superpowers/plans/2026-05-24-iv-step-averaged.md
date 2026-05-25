# Step-Averaged I-V Curve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the embedded I-V curve from "frame-by-frame up to playback position" to "Je-step-averaged for all completed steps, with current playback position as a dot marker" — so the I-V curve shows simulation progress independent of playback position.

**Architecture:** `IVCache` gains step-boundary awareness (from `build_timing` steps). A new `step_averaged_iv()` method groups frames into Je steps and averages voltage over each step's `save_time` window. `_draw_iv` uses this for the curve instead of raw frame data. Timing steps flow through `StreamingTDGLPlayer` → `create_player` → `IVCache`.

**Tech Stack:** Existing stack — numpy, h5py, Pillow for rendering. Reuses `build_timing()` from `tdgl_workflow.timing`.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/tdgl_sdk/viewer/_iv.py` | Modify | Add `_timing_steps`, `set_timing_steps()`, `step_averaged_iv()` to IVCache |
| `src/tdgl_sdk/viewer/_render.py` | Modify | Update `_draw_iv` to use step-averaged data for curve, keep raw dot for current position |
| `src/tdgl_sdk/viewer/_player.py` | Modify | Pass timing steps from `StreamingTDGLPlayer` and `create_player` into IVCache |
| `notebooks/e2e_sim_test.py` | Modify | Pass `timing_steps` to `create_player` for static preview; `watch_live` gets it automatically |

---

### Task 1: Add step-averaged I-V computation to IVCache

**Files:**
- Modify: `src/tdgl_sdk/viewer/_iv.py`

- [ ] **Step 1: Add timing step storage and setter to IVCache.__init__**

In `src/tdgl_sdk/viewer/_iv.py`, add `_timing_steps = None` to `__init__` and add a `set_timing_steps` method:

```python
class IVCache:
    def __init__(self, h5_path, mesh, poll_interval=1.0, batch_size=64, **s3_kwds):
        # ... existing fields ...
        self._timing_steps = None  # List of step dicts from build_timing

    def set_timing_steps(self, steps):
        """Set timing step boundaries for Je-step-averaged I-V.

        Args:
            steps: List of step dicts from build_timing(), each with
                   save_start, save_end, je_end keys.
        """
        self._timing_steps = steps
```

- [ ] **Step 2: Add step_averaged_iv() method to IVCache**

Add this method after `set_timing_steps`:

```python
def step_averaged_iv(self, current_frame_idx=None):
    """Return I-V data averaged per completed Je step.

    When timing steps are set, groups all cached frames into Je steps
    by their time attribute and averages V over each step's save_time window.
    Only fully completed steps (last frame time >= save_end) are included.

    When no timing steps are set, falls back to raw frame-by-frame data
    up to current_frame_idx.

    Returns:
        (I_arr, V_arr, n_completed_steps, total_steps)
    """
    if self._timing_steps is None:
        upto = current_frame_idx
        I, V, _ = self.arrays(upto=upto)
        return I, V, len(I), 0

    with self.lock:
        t_all = list(self.t)
        I_all = list(self.I)
        V_all = list(self.V)

    if not t_all:
        return np.array([]), np.array([]), 0, len(self._timing_steps)

    avg_I = []
    avg_V = []
    n_completed = 0

    for step in self._timing_steps:
        save_start = step["save_start"]
        save_end = step["save_end"]

        # Find frames within this step's save window
        indices = [i for i, t in enumerate(t_all) if save_start <= t <= save_end]

        if not indices:
            continue

        # Check if this step is complete: last frame time >= save_end (with tolerance)
        last_t = t_all[indices[-1]]
        if last_t < save_end - 0.1:
            # Step not yet complete — skip for averaged curve
            continue

        # Average V over the save window (skip NaN)
        step_V = [V_all[i] for i in indices]
        step_I = [I_all[i] for i in indices]
        valid = [(i, v) for i, v in zip(step_I, step_V) if not np.isnan(v)]

        if valid:
            avg_I.append(float(np.mean([x[0] for x in valid])))
            avg_V.append(float(np.mean([x[1] for x in valid])))
            n_completed += 1

    return (
        np.array(avg_I),
        np.array(avg_V),
        n_completed,
        len(self._timing_steps),
    )
```

- [ ] **Step 3: Verify syntax by importing**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._iv import IVCache; print('OK')"`
Expected: `OK`

---

### Task 2: Update _draw_iv to use step-averaged data

**Files:**
- Modify: `src/tdgl_sdk/viewer/_render.py`

- [ ] **Step 1: Rewrite _draw_iv to use step-averaged I-V**

Replace the entire `_draw_iv` function in `src/tdgl_sdk/viewer/_render.py` (lines 100-152) with:

```python
def _draw_iv(draw, iv_cache, idx, box):
    iv_cache.ensure(idx)
    # Step-averaged curve uses ALL available data (not limited by playback)
    avg_I, avg_V, n_completed, n_total = iv_cache.step_averaged_iv(
        current_frame_idx=iv_cache.size() - 1,
    )

    # Current frame raw data for the position dot
    cur_I_raw, cur_V_raw, _ = iv_cache.arrays(upto=idx)

    # Compute ranges from step-averaged data + current dot
    all_I = np.concatenate([avg_I, cur_I_raw[~np.isnan(cur_V_raw)]]) if len(avg_I) and len(cur_I_raw) else (avg_I if len(avg_I) else cur_I_raw)
    all_V = np.concatenate([avg_V, cur_V_raw[~np.isnan(cur_V_raw)]]) if len(avg_V) and len(cur_V_raw) else (avg_V if len(avg_V) else cur_V_raw)
    if len(all_I) == 0:
        all_I = np.array([0.0, 1.0])
        all_V = np.array([0.0, 1.0])
    I_min, I_max = float(np.nanmin(all_I)), float(np.nanmax(all_I))
    V_min, V_max = float(np.nanmin(all_V)), float(np.nanmax(all_V))
    if I_min == I_max:
        I_min -= 0.5; I_max += 0.5
    if V_min == V_max:
        V_min -= 0.5; V_max += 0.5
    I_den = I_max - I_min or 1.0
    V_den = V_max - V_min or 1.0

    x0, y0, x1, y1 = box
    left, right, top, bottom = x0 + 54, x1 - 20, y0 + 18, y1 - 34
    draw.rectangle([x0, y0, x1, y1], fill=(30, 30, 30))
    for t in range(5):
        y = top + (1 - t / 4) * (bottom - top)
        draw.line([(left, y), (right, y)], fill=(50, 50, 50))
        draw.text((left - 48, y - 6), f"{V_min + t / 4 * V_den:.2f}", fill=(150, 150, 150))
    for t in range(5):
        x = left + t / 4 * (right - left)
        draw.text((x - 18, bottom + 8), f"{I_min + t / 4 * I_den:.2f}", fill=(150, 150, 150))
    draw.line([(left, top), (left, bottom), (right, bottom)], fill=(105, 105, 105), width=1)

    # Draw step-averaged I-V curve
    if len(avg_I) > 1:
        pts = []
        for I_val, V_val in zip(avg_I, avg_V):
            if np.isnan(V_val):
                if len(pts) > 1:
                    draw.line(pts, fill=(233, 69, 96), width=2)
                pts = []
                continue
            x = left + (float(I_val) - I_min) / I_den * (right - left)
            y = top + (1 - (float(V_val) - V_min) / V_den) * (bottom - top)
            pts.append((x, y))
        if len(pts) > 1:
            draw.line(pts, fill=(233, 69, 96), width=2)
        # Draw small dots at each completed step
        for I_val, V_val in zip(avg_I, avg_V):
            if np.isnan(V_val):
                continue
            x = left + (float(I_val) - I_min) / I_den * (right - left)
            y = top + (1 - (float(V_val) - V_min) / V_den) * (bottom - top)
            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(233, 69, 96))

    # Current playback position dot (white on black)
    if len(cur_I_raw) and not np.isnan(cur_V_raw[-1]):
        x = left + (float(cur_I_raw[-1]) - I_min) / I_den * (right - left)
        y = top + (1 - (float(cur_V_raw[-1]) - V_min) / V_den * (bottom - top))
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(0, 0, 0))
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255))

    draw.text(
        ((left + right) // 2 - 82, y1 - 18),
        "I (transport current)",
        fill=(150, 150, 150),
    )
    draw.text((8, y0 + 70), "V", fill=(150, 150, 150))
    # Status line: completed steps / total steps
    step_info = f"Je steps: {n_completed}/{n_total}" if n_total > 0 else f"IV cached={iv_cache.size()}"
    cur_t_raw = iv_cache.t[:idx + 1] if idx + 1 <= len(iv_cache.t) else iv_cache.t
    t_label = f"t={cur_t_raw[-1]:.3g}" if len(cur_t_raw) else "t=?"
    draw.text(
        (right - 250, top + 4),
        f"{t_label}, {step_info}",
        fill=(150, 150, 150),
    )
```

- [ ] **Step 2: Verify import succeeds**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._render import render_frame_png; print('OK')"`
Expected: `OK`

---

### Task 3: Pass timing steps through the player pipeline

**Files:**
- Modify: `src/tdgl_sdk/viewer/_player.py`

- [ ] **Step 1: Update create_player to accept timing_steps**

In `src/tdgl_sdk/viewer/_player.py`, modify the `create_player` function (around line 590) to accept and pass `timing_steps`:

```python
def create_player(
    h5_path: str,
    live: bool = False,
    playback_dt: float = 1.0,
    timing_steps: list | None = None,
    **s3_kwds,
) -> RealtimeTDGLWidgetPlayer:
    """Create a widget player for an HDF5 file.

    Args:
        h5_path: Path to the HDF5 file (local path or http:// URL for MinIO).
        live: If True, auto-plays and waits at the boundary for new frames.
        playback_dt: Simulation time per animation step (default 1.0).
        timing_steps: Optional list of step dicts from build_timing() for
                      step-averaged I-V curve. When provided, the I-V curve
                      shows averaged voltage per completed Je step.
        **s3_kwds: S3 credentials for ROS3 driver (s3_access_key, s3_secret_key).
    """
    mesh = load_mesh(h5_path, **s3_kwds)
    mu_vmax = estimate_mu_vmax(h5_path, mesh["total_frames"], **s3_kwds)
    iv_cache = IVCache(h5_path, mesh, poll_interval=1.0, batch_size=128, **s3_kwds)
    if timing_steps is not None:
        iv_cache.set_timing_steps(timing_steps)
    iv_cache.ensure(0)
    iv_cache.start()
    player = RealtimeTDGLWidgetPlayer(h5_path, mesh, iv_cache, mu_vmax, **s3_kwds)
    player.live = live
    player.playback_dt = playback_dt
    return player
```

- [ ] **Step 2: Update StreamingTDGLPlayer to compute and pass timing steps**

In `StreamingTDGLPlayer._create_player` (around line 550), compute timing steps from `timing_params` and pass to `create_player`:

```python
def _create_player(self, status, solve_time):
    """Create the inner player with pre-allocated time grid."""
    from IPython.display import clear_output

    with self.output:
        clear_output(wait=True)

    # Compute timing steps from timing_params for step-averaged I-V
    timing_steps = self._compute_timing_steps()

    self._player = create_player(
        self._h5_url, live=(status == "running"),
        playback_dt=self._playback_dt,
        timing_steps=timing_steps,
        **self._s3_kwds,
    )

    # Pre-allocate the timeline from solve_time
    if solve_time and solve_time > 0:
        self._player.set_time_grid(solve_time, self._playback_dt)

    with self.output:
        clear_output(wait=True)
        self._player.display_player()  # auto-plays if live
```

Add the `_compute_timing_steps` method to `StreamingTDGLPlayer`:

```python
def _compute_timing_steps(self):
    """Compute timing step boundaries from timing_params."""
    if not self._timing_params:
        return None
    try:
        from tdgl_workflow.timing import build_timing
        result = build_timing(**self._timing_params)
        return result.get("steps", [])
    except Exception:
        return None
```

- [ ] **Step 3: Verify import succeeds**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._player import create_player, StreamingTDGLPlayer; print('OK')"`
Expected: `OK`

---

### Task 4: Update notebook to pass timing_params

**Files:**
- Modify: `notebooks/e2e_sim_test.py`

- [ ] **Step 1: Compute timing_steps for static player (Step 6)**

In the notebook, before the `create_player` call (around line 145), compute timing steps:

```python
# Compute timing steps for step-averaged I-V curve
from tdgl_workflow.timing import build_timing
_timing = build_timing(**TIMING_PARAMS)
timing_steps = _timing.get("steps", [])
```

Then update the `create_player` call:

```python
player = create_player(h5_url, timing_steps=timing_steps, **s3_kwds)
```

The `watch_live` / `pipeline.watch_live` call (Step 2) already passes `timing_params=TIMING_PARAMS`, which `StreamingTDGLPlayer` will now use automatically — no change needed there.

- [ ] **Step 2: Verify the notebook parses without error**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "
from tdgl_workflow.timing import build_timing
steps = build_timing(je_initial=0.0, je_final=12, je_step=0.2, ramp_time=100.0, stable_time=200.0, save_time=50.0)
print(f'Steps: {len(steps[\"steps\"])}, solve_time: {steps[\"solve_time\"]}')
print(f'First step: {steps[\"steps\"][0]}')
"`
Expected: Prints step count and first step boundaries

---

### Task 5: Update get_iv_data for step-averaged mode

**Files:**
- Modify: `src/tdgl_sdk/viewer/_player.py`

- [ ] **Step 1: Add step_averaged flag to get_iv_data**

In `RealtimeTDGLWidgetPlayer.get_iv_data` (around line 380), add support for step-averaged data. When timing steps are set and `step_averaged=True`, return step-averaged data:

```python
def get_iv_data(self, upto: int | None = None, step_averaged: bool = False) -> dict:
    import numpy as np

    self._refresh_total()
    target = self.total - 1 if upto is None else upto
    self.iv_cache.ensure(max(0, target))

    if step_averaged:
        avg_I, avg_V, n_completed, n_total = self.iv_cache.step_averaged_iv()
        valid = ~np.isnan(avg_V)
        I = avg_I[valid]
        V = avg_V[valid]
    else:
        I_all, V_all, t_all = self.iv_cache.arrays(upto=upto)
        valid = ~np.isnan(V_all)
        I = I_all[valid]
        V = V_all[valid]
        t = t_all[valid]

    I_min, I_max = (float(I.min()), float(I.max())) if len(I) > 0 else (0.0, 1.0)
    V_min, V_max = (float(V.min()), float(V.max())) if len(V) > 0 else (0.0, 1.0)
    if I_min == I_max:
        I_min -= 0.5; I_max += 0.5
    if V_min == V_max:
        V_min -= 0.5; V_max += 0.5

    # Current playback position on the I-V curve
    current_I, current_V = None, None
    I_all_raw, V_all_raw, _ = self.iv_cache.arrays()
    if self.current < len(self.time_grid) and len(I_all_raw) > 0:
        frame_idx = self._find_frame_for_time(
            self.time_grid[self.current]
        )
        if 0 <= frame_idx < len(I_all_raw):
            current_I = float(I_all_raw[frame_idx])
            current_V = float(V_all_raw[frame_idx])

    return {
        "n_points": len(I),
        "I": I.tolist(),
        "V": V.tolist(),
        "I_range": [I_min, I_max],
        "V_range": [V_min, V_max],
        "current_I": current_I,
        "current_V": current_V,
        "step_averaged": step_averaged,
    }
```

- [ ] **Step 2: Verify import succeeds**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "from tdgl_sdk.viewer._player import create_player; print('OK')"`
Expected: `OK`

---

### Task 6: Update the notebook I-V curve plot (Step 8) to use step-averaged data

**Files:**
- Modify: `notebooks/e2e_sim_test.py`

- [ ] **Step 1: Update I-V plot to use step-averaged data**

In the notebook Step 8 (around line 169), change `get_iv_data()` to `get_iv_data(step_averaged=True)`:

```python
# ── Step 8: I-V curve ───────────────────────────────────────────────────
# Step-averaged I-V: one point per completed Je step, V averaged over save_time.
# Blue dot marks the current playback position.
iv = player.get_iv_data(step_averaged=True)
print(f"I-V points (Je steps): {iv['n_points']}")
```

- [ ] **Step 2: Verify the notebook file parses**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "
import ast
with open('notebooks/e2e_sim_test.py') as f:
    ast.parse(f.read())
print('Syntax OK')
"`
Expected: `Syntax OK`

---

### Task 7: End-to-end smoke test

- [ ] **Step 1: Run full import chain verification**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -c "
from tdgl_sdk.viewer._iv import IVCache
from tdgl_sdk.viewer._render import render_frame_png
from tdgl_sdk.viewer._player import create_player, StreamingTDGLPlayer
from tdgl_workflow.timing import build_timing

# Verify step computation
t = build_timing(je_initial=0.0, je_final=2.0, je_step=0.5, ramp_time=10.0, stable_time=20.0, save_time=5.0)
steps = t['steps']
print(f'Steps: {len(steps)}, solve_time: {t[\"solve_time\"]}')

# Verify IVCache has new methods
assert hasattr(IVCache, 'set_timing_steps'), 'Missing set_timing_steps'
assert hasattr(IVCache, 'step_averaged_iv'), 'Missing step_averaged_iv'

# Verify create_player accepts timing_steps
import inspect
sig = inspect.signature(create_player)
assert 'timing_steps' in sig.parameters, 'Missing timing_steps param'

print('All checks passed')
"`
Expected: `All checks passed`

- [ ] **Step 2: Commit all changes**

```bash
git add src/tdgl_sdk/viewer/_iv.py src/tdgl_sdk/viewer/_render.py src/tdgl_sdk/viewer/_player.py notebooks/e2e_sim_test.py
git commit -m "feat: step-averaged I-V curve — buffer all completed Je steps, mark current position"
```
