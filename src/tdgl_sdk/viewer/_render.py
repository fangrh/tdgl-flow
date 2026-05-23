import collections
import io
import threading

import h5py
import matplotlib as mpl
import numpy as np
from PIL import Image, ImageDraw

from tdgl_sdk.viewer._mesh import NX, NY, PSI_VMAX, interpolate

FRAME_W, FRAME_H = 760, 470
PANEL_W, PANEL_H = 360, 180
BUFFER_KEEP = 5

cmap_psi = mpl.colormaps["inferno"]
cmap_mu = mpl.colormaps["RdBu_r"]


def field_rgba(h5_path, points, grid_pts, idx, field, mu_vmax):
    with h5py.File(h5_path, "r") as f:
        if field == "psi":
            raw = np.abs(np.array(f[f"data/{idx}/psi"]))
        else:
            raw = np.array(f[f"data/{idx}/mu"])
    Z = interpolate(points, grid_pts, raw)
    if field == "psi":
        norm = np.clip(np.clip(Z, 0, None) / PSI_VMAX, 0, 1)
        return (cmap_psi(norm) * 255).astype(np.uint8)
    norm = np.clip((Z + mu_vmax) / (2 * mu_vmax), 0, 1)
    return (cmap_mu(norm) * 255).astype(np.uint8)


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


def render_frame_png(h5_path, mesh, iv_cache, mu_vmax, idx):
    idx = int(idx)
    points = mesh["points"]
    grid_pts = mesh["grid_pts"]
    total = mesh["total_frames"]

    canvas = Image.new("RGBA", (FRAME_W, FRAME_H), (30, 30, 30, 255))
    draw = ImageDraw.Draw(canvas)

    psi_arr = field_rgba(h5_path, points, grid_pts, idx, "psi", mu_vmax)
    mu_arr = field_rgba(h5_path, points, grid_pts, idx, "mu", mu_vmax)
    psi_img = Image.fromarray(psi_arr, mode="RGBA").resize(
        (PANEL_W, PANEL_H), Image.Resampling.NEAREST
    )
    mu_img = Image.fromarray(mu_arr, mode="RGBA").resize(
        (PANEL_W, PANEL_H), Image.Resampling.NEAREST
    )
    canvas.paste(psi_img, (14, 42))
    canvas.paste(mu_img, (386, 42))

    draw.text((14, 16), f"TDGL frame {idx} / {total - 1}", fill=(235, 235, 235))
    draw.text((156, 226), "|psi| inferno", fill=(120, 120, 120))
    draw.text((528, 226), "mu RdBu", fill=(120, 120, 120))
    _draw_iv(draw, iv_cache, idx, (14, 252, 746, 454))

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _draw_iv(draw, iv_cache, idx, box):
    iv_cache.ensure(idx)
    hist_I, hist_V, _ = iv_cache.arrays()
    cur_I, cur_V, cur_t = iv_cache.arrays(upto=idx)
    I_min, I_max, V_min, V_max = iv_cache.ranges()
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
            f"t={cur_t[-1]:.3g}, IV cached={iv_cache.size()}",
            fill=(150, 150, 150),
        )
