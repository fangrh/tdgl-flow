import json
import threading
import time

import ipywidgets as widgets
from IPython.display import display

from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as _RustViewer

FRAME_W = 760
PREFETCH_AHEAD = 5  # Number of frames to prefetch ahead


class TdglViewer:
    """Interactive 2x2 TDGL simulation viewer with ipywidgets UI."""

    def __init__(self, minio_url="http://localhost:30900"):
        self._rust = _RustViewer(minio_url)
        self._playing = False
        self._stop = threading.Event()
        self._thread = None
        self._fps = 10
        self._iv_monitor_thread = None

    def list_runs(self):
        return self._rust.list_runs()

    def open(self, run_id=None, run_index=None):
        self._rust.open(run_id=run_id, run_index=run_index)

    def total_frames(self):
        return self._rust.total_frames()

    def render_frame(self, frame_idx):
        return self._rust.render_frame(frame_idx)

    def start_iv_scan(self, average_time=None):
        self._rust.start_iv_scan(average_time=average_time)

    def stop_iv_scan(self):
        self._rust.stop_iv_scan()

    def get_iv_progress(self):
        return json.loads(self._rust.get_iv_progress())

    def get_timing_steps(self):
        return json.loads(self._rust.get_timing_steps())

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
        avg_slider = widgets.FloatSlider(
            value=0.5, min=0.1, max=1.0, step=0.05, description="Avg",
            continuous_update=False,
            layout=widgets.Layout(width="180px"),
        )
        iv_status = widgets.Label(value="I-V: idle")
        status = widgets.Label(value="ready")

        # Frame cache: only keep PREFETCH_AHEAD frames around current position
        _cache = {}
        _cache_lock = threading.Lock()
        _current_frame = [0]

        def _prefetch(center):
            """Prefetch frames around center, evict old ones."""
            total = self._rust.total_frames()
            for i in range(max(0, center - 1), min(total, center + PREFETCH_AHEAD + 1)):
                with _cache_lock:
                    if i not in _cache:
                        _cache[i] = self._rust.render_frame(i)
            # Evict frames far from center
            with _cache_lock:
                to_evict = [k for k in _cache if abs(k - center) > PREFETCH_AHEAD + 2]
                for k in to_evict:
                    del _cache[k]

        def _render(idx):
            idx = max(0, min(idx, self._rust.total_frames() - 1))
            _current_frame[0] = idx
            with _cache_lock:
                png = _cache.get(idx)
            if png is None:
                png = self._rust.render_frame(idx)
                with _cache_lock:
                    _cache[idx] = png
            image.value = png
            slider.value = idx
            time_label.value = f"frame {idx} / {self._rust.total_frames() - 1}"
            status.value = f"frame {idx}/{self._rust.total_frames() - 1}"
            # Evict old frames
            threading.Thread(target=_prefetch, args=(idx,), daemon=True).start()

        # Prefetch initial frames
        _prefetch(0)
        _render(0)

        # Start IV scanner if timing steps available
        def _start_iv(average_time):
            try:
                self._rust.start_iv_scan(average_time=average_time)
                iv_status.value = "I-V: scanning..."
            except Exception as e:
                iv_status.value = f"I-V: {e}"

        def _monitor_iv():
            """Poll IV progress and update status."""
            while not self._stop.is_set():
                try:
                    prog = json.loads(self._rust.get_iv_progress())
                    done = prog.get("done", False)
                    completed = prog.get("steps_completed", 0)
                    total_s = prog.get("steps_total", 0)
                    if done:
                        iv_status.value = f"I-V: done ({completed}/{total_s} steps)"
                        break
                    else:
                        iv_status.value = f"I-V: {completed}/{total_s} steps..."
                except Exception:
                    pass
                self._stop.wait(0.5)

        def on_avg_change(change):
            """User changed average_time — restart IV scan."""
            avg = change["new"]
            self._rust.stop_iv_scan()
            _start_iv(avg)
            # Restart monitor
            if self._iv_monitor_thread and self._iv_monitor_thread.is_alive():
                pass  # Monitor will pick up new scanner
            else:
                self._iv_monitor_thread = threading.Thread(
                    target=_monitor_iv, daemon=True
                )
                self._iv_monitor_thread.start()

        def on_dropdown(change):
            idx = change["new"]
            self._stop_playback()
            self._rust.stop_iv_scan()
            self._rust.open(run_index=idx)
            total = self._rust.total_frames()
            slider.max = max(0, total - 1)
            slider.value = 0
            _cache.clear()
            _prefetch(0)
            _render(0)
            _start_iv(avg_slider.value)
            self._iv_monitor_thread = threading.Thread(
                target=_monitor_iv, daemon=True
            )
            self._iv_monitor_thread.start()

        def on_slider(change):
            _render(change["new"])

        def on_play(_):
            if self._playing:
                self._stop_playback()
                play_btn.description = "Play"
                play_btn.icon = "play"
            else:
                self._start_playback(slider, image, status, play_btn, _cache, _cache_lock)

        def on_fps(change):
            self._fps = change["new"]

        run_dropdown.observe(on_dropdown, names="value")
        slider.observe(on_slider, names="value")
        play_btn.on_click(on_play)
        fps_slider.observe(on_fps, names="value")
        avg_slider.observe(on_avg_change, names="value")

        # Auto-start IV scan
        _start_iv(0.5)
        self._iv_monitor_thread = threading.Thread(
            target=_monitor_iv, daemon=True
        )
        self._iv_monitor_thread.start()

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, slider, time_label]),
            widgets.HBox([fps_slider, avg_slider, iv_status]),
            image,
        ])
        display(ui)

    def _start_playback(self, slider, image, status, play_btn, cache, cache_lock):
        self._playing = True
        self._stop.clear()
        play_btn.description = "Pause"
        play_btn.icon = "pause"
        self._thread = threading.Thread(
            target=self._loop,
            args=(slider, image, status, cache, cache_lock),
            daemon=True,
        )
        self._thread.start()

    def _stop_playback(self):
        self._playing = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(self, slider, image, status, cache, cache_lock):
        while not self._stop.is_set():
            current = slider.value
            next_frame = current + 1
            total = self._rust.total_frames()
            if next_frame >= total:
                self._stop.set()
                break

            # Check cache, evict old, prefetch ahead
            with cache_lock:
                png = cache.get(next_frame)
                # Evict old frames
                to_evict = [k for k in cache if k < current - 2]
                for k in to_evict:
                    del cache[k]

            if png is None:
                t0 = time.perf_counter()
                png = self._rust.render_frame(next_frame)
                with cache_lock:
                    cache[next_frame] = png
            else:
                t0 = time.perf_counter()

            image.value = png
            slider.value = next_frame

            # Prefetch next few frames in background
            threading.Thread(
                target=self._prefetch_range,
                args=(next_frame + 1, min(total, next_frame + PREFETCH_AHEAD), cache, cache_lock),
                daemon=True,
            ).start()

            elapsed = time.perf_counter() - t0
            interval = 1.0 / self._fps
            remaining = max(0.0, interval - elapsed)
            self._stop.wait(remaining)

    def _prefetch_range(self, start, end, cache, cache_lock):
        for i in range(start, end):
            with cache_lock:
                if i in cache:
                    continue
            png = self._rust.render_frame(i)
            with cache_lock:
                cache[i] = png

    def get_iv_data(self):
        """Get current I-V data as dict."""
        prog = json.loads(self._rust.get_iv_progress())
        return prog

    def __del__(self):
        self._stop_playback()
