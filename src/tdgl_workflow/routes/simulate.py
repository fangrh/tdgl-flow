import uuid
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from tdgl_workflow.config import Settings
from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.timing import build_timing, build_timing_segmented

router = APIRouter()

_settings = Settings()

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    autoescape=True,
    cache_size=0,
)
_env.globals["base_path"] = _settings.base_path


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

    cpu_cores = float(form_data.get("cpu_cores", "2"))
    memory_gb = float(form_data.get("memory_gb", "4"))

    settings: Settings = request.app.state.settings

    # Regenerate mesh and timing from stored params
    mesh_data = build_rectangular_device(
        film_width=device_params["film_width"],
        film_height=device_params["film_height"],
        elec_width=device_params["elec_width"],
        elec_height=device_params["elec_height"],
        elec_y_offset=device_params["elec_y_offset"],
        probe_points=[tuple(p) for p in device_params["probe_points"]],
        max_edge_length=device_params["max_edge_length"],
        smooth=device_params["smooth"],
    )

    if timing_params.get("mode") == "segmented":
        timing_data = build_timing_segmented(
            segments=timing_params["segments"],
            ramp_time=timing_params["ramp_time"],
            stable_time=timing_params["stable_time"],
            save_time=timing_params["save_time"],
        )
    else:
        timing_data = build_timing(
            je_initial=timing_params["je_initial"],
            je_final=timing_params["je_final"],
            je_step=timing_params["je_step"],
            ramp_time=timing_params["ramp_time"],
            stable_time=timing_params["stable_time"],
            save_time=timing_params["save_time"],
            ramp_down=timing_params.get("ramp_down", False),
        )

    num_sites = mesh_data["num_sites"]

    # Compute 2D grid shape from mesh spatial extent and density
    import numpy as np
    sites = np.array(mesh_data["sites"])
    x_range = sites[:, 0].max() - sites[:, 0].min()
    y_range = sites[:, 1].max() - sites[:, 1].min()
    spacing = np.sqrt(x_range * y_range / max(num_sites, 1))
    nx = max(2, round(x_range / spacing))
    ny = max(2, round(y_range / spacing))
    grid_shape = [ny, nx]

    # Build full params with mesh/schedule for data service storage
    full_device_params = dict(device_params)
    full_device_params["mesh"] = mesh_data

    full_timing_params = dict(timing_params)
    full_timing_params["schedule"] = {
        "steps": timing_data["steps"],
        "ramp_down_steps": timing_data["ramp_down_steps"],
        "solve_time": timing_data["solve_time"],
        "n_steps": timing_data["n_steps"],
    }
    full_timing_params["grid_shape"] = grid_shape
    full_timing_params["solver_options"] = solver_options

    mesh_data = full_device_params.get("mesh", {})

    with httpx.Client(timeout=30.0) as client:
        submit_resp = client.post(
            f"{settings.data_service_url}/api/runs",
            json={
                "solver_type": "cpp-tdgl",
                "n_sites": mesh_data.get("num_sites", 0),
                "device_params": full_device_params,
                "timing_params": full_timing_params,
                "mesh_sites": mesh_data.get("sites"),
                "mesh_elements": mesh_data.get("elements"),
                "solver_options": solver_options,
                "total_frames": timing_data["n_steps"],
            },
        )
        submit_resp.raise_for_status()
        created_run = submit_resp.json()
        run_id = created_run["run_id"]

        import json as _json
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
                        {"name": "device-params-json", "value": _json.dumps(full_device_params)},
                        {"name": "timing-params-json", "value": _json.dumps(full_timing_params)},
                        {"name": "solver-options-json", "value": _json.dumps(solver_options)},
                        {"name": "cpu", "value": str(cpu_cores)},
                        {"name": "memory", "value": f"{memory_gb}Gi"},
                    ],
                },
            },
        }

        try:
            client.post(
                f"{settings.argo_server_url}/api/v1/workflows/{settings.tdgl_namespace}",
                json={"workflow": workflow},
                headers={"Content-Type": "application/json"},
                verify=False,
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
        "device_params": full_device_params,
        "timing_params": full_timing_params,
        "submitted": True,
        "run_id": run_id,
        "viewer_url": "/tdgl/viewer",
        "runs": runs,
    })
