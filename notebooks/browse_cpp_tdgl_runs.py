#%%
"""Browse past cpp-tdgl runs from MinIO.

Prerequisites:
    kubectl port-forward -n tdgl svc/minio 30900:9000 &
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

# Filter to cpp-tdgl runs (those with a discrete_index_file in the manifest)
cpp_runs = [
    r for r in runs
    if r.get("discrete_index_file") or r.get("tool") == "cpp-tdgl"
]

print(f"Found {len(cpp_runs)} cpp-tdgl run(s) ({len(runs)} total):\n")
for r in cpp_runs:
    run_id = r.get("run_id", "?")
    status = r.get("status", "?")
    created = r.get("created_at", "?")
    steps = r.get("num_steps", "?")
    solve_time = r.get("solve_time", "?")
    print(f"  {run_id}  status={status}  steps={steps}  "
          f"solve_time={solve_time}  created={created}")

#%%
# ── Open a specific run in the viewer ────────────────────────────────────
if cpp_runs:
    from cpp_tdgl_viewer_rust.widget import CppTdglViewer

    latest = cpp_runs[0]
    run_id = latest["run_id"]
    print(f"\nOpening latest run: {run_id}")

    viewer = CppTdglViewer(MINIO_URL)
    viewer.open(run_id=run_id)
    viewer.display()
else:
    print("No cpp-tdgl runs found. Submit one with run_cpp_tdgl.py first.")

#%%
