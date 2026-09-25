"""ASGI entrypoint: ``uvicorn flowforge.main:app``."""

from flowforge.api.app import create_app
from flowforge.config import get_settings
from flowforge.observability.logging import configure_logging

_settings = get_settings()
configure_logging(_settings.log_level, json=_settings.log_json)
app = create_app(_settings)
