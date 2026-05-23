"""Reusable TDGL HDF5 viewer extracted from notebook 009.

Provides a one-call `create_player(h5_path)` that returns an ipywidgets-based
frame player with psi/mu heatmaps and live I-V curve overlay.
"""
import collections
import io
import threading
import time

import h5py
import matplotlib as mpl
import numpy as np
from PIL import Image, ImageDraw
from scipy.interpolate import griddata

try:
    import ipywidgets as widgets
    from IPython.display import display
except ImportError:
    widgets = None
    display = None

# --- defaults ---
NX, NY = 100, 50
PSI_VMAX = 1.05
FRAME_W, FRAME_H = 760, 470
PANEL_W, PANEL_H = 360, 180
FPS_DEFAULT = 10
BUFFER_KEEP = 5

cmap_psi = mpl.colormaps["inferno"]
cmap_mu = mpl.colormaps["RdBu_r"]


def _load_mesh(h5_path):
    with h5py.File(h5_path, "r") as f:
        points = np.array(f["solution/device/mesh/sites"])
        edges = np.array(f["solution/device/mesh/edge_mesh/edges"])
        edge_dirs = np.array(f["solution/device/mesh/edge_mesh/directions"])
        dual_lengths = np.array(f["solution/device/mesh/edge_mesh/dual_edge_lengths"])
        total = len(f["data"].keys())

    xmin, xmax = points[:, 0].min(), points[:, 0].max()
    ymin, ymax = points[:, 1].min(), points[:, 1].max()
    gx = np.linspace(xmin, xmax, NX)
    gy = np.linspace(ymin, ymax, NY)
    GX, GY = np.meshgrid(gx, gy)
    grid_pts = np.column_stack([GX.ravel(), GY.ravel()])

    s1x, s2x = points[edges[:, 0], 0], points[edges[:, 1], 0]
    cross = ((s1x <= 0) & (s2x > 0)) | ((s1x > 0) & (s2x <= 0))

    return {
        "points": points,
        "edges": edges,
        "edge_dirs": edge_dirs,
        "dual_lengths": dual_lengths,
        "cross": cross,
        "grid_pts": grid_pts,
        "total_frames": total,
    }


def _estimate_mu_vmax(h5_path, total):
    mu_maxes = []
    with h5py.File(h5_path, "r") as f:
        for i in range(total):
            try:
                mu_maxes.append(float(np.abs(np.array(f[f"data/{i}/mu"])).max()))
            except Exception:
                pass
    if mu_maxes and max(mu_maxes) > 0:
        return float(np.percentile(mu_maxes, 99))
    return 1.0


def _interpolate(points, grid_pts, raw):
    return griddata(points, raw, grid_pts, method="cubic", fill_value=0.0).reshape(NY, NX)


def _field_rgba(h5_path, points, grid_pts, idx, field, mu_vmax):
    with h5py.File(h5_path, "r") as f:
        if field == "psi":
            raw = np.abs(np.array(f[f"data/{idx}/psi"]))
        else:
            raw = np.array(f[f"data/{idx}/mu"])
    Z = _interpolate(points, grid_pts, raw)
    if field == "psi":
        norm = np.clip(np.clip(Z, 0, None) / PSI_VMAX, 0, 1)
        return (cmap_psi(norm) * 255).astype(np.uint8)
    norm = np.clip((Z + mu_vmax) / (2 * mu_vmax), 0, 1)
    return (cmap_mu(norm) * 255).astype(np.uint8)


class IVCache:
    """Incremental I-V cache for an HDF5 file."""

    def __init__(self, h5_path, mesh, poll_interval=1.0, batch_size=64):
        self.h5_path = h5_path
        self._mesh = mesh
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.I = []
        self.V = []
        self.t = []
        self.last_total = 0
        self.error = None

    def start(self):
        self.stop()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=1)

    def _frame_iv(self, f, idx):
        d = f[f"data/{idx}"]
        cross = self._mesh["cross"]
        edge_dirs = self._mesh["edge_dirs"]
        dual_lengths = self._mesh["dual_lengths"]
        J = np.array(d["normal_current"]) + np.array(d["supercurrent"])
        I_val = float(np.sum(J[cross] * edge_dirs[cross, 0] * dual_lengths[cross]))
        try:
            mu_rs = np.array(d["running_state/mu"])
            dt_rs = np.array(d["running_state/dt"])
            voltage_samples = mu_rs[0] - mu_rs[1]
            dt_sum = float(dt_rs.sum())
            V_val = float(np.sum(voltage_samples * dt_rs) / dt_sum) if dt_sum > 0 else float(voltage_samples.mean())
        except Exception:
            V_val = 0.0
        t_val = float(d.attrs.get("time", idx))
        return I_val, V_val, t_val

    def update_available(self, target=None):
        with self.lock:
            start = len(self.I)
        with h5py.File(self.h5_path, "r") as f:
            available = len(f["data"].keys())
            end = available if target is None else min(available, int(target) + 1)
            while start < end:
                batch_end = min(end, start + self.batch_size)
                batch = [self._frame_iv(f, i) for i in range(start, batch_end)]
                with self.lock:
                    self.I.extend(x[0] for x in batch)
                    self.V.extend(x[1] for x in batch)
                    self.t.extend(x[2] for x in batch)
                    self.last_total = available
                start = batch_end
        return self.size()

    def ensure(self, idx):
        with self.lock:
            if len(self.I) > idx:
                return len(self.I)
        return self.update_available(target=idx)

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                self.update_available()
                self.error = None
            except Exception as exc:
                self.error = exc
            self.stop_event.wait(self.poll_interval)

    def arrays(self, upto=None):
        with self.lock:
            n = len(self.I) if upto is None else min(len(self.I), int(upto) + 1)
            return np.array(self.I[:n]), np.array(self.V[:n]), np.array(self.t[:n])

    def ranges(self):
        I, V, _ = self.arrays()
        if len(I) == 0:
            return 0.0, 1.0, 0.0, 1.0
        I_min, I_max = float(I.min()), float(I.max())
        V_min, V_max = float(V.min()), float(V.max())
        if I_min == I_max:
            I_min -= 0.5
            I_max += 0.5
        if V_min == V_max:
            V_min -= 0.5
            V_max += 0.5
        return I_min, I_max, V_min, V_max

    def size(self):
        with self.lock:
            return len(self.I)


class RealtimeFrameBuffer:
    def __init__(self, keep=BUFFER_KEEP):
        self.keep = keep
        self.frames = collections.OrderedDict()
        self.lock = threading.RLock()

    def get(self, idx, render_fn):
        idx = int(idx)
        with self.lock:
            png = self.frames.get(idx)
            if png is not None:
                self.frames.move_to_end(idx)
                return png
        png = render_fn(idx)
        with self.lock:
            self.frames[idx] = png
            self.frames.move_to_end(idx)
            self._prune()
        return png

    def _prune(self):
        while len(self.frames) > self.keep:
            self.frames.popitem(last=False)

    def keep_near(self, center):
        center = int(center)
        lo = max(0, center - 2)
        with self.lock:
            for key in list(self.frames):
                if key < lo:
                    del self.frames[key]

    def keys(self):
        with self.lock:
            return list(self.frames.keys())


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
        idx = int(idx)
        points = self._mesh["points"]
        grid_pts = self._mesh["grid_pts"]

        canvas = Image.new("RGBA", (FRAME_W, FRAME_H), (30, 30, 30, 255))
        draw = ImageDraw.Draw(canvas)

        psi_arr = _field_rgba(self.h5_path, points, grid_pts, idx, "psi", self.mu_vmax)
        mu_arr = _field_rgba(self.h5_path, points, grid_pts, idx, "mu", self.mu_vmax)
        psi_img = Image.fromarray(psi_arr, mode="RGBA").resize(
            (PANEL_W, PANEL_H), Image.Resampling.NEAREST
        )
        mu_img = Image.fromarray(mu_arr, mode="RGBA").resize(
            (PANEL_W, PANEL_H), Image.Resampling.NEAREST
        )
        canvas.paste(psi_img, (14, 42))
        canvas.paste(mu_img, (386, 42))

        draw.text((14, 16), f"TDGL frame {idx} / {self.total - 1}", fill=(235, 235, 235))
        draw.text((156, 226), "|psi| inferno", fill=(120, 120, 120))
        draw.text((528, 226), "mu RdBu", fill=(120, 120, 120))
        self._draw_iv(draw, idx, (14, 252, 746, 454))

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=False)
        return buf.getvalue()

    def _draw_iv(self, draw, idx, box):
        self.iv_cache.ensure(idx)
        hist_I, hist_V, _ = self.iv_cache.arrays()
        cur_I, cur_V, cur_t = self.iv_cache.arrays(upto=idx)
        I_min, I_max, V_min, V_max = self.iv_cache.ranges()
        I_den = I_max - I_min or 1.0
        V_den = V_max - V_min or 1.0

        x0, y0, x1, y1 = box
        left, right, top, bottom = x0 + 54, x1 - 20, y0 + 18, y1 - 34
        draw.rectangle([x0, y0, x1, y1], fill=(30, 30, 30))
        for t in range(5):
            y = top + (1 - t / 4) * (bottom - top)
            draw.line([(left, y), (right, y)], fill=(50, 50, 50))
            draw.text((left - 48, y - 6), f"{V_min + t / 4 * V_den:.2f}", fill=(150, 150, 150))
        for t in range(5):
            x = left + t / 4 * (right - left)
            draw.text((x - 18, bottom + 8), f"{I_min + t / 4 * I_den:.2f}", fill=(150, 150, 150))
        draw.line([(left, top), (left, bottom), (right, bottom)], fill=(105, 105, 105), width=1)

        pts = []
        for I, V in zip(hist_I, hist_V):
            x = left + (float(I) - I_min) / I_den * (right - left)
            y = top + (1 - (float(V) - V_min) / V_den) * (bottom - top)
            pts.append((x, y))
        if len(pts) > 1:
            draw.line(pts, fill=(233, 69, 96), width=2)
        if len(cur_I):
            x = left + (float(cur_I[-1]) - I_min) / I_den * (right - left)
            y = top + (1 - (float(cur_V[-1]) - V_min) / V_den) * (bottom - top)
            draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(0, 0, 0))
            draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255))

        draw.text(
            ((left + right) // 2 - 82, y1 - 18),
            "I (transport current)",
            fill=(150, 150, 150),
        )
        draw.text((8, y0 + 70), "V", fill=(150, 150, 150))
        if len(cur_t):
            draw.text(
                (right - 200, top + 4),
                f"t={cur_t[-1]:.3g}, IV cached={self.iv_cache.size()}",
                fill=(150, 150, 150),
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


def create_player(h5_path: str) -> RealtimeTDGLWidgetPlayer:
    """Create a widget player for an HDF5 file.

    Returns a RealtimeTDGLWidgetPlayer. Call .display_player() to show it.
    """
    mesh = _load_mesh(h5_path)
    mu_vmax = _estimate_mu_vmax(h5_path, mesh["total_frames"])
    iv_cache = IVCache(h5_path, mesh, poll_interval=1.0, batch_size=128)
    iv_cache.ensure(0)
    iv_cache.start()
    player = RealtimeTDGLWidgetPlayer(h5_path, mesh, iv_cache, mu_vmax)
    return player


class StreamingTDGLPlayer:
    """Watches a run in MinIO and auto-updates the viewer as new frames arrive.

    Downloads the HDF5 from MinIO, opens a player, and periodically re-downloads
    to pick up new frames written by the runner's periodic upload thread.
    """

    def __init__(self, store, run_id, poll_interval=15):
        if widgets is None:
            raise ImportError("ipywidgets is required")

        self.store = store
        self.run_id = run_id
        self.poll_interval = poll_interval
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
                status = manifest.get("status", "unknown")

                if status in ("running", "completed"):
                    new_path = self.store.download_h5(self.run_id)
                    with h5py.File(new_path, "r") as f:
                        n_frames = len(f["data"].keys())

                    if self._player is None or self._h5_path != new_path:
                        self._rebuild_player(new_path, n_frames, status)
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

    def _rebuild_player(self, h5_path, n_frames, status):
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


def watch_run(store, run_id: str, poll_interval: int = 15) -> StreamingTDGLPlayer:
    """Create a streaming player that watches a running simulation in MinIO.

    Usage:
        player = watch_run(store, "my-run-id")
        player.display_player()
    """
    player = StreamingTDGLPlayer(store, run_id, poll_interval)
    return player
