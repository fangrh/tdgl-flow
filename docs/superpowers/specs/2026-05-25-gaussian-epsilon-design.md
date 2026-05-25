# Gaussian Epsilon Design

## Goal

Add spatially-varying `disorder_epsilon` to TDGL simulations via an array of
elliptical Gaussian spots. Epsilon represents `1 - T`, where T is the local
critical temperature suppression caused by the Gaussian illumination pattern.

## Inputs

| Parameter   | Shape | Description                              |
|-------------|-------|------------------------------------------|
| positions   | N x 2 | Center of each spot in device coordinates|
| widths      | N x 2 | Sigma (sx, sy) for each elliptical spot  |
| strengths   | N     | Peak T suppression for each spot         |

All arrays are passed as JSON via `epsilon_params`.

## Formula

For each mesh site at `(x, y)`:

```
T = sum_i( strengths[i] * exp(-dx²/(2*sx²) - dy²/(2*sy²)) )
epsilon = clamp(1 - T, 0, 1)
```

where `dx = x - positions[i][0]`, `dy = y - positions[i][1]`.

## Architecture

### New file: `src/tdgl_workflow/epsilon.py`

`make_gaussian_epsilon(positions, widths, strengths)` returns a callable
`epsilon(r)` compatible with `tdgl.solve(disorder_epsilon=...)`.

### Parameter flow

```
notebook
  → SimulationPipeline.submit(epsilon_params={...})
  → Argo Workflow arg: epsilon-params-json
  → Runner env var: EPSILON_PARAMS
  → runner.py: make_gaussian_epsilon(...) → epsilon_fn
  → tdgl.solve(disorder_epsilon=epsilon_fn)
```

`epsilon_params` is optional. When omitted, `disorder_epsilon` is not set
(behavior unchanged from current code).

### Example

```python
EPSILON_PARAMS = {
    "type": "gaussian",
    "positions": [[1.0, 0.5], [3.0, -1.0]],
    "widths": [[0.3, 0.2], [0.3, 0.2]],
    "strengths": [0.8, 0.6],
}
```

`"type": "gaussian"` is reserved for future extension. Only this type is
implemented initially.

## Files to modify

| File | Change |
|------|--------|
| `src/tdgl_workflow/epsilon.py` | **New.** `make_gaussian_epsilon()` factory |
| `services/py-tdgl-runner/runner.py` | Parse `EPSILON_PARAMS`, create epsilon_fn, pass to `tdgl.solve()` |
| `workflows/rectangle-device-builder.yaml` | Add `epsilon-params-json` workflow argument |
| `src/tdgl_sdk/pipeline.py` | Add `epsilon_params` to `submit()` |
| `notebooks/e2e_sim_test_2x2.py` | Add epsilon test cell |

## Testing

1. Local unit test: import `make_gaussian_epsilon`, verify output at known coordinates
2. End-to-end: submit with `epsilon_params` via pipeline, verify runner applies it

## Deployment (no CI/CD)

1. Branch `feat/gaussian-epsilon`
2. `podman build` → push to cluster registry
3. Update Argo WorkflowTemplate image
