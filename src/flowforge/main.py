"""ASGI entrypoint: ``uvicorn flowforge.main:app``."""

from flowforge.api.app import create_app

app = create_app()
