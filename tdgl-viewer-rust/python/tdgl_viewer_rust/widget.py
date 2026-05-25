import threading
import time
import ipywidgets as widgets
from IPython.display import display

from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as _RustViewer

FRAME_W = 760


class TdglViewer:
    """Interactive 2x2 TDGL simulation viewer with ipywidgets UI."""

    def __init__(self, minio_url="http://localhost:30900"):
        self._rust = _RustViewer(minio_url)
        self._playing = False
        self._stop = threading.Event()
        self._thread = None
        self._fps = 10

    def list_runs(self):
        """List available runs. Returns list of display labels."""
        return self._rust.list_runs()

    def open(self, run_id=None, run_index=None):
        """Open a run by ID or index."""
        self._rust.open(run_id=run_id, run_index=run_index)

    def display(self):
        """Display interactive viewer in Jupyter."""
        runs = self._rust.list_runs()
        if not runs:
            print("No runs found.")
            return

        run_dropdown = widgets.Dropdown(
            options=[(label, i) for i, label in enumerate(runs)],
            description="Run:",
            layout=widgets.Layout(width="clamp(400px, 60vw, 800px)"),
        )

        # Open first run by default
        self._rust.open(run_index=0)
        total = self._rust.total_frames()

        image = widgets.Image(format="png", width=FRAME_W)
        play_btn = widgets.Button(
            description="Play", icon="play",
            layout=widgets.Layout(width="92px"),
        )
        slider = widgets.IntSlider(
            value=0, min=0, max=max(0, total - 1), step=1,
            continuous_update=False,
            layout=widgets.Layout(width="500px"),
        )
        time_label = widgets.Label(
            value=f"frame 0 / {max(0, total - 1)}",
            layout=widgets.Layout(width="220px"),
        )
        fps_slider = widgets.IntSlider(
            value=10, min=1, max=30, description="FPS",
            continuous_update=False,
            layout=widgets.Layout(width="180px"),
        )
        speed_input = widgets.IntText(
            value=1, description="Speed",
            layout=widgets.Layout(width="120px"),
        )
        status = widgets.Label(value="ready")

        # Render first frame
        if total > 0:
            png = self._rust.render_frame(0)
            image.value = bytes(png)
            time_label.value = f"frame 0 / {total - 1}"

        def _render(idx):
            idx = max(0, min(idx, self._rust.total_frames() - 1))
            png = self._rust.render_frame(idx)
            image.value = bytes(png)
            slider.value = idx
            time_label.value = f"frame {idx} / {self._rust.total_frames() - 1}"
            status.value = f"frame {idx}/{self._rust.total_frames() - 1}"

        def on_dropdown(change):
            idx = change["new"]
            self._stop_playback()
            self._rust.open(run_index=idx)
            total = self._rust.total_frames()
            slider.max = max(0, total - 1)
            slider.value = 0
            _render(0)

        def on_slider(change):
            _render(change["new"])

        def on_play(_):
            if self._playing:
                self._stop_playback()
                play_btn.description = "Play"
                play_btn.icon = "play"
            else:
                self._start_playback(slider, image, status, play_btn)

        def on_fps(change):
            self._fps = change["new"]

        run_dropdown.observe(on_dropdown, names="value")
        slider.observe(on_slider, names="value")
        play_btn.on_click(on_play)
        fps_slider.observe(on_fps, names="value")

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, slider, time_label]),
            widgets.HBox([fps_slider, speed_input, status]),
            image,
        ])
        display(ui)

    def _start_playback(self, slider, image, status, play_btn):
        self._playing = True
        self._stop.clear()
        play_btn.description = "Pause"
        play_btn.icon = "pause"
        self._thread = threading.Thread(
            target=self._loop, args=(slider, image, status), daemon=True,
        )
        self._thread.start()

    def _stop_playback(self):
        self._playing = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(self, slider, image, status):
        while not self._stop.is_set():
            current = slider.value
            next_frame = current + 1
            total = self._rust.total_frames()
            if next_frame >= total:
                self._stop.set()
                break
            t0 = time.perf_counter()
            png = self._rust.render_frame(next_frame)
            image.value = bytes(png)
            slider.value = next_frame
            elapsed = time.perf_counter() - t0
            interval = 1.0 / self._fps
            remaining = max(0.0, interval - elapsed)
            self._stop.wait(remaining)

    def get_iv_data(self):
        """Returns dict with I, V arrays. Not yet implemented."""
        raise NotImplementedError("IV data retrieval coming in next iteration")

    def __del__(self):
        self._stop_playback()