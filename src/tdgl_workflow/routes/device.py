from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.plots import render_mesh_plot

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


def _device_form_data(form: dict) -> dict:
    return {
        "film_width": float(form.get("film_width", 10)),
        "film_height": float(form.get("film_height", 2)),
        "elec_width": float(form.get("elec_width", 0.5)),
        "elec_height": float(form.get("elec_height", 1)),
        "elec_y_offset": float(form.get("elec_y_offset", 0)),
        "probe_points": [
            (float(form.get("probe1_x", -3)), float(form.get("probe1_y", 0))),
            (float(form.get("probe2_x", 3)), float(form.get("probe2_y", 0))),
        ],
        "max_edge_length": float(form.get("max_edge_length", 1.0)),
        "smooth": int(form.get("smooth", 100)),
    }


@router.get("/device", response_class=HTMLResponse)
def device_page(request: Request):
    return _render_template("device.html", {
        "request": request,
        "page": "device",
        "form": {},
        "plot_b64": None,
    })


@router.post("/device", response_class=HTMLResponse)
async def device_preview(request: Request):
    form_data = await request.form()
    form = {k: v for k, v in form_data.items()}
    action = form.get("action", "preview")

    params = _device_form_data(form)
    mesh_data = build_rectangular_device(**params)
    plot_b64 = render_mesh_plot(mesh_data)

    device_params = {k: v for k, v in params.items()}
    device_params["mesh"] = {
        "sites": mesh_data["sites"],
        "elements": mesh_data["elements"],
        "probe_indices": mesh_data["probe_indices"],
        "num_sites": mesh_data["num_sites"],
        "num_elements": mesh_data["num_elements"],
    }
    request.session["device_params"] = device_params

    if action == "next":
        return RedirectResponse("/timing", status_code=303)

    return _render_template("device.html", {
        "request": request,
        "page": "device",
        "form": form,
        "plot_b64": plot_b64,
    })