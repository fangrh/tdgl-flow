# kubeflow-tdgl

Clean Kubeflow-oriented TDGL simulation platform.

The first implemented subsystem is the TDGL data service:

- PostgreSQL-compatible metadata schema
- Filesystem-backed Zarr frame arrays
- FastAPI read/write API
- Server-Sent Events for real-time frame availability
- Synthetic TDGL-like data for tests and UI prototyping

## Development

```bash
python -m pip install -e ".[dev]"
pytest
uvicorn tdgl_data.app:create_app --factory --reload
```
