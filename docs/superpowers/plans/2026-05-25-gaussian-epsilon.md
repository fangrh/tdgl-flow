# Gaussian Epsilon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gaussian spot array `disorder_epsilon` to TDGL simulations, flowing through pipeline → workflow → runner.

**Architecture:** New `epsilon.py` module provides `make_gaussian_epsilon()` factory returning a callable compatible with `tdgl.solve(disorder_epsilon=...)`. Parameters pass as JSON through the same pipeline→Arggo→runner path as existing params.

**Tech Stack:** numpy, tdgl, Argo Workflows, podman

---

### Task 1: Create epsilon module

**Files:**
- Create: `src/tdgl_workflow/epsilon.py`

- [ ] **Step 1: Write `src/tdgl_workflow/epsilon.py`**

```python
"""Gaussian spot array epsilon for TDGL simulations."""

import numpy as np


def make_gaussian_epsilon(
    positions: list[list[float]],
    widths: list[list[float]],
    strengths: list[float],
):
    """Return an epsilon(r) callable for tdgl.solve(disorder_epsilon=...).

    Args:
        positions: Nx2 array of spot centers [x, y] in device coordinates.
        widths: Nx2 array of [sigma_x, sigma_y] for each elliptical spot.
        strengths: N array of peak T suppression for each spot.

    Returns:
        Callable epsilon(r) where r is (x, y) tuple.
        epsilon = clamp(1 - sum(T_i), 0, 1)
    """
    pos = np.asarray(positions, dtype=np.float64)
    w = np.asarray(widths, dtype=np.float64)
    s = np.asarray(strengths, dtype=np.float64)

    def epsilon(r):
        x, y = r
        dx = x - pos[:, 0]
        dy = y - pos[:, 1]
        sx2 = w[:, 0] ** 2
        sy2 = w[:, 1] ** 2
        T = float(np.sum(s * np.exp(-dx**2 / (2 * sx2) - dy**2 / (2 * sy2))))
        return max(0.0, 1.0 - T)

    return epsilon
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/tdgl_workflow/epsilon.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_workflow/epsilon.py
git commit -m "feat: add make_gaussian_epsilon for spatially-varying disorder_epsilon"
```

---

### Task 2: Write unit tests for epsilon

**Files:**
- Create: `tests/test_epsilon.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for Gaussian epsilon factory."""

import numpy as np
import pytest

from tdgl_workflow.epsilon import make_gaussian_epsilon


def test_single_spot_center():
    """At the center of a single spot, epsilon = 1 - strength."""
    epsilon = make_gaussian_epsilon(
        positions=[[2.0, 1.0]],
        widths=[[0.5, 0.5]],
        strengths=[0.8],
    )
    result = epsilon((2.0, 1.0))
    assert np.isclose(result, 0.2)


def test_single_spot_far_away():
    """Far from any spot, epsilon ~ 1.0."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0]],
        widths=[[1.0, 1.0]],
        strengths=[0.5],
    )
    result = epsilon((100.0, 100.0))
    assert np.isclose(result, 1.0)


def test_two_spots_add_linearly():
    """Two overlapping spots: T values add linearly."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0], [0.0, 0.0]],
        widths=[[1.0, 1.0], [1.0, 1.0]],
        strengths=[0.3, 0.3],
    )
    result = epsilon((0.0, 0.0))
    assert np.isclose(result, 0.4)  # 1 - (0.3 + 0.3)


def test_clamp_to_zero():
    """Overlapping spots with total T > 1 clamp epsilon to 0."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0], [0.0, 0.0]],
        widths=[[0.5, 0.5], [0.5, 0.5]],
        strengths=[0.8, 0.8],
    )
    result = epsilon((0.0, 0.0))
    assert result == 0.0


def test_elliptical_spot():
    """Elliptical spot: different widths in x and y."""
    epsilon = make_gaussian_epsilon(
        positions=[[0.0, 0.0]],
        widths=[[1.0, 10.0]],
        strengths=[1.0],
    )
    # At (1,0): exp(-1/2) ~ 0.607 -> epsilon ~ 0.393
    val_x = epsilon((1.0, 0.0))
    assert np.isclose(val_x, 1.0 - np.exp(-0.5), atol=1e-10)
    # At (0,1): exp(-1/200) ~ 0.995 -> epsilon ~ 0.005
    val_y = epsilon((0.0, 1.0))
    assert np.isclose(val_y, 1.0 - np.exp(-1.0 / 200), atol=1e-10)


def test_no_spots_epsilon_is_one():
    """With zero spots, epsilon is 1.0 everywhere."""
    epsilon = make_gaussian_epsilon(
        positions=[],
        widths=[],
        strengths=[],
    )
    assert epsilon((5.0, -3.0)) == 1.0
```

- [ ] **Step 2: Run tests**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_epsilon.py -v`

Expected: All 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_epsilon.py
git commit -m "test: add unit tests for Gaussian epsilon"
```

---

### Task 3: Add epsilon_params to pipeline.submit()

**Files:**
- Modify: `src/tdgl_sdk/pipeline.py:36-66` (the `submit` method)

- [ ] **Step 1: Update `submit()` signature and arguments**

In `src/tdgl_sdk/pipeline.py`, change the `submit` method to accept `epsilon_params` and pass it as a workflow argument.

Change the method signature from:

```python
    def submit(
        self,
        device_params: dict,
        timing_params: dict,
        solver_options: dict | None = None,
    ) -> tuple[str, str]:
```

to:

```python
    def submit(
        self,
        device_params: dict,
        timing_params: dict,
        solver_options: dict | None = None,
        epsilon_params: dict | None = None,
    ) -> tuple[str, str]:
```

And add the epsilon argument to the `arguments` list, after the existing `solver-options-json` parameter:

```python
                Parameter(name="solver-options-json", value=json.dumps(solver_options or {})),
                Parameter(name="epsilon-params-json", value=json.dumps(epsilon_params or {})),
```

- [ ] **Step 2: Update `run()` method to forward epsilon_params**

In the same file, the `run()` method calls `self.submit()`. Add `epsilon_params` parameter:

Change:
```python
    def run(
        self,
        device_params: dict,
        timing_params: dict,
        solver_options: dict | None = None,
        poll_timeout: int = 600,
    ) -> dict:
```

to:

```python
    def run(
        self,
        device_params: dict,
        timing_params: dict,
        solver_options: dict | None = None,
        epsilon_params: dict | None = None,
        poll_timeout: int = 600,
    ) -> dict:
```

And change the `self.submit()` call inside `run()` from:

```python
            run_id, wf_name = self.submit(
                device_params=device_params,
                timing_params=timing_params,
                solver_options=solver_options,
            )
```

to:

```python
            run_id, wf_name = self.submit(
                device_params=device_params,
                timing_params=timing_params,
                solver_options=solver_options,
                epsilon_params=epsilon_params,
            )
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/tdgl_sdk/pipeline.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/tdgl_sdk/pipeline.py
git commit -m "feat: add epsilon_params to SimulationPipeline.submit()"
```

---

### Task 4: Update runner to parse EPSILON_PARAMS and pass to solver

**Files:**
- Modify: `services/py-tdgl-runner/runner.py:83-160` (the `main()` function)

- [ ] **Step 1: Add epsilon import and parsing**

At the top of `runner.py`, add the import after existing imports:

```python
from tdgl_workflow.epsilon import make_gaussian_epsilon
```

In `main()`, after line 87 (`solver_options = json.loads(solver_options_raw)`), add:

```python
    epsilon_params_raw = os.environ.get("EPSILON_PARAMS", "{}")
    epsilon_params = json.loads(epsilon_params_raw)
```

- [ ] **Step 2: Build epsilon_fn from params**

After the `epsilon_params` parsing, add the epsilon function construction:

```python
    epsilon_fn = None
    if epsilon_params.get("type") == "gaussian":
        epsilon_fn = make_gaussian_epsilon(
            positions=epsilon_params["positions"],
            widths=epsilon_params["widths"],
            strengths=epsilon_params["strengths"],
        )
        print(f"Epsilon: Gaussian array, {len(epsilon_params['positions'])} spots")
```

- [ ] **Step 3: Pass epsilon_fn to tdgl.solve()**

Change the `tdgl.solve()` call (around line 156) from:

```python
        solution = tdgl.solve(
            device,
            options,
            terminal_currents=get_terminal_currents,
        )
```

to:

```python
        solve_kwargs = dict(
            device=device,
            options=options,
            terminal_currents=get_terminal_currents,
        )
        if epsilon_fn is not None:
            solve_kwargs["disorder_epsilon"] = epsilon_fn
        solution = tdgl.solve(**solve_kwargs)
```

- [ ] **Step 4: Verify syntax**

Run: `python -c "import ast; ast.parse(open('services/py-tdgl-runner/runner.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add services/py-tdgl-runner/runner.py
git commit -m "feat: runner parses EPSILON_PARAMS and passes disorder_epsilon to solver"
```

---

### Task 5: Update in-cluster WorkflowTemplate

**No files in git** — the `py-tdgl-sim` WorkflowTemplate lives only in the k8s cluster. It needs a new `epsilon-params-json` parameter and an `EPSILON_PARAMS` env var on the simulate-step.

- [ ] **Step 1: Patch the WorkflowTemplate**

```bash
kubectl patch workflowtemplate py-tdgl-sim -n tdgl --type='json' -p='[
  {"op":"add","path":"/spec/arguments/parameters/-","value":{"name":"epsilon-params-json","value":"{}"}},
  {"op":"add","path":"/spec/templates/3/container/env/-","value":{"name":"EPSILON_PARAMS","value":"{{workflow.parameters.epsilon-params-json}}"}}
]'
```

- [ ] **Step 2: Verify the patch**

```bash
kubectl get workflowtemplate py-tdgl-sim -n tdgl -o jsonpath='{.spec.arguments.parameters[*].name}'
```

Expected output should include `epsilon-params-json`.

```bash
kubectl get workflowtemplate py-tdgl-sim -n tdgl -o jsonpath='{.spec.templates[3].container.env[*].name}'
```

Expected output should include `EPSILON_PARAMS`.

---

### Task 6: Build and deploy updated runner image

- [ ] **Step 1: Build image with podman**

```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl
podman build -f services/py-tdgl-runner/Dockerfile -t localhost:5000/py-tdgl-runner:epsilon-dev .
```

- [ ] **Step 2: Push to cluster registry**

```bash
podman push localhost:5000/py-tdgl-runner:epsilon-dev
```

Note: If no local registry exists, push to `ghcr.io/fangrh/py-tdgl-runner:epsilon-dev` instead, or set up a k8s registry. Adjust the tag accordingly.

- [ ] **Step 3: Update WorkflowTemplate image**

```bash
kubectl patch workflowtemplate py-tdgl-sim -n tdgl --type='json' -p='[
  {"op":"replace","path":"/spec/arguments/parameters/1/value","value":"<registry>/py-tdgl-runner:epsilon-dev"}
]'
```

---

### Task 7: End-to-end test with epsilon

**Files:**
- Modify: `notebooks/e2e_sim_test_2x2.py`

- [ ] **Step 1: Add epsilon_params to the notebook config section**

In the config cell (after TIMING_PARAMS), add:

```python
EPSILON_PARAMS = {
    "type": "gaussian",
    "positions": [[-1.0, 0.0], [1.0, 0.0]],
    "widths": [[0.5, 0.5], [0.5, 0.5]],
    "strengths": [0.3, 0.3],
}

print(f"  Epsilon: {len(EPSILON_PARAMS['positions'])} Gaussian spots")
```

- [ ] **Step 2: Pass epsilon_params to pipeline.submit()**

In the submit cell, change:

```python
run_id, wf_name = pipeline.submit(
    device_params=DEVICE_PARAMS,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
)
```

to:

```python
run_id, wf_name = pipeline.submit(
    device_params=DEVICE_PARAMS,
    timing_params=TIMING_PARAMS,
    solver_options=SOLVER_OPTIONS,
    epsilon_params=EPSILON_PARAMS,
)
```

- [ ] **Step 3: Commit**

```bash
git add notebooks/e2e_sim_test_2x2.py
git commit -m "feat: add Gaussian epsilon params to e2e test notebook"
```

---

### Task 8: Create feature branch and push all commits

- [ ] **Step 1: Create branch from current main**

```bash
git checkout -b feat/gaussian-epsilon
```

- [ ] **Step 2: Push branch**

```bash
git push -u origin feat/gaussian-epsilon
```
