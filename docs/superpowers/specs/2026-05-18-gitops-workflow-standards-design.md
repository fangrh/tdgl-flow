# GitOps Workflow Standards for AI Agent Development

**Date:** 2026-05-18
**Status:** Draft
**Target user:** Claude Code (primary)

## Problem

When adding new microservices or modifying the TDGL simulation platform, the agent (Claude Code) lacks a standardized workflow. This leads to:

- Scanning the entire project to understand what files to create/modify
- Missing CI triggers or manifest updates
- No fast feedback loop during development
- Inconsistent Argo Workflow templates

## Solution

Encode the full GitOps workflow as CLAUDE.md rules + template files, so the agent follows the same disciplined process every time.

---

## 1. Repository Architecture

### Current (monorepo)

```
kubeflow-tdgl/           # Single repo: source + manifests
├── .github/workflows/   # CI
├── clusters/argocd/     # Argo CD apps
├── infra/               # Infra manifests
├── services/            # Service source + k8s manifests
├── workflows/           # Argo Workflow templates
├── src/                 # Shared Python library
└── tests/
```

### Target (config repo separation)

```
kubeflow-tdgl/                  # Source code repo
├── CLAUDE.md                   # GitOps workflow rules
├── templates/                  # Service & workflow templates
│   ├── service/
│   └── workflow/
├── services/                   # Microservice source code
├── src/                        # Shared library
├── tests/
└── .github/workflows/ci.yml   # CI builds images only

kubeflow-tdgl-manifests/        # Config repo (new)
├── clusters/argocd/            # Argo CD Application CRDs
├── infra/                      # Infrastructure manifests
├── services/                   # Per-service kustomize bases
└── workflows/                  # WorkflowTemplate manifests
```

**Migration path:** Start with CLAUDE.md rules in current monorepo. Split config repo after the team is comfortable with the standardized process.

### CI Flow After Separation

```
Code push → GitHub Actions builds image → pushes tag to manifests repo → Argo CD syncs
```

This avoids the CI infinite-loop problem Argo CD best practices warn about.

---

## 2. Dual-Mode Development

### Dev Mode (fast iteration)

The agent should present dev tool options and recommend based on the project's needs. This is a decision the agent makes *with the user*, not a fixed choice.

**Dev tool options (agent should present these):**

| Tool | Strengths | Best for |
|------|-----------|----------|
| **Skaffold** | Native K8s, sync mode (file sync without rebuild), Google-backed | Simple services, already using kustomize |
| **Tilt** | Excellent UI, live-update, multi-service orchestration | Multi-service dev with dependencies |
| **Garden** | Full stack orchestration, caching, team sharing | Complex microservice graphs |
| **Raw docker/kubectl** | No extra tooling, maximum control | One-off debugging, CI-like testing |

**Agent recommendation rules:**
1. For this project (few services, kustomize, Python+Docker): recommend **Skaffold** as default
2. If user needs multi-service hot-reload with UI: recommend **Tilt**
3. If user wants zero extra tooling: fallback to raw `docker build → push → kubectl apply`
4. Always present at least 2 options and explain the trade-off

**Common dev commands (regardless of tool):**

| Action | Command |
|--------|---------|
| Access service | `kubectl port-forward svc/<name> <port>:<port>` |
| View logs | `kubectl logs -f deployment/<name> -n tdgl` |
| Argo CD during dev | Set to manual sync or pause to avoid overwriting dev changes |

Agent should default to dev mode when iterating on a service.

### Prod Mode (formal release)

```
PR → CI builds all changed images → merge to main → tag manifests repo → Argo CD auto-sync
```

Agent should use prod mode only when changes are ready for main branch.

---

## 3. New Service Addition Standard

### Step 1: Copy template

```bash
cp -r templates/service/ services/<service-name>/
```

### Step 2: Required files

```
services/<service-name>/
├── Dockerfile
├── k8s/
│   ├── deployment.yaml
│   ├── service.yaml          # only if externally accessible
│   └── kustomization.yaml
└── src/                      # service source code
```

### Step 3: Mandatory config updates

| File | Change |
|------|--------|
| `.github/workflows/ci.yml` | Add build step for new service image |
| `clusters/argocd/apps/services.yaml` | Add resource path if using separate kustomize |
| `services/kustomization.yaml` | Add new service resources |
| `workflows/<service-name>.yaml` | Add WorkflowTemplate if service runs async jobs |

### Step 4: Naming conventions

- Image: `ghcr.io/<org>/tdgl-<service-name>`
- K8s namespace: `tdgl`
- Argo App: `tdgl-services` (unified app, wave 1)
- Workflow template: `<service-name>-job`

### Step 5: Validation checklist

- [ ] `docker build` succeeds locally
- [ ] `kubectl apply --dry-run=client -k services/<name>/k8s/` passes
- [ ] CI workflow includes new service path trigger
- [ ] Argo CD can sync the new manifests
- [ ] WorkflowTemplate submits successfully with test parameters

---

## 4. CI Trigger Rules

### Path-based triggers

Each service only builds when its own path or the shared library changes:

```yaml
# In ci.yml
paths:
  - 'services/<service-name>/**'
  - 'src/**'   # shared lib change triggers ALL services
```

### Build priority

| Change | Action |
|--------|--------|
| `src/**` (shared lib) | Rebuild all service images |
| `services/<name>/**` | Rebuild only that service |
| `infra/**`, `clusters/**` | No build (Argo CD handles) |
| `workflows/**` | No build |

### Image tag strategy

- Dev: `<branch>-<short-sha>` (e.g., `main-abc1234`)
- Release: `v<semver>` (manual tag trigger)

### Post-build actions

1. Push image to GHCR
2. Update corresponding K8s manifest image tag
3. If config repo exists: commit and push tag change there

---

## 5. Manifest Modification Rules

### Argo CD sync ordering

- Wave 0: Infrastructure (`infra/`)
- Wave 1: Services (`services/`)
- Never modify wave ordering without explicit approval

### Modification constraints

1. **Resource limits** — Use Kustomize patches or overlays, don't hardcode in base manifests
2. **Argo CD managed fields** — Don't modify fields managed by Argo CD (e.g., `replicas` if HPA is active)
3. **ConfigMap/Secret changes** — Add annotation for reload:
   ```yaml
   annotations:
     argocd.argoproj.io/sync-options: Replace=true
   ```
4. **Image tags** — Use Kustomize `images` transformer, never hardcode:
   ```yaml
   images:
     - name: ghcr.io/org/tdgl-service
       newTag: $(IMAGE_TAG)
   ```

---

## 6. Argo Workflow Template Standards

### Required structure

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: <template-name>
  namespace: tdgl
spec:
  entrypoint: main
  activeDeadlineSeconds: 3600    # REQUIRED: timeout
  arguments:
    parameters:
      - name: run-id             # REQUIRED: unique run identifier
      - name: image-tag
        value: "latest"
  templates:
    - name: main
      inputs:
        parameters: [...]
      container:
        image: ghcr.io/<org>/tdgl-<runner>:{{workflow.parameters.image-tag}}
        resources:
          requests: { cpu: "1", memory: "2Gi" }
          limits: { cpu: "2", memory: "4Gi" }
      retryStrategy:
        limit: "3"
        retryPolicy: "OnTransientError"
      # Output must go to mounted PVC (Zarr store)
```

### Mandatory rules

1. All workflow executions must use WorkflowTemplate (no ad-hoc Workflow CRDs)
2. Must declare `resources.requests` and `resources.limits`
3. Must set `activeDeadlineSeconds` for timeout
4. Must accept `run-id` as parameter (no hardcoded identifiers)
5. Output must write to the mounted PVC (`/data/zarr/`)
6. Must handle failure gracefully (set `retryStrategy` for transient errors)

---

## 7. Templates & Patterns System

Templates are **complete, working examples** — not skeletons with placeholders. The agent reads them as reference, copies them, and adapts the concrete values. No `__PLACEHOLDER__` syntax.

### 7.1 Directory structure

```
templates/
├── services/
│   ├── _base/                    # Standard microservice template
│   │   ├── Dockerfile
│   │   ├── k8s/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── kustomization.yaml
│   │   └── runner.py             # Entry point pattern
│   └── _patterns/                # Proven service patterns
│       ├── web-api.md            # Pattern: HTTP API service
│       ├── background-worker.md  # Pattern: async job processor
│       └── data-pipeline.md      # Pattern: data transform service
├── workflows/
│   ├── _base/                    # Standard WorkflowTemplate
│   │   └── workflow-template.yaml
│   └── _patterns/                # Proven workflow patterns
│       ├── single-task.md        # Pattern: one container, simple params
│       ├── dag-pipeline.md       # Pattern: multi-step DAG
│       └── parameter-sweep.md    # Pattern: parallel parameter sweep
└── README.md                     # Index: which template/pattern for which use case
```

### 7.2 Template design principles

1. **Complete and runnable** — each template file is a real, working YAML/Dockerfile that the agent can `cp -r` then adapt
2. **Values are obvious** — use clear names like `my-service`, `8080` so the agent knows what to change
3. **One page per pattern** — each `.md` pattern file is under 60 lines, containing: description, when to use, key code snippets, and gotchas
4. **Agent-optimized format** — pattern files use consistent sections:

```markdown
# Pattern: <name>

## When to use
<one sentence>

## Key structure
<code snippet showing the essential parts>

## What to change
- `field` → description

## Gotchas
- common mistakes
```

### 7.3 Pattern library (extensible)

When a service or workflow turns out well-designed, it can be distilled into a new pattern:

1. Identify the reusable pattern (not the domain-specific logic)
2. Extract the structure into a new `.md` file under `_patterns/`
3. Add to `templates/README.md` index
4. CLAUDE.md automatically picks it up via the templates reference

The agent should propose pattern extraction when it notices repeated structures across services.

---

## 8. CLAUDE.md Structure

CLAUDE.md will be **concise and action-oriented**. Structure:

```markdown
# GitOps Workflow Rules

## Quick Reference
| Task | Steps | Template |
|------|-------|----------|
| Add service | copy `templates/services/_base/` → adapt → update CI/Argo | [link] |
| Add workflow | copy `templates/workflows/_base/` → adapt | [link] |
| Debug service | dev mode: build → push → kubectl apply | Section 2 |
| Modify manifest | follow Section 5 rules | — |

## New Service Checklist
<condensed Section 3>

## CI Rules
<condensed Section 4>

## Manifest Rules
<condensed Section 5>

## Workflow Rules
<condensed Section 6>

## Dev Mode Commands
<table from Section 2>

## Templates & Patterns
- Read `templates/README.md` for available patterns
- Check `templates/services/_patterns/` for proven designs
- Check `templates/workflows/_patterns/` for workflow patterns
```

The CLAUDE.md is rules only. The design doc (this file) is the rationale.
