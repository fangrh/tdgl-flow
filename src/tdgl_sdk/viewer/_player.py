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
    def __init__(self, h5_path, mesh, iv_cache, mu_vmax, **s3_kwds):
        if widgets is None:
            raise ImportError("ipywidgets is required for the widget player")

        self.h5_path = h5_path
        self._mesh = mesh
        self.iv_cache = iv_cache
        self.mu_vmax = mu_vmax
        self._s3_kwds = s3_kwds
        self.total = mesh["total_frames"]
        self.current = 0
        self.playing = False
        self.live = False
        self.stop_event = threading.Event()
        self.thread = None
        self.render_lock = threading.RLock()

        # Frame times — physical simulation time for each saved frame
        self.frame_times = self._load_frame_times()
        # Expected total simulation time (set by StreamingTDGLPlayer from manifest)
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
            max=max(0, self.total - 1),
            step=1,
            description="Frame",
            continuous_update=False,
            layout=widgets.Layout(width="400px"),
        )
        self.time_label = widgets.Label(
            value=self._fmt_time(0),
            layout=widgets.Layout(width="180px"),
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

    def _fmt_time(self, idx):
        """Format time display: 't=X.XXX / Y.YYY s'."""
        if not self.frame_times:
            return f"t=? / {self.solve_time:.3f} s" if self.solve_time else "t=? / ? s"
        t = self.frame_times[min(idx, len(self.frame_times) - 1)]
        t_end = self.solve_time or self.frame_times[-1]
        return f"t={t:.3f} / {t_end:.3f} s"

    def _render(self, idx):
        return render_frame_png(
            self.h5_path, self._mesh, self.iv_cache, self.mu_vmax, idx,
            **self._s3_kwds
        )

    def display_player(self):
        display(self.ui)
        if self.live and self.total > 0:
            self.play()

    def _available_frames(self) -> int:
        """Check how many frames actually exist in the HDF5 right now."""
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

    def show(self, idx, wait=True):
        """Display a frame.

        In live mode, seeking beyond available frames jumps to the latest
        available frame and shows a waiting status.
        """
        self._refresh_total()
        available = self.total
        requested = idx

        if idx >= available:
            if self.live and wait:
                for _ in range(120):
                    self._refresh_total()
                    if self.total > idx:
                        available = self.total
                        break
                    self.stop_event.wait(0.5)
                    if self.stop_event.is_set():
                        return
            idx = max(0, available - 1)

        self.slider.max = max(0, available - 1)
        idx = int(max(0, min(self.slider.max, idx)))
        with self.render_lock:
            self.current = idx
            png = self.buffer.get(idx, self._render)
            self.image.value = png
            if self.slider.value != idx:
                self.slider.value = idx
            self.time_label.value = self._fmt_time(idx)
            self.buffer.keep_near(idx)
            tag = "LIVE " if self.live else ""
            self.status.value = (
                f"{tag}frame {idx}/{available-1}; "
                f"I-V {self.iv_cache.size()}/{self.total}"
            )

    def _on_slider(self, change):
        if int(change["new"]) != self.current:
            self.show(change["new"])

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
            next_idx = self.current + 1
            self._refresh_total()
            if next_idx >= self.total:
                if self.live:
                    t_now = (
                        self.frame_times[self.current]
                        if self.current < len(self.frame_times)
                        else 0
                    )
                    self.status.value = (
                        f"LIVE t={t_now:.3f}s — waiting for frame {next_idx}..."
                    )
                    self.stop_event.wait(2.0)
                    continue
                else:
                    self.pause()
                    return
            t0 = time.perf_counter()
            self.show(next_idx, wait=False)
            elapsed = time.perf_counter() - t0
            self.stop_event.wait(max(0.0, 1.0 / max(1, self.fps.value) - elapsed))

    # ── Agent diagnostic API ────────────────────────────────────────────

    def get_status(self) -> dict:
        self._refresh_total()
        I, V, t = self.iv_cache.arrays()
        return {
            "current_frame": self.current,
            "total_frames": self.total,
            "playing": self.playing,
            "live": self.live,
            "current_time": (
                self.frame_times[self.current]
                if self.current < len(self.frame_times)
                else None
            ),
            "total_time": self.frame_times[-1] if self.frame_times else None,
            "at_edge": self.current >= self.total - 1,
            "iv_cache": {
                "cached_points": len(I),
                "total_frames": self.total,
                "progress_pct": round(len(I) / max(1, self.total) * 100, 1),
            },
            "buffer_frames": self.buffer.keys(),
            "h5_path": self.h5_path,
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

    def get_iv_data(self, upto: int | None = None) -> dict:
        self.iv_cache.ensure(upto or 0)
        I, V, t = self.iv_cache.arrays(upto=upto)
        I_min, I_max, V_min, V_max = self.iv_cache.ranges()
        return {
            "n_points": len(I),
            "I": I.tolist(),
            "V": V.tolist(),
            "t": t.tolist(),
            "I_range": [I_min, I_max],
            "V_range": [V_min, V_max],
        }


class StreamingTDGLPlayer:
    """Watches a running simulation in MinIO and shows live animation.

    Waits for data to appear, creates the inner player with live=True (which
    auto-plays and waits at the boundary for new frames), then monitors the
    manifest for completion/failure.

    Reads HDF5 directly from MinIO via ROS3 — no local download needed.
    """

    def __init__(self, store, run_id, poll_interval=15, argo_host=None):
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
        self._solve_time = 0.0  # total simulation time from manifest
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

                # Track expected simulation time from manifest
                timing = manifest.get("timing_params") or {}
                if timing.get("solve_time"):
                    self._solve_time = timing["solve_time"]

                if status in ("running", "completed"):
                    try:
                        with h5open(self._h5_url, "r", **self._s3_kwds) as f:
                            n_frames = len(f["data"].keys()) if "data" in f else 0
                    except Exception:
                        n_frames = 0

                    if n_frames == 0:
                        self.status_label.value = f"{status} — waiting for HDF5..."
                        self._stop_event.wait(self.poll_interval)
                        continue

                    # Create player once when data first appears
                    if self._player is None:
                        self._create_player(status)

                    # Mark completion
                    if status == "completed" and not self._completed:
                        self._completed = True
                        if self._player:
                            self._player.live = False
                        self.status_label.value = f"Complete — {n_frames} frames"
                    elif status == "running" and not self._completed:
                        self.status_label.value = f"LIVE — {n_frames} frames"

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
        """Create the inner player once when data first appears."""
        from IPython.display import clear_output

        with self.output:
            clear_output(wait=True)

        self._player = create_player(
            self._h5_url, live=(status == "running"), **self._s3_kwds
        )
        if self._solve_time > 0:
            self._player.solve_time = self._solve_time
        with self.output:
            clear_output(wait=True)
            self._player.display_player()  # auto-plays if live

    def stop(self):
        self._stop_event.set()
        if self._player is not None:
            self._player.pause()
            self._player.iv_cache.stop()
        self.status_label.value = "stopped"

    # ── Agent diagnostic API ────────────────────────────────────────────

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
    **s3_kwds,
) -> RealtimeTDGLWidgetPlayer:
    """Create a widget player for an HDF5 file.

    Args:
        h5_path: Path to the HDF5 file (local path or http:// URL for MinIO).
        live: If True, auto-plays and waits at the boundary for new frames.
        **s3_kwds: S3 credentials for ROS3 driver (s3_access_key, s3_secret_key).
    """
    mesh = load_mesh(h5_path, **s3_kwds)
    mu_vmax = estimate_mu_vmax(h5_path, mesh["total_frames"], **s3_kwds)
    iv_cache = IVCache(h5_path, mesh, poll_interval=1.0, batch_size=128, **s3_kwds)
    iv_cache.ensure(0)
    iv_cache.start()
    player = RealtimeTDGLWidgetPlayer(h5_path, mesh, iv_cache, mu_vmax, **s3_kwds)
    player.live = live
    return player


def watch_run(
    store, run_id: str, poll_interval: int = 15, argo_host: str | None = None
) -> StreamingTDGLPlayer:
    """Create a streaming player that watches a running simulation in MinIO.

    Reads HDF5 directly via ROS3 — no local download needed.
    """
    return StreamingTDGLPlayer(store, run_id, poll_interval, argo_host=argo_host)


def debug_player(h5_path: str, seed: int = 42, **s3_kwds) -> dict:
    """Automated smoke test for the viewer player.

    Simulates human interaction: play, random seek, pause, stop.
    Checks frame data and I-V at each step.
    Returns a structured result dict for the agent — no visual output.
    """
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

    # Step 1: check initial state
    status = player.get_status()
    step = {"action": "init", "ok": True, "status": status}
    if status["total_frames"] == 0:
        step["ok"] = False
        step["error"] = "No frames in HDF5"
        errors.append("No frames in HDF5")
    if status["playing"]:
        step["ok"] = False
        step["error"] = "Player should not be playing at init"
        errors.append("Player should not be playing at init")
    steps.append(step)

    if total == 0:
        player.iv_cache.stop()
        return {"passed": False, "h5_path": h5_path, "total_frames": 0, "steps": steps, "errors": errors}

    # Step 2: play for a few frames
    try:
        player.play()
        time.sleep(min(0.5, total * 0.05))
        player.pause()
        status = player.get_status()
        step = {"action": "play_pause", "ok": True, "status": status}
        if not status["playing"] is False:
            step["ok"] = False
            step["error"] = "Player still playing after pause"
            errors.append("Player still playing after pause")
        steps.append(step)
    except Exception as exc:
        steps.append({"action": "play_pause", "ok": False, "error": str(exc)})
        errors.append(f"play/pause failed: {exc}")

    # Step 3: random seeks
    seek_indices = _pick_seek_targets(total, rng, n=4)
    for idx in seek_indices:
        try:
            player.show(idx)
            frame_data = player.get_frame_data(idx)
            status = player.get_status()
            ok = True
            seek_errors = []
            if status["current_frame"] != idx:
                ok = False
                seek_errors.append(f"Expected frame {idx}, got {status['current_frame']}")
            if frame_data.get("psi_nan", 0) > 0:
                ok = False
                seek_errors.append(f"Frame {idx} has {frame_data['psi_nan']} NaN in psi")
            if not frame_data.get("psi_present", False):
                ok = False
                seek_errors.append(f"Frame {idx} missing psi dataset")
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

    # Step 4: check I-V data
    try:
        iv = player.get_iv_data()
        ok = True
        iv_errors = []
        if iv["n_points"] == 0:
            ok = False
            iv_errors.append("I-V cache is empty")
        for name in ("I", "V", "t"):
            if name not in iv or len(iv[name]) == 0:
                ok = False
                iv_errors.append(f"I-V missing {name} data")
        if not ok:
            errors.extend(iv_errors)
        steps.append({
            "action": "check_iv",
            "ok": ok,
            "error": "; ".join(iv_errors) if iv_errors else None,
            "n_points": iv["n_points"],
            "I_range": iv["I_range"],
            "V_range": iv["V_range"],
        })
    except Exception as exc:
        steps.append({"action": "check_iv", "ok": False, "error": str(exc)})
        errors.append(f"I-V check failed: {exc}")

    # Step 5: seek beyond available
    beyond_idx = total + 50
    try:
        player.show(beyond_idx, wait=False)
        status = player.get_status()
        ok = status["current_frame"] < beyond_idx
        if not ok:
            errors.append(f"Seek to {beyond_idx} did not snap to available: frame={status['current_frame']}")
        steps.append({
            "action": "seek_beyond",
            "requested": beyond_idx,
            "landed_on": status["current_frame"],
            "ok": ok,
            "status": status,
        })
    except Exception as exc:
        steps.append({"action": "seek_beyond", "ok": False, "error": str(exc)})
        errors.append(f"seek beyond failed: {exc}")

    # Step 6: stop
    try:
        player.stop()
        status = player.get_status()
        ok = status["current_frame"] == 0
        if not ok:
            errors.append(f"Stop did not rewind: frame={status['current_frame']}")
        steps.append({"action": "stop", "ok": ok, "status": status})
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
