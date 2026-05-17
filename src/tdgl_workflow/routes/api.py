import httpx
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



@router.get("/cluster/resources")
async def cluster_resources():
    """Query Kubernetes API for node allocatable resources."""
    try:
        import json as _json
        with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as f:
            token = f.read().strip()
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            namespace = f.read().strip()

        resp = httpx.get(
            "https://kubernetes.default.svc/api/v1/nodes",
            headers={"Authorization": f"Bearer {token}"},
            verify="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
            timeout=5.0,
        )
        resp.raise_for_status()
        nodes = resp.json().get("items", [])

        total_cpu = 0
        total_memory_ki = 0
        for node in nodes:
            alloc = node.get("status", {}).get("allocatable", {})
            cpu_str = alloc.get("cpu", "0")
            # Parse CPU: could be "12" or "1200m"
            if cpu_str.endswith("m"):
                total_cpu += int(cpu_str[:-1]) / 1000
            else:
                total_cpu += int(cpu_str)
            mem_str = alloc.get("memory", "0Ki")
            if mem_str.endswith("Ki"):
                total_memory_ki += int(mem_str[:-2])

        return JSONResponse({
            "cpu_cores": round(total_cpu, 1),
            "memory_gb": round(total_memory_ki / 1024 / 1024, 1),
            "nodes": len(nodes),
        })
    except Exception as e:
        return JSONResponse({"cpu_cores": 12, "memory_gb": 31, "nodes": 1, "error": str(e)})
