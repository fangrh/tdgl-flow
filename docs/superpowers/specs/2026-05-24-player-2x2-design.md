# Player 2x2 Design

## Goal

Create a second player layout with a 2x2 grid: order parameter and mu on top, V-vs-t and I-V on the bottom. Implemented as a subclass of the existing player.

## Layout

```
+-------------------+-------------------+
| |psi| (inferno)   | mu (RdBu)        |
+-------------------+-------------------+
| V vs t (dot)      | I-V curve (dot)   |
+-------------------+-------------------+
```

Controls below: play/pause, frame slider, FPS, speed, I-V batch size — same as existing player.

## New file

`src/tdgl_sdk/viewer/_player_2x2.py`

### Player2x2 class

- Inherits from `RealtimeTDGLWidgetPlayer`
- Overrides `_build_output_area()` to create a 2x2 `ipywidgets.GridBox`
- Top-left: `|psi|` image via existing render logic
- Top-right: `mu` image via existing render logic
- Bottom-left: V-vs-t matplotlib plot (new)
- Bottom-right: I-V curve via existing logic

### V-vs-t panel

- X-axis: time `t` from `IVCache`
- Y-axis: voltage `V` from `IVCache`
- Blue line for full trace
- White dot on dark background at current playback position
- Updates every frame advance

### Factory function

`create_player_2x2(h5_url, timing_steps=None, average_time=50.0, debug=False, **s3_kwds)`

Same signature and behavior as `create_player()`.

## Changes to existing files

- `src/tdgl_sdk/viewer/__init__.py`: add `create_player_2x2` export

## What is NOT changing

- Existing player (`_player.py`) untouched
- I-V cache (`_iv.py`) untouched
- No new controls or parameters
