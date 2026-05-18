from viewer_manager.app import create_app


def create_dev_app():
    return create_app(create_schema=True)
