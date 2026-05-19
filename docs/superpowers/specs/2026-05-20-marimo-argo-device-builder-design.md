# Marimo Argo Device Builder Design

## Goal

Make the local marimo device builder a reliable Argo validation tool.

The notebook runs on the local machine, while the device builder runs inside Kubernetes through the `rectangle-device-builder` Argo `WorkflowTemplate`. The user needs the notebook to submit device parameters to Argo, follow that exact workflow automatically, and render only the mesh returned by that workflow.

The goal is not to bypass Argo for speed. Argo must remain the source of truth for the plotted result.

## Current Context

- `notebooks/device_builder.py` is a marimo notebook.
- The notebook talks to Argo Server at `http://localhost:2746`.
- It submits the `rectangle-device-builder` workflow template with:
  - `run-id`
  - `image`
  - `device-params-json`
- `workflows/rectangle-device-builder.yaml` starts a Kubernetes Pod that runs `/app/build_device.py`.
- `services/py-tdgl-runner/build_device.py` reads `DEVICE_PARAMS`, builds the mesh, writes `mesh_meta.json`, and prints the mesh between `MESH_JSON_START` and `MESH_JSON_END`.
- The notebook parses workflow logs to recover the mesh JSON.

Recent debugging showed two separate concerns:

- Argo submission itself is fast enough; waiting mostly comes from Kubernetes workflow and Pod lifecycle.
- The notebook interaction must make it obvious which parameter snapshot was submitted and which Argo workflow produced the plotted mesh.

## Requirements

1. The user edits device parameters in marimo and clicks **Build through Argo**.
2. The click captures one immutable parameter snapshot.
3. The notebook submits that exact snapshot to Argo.
4. The notebook tracks only the submitted workflow, not the latest successful rectangle workflow.
5. The notebook polls Argo automatically while the submitted workflow is non-terminal.
6. Polling stops after `Succeeded`, `Failed`, or `Error`.
7. The plot is rendered only from mesh JSON returned by the submitted Argo workflow logs.
8. If the workflow is still running, the plot area shows a waiting state.
9. If the workflow fails, the notebook shows the workflow and node failure messages.
10. If returned mesh metadata does not match submitted parameters, the notebook shows a mismatch warning.

## Non-Goals

- Do not add a local preview plot as the displayed result.
- Do not replace Argo with a local build path.
- Do not add a new local bridge service in this iteration.
- Do not change the simulation workflows.
- Do not redesign the Kubernetes deployment model.

## Recommended Approach

Implement notebook-side automatic polling.

The local marimo notebook remains the controller:

1. Submit workflow through Hera/Argo.
2. Store submitted workflow identity and parameter snapshot.
3. Poll Argo status every two seconds while the workflow is pending or running.
4. Fetch and parse logs after success.
5. Render the returned Argo mesh.

This keeps the architecture simple and preserves Argo as the validation path.

## Notebook State Model

The notebook should separate four kinds of state:

- `submitted_params`: the immutable parameters captured from the last Build click.
- `submitted_workflow`: the Argo workflow name created for those parameters.
- `workflow_phase`: the current Argo phase for `submitted_workflow`.
- `argo_mesh_result`: the parsed mesh JSON from the submitted workflow logs, populated only after success.

The plot cell should depend on `argo_mesh_result`, not on current form values and not on local mesh generation.

## UI Flow

### Initial State

The notebook shows the parameter form and a message:

```text
No Argo workflow submitted yet.
```

The plot area shows:

```text
Waiting for Argo mesh result before plotting.
```

### Build Click

When the user clicks **Build through Argo**:

1. Capture the form values into `submitted_params`.
2. Submit `submitted_params` as `device-params-json`.
3. Store the returned Argo workflow name in `submitted_workflow`.
4. Clear any prior `argo_mesh_result`.
5. Show submitted workflow name and submitted JSON.

### Running State

While the workflow phase is `Pending`, `Running`, or `Unknown`:

- Show the current phase.
- Show the submitted params.
- Poll every two seconds.
- Keep plot empty or in a waiting state.

Polling should not run before a workflow is submitted and should stop automatically once the workflow reaches a terminal phase.

### Success State

When the workflow reaches `Succeeded`:

1. Fetch logs for `submitted_workflow`.
2. Parse content between `MESH_JSON_START` and `MESH_JSON_END`.
3. Store parsed JSON in `argo_mesh_result`.
4. Compare returned mesh metadata against `submitted_params`.
5. Render the plot from `argo_mesh_result`.
6. Stop polling.

### Failed State

When the workflow reaches `Failed` or `Error`:

- Show the workflow phase.
- Show submitted params.
- Extract node-level error messages where available.
- Keep `argo_mesh_result` empty for this failed submission.
- Stop polling.

## Components

### Parameter Form Cell

Use normal `mo.ui.number` controls in a readable table-like layout. Avoid `mo.ui.dictionary` for this notebook because its JSON-style UI made it hard to tell which values were edited versus submitted.

The form should submit all values as one snapshot.

### Submission Cell

Responsibilities:

- Convert form values to the `device-params-json` schema.
- Submit `rectangle-device-builder` through Hera.
- Store submitted workflow and params in marimo state.
- Avoid silently reusing a previous workflow for new params.

### Status Polling Cell

Responsibilities:

- Query only `submitted_workflow`.
- Derive phase from Argo `status.phase`, with label fallback.
- Poll every two seconds while non-terminal.
- Stop polling after terminal state.
- Surface failure details.

### Result Loader

Responsibilities:

- Fetch workflow logs after success.
- Parse `MESH_JSON_START` and `MESH_JSON_END`.
- Validate returned parameter metadata against submitted params.
- Produce `argo_mesh_result`.

### Plot Cell

Responsibilities:

- Render a waiting message when `argo_mesh_result` is absent.
- Render the Plotly figure only from `argo_mesh_result`.
- Include enough title or summary text to identify the Argo result dimensions.

## Data Flow

```text
marimo form submit
  -> submitted_params
  -> Hera Workflow(create)
  -> Argo WorkflowTemplate rectangle-device-builder
  -> Kubernetes Pod runs /app/build_device.py
  -> Pod stdout prints MESH_JSON_START/END
  -> marimo polls submitted workflow
  -> marimo fetches logs after success
  -> argo_mesh_result
  -> Plotly plot
```

## Error Handling

The notebook should show clear messages for:

- Argo Server unreachable.
- Workflow create failed.
- Workflow missing or deleted.
- Workflow failed or errored.
- Workflow succeeded but mesh markers are missing.
- Mesh JSON parse error.
- Returned mesh metadata mismatch.

Errors should include the submitted workflow name and submitted params where possible.

## Performance Expectations

The expected latency is dominated by Argo and Kubernetes:

- Workflow creation and Pod scheduling.
- PVC provisioning in the current template.
- Argo executor startup.
- Runner container startup.
- Workflow controller reconciliation.

The notebook should not hide this latency. It should make progress visible and update automatically when Argo finishes.

Future optimization can consider:

- Replacing per-run PVCs with `emptyDir` for this preview-only builder.
- Changing `imagePullPolicy` from `Always` to `IfNotPresent` if GitOps policy permits.
- Writing results to a service or object store instead of scraping logs.

Those optimizations are outside this first notebook-side design.

## Verification Plan

1. Submit parameter set A.
2. Confirm workflow args contain parameter set A.
3. Confirm plot stays waiting while workflow is non-terminal.
4. Confirm returned mesh metadata matches parameter set A.
5. Confirm plot renders from returned Argo mesh.
6. Submit parameter set B.
7. Confirm notebook tracks workflow B, not workflow A or latest successful workflow.
8. Confirm polling stops after success.
9. Trigger or inspect a failed rectangle workflow and confirm failure details are visible.

## Scope

This spec covers only `notebooks/device_builder.py`.

Changes to `workflows/rectangle-device-builder.yaml` may be designed separately if workflow runtime remains too slow after notebook interaction is reliable.
