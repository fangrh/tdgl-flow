from fastapi import FastAPI

from tdgl_data.app import create_app


def create_dev_app() -> FastAPI:
    return create_app(create_schema=True)
