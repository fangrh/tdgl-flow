#%%
"""Rust-accelerated TDGL 2x2 viewer — browse all runs (single-file and discrete).

Panels: |psi| heatmap (top-left), mu heatmap (top-right),
        V vs time (bottom-left), I-V curve (bottom-right).

Prerequisites:
    cd tdgl-viewer-rust && maturin develop --release
    MinIO: kubectl port-forward -n tdgl svc/minio 30900:9000
"""

#%%
from tdgl_viewer_rust.widget import TdglViewer, TdglDiscreteViewer

MINIO_URL = "http://localhost:30900"

#%%
# ── Discrete (Triton) viewer ──────────────────────────────────────────────
discrete = TdglDiscreteViewer(
    MINIO_URL,
    fps=10,
    speed=5,
    average_time=0.5,
    show_vt_dot=True,
    refresh_interval=5.0,
    debug=True,
)
discrete.display()

#%%
# ── Single-file viewer (K8s runs) ─────────────────────────────────────────
viewer = TdglViewer(
    MINIO_URL,
    fps=10,
    speed=1,
    average_time=0.5,
    show_vt_dot=True,
    debug=True,
)
viewer.display()

# %%
