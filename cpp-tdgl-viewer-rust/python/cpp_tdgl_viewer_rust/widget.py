"""ipywidgets wrapper for the cpp-tdgl Rust viewer."""

import threading
import time

from IPython.display import display

import ipywidgets as widgets


class CppTdglViewer:
    """Interactive Jupyter widget for browsing cpp-tdgl simulation results.

    Usage::

        viewer = CppTdglViewer("http://localhost:30900")
        viewer.open(run_id="abc123")
        viewer.display()
    """

    def __init__(self, minio_url, bucket="tdgl-results", fps=10, speed=1):
        self._minio_url = minio_url
        self._bucket = bucket
        self._fps = fps
        self._speed = speed
        self._viewer = None
        self._run_id = None
        self._timer = None

        self._step_dropdown = widgets.Dropdown(
            options=[],
            description="Step:",
            disabled=False,
        )
        self._frame_slider = widgets.IntSlider(
            value=0,
            min=0,
            max=100,
            step=1,
            description="Frame:",
            continuous_update=False,
        )
        self._play_btn = widgets.Button(
            description="Play", icon="play", button_style="success",
        )
        self._image = widgets.Image(format="png", width=760)

    def open(self, run_id):
        """Open a simulation run by ID (reads from MinIO)."""
        from cpp_tdgl_viewer_rust import CppTdglViewer as _RustViewer

        self._viewer = _RustViewer(self._minio_url, self._bucket)
        self._viewer.open(run_id)
        self._run_id = run_id

        n_steps = self._viewer.get_step_count()
        self._step_dropdown.options = list(range(n_steps))
        if n_steps > 0:
            self._step_dropdown.value = 0
            self._update_frame_count(0)
            self._render_frame(0, 0)

    def display(self):
        """Render the interactive widget in the notebook."""
        info_label = widgets.Label(value="")

        def on_step_change(change):
            step_idx = change["new"]
            self._update_frame_count(step_idx)
            self._render_frame(step_idx, 0)
            self._update_info(info_label, step_idx, 0)

        def on_frame_change(change):
            frame_idx = change["new"]
            step_idx = self._step_dropdown.value
            self._render_frame(step_idx, frame_idx)
            self._update_info(info_label, step_idx, frame_idx)

        def on_play(_):
            if self._timer is not None:
                self._stop_playback()
                self._play_btn.description = "Play"
                self._play_btn.icon = "play"
                self._play_btn.button_style = "success"
            else:
                self._start_playback()
                self._play_btn.description = "Pause"
                self._play_btn.icon = "pause"
                self._play_btn.button_style = "warning"

        self._step_dropdown.observe(on_step_change, names="value")
        self._frame_slider.observe(on_frame_change, names="value")
        self._play_btn.on_click(on_play)

        controls = widgets.HBox([
            self._step_dropdown,
            self._play_btn,
            self._frame_slider,
        ])
        ui = widgets.VBox([controls, info_label, self._image])
        display(ui)

    # ── Private helpers ──────────────────────────────────────────────────

    def _update_frame_count(self, step_idx):
        if self._viewer is not None:
            n_frames = self._viewer.get_frame_count(step_idx)
            self._frame_slider.max = max(0, n_frames - 1)
            self._frame_slider.value = 0

    def _render_frame(self, step_idx, frame_idx):
        if self._viewer is None:
            return
        try:
            img_bytes = self._viewer.render_frame(step_idx, frame_idx)
            self._image.value = img_bytes
        except Exception as e:
            # Frame not available yet (simulation still running)
            pass

    def _update_info(self, label, step_idx, frame_idx):
        if self._viewer is None:
            return
        try:
            je = self._viewer.get_je(step_idx)
            n_sites = self._viewer.get_mesh_points()
            t_min = self._viewer.get_min_time(step_idx)
            t_max = self._viewer.get_max_time(step_idx)
            n_frames = self._viewer.get_frame_count(step_idx)
            label.value = (
                f"Step {step_idx} | Je={je:.2f} | "
                f"t=[{t_min:.0f}, {t_max:.0f}] | "
                f"Frame {frame_idx}/{n_frames} | "
                f"{n_sites} sites"
            )
        except Exception:
            label.value = f"Step {step_idx} | Frame {frame_idx}"

    def _start_playback(self):
        def loop():
            while self._timer is not None:
                frame = self._frame_slider.value + 1
                if frame > self._frame_slider.max:
                    frame = 0
                self._frame_slider.value = frame
                time.sleep(1.0 / self._fps)

        self._timer = threading.Thread(target=loop, daemon=True)
        self._timer.start()

    def _stop_playback(self):
        t = self._timer
        self._timer = None
        if t is not None:
            t.join(timeout=2.0)


__all__ = ["CppTdglViewer"]
