from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tdgl_workflow.timing import build_timing
from tdgl_workflow.plots import render_timing_plot

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


@router.get("/timing", response_class=HTMLResponse)
def timing_page(request: Request):
    has_device = "device_params" in request.session
    return _render_template("timing.html", {
        "request": request,
        "page": "timing",
        "form": {},
        "plot_b64": None,
        "has_device": has_device,
    })


@router.post("/timing", response_class=HTMLResponse)
async def timing_preview(request: Request):
    form_data = await request.form()
    form = {k: v for k, v in form_data.items()}
    action = form.get("action", "preview")

    has_device = "device_params" in request.session
    if not has_device:
        return RedirectResponse("/device", status_code=303)

    params = {
        "je_initial": float(form.get("je_initial", 0)),
        "je_final": float(form.get("je_final", 5)),
        "je_step": float(form.get("je_step", 1)),
        "ramp_time": float(form.get("ramp_time", 0.5)),
        "stable_time": float(form.get("stable_time", 2)),
        "save_time": float(form.get("save_time", 1)),
        "ramp_down": "ramp_down" in form,
    }

    timing_data = build_timing(**params)
    plot_b64 = render_timing_plot(timing_data)

    timing_params = {k: v for k, v in params.items()}
    timing_params["schedule"] = {
        "steps": timing_data["steps"],
        "ramp_down_steps": timing_data["ramp_down_steps"],
        "solve_time": timing_data["solve_time"],
        "n_steps": timing_data["n_steps"],
    }
    request.session["timing_params"] = timing_params

    if action == "next":
        return RedirectResponse("/simulate", status_code=303)

    return _render_template("timing.html", {
        "request": request,
        "page": "timing",
        "form": form,
        "plot_b64": plot_b64,
        "has_device": has_device,
    })