import { createFileRoute, Navigate, Outlet } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/hooks/use-auth";

export const Route = createFileRoute("/_authenticated")({
  component: Guard,
});

function Guard() {
  const { user, loading } = useAuth();
  if (loading)
    return <div className="flex min-h-screen items-center justify-center text-muted-foreground">Loading…</div>;
  if (!user) return <Navigate to="/auth" />;
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
