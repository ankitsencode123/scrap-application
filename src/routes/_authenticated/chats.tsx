import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  Check,
  CheckCheck,
  Loader2,
  MessageCircle,
  Send,
  Volume2,
  VolumeX,
  WifiOff,
} from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage, type ApiListing, type ApiMessage, type PublicProfile } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useChatSocket, useUnreadChats } from "@/hooks/use-chat-socket";
import { UserAvatar } from "@/components/UserAvatar";
import { isChatSoundMuted, playChatChime, setChatSoundMuted } from "@/lib/sound";

type Search = { listing?: string | undefined; with?: string | undefined };

function formatMessageTime(iso?: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    if (isToday) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    const diffDays = Math.round((now.getTime() - d.getTime()) / (1000 * 3600 * 24));
    if (diffDays <= 6) {
      return d.toLocaleDateString([], { weekday: "short" });
    }
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

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
      {
        property: "og:description",
        content: "Chat with sellers and collectors about scrap pickups.",
      },
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
  const [filterUnreadOnly, setFilterUnreadOnly] = useState(false);

  useEffect(() => {
    setActiveWithState(search.with);
    setActiveListingState(search.listing);
  }, [search.with, search.listing]);

  const {
    isConnected,
    typingState,
    presenceState,
    arrivedMsgIds,
    arrivedThreadKeys,
    lastSeenWatermarks,
    sendChatMessage,
    startTyping,
    stopTyping,
    markRead,
  } = useChatSocket(activeListingState, activeWithState);

  // Global unread counters across all threads
  const { data: unreadData } = useUnreadChats();

  const { data: msgs = [], isLoading } = useQuery({
    queryKey: ["messages"],
    refetchInterval: search.with ? 1200 : 3000,
    refetchOnWindowFocus: true,
    queryFn: () => api<ApiMessage[]>("/messages"),
  });

  // User's own listings as fallback to initiate thread if listing is not yet specified
  const { data: myListings = [] } = useQuery({
    queryKey: ["my-listings-for-chat"],
    enabled: Boolean(search.with && !search.listing),
    queryFn: () => api<ApiListing[]>("/listings", { query: { scope: "mine" } }).catch(() => []),
  });

  const threads = useMemo(() => {
    const m = new Map<string, { listing: string; with: string; last: ApiMessage; count: number }>();
    for (const x of msgs) {
      const other = x.sender_id === user?.id ? x.recipient_id : x.sender_id;
      const key = `${x.listing_id}|${other}`;
      const existing = m.get(key);
      m.set(key, {
        listing: x.listing_id,
        with: other,
        last: x,
        count: (existing?.count ?? 0) + 1,
      });
    }
    if (search.listing && search.with && !m.has(`${search.listing}|${search.with}`)) {
      m.set(`${search.listing}|${search.with}`, {
        listing: search.listing,
        with: search.with,
        last: undefined as never,
        count: 0,
      });
    }
    return [...m.values()].sort((a, b) =>
      (b.last?.created_at ?? "9").localeCompare(a.last?.created_at ?? "9"),
    );
  }, [msgs, user?.id, search.listing, search.with]);

  // Names / titles come with the messages; a brand-new thread (no messages yet) is looked up directly.
  const names = new Map<string, string>();
  const titles = new Map<string, string>();
  for (const x of msgs) {
    names.set(x.sender_id, x.sender_name);
    names.set(x.recipient_id, x.recipient_name);
    titles.set(x.listing_id, x.listing_title);
  }
  const newThread = Boolean(search.listing && search.with && !titles.has(search.listing));
  const { data: newPerson } = useQuery({
    queryKey: ["chatPerson", search.with],
    enabled: Boolean(newThread && search.with && !names.has(search.with)),
    queryFn: () => api<PublicProfile>(`/users/${search.with}`).catch(() => null),
  });
  const { data: newListing } = useQuery({
    queryKey: ["chatListing", search.listing],
    enabled: Boolean(newThread && search.listing),
    queryFn: () => api<ApiListing>(`/listings/${search.listing}`).catch(() => null),
  });
  const name = (id: string) =>
    names.get(id) ?? (id === search.with ? newPerson?.display_name : undefined) ?? "User";
  const title = (id: string) =>
    titles.get(id) ?? (id === search.listing ? newListing?.title : undefined) ?? "Listing";

  const open = Boolean(search.listing && search.with);
  const convo = msgs.filter(
    (m) =>
      m.listing_id === search.listing &&
      (m.sender_id === search.with || m.recipient_id === search.with),
  );

  const activeTypingKey = `${search.listing}|${search.with}`;
  const isPeerTyping = Boolean(typingState[activeTypingKey]);
  const isPeerOnline = Boolean(search.with && presenceState[search.with]);

  // Auto-resolve single parameter searches to immediately open the specific chat thread
  useEffect(() => {
    if (search.listing && !search.with && msgs.length > 0) {
      const match = msgs.find((m) => m.listing_id === search.listing);
      if (match) {
        const other = match.sender_id === user?.id ? match.recipient_id : match.sender_id;
        navigate({
          to: "/chats",
          search: { listing: search.listing, with: other },
          replace: true,
        });
      }
    } else if (search.with && !search.listing) {
      const match = msgs.find((m) => m.sender_id === search.with || m.recipient_id === search.with);
      if (match) {
        navigate({
          to: "/chats",
          search: { listing: match.listing_id, with: search.with },
          replace: true,
        });
      } else if (myListings.length > 0) {
        // Fallback to seller's active or first listing
        navigate({
          to: "/chats",
          search: { listing: myListings[0].id, with: search.with },
          replace: true,
        });
      }
    }
  }, [search.listing, search.with, msgs, myListings, user?.id, navigate]);

  const totalUnreadCount = unreadData?.total ?? 0;

  // Filter threads if user clicks "Unread only"
  const visibleThreads = useMemo(() => {
    if (!filterUnreadOnly) return threads;
    return threads.filter((t) => {
      const unreadItem = unreadData?.conversations?.find(
        (c) => c.listing_id === t.listing && c.peer_id === t.with,
      );
      return (unreadItem?.unread ?? 0) > 0;
    });
  }, [threads, filterUnreadOnly, unreadData]);

  const handleMarkRead = useCallback(() => {
    if (search.listing && search.with) {
      markRead(search.listing, search.with);
    }
  }, [search.listing, search.with, markRead]);

  if (!user) {
    return (
      <div className="flex min-h-[350px] items-center justify-center">
        <Loader2 className="size-6 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="grid h-[calc(100vh-11rem)] gap-4 md:h-[calc(100vh-7rem)] md:grid-cols-[330px_1fr]">
      {/* -------------------- SIDEBAR: THREADS & MESSAGE COUNTS -------------------- */}
      <aside
        className={cn(
          "flex flex-col overflow-hidden rounded-3xl border bg-card shadow-soft",
          open && "hidden md:flex",
        )}
      >
        {/* Header & Connection status */}
        <div className="flex items-center justify-between border-b p-4 pb-3">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-extrabold flex items-center gap-2">
              Chats
              {totalUnreadCount > 0 && (
                <span className="flex size-5 items-center justify-center rounded-full bg-emerald-600 text-[11px] font-bold text-white shadow-sm ring-2 ring-emerald-500/30 animate-pulse">
                  {totalUnreadCount > 99 ? "99+" : totalUnreadCount}
                </span>
              )}
            </h1>
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

        {/* Filter bar: All vs Unread */}
        {threads.length > 0 && (
          <div className="flex items-center gap-1.5 border-b bg-muted/30 px-3 py-2 text-xs">
            <button
              type="button"
              onClick={() => setFilterUnreadOnly(false)}
              className={cn(
                "rounded-full px-3 py-1 font-semibold transition",
                !filterUnreadOnly
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              All ({threads.length})
            </button>
            <button
              type="button"
              onClick={() => setFilterUnreadOnly(true)}
              className={cn(
                "flex items-center gap-1 rounded-full px-3 py-1 font-semibold transition",
                filterUnreadOnly
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              Unread
              {totalUnreadCount > 0 && (
                <span
                  className={cn(
                    "flex size-4 items-center justify-center rounded-full text-[10px] font-bold",
                    filterUnreadOnly ? "bg-white text-primary" : "bg-emerald-600 text-white",
                  )}
                >
                  {totalUnreadCount}
                </span>
              )}
            </button>
          </div>
        )}

        {/* Thread list with side count badges & arrival pulse highlights */}
        <div className="flex-1 overflow-y-auto divide-y">
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
          ) : visibleThreads.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              <MessageCircle className="mx-auto mb-2 size-8 opacity-30" />
              {filterUnreadOnly
                ? "No unread messages."
                : "No chats yet. Open a listing and tap “Chat”."}
            </div>
          ) : (
            visibleThreads.map((t) => {
              const isOnline = Boolean(presenceState[t.with]);
              const isTyping = Boolean(typingState[`${t.listing}|${t.with}`]);
              const isSelected = t.listing === search.listing && t.with === search.with;

              // Find side count of unread messages for this specific thread
              const unreadInfo = unreadData?.conversations?.find(
                (c) => c.listing_id === t.listing && c.peer_id === t.with,
              );
              const unreadCount = unreadInfo?.unread ?? 0;
              const hasUnread = unreadCount > 0;

              // Visual highlight for incoming messages on this thread
              const isRecentlyArrived = arrivedThreadKeys.has(`${t.listing}|${t.with}`);

              return (
                <Link
                  key={t.listing + t.with}
                  to="/chats"
                  search={{ listing: t.listing, with: t.with }}
                  className={cn(
                    "flex items-center gap-3 px-4 py-3.5 transition relative",
                    isSelected ? "bg-primary-soft/60" : "hover:bg-secondary/60",
                    hasUnread &&
                      !isSelected &&
                      "border-l-4 border-l-emerald-600 bg-emerald-50/40 dark:bg-emerald-950/20",
                    isRecentlyArrived &&
                      "ring-2 ring-emerald-500 bg-emerald-500/15 animate-pulse shadow-md",
                  )}
                >
                  {/* User Avatar with presence badge */}
                  <div className="relative shrink-0">
                    <UserAvatar
                      name={name(t.with)}
                      className="size-11 ring-1 ring-border shadow-sm"
                    />
                    <span
                      className={cn(
                        "absolute bottom-0 right-0 size-3 rounded-full ring-2 ring-card shrink-0",
                        isOnline ? "bg-emerald-500" : "bg-muted-foreground/30",
                      )}
                      title={isOnline ? "Online" : "Offline"}
                    />
                  </div>

                  {/* Info & Last snippet */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-1 mb-0.5">
                      <p
                        className={cn(
                          "truncate text-sm",
                          hasUnread
                            ? "font-bold text-foreground"
                            : "font-semibold text-foreground/90",
                        )}
                      >
                        {name(t.with)}
                      </p>
                      {t.last?.created_at && (
                        <span
                          className={cn(
                            "text-[11px] shrink-0",
                            hasUnread
                              ? "font-bold text-emerald-600 dark:text-emerald-400"
                              : "text-muted-foreground",
                          )}
                        >
                          {formatMessageTime(t.last.created_at)}
                        </span>
                      )}
                    </div>

                    <p className="truncate text-xs text-muted-foreground font-medium mb-1">
                      {title(t.listing)}
                    </p>

                    <div className="flex items-center justify-between gap-2">
                      <p
                        className={cn(
                          "truncate text-xs",
                          hasUnread ? "font-semibold text-foreground" : "text-muted-foreground",
                        )}
                      >
                        {isTyping ? (
                          <span className="text-primary italic font-medium flex items-center gap-1">
                            typing…
                          </span>
                        ) : (
                          t.last?.body || "No messages yet"
                        )}
                      </p>

                      {/* SIDE COUNT BADGE */}
                      {unreadCount > 0 && (
                        <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-[10px] font-extrabold text-white shadow-sm ring-2 ring-emerald-600/30 animate-in zoom-in-75">
                          {unreadCount > 99 ? "99+" : unreadCount}
                        </span>
                      )}
                    </div>
                  </div>
                </Link>
              );
            })
          )}
        </div>
      </aside>

      {/* -------------------- MAIN: ACTIVE CHAT THREAD -------------------- */}
      <section
        className={cn(
          "flex min-h-0 flex-col rounded-3xl border bg-card shadow-soft overflow-hidden",
          !open && "hidden md:flex",
        )}
      >
        {open ? (
          <Thread
            listing={search.listing!}
            other={search.with!}
            peerName={name(search.with!)}
            peerAvatar={newPerson?.avatar_url || null}
            listingTitle={title(search.listing!)}
            msgs={convo}
            loadingMsgs={isLoading}
            me={user?.id ?? ""}
            isPeerOnline={isPeerOnline}
            isPeerTyping={isPeerTyping}
            arrivedMsgIds={arrivedMsgIds}
            lastSeenWatermarks={lastSeenWatermarks}
            onSendMessage={sendChatMessage}
            onStartTyping={startTyping}
            onStopTyping={stopTyping}
            onMarkRead={handleMarkRead}
            onBack={() => navigate({ to: "/chats", search: {} })}
          />
        ) : (
          <div className="m-auto flex flex-col items-center justify-center gap-3 p-6 text-center text-muted-foreground">
            <div className="grid size-16 place-items-center rounded-3xl bg-primary-soft text-primary shadow-soft">
              <MessageCircle className="size-8" />
            </div>
            <div>
              <h3 className="text-base font-bold text-foreground">Select a Conversation</h3>
              <p className="text-xs text-muted-foreground max-w-xs mt-1">
                Choose a pickup chat from the left or open any scrap listing to contact the seller
                or collector.
              </p>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function Thread({
  listing,
  other,
  peerName,
  peerAvatar,
  listingTitle,
  msgs,
  loadingMsgs,
  me,
  isPeerOnline,
  isPeerTyping,
  arrivedMsgIds,
  lastSeenWatermarks,
  onSendMessage,
  onStartTyping,
  onStopTyping,
  onMarkRead,
  onBack,
}: {
  listing: string;
  other: string;
  peerName: string;
  peerAvatar?: string | null;
  listingTitle: string;
  me: string;
  msgs: ApiMessage[];
  loadingMsgs?: boolean;
  isPeerOnline?: boolean;
  isPeerTyping?: boolean;
  arrivedMsgIds: Set<string>;
  lastSeenWatermarks: Record<string, string>;
  onSendMessage: (
    listingId: string,
    recipientId: string,
    body: string,
    clientId: string,
  ) => boolean;
  onStartTyping: (listingId: string, recipientId: string) => void;
  onStopTyping: (listingId: string, recipientId: string) => void;
  onMarkRead: () => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const scrollContainer = useRef<HTMLDivElement>(null);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [isMuted, setIsMuted] = useState(isChatSoundMuted());
  const [showScrollDown, setShowScrollDown] = useState(false);

  const onMarkReadRef = useRef(onMarkRead);
  onMarkReadRef.current = onMarkRead;

  const prevConvoKey = useRef("");
  const prevMsgLength = useRef(0);

  // Mark read once when conversation opens or when new messages arrive
  useEffect(() => {
    const convoKey = `${listing}|${other}`;
    if (convoKey !== prevConvoKey.current || msgs.length !== prevMsgLength.current) {
      prevConvoKey.current = convoKey;
      prevMsgLength.current = msgs.length;
      onMarkReadRef.current();
    }
  }, [listing, other, msgs.length]);

  // Handle auto-scroll & scroll-down indicator
  const scrollToBottom = (smooth = true) => {
    end.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [msgs.length, isPeerTyping]);

  const handleScroll = () => {
    if (!scrollContainer.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer.current;
    const isUp = scrollHeight - scrollTop - clientHeight > 120;
    setShowScrollDown(isUp);
  };

  const toggleSound = () => {
    const next = !isMuted;
    setIsMuted(next);
    setChatSoundMuted(next);
    if (!next) {
      playChatChime();
      toast.success("Chat sounds enabled");
    } else {
      toast.info("Chat sounds muted");
    }
  };

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

    qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) => [
      ...old,
      optimisticMsg,
    ]);

    // 3. Send via WebSocket (or fallback to REST API)
    try {
      const sentViaWs = onSendMessage(listing, other, body, clientId);
      if (!sentViaWs) {
        const res = await api<ApiMessage>("/messages", {
          method: "POST",
          body: { listing_id: listing, recipient_id: other, body },
        });
        qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) =>
          old.map((m) => (m.id === clientId ? res : m)),
        );
      }
    } catch (err) {
      // Revert optimistic message if network failed
      qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) =>
        old.filter((m) => m.id !== clientId),
      );
      setText(body);
      toast.error(errorMessage(err));
    }
  };

  const peerSeenWatermark = lastSeenWatermarks[`${listing}|${other}`];

  return (
    <>
      {/* -------------------- THREAD HEADER -------------------- */}
      <div className="flex items-center justify-between border-b px-4 py-3 bg-card/60 backdrop-blur z-10">
        <div className="flex items-center gap-3 min-w-0">
          <Button variant="ghost" size="icon" className="md:hidden shrink-0 -ml-2" onClick={onBack}>
            <ArrowLeft className="size-5" />
          </Button>
          <UserAvatar
            src={peerAvatar}
            name={peerName}
            className="size-10 shrink-0 ring-1 ring-border shadow-sm"
            previewable={true}
          />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-bold text-sm truncate">{peerName}</span>
              <span
                className={cn(
                  "size-2 rounded-full shrink-0",
                  isPeerOnline ? "bg-emerald-500 animate-pulse" : "bg-muted-foreground/30",
                )}
                title={isPeerOnline ? "Online" : "Offline"}
              />
              <span className="text-[11px] text-muted-foreground font-medium">
                {isPeerOnline ? "Active now" : "Offline"}
              </span>
            </div>
            <Link
              to="/listings/$id"
              params={{ id: listing }}
              className="truncate block text-xs text-primary font-medium hover:underline"
            >
              📦 {listingTitle}
            </Link>
          </div>
        </div>

        {/* Audio notification mute toggle button */}
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleSound}
            className="size-9 rounded-full text-muted-foreground hover:text-foreground"
            title={isMuted ? "Unmute chat chime" : "Mute chat chime"}
          >
            {isMuted ? (
              <VolumeX className="size-4" />
            ) : (
              <Volume2 className="size-4 text-emerald-600" />
            )}
          </Button>
        </div>
      </div>

      {/* -------------------- MESSAGE BUBBLE AREA WITH HIGHLIGHTS -------------------- */}
      <div
        ref={scrollContainer}
        onScroll={handleScroll}
        className="flex-1 space-y-3 overflow-y-auto p-4 relative"
      >
        {loadingMsgs && msgs.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="size-6 animate-spin text-primary" />
            <span className="text-xs">Loading conversation…</span>
          </div>
        ) : msgs.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center p-6 gap-2">
            <div className="grid size-12 place-items-center rounded-2xl bg-muted text-muted-foreground">
              <MessageCircle className="size-6" />
            </div>
            <p className="text-sm font-semibold text-foreground">No messages yet</p>
            <p className="text-xs text-muted-foreground max-w-xs">
              Say hello and negotiate pickup price, timing, or scrap sorting details!
            </p>
          </div>
        ) : (
          msgs.map((m) => {
            const isMe = m.sender_id === me;
            const isArrived = arrivedMsgIds.has(m.id);

            // Calculate seen status for sender messages
            const isSeen =
              isMe &&
              peerSeenWatermark &&
              new Date(peerSeenWatermark).getTime() >= new Date(m.created_at).getTime();

            return (
              <div
                key={m.id}
                className={cn(
                  "relative max-w-[82%] rounded-2xl px-3.5 py-2.5 text-sm transition-all duration-500",
                  isMe
                    ? "ml-auto bg-primary text-primary-foreground rounded-br-sm shadow-soft"
                    : "mr-auto bg-secondary text-secondary-foreground rounded-bl-sm border shadow-xs",
                  // FLASH HIGHLIGHT WHEN CHAT ARRIVES
                  isArrived &&
                    !isMe &&
                    "ring-2 ring-emerald-500 shadow-lg shadow-emerald-500/20 bg-emerald-50 dark:bg-emerald-950/40 animate-in fade-in zoom-in-95 duration-500",
                )}
              >
                {/* Arrival highlight indicator badge */}
                {isArrived && !isMe && (
                  <span className="absolute -top-2 left-2 flex items-center gap-1 rounded-full bg-emerald-600 px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-white shadow-sm animate-pulse">
                    New
                  </span>
                )}

                <div className="whitespace-pre-wrap break-words leading-relaxed font-normal">
                  {m.body}
                </div>

                <div
                  className={cn(
                    "mt-1 flex items-center justify-end gap-1 text-[10px]",
                    isMe ? "text-primary-foreground/75" : "text-muted-foreground",
                  )}
                >
                  <span>
                    {new Date(m.created_at).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>

                  {/* Delivery / Read checkmarks for sender */}
                  {isMe && (
                    <span title={isSeen ? "Seen by recipient" : "Delivered"}>
                      {isSeen ? (
                        <CheckCheck className="size-3.5 text-emerald-300 inline" />
                      ) : (
                        <Check className="size-3.5 opacity-70 inline" />
                      )}
                    </span>
                  )}
                </div>
              </div>
            );
          })
        )}

        {/* Typing indicator */}
        {isPeerTyping && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground italic px-3 py-1.5 rounded-full bg-muted/60 w-fit animate-in fade-in">
            <span className="size-1.5 rounded-full bg-emerald-500 animate-bounce [animation-delay:-0.3s]" />
            <span className="size-1.5 rounded-full bg-emerald-500 animate-bounce [animation-delay:-0.15s]" />
            <span className="size-1.5 rounded-full bg-emerald-500 animate-bounce" />
            <span className="ml-1 text-[11px] font-medium">{peerName} is typing…</span>
          </div>
        )}
        <div ref={end} />

        {/* Scroll-to-bottom floating button when scrolled up */}
        {showScrollDown && (
          <button
            type="button"
            onClick={() => scrollToBottom(true)}
            className="sticky bottom-2 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-lift hover:opacity-90 transition animate-in zoom-in-75"
          >
            <ArrowDown className="size-3.5" />
            <span>Latest messages</span>
          </button>
        )}
      </div>

      {/* -------------------- MESSAGE INPUT FORM -------------------- */}
      <form
        className="flex gap-2 border-t p-3 bg-card"
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
          placeholder={`Message ${peerName}…`}
          className="rounded-full px-4"
          autoFocus
        />
        <Button
          type="submit"
          size="icon"
          disabled={!text.trim()}
          className="rounded-full shrink-0 shadow-soft"
        >
          <Send className="size-4" />
        </Button>
      </form>
    </>
  );
}
