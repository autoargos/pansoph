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
// If the token still expires (renewal disabled or failed), try one silent renew; on
// failure the next API call surfaces the 401 and the user can re-login.
manager.events.addAccessTokenExpired(() => {
  void manager.signinSilent().catch(() => setAuthToken(null));
});
// When any API call sees a 401, silently renew (a fresh access token is usually one
// refresh away) so the action can be retried instead of failing.
setUnauthorizedHandler(() => {
  void manager
    .signinSilent()
    .then((u) => u && setAuthToken(u.access_token))
    .catch(() => setAuthToken(null));
});

// The identity provider's self-service account console for the signed-in user (manage
// profile, password, sessions). Keycloak serves it at {issuer}/account; swapping the IdP
// means overriding this. This is where "Manage account" in the profile sends the user.
export function accountUrl(): string {
  return `${OIDC_AUTHORITY}/account`;
}

async function bootstrap(): Promise<User | null> {
  if (window.location.search.includes("code=")) {
    const user = await manager.signinRedirectCallback();
    window.history.replaceState({}, document.title, "/");
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
