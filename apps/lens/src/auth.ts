import { type User, UserManager } from "oidc-client-ts";
import { useEffect, useState } from "react";

import { setAuthToken } from "./api.ts";

// OIDC against the hosted Keycloak. The IdP is swappable; this only knows the issuer
// and client id, both pointing at the demo realm.
const manager = new UserManager({
  authority: "http://localhost:8090/realms/pansoph",
  client_id: "lens",
  redirect_uri: `${window.location.origin}/`,
  post_logout_redirect_uri: `${window.location.origin}/`,
  response_type: "code",
  scope: "openid profile email",
});

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

export function usernameOf(user: User): string {
  return (user.profile.preferred_username as string) ?? "user";
}
