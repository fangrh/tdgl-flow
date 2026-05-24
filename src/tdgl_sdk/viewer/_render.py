import collections
import io
import threading

import matplotlib as mpl
import numpy as np
from PIL import Image, ImageDraw

from tdgl_sdk.viewer._mesh import NX, NY, PSI_VMAX, h5open, interpolate

FRAME_W, FRAME_H = 760, 470
PANEL_W, PANEL_H = 360, 180
BUFFER_KEEP = 5

cmap_psi = mpl.colormaps["inferno"]
cmap_mu = mpl.colormaps["RdBu_r"]


def field_rgba(h5_path, points, grid_pts, idx, field, mu_vmax, **s3_kwds):
    with h5open(h5_path, "r", **s3_kwds) as f:
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


def render_frame_png(h5_path, mesh, iv_cache, mu_vmax, idx, **s3_kwds):
    idx = int(idx)
    points = mesh["points"]
    grid_pts = mesh["grid_pts"]
    total = mesh["total_frames"]

    canvas = Image.new("RGBA", (FRAME_W, FRAME_H), (30, 30, 30, 255))
    draw = ImageDraw.Draw(canvas)

    psi_arr = field_rgba(h5_path, points, grid_pts, idx, "psi", mu_vmax, **s3_kwds)
    mu_arr = field_rgba(h5_path, points, grid_pts, idx, "mu", mu_vmax, **s3_kwds)
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
    # Step-averaged curve uses ALL available data (not limited by playback)
    avg_I, avg_V, n_completed, n_total = iv_cache.step_averaged_iv(
        current_frame_idx=iv_cache.size() - 1,
    )

    # Current frame raw data for the position dot
    cur_I_raw, cur_V_raw, _ = iv_cache.arrays(upto=idx)

    # Compute ranges from step-averaged data + current dot
    all_I = np.concatenate([avg_I, cur_I_raw[~np.isnan(cur_V_raw)]]) if len(avg_I) and len(cur_I_raw) else (avg_I if len(avg_I) else cur_I_raw)
    all_V = np.concatenate([avg_V, cur_V_raw[~np.isnan(cur_V_raw)]]) if len(avg_V) and len(cur_V_raw) else (avg_V if len(avg_V) else cur_V_raw)
    if len(all_I) == 0:
        all_I = np.array([0.0, 1.0])
        all_V = np.array([0.0, 1.0])
    I_min, I_max = float(np.nanmin(all_I)), float(np.nanmax(all_I))
    V_min, V_max = float(np.nanmin(all_V)), float(np.nanmax(all_V))
    if I_min == I_max:
        I_min -= 0.5; I_max += 0.5
    if V_min == V_max:
        V_min -= 0.5; V_max += 0.5
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

    # Draw step-averaged I-V curve
    if len(avg_I) >= 1:
        pts = []
        for I_val, V_val in zip(avg_I, avg_V):
            if np.isnan(V_val):
                if len(pts) > 1:
                    draw.line(pts, fill=(233, 69, 96), width=2)
                pts = []
                continue
            x = left + (float(I_val) - I_min) / I_den * (right - left)
            y = top + (1 - (float(V_val) - V_min) / V_den) * (bottom - top)
            pts.append((x, y))
        if len(pts) > 1:
            draw.line(pts, fill=(233, 69, 96), width=2)
        # Draw small dots at each completed step
        for I_val, V_val in zip(avg_I, avg_V):
            if np.isnan(V_val):
                continue
            x = left + (float(I_val) - I_min) / I_den * (right - left)
            y = top + (1 - (float(V_val) - V_min) / V_den) * (bottom - top)
            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(233, 69, 96))

    # Current playback position dot (white on black)
    if len(cur_I_raw) and not np.isnan(cur_V_raw[-1]):
        x = left + (float(cur_I_raw[-1]) - I_min) / I_den * (right - left)
        y = top + (1 - (float(cur_V_raw[-1]) - V_min) / V_den) * (bottom - top)
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(0, 0, 0))
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255))

    draw.text(
        ((left + right) // 2 - 82, y1 - 18),
        "I (transport current)",
        fill=(150, 150, 150),
    )
    draw.text((8, y0 + 70), "V", fill=(150, 150, 150))
    # Status line: completed steps / total steps
    step_info = f"Je steps: {n_completed}/{n_total}" if n_total > 0 else f"IV cached={iv_cache.size()}"
    cur_t_raw = iv_cache.t[:idx + 1] if idx + 1 <= len(iv_cache.t) else iv_cache.t
    t_label = f"t={cur_t_raw[-1]:.3g}" if len(cur_t_raw) else "t=?"
    draw.text(
        (right - 250, top + 4),
        f"{t_label}, {step_info}",
        fill=(150, 150, 150),
    )
