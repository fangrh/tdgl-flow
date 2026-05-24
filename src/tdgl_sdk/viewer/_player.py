import bisect
import math
import threading
import time

from tdgl_sdk.viewer._iv import IVCache
from tdgl_sdk.viewer._mesh import estimate_mu_vmax, h5open, load_mesh
from tdgl_sdk.viewer._render import (
    FRAME_W,
    RealtimeFrameBuffer,
    render_frame_png,
)

try:
    import ipywidgets as widgets
    from IPython.display import display
except ImportError:
    widgets = None
    display = None

FPS_DEFAULT = 10


def compute_time_grid(solve_time: float, playback_dt: float = 1.0):
    """Pre-compute the expected time positions for the full simulation.

    Returns a list of simulation times: [0, dt, 2*dt, ..., solve_time].
    The slider and playback advance through these positions.
    Actual frame data is matched from HDF5 as it arrives.
    """
    if solve_time <= 0 or playback_dt <= 0:
        return [0.0]
    n = math.ceil(solve_time / playback_dt)
    return [round(i * playback_dt, 6) for i in range(n + 1)]


class RealtimeTDGLWidgetPlayer:
    def __init__(self, h5_path, mesh, iv_cache, mu_vmax, debug_log=None, **s3_kwds):
        if widgets is None:
            raise ImportError("ipywidgets is required for the widget player")

        self.h5_path = h5_path
        self._mesh = mesh
        self.iv_cache = iv_cache
        self.mu_vmax = mu_vmax
        self._s3_kwds = s3_kwds
        self._debug = debug_log
        self.total = mesh["total_frames"]  # actual frames in HDF5
        self.current = 0  # current position in time_grid
        self.playing = False
        self.live = False
        self.stop_event = threading.Event()
        self.thread = None
        self.render_lock = threading.RLock()
        self.playback_dt = 1.0

        # Actual frame times loaded from HDF5 (grows in live mode)
        self.frame_times = self._load_frame_times()

        # Pre-computed time grid — the playback timeline.
        # Set by StreamingTDGLPlayer from solve_time before display.
        # Defaults to actual frame times if no timing info provided.
        self.time_grid = list(self.frame_times) if self.frame_times else [0.0]
        self.solve_time = self.frame_times[-1] if self.frame_times else 0

        self.buffer = RealtimeFrameBuffer()
        self.image = widgets.Image(
            value=self.buffer.get(0, self._render),
            format="png",
            width=FRAME_W,
        )
        self.play_button = widgets.Button(
            description="Play", icon="play", layout=widgets.Layout(width="92px")
        )
        self.stop_button = widgets.Button(
            description="Stop", icon="stop", layout=widgets.Layout(width="92px")
        )
        self.slider = widgets.IntSlider(
            value=0,
            min=0,
            max=max(0, len(self.time_grid) - 1),
            step=1,
            description="Step",
            continuous_update=False,
            readout=False,
            layout=widgets.Layout(width="400px"),
        )
        self.time_label = widgets.Label(
            value=self._fmt_step(0),
            layout=widgets.Layout(width="200px"),
        )
        self.fps = widgets.IntSlider(
            value=FPS_DEFAULT,
            min=1,
            max=20,
            step=1,
            description="FPS",
            continuous_update=False,
            layout=widgets.Layout(width="220px"),
        )
        self.status = widgets.Label(value="buffer [0]")

        self.play_button.on_click(self.toggle)
        self.stop_button.on_click(self.stop)
        self.slider.observe(self._on_slider, names="value")

        self.ui = widgets.VBox([
            widgets.HBox([self.play_button, self.stop_button, self.slider, self.time_label]),
            widgets.HBox([self.fps, self.status]),
            self.image,
        ])

    def _load_frame_times(self):
        """Load physical time for each frame from HDF5 attrs."""
        times = []
        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            for i in range(self.total):
                t = float(f[f"data/{i}"].attrs.get("time", i))
                times.append(t)
        return times

    def set_time_grid(self, solve_time, playback_dt=None):
        """Set the pre-computed timeline from solve_time.

        Called by StreamingTDGLPlayer after reading the manifest.
        Expands slider range to cover the full expected timeline.
        """
        if playback_dt is not None:
            self.playback_dt = playback_dt
        self.solve_time = solve_time
        self.time_grid = compute_time_grid(solve_time, self.playback_dt)
        self.slider.max = max(0, len(self.time_grid) - 1)
        self.time_label.value = self._fmt_step(self.current)

    def _fmt_step(self, step):
        """Format current step: 't=X.XXX / Y.YYY s (frame N)'."""
        if step >= len(self.time_grid):
            step = len(self.time_grid) - 1
        t = self.time_grid[step]
        t_end = self.time_grid[-1]
        frame_idx = self._find_frame_for_time(t)
        frame_info = f" frame {frame_idx}" if frame_idx >= 0 else " (waiting)"
        return f"t={t:.3f} / {t_end:.3f} s{frame_info}"

    @property
    def debug_log(self):
        return self._debug

    def _find_frame_for_time(self, target_time):
        """Find the best available frame for a target simulation time.

        Returns frame index whose time is closest to target_time,
        or -1 if no frames exist.
        """
        if not self.frame_times:
            return -1
        idx = bisect.bisect_left(self.frame_times, target_time)
        if idx >= len(self.frame_times):
            return len(self.frame_times) - 1
        if idx == 0:
            return 0
        # Pick closer of idx-1 and idx
        d_left = abs(self.frame_times[idx - 1] - target_time)
        d_right = abs(self.frame_times[idx] - target_time)
        return idx - 1 if d_left <= d_right else idx

    def _render(self, idx):
        return render_frame_png(
            self.h5_path, self._mesh, self.iv_cache, self.mu_vmax, idx,
            debug_log=self._debug, **self._s3_kwds
        )

    def display_player(self):
        display(self.ui)
        if self.live and self.total > 0:
            self.play()

    def _available_frames(self) -> int:
        try:
            with h5open(self.h5_path, "r", **self._s3_kwds) as f:
                if "data" in f:
                    return len(f["data"].keys())
        except Exception:
            pass
        return 0

    def _refresh_total(self):
        """Update self.total and frame_times from the live HDF5."""
        available = self._available_frames()
        if available > self.total:
            with h5open(self.h5_path, "r", **self._s3_kwds) as f:
                for i in range(self.total, available):
                    t = float(f[f"data/{i}"].attrs.get("time", i))
                    self.frame_times.append(t)
            self.total = available

    def show(self, step, wait=True):
        """Display the frame for a time-grid step position.

        Finds the closest available frame for this step's target time.
        In live mode, waits for data if no frame exists yet.
        """
        step = max(0, min(step, len(self.time_grid) - 1))
        target_time = self.time_grid[step]

        self._refresh_total()
        frame_idx = self._find_frame_for_time(target_time)

        if frame_idx < 0:
            if self.live and wait:
                for _ in range(120):
                    self._refresh_total()
                    frame_idx = self._find_frame_for_time(target_time)
                    if frame_idx >= 0:
                        break
                    self.stop_event.wait(0.5)
                    if self.stop_event.is_set():
                        return
            if frame_idx < 0:
                frame_idx = max(0, self.total - 1)

        frame_idx = min(frame_idx, self.total - 1)
        if self._debug:
            self._debug.log("show", step=step, frame=frame_idx,
                           total_frames=self.total, playing=self.playing)
        with self.render_lock:
            self.current = step
            png = self.buffer.get(frame_idx, self._render)
            self.image.value = png
            try:
                self.slider.unobserve(self._on_slider, names="value")
            except Exception:
                pass
            self.slider.value = step
            self.slider.observe(self._on_slider, names="value")
            self.time_label.value = self._fmt_step(step)
            self.buffer.keep_near(frame_idx)
            tag = "LIVE " if self.live else ""
            self.status.value = (
                f"{tag}step {step}/{len(self.time_grid)-1} "
                f"frame {frame_idx}/{self.total-1}; "
                f"I-V {self.iv_cache.size()}"
            )

    def _on_slider(self, change):
        if int(change["new"]) != self.current:
            self.show(change["new"])

    def stop(self, _=None):
        self.pause()
        self.show(0)

    def toggle(self, _=None):
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.playing = True
        self.play_button.description = "Pause"
        self.play_button.icon = "pause"
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def pause(self):
        self.playing = False
        self.stop_event.set()
        self.play_button.description = "Play"
        self.play_button.icon = "play"

    def stop(self, _=None):
        self.pause()
        self.show(0)

    def _loop(self):
        while not self.stop_event.is_set():
            if self._debug:
                self._debug.log("loop_tick", current=self.current,
                               max_step=len(self.time_grid)-1)
            next_step = self.current + 1

            if next_step >= len(self.time_grid):
                if self.live:
                    t = self.time_grid[self.current] if self.current < len(self.time_grid) else 0
                    self.status.value = f"LIVE t={t:.3f}s — waiting..."
                    self.stop_event.wait(2.0)
                    continue
                else:
                    self.pause()
                    return

            target_time = self.time_grid[next_step]
            self._refresh_total()
            frame_idx = self._find_frame_for_time(target_time)

            if frame_idx < 0:
                if self.live:
                    self.status.value = (
                        f"LIVE t={target_time:.3f}s — waiting for data..."
                    )
                    self.stop_event.wait(2.0)
                    continue
                else:
                    self.pause()
                    return

            t0 = time.perf_counter()
            self.show(next_step, wait=False)
            elapsed = time.perf_counter() - t0
            self.stop_event.wait(max(0.0, 1.0 / max(1, self.fps.value) - elapsed))

    # ── Agent diagnostic API ────────────────────────────────────────────

    def get_status(self) -> dict:
        self._refresh_total()
        I, V, t = self.iv_cache.arrays()
        t_now = self.time_grid[self.current] if self.current < len(self.time_grid) else None
        return {
            "current_step": self.current,
            "current_time": t_now,
            "total_steps": len(self.time_grid),
            "available_frames": self.total,
            "playing": self.playing,
            "live": self.live,
            "solve_time": self.solve_time,
            "at_edge": self.current >= len(self.time_grid) - 1,
            "iv_cache": {
                "cached_points": len(I),
                "total_frames": self.total,
            },
            "buffer_frames": self.buffer.keys(),
            "h5_path": self.h5_path,
        }

    def diagnose_mapping(self) -> dict:
        """Diagnostic: show the complete slider→time→frame mapping."""
        self._refresh_total()
        # Show time_grid summary
        grid = self.time_grid
        # Show frame_times summary
        ft = self.frame_times
        # Build mapping: for each time_grid position, which frame does it map to
        mapping = []
        for step in range(len(grid)):
            t = grid[step]
            fi = self._find_frame_for_time(t) if ft else -1
            ft_val = ft[fi] if 0 <= fi < len(ft) else None
            mapping.append({"step": step, "target_time": t, "frame_idx": fi, "frame_time": ft_val})
        return {
            "time_grid_size": len(grid),
            "time_grid_range": [grid[0], grid[-1]],
            "available_frames": self.total,
            "frame_times": ft,
            "solve_time": self.solve_time,
            "playback_dt": self.playback_dt,
            "mapping": mapping,
        }

    def get_frame_data(self, idx: int) -> dict:
        idx = int(max(0, min(self.total - 1, idx)))
        import numpy as np

        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            frame = f[f"data/{idx}"]
            result = {
                "frame_idx": idx,
                "time": float(frame.attrs.get("time", idx)),
                "datasets": list(frame.keys()),
            }

            for field in ("psi", "mu"):
                if field not in frame:
                    result[f"{field}_present"] = False
                    continue
                arr = np.array(frame[field])
                arr_real = np.abs(arr) if np.iscomplexobj(arr) else arr
                result[f"{field}_present"] = True
                result[f"{field}_shape"] = list(arr.shape)
                result[f"{field}_range"] = [float(np.nanmin(arr_real)), float(np.nanmax(arr_real))]
                result[f"{field}_nan"] = int(np.sum(np.isnan(arr)))

            for field in ("normal_current", "supercurrent"):
                result[f"{field}_present"] = field in frame

            return result

    def get_iv_data(self, upto: int | None = None, step_averaged: bool = False) -> dict:
        import numpy as np

        self._refresh_total()
        target = self.total - 1 if upto is None else upto
        self.iv_cache.ensure(max(0, target))

        if step_averaged:
            avg_I, avg_V, n_completed, n_total = self.iv_cache.step_averaged_iv()
            valid = ~np.isnan(avg_V)
            I = avg_I[valid]
            V = avg_V[valid]
        else:
            I_all, V_all, t_all = self.iv_cache.arrays(upto=upto)
            valid = ~np.isnan(V_all)
            I = I_all[valid]
            V = V_all[valid]
            t = t_all[valid]

        I_min, I_max = (float(I.min()), float(I.max())) if len(I) > 0 else (0.0, 1.0)
        V_min, V_max = (float(V.min()), float(V.max())) if len(V) > 0 else (0.0, 1.0)
        if I_min == I_max:
            I_min -= 0.5; I_max += 0.5
        if V_min == V_max:
            V_min -= 0.5; V_max += 0.5

        # Current playback position on the I-V curve (uses raw data)
        current_I, current_V = None, None
        I_all_raw, V_all_raw, _ = self.iv_cache.arrays()
        if self.current < len(self.time_grid) and len(I_all_raw) > 0:
            frame_idx = self._find_frame_for_time(
                self.time_grid[self.current]
            )
            if 0 <= frame_idx < len(I_all_raw):
                current_I = float(I_all_raw[frame_idx])
                current_V = float(V_all_raw[frame_idx])

        result = {
            "n_points": len(I),
            "I": I.tolist(),
            "V": V.tolist(),
            "I_range": [I_min, I_max],
            "V_range": [V_min, V_max],
            "current_I": current_I,
            "current_V": current_V,
            "step_averaged": step_averaged,
        }
        return result


class StreamingTDGLPlayer:
    """Watches a running simulation in MinIO with a pre-allocated timeline.

    Reads timing params upfront to compute a time grid. The slider shows
    the full timeline from t=0 to t=solve_time. As data arrives from MinIO,
    frames are matched to the nearest time-grid position and rendered.

    Reads HDF5 directly from MinIO via ROS3 — no local download needed.
    """

    def __init__(self, store, run_id, poll_interval=15, argo_host=None,
                 timing_params=None, solver_options=None, playback_dt=1.0,
                 average_time=None, debug=False):
        if widgets is None:
            raise ImportError("ipywidgets is required")

        self.store = store
        self.run_id = run_id
        self.poll_interval = poll_interval
        self.argo_host = argo_host
        self._stop_event = threading.Event()
        self._poll_thread = None
        self._player = None
        self._completed = False
        self._timing_params = timing_params
        self._average_time = average_time
        self._solver_options = solver_options or {}
        self._playback_dt = playback_dt
        self._debug_flag = debug
        from tdgl_sdk.viewer._debug import DebugLog
        self._debug = DebugLog() if debug else None
        self._s3_kwds = {
            "s3_access_key": store.s3._request_signer._credentials.access_key,
            "s3_secret_key": store.s3._request_signer._credentials.secret_key,
        }
        self._h5_url = store.h5_url(run_id)

        self.status_label = widgets.Label(value="connecting...")
        self.stop_btn = widgets.Button(description="Stop watching", icon="eye-slash")
        self.stop_btn.on_click(lambda _: self.stop())
        self.output = widgets.Output()

        self.ui = widgets.VBox([
            widgets.HBox([self.status_label, self.stop_btn]),
            self.output,
        ])

    def _compute_timing_steps(self):
        """Compute timing step boundaries from timing_params."""
        if not self._timing_params:
            return None
        try:
            from tdgl_workflow.timing import build_timing
            result = build_timing(**self._timing_params)
            return result.get("steps", [])
        except Exception:
            return None

    @property
    def debug_log(self):
        return self._debug if self._debug else (self._player.debug_log if self._player else None)

    def display_player(self):
        display(self.ui)
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                manifest = self.store.get_run(self.run_id)

                if manifest is None:
                    wf_status = self._check_argo_status()
                    if wf_status == "failed":
                        self.status_label.value = "Workflow FAILED"
                        return
                    elif wf_status in ("running", "pending", "submitted"):
                        self.status_label.value = (
                            f"Workflow {wf_status} — waiting for simulation..."
                        )
                    else:
                        self.status_label.value = f"waiting... (Argo: {wf_status})"
                    self._stop_event.wait(self.poll_interval)
                    continue

                status = manifest.get("status", "unknown")
                timing = manifest.get("timing_params") or {}
                solve_time = timing.get("solve_time", 0)

                if status in ("running", "completed"):
                    try:
                        with h5open(self._h5_url, "r", **self._s3_kwds) as f:
                            n_frames = len(f["data"].keys()) if "data" in f else 0
                    except Exception:
                        n_frames = 0

                    if self._debug:
                        self._debug.log("poll_frames", status=status, n_frames=n_frames, solve_time=solve_time)

                    if n_frames == 0:
                        self.status_label.value = f"{status} — waiting for HDF5..."
                        self._stop_event.wait(self.poll_interval)
                        continue

                    if self._player is None:
                        self._create_player(status, solve_time)
                    elif solve_time > 0 and self._player.solve_time != solve_time:
                        # Update time grid if manifest now has solve_time
                        self._player.set_time_grid(solve_time, self._playback_dt)

                    if status == "completed" and not self._completed:
                        self._completed = True
                        if self._player:
                            self._player.live = False
                        self.status_label.value = f"Complete — {n_frames} frames"

                elif status == "failed":
                    self.status_label.value = (
                        f"FAILED: {manifest.get('error', '')}"
                    )
                    return
                else:
                    self.status_label.value = f"status: {status}"

            except Exception as exc:
                self.status_label.value = f"poll error: {exc}"

            self._stop_event.wait(self.poll_interval)

    def _check_argo_status(self):
        if not self.argo_host:
            return "unknown"
        try:
            import httpx

            resp = httpx.get(
                f"{self.argo_host}/api/v1/workflows/tdgl",
                params={"labelSelector": f"run-id={self.run_id}"},
                verify=False, timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items") or []
            if not items:
                return "not_found"
            phase = (items[0].get("status") or {}).get("phase", "Unknown")
            return phase.lower()
        except Exception:
            return "unknown"

    def _create_player(self, status, solve_time):
        """Create the inner player with pre-allocated time grid."""
        from IPython.display import clear_output

        with self.output:
            clear_output(wait=True)

        timing_steps = self._compute_timing_steps()

        self._player = create_player(
            self._h5_url, live=(status == "running"),
            playback_dt=self._playback_dt,
            timing_steps=timing_steps,
            average_time=self._average_time,
            debug=self._debug_flag,
            debug_log=self._debug,
            **self._s3_kwds,
        )

        # Pre-allocate the timeline from solve_time
        if solve_time and solve_time > 0:
            self._player.set_time_grid(solve_time, self._playback_dt)

        with self.output:
            clear_output(wait=True)
            self._player.display_player()  # auto-plays if live

    def stop(self):
        self._stop_event.set()
        if self._player is not None:
            self._player.pause()
            self._player.iv_cache.stop()
        self.status_label.value = "stopped"

    def get_status(self) -> dict:
        result = {
            "run_id": self.run_id,
            "watching": not self._stop_event.is_set(),
            "completed": self._completed,
            "h5_url": self._h5_url,
        }
        if self._player is not None:
            result["player"] = self._player.get_status()
        return result


def create_player(
    h5_path: str,
    live: bool = False,
    playback_dt: float = 1.0,
    timing_steps: list | None = None,
    average_time: float | None = None,
    debug: bool = False,
    debug_log=None,
    **s3_kwds,
) -> RealtimeTDGLWidgetPlayer:
    """Create a widget player for an HDF5 file.

    Args:
        h5_path: Path to the HDF5 file (local path or http:// URL for MinIO).
        live: If True, auto-plays and waits at the boundary for new frames.
        playback_dt: Simulation time per animation step (default 1.0).
        timing_steps: Optional list of step dicts from build_timing() for
                      step-averaged I-V curve.
        average_time: Duration at end of each step's stable period to average
                      V over. If None, uses full stable period [ramp_end, stable_end].
        debug: If True, enable debug logging throughout the player pipeline.
        debug_log: Optional existing DebugLog to share (used internally by
                   StreamingTDGLPlayer to avoid creating a second instance).
        **s3_kwds: S3 credentials for ROS3 driver (s3_access_key, s3_secret_key).
    """
    from tdgl_sdk.viewer._debug import DebugLog
    if debug_log is None and debug:
        debug_log = DebugLog()
    mesh = load_mesh(h5_path, **s3_kwds)
    mu_vmax = estimate_mu_vmax(h5_path, mesh["total_frames"], **s3_kwds)
    iv_cache = IVCache(h5_path, mesh, poll_interval=1.0, batch_size=128, debug_log=debug_log, **s3_kwds)
    if timing_steps is not None:
        iv_cache.set_timing_steps(timing_steps, average_time=average_time)
    iv_cache.ensure(0)
    iv_cache.start()
    player = RealtimeTDGLWidgetPlayer(h5_path, mesh, iv_cache, mu_vmax, debug_log=debug_log, **s3_kwds)
    player.live = live
    player.playback_dt = playback_dt
    return player


def watch_run(
    store, run_id: str, poll_interval: int = 15, argo_host: str | None = None,
    timing_params: dict | None = None, solver_options: dict | None = None,
    playback_dt: float = 1.0, average_time: float | None = None,
    debug: bool = False,
) -> StreamingTDGLPlayer:
    """Create a streaming player that watches a running simulation in MinIO.

    Pre-allocates the timeline from timing params so the slider shows
    the full solve_time range before any data arrives.
    """
    return StreamingTDGLPlayer(
        store, run_id, poll_interval,
        argo_host=argo_host,
        timing_params=timing_params,
        solver_options=solver_options,
        playback_dt=playback_dt,
        average_time=average_time,
        debug=debug,
    )


def debug_player(h5_path: str, seed: int = 42, **s3_kwds) -> dict:
    """Automated smoke test for the viewer player."""
    import random

    import numpy as np

    rng = random.Random(seed)
    steps = []
    errors = []

    try:
        player = create_player(h5_path, **s3_kwds)
    except Exception as exc:
        return {
            "passed": False,
            "h5_path": h5_path,
            "total_frames": 0,
            "steps": [],
            "errors": [f"create_player failed: {exc}"],
        }

    total = player.total

    status = player.get_status()
    step = {"action": "init", "ok": True, "status": status}
    if status["available_frames"] == 0:
        step["ok"] = False
        errors.append("No frames in HDF5")
    steps.append(step)

    if total == 0:
        player.iv_cache.stop()
        return {"passed": False, "h5_path": h5_path, "total_frames": 0, "steps": steps, "errors": errors}

    # Play for a few frames
    try:
        player.play()
        time.sleep(min(0.5, total * 0.05))
        player.pause()
        status = player.get_status()
        steps.append({"action": "play_pause", "ok": True, "status": status})
    except Exception as exc:
        steps.append({"action": "play_pause", "ok": False, "error": str(exc)})
        errors.append(f"play/pause failed: {exc}")

    # Seeks
    seek_indices = _pick_seek_targets(total, rng, n=4)
    for idx in seek_indices:
        try:
            # For non-live player, seek by frame index via _find_frame_for_time
            target_time = player.frame_times[idx]
            step_pos = bisect.bisect_left(player.time_grid, target_time)
            player.show(step_pos)
            frame_data = player.get_frame_data(idx)
            status = player.get_status()
            ok = True
            seek_errors = []
            if frame_data.get("psi_nan", 0) > 0:
                ok = False
                seek_errors.append(f"Frame {idx} has NaN in psi")
            if not frame_data.get("psi_present", False):
                ok = False
                seek_errors.append(f"Frame {idx} missing psi")
            if not ok:
                errors.extend(seek_errors)
            steps.append({
                "action": "seek",
                "frame": idx,
                "ok": ok,
                "error": "; ".join(seek_errors) if seek_errors else None,
                "frame_data": frame_data,
                "status": status,
            })
        except Exception as exc:
            steps.append({"action": "seek", "frame": idx, "ok": False, "error": str(exc)})
            errors.append(f"seek to frame {idx} failed: {exc}")

    # I-V data
    try:
        iv = player.get_iv_data()
        steps.append({
            "action": "check_iv",
            "ok": True,
            "n_points": iv["n_points"],
            "I_range": iv["I_range"],
            "V_range": iv["V_range"],
        })
    except Exception as exc:
        steps.append({"action": "check_iv", "ok": False, "error": str(exc)})
        errors.append(f"I-V check failed: {exc}")

    # Seek beyond
    try:
        player.show(len(player.time_grid) + 50, wait=False)
        status = player.get_status()
        steps.append({"action": "seek_beyond", "ok": True, "status": status})
    except Exception as exc:
        steps.append({"action": "seek_beyond", "ok": False, "error": str(exc)})
        errors.append(f"seek beyond failed: {exc}")

    # Stop
    try:
        player.stop()
        status = player.get_status()
        steps.append({"action": "stop", "ok": True, "status": status})
    except Exception as exc:
        steps.append({"action": "stop", "ok": False, "error": str(exc)})
        errors.append(f"stop failed: {exc}")

    player.iv_cache.stop()

    return {
        "passed": len(errors) == 0,
        "h5_path": h5_path,
        "total_frames": total,
        "steps": steps,
        "errors": errors,
    }


def _pick_seek_targets(total, rng, n=4):
    if total <= n + 1:
        return list(range(total))
    targets = set()
    targets.add(0)
    targets.add(total - 1)
    while len(targets) < min(n, total):
        targets.add(rng.randint(1, total - 2))
    return sorted(targets)
