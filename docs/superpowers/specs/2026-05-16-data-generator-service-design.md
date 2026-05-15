# Data Generator Service Design (Sub-project 2)

## Goal

Build a separate FastAPI service with a web UI that generates synthetic TDGL data
in batches, writing frames to the data service API. One Je value per batch, with
configurable delays between batches to simulate real TDGL simulation output.

## Architecture

A new `tdgl_generator/` package runs as its own FastAPI app. A single HTML page
provides a form to configure generation parameters and start/stop generation
runs. The generator calls the data service REST API to create runs and append
frames. It runs as its own K8s Deployment.

## Generator Web UI

A single page at `/` with a form:

- **Je range**: min (default -1.0), max (default 1.0), count (default 10)
- **Frames per Je**: how many frames per Je value (default 5)
- **Delay between batches**: seconds to wait after each Je batch (default 2.0)
- **Grid shape**: Y rows, X columns (default 72x72)
- **Start** button: begins generation
- **Stop** button: cancels running generation
- **Status area**: shows progress (current Je, frame index, batch number)

## Generation Flow

1. User fills form, clicks Start.
2. Generator calls `POST {data_service_url}/api/runs` to create a run.
3. For each Je value in the sweep (count values evenly spaced from min to max):
   - Generate N frames of synthetic data at that Je using
     `tdgl_data.synthetic.generate_synthetic_run`.
   - Call `POST {data_service_url}/api/runs/{id}/frames` for each frame.
   - Wait the configured delay seconds.
4. Status updates after each frame and batch.
5. Stop button cancels the running asyncio task.

## Configuration

Environment variables:
- `TDGL_DATA_SERVICE_URL`: base URL of the data service (required).
  Example: `http://data-service.tdgl.svc.cluster.local`.
- `TDGL_GENERATOR_PORT`: port to listen on (default 8001).

## K8s

- Separate Deployment in the `tdgl` namespace.
- ConfigMap sets `TDGL_DATA_SERVICE_URL`.
- No PVC needed (stateless).
- Service exposes port 8001.

## Docker

Own Dockerfile in `tdgl_generator/` or a second build target. Shares the
synthetic data generation from `tdgl_data.synthetic` — imported as a library
dependency since both are in the same repository.

## Testing

- Unit tests for the generation sweep logic (Je values, frame generation).
- API tests for the start/stop endpoints.
- Test that frames are correctly sent to a mock data service.
