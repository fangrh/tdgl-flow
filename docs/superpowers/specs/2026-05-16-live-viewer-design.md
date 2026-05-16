# Live Viewer Design (Sub-project 3)

## Goal

Enhance the existing heatmap viewer to receive real-time frame updates via SSE,
so new frames appear automatically as the data generator produces them.

## Architecture

The existing `viewer.html` opens an `EventSource` connection to
`/api/runs/{run_id}/events` (added in sub-project 1). When `frame_available`
events arrive, the viewer adds them to its local frame list and optionally
auto-navigates to the latest frame.

No new backend endpoints needed.

## Changes to viewer.html

### EventSource connection

When a run is selected (in `loadTimeline`), open an EventSource:

```javascript
const source = new EventSource(`/api/runs/${runId}/events`);
source.addEventListener("frame_available", (event) => { ... });
source.onerror = () => { source.close(); };
```

Store the EventSource in `state.eventSource`. Close it when switching runs or
on page unload.

### Frame arrival handler

On `frame_available` event:
- Parse the event data to get `frame_index`, `time_value`, `je`, `voltage`.
- If this frame_index is not already in `state.frames`, append it.
- Update the slider max to `state.frames.length - 1`.
- Update the frame count label.
- If auto-follow is enabled and the viewer is at the last frame, navigate to
  the new frame by calling `loadFrame`.

### Auto-follow toggle

Add an "Auto-follow" checkbox in the timeline controls, checked by default.
When checked, new frames auto-load. When unchecked, the user navigates manually.

### Cleanup

- Close EventSource when switching runs (in `loadTimeline` before opening new one).
- Close EventSource on page `beforeunload`.
- If EventSource errors (connection lost), show a status message and attempt
  reconnection after 5 seconds.

## Testing

- Test that viewer HTML contains `EventSource` and `auto-follow` references.
- Manual verification: start generator, open viewer, confirm frames appear
  automatically.
