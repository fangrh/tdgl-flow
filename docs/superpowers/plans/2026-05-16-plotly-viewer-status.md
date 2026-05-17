# Plotly Viewer with Running Status

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Canvas rendering with Plotly.js for smooth WebGL-accelerated heatmaps and I-V curves, and mark runs as "running" when the generator is actively writing frames.

**Architecture:** Plotly.js replaces all `<canvas>` elements with `<div>` elements. Plotly.react() provides efficient real-time updates (only re-renders changed data traces). The backend gets a PATCH endpoint for run status, and the generator calls it to mark runs as running/completed. The viewer run list shows colored status badges.

**Tech Stack:** Plotly.js (CDN), FastAPI PATCH endpoint, existing SSE and buffer infrastructure

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `tdgl_data/app.py` | Modify | Add PATCH status endpoint |
| `tdgl_data/repository.py` | Modify | Add `update_run_status` function |
| `tdgl_generator/cli.py` | Modify | Call PATCH status endpoint at start/end |
| `tdgl_data/static/viewer.html` | Rewrite | Replace Canvas with Plotly, add status badges |

---

### Task 1: Add run status update endpoint and generator integration

**Files:**
- Modify: `tdgl_data/repository.py`
- Modify: `tdgl_data/app.py`
- Modify: `tdgl_generator/cli.py`

- [ ] **Step 1: Add `update_run_status` to repository**

In `tdgl_data/repository.py`, add after `complete_run`:

```python
def update_run_status(session: Session, run_id: str, status: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found")
    run.status = status
    if status in ("completed", "failed"):
        run.completed_at = utcnow()
    elif status == "running":
        run.started_at = utcnow()
    session.flush()
    return run
```

- [ ] **Step 2: Add PATCH endpoint to app.py**

In `tdgl_data/app.py`, add the import:

```python
from tdgl_data.repository import (
    ...,
    update_run_status,
)
```

Add a new Pydantic schema in `tdgl_data/schemas.py`:

```python
class UpdateRunStatusRequest(BaseModel):
    status: str
```

In `app.py`, add the endpoint (after `api_create_run`):

```python
    @app.patch("/api/runs/{run_id}/status", response_model=RunResponse)
    def api_update_run_status(run_id: str, body: UpdateRunStatusRequest) -> RunResponse:
        with session_factory() as session:
            try:
                run = update_run_status(session, run_id, body.status)
            except LookupError:
                raise HTTPException(status_code=404, detail="Run not found") from None
            session.commit()
            session.refresh(run)
        return _run_response(run)
```

Add the import of `UpdateRunStatusRequest` to the schemas import block in `app.py`.

- [ ] **Step 3: Update generator CLI to set status**

In `tdgl_generator/cli.py`, update the `run()` function. After creating the run and getting the `run_id`, add:

```python
        await client.patch(f"/api/runs/{run_id}/status", json={"status": "running"})
```

At the end of `run()`, after the generation loop completes, add:

```python
        await client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add tdgl_data/repository.py tdgl_data/schemas.py tdgl_data/app.py tdgl_generator/cli.py
git commit -m "feat: add run status endpoint and generator status updates"
```

---

### Task 2: Rewrite viewer with Plotly.js and status badges

**Files:**
- Rewrite: `tdgl_data/static/viewer.html`

This task replaces the entire viewer. The state management, SSE handling, buffer logic, and event handling stay the same. All Canvas rendering code is replaced with Plotly calls.

- [ ] **Step 1: Replace the viewer file**

Replace the entire contents of `tdgl_data/static/viewer.html` with the Plotly-based viewer. The new file:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TDGL Heatmap Viewer</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f9;
      color: #1f2933;
    }
    body { margin: 0; }
    main { max-width: 1580px; margin: 0 auto; padding: 24px; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
    h1 { margin: 0; font-size: 24px; font-weight: 650; }
    .run-list-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
    .run-list-header h2 { margin: 0; font-size: 15px; font-weight: 650; }
    .run-list { max-height: 200px; overflow-y: auto; border: 1px solid #c8d0d9; border-radius: 6px; background: #ffffff; margin-bottom: 14px; }
    .run-item { display: flex; align-items: center; gap: 10px; padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #eaedf1; }
    .run-item:last-child { border-bottom: none; }
    .run-item:hover { background: #f0f4f8; }
    .run-item.selected { background: #e0f2f1; border-left: 3px solid #0f766e; padding-left: 9px; }
    .run-item-info { flex: 1; min-width: 0; }
    .run-item-id { font-size: 13px; font-weight: 600; color: #1f2933; }
    .run-item-meta { font-size: 12px; color: #667381; margin-top: 2px; }
    .run-item-delete { background: none; border: none; color: #9aa5b4; font-size: 18px; padding: 0 4px; min-height: auto; cursor: pointer; line-height: 1; border-radius: 4px; }
    .run-item-delete:hover { color: #d92d20; background: #fef2f2; }
    .status-badge { display: inline-block; font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 4px; text-transform: uppercase; }
    .status-running { background: #fef3c7; color: #92400e; }
    .status-completed { background: #d1fae5; color: #065f46; }
    .status-created { background: #e5e7eb; color: #374151; }
    .status-failed { background: #fee2e2; color: #991b1b; }
    .sync-btn { min-height: 36px; border: 1px solid #c8d0d9; border-radius: 6px; background: #ffffff; color: #1f2933; font: inherit; padding: 0 10px; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; }
    .sync-btn:hover { background: #f0f4f8; }
    .sync-btn.spinning .sync-icon { animation: spin 0.8s linear infinite; }
    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    .sync-icon { display: inline-block; font-size: 16px; }
    .timeline { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 14px; }
    button, select, input[type="number"] {
      min-height: 36px;
      border: 1px solid #c8d0d9;
      border-radius: 6px;
      background: #ffffff;
      color: #1f2933;
      font: inherit;
      padding: 0 12px;
    }
    button { cursor: pointer; }
    button:disabled, select:disabled, input:disabled { cursor: not-allowed; opacity: 0.55; }
    input[type="range"] { flex: 1 1 320px; min-width: 180px; }
    .meta { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 8px; margin: 14px 0 18px; }
    .metric { border: 1px solid #d9e0e7; border-radius: 6px; background: #ffffff; padding: 10px 12px; }
    .metric span { display: block; color: #667381; font-size: 12px; margin-bottom: 4px; }
    .metric strong { font-size: 16px; font-weight: 650; }
    .plots { display: grid; gap: 16px; }
    .plots-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(340px, 1fr); }
    .panel { min-width: 0; }
    .panel h2 { margin: 0 0 8px; font-size: 16px; font-weight: 650; }
    .plotly-div { width: 100%; min-height: 420px; }
    .status { min-height: 22px; color: #52606d; margin-top: 12px; }
    .status.error { color: #b42318; }
    @media (max-width: 760px) {
      main { padding: 16px; }
      .plots-row { grid-template-columns: 1fr; }
      .meta { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>TDGL Heatmap Viewer</h1>
    </header>
    <div class="run-list-header">
      <h2>Datasets</h2>
      <button class="sync-btn" id="syncRuns" title="Refresh run list">
        <span class="sync-icon">&#x21bb;</span> Sync
      </button>
    </div>
    <div class="run-list" id="runList"></div>
    <section class="timeline" aria-label="Frame controls">
      <button id="prevFrame" title="Previous frame">Prev</button>
      <button id="playPause" title="Play or pause">Play</button>
      <button id="nextFrame" title="Next frame">Next</button>
      <label>Step <input id="playbackStep" type="number" min="1" value="1" aria-label="Playback step"></label>
      <label>Speed <select id="playbackSpeed" aria-label="Playback speed">
        <option value="250">1x</option>
        <option value="125">2x</option>
        <option value="60">4x</option>
        <option value="30">8x</option>
      </select></label>
      <label><input id="autoFollow" type="checkbox" checked> Auto-follow</label>
      <input id="frameSlider" type="range" min="0" max="0" value="0" disabled aria-label="Frame">
      <span id="framePositionValue">0 / 0</span>
    </section>
    <section class="meta" aria-label="Frame metadata">
      <div class="metric"><span>Frame index</span><strong id="frameValue">-</strong></div>
      <div class="metric"><span>Time</span><strong id="timeValue">-</strong></div>
      <div class="metric"><span>Je</span><strong id="jeValue">-</strong></div>
      <div class="metric"><span>Voltage</span><strong id="voltageValue">-</strong></div>
    </section>
    <section class="plots plots-row" aria-label="Plots">
      <div class="panel">
        <h2>|psi|</h2>
        <div id="psiPlot" class="plotly-div"></div>
      </div>
      <div class="panel">
        <h2>mu</h2>
        <div id="muPlot" class="plotly-div"></div>
      </div>
      <div class="panel">
        <h2>I-V curve</h2>
        <div id="ivPlot" class="plotly-div"></div>
      </div>
    </section>
    <div id="status" class="status"></div>
  </main>
  <script>
    const BUFFER_RADIUS = 5;

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
    const els = {
      runList: document.getElementById("runList"),
      syncRuns: document.getElementById("syncRuns"),
      frameSlider: document.getElementById("frameSlider"),
      playbackStep: document.getElementById("playbackStep"),
      playbackSpeed: document.getElementById("playbackSpeed"),
      autoFollow: document.getElementById("autoFollow"),
      framePositionValue: document.getElementById("framePositionValue"),
      prevFrame: document.getElementById("prevFrame"),
      playPause: document.getElementById("playPause"),
      nextFrame: document.getElementById("nextFrame"),
      frameValue: document.getElementById("frameValue"),
      timeValue: document.getElementById("timeValue"),
      jeValue: document.getElementById("jeValue"),
      voltageValue: document.getElementById("voltageValue"),
      status: document.getElementById("status"),
    };

    const HEATMAP_LAYOUT = {
      margin: {l: 50, r: 20, t: 10, b: 50},
      xaxis: {title: "x", constrain: "domain"},
      yaxis: {title: "y", scaleanchor: "x", constrain: "domain"},
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
    };
    const HEATMAP_CONFIG = {responsive: true, displayModeBar: false};

    function setStatus(message, isError = false) {
      els.status.textContent = message;
      els.status.classList.toggle("error", isError);
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, {headers: {"Content-Type": "application/json"}, ...options});
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`${response.status} ${response.statusText}: ${text}`);
      }
      if (response.status === 204) return null;
      return response.json();
    }

    function formatNumber(value) {
      if (value === undefined || value === null) return "-";
      return Number(value).toPrecision(4);
    }

    function psiMagnitude(arrays) {
      return arrays.psi_real.map((row, y) => row.map((real, x) => Math.hypot(real, arrays.psi_imag[y][x])));
    }

    function renderPsiHeatmap(data, bounds) {
      const trace = {
        z: data,
        type: "heatmap",
        colorscale: "Viridis",
        zmin: bounds?.min,
        zmax: bounds?.max,
        showscale: true,
        colorbar: {title: "|psi|", thickness: 15},
      };
      Plotly.react("psiPlot", [trace], HEATMAP_LAYOUT, HEATMAP_CONFIG);
    }

    function renderMuHeatmap(data, bounds) {
      const trace = {
        z: data,
        type: "heatmap",
        colorscale: "RdBu_r",
        zmin: bounds?.min,
        zmax: bounds?.max,
        showscale: true,
        colorbar: {title: "mu", thickness: 15},
      };
      Plotly.react("muPlot", [trace], HEATMAP_LAYOUT, HEATMAP_CONFIG);
    }

    function renderIvCurve(ivData, currentFrameIndex) {
      const x = ivData.map(p => p.je);
      const y = ivData.map(p => p.voltage);
      const traces = [{
        x, y,
        mode: "lines+markers",
        type: "scatter",
        name: "I-V",
        line: {color: "#0f766e", width: 2},
        marker: {size: 3},
      }];
      if (currentFrameIndex !== null && currentFrameIndex !== undefined) {
        const current = ivData.find(p => p.frame_index === currentFrameIndex);
        if (current) {
          traces.push({
            x: [current.je],
            y: [current.voltage],
            mode: "markers",
            type: "scatter",
            name: "current",
            marker: {size: 12, color: "#d92d20", symbol: "x"},
          });
        }
      }
      const layout = {
        margin: {l: 60, r: 20, t: 10, b: 50},
        xaxis: {title: "Je"},
        yaxis: {title: "Voltage"},
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        showlegend: false,
      };
      Plotly.react("ivPlot", traces, layout, {responsive: true, displayModeBar: false});
    }

    function adaptivePsiBounds(current, arrays) {
      let min = Infinity, max = -Infinity;
      for (const row of psiMagnitude(arrays)) {
        for (const v of row) {
          if (v < min) min = v;
          if (v > max) max = v;
        }
      }
      if (!current) return {min, max};
      return {
        min: Math.min(current.min, min),
        max: Math.max(current.max, max),
      };
    }

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
        } catch (_error) {}
      }
      for (const key of state.frameBuffer.keys()) {
        if (key < centerFrameIndex - BUFFER_RADIUS * 2 || key > centerFrameIndex + BUFFER_RADIUS * 2) {
          state.frameBuffer.delete(key);
        }
      }
    }

    function closeEventSource() {
      if (state.eventSource) {
        state.eventSource.close();
        state.eventSource = null;
      }
    }

    function openEventSource(runId) {
      closeEventSource();
      state.eventSource = new EventSource(`/api/runs/${runId}/events`);
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
          renderIvCurve(state.iv, state.currentFrameIndex);
        }
        setStatus(`Frame ${data.frame_index} arrived (${data.frame_count} total).`);
      });
      state.eventSource.addEventListener("run_completed", () => {
        loadRuns(state.runId);
      });
      state.eventSource.onerror = () => {
        setStatus("Event stream disconnected. Reconnecting...", false);
      };
    }

    function setControlsEnabled(enabled) {
      els.frameSlider.disabled = !enabled;
      els.prevFrame.disabled = !enabled;
      els.nextFrame.disabled = !enabled;
      els.playPause.disabled = !enabled;
    }

    function updateFramePositionLabel(frameIndex = 0) {
      const total = state.totalFrames || state.frames.length;
      if (!total) {
        els.framePositionValue.textContent = "0 / 0";
        return;
      }
      els.framePositionValue.textContent = `${frameIndex + 1} / ${total}`;
    }

    function statusBadgeClass(status) {
      if (status === "running") return "status-running";
      if (status === "completed") return "status-completed";
      if (status === "failed") return "status-failed";
      return "status-created";
    }

    function formatRunDate(run) {
      const created = run.created_at || run.metadata?.created_at;
      if (!created) return "unknown";
      const d = new Date(created + (created.endsWith("Z") ? "" : "Z"));
      if (isNaN(d.getTime())) return created.slice(0, 19);
      return d.toLocaleString();
    }

    function renderRunList() {
      els.runList.innerHTML = "";
      for (const run of state.runs) {
        const item = document.createElement("div");
        item.className = "run-item" + (run.run_id === state.runId ? " selected" : "");
        item.innerHTML = `
          <div class="run-item-info">
            <div class="run-item-id">${run.solver_type} ${run.run_id.slice(0, 8)}</div>
            <div class="run-item-meta">
              <span class="status-badge ${statusBadgeClass(run.status)}">${run.status}</span>
              &middot; ${formatRunDate(run)} &middot; ${run.mesh_metadata?.grid_shape?.join("x") || "?"}
            </div>
          </div>
          <button class="run-item-delete" data-run-id="${run.run_id}" title="Delete run">&times;</button>
        `;
        item.addEventListener("click", (e) => {
          if (e.target.closest(".run-item-delete")) return;
          if (run.run_id !== state.runId) {
            stopPlayback();
            state.runId = run.run_id;
            renderRunList();
            loadTimeline(state.runId).catch((error) => setStatus(error.message, true));
          }
        });
        const deleteBtn = item.querySelector(".run-item-delete");
        deleteBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          deleteRun(run.run_id);
        });
        els.runList.append(item);
      }
    }

    async function deleteRun(runId) {
      if (!confirm(`Delete run ${runId.slice(0, 8)}?`)) return;
      try {
        stopPlayback();
        setStatus("Deleting run...");
        await requestJson(`/api/runs/${runId}`, {method: "DELETE"});
        await loadRuns(state.runId === runId ? null : state.runId);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function loadRuns(selectRunId = null) {
      state.runs = await requestJson("/api/runs");
      state.runId = selectRunId || state.runId || state.runs[0]?.run_id || null;
      if (state.runId && !state.runs.some(r => r.run_id === state.runId)) {
        state.runId = state.runs[0]?.run_id || null;
      }
      state.run = state.runs.find(r => r.run_id === state.runId) || null;
      renderRunList();
      if (state.runId) {
        await loadTimeline(state.runId);
      } else {
        closeEventSource();
        state.frames = [];
        state.totalFrames = 0;
        state.psiBounds = null;
        setControlsEnabled(false);
        Plotly.purge("psiPlot");
        Plotly.purge("muPlot");
        Plotly.purge("ivPlot");
        updateFramePositionLabel();
        setStatus("No runs available.");
      }
    }

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
        Plotly.purge("psiPlot");
        Plotly.purge("muPlot");
        Plotly.purge("ivPlot");
        state.psiBounds = null;
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
      renderIvCurve(state.iv, null);
      setControlsEnabled(true);
      openEventSource(runId);
      if (state.frames.length) {
        await loadFrame(0);
      } else {
        setStatus("Waiting for frames to arrive...");
      }
    }

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
      const psiData = psiMagnitude(frame.arrays);
      state.psiBounds = adaptivePsiBounds(state.psiBounds, frame.arrays);
      renderPsiHeatmap(psiData, state.psiBounds);
      const muBounds = state.timeline.stats.mu || null;
      renderMuHeatmap(frame.arrays.mu, muBounds);
      renderIvCurve(state.iv, frame.frame_index);
      els.frameValue.textContent = String(frame.frame_index);
      els.timeValue.textContent = formatNumber(frame.time_value);
      els.jeValue.textContent = formatNumber(frame.je);
      els.voltageValue.textContent = formatNumber(frame.voltage);
      setStatus(`Loaded frame ${frame.frame_index}.`);
      fillBuffer(targetFrameIndex);
    }

    function stopPlayback() {
      state.playing = false;
      els.playPause.textContent = "Play";
      if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
      }
    }

    function playbackStepSize() {
      const value = Number.parseInt(els.playbackStep.value, 10);
      return Number.isFinite(value) && value > 0 ? value : 1;
    }

    function nextFrameIndex(direction = 1) {
      const total = state.totalFrames || state.frames.length;
      if (!total) return 0;
      const raw = direction < 0
        ? state.currentFrameIndex - playbackStepSize()
        : state.currentFrameIndex + playbackStepSize();
      return ((raw % total) + total) % total;
    }

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

    els.syncRuns.addEventListener("click", () => {
      els.syncRuns.classList.add("spinning");
      loadRuns(state.runId).then(() => {
        setTimeout(() => els.syncRuns.classList.remove("spinning"), 400);
      }).catch((error) => {
        els.syncRuns.classList.remove("spinning");
        setStatus(error.message, true);
      });
    });

    els.frameSlider.addEventListener("input", () => {
      stopPlayback();
      loadFrame(Number(els.frameSlider.value)).catch((error) => setStatus(error.message, true));
    });

    els.prevFrame.addEventListener("click", () => {
      stopPlayback();
      loadFrame(nextFrameIndex(-1)).catch((error) => setStatus(error.message, true));
    });

    els.nextFrame.addEventListener("click", () => {
      stopPlayback();
      loadFrame(nextFrameIndex()).catch((error) => setStatus(error.message, true));
    });

    els.playPause.addEventListener("click", () => {
      if (state.playing) {
        stopPlayback();
      } else {
        startPlayback();
      }
    });

    window.addEventListener("beforeunload", closeEventSource);

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        loadRuns(state.runId).catch(() => {});
      }
    });

    setControlsEnabled(false);
    loadRuns().catch((error) => setStatus(error.message, true));
  </script>
</body>
</html>
```

- [ ] **Step 2: Update tests**

In `tests/test_api.py`, update any tests that check for Canvas-specific elements. Key changes:
- `'id="psiCanvas"'` → `'id="psiPlot"'`
- `'id="muCanvas"'` → `'id="muPlot"'`
- `'id="ivCanvas"'` → `'id="ivPlot"'`
- Remove references to canvas-specific functions like `drawHeatmapAxes`, `heatmapCanvasSize`, `canvas.style.aspectRatio`
- Replace with Plotly-specific checks: `"Plotly.react"`, `"plotly-div"`
- `nextFrameIndex` and `state.currentFrameIndex` should already be in the tests from the previous refactor

Read the test file first, then update all assertions that reference removed Canvas code.

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tdgl_data/static/viewer.html tests/test_api.py
git commit -m "feat: replace canvas with plotly for smooth rendering and add status badges"
```

---

### Task 3: Build, deploy, and test

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

- [ ] **Step 3: Port-forward and verify**

```bash
kubectl port-forward svc/data-viewer -n tdgl 8000:80
```

Open `http://localhost:8000/viewer`. Verify:
1. Run list shows status badges (created/running/completed)
2. Heatmaps render smoothly via Plotly (zoom, pan, hover)
3. I-V curve updates in real-time
4. Slider shows full range for runs with total_frames
5. Plotly.js loads from CDN (ensure cluster has internet access or bundle Plotly)

- [ ] **Step 4: Run generator and verify status badge updates**

```bash
kubectl create job tdgl-generator-test --from=job/tdgl-generator -n tdgl
```

Expected: Run appears with "running" badge, changes to "completed" when done.
