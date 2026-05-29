#%%
"""Browse past cpp-tdgl runs from MinIO with rich metadata display.

Prerequisites:
    kubectl port-forward -n tdgl svc/minio 30900:9000 &
    cd cpp-tdgl-viewer-rust && maturin develop --release
"""

#%%
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tdgl_sdk.client import TDGLRunStore

MINIO_URL = "http://localhost:30900"

#%%
store = TDGLRunStore(
    endpoint_url=MINIO_URL,
    access_key="minioadmin",
    secret_key="minioadmin123",
    bucket="tdgl-results",
)

runs = store.list_runs()

# Filter to cpp-tdgl runs
cpp_runs = [
    r for r in runs
    if r.get("discrete_index_file") or r.get("tool") == "cpp-tdgl"
]

#%%
# ── Run listing with status badges ────────────────────────────────────────
from IPython.display import HTML, display

STATUS_COLORS = {
    "running": "#2196F3",
    "completed": "#4CAF50",
    "succeeded": "#4CAF50",
    "failed": "#F44336",
}

def _badge(status):
    c = STATUS_COLORS.get(status, "#757575")
    return f'<span style="background:{c};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px">{status}</span>'

if not cpp_runs:
    print("No cpp-tdgl runs found. Submit one with run_cpp_tdgl.py first.")
else:
    html = f'<div style="font-weight:bold;margin-bottom:8px">{len(cpp_runs)} cpp-tdgl run(s) ({len(runs)} total)</div>'
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px">'
    html += '<tr style="border-bottom:2px solid #444"><th style="text-align:left;padding:4px">Run ID</th><th>Status</th><th>Steps</th><th>Solve Time</th><th>Created</th><th>Device</th></tr>'

    for r in cpp_runs:
        rid = r.get("run_id", "?")
        status = r.get("status", "unknown")
        created = r.get("created_at", "?")
        steps = r.get("num_steps", "?")
        solve_time = r.get("solve_time", "?")
        # Extract device info from params if available
        dp = r.get("device_params", {})
        device_str = f'{dp.get("film_width", "?")}x{dp.get("film_height", "?")}' if dp else "?"

        html += (
            f'<tr style="border-bottom:1px solid #333">'
            f'<td style="padding:4px;font-family:monospace;font-size:11px">{rid}</td>'
            f'<td style="padding:4px;text-align:center">{_badge(status)}</td>'
            f'<td style="padding:4px;text-align:right">{steps}</td>'
            f'<td style="padding:4px;text-align:right">{solve_time}</td>'
            f'<td style="padding:4px;font-size:11px">{created[:19]}</td>'
            f'<td style="padding:4px;font-size:11px">{device_str}</td>'
            f'</tr>'
        )
    html += '</table>'
    display(HTML(html))

#%%
# ── Open latest run in viewer ─────────────────────────────────────────────
if cpp_runs:
    from cpp_tdgl_viewer_rust.widget import CppTdglViewer

    latest = cpp_runs[0]
    run_id = latest["run_id"]
    status = latest.get("status", "unknown")
    steps = latest.get("num_steps", "?")
    solve_time = latest.get("solve_time", "?")

    print(f"Opening latest run: {run_id}")
    print(f"  Status: {status}, Steps: {steps}, Solve time: {solve_time}")

    viewer = CppTdglViewer(
        MINIO_URL,
        fps=10,
        speed=5,
    )
    viewer.open(run_id=run_id)
    viewer.display()
else:
    print("No runs to view.")

#%%
# ── Open a specific run (edit RUN_ID below) ───────────────────────────────
# Uncomment and set RUN_ID to view a specific run:
# SPECIFIC_RUN_ID = "20260529-XXXXXX-abcdef"
# viewer2 = CppTdglViewer(MINIO_URL, fps=10, speed=5)
# viewer2.open(run_id=SPECIFIC_RUN_ID)
# viewer2.display()