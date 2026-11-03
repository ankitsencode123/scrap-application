import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Send } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Search = { listing?: string | undefined; with?: string | undefined };

export const Route = createFileRoute("/_authenticated/chats")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    listing: typeof s["listing"] === "string" ? s["listing"] : undefined,
    with: typeof s["with"] === "string" ? s["with"] : undefined,
  }),
  head: () => ({ meta: [{ title: "Chats — scrappe" }, { name: "description", content: "Chat with sellers and collectors about scrap pickups." }, { property: "og:title", content: "Chats — scrappe" }, { property: "og:description", content: "Chat with sellers and collectors about scrap pickups." }, { property: "og:type", content: "website" }, { name: "twitter:card", content: "summary" }] }),
  component: ChatsPage,
});

function ChatsPage() {
  const { user } = useAuth();
  const search = Route.useSearch();
  const navigate = useNavigate();

  const { data: msgs = [] } = useQuery({
    queryKey: ["messages", user?.id],
    refetchInterval: 3000,
    queryFn: async () => (await supabase.from("messages").select("*").order("created_at", { ascending: true }).limit(1000)).data ?? [],
  });

  const threads = useMemo(() => {
    const m = new Map<string, { listing: string; with: string; last: (typeof msgs)[number] }>();
    for (const x of msgs) {
      const other = x.sender_id === user?.id ? x.recipient_id : x.sender_id;
      m.set(`${x.listing_id}|${other}`, { listing: x.listing_id, with: other, last: x });
    }
    if (search.listing && search.with && !m.has(`${search.listing}|${search.with}`))
      m.set(`${search.listing}|${search.with}`, { listing: search.listing, with: search.with, last: undefined as never });
    return [...m.values()].sort((a, b) => (b.last?.created_at ?? "9").localeCompare(a.last?.created_at ?? "9"));
  }, [msgs, user?.id, search.listing, search.with]);

  const ids = [...new Set(threads.map((t) => t.with))];
  const lids = [...new Set(threads.map((t) => t.listing))];
  const { data: people = [] } = useQuery({
    queryKey: ["people", ids.join()],
    enabled: ids.length > 0,
    queryFn: async () => (await supabase.from("profiles").select("id,full_name").in("id", ids)).data ?? [],
  });
  const { data: listings = [] } = useQuery({
    queryKey: ["chatListings", lids.join()],
    enabled: lids.length > 0,
    queryFn: async () => (await supabase.from("listings").select("id,title").in("id", lids)).data ?? [],
  });
  const name = (id: string) => people.find((p) => p.id === id)?.full_name || "User";
  const title = (id: string) => listings.find((p) => p.id === id)?.title || "Listing";

  const open = !!(search.listing && search.with);
  const convo = msgs.filter((m) => m.listing_id === search.listing && (m.sender_id === search.with || m.recipient_id === search.with));

  return (
    <div className="grid h-[calc(100vh-11rem)] gap-4 md:h-[calc(100vh-7rem)] md:grid-cols-[300px_1fr]">
      <aside className={cn("overflow-y-auto rounded-3xl border bg-card", open && "hidden md:block")}>
        <h1 className="p-4 text-xl font-extrabold">Chats</h1>
        {threads.length === 0 && <p className="px-4 text-sm text-muted-foreground">No chats yet. Open a listing and tap “Chat”.</p>}
        {threads.map((t) => (
          <Link key={t.listing + t.with} to="/chats" search={{ listing: t.listing, with: t.with }}
            className={cn("block border-t px-4 py-3 hover:bg-secondary/50", t.listing === search.listing && t.with === search.with && "bg-secondary")}>
            <p className="font-semibold">{name(t.with)}</p>
            <p className="truncate text-xs text-muted-foreground">{title(t.listing)}</p>
            {t.last && <p className="truncate text-sm text-muted-foreground">{t.last.body}</p>}
          </Link>
        ))}
      </aside>
      <section className={cn("flex min-h-0 flex-col rounded-3xl border bg-card", !open && "hidden md:flex")}>
        {open ? (
          <Thread listing={search.listing!} other={search.with!} title={`${name(search.with!)} · ${title(search.listing!)}`}
            msgs={convo} me={user!.id} onBack={() => navigate({ to: "/chats", search: {} })} />
        ) : (
          <div className="m-auto text-muted-foreground">Pick a chat</div>
        )}
      </section>
    </div>
  );
}

function Thread({ listing, other, title, msgs, me, onBack }: {
  listing: string; other: string; title: string; me: string; onBack: () => void;
  msgs: { id: string; body: string; sender_id: string; created_at: string }[];
}) {
  const [text, setText] = useState("");
  const [local, setLocal] = useState<typeof msgs>([]);
  const end = useRef<HTMLDivElement>(null);
  const all = [...msgs, ...local.filter((l) => !msgs.some((m) => m.body === l.body && m.sender_id === me && m.created_at >= l.created_at.slice(0, 16)))];
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [all.length]);

  const send = async () => {
    const body = text.trim();
    if (!body) return;
    setText("");
    setLocal((x) => [...x, { id: "tmp" + Date.now(), body, sender_id: me, created_at: new Date().toISOString() }]);
    await supabase.from("messages").insert({ listing_id: listing, sender_id: me, recipient_id: other, body });
  };

  return (
    <>
      <div className="flex items-center gap-2 border-b p-3">
        <Button variant="ghost" size="icon" className="md:hidden" onClick={onBack}><ArrowLeft /></Button>
        <Link to="/listings/$id" params={{ id: listing }} className="truncate font-semibold hover:underline">{title}</Link>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-4">
        {all.map((m) => (
          <div key={m.id} className={cn("max-w-[80%] rounded-2xl px-3 py-2 text-sm",
            m.sender_id === me ? "ml-auto bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground")}>
            {m.body}
            <div className="mt-0.5 text-[10px] opacity-70">{new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
          </div>
        ))}
        <div ref={end} />
      </div>
      <form className="flex gap-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a message…" />
        <Button type="submit" size="icon"><Send /></Button>
      </form>
    </>
  );
}
