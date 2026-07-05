"""The grabber guardrail. This gates releases.

pansoph CLAUDE.md rule 5 is not negotiable: the scout browser grabber captures only
what the signed in user can already see, on an explicit click, and never bypasses
authentication, solves a CAPTCHA, or replays a credential. This suite reads the scout
source and fails the build if that invariant regresses, so a future change cannot quietly
turn the grabber into a scraper. It is static (no browser, no network), so it runs on
every PR next to the permission red team.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SCOUT = Path(__file__).resolve().parents[2] / "apps" / "scout"
_CONFIG = _SCOUT / "wxt.config.ts"
_CONTENT = _SCOUT / "entrypoints" / "content.ts"

# The only extension permissions scout is allowed to request. activeTab plus scripting is
# the consent-gated pattern (the user clicks, the extension injects into that one tab);
# storage holds local config. Anything else widens the grabber's reach.
_ALLOWED_PERMISSIONS = {"activeTab", "scripting", "storage"}

# Capabilities that would let the grabber read credentials, intercept or rewrite network
# traffic, or act without the per-capture consent click. None may appear anywhere in the
# scout source. These are the concrete shapes of a rule-5 violation.
_FORBIDDEN_TOKENS = (
    "webRequest",
    "webRequestBlocking",
    "declarativeNetRequest",
    "cookies",
    "document.cookie",
    "navigator.credentials",
    "chrome.debugger",
    '"debugger"',
    "captcha",
    "2captcha",
    "anticaptcha",
)


def _entrypoint_sources() -> list[Path]:
    return sorted((_SCOUT / "entrypoints").rglob("*.ts"))


@pytest.mark.guardrail
def test_scout_requests_only_the_allowlisted_permissions() -> None:
    # Parse the permissions array out of the WXT manifest config and assert it is a subset
    # of the allowlist. A new permission must be added here deliberately, with review.
    text = _CONFIG.read_text()
    marker = "permissions:"
    assert marker in text, "scout manifest must declare a permissions array"
    after = text.split(marker, 1)[1]
    array = after[after.index("[") + 1 : after.index("]")]
    requested = {tok.strip().strip("\"'") for tok in array.split(",") if tok.strip()}
    extra = requested - _ALLOWED_PERMISSIONS
    assert not extra, f"scout requests non-allowlisted permissions: {sorted(extra)}"


@pytest.mark.guardrail
def test_scout_captures_only_on_explicit_consent() -> None:
    # The content script must act only when the popup explicitly asks it to capture, never
    # on page load and never on its own. Pin the consent guard so a regression that starts
    # auto capturing fails here.
    text = _CONTENT.read_text()
    assert 'action !== "capture"' in text, (
        "the content script must gate on an explicit capture action (consent), not auto run"
    )
    # It reads the DOM the user already sees; it must not open its own connections.
    for banned in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon"):
        assert banned not in text, (
            f"the content script must not originate network calls ({banned}); it returns "
            "the captured text to the extension, which sends it with the user's session"
        )


@pytest.mark.guardrail
def test_scout_source_has_no_auth_bypass_or_credential_capture() -> None:
    # Scan every scout entrypoint plus the manifest config for the concrete shapes of an
    # authentication bypass, a credential capture, or a CAPTCHA solver.
    offenders: list[str] = []
    for src in [_CONFIG, *_entrypoint_sources()]:
        body = src.read_text()
        for token in _FORBIDDEN_TOKENS:
            if token in body:
                offenders.append(f"{src.relative_to(_SCOUT)}: {token!r}")
    assert not offenders, "scout must never bypass auth or capture credentials: " + "; ".join(
        offenders
    )
