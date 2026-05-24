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
        self.total = mesh["total_frames"]
        self.current = 0
        self.playing = False
        self.live = False
        self.stop_event = threading.Event()
        self.thread = None
        self.render_lock = threading.RLock()

        self.frame_times = self._load_frame_times_lazy()

        self._speed = 1
        self.buffer = RealtimeFrameBuffer()
        self.image = widgets.Image(
            value=self.buffer.get(0, self._render),
            format="png",
            width=FRAME_W,
        )
        self.play_button = widgets.Button(
            description="Play", icon="play", layout=widgets.Layout(width="92px")
        )
        self.speed_input = widgets.IntText(
            value=1,
            description="Speed",
            layout=widgets.Layout(width="130px"),
        )
        self.slider = widgets.IntSlider(
            value=0,
            min=0,
            max=max(0, self.total - 1),
            step=1,
            description="Frame",
            continuous_update=False,
            readout=False,
            layout=widgets.Layout(width="400px"),
        )
        self.time_label = widgets.Label(
            value=self._fmt_frame(0),
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
        self.speed_input.observe(self._on_speed, names="value")
        self.slider.observe(self._on_slider, names="value")

        self.ui = widgets.VBox([
            widgets.HBox([self.play_button, self.speed_input, self.slider, self.time_label]),
            widgets.HBox([self.fps, self.status]),
            self.image,
        ])

    def _load_frame_times_lazy(self):
        if self.total == 0:
            return []
        times = []
        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            t = float(f["data/0"].attrs.get("time", 0))
            times.append(t)
        return times

    def _ensure_frame_time(self, idx):
        if idx < len(self.frame_times):
            return
        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            for i in range(len(self.frame_times), min(idx + 1, self.total)):
                t = float(f[f"data/{i}"].attrs.get("time", i))
                self.frame_times.append(t)

    def _fmt_frame(self, idx):
        idx = max(0, min(idx, self.total - 1))
        self._ensure_frame_time(idx)
        t = self.frame_times[idx] if idx < len(self.frame_times) else 0.0
        return f"frame {idx} / {self.total - 1}  t={t:.3f}s"

    @property
    def debug_log(self):
        return self._debug

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
        available = self._available_frames()
        if available > self.total:
            with h5open(self.h5_path, "r", **self._s3_kwds) as f:
                for i in range(self.total, available):
                    t = float(f[f"data/{i}"].attrs.get("time", i))
                    self.frame_times.append(t)
            self.total = available
            self.slider.max = max(0, self.total - 1)

    def show(self, frame_idx, wait=True):
        frame_idx = max(0, min(frame_idx, self.total - 1))

        if self.live and frame_idx >= self.total and wait:
            for _ in range(120):
                self._refresh_total()
                if frame_idx < self.total:
                    break
                self.stop_event.wait(0.5)
                if self.stop_event.is_set():
                    return

        frame_idx = min(frame_idx, self.total - 1)
        if self._debug:
            self._debug.log("show", frame=frame_idx,
                            total_frames=self.total, playing=self.playing)
        with self.render_lock:
            self.current = frame_idx
            png = self.buffer.get(frame_idx, self._render)
            self.image.value = png
            try:
                self.slider.unobserve(self._on_slider, names="value")
            except Exception:
                pass
            self.slider.value = frame_idx
            self.slider.observe(self._on_slider, names="value")
            self.time_label.value = self._fmt_frame(frame_idx)
            self.buffer.keep_near(frame_idx)
            tag = "LIVE " if self.live else ""
            self.status.value = (
                f"{tag}frame {frame_idx}/{self.total - 1}; "
                f"I-V {self.iv_cache.size()}"
            )

    def _on_slider(self, change):
        if int(change["new"]) != self.current:
            self.show(change["new"])

    def _on_speed(self, change):
        new_speed = max(1, int(change.get("new", 1)))
        was_playing = self.playing
        self.pause()
        self._speed = new_speed
        self.buffer.clear()
        self.show(self.current, wait=False)
        if was_playing:
            self.play()

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

    def _loop(self):
        while not self.stop_event.is_set():
            if self._debug:
                self._debug.log("loop_tick", current=self.current,
                                speed=self._speed, total=self.total)
            next_frame = self.current + self._speed

            if next_frame >= self.total:
                if self.live:
                    self._refresh_total()
                    if next_frame >= self.total:
                        self.status.value = f"LIVE frame {self.current} — waiting..."
                        self.stop_event.wait(2.0)
                        continue
                else:
                    next_frame = self.total - 1
                    if next_frame <= self.current:
                        self.show(next_frame, wait=False)
                        self.pause()
                        return

            t0 = time.perf_counter()
            self.show(next_frame, wait=False)
            elapsed = time.perf_counter() - t0
            self.stop_event.wait(max(0.0, 1.0 / max(1, self.fps.value) - elapsed))

    # ── Agent diagnostic API ────────────────────────────────────────────

    def get_status(self) -> dict:
        self._refresh_total()
        I, V, t = self.iv_cache.arrays()
        cur_t = self.frame_times[self.current] if self.current < len(self.frame_times) else None
        return {
            "current_frame": self.current,
            "current_time": cur_t,
            "total_frames": self.total,
            "playing": self.playing,
            "live": self.live,
            "at_edge": self.current >= self.total - 1,
            "iv_cache": {
                "cached_points": len(I),
                "total_frames": self.total,
            },
            "buffer_frames": self.buffer.keys(),
            "h5_path": self.h5_path,
        }

    def diagnose_mapping(self) -> dict:
        self._refresh_total()
        return {
            "available_frames": self.total,
            "frame_times": self.frame_times,
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

        current_I, current_V = None, None
        I_all_raw, V_all_raw, _ = self.iv_cache.arrays()
        if 0 <= self.current < len(I_all_raw):
            current_I = float(I_all_raw[self.current])
            current_V = float(V_all_raw[self.current])

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
    """Watches a running simulation in MinIO with a frame-index slider.

    Reads HDF5 directly from MinIO via ROS3 — no local download needed.
    """

    def __init__(self, store, run_id, poll_interval=15, argo_host=None,
                 timing_params=None, solver_options=None,
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

                if status in ("running", "completed"):
                    try:
                        with h5open(self._h5_url, "r", **self._s3_kwds) as f:
                            n_frames = len(f["data"].keys()) if "data" in f else 0
                    except Exception:
                        n_frames = 0

                    if self._debug:
                        self._debug.log("poll_frames", status=status, n_frames=n_frames)

                    if n_frames == 0:
                        self.status_label.value = f"{status} — waiting for HDF5..."
                        self._stop_event.wait(self.poll_interval)
                        continue

                    if self._player is None:
                        self._create_player(status)
                    else:
                        self._player._refresh_total()

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

    def _create_player(self, status):
        from IPython.display import clear_output

        with self.output:
            clear_output(wait=True)

        timing_steps = self._compute_timing_steps()

        self._player = create_player(
            self._h5_url, live=(status == "running"),
            timing_steps=timing_steps,
            average_time=self._average_time,
            debug=self._debug_flag,
            debug_log=self._debug,
            **self._s3_kwds,
        )

        with self.output:
            clear_output(wait=True)
            self._player.display_player()

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
    return player


def watch_run(
    store, run_id: str, poll_interval: int = 15, argo_host: str | None = None,
    timing_params: dict | None = None, solver_options: dict | None = None,
    average_time: float | None = None, debug: bool = False,
) -> StreamingTDGLPlayer:
    """Create a streaming player that watches a running simulation in MinIO."""
    return StreamingTDGLPlayer(
        store, run_id, poll_interval,
        argo_host=argo_host,
        timing_params=timing_params,
        solver_options=solver_options,
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
    if status["total_frames"] == 0:
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
            player.show(idx)
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
        player.show(total + 50, wait=False)
        status = player.get_status()
        steps.append({"action": "seek_beyond", "ok": True, "status": status})
    except Exception as exc:
        steps.append({"action": "seek_beyond", "ok": False, "error": str(exc)})
        errors.append(f"seek beyond failed: {exc}")

    # Pause + reset
    try:
        player.pause()
        player.show(0)
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
