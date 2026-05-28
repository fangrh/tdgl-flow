from IPython.display import display

import ipywidgets as widgets


class CppTdglViewer:
    def __init__(self, minio_url, bucket="tdgl-results", fps=10, speed=1):
        self._minio_url = minio_url
        self._bucket = bucket
        self._fps = fps
        self._speed = speed
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
        self._play_btn = widgets.Button(description="Play", icon="play")
        self._image = widgets.Image(format="png", width=760)
        self._viewer = None
        self._run_id = None

    def open(self, run_id):
        from cpp_tdgl_viewer_rust import CppTdglViewer
        self._viewer = CppTdglViewer(self._minio_url, self._bucket)
        self._viewer.open(run_id)
        self._run_id = run_id
        n_steps = self._viewer.get_step_count()
        self._step_dropdown.options = list(range(n_steps))
        if n_steps > 0:
            self._step_dropdown.value = 0
            self._update_frame_count(0)

    def _update_frame_count(self, step_idx):
        if self._viewer is not None:
            n_frames = self._viewer.get_frame_count(step_idx)
            self._frame_slider.max = max(0, n_frames - 1)
            self._frame_slider.value = 0

    def display(self):
        step_label = widgets.Label(value="Step: 0 / 0")
        frame_label = widgets.Label(value="Frame: 0")

        def on_step_change(change):
            step_idx = change["new"]
            step_label.value = f"Step: {step_idx}"
            self._update_frame_count(step_idx)

        def on_frame_change(change):
            frame_idx = change["new"]
            frame_label.value = f"Frame: {frame_idx}"

        def on_play(_):
            pass

        self._step_dropdown.observe(on_step_change, names="value")
        self._frame_slider.observe(on_frame_change, names="value")
        self._play_btn.on_click(on_play)

        ui = widgets.VBox([
            self._step_dropdown,
            widgets.HBox([self._play_btn, self._frame_slider, frame_label]),
            self._image,
        ])
        display(ui)

__all__ = ["CppTdglViewer"]
