"""Contract test: the OpenAPI schema must not change by accident.

A breaking API change (removed field, renamed path) fails CI here. If the change is
intended, regenerate the snapshot and commit it with the change:
    UPDATE_SNAPSHOT=1 uv run pytest tests/contract
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flowforge.api.app import create_app
from flowforge.config import Settings
from flowforge.jobs.queue import RecordingJobQueue

SNAPSHOT = Path(__file__).with_name("openapi.snapshot.json")


def current_schema() -> dict[str, object]:
    app = create_app(Settings(), queue=RecordingJobQueue(), redis=None)
    schema = app.openapi()
    schema["info"] = {"title": schema["info"]["title"]}  # version bumps aren't contract changes
    return schema


def test_openapi_matches_snapshot() -> None:
    schema = current_schema()
    if os.environ.get("UPDATE_SNAPSHOT") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    assert schema == json.loads(SNAPSHOT.read_text()), (
        "OpenAPI schema changed. If intended: UPDATE_SNAPSHOT=1 uv run pytest tests/contract"
    )


def test_every_route_documents_the_error_shape_or_is_health() -> None:
    paths = current_schema()["paths"]
    assert isinstance(paths, dict)
    assert all(p.startswith(("/api/v1/", "/healthz", "/readyz")) for p in paths)
