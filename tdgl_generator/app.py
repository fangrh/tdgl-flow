import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from tdgl_data.synthetic import generate_synthetic_run


class GenerateRequest(BaseModel):
    je_min: float = -1.0
    je_max: float = 1.0
    je_count: int = 10
    frames_per_je: int = 5
    delay_seconds: float = 2.0
    grid_y: int = 72
    grid_x: int = 72


def create_app(*, data_service_url: str | None = None) -> FastAPI:
    from tdgl_generator.config import Settings

    settings = Settings()
    if data_service_url is None:
        data_service_url = settings.data_service_url

    app = FastAPI(title="TDGL Data Generator")
    app.state.data_service_url = data_service_url.rstrip("/")
    app.state.current_task: asyncio.Task | None = None
    app.state.status = "idle"
    app.state.log: list[str] = []

    @app.get("/", response_class=HTMLResponse)
    def api_index() -> HTMLResponse:
        viewer_path = Path(__file__).with_name("static") / "generator.html"
        if not viewer_path.exists():
            raise HTTPException(status_code=500, detail="Generator UI not found")
        return HTMLResponse(viewer_path.read_text(encoding="utf-8"))

    @app.post("/api/generate")
    async def api_generate(body: GenerateRequest):
        if app.state.current_task is not None and not app.state.current_task.done():
            raise HTTPException(status_code=409, detail="Generation already running")

        app.state.status = "running"
        app.state.log = [f"Starting generation: {body.je_count} Je values, {body.frames_per_je} frames each"]

        async def run_generation():
            base_url = app.state.data_service_url
            async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
                run_resp = await client.post("/api/runs", json={
                    "solver_type": "synthetic",
                    "grid_shape": [body.grid_y, body.grid_x],
                })
                run_resp.raise_for_status()
                run_id = run_resp.json()["run_id"]
                app.state.log.append(f"Created run {run_id[:8]}")

                frame_index = 0
                for je_i in range(body.je_count):
                    if app.state.current_task is None or app.state.current_task.cancelled():
                        break
                    je = body.je_min + (body.je_max - body.je_min) * je_i / max(body.je_count - 1, 1)
                    for sf in generate_synthetic_run(
                        body.frames_per_je,
                        (body.grid_y, body.grid_x),
                        seed=je_i,
                    ):
                        frame_resp = await client.post(
                            f"/api/runs/{run_id}/frames",
                            json={
                                "frame_index": frame_index,
                                "time_value": sf.time_value,
                                "je": sf.je,
                                "voltage": sf.voltage,
                                "psi_real": sf.psi_real.tolist(),
                                "psi_imag": sf.psi_imag.tolist(),
                                "mu": sf.mu.tolist(),
                            },
                        )
                        frame_resp.raise_for_status()
                        frame_index += 1

                    app.state.log.append(f"Batch {je_i + 1}/{body.je_count}: Je={je:.3f}, {frame_index} frames total")

                    if je_i < body.je_count - 1:
                        await asyncio.sleep(body.delay_seconds)

            app.state.status = "completed"
            app.state.log.append(f"Done. {frame_index} frames generated.")

        app.state.current_task = asyncio.create_task(run_generation())
        return {"status": "started", "run_id_prefix": app.state.log[-1].split()[-1] if app.state.log else ""}

    @app.post("/api/stop")
    async def api_stop():
        if app.state.current_task is not None and not app.state.current_task.done():
            app.state.current_task.cancel()
            app.state.status = "stopped"
            app.state.log.append("Generation stopped.")
            return {"status": "stopped"}
        return {"status": "idle"}

    @app.get("/api/status")
    def api_status():
        return {
            "status": app.state.status,
            "log": app.state.log[-20:],
        }

    return app