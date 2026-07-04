"""OAuth provider instances, one module per provider.

Each module exports a `PROVIDER` (an `OAuthProvider`). The registry collects them
by name. Provider modules are otherwise independent, so a follow-up agent can flesh
one out without touching the others. No em dashes anywhere.
"""

from __future__ import annotations
