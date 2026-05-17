from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.timing import build_timing, build_timing_segmented

router = APIRouter(prefix="/api")


@router.post("/preview/mesh")
async def preview_mesh(request: Request):
    form = await request.json()
    mesh_data = build_rectangular_device(
        film_width=float(form["film_width"]),
        film_height=float(form["film_height"]),
        elec_width=float(form["elec_width"]),
        elec_height=float(form["elec_height"]),
        elec_y_offset=float(form["elec_y_offset"]),
        probe_points=[tuple(p) for p in form["probe_points"]],
        max_edge_length=float(form["max_edge_length"]),
        smooth=int(form["smooth"]),
    )
    return JSONResponse(mesh_data)


@router.post("/preview/timing")
async def preview_timing(request: Request):
    form = await request.json()
    mode = form.get("mode", "simple")

    if mode == "segmented":
        segments = [
            {
                "je_initial": float(s["je_initial"]),
                "je_final": float(s["je_final"]),
                "je_step": float(s["je_step"]),
            }
            for s in form.get("segments", [])
        ]
        timing_data = build_timing_segmented(
            segments=segments,
            ramp_time=float(form["ramp_time"]),
            stable_time=float(form["stable_time"]),
            save_time=float(form["save_time"]),
        )
    else:
        timing_data = build_timing(
            je_initial=float(form["je_initial"]),
            je_final=float(form["je_final"]),
            je_step=float(form["je_step"]),
            ramp_time=float(form["ramp_time"]),
            stable_time=float(form["stable_time"]),
            save_time=float(form["save_time"]),
            ramp_down=form.get("ramp_down", False),
        )

    return JSONResponse(timing_data)
