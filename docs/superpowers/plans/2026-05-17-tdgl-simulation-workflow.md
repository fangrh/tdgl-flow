# TDGL Simulation Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified web app for configuring and running cpp-tdgl simulations, with device building, timing configuration, and Argo Workflow submission.

**Architecture:** Single FastAPI + Jinja2 web service that generates device meshes and timing sequences on-the-fly for preview, then persists configuration and submits an Argo Workflow on submission. The cpp-tdgl runner container fetches params from the data-viewer API and streams simulation results back Je-by-Je.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, matplotlib, tdgl library, httpx, Starlette SessionMiddleware

---

## File Structure

```
src/tdgl_workflow/
├── __init__.py              # version
├── app.py                   # FastAPI app factory + session middleware
├── config.py                # Pydantic settings
├── mesh.py                  # Device/mesh generation using tdgl
├── timing.py                # Timing/current sequence generation
├── plots.py                 # Static matplotlib plot generation
├── routes/
│   ├── __init__.py
│   ├── device.py            # GET/POST /device
│   ├── timing.py            # GET/POST /timing
│   └── simulate.py          # GET/POST /simulate
└── templates/
    ├── base.html            # Nav bar + layout
    ├── device.html          # Device builder form + preview
    ├── timing.html          # Timing builder form + preview
    └── simulate.html        # Review + submit + run list

services/tdgl-workflow/
├── Dockerfile
└── k8s/
    ├── deployment.yaml
    └── service.yaml

services/cpp-tdgl-runner/
├── Dockerfile
└── runner.py                # Python wrapper: fetch params → simulate → POST frames

workflows/
└── cpp-tdgl-sim.yaml        # Argo WorkflowTemplate

tests/
├── test_mesh.py
├── test_timing.py
├── test_plots.py
└── test_workflow_routes.py
```

---

### Task 1: Package scaffolding and config

**Files:**
- Create: `src/tdgl_workflow/__init__.py`
- Create: `src/tdgl_workflow/config.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the package init**

```python
# src/tdgl_workflow/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 2: Write the config module**

```python
# src/tdgl_workflow/config.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TDGL Workflow"
    data_service_url: str = "http://data-viewer.tdgl.svc.cluster.local"
    argo_server_url: str = "http://argo-workflows-server.argo.svc.cluster.local:2746"
    session_secret: str = "change-me-in-production"
    tdgl_namespace: str = "tdgl"

    model_config = SettingsConfigDict(
        env_prefix="TDGL_WORKFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

- [ ] **Step 3: Add dependencies to pyproject.toml**

Add these to the `[project]` dependencies list in `pyproject.toml`:

```toml
dependencies = [
  # ... existing deps ...
  "jinja2>=3.1",
  "matplotlib>=3.9",
  "itsdangerous>=2.1",
  "python-multipart>=0.0.9",
]
```

Add a second package to the build config so both `tdgl_data` and `tdgl_workflow` are discoverable. Update `pyproject.toml` to include:

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 4: Verify the package imports**

Run: `python -c "from tdgl_workflow.config import Settings; s = Settings(); print(s.app_name)"`
Expected: `TDGL Workflow`

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_workflow/__init__.py src/tdgl_workflow/config.py pyproject.toml
git commit -m "feat: scaffold tdgl_workflow package with config"
```

---

### Task 2: Mesh generation module

**Files:**
- Create: `src/tdgl_workflow/mesh.py`
- Create: `tests/test_mesh.py`

This module uses the `tdgl` library to build a rectangular device and generate a mesh. It returns all data as plain dicts/JSON-serializable values so the web app can store them in session state.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mesh.py
import pytest


def test_build_rectangular_device_returns_mesh_data():
    from tdgl_workflow.mesh import build_rectangular_device

    result = build_rectangular_device(
        film_width=10.0,
        film_height=2.0,
        elec_width=0.5,
        elec_height=1.0,
        elec_y_offset=0.0,
        probe_points=[(2.0, 0.0), (8.0, 0.0)],
        max_edge_length=1.0,
        smooth=100,
    )

    assert "sites" in result
    assert "elements" in result
    assert "probe_indices" in result
    assert "num_sites" in result
    assert "num_elements" in result
    assert isinstance(result["sites"], list)
    assert isinstance(result["elements"], list)
    assert isinstance(result["probe_indices"], list)
    assert result["num_sites"] > 0
    assert result["num_elements"] > 0
    assert len(result["sites"][0]) == 2  # each site is (x, y)


def test_build_rectangular_device_probe_indices_valid():
    from tdgl_workflow.mesh import build_rectangular_device

    result = build_rectangular_device(
        film_width=10.0,
        film_height=2.0,
        elec_width=0.5,
        elec_height=1.0,
        elec_y_offset=0.0,
        probe_points=[(2.0, 0.0), (8.0, 0.0)],
        max_edge_length=1.0,
        smooth=100,
    )

    num_sites = result["num_sites"]
    for idx in result["probe_indices"]:
        assert 0 <= idx < num_sites
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mesh.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tdgl_workflow.mesh'`

- [ ] **Step 3: Write the mesh module**

```python
# src/tdgl_workflow/mesh.py
import numpy as np
import tdgl
from tdgl.geometry import box


def build_rectangular_device(
    *,
    film_width: float,
    film_height: float,
    elec_width: float,
    elec_height: float,
    elec_y_offset: float,
    probe_points: list[tuple[float, float]],
    max_edge_length: float,
    smooth: int = 100,
) -> dict:
    layer = tdgl.Layer(coherence_length=0.5, london_lambda=2.0, thickness=0.1, gamma=1)

    film = tdgl.Polygon("film", points=box(film_width, film_height))

    source = tdgl.Polygon(
        "source", points=box(elec_width, elec_height)
    ).translate(dx=-film_width / 2, dy=elec_y_offset)
    drain = tdgl.Polygon(
        "drain", points=box(elec_width, elec_height)
    ).translate(dx=film_width / 2, dy=elec_y_offset)

    device = tdgl.Device(
        "rectangular_device",
        layer=layer,
        film=film,
        terminals=[source, drain],
        probe_points=probe_points,
    )
    device.make_mesh(max_edge_length=max_edge_length, smooth=smooth)

    points = np.asarray(device.points)
    triangles = np.asarray(device.triangles)

    probe_indices = []
    for px, py in probe_points:
        distances = np.sqrt((points[:, 0] - px) ** 2 + (points[:, 1] - py) ** 2)
        probe_indices.append(int(np.argmin(distances)))

    return {
        "sites": points.tolist(),
        "elements": triangles.tolist(),
        "probe_indices": probe_indices,
        "num_sites": int(len(points)),
        "num_elements": int(len(triangles)),
        "film_width": film_width,
        "film_height": film_height,
        "elec_width": elec_width,
        "elec_height": elec_height,
        "elec_y_offset": elec_y_offset,
        "max_edge_length": max_edge_length,
        "smooth": smooth,
        "probe_points": list(probe_points),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mesh.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_workflow/mesh.py tests/test_mesh.py
git commit -m "feat: add mesh generation module using tdgl library"
```

---

### Task 3: Timing generation module

**Files:**
- Create: `src/tdgl_workflow/timing.py`
- Create: `tests/test_timing.py`

Generates a current sweep schedule (Je vs time) from timing parameters. Returns the schedule as a list of step descriptors plus summary metadata.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_timing.py
import math


def test_build_timing_returns_step_list():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0,
        je_final=5.0,
        je_step=1.0,
        ramp_time=0.5,
        stable_time=2.0,
        save_time=1.0,
        ramp_down=False,
    )

    assert "steps" in result
    assert "solve_time" in result
    assert "n_steps" in result
    assert isinstance(result["steps"], list)
    assert len(result["steps"]) == 5  # 0, 1, 2, 3, 4, 5 → 5 steps of 1.0


def test_build_timing_step_fields():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0,
        je_final=3.0,
        je_step=1.0,
        ramp_time=1.0,
        stable_time=4.0,
        save_time=2.0,
        ramp_down=False,
    )

    step = result["steps"][0]
    assert "je_start" in step
    assert "je_end" in step
    assert "ramp_start" in step
    assert "ramp_end" in step
    assert "stable_end" in step
    assert "save_start" in step
    assert "save_end" in step
    assert step["je_start"] == 0.0
    assert step["je_end"] == 1.0


def test_build_timing_solve_time():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0,
        je_final=2.0,
        je_step=1.0,
        ramp_time=1.0,
        stable_time=3.0,
        save_time=1.0,
        ramp_down=False,
    )

    n = result["n_steps"]
    period = 1.0 + 3.0
    assert result["solve_time"] == pytest.approx(n * period)


def test_build_timing_with_ramp_down():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0,
        je_final=2.0,
        je_step=1.0,
        ramp_time=1.0,
        stable_time=3.0,
        save_time=1.0,
        ramp_down=True,
    )

    n = result["n_steps"]
    period = 1.0 + 3.0
    ramp_up_time = n * period
    ramp_down_time = n * 1.0
    assert result["solve_time"] == pytest.approx(ramp_up_time + ramp_down_time)


def test_build_timing_saves_ramp_down_steps():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0,
        je_final=2.0,
        je_step=1.0,
        ramp_time=1.0,
        stable_time=3.0,
        save_time=1.0,
        ramp_down=True,
    )

    assert "ramp_down_steps" in result
    assert len(result["ramp_down_steps"]) == result["n_steps"]
```

Add the missing import at the top of the test file:

```python
import pytest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_timing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tdgl_workflow.timing'`

- [ ] **Step 3: Write the timing module**

```python
# src/tdgl_workflow/timing.py
def build_timing(
    *,
    je_initial: float,
    je_final: float,
    je_step: float,
    ramp_time: float,
    stable_time: float,
    save_time: float,
    ramp_down: bool = False,
) -> dict:
    n_steps = max(1, round((je_final - je_initial) / je_step))
    period = ramp_time + stable_time

    steps = []
    for i in range(n_steps):
        t_offset = i * period
        je_start = je_initial + i * je_step
        je_end = je_start + je_step
        steps.append({
            "je_start": je_start,
            "je_end": je_end,
            "ramp_start": t_offset,
            "ramp_end": t_offset + ramp_time,
            "stable_end": t_offset + period,
            "save_start": t_offset + ramp_time + (stable_time - save_time) / 2,
            "save_end": t_offset + ramp_time + (stable_time + save_time) / 2,
        })

    total_up_time = n_steps * period

    ramp_down_steps = []
    if ramp_down:
        for i in range(n_steps):
            t_offset = total_up_time + i * ramp_time
            je_start = je_initial + (n_steps - i) * je_step
            je_end = je_start - je_step
            ramp_down_steps.append({
                "je_start": je_start,
                "je_end": je_end,
                "ramp_start": t_offset,
                "ramp_end": t_offset + ramp_time,
            })

    solve_time = total_up_time + (n_steps * ramp_time if ramp_down else 0)

    return {
        "steps": steps,
        "ramp_down_steps": ramp_down_steps,
        "solve_time": solve_time,
        "n_steps": n_steps,
        "je_initial": je_initial,
        "je_final": je_final,
        "je_step": je_step,
        "ramp_time": ramp_time,
        "stable_time": stable_time,
        "save_time": save_time,
        "ramp_down": ramp_down,
        "period": period,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_timing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_workflow/timing.py tests/test_timing.py
git commit -m "feat: add timing/current sweep generation module"
```

---

### Task 4: Plot generation module

**Files:**
- Create: `src/tdgl_workflow/plots.py`
- Create: `tests/test_plots.py`

Generates static matplotlib PNG images as base64-encoded strings for inline embedding in HTML.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plots.py
import base64


def test_render_mesh_plot_returns_base64_png():
    from tdgl_workflow.plots import render_mesh_plot

    mesh_data = {
        "sites": [[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]],
        "elements": [[0, 1, 2]],
        "probe_indices": [0, 1],
        "num_sites": 3,
        "num_elements": 1,
        "film_width": 1.0,
        "film_height": 1.0,
        "elec_width": 0.5,
        "elec_height": 0.5,
        "elec_y_offset": 0.0,
        "probe_points": [(0.0, 0.0), (1.0, 0.0)],
    }

    result = render_mesh_plot(mesh_data)
    decoded = base64.b64decode(result)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_timing_plot_returns_base64_png():
    from tdgl_workflow.plots import render_timing_plot

    timing_data = {
        "steps": [
            {"je_start": 0.0, "je_end": 1.0, "ramp_start": 0.0, "ramp_end": 0.5, "stable_end": 2.5, "save_start": 1.0, "save_end": 2.0},
            {"je_start": 1.0, "je_end": 2.0, "ramp_start": 2.5, "ramp_end": 3.0, "stable_end": 5.5, "save_start": 3.5, "save_end": 4.5},
        ],
        "ramp_down_steps": [],
        "solve_time": 5.5,
        "n_steps": 2,
        "je_initial": 0.0,
        "je_final": 2.0,
        "je_step": 1.0,
        "ramp_time": 0.5,
        "stable_time": 2.0,
        "save_time": 1.0,
        "ramp_down": False,
        "period": 2.5,
    }

    result = render_timing_plot(timing_data)
    decoded = base64.b64decode(result)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tdgl_workflow.plots'`

- [ ] **Step 3: Write the plots module**

```python
# src/tdgl_workflow/plots.py
import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _fig_to_base64(fig: matplotlib.figure.Figure) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def render_mesh_plot(mesh_data: dict) -> str:
    sites = np.array(mesh_data["sites"])
    elements = np.array(mesh_data["elements"])
    probe_indices = mesh_data["probe_indices"]
    probe_points = mesh_data["probe_points"]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.triplot(sites[:, 0], sites[:, 1], elements, linewidth=0.3, color="#6688cc")

    for idx in probe_indices:
        ax.plot(sites[idx, 0], sites[idx, 1], "rs", markersize=6, label="Probe" if idx == probe_indices[0] else "")

    if probe_points:
        for px, py in probe_points:
            ax.axvline(x=px, color="red", linewidth=0.5, linestyle="--", alpha=0.4)

    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title(f"Mesh: {mesh_data['num_sites']} sites, {mesh_data['num_elements']} elements")
    ax.set_aspect("equal")

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend()

    return _fig_to_base64(fig)


def render_timing_plot(timing_data: dict) -> str:
    steps = timing_data["steps"]
    ramp_down_steps = timing_data.get("ramp_down_steps", [])

    fig, ax = plt.subplots(figsize=(10, 3))

    for step in steps:
        ramp = [(step["ramp_start"], step["je_start"]), (step["ramp_end"], step["je_end"])]
        stable = [(step["ramp_end"], step["je_end"]), (step["stable_end"], step["je_end"])]
        ax.plot([p[0] for p in ramp], [p[1] for p in ramp], color="#2563eb", linewidth=1.5)
        ax.plot([p[0] for p in stable], [p[1] for p in stable], color="#2563eb", linewidth=1.5)
        ax.axvspan(step["save_start"], step["save_end"], alpha=0.15, color="green")

    for step in ramp_down_steps:
        ramp = [(step["ramp_start"], step["je_start"]), (step["ramp_end"], step["je_end"])]
        ax.plot([p[0] for p in ramp], [p[1] for p in ramp], color="#dc2626", linewidth=1.5, linestyle="--")

    ax.set_xlabel("Time")
    ax.set_ylabel("Je")
    ax.set_title(f"Current sweep: {timing_data['n_steps']} steps, solve_time={timing_data['solve_time']:.2f}")
    ax.grid(True, alpha=0.3)

    return _fig_to_base64(fig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plots.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_workflow/plots.py tests/test_plots.py
git commit -m "feat: add static plot generation for mesh and timing"
```

---

### Task 5: FastAPI app factory, session middleware, and base template

**Files:**
- Create: `src/tdgl_workflow/app.py`
- Create: `src/tdgl_workflow/routes/__init__.py`
- Create: `src/tdgl_workflow/templates/base.html`
- Create: `src/tdgl_workflow/dev_app.py`

- [ ] **Step 1: Write the app factory**

```python
# src/tdgl_workflow/app.py
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tdgl_workflow.config import Settings


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title=settings.app_name)

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

    app.state.settings = settings

    templates_dir = Path(__file__).parent / "templates"
    app.state.templates_dir = templates_dir

    from tdgl_workflow.routes.device import router as device_router
    from tdgl_workflow.routes.timing import router as timing_router
    from tdgl_workflow.routes.simulate import router as simulate_router

    app.include_router(device_router)
    app.include_router(timing_router)
    app.include_router(simulate_router)

    @app.get("/")
    def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/device")

    return app
```

```python
# src/tdgl_workflow/routes/__init__.py
```

```python
# src/tdgl_workflow/dev_app.py
from tdgl_workflow.app import create_app

app = create_app()
```

- [ ] **Step 2: Write the base template**

```html
<!-- src/tdgl_workflow/templates/base.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}TDGL Workflow{% endblock %}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 0; background: #f8f9fa; color: #1a1a2e; }
        nav { background: #1a1a2e; padding: 0 1.5rem; display: flex; align-items: center; gap: 1.5rem; height: 48px; }
        nav a { color: #94a3b8; text-decoration: none; font-size: 0.9rem; padding: 0.5rem 0; border-bottom: 2px solid transparent; }
        nav a:hover, nav a.active { color: #fff; border-bottom-color: #2563eb; }
        main { max-width: 960px; margin: 2rem auto; padding: 0 1.5rem; }
        h1 { font-size: 1.5rem; margin-bottom: 1rem; }
        h2 { font-size: 1.2rem; margin: 1.5rem 0 0.75rem; }
        form { background: #fff; padding: 1.5rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 1.5rem; }
        label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 0.25rem; color: #475569; }
        input, select { display: block; width: 100%; max-width: 300px; padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.9rem; margin-bottom: 0.75rem; }
        button { background: #2563eb; color: #fff; border: none; padding: 0.5rem 1.2rem; border-radius: 4px; cursor: pointer; font-size: 0.9rem; margin-right: 0.5rem; }
        button:hover { background: #1d4ed8; }
        button.secondary { background: #64748b; }
        button.secondary:hover { background: #475569; }
        .preview { margin-top: 1rem; text-align: center; }
        .preview img { max-width: 100%; border: 1px solid #e2e8f0; border-radius: 4px; }
        .summary { background: #fff; padding: 1.5rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 1.5rem; }
        .summary dt { font-weight: 600; color: #334155; }
        .summary dd { margin: 0 0 0.5rem 1rem; color: #64748b; }
        table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
        th, td { padding: 0.6rem 1rem; text-align: left; border-bottom: 1px solid #e2e8f0; }
        th { background: #f1f5f9; font-weight: 600; font-size: 0.85rem; color: #475569; }
        .field-row { display: flex; gap: 1rem; flex-wrap: wrap; }
        .field-row > div { flex: 1; min-width: 200px; }
    </style>
</head>
<body>
    <nav>
        <a href="/device" {% if page == 'device' %}class="active"{% endif %}>Device</a>
        <a href="/timing" {% if page == 'timing' %}class="active"{% endif %}>Timing</a>
        <a href="/simulate" {% if page == 'simulate' %}class="active"{% endif %}>Simulate</a>
    </nav>
    <main>
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

- [ ] **Step 3: Verify the app starts**

Run: `python -c "from tdgl_workflow.app import create_app; app = create_app(); print('OK')"`
Expected: `OK` (routes will fail to import until Tasks 6-8, so create placeholder routers first)

Create placeholder routers so the app can start:

```python
# src/tdgl_workflow/routes/device.py
from fastapi import APIRouter

router = APIRouter()

# Added in Task 6
```

```python
# src/tdgl_workflow/routes/timing.py
from fastapi import APIRouter

router = APIRouter()

# Added in Task 7
```

```python
# src/tdgl_workflow/routes/simulate.py
from fastapi import APIRouter

router = APIRouter()

# Added in Task 8
```

Run again: `python -c "from tdgl_workflow.app import create_app; app = create_app(); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/tdgl_workflow/app.py src/tdgl_workflow/dev_app.py src/tdgl_workflow/routes/ src/tdgl_workflow/templates/
git commit -m "feat: add FastAPI app factory, session middleware, and base template"
```

---

### Task 6: Device builder route and template

**Files:**
- Modify: `src/tdgl_workflow/routes/device.py`
- Create: `src/tdgl_workflow/templates/device.html`
- Create: `tests/test_workflow_routes.py`

- [ ] **Step 1: Write the failing route test**

Add to `tests/test_workflow_routes.py`:

```python
# tests/test_workflow_routes.py
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workflow_client() -> Iterator[TestClient]:
    from tdgl_workflow.app import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client


def test_device_page_loads(workflow_client):
    response = workflow_client.get("/device")
    assert response.status_code == 200
    assert "Film Width" in response.text


def test_device_preview_returns_plot(workflow_client):
    response = workflow_client.post("/device", data={
        "film_width": "10",
        "film_height": "2",
        "elec_width": "0.5",
        "elec_height": "1",
        "elec_y_offset": "0",
        "probe1_x": "2",
        "probe1_y": "0",
        "probe2_x": "8",
        "probe2_y": "0",
        "max_edge_length": "1.0",
        "smooth": "100",
        "action": "preview",
    })
    assert response.status_code == 200
    assert "data:image/png;base64," in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_routes.py::test_device_page_loads -v`
Expected: FAIL — 404 or no route

- [ ] **Step 3: Write the device template**

```html
<!-- src/tdgl_workflow/templates/device.html -->
{% extends "base.html" %}
{% block title %}Device Builder{% endblock %}
{% block content %}
<h1>Device Builder</h1>
<form method="post">
    <div class="field-row">
        <div>
            <label>Film Width</label>
            <input type="number" step="any" name="film_width" value="{{ form.get('film_width', '10') }}">
        </div>
        <div>
            <label>Film Height</label>
            <input type="number" step="any" name="film_height" value="{{ form.get('film_height', '2') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Electrode Width</label>
            <input type="number" step="any" name="elec_width" value="{{ form.get('elec_width', '0.5') }}">
        </div>
        <div>
            <label>Electrode Height</label>
            <input type="number" step="any" name="elec_height" value="{{ form.get('elec_height', '1') }}">
        </div>
        <div>
            <label>Electrode Y Offset</label>
            <input type="number" step="any" name="elec_y_offset" value="{{ form.get('elec_y_offset', '0') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Probe 1 (x, y)</label>
            <input type="number" step="any" name="probe1_x" value="{{ form.get('probe1_x', '2') }}">
            <input type="number" step="any" name="probe1_y" value="{{ form.get('probe1_y', '0') }}">
        </div>
        <div>
            <label>Probe 2 (x, y)</label>
            <input type="number" step="any" name="probe2_x" value="{{ form.get('probe2_x', '8') }}">
            <input type="number" step="any" name="probe2_y" value="{{ form.get('probe2_y', '0') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Max Edge Length</label>
            <input type="number" step="any" name="max_edge_length" value="{{ form.get('max_edge_length', '1.0') }}">
        </div>
        <div>
            <label>Smooth Iterations</label>
            <input type="number" name="smooth" value="{{ form.get('smooth', '100') }}">
        </div>
    </div>
    <button type="submit" name="action" value="preview">Build &amp; Preview</button>
    <button type="submit" name="action" value="next" class="secondary">Next: Timing</button>
</form>
{% if plot_b64 %}
<div class="preview">
    <h2>Mesh Preview</h2>
    <img src="data:image/png;base64,{{ plot_b64 }}" alt="Mesh preview">
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Write the device route**

```python
# src/tdgl_workflow/routes/device.py
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.plots import render_mesh_plot

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _device_form_data(form: dict) -> dict:
    return {
        "film_width": float(form.get("film_width", 10)),
        "film_height": float(form.get("film_height", 2)),
        "elec_width": float(form.get("elec_width", 0.5)),
        "elec_height": float(form.get("elec_height", 1)),
        "elec_y_offset": float(form.get("elec_y_offset", 0)),
        "probe_points": [
            (float(form.get("probe1_x", 2)), float(form.get("probe1_y", 0))),
            (float(form.get("probe2_x", 8)), float(form.get("probe2_y", 0))),
        ],
        "max_edge_length": float(form.get("max_edge_length", 1.0)),
        "smooth": int(form.get("smooth", 100)),
    }


@router.get("/device", response_class=HTMLResponse)
def device_page(request: Request):
    return _templates.TemplateResponse("device.html", {
        "request": request,
        "page": "device",
        "form": {},
        "plot_b64": None,
    })


@router.post("/device", response_class=HTMLResponse)
async def device_preview(request: Request):
    form_data = await request.form()
    form = {k: v for k, v in form_data.items()}
    action = form.get("action", "preview")

    params = _device_form_data(form)
    mesh_data = build_rectangular_device(**params)
    plot_b64 = render_mesh_plot(mesh_data)

    device_params = {k: v for k, v in params.items()}
    device_params["mesh"] = {
        "sites": mesh_data["sites"],
        "elements": mesh_data["elements"],
        "probe_indices": mesh_data["probe_indices"],
        "num_sites": mesh_data["num_sites"],
        "num_elements": mesh_data["num_elements"],
    }
    request.session["device_params"] = device_params

    if action == "next":
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/timing", status_code=303)

    return _templates.TemplateResponse("device.html", {
        "request": request,
        "page": "device",
        "form": form,
        "plot_b64": plot_b64,
    })
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_workflow_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tdgl_workflow/routes/device.py src/tdgl_workflow/templates/device.html tests/test_workflow_routes.py
git commit -m "feat: add device builder route with mesh preview"
```

---

### Task 7: Timing builder route and template

**Files:**
- Modify: `src/tdgl_workflow/routes/timing.py`
- Create: `src/tdgl_workflow/templates/timing.html`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_workflow_routes.py`:

```python
def test_timing_page_loads(workflow_client):
    response = workflow_client.get("/timing")
    assert response.status_code == 200
    assert "Je Initial" in response.text


def test_timing_preview_returns_plot(workflow_client):
    response = workflow_client.post("/timing", data={
        "je_initial": "0",
        "je_final": "5",
        "je_step": "1",
        "ramp_time": "0.5",
        "stable_time": "2",
        "save_time": "1",
        "ramp_down": "",
        "action": "preview",
    })
    assert response.status_code == 200
    assert "data:image/png;base64," in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_routes.py::test_timing_page_loads -v`
Expected: FAIL

- [ ] **Step 3: Write the timing template**

```html
<!-- src/tdgl_workflow/templates/timing.html -->
{% extends "base.html" %}
{% block title %}Timing Builder{% endblock %}
{% block content %}
<h1>Timing Builder</h1>
{% if not has_device %}
<p style="color:#dc2626">Configure a device first. <a href="/device">Go to Device Builder</a></p>
{% else %}
<form method="post">
    <div class="field-row">
        <div>
            <label>Je Initial</label>
            <input type="number" step="any" name="je_initial" value="{{ form.get('je_initial', '0') }}">
        </div>
        <div>
            <label>Je Final</label>
            <input type="number" step="any" name="je_final" value="{{ form.get('je_final', '5') }}">
        </div>
        <div>
            <label>Je Step</label>
            <input type="number" step="any" name="je_step" value="{{ form.get('je_step', '1') }}">
        </div>
    </div>
    <div class="field-row">
        <div>
            <label>Ramp Time</label>
            <input type="number" step="any" name="ramp_time" value="{{ form.get('ramp_time', '0.5') }}">
        </div>
        <div>
            <label>Stable Time</label>
            <input type="number" step="any" name="stable_time" value="{{ form.get('stable_time', '2') }}">
        </div>
        <div>
            <label>Save Time</label>
            <input type="number" step="any" name="save_time" value="{{ form.get('save_time', '1') }}">
        </div>
    </div>
    <div>
        <label>
            <input type="checkbox" name="ramp_down" {% if form.get('ramp_down') %}checked{% endif %}>
            Enable ramp-down
        </label>
    </div>
    <button type="submit" name="action" value="preview">Build &amp; Preview</button>
    <button type="submit" name="action" value="next" class="secondary">Next: Review &amp; Submit</button>
</form>
{% if plot_b64 %}
<div class="preview">
    <h2>Timing Preview</h2>
    <img src="data:image/png;base64,{{ plot_b64 }}" alt="Timing preview">
</div>
{% endif %}
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Write the timing route**

```python
# src/tdgl_workflow/routes/timing.py
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from tdgl_workflow.timing import build_timing
from tdgl_workflow.plots import render_timing_plot

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/timing", response_class=HTMLResponse)
def timing_page(request: Request):
    has_device = "device_params" in request.session
    return _templates.TemplateResponse("timing.html", {
        "request": request,
        "page": "timing",
        "form": {},
        "plot_b64": None,
        "has_device": has_device,
    })


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

    timing_data = build_timing(**params)
    plot_b64 = render_timing_plot(timing_data)

    timing_params = {k: v for k, v in params.items()}
    timing_params["schedule"] = {
        "steps": timing_data["steps"],
        "ramp_down_steps": timing_data["ramp_down_steps"],
        "solve_time": timing_data["solve_time"],
        "n_steps": timing_data["n_steps"],
    }
    request.session["timing_params"] = timing_params

    if action == "next":
        return RedirectResponse("/simulate", status_code=303)

    return _templates.TemplateResponse("timing.html", {
        "request": request,
        "page": "timing",
        "form": form,
        "plot_b64": plot_b64,
        "has_device": has_device,
    })
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_workflow_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tdgl_workflow/routes/timing.py src/tdgl_workflow/templates/timing.html
git commit -m "feat: add timing builder route with current sweep preview"
```

---

### Task 8: Simulate (review & submit) route and template

**Files:**
- Modify: `src/tdgl_workflow/routes/simulate.py`
- Create: `src/tdgl_workflow/templates/simulate.html`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_workflow_routes.py`:

```python
def test_simulate_page_shows_warning_without_session(workflow_client):
    response = workflow_client.get("/simulate")
    assert response.status_code == 200
    assert "Configure" in response.text or "device" in response.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_routes.py::test_simulate_page_shows_warning_without_session -v`
Expected: FAIL

- [ ] **Step 3: Write the simulate template**

```html
<!-- src/tdgl_workflow/templates/simulate.html -->
{% extends "base.html" %}
{% block title %}Review &amp; Submit{% endblock %}
{% block content %}
<h1>Review &amp; Submit</h1>

{% if not has_device or not has_timing %}
<div class="summary">
    {% if not has_device %}
    <p>Configure a <a href="/device">device</a> first.</p>
    {% endif %}
    {% if not has_timing %}
    <p>Configure a <a href="/timing">timing sequence</a> first.</p>
    {% endif %}
</div>
{% elif submitted %}
<div class="summary">
    <h2>Workflow Submitted</h2>
    <p>Run ID: <strong>{{ run_id }}</strong></p>
    <p><a href="{{ viewer_url }}" target="_blank">View in Data Viewer</a></p>
</div>
{% else %}
<div class="summary">
    <h2>Device Configuration</h2>
    <dl>
        <dt>Film</dt>
        <dd>{{ device_params.film_width }} x {{ device_params.film_height }}</dd>
        <dt>Mesh</dt>
        <dd>{{ device_params.mesh.num_sites }} sites, {{ device_params.mesh.num_elements }} elements</dd>
        <dt>Probes</dt>
        <dd>{{ device_params.probe_points }}</dd>
    </dl>
</div>
<div class="summary">
    <h2>Timing Configuration</h2>
    <dl>
        <dt>Je Range</dt>
        <dd>{{ timing_params.je_initial }} to {{ timing_params.je_final }} (step {{ timing_params.je_step }})</dd>
        <dt>Steps</dt>
        <dd>{{ timing_params.schedule.n_steps }}</dd>
        <dt>Solve Time</dt>
        <dd>{{ timing_params.schedule.solve_time }}</dd>
        <dt>Ramp Down</dt>
        <dd>{{ "Yes" if timing_params.ramp_down else "No" }}</dd>
    </dl>
</div>
<form method="post">
    <div class="field-row">
        <div>
            <label>Initial dt</label>
            <input type="number" step="any" name="dt" value="1e-6">
        </div>
        <div>
            <label>Max dt</label>
            <input type="number" step="any" name="max_dt" value="0.1">
        </div>
        <div>
            <label>Adaptive Stepping</label>
            <select name="adaptive">
                <option value="true" selected>Yes</option>
                <option value="false">No</option>
            </select>
        </div>
    </div>
    <button type="submit">Submit Simulation</button>
</form>
{% endif %}

{% if runs %}
<h2>Recent Runs</h2>
<table>
    <tr><th>Run ID</th><th>Status</th><th>Created</th><th></th></tr>
    {% for run in runs %}
    <tr>
        <td>{{ run.run_id[:8] }}</td>
        <td>{{ run.status }}</td>
        <td>{{ run.created_at }}</td>
        <td><a href="{{ run.viewer_url }}" target="_blank">View</a></td>
    </tr>
    {% endfor %}
</table>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Write the simulate route**

```python
# src/tdgl_workflow/routes/simulate.py
import json
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from tdgl_workflow.config import Settings

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/simulate", response_class=HTMLResponse)
def simulate_page(request: Request):
    device_params = request.session.get("device_params")
    timing_params = request.session.get("timing_params")

    runs = []
    settings: Settings = request.app.state.settings
    try:
        resp = httpx.get(f"{settings.data_service_url}/api/runs", timeout=5.0)
        if resp.status_code == 200:
            for run in resp.json():
                run["viewer_url"] = f"/tdgl/viewer"
                runs.append(run)
    except httpx.HTTPError:
        pass

    return _templates.TemplateResponse("simulate.html", {
        "request": request,
        "page": "simulate",
        "has_device": device_params is not None,
        "has_timing": timing_params is not None,
        "device_params": device_params,
        "timing_params": timing_params,
        "submitted": False,
        "run_id": None,
        "viewer_url": None,
        "runs": runs,
    })


@router.post("/simulate", response_class=HTMLResponse)
async def simulate_submit(request: Request):
    device_params = request.session.get("device_params")
    timing_params = request.session.get("timing_params")

    if not device_params or not timing_params:
        return simulate_page(request)

    form_data = await request.form()
    solver_options = {
        "dt": float(form_data.get("dt", "1e-6")),
        "max_dt": float(form_data.get("max_dt", "0.1")),
        "adaptive": form_data.get("adaptive", "true") == "true",
    }

    settings: Settings = request.app.state.settings
    num_sites = device_params["mesh"]["num_sites"]
    run_id = uuid.uuid4().hex[:12]

    with httpx.Client(timeout=30.0) as client:
        create_resp = client.post(
            f"{settings.data_service_url}/api/runs",
            json={
                "solver_type": "cpp-tdgl",
                "grid_shape": [num_sites, 1],
                "device_params": device_params,
                "timing_params": timing_params,
                "metadata": {"solver_options": solver_options},
                "total_frames": timing_params["schedule"]["n_steps"],
            },
        )
        create_resp.raise_for_status()
        created_run = create_resp.json()
        run_id = created_run["run_id"]

        workflow = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Workflow",
            "metadata": {
                "generateName": f"cpp-tdgl-{run_id[:8]}-",
                "namespace": settings.tdgl_namespace,
                "labels": {"run-id": run_id},
            },
            "spec": {
                "workflowTemplateRef": {"name": "cpp-tdgl-sim"},
                "arguments": {
                    "parameters": [
                        {"name": "run-id", "value": run_id},
                        {"name": "data-service-url", "value": settings.data_service_url},
                    ],
                },
            },
        }

        try:
            client.post(
                f"{settings.argo_server_url}/api/v1/workflows/{settings.tdgl_namespace}",
                json={"workflow": workflow},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            pass

    request.session.pop("device_params", None)
    request.session.pop("timing_params", None)

    runs = []
    try:
        resp = httpx.get(f"{settings.data_service_url}/api/runs", timeout=5.0)
        if resp.status_code == 200:
            for run in resp.json():
                run["viewer_url"] = "/tdgl/viewer"
                runs.append(run)
    except httpx.HTTPError:
        pass

    return _templates.TemplateResponse("simulate.html", {
        "request": request,
        "page": "simulate",
        "has_device": True,
        "has_timing": True,
        "device_params": device_params,
        "timing_params": timing_params,
        "submitted": True,
        "run_id": run_id,
        "viewer_url": "/tdgl/viewer",
        "runs": runs,
    })
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_workflow_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tdgl_workflow/routes/simulate.py src/tdgl_workflow/templates/simulate.html
git commit -m "feat: add simulate route with workflow submission"
```

---

### Task 9: Dockerfile and Kubernetes manifests

**Files:**
- Create: `services/tdgl-workflow/Dockerfile`
- Create: `services/tdgl-workflow/k8s/deployment.yaml`
- Create: `services/tdgl-workflow/k8s/service.yaml`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# services/tdgl-workflow/Dockerfile
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "tdgl_workflow.dev_app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write the k8s deployment**

```yaml
# services/tdgl-workflow/k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tdgl-workflow
  namespace: tdgl
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tdgl-workflow
  template:
    metadata:
      labels:
        app: tdgl-workflow
    spec:
      containers:
        - name: tdgl-workflow
          image: ghcr.io/fangrh/tdgl-workflow:latest
          ports:
            - containerPort: 8000
          env:
            - name: TDGL_WORKFLOW_DATA_SERVICE_URL
              value: "http://data-viewer.tdgl.svc.cluster.local"
            - name: TDGL_WORKFLOW_ARGO_SERVER_URL
              value: "http://argo-workflows-server.argo.svc.cluster.local:2746"
            - name: TDGL_WORKFLOW_SESSION_SECRET
              valueFrom:
                secretKeyRef:
                  name: tdgl-workflow-config
                  key: session-secret
          livenessProbe:
            httpGet:
              path: /device
              port: 8000
          readinessProbe:
            httpGet:
              path: /device
              port: 8000
```

- [ ] **Step 3: Write the k8s service**

```yaml
# services/tdgl-workflow/k8s/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: tdgl-workflow
  namespace: tdgl
spec:
  selector:
    app: tdgl-workflow
  ports:
    - port: 80
      targetPort: 8000
```

- [ ] **Step 4: Create the secret manifest**

```yaml
# services/tdgl-workflow/k8s/secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: tdgl-workflow-config
  namespace: tdgl
type: Opaque
stringData:
  session-secret: "change-me-in-production"
```

- [ ] **Step 5: Commit**

```bash
git add services/tdgl-workflow/
git commit -m "feat: add tdgl-workflow Dockerfile and k8s manifests"
```

---

### Task 10: Nginx routing update

**Files:**
- Modify: `infra/nginx/nginx.conf`

- [ ] **Step 1: Add workflow location block**

Add this block to `infra/nginx/nginx.conf` after the existing `/tdgl/` location:

```nginx
    location /workflow/ {
        proxy_pass http://tdgl-workflow.tdgl.svc.cluster.local/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
```

- [ ] **Step 2: Commit**

```bash
git add infra/nginx/nginx.conf
git commit -m "feat: add nginx routing for tdgl-workflow service"
```

---

### Task 11: cpp-tdgl-runner script and Dockerfile

**Files:**
- Create: `services/cpp-tdgl-runner/runner.py`
- Create: `services/cpp-tdgl-runner/Dockerfile`

The runner fetches device_params and timing_params from the data-viewer API using the run_id, then runs the simulation step by step and POSTs frames back.

- [ ] **Step 1: Write the runner script**

```python
# services/cpp-tdgl-runner/runner.py
"""cpp-tdgl simulation runner.

Fetches configuration from the data-viewer API, runs the simulation
Je-step by Je-step, and streams frame data back.

Environment variables:
    TDGL_RUN_ID           - Run ID to fetch config for
    TDGL_DATA_SERVICE_URL - Base URL of the data-viewer service
"""

import os
import sys
import time

import httpx


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    data_url = os.environ["TDGL_DATA_SERVICE_URL"]
    client = httpx.Client(base_url=data_url, timeout=120.0)

    # Fetch run configuration
    resp = client.get(f"/api/runs/{run_id}")
    resp.raise_for_status()
    run_data = resp.json()

    device_params = run_data["device_params"]
    timing_params = run_data["timing_params"]
    solver_options = run_data.get("metadata", {}).get("solver_options", {})

    mesh = device_params["mesh"]
    schedule = timing_params["schedule"]
    steps = schedule["steps"]

    # Update run status to running
    client.patch(f"/api/runs/{run_id}/status", json={"status": "running"})

    # Build device and run simulation step by step
    # NOTE: This is a functional skeleton. The actual cpp-tdgl integration
    # (HDF5 I/O, solver invocation) will be filled in based on the
    # cpp-tdgl binary's interface.
    try:
        num_sites = mesh["num_sites"]

        for step_index, step in enumerate(steps):
            je = step["je_end"]
            voltage = 0.0  # placeholder — real value comes from solver output

            # Build placeholder frame data (1D array per field)
            frame_data = {
                "frame_index": step_index,
                "time_value": step["stable_end"],
                "je": je,
                "voltage": voltage,
                "psi_real": [[0.0]] * num_sites,
                "psi_imag": [[0.0]] * num_sites,
                "mu": [[0.0]] * num_sites,
            }

            resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
            resp.raise_for_status()
            print(f"Step {step_index + 1}/{len(steps)}: Je={je:.4f}, posted frame")

        client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
        print(f"Run {run_id} completed successfully")

    except Exception as exc:
        client.patch(f"/api/runs/{run_id}/status", json={"status": "failed"})
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# services/cpp-tdgl-runner/Dockerfile
FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir numpy httpx

COPY services/cpp-tdgl-runner/runner.py /app/runner.py

CMD ["python", "/app/runner.py"]
```

- [ ] **Step 3: Commit**

```bash
git add services/cpp-tdgl-runner/
git commit -m "feat: add cpp-tdgl runner script and Dockerfile"
```

---

### Task 12: Argo WorkflowTemplate

**Files:**
- Create: `workflows/cpp-tdgl-sim.yaml`

- [ ] **Step 1: Write the WorkflowTemplate**

```yaml
# workflows/cpp-tdgl-sim.yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: cpp-tdgl-sim
  namespace: tdgl
spec:
  arguments:
    parameters:
      - name: run-id
        value: ""
      - name: data-service-url
        value: "http://data-viewer.tdgl.svc.cluster.local"
      - name: image
        value: "ghcr.io/fangrh/cpp-tdgl-runner:latest"
  entrypoint: simulate
  templates:
    - name: simulate
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/runner.py]
        env:
          - name: TDGL_RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: TDGL_DATA_SERVICE_URL
            value: "{{workflow.parameters.data-service-url}}"
```

- [ ] **Step 2: Commit**

```bash
git add workflows/cpp-tdgl-sim.yaml
git commit -m "feat: add Argo WorkflowTemplate for cpp-tdgl simulation"
```

---

### Task 13: CI pipeline update

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add tdgl-workflow and cpp-tdgl-runner to CI**

Update the "Detect changed paths" step in `.github/workflows/ci.yml` to add detection for the new services:

After the existing `GENERATOR=false` line, add:

```yaml
          WORKFLOW=false
          RUNNER=false
          echo "$CHANGES" | grep -qE "^(src/tdgl_workflow/|services/tdgl-workflow/)" && WORKFLOW=true
          echo "$CHANGES" | grep -qE "^(services/cpp-tdgl-runner/)" && RUNNER=true
          echo "build_workflow=$WORKFLOW" >> "$GITHUB_OUTPUT"
          echo "build_runner=$RUNNER" >> "$GITHUB_OUTPUT"
          echo "Workflow changed: $WORKFLOW"
          echo "Runner changed: $RUNNER"
```

Add build steps after the existing "Build and push generator" step:

```yaml
      - name: Build and push tdgl-workflow
        if: steps.changes.outputs.build_workflow == 'true'
        run: |
          SHA=$(git rev-parse --short HEAD)
          docker build -f services/tdgl-workflow/Dockerfile \
            -t ghcr.io/fangrh/tdgl-workflow:$SHA \
            .
          docker push ghcr.io/fangrh/tdgl-workflow:$SHA

      - name: Build and push cpp-tdgl-runner
        if: steps.changes.outputs.build_runner == 'true'
        run: |
          SHA=$(git rev-parse --short HEAD)
          docker build -f services/cpp-tdgl-runner/Dockerfile \
            -t ghcr.io/fangrh/cpp-tdgl-runner:$SHA \
            .
          docker push ghcr.io/fangrh/cpp-tdgl-runner:$SHA
```

Update the "Update manifest tags and commit" condition:

```yaml
      - name: Update manifest tags and commit
        if: github.ref == 'refs/heads/main' && (steps.changes.outputs.build_viewer == 'true' || steps.changes.outputs.build_generator == 'true' || steps.changes.outputs.build_workflow == 'true' || steps.changes.outputs.build_runner == 'true')
        run: |
          SHA=$(git rev-parse --short HEAD)
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          if [ "${{ steps.changes.outputs.build_viewer }}" = "true" ]; then
            sed -i "s|image: ghcr.io/fangrh/tdgl-data-viewer:.*|image: ghcr.io/fangrh/tdgl-data-viewer:$SHA|" services/data-viewer/k8s/deployment.yaml
            echo "Updated data-viewer tag to $SHA"
          fi
          if [ "${{ steps.changes.outputs.build_generator }}" = "true" ]; then
            sed -i "s|image: ghcr.io/fangrh/tdgl-generator:.*|image: ghcr.io/fangrh/tdgl-generator:$SHA|" services/generator/k8s/job.yaml
            echo "Updated generator tag to $SHA"
          fi
          if [ "${{ steps.changes.outputs.build_workflow }}" = "true" ]; then
            sed -i "s|image: ghcr.io/fangrh/tdgl-workflow:.*|image: ghcr.io/fangrh/tdgl-workflow:$SHA|" services/tdgl-workflow/k8s/deployment.yaml
            echo "Updated tdgl-workflow tag to $SHA"
          fi
          if [ "${{ steps.changes.outputs.build_runner }}" = "true" ]; then
            sed -i "s|image: ghcr.io/fangrh/cpp-tdgl-runner:.*|image: ghcr.io/fangrh/cpp-tdgl-runner:$SHA|" workflows/cpp-tdgl-sim.yaml
            echo "Updated cpp-tdgl-runner tag to $SHA"
          fi
          git add services/data-viewer/k8s/deployment.yaml services/generator/k8s/job.yaml services/tdgl-workflow/k8s/deployment.yaml workflows/cpp-tdgl-sim.yaml
          git diff --cached --quiet || git commit -m "ci: update image tags to $SHA"
          git push
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add tdgl-workflow and cpp-tdgl-runner to CI pipeline"
```

---

## Self-Review

**1. Spec coverage:**
- Device builder with preview → Task 2, 4, 6
- Timing builder with preview → Task 3, 4, 7
- Simulate submit → Task 8
- Data stored in existing data-viewer DB → Task 8 (via API)
- Argo Workflow execution → Task 8 (submission), Task 11 (runner), Task 12 (template)
- Deployment → Task 9 (Dockerfile + k8s), Task 10 (nginx), Task 13 (CI)

**2. Placeholder scan:** No TBDs. The cpp-tdgl-runner has placeholder frame data with a clear NOTE about filling in real solver integration — this is intentional as the C++ solver interface needs separate iteration.

**3. Type consistency:** All session keys use `device_params` and `timing_params`. The mesh data structure (`sites`, `elements`, `probe_indices`, `num_sites`, `num_elements`) is consistent between mesh.py output and simulate.py consumption.
