import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Loader2, Send, Wifi, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage, type ApiListing, type ApiMessage, type PublicProfile } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useChatSocket } from "@/hooks/use-chat-socket";

type Search = { listing?: string | undefined; with?: string | undefined };

export const Route = createFileRoute("/_authenticated/chats")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    listing: typeof s["listing"] === "string" ? s["listing"] : undefined,
    with: typeof s["with"] === "string" ? s["with"] : undefined,
  }),
  head: () => ({
    meta: [
      { title: "Chats — scrappe" },
      { name: "description", content: "Chat with sellers and collectors about scrap pickups." },
      { property: "og:title", content: "Chats — scrappe" },
      { property: "og:description", content: "Chat with sellers and collectors about scrap pickups." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ChatsPage,
});

function ChatsPage() {
  const { user } = useAuth();
  const search = Route.useSearch();
  const navigate = useNavigate();

  // Real-time WebSocket with fast fallback polling when disconnected
  const [activeWithState, setActiveWithState] = useState(search.with);
  const [activeListingState, setActiveListingState] = useState(search.listing);

  useEffect(() => {
    setActiveWithState(search.with);
    setActiveListingState(search.listing);
  }, [search.with, search.listing]);

  const {
    isConnected,
    typingState,
    presenceState,
    sendChatMessage,
    startTyping,
    stopTyping,
  } = useChatSocket(activeListingState, activeWithState);

  const { data: msgs = [], isLoading } = useQuery({
    queryKey: ["messages"],
    refetchInterval: search.with ? 1200 : 3000,
    refetchOnWindowFocus: true,
    queryFn: () => api<ApiMessage[]>("/messages"),
  });

  const threads = useMemo(() => {
    const m = new Map<string, { listing: string; with: string; last: ApiMessage }>();
    for (const x of msgs) {
      const other = x.sender_id === user?.id ? x.recipient_id : x.sender_id;
      m.set(`${x.listing_id}|${other}`, { listing: x.listing_id, with: other, last: x });
    }
    if (search.listing && search.with && !m.has(`${search.listing}|${search.with}`))
      m.set(`${search.listing}|${search.with}`, { listing: search.listing, with: search.with, last: undefined as never });
    return [...m.values()].sort((a, b) => (b.last?.created_at ?? "9").localeCompare(a.last?.created_at ?? "9"));
  }, [msgs, user?.id, search.listing, search.with]);

  // Names / titles come with the messages; a brand-new thread (no messages yet) is looked up directly.
  const names = new Map<string, string>();
  const titles = new Map<string, string>();
  for (const x of msgs) {
    names.set(x.sender_id, x.sender_name);
    names.set(x.recipient_id, x.recipient_name);
    titles.set(x.listing_id, x.listing_title);
  }
  const newThread = !!(search.listing && search.with) && !titles.has(search.listing!);
  const { data: newPerson } = useQuery({
    queryKey: ["chatPerson", search.with],
    enabled: newThread && !names.has(search.with!),
    queryFn: () => api<PublicProfile>(`/users/${search.with}`),
  });
  const { data: newListing } = useQuery({
    queryKey: ["chatListing", search.listing],
    enabled: newThread,
    queryFn: () => api<ApiListing>(`/listings/${search.listing}`),
  });
  const name = (id: string) => names.get(id) ?? (id === search.with ? newPerson?.display_name : undefined) ?? "User";
  const title = (id: string) => titles.get(id) ?? (id === search.listing ? newListing?.title : undefined) ?? "Listing";

  const open = !!(search.listing && search.with);
  const convo = msgs.filter(
    (m) => m.listing_id === search.listing && (m.sender_id === search.with || m.recipient_id === search.with),
  );

  const activeTypingKey = `${search.listing}|${search.with}`;
  const isPeerTyping = Boolean(typingState[activeTypingKey]);
  const isPeerOnline = Boolean(search.with && presenceState[search.with]);

  return (
    <div className="grid h-[calc(100vh-11rem)] gap-4 md:h-[calc(100vh-7rem)] md:grid-cols-[300px_1fr]">
      <aside className={cn("overflow-y-auto rounded-3xl border bg-card", open && "hidden md:block")}>
        <div className="flex items-center justify-between p-4 pb-2">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-extrabold">Chats</h1>
            {isConnected ? (
              <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full dark:bg-emerald-950/40 dark:text-emerald-400">
                <span className="size-1.5 rounded-full bg-emerald-500 animate-pulse" />
                Live
              </span>
            ) : (
              <span className="flex items-center gap-1 text-[11px] text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                <WifiOff className="size-3" />
                Connecting…
              </span>
            )}
          </div>
          {isLoading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        </div>
        {isLoading && threads.length === 0 ? (
          <div className="space-y-3 p-4">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-4 animate-spin text-primary" />
              <span>Loading conversations…</span>
            </div>
            {[1, 2, 3].map((i) => (
              <div key={i} className="animate-pulse space-y-2 rounded-xl bg-muted/40 p-3">
                <div className="h-4 w-1/3 rounded bg-muted" />
                <div className="h-3 w-1/2 rounded bg-muted" />
              </div>
            ))}
          </div>
        ) : threads.length === 0 ? (
          <p className="px-4 text-sm text-muted-foreground">No chats yet. Open a listing and tap “Chat”.</p>
        ) : (
          threads.map((t) => {
            const isOnline = Boolean(presenceState[t.with]);
            const isTyping = Boolean(typingState[`${t.listing}|${t.with}`]);
            return (
              <Link
                key={t.listing + t.with}
                to="/chats"
                search={{ listing: t.listing, with: t.with }}
                className={cn(
                  "block border-t px-4 py-3 hover:bg-secondary/50 transition relative",
                  t.listing === search.listing && t.with === search.with && "bg-secondary",
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "size-2 rounded-full shrink-0",
                        isOnline ? "bg-emerald-500" : "bg-muted-foreground/30",
                      )}
                    />
                    <p className="font-semibold">{name(t.with)}</p>
                  </div>
                  {isTyping && (
                    <span className="text-[11px] text-primary italic font-medium">typing…</span>
                  )}
                </div>
                <p className="truncate text-xs text-muted-foreground">{title(t.listing)}</p>
                {t.last && <p className="truncate text-sm text-muted-foreground">{t.last.body}</p>}
              </Link>
            );
          })
        )}
      </aside>
      <section className={cn("flex min-h-0 flex-col rounded-3xl border bg-card", !open && "hidden md:flex")}>
        {open ? (
          <Thread
            listing={search.listing!}
            other={search.with!}
            peerName={name(search.with!)}
            listingTitle={title(search.listing!)}
            msgs={convo}
            loadingMsgs={isLoading}
            me={user!.id}
            isPeerOnline={isPeerOnline}
            isPeerTyping={isPeerTyping}
            onSendMessage={sendChatMessage}
            onStartTyping={startTyping}
            onStopTyping={stopTyping}
            onBack={() => navigate({ to: "/chats", search: {} })}
          />
        ) : (
          <div className="m-auto text-muted-foreground">Pick a chat</div>
        )}
      </section>
    </div>
  );
}

function Thread({
  listing,
  other,
  peerName,
  listingTitle,
  msgs,
  loadingMsgs,
  me,
  isPeerOnline,
  isPeerTyping,
  onSendMessage,
  onStartTyping,
  onStopTyping,
  onBack,
}: {
  listing: string;
  other: string;
  peerName: string;
  listingTitle: string;
  me: string;
  msgs: ApiMessage[];
  loadingMsgs?: boolean;
  isPeerOnline?: boolean;
  isPeerTyping?: boolean;
  onSendMessage: (listingId: string, recipientId: string, body: string, clientId: string) => boolean;
  onStartTyping: (listingId: string, recipientId: string) => void;
  onStopTyping: (listingId: string, recipientId: string) => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs.length, isPeerTyping]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setText(val);
    if (val.trim()) {
      onStartTyping(listing, other);
      if (typingTimer.current) clearTimeout(typingTimer.current);
      typingTimer.current = setTimeout(() => {
        onStopTyping(listing, other);
      }, 1500);
    } else {
      if (typingTimer.current) clearTimeout(typingTimer.current);
      onStopTyping(listing, other);
    }
  };

  const send = async () => {
    const body = text.trim();
    if (!body) return;

    // 1. Immediately clear input & cancel typing timer
    setText("");
    if (typingTimer.current) clearTimeout(typingTimer.current);
    onStopTyping(listing, other);

    // 2. Create optimistic message & inject instantly into cache (0ms latency!)
    const clientId = `c-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const optimisticMsg: ApiMessage = {
      id: clientId,
      listing_id: listing,
      listing_title: listingTitle,
      sender_id: me,
      sender_name: "You",
      recipient_id: other,
      recipient_name: peerName,
      body,
      created_at: new Date().toISOString(),
    };

    qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) => [...old, optimisticMsg]);

    // 3. Send via WebSocket (or fallback to REST API)
    try {
      const sentViaWs = onSendMessage(listing, other, body, clientId);
      if (!sentViaWs) {
        const res = await api<ApiMessage>("/messages", {
          method: "POST",
          body: { listing_id: listing, recipient_id: other, body },
        });
        qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) =>
          old.map((m) => (m.id === clientId ? res : m))
        );
      }
    } catch (err) {
      // Revert optimistic message if network failed
      qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) =>
        old.filter((m) => m.id !== clientId)
      );
      setText(body);
      toast.error(errorMessage(err));
    }
  };

  return (
    <>
      <div className="flex items-center justify-between border-b p-3">
        <div className="flex items-center gap-2 min-w-0">
          <Button variant="ghost" size="icon" className="md:hidden shrink-0" onClick={onBack}>
            <ArrowLeft />
          </Button>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold truncate">{peerName}</span>
              <span
                className={cn(
                  "size-2 rounded-full shrink-0",
                  isPeerOnline ? "bg-emerald-500" : "bg-muted-foreground/30",
                )}
                title={isPeerOnline ? "Online" : "Offline"}
              />
              <span className="text-[11px] text-muted-foreground">
                {isPeerOnline ? "Online" : "Offline"}
              </span>
            </div>
            <Link
              to="/listings/$id"
              params={{ id: listing }}
              className="truncate block text-xs text-muted-foreground hover:underline"
            >
              {listingTitle}
            </Link>
          </div>
        </div>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto p-4">
        {loadingMsgs && msgs.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="size-6 animate-spin text-primary" />
            <span className="text-xs">Loading conversation…</span>
          </div>
        ) : msgs.length === 0 ? (
          <div className="flex h-full items-center justify-center text-center text-xs text-muted-foreground">
            No messages yet. Say hello to get started!
          </div>
        ) : (
          msgs.map((m) => (
            <div
              key={m.id}
              className={cn(
                "max-w-[80%] rounded-2xl px-3 py-2 text-sm",
                m.sender_id === me
                  ? "ml-auto bg-primary text-primary-foreground"
                  : "bg-secondary text-secondary-foreground",
              )}
            >
              {m.body}
              <div className="mt-0.5 text-[10px] opacity-70">
                {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </div>
            </div>
          ))
        )}

        {isPeerTyping && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground italic px-2 py-1">
            <span className="size-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
            <span className="size-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
            <span className="size-1.5 rounded-full bg-primary animate-bounce" />
            <span className="ml-1">{peerName} is typing…</span>
          </div>
        )}
        <div ref={end} />
      </div>

      <form
        className="flex gap-2 border-t p-3"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <Input
          value={text}
          onChange={handleChange}
          onBlur={() => {
            if (typingTimer.current) clearTimeout(typingTimer.current);
            onStopTyping(listing, other);
          }}
          placeholder="Type a message…"
          autoFocus
        />
        <Button type="submit" size="icon" disabled={!text.trim()}>
          <Send className="size-4" />
        </Button>
      </form>
    </>
  );
}
