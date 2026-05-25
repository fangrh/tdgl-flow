# Project Trim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all artifacts from the pre-trim era (viewer-manager, cpp-tdgl, data-viewer, generator, etc.) and simplify CLAUDE.md so it stops loading 222 lines of rules every conversation.

**Architecture:** Pure file deletion + one CLAUDE.md rewrite. No code changes. The working system (py-tdgl-runner, CI/CD, notebooks, infra) stays untouched.

**Tech Stack:** git rm, rm, Write tool

---

## What Stays

These are the working system — **do not touch**:

- `services/py-tdgl-runner/` — simulation runner
- `workflows/rectangle-device-builder.yaml` — device builder workflow
- `src/tdgl_sdk/` — SDK (pipeline, viewer, client, diagnostics)
- `src/tdgl_workflow/` — mesh + timing helpers
- `notebooks/` — e2e_sim_test.py, 009-native-widget-player.ipynb, README.md
- `infra/` — Argo Workflows, MinIO, nginx, namespace
- `clusters/argocd/` — Argo CD app definitions (still in deployment flow)
- `.github/workflows/ci.yml` — CI
- `tests/` — all test files
- `Makefile`, `pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`, `.dockerignore`

---

### Task 1: Remove tracked files — alembic.ini

**Files:**
- Delete: `alembic.ini`

- [ ] **Step 1: Remove from git**

```bash
git rm alembic.ini
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove orphaned alembic.ini (viewer-manager artifact)"
```

---

### Task 2: Remove tracked files — templates/

**Files:**
- Delete: `templates/` (entire directory)

Templates are scaffolding for services/workflows that don't exist. The working py-tdgl-runner and rectangle-device-builder are the real reference now.

- [ ] **Step 1: Remove from git**

```bash
git rm -r templates/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove templates/ — existing services are the reference"
```

---

### Task 3: Remove tracked files — old design specs

**Files:**
- Delete: `docs/superpowers/specs/` (18 files)

All specs are for features removed in the trim (data-viewer, cpp-tdgl, generator, viewer-manager, live-viewer, etc.).

- [ ] **Step 1: Remove from git**

```bash
git rm -r docs/superpowers/specs/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove old design specs for trimmed-away features"
```

---

### Task 4: Remove tracked files — old plans

**Files:**
- Delete: `docs/superpowers/plans/` (19 tracked files)

Historical plans for features that no longer exist (data-viewer, cpp-tdgl, plotly-viewer, viewer-manager, etc.).

- [ ] **Step 1: Remove from git**

```bash
git rm -r docs/superpowers/plans/
```

This also removes the `docs/superpowers/` directory entirely since it will be empty.

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove old implementation plans for trimmed-away features"
```

---

### Task 5: Clean up untracked files

**Files:**
- Delete: `animation_jupyter.md`
- Delete: `ball-tracking-live-plot/` (entire directory)
- Delete: `Jupyter_MCP_Setup_Guide.md`
- Delete: `real_time_display.md`
- Delete: `skills-lock.json`
- Delete: `test_widget.ipynb`
- Delete: `.agents/`

These are untracked stray files that accumulated during development. None are referenced by the working system.

- [ ] **Step 1: Delete all untracked files**

```bash
rm -f animation_jupyter.md Jupyter_MCP_Setup_Guide.md real_time_display.md skills-lock.json test_widget.ipynb
rm -rf ball-tracking-live-plot/ .agents/
```

- [ ] **Step 2: Verify nothing working references these**

```bash
grep -r "ball-tracking\|animation_jupyter\|Jupyter_MCP\|real_time_display\|test_widget" --include="*.py" --include="*.yaml" --include="*.toml" src/ services/ notebooks/ tests/ .github/ || echo "No references found — safe to delete"
```

Expected: "No references found"

---

### Task 6: Rewrite CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

Replace the current 222-line rules document with a minimal version. The old content prescribes rules for services that don't exist (viewer-manager, data-viewer, generator, cpp-tdgl). The working code is self-documenting — the existing py-tdgl-runner and ci.yml are the reference.

- [ ] **Step 1: Write new CLAUDE.md**

```markdown
# kubeflow-tdgl

End-to-end TDGL simulation: build device → run py-tdgl → store in MinIO → view in notebook.

## Project Structure

| Path | Purpose |
|------|---------|
| `services/py-tdgl-runner/` | Argo Workflow runner image |
| `workflows/rectangle-device-builder.yaml` | Standalone device-builder workflow |
| `src/tdgl_sdk/` | Notebook SDK: pipeline, MinIO access, viewer, diagnostics |
| `src/tdgl_workflow/` | Shared: mesh builder, timing schedule |
| `notebooks/e2e_sim_test.py` | Main end-to-end test + live viewer |
| `infra/` | Namespace, Argo Workflows, MinIO, nginx |
| `clusters/argocd/` | Argo CD app definitions |
| `tests/` | pytest suite |

## CI

Push to main → CI builds `ghcr.io/fangrh/py-tdgl-runner:<sha>` → updates workflowtemplate tag → Argo CD auto-syncs.

Path trigger: `services/py-tdgl-runner/**`, `src/**`, `pyproject.toml`.

## Dev

```bash
pip install -e ".[dev]"
kubectl port-forward -n tdgl svc/nginx-ingress 30080:80
kubectl port-forward -n tdgl svc/minio 30900:9000
pytest -q tests/
```

## Adding a New Service or Workflow

Use the existing `services/py-tdgl-runner/` and `workflows/rectangle-device-builder.yaml` as reference. Copy and adapt.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "chore: trim CLAUDE.md — existing code is the reference"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run the test suite**

```bash
python -m pytest -q tests/test_py_runner_timeline.py tests/test_viewer_diagnostics.py tests/test_timing.py tests/test_mesh.py tests/test_pipeline.py
```

Expected: all pass

- [ ] **Step 2: Verify CI config is intact**

```bash
cat .github/workflows/ci.yml | grep -c "py-tdgl-runner"
```

Expected: ≥ 1

- [ ] **Step 3: Check git status is clean**

```bash
git status
```

Expected: working tree clean (or only untracked files that are gitignored)

---

## Self-Review

**1. Spec coverage:** User asked to clean everything unrelated to py-tdgl simulation, CI/CD, and visualization notebooks. Covered by Tasks 1–6. CLAUDE.md simplified in Task 6.

**2. Placeholder scan:** All steps contain exact commands and exact file content. No TBD/TODO.

**3. Type consistency:** N/A — no code changes, only file deletions and one markdown rewrite.
