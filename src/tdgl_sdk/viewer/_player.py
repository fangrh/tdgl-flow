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
        self.live = False  # True when simulation is still writing frames
        self.stop_event = threading.Event()
        self.thread = None
        self.render_lock = threading.RLock()

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
            layout=widgets.Layout(width="520px"),
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
        self.label = widgets.Label(value=f"0 / {max(0, self.total - 1)}")
        self.status = widgets.Label(value="heatmap buffer [0]")

        self.play_button.on_click(self.toggle)
        self.stop_button.on_click(self.stop)
        self.slider.observe(self._on_slider, names="value")

        self.ui = widgets.VBox([
            widgets.HBox([self.play_button, self.stop_button, self.slider, self.label]),
            widgets.HBox([self.fps, self.status]),
            self.image,
        ])

    def _render(self, idx):
        return render_frame_png(
            self.h5_path, self._mesh, self.iv_cache, self.mu_vmax, idx,
            **self._s3_kwds
        )

    def display_player(self):
        display(self.ui)

    def _available_frames(self) -> int:
        """Check how many frames actually exist in the HDF5 right now."""
        with h5open(self.h5_path, "r", **self._s3_kwds) as f:
            if "data" in f:
                return len(f["data"].keys())
        return 0

    def _refresh_total(self):
        """Update self.total from the live HDF5. Called before seek/render."""
        available = self._available_frames()
        if available > self.total:
            self.total = available

    def show(self, idx, wait=True):
        """Display a frame. In live mode, seeking beyond available frames
        jumps to the latest available frame and shows a waiting status.

        Args:
            idx: Frame index to display.
            wait: If True and live mode, poll until the frame exists.
                  If False, snap to latest available immediately.
        """
        self._refresh_total()
        available = self.total
        requested = idx

        if idx >= available:
            if self.live and wait:
                # Wait up to 60s for the frame to appear
                for _ in range(120):
                    self._refresh_total()
                    if self.total > idx:
                        available = self.total
                        break
                    self.stop_event.wait(0.5)
                    if self.stop_event.is_set():
                        return
            # Clamp to latest available
            idx = max(0, available - 1)

        self.slider.max = max(0, self.total - 1) if not self.live else max(0, available - 1)
        idx = int(max(0, min(self.slider.max, idx)))
        with self.render_lock:
            self.current = idx
            png = self.buffer.get(idx, self._render)
            self.image.value = png
            if self.slider.value != idx:
                self.slider.value = idx
            tag = "LIVE" if self.live else ""
            waited = f" (requested {requested}, jumped to {idx})" if requested != idx and requested >= available else ""
            self.label.value = f"{tag}{idx} / {self.slider.max}{waited}"
            self.buffer.keep_near(idx)
            keys = self.buffer.keys()
            self.status.value = (
                f"{tag} buffer {keys}; I-V cached {self.iv_cache.size()}/{self.total}"
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
                    # Wait for new frames to arrive
                    self.stop_event.wait(1.0)
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
        """Return current player state as a dict. No widgets needed.

        Agent can call this to check: how many frames, current position,
        playing state, live mode, I-V cache progress, buffer contents.
        """
        self._refresh_total()
        I, V, t = self.iv_cache.arrays()
        return {
            "current_frame": self.current,
            "total_frames": self.total,
            "playing": self.playing,
            "live": self.live,
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
        """Return psi/mu stats for a specific frame without rendering.

        Agent can call this to check data at any frame index.
        Returns shape, range, and NaN/Inf counts — no image generation.
        """
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
        """Return I-V curve data up to a frame index.

        Agent can call this to get the full I-V trace or up to a specific frame.
        Returns lists of I, V, t values and axis ranges.
        """
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
    """Watches a run in MinIO and auto-updates the viewer as new frames arrive."""

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
        self._h5_path = None

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
                        self.status_label.value = "Workflow FAILED before simulation started"
                        return
                    elif wf_status == "running":
                        self.status_label.value = "Workflow running — waiting for simulation to start..."
                    elif wf_status == "succeeded":
                        self.status_label.value = "Workflow succeeded but no results in MinIO"
                    else:
                        self.status_label.value = f"waiting... (Argo: {wf_status})"
                    self._stop_event.wait(self.poll_interval)
                    continue

                status = manifest.get("status", "unknown")

                if status in ("running", "completed"):
                    h5_path = self.store.download_h5(self.run_id)
                    if h5_path is None:
                        self.status_label.value = f"{status} — waiting for HDF5..."
                        self._stop_event.wait(self.poll_interval)
                        continue
                    with h5open(h5_path, "r") as f:
                        n_frames = len(f["data"].keys())

                    if self._player is None or self._h5_path != h5_path:
                        self._rebuild_player(h5_path, n_frames, status)
                    else:
                        self._update_frame_count(n_frames, status)

                elif status == "failed":
                    self.status_label.value = f"Run {self.run_id[:8]} FAILED: {manifest.get('error', '')}"
                    return
                else:
                    self.status_label.value = f"Run {self.run_id[:8]} status: {status}"

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

    def _rebuild_player(self, h5_path, n_frames, status):
        from IPython.display import clear_output

        with self.output:
            clear_output(wait=True)
        if self._player is not None:
            self._player.pause()
            self._player.iv_cache.stop()

        self._h5_path = h5_path
        if n_frames == 0:
            self.status_label.value = f"{status} — waiting for frames..."
            return

        self._player = create_player(h5_path)
        self.status_label.value = f"{status} — {n_frames} frames"
        with self.output:
            clear_output(wait=True)
            self._player.display_player()

    def _update_frame_count(self, n_frames, status):
        if self._player and n_frames > self._player.total:
            self._player.total = n_frames
            self._player.slider.max = max(0, n_frames - 1)
        tag = "LIVE" if status == "running" else "done"
        self.status_label.value = f"{tag} — {n_frames} frames"

    def stop(self):
        self._stop_event.set()
        if self._player is not None:
            self._player.pause()
            self._player.iv_cache.stop()
        self.status_label.value = "stopped"

    # ── Agent diagnostic API ────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return streaming watcher state as a dict. No widgets needed.

        Agent can call this to check: is the run live, how many frames
        arrived, manifest status, inner player state.
        """
        result = {
            "run_id": self.run_id,
            "watching": not self._stop_event.is_set(),
            "h5_downloaded": self._h5_path is not None,
        }
        if self._player is not None:
            result["player"] = self._player.get_status()
        return result


def create_player(h5_path: str, live: bool = False, **s3_kwds) -> RealtimeTDGLWidgetPlayer:
    """Create a widget player for an HDF5 file.

    Args:
        h5_path: Path to the HDF5 file (local path or http:// URL for MinIO).
        live: If True, the player expects the file to grow as the simulation
              runs. Seeking beyond available frames jumps to the latest frame,
              and playback waits at the boundary for new frames.
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


def watch_run(store, run_id: str, poll_interval: int = 15, argo_host: str | None = None) -> StreamingTDGLPlayer:
    """Create a streaming player that watches a running simulation in MinIO."""
    player = StreamingTDGLPlayer(store, run_id, poll_interval, argo_host=argo_host)
    return player


def debug_player(h5_path: str, seed: int = 42, **s3_kwds) -> dict:
    """Automated smoke test for the viewer player.

    Simulates human interaction: play, random seek, pause, stop.
    Checks frame data and I-V at each step.
    Returns a structured result dict for the agent — no visual output.

    Returns:
        {
            "passed": bool,
            "h5_path": str,
            "total_frames": int,
            "steps": [
                {"action": "play", "ok": true, "status": {...}},
                {"action": "seek", "frame": 42, "ok": true, "frame_data": {...}},
                ...
            ],
            "errors": [str, ...],  # only if passed=False
        }

    Usage:
        from tdgl_sdk.viewer import debug_player
        result = debug_player("sim_output.h5")
        if not result["passed"]:
            for e in result["errors"]:
                print(e)
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
        time.sleep(min(0.5, total * 0.05))  # let it run a bit
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

    # Step 3: random seeks — like clicking the progress bar
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

    # Step 5: seek beyond available frames (like clicking far ahead on progress bar)
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

    # Step 6: stop (rewind to frame 0)
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

    # Cleanup
    player.iv_cache.stop()

    return {
        "passed": len(errors) == 0,
        "h5_path": h5_path,
        "total_frames": total,
        "steps": steps,
        "errors": errors,
    }


def _pick_seek_targets(total, rng, n=4):
    """Pick n random frame indices spread across the file."""
    if total <= n + 1:
        return list(range(total))
    targets = set()
    targets.add(0)
    targets.add(total - 1)
    while len(targets) < min(n, total):
        targets.add(rng.randint(1, total - 2))
    return sorted(targets)
