# Playback Speed Control & UI Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Stop button with a speed control widget. Speed = positive integer that skips frames during playback. Slider range stays the same, I-V logic unchanged.

**Architecture:** Speed is stored as `self._speed` on `RealtimeTDGLWidgetPlayer`. The `_loop()` increments by `speed` instead of 1. The buffer pre-caches ahead by speed. When speed changes, pause playback, clear the buffer, re-buffer, and optionally resume.

**Tech Stack:** ipywidgets (IntText, Button), existing threading model

---

### Task 1: Remove Stop button, add Speed widget

**Files:**
- Modify: `src/tdgl_sdk/viewer/_player.py` (RealtimeTDGLWidgetPlayer)

- [ ] **Step 1: Replace Stop button with Speed control in `__init__`**

Remove `self.stop_button` widget and its click handler. Add `self._speed = 1` attribute and a `widgets.IntText` for speed input.

In `__init__`, replace the stop_button block with:

```python
self._speed = 1
self.play_button = widgets.Button(
    description="Play", icon="play", layout=widgets.Layout(width="92px")
)
self.speed_input = widgets.IntText(
    value=1,
    description="Speed",
    layout=widgets.Layout(width="130px"),
)
```

Remove the line:
```python
self.stop_button.on_click(self.stop)
```

Add speed observer:
```python
self.speed_input.observe(self._on_speed, names="value")
```

Update the UI layout — remove stop_button from HBox:
```python
self.ui = widgets.VBox([
    widgets.HBox([self.play_button, self.speed_input, self.slider, self.time_label]),
    widgets.HBox([self.fps, self.status]),
    self.image,
])
```

- [ ] **Step 2: Implement `_on_speed` callback**

When speed changes: pause, clear buffer, set new speed, re-buffer current frame, optionally resume.

```python
def _on_speed(self, change):
    new_speed = max(1, int(change.get("new", 1)))
    was_playing = self.playing
    self.pause()
    self._speed = new_speed
    self.buffer.clear()
    self.show(self.current, wait=False)
    if was_playing:
        self.play()
```

- [ ] **Step 3: Add `Buffer.clear()` method**

In `src/tdgl_sdk/viewer/_render.py`, add to `RealtimeFrameBuffer`:

```python
def clear(self):
    with self.lock:
        self.frames.clear()
```

- [ ] **Step 4: Remove `stop()` method, keep only `toggle`/`play`/`pause`**

Remove the `stop()` method entirely (it was just pause + show(0)). The play button already toggles between play/pause, which is sufficient.

- [ ] **Step 5: Update `_loop()` to skip frames**

Change the frame increment from 1 to `self._speed`:

```python
def _loop(self):
    while not self.stop_event.is_set():
        if self._debug:
            self._debug.log("loop_tick", current=self.current,
                            speed=self._speed, total=self.total)
        next_frame = self.current + self._speed

        if next_frame >= self.total:
            if self.live:
                self._refresh_total()
                if next_frame >= self.total:
                    self.status.value = f"LIVE frame {self.current} — waiting..."
                    self.stop_event.wait(2.0)
                    continue
            else:
                # Clamp to last frame instead of overshooting
                next_frame = self.total - 1
                if next_frame <= self.current:
                    self.show(next_frame, wait=False)
                    self.pause()
                    return

        t0 = time.perf_counter()
        self.show(next_frame, wait=False)
        elapsed = time.perf_counter() - t0
        self.stop_event.wait(max(0.0, 1.0 / max(1, self.fps.value) - elapsed))
```

- [ ] **Step 6: Update `keep_near` to account for speed**

In `_render.py`, the `RealtimeFrameBuffer.keep_near` method prunes frames too aggressively when skipping. Currently it keeps `center - 2` onward. Change it to keep a wider window:

```python
def keep_near(self, center):
    center = int(center)
    lo = max(0, center - 2)
    with self.lock:
        for key in list(self.frames):
            if key < lo:
                del self.frames[key]
```

This is already fine — since we skip frames, old frames below `center - 2` get naturally pruned. No change needed here.

- [ ] **Step 7: Update `debug_player()` — remove `stop()` call**

In `debug_player()`, the final "Stop" test step calls `player.stop()`. Since stop is removed, change it to:

```python
# Pause + reset
try:
    player.pause()
    player.show(0)
    status = player.get_status()
    steps.append({"action": "stop", "ok": True, "status": status})
except Exception as exc:
    steps.append({"action": "stop", "ok": False, "error": str(exc)})
    errors.append(f"stop failed: {exc}")
```

- [ ] **Step 8: Update `StreamingTDGLPlayer.stop()` — remove inner player stop**

In `StreamingTDGLPlayer.stop()`, it calls `self._player.pause()` which is fine. Remove any reference to removed methods:

```python
def stop(self):
    self._stop_event.set()
    if self._player is not None:
        self._player.pause()
        self._player.iv_cache.stop()
    self.status_label.value = "stopped"
```

This is already correct — no change needed.

- [ ] **Step 9: Commit**

```bash
git add src/tdgl_sdk/viewer/_player.py src/tdgl_sdk/viewer/_render.py
git commit -m "feat: replace Stop button with Speed control (frame skip during playback)"
```

## Files Modified

| File | Change |
|------|--------|
| `src/tdgl_sdk/viewer/_player.py` | Remove stop_button/stop(), add speed_input/_on_speed/_speed, update _loop() to skip frames, update debug_player() |
| `src/tdgl_sdk/viewer/_render.py` | Add `clear()` method to `RealtimeFrameBuffer` |

## What Stays Unchanged

- `IVCache` / `_iv.py` — I-V logic is frame-index-based, unaffected by speed
- `_render.py` render functions — take frame index directly, speed doesn't matter
- `_draw_iv()` — white dot position follows `self.current`, which jumps by speed. This is correct behavior — dot just moves faster.
- Slider range (`max = total - 1`) — unchanged. Speed only affects playback stepping.
- `StreamingTDGLPlayer` — no changes needed (it delegates to inner player)

## Verification

1. Open notebook, run player. Verify: Play button toggles play/pause, no Stop button visible.
2. Set Speed to 1 — plays every frame normally.
3. Set Speed to 5 — playback jumps 5 frames at a time, slider still shows correct frame index, total range unchanged.
4. Change speed mid-playback — should pause briefly, clear buffer, resume at new speed.
5. Set Speed to 0 or negative — should clamp to 1.
6. I-V curve should still render correctly (dot jumps faster but curve unchanged).
7. Run `debug_player()` — should pass with the updated stop test.
