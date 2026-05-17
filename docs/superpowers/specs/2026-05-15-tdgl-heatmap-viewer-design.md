# TDGL Heatmap Viewer Design

## Goal

Add a browser-based viewer for the existing TDGL data service so a developer can
inspect generated frame data as heatmaps without setting up a separate frontend
project.

## Scope

The viewer renders two heatmaps and one I-V plot for one selected frame:

- `|psi|`, computed in the browser as `sqrt(psi_real^2 + psi_imag^2)`.
- `mu`, read directly from the frame response.
- I-V curve, using `Je` on the x-axis and voltage on the y-axis.

The viewer does not render `psi_real` or `psi_imag` separately.

## Architecture

The FastAPI app serves a static viewer page at `/viewer`. Browser JavaScript uses
the existing frame API:

- `GET /api/runs`
- `GET /api/runs/{run_id}/timeline`
- `GET /api/runs/{run_id}/frames/{frame_index}`

A small demo endpoint creates a synthetic run with frames so the viewer has data
to display immediately. Production app startup remains explicit about schema
creation. A separate development factory creates the schema for local viewer
testing.

## UI Behavior

The page includes:

- Run selector.
- Create demo run button.
- Frame slider.
- Previous, play/pause, and next controls.
- Current frame, time, current density, and voltage readout.
- Two canvas heatmaps labeled `|psi|` and `mu`.
- One canvas I-V plot labeled `I-V curve`.
- A highlighted annotation dot on the I-V curve at the current frame's `Je`
  and voltage.
- Fixed colorbars for both `|psi|` and `mu` using global min/max over the full
  run, not per-frame min/max.
- Static tick marks on both colorbars and static x/y tick marks on both
  heatmaps.
- Desktop layout places `|psi|`, `mu`, and the I-V curve in one horizontal row.

Each heatmap uses its own color scale. `mu` uses timeline stats when available.
`|psi|` computes global magnitude bounds by reading the run's available frames
once and calculating `sqrt(psi_real^2 + psi_imag^2)` for each pixel. Both
heatmaps and their colorbars use these fixed global bounds for the entire run.
Colorbar ticks use five fixed values from the global bounds. Heatmap x/y ticks
use array index coordinates (`0`, midpoint, and max index) and remain fixed for
the selected run.

On wide screens the plot section uses three columns so both heatmaps and the
I-V plot are visible in one line. On narrow screens the panels stack vertically
to keep labels readable.

The I-V plot loads `/api/runs/{run_id}/iv` when a run is selected. The current
frame slider and play/pause state share the same frame index with the I-V
annotation dot, so the dot moves as the heatmaps update. Demo data uses a
nonlinear voltage curve so the plot has a visible shape during browser testing.

## Error Handling

Failed API calls show a concise inline status message. If a run has no available
frames, the slider is disabled and the heatmaps are cleared.

## Testing

Backend tests cover:

- `/viewer` returns HTML.
- The demo endpoint creates a run with readable frames.
- The demo endpoint creates a nonlinear I-V curve with readable I-V points.
- The development app factory creates a working schema for local browser use.
- The viewer contains colorbar canvases and global `|psi|` bound calculation.
- The viewer contains static colorbar tick and heatmap axis tick rendering.

Manual verification opens `/viewer`, creates a demo run, moves the slider, and
confirms both heatmaps update without color scale changes, both colorbars show
static ticks, both heatmaps show static x/y ticks, and the I-V annotation dot
moves along the curve.
