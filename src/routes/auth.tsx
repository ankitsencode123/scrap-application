import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { lovable } from "@/integrations/lovable/index";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Logo } from "@/components/AppShell";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

type Search = { type?: "seller" | "collector" | undefined };

export const Route = createFileRoute("/auth")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    type: s["type"] === "collector" ? "collector" : s["type"] === "seller" ? "seller" : undefined,
  }),
  head: () => ({
    meta: [
      { title: "Sign in — scrappe" },
      { name: "description", content: "Sign in or create a scrappe account as a seller or collector." },
      { property: "og:title", content: "Sign in — scrappe" },
      { property: "og:description", content: "Sign in or create a scrappe account as a seller or collector." },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const { type } = Route.useSearch();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"signin" | "signup">(type ? "signup" : "signin");
  const [accountType, setAccountType] = useState<"seller" | "collector">(type ?? "seller");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) navigate({ to: "/home" });
  }, [user, navigate]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    if (mode === "signup") {
      const { error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: window.location.origin,
          data: { full_name: name, account_type: accountType },
        },
      });
      if (error) toast.error(error.message);
      else toast.success("Check your email to confirm your account.");
    } else {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) toast.error(error.message);
    }
    setBusy(false);
  };

  const google = async () => {
    const r = await lovable.auth.signInWithOAuth("google", { redirect_uri: window.location.origin });
    if (r.error) toast.error(r.error.message ?? "Google sign-in failed");
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <Logo />
      <div className="mt-6 w-full max-w-sm rounded-3xl border bg-card p-6 shadow-soft">
        <h1 className="text-2xl font-extrabold">{mode === "signup" ? "Create account" : "Welcome back"}</h1>
        <form onSubmit={submit} className="mt-5 space-y-4">
          {mode === "signup" && (
            <>
              <div className="grid grid-cols-2 gap-2">
                {(["seller", "collector"] as const).map((t) => (
                  <button key={t} type="button" onClick={() => setAccountType(t)}
                    className={cn("rounded-xl border p-3 text-left text-sm transition",
                      accountType === t ? "border-primary bg-secondary" : "hover:bg-muted")}>
                    <div className="font-bold">{t === "seller" ? "🏠 Seller" : "🛺 Collector"}</div>
                    <div className="text-xs text-muted-foreground">{t === "seller" ? "I have scrap" : "I buy scrap"}</div>
                  </button>
                ))}
              </div>
              <div className="space-y-1.5"><Label>Full name</Label>
                <Input required value={name} onChange={(e) => setName(e.target.value)} className="h-11" /></div>
            </>
          )}
          <div className="space-y-1.5"><Label>Email</Label>
            <Input required type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="h-11" /></div>
          <div className="space-y-1.5"><Label>Password</Label>
            <Input required type="password" minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} className="h-11" /></div>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {mode === "signup" ? "Create account" : "Sign in"}
          </Button>
        </form>
        <div className="my-4 flex items-center gap-3 text-xs text-muted-foreground"><span className="h-px flex-1 bg-border" />or<span className="h-px flex-1 bg-border" /></div>
        <Button variant="outline" size="lg" className="w-full" onClick={google}>Continue with Google</Button>
        <p className="mt-5 text-center text-sm text-muted-foreground">
          {mode === "signup" ? "Already have an account?" : "New here?"}{" "}
          <button className="font-semibold text-primary" onClick={() => setMode(mode === "signup" ? "signin" : "signup")}>
            {mode === "signup" ? "Sign in" : "Create one"}
          </button>
        </p>
      </div>
    </div>
  );
}
