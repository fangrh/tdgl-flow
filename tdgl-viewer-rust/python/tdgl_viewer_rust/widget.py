import json
import threading
import time

import ipywidgets as widgets
from IPython.display import display

from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as _RustViewer
from tdgl_viewer_rust.tdgl_viewer_rust import TdglDiscreteViewer as _RustDiscreteViewer

FRAME_W = 760
PREFETCH_AHEAD = 8


class TdglDiscreteViewer:
    """Interactive 2x2 TDGL discrete viewer with run dropdown, time-based slider and live refresh."""

    def __init__(
        self,
        minio_url="http://localhost:30900",
        fps=10,
        speed=1,
        average_time=0.5,
        show_vt_dot=True,
        refresh_interval=5.0,
        debug=False,
    ):
        self._rust = _RustDiscreteViewer(minio_url)
        self._debug = debug
        if debug:
            self._rust.enable_debug()
        self._playing = False
        self._stop = threading.Event()
        self._thread = None
        self._fps = max(1, int(fps))
        self._speed = max(0.1, float(speed))
        self._average_time = float(average_time)
        self._show_vt_dot = bool(show_vt_dot)
        self._refresh_interval = max(1.0, float(refresh_interval))
        self._run_id = None

    def open(self, run_id=None):
        if run_id is None:
            raise ValueError("run_id is required for TdglDiscreteViewer")
        self._run_id = run_id
        self._rust.open(run_id)

    def total_frames(self):
        return self._rust.total_frames()

    def render_frame(self, frame_idx):
        return self._rust.render_frame(frame_idx)

    def set_show_vt_dot(self, show=True):
        self._show_vt_dot = bool(show)
        self._rust.set_show_vt_dot(self._show_vt_dot)

    def display(self):
        """Display interactive viewer with run dropdown, playback controls, and live refresh."""
        self._rust.set_show_vt_dot(self._show_vt_dot)

        # ── Run selection ──────────────────────────────────────────────
        try:
            run_labels = self._rust.list_discrete_runs(False)
            run_ids = self._rust.get_discrete_run_ids()
        except Exception:
            run_labels = []
            run_ids = []

        if self._run_id and self._run_id not in run_ids:
            run_ids.insert(0, self._run_id)
            run_labels.insert(0, f"{self._run_id[:8]} (current)")

        run_dropdown = widgets.Dropdown(
            options=[(label, rid) for label, rid in zip(run_labels, run_ids)],
            description="Run:",
            layout=widgets.Layout(width="clamp(400px, 60vw, 800px)"),
        )
        if self._run_id and self._run_id in run_ids:
            run_dropdown.value = self._run_id

        # ── Open run if needed ─────────────────────────────────────────
        if self._run_id is None and run_ids:
            self.open(run_id=run_ids[0])

        total = self._rust.total_frames()
        if total == 0 and not run_ids:
            print("No discrete runs found.")
            return

        try:
            solve_t = self._rust.solve_time()
        except Exception:
            try:
                latest_t = self._rust.latest_frame_time()
                solve_t = latest_t * 1.1 if latest_t > 0 else 1.0
            except Exception:
                solve_t = 1.0

        # ── Widgets ────────────────────────────────────────────────────
        image = widgets.Image(format="png", width=FRAME_W)
        play_btn = widgets.Button(
            description="Play", icon="play",
            layout=widgets.Layout(width="92px"),
        )
        time_slider = widgets.FloatSlider(
            value=0.0, min=0.0, max=max(solve_t, 0.1), step=0.1,
            continuous_update=False,
            readout=False,
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
        refresh_input = widgets.BoundedFloatText(
            value=self._refresh_interval, min=1.0, max=300.0, step=1.0,
            description="Refresh(s)",
            style={"description_width": "70px"},
            layout=widgets.Layout(width="120px"),
        )
        status = widgets.Label(value=f"frame 0/{max(total - 1, 0)}")

        # ── State ──────────────────────────────────────────────────────
        _cache = {}
        _cache_lock = threading.Lock()
        _current_frame = [0]
        _render_token = [0]
        _play_token = [0]
        _suppress_slider = [False]
        _suppress_dropdown = [False]
        _latest_frame = [max(total - 1, 0)]
        _solve_time = [solve_t]
        _live_stop = threading.Event()
        _live_thread = [None]

        def _time_to_frame(t):
            f = self._rust.time_to_frame(t)
            return min(f, _latest_frame[0])

        def _render(frame_idx, update_slider=True):
            frame_idx = max(0, min(frame_idx, _latest_frame[0]))
            _current_frame[0] = frame_idx
            _render_token[0] += 1
            ft = self._rust.frame_time_at(frame_idx)
            t_val = ft if ft is not None else 0.0
            if update_slider and abs(time_slider.value - t_val) > 0.05:
                _suppress_slider[0] = True
                time_slider.value = t_val
                _suppress_slider[0] = False
            time_label.value = f"t={t_val:.1f} / {_solve_time[0]:.1f}"
            n = self._rust.total_frames()
            status.value = f"frame {frame_idx}/{n - 1}"

            with _cache_lock:
                png = _cache.get(frame_idx)
            if png is not None:
                image.value = png
                return

            status.value = f"frame {frame_idx}/{n - 1} (loading...)"

            def _render_worker(fi, rt):
                try:
                    png = self._rust.render_frame(fi)
                except Exception:
                    return
                with _cache_lock:
                    _cache[fi] = png
                if _render_token[0] == rt and _current_frame[0] == fi:
                    image.value = png
                    n = self._rust.total_frames()
                    status.value = f"frame {fi}/{n - 1}"

            threading.Thread(target=_render_worker, args=(frame_idx, _render_token[0]), daemon=True).start()

        def _live_refresh():
            _prev_total = [self._rust.total_frames()]
            while not _live_stop.is_set():
                _live_stop.wait(max(1.0, self._refresh_interval))
                if _live_stop.is_set():
                    break
                try:
                    n = self._rust.refresh_index()
                    _latest_frame[0] = n - 1
                    if n > 0:
                        try:
                            _solve_time[0] = self._rust.solve_time()
                        except Exception:
                            lt = self._rust.latest_frame_time()
                            _solve_time[0] = lt * 1.1 if lt > 0 else 1.0
                        time_slider.max = max(_solve_time[0], 0.1)
                    if n > _prev_total[0]:
                        with _cache_lock:
                            _cache.clear()
                        _prev_total[0] = n
                        _render(_current_frame[0])
                    # Refresh run list periodically
                    try:
                        _suppress_dropdown[0] = True
                        new_labels = self._rust.list_discrete_runs(True)
                        new_ids = self._rust.get_discrete_run_ids()
                        run_dropdown.options = [(l, rid) for l, rid in zip(new_labels, new_ids)]
                        if self._run_id in new_ids:
                            run_dropdown.value = self._run_id
                        _suppress_dropdown[0] = False
                    except Exception:
                        _suppress_dropdown[0] = False
                except Exception as e:
                    if self._debug:
                        print(f"[discrete-viewer-debug] refresh error: {e}")

        _live_stop.clear()
        t = threading.Thread(target=_live_refresh, daemon=True)
        t.start()
        _live_thread[0] = t

        def on_dropdown(change):
            if _suppress_dropdown[0]:
                return
            selected_id = change["new"]
            if selected_id == self._run_id:
                return
            # Stop playback
            _play_token[0] += 1
            self._stop_playback()
            _live_stop.set()
            if _live_thread[0] and _live_thread[0].is_alive():
                _live_thread[0].join(timeout=5.0)
            # Open new run
            try:
                self.open(run_id=selected_id)
            except Exception as e:
                status.value = f"Error: {e}"
                return
            # Reset UI state
            total = self._rust.total_frames()
            try:
                st = self._rust.solve_time()
            except Exception:
                st = 1.0
            _solve_time[0] = st
            _latest_frame[0] = max(total - 1, 0)
            time_slider.max = max(st, 0.1)
            time_slider.value = 0.0
            with _cache_lock:
                _cache.clear()
            _render(0)
            # Restart live refresh
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
            _render(frame_idx, update_slider=False)
            if was_playing:
                self._start_playback(
                    time_slider, image, time_label, status, play_btn,
                    _cache, _cache_lock, _suppress_slider, _play_token,
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
                )

        def on_fps(change):
            self._fps = change["new"]

        def on_speed(change):
            self._speed = max(0.1, float(change["new"]))

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
        refresh_input.observe(on_refresh, names="value")

        if total > 0:
            _render(0)

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, time_slider, time_label]),
            widgets.HBox(
                [fps_slider, speed_input, vt_dot_check, refresh_input, status],
                layout=widgets.Layout(gap="16px"),
            ),
            image,
        ], layout=widgets.Layout(gap="6px", padding="4px"))
        display(ui)

    def _start_playback(
        self, time_slider, image, time_label, status, play_btn,
        cache, cache_lock, suppress_slider, play_token,
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
                suppress_slider, play_token, token, play_btn,
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
        suppress_slider, play_token, token, play_btn,
    ):
        prev_frame = -1
        frame_carry = 0.0
        try:
            while not self._stop.is_set():
                if play_token[0] != token:
                    break

                speed = max(0.1, float(self._speed))
                interval = 1.0 / self._fps
                latest = self._rust.total_frames() - 1
                current_frame = self._rust.time_to_frame(time_slider.value)
                current_frame = min(current_frame, latest)
                try:
                    solve_t = self._rust.solve_time()
                except Exception:
                    solve_t = 1.0
                latest_t = self._rust.latest_frame_time()

                if current_frame >= latest:
                    if latest_t < solve_t * 0.9:
                        self._stop.wait(1.0)
                        continue
                    if prev_frame != latest:
                        _render_done = threading.Event()
                        _render_result = [None]

                        def _do_render(fi):
                            try:
                                _render_result[0] = self._rust.render_frame(fi)
                            except Exception:
                                pass
                            _render_done.set()

                        threading.Thread(target=_do_render, args=(latest,), daemon=True).start()
                        while not _render_done.is_set() and not self._stop.is_set():
                            self._stop.wait(0.1)
                        if self._stop.is_set():
                            break
                        png = _render_result[0]
                        if png is None:
                            break
                        image.value = png
                        ft = self._rust.frame_time_at(latest)
                        t_val = ft if ft is not None else 0.0
                        suppress_slider[0] = True
                        time_slider.value = t_val
                        suppress_slider[0] = False
                        time_label.value = f"t={t_val:.1f} / {solve_t:.1f}"
                        status.value = f"frame {latest}/{latest}"
                        prev_frame = latest
                    break

                frame_carry += speed
                frame_step = int(frame_carry)
                if frame_step < 1:
                    self._stop.wait(interval)
                    continue
                frame_carry -= frame_step

                next_frame = min(current_frame + frame_step, latest)

                with cache_lock:
                    png = cache.get(next_frame)

                if png is None:
                    _render_done = threading.Event()
                    _render_result = [None]

                    def _do_render(fi):
                        try:
                            _render_result[0] = self._rust.render_frame(fi)
                        except Exception:
                            pass
                        _render_done.set()

                    threading.Thread(target=_do_render, args=(next_frame,), daemon=True).start()
                    while not _render_done.is_set() and not self._stop.is_set():
                        self._stop.wait(0.1)
                    if self._stop.is_set():
                        break
                    png = _render_result[0]
                    if png is None:
                        self._stop.wait(1.0)
                        continue
                    if play_token[0] != token or self._stop.is_set():
                        break
                    with cache_lock:
                        cache[next_frame] = png

                if play_token[0] != token or self._stop.is_set():
                    break

                prev_frame = next_frame
                image.value = png
                ft = self._rust.frame_time_at(next_frame)
                t_val = ft if ft is not None else 0.0
                suppress_slider[0] = True
                time_slider.value = t_val
                suppress_slider[0] = False
                time_label.value = f"t={t_val:.1f} / {solve_t:.1f}"
                status.value = f"frame {next_frame}/{latest}"

                elapsed = 0.001
                remaining = max(0.0, interval - elapsed)
                while remaining > 0 and not self._stop.is_set():
                    chunk = min(remaining, 0.1)
                    self._stop.wait(chunk)
                    remaining -= chunk
        finally:
            if play_token[0] == token:
                self._playing = False
                play_btn.description = "Play"
                play_btn.icon = "play"

    def get_iv_data(self):
        return json.loads(self._rust.get_iv_progress())

    def __del__(self):
        self._stop_playback()


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
        debug=False,
    ):
        self._rust = _RustViewer(minio_url)
        self._debug = debug
        if debug:
            self._rust.enable_debug()
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
        return self._rust.list_runs(True)

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
        runs = self._rust.list_runs(False)
        if not runs:
            runs = self._rust.list_runs(True)
        if not runs:
            print("No runs found.")
            return

        run_dropdown = widgets.Dropdown(
            options=[(label, i) for i, label in enumerate(runs)],
            description="Run:",
            layout=widgets.Layout(width="clamp(400px, 60vw, 800px)"),
        )

        # Only open if no run is currently opened
        if self._rust.get_run_info() is None:
            opened = False
            for i in range(len(runs)):
                try:
                    self._rust.open(run_index=i)
                    opened = True
                    break
                except Exception:
                    continue
            if not opened:
                print("No runs with viewer-index.json found.")
                return
            run_dropdown.value = i
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
            readout=False,
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
        _suppress_dropdown = [False]
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
                try:
                    png = self._rust.render_frame(fi)
                except Exception:
                    return
                with _cache_lock:
                    _cache[fi] = png
                if _render_token[0] == rt and _current_frame[0] == fi:
                    image.value = png
                    total = self._rust.total_frames()
                    status.value = f"frame {fi}/{total - 1}"
                    _prefetch(fi)

            threading.Thread(target=_render_worker, args=(frame_idx, token), daemon=True).start()

        def _start_iv(average_time):
            if self._debug:
                import traceback
                print("[viewer-debug] _start_iv() called, traceback:")
                traceback.print_stack()
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
                    scanned = prog.get("frames_scanned", 0)
                    if done:
                        iv_status.value = f"I-V: done ({completed}/{total_s})"
                        break
                    else:
                        iv_status.value = f"I-V: {completed}/{total_s} ({scanned} frames)"
                except Exception:
                    pass
                self._stop.wait(0.5)

        def _live_refresh():
            """Background thread: refresh index to pick up new frames."""
            _prev_total = [self._rust.total_frames()]
            _prev_status = [None]
            try:
                info = self._rust.get_run_info()
                if info:
                    _prev_status[0] = json.loads(info).get("status", "")
            except Exception:
                pass
            while not _live_stop.is_set():
                _live_stop.wait(max(1.0, self._refresh_interval))
                if _live_stop.is_set():
                    break
                try:
                    n = self._rust.refresh_index()
                    _latest_frame[0] = n - 1
                    # Refresh run list to pick up status changes (running→completed)
                    try:
                        updated_runs = self._rust.list_runs(True)
                        if updated_runs:
                            _suppress_dropdown[0] = True
                            run_dropdown.options = [(label, i) for i, label in enumerate(updated_runs)]
                            _suppress_dropdown[0] = False
                    except Exception:
                        pass
                    # Restart IV scanner only when simulation transitions to completed
                    try:
                        info = self._rust.get_run_info()
                        if info:
                            current_status = json.loads(info).get("status", "")
                            if (
                                current_status == "completed"
                                and _prev_status[0] is not None
                                and _prev_status[0] != "completed"
                            ):
                                _prev_status[0] = "completed"
                                _start_iv(self._average_time)
                                if not (self._iv_monitor_thread and self._iv_monitor_thread.is_alive()):
                                    self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
                                    self._iv_monitor_thread.start()
                            else:
                                _prev_status[0] = current_status
                    except Exception:
                        pass
                    if n > _prev_total[0]:
                        _prev_total[0] = n
                except Exception as e:
                    if self._debug:
                        print(f"[viewer-debug] refresh error: {e}")

        _live_stop.clear()
        t = threading.Thread(target=_live_refresh, daemon=True)
        t.start()
        _live_thread[0] = t

        def on_avg_change(change):
            avg = float(change["new"])
            self._average_time = avg
            self._rust.stop_iv_scan()
            _start_iv(avg)
            if not (self._iv_monitor_thread and self._iv_monitor_thread.is_alive()):
                self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
                self._iv_monitor_thread.start()

        def on_dropdown(change):
            if _suppress_dropdown[0]:
                return
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

        self._iv_monitor_thread = threading.Thread(target=_monitor_iv, daemon=True)
        self._iv_monitor_thread.start()
        _render(0)

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, time_slider, time_label]),
            widgets.HBox(
                [fps_slider, speed_input, vt_dot_check, avg_input, iv_status, refresh_input],
                layout=widgets.Layout(gap="16px"),
            ),
            image,
        ], layout=widgets.Layout(gap="6px", padding="4px"))
        display(ui)

    def _start_playback(
        self, time_slider, image, time_label, status, play_btn,
        cache, cache_lock, suppress_slider, play_token,
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
                suppress_slider, play_token, token, play_btn,
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
        suppress_slider, play_token, token, play_btn,
    ):
        prev_frame = -1
        frame_carry = 0.0
        try:
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
                    try:
                        png = self._rust.render_frame(next_frame)
                    except Exception:
                        # Frame data not yet available (H5 upload in progress)
                        self._stop.wait(1.0)
                        continue
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
        finally:
            if play_token[0] == token:
                self._playing = False
                play_btn.description = "Play"
                play_btn.icon = "play"

    def _render_loop_frame(
        self, frame_idx, image, time_slider, time_label,
        status, cache, cache_lock, suppress_slider,
        solve_t, png=None,
    ):
        if png is None:
            with cache_lock:
                png = cache.get(frame_idx)
            if png is None:
                try:
                    png = self._rust.render_frame(frame_idx)
                except Exception:
                    return
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
            try:
                png = self._rust.render_frame(i)
            except Exception:
                break
            with cache_lock:
                cache[i] = png
                while len(cache) > PREFETCH_AHEAD + 4:
                    cache.pop(min(cache), None)

    def get_iv_data(self):
        prog = json.loads(self._rust.get_iv_progress())
        return prog

    def __del__(self):
        self._stop_playback()
