"""ASGI entry point: `uvicorn hub.api.main:app`."""

from __future__ import annotations

from hub import log
from hub.api.app import create_app
from hub.config import get_settings

settings = get_settings()
log.configure(settings.log_level)
app = create_app(settings)
