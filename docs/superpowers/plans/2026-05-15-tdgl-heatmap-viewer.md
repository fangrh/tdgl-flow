# TDGL Heatmap Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser viewer that renders `|psi|` and `mu` heatmaps from the TDGL frame API.

**Architecture:** FastAPI serves a static HTML/JS viewer and exposes a demo-run endpoint that writes synthetic frames through the same storage path as normal frame appends. A separate development app factory enables schema creation for local viewer testing without changing the production factory default.

**Tech Stack:** FastAPI, Pydantic, NumPy, existing Zarr storage, plain HTML/CSS/JavaScript canvas rendering, pytest.

---

## File Structure

- Modify `tdgl_data/schemas.py`: add `CreateDemoRunRequest`.
- Modify `tdgl_data/app.py`: add `/viewer` and `/api/demo-runs`.
- Create `tdgl_data/dev_app.py`: local factory that calls `create_app(create_schema=True)`.
- Create `tdgl_data/static/viewer.html`: browser UI and canvas renderer.
- Modify `README.md`: document how to run the viewer.
- Modify `tests/test_api.py`: test viewer route and demo data.
- Modify `tests/test_synthetic.py`: test the synthetic demo I-V curve shape.

### Task 1: Viewer and Demo API

**Files:**
- Modify: `tdgl_data/schemas.py`
- Modify: `tdgl_data/app.py`
- Create: `tdgl_data/dev_app.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert `/viewer` returns HTML, `/api/demo-runs` creates readable frames, and the dev factory can create a run without a missing schema error.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_api.py::test_viewer_returns_html tests/test_api.py::test_create_demo_run_writes_readable_heatmap_frames tests/test_api.py::test_dev_app_factory_creates_schema -v`

Expected: fail because routes and factory do not exist.

- [ ] **Step 3: Implement minimal backend**

Add `CreateDemoRunRequest`, serve `tdgl_data/static/viewer.html`, create demo runs using `generate_synthetic_run`, and add `create_dev_app()`.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_api.py::test_viewer_returns_html tests/test_api.py::test_create_demo_run_writes_readable_heatmap_frames tests/test_api.py::test_dev_app_factory_creates_schema -v`

Expected: pass.

### Task 2: Browser Heatmap UI

**Files:**
- Create: `tdgl_data/static/viewer.html`
- Modify: `README.md`

- [ ] **Step 1: Implement the static viewer**

Use plain JavaScript to load runs, create demo data, fetch timeline/frame data,
compute `|psi|`, and draw `|psi|` and `mu` to two canvases.

- [ ] **Step 2: Run backend tests**

Run: `pytest`

Expected: pass.

- [ ] **Step 3: Start the dev server**

Run: `uvicorn tdgl_data.dev_app:create_dev_app --factory --reload`

Expected: server starts and `/viewer` is available.

- [ ] **Step 4: Manual browser check**

Open `http://127.0.0.1:8000/viewer`, click `Create demo`, move the slider, and
confirm the two heatmaps update.

### Task 3: I-V Curve Plot

**Files:**
- Modify: `tdgl_data/synthetic.py`
- Modify: `tdgl_data/static/viewer.html`
- Modify: `tests/test_synthetic.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert generated synthetic voltage is nonlinear and the viewer
HTML includes an `ivCanvas` element plus I-V rendering hooks.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_synthetic.py::test_generated_current_voltage_curve_is_nonlinear tests/test_api.py::test_viewer_includes_iv_curve_plot -v`

Expected: fail because voltage is currently linear and the viewer has no I-V
canvas.

- [ ] **Step 3: Implement minimal I-V plot**

Update synthetic voltage to a nonlinear curve, fetch `/iv` in the viewer, draw
the I-V curve to a canvas, and draw an annotation dot for the current frame.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_synthetic.py::test_generated_current_voltage_curve_is_nonlinear tests/test_api.py::test_viewer_includes_iv_curve_plot -v`

Expected: pass.

- [ ] **Step 5: Run full verification**

Run: `pytest` and `ruff check tdgl_data/app.py tdgl_data/schemas.py tdgl_data/dev_app.py tdgl_data/synthetic.py tests/test_api.py tests/test_synthetic.py`

Expected: pass.

### Task 6: Single-Row Desktop Plot Layout

**Files:**
- Modify: `tdgl_data/static/viewer.html`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

Add a viewer HTML test that checks for the single-row layout class
`plots-one-line` and that the I-V panel no longer uses `plot-wide`.

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_api.py::test_viewer_uses_single_row_plot_layout -v`

Expected: fail because the current I-V panel spans the full width.

- [ ] **Step 3: Implement CSS/markup change**

Use a three-column grid on desktop for the two heatmaps and I-V plot. Keep a
single-column stack on small screens.

- [ ] **Step 4: Run focused test**

Run: `pytest tests/test_api.py::test_viewer_uses_single_row_plot_layout -v`

Expected: pass.

- [ ] **Step 5: Run full verification**

Run: `pytest` and `ruff check tdgl_data/app.py tdgl_data/schemas.py tdgl_data/dev_app.py tdgl_data/synthetic.py tests/test_api.py tests/test_synthetic.py`

Expected: pass.

### Task 5: Static Tick Marks

**Files:**
- Modify: `tdgl_data/static/viewer.html`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

Add a viewer HTML test that checks for static tick rendering hooks:
`drawHeatmapAxes`, `drawColorbarTicks`, and `staticTickValues`.

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_api.py::test_viewer_includes_static_tick_rendering -v`

Expected: fail because these hooks are not present yet.

- [ ] **Step 3: Implement static ticks**

Draw heatmap x/y ticks on the heatmap canvases using array index coordinates.
Draw five colorbar tick marks and labels from the fixed global colorbar bounds.

- [ ] **Step 4: Run focused test**

Run: `pytest tests/test_api.py::test_viewer_includes_static_tick_rendering -v`

Expected: pass.

- [ ] **Step 5: Run full verification**

Run: `pytest` and `ruff check tdgl_data/app.py tdgl_data/schemas.py tdgl_data/dev_app.py tdgl_data/synthetic.py tests/test_api.py tests/test_synthetic.py`

Expected: pass.

### Task 4: Fixed Global Colorbars

**Files:**
- Modify: `tdgl_data/static/viewer.html`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Add a viewer HTML test that checks for `psiColorbar`, `muColorbar`,
`computePsiBounds`, and `drawColorbar`.

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_api.py::test_viewer_includes_fixed_global_colorbars -v`

Expected: fail because the viewer has no colorbar canvases or global `|psi|`
bound function.

- [ ] **Step 3: Implement colorbars**

Add compact colorbar canvases beside each heatmap. Use timeline global `mu`
bounds and compute global `|psi|` bounds once per run by reading each available
frame. Draw heatmaps and colorbars with these fixed bounds.

- [ ] **Step 4: Run focused test**

Run: `pytest tests/test_api.py::test_viewer_includes_fixed_global_colorbars -v`

Expected: pass.

- [ ] **Step 5: Run full verification**

Run: `pytest` and `ruff check tdgl_data/app.py tdgl_data/schemas.py tdgl_data/dev_app.py tdgl_data/synthetic.py tests/test_api.py tests/test_synthetic.py`

Expected: pass.
