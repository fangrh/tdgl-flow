# GitOps Workflow Rules

This project uses Argo CD + Argo Workflows + GitHub Actions CI for deployment.
Follow these rules when modifying services, workflows, or CI.

## Quick Reference

| Task | What to do |
|------|-----------|
| Add a new service | Copy `templates/services/_base/` → adapt → update CI + Argo CD + kustomization |
| Add a new workflow | Copy `templates/workflows/_base/` → adapt → add to `workflows/` |
| Debug a service | Use dev mode (see below) — build image, push, kubectl apply |
| Modify K8s manifests | Follow manifest rules below — no hardcoded tags or limits |
| Change CI triggers | Follow CI rules below — path-based, per-service |

## Adding a New Service

### 1. Copy template
```bash
cp -r templates/services/_base/ services/<name>/
```

### 2. Required files
```
services/<name>/
├── Dockerfile
├── runner.py              # or main.py for FastAPI services
└── k8s/
    ├── deployment.yaml
    ├── service.yaml       # remove if internal-only
    └── kustomization.yaml
```

### 3. Must also update

| File | What to add |
|------|------------|
| `.github/workflows/ci.yml` | Change detection line + build step + tag update step |
| `services/kustomization.yaml` | Add `- <name>/k8s/` to resources list |
| `clusters/argocd/apps/services.yaml` | No change needed (already points at `services/` path) |

### 4. Naming

- Image: `ghcr.io/fangrh/tdgl-<name>`
- Namespace: `tdgl`
- Argo App: `tdgl-services` (wave 1, auto-sync)

### 5. Validate

- [ ] `docker build -f services/<name>/Dockerfile .` succeeds
- [ ] `kubectl apply --dry-run=client -k services/<name>/k8s/` passes
- [ ] CI path detection includes new service
- [ ] Argo CD syncs without errors

## CI Rules

### Path triggers (in ci.yml)
Each service builds only when its path changes:
```
services/<name>/**   → builds tdgl-<name>
src/**               → rebuilds ALL services (shared library)
infra/**, clusters/** → no build (Argo CD only)
workflows/**          → no build
```

### Adding a new service to CI
Three places in `.github/workflows/ci.yml`:
1. **Change detection** — add a grep line: `echo "$CHANGES" | grep -qE "^services/<name>/" && YOUR_FLAG=true`
2. **Build step** — add `if: steps.changes.outputs.your_flag == 'true'` build+push block
3. **Tag update** — add `sed -i` line in the manifest tag update step

### Image tags
- Auto-generated: `<short-git-sha>` (e.g., `b373557`)
- Format: `ghcr.io/fangrh/tdgl-<name>:<sha>`

## Manifest Rules

### Do
- Use Kustomize for all K8s resource management
- Keep resource limits in deployment.yaml (requests + limits required)
- Use `imagePullSecrets: ghcr-secret` for all deployments
- Add `livenessProbe` and `readinessProbe` for HTTP services

### Don't
- Don't hardcode image tags — CI auto-updates them via `sed`
- Don't modify Argo CD sync-wave values (wave 0 = infra, wave 1 = services)
- Don't set `replicas` > 1 unless you also set up HPA
- Don't create ad-hoc Workflow CRDs — always use WorkflowTemplate

## Workflow Rules

### Required fields in every WorkflowTemplate
- `activeDeadlineSeconds` — prevent hung workflows
- `resources.requests` AND `resources.limits` — on every container
- `run-id` parameter — no hardcoded identifiers
- `volumeClaimTemplates` — for per-run data storage at `/data`

### Workflow parameter pattern
```yaml
arguments:
  parameters:
    - name: run-id
      value: ""
    - name: image
      value: "ghcr.io/fangrh/tdgl-<runner>:latest"
```

### Submitting workflows
All submissions go through WorkflowTemplate references:
```bash
kubectl -n tdgl submit workflow --from workflowtemplate/<name> \
  -p run-id=<id> -p image=ghcr.io/fangrh/tdgl-<runner>:<tag>
```

## Dev Mode

When iterating on a service, use dev mode for fast feedback.

### Dev tool options
Present these to the user and recommend based on needs:

| Tool | Best for | Recommend when |
|------|----------|----------------|
| **Skaffold** | Simple services, kustomize | Default recommendation for this project |
| **Tilt** | Multi-service with UI | User needs hot-reload across services |
| **Raw docker/kubectl** | One-off debugging | No extra tooling desired |

### Common dev commands
```bash
# Build and push
docker build -f services/<name>/Dockerfile -t ghcr.io/fangrh/tdgl-<name>:dev .
docker push ghcr.io/fangrh/tdgl-<name>:dev

# Deploy
kubectl apply -k services/<name>/k8s/ -n tdgl

# Debug
kubectl port-forward svc/<name> 8080:80 -n tdgl
kubectl logs -f deployment/<name> -n tdgl
```

### Argo CD during dev
Set Argo CD to manual sync or pause auto-sync to prevent overwriting dev changes.

### After dev testing succeeds
When service changes are verified and working, remind the user:
> "Dev testing passed. Ready to re-enable Argo CD auto-sync? Run:
> `argocd app set tdgl-services --sync-policy automated`"

Do not re-enable auto-sync without user confirmation.

## Prod Mode
```
PR → CI builds changed images → merge to main → CI updates manifest tags → Argo CD auto-sync
```
Use prod mode only when changes are ready for main branch.

## Templates & Patterns
- Read `templates/README.md` for available templates and patterns
- Check `templates/services/_patterns/` for proven service designs
- Check `templates/workflows/_patterns/` for workflow patterns
- When a service/workflow design works well, extract it as a new pattern
