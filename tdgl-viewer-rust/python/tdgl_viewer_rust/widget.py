import json
import threading
import time

import ipywidgets as widgets
from IPython.display import display

from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as _RustViewer

FRAME_W = 760
PREFETCH_AHEAD = 8


class TdglViewer:
    """Interactive 2x2 TDGL simulation viewer with time-based slider and live refresh."""

    def __init__(
        self,
        minio_url="http://localhost:30900",
        fps=10,
        speed=1,
        average_time=0.5,
        show_vt_dot=True,
        refresh_interval=5.0,
    ):
        self._rust = _RustViewer(minio_url)
        self._playing = False
        self._stop = threading.Event()
        self._thread = None
        self._fps = max(1, int(fps))
        self._speed = max(0.1, float(speed))
        self._average_time = float(average_time)
        self._show_vt_dot = bool(show_vt_dot)
        self._refresh_interval = max(1.0, float(refresh_interval))
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

    def set_show_vt_dot(self, show=True):
        self._show_vt_dot = bool(show)
        self._rust.set_show_vt_dot(self._show_vt_dot)

    def display(self):
        """Display interactive viewer in Jupyter with time-based slider."""
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
        self._rust.set_show_vt_dot(self._show_vt_dot)

        # Time axis setup
        solve_t = self._rust.solve_time()
        latest_t = self._rust.latest_frame_time()
        total = self._rust.total_frames()

        image = widgets.Image(format="png", width=FRAME_W)
        play_btn = widgets.Button(
            description="Play", icon="play",
            layout=widgets.Layout(width="92px"),
        )
        time_slider = widgets.FloatSlider(
            value=0.0, min=0.0, max=max(solve_t, 0.1), step=0.1,
            continuous_update=False,
            layout=widgets.Layout(width="500px"),
        )
        time_label = widgets.Label(
            value=f"t=0.0 / {solve_t:.1f}",
            layout=widgets.Layout(width="220px"),
        )
        fps_slider = widgets.IntSlider(
            value=self._fps, min=1, max=30, description="FPS",
            continuous_update=False,
            layout=widgets.Layout(width="180px"),
        )
        speed_input = widgets.BoundedFloatText(
            value=self._speed, min=0.1, max=10000, description="Speed",
            layout=widgets.Layout(width="150px"),
        )
        vt_dot_check = widgets.Checkbox(
            value=self._show_vt_dot, description="V(t) dot",
            indent=False,
            layout=widgets.Layout(width="110px"),
        )
        avg_input = widgets.BoundedFloatText(
            value=self._average_time, min=0.1, max=1.0, step=0.05,
            description="Avg",
            style={"description_width": "32px"},
            layout=widgets.Layout(width="100px"),
        )
        progress_label = widgets.Label(
            value=f"simulated: {latest_t:.1f} / {solve_t:.1f}",
            layout=widgets.Layout(width="200px"),
        )
        refresh_input = widgets.BoundedFloatText(
            value=self._refresh_interval, min=1.0, max=300.0, step=1.0,
            description="Refresh(s)",
            style={"description_width": "70px"},
            layout=widgets.Layout(width="120px"),
        )
        iv_status = widgets.Label(value="I-V: idle")
        status = widgets.Label(value="ready")

        # Frame cache
        _cache = {}
        _cache_lock = threading.Lock()
        _current_frame = [0]
        _render_token = [0]
        _play_token = [0]
        _suppress_slider = [False]
        _latest_frame = [total - 1]
        _solve_time = [solve_t]
        _live_stop = threading.Event()
        _live_thread = [None]

        def _time_to_frame(t):
            f = self._rust.time_to_frame(t)
            return min(f, _latest_frame[0])

        def _evict_for(center):
            step = max(1, int(self._speed))
            keep_min = center
            keep_max = center + step * (PREFETCH_AHEAD + 2)
            with _cache_lock:
                for k in list(_cache):
                    if k < keep_min or k > keep_max:
                        del _cache[k]

        def _prefetch(center):
            if center != _current_frame[0]:
                return
            total = self._rust.total_frames()
            step = max(1, int(self._speed))
            for n in range(1, PREFETCH_AHEAD + 1):
                if center != _current_frame[0]:
                    return
                i = center + n * step
                if i >= total:
                    break
                with _cache_lock:
                    if i in _cache:
                        continue
                png = self._rust.render_frame(i)
                if center != _current_frame[0]:
                    return
                with _cache_lock:
                    _cache[i] = png
            _evict_for(center)

        def _render(frame_idx):
            frame_idx = max(0, min(frame_idx, _latest_frame[0]))
            _current_frame[0] = frame_idx
            _render_token[0] += 1
            token = _render_token[0]
            ft = self._rust.frame_time_at(frame_idx)
            t_val = ft if ft is not None else 0.0
            if abs(time_slider.value - t_val) > 0.05:
                _suppress_slider[0] = True
                time_slider.value = t_val
                _suppress_slider[0] = False
            total = self._rust.total_frames()
            time_label.value = f"t={t_val:.1f} / {_solve_time[0]:.1f}"
            status.value = f"frame {frame_idx}/{total - 1}"
            _evict_for(frame_idx)

            with _cache_lock:
                png = _cache.get(frame_idx)
            if png is not None:
                image.value = png
                status.value = f"frame {frame_idx}/{total - 1}"
                threading.Thread(target=_prefetch, args=(frame_idx,), daemon=True).start()
                return

            def _render_worker(fi, rt):
                png = self._rust.render_frame(fi)
                with _cache_lock:
                    _cache[fi] = png
                if _render_token[0] == rt and _current_frame[0] == fi:
                    image.value = png
                    total = self._rust.total_frames()
                    status.value = f"frame {fi}/{total - 1}"
                    _prefetch(fi)

            threading.Thread(target=_render_worker, args=(frame_idx, token), daemon=True).start()

        def _live_refresh():
            """Background thread: refresh index to pick up new frames."""
            while not _live_stop.is_set():
                _live_stop.wait(max(1.0, self._refresh_interval))
                if _live_stop.is_set():
                    break
                try:
                    n = self._rust.refresh_index()
                    lt = self._rust.latest_frame_time()
                    _latest_frame[0] = n - 1
                    progress_label.value = f"simulated: {lt:.1f} / {_solve_time[0]:.1f}"
                except Exception:
                    pass

        _live_stop.clear()
        t = threading.Thread(target=_live_refresh, daemon=True)
        t.start()
        _live_thread[0] = t

        def _start_iv(average_time):
            try:
                self._rust.start_iv_scan(average_time=average_time)
                iv_status.value = "I-V: scanning..."
            except Exception as e:
                iv_status.value = f"I-V: {e}"

        def _monitor_iv():
            while not self._stop.is_set():
                try:
                    prog = json.loads(self._rust.get_iv_progress())
                    done = prog.get("done", False)
                    completed = prog.get("steps_completed", 0)
                    total_s = prog.get("steps_total", 0)
                    if done:
                        iv_status.value = f"I-V: done ({completed}/{total_s})"
                        break
                    else:
                        iv_status.value = f"I-V: {completed}/{total_s}..."
                except Exception:
                    pass
                self._stop.wait(0.5)

        def on_avg_change(change):
            avg = float(change["new"])
            self._average_time = avg
            self._rust.stop_iv_scan()
            _start_iv(avg)
            if not (self._iv_monitor_thread and self._iv_monitor_thread.is_alive()):
                self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
                self._iv_monitor_thread.start()

        def on_dropdown(change):
            idx = change["new"]
            _play_token[0] += 1
            self._stop_playback()
            _live_stop.set()
            self._rust.stop_iv_scan()
            self._rust.open(run_index=idx)
            self._rust.set_show_vt_dot(self._show_vt_dot)
            st = self._rust.solve_time()
            _solve_time[0] = st
            time_slider.max = max(st, 0.1)
            time_slider.value = 0.0
            _cache.clear()
            total = self._rust.total_frames()
            _latest_frame[0] = total - 1
            progress_label.value = f"simulated: {self._rust.latest_frame_time():.1f} / {st:.1f}"
            _render(0)
            _start_iv(avg_input.value)
            self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
            self._iv_monitor_thread.start()
            _live_stop.clear()
            t = threading.Thread(target=_live_refresh, daemon=True)
            t.start()
            _live_thread[0] = t

        def on_time_slider(change):
            if _suppress_slider[0]:
                return
            was_playing = self._playing
            if was_playing:
                _play_token[0] += 1
            t_val = change["new"]
            frame_idx = _time_to_frame(t_val)
            _render(frame_idx)
            if was_playing:
                self._start_playback(
                    time_slider, image, time_label, status, play_btn,
                    _cache, _cache_lock, _suppress_slider, _play_token,
                    progress_label,
                )

        def on_play(_):
            if self._playing:
                self._stop_playback()
                play_btn.description = "Play"
                play_btn.icon = "play"
            else:
                self._start_playback(
                    time_slider, image, time_label, status, play_btn,
                    _cache, _cache_lock, _suppress_slider, _play_token,
                    progress_label,
                )

        def on_fps(change):
            self._fps = change["new"]

        def on_speed(change):
            self._speed = max(0.1, float(change["new"]))
            _evict_for(_current_frame[0])
            threading.Thread(target=_prefetch, args=(_current_frame[0],), daemon=True).start()

        def on_vt_dot(change):
            self.set_show_vt_dot(bool(change["new"]))
            with _cache_lock:
                _cache.clear()
            _render(_current_frame[0])

        def on_refresh(change):
            self._refresh_interval = max(1.0, float(change["new"]))

        run_dropdown.observe(on_dropdown, names="value")
        time_slider.observe(on_time_slider, names="value")
        play_btn.on_click(on_play)
        fps_slider.observe(on_fps, names="value")
        speed_input.observe(on_speed, names="value")
        vt_dot_check.observe(on_vt_dot, names="value")
        avg_input.observe(on_avg_change, names="value")
        refresh_input.observe(on_refresh, names="value")

        _start_iv(self._average_time)
        self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
        self._iv_monitor_thread.start()
        _render(0)

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, time_slider, time_label]),
            widgets.HBox(
                [fps_slider, speed_input, vt_dot_check, avg_input, iv_status, refresh_input, progress_label],
                layout=widgets.Layout(gap="16px"),
            ),
            image,
        ], layout=widgets.Layout(gap="6px", padding="4px"))
        display(ui)

    def _start_playback(
        self, time_slider, image, time_label, status, play_btn,
        cache, cache_lock, suppress_slider, play_token,
        progress_label,
    ):
        self._playing = True
        self._stop.clear()
        play_token[0] += 1
        token = play_token[0]
        play_btn.description = "Pause"
        play_btn.icon = "pause"
        self._thread = threading.Thread(
            target=self._loop,
            args=(
                time_slider, image, time_label, status, cache, cache_lock,
                suppress_slider, play_token, token, progress_label,
            ),
            daemon=True,
        )
        self._thread.start()

    def _stop_playback(self):
        self._playing = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(
        self, time_slider, image, time_label, status, cache, cache_lock,
        suppress_slider, play_token, token, progress_label,
    ):
        prev_frame = -1
        frame_carry = 0.0
        while not self._stop.is_set():
            if play_token[0] != token:
                break

            solve_t = self._rust.solve_time()
            speed = max(0.1, float(self._speed))
            interval = 1.0 / self._fps
            latest = self._rust.total_frames() - 1
            current_frame = self._rust.time_to_frame(time_slider.value)
            current_frame = min(current_frame, latest)
            latest_t = self._rust.latest_frame_time()

            # Reached the end of all available data
            if current_frame >= latest:
                if latest_t < solve_t:
                    self._stop.wait(1.0)
                    continue
                if prev_frame != latest:
                    self._render_loop_frame(
                        latest, image, time_slider, time_label,
                        status, cache, cache_lock, suppress_slider,
                        solve_t,
                    )
                    prev_frame = latest
                self._stop.set()
                break

            frame_carry += speed
            frame_step = int(frame_carry)
            if frame_step < 1:
                self._stop.wait(interval)
                continue
            frame_carry -= frame_step

            next_frame = min(current_frame + frame_step, latest)

            # Same frame as before — still advance time display but don't re-render
            if next_frame == prev_frame:
                suppress_slider[0] = True
                ft = self._rust.frame_time_at(next_frame)
                time_slider.value = ft if ft is not None else time_slider.value
                suppress_slider[0] = False
                elapsed = 0.001
                remaining = max(0.0, interval - elapsed)
                self._stop.wait(remaining)
                continue

            with cache_lock:
                png = cache.get(next_frame)

            if png is None:
                t0 = time.perf_counter()
                png = self._rust.render_frame(next_frame)
                if play_token[0] != token or self._stop.is_set():
                    break
                with cache_lock:
                    cache[next_frame] = png
            else:
                t0 = time.perf_counter()

            if play_token[0] != token or self._stop.is_set():
                break

            prev_frame = next_frame
            self._render_loop_frame(
                next_frame, image, time_slider, time_label,
                status, cache, cache_lock, suppress_slider,
                solve_t, png=png,
            )

            threading.Thread(
                target=self._prefetch_range,
                args=(
                    next_frame + max(1, int(speed)),
                    PREFETCH_AHEAD,
                    max(1, int(speed)),
                    cache,
                    cache_lock,
                ),
                daemon=True,
            ).start()

            elapsed = time.perf_counter() - t0
            remaining = max(0.0, interval - elapsed)
            self._stop.wait(remaining)

    def _render_loop_frame(
        self, frame_idx, image, time_slider, time_label,
        status, cache, cache_lock, suppress_slider,
        solve_t, png=None,
    ):
        if png is None:
            with cache_lock:
                png = cache.get(frame_idx)
            if png is None:
                png = self._rust.render_frame(frame_idx)
                with cache_lock:
                    cache[frame_idx] = png
        image.value = png
        ft = self._rust.frame_time_at(frame_idx)
        t_val = ft if ft is not None else 0.0
        suppress_slider[0] = True
        time_slider.value = t_val
        suppress_slider[0] = False
        time_label.value = f"t={t_val:.1f} / {solve_t:.1f}"
        total = self._rust.total_frames()
        status.value = f"frame {frame_idx}/{total - 1}"

    def _prefetch_range(self, start, count, step, cache, cache_lock):
        total = self._rust.total_frames()
        for n in range(count):
            i = start + n * max(1, int(step))
            if i >= total:
                break
            with cache_lock:
                if i in cache:
                    continue
            png = self._rust.render_frame(i)
            with cache_lock:
                cache[i] = png
                while len(cache) > PREFETCH_AHEAD + 4:
                    cache.pop(min(cache), None)

    def get_iv_data(self):
        prog = json.loads(self._rust.get_iv_progress())
        return prog

    def __del__(self):
        self._stop_playback()
