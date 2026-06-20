"""Tests for VCR config and scrubbing."""

from __future__ import annotations

from pansoph_connector_sdk.verify.fixtures import scrub_response, vcr_config


def test_vcr_config_filters_secrets() -> None:
    cfg = vcr_config()
    assert "authorization" in [h.lower() for h in cfg["filter_headers"]]
    assert cfg["record_mode"] == "none"


def test_scrub_response_redacts_known_token_fields() -> None:
    response = {"body": {"string": b'{"access_token": "supersecret", "name": "ok"}'}}
    scrubbed = scrub_response(response)
    assert b"supersecret" not in scrubbed["body"]["string"]
    assert b"ok" in scrubbed["body"]["string"]
