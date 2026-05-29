#%%
"""Run & Data Manager — manage Argo Workflows + MinIO datasets.

Prerequisites:
    kubectl port-forward -n tdgl svc/argo-server 30080:2746 &
    kubectl port-forward -n tdgl svc/minio 30900:9000 &

Features:
    - List / stop / delete Argo workflows
    - Auto-cancel Slurm jobs on Triton when stopping triton-tdgl workflows
    - Browse / delete MinIO datasets
    - Storage stats
    - One-click full cleanup
"""

#%%
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import json
import os
import subprocess

import httpx
import ipywidgets as w
from IPython.display import HTML, clear_output, display

from tdgl_sdk import TDGLRunStore
from tdgl_sdk.pipeline import SimulationPipeline

# ── Configuration ──────────────────────────────────────────────────────
ARGO_URL = "http://localhost:30080"
MINIO_URL = "http://localhost:30900"
NAMESPACE = "tdgl"

# Triton SSH (for cancelling Slurm jobs)
TRITON_HOST = os.environ.get("TRITON_HOST", "fangr1@code.triton.aalto.fi")
SSH_KEY = os.environ.get(
    "SSH_KEY_PATH",
    str(Path.home() / ".ssh" / "id_ed25519"),
)

store = TDGLRunStore(endpoint_url=MINIO_URL)
pipeline = SimulationPipeline(argo_url=ARGO_URL, minio_endpoint=MINIO_URL)

# ── Shared helpers ─────────────────────────────────────────────────────

PHASE_COLORS = {
    "Running": "#2196F3",
    "Succeeded": "#4CAF50",
    "Failed": "#F44336",
    "Error": "#FF9800",
    "Pending": "#9E9E9E",
    "Submitted": "#9E9E9E",
}


def _badge(phase: str) -> str:
    c = PHASE_COLORS.get(phase, "#757575")
    return (
        f'<span style="background:{c};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:11px">{phase}</span>'
    )


def _list_workflows() -> list[dict]:
    """Fetch all workflows from Argo. Returns list of dicts."""
    r = httpx.get(
        f"{ARGO_URL}/api/v1/workflows/{NAMESPACE}",
        verify=False, timeout=10,
    )
    r.raise_for_status()
    rows = []
    for wf in r.json().get("items", []):
        name = wf["metadata"]["name"]
        status = wf.get("status") or {}
        rows.append({
            "name": name,
            "phase": status.get("phase", "Unknown"),
            "started": status.get("startedAt", "?"),
            "finished": status.get("finishedAt", "-"),
            "message": status.get("message", ""),
        })
    return rows


def _stop_workflow(name: str) -> list[str]:
    """Stop an Argo workflow. If it is a triton-tdgl run, also scancel
    the associated Slurm job via SSH. Returns list of log lines."""
    logs = []

    # 1. Try to cancel associated Slurm job BEFORE stopping the workflow
    #    so the runner process is still alive to upload a final manifest.
    is_triton = name.startswith("triton-tdgl")
    triton_job_id = None

    if is_triton:
        # Extract run_id from workflow parameters
        try:
            r = httpx.get(
                f"{ARGO_URL}/api/v1/workflows/{NAMESPACE}/{name}",
                verify=False, timeout=10,
            )
            r.raise_for_status()
            wf_data = r.json()
            params = (
                (wf_data.get("spec") or {}).get("arguments", {}).get("parameters", [])
            )
            for p in params:
                if p.get("name") == "run-id":
                    run_id = p.get("value", "")
                    break
            else:
                run_id = ""

            if run_id:
                manifest = store.get_run(run_id)
                if manifest and "triton_job_id" in manifest:
                    triton_job_id = manifest["triton_job_id"]
        except Exception as e:
            logs.append(f"Warning: could not look up Slurm job: {e}")

        if triton_job_id:
            try:
                ssh_opts = [
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=10",
                    "-o", "UserKnownHostsFile=/dev/null",
                ]
                result = subprocess.run(
                    ["ssh", "-i", SSH_KEY, *ssh_opts, TRITON_HOST,
                     f"scancel {triton_job_id}"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if result.returncode == 0:
                    logs.append(f"Cancelled Slurm job {triton_job_id} on Triton")
                else:
                    logs.append(
                        f"scancel returned {result.returncode}: {result.stderr.strip()}"
                    )
            except Exception as e:
                logs.append(f"SSH scancel failed: {e}")

    # 2. Stop the Argo workflow
    resp = httpx.put(
        f"{ARGO_URL}/api/v1/workflows/{NAMESPACE}/{name}/stop",
        verify=False, timeout=10,
    )
    logs.append(f"Stopped workflow {name} (HTTP {resp.status_code})")
    return logs


def _delete_workflow(name: str) -> str:
    """Delete an Argo workflow. Returns status string."""
    resp = httpx.delete(
        f"{ARGO_URL}/api/v1/workflows/{NAMESPACE}/{name}",
        verify=False, timeout=10,
    )
    return f"{name} (HTTP {resp.status_code})"


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024 ** 3:.2f} GB"


print("Ready.")

#%%
# ── 1. Argo Workflow Dashboard ─────────────────────────────────────────

wf_out = w.Output(layout=w.Layout(max_height="400px", overflow="auto"))
wf_act = w.Output()
wf_refresh = w.Button(description="🔄 Refresh", button_style="info",
                       layout=w.Layout(width="120px"))
wf_stop_all = w.Button(description="⏹ Stop All Running", button_style="danger",
                        layout=w.Layout(width="180px"))
wf_del_done = w.Button(description="🗑 Delete Finished", button_style="warning",
                        layout=w.Layout(width="180px"))


def _render_wf_table(rows: list[dict]) -> str:
    if not rows:
        return "<i>No workflows found.</i>"
    running = sum(1 for r in rows if r["phase"] == "Running")
    done = sum(1 for r in rows if r["phase"] in ("Succeeded", "Failed", "Error"))
    html = (
        f'<div style="margin-bottom:8px;font-weight:bold">'
        f"{len(rows)} workflows — {running} running, {done} finished</div>"
    )
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px">'
    html += (
        '<tr style="border-bottom:2px solid #444">'
        "<th style=\"text-align:left;padding:4px\">Name</th>"
        "<th>Phase</th><th>Started</th><th>Finished</th><th>Message</th></tr>"
    )
    for r in sorted(rows, key=lambda x: x["started"], reverse=True):
        started_ts = r["started"][11:19] if len(r["started"]) > 10 else r["started"]
        finished_ts = r["finished"][11:19] if len(r["finished"]) > 10 else r["finished"]
        msg = r["message"][:60] + "..." if len(r["message"]) > 60 else r["message"]
        is_triton = r["name"].startswith("triton-tdgl")
        icon = " 🖥" if is_triton else ""
        html += (
            f'<tr style="border-bottom:1px solid #333">'
            f'<td style="padding:4px;font-family:monospace;font-size:11px">'
            f"{r['name']}{icon}</td>"
            f'<td style="padding:4px;text-align:center">{_badge(r["phase"])}</td>'
            f'<td style="padding:4px;font-size:11px">{started_ts}</td>'
            f'<td style="padding:4px;font-size:11px">{finished_ts}</td>'
            f'<td style="padding:4px;font-size:11px;color:#aaa">{msg}</td>'
            f"</tr>"
        )
    html += "</table>"
    return html


def _on_wf_refresh(_=None):
    with wf_out:
        clear_output(wait=True)
        try:
            rows = _list_workflows()
            display(HTML(_render_wf_table(rows)))
        except Exception as e:
            display(HTML(f'<span style="color:red">Error: {e}</span>'))


def _on_stop_all(_=None):
    with wf_act:
        clear_output(wait=True)
        try:
            rows = _list_workflows()
            running = [r for r in rows if r["phase"] == "Running"]
            if not running:
                print("No running workflows.")
                return
            for r in running:
                logs = _stop_workflow(r["name"])
                for line in logs:
                    print(line)
            _on_wf_refresh()
        except Exception as e:
            print(f"Error: {e}")


def _on_del_done(_=None):
    with wf_act:
        clear_output(wait=True)
        try:
            rows = _list_workflows()
            finished = [r for r in rows if r["phase"] in ("Succeeded", "Failed", "Error")]
            if not finished:
                print("No finished workflows to delete.")
                return
            for r in finished:
                print(f"Deleted {_delete_workflow(r['name'])}")
            _on_wf_refresh()
        except Exception as e:
            print(f"Error: {e}")


wf_refresh.on_click(_on_wf_refresh)
wf_stop_all.on_click(_on_stop_all)
wf_del_done.on_click(_on_del_done)

display(w.HBox([wf_refresh, wf_stop_all, wf_del_done]))
display(wf_out)
display(wf_act)
_on_wf_refresh()

#%%
# ── 2. Individual Workflow Actions (Slurm-aware stop) ──────────────────

wf_sel_out = w.Output()
wf_sel = w.SelectMultiple(
    layout=w.Layout(width="100%", height="200px"),
)
wf_sel_load = w.Button(description="📋 Load", layout=w.Layout(width="100px"))
wf_sel_stop = w.Button(
    description="⏹ Stop Selected", button_style="danger",
    layout=w.Layout(width="160px"),
    tooltip="Stop selected workflows. Cancels Slurm job if triton-tdgl.",
)
wf_sel_del = w.Button(
    description="🗑 Delete Selected", button_style="warning",
    layout=w.Layout(width="160px"),
)


def _load_wf_sel(_=None):
    try:
        rows = _list_workflows()
        wf_sel.options = [
            (f"[{r['phase']}] {r['name']}", r["name"]) for r in rows
        ]
    except Exception as e:
        with wf_sel_out:
            print(f"Error: {e}")


def _stop_selected(_=None):
    with wf_sel_out:
        clear_output(wait=True)
        selected = wf_sel.value
        if not selected:
            print("Select workflows first.")
            return
        for name in selected:
            logs = _stop_workflow(name)
            for line in logs:
                print(line)
        _load_wf_sel()


def _del_selected(_=None):
    with wf_sel_out:
        clear_output(wait=True)
        selected = wf_sel.value
        if not selected:
            print("Select workflows first.")
            return
        for name in selected:
            print(f"Deleted {_delete_workflow(name)}")
        _load_wf_sel()


wf_sel_load.on_click(_load_wf_sel)
wf_sel_stop.on_click(_stop_selected)
wf_sel_del.on_click(_del_selected)

display(w.HBox([wf_sel_load, wf_sel_stop, wf_sel_del]))
display(wf_sel)
display(wf_sel_out)
_load_wf_sel()

#%%
# ── 3. MinIO Dataset Browser ───────────────────────────────────────────

run_out = w.Output(layout=w.Layout(max_height="400px", overflow="auto"))
run_act = w.Output()
run_sel = w.SelectMultiple(layout=w.Layout(width="100%", height="250px"))
run_refresh = w.Button(description="🔄 Refresh", button_style="info",
                        layout=w.Layout(width="120px"))
run_del_sel = w.Button(description="🗑 Delete Selected", button_style="danger",
                        layout=w.Layout(width="160px"))
run_del_all = w.Button(description="⚠️ Delete ALL", button_style="danger",
                        layout=w.Layout(width="140px"))

STATUS_COLORS = {
    "running": "#2196F3",
    "completed": "#4CAF50",
    "succeeded": "#4CAF50",
    "failed": "#F44336",
}


def _load_runs(_=None):
    with run_out:
        clear_output(wait=True)
        try:
            runs = store.list_runs()
            if not runs:
                display(HTML("<i>No runs in MinIO.</i>"))
                run_sel.options = []
                return

            html = (
                f'<div style="margin-bottom:8px;font-weight:bold">'
                f"{len(runs)} runs</div>"
            )
            html += (
                '<table style="width:100%;border-collapse:collapse;font-size:13px">'
                '<tr style="border-bottom:2px solid #444">'
                "<th style=\"text-align:left;padding:4px\">Run ID</th>"
                "<th>Status</th><th>Created</th><th>Slurm Job</th></tr>"
            )
            options = []
            for r in runs:
                rid = r.get("run_id", "?")
                status = r.get("status", "unknown")
                created = r.get("created_at", "?")
                slurm_id = r.get("triton_job_id", "-")
                c = STATUS_COLORS.get(status, "#757575")
                badge = (
                    f'<span style="background:{c};color:#fff;padding:2px 8px;'
                    f'border-radius:4px;font-size:11px">{status}</span>'
                )
                ts = created[11:19] if len(created) > 10 else created
                html += (
                    f'<tr style="border-bottom:1px solid #333">'
                    f'<td style="padding:4px;font-family:monospace;font-size:11px">{rid}</td>'
                    f"<td style=\"padding:4px;text-align:center\">{badge}</td>"
                    f'<td style="padding:4px;font-size:11px">{ts}</td>'
                    f'<td style="padding:4px;font-size:11px">{slurm_id}</td>'
                    f"</tr>"
                )
                options.append((f"[{status}] {rid} ({created[:10]})", rid))
            html += "</table>"
            display(HTML(html))
            run_sel.options = options
        except Exception as e:
            display(HTML(f'<span style="color:red">Error: {e}</span>'))


def _del_sel_runs(_=None):
    with run_act:
        clear_output(wait=True)
        selected = run_sel.value
        if not selected:
            print("Select runs first.")
            return
        for rid in selected:
            store.delete_run(rid)
            print(f"Deleted run: {rid}")
        _load_runs()


def _del_all_runs(_=None):
    with run_act:
        clear_output(wait=True)
        n = store.clear_all_runs()
        print(f"Deleted {n} objects from MinIO.")
        _load_runs()


run_refresh.on_click(_load_runs)
run_del_sel.on_click(_del_sel_runs)
run_del_all.on_click(_del_all_runs)

display(w.HBox([run_refresh, run_del_sel, run_del_all]))
display(run_sel)
display(run_out)
display(run_act)
_load_runs()

#%%
# ── 4. MinIO Storage Stats ─────────────────────────────────────────────

stats_out = w.Output()
stats_btn = w.Button(
    description="📊 Compute Storage", button_style="info",
    layout=w.Layout(width="160px"),
)


def _show_stats(_=None):
    with stats_out:
        clear_output(wait=True)
        try:
            paginator = store.s3.get_paginator("list_objects_v2")
            total_size = 0
            total_objs = 0
            by_run: dict[str, tuple[int, int]] = {}
            for page in paginator.paginate(
                Bucket=store.bucket, Prefix="tdgl-runs/"
            ):
                for obj in page.get("Contents", []):
                    sz = obj.get("Size", 0)
                    total_size += sz
                    total_objs += 1
                    parts = obj["Key"].split("/")
                    if len(parts) >= 2:
                        rid = parts[1]
                        cnt, s = by_run.get(rid, (0, 0))
                        by_run[rid] = (cnt + 1, s + sz)

            html = (
                f'<div style="font-weight:bold;margin-bottom:8px">'
                f"Total: {_fmt_size(total_size)} across {total_objs} objects "
                f"({len(by_run)} runs)</div>"
            )
            top = sorted(by_run.items(), key=lambda x: x[1][1], reverse=True)[:10]
            if top:
                html += (
                    '<table style="width:100%;border-collapse:collapse;font-size:13px">'
                    '<tr style="border-bottom:2px solid #444">'
                    "<th style=\"text-align:left;padding:4px\">Run ID</th>"
                    "<th>Objects</th><th>Size</th></tr>"
                )
                for rid, (cnt, sz) in top:
                    html += (
                        f'<tr style="border-bottom:1px solid #333">'
                        f'<td style="padding:4px;font-family:monospace;font-size:11px">'
                        f"{rid}</td>"
                        f'<td style="padding:4px;text-align:right">{cnt}</td>'
                        f'<td style="padding:4px;text-align:right">{_fmt_size(sz)}</td>'
                        f"</tr>"
                    )
                html += "</table>"
            display(HTML(html))
        except Exception as e:
            display(HTML(f'<span style="color:red">Error: {e}</span>'))


stats_btn.on_click(_show_stats)

display(stats_btn)
display(stats_out)
_show_stats()

#%%
# ── 5. One-Click Full Cleanup ──────────────────────────────────────────
# Stops all running workflows (cancels Slurm jobs too), deletes all
# finished workflows, then clears all MinIO data.

cleanup_out = w.Output()
cleanup_btn = w.Button(
    description="🧹 Full Cleanup (stop + delete + clear MinIO)",
    button_style="danger",
    layout=w.Layout(width="400px"),
)


def _full_cleanup(_=None):
    with cleanup_out:
        clear_output(wait=True)

        # 1. Stop running (includes Slurm scancel for triton workflows)
        try:
            rows = _list_workflows()
            running = [r for r in rows if r["phase"] == "Running"]
            for r in running:
                logs = _stop_workflow(r["name"])
                for line in logs:
                    print(line)
        except Exception as e:
            print(f"Stop error: {e}")

        # 2. Delete all workflows
        try:
            rows = _list_workflows()
            for r in rows:
                print(f"Deleted {_delete_workflow(r['name'])}")
        except Exception as e:
            print(f"Delete error: {e}")

        # 3. Clear MinIO
        try:
            n = store.clear_all_runs()
            print(f"Deleted {n} MinIO objects")
        except Exception as e:
            print(f"MinIO error: {e}")

        print("\nDone.")
        _load_runs()


cleanup_btn.on_click(_full_cleanup)
display(cleanup_btn)
display(cleanup_out)
