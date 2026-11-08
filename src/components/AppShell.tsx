import { Link, useLocation } from "@tanstack/react-router";
import { Home, Map, MessageCircle, Plus, User } from "lucide-react";
import type { ReactNode } from "react";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";
import { UserAvatar } from "@/components/UserAvatar";

const NAV = [
  { to: "/home", label: "Home", icon: Home },
  { to: "/map", label: "Map", icon: Map },
  { to: "/sell", label: "Sell", icon: Plus, primary: true },
  { to: "/chats", label: "Chats", icon: MessageCircle },
  { to: "/profile", label: "Profile", icon: User },
] as const;

export function Logo() {
  return (
    <span className="flex items-center gap-0.5 font-display text-xl font-extrabold text-primary">
      scrap<span className="rounded-full bg-sun px-1.5 text-sun-foreground">pe</span>
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const { profile } = useAuth();
  const isCollector = profile?.role === "collector";
  const items = NAV.filter((n) => !(isCollector && n.to === "/sell"));

  return (
    <div className="min-h-screen pb-24 md:pb-0">
      <header className="sticky top-0 z-30 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Link to="/home"><Logo /></Link>
          <nav className="hidden items-center gap-1.5 md:flex">
            {items.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className={cn(
                  "flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition",
                  n.to === "/sell"
                    ? "bg-sun font-semibold text-sun-foreground shadow-soft hover:brightness-105"
                    : pathname.startsWith(n.to)
                      ? "bg-primary-soft font-semibold text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {n.to === "/profile" ? (
                  <UserAvatar
                    src={profile?.avatar_url}
                    name={profile?.display_name}
                    className="size-4"
                    fallbackClassName="size-4 text-[9px]"
                  />
                ) : (
                  <n.icon className="size-4" />
                )}
                {n.to === "/sell" ? "Sell scrap" : n.label}
              </Link>
            ))}
            {isCollector && (
              <Link
                to="/map"
                className="flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-soft transition hover:opacity-90"
              >
                Go online
              </Link>
            )}
          </nav>
          <div className="flex items-center gap-2 md:hidden">
            <Link to="/profile" className="flex items-center">
              <UserAvatar
                src={profile?.avatar_url}
                name={profile?.display_name}
                className="size-7"
                fallbackClassName="size-7 text-xs bg-primary text-primary-foreground font-bold shadow-sm"
              />
            </Link>
            <span className="rounded-full bg-primary-soft px-3 py-1 text-xs font-semibold text-primary">
              {isCollector ? "Collector" : "Seller"}
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t bg-card/95 backdrop-blur pb-safe md:hidden">
        <div className="mx-auto flex max-w-md items-end justify-around px-2 pt-2">
          {items.map((n) => {
            const active = pathname.startsWith(n.to);
            if ("primary" in n && n.primary)
              return (
                <Link key={n.to} to={n.to} className="-mt-6 flex flex-col items-center gap-1">
                  <span className="flex size-14 items-center justify-center rounded-2xl bg-sun text-sun-foreground shadow-lift">
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
                {n.to === "/profile" ? (
                  <UserAvatar
                    src={profile?.avatar_url}
                    name={profile?.display_name}
                    className={cn("size-5", active && "ring-2 ring-primary")}
                    fallbackClassName={cn("size-5 text-[10px]", active && "ring-2 ring-primary")}
                  />
                ) : (
                  <n.icon className={cn("size-5", active && "stroke-[2.5]")} />
                )}
                {n.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
