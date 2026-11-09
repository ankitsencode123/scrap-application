import { Link } from "@tanstack/react-router";
import { Bell, Home, Map, MessageCircle, Plus, Search, Truck, User } from "lucide-react";
import type { ReactNode } from "react";

export function Logo() {
  return (
    <Link
      to="/home"
      className="flex items-center gap-0.5 font-display text-xl font-extrabold text-primary"
    >
      scrap<span className="rounded-full bg-sun px-1.5 text-sun-foreground">pe</span>
    </Link>
  );
}

import { useUnreadChats } from "@/hooks/use-chat-socket";

export function Shell({
  role,
  children,
  standalone = false,
}: {
  role: "seller" | "collector";
  children: ReactNode;
  standalone?: boolean;
}) {
  const { data: unreadData } = useUnreadChats();
  const unreadTotal = unreadData?.total ?? 0;

  if (!standalone) {
    return <div className="space-y-6 animate-rise">{children}</div>;
  }

  const nav = [
    { label: "Home", to: "/home" as const, icon: Home, active: true },
    { label: "Map", to: "/map" as const, icon: Map },
    {
      label: "Chats",
      to: "/chats" as const,
      icon: MessageCircle,
      badge: unreadTotal > 0 ? unreadTotal : undefined,
    },
    { label: "Profile", to: "/profile" as const, icon: User },
  ];

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6">
          <Logo />
          <div className="ml-4 hidden flex-1 items-center gap-2 rounded-full border bg-card px-4 py-2 text-sm text-muted-foreground md:flex md:max-w-sm">
            <Search className="h-4 w-4" />
            {role === "seller"
              ? "Search pickups, collectors…"
              : "Search listings, areas, materials…"}
          </div>
          <nav className="ml-auto flex items-center gap-1">
            {nav.map((n) => (
              <Link
                key={n.label}
                to={n.to}
                className={`relative hidden items-center gap-1.5 rounded-full px-3 py-2 text-sm font-medium transition sm:flex ${
                  n.active
                    ? "bg-primary-soft text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                <n.icon className="h-4 w-4" />
                {n.label}
                {n.badge && (
                  <span className="ml-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-sun px-1 text-[10px] font-bold text-sun-foreground">
                    {n.badge}
                  </span>
                )}
              </Link>
            ))}
            {role === "seller" ? (
              <Link
                to="/sell"
                className="ml-1 flex items-center gap-1.5 rounded-full bg-sun px-4 py-2 text-sm font-semibold text-sun-foreground shadow-soft transition hover:brightness-105"
              >
                <Plus className="h-4 w-4" /> Sell scrap
              </Link>
            ) : (
              <Link
                to="/map"
                className="ml-1 flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90"
              >
                <Truck className="h-4 w-4" /> Go online
              </Link>
            )}
            <button
              className="relative ml-1 grid h-9 w-9 place-items-center rounded-full border bg-card"
              type="button"
              aria-label="Notifications"
            >
              <Bell className="h-4 w-4" />
              <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-destructive" />
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">{children}</main>
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card-soft animate-rise p-5 ${className}`}>{children}</div>;
}

export function SectionTitle({
  title,
  action,
  onAction,
}: {
  title: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <h3 className="text-lg font-bold">{title}</h3>
      {action && (
        <button
          type="button"
          onClick={onAction}
          className="text-sm font-medium text-primary hover:underline"
        >
          {action}
        </button>
      )}
    </div>
  );
}

export function Stat({
  label,
  value,
  delta,
  icon: Icon,
  tone = "primary",
}: {
  label: string;
  value: string;
  delta?: string;
  icon: React.ComponentType<{ className?: string }>;
  tone?: "primary" | "sun" | "info";
}) {
  const toneCls =
    tone === "sun"
      ? "bg-sun-soft text-sun-foreground"
      : tone === "info"
        ? "bg-info-soft text-info"
        : "bg-primary-soft text-primary";
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between">
        <div className={`grid h-10 w-10 place-items-center rounded-xl ${toneCls}`}>
          <Icon className="h-5 w-5" />
        </div>
        {delta && (
          <span className="rounded-full bg-primary-soft px-2 py-0.5 text-xs font-semibold text-success">
            {delta}
          </span>
        )}
      </div>
      <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="font-display text-2xl font-extrabold">{value}</p>
    </Card>
  );
}

export function Bars({ data, labels }: { data: number[]; labels: string[] }) {
  const max = Math.max(...data, 1);
  return (
    <div className="flex h-44 items-end gap-2">
      {data.map((v, i) => (
        <div key={i} className="group flex flex-1 flex-col items-center gap-2">
          <div className="relative flex w-full flex-1 items-end">
            <div
              className={`w-full rounded-t-lg transition-all group-hover:opacity-90 ${
                i === data.length - 1 ? "bg-sun-grad" : "bg-primary/80"
              }`}
              style={{ height: `${Math.max(6, (v / max) * 100)}%` }}
            />
            <span className="pointer-events-none absolute -top-6 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded bg-foreground px-1.5 py-0.5 text-[10px] text-background opacity-0 transition-opacity group-hover:opacity-100">
              ₹{v}
            </span>
          </div>
          <span className="text-[11px] text-muted-foreground">{labels[i]}</span>
        </div>
      ))}
    </div>
  );
}

export function Stars({ n }: { n: number }) {
  const count = Math.min(5, Math.max(0, Math.round(n)));
  return (
    <span className="text-sun">
      {"★★★★★".slice(0, count)}
      <span className="text-border">{"★★★★★".slice(count)}</span>
    </span>
  );
}

export function StatusPill({ s }: { s: string }) {
  const map: Record<string, string> = {
    Completed: "bg-primary-soft text-primary",
    "On the way": "bg-info-soft text-info",
    Scheduled: "bg-sun-soft text-sun-foreground",
    Pending: "bg-muted text-muted-foreground",
    Cancelled: "bg-destructive/10 text-destructive",
    Accepted: "bg-primary-soft text-primary",
    completed: "bg-primary-soft text-primary",
    on_the_way: "bg-info-soft text-info",
    offer_accepted: "bg-sun-soft text-sun-foreground",
    posted: "bg-muted text-muted-foreground",
    cancelled: "bg-destructive/10 text-destructive",
  };
  const labelMap: Record<string, string> = {
    completed: "Completed",
    on_the_way: "On the way",
    offer_accepted: "Offer accepted",
    posted: "Posted",
    cancelled: "Cancelled",
  };
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-1 text-xs font-semibold ${map[s] ?? "bg-muted"}`}
    >
      {labelMap[s] ?? s}
    </span>
  );
}
