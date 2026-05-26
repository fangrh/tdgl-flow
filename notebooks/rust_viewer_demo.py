#%%
"""Rust Viewer Demo — tdgl_viewer_rust interactive player.

Run cell-by-cell in VS Code Interactive or Jupyter.

Prerequisites:
    cd tdgl-viewer-rust && maturin develop --release
    MinIO: kubectl port-forward -n tdgl svc/minio 30900:9000
"""

#%%
import time
import json
from tdgl_viewer_rust import TdglViewer

print("Import OK")

#%%
MINIO_URL = "http://localhost:30900"
DEFAULT_FPS = 10
DEFAULT_SPEED = 1
DEFAULT_AVERAGE_TIME = 0.5
DEFAULT_SHOW_VT_DOT = True

VIEWER_DEFAULTS = dict(
    fps=DEFAULT_FPS,
    speed=DEFAULT_SPEED,
    average_time=DEFAULT_AVERAGE_TIME,
    show_vt_dot=DEFAULT_SHOW_VT_DOT,
)

#%%
# ── Step 1: List runs ─────────────────────────────────────────────────────
viewer = TdglViewer(MINIO_URL, **VIEWER_DEFAULTS)
runs = viewer.list_runs()
print(f"Found {len(runs)} runs\n")
for i, label in enumerate(runs):
    print(f"  [{i}] {label}")

#%%
# ── Step 2: Open a run ────────────────────────────────────────────────────
SELECTED_INDEX = 0

viewer.open(run_index=SELECTED_INDEX)
total = viewer.total_frames()
print(f"Opened run {SELECTED_INDEX}: {total} frames")

#%%
# ── Step 3: Render a single frame ─────────────────────────────────────────
t0 = time.perf_counter()
png = viewer.render_frame(0)
t1 = time.perf_counter()
print(f"Frame 0: {len(png)} bytes, {(t1-t0)*1000:.1f}ms, PNG: {png[:4] == b'\\x89PNG'}")

#%%
# ── Step 4: Benchmark rendering speed ─────────────────────────────────────
viewer.open(run_index=SELECTED_INDEX)
total = viewer.total_frames()
print(f"Total frames: {total}")

N = 30
times = []
for i in range(N):
    t0 = time.perf_counter()
    viewer.render_frame(i)
    t1 = time.perf_counter()
    times.append((t1 - t0) * 1000)

avg = sum(times) / len(times)
fps = 1000.0 / avg
print(f"Avg: {avg:.1f}ms/frame ({fps:.1f} FPS)")
print(f"P50: {sorted(times)[N//2]:.1f}ms, P95: {sorted(times)[int(N*0.95)]:.1f}ms")

#%%
# ── Step 5: Start IV scan and measure ─────────────────────────────────────
viewer.open(run_index=SELECTED_INDEX)
viewer.start_iv_scan(average_time=DEFAULT_AVERAGE_TIME)
print("IV scan started (background thread)")

for i in range(15):
    time.sleep(0.3)
    prog = viewer.get_iv_progress()
    if prog["done"]:
        break
    print(f"  IV: {prog['steps_completed']}/{prog['steps_total']} steps, "
          f"scanned {prog['frames_scanned']} frames")

prog = viewer.get_iv_progress()
print(f"\nIV done: {prog['steps_completed']}/{prog['steps_total']} steps")
if prog["points"]:
    for pt in prog["points"]:
        iv = pt.get("i")
        vv = pt.get("v")
        if iv is not None and vv is not None:
            print(f"  Je={iv:.3f}  V={vv:+.8f}")

#%%
# ── Step 6: I-V curve plot ────────────────────────────────────────────────
import matplotlib.pyplot as plt

prog = viewer.get_iv_progress()
I, V = [], []
if prog["points"]:
    I = [pt["i"] for pt in prog["points"] if pt.get("i") is not None]
    V = [pt["v"] for pt in prog["points"] if pt.get("v") is not None]
    fig, ax = plt.subplots(1, 1, figsize=(6, 4))
    ax.plot(I, V, "r-o", markersize=4, linewidth=1)
    ax.set_xlabel("Je (applied current)")
    ax.set_ylabel("V (voltage)")
    ax.set_title(f"I-V Curve ({len(I)} points, avg_time={DEFAULT_AVERAGE_TIME})")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()
else:
    print("No I-V data yet")

#%%
# ── Step 7: Change average_time and re-scan ───────────────────────────────
viewer.stop_iv_scan()
viewer.start_iv_scan(average_time=0.8)
print("Re-scanning with average_time=0.8...")

time.sleep(3)
prog2 = viewer.get_iv_progress()
print(f"Done: {prog2['steps_completed']}/{prog2['steps_total']} steps")

if prog2["points"]:
    I2 = [pt["i"] for pt in prog2["points"] if pt.get("i") is not None]
    V2 = [pt["v"] for pt in prog2["points"] if pt.get("v") is not None]
    fig, ax = plt.subplots(1, 1, figsize=(6, 4))
    ax.plot(I2, V2, "b-o", markersize=4, linewidth=1, label="avg=0.8")
    if I and V:
        ax.plot(I, V, "r--o", markersize=3, linewidth=1, alpha=0.5, label="avg=0.5")
    ax.set_xlabel("Je")
    ax.set_ylabel("V")
    ax.set_title("I-V: average_time comparison")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

#%%
# ── Step 8: Simultaneous playback + IV ────────────────────────────────────
# Render frames while IV scanner runs in background
viewer.stop_iv_scan()
viewer.open(run_index=SELECTED_INDEX)
total = viewer.total_frames()

viewer.start_iv_scan(average_time=DEFAULT_AVERAGE_TIME)
print(f"Playing {total} frames while IV scans...")

render_times = []
for fi in range(0, min(100, total), 10):
    t0 = time.perf_counter()
    viewer.render_frame(fi)
    t1 = time.perf_counter()
    render_times.append((t1 - t0) * 1000)

prog = viewer.get_iv_progress()
avg_render = sum(render_times) / len(render_times)
print(f"Render: {avg_render:.1f}ms/frame ({1000/avg_render:.1f} FPS)")
print(f"IV: {prog['steps_completed']}/{prog['steps_total']} steps, done={prog['done']}")

#%%
# ── Step 9: Interactive player ─────────────────────────────────────────────
# Full interactive viewer with play/pause, run selector, FPS, speed,
# V(t) dot toggle, and IV scan average controls.
# Requires Jupyter/VS Code Interactive with ipywidgets.

viewer2 = TdglViewer(MINIO_URL, **VIEWER_DEFAULTS)
viewer2.open(run_index=SELECTED_INDEX)
viewer2.display()

#%%
# ── Step 10: Get IV data from player ──────────────────────────────────────
time.sleep(3)
iv = viewer2.get_iv_progress()
print(f"I-V: {iv['steps_completed']}/{iv['steps_total']} steps, done={iv['done']}")
if iv["points"]:
    for pt in iv["points"][:5]:
        iv_i = pt.get("i")
        iv_v = pt.get("v")
        if iv_i is not None and iv_v is not None:
            print(f"  Je={iv_i:.3f}  V={iv_v:+.8f}")

#%%
# ── Cleanup ────────────────────────────────────────────────────────────────
viewer.stop_iv_scan()
viewer2.stop_iv_scan()
print("Done.")
