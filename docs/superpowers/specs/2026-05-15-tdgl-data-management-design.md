# TDGL Data Management Design

Date: 2026-05-15

## Scope

This specification covers the first subproject for the clean `kubeflow-tdgl`
repository: TDGL database and data-management infrastructure. The
`../git-tdgl-light` project is reference material only. This repository starts
fresh and uses synthetic TDGL-like data for the first tests.

The design supports real-time visualization by exposing simulation frames as
data, not pre-rendered MP4 files. The frontend will decode and draw heatmaps,
colorbars, I-V markers, timeline controls, play/pause state, and synchronized
panels directly from stored frame data.

## Goals

- Store queryable run metadata, timeline data, status, provenance, and I-V
  scalar points in PostgreSQL.
- Store dense frame arrays in Zarr, using filesystem storage first and an
  object-store interface that can later target MinIO or S3.
- Expose a FastAPI data service for run creation, frame append, frame reads,
  timeline reads, I-V reads, status changes, and Server-Sent Events.
- Make newly committed frames visible immediately through the API.
- Provide synthetic random TDGL-like data for tests and early UI development.
- Manage PostgreSQL schema with Alembic migrations from the start.

## Non-Goals

- No MP4/video rendering pipeline in this subproject.
- No full Kubeflow pipeline implementation yet.
- No production MinIO/S3 deployment yet.
- No migration of `../git-tdgl-light` history or direct source copy.
- No final frontend implementation in this subproject.

## Architecture

The first version is a standalone TDGL data service. It should run locally
without Kubeflow so the storage contract can be tested before workflow
orchestration is added. Later Kubeflow pipeline steps will call the same API.

Core components:

- PostgreSQL: authoritative metadata store for runs, frames, scalar I-V points,
  status, provenance, and UI timeline data.
- Zarr store: dense simulation frame arrays such as `psi_real`, `psi_imag`,
  and `mu`.
- Storage abstraction: filesystem-backed in v1, with the same interface later
  backed by MinIO/S3.
- FastAPI data service: creates runs, appends frames, exposes read APIs, and
  emits Server-Sent Events.
- Synthetic data generator: creates smooth random TDGL-like frames for tests.
- Alembic: owns database schema migrations.

Data flow:

```text
simulation or test generator
  -> POST /api/runs
  -> POST /api/runs/{run_id}/frames
  -> write dense arrays into Zarr
  -> write frame metadata and I-V scalar values into PostgreSQL
  -> mark frame as available
  -> publish SSE event frame_available
  -> frontend fetches timeline, I-V data, and selected frames
```

The UI derives `|psi|^2` and phase from `psi_real` and `psi_imag`. The I-V plot
uses PostgreSQL scalar values so the full curve can load quickly without
opening every frame array.

## PostgreSQL Data Model

PostgreSQL stores only relational, queryable, and synchronization data.

### `runs`

One row per TDGL simulation run.

Fields:

- `run_id`: UUID or ULID string primary identifier.
- `status`: `created`, `running`, `completed`, or `failed`.
- `solver_type`: `cpp`, `cuda`, `synthetic`, or future solver labels.
- `created_at`, `started_at`, `completed_at`.
- `git_commit`, `image_tag`.
- `kubeflow_run_id`, `kubeflow_pipeline_id`, `kubeflow_task_id`: nullable until
  Kubeflow integration exists.
- `device_params`: JSONB.
- `timing_params`: JSONB.
- `mesh_metadata`: JSONB summary, including grid shape and coordinate metadata.
- `zarr_root`: logical storage key for this run.
- `metadata`: JSONB for extra provenance.

### `frames`

One row per simulation frame.

Fields:

- `run_id`.
- `frame_index`: zero-based integer.
- `time_value`: simulation time.
- `je`: current value.
- `voltage`: measured probe voltage.
- `status`: `writing`, `available`, or `failed`.
- `zarr_group`: logical path for arrays.
- `checksum`: optional checksum for idempotent append checks.
- `created_at`, `committed_at`.

Unique constraint: `(run_id, frame_index)`.

### `iv_points`

Scalar I-V data optimized for fast plot loading.

Fields:

- `run_id`.
- `frame_index`.
- `je`.
- `voltage`.
- `time_value`.

Unique constraint: `(run_id, frame_index)`.

### `run_events`

Append-only audit and SSE replay support.

Fields:

- `event_id`: increasing integer or sortable identifier.
- `run_id`.
- `event_type`: `run_created`, `run_started`, `frame_available`,
  `run_completed`, `run_failed`.
- `payload`: JSONB.
- `created_at`.

## Zarr Layout

Dense arrays live in Zarr, not PostgreSQL.

Logical layout:

```text
zarr-root/
  runs/
    {run_id}/
      frames.zarr/
        psi_real[time, y, x]
        psi_imag[time, y, x]
        mu[time, y, x]
        mask[y, x]              optional
        x[y, x] or x[x]         optional
        y[y, x] or y[y]         optional
```

Chunking is frame-oriented, for example `(1, 128, 128)` or `(1, grid_y, grid_x)`
for small test data. This allows one frame to be read cheaply for interactive
display. Compression is enabled by default.

The storage layer exposes a small interface:

```text
create_run_store(run_id, grid_shape, fields)
append_frame(run_id, frame_index, arrays)
read_frame(run_id, frame_index, fields)
get_store_uri(run_id)
```

The first implementation backs this interface with local filesystem paths.
MinIO/S3 support can later replace the backing store without changing API
handlers, repository code, or frontend contracts.

## API Contract

FastAPI endpoints:

```text
POST /api/runs
GET  /api/runs
GET  /api/runs/{run_id}
POST /api/runs/{run_id}/frames
GET  /api/runs/{run_id}/timeline
GET  /api/runs/{run_id}/iv
GET  /api/runs/{run_id}/frames/{frame_index}
GET  /api/runs/{run_id}/events
POST /api/runs/{run_id}/complete
POST /api/runs/{run_id}/fail
```

Frame append accepts scalar metadata and dense arrays. For tests, JSON may be
allowed for small arrays. The durable API should favor binary or multipart
NumPy/Zarr-compatible upload so large arrays are not sent as huge JSON lists.

Frame read response returns the selected frame arrays and scalar metadata. The
first version should expose raw `psi_real`, `psi_imag`, and `mu`; the frontend
derives `|psi|^2` and phase.

Timeline response returns ordered frame metadata:

```json
{
  "run_id": "example",
  "frames": [
    {
      "frame_index": 0,
      "time_value": 0.0,
      "je": 0.0,
      "voltage": 0.001,
      "status": "available"
    }
  ],
  "stats": {
    "psi_real": {"min": -1.0, "max": 1.0},
    "psi_imag": {"min": -1.0, "max": 1.0},
    "mu": {"min": -0.5, "max": 0.5}
  }
}
```

## Real-Time Event Behavior

The event stream uses Server-Sent Events in v1:

```text
GET /api/runs/{run_id}/events
```

Example events:

```json
{"type":"run_created","run_id":"..."}
{"type":"frame_available","run_id":"...","frame_index":12,"time":0.35,"je":1.2,"voltage":0.003}
{"type":"run_completed","run_id":"..."}
{"type":"run_failed","run_id":"...","message":"..."}
```

The frame append operation is atomic from the API consumer's perspective:

1. Write dense arrays to Zarr.
2. Insert or update PostgreSQL frame metadata and I-V scalar values.
3. Mark the frame as `available`.
4. Insert a `run_events` row.
5. Emit `frame_available` on SSE.

The service must not emit `frame_available` before the frame metadata and Zarr
arrays are readable.

SSE should support reconnect with `Last-Event-ID` by replaying matching
`run_events` rows before continuing with live events.

## Frontend Synchronization Contract

This subproject does not implement the final frontend, but it defines the
contract needed by the future UI:

- The UI opens a run and loads `/timeline` and `/iv`.
- The UI subscribes to `/events`.
- Each `frame_available` extends the slider/timeline.
- Heatmaps, I-V annotation dot, current time display, slider, and play/pause
  use one shared `frame_index`.
- Colorbar bounds come from timeline stats and update as frames arrive.
- No MP4 file is required.

## Error Handling

- Zarr write failure prevents the frame from becoming `available`.
- Duplicate frame append returns `409 Conflict` unless idempotent append is
  explicitly supported and checksum/metadata match.
- Missing run returns `404`.
- Missing frame returns `404` in v1.
- Failed runs keep available frames for debugging.
- Store and database errors are logged with `run_id` and `frame_index`.
- API error bodies stay concise and avoid exposing internal paths or secrets.
- Alembic migrations run explicitly in dev/test/CI, not hidden inside app
  startup.

## Testing Strategy

Use synthetic TDGL-like data first because no existing run data fixture is
available in `../git-tdgl-light`.

Test layers:

- Repository tests: create run, append frame metadata, list timeline, complete
  run, fail run.
- Zarr store tests: create run arrays, append frames, read frames, verify shape,
  chunking, compression, and values.
- API tests: create run, append synthetic frames, read timeline, read I-V, read
  individual frames.
- SSE tests: append frames and verify ordered `frame_available` events.
- Contract tests: verify frontend-facing JSON shapes for timeline, I-V, frame,
  and run status responses.
- CI integration tests: run PostgreSQL as a service container when CI/CD is
  introduced.

Synthetic data should generate smooth fields over a 2D grid:

- `psi_real`: time-varying smooth random field.
- `psi_imag`: phase-shifted smooth random field.
- `mu`: smoother potential-like field.
- `je`: monotonic or sweep-like current sequence.
- `voltage`: current-dependent value with small noise.

## Acceptance Criteria

- A developer can create a run, append synthetic frames, and read them back
  through the API.
- PostgreSQL contains run metadata, frame metadata, and I-V scalar points.
- Zarr contains dense frame arrays with frame-oriented chunks.
- SSE emits one `frame_available` event after each committed frame.
- The data contract supports a future synchronized UI without MP4 rendering.
- The storage abstraction can later target MinIO/S3 without API contract
  changes.
