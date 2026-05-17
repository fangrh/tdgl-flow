# Plotly Visualization & Session Cookie Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace static matplotlib PNGs with interactive Plotly.js plots showing electrodes/probes, and fix the session cookie overflow by only storing input params in the session (regenerate mesh on demand).

**Architecture:** The session currently stores the full mesh data (~100KB+ of sites/elements arrays) which overflows nginx proxy buffers as cookies. Fix: store only input parameters in the session, add a JSON API endpoint `/api/preview/mesh` that returns mesh data for Plotly.js to render client-side. The simulate route regenerates the mesh from stored params on submission. Same pattern for timing.

**Tech Stack:** Plotly.js (CDN), FastAPI JSON endpoints, existing tdgl library for mesh generation.

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/tdgl_workflow/routes/device.py` | Modify | Add `/api/preview/mesh` JSON endpoint, store only params in session |
| `src/tdgl_workflow/routes/timing.py` | Modify | Add `/api/preview/timing` JSON endpoint, store only params in session |
| `src/tdgl_workflow/routes/simulate.py` | Modify | Regenerate mesh from params on submission |
| `src/tdgl_workflow/routes/api.py` | Create | Shared JSON preview endpoints |
| `src/tdgl_workflow/templates/device.html` | Modify | Replace `<img>` with Plotly.js div + JS |
| `src/tdgl_workflow/templates/timing.html` | Modify | Replace `<img>` with Plotly.js div + JS |
| `src/tdgl_workflow/templates/base.html` | Modify | Add Plotly.js CDN script |
| `src/tdgl_workflow/plots.py` | Delete | No longer needed (client-side rendering) |
| `infra/nginx/configmap.yaml` | Modify | Remove the 256k buffer hack (no longer needed) |

---

### Task 1: Store only input params in session, remove mesh data from session

**Files:**
- Modify: `src/tdgl_workflow/routes/device.py`

**Context:** Currently `device_params` in session contains a `mesh` key with sites/elements arrays (~100KB+). We only need to store the scalar input params. The mesh will be regenerated when needed (preview and submission).

- [ ] **Step 1: Update device.py POST handler to NOT store mesh in session**

In `src/tdgl_workflow/routes/device.py`, change the POST handler:

Replace the lines that build `device_params` with mesh data:
```python
    device_params = {k: v for k, v in params.items()}
    device_params["mesh"] = {
        "sites": mesh_data["sites"],
        "elements": mesh_data["elements"],
        "probe_indices": mesh_data["probe_indices"],
        "num_sites": mesh_data["num_sites"],
        "num_elements": mesh_data["num_elements"],
    }
    request.session["device_params"] = device_params
```

With this (only store input params, plus num_sites/num_elements as metadata):
```python
    request.session["device_params"] = {
        "film_width": params["film_width"],
        "film_height": params["film_height"],
        "elec_width": params["elec_width"],
        "elec_height": params["elec_height"],
        "elec_y_offset": params["elec_y_offset"],
        "probe_points": params["probe_points"],
        "max_edge_length": params["max_edge_length"],
        "smooth": params["smooth"],
    }
```

Also change the POST to return mesh data as JSON for the AJAX call instead of rendering template. But first, let's do the simpler change: just return mesh_data via a new endpoint.

The full updated `device.py` POST handler should be:

```python
@router.post("/device", response_class=HTMLResponse)
async def device_preview(request: Request):
    form_data = await request.form()
    form = {k: v for k, v in form_data.items()}
    action = form.get("action", "preview")

    params = _device_form_data(form)

    request.session["device_params"] = {
        "film_width": params["film_width"],
        "film_height": params["film_height"],
        "elec_width": params["elec_width"],
        "elec_height": params["elec_height"],
        "elec_y_offset": params["elec_y_offset"],
        "probe_points": params["probe_points"],
        "max_edge_length": params["max_edge_length"],
        "smooth": params["smooth"],
    }

    if action == "next":
        return RedirectResponse("/timing", status_code=303)

    return _render_template("device.html", {
        "request": request,
        "page": "device",
        "form": form,
        "plot_b64": None,
    })
```

- [ ] **Step 2: Commit**

```bash
git add src/tdgl_workflow/routes/device.py
git commit -m "fix: store only input params in session, remove mesh data to prevent cookie overflow"
```

---

### Task 2: Add JSON preview API endpoints

**Files:**
- Create: `src/tdgl_workflow/routes/api.py`
- Modify: `src/tdgl_workflow/app.py`

**Context:** The browser needs mesh/timing data as JSON to render with Plotly.js. Add POST endpoints that accept params and return JSON data.

- [ ] **Step 1: Create api.py with preview endpoints**

Create `src/tdgl_workflow/routes/api.py`:

```python
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.timing import build_timing

router = APIRouter(prefix="/api")


@router.post("/preview/mesh")
async def preview_mesh(request: Request):
    form = await request.json()
    mesh_data = build_rectangular_device(
        film_width=float(form["film_width"]),
        film_height=float(form["film_height"]),
        elec_width=float(form["elec_width"]),
        elec_height=float(form["elec_height"]),
        elec_y_offset=float(form["elec_y_offset"]),
        probe_points=[tuple(p) for p in form["probe_points"]],
        max_edge_length=float(form["max_edge_length"]),
        smooth=int(form["smooth"]),
    )
    return JSONResponse(mesh_data)


@router.post("/preview/timing")
async def preview_timing(request: Request):
    form = await request.json()
    params = {
        "je_initial": float(form["je_initial"]),
        "je_final": float(form["je_final"]),
        "je_step": float(form["je_step"]),
        "ramp_time": float(form["ramp_time"]),
        "stable_time": float(form["stable_time"]),
        "save_time": float(form["save_time"]),
        "ramp_down": form.get("ramp_down", False),
    }
    timing_data = build_timing(**params)
    return JSONResponse(timing_data)
```

- [ ] **Step 2: Register the api router in app.py**

In `src/tdgl_workflow/app.py`, add the import and include:

After the existing router imports, add:
```python
    from tdgl_workflow.routes.api import router as api_router
```

After the existing `app.include_router` calls, add:
```python
    app.include_router(api_router)
```

The full `create_app` function becomes:
```python
def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title=settings.app_name)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.state.settings = settings

    from tdgl_workflow.routes.device import router as device_router
    from tdgl_workflow.routes.timing import router as timing_router
    from tdgl_workflow.routes.simulate import router as simulate_router
    from tdgl_workflow.routes.api import router as api_router

    app.include_router(device_router)
    app.include_router(timing_router)
    app.include_router(simulate_router)
    app.include_router(api_router)

    @app.get("/")
    def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/device")

    return app
```

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_workflow/routes/api.py src/tdgl_workflow/app.py
git commit -m "feat: add JSON preview API endpoints for mesh and timing"
```

---

### Task 3: Switch device page to Plotly.js with AJAX preview

**Files:**
- Modify: `src/tdgl_workflow/templates/base.html`
- Modify: `src/tdgl_workflow/templates/device.html`

**Context:** Replace the static matplotlib `<img>` with a Plotly.js interactive plot. The form submits via `fetch()` to the JSON API, then renders the mesh client-side with Plotly. Electrodes and probes are drawn as shapes/annotations.

- [ ] **Step 1: Add Plotly.js CDN to base.html**

In `src/tdgl_workflow/templates/base.html`, add before the closing `</head>` tag:

```html
    <script src="https://cdn.plot.ly/plotly-3.0.1.min.js"></script>
```

- [ ] **Step 2: Rewrite device.html with Plotly.js**

Replace the full content of `src/tdgl_workflow/templates/device.html` with:

```html
{% extends "base.html" %}
{% block title %}Device Builder{% endblock %}
{% block content %}
<h1>Device Builder</h1>
<form id="deviceForm" method="post">
    <div class="field-row">
        <div>
            <label>Film Width</label>
            <input type="number" step="any" name="film_width" id="film_width" value="{{ form.get('film_width', '10') }}">
        </div>
        <div>
            <label>Film Height</label>
            <input type="number" step="any" name="film_height" id="film_height" value="{{ form.get('film_height', '2') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Electrode Width</label>
            <input type="number" step="any" name="elec_width" id="elec_width" value="{{ form.get('elec_width', '0.5') }}">
        </div>
        <div>
            <label>Electrode Height</label>
            <input type="number" step="any" name="elec_height" id="elec_height" value="{{ form.get('elec_height', '1') }}">
        </div>
        <div>
            <label>Electrode Y Offset</label>
            <input type="number" step="any" name="elec_y_offset" id="elec_y_offset" value="{{ form.get('elec_y_offset', '0') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Probe 1 (x, y)</label>
            <input type="number" step="any" name="probe1_x" id="probe1_x" value="{{ form.get('probe1_x', '-3') }}">
            <input type="number" step="any" name="probe1_y" id="probe1_y" value="{{ form.get('probe1_y', '0') }}">
        </div>
        <div>
            <label>Probe 2 (x, y)</label>
            <input type="number" step="any" name="probe2_x" id="probe2_x" value="{{ form.get('probe2_x', '3') }}">
            <input type="number" step="any" name="probe2_y" id="probe2_y" value="{{ form.get('probe2_y', '0') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Max Edge Length</label>
            <input type="number" step="any" name="max_edge_length" id="max_edge_length" value="{{ form.get('max_edge_length', '1.0') }}">
        </div>
        <div>
            <label>Smooth Iterations</label>
            <input type="number" name="smooth" id="smooth" value="{{ form.get('smooth', '100') }}">
        </div>
    </div>
    <button type="button" id="previewBtn">Build &amp; Preview</button>
    <button type="submit" name="action" value="next" class="secondary">Next: Timing</button>
    <span id="status" style="margin-left:1rem;color:#64748b;font-size:0.9rem;"></span>
</form>
<div id="meshPlot" class="preview" style="display:none;">
    <h2>Mesh Preview <span id="meshInfo" style="font-weight:normal;color:#64748b;font-size:0.85rem;"></span></h2>
    <div id="plotDiv"></div>
</div>
<script>
document.getElementById("previewBtn").addEventListener("click", async function() {
    var btn = this;
    var status = document.getElementById("status");
    btn.disabled = true;
    status.textContent = "Building mesh...";

    var body = {
        film_width: parseFloat(document.getElementById("film_width").value),
        film_height: parseFloat(document.getElementById("film_height").value),
        elec_width: parseFloat(document.getElementById("elec_width").value),
        elec_height: parseFloat(document.getElementById("elec_height").value),
        elec_y_offset: parseFloat(document.getElementById("elec_y_offset").value),
        probe_points: [
            [parseFloat(document.getElementById("probe1_x").value),
             parseFloat(document.getElementById("probe1_y").value)],
            [parseFloat(document.getElementById("probe2_x").value),
             parseFloat(document.getElementById("probe2_y").value)]
        ],
        max_edge_length: parseFloat(document.getElementById("max_edge_length").value),
        smooth: parseInt(document.getElementById("smooth").value)
    };

    try {
        var resp = await fetch("/api/preview/mesh", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body)
        });
        if (!resp.ok) throw new Error("Server error: " + resp.status);
        var data = await resp.json();

        var sites = data.sites;
        var elements = data.elements;
        var probes = data.probe_points;
        var w = data.film_width;
        var h = data.film_height;
        var ew = data.elec_width;
        var eh = data.elec_height;
        var ey = data.elec_y_offset;

        // Build triangulation traces
        var x = sites.map(function(s) { return s[0]; });
        var y = sites.map(function(s) { return s[1]; });
        var i_arr = elements.map(function(e) { return e[0]; });
        var j_arr = elements.map(function(e) { return e[1]; });
        var k_arr = elements.map(function(e) { return e[2]; });

        var traces = [{
            type: "triangulation",
            x: x, y: y,
            i: i_arr, j: j_arr, k: k_arr,
            mode: "lines",
            line: {color: "#6688cc", width: 0.5},
            hoverinfo: "skip"
        }];

        // Film boundary
        var shapes = [{
            type: "rect",
            x0: -w/2, y0: -h/2, x1: w/2, y1: h/2,
            line: {color: "black", width: 2},
            fillcolor: "rgba(204,229,255,0.3)"
        }];

        // Source electrode (red, left side)
        shapes.push({
            type: "rect",
            x0: -w/2 - ew/2, y0: ey - eh/2,
            x1: -w/2 + ew/2, y1: ey + eh/2,
            line: {color: "darkred", width: 1.5},
            fillcolor: "rgba(255,107,107,0.4)"
        });

        // Drain electrode (blue, right side)
        shapes.push({
            type: "rect",
            x0: w/2 - ew/2, y0: ey - eh/2,
            x1: w/2 + ew/2, y1: ey + eh/2,
            line: {color: "darkblue", width: 1.5},
            fillcolor: "rgba(77,171,247,0.4)"
        });

        // Annotations for electrodes
        var annotations = [
            {x: -w/2, y: ey + eh/2 + h*0.05, text: "Source", showarrow: false, font: {color: "darkred", size: 11}},
            {x: w/2, y: ey + eh/2 + h*0.05, text: "Drain", showarrow: false, font: {color: "darkblue", size: 11}}
        ];

        // Probe markers
        probes.forEach(function(p, idx) {
            var colors = ["#2b8a3e", "#e67700"];
            traces.push({
                type: "scatter", mode: "markers",
                x: [p[0]], y: [p[1]],
                marker: {color: colors[idx], size: 12, line: {color: "black", width: 1}},
                name: "Probe " + (idx+1),
                hovertemplate: "Probe " + (idx+1) + " (" + p[0] + ", " + p[1] + ")<extra></extra>"
            });
        });

        var layout = {
            xaxis: {title: "x", scaleanchor: "y", scaleratio: 1},
            yaxis: {title: "y"},
            shapes: shapes,
            annotations: annotations,
            showlegend: true,
            height: 500,
            margin: {l: 60, r: 30, t: 30, b: 50},
            paper_bgcolor: "white",
            plot_bgcolor: "white"
        };

        Plotly.newPlot("plotDiv", traces, layout, {responsive: true});

        document.getElementById("meshPlot").style.display = "block";
        document.getElementById("meshInfo").textContent =
            "(" + data.num_sites + " sites, " + data.num_elements + " elements)";
        status.textContent = "";
    } catch(err) {
        status.textContent = "Error: " + err.message;
    }
    btn.disabled = false;
});
</script>
{% endblock %}
```

Note: The "Next: Timing" button still does a regular form POST to save params to session and redirect. The "Build & Preview" button uses `fetch()` to the JSON API and renders with Plotly.js.

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_workflow/templates/base.html src/tdgl_workflow/templates/device.html
git commit -m "feat: replace matplotlib with Plotly.js interactive mesh preview with electrodes"
```

---

### Task 4: Switch timing page to Plotly.js with AJAX preview

**Files:**
- Modify: `src/tdgl_workflow/routes/timing.py`
- Modify: `src/tdgl_workflow/templates/timing.html`

**Context:** Same pattern as device page. Store only input params in session (remove schedule data). Preview uses JSON API + Plotly.js.

- [ ] **Step 1: Update timing.py to store only input params**

In `src/tdgl_workflow/routes/timing.py`, change the POST handler to store only params:

Replace:
```python
    timing_params = {k: v for k, v in params.items()}
    timing_params["schedule"] = {
        "steps": timing_data["steps"],
        "ramp_down_steps": timing_data["ramp_down_steps"],
        "solve_time": timing_data["solve_time"],
        "n_steps": timing_data["n_steps"],
    }
    request.session["timing_params"] = timing_params
```

With:
```python
    request.session["timing_params"] = params
```

Also remove the `plot_b64` rendering since we're using client-side Plotly now. The full updated POST handler:

```python
@router.post("/timing", response_class=HTMLResponse)
async def timing_preview(request: Request):
    form_data = await request.form()
    form = {k: v for k, v in form_data.items()}
    action = form.get("action", "preview")

    has_device = "device_params" in request.session
    if not has_device:
        return RedirectResponse("/device", status_code=303)

    params = {
        "je_initial": float(form.get("je_initial", 0)),
        "je_final": float(form.get("je_final", 5)),
        "je_step": float(form.get("je_step", 1)),
        "ramp_time": float(form.get("ramp_time", 0.5)),
        "stable_time": float(form.get("stable_time", 2)),
        "save_time": float(form.get("save_time", 1)),
        "ramp_down": "ramp_down" in form,
    }

    request.session["timing_params"] = params

    if action == "next":
        return RedirectResponse("/simulate", status_code=303)

    return _render_template("timing.html", {
        "request": request,
        "page": "timing",
        "form": form,
        "has_device": has_device,
    })
```

Also remove the `plot_b64` from the GET handler:

```python
@router.get("/timing", response_class=HTMLResponse)
def timing_page(request: Request):
    has_device = "device_params" in request.session
    return _render_template("timing.html", {
        "request": request,
        "page": "timing",
        "form": {},
        "has_device": has_device,
    })
```

And remove the imports of `build_timing` and `render_timing_plot` since they're no longer used in this file (they're used in the API route now):

```python
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
```

- [ ] **Step 2: Rewrite timing.html with Plotly.js**

Replace `src/tdgl_workflow/templates/timing.html` with:

```html
{% extends "base.html" %}
{% block title %}Timing Builder{% endblock %}
{% block content %}
<h1>Timing Builder</h1>
{% if not has_device %}
<p style="color:#dc2626;">Please <a href="/device">configure a device</a> first.</p>
{% else %}
<form id="timingForm" method="post">
    <div class="field-row">
        <div>
            <label>Je Initial</label>
            <input type="number" step="any" name="je_initial" id="je_initial" value="{{ form.get('je_initial', '0') }}">
        </div>
        <div>
            <label>Je Final</label>
            <input type="number" step="any" name="je_final" id="je_final" value="{{ form.get('je_final', '5') }}">
        </div>
        <div>
            <label>Je Step</label>
            <input type="number" step="any" name="je_step" id="je_step" value="{{ form.get('je_step', '1') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Ramp Time</label>
            <input type="number" step="any" name="ramp_time" id="ramp_time" value="{{ form.get('ramp_time', '0.5') }}">
        </div>
        <div>
            <label>Stable Time</label>
            <input type="number" step="any" name="stable_time" id="stable_time" value="{{ form.get('stable_time', '2') }}">
        </div>
        <div>
            <label>Save Time</label>
            <input type="number" step="any" name="save_time" id="save_time" value="{{ form.get('save_time', '1') }}">
        </div>
    </div>
    <div style="margin-bottom:1rem;">
        <label style="display:inline;font-weight:normal;">
            <input type="checkbox" name="ramp_down" id="ramp_down" {% if form.get('ramp_down') %}checked{% endif %}>
            Include ramp-down
        </label>
    </div>
    <button type="button" id="previewBtn">Preview Timing</button>
    <button type="submit" name="action" value="next" class="secondary">Next: Simulate</button>
    <span id="status" style="margin-left:1rem;color:#64748b;font-size:0.9rem;"></span>
</form>
<div id="timingPlot" class="preview" style="display:none;">
    <h2>Current Sweep <span id="timingInfo" style="font-weight:normal;color:#64748b;font-size:0.85rem;"></span></h2>
    <div id="plotDiv"></div>
</div>
<script>
document.getElementById("previewBtn").addEventListener("click", async function() {
    var btn = this;
    var status = document.getElementById("status");
    btn.disabled = true;
    status.textContent = "Building schedule...";

    var body = {
        je_initial: parseFloat(document.getElementById("je_initial").value),
        je_final: parseFloat(document.getElementById("je_final").value),
        je_step: parseFloat(document.getElementById("je_step").value),
        ramp_time: parseFloat(document.getElementById("ramp_time").value),
        stable_time: parseFloat(document.getElementById("stable_time").value),
        save_time: parseFloat(document.getElementById("save_time").value),
        ramp_down: document.getElementById("ramp_down").checked
    };

    try {
        var resp = await fetch("/api/preview/timing", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body)
        });
        if (!resp.ok) throw new Error("Server error: " + resp.status);
        var data = await resp.json();

        var xData = [];
        var yData = [];
        var shapes = [];

        data.steps.forEach(function(step) {
            // Ramp up
            xData.push(step.ramp_start, step.ramp_end, null);
            yData.push(step.je_start, step.je_end, null);
            // Stable
            xData.push(step.ramp_end, step.stable_end, null);
            yData.push(step.je_end, step.je_end, null);
            // Save region
            shapes.push({
                type: "rect",
                x0: step.save_start, y0: 0,
                x1: step.save_end, y1: step.je_end * 1.1,
                fillcolor: "rgba(34,197,94,0.1)",
                line: {width: 0}
            });
        });

        if (data.ramp_down_steps) {
            data.ramp_down_steps.forEach(function(step) {
                xData.push(step.ramp_start, step.ramp_end, null);
                yData.push(step.je_start, step.je_end, null);
            });
        }

        var traces = [{
            x: xData, y: yData,
            mode: "lines",
            line: {color: "#2563eb", width: 2},
            name: "Je sweep"
        }];

        var layout = {
            xaxis: {title: "Time", gridcolor: "#e2e8f0"},
            yaxis: {title: "Je", gridcolor: "#e2e8f0"},
            shapes: shapes,
            height: 350,
            margin: {l: 60, r: 30, t: 30, b: 50},
            paper_bgcolor: "white",
            plot_bgcolor: "white",
            showlegend: false
        };

        Plotly.newPlot("plotDiv", traces, layout, {responsive: true});

        document.getElementById("timingPlot").style.display = "block";
        document.getElementById("timingInfo").textContent =
            "(" + data.n_steps + " steps, solve_time=" + data.solve_time.toFixed(2) + ")";
        status.textContent = "";
    } catch(err) {
        status.textContent = "Error: " + err.message;
    }
    btn.disabled = false;
});
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_workflow/routes/timing.py src/tdgl_workflow/templates/timing.html
git commit -m "feat: replace matplotlib with Plotly.js interactive timing preview, store only params in session"
```

---

### Task 5: Fix simulate route to regenerate mesh from params

**Files:**
- Modify: `src/tdgl_workflow/routes/simulate.py`

**Context:** The simulate route currently reads `device_params["mesh"]` from session which no longer exists. It needs to call `build_rectangular_device()` to regenerate the mesh, and `build_timing()` to regenerate the schedule.

- [ ] **Step 1: Update simulate.py to regenerate mesh and timing on submission**

Add imports at the top of `src/tdgl_workflow/routes/simulate.py`:

```python
from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.timing import build_timing
```

In the `simulate_submit` function, replace the line:
```python
    num_sites = device_params["mesh"]["num_sites"]
```

With mesh regeneration:
```python
    mesh_data = build_rectangular_device(
        film_width=device_params["film_width"],
        film_height=device_params["film_height"],
        elec_width=device_params["elec_width"],
        elec_height=device_params["elec_height"],
        elec_y_offset=device_params["elec_y_offset"],
        probe_points=[tuple(p) for p in device_params["probe_points"]],
        max_edge_length=device_params["max_edge_length"],
        smooth=device_params["smooth"],
    )

    timing_data = build_timing(**timing_params)

    num_sites = mesh_data["num_sites"]

    # Reconstruct full device_params with mesh for data service storage
    full_device_params = dict(device_params)
    full_device_params["mesh"] = {
        "sites": mesh_data["sites"],
        "elements": mesh_data["elements"],
        "probe_indices": mesh_data["probe_indices"],
        "num_sites": mesh_data["num_sites"],
        "num_elements": mesh_data["num_elements"],
    }

    full_timing_params = dict(timing_params)
    full_timing_params["schedule"] = {
        "steps": timing_data["steps"],
        "ramp_down_steps": timing_data["ramp_down_steps"],
        "solve_time": timing_data["solve_time"],
        "n_steps": timing_data["n_steps"],
    }
```

Then update the `httpx.post` call to use `full_device_params` and `full_timing_params`:
```python
        create_resp = client.post(
            f"{settings.data_service_url}/api/runs",
            json={
                "solver_type": "cpp-tdgl",
                "grid_shape": [num_sites, 1],
                "device_params": full_device_params,
                "timing_params": full_timing_params,
                "metadata": {"solver_options": solver_options},
                "total_frames": timing_data["n_steps"],
            },
        )
```

And update the template context at the end to use full params:
```python
    return _render_template("simulate.html", {
        "request": request,
        "page": "simulate",
        "has_device": True,
        "has_timing": True,
        "device_params": full_device_params,
        "timing_params": full_timing_params,
        "submitted": True,
        "run_id": run_id,
        "viewer_url": "/tdgl/viewer",
        "runs": runs,
    })
```

- [ ] **Step 2: Commit**

```bash
git add src/tdgl_workflow/routes/simulate.py
git commit -m "fix: regenerate mesh and timing from params on workflow submission"
```

---

### Task 6: Remove plots.py and nginx buffer hack

**Files:**
- Delete: `src/tdgl_workflow/plots.py`
- Modify: `infra/nginx/configmap.yaml`

**Context:** plots.py is no longer used (all rendering is client-side). The nginx 256k buffer hack can be reverted since sessions no longer carry large data.

- [ ] **Step 1: Delete plots.py**

```bash
rm src/tdgl_workflow/plots.py
```

- [ ] **Step 2: Remove the large buffer settings from nginx configmap**

In `infra/nginx/configmap.yaml`, remove these three lines from the `/workflow/` location block:
```
            proxy_buffer_size 256k;
            proxy_buffers 8 256k;
            proxy_busy_buffers_size 256k;
```

And remove the `large_client_header_buffers` line from the server block:
```
        large_client_header_buffers 4 512k;
```

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_workflow/plots.py infra/nginx/configmap.yaml
git commit -m "chore: remove plots.py and nginx buffer hack (no longer needed)"
```

---

### Task 7: Build, deploy, and verify

**Files:** None (operational)

- [ ] **Step 1: Push all commits and wait for CI**

```bash
git push origin main
```

- [ ] **Step 2: After CI builds the image, patch the deployment with the new tag**

Get the SHA from the CI commit that includes the changes, update `services/tdgl-workflow/k8s/deployment.yaml` with the new SHA tag, push, and let ArgoCD sync.

- [ ] **Step 3: Verify in browser**

1. Open http://localhost:30080/workflow/device
2. Click "Build & Preview" - should see interactive Plotly mesh with:
   - Blue triangulation lines
   - Film boundary (black rectangle)
   - Source electrode (red rectangle, left side)
   - Drain electrode (blue rectangle, right side)
   - Probe markers (green and orange circles)
3. Zoom/pan the plot to verify interactivity
4. Click "Next: Timing" - should navigate to timing page
5. Click "Preview Timing" - should see interactive Plotly sweep plot
6. Click "Next: Simulate" - should show review page with params
7. Submit - should work without 502 errors
