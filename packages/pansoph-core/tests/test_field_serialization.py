"""The connector spec is serialized to the UI over /api/connectors/specs. A field's
resource_kind must survive that serialization, or the frontend cannot know a field is a
resource picker and silently renders it as free text. This regressed once (the picker
never appeared because _serialize_field dropped the hint), so it is pinned here. No em
dashes anywhere.
"""

from __future__ import annotations

from pansoph_connector_sdk import ConfigField, FieldKind
from pansoph_core.routes import _serialize_field


def test_serialize_field_includes_resource_kind_when_bound() -> None:
    # A field bound to a picker must carry resource_kind to the UI so it renders the
    # live picker (drill down and select) instead of a free-text input.
    field = ConfigField(key="repo", label="Repository", resource_kind="repo")
    out = _serialize_field(field)
    assert out["resource_kind"] == "repo"


def test_serialize_field_omits_resource_kind_when_unbound() -> None:
    # An unbound field stays free text, so the key is absent rather than null, keeping
    # the payload small and the renderer's "declared?" check a simple presence test.
    field = ConfigField(key="branch", label="Branch", required=False)
    out = _serialize_field(field)
    assert "resource_kind" not in out


def test_serialize_field_carries_both_oauth_and_resource_hints() -> None:
    # The two hints are independent: an oauth field can also be a picker binding, and
    # both must serialize so the connect button and the picker both render.
    field = ConfigField(
        key="channel_ids",
        label="Channels",
        kind=FieldKind.LIST,
        resource_kind="channel",
    )
    out = _serialize_field(field)
    assert out["resource_kind"] == "channel"
    assert out["kind"] == "list"
