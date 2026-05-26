# Widget Layout Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the TdglViewer ipywidgets layout — better visual grouping, easier `average_time` control, cleaner spacing.

**Architecture:** Restructure the `display()` method's VBox from one crowded settings row into two logical groups (playback vs analysis). Replace the finicky `FloatSlider` for average_time with a `BoundedFloatText` + descriptive label. Add section headers and gap spacing for visual clarity.

**Tech Stack:** ipywidgets (already in use), no new dependencies.

---

### Task 1: Replace average_time FloatSlider with BoundedFloatText

**Files:**
- Modify: `tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py:108-112`

The current `FloatSlider` (0.1–1.0, step 0.05, width 180px) is too small to drag precisely. Replace with a `BoundedFloatText` for direct numeric input — much easier to set exact values.

- [ ] **Step 1: Replace the avg_slider widget definition**

In `widget.py`, replace lines 108–112:

```python
# OLD:
avg_slider = widgets.FloatSlider(
    value=self._average_time, min=0.1, max=1.0, step=0.05, description="Avg",
    continuous_update=False,
    layout=widgets.Layout(width="180px"),
)

# NEW:
avg_input = widgets.BoundedFloatText(
    value=self._average_time, min=0.1, max=1.0, step=0.05,
    description="Avg time",
    layout=widgets.Layout(width="140px"),
    style={"description_width": "55px"},
)
```

- [ ] **Step 2: Update all references from `avg_slider` to `avg_input`**

There are 3 references to update in `display()`:

1. The `on_avg_change` callback (line 215) — rename `avg_slider` → `avg_input` in the callback body and the observe call (line 292)
2. The `on_dropdown` callback (line 242) — `avg_slider.value` → `avg_input.value`
3. The HBox construction (line 304) — `avg_slider` → `avg_input`

Specifically:

```python
# on_avg_change (line 215): no change needed inside, just the observe target
# line 292:
avg_input.observe(on_avg_change, names="value")

# on_dropdown (line 242):
_start_iv(avg_input.value)

# HBox construction (line 304): see Task 2 for the new layout
```

- [ ] **Step 3: Rebuild and verify**

```bash
cd tdgl-viewer-rust && maturin develop --release
```

Open `notebooks/browse_rust_viewer.py` in Jupyter, verify the Avg control is now a text input you can type into.

- [ ] **Step 4: Commit**

```bash
git add tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py
git commit -m "fix: replace avg FloatSlider with BoundedFloatText for easier adjustment"
```

---

### Task 2: Restructure layout into logical groups with visual headers

**Files:**
- Modify: `tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py:301-307`

Split the single crowded HBox `[fps_slider, speed_input, vt_dot_check, avg_slider, iv_status]` into two groups with section labels and gap spacing.

- [ ] **Step 1: Add section header helper and rebuild the UI layout**

Replace lines 301–307 (the `ui = widgets.VBox([...])` block and the `display(ui)` call) with:

```python
        _section = lambda text: widgets.HTML(
            value=f'<div style="font-size:11px;color:#888;margin:4px 0 2px 0">{text}</div>'
        )

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, slider, time_label]),
            _section("Playback"),
            widgets.HBox(
                [fps_slider, speed_input],
                layout=widgets.Layout(gap="12px", padding="0 0 0 4px"),
            ),
            _section("Analysis"),
            widgets.HBox(
                [vt_dot_check, avg_input, iv_status],
                layout=widgets.Layout(gap="12px", padding="0 0 0 4px"),
            ),
            image,
        ], layout=widgets.Layout(padding="8px"))
        display(ui)
```

This creates a clear visual hierarchy:
- **Row 1:** Run dropdown
- **Row 2:** Transport bar (play + slider + frame counter)
- **Row 3:** "Playback" header + FPS + Speed
- **Row 4:** "Analysis" header + V(t) dot + Avg time + IV status
- **Row 5:** Image

- [ ] **Step 2: Widen FPS slider slightly for better usability**

Change the fps_slider layout (line 94–98) width from `180px` to `200px`:

```python
fps_slider = widgets.IntSlider(
    value=self._fps, min=1, max=30, description="FPS",
    continuous_update=False,
    layout=widgets.Layout(width="200px"),
)
```

- [ ] **Step 3: Rebuild and visual test**

```bash
cd tdgl-viewer-rust && maturin develop --release
```

Open `browse_rust_viewer.py` in Jupyter. Verify:
- Two section headers ("Playback" and "Analysis") appear
- Controls are visually separated into two rows
- Gap spacing between controls looks clean
- Image still renders correctly below

- [ ] **Step 4: Commit**

```bash
git add tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py
git commit -m "refactor: reorganize viewer controls into grouped layout with section headers"
```

---

## Self-Review

**Spec coverage:**
- "控件布局不好看" → Task 2 restructures into grouped layout with headers and gaps
- "average 不好调整" → Task 1 replaces FloatSlider with BoundedFloatText for direct input

**Placeholder scan:** No TBDs, TODOs, or vague steps. All code shown inline.

**Type consistency:** `avg_input` is a `BoundedFloatText` with `.value` (float) — same interface as the old `FloatSlider`, so `on_avg_change` and `on_dropdown` callbacks work identically. The `observe(..., names="value")` pattern is the same for both widget types.

---

## Preview: Before vs After

**Before:**
```
[Run dropdown                                    ]
[▶ Play] [========frame slider========] [frame 0/100]
[FPS ═══ Speed[1] ☑V(t) dot Avg ═══ IV: idle    ]
[================== IMAGE ==================]
```

**After:**
```
[Run dropdown                                    ]
[▶ Play] [========frame slider========] [frame 0/100]
Playback
  [FPS ══════ ]  [Speed [1]      ]
Analysis
  [☑V(t) dot]  [Avg time [0.50]]  [I-V: idle]
[================== IMAGE ==================]
```
