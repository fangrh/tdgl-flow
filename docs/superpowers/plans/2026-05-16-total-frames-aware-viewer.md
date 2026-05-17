# Total-Frames-Aware Viewer with Waiting

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The generator declares total frame count upfront, the viewer sets the slider to the full range immediately, and navigates by frame_index — showing "waiting" if a frame hasn't arrived yet. I-V curve updates in real-time as frames stream in.

**Architecture:** Add `total_frames` to the Run model (nullable for backward compat). Generator passes `je_count * frames_per_je` at run creation. Viewer switches from array-index-based to frame-index-based navigation. When a frame is unavailable, the viewer shows a waiting message and auto-loads when the SSE event arrives.

**Tech Stack:** SQLAlchemy, FastAPI, Pydantic, vanilla JS

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `tdgl_data/models.py` | Modify | Add `total_frames` column to Run |
| `tdgl_data/schemas.py` | Modify | Add `total_frames` to CreateRunRequest, RunResponse |
| `tdgl_data/repository.py` | Modify | Accept `total_frames` in create_run |
| `tdgl_data/app.py` | Modify | Pass `total_frames` through to model and response |
| `tdgl_generator/cli.py` | Modify | Calculate and send `total_frames` |
| `tdgl_data/static/viewer.html` | Modify | Frame-index navigation, waiting state, real-time I-V |
| `tests/test_api.py` | Modify | Update tests for new total_frames field |

---

### Task 1: Add `total_frames` to the backend

**Files:**
- Modify: `tdgl_data/models.py`
- Modify: `tdgl_data/schemas.py`
- Modify: `tdgl_data/repository.py`
- Modify: `tdgl_data/app.py`

- [ ] **Step 1: Add column to Run model**

In `tdgl_data/models.py`, add after the `metadata_` column (after line 47):

```python
    total_frames: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
```

- [ ] **Step 2: Add to schemas**

In `tdgl_data/schemas.py`, add `total_frames` to `CreateRunRequest` (after `image_tag`):

```python
class CreateRunRequest(BaseModel):
    solver_type: str = "synthetic"
    grid_shape: tuple[StrictPositiveInt, StrictPositiveInt] = Field(default=(64, 64))
    device_params: dict = Field(default_factory=dict)
    timing_params: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    git_commit: str | None = None
    image_tag: str | None = None
    total_frames: int | None = None
```

Add `total_frames` to `RunResponse` (after `created_at`):

```python
class RunResponse(BaseModel):
    run_id: str
    status: str
    solver_type: str
    mesh_metadata: dict
    device_params: dict
    timing_params: dict
    metadata: dict
    created_at: str | None = None
    total_frames: int | None = None
```

- [ ] **Step 3: Update repository**

In `tdgl_data/repository.py`, update `create_run` to accept `total_frames`:

Change the function signature (line 9):

```python
def create_run(
    session: Session,
    *,
    solver_type: str,
    grid_shape: tuple[int, int],
    device_params: dict | None = None,
    timing_params: dict | None = None,
    metadata: dict | None = None,
    git_commit: str | None = None,
    image_tag: str | None = None,
    total_frames: int | None = None,
) -> Run:
```

Add `total_frames=total_frames,` to the Run constructor (after `image_tag=image_tag,`):

```python
    run = Run(
        run_id=str(uuid4()),
        solver_type=solver_type,
        status="created",
        mesh_metadata={"grid_shape": list(grid_shape)},
        device_params=device_params or {},
        timing_params=timing_params or {},
        metadata_=metadata or {},
        git_commit=git_commit,
        image_tag=image_tag,
        total_frames=total_frames,
    )
```

- [ ] **Step 4: Update app.py**

In `tdgl_data/app.py`, update `api_create_run` to pass `total_frames`:

```python
    @app.post("/api/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
    def api_create_run(body: CreateRunRequest) -> RunResponse:
        with session_factory() as session:
            run = create_run(
                session,
                solver_type=body.solver_type,
                grid_shape=body.grid_shape,
                device_params=body.device_params,
                timing_params=body.timing_params,
                metadata=body.metadata,
                git_commit=body.git_commit,
                image_tag=body.image_tag,
                total_frames=body.total_frames,
            )
            session.commit()
            session.refresh(run)
        return _run_response(run)
```

Update `_run_response` to include `total_frames`:

```python
def _run_response(run: Run) -> RunResponse:
    return RunResponse(
        run_id=run.run_id,
        status=run.status,
        solver_type=run.solver_type,
        mesh_metadata=run.mesh_metadata,
        device_params=run.device_params,
        timing_params=run.timing_params,
        metadata=run.metadata_,
        created_at=run.created_at.isoformat() if run.created_at else None,
        total_frames=run.total_frames,
    )
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass (total_frames is nullable/optional, backward compatible)

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/models.py tdgl_data/schemas.py tdgl_data/repository.py tdgl_data/app.py
git commit -m "feat: add total_frames to run model and API"
```

---

### Task 2: Update generator CLI to pass `total_frames`

**Files:**
- Modify: `tdgl_generator/cli.py`

- [ ] **Step 1: Send total_frames when creating run**

In `tdgl_generator/cli.py`, update the `run()` function. Change the POST body from:

```python
        run_resp = await client.post("/api/runs", json={
            "solver_type": "synthetic",
            "grid_shape": [grid_y, grid_x],
        })
```

To:

```python
        run_resp = await client.post("/api/runs", json={
            "solver_type": "synthetic",
            "grid_shape": [grid_y, grid_x],
            "total_frames": je_count * frames_per_je,
        })
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add tdgl_generator/cli.py
git commit -m "feat: generator sends total_frames at run creation"
```

---

### Task 3: Refactor viewer to frame-index-based navigation with waiting

**Files:**
- Modify: `tdgl_data/static/viewer.html`

This is the largest task. The viewer switches from array-position-based to frame-index-based navigation.

- [ ] **Step 1: Update state object**

Find the `state` object (around line 154):

```javascript
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
      eventSource: null,
    };
```

Replace with:

```javascript
    const state = {
      runs: [],
      runId: null,
      run: null,
      timeline: null,
      iv: [],
      frames: [],
      totalFrames: 0,
      currentFrameIndex: 0,
      waitingForFrame: null,
      psiBounds: null,
      playing: false,
      timer: null,
      frameBuffer: new Map(),
      eventSource: null,
    };
```

- [ ] **Step 2: Update `updateFramePositionLabel`**

Find (around line 635):

```javascript
    function updateFramePositionLabel(position = 0) {
      if (!state.frames.length) {
        els.framePositionValue.textContent = "0 / 0";
        return;
      }
      els.framePositionValue.textContent = `${position + 1} / ${state.frames.length}`;
    }
```

Replace with:

```javascript
    function updateFramePositionLabel(frameIndex = 0) {
      const total = state.totalFrames || state.frames.length;
      if (!total) {
        els.framePositionValue.textContent = "0 / 0";
        return;
      }
      els.framePositionValue.textContent = `${frameIndex + 1} / ${total}`;
    }
```

- [ ] **Step 3: Update `openEventSource` SSE handler**

Find the `frame_available` event listener inside `openEventSource` (around line 597):

```javascript
      state.eventSource.addEventListener("frame_available", (event) => {
        const data = JSON.parse(event.data);
        const existing = state.frames.find(f => f.frame_index === data.frame_index);
        if (existing) return;
        state.frames.push({
          frame_index: data.frame_index,
          time_value: data.time_value,
          je: data.je,
          voltage: data.voltage,
          status: "available",
        });
        state.iv.push({
          frame_index: data.frame_index,
          time_value: data.time_value,
          je: data.je,
          voltage: data.voltage,
        });
        els.frameSlider.max = String(state.frames.length - 1);
        updateFramePositionLabel(state.frameIndex);
        if (els.autoFollow.checked) {
          loadFrame(state.frames.length - 1);
        } else {
          drawIvPlot(state.frames[state.frameIndex]?.frame_index);
        }
        setStatus(`Frame ${data.frame_index} arrived (${data.frame_count} total).`);
      });
```

Replace with:

```javascript
      state.eventSource.addEventListener("frame_available", (event) => {
        const data = JSON.parse(event.data);
        const existing = state.frames.find(f => f.frame_index === data.frame_index);
        if (existing) return;
        state.frames.push({
          frame_index: data.frame_index,
          time_value: data.time_value,
          je: data.je,
          voltage: data.voltage,
          status: "available",
        });
        state.frames.sort((a, b) => a.frame_index - b.frame_index);
        state.iv.push({
          frame_index: data.frame_index,
          time_value: data.time_value,
          je: data.je,
          voltage: data.voltage,
        });
        updateFramePositionLabel(state.currentFrameIndex);
        if (state.waitingForFrame === data.frame_index) {
          state.waitingForFrame = null;
          loadFrame(data.frame_index);
        } else if (els.autoFollow.checked) {
          loadFrame(data.frame_index);
        } else {
          drawIvPlot(state.currentFrameIndex);
        }
        setStatus(`Frame ${data.frame_index} arrived (${data.frame_count} total).`);
      });
```

Key changes: removed `els.frameSlider.max` update (slider range is fixed from totalFrames), added `waitingForFrame` check, frames sorted by index, `state.currentFrameIndex` replaces `state.frameIndex`.

- [ ] **Step 4: Update `loadTimeline`**

Find (around line 719):

```javascript
    async function loadTimeline(runId) {
      setControlsEnabled(false);
      state.timeline = await requestJson(`/api/runs/${runId}/timeline`);
      state.iv = await requestJson(`/api/runs/${runId}/iv`);
      state.frames = state.timeline.frames;
      state.frameBuffer.clear();
      closeEventSource();
      if (!state.frames.length) {
        closeEventSource();
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
      openEventSource(runId);
      await loadFrame(0);
    }
```

Replace with:

```javascript
    async function loadTimeline(runId) {
      setControlsEnabled(false);
      state.timeline = await requestJson(`/api/runs/${runId}/timeline`);
      state.iv = await requestJson(`/api/runs/${runId}/iv`);
      state.frames = state.timeline.frames;
      state.totalFrames = state.run?.total_frames || state.frames.length;
      state.frameBuffer.clear();
      state.waitingForFrame = null;
      closeEventSource();
      if (!state.totalFrames) {
        closeEventSource();
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
      state.currentFrameIndex = 0;
      els.frameSlider.min = "0";
      els.frameSlider.max = String(state.totalFrames - 1);
      els.frameSlider.value = "0";
      updateFramePositionLabel(0);
      state.psiBounds = null;
      if (state.timeline.stats.mu) {
        drawColorbar(els.muColorbar, state.timeline.stats.mu, "mu");
      }
      setControlsEnabled(true);
      openEventSource(runId);
      if (state.frames.length) {
        await loadFrame(0);
      } else {
        setStatus("Waiting for frames to arrive...");
      }
    }
```

Key changes: `state.totalFrames` from run metadata, slider max uses totalFrames, handles case where no frames exist yet (run just created).

- [ ] **Step 5: Update `loadFrame`**

Find (around line 755):

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

Replace with:

```javascript
    async function loadFrame(targetFrameIndex) {
      const metadata = state.frames.find(f => f.frame_index === targetFrameIndex);
      if (!metadata) {
        state.waitingForFrame = targetFrameIndex;
        state.currentFrameIndex = targetFrameIndex;
        els.frameSlider.value = String(targetFrameIndex);
        updateFramePositionLabel(targetFrameIndex);
        els.frameValue.textContent = "-";
        els.timeValue.textContent = "-";
        els.jeValue.textContent = "-";
        els.voltageValue.textContent = "-";
        stopPlayback();
        setStatus(`Waiting for frame ${targetFrameIndex}...`);
        return;
      }
      state.waitingForFrame = null;
      state.currentFrameIndex = targetFrameIndex;
      els.frameSlider.value = String(targetFrameIndex);
      updateFramePositionLabel(targetFrameIndex);
      const frame = state.frameBuffer.get(targetFrameIndex) || await requestJson(`/api/runs/${state.runId}/frames/${metadata.frame_index}`);
      state.frameBuffer.set(targetFrameIndex, frame);
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
      fillBuffer(targetFrameIndex);
    }
```

Key changes: takes frame_index not position, finds metadata by frame_index, handles missing frame (waiting state), stores loaded frame in buffer, stops playback when frame unavailable.

- [ ] **Step 6: Update `fillBuffer`**

Find (around line 566):

```javascript
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

Replace with:

```javascript
    async function fillBuffer(centerFrameIndex) {
      const total = state.totalFrames;
      const low = Math.max(0, centerFrameIndex - BUFFER_RADIUS);
      const high = Math.min(total - 1, centerFrameIndex + BUFFER_RADIUS);
      for (let fi = low; fi <= high; fi += 1) {
        if (state.frameBuffer.has(fi)) continue;
        const metadata = state.frames.find(f => f.frame_index === fi);
        if (!metadata) continue;
        try {
          const frame = await requestJson(`/api/runs/${state.runId}/frames/${metadata.frame_index}`);
          state.frameBuffer.set(fi, frame);
        } catch (_error) {
          // skip failed prefetch
        }
      }
      for (const key of state.frameBuffer.keys()) {
        if (key < centerFrameIndex - BUFFER_RADIUS * 2 || key > centerFrameIndex + BUFFER_RADIUS * 2) {
          state.frameBuffer.delete(key);
        }
      }
    }
```

Key change: uses frame_index, skips unavailable frames in buffer fill, bounds use `totalFrames`.

- [ ] **Step 7: Update `nextFramePosition`**

Find (around line 795):

```javascript
    function nextFramePosition(direction = 1) {
      if (!state.frames.length) return 0;
      const raw = direction < 0
        ? state.frameIndex - playbackStepSize()
        : state.frameIndex + playbackStepSize();
      return ((raw % state.frames.length) + state.frames.length) % state.frames.length;
    }
```

Replace with:

```javascript
    function nextFrameIndex(direction = 1) {
      const total = state.totalFrames || state.frames.length;
      if (!total) return 0;
      const raw = direction < 0
        ? state.currentFrameIndex - playbackStepSize()
        : state.currentFrameIndex + playbackStepSize();
      return ((raw % total) + total) % total;
    }
```

- [ ] **Step 8: Update `startPlayback`**

Find (around line 803):

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

Replace with:

```javascript
    function startPlayback() {
      const total = state.totalFrames || state.frames.length;
      if (!total) return;
      state.playing = true;
      els.playPause.textContent = "Pause";
      const delay = Number(els.playbackSpeed.value) || 250;
      state.timer = setInterval(() => {
        const next = nextFrameIndex();
        loadFrame(next).catch((error) => {
          stopPlayback();
          setStatus(error.message, true);
        });
      }, delay);
    }
```

- [ ] **Step 9: Update button event handlers**

Find the prev/next button handlers (around line 833):

```javascript
    els.prevFrame.addEventListener("click", () => {
      stopPlayback();
      loadFrame(nextFramePosition(-1)).catch((error) => setStatus(error.message, true));
    });

    els.nextFrame.addEventListener("click", () => {
      stopPlayback();
      loadFrame(nextFramePosition()).catch((error) => setStatus(error.message, true));
    });
```

Replace with:

```javascript
    els.prevFrame.addEventListener("click", () => {
      stopPlayback();
      loadFrame(nextFrameIndex(-1)).catch((error) => setStatus(error.message, true));
    });

    els.nextFrame.addEventListener("click", () => {
      stopPlayback();
      loadFrame(nextFrameIndex()).catch((error) => setStatus(error.message, true));
    });
```

- [ ] **Step 10: Update `loadRuns` to store the run object**

In the `loadRuns` function, after `state.runId` is set, also store the current run:

Find inside `loadRuns`:

```javascript
      state.runId = selectRunId || state.runId || state.runs[0]?.run_id || null;
      if (state.runId && !state.runs.some(r => r.run_id === state.runId)) {
        state.runId = state.runs[0]?.run_id || null;
      }
      renderRunList();
```

Replace with:

```javascript
      state.runId = selectRunId || state.runId || state.runs[0]?.run_id || null;
      if (state.runId && !state.runs.some(r => r.run_id === state.runId)) {
        state.runId = state.runs[0]?.run_id || null;
      }
      state.run = state.runs.find(r => r.run_id === state.runId) || null;
      renderRunList();
```

- [ ] **Step 11: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 12: Commit**

```bash
git add tdgl_data/static/viewer.html
git commit -m "feat: frame-index navigation with total_frames slider and waiting state"
```

---

### Task 4: Build, deploy, and test with generator

- [ ] **Step 1: Rebuild and push image**

```bash
podman build -t ghcr.io/fangrh/tdgl-flow:latest .
podman push ghcr.io/fangrh/tdgl-flow:latest
```

- [ ] **Step 2: Redeploy**

```bash
kubectl rollout restart deployment data-viewer -n tdgl
kubectl wait --for=condition=ready pod -l app=data-viewer -n tdgl --timeout=60s
```

- [ ] **Step 3: Port-forward and open viewer**

```bash
kubectl port-forward svc/data-viewer -n tdgl 8000:80
```

Open `http://localhost:8000/viewer` in browser.

- [ ] **Step 4: Run generator and verify**

```bash
kubectl create job tdgl-generator-test --from=job/tdgl-generator -n tdgl
```

Expected behavior:
1. New run appears in dataset list (click Sync if needed)
2. Select the run — slider immediately shows full range (0–999 for 1000 frames)
3. Available frames load and display heatmaps
4. Drag slider to a frame that hasn't arrived yet — shows "Waiting for frame N..."
5. I-V curve updates in real-time as each frame arrives
6. When the waiting frame arrives, it auto-loads and displays
