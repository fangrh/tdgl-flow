# On-Demand Viewer Architecture Design

**Date:** 2026-05-18
**Status:** Draft
**Related bug:** data-viewer CrashLoopBackOff due to missing DB columns (no migration)

## Problem

1. `data-viewer` runs as a permanent Deployment but is only needed when users view data — wastes resources
2. Database schema changes (new columns in `runs` table) cause CrashLoopBackOff because there's no migration mechanism
3. No extensibility for different viewer types (mesh, timeseries, etc.)

## Solution

Replace the permanent `data-viewer` Deployment with an on-demand system: a new `viewer-manager` microservice creates temporary viewer Pods per user session, with automatic cleanup on idle timeout. Database migrations run via Argo CD Pre-Sync Hook.

---

## 1. Architecture Overview

```
                          ┌─────────────┐
                          │   主页面     │
                          │  (tdgl-wf)  │
                          └──────┬──────┘
                                 │ POST /api/viewer-sessions
                          ┌──────▼──────┐
                          │viewer-manager│ ← 新微服务
                          └──────┬──────┘
                    ┌────────────┼────────────┐
                    │ 调 K8s API │ 查 DB      │ 定时清理
              ┌─────▼─────┐  ┌───▼───┐  ┌────▼────┐
              │ Pod+Svc   │  │sessions│  │ 后台任务 │
              │ (临时)    │  │ table  │  │ 超时删除 │
              └─────┬─────┘  └───────┘  └─────────┘
                    │
              ┌─────▼──────┐
              │ data-viewer│ (按需 Pod, 不是常驻)
              │ /mesh-viewer│ (后续扩展)
              └────────────┘

                    ┌─────────────┐
                    │    nginx    │
                    │ /viewer-session/{sid}/ → viewer-manager → proxy to Pod
                    └─────────────┘
```

**New components:**
- `viewer-manager` service: REST API + K8s client + background cleanup
- `viewer_sessions` database table: session metadata
- `/viewer-session/` nginx location: proxied through viewer-manager

**Modified components:**
- `data-viewer`: removed as permanent Deployment, becomes on-demand Pod
- `tdgl-workflow`: frontend adds iframe area + View button
- `nginx`: adds viewer-session route
- `infra/`: adds Alembic migration Pre-Sync Hook Job

**Unchanged:**
- `cpp-tdgl-runner`, `tdgl-generator`, Argo Workflows — simulation pipeline stays the same

---

## 2. Session Lifecycle

### State Machine

```
PENDING → STARTING → READY → EXPIRED → CLEANED
                 ↘ FAILED ↗
```

| State | Meaning |
|-------|---------|
| PENDING | API request received, checking for reusable session |
| STARTING | K8s Pod created, waiting for Ready |
| READY | Pod running, accessible |
| FAILED | Pod failed to start or crashed |
| EXPIRED | Idle timeout, marked for cleanup |
| CLEANED | Pod/Service deleted |

### Database Table: `viewer_sessions`

```python
class ViewerSession:
    session_id: str          # UUID
    run_id: str              # associated run
    viewer_type: str         # "data-viewer" | "mesh-viewer" | ...
    status: str              # state machine status
    pod_name: str            # K8s Pod name
    service_name: str        # K8s Service name
    session_url: str         # accessible URL
    active_clients: int      # currently connected clients
    last_accessed_at: datetime
    created_at: datetime
    expires_at: datetime     # expected expiry time
    error_message: str       # failure details
```

### Heartbeat Mechanism

```
Frontend every 30s → POST /api/viewer-sessions/{sid}/heartbeat
  → update last_accessed_at = now()

Frontend page unload → POST /api/viewer-sessions/{sid}/release
  → active_clients -= 1 (minimum 0)

Create/reuse session → active_clients += 1

Background cleanup task (runs inside viewer-manager, every 60s):
  1. Find sessions where active_clients == 0 AND last_accessed_at > 15min
  2. Mark EXPIRED
  3. Delete K8s Pod/Service
  4. Mark CLEANED

Failed Pod cleanup:
  1. Find sessions where status == FAILED AND created_at > 10min
  2. Delete K8s resources, mark CLEANED
```

### Session Reuse Rules

```
POST /api/viewer-sessions { run_id, viewer_type }

1. Lookup: run_id + viewer_type + status in (READY, STARTING)
2. Found → active_clients += 1, update last_accessed_at, return session_url
3. Not found → create new Pod with active_clients = 1, return new session
```

Multi-user sharing: as long as any client is sending heartbeats, session stays alive.

---

## 3. viewer-manager API

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/viewer-sessions` | Create/reuse session |
| GET | `/api/viewer-sessions/{sid}` | Query session status |
| GET | `/api/viewer-sessions` | List active sessions |
| POST | `/api/viewer-sessions/{sid}/heartbeat` | Heartbeat |
| POST | `/api/viewer-sessions/{sid}/release` | Client release |
| DELETE | `/api/viewer-sessions/{sid}` | Manual close |

### Create Session Flow

```
POST /api/viewer-sessions
  { run_id: "abc", viewer_type: "data-viewer" }

Response 200 (reuse):
  { session_id, status: "READY", session_url, active_clients: 2 }

Response 201 (new):
  { session_id, status: "STARTING", session_url }

Response 500:
  { error: "failed to create pod", message: "..." }
```

### K8s Pod Template

viewer-manager creates via Python `kubernetes` client:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: viewer-{session_id}
  namespace: tdgl
  labels:
    app: viewer-session
    viewer-type: data-viewer
    session-id: "{session_id}"
spec:
  containers:
    - name: viewer
      image: ghcr.io/fangrh/tdgl-data-viewer:{tag}
      env:
        - name: VIEWER_SESSION_ID
          value: "{session_id}"
        - name: RUN_ID
          value: "{run_id}"
      volumeMounts:
        - name: zarr-data
          mountPath: /data/zarr
  volumes:
    - name: zarr-data
      persistentVolumeClaim:
        claimName: zarr-data
```

Corresponding Service:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: viewer-{session_id}
  namespace: tdgl
spec:
  selector:
    session-id: "{session_id}"
  ports:
    - port: 80
      targetPort: 8000
```

### Routing: viewer-manager as Reverse Proxy

The session URL points to viewer-manager itself:
```
/viewer-session/{sid}/* → viewer-manager → looks up Pod → proxies via httpx
```

Nginx only needs a fixed route:
```nginx
location /viewer-session/ {
    proxy_pass http://viewer-manager.tdgl.svc.cluster.local/;
}
```

viewer-manager internally resolves `{sid}` to the Pod's Service address and proxies the request. This avoids dynamic nginx configuration.

---

## 4. Frontend iframe Integration

### Layout (tdgl-workflow main page)

```
┌──────────────────────────────────────────────┐
│  Navigation bar                               │
├──────────────────────────────────────────────┤
│              │                                │
│   Run list   │    Viewer area (iframe)        │
│   [Run A]    │    ┌────────────────────┐      │
│   [Run B] ← │    │                    │      │
│   [Run C]    │    │   data-viewer      │      │
│              │    │   iframe content    │      │
│   View btn   │    │                    │      │
│              │    └────────────────────┘      │
│              │    Status: Ready ●             │
│              │    [Close Viewer]              │
├──────────────────────────────────────────────┤
```

### Interaction Flow

1. User clicks View on a run
   → JS calls `POST /api/viewer-sessions { run_id, viewer_type }`
   → Show "Starting..." status, loading spinner in iframe area

2. Poll session status (`GET /api/viewer-sessions/{sid}`)
   → `STARTING` → keep waiting (poll every 2s)
   → `READY` → set `iframe.src = session_url`
   → `FAILED` → show error message

3. After iframe loads
   → Start heartbeat: `setInterval(30s, POST heartbeat)`
   → Show "Ready ●" status

4. User clicks View on another run
   → `POST release` on old session
   → Repeat from step 1

5. Page unload
   → `POST release`

### Security / CSP

- viewer-manager reverse proxy sets:
  ```
  Content-Security-Policy: frame-ancestors 'self'
  X-Frame-Options: SAMEORIGIN
  ```
- Same-origin through nginx unified entry point — no CORS needed
- viewer-manager passes through all headers when proxying

### Error Isolation

- iframe load failure → `onerror` event captured, show error placeholder
- Viewer Pod crash → heartbeat detects session → FAILED → show error
- Main page unaffected, user can select another run

---

## 5. Database Migration

### Alembic via Argo CD Pre-Sync Hook

New manifest in `infra/`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate
  namespace: tdgl
  annotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded
spec:
  template:
    spec:
      containers:
        - name: migrate
          image: ghcr.io/fangrh/viewer-manager:{tag}
          command: ["alembic", "upgrade", "head"]
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: data-viewer-config
                  key: database-url
      restartPolicy: Never
  backoffLimit: 3
```

Runs automatically before each Argo CD sync. If migration fails, sync aborts — preventing new images from starting against an old schema.

This solves the CrashLoopBackOff root cause (commit `2d799a6` added columns without migration).

---

## 6. Sub-Project Decomposition

### Sub-project A: Backend Core (viewer-manager + lifecycle + migration)

- New viewer-manager microservice (FastAPI + kubernetes client + alembic)
- Session CRUD API + K8s Pod lifecycle
- Heartbeat and auto-cleanup background task
- Alembic setup + initial migration (viewer_sessions table + missing runs columns)
- Pre-Sync Hook Job manifest
- Remove data-viewer permanent Deployment

### Sub-project B: Frontend Integration (iframe + nginx)

- tdgl-workflow frontend: add View button and iframe area
- viewer-manager reverse proxy implementation
- nginx route update
- Heartbeat JS logic + error handling

### Sub-project C: Viewer Extension (future)

- Split data-viewer into mesh-viewer / zarr-viewer / timeseries-viewer
- viewer_type registry in viewer-manager
- Route by data type

**Build order:** A → B → C. Each sub-project gets its own spec → plan → implementation cycle.

---

## 7. CLAUDE.md Updates

After implementation, update CLAUDE.md with:
- viewer-manager service in the service list
- New WorkflowTemplate pattern for on-demand viewer sessions
- DB migration rules: all schema changes go through Alembic + Pre-Sync Hook
- Cleanup: viewer-manager handles Pod lifecycle, not manual kubectl

---

## 8. Immediate Fix (before refactor)

The current CrashLoopBackOff can be fixed immediately with:
```sql
ALTER TABLE runs ADD COLUMN mesh_sites JSONB;
ALTER TABLE runs ADD COLUMN mesh_elements INTEGER;
ALTER TABLE runs ADD COLUMN n_sites INTEGER;
ALTER TABLE runs ADD COLUMN solver_options JSONB;
```

This unblocks the current deployment while the refactor is in progress.
