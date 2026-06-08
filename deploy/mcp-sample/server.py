"""A sample MCP server exposing a rich, fictional company knowledge base.

It exists so the MCP bridge connector can be demonstrated end to end without an
external MCP server, with enough breadth to show document-level access control across
departments. The pansoph MCP bridge calls resources/list and resources/read; this
server answers them. Run inside the stack; the bridge points at
http://mcp-sample:8000/mcp and confines each instance to one URI scheme (department)
via resource_uri_prefix.

Everything here is fictional content for "Northwind Labs", a made-up B2B analytics
company. No real data.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("northwind-knowledge", host="0.0.0.0", port=8000)

# uri -> document text. The URI scheme is the department; a connector instance is
# confined to one scheme and given that department's ACL, so retrieval is naturally
# segmented by group. Public (company-wide) docs everyone can read.
DOCS: dict[str, str] = {
    # --- company:// : public, every employee ---
    "company://handbook": (
        "Northwind Labs Employee Handbook. We are a remote-first B2B analytics company "
        "of about 180 people across three hubs. Core working hours are 10:00-15:00 in "
        "your local timezone. All-hands is the first Thursday of each month. Expense "
        "anything under $100 without approval. Be kind, write things down, and default "
        "to transparency for everything that is not access-controlled."
    ),
    "company://holidays-2026": (
        "Northwind Labs 2026 holiday calendar. Company holidays: Jan 1, Feb 16, May 25, "
        "Jul 3, Sep 7, Nov 26-27, Dec 24-25, Dec 31. In addition every employee gets a "
        "floating week of winter shutdown from Dec 28. Unlimited PTO with a four week "
        "minimum that managers actively encourage."
    ),
    "company://all-hands-2026-06": (
        "All-hands notes, June 2026. Revenue is tracking ahead of plan, the new search "
        "product enters beta this quarter, and we are hiring across engineering and "
        "sales. Leadership reaffirmed the shift to a usage-based pricing model later in "
        "the year. Q&A covered the office reopening and the new benefits package."
    ),
    "company://values": (
        "Northwind Labs values. 1) Customers can only ever see what they are allowed "
        "to. 2) Write the doc before the code. 3) Small reversible steps over big bets. "
        "4) Disagree, decide, commit. 5) Own the outcome, not the task."
    ),
    "company://it-helpdesk": (
        "IT help desk. Hardware refresh every three years. Use the SSO portal for all "
        "internal apps; never reuse passwords. Report phishing to security@. Laptops are "
        "encrypted and managed; do not install unmanaged software on them. VPN is only "
        "needed for the lab environment."
    ),
    "company://office-locations": (
        "Office locations. Northwind Labs maintains collaboration spaces in Lisbon, "
        "Austin, and Singapore, open Tuesday through Thursday. Desks are hot-desks and "
        "booked through the office app. Most of the company works fully remote."
    ),
    # --- eng:// : engineering ---
    "eng://architecture-overview": (
        "Engineering architecture overview. The platform is a set of independently "
        "deployable services behind an API gateway: ingestion, the query service, the "
        "authorization service, and the web app. Postgres is the system of record, with "
        "a vector index for semantic search and an event bus for async work. Everything "
        "is multi-tenant with row level security."
    ),
    "eng://oncall-runbook": (
        "On-call runbook. Primary on-call rotates weekly; secondary backs them up. Page "
        "severity: SEV1 customer-down, SEV2 degraded, SEV3 internal. First steps: check "
        "the status dashboard, the deploy log, and the error budget. Roll back first, "
        "investigate second. File a postmortem within 48 hours for any SEV1 or SEV2."
    ),
    "eng://postmortem-2026-05-db-outage": (
        "Postmortem: May 2026 database outage. A migration held a long lock on the "
        "tenants table during peak traffic, causing a 22 minute partial outage. Root "
        "cause: missing lock-timeout and no online-migration tooling. Action items: "
        "adopt expand-contract migrations, set statement timeouts, and add a pre-deploy "
        "lock check. No data was lost."
    ),
    "eng://service-catalog": (
        "Service catalog. ingestion-svc (owns connectors and the sync pipeline), "
        "query-svc (hybrid retrieval and reranking), authz-svc (the permission engine "
        "and ACL sync), web-app (the customer UI), and billing-svc. Each service has an "
        "owner team, an SLO, and a runbook linked here."
    ),
    "eng://coding-standards": (
        "Coding standards. Trunk-based development, every change behind a flag, no "
        "long-lived branches. Tests are required for the permission path and gate the "
        "release. Prefer boring technology. Public APIs need a design doc. Lint and "
        "format run in CI; no merge without a green permission red-team suite."
    ),
    "eng://release-process": (
        "Release process. Continuous deploy to staging on merge, promotion to "
        "production is one click behind the on-call. Database changes are expand-"
        "contract only. Every release is signed and pinned by digest. Roll back is "
        "always available and is the default response to a regression."
    ),
    # --- product:// : product (the confidential headline lives here) ---
    "product://roadmap-2026": (
        "CONFIDENTIAL product roadmap, 2026. Q3: ship Search v2 to general availability "
        "and launch the permission-aware assistant. Q4: usage-based pricing, the "
        "self-serve onboarding flow, and the partner marketplace. H1 2027 exploration: "
        "on-device models and an offline mode. Do not share outside the product and "
        "leadership groups."
    ),
    "product://prd-search-v2": (
        "PRD: Search v2. Goal: cut time-to-answer in half by combining hybrid retrieval "
        "with inline citations that deep-link to the exact passage. Must respect "
        "document-level permissions at query time. Success metric: answer acceptance "
        "rate above 70 percent on the design-partner corpus."
    ),
    "product://competitive-analysis": (
        "Competitive analysis. Incumbents do permission-aware retrieval well but paywall "
        "it; the permissive open tools treat access control as an afterthought. Our "
        "wedge is free, built-in, document-level access control plus a connector model "
        "that compounds. Watch the fast-moving open connectors projects on breadth."
    ),
    "product://pricing-experiments": (
        "Pricing experiments. Testing a usage-based model priced on indexed documents "
        "and queries, with a generous free self-host tier. Early signal: teams convert "
        "when permission sync is included rather than gated. Avoid per-seat pricing, it "
        "punishes the broad rollout we want."
    ),
    "product://user-research-q2": (
        "User research, Q2. Across twelve design-partner interviews the deciding feature "
        "was provable permission enforcement, not retrieval quality or latency. Admins "
        "want a who-can-see-what audit view. Users want citations they can actually "
        "open. Onboarding a connector is the biggest drop-off point today."
    ),
    # --- finance:// : finance ---
    "finance://q2-financials": (
        "Q2 financial summary (confidential). Revenue grew 31 percent quarter over "
        "quarter, gross margin held at 78 percent, and net retention is 124 percent. "
        "Cash runway is 26 months at the current burn. Largest cost lines are payroll "
        "and cloud infrastructure."
    ),
    "finance://budget-2026": (
        "2026 budget (confidential). Headcount investment weighted to engineering and "
        "sales. Cloud spend capped with committed-use discounts. A new line for security "
        "tooling and a third-party audit. Marketing held flat pending the pricing change."
    ),
    "finance://vendor-contracts": (
        "Vendor contracts. Cloud commit renews in October; renegotiate before the search "
        "launch raises usage. Observability and the data warehouse are up for review. "
        "Consolidate overlapping SaaS subscriptions flagged in the Q1 spend audit."
    ),
    "finance://revenue-forecast": (
        "Revenue forecast (confidential). Base case assumes the usage-based pricing ships "
        "in Q4 with a two quarter ramp. Upside case adds the partner marketplace. "
        "Downside case holds current pricing and slower enterprise close rates."
    ),
    "finance://expense-policy": (
        "Expense policy. Anything under $100 needs no pre-approval. Travel books through "
        "the corporate tool. Home-office stipend renews annually. Submit within 30 days; "
        "anything older needs a manager note. No alcohol on client entertainment without "
        "VP sign-off."
    ),
    # --- hr:// : people / hr ---
    "hr://compensation-bands": (
        "Compensation bands (confidential). Five engineering levels from associate to "
        "principal, with transparent bands and a single global pay scale adjusted for "
        "two cost-of-labor zones. Equity refreshes annually for strong performers. Bands "
        "are reviewed each January against market data."
    ),
    "hr://hiring-plan-2026": (
        "2026 hiring plan (confidential). 24 net new roles: 11 engineering, 6 sales, 3 "
        "product, 2 finance, 2 people. Priority backfills on the on-call rotation and the "
        "enterprise sales team. Structured interviews with a work-sample stage; no "
        "whiteboard trivia."
    ),
    "hr://benefits-2026": (
        "2026 benefits. Full remote stipend, health coverage in all hub countries, 16 "
        "weeks parental leave for all parents, a learning budget, and the unlimited PTO "
        "with a four week minimum. New this year: a mental-health benefit and a "
        "sabbatical after four years."
    ),
    "hr://performance-reviews": (
        "Performance review process (confidential). Twice yearly, calibrated across "
        "managers, based on impact and the company values. No stack ranking. Promotions "
        "are decided in a cross-team committee with a written case. Feedback is "
        "continuous; the review should hold no surprises."
    ),
    "hr://headcount": (
        "Headcount snapshot (confidential). 180 employees: 78 engineering, 30 sales, 20 "
        "product and design, 14 customer success, 12 G&A, 26 across finance, people, and "
        "operations. Attrition is below industry average. Two open leadership searches."
    ),
    # --- sales:// : sales ---
    "sales://pipeline-q3": (
        "Q3 sales pipeline (confidential). 4.2 million in weighted pipeline, led by three "
        "enterprise deals in security review. Permission enforcement is the recurring "
        "requirement that unblocks procurement. Forecast call is every Monday."
    ),
    "sales://top-accounts": (
        "Top accounts. The largest three are a fintech, a healthcare network, and a "
        "government contractor, all of whom require document-level access control and an "
        "audit trail before rollout. Each has an executive sponsor and a named success "
        "manager."
    ),
    "sales://playbook": (
        "Sales playbook. Lead with the permission story: a user can only ever retrieve "
        "what they are already allowed to see. Demo the who-can-see-what check live. "
        "Qualify on number of sources and whether they self-host. Avoid competing on raw "
        "connector count; compete on access control and reach."
    ),
    "sales://win-loss-q2": (
        "Win-loss analysis, Q2. Wins cited built-in permission sync and self-hosting. "
        "Losses cited connector breadth and the missing SSO integrations, both on the "
        "roadmap. Two losses reopened after the OIDC login shipped."
    ),
    "sales://partnerships": (
        "Partnerships (confidential). Early talks with two systems integrators to resell "
        "the self-hosted edition, and a marketplace listing planned alongside the Q4 "
        "pricing change. Co-selling motion to be defined with the partner marketplace."
    ),
    # --- security:// : restricted to leadership + engineering ---
    "security://incident-response-plan": (
        "Incident response plan (confidential). Severities, the on-call security "
        "rotation, and the escalation path to leadership. Customer notification within "
        "the contractual window. Evidence preservation first. Post-incident review is "
        "blameless and tracked to completion."
    ),
    "security://access-review-q2": (
        "Quarterly access review, Q2 (confidential). All production access re-attested; "
        "three stale grants revoked. Least-privilege confirmed for the data stores. "
        "Action: move the last shared service account to short-lived scoped tokens."
    ),
    "security://pentest-findings-2026": (
        "Penetration test findings, 2026 (confidential). No critical issues. Two highs "
        "fixed: a verbose error path and a missing rate limit on an admin endpoint. "
        "Retest passed. Full report shared with leadership and the engineering security "
        "owners only."
    ),
    "security://security-policy": (
        "Security policy. SSO everywhere, encryption at rest and in transit, signed and "
        "digest-pinned images, an allowlisted egress, and an immutable audit log of every "
        "admin and connector action. Untrusted connector code runs sandboxed, never in "
        "the core."
    ),
}


def _slug(uri: str) -> str:
    return uri.replace("://", "_").replace("/", "_").replace("-", "_")


def _register(uri: str, text: str) -> None:
    @mcp.resource(uri, name=_slug(uri), description=uri)
    def _resource() -> str:
        return text


for _uri, _text in DOCS.items():
    _register(_uri, _text)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
