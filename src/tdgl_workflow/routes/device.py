from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.config import Settings

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
    })


@router.post("/device", response_class=HTMLResponse)
async def device_preview(request: Request):
    form_data = await request.form()
    form = {k: v for k, v in form_data.items()}
    action = form.get("action", "preview")

    params = _device_form_data(form)

    request.session["device_params"] = {
        "film_width": params["film_width"],
        "film_height": params["film_height"],
        "elec_width": params["elec_width"],
        "elec_height": params["elec_height"],
        "elec_y_offset": params["elec_y_offset"],
        "probe_points": [list(p) for p in params["probe_points"]],
        "max_edge_length": params["max_edge_length"],
        "smooth": params["smooth"],
    }

    if action == "next":
        return RedirectResponse(_settings.base_path + "/timing", status_code=303)

    return _render_template("device.html", {
        "request": request,
        "page": "device",
        "form": form,
    })
