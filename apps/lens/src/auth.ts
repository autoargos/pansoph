import { type User, UserManager } from "oidc-client-ts";
import { useEffect, useState } from "react";

import { setAuthToken, setUnauthorizedHandler } from "./api.ts";

// OIDC against the hosted Keycloak. The IdP is swappable; this only knows the issuer
// and client id, both pointing at the demo realm.
const OIDC_AUTHORITY = "http://localhost:8090/realms/pansoph";

const manager = new UserManager({
  authority: OIDC_AUTHORITY,
  client_id: "lens",
  redirect_uri: `${window.location.origin}/`,
  post_logout_redirect_uri: `${window.location.origin}/`,
  response_type: "code",
  scope: "openid profile email",
  // Keycloak access tokens are short lived (minutes). Renew them silently via the refresh
  // token before they expire, so an open session does not start 401ing on every action
  // (which made sync silently do nothing and connectors show "Connect" when already
  // connected). oidc-client-ts renews ~1 minute before expiry using the stored refresh
  // token, no hidden iframe needed.
  automaticSilentRenew: true,
});

// Keep the bearer token the API layer sends in sync with the session as it is renewed,
// loaded, or cleared. Without this, a renewed token would sit in the UserManager while the
// API kept sending the stale one. Registered once at module load.
manager.events.addUserLoaded((user) => setAuthToken(user.access_token));
manager.events.addUserUnloaded(() => setAuthToken(null));

// Guards against firing several redirects when a burst of API calls all 401 at once (three
// sync retries, say): the first failed renewal starts the redirect, the rest are no-ops.
let _reauthInFlight = false;

// Renew the access token, and if that is impossible, re-establish the session.
//
// A short access token is normally one refresh away, so try a silent renew first. But when
// the refresh token itself is gone (the SSO idle window elapsed while the tab was closed,
// which is exactly what happens after several hours away) the silent renew fails, and the
// old behavior just cleared the token and left every action 401ing with "session expired"
// forever. Instead, fall back to a full redirect to the IdP: it resumes silently if the
// Keycloak SSO cookie is still alive, or prompts a fresh login if not. Either way the user
// ends up signed in again rather than stuck. Guarded so a burst of 401s redirects once.
async function renewOrReauth(): Promise<void> {
  try {
    const user = await manager.signinSilent();
    if (user) {
      setAuthToken(user.access_token);
      return;
    }
  } catch {
    // fall through to the interactive redirect below
  }
  setAuthToken(null);
  if (_reauthInFlight) return;
  _reauthInFlight = true;
  // Return to wherever the user was after the round trip, not always the home page.
  void manager.signinRedirect({
    state: { returnTo: window.location.pathname + window.location.search },
  });
}

// If the access token expires while the app is open, renew (or re-auth) proactively.
manager.events.addAccessTokenExpired(() => void renewOrReauth());
// When any API call sees a 401, renew (or re-auth) so the action can be retried instead of
// failing silently or leaving the session wedged.
setUnauthorizedHandler(() => void renewOrReauth());

// The identity provider's self-service account console for the signed-in user (manage
// profile, password, sessions). Keycloak serves it at {issuer}/account; swapping the IdP
// means overriding this. This is where "Manage account" in the profile sends the user.
export function accountUrl(): string {
  return `${OIDC_AUTHORITY}/account`;
}

async function bootstrap(): Promise<User | null> {
  if (window.location.search.includes("code=")) {
    const user = await manager.signinRedirectCallback();
    // Restore the page the user was on before a re-auth redirect (renewOrReauth stashes it
    // in the OIDC state); default to home for a first sign-in that carries no returnTo.
    const returnTo = (user.state as { returnTo?: string } | undefined)?.returnTo;
    window.history.replaceState({}, document.title, returnTo || "/");
    return user;
  }
  return manager.getUser();
}

export function login(): void {
  void manager.signinRedirect();
}

export function logout(): void {
  setAuthToken(null);
  void manager.signoutRedirect();
}

export interface Session {
  user: User | null;
  ready: boolean;
}

export function useAuth(): Session {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    bootstrap()
      .then((u) => {
        setUser(u);
        if (u) setAuthToken(u.access_token);
      })
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, []);
  return { user, ready };
}

export function groupsOf(user: User): string[] {
  const g = user.profile.groups;
  return Array.isArray(g) ? (g as string[]) : [];
}

// Management roles from the token. Orthogonal to groups: groups gate what documents
// you can retrieve, roles gate what management actions you may perform. Mirrors the
// agora roles claim; the backend is the source of truth, this is only for show/hide.
export function rolesOf(user: User): string[] {
  const r = user.profile.roles;
  return Array.isArray(r) ? (r as string[]) : [];
}

export function canManageConnectors(roles: string[]): boolean {
  return roles.includes("admin") || roles.includes("connector_admin");
}

export function isAdmin(roles: string[]): boolean {
  return roles.includes("admin");
}

export function usernameOf(user: User): string {
  return (user.profile.preferred_username as string) ?? "user";
}
