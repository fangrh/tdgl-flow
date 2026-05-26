#%%
"""Rust-accelerated TDGL 2x2 viewer demo.

Panels: |psi| heatmap (top-left), mu heatmap (top-right),
        V vs time (bottom-left), I-V curve (bottom-right).

Prerequisites:
    cd tdgl-viewer-rust && maturin develop --release
    MinIO: kubectl port-forward -n tdgl svc/minio 30900:9000
"""

#%%
from tdgl_viewer_rust.widget import TdglViewer

MINIO_URL = "http://localhost:30900"
DEFAULT_FPS = 10
DEFAULT_SPEED = 1
DEFAULT_AVERAGE_TIME = 0.5
DEFAULT_SHOW_VT_DOT = True

viewer = TdglViewer(
    MINIO_URL,
    fps=DEFAULT_FPS,
    speed=DEFAULT_SPEED,
    average_time=DEFAULT_AVERAGE_TIME,
    show_vt_dot=DEFAULT_SHOW_VT_DOT,
)

#%%
# List available runs
runs = viewer.list_runs()
for i, label in enumerate(runs):
    print(f"  {i}: {label}")

#%%
# Open first run and show interactive player
viewer.open(run_index=0)
print(f"Total frames: {viewer.total_frames()}")

# Display the interactive 2x2 player with play/pause, FPS, speed,
# V(t) dot toggle, and IV scan average controls.
viewer.display()

# %%
