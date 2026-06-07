# scout

The pansoph browser grabber. The answer for sources that have neither an API nor an MCP server.

scout ingests only what the signed in user can already see, with explicit consent, and posts it to agora. It is the Tier 3 connector.

## Hard guardrails (enforced in code and tested, task #12)

- Never bypasses authentication. It captures the page the user is already viewing.
- Never replays credentials and never solves anti bot or CAPTCHA challenges.
- Honors robots directives and rate limits.
- Capture is explicit and consented, never silent background scraping.

This line is informed by current case law, which protects public data access but treats gated content and fake accounts as the red line.

## Stack (implemented)

- WXT, Manifest V3, vanilla TypeScript (no framework, smallest surface).
- Readability based content extraction in a content script that only acts on an explicit capture message.
- A consent first popup: the user sets the agora URL, tenant, groups to share with, and public flag, then clicks Capture.
- Posts the captured page to agora's `POST /api/grab`, which runs the same indexing path as a connector sync, so the page is immediately retrievable under the ACL the popup set.

Build with `pnpm build` (output in `.output/chrome-mv3`), load unpacked in the browser.
