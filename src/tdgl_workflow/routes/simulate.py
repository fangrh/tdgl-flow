import uuid
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from tdgl_workflow.config import Settings

router = APIRouter()

# Create custom Jinja2 environment with caching disabled for Python 3.13 compatibility
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    autoescape=True,
    cache_size=0,  # Disable caching
)


def _render_template(template_name: str, context: dict):
    from starlette.templating import _TemplateResponse
    template = _env.get_template(template_name)
    return _TemplateResponse(template, context)


def _fetch_runs(settings: Settings) -> list:
    try:
        resp = httpx.get(f"{settings.data_service_url}/api/runs", timeout=5.0)
        if resp.status_code == 200:
            runs = resp.json()
            for run in runs:
                run["viewer_url"] = "/tdgl/viewer"
            return runs
    except httpx.HTTPError:
        pass
    return []


@router.get("/simulate", response_class=HTMLResponse)
def simulate_page(request: Request):
    device_params = request.session.get("device_params")
    timing_params = request.session.get("timing_params")
    settings: Settings = request.app.state.settings
    runs = _fetch_runs(settings)

    return _render_template("simulate.html", {
        "request": request,
        "page": "simulate",
        "has_device": device_params is not None,
        "has_timing": timing_params is not None,
        "device_params": device_params,
        "timing_params": timing_params,
        "submitted": False,
        "run_id": None,
        "viewer_url": None,
        "runs": runs,
    })


@router.post("/simulate", response_class=HTMLResponse)
async def simulate_submit(request: Request):
    device_params = request.session.get("device_params")
    timing_params = request.session.get("timing_params")

    if not device_params or not timing_params:
        return simulate_page(request)

    form_data = await request.form()
    solver_options = {
        "dt": float(form_data.get("dt", "1e-6")),
        "max_dt": float(form_data.get("max_dt", "0.1")),
        "adaptive": form_data.get("adaptive", "true") == "true",
    }

    settings: Settings = request.app.state.settings
    num_sites = device_params["mesh"]["num_sites"]

    with httpx.Client(timeout=30.0) as client:
        create_resp = client.post(
            f"{settings.data_service_url}/api/runs",
            json={
                "solver_type": "cpp-tdgl",
                "grid_shape": [num_sites, 1],
                "device_params": device_params,
                "timing_params": timing_params,
                "metadata": {"solver_options": solver_options},
                "total_frames": timing_params["schedule"]["n_steps"],
            },
        )
        create_resp.raise_for_status()
        created_run = create_resp.json()
        run_id = created_run["run_id"]

        workflow = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Workflow",
            "metadata": {
                "generateName": f"cpp-tdgl-{run_id[:8]}-",
                "namespace": settings.tdgl_namespace,
                "labels": {"run-id": run_id},
            },
            "spec": {
                "workflowTemplateRef": {"name": "cpp-tdgl-sim"},
                "arguments": {
                    "parameters": [
                        {"name": "run-id", "value": run_id},
                        {"name": "data-service-url", "value": settings.data_service_url},
                    ],
                },
            },
        }

        try:
            client.post(
                f"{settings.argo_server_url}/api/v1/workflows/{settings.tdgl_namespace}",
                json={"workflow": workflow},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            pass

    request.session.pop("device_params", None)
    request.session.pop("timing_params", None)

    runs = _fetch_runs(settings)
    return _render_template("simulate.html", {
        "request": request,
        "page": "simulate",
        "has_device": True,
        "has_timing": True,
        "device_params": device_params,
        "timing_params": timing_params,
        "submitted": True,
        "run_id": run_id,
        "viewer_url": "/tdgl/viewer",
        "runs": runs,
    })