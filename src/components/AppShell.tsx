import { Link, useLocation } from "@tanstack/react-router";
import { Home, Map, MessageCircle, Plus, User } from "lucide-react";
import type { ReactNode } from "react";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/home", label: "Home", icon: Home },
  { to: "/map", label: "Map", icon: Map },
  { to: "/sell", label: "Sell", icon: Plus, primary: true },
  { to: "/chats", label: "Chats", icon: MessageCircle },
  { to: "/profile", label: "Profile", icon: User },
] as const;

export function Logo() {
  return (
    <span className="font-display text-xl font-extrabold tracking-tight text-primary">
      scrap<span className="text-accent-foreground bg-accent rounded-md px-1 ml-0.5">pe</span>
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const { profile } = useAuth();
  const isCollector = profile?.account_type === "collector";
  const items = NAV.filter((n) => !(isCollector && n.to === "/sell"));

  return (
    <div className="min-h-screen pb-24 md:pb-0">
      <header className="sticky top-0 z-30 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
          <Link to="/home"><Logo /></Link>
          <nav className="hidden items-center gap-1 md:flex">
            {items.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className={cn(
                  "flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition",
                  n.to === "/sell"
                    ? "bg-accent text-accent-foreground"
                    : pathname.startsWith(n.to)
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                )}
              >
                <n.icon className="size-4" />
                {n.to === "/sell" ? "Sell scrap" : n.label}
              </Link>
            ))}
          </nav>
          <span className="rounded-full bg-secondary px-3 py-1 text-xs font-semibold text-secondary-foreground md:hidden">
            {isCollector ? "Collector" : "Seller"}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-5">{children}</main>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t bg-card/95 backdrop-blur pb-safe md:hidden">
        <div className="mx-auto flex max-w-md items-end justify-around px-2 pt-2">
          {items.map((n) => {
            const active = pathname.startsWith(n.to);
            if ("primary" in n && n.primary)
              return (
                <Link key={n.to} to={n.to} className="-mt-6 flex flex-col items-center gap-1">
                  <span className="flex size-14 items-center justify-center rounded-2xl bg-accent text-accent-foreground shadow-soft">
                    <n.icon className="size-6" />
                  </span>
                  <span className="text-[11px] font-semibold">{n.label}</span>
                </Link>
              );
            return (
              <Link
                key={n.to}
                to={n.to}
                className={cn(
                  "flex min-w-14 flex-col items-center gap-1 py-1 text-[11px] font-medium",
                  active ? "text-primary" : "text-muted-foreground",
                )}
              >
                <n.icon className={cn("size-5", active && "stroke-[2.5]")} />
                {n.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
