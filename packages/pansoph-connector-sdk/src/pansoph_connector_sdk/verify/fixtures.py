"""Helpers for hermetic HTTP replay and secret scrubbing.

vcr_config returns a config dict for pytest-recording / vcrpy that filters auth
material and replays offline (record_mode none). scrub_response redacts known
secret-bearing JSON fields from a recorded response body before it is committed.
Tests that prefer hand-authored fixtures use respx instead and do not need this.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SECRET_FIELDS = ("access_token", "refresh_token", "token", "client_secret", "api_key")
_REDACTED = "REDACTED"


def vcr_config() -> dict[str, Any]:
    return {
        "record_mode": "none",
        "filter_headers": ["authorization", "Authorization", "cookie", "Cookie"],
        "filter_query_parameters": ["token", "key", "access_token"],
        "before_record_response": scrub_response,
    }


def scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body", {})
    raw = body.get("string")
    if isinstance(raw, bytes):
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            body["string"] = _scrub_text(raw)
            return response
        body["string"] = json.dumps(_scrub_obj(data)).encode("utf-8")
    return response


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if k.lower() in _SECRET_FIELDS else _scrub_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    return obj


def _scrub_text(raw: bytes) -> bytes:
    text = raw.decode("utf-8", "replace")
    for field in _SECRET_FIELDS:
        text = re.sub(rf'("{field}"\s*:\s*)"[^"]*"', rf'\1"{_REDACTED}"', text)
    return text.encode("utf-8")
