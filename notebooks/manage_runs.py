#%%
"""Manage TDGL simulation runs — list, delete selected, delete all.

Run cell-by-cell in VS Code Interactive or Jupyter.
WARNING: Delete operations are irreversible!

Prerequisites:
    pip install boto3
    MinIO: kubectl port-forward -n tdgl svc/minio 30900:9000
"""

#%%
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tdgl_sdk.client import TDGLRunStore

MINIO_ENDPOINT = "http://localhost:30900"

store = TDGLRunStore(endpoint_url=MINIO_ENDPOINT)
print("Connected to MinIO")

#%%
# ── List all runs ───────────────────────────────────────────────────────
runs = store.list_runs()

print(f"Found {len(runs)} runs\n")
print(f"{'#':>3}  {'Run ID':<24} {'Status':<10} {'Frames':>6}  {'Film WxH':<14}  {'Created'}")
print("-" * 90)

for i, r in enumerate(runs):
    rid = r.get("run_id", "?")[:24]
    status = r.get("status", "?")
    n_frames = r.get("n_frames", "-")

    dp = r.get("device_params", {})
    film = f"{dp.get('film_width', '?')}x{dp.get('film_height', '?')}" if dp else "?"

    created = r.get("created_at", "?")[:19]

    print(f"{i:>3}  {rid:<24} {status:<10} {n_frames:>6}  {film:<14}  {created}")

#%%
# ── Delete a single run ─────────────────────────────────────────────────
# Set the index from the list above.
DELETE_INDEX = 0

target = runs[DELETE_INDEX]
run_id = target["run_id"]
print(f"Deleting run: {run_id} ({target.get('status')})")
store.delete_run(run_id)
print(f"Deleted {run_id}")

#%%
# ── Delete multiple runs ────────────────────────────────────────────────
# Set a list of indices, e.g. [0, 2, 5] or list(range(3, 10)).
DELETE_INDICES = [0, 1]

for idx in DELETE_INDICES:
    if idx < len(runs):
        r = runs[idx]
        rid = r["run_id"]
        print(f"Deleting #{idx}: {rid}")
        store.delete_run(rid)
print(f"Deleted {len(DELETE_INDICES)} runs")

#%%
# ── Delete ALL runs ─────────────────────────────────────────────────────
# WARNING: This deletes everything! Uncomment to run.
# deleted = store.clear_all_runs()
# print(f"Deleted {deleted} objects from MinIO")
