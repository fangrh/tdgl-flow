# Real-Time I-V Plot Update

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the I-V curve update in real-time as new frames arrive via SSE, without re-fetching from the API.

**Architecture:** The SSE `frame_available` event already carries `je` and `voltage`. The viewer will push these into `state.iv` as they arrive and redraw the I-V plot. The client-side frame buffer (BUFFER_RADIUS=5) already limits memory — the OOM fix was increasing the server pod to 1Gi (already deployed).

**Tech Stack:** Vanilla JS, FastAPI SSE, existing viewer state model

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `tdgl_data/static/viewer.html` | Modify | Update SSE handler to push I-V points and redraw |

---

### Task 1: Add real-time I-V point updates to SSE handler

**Files:**
- Modify: `tdgl_data/static/viewer.html` (SSE `frame_available` listener, ~line 597)

- [ ] **Step 1: Update the `frame_available` event handler to push I-V points**

In the `openEventSource` function, inside the `frame_available` event listener, add the new I-V point to `state.iv` and redraw the I-V plot.

Find this block inside the `frame_available` listener:

```javascript
        state.frames.push({
          frame_index: data.frame_index,
          time_value: data.time_value,
          je: data.je,
          voltage: data.voltage,
          status: "available",
        });
        els.frameSlider.max = String(state.frames.length - 1);
        updateFramePositionLabel(state.frameIndex);
        if (els.autoFollow.checked) {
          loadFrame(state.frames.length - 1);
        }
        setStatus(`Frame ${data.frame_index} arrived (${data.frame_count} total).`);
```

Replace with:

```javascript
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
```

The change adds `state.iv.push(...)` to append the new I-V point, and adds an `else` branch to redraw the I-V plot even when auto-follow is off.

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: ALL 78 tests PASS

- [ ] **Step 3: Rebuild and push image**

```bash
podman build -t ghcr.io/fangrh/tdgl-flow:latest .
podman push ghcr.io/fangrh/tdgl-flow:latest
```

- [ ] **Step 4: Redeploy**

```bash
kubectl rollout restart deployment data-viewer -n tdgl
kubectl wait --for=condition=ready pod -l app=data-viewer -n tdgl --timeout=60s
```

- [ ] **Step 5: Commit**

```bash
git add tdgl_data/static/viewer.html
git commit -m "feat: update I-V plot in real-time as frames arrive via SSE"
```

---

### Task 2: Verify with generator

- [ ] **Step 1: Port-forward and open viewer**

```bash
kubectl port-forward svc/data-viewer -n tdgl 8000:80
```

Open `http://localhost:8000/viewer` in browser.

- [ ] **Step 2: Run generator and verify I-V updates live**

```bash
kubectl create job tdgl-generator-test --from=job/tdgl-generator -n tdgl
```

Expected: As frames stream in, the I-V curve plot should update in real-time — each new point appears on the curve without needing to click Refresh or re-select the run.
