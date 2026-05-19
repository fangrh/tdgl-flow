# Continuous Sweep Save-Window Workflow Design

Date: 2026-05-19

## Status

Draft for user review.

## Goal

Fix the TDGL workflow correctness path before larger UI work:

1. Python and C++ workflows both run as continuous current sweeps.
2. `timing.save_time` means the final portion of each current step's stable window.
3. Saved data contains all frames produced inside each save window, not a single averaged frame.
4. Saved frames from all current steps form one continuous playback timeline.
5. Python output can be used as the reference behavior for C++ output.
6. The workflow UI can choose Python or C++ with minimal changes.
7. The viewer becomes an embedded run viewer selected from the workflow panel.

## Current Problems

The current implementation has inconsistent semantics across timing, runners, and viewer:

- `save_start` and `save_end` are centered in the stable window, but the desired behavior is the end of the stable window.
- The C++ runner effectively loops over current steps and stores one averaged frame per step.
- The Python runner currently uses a full `tdgl.SweepScenario` result but does not normalize storage to the same save-window timeline as C++.
- Viewer playback is mixed with run selection and deletion, which makes iframe embedding awkward.
- The workflow submit path is hard-coded to `cpp-tdgl-sim`.

## Non-Goals

- Do not redesign the full device/timing/simulate wizard.
- Do not add arbitrary user-submitted Argo workflow templates.
- Do not require a large C++ solver rewrite before correctness can improve.
- Do not make the viewer responsible for choosing or deleting runs.

## Core Timing Semantics

Each timing step represents one current transition and stable hold:

```text
ramp_start -> ramp_end       : Je ramps linearly from je_start to je_end
ramp_end   -> stable_end     : Je remains at je_end
save_start -> save_end       : final portion of the stable window
```

The timing builder must set:

```text
save_end = stable_end
save_start = stable_end - save_time
```

Validation rules:

- `je_step` must be non-zero.
- `ramp_time` must be greater than or equal to zero.
- `stable_time` must be greater than zero.
- `save_time` must be greater than zero and less than or equal to `stable_time`.

The timing schedule still stores global physical times for `ramp_start`, `ramp_end`, `stable_end`, `save_start`, and `save_end`.

## Storage Timeline Semantics

The persisted `time_value` is the playback time across saved windows only.

Example: if each current step saves 10 time units, then:

```text
Je step 1 saved frames: time_value 0..10
Je step 2 saved frames: time_value 10..20
Je step 3 saved frames: time_value 20..30
```

This is intentionally different from full physical solver time, because ramp and non-saved stable intervals are not part of the playback timeline.

For each uploaded frame:

- `frame_index` is globally increasing.
- `time_value` is continuous saved-window playback time.
- `je` is the current step's target current, `je_end`.
- `voltage` is computed from the configured probe indices using the agreed sign convention.
- `frame_stats.physical_time` stores the solver/global physical time.
- `frame_stats.local_time` stores solver-local time when applicable.
- `frame_stats.save_window_index` stores the current-step index.
- `frame_stats.window_frame_index` stores the frame index within the current save window.
- `frame_stats.save_start` and `frame_stats.save_end` store the global physical save window.
- `frame_stats.voltage_valid` records whether voltage was computed from at least two probes.

IV points are separate from frames. Each current step produces one IV point by averaging frame voltages over that current step's saved window.

## Runner Design

### Shared Requirements

Both runners must:

- Read the same `timing.json` schedule.
- Treat the sweep as continuous.
- Upload every solver-saved frame inside each save window.
- Map saved frames to the continuous saved-window `time_value`.
- Create one IV point per current step from the average save-window voltage.
- Mark the run `failed` if a required save window has no saved frames, rather than uploading synthetic all-zero data.

### Python Runner

The Python runner is the reference behavior.

It should run a continuous sweep using py-tdgl and map solver outputs back to the timing schedule:

1. Build the device from `mesh_meta.json`.
2. Build a continuous scenario from all timing steps.
3. Run py-tdgl once for the full schedule where feasible.
4. Filter saved solution frames whose physical time falls inside each step's save window.
5. Upload those frames using the saved-window playback timeline.
6. Average each save window's frame voltages into one IV point.

If py-tdgl output cadence cannot be controlled exactly by window boundaries, the runner should still use the actual saved frames and record their physical times in `frame_stats`.

### C++ Runner

The C++ runner may keep the lower-risk implementation model:

```text
for each timing step:
    run solver for this step
    pass previous output as restart input
    read all HDF5 frames in this step's save window
    upload those frames with continuous saved-window time_value
    write one averaged IV point for the step
```

This is process-stepwise but state-continuous. It satisfies the user-facing continuous sweep semantics as long as:

- the restart carries the previous final solver state into the next current step;
- the current ramps from `je_start` to `je_end`;
- only save-window frames are uploaded;
- uploaded `time_value` continues across save windows.

A later C++ solver enhancement may move the whole timing schedule into one native solver process, but that is not required for this correction pass.

## Voltage Semantics

Python and C++ must use the same voltage convention:

```text
voltage = mu[probe_indices[1]] - mu[probe_indices[0]]
```

If this sign disagrees with py-tdgl conventions during reference comparison, the convention should be changed in one place and covered by tests.

When fewer than two probes exist:

- keep schema compatibility by storing `voltage = 0.0` if the database requires a float;
- set `frame_stats.voltage_valid = false`;
- do not include invalid frames in IV averaging.

## Workflow UI Design

The existing `/simulate` page gets a minimal solver selector:

- `C++ tdgl` maps to `solver_type = "cpp-tdgl"` and template `cpp-tdgl-sim`.
- `Python tdgl` maps to `solver_type = "py-tdgl"` and template `py-tdgl-sim`.

The submit path stores the selected `solver_type` on the run and chooses the Argo template from a server-side whitelist. The API must not accept arbitrary template names.

The workflow panel owns run management:

- list recent runs;
- show solver type, status, created time, and frame count when available;
- click a run to display it in the embedded viewer iframe;
- delete runs from this panel;
- clear or switch the iframe if the selected run is deleted.

No new nginx route is required for this pass.

## Embedded Viewer Design

The viewer becomes focused on displaying one selected run.

Viewer URL behavior:

- support a run-specific URL such as `/tdgl/viewer?run_id=<run_id>`;
- when `run_id` is present, load only that run;
- when no `run_id` is present, show a small empty state such as `No run selected`;
- do not render the global dataset list;
- do not render delete controls.

Playback behavior:

- connect to SSE for the selected run;
- play frames in increasing `frame_index`;
- if the next frame is not available and the run is still active, keep playback state as playing and display a waiting message;
- when SSE announces the missing frame, continue playback;
- update slider bounds as frames arrive;
- keep auto-follow separate from play/pause.

Plot behavior:

- per-frame plots use the selected frame's arrays and metadata;
- IV plot uses averaged `iv_points`, one point per current step;
- the current frame voltage remains visible in metadata.

## Data Service Expectations

The existing per-site Zarr frame storage remains the primary frame storage.

Needed behavior:

- frames can be appended in global `frame_index` order;
- timeline endpoints expose all saved frames, not just one frame per current step;
- IV endpoints expose one averaged point per current step;
- run deletion removes run metadata, frame metadata, IV points, and Zarr data;
- SSE emits `frame_available` for every uploaded frame.

If existing schema requires non-null floats for voltage, use `0.0` plus `frame_stats.voltage_valid=false` for invalid voltage cases.

## Argo Submission

Both HTML submit and `/api/workflows/submit` use the same whitelist:

```python
SOLVER_WORKFLOWS = {
    "cpp-tdgl": "cpp-tdgl-sim",
    "py-tdgl": "py-tdgl-sim",
}
```

Submission writes:

- `runs.solver_type`;
- full device params including mesh;
- full timing params including schedule;
- solver options;
- estimated total frames if known, otherwise leave it as an estimate and rely on actual uploaded frames.

The generated Argo workflow name should include the selected solver prefix.

## Testing Plan

### Timing Tests

- `save_start == stable_end - save_time`.
- `save_end == stable_end`.
- global physical timing fields are continuous across steps.
- invalid `save_time > stable_time` raises an error.
- invalid `je_step == 0` raises an error.

### Submit Tests

- `/simulate` renders the solver selector.
- HTML submit with Python solver uses `py-tdgl-sim`.
- API submit with `solver_type="py-tdgl"` uses `py-tdgl-sim`.
- unknown solver type returns a client error.
- run records store the selected solver type.

### Runner Tests

- C++ HDF5 reader returns every frame inside the save window.
- C++ HDF5 reader rejects an empty save window.
- uploaded frame `time_value` concatenates saved windows into one continuous timeline.
- IV point voltage is the average of valid frame voltages in one save window.
- Python and C++ use the same probe voltage sign convention.

### Viewer Tests

- viewer supports `run_id` in the URL.
- viewer does not render a global run list.
- viewer does not render delete controls.
- playback waits when the next frame is missing and resumes when SSE announces it.
- workflow panel can select a run and update the iframe URL.
- workflow panel owns delete behavior.

## Implementation Order

1. Fix timing semantics and tests.
2. Add solver selector and whitelist-based submit behavior.
3. Refactor runner save-window upload semantics.
4. Separate IV averaging from frame upload.
5. Refactor viewer into run-specific embedded mode.
6. Add workflow-panel run selection and delete behavior.
7. Add Python/C++ comparison fixtures for a small deterministic device.

## Open Decisions

No unresolved product decisions remain for this spec. Implementation may still uncover py-tdgl API details that affect how saved frames are extracted, but the storage semantics must remain as defined here.
