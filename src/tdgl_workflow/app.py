from fastapi import FastAPI
from tdgl_workflow.config import Settings


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title=settings.app_name)
    app.state.settings = settings

    from tdgl_workflow.routes.api import router as api_router

    app.include_router(api_router)

    return app
