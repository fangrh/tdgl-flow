# Large Frame Set Performance Design

## Goal

Fix the viewer so it remains responsive with runs containing 1000+ frames, and
improve frame navigation for large datasets.

## Problem

Two bottlenecks block the UI when loading a run with many frames:

1. **Server**: The `/api/runs/{run_id}/timeline` endpoint reads every frame's Zarr
   arrays to compute global min/max stats. With 1000 frames, this is very slow.
2. **Client**: The viewer's `computePsiBounds` function fetches every frame from the
   API to compute `|psi|` colorbar bounds. The frame controls stay disabled until
   this completes, which may never happen for large runs.

## Design

### 1. Server: Frame-level stat caching

Add a `frame_stats` JSON column to the `Frame` model. When a frame is written (via
`append_frame` or demo creation), compute min/max for `psi_real`, `psi_imag`, and
`mu` and store them in this column.

The timeline endpoint aggregates stats from these cached column values instead of
reading Zarr arrays. This turns 1000 disk reads into a single SQL query.

**Changes:**

- `models.py`: Add `frame_stats` column (JSON, nullable) to `Frame`.
- `app.py`: Compute and store stats during frame write (both `api_append_frame`
  and `api_create_demo_run`).
- `app.py`: `api_timeline` reads `frame_stats` from database rows instead of calling
  `zarr_store.read_frame` in a loop.

### 2. Client: Adaptive colorbars

Remove the blocking `computePsiBounds` call. Instead:

- For `mu`: use the server-provided global stats from the timeline response (now fast
  thanks to cached stats).
- For `|psi|`: start with bounds from the first frame, then expand the range as the
  user navigates to more frames. The colorbar gradually grows to encompass the true
  global range.

Controls become enabled immediately after the first frame loads.

**Changes:**

- `viewer.html`: Remove `computePsiBounds` and `expandBounds` functions.
- `viewer.html`: Initialize `psiBounds` from the first frame's data.
- `viewer.html`: Expand `psiBounds` in `loadFrame` when a new frame's data exceeds
  the current bounds.
- `viewer.html`: Re-draw the psi colorbar whenever bounds expand.

### 3. Client: Frame buffer

Maintain a sliding window of pre-fetched frames around the current position
(current ± 5). When the user navigates, the target frame is likely already in
the buffer. A background fetch fills the buffer; frames far from the current
position are evicted.

**Buffer behavior:**

- Buffer size: 11 frames (current + 5 before + 5 after), clamped to frame range.
- On navigation: check buffer first, fetch only if missing.
- After a frame loads, trigger background fetch of nearest unbuffered neighbors.
- Evict frames more than 10 positions away from the current frame.
- Each buffered frame stores its parsed response data (arrays, metadata).

**Changes:**

- `viewer.html`: Add `frameBuffer` map to state (key: position, value: frame data).
- `viewer.html`: Add `fillBuffer(centerPosition)` async function.
- `viewer.html`: Modify `loadFrame` to check buffer before fetching.
- `viewer.html`: Modify playback to use buffered frames.

### 4. Client: Playback speed control

Add a speed selector alongside the existing step size control. Options: 1x (250ms),
2x (125ms), 4x (60ms), 8x (30ms). Default is 1x.

**Changes:**

- `viewer.html`: Add a `<select>` element for speed.
- `viewer.html`: Use selected speed to set `setInterval` delay in `startPlayback`.

## Error Handling

- If a buffered frame fetch fails, remove it from the buffer and log to status.
- If the timeline endpoint returns incomplete stats (no frames yet), fall back to
  per-frame colorbar bounds.
- Buffer fetches do not block navigation — if a frame isn't buffered, fetch it
  directly as before.

## Testing

Backend tests cover:

- Frame records store `frame_stats` with correct min/max values.
- Timeline endpoint returns aggregated stats from cached values (no Zarr reads).
- Existing frame append and demo run tests still pass.

Manual verification:

- Create a demo run with 1000 frames.
- Select the run — controls should become active within seconds.
- Navigate frames — heatmaps update, psi colorbar expands as new extremes are found.
- Play through frames at different speeds.
- Verify frame buffer makes navigation smooth (no loading spinner between nearby frames).
