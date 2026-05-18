# SDK & Notebook Rules Design

**Date:** 2026-05-19
**Scope:** Add rules to CLAUDE.md requiring Python SDK API + notebook examples for every microservice, enabling agent-driven debugging.

## Problem

Currently the `TDGLClient` SDK only covers the simulation workflow (device build, timing, submit, results). Other microservices — viewer-manager, generator, data-viewer — lack Python API wrappers. When an agent (or user) needs to debug a service, the only option is clicking through the web UI or running raw kubectl commands. This makes automated diagnosis difficult.

## Design Decision

Extend the existing `TDGLClient` in `src/tdgl_sdk/client.py` to cover all microservices, with built-in diagnostic methods. Every service gets a matching notebook example. Enforce via CLAUDE.md rules.

### Why unified SDK

A single `TDGLClient` instance lets agents call any service without managing multiple client objects. It matches the project's microservices-under-one-namespace architecture.

## Rule: SDK Extension

Every microservice (app or workflow) must have API methods in `TDGLClient`:

- **Business methods:** Named `<verb>_<resource>()` — e.g., `list_viewers()`, `create_viewer()`, `delete_viewer()`.
- **Diagnostic methods:** Every service must expose:
  - `health_<service>()` — returns service health status (up/down/degraded)
  - `get_<service>_logs(limit=N)` — returns recent log entries
  - `get_<service>_status()` — returns detailed service status (version, uptime, active sessions, etc.)
- **Implementation:** Use `httpx`, same as existing methods. Follow the pattern of `resp.raise_for_status()` + `return resp.json()`.

### Method template

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

## Rule: Notebook Examples

Every microservice must have two example files in `notebooks/`:

| File | Purpose |
|------|---------|
| `notebooks/<name>_api_demo.ipynb` | Interactive Jupyter notebook |
| `notebooks/<name>_api_demo.py` | Equivalent pure-Python script |

### Notebook structure

Each notebook must include:

1. **Setup cell** — import TDGLClient, connect to cluster
2. **Basic usage** — demonstrate each business API method
3. **Error handling** — show what happens with invalid inputs, how to catch errors
4. **Diagnostics** — demonstrate `health_*()`, `get_*_logs()`, `get_*_status()`
5. **Agent debug scenario** — a code block showing how an agent would programmatically detect and report a service issue

### Naming convention

- Service `viewer-manager` → files: `viewer_manager_api_demo.ipynb`, `viewer_manager_api_demo.py`
- Service `data-viewer` → files: `data_viewer_api_demo.ipynb`, `data_viewer_api_demo.py`

## Rule: Adding a New Service — Updated Checklist

When adding a new service, the existing CLAUDE.md checklist gets these additional items:

| File | What to add |
|------|------------|
| `src/tdgl_sdk/client.py` | Add business API methods + 3 diagnostic methods (health, logs, status) |
| `notebooks/<name>_api_demo.ipynb` | New notebook with setup + usage + errors + diagnostics |
| `notebooks/<name>_api_demo.py` | New Python script, same content as notebook |
| `tests/test_sdk.py` | Add test cases for new API methods |

### Validation

- [ ] `from tdgl_sdk import TDGLClient; c = TDGLClient(base_url); c.health_<name>()` works
- [ ] `pytest tests/test_sdk.py -k "<name>"` passes
- [ ] `jupyter execute notebooks/<name>_api_demo.ipynb` runs without error (or manual run)
- [ ] `python notebooks/<name>_api_demo.py` runs without error

## Existing Services — Backfill Plan

Current services that need SDK + notebook additions:

| Service | SDK Status | Notebook Status |
|---------|-----------|-----------------|
| simulator (cpp-tdgl-runner) | Done | Done (tdgl_demo.ipynb) |
| viewer-manager | Missing | Missing |
| generator | Missing | Missing |
| data-viewer | Missing | Missing |
| tdgl-workflow | Missing | Missing |

These will be added as a separate implementation task after the rule is established.

## Impact on Existing CLAUDE.md Sections

### "Adding a New Service" — add to "Must also update" table:

```
src/tdgl_sdk/client.py            | Add API methods + diagnostic methods
notebooks/<name>_api_demo.ipynb   | New: interactive API demo notebook
notebooks/<name>_api_demo.py      | New: equivalent Python script
```

### "Validate" checklist — add:

```
- [ ] SDK API methods added and tested
- [ ] Notebook examples created and runnable
- [ ] Diagnostic methods (health, logs, status) return valid responses
```

### New section: `## SDK & Notebook Rules`

Will contain the full rule set described above.
