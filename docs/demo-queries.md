# Demo queries

A rich, permission-segmented corpus is seeded for the fictional company **Northwind
Labs**: a public company wiki plus six department knowledge bases (engineering,
product, finance, hr, sales, security), each a separately scoped connector instance
with its own ACL, plus a public GitHub repo. Four personas show document-level access
control in action.

Type these into **lens** (http://localhost:3000) on the **Chat** or **Search** screen,
signed in as the named user. Chat gives a cited answer; Search shows the permitted
documents directly.

## Personas and what they can see

| User / pass | Role | Groups | Can read |
|---|---|---|---|
| `alice` / `alice` | admin | engineering, product, leadership, g1 | company, engineering, product, security, github |
| `bob` / `bob` | member | sales | company, sales, github |
| `carol` / `carol` | member | finance, hr | company, finance, hr, github |
| `dave` / `dave` | connector_admin | engineering | company, engineering, security, github |

Company-wide docs are public; everything else is gated by group. Roles only gate
management actions (creating connectors, the Access screen), never what you can read.

---

## 1. The headline: same question, different permitted view

Ask the **exact same question as each user** on the Chat screen:

> **"Summarize our confidential 2026 product roadmap."**

| User | Result |
|---|---|
| **alice** | Answers with the roadmap (Q3 GA + assistant, Q4 usage-based pricing and marketplace), **with a citation** to `product://roadmap-2026`. |
| **bob** | Honestly says it has no such source. Bob is in sales, not product. |
| **carol** | Same: no access. Finance/HR, not product. |
| **dave** | Same: no access. Engineering, not product. |

Only alice is in the `product` group, so for everyone else the roadmap never enters
the candidate set. This is the engine refusing to return a document, not a hidden
button.

---

## 2. Per-persona: rich answers from your own departments

**alice** (engineering + product + leadership):
- "Walk me through the May 2026 database outage postmortem and its action items."
- "What is the architecture of the platform and which services own what?"
- "What is our pricing strategy and what did Q2 user research conclude?"
- "Summarize the 2026 penetration test findings." (security, via engineering)

**bob** (sales):
- "What is in our Q3 sales pipeline and who are the top accounts?"
- "How should I pitch the permission story to a security-conscious buyer?"
- "What did the Q2 win-loss analysis find?"

**carol** (finance + people):
- "Summarize the Q2 financials and the 2026 budget."
- "What are our compensation bands and the 2026 hiring plan?"
- "What's in the 2026 benefits package?"

**dave** (engineering, connector_admin):
- "Explain the on-call runbook and the release process."
- "What are our coding standards and how do releases get promoted?"
- "Summarize the quarterly access review and the security policy." (security, via engineering)

**everyone** (public company wiki):
- "What are the company holidays for 2026 and the core working hours?"
- "What are Northwind's values?"
- "Where are the offices and how does hot-desking work?"

---

## 3. RBAC proof: the negative queries (the convincing part)

Ask a question that belongs to a **department the user is not in**. The answer is an
honest "the sources do not contain that" because the document is filtered out at
retrieval, not just hidden in the UI.

| Signed in as | Ask | Expected |
|---|---|---|
| **bob** | "What are the employee compensation bands?" | No access (HR) |
| **bob** | "What's on the confidential product roadmap?" | No access (product) |
| **carol** | "What is in the Q3 sales pipeline?" | No access (sales) |
| **carol** | "Summarize the database outage postmortem." | No access (engineering) |
| **dave** | "What is the 2026 revenue forecast?" | No access (finance) |
| **dave** | "What are the top sales accounts?" | No access (sales) |
| **bob** | "Summarize the penetration test findings." | No access (security) |

Run the same question as alice (for the cross-cutting ones she can see) to show the
contrast immediately.

---

## 4. The Access screen: prove who-can-see-what live (alice only)

On the **Access** screen, in the **Check access** box:

| User | Groups | Query | Result |
|---|---|---|---|
| `bob` | `sales` | confidential roadmap | nothing |
| `alice` | `product` | confidential roadmap | the roadmap doc appears |
| `carol` | `finance, hr` | compensation bands | the HR doc appears |
| `carol` | `finance, hr` | sales pipeline | nothing |

This runs the same permission-enforced retrieval path as chat, so an admin can audit
access for any identity without impersonating them.

The same screen shows the **connector instances grouped by scope** (organization /
group / individual), making the multi-instance, per-department model visible at a
glance.

---

## 5. Quick command-line check (no UI)

```bash
tok() { curl -s -X POST http://localhost:8090/realms/pansoph/protocol/openid-connect/token \
  -d grant_type=password -d client_id=lens -d "username=$1" -d "password=$1" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }

for u in alice bob carol dave; do T=$(tok $u);
  curl -s -X POST localhost:8080/api/search -H "authorization: Bearer $T" \
    -H 'content-type: application/json' \
    -d '{"query":"confidential 2026 product roadmap","final_k":60}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['results'];print('$u roadmap =', any('roadmap' in r['doc_id'] for r in d))"; done
# expect: alice True, bob/carol/dave False
```
