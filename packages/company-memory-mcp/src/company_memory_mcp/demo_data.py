"""The synthetic demo's expected permission matrix: one source of truth for the hermetic
tests and the live `company-memory verify`. Fictional users, groups, and documents only.

Group ACLs in the corpus are a union of grants (pansoph has no intersection), so a
document "for engineering on Project Atlas" is granted to `project-atlas`, whose members
are Atlas engineers. Membership in `engineering` alone does not reach it.
"""

from __future__ import annotations

INSTANCE = "synthetic"
CONNECTOR = "cm_synthetic"
TENANT = "default"
EMAIL_DOMAIN = "company-memory.test"

DEMO_QUERY = "What do you know about current project budgets and architecture decisions?"
REVOCATION_QUERY = "What authentication architecture does Project Atlas use?"
REVOCATION_USER, REVOCATION_GROUP = "alice", "project-atlas"

# Baseline directory groups (the Keycloak realm and `company-memory seed` restore these).
# `*-publishers` groups are write grants for manually added knowledge (publish-policy.json);
# they grant no reads.
GROUPS: dict[str, list[str]] = {
    "alice": ["everyone", "engineering", "project-atlas", "project-atlas-publishers"],
    "bob": ["everyone", "engineering", "project-beacon", "project-beacon-publishers"],
    "carol": ["everyone", "leadership"],
    "dave": ["everyone", "people-strategy"],
}
ALL_GROUPS = [
    "everyone",
    "engineering",
    "leadership",
    "people-strategy",
    "project-atlas",
    "project-beacon",
    "project-atlas-publishers",
    "project-beacon-publishers",
    "engineering-publishers",
]

PUBLIC = {"engineering-guidelines", "it-onboarding", "project-directory"}
EXPECTED: dict[str, set[str]] = {
    "alice": PUBLIC
    | {
        "atlas-adr-007-authentication",
        "atlas-architecture-overview",
        "itops-runbook-database-failover",
        "itops-known-infrastructure-issues",
    },
    "bob": PUBLIC
    | {
        "beacon-postmortem-inc-4471",
        "beacon-architecture-overview",
        "itops-runbook-database-failover",
        "itops-known-infrastructure-issues",
    },
    "carol": PUBLIC | {"leadership-fy27-financial-plan"},
    "dave": PUBLIC | {"people-capability-plan", "itops-runbook-database-failover"},
}

# Distinctive phrases that appear only in restricted documents.
MARKERS: dict[str, list[str]] = {
    "atlas": ["ATLAS ADR-007", "PASSKEY-FIRST", "ATLAS-ARCH-OVERVIEW", "ADR-003"],
    "beacon": ["INC-4471", "BEACON-ARCH-OVERVIEW", "REPLICATION LAG", "ADR-002"],
    "leadership": ["7.3M", "ORION BUDGET", "2.1M", "1.4M"],
    "people": ["DATA-PLATFORM GUILD", "CAPABILITY PLAN"],
    "eng_only": ["ITOPS-KNOWN-ISSUES"],
    "runbook": ["RUNBOOK RB-12 SHARED"],
}
FORBIDDEN: dict[str, list[str]] = {
    "alice": MARKERS["beacon"] + MARKERS["leadership"] + MARKERS["people"],
    "bob": MARKERS["atlas"] + MARKERS["leadership"] + MARKERS["people"],
    "carol": MARKERS["atlas"]
    + MARKERS["beacon"]
    + MARKERS["people"]
    + MARKERS["eng_only"]
    + MARKERS["runbook"],
    "dave": MARKERS["atlas"] + MARKERS["beacon"] + MARKERS["leadership"] + MARKERS["eng_only"],
}


def doc_id(stem: str) -> str:
    return f"{CONNECTOR}:{INSTANCE}:{stem}"


def stem_of(source_id: str) -> str:
    return source_id.rsplit(":", 1)[-1]


def email(user: str) -> str:
    return f"{user}@{EMAIL_DOMAIN}"


# Manually added knowledge (the MCP write tool). The live demo adds this file through
# Claude Code; `company-memory verify` adds its own probe documents and deletes them.
KNOWLEDGE_FILE = "deploy/company-memory/demo-input/atlas-authentication.md"
KNOWLEDGE_QUERY = "What authentication technology does Project Atlas use and why?"
KNOWLEDGE_MARKERS = ["HARBOR-KEY", "atlas-field-users-harbor", "ADR-012"]
# The demo document is published to project-atlas: nobody else may see its markers.
for _user in ("bob", "carol", "dave"):
    FORBIDDEN[_user] = FORBIDDEN[_user] + KNOWLEDGE_MARKERS
