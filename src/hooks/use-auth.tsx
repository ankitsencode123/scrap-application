import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  api,
  getTokens,
  SESSION_EXPIRED_EVENT,
  setTokens,
  storeTokenResponse,
  type ApiUser,
  type Role,
  type TokenResponse,
} from "@/lib/api";

type SignUpInput = { email: string; password: string; display_name: string; role: Role };

type AuthCtx = {
  /** The signed-in account (same object as `profile`; kept for components that only need id/email). */
  user: ApiUser | null;
  profile: ApiUser | null;
  loading: boolean;
  refreshProfile: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (input: SignUpInput) => Promise<void>;
  /** Pass the Google ID token; `role` is needed the first time (API answers 400 ROLE_REQUIRED otherwise). */
  signInWithGoogle: (idToken: string, role?: Role) => Promise<void>;
  signOut: () => Promise<void>;
};

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [profile, setProfile] = useState<ApiUser | null>(null);
  const [loading, setLoading] = useState(true);

  const loadProfile = useCallback(async () => {
    if (!getTokens()) return setProfile(null);
    try {
      setProfile(await api<ApiUser>("/users/me"));
    } catch {
      // 401s are handled (refresh / sign-out event) inside api(); a network error keeps the old profile.
    }
  }, []);

  const clearSession = useCallback(() => {
    setTokens(null);
    setProfile(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    loadProfile().finally(() => setLoading(false));
    const onExpired = () => clearSession();
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [loadProfile, clearSession]);

  const accept = (r: TokenResponse) => {
    qc.clear();
    storeTokenResponse(r);
    setProfile(r.user);
  };

  return (
    <Ctx.Provider
      value={{
        user: profile,
        profile,
        loading,
        refreshProfile: loadProfile,
        signIn: async (email, password) =>
          accept(await api<TokenResponse>("/auth/login", { method: "POST", body: { email, password }, auth: false })),
        signUp: async (input) =>
          accept(await api<TokenResponse>("/auth/signup", { method: "POST", body: input, auth: false })),
        signInWithGoogle: async (idToken, role) =>
          accept(
            await api<TokenResponse>("/auth/google", {
              method: "POST",
              body: { id_token: idToken, role: role ?? null },
              auth: false,
            }),
          ),
        signOut: async () => clearSession(),
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth must be inside AuthProvider");
  return c;
}
