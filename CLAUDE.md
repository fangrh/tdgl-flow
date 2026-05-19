# GitOps Workflow Rules

This project uses Argo CD + Argo Workflows + GitHub Actions CI for deployment.
Follow these rules when modifying services, workflows, or CI.

## Quick Reference

| Task | What to do |
|------|-----------|
| Add a new service | Copy `templates/services/_base/` → adapt → update CI + Argo CD + kustomization + SDK + notebook |
| Add a new workflow | Copy `templates/workflows/_base/` → adapt → add to `workflows/` |
| Debug a service | Use SDK diagnostic methods or dev mode (see below) |
| Start a session | Read `.dev-mode` to know current dev state (bind-mount modules) |
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
| `src/tdgl_sdk/client.py` | Add business API methods + diagnostic methods (health, logs, status) |
| `notebooks/<name>_api_demo.ipynb` | New: interactive API demo notebook |
| `notebooks/<name>_api_demo.py` | New: equivalent Python script |

### 4. Naming

- Image: `ghcr.io/fangrh/tdgl-<name>`
- Namespace: `tdgl`
- Argo App: `tdgl-services` (wave 1, auto-sync)

### 5. Validate

- [ ] `docker build -f services/<name>/Dockerfile .` succeeds
- [ ] `kubectl apply --dry-run=client -k services/<name>/k8s/` passes
- [ ] CI path detection includes new service
- [ ] Argo CD syncs without errors
- [ ] SDK API methods added to `src/tdgl_sdk/client.py` and tested with `pytest tests/test_sdk.py -k "<name>"`
- [ ] Notebook examples created: `notebooks/<name>_api_demo.ipynb` + `.py` — both runnable
- [ ] Diagnostic methods (`health_<name>()`, `get_<name>_logs()`, `get_<name>_status()`) return valid responses

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

## Viewer Manager

The `viewer-manager` service manages on-demand viewer Pods. Viewer sessions are temporary — created when users click View, cleaned up after idle timeout.

### Architecture
- `viewer-manager` creates/deletes viewer Pods via Kubernetes API
- Each session gets a unique `session_id` and a URL at `/viewer-session/{sid}/`
- Sessions are reused when the same run_id + viewer_type is requested
- Background task cleans up idle (15min) and failed (10min) sessions

### When modifying viewer-manager
- All DB schema changes must go through Alembic: `alembic revision --autogenerate -m "description"`
- Migration runs via Argo CD Pre-Sync Hook before each sync
- Tests mock K8s API — run with `pytest tests/test_viewer_manager.py -v`
- The `kubernetes` Python client reads in-cluster config by default


## SDK & Notebook Rules

Every microservice must have a Python API wrapper in the unified `TDGLClient` SDK (`src/tdgl_sdk/client.py`) and matching notebook examples in `notebooks/`. This enables agents to programmatically call services and diagnose issues without requiring users to click through the web UI.

### SDK Extension

Add methods to `TDGLClient` for every service:

- **Business methods:** `<verb>_<resource>()` — e.g., `list_viewers()`, `create_viewer()`, `delete_viewer()`
- **Diagnostic methods** (required for every service):
  - `health_<service>()` — returns `{"status": "up"|"down"|"degraded", ...}`
  - `get_<service>_logs(limit=50)` — returns recent log entries
  - `get_<service>_status()` — returns detailed status (version, uptime, active sessions, etc.)
- **Implementation:** use `httpx`, follow existing `resp.raise_for_status()` + `return resp.json()` pattern

Method template:
```python
def list_<resources>(self) -> list[dict]:
    resp = httpx.get(f"{self.base_url}/api/<resources>", timeout=10.0)
    resp.raise_for_status()
    return resp.json()

def health_<service>(self) -> dict:
    resp = httpx.get(f"{self.base_url}/api/<service>/health", timeout=5.0)
    resp.raise_for_status()
    return resp.json()

def get_<service>_logs(self, limit: int = 50) -> list[dict]:
    resp = httpx.get(f"{self.base_url}/api/<service>/logs", params={"limit": limit}, timeout=10.0)
    resp.raise_for_status()
    return resp.json()
```

### Notebook Examples

Every service must have two files in `notebooks/`:

| File | Purpose |
|------|---------|
| `notebooks/<name>_api_demo.ipynb` | Interactive Jupyter notebook |
| `notebooks/<name>_api_demo.py` | Equivalent pure-Python script |

Each notebook must include:
1. **Setup** — import TDGLClient, connect to cluster
2. **Basic usage** — demonstrate each business API method
3. **Error handling** — invalid inputs, error catching patterns
4. **Diagnostics** — `health_*()`, `get_*_logs()`, `get_*_status()`
5. **Agent debug scenario** — code block showing how an agent programmatically detects and reports a service issue

Naming: use underscores — service `viewer-manager` → `viewer_manager_api_demo.ipynb`.

### Testing

- [ ] `from tdgl_sdk import TDGLClient; c = TDGLClient(base_url); c.health_<name>()` works
- [ ] `pytest tests/test_sdk.py -k "<name>"` passes
- [ ] `python notebooks/<name>_api_demo.py` runs without error
- [ ] Diagnostic methods return valid responses
## Dev Mode

When iterating on a service, use dev mode for fast feedback.

### Dev tool options
Present these to the user and recommend based on needs:

| Tool | Best for | Recommend when |
|------|----------|----------------|
| **Skaffold** | Simple services, kustomize | Default recommendation for this project |
| **Tilt** | Multi-service with UI | User needs hot-reload across services |
| **Raw docker/kubectl** | One-off debugging | No extra tooling desired |

### Bind Mount Dev Pattern (Inner Loop)
During development, compiled artifacts can be bind-mounted into containers for fast iteration
without rebuilding images. This is the **Inner Loop** — once business logic is validated, bake
artifacts into the Docker image for production.

```
bind/                    # NOT committed to git
├── <module>/            # per business module
│   └── ...              # compiled/built artifacts ready to mount
```

- `bind/<module>/` maps to the corresponding `src/<module>/` source code
- Only mount specific modules you are actively iterating on
- Add `bind/` to `.gitignore` — it contains local build artifacts
- Transition to prod: rebuild the Docker image with artifacts included, remove the mount

### When to suggest Bind Mount Dev
The agent should proactively suggest this mode when **any** of these conditions are met:
- User is iterating on a specific module and rebuilding images repeatedly
- User is debugging compiled artifacts (e.g. C++ solver binaries)
- User asks for faster dev iteration or mentions slow rebuild cycles
- Changes are confined to `src/<module>/` and don't touch Dockerfile or dependencies

When triggered, present it as a choice: "You're iterating on `<module>`. Want to use bind mount dev
to skip image rebuilds?" The user can also explicitly request this mode at any time.

### Dev mode state file (`.dev-mode`)
A `.dev-mode` file in the repo root tracks which modules are currently in bind mount dev mode.
Every agent session MUST read this file at startup to understand the current dev state.

Format:
```yaml
# .dev-mode
mode: bind-mount
modules:
  - cpp-tdgl
  # - tdgl_data
active_since: "2026-05-19"
```

- `mode`: `bind-mount` or `standard` (image rebuild per iteration)
- `modules`: list of modules currently bind-mounted into containers
- Agent must update this file when activating or deactivating bind mount dev
- Agent must remind user to transition to prod mode (remove mount, rebuild image) when done
- `.dev-mode` is committed to git so team visibility is maintained

```bash
# Example: mount compiled cpp-tdgl binary into runner container
kubectl -n tdgl patch deployment cpp-tdgl-runner --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-",
  "value":{"name":"bind-mount","mountPath":"/app/tdgl_solver"}},
  {"op":"add","path":"/spec/template/spec/volumes/-",
  "value":{"name":"bind-mount","hostPath":{"path":"/path/to/bind/cpp-tdgl","type":"Directory"}}}]'
```

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
