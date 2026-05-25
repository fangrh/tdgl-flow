# V-vs-t Current Step Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the V-vs-t panel to show only the current Je step's voltage trace, with time starting from 0.

**Architecture:** Modify `_draw_vt` to look up which timing step the current frame belongs to, filter V/t arrays to that step's time range `[ramp_start, stable_end)`, and subtract `ramp_start` so the x-axis starts at 0. When no timing steps are set, fall back to the current behavior (full trace).

**Tech Stack:** Python, numpy, PIL/Pillow (same as existing render code).

---

### Task 1: Modify `_draw_vt` to show current step only

**Files:**
- Modify: `src/tdgl_sdk/viewer/_render.py` lines 229-289

Replace the existing `_draw_vt` function with the version below. The key changes:
1. Look up the current step from `iv_cache._timing_steps` using the current frame's time
2. Filter V/t to only frames within `[ramp_start, stable_end)`
3. Subtract `ramp_start` from time values so x-axis starts at 0
4. Add step info label ("Je step N/M") in the top-right corner
5. Fall back to full trace when no timing steps are set

- [ ] **Step 1: Replace `_draw_vt` function**

Replace the entire `_draw_vt` function (lines 229-289) in `src/tdgl_sdk/viewer/_render.py` with:

```python
def _draw_vt(draw, iv_cache, idx, box, debug_log=None):
    """Draw voltage vs time for the current Je step, time starting at 0."""
    iv_cache.ensure(idx)
    _, V_all, t_all = iv_cache.arrays(upto=idx)
    if len(t_all) == 0:
        t_all = np.array([0.0, 1.0])
        V_all = np.array([0.0, 1.0])

    # Find the current Je step from timing_steps
    cur_t = t_all[-1] if len(t_all) else 0.0
    timing_steps = getattr(iv_cache, "_timing_steps", None)
    current_step = None
    step_idx = 0
    if timing_steps:
        for si, step in enumerate(timing_steps):
            if step["ramp_start"] <= cur_t < step["stable_end"]:
                current_step = step
                step_idx = si + 1
                break

    # Filter to current step, offset time to 0
    if current_step is not None:
        mask = (t_all >= current_step["ramp_start"]) & (t_all < current_step["stable_end"])
        t_step = t_all[mask] - current_step["ramp_start"]
        V_step = V_all[mask]
    else:
        t_step = t_all
        V_step = V_all

    valid = ~np.isnan(V_step)
    t_valid = t_step[valid]
    V_valid = V_step[valid]
    if len(t_valid) == 0:
        t_valid = np.array([0.0, 1.0])
        V_valid = np.array([0.0, 1.0])

    t_min, t_max = float(t_valid.min()), float(t_valid.max())
    V_min, V_max = float(V_valid.min()), float(V_valid.max())
    if t_min == t_max:
        t_min -= 0.5; t_max += 0.5
    if V_min == V_max:
        V_min -= 0.5; V_max += 0.5
    t_den = t_max - t_min or 1.0
    V_den = V_max - V_min or 1.0

    x0, y0, x1, y1 = box
    left, right, top, bottom = x0 + 54, x1 - 20, y0 + 18, y1 - 34
    draw.rectangle([x0, y0, x1, y1], fill=(30, 30, 30))

    # Grid lines + tick labels
    for t in range(5):
        y = top + (1 - t / 4) * (bottom - top)
        draw.line([(left, y), (right, y)], fill=(50, 50, 50))
        draw.text((left - 48, y - 6), f"{V_min + t / 4 * V_den:.2f}", fill=(150, 150, 150))
    for t in range(5):
        x = left + t / 4 * (right - left)
        draw.text((x - 18, bottom + 8), f"{t_min + t / 4 * t_den:.2g}", fill=(150, 150, 150))
    draw.line([(left, top), (left, bottom), (right, bottom)], fill=(105, 105, 105), width=1)

    # Blue V-vs-t trace
    pts = []
    for t_val, V_val in zip(t_valid, V_valid):
        x = left + (float(t_val) - t_min) / t_den * (right - left)
        y = top + (1 - (float(V_val) - V_min) / V_den) * (bottom - top)
        pts.append((x, y))
    if len(pts) > 1:
        draw.line(pts, fill=(100, 149, 237), width=2)

    # Current playback position dot (white on black)
    if len(t_valid) and not np.isnan(V_valid[-1]):
        x = left + (float(t_valid[-1]) - t_min) / t_den * (right - left)
        y = top + (1 - (float(V_valid[-1]) - V_min) / V_den) * (bottom - top)
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(0, 0, 0))
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255))

    # Axis labels
    draw.text(
        ((left + right) // 2 - 18, y1 - 18),
        "t",
        fill=(150, 150, 150),
    )
    draw.text((8, y0 + 70), "V", fill=(150, 150, 150))

    # Step info
    if timing_steps and current_step:
        draw.text(
            (right - 140, top + 4),
            f"Je step {step_idx}/{len(timing_steps)}",
            fill=(150, 150, 150),
        )
```

- [ ] **Step 2: Commit**

```bash
git add src/tdgl_sdk/viewer/_render.py
git commit -m "feat(viewer): show only current Je step in V-vs-t with time from 0"
```
