"""Argo Workflow SDK Demo — pure Python script.

Uses hera-workflows SDK (v6) to directly submit and manage Argo Workflows.
Equivalent to notebooks/argo_workflow_demo.ipynb.

Prerequisites:
    pip install hera-workflows

    # Port-forward Argo Server for local access:
    kubectl port-forward -n argo svc/argo-workflows-server 2746:2746
"""

import json
import time
import uuid

from hera.workflows import Workflow, WorkflowsService, Parameter
from hera.workflows.models import (
    WorkflowRetryRequest,
    WorkflowTemplateRef as WTR,
)

NAMESPACE = "tdgl"

# ── 1. Connect to Argo Server ─────────────────────────────────────────
# Option A: In-cluster (inside a Pod)
# svc = WorkflowsService(
#     host="https://argo-workflows-server.argo.svc.cluster.local:2746",
#     verify_ssl=False,
#     namespace=NAMESPACE,
# )

# Option B: Local via port-forward
#   kubectl port-forward -n argo svc/argo-workflows-server 2746:2746
svc = WorkflowsService(
    host="http://localhost:2746",
    verify_ssl=False,
    namespace=NAMESPACE,
)

print(f"Argo host: {svc.host}")

# ── 2. List existing workflows ────────────────────────────────────────
result = svc.list_workflows(namespace=NAMESPACE)
items = result.items or []
print(f"Found {len(items)} workflows in namespace '{NAMESPACE}':")
for wf in items:
    name = wf.metadata.name
    phase = getattr(wf.status, "phase", "Unknown") if wf.status else "Unknown"
    print(f"  {name}  {phase}")

# ── 3. Submit workflow from template ──────────────────────────────────
run_id = str(uuid.uuid4())

device_params = {
    "film_width": 10.0,
    "film_height": 2.0,
    "elec_width": 0.5,
    "elec_height": 1.0,
    "probe_points": [[-2.0, 0.0], [2.0, 0.0]],
    "max_edge_length": 0.5,
}

timing_params = {
    "je_initial": 0.0,
    "je_final": 5.0,
    "je_step": 1.0,
    "ramp_time": 1.0,
    "stable_time": 5.0,
    "save_time": 3.0,
}

workflow = Workflow(
    generate_name="py-tdgl-",
    namespace=NAMESPACE,
    workflow_template_ref=WTR(name="py-tdgl-sim"),
    arguments=[
        Parameter(name="run-id", value=run_id),
        Parameter(name="data-service-url", value="http://data-viewer.tdgl.svc.cluster.local"),
        Parameter(name="device-params-json", value=json.dumps(device_params)),
        Parameter(name="timing-params-json", value=json.dumps(timing_params)),
        Parameter(name="solver-options-json", value="{}"),
        Parameter(name="cpu", value="2"),
        Parameter(name="memory", value="4Gi"),
        Parameter(name="dev-mode", value="false"),
    ],
    workflows_service=svc,
)

created = workflow.create()
wf_name = created.metadata.name
print(f"Run ID:  {run_id}")
print(f"Workflow submitted: {wf_name}")

# ── 4. Monitor workflow status ────────────────────────────────────────
phase = "Unknown"
while True:
    wf = svc.get_workflow(name=wf_name, namespace=NAMESPACE)
    phase = getattr(wf.status, "phase", "Unknown") if wf.status else "Unknown"
    print(f"  {wf_name}: {phase}")
    if phase in ("Succeeded", "Failed", "Error"):
        break
    time.sleep(5)

print(f"\nFinal status: {phase}")

# ── 5. Get workflow step info ─────────────────────────────────────────
nodes = getattr(wf.status, "nodes", None)
if nodes:
    for node in nodes:
        name = getattr(node, "display_name", getattr(node, "name", "?"))
        step_phase = getattr(node, "phase", "Unknown")
        print(f"  Step: {name}  Status: {step_phase}")

# ── 6. Retry if failed ────────────────────────────────────────────────
if phase in ("Failed", "Error"):
    retried = svc.retry_workflow(name=wf_name, req=WorkflowRetryRequest(), namespace=NAMESPACE)
    print(f"Retried workflow: {retried.metadata.name}")
else:
    print("No retry needed.")
