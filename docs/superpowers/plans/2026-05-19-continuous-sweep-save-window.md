# Continuous Sweep Save-Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Python and C++ TDGL workflows use continuous sweep save-window semantics, with minimal solver selection in the workflow UI and an embedded run-specific viewer.

**Architecture:** Timing produces global physical windows; runners upload every solver-saved frame in each save window using a concatenated playback `time_value`; IV points are explicitly averaged per current step instead of created per frame. The workflow UI owns run selection/deletion and embeds a viewer iframe; the viewer only renders the selected run.

**Tech Stack:** FastAPI, Jinja2, SQLAlchemy, Pydantic, pytest, httpx, h5py, NumPy, py-tdgl, Argo WorkflowTemplates, vanilla JavaScript, Plotly.

---

## File Structure

- Modify `src/tdgl_workflow/timing.py`: validate timing input and move save windows to the stable-window tail.
- Modify `tests/test_timing.py`: pin timing validation and save-window semantics.
- Modify `src/tdgl_workflow/routes/simulate.py`: add solver whitelist and HTML submit mapping.
- Modify `src/tdgl_workflow/routes/api.py`: add solver whitelist and API submit mapping.
- Modify `src/tdgl_workflow/templates/simulate.html`: add solver selector, run-management panel, and iframe target.
- Modify `tests/test_workflow_routes.py` and `tests/test_device_timing_api.py`: verify solver selector and submit mapping.
- Modify `src/tdgl_data/schemas.py`: add frame stats input and explicit IV append request.
- Modify `src/tdgl_data/repository.py`: stop creating IV points from every frame and add explicit IV upsert.
- Modify `src/tdgl_data/app.py`: preserve runner-provided frame stats and expose explicit IV append endpoint.
- Modify `tests/test_repository.py` and `tests/test_api.py`: verify frame/IV separation and metadata behavior.
- Modify `services/cpp-tdgl-runner/runner.py`: read all save-window frames, concatenate saved-window playback time, and submit IV averages.
- Add `tests/test_cpp_runner_save_window.py`: unit-test C++ runner save-window extraction without running the C++ solver.
- Modify `services/py-tdgl-runner/runner.py`: normalize py-tdgl output to save-window playback timeline and submit IV averages.
- Add `tests/test_py_runner_timeline.py`: test shared save-window timeline helpers for Python runner behavior.
- Modify `src/tdgl_data/static/viewer.html`: remove dataset list/delete behavior and support `run_id` URL mode.
- Modify viewer-related assertions in `tests/test_api.py`: verify run-specific embedded viewer behavior.

## Task 1: Timing Save-Window Semantics

**Files:**
- Modify: `src/tdgl_workflow/timing.py`
- Modify: `tests/test_timing.py`
- Test: `tests/test_device_timing_api.py`

- [ ] **Step 1: Write failing timing tests**

Add these tests to `tests/test_timing.py`:

```python
def test_save_window_uses_end_of_stable_window():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0,
        je_final=2.0,
        je_step=1.0,
        ramp_time=1.0,
        stable_time=4.0,
        save_time=1.5,
        ramp_down=False,
    )

    first = result["steps"][0]
    assert first["ramp_start"] == pytest.approx(0.0)
    assert first["ramp_end"] == pytest.approx(1.0)
    assert first["stable_end"] == pytest.approx(5.0)
    assert first["save_start"] == pytest.approx(3.5)
    assert first["save_end"] == pytest.approx(5.0)


@pytest.mark.parametrize(
    "params, message",
    [
        ({"je_step": 0.0}, "je_step must be non-zero"),
        ({"ramp_time": -1.0}, "ramp_time must be greater than or equal to 0"),
        ({"stable_time": 0.0}, "stable_time must be greater than 0"),
        ({"save_time": 0.0}, "save_time must be greater than 0"),
        ({"save_time": 5.0}, "save_time must be less than or equal to stable_time"),
    ],
)
def test_build_timing_validates_inputs(params, message):
    from tdgl_workflow.timing import build_timing

    kwargs = {
        "je_initial": 0.0,
        "je_final": 2.0,
        "je_step": 1.0,
        "ramp_time": 1.0,
        "stable_time": 3.0,
        "save_time": 1.0,
        "ramp_down": False,
    }
    kwargs.update(params)

    with pytest.raises(ValueError, match=message):
        build_timing(**kwargs)


def test_timing_physical_windows_are_continuous():
    from tdgl_workflow.timing import build_timing

    result = build_timing(
        je_initial=0.0,
        je_final=3.0,
        je_step=1.0,
        ramp_time=0.5,
        stable_time=2.0,
        save_time=1.0,
        ramp_down=False,
    )

    steps = result["steps"]
    assert steps[1]["ramp_start"] == pytest.approx(steps[0]["stable_end"])
    assert steps[2]["ramp_start"] == pytest.approx(steps[1]["stable_end"])
```

- [ ] **Step 2: Run timing tests to verify failure**

Run:

```bash
pytest tests/test_timing.py -v
```

Expected: at least `test_save_window_uses_end_of_stable_window` fails because `save_start` is currently centered in the stable window; validation tests fail because `build_timing` does not raise these `ValueError`s.

- [ ] **Step 3: Implement timing validation and tail save windows**

Replace the top of `src/tdgl_workflow/timing.py` with this helper and update `_build_steps`:

```python
def _validate_timing_inputs(je_step: float, ramp_time: float, stable_time: float, save_time: float) -> None:
    if je_step == 0:
        raise ValueError("je_step must be non-zero")
    if ramp_time < 0:
        raise ValueError("ramp_time must be greater than or equal to 0")
    if stable_time <= 0:
        raise ValueError("stable_time must be greater than 0")
    if save_time <= 0:
        raise ValueError("save_time must be greater than 0")
    if save_time > stable_time:
        raise ValueError("save_time must be less than or equal to stable_time")


def _build_steps(je_initial, je_final, je_step, ramp_time, stable_time, save_time, t_offset=0):
    _validate_timing_inputs(je_step, ramp_time, stable_time, save_time)
    n_steps = max(1, round(abs(je_final - je_initial) / abs(je_step)))
    period = ramp_time + stable_time
    sign = 1 if je_final >= je_initial else -1

    steps = []
    for i in range(n_steps):
        t = t_offset + i * period
        je_start = je_initial + sign * i * abs(je_step)
        je_end = je_start + sign * abs(je_step)
        stable_end = t + period
        steps.append({
            "je_start": je_start,
            "je_end": je_end,
            "ramp_start": t,
            "ramp_end": t + ramp_time,
            "stable_end": stable_end,
            "save_start": stable_end - save_time,
            "save_end": stable_end,
        })

    total_time = n_steps * period
    return steps, total_time, n_steps
```

Also fix `test_build_timing_with_ramp_down` and `test_build_timing_saves_ramp_down_steps` expectations if they fail because `n_steps` includes both up and down steps. The expected ramp-down length is `len(result["steps"])`, not `result["n_steps"]`.

- [ ] **Step 4: Run timing and API timing tests**

Run:

```bash
pytest tests/test_timing.py tests/test_device_timing_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit timing semantics**

```bash
git add src/tdgl_workflow/timing.py tests/test_timing.py tests/test_device_timing_api.py
git commit -m "fix: use stable-tail save windows"
```

## Task 2: Solver Selection in Workflow Submission

**Files:**
- Modify: `src/tdgl_workflow/routes/simulate.py`
- Modify: `src/tdgl_workflow/routes/api.py`
- Modify: `src/tdgl_workflow/templates/simulate.html`
- Modify: `tests/test_workflow_routes.py`
- Modify: `tests/test_device_timing_api.py`

- [ ] **Step 1: Write failing solver UI and API tests**

Add to `tests/test_workflow_routes.py`:

```python
def test_simulate_page_renders_solver_selector(workflow_client):
    workflow_client.post("/device", data={
        "film_width": "10", "film_height": "2",
        "elec_width": "0.5", "elec_height": "1",
        "elec_y_offset": "0", "probe_x1": "-2",
        "probe_y1": "0", "probe_x2": "2", "probe_y2": "0",
        "max_edge_length": "1", "smooth": "100",
    })
    workflow_client.post("/timing", data={
        "mode": "simple",
        "je_initial": "0", "je_final": "2", "je_step": "1",
        "ramp_time": "1", "stable_time": "3", "save_time": "1",
    })

    response = workflow_client.get("/simulate")

    assert response.status_code == 200
    assert 'name="solver_type"' in response.text
    assert 'value="cpp-tdgl"' in response.text
    assert 'value="py-tdgl"' in response.text
```

Add to `tests/test_device_timing_api.py`:

```python
def test_workflow_submit_rejects_unknown_solver(client):
    resp = client.post("/api/workflows/submit", json={
        "solver_type": "unknown",
        "device_params": {},
        "timing_params": {},
        "mesh_data": {"num_sites": 1, "sites": [[0.0, 0.0]], "elements": []},
        "schedule": {"n_steps": 1},
        "solver_options": {},
        "resources": {"cpu_cores": 1, "memory_gb": 1},
    })

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unsupported solver_type: unknown"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_workflow_routes.py::test_simulate_page_renders_solver_selector tests/test_device_timing_api.py::test_workflow_submit_rejects_unknown_solver -v
```

Expected: solver selector assertion fails and API returns a non-400 response.

- [ ] **Step 3: Add whitelist helper to `src/tdgl_workflow/routes/api.py`**

Add imports and constants near the top:

```python
from fastapi import APIRouter, HTTPException, Request

SOLVER_WORKFLOWS = {
    "cpp-tdgl": "cpp-tdgl-sim",
    "py-tdgl": "py-tdgl-sim",
}


def workflow_template_for_solver(solver_type: str) -> str:
    try:
        return SOLVER_WORKFLOWS[solver_type]
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unsupported solver_type: {solver_type}") from None
```

In `submit_workflow`, read `solver_type` and use it:

```python
    solver_type = body.get("solver_type", "cpp-tdgl")
    workflow_template = workflow_template_for_solver(solver_type)
```

Change run creation and workflow metadata:

```python
                "solver_type": solver_type,
```

```python
            "generateName": f"{solver_type}-{actual_run_id[:8]}-",
```

```python
            "workflowTemplateRef": {"name": workflow_template},
```

- [ ] **Step 4: Reuse the whitelist in `src/tdgl_workflow/routes/simulate.py`**

Import the helper:

```python
from tdgl_workflow.routes.api import workflow_template_for_solver
```

In `simulate_submit`, read and validate:

```python
    solver_type = str(form_data.get("solver_type", "cpp-tdgl"))
    workflow_template = workflow_template_for_solver(solver_type)
```

Use `solver_type` in the run creation and workflow:

```python
                "solver_type": solver_type,
```

```python
                "generateName": f"{solver_type}-{run_id[:8]}-",
```

```python
                "workflowTemplateRef": {"name": workflow_template},
```

Add `"solver_type": solver_type` to the template context after submission.

- [ ] **Step 5: Add solver selector to `simulate.html`**

Inside `<form id="simForm" method="post">`, before `<h2>Solver Options</h2>`, add:

```html
    <h2>Workflow</h2>
    <div class="field-row">
        <div>
            <label>Solver</label>
            <select name="solver_type" id="solver_type">
                <option value="cpp-tdgl" selected>C++ tdgl</option>
                <option value="py-tdgl">Python tdgl</option>
            </select>
        </div>
    </div>
```

In the localStorage save/load block, include `solver_type`:

```javascript
            if (p.solver_type !== undefined) document.getElementById("solver_type").value = p.solver_type;
```

```javascript
            solver_type: document.getElementById("solver_type").value,
```

- [ ] **Step 6: Run workflow route tests**

Run:

```bash
pytest tests/test_workflow_routes.py tests/test_device_timing_api.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit solver selection**

```bash
git add src/tdgl_workflow/routes/api.py src/tdgl_workflow/routes/simulate.py src/tdgl_workflow/templates/simulate.html tests/test_workflow_routes.py tests/test_device_timing_api.py
git commit -m "feat: select tdgl solver workflow"
```

## Task 3: Separate Frame Appends from IV Points

**Files:**
- Modify: `src/tdgl_data/schemas.py`
- Modify: `src/tdgl_data/repository.py`
- Modify: `src/tdgl_data/app.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing repository tests**

In `tests/test_repository.py`, replace `test_append_frame_record_creates_timeline_and_iv_point` with:

```python
def test_append_frame_record_does_not_create_iv_point(session):
    from tdgl_data.repository import append_iv_point_record

    run = create_run(session, solver_type="cpp-tdgl", n_sites=4)
    append_frame_record(
        session,
        run_id=run.run_id,
        frame_index=0,
        time_value=1.0,
        je=2.0,
        voltage=0.01,
        frame_stats={"mu": {"min": 0.0, "max": 0.2}, "physical_time": 6.0},
    )

    frame = get_frame(session, run.run_id, 0)
    timeline = get_timeline(session, run.run_id)
    iv_points = get_iv_points(session, run.run_id)

    assert frame.frame_stats["physical_time"] == pytest.approx(6.0)
    assert len(timeline) == 1
    assert iv_points == []

    append_iv_point_record(
        session,
        run_id=run.run_id,
        frame_index=0,
        time_value=1.0,
        je=2.0,
        voltage=0.01,
    )
    iv_points = get_iv_points(session, run.run_id)
    assert len(iv_points) == 1
    assert iv_points[0].voltage == pytest.approx(0.01)
```

- [ ] **Step 2: Write failing API test for explicit IV append**

Add to `tests/test_api.py`:

```python
def test_frame_append_does_not_create_iv_until_explicit_post(client):
    created = client.post("/api/runs", json={"solver_type": "cpp-tdgl", "n_sites": 2})
    run_id = created.json()["run_id"]

    frame_body = {
        "frame_index": 0,
        "time_value": 0.5,
        "je": 1.0,
        "voltage": 0.02,
        "psi_real": [1.0, 1.0],
        "psi_imag": [0.0, 0.0],
        "mu": [0.0, 0.02],
        "frame_stats": {
            "physical_time": 4.5,
            "save_window_index": 0,
            "window_frame_index": 0,
            "voltage_valid": True,
        },
    }
    assert client.post(f"/api/runs/{run_id}/frames", json=frame_body).status_code == 201

    iv_before = client.get(f"/api/runs/{run_id}/iv")
    assert iv_before.status_code == 200
    assert iv_before.json() == []

    iv_resp = client.post(f"/api/runs/{run_id}/iv", json={
        "frame_index": 0,
        "time_value": 0.5,
        "je": 1.0,
        "voltage": 0.02,
    })
    assert iv_resp.status_code == 201

    iv_after = client.get(f"/api/runs/{run_id}/iv")
    assert iv_after.json() == [{
        "frame_index": 0,
        "time_value": 0.5,
        "je": 1.0,
        "voltage": 0.02,
    }]

    frame = client.get(f"/api/runs/{run_id}/frames/0")
    assert frame.status_code == 200
```

- [ ] **Step 3: Run targeted tests to verify failure**

Run:

```bash
pytest tests/test_repository.py::test_append_frame_record_does_not_create_iv_point tests/test_api.py::test_frame_append_does_not_create_iv_until_explicit_post -v
```

Expected: repository test fails because `append_frame_record` still creates an IV point; API test fails because `FrameAppendRequest` rejects `frame_stats` or because `/api/runs/{run_id}/iv` POST does not exist.

- [ ] **Step 4: Add schema types**

Modify `src/tdgl_data/schemas.py`:

```python
class FrameAppendRequest(BaseModel):
    frame_index: StrictNonNegativeInt
    time_value: float
    je: float
    voltage: float
    psi_real: list[float]
    psi_imag: list[float]
    mu: list[float]
    frame_stats: dict = Field(default_factory=dict)
```

Add:

```python
class IVPointAppendRequest(BaseModel):
    frame_index: StrictNonNegativeInt
    time_value: float
    je: float
    voltage: float
```

- [ ] **Step 5: Add explicit IV repository function**

Modify `src/tdgl_data/repository.py`: remove IV creation from `append_frame_record` and add:

```python
def append_iv_point_record(
    session: Session,
    *,
    run_id: str,
    frame_index: int,
    time_value: float,
    je: float,
    voltage: float,
) -> IVPoint:
    point = IVPoint(
        run_id=run_id,
        frame_index=frame_index,
        je=je,
        voltage=voltage,
        time_value=time_value,
    )
    session.add(point)
    session.flush()
    return point
```

Update `delete_frame_record` to stop deleting IV points by frame index. Run deletion already deletes all `IVPoint`s for the run, so keep that behavior.

- [ ] **Step 6: Preserve runner frame stats and add IV endpoint**

Modify imports in `src/tdgl_data/app.py`:

```python
    append_iv_point_record,
```

and schemas import:

```python
    IVPointAppendRequest,
```

In `api_append_frame`, replace `stats = {}` with:

```python
            stats = dict(body.frame_stats or {})
```

When computing array min/max, merge into the same dict:

```python
            for name, arr in arrays.items():
                stats[name] = {"min": float(np.min(arr)), "max": float(np.max(arr))}
```

Add this endpoint after `api_iv`:

```python
    @app.post(
        "/api/runs/{run_id}/iv",
        response_model=IVPointResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def api_append_iv_point(run_id: str, body: IVPointAppendRequest) -> IVPointResponse:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
            point = append_iv_point_record(
                session,
                run_id=run_id,
                frame_index=body.frame_index,
                time_value=body.time_value,
                je=body.je,
                voltage=body.voltage,
            )
            session.commit()
            return IVPointResponse(
                frame_index=point.frame_index,
                time_value=point.time_value,
                je=point.je,
                voltage=point.voltage,
            )
```

- [ ] **Step 7: Update existing tests that assumed per-frame IV**

Run:

```bash
pytest tests/test_repository.py tests/test_api.py -v
```

Expected: tests that expect `/iv` to include every appended frame fail. Update those tests so they explicitly POST `/api/runs/{run_id}/iv` before asserting IV content. Keep frame/timeline assertions unchanged.

- [ ] **Step 8: Run data service tests**

Run:

```bash
pytest tests/test_repository.py tests/test_api.py tests/test_zarr_per_site.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit frame/IV separation**

```bash
git add src/tdgl_data/schemas.py src/tdgl_data/repository.py src/tdgl_data/app.py tests/test_repository.py tests/test_api.py
git commit -m "fix: store iv points explicitly"
```

## Task 4: C++ Runner Save-Window Frame Uploads

**Files:**
- Modify: `services/cpp-tdgl-runner/runner.py`
- Add: `tests/test_cpp_runner_save_window.py`

- [ ] **Step 1: Write failing C++ runner helper tests**

Create `tests/test_cpp_runner_save_window.py`:

```python
import importlib.util
from pathlib import Path

import h5py
import numpy as np
import pytest


RUNNER_PATH = Path(__file__).parents[1] / "services" / "cpp-tdgl-runner" / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("cpp_tdgl_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_output(path: Path) -> None:
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        for index, time_value in enumerate([0.5, 1.0, 1.5, 2.0]):
            g = data.create_group(str(index))
            g.attrs["time"] = time_value
            g.create_dataset("psi_real", data=np.array([1.0, 1.0 + index]))
            g.create_dataset("psi_imag", data=np.array([0.0, 0.1 * index]))
            g.create_dataset("mu", data=np.array([0.0, 0.2 * index]))


def test_read_save_window_returns_each_frame(tmp_path):
    runner = load_runner()
    output = tmp_path / "out.h5"
    write_output(output)

    frames = runner._read_save_window_frames(str(output), save_start_rel=1.0, save_end_rel=2.0)

    assert [frame["local_time"] for frame in frames] == [1.0, 1.5, 2.0]
    assert frames[0]["mu"].tolist() == [0.0, 0.2]
    assert frames[2]["psi_real"].tolist() == [1.0, 4.0]


def test_read_save_window_rejects_empty_window(tmp_path):
    runner = load_runner()
    output = tmp_path / "out.h5"
    write_output(output)

    with pytest.raises(RuntimeError, match="No saved frames found"):
        runner._read_save_window_frames(str(output), save_start_rel=3.0, save_end_rel=4.0)


def test_saved_window_time_mapper_concatenates_windows():
    runner = load_runner()
    mapper = runner.SaveWindowTimeline()

    first = mapper.map_frame(save_start_rel=1.0, local_time=1.0)
    second = mapper.map_frame(save_start_rel=1.0, local_time=2.0)
    mapper.finish_window(save_time=1.0)
    third = mapper.map_frame(save_start_rel=3.0, local_time=3.0)

    assert first == pytest.approx(0.0)
    assert second == pytest.approx(1.0)
    assert third == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_cpp_runner_save_window.py -v
```

Expected: fails because `_read_save_window_frames` and `SaveWindowTimeline` do not exist.

- [ ] **Step 3: Add C++ runner save-window helpers**

In `services/cpp-tdgl-runner/runner.py`, replace `_read_save_window` with:

```python
class SaveWindowTimeline:
    def __init__(self) -> None:
        self.offset = 0.0

    def map_frame(self, *, save_start_rel: float, local_time: float) -> float:
        return self.offset + max(0.0, local_time - save_start_rel)

    def finish_window(self, *, save_time: float) -> None:
        self.offset += save_time


def _read_save_window_frames(hdf5_path: str, save_start_rel: float, save_end_rel: float) -> list[dict]:
    with h5py.File(hdf5_path, "r") as f:
        data_grp = f.get("data")
        if not data_grp:
            raise RuntimeError(f"No solver data group found in {hdf5_path}")

        frames = []
        for key in data_grp.keys():
            try:
                int(key)
            except ValueError:
                continue
            g = data_grp[key]
            local_time = float(g.attrs.get("time", 0.0))
            if save_start_rel <= local_time <= save_end_rel:
                frames.append({
                    "local_time": local_time,
                    "psi_real": np.array(g["psi_real"], dtype=np.float64),
                    "psi_imag": np.array(g["psi_imag"], dtype=np.float64),
                    "mu": np.array(g["mu"], dtype=np.float64),
                })

        frames.sort(key=lambda item: item["local_time"])
        if not frames:
            raise RuntimeError(
                f"No saved frames found in save window [{save_start_rel}, {save_end_rel}] for {hdf5_path}"
            )
        return frames


def _voltage_from_mu(mu: np.ndarray, probe_indices: list[int]) -> tuple[float, bool]:
    if len(probe_indices) < 2:
        return 0.0, False
    return float(mu[probe_indices[1]] - mu[probe_indices[0]]), True
```

- [ ] **Step 4: Update C++ main loop upload behavior**

In `main()`, before the step loop, add:

```python
            frame_index = 0
            timeline = SaveWindowTimeline()
```

Replace the success branch that builds one averaged `frame_data` with:

```python
                    save_start_rel = step["save_start"] - step["ramp_start"]
                    save_end_rel = step["save_end"] - step["ramp_start"]
                    save_frames = _read_save_window_frames(output_hdf5, save_start_rel, save_end_rel)

                    valid_voltages = []
                    last_frame_time_value = None
                    for window_frame_index, saved in enumerate(save_frames):
                        voltage, voltage_valid = _voltage_from_mu(saved["mu"], probe_indices)
                        if voltage_valid:
                            valid_voltages.append(voltage)
                        time_value = timeline.map_frame(
                            save_start_rel=save_start_rel,
                            local_time=saved["local_time"],
                        )
                        physical_time = step["ramp_start"] + saved["local_time"]
                        frame_data = {
                            "frame_index": frame_index,
                            "time_value": time_value,
                            "je": je,
                            "voltage": voltage,
                            "psi_real": saved["psi_real"].tolist(),
                            "psi_imag": saved["psi_imag"].tolist(),
                            "mu": saved["mu"].tolist(),
                            "frame_stats": {
                                "physical_time": physical_time,
                                "local_time": saved["local_time"],
                                "save_window_index": step_index,
                                "window_frame_index": window_frame_index,
                                "save_start": step["save_start"],
                                "save_end": step["save_end"],
                                "voltage_valid": voltage_valid,
                                "solver_type": "cpp-tdgl",
                            },
                        }
                        resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
                        resp.raise_for_status()
                        last_frame_time_value = time_value
                        frame_index += 1

                    if valid_voltages and last_frame_time_value is not None:
                        iv_resp = client.post(f"/api/runs/{run_id}/iv", json={
                            "frame_index": frame_index - 1,
                            "time_value": last_frame_time_value,
                            "je": je,
                            "voltage": float(np.mean(valid_voltages)),
                        })
                        iv_resp.raise_for_status()
                    timeline.finish_window(save_time=step["save_end"] - step["save_start"])
                    print(f"  Posted {len(save_frames)} frames for save window, Je={je:.6f}")
```

Remove the old unconditional frame POST at the end of the step loop:

```python
                resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
                resp.raise_for_status()
                print(f"  Posted frame {step_index + 1}/{len(steps)}")
```

Keep failure handling by raising and marking the run `failed`; do not post all-zero frames on solver failure.

- [ ] **Step 5: Run C++ runner tests**

Run:

```bash
pytest tests/test_cpp_runner_save_window.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run data service tests affected by frame stats**

Run:

```bash
pytest tests/test_api.py::test_frame_append_does_not_create_iv_until_explicit_post tests/test_cpp_runner_save_window.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit C++ runner save-window upload**

```bash
git add services/cpp-tdgl-runner/runner.py tests/test_cpp_runner_save_window.py
git commit -m "fix: upload cpp save-window frames"
```

## Task 5: Python Runner Save-Window Normalization

**Files:**
- Modify: `services/py-tdgl-runner/runner.py`
- Add: `tests/test_py_runner_timeline.py`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_py_runner_timeline.py`:

```python
import importlib.util
from pathlib import Path

import numpy as np
import pytest


RUNNER_PATH = Path(__file__).parents[1] / "services" / "py-tdgl-runner" / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("py_tdgl_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_group_solution_frames_by_save_window():
    runner = load_runner()
    steps = [
        {"je_end": 1.0, "save_start": 3.0, "save_end": 5.0},
        {"je_end": 2.0, "save_start": 8.0, "save_end": 10.0},
    ]
    times = np.array([2.0, 3.0, 4.0, 5.0, 8.0, 9.0, 10.0, 11.0])

    grouped = runner._group_solution_indices_by_save_window(times, steps)

    assert grouped == [[1, 2, 3], [4, 5, 6]]


def test_playback_time_from_physical_time_concatenates_windows():
    runner = load_runner()
    mapper = runner.SaveWindowTimeline()

    assert mapper.map_physical(save_start=3.0, physical_time=3.0) == pytest.approx(0.0)
    assert mapper.map_physical(save_start=3.0, physical_time=5.0) == pytest.approx(2.0)
    mapper.finish_window(save_time=2.0)
    assert mapper.map_physical(save_start=8.0, physical_time=8.0) == pytest.approx(2.0)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_py_runner_timeline.py -v
```

Expected: fails because helper functions/classes do not exist.

- [ ] **Step 3: Add Python runner save-window helpers**

In `services/py-tdgl-runner/runner.py`, add above `main()`:

```python
class SaveWindowTimeline:
    def __init__(self) -> None:
        self.offset = 0.0

    def map_physical(self, *, save_start: float, physical_time: float) -> float:
        return self.offset + max(0.0, physical_time - save_start)

    def finish_window(self, *, save_time: float) -> None:
        self.offset += save_time


def _group_solution_indices_by_save_window(times: np.ndarray, steps: list[dict]) -> list[list[int]]:
    grouped = []
    for step in steps:
        indices = [
            int(i)
            for i, time_value in enumerate(times)
            if step["save_start"] <= float(time_value) <= step["save_end"]
        ]
        if not indices:
            raise RuntimeError(
                f"No saved frames found in save window [{step['save_start']}, {step['save_end']}]"
            )
        grouped.append(indices)
    return grouped


def _voltage_from_mu(mu: np.ndarray, probe_indices: list[int]) -> tuple[float, bool]:
    if len(probe_indices) < 2:
        return 0.0, False
    return float(mu[probe_indices[1]] - mu[probe_indices[0]]), True
```

- [ ] **Step 4: Update Python runner upload loop**

After the existing py-tdgl solve call returns `solution`, replace the current upload loop:

```python
        for i, (time, psi, mu) in enumerate(zip(solution.times, solution.psi, solution.mu)):
```

with:

```python
        solution_times = np.asarray(solution.times, dtype=np.float64)
        grouped_indices = _group_solution_indices_by_save_window(solution_times, steps)
        timeline = SaveWindowTimeline()
        frame_index = 0

        for step_index, (step, indices) in enumerate(zip(steps, grouped_indices)):
            valid_voltages = []
            last_frame_time_value = None
            je = float(step["je_end"])

            for window_frame_index, solution_index in enumerate(indices):
                physical_time = float(solution_times[solution_index])
                psi = solution.psi[solution_index]
                mu = solution.mu[solution_index]
                voltage, voltage_valid = _voltage_from_mu(mu, probe_indices)
                if voltage_valid:
                    valid_voltages.append(voltage)

                time_value = timeline.map_physical(
                    save_start=step["save_start"],
                    physical_time=physical_time,
                )
                frame_data = {
                    "frame_index": frame_index,
                    "time_value": time_value,
                    "je": je,
                    "voltage": voltage,
                    "psi_real": psi.real.tolist(),
                    "psi_imag": psi.imag.tolist(),
                    "mu": mu.tolist(),
                    "frame_stats": {
                        "physical_time": physical_time,
                        "local_time": physical_time,
                        "save_window_index": step_index,
                        "window_frame_index": window_frame_index,
                        "save_start": step["save_start"],
                        "save_end": step["save_end"],
                        "voltage_valid": voltage_valid,
                        "solver_type": "py-tdgl",
                    },
                }
                resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
                resp.raise_for_status()
                last_frame_time_value = time_value
                frame_index += 1

            if valid_voltages and last_frame_time_value is not None:
                iv_resp = client.post(f"/api/runs/{run_id}/iv", json={
                    "frame_index": frame_index - 1,
                    "time_value": last_frame_time_value,
                    "je": je,
                    "voltage": float(np.mean(valid_voltages)),
                })
                iv_resp.raise_for_status()
            timeline.finish_window(save_time=step["save_end"] - step["save_start"])
```

- [ ] **Step 5: Run Python runner helper tests**

Run:

```bash
pytest tests/test_py_runner_timeline.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run runner helper tests together**

Run:

```bash
pytest tests/test_cpp_runner_save_window.py tests/test_py_runner_timeline.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit Python runner normalization**

```bash
git add services/py-tdgl-runner/runner.py tests/test_py_runner_timeline.py
git commit -m "fix: normalize py save-window frames"
```

## Task 6: Embedded Viewer Mode

**Files:**
- Modify: `src/tdgl_data/static/viewer.html`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Replace viewer tests for dataset list/delete behavior**

In `tests/test_api.py`, update viewer tests:

```python
def test_viewer_is_run_specific_without_dataset_list(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert 'id="runList"' not in response.text
    assert 'class="run-item-delete"' not in response.text
    assert "deleteRun" not in response.text
    assert "URLSearchParams" in response.text
    assert "run_id" in response.text


def test_viewer_has_empty_state_for_missing_run_id(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert "No run selected" in response.text
```

Remove or rewrite the old `test_viewer_can_delete_selected_history_run`; deletion moves to workflow panel tests in Task 7.

- [ ] **Step 2: Run viewer tests to verify failure**

Run:

```bash
pytest tests/test_api.py::test_viewer_is_run_specific_without_dataset_list tests/test_api.py::test_viewer_has_empty_state_for_missing_run_id -v
```

Expected: fails because viewer still renders `runList`, delete buttons, and run-list JavaScript.

- [ ] **Step 3: Remove run-list HTML and add empty state**

In `src/tdgl_data/static/viewer.html`, remove:

```html
    <div class="run-list-header">
      <h2>Datasets</h2>
      <button class="sync-btn" id="syncRuns" title="Refresh run list">
        <span class="sync-icon">&#x21bb;</span> Sync
      </button>
    </div>
    <div class="run-list" id="runList"></div>
```

Add after `<header>`:

```html
    <div id="emptyState" class="status">No run selected</div>
```

Remove CSS rules for `.run-list-header`, `.run-list`, `.run-item`, and `.run-item-delete`.

- [ ] **Step 4: Simplify viewer state and startup**

In the JavaScript `els` object, remove `runList` and `syncRuns`. Add:

```javascript
      emptyState: document.getElementById("emptyState"),
```

Add this function:

```javascript
    function selectedRunIdFromUrl() {
      const params = new URLSearchParams(window.location.search);
      return params.get("run_id");
    }
```

Replace the startup logic at the bottom of the script with:

```javascript
    async function startViewer() {
      const runId = selectedRunIdFromUrl();
      if (!runId) {
        els.emptyState.style.display = "block";
        setStatus("No run selected");
        return;
      }
      els.emptyState.style.display = "none";
      state.runId = runId;
      try {
        state.run = await requestJson(`api/runs/${runId}`);
        await loadTimeline(runId);
      } catch (err) {
        setStatus(`Unable to load run: ${err.message}`, true);
      }
    }

    startViewer();
```

Remove `renderRunList`, `deleteRun`, and `loadRuns` functions. Keep `loadTimeline`, `loadFrame`, SSE, buffering, and playback functions.

- [ ] **Step 5: Make playback wait for missing next frame**

Update the play tick function so when `state.playing` is true and the target frame metadata is missing, it sets `state.waitingForFrame` and does not clear the timer. Use this behavior:

```javascript
      if (!metadata) {
        state.waitingForFrame = targetFrameIndex;
        setStatus(`Waiting for frame ${targetFrameIndex}.`);
        return;
      }
```

In the SSE `frame_available` handler, after adding the frame:

```javascript
        if (state.waitingForFrame === data.frame_index && state.playing) {
          state.waitingForFrame = null;
          loadFrame(data.frame_index);
        }
```

- [ ] **Step 6: Run viewer tests**

Run:

```bash
pytest tests/test_api.py -k "viewer" -v
```

Expected: viewer tests pass after updating old assertions to embedded mode.

- [ ] **Step 7: Commit embedded viewer**

```bash
git add src/tdgl_data/static/viewer.html tests/test_api.py
git commit -m "feat: make viewer run-specific"
```

## Task 7: Workflow Panel Run Selection and Delete

**Files:**
- Modify: `src/tdgl_workflow/templates/simulate.html`
- Modify: `src/tdgl_workflow/routes/api.py`
- Modify: `tests/test_workflow_routes.py`

- [ ] **Step 1: Write failing workflow panel tests**

Add to `tests/test_workflow_routes.py`:

```python
def test_simulate_page_contains_embedded_viewer_panel(workflow_client):
    response = workflow_client.get("/simulate")

    assert response.status_code == 200
    assert 'id="workflowRunPanel"' in response.text
    assert 'id="viewerFrame"' in response.text
    assert "viewerFrame.src" in response.text
    assert "/tdgl/viewer?run_id=" in response.text
    assert "deleteWorkflowRun" in response.text
```

Add to `tests/test_device_timing_api.py`:

```python
def test_workflow_delete_run_proxies_to_data_service(client, monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 204
        content = b""

    async def fake_delete(self, url):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.delete", fake_delete)

    resp = client.delete("/api/runs/run-123")

    assert resp.status_code == 204
    assert calls
    assert calls[0].endswith("/api/runs/run-123")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_workflow_routes.py::test_simulate_page_contains_embedded_viewer_panel tests/test_device_timing_api.py::test_workflow_delete_run_proxies_to_data_service -v
```

Expected: panel test fails because iframe controls are absent; API test fails because workflow service has no delete proxy.

- [ ] **Step 3: Add workflow delete proxy**

In `src/tdgl_workflow/routes/api.py`, add:

```python
@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, request: Request):
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{settings.data_service_url}/api/runs/{run_id}")
        return JSONResponse({}, status_code=resp.status_code)
```

If returning JSON on 204 causes test/client issues, return `Response(status_code=resp.status_code)` and import `Response` from `fastapi.responses`.

- [ ] **Step 4: Add workflow panel and iframe**

In `src/tdgl_workflow/templates/simulate.html`, replace the recent runs table block with:

```html
<h2>Recent Runs</h2>
<div id="workflowRunPanel">
    {% if runs %}
    <table id="runsTable">
        <tr><th>Run ID</th><th>Solver</th><th>Status</th><th>Created</th><th></th><th></th></tr>
        {% for run in runs %}
        <tr data-run-id="{{ run.run_id }}">
            <td>{{ run.run_id[:8] }}</td>
            <td>{{ run.solver_type }}</td>
            <td><span class="status-badge status-{{ run.status }}">{{ run.status }}</span></td>
            <td>{{ run.created_at }}</td>
            <td><button type="button" class="btn-view" onclick="selectWorkflowRun('{{ run.run_id }}')">View</button></td>
            <td><button type="button" class="btn-delete" onclick="deleteWorkflowRun('{{ run.run_id }}')">Delete</button></td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <p>No runs yet.</p>
    {% endif %}
    <iframe id="viewerFrame" title="TDGL viewer" style="width:100%;height:760px;border:1px solid #cbd5e1;border-radius:6px;margin-top:1rem;"></iframe>
</div>
```

Add script:

```html
<script>
function selectWorkflowRun(runId) {
    var frame = document.getElementById("viewerFrame");
    frame.src = "/tdgl/viewer?run_id=" + encodeURIComponent(runId);
}

function deleteWorkflowRun(runId) {
    fetch(basePath + "/api/runs/" + encodeURIComponent(runId), {method: "DELETE"})
        .then(function(resp) {
            if (!resp.ok && resp.status !== 204) throw new Error("Delete failed");
            var row = document.querySelector('#runsTable tr[data-run-id="' + runId + '"]');
            if (row) row.remove();
            var frame = document.getElementById("viewerFrame");
            if (frame.src.indexOf(encodeURIComponent(runId)) !== -1) frame.removeAttribute("src");
        })
        .catch(function(err) { alert(err.message); });
}
</script>
```

Keep the existing status refresh script, but update the row selector to continue using `#runsTable tr[data-run-id]`.

- [ ] **Step 5: Run workflow route tests**

Run:

```bash
pytest tests/test_workflow_routes.py tests/test_device_timing_api.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit workflow panel viewer embedding**

```bash
git add src/tdgl_workflow/templates/simulate.html src/tdgl_workflow/routes/api.py tests/test_workflow_routes.py tests/test_device_timing_api.py
git commit -m "feat: embed viewer in workflow panel"
```

## Task 8: Final Verification

**Files:**
- No planned source edits unless verification exposes a bug.

- [ ] **Step 1: Run full test suite**

Run:

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Run focused static searches**

Run:

```bash
rg -n "workflowTemplateRef.*cpp-tdgl-sim|solver_type.*cpp-tdgl|append_frame_record\\(|class=\"run-item-delete\"|id=\"runList\"" src tests services
```

Expected:

- hard-coded `cpp-tdgl-sim` only appears in whitelist/default option contexts;
- `append_frame_record(` does not create IV points;
- `class="run-item-delete"` and `id="runList"` do not appear in `src/tdgl_data/static/viewer.html`.

- [ ] **Step 3: Manually smoke-test local apps if dependencies are available**

Run data service:

```bash
uvicorn tdgl_data.dev_app:create_dev_app --factory --reload --port 8000
```

Run workflow service in another terminal:

```bash
uvicorn tdgl_workflow.dev_app:create_dev_app --factory --reload --port 8001
```

Expected:

- `http://127.0.0.1:8001/simulate` renders the solver selector and workflow run panel.
- `http://127.0.0.1:8000/viewer` shows `No run selected`.
- `http://127.0.0.1:8000/viewer?run_id=<existing-run-id>` loads only that run.

- [ ] **Step 4: Commit verification fixes if needed**

If Step 1, 2, or 3 requires small fixes, commit them:

```bash
git add <changed-files>
git commit -m "fix: finish continuous sweep verification"
```

If no fixes are needed, do not create an empty commit.

## Self-Review

- Spec coverage: timing semantics are covered in Task 1; solver selection in Task 2; frame/IV separation in Task 3; C++ save-window uploads in Task 4; Python normalization in Task 5; embedded viewer in Task 6; workflow panel run ownership in Task 7; verification in Task 8.
- Placeholder scan: this plan has no incomplete sections or generic edge-case-only instructions. Each code-changing task includes concrete code or exact behavior.
- Type consistency: `solver_type`, `frame_stats`, `/api/runs/{run_id}/iv`, `SaveWindowTimeline`, and save-window field names match across tasks.
