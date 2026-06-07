# scout

The pansoph browser grabber. The answer for sources that have neither an API nor an MCP server.

scout ingests only what the signed in user can already see, with explicit consent, and posts it to agora. It is the Tier 3 connector.

## Hard guardrails (enforced in code and tested, task #12)

- Never bypasses authentication. It captures the page the user is already viewing.
- Never replays credentials and never solves anti bot or CAPTCHA challenges.
- Honors robots directives and rate limits.
- Capture is explicit and consented, never silent background scraping.

This line is informed by current case law, which protects public data access but treats gated content and fake accounts as the red line.

## Stack (scaffolded in task #12)

- WXT, Manifest V3, React.
- Readability based content extraction.
- A consent first capture UX.
- Posts captured content to agora's ingestion endpoint, tagged to a connector instance.
