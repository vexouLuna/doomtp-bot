import json
import re

from doomtp_bot.api.app import create_app
from doomtp_bot.core.health import HealthRegistry

# The route docstrings become the descriptions in /openapi.json and /docs. Readers of those have no
# ADRs or architecture document, so references to them belong in a `#` comment above the route.
INTERNAL = re.compile(r"ADR-\d+|architecture §")


def test_the_openapi_schema_cites_no_internal_documents() -> None:
    schema = json.dumps(create_app(HealthRegistry()).openapi(), ensure_ascii=False)
    assert INTERNAL.findall(schema) == []


def test_the_typed_v1_routes_have_response_schemas() -> None:
    paths = create_app(HealthRegistry()).openapi()["paths"]
    typed = [
        ("/api/v1/channels", "get"),
        ("/api/v1/channels/{login}", "get"),
        ("/api/v1/channels/{login}", "patch"),
        ("/api/v1/channels/{login}/runs", "get"),
        ("/api/v1/channels/{login}/messages", "get"),
        ("/api/v1/channels/{login}/log", "get"),
        ("/api/v1/channels/{login}/log/coverage", "get"),
        ("/api/v1/audit", "get"),
    ]
    for path, method in typed:
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema, (path, method)
