# Remove Demo Generation from Data Viewer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the demo data generation endpoint and UI from the data-viewer, since data is now generated exclusively by the generator K8s Job.

**Architecture:** The data-viewer becomes a read-only service backed by PostgreSQL. All demo-related API endpoints, schemas, UI elements, and tests are removed. The `generate_synthetic_run` import is removed from `app.py` but kept in `synthetic.py` since the generator still uses it.

**Tech Stack:** FastAPI, SQLAlchemy, vanilla JS/HTML

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `tdgl_data/app.py` | Modify | Remove `/api/demo-runs` endpoint and `generate_synthetic_run` import |
| `tdgl_data/schemas.py` | Modify | Remove `CreateDemoRunRequest` class |
| `tdgl_data/static/viewer.html` | Modify | Remove "Create demo" button, demo options, and related JS |
| `tests/test_api.py` | Modify | Remove/update demo-related tests |

---

### Task 1: Remove CreateDemoRunRequest schema

**Files:**
- Modify: `tdgl_data/schemas.py`

- [ ] **Step 1: Remove the CreateDemoRunRequest class**

Delete lines 19-23 from `tdgl_data/schemas.py`:

```python
class CreateDemoRunRequest(BaseModel):
    frame_count: StrictPositiveInt = 24
    grid_shape: tuple[StrictPositiveInt, StrictPositiveInt] = Field(default=(64, 64))
    seed: int = 0
```

- [ ] **Step 2: Verify no remaining imports are orphaned**

`StrictPositiveInt` is still used by `CreateRunRequest` and `FrameAppendRequest`, so no import changes needed.

- [ ] **Step 3: Commit**

```bash
git add tdgl_data/schemas.py
git commit -m "refactor: remove CreateDemoRunRequest schema"
```

---

### Task 2: Remove demo endpoint and synthetic import from app.py

**Files:**
- Modify: `tdgl_data/app.py`

- [ ] **Step 1: Remove the `generate_synthetic_run` import**

In `tdgl_data/app.py`, delete the line:

```python
from tdgl_data.synthetic import generate_synthetic_run
```

- [ ] **Step 2: Remove `CreateDemoRunRequest` from the schemas import**

Change:

```python
from tdgl_data.schemas import (
    CreateDemoRunRequest,
    CreateRunRequest,
    FrameAppendRequest,
    ...
)
```

To:

```python
from tdgl_data.schemas import (
    CreateRunRequest,
    FrameAppendRequest,
    ...
)
```

- [ ] **Step 3: Remove the `api_create_demo_run` endpoint**

Delete the entire `@app.post("/api/demo-runs", ...)` endpoint function (approximately 40 lines starting with `@app.post("/api/demo-runs"`).

- [ ] **Step 4: Run tests to see which ones break**

Run: `python -m pytest tests/test_api.py -v`
Expected: Tests referencing `demo-runs` or `CreateDemoRunRequest` will FAIL or ERROR. All other tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tdgl_data/app.py
git commit -m "refactor: remove demo-runs endpoint from data-viewer API"
```

---

### Task 3: Remove demo UI from viewer.html

**Files:**
- Modify: `tdgl_data/static/viewer.html`

- [ ] **Step 1: Remove the demo options HTML block**

Delete the entire `<div class="demo-options">` element:

```html
<div class="demo-options" aria-label="Demo run options">
  <label>X size <input id="demoXSize" type="number" min="1" max="512" value="72"></label>
  <label>Y size <input id="demoYSize" type="number" min="1" max="512" value="72"></label>
  <label>Frames <input id="demoFrameCount" type="number" min="1" max="500" value="24"></label>
  <button id="createDemo" class="primary">Create demo</button>
</div>
```

- [ ] **Step 2: Remove the `demoRequestBody` JavaScript function**

Delete:

```javascript
function demoRequestBody() {
  const xSize = positiveIntegerFromInput(els.demoXSize, 72);
  const ySize = positiveIntegerFromInput(els.demoYSize, 72);
  const frameCount = positiveIntegerFromInput(els.demoFrameCount, 24);
  return {
    frame_count: frameCount,
    grid_shape: [ySize, xSize],
    seed: Date.now() % 100000,
  };
}
```

- [ ] **Step 3: Remove the `createDemo` click handler**

Delete:

```javascript
els.createDemo.addEventListener("click", async () => {
  try {
    stopPlayback();
    setStatus("Creating demo run...");
    const run = await requestJson("/api/demo-runs", {
      method: "POST",
      body: JSON.stringify(demoRequestBody()),
    });
    await loadRuns(run.run_id);
  } catch (error) {
    setStatus(error.message, true);
  }
});
```

- [ ] **Step 4: Remove the `demoXSize`, `demoYSize`, `demoFrameCount`, `createDemo` element references from the `els` object**

Find and remove these lines from the `els` object:

```javascript
demoXSize: document.getElementById("demoXSize"),
demoYSize: document.getElementById("demoYSize"),
demoFrameCount: document.getElementById("demoFrameCount"),
createDemo: document.getElementById("createDemo"),
```

- [ ] **Step 5: Commit**

```bash
git add tdgl_data/static/viewer.html
git commit -m "refactor: remove demo creation UI from viewer"
```

---

### Task 4: Update tests

**Files:**
- Modify: `tests/test_api.py`

- [ ] **Step 1: Update `test_delete_run_removes_database_record`**

This test currently creates a demo run to test deletion. Change it to use the regular `/api/runs` + `/api/runs/{id}/frames` flow instead:

```python
def test_delete_run_removes_database_record(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [3, 4]})
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    client.post(f"/api/runs/{run_id}/frames", json={
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        "psi_imag": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        "mu": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
    })

    deleted = client.delete(f"/api/runs/{run_id}")

    assert deleted.status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.get(f"/api/runs/{run_id}/timeline").status_code == 404
```

- [ ] **Step 2: Update `test_sse_endpoint_exists_for_valid_run`**

Change from using demo-runs to regular run creation:

```python
def test_sse_endpoint_exists_for_valid_run(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [2, 2]})
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    response = client.head(f"/api/runs/{run_id}/events")
    assert response.status_code != 404
```

- [ ] **Step 3: Delete `test_create_demo_run_writes_readable_heatmap_frames`**

Delete the entire function. This test verified demo-specific behavior which no longer exists.

- [ ] **Step 4: Delete `test_viewer_demo_creation_exposes_frame_and_grid_options`**

Delete the entire function. The demo UI no longer exists.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -v`
Expected: ALL tests PASS (80 tests become ~78 tests)

- [ ] **Step 6: Commit**

```bash
git add tests/test_api.py
git commit -m "refactor: update tests to remove demo-runs references"
```

---

### Task 5: Verify and deploy

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 2: Rebuild and push image**

```bash
podman build -t ghcr.io/fangrh/tdgl-flow:latest .
podman push ghcr.io/fangrh/tdgl-flow:latest
```

- [ ] **Step 3: Redeploy to K8s**

```bash
kubectl rollout restart deployment data-viewer -n tdgl
kubectl wait --for=condition=ready pod -l app=data-viewer -n tdgl --timeout=60s
```

- [ ] **Step 4: Commit and push**

```bash
git push origin main
```
