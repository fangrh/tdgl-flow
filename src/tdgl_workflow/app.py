from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware
from fastapi import FastAPI
from tdgl_workflow.config import Settings


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title=settings.app_name)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.state.settings = settings

    from tdgl_workflow.routes.device import router as device_router
    from tdgl_workflow.routes.timing import router as timing_router
    from tdgl_workflow.routes.simulate import router as simulate_router

    app.include_router(device_router)
    app.include_router(timing_router)
    app.include_router(simulate_router)

    @app.get("/")
    def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/device")

    return app