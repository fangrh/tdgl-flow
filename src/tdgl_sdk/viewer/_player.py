import threading
import time

import h5py

from tdgl_sdk.viewer._iv import IVCache
from tdgl_sdk.viewer._mesh import estimate_mu_vmax, load_mesh
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
    def __init__(self, h5_path, mesh, iv_cache, mu_vmax):
        if widgets is None:
            raise ImportError("ipywidgets is required for the widget player")

        self.h5_path = h5_path
        self._mesh = mesh
        self.iv_cache = iv_cache
        self.mu_vmax = mu_vmax
        self.total = mesh["total_frames"]
        self.current = 0
        self.playing = False
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
            self.h5_path, self._mesh, self.iv_cache, self.mu_vmax, idx
        )

    def display_player(self):
        display(self.ui)

    def show(self, idx):
        self.slider.max = max(0, self.total - 1)
        idx = int(max(0, min(self.slider.max, idx)))
        with self.render_lock:
            self.current = idx
            png = self.buffer.get(idx, self._render)
            self.image.value = png
            if self.slider.value != idx:
                self.slider.value = idx
            self.label.value = f"{idx} / {self.slider.max}"
            self.buffer.keep_near(idx)
            keys = self.buffer.keys()
            self.status.value = (
                f"buffer {keys}; I-V cached {self.iv_cache.size()}/{self.total}"
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
            if next_idx >= self.total:
                self.pause()
                return
            t0 = time.perf_counter()
            self.show(next_idx)
            elapsed = time.perf_counter() - t0
            self.stop_event.wait(max(0.0, 1.0 / max(1, self.fps.value) - elapsed))


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
                    with h5py.File(h5_path, "r") as f:
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


def create_player(h5_path: str) -> RealtimeTDGLWidgetPlayer:
    """Create a widget player for an HDF5 file."""
    mesh = load_mesh(h5_path)
    mu_vmax = estimate_mu_vmax(h5_path, mesh["total_frames"])
    iv_cache = IVCache(h5_path, mesh, poll_interval=1.0, batch_size=128)
    iv_cache.ensure(0)
    iv_cache.start()
    player = RealtimeTDGLWidgetPlayer(h5_path, mesh, iv_cache, mu_vmax)
    return player


def watch_run(store, run_id: str, poll_interval: int = 15, argo_host: str | None = None) -> StreamingTDGLPlayer:
    """Create a streaming player that watches a running simulation in MinIO."""
    player = StreamingTDGLPlayer(store, run_id, poll_interval, argo_host=argo_host)
    return player
