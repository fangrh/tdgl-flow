import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    autoescape=True,
    cache_size=0,
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

    ramp_time = float(form.get("ramp_time", 0.5))
    stable_time = float(form.get("stable_time", 2))
    save_time = float(form.get("save_time", 1))

    segments_json = form.get("segments_json", "")
    if segments_json:
        segments = json.loads(segments_json)
        params = {
            "mode": "segmented",
            "segments": segments,
            "ramp_time": ramp_time,
            "stable_time": stable_time,
            "save_time": save_time,
        }
    else:
        params = {
            "mode": "simple",
            "je_initial": float(form.get("je_initial", 0)),
            "je_final": float(form.get("je_final", 5)),
            "je_step": float(form.get("je_step", 1)),
            "ramp_time": ramp_time,
            "stable_time": stable_time,
            "save_time": save_time,
            "ramp_down": "ramp_down" in form,
        }

    request.session["timing_params"] = params

    if action == "next":
        return RedirectResponse("/simulate", status_code=303)

    return _render_template("timing.html", {
        "request": request,
        "page": "timing",
        "form": form,
        "has_device": has_device,
    })
