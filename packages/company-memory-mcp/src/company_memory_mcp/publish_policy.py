"""Who may publish manually added knowledge into which shared scope.

Reading a group's documents and writing into that group are separate rights. Membership
in `project-atlas` lets a user read Atlas knowledge; it never lets them publish there.
Publishing needs an explicit grant: membership in a publisher group that the policy file
names for that scope. Publisher groups live in the IdP next to the reader groups, so a
grant or a revocation is an ordinary directory change and applies on the next request
(groups are read from userinfo per request).

The policy is a JSON file, per tenant:

    {"version": 1,
     "tenants": {
       "default": {"project-atlas": {"publisher_groups": ["project-atlas-publishers"]}},
       "door3": {"it-support": {"publisher_groups": ["it-support-publishers"],
                                "read_groups": ["everyone"]}}}}

A scope's readers are its `read_groups` (default: the group named like the scope), and the
published document's ACL grants exactly those groups. Rules (all must hold):

1. the scope is listed for the caller's tenant (unlisted scopes are never publishable);
2. the caller holds one of that scope's publisher groups;
3. the caller is currently a member of one of the scope's read groups, so nobody writes
   into a space they cannot read.

`everyone` may be a read group: it is an explicit directory group holding every
configured user (company_memory_mcp.identity), not "any request". It can never be a
scope name or a publisher group, and a publisher group can never also be a read group, so
reading a scope never grants publishing to it. The implicit tenant group, `public`, and
`*` are refused everywhere, so nothing is published to unauthenticated or unconfigured
callers. A missing policy means private knowledge only. An unreadable policy fails closed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pansoph_authz import TENANT_EVERYONE

POLICY_ENV = "CM_PUBLISH_POLICY_FILE"
_GROUP = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TENANT_WIDE = frozenset({"everyone", TENANT_EVERYONE, "public", "*"})


class PolicyError(ValueError):
    """The publish policy file is missing, malformed, or grants a tenant wide scope."""


_NEVER_READERS = frozenset({TENANT_EVERYONE, "public", "*"})


@dataclass(frozen=True)
class ScopeRule:
    publisher_groups: frozenset[str]
    read_groups: tuple[str, ...]


@dataclass(frozen=True)
class PublishDecision:
    allowed: bool
    reason: str
    read_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishPolicy:
    # tenant -> scope name -> rule
    scopes: dict[str, dict[str, ScopeRule]]

    def decide(self, *, tenant_id: str, caller_groups: list[str], scope: str) -> PublishDecision:
        rule = self.scopes.get(tenant_id, {}).get(scope)
        if rule is None:
            return PublishDecision(False, "scope is not publishable")
        held = set(caller_groups)
        if held.isdisjoint(rule.publisher_groups):
            return PublishDecision(False, "caller has no publish grant for this scope")
        if held.isdisjoint(rule.read_groups):
            return PublishDecision(False, "caller is not a reader of this scope")
        return PublishDecision(True, "publisher grant", rule.read_groups)

    def publishable_for(self, tenant_id: str, caller_groups: list[str]) -> list[str]:
        """The shared scopes this caller may publish to right now (for tool help)."""
        rules = self.scopes.get(tenant_id, {})
        return sorted(
            s
            for s in rules
            if self.decide(tenant_id=tenant_id, caller_groups=caller_groups, scope=s).allowed
        )


def parse_policy(data: object) -> PublishPolicy:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise PolicyError("publish policy must be a JSON object with version 1")
    tenants = data.get("tenants")
    if not isinstance(tenants, dict):
        raise PolicyError("publish policy needs a 'tenants' object")
    out: dict[str, dict[str, ScopeRule]] = {}
    for tenant, rules in tenants.items():
        if not isinstance(rules, dict):
            raise PolicyError(f"tenant {tenant!r}: scopes must be an object")
        scoped: dict[str, ScopeRule] = {}
        for scope, rule in rules.items():
            if scope in _TENANT_WIDE:
                raise PolicyError(f"scope {scope!r} is tenant wide and cannot be publishable")
            if not _GROUP.match(scope):
                raise PolicyError(f"bad scope group name: {scope!r}")
            if not isinstance(rule, dict):
                raise PolicyError(f"scope {scope!r}: rule must be an object")
            groups = rule.get("publisher_groups")
            if not isinstance(groups, list) or not groups:
                raise PolicyError(f"scope {scope!r} needs a non empty publisher_groups list")
            readers = rule.get("read_groups", [scope])
            if not isinstance(readers, list) or not readers:
                raise PolicyError(f"scope {scope!r}: read_groups must be a non empty list")
            for r in readers:
                if not isinstance(r, str) or not _GROUP.match(r) or r in _NEVER_READERS:
                    raise PolicyError(f"scope {scope!r}: bad read group {r!r}")
            for g in groups:
                if not isinstance(g, str) or not _GROUP.match(g) or g in _TENANT_WIDE:
                    raise PolicyError(f"scope {scope!r}: bad publisher group {g!r}")
                if g == scope or g in readers:
                    # Reader membership must never double as the write grant.
                    raise PolicyError(f"scope {scope!r}: publisher group {g!r} is also a reader")
            scoped[scope] = ScopeRule(frozenset(groups), tuple(dict.fromkeys(readers)))
        out[str(tenant)] = scoped
    return PublishPolicy(out)


def load_policy() -> PublishPolicy:
    """Read the policy named by CM_PUBLISH_POLICY_FILE. Unset means no shared scopes."""
    path = os.environ.get(POLICY_ENV, "")
    if not path:
        return PublishPolicy({})
    try:
        return parse_policy(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read the publish policy ({type(exc).__name__})") from exc
