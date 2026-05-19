import json as _json
import uuid as _uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from tdgl_workflow.config import Settings
from tdgl_workflow.mesh import build_rectangular_device
from tdgl_workflow.timing import build_timing, build_timing_segmented

router = APIRouter(prefix="/api")

SOLVER_WORKFLOWS = {
    "cpp-tdgl": "cpp-tdgl-sim",
    "py-tdgl": "py-tdgl-sim",
}


def workflow_template_for_solver(solver_type: str) -> str:
    try:
        return SOLVER_WORKFLOWS[solver_type]
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unsupported solver_type: {solver_type}") from None


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



@router.get("/runs")
async def list_runs(request: Request):
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{settings.data_service_url}/api/runs")
        return JSONResponse(resp.json(), status_code=resp.status_code)


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, request: Request):
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{settings.data_service_url}/api/runs/{run_id}")
        return Response(status_code=resp.status_code)


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


@router.post("/device/build")
async def device_build(request: Request):
    return await preview_mesh(request)


@router.post("/timing/build")
async def timing_build(request: Request):
    return await preview_timing(request)


@router.post("/workflows/submit")
async def submit_workflow(request: Request):
    body = await request.json()
    settings: Settings = request.app.state.settings

    solver_type = body.get("solver_type", "cpp-tdgl")
    workflow_template = workflow_template_for_solver(solver_type)

    device_params = body.get("device_params", {})
    timing_params = body.get("timing_params", {})
    mesh_data = body.get("mesh_data", {})
    schedule = body.get("schedule", {})
    solver_options = body.get("solver_options", {})
    resources = body.get("resources", {"cpu_cores": 2, "memory_mib": 2048})

    n_sites = mesh_data.get("num_sites", len(mesh_data.get("sites", [])))
    mesh_sites = mesh_data.get("sites")
    mesh_elements = mesh_data.get("elements")

    async with httpx.AsyncClient(timeout=30.0) as client:
        create_resp = await client.post(
            f"{settings.data_service_url}/api/runs",
            json={
                "solver_type": solver_type,
                "n_sites": n_sites,
                "device_params": device_params,
                "timing_params": timing_params,
                "mesh_sites": mesh_sites,
                "mesh_elements": mesh_elements,
                "solver_options": solver_options,
                "total_frames": schedule.get("n_steps", 0),
            },
        )
        create_resp.raise_for_status()
        created_run = create_resp.json()
        actual_run_id = created_run["run_id"]

    workflow = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "generateName": f"{solver_type}-{actual_run_id[:8]}-",
            "namespace": settings.tdgl_namespace,
            "labels": {"run-id": actual_run_id},
        },
        "spec": {
            "workflowTemplateRef": {"name": workflow_template},
            "arguments": {
                "parameters": [
                    {"name": "run-id", "value": actual_run_id},
                    {"name": "data-service-url", "value": settings.data_service_url},
                    {"name": "device-params-json", "value": _json.dumps(device_params)},
                    {"name": "timing-params-json", "value": _json.dumps(timing_params)},
                    {"name": "solver-options-json", "value": _json.dumps(solver_options)},
                    {"name": "cpu", "value": str(resources.get("cpu_cores", 2))},
                    {"name": "memory", "value": f"{resources.get('memory_gb', resources.get('memory_mib', 2048) / 1024)}Gi"},
                    {"name": "dev-mode", "value": "true"},
                ],
            },
        },
    }

    workflow_name = None
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as argo_client:
            argo_resp = await argo_client.post(
                f"{settings.argo_server_url}/api/v1/workflows/{settings.tdgl_namespace}",
                json={"workflow": workflow},
                headers={"Content-Type": "application/json"},
            )
            if argo_resp.status_code < 300:
                workflow_name = argo_resp.json()["metadata"]["name"]
    except httpx.HTTPError:
        pass

    return JSONResponse({
        "run_id": actual_run_id,
        "workflow_name": workflow_name,
        "status": "created",
    })


@router.post("/viewer-sessions")
async def create_viewer_session(request: Request):
    body = await request.json()
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{settings.viewer_manager_url}/api/viewer-sessions",
            json=body,
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)


@router.get("/viewer-sessions/{session_id}")
async def get_viewer_session(session_id: str, request: Request):
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{settings.viewer_manager_url}/api/viewer-sessions/{session_id}",
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)


@router.post("/viewer-sessions/{session_id}/heartbeat")
async def heartbeat_viewer_session(session_id: str, request: Request):
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{settings.viewer_manager_url}/api/viewer-sessions/{session_id}/heartbeat",
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)


@router.post("/viewer-sessions/{session_id}/release")
async def release_viewer_session(session_id: str, request: Request):
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{settings.viewer_manager_url}/api/viewer-sessions/{session_id}/release",
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)


@router.delete("/viewer-sessions/{session_id}")
async def delete_viewer_session(session_id: str, request: Request):
    settings: Settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.delete(
            f"{settings.viewer_manager_url}/api/viewer-sessions/{session_id}",
        )
        return JSONResponse(status_code=resp.status_code)
