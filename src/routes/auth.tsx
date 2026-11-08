import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, errorMessage, type Role } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Logo } from "@/components/AppShell";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";

type Search = { type?: "seller" | "collector" | undefined };

export const Route = createFileRoute("/auth")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    type: s["type"] === "collector" ? "collector" : s["type"] === "seller" ? "seller" : undefined,
  }),
  head: () => ({
    meta: [
      { title: "Sign in — scrappe" },
      {
        name: "description",
        content: "Sign in or create a scrappe account as a seller or collector.",
      },
      { property: "og:title", content: "Sign in — scrappe" },
      {
        property: "og:description",
        content: "Sign in or create a scrappe account as a seller or collector.",
      },
    ],
  }),
  component: AuthPage,
});

const GOOGLE_CLIENT_ID = (import.meta.env["VITE_GOOGLE_CLIENT_ID"] as string | undefined) ?? "";

type GoogleId = {
  initialize: (o: { client_id: string; callback: (r: { credential: string }) => void }) => void;
  renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
};
const googleId = () =>
  (window as unknown as { google?: { accounts?: { id?: GoogleId } } }).google?.accounts?.id;

function loadGoogleScript(): Promise<void> {
  if (googleId()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Couldn't load Google sign-in"));
    document.head.appendChild(s);
  });
}

function AuthPage() {
  const { type } = Route.useSearch();
  const { user, signIn, signUp, signInWithGoogle } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"signin" | "signup">(type ? "signup" : "signin");
  const [accountType, setAccountType] = useState<"seller" | "collector">(type ?? "seller");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  // Google ID token waiting for the user to pick Seller/Collector (API answered 400 ROLE_REQUIRED).
  const [pendingGoogle, setPendingGoogle] = useState<string | null>(null);
  const googleBtn = useRef<HTMLDivElement>(null);
  const modeRef = useRef({ mode, accountType });
  modeRef.current = { mode, accountType };

  useEffect(() => {
    if (user) navigate({ to: "/home" });
  }, [user, navigate]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "signup")
        await signUp({ email, password, display_name: name, role: accountType });
      else await signIn(email, password);
    } catch (err) {
      toast.error(errorMessage(err));
    }
    setBusy(false);
  };

  const finishGoogle = useCallback(
    async (idToken: string, role?: Role) => {
      setBusy(true);
      try {
        await signInWithGoogle(idToken, role);
        setPendingGoogle(null);
      } catch (err) {
        if (err instanceof ApiError && err.code === "ROLE_REQUIRED") {
          // New Google account: ask which side they are, then call the API again with the role.
          if (modeRef.current.mode === "signup" && !role)
            return void finishGoogle(idToken, modeRef.current.accountType);
          setPendingGoogle(idToken);
        } else toast.error(errorMessage(err));
      }
      setBusy(false);
    },
    [signInWithGoogle],
  );

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;
    let cancelled = false;
    loadGoogleScript()
      .then(() => {
        const g = googleId();
        if (cancelled || !g || !googleBtn.current) return;
        googleBtn.current.innerHTML = "";
        g.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (r) => void finishGoogle(r.credential),
        });
        g.renderButton(googleBtn.current, {
          theme: "outline",
          size: "large",
          width: 320,
          text: "continue_with",
        });
      })
      .catch((err) => toast.error(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [finishGoogle]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <Logo />
      <div className="mt-6 w-full max-w-sm rounded-3xl border bg-card p-6 shadow-soft">
        <h1 className="text-2xl font-extrabold">
          {mode === "signup" ? "Create account" : "Welcome back"}
        </h1>
        <form onSubmit={submit} className="mt-5 space-y-4">
          {mode === "signup" && (
            <>
              <div className="grid grid-cols-2 gap-2">
                {(["seller", "collector"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setAccountType(t)}
                    className={cn(
                      "rounded-xl border p-3 text-left text-sm transition",
                      accountType === t ? "border-primary bg-secondary" : "hover:bg-muted",
                    )}
                  >
                    <div className="font-bold">{t === "seller" ? "🏠 Seller" : "🛺 Collector"}</div>
                    <div className="text-xs text-muted-foreground">
                      {t === "seller" ? "I have scrap" : "I buy scrap"}
                    </div>
                  </button>
                ))}
              </div>
              <div className="space-y-1.5">
                <Label>Full name</Label>
                <Input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="h-11"
                />
              </div>
            </>
          )}
          <div className="space-y-1.5">
            <Label>Email</Label>
            <Input
              required
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="h-11"
            />
          </div>
          <div className="space-y-1.5">
            <Label>Password</Label>
            <Input
              required
              type="password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="h-11"
            />
          </div>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
            {busy
              ? mode === "signup"
                ? "Creating account…"
                : "Signing in…"
              : mode === "signup"
                ? "Create account"
                : "Sign in"}
          </Button>
        </form>
        <div className="my-4 flex items-center gap-3 text-xs text-muted-foreground">
          <span className="h-px flex-1 bg-border" />
          or
          <span className="h-px flex-1 bg-border" />
        </div>
        {pendingGoogle && (
          <div className="space-y-3 rounded-2xl border bg-secondary/40 p-3">
            <p className="text-sm font-semibold">Almost done — how will you use scrappe?</p>
            <div className="grid grid-cols-2 gap-2">
              {(["seller", "collector"] as const).map((t) => (
                <Button
                  key={t}
                  variant="outline"
                  disabled={busy}
                  onClick={() => finishGoogle(pendingGoogle, t)}
                >
                  {busy ? <Loader2 className="mr-1.5 size-4 animate-spin" /> : null}
                  {t === "seller" ? "🏠 Seller" : "🛺 Collector"}
                </Button>
              ))}
            </div>
          </div>
        )}
        {GOOGLE_CLIENT_ID ? (
          <div ref={googleBtn} className={cn("flex justify-center", pendingGoogle && "hidden")} />
        ) : (
          <Button
            variant="outline"
            size="lg"
            className="w-full"
            onClick={() =>
              toast.error("Google sign-in isn't configured (set VITE_GOOGLE_CLIENT_ID)")
            }
          >
            Continue with Google
          </Button>
        )}
        <p className="mt-5 text-center text-sm text-muted-foreground">
          {mode === "signup" ? "Already have an account?" : "New here?"}{" "}
          <button
            className="font-semibold text-primary"
            onClick={() => setMode(mode === "signup" ? "signin" : "signup")}
          >
            {mode === "signup" ? "Sign in" : "Create one"}
          </button>
        </p>
      </div>
    </div>
  );
}
