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

    def clear(self):
        with self.lock:
            self.frames.clear()

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


def _read_fields(h5_path, idx, **s3_kwds):
    """Read psi and mu arrays from HDF5 in a single file open."""
    with h5open(h5_path, "r", **s3_kwds) as f:
        psi_raw = np.abs(np.array(f[f"data/{idx}/psi"]))
        mu_raw = np.array(f[f"data/{idx}/mu"])
    return psi_raw, mu_raw


def _field_images(points, grid_pts, psi_raw, mu_raw, mu_vmax):
    """Interpolate and colormap both field arrays into PIL images."""
    psi_Z = interpolate(points, grid_pts, psi_raw)
    mu_Z = interpolate(points, grid_pts, mu_raw)
    psi_norm = np.clip(np.clip(psi_Z, 0, None) / PSI_VMAX, 0, 1)
    psi_arr = (cmap_psi(psi_norm) * 255).astype(np.uint8)
    mu_norm = np.clip((mu_Z + mu_vmax) / (2 * mu_vmax), 0, 1)
    mu_arr = (cmap_mu(mu_norm) * 255).astype(np.uint8)
    psi_img = Image.fromarray(psi_arr, mode="RGBA").resize(
        (PANEL_W, PANEL_H), Image.Resampling.NEAREST
    )
    mu_img = Image.fromarray(mu_arr, mode="RGBA").resize(
        (PANEL_W, PANEL_H), Image.Resampling.NEAREST
    )
    return psi_img, mu_img


def render_frame_png(h5_path, mesh, iv_cache, mu_vmax, idx, debug_log=None, **s3_kwds):
    idx = int(idx)
    points = mesh["points"]
    grid_pts = mesh["grid_pts"]
    total = mesh["total_frames"]

    psi_raw, mu_raw = _read_fields(h5_path, idx, **s3_kwds)
    psi_img, mu_img = _field_images(points, grid_pts, psi_raw, mu_raw, mu_vmax)

    canvas = Image.new("RGBA", (FRAME_W, FRAME_H), (30, 30, 30, 255))
    draw = ImageDraw.Draw(canvas)
    canvas.paste(psi_img, (14, 42))
    canvas.paste(mu_img, (386, 42))

    draw.text((14, 16), f"TDGL frame {idx} / {total - 1}", fill=(235, 235, 235))
    draw.text((156, 226), "|psi| inferno", fill=(120, 120, 120))
    draw.text((528, 226), "mu RdBu", fill=(120, 120, 120))
    _draw_iv(draw, iv_cache, idx, (14, 252, 746, 454), debug_log=debug_log)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def render_frame_2x2(h5_path, mesh, iv_cache, mu_vmax, idx, debug_log=None, **s3_kwds):
    """Render a 2x2 panel frame: psi, mu, V-vs-t, I-V."""
    idx = int(idx)
    points = mesh["points"]
    grid_pts = mesh["grid_pts"]
    total = mesh["total_frames"]

    psi_raw, mu_raw = _read_fields(h5_path, idx, **s3_kwds)
    psi_img, mu_img = _field_images(points, grid_pts, psi_raw, mu_raw, mu_vmax)

    canvas = Image.new("RGBA", (FRAME_W, FRAME_H), (30, 30, 30, 255))
    draw = ImageDraw.Draw(canvas)
    canvas.paste(psi_img, (14, 42))
    canvas.paste(mu_img, (386, 42))

    draw.text((14, 16), f"TDGL frame {idx} / {total - 1}", fill=(235, 235, 235))
    draw.text((156, 226), "|psi| inferno", fill=(120, 120, 120))
    draw.text((528, 226), "mu RdBu", fill=(120, 120, 120))

    # Bottom panels: V-vs-t (left), I-V (right)
    half_w = (746 - 14) // 2
    vt_box = (14, 252, 14 + half_w, 454)
    iv_box = (14 + half_w + 10, 252, 746, 454)
    _draw_vt(draw, iv_cache, idx, vt_box, debug_log=debug_log)
    _draw_iv(draw, iv_cache, idx, iv_box, debug_log=debug_log)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _draw_iv(draw, iv_cache, idx, box, debug_log=None):
    # Step-averaged curve uses ALL available data (not limited by playback)
    avg_I, avg_V, n_completed, n_total = iv_cache.step_averaged_iv(
        current_frame_idx=iv_cache.size() - 1,
    )
    if debug_log:
        debug_log.log(
            "draw_iv", frame=idx, cache_size=iv_cache.size(),
            n_avg=len(avg_I), n_completed=n_completed, n_total=n_total,
        )

    # Current frame raw data for the position dot
    cur_I, cur_V, cur_t = iv_cache.frame_iv(idx)
    cached_I_raw, cached_V_raw, _ = iv_cache.arrays(upto=idx)

    # Compute ranges from step-averaged data + current dot
    valid_avg = ~np.isnan(avg_V) if len(avg_V) else np.array([], dtype=bool)
    valid_raw = ~np.isnan(cached_V_raw) if len(cached_V_raw) else np.array([], dtype=bool)
    all_I_parts = []
    all_V_parts = []
    if len(avg_I):
        all_I_parts.append(avg_I[valid_avg])
        all_V_parts.append(avg_V[valid_avg])
    if len(cached_I_raw):
        all_I_parts.append(cached_I_raw[valid_raw])
        all_V_parts.append(cached_V_raw[valid_raw])
    if not np.isnan(cur_V):
        all_I_parts.append(np.array([cur_I], dtype=np.float64))
        all_V_parts.append(np.array([cur_V], dtype=np.float64))
    all_I = np.concatenate(all_I_parts) if all_I_parts else np.array([])
    all_V = np.concatenate(all_V_parts) if all_V_parts else np.array([])
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
    if not np.isnan(cur_V):
        x = left + (float(cur_I) - I_min) / I_den * (right - left)
        y = top + (1 - (float(cur_V) - V_min) / V_den) * (bottom - top)
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
    t_label = f"t={cur_t:.3g}"
    draw.text(
        (right - 250, top + 4),
        f"{t_label}, {step_info}",
        fill=(150, 150, 150),
    )


def _draw_vt(draw, iv_cache, idx, box, debug_log=None):
    """Draw voltage vs time for the current Je step, time starting at 0."""
    t_step, V_step, step_idx, n_steps = iv_cache.vt_step(idx)
    if len(t_step) == 0:
        t_step = np.array([0.0, 1.0])
        V_step = np.array([0.0, 1.0])

    valid = ~np.isnan(V_step)
    t_valid = t_step[valid]
    V_valid = V_step[valid]
    if len(t_valid) == 0:
        t_valid = np.array([0.0, 1.0])
        V_valid = np.array([0.0, 1.0])

    t_min, t_max = float(t_valid.min()), float(t_valid.max())
    V_min, V_max = float(V_valid.min()), float(V_valid.max())
    if t_min == t_max:
        t_min -= 0.5; t_max += 0.5
    if V_min == V_max:
        V_min -= 0.5; V_max += 0.5
    t_den = t_max - t_min or 1.0
    V_den = V_max - V_min or 1.0

    x0, y0, x1, y1 = box
    left, right, top, bottom = x0 + 54, x1 - 20, y0 + 18, y1 - 34
    draw.rectangle([x0, y0, x1, y1], fill=(30, 30, 30))

    # Grid lines + tick labels
    for t in range(5):
        y = top + (1 - t / 4) * (bottom - top)
        draw.line([(left, y), (right, y)], fill=(50, 50, 50))
        draw.text((left - 48, y - 6), f"{V_min + t / 4 * V_den:.2f}", fill=(150, 150, 150))
    for t in range(5):
        x = left + t / 4 * (right - left)
        draw.text((x - 18, bottom + 8), f"{t_min + t / 4 * t_den:.2g}", fill=(150, 150, 150))
    draw.line([(left, top), (left, bottom), (right, bottom)], fill=(105, 105, 105), width=1)

    # Blue V-vs-t trace
    pts = []
    for t_val, V_val in zip(t_valid, V_valid):
        x = left + (float(t_val) - t_min) / t_den * (right - left)
        y = top + (1 - (float(V_val) - V_min) / V_den) * (bottom - top)
        pts.append((x, y))
    if len(pts) > 1:
        draw.line(pts, fill=(100, 149, 237), width=2)

    # Axis labels
    draw.text(
        ((left + right) // 2 - 18, y1 - 18),
        "t",
        fill=(150, 150, 150),
    )
    draw.text((8, y0 + 70), "V", fill=(150, 150, 150))

    # Step info
    if n_steps and step_idx:
        draw.text(
            (right - 140, top + 4),
            f"Je step {step_idx}/{n_steps}",
            fill=(150, 150, 150),
        )
