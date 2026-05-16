# Large Frame Set Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the viewer so it remains responsive with 1000+ frames by caching frame stats in the database and using adaptive colorbars with frame buffering in the client.

**Architecture:** Server caches per-frame min/max in a new JSON column. Timeline endpoint aggregates cached stats instead of reading Zarr. Client removes the blocking `computePsiBounds` call, starts with first-frame colorbars, expands adaptively, and pre-fetches nearby frames. Playback speed selector gives control over animation rate.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), vanilla HTML/JS with Canvas API (frontend), Zarr (array storage)

---

### Task 1: Add frame_stats column to Frame model

**Files:**
- Modify: `tdgl_data/models.py:60-80` (Frame class)

- [ ] **Step 1: Add the frame_stats column to Frame model**

In `tdgl_data/models.py`, add a `frame_stats` column to the `Frame` class, after the `checksum` column (line 72):

```python
frame_stats: Mapped[dict | None] = mapped_column(
    MutableDict.as_mutable(json_type), default=None, nullable=True
)
```

This uses the same `json_type` and `MutableDict` pattern already used by `device_params`, `timing_params`, etc. It is nullable because existing frames won't have it.

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `python -m pytest tests/test_api.py -v`
Expected: All existing tests PASS. The new nullable column doesn't break anything.

- [ ] **Step 3: Commit**

```bash
git add tdgl_data/models.py
git commit -m "feat: add frame_stats column to Frame model"
```

---

### Task 2: Compute and store frame stats at write time, use cached stats in timeline

**Files:**
- Modify: `tdgl_data/app.py:110-118` (replace `_update_stats` with `_compute_frame_stats` + `_update_stats`)
- Modify: `tdgl_data/app.py:191-239` (`api_create_demo_run`)
- Modify: `tdgl_data/app.py:266-309` (`api_append_frame`)
- Modify: `tdgl_data/app.py:312-333` (`api_timeline`)
- Modify: `tdgl_data/repository.py:51-85` (`append_frame_record`)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
def test_append_frame_stores_frame_stats(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [2, 2]})
    run_id = created.json()["run_id"]

    response = client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": 0,
            "time_value": 0.1,
            "je": 1.0,
            "voltage": 0.03,
            "psi_real": [[1.0, 0.5], [0.25, 0.0]],
            "psi_imag": [[0.0, 0.5], [0.75, 1.0]],
            "mu": [[-0.1, 0.0], [0.1, 0.2]],
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "available"


def test_timeline_stats_use_cached_frame_stats_without_zarr_reads(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [2, 2]})
    run_id = created.json()["run_id"]

    client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": 0,
            "time_value": 0.0,
            "je": 0.0,
            "voltage": 0.0,
            "psi_real": [[1.0, 2.0], [3.0, 4.0]],
            "psi_imag": [[0.0, 0.0], [0.0, 0.0]],
            "mu": [[0.5, 1.0], [1.5, 2.0]],
        },
    )
    client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": 1,
            "time_value": 0.1,
            "je": 1.0,
            "voltage": 0.03,
            "psi_real": [[-1.0, 0.0], [0.0, 5.0]],
            "psi_imag": [[0.0, 0.0], [0.0, 0.0]],
            "mu": [[-1.0, 0.0], [0.0, 3.0]],
        },
    )

    timeline = client.get(f"/api/runs/{run_id}/timeline")
    assert timeline.status_code == 200
    stats = timeline.json()["stats"]

    assert stats["psi_real"]["min"] == pytest.approx(-1.0)
    assert stats["psi_real"]["max"] == pytest.approx(5.0)
    assert stats["mu"]["min"] == pytest.approx(-1.0)
    assert stats["mu"]["max"] == pytest.approx(3.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::test_append_frame_stores_frame_stats tests/test_api.py::test_timeline_stats_use_cached_frame_stats_without_zarr_reads -v`
Expected: FAIL — `frame_stats` is not being stored or aggregated yet.

- [ ] **Step 3: Replace _update_stats with _compute_frame_stats + _update_stats**

In `tdgl_data/app.py`, replace the existing `_update_stats` function (lines 110-118):

```python
def _compute_frame_stats(arrays: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for name, values in arrays.items():
        stats[name] = {"min": float(np.min(values)), "max": float(np.max(values))}
    return stats


def _update_stats(
    stats: dict[str, dict[str, float]],
    frame_stats: dict[str, dict[str, float]],
) -> None:
    for name, entry in frame_stats.items():
        aggregate = stats.setdefault(name, {"min": float("inf"), "max": float("-inf")})
        aggregate["min"] = min(aggregate["min"], entry["min"])
        aggregate["max"] = max(aggregate["max"], entry["max"])
```

- [ ] **Step 4: Add frame_stats parameter to append_frame_record**

In `tdgl_data/repository.py`, update `append_frame_record` to accept and store `frame_stats`. Add `frame_stats: dict | None = None` parameter and `frame_stats=frame_stats,` to the Frame constructor:

```python
def append_frame_record(
    session: Session,
    *,
    run_id: str,
    frame_index: int,
    time_value: float,
    je: float,
    voltage: float,
    zarr_group: str,
    checksum: str | None = None,
    frame_stats: dict | None = None,
    status: str = "available",
) -> Frame:
    now = utcnow()
    frame = Frame(
        run_id=run_id,
        frame_index=frame_index,
        time_value=time_value,
        je=je,
        voltage=voltage,
        status=status,
        zarr_group=zarr_group,
        checksum=checksum,
        frame_stats=frame_stats,
        created_at=now,
        committed_at=now,
    )
    iv_point = IVPoint(
        run_id=run_id,
        frame_index=frame_index,
        je=je,
        voltage=voltage,
        time_value=time_value,
    )
    session.add_all([frame, iv_point])
    session.flush()
    return frame
```

- [ ] **Step 5: Compute and store stats in api_append_frame**

In `tdgl_data/app.py`, update `api_append_frame`. Replace the section that computes arrays and creates the frame record (the lines after `arrays = _frame_arrays(body, _grid_shape(run))` through the `append_frame_record` call):

```python
            arrays = _frame_arrays(body, _grid_shape(run))
            stats = _compute_frame_stats(arrays)
            try:
                frame = append_frame_record(
                    session,
                    run_id=run_id,
                    frame_index=body.frame_index,
                    time_value=body.time_value,
                    je=body.je,
                    voltage=body.voltage,
                    zarr_group=run.zarr_root,
                    frame_stats=stats,
                    status="writing",
                )
```

- [ ] **Step 6: Compute and store stats in api_create_demo_run**

In `app.py`, update the demo run loop. Replace the for loop body:

```python
                for synthetic_frame in generate_synthetic_run(
                    body.frame_count,
                    body.grid_shape,
                    seed=body.seed,
                ):
                    frame_arrays = synthetic_frame.arrays()
                    stats = _compute_frame_stats(frame_arrays)
                    frame = append_frame_record(
                        session,
                        run_id=run.run_id,
                        frame_index=synthetic_frame.frame_index,
                        time_value=synthetic_frame.time_value,
                        je=synthetic_frame.je,
                        voltage=synthetic_frame.voltage,
                        zarr_group=run.zarr_root,
                        frame_stats=stats,
                        status="writing",
                    )
                    zarr_store.append_frame(
                        run.run_id,
                        synthetic_frame.frame_index,
                        frame_arrays,
                    )
```

- [ ] **Step 7: Update api_timeline to aggregate cached stats**

Replace the `api_timeline` endpoint (lines 312-333):

```python
    @app.get("/api/runs/{run_id}/timeline", response_model=TimelineResponse)
    def api_timeline(run_id: str) -> TimelineResponse:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
            frames = [
                frame for frame in get_timeline(session, run_id) if frame.status == "available"
            ]

        stats: dict[str, dict[str, float]] = {}
        for frame in frames:
            if frame.frame_stats is None:
                continue
            _update_stats(stats, frame.frame_stats)

        return TimelineResponse(
            run_id=run_id,
            frames=[_frame_metadata(frame) for frame in frames],
            stats=stats,
        )
```

This reads `frame_stats` from the already-loaded Frame objects — no Zarr reads at all.

- [ ] **Step 8: Run all tests**

Run: `python -m pytest tests/test_api.py -v`
Expected: All tests PASS, including the two new tests.

- [ ] **Step 9: Commit**

```bash
git add tdgl_data/app.py tdgl_data/repository.py tests/test_api.py
git commit -m "feat: cache frame stats in database for fast timeline aggregation"
```

---

### Task 3: Rewrite client with adaptive colorbars, frame buffer, and speed control

This task combines the three client-side changes since they all modify the same `viewer.html` file and depend on each other. The frame buffer and adaptive colorbars work together in `loadFrame`, and all three are tested as one unit.

**Files:**
- Modify: `tdgl_data/static/viewer.html` (state, functions, HTML)
- Test: `tests/test_api.py` (viewer HTML assertions)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
def test_viewer_uses_adaptive_psi_colorbars(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert "computePsiBounds" not in response.text
    assert "expandBounds" not in response.text
    assert "adaptivePsiBounds" in response.text


def test_viewer_includes_frame_buffer(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert "frameBuffer" in response.text
    assert "fillBuffer" in response.text
    assert "BUFFER_RADIUS" in response.text


def test_viewer_includes_playback_speed_control(client):
    response = client.get("/viewer")
    assert response.status_code == 200
    assert 'id="playbackSpeed"' in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::test_viewer_uses_adaptive_psi_colorbars tests/test_api.py::test_viewer_includes_frame_buffer tests/test_api.py::test_viewer_includes_playback_speed_control -v`
Expected: FAIL — viewer still has old code.

- [ ] **Step 3: Update state object — add buffer and BUFFER_RADIUS**

In `viewer.html`, add `BUFFER_RADIUS` constant and `frameBuffer` to state. Replace the `state` object (lines 131-141):

```javascript
    const BUFFER_RADIUS = 5;

    const state = {
      runs: [],
      runId: null,
      timeline: null,
      iv: [],
      frames: [],
      frameIndex: 0,
      psiBounds: null,
      playing: false,
      timer: null,
      frameBuffer: new Map(),
    };
```

- [ ] **Step 4: Replace expandBounds and computePsiBounds with adaptivePsiBounds + fillBuffer**

In `viewer.html`, remove `expandBounds` (lines 544-551) and `computePsiBounds` (lines 553-560). Replace them with:

```javascript
    function adaptivePsiBounds(current, arrays) {
      const frameBounds = minMax(psiMagnitude(arrays));
      if (!current) return frameBounds;
      const expanded = {
        min: Math.min(current.min, frameBounds.min),
        max: Math.max(current.max, frameBounds.max),
      };
      return (expanded.min === current.min && expanded.max === current.max) ? current : expanded;
    }

    async function fillBuffer(centerPosition) {
      const total = state.frames.length;
      const low = Math.max(0, centerPosition - BUFFER_RADIUS);
      const high = Math.min(total - 1, centerPosition + BUFFER_RADIUS);
      for (let pos = low; pos <= high; pos += 1) {
        if (state.frameBuffer.has(pos)) continue;
        const metadata = state.frames[pos];
        try {
          const frame = await requestJson(`/api/runs/${state.runId}/frames/${metadata.frame_index}`);
          state.frameBuffer.set(pos, frame);
        } catch (_error) {
          // skip failed prefetch
        }
      }
      for (const key of state.frameBuffer.keys()) {
        if (key < centerPosition - BUFFER_RADIUS * 2 || key > centerPosition + BUFFER_RADIUS * 2) {
          state.frameBuffer.delete(key);
        }
      }
    }
```

- [ ] **Step 5: Rewrite loadTimeline to enable controls immediately**

Replace the `loadTimeline` function:

```javascript
    async function loadTimeline(runId) {
      setControlsEnabled(false);
      state.timeline = await requestJson(`/api/runs/${runId}/timeline`);
      state.iv = await requestJson(`/api/runs/${runId}/iv`);
      state.frames = state.timeline.frames;
      state.frameBuffer.clear();
      if (!state.frames.length) {
        setControlsEnabled(false);
        clearCanvas(els.psiCanvas);
        clearCanvas(els.muCanvas);
        clearCanvas(els.psiColorbar);
        clearCanvas(els.muColorbar);
        clearCanvas(els.ivCanvas);
        state.psiBounds = null;
        updateColorbarLabels("psi", null);
        updateColorbarLabels("mu", null);
        updateFramePositionLabel();
        setStatus("Selected run has no available frames.");
        return;
      }
      state.frameIndex = 0;
      els.frameSlider.min = "0";
      els.frameSlider.max = String(state.frames.length - 1);
      els.frameSlider.value = "0";
      updateFramePositionLabel(0);
      state.psiBounds = null;
      if (state.timeline.stats.mu) {
        drawColorbar(els.muColorbar, state.timeline.stats.mu, "mu");
      }
      setControlsEnabled(true);
      await loadFrame(0);
    }
```

Key changes: removed `computePsiBounds` call, controls enabled immediately, mu colorbar drawn from timeline stats if available, frame buffer cleared.

- [ ] **Step 6: Rewrite loadFrame with adaptive bounds and buffer lookup**

Replace the `loadFrame` function:

```javascript
    async function loadFrame(position) {
      const metadata = state.frames[position];
      if (!metadata) return;
      state.frameIndex = position;
      els.frameSlider.value = String(position);
      updateFramePositionLabel(position);
      const frame = state.frameBuffer.get(position) || await requestJson(`/api/runs/${state.runId}/frames/${metadata.frame_index}`);
      const prevBounds = state.psiBounds;
      state.psiBounds = adaptivePsiBounds(state.psiBounds, frame.arrays);
      if (state.psiBounds !== prevBounds) {
        drawColorbar(els.psiColorbar, state.psiBounds, "psi");
      }
      if (!state.timeline.stats.mu) {
        drawColorbar(els.muColorbar, minMax(frame.arrays.mu), "mu");
      }
      drawHeatmap(els.psiCanvas, psiMagnitude(frame.arrays), state.psiBounds);
      drawHeatmap(els.muCanvas, frame.arrays.mu, state.timeline.stats.mu || minMax(frame.arrays.mu));
      drawIvPlot(frame.frame_index);
      els.frameValue.textContent = String(frame.frame_index);
      els.timeValue.textContent = formatNumber(frame.time_value);
      els.jeValue.textContent = formatNumber(frame.je);
      els.voltageValue.textContent = formatNumber(frame.voltage);
      setStatus(`Loaded frame ${frame.frame_index}.`);
      fillBuffer(position);
    }
```

Key changes: psi bounds expand per frame, mu falls back to per-frame if no timeline stats, buffer checked before fetch, `fillBuffer` called after load.

- [ ] **Step 7: Add psiBounds reset in loadRuns else branch**

In the `loadRuns` function's else branch (the `else` block starting around line 592 where `state.runId` is null), add `state.psiBounds = null;` before `setControlsEnabled(false);`. The else branch should include:

```javascript
      } else {
        state.frames = [];
        state.psiBounds = null;
        setControlsEnabled(false);
```

- [ ] **Step 8: Add playback speed selector HTML**

In the timeline section (around line 82-88), replace the step label line:

```html
      <label>Step <input id="playbackStep" type="number" min="1" value="1" aria-label="Playback step"></label>
```

With:

```html
      <label>Step <input id="playbackStep" type="number" min="1" value="1" aria-label="Playback step"></label>
      <label>Speed <select id="playbackSpeed" aria-label="Playback speed">
        <option value="250">1x</option>
        <option value="125">2x</option>
        <option value="60">4x</option>
        <option value="30">8x</option>
      </select></label>
```

- [ ] **Step 9: Add speed element reference and update startPlayback**

In the `els` object, add after the `playbackStep` entry:

```javascript
      playbackSpeed: document.getElementById("playbackSpeed"),
```

Replace the `startPlayback` function:

```javascript
    function startPlayback() {
      if (!state.frames.length) return;
      state.playing = true;
      els.playPause.textContent = "Pause";
      const delay = Number(els.playbackSpeed.value) || 250;
      state.timer = setInterval(() => {
        const next = nextFramePosition();
        loadFrame(next).catch((error) => {
          stopPlayback();
          setStatus(error.message, true);
        });
      }, delay);
    }
```

The only change from the original is `const delay = Number(els.playbackSpeed.value) || 250;` replacing the hardcoded `250`.

- [ ] **Step 10: Update existing tests that reference removed functions**

In `tests/test_api.py`, update `test_viewer_includes_fixed_global_colorbars`:

```python
def test_viewer_includes_fixed_global_colorbars(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    assert 'id="psiColorbar"' in response.text
    assert 'id="muColorbar"' in response.text
    assert "adaptivePsiBounds" in response.text
    assert "drawColorbar" in response.text
    assert "psiBounds" in response.text
```

Update `test_viewer_sets_frame_bar_scale_before_scanning_frame_data`:

```python
def test_viewer_sets_frame_bar_scale_before_loading_first_frame(client):
    response = client.get("/viewer")

    assert response.status_code == 200
    slider_max_index = response.text.index('els.frameSlider.max = String(state.frames.length - 1)')
    controls_enabled_index = response.text.index("setControlsEnabled(true)")
    assert slider_max_index < controls_enabled_index
```

- [ ] **Step 11: Run all tests**

Run: `python -m pytest tests/test_api.py -v`
Expected: All tests PASS.

- [ ] **Step 12: Commit**

```bash
git add tdgl_data/static/viewer.html tests/test_api.py
git commit -m "feat: adaptive colorbars, frame buffer, and playback speed control"
```
